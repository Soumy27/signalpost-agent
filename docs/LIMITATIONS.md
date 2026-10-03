# Limitations

- **Website discovery without a search API.** Candidates come only from the register, the registered e-mail domain, NAV job ads and name-derived domains. Companies whose site uses a brand name unrelated to the legal name, and that don't list it in the register, are usually missed. No search provider is used, because evaluator runs can't use personal account credentials.
- **JavaScript-only sites.** There is no browser rendering, so sites whose HTML is an empty JS shell can't be verified or mined. The gate treats them as empty pages, so they are never published wrongly.
- **Single-token names.** A name such as `GUDRUN AS` on a guessed domain needs corroboration (phone, address, postcode + city, e-mail or a registered leader on the site), so many such sites stay `ambiguous`.
- **Job ads.** NAV's feed covers ads published on arbeidsplassen.nav.no, which includes most but not all Norwegian ads. Ads posted after the last cache sync need one detail request each to learn the employer org. no.; this is capped at 30 % of the run budget.
- **Accounts.** The normalised Regnskapsregisteret API returns errors for a small slice of banks and insurers; these are reported as `failed`. Filing PDFs are not parsed.
- **No sentiment, ratings or reviews.** The official Mattilsynet smiley inspection dataset's documented download host (`hotell.difi.no`) no longer resolves. The replacement site's search index has no organisation numbers, so attribution would rely on name/address matching and it is not used. No permitted, key-free source with exact-company attribution was available, so these families are not attempted rather than approximated.
- **Social profiles** are links from the verified site only. The platforms themselves are not crawled, so follower counts and posts are not collected.
- **Brønnøysund announcements** (`w2.brreg.no/kunngjoring`) are disallowed by robots.txt and are not used.
