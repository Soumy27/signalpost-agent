"""Decision-useful synthesis built only from published claims.

Each sentence lists the claim ids that support it, so nothing in the summary
goes beyond the evidence. Unknowns are stated explicitly.
"""
from .evidence import SECTIONS
from .official import LEGAL_FORMS_EN


def _money(v):
    amt, cur = v.get("amount"), v.get("currency") or "NOK"
    if amt is None:
        return None
    a = abs(amt)
    s = f"{amt / 1e9:.1f} bn" if a >= 1e9 else f"{amt / 1e6:.1f} m" if a >= 1e6 else f"{amt / 1e3:.0f} k" if a >= 1e3 else f"{amt:.0f}"
    return f"{cur} {s}"


def build(claims, sections, changes, ctx):
    by = {}
    for c in claims:
        if c["availability"] == "available":
            by.setdefault(c["field"], []).append(c)

    def one(field):
        return (by.get(field) or [None])[0]

    parts = []

    def say(key, text, *cs):
        ids = [c["id"] for c in cs if c]
        parts.append({"topic": key, "text": text, "supporting_claims": ids})

    name, form, muni = one("legal_name"), one("legal_form"), one("municipality")
    if not name:
        status = sections.get("identity", {})
        return {"text": status.get("reason") or "The legal identity could not be established.", "sentences": [],
                "unknowns": [], "changes": []}
    fdesc = (form["value"].get("english") or (form["value"].get("description") or "").lower()) if form else "registered entity"
    first = f"{name['value']} (org. no. {ctx['orgnr']}) is {'an' if fdesc[:1] in 'aeiou' else 'a'} {fdesc}"
    if muni:
        first += f" registered in {muni['value']['name'].title()}"
    fd = one("founding_date") or one("registration_date")
    if fd:
        first += f", founded {fd['value'][:4]}"
    brand = one("public_brand")
    if brand:
        first += f", trading as “{brand['value']}”"
    say("identity", first + ".", name, form, muni, fd, brand)

    st = one("registry_status")
    if st and st["value"] != "active":
        say("status", f"Register status: {st['value'].replace('_', ' ')}"
            + (f" since {st['effective_date']}" if st.get("effective_date") else "") + ".", st)

    ind = sorted(by.get("industry", []), key=lambda c: c["value"].get("rank", 9))
    desc = one("website_description")
    act = None if desc else (one("registered_activity") or one("statutory_purpose"))
    if ind or desc or act:
        t = "What it does: "
        if ind:
            v = ind[0]["value"]
            t += f"registered industry {v.get('description_en') or v['description']} (NACE {v['code']})"
        if desc:
            t += ("; " if ind else "") + f"the company describes itself as: “{desc['value'][:220].rstrip('.')}”"
        elif act:
            t += ("; " if ind else "") + f"registered activity: “{act['value'][:220].rstrip('.')}”"
        say("business", t + ".", *(ind[:1] + [desc or act]))

    emp = one("registered_employees")
    lead = [one("chief_executive"), one("board_chair"), one("owner")]
    bits = []
    for c, label in zip(lead, ("CEO", "board chair", "owner")):
        if c:
            bits.append(f"{label} {c['value']['name']}")
    if bits or emp:
        t = ("Leadership: " + ", ".join(bits) + ".") if bits else ""
        if emp:
            t += f" Registered employees: {emp['value']}."
        say("people", t.strip(), *(lead + [emp]))

    rev = sorted([c for c in by.get("revenue", []) if c["value"]["scope"] == "company"], key=lambda c: c["value"]["fiscal_year"], reverse=True)
    npf = {c["value"]["fiscal_year"]: c for c in by.get("net_profit", []) if c["value"]["scope"] == "company"}
    eq = {c["value"]["fiscal_year"]: c for c in by.get("equity", []) if c["value"]["scope"] == "company"}
    if rev:
        y = rev[0]["value"]["fiscal_year"]
        t = f"Latest filed accounts ({y}): revenue {_money(rev[0]['value'])}"
        sup = [rev[0]]
        if y in npf:
            t += f", net result {_money(npf[y]['value'])}"
            sup.append(npf[y])
        if y in eq:
            t += f", equity {_money(eq[y]['value'])}"
            sup.append(eq[y])
        if len(rev) > 1 and rev[1]["value"]["amount"] and rev[1]["value"]["currency"] == rev[0]["value"]["currency"]:
            chg = (rev[0]["value"]["amount"] - rev[1]["value"]["amount"]) / abs(rev[1]["value"]["amount"]) * 100
            t += f"; revenue {chg:+.0f}% vs {rev[1]['value']['fiscal_year']} (derived from the two filings)"
            sup.append(rev[1])
        say("financials", t + ".", *sup)
    grp = [c for c in by.get("revenue", []) if c["value"]["scope"] == "group"]
    if grp:
        g = max(grp, key=lambda c: c["value"]["fiscal_year"])
        say("financials", f"Group revenue {g['value']['fiscal_year']}: {_money(g['value'])}.", g)
    parent = one("parent_company") or one("parent_entity")
    if parent:
        say("group", f"Part of a group: parent {parent['value'].get('name')} ({parent['value'].get('orgnr')}).", parent)
    subs = by.get("subsidiary", [])
    if subs:
        say("group", f"It has {len(subs)} registered subsidiar{'y' if len(subs) == 1 else 'ies'}.", *subs[:3])
    wp = one("registered_workplace_count")
    if wp and wp["value"]:
        say("locations", f"{wp['value']} registered workplace{'s' if wp['value'] != 1 else ''} (subunits).", wp)

    web, socials = one("official_website"), by.get("social_profile", [])
    if web:
        proof = "organisation number shown on the site" if web["value"]["identity_proof"] == "orgnr_on_site" else "legal name verified on the site"
        t = f"Official website {web['value']['url']} ({proof})"
        if socials:
            t += "; profiles on " + ", ".join(sorted(c["value"]["platform"].title() for c in socials))
        say("online", t + ".", web, *socials)
    jobs = [c for c in by.get("job_posting", [])]
    if jobs:
        nav = [c for c in jobs if c["value"].get("source") == "arbeidsplassen.nav.no"]
        titles = ", ".join(f"“{c['value']['title']}”" for c in jobs[:3])
        say("hiring", f"Appears to be hiring: {len(jobs)} job posting{'s' if len(jobs) != 1 else ''}"
            + (f" ({len(nav)} active on arbeidsplassen.nav.no)" if nav else "") + f", e.g. {titles}.", *jobs[:5])
    news = sorted(by.get("company_news", []), key=lambda c: c["value"]["date"], reverse=True)
    if news:
        say("activity", f"Latest dated activity: “{news[0]['value']['title']}” ({news[0]['value']['date']}); "
            f"{len(news)} dated item{'s' if len(news) != 1 else ''} from the company's own channels.", *news[:3])

    change_text = []
    for ch in changes[:6]:
        change_text.append(ch["description"])
    if change_text:
        parts.append({"topic": "changes", "text": "Changes since the previous check: " + "; ".join(change_text) + ".",
                      "supporting_claims": []})

    titles = dict(SECTIONS)
    unknowns = []
    for key, st in sections.items():
        if st["state"] != "available":
            unknowns.append({"section": key, "title": titles.get(key, key), "state": st["state"], "reason": st.get("reason")})
    if unknowns:
        parts.append({"topic": "unknowns", "supporting_claims": [],
                      "text": "Not established: " + "; ".join(f"{u['title'].lower()} ({u['state'].replace('_', ' ')})" for u in unknowns) + "."})
    return {"text": " ".join(p["text"] for p in parts), "sentences": parts, "unknowns": unknowns,
            "changes": change_text}
