"""One bounded collection cycle for the POKT tracks.

  A Validator        : bonded set, active-set cutoff, candidate validators (jailed/commission/rank)
  B Supplier capital : supplier records whose owner is the user's address (once known), rev_share split
  C Direct supplier  : per watched supplier: stake, services, unbonding flag, EventClaimSettled scan
                       (cursor-based, bounded) -> settlements ndjson + 24h/7d aggregates
  D AI/API           : nothing here; the daily brief job reads STATE.json

Outputs under <data_dir>/pokt/:
  STATE.json, ALERTS.json, STATUS.md, cursors.json, history/<date>.ndjson,
  settlements/<operator>.ndjson, receipts/<run_id>.json, lock
Nothing is deleted or overwritten except STATE/ALERTS/STATUS/cursors (atomic replace).
"""
import datetime
import json
import os
import socket
import time
import uuid

from . import chain, settlement
from .client import BudgetExceeded, Client, HttpFailure

SCHEMA = "llm-runtime-pokt-state/v1"
KST = datetime.timezone(datetime.timedelta(hours=9))


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if "." in s:  # trim nanoseconds to microseconds for fromisoformat
        head, tail = s.split(".", 1)
        i = 0
        while i < len(tail) and tail[i].isdigit():
            i += 1
        s = "%s.%s%s" % (head, tail[:i][:6].ljust(6, "0"), tail[i:])
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _append_line(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_ndjson(path, limit=None):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows[-limit:] if limit else rows


# -- configuration ---------------------------------------------------------------------------
def load_watch(runtime_root, data_dir, env=None):
    env = env if env is not None else os.environ
    cfg = _read_json(os.path.join(runtime_root, "config", "pokt-watch.json"), {}) or {}
    local = _read_json(os.path.join(data_dir, "pokt", "watch.local.json"), {}) or {}
    user = dict(cfg.get("user") or {})
    user.update({k: v for k, v in (local.get("user") or {}).items() if v not in (None, "", [])})
    if env.get("POKT_OWNER_ADDRESS"):
        user["owner_address"] = env["POKT_OWNER_ADDRESS"].strip()
    if env.get("POKT_OPERATOR_ADDRESSES"):
        user["operator_addresses"] = [a.strip() for a in env["POKT_OPERATOR_ADDRESSES"].split(",") if a.strip()]
    if env.get("POKT_SERVICE_IDS"):
        user["service_ids"] = [a.strip() for a in env["POKT_SERVICE_IDS"].split(",") if a.strip()]
    suppliers = list(cfg.get("suppliers") or []) + list(local.get("suppliers") or [])
    seen = set(s.get("operator_address") for s in suppliers)
    for op in user.get("operator_addresses") or []:
        if op not in seen:
            suppliers.append({"operator_address": op, "label": "USER_OPERATOR", "role": "USER_OWNED_OR_CONTRACTED", "track": "B_or_C"})
            seen.add(op)
    scan = dict({"initial_lookback_blocks": 240, "max_blocks_per_cycle": 3000, "max_settlement_blocks_per_cycle": 6,
                 "max_pages": 3, "max_suppliers": 6}, **(cfg.get("scan") or {}))
    alerts = dict({"settlement_silence_hours": 24, "state_stale_hours": 3}, **(cfg.get("alerts") or {}))
    market = dict(cfg.get("market") or {})
    return {"suppliers": suppliers[: scan["max_suppliers"]], "validator_candidates": cfg.get("validator_candidates") or [],
            "user": user, "scan": scan, "alerts": alerts, "market": market}


# -- supplier scan ---------------------------------------------------------------------------
def scan_supplier(c, op, head, cursors, scan_cfg, settle_dir, errors):
    """Bounded EventClaimSettled scan for one operator. Returns (record, new_rows)."""
    rec = chain.supplier(c, op)
    path = os.path.join(settle_dir, "%s.ndjson" % op)
    existing = _read_ndjson(path)
    known = set(r.get("key") for r in existing)
    cursor = cursors.get(op)
    if cursor is None:
        cursor = max(0, head["height"] - int(scan_cfg["initial_lookback_blocks"]))
    upper = min(head["height"], cursor + int(scan_cfg["max_blocks_per_cycle"]))
    new_rows = []
    scan_info = {"cursor_before": cursor, "upper": upper, "blocks_found": 0, "blocks_processed": 0,
                 "coverage": "NOT_RUN", "backlog_blocks": head["height"] - upper}
    if upper <= cursor:
        scan_info["coverage"] = "UP_TO_DATE"
        cursors[op] = cursor
    else:
        try:
            found = []
            total = None
            for page in range(1, int(scan_cfg["max_pages"]) + 1):
                res = chain.block_search(c, chain.settlement_query(op, cursor, upper), page=page)
                total = res["total"]
                found.extend(res["blocks"])
                if len(found) >= total or not res["blocks"]:
                    break
            found.sort(key=lambda b: b["height"])
            scan_info["blocks_found"] = total if total is not None else len(found)
            limit = int(scan_cfg["max_settlement_blocks_per_cycle"])
            todo = found[:limit]
            last_ok = cursor
            for b in todo:
                try:
                    br = chain.block_results(c, b["height"])
                except (HttpFailure, ValueError) as e:
                    errors.append({"scope": "block_results", "operator": op, "height": b["height"], "error": str(e)[:200]})
                    break
                rows = settlement.extract_claims(br, b["height"], operator_address=op, block_time_utc=b.get("time_utc"))
                for r in rows:
                    if r["key"] in known:
                        continue
                    known.add(r["key"])
                    _append_line(path, r)
                    new_rows.append(r)
                last_ok = b["height"]
                scan_info["blocks_processed"] += 1
            if scan_info["blocks_processed"] == len(found) and (total is None or len(found) >= total):
                cursors[op] = upper
                scan_info["coverage"] = "COMPLETE_TO_UPPER"
            else:
                cursors[op] = last_ok
                scan_info["coverage"] = "PARTIAL_CURSOR_AT_LAST_PROCESSED"
        except BudgetExceeded as e:
            scan_info["coverage"] = "BUDGET_EXCEEDED"
            errors.append({"scope": "scan", "operator": op, "error": str(e)})
        except (HttpFailure, ValueError) as e:
            scan_info["coverage"] = "INDEX_UNAVAILABLE"
            errors.append({"scope": "scan", "operator": op, "error": str(e)[:200]})
    scan_info["cursor_after"] = cursors.get(op)
    scan_info["backlog_blocks"] = head["height"] - (cursors.get(op) or cursor)
    all_rows = existing + new_rows
    now = _utc_now()
    recent = {}
    for label, hours in (("24h", 24), ("7d", 168)):
        since = now - datetime.timedelta(hours=hours)
        sub = [r for r in all_rows if (_parse_iso(r.get("block_time_utc")) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)) >= since]
        recent[label] = settlement.aggregate(sub, rec.get("owner_address") or op) if rec.get("found") else settlement.aggregate(sub, op)
    last = max(all_rows, key=lambda r: r["height"]) if all_rows else None
    rec.update({
        "scan": scan_info,
        "settlement_rows_total": len(all_rows),
        "settlement_rows_new": len(new_rows),
        "last_settlement": {"height": last["height"], "block_time_utc": last.get("block_time_utc"), "service_id": last.get("service_id"),
                            "reward_to_owner_pokt": last.get("reward_to_owner_pokt")} if last else None,
        "rewards": recent,
    })
    return rec, new_rows


