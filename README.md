# Signalpost company agent

Give it Norwegian organisation numbers. For each one it researches permitted public sources and returns **exactly one terminal envelope**. The envelope holds the legal identity, claims, claim-level evidence, availability states, source snapshots, refresh metadata, errors and a plain-language summary.

- **Exact identity first.** Every published fact is tied to the organisation number. Register data is matched by org. no. in the response. Website content is published only after the site resolves to the exact legal entity (see [docs/IDENTITY_RESOLUTION.md](docs/IDENTITY_RESOLUTION.md)). Uncertain matches are reported as `ambiguous` and nothing from them is published.
- **Evidence on every claim.** Each claim records the source URL, final URL and redirect chain, retrieval time, SHA-256 of the captured body, the exact JSON pointer or text span, the extraction method and, where relevant, the reporting period. Raw bodies are kept as immutable snapshots under `out/snapshots/`.
- **No fabricated numbers.** Financial values are copied as filed from Regnskapsregisteret, with currency, scope (company or group) and period. A missing value is an explicit `not_available`, never a zero.
- **Refresh as a diff.** Re-runs emit typed changes (`new_role`, `new_filing`, `new_job`, `closed_job`, `changed_registered_business_address`, …). Each change carries the previous value, the previous evidence and the current evidence. Re-running the same snapshot is idempotent.
- **No paid services, no keys, no LLM.** It uses Python 3.9+ and the standard library only, and the third-party cost is $0.

## Setup and the evaluator command

There are no dependencies and no install step (`pip install .` is optional). The one command:

```bash
python3 -m signalpost run batch.jsonl --out out
```

`batch.jsonl` holds one `{"organisation_number": "123456789"}` per line. JSON arrays, CSV and plain-text lists also work. The command exits 0 when the output passes the contract validation.

| Output | Contents |
|---|---|
| `out/envelopes.jsonl` | one terminal envelope per input, in input order |
| `out/run-report.json` | counts, availability states per section, requests, bytes, p50/p95 latency, runtime, cost, validation checks |
| `out/index.html` + `report-data.js` | responsive viewer to search, filter, compare and verify every fact on desktop or mobile |
| `out/state/` | refresh state: previous claims and evidence, change log, job-feed index |
| `out/snapshots/` | gzip-compressed raw responses, named by their SHA-256 |
| `out/requests.log.jsonl` | every outbound request and its status |
| `out/envelopes.checkpoint.jsonl` | provisional envelopes written after the register phase (crash safety) |

If Builderr supplies its frozen registry snapshot, pass it with `--bulk brreg-enheter.csv[.gz]`. It is optional: the live register is always tried first, and the snapshot is only used to anchor identity for a company whose live lookup fails, with the snapshot's SHA-256 recorded as evidence.

Budgets come from `--max-requests` / `--minutes`, or from the environment variables `SIGNALPOST_MAX_REQUESTS` and `SIGNALPOST_MAX_MINUTES`. The defaults are 30 requests per company (minimum 2,000) and 45 minutes. The run stops early enough to always write every envelope.

Other commands:

```bash
python3 -m signalpost lookup 923609016                                      # one company, envelope to stdout
python3 -m signalpost sample 100 --universe signalpost-company-universe-2025.jsonl.gz > smoke.jsonl
python3 -m signalpost validate out/envelopes.jsonl --expected 100           # contract check
python3 -m signalpost changed --out out                                     # stored companies changed upstream
python3 -m unittest discover tests                                          # offline tests
```

To refresh, run the same command again with the same `--out` (or `--state`). The previous state is read from there.

## Smoke test

[`smoke/`](smoke/) holds the 100-company smoke test. The companies were sampled at random from Builderr's public universe (`smoke/smoke-companies.jsonl`, seed 2026).

| | First run | Refresh run |
|---|---|---|
| Envelopes | 100 / 100, validation passed | 100 / 100, validation passed |
| Published claims | 7,846 | 7,846 |
| Changes detected | – | 0 (no false changes) |
| Requests | 686 | 691 |
| Runtime | 128 s | 138 s |
| Third-party cost | $0 | $0 |

Files: `envelopes.jsonl`, `run-report.json`, `refresh-run-report.json`, `requests.log.jsonl`, and the viewer `index.html` + `report-data.js`. To browse the viewer, open `index.html` from a local web server, for example `python3 -m http.server -d smoke`.

