# 2.4 — Fallback, retries & failover across providers

!!! bottomline "Bottom line"
    Providers degrade, rate-limit you, and go down — usually one vendor at a time. A **`BackendTrafficPolicy`** lets the gateway retry transient failures, bound them with timeouts, and **fail over to a *different* model vendor** when the primary keeps failing. By the end you can configure retry + timeout on a route, attach a fallback backend, force the primary to fail, and prove the gateway transparently serves the request from the fallback — with the client unaware anything went wrong.

!!! eli5 "In plain words"
    Sometimes the robot you wanted is taking a nap or feeling grumpy and won't answer. Instead of telling you "sorry, no answer today," the friendly helper quietly walks over to a different robot and asks your question there — so you still get your answer and never even notice the first robot was asleep. That quiet switch to a backup robot when the first one won't help is **fallback, or failover**.

## Why this exists

In 2.3 you put one virtual name in front of one model. That's a single point of failure wearing a stable alias. The moment `chat-default` resolves to exactly one OpenAI backend, an OpenAI incident — a `503`, a `429` storm, a region blip — is a full outage for every app that depends on that alias, even though a perfectly good Claude or Bedrock model is sitting idle one `backendRef` away.

The honest reality of model traffic is that providers fail *partially and transiently* far more often than they fail hard. You get an intermittent `500`, a connection reset, a rate-limit `429` that would clear if you waited 200ms. A naive client treats every one of those as a hard error and surfaces it to the user. A resilient edge does what you'd do by hand: retry the blips a bounded number of times, give up fast when it's clearly down, and route to a second vendor rather than fail the request.

The reason this belongs at the gateway and not in each app is the same reason everything in this course belongs there. If resilience lives in code, every service reimplements retry budgets, timeout values, and "which backup model do we use" — and they drift, and half of them retry non-idempotent calls and double-charge you. Lifted to one `BackendTrafficPolicy`, the failover behaviour is declared once, applied uniformly, and tuned by an operator who can see the whole fleet's error rates — not guessed at per service.

!!! apigee "From Apigee"
    You've wired this exact resilience before, for REST. A `BackendTrafficPolicy`'s **retries** are Apigee's retry on a TargetEndpoint; its **timeouts** are your `io.timeout.millis` / connect-timeout; and prioritized `backendRefs` are an Apigee **LoadBalancer over multiple TargetServers** with `MaxFailures` and a fallback `Server` — the gateway sheds load off a failing target onto a healthy one. **Health checks** that mark a target down map to Envoy's outlier detection / passive health checking. The single genuinely new move: in Apigee your fallback TargetServers were *the same backend, different hosts*; here the fallback is **a different model vendor entirely** — failing over from OpenAI to Bedrock, not from `host-a` to `host-b`.

!!! java "From Java microservices"
    This is the **Resilience4j `Retry` + `CircuitBreaker` + fallback** you've wrapped around an LLM call, hoisted out of every service into one policy. The `maxAttempts`, the `retryExceptions`, the exponential `waitDuration`, the `@CircuitBreaker(fallbackMethod = "callBackupModel")` that swaps to a cheaper provider — all of it. At the application layer that config is per-service and per-pod: each replica keeps its own circuit state, so the breaker opens inconsistently across the fleet and your real retry budget is `attempts × replicas`. The gateway runs it as shared infrastructure, in front of all callers, with one consistent view of when a provider is unhealthy.

!!! breaks "Where the analogy breaks"
    A REST retry is cheap and safe to be generous with — the call is usually idempotent and costs one unit. An **LLM completion is neither.** It is billed per token the *instant* the model starts producing output, so a call that streams 800 tokens and then drops the connection has **already cost you money**, and a blind retry pays for it twice. Worse, completions are non-idempotent: retrying isn't "the same request again," it's "a second, differently-worded, separately-billed generation." So unlike a Resilience4j retry where more attempts is just more latency, here every attempt is a real charge and a real side effect. Cap retries hard, prefer to retry only *pre-response* failures (connection refused, fast `503`), and never reflexively retry a call that may have partially completed.

## The concept

A `BackendTrafficPolicy` (`gateway.envoyproxy.io/v1alpha1`) attaches to the route and layers three behaviours: **bounded retries** of transient failures, a **timeout** that stops waiting on a slow provider, and — by ordering `backendRefs` on the route — **failover to a second backend** when the primary is exhausted. Walk one request through a primary outage and out the fallback:

