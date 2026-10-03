"""Offline tests (no network): python3 -m unittest discover tests"""
import tempfile
import unittest

from signalpost import orgnr, profile, store, website
from signalpost.http import Response


class FakeClient:
    """Serves canned responses by URL; counts requests like the real client."""

    def __init__(self, routes):
        self.routes = routes
        self.count = 0

    def get(self, url, **kw):
        self.count += 1
        status, body = self.routes.get(url, (404, b""))
        if isinstance(body, str):
            body = body.encode()
        return Response(url, status, body, url, {"Content-Type": "text/html; charset=utf-8"})


ER = "https://data.brreg.no/enhetsregisteret/api"
RR = "https://data.brreg.no/regnskapsregisteret/regnskap"

ENTITY = """{"organisasjonsnummer":"997874234","navn":"ANNI&CO AS",
 "organisasjonsform":{"kode":"AS","beskrivelse":"Aksjeselskap"},
 "registreringsdatoEnhetsregisteret":"2012-01-10","stiftelsesdato":"2011-12-20",
 "forretningsadresse":{"adresse":["Storgata 1"],"postnummer":"2050","poststed":"JESSHEIM","kommune":"ULLENSAKER","kommunenummer":"3209","landkode":"NO","land":"Norge"},
 "naeringskode1":{"kode":"47.710","beskrivelse":"Detaljhandel med klær"},
 "antallAnsatte":7,"harRegistrertAntallAnsatte":true,"hjemmeside":"www.anniogco.no","konkurs":false}"""
ROLES = """{"rollegrupper":[{"sistEndret":"2020-01-01","type":{"kode":"DAGL"},"roller":[
 {"type":{"kode":"DAGL","beskrivelse":"Daglig leder"},"person":{"navn":{"fornavn":"Kari","etternavn":"Nordmann"}},"avregistrert":false}]},
 {"sistEndret":"2021-01-01","type":{"kode":"STYR"},"roller":[
 {"type":{"kode":"LEDE","beskrivelse":"Styrets leder"},"person":{"navn":{"fornavn":"Ola","etternavn":"Nordmann"}},"avregistrert":false},
 {"type":{"kode":"MEDL","beskrivelse":"Styremedlem"},"person":{"navn":{"fornavn":"Gammel","etternavn":"Person"}},"avregistrert":true}]}]}"""
ACCOUNTS = """[{"regnskapstype":"SELSKAP","journalnr":"1","virksomhet":{"organisasjonsnummer":"997874234"},
 "regnskapsperiode":{"fraDato":"2025-01-01","tilDato":"2025-12-31"},"valuta":"NOK",
 "resultatregnskapResultat":{"aarsresultat":368000.0,"driftsresultat":{"driftsresultat":500000.0,"driftsinntekter":{"sumDriftsinntekter":11200000.0}}},
 "egenkapitalGjeld":{"egenkapital":{"sumEgenkapital":1700000.0}},"eiendeler":{"sumEiendeler":4000000.0}},
 {"regnskapstype":"SELSKAP","journalnr":"2","virksomhet":{"organisasjonsnummer":"999999999"},
 "regnskapsperiode":{"fraDato":"2026-01-01","tilDato":"2026-12-31"},"valuta":"NOK",
 "resultatregnskapResultat":{"aarsresultat":1.0}}]"""


def routes(entity=ENTITY):
    return {
        f"{ER}/enheter/997874234": (200, entity),
        f"{ER}/enheter/997874234/roller": (200, ROLES),
        f"{ER}/underenheter?overordnetEnhet=997874234&size=20": (200, '{"page":{"totalElements":0}}'),
        f"{RR}/997874234": (200, ACCOUNTS),
    }


class OrgnrTests(unittest.TestCase):
    def test_mod11(self):
        self.assertTrue(orgnr.is_valid("923609016"))
        self.assertFalse(orgnr.is_valid("923609017"))
        self.assertFalse(orgnr.is_valid("12345"))

    def test_normalize_and_pattern(self):
        self.assertEqual(orgnr.normalize("NO 923 609 016 MVA"), "923609016")
        self.assertTrue(orgnr.pattern("923609016").search("Org.nr: 923 609 016"))
        self.assertFalse(orgnr.pattern("923609016").search("19236090161"))


