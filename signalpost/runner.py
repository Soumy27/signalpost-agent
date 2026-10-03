"""Batch runner: one terminal envelope per input organisation number.

Phases (each bounded by the request/time budget):
  1. official registers for every company            (~4-6 requests each)
  2. NAV job-feed incremental sync                    (shared, a few pages)
  3. website discovery + verified-site crawl          (budget-shared)
  4. job-ad verification against employer org. no.    (budget-shared)
  5. synthesis, refresh diff, envelopes, report, HTML

A crash or budget stop in any phase still yields a terminal envelope for
every company (`failed` with the error), never a missing row.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, agent, brreg, report, summary
from .evidence import SECTIONS, STATES, Ledger
from .http import Client
from .jobs import JobIndex
from .orgnr import is_valid, normalize
from .store import Store, refresh


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_inputs(path):
    """JSONL ({"organisation_number": ...}), JSON list, CSV or plain text."""
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    raw = []
    stripped = text.strip()
    if stripped.startswith("["):
        body = json.loads(stripped)
        raw = [x.get("organisation_number") or x.get("orgnr") if isinstance(x, dict) else x for x in body]
    elif stripped.startswith("{") and "\n" not in stripped:
        body = json.loads(stripped)
        raw = body.get("organisation_numbers") or [body.get("organisation_number")]
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                d = json.loads(line)
                raw.append(d.get("organisation_number") or d.get("orgnr") or d.get("organisasjonsnummer"))
            else:
                for tok in line.replace(";", ",").split(","):
                    tok = tok.strip().strip('"')
                    if tok and any(ch.isdigit() for ch in tok):
                        raw.append(tok)
                        break
    out, seen = [], set()
    for r in raw:
        n = normalize(r or "")
        if n not in seen:
            seen.add(n)
            out.append((n, r))
    return out


def envelope(orgnr, L, ctx, run_meta, raw_input=None):
    agent.finalize_sections(L, ctx)
    kind = ctx.get("kind")
    if raw_input is not None:
        state = "failed"
    elif kind in ("enhet", "underenhet", "deleted"):
        state = "available"
    elif kind == "not_found":
        state = "not_available"
    else:
        state = "failed"
    ident = {}
    for c in L.claims:
        if c["section"] == "identity" and c["availability"] == "available" and c["field"] in (
                "legal_name", "legal_form", "registry_status", "registered_business_address", "municipality",
                "public_brand", "founding_date", "registration_date", "registered_employees"):
            ident.setdefault(c["field"], c["value"])
    inds = [c["value"] for c in L.claims if c["field"] == "industry"]
    if inds:
        ident["industry"] = sorted(inds, key=lambda v: v.get("rank", 9))[0]
    return {
        "organisation_number": orgnr,
        "state": state,
        "run": {**run_meta, "terminal_status": "completed" if state != "failed" else "failed"},
        "legal_identity": ident,
        "summary": None,
        "sections": {k: L.sections.get(k) for k, _ in SECTIONS},
        "claims": L.claims,
        "evidence": L.evidence,
        "changes": [],
        "errors": L.errors,
        "operations": {"requests": ctx.get("_requests", 0), "runtime_ms": ctx.get("_ms", 0), "third_party_cost_usd": 0},
    }


def run(input_path, out_dir="out", run_id=None, max_requests=None, minutes=None, workers=24, web=True, jobs=True,
        web_budget=16, snapshots=True, html=True, state_dir=None, bulk_path=None):
    t0 = time.monotonic()
    started = now_iso()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    inputs = read_inputs(input_path)
    n = len(inputs)
    run_id = run_id or "run-" + started.replace(":", "").replace("-", "")
    max_requests = max_requests or int(os.environ.get("SIGNALPOST_MAX_REQUESTS", 0) or 0) or max(2000, 30 * n)
    minutes = minutes or float(os.environ.get("SIGNALPOST_MAX_MINUTES", 0) or 0) or 45
    safety = min(120, max(30, minutes * 60 * 0.06))
    client = Client(max_requests=max_requests, deadline_seconds=minutes * 60 - safety,
                    snapshot_dir=(out / "snapshots") if snapshots else None)
    store = Store(state_dir or out / "state")
    run_meta = {"run_id": run_id, "started_at": started, "agent_version": __version__}
    log(f"[signalpost {__version__}] {n} companies, budget {max_requests} requests / {minutes:g} min -> {out}/")

    results = {}
    valid = [o for o, _ in inputs if is_valid(o)]
    invalid = {o: raw for o, raw in inputs if not is_valid(o)}

    bulk = None
    if bulk_path:
        try:
            bulk = brreg.load_bulk(bulk_path, valid)
            log(f"[registry] frozen snapshot {bulk_path}: {len(bulk[0])}/{len(valid)} rows, sha256 {bulk[1][:16]}…")
        except Exception as e:
            log(f"[registry] could not read snapshot {bulk_path}: {e}")

    # the job-feed sync runs alongside the official phase
    index = JobIndex(store.dir / "nav_jobs_index.jsonl.gz")
    pool = ThreadPoolExecutor(max_workers=1)
    sync_future = pool.submit(_sync_jobs, client, index, safety, max_requests) if jobs else None

    # phase 1: official registers
    def p1(o):
        L, ctx = agent.research_official(client, o, bulk)
        prev = store.load(o) or {}
        ctx["previous_website"] = next((c["value"]["url"] for c in prev.get("claims", [])
                                        if c["field"] == "official_website" and c.get("availability") == "available"), None)
        return o, (L, ctx)
    with ThreadPoolExecutor(max_workers=min(workers, 16)) as ex:
        for i, (o, res) in enumerate(ex.map(p1, valid), 1):
            results[o] = res
            if i % 50 == 0 or i == len(valid):
                log(f"[official] {i}/{len(valid)}  requests={client.count}")
    _checkpoint(out, inputs, results, invalid, run_meta)
    ok = [o for o in valid if results[o][1].get("kind") in ("enhet", "underenhet")]

    # phase 2: exact job-ad matches by employer org. no. (also yields homepage candidates)
    if sync_future:
        info = sync_future.result()
        log(f"[jobs] NAV feed index: {info}")
        try:
            index.save()
        except OSError as e:
            log(f"[jobs] could not save index: {e}")
        for o in ok:
            ctx = results[o][1]
            ads = index.by_orgnr({o} | set(ctx.get("subunit_orgnrs") or ()))
            ctx["nav_ads"] = ads
            ctx["employer_homepages"] = list(dict.fromkeys(a["homepage"] for a in ads if a.get("homepage")))[:2]
    pool.shutdown()

    # phase 3: website discovery + verified-site crawl
    if web:
        # fixed per-company page budget for the whole run (deterministic crawl => no false changes)
        share = max(4, min(web_budget, (client.remaining - 4 * len(ok)) // max(1, len(ok)) + 6))

        def p3(o):
            L, ctx = results[o]
            if client.time_left() < 20 or client.remaining < 8:
                L.section("website", "failed", "Run budget exhausted before the website could be checked.")
                return
            agent.research_web(client, L, ctx, budget=share)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, _ in enumerate(ex.map(p3, ok), 1):
                if i % 100 == 0 or i == len(ok):
                    log(f"[web] {i}/{len(ok)}  requests={client.count}")

    # phase 4: job-ad claims (live re-fetch of matched ads for fresh evidence)
    if jobs:
        def p4(o):
            L, ctx = results[o]
            if client.time_left() < 10 or client.remaining < 2:
                L.section("jobs", "failed", "Run budget exhausted before job ads could be checked.")
                return
            agent.research_jobs(client, L, ctx, index)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(p4, ok))
        log(f"[jobs] done  requests={client.count}")

    # phase 5: envelopes
    completed = now_iso()
    run_meta["completed_at"] = completed
    envelopes = []
    for o, raw in inputs:
        try:
            if o in invalid:
                L = Ledger(o)
                L.error("input", f"Invalid Norwegian organisation number: {raw!r}", retryable=False)
                env = envelope(o, L, {"kind": "invalid"}, run_meta, raw_input=raw)
                env["summary"] = {"text": "Input is not a valid Norwegian organisation number (9 digits, MOD11).",
                                  "sentences": [], "unknowns": [], "changes": []}
            else:
                L, ctx = results[o]
                env = envelope(o, L, ctx, run_meta)
                if env["claims"]:
                    refresh(store, env, run_id, completed)
                env["summary"] = summary.build(env["claims"], env["sections"], env["changes"], {"orgnr": o, **ctx})
        except Exception as e:  # never drop a company
            L = Ledger(o)
            L.error("assembly", f"{type(e).__name__}: {e}", retryable=True)
            env = envelope(o, L, {"kind": "error"}, run_meta)
            env["summary"] = {"text": "Profile assembly failed; see errors.", "sentences": [], "unknowns": [], "changes": []}
        envelopes.append(env)
    _write_jsonl(out / "envelopes.jsonl", envelopes)

    rep = _report(envelopes, client, run_meta, n, t0, index if jobs else None, max_requests, minutes)
    (out / "run-report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "requests.log.jsonl").open("w", encoding="utf-8") as f:
        for u, s in client.log:
            f.write(json.dumps({"url": u, "status": s}) + "\n")
    if html:
        report.write_html(out / "index.html", envelopes, rep)
    log(json.dumps({k: rep[k] for k in ("emitted_envelopes", "states", "operations", "validation")}, indent=1))
    return envelopes, rep


def _sync_jobs(client, index, safety, max_requests):
    """Incremental feed sync, then learn employer org. numbers for new ads.
    Bounded to a share of the run budget so research is never starved."""
    try:
        index.sync(client, stop_seconds=safety + 300)
        index.enrich(client, max_requests=int(max_requests * 0.3), workers=6,
                     stop_seconds=max(safety + 300, client.time_left() * 0.6))
    except Exception as e:
        index.sync_info["status"] = f"failed: {type(e).__name__}"
    return index.sync_info


def _checkpoint(out, inputs, results, invalid, run_meta):
    """Provisional envelopes after the official phase, so a hard kill still
    leaves one row per company."""
    rows = []
    for o, raw in inputs:
        if o in invalid or o not in results:
            rows.append({"organisation_number": o, "state": "failed", "run": {**run_meta, "terminal_status": "checkpoint"}})
            continue
        L, ctx = results[o]
        kind = ctx.get("kind")
        rows.append({"organisation_number": o,
                     "state": "available" if kind in ("enhet", "underenhet", "deleted") else "not_available" if kind == "not_found" else "failed",
                     "run": {**run_meta, "terminal_status": "checkpoint"}, "claims": L.claims, "evidence": L.evidence,
                     "errors": L.errors})
    _write_jsonl(out / "envelopes.checkpoint.jsonl", rows)


def _write_jsonl(path, rows):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def _report(envelopes, client, run_meta, n, t0, index, max_requests, minutes):
    states, sec = {}, {}
    for e in envelopes:
        states[e["state"]] = states.get(e["state"], 0) + 1
        for k, s in (e.get("sections") or {}).items():
            st = (s or {}).get("state", "missing")
            sec.setdefault(k, {}).setdefault(st, 0)
            sec[k][st] += 1
    per = sorted(e["operations"]["runtime_ms"] for e in envelopes)
    lat = sorted(client.latencies)
    orgs = [e["organisation_number"] for e in envelopes]
    published = [c for e in envelopes for c in e["claims"] if c["availability"] == "available"]
    unsupported = 0
    for e in envelopes:
        ids = {ev["id"] for ev in e["evidence"]}
        unsupported += sum(1 for c in e["claims"] if c["availability"] == "available"
                           and (not c["evidence_ids"] or any(i not in ids for i in c["evidence_ids"])))
    checks = {
        "exact_expected_count": len(envelopes) == n,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "all_states_valid": all(e["state"] in STATES for e in envelopes),
        "all_section_states_valid": all((s or {}).get("state") in STATES for e in envelopes for s in e["sections"].values()),
        "every_published_claim_has_evidence": unsupported == 0,
        "evidence_has_hash_and_time": all(ev.get("content_sha256") and ev.get("retrieved_at") for e in envelopes for ev in e["evidence"]),
    }
    return {
        "run_id": run_meta["run_id"],
        "agent_version": __version__,
        "started_at": run_meta["started_at"],
        "completed_at": run_meta.get("completed_at"),
        "expected_count": n,
        "emitted_envelopes": len(envelopes),
        "states": states,
        "section_states": sec,
        "published_claims": len(published),
        "claims_by_section": _count(c["section"] for c in published),
        "changes_detected": sum(len(e["changes"]) for e in envelopes),
        "operations": {
            "requests": client.count, "request_budget": max_requests, "time_budget_minutes": minutes,
            "bytes": client.bytes, "runtime_seconds": round(time.monotonic() - t0, 1),
            "p50_request_ms": lat[len(lat) // 2] if lat else None,
            "p95_request_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
            "p50_company_ms": per[len(per) // 2] if per else None,
            "p95_company_ms": per[min(len(per) - 1, int(len(per) * 0.95))] if per else None,
            "third_party_cost_usd": 0.0, "cost_per_company_usd": 0.0,
        },
        "job_feed": index.sync_info if index else None,
        "validation": {"passed": all(checks.values()), "checks": checks, "unsupported_claims": unsupported},
    }


def _count(it):
    d = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d
