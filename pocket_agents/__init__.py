"""POKT always-on support package (read-only public chain data, no keys, no signing).

Modules
  client     GET-only HTTP with an origin allow-list, byte/time budgets and receipts
  chain      typed queries against the public Pocket MainNet REST/RPC endpoints
  settlement EventClaimSettled extraction from block_results (supplier income evidence)
  collect    one bounded collection cycle -> data/pokt/STATE.json, ALERTS.json, history, receipts
  prepare    prints exact pocketd commands / config files for the user to run (never executes them)
  cli        python -m pocket_agents <collect|status|alerts|prepare-*>

Everything here is Python 3.9 compatible and uses only the standard library.
"""
