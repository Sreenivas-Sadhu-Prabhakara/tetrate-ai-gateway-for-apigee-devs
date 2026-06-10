# 5.4 — Securing tools: OAuth & fine-grained authorization

!!! bottomline "Bottom line"
    A tool in the catalog isn't a tool the agent may *call*. **`securityPolicy.oauth`** makes the MCPRoute a protected resource per the **MCP Authorization spec** — the agent must present a valid token — and **`authorization.rules`** then decide, per invocation, which identities may call which tools using **JWT scopes/claims plus CEL**. By the end you can require a scope to invoke a write tool, prove a read-only token is refused it via a CEL rule, and confirm a read tool still works.

## Why this exists

Selection in 5.3 controlled what's *visible*; it does not control who may *act*. Two agents — a read-only assistant and a privileged ops agent — may share the same catalog yet must have very different powers over it. Visibility is not authorization.

So the gateway authenticates the caller (OAuth) and authorizes each call (rules). Per the MCP Authorization spec, the MCPRoute advertises `protectedResourceMetadata` so an agent's MCP client can discover the authorization server, obtain a token, and present it. On every `tools/call`, the gateway validates the JWT against the `issuer` and `audiences`, then evaluates `authorization.rules` — matching the token's **scopes and claims** against the **target tool**, with **CEL** for conditions a flat scope list can't express ("`tools:write` *and* the tool name starts with `github__`"). The decision is allow or deny, made at the edge, identically for every agent — not reimplemented inside each tool server.

## The concept

The agent presents a token; the gateway checks OAuth, then the rules, then routes — or denies before the tool ever runs:

```widget
{
  "type": "sequence",
  "title": "An authorized tool call",
  "actors": [
    {"id": "ag", "label": "AI agent"},
    {"id": "gw", "label": "MCP gateway"},
    {"id": "as", "label": "Auth server"},
    {"id": "ts", "label": "Tool server"}
  ],
  "steps": [
    {"from": "ag", "to": "gw", "label": "tools/call (no/invalid token)", "note": "First call carries no token; the gateway's securityPolicy.oauth rejects it and points to the auth server via protectedResourceMetadata."},
    {"from": "ag", "to": "as", "label": "obtain token", "note": "The MCP client discovers the issuer, authenticates, and is granted a JWT whose scopes (e.g. tools:read) reflect what this agent may do."},
    {"from": "ag", "to": "gw", "label": "tools/call github__issue_read + Bearer token", "note": "Now the call carries the JWT. The gateway validates it against issuer + audiences."},
    {"from": "gw", "to": "gw", "label": "evaluate authorization.rules (scopes/claims + CEL)", "note": "Rules combine the token's scopes/claims, the target tool name, and CEL. A read tool with a tools:read token matches an allow rule."},
    {"from": "gw", "to": "ts", "label": "route to tool server", "note": "Allowed: the gateway strips the server prefix and invokes the upstream tool. A denied call stops here with 403 — the tool never runs."},
    {"from": "ts", "to": "ag", "kind": "return", "label": "result", "note": "Authorized, invoked, audited. A tools:read token calling github__issue_create would have been denied by CEL before reaching this step."}
  ]
}
```

