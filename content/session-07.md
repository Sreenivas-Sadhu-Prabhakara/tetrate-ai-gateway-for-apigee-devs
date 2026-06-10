# 2.2 — Provider credentials & secrets: BackendSecurityPolicy

!!! bottomline "Bottom line"
    A **BackendSecurityPolicy** is how the gateway — not your app — holds the provider credential. It references a Kubernetes `Secret` and attaches to an AIServiceBackend via `targetRefs`; on every upstream call the gateway injects that credential (a bearer key, AWS SigV4, etc.) so the request to the provider is authenticated without the client ever seeing it. By the end you can delete `OPENAI_API_KEY` from an app's config, prove the app sends no provider key, and rotate the key by editing only the Secret.

!!! eli5 "In plain words"
    Each robot has its own secret password to prove you're allowed to talk to it. Instead of you carrying all those passwords around in your pocket where you might drop one, the friendly helper at the door keeps every robot's password in its own little locked drawer. When you ask a question, the helper opens the right drawer and uses the password for you — so you never touch a password at all. That locked drawer of passwords the helper keeps is the **BackendSecurityPolicy**.

## Why this exists

In session 1.1 you inventoried provider keys scattered across services. Each one is a liability: it can leak in a log, a heap dump, a misconfigured env var, or a stale `application.yml` in a Git history. Worse, *rotation* means touching and redeploying every service that holds the key — so in practice keys don't get rotated, which is exactly the audit finding you don't want.

