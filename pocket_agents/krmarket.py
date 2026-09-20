"""KR Market Data v0 (kr-market-data-v1) - Korean crypto exchange market data as a Pocket service.

Upbit and Bithumb public KRW markets (ticker, orderbook), a USD reference (Binance spot) and USD/KRW rates
(exchangerate-api daily + Yahoo intraday) combined into the "kimchi premium" per venue. No keys, no LLM.
Deterministic per read instant: every response carries fetched_at_utc and the exact source URLs; nothing is
estimated, forecast or advised.

Endpoints (REST, JSON object always):
  GET  /v1/version   GET /v1/health   GET /openapi.json
  GET  /v1/markets                 KRW markets on Upbit (+ whether Bithumb lists them), names ko/en
  GET  /v1/ticker/{SYMBOL}         both venues' KRW ticker
  GET  /v1/orderbook/{SYMBOL}?depth=5
  GET  /v1/premium/{SYMBOL}        KRW price vs USD reference x USDKRW, per venue
  GET  /v1/fx                      USDKRW from two public sources
  POST /v1/query {question, symbol?}   deterministic intent over the same data (ko/en)
"""
import concurrent.futures
import datetime
import http.server
import json
import re
import socketserver
import threading
import time
import urllib.parse
import urllib.request

SERVICE_ID = "kr-market-data-v1"
VERSION = "0.1.0"
UTC = datetime.timezone.utc
UA = "kr-market-data-v1/%s (Pocket Network service; public exchange APIs; read-only)" % VERSION
TIMEOUT = 6
UPBIT = "https://api.upbit.com/v1"
BITHUMB = "https://api.bithumb.com/v1"
BINANCE = "https://api.binance.com/api/v3/ticker/price?symbol=%sUSDT"
ERAPI = "https://open.er-api.com/v6/latest/USD"
YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/KRW%3DX?range=1d&interval=5m"
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")
KO_NAMES = {"비트코인": "BTC", "이더리움": "ETH", "리플": "XRP", "솔라나": "SOL", "도지": "DOGE", "도지코인": "DOGE", "에이다": "ADA", "포켓": "POKT",
            "테더": "USDT", "수이": "SUI", "세이": "SEI", "하이퍼리퀴드": "HYPE", "트론": "TRX", "체인링크": "LINK", "아발란체": "AVAX"}
_cache = {}
_lock = threading.Lock()

# transport is injectable for tests: fetch(url) -> parsed JSON
def _fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(4 * 1024 * 1024).decode("utf-8"))


fetch = _fetch


def _reason(e):
    """Short status code for a failed upstream call; never the exception text (design rule 6: no error-looking words)."""
    import socket
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP_%d" % e.code
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "TIMEOUT"
    if isinstance(e, (urllib.error.URLError, OSError)):
        return "NETWORK"
    if isinstance(e, (ValueError, KeyError, TypeError, IndexError)):
        return "PARSE"
    return "UPSTREAM"


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


