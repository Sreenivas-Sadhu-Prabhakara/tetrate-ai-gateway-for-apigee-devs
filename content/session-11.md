# 3.1 — Token-based rate limiting: count tokens, not requests

!!! bottomline "Bottom line"
    The single most important AI-gateway idea: you rate-limit on **tokens**, not requests. By the end of this session you can configure a distributed limit on input + output tokens, scoped per model and per user, using the gateway's token metering — so one user firing a few enormous completions can't blow the budget while ten thousand tiny calls sail through.

## Why this exists

A request-based limit is a lie for AI traffic. "100 requests/minute" treats a 20-token "hi" and a 60,000-token document summarisation as equal, even though one costs **3,000×** the other. Cost and provider capacity are measured in tokens, so your limit has to be too.

This is also where the *variable, after-the-fact* nature of LLM cost finally bites. You don't know a call's token count when it arrives — only after the model responds. So the gateway meters usage from the **response**, attributes it to the right caller and model, and decrements a distributed counter. That's a thing no per-pod limiter can do correctly, and it's the foundation every later governance feature (budgets, tiers, chargeback) builds on.

## The concept

Token rate limiting is one layer in the governance stack — the one that turns raw token usage into an enforced limit, keyed by **who** (a user/team identity) and **what** (the model):

<figure class="svg-figure">
<img src="assets/svg/governance-stack.svg" alt="A request descends through identity, model entitlement, token rate limit, budget attribution, and guardrails; token usage is metered to cost.">
<figcaption>The token rate-limit layer reads usage the gateway extracts from each response and counts it against a distributed budget, keyed by model and identity. Budgets (3.2) and tiers (3.3) build directly on this counter.</figcaption>
</figure>

Mechanically, Envoy AI Gateway leans on Envoy Gateway's **global rate limit** with **cost**: the AI Gateway filter inserts an `x-ai-eg-model` header (the model name, pulled from the request body) and exposes the response's token usage as metadata. A `BackendTrafficPolicy` then defines a rule keyed on that model header plus a user identity, whose **cost is the token count** rather than a flat `1`. "Global" matters: the counter is shared across every gateway replica, so the limit is real fleet-wide, not per instance.

!!! pitfall "Watch out"
    "Global" is load-bearing. If you reach for a per-replica limiter instead of the gateway's global counter, your real limit is silently `limit × replicas` and it shifts every time the gateway autoscales — the exact bug token budgets exist to prevent.

!!! apigee "From Apigee"
    This is **Apigee Quota, with tokens as the unit.** Everything you know transfers: a distributed counter over an interval, keyed by an identifier (there, `client_id`; here, user + model), enforced fleet-wide. The one change is the *count source*: instead of `+1` per call, the gateway does `+N` where N is the tokens the model reported — the same way you'd drive Quota from a `countRef` instead of a fixed `count`, except the gateway computes N for you from the response.

!!! java "From Java microservices"
    You've reached for Bucket4j or a Resilience4j `RateLimiter` around an LLM call and felt it not fit — because it's **per pod** (so your real limit is `limit × replicas`, and it drifts as you autoscale) and it can only count **calls**, not the tokens each call actually cost. The gateway's limit is **distributed** (one shared counter) and **usage-based** (it counts the tokens the model returned). It's the limiter you wanted but couldn't build at the application layer.

!!! breaks "Where the analogy breaks"
    A request quota decides admission *before* the work runs. A token limit can only know the true cost *after* the model responds — so a single in-flight request can overshoot the budget by its own size before the counter catches up. You govern the *next* request, not the current one. That's acceptable and normal, but if you reason about it like an Apigee request quota ("nothing over the line ever runs"), the overshoot will surprise you. Pair token limits with a sane **max-tokens** ceiling so a single call can't overshoot wildly.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — cap tokens per user, per model

**Prereqs:** the self-hosted gateway from 1.5 with an `AIGatewayRoute` serving a model (export `$NAMESPACE` and `$GATEWAY_HOST`), and `kubectl`. Callers pass a `x-user-id` header (you enforce real identity in 4.1).

