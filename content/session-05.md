# 1.5 — Self-host it: Envoy AI Gateway's three core resources

!!! bottomline "Bottom line"
    A self-hosted AI gateway is **three declarative resources** layered on Envoy Gateway and the Kubernetes Gateway API: an **AIServiceBackend** (the provider and its API schema), a **BackendSecurityPolicy** (the upstream credential), and an **AIGatewayRoute** (the client-facing route). By the end of this session you can install the gateway with Helm, apply those three CRDs, and route a hello-world chat completion through your own cluster — the same OpenAI-shaped call you made against TARS in 1.4, now on infrastructure you operate.

!!! eli5 "In plain words"
    Instead of renting a door helper, you can build your very own out of three simple LEGO pieces. One piece is a name tag that says which robot to talk to, one is a tiny locked box holding the robot's secret password, and one is a doorway sign that tells each question which way to go. Snap those three together and your homemade helper is ready for work. Those three pieces are the **gateway's core resources**.

## Why this exists

In 1.4 you pointed a base URL at a managed endpoint and it just worked. That's the right first step, but plenty of teams need the gateway *inside their own trust boundary* — same VPC as the apps, their own audit log, their own provider keys never leaving the cluster. Self-hosting Envoy AI Gateway gives you exactly that: the identical OpenAI-compatible contract, running on Envoy Gateway, configured entirely as Kubernetes resources.

The reason it's worth a whole session is that self-hosting forces you to see the **anatomy** the managed product hides. A governed model call doesn't come from one magic object — it's assembled from three resources, each owning one concern: *which provider and protocol* (AIServiceBackend), *what credential to present upstream* (BackendSecurityPolicy), and *which client requests map to which backend* (AIGatewayRoute). Get these three straight and every later session — credentials, routing, fallback, token limits — is just adding policy onto a skeleton you already understand.

This is also the moment the mental model flips from *code* to *config*. There is no application to write. You declare desired state in YAML, `kubectl apply`, and the Envoy AI Gateway controller programs the data plane. The "deployment" is a set of CRDs in a Git repo.

!!! apigee "From Apigee"
    This is the **bundle anatomy of the AI world**, and it maps almost one-to-one onto the proxy bundle you've shipped a hundred times:

    | Apigee object | Envoy AI Gateway resource | Role |
    | --- | --- | --- |
    | ProxyEndpoint (the basepath clients hit) | **AIGatewayRoute** | client-facing route + match rules |
    | TargetEndpoint / TargetServer | **AIServiceBackend** | the upstream provider + its API schema |
    | Target auth (KVM/credential the proxy injects) | **BackendSecurityPolicy** | the upstream credential |
    | Environment / Gateway listener | **Gateway** (Gateway API) | where the proxy is exposed |

    The difference is purely *where it lives*: instead of an XML bundle pushed to an Apigee org, it's CRDs applied to a cluster. The decomposition — route, target, credential — is the one you already think in.

!!! java "From Java microservices"
    You're used to wiring this in code: a `@Configuration` class with an `@Bean` for the `RestClient`, the base URL from `application.yml`, the API key from a `@Value`, and an `interceptor` bean for auth. Self-hosting moves all of that out of the JVM and into **declarative CRDs**. The AIServiceBackend is the `RestClient` bean's target; the BackendSecurityPolicy is the auth interceptor; the AIGatewayRoute is the `@RequestMapping` that decides which request reaches which client. It's the same dependency graph you'd assemble with Spring's `@Bean` wiring — but configured, not coded, and reconciled by a controller instead of an application context.

!!! breaks "Where the analogy breaks"
    An Apigee bundle is a single versioned artifact you deploy atomically; these three CRDs are **independent objects reconciled continuously and asynchronously**. You can apply the AIGatewayRoute before its AIServiceBackend exists, or update a BackendSecurityPolicy with the route untouched — and the gateway converges toward whatever the current set of objects describes, on its own schedule. There is no single "deploy" event and no bundle revision. You reason in *desired state and status conditions* (is this object `Accepted`?), not in a deploy log — which is liberating once it clicks and confusing if you expect Apigee's atomic push.

## The concept

These three resources are the machinery that makes the gateway box from session 1.1 real. The same request path you already know — app to one endpoint, policies, route to a provider — but now each segment is backed by a named CRD you can `kubectl get`:

<figure class="svg-figure">
<img src="assets/svg/ai-request-path.svg" alt="Your app calls one OpenAI-compatible endpoint; the gateway authenticates, meters, and routes to a provider. The route is an AIGatewayRoute, the provider an AIServiceBackend, the upstream credential a BackendSecurityPolicy.">
<figcaption>The three resources that make the gateway box real. The AIGatewayRoute is the client-facing contract; behind it, an AIServiceBackend names the provider and its API schema, and a BackendSecurityPolicy supplies the upstream credential. Later sessions add policy onto this skeleton.</figcaption>
</figure>