def _sym(s):
    s = (s or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise ValueError("INVALID_SYMBOL")
    return s


def _venue_ticker(base, venue, sym):
    url = "%s/ticker?markets=KRW-%s" % (base, sym)
    try:
        rows = fetch(url)
        t = rows[0] if isinstance(rows, list) and rows else None
        if not t:
            return {"venue": venue, "status": "NOT_LISTED", "source": url}
        return {"venue": venue, "status": "OK", "market": t.get("market"), "trade_price_krw": t.get("trade_price"),
                "opening_price_krw": t.get("opening_price"), "high_price_krw": t.get("high_price"), "low_price_krw": t.get("low_price"),
                "prev_closing_price_krw": t.get("prev_closing_price"), "change": t.get("change"), "change_rate": t.get("signed_change_rate"),
                "acc_trade_volume_24h": t.get("acc_trade_volume_24h"), "acc_trade_price_24h_krw": t.get("acc_trade_price_24h"),
                "trade_time_utc": _ms_to_iso(t.get("trade_timestamp")),
                "data_lag_seconds": round(max(0.0, time.time() - int(t.get("trade_timestamp")) / 1000.0), 3) if t.get("trade_timestamp") else None, "source": url}
    except Exception as e:
        return {"venue": venue, "status": "UNAVAILABLE", "reason": _reason(e), "source": url}


def _ms_to_iso(ms):
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000.0, UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return None


def _venue_orderbook(base, venue, sym, depth):
    url = "%s/orderbook?markets=KRW-%s" % (base, sym)
    try:
        rows = fetch(url)
        ob = rows[0] if isinstance(rows, list) and rows else None
        if not ob:
            return {"venue": venue, "status": "NOT_LISTED", "source": url}
        units = (ob.get("orderbook_units") or [])[:depth]
        return {"venue": venue, "status": "OK", "market": ob.get("market"), "timestamp_utc": _ms_to_iso(ob.get("timestamp")),
                "total_ask_size": ob.get("total_ask_size"), "total_bid_size": ob.get("total_bid_size"),
                "units": [{"bid_price": u.get("bid_price"), "bid_size": u.get("bid_size"), "ask_price": u.get("ask_price"), "ask_size": u.get("ask_size")} for u in units],
                "source": url}
    except Exception as e:
        return {"venue": venue, "status": "UNAVAILABLE", "reason": _reason(e), "source": url}


def _parallel(tasks):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks) or 1) as ex:
        futs = {k: ex.submit(fn) for k, fn in tasks.items()}
        return {k: f.result() for k, f in futs.items()}


def ticker(sym):
    sym = _sym(sym)
    res = _cached("ticker:" + sym, 5, lambda: _parallel({"upbit": lambda: _venue_ticker(UPBIT, "upbit", sym), "bithumb": lambda: _venue_ticker(BITHUMB, "bithumb", sym)}))
    return {"schema": "kr-market-ticker/v1", "service": SERVICE_ID, "symbol": sym, "quote": "KRW", "fetched_at_utc": _now(), "venues": [res["upbit"], res["bithumb"]]}


def orderbook(sym, depth=5):
    sym = _sym(sym)
    depth = max(1, min(int(depth or 5), 30))
    res = _parallel({"upbit": lambda: _venue_orderbook(UPBIT, "upbit", sym, depth), "bithumb": lambda: _venue_orderbook(BITHUMB, "bithumb", sym, depth)})
    return {"schema": "kr-market-orderbook/v1", "service": SERVICE_ID, "symbol": sym, "quote": "KRW", "depth": depth, "fetched_at_utc": _now(), "venues": [res["upbit"], res["bithumb"]]}


def _usd_ref(sym):
    url = BINANCE % sym
    try:
        d = fetch(url)
        return {"venue": "binance", "status": "OK", "pair": "%sUSDT" % sym, "price_usdt": float(d["price"]), "source": url}
    except Exception as e:
        return {"venue": "binance", "status": "UNAVAILABLE", "reason": _reason(e), "source": url}


def _fx_erapi():
    try:
        d = fetch(ERAPI)
        return {"provider": "exchangerate-api (daily)", "status": "OK", "usdkrw": float(d["rates"]["KRW"]), "as_of_utc": d.get("time_last_update_utc"), "source": ERAPI}
    except Exception as e:
        return {"provider": "exchangerate-api (daily)", "status": "UNAVAILABLE", "reason": _reason(e), "source": ERAPI}


