"""DART KR Events v0 (dart-kr-events-v1) - Korean corporate filings as change tracking (OpenDART, key from OPENDART_API_KEY).

Question: "What changed for this company since I last checked, which filings were corrected, and what were the terms
as knowable on a given date?"  Not an English mirror of OpenDART: it links corrections to originals, diffs structured
issuance terms (rights offering, CB, BW, EB, bonus issue) field by field, and answers point-in-time.
Rules (GPT reframe 2026-09-20): use DART's correction markers but never claim a complete correction graph; never mix
later corrections into an as-of view; keep "planned" and "completed" apart (completion is not inferred).

Endpoints (REST, JSON object always):
  GET /v1/version  GET /v1/health  GET /openapi.json
  GET /v1/corp?name=삼성전자 | ?stock_code=005930          corp_code lookup (corpCode.xml cached locally)
  GET /v1/filings?corp_code=&from=YYYYMMDD&to=YYYYMMDD    normalized filings with kind ORIGINAL|CORRECTION|WITHDRAWAL|ATTACHMENT
  GET /v1/changes?corp_code=&since=YYYYMMDD               corrections since date, linked originals, term diffs where structured
  GET /v1/asof?corp_code=&date=YYYYMMDD                   what was knowable on that date (no later corrections)
  POST /v1/query {question, corp_code?|name?, since?|date?}
"""
import datetime
import http.server
import io
import json
import os
import re
import socketserver
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

SERVICE_ID = "dart-kr-events-v1"
VERSION = "0.1.0"
UTC = datetime.timezone.utc
UA = "dart-kr-events-v1/%s (Pocket Network service; read-only)" % VERSION
API = "https://opendart.fss.or.kr/api/"
TERM_APIS = {  # report-name keyword -> structured OpenDART endpoint
    "유상증자결정": "piicDecsn", "무상증자결정": "fricDecsn", "전환사채권발행결정": "cvbdIsDecsn", "신주인수권부사채권발행결정": "bdwtIsDecsn", "교환사채권발행결정": "exbdIsDecsn",
}
PREFIX = [("[기재정정]", "CORRECTION"), ("[정정]", "CORRECTION"), ("[철회]", "WITHDRAWAL"), ("[첨부정정]", "ATTACHMENT"), ("[첨부추가]", "ATTACHMENT"), ("[연장결정]", "EXTENSION"), ("[발행조건확정]", "TERMS_FIXED")]
_cache = {}
_lock = threading.Lock()


def _key():
    return os.environ.get("OPENDART_API_KEY", "").strip()


class KeyMissing(Exception):
    pass


