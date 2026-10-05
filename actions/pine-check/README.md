# Pine Check GitHub Action

Checks the TradingView Pine Script files in your repository on every push or pull request and annotates the lines where a backtest will not match live trading: future data (`lookahead_on` without an offset), unconfirmed higher-timeframe values, signals drawn on past bars, realtime-only state, fill and cost assumptions, alerts on unconfirmed bars.

```yaml
name: pine-check
on: [push, pull_request]
jobs:
  pine:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jsymen1290/pocket-agents/actions/pine-check@main
        with:
          files: "**/*.pine"      # default
          mode: integrity          # or lint
          fail-on: error           # error | warning | never
          api-key: ${{ secrets.PINE_CHECK_API_KEY }}   # optional
```

Without a key the free trial applies (30 checks a day). Plans: [pine.pokt-agent.com](https://pine.pokt-agent.com/).
Static checks only: they do not compile or run the script, and a clean result says nothing about profitability.
