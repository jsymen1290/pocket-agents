"""Keep a service's caches warm by replaying its own card probes against itself.

Why (2026-09-25): PNF runs an internal monitor over new portal services and QoS feeds the agent
indexes. Measured first-call latency after a restart or cache expiry: dart-kr-events-v1 3.81 s
(corp table parse + OpenDART fetch), kr-export-pulse-v1 0.88 s (Korea Customs fetch); the second
call is 0.03 s and 0.005 s. Replaying the exact probe requests declared in the service card fills
the exact cache keys the monitor and first callers will hit.

The thread only calls 127.0.0.1 on the service's own port, never an external host directly, and
never raises into the server. Disable with POKT_WARMUP=0.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request


def enabled():
    return os.environ.get("POKT_WARMUP", "1").strip().lower() not in ("0", "false", "no")


def probes_from_card(card):
    """The REST requests a monitor would send, as (method, path, body-or-None)."""
    out = []
    for p in ((card.get("serving") or {}).get("healthcheck") or []):
        req = p.get("request") or {}
        path = req.get("path")
        if not path:
            continue
        out.append(((req.get("method") or "GET").upper(), path, req.get("body")))
    return out


def run_once(port, probes, timeout=60):
    """Send every probe once. Returns [(method, path, status_or_error, seconds)]."""
    results = []
    for method, path, body in probes:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
                                     headers={"content-type": "application/json", "user-agent": "pokt-warmup/1"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception as e:  # never propagate into the server
            status = "ERR %s" % type(e).__name__
        results.append((method, path, status, round(time.time() - t0, 3)))
    return results


def start(port, probes, interval, name="", first_delay=2.0):
    """Warm once shortly after start, then every `interval` seconds. Returns the thread or None."""
    if not enabled() or not probes:
        return None

    def loop():
        time.sleep(first_delay)
        first = True
        while True:
            res = run_once(port, probes)
            bad = [r for r in res if r[2] != 200]
            if first or bad:
                print("warmup %s: %s" % (name, "; ".join("%s %s %s %.2fs" % r for r in res)), file=sys.stderr, flush=True)
            first = False
            time.sleep(interval)

    t = threading.Thread(target=loop, name="warmup-%s" % name, daemon=True)
    t.start()
    return t
