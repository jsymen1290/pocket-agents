"""Operator watch (pocket-operator-watch, GPT rank-1 reframe 2026-09-20) - second tool bundle inside pokt-settlement-agent-v1.

Question it answers: "Has something happened that could stop or change my income, and what should I check first?"
Three checks, all from public chain data, facts separated from judgments:
  1. settlement gap      - last settled claim vs sessions elapsed; classifies WITHIN_WINDOW / NO_SETTLEMENT_IN_SCAN /
                           SCAN_RANGE_INSUFFICIENT / NEVER_OBSERVED. Never says "missing" without a scan that covers it.
  2. config changes      - on-chain service_config_history: what changed (services, endpoints, rev_share) and at which height.
  3. fee balance + claims - operator balance vs a caller threshold, pending claims/proofs on chain, unbonding flag.
Judgments are booleans with explicit "not confirmed" defaults: principal_loss_confirmed=False, settlement_missing_confirmed
only when a scan covering >= 3 full sessions shows relays were claimed but nothing settled (which needs claims data), else False.
"""
import datetime
import os

from . import chain
from .collect import _read_ndjson, _parse_iso
from .settlement import aggregate

UTC = datetime.timezone.utc
DEFAULT_MIN_FEE_UPOKT = 1_000_000  # 1 POKT; the miner's own balance monitor default


def _config_history(c, operator):
    d = c.rest("/pokt-network/poktroll/supplier/supplier/%s" % operator)
    s = d.get("supplier") or {}
    hist = []
    for h in s.get("service_config_history", []) or []:
        sv = h.get("service") or {}
        hist.append({"activation_height": int(h.get("activation_height") or 0), "service_id": sv.get("service_id"),
                     "endpoints": [(e.get("url"), e.get("rpc_type")) for e in sv.get("endpoints", [])],
                     "rev_share": [(r.get("address"), str(r.get("rev_share_percentage"))) for r in sv.get("rev_share", [])]})
    hist.sort(key=lambda h: (h["activation_height"], h["service_id"] or ""))
    return hist, s


def config_changes(hist, head):
    """Group history by service; diff consecutive entries; flag entries not yet active."""
    by = {}
    for h in hist:
        by.setdefault(h["service_id"], []).append(h)
    changes = []
    for sid, rows in by.items():
        for prev, cur in zip(rows, rows[1:]):
            diff = []
            if set(prev["endpoints"]) != set(cur["endpoints"]):
                diff.append({"field": "endpoints", "before": prev["endpoints"], "after": cur["endpoints"]})
            if set(prev["rev_share"]) != set(cur["rev_share"]):
                diff.append({"field": "rev_share", "before": prev["rev_share"], "after": cur["rev_share"]})
            if diff:
                changes.append({"service_id": sid, "activation_height": cur["activation_height"], "active": head >= cur["activation_height"], "changes": diff})
        first = rows[0]
        changes.append({"service_id": sid, "activation_height": first["activation_height"], "active": head >= first["activation_height"], "changes": [{"field": "service_added"}]})
    changes.sort(key=lambda x: x["activation_height"])
    return changes


def pending_claims(c, operator):
    out = {}
    for kind in ("claim", "proof"):
        try:
            d = c.rest("/pokt-network/poktroll/proof/%s?supplier_operator_address=%s&pagination.limit=50" % (kind, operator))
            rows = d.get(kind + "s") or []
            out[kind + "s_pending"] = len(rows)
            out[kind + "_sessions"] = [(r.get("session_header") or {}).get("session_id", "")[:12] for r in rows[:10]]
        except Exception as e:  # LCD lists a claim only until it settles; absence is normal
            out[kind + "s_pending"] = None
            out[kind + "_status"] = "UNAVAILABLE"
    return out


