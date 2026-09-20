"""Pine Script Lint v0 (pine-script-lint-v1) - deterministic static checks for TradingView Pine Script v5/v6.

Source-only analysis (no TradingView access, no compiler): version annotation, declaration, balanced
brackets, unterminated strings, v4/v5 remnants, reassignment of undeclared names, request.security
lookahead (repaint risk), plot/security count limits, unknown namespaces, mixed indentation, optional naming
conventions. Same source -> same findings. It does not claim "compiles"; it reports what it can see.

Endpoints (REST, JSON object always):
  GET  /v1/version   GET /v1/health   GET /openapi.json   GET /v1/rules
  POST /v1/lint {source, conventions?: bool}
"""
import datetime
import http.server
import json
import re
import socketserver

SERVICE_ID = "pine-script-lint-v1"
VERSION = "0.1.0"
UTC = datetime.timezone.utc
MAX_SOURCE = 200 * 1024
PLOT_LIMIT = 64
SECURITY_LIMIT = 40
KNOWN_NS = {"ta", "math", "str", "array", "matrix", "map", "request", "color", "input", "label", "line", "box", "table", "polyline", "linefill",
            "strategy", "syminfo", "timeframe", "barstate", "chart", "runtime", "log", "ticker", "session", "format", "position", "size", "shape",
            "location", "xloc", "yloc", "extend", "scale", "display", "order", "alert", "dayofweek", "currency", "font", "text", "adjustment",
            "backadjustment", "settlement_as_close", "plot", "hline", "barmerge", "splits", "dividends", "earnings", "timeframe", "math", "strategy",
            "line", "label", "box", "table", "color", "str", "int", "float", "bool", "string", "time", "plotshape", "plotchar", "plotarrow"}
