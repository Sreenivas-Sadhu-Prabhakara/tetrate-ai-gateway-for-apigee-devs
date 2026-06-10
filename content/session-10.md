# 2.5 — Beyond chat: embeddings, multimodal & other endpoints

!!! bottomline "Bottom line"
    Chat completions are not the only model traffic in your estate — **embeddings** power every RAG and search pipeline, and **multimodal** calls are growing. The same gateway routes all of them: one OpenAI-compatible endpoint, one approved catalog, one set of policies. By the end you can send an **embeddings** request through the gateway, confirm it's routed and metered, and understand why embeddings need their own metering thinking.

!!! eli5 "In plain words"
    The robots don't only chat with you. Some take your words and turn them into a secret string of numbers — a number-fingerprint — so the helper can find which words are alike, and some can look at a picture and tell you what's in it. The very same friendly helper at the door handles all of them, not just the talking ones. Those other kinds of robot jobs — number-fingerprints and picture-reading and more — are **embeddings, multimodal, and other endpoints**.

## Why this exists

If only your `ChatClient` goes through the gateway, you've governed the visible half of your AI traffic and left the rest ungoverned. The unglamorous truth is that **embeddings often dominate by volume**: every document you ingest for RAG, every user query that hits a vector search, every re-index of a knowledge base is an embeddings call. Those run in batch loops, thousands at a time, and if they bypass the gateway they bypass your catalog, your budgets, your audit — the exact governance you spent Part 2 building.

The good news is there's almost nothing new to learn. Embeddings, multimodal, audio, and image endpoints are just **different operations on the same OpenAI-compatible contract**: `/v1/embeddings`, `/v1/chat/completions`, and friends. The gateway already fronts that contract. Routing a non-chat call is the same `AIGatewayRoute` machinery from 2.3 — match the model name, pick an approved backend — pointed at an embeddings model instead of a chat model. Your `EmbeddingClient` changes its base URL exactly the way your `ChatClient` did, and your whole vector pipeline inherits metering, the approved catalog, and the failover you built in 2.4 *for free*.

The one place this genuinely differs is **metering**, and it's a trap worth naming up front. Chat is billed on input *and* output tokens; an embeddings call has **no completion** — it returns vectors, not generated text, so its cost is **input tokens only**. Any budget, rate limit, or chargeback report keyed on *output* or *total-with-output* tokens will systematically under-count embeddings, sometimes to nearly zero. The endpoint is the same shape; the cost model is not.

!!! apigee "From Apigee"
    These are **different operations (resources/verbs) on the same API Product**, exactly like an Apigee proxy exposing `/search`, `/orders`, and `/orders/{id}` under one basepath with per-resource RouteRules. `/v1/embeddings` and `/v1/chat/completions` are distinct paths scoped by the route, the way proxy *resources* (the conditional flows in your `proxies/default.xml`) scope behaviour per path+verb. You'd never put each operation behind its own proxy and key; you put them under one product and let RouteRules dispatch. Same here: one gateway, one credential surface, route rules that send each endpoint to the right backend — and the same analytics dimensions apply, just remember the **unit differs by operation** (embeddings meter input only).

!!! java "From Java microservices"
    In Spring AI you already hold two beans against the same provider: an `EmbeddingClient` (or `EmbeddingModel`) and a `ChatClient`, configured from the same `spring.ai.openai.*` block. When you repointed `base-url` at the gateway in 1.4, you may have only redirected the chat client and left embeddings pointing at the provider — splitting your governance in half without noticing. Point **both** beans at the gateway and your vector-ingest jobs, your RAG retrieval path, and your chat path all flow through one governed edge. The embedding pipeline that feeds your vector store inherits the catalog, the token limits, and the cross-vendor failover with no extra code — it was always the same client family, just a different operation.

!!! breaks "Where the analogy breaks"
    The Apigee instinct says "different operations are interchangeable units against the same quota" — and here they are emphatically **not the same unit**. A chat call and an embeddings call can both succeed against the same product, but one is metered on input+output tokens and the other on input only, so treating them as fungible against a single token budget mis-prices both. And the Spring instinct that "it's the same provider, so it's the same governance" misses that a multimodal request may carry an **image or audio payload**, not text — sizes, timeouts, and even what "a token" means shift, so the request-shape assumptions baked into your chat-tuned policies (max body size, per-try timeout from 2.4) won't automatically fit. Same contract, genuinely different payload economics.