def operator_watch(c, operator, data_dir=None, min_fee_upokt=DEFAULT_MIN_FEE_UPOKT, hours=24):
    if not chain.is_address(operator):
        raise ValueError("operator must be pokt1...")
    head = chain.latest_block(c)
    head_h = int(head.get("height") or 0)
    params = chain.params(c)
    blocks_per_session = int(((params.get("shared") or {}).get("num_blocks_per_session")) or 20)
    rec = chain.supplier(c, operator)
    facts = {"head_height": head_h, "head_time_utc": head.get("time_utc"), "blocks_per_session": blocks_per_session}
    if not rec.get("found"):
        return {"schema": "pokt-operator-watch/v1", "operator_address": operator, "supplier_found": False, "facts": facts,
                "judgments": {"principal_loss_confirmed": False, "settlement_missing_confirmed": False, "operator_attention_needed": True},
                "check_first": ["supplier record not found on chain: verify the operator address or whether the stake was withdrawn"]}
    hist, raw = _config_history(c, operator)
    changes = config_changes(hist, head_h)
    bal = chain.balance(c, operator)
    bal_upokt = int((bal or {}).get("upokt") or (bal or {}).get("amount") or 0) if isinstance(bal, dict) else int(bal or 0)
    claims = pending_claims(c, operator)
    # settlement gap from the collector's ndjson (watched operators) or none
    rows, source = [], "NONE"
    nd = os.path.join(data_dir, "pokt", "settlements", "%s.ndjson" % operator) if data_dir else None
    if nd and os.path.isfile(nd):
        rows = _read_ndjson(nd)
        source = "COLLECTOR_NDJSON"
    elif data_dir:  # watched by the collector but no settlement file yet (no settlement ever observed)
        try:
            import json
            st = json.load(open(os.path.join(data_dir, "pokt", "STATE.json"), encoding="utf-8"))
            if any((s.get("operator_address") or s.get("operator")) == operator for s in (st.get("suppliers") or [])):
                source = "COLLECTOR_WATCHED_NO_SETTLEMENT_YET"
        except (OSError, ValueError, AttributeError):
            pass
    last = max((int(r.get("height") or 0) for r in rows), default=0)
    last_time = max((r.get("block_time_utc") or "" for r in rows), default=None) or None
    scan_blocks = None
    if rows:
        scan_blocks = head_h - min(int(r.get("height") or 0) for r in rows)
    sessions_since = (head_h - last) / float(blocks_per_session) if last else None
    if not rows:
        gap_class = "NEVER_OBSERVED" if source == "NONE" else ("NO_SETTLEMENT_OBSERVED_YET" if source == "COLLECTOR_WATCHED_NO_SETTLEMENT_YET" else "NO_SETTLEMENT_IN_SCAN")
    elif sessions_since is not None and sessions_since <= 3:
        gap_class = "WITHIN_WINDOW"
    elif scan_blocks is not None and scan_blocks < 3 * blocks_per_session:
        gap_class = "SCAN_RANGE_INSUFFICIENT"
    else:
        gap_class = "NO_SETTLEMENT_IN_SCAN"
    now = datetime.datetime.now(UTC)
    since = now - datetime.timedelta(hours=hours)
    recent = [r for r in rows if (_parse_iso(r.get("block_time_utc")) or datetime.datetime.min.replace(tzinfo=UTC)) >= since]
    agg = aggregate(recent, rec.get("owner_address"))
    recent_change = [ch for ch in changes if ch["activation_height"] >= head_h - 3 * blocks_per_session * 24]  # ~ last 24h of sessions
    fee_low = bal_upokt < int(min_fee_upokt)
    attention = fee_low or rec.get("unbonding") or gap_class in ("NO_SETTLEMENT_IN_SCAN",) or any(not ch["active"] for ch in changes)
    check_first = []
    if fee_low:
        check_first.append("operator balance %d upokt is below your threshold %d upokt: claim/proof submissions need gas" % (bal_upokt, int(min_fee_upokt)))
    if rec.get("unbonding"):
        check_first.append("supplier is unbonding (unstake_session_end_height %s): no new sessions will be assigned" % rec.get("unstake_session_end_height"))
    for ch in changes:
        if not ch["active"]:
            check_first.append("service config for %s activates at %d (in %d blocks); until then the previous config serves" % (ch["service_id"], ch["activation_height"], ch["activation_height"] - head_h))
    if gap_class == "NO_SETTLEMENT_IN_SCAN":
        check_first.append("no settlement for %.1f sessions within a scan of %s blocks: check relays served (RelayMiner logs) before treating this as missing income" % (sessions_since or 0, scan_blocks))
    if gap_class == "NEVER_OBSERVED":
        check_first.append("this operator is not in the collector watch list; settlement history is not scanned here (use POST /v1/settlement with a block range)")
    if source == "COLLECTOR_WATCHED_NO_SETTLEMENT_YET":
        check_first.append("watched since the collector started but no settlement has been observed yet: expected until the first served session settles")
    if not check_first:
        check_first.append("no operator action indicated by these checks")
    return {
        "schema": "pokt-operator-watch/v1", "operator_address": operator, "owner_address": rec.get("owner_address"), "supplier_found": True,
        "facts": dict(facts, stake_upokt=rec.get("stake_upokt"), services=rec.get("service_ids"), unbonding=rec.get("unbonding"),
                      operator_balance_upokt=bal_upokt, fee_threshold_upokt=int(min_fee_upokt),
                      last_settlement={"height": last or None, "block_time_utc": last_time, "sessions_since": round(sessions_since, 2) if sessions_since is not None else None,
                                       "scan_blocks": scan_blocks, "source": source},
                      settlements_last_hours={"hours": hours, "count": agg["settlements"], "relays": agg["relays"], "reward_to_owner_pokt": agg["reward_pokt"]},
                      pending=claims, config_history_entries=len(hist)),
        "config_changes": changes, "recent_config_changes": recent_change,
        "gap_classification": gap_class,
        "judgments": {"principal_loss_confirmed": False, "settlement_missing_confirmed": False, "fee_balance_below_threshold": fee_low,
                      "config_change_pending_activation": any(not ch["active"] for ch in changes), "operator_attention_needed": bool(attention)},
        "check_first": check_first,
        "not_verified": ["relays actually served (only the RelayMiner knows)", "gateway scoring of this supplier", "off-chain costs"],
        "method": "facts from LCD supplier/balance/claim/proof queries and the collector's settlement ndjson; judgments are rules over those facts; nothing is estimated",
    }
