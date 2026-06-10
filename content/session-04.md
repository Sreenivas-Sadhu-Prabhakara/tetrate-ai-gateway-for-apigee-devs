# 1.4 — Hello, gateway: TARS as a drop-in OpenAI endpoint

!!! bottomline "Bottom line"
    Your first governed LLM call is a **one-line change**: swap the OpenAI base URL for the Tetrate Agent Router Service endpoint, keep the same OpenAI-shaped request, and your provider key leaves your app entirely. By the end of this session the *same* client code returns completions from **two different model vendors** just by changing the `model` field — proof that the gateway, not your app, now owns providers and credentials.

## Why this exists

This is the payoff for the inventory you did in 1.1 and the mapping in 1.2: the moment you actually point a client at the gateway and watch governed completions come back. It's deliberately small. The whole promise of an OpenAI-compatible gateway is that adoption costs you a base URL, not a rewrite — and the fastest way to believe that is to do it.

It also lands the single most important property for a Java/Apigee team: **no provider key in your app**. Today your service reads `OPENAI_API_KEY` and holds a rotation-and-leak liability. After this change your app holds a *gateway* key (`$ROUTER_KEY`), and TARS holds the provider credentials. You've moved a secret out of every service and into one governed edge — the exact move you'd make in Apigee by putting backend auth in a KVM instead of in clients.

The second property you'll see is **provider portability**. Because the gateway speaks one OpenAI-compatible contract and routes by the `model` field, calling Anthropic vs OpenAI is a *string change in the request*, not a new SDK, a new auth scheme, or new error handling. That's the unification this whole course builds on.

!!! apigee "From Apigee"
    This is your **first passthrough proxy**. In Apigee you'd point a client at the proxy basepath (`https://org-env.apigee.net/payments`) instead of the backend host, and the proxy forwards it — the client never sees the target. Here you point the client at the gateway host (`https://router.tetrate.ai`) instead of `api.openai.com`, and TARS forwards to the real provider. The backend credential lives at the gateway (like target auth / a KVM secret), not in the caller. If you've ever cut a client over from a direct backend URL to an Apigee basepath and watched it just work, this is that — for model traffic.

!!! java "From Java microservices"
    In Spring AI you change exactly one property: `spring.ai.openai.base-url` (and set the api-key to your gateway key). The `ChatClient`, the `@Bean` wiring, the prompt-building code — all untouched, because the wire contract is still OpenAI Chat Completions. It's the same trick as repointing a `RestClient`/`WebClient` `baseUrl` at a gateway: the calling code doesn't know or care that a governed hop now sits in the path. With the raw OpenAI Java SDK it's identical — set the client's base URL to the router and the rest of your code compiles unchanged.

!!! breaks "Where the analogy breaks"
    An Apigee passthrough is genuinely transparent — same payload in, same payload out, fixed cost. This one isn't *quite*: the gateway is **metering tokens and attributing cost** on every call (you'll govern that in Part 3), so an identical request can succeed or be throttled depending on budget state your app can't see. And the `model` field isn't a routing hint you can ignore — it selects a *vendor with different behaviour, pricing, and token accounting*. Swapping `model` looks like changing a query param, but you may be changing provider, latency profile, and cost per call all at once. "Transparent passthrough" undersells what's happening.

## The concept

Frame the SVG you've already seen as "your first call through it": your app makes one OpenAI-shaped `POST /v1/chat/completions` to the gateway host; the gateway authenticates your caller key, picks the provider named by `model`, injects the *provider's* credential, calls it, meters the tokens in the response, and returns the standard OpenAI response body to you.

<figure class="svg-figure">
<img src="assets/svg/ai-request-path.svg" alt="Your app calls one endpoint; the AI gateway authenticates, meters tokens, applies guardrails, routes to a provider, and meters the response back.">
<figcaption>Your first call through the gateway. The app speaks OpenAI to one host; TARS authenticates the caller, routes by the <code>model</code> field to the right vendor, injects that vendor's key, and meters the response. The only thing your code changed was the base URL.</figcaption>
</figure>

The conceptual unlock is that the `model` field is now a **routing key into a catalog**, not a hard-coded coupling to one vendor's SDK. One client, one endpoint, one auth header — and the provider on the other side is a configuration decision, not a code decision.

!!! pitfall "Watch out"
    Get the base-URL shape right or you'll fight 404s. Spring AI appends the standard OpenAI paths (`/v1/chat/completions`) to the configured `base-url`, so you set the **host root** (e.g. `https://router.tetrate.ai`), *not* the full completions path — set the full path and you'll get a doubled `/v1/v1/...`. Conversely, raw `curl` needs the **full** path. Confirm the exact base URL and whether a `/v1` segment is expected against the current TARS / Continue.dev docs for your account before you debug anything else.

## Hands-on lab

Two routes to the same result: a raw `curl` to prove the wire contract, then Spring AI to prove the one-line change in real app config. You need `$ROUTER_KEY` from session 1.3.

<div class="lab" markdown="1">
#### Lab — your first governed call (and second vendor)

**1. Call TARS with curl** using the standard OpenAI Chat Completions body and your gateway key as a bearer token. Name a model from the TARS catalog in the `model` field:

```bash
curl -s https://router.tetrate.ai/v1/chat/completions \
  -H "Authorization: Bearer $ROUTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "In one sentence: what is an AI gateway?"}]
  }' | jq '{model, text: .choices[0].message.content, usage}'
```

You should get a normal OpenAI-shaped response — `choices[0].message.content` plus a `usage` block with token counts. That `usage` block is the gateway metering your call; it's the raw material for everything in Part 3.

**2. Switch vendors by changing ONE field.** Re-run the exact same request, changing only `model` to a different vendor's model in the catalog (e.g. an Anthropic Claude model). Everything else — host, auth header, body shape, your parsing — is identical:

```bash
curl -s https://router.tetrate.ai/v1/chat/completions \
  -H "Authorization: Bearer $ROUTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-4-sonnet-20250514",
    "messages": [{"role": "user", "content": "In one sentence: what is an AI gateway?"}]
  }' | jq '{model, text: .choices[0].message.content, usage}'
```

!!! pitfall "Watch out"
    The `model` string must match the **TARS catalog name** exactly, or you'll get a 404/400 for an unknown model — not a fallback. Catalog model IDs are versioned and provider-specific (e.g. `claude-4-sonnet-20250514`), so check the catalog (`https://router.tetrate.ai/models` or the portal) rather than guessing a vendor's marketing name. A wrong `model` is the single most common first-call failure.

**3. Make the same change in Spring AI** — the real-world version of the swap. Use the OpenAI starter and repoint only the base URL and key; do not touch any `ChatClient` code:

```yaml
# application.yml — through TARS; no provider key in your app
spring:
  ai:
    openai:
      base-url: https://router.tetrate.ai     # the gateway host (NOT the full /v1 path)
      api-key: ${ROUTER_KEY}                   # a gateway key, not a provider key
      chat:
        options:
          model: gpt-4o-mini                    # routing key into the TARS catalog
```

Your existing call site stays exactly as it was — the base-URL change is the entire diff:

```java
// unchanged application code — it has no idea a governed hop is now in the path
String answer = chatClient.prompt()
    .user("In one sentence: what is an AI gateway?")
    .call()
    .content();
```

To prove portability in-app, override the model per call (or via config) to a Claude catalog model and run again — same code, different vendor:

```java
String viaClaude = chatClient.prompt()
    .options(OpenAiChatOptions.builder().model("claude-4-sonnet-20250514").build())
    .user("In one sentence: what is an AI gateway?")
    .call()
    .content();
```

**What success looks like:** identical client code — both the `curl` and the Spring AI call site — returns valid completions from **two different model vendors** through the **one** TARS endpoint, just by changing the `model` field. Your application config contains a **gateway** key and **zero provider keys**. You've made the base-URL change that the whole course is built on.

</div>

## Verify it

Confirm the three properties that make this real:

- **It's governed, not direct.** The response carries a `usage` token block and the call is attributed in your TARS dashboard. A direct OpenAI call wouldn't be metered by Tetrate.
- **No provider key in your app.** Grep your config — only `$ROUTER_KEY` should be present; `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are gone.
- **One contract, two vendors.** The same parsing code handled both responses.

```bash
# the app should hold a gateway key and NO provider keys
grep -rEn "OPENAI_API_KEY|ANTHROPIC_API_KEY|api\.openai\.com|api\.anthropic\.com" . \
  --include=*.yml --include=*.yaml --include=*.properties --include=*.java \
  && echo "!! provider keys/hosts still present — move them to the gateway" \
  || echo "OK: no provider keys or direct provider hosts in app config"
```

!!! failure "Common failure modes"
    - **401 / 403 from the gateway.** The `Authorization` header is missing, malformed, or using a provider key instead of `$ROUTER_KEY`. It must be `Bearer <your TARS key>`.
    - **404 on the path.** Usually a base-URL shape problem: full `/v1/chat/completions` in `curl` but only the host root in Spring AI's `base-url`. A doubled `/v1/v1/` is the giveaway.
    - **400/404 unknown model.** The `model` string doesn't match a TARS catalog ID. Catalog names are exact and versioned — copy them from the catalog, don't guess.
    - **Costs show up but no governance does.** That's expected here — metering is on, but budgets/limits aren't configured yet. You add token limits in 3.1, not now.
    - **Leaving the provider key "as a fallback."** If `OPENAI_API_KEY` is still in config, you haven't actually removed the liability — delete it.

!!! stretch "Stretch goal"
    Take a small Spring AI app you already have (or the OpenAI Java SDK quickstart) and point it at TARS by changing only the base URL and key. Then make it call **two providers from one config**: bind a `model` value per environment profile so `dev` uses a cheap model and `staging` uses a premium one — without touching a line of application code. You've just proven that "which vendor" is now an operations decision, exactly as "which TargetServer" is in Apigee.

## Recap & next

You made your first governed LLM call by changing one line — the base URL — and routed identical OpenAI-shaped requests to two different model vendors through TARS, with no provider key anywhere in your app. The `model` field is now a routing key into a catalog, and the gateway owns providers, credentials, and metering. That's the entire adoption story from the app's side, proven.

**Next — 1.5:** descend a rung. You'll self-host the gateway on Kubernetes and read the three resources that make it work — `AIServiceBackend` (the provider), `BackendSecurityPolicy` (the credential), and `AIGatewayRoute` (the route) — so you understand exactly what TARS is doing for you behind that one base URL.