## The concept

Every endpoint speaks the same OpenAI-compatible API and rides the same route machinery — but **what each is metered on differs**, and that difference drives your budgets and limits downstream:

| Endpoint | OpenAI-compatible path | Typical payload | Metered on | Governance note |
|---|---|---|---|---|
| **Chat / completions** | `/v1/chat/completions` | text in → text out | **input + output** tokens | the default everything in Part 3 assumes |
| **Embeddings** | `/v1/embeddings` | text in → vectors out | **input tokens only** (no completion) | high volume; output-keyed limits under-count it |
| **Multimodal (vision)** | `/v1/chat/completions` | text + image in → text out | input (incl. image tokens) + output | larger bodies; revisit max-body / timeout from 2.4 |
| **Audio (speech / transcribe)** | `/v1/audio/*` | audio ↔ text | seconds or characters, **not tokens** | token budgets may not apply at all — meter by the vendor's unit |
| **Image generation** | `/v1/images/*` | text in → image out | **per image**, by size/quality | not token-based; needs its own cost dimension |

The shape is identical to 2.3: the gateway lifts the `model` from the body into `x-ai-eg-model`, a route rule matches it to an approved `AIServiceBackend`, and policies apply. The *only* thing you add per endpoint is a route rule pointing at the right backend (an embeddings model, a vision model) — the catalog, the credential handling (2.2), and the failover (2.4) come along unchanged. The work is almost entirely in **getting the metering dimension right per endpoint**, not in the routing.

!!! pitfall "Watch out"
    Embeddings are **input-token metered — there is no output completion** — so any budget or rate limit keyed only on *output* tokens (or on `llm_total_token` where "total" is computed as input+output and your embeddings provider reports output as `0`) will count a massive embeddings re-index as nearly **free**, then act surprised at the bill. When you build token limits in Part 3, key embeddings traffic on **input/prompt tokens** explicitly, and budget it separately from chat. The failure is silent: the dashboard looks calm while a batch ingest quietly burns through input-token spend no output-based counter ever sees.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — route and meter an embeddings request through the gateway

**Prereqs:** the self-hosted gateway and `Gateway` from 1.5, a working credential (2.2), `kubectl`, and `$NAMESPACE` / `$GATEWAY_HOST` exported. The goal: add an **embeddings** backend + route rule alongside your existing chat route, send a real embeddings call, and confirm it's routed and metered. (Field names track the Envoy AI Gateway version — verify the `schema` and route shape against the supported-endpoints docs for your release.)

**1. Declare an embeddings backend.** It's an ordinary `AIServiceBackend` — same shape as your chat backend, pointed at an embeddings-capable model:

```yaml
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: AIServiceBackend
metadata:
  name: openai-embeddings
  namespace: ${NAMESPACE}
spec:
  schema:
    name: OpenAI                         # same OpenAI-compatible contract
  backendRef:
    name: openai                         # the Backend (gateway.envoyproxy.io/v1alpha1) from 2.1
    kind: Backend
    group: gateway.envoyproxy.io
```

**2. Add an embeddings route rule** beside your chat rule — same route, a new approved catalog entry:

```yaml
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: AIGatewayRoute
metadata:
  name: ai-gateway-route
  namespace: ${NAMESPACE}
spec:
  parentRefs:
    - name: ai-gateway                   # the Gateway from 1.5
  rules:
    - matches:                            # existing chat rule (from 2.3) stays
        - headers:
            - { type: Exact, name: x-ai-eg-model, value: chat-default }
      backendRefs:
        - name: openai
          modelNameOverride: gpt-4o-mini
    - matches:                            # NEW: embeddings as an approved catalog entry
        - headers:
            - { type: Exact, name: x-ai-eg-model, value: embeddings-default }
      backendRefs:
        - name: openai-embeddings
          modelNameOverride: text-embedding-3-small
```

**3. Apply and confirm the route is programmed:**

```bash
kubectl apply -f embeddings-backend.yaml -f aigatewayroute.yaml
kubectl get aigatewayroute ai-gateway-route -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```

**4. Send a real embeddings request** to `/v1/embeddings` through the gateway — note the path and body differ from chat, but the gateway and credential are the same:

```bash
curl -s "http://$GATEWAY_HOST/v1/embeddings" -H "content-type: application/json" \
  -d '{"model":"embeddings-default",
       "input":"Governance applies to vectors too, not just chat."}' \
  | jq '{model: .model, dims: (.data[0].embedding | length), usage: .usage}'
```

