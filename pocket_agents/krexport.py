"""KR Export Pulse v0 (kr-export-pulse-v1) - Korea Customs Service export statistics as a verification service.

Question: "What changed in Korea's semiconductor / car / parts exports, which items drove the total, and was the number
revised since I last looked?"  Sources (data.go.kr, key from DATA_GO_KR_SERVICE_KEY, free, no reuse restriction):
  10-day provisional exports by 10 major items  (apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs)
  monthly trade by HS code and country           (apis.data.go.kr/1220000/nitemtrade)
Rules (GPT reframe 2026-09-20): derive 11-20 and 21-end increments only from the same fetch; never map HS codes to
10-day item categories one-to-one; keep provisional vs revised apart; revision history starts at first observation.

Endpoints (REST, JSON object always):
  GET /v1/version  GET /v1/health  GET /openapi.json  GET /v1/items
  GET /v1/tenday?from=YYYYMM&to=YYYYMM          normalized cumulative rows + derived increments
  GET /v1/pulse?months=3                        latest period vs prior month / prior year, item contributions
  GET /v1/hs?hs=8542&from=YYYYMM&to=YYYYMM[&country=US]
  GET /v1/revisions[?period=202608]             observed changes of the same period across fetches
  POST /v1/query {question, hs?, from?, to?}    deterministic ko/en
"""
import datetime
import http.server
import json
import os
import re
import socketserver
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SERVICE_ID = "kr-export-pulse-v1"
VERSION = "0.1.0"
UTC = datetime.timezone.utc
UA = "kr-export-pulse-v1/%s (Pocket Network service; read-only)" % VERSION
TENDAY = "https://apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs"
HS = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
UNIT = "USD thousand"
# order as given in the KCS API description (2026-09-20): total, then 10 major items
ITEMS = [("00", "total", "전체"), ("01", "semiconductors", "반도체"), ("02", "steel_products", "철강제품"), ("03", "passenger_cars", "승용차"),
         ("04", "petroleum_products", "석유제품"), ("05", "wireless_comm_devices", "무선통신기기"), ("06", "ships", "선박"),
         ("07", "auto_parts", "자동차부품"), ("08", "computer_peripherals", "컴퓨터주변기기"), ("09", "precision_instruments", "정밀기기"),
         ("10", "home_appliances", "가전제품")]
KO_ITEM = {ko: en for _, en, ko in ITEMS}
_cache = {}
_lock = threading.Lock()


def _key():
    return os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()


