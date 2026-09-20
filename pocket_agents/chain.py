"""Typed read-only queries against Pocket MainNet (chain-id `pocket`, bech32 `pokt`).

Every function takes a Client and returns plain dicts; nothing here signs, broadcasts or stores.
Source of paths: https://docs.pocket.network/get-started/networks/ and the poktroll REST/gRPC
gateway routes observed live on 2026-09-19.
"""
import json
import re
import urllib.parse

CHAIN_ID = "pocket"
UPOKT = 1000000
ADDR_RE = re.compile(r"^pokt1[02-9ac-hj-np-z]{38}$")
VALOPER_RE = re.compile(r"^poktvaloper1[02-9ac-hj-np-z]{38}$")


def upokt_to_pokt(amount):
    """'15730upokt' | '15730' | 15730 -> Decimal-safe string in POKT (6 places)."""
    if amount is None:
        return None
    s = str(amount).replace("upokt", "").strip()
    if not re.match(r"^-?\d+$", s):
        return None
    neg = s.startswith("-")
    n = int(s.lstrip("-"))
    out = "%d.%06d" % (n // UPOKT, n % UPOKT)
    return "-" + out if neg else out


def upokt_int(amount):
    if amount is None:
        return None
    s = str(amount).replace("upokt", "").strip()
    return int(s) if re.match(r"^\d+$", s) else None


def is_address(a):
    return bool(a) and bool(ADDR_RE.match(a))


def is_valoper(a):
    return bool(a) and bool(VALOPER_RE.match(a))


# -- head / params --------------------------------------------------------------------------
def latest_block(c):
    d = c.rest("/cosmos/base/tendermint/v1beta1/blocks/latest")
    h = d["block"]["header"]
    if h.get("chain_id") != CHAIN_ID:
        raise ValueError("CHAIN_ID_MISMATCH %r" % h.get("chain_id"))
    return {"height": int(h["height"]), "time_utc": h["time"], "chain_id": h["chain_id"],
            "proposer_address": h.get("proposer_address")}


def block_header(c, height):
    d = c.rest("/cosmos/base/tendermint/v1beta1/blocks/%d" % int(height))
    h = d["block"]["header"]
    return {"height": int(h["height"]), "time_utc": h["time"], "chain_id": h["chain_id"]}


def params(c):
    out = {}
    for key, path in (("supplier", "/pokt-network/poktroll/supplier/params"),
                      ("shared", "/pokt-network/poktroll/shared/params"),
                      ("staking", "/cosmos/staking/v1beta1/params"),
                      ("tokenomics", "/pokt-network/poktroll/tokenomics/params"),
                      ("proof", "/pokt-network/poktroll/proof/params")):
        out[key] = c.rest(path).get("params")
    sup = out["supplier"] or {}
    shared = out["shared"] or {}
    staking = out["staking"] or {}
    derived = {
        "supplier_min_stake_pokt": upokt_to_pokt((sup.get("min_stake") or {}).get("amount")),
        "supplier_staking_fee_upokt": (sup.get("staking_fee") or {}).get("amount"),
        "blocks_per_session": int(shared.get("num_blocks_per_session", 0) or 0),
        "supplier_unbonding_sessions": int(shared.get("supplier_unbonding_period_sessions", 0) or 0),
        "validator_unbonding_seconds": int(str(staking.get("unbonding_time", "0s")).rstrip("s") or 0),
        "max_validators": int(staking.get("max_validators", 0) or 0),
        "compute_units_to_tokens_multiplier": shared.get("compute_units_to_tokens_multiplier"),
    }
    if derived["blocks_per_session"] and derived["supplier_unbonding_sessions"]:
        derived["supplier_unbonding_blocks"] = derived["blocks_per_session"] * derived["supplier_unbonding_sessions"]
    out["derived"] = derived
    return out


# -- accounts -------------------------------------------------------------------------------
def balance(c, address):
    d = c.rest("/cosmos/bank/v1beta1/balances/%s/by_denom?denom=upokt" % address)
    amt = (d.get("balance") or {}).get("amount", "0")
    return {"address": address, "upokt": amt, "pokt": upokt_to_pokt(amt)}


def delegations(c, address):
    d = c.rest("/cosmos/staking/v1beta1/delegations/%s?pagination.limit=100" % address)
    rows = []
    for r in d.get("delegation_responses", []):
        rows.append({"validator": r["delegation"]["validator_address"],
                     "shares": r["delegation"].get("shares"),
                     "pokt": upokt_to_pokt((r.get("balance") or {}).get("amount"))})
    return rows


def unbonding_delegations(c, address):
    d = c.rest("/cosmos/staking/v1beta1/delegators/%s/unbonding_delegations?pagination.limit=100" % address)
    rows = []
    for r in d.get("unbonding_responses", []):
        for e in r.get("entries", []):
            rows.append({"validator": r["validator_address"], "completion_time": e.get("completion_time"),
                         "pokt": upokt_to_pokt(e.get("balance"))})
    return rows


def delegation_rewards(c, address):
    d = c.rest("/cosmos/distribution/v1beta1/delegators/%s/rewards" % address)
    total = "0"
    for t in d.get("total", []):
        if t.get("denom") == "upokt":
            total = str(t.get("amount", "0")).split(".")[0]
    return {"total_pokt": upokt_to_pokt(total), "by_validator": len(d.get("rewards", []))}


# -- suppliers ------------------------------------------------------------------------------
def supplier(c, operator_address):
    """Supplier record or {'found': False}. Never raises on 404."""
    from .client import HttpFailure
    try:
        d = c.rest("/pokt-network/poktroll/supplier/supplier/%s" % operator_address)
    except HttpFailure as e:
        if e.status in (400, 404, 500):
            return {"found": False, "operator_address": operator_address, "error": str(e)[:120]}
        raise
    s = d.get("supplier") or {}
    if not s:
        return {"found": False, "operator_address": operator_address}
    services = []
    for sv in s.get("services", []):
        services.append({
            "service_id": sv.get("service_id"),
            "endpoints": [{"url": e.get("url"), "rpc_type": e.get("rpc_type")} for e in sv.get("endpoints", [])],
            "rev_share": [{"address": r.get("address"), "percent": r.get("rev_share_percentage")} for r in sv.get("rev_share", [])],
        })
    unstake_h = int(s.get("unstake_session_end_height", 0) or 0)
    return {
        "found": True,
        "operator_address": s.get("operator_address"),
        "owner_address": s.get("owner_address"),
        "stake_pokt": upokt_to_pokt((s.get("stake") or {}).get("amount")),
        "stake_upokt": (s.get("stake") or {}).get("amount"),
        "services": services,
        "service_ids": [sv.get("service_id") for sv in s.get("services", [])],
        "unstake_session_end_height": unstake_h,
        "unbonding": unstake_h > 0,
        "service_config_history_len": len(s.get("service_config_history", []) or []),
    }


def service(c, service_id):
    """On-chain service record (owner, CU/relay, card present) or {'found': False}."""
    from .client import HttpFailure
    try:
        d = c.rest("/pokt-network/poktroll/service/service/%s" % service_id)
    except HttpFailure as e:
        if e.status in (400, 404, 500):
            return {"found": False, "service_id": service_id}
        raise
    s = d.get("service") or {}
    if not s:
        return {"found": False, "service_id": service_id}
    card = (s.get("metadata") or {}).get("card")
    return {"found": True, "service_id": s.get("id"), "name": s.get("name"), "compute_units_per_relay": s.get("compute_units_per_relay"),
            "owner_address": s.get("owner_address"), "card_bytes": len(card) if card else 0}


def supplier_count(c):
    d = c.rest("/pokt-network/poktroll/supplier/supplier?pagination.limit=1&pagination.count_total=true")
    return int((d.get("pagination") or {}).get("total", 0) or 0)


# -- validators -----------------------------------------------------------------------------
def validators(c):
    d = c.rest("/cosmos/staking/v1beta1/validators?pagination.limit=300")
    rows = []
    for v in d.get("validators", []):
        rate = (v.get("commission") or {}).get("commission_rates", {}).get("rate")
        rows.append({
            "operator_address": v.get("operator_address"),
            "moniker": (v.get("description") or {}).get("moniker"),
            "website": (v.get("description") or {}).get("website"),
            "status": v.get("status"),
            "bonded": v.get("status") == "BOND_STATUS_BONDED",
            "jailed": bool(v.get("jailed")),
            "tokens_pokt": upokt_to_pokt(v.get("tokens")),
            "tokens_upokt": v.get("tokens"),
            "commission_rate": ("%.4f" % float(rate)) if rate is not None else None,
            "max_commission_rate": (v.get("commission") or {}).get("commission_rates", {}).get("max_rate"),
        })
    rows.sort(key=lambda r: -int(r["tokens_upokt"] or 0))
    return rows


def validator_set_summary(rows, max_validators):
    bonded = [r for r in rows if r["bonded"]]
    total = sum(int(r["tokens_upokt"] or 0) for r in bonded)
    cutoff = bonded[max_validators - 1]["tokens_pokt"] if max_validators and len(bonded) >= max_validators else (
        bonded[-1]["tokens_pokt"] if bonded else None)
    return {"bonded_count": len(bonded), "max_validators": max_validators,
            "total_bonded_pokt": upokt_to_pokt(total), "active_set_cutoff_pokt": cutoff,
            "jailed_count": sum(1 for r in rows if r["jailed"])}


# -- RPC (CometBFT) -------------------------------------------------------------------------
def block_search(c, query, page=1, per_page=100):
    q = urllib.parse.quote(json.dumps(query), safe="")
    d = c.rpc("/block_search?query=%s&page=%d&per_page=%d&order_by=%s" % (q, page, per_page, urllib.parse.quote('"asc"')))
    res = d.get("result") or {}
    blocks = []
    for b in res.get("blocks", []):
        h = (b.get("block") or {}).get("header") or {}
        if h.get("chain_id") != CHAIN_ID:
            raise ValueError("CHAIN_ID_MISMATCH in block_search")
        blocks.append({"height": int(h["height"]), "time_utc": h.get("time")})
    return {"total": int(res.get("total_count", "0") or 0), "blocks": blocks}


def block_results(c, height):
    d = c.rpc("/block_results?height=%d" % int(height))
    res = d.get("result") or {}
    if str(res.get("height")) != str(height):
        raise ValueError("BLOCK_RESULTS_HEIGHT_MISMATCH")
    return res


def settlement_query(operator_address, lower_exclusive, upper_inclusive, service_id=None):
    """CometBFT query for settlement blocks of one supplier. Attribute values are JSON strings,
    hence the embedded double quotes (verified against the live index in the C track)."""
    q = ("block.height > %d AND block.height <= %d AND pocket.tokenomics.EventClaimSettled.supplier_operator_address = '%s'"
         % (int(lower_exclusive), int(upper_inclusive), json.dumps(operator_address)))
    if service_id:
        q += " AND pocket.tokenomics.EventClaimSettled.service_id = '%s'" % json.dumps(service_id)
    return q