def _fx_yahoo():
    try:
        d = fetch(YAHOO)
        r = d["chart"]["result"][0]
        meta = r.get("meta") or {}
        px = meta.get("regularMarketPrice")
        if px is None:
            closes = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
            px = closes[-1] if closes else None
        mt = meta.get("regularMarketTime")
        return {"provider": "Yahoo Finance KRW=X (intraday)", "status": "OK" if px is not None else "UNAVAILABLE", "usdkrw": float(px) if px is not None else None,
                "as_of_utc": datetime.datetime.fromtimestamp(mt, UTC).isoformat(timespec="seconds") if mt else None, "source": YAHOO}
    except Exception as e:
        return {"provider": "Yahoo Finance KRW=X (intraday)", "status": "UNAVAILABLE", "reason": _reason(e), "source": YAHOO}


def fx():
    res = _cached("fx", 60, lambda: _parallel({"erapi": _fx_erapi, "yahoo": _fx_yahoo}))
    rates = [r for r in (res["yahoo"], res["erapi"]) if r.get("status") == "OK" and r.get("usdkrw")]
    return {"schema": "kr-market-fx/v1", "service": SERVICE_ID, "pair": "USDKRW", "fetched_at_utc": _now(), "sources": [res["yahoo"], res["erapi"]],
            "reference_usdkrw": rates[0]["usdkrw"] if rates else None, "reference_provider": rates[0]["provider"] if rates else None,
            "method": "first available of: Yahoo intraday, exchangerate-api daily"}


def premium(sym):
    sym = _sym(sym)
    res = _parallel({"tk": lambda: ticker(sym), "usd": lambda: _usd_ref(sym), "fx": fx})
    tk, usd, fxr = res["tk"], res["usd"], res["fx"]
    rate = fxr.get("reference_usdkrw")
    venues = []
    for v in tk["venues"]:
        row = {"venue": v["venue"], "status": v["status"], "trade_price_krw": v.get("trade_price_krw")}
        if v.get("status") == "OK" and usd.get("status") == "OK" and rate and v.get("trade_price_krw"):
            implied = usd["price_usdt"] * rate
            row.update(usd_reference_in_krw=round(implied, 4), premium_pct=round((float(v["trade_price_krw"]) / implied - 1.0) * 100.0, 4))
        else:
            row.update(premium_pct=None)
        venues.append(row)
    return {"schema": "kr-market-premium/v1", "service": SERVICE_ID, "symbol": sym, "fetched_at_utc": _now(), "venues": venues,
            "usd_reference": usd, "usdkrw": {"rate": rate, "provider": fxr.get("reference_provider")},
            "method": "premium_pct = KRW trade price / (Binance %sUSDT price x USDKRW) - 1; USDT treated as 1 USD; observed at fetch time, not a forecast" % sym,
            "sources": [v.get("source") for v in tk["venues"]] + [usd.get("source")] + [s.get("source") for s in fxr["sources"]]}


