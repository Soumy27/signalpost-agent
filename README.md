# Signalpost Company Agent

Give it a Norwegian organisation number. It looks the company up in permitted public sources and returns a profile where **every fact carries its source URL, retrieval time and the source's own date**, together with a plain-English summary. Profiles are stored and re-checked, and every change is recorded.

- **No API keys, no LLM, no paid services.** It uses Python 3.9+ and the standard library only.
- **Identity-safe.** A fact is published only if the organisation number in the source response matches the one requested. Website content is published only when the organisation number or the company's distinctive name appears on the site.
- **No fabricated numbers.** Financial figures are copied as filed from Regnskapsregisteret, with currency, period and statement type (company vs group). The only derived number is a revenue-change percentage, and it is labelled as derived.

## The one command

```bash
python3 -m signalpost run companies.txt --export jsonl
```

`companies.txt` holds organisation numbers, one per line (spaces, `NO … MVA`, commas and CSV columns are tolerated). The output goes to:

| Path | Contents |
|---|---|
| `data/profiles/<orgnr>.json` | one profile per company, including its change history |
| `data/profiles.jsonl` | all profiles from this run (`--export csv` gives a flat CSV instead) |
| `data/last_run.json` | run report: duration, request count, outcomes, cost |
| `data/last_run_requests.json` | audit log of every outbound request and its HTTP status |

Other commands:

```bash
python3 -m signalpost lookup 923609016          # one company, JSON to stdout
python3 -m signalpost refresh                   # re-check stored profiles that changed upstream
python3 -m signalpost sample 1000 > list.txt    # random registered companies
python3 -m signalpost export --format csv       # dump the whole store
python3 -m unittest discover tests              # offline tests
```

## Sources (all open, public, no login)

| Source | Endpoint | What it provides |
|---|---|---|
| Enhetsregisteret (entity) | `data.brreg.no/enhetsregisteret/api/enheter/{orgnr}` | name, legal form, status (active/bankrupt/liquidation/deleted), founding and registration dates, addresses, NACE codes, sector, employees, website, e-mail, phone, share capital, purpose, VAT and other registrations, former names, group flag |
| Enhetsregisteret (sub-unit) | `…/underenheter/{orgnr}` | used when the number is a branch or location; the parent entity is resolved too |
| Enhetsregisteret (roles) | `…/enheter/{orgnr}/roller` | CEO, board chair and members, deputies, auditor, accountant, owner (ENK), partners, signature and procuration |
| Enhetsregisteret (sub-units) | `…/underenheter?overordnetEnhet={orgnr}` | number and list of branches |
| Regnskapsregisteret | `data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` | latest and previous filed annual accounts (company and group): revenue, operating profit, profit before tax, net profit, assets, equity, liabilities, cash, payroll; audit status |
| Company website | homepage of the registered site, or the domain of the registered non-free-mail e-mail | identity check, title, description, social profiles; `robots.txt` is always honoured |
| Update feeds | `…/oppdateringer/enheter`, `…/oppdateringer/roller` | used by `refresh` to find which stored companies changed |

Brreg data is published under the Norwegian Licence for Open Government Data (NLOD). Birth dates of role holders are deliberately **not** stored.

## Profile format

```jsonc
{
  "orgnr": "997874234",
  "lookup_result": "enhet",          // enhet | underenhet | deleted | not_found | identity_mismatch
  "summary": "ANNI&CO AS (org. no. 997874234) is a private limited company based in Ullensaker, ...",
  "facts": {
    "chief_executive": {
      "value": {"name": "…", "type": "person", "role_code": "DAGL", "role": "Daglig leder"},
      "source": "brreg_roles",        // key into "sources"
      "as_of": "2020-11-02",          // date given by the source itself
      "observed_since": "2026-10-03T12:00:00+00:00"
    },
    "financials": {
      "value": {"fiscal_year": 2025, "currency": "NOK", "statement_type": "SELSKAP",
                "figures": {"revenue": 11200000.0, "net_profit": 368000.0, "...": "..."}},
      "source": "brreg_accounts", "as_of": "2025-12-31"
    }
  },
  "sources": {"brreg_roles": {"url": "https://data.brreg.no/…/roller", "http_status": 200, "retrieved_at": "…"}},
  "identity_checks": [{"source": "brreg_entity", "method": "orgnr_exact_match", "match": true}],
  "notes": ["Plain-language explanations of gaps, e.g. why no accounts exist"],
  "history": {"first_seen": "…", "last_checked": "…", "last_changed": "…",
              "changes": [{"field": "employees", "old": 7, "new": 9, "detected_at": "…"}]}
}
```

## Keeping profiles current

- Each re-run compares new facts with the stored ones. Changed values are written to `history.changes` with old value, new value and detection time, and `observed_since` shows how long each value has been unchanged.
- `refresh` reads Brreg's entity and role update feeds since the last refresh and re-checks only the stored companies that changed. It also re-checks profiles older than `--stale-days` (default 30), because accounts have no feed.
- If a website is temporarily unreachable, the last verified value is kept and marked `"stale": true` rather than reported as removed.

## Limits and cost

The daily test is 100 companies, a 45-minute limit, 2,000 outbound requests and $10.

| | Typical for 100 companies | Enforced cap |
|---|---|---|
| Outbound requests (every retry and `robots.txt` counted) | ~450 | `--max-requests 2000` |
| Wall time | ~2–3 min | `--minutes 45` (stops 60 s early) |
| External API cost | **$0.00** (no paid APIs, no LLM) | – |

Requests are spent in two phases. The register-based core profile is built first for every company (about 4–5 requests each). Website verification then uses whatever budget is left. A tight budget therefore reduces website coverage but never accuracy.

## Model / API details (for submission)

- **Model:** none. The extraction, identity matching and explanations are deterministic code, so results are reproducible and no values can be hallucinated.
- **APIs:** Brønnøysundregistrene open data APIs (free, no key) and plain HTTPS GETs of company homepages.
- **Expected run cost:** $0 per daily run.

## Layout

```
signalpost/
  __main__.py   CLI (run, lookup, refresh, sample, export)
  runner.py     batch runner with request/time budget
  http.py       budgeted HTTP client (counts every request)
  brreg.py      Enhetsregisteret, Regnskapsregisteret and update feeds
  website.py    robots-aware homepage check and identity verification
  profile.py    facts, evidence, identity checks, plain-English summary
  store.py      per-company JSON store with change history
  orgnr.py      MOD11 validation
tests/          offline unit tests
```
