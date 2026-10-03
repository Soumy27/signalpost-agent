"""Official-register claims: identity, accounts, roles, workplaces and group.

All values are read verbatim from Brønnøysund JSON responses, each with a JSON
pointer span. Financial figures are never computed, rounded or filled in.
"""
import json
from pathlib import Path

from . import brreg
from .http import Response
from .evidence import norm_key

REG, ACC = "official_registry", "official_accounts"

ROLE_FIELDS = {
    "DAGL": "chief_executive", "LEDE": "board_chair", "NEST": "board_deputy_chair", "MEDL": "board_member",
    "VARA": "board_deputy_member", "OBS": "board_observer", "REVI": "auditor", "REGN": "accountant",
    "INNH": "owner", "DTPR": "partner", "DTSO": "partner", "KOMP": "general_partner", "KONT": "contact_person",
    "FFØR": "business_manager", "REPR": "norwegian_representative", "KDEB": "bankruptcy_trustee",
    "BEST": "managing_shipowner", "SIGN": "signatory", "PROK": "procuration_holder", "HFOR": "head_office_link",
    "DTSO_": "partner", "STYR": "board_member", "FISJ": "demerger_party", "FUSJ": "merger_party",
}

LEGAL_FORMS_EN = {
    "AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
    "NUF": "Norwegian-registered foreign enterprise", "ANS": "general partnership",
    "DA": "partnership with shared liability", "SA": "cooperative", "STI": "foundation",
    "FLI": "association", "BA": "company with limited liability", "KS": "limited partnership",
    "IKS": "inter-municipal company", "KF": "municipal enterprise", "SF": "state-owned enterprise",
    "ORGL": "organisational unit", "SPA": "savings bank", "BRL": "housing cooperative", "BBL": "housing builders' cooperative",
    "ESEK": "condominium", "BEDR": "business subunit", "AAFY": "subunit", "SAM": "jointly owned property",
    "PRE": "shipping partnership", "GFS": "mutual insurance company", "PK": "pension fund",
    "VPFO": "securities fund", "ANNA": "other legal entity", "SÆR": "special-legislation company", "SE": "European company",
    "KIRK": "church body", "UTLA": "foreign entity",
}

METRICS = [
    ("revenue", "/resultatregnskapResultat/driftsresultat/driftsinntekter/sumDriftsinntekter"),
    ("sales_revenue", "/resultatregnskapResultat/driftsresultat/driftsinntekter/salgsinntekter"),
    ("operating_expenses", "/resultatregnskapResultat/driftsresultat/driftskostnad/sumDriftskostnad"),
    ("payroll_expenses", "/resultatregnskapResultat/driftsresultat/driftskostnad/loennskostnad"),
    ("operating_profit", "/resultatregnskapResultat/driftsresultat/driftsresultat"),
    ("net_financial_items", "/resultatregnskapResultat/finansresultat/nettoFinans"),
    ("profit_before_tax", "/resultatregnskapResultat/ordinaertResultatFoerSkattekostnad"),
    ("tax_expense", "/resultatregnskapResultat/ordinaertResultatSkattekostnad"),
    ("net_profit", "/resultatregnskapResultat/aarsresultat"),
    ("total_assets", "/eiendeler/sumEiendeler"),
    ("fixed_assets", "/eiendeler/anleggsmidler/sumAnleggsmidler"),
    ("current_assets", "/eiendeler/omloepsmidler/sumOmloepsmidler"),
    ("cash_and_bank", "/eiendeler/sumBankinnskuddOgKontanter"),
    ("equity", "/egenkapitalGjeld/egenkapital/sumEgenkapital"),
    ("total_liabilities", "/egenkapitalGjeld/gjeldOversikt/sumGjeld"),
    ("short_term_liabilities", "/egenkapitalGjeld/gjeldOversikt/kortsiktigGjeld/sumKortsiktigGjeld"),
    ("long_term_liabilities", "/egenkapitalGjeld/gjeldOversikt/langsiktigGjeld/sumLangsiktigGjeld"),
]


_NACE_EN = None