def executable(sym, qty, side="buy", fee_pct=0.0, depth=30):
    """Walk the orderbook: average fill price for `qty` base units per venue, coverage, and data lag. GPT rank-7 reframe (market integrity)."""
    sym = _sym(sym)
    try:
        qty = float(qty)
        fee_pct = float(fee_pct or 0.0)
    except (TypeError, ValueError):
        raise ValueError("INVALID_QTY_OR_FEE")
    if qty <= 0 or fee_pct < 0 or fee_pct > 10:
        raise ValueError("INVALID_QTY_OR_FEE")
    side = "sell" if str(side).lower() == "sell" else "buy"
    ob = orderbook(sym, depth)
    fetched = datetime.datetime.now(UTC)
    venues = []
    for v in ob["venues"]:
        row = {"venue": v["venue"], "status": v["status"]}
        if v.get("status") == "OK":
            remaining, cost, levels = qty, 0.0, 0
            for u in v["units"]:
                px = float(u["ask_price"] if side == "buy" else u["bid_price"])
                size = float(u["ask_size"] if side == "buy" else u["bid_size"])
                take = min(remaining, size)
                cost += take * px
                remaining -= take
                levels += 1
                if remaining <= 1e-12:
                    break
            filled = qty - max(remaining, 0.0)
            avg = cost / filled if filled > 0 else None
            best = float(v["units"][0]["ask_price"] if side == "buy" else v["units"][0]["bid_price"]) if v["units"] else None
            ts = _parse(v.get("timestamp_utc"))
            row.update(filled_qty=round(filled, 8), coverage=round(filled / qty, 4), levels_used=levels, average_price_krw=round(avg, 4) if avg else None,
                       best_price_krw=best, slippage_pct=round((avg / best - 1.0) * 100.0 * (1 if side == "buy" else -1), 4) if avg and best else None,
                       average_price_after_fee_krw=round(avg * (1 + fee_pct / 100.0 * (1 if side == "buy" else -1)), 4) if avg else None,
                       orderbook_lag_seconds=round(max(0.0, (fetched - ts).total_seconds()), 3) if ts else None,
                       note="coverage < 1 means the visible book (depth %d) cannot fill the quantity" % depth)
        venues.append(row)
    return {"schema": "kr-market-executable/v1", "service": SERVICE_ID, "symbol": sym, "quote": "KRW", "side": side, "qty": qty, "fee_pct": fee_pct, "depth": depth,
            "fetched_at_utc": fetched.isoformat(timespec="seconds"), "venues": venues,
            "asset_identity": "ticker-level match only (KRW-%s on each venue); chain/contract identity of the listed asset is not verified here" % sym,
            "method": "walk %s levels in price order until qty is filled; average_price = cost/filled; fee applied as a percentage; lag = fetch time - venue orderbook timestamp" % ("ask" if side == "buy" else "bid")}