def _fetch(url):
    req = urllib.request.Request(url, headers={"accept": "*/*", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(16 * 1024 * 1024)


fetch = _fetch


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


def _api(name, **params):
    if not _key():
        raise KeyMissing()
    params = {k: v for k, v in params.items() if v not in (None, "")}
    params["crtfc_key"] = _key()
    d = json.loads(_cached(name + ":" + json.dumps(sorted(params.items())), 600, lambda: fetch(API + name + ".json?" + urllib.parse.urlencode(params))).decode("utf-8"))
    st = d.get("status")
    if st == "013":
        return {"status": "013", "list": []}
    if st != "000":
        raise RuntimeError("UPSTREAM_%s" % st)
    return d


def _ymd(s, default=None):
    s = s or default
    if not re.match(r"^\d{8}$", s or ""):
        raise ValueError("INVALID_YYYYMMDD")
    return s


# -- corp codes ------------------------------------------------------------------------------------
def _corp_table(data_dir):
    p = os.path.join(data_dir or ".", "dart", "corpCode.xml")
    fresh = os.path.isfile(p) and time.time() - os.path.getmtime(p) < 7 * 86400
    if not fresh:
        if not _key():
            raise KeyMissing()
        raw = fetch(API + "corpCode.xml?crtfc_key=" + _key())
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read([n for n in z.namelist() if n.lower().endswith(".xml")][0])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(xml)

    def load():
        rows = []
        for el in ET.parse(p).getroot().iter("list"):
            rows.append({"corp_code": (el.findtext("corp_code") or "").strip(), "corp_name": (el.findtext("corp_name") or "").strip(),
                         "stock_code": (el.findtext("stock_code") or "").strip(), "modify_date": (el.findtext("modify_date") or "").strip()})
        return rows
    return _cached("corp_table:" + str(int(os.path.getmtime(p))), 86400, load)


def corp(data_dir, name=None, stock_code=None, limit=20):
    rows = _corp_table(data_dir)
    if stock_code:
        hits = [r for r in rows if r["stock_code"] == stock_code.strip()]
    elif name:
        n = name.strip()
        exact = [r for r in rows if r["corp_name"] == n]
        hits = exact or [r for r in rows if n in r["corp_name"]]
        hits.sort(key=lambda r: (r["stock_code"] == "", len(r["corp_name"])))
    else:
        raise ValueError("name or stock_code required")
    return {"schema": "dart-corp/v1", "service": SERVICE_ID, "query": {"name": name, "stock_code": stock_code}, "count": len(hits), "matches": hits[:limit],
            "note": "listed companies have a stock_code; many namesakes are unlisted", "source": API + "corpCode.xml"}


# -- filings ---------------------------------------------------------------------------------------
def _classify(report_nm):
    nm = (report_nm or "").strip()
    kind = "ORIGINAL"
    base = nm
    changed = True
    while changed:
        changed = False
        for pre, k in PREFIX:
            if base.startswith(pre):
                base = base[len(pre):].strip()
                kind = k if kind == "ORIGINAL" or k in ("WITHDRAWAL",) else kind
                changed = True
    return kind, re.sub(r"\s+", " ", base)


def filings(corp_code, frm=None, to=None, last_report_only=False):
    if not re.match(r"^\d{8}$", corp_code or ""):
        raise ValueError("INVALID_CORP_CODE")
    today = datetime.date.today()
    to = _ymd(to, today.strftime("%Y%m%d"))
    frm = _ymd(frm, (today - datetime.timedelta(days=90)).strftime("%Y%m%d"))
    rows, page = [], 1
    while page <= 10:
        d = _api("list", corp_code=corp_code, bgn_de=frm, end_de=to, page_no=page, page_count=100, last_reprt_at="Y" if last_report_only else "N")
        rows += d.get("list") or []
        if page >= int(d.get("total_page") or 1):
            break
        page += 1
    out = []
    for r in rows:
        kind, base = _classify(r.get("report_nm"))
        out.append({"rcept_no": r.get("rcept_no"), "rcept_dt": r.get("rcept_dt"), "report_nm": (r.get("report_nm") or "").strip(), "base_name": base, "kind": kind,
                    "filer": r.get("flr_nm"), "market": r.get("corp_cls"), "remark": r.get("rm"), "corp_name": r.get("corp_name"),
                    "viewer": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=%s" % r.get("rcept_no")})
    out.sort(key=lambda x: x["rcept_no"])
    return {"schema": "dart-filings/v1", "service": SERVICE_ID, "corp_code": corp_code, "range": {"from": frm, "to": to}, "fetched_at_utc": _now(), "count": len(out), "filings": out}


def _link(filings_rows):
    """Correction -> most recent earlier filing with the same base name. Heuristic; DART exposes no explicit graph."""
    links = {}
    for i, f in enumerate(filings_rows):
        if f["kind"] in ("CORRECTION", "WITHDRAWAL", "ATTACHMENT", "EXTENSION", "TERMS_FIXED"):
            cands = [g for g in filings_rows[:i] if g["base_name"] == f["base_name"]]
            links[f["rcept_no"]] = cands[-1]["rcept_no"] if cands else None
    return links


def _term_diff(corp_code, base_name, orig_no, corr_no, frm, to):
    ep = next((v for k, v in TERM_APIS.items() if k in base_name), None)
    if not ep:
        return None
    d = _api(ep, corp_code=corp_code, bgn_de=frm, end_de=to)
    rows = {r.get("rcept_no"): r for r in d.get("list") or []}
    a, b = rows.get(orig_no), rows.get(corr_no)
    if not a or not b:
        return {"endpoint": ep, "status": "ROWS_NOT_FOUND", "have": sorted(rows.keys())[-5:]}
    skip = {"rcept_no", "corp_cls", "corp_code", "corp_name"}
    changed = [{"field": k, "before": a.get(k), "after": b.get(k)} for k in b if k not in skip and a.get(k) != b.get(k)]
    return {"endpoint": ep, "status": "OK", "changed_fields": changed, "unchanged_field_count": sum(1 for k in b if k not in skip and a.get(k) == b.get(k))}


def changes(corp_code, since=None, to=None):
    today = datetime.date.today()
    since = _ymd(since, (today - datetime.timedelta(days=30)).strftime("%Y%m%d"))
    to = _ymd(to, today.strftime("%Y%m%d"))
    # originals may predate `since`: look back 180 days for linking, report only events since `since`
    lookback = (datetime.datetime.strptime(since, "%Y%m%d") - datetime.timedelta(days=180)).strftime("%Y%m%d")
    fl = filings(corp_code, lookback, to)["filings"]
    links = _link(fl)
    by_no = {f["rcept_no"]: f for f in fl}
    events = []
    for f in fl:
        if f["rcept_dt"] < since:
            continue
        ev = {"rcept_no": f["rcept_no"], "rcept_dt": f["rcept_dt"], "kind": f["kind"], "report_nm": f["report_nm"], "base_name": f["base_name"], "viewer": f["viewer"]}
        if f["kind"] != "ORIGINAL":
            orig = links.get(f["rcept_no"])
            ev["correction_of"] = orig
            ev["link_status"] = "LINKED_BY_NAME" if orig else "UNLINKED"
            if orig:
                ev["original_rcept_dt"] = by_no[orig]["rcept_dt"]
                ev["term_diff"] = _term_diff(corp_code, f["base_name"], orig, f["rcept_no"], lookback, to)
        events.append(ev)
    counts = {}
    for e in events:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {"schema": "dart-changes/v1", "service": SERVICE_ID, "corp_code": corp_code, "since": since, "to": to, "fetched_at_utc": _now(), "counts": counts, "events": events,
            "judgments": {"completion_confirmed": False, "note": "a decision filing is a plan; payment, issuance or conversion completion is not inferred here"},
            "method": "kind from DART report-name prefixes; correction linked to the latest earlier filing with the same base name (heuristic); term diffs from OpenDART structured endpoints where the report type has one"}


def asof(corp_code, date, lookback_days=120):
    date = _ymd(date)
    frm = (datetime.datetime.strptime(date, "%Y%m%d") - datetime.timedelta(days=lookback_days)).strftime("%Y%m%d")
    fl = [f for f in filings(corp_code, frm, date)["filings"] if frm <= (f.get("rcept_dt") or "") <= date]  # defensive: never admit later receipts
    links = _link(fl)
    superseded = set(v for v in links.values() if v)
    effective = [f for f in fl if f["rcept_no"] not in superseded and f["kind"] != "WITHDRAWAL"]
    withdrawn = [links[f["rcept_no"]] for f in fl if f["kind"] == "WITHDRAWAL" and links.get(f["rcept_no"])]
    effective = [f for f in effective if f["rcept_no"] not in withdrawn]
    return {"schema": "dart-asof/v1", "service": SERVICE_ID, "corp_code": corp_code, "as_of": date, "lookback_days": lookback_days, "fetched_at_utc": _now(),
            "knowable_filings": len(fl), "effective_filings": effective[-100:], "superseded_count": len(superseded), "withdrawn_count": len(withdrawn),
            "rule": "only filings received on or before as_of; a filing superseded by a same-name correction on or before as_of is replaced by that correction; later corrections are excluded"}


INTENTS = ("CHANGES", "FILINGS", "ASOF", "CORP", "UNKNOWN")


def parse_question(q):
    q = q if isinstance(q, str) else ""
    if len(q) > 2000:
        raise ValueError("INVALID_QUESTION")
    date = re.search(r"\b(20\d{6})\b", q)
    stock = next((m for m in re.finditer(r"(?<!\d)(\d{6})(?!\d)", q) if not date or m.start() != date.start()), None)
    name = None
    m = re.search(r"([가-힣A-Za-z0-9&·]+?)(?:의|에 대해|에 대한|\s+(?:공시|정정|filings?|changes?))", q)
    if m and len(m.group(1)) >= 2:
        name = m.group(1)
    intent = "UNKNOWN"
    if re.search(r"as of|시점|당시|그때|알 수 있었", q, re.I) and date:
        intent = "ASOF"
    elif re.search(r"정정|바뀐|바뀌|변경|changed|correct|what changed|since", q, re.I):
        intent = "CHANGES"
    elif re.search(r"공시|filing|disclos|목록|list", q, re.I):
        intent = "FILINGS"
    elif re.search(r"corp.?code|코드|종목|찾아|lookup|which company", q, re.I):
        intent = "CORP"
    return {"intent": intent, "name": name, "stock_code": stock.group(1) if stock else None, "date": date.group(1) if date else None}


def answer(body, data_dir=None):
    body = body if isinstance(body, dict) else {}
    q = body.get("question") or ""
    p = parse_question(q)
    intent = p["intent"]
    res = {"schema": "dart-answer/v1", "service": SERVICE_ID, "intent": intent, "question": q[:300], "status": "OK"}
    if intent == "UNKNOWN":
        res.update(status="NEEDS_CLARIFICATION", intents=list(INTENTS))
        return res
    corp_code = body.get("corp_code")
    if not corp_code and (body.get("name") or body.get("stock_code") or p["name"] or p["stock_code"]):
        c = corp(data_dir, body.get("name") or p["name"], body.get("stock_code") or p["stock_code"])
        listed = [m for m in c["matches"] if m["stock_code"]] or c["matches"]
        if not listed:
            res.update(status="NOT_FOUND", corp_lookup=c)
            return res
        corp_code = listed[0]["corp_code"]
        res["corp"] = listed[0]
        if intent == "CORP":
            res.update(data=c)
            return res
    if not corp_code:
        res.update(status="NEEDS_CLARIFICATION", missing=["corp_code or name or stock_code"])
        return res
    if intent == "ASOF":
        res.update(data=asof(corp_code, body.get("date") or p["date"]))
    elif intent == "FILINGS":
        res.update(data=filings(corp_code, body.get("from"), body.get("to")))
    else:
        res.update(data=changes(corp_code, body.get("since"), body.get("to")))
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
            return self._json(503, {"service": SERVICE_ID, "status": "KEY_NOT_CONFIGURED", "detail": "OPENDART_API_KEY is not set on this supplier"})
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
            return self._json(200, {"service": SERVICE_ID, "version": VERSION, "publisher": "Financial Supervisory Service OpenDART", "key_configured": bool(_key())})
        if p == "/v1/health":
            return self._json(200, {"status": "ok", "service": SERVICE_ID, "time_utc": _now()})
        if p == "/v1/corp":
            return self._run(lambda: corp(self.data_dir, qd.get("name"), qd.get("stock_code")))
        if p == "/v1/filings":
            return self._run(lambda: filings(qd.get("corp_code"), qd.get("from"), qd.get("to"), qd.get("last") == "1"))
        if p == "/v1/changes":
            return self._run(lambda: changes(qd.get("corp_code"), qd.get("since"), qd.get("to")))
        if p == "/v1/asof":
            return self._run(lambda: asof(qd.get("corp_code"), qd.get("date"), max(7, min(int(qd.get("lookback_days") or 120), 365))))
        return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})

    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        code = 200 if p in ("/", "/index.html", "/openapi.json", "/v1/version", "/v1/health", "/v1/corp", "/v1/filings", "/v1/changes", "/v1/asof") else 404
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


def serve(data_dir, host="0.0.0.0", port=8798):
    _Handler.data_dir = data_dir
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()


def service_card(base="https://dart.pokt-agent.com"):
    return {
        "schema": "pocket-service-card/v1",
        "description": "DART KR Events: what changed for a Korean listed company since a date, which filings were corrected or withdrawn, and "
                       "what the terms were as knowable on a given day. Over OpenDART: filings normalised with kind ORIGINAL | CORRECTION | "
                       "WITHDRAWAL | ATTACHMENT, corrections linked to their originals (heuristic, stated), field-by-field term diffs for rights "
                       "offerings, bonus issues, convertible, warrant and exchangeable bonds, and a point-in-time view that excludes later corrections. "
                       "Plans are never reported as completed. Observed filings only. Every response is a JSON object. Identity via GET /v1/version, "
                       "readiness via GET /v1/health, function via GET /v1/corp, /v1/filings, /v1/changes, /v1/asof and POST /v1/query (ko/en).",
        "updated": datetime.date.today().isoformat(),
        "rpc_types": [{"type": "REST", "intent": "expected", "backend_hint": "%s HTTP server on :8798; mount at /" % SERVICE_ID,
                       "notes": "Supplier holds its own OpenDART key (not shared with callers). Without a key the backend answers 503 KEY_NOT_CONFIGURED. corp_code is the 8-digit DART code; look it up via /v1/corp."}],
        "apis": ["%s-api" % SERVICE_ID],
        "specs": [{"kind": "openapi", "api": "%s-api" % SERVICE_ID, "url": "%s/openapi.json" % base, "notes": "Served by the same backend."}],
        "docs": base + "/",
        "access": "public",
        "results": "variable",
        "serving": {
            "backend": "Stateless proxy over OpenDART list/corpCode/issuance endpoints with a 10 min cache and a weekly corp table; supplier-side key.",
            "implementations": ["%s >= %s" % (SERVICE_ID, VERSION)], "min_disk_gb": 1, "min_ram_gb": 1,
            "healthcheck": [
                {"rpc_type": "REST", "request": {"path": "/v1/version", "method": "GET"}, "expect": {"json_path": "$.service", "matches": "^%s$" % SERVICE_ID}, "notes": "Identity probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/health", "method": "GET"}, "expect": {"json_path": "$.status", "matches": "^ok$"}, "notes": "Readiness probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/changes?corp_code=00126380", "method": "GET"}, "expect": {"json_path": "$.schema", "matches": "^dart-changes/v1$"},
                 "notes": "Functional probe: Samsung Electronics changes in the last 30 days with correction links."},
                {"rpc_type": "REST", "request": {"path": "/v1/query", "method": "POST", "headers": {"content-type": "application/json"}, "body": {"question": "삼성전자 정정공시 뭐가 바뀌었어?"}},
                 "expect": {"json_path": "$.intent", "matches": "^CHANGES$"}, "notes": "Functional probe: Korean question routed deterministically with name lookup."},
            ],
        },
    }


