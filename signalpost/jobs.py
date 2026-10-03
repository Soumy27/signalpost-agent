"""Job ads from NAV's public job-vacancy feed (arbeidsplassen.nav.no).

The feed (https://pam-stilling-feed.nav.no) is NAV's open re-use API; it is
read with the public token NAV publishes at /api/publicToken. Feed pages list
ads by employer *name* only, so the agent keeps a local index of active ads
(a declared cache, refreshed incrementally on every run) and uses it to find
candidate ads. A candidate is published only after the ad detail confirms the
employer organisation number equals the company (or one of its registered
subunits).
"""
import gzip
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from .http import BudgetExceeded

FEED = "https://pam-stilling-feed.nav.no"
BUNDLED_CACHE = Path(__file__).parent / "data" / "nav_jobs_index.jsonl.gz"
LEGAL = {"as", "asa", "ans", "da", "enk", "sa", "nuf", "ba", "ks", "iks", "sf", "avd", "avdeling", "stiftelsen"}


def norm_name(name):
    t = (name or "").casefold().translate(str.maketrans({"ø": "o", "å": "a", "æ": "ae"}))
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", t))


def core_name(name):
    return " ".join(w for w in norm_name(name).split() if w not in LEGAL)


class JobIndex:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.ads = {}
        self.synced_until = None
        self.sync_info = {"pages": 0, "status": "not_synced"}
        self._token = None
        self._lock = threading.Lock()
        src = self.path if self.path and self.path.exists() else BUNDLED_CACHE
        if src.exists():
            self._load(src)
        self._by_name = None

    def _load(self, src):
        with gzip.open(src, "rt", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if "_meta" in row:
                    self.synced_until = row["_meta"].get("synced_until")
                else:
                    self.ads[row["uuid"]] = row

    def save(self, path=None):
        path = Path(path or self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(json.dumps({"_meta": {"synced_until": self.synced_until, "source": FEED}}) + "\n")
            for ad in sorted(self.ads.values(), key=lambda a: a["uuid"]):
                if ad.get("status") == "ACTIVE":
                    f.write(json.dumps(ad, ensure_ascii=False) + "\n")
        tmp.replace(path)

    def token(self, client):
        if not self._token:
            r = client.get(f"{FEED}/api/publicToken", accept="text/plain", retries=1)
            m = re.search(r"eyJ[\w.-]+", r.text())
            if not m:
                raise RuntimeError("NAV public token unavailable")
            self._token = m.group(0)
        return self._token

    def sync(self, client, bootstrap_days=100, max_pages=400, stop_seconds=None):
        """Pull feed pages modified since the last sync (incremental)."""
        start = self.synced_until or (datetime.now(timezone.utc) - timedelta(days=bootstrap_days)).isoformat()
        since = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
        hdr = {"Authorization": f"Bearer {self.token(client)}", "If-Modified-Since": format_datetime(since, usegmt=True)}
        url = f"{FEED}/api/v1/feed"
        pages = 0
        try:
            while pages < max_pages:
                if stop_seconds is not None and client.time_left() < stop_seconds:
                    self.sync_info["status"] = "partial_time_budget"
                    break
                r = client.get(url, timeout=60, max_bytes=30_000_000, headers=hdr, retries=2)
                if r.status != 200:
                    self.sync_info["status"] = f"http_{r.status}"
                    break
                data = r.json()
                items = data.get("items") or []
                pages += 1
                for it in items:
                    e = it.get("_feed_entry") or {}
                    uid = e.get("uuid") or it["id"]
                    old = self.ads.get(uid) or {}
                    row = {"uuid": uid, "title": e.get("title") or it.get("title"),
                           "business": e.get("businessName"), "status": e.get("status"),
                           "municipal": e.get("municipal"), "modified": it.get("date_modified")}
                    if old.get("orgnr"):  # employer does not change when an ad is edited
                        row.update({k: old[k] for k in ("orgnr", "employer", "homepage", "expires", "published") if k in old})
                    self.ads[uid] = row
                if items:
                    self.synced_until = items[-1].get("date_modified") or self.synced_until
                if len(items) < 1000 or not data.get("next_url"):
                    self.sync_info["status"] = "complete"
                    break
                url = FEED + data["next_url"]
                hdr = {"Authorization": hdr["Authorization"]}
        except BudgetExceeded:
            self.sync_info["status"] = "partial_request_budget"
        self.sync_info.update(pages=pages, synced_until=self.synced_until,
                              active_ads=sum(1 for a in self.ads.values() if a.get("status") == "ACTIVE"))
        self._by_name = None
        return self.sync_info

    def enrich(self, client, max_requests, workers=6, stop_seconds=None):
        """Fetch ad details (newest first) to learn each active ad's employer org. no."""
        from concurrent.futures import ThreadPoolExecutor
        todo = sorted((a for a in self.ads.values() if a.get("status") == "ACTIVE" and not a.get("orgnr")),
                      key=lambda a: a.get("modified") or "", reverse=True)[:max(0, max_requests)]
        done = [0]

        def one(ad):
            if stop_seconds is not None and client.time_left() < stop_seconds:
                return
            try:
                r, d = self.detail(client, ad["uuid"])
            except BudgetExceeded:
                return
            except Exception:
                return
            if not d:
                if r.status in (404, 410):
                    ad["status"] = "INACTIVE"
                return
            a = d.get("ad_content") or {}
            emp = a.get("employer") or {}
            with self._lock:
                ad.update(orgnr=emp.get("orgnr") or "-", employer=emp.get("name"), homepage=emp.get("homepage"),
                          expires=(a.get("expires") or "")[:10] or None, published=(a.get("published") or "")[:10] or None)
                done[0] += 1

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(one, todo))
        self._by_orgnr = None
        self.sync_info["details_fetched"] = done[0]
        self.sync_info["ads_without_orgnr"] = sum(1 for a in self.ads.values() if a.get("status") == "ACTIVE" and not a.get("orgnr"))
        return done[0]

    def by_orgnr(self, orgnrs):
        if getattr(self, "_by_orgnr", None) is None:
            idx = {}
            for ad in self.ads.values():
                if ad.get("status") == "ACTIVE" and ad.get("orgnr") and ad["orgnr"] != "-":
                    idx.setdefault(ad["orgnr"], []).append(ad)
            self._by_orgnr = idx
        out = []
        for o in orgnrs:
            out.extend(self._by_orgnr.get(o, []))
        out.sort(key=lambda a: a.get("modified") or "", reverse=True)
        return out

    def candidates(self, names, limit=12):
        if self._by_name is None:
            idx = {}
            for ad in self.ads.values():
                if ad.get("status") != "ACTIVE" or not ad.get("business") or ad.get("orgnr"):
                    continue  # ads with a known employer org. no. are matched exactly instead
                for key in {norm_name(ad["business"]), core_name(ad["business"])}:
                    if key:
                        idx.setdefault(key, []).append(ad)
            self._by_name = idx
        seen, out = set(), []
        for n in names:
            for key in (norm_name(n), core_name(n)):
                if len(key) < 3:
                    continue
                for ad in self._by_name.get(key, []):
                    if ad["uuid"] not in seen:
                        seen.add(ad["uuid"])
                        out.append(ad)
        out.sort(key=lambda a: a.get("modified") or "", reverse=True)
        return out[:limit]

    def detail(self, client, uuid):
        r = client.get(f"{FEED}/api/v1/feedentry/{uuid}", headers={"Authorization": f"Bearer {self.token(client)}"},
                       timeout=20, retries=1)
        return r, (r.json() if r.status == 200 else None)


def html_to_text(s, limit=400):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&[a-z#0-9]+;", " ", s)
    s = " ".join(s.split())
    return s[:limit]
