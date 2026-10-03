"""Offline tests (no network): python3 -m unittest discover tests"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from signalpost import official, orgnr, runner, store, website
from signalpost.evidence import STATES, Ledger
from signalpost.http import Response

ER = "https://data.brreg.no/enhetsregisteret/api"
RR = "https://data.brreg.no/regnskapsregisteret/regnskap"
ORG = "997874234"


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.count = 0

    def get(self, url, **kw):
        self.count += 1
        status, body = self.routes.get(url, (404, b""))
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        return Response(url, status, body, url, {"Content-Type": "text/html; charset=utf-8"})

    def time_left(self):
        return 1e9

    remaining = 10 ** 6


def entity(**over):
    d = {"organisasjonsnummer": ORG, "navn": "ANNI&CO AS", "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
         "registreringsdatoEnhetsregisteret": "2012-01-10", "stiftelsesdato": "2011-12-20",
         "forretningsadresse": {"adresse": ["Storgata 1"], "postnummer": "2050", "poststed": "JESSHEIM", "kommune": "ULLENSAKER",
                                "kommunenummer": "3209", "landkode": "NO", "land": "Norge"},
         "naeringskode1": {"kode": "47.710", "beskrivelse": "Detaljhandel med klær"}, "antallAnsatte": 7,
         "harRegistrertAntallAnsatte": True, "hjemmeside": "www.anniogco.no", "telefon": "63 96 57 30", "konkurs": False,
         "erIKonsern": False, "sisteInnsendteAarsregnskap": "2025"}
    d.update(over)
    return d


ROLES = {"rollegrupper": [
    {"sistEndret": "2020-01-01", "type": {"kode": "DAGL"}, "roller": [
        {"type": {"kode": "DAGL", "beskrivelse": "Daglig leder"}, "person": {"navn": {"fornavn": "Kari", "etternavn": "Nordmann"}}}]},
    {"sistEndret": "2021-01-01", "type": {"kode": "STYR"}, "roller": [
        {"type": {"kode": "LEDE", "beskrivelse": "Styrets leder"}, "person": {"navn": {"fornavn": "Ola", "etternavn": "Nordmann"}}},
        {"type": {"kode": "MEDL", "beskrivelse": "Styremedlem"}, "person": {"navn": {"fornavn": "Gammel", "etternavn": "Person"}},
         "avregistrert": True}]}]}
ACCOUNTS = [
    {"regnskapstype": "SELSKAP", "journalnr": "1", "virksomhet": {"organisasjonsnummer": ORG}, "valuta": "NOK",
     "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
     "resultatregnskapResultat": {"aarsresultat": 368000.0, "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 11200000.0}}},
     "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": 1700000.0}}},
    {"regnskapstype": "SELSKAP", "journalnr": "2", "virksomhet": {"organisasjonsnummer": "999999999"}, "valuta": "NOK",
     "regnskapsperiode": {"fraDato": "2026-01-01", "tilDato": "2026-12-31"}, "resultatregnskapResultat": {"aarsresultat": 1.0}}]


def routes(ent=None, roles=ROLES, accounts=ACCOUNTS):
    return {
        f"{ER}/enheter/{ORG}": (200, ent or entity()),
        f"{ER}/enheter/{ORG}/roller": (200, roles),
        f"{ER}/underenheter?overordnetEnhet={ORG}&size=50": (200, {"page": {"totalElements": 0}}),
        f"{RR}/{ORG}": (200, accounts),
    }


def collect(r):
    L = Ledger(ORG)
    ctx = official.collect(FakeClient(r), L, ORG)
    return L, ctx


class OrgnrTests(unittest.TestCase):
    def test_mod11(self):
        self.assertTrue(orgnr.is_valid("923609016"))
        self.assertFalse(orgnr.is_valid("923609017"))

    def test_pattern(self):
        self.assertTrue(orgnr.pattern("923609016").search("Org.nr: 923 609 016 MVA"))
        self.assertFalse(orgnr.pattern("923609016").search("19236090161"))


class OutboundPolicyTests(unittest.TestCase):
    def test_private_and_odd_urls_blocked(self):
        from signalpost.http import UnsafeURL, check_url
        for bad in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://localhost:80/",
                    "file:///etc/passwd", "http://user:pw@example.com/", "http://10.0.0.5/", "https://example.com:6379/"):
            with self.assertRaises(UnsafeURL, msg=bad):
                check_url(bad)


class OfficialTests(unittest.TestCase):
    def test_claims_have_pointer_evidence(self):
        L, ctx = collect(routes())
        ev = {e["id"]: e for e in L.evidence}
        for c in L.claims:
            if c["availability"] == "available":
                self.assertTrue(c["evidence_ids"])
                for i in c["evidence_ids"]:
                    self.assertTrue(ev[i]["content_sha256"] and ev[i]["retrieved_at"] and ev[i]["claim_span"])
        self.assertEqual(L.first("legal_name"), "ANNI&CO AS")
        self.assertEqual(L.first("chief_executive")["name"], "Kari Nordmann")
        self.assertEqual(L.values("board_member"), [])  # deregistered member excluded

    def test_financials_verbatim_with_period(self):
        L, _ = collect(routes())
        rev = [c for c in L.claims if c["field"] == "revenue"]
        self.assertEqual(len(rev), 1)
        self.assertEqual(rev[0]["value"]["amount"], 11200000.0)
        self.assertEqual(rev[0]["reporting_period"], {"start": "2025-01-01", "end": "2025-12-31"})
        # the row filed under another org. no. is discarded, not published
        self.assertFalse(any(c["value"].get("fiscal_year") == 2026 for c in L.claims if isinstance(c["value"], dict)))

    def test_no_employee_count_is_not_zero(self):
        L, _ = collect(routes(entity(antallAnsatte=None, harRegistrertAntallAnsatte=False)))
        c = next(c for c in L.claims if c["field"] == "registered_employees")
        self.assertEqual(c["availability"], "not_available")
        self.assertIsNone(c["value"])

    def test_identity_mismatch_publishes_nothing(self):
        L, ctx = collect(routes(entity(organisasjonsnummer="923609016")))
        self.assertEqual(ctx["kind"], "identity_mismatch")
        self.assertEqual(L.claims, [])

    def test_snapshot_fallback_when_live_register_fails(self):
        class Down(FakeClient):
            def get(self, url, **kw):
                if url == f"{ER}/enheter/{ORG}":
                    raise TimeoutError("simulated outage")
                return super().get(url, **kw)
        row = {"organisasjonsnummer": ORG, "navn": "ANNI&CO AS", "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
               "forretningsadresse": {"adresse": ["Storgata 1"], "postnummer": "2050", "poststed": "JESSHEIM", "kommune": "ULLENSAKER"}}
        L = Ledger(ORG)
        ctx = official.collect(Down(routes()), L, ORG, ({ORG: row}, "abc123"))
        self.assertEqual(ctx["kind"], "enhet")
        self.assertEqual(L.first("legal_name"), "ANNI&CO AS")
        self.assertTrue(any("frozen registry snapshot" in e["error"] for e in L.errors))

    def test_missing_accounts_is_explicit(self):
        r = routes()
        del r[f"{RR}/{ORG}"]
        L, _ = collect(r)
        self.assertEqual(L.sections["financials"]["state"], "not_available")


CTX = {"orgnr": ORG, "name": "ANNI&CO AS", "subunit_orgnrs": set(), "phones": ["63 96 57 30"], "emails": [],
       "people": ["Kari Nordmann"], "address": {"adresse": ["Storgata 1"], "postnummer": "2050", "poststed": "JESSHEIM"}}


def crawl(pages, origin="registry", url="https://anniogco.no"):
    with mock.patch.object(website, "resolves", return_value=True):
        cr = website.Crawler(FakeClient(pages), CTX, 20)
        return cr.try_site(url, origin)


class WebsiteTests(unittest.TestCase):
    def test_orgnr_on_site_is_exact(self):
        s = crawl({"https://anniogco.no": (200, "<title>Anni&amp;Co</title><p>Butikk på Jessheim. Org.nr 997 874 234</p>")})
        self.assertEqual((s.score, s.verification), (1.0, "orgnr_on_site"))

    def test_other_companys_orgnr_rejected(self):
        s = crawl({"https://anniogco.no": (200, "<title>Anni Co Group</title><p>Velkommen til kjeden. Vi har butikker over hele landet. Org.nr: 923 609 016 – alle butikker i kjeden</p>")})
        self.assertEqual(s.verification, "different_orgnr_on_site")

    def test_parked_domain_rejected(self):
        s = crawl({"https://anniogco.no": (200, "<title>anniogco.no</title><p>This domain is for sale! Contact us today.</p>")})
        self.assertEqual(s.verification, "parked_or_empty")

    def test_robots_respected(self):
        s = crawl({"https://anniogco.no/robots.txt": (200, "User-agent: *\nDisallow: /")})
        self.assertEqual(s.verification, "blocked_by_robots_txt")

    def test_guessed_domain_needs_corroboration(self):
        home = "<title>Anni &amp; Co</title><p>" + "Klær og tilbehør for alle anledninger. " * 5 + "</p>"
        s = crawl({"https://anniogco.no": (200, home)}, origin="name_guess")
        self.assertLess(s.score, 0.9)
        s = crawl({"https://anniogco.no": (200, home.replace("</p>", " Ring oss på 63 96 57 30</p>"))}, origin="name_guess")
        self.assertGreaterEqual(s.score, 0.9)

    def test_social_handle_must_match(self):
        self.assertTrue(website._handle_matches("anniogco", website.tokens("ANNI&CO AS"), []))
        self.assertFalse(website._handle_matches("webbyraaet", website.tokens("ANNI&CO AS"), []))

    def test_job_ad_urls(self):
        self.assertFalse(website.is_job_ad_url("https://www.equinor.com/careers/graduates", "equinor.com"))
        self.assertTrue(website.is_job_ad_url("https://acme.teamtailor.com/jobs/123456-sales", "acme.no"))
        self.assertTrue(website.is_job_ad_url("https://x.no/ledige-stillinger/elektriker-til-avdeling-sandnes", "x.no"))

    def test_dated_news_links(self):
        html = ('<div><span>12. september 2026</span><a href="/nyheter/vi-har-vunnet-ny-kontrakt">Vi har vunnet ny stor kontrakt</a></div>'
                '<div><a href="/andre-tjenester">Andre tjenesterLes mer</a> 03.08.2026</div>'
                '<div><a href="/testinnlegg-nr-3">Testinnlegg nr. 3 her</a> 01.08.2026</div>')
        p = website.Page("https://x.no/")
        p.feed(html)
        found = website.dated_links(p, "x.no", "https://x.no/")
        self.assertEqual([(t, d) for _, t, d, _ in found], [("Vi har vunnet ny stor kontrakt", "2026-09-12")])

    def test_freemail_ignored(self):
        self.assertIsNone(website.email_domain("someone@gmail.com"))
        self.assertEqual(website.normalize_url("www-firma.no"), "https://www.firma.no")
        self.assertIsNone(website.normalize_url("facebook.com/firma"))


def env_from(L):
    return {"organisation_number": ORG, "claims": L.claims, "evidence": L.evidence, "sections": dict(L.sections)}


class RefreshTests(unittest.TestCase):
    def test_idempotent_and_typed_changes(self):
        with tempfile.TemporaryDirectory() as d:
            st = store.Store(d)
            L, _ = collect(routes())
            self.assertEqual(store.refresh(st, env_from(L), "r1", "2026-10-01T00:00:00Z"), [])
            L, _ = collect(routes())
            self.assertEqual(store.refresh(st, env_from(L), "r2", "2026-10-02T00:00:00Z"), [])  # same snapshot
            roles = json.loads(json.dumps(ROLES))
            roles["rollegrupper"][0]["roller"][0]["person"]["navn"]["fornavn"] = "Per"
            L, _ = collect(routes(entity(antallAnsatte=9), roles=roles))
            env = env_from(L)
            ch = store.refresh(st, env, "r3", "2026-10-03T00:00:00Z")
            types = sorted(c["change_type"] for c in ch)
            self.assertIn("changed_registered_employees", types)
            self.assertIn("new_role", types)
            self.assertIn("removed_role", types)
            emp = next(c for c in ch if c["field"] == "registered_employees")
            self.assertEqual((emp["previous_value"], emp["current_value"]), (7, 9))
            self.assertTrue(emp["previous_evidence"])  # prior evidence preserved
            self.assertEqual(env["refresh"]["check_count"], 3)

    def test_failed_volatile_source_keeps_last_value(self):
        with tempfile.TemporaryDirectory() as d:
            st = store.Store(d)
            L, _ = collect(routes())
            resp = Response("https://anniogco.no", 200, b"<p>Org.nr 997 874 234</p>")
            ev = L.add_evidence(resp, "company_owned", "Org.nr 997 874 234", "orgnr_regex_visible_text")
            L.claim("website", "official_website", {"url": "https://anniogco.no", "domain": "anniogco.no"}, [ev], key="anniogco.no")
            store.refresh(st, env_from(L), "r1", "2026-10-01T00:00:00Z")
            L, _ = collect(routes())
            L.section("website", "failed", "unreachable")
            env = env_from(L)
            ch = store.refresh(st, env, "r2", "2026-10-02T00:00:00Z")
            self.assertEqual(ch, [])
            kept = next(c for c in env["claims"] if c["field"] == "official_website")
            self.assertTrue(kept["stale"])
            self.assertTrue(kept["evidence_ids"])


    def _site_claims(self, L, url="https://anniogco.no/", found_via="registry", with_desc=True):
        resp = Response(url, 200, b"<p>Org.nr 997 874 234</p>")
        ev = L.add_evidence(resp, "company_owned", "Org.nr 997 874 234", "orgnr_regex_visible_text")
        L.claim("website", "official_website", {"url": url, "domain": "anniogco.no", "found_via": found_via}, [ev], key="anniogco.no")
        if with_desc:
            L.claim("identity", "website_description", "Klesbutikk på Jessheim", [ev])

    def test_metadata_and_slash_are_not_changes(self):
        with tempfile.TemporaryDirectory() as d:
            st = store.Store(d)
            L, _ = collect(routes())
            self._site_claims(L)
            store.refresh(st, env_from(L), "r1", "2026-10-01T00:00:00Z")
            L, _ = collect(routes())
            self._site_claims(L, url="https://anniogco.no", found_via="previously_verified")
            self.assertEqual(store.refresh(st, env_from(L), "r2", "2026-10-02T00:00:00Z"), [])

    def test_company_owned_removal_needs_two_misses(self):
        with tempfile.TemporaryDirectory() as d:
            st = store.Store(d)
            L, _ = collect(routes())
            self._site_claims(L)
            store.refresh(st, env_from(L), "r1", "2026-10-01T00:00:00Z")
            L, _ = collect(routes())
            self._site_claims(L, with_desc=False)
            self.assertEqual(store.refresh(st, env_from(L), "r2", "2026-10-02T00:00:00Z"), [])
            L, _ = collect(routes())
            self._site_claims(L, with_desc=False)
            ch = store.refresh(st, env_from(L), "r3", "2026-10-03T00:00:00Z")
            self.assertEqual([c["change_type"] for c in ch], ["removed_website_description"])


class BatchContractTests(unittest.TestCase):
    def test_one_terminal_envelope_per_input(self):
        r = routes()
        r[f"{ER}/enheter/923609016"] = (404, b"")
        r[f"{ER}/underenheter/923609016"] = (404, b"")
        with tempfile.TemporaryDirectory() as d:
            inp = Path(d) / "batch.jsonl"
            inp.write_text('{"organisation_number":"997874234"}\n{"organisation_number":"923609016"}\n'
                           '{"organisation_number":"123456789"}\n', encoding="utf-8")
            fake = FakeClient(r)
            with mock.patch("signalpost.http.Client.get", side_effect=lambda url, **kw: fake.get(url, **kw)):
                envs, rep = runner.run(str(inp), out_dir=str(Path(d) / "out"), web=False, jobs=False, html=True)
            self.assertEqual([e["organisation_number"] for e in envs], [ORG, "923609016", "123456789"])
            self.assertEqual([e["state"] for e in envs], ["available", "not_available", "failed"])
            self.assertTrue(all(e["state"] in STATES for e in envs))
            self.assertTrue(rep["validation"]["passed"])
            self.assertTrue((Path(d) / "out" / "index.html").exists())
            self.assertIn("Latest filed accounts (2025)", envs[0]["summary"]["text"])


if __name__ == "__main__":
    unittest.main()
