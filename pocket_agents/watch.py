"""POKT Network Watch v0 (track D, master-map agent #1 as a product) - HTTP service on the Mac.

Answers "what changed on Pocket Network, and can you prove it" from data the always-on collector and the
surveillance job already write under data/pokt/: chain head + governance params (STATE.json), active alerts
(ALERTS.json), 15 official sources with per-fetch receipts (surveillance/STATUS.json, EVENTS.ndjson).
Deterministic per snapshot: every response carries as_of (run ids) and nothing is estimated or forecast.

Endpoints (REST, JSON object always):
  GET  /v1/version   GET /v1/health   GET /openapi.json
  GET  /v1/network/status        chain head, params, active alerts, source summary
  GET  /v1/sources               the watched sources with last fetch, change class, sha256
  GET  /v1/events?limit&source   change events (newest last), each bound to a receipt + sha256
  GET  /v1/events/{event_id}/verify   receipt <-> snapshot hash check
  POST /v1/watch {question?, hours?, source_id?, event_id?}   deterministic answer over the same data
Never: keys, signing, tx, prices, income.
"""
import datetime
import http.server
import json
import os
import re
import socketserver

from . import surveillance

SERVICE_ID = "pokt-network-watch-v1"
VERSION = "0.1.0"
UTC = datetime.timezone.utc
MAX_LIMIT = 200
PARAM_KEYS = ("shared", "supplier", "application", "service", "tokenomics", "proof", "staking", "mint")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _state(data_dir):
    return _load(os.path.join(data_dir, "pokt", "STATE.json")) or {}


def _alerts(data_dir):
    return _load(os.path.join(data_dir, "pokt", "ALERTS.json")) or {"active": [], "resolved": []}


def _as_of(data_dir):
    st = _state(data_dir)
    sv = surveillance.status(data_dir) or {}
    return {"collector_run_id": st.get("run_id"), "collector_at_utc": st.get("generated_at_utc"),
            "surveillance_run_id": sv.get("run_id"), "surveillance_at_utc": sv.get("at_utc")}


def network_status(data_dir):
    st = _state(data_dir)
    al = _alerts(data_dir)
    sv = surveillance.status(data_dir) or {}
    chain = st.get("chain") or {}
    params = {k: v for k, v in (st.get("params") or {}).items() if k in PARAM_KEYS}
    econ = st.get("network_economics")
    return {
        "schema": "pokt-network-watch-status/v1", "service": SERVICE_ID, "as_of": _as_of(data_dir),
        "chain": {"chain_id": chain.get("chain_id"), "height": chain.get("height"), "time_utc": chain.get("time_utc")},
        "params": params,
        "alerts": {"active": al.get("active") or [], "active_count": len(al.get("active") or []), "resolved_count": len(al.get("resolved") or [])},
        "sources": {"count": sv.get("sources"), "changed_last_run": sv.get("changed"), "first_seen_last_run": sv.get("first"),
                    "unreachable_last_run": sv.get("failed"), "at_utc": sv.get("at_utc")},
        "network_economics": econ.get("summary") if isinstance(econ, dict) else None,
    }


def sources(data_dir):
    sv = surveillance.status(data_dir) or {}
    rows = []
    for r in sv.get("results") or []:
        rows.append({k: r.get(k) for k in ("id", "url", "class", "tracks", "fetched_at_utc", "http_status", "bytes", "change", "sha256", "text_sha256") if k in r})
    return {"schema": "pokt-network-watch-sources/v1", "service": SERVICE_ID, "as_of": _as_of(data_dir), "count": len(rows), "sources": rows}


def events(data_dir, limit=50, source_id=None, since_utc=None, only_changes=True):
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    rows = surveillance.history(data_dir, limit=100000, only_changes=only_changes)
    if source_id:
        rows = [r for r in rows if r.get("source_id") == source_id]
    if since_utc:
        rows = [r for r in rows if (r.get("at_utc") or "") >= since_utc]
    rows = rows[-limit:]
    out = [{k: r.get(k) for k in ("event_id", "at_utc", "source_id", "url", "class", "tracks", "change", "added_lines", "removed_lines",
                                  "added_sample", "removed_sample", "receipt", "sha256", "text_sha256") if k in r} for r in rows]
    return {"schema": "pokt-network-watch-events/v1", "service": SERVICE_ID, "as_of": _as_of(data_dir), "count": len(out),
            "filter": {"limit": limit, "source_id": source_id, "since_utc": since_utc, "only_changes": only_changes}, "events": out}


