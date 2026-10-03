# Data schema

## Envelope (one per input, JSONL)

| Field | Type | Notes |
|---|---|---|
| `organisation_number` | string | the input, normalised to 9 digits |
| `state` | enum | `available` (identity established), `not_available` (number not in the register), `failed` (invalid input, register mismatch or error). `blocked`, `not_applicable` and `ambiguous` are used at section level |
| `run` | object | `run_id`, `started_at`, `completed_at`, `agent_version`, `terminal_status` |
| `legal_identity` | object | convenience copy of the core identity claims (name, form, status, address, municipality, industry, brand, dates, employees) |
| `summary` | object | `text`, `sentences[]` (each with `topic`, `text` and `supporting_claims` ids), `unknowns[]`, `changes[]` |
| `sections` | object | one entry per section: `identity`, `financials`, `leadership`, `locations`, `group`, `website`, `social_profiles`, `contact`, `jobs`, `activity`. Each has `state`, plus `reason` and `sources_checked` when not available |
| `claims` | array | see below |
| `evidence` | array | see below |
| `changes` | array | material changes since the previous run (see [REFRESH.md](REFRESH.md)) |
| `errors` | array | `{source, url, error, retryable}` |
| `operations` | object | `requests`, `runtime_ms`, `third_party_cost_usd` |
| `refresh` | object | `previous_run_id`, `previous_completed_at`, `first_seen_at`, `last_checked_at`, `check_count`, `changes_this_run`, `claims_snapshot_sha256`, `unchanged_since_previous` |

## Claim

| Field | Notes |
|---|---|
| `id` | `cl-N`, unique within the envelope |
| `key` | stable across runs: `field:normalised-identity`, e.g. `board_member:ola nordmann`, `revenue:company:2025`, `job_posting:<uuid>` |
| `section`, `field` | the section and fact type |
| `value` | scalar or object. Money is `{amount, currency, scope, fiscal_year}` |
| `availability` | `available`, or an explicit absence (`not_available`) with `value: null` and a `note` |
| `confidence` | 1.0 for register data and org.-no.-verified sites, 0.95 for name-verified sites, ≤ 0.93 for derived site items |
| `evidence_ids` | evidence ids in the same envelope (at least one for every available claim) |
| `reporting_period` | `{start, end}` for accounting values |
| `effective_date` | date given by the source itself (role change date, filing period end, ad publication date, news date) |
| `first_observed_at`, `last_observed_at` | from refresh history |
| `stale` | true when a volatile value could not be re-confirmed and was carried forward |
| `note` | provenance remarks |

## Evidence

| Field | Notes |
|---|---|
| `id` | `ev-N` |
| `source_url`, `final_url`, `redirect_chain` | the request, the final URL after redirects, and each redirect hop |
| `source_class` | `official_registry`, `official_accounts`, `company_owned` or `public_api` |
| `source_name` | human-readable source |
| `http_status`, `retrieved_at` | the response status and UTC timestamp |
| `content_sha256` | SHA-256 of the captured body. The body is stored at `out/snapshots/<sha256>.gz` |
| `claim_span` | the exact JSON pointer and value (`/rollegrupper/0/roller/0/person/navn = {…}`) or the text excerpt the value was read from |
| `selector` | JSON pointer or HTML selector |
| `extraction_method` | `json_pointer`, `orgnr_regex_visible_text`, `legal_name_tokens`, `meta_description`, `html_anchor`, `jsonld`, `rss_or_atom_item`, `wordpress_rest_api`, `html_date_proximity`, … |
| `extractor_version` | agent version |
| `reporting_period` | for accounts |
| `carried_from_run` | set when the evidence comes from an earlier run (stale carry-forward) |

## Run report (`run-report.json`)

It records the expected and emitted counts, entity states, per-section state counts, published claims per section, the number of changes, operations (requests, budget, bytes, runtime, p50/p95 per request and per company, cost), the job-feed sync status, and validation checks: exact count, unique numbers, valid states, evidence on every published claim, and hash and time on every evidence record.
