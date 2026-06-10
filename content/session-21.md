# 5.2 — MCP gateways: the MCPRoute resource

!!! bottomline "Bottom line"
    The agentic equivalent of fronting a backend: you put the gateway in front of **Model Context Protocol (MCP) tool servers** with an **MCPRoute**, so an agent reaches its tools through one governed, observable, secured endpoint instead of wiring raw HTTP clients into the agent. By the end you can front a tool server with an MCPRoute and call one of its tools through the gateway.

## Why this exists

In Part 2 you put the gateway in front of *models*. But a modern agent doesn't just complete text — it **calls tools**: search a repo, query docs, hit an internal API. Each tool is a server speaking **MCP**, and without a gateway your agent ends up holding a connection, credentials, and trust for every one of them directly. That's the exact sprawl you removed for model calls in session 1.1, reappearing for tools.

An MCP gateway fixes it the same way: the agent connects to **one** endpoint and sees one tool catalog; the gateway routes each tool call to the right server, authorizes it, and records it. Tools become **governed backends**, not ad-hoc clients scattered through agent code.

## The concept

One agent, one endpoint, many tool servers behind it — the gateway authorizes and multiplexes:

<figure class="svg-figure">
<img src="assets/svg/mcp-topology.svg" alt="An AI agent reaches many MCP tool servers through one MCP gateway, which authorizes with OAuth and multiplexes tools under prefixed names.">
<figcaption>An MCPRoute fronts one or more MCP servers. The agent sees a single governed tool surface; the gateway routes, authorizes (5.4), and observes each invocation. Aggregating many servers is 5.3.</figcaption>
</figure>

An `MCPRoute` has the same shape as the `AIGatewayRoute` you already know — it just points at MCP backends instead of model providers. Step through a single governed tool call:

!!! pitfall "Watch out"
    An MCPRoute is **not** a plain HTTP proxy. The agent must speak MCP to the route's `path` over a *streamed, stateful* session — pointing a bare REST client at it, or load-balancing that session like independent stateless calls, breaks tool state and the `tools/list` handshake.

```widget
{
  "type": "sequence",
  "title": "A tool call through the MCP gateway",
  "actors": [
    {"id": "ag", "label": "AI agent"},
    {"id": "gw", "label": "MCP gateway"},
    {"id": "ts", "label": "Tool server"}
  ],
  "steps": [
    {"from": "ag", "to": "gw", "label": "connect (Streamable HTTP)", "note": "The agent opens one stateful MCP session to the gateway's MCPRoute path — not to each tool server."},
    {"from": "ag", "to": "gw", "label": "tools/list", "note": "The gateway returns the aggregated catalog: tool names prefixed by server, e.g. github__issue_read."},
    {"from": "ag", "to": "gw", "label": "tools/call github__issue_read", "note": "The agent invokes a tool; the gateway checks the securityPolicy before routing."},
    {"from": "gw", "to": "ts", "label": "route to GitHub MCP server", "note": "The prefix tells the gateway which backendRef to route to; it strips the prefix for the upstream server."},
    {"from": "ts", "to": "gw", "kind": "return", "label": "tool result", "note": "The server returns the result over the streamed session."},
    {"from": "gw", "to": "ag", "kind": "return", "label": "result (observed + audited)", "note": "The gateway records the invocation for observability before returning it."}
  ]
}
```

!!! apigee "From Apigee"
    An **MCPRoute is an AIGatewayRoute for tools** — same Gateway API shape: `parentRefs` to the Gateway, a `path`, `backendRefs` to the upstreams, and a `securityPolicy`. It's the facade pattern you'd build in Apigee to aggregate several backends behind one proxy, except the backends are tool servers and the gateway speaks MCP. If you've fronted three microservices with one Apigee proxy and a set of RouteRules, you've done this — for REST.

!!! java "From Java microservices"
    Today an agent framework wires a client per tool: a `RestClient` for the GitHub API, a JDBC `DataSource` for the DB tool, each with its own URL, credential, and error handling injected into the agent. The MCPRoute makes those **discovered backends** behind one connection — closer to a service registry + gateway than to N hand-injected beans. Your agent depends on *one* tool endpoint, and operations decides what's actually behind it.

!!! breaks "Where the analogy breaks"
    MCP is **stateful and streamed**, which a REST proxy is not. A tool session is a long-lived Streamable-HTTP connection carrying multi-part JSON-RPC, not a series of independent request/response pairs — so reasoning about it like a stateless Apigee proxy call will mislead you on sessions, ordering, and timeouts. And tool *names* are part of the routing key: the gateway prefixes them (`github__…`) to disambiguate servers, which has no analogue in path-based REST routing.