# -- market (optional, read-only file written by the existing pokt-monitor daemon) ----------
def read_market(market_cfg):
    """Read-only view of the existing pokt-monitor daemon's latest.json (schema observed 2026-09-19:
    venues.{gate,bithumb}.{livePrice,close,ret24,quality,executionReady}, cross.premiumPct, state.status)."""
    path = os.path.expanduser((market_cfg or {}).get("pokt_monitor_latest") or "")
    if not path or not os.path.isfile(path):
        return {"available": False, "reason": "pokt-monitor latest.json not found"}
    d = _read_json(path, {}) or {}
    out = {"available": True, "path": path, "updated_at": d.get("updatedAt"), "data_health": d.get("dataHealth"),
           "file_mtime_utc": _iso(datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc))}
    venues = d.get("venues") or {}
    for venue, unit in (("gate", "USDT"), ("bithumb", "KRW")):
        v = venues.get(venue) or {}
        if isinstance(v, dict) and v:
            out[venue] = {"unit": unit, "valid": v.get("valid"), "live_price": v.get("livePrice") if v.get("livePrice") is not None else v.get("close"),
                          "close": v.get("close"), "live_fresh": v.get("liveDataFresh"),
                          "ret24": round(v["ret24"], 4) if isinstance(v.get("ret24"), (int, float)) else None,
                          "quality": v.get("quality"), "execution_ready": v.get("executionReady")}
    cross = d.get("cross") or {}
    if cross:
        out["cross"] = {"premium_pct": round(cross["premiumPct"], 3) if isinstance(cross.get("premiumPct"), (int, float)) else None,
                        "both_valid": cross.get("bothValid"), "cross_confirmed": cross.get("crossConfirmed")}
    st = d.get("state") or {}
    if st:
        out["monitor_state"] = {"status": st.get("status"), "lifecycle": st.get("lifecycle"), "event": st.get("event")}
    return out