def openapi_spec(base="https://dart.pokt-agent.com"):
    cc = {"name": "corp_code", "in": "query", "required": True, "schema": {"type": "string", "pattern": "^\\d{8}$"}}
    return {
        "openapi": "3.0.3",
        "info": {"title": "DART KR Events", "version": VERSION, "description": "Korean corporate filings as change tracking: corrections, term diffs, point-in-time. Observed filings only."},
        "servers": [{"url": base}],
        "paths": {
            "/v1/version": {"get": {"summary": "Identity", "responses": {"200": {"description": "service id, version, key_configured"}}}},
            "/v1/health": {"get": {"summary": "Readiness", "responses": {"200": {"description": "{status: ok}"}}}},
            "/v1/corp": {"get": {"summary": "corp_code lookup by name or stock code", "parameters": [{"name": "name", "in": "query", "schema": {"type": "string"}}, {"name": "stock_code", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "dart-corp/v1"}}}},
            "/v1/filings": {"get": {"summary": "Normalised filings with kind", "parameters": [cc, {"name": "from", "in": "query", "schema": {"type": "string"}}, {"name": "to", "in": "query", "schema": {"type": "string"}}], "responses": {"200": {"description": "dart-filings/v1"}}}},
            "/v1/changes": {"get": {"summary": "Corrections/withdrawals since a date with linked originals and term diffs", "parameters": [cc, {"name": "since", "in": "query", "schema": {"type": "string", "pattern": "^\\d{8}$"}}], "responses": {"200": {"description": "dart-changes/v1"}}}},
            "/v1/asof": {"get": {"summary": "Filings effective as of a date (later corrections excluded)", "parameters": [cc, {"name": "date", "in": "query", "required": True, "schema": {"type": "string", "pattern": "^\\d{8}$"}}], "responses": {"200": {"description": "dart-asof/v1"}}}},
            "/v1/query": {"post": {"summary": "Deterministic question (ko/en)", "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"question": {"type": "string"}, "corp_code": {"type": "string"}, "name": {"type": "string"}, "stock_code": {"type": "string"}, "since": {"type": "string"}, "date": {"type": "string"}}}}}},
                "responses": {"200": {"description": "dart-answer/v1"}}}},
        },
    }


