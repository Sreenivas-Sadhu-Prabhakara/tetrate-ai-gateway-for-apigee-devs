# 1.1 — What an AI gateway is — and why "just call OpenAI" stops working

!!! bottomline "Bottom line"
    An **AI gateway** is an API-management product for LLM and agent traffic: one governed edge that sits between your apps and the model providers. By the end of this session you can explain what it adds over calling a provider SDK directly from each service, and why that direct approach quietly falls apart once you have many apps, many models, and a finance team asking questions.

## Why this exists

Right now, somewhere in your estate, a Spring service does this: reads `OPENAI_API_KEY` from config, builds a `ChatClient`, calls `chat.completions`, maybe wraps it in a Resilience4j retry, logs the prompt, and ships. It works. So does the next service. And the next. That's exactly the problem.

Every one of those services now independently owns: a **provider key** (rotated how?), a **retry/fallback** policy (consistent with the others?), **logging** that may be leaking prompts containing PII, and **zero shared answer** to "how many tokens did the payments team burn on GPT-class models last week, and were they even allowed to use that model?" Multiply by every service and every model, and you have the same sprawl an API gateway was invented to solve — except the payload is now **tokens, prompts, and tool calls**, and the cost is metered by the *thousand tokens*, not the request.

An AI gateway is the one place those cross-cutting concerns live. Your apps make a normal, OpenAI-shaped call to **one endpoint**; the gateway authenticates the caller, meters tokens, enforces budgets and which models are allowed, redacts PII, applies guardrails, routes to the right provider (with fallback), and records every call for cost and audit.

## The concept

The shape is the same one you already trust from API management: a request enters one edge, policies run, a backend is called, and the response comes back metered. Hold this silhouette — we dissect each box in later sessions:

<figure class="svg-figure">
<img src="assets/svg/ai-request-path.svg" alt="Your app calls one endpoint; the AI gateway authenticates, meters tokens, applies guardrails, routes to a provider, and meters the response back.">
<figcaption>The AI gateway request path. Your app speaks one OpenAI-compatible API; the gateway governs the call and fans out to providers. This is the "you are here" map for the whole course.</figcaption>
</figure>

The mental shift is small but real: you stop thinking *"which provider SDK does this service call?"* and start thinking *"what is **configured** to run at the edge, for all of them?"* The unit of work becomes a **policy** at the gateway, not a code path in each app.

!!! pitfall "Watch out"
    Don't let "it's just a base-URL change" fool you into skipping the design. *Adoption* is one line; *what the edge enforces* — budgets, model access, guardrails — is real policy you have to think through. Teams that flip the base URL and configure nothing have moved the calls but governed nothing.

!!! apigee "From Apigee"
    You've built this before — for REST. An AI gateway is **Apigee for model traffic**. The proxy that fronted your backend is now an **AIGatewayRoute** fronting model providers; **VerifyAPIKey/OAuthV2** still gate the caller; **Quota** still protects capacity (but counts **tokens**); **API Products** still decide who gets what (but the "what" is **models and tiers**); **Analytics** still tells you who called what (but the dimensions are model, tokens, and cost). If you can explain why a team shouldn't call backends directly without a gateway, you already understand 80% of why they shouldn't call OpenAI directly either.

!!! java "From Java microservices"
    Think about the boilerplate around every LLM call in your services: the key in `application.yml`, the `RestClient`/`ChatClient` timeout and retry, the `try/catch` that falls back to a cheaper model, the logging interceptor. That's the **same cross-cutting code, copied per service** — exactly the stuff you'd normally push into a filter, an aspect, or a shared library. The gateway is that shared library, but as **operated infrastructure**: you point `spring.ai.openai.base-url` at it and delete the rest.

!!! breaks "Where the analogy breaks"
    Two things have no clean Apigee or Spring equivalent, and they're the reason this course exists:

    1. **The cost unit is non-deterministic.** A REST call is one unit against a quota. An LLM call costs a *variable* number of tokens you only know **after** the model responds. Rate limiting, budgets, and caching all have to reason about that — your per-pod `RateLimiter` and fixed `count` quotas simply can't.
    2. **The payload is natural language and tools.** "Validate the input" used to mean a schema. Now it means defending against **prompt injection**, redacting **PII from free text**, and authorizing **tool calls** an agent decides to make at runtime. There's no `@Valid` for that.