def nace_en(code):
    global _NACE_EN
    if _NACE_EN is None:
        try:
            _NACE_EN = json.loads((Path(__file__).parent / "data" / "nace_en.json").read_text(encoding="utf-8"))["codes"]
        except (OSError, ValueError, KeyError):
            _NACE_EN = {}
    return _NACE_EN.get(code) or _NACE_EN.get((code or "")[:5])


def _d(s):
    return s[:10] if isinstance(s, str) and s else None


def _get(d, pointer):
    for p in [x for x in pointer.split("/") if x]:
        if isinstance(d, list):
            try:
                d = d[int(p)]
            except (ValueError, IndexError):
                return None
        elif isinstance(d, dict):
            d = d.get(p)
        else:
            return None
    return d


def _addr(a):
    if not a:
        return None
    lines = [x for x in (a.get("adresse") or []) if x]
    city = " ".join(x for x in (a.get("postnummer"), a.get("poststed")) if x)
    text = ", ".join(lines + ([city] if city else []) + ([a["land"]] if a.get("land") and a.get("landkode") != "NO" else []))
    return {"text": text, "street": lines, "postal_code": a.get("postnummer"), "city": a.get("poststed"),
            "municipality": a.get("kommune"), "municipality_number": a.get("kommunenummer"),
            "country_code": a.get("landkode")}


