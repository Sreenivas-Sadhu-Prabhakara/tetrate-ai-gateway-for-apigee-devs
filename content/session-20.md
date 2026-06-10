# 5.1 — From chat to agents: what changes for the gateway

!!! bottomline "Bottom line"
    A chat completion is **one** gateway round-trip: prompt in, answer out. An **agent** is a *loop* of them — the model asks for a tool, the gateway runs the tool call, the result goes back to the model, and it asks again, until it finally answers. The unit of work stops being a single request and becomes a **multi-step session**. By the end you can trace one agent run, count how many gateway round-trips a single user prompt actually produces, and see why budgets, guardrails, and identity must now span the whole loop, not one call.

## Why this exists

Everything in Parts 1–4 governs a request. One prompt arrives, policies run, a model is called, a metered answer returns. That model holds for *chat*. It quietly breaks for *agents*.

An agentic workload doesn't answer in one shot. The model decides, mid-flight, that it needs to **call a tool** — search a repo, query a database, hit an internal API — and emits a tool-call instead of a final answer. Your runtime executes that tool, feeds the result back, and the model continues. One user question can drive a dozen of these hops before the model has enough to answer. Tool calling, multi-step loops, and long-running sessions are the defining shape of agentic traffic, and none of them existed in the chat model.

A second property compounds it: these sessions are **long-running and stateful**. A chat completion is over in seconds; an agent run can span minutes, hold an open session, and carry an ever-growing transcript that every model hop re-sends. So the gateway isn't just seeing more requests — it's seeing requests that are *related*, *ordered*, and *cumulative*, where the cost and risk of hop N depend on everything that happened in hops 1…N−1.

This matters to the gateway because **every hop is gateway traffic**. Every model call in the loop is metered, guardrailed, and identity-checked; every tool call (Part 5.2 onward, through an `MCPRoute`) is routed, authorized, and audited. If your governance reasons about "the request," it governs the first hop and goes blind for the rest. The gateway has to govern the **whole loop** — every model hop *and* every tool call — as one session.

!!! apigee "From Apigee"
    An agent call is **not one proxy invocation — it's a conversation of them.** The closest thing you've built is *orchestration*: a proxy that fans out to several backends, or a flow that chains service callouts, where the client's single request triggers a sequence of governed sub-calls behind the facade. An agent run is that, but the *sequence is decided by the model at runtime*, not by your flow logic — the model chooses the next tool mid-conversation. So your Quota, SpikeArrest, and analytics now have to reason about a **session of related calls**, not one transaction. If you've ever had to make a Quota span a multi-step orchestration rather than a single proxy hit, you've felt the shape of this problem.

!!! java "From Java microservices"
    It's the difference between one `@GetMapping` that returns in a single call and a **saga** — a coordinated sequence of service calls, each dependent on the last, that together fulfil one business intent. A single endpoint is easy to govern; a saga's cost, failure modes, and authorization span the whole chain. With agents the gateway governs the saga: every model hop and every tool call in the loop, as one unit. Reaching for your single-request mental model (one controller, one trace, one cost) will undercount an agent run the same way it would undercount a saga that fans into a dozen downstream calls.

!!! breaks "Where the analogy breaks"
    Both an Apigee orchestration and a Spring saga have a **fixed, author-defined call graph** — you wrote the steps, so you know them ahead of time. An agent loop is **dynamic and emergent**: the model decides at each hop whether to call a tool, which one, and whether to keep going, so the number of round-trips and their cost are unknown until the run finishes. You can't pre-size a budget or pre-list the calls the way you would for a saga. Governance has to be *reactive across an open-ended loop* — capping iterations, accumulating spend as it happens, and being ready for a run that takes one hop or thirty. That open-endedness has no clean equivalent in either world.

## The concept

A single user prompt fans out into many governed round-trips. The model and the tools alternate, and the gateway sits on **every** arrow:

```widget
{
  "type": "sequence",
  "title": "One user prompt → many gateway round-trips",
  "actors": [
    {"id": "user", "label": "User / app"},
    {"id": "gw", "label": "AI gateway"},
    {"id": "model", "label": "Model"},
    {"id": "tool", "label": "Tool server"}
  ],
  "steps": [
    {"from": "user", "to": "gw", "label": "prompt: \"What's failing in prod?\"", "note": "ONE user request. Identity, guardrails, budget all attach here — but the work has only just begun."},
    {"from": "gw", "to": "model", "label": "round-trip 1: completion", "note": "Metered + guardrailed. The model doesn't answer — it asks to call a tool."},
    {"from": "model", "to": "gw", "kind": "return", "label": "tool_call: search_logs(...)", "note": "A tool-call, not a final answer. The loop begins."},
    {"from": "gw", "to": "tool", "label": "round-trip 2: tools/call (MCPRoute, 5.2)", "note": "Routed, authorized, audited as its own governed hop — see Part 5.2."},
    {"from": "tool", "to": "gw", "kind": "return", "label": "tool result: log excerpts", "note": "Result returned to the gateway, then handed back to the model."},
    {"from": "gw", "to": "model", "label": "round-trip 3: completion (with tool result)", "note": "Another metered model call. It may ask for ANOTHER tool — the loop can repeat many times."},
    {"from": "model", "to": "gw", "kind": "return", "label": "tool_call: query_metrics(...)", "note": "And again: model → tool → model. Each iteration is more tokens and another tool hop."},
    {"from": "gw", "to": "model", "label": "round-trip N: final completion", "note": "Only now does the model produce a real answer instead of a tool-call."},
    {"from": "gw", "to": "user", "kind": "return", "label": "answer (after N round-trips)", "note": "The user sees ONE response. Behind it: many metered model calls and tool calls the gateway governed as one session."}
  ]
}
```

The shift is from **one request** to **a session that spans many requests**. That reframes every governance primitive you built:

- **Budgets (3.2)** must accumulate across the loop. Token cost isn't the first call — it's the *sum* of every model hop in the run, and they compound because each step re-sends growing context.
- **Guardrails (4.2–4.4)** must run on every hop. A tool result re-entering the model is fresh untrusted input; output moderation has to judge the *final* answer after N hops, not just hop one.
- **Identity (4.1)** must propagate through the loop. The caller's identity and entitlements have to ride every model and tool call, or a mid-loop tool call runs as nobody — and tool calls are precisely where an agent can read or change real systems.

None of these are new *mechanisms* — they're the same metering, guardrail, and identity primitives from Parts 3 and 4. What changes is their **scope**: they have to be applied per-session across an open-ended loop instead of per-request to a single call. Getting that scope wrong is the dominant failure mode of agentic governance, and the rest of Part 5 is about getting it right for the tool half of the loop.

!!! pitfall "Watch out"
    **Budgeting per top-level request hides the real cost.** If you meter "the user's request" as one unit, you've counted one model call and missed the other N−1 plus every tool call. One innocent-looking prompt can fan into *dozens* of metered model+tool round-trips, and because each model hop re-sends an ever-growing context window, the token cost grows **super-linearly** across the loop — late hops are the expensive ones. Budget the **whole agent session**, not the prompt that started it, or your cost controls (3.1, 3.2) are governing a fraction of the spend and the rest runs unmetered.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — count the round-trips behind one prompt

**Prereqs:** the self-hosted gateway from 1.5 with telemetry on (you wire this fully in 6.1), a tool-calling agent pointed at the gateway, and access to the gateway's request logs/metrics. Export `$NAMESPACE`, `$GATEWAY_HOST`, `$GATEWAY_KEY`. This lab is about *observing* the loop, not configuring a new policy.

**1. Run a single agent prompt that forces tool use.** Give the agent a question it can't answer without calling at least one tool, and tag the run so you can isolate it in the logs:

```bash
curl -s "https://$GATEWAY_HOST/v1/chat/completions" \
  -H "Authorization: Bearer $GATEWAY_KEY" -H "content-type: application/json" \
  -H "x-session-id: agent-run-001" \
  -d '{"model":"chat-default",
       "tools":[{"type":"function","function":{"name":"search_logs",
         "description":"Search production logs","parameters":{"type":"object",
         "properties":{"query":{"type":"string"}},"required":["query"]}}}],
       "messages":[{"role":"user","content":"What error is spiking in prod, and why?"}]}' \
  | jq '.choices[0].message.tool_calls'
# the model returns a tool_call, not an answer — the loop has started
```

**2. Count the gateway round-trips for that one prompt.** Filter the gateway access logs by your session tag and tally how many model calls and tool calls one user prompt produced:

```bash
# model calls the gateway saw for this run
kubectl logs -n "$NAMESPACE" -l app=ai-gateway --tail=2000 \
  | grep "agent-run-001" \
  | grep "/v1/chat/completions" \
  | wc -l
# → e.g. 5  (one user prompt, FIVE metered model round-trips)
```

!!! pitfall "Watch out"
    The number you get is **not** the number a per-request dashboard shows. A top-level metric counts `agent-run-001` as *one* request; the gateway logs show five model calls plus the tool calls behind them. If those two numbers disagree on your real dashboards, that gap is exactly the unmetered cost from the pitfall above — and it's why the rest of Part 5 governs the loop, not the request.

**3. Watch token cost accumulate across the loop.** Sum the per-hop token usage the gateway recorded for the session and note where it concentrates — the later, context-heavy hops:

```bash
kubectl logs -n "$NAMESPACE" -l app=ai-gateway --tail=2000 \
  | grep "agent-run-001" \
  | grep -o 'total_tokens=[0-9]*' \
  | awk -F= '{sum+=$2; print "hop tokens:", $2} END {print "SESSION TOTAL:", sum}'
# later hops carry more tokens — context grows each iteration
```

**What success looks like:** one user prompt, but the gateway logs show **multiple** model round-trips (plus tool calls) under the same session id, and the per-hop token counts **grow** through the loop so the session total dwarfs the first call. You can now state, with numbers, that an agent run is many governed round-trips and that cost accumulates across them — the premise the rest of Part 5 builds on.
</div>

## Verify it

!!! failure "Common failure modes"
    - **Counting the loop as one request.** A per-request view shows one call and one cost; the real run is N model hops plus tool calls. If your numbers say "1," you're measuring the prompt, not the session.
    - **Budgets that don't span the session.** A budget keyed only on the top-level request meters the first hop and lets the rest run free. Spend must accumulate across every hop of the run (3.2).
    - **Guardrails on hop one only.** Tool results re-entering the model are fresh untrusted input, and the final answer comes after N hops. Input and output guardrails have to apply across the loop, not just the opening call (4.2–4.4).
    - **Identity lost mid-loop.** If the caller's identity doesn't propagate to every model and tool call, a mid-loop tool call runs unauthenticated — and tool calls are exactly where damage happens (locked down in 5.4).
    - **Unbounded loops.** No cap on iterations means a confused agent can spin tool→model→tool indefinitely, burning tokens with no answer. Bound the loop and alert on runs that exceed it.

!!! stretch "Stretch goal"
    Take a single agent prompt and run it twice: once with a trivial question the model answers directly (one round-trip), once with a question that forces a chain of tool calls (many). Put the two session totals — round-trips and tokens — side by side. The ratio between them is the multiplier your *current* per-request budgeting is blind to. Then sketch what a session-scoped budget would have to track to catch the expensive run before it finished, not after the bill arrives.

## Recap & next

You can now articulate what changes when traffic goes agentic: the unit of work is a **multi-step session**, not a single request; one user prompt fans into many gateway round-trips (every model hop *and* every tool call); token cost accumulates and compounds across the loop; and budgets, guardrails, and identity must therefore span the whole session. This is the conceptual opener to Part 5 — the mechanics of the tool side follow.

**Next — 5.2:** the resource that fronts the tool half of the loop. You'll put the gateway in front of **Model Context Protocol (MCP) tool servers** with an `MCPRoute`, so an agent reaches every tool through one governed, observable, secured endpoint — the tools-shaped sibling of the `AIGatewayRoute` you already know.
