"""Research one company: official registers -> verified website -> job ads."""
import time

from . import official, website
from .evidence import SECTIONS, Ledger
from .http import BudgetExceeded
from .jobs import html_to_text

NAV = "public_api"


class CompanyClient:
    """Per-company view of the shared client, counting this company's requests."""

    def __init__(self, client):
        self.c = client
        self.requests = 0

    def get(self, *a, **kw):
        self.requests += 1
        return self.c.get(*a, **kw)

    def __getattr__(self, name):
        return getattr(self.c, name)


def research_official(client, orgnr, bulk=None):
    t0 = time.monotonic()
    cc = CompanyClient(client)
    L = Ledger(orgnr)
    try:
        ctx = official.collect(cc, L, orgnr, bulk)
    except BudgetExceeded as e:
        ctx = {"orgnr": orgnr, "kind": "budget"}
        L.error("run_budget", e, retryable=True)
    except Exception as e:
        ctx = {"orgnr": orgnr, "kind": "error"}
        L.error("official_registers", f"{type(e).__name__}: {e}")
    ctx["_requests"] = cc.requests
    ctx["_ms"] = int((time.monotonic() - t0) * 1000)
    return L, ctx


def research_web(client, L, ctx, budget=16):
    t0 = time.monotonic()
    cc = CompanyClient(client)
    brand = None
    try:
        site, attempts, crawler = website.discover(cc, ctx, budget)
        checked = [f"{a.url} ({a.origin}: {a.verification})" for a in attempts]
        if site:
            brand = website.extract(L, site, ctx)
            ctx["site_domain"] = website.host_of(site.pages[0][1].final_url)
        else:
            states = [a.verification for a in attempts]
            prev = next((a for a in attempts if a.origin == "previously_verified"), None)
            decisive = ("parked_or_empty", "different_orgnr_on_site", "name_not_found", "partial_name_match",
                        "business_sports_club_mismatch", "no_distinctive_name_tokens")
            if prev and prev.verification not in decisive:
                L.section("website", "failed", f"The previously verified website {prev.url} could not be reached on this run "
                          f"({prev.verification}); the last verified value is kept and marked stale.", checked=checked)
                for s in ("social_profiles", "activity"):
                    L.section(s, "failed", "Website unreachable on this run.")
            elif any(a.origin != "name_guess" and (a.verification in ("unreachable",) or a.verification.startswith(("http_429", "http_5")))
                     for a in attempts):
                bad = next(a for a in attempts if a.origin != "name_guess" and (a.verification == "unreachable" or a.verification.startswith(("http_429", "http_5"))))
                L.section("website", "failed", f"The company's registered site {bad.url} was temporarily unavailable "
                          f"({bad.verification.replace('_', ' ')}); website facts could not be checked on this run.", checked=checked)
                for s in ("social_profiles", "activity"):
                    L.section(s, "failed", "Website temporarily unavailable on this run.")
            elif not attempts:
                L.section("website", "not_available", "No website in the register, no company e-mail domain and no plausible domain from the legal name.")
            elif any(a.verification == "blocked_by_robots_txt" and a.origin != "name_guess" for a in attempts) \
                    and not any(a.score >= 0.6 for a in attempts):
                L.section("website", "blocked", "Candidate website disallows automated access in robots.txt.", checked=checked)
            elif any(0.6 <= a.score < 0.9 for a in attempts):
                L.section("website", "ambiguous", "A candidate site partly matches the company but could not be tied to the exact legal "
                          "entity (no org. no. and incomplete name match); nothing from it is published.", checked=checked)
            else:
                L.section("website", "not_available", "No candidate website could be verified as belonging to this legal entity.",
                          checked=checked)
            reg = next((a for a in attempts if a.origin == "registry"), None)
            if reg and "website" in L.sections:
                L.sections["website"]["reason"] += (f" The register-listed site {reg.url} was checked: {reg.verification.replace('_', ' ')}"
                                                    + (f" ({reg.reasons[0]})" if reg.reasons else "") + ".")
            for s in ("social_profiles", "activity"):
                if s not in L.sections:
                    L.section(s, "not_available", "No verified company website to source this from.")
    except BudgetExceeded:
        L.section("website", "failed", "Run budget exhausted before the website could be checked.")
    except Exception as e:
        L.section("website", "failed", f"Website check failed: {type(e).__name__}")
        L.error("website", f"{type(e).__name__}: {e}")
    ctx["brand"] = brand
    ctx["_requests"] = ctx.get("_requests", 0) + cc.requests
    ctx["_ms"] = ctx.get("_ms", 0) + int((time.monotonic() - t0) * 1000)


