# Signalpost company agent

**Live viewer (smoke-test results): https://soumy27.github.io/signalpost-agent/**. Find a company, check where every fact came from, and compare two side by side, on desktop or phone.

Give it Norwegian organisation numbers. For each one it researches permitted public sources and returns **exactly one terminal envelope**. The envelope holds the legal identity, claims, claim-level evidence, availability states, source snapshots, refresh metadata, errors and a plain-language summary.

- **Exact identity first.** Every published fact is tied to the organisation number. Register data is matched by org. no. in the response. Website content is published only after the site resolves to the exact legal entity (see [docs/IDENTITY_RESOLUTION.md](docs/IDENTITY_RESOLUTION.md)). Uncertain matches are reported as `ambiguous` and nothing from them is published.
- **Evidence on every claim.** Each claim records the source URL, final URL and redirect chain, retrieval time, SHA-256 of the captured body, the exact JSON pointer or text span, the extraction method and, where relevant, the reporting period. Raw bodies are kept as immutable snapshots under `out/snapshots/`.
- **No fabricated numbers.** Financial values are copied as filed from Regnskapsregisteret, with currency, scope (company or group) and period. A missing value is an explicit `not_available`, never a zero.
- **Refresh as a diff.** Re-runs emit typed changes (`new_role`, `new_filing`, `new_job`, `closed_job`, `changed_registered_business_address`, …). Each change carries the previous value, the previous evidence and the current evidence. Re-running the same snapshot is idempotent.
- **No dependencies, no LLM, $0 by default.** It uses Python 3.9+ and the standard library only. With no key set, every source is free and keyless. One search API key, if supplied, adds optional candidate discovery (see below).

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

### Optional: search-API candidate discovery

If one of these environment variables is set, companies whose free candidates (register website, e-mail domain, job-ad homepage, name-derived domains) did not verify also get up to two search queries: the name plus the registered town, then the organisation number.

| Variable | Provider |
|---|---|
| `BRAVE_SEARCH_API_KEY` | Brave Search API (checked first) |
| `SERPER_API_KEY` | Serper |
| `TAVILY_API_KEY` | Tavily |

Search output is transient. It only proposes candidate domains, is never stored or cited, and directories, registries and social networks are dropped before any fetch. Each candidate must then pass the strictest identity gate, the same one used for guessed domains: the org. no. on the site, or on a `.no` site the full name plus independent corroboration. `SIGNALPOST_SEARCH_QPS` (default 1, Brave's free-tier rate) and `SIGNALPOST_SEARCH_MAX_QUERIES` (default 2 per company) bound the volume. Searching stops when a quarter of the run's time is left, and a rejected key turns search off after one call. Without a key the run is unchanged and costs $0.

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

[`smoke/`](smoke/) holds the 100-company smoke test. The companies were sampled at random from Builderr's public universe (`smoke/smoke-companies.jsonl`, seed 2026). See `smoke/run-report.json` (first run) and `smoke/refresh-run-report.json` (an immediate re-run, which must report zero false changes). The same results are published as the live viewer linked above.

## What changed in version 2.2

- **Optional search discovery** (above), off unless a key is supplied, gated exactly like a guessed domain.
- **Corroboration:** a registered role holder now also matches without middle names ("Stig Fardal" for "Stig Bertelsen Fardal"), and every registered address, including postal, location and registered workplaces (subunits), counts as an address match, not only the business address. P.O. boxes never count.
- **Job index:** the bundled NAV ad cache has been refreshed and enriched with employer org. nos.
- **Scale check:** 1,000 random companies from the public universe finished in 12 minutes with 1,000 terminal envelopes (see [docs/EVAL.md](docs/EVAL.md)).

## What changed in version 2.1 (after the first scored round)

- **Precision:**
  - Guessed non-`.no` domains need the org. no. on the site.
  - Corroboration reads only visible text and `tel:`/`mailto:` links.
  - Every descriptive word of the legal name must match, so group sites no longer verify subsidiaries.
  - Social handles must name the company, and vendor template accounts are rejected.
  - Webmail and login pages, and the shared domain of platform profile pages, are excluded.
  - Spam-compromised blogs publish no activity.
  - Only plausible Norwegian phone numbers are kept.
- **Synthesis:** `summary` is a plain-text paragraph with numbered citations, and `synthesis` adds what the company does, what changed (or "first observation") and what remains unknown, each with sources.
- **UX:** a redesigned viewer for find, verify and compare, published with GitHub Pages.
- **Recall:** open job ads are read from recruitment portals linked from verified sites, there are more domain candidates, and the full legal name with its legal form counts as corroboration. The job-feed catch-up no longer delays the website phase and has a hard time cap.

See [docs/EVAL.md](docs/EVAL.md) for the measurements.

## Envelope (abridged)

```jsonc
{
  "organisation_number": "997874234",
  "state": "available",                       // available | not_available | blocked | not_applicable | ambiguous | failed
  "run": {"run_id": "…", "started_at": "…", "completed_at": "…", "terminal_status": "completed"},
  "legal_identity": {"legal_name": "ANNI&CO AS", "legal_form": {…}, "registry_status": "active", …},
  "summary": "ANNI&CO AS is a … company in Ullensaker [1]. … Revenue NOK 11.2 m in 2025 [4]. …",
  "synthesis": {"what_it_does": {"text": "…", "sources": [1]}, "what_changed": {"status": "first_observation"},
                "what_remains_unknown": {"text": "…", "items": […]}, "sources": [{"n": 1, "url": "…"}], …},
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
| Website | registry URL, e-mail domain, job-ad homepage, name-derived domains, optional search API | verified official site with identity proof |
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
  - Optional search API (Brave Search, Serper or Tavily) for candidate domains only, when a key is supplied.
- **Secrets:** none required. An optional search key is read from the environment only, sent only in a request header and never logged or written to output.
- **Expected cost per official run:** $0 without a search key. With a Brave key, at most 2 queries per company that has no verified free candidate; at about $5 per 1,000 queries, that is at most about $10 for a 1,000-company run (the report states queries made and estimated cost). A typical run makes about 6–12 requests per company.

## Documentation

- [docs/AGENT.md](docs/AGENT.md): research and abstention policy
- [docs/IDENTITY_RESOLUTION.md](docs/IDENTITY_RESOLUTION.md): candidate generation and publication gates
- [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md): envelopes, claims, evidence and states
- [docs/REFRESH.md](docs/REFRESH.md): snapshots, diffs, idempotency and stale values
- [docs/CRAWLERS.md](docs/CRAWLERS.md): sources, rights, budgets and fallbacks
- [docs/EVAL.md](docs/EVAL.md): how it was measured
- [docs/LIMITATIONS.md](docs/LIMITATIONS.md): known gaps
