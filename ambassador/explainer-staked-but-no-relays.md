# My supplier is staked. Why does it earn nothing?

*An explainer for new node runners. Every number below was read from MainNet on 2026-11-13 (block ~1,006,900) and can be re-checked with the commands shown.*

In October a supplier operator asked this in `#node-chat`: 60,000 POKT staked on `solana`, online since mid-September, and two settlements of one relay each, both showing **0.00 POKT** ([the question](https://discord.com/channels/553741558869131266/564836328202567725/1555149782291324960)). It is the most common first-month surprise on Pocket, and the answer is not "wait longer".

**Staking buys eligibility. It does not buy traffic.** Four gates sit between a stake and a payout, and each one can be checked.

## Gate 1: someone has to be paying for that service

Work on Pocket is organised in sessions. A session exists for one *(application, service)* pair, so a service only produces relays when an application is staked for it and a gateway (or the application itself) is sending requests. No staked application on your service means no sessions, however healthy your node is.

```bash
pocketd query session get-session <application_address> <service_id> --network main
```

The response lists the suppliers assigned for the current window. If you do not know an application address for your service, that is already the finding: ask in Discord who is sending traffic to it.

## Gate 2: you have to be seated

A session seats a limited number of suppliers:

```bash
pocketd query session params --network main
# num_suppliers_per_session: "50"
```

The seats are filled by a deterministic hash of the session ID and each operator address. The docs say it plainly: *stake is an eligibility gate, not a weighting*. Staking 600,000 instead of the 59,500 minimum does not move you up the list. If `N` suppliers are staked on a service, your long-run share of seats is about `50 / N`. On a crowded chain RPC service that can be a small fraction; on a niche data service with three suppliers you are seated every time.

Sessions rotate every 20 blocks (`pocketd query shared params`, `num_blocks_per_session`), which is roughly 21 minutes at the current ~62-second block time. So "not seated right now" changes quickly, and a day of observation is a fair sample.

## Gate 3: seated is not the same as called

Inside a session the gateway decides which of the seated suppliers receives each request. Gateways route on quality: does the endpoint answer, is the answer correct, how fast is it.

In the October case the operator looked up a live `solana` session, found they were not in it, and noticed that the roughly 50 suppliers that were seated all used **HTTPS hostnames**, while their own endpoint was a plain `http://` address. The team's reply added the other half: a staked node also needs an application stake that is delegated to a gateway before any traffic reaches it (gate 1). For services listed on the Agentic Portal this is a written rule, not a preference: the Service Audit requires an HTTPS hostname endpoint (A5) with a publicly trusted TLS certificate (A6).

Practical minimum before blaming the network:

- a public hostname, not an IP
- a certificate a browser accepts without warnings
- the backend actually returns correct data for that service (test it through your own RelayMiner, not only directly)

## Gate 4: small numbers round to zero

"1 relay, 0.00 POKT" is usually correct arithmetic, not a bug. A relay costs the application:

```
compute_units_per_relay × compute_units_to_tokens_multiplier ÷ compute_unit_cost_granularity   (in upokt)
```

For `solana` today: `5,033 × 153,156 ÷ 1,000,000 = 771 upokt`, which is 0.00077 POKT. The supplier receives 79% of the 97.5% that is minted back, about **0.0006 POKT**. An explorer that shows two decimals prints 0.00.

The same formula for other services: `eth` (1,579 CU) pays about 0.00024 POKT per relay in total, and a 50,000 CU data service about 0.0077 POKT. Earnings come from volume. A supplier that settles one relay per session has passed gates 1 to 3 only barely.

## A five-line checklist

1. Is an application staked and sending traffic on this service ID?
2. Am I in the supplier list of a current session for that application?
3. Is my endpoint an HTTPS hostname with a valid certificate?
4. Does a request through my RelayMiner return the right answer, quickly?
5. Do my settlements show relays in the hundreds or thousands, or single digits?

If 1 fails, pick a service with demand or bring an application. If 2 fails, wait a few sessions and count again. If 3 or 4 fail, fix the endpoint. If only 5 fails, you are being out-competed on quality or the service is quiet.

## Limits of this explainer

- Gateway routing rules belong to each gateway operator. I describe what the docs state and what operators report, not a guaranteed algorithm.
- All parameters above are governance-controlled and can change. Query them instead of trusting any article, including this one.
- On high-volume services the claim is an estimate built from sampled "reward-applicable" relays (relay mining), so per-session figures move around.

## Sources

- [Shannon architecture: sessions and supplier selection](https://docs.pocket.network/protocol/architecture)
- [Sessions, Claims & Proofs](https://docs.pocket.network/protocol/sessions-claims-proofs/)
- [List your service: audit rules A1–A9](https://docs.pocket.network/agent-services/list-your-service)
- Live parameters: `pocketd query session params`, `pocketd query shared params`, `pocketd query service show-service solana`, all with `--network main`
- The original question and the team's answer in `#node-chat`, October 2026 (linked above)

*Written by jsymen, operator of supplier `pokt10hhuv4yllh4yuvt32j4k093n99pr4vmr0exxxk`.*
