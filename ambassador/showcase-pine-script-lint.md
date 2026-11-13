# Pine Script Lint in action: catching a backtest that reads the future

*A walkthrough of a listed Pocket service, `pine-script-lint-v1`, with real requests and responses captured on 2026-11-13.*

- Portal page: https://agent.pocket.network/services/pine-script-lint-v1
- Explorer: https://explorer.pocket.network/service/pine-script-lint-v1
- Public docs: https://pine.pokt-agent.com

## What the service is for

TradingView strategies are written in Pine Script. A script can look profitable in a backtest because it quietly uses information that was not available at the time: tomorrow's daily close, an unfinished higher-timeframe bar, a signal drawn on a past bar. TradingView compiles such scripts without complaint. This service reads the source and reports those structures with a line number, a rule code and a severity. It never runs the script and never claims that it compiles.

## Step 1: find it and read its contract (free)

The portal's hosted MCP endpoint answers discovery calls without payment:

```bash
curl -s -X POST https://agent.pocket.network/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_services","arguments":{"query":"pine script"}}}'
```

Result: one match, `pine-script-lint-v1`. `describe_service` then returns the price (`0.005000` USD), the operations (`POST /v1/integrity`, `POST /v1/lint`, `GET /v1/rules`, `GET /v1/version`, `GET /v1/health`), the input schema and a captured example.

## Step 2: send a script that cheats

Four lines. The `request.security` call asks for the daily close with `lookahead_on`, which on historical bars returns the close of a day that has not finished yet.

```bash
curl -s -X POST https://pine.pokt-agent.com/v1/integrity -H 'content-type: application/json' \
  -d '{"source":"//@version=6\nindicator(\"x\", overlay=true)\nd = request.security(syminfo.tickerid, \"D\", close, lookahead=barmerge.lookahead_on)\nplot(d)\n"}'
```

Response (trimmed to the fields that matter, request id `r_0d8ef4807faa`):

```json
{
  "schema": "pine-integrity-result/v1",
  "service": "pine-script-lint-v1",
  "linter_version": "0.2.1",
  "pine_version": 6,
  "verdict": "FAIL",
  "counts": {"error": 1, "warning": 1, "info": 0},
  "findings": [
    {"code": "P007", "severity": "warning", "line": 3,
     "message": "request.security with lookahead_on repaints on historical bars"},
    {"code": "P201", "severity": "error", "line": 3,
     "message": "request.security(..., lookahead_on) without a [1]-style offset reads the future on historical bars"}
  ],
  "scope": "static heuristics; it does not run the script and cannot prove the absence of repainting"
}
```

## Step 3: fix it and send it again

The accepted way to use a confirmed higher-timeframe value is to offset the series by one bar. One change: `close` becomes `close[1]`.

Response (request id `r_aa119f2f4e24`):

```json
{
  "verdict": "CLEAN",
  "counts": {"error": 0, "warning": 0, "info": 1},
  "findings": [
    {"code": "P007", "severity": "info", "line": 3,
     "message": "request.security with lookahead_on and a [1] offset (accepted non-repainting idiom for confirmed HTF bars); verify the offset covers every series"}
  ]
}
```

The error is gone and the warning is downgraded to a note. The service recognises the correct idiom instead of punishing every use of `lookahead_on`, which is what makes it usable in CI: a clean script stays clean.

## Step 4: what the same call costs through Pocket

Steps 2 and 3 used the supplier's public endpoint, which is open for this service. An agent going through the Agentic Portal calls `POST https://agent.pocket.network/v1/pine-script-lint-v1` and first receives the price:

```
HTTP/1.1 402 Payment Required
```
```json
{"x402Version": 2,
 "accepts": [{"scheme": "exact", "network": "eip155:8453", "amount": "5000",
              "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
              "payTo": "0xF732ea490c5766071785a2310523f7fA2CEbB829", "maxTimeoutSeconds": 60}]}
```

`5000` is in USDC's six decimals: half a cent on Base. The client signs that authorization, retries, and gets the same JSON wrapped in the portal envelope (`portal.provenance`, `portal.serviceId`, `portal.schemaCheck`, and the supplier's response under `data`). On chain the relay is priced at 50,000 compute units (`pocketd query service show-service pine-script-lint-v1 --network main`).

## What I did and did not test

- Tested today: the two integrity calls above (direct endpoint), the free `search_services` and `describe_service` calls, and the unpaid portal call that returns the 402 terms. Outputs are pasted as received, shortened only where marked.
- Not tested today: a paid portal call. It needs a funded Base wallet, and I did not make one for this walkthrough. The portal's own captured example for this service (in `describe_service`, dated 2026-11-09) shows the paid response for an equivalent failing script.

## Where it fits

- A trader checks a script before trusting its backtest.
- A coding agent that writes Pine runs the check on its own output and fixes what it finds.
- A repository runs it on every push through the [Pine Check GitHub Action](https://github.com/marketplace/actions/pine-check).

## Limits

Static heuristics only. A `CLEAN` verdict means none of the known patterns were found, not that the strategy is sound or profitable. `GET /v1/rules` lists exactly what is checked.

*Disclosure: I built and operate this service (2nd place, Pocket Agentic Services Hackathon).*
