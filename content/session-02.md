# 1.2 — The AI gateway vs the API gateway you know

!!! bottomline "Bottom line"
    Most of Apigee transfers to an AI gateway one-to-one: a proxy becomes a route, a TargetServer becomes a backend, a Quota becomes a rate limit, an API Product becomes a model tier. By the end of this session you can map your Apigee mental model onto the AI gateway object-by-object — and name the **three things that genuinely don't map**, because the payload is now tokens, prompts, and tool calls.

!!! eli5 "In plain words"
    You already have a friendly helper at your front door who takes in all your normal toy deliveries, checks the labels, and keeps things tidy. Now the talking robots are moving in too, and they need a door helper of their very own. It's the same kind of helper doing mostly the same job — but because robots talk in words and reach into cupboards, this one learns a few brand-new tricks the old helper never needed. Lining up your trusty door helper against the robots' new one is comparing the **API gateway** you know with the **AI gateway**.

## Why this exists

You already operate a gateway. When someone says "AI gateway," your instinct is right: it's the same silhouette you trust from Apigee — one governed edge, policies in the middle, a backend behind it. The fastest way to learn the AI gateway is therefore *not* to learn it from scratch, but to lay it over Apigee and ask "what's the same, what's renamed, and what's actually new?"

The answer is reassuring: the overwhelming majority is renamed, not new. Caller identity, quotas, product tiers, target routing, backend credentials, analytics — all have direct equivalents, and the reasons they exist are identical. If you can justify why a team shouldn't call a backend directly without a proxy, you can justify the AI gateway.

But three things resist the mapping, and they are the reason this whole course exists. They all trace back to one fact: an Apigee proxy moves **structured JSON priced per request**, while an AI gateway moves **natural language priced per token**, and increasingly **tool calls inside a stateful agent session**. Hold those three — cost unit, payload, statefulness — as you read the table below. Everything new lives there.

!!! apigee "From Apigee"
    Here is the object-by-object map. Read it as "the thing you'd reach for, and its AI-gateway name." The shapes are so close that your runtime intuition mostly survives intact.

    | Apigee object | AI-gateway equivalent | What stays the same |
    | --- | --- | --- |
    | API Proxy / ProxyEndpoint | `AIGatewayRoute` | One front door; rules match the request and route it |
    | TargetServer / target endpoint | `AIServiceBackend` | A named upstream the route forwards to |
    | Target auth / KVM secret | `BackendSecurityPolicy` | The *gateway* holds the backend credential, not the client |
    | VerifyAPIKey / OAuthV2 / VerifyJWT | Caller auth at the edge (4.1) | Identity is the gate before any limit applies |
    | Quota policy | Token rate limit / `BackendTrafficPolicy` (3.1) | A distributed counter over an interval, keyed by identity |
    | API Product | Model tier / sanctioned-model access (3.3) | "Who is entitled to call what" |
    | Analytics | AI analytics & traces (6.1) | Who called what, how often, at what cost |
    | Threat protection (JSON/regex) | Guardrails (4.2–4.4) | Inspect and reject hostile input before the backend |

    If this table feels like a renaming exercise, that's the point — for most of the platform, it is.

!!! java "From Java microservices"
    The instinct is the same one you act on every time you push cross-cutting code out of a service. The retry, the timeout, the auth filter, the metrics, the egress allow-list — you already know these don't belong copy-pasted in every service; they belong at an edge. The AI gateway is that move applied to LLM calls. The only twist is that the "request" your edge now governs is a **chat completion** — an OpenAI-shaped JSON body whose `messages` are free text and whose cost you won't know until the response comes back. Your `ChatClient` call site stops owning keys, retries, and budgets; it just points at one base URL.

!!! breaks "Where the analogy breaks"
    Three things have no clean Apigee *or* Spring equivalent, and they're exactly the rows you'll spend most of this course on:

    1. **The cost unit is variable and after-the-fact.** Apigee Quota does `+1` per call; you know the cost on arrival. An LLM call costs a variable number of **tokens you only learn from the response**, so admission control and budgets have to reason about cost they can't see yet.
    2. **The payload is natural language and tool calls.** "Validate the input" meant a JSON schema. Now it means prompt-injection defense, PII redaction from free text, and output moderation — judgments, not schema checks. There is no `@Valid` and no Apigee JSON-threat policy that covers this.
    3. **Agentic traffic is stateful and multi-step.** A proxy call is one request/response. An agent run is a *conversation* of model and tool calls over a long-lived MCP session (Part 5). Reasoning about it like one stateless proxy hop will mislead you on sessions, ordering, and budgets.

## The concept

The mapping is best held as a picture: the same request path you know, with AI-specific units flowing through it. Your app speaks one OpenAI-compatible API to one endpoint; the gateway authenticates, meters **tokens**, applies **guardrails**, and routes to a provider — the boxes are familiar, the units inside them are new.

<figure class="svg-figure">
<img src="assets/svg/ai-request-path.svg" alt="Your app calls one endpoint; the AI gateway authenticates, meters tokens, applies guardrails, routes to a provider, and meters the response back.">
<figcaption>The same gateway silhouette you know from Apigee, re-labelled for AI. Identity, routing, and analytics map cleanly; the metering unit (tokens), the guardrails (natural language), and the agentic session are the genuinely new parts.</figcaption>
</figure>

The discipline that makes this mapping useful is *honesty about the gaps*. It's tempting to declare victory at "Quota → rate limit" and move on — but a request quota and a token limit behave differently the moment a single huge completion arrives, and treating them as identical is how budgets get blown. Map what maps; circle what doesn't.