def verify(data_dir, event_id):
    for r in surveillance.history(data_dir, limit=100000, only_changes=False):
        if r.get("event_id") == event_id:
            res = surveillance.verify_receipt(data_dir, r)
            return dict(res, service=SERVICE_ID, source_id=r.get("source_id"), at_utc=r.get("at_utc"), sha256=r.get("sha256"), text_sha256=r.get("text_sha256"))
    return None


INTENTS = ("WHAT_CHANGED", "ALERTS", "PARAMS", "SOURCES", "VERIFY", "STATUS")


def parse_question(q):
    """Deterministic rules; the LLM never decides intent. Korean and English."""
    q = q if isinstance(q, str) else ""
    if len(q) > 4000:
        raise ValueError("INVALID_QUESTION")
    hours = None
    m = re.search(r"(\d+)\s*(?:h\b|hours?|시간)", q, re.I)
    if m:
        hours = int(m.group(1))
    elif re.search(r"\b24h\b|\btoday\b|오늘|하루", q, re.I):
        hours = 24
    elif re.search(r"week|주간|일주일|7\s*일", q, re.I):
        hours = 168
    ev = re.search(r"([a-z0-9-]+-\d{8}T\d{6}Z)", q)
    intent = "STATUS"
    if ev or re.search(r"verify|receipt|증빙|검증|영수증", q, re.I):
        intent = "VERIFY"
    elif re.search(r"alert|경보|알림|이상", q, re.I):
        intent = "ALERTS"
    elif re.search(r"param|파라미터|min_stake|\bfee\b|수수료|거버넌스|governance", q, re.I):
        intent = "PARAMS"
    elif re.search(r"\bsources?\b|출처|감시 대상|watch ?list", q, re.I):
        intent = "SOURCES"
    elif re.search(r"chang|diff|updat|바뀌|바뀐|바꾸|변경|변화|업데이트|새로|what.?s new", q, re.I):
        intent = "WHAT_CHANGED"
    return {"intent": intent, "hours": hours, "event_id": ev.group(1) if ev else None}


def answer(data_dir, body):
    body = body if isinstance(body, dict) else {}
    q = body.get("question") or ""
    parsed = parse_question(q)
    hours = body.get("hours") or parsed["hours"] or 24
    try:
        hours = max(1, min(int(hours), 24 * 90))
    except (TypeError, ValueError):
        raise ValueError("INVALID_HOURS")
    source_id = body.get("source_id")
    now = datetime.datetime.now(UTC)
    since = (now - datetime.timedelta(hours=hours)).isoformat()
    intent = parsed["intent"]
    res = {"schema": "pokt-network-watch-answer/v1", "service": SERVICE_ID, "intent": intent, "question": q[:500], "window_hours": hours,
           "since_utc": since, "as_of": _as_of(data_dir), "status": "OK"}
    if intent == "VERIFY":
        eid = body.get("event_id") or parsed["event_id"]
        if not eid:
            res.update(status="NEEDS_CLARIFICATION", missing=["event_id"])
            return res
        v = verify(data_dir, eid)
        res.update(verification=v, status="OK" if v else "NOT_FOUND")
        return res
    if intent == "ALERTS":
        al = _alerts(data_dir)
        res.update(alerts_active=al.get("active") or [], alerts_resolved_recent=(al.get("resolved") or [])[-10:])
        return res
    if intent == "PARAMS":
        ns = network_status(data_dir)
        res.update(params=ns["params"], chain=ns["chain"])
        return res
    if intent == "SOURCES":
        res.update(sources=sources(data_dir)["sources"])
        return res
    ev = events(data_dir, limit=MAX_LIMIT, source_id=source_id, since_utc=since)
    res.update(changed_sources=sorted({e["source_id"] for e in ev["events"]}), events=ev["events"], event_count=ev["count"])
    if intent == "STATUS":
        res.update(network=network_status(data_dir))
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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        out = {}
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k] = v
        return out

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        try:
            if p in ("/", "/index.html"):
                return self._html(200, UI_HTML)
            if p == "/openapi.json":
                return self._json(200, openapi_spec())
            if p == "/v1/version":
                return self._json(200, {"service": SERVICE_ID, "version": VERSION, "chain_id": (_state(self.data_dir).get("chain") or {}).get("chain_id", "pocket")})
            if p == "/v1/health":
                sv = surveillance.status(self.data_dir)
                return self._json(200, {"status": "ok", "service": SERVICE_ID, "time_utc": datetime.datetime.now(UTC).isoformat(), "surveillance_at_utc": (sv or {}).get("at_utc")})
            if p == "/v1/network/status":
                return self._json(200, network_status(self.data_dir))
            if p == "/v1/sources":
                return self._json(200, sources(self.data_dir))
            if p == "/v1/events":
                qd = self._query()
                return self._json(200, events(self.data_dir, limit=qd.get("limit", 50), source_id=qd.get("source"), since_utc=qd.get("since"), only_changes=qd.get("all") != "1"))
            m = re.match(r"^/v1/events/([A-Za-z0-9._-]+)/verify$", p)
            if m:
                v = verify(self.data_dir, m.group(1))
                return self._json(200, v) if v else self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND", "event_id": m.group(1)})
            return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        except Exception as e:  # never leak a traceback
            return self._json(500, {"service": SERVICE_ID, "status": "INTERNAL", "detail": type(e).__name__})

    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        code = 200 if p in ("/", "/index.html", "/openapi.json", "/v1/version", "/v1/health", "/v1/network/status", "/v1/sources", "/v1/events") else 404
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
        if p not in ("/v1/watch", "/", ""):
            return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                return self._json(413, {"service": SERVICE_ID, "status": "BODY_TOO_LARGE"})
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            return self._json(200, answer(self.data_dir, body))
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        except Exception as e:
            return self._json(500, {"service": SERVICE_ID, "status": "INTERNAL", "detail": type(e).__name__})


