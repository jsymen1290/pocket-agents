# Deploy notes (Beta TestNet, 2026-09-20)

- RelayMiner: [pokt-network/pocket-relay-miner](https://github.com/pokt-network/pocket-relay-miner) (relayer + miner + Redis 8.2+). The legacy `pocketd relayminer` cannot submit claims for REST services through public gRPC (`x-cosmos-block-height` header stripped).
- One relayer serves all four services (routed by service id); each backend is a local port (8793-8796).
- Keys: the miner reads a hex keys file or a Cosmos keyring; the `file` keyring backend needs a TTY, so use a dedicated operator key exported to a 0600 file (never commit it).
- launchd on macOS: program, config, keys and logs must live on the home volume; anything on an external volume exits 78 or hangs silently. Use `/bin/bash` + a wrapper script as the program.
- Two networks on one Redis need distinct `redis.namespace.base_prefix` values or the second miner stays in standby.
- Gas: `stake-supplier --services-only` with n services needs about 200k gas per service.
- Order: add-service (card) -> owner stake-only -> operator services-only (activates at next session) -> app stake -> relay -> claim -> proof -> settlement (~25 min on beta) -> audit.