## Hands-on lab

You set up a real gateway in 1.4 (managed) and 1.5 (self-hosted). This first lab needs no infrastructure — it's the highest-leverage 20 minutes in the course: see your own sprawl, and preview the one-line change that ends it.

<div class="lab" markdown="1">
#### Lab — inventory your direct LLM calls

**1. Find every place your code calls a model provider directly.** In a Spring estate that's usually a base URL or an SDK client. Grep for the tells:

```bash
# provider SDKs, base URLs, and keys scattered across services
grep -rEn "api\.openai\.com|OpenAiChatModel|spring\.ai\.openai|OPENAI_API_KEY|anthropic|bedrockruntime" . \
  --include=*.java --include=*.yml --include=*.yaml --include=*.properties
```

**2. For each hit, tally what that call site owns itself** — be honest, most own all of it:

```text
[ ] the provider API key (and its rotation)
[ ] retry / timeout / fallback to another model
[ ] which model name is hard-coded
[ ] prompt/response logging (PII risk?)
[ ] any per-user or per-team cost limit (usually: none)
[ ] any guardrail on input or output (usually: none)
```

**3. Preview the change that's coming.** Here is the *only* code difference between calling OpenAI directly and calling it through a gateway — the base URL. Today (direct):

```yaml
# application.yml — direct to the provider, key in your app
spring:
  ai:
    openai:
      base-url: https://api.openai.com
      api-key: ${OPENAI_API_KEY}
```

In session 1.4 it becomes this — your app no longer holds a provider key at all:

```yaml
# application.yml — through the gateway; the gateway holds the provider key
spring:
  ai:
    openai:
      base-url: https://router.tetrate.ai     # the gateway endpoint
      api-key: ${GATEWAY_KEY}                  # a gateway key, not a provider key
```

!!! pitfall "Watch out"
    The provider key must leave the app *entirely* — not linger in `application.yml` "as a backup." Every app that still holds a real provider key can call the provider directly and bypass every gateway policy. Adoption isn't done until the app holds only a **gateway** key.

**What success looks like:** a written list of every direct call site and the concerns each duplicates, plus a clear understanding that adopting the gateway is, from the app's side, a **one-line base-URL change** — everything else you listed moves to the edge.
</div>

## Verify it

You're ready to move on when you can answer, without looking back:

- What does an AI gateway add over a direct provider call? *(One governed edge for identity, token metering, budgets, model access, guardrails, routing, and audit — instead of each service owning all of it.)*
- Why can't your existing per-pod rate limiter govern LLM cost? *(Cost is tokens, known only after the response, and counted across the whole fleet — not per request, per pod.)*
- From an app's perspective, how invasive is adoption? *(A base-URL change; the provider key leaves your app entirely.)*

!!! failure "Common failure modes"
    - **"We already have an API gateway, so we're covered."** A classic API gateway can proxy the HTTP call, but it doesn't understand tokens, can't meter cost, and has no concept of prompts or tools. *(Symptom: a quota that counts requests while your bill is measured in tokens.)*
    - **Treating the LLM call as just another REST call.** It's variable-cost and natural-language — the two things that make AI governance hard. Designing as if it's a fixed-cost JSON endpoint is how budgets and guardrails get skipped.
    - **Putting business logic at the AI edge.** The gateway governs *traffic*; your domain logic and prompt construction stay in the app. *(Symptom: the gateway "knows" about your use case.)*
    - **Letting provider keys stay in apps "for now."** Every app that holds a key is a rotation and leak liability the gateway exists to remove.

!!! stretch "Stretch goal"
    Take the inventory from the lab and estimate one number you almost certainly can't produce today: **token spend per team, last month.** Try to reconstruct it from logs. The difficulty of that exercise — and it will be difficult — is the single clearest business case for the gateway you'll build in this course.

## Recap & next

You can now say what an AI gateway is (an API-management product for model traffic), why direct per-service calls don't scale (duplicated concerns, no cost governance, leaking prompts), and how small adoption is from the app side (a base-URL change). You also have a concrete inventory of your own sprawl — the backlog this course works through.

**Next — 1.2:** map the AI gateway onto the **Apigee gateway you already know**, object by object — and pin down the handful of things that are genuinely new because the payload is tokens, prompts, and tools.