def _fetch_xml(url):
    req = urllib.request.Request(url, headers={"accept": "application/xml", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(8 * 1024 * 1024).decode("utf-8")


fetch_xml = _fetch_xml


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def _now():
    return datetime.datetime.now(UTC).isoformat(timespec="seconds")


def _num(s):
    s = (s or "").replace(",", "").strip()
    return int(s) if re.match(r"^-?\d+$", s) else None


def _yyyymm(s, default):
    s = (s or default)
    if not re.match(r"^\d{6}$", s or ""):
        raise ValueError("INVALID_YYYYMM")
    return s


def _parse(xml_text):
    root = ET.fromstring(xml_text)
    code = (root.findtext(".//resultCode") or "").strip()
    msg = (root.findtext(".//resultMsg") or "").strip()
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.findall(".//item")]
    return code, msg, items


class KeyMissing(Exception):
    pass


def tenday_raw(frm, to):
    if not _key():
        raise KeyMissing()
    url = TENDAY + "?" + urllib.parse.urlencode({"serviceKey": _key(), "strtYymm": frm, "endYymm": to})
    code, msg, items = _parse(fetch_xml(url))
    if code != "00":
        raise RuntimeError("UPSTREAM_%s" % (code or "NA"))
    return items


def _period_end_days(mon):
    y, m = int(mon[:4]), int(mon[4:])
    nxt = datetime.date(y + (m == 12), (m % 12) + 1, 1)
    return (nxt - datetime.timedelta(days=1)).day


def normalize_tenday(items):
    rows = []
    for it in items:
        mon, dt = it.get("priodMon"), it.get("priodDt")
        if not mon or not dt:
            continue
        end = int(dt.split("~")[1]) if "~" in dt else None
        stage = "d1_10" if end == 10 else ("d1_20" if end == 20 else "month")
        vals = {en: _num(it.get("itemUsdAmt" + code)) for code, en, _ in ITEMS}
        rows.append({"period": mon, "days": dt, "stage": stage, "cumulative": vals})
    rows.sort(key=lambda r: (r["period"], {"d1_10": 0, "d1_20": 1, "month": 2}[r["stage"]]))
    return rows


def derive_increments(rows):
    """Same-fetch increments: 11-20 = (1-20) - (1-10); 21-end = (month) - (1-20). Never across fetches."""
    by = {}
    for r in rows:
        by.setdefault(r["period"], {})[r["stage"]] = r["cumulative"]
    out = []
    for mon in sorted(by):
        st = by[mon]
        segs = []
        if "d1_10" in st:
            segs.append({"days": "01~10", "amounts": st["d1_10"], "derived": False})
        if "d1_10" in st and "d1_20" in st:
            segs.append({"days": "11~20", "amounts": {k: (st["d1_20"][k] - st["d1_10"][k]) if st["d1_20"].get(k) is not None and st["d1_10"].get(k) is not None else None for k in st["d1_20"]}, "derived": True})
        if "d1_20" in st and "month" in st:
            segs.append({"days": "21~%02d" % _period_end_days(mon), "amounts": {k: (st["month"][k] - st["d1_20"][k]) if st["month"].get(k) is not None and st["d1_20"].get(k) is not None else None for k in st["month"]}, "derived": True})
        out.append({"period": mon, "segments": segs, "stages_available": sorted(st.keys())})
    return out


def _snapshot(data_dir, rows):
    """Record each (period, stage) cumulative vector when it differs from the last recorded one. Returns new revision events."""
    if not data_dir:
        return []
    root = os.path.join(data_dir, "krexport", "tenday")
    os.makedirs(root, exist_ok=True)
    events = []
    for r in rows:
        p = os.path.join(root, "%s_%s.ndjson" % (r["period"], r["stage"]))
        last = None
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        last = json.loads(line)
                    except ValueError:
                        pass
        if last is None or last.get("cumulative") != r["cumulative"]:
            rec = {"observed_at_utc": _now(), "period": r["period"], "stage": r["stage"], "days": r["days"], "cumulative": r["cumulative"],
                   "revision_of": last.get("observed_at_utc") if last else None,
                   "delta": {k: (r["cumulative"][k] - last["cumulative"].get(k)) if last and r["cumulative"].get(k) is not None and last["cumulative"].get(k) is not None else None for k in r["cumulative"]} if last else None}
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if last:
                events.append(rec)
    return events


def tenday(frm=None, to=None, data_dir=None):
    today = datetime.date.today()
    to = _yyyymm(to, today.strftime("%Y%m"))
    frm = _yyyymm(frm, (today.replace(day=1) - datetime.timedelta(days=95)).strftime("%Y%m"))
    items = _cached("tenday:%s:%s" % (frm, to), 3600, lambda: tenday_raw(frm, to))
    rows = normalize_tenday(items)
    revs = _snapshot(data_dir, rows)
    cur = today.strftime("%Y%m")
    return {"schema": "kr-export-tenday/v1", "service": SERVICE_ID, "fetched_at_utc": _now(), "unit": UNIT, "range": {"from": frm, "to": to},
            "publication": "1-10 on the 11th, 1-20 on the 21st, full month on the 1st of the next month; current month provisional, earlier months revised for corrections/withdrawals",
            "rows": [dict(r, provisional=(r["period"] >= cur)) for r in rows], "increments": derive_increments(rows),
            "revisions_observed_this_fetch": revs, "source": TENDAY.replace("getPrlstMmUtPrviExpAcrs", ""), "publisher": "Korea Customs Service via data.go.kr"}


def pulse(months=3, data_dir=None):
    months = max(2, min(int(months or 3), 24))
    today = datetime.date.today()
    frm = (today.replace(day=1) - datetime.timedelta(days=31 * (months + 12))).strftime("%Y%m")
    td = tenday(frm, today.strftime("%Y%m"), data_dir)
    rows = td["rows"]
    if not rows:
        return {"schema": "kr-export-pulse/v1", "service": SERVICE_ID, "status": "NO_DATA", "fetched_at_utc": _now()}
    latest = rows[-1]
    same_stage = [r for r in rows if r["stage"] == latest["stage"]]

    def find(period):
        return next((r for r in same_stage if r["period"] == period), None)
    y, m = int(latest["period"][:4]), int(latest["period"][4:])
    prev_m = "%04d%02d" % (y - (m == 1), 12 if m == 1 else m - 1)
    prev_y = "%04d%02d" % (y - 1, m)
    comps = {}
    for label, ref in (("vs_prior_month_same_stage", find(prev_m)), ("vs_prior_year_same_stage", find(prev_y))):
        if not ref:
            comps[label] = None
            continue
        a, b = latest["cumulative"], ref["cumulative"]
        tot_change = (a["total"] or 0) - (b["total"] or 0)
        contrib = []
        for _, en, ko in ITEMS[1:]:
            if a.get(en) is None or b.get(en) is None:
                continue
            ch = a[en] - b[en]
            contrib.append({"item": en, "item_ko": ko, "change": ch, "change_pct": round(ch / b[en] * 100, 2) if b[en] else None,
                            "share_of_total_change_pct": round(ch / tot_change * 100, 1) if tot_change else None})
        contrib.sort(key=lambda c: -abs(c["change"]))
        comps[label] = {"reference_period": ref["period"], "reference_days": ref["days"], "total_change": tot_change,
                        "total_change_pct": round(tot_change / b["total"] * 100, 2) if b.get("total") else None, "contributions": contrib}
    return {"schema": "kr-export-pulse/v1", "service": SERVICE_ID, "fetched_at_utc": _now(), "unit": UNIT,
            "latest": {"period": latest["period"], "days": latest["days"], "stage": latest["stage"], "provisional": latest["provisional"], "cumulative": latest["cumulative"]},
            "comparisons": comps, "rules": ["same stage compared (1-10 vs 1-10, month vs month)", "current month is provisional; prior-year value may itself have been revised",
                                            "contribution = item change / total change (sign-aware); items are a subset so shares do not sum to 100"],
            "revisions_observed_this_fetch": td["revisions_observed_this_fetch"], "source": td["source"]}


def hs(code, frm=None, to=None, country=None):
    if not _key():
        raise KeyMissing()
    code = (code or "").strip()
    if not re.match(r"^\d{2,10}$", code):
        raise ValueError("INVALID_HS")
    today = datetime.date.today()
    to = _yyyymm(to, (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y%m"))
    frm = _yyyymm(frm, to)
    url = HS + "?" + urllib.parse.urlencode({"serviceKey": _key(), "strtYymm": frm, "endYymm": to, "hsSgn": code})
    rcode, msg, items = _cached("hs:%s:%s:%s" % (code, frm, to), 3600, lambda: _parse(fetch_xml(url)))
    if rcode != "00":
        raise RuntimeError("UPSTREAM_%s" % (rcode or "NA"))
    rows = []
    totals = None
    for it in items:
        if it.get("year") == "총계":
            totals = {"export_usd": _num(it.get("expDlr")), "import_usd": _num(it.get("impDlr")), "balance_usd": _num(it.get("balPayments")), "export_kg": _num(it.get("expWgt")), "import_kg": _num(it.get("impWgt"))}
            continue
        if country and it.get("statCd") != country.upper():
            continue
        rows.append({"period": (it.get("year") or "").replace(".", ""), "hs": it.get("hsCd"), "hs_name_ko": it.get("statKor"), "country": it.get("statCd"), "country_ko": it.get("statCdCntnKor1"),
                     "export_usd": _num(it.get("expDlr")), "import_usd": _num(it.get("impDlr")), "balance_usd": _num(it.get("balPayments")), "export_kg": _num(it.get("expWgt")), "import_kg": _num(it.get("impWgt"))})
    return {"schema": "kr-export-hs/v1", "service": SERVICE_ID, "fetched_at_utc": _now(), "hs_query": code, "range": {"from": frm, "to": to}, "country_filter": (country or "").upper() or None,
            "unit": "USD (not thousands) and kg", "count": len(rows), "totals_all_countries": totals, "rows": rows,
            "note": "monthly HS rows are revised for corrections/withdrawals; they are NOT mapped to the 10-day major-item categories", "source": HS.replace("getNitemtradeList", "")}


def revisions(data_dir, period=None, limit=100):
    root = os.path.join(data_dir or "", "krexport", "tenday")
    out = []
    if data_dir and os.path.isdir(root):
        for f in sorted(os.listdir(root)):
            if period and not f.startswith(period):
                continue
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("revision_of"):
                        out.append(rec)
    out.sort(key=lambda r: r["observed_at_utc"])
    return {"schema": "kr-export-revisions/v1", "service": SERVICE_ID, "period_filter": period, "count": len(out[-limit:]), "revisions": out[-limit:],
            "scope": "revisions observed by this service since it started fetching; earlier revisions are not reconstructed"}


INTENTS = ("PULSE", "TENDAY", "HS", "ITEMS", "REVISIONS", "UNKNOWN")


def parse_question(q):
    q = q if isinstance(q, str) else ""
    if len(q) > 2000:
        raise ValueError("INVALID_QUESTION")
    hs_m = re.search(r"\b(?:hs|HS)\s*[:#]?\s*(\d{2,10})\b", q) or re.search(r"\b(\d{4,10})\b(?=\s*(?:번|코드|hs))", q, re.I)
    intent = "UNKNOWN"
    if re.search(r"revis|수정|정정|바뀌었|달라졌", q, re.I):
        intent = "REVISIONS"
    elif hs_m or re.search(r"\bhs\b|국가별|by country|hs code", q, re.I):
        intent = "HS"
    elif re.search(r"items?\b|품목 목록|카테고리|categories", q, re.I) and not re.search(r"change|변화|증가|감소", q, re.I):
        intent = "ITEMS"
    elif re.search(r"10.?day|10일|ten.?day|누적|cumulative|raw", q, re.I):
        intent = "TENDAY"
    elif re.search(r"chang|변화|증가|감소|driv|기여|pulse|흐름|trend|추세|수출", q, re.I):
        intent = "PULSE"
    return {"intent": intent, "hs": hs_m.group(1) if hs_m else None}


def answer(body, data_dir=None):
    body = body if isinstance(body, dict) else {}
    q = body.get("question") or ""
    p = parse_question(q)
    intent = p["intent"]
    res = {"schema": "kr-export-answer/v1", "service": SERVICE_ID, "intent": intent, "question": q[:300], "status": "OK"}
    if intent == "UNKNOWN":
        res.update(status="NEEDS_CLARIFICATION", intents=list(INTENTS))
        return res
    if intent == "ITEMS":
        res.update(data={"items": [{"code": c, "item": en, "item_ko": ko} for c, en, ko in ITEMS], "unit": UNIT})
    elif intent == "TENDAY":
        res.update(data=tenday(body.get("from"), body.get("to"), data_dir))
    elif intent == "HS":
        code = body.get("hs") or p["hs"]
        if not code:
            res.update(status="NEEDS_CLARIFICATION", missing=["hs (2-10 digit code)"])
            return res
        res.update(data=hs(code, body.get("from"), body.get("to"), body.get("country")))
    elif intent == "REVISIONS":
        res.update(data=revisions(data_dir, body.get("period")))
    else:
        res.update(data=pulse(body.get("months") or 3, data_dir))
    return res


# -- HTTP ----------------------------------------------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    data_dir = None

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self, fn):
        try:
            return self._json(200, fn())
        except KeyMissing:
            return self._json(503, {"service": SERVICE_ID, "status": "KEY_NOT_CONFIGURED", "detail": "DATA_GO_KR_SERVICE_KEY is not set on this supplier"})
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        except RuntimeError as e:
            return self._json(502, {"service": SERVICE_ID, "status": str(e)[:40]})
        except Exception as e:
            return self._json(503, {"service": SERVICE_ID, "status": "UPSTREAM_UNAVAILABLE", "detail": type(e).__name__})

    def do_GET(self):
        p, _, qs = self.path.partition("?")
        qd = dict(urllib.parse.parse_qsl(qs))
        if p in ("/", "/index.html"):
            return self._html(200, UI_HTML)
        if p == "/openapi.json":
            return self._json(200, openapi_spec())
        if p == "/v1/version":
            return self._json(200, {"service": SERVICE_ID, "version": VERSION, "publisher": "Korea Customs Service via data.go.kr", "key_configured": bool(_key())})
        if p == "/v1/health":
            return self._json(200, {"status": "ok", "service": SERVICE_ID, "time_utc": _now()})
        if p == "/v1/items":
            return self._json(200, {"schema": "kr-export-items/v1", "service": SERVICE_ID, "unit": UNIT, "items": [{"code": c, "item": en, "item_ko": ko} for c, en, ko in ITEMS]})
        if p == "/v1/tenday":
            return self._run(lambda: tenday(qd.get("from"), qd.get("to"), self.data_dir))
        if p == "/v1/pulse":
            return self._run(lambda: pulse(qd.get("months") or 3, self.data_dir))
        if p == "/v1/hs":
            return self._run(lambda: hs(qd.get("hs"), qd.get("from"), qd.get("to"), qd.get("country")))
        if p == "/v1/revisions":
            return self._run(lambda: revisions(self.data_dir, qd.get("period")))
        return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})

    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        code = 200 if p in ("/", "/index.html", "/openapi.json", "/v1/version", "/v1/health", "/v1/items", "/v1/tenday", "/v1/pulse", "/v1/hs", "/v1/revisions") else 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8" if p.startswith("/v1") or p.endswith(".json") else "text/html; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self):
        return self._json(405, {"service": SERVICE_ID, "status": "METHOD_NOT_ALLOWED"})

    do_DELETE = do_PATCH = do_PUT

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        if p not in ("/v1/query", "/", ""):
            return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 16 * 1024:
                return self._json(413, {"service": SERVICE_ID, "status": "BODY_TOO_LARGE"})
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        return self._run(lambda: answer(body, self.data_dir))


