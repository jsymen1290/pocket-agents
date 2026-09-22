# pocket-agents

Seven verification-style services for the [Pocket Network](https://pocket.network) Agentic Portal, built and operated by one supplier on a Mac mini. Stdlib-only Python (3.9+), no keys, every response a JSON object, deterministic per snapshot, facts separated from judgments.

| Service id (beta + main) | Public docs | What it answers |
|---|---|---|
| `pokt-settlement-agent-v1` | https://pokt-agent.com | What was this supplier actually paid, with per-event evidence; supplier/network economics (observed, no forecasts); **operator watch** (`/v1/supplier/{op}/ops`): settlement gap class, on-chain config changes, fee balance, pending claims/proofs, check-first list |
| `pokt-network-watch-v1` | https://watch.pokt-agent.com | What changed on Pocket Network: chain params, active alerts, change events over 15 official sources, each bound to a fetch receipt + SHA-256 that can be re-verified |
| `kr-market-data-v1` | https://kr.pokt-agent.com | Are two Korean-exchange prices comparable? Upbit/Bithumb KRW tickers with data lag, orderbook-walked executable price (coverage, slippage, fee), kimchi premium vs Binance USDT, USD/KRW |
| `kr-export-pulse-v1` | https://export.pokt-agent.com | What changed in Korea's exports (10 major items, 10-day provisional), which items drove it, and was it revised: same-fetch increments, same-stage comparisons, HS-by-country, revision log (supplier-side data.go.kr key) |
| `dart-kr-events-v1` | https://dart.pokt-agent.com | What changed for a Korean listed company: filings normalised by kind, corrections linked to originals, field-by-field issuance term diffs, point-in-time view (supplier-side OpenDART key) |
| `agent-service-benchmark-v1` | https://bench.pokt-agent.com | Which portal service should an agent call, and what is the evidence? Acceptance-audit verdict, serving flag, price, schemas, example freshness and category competition, ordered by a rule stated in every response. No invented score, no paid call on your behalf |
| `pine-script-lint-v1` | https://pine.pokt-agent.com | Does this Pine Script contain structures that make history look better than live? `/v1/integrity` (future data, unconfirmed HTF, past-drawn signals, realtime-only state, strategy assumptions) + `/v1/lint` (syntax, v4 remnants, limits) |

All six pass the portal audit 9/9 on Beta TestNet (2026-09-20) with settled claims served through the new `pocket-relay-miner`.

## Two calls, shown

Both of these are live right now, no key and no account.

**Does this Pine script make backtests look better than live trading?**

```bash
curl -s -X POST https://pine.pokt-agent.com/v1/integrity -H 'content-type: application/json'   -d '{"source":"//@version=6
indicator(\"x\", overlay=true)
d = request.security(syminfo.tickerid, \"D\", close, lookahead=barmerge.lookahead_on)
plot(d)
"}'
```

```json
{"verdict": "FAIL", "counts": {"error": 1, "warning": 1, "info": 0},
 "findings": [
   {"code": "P007", "severity": "warning", "line": 3, "message": "request.security with lookahead_on repaints on historical bars"},
   {"code": "P201", "severity": "error",   "line": 3, "message": "request.security(..., lookahead_on) without a [1]-style offset reads the future on historical bars"}]}
```

Add `close[1]` to that same call and the error disappears: the accepted confirmed-bar idiom is recognised, not punished.

**What would 0.5 BTC actually cost on a Korean exchange right now?**

```bash
curl -s 'https://kr.pokt-agent.com/v1/executable/BTC?qty=0.5&side=buy&fee_pct=0.05'
```

```json
{"venue": "upbit", "average_price_krw": 116381997.41, "coverage": 1.0,
 "slippage_pct": 0.0009, "orderbook_lag_seconds": 1.865}
```

`coverage` is the fraction of the quantity the visible book can fill, `slippage_pct` is against best price, and `orderbook_lag_seconds` is how stale the book was when we read it. A venue that is down returns a status code, never a guess.

Median response time measured from the host on 2026-09-22: 248-333 ms across the six services.

## Layout

```
pocket_agents/
  client.py        GET-only allow-listed HTTP client for public Pocket REST/RPC (budgeted)
  indexer.py       data.pocket.network GraphQL: every settlement for an operator and range, complete coverage
  chain.py         LCD/RPC queries: params, supplier, service, balance, block_search/results
  settlement.py    EventClaimSettled extraction + aggregation
  collect.py       always-on collector (STATE/ALERTS/settlements ndjson) that the agents read
  surveillance.py  15 official sources: fetch, diff, receipts, verify
  economics.py     supplier/network economics from public analytics (observed values only)
  ops.py           operator watch (facts vs judgments)
  agent.py         pokt-settlement-agent-v1 HTTP service (:8793) + service card + OpenAPI
  watch.py         pokt-network-watch-v1 (:8794)
  krmarket.py      kr-market-data-v1 (:8795)
  pinelint.py      pine-script-lint-v1 (:8796)
  benchmark.py     agent-service-benchmark-v1 (:8799) portal catalogue + acceptance audit comparison
  krexport.py      kr-export-pulse-v1 (:8797)  needs DATA_GO_KR_SERVICE_KEY
  dartevents.py    dart-kr-events-v1 (:8798)   needs OPENDART_API_KEY
  cli.py           entry points: *-serve, *-card, collect, surveil, ask
tests/test_services.py   offline tests with fixtures (no network)
deploy/            watch list, surveillance sources, RelayMiner/launchd templates (no secrets)
docs/              design notes
```

## Run locally

```bash
python -m unittest tests.test_services
python -m pocket_agents pinelint-serve --port 8796     # then POST http://127.0.0.1:8796/v1/lint {"source": "..."}
python -m pocket_agents krmarket-serve --port 8795
python -m pocket_agents watch-serve --port 8794        # needs data/ written by `collect` and `surveil`
python -m pocket_agents agent-serve --port 8793 --no-llm
```

Optional LLM narration in the settlement agent and surveillance review is disabled unless a runtime with `config`/`router` modules is present; every answer has a deterministic template fallback and a number guard.

## Serving on Pocket

Each service ships its on-chain card (`*-card`) and OpenAPI. Deployment follows the portal's design rules: JSON-object responses, inputs in the body, no caller auth, no error-looking words in success bodies, identity/readiness/functional probes, determinism. See `deploy/` for the relayer/miner config shapes and `docs/` for the registration record (service, supplier, application stake, first settled claim).

## License

MIT
