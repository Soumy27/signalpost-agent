"""Builds an evidence-backed company profile.

Every fact is stored as {"value", "source", "as_of"} where `source` points into
profile["sources"] (URL + retrieval time) and `as_of` is the date the source
itself gives for the fact (registration date, role change date, accounting
period end...). Financial values are copied verbatim from Regnskapsregisteret;
nothing is estimated or generated.
"""
from . import brreg, website
from .http import BudgetExceeded

SCHEMA_VERSION = "1.0"

ROLE_FIELDS = {
    "DAGL": "chief_executive",
    "LEDE": "board_chair",
    "NEST": "board_deputy_chair",
    "MEDL": "board_members",
    "VARA": "board_deputy_members",
    "OBS": "board_observers",
    "REVI": "auditor",
    "REGN": "accountant",
    "INNH": "owner",
    "DTPR": "partners",
    "DTSO": "partners",
    "KOMP": "general_partners",
    "KONT": "contact_person",
    "FFØR": "business_manager",
    "REPR": "norwegian_representative",
    "KDEB": "bankruptcy_trustee",
    "BEST": "managing_shipowner",
    "SIGN": "signature",
    "PROK": "procuration",
}


def _date(s):
    return s[:10] if isinstance(s, str) and s else None


def _addr(a):
    if not a:
        return None
    parts = list(a.get("adresse") or [])
    parts.append(" ".join(x for x in (a.get("postnummer"), a.get("poststed")) if x))
    if a.get("land") and a.get("landkode") != "NO":
        parts.append(a["land"])
    return {
        "text": ", ".join(p for p in parts if p),
        "street": a.get("adresse") or [],
        "postal_code": a.get("postnummer"),
        "city": a.get("poststed"),
        "municipality": a.get("kommune"),
        "municipality_number": a.get("kommunenummer"),
        "country_code": a.get("landkode"),
    }


LEGAL_FORMS_EN = {
    "AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
    "NUF": "Norwegian-registered foreign enterprise", "ANS": "general partnership",
    "DA": "partnership with shared liability", "SA": "cooperative", "STI": "foundation",
    "FLI": "association", "BA": "company with limited liability", "KS": "limited partnership",
    "IKS": "inter-municipal company", "KF": "municipal enterprise", "SF": "state-owned enterprise",
    "ORGL": "organisational unit", "SPA": "savings bank", "BRL": "housing cooperative",
    "ESEK": "condominium", "BEDR": "business subunit", "AAFY": "subunit (non-business)",
    "KOMM": "municipality", "FKF": "county municipal enterprise", "PK": "pension fund",
    "SAM": "jointly owned property", "STAT": "state body", "FYLK": "county authority",
    "KIRK": "church body", "PRE": "shipping partnership", "OPMV": "VAT-separated unit",
    "ADOS": "administrative unit", "UTLA": "foreign entity", "GFS": "mutual insurance company",
}


def _code(c):
    return {"code": c.get("kode"), "description": c.get("beskrivelse")} if c else None


class ProfileBuilder:
    def __init__(self, orgnr):
        self.orgnr = orgnr
        self.facts = {}
        self.sources = {}
        self.notes = []
        self.checks = []

    def src(self, source):
        key = source["id"]
        self.sources[key] = source
        return key

    def add(self, field, value, source_id, as_of=None, **extra):
        if value is None or value == [] or value == {} or value == "":
            return
        f = {"value": value, "source": source_id}
        if as_of:
            f["as_of"] = as_of
        f.update({k: v for k, v in extra.items() if v is not None})
        self.facts[field] = f

    def check(self, source_id, found_orgnr):
        ok = found_orgnr == self.orgnr
        entry = {"source": source_id, "method": "orgnr_exact_match", "orgnr_in_response": found_orgnr, "match": ok}
        if entry not in self.checks:
            self.checks.append(entry)
        if not ok:
            self.notes.append(f"Discarded data from {source_id}: response referred to {found_orgnr}, not {self.orgnr}.")
        return ok