!!! pitfall "Watch out"
    The most expensive mistake is mapping **Quota → token limit** and assuming the *semantics* carry over too. They don't. An Apigee Quota admits or rejects a call *before* it runs, on a cost you already know. A token limit can only know a call's true cost *after* the model responds — so the limit governs the *next* request, and one in-flight call can overshoot its own budget. Same row in the table, different behaviour. Treat the table as a map of *names*, not a guarantee of *semantics*.

## Hands-on lab

This is a conceptual session — no infrastructure. The deliverable is a mapping document for *your own* estate, which becomes the reference you check every later session against.

<div class="lab" markdown="1">
#### Lab — map your Apigee estate onto the AI gateway

**1. Start from the mapping table** and copy it into a doc for your team. Fill the right-hand side with the *actual* names from your world — your real proxy names, TargetServers, Quota policies, and API Products:

```text
Apigee object (yours)         → AI-gateway equivalent      → notes / who owns it
----------------------------------------------------------------------------------
proxy: payments-llm-v1        → AIGatewayRoute             → platform team
TargetServer: openai-prod     → AIServiceBackend           → holds no key in-app
KVM: openai-secret            → BackendSecurityPolicy      → gateway injects key
Quota: 1000/min per app       → token rate limit           → !! unit changes (see below)
API Product: gold-tier        → model tier / catalog       → which models, which teams
Analytics dashboards          → AI analytics & traces      → new dims: model, tokens, $
JSONThreatProtection          → guardrails                 → now natural-language
```

**2. Now do the part that matters: circle the three things that DON'T map.** For each, write one sentence on *why* it breaks, grounded in your estate:

```text
[ ] COST UNIT — our Quota counts requests; AI cost is tokens we learn after the
    response. Our "1000/min" cap is meaningless when one call can cost 60k tokens.
[ ] PAYLOAD — our JSONThreatProtection validates schemas; prompts are free text.
    We have no policy today that defends against prompt injection or redacts PII.
[ ] STATEFULNESS — our proxies are stateless hops; an agent run is a multi-step
    session over MCP. Our per-request thinking won't bound an agent loop.
```

!!! pitfall "Watch out"
    Don't let "Analytics → AI analytics" lull you. Your Apigee dashboards are keyed on requests, latency, and status codes — none of which is the number finance will ask for. The new mandatory dimensions are **model**, **tokens**, and **cost per identity**. If your mapping doc doesn't flag that your current analytics literally cannot produce a per-team token-spend number, you've under-counted the gap (you build exactly that report in 3.2).

**3. Pick one row and pressure-test it** by writing the worst question a stakeholder could ask. For the Quota row: *"A user fired ten 50k-token summarisations in a minute and stayed under our 1000-requests/min Quota — what stopped the spend?"* If your current Apigee setup has no answer, you've just written the business case for session 3.1.

**What success looks like:** a one-page mapping doc for your real estate where every Apigee object has an AI-gateway equivalent, **and three items are circled as genuinely new** — each with a one-sentence reason tied to tokens, natural-language payloads, or agentic statefulness. That circled list is your reading order for the rest of the course.
</div>

## Verify it

You're ready to move on when you can answer, without looking back:

- Name the AI-gateway equivalent of an Apigee proxy, TargetServer, KVM secret, Quota, and API Product. *(`AIGatewayRoute`, `AIServiceBackend`, `BackendSecurityPolicy`, token rate limit / `BackendTrafficPolicy`, model tier.)*
- Which Apigee row maps in *name* but not in *semantics*, and why? *(Quota → token limit: a request quota admits before the call; a token limit charges after the response, so it governs the next request.)*
- What are the three things that don't map at all? *(Variable after-the-fact token cost; natural-language and tool-call payload; stateful multi-step agent sessions.)*

!!! failure "Common failure modes"
    - **Stopping at the rename.** Declaring "Quota = token limit, done" hides the after-the-fact cost behaviour and the overshoot it allows. The table is a map of names; verify semantics row by row.
    - **Assuming your existing analytics covers AI.** Request-keyed dashboards can't answer token-spend-per-team. Mapping Analytics → AI analytics without flagging the new dimensions undercounts the work.
    - **Treating guardrails as just another JSONThreatProtection.** Schema validation doesn't generalise to prompt injection, PII in free text, or output moderation — those are judgments about language, not structure.
    - **Forgetting statefulness exists.** Mapping every row as a stateless proxy hop leaves agentic traffic (Part 5) completely ungoverned, because an agent run is a session, not a request.

!!! stretch "Stretch goal"
    Take **five** concrete objects from your real Apigee org — name actual proxies, TargetServers, and API Products — and build the two-column table for them. Then mark, with a clear symbol, which of the five has *no clean AI-gateway analogue* and why. Most teams find one of their five (often a threat-protection or a request-Quota policy) lands squarely in the "doesn't map" column — and that object is usually the most important one to redesign first.

## Recap & next

You can now lay your Apigee mental model over the AI gateway object-by-object: proxy → route, TargetServer → backend, KVM → security policy, Quota → token limit, Product → model tier, Analytics → AI analytics, threat protection → guardrails. Crucially, you can name the three things that don't map — variable after-the-fact token cost, natural-language and tool-call payloads, and stateful agentic sessions — and you've circled them for your own estate.

**Next — 1.3:** with the mental model in place, meet the vendor landscape — Tetrate's managed Agent Router Service, Agent Router Enterprise, Agent Operations Director, and the open-source Envoy AI Gateway they all build on — and pick the on-ramp that fits your org.