def serve(data_dir, host="0.0.0.0", port=8797):
    _Handler.data_dir = data_dir
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()


def service_card(base="https://export.pokt-agent.com"):
    return {
        "schema": "pocket-service-card/v1",
        "description": "KR Export Pulse: what changed in Korea's exports of semiconductors, cars, auto parts and 7 other major items, which items "
                       "drove the total, and whether a number was revised since last observed. Korea Customs Service 10-day provisional statistics "
                       "(cumulative 1-10 / 1-20 / month, USD thousand) with same-fetch increments, same-stage comparisons to the prior month and "
                       "prior year with item contributions, monthly HS-code trade by country, and a revision log kept from first observation. "
                       "Provisional and revised values are labelled; HS codes are never mapped one-to-one onto the 10-day item categories. "
                       "Observed values only, no forecasts. Every response is a JSON object. Identity via GET /v1/version, readiness via "
                       "GET /v1/health, function via GET /v1/pulse, /v1/tenday, /v1/hs, /v1/revisions, /v1/items and POST /v1/query (ko/en).",
        "updated": datetime.date.today().isoformat(),
        "rpc_types": [{"type": "REST", "intent": "expected", "backend_hint": "%s HTTP server on :8797; mount at /" % SERVICE_ID,
                       "notes": "Supplier holds its own data.go.kr key (free, unrestricted reuse licence). Without a key the backend answers 503 KEY_NOT_CONFIGURED."}],
        "apis": ["%s-api" % SERVICE_ID],
        "specs": [{"kind": "openapi", "api": "%s-api" % SERVICE_ID, "url": "%s/openapi.json" % base, "notes": "Served by the same backend."}],
        "docs": base + "/",
        "access": "public",
        "results": "variable",
        "serving": {
            "backend": "Stateless proxy + local revision log over data.go.kr customs APIs; 1 h cache; supplier-side key.",
            "implementations": ["%s >= %s" % (SERVICE_ID, VERSION)], "min_disk_gb": 1, "min_ram_gb": 1,
            "healthcheck": [
                {"rpc_type": "REST", "request": {"path": "/v1/version", "method": "GET"}, "expect": {"json_path": "$.service", "matches": "^%s$" % SERVICE_ID}, "notes": "Identity probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/health", "method": "GET"}, "expect": {"json_path": "$.status", "matches": "^ok$"}, "notes": "Readiness probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/pulse?months=3", "method": "GET"}, "expect": {"json_path": "$.schema", "matches": "^kr-export-pulse/v1$"},
                 "notes": "Functional probe: latest period vs prior month/year with item contributions."},
                {"rpc_type": "REST", "request": {"path": "/v1/query", "method": "POST", "headers": {"content-type": "application/json"}, "body": {"question": "반도체 수출 지난달 대비 얼마나 변했어?"}},
                 "expect": {"json_path": "$.intent", "matches": "^PULSE$"}, "notes": "Functional probe: Korean question routed deterministically."},
            ],
        },
    }