# ---- Enhetsregisteret ---------------------------------------------------------------

def _entity_facts(b, d, sid, kind):
    reg = _date(d.get("registreringsdatoEnhetsregisteret"))
    b.add("name", d.get("navn"), sid, reg)
    b.add("legal_form", _code(d.get("organisasjonsform")), sid, reg)
    b.add("register_unit_type", "subunit (underenhet)" if kind == "underenhet" else "main entity (enhet)", sid)

    if d.get("slettedato"):
        status = "deleted"
    elif d.get("konkurs"):
        status = "bankrupt"
    elif d.get("underTvangsavviklingEllerTvangsopplosning"):
        status = "under_forced_liquidation"
    elif d.get("underAvvikling"):
        status = "under_liquidation"
    elif d.get("nedleggelsesdato"):
        status = "closed"
    else:
        status = "active"
    b.add("status", status, sid, _date(d.get("slettedato") or d.get("konkursdato") or d.get("underAvviklingDato")
                                        or d.get("tvangsavvikletPgaManglendeSlettingDato") or d.get("nedleggelsesdato")))
    b.add("deleted_date", _date(d.get("slettedato")), sid)
    b.add("bankruptcy_date", _date(d.get("konkursdato")), sid)
    b.add("liquidation_date", _date(d.get("underAvviklingDato")), sid)
    b.add("closed_date", _date(d.get("nedleggelsesdato")), sid)

    b.add("founded_date", _date(d.get("stiftelsesdato")), sid)
    b.add("start_date", _date(d.get("oppstartsdato")), sid)
    b.add("registered_date", reg, sid)
    b.add("business_register_date", _date(d.get("registreringsdatoForetaksregisteret")), sid)
    b.add("articles_of_association_date", _date(d.get("vedtektsdato")), sid)

    b.add("business_address", _addr(d.get("forretningsadresse")), sid)
    b.add("location_address", _addr(d.get("beliggenhetsadresse")), sid)
    b.add("postal_address", _addr(d.get("postadresse")), sid)
    main_addr = d.get("forretningsadresse") or d.get("beliggenhetsadresse") or {}
    if main_addr.get("kommune"):
        b.add("municipality", {"name": main_addr.get("kommune"), "number": main_addr.get("kommunenummer")}, sid)

    codes = [_code(d[k]) for k in ("naeringskode1", "naeringskode2", "naeringskode3") if d.get(k)]
    b.add("industry_codes", codes, sid, note="NACE (SN2007/SN2025) codes as registered")
    b.add("primary_industry", codes[0] if codes else None, sid)
    b.add("institutional_sector", _code(d.get("institusjonellSektorkode")), sid)

    if d.get("harRegistrertAntallAnsatte") is not False and d.get("antallAnsatte") is not None:
        b.add("employees", d["antallAnsatte"], sid,
              _date(d.get("registreringsdatoAntallAnsatteNAVAaregisteret") or d.get("registreringsdatoAntallAnsatteEnhetsregisteret")),
              note="Employee count reported to NAV Aa-registeret via Enhetsregisteret")
    elif d.get("harRegistrertAntallAnsatte") is False:
        b.add("employees", 0, sid, note="Register states no registered employees")

    b.add("website_registered", d.get("hjemmeside"), sid)
    b.add("email", d.get("epostadresse"), sid)
    b.add("phone", d.get("telefon"), sid)
    b.add("mobile", d.get("mobil"), sid)

    k = d.get("kapital")
    if k:
        b.add("share_capital", {"amount": k.get("belop"), "currency": k.get("valuta"), "shares": k.get("antallAksjer"),
                                "type": k.get("type"), "fully_paid": k.get("fullInnbetalt")}, sid, _date(k.get("innfortDato")))

    b.add("purpose", " ".join(d.get("vedtektsfestetFormaal") or []) or None, sid)
    b.add("activity", " ".join(d.get("aktivitet") or []) or None, sid)

    regs = {
        "vat_register": d.get("registrertIMvaregisteret"),
        "business_register": d.get("registrertIForetaksregisteret"),
        "voluntary_register": d.get("registrertIFrivillighetsregisteret"),
        "foundation_register": d.get("registrertIStiftelsesregisteret"),
        "party_register": d.get("registrertIPartiregisteret"),
    }
    b.add("registrations", {k: v for k, v in regs.items() if v is not None}, sid)
    b.add("vat_registered_date", _date(d.get("registreringsdatoMerverdiavgiftsregisteret")), sid)
    b.add("voluntary_vat_registration", d.get("frivilligMvaRegistrertBeskrivelser"), sid)
    b.add("part_of_group", d.get("erIKonsern"), sid)
    b.add("parent_orgnr", d.get("overordnetEnhet"), sid)
    b.add("former_names", [{"name": h.get("navn"), "from": _date(h.get("fraDato")), "to": _date(h.get("tilDato"))}
                           for h in d.get("historiskeNavn") or []], sid)
    b.add("latest_annual_accounts_filed", d.get("sisteInnsendteAarsregnskap"), sid)
    b.add("audit_opt_out_date", _date(d.get("fravalgRevisjonDato")), sid)
    b.add("register_annotations", [p.get("infotekst") or p for p in d.get("paategninger") or []], sid)
    b.add("language_form", d.get("maalform"), sid)