class ProfileTests(unittest.TestCase):
    def test_core_profile_facts_and_evidence(self):
        p = profile.build_core(FakeClient(routes()), "997874234")
        f = p["facts"]
        self.assertEqual(f["name"]["value"], "ANNI&CO AS")
        self.assertEqual(f["chief_executive"]["value"]["name"], "Kari Nordmann")
        self.assertEqual(f["board_chair"]["value"]["name"], "Ola Nordmann")
        self.assertNotIn("board_members", f)  # deregistered member is excluded
        self.assertEqual(f["financials"]["value"]["figures"]["revenue"], 11200000.0)
        self.assertEqual(f["financials"]["value"]["fiscal_year"], 2025)
        for fact in f.values():
            self.assertIn(fact["source"], p["sources"])
            self.assertTrue(p["sources"][fact["source"]]["url"].startswith("https://"))

    def test_foreign_orgnr_accounts_rejected(self):
        p = profile.build_core(FakeClient(routes()), "997874234")
        # the 2026 row belongs to another orgnr and must not be published
        self.assertEqual(p["facts"]["financials"]["value"]["fiscal_year"], 2025)
        self.assertTrue(any(not c["match"] for c in p["identity_checks"]))

    def test_identity_mismatch_publishes_nothing(self):
        wrong = ENTITY.replace('"997874234"', '"923609016"')
        p = profile.build_core(FakeClient(routes(wrong)), "997874234")
        self.assertEqual(p["lookup_result"], "identity_mismatch")
        self.assertEqual(p["facts"], {})

    def test_not_found(self):
        p = profile.build_core(FakeClient({}), "923609016")
        self.assertEqual(p["lookup_result"], "not_found")


class WebsiteTests(unittest.TestCase):
    def test_orgnr_on_site_verifies(self):
        html = "<html><title>Anni&amp;Co</title><meta name=description content='Klesbutikk'><p>Org.nr 997 874 234</p></html>"
        c = FakeClient({"https://anniogco.no": (200, html)})
        r = website.inspect_site(c, "https://anniogco.no", "997874234", "ANNI&CO AS")
        self.assertEqual(r["verification"], "orgnr_on_site")
        self.assertEqual(r["description"], "Klesbutikk")

    def test_other_company_site_rejected(self):
        html = "<html><title>Big Group</title><p>Org.nr: 923 609 016</p></html>"
        c = FakeClient({"https://group.no": (200, html)})
        r = website.inspect_site(c, "https://group.no", "997874234", "ANNI&CO AS")
        self.assertEqual(r["verification"], "different_orgnr_on_site")

    def test_robots_respected(self):
        c = FakeClient({"https://x.no/robots.txt": (200, "User-agent: *\nDisallow: /")})
        r = website.inspect_site(c, "https://x.no/", "997874234", "X AS")
        self.assertEqual(r["verification"], "blocked_by_robots_txt")

    def test_freemail_ignored(self):
        self.assertIsNone(website.domain_from_email("someone@gmail.com"))
        self.assertEqual(website.domain_from_email("post@firma.no"), "https://firma.no")
        self.assertEqual(website.normalize_url("www-firma.no"), "https://www.firma.no")


class StoreTests(unittest.TestCase):
    def test_change_history(self):
        with tempfile.TemporaryDirectory() as d:
            s = store.Store(d)
            p1 = profile.build_core(FakeClient(routes()), "997874234")
            self.assertEqual(s.save(p1), [])
            p2 = profile.build_core(FakeClient(routes(ENTITY.replace('"antallAnsatte":7', '"antallAnsatte":9'))), "997874234")
            changes = s.save(p2)
            self.assertEqual([c["field"] for c in changes], ["employees"])
            self.assertEqual((changes[0]["old"], changes[0]["new"]), (7, 9))
            saved = s.load("997874234")
            self.assertEqual(saved["history"]["check_count"], 2)
            self.assertEqual(saved["history"]["changes"][0]["field"], "employees")


if __name__ == "__main__":
    unittest.main()
