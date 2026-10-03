# Connectors, rights and budgets

| Connector | Access | Rights / policy | Requests |
|---|---|---|---|
| Enhetsregisteret entity, subunit, roles, subunit search, group structure | `data.brreg.no/enhetsregisteret/api/…` | Open data, NLOD 2.0, no key. Role holders' birth dates are discarded | 4–6 per company |
| Regnskapsregisteret | `data.brreg.no/regnskapsregisteret/regnskap/{orgnr}` | Open data, NLOD 2.0 | 1 per company |
| NAV job-vacancy feed | `pam-stilling-feed.nav.no/api/v1/feed` and `/feedentry/{uuid}`, with the public token from `/api/publicToken` | NAV's public re-use feed for third parties. Only ad metadata (title, employer, dates, location, link and a short excerpt) is published | incremental sync of a few pages per run, plus one detail per new ad, plus one live re-fetch per matched ad |
| Statistics Norway KLASS (bundled) | `signalpost/data/nace_en.json`, from `data.ssb.no/api/klass/v1/classifications/6/codes?language=en` | CC BY 4.0, attributed. A static reference table for English industry names; no request is made at run time | 0 |
| Company websites | homepage plus up to about 6 targeted pages (contact, about, careers, news, people, locations), the RSS/Atom feed and the WordPress posts API | `robots.txt` is fetched and honoured for every host. A clear user agent is sent. Only identity-verified sites are mined | up to 16 per company |

Not used: search-engine result pages, LinkedIn, Meta, Glassdoor, Indeed or any other platform whose terms prohibit automated collection. Brønnøysund's `w2.brreg.no/kunngjoring` announcements are excluded because its `robots.txt` disallows them. Social profiles are reported only as links found on the verified company site, and the platforms themselves are never crawled.

## Declared cache

`signalpost/data/nav_jobs_index.jsonl.gz` is a cache of NAV's public feed: the active ads with employer org. no., employer name, employer homepage, title and dates. It is built from the feed by `JobIndex.sync()` and `JobIndex.enrich()`, synced incrementally at the start of every run, and the updated copy is written to `out/state/nav_jobs_index.jsonl.gz`. Ads that match a company are re-fetched live during the run, so their evidence carries the run's own retrieval time and content hash.

## Budgets

- **Global:** `--max-requests` (or `SIGNALPOST_MAX_REQUESTS`; default 30 × companies, minimum 2,000) and `--minutes` (or `SIGNALPOST_MAX_MINUTES`; default 45). Every request is counted, including retries, robots.txt and feed pages. The run stops 6 % before the deadline (between 30 s and 120 s) to write all outputs.
- **Job-feed enrichment** may use at most 30 % of the request budget and about 40 % of the remaining time.
- **Per-company web budget:** at most 16 requests, shrunk automatically when the global budget is tight.
- **Order of degradation:** register data is fetched first for every company. If the budget runs short, website and job sections become `failed` with the reason "Run budget exhausted"; envelopes are never dropped.

## Concurrency and politeness

- Up to 16 parallel workers for Brønnøysund and 24 for websites. Each website is a different host, so per-host load is one request at a time.
- 6 workers for NAV ad details.
- Retries: 2 for APIs (with back-off on 429 and 5xx), 0 for websites.
- Response size cap: 2 MB per page (30 MB for feed pages).

## Outbound URL policy (safe URL handling)

Website URLs come from public data (register entries, e-mail domains, job ads), so every request goes through `http.check_url` before it is sent, and so does every redirect hop:

- only `http`/`https`, with no credentials embedded in the URL;
- only ports 80, 443, 8080 and 8443;
- the host must resolve only to public (global) IP addresses. Loopback, private, link-local (including cloud metadata `169.254.169.254`), reserved and multicast addresses are refused.

Refused URLs are logged as `blocked:outbound_policy` in `requests.log.jsonl` and never fetched. No secrets are used; nothing is sent to any site beyond a plain GET with a descriptive user agent.
