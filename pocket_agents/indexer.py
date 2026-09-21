"""Public Pocket indexer (data.pocket.network GraphQL) as the primary settlement source.

Why this exists: the RPC path fetched block_results one block at a time and was capped, so a 24 h question
answered with a fraction of the settlements (measured 2026-09-22: 22.9 of 119.2 POKT, 81% missing). PNF
reported the same symptom. The indexer returns every EventClaimSettled row for an operator and a block range
in one paginated query, so coverage is complete and the answer is correct.

Rows come back in exactly the shape settlement.extract_claims() produces, so callers and aggregate() are
unchanged. Evidence differs by source and says so: the RPC path hashes the raw finalize-block event, this
path hashes the canonical indexer node. Both are recomputable by the caller from the same public source.
"""
import hashlib
import json
import os
import urllib.request

ENDPOINT = os.environ.get("POKT_INDEXER_URL", "https://data.pocket.network/graphql")
UA = "llm-runtime-pokt-indexer/0.1 (read-only)"
PAGE = 200
MAX_ROWS = 5000
TIMEOUT = 25
HASH_BASIS = "indexer_event_node/v1"

FIELDS = ("id blockId supplierId supplierOwnerId applicationId serviceId sessionId sessionEndHeight "
          "numRelays numClaimedComputedUnits claimedAmount settledAmount mintedAmount "
          "proofRequirement proofValidationStatus block{timestamp} "
          "modToAcctTransfers{nodes{recipientId amount denom}}")


class IndexerUnavailable(Exception):
    pass


def enabled():
    return os.environ.get("POKT_INDEXER", "1").strip() not in ("0", "false", "no")


def _post(query, timeout=TIMEOUT):
    req = urllib.request.Request(ENDPOINT, data=json.dumps({"query": query}).encode("utf-8"),
                                 headers={"content-type": "application/json", "accept": "application/json", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read(16 * 1024 * 1024).decode("utf-8"))
    if body.get("errors"):
        raise IndexerUnavailable("GRAPHQL_ERROR: %s" % json.dumps(body["errors"])[:160])
    return body.get("data") or {}


post = _post  # injectable for tests


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _row(node):
    """One indexer node -> the same row shape settlement.extract_claims() returns."""
    from .chain import upokt_to_pokt
    height = _int(node.get("blockId"))
    eid = node.get("id") or ""
    try:
        event_index = int(eid.rsplit("-", 1)[-1])
    except (ValueError, AttributeError):
        event_index = None
    dist = {}
    for t in ((node.get("modToAcctTransfers") or {}).get("nodes") or []):
        if (t.get("denom") or "upokt") != "upokt":
            continue
        addr = t.get("recipientId")
        if addr:
            dist[addr] = dist.get(addr, 0) + _int(t.get("amount"))
    owner = node.get("supplierOwnerId")
    op = node.get("supplierId")
    row = {
        "height": height,
        "block_time_utc": ((node.get("block") or {}).get("timestamp")),
        "event_index": event_index,
        "session_id": node.get("sessionId"),
        "session_end_block_height": _int(node.get("sessionEndHeight")) or None,
        "service_id": node.get("serviceId"),
        "application_address": node.get("applicationId"),
        "supplier_operator_address": op,
        "supplier_owner_address": owner,
        "num_relays": _int(node.get("numRelays")),
        "num_claimed_compute_units": _int(node.get("numClaimedComputedUnits")),
        "claimed_upokt": _int(node.get("claimedAmount"), None),
        "settled_upokt": _int(node.get("settledAmount"), None),
        "minted_upokt": _int(node.get("mintedAmount"), None),
        "claim_proof_status_int": node.get("proofValidationStatus"),
        "proof_requirement_int": node.get("proofRequirement"),
        "reward_to_owner_upokt": dist.get(owner, 0) if owner else 0,
        "reward_to_operator_upokt": dist.get(op, 0) if op else 0,
        "reward_distribution_upokt": dist,
        "reward_recipients": len(dist),
        "evidence_source": "INDEXER",
        "hash_basis": HASH_BASIS,
        "indexer_event_id": eid or None,
    }
    row["reward_to_owner_pokt"] = upokt_to_pokt(row["reward_to_owner_upokt"])
    row["reward_to_operator_pokt"] = upokt_to_pokt(row["reward_to_operator_upokt"])
    canon = json.dumps(node, sort_keys=True, separators=(",", ":")).encode("utf-8")
    row["event_sha256"] = hashlib.sha256(canon).hexdigest()
    row["key"] = "%d|%s|%s|%s" % (height, row["session_id"], row["service_id"], op)
    return row


def settlements(operator, lower_exclusive, upper_inclusive, max_rows=MAX_ROWS):
    """Every EventClaimSettled for `operator` in (lower, upper]. Returns (rows, complete).

    complete is False only when max_rows was reached, which the caller must report as partial coverage.
    Raises IndexerUnavailable on transport or query failure so the caller can fall back to the RPC scan.
    """
    if not enabled():
        raise IndexerUnavailable("DISABLED")
    lo, hi = int(lower_exclusive), int(upper_inclusive)
    rows, offset = [], 0
    while len(rows) < max_rows:
        q = ('{eventClaimSettleds(filter:{supplierId:{equalTo:"%s"},blockId:{greaterThan:"%d",lessThanOrEqualTo:"%d"}},'
             'first:%d,offset:%d,orderBy:BLOCK_ID_ASC){nodes{%s}}}') % (operator, lo, hi, PAGE, offset, FIELDS)
        try:
            data = post(q)
        except IndexerUnavailable:
            raise
        except Exception as e:
            raise IndexerUnavailable("%s: %s" % (type(e).__name__, str(e)[:120]))
        nodes = ((data.get("eventClaimSettleds") or {}).get("nodes")) or []
        rows.extend(_row(n) for n in nodes)
        if len(nodes) < PAGE:
            return rows, True
        offset += PAGE
    return rows[:max_rows], False
