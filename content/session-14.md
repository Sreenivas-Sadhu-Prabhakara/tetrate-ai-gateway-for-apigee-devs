# 3.4 — Caching to cut cost & latency

!!! bottomline "Bottom line"
    The cheapest, fastest token is the one you never send to a provider. By the end of this session you can enable **caching at the gateway** for a safe-to-cache call, measure the latency and cost drop on a repeated request, and — most importantly — reason about *which* responses are safe to cache. You'll meet three flavours: **response caching** (exact match), **prompt caching** (provider-side reuse of a prompt prefix), and **semantic caching** (match by embedding similarity), and the hard rule that ties them together: cache only what's deterministic and not per-user.

## Why this exists

In 3.1–3.3 every governed call still went to the provider and still cost tokens. But a great deal of AI traffic is **repeated**: the same embedding request for the same document, the same deterministic classification, the same FAQ-style question asked a thousand ways. Paying the provider — in money *and* in seconds of latency — for an answer you already computed is pure waste. Caching at the gateway turns the second-and-onward identical call into a local lookup: no provider round-trip, no tokens billed, milliseconds instead of seconds.

Doing it at the **gateway** rather than in each app matters for the same reason everything else in this course does: a per-service `@Cacheable` only helps that service and its own pods, while a gateway cache is **shared across every app and replica** — one service warms the cache, all of them benefit. The catch, and the reason this is a whole session rather than a config flag, is that LLM responses are not obviously cacheable: many are non-deterministic, and many are personalised. Cache the wrong one and you don't just serve stale data — you hand one user **another user's answer**.

## The concept

A cache hit short-circuits the expensive path entirely: the gateway answers from its store and the provider is never called — no tokens, no latency:

```widget
{
  "type": "sequence",
  "title": "A cache hit at the AI gateway",
  "actors": [
    {"id": "app", "label": "Your app"},
    {"id": "gw", "label": "AI gateway (cache)"},
    {"id": "prov", "label": "Provider"}
  ],
  "steps": [
    {"from": "app", "to": "gw", "label": "POST /v1/... (1st time)", "note": "First call. The gateway builds a cache key from the request — model + normalized body, plus any dimensions you add (e.g. tenant)."},
    {"from": "gw", "to": "prov", "label": "MISS → upstream call", "note": "No entry yet, so the gateway calls the provider, pays the tokens, and stores the response under the key."},
    {"from": "prov", "to": "gw", "kind": "return", "label": "completion (billed)", "note": "The real, token-costing response — cached on the way back out."},
    {"from": "gw", "to": "app", "kind": "return", "label": "response", "note": "App gets its answer; first call paid full price."},
    {"from": "app", "to": "gw", "label": "POST /v1/... (identical)", "note": "Same request again — same cache key."},
    {"from": "gw", "to": "app", "kind": "return", "label": "HIT — no provider call", "note": "Served from cache: zero provider tokens, millisecond latency. The provider is never touched."}
  ]
}
```

There are three flavours, and they differ in *how* they decide two requests are "the same." **Response caching** keys on an exact (normalized) match of the request — identical model and body return the stored answer. **Prompt caching** is a provider-side feature where a long, stable prompt *prefix* (a big system prompt, a shared context) is cached upstream so repeated calls re-pay only for the changing suffix — cheaper, but the call still happens. **Semantic caching** is the clever one: it embeds the incoming prompt and serves a stored answer when an existing entry is *similar enough* by vector distance, so "What's your refund policy?" and "How do I get a refund?" can hit the same cached answer. Power and danger rise together down that list.

!!! pitfall "Watch out"
    **Caching a non-deterministic or per-user response serves the wrong answer.** Two failure shapes: (1) a high-`temperature` or personalised completion *should* differ each call — caching it freezes one random or one user's result for everyone; (2) a response that embeds user-specific or PII-bearing context, cached under a key that omits the user, will leak **one tenant's answer to another tenant**. The defences are inseparable: cache only **deterministic, idempotent, non-personalised** calls (e.g. `temperature: 0` classification, stable embeddings), and put every dimension that *changes the correct answer* into the cache key (tenant, locale, the relevant context). A cache key that's too coarse is a cross-user data leak wearing a performance win.