def serve(data_dir, host="0.0.0.0", port=8794):
    _Handler.data_dir = data_dir
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()


def service_card(base="https://watch.pokt-agent.com"):
    return {
        "schema": "pocket-service-card/v1",
        "description": "POKT Network Watch: what changed on Pocket Network and the proof of it. Chain head and governance parameters, active "
                       "network alerts, and change events over 15 official sources (roadmap, docs, agent portal, explorer, GitHub, analytics), "
                       "each event bound to a fetch receipt and SHA-256 that can be re-verified. Deterministic per snapshot (as_of run ids); "
                       "observed values only, no forecasts, prices or income. Every response is a JSON object. Identity via GET /v1/version, "
                       "readiness via GET /v1/health, function via GET /v1/network/status, GET /v1/sources, GET /v1/events, "
                       "GET /v1/events/{id}/verify and POST /v1/watch {question, hours?, source_id?}.",
        "updated": datetime.date.today().isoformat(),
        "rpc_types": [{"type": "REST", "intent": "expected", "backend_hint": "%s HTTP server on :8794; mount at /" % SERVICE_ID,
                       "notes": "GET endpoints need no body; POST /v1/watch takes {question?, hours?, source_id?, event_id?} and answers with events[], alerts or params."}],
        "apis": ["%s-api" % SERVICE_ID],
        "specs": [{"kind": "openapi", "api": "%s-api" % SERVICE_ID, "url": "%s/openapi.json" % base, "notes": "Served by the same backend."}],
        "docs": base + "/",
        "access": "public",
        "results": "variable",
        "serving": {
            "backend": "Read-only over public Pocket MainNet REST/RPC plus HTTP GET of official Pocket web/docs/GitHub/analytics pages; receipts stored per fetch. No keys.",
            "implementations": ["%s >= %s" % (SERVICE_ID, VERSION)], "min_disk_gb": 1, "min_ram_gb": 1,
            "healthcheck": [
                {"rpc_type": "REST", "request": {"path": "/v1/version", "method": "GET"}, "expect": {"json_path": "$.service", "matches": "^%s$" % SERVICE_ID},
                 "notes": "Identity probe: pins the backend to this service id."},
                {"rpc_type": "REST", "request": {"path": "/v1/health", "method": "GET"}, "expect": {"json_path": "$.status", "matches": "^ok$"}, "notes": "Readiness probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/sources", "method": "GET"}, "expect": {"json_path": "$.schema", "matches": "^pokt-network-watch-sources/v1$"},
                 "notes": "Functional probe: the watched-source list with last fetch receipts."},
                {"rpc_type": "REST", "request": {"path": "/v1/watch", "method": "POST", "headers": {"content-type": "application/json"},
                                                  "body": {"question": "What changed on Pocket Network in the last 24 hours?", "hours": 24}},
                 "expect": {"json_path": "$.intent", "matches": "^WHAT_CHANGED$"}, "notes": "Functional probe: deterministic intent + events[] bound to receipts."},
            ],
        },
    }


