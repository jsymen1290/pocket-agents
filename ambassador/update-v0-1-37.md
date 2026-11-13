# Ecosystem update explained: the v0.1.37 emergency upgrade (12 November 2026)

## What happened

On 12 November the protocol team shipped **v0.1.37**, described in the [announcement](https://discord.com/channels/553741558869131266/1234943674903953529/1570434695471042671) as an emergency, consensus-breaking security release. MainNet upgraded at block **1,005,645**, at about 14:04 UTC. The [follow-up](https://discord.com/channels/553741558869131266/1234943674903953529/1570455901649895518) reported that block production resumed within a minute.

## What "consensus-breaking" means

Every validator has to run the same rules to agree on a block. A consensus-breaking release changes those rules, so it cannot be rolled out node by node. The chain names a block height in advance; at that block every node stops, switches binary, and continues. Nodes that do not switch cannot follow the chain afterwards.

## Who had to do something

| You run | Action |
|---|---|
| A validator or full node | Upgrade the binary at the height (Cosmovisor does this automatically if configured) |
| A RelayMiner (supplier) | None, per the announcement |
| A gateway or application | None |
| You just call services | None |

For builders: the release moved to a newer Go toolchain (1.27.4), so anyone compiling `pocketd` from source needs that version.

## How to confirm it on chain

```bash
pocketd query upgrade applied v0.1.37 --network main
# height: "1005645"
```

I ran this on 13 November and got that height.

## Why it matters beyond one patch

- **Supplier operators had nothing to do.** I made no change to my own RelayMiner. A one-minute pause is short next to the claim and proof windows (10 blocks each, about ten minutes), so it should not cost a session's payment.
- **Security fixes ship fast and with little detail.** That is normal: the details of a vulnerability are published after the fix is live, not before. The team also said at Office Hours that security upgrades will come more often.
- **If you run a full node without Cosmovisor, subscribe to `#shannon-mainnet`.** An emergency upgrade gives hours of notice, not weeks.

## Sources

- Release: https://github.com/pokt-network/poktroll/releases/tag/v0.1.37
- Announcement and completion notice in `#shannon-mainnet` (linked above)
- Live: `pocketd query upgrade applied v0.1.37 --network main`
