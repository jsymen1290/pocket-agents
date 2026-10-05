"""POKT Agent MCP gateway (pokt-agent-mcp) - every pokt-agent.com service as Model Context Protocol tools.

Two transports, one tool table:
  HTTP   POST /mcp  (Streamable HTTP, JSON responses, stateless; for remote connectors: https://mcp.pokt-agent.com/mcp)
  stdio  python3 pokt_agent_mcp.py              (this file; standard library only, Python 3.9+)
Each tool forwards to one REST service. On the supplier the gateway calls the services on 127.0.0.1 (POKT_MCP_LOCAL=1);
elsewhere it calls the public https hosts. Pine Check calls keep the free-trial limit of the caller (Cloudflare client
headers are forwarded); an API key can be given to the stdio server as PINE_CHECK_API_KEY.
"""
import datetime
import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVICE_ID = "pokt-agent-mcp"
VERSION = "0.1.0"
PROTOCOL = "2025-06-18"
UTC = datetime.timezone.utc
HOSTS = {  # service -> (public base, local port)
    "pine": ("https://pine.pokt-agent.com", 8796), "backtest": ("https://backtest.pokt-agent.com", 8805),
    "figures": ("https://figures.pokt-agent.com", 8806), "impact": ("https://impact.pokt-agent.com", 8807),
    "rates": ("https://rates.pokt-agent.com", 8804), "law": ("https://law.pokt-agent.com", 8802),
    "apt": ("https://apt.pokt-agent.com", 8800), "dart": ("https://dart.pokt-agent.com", 8798), "export": ("https://export.pokt-agent.com", 8797),
}
S = lambda **p: {"type": "object", "properties": p}  # noqa: E731
STR = {"type": "string"}
TOOLS = [
    {"name": "pine_integrity", "svc": "pine", "method": "POST", "path": "/v1/integrity",
     "description": "Check TradingView Pine Script v5/v6 for structures that make backtests look better than live trading: future data (lookahead_on without offset), unconfirmed higher-timeframe values, signals drawn on past bars, realtime-only state, fill and cost assumptions, alerts on unconfirmed bars. Returns verdict, findings with line and rule, and what was not checked.",
     "inputSchema": dict(S(source=dict(STR, description="Pine Script source")), required=["source"])},
    {"name": "pine_lint", "svc": "pine", "method": "POST", "path": "/v1/lint",
     "description": "Lint TradingView Pine Script: version annotation, declaration, brackets, strings, v4 remnants in v5/v6 code, undeclared reassignment, limits, plus all integrity checks.",
     "inputSchema": dict(S(source=dict(STR, description="Pine Script source")), required=["source"])},
    {"name": "backtest_reconcile", "svc": "backtest", "method": "POST", "path": "/v1/reconcile",
     "description": "Recompute net profit, win rate, profit factor and closed-trade drawdown from a TradingView 'List of trades' CSV or a JSON trade list with commission settings, and mark each claimed metric MATCH or MISMATCH.",
     "inputSchema": S(trades_csv=dict(STR, description="TradingView Strategy Tester List of trades CSV export"), trades={"type": "array", "items": {"type": "object"}},
                      initial_capital={"type": "number"}, commission={"type": "object", "description": "{type: percent|per_contract|cash_per_order, value}"},
                      claimed={"type": "object", "description": "metrics to check, e.g. {net_profit, win_rate, profit_factor, max_drawdown}"})},
    {"name": "kr_figure_check", "svc": "figures", "method": "POST", "path": "/v1/check",
     "description": "Check the numbers in a Korean finance text against official sources: percent vs percentage point, 조/억 units, Bank of Korea base rate and KTB yields, KRW exchange rates, listed-company revenue/operating profit/net income from DART (consolidated vs separate).",
     "inputSchema": dict(S(text=STR, as_of=dict(STR, description="YYYY-MM-DD"), corp=STR, year={"type": "integer"}), required=["text"])},
    {"name": "dart_correction_impact", "svc": "impact", "method": "POST", "path": "/v1/impact",
     "description": "For a Korean listed company: which cited DART filings were later corrected or withdrawn, and which numbers or dates in a report equal a corrected value (STALE), with the corrected value and filing.",
     "inputSchema": S(corp=dict(STR, description="company name"), corp_code=STR, rcept_nos={"type": "array", "items": STR}, text=STR, since=dict(STR, description="YYYYMMDD"))},
    {"name": "kr_rates", "svc": "rates", "method": "GET", "path": "/v1/rates",
     "description": "Official Korean interest rates on a date: BoK base rate, call, KOFR, CD, CP, MSB, KTB 1y-50y and spreads, each with its observation date.",
     "inputSchema": S(date=dict(STR, description="YYYY-MM-DD, default today"))},
    {"name": "kr_fx", "svc": "rates", "method": "GET", "path": "/v1/fx",
     "description": "KRW exchange rates from the Export-Import Bank of Korea: deal basis, telegraphic buying and selling rates.",
     "inputSchema": S(ccy=dict(STR, description="comma-separated ISO codes, e.g. USD,JPY"), date=STR)},
    {"name": "kr_law_article", "svc": "law", "method": "GET", "path": "/v1/article",
     "description": "One article of a Korean statute quoted verbatim with paragraphs and items, promulgation and enforcement dates and the official link. Accepts official names, abbreviations (자본시장법) and common English names.",
     "inputSchema": dict(S(law=STR, no=dict(STR, description="article number, e.g. 178 or 178의2")), required=["law", "no"])},
    {"name": "kr_apartment_trades", "svc": "apt", "method": "POST", "path": "/v1/query",
     "description": "Korean apartment sale prices from the MOLIT real-transaction register by district and month, trends and single complexes. Ask in Korean or English.",
     "inputSchema": dict(S(question=STR), required=["question"])},
    {"name": "dart_events", "svc": "dart", "method": "POST", "path": "/v1/query",
     "description": "Korean listed-company disclosures from OpenDART: filings, corrections linked to originals with before/after values, point-in-time view. Ask in Korean or English.",
     "inputSchema": dict(S(question=STR, since=STR, to=STR), required=["question"])},
    {"name": "kr_export_pulse", "svc": "export", "method": "POST", "path": "/v1/query",
     "description": "South Korea's export statistics from Korea Customs Service: 10-day provisional totals, changes vs prior month and year, HS code and country trade.",
     "inputSchema": dict(S(question=STR), required=["question"])},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def _base(svc):
    pub, port = HOSTS[svc]
    return "http://127.0.0.1:%d" % port if os.environ.get("POKT_MCP_LOCAL") == "1" else pub


def call_tool(name, args, fwd=None):
    t = BY_NAME.get(name)
    if not t:
        raise KeyError(name)
    args = {k: v for k, v in (args or {}).items() if v is not None}
    headers = {"user-agent": "%s/%s" % (SERVICE_ID, VERSION), "accept": "application/json"}
    headers.update(fwd or {})
    key = os.environ.get("PINE_CHECK_API_KEY")
    if key and t["svc"] == "pine":
        headers["Authorization"] = "Bearer " + key
    url = _base(t["svc"]) + t["path"]
    data = None
    if t["method"] == "GET":
        url += "?" + urllib.parse.urlencode({k: (v if isinstance(v, str) else json.dumps(v)) for k, v in args.items()})
    else:
        data = json.dumps(args, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=t["method"])
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def handle(msg, fwd=None):
    """One JSON-RPC message -> response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVICE_ID, "title": "POKT Agent tools (Pine Check, backtests, Korean finance data)", "version": VERSION},
                   "instructions": "Deterministic checks and official Korean data. Every tool returns one JSON object with its sources and limits."})
    if method and method.startswith("notifications/"):
        return None
    if method == "ping":
        return ok({})
    if method in ("resources/list", "resources/templates/list", "prompts/list"):  # clients probe these; we serve tools only
        return ok({"resources/list": {"resources": []}, "resources/templates/list": {"resourceTemplates": []}, "prompts/list": {"prompts": []}}[method])
    if method == "tools/list":
        return ok({"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"],
                              "annotations": {"readOnlyHint": True, "openWorldHint": True}} for t in TOOLS]})
    if method == "tools/call":
        name = params.get("name")
        if name not in BY_NAME:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool %s" % name}}
        try:
            status, body = call_tool(name, params.get("arguments"), fwd)
        except Exception as e:
            return ok({"content": [{"type": "text", "text": "upstream error: %s" % type(e).__name__}], "isError": True})
        return ok({"content": [{"type": "text", "text": body}], "isError": status >= 400})
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}}


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj=None, ctype="application/json"):
        body = b"" if obj is None else (obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type, mcp-protocol-version, mcp-session-id, authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p in ("/", "/index.html"):
            return self._send(200, UI_HTML.encode("utf-8"), "text/html")
        if p == "/v1/health":
            return self._send(200, {"status": "ok", "service": SERVICE_ID, "time_utc": datetime.datetime.now(UTC).isoformat(timespec="seconds")})
        if p == "/v1/version":
            return self._send(200, {"service": SERVICE_ID, "version": VERSION, "tools": [t["name"] for t in TOOLS]})
        if p == "/mcp":
            return self._send(405, {"error": "this server does not open an SSE stream; POST JSON-RPC to /mcp"})
        return self._send(404, {"status": "NOT_FOUND"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/mcp":
            return self._send(404, {"status": "NOT_FOUND"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4 * 1024 * 1024:
                return self._send(413, {"error": "body too large"})
            msg = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        fwd = {k: self.headers[k] for k in ("CF-Connecting-IP", "CF-Ray") if self.headers.get(k)}
        if isinstance(msg, list):
            out = [r for r in (handle(m, fwd) for m in msg) if r is not None]
            return self._send(200, out) if out else self._send(202)
        r = handle(msg, fwd)
        return self._send(202) if r is None else self._send(200, r)


def serve(host="0.0.0.0", port=8808):
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()


def stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            r = handle(json.loads(line))
        except ValueError:
            r = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if r is not None:
            sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
            sys.stdout.flush()


UI_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>POKT Agent MCP</title><style>body{font:15px/1.55 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
code,pre{background:#f4f4f6;border-radius:4px;padding:.1rem .3rem}pre{padding:.8rem;overflow:auto}h1{font-size:1.5rem}td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}table{border-collapse:collapse}</style></head>
<body><h1>POKT Agent MCP <small>v%s</small></h1>
<p>Remote MCP server (Streamable HTTP, no sign-in): <code>https://mcp.pokt-agent.com/mcp</code>. Add it as a custom connector in your MCP client. Local stdio version: <a href="https://github.com/jsymen1290/pocket-agents/tree/main/mcp">github.com/jsymen1290/pocket-agents/mcp</a>.</p>
<table><tr><th>Tool</th><th>What it does</th></tr>%s</table>
<p>Pine Check tools share the free trial of the REST API (30 checks a day per client); for more, use an API key with the REST API or the stdio server (<code>PINE_CHECK_API_KEY</code>). See <a href="https://pine.pokt-agent.com/">pine.pokt-agent.com</a>.</p></body></html>""" % (
    VERSION, "".join("<tr><td><code>%s</code></td><td>%s</td></tr>" % (t["name"], t["description"].split(":")[0].split(". ")[0]) for t in TOOLS))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--http":
        serve(port=int(sys.argv[2]) if len(sys.argv) > 2 else 8808)
    else:
        stdio()