!!! apigee "From Apigee"
    This is **OAuthV2 plus operation-scoping, aimed at tools.** `securityPolicy.oauth` is the OAuthV2 policy that verifies the bearer token (`issuer` = the token endpoint you'd configure, `audiences` = the resource); `authorization.rules` is the scope/operation check you'd express with `oauth.scope` conditions and Flow Conditions, except the "operation" is an MCP tool name and the conditions are **CEL** instead of message templates. If you've gated `POST /orders` behind a `orders.write` scope while leaving `GET /orders` on `orders.read`, you've built exactly this — for a tool instead of a verb+path.

!!! java "From Java microservices"
    Picture `@PreAuthorize("hasAuthority('SCOPE_tools:write')")` on each tool method, with a Spring Security resource server validating the JWT. The MCPRoute hoists that to the **edge**: the same scope check, the same JWT validation, but enforced once at the gateway for every tool server — so a new tool server inherits authorization without re-implementing method security, and the policy lives in YAML your platform team owns, not annotations scattered across services.

!!! breaks "Where the analogy breaks"
    A REST endpoint is authorized **once per request**; an MCP session is long-lived and carries **many** `tools/call` messages over one connection. Authorizing at *connect* time (or only at `tools/list`) is not enough — a token validated at the handshake could otherwise call tools its scopes never permitted for the rest of the session. The gateway must evaluate `authorization.rules` on **every invocation**, per JSON-RPC message, not per TCP connection. There's no stateless-request boundary to lean on the way there is in Apigee or a Spring filter chain.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — require a scope to call a write tool, refuse a read-only token

**Prereqs:** the aggregated MCPRoute `tools` from 5.3 (export `$NAMESPACE` and `$GATEWAY_HOST`), `kubectl`, an OAuth authorization server that can issue JWTs with a `scope` claim, and a way to mint two tokens: one with `tools:read`, one with `tools:read tools:write`. For this lab the GitHub backend also exposes a write tool, `issue_create`.

**1. Expose the write tool** (add it to the GitHub backend's selector so it's in the catalog — authorization, not visibility, will gate it):

```yaml
    - name: github-mcp
      toolSelector:
        include:
          - issue_read
          - issue_search
          - issue_create        # now visible; rules below decide who may call it
```

**2. Add `securityPolicy` to the MCPRoute** — OAuth for authentication, `authorization.rules` for fine-grained tool access with CEL:

```yaml
apiVersion: aigateway.envoyproxy.io/v1beta1
kind: MCPRoute
metadata:
  name: tools
  namespace: ${NAMESPACE}
spec:
  parentRefs:
    - name: ai-gateway
  path: /mcp
  securityPolicy:
    oauth:
      issuer: https://auth.example.com/realms/agents
      audiences:
        - mcp-tools
      protectedResourceMetadata: true     # advertise discovery per MCP Authz spec
    authorization:
      rules:
        # read tools: any authenticated token with tools:read
        - action: Allow
          expression: >
            "tools:read" in request.auth.claims.scope.split(" ")
            && (request.mcp.tool.startsWith("github__issue_read")
                || request.mcp.tool.startsWith("github__issue_search")
                || request.mcp.tool.startsWith("context7__get_"))
        # write tools: require tools:write explicitly
        - action: Allow
          expression: >
            "tools:write" in request.auth.claims.scope.split(" ")
            && request.mcp.tool == "github__issue_create"
        # anything not matched above is denied
        - action: Deny
          expression: "true"
  backendRefs:
    - name: github-mcp
      toolSelector:
        include: [issue_read, issue_search, issue_create]
    - name: context7-mcp
      toolSelector:
        includeRegex: ["get_.*"]
```

**3. Apply and confirm acceptance:**

```bash
kubectl apply -f mcproute.yaml
kubectl get mcproute tools -n "$NAMESPACE" \
  -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```

**4. Call a read tool with the read-only token — expect success:**

```bash
curl -s "https://$GATEWAY_HOST/mcp" \
  -H "authorization: Bearer $READ_TOKEN" -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"github__issue_read","arguments":{"number":42}}}' | jq '.result'
```

**5. Call the write tool with that same read-only token — expect a deny:**

```bash
curl -s -o /dev/null -w "write w/ read token -> HTTP %{http_code}\n" \
  "https://$GATEWAY_HOST/mcp" \
  -H "authorization: Bearer $READ_TOKEN" -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"github__issue_create","arguments":{"title":"hi"}}}'
# → HTTP 403  (no tools:write scope; the CEL write-rule didn't match, Deny did)
```

!!! pitfall "Watch out"
    Authorizing only at **connect** time or only on `tools/list` is the classic hole: a token passes the handshake, then calls a write tool its scopes never allowed because nothing re-checks per call. Your rules must key on `request.mcp.tool` so they evaluate on **every** `tools/call` — confirm step 5 denies even though the *same* token succeeded at step 4 on the same session.

**What success looks like:** the read tool returns a result under the `tools:read` token; the identical session's `tools/call` for the write tool returns **403** because the CEL write-rule requires `tools:write` and the catch-all `Deny` fires. Swap in the `tools:write` token and the write call succeeds — authorization is decided per invocation, by scope and CEL, at the edge.
</div>

## Verify it

!!! failure "Common failure modes"
    - **Connect-time-only authorization.** Validating the token at the handshake but not per `tools/call` lets a token invoke tools beyond its scopes for the life of the session. Enforce on every invocation.
    - **Wrong `audiences` or `issuer`.** A token valid elsewhere is rejected (or, worse, a too-broad audience accepts tokens minted for another resource). Match `audiences` to *this* MCP resource exactly.
    - **Scope checked, tool not.** A rule that allows any `tools:write` token to call *any* tool grants the write scope blanket power. Combine the scope with `request.mcp.tool` in CEL so write means a *specific* tool.
    - **No default-deny.** Without a trailing `Deny` rule, an invocation matching no `Allow` may fall through permissively. End the rule list with an explicit deny.
    - **`protectedResourceMetadata` off.** Compliant MCP clients can't discover the auth server and won't know how to obtain a token — calls fail with no actionable challenge.

!!! stretch "Stretch goal"
    Add a third rule that allows `github__issue_create` **only** when a custom claim matches — e.g. `request.auth.claims.team == "platform"` — so even a `tools:write` token from another team is denied. Then mint two write tokens differing only by `team` and confirm one is allowed and one is refused. You've now authorized on **identity claims**, not just scopes — the lever 5.5 uses to give each *agent* its own entitlements.

## Recap & next

You can now protect MCP tools with **OAuth** (`securityPolicy.oauth` — `issuer`, `audiences`, `protectedResourceMetadata`) per the MCP Authorization spec, and gate each `tools/call` with **`authorization.rules`** combining JWT scopes, claims, the target tool, and **CEL**. Crucially, you enforce this **per invocation**, not just at connect or list — visibility and authorization are now separate, edge-enforced concerns.

**Next — 5.5:** treat the agent itself as a first-class identity. You'll carry that identity across model **and** tool calls and put the autonomous loop on a leash — per-agent budgets, guardrails, and a step-cap so a runaway agent can't loop or overspend.
