# 4.1 — Authenticating callers at the AI edge

!!! bottomline "Bottom line"
    Before a token limit, a budget, or a guardrail can mean anything, the gateway has to know **who is calling**. In this session you attach an Envoy Gateway `SecurityPolicy` to the AI route so every request must present a valid **API key, JWT, or OIDC** identity — and you watch a verified claim (a `sub` or `team`) flow into the per-user token budget you built in Part 3. Auth is the first policy in the chain because it's what makes every later policy enforceable instead of advisory.

## Why this exists

Everything in Part 3 — token rate limits (3.1), budgets (3.2), tiers (3.3) — keys on *who* the caller is. But so far that "who" has been a bare `x-user-id` header the client sets itself. That's fine for wiring up the mechanics; it's worthless as governance, because anyone can type `x-user-id: alice` and spend Alice's budget, or `x-user-id: someone-else` to dodge their own.

Caller authentication closes that hole. The gateway demands a **cryptographically verifiable** identity at the edge — a signed JWT, a registered API key, an OIDC login — and rejects anything that can't prove it. Only after that check passes does the request reach the token meter, and the identity the limits key on is now the *verified* one, not a self-asserted header. This is the difference between a budget that's enforced and a budget that's a suggestion.

It runs **first** for a reason: there's no point metering tokens, applying guardrails, or paying a provider for a caller you can't name. Reject the unauthenticated request before any of that work — or cost — happens.

## The concept

A `SecurityPolicy` attaches to the route via `spec.targetRefs` and gates the request *before* the AI policies run. The verified identity is then available downstream as the key the budget counts against:

```widget
{
  "type": "sequence",
  "title": "Caller auth at the AI edge, before any limit runs",
  "actors": [
    {"id": "app", "label": "Your app / agent"},
    {"id": "sec", "label": "SecurityPolicy (edge auth)"},
    {"id": "lim", "label": "Token limit / budget"},
    {"id": "prov", "label": "LLM provider"}
  ],
  "steps": [
    {"from": "app", "to": "sec", "label": "POST /v1/chat/completions + Bearer JWT", "note": "The caller presents a signed JWT (or API key / OIDC token). No identity, no entry."},
    {"from": "sec", "to": "sec", "label": "verify signature + claims", "note": "The policy validates the token against the provider's remoteJWKS and checks the required scope/claim. Fail → 401/403; the request stops here."},
    {"from": "sec", "to": "lim", "label": "pass: identity = sub/team", "note": "The verified claim becomes the key. The limit now counts against a real identity, not a spoofable header."},
    {"from": "lim", "to": "prov", "label": "within budget → upstream call", "note": "Only an authenticated, in-budget request ever reaches the provider — or costs a token."},
    {"from": "prov", "to": "app", "kind": "return", "label": "completion (metered to that identity)", "note": "Usage is attributed to the verified caller, so chargeback and tiers are trustworthy."}
  ]
}
```

The `SecurityPolicy` is the same Envoy Gateway resource (`gateway.envoyproxy.io/v1alpha1`) you'd use to protect any HTTPRoute. `spec.jwt` defines providers, each with a `remoteJWKS` URL the gateway fetches signing keys from; `spec.apiKeyAuth` checks keys against a secret; `spec.oidc` runs a full login flow. You attach it to the AI route with `spec.targetRefs`, exactly as you attached the `BackendTrafficPolicy` for token limits in 3.1.

!!! pitfall "Watch out"
    Until a `SecurityPolicy` is in place, your per-user budgets are keyed on a **spoofable header**. `x-user-id: alice` is a claim the client made about itself, not a fact the gateway verified. Auth must establish identity *first* — otherwise every limit, tier, and chargeback report downstream is built on a value any caller can forge.

!!! apigee "From Apigee"
    This is the policy you've put at the top of every proxy flow, unchanged in spirit. **VerifyAPIKey** becomes `spec.apiKeyAuth`; **OAuthV2 / VerifyJWT** (validating a token against a JWKS) becomes `spec.jwt` with a `remoteJWKS` provider; an OIDC login flow becomes `spec.oidc`. The job is identical: gate the caller at the edge, extract a verified identifier, and let downstream policies (Quota there, token limits here) key on it. The only shift is the resource — a `SecurityPolicy` attached by `targetRefs` instead of a policy step in a proxy flow.

    | Apigee | AI edge |
    | --- | --- |
    | VerifyAPIKey | `SecurityPolicy.spec.apiKeyAuth` |
    | OAuthV2 / VerifyJWT (JWKS) | `SecurityPolicy.spec.jwt` (`remoteJWKS`) |
    | OAuth / OIDC login flow | `SecurityPolicy.spec.oidc` |
    | `client_id` keying a Quota | verified claim (`sub`/`team`) keying a token limit |

!!! java "From Java microservices"
    It's the **Spring Security filter chain**, relocated. The `OncePerRequestFilter` that validates the bearer token, the `JwtDecoder` wired to a JWKS endpoint, the `@PreAuthorize("hasAuthority('SCOPE_...')")` on your controller — that whole front-door check now sits in front of *every* LLM and tool call as a `SecurityPolicy`, instead of being recompiled into each service. The authenticated principal you'd read from the `SecurityContext` is the same identity the gateway hands to the token budget.

