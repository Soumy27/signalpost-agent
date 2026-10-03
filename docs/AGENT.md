# Research and abstention policy

1. **Anchor.** Fetch the entity from Enhetsregisteret and confirm the organisation number in the response. Only then fetch roles, subunits, annual accounts and, when the register says the entity is in a group, the group structure.
2. **Resolve the public identity.** Generate website candidates (register URL, e-mail domain, job-ad homepage, name-derived domains) and pass each through the publication gate in [IDENTITY_RESOLUTION.md](IDENTITY_RESOLUTION.md). Stop at the first exact match.
3. **Crawl deterministically.** On a verified site, read the homepage and a few targeted pages. Extract in layers: structured data (JSON-LD, OpenGraph, RSS) first, then DOM attributes (`mailto:`, `tel:` and link targets), then dated text patterns. There is no model extraction and no browser rendering.
4. **Hiring.** Match NAV job ads by exact employer org. no. (the company or its subunits), and add job-ad links found on the verified site.
5. **Synthesise.** Build the summary only from published claims. Each sentence lists its supporting claim ids, and the summary states what remains unknown.
6. **Refresh.** Diff against the previous state, emit typed changes with both sides' evidence, and carry forward values from sources that failed on this run.

## Abstention

- If identity is uncertain, the section is `ambiguous` and nothing from that candidate is published.
- If a source was checked and holds nothing, the section is `not_available`, with the reason and the sources checked.
- If robots.txt disallows a site, the section is `blocked`.
- If a section doesn't apply (for example accounts for a subunit, or group structure for a company outside a group), it is `not_applicable`.
- On a source error or exhausted budget, the section is `failed` and the error is recorded.
- A missing value is never converted to zero. A register that reports no employee count gives `registered_employees: not_available`.