The BackendSecurityPolicy moves that secret to one place: the gateway. The client authenticates to the *gateway* (with a gateway key or JWT — that's session 4.1); the gateway authenticates to the *provider* using the credential in the policy. Two separate trust legs, and the provider key never crosses into application space. This is the single most concrete payoff of self-hosting: a clean answer to "where do our model-provider keys live?" — in one Secret, governed by one policy, injected on the upstream hop.

Because the credential is decoupled from app code, **rotation becomes a Secret edit**. Update the `Secret`, and the next upstream call uses the new key — no service redeploys, no coordinated rollout. The thing that was painful and therefore skipped becomes a one-line change with no blast radius on the apps.

!!! apigee "From Apigee"
    This is **the gateway holding the backend credential** — exactly Apigee target authentication. You've stored a backend's API key or service-account credential in a KVM (or an encrypted KVM) and had the proxy inject it on the TargetEndpoint via an AssignMessage or a service-callout, so the *client* never holds the backend secret. The BackendSecurityPolicy is that KVM-plus-injection, made first-class: the `Secret` is the (encrypted) KVM entry, `targetRefs` is "attach this credential to that target," and the injection happens automatically per the policy `type`. Rotating the KVM value without redeploying the proxy is the same muscle as editing the Secret here.

!!! java "From Java microservices"
    Picture deleting `spring.ai.openai.api-key: ${OPENAI_API_KEY}` from `application.yml` and removing the env var from the deployment — and the app still works. That's the move. Today the key reaches your app from a secret manager (Vault, AWS Secrets Manager) or is injected by a sidecar, and your code reads it to build the client. The gateway makes the app's credential awareness **zero**: the secret manager / sidecar role moves to the BackendSecurityPolicy, and your app's config no longer references a provider key at all. Rotation that used to mean a Vault update *plus* a rolling restart now means just the Secret update — the app never re-reads anything.

!!! breaks "Where the analogy breaks"
    Apigee target auth was often request-scoped logic you could shape per call in a flow; a BackendSecurityPolicy is a **declarative attachment reconciled by a controller**, not code that runs in your proxy pipeline. And the injection is **provider-shaped, not a generic header-set**: `type: APIKey` adds a bearer token, but `type: AWSCredentials` performs full SigV4 request signing (a keyed hash over method, headers, and body) and `GCPCredentials` mints short-lived tokens. You're not templating a header — you're selecting an auth *scheme* the gateway implements. Reasoning about it like a simple "add this Authorization header" AssignMessage will mislead you the moment a provider needs signing or token exchange.

## The concept

Two distinct authentication legs, with the provider key confined to the upstream one. The client never holds it; the gateway injects it from the policy's Secret:

```widget
{
  "type": "sequence",
  "title": "Credential injection on the upstream call",
  "actors": [
    {"id": "app", "label": "Your app"},
    {"id": "gw", "label": "AI gateway"},
    {"id": "sec", "label": "Secret (via BackendSecurityPolicy)"},
    {"id": "prov", "label": "Provider"}
  ],
  "steps": [
    {"from": "app", "to": "gw", "label": "POST /v1/chat/completions", "note": "Carries a gateway credential (or none yet) — but NO provider key. The body names the model."},
    {"from": "gw", "to": "gw", "label": "resolve backend + policy", "note": "The route picks the AIServiceBackend; its attached BackendSecurityPolicy says which credential to present."},
    {"from": "gw", "to": "sec", "label": "read credential", "note": "The gateway reads the Secret referenced by the policy (key: apiKey for APIKey, or AWS creds for AWSCredentials)."},
    {"from": "gw", "to": "prov", "label": "upstream call + injected auth", "note": "APIKey -> Authorization: Bearer …; AWSCredentials -> SigV4-signed request. The injection matches the provider's scheme."},
    {"from": "prov", "to": "gw", "kind": "return", "label": "completion", "note": "Authenticated by the gateway-held credential — the app was never involved."},
    {"from": "gw", "to": "app", "kind": "return", "label": "OpenAI-shaped response", "note": "The app got its answer and never saw a provider key. Rotation = edit the Secret only."}
  ]
}
```

The policy itself is small: a `type` that selects the auth scheme, a credential reference (e.g. `apiKey.secretRef` for `APIKey`), and `targetRefs` binding it to one or more AIServiceBackends. That binding is the whole trick — the credential is associated with a *backend*, so any route that lands on that backend gets the credential automatically, and a backend with no attached policy simply has no upstream auth.

!!! pitfall "Watch out"
    A BackendSecurityPolicy can be `Accepted` while the upstream auth is still broken, because acceptance only validates the *object*, not that the Secret exists, has the right key name, or holds a valid credential. The `APIKey` type reads the Secret's `apiKey` key specifically — a Secret with the value under `api-key` or `token` injects nothing. So a green status and a 401 from the provider coexist happily. Always test an actual upstream call, and check the *provider's* response code, not just the policy condition.

A useful consequence: because the credential lives in the Secret and the policy points at it by name, **key rotation never touches the app and rarely touches the policy**. You update the Secret's value (or roll to a new Secret and repoint `secretRef`), and the next call signs/bears the new key. No redeploy of the gateway config, no redeploy of any service.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — take the provider key out of the app

**Prereqs:** the self-hosted gateway and OpenAI route from 1.5/2.1 (export `$NAMESPACE` and `$GATEWAY_HOST`), `kubectl`, and an OpenAI key. The goal is to *prove* the app holds no provider key while calls still succeed, then rotate without an app change.

**1. Put the provider key in a Secret** under the key name the `APIKey` policy expects — `apiKey`:

```bash
kubectl create secret generic openai-apikey -n "$NAMESPACE" \
  --from-literal=apiKey="$OPENAI_API_KEY"
```

**2. Create the BackendSecurityPolicy and attach it to the OpenAI backend** via `targetRefs`. This is the only object that references the Secret:

```yaml
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: BackendSecurityPolicy
metadata:
  name: openai-cred
  namespace: ${NAMESPACE}
spec:
  type: APIKey                     # inject Authorization: Bearer <key>
  apiKey:
    secretRef:
      name: openai-apikey          # the Secret from step 1; key must be "apiKey"
  targetRefs:
    - group: aigateway.envoyproxy.io
      kind: AIServiceBackend
      name: openai                 # bind the credential to the OpenAI backend
```

```bash
kubectl apply -f openai-cred.yaml
kubectl get backendsecuritypolicy openai-cred -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```

!!! pitfall "Watch out"
    The `targetRefs` `kind` here is **AIServiceBackend**, not `Gateway` or `HTTPRoute` — the credential attaches to the *backend* (which provider to authenticate to), not to the route (who's calling). Point `targetRefs` at the route and the policy is `Accepted` but never injects anything, because no backend is bound to it. *(Symptom: 401 from the provider despite a green policy.)*

**3. Prove the app holds no provider key.** Send the completion with **only** a gateway credential (or, in this lab, no auth header at all) and explicitly *no* `Authorization` bearing the provider key. The gateway adds the upstream auth itself:

```bash
# Note: no OpenAI key anywhere in this request. The gateway injects it.
curl -s "http://$GATEWAY_HOST/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini",
       "messages":[{"role":"user","content":"Confirm: did the gateway supply my key?"}]}' \
  | jq -r '.choices[0].message.content'
```

**4. Rotate the key — Secret only, no app or route change.** This is the rotation drill you could never do cheaply before:

```bash
kubectl create secret generic openai-apikey -n "$NAMESPACE" \
  --from-literal=apiKey="$NEW_OPENAI_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
# re-run the curl from step 3 — it now authenticates with the new key.
# No service redeployed; no AIGatewayRoute or BackendSecurityPolicy edited.
```

**5. (App side) Delete the provider key from config.** In the Spring app pointed at the gateway, remove the provider key entirely — the app now depends only on the gateway:

```yaml
# application.yml — BEFORE: app holds the provider key
# spring.ai.openai.api-key: ${OPENAI_API_KEY}   <- delete this line
spring:
  ai:
    openai:
      base-url: http://your-gateway-host        # the gateway; no provider key here
```

**What success looks like:** the completion in step 3 succeeds with **no provider key in the request** — the gateway injected it from the Secret. Step 4 swaps the key with zero redeploys and the call keeps working. And the app's config in step 5 no longer contains any `OPENAI_API_KEY`. The provider credential now lives in exactly one place you control.
</div>

## Verify it

Confirm the credential is the gateway's, not the app's:

```bash
# the only object that should reference a provider Secret:
kubectl get backendsecuritypolicy -n "$NAMESPACE" -o yaml | grep -A2 secretRef
# and prove the app's config is clean:
grep -rEn "OPENAI_API_KEY|openai.*api-key|sk-[A-Za-z0-9]" your-app/ \
  --include=*.yml --include=*.yaml --include=*.properties --include=*.java
```

- The completion succeeds while the request carries no provider key — the BackendSecurityPolicy supplied it on the upstream hop.
- The app grep returns nothing. If it returns a key, you've left a credential — and a rotation liability — in app space.
- A rotation (step 4) takes effect without redeploying any service. If you found yourself restarting pods to pick up the new key, the key was still in the app, not the gateway.

!!! failure "Common failure modes"
    - **Secret key name isn't `apiKey`.** The `APIKey` policy reads that specific key; `api-key`/`token`/`OPENAI_API_KEY` inject nothing. *(Symptom: policy `Accepted`, provider returns 401.)*
    - **`targetRefs` points at the route, not the AIServiceBackend.** The credential never binds to a backend. *(Symptom: green policy, 401 upstream.)*
    - **Wrong `type` for the provider.** `APIKey` for a backend that needs `AWSCredentials`/`GCPCredentials` — a bearer header where signing or token-exchange is required. Match `type` to the provider's scheme.
    - **Leaving the key in the app "as a fallback."** Now you have two places to rotate and one to leak. Delete it; the gateway is the single owner.
    - **Secret in the wrong namespace.** The policy and Secret must be reachable per the reference; a Secret in another namespace without the right cross-namespace grant won't be read.

!!! stretch "Stretch goal"
    Take a real provider key out of a Spring service and into a BackendSecurityPolicy end to end: delete it from `application.yml` and the deployment env, apply the policy, and confirm the service still completes calls through the gateway. Then rotate the key via the Secret and watch traffic keep flowing with **zero** service restarts — the operational win you'll cite when this gets reviewed by security.

## Recap & next

You can now keep provider keys entirely out of app code with a **BackendSecurityPolicy**: a Secret plus a `type` that selects the auth scheme, bound to an AIServiceBackend via `targetRefs`, injected by the gateway on the upstream call. You proved an app sends no provider key yet its calls succeed, and rotated a key by editing only the Secret — no redeploys, no app awareness, one place to govern.

**Next — 2.3:** now that credentials are handled, control *which* models callers may reach. You'll do **model routing and build an approved model catalog** — virtual model names mapped to concrete backends, so clients stop carrying raw provider IDs and you decide, at the edge, which models exist.