!!! breaks "Where the analogy breaks"
    In Spring, authentication and authorization usually live in the same place, and you express fine-grained rules (`@PreAuthorize`, method security) right next to your business logic. At the AI edge they split: the `SecurityPolicy` proves *identity* and can require a coarse scope or claim, but the interesting authorization — which models this caller may use, what budget they get, which tools they can invoke — lives in *separate* policies (model entitlement in Part 2, budgets in 3.2, tool auth in 5.4) keyed on the identity this one establishes. Don't try to encode entitlements in the auth policy; its only job is to turn an untrusted request into a named one.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — require a scoped JWT, then watch identity reach the budget

**Prereqs:** the self-hosted gateway and `AIGatewayRoute` from 1.5 (export `$NAMESPACE` and `$GATEWAY_HOST`), the per-user `BackendTrafficPolicy` from 3.1, `kubectl`, and an OIDC issuer that publishes a JWKS (any IdP — the URLs below are placeholders).

**1. Attach a `SecurityPolicy` requiring a valid JWT** with a specific claim. It targets the same route your AI policies are on, so it runs first:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: ai-edge-auth
  namespace: ${NAMESPACE}
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: ai-gateway-route          # the route from session 1.5
  jwt:
    providers:
      - name: corp-idp
        issuer: https://idp.example.com/
        remoteJWKS:
          uri: https://idp.example.com/.well-known/jwks.json
        # expose verified claims as headers so downstream policies can key on them
        claimToHeaders:
          - claim: sub
            header: x-jwt-sub
          - claim: team
            header: x-jwt-team
```

**2. Apply it and confirm it's accepted:**

```bash
kubectl apply -f ai-edge-auth.yaml
kubectl get securitypolicy ai-edge-auth -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```

**3. Prove the gate works. First, call with no token — it must be rejected:**

```bash
curl -s -o /dev/null -w "no token -> HTTP %{http_code}\n" \
  "https://$GATEWAY_HOST/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
# expect: 401
```

**4. Now call with a valid, correctly-scoped JWT** (mint one from your IdP into `$GATEWAY_KEY` for this lab). It should pass — and the gateway forwards the verified `sub`/`team` as headers your budget keys on:

```bash
curl -s -o /dev/null -w "valid token -> HTTP %{http_code}\n" \
  "https://$GATEWAY_HOST/v1/chat/completions" \
  -H "authorization: Bearer $GATEWAY_KEY" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
# expect: 200
```

**5. Point the token budget at the verified claim, not a client header.** Update the `BackendTrafficPolicy` from 3.1 so its `clientSelectors` key on `x-jwt-sub` (set by the gateway from the JWT) instead of the spoofable `x-user-id`:

```yaml
        clientSelectors:
          - headers:
              - name: x-jwt-sub            # verified by SecurityPolicy — not client-set
                type: Distinct
              - name: x-ai-eg-model
                type: Distinct
```

!!! pitfall "Watch out"
    If your budget still keys on a header the **client** sends (like `x-user-id`) rather than one the **gateway derived from the verified token** (`x-jwt-sub`), you've authenticated the caller and then thrown the identity away. The two must connect: the claim the `SecurityPolicy` verified is the exact header the limit counts. Otherwise a caller could present a valid token but a forged `x-user-id` and spend someone else's budget.

**What success looks like:** the unauthenticated call returns **401** (and an under-scoped token would return **403**), the valid call returns **200**, and spend from the valid call is attributed to the **verified `sub`** in the token budget — so two callers can no longer spend each other's allowance by editing a header.
</div>

## Verify it

!!! failure "Common failure modes"
    - **No auth at all, budgets on a raw header.** The per-user limit looks real but keys on `x-user-id`, which any client sets — so identity, budgets, and chargeback are all forgeable. The `SecurityPolicy` is what makes them enforceable.
    - **Auth verified but identity discarded.** The JWT is validated, then the budget still keys on a client-set header instead of the verified claim — the proof is thrown away before it's used. Wire `claimToHeaders` into the limit's `clientSelectors`.
    - **JWKS unreachable or stale.** If the gateway can't fetch the `remoteJWKS`, every call fails closed (or, mis-cached, fails open). Confirm the issuer and JWKS URL are reachable from the gateway and that key rotation is honored.
    - **Confusing authentication with authorization.** A valid token proves *who*, not *what they may do*. Model access, budget tier, and tool scope are separate policies keyed on this identity — don't expect `SecurityPolicy` to enforce them.
    - **Issuer or audience not pinned.** Accepting any signed token, or any issuer, lets tokens minted for another system in. Pin `issuer` (and audience where supported) to your IdP.

!!! stretch "Stretch goal"
    Add a second JWT provider for a partner IdP, and require a specific claim value (e.g. `tier: premium`) to reach the premium-model route from 3.3. Then confirm that a token *without* that claim is rejected at the edge — your first taste of identity-driven entitlement, where the verified claim doesn't just name the caller but decides what they're allowed to reach.

## Recap & next

You can now place a `SecurityPolicy` on the AI route so callers must present a verified API key, JWT, or OIDC identity before any model runs, reject unauthenticated (401) or under-scoped (403) calls at the edge, and flow the verified claim into the per-user token budget — turning Part 3's mechanics into real, unforgeable governance. Identity is the foundation every other edge policy stands on.

**Next — 4.2:** with the caller named, defend the *prompt*. You'll add **AI guardrails** that inspect the request before the model runs and block **prompt-injection and jailbreak** attempts — the input validation `@Valid` could never express.
