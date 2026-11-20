# Call a Pocket service from an AI agent in 10 minutes

This tutorial takes you from nothing to a signed, paid relay against a live Pocket service. You create a key, fund it, stake it as an application for one service, and send a relay that a supplier answers. It runs on Beta TestNet, so it costs nothing. The last section lists what changes on MainNet.

I ran every command below from a fresh key on 20 November 2026. From the first command to the answer took 3 minutes 24 seconds, most of it waiting for the next session to start.

## What you need

- `pocketd` v0.1.36 or later ([install guide](https://docs.pocket.network/))
- `curl` and `python3`
- A terminal. No account, card or API key.

## 1. Create a key (10 seconds)

The `test` keyring stores keys without a password. Use it only on Beta.

```bash
KR="--network=beta --keyring-backend test --keyring-dir $HOME/pocket-tutorial"
pocketd keys add my-agent $KR
APP=$(pocketd keys show my-agent -a $KR)
echo $APP
```

Keep the mnemonic it prints somewhere safe if you plan to reuse the key.

## 2. Get Beta POKT from the faucet (10 seconds)

```bash
curl -s "https://faucet.beta.pocket.network/send/beta-pokt/$APP"
pocketd query bank balance $APP upokt --network=beta
```

Expected: a transaction hash, then a balance of `100000000000` upokt (100,000 Beta POKT). The faucet allows two requests per address per day.

## 3. Stake the key as an application for one service (10 seconds)

An application pays for relays out of its stake. This one is staked for `kr-apt-trades-v1`, a service that returns Korean apartment sale records.

```bash
printf 'stake_amount: 2000000000upokt\nservice_ids:\n  - kr-apt-trades-v1\n' > app_stake.yaml
pocketd tx application stake-application --config app_stake.yaml --from my-agent $KR \
  --gas 300000 --fees 300000upokt -y
```

Expected: `code: 0` and a `txhash`.

Stake 2,000 rather than the 1,000 minimum. Every settled relay burns a little of the stake, and an application that drops below the minimum is unstaked by the chain.

## 4. Wait for the next session (up to 10 minutes)

Pocket groups relays into sessions of 20 blocks, and a new application joins at the next session boundary. Beta blocks are about 30 seconds apart.

```bash
RPC=https://sauron-rpc.beta.infra.pocket.network
H=$(curl -s $RPC/status | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["sync_info"]["latest_block_height"])')
echo "now $H, session starts at $(( (H/20)*20 + 21 ))"
```

Re-run until the height passes the printed number. In my run that took about three minutes.

## 5. Send a relay

The relay is signed with your application key and sent to a supplier staked for the service. The supplier below serves `kr-apt-trades-v1` on Beta.

```bash
SUPPLIER=pokt16m783stsdya2cv9cvah57hhekyxkv7xphajqhf
pocketd relayminer relay --app $APP --supplier $SUPPLIER --from my-agent $KR \
  --grpc-insecure=false --count 1 \
  --payload '{"question":"강남구 아파트 매매 시세 추이"}'
```

Expected log lines:

```
✅ Request 1: Status code 200, Response size … bytes
✅ Successfully sent 1 relay requests
```

followed by the service's answer. Shortened:

```json
{
  "intent": "TREND",
  "status": "OK",
  "service": "kr-apt-trades-v1",
  "data": {
    "region": {"lawd_cd": "11680", "name": "강남구"},
    "months": [
      {"month": "202610", "month_status": "REPORTING_WINDOW_OPEN", "count": 154, "cancelled": 4,
       "median_price_krw": 2925000000, "mom_change_pct": 10.86}
    ],
    "note": "Cancelled trades excluded from statistics. Months still inside the 30-day reporting window can grow."
  }
}
```

That is a real answer from the Ministry of Land's transaction register, paid for by your stake and served by an independent supplier.

## 6. Hand it to an agent

An agent only needs the same call. The smallest wrapper is a tool that shells out to step 5 and returns the JSON (I ran this exact function against Beta; `APP`, `SUPPLIER` and `KEYRING_DIR` are the values from the steps above):

```python
import json, subprocess

def kr_apartment_trades(question: str) -> dict:
    """Korean apartment sale records by district and month."""
    out = subprocess.run(
        ["pocketd", "relayminer", "relay", "--app", APP, "--supplier", SUPPLIER,
         "--from", "my-agent", "--network=beta", "--keyring-backend", "test",
         "--keyring-dir", KEYRING_DIR, "--grpc-insecure=false", "--count", "1",
         "--payload", json.dumps({"question": question}, ensure_ascii=False)],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out[out.index("{\n"):])
```

Register `kr_apartment_trades` as a tool in your agent framework, and the model can call it like any other function.

## What changes on MainNet

| Step | Beta | MainNet |
|---|---|---|
| Flags | `--network=beta --keyring-backend test` | `--network=main --keyring-backend file` (password-protected) |
| Funds | faucet | real POKT; the application minimum is 1,000 POKT |
| Supplier | any supplier staked for the service on Beta | find one with `pocketd query supplier list-suppliers --service-id kr-apt-trades-v1 --network=main` |
| Cost per relay | free | compute units per relay × `compute_units_to_tokens_multiplier` ÷ `compute_unit_cost_granularity` upokt; check both with `pocketd query shared params --network=main` |

If you would rather pay per call in USDC without holding POKT, the same service is sold through the [Agentic Portal](https://agent.pocket.network/services/kr-apt-trades-v1) at $0.005 per call.

## If something goes wrong

- **"application not found" or "not in session":** you sent the relay before the next session started. Wait for step 4.
- **Balance is zero after the faucet:** wait 10 seconds and query again. Beta blocks are slow.
- **Stake transaction fails with insufficient fee:** raise `--fees` and `--gas` together.

Never paste a mnemonic or a MainNet key into a script you share. Everything above uses a throwaway Beta key.
