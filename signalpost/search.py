"""Optional search-API candidate discovery.

Off unless a key is supplied through an environment variable:

    BRAVE_SEARCH_API_KEY   Brave Search API   (https://api.search.brave.com)
    SERPER_API_KEY         Serper             (https://google.serper.dev)
    TAVILY_API_KEY         Tavily             (https://api.tavily.com)

Search output is *transient*: it is used only to propose candidate domains,
never stored (no snapshot, no title/snippet/rank kept) and never cited as
evidence. Every candidate is then fetched independently and must pass the
same exact-entity gate as a domain guessed from the legal name: the
organisation number on the site, or the full name plus independent
corroboration on a .no site. Directories, social networks and registries are
discarded before any fetch.

Rate and volume are bounded per run:
    SIGNALPOST_SEARCH_QPS          queries per second (default 1, the Brave free-tier limit)
    SIGNALPOST_SEARCH_MAX_QUERIES  queries per run    (default 2 per company)

Without SIGNALPOST_SEARCH_QPS, the Brave rate follows the key's plan: the per-second limit in
the X-RateLimit-Limit response header (capped at 20/s), and the run stops querying before the
key's monthly allowance in X-RateLimit-Remaining runs out.
"""
import json
import os
import re
import threading
import time
import urllib.parse

from .http import BudgetExceeded

# never a company's own site
EXCLUDED = {
    "proff.no", "purehelp.no", "gulesider.no", "1881.no", "180.no", "brreg.no", "forvalt.no", "finn.no",
    "regnskapstall.no", "bedriftsdatabasen.no", "kompass.com", "dnb.com", "northdata.com", "northdata.de",
    "opencorporates.com", "allabolag.se", "proff.se", "proff.dk", "firmanett.no", "bizzdo.no", "enirobedrift.no",
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com",
    "pinterest.com", "wikipedia.org", "wikidata.org", "google.com", "google.no", "apple.com", "bing.com",
    "tripadvisor.com", "tripadvisor.no", "yelp.com", "trustpilot.com", "mittanbud.no", "anbudstorget.no",
    "nav.no", "arbeidsplassen.no", "regjeringen.no", "lovdata.no", "doffin.no", "altinn.no", "skatteetaten.no",
    "norid.no", "domeneshop.no", "uniweb.no", "one.com", "godaddy.com", "sedo.com", "dan.com", "afternic.com",
    "nettavisen.no", "vg.no", "dagbladet.no", "aftenposten.no", "e24.no", "dn.no", "tu.no", "nrk.no",
    "kartverket.no", "seeiendom.no", "eiendomsverdi.no", "hjemla.no", "boligsiden.no", "amazon.com",
    "kunngjoringer.no", "virk.dk", "companieshouse.gov.uk", "bloomberg.com", "crunchbase.com", "zoominfo.com",
    "rocketreach.co", "apollo.io", "glassdoor.com", "glassdoor.no", "indeed.com", "jobbnorge.no",
    "webcruiter.no", "teamtailor.com", "mapquest.com", "waze.com", "hitta.se", "eniro.se", "krak.dk",
    "infobel.com", "cylex.no", "bedriftskatalogen.no", "norgesbedrift.no", "firmaregister.no",
    "bedriftsfakta.no", "firmasok.no", "norskefirmaer.no", "proff.com", "bisnode.no", "infotorg.no", "ravninfo.no",
    "creditsafe.com", "soliditet.no", "purehelp.com", "bedrift.info", "firmaliste.no", "enhetsregisteret.no",
    "regnskapstall.com", "dun.com", "dnbhoovers.com", "emis.com", "europages.com", "europages.no", "yellow.place",
    "hotfrog.no", "bedriftsguiden.no", "firmainfo.no", "brreg.info", "ledigestillinger.no", "stepstone.no",
    "karriere.no", "jobindex.no", "jobbsafari.no", "simplyhired.com", "medium.com", "reddit.com", "issuu.com",
    "booking.com", "hotels.com", "expedia.com", "airbnb.com", "airbnb.no", "visitnorway.com", "foodora.no", "wolt.com",
    "goo.gl", "bit.ly", "github.com", "gitlab.com",
}
ENDPOINTS = {
    "brave": "https://api.search.brave.com/res/v1/web/search",
    "serper": "https://google.serper.dev/search",
    "tavily": "https://api.tavily.com/search",
}
ENV = {"brave": "BRAVE_SEARCH_API_KEY", "serper": "SERPER_API_KEY", "tavily": "TAVILY_API_KEY"}