def _parse(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None
    except (ValueError, AttributeError):
        return None


def markets():
    def load():
        up = fetch(UPBIT + "/market/all?isDetails=false")
        krw = [m for m in up if str(m.get("market", "")).startswith("KRW-")]
        try:
            bt = {m.get("market") for m in fetch(BITHUMB + "/market/all?isDetails=false")}
        except Exception:
            bt = None
        rows = [{"symbol": m["market"][4:], "market": m["market"], "korean_name": m.get("korean_name"), "english_name": m.get("english_name"),
                 "on_upbit": True, "on_bithumb": (m["market"] in bt) if bt is not None else None} for m in krw]
        return {"rows": rows, "bithumb_list": "OK" if bt is not None else "UNAVAILABLE"}
    d = _cached("markets", 3600, load)
    return {"schema": "kr-market-markets/v1", "service": SERVICE_ID, "quote": "KRW", "fetched_at_utc": _now(), "count": len(d["rows"]),
            "bithumb_listing_status": d["bithumb_list"], "markets": d["rows"], "sources": [UPBIT + "/market/all?isDetails=false", BITHUMB + "/market/all?isDetails=false"]}


INTENTS = ("PRICE", "PREMIUM", "ORDERBOOK", "MARKETS", "FX", "UNKNOWN")


def parse_question(q):
    q = q if isinstance(q, str) else ""
    if len(q) > 2000:
        raise ValueError("INVALID_QUESTION")
    sym = None
    for ko, s in KO_NAMES.items():
        if ko in q:
            sym = s
            break
    if not sym:
        m = re.search(r"\b([A-Z]{2,10})\b(?:/KRW|-KRW|USDT)?", q)
        if m and m.group(1) not in ("KRW", "USD", "USDKRW", "FX", "OK", "THE", "AND", "FOR", "ON"):
            sym = m.group(1)
    intent = "UNKNOWN"
    if re.search(r"premium|프리미엄|김치|김프", q, re.I):
        intent = "PREMIUM"
    elif re.search(r"orderbook|order book|호가|depth|bid|ask", q, re.I):
        intent = "ORDERBOOK"
    elif re.search(r"markets?\b|listed|상장|목록|list", q, re.I):
        intent = "MARKETS"
    elif re.search(r"\bfx\b|usdkrw|환율|dollar|달러|원/달러", q, re.I):
        intent = "FX"
    elif re.search(r"price|시세|가격|얼마|quote|ticker", q, re.I):
        intent = "PRICE"
    return {"intent": intent, "symbol": sym}


def answer(body):
    body = body if isinstance(body, dict) else {}
    q = body.get("question") or ""
    p = parse_question(q)
    sym = body.get("symbol") or p["symbol"]
    intent = p["intent"]
    res = {"schema": "kr-market-answer/v1", "service": SERVICE_ID, "intent": intent, "question": q[:300], "symbol": sym, "status": "OK"}
    if intent == "UNKNOWN":
        res.update(status="NEEDS_CLARIFICATION", missing=["intent: price | premium | orderbook | markets | fx"], intents=list(INTENTS))
        return res
    if intent == "MARKETS":
        res.update(data=markets())
        return res
    if intent == "FX":
        res.update(data=fx())
        return res
    if not sym:
        res.update(status="NEEDS_CLARIFICATION", missing=["symbol (e.g. BTC)"])
        return res
    if intent == "PRICE":
        res.update(data=ticker(sym))
    elif intent == "PREMIUM":
        res.update(data=premium(sym))
    elif intent == "ORDERBOOK":
        res.update(data=orderbook(sym, body.get("depth") or 5))
    return res


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
        p, _, qs = self.path.partition("?")
        qd = dict(urllib.parse.parse_qsl(qs))
        try:
            if p in ("/", "/index.html"):
                return self._html(200, UI_HTML)
            if p == "/openapi.json":
                return self._json(200, openapi_spec())
            if p == "/v1/version":
                return self._json(200, {"service": SERVICE_ID, "version": VERSION, "venues": ["upbit", "bithumb"], "quote": "KRW"})
            if p == "/v1/health":
                return self._json(200, {"status": "ok", "service": SERVICE_ID, "time_utc": _now()})
            if p == "/v1/markets":
                return self._json(200, markets())
            if p == "/v1/fx":
                return self._json(200, fx())
            m = re.match(r"^/v1/(ticker|orderbook|premium|executable)/([A-Za-z0-9]{2,12})$", p)
            if m:
                kind, sym = m.group(1), m.group(2)
                if kind == "ticker":
                    return self._json(200, ticker(sym))
                if kind == "orderbook":
                    return self._json(200, orderbook(sym, qd.get("depth") or 5))
                if kind == "executable":
                    return self._json(200, executable(sym, qd.get("qty") or 1, qd.get("side") or "buy", qd.get("fee_pct") or 0, int(qd.get("depth") or 30)))
                return self._json(200, premium(sym))
            return self._json(404, {"service": SERVICE_ID, "status": "NOT_FOUND"})
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        except Exception as e:
            return self._json(500, {"service": SERVICE_ID, "status": "INTERNAL", "detail": type(e).__name__})

    def do_HEAD(self):
        p = self.path.split("?", 1)[0]
        code = 200 if p in ("/", "/index.html", "/openapi.json", "/v1/version", "/v1/health", "/v1/markets", "/v1/fx") or p.startswith("/v1/ticker/") or p.startswith("/v1/premium/") else 404
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
            return self._json(200, answer(body))
        except ValueError as e:
            return self._json(400, {"service": SERVICE_ID, "status": "BAD_REQUEST", "detail": str(e)[:200]})
        except Exception as e:
            return self._json(500, {"service": SERVICE_ID, "status": "INTERNAL", "detail": type(e).__name__})


def serve(host="0.0.0.0", port=8795):
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), _Handler) as httpd:
        httpd.daemon_threads = True
        httpd.serve_forever()


