# 3.2 — Budgets & cost attribution per user, team & app

!!! bottomline "Bottom line"
    Token metering only becomes *governance* once you attribute spend to a **who** — a user, a team, an app — and put a **budget** on it. By the end of this session you can tag traffic with an identity, produce a per-team token-spend breakdown, and enforce a per-team budget on top of the token counter from 3.1. This is the heart of **Tetrate Agent Operations Director (TAOD)**: discover GenAI usage, attribute cost per user/team/app, and enforce limits — the chargeback report you could never assemble from logs.

## Why this exists

In 3.1 you proved the gateway can count tokens and stop a runaway user. But a raw token counter answers the wrong question for the people who actually care. Finance doesn't ask "how many tokens crossed the edge?" — they ask "**which team spent what, and were they within budget?**" Without attribution, your beautiful token meter is one undifferentiated number, and the first cost-review meeting goes nowhere.

Attribution turns metered tokens into accountable money. You stamp every call with the identity it belongs to — `x-user-id`, `x-team`, an app id — and the gateway slices usage along those dimensions. From there, two things fall out for free: **showback** (here's what each team consumed) and **chargeback** (here's the bill, enforced as a budget that throttles when crossed). TAOD is the product that does this end to end — discovering shadow GenAI usage you didn't know existed, attributing it, and governing it against ROI and risk policy. Underneath, it's the same Envoy AI Gateway mechanism you already touched in 3.1, keyed on a richer identity.

## The concept

Budgets and attribution are the layer directly above the token rate limit in the governance stack — the token counter from 3.1 feeds it, and it answers *whose* spend this was:

<figure class="svg-figure">
<img src="assets/svg/governance-stack.svg" alt="A request descends through identity, model entitlement, token rate limit, budget attribution, and guardrails; token usage is metered to cost per user, team, and app.">
<figcaption>The budget &amp; attribution layer reads the same metered token usage as the rate-limit layer below it, but keys the counter on a richer identity — user, team, app — so spend rolls up the way an org chart does. Same meter, different grouping key.</figcaption>
</figure>

Mechanically there is no new machinery to learn. It's the **same `BackendTrafficPolicy`** from 3.1, with two changes: you add the identity dimensions you want to bill on (`x-team`, an app id) to the `clientSelectors`, and you set the `limit` to that identity's budget rather than a single user's throttle. The gateway already exposes the per-response token usage as metadata (`io.envoy.ai_gateway` / `llm_total_token`); attribution is just *which header you group that cost by*. A per-user limit governs one noisy user; a per-team limit governs a cost centre.

!!! pitfall "Watch out"
    **Cost is not tokens.** A token on a frontier model can cost 20–60× a token on a small model, so a raw `llm_total_token` count ranks teams by *volume*, not *spend*. A team running a million cheap-model tokens may cost less than a team running fifty thousand premium-model tokens. For a real chargeback figure you must weight tokens by the model's `$/token` — attribute **model-weighted cost**, not raw token count. Key your policy on `x-ai-eg-model` as well as identity so each model's spend is separable and can be priced correctly downstream.

!!! apigee "From Apigee"
    You already run this play for REST. Cost attribution is **Apigee Analytics plus monetization, with tokens as the billable unit.** Analytics sliced traffic by developer, app, and API product; here you slice metered tokens by user, team, and app — the same dimensional rollup, different fact. Monetization let you attach rate plans and spend caps to a developer or product; a per-team token budget is that spend cap, enforced as a **Quota** (3.1) rather than a billing record. The mental model — *identity → entitlement → metered usage → enforced limit → report* — is unchanged. What's new is only that the meter reads variable token cost from each response instead of `+1` per call.

!!! java "From Java microservices"
    This is the **chargeback you'd otherwise hand-build from Micrometer.** You'd tag an LLM-call timer or counter with `team` and `model`, scrape it into Prometheus, and write a Grafana panel that sums token cost per team — then realise the counter is per-pod, drifts under autoscaling, and has no enforcement, so you bolt on a custom budget check in each service. The gateway makes that a first-class, fleet-wide concern: one distributed counter, grouped by identity, with the budget *enforced at the edge* instead of advisory in a dashboard. You stop maintaining attribution plumbing and start declaring it.

!!! breaks "Where the analogy breaks"
    Apigee monetization and your Micrometer counters both record cost *after the fact* — they're reporting systems; the spend already happened. A gateway token budget is also an **enforcement** system: cross the line and the next call gets `429`. That fusion of *accounting* and *admission control* in one policy has no clean equivalent on either side. The subtler break is from 3.1 and still bites here: because token cost is only known after the response, a team's budget can **overshoot by one in-flight call** before the counter catches up. Your chargeback total is exact; your real-time enforcement is "next request," not "this request." Reason about it as a billing meter that also brakes, not as a hard pre-admission gate.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — per-team spend breakdown and a team budget

**Prereqs:** the gateway and `AIGatewayRoute` from 1.5 with token metering working from 3.1 (export `$NAMESPACE`, `$GATEWAY_HOST`, `$GATEWAY_KEY`), and `kubectl`. Callers already pass `x-user-id`; you'll now add `x-team`.

**1. Tag traffic with a team identity.** Attribution starts at the request. Have each caller stamp the team it belongs to — in Spring this is one header on the outbound `ChatClient`/`RestClient`; here we set it directly on the curl so you can see it work:

```bash
# the payments team makes a call
curl -s -o /dev/null -w "payments -> HTTP %{http_code}\n" \
  "https://$GATEWAY_HOST/v1/chat/completions" \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "x-user-id: alice" -H "x-team: payments" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","max_tokens":256,
       "messages":[{"role":"user","content":"Summarise this invoice."}]}'
```

**2. Enforce a per-team budget** — the 3.1 policy, re-keyed on `x-team` and `x-ai-eg-model` so each team's spend is separable per model and priceable:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: team-budget
  namespace: ${NAMESPACE}
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: ai-gateway-route            # the route from 1.5
  rateLimit:
    type: Global
    global:
      rules:
        - clientSelectors:
            - headers:
                - name: x-team             # the cost centre we bill
                  type: Distinct
                - name: x-ai-eg-model      # keep model separable for pricing
                  type: Distinct
          limit:
            requests: 2000000             # 2M tokens per team, per model...
            unit: Day                     # ...per day — the team's daily budget
          cost:
            request:
              number: 0                   # don't charge on the way in
            response:
              from: Metadata              # charge the tokens the model reported
              metadata:
                namespace: io.envoy.ai_gateway
                key: llm_total_token
```

**3. Apply it and confirm acceptance:**

```bash
kubectl apply -f team-budget.yaml
kubectl get backendtrafficpolicy team-budget -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[0].type}{"\n"}'
```

**4. Produce a per-team spend breakdown.** Drive a little traffic as two teams on two models, then read the per-identity token usage the gateway records (in a managed deployment this is the TAOD cost dashboard; self-hosted, read it from the metrics the gateway emits, keyed by `x-team` and `x-ai-eg-model`):

```bash
# scrape per-team, per-model token usage from the gateway's metrics endpoint
curl -s "http://localhost:9090/metrics" \
  | grep -E 'ai_gateway.*(team|model)' \
  | grep -i token
```

!!! pitfall "Watch out"
    A `Distinct` selector creates a **separate counter per observed header value**, so a team that never calls has no counter and a *misspelled* `x-team` silently gets its own private budget — spend you'll never see in the rollup. Validate the header against an allow-list of real teams at the edge (4.x identity), or one fat-fingered `x-team: payment` becomes untracked, unbudgeted shadow spend.

**What success looks like:** you can show a token-spend figure broken out by `x-team` (and by model within each team), and once a team crosses its daily token budget for a model, that team's further calls on that model return **`429`** — while other teams, and the same team on other models, are unaffected. You've turned the 3.1 token meter into an attributed, enforced, per-cost-centre budget.
</div>

## Verify it

You've built real cost governance when each of these holds:

```bash
# same load under two x-team values lands in two separate counters
for team in payments search; do
  curl -s -o /dev/null -w "$team -> %{http_code}\n" \
    "https://$GATEWAY_HOST/v1/chat/completions" \
    -H "Authorization: Bearer $GATEWAY_KEY" \
    -H "x-user-id: svc" -H "x-team: $team" -H "content-type: application/json" \
    -d '{"model":"gpt-4o-mini","max_tokens":512,
         "messages":[{"role":"user","content":"Write a long essay."}]}'
done
```

- Two different `x-team` values get **independent budgets** — if they share one counter, your `clientSelectors` aren't keyed on the team header.
- A team's premium-model spend and cheap-model spend show up as **separate lines** — because you keyed on `x-ai-eg-model`. If they're merged, you can't price the bill correctly.
- The spend breakdown sums to roughly the total tokens the route metered in 3.1 — attribution should *partition* usage, not lose or double-count it.

!!! failure "Common failure modes"
    - **Attributing by token count, not cost.** Ranking teams by raw tokens hides the team quietly burning a frontier model. Always weight by the model's price — see the pitfall above.
    - **No identity on the request.** If callers don't stamp `x-team`/app id, every dimension collapses to one anonymous bucket and attribution is impossible. The header is the whole game; enforce it (4.x).
    - **Trusting an unvalidated identity header.** A client can send any `x-team` string. Until you bind identity to an authenticated principal (4.1), a caller can mislabel its spend onto another team — or onto a typo'd team that escapes the rollup.
    - **Confusing showback with enforcement.** A dashboard that *reports* overspend is not a budget. If there's no `BackendTrafficPolicy` with a `limit`, nothing actually throttles when a team blows its cap.
    - **One global budget instead of per-team.** A single fleet-wide limit protects the provider but tells finance nothing and lets one team starve the rest. Budgets must be keyed on the cost centre.

!!! stretch "Stretch goal"
    Build a tiny model-price table (`$/1K tokens` per model) and combine it with the per-team, per-model token breakdown to compute **model-weighted monthly cost per team** — the actual chargeback number. Then compare two teams with identical token totals but different model mixes and watch their bills diverge. That divergence is precisely why raw token attribution misleads, and why TAOD prices spend by model before it governs it.

## Recap & next

You can now attribute metered tokens to a **user, team, and app** via identity headers, produce a per-team (and per-model) spend breakdown, and enforce a **per-team budget** by re-keying the 3.1 token counter — the core loop of Tetrate Agent Operations Director. Crucially, you know that cost is **model-weighted**, not raw tokens, so your chargeback reflects money rather than volume.

**Next — 3.3:** decide not just *how much* a team may spend, but *which models it's even allowed to call*. You'll define **sanctioned-model access tiers** — an API Product for models — and prove a cheap-tier caller is refused the premium model with a `403`.