Read the resources from the outside in. The **AIGatewayRoute** attaches to a `Gateway` (the listener clients hit) via `parentRefs` and carries `rules` whose `matches` key on the `x-ai-eg-model` header — the model name the gateway extracts from the request body — to choose a `backendRefs` target. The **AIServiceBackend** is that target: it declares `spec.schema.name` (e.g. `OpenAI`) so the gateway knows the provider's API dialect, and a `backendRef` to an Envoy Gateway `Backend` (the actual host:port). The **BackendSecurityPolicy** attaches to the AIServiceBackend via `targetRefs` and points at a Kubernetes `Secret` holding the provider key, which the gateway injects on the upstream call.

!!! pitfall "Watch out"
    The group matters and it's easy to mix up. The AI CRDs live under `aigateway.envoyproxy.io/v1alpha1`. The `Backend` an AIServiceBackend points at is an **Envoy Gateway** resource under `gateway.envoyproxy.io`, and the `Gateway` an AIGatewayRoute attaches to is a **Gateway API** resource under `gateway.networking.k8s.io`. Three different API groups in three references. Copy a `group:` value from the wrong example and the object is rejected or silently never `Accepted`. Field names track the release — verify against the API reference for your version.

The payoff of this split is that each concern changes independently. Rotating a key touches only the Secret. Adding a provider adds an AIServiceBackend and a route rule. Changing who can reach the gateway is the `Gateway` and (later) auth policy. Nothing is entangled in app code.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — stand up the gateway and route a hello-world completion

**Prereqs:** a Kubernetes cluster (a local `kind` cluster is fine), `kubectl`, `helm`, and an OpenAI API key. Export `$NAMESPACE` (use `default` for this lab) and you'll set `$GATEWAY_HOST` after the gateway is up.

**1. Install Envoy Gateway, then Envoy AI Gateway.** The AI gateway runs *on* Envoy Gateway, so install that first, then the AI Gateway CRDs and controller. (Pin `--version` to a real release tag for your install; `v0.0.0-latest` tracks main and drifts.)

```bash
# Envoy Gateway (the data plane the AI gateway programs)
helm upgrade -i eg oci://docker.io/envoyproxy/gateway-helm \
  --version v0.0.0-latest --namespace envoy-gateway-system --create-namespace
kubectl wait --timeout=2m -n envoy-gateway-system \
  deployment/envoy-gateway --for=condition=Available

# Envoy AI Gateway CRDs + controller
helm upgrade -i aieg-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm \
  --version v0.0.0-latest --namespace envoy-ai-gateway-system --create-namespace
helm upgrade -i aieg oci://docker.io/envoyproxy/ai-gateway-helm \
  --version v0.0.0-latest --namespace envoy-ai-gateway-system --create-namespace
kubectl wait --timeout=2m -n envoy-ai-gateway-system \
  deployment/ai-gateway-controller --for=condition=Available
```

!!! pitfall "Watch out"
    Order and readiness are not optional. If you apply the AI CRDs before the Envoy Gateway control plane is `Available`, the AIGatewayRoute may never reach `Accepted` because there's no controller to program. Always `kubectl wait` on each deployment before moving on — a route stuck in `Pending` is almost always "the layer below isn't ready yet," not a YAML bug.

**2. Create the provider Secret** holding your OpenAI key. The key inside the Secret is named `apiKey`:

```bash
kubectl create secret generic openai-apikey -n "$NAMESPACE" \
  --from-literal=apiKey="$OPENAI_API_KEY"
```

**3. Apply the three resources plus the `Gateway` and `Backend` they reference.** Read it top-down: Gateway (listener), Backend (the provider host), AIServiceBackend (provider + schema), BackendSecurityPolicy (credential), AIGatewayRoute (route):

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: ai-gateway
  namespace: ${NAMESPACE}
spec:
  gatewayClassName: envoy-ai-gateway-basic   # the GatewayClass the AI gateway ships
  listeners:
    - name: http
      protocol: HTTP
      port: 80
---
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: Backend
metadata:
  name: openai
  namespace: ${NAMESPACE}
spec:
  endpoints:
    - fqdn:
        hostname: api.openai.com
        port: 443
---
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: AIServiceBackend
metadata:
  name: openai
  namespace: ${NAMESPACE}
spec:
  schema:
    name: OpenAI                 # the provider's API dialect
  backendRef:
    name: openai                 # -> the Backend above
    kind: Backend
    group: gateway.envoyproxy.io
---
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: BackendSecurityPolicy
metadata:
  name: openai-cred
  namespace: ${NAMESPACE}
