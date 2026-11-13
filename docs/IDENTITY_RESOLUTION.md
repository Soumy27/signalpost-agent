# Identity resolution

The organisation number is the anchor. A brand, domain, social handle or job ad is only a *candidate* until it resolves back to that exact legal entity. When the evidence is not strong enough, the agent abstains and returns `ambiguous` or `not_available`. A missed fact costs less than a wrong-company publication.

## Official registers

- Every Brønnøysund response is checked: `organisasjonsnummer` in the body must equal the requested number. If the entity response doesn't match, nothing is published and the envelope is `failed`.
- An annual-account row filed under a different organisation number is discarded and logged in `errors`.
- Subunits (`underenhet`) are resolved as such. The parent is labelled `parent_entity` and is never merged into the subunit.
- Group relationships come from `konsernstruktur` and are labelled `parent_company`, `ultimate_parent` or `subsidiary`. Facts about other group members are never attributed to this company.

## Website candidates (in order)

1. The website verified on the previous run, if there was one.
2. The website listed in Enhetsregisteret (`hjemmeside`). Typos such as `www-firma.no` are normalised. Directory and social URLs are ignored.
3. The domain of the registered e-mail address, unless it is a free-mail or ISP domain (the list is in `website.py`).
4. The employer homepage stated in a NAV job ad whose employer org. no. is this company.
5. Up to five domains derived from the legal name (e.g. `ANNI&CO AS` gives `anniogco.no`, `anni-co.no` and so on). A guess is fetched only if the domain resolves in DNS.

## Publication gate

| Evidence | Score | Published? |
|---|---|---|
| The organisation number (any common formatting) appears on the homepage, contact or about page | 1.00 | yes, `orgnr_on_site` |
| **Every** name token except legal suffixes and stop words (`AS`, `ASA`, `og`, `and`, …) appears together in the homepage's identity text (title, `og:site_name`, JSON-LD `Organization` name, host name, H1). Descriptive words such as *consulting*, *eiendom*, *holding* and *norge* must match too, so a group brand never matches a subsidiary | 0.95 | yes for candidates 1–4, `legal_name_on_site` |
| As above, for a name-derived `.no` domain (5), **plus** independent corroboration read from visible text or `tel:`/`mailto:` links only (never scripts or markup): the registered phone number (whole number, digit boundaries), the registered street address with postcode, the registered e-mail, or a registered CEO or chair by full name | 0.95 | yes |
| Name-derived domain that is **not** `.no` (e.g. `.com`) without the org. no. on the site | 0.85 | no, `ambiguous` |
| Name-derived domain with a name match but no corroboration | 0.85 | no, `ambiguous` |
| Partial name overlap (≥ 50 % of tokens) | 0.60 | no, `ambiguous` |
| Another valid organisation number labelled `org.nr`/`MVA` on the page, and not one of this company's subunits | 0.20 | no; this is a parent, group, franchise or agency site |
| Parked, for-sale, under-construction, hosting-default or empty page | 0.10 | no |
| Business sports club (`BIL`) pointing to the operating company's site | 0.30 | no |

These rules were tightened after the first scored round, which found wrong-company claims. For example, `isabellacafe.com` (a US restaurant) was published for *Isabella Cafe AS* in Ringerike, and Squarespace's own social accounts were taken from a Squarespace template site. Each failure mode now has a regression test.

## Things published from a verified site

- **Social profiles**: only when the handle itself contains the company's name or its verified brand. A JSON-LD `sameAs` link alone is not trusted, because site-builder templates ship their own vendor accounts. Vendor and platform accounts (Squarespace, Wix, Shopify, WordPress, …) are always rejected. Links are reduced to the profile root (a link to one video or post becomes the page itself).
- **Phone numbers**: only plausible Norwegian numbers (`+47` plus 8 digits, first digit 2–9), stored in canonical form. Concatenated template numbers and placeholders are dropped.
- **Contact e-mails**: only addresses on the site's own registrable domain, or the registered e-mail.
- **Job postings**: only URLs that match a known job-ad pattern (Teamtailor, Webcruiter, Jobbnorge, FINN, Recman, HR-manager, Easycruit, ReachMee, Workday, SmartRecruiters, Lever, Greenhouse, Jobylon, The Hub, arbeidsplassen) or a job-detail path on the company's own domain. Careers *sections* such as "Graduate programme" or "Our culture" are excluded.
- **Job postings from a recruitment portal**: when the verified site links to a recruitment portal, that portal page is read and its individual open ads are published, each labelled with the portal and the company link it was reached from.
- **Dated activity**: only items with a machine-readable or printed date, from RSS/Atom, the WordPress posts API, `<article><time>` markup, JSON-LD articles, or a news list where a date sits next to the link. Only items from the last two years are kept, and none dated in the future. The homepage itself is never a news item. If any post on a site is gambling or pharma spam, or is written in a foreign script (a sign the site has been compromised), **no** post from that site is published.
- **Self-description**: published as company-owned copy, labelled as such, and never used as an independent assessment.

## Job ads (NAV)

A NAV ad is published only when the ad detail's `employer.orgnr` equals the company or one of its registered subunits. Name similarity is used only to choose which ads to look up. It is never evidence on its own.
