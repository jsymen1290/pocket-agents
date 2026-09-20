"""POKT Opportunity / Decision Agent (master map agent #3).

Deterministic layer (this module): capital KPIs, per-track impact triggers from observed facts, next user
action, decision log. Optional narration is done by the daily brief job (number-guarded). It never moves
assets and never treats an announcement as revenue.

Inputs : STATE.json (chain facts), ALERTS.json, surveillance EVENTS/REVIEWS, capital.local.json (user-declared
         total and intended allocation), decisions.ndjson (RECORD_DECISION history)
Outputs: <data>/pokt/DECISION.json  with header KPIs
         TOTAL_POKT / ALLOCATED / RESERVED / ACTUALLY_STAKED / ACTUAL_REWARD_RECEIVED / ACTUAL_CASH_REALIZED / NEXT_USER_ACTION
         and impacts per track {A,B,C,D} = {level, reasons[], evidence[]}.
"""
import datetime
import json
import os

from .chain import upokt_to_pokt

UTC = datetime.timezone.utc
DEFAULT_CAPITAL = {"schema": "pokt-capital/v1", "total_pokt": 5000000, "total_evidence": "USER_DECLARED", "allocation_intent_pokt": {"A": 0, "B": 59500, "C": 0, "D": 6000},
                   "reserve_pokt": 0, "note": "Set with `pokt capital --total N --allocate A=..,B=..,C=..,D=.. --reserve N`. Intent, not execution."}


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _ndjson(path, limit=None):
    rows = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows[-limit:] if limit else rows


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def load_capital(data_dir):
    cap = dict(DEFAULT_CAPITAL)
    cap.update(_read(os.path.join(data_dir, "pokt", "capital.local.json"), {}) or {})
    return cap