# -- alerts ----------------------------------------------------------------------------------
def _alert(alerts, code, subject, severity, message, track):
    alerts.append({"code": code, "subject": subject, "severity": severity, "message": message, "track": track})


def compute_alerts(state, prev_state, watch):
    a = []
    now = _utc_now()
    if state.get("chain") is None:
        _alert(a, "RPC_ALL_FAILED", "chain", "HIGH", "공개 REST/RPC 원점 모두 실패; 이번 주기 체인 상태 없음", "ALL")
        return a
    min_stake = ((state.get("params") or {}).get("derived") or {}).get("supplier_min_stake_pokt")
    prev_sup = {s["operator_address"]: s for s in (prev_state or {}).get("suppliers", []) if s.get("operator_address")}
    for s in state.get("suppliers", []):
        op = s.get("operator_address")
        is_user = s.get("role") != "PUBLIC_SAMPLE_NOT_USER_WALLET"
        if not s.get("found"):
            _alert(a, "SUPPLIER_NOT_FOUND", op, "HIGH" if is_user else "WARN", "체인에 supplier 레코드 없음 (unstake 완료·오타·미스테이크)", s.get("track"))
            continue
        if s.get("unbonding"):
            _alert(a, "SUPPLIER_UNBONDING", op, "HIGH", "supplier가 unstake 진행 중 (unstake_session_end_height=%s)" % s.get("unstake_session_end_height"), s.get("track"))
        if min_stake and s.get("stake_pokt") and float(s["stake_pokt"]) < float(min_stake):
            _alert(a, "SUPPLIER_STAKE_BELOW_MIN", op, "HIGH", "stake %s < min_stake %s POKT" % (s["stake_pokt"], min_stake), s.get("track"))
        prev = prev_sup.get(op)
        if prev and prev.get("found") and sorted(prev.get("service_ids") or []) != sorted(s.get("service_ids") or []):
            _alert(a, "SUPPLIER_SERVICES_CHANGED", op, "INFO", "services %s -> %s" % (prev.get("service_ids"), s.get("service_ids")), s.get("track"))
        if prev and prev.get("found") and prev.get("stake_upokt") != s.get("stake_upokt"):
            _alert(a, "SUPPLIER_STAKE_CHANGED", op, "WARN" if is_user else "INFO", "stake %s -> %s POKT" % (prev.get("stake_pokt"), s.get("stake_pokt")), s.get("track"))
        last = s.get("last_settlement")
        if last and last.get("block_time_utc"):
            t = _parse_iso(last["block_time_utc"])
            hours = (now - t).total_seconds() / 3600 if t else None
            if hours is not None and hours > float(watch["alerts"]["settlement_silence_hours"]):
                _alert(a, "SUPPLIER_SETTLEMENT_SILENCE", op, "WARN", "마지막 관측 정산 %.1f시간 전 (height %s); 관측 범위 한계일 수 있음" % (hours, last["height"]), s.get("track"))
        cov = (s.get("scan") or {}).get("coverage")
        if cov in ("INDEX_UNAVAILABLE", "BUDGET_EXCEEDED"):
            _alert(a, "SUPPLIER_SCAN_DEGRADED", op, "WARN", "정산 스캔 %s" % cov, s.get("track"))
    vs = state.get("validators") or {}
    prev_c = {v["operator_address"]: v for v in ((prev_state or {}).get("validators") or {}).get("candidates", []) if v.get("operator_address")}
    for v in vs.get("candidates", []):
        if not v.get("matched"):
            _alert(a, "VALIDATOR_CANDIDATE_NOT_FOUND", v.get("moniker"), "WARN", "검증자 목록에서 후보를 찾지 못함", "A")
            continue
        if v.get("jailed"):
            _alert(a, "VALIDATOR_JAILED", v["moniker"], "HIGH", "후보 검증자 jailed", "A")
        if not v.get("bonded"):
            _alert(a, "VALIDATOR_NOT_BONDED", v["moniker"], "HIGH", "후보 검증자가 활성 집합 밖 (%s)" % v.get("status"), "A")
        p = prev_c.get(v.get("operator_address"))
        if p and p.get("commission_rate") != v.get("commission_rate"):
            _alert(a, "VALIDATOR_COMMISSION_CHANGED", v["moniker"], "WARN", "commission %s -> %s" % (p.get("commission_rate"), v.get("commission_rate")), "A")
    u = state.get("user") or {}
    pu = (prev_state or {}).get("user") or {}
    if u.get("configured"):
        if pu.get("balance_pokt") is not None and pu.get("balance_pokt") != u.get("balance_pokt"):
            _alert(a, "USER_BALANCE_CHANGED", u.get("owner_address"), "INFO", "잔액 %s -> %s POKT" % (pu.get("balance_pokt"), u.get("balance_pokt")), "A")
        if pu.get("configured") and json.dumps(pu.get("delegations"), sort_keys=True) != json.dumps(u.get("delegations"), sort_keys=True):
            _alert(a, "USER_DELEGATION_CHANGED", u.get("owner_address"), "INFO", "위임 목록 변경", "A")
        for s in state.get("suppliers", []):
            if s.get("found") and s.get("owner_address") == u.get("owner_address") and s.get("operator_address") != u.get("owner_address"):
                pass  # contracted operator (B) is visible via owner binding; informational only
    prev_svc = {x.get("service_id"): x for x in (prev_state or {}).get("services", []) or []}
    for sv in state.get("services", []) or []:
        if sv.get("found") is False:
            _alert(a, "SERVICE_NOT_FOUND", sv.get("service_id"), "WARN", "등록 서비스가 체인에 없음", "D")
        elif sv.get("found") and u.get("configured") and sv.get("owner_address") != u.get("owner_address"):
            _alert(a, "SERVICE_OWNER_MISMATCH", sv.get("service_id"), "HIGH", "서비스 owner %s 가 내 owner 주소와 다름" % sv.get("owner_address"), "D")
        p = prev_svc.get(sv.get("service_id"))
        if p and p.get("found") and sv.get("found") and (p.get("compute_units_per_relay"), p.get("card_bytes")) != (sv.get("compute_units_per_relay"), sv.get("card_bytes")):
            _alert(a, "SERVICE_CHANGED", sv.get("service_id"), "INFO", "CU/relay 또는 card 변경", "D")
    for e in state.get("errors", []):
        if e.get("scope") == "params":
            _alert(a, "PARAMS_UNAVAILABLE", "params", "WARN", e.get("error"), "ALL")
    return a


