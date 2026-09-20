"""Supplier / network economics (master-map agent #3 as a product endpoint inside pokt-settlement-agent-v1).

network_snapshot(): total supplier stake, supplier count, 7d/30d change, daily settlement volume and an
observed network-average settlement yield (settled POKT per day / total supplier stake, annualised as a
plain ratio of observed values, labelled OBSERVED_NETWORK_AVERAGE — not a forecast, not the caller's yield).
Sources: analytics.pocket.network (public JSON, same endpoints as the C-track collector) + chain params.
supplier_economics(): one operator's stake, share of network stake, 7d/30d settlements from the collector's
ndjson (if watched) or a bounded live scan, service breakdown. Numbers are observed integers; nothing is
estimated; costs/prices/FX stay null.
"""
import datetime
import json
import os
import urllib.request

from . import chain, settlement
from .collect import _read_ndjson, _parse_iso

ANALYTICS = "https://analytics.pocket.network/api"
SUPPLIER_SHARE = 0.975 * 0.79  # tokenomics params observed 2026-09-19: mint_ratio x mint_equals_burn_claim_distribution.supplier
UTC = datetime.timezone.utc
_CACHE = {"at": 0, "data": None}


def _get(path, timeout=20):
    req = urllib.request.Request(ANALYTICS + path, headers={"accept": "application/json", "user-agent": "llm-runtime-pokt-economics/0.1 (read-only)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(8 * 1024 * 1024).decode("utf-8"))


def _pct(a, b):
    try:
        return round((float(a) / float(b) - 1.0) * 100.0, 3) if float(b) else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def network_snapshot(cache_seconds=600):
    """Network-wide supplier economics from public analytics; cached in-process."""
    import time
    if _CACHE["data"] and time.time() - _CACHE["at"] < cache_seconds:
        return _CACHE["data"]
    sup = _get("/suppliers?range=60d")
    net = _get("/network?range=60d")
    traffic = _get("/traffic?range=60d")
    evo = sup.get("evolution") or []
    last = evo[-1] if evo else {}
    d7 = evo[-8] if len(evo) >= 8 else None
    d30 = evo[-31] if len(evo) >= 31 else None
    staked = net.get("staked") or []
    claims = net.get("claims") or []
    perf = traffic.get("performance") or []
    # settled POKT/day across the network: sum of per-service claimedUpokt over the 60d window / days
    claimed_60d_upokt = sum(int(p.get("claimedUpokt") or 0) for p in perf)
    days = max(1, len(claims))
    settled_per_day_pokt = claimed_60d_upokt / 1e6 / days
    total_stake = float(last.get("stakedPokt") or 0)
    yield_ratio = (settled_per_day_pokt * 365.0 / total_stake) if total_stake else None
    out = {
        "schema": "pokt-network-economics/v1", "as_of": (sup.get("stats") or {}).get("snapshotDate"), "source": "analytics.pocket.network (public)",
        "suppliers": {"count": (sup.get("stats") or {}).get("totalSuppliers"), "unstaking": last.get("unstaking"),
                      "count_change_7d_pct": _pct(last.get("suppliers"), (d7 or {}).get("suppliers")), "count_change_30d_pct": _pct(last.get("suppliers"), (d30 or {}).get("suppliers"))},
        "supplier_stake_pokt": {"total": round(total_stake, 2), "avg_per_supplier": round(total_stake / float(last.get("suppliers") or 1), 2),
                                "change_7d_pct": _pct(last.get("stakedPokt"), (d7 or {}).get("stakedPokt")), "change_30d_pct": _pct(last.get("stakedPokt"), (d30 or {}).get("stakedPokt")),
                                "series_60d": [{"date": e.get("date", "")[:10], "stakedPokt": round(float(e.get("stakedPokt") or 0)), "suppliers": e.get("suppliers")} for e in evo]},
        "all_staked_pokt": {"total": round(float((staked[-1] if staked else {}).get("stakedPokt") or 0), 2), "note": "validators+suppliers+apps+gateways"},
        "concentration_top": (sup.get("concentration") or [])[:3],
        "settlement": {"claims_last_day": (claims[-1] if claims else {}).get("claims"), "proofs_last_day": (claims[-1] if claims else {}).get("proofs"),
                       "claimed_pokt_per_day_60d_avg": round(settled_per_day_pokt, 2), "relays_24h": (traffic.get("stats") or {}).get("relays24h"),
                       "relays_24h_change_pct": (traffic.get("stats") or {}).get("relays24hChange")},
        "observed_network_average_yield": {"annualised_ratio": round(yield_ratio, 5) if yield_ratio is not None else None,
                                           "annualised_pct": round(yield_ratio * 100, 3) if yield_ratio is not None else None,
                                           "supplier_share_annualised_pct": round(yield_ratio * 100 * SUPPLIER_SHARE, 3) if yield_ratio is not None else None,
                                           "supplier_share_factor": SUPPLIER_SHARE,
                                           "label": "OBSERVED_NETWORK_AVERAGE_NOT_FORECAST",
                                           "method": "sum(claimedUpokt per service, 60d) / days * 365 / total supplier stake = gross (what applications paid); "
                                                     "supplier_share applies tokenomics mint_ratio 0.975 x supplier distribution 0.79 (chain params 2026-09-19); before costs, before owner/operator split, not any single supplier's yield"},
    }
    _CACHE.update({"at": time.time(), "data": out})
    return out


def supplier_economics(client, operator, data_dir=None, head=None):
    rec = chain.supplier(client, operator)
    if not rec.get("found"):
        return {"schema": "pokt-supplier-economics/v1", "operator_address": operator, "supplier_found": False}
    net = None
    try:
        net = network_snapshot()
    except Exception as e:  # analytics outage must not break the chain facts
        net = {"error": str(e)[:120]}
    rows = []
    source = "NONE"
    nd = os.path.join(data_dir, "pokt", "settlements", "%s.ndjson" % operator) if data_dir else None
    if nd and os.path.isfile(nd):
        rows = _read_ndjson(nd)
        source = "COLLECTOR_NDJSON"
    now = datetime.datetime.now(UTC)
    owner = rec.get("owner_address")
    windows = {}
    for label, hours in (("24h", 24), ("7d", 168), ("30d", 720)):
        since = now - datetime.timedelta(hours=hours)
        sub = [r for r in rows if (_parse_iso(r.get("block_time_utc")) or datetime.datetime.min.replace(tzinfo=UTC)) >= since]
        agg = settlement.aggregate(sub, owner)
        windows[label] = {"settlements": agg["settlements"], "relays": agg["relays"], "reward_to_owner_pokt": agg["reward_pokt"], "by_service": agg["by_service"]}
    stake = float(rec.get("stake_pokt") or 0)
    r7 = float(windows["7d"]["reward_to_owner_pokt"] or 0)
    total_net = float(((net or {}).get("supplier_stake_pokt") or {}).get("total") or 0) if isinstance(net, dict) else 0.0
    return {
        "schema": "pokt-supplier-economics/v1", "operator_address": operator, "owner_address": owner, "supplier_found": True,
        "stake_pokt": rec.get("stake_pokt"), "services": rec.get("service_ids"), "unbonding": rec.get("unbonding"),
        "share_of_network_supplier_stake_pct": round(stake / total_net * 100, 4) if total_net else None,
        "windows": windows, "settlement_source": source,
        "observed_owner_yield_7d": {"ratio_of_stake": round(r7 / stake, 6) if stake else None,
                                    "annualised_pct_if_repeated": round(r7 / stake * 52 * 100, 3) if stake else None,
                                    "label": "OBSERVED_7D_EXTRAPOLATION_NOT_FORECAST", "before_costs": True},
        "network": {k: (net or {}).get(k) for k in ("as_of", "supplier_stake_pokt", "observed_network_average_yield", "settlement")} if isinstance(net, dict) else net,
        "not_computed": {"costs": None, "krw": None, "usd": None},
    }