def openapi_spec(base="https://export.pokt-agent.com"):
    ym = {"in": "query", "schema": {"type": "string", "pattern": "^\\d{6}$"}, "description": "YYYYMM"}
    return {
        "openapi": "3.0.3",
        "info": {"title": "KR Export Pulse", "version": VERSION, "description": "Korea Customs Service export statistics as change analysis with revision tracking. Observed values only."},
        "servers": [{"url": base}],
        "paths": {
            "/v1/version": {"get": {"summary": "Identity", "responses": {"200": {"description": "service id, version, key_configured"}}}},
            "/v1/health": {"get": {"summary": "Readiness", "responses": {"200": {"description": "{status: ok}"}}}},
            "/v1/items": {"get": {"summary": "The 10 major items and unit", "responses": {"200": {"description": "kr-export-items/v1"}}}},
            "/v1/tenday": {"get": {"summary": "10-day cumulative rows + same-fetch increments", "parameters": [dict(ym, name="from"), dict(ym, name="to")], "responses": {"200": {"description": "kr-export-tenday/v1"}, "503": {"description": "KEY_NOT_CONFIGURED"}}}},
            "/v1/pulse": {"get": {"summary": "Latest period vs prior month / prior year (same stage), item contributions", "parameters": [{"name": "months", "in": "query", "schema": {"type": "integer", "default": 3}}], "responses": {"200": {"description": "kr-export-pulse/v1"}}}},
            "/v1/hs": {"get": {"summary": "Monthly trade by HS code and country", "parameters": [{"name": "hs", "in": "query", "required": True, "schema": {"type": "string"}}, dict(ym, name="from"), dict(ym, name="to"), {"name": "country", "in": "query", "schema": {"type": "string"}, "description": "ISO-2, e.g. US"}], "responses": {"200": {"description": "kr-export-hs/v1"}}}},
            "/v1/revisions": {"get": {"summary": "Revisions of the same period observed across fetches", "parameters": [{"name": "period", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "kr-export-revisions/v1"}}}},
            "/v1/query": {"post": {"summary": "Deterministic question (ko/en)", "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"question": {"type": "string"}, "hs": {"type": "string"}, "from": {"type": "string"}, "to": {"type": "string"}, "country": {"type": "string"}, "months": {"type": "integer"}}}}}},
                "responses": {"200": {"description": "kr-export-answer/v1"}}}},
        },
    }


