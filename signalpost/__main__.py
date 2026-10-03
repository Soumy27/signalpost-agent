"""Signalpost company agent.

  python3 -m signalpost run batch.jsonl --out out      # evaluator command (one envelope per input)
  python3 -m signalpost lookup 923609016               # one company, envelope JSON to stdout
  python3 -m signalpost sample 100 --universe U.jsonl.gz > smoke.jsonl
  python3 -m signalpost changed --out out              # org. numbers changed upstream since last run
  python3 -m signalpost validate out/envelopes.jsonl --expected 100
"""
import argparse
import gzip
import json
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import brreg, runner
from .evidence import STATES
from .http import Client
from .store import Store


def cmd_run(a):
    _, rep = runner.run(a.input, out_dir=a.out, run_id=a.run_id, max_requests=a.max_requests, minutes=a.minutes,
                        workers=a.workers, web=not a.no_web, jobs=not a.no_jobs, snapshots=not a.no_snapshots,
                        html=not a.no_html, state_dir=a.state, bulk_path=a.bulk)
    sys.exit(0 if rep["validation"]["passed"] else 1)


def cmd_lookup(a):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(a.orgnr))
    envs, _ = runner.run(f.name, out_dir=a.out, workers=4, web=not a.no_web, jobs=not a.no_jobs, html=False)
    print(json.dumps(envs[0] if len(envs) == 1 else envs, ensure_ascii=False, indent=2))


def cmd_sample(a):
    rng = random.Random(a.seed)
    if a.universe:
        opener = gzip.open if a.universe.endswith(".gz") else open
        with opener(a.universe, "rt", encoding="utf-8") as f:
            rows = [json.loads(l)["organisation_number"] for l in f if l.strip()]
        picked = rng.sample(rows, a.n)
    else:
        client, picked = Client(max_requests=500), set()
        start, days = datetime(1995, 1, 1), (datetime.now() - datetime(1995, 1, 1)).days
        while len(picked) < a.n and client.remaining > 0:
            d0 = start + timedelta(days=rng.randrange(days))
            r, data = brreg.get_json(client, f"{brreg.ER}/enheter?size=100&page={rng.randrange(5)}&organisasjonsform=AS"
                                             f"&fraRegistreringsdatoEnhetsregisteret={d0:%Y-%m-%d}"
                                             f"&tilRegistreringsdatoEnhetsregisteret={d0 + timedelta(days=30):%Y-%m-%d}")
            items = ((data or {}).get("_embedded") or {}).get("enheter", [])
            rng.shuffle(items)
            picked.update(e["organisasjonsnummer"] for e in items[:20])
        picked = list(picked)[:a.n]
    for o in picked:
        print(json.dumps({"organisation_number": o}))


def cmd_changed(a):
    """List stored companies whose register entry or roles changed since a time."""
    store = Store(Path(a.out) / "state")
    since = a.since or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    client = Client(max_requests=200, deadline_seconds=900)
    changed = set(brreg.changed_entities_since(client, since)) | brreg.changed_roles_since(client, since)
    for o in sorted(set(store.all()) & changed):
        print(json.dumps({"organisation_number": o}))


def cmd_validate(a):
    rows = [json.loads(l) for l in Path(a.envelopes).read_text(encoding="utf-8").splitlines() if l.strip()]
    orgs = [r["organisation_number"] for r in rows]
    problems = []
    if a.expected is not None and len(rows) != a.expected:
        problems.append(f"expected {a.expected} envelopes, found {len(rows)}")
    if len(orgs) != len(set(orgs)):
        problems.append("duplicate organisation numbers")
    for r in rows:
        if r.get("state") not in STATES:
            problems.append(f"{r['organisation_number']}: invalid state {r.get('state')}")
        ids = {e["id"] for e in r.get("evidence", [])}
        for c in r.get("claims", []):
            if c.get("availability") == "available" and (not c.get("evidence_ids") or not set(c["evidence_ids"]) <= ids):
                problems.append(f"{r['organisation_number']}: claim {c['id']} lacks evidence")
        for e in r.get("evidence", []):
            if not (e.get("source_url") and e.get("retrieved_at") and e.get("content_sha256")):
                problems.append(f"{r['organisation_number']}: evidence {e['id']} incomplete")
    print(json.dumps({"envelopes": len(rows), "passed": not problems, "problems": problems[:50]}, indent=2))
    sys.exit(0 if not problems else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="signalpost", description="Norwegian company research agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="research a batch; writes one terminal envelope per input")
    s.add_argument("input", help="JSONL/JSON/CSV/text file of organisation numbers ('-' = stdin)")
    s.add_argument("--out", default="out")
    s.add_argument("--state", help="refresh state directory (default: <out>/state)")
    s.add_argument("--run-id")
    s.add_argument("--max-requests", type=int, help="default: $SIGNALPOST_MAX_REQUESTS or max(2000, 30 per company)")
    s.add_argument("--minutes", type=float, help="default: $SIGNALPOST_MAX_MINUTES or 45")
    s.add_argument("--workers", type=int, default=24)
    s.add_argument("--no-web", action="store_true")
    s.add_argument("--no-jobs", action="store_true")
    s.add_argument("--no-snapshots", action="store_true")
    s.add_argument("--no-html", action="store_true")
    s.add_argument("--bulk", help="optional frozen Brreg entity snapshot (CSV/CSV.gz) used if the live register fails")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("lookup", help="research one or more companies and print the envelope(s)")
    s.add_argument("orgnr", nargs="+")
    s.add_argument("--out", default="out-lookup")
    s.add_argument("--no-web", action="store_true")
    s.add_argument("--no-jobs", action="store_true")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("sample", help="print N random organisation numbers as JSONL")
    s.add_argument("n", type=int)
    s.add_argument("--universe", help="Builderr universe .jsonl.gz (recommended)")
    s.add_argument("--seed", type=int)
    s.set_defaults(func=cmd_sample)

    s = sub.add_parser("changed", help="stored companies changed in the register since a time (refresh scheduling)")
    s.add_argument("--out", default="out")
    s.add_argument("--since")
    s.set_defaults(func=cmd_changed)

    s = sub.add_parser("validate", help="check an envelopes.jsonl against the output contract")
    s.add_argument("envelopes")
    s.add_argument("--expected", type=int)
    s.set_defaults(func=cmd_validate)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
