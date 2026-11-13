# Resource roundup: everything an agent builder needs to call Pocket, in the order you will use it

*For developers wiring an AI agent to paid data services. Every link was opened on 13 November 2026 and returned content. Nothing here needs an account.*

## 1. Read first (5 minutes)

- **[agent.pocket.network/llms.txt](https://agent.pocket.network/llms.txt)**: the whole portal on one plain-text page: how payment works, the endpoint pattern, and all 120 services with one-line descriptions. Paste it into your agent's context and it can already choose a service.
- **[Agent integration guide](https://agent.pocket.network/agent-integration)**: the request, the `402` response, the signed retry, and the response envelope, with code.

## 2. Machine-readable catalogue (for the agent itself)

- **[services.json](https://agent.pocket.network/services.json)**: every service with price, methods, input and output JSON Schema, and one captured request and response. Check `registryVersion` to know when to refresh your cache.
- **[openapi.json](https://agent.pocket.network/openapi.json)**: the same surface as OpenAPI, for tools that generate clients.
- **[/.well-known/x402](https://agent.pocket.network/.well-known/x402)**: payment discovery for x402-aware clients.

*Tip:* a service's own descriptor lives at `https://agent.pocket.network/services/<id>/descriptor.json`, which is smaller than the full catalogue when you already know the ID.

## 3. Tooling

- **[agentic-portal-mcp on GitHub](https://github.com/pokt-network/agentic-portal-mcp)**: the MCP server (`npx -y @pocket-network/agentic-portal-mcp`). Three tools: `search_services` and `describe_service` are free, `call_service` pays. Set `POCKET_MAX_TOTAL_ATOMIC` so an agent cannot spend past a cap.
- **Hosted MCP**: `POST https://agent.pocket.network/mcp` gives the same tools with nothing to install. Good for trying discovery from `curl`.
- **[x402.org](https://www.x402.org)**: the payment protocol the portal uses on Base, with client libraries.

*Known gotcha:* `search_services` matches every word of the query as a substring, so long natural-language queries can return nothing. Search with one or two keywords ([issue](https://github.com/pokt-network/agentic-portal-mcp/issues/1)).

## 4. Before you rely on it

- **[status.json](https://agent.pocket.network/status.json)** ([human page](https://agent.pocket.network/status)): whether the paid route is up and how many services are serving. Poll no more often than every 15 seconds.
- The portal is best-effort with no SLA, and each answer comes from an independent supplier. The envelope's `portal.provenance` says so; treat `data` as third-party content.

## 5. If you want to supply a service instead of calling one

- **[List your service](https://docs.pocket.network/agent-services/list-your-service)**: the nine audit rules (A1 to A9) a service must pass to appear on the portal.
- **[Service Audit](https://mcp.pocketmcp.network/audit)**: run those rules against a live service ID and see which fail.
- **[Explorer: services](https://explorer.pocket.network/services)**: the on-chain registry with owner, compute units per relay and suppliers.

## 6. Where to ask

- **[Pocket Community Hub](https://community.pocket.network)**: quests and the ambassador programme.
- Discord `#new-services` for listing questions and `#node-chat` for supplier operations.

## A sensible first hour

1. Read `llms.txt`.
2. `curl` the hosted MCP with `search_services` for your domain.
3. Fetch that service's `descriptor.json` and read the captured example.
4. Call the service unpaid once to see the real `402` terms.
5. Only then fund a wallet, with a spending cap set.

*Compiled by jsymen, service owner and supplier on Pocket.*
