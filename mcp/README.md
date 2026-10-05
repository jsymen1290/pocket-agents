# POKT Agent MCP

Every pokt-agent.com service as [Model Context Protocol](https://modelcontextprotocol.io) tools: Pine Script checks, backtest reconciliation and official Korean finance data. Read-only, deterministic where the source is, one JSON object per call with its sources and limits.

## Use it

**Remote (no install, no sign-in):** add `https://mcp.pokt-agent.com/mcp` as a custom connector (Streamable HTTP) in your MCP client.

**Local (stdio, Python 3.9+, standard library only):**

```json
{
  "mcpServers": {
    "pokt-agent": {
      "command": "python3",
      "args": ["/path/to/pocket-agents/mcp/pokt_agent_mcp.py"],
      "env": { "PINE_CHECK_API_KEY": "" }
    }
  }
}
```

`PINE_CHECK_API_KEY` is optional. Without it the Pine tools use the free trial (30 checks a day per client); see [pine.pokt-agent.com](https://pine.pokt-agent.com/) for plans.

## Tools

| Tool | What it does |
|---|---|
| `pine_integrity` | TradingView Pine v5/v6: future data, unconfirmed higher-timeframe values, signals drawn in the past, realtime-only state, fill and cost assumptions |
| `pine_lint` | adds syntax and version checks to the integrity checks |
| `backtest_reconcile` | recompute net profit, win rate, profit factor and drawdown from a TradingView trade list; each claimed metric MATCH or MISMATCH |
| `kr_figure_check` | numbers in a Korean finance text vs ECOS, Export-Import Bank of Korea and DART (percent vs %p, 조/억, consolidated vs separate) |
| `dart_correction_impact` | which cited DART filings were later corrected, and which numbers in a report are now stale |
| `kr_rates` | BoK base rate, money-market rates and the KTB curve on a date |
| `kr_fx` | KRW deal basis, buying and selling rates |
| `kr_law_article` | one article of a Korean statute, quoted, with dates and the official link |
| `kr_apartment_trades` | Korean apartment sale prices (MOLIT) |
| `dart_events` | Korean listed-company disclosures and corrections |
| `kr_export_pulse` | South Korea's export statistics (Korea Customs Service) |

The same services are on [Pocket Network](https://agent.pocket.network) and as plain REST APIs (each tool's host is in `HOSTS` in the source).
