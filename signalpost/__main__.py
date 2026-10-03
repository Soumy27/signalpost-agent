"""Signalpost company agent CLI.

  python3 -m signalpost lookup 923609016            # one company, JSON to stdout
  python3 -m signalpost run companies.txt           # batch -> data/profiles/*.json
  python3 -m signalpost refresh                     # re-check stored profiles that changed upstream
  python3 -m signalpost sample 1000 > companies.txt # random sample of registered companies
  python3 -m signalpost export --format jsonl       # all profiles in one file
"""
import argparse
import csv
import json
import random
import sys
from datetime import datetime, timedelta, timezone

from . import brreg, runner
from .http import Client
from .store import Store


def cmd_lookup(a):
    lines = a.orgnr
    orgnrs, bad = runner.parse_orgnrs(lines)
    profiles, _ = runner.run(orgnrs, out_dir=a.out, web=not a.no_web, quiet=True, workers=4)
    out = [profiles[o] for o in orgnrs if o in profiles] + [runner.invalid_profile(b) for b in bad]
    for p in out:  # include history from the store
        stored = Store(a.out).load(p["orgnr"])
        if stored:
            p.update(history=stored.get("history"))
    print(json.dumps(out[0] if len(out) == 1 else out, ensure_ascii=False, indent=2))


def cmd_run(a):
    if a.input == "-":
        lines = sys.stdin.read().splitlines()
    else:
        with open(a.input, encoding="utf-8") as f:
            lines = f.read().splitlines()
    orgnrs, bad = runner.parse_orgnrs(lines)
    if bad:
        runner.log(f"Ignoring {len(bad)} invalid organisation numbers: {bad[:10]}")
    runner.log(f"Building profiles for {len(orgnrs)} companies -> {a.out}/profiles/")
    runner.run(orgnrs, out_dir=a.out, workers=a.workers, max_requests=a.max_requests,
               minutes=a.minutes, web=not a.no_web)
    if a.export:
        export(Store(a.out), a.export, orgnrs)


def cmd_refresh(a):
    store = Store(a.out)
    meta = store.read_meta("refresh_state.json")
    since = a.since or meta.get("last_refresh") or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    stored = set(store.all_orgnrs())
    client = Client(max_requests=a.max_requests)
    changed = set(brreg.changed_entities_since(client, since)) | brreg.changed_roles_since(client, since)
    todo = sorted(stored & changed)
    # accounts have no feed: also re-check the stalest profiles
    stale_days = a.stale_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
    for o in stored - set(todo):
        p = store.load(o) or {}
        if (p.get("history") or {}).get("last_checked", "") < cutoff:
            todo.append(o)
    runner.log(f"{len(changed)} register changes since {since}; re-checking {len(todo)} stored profiles")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if todo:
        runner.run(todo, out_dir=a.out, workers=a.workers,
                   max_requests=max(0, a.max_requests - client.count), web=not a.no_web)
    store.write_meta("refresh_state.json", {"last_refresh": now})


def cmd_sample(a):
    """Random registered companies, spread across registration dates."""
    rng = random.Random(a.seed)
    client = Client(max_requests=500)
    picked = set()
    start = datetime(1995, 1, 1)
    days = (datetime.now() - start).days
    while len(picked) < a.n and client.remaining > 0:
        d0 = start + timedelta(days=rng.randrange(days))
        d1 = d0 + timedelta(days=30)
        url = (f"{brreg.ER}/enheter?size=100&page={rng.randrange(5)}"
               f"&fraRegistreringsdatoEnhetsregisteret={d0:%Y-%m-%d}&tilRegistreringsdatoEnhetsregisteret={d1:%Y-%m-%d}")
        r = client.get(url)
        if r.status != 200:
            continue
        items = (r.json().get("_embedded") or {}).get("enheter", [])
        rng.shuffle(items)
        for e in items[:20]:
            picked.add(e["organisasjonsnummer"])
    for o in sorted(picked)[:a.n] if a.sorted else list(picked)[:a.n]:
        print(o)


def export(store, fmt, orgnrs=None, path=None):
    orgnrs = orgnrs or store.all_orgnrs()
    path = path or str(store.root / f"profiles.{fmt}")
    rows = [p for p in (store.load(o) for o in orgnrs) if p]
    if fmt == "jsonl":
        with open(path, "w", encoding="utf-8") as f:
            for p in rows:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
    else:
        cols = ["orgnr", "name", "status", "legal_form", "municipality", "primary_industry", "employees",
                "chief_executive", "board_chair", "fiscal_year", "currency", "revenue", "net_profit", "equity",
                "website", "last_changed", "summary"]
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, cols)
            w.writeheader()
            for p in rows:
                v = lambda k: (p["facts"].get(k) or {}).get("value")
                fin = v("financials") or {}
                w.writerow({
                    "orgnr": p["orgnr"], "name": v("name"), "status": v("status"),
                    "legal_form": (v("legal_form") or {}).get("code"),
                    "municipality": (v("municipality") or {}).get("name"),
                    "primary_industry": (v("primary_industry") or {}).get("code"),
                    "employees": v("employees"),
                    "chief_executive": (v("chief_executive") or {}).get("name") if isinstance(v("chief_executive"), dict) else None,
                    "board_chair": (v("board_chair") or {}).get("name") if isinstance(v("board_chair"), dict) else None,
                    "fiscal_year": fin.get("fiscal_year"), "currency": fin.get("currency"),
                    "revenue": (fin.get("figures") or {}).get("revenue"),
                    "net_profit": (fin.get("figures") or {}).get("net_profit"),
                    "equity": (fin.get("figures") or {}).get("equity"),
                    "website": v("website"), "last_changed": (p.get("history") or {}).get("last_changed"),
                    "summary": p.get("summary"),
                })
    runner.log(f"Exported {len(rows)} profiles -> {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="signalpost", description="Norwegian company profile agent")
    ap.add_argument("--out", default="data", help="data directory (default: data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("lookup", help="profile one or more org numbers, print JSON")
    s.add_argument("orgnr", nargs="+")
    s.add_argument("--no-web", action="store_true")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("run", help="batch-build profiles from a file of org numbers ('-' = stdin)")
    s.add_argument("input")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--max-requests", type=int, default=2000)
    s.add_argument("--minutes", type=float, default=45)
    s.add_argument("--no-web", action="store_true")
    s.add_argument("--export", choices=["jsonl", "csv"])
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("refresh", help="re-check stored profiles changed in the register since last refresh")
    s.add_argument("--since", help="ISO timestamp, e.g. 2026-10-01T00:00:00.000Z")
    s.add_argument("--stale-days", type=int, default=30)
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--max-requests", type=int, default=2000)
    s.add_argument("--no-web", action="store_true")
    s.set_defaults(func=cmd_refresh)

    s = sub.add_parser("sample", help="print N random registered org numbers")
    s.add_argument("n", type=int)
    s.add_argument("--seed", type=int)
    s.add_argument("--sorted", action="store_true")
    s.set_defaults(func=cmd_sample)

    s = sub.add_parser("export", help="export stored profiles")
    s.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
    s.set_defaults(func=lambda a: export(Store(a.out), a.format))

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