def merge_alerts(new_list, prev_active, now_iso):
    prev = {(x["code"], x["subject"]): x for x in (prev_active or [])}
    out = []
    for n in new_list:
        k = (n["code"], n["subject"])
        if k in prev:
            p = prev[k]
            n.update({"first_seen_utc": p.get("first_seen_utc", now_iso), "last_seen_utc": now_iso, "count": int(p.get("count", 1)) + 1, "new": False})
        else:
            n.update({"first_seen_utc": now_iso, "last_seen_utc": now_iso, "count": 1, "new": True})
        out.append(n)
    resolved = [dict(p, resolved_at_utc=now_iso) for k, p in prev.items() if k not in set((x["code"], x["subject"]) for x in new_list)]
    return out, resolved


# -- status markdown -------------------------------------------------------------------------
def render_status(state, alerts):
    L = []
    ch = state.get("chain") or {}
    L.append("# POKT 상시 수집 상태")
    L.append("")
    L.append("- 생성: %s (KST %s)" % (state.get("generated_at_utc"), state.get("generated_at_kst")))
    L.append("- 체인: height %s, block time %s" % (ch.get("height"), ch.get("time_utc")))
    d = (state.get("params") or {}).get("derived") or {}
    L.append("- 파라미터: supplier min_stake %s POKT, validator unbonding %ss, max_validators %s, supplier unbonding %s blocks"
             % (d.get("supplier_min_stake_pokt"), d.get("validator_unbonding_seconds"), d.get("max_validators"), d.get("supplier_unbonding_blocks")))
    L.append("- 요청: %s, 실패 %s, 오류 %s건" % ((state.get("scan") or {}).get("requests"), (state.get("scan") or {}).get("failed"), len(state.get("errors") or [])))
    L.append("")
    L.append("## 알림 (활성 %d, 신규 %d)" % (len(alerts), sum(1 for a in alerts if a.get("new"))))
    for a in alerts:
        L.append("- [%s] %s %s — %s%s" % (a["severity"], a["code"], a["subject"], a["message"], " (NEW)" if a.get("new") else ""))
    if not alerts:
        L.append("- 없음")
    L.append("")
    L.append("## C/B Supplier")
    L.append("| label | operator | found | stake POKT | services | unbonding | 24h POKT(정산수) | 7d POKT(정산수) | 마지막 정산 | scan |")
    L.append("|---|---|---|---:|---|---|---:|---:|---|---|")
    for s in state.get("suppliers", []):
        r24 = (s.get("rewards") or {}).get("24h") or {}
        r7 = (s.get("rewards") or {}).get("7d") or {}
        last = s.get("last_settlement") or {}
        L.append("| %s | %s… | %s | %s | %s | %s | %s (%s) | %s (%s) | %s | %s |" % (
            s.get("label"), (s.get("operator_address") or "")[:14], s.get("found"), s.get("stake_pokt"),
            ",".join(s.get("service_ids") or [])[:40], s.get("unbonding"), r24.get("reward_pokt"), r24.get("settlements"),
            r7.get("reward_pokt"), r7.get("settlements"), last.get("block_time_utc"), (s.get("scan") or {}).get("coverage")))
    L.append("")
    v = state.get("validators") or {}
    sm = v.get("summary") or {}
    L.append("## A Validator")
    L.append("- 활성 %s/%s, 총 본딩 %s POKT, 활성집합 컷오프 %s POKT, jailed %s" % (sm.get("bonded_count"), sm.get("max_validators"), sm.get("total_bonded_pokt"), sm.get("active_set_cutoff_pokt"), sm.get("jailed_count")))
    L.append("| 후보 | 상태 | jailed | 토큰 POKT | commission | 순위 |")
    L.append("|---|---|---|---:|---:|---:|")
    for c in v.get("candidates", []):
        L.append("| %s | %s | %s | %s | %s | %s |" % (c.get("moniker"), c.get("status"), c.get("jailed"), c.get("tokens_pokt"), c.get("commission_rate"), c.get("rank")))
    L.append("")
    u = state.get("user") or {}
    L.append("## 사용자 주소")
    if u.get("configured"):
        L.append("- owner %s: 잔액 %s POKT, 위임 %d건 (%s POKT), 언본딩 %d건, 미수령 보상 %s POKT, 소유 supplier %d건" % (
            u.get("owner_address"), u.get("balance_pokt"), len(u.get("delegations") or []), u.get("delegated_pokt"),
            len(u.get("unbonding") or []), (u.get("rewards") or {}).get("total_pokt"), len(u.get("owned_suppliers") or [])))
    else:
        L.append("- 미설정. 자가보관 지갑 생성 후 data/pokt/watch.local.json 또는 .env POKT_OWNER_ADDRESS 에 pokt1 주소를 넣으면 A/B 실제 상태를 추적한다.")
    L.append("")
    L.append("## D 등록 서비스")
    for sv in state.get("services", []) or []:
        L.append("- %s: %s" % (sv.get("service_id"), ("found, owner %s, CU/relay %s, card %sB" % (sv.get("owner_address"), sv.get("compute_units_per_relay"), sv.get("card_bytes"))) if sv.get("found") else "NOT_FOUND"))
    if not state.get("services"):
        L.append("- 없음 (set-user --services ID 로 등록)")
    m = state.get("market") or {}
    L.append("")
    L.append("## 시장 (기존 pokt-monitor 파일 읽기 전용)")
    if m.get("available"):
        g, b = m.get("gate") or {}, m.get("bithumb") or {}
        L.append("- 갱신 %s (%s): Gate %s USDT (24h %s, %s) · Bithumb %s KRW (24h %s, %s) · 프리미엄 %s%% · 모니터 %s" % (
            m.get("updated_at"), m.get("data_health"), g.get("live_price"), g.get("ret24"), g.get("quality"), b.get("live_price"), b.get("ret24"), b.get("quality"),
            (m.get("cross") or {}).get("premium_pct"), (m.get("monitor_state") or {}).get("status")))
    else:
        L.append("- %s" % m.get("reason"))
    L.append("")
    L.append("모든 수치는 공개 REST/RPC GET 관측이며 사용자 수익·비용 후 순수익·시장 매도 가능액을 뜻하지 않는다. 정산 스캔은 cursor 이후 구간만 보장한다.")
    return "\n".join(L) + "\n"