def service_card(base="https://kr.pokt-agent.com"):
    return {
        "schema": "pocket-service-card/v1",
        "description": "KR Market Data and Integrity: are two Korean-exchange prices comparable, and what would a given quantity really cost? "
                       "Upbit and Bithumb KRW tickers with data lag, orderbooks, GET /v1/executable/{SYMBOL}?qty&side&fee_pct (orderbook-walked "
                       "average fill price, coverage, slippage, fee, lag per venue), the per-venue kimchi premium against a Binance USDT reference, "
                       "USD/KRW from two public sources and the KRW market list. Asset identity is ticker-level only and stated as such. Observed values "
                       "at fetch time; no forecasts, no advice, no keys. Every response is a JSON object. Identity via GET /v1/version, readiness via "
                       "GET /v1/health, plus /v1/ticker, /v1/orderbook, /v1/premium, /v1/markets, /v1/fx and POST /v1/query (Korean or English).",
        "updated": datetime.date.today().isoformat(),
        "rpc_types": [{"type": "REST", "intent": "expected", "backend_hint": "%s HTTP server on :8795; mount at /" % SERVICE_ID,
                       "notes": "SYMBOL is the base asset (BTC, ETH, XRP, POKT ...); quote is always KRW. Venue rows carry status OK | NOT_LISTED | UNAVAILABLE."}],
        "apis": ["%s-api" % SERVICE_ID],
        "specs": [{"kind": "openapi", "api": "%s-api" % SERVICE_ID, "url": "%s/openapi.json" % base, "notes": "Served by the same backend."}],
        "docs": base + "/",
        "access": "public",
        "results": "variable",
        "serving": {
            "backend": "Stateless proxy over public Upbit/Bithumb v1, Binance spot and USD/KRW sources with a 5-60 s in-process cache; no credentials.",
            "implementations": ["%s >= %s" % (SERVICE_ID, VERSION)], "min_disk_gb": 1, "min_ram_gb": 1,
            "healthcheck": [
                {"rpc_type": "REST", "request": {"path": "/v1/version", "method": "GET"}, "expect": {"json_path": "$.service", "matches": "^%s$" % SERVICE_ID},
                 "notes": "Identity probe: pins the backend to this service id."},
                {"rpc_type": "REST", "request": {"path": "/v1/health", "method": "GET"}, "expect": {"json_path": "$.status", "matches": "^ok$"}, "notes": "Readiness probe."},
                {"rpc_type": "REST", "request": {"path": "/v1/premium/BTC", "method": "GET"}, "expect": {"json_path": "$.schema", "matches": "^kr-market-premium/v1$"},
                 "notes": "Functional probe: live BTC premium with per-venue rows, USD reference and USDKRW sources."},
                {"rpc_type": "REST", "request": {"path": "/v1/query", "method": "POST", "headers": {"content-type": "application/json"}, "body": {"question": "비트코인 김치 프리미엄 얼마야?"}},
                 "expect": {"json_path": "$.intent", "matches": "^PREMIUM$"}, "notes": "Functional probe: Korean question routed deterministically."},
            ],
        },
    }


