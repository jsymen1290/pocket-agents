#!/usr/bin/env python3
"""Local relay-miner backend that forwards to one upstream and adds one auth header.
Usage: auth_proxy.py PORT CONFIG   CONFIG lines: url=<https://...>  header=<Name>  value=<secret> (file mode must be 600)."""
import http.server, os, socketserver, stat, sys, urllib.request, urllib.error

def load(path):
    if os.stat(path).st_mode & 0o077: sys.exit("config must be mode 600")
    c = dict(l.rstrip("\n").split("=", 1) for l in open(path) if "=" in l and not l.startswith("#"))
    return c["url"].rstrip("/"), c["header"], c["value"]

class H(http.server.BaseHTTPRequestHandler):
    timeout = 60
    def log_message(self, *a): pass
    def go(self):
        n = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(n) if n else None
        req = urllib.request.Request(UP + self.path, data=body, method=self.command)
        for k in ("Content-Type", "Accept"):
            if self.headers.get(k): req.add_header(k, self.headers[k])
        req.add_header(HDR, VAL)
        try:
            with urllib.request.urlopen(req, timeout=25) as r: st, out, ct = r.status, r.read(), r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e: st, out, ct = e.code, e.read(), e.headers.get("Content-Type", "application/json")
        except Exception as e: st, out, ct = 502, ('{"error":"upstream: %s"}' % type(e).__name__).encode(), "application/json"
        self.send_response(st); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    do_GET = do_POST = go

class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True

if __name__ == "__main__":
    UP, HDR, VAL = load(sys.argv[2])
    S(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