def collect(client, L, orgnr, bulk=None):
    """Fill ledger L with official claims. Returns a context dict used by the
    web and jobs modules (identity anchors)."""
    ctx = {"orgnr": orgnr, "kind": None, "names": [], "subunit_orgnrs": set(), "people": [],
           "phones": [], "emails": [], "address": None, "postal_codes": set()}
    try:
        kind, data, r = brreg.fetch_entity(client, orgnr)
    except Exception as e:
        if not bulk or orgnr not in bulk[0]:
            raise
        # live register unavailable: anchor identity on the frozen registry snapshot
        data = bulk[0][orgnr]
        r = Response(brreg.BULK_URL, 200, json.dumps(data, ensure_ascii=False, sort_keys=True).encode())
        kind = "enhet"
        L.error("brreg_entity", f"Live entity lookup failed ({type(e).__name__}: {e}); identity taken from the frozen "
                f"registry snapshot sha256={bulk[1]}", retryable=True)
    ctx["kind"] = kind
    if kind == "not_found":
        L.section("identity", "not_available", "Organisation number not found in Enhetsregisteret (entity or subunit).",
                  checked=[r.url])
        return ctx
    if data.get("organisasjonsnummer") != orgnr:
        L.section("identity", "failed", "Register response did not match the requested organisation number.", force=True)
        ctx["kind"] = "identity_mismatch"
        return ctx

    def c(section, field, pointer, value=None, transform=None, **kw):
        raw = _get(data, pointer)
        if raw is None or raw == [] or raw == "":
            return None
        v = value if value is not None else (transform(raw) if transform else raw)
        return L.claim(section, field, v, [L.json_evidence(r, data, pointer, REG)], **kw)

    reg_date = _d(data.get("registreringsdatoEnhetsregisteret"))
    c("identity", "organisation_number", "/organisasjonsnummer")
    c("identity", "legal_name", "/navn")
    ctx["names"].append(data.get("navn"))
    form = data.get("organisasjonsform") or {}
    c("identity", "legal_form", "/organisasjonsform",
      value={"code": form.get("kode"), "description": form.get("beskrivelse"), "english": LEGAL_FORMS_EN.get(form.get("kode"))})
    c("identity", "register_unit_type", "/respons_klasse" if data.get("respons_klasse") else "/organisasjonsnummer",
      value="subunit (underenhet)" if kind == "underenhet" else "main entity (enhet)")

    status, sptr = "active", "/organisasjonsnummer"
    for flag, label, dptr in (("slettedato", "deleted", "/slettedato"), ("konkurs", "bankrupt", "/konkursdato"),
                              ("underTvangsavviklingEllerTvangsopplosning", "under_forced_liquidation", "/tvangsavvikletPgaManglendeSlettingDato"),
                              ("underAvvikling", "under_liquidation", "/underAvviklingDato"),
                              ("nedleggelsesdato", "closed", "/nedleggelsesdato")):
        if data.get(flag):
            status, sptr = label, "/" + flag
            break
    L.claim("identity", "registry_status", status, [L.json_evidence(r, data, sptr, REG)],
            effective_date=_d(data.get("slettedato") or data.get("konkursdato") or data.get("underAvviklingDato") or data.get("nedleggelsesdato")),
            note=None if sptr != "/organisasjonsnummer" else "No bankruptcy, liquidation or deletion flag in the register")
    for field, ptr in (("deletion_date", "/slettedato"), ("bankruptcy_date", "/konkursdato"),
                       ("liquidation_date", "/underAvviklingDato"), ("founding_date", "/stiftelsesdato"),
                       ("start_date", "/oppstartsdato"), ("registration_date", "/registreringsdatoEnhetsregisteret"),
                       ("business_register_date", "/registreringsdatoForetaksregisteret"),
                       ("articles_of_association_date", "/vedtektsdato"), ("closed_date", "/nedleggelsesdato")):
        c("identity", field, ptr, transform=_d)

    for field, ptr in (("registered_business_address", "/forretningsadresse"), ("registered_location_address", "/beliggenhetsadresse"),
                       ("postal_address", "/postadresse")):
        a = _addr(data.get(ptr[1:]))
        if a:
            c("identity" if field != "registered_location_address" else "locations", field, ptr, value=a)
    main = data.get("forretningsadresse") or data.get("beliggenhetsadresse") or {}
    ctx["address"] = main
    for a in (data.get("forretningsadresse"), data.get("beliggenhetsadresse"), data.get("postadresse")):
        if a and a.get("postnummer"):
            ctx["postal_codes"].add(a["postnummer"])
    if main.get("kommune"):
        ptr = "/forretningsadresse/kommune" if data.get("forretningsadresse") else "/beliggenhetsadresse/kommune"
        c("identity", "municipality", ptr, value={"name": main.get("kommune"), "number": main.get("kommunenummer")})

    for i, k in enumerate(("naeringskode1", "naeringskode2", "naeringskode3")):
        nk = data.get(k)
        if nk:
            c("identity", "industry", f"/{k}", value={"code": nk.get("kode"), "description": nk.get("beskrivelse"),
                                                     "description_en": nace_en(nk.get("kode")), "rank": i + 1},
              key=nk.get("kode"))
    sec = data.get("institusjonellSektorkode")
    if sec:
        c("identity", "institutional_sector", "/institusjonellSektorkode", value={"code": sec.get("kode"), "description": sec.get("beskrivelse")})

    if data.get("antallAnsatte") is not None and data.get("harRegistrertAntallAnsatte") is not False:
        c("identity", "registered_employees", "/antallAnsatte",
          effective_date=_d(data.get("registreringsdatoAntallAnsatteNAVAaregisteret") or data.get("registreringsdatoAntallAnsatteEnhetsregisteret")),
          note="Employee count reported to NAV Aa-registeret, as shown in Enhetsregisteret")
    elif data.get("harRegistrertAntallAnsatte") is False:
        L.absent("identity", "registered_employees", "not_available",
                 "The register states no employee count is registered; this is not reported as zero.",
                 [L.json_evidence(r, data, "/harRegistrertAntallAnsatte", REG)])

    k = data.get("kapital")
    if k:
        c("identity", "share_capital", "/kapital", value={"amount": k.get("belop"), "currency": k.get("valuta"),
                                                          "shares": k.get("antallAksjer"), "type": k.get("type")},
          effective_date=_d(k.get("innfortDato")))
    if data.get("vedtektsfestetFormaal"):
        c("identity", "statutory_purpose", "/vedtektsfestetFormaal", transform=lambda v: " ".join(v))
    if data.get("aktivitet"):
        c("identity", "registered_activity", "/aktivitet", transform=lambda v: " ".join(v))
    for field, ptr in (("vat_registered", "/registrertIMvaregisteret"), ("in_business_register", "/registrertIForetaksregisteret"),
                       ("in_voluntary_register", "/registrertIFrivillighetsregisteret"),
                       ("in_foundation_register", "/registrertIStiftelsesregisteret"),
                       ("part_of_group", "/erIKonsern"), ("latest_filed_accounts_year", "/sisteInnsendteAarsregnskap"),
                       ("language_form", "/maalform")):
        c("identity", field, ptr)
    c("identity", "vat_registration_date", "/registreringsdatoMerverdiavgiftsregisteret", transform=_d)
    for i, h in enumerate(data.get("historiskeNavn") or []):
        c("identity", "former_name", f"/historiskeNavn/{i}", key=h.get("navn"),
          value={"name": h.get("navn"), "from": _d(h.get("fraDato")), "to": _d(h.get("tilDato"))})
        ctx["names"].append(h.get("navn"))
    if data.get("hjemmeside"):
        c("identity", "registry_website", "/hjemmeside")
        ctx["registry_website"] = data["hjemmeside"]
    for field, ptr in (("registry_email", "/epostadresse"), ("registry_phone", "/telefon"), ("registry_mobile", "/mobil")):
        if c("contact", field, ptr):
            (ctx["emails"] if "email" in field else ctx["phones"]).append(data[ptr[1:]])
    ctx["legal_form"] = form.get("kode")
    ctx["name"] = data.get("navn")
    ctx["industry"] = (data.get("naeringskode1") or {}).get("beskrivelse")
    ctx["in_group"] = bool(data.get("erIKonsern"))
    ctx["latest_year"] = data.get("sisteInnsendteAarsregnskap")
    L.section("identity", "available")

    if kind == "underenhet":
        parent = data.get("overordnetEnhet")
        if parent:
            pr, pdata = brreg.fetch_parent(client, parent)
            ev = [L.json_evidence(r, data, "/overordnetEnhet", REG)]
            val = {"orgnr": parent, "relationship": "parent entity of this subunit"}
            if pdata and pdata.get("organisasjonsnummer") == parent:
                ev.append(L.json_evidence(pr, pdata, "/navn", REG))
                val["name"] = pdata.get("navn")
            L.claim("group", "parent_entity", val, ev, key=parent)
        for s in ("financials", "leadership"):
            L.section(s, "not_applicable", "This number is a subunit; accounts and roles are registered on the parent entity.")
        L.section("locations", "available")
        return ctx

    if kind == "deleted":
        L.section("financials", "not_applicable", "Entity is deleted from the register.")

    _guard(L, "leadership", "brreg_roles", _roles, client, L, orgnr, ctx)
    if kind == "deleted":
        return ctx
    _guard(L, "locations", "brreg_subunits", _subunits, client, L, orgnr, ctx)
    _guard(L, "financials", "brreg_accounts", _accounts_module, client, L, orgnr, ctx)
    if ctx["in_group"]:
        _guard(L, "group", "brreg_group", _group_module, client, L, orgnr, ctx)
    else:
        L.section("group", "not_applicable", "The register does not mark this entity as part of a group (erIKonsern=false).")
    return ctx


