"""Budgeted HTTP client.

Every outbound request (including retries and robots.txt checks) is counted so a
run can never exceed the daily limits (2,000 requests / 45 minutes).
"""
import json
import threading
import time
import urllib.error
import urllib.request

USER_AGENT = "SignalpostAgent/1.0 (company-profile research; open public data)"


class BudgetExceeded(Exception):
    pass


class Response:
    def __init__(self, url, status, body, final_url=None, headers=None):
        self.url = url
        self.status = status
        self.body = body
        self.final_url = final_url or url
        self.headers = headers or {}

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def text(self):
        charset = "utf-8"
        ctype = self.headers.get("Content-Type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")


class Client:
    def __init__(self, max_requests=2000, deadline_seconds=45 * 60, max_bytes=1_500_000):
        self.max_requests = max_requests
        self.deadline = time.monotonic() + deadline_seconds
        self.max_bytes = max_bytes
        self.count = 0
        self.log = []  # (url, status) for audit
        self._lock = threading.Lock()

    @property
    def remaining(self):
        return self.max_requests - self.count

    def time_left(self):
        return self.deadline - time.monotonic()

    def _reserve(self):
        with self._lock:
            if self.count >= self.max_requests:
                raise BudgetExceeded("request budget exhausted")
            if time.monotonic() >= self.deadline:
                raise BudgetExceeded("time budget exhausted")
            self.count += 1

    def get(self, url, timeout=15, retries=2, accept="application/json", max_bytes=None):
        """GET a URL. Returns Response (also for 4xx). Raises BudgetExceeded or
        the last network error if all attempts fail."""
        last_err = None
        max_bytes = max_bytes or self.max_bytes
        for attempt in range(retries + 1):
            self._reserve()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body = r.read(max_bytes)
                    resp = Response(url, r.status, body, r.geturl(), dict(r.headers))
            except urllib.error.HTTPError as e:
                resp = Response(url, e.code, e.read(max_bytes) if e.fp else b"", url, dict(e.headers or {}))
            except Exception as e:  # network / TLS / timeout
                last_err = e
                with self._lock:
                    self.log.append((url, f"error:{type(e).__name__}"))
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                continue
            with self._lock:
                self.log.append((url, resp.status))
            if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return resp
        raise last_err