```widget
{
  "type": "sequence",
  "title": "A request fails over from primary to fallback provider",
  "actors": [
    {"id": "app", "label": "Your app"},
    {"id": "gw", "label": "AI gateway"},
    {"id": "p", "label": "Primary (OpenAI)"},
    {"id": "f", "label": "Fallback (Bedrock)"}
  ],
  "steps": [
    {"from": "app", "to": "gw", "label": "POST /v1/chat/completions (model: chat-default)", "note": "One OpenAI-shaped call to the stable alias from 2.3 — the app knows nothing about providers."},
    {"from": "gw", "to": "p", "label": "try primary", "note": "The route's first backendRef is OpenAI; the gateway attempts it first."},
    {"from": "p", "to": "gw", "kind": "return", "label": "503 Service Unavailable", "note": "Transient, pre-response error — safe to retry. The connection failed before any tokens were billed."},
    {"from": "gw", "to": "p", "label": "retry (attempt 2, backoff)", "note": "BackendTrafficPolicy retries on 5xx/connect errors, with a per-retry timeout and capped attempts."},
    {"from": "p", "to": "gw", "kind": "return", "label": "503 again", "note": "Primary still down. Retries exhausted — the gateway stops paying attention to this backend for now."},
    {"from": "gw", "to": "f", "label": "fail over to next backendRef", "note": "The gateway routes to the second, lower-priority backend — a different vendor entirely."},
    {"from": "f", "to": "gw", "kind": "return", "label": "200 + completion", "note": "Bedrock serves the request. modelNameOverride (2.3) maps chat-default to the vendor's real model ID."},
    {"from": "gw", "to": "app", "kind": "return", "label": "200 (served from fallback)", "note": "The client sees a normal success. The failover was transparent — that's the whole point."}
  ]
}
```

Mechanically: **retries and timeouts** are fields on the `BackendTrafficPolicy` (`spec.retry` with `numRetries` and the `retryOn` conditions; `spec.timeout` for request and per-try budgets). **Failover** is expressed by listing more than one backend on the route's `backendRefs` with different priorities/weights — when the gateway exhausts the primary (retries spent, or outlier detection has ejected it), it serves from the next backend in line. Because each backend carries its own `modelNameOverride` (2.3), the same virtual `chat-default` resolves to OpenAI's `gpt-4o-mini` on the primary and to a Bedrock Claude ID on the fallback — one alias, two vendors, automatic switchover.

!!! pitfall "Watch out"
    Retrying a partially-billed completion can **double-charge** you. The danger window is a failure that arrives *after* the model began generating — a dropped stream, a timeout mid-response, a `499`/`504`. At that point you've paid for the tokens already produced, and a retry pays again for a *fresh* generation. Configure `retryOn` to cover only clearly **pre-response** conditions (connection refused, fast `503`, `429`), keep `numRetries` low (1–2), and treat any retry of a completion as a billable, non-idempotent event — not a free do-over. The instinct from REST ("retries are basically free, crank them up") is exactly wrong here.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — retry, timeout, and fail over to a second vendor

**Prereqs:** the self-hosted gateway and `Gateway` from 1.5, the virtual `chat-default` route from 2.3, two working `AIServiceBackend`s with credentials — a primary (`openai`) and a fallback on a *different* vendor (`bedrock`, configured per 2.1/2.2) — plus `kubectl` and `$NAMESPACE` / `$GATEWAY_HOST` exported. (Field names track the Envoy AI Gateway / Envoy Gateway version — verify `retry`, `timeout`, and the `backendRefs` priority shape against the BackendTrafficPolicy docs for your release.)

**1. Put two backends behind the one virtual name, primary first.** Order is priority: the gateway tries the first `backendRef`, then the next on failure.

```yaml
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: AIGatewayRoute
metadata:
  name: ai-gateway-route
  namespace: ${NAMESPACE}
spec:
  parentRefs:
    - name: ai-gateway                  # the Gateway from 1.5
  rules:
    - matches:
        - headers:
            - type: Exact
              name: x-ai-eg-model
              value: chat-default        # the stable alias from 2.3
      backendRefs:
        - name: openai                   # PRIMARY — tried first
          priority: 0
          modelNameOverride: gpt-4o-mini
        - name: bedrock                  # FALLBACK — different vendor
          priority: 1
          modelNameOverride: anthropic.claude-3-5-sonnet-20241022-v2:0
```

**2. Attach retry + timeout with a `BackendTrafficPolicy`.** Bound the blips; fail fast when it's really down.

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: ai-resilience
  namespace: ${NAMESPACE}
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: ai-gateway-route             # the route above
  timeout:
    http:
      requestTimeout: 30s                # overall budget for the request
  retry:
    numRetries: 2                        # low on purpose — see the pitfall
    perRetry:
      timeout: 8s
    retryOn:
      httpStatusCodes: [503, 429]        # PRE-response, transient only
      triggers:
        - connect-failure
        - reset
