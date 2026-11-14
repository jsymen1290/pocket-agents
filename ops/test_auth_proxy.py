import http.server, json, os, subprocess, sys, threading, time, urllib.request, urllib.error, tempfile
seen = {}
class B(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        seen["h"] = self.headers.get("X-Key"); code = 200 if seen["h"] == "s3cret" else 401
        self.send_response(code); self.end_headers(); self.wfile.write(b'{"ok":true}')
    def do_POST(self):
        n = int(self.headers["Content-Length"]); seen["b"] = self.rfile.read(n); self.send_response(200); self.end_headers(); self.wfile.write(b'{"p":1}')
srv = http.server.HTTPServer(("127.0.0.1", 18701), B); threading.Thread(target=srv.serve_forever, daemon=True).start()
d = tempfile.mkdtemp(); cfg = os.path.join(d, "c"); open(cfg, "w").write("url=http://127.0.0.1:18701\nheader=X-Key\nvalue=s3cret\n"); os.chmod(cfg, 0o600)
p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "auth_proxy.py"), "18702", cfg]); time.sleep(1.2)
try:
    r = urllib.request.urlopen("http://127.0.0.1:18702/health"); assert r.status == 200 and seen["h"] == "s3cret"
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18702/v1/x", data=b'{"a":1}', headers={"Content-Type": "application/json"})); assert seen["b"] == b'{"a":1}'
    srv.shutdown(); srv2 = None
    try: urllib.request.urlopen("http://127.0.0.1:18702/health"); raise SystemExit("expected 502")
    except urllib.error.HTTPError as e: assert e.code == 502
    os.chmod(cfg, 0o644); q = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "auth_proxy.py"), "18703", cfg], capture_output=True, text=True); assert q.returncode != 0 and "600" in q.stderr
    print("OK")
finally: p.kill()
