# Pocket vs. a traditional API provider: what actually differs when you make the call

*A comparison for developers deciding where an agent's data calls should go. Pocket figures are from the Agentic Portal and MainNet on 13 November 2026.*

## The same request, two ways

**Traditional provider.** Sign up, verify an email, add a card, create a key, store the key as a secret, pick a plan, then call. The provider runs the servers and you trust its answer and its invoice.

**Pocket (Agentic Portal).** Call the endpoint. It answers `402 Payment Required` with the price. Sign a USDC authorization, retry, receive the data and a receipt. No signup, no key, no plan.

## Side by side

| | Traditional API | Pocket |
|---|---|---|
| Getting access | Account, key, usually a card | None. A wallet that can sign |
| Pricing | Plans, tiers, minimums, overage | $0.005 per call, every service, stated in the `402` |
| Payment | Monthly invoice | Per request, USDC on Base (x402) or Tempo (MPP) |
| Who answers | The provider's servers | Independent suppliers staked on that service |
| Who can add a service | The provider | Anyone: 1,000 POKT service fee, then suppliers stake to serve it |
| Proof of work done | The provider's usage dashboard | On-chain claims and proofs per session |
| If the operator disappears | The API is gone | Another supplier can stake and serve the same service ID |
| Discovery by an agent | Read docs written for humans | `llms.txt`, `services.json` with schemas, MCP tools |
| Secrets to protect | An API key per provider | One wallet key, with a spend cap |
| Round trips per call | One | Two: the `402` with the price, then the paid retry |
| Path of a request | Client to provider | Client to portal to gateway to a supplier's RelayMiner to its backend |
| SLA and support | Often contractual | Best-effort, no SLA |

## Performance, stated carefully

I have no benchmark to offer and will not invent one. What can be said from the design:

- **Pocket adds hops.** A portal call passes through the portal, a gateway and the supplier's RelayMiner before it reaches the backend, and an unpaid first request costs one extra round trip. For a data lookup that an agent makes once per task this is rarely the bottleneck; for a latency-critical loop it can be.
- **Latency varies by supplier.** Different suppliers run different hardware in different places. Gateways route on observed quality, but nothing guarantees a figure.
- **Availability comes from redundancy, not from a promise.** A traditional provider gives an uptime number in a contract. Pocket gives the possibility of several independent suppliers per service and a public status page. For a service with a single supplier, that redundancy does not exist yet.
- **Settlement speed is not request speed.** The response is immediate; the supplier's payment settles on chain about half an hour later. The caller never waits for that.

Measure your own endpoint before deciding. `status.json` and a handful of timed calls tell you more than any comparison article.

## Where Pocket is the better fit

- **Agents that choose tools at run time.** An agent cannot fill in a signup form. It can read a catalogue and pay half a cent.
- **Many small sources.** Ten providers means ten accounts, ten keys and ten minimum plans. On Pocket it is one payment method and the same call shape for all 120 services.
- **Occasional use.** Twenty calls a month cost $0.10, not a subscription.
- **Niche data nobody sells as a product.** Because anyone can register and supply a service, long-tail sources (a national statistics feed, a linter for one language) can exist without a company behind them.

## Where a traditional provider is the better fit

- **You need a contract.** Uptime guarantees, a support line, data-processing agreements. The portal promises none of these.
- **Very high volume on one endpoint.** At millions of calls, a negotiated plan will usually beat a flat per-call price.
- **Streaming or stateful sessions.** The portal's model is one request, one payment, one response.
- **You must know exactly who processed the data.** On Pocket the answering supplier is one of those seated for the session, not a named vendor.

## What does not change

You still validate what comes back. A traditional provider can return wrong data too; Pocket simply says so on the label, marking every response `third-party-supplier`. The difference is that on Pocket the work is also recorded on chain, so *whether* a relay was served and paid for is public, even though *correctness* remains your check.

## A practical way to decide

Use Pocket for discovery-driven, low-volume, many-source calls made by agents. Keep a contracted provider for the one or two endpoints your product cannot run without. They are not exclusive; most systems will want both.

## Sources

- Portal overview and payment flow: https://agent.pocket.network/llms.txt
- Integration guide: https://agent.pocket.network/agent-integration
- Service fee and stake parameters: `pocketd query service params --network main`, `pocketd query supplier params --network main`
- Claims and proofs: https://docs.pocket.network/protocol/sessions-claims-proofs/
- Portal status: https://agent.pocket.network/status
