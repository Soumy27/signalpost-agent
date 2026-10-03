# Evaluation

All sets are random samples from Builderr's public universe (`signalpost-company-universe-2025.jsonl.gz`, 411,160 companies), drawn with `python3 -m signalpost sample N --universe … --seed S`. The sets don't overlap.

| Set | Seed | Use |
|---|---|---|
| development | 7 | tuning the identity gate, extraction and discovery |
| validation | 99 | precision audit and refresh false-change test, run once per change |
| smoke | 2026 | the submitted 100-company smoke test (`smoke/`) |

## Precision audit (validation set, 100 companies)

Every website-derived publication was checked by hand against the live site.

| Check | Result |
|---|---|
| Verified official websites | 15 / 100 companies |
| Correct legal entity | **15 / 15** (9 with the org. no. on the site; 6 with name plus registered address, e-mail or role-holder corroboration) |
| Wrong-company publications | **0** |
| Social profiles published | 13, all with handles of the company itself |
| Job-ad URLs | the same audit on the dev set found careers *sections* counted as ads, which led to a stricter job-ad URL rule (see IDENTITY_RESOLUTION.md) |

Rejected candidates included group and parent sites (`bring.com` for Bring Intermodal; sites showing another org. no.), parked or under-construction domains, and same-name businesses on guessed domains without corroboration. All were reported as `ambiguous`, `not_available` or `blocked`, never published.

## Refresh correctness (validation set)

| Test | Result |
|---|---|
| Two back-to-back live runs | **0 false changes** across 100 companies; `unchanged_since_previous` true for all |
| Same-snapshot re-run (unit test) | no changes, no duplicate change-log rows |
| Real change (unit test: employees 7 → 9, CEO replaced) | `changed_registered_employees`, `new_role` and `removed_role`, with previous evidence attached |
| Website unreachable / rate-limited on refresh | values kept and marked `stale`; no removal reported |
| Website item missing once | carried as `stale`/`pending_removal`; removal reported only after a second consecutive miss |

An earlier version showed 22 false changes on the same test. They came from run metadata in values (`found_via`, trailing slashes), a site returning HTTP 429, and website-derived fields outside the website section. All three are fixed and covered by tests.

## Coverage and cost (100-company runs)

Submitted smoke run (`smoke/run-report.json`): 100/100 envelopes, 7,846 published claims, 684 requests, 130 s, $0. The refresh run reported 0 changes. Section availability on the smoke set: identity, financials, leadership and locations 100 %, verified website 15 %, contact 40 %, group 9 % (all in-group companies), social profiles 9 %, hiring 4 %, dated activity 3 %. Typical figures from the validation set:

| Section | Available |
|---|---|
| identity, leadership, locations | 100 % |
| financials | 99–100 % (failures are Regnskapsregisteret HTTP 500s, reported as `failed`) |
| group | all companies the register marks as in a group |
| verified website | 12–21 % |
| contact | ~45 % |
| social profiles | 6–9 % |
| dated activity | 2–3 % |
| hiring | depends on NAV ads by employer org. no.; see the smoke report |

About 6–12 requests per company, 1.5–3 minutes per 100 companies, $0 third-party cost.

## Tests

`python3 -m unittest discover tests` runs the offline suite with no network. It covers MOD11, JSON-pointer evidence, verbatim financials with periods, rejection of foreign account rows, no silent zeros, identity mismatch, the snapshot fallback, every website gate, social handle checks, the job-ad URL rule, refresh idempotency, typed changes, stale carry-forward, two-miss removal, the outbound URL policy, and one envelope per input (including invalid and unknown numbers).