## Envelope (abridged)

```jsonc
{
  "organisation_number": "997874234",
  "state": "available",                       // available | not_available | blocked | not_applicable | ambiguous | failed
  "run": {"run_id": "…", "started_at": "…", "completed_at": "…", "terminal_status": "completed"},
  "legal_identity": {"legal_name": "ANNI&CO AS", "legal_form": {…}, "registry_status": "active", …},
  "summary": {"text": "…", "sentences": [{"topic": "financials", "text": "…", "supporting_claims": ["cl-41", …]}], "unknowns": […]},
  "sections": {"website": {"state": "available"}, "jobs": {"state": "not_available", "reason": "…", "sources_checked": […]}, …},
  "claims": [{"id": "cl-7", "key": "chief_executive:kari nordmann", "section": "leadership", "field": "chief_executive",
              "value": {"name": "Kari Nordmann", "role": "Daglig leder"}, "availability": "available", "confidence": 1.0,
              "evidence_ids": ["ev-7"], "effective_date": "2020-01-01", "first_observed_at": "…", "last_observed_at": "…"}],
  "evidence": [{"id": "ev-7", "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/997874234/roller",
                "source_class": "official_registry", "retrieved_at": "…", "content_sha256": "…",
                "claim_span": "/rollegrupper/0/roller/0/person/navn = {…}", "extraction_method": "json_pointer"}],
  "changes": [{"change_type": "new_role", "previous_value": null, "current_value": {…}, "previous_evidence": […], …}],
  "errors": [],
  "operations": {"requests": 11, "runtime_ms": 8120, "third_party_cost_usd": 0}
}
```

The full schema is in [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md).

## What a profile covers

| Section | Sources | Examples |
|---|---|---|
| Legal identity and brand | Enhetsregisteret, verified site | name, form, status, dates, addresses, NACE, employees, share capital, purpose, former names, public brand, self-description |
| Annual accounts | Regnskapsregisteret | revenue, operating profit, profit before tax, net profit, assets, equity, liabilities, cash and payroll, for company and group, latest and previous year, with periods |
| Leadership | Enhetsregisteret roles | CEO, chair, board, deputies, auditor, accountant, owner, signatories (birth dates are discarded) |
| Locations | subunits, site structured data | registered workplaces with address, employees and industry |
| Group | konsernstruktur | parent, ultimate parent, subsidiaries, group size |
| Website | registry URL, e-mail domain, job-ad homepage, name-derived domains | verified official site with identity proof |
| Social profiles | links and `sameAs` on the verified site | LinkedIn, Facebook, Instagram, YouTube, X, TikTok |
| Contact | register, verified site | phone, e-mail |
| Hiring | NAV job feed (exact employer org. no.), verified site | active ads with title, dates, location, link; careers page and portal |
| Dated activity | verified site RSS/Atom, WordPress posts, dated news lists, JSON-LD articles | dated items from the last two years |

## Models, APIs, licences and cost

- **Models:** none. Extraction, identity resolution and summaries are deterministic.
- **APIs:**
  - Brønnøysundregistrene Enhetsregisteret and Regnskapsregisteret: open data under NLOD 2.0, no key.
  - NAV arbeidsplassen public job-vacancy feed: read with the public token NAV publishes, and declared as a cache (see [docs/CRAWLERS.md](docs/CRAWLERS.md)).
  - Statistics Norway KLASS: English industry names (CC BY 4.0), bundled as a static table.
  - Company-owned websites: `robots.txt` is honoured and access is limited to a few targeted pages.
- **Secrets:** none required.
- **Expected cost per official run:** $0 third-party cost. A typical run makes about 6–12 requests per company.

## Documentation

- [docs/AGENT.md](docs/AGENT.md): research and abstention policy
- [docs/IDENTITY_RESOLUTION.md](docs/IDENTITY_RESOLUTION.md): candidate generation and publication gates
- [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md): envelopes, claims, evidence and states
- [docs/REFRESH.md](docs/REFRESH.md): snapshots, diffs, idempotency and stale values
- [docs/CRAWLERS.md](docs/CRAWLERS.md): sources, rights, budgets and fallbacks
- [docs/EVAL.md](docs/EVAL.md): how it was measured
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md): known gaps