def research_jobs(client, L, ctx, index, max_details=6):
    t0 = time.monotonic()
    cc = CompanyClient(client)
    allowed = {ctx["orgnr"]} | set(ctx.get("subunit_orgnrs") or ())
    names = [n for n in [ctx.get("name"), ctx.get("brand")] + list(ctx.get("names") or [])[1:6] if n]
    found = 0
    try:
        exact = ctx.get("nav_ads") or []
        cands = (exact + [a for a in index.candidates(names) if a not in exact])[:max_details + len(exact[:10])]
        for ad in cands[:16]:
            r, d = index.detail(cc, ad["uuid"])
            if not d:
                continue
            a = d.get("ad_content") or {}
            emp = a.get("employer") or {}
            if emp.get("orgnr") not in allowed:
                continue
            exp = (a.get("expires") or "")[:10]
            if exp and exp < time.strftime("%Y-%m-%d"):
                continue
            loc = ", ".join(sorted({(w.get("city") or w.get("municipal") or "").title() for w in a.get("workLocations") or [] if w}))
            ev = [L.json_evidence(r, d, "/ad_content/employer/orgnr", NAV), L.json_evidence(r, d, "/ad_content/title", NAV)]
            L.claim("jobs", "job_posting", {
                "title": a.get("title"), "url": a.get("link") or f"https://arbeidsplassen.nav.no/stillinger/stilling/{ad['uuid']}",
                "source": "arbeidsplassen.nav.no", "employer_orgnr": emp.get("orgnr"), "employer_name": emp.get("name"),
                "published": (a.get("published") or "")[:10] or None, "expires": exp or None,
                "application_due": a.get("applicationDue"), "location": loc or None, "positions": a.get("positioncount"),
                "extent": a.get("extent"), "engagement_type": a.get("engagementtype"),
                "category": ((a.get("occupationCategories") or [{}])[0] or {}).get("level1"),
                "summary": html_to_text(a.get("description"), 240) or None, "status": "active",
            }, ev, confidence=1.0, key=ad["uuid"], effective_date=(a.get("published") or "")[:10] or None,
                note="Employer organisation number in the ad matches this company" +
                     (" (registered subunit)" if emp.get("orgnr") != ctx["orgnr"] else ""))
            found += 1
    except BudgetExceeded:
        L.section("jobs", "failed", "Run budget exhausted before job ads could be checked.")
    except Exception as e:
        L.error("nav_jobs", f"{type(e).__name__}: {e}")
        L.section("jobs", "failed", "NAV job feed lookup failed.")
    if found:
        L.claim("jobs", "active_job_ads", found, [c["evidence_ids"][0] for c in L.claims if c["field"] == "job_posting"
                                                 and c["value"].get("source") == "arbeidsplassen.nav.no"][:1],
                note="Active ads on arbeidsplassen.nav.no whose employer org. no. is this company or its subunit")
    elif "jobs" not in L.sections:
        info = index.sync_info
        L.section("jobs", "not_available",
                  f"No active job ads found for this employer in NAV's public job feed (index of "
                  f"{info.get('active_ads', len(index.ads))} active ads, synced to {info.get('synced_until') or index.synced_until})"
                  " or on a verified company website.")
    ctx["_requests"] = ctx.get("_requests", 0) + cc.requests
    ctx["_ms"] = ctx.get("_ms", 0) + int((time.monotonic() - t0) * 1000)


ABSENT_FIELD = {"financials": "annual_accounts_filing", "leadership": "chief_executive", "locations": "registered_workplace",
                "group": "parent_company", "website": "official_website", "social_profiles": "social_profile",
                "contact": "contact_details", "jobs": "job_posting", "activity": "company_news"}


def finalize_sections(L, ctx):
    for name, _ in SECTIONS:
        if name not in L.sections:
            if ctx.get("kind") in ("not_found", "identity_mismatch", "error", "budget", "invalid"):
                L.section(name, "failed" if ctx.get("kind") in ("error", "budget") else "not_applicable",
                          "Not researched because the legal identity could not be established.")
            else:
                L.section(name, "not_available", "No supported information found in the checked sources.")
    # explicit, checked absences as claims (a checked source with nothing is not the same as unchecked)
    for name, field in ABSENT_FIELD.items():
        st = L.sections.get(name) or {}
        if st.get("state") != "available" and ctx.get("kind") in ("enhet", "underenhet", "deleted"):
            c = L.absent(name, field, st.get("state", "not_available"), st.get("reason") or "Not found in the checked sources.")
            if c and st.get("sources_checked"):
                c["sources_checked"] = st["sources_checked"]