def _reg(host):
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def excluded(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host:
        return True
    reg = _reg(host)
    return reg in EXCLUDED or any(host.endswith("." + d) or host == d for d in EXCLUDED)


class Searcher:
    MAX_QPS = 20.0

    def __init__(self, provider, key, qps=1.0, max_queries=2000, adaptive=False):
        self.provider, self.key = provider, key
        self.adaptive = adaptive  # follow the provider's rate-limit headers (no explicit QPS set)
        self.interval = 1.0 / max(0.05, qps)
        self.max_queries = max_queries
        self.queries = 0
        self.errors = 0
        self._lock = threading.Lock()
        self._next = 0.0
        self.disabled = None  # reason, once the provider rejects the key
        self.reserve = 300  # seconds of run time kept free of search waits
        # list-price estimate per query, for the declared cost (override: SIGNALPOST_SEARCH_COST_PER_QUERY)
        self.cost_per_query = float(os.environ.get("SIGNALPOST_SEARCH_COST_PER_QUERY") or
                                    {"brave": 0.005, "serper": 0.001, "tavily": 0.008}[provider])

    def describe(self):
        return {"provider": self.provider, "queries": self.queries, "errors": self.errors,
                "qps": round(1.0 / self.interval, 2),
                "estimated_cost_usd": round(self.queries * self.cost_per_query, 4),
                "max_queries": self.max_queries, "disabled": self.disabled,
                "storage": "transient: candidate domains only; results are not stored or cited"}

    def _slot(self, client, reserve_seconds):
        """Wait for the rate limiter. Returns False when the run cannot afford the wait."""
        with self._lock:
            if self.disabled or self.queries >= self.max_queries:
                return False
            now = time.monotonic()
            start = max(now, self._next)
            if client.time_left() - (start - now) < reserve_seconds:
                return False
            self._next = start + self.interval
            self.queries += 1
        if start > now:
            time.sleep(start - now)
        return True

    def _adapt(self, headers):
        """Brave reports `X-RateLimit-Limit: <per second>, <per month>` and the same shape in
        `X-RateLimit-Remaining`. Raise the rate to the plan's limit; stop before the monthly quota."""
        h = {k.lower(): v for k, v in (headers or {}).items()}

        def nums(name):
            return [int(x) for x in re.findall(r"\d+", h.get(name, ""))]
        limit, remaining = nums("x-ratelimit-limit"), nums("x-ratelimit-remaining")
        with self._lock:
            if self.adaptive and limit and limit[0] > 0:
                self.interval = 1.0 / min(self.MAX_QPS, float(limit[0]))
            if len(remaining) >= 2:
                self.max_queries = min(self.max_queries, self.queries + remaining[1])

    def search(self, client, query):
        """Result URLs (most relevant first), or [] when skipped or failed."""
        if not self._slot(client, self.reserve):
            return []
        try:
            if self.provider == "brave":
                url = ENDPOINTS["brave"] + "?" + urllib.parse.urlencode(
                    {"q": query, "count": 10, "country": "NO", "safesearch": "off", "text_decorations": 0})
                r = client.get(url, timeout=12, retries=1, store=False,
                               headers={"X-Subscription-Token": self.key, "Accept": "application/json"})
                items = (r.json().get("web") or {}).get("results") or [] if r.status == 200 else []
                urls = [i.get("url") for i in items]
            elif self.provider == "serper":
                body = json.dumps({"q": query, "gl": "no", "num": 10}).encode()
                r = client.get(ENDPOINTS["serper"], timeout=12, retries=1, store=False, data=body,
                               headers={"X-API-KEY": self.key, "Content-Type": "application/json"})
                urls = [i.get("link") for i in (r.json().get("organic") or [])] if r.status == 200 else []
            else:
                body = json.dumps({"query": query, "max_results": 10, "search_depth": "basic"}).encode()
                r = client.get(ENDPOINTS["tavily"], timeout=15, retries=1, store=False, data=body,
                               headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
                urls = [i.get("url") for i in (r.json().get("results") or [])] if r.status == 200 else []
        except BudgetExceeded:
            raise
        except Exception:
            with self._lock:
                self.errors += 1
            return []
        if self.provider == "brave":
            self._adapt(r.headers)
        if r.status in (401, 403):
            self.disabled = f"provider rejected the key (http {r.status})"
        elif r.status != 200:
            with self._lock:
                self.errors += 1
        return [u for u in urls if isinstance(u, str) and u.startswith(("http://", "https://"))]


def from_env(n_companies=1000):
    for provider in ("brave", "serper", "tavily"):
        key = (os.environ.get(ENV[provider]) or "").strip()
        if key:
            fixed = os.environ.get("SIGNALPOST_SEARCH_QPS")
            qps = float(fixed or (1.0 if provider == "brave" else 5.0))
            cap = int(os.environ.get("SIGNALPOST_SEARCH_MAX_QUERIES") or 2 * max(1, n_companies))
            return Searcher(provider, key, qps=qps, max_queries=cap, adaptive=not fixed)
    return None


LEGAL_SUFFIX = re.compile(r"\s+(as|asa|sa|ans|da|ba|nuf|enk|ks|aksjeselskap|i likvidasjon|under avvikling)\.?$", re.I)


def queries(ctx):
    """Name query first (finds the brand site), then the org. no. (finds the footer of the company's own site)."""
    name = LEGAL_SUFFIX.sub("", (ctx.get("name") or "").strip()).strip()
    town = ((ctx.get("address") or {}).get("poststed") or "").title()
    out = []
    if name:
        out.append(f'"{name}" {town}'.strip())
    org = ctx.get("orgnr") or ""
    if len(org) == 9:
        out.append(f'"{org[:3]} {org[3:6]} {org[6:]}" OR "{org}"')
    return out


def candidate_urls(results, limit=4):
    seen, out = set(), []
    for u in results:
        if excluded(u):
            continue
        host = (urllib.parse.urlsplit(u).hostname or "").lower()
        reg = _reg(host)
        if reg in seen:
            continue
        seen.add(reg)
        out.append(f"https://{host}")
        if len(out) >= limit:
            break
    return out
