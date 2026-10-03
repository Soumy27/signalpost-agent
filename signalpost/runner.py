"""Batch runner that respects the daily limits.

Phase 1 builds the register-based core profile for every company (≈4–5
requests each). Phase 2 spends whatever request/time budget is left on
website verification, so a tight budget degrades coverage, never correctness.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import profile as prof
from .http import BudgetExceeded, Client
from .orgnr import is_valid, normalize
from .store import Store

WEB_REQUESTS_PER_SITE = 3  # robots.txt + https (+ http fallback)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def parse_orgnrs(lines):
    seen, out, bad = set(), [], []
    for line in lines:
        for tok in line.replace(",", " ").replace(";", " ").split():
            n = normalize(tok)
            if not n:
                continue
            if not is_valid(n):
                bad.append(tok)
            elif n not in seen:
                seen.add(n)
                out.append(n)
    return out, bad


def invalid_profile(raw):
    p = prof.ProfileBuilder(normalize(raw) or str(raw))
    p.notes.append("Input is not a valid Norwegian organisation number (9 digits with MOD11 check digit); "
                   "no lookup was made.")
    return prof.finish(p, "invalid_orgnr")


def run(orgnrs, out_dir="data", workers=8, max_requests=2000, minutes=45, web=True, quiet=False):
    t0 = time.monotonic()
    started_at = prof.brreg.now_iso()
    # keep a safety margin so we always finish writing before the hard limit
    client = Client(max_requests=max_requests, deadline_seconds=minutes * 60 - 60)
    store = Store(out_dir)
    profiles, failed = {}, {}

    def core(o):
        try:
            return o, prof.build_core(client, o), None
        except BudgetExceeded as e:
            return o, None, str(e)
        except Exception as e:
            return o, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (o, p, err) in enumerate(ex.map(core, orgnrs), 1):
            if p:
                profiles[o] = p
            else:
                failed[o] = err
            if not quiet and i % 25 == 0:
                log(f"[core] {i}/{len(orgnrs)} done, {client.count} requests used")

    if web:
        todo = [p for p in profiles.values() if p["lookup_result"] in ("enhet", "underenhet")
                and (p["facts"].get("website_registered") or p["facts"].get("email"))]

        def enrich(p):
            if client.remaining < WEB_REQUESTS_PER_SITE * 2 or client.time_left() < 30:
                return
            try:
                prof.enrich_web(client, p)
            except BudgetExceeded:
                p["notes"].append("Website check skipped: run budget exhausted.")

        with ThreadPoolExecutor(max_workers=workers * 2) as ex:
            list(ex.map(enrich, todo))
        if not quiet:
            log(f"[web] checked {len(todo)} sites, {client.count} requests used")

    changed = 0
    for p in profiles.values():
        if store.save(p):
            changed += 1

    report = {
        "started_at": started_at,
        "finished_at": prof.brreg.now_iso(),
        "duration_seconds": round(time.monotonic() - t0, 1),
        "companies_requested": len(orgnrs),
        "profiles_written": len(profiles),
        "profiles_with_changes": changed,
        "failed": failed,
        "outbound_requests": client.count,
        "request_limit": max_requests,
        "external_api_cost_usd": 0.0,
        "lookup_results": _count(p["lookup_result"] for p in profiles.values()),
    }
    store.write_meta("last_run.json", report)
    store.write_meta("last_run_requests.json", client.log)
    if not quiet:
        log(json.dumps({k: v for k, v in report.items() if k != "failed"}, indent=2))
        if failed:
            log(f"{len(failed)} companies failed: " + ", ".join(list(failed)[:10]))
    return profiles, report


def _count(it):
    out = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out