!!! apigee "From Apigee"
    This is **ResponseCache, pointed at model traffic** — and your Apigee instincts mostly transfer. You already think in cache *keys* (`CacheKey` with `KeyFragment`s), *TTLs*, and *cache population vs. lookup*; a gateway AI cache is the same machinery with model + request body as the key. But two ResponseCache habits become safety-critical here. First, **`KeyFragment` selection is now a security control**: forgetting to include the user/tenant fragment on a REST cache served a slightly-stale list; forgetting it on an AI cache serves another user's *generated answer*. Second, you cached responses you knew were **idempotent**; for LLMs you must additionally confirm the call is **deterministic**, because the same input can legitimately produce different output. Same pattern, sharper edges.

!!! java "From Java microservices"
    You've reached for `@Cacheable` around an expensive embedding or classification call — keyed on the method arguments, backed by Caffeine or Redis. The gateway cache is that annotation **moved to the edge and shared across every service**, so one app's cache warm-up benefits all of them instead of living in one pod's heap. The discipline is identical and you already know it: `@Cacheable` on a method whose result depends on the *current user* but whose key omits that user is the classic Spring cache-poisoning bug — you've seen it serve user B the object cached for user A. The gateway cache has the exact same trap at fleet scale; the `key` SpEL you'd write to include the principal is the cache-key dimension you must include here.

!!! breaks "Where the analogy breaks"
    A REST `ResponseCache` or `@Cacheable` decides "same request" by **exact key equality** — bytes match or they don't. **Semantic caching breaks that entirely**: it serves a hit for a request that is *not identical*, only *similar*, judged by embedding distance against a threshold. There is no equivalent in Apigee or Spring caching, and it brings a failure mode neither has: set the similarity threshold too loose and the cache confidently returns an answer to a *different question* that merely sounds alike. You're no longer caching exact answers — you're betting that "close enough in vector space" means "correct," and that bet is tunable, fallible, and entirely new. Exact-match caching can be stale; semantic caching can be **wrong**.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — cache a deterministic call and measure the win

**Prereqs:** the gateway and `AIGatewayRoute` from 1.5 (export `$NAMESPACE`, `$GATEWAY_HOST`, `$GATEWAY_KEY`), and `kubectl`. We cache a **safe** call: an embeddings request (deterministic, no per-user content) — the canonical cacheable AI workload.

**1. Enable response caching on the route** with a cache key built from the model and the request body, and a TTL. Caching is a `BackendTrafficPolicy` concern, alongside the rate limits you set in 3.1:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: ai-response-cache
  namespace: ${NAMESPACE}
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: ai-gateway-route            # the route from 1.5
  responseCache:
    enabled: true
    ttl: 1h
    key:
      # the dimensions that define "the same request" — model + body...
      includeBody: true
      includeHeaders:
        - x-ai-eg-model
        - x-tenant                      # ...and tenant, so caches never cross tenants
```

**2. Apply and confirm acceptance:**

```bash
kubectl apply -f ai-response-cache.yaml
kubectl get backendtrafficpolicy ai-response-cache -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[0].type}{"\n"}'
```

**3. Time the first (miss) call** — it pays full price and warms the cache:

```bash
curl -s -o /dev/null -w "MISS: %{http_code} in %{time_total}s\n" \
  "https://$GATEWAY_HOST/v1/embeddings" \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "x-tenant: acme" -H "content-type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"the quick brown fox"}'
```

**4. Repeat the identical call** — this should be a hit: no provider round-trip, far lower latency:

```bash
curl -s -o /dev/null -w "HIT:  %{http_code} in %{time_total}s\n" \
  "https://$GATEWAY_HOST/v1/embeddings" \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "x-tenant: acme" -H "content-type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"the quick brown fox"}'