!!! pitfall "Watch out"
    Look at `usage` in that response: you'll see `prompt_tokens` (the input) and `total_tokens`, but **no `completion_tokens`** — embeddings produce no completion. If your metering reads only `completion_tokens`, this entire call meters as **zero**. Confirm now that the dimension you intend to budget on (input/prompt tokens) is actually present and non-zero for embeddings *before* you wire limits in Part 3.

**5. Prove it was routed and metered** through the gateway, not sent direct. Check the gateway's telemetry / access logs (full observability is 6.1) for the embeddings model and its token usage:

```bash
kubectl logs -n "$NAMESPACE" deploy/envoy-ai-gateway --tail=20 | grep -i "embeddings-default"
# expect: x-ai-eg-model=embeddings-default, a 200, and a non-zero input/prompt token count
```

**What success looks like:** the curl in step 4 returns a real embedding vector (a non-trivial `dims` count) through the **gateway** using the **same credential and base URL** as your chat traffic, the `usage` block shows **input/prompt tokens with no completion tokens**, and the gateway logs the call under `embeddings-default` — proof your vector pipeline now flows through the governed edge and is metered on the right dimension.
</div>

## Verify it

You're done when non-chat traffic is as governed as chat:

- An embeddings request to `/v1/embeddings` with `model: embeddings-default` returns vectors with a `200`; an *unlisted* embeddings model 404s at the route exactly like chat — the catalog applies to all operations, not just chat.
- The response `usage` for embeddings shows input/prompt tokens and **no completion tokens** — confirming the metering dimension differs from chat and your Part 3 budgets must account for it.
- Both your `EmbeddingClient` and `ChatClient` (or the equivalent curl calls) reach the same `$GATEWAY_HOST` with the same gateway credential — neither still points at a provider directly.

```bash
# confirm the embeddings rule is present in the approved catalog:
kubectl get aigatewayroute ai-gateway-route -n "$NAMESPACE" -o yaml \
  | grep -A1 'value: embeddings'
```

!!! failure "Common failure modes"
    - **Embeddings still bypass the gateway.** Only the chat client was repointed in 1.4; the embedding/ingest job still hits the provider directly, so a huge volume of traffic is ungoverned and unmetered. *(Symptom: vector-store ingest spend appears nowhere in gateway analytics.)*
    - **Metering keyed only on output/completion tokens.** Embeddings have none, so they meter as ~zero and silently escape budgets and limits. *(Symptom: calm dashboards, a surprising input-token bill.)*
    - **Reusing chat policy verbatim for multimodal.** Image/audio payloads are larger; the chat-tuned max-body size or per-try timeout (2.4) rejects or times out big requests. *(Symptom: multimodal `413`/`504` while chat is fine.)*
    - **Assuming all endpoints are token-metered.** Audio and image endpoints meter on seconds, characters, or per-image — token budgets don't map. *(Symptom: image-generation spend invisible to any token counter.)*
    - **No catalog entry for the new endpoint.** The embeddings model isn't in any route rule, so the call 404s — or worse, an over-broad rule lets an unvetted embeddings model through. Add an explicit, scoped rule.

!!! stretch "Stretch goal"
    Wire a small Spring AI RAG flow whose **both** `EmbeddingModel` and `ChatModel` point at the gateway: embed a few documents into a vector store, retrieve, then chat over the context — all through one governed edge. Then read the gateway's metering and produce a single number you couldn't get before: **input-token spend on embeddings vs. input+output spend on chat for the same RAG request.** Seeing how much of a RAG pipeline's cost hides in embeddings is the clearest argument for governing non-chat traffic, and the setup you'll meter properly in Part 3.

## Recap & next

You can now route **non-chat endpoints** — embeddings first, then multimodal, audio, and image — through the *same* governed gateway using the same `AIGatewayRoute` machinery from 2.3, so your vector and RAG pipelines inherit the catalog, credentials, and failover for free. The one thing that genuinely differs is **metering**: embeddings are input-token-only, and other endpoints meter on entirely non-token units — a distinction your budgets must respect.

**Next — 3.1:** with all your AI traffic now flowing through one edge, you start governing its cost. You'll build **token-based rate limiting** — counting tokens, not requests — per model and per user, the single most important AI-gateway idea and the foundation budgets, tiers, and chargeback all stand on.