PLOT_FUNCS = ("plot", "plotshape", "plotchar", "plotcandle", "plotbar", "plotarrow", "hline", "fill", "bgcolor", "barcolor")
LEGACY = [
    ("P101", r"^\s*study\s*\(", "study() is v4; use indicator() in v5/v6", "error"),
    ("P102", r"(?<![\w.])security\s*\(", "bare security() is v4; use request.security()", "error"),
    ("P103", r"\btransp\s*=", "transp= was removed in v5; use color.new(color, transparency)", "error"),
    ("P104", r"(?<![\w.])(rsi|sma|ema|atr|highest|lowest|crossover|crossunder|change|stdev|vwap|macd|bb)\s*\(", "bare TA function is v4; use ta.<name>()", "error"),
    ("P105", r"(?<![\w.])tostring\s*\(", "tostring() is v4; use str.tostring()", "error"),
    ("P106", r"(?<![\w.])input\s*\([^)]*\btype\s*=", "input(type=...) is v4; use input.int/float/bool/string", "error"),
    ("P107", r"\binput\.resolution\b", "input.resolution is v4; use input.timeframe", "error"),
    ("P108", r"(?<![\w.])iff\s*\(", "iff() is deprecated; use the ternary operator", "warning"),
    ("P109", r"(?<![\w.])tickerid\b(?!\s*\()", "tickerid is v4; use syminfo.tickerid", "error"),
]
RULES = [
    {"code": "P001", "severity": "error", "title": "missing //@version annotation as the first statement"},
    {"code": "P002", "severity": "warning", "title": "Pine version below 6 (v6 is current; v5 still supported)"},
    {"code": "P003", "severity": "error", "title": "missing indicator()/strategy()/library() declaration"},
    {"code": "P004", "severity": "error", "title": "unbalanced parentheses, brackets or braces"},
    {"code": "P005", "severity": "error", "title": "unterminated string literal"},
    {"code": "P006", "severity": "warning", "title": "':=' reassigns a name that was never declared with '=' or 'var'"},
    {"code": "P007", "severity": "warning", "title": "request.security() without lookahead=barmerge.lookahead_off (repaint risk) or with lookahead_on"},
    {"code": "P008", "severity": "error", "title": "plot-family calls exceed the 64 limit"},
    {"code": "P009", "severity": "error", "title": "request.security() calls exceed the 40 limit"},
    {"code": "P010", "severity": "warning", "title": "unknown namespace before '.' (possible typo)"},
    {"code": "P011", "severity": "warning", "title": "mixed tab and space indentation"},
    {"code": "P012", "severity": "info", "title": "alertcondition() with a literal condition"},
    {"code": "P013", "severity": "info", "title": "ta.* length argument is an expression (must be simple int at compile time)"},
    {"code": "P014", "severity": "info", "title": "division whose denominator is a bare identifier with no zero guard on the line"},
    {"code": "P015", "severity": "info", "title": "declaration is missing overlay= (defaults to false)"},
    {"code": "P020", "severity": "info", "title": "naming convention (opt-in): inputs cfg_*, raw data raw_*, features feat_*, signals sig_*, plots plot_*"},
    # integrity (backtest-vs-realtime reproducibility) rules, GPT rank-4 reframe 2026-09-20
    {"code": "P201", "severity": "error", "title": "future data: request.security with lookahead_on and no historical offset on the expression"},
    {"code": "P202", "severity": "warning", "title": "higher-timeframe value used unconfirmed (no [1] offset): realtime bar differs from history"},
    {"code": "P203", "severity": "warning", "title": "plot-family call with a negative offset draws the signal on past bars"},
    {"code": "P204", "severity": "info", "title": "drawing anchored to a past bar (bar_index - n)"},
    {"code": "P205", "severity": "info", "title": "ta.pivothigh/pivotlow: confirmed only after rightbars, so signals appear late in history but not late on the pivot bar"},
    {"code": "P206", "severity": "warning", "title": "barstate.isrealtime/islast/isnew in signal logic: history and realtime execute differently"},
    {"code": "P207", "severity": "warning", "title": "timenow in script logic differs between historical and realtime bars"},
    {"code": "P208", "severity": "info", "title": "varip keeps intrabar state that a backtest cannot reproduce"},
    {"code": "P209", "severity": "warning", "title": "strategy(calc_on_every_tick=true): fills on intrabar ticks that history does not have"},
    {"code": "P210", "severity": "info", "title": "strategy() without commission_value / slippage: cost-free backtest"},
    {"code": "P211", "severity": "info", "title": "alert/alertcondition on an unconfirmed bar (no barstate.isconfirmed guard in file)"},
    {"code": "P212", "severity": "warning", "title": "strategy(process_orders_on_close=true) or calc_on_order_fills=true: same-bar execution assumptions"},
] + [{"code": c, "severity": sev, "title": msg} for c, _, msg, sev in LEGACY]
INTEGRITY_CODES = {"P007", "P201", "P202", "P203", "P204", "P205", "P206", "P207", "P208", "P209", "P210", "P211", "P212"}