**1. Define a token budget** with a `BackendTrafficPolicy` whose rate-limit cost comes from token usage, keyed by user and the gateway's model header. (Field names track the Envoy AI Gateway version — verify against the usage-based rate-limiting docs for your release.)

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: token-budget
  namespace: ${NAMESPACE}
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: ai-gateway-route          # the route from session 1.5
  rateLimit:
    type: Global
    global:
      rules:
        - clientSelectors:
            - headers:
                - name: x-user-id          # who
                  type: Distinct
                - name: x-ai-eg-model       # which model (inserted by the gateway)
                  type: Distinct
          limit:
            requests: 100000              # 100k tokens...
            unit: Hour                    # ...per user, per model, per hour
          cost:
            request:
              number: 0                   # don't charge on the way in
            response:
              from: Metadata              # charge the tokens the model reported
              metadata:
                namespace: io.envoy.ai_gateway
                key: llm_total_token
```

!!! pitfall "Watch out"
    The whole policy hinges on `cost.response.from: Metadata` pointing at the **token** metadata. If that key is missing or mistyped, the rule silently falls back to counting **requests** — you'll believe you have a token budget while a few giant completions sail through. Confirm the metadata namespace/key match your gateway release.

**2. Apply it and confirm it's accepted:**

```bash
kubectl apply -f token-budget.yaml
kubectl get backendtrafficpolicy token-budget -n "$NAMESPACE" -o jsonpath='{.status.conditions[0].type}{"\n"}'
```

**3. Spend the budget and watch it throttle.** Fire a few large completions as one user and read the rate-limit headers:

```bash
for i in 1 2 3; do
  curl -s -o /dev/null -w "call $i -> HTTP %{http_code}\n" \
    "https://$GATEWAY_HOST/v1/chat/completions" \
    -H "x-user-id: alice" -H "content-type: application/json" \
    -d '{"model":"gpt-4o-mini","max_tokens":1024,
         "messages":[{"role":"user","content":"Write a long detailed essay about the moon."}]}'
done
```

**What success looks like:** the first call or two return `200`, and once Alice's metered tokens cross 100k for that model in the hour, further calls return **`429 Too Many Requests`** — while a *different* `x-user-id`, or the *same* user on a *different* model, is unaffected. You're now limiting on real token usage, distributed across every gateway replica.
</div>

## Verify it

- Send the same load under two different `x-user-id` values: each gets its own budget. If both share one counter, your `clientSelectors` aren't keyed on the user header.
- Compare a burst of tiny prompts against one huge prompt: the huge one exhausts the budget far faster, because cost is tokens, not calls. That asymmetry is the whole point.
- Check the `429` arrives *after* the overshooting call completes, not before — confirming limits govern the next request (see the breaks callout).

!!! failure "Common failure modes"
    - **Limiting on requests, not tokens.** A request limit lets a few giant calls torch the budget. If your `cost.response.from` isn't token metadata, you haven't actually built a token limit. *(Symptom: spend spikes while the request graph looks flat.)*
    - **Forgetting the model key.** Limit per user only, and a user's cheap-model traffic and premium-model traffic share one budget — so cheap calls can starve premium ones (and vice versa). Key on **user + model**.
    - **Per-pod thinking.** If you reach for a local limiter instead of the gateway's global one, your real limit silently multiplies by replica count and moves when you autoscale.
    - **No max-tokens ceiling.** Without a per-call cap, one request can overshoot the whole budget by itself before the counter updates.

!!! stretch "Stretch goal"
    Take a Resilience4j `RateLimiter` config you have today around an LLM call and write, side by side, the `BackendTrafficPolicy` that replaces it. Note the two things the YAML expresses that the Java config *cannot*: distributed (fleet-wide) counting, and cost measured in returned tokens. That gap is exactly why this belongs at the edge.

## Recap & next

You can now explain why AI traffic must be limited on **tokens**, configure a distributed, usage-based limit keyed on **user + model**, and reason about the after-the-fact overshoot. This token counter is the substrate everything else in Part 3 stands on.

**Next — 3.2:** turn raw token metering into money. You'll attribute spend to the right **user, team, and app** and enforce **budgets** — the core of Tetrate Agent Operations Director, and the chargeback report you could never produce from logs.