UI_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DART KR Events</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}code{background:#f4f4f6;border-radius:4px;padding:.1rem .3rem}h1{font-size:1.5rem}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}</style></head>
<body><h1>DART KR Events <small>v%s</small></h1>
<p>Korean corporate filings as change tracking over OpenDART: corrections linked to originals, field-by-field term diffs for issuance decisions, and point-in-time views that exclude later corrections. Plans are never reported as completed. Every response a JSON object.</p>
<table><tr><th>Endpoint</th><th>Purpose</th></tr>
<tr><td><a href="/v1/corp?name=삼성전자">GET /v1/corp?name=삼성전자</a></td><td>corp_code lookup</td></tr>
<tr><td><a href="/v1/changes?corp_code=00126380">GET /v1/changes?corp_code=00126380</a></td><td>corrections since date, linked originals, term diffs</td></tr>
<tr><td><a href="/v1/filings?corp_code=00126380">GET /v1/filings?corp_code=…</a></td><td>normalised filings with kind</td></tr>
<tr><td><code>GET /v1/asof?corp_code=…&date=YYYYMMDD</code></td><td>what was knowable on that date</td></tr>
<tr><td><code>POST /v1/query</code></td><td><code>{"question":"삼성전자 정정공시 뭐가 바뀌었어?"}</code></td></tr>
<tr><td><a href="/openapi.json">GET /openapi.json</a></td><td>OpenAPI 3 spec</td></tr></table>
<p>Service id <code>%s</code> on Pocket Network.</p></body></html>""" % (VERSION, SERVICE_ID)