def save_capital(data_dir, total=None, allocate=None, reserve=None):
    cap = load_capital(data_dir)
    if total is not None:
        cap["total_pokt"] = int(total)
    if allocate:
        cap["allocation_intent_pokt"] = dict(cap.get("allocation_intent_pokt") or {}, **{k: int(v) for k, v in allocate.items()})
    if reserve is not None:
        cap["reserve_pokt"] = int(reserve)
    cap["updated_at_utc"] = datetime.datetime.now(UTC).isoformat()
    p = os.path.join(data_dir, "pokt", "capital.local.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cap, fh, ensure_ascii=False, indent=2)
    return cap


def record_decision(data_dir, track, text, decided_by="user", refs=None):
    """RECORD_DECISION: append-only human decision log (never edits prior rows)."""
    row = {"at_utc": datetime.datetime.now(UTC).isoformat(), "track": track, "decision": text, "decided_by": decided_by, "refs": refs or []}
    p = os.path.join(data_dir, "pokt", "decisions.ndjson")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def compute(data_dir, state=None, alerts=None):
    root = os.path.join(data_dir, "pokt")
    state = state or _read(os.path.join(root, "STATE.json"), {}) or {}
    alerts = alerts if alerts is not None else ((_read(os.path.join(root, "ALERTS.json"), {}) or {}).get("active", []))
    cap = load_capital(data_dir)
    u = state.get("user") or {}
    services = state.get("services") or []
    suppliers = state.get("suppliers") or []
    owner = u.get("owner_address")
    # -- KPIs (chain-observed where possible; null when unobservable) --
    delegated = _f(u.get("delegated_pokt")) if u.get("configured") else 0.0
    owned_stake = sum(_f(s.get("stake_pokt")) for s in suppliers if s.get("found") and owner and s.get("owner_address") == owner)
    reward_settle = 0.0
    for s in suppliers:
        if s.get("found") and owner and s.get("owner_address") == owner:
            reward_settle += _f(((s.get("rewards") or {}).get("7d") or {}).get("reward_pokt"))
    reward_deleg = _f((u.get("rewards") or {}).get("total_pokt")) if u.get("configured") else 0.0
    header = {
        "TOTAL_POKT": {"value": cap.get("total_pokt"), "evidence": cap.get("total_evidence", "USER_DECLARED")},
        "ALLOCATED": {"value": sum(int(v) for v in (cap.get("allocation_intent_pokt") or {}).values()), "by_track": cap.get("allocation_intent_pokt"), "evidence": "USER_INTENT"},
        "RESERVED": {"value": cap.get("reserve_pokt", 0), "evidence": "USER_INTENT"},
        "ACTUALLY_STAKED": {"value": round(delegated + owned_stake, 6), "delegated_pokt": round(delegated, 6), "owned_supplier_stake_pokt": round(owned_stake, 6),
                            "evidence": "CHAIN" if u.get("configured") else "WALLET_NOT_CONFIGURED"},
        "ACTUAL_REWARD_RECEIVED": {"value": round(reward_settle + reward_deleg, 6), "supplier_settlements_7d_pokt": round(reward_settle, 6),
                                   "delegation_rewards_unclaimed_pokt": round(reward_deleg, 6), "evidence": "CHAIN_7D_WINDOW" if u.get("configured") else "WALLET_NOT_CONFIGURED"},
        "ACTUAL_CASH_REALIZED": {"value": None, "evidence": "NOT_OBSERVABLE_ONCHAIN (exchange sells are user-reported)"},
        "WALLET_LIQUID": {"value": _f(u.get("balance_pokt")) if u.get("configured") else None, "evidence": "CHAIN"},
    }
    # -- impacts --
    impacts = {t: {"level": "NONE", "reasons": [], "evidence": []} for t in ("A", "B", "C", "D")}

    def hit(track, level, reason, ev=None):
        order = {"NONE": 0, "INFO": 1, "REVIEW": 2, "HIGH": 3}
        if order[level] > order[impacts[track]["level"]]:
            impacts[track]["level"] = level
        impacts[track]["reasons"].append(reason)
        if ev:
            impacts[track]["evidence"].append(ev)

    for a in alerts:
        code, tr = a.get("code"), a.get("track")
        lvl = {"HIGH": "HIGH", "WARN": "REVIEW", "INFO": "INFO"}.get(a.get("severity"), "INFO")
        tracks = ["A"] if tr == "A" else (["D"] if tr == "D" else (["B", "C"] if tr in ("B_or_C", "C_REFERENCE") else (["A", "B", "C", "D"] if tr == "ALL" else [])))
        if code == "SUPPLIER_NOT_FOUND" and a.get("subject") in (u.get("operator_addresses") or []) or (code == "SUPPLIER_NOT_FOUND" and tr == "B_or_C"):
            hit("B", "INFO", "내 operator가 아직 supplier로 스테이크되지 않음 (계획 단계)", a.get("subject"))
            hit("C", "INFO", "동일", a.get("subject"))
            continue
        for t in tracks:
            hit(t, lvl, "%s: %s" % (code, a.get("message")), a.get("subject"))
    for r in _ndjson(os.path.join(root, "surveillance", "REVIEWS.ndjson"), limit=30):
        if r.get("classification") in ("REVIEW", "STRATEGY_CHANGING"):
            for t in r.get("tracks") or []:
                hit(t, "HIGH" if r["classification"] == "STRATEGY_CHANGING" else "REVIEW", "관찰 변화 %s: %s" % (r.get("source_id"), r.get("why_ko", "")[:120]), r.get("event_id"))
    vsum = ((state.get("validators") or {}).get("summary") or {})
    if vsum.get("jailed_count", 0) > 0 and any(c.get("jailed") for c in (state.get("validators") or {}).get("candidates", [])):
        hit("A", "HIGH", "후보 검증자 jailed", "validators.candidates")
    if services and all(s.get("found") for s in services):
        hit("D", "INFO", "서비스 %s 온체인 등록 유지" % ",".join(s["service_id"] for s in services), "services")
    # -- next user action (ordered gates) --
    if not u.get("configured"):
        nxt = "자가보관 지갑 생성 후 `pokt set-user --owner` 등록"
    elif services and not any(s.get("found") and s.get("owner_address") == owner for s in suppliers):
        need = 59500 + 500
        liquid = _f(u.get("balance_pokt"))
        nxt = ("owner-ops에 %s POKT 추가 입금 후 stake-supplier 실행 (현재 유동 %.2f)" % ("{:,}".format(int(need - liquid)), liquid)) if liquid < need else "stake-supplier 실행(자금 충족) → RelayMiner 기동"
    elif any(s.get("found") and s.get("owner_address") == owner and s.get("settlement_rows_total", 0) == 0 for s in suppliers):
        nxt = "RelayMiner 기동·터널 :8545 전환 후 첫 정산 확인"
    else:
        nxt = "정산 관측 지속; A 위임 지갑(Keplr+Ledger) 결정"
    header["NEXT_USER_ACTION"] = {"value": nxt}
    decisions = _ndjson(os.path.join(root, "decisions.ndjson"), limit=10)
    out = {"schema": "pokt-decision/v1", "generated_at_utc": datetime.datetime.now(UTC).isoformat(), "state_run_id": state.get("run_id"), "header": header,
           "impacts": impacts, "capital_allocation_impact": {"value": None, "reason": "no observed change in fees/min_stake/APR inputs; allocation stays user intent"},
           "new_opportunities": [], "codex_change_required": any(i["level"] == "HIGH" for i in impacts.values()),
           "user_action_required": header["NEXT_USER_ACTION"]["value"], "recent_decisions": decisions,
           "boundary": "A single announcement never reallocates capital; evidence of demand/revenue/cost/protocol change is required."}
    params = (state.get("params") or {}).get("derived") or {}
    if params.get("supplier_min_stake_pokt") and _f(params["supplier_min_stake_pokt"]) != 59500.0:
        out["capital_allocation_impact"] = {"value": "REVIEW", "reason": "supplier min_stake changed to %s" % params["supplier_min_stake_pokt"]}
        hit("B", "REVIEW", "min_stake 변경", "params")
        hit("C", "REVIEW", "min_stake 변경", "params")
    p = os.path.join(root, "DECISION.json")
    os.makedirs(root, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return out
