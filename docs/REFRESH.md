# Refresh

Refreshing means running the same command with the same `--out` (or `--state`) directory. State is kept per company in `state/<orgnr>.json.gz`. It holds the last claims and evidence, the first time each claim key was seen, a claims-snapshot hash and an append-only change log.

## Diff

Claims are compared by their stable `key`, using the value (not retrieval times or hashes):

| Situation | Change type (examples) |
|---|---|
| key appears | `new_role`, `new_filing`, `new_job`, `new_location`, `new_activity`, `new_website`, `new_social_profile`, `new_subsidiary`, `new_<field>` |
| key disappears | `removed_role`, `closed_job`, `closed_location`, `removed_website`, `removed_<field>` |
| value differs | `changed_registered_business_address`, `changed_registry_status`, `changed_registered_employees`, `changed_role`, `changed_<field>` |

Each change carries `previous_value`, `current_value`, `previous_evidence` (full copies of the earlier evidence records), `current_evidence_ids`, `previous_observed_at`, `detected_at`, `materiality` (`high` for status, name, form, address, CEO, chair, owner, auditor, parent and filings) and a readable `description`. The summary lists the most material changes.

## Idempotency

- Re-running on the same source snapshot produces no changes, and the claims snapshot hash is unchanged (`refresh.unchanged_since_previous: true`).
- The change log is de-duplicated on `(claim_key, change_type, current value)`, so the same snapshot never creates duplicate records.
- `first_observed_at` is kept from the first run that saw a value.

## Failed or skipped sources

The website and job-feed sections are volatile. If the website section is `failed` or `blocked` on a refresh (for example a previously verified site is unreachable), its earlier claims are carried forward with `stale: true`. Their evidence is copied with `carried_from_run`, and no removal is reported. A previously verified site that is unreachable sets the website section to `failed` rather than `not_available`, so a transient outage is never reported as a change.

## Scheduling

`python3 -m signalpost changed --out out --since <ISO time>` reads Brønnøysund's entity and role update feeds and lists the stored companies that changed. Annual accounts change about once a year. The job feed is synced incrementally on every run from the last cached position.
