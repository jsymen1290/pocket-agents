"""GET-only HTTP client for public Pocket endpoints.

Guarantees: only allow-listed origins, no credentials, no redirects followed, per-request timeout
and byte cap, per-cycle request and byte budget, one receipt (url, status, sha256, bytes, utc) per
request. A failed primary origin falls through to the next allowed origin of the same kind.
"""
import datetime
import hashlib
import json
import socket
import urllib.error
import urllib.request

REST_ORIGINS = ("https://sauron-api.infra.pocket.network", "https://api-pocket.blockval.io")
RPC_ORIGINS = ("https://sauron-rpc.infra.pocket.network", "https://rpc-pocket.blockval.io")
ALLOWED_ORIGINS = REST_ORIGINS + RPC_ORIGINS

DEFAULT_LIMITS = {
    "requests": 60,                    # per collection cycle
    "timeout_seconds": 20,
    "response_bytes": 50 * 1024 * 1024,  # block_results of a settlement block can be several MB
    "total_bytes": 160 * 1024 * 1024,
}


class BudgetExceeded(Exception):
    pass


class HttpFailure(Exception):
    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Client:
    def __init__(self, limits=None, transport=None):
        self.limits = dict(DEFAULT_LIMITS)
        if limits:
            self.limits.update(limits)
        self.receipts = []
        self.request_count = 0
        self.total_bytes = 0
        self.transport = transport  # tests: callable(url) -> (status, bytes)
        self._opener = urllib.request.build_opener(_NoRedirect())

    # -- low level -------------------------------------------------------------------------
    def _check_url(self, url):
        origin = url.split("/", 3)
        origin = "/".join(origin[:3]) if len(origin) >= 3 else url
        if origin not in ALLOWED_ORIGINS:
            raise HttpFailure("ORIGIN_NOT_ALLOWED: %s" % origin)
        if "@" in url.split("?", 1)[0]:
            raise HttpFailure("URL_USERINFO_NOT_ALLOWED")

    def get_bytes(self, url):
        self._check_url(url)
        if self.request_count >= self.limits["requests"]:
            raise BudgetExceeded("REQUEST_LIMIT %d" % self.limits["requests"])
        self.request_count += 1
        receipt = {"sequence": self.request_count, "url": url, "method": "GET", "started_at_utc": _utc(),
                   "status": None, "bytes": None, "sha256": None, "error": None}
        self.receipts.append(receipt)
        try:
            if self.transport is not None:
                status, body = self.transport(url)
            else:
                req = urllib.request.Request(url, headers={"accept": "application/json",
                                                           "user-agent": "llm-runtime-pokt-readonly/0.5"})
                try:
                    resp = self._opener.open(req, timeout=self.limits["timeout_seconds"])
                except urllib.error.HTTPError as e:
                    status, body = e.code, e.read(self.limits["response_bytes"] + 1)
                else:
                    with resp:
                        status = resp.status
                        body = resp.read(self.limits["response_bytes"] + 1)
            if len(body) > self.limits["response_bytes"]:
                raise HttpFailure("RESPONSE_BYTES_EXCEEDED")
            self.total_bytes += len(body)
            if self.total_bytes > self.limits["total_bytes"]:
                raise BudgetExceeded("TOTAL_BYTES_EXCEEDED")
            receipt["status"] = status
            receipt["bytes"] = len(body)
            receipt["sha256"] = hashlib.sha256(body).hexdigest()
            receipt["completed_at_utc"] = _utc()
            if status != 200:
                raise HttpFailure("HTTP_%s" % status, status)
            return body
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            receipt["error"] = str(e)[:300]
            receipt["completed_at_utc"] = _utc()
            raise HttpFailure("NETWORK: %s" % str(e)[:200])
        except (HttpFailure, BudgetExceeded) as e:
            receipt["error"] = str(e)[:300]
            receipt["completed_at_utc"] = _utc()
            raise

    def get_json(self, url):
        body = self.get_bytes(url)
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise HttpFailure("BAD_JSON: %s" % str(e)[:100])
        if isinstance(data, dict) and data.get("error") and "result" not in data:
            raise HttpFailure("RPC_ERROR: %s" % json.dumps(data.get("error"))[:200])
        return data

    # -- origin fallback -------------------------------------------------------------------
    def rest(self, path):
        return self._first_ok(REST_ORIGINS, path)

    def rpc(self, path):
        return self._first_ok(RPC_ORIGINS, path)

    def _first_ok(self, origins, path):
        last = None
        for origin in origins:
            try:
                return self.get_json(origin + path)
            except BudgetExceeded:
                raise
            except HttpFailure as e:
                last = e
                if e.status is not None and 400 <= e.status < 500 and e.status != 429:
                    raise  # a 4xx is an answer, not an outage
        raise last if last else HttpFailure("NO_ORIGIN")

    def summary(self):
        return {"requests": self.request_count, "bytes": self.total_bytes,
                "failed": sum(1 for r in self.receipts if r["error"])}
