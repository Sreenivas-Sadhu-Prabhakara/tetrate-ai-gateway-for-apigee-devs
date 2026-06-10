# 6.1 — Observability for AI traffic: tokens, latency & tracing

!!! bottomline "Bottom line"
    Everything you governed in Parts 3–5 — tokens, budgets, models, guardrails, tools — is only as good as your ability to **see it happening**. By the end of this session you can enable token-usage and latency metrics on the gateway and read an **OpenInference** trace of a multi-step agent run, with per-step token and latency breakdowns, so "what is our AI traffic actually doing, and what is it costing" becomes a dashboard query instead of a guess.

!!! eli5 "In plain words"
    Every time the helper runs an errand, they jot it down in a little diary: how long the trip took, how many words were said, and which robots did the work. Later the grown-ups can flip through the diary and see exactly what happened all day — no guessing. Keeping that diary of every trip is **observability**.

## Why this exists

You spent five parts moving cross-cutting concerns to the edge. The payoff is that the edge is now the **one place every LLM and tool call passes through** — which makes it the one place you can measure them. A direct-from-each-service estate can't answer "tokens per team last month" (you proved that in session 1.1's stretch goal); a governed edge answers it because every call is metered there.

But AI observability is not just "the same dashboards with new labels." The interesting dimensions are **model, tokens, and tool calls**, and the interesting unit of work is no longer a request — it's an **agent run** that fans a single user prompt into many model and tool spans. If you only chart per-request metrics, you will systematically under-count what your agents cost. This session wires the metrics and traces; 6.2 uses them to debug.

!!! apigee "From Apigee"
    This is **Apigee Analytics for AI traffic.** The built-in dashboards and the trace timeline are the same instinct — observe traffic at the proxy, slice it by dimensions — but the dimensions change. Where Apigee gave you traffic, latency, error rate, and cache hit sliced by proxy, target, and developer app, the AI gateway gives you the same skeleton sliced by **model, token count (prompt vs. completion), tool call, and cost**. Tetrate Agent Operations Director is the cost/usage dashboard layer on top, the way Apigee's Analytics dashboards sat on top of the raw metrics.

    | Apigee Analytics | AI gateway observability |
    |---|---|
    | Traffic (request count) | Request count **+ token count** (prompt / completion) |
    | Target latency | Model latency, **time-to-first-token** for streaming |
    | Dimensions: proxy, target, app | Dimensions: model, user/team, tool, **cost** |
    | Trace/Debug session | OpenInference trace of LLM + agent spans |

!!! java "From Java microservices"
    You already know the shape: **Micrometer** counters and timers exposed through Actuator, scraped to Prometheus, plus **distributed traces** (OpenTelemetry, Sleuth-era spans) so one request's path across services is one connected trace. The difference is *where the instrumentation lives*. You won't add a `@Timed` to every LLM call in every service — the gateway emits the token and latency metrics for all of them, and stamps spans following **OpenInference** semantic conventions (the LLM-aware vocabulary: prompt, model, token counts, tool calls) so an agent run reads as one trace instead of disconnected client calls.

!!! breaks "Where the analogy breaks"
    In a REST estate, one inbound request is roughly one span and one unit of cost — so per-request dashboards tell the truth. For agents that assumption collapses. A **single** user prompt fans out into a planning model call, several tool calls, and a synthesis call — many spans, variable token cost each, all belonging to **one logical interaction**. Per-request metrics will show you a flat-looking graph while spend climbs, because the expensive thing (the agent run) isn't your unit of measurement. You have to trace and aggregate at the **session/run** level, not the request level — there is no Apigee proxy or Spring controller whose "one call" maps cleanly onto it.

## The concept

Observability isn't a new box bolted on — it reads the same governance stack every request already descends through, emitting a metric and a span at each layer:

<figure class="svg-figure">
<img src="assets/svg/governance-stack.svg" alt="A request descends through identity, model entitlement, token rate limit, budget attribution, and guardrails; each layer emits token-usage and latency metrics and an OpenInference span, metered to cost.">
<figcaption>The same governance stack from Part 3, read as a telemetry source. Each layer the request passes through emits token-usage and latency metrics and contributes an OpenInference span; an agent run is the sum of these across many model and tool calls.</figcaption>
</figure>

Concretely the gateway gives you two streams. **Metrics**: token-usage counters (prompt, completion, total) and latency histograms, labelled by model and caller — the time series you alert and dashboard on. **Traces**: per-call spans following OpenInference conventions, so a model call carries its prompt reference, model name, and token counts, and a tool call carries its tool name and arguments — stitched into one trace per agent run. Tetrate Agent Operations Director consumes the usage stream to surface cost/usage dashboards; your existing OpenTelemetry backend (Tempo, Jaeger, Honeycomb) consumes the spans.

!!! pitfall "Watch out"
    Per-request metrics hide agentic cost. One user prompt fans into many spans, so a "requests/min" or "p95 latency per request" panel can look healthy while a runaway agent loops through dozens of tool calls per interaction. Build at least one panel keyed on the **session/run**, not the HTTP request, or you will be blind to exactly the traffic Part 5 introduced.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — enable telemetry and read a per-step agent run