spec:
  type: APIKey
  apiKey:
    secretRef:
      name: openai-apikey        # the Secret from step 2 (key: apiKey)
  targetRefs:
    - group: aigateway.envoyproxy.io
      kind: AIServiceBackend
      name: openai               # attach the credential to the backend
---
apiVersion: aigateway.envoyproxy.io/v1alpha1
kind: AIGatewayRoute
metadata:
  name: ai-gateway-route
  namespace: ${NAMESPACE}
spec:
  parentRefs:
    - name: ai-gateway           # -> the Gateway above
      kind: Gateway
      group: gateway.networking.k8s.io
  rules:
    - matches:
        - headers:
            - type: Exact
              name: x-ai-eg-model   # the gateway fills this from the body's "model"
              value: gpt-4o-mini
      backendRefs:
        - name: openai              # -> the AIServiceBackend
```

**4. Confirm each object is accepted, then resolve the address.** Status conditions are how you "read a deploy log" here:

```bash
kubectl get aigatewayroute ai-gateway-route -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
# port-forward the generated Envoy service for local testing
export GATEWAY_HOST=localhost:8080
kubectl port-forward -n envoy-gateway-system \
  svc/$(kubectl get svc -n envoy-gateway-system \
    -l gateway.envoyproxy.io/owning-gateway-name=ai-gateway \
    -o jsonpath='{.items[0].metadata.name}') 8080:80
```

**5. Route a hello-world completion** through your own gateway — the exact OpenAI-shaped call from 1.4, now hitting your cluster. The `model` in the body is what the gateway turns into the `x-ai-eg-model` match:

```bash
curl -s "http://$GATEWAY_HOST/v1/chat/completions" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o-mini",
       "messages":[{"role":"user","content":"Say hello from my self-hosted gateway."}]}' \
  | jq -r '.choices[0].message.content'
```

**What success looks like:** the AIGatewayRoute reports `Accepted: True`, and the curl returns a real completion routed OpenAI ← AIGatewayRoute ← AIServiceBackend ← BackendSecurityPolicy — through a gateway you installed and own. Your app sent no provider key; the gateway injected it.
</div>

## Verify it

You're done when you can see the three resources doing their jobs:

```bash
kubectl get aigatewayroute,aiservicebackend,backendsecuritypolicy -n "$NAMESPACE"
```

- All three exist and the route is `Accepted`. If the route is `Pending`, the Gateway or the layer below it isn't ready — check `kubectl get gateway` and the controller deployments.
- The completion succeeds with **no provider key in the request** — the credential came from the BackendSecurityPolicy, not the caller. That's the whole point of the split, and it's the subject of session 2.2.
- For pure local dev with no cluster, `aigw run <config>` runs the same gateway as a standalone proxy with no Docker or Kubernetes — handy for iterating on config before you apply it.

!!! failure "Common failure modes"
    - **Wrong API group on a reference.** The `Backend` is `gateway.envoyproxy.io`, the `Gateway` is `gateway.networking.k8s.io`, the AI CRDs are `aigateway.envoyproxy.io`. A mismatched `group:` leaves the object un-`Accepted`. *(Symptom: route stuck `Pending`, no error in the app.)*
    - **Secret key name mismatch.** The BackendSecurityPolicy `apiKey.secretRef` expects the Secret's key to be `apiKey`. Name it `api-key` or `OPENAI_API_KEY` and the upstream call gets no credential. *(Symptom: 401 from the provider.)*
    - **Skipping the readiness waits.** Applying CRDs before Envoy Gateway is `Available` produces a route that never programs. *(Symptom: `Accepted` never flips to `True`.)*
    - **No backend match for the model.** The `value:` in the route's header match must equal the `model` your client sends. A typo means no rule matches and the request 404s at the route.
    - **Pinning to `v0.0.0-latest` in anything but a throwaway.** It tracks `main` and can change under you; pin a release tag.

!!! stretch "Stretch goal"
    Run the same config locally with the `aigw` CLI: `aigw run` against a config file, no cluster involved, and send the identical curl. Then apply the unchanged manifests to a `kind` cluster and confirm both paths serve the same completion — proving the config is portable from laptop to cluster, which is exactly the config-as-code story you'll lean on for promotion in 6.3.

## Recap & next

You can now install Envoy AI Gateway on Envoy Gateway and the Gateway API, and read the three resources that define a self-hosted AI gateway: **AIServiceBackend** (provider + schema), **BackendSecurityPolicy** (upstream credential), and **AIGatewayRoute** (client route). You routed a real completion through a gateway you own, with the provider key living in a Secret instead of your app — and you reason about it in status conditions, not deploy logs.

**Next — 2.1:** lean on the AIServiceBackend's `schema` field. You'll use **one OpenAI-compatible API to reach many providers**, adding a second backend behind the same route so the same client code reaches a different vendor by changing only the `model` name.