def openapi_spec(base="https://watch.pokt-agent.com"):
    return {
        "openapi": "3.0.3",
        "info": {"title": "POKT Network Watch", "version": VERSION,
                 "description": "What changed on Pocket Network (chain params, alerts, official sources) with per-event receipts and SHA-256 evidence. Observed values only."},
        "servers": [{"url": base}],
        "paths": {
            "/v1/version": {"get": {"summary": "Identity", "responses": {"200": {"description": "service id and version"}}}},
            "/v1/health": {"get": {"summary": "Readiness", "responses": {"200": {"description": "{status: ok}"}}}},
            "/v1/network/status": {"get": {"summary": "Chain head, governance params, active alerts, source summary", "responses": {"200": {"description": "pokt-network-watch-status/v1"}}}},
            "/v1/sources": {"get": {"summary": "Watched official sources with last fetch, change class, sha256", "responses": {"200": {"description": "pokt-network-watch-sources/v1"}}}},
            "/v1/events": {"get": {"summary": "Change events, newest last, each bound to a receipt",
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "maximum": MAX_LIMIT}}, {"name": "source", "in": "query", "schema": {"type": "string"}},
                               {"name": "since", "in": "query", "schema": {"type": "string", "format": "date-time"}}, {"name": "all", "in": "query", "schema": {"type": "string", "enum": ["1"]}, "description": "include UNCHANGED fetches"}],
                "responses": {"200": {"description": "pokt-network-watch-events/v1"}}}},
            "/v1/events/{event_id}/verify": {"get": {"summary": "Re-verify an event's receipt against the stored snapshot",
                "parameters": [{"name": "event_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "VERIFIED | RECEIPT_MISMATCH | RECEIPT_MISSING"}, "404": {"description": "unknown event"}}}},
            "/v1/watch": {"post": {"summary": "Deterministic question over the same data", "requestBody": {"required": False, "content": {"application/json": {"schema": {
                "type": "object", "properties": {"question": {"type": "string", "example": "What changed in the last 24 hours?"}, "hours": {"type": "integer", "default": 24},
                                                 "source_id": {"type": "string"}, "event_id": {"type": "string"}}}}}},
                "responses": {"200": {"description": "pokt-network-watch-answer/v1 with intent, events[] / alerts / params / verification"}, "400": {"description": "bad request"}}}},
        },
    }


UI_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>POKT Network Watch</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
code,pre{background:#f4f4f6;border-radius:4px;padding:.1rem .3rem}pre{padding:.8rem;overflow:auto}h1{font-size:1.5rem}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}</style></head>
<body><h1>POKT Network Watch <small>v%s</small></h1>
<p>What changed on Pocket Network, with the proof: chain parameters, active alerts and change events over 15 official sources, each bound to a fetch receipt and SHA-256. Observed values only. Every response is a JSON object.</p>
<table><tr><th>Endpoint</th><th>Purpose</th></tr>
<tr><td><a href="/v1/network/status">GET /v1/network/status</a></td><td>chain head, params, active alerts, source summary</td></tr>
<tr><td><a href="/v1/sources">GET /v1/sources</a></td><td>watched sources, last fetch, change class, sha256</td></tr>
<tr><td><a href="/v1/events?limit=20">GET /v1/events</a></td><td>change events (newest last), receipt-bound</td></tr>
<tr><td><code>GET /v1/events/{id}/verify</code></td><td>re-verify a receipt against the stored snapshot</td></tr>
<tr><td><code>POST /v1/watch</code></td><td><code>{"question":"What changed in the last 24 hours?"}</code></td></tr>
<tr><td><a href="/openapi.json">GET /openapi.json</a></td><td>OpenAPI 3 spec</td></tr></table>
<p>Service id <code>%s</code> on Pocket Network. Sibling service: <a href="https://pokt-agent.com/">POKT Settlement Agent</a>.</p></body></html>""" % (VERSION, SERVICE_ID)