**Prereqs:** the self-hosted gateway from 1.5 with at least one `AIGatewayRoute`, an agent flow from Part 5 (an `MCPRoute` and a tool server), `kubectl`, and an OpenTelemetry collector reachable in-cluster. Export `$NAMESPACE`, `$GATEWAY_HOST`, and `$GATEWAY_KEY`.

**1. Point the gateway's telemetry at your collector.** Token-usage and latency metrics are emitted in Prometheus format; traces are exported over OTLP. Configure the OTLP endpoint so spans carry OpenInference attributes:

```yaml
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: AIGatewayTelemetry
metadata:
  name: ai-telemetry
  namespace: ${NAMESPACE}
spec:
  metrics:
    prometheus:
      enabled: true          # token + latency time series, scraped by Prometheus
  tracing:
    otlp:
      endpoint: otel-collector.${NAMESPACE}.svc.cluster.local:4317
      semantics: OpenInference   # prompt, model, tokens, tool-call span attributes
```

!!! pitfall "Watch out"
    Prompts and PII can leak into traces. OpenInference spans *can* carry the prompt text and tool arguments — useful for debugging, dangerous in a shared tracing backend. Capture references or redact span content (tie this back to the PII redaction from 4.3); never ship raw prompts to a trace store your whole org can read.

**2. Apply it and confirm metrics are flowing.** Hit the gateway once, then read the token counters straight off the metrics endpoint:

```bash
kubectl apply -f ai-telemetry.yaml
curl -s "https://$GATEWAY_HOST/v1/chat/completions" \
  -H "authorization: Bearer $GATEWAY_KEY" -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}]}' >/dev/null
# token-usage time series the gateway just incremented:
kubectl exec -n "$NAMESPACE" deploy/ai-gateway -- \
  curl -s localhost:19001/metrics | grep -E "gen_ai_.*token|ai_gateway.*token"
```

**3. Run an agent interaction and read it as ONE trace.** Trigger a Part 5 agent run (one prompt that uses tools), then pull the trace and break it down per step:

```bash
# kick off an agent run that plans, calls tools, then synthesises
curl -s "https://$GATEWAY_HOST/v1/chat/completions" \
  -H "authorization: Bearer $GATEWAY_KEY" -H "content-type: application/json" \
  -H "x-session-id: demo-run-1" -d @agent-run.json >/dev/null

# fetch the trace for that session from your OTLP backend (Tempo shown)
curl -s "http://tempo.${NAMESPACE}.svc.cluster.local:3100/api/search?tags=session.id=demo-run-1" \
  | jq '.traces[0].spanSet.spans[] | {name, tokens: .attributes["llm.token_count.total"], ms: .durationMs}'
```

You should see several spans under one trace: a model span (planning), one span per tool call, and a final model span (synthesis) — each with its own token count and latency. Sum the token columns and you have the **true cost of that single user prompt**, not just the cost of the last HTTP request.

**What success looks like:** the metrics endpoint shows token-usage and latency series labelled by model; and one agent interaction renders as a **single OpenInference trace** whose per-step breakdown lets you point at exactly which model call or tool call dominated the tokens and the latency.
</div>

## Verify it

!!! failure "Common failure modes"
    - **Dashboarding requests, not tokens or runs.** A request-count panel looks flat while token spend climbs; if your primary cost view isn't token- and session-keyed, you're measuring the wrong thing.
    - **Spans that don't join up.** Without a stable session/run correlator (e.g. `x-session-id`), each model and tool call is an orphan span and the agent run is unreadable — you lose the per-step breakdown entirely.
    - **Raw prompts/PII in traces.** Capturing full prompt text into a shared trace backend turns observability into a data leak; redact or reference instead (4.3).
    - **No collector, silent drop.** If the OTLP endpoint is wrong or down, the gateway emits spans into the void and you debug 6.2 blind. Confirm spans land *before* you need them.
    - **Reading p95 per request as agent latency.** An agent run's wall-clock is the trace duration, not any single span — per-request latency understates what the user actually waited.

!!! stretch "Stretch goal"
    Take one real agent run and compute two numbers from its trace: total tokens across all spans, and the share contributed by tool-call round-trips versus model calls. Then compare that to the single per-request token metric you'd have seen without tracing. The gap between the two is precisely the agentic cost that per-request observability was hiding — and the business case for session-level dashboards in Agent Operations Director.

## Recap & next

You can now enable token-usage and latency **metrics** and OpenInference **traces** on the gateway, read a multi-step agent run as one trace with per-step token and latency breakdowns, and explain why agentic cost must be observed at the **session level** rather than per request — while keeping prompts and PII out of your trace store. You have the signals; now you use them under pressure.

**Next — 6.2:** when a request misbehaves, you'll turn these same signals into a debugger — stepping a failing call through routing, guardrails, and the provider to find exactly **which policy fired and why**.