def openapi_spec(base="https://kr.pokt-agent.com"):
    sym = {"name": "symbol", "in": "path", "required": True, "schema": {"type": "string", "pattern": "^[A-Za-z0-9]{2,12}$"}, "example": "BTC"}
    return {
        "openapi": "3.0.3",
        "info": {"title": "KR Market Data", "version": VERSION, "description": "Upbit/Bithumb KRW market data, USD/KRW and kimchi premium for agents. Observed values only."},
        "servers": [{"url": base}],
        "paths": {
            "/v1/version": {"get": {"summary": "Identity", "responses": {"200": {"description": "service id and version"}}}},
            "/v1/health": {"get": {"summary": "Readiness", "responses": {"200": {"description": "{status: ok}"}}}},
            "/v1/markets": {"get": {"summary": "KRW markets (Upbit list, Bithumb listing flag), names ko/en", "responses": {"200": {"description": "kr-market-markets/v1"}}}},
            "/v1/fx": {"get": {"summary": "USDKRW from Yahoo intraday and exchangerate-api daily", "responses": {"200": {"description": "kr-market-fx/v1"}}}},
            "/v1/ticker/{symbol}": {"get": {"summary": "KRW ticker on Upbit and Bithumb", "parameters": [sym], "responses": {"200": {"description": "kr-market-ticker/v1"}, "400": {"description": "bad symbol"}}}},
            "/v1/orderbook/{symbol}": {"get": {"summary": "Top-of-book on both venues", "parameters": [sym, {"name": "depth", "in": "query", "schema": {"type": "integer", "default": 5, "maximum": 30}}],
                                                "responses": {"200": {"description": "kr-market-orderbook/v1"}}}},
            "/v1/premium/{symbol}": {"get": {"summary": "Kimchi premium per venue vs Binance USDT x USDKRW", "parameters": [sym], "responses": {"200": {"description": "kr-market-premium/v1"}}}},
            "/v1/executable/{symbol}": {"get": {"summary": "Orderbook-walked average fill price for a quantity: coverage, slippage, fee, data lag per venue",
                "parameters": [sym, {"name": "qty", "in": "query", "required": True, "schema": {"type": "number"}}, {"name": "side", "in": "query", "schema": {"type": "string", "enum": ["buy", "sell"], "default": "buy"}},
                               {"name": "fee_pct", "in": "query", "schema": {"type": "number", "default": 0}}, {"name": "depth", "in": "query", "schema": {"type": "integer", "default": 30, "maximum": 30}}],
                "responses": {"200": {"description": "kr-market-executable/v1"}, "400": {"description": "bad quantity or fee"}}}},
            "/v1/query": {"post": {"summary": "Deterministic question (ko/en) -> one of the endpoints above", "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "properties": {"question": {"type": "string", "example": "비트코인 김치 프리미엄 얼마야?"}, "symbol": {"type": "string"}, "depth": {"type": "integer"}}}}}},
                "responses": {"200": {"description": "kr-market-answer/v1 with intent, symbol, data"}, "400": {"description": "bad request"}}}},
        },
    }


UI_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KR Market Data</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
code{background:#f4f4f6;border-radius:4px;padding:.1rem .3rem}h1{font-size:1.5rem}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.3rem .6rem;text-align:left}</style></head>
<body><h1>KR Market Data <small>v%s</small></h1>
<p>Korean crypto exchange market data for agents: Upbit and Bithumb KRW tickers and orderbooks, the KRW market list, USD/KRW rates and the per-venue kimchi premium against a Binance USDT reference. Observed values at fetch time, every response a JSON object, no keys.</p>
<table><tr><th>Endpoint</th><th>Purpose</th></tr>
<tr><td><a href="/v1/ticker/BTC">GET /v1/ticker/BTC</a></td><td>KRW ticker on both venues</td></tr>
<tr><td><a href="/v1/orderbook/BTC?depth=5">GET /v1/orderbook/BTC</a></td><td>top-of-book on both venues</td></tr>
<tr><td><a href="/v1/premium/BTC">GET /v1/premium/BTC</a></td><td>kimchi premium per venue, method stated</td></tr>
<tr><td><a href="/v1/markets">GET /v1/markets</a></td><td>KRW markets, names ko/en, Bithumb listing flag</td></tr>
<tr><td><a href="/v1/fx">GET /v1/fx</a></td><td>USDKRW from two public sources</td></tr>
<tr><td><code>POST /v1/query</code></td><td><code>{"question":"비트코인 김치 프리미엄 얼마야?"}</code></td></tr>
<tr><td><a href="/openapi.json">GET /openapi.json</a></td><td>OpenAPI 3 spec</td></tr></table>
<p>Service id <code>%s</code> on Pocket Network. Siblings: <a href="https://pokt-agent.com/">POKT Settlement Agent</a>, <a href="https://watch.pokt-agent.com/">POKT Network Watch</a>.</p></body></html>""" % (VERSION, SERVICE_ID)