UI_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KR Export Pulse</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}code{background:#f4f4f6;border-radius:4px;padding:.1rem .3rem}h1{font-size:1.5rem}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}</style></head>
<body><h1>KR Export Pulse <small>v%s</small></h1>
<p>Korea Customs Service export statistics as change analysis: 10-day provisional figures for 10 major items, same-fetch increments, prior-month/prior-year comparisons with item contributions, monthly HS trade by country, and a revision log. Observed values only; every response a JSON object.</p>
<table><tr><th>Endpoint</th><th>Purpose</th></tr>
<tr><td><a href="/v1/pulse?months=3">GET /v1/pulse</a></td><td>latest period vs prior month / year, contributions</td></tr>
<tr><td><a href="/v1/tenday">GET /v1/tenday</a></td><td>cumulative rows + derived increments</td></tr>
<tr><td><a href="/v1/hs?hs=8542">GET /v1/hs?hs=8542</a></td><td>monthly HS trade by country</td></tr>
<tr><td><a href="/v1/revisions">GET /v1/revisions</a></td><td>revisions observed since first fetch</td></tr>
<tr><td><a href="/v1/items">GET /v1/items</a></td><td>item list and unit</td></tr>
<tr><td><code>POST /v1/query</code></td><td><code>{"question":"반도체 수출 지난달 대비 얼마나 변했어?"}</code></td></tr>
<tr><td><a href="/openapi.json">GET /openapi.json</a></td><td>OpenAPI 3 spec</td></tr></table>
<p>Service id <code>%s</code> on Pocket Network.</p></body></html>""" % (VERSION, SERVICE_ID)