def _strip(line):
    """Remove string literals and trailing comments; return (code, unterminated_string)."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break
        if ch in ("'", '"'):
            q = ch
            j = i + 1
            while j < n and line[j] != q:
                j += 2 if line[j] == "\\" else 1
            if j >= n:
                return "".join(out), True
            out.append('""')
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), False


def lint(source, conventions=False):
    if not isinstance(source, str):
        raise ValueError("INVALID_SOURCE")
    if len(source.encode("utf-8", "replace")) > MAX_SOURCE:
        raise ValueError("SOURCE_TOO_LARGE")
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    F = []

    def add(code, ln, msg, sev=None):
        sev = sev or next((r["severity"] for r in RULES if r["code"] == code), "info")
        F.append({"code": code, "severity": sev, "line": ln, "message": msg})

    code_lines = []
    for idx, raw in enumerate(lines, 1):
        code, unterminated = _strip(raw)
        if unterminated:
            add("P005", idx, "string literal is not closed on this line")
        code_lines.append(code)
    # version
    version = None
    first_stmt = next((i for i, l in enumerate(lines, 1) if l.strip()), None)
    m = re.search(r"^\s*//@version\s*=\s*(\d+)", source, re.M)
    if m:
        version = int(m.group(1))
        if first_stmt is not None and not lines[first_stmt - 1].strip().startswith("//@version"):
            add("P001", first_stmt, "//@version must be the first non-empty line (found later)")
        if version < 6:
            add("P002", 1, "script is //@version=%d; v6 is current" % version)
    else:
        add("P001", 1, "no //@version=N annotation; TradingView will assume v1")
    # declaration
    decl = None
    for i, l in enumerate(code_lines, 1):
        mm = re.match(r"^\s*(indicator|strategy|library)\s*\(", l)
        if mm:
            decl = {"kind": mm.group(1), "line": i}
            break
    if not decl:
        add("P003", 1, "no indicator(), strategy() or library() call at statement start")
    elif decl["kind"] != "library":
        span = " ".join(code_lines[decl["line"] - 1: decl["line"] + 6])
        if "overlay" not in span:
            add("P015", decl["line"], "%s() has no overlay= argument" % decl["kind"])
    # brackets (whole file, strings stripped)
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for i, l in enumerate(code_lines, 1):
        for ch in l:
            if ch in "([{":
                stack.append((ch, i))
            elif ch in ")]}":
                if stack and stack[-1][0] == pairs[ch]:
                    stack.pop()
                else:
                    add("P004", i, "unexpected '%s'" % ch)
    for ch, i in stack[:3]:
        add("P004", i, "'%s' opened here is never closed" % ch)
    # legacy syntax
    for c, pat, msg, sev in LEGACY:
        rx = re.compile(pat)
        for i, l in enumerate(code_lines, 1):
            if rx.search(l):
                add(c, i, msg, sev)
    # declarations vs reassignment
    declared = set()
    decl_rx = re.compile(r"^\s*(?:var\s+|varip\s+)?(?:(?:int|float|bool|string|color|label|line|box|table|array<[^>]+>|matrix<[^>]+>|map<[^>]+>|[A-Za-z_]\w*)\s+)?([A-Za-z_]\w*)\s*=(?!=)")
    func_rx = re.compile(r"^\s*([A-Za-z_]\w*)\s*\(([^)]*)\)\s*=>")
    tuple_rx = re.compile(r"^\s*\[([^\]]+)\]\s*=(?!=)")
    for l in code_lines:
        m1 = decl_rx.match(l)
        if m1:
            declared.add(m1.group(1))
        m2 = func_rx.match(l)
        if m2:
            declared.add(m2.group(1))
            for p in m2.group(2).split(","):
                p = p.strip().split()
                if p:
                    declared.add(p[-1])
        m3 = tuple_rx.match(l)
        if m3:
            for p in m3.group(1).split(","):
                declared.add(p.strip())
        for mm in re.finditer(r"\bfor\s+([A-Za-z_]\w*)\s*=", l):
            declared.add(mm.group(1))
        for mm in re.finditer(r"\bfor\s+\[?([A-Za-z_]\w*)(?:\s*,\s*([A-Za-z_]\w*))?\]?\s+in\b", l):
            declared.add(mm.group(1))
            if mm.group(2):
                declared.add(mm.group(2))
    for i, l in enumerate(code_lines, 1):
        for mm in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)\s*:=", l):
            name = mm.group(1)
            if name not in declared:
                add("P006", i, "'%s' is reassigned with := but never declared" % name)
    # request.security lookahead
    sec_count = 0
    for i, l in enumerate(code_lines, 1):
        for mm in re.finditer(r"request\.security\s*\(", l):
            sec_count += 1
            tail = " ".join(code_lines[i - 1: i + 3])
            if "lookahead_on" in tail:
                if "[1]" in tail:
                    add("P007", i, "request.security with lookahead_on and a [1] offset (accepted non-repainting idiom for confirmed HTF bars); verify the offset covers every series", "info")
                else:
                    add("P007", i, "request.security with lookahead_on repaints on historical bars")
            elif "lookahead" not in tail:
                add("P007", i, "request.security without explicit lookahead=barmerge.lookahead_off")
    if sec_count > SECURITY_LIMIT:
        add("P009", 1, "%d request.security calls (limit %d)" % (sec_count, SECURITY_LIMIT))
    # plot count
    plot_count = 0
    for l in code_lines:
        for fn in PLOT_FUNCS:
            plot_count += len(re.findall(r"(?<![\w.])%s\s*\(" % fn, l))
    if plot_count > PLOT_LIMIT:
        add("P008", 1, "%d plot-family calls (limit %d)" % (plot_count, PLOT_LIMIT))
    # unknown namespaces
    seen_ns = set()
    for i, l in enumerate(code_lines, 1):
        for mm in re.finditer(r"(?<![\w.])([a-z][a-z0-9_]*)\.([a-z_][A-Za-z0-9_]*)\s*\(", l):
            ns = mm.group(1)
            if ns not in KNOWN_NS and ns not in declared and ns not in seen_ns:
                seen_ns.add(ns)
                add("P010", i, "'%s.' is not a known namespace" % ns)
    # indentation
    tabs = any(l.startswith("\t") for l in lines)
    spaces = any(l.startswith(" ") for l in lines)
    if tabs and spaces:
        first_tab = next(i for i, l in enumerate(lines, 1) if l.startswith("\t"))
        add("P011", first_tab, "file mixes tab- and space-indented lines")
    # alertcondition literal
    for i, l in enumerate(code_lines, 1):
        if re.search(r"(?<![\w.])alertcondition\s*\(\s*(true|false)\b", l):
            add("P012", i, "alertcondition() condition is a literal")
    # ta length expression
    for i, l in enumerate(code_lines, 1):
        for mm in re.finditer(r"\bta\.(sma|ema|rma|wma|rsi|atr|highest|lowest|stdev|cci|mfi|roc|mom|linreg|percentrank|change)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)", l):
            args = mm.group(2).split(",")
            if len(args) >= 2:
                length = args[1].strip()
                if length and not re.match(r"^(\d+|[A-Za-z_]\w*)$", length):
                    add("P013", i, "ta.%s length '%s' is an expression" % (mm.group(1), length[:40]))
    # division guard
    for i, l in enumerate(code_lines, 1):
        if re.search(r"/\s*[A-Za-z_]\w*\b", l) and not re.search(r"==\s*0|!=\s*0|nz\(|math\.max\(|\?|/\s*\d", l) and "//" not in l:
            add("P014", i, "division by a bare identifier without a visible zero guard")
    # integrity: backtest vs realtime reproducibility
    for i, l in enumerate(code_lines, 1):
        for mm in re.finditer(r"request\.security\s*\(", l):
            tail = " ".join(code_lines[i - 1: i + 3])
            if "lookahead_on" in tail and "[1]" not in tail and "[2]" not in tail:
                add("P201", i, "request.security(..., lookahead_on) without a [1]-style offset reads the future on historical bars")
            elif "lookahead_on" not in tail and not re.search(r"\]\s*\[\s*1\s*\]|\)\s*\[\s*1\s*\]|\w\s*\[\s*1\s*\]", tail):
                add("P202", i, "higher-timeframe value taken from the still-forming bar; use [1] on the expression for confirmed values")
        for fn in PLOT_FUNCS:
            for mm in re.finditer(r"(?<![\w.])%s\s*\(([^)]*)\)" % fn, l):
                if re.search(r"\boffset\s*=\s*-\s*\d", mm.group(1)):
                    add("P203", i, "%s(offset=-n) shifts the mark back in time" % fn)
        if re.search(r"\b(label|line|box)\.new\s*\([^)]*bar_index\s*-\s*[1-9]", l):
            add("P204", i, "drawing created at bar_index - n")
        if re.search(r"\bta\.pivot(high|low)\s*\(", l):
            add("P205", i, "pivot detected rightbars after the pivot bar")
        if re.search(r"\bbarstate\.(isrealtime|islast|isnew)\b", l) and not re.search(r"\b(label|line|box|table)\.", l):
            add("P206", i, "barstate.isrealtime/islast/isnew in logic")
        if re.search(r"(?<![\w.])timenow\b", l):
            add("P207", i, "timenow used")
        if re.search(r"^\s*varip\s+", l):
            add("P208", i, "varip declaration")
        if re.search(r"^\s*strategy\s*\(", l):
            span = " ".join(code_lines[i - 1: i + 6])
            if re.search(r"calc_on_every_tick\s*=\s*true", span):
                add("P209", i, "calc_on_every_tick=true")
            if not re.search(r"commission_value|slippage", span):
                add("P210", i, "no commission_value/slippage in strategy()")
            if re.search(r"process_orders_on_close\s*=\s*true|calc_on_order_fills\s*=\s*true", span):
                add("P212", i, "process_orders_on_close or calc_on_order_fills is true")
    if any(re.search(r"(?<![\w.])(alert|alertcondition)\s*\(", l) for l in code_lines) and not any("barstate.isconfirmed" in l for l in code_lines):
        ln = next(i for i, l in enumerate(code_lines, 1) if re.search(r"(?<![\w.])(alert|alertcondition)\s*\(", l))
        add("P211", ln, "alerts fire on unconfirmed bars unless the condition is gated by barstate.isconfirmed")
    # naming conventions (opt-in)
    if conventions:
        for i, l in enumerate(code_lines, 1):
            m1 = decl_rx.match(l)
            if not m1:
                continue
            name = m1.group(1)
            if "input." in l and not name.startswith("cfg_"):
                add("P020", i, "input '%s' should be cfg_*" % name)
            elif re.search(r"\b(plot|plotshape|plotchar|hline)\s*\(", l) and not name.startswith("plot_"):
                add("P020", i, "plot handle '%s' should be plot_*" % name)
    F.sort(key=lambda f: (f["line"], f["code"]))
    counts = {"error": 0, "warning": 0, "info": 0}
    for f in F:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {
        "schema": "pine-lint-result/v1", "service": SERVICE_ID, "linter_version": VERSION,
        "pine_version": version, "declaration": decl, "verdict": "FAIL" if counts["error"] else ("WARN" if counts["warning"] else "CLEAN"),
        "counts": counts, "findings": F,
        "stats": {"lines": len(lines), "non_empty_lines": sum(1 for l in lines if l.strip()), "functions": sum(1 for l in code_lines if func_rx.match(l)),
                  "inputs": sum(len(re.findall(r"\binput\.(int|float|bool|string|color|timeframe|source|symbol|session|price|text_area|enum)\s*\(", l)) for l in code_lines),
                  "plots": plot_count, "securities": sec_count, "alertconditions": sum(len(re.findall(r"(?<![\w.])alertcondition\s*\(", l)) for l in code_lines)},
        "scope": "static source checks only; compile success can only be confirmed in the TradingView Pine editor",
    }


def integrity(source):
    """Backtest-vs-realtime reproducibility subset: does the script contain structures that make history look better than live?"""
    r = lint(source)
    F = [f for f in r["findings"] if f["code"] in INTEGRITY_CODES]
    counts = {"error": 0, "warning": 0, "info": 0}
    for f in F:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"schema": "pine-integrity-result/v1", "service": SERVICE_ID, "linter_version": VERSION, "pine_version": r["pine_version"], "declaration": r["declaration"],
            "verdict": "FAIL" if counts["error"] else ("WARN" if counts["warning"] else "CLEAN"), "counts": counts, "findings": F,
            "checks": ["future data (lookahead_on without offset)", "unconfirmed higher-timeframe values", "signals drawn in the past", "pivot lag",
                       "realtime-only state (barstate, timenow, varip)", "strategy fill and cost assumptions", "alerts on unconfirmed bars"],
            "stats": r["stats"], "scope": "static heuristics; it does not run the script and cannot prove the absence of repainting"}


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

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p in ("/", "/index.html"):
            return self._html(200, UI_HTML)
        if p == "/openapi.json":
            return self._json(200, openapi_spec())
        if p == "/v1/version":
            return self._json(200, {"service": SERVICE_ID, "version": VERSION, "pine_versions": [5, 6]})
        if p == "/v1/health":
            return self._json(200, {"status": "ok", "service": SERVICE_ID, "time_utc": datetime.datetime.now(UTC).isoformat(timespec="seconds")})
        if p == "/v1/rules":
            return self._json(200, {"schema": "pine-lint-rules/v1", "service": SERVICE_ID, "count": len(RULES), "rules": RULES})
        return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})

    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        code = 200 if p in ("/", "/index.html", "/openapi.json", "/v1/version", "/v1/health", "/v1/rules") else 404
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
        if p not in ("/v1/lint", "/v1/integrity", "/", ""):
            return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_SOURCE + 4096:
                return self._json(413, {"service": SERVICE_ID, "status": "BODY_TOO_LARGE"})
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            src = body.get("source") if isinstance(body, dict) else None
            if src is None:
                return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": "body must be {\"source\": \"<pine script>\"}"})
            if p == "/v1/integrity" or body.get("mode") == "integrity":
                return self._json(200, integrity(src))
            return self._json(200, lint(src, bool(body.get("conventions"))))
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        except Exception as e:
            return self._json(500, {"service": SERVICE_ID, "status": "INTERNAL", "detail": type(e).__name__})


def serve(host="0.0.0.0", port=8796):
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()


SAMPLE = "//@version=6\nindicator(\"probe\", overlay=true)\nplot(close)\n"


def service_card(base="https://pine.pokt-agent.com"):
    return {
        "schema": "pocket-service-card/v1",
        "description": "Pine Script Lint and Integrity: deterministic static checks for TradingView Pine Script v5/v6. POST /v1/integrity answers "
                       "'does this script contain structures that make history look better than live?': future data via lookahead_on without offset, "
                       "unconfirmed higher-timeframe values, signals drawn on past bars, pivot lag, realtime-only state (barstate, timenow, varip), "
                       "strategy fill and cost assumptions, alerts on unconfirmed bars. POST /v1/lint adds syntax and version checks (v4 remnants, "
                       "brackets, strings, undeclared reassignment, plot/security limits, namespaces). Each finding has line, code, severity. Same "
                       "source, same findings; it never claims the script compiles or proves no repainting. Every response is a JSON object. "
                       "Identity via GET /v1/version, readiness via GET /v1/health, rules via GET /v1/rules.",
        "updated": datetime.date.today().isoformat(),
        "rpc_types": [{"type": "REST", "intent": "expected", "backend_hint": "%s HTTP server on :8796; mount at /" % SERVICE_ID,
                       "notes": "POST /v1/lint body {\"source\": \"<pine>\", \"conventions\": false}; source up to 200 KiB; verdict CLEAN | WARN | FAIL."}],
        "apis": ["%s-api" % SERVICE_ID],
        "specs": [{"kind": "openapi", "api": "%s-api" % SERVICE_ID, "url": "%s/openapi.json" % base, "notes": "Served by the same backend."}],
        "docs": base + "/",
        "access": "public",
        "results": "deterministic",
        "serving": {
            "backend": "Pure-Python static analyser; no upstream calls, no state, no keys.",
            "implementations": ["%s >= %s" % (SERVICE_ID, VERSION)], "min_disk_gb": 1, "min_ram_gb": 1,
            "healthcheck": [
                {"rpc_type": "REST", "request": {"path": "/v1/version", "method": "GET"}, "expect": {"json_path": "$.service", "matches": "^%s$" % SERVICE_ID},
                 "notes": "Identity probe: pins the backend to this service id."},
                {"rpc_type": "REST", "request": {"path": "/v1/health", "method": "GET"}, "expect": {"json_path": "$.status", "matches": "^ok$"}, "notes": "Readiness probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/lint", "method": "POST", "headers": {"content-type": "application/json"}, "body": {"source": SAMPLE}},
                 "expect": {"json_path": "$.verdict", "matches": "^CLEAN$"}, "notes": "Functional probe: a minimal valid v6 indicator lints CLEAN."},
                {"rpc_type": "REST", "request": {"path": "/v1/integrity", "method": "POST", "headers": {"content-type": "application/json"},
                                                  "body": {"source": "//@version=6\nindicator(\"y\")\nd = request.security(syminfo.tickerid, \"D\", close, lookahead=barmerge.lookahead_on)\nplot(d)\n"}},
                 "expect": {"json_path": "$.verdict", "matches": "^FAIL$"}, "notes": "Functional probe: lookahead_on without an offset is future data and must FAIL integrity."},
            ],
        },
    }


def openapi_spec(base="https://pine.pokt-agent.com"):
    return {
        "openapi": "3.0.3",
        "info": {"title": "Pine Script Lint", "version": VERSION, "description": "Deterministic static checks for TradingView Pine Script v5/v6 source. Static only; compile success is confirmed only in the Pine editor."},
        "servers": [{"url": base}],
        "paths": {
            "/v1/version": {"get": {"summary": "Identity", "responses": {"200": {"description": "service id and version"}}}},
            "/v1/health": {"get": {"summary": "Readiness", "responses": {"200": {"description": "{status: ok}"}}}},
            "/v1/rules": {"get": {"summary": "Rule catalogue with codes and severities", "responses": {"200": {"description": "pine-lint-rules/v1"}}}},
            "/v1/integrity": {"post": {"summary": "Backtest-vs-realtime integrity checks only (future data, unconfirmed HTF, past-drawn signals, realtime-only state, strategy assumptions)",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["source"], "properties": {"source": {"type": "string", "maxLength": MAX_SOURCE}}}}}},
                "responses": {"200": {"description": "pine-integrity-result/v1: verdict, counts, findings[], checks[]"}}}},
            "/v1/lint": {"post": {"summary": "Lint a Pine Script source", "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["source"], "properties": {"source": {"type": "string", "maxLength": MAX_SOURCE}, "conventions": {"type": "boolean", "default": False}}}}}},
                "responses": {"200": {"description": "pine-lint-result/v1: verdict, counts, findings[{code, severity, line, message}], stats"}, "400": {"description": "bad request"}, "413": {"description": "source too large"}}}},
        },
    }


UI_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pine Script Lint</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
code,pre{background:#f4f4f6;border-radius:4px;padding:.1rem .3rem}pre{padding:.8rem;overflow:auto}h1{font-size:1.5rem}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}</style></head>
<body><h1>Pine Script Lint <small>v%s</small></h1>
<p>Deterministic static checks for TradingView Pine Script v5/v6: version annotation, declaration, brackets, strings, v4 remnants, undeclared reassignment, request.security lookahead (repaint), plot/security limits, unknown namespaces, indentation, optional naming conventions. Same source, same findings. It never claims the script compiles.</p>
<table><tr><th>Endpoint</th><th>Purpose</th></tr>
<tr><td><code>POST /v1/lint</code></td><td><code>{"source":"//@version=6\\nindicator(\\"x\\")\\nplot(close)","conventions":false}</code></td></tr>
<tr><td><a href="/v1/rules">GET /v1/rules</a></td><td>rule catalogue</td></tr>
<tr><td><a href="/openapi.json">GET /openapi.json</a></td><td>OpenAPI 3 spec</td></tr></table>
<p>Service id <code>%s</code> on Pocket Network. Siblings: <a href="https://pokt-agent.com/">POKT Settlement Agent</a>, <a href="https://watch.pokt-agent.com/">POKT Network Watch</a>, <a href="https://kr.pokt-agent.com/">KR Market Data</a>.</p></body></html>""" % (VERSION, SERVICE_ID)
