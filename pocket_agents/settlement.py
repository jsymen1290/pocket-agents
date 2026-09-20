"""EventClaimSettled extraction: what a supplier was actually paid, per session and service.

Attribute names verified on MainNet block 920873 (2026-09-19): supplier_operator_address,
supplier_owner_address, service_id, session_id, session_end_block_height, claimed_upokt,
settled_upokt, minted_upokt, num_relays, num_claimed_compute_units, claim_proof_status_int,
proof_requirement_int, reward_distribution (JSON object address -> "Nupokt").
A settlement is counted once per (height, session_id, service_id, operator); the recipient split
comes from reward_distribution only. No estimation, no price, no cost.
"""
import hashlib
import json

from .chain import upokt_int, upokt_to_pokt

CLAIM_SETTLED = "pocket.tokenomics.EventClaimSettled"


def _attrs(event):
    out = {}
    for a in event.get("attributes", []) or []:
        k, v = a.get("key"), a.get("value")
        if k is None:
            continue
        if isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


def _json_attr(v):
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def extract_claims(block_results, height, operator_address=None, block_time_utc=None):
    """All EventClaimSettled rows in one block (optionally filtered to one operator)."""
    events = block_results.get("finalize_block_events") or []
    rows = []
    for index, ev in enumerate(events):
        if ev.get("type") != CLAIM_SETTLED:
            continue
        a = _attrs(ev)
        op = a.get("supplier_operator_address") or a.get("supplier_operator_addr")
        if operator_address and op != operator_address:
            continue
        dist = _json_attr(a.get("reward_distribution")) or {}
        dist_pokt = {}
        for addr, amt in dist.items():
            n = upokt_int(amt)
            if n is not None:
                dist_pokt[addr] = n
        owner = a.get("supplier_owner_address") or a.get("supplier_owner_addr")
        row = {
            "height": int(height),
            "block_time_utc": block_time_utc,
            "event_index": index,
            "session_id": a.get("session_id"),
            "session_end_block_height": int(a.get("session_end_block_height") or 0) or None,
            "service_id": a.get("service_id"),
            "application_address": a.get("application_address"),
            "supplier_operator_address": op,
            "supplier_owner_address": owner,
            "num_relays": int(a.get("num_relays") or 0),
            "num_claimed_compute_units": int(a.get("num_claimed_compute_units") or 0),
            "claimed_upokt": upokt_int(a.get("claimed_upokt")),
            "settled_upokt": upokt_int(a.get("settled_upokt")),
            "minted_upokt": upokt_int(a.get("minted_upokt")),
            "claim_proof_status_int": a.get("claim_proof_status_int"),
            "proof_requirement_int": a.get("proof_requirement_int"),
            "reward_to_owner_upokt": dist_pokt.get(owner, 0) if owner else 0,
            "reward_to_operator_upokt": dist_pokt.get(op, 0) if op else 0,
            "reward_distribution_upokt": dist_pokt,
            "reward_recipients": len(dist_pokt),
        }
        row["reward_to_owner_pokt"] = upokt_to_pokt(row["reward_to_owner_upokt"])
        row["reward_to_operator_pokt"] = upokt_to_pokt(row["reward_to_operator_upokt"])
        canon = json.dumps(ev, sort_keys=True, separators=(",", ":")).encode("utf-8")
        row["event_sha256"] = hashlib.sha256(canon).hexdigest()
        row["key"] = "%d|%s|%s|%s" % (row["height"], row["session_id"], row["service_id"], op)
        rows.append(row)
    return rows


def aggregate(rows, address, since_height=None):
    """Sum rewards paid to `address` (as owner or any recipient) across rows."""
    total = 0
    relays = 0
    by_service = {}
    count = 0
    last_height = None
    for r in rows:
        if since_height is not None and r["height"] <= since_height:
            continue
        amt = (r.get("reward_distribution_upokt") or {}).get(address, 0)
        total += amt
        relays += r.get("num_relays", 0)
        count += 1
        s = r.get("service_id") or "?"
        by_service.setdefault(s, {"settlements": 0, "relays": 0, "upokt": 0})
        by_service[s]["settlements"] += 1
        by_service[s]["relays"] += r.get("num_relays", 0)
        by_service[s]["upokt"] += amt
        if last_height is None or r["height"] > last_height:
            last_height = r["height"]
    for s in by_service.values():
        s["pokt"] = upokt_to_pokt(s["upokt"])
    return {"settlements": count, "relays": relays, "reward_upokt": total, "reward_pokt": upokt_to_pokt(total),
            "by_service": by_service, "last_height": last_height}
