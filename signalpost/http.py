"""Budgeted HTTP client with evidence capture.

Every outbound request (retries, robots.txt and redirects' final hop included)
is counted so a run never exceeds its request/time budget. Each response keeps
its redirect chain and the SHA-256 of the body so claims can cite an immutable
snapshot; bodies can optionally be stored gzip-compressed by hash.
"""
import functools
import gzip
import hashlib
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

USER_AGENT = f"SignalpostAgent/{__version__} (company-profile research; respects robots.txt)"


class BudgetExceeded(Exception):
    pass


class UnsafeURL(Exception):
    """Outbound URL policy: only http(s) on standard ports to public IP addresses."""


@functools.lru_cache(maxsize=4096)
def _public_host(host, port):
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return None  # unresolvable: let the request fail normally
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def check_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UnsafeURL(f"scheme not allowed: {url[:80]}")
    if p.username or p.password:
        raise UnsafeURL("credentials in URL not allowed")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in (80, 443, 8080, 8443):
        raise UnsafeURL(f"port {port} not allowed")
    if _public_host(p.hostname.lower(), port) is False:
        raise UnsafeURL(f"{p.hostname} resolves to a non-public address")


class Response:
    def __init__(self, url, status, body, final_url=None, headers=None, redirects=None, elapsed_ms=0):
        self.url = url
        self.status = status
        self.body = body or b""
        self.final_url = final_url or url
        self.headers = headers or {}
        self.redirects = redirects or []
        self.elapsed_ms = elapsed_ms
        self.sha256 = hashlib.sha256(self.body).hexdigest()
        self.retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def text(self):
        charset = "utf-8"
        ctype = self.headers.get("Content-Type", "") or self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip().strip('"') or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({"status": code, "url": req.full_url, "location": newurl})
        if len(self.chain) > 8:
            return None
        check_url(urllib.parse.urljoin(req.full_url, newurl))  # every hop obeys the outbound policy
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, max_requests=20000, deadline_seconds=45 * 60, max_bytes=2_000_000, snapshot_dir=None):
        self.max_requests = max_requests
        self.started = time.monotonic()
        self.deadline = self.started + deadline_seconds
        self.max_bytes = max_bytes
        self.count = 0
        self.bytes = 0
        self.latencies = []
        self.log = []  # (url, status) audit trail
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        if self.snapshot_dir:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
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

    def _store(self, resp):
        if not self.snapshot_dir or not resp.body:
            return
        p = self.snapshot_dir / f"{resp.sha256}.gz"
        if not p.exists():
            tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(gzip.compress(resp.body, compresslevel=6))
            tmp.replace(p)

    def get(self, url, timeout=15, retries=2, accept="application/json", max_bytes=None, headers=None):
        """GET a URL. Returns a Response for any HTTP status. Raises BudgetExceeded,
        or the last network error if every attempt failed."""
        last_err = None
        max_bytes = max_bytes or self.max_bytes
        check_url(url)
        for attempt in range(retries + 1):
            self._reserve()
            redirects = _Redirects()
            opener = urllib.request.build_opener(redirects)
            hdrs = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.6"}
            hdrs.update(headers or {})
            req = urllib.request.Request(url, headers=hdrs)
            t0 = time.monotonic()
            try:
                with opener.open(req, timeout=timeout) as r:
                    body = r.read(max_bytes)
                    resp = Response(url, r.status, body, r.geturl(), dict(r.headers), redirects.chain)
            except urllib.error.HTTPError as e:
                try:
                    body = e.read(max_bytes) if e.fp else b""
                except Exception:
                    body = b""
                resp = Response(url, e.code, body, url, dict(e.headers or {}), redirects.chain)
            except UnsafeURL:
                with self._lock:
                    self.log.append((url, "blocked:outbound_policy"))
                raise
            except Exception as e:  # DNS, TLS, timeout, reset
                last_err = e
                with self._lock:
                    self.log.append((url, f"error:{type(e).__name__}"))
                if attempt < retries:
                    time.sleep(min(8, 0.5 * 2 ** attempt))
                continue
            resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
            with self._lock:
                self.log.append((url, resp.status))
                self.bytes += len(resp.body)
                self.latencies.append(resp.elapsed_ms)
            if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            if resp.status < 400:
                self._store(resp)
            return resp
        raise last_err