## Hands-on lab

<div class="lab" markdown="1">
#### Lab — front an MCP tool server with an MCPRoute

**Prereqs:** the gateway and `Gateway` resource from 1.5 (export `$NAMESPACE` and `$GATEWAY_HOST`), `kubectl`, and a sample MCP server reachable in-cluster (any MCP server works; this lab assumes one exposed as a `Backend`/Service named `github-mcp`).

**1. Declare the MCP backend** the route will point at:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: Backend
metadata:
  name: github-mcp
  namespace: ${NAMESPACE}
spec:
  endpoints:
    - fqdn:
        hostname: github-mcp.${NAMESPACE}.svc.cluster.local
        port: 8080
```

**2. Create the MCPRoute** — the agent-facing endpoint that multiplexes tool servers:

```yaml
apiVersion: aigateway.envoyproxy.io/v1beta1
kind: MCPRoute
metadata:
  name: tools
  namespace: ${NAMESPACE}
spec:
  parentRefs:
    - name: ai-gateway              # the Gateway from session 1.5
  path: /mcp                        # where the agent connects
  backendRefs:
    - name: github-mcp
      # toolSelector scopes which tools this server exposes (full control in 5.3)
      toolSelector:
        includeRegex:
          - "issue_.*"
```

!!! pitfall "Watch out"
    Without a `toolSelector`, an MCPRoute exposes the server's **entire** tool surface to the agent — including destructive tools you never meant to grant. Start with an explicit `include`/`includeRegex` allow-list; you lock it down by identity and scope in 5.4.

**3. Apply and confirm the route is programmed:**

```bash
kubectl apply -f github-mcp.yaml -f mcproute.yaml
kubectl get mcproute tools -n "$NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}{"\n"}'
```

**4. List and call a tool through the gateway** with an MCP client (or curl over the Streamable-HTTP/JSON-RPC endpoint). Listing should show **prefixed** names:

```bash
# tools/list — note the server prefix on every tool name
curl -s "https://$GATEWAY_HOST/mcp" \
  -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools[].name'
# → "github__issue_read"  "github__issue_search"
```

**What success looks like:** `tools/list` returns the GitHub server's tools under a `github__` prefix (proof the gateway is multiplexing, not pass-through), and a `tools/call` for `github__issue_read` returns a real result routed through the MCPRoute — your agent reached a tool through one governed endpoint, with the invocation now observable at the gateway.
</div>

## Verify it

- `tools/list` names are **prefixed** (`github__…`). If they're bare, the request bypassed the MCP gateway and hit the server directly.
- A tool **excluded** by the `toolSelector` regex does not appear in the catalog at all — the gateway is the one deciding what the agent can see.
- The invocation shows up in the gateway's telemetry (you wire this fully in 6.1) — tools are now observable, which they never were as direct clients.

!!! failure "Common failure modes"
    - **Agent connects straight to the tool server.** Then you've governed nothing — no auth, no audit, no selection. The agent must point only at the MCPRoute path. *(Symptom: tool names have no server prefix.)*
    - **Treating MCP like stateless REST.** The session is long-lived and streamed; dropping or load-balancing it like independent HTTP calls breaks tool state and ordering.
    - **Exposing every tool by default.** Without a `toolSelector`, the agent gets the server's *entire* surface — including destructive tools you didn't mean to grant. Scope it (and lock it down properly in 5.4).
    - **Name collisions across servers.** Two servers with a same-named tool rely on the prefix to disambiguate; assuming bare names will route the wrong call.

!!! stretch "Stretch goal"
    Front a sample MCP server with an MCPRoute and use `toolSelector` to expose only its read tools. Then try to call a write tool by its prefixed name and confirm it isn't in the catalog — your first taste of tool-level authorization, which you'll enforce by identity and scope in 5.4.

## Recap & next

You can now front MCP tool servers with an **MCPRoute**, understand it as the tools-shaped sibling of `AIGatewayRoute`, and call a tool through one governed, streamed endpoint with selective exposure. Tools are now backends you operate, not clients buried in agent code.

**Next — 5.3:** scale this to many servers. You'll **aggregate and multiplex** several MCP servers behind one surface — tool-name prefixing, collision handling, and `toolSelector` to expose exactly the right subset of a large tool estate.