```

**3. Apply both and confirm they're accepted:**

```bash
kubectl apply -f aigatewayroute.yaml -f backendtrafficpolicy.yaml
kubectl get backendtrafficpolicy ai-resilience -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```

!!! pitfall "Watch out"
    `retryOn` here lists `503` and `429` but deliberately **omits `504`** (gateway timeout) and connection *resets that occur mid-stream*. Those can land *after* the model started generating — retrying them risks the double-charge from the concept pitfall. If you widen `retryOn` to "all 5xx" to make a demo greener, you've quietly re-armed the billing hazard. Keep the retry set narrow and pre-response.

**4. Force the primary to fail.** The cleanest way without a real outage: point the primary `AIServiceBackend` at an unreachable endpoint (or temporarily revoke its credential so it returns `401`/`503`), so OpenAI consistently errors while Bedrock stays healthy. Re-apply that broken primary, then send a normal request to the unchanged alias:

```bash
curl -s "http://$GATEWAY_HOST/v1/chat/completions" -H "content-type: application/json" \
  -d '{"model":"chat-default",
       "messages":[{"role":"user","content":"In one sentence: why have a fallback provider?"}]}' \
  | jq -r '.choices[0].message.content'
```

**5. Prove which vendor answered.** The body never names a provider, so confirm failover from the gateway's telemetry / access logs (you wire full observability in 6.1) — the request should show the primary erroring, retries exhausted, then the fallback backend serving `200`:

```bash
kubectl logs -n "$NAMESPACE" deploy/envoy-ai-gateway --tail=20 | grep -i "chat-default"
# expect: attempts against openai -> 503, then bedrock -> 200
```

**What success looks like:** with the primary deliberately broken, the curl in step 4 still returns a real completion — served by the **fallback vendor** — and the client request body is byte-for-byte the same as when the primary was healthy. The logs show the primary erroring and retries exhausting *before* the fallback served. You've turned a single-vendor outage into a transparent provider switch.
</div>

## Verify it

You're done when a dead primary is invisible to the caller:

- With the primary healthy, the request is served by the primary; with the primary broken, the *identical* request is served by the fallback and the client sees `200` either way. If the broken case returns `5xx`, your `backendRefs` priority or `BackendTrafficPolicy` isn't attached to the route.
- A transient `503` from the primary is retried (not surfaced), but a `400`/`404` is **not** retried — confirming `retryOn` is scoped to transient, pre-response conditions and not client errors.
- The request honours the `requestTimeout` ceiling — a hung primary doesn't make the caller wait forever; it times out and fails over within budget.

```bash
# confirm the policy is bound to the route and which codes it retries:
kubectl get backendtrafficpolicy ai-resilience -n "$NAMESPACE" -o yaml \
  | grep -A6 'retry:'
```

!!! failure "Common failure modes"
    - **Retrying non-idempotent, partially-billed completions.** A wide `retryOn` (all 5xx, including `504`/mid-stream resets) double-charges you for a single user request. *(Symptom: token spend higher than request count predicts; duplicate-looking generations in logs.)*
    - **Fallback never engages.** Only one `backendRef`, or both at the same priority, so there's nothing to fail over *to*. *(Symptom: primary outage returns `503` to the client instead of a fallback `200`.)*
    - **Policy targets the wrong object.** `targetRefs` names a route that doesn't exist or the wrong kind, so retries/timeouts silently don't apply. *(Symptom: status condition not `Accepted`; behaviour identical to no policy.)*
    - **Fallback model rejects the request shape.** The primary and fallback are both OpenAI-compatible at the gateway, but the fallback's `modelNameOverride` names a model the vendor doesn't recognise. *(Symptom: failover happens, then the fallback returns `400`/`404` from the provider.)*
    - **Retry budget multiplied by client-side retries.** The app *also* retries on `5xx`, so a single user action becomes `client-retries × gateway-retries` real generations. Centralise retry at the edge and stop retrying in the app.

!!! stretch "Stretch goal"
    Add a **third** backend in a different region or vendor and tune the policy so a `429` (rate-limit) from the primary fails over *immediately* with `numRetries: 0`, while a `503` retries once before failing over. Then take a Resilience4j `Retry` + `CircuitBreaker` config you run today around an LLM call and write the equivalent `BackendTrafficPolicy` beside it — noting the two things the YAML expresses that the Java cannot: one shared circuit state across the whole fleet, and failover to a genuinely different *vendor*, not just another host.

## Recap & next

You can now configure **bounded retries** and **timeouts** with a `BackendTrafficPolicy`, order `backendRefs` so the gateway **fails over to a different model vendor** when a primary is exhausted, and reason about why LLM retries are billable, non-idempotent events that must be capped — not the free do-overs you're used to from REST. A single-vendor outage is now a transparent switch the client never sees.

**Next — 2.5:** chat isn't the only thing flowing through here. You'll route **embeddings, multimodal, and other endpoints** through the *same* governed gateway — so your vector pipelines inherit the metering, catalog, and resilience you just built — and learn why embeddings need their own metering thinking.