```

!!! pitfall "Watch out"
    Notice `x-tenant` is part of the cache key. Drop it and tenant `acme`'s embedding answer is served to tenant `globex` for the same input string — fine for a public sentence, a **data leak** the moment the input is a customer record. When in doubt about whether a dimension affects the correct answer, **include it in the key**; an over-keyed cache merely misses more often, an under-keyed one serves the wrong tenant's data. Never cache a high-`temperature` chat completion this way — only deterministic calls belong here.

**What success looks like:** the second call returns the **same body** as the first but with a **markedly lower `time_total`** (the provider round-trip is gone), and the gateway's token meter from 3.1 shows **no new tokens** for the repeated call — the hit cost zero provider tokens. You've cut both latency and cost on identical traffic, fleet-wide, without touching a line of app code.
</div>

## Verify it

You've cached safely when all of these hold:

```bash
# same input, two different tenants — answers must NOT cross
for t in acme globex; do
  curl -s -o /dev/null -w "$t -> %{http_code} in %{time_total}s\n" \
    "https://$GATEWAY_HOST/v1/embeddings" \
    -H "Authorization: Bearer $GATEWAY_KEY" \
    -H "x-tenant: $t" -H "content-type: application/json" \
    -d '{"model":"text-embedding-3-small","input":"customer record 42"}'
done
```

- The repeated identical call is **dramatically faster** and bills **no new tokens** — if latency and token count are unchanged, caching isn't engaged (check the policy `Accepted` and that the body normalized to the same key).
- Two tenants sending the *same* input each get their **own** cache entry — if the second tenant's call is an instant hit, your key is missing the tenant dimension and you're leaking across tenants.
- Changing any byte of the input produces a **miss** — exact-match caching must not collapse different inputs onto one entry (that's semantic caching's job, with its own threshold to tune).

!!! failure "Common failure modes"
    - **Caching non-deterministic completions.** A high-`temperature` chat response should vary; caching freezes one sample for everyone. Only cache `temperature: 0` / idempotent calls.
    - **Cache key too coarse.** Omitting tenant/user/locale serves one caller's generated answer to another — a data leak, not just staleness. Every dimension that changes the correct answer belongs in the key.
    - **Semantic threshold too loose.** Set the similarity bar low and the cache answers a *different* question that merely sounds alike. Tune the threshold and measure wrong-hit rate, don't eyeball it.
    - **Stale answers past their usefulness.** A TTL that's too long serves outdated completions after the underlying data or prompt changed. Match TTL to how fast the right answer goes stale.
    - **Assuming a hit is free of governance.** A cached response still belongs to a caller — keep attribution (3.2) meaningful even when tokens are zero, so a heavily-cached team isn't invisible.

!!! stretch "Stretch goal"
    Enable **semantic caching** on a deterministic Q&A endpoint and probe its boundary: send a question, then a paraphrase, then a *related-but-different* question. Find the similarity threshold where paraphrases correctly hit the same answer but the different question correctly misses — and note how moving the threshold a little trades cost savings against the risk of confidently answering the wrong question. That tuning dial, absent from any exact-match cache, is the whole reason semantic caching is powerful and dangerous in equal measure.

## Recap & next

You can now cut cost and latency with **caching at the gateway** — response (exact match), prompt (provider-side prefix reuse), and semantic (embedding similarity) — measure the win on a repeated call, and, above all, decide *what is safe to cache*: deterministic, idempotent, non-personalised requests, with every answer-changing dimension in the cache key. That discipline is what separates a performance win from a cross-tenant leak. With rate limits, budgets, tiers, and caching in place, Part 3's cost-and-governance layer is complete.

**Next — 4.1:** every control so far trusted a `x-user-id`, `x-team`, or `x-tier` header the client simply asserted. Now you make identity *real* — **authenticating callers at the AI edge** so the principal behind every budget, tier, and cache key is verified, not claimed.