# -- cycle -----------------------------------------------------------------------------------
def run_cycle(runtime_root, data_dir, env=None, client=None, now=None):
    t0 = time.monotonic()
    now = now or _utc_now()
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = os.path.join(data_dir, "pokt")
    os.makedirs(root, exist_ok=True)
    lock = os.path.join(root, "lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - os.path.getmtime(lock)
        if age < 1500:
            return {"run_id": run_id, "status": "SKIPPED", "reason": "LOCK_HELD_%.0fs" % age}
        os.remove(lock)  # stale (previous run died); one retry only
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        return _cycle(runtime_root, data_dir, root, run_id, now, t0, env, client)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _cycle(runtime_root, data_dir, root, run_id, now, t0, env, client):
    watch = load_watch(runtime_root, data_dir, env)
    c = client or Client()
    prev_state = _read_json(os.path.join(root, "STATE.json"), None)
    prev_alerts = (_read_json(os.path.join(root, "ALERTS.json"), {}) or {}).get("active", [])
    cursors = _read_json(os.path.join(root, "cursors.json"), {}) or {}
    errors = []
    state = {"schema": SCHEMA, "run_id": run_id, "generated_at_utc": _iso(now), "generated_at_kst": now.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
             "host": socket.gethostname(), "chain": None, "params": None, "suppliers": [], "validators": None, "user": None,
             "market": None, "errors": errors, "watch": {"suppliers": len(watch["suppliers"]), "candidates": len(watch["validator_candidates"]),
                                                        "user_configured": bool(watch["user"].get("owner_address"))}}
    head = None
    try:
        head = chain.latest_block(c)
        state["chain"] = head
    except (HttpFailure, BudgetExceeded, ValueError, KeyError) as e:
        errors.append({"scope": "head", "error": str(e)[:200]})
    if head:
        try:
            state["params"] = chain.params(c)
        except (HttpFailure, BudgetExceeded, ValueError, KeyError) as e:
            errors.append({"scope": "params", "error": str(e)[:200]})
            state["params"] = (prev_state or {}).get("params")
        settle_dir = os.path.join(root, "settlements")
        for w in watch["suppliers"]:
            op = w.get("operator_address")
            if not chain.is_address(op):
                errors.append({"scope": "watch", "error": "invalid supplier address %r" % op})
                continue
            try:
                rec, _new = scan_supplier(c, op, head, cursors, watch["scan"], settle_dir, errors)
            except (HttpFailure, BudgetExceeded, ValueError, KeyError) as e:
                errors.append({"scope": "supplier", "operator": op, "error": str(e)[:200]})
                rec = {"found": None, "operator_address": op, "scan": {"coverage": "ERROR"}}
            rec.update({"label": w.get("label"), "role": w.get("role"), "track": w.get("track")})
            state["suppliers"].append(rec)
        try:
            rows = chain.validators(c)
            maxv = ((state.get("params") or {}).get("derived") or {}).get("max_validators") or 22
            summary = chain.validator_set_summary(rows, maxv)
            by_addr = {r["operator_address"]: (i + 1, r) for i, r in enumerate(rows)}
            by_moniker = {}
            for i, r in enumerate(rows):
                by_moniker.setdefault(r.get("moniker"), (i + 1, r))
            cands = []
            for cand in watch["validator_candidates"]:
                hit = by_addr.get(cand.get("operator_address")) or by_moniker.get(cand.get("moniker"))
                if hit:
                    rank, r = hit
                    cands.append(dict(r, moniker=cand.get("moniker") or r.get("moniker"), matched=True, rank=rank, source=cand.get("source")))
                else:
                    cands.append({"moniker": cand.get("moniker"), "operator_address": cand.get("operator_address"), "matched": False, "source": cand.get("source")})
            state["validators"] = {"summary": summary, "candidates": cands,
                                   "top": [{k: r[k] for k in ("moniker", "operator_address", "tokens_pokt", "commission_rate", "jailed", "bonded")} for r in rows[:25]]}
        except (HttpFailure, BudgetExceeded, ValueError, KeyError) as e:
            errors.append({"scope": "validators", "error": str(e)[:200]})
            state["validators"] = (prev_state or {}).get("validators")
        state["services"] = []
        for sid in (watch["user"].get("service_ids") or [])[:6]:
            try:
                state["services"].append(chain.service(c, sid))
            except (HttpFailure, BudgetExceeded, ValueError, KeyError) as e:
                errors.append({"scope": "service", "service_id": sid, "error": str(e)[:200]})
                state["services"].append({"found": None, "service_id": sid})
        u = watch["user"]
        owner = u.get("owner_address")
        if owner and chain.is_address(owner):
            info = {"configured": True, "owner_address": owner}
            try:
                info["balance_pokt"] = chain.balance(c, owner)["pokt"]
                info["delegations"] = chain.delegations(c, owner)
                info["delegated_pokt"] = chain.upokt_to_pokt(sum(int(str(d["pokt"]).replace(".", "")) for d in info["delegations"] if d.get("pokt")))
                info["unbonding"] = chain.unbonding_delegations(c, owner)
                info["rewards"] = chain.delegation_rewards(c, owner)
            except (HttpFailure, BudgetExceeded, ValueError, KeyError) as e:
                errors.append({"scope": "user", "error": str(e)[:200]})
            info["owned_suppliers"] = [s["operator_address"] for s in state["suppliers"] if s.get("found") and s.get("owner_address") == owner]
            state["user"] = info
        elif owner:
            errors.append({"scope": "user", "error": "invalid owner address %r" % owner})
            state["user"] = {"configured": False}
        else:
            state["user"] = {"configured": False}
    state["market"] = read_market(watch["market"])
    try:
        from .economics import network_snapshot
        ne = network_snapshot()
        state["network_economics"] = {k: ne.get(k) for k in ("as_of", "suppliers", "supplier_stake_pokt", "all_staked_pokt", "settlement", "observed_network_average_yield")}
        state["network_economics"]["supplier_stake_pokt"] = dict(state["network_economics"]["supplier_stake_pokt"] or {})
        state["network_economics"]["supplier_stake_pokt"]["series_60d"] = (state["network_economics"]["supplier_stake_pokt"].get("series_60d") or [])[-30:]
    except Exception as e:
        state["network_economics"] = {"error": str(e)[:120]}
    try:
        from . import surveillance
        sv = surveillance.status(data_dir)
        recent = surveillance.history(data_dir, limit=5)
        state["surveillance"] = {"available": bool(sv), "at_kst": (sv or {}).get("at_kst"), "sources": (sv or {}).get("sources"), "changed_last_run": (sv or {}).get("changed"),
                                 "failed_last_run": (sv or {}).get("failed"), "recent_events": [{k: e.get(k) for k in ("at_utc", "source_id", "change", "added_lines", "removed_lines", "classification")} for e in recent],
                                 "recent_reviews": [{k: r.get(k) for k in ("source_id", "classification", "tracks", "why_ko")} for r in surveillance.latest_reviews(data_dir, limit=5)]}
    except Exception as e:
        state["surveillance"] = {"available": False, "error": str(e)[:120]}
    state["scan"] = dict(c.summary(), duration_s=round(time.monotonic() - t0, 1))
    alerts = compute_alerts(state, prev_state, watch)
    active, resolved = merge_alerts(alerts, prev_alerts, _iso(now))
    state["alerts_active"] = len(active)
    state["alerts_new"] = sum(1 for a in active if a.get("new"))
    state["status"] = "DEGRADED" if head is None else ("CAUTION" if any(a["severity"] == "HIGH" for a in active) or errors else "NORMAL")
    # persist
    _atomic_write(os.path.join(root, "STATE.json"), json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    prev_resolved = (_read_json(os.path.join(root, "ALERTS.json"), {}) or {}).get("resolved", [])[-200:]
    _atomic_write(os.path.join(root, "ALERTS.json"), json.dumps({"generated_at_utc": _iso(now), "active": active, "resolved": (prev_resolved + resolved)[-200:]}, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(os.path.join(root, "cursors.json"), json.dumps(cursors, indent=2) + "\n")
    _atomic_write(os.path.join(root, "STATUS.md"), render_status(state, active))
    decision = None
    try:
        from .decision import compute
        decision = compute(data_dir, state, active)
    except Exception as e:
        errors.append({"scope": "decision", "error": str(e)[:200]})
    try:
        from .web import latest_brief, render_html
        brief = latest_brief(data_dir)
        if brief:
            _atomic_write(os.path.join(root, "brief.md"), brief)
        _atomic_write(os.path.join(root, "index.html"), render_html(state, active, brief, decision))
    except Exception as e:  # the page is a convenience; never fail the cycle for it
        errors.append({"scope": "html", "error": str(e)[:200]})
    _atomic_write(os.path.join(root, "receipts", "%s.json" % run_id), json.dumps({"run_id": run_id, "receipts": c.receipts, "summary": c.summary()}, indent=1) + "\n")
    hist = {"run_id": run_id, "at": _iso(now), "status": state["status"], "height": (head or {}).get("height"), "alerts": len(active), "new": state["alerts_new"],
            "suppliers": [{"op": s.get("operator_address"), "found": s.get("found"), "stake": s.get("stake_pokt"), "new_rows": s.get("settlement_rows_new"),
                           "r24": ((s.get("rewards") or {}).get("24h") or {}).get("reward_pokt"), "cov": (s.get("scan") or {}).get("coverage")} for s in state["suppliers"]],
            "requests": state["scan"]["requests"], "errors": len(errors)}
    _append_line(os.path.join(root, "history", now.astimezone(KST).strftime("%Y-%m-%d") + ".ndjson"), hist)
    return {"run_id": run_id, "status": state["status"], "height": (head or {}).get("height"), "alerts_active": len(active), "alerts_new": state["alerts_new"],
            "suppliers": len(state["suppliers"]), "requests": state["scan"]["requests"], "errors": len(errors), "state_path": os.path.join(root, "STATE.json")}