# ---- roles -----------------------------------------------------------------------------

def _party(role):
    if role.get("person"):
        n = role["person"].get("navn") or {}
        name = " ".join(x for x in (n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")) if x)
        out = {"name": name, "type": "person"}
        if role["person"].get("erDoed"):
            out["deceased"] = True
        return out
    if role.get("enhet"):
        e = role["enhet"]
        name = " ".join(e.get("navn") or []) if isinstance(e.get("navn"), list) else e.get("navn")
        return {"name": name, "type": "organisation", "orgnr": e.get("organisasjonsnummer"),
                "legal_form": (e.get("organisasjonsform") or {}).get("kode")}
    return {"name": None, "type": "unknown"}


def _role_facts(b, data, sid):
    by_field = {}
    all_roles = []
    for group in data.get("rollegrupper") or []:
        as_of = _date(group.get("sistEndret"))
        gtype = (group.get("type") or {}).get("kode")
        for role in group.get("roller") or []:
            if role.get("avregistrert") or role.get("fratraadt"):
                continue
            rtype = role.get("type") or {}
            entry = {"role_code": rtype.get("kode"), "role": rtype.get("beskrivelse"), **_party(role)}
            if role.get("groupRole"):
                entry["group_role"] = role["groupRole"]
            all_roles.append({**entry, "as_of": as_of})
            field = ROLE_FIELDS.get(rtype.get("kode")) or ROLE_FIELDS.get(gtype)
            if field:
                by_field.setdefault(field, {"items": [], "as_of": as_of})["items"].append(entry)
    single = {"chief_executive", "board_chair", "board_deputy_chair", "auditor", "accountant", "owner",
              "contact_person", "business_manager", "norwegian_representative", "managing_shipowner"}
    for field, v in by_field.items():
        value = v["items"][0] if field in single and len(v["items"]) == 1 else v["items"]
        b.add(field, value, sid, v["as_of"])
    b.add("roles", all_roles, sid, note="All current (non-resigned) registered roles")


# ---- accounts --------------------------------------------------------------------------

def _num(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _summarise_accounts(r):
    res = r.get("resultatregnskapResultat") or {}
    ek = r.get("egenkapitalGjeld") or {}
    eid = r.get("eiendeler") or {}
    period = r.get("regnskapsperiode") or {}
    fig = {
        "revenue": _num(res, "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
        "sales_revenue": _num(res, "driftsresultat", "driftsinntekter", "salgsinntekter"),
        "operating_expenses": _num(res, "driftsresultat", "driftskostnad", "sumDriftskostnad"),
        "payroll_expenses": _num(res, "driftsresultat", "driftskostnad", "loennskostnad"),
        "operating_profit": _num(res, "driftsresultat", "driftsresultat"),
        "net_financial_items": _num(res, "finansresultat", "nettoFinans"),
        "profit_before_tax": _num(res, "ordinaertResultatFoerSkattekostnad"),
        "tax_expense": _num(res, "ordinaertResultatSkattekostnad"),
        "net_profit": _num(res, "aarsresultat"),
        "total_assets": _num(eid, "sumEiendeler"),
        "fixed_assets": _num(eid, "anleggsmidler", "sumAnleggsmidler"),
        "current_assets": _num(eid, "omloepsmidler", "sumOmloepsmidler"),
        "cash_and_bank": _num(eid, "sumBankinnskuddOgKontanter"),
        "equity": _num(ek, "egenkapital", "sumEgenkapital"),
        "total_liabilities": _num(ek, "gjeldOversikt", "sumGjeld"),
        "short_term_liabilities": _num(ek, "gjeldOversikt", "kortsiktigGjeld", "sumKortsiktigGjeld"),
        "long_term_liabilities": _num(ek, "gjeldOversikt", "langsiktigGjeld", "sumLangsiktigGjeld"),
    }
    rev = r.get("revisjon") or {}
    princ = r.get("regnkapsprinsipper") or {}
    return {
        "fiscal_year": int(period["tilDato"][:4]) if period.get("tilDato") else None,
        "period_start": period.get("fraDato"),
        "period_end": period.get("tilDato"),
        "statement_type": r.get("regnskapstype"),
        "currency": r.get("valuta"),
        "figures": {k: v for k, v in fig.items() if v is not None},
        "audited": None if not rev else not (rev.get("ikkeRevidertAarsregnskap") or rev.get("fravalgRevisjon")),
        "audit_opted_out": rev.get("fravalgRevisjon"),
        "accounting_rules": princ.get("regnskapsregler"),
        "small_company": princ.get("smaaForetak"),
        "liquidation_accounts": r.get("avviklingsregnskap"),
        "filing_journal_number": r.get("journalnr"),
    }


def _account_facts(b, accounts, sid):
    usable = []
    for r in accounts:
        if b.check(sid, str(_num(r, "virksomhet", "organisasjonsnummer"))):
            usable.append(_summarise_accounts(r))
    usable = [a for a in usable if a["period_end"]]
    for stype, field in (("SELSKAP", "financials"), ("KONSERN", "group_financials")):
        rows = sorted((a for a in usable if a["statement_type"] == stype), key=lambda a: a["period_end"], reverse=True)
        if not rows:
            continue
        b.add(field, rows[0], sid, rows[0]["period_end"],
              note="Figures copied verbatim from the filed annual accounts")
        if len(rows) > 1:
            b.add(field + "_previous_year", rows[1], sid, rows[1]["period_end"])


# ---- assemble ----------------------------------------------------------------------------

def build_core(client, orgnr):
    b = ProfileBuilder(orgnr)
    kind, data, s = brreg.fetch_entity(client, orgnr)
    sid = b.src(s)
    if kind == "not_found":
        b.notes.append("Organisation number not found in Enhetsregisteret (neither as entity nor subunit).")
        return finish(b, kind)
    if not b.check(sid, data.get("organisasjonsnummer")):
        return finish(b, "identity_mismatch")
    _entity_facts(b, data, sid, kind)

    if kind == "underenhet" and data.get("overordnetEnhet"):
        parent, ps = brreg.fetch_parent(client, data["overordnetEnhet"])
        if parent:
            psid = b.src(ps)
            b.add("parent", {"orgnr": parent.get("organisasjonsnummer"), "name": parent.get("navn"),
                             "legal_form": (parent.get("organisasjonsform") or {}).get("kode")}, psid)
        b.notes.append(f"{orgnr} is a subunit (underenhet, e.g. a branch or business location). "
                       f"Roles and annual accounts are registered on the parent entity {data['overordnetEnhet']}.")

    if kind in ("enhet", "deleted"):
        roles, rs = brreg.fetch_roles(client, orgnr)
        rsid = b.src(rs)
        if roles is not None:
            _role_facts(b, roles, rsid)

    if kind == "enhet":
        subs, ss = brreg.fetch_subunits(client, orgnr)
        if subs is not None:
            ssid = b.src(ss)
            total = (subs.get("page") or {}).get("totalElements", 0)
            b.add("subunit_count", total, ssid)
            items = [{"orgnr": u.get("organisasjonsnummer"), "name": u.get("navn"),
                      "city": (u.get("beliggenhetsadresse") or {}).get("poststed"),
                      "employees": u.get("antallAnsatte")}
                     for u in (subs.get("_embedded") or {}).get("underenheter", [])
                     if u.get("overordnetEnhet") == orgnr]
            b.add("subunits", items, ssid, note=f"First {len(items)} of {total}" if total > len(items) else None)

    if kind in ("enhet", "deleted"):
        accounts, acs = brreg.fetch_accounts(client, orgnr)
        asid = b.src(acs)
        if accounts:
            _account_facts(b, accounts, asid)
        elif accounts == []:
            form = (b.facts.get("legal_form", {}).get("value") or {}).get("code")
            if form in ("ENK", "ANS", "DA", "FLI", "SAM", "ESEK", "KOMM", "STAT", "ORGL"):
                b.notes.append(f"No annual accounts in Regnskapsregisteret; legal form {form} is generally not "
                               "required to file accounts there.")
            else:
                b.notes.append("No annual accounts are published in Regnskapsregisteret for this entity "
                               "(newly founded, not yet filed, or exempt).")
    return finish(b, kind)


def enrich_web(client, profile):
    """Adds website-derived facts in place. Safe to skip when budget is short."""
    facts = profile["facts"]
    name = (facts.get("name") or {}).get("value", "")
    candidates = []
    reg = website.normalize_url((facts.get("website_registered") or {}).get("value"))
    if reg:
        candidates.append(("register", reg))
    email_dom = website.domain_from_email((facts.get("email") or {}).get("value"))
    if email_dom and not any(website.normalize_url(email_dom).split("//")[1].removeprefix("www.") in c[1] for c in candidates):
        candidates.append(("email_domain", email_dom))

    for origin, url in candidates:
        try:
            info = website.inspect_site(client, url, profile["orgnr"], name)
        except BudgetExceeded:
            raise
        except Exception as e:
            info = {"requested_url": url, "verification": f"error:{type(e).__name__}"}
        sid = f"website_{origin}"
        profile["sources"][sid] = {
            "id": sid, "name": f"Company website ({'listed in Enhetsregisteret' if origin == 'register' else 'domain of registered e-mail'})",
            "url": info.get("final_url") or url, "http_status": info.get("http_status"),
            "retrieved_at": brreg.now_iso(),
        }
        v = info["verification"]
        profile["identity_checks"].append({"source": sid, "method": v,
                                           "match": v in ("orgnr_on_site", "name_on_site")})
        if v in ("orgnr_on_site", "name_on_site"):
            fact = {"value": info.get("final_url") or url, "source": sid,
                    "verification": v, "found_via": origin}
            facts["website"] = fact
            for key, field in (("title", "website_title"), ("description", "website_description"),
                               ("social_profiles", "social_profiles")):
                if info.get(key):
                    facts[field] = {"value": info[key], "source": sid, "verification": v}
            break
        if origin == "register":
            profile["notes"].append(f"Registered website {url} could not be tied to the company ({v}); "
                                    "only the register entry is reported.")
    profile["summary"] = summarise(profile)
    return profile


def finish(b, kind):
    p = {
        "schema_version": SCHEMA_VERSION,
        "orgnr": b.orgnr,
        "lookup_result": kind,
        "generated_at": brreg.now_iso(),
        "facts": b.facts,
        "sources": b.sources,
        "identity_checks": b.checks,
        "notes": b.notes,
    }
    p["summary"] = summarise(p)
    return p


# ---- plain-language explanation --------------------------------------------------------

def _money(v, cur):
    if v is None:
        return None
    a = abs(v)
    if a >= 1e9:
        s = f"{v / 1e9:.1f} bn"
    elif a >= 1e6:
        s = f"{v / 1e6:.1f} m"
    elif a >= 1e3:
        s = f"{v / 1e3:.0f} k"
    else:
        s = f"{v:.0f}"
    return f"{cur or 'NOK'} {s}"


def _v(p, field):
    return (p["facts"].get(field) or {}).get("value")


def summarise(p):
    if p["lookup_result"] == "not_found":
        return f"No company with organisation number {p['orgnr']} exists in Enhetsregisteret."
    if p["lookup_result"] == "identity_mismatch":
        return "The register response did not match the requested organisation number; no facts were published."
    name = _v(p, "name") or p["orgnr"]
    lf = _v(p, "legal_form") or {}
    form = LEGAL_FORMS_EN.get(lf.get("code")) or (lf.get("description") or "").lower() or None
    muni = (_v(p, "municipality") or {}).get("name")
    s = [f"{name} (org. no. {p['orgnr']})"]
    form = form or "registered entity"
    first = (" is an " if form[0] in "aeiou" else " is a ") + form
    if muni:
        first += f" based in {muni.title()}"
    if _v(p, "founded_date") or _v(p, "registered_date"):
        first += f", founded {(_v(p, 'founded_date') or _v(p, 'registered_date'))[:4]}"
    s[0] += first + "."
    status = _v(p, "status")
    if status and status != "active":
        when = (p["facts"]["status"].get("as_of") or "")
        s.append(f"Status: {status.replace('_', ' ')}{' since ' + when if when else ''}.")
    parent = _v(p, "parent")
    if parent:
        s.append(f"It is a subunit of {parent.get('name')} ({parent.get('orgnr')}).")
    ind = _v(p, "primary_industry")
    if ind:
        s.append(f"Primary industry: {ind['description']} (NACE {ind['code']}).")
    emp = _v(p, "employees")
    if emp is not None:
        s.append(f"Registered employees: {emp}.")
    people = []
    for field, label in (("chief_executive", "CEO"), ("board_chair", "board chair"), ("owner", "owner")):
        v = _v(p, field)
        if isinstance(v, dict) and v.get("name"):
            people.append(f"{label} {v['name']}")
    if people:
        s.append("Key people: " + ", ".join(people) + ".")
    fin = _v(p, "financials")
    if fin:
        f = fin["figures"]
        parts = []
        if "revenue" in f:
            parts.append(f"revenue {_money(f['revenue'], fin['currency'])}")
        if "net_profit" in f:
            parts.append(f"net result {_money(f['net_profit'], fin['currency'])}")
        if "equity" in f:
            parts.append(f"equity {_money(f['equity'], fin['currency'])}")
        prev = _v(p, "financials_previous_year")
        trend = ""
        if prev and prev["figures"].get("revenue") and "revenue" in f and prev["currency"] == fin["currency"]:
            chg = (f["revenue"] - prev["figures"]["revenue"]) / abs(prev["figures"]["revenue"]) * 100
            trend = f" Revenue changed {chg:+.0f}% from {prev['fiscal_year']} (derived from the two filings)."
        if parts:
            s.append(f"Annual accounts {fin['fiscal_year']}: " + ", ".join(parts) + "." + trend)
    grp = _v(p, "group_financials")
    if grp and "revenue" in grp["figures"]:
        s.append(f"Group revenue {grp['fiscal_year']}: {_money(grp['figures']['revenue'], grp['currency'])}.")
    subs = _v(p, "subunit_count")
    if subs:
        s.append(f"It has {subs} registered subunit{'s' if subs != 1 else ''}.")
    web = p["facts"].get("website")
    if web:
        how = "org. no. found on site" if web.get("verification") == "orgnr_on_site" else "company name found on site"
        s.append(f"Website: {web['value']} ({how}).")
    return " ".join(s)