def _guard(L, section, source, fn, *args):
    """A failing secondary lookup fails only its own section, never the company."""
    from .http import BudgetExceeded
    try:
        fn(*args)
    except BudgetExceeded:
        raise
    except Exception as e:
        L.section(section, "failed", f"{source} lookup failed: {type(e).__name__}", force=True)
        L.error(source, f"{type(e).__name__}: {e}")


def _roles(client, L, orgnr, ctx):
    rr, roles = brreg.fetch_roles(client, orgnr)
    if roles is None:
        if rr.status in (404, 410):
            L.section("leadership", "not_available", f"Roles endpoint returned HTTP {rr.status}.", checked=[rr.url])
        else:
            L.section("leadership", "failed", f"Roles endpoint returned HTTP {rr.status}.", checked=[rr.url])
            L.error("brreg_roles", f"HTTP {rr.status}", rr.url)
    else:
        for gi, group in enumerate(roles.get("rollegrupper") or []):
            gcode = (group.get("type") or {}).get("kode")
            for ri, role in enumerate(group.get("roller") or []):
                if role.get("avregistrert") or role.get("fratraadt"):
                    continue
                rtype = role.get("type") or {}
                field = ROLE_FIELDS.get(rtype.get("kode")) or ROLE_FIELDS.get(gcode) or "other_role"
                base = f"/rollegrupper/{gi}/roller/{ri}"
                if role.get("person"):
                    n = role["person"].get("navn") or {}
                    name = " ".join(x for x in (n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")) if x)
                    val = {"name": name, "type": "person", "role": rtype.get("beskrivelse"), "role_code": rtype.get("kode")}
                    if role["person"].get("erDoed"):
                        val["deceased"] = True
                    ptr = base + "/person/navn"
                    ctx["people"].append(name)
                elif role.get("enhet"):
                    e = role["enhet"]
                    nm = " ".join(e.get("navn")) if isinstance(e.get("navn"), list) else e.get("navn")
                    val = {"name": nm, "type": "organisation", "orgnr": e.get("organisasjonsnummer"),
                           "role": rtype.get("beskrivelse"), "role_code": rtype.get("kode")}
                    ptr = base + "/enhet"
                else:
                    continue
                L.claim("leadership", field, val, [L.json_evidence(rr, roles, ptr, REG)],
                        key=val.get("orgnr") or val["name"], effective_date=_d(group.get("sistEndret")))
        if not any(c["section"] == "leadership" for c in L.claims):
            L.section("leadership", "not_available", "No current role holders are registered.", checked=[rr.url])



def _subunits(client, L, orgnr, ctx):
    sr, subs = brreg.fetch_subunits(client, orgnr)
    if subs is None:
        L.section("locations", "failed", f"Subunit search returned HTTP {sr.status}.")
        L.error("brreg_subunits", f"HTTP {sr.status}", sr.url)
        return
    total = (subs.get("page") or {}).get("totalElements", 0)
    L.claim("locations", "registered_workplace_count", total, [L.json_evidence(sr, subs, "/page/totalElements", REG)])
    for i, u in enumerate((subs.get("_embedded") or {}).get("underenheter", [])):
        if u.get("overordnetEnhet") != orgnr:
            continue
        ctx["subunit_orgnrs"].add(u.get("organisasjonsnummer"))
        if u.get("navn"):
            ctx["names"].append(u["navn"])
        a = _addr(u.get("beliggenhetsadresse"))
        L.claim("locations", "registered_workplace", {
            "orgnr": u.get("organisasjonsnummer"), "name": u.get("navn"), "address": a["text"] if a else None,
            "municipality": (u.get("beliggenhetsadresse") or {}).get("kommune"),
            "employees": u.get("antallAnsatte") if u.get("harRegistrertAntallAnsatte") is not False else None,
            "industry": (u.get("naeringskode1") or {}).get("beskrivelse"),
            "start_date": _d(u.get("oppstartsdato")),
        }, [L.json_evidence(sr, subs, f"/_embedded/underenheter/{i}", REG)], key=u.get("organisasjonsnummer"))
    if total == 0:
        L.section("locations", "available")


def _accounts_module(client, L, orgnr, ctx):
    ar, accounts = brreg.fetch_accounts(client, orgnr)
    if accounts is None and ar.status == 404:
        L.section("financials", "not_available",
                  "Regnskapsregisteret returned no normalised accounts for this entity (no record is not the same as zero).",
                  checked=[ar.url])
    elif accounts is None:
        L.section("financials", "failed", f"Regnskapsregisteret returned HTTP {ar.status}.", checked=[ar.url])
        L.error("brreg_accounts", f"HTTP {ar.status}", ar.url)
    else:
        rows = accounts if isinstance(accounts, list) else [accounts]
        _accounts(L, ar, accounts, rows, orgnr, ctx)


def _group_module(client, L, orgnr, ctx):
    gr, tree = brreg.fetch_group(client, orgnr)
    if tree is None:
        L.section("group", "not_available" if gr.status == 404 else "failed",
                  f"Group structure endpoint returned HTTP {gr.status}.", checked=[gr.url])
    else:
        _group(L, gr, tree, orgnr)


def _accounts(L, ar, raw, rows, orgnr, ctx):
    prefix = "" if isinstance(raw, dict) else None
    usable = []
    for i, row in enumerate(rows):
        base = prefix if prefix is not None else f"/{i}"
        if str(_get(row, "/virksomhet/organisasjonsnummer")) != orgnr:
            L.error("brreg_accounts", f"Discarded account row for {_get(row, '/virksomhet/organisasjonsnummer')}", ar.url, False)
            continue
        per = row.get("regnskapsperiode") or {}
        if per.get("tilDato"):
            usable.append((base, row, {"start": per.get("fraDato"), "end": per.get("tilDato")}))
    if not usable:
        L.section("financials", "not_available", "No usable account rows for this organisation number.", checked=[ar.url])
        return
    for stype in ("SELSKAP", "KONSERN"):
        mine = sorted((u for u in usable if u[1].get("regnskapstype") == stype), key=lambda u: u[2]["end"], reverse=True)
        for rank, (base, row, period) in enumerate(mine):
            year = period["end"][:4]
            scope = "company" if stype == "SELSKAP" else "group"
            cur = row.get("valuta")
            L.claim("financials", "annual_accounts_filing", {
                "fiscal_year": int(year), "scope": scope, "currency": cur, "journal_number": row.get("journalnr"),
                "accounting_rules": _get(row, "/regnkapsprinsipper/regnskapsregler"),
                "small_company": _get(row, "/regnkapsprinsipper/smaaForetak"),
                "audited": None if not row.get("revisjon") else not (_get(row, "/revisjon/ikkeRevidertAarsregnskap") or _get(row, "/revisjon/fravalgRevisjon")),
                "liquidation_accounts": row.get("avviklingsregnskap"), "latest": rank == 0,
            }, [L.json_evidence(ar, raw, base + "/journalnr", ACC, period=period)], key=f"{scope}:{year}", period=period)
            for field, ptr in METRICS:
                v = _get(row, ptr)
                if v is None:
                    continue
                L.claim("financials", field, {"amount": v, "currency": cur, "scope": scope, "fiscal_year": int(year)},
                        [L.json_evidence(ar, raw, base + ptr, ACC, period=period)], key=f"{scope}:{year}", period=period,
                        note="Copied verbatim from the filed annual accounts")
    L.section("financials", "available")


def _group(L, gr, tree, orgnr):
    found = {}

    def walk(node, path, parent):
        if node.get("organisasjonsnummer") == orgnr and "node" not in found:
            found.update(node=node, path=path, parent=parent)
        for i, ch in enumerate(node.get("children") or []):
            walk(ch, f"{path}/children/{i}", node)

    walk(tree, "", None)
    if tree.get("organisasjonsnummer") and tree.get("organisasjonsnummer") != orgnr:
        L.claim("group", "ultimate_parent", {"orgnr": tree.get("organisasjonsnummer"), "name": tree.get("navn"),
                                             "relationship": "top entity of the registered group structure"},
                [L.json_evidence(gr, tree, "/navn", REG)], key=tree.get("organisasjonsnummer"))
    node = found.get("node")
    if node and node.get("parentOrganisasjonsnummer"):
        L.claim("group", "parent_company", {"orgnr": node["parentOrganisasjonsnummer"], "name": node.get("parentNavn"),
                                            "relationship": (node.get("knytningsform") or {}).get("beskrivelse"),
                                            "ownership_basis": node.get("grunnlag")},
                [L.json_evidence(gr, tree, found["path"] + "/parentOrganisasjonsnummer", REG)],
                key=node["parentOrganisasjonsnummer"], effective_date=node.get("dato"))
    children = (node or {}).get("children") or []
    for i, ch in enumerate(children):
        L.claim("group", "subsidiary", {"orgnr": ch.get("organisasjonsnummer"), "name": ch.get("navn"),
                                        "relationship": (ch.get("knytningsform") or {}).get("beskrivelse"),
                                        "ownership_basis": ch.get("grunnlag")},
                [L.json_evidence(gr, tree, f"{found['path']}/children/{i}", REG)], key=ch.get("organisasjonsnummer"),
                effective_date=ch.get("dato"))

    def count(n):
        return 1 + sum(count(ch) for ch in n.get("children") or [])

    L.claim("group", "group_entity_count", count(tree), [L.json_evidence(gr, tree, "/organisasjonsnummer", REG)],
            note="Number of entities in the registered group structure, including the top entity")
    if not node:
        L.section("group", "available")
