# From relay to payout: why Pocket pays you half an hour later

*An explainer for service owners and suppliers who see a request answered in 300 ms and the POKT arrive much later. Parameters were read from MainNet on 2026-11-13.*

A supplier on Pocket answers a request immediately but is paid only after the chain has had a chance to audit the work. The delay is not congestion. It is a fixed timetable written in on-chain parameters, and once you can read it you can predict to the block when a session will settle.

## The timetable

```bash
pocketd query shared params --network main
```

| Parameter | Live value | Meaning |
|---|---|---|
| `num_blocks_per_session` | 20 | Relays are served in 20-block windows |
| `grace_period_end_offset_blocks` | 10 | Late relays for the old session are still accepted for 10 blocks |
| `claim_window_open_offset_blocks` | 11 | Claims are accepted from 11 blocks after the session ends |
| `claim_window_close_offset_blocks` | 10 | The claim window stays open 10 blocks |
| `proof_window_open_offset_blocks` | 1 | Proofs are accepted 1 block after claims close |
| `proof_window_close_offset_blocks` | 10 | The proof window stays open 10 blocks |

Counted from the last block of a session:

```
session ends ─ +11 claim opens ─ +21 claim closes ─ +22 proof opens ─ +32 proof closes → settlement
```

Blocks currently take about 62 seconds (1,260 blocks between the v0.1.37 upgrade at 1,005,645 and block 1,006,905, measured over 21 h 42 min). So a relay served at the start of a session is paid roughly 20 + 32 = 52 blocks later, about **54 minutes**; a relay served at the very end, about **33 minutes** later.

## What happens in each window

**Session.** RelayMiner answers requests and stores each signed relay in a Merkle tree on disk. Nothing is on chain yet.

**Claim.** RelayMiner submits one transaction per (session, application): the root of that tree and the total compute units it stands for. This is the commitment. A supplier that misses the claim window is not paid for that session at all.

**Proof.** After the claim is recorded, the chain picks one leaf of the tree using data the supplier could not know in advance, and the supplier must reveal it. Not every claim needs one:

```bash
pocketd query proof params --network main
# proof_request_probability: 0.001
# proof_requirement_threshold: 10000000 upokt   (10 POKT)
# proof_missing_penalty: 1 upokt
```

A claim worth 10 POKT or more always needs a proof. Smaller claims are asked for one with probability 0.1%. If a proof is required and does not arrive, the claim expires and the session's payment is forfeited.

**Settlement.** When the proof window closes the chain burns the cost from the application's stake and mints the payout.

## How much

```
relays × compute_units_per_relay × 153,156 ÷ 1,000,000   = upokt burned from the application
```

`153,156` is `compute_units_to_tokens_multiplier` and `1,000,000` is `compute_unit_cost_granularity`, both from the same `shared` params. `compute_units_per_relay` is set per service by its owner (`pocketd query service show-service <id>`).

A worked example with a service I own, `kr-apt-trades-v1` (50,000 CU), for 1,000 relays in one session:

| Step | Amount |
|---|---|
| Burned from the application | 7.6578 POKT |
| Minted back (`mint_ratio` 0.975) | 7.4664 POKT |
| Supplier, 79% | 5.8984 POKT |
| Block proposer / validators, 14% | 1.0453 POKT |
| DAO, 4.5% | 0.3360 POKT |
| Service owner, 2.5% | 0.1867 POKT |
| Removed from supply | 0.1914 POKT |

The split is `mint_equals_burn_claim_distribution` in `pocketd query tokenomics params`.

## What this means in practice

- **A RelayMiner restart at the wrong moment costs a whole session.** The tree lives on the supplier's disk until the claim is in. If the process is down through the claim window (10 blocks, about 10 minutes), the work is unpaid. Plan restarts for just after a claim has been submitted.
- **The supplier needs gas in its own balance.** Claims and proofs are transactions. An operator account that runs dry stops claiming while still serving relays for free.
- **Dashboards lag by design.** "No rewards in the last 30 minutes" is the normal state of a healthy supplier.
- **Service owners are paid on the same clock.** The 2.5% owner share arrives in the same settlement as the supplier's 79%, whichever supplier served the relay.

## Limits

- Block time drifts (the team reported about 64 s in June), so minute figures are estimates. Block counts are exact.
- These are governance parameters. The docs page for this topic showed 60 blocks per session when I wrote this, while the chain says 20 ([fix proposed](https://github.com/pokt-network/pocket-docs/pull/16)). Always query the chain.
- A tiny separate `global_inflation_per_claim` (0.000001) exists with its own allocation table. At one-millionth of a settlement it does not change the table above.
- On busy services the relay count in a claim is an estimate from sampled relays (relay mining difficulty), so two sessions with equal traffic can settle slightly different amounts.

## Sources

- [Sessions, Claims & Proofs](https://docs.pocket.network/protocol/sessions-claims-proofs/)
- [POKT Tokenomics](https://docs.pocket.network/token/tokenomics)
- [Supplier Rewards & Economics](https://docs.pocket.network/node-operators/rewards-economics/)
- Live: `pocketd query shared params`, `pocketd query proof params`, `pocketd query tokenomics params`, `pocketd query service show-service kr-apt-trades-v1`, all with `--network main`
- [v0.1.37 upgrade notice](https://discord.com/channels/553741558869131266/1234943674903953529/1570434695471042671) for the upgrade height and time used in the block-time estimate

*Written by jsymen, supplier operator and owner of `kr-apt-trades-v1`.*
