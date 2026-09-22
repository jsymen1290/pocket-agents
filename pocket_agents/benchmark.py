"""Agent Service Benchmark v0 (agent-service-benchmark-v1) - the Trust Layer entry point.

Question: "Of the portal services that could answer my task, which should I call, and what is the evidence?"

An agent facing 83 services (and a stated goal of 1,000 by year end) has no free way to compare them. This
service answers from three public sources, none of which costs a paid call:
  portal catalogue   https://agent.pocket.network/services.json   (id, category, price, serving, schemas, example capturedAt, firstParty)
  portal status      https://agent.pocket.network/status.json     (registryVersion, serving counts, checkedAt)
  official audit     https://mcp.pocketmcp.network/api/audit      (the 9 acceptance rules A1-A9 per service)

It reports evidence and orders candidates by a rule it states in every response. It does not invent a quality
score, does not claim to measure correctness or uptime history, and never calls a paid endpoint on the
caller's behalf. Category competition density is reported because it is the single largest driver of how
often any one service gets chosen.

Endpoints (REST, JSON object always):
  GET  /v1/version  GET /v1/health  GET /openapi.json
  GET  /v1/catalogue?category=&serving=          portal snapshot, one row per service
  GET  /v1/categories                            competition density per category
  GET  /v1/service/{id}                          one service: portal record + audit verdict + density
  POST /v1/compare {category|ids|question, audit?}  ranked candidates with evidence and the ordering rule
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

SERVICE_ID = "agent-service-benchmark-v1"
VERSION = "0.1.0"
UTC = datetime.timezone.utc
UA = "agent-service-benchmark-v1/%s (Pocket Network service; read-only)" % VERSION
CATALOGUE_URL = os.environ.get("POKT_PORTAL_SERVICES_URL", "https://agent.pocket.network/services.json")
STATUS_URL = os.environ.get("POKT_PORTAL_STATUS_URL", "https://agent.pocket.network/status.json")
AUDIT_URL = os.environ.get("POKT_AUDIT_URL", "https://mcp.pocketmcp.network/api/audit")
CATALOGUE_TTL = 900
AUDIT_TTL = 900
AUDIT_MAX_IDS = 50
MAX_COMPARE = 25
VERDICT_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2, "UNKNOWN": 3}
ORDERING_RULE = ("1) audit verdict PASS before WARN before FAIL before UNKNOWN, "
                 "2) serving true before false, "
                 "3) lower priceUsd first, "
                 "4) fresher portal example (capturedAt) first, "
                 "5) serviceId alphabetically as the final tie-break. "
                 "Every input to this ordering is returned alongside each candidate so the caller can re-rank.")
NOT_MEASURED = ["response correctness", "uptime history", "error rate under load",
                "whether the upstream data licence permits the caller's use",
                "how the portal actually routes or discovers services"]
_cache = {}
_lock = threading.Lock()


class UpstreamUnavailable(Exception):
    pass


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(32 * 1024 * 1024).decode("utf-8"))


get = _get  # injectable for tests


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


def _age_days(iso):
    if not iso:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((datetime.datetime.now(UTC) - t).total_seconds() / 86400.0, 2)


def _fetch_catalogue():
    def load():
        cat = get(CATALOGUE_URL)
        try:
            st = get(STATUS_URL)
        except Exception:
            st = {}
        return {"catalogue": cat, "status": st, "fetched_at_utc": _now()}
    try:
        return _cached("catalogue", CATALOGUE_TTL, load)
    except Exception as e:
        raise UpstreamUnavailable("%s: %s" % (type(e).__name__, str(e)[:120]))


def _row(s, density):
    cat = s.get("category")
    return {
        "service_id": s.get("serviceId"),
        "display_name": s.get("displayName"),
        "category": cat,
        "price_usd": float(s.get("priceUsd")) if s.get("priceUsd") is not None else None,
        "serving": s.get("serving"),
        "first_party": s.get("firstParty"),
        "resource_url": s.get("resourceUrl"),
        "page": s.get("page"),
        "protocols": s.get("protocols"),
        "methods": sorted((s.get("methods") or {}).keys()),
        "has_input_schema": bool(s.get("inputSchema")),
        "has_output_schema": bool(s.get("outputSchema")),
        "portal_example_captured_at": (s.get("example") or {}).get("capturedAt"),
        "portal_example_age_days": _age_days((s.get("example") or {}).get("capturedAt")),
        "payment_rails": [{"id": r.get("id"), "network": r.get("network")} for r in (s.get("rails") or [])],
        "category_competing_services": density.get(cat),
        "description": (s.get("description") or "")[:300],
    }


def _density(services):
    d = {}
    for s in services:
        c = s.get("category")
        d[c] = d.get(c, 0) + 1
    return d


def catalogue(category=None, serving=None):
    snap = _fetch_catalogue()
    cat = snap["catalogue"]
    services = cat.get("services") or []
    density = _density(services)
    rows = [_row(s, density) for s in services]
    if category:
        rows = [r for r in rows if (r["category"] or "").lower() == category.lower()]
    if serving is not None:
        rows = [r for r in rows if bool(r["serving"]) is bool(serving)]
    rows.sort(key=lambda r: (r["category"] or "", r["service_id"] or ""))
    st = snap["status"] or {}
    return {
        "schema": "agent-service-catalogue/v1", "service": SERVICE_ID, "fetched_at_utc": snap["fetched_at_utc"],
        "portal": {"registry_version": cat.get("registryVersion") or st.get("registryVersion"),
                   "status_checked_at": st.get("checkedAt"), "overall": st.get("overall"),
                   "count_reported": cat.get("count"), "serving_reported": (st.get("services") or {}).get("serving"),
                   "x402_discovery": cat.get("x402Discovery")},
        "filter": {"category": category, "serving": serving},
        "count": len(rows), "services": rows,
        "sources": [CATALOGUE_URL, STATUS_URL],
        "note": "registry_version and status_checked_at identify this snapshot. Two snapshots with different registry versions are not the same cross-section.",
    }


def categories():
    snap = _fetch_catalogue()
    services = (snap["catalogue"].get("services") or [])
    density = _density(services)
    total = sum(density.values()) or 1
    rows = []
    for c, n in sorted(density.items(), key=lambda kv: -kv[1]):
        prices = [float(s["priceUsd"]) for s in services if s.get("category") == c and s.get("priceUsd") is not None]
        rows.append({"category": c, "services": n, "share_of_catalogue_pct": round(n / total * 100, 2),
                     "price_usd_min": min(prices) if prices else None, "price_usd_max": max(prices) if prices else None,
                     "first_party_services": sum(1 for s in services if s.get("category") == c and s.get("firstParty")),
                     "serving": sum(1 for s in services if s.get("category") == c and s.get("serving"))})
    return {"schema": "agent-service-categories/v1", "service": SERVICE_ID, "fetched_at_utc": snap["fetched_at_utc"],
            "registry_version": snap["catalogue"].get("registryVersion"), "total_services": total,
            "categories": rows,
            "why_this_matters": "A service competes inside its category. The same effort in a category with 4 peers and one with 47 peers does not yield the same chance of being chosen.",
            "not_measured": ["actual request share per category (the portal publishes service counts, not request counts)"],
            "sources": [CATALOGUE_URL]}


def audit(ids, network="main"):
    ids = [i for i in ids if re.match(r"^[a-z0-9][a-z0-9-]{1,62}$", str(i or ""))][:AUDIT_MAX_IDS]
    if not ids:
        return {}
    key = "audit:%s:%s" % (network, ",".join(sorted(ids)))

    def load():
        url = "%s?network=%s&ids=%s" % (AUDIT_URL, urllib.parse.quote(network), urllib.parse.quote(",".join(ids)))
        d = get(url, timeout=90)
        out = {}
        for s in d.get("services") or []:
            rules = s.get("rules") or {}
            out[s.get("id")] = {"verdict": s.get("verdict"), "rules": rules,
                                "failed_rules": [f.get("rule") for f in (s.get("findings") or []) if f.get("level") == "FAIL"],
                                "findings": [{"rule": f.get("rule"), "level": f.get("level"), "message": (f.get("message") or "")[:200]}
                                             for f in (s.get("findings") or [])],
                                "audited_at": d.get("audited_at"), "network": network}
        return out
    try:
        return _cached(key, AUDIT_TTL, load)
    except Exception as e:
        raise UpstreamUnavailable("audit %s: %s" % (type(e).__name__, str(e)[:120]))


def service(service_id, network="main"):
    c = catalogue()
    row = next((r for r in c["services"] if r["service_id"] == service_id), None)
    if row is None:
        return {"schema": "agent-service-detail/v1", "service": SERVICE_ID, "service_id": service_id,
                "status": "NOT_IN_PORTAL_CATALOGUE", "fetched_at_utc": c["fetched_at_utc"],
                "registry_version": c["portal"]["registry_version"],
                "note": "the portal catalogue for this registry version does not list this service id"}
    a = {}
    audit_status = "OK"
    try:
        a = audit([service_id], network).get(service_id) or {}
    except UpstreamUnavailable as e:
        audit_status = str(e)[:80]
    return {"schema": "agent-service-detail/v1", "service": SERVICE_ID, "fetched_at_utc": c["fetched_at_utc"],
            "registry_version": c["portal"]["registry_version"], "status": "OK",
            "portal_record": row, "audit": a or None, "audit_status": audit_status,
            "not_measured": NOT_MEASURED, "sources": [CATALOGUE_URL, STATUS_URL, AUDIT_URL]}


def _rank_key(cand):
    a = cand.get("audit") or {}
    v = VERDICT_ORDER.get(a.get("verdict") or "UNKNOWN", 3)
    serving = 0 if cand["portal_record"].get("serving") else 1
    price = cand["portal_record"].get("price_usd")
    price = price if price is not None else 9e9
    age = cand["portal_record"].get("portal_example_age_days")
    age = age if age is not None else 9e9
    return (v, serving, price, age, cand["portal_record"].get("service_id") or "")


KEYWORDS = {
    "blockchain": ["rpc", "evm", "chain", "contract", "abi", "tx", "transaction", "gas", "token", "onchain", "블록체인", "체인"],
    "finance": ["price", "market", "ticker", "premium", "holdings", "sec", "fund", "etf", "rate", "시세", "시장", "금융"],
    "government": ["export", "customs", "tariff", "federal", "register", "entity", "wage", "수출", "관세", "정부"],
    "compliance": ["sanction", "screening", "watchlist", "debarment", "ofac", "제재", "컴플라이언스"],
    "security": ["injection", "advisor", "vulnerab", "cve", "scan", "lint", "integrity", "repaint", "보안", "검사"],
    "health": ["clinical", "trial", "drug", "fda", "medication", "임상", "의약"],
    "documents": ["ocr", "table", "pdf", "parse", "redaction", "문서"],
    "ai": ["embedding", "rerank", "similarity", "llm", "inference", "임베딩"],
    "web": ["search", "dns", "whois", "domain", "웹", "검색"],
    "research": ["literature", "paper", "scholar", "openalex", "논문", "학술"],
}


def parse_question(q):
    """Deterministic category routing. The LLM never decides. Korean and English."""
    q = q if isinstance(q, str) else ""
    if len(q) > 2000:
        raise ValueError("INVALID_QUESTION")
    low = q.lower()
    scores = {}
    for cat, words in KEYWORDS.items():
        n = sum(1 for w in words if w in low)
        if n:
            scores[cat] = n
    if not scores:
        return {"category": None, "matched": {}}
    best = max(scores.items(), key=lambda kv: (kv[1], -len(kv[0])))
    return {"category": best[0], "matched": scores}


def compare(body, network="main"):
    body = body if isinstance(body, dict) else {}
    ids = body.get("ids")
    category = body.get("category")
    parsed = None
    if not ids and not category and body.get("question"):
        parsed = parse_question(body.get("question"))
        category = parsed["category"]
    c = catalogue()
    if ids:
        if not isinstance(ids, list):
            raise ValueError("ids must be a list")
        wanted = [str(i) for i in ids][:MAX_COMPARE]
        rows = [r for r in c["services"] if r["service_id"] in wanted]
        missing = [i for i in wanted if i not in {r["service_id"] for r in rows}]
    elif category:
        rows = [r for r in c["services"] if (r["category"] or "").lower() == str(category).lower()][:MAX_COMPARE]
        missing = []
    else:
        return {"schema": "agent-service-compare/v1", "service": SERVICE_ID, "status": "NEEDS_CLARIFICATION",
                "missing": ["ids[] or category or a question that maps to a category"],
                "categories": [r["category"] for r in categories()["categories"]], "fetched_at_utc": c["fetched_at_utc"]}
    want_audit = body.get("audit", True) is not False
    audits, audit_status = {}, "SKIPPED"
    if want_audit and rows:
        try:
            audits = audit([r["service_id"] for r in rows], network)
            audit_status = "OK"
        except UpstreamUnavailable as e:
            audit_status = str(e)[:80]
    cands = [{"portal_record": r, "audit": audits.get(r["service_id"])} for r in rows]
    cands.sort(key=_rank_key)
    for i, cd in enumerate(cands, 1):
        cd["rank"] = i
    return {"schema": "agent-service-compare/v1", "service": SERVICE_ID, "status": "OK",
            "fetched_at_utc": c["fetched_at_utc"], "registry_version": c["portal"]["registry_version"],
            "query": {"ids": ids, "category": category, "question_routed": parsed, "audit_requested": want_audit, "network": network},
            "count": len(cands), "not_in_catalogue": missing if ids else [],
            "audit_status": audit_status, "ordering_rule": ORDERING_RULE,
            "candidates": cands, "not_measured": NOT_MEASURED,
            "sources": [CATALOGUE_URL, STATUS_URL, AUDIT_URL]}


# -- HTTP ----------------------------------------------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
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
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        except UpstreamUnavailable as e:
            return self._json(503, {"service": SERVICE_ID, "status": "UPSTREAM_UNAVAILABLE", "detail": str(e)[:200]})
        except Exception as e:
            return self._json(500, {"service": SERVICE_ID, "status": "INTERNAL", "detail": type(e).__name__})

    def do_GET(self):
        p, _, qs = self.path.partition("?")
        qd = dict(urllib.parse.parse_qsl(qs))
        if p in ("/", "/index.html"):
            return self._html(200, UI_HTML)
        if p == "/openapi.json":
            return self._json(200, openapi_spec())
        if p == "/v1/version":
            return self._json(200, {"service": SERVICE_ID, "version": VERSION,
                                    "sources": [CATALOGUE_URL, STATUS_URL, AUDIT_URL]})
        if p == "/v1/health":
            return self._json(200, {"status": "ok", "service": SERVICE_ID, "time_utc": _now()})
        if p == "/v1/categories":
            return self._run(categories)
        if p == "/v1/catalogue":
            sv = qd.get("serving")
            sv = None if sv is None else sv.lower() in ("1", "true", "yes")
            return self._run(lambda: catalogue(qd.get("category"), sv))
        m = re.match(r"^/v1/service/([A-Za-z0-9][A-Za-z0-9._-]{1,62})$", p)
        if m:
            return self._run(lambda: service(m.group(1), qd.get("network") or "main"))
        return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})

    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        ok = p in ("/", "/index.html", "/openapi.json", "/v1/version", "/v1/health", "/v1/categories", "/v1/catalogue") or p.startswith("/v1/service/")
        self.send_response(200 if ok else 404)
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
        if p not in ("/v1/compare", "/", ""):
            return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 32 * 1024:
                return self._json(413, {"service": SERVICE_ID, "status": "BODY_TOO_LARGE"})
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        return self._run(lambda: compare(body))


def serve(host="0.0.0.0", port=8799):
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()


def service_card(base="https://bench.pokt-agent.com"):
    return {
        "schema": "pocket-service-card/v1",
        "description": "Agent Service Benchmark: which Pocket portal service should an agent call, and what is the evidence? "
                       "Reads the portal catalogue, the portal status snapshot and the official 9-rule acceptance audit, then "
                       "returns each candidate's audit verdict and failed rules, serving flag, price, declared input and output "
                       "schemas, portal example freshness, payment rails and how many services compete in the same category. "
                       "Candidates are ordered by a rule stated in every response, and every input to that order is returned so "
                       "the caller can re-rank. It invents no quality score and names what it does not measure. No paid call is "
                       "made on the caller's behalf. Every response is a JSON object. Identity via GET /v1/version, readiness via "
                       "GET /v1/health, function via GET /v1/categories, GET /v1/catalogue, GET /v1/service/{id} and "
                       "POST /v1/compare {category|ids|question}.",
        "updated": datetime.date.today().isoformat(),
        "rpc_types": [{"type": "REST", "intent": "expected", "backend_hint": "%s HTTP server on :8799; mount at /" % SERVICE_ID,
                       "notes": "Snapshots are identified by the portal registry version; two snapshots with different registry versions are not one cross-section."}],
        "apis": ["%s-api" % SERVICE_ID],
        "specs": [{"kind": "openapi", "api": "%s-api" % SERVICE_ID, "url": "%s/openapi.json" % base, "notes": "Served by the same backend."}],
        "docs": base + "/",
        "access": "public",
        "results": "variable",
        "serving": {
            "backend": "Read-only over the public portal catalogue and status documents and the public acceptance audit API; 15 min cache; no keys, no paid calls.",
            "implementations": ["%s >= %s" % (SERVICE_ID, VERSION)], "min_disk_gb": 1, "min_ram_gb": 1,
            "healthcheck": [
                {"rpc_type": "REST", "request": {"path": "/v1/version", "method": "GET"}, "expect": {"json_path": "$.service", "matches": "^%s$" % SERVICE_ID},
                 "notes": "Identity probe: pins the backend to this service id."},
                {"rpc_type": "REST", "request": {"path": "/v1/health", "method": "GET"}, "expect": {"json_path": "$.status", "matches": "^ok$"}, "notes": "Readiness probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/categories", "method": "GET"}, "expect": {"json_path": "$.schema", "matches": "^agent-service-categories/v1$"},
                 "notes": "Functional probe: live competition density per portal category."},
                {"rpc_type": "REST", "request": {"path": "/v1/compare", "method": "POST", "headers": {"content-type": "application/json"},
                                                  "body": {"question": "I need scholarly literature search", "audit": False}},
                 "expect": {"json_path": "$.query.category", "matches": "^research$"},
                 "notes": "Functional probe: a task description routed deterministically to a category and ranked."},
            ],
        },
    }


def openapi_spec(base="https://bench.pokt-agent.com"):
    return {
        "openapi": "3.0.3",
        "info": {"title": "Agent Service Benchmark", "version": VERSION,
                 "description": "Compare Pocket portal services on public evidence: acceptance audit verdict, serving flag, price, schemas, example freshness, category competition. Stated ordering rule, no invented score."},
        "servers": [{"url": base}],
        "paths": {
            "/v1/version": {"get": {"summary": "Identity and upstream sources", "responses": {"200": {"description": "service id, version, sources"}}}},
            "/v1/health": {"get": {"summary": "Readiness", "responses": {"200": {"description": "{status: ok}"}}}},
            "/v1/categories": {"get": {"summary": "Competition density per portal category, with price range and first-party counts",
                                       "responses": {"200": {"description": "agent-service-categories/v1"}}}},
            "/v1/catalogue": {"get": {"summary": "Portal catalogue snapshot, one row per service",
                                      "parameters": [{"name": "category", "in": "query", "schema": {"type": "string"}},
                                                     {"name": "serving", "in": "query", "schema": {"type": "boolean"}}],
                                      "responses": {"200": {"description": "agent-service-catalogue/v1"}, "503": {"description": "portal documents unreachable"}}}},
            "/v1/service/{service_id}": {"get": {"summary": "One service: portal record, audit verdict and failed rules, category density",
                                                 "parameters": [{"name": "service_id", "in": "path", "required": True, "schema": {"type": "string"}},
                                                                {"name": "network", "in": "query", "schema": {"type": "string", "enum": ["main", "beta"], "default": "main"}}],
                                                 "responses": {"200": {"description": "agent-service-detail/v1"}}}},
            "/v1/compare": {"post": {"summary": "Rank candidate services on public evidence",
                                     "requestBody": {"required": True, "content": {"application/json": {"schema": {
                                         "type": "object", "properties": {
                                             "question": {"type": "string", "example": "I need Korean export statistics"},
                                             "category": {"type": "string", "example": "government"},
                                             "ids": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_COMPARE},
                                             "audit": {"type": "boolean", "default": True, "description": "set false to skip the acceptance audit lookup and answer from the catalogue alone"},
                                             "network": {"type": "string", "enum": ["main", "beta"], "default": "main"}}}}}},
                                     "responses": {"200": {"description": "agent-service-compare/v1 with candidates[], ordering_rule and not_measured[]"},
                                                   "400": {"description": "bad request"}}}},
        },
    }


UI_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Service Benchmark</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;color:#222}
code{background:#f4f4f6;border-radius:4px;padding:.1rem .3rem}h1{font-size:1.5rem}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}</style></head>
<body><h1>Agent Service Benchmark <small>v%s</small></h1>
<p>An agent facing a growing portal has no free way to compare services. This one answers from three public documents: the portal catalogue, the portal status snapshot, and the official nine-rule acceptance audit. It returns the evidence and orders candidates by a rule it states in every response. It invents no quality score and names what it does not measure. It never makes a paid call on your behalf.</p>
<table><tr><th>Endpoint</th><th>Purpose</th></tr>
<tr><td><a href="/v1/categories">GET /v1/categories</a></td><td>how many services compete in each category</td></tr>
<tr><td><a href="/v1/catalogue?category=government">GET /v1/catalogue</a></td><td>catalogue snapshot, filterable</td></tr>
<tr><td><a href="/v1/service/literature-search">GET /v1/service/{id}</a></td><td>one service with its audit verdict</td></tr>
<tr><td><code>POST /v1/compare</code></td><td><code>{"question":"I need Korean export statistics"}</code></td></tr>
<tr><td><a href="/openapi.json">GET /openapi.json</a></td><td>OpenAPI 3 spec</td></tr></table>
<p>Service id <code>%s</code>. Siblings: <a href="https://pokt-agent.com/">Settlement Agent</a>, <a href="https://watch.pokt-agent.com/">Network Watch</a>, <a href="https://kr.pokt-agent.com/">KR Market</a>, <a href="https://export.pokt-agent.com/">KR Export</a>, <a href="https://dart.pokt-agent.com/">DART Events</a>, <a href="https://pine.pokt-agent.com/">Pine Lint</a>.</p></body></html>""" % (VERSION, SERVICE_ID)
