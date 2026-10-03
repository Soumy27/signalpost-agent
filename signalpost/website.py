"""Company website: discovery, identity gate, targeted crawl and extraction.

Candidates (in order): the website listed in Enhetsregisteret, the domain of
the registered (non-free-mail) e-mail address, then a few domains derived from
the legal name. robots.txt is always honoured. Nothing from a site is
published unless the site resolves to the exact legal entity:

  * organisation number on a crawled page               -> exact (1.0)
  * all distinctive legal-name tokens in the homepage's
    identity text (title, site name, JSON-LD, host)     -> exact (0.95)
      - for name-derived domains this also needs an independent
        corroboration (registered phone, postcode+street, e-mail
        domain or a registered role holder on the site)
  * parked/for-sale pages or another company's org. no. -> rejected
  * anything weaker                                     -> ambiguous (unpublished)
"""
import html as html_lib
import json
import re
import socket
import unicodedata
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from . import orgnr as orgnr_mod
from .http import USER_AGENT, BudgetExceeded

CO = "company_owned"

FREEMAIL = {
    "gmail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no", "yahoo.com",
    "yahoo.no", "icloud.com", "me.com", "msn.com", "online.no", "frisurf.no", "start.no", "broadpark.no",
    "lyse.net", "altibox.no", "c2i.net", "getmail.no", "tele2.no", "telenor.no", "haugnett.no", "enivest.net",
    "ebnett.no", "bluezone.no", "loqal.no", "neasonline.no", "tdcnet.no", "vikenfiber.no", "aol.com",
    "protonmail.com", "proton.me", "gmx.com", "gmx.net", "windowslive.com", "hotmail.co.uk", "mail.com",
    "yandex.com", "googlemail.com", "post.no", "sensewave.no", "bigfoot.com", "mail.ru", "q.com", "epost.no",
    "ymail.com", "rocketmail.com", "kpnmail.nl", "chello.no", "netcom.no", "nextgentel.com", "ngbredband.no",
}
# Domains that are never a company's own site even if listed in the register
NOT_COMPANY_SITES = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
                     "tiktok.com", "proff.no", "gulesider.no", "1881.no", "finn.no", "purehelp.no",
                     "brreg.no", "google.com", "wix.com", "mittanbud.no", "nav.no")
SOCIAL = {
    "linkedin": ("linkedin.com",), "facebook": ("facebook.com", "fb.com"), "instagram": ("instagram.com",),
    "youtube": ("youtube.com",), "x": ("twitter.com", "x.com"), "tiktok": ("tiktok.com",),
    "pinterest": ("pinterest.com",), "vimeo": ("vimeo.com",), "github": ("github.com",),
}
_UNUSED_ATS = ("teamtailor.com", "webcruiter.com", "webcruiter.no", "jobbnorge.no", "recman.no", "recman.page",
       "hrmanager.no", "easycruit.com", "reachmee.com", "myworkdayjobs.com", "smartrecruiters.com",
       "lever.co", "greenhouse.io", "jobylon.com", "talentech.io", "varbi.com", "emply.com", "finn.no/job",
       "arbeidsplassen.nav.no", "careers.", "career.", "jobs.", "karriere.", "thehub.io", "simployer.com")
GENERIC = {"as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "stiftelsen", "nuf", "ab", "limited",
           "ltd", "inc", "plc", "the", "og", "and", "i", "av", "for", "til", "norge", "norway", "norsk",
           "holding", "invest", "eiendom", "group", "gruppen", "avd", "avdeling", "co", "company", "service",
           "services", "drift", "consult", "consulting"}
PARKED = ("domain is for sale", "domain for sale", "domenet er til salgs", "hugedomains", "parked at", "parkingcrew",
          "this domain has been registered", "her flytter snart en ny gjest", "is under construction",
          "under construction", "kommer snart", "coming soon", "default web site page", "welcome to nginx",
          "apache2 debian default page", "it works!", "domene registrert", "this site can’t be reached",
          "find the best information and most relevant links on all topics related to", "sedo domain parking",
          "one.com - domene", "webhuset", "denne siden er under oppbygging", "be back soon", "we'll be back",
          "maintenance mode", "site is under maintenance", "nettsiden er under arbeid", "siden er under utvikling",
          "account suspended", "this account has been suspended", "website expired", "domain has expired",
          "index of /", "plesk default page", "cpanel default page")
SUBPAGES = {
    "contact": r"kontakt|contact|finn-oss|finn_oss|find-us|om-oss/kontakt",
    "about": r"om-oss|om_oss|omoss|about|hvem-er-vi|historie|selskapet|var-historie|who-we-are",
    "careers": r"karriere|ledige-stillinger|ledige_stillinger|stillinger|jobb-hos-oss|jobbe-hos-oss|career|jobs|vacanc|work-with-us|rekruttering",
    "news": r"nyhet|news|aktuelt|blogg|blog|presse|artikler|media|insights|innsikt",
    "people": r"ledelse|ansatte|medarbeidere|team|people|leadership|management|styret|vare-folk|kontaktpersoner",
    "locations": r"avdeling|kontorer|locations|butikker|vare-butikker|offices|lokasjoner|finn-butikk",
}
JOB_WORDS = re.compile(r"stilling|jobb|karriere|career|job|vacanc|rekrutter|ansett|apply|soknad")
GENERIC_LINK = re.compile(r"^(se |vis |les |read |apply|sok |alle |all |mer |more |view |open |our |var |vare |hva |what |why |hvorfor |"
                          r"om |about |kontakt|contact|ledige stillinger$|stillinger$|jobs$|careers?$|karriere$|jobb hos|work with|"
                          r"graduate|intern|trainee|laerling|apprentic|student|sommerjobb|summer|culture|kultur|benefits|fordeler|"
                          r"how |slik |recruitment|rekrutteringsprosess|faq|personvern|privacy|cookie|send |klikk|click|"
                          r"les mer|read more|view project|apen soknad|open application)|(soknad|\bcv\b|\bher$|\bhere$)")
ATS_DOMAINS = ("teamtailor.com", "webcruiter.com", "webcruiter.no", "jobbnorge.no", "recman.no", "recman.page", "hrmanager.no",
               "easycruit.com", "reachmee.com", "myworkdayjobs.com", "smartrecruiters.com", "lever.co", "greenhouse.io",
               "jobylon.com", "varbi.com", "emply.com", "simployer.com", "talentech.io", "thehub.io", "finn.no/job")
JOB_AD_PATTERNS = [
    r"teamtailor\.com/(?:[a-z]{2}/)?jobs/\d+", r"webcruiter\.(?:no|com)/.*(?:advertid|ad_id|/job/)", r"jobbnorge\.no/.*(?:/\d{5,}|jobid=)",
    r"finn\.no/(?:job/ad/\d+|job/.*finnkode=\d+|\d{6,})", r"recman\.(?:no|page)/.*(?:job_id=|/job/)", r"hrmanager\.no/.*(?:/ad/|adid=)",
    r"easycruit\.com/vacancy/\d+", r"reachmee\.com/.*(?:jobid=|/job/)", r"myworkdayjobs\.com/.+/job/", r"smartrecruiters\.com/[^/]+/\d+",
    r"jobs\.lever\.co/[^/]+/[0-9a-f-]{20,}", r"greenhouse\.io/.*/jobs/\d+", r"jobylon\.com/jobs/\d+", r"thehub\.io/jobs/[0-9a-f]{12,}",
    r"arbeidsplassen\.nav\.no/stillinger/stilling/[0-9a-f-]{20,}", r"varbi\.com/.*/(?:job|what:job)/\d+", r"emply\.com/.*/ad/",
]


def is_job_ad_url(href, own_domain):
    h = href.lower()
    if any(re.search(p, h) for p in JOB_AD_PATTERNS):
        return True
    if reg_domain(host_of(href)) != own_domain:
        return False
    path = urlparse(h).path.rstrip("/")
    m = re.search(r"/(?:jobs?|stillinger|stilling|ledige-stillinger|ledig-stilling|vacancies|vacancy|positions?|utlysninger)/([^/]+)$", path)
    return bool(m and m.group(1).count("-") >= 2 and not re.search(r"(soknad|apply|prosess|process|faq|graduate|trainee|student|intern)", m.group(1)))


FALLBACK_PATHS = {"contact": ["/kontakt", "/kontakt-oss"], "about": ["/om-oss"]}


def fold(text):
    t = (text or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().casefold()


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", fold(text)) if t not in GENERIC and len(t) > 1]


def host_of(url):
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def reg_domain(host):
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize_url(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^(https?://)?www-", r"\1www.", raw, flags=re.I)
    raw = raw.split()[0].rstrip("/.,;")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    h = host_of(raw)
    if not h or "." not in h or any(h == d or h.endswith("." + d) for d in NOT_COMPANY_SITES):
        return None
    return raw


def email_domain(email):
    if not email or "@" not in email:
        return None
    d = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    return None if d in FREEMAIL or "." not in d else d


def name_domains(legal_name):
    """A few conservative guesses from the legal name (e.g. 'Anni&Co AS' -> anniogco.no)."""
    raw = fold(legal_name or "")
    variants = []
    for amp in ("og", "", "and"):
        t = [w for w in re.findall(r"[a-z0-9]+|&", raw.replace("&", " & ")) if w not in ("as", "asa", "ans", "da", "sa", "nuf", "enk")]
        t = [amp if w == "&" else w for w in t]
        t = [w for w in t if w]
        if not t:
            continue
        variants.append("".join(t))
        if len(t) > 1:
            variants.append("-".join(t))
    core = [w for w in re.findall(r"[a-z0-9]+", raw) if w not in GENERIC and len(w) > 1]
    if core and "".join(core) not in variants and len("".join(core)) >= 6 and len(core) >= 2:
        variants.append("".join(core))
    if len(core) >= 3 and len(core[0] + core[1]) >= 6:
        variants.append(core[0] + core[1])
    out = []
    for v in dict.fromkeys(variants):
        if len(v.replace("-", "")) >= 5 and not all(w in GENERIC for w in v.split("-")):
            out.append(f"https://{v}.no")
    if variants and len(variants[0]) >= 6:
        out.append(f"https://{variants[0]}.com")
    return out[:5]


def resolves(host):
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return True
    except (socket.gaierror, UnicodeError, OSError):
        return False


class Page(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title = ""
        self.h1 = []
        self.meta = {}
        self.links = []          # (abs_url, text)
        self.feeds = []
        self.jsonld = []
        self.text = []
        self.articles = []
        self._stack = []
        self._a = None
        self._art = None
        self._h = None
        self._len = 0
        self.link_pos = []       # (abs_url, text, offset in raw text)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript", "svg", "template"):
            self._stack.append((tag, a.get("type", "").lower()))
            return
        if tag == "title" or tag == "h1":
            self._h = [tag, ""]
        if tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "link":
            rel = a.get("rel", "").lower()
            typ = a.get("type", "").lower()
            if "alternate" in rel and ("rss" in typ or "atom" in typ) and a.get("href"):
                self.feeds.append(urljoin(self.base, a["href"]))
        elif tag == "a" and a.get("href"):
            href = a["href"].strip()
            if not href.lower().startswith(("javascript:", "#")):
                self._a = [urljoin(self.base, href), "", self._len]
                if self._art is not None and not self._art.get("href"):
                    self._art["href"] = self._a[0]
        elif tag == "article":
            self._art = {}
        elif tag == "time" and self._art is not None and a.get("datetime"):
            self._art.setdefault("date", a["datetime"])
        elif tag in ("h2", "h3", "h4") and self._art is not None:
            self._art.setdefault("_h", "")
            self._art["_inh"] = True
        if tag in ("br", "p", "div", "li", "td", "h2", "h3", "section", "footer", "address"):
            self.text.append(" ")
            self._len += 1

    def handle_endtag(self, tag):
        if self._stack and tag == self._stack[-1][0]:
            self._stack.pop()
            return
        if self._h and tag == self._h[0]:
            if tag == "title":
                self.title += self._h[1]
            else:
                self.h1.append(" ".join(self._h[1].split()))
            self._h = None
        if tag == "a" and self._a:
            t = " ".join(self._a[1].split())
            self.links.append((self._a[0], t))
            self.link_pos.append((self._a[0], t, (self._a[2], self._len)))
            self._a = None
        if tag in ("h2", "h3", "h4") and self._art is not None:
            self._art["_inh"] = False
        if tag == "article" and self._art is not None:
            if self._art.get("date"):
                title = " ".join((self._art.get("_h") or "").split())
                self.articles.append({"title": title, "href": self._art.get("href"), "date": self._art["date"]})
            self._art = None

    def handle_data(self, data):
        if self._stack:
            if self._stack[-1][0] == "script" and "ld+json" in self._stack[-1][1]:
                self.jsonld.append(data)
            return
        if self._h:
            self._h[1] += data
        if self._a:
            self._a[1] += data
        if self._art is not None and self._art.get("_inh"):
            self._art["_h"] = self._art.get("_h", "") + data
        self.text.append(data)
        self._len += len(data)

    def visible_text(self):
        return " ".join("".join(self.text).split())

    def structured(self):
        out = []
        for blob in self.jsonld:
            try:
                data = json.loads(blob.strip())
            except Exception:
                continue
            stack = [data]
            while stack:
                d = stack.pop()
                if isinstance(d, list):
                    stack.extend(d)
                elif isinstance(d, dict):
                    out.append(d)
                    if "@graph" in d:
                        stack.append(d["@graph"])
        return out


def parse(resp):
    p = Page(resp.final_url)
    try:
        p.feed(resp.text())
        p.close()
    except Exception:
        pass
    return p


def snippet(text, m, pad=60):
    s = max(0, m.start() - pad)
    return " ".join(text[s:m.end() + pad].split())


def _types(d):
    t = d.get("@type")
    return [x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


ORG_TYPES = ("organization", "corporation", "localbusiness", "store", "restaurant", "professionalservice",
             "homeandconstructionbusiness", "medicalbusiness", "autodealer", "legalservice", "lodgingbusiness",
             "foodestablishment", "financialservice", "realestateagent", "healthandbeautybusiness", "dentist",
             "automotivebusiness", "educationalorganization", "sportsorganization", "ngo", "govermentorganization")


class SiteResult:
    def __init__(self, url, origin):
        self.url = url
        self.origin = origin
        self.verification = "unreachable"
        self.score = 0.0
        self.reasons = []
        self.pages = []          # (kind, resp, Page)
        self.http_status = None
        self.error = None
        self.proof = None        # (resp, span, method)


class Crawler:
    def __init__(self, client, ctx, per_company_requests=16):
        self.client = client
        self.ctx = ctx
        self.cap = per_company_requests
        self.used = 0
        self.robots = {}
        self.core = tokens(ctx.get("name"))

    def get(self, url, **kw):
        if self.used >= self.cap:
            raise BudgetExceeded("per-company web budget used")
        self.used += 1
        return self.client.get(url, **kw)

    def allowed(self, url):
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self.robots:
            rp = None
            try:
                r = self.get(origin + "/robots.txt", timeout=6, retries=0, accept="text/plain")
                if r.status < 400 and b"<html" not in r.body[:500].lower():
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(r.text().splitlines())
            except BudgetExceeded:
                raise
            except Exception:
                rp = None
            self.robots[origin] = rp
        rp = self.robots[origin]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def fetch_html(self, url, timeout=10):
        if not self.allowed(url):
            return "blocked", None
        r = self.get(url, timeout=timeout, retries=0, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
        ctype = (r.headers.get("Content-Type") or r.headers.get("content-type") or "").lower()
        if r.status >= 400 or ("html" not in ctype and "xml" not in ctype and ctype):
            return f"http_{r.status}", r
        return "ok", r

    # -- identity ----------------------------------------------------------------------
    def identity(self, site, resp, page, homepage=True):
        orgnr = self.ctx["orgnr"]
        html = resp.text()
        text = page.visible_text()
        m = orgnr_mod.pattern(orgnr).search(text)
        if m:
            return 1.0, "orgnr_on_site", (resp, snippet(text, m), "orgnr_regex_visible_text")
        m = orgnr_mod.pattern(orgnr).search(html)
        if m:
            return 1.0, "orgnr_on_site", (resp, snippet(html, m), "orgnr_regex_html_source")
        if not homepage:
            if site.verification == "name_only_needs_corroboration":
                corro = self.corroboration(text + " " + html[:200000])
                if corro:
                    return 0.95, "legal_name_on_site", (resp, f"legal name on homepage; corroborated by {corro} on {resp.final_url}",
                                                        "legal_name_tokens+corroboration")
            return 0.0, None, None
        low = fold(" ".join([page.title, text[:4000]]))
        if any(p in low for p in PARKED) or (len(text) < 60 and not page.structured()):
            return 0.1, "parked_or_empty", None
        others = set()
        for m in re.finditer(r"(?<!\d)([89]\d{2})[\s. ]?(\d{3})[\s. ]?(\d{3})(?!\d)", text):
            cand = "".join(m.groups())
            if cand != orgnr and orgnr_mod.is_valid(cand) and cand not in self.ctx["subunit_orgnrs"] and \
                    re.search(r"(org|mva|foretaks|vat)", text[max(0, m.start() - 40):m.start()], re.I):
                others.add(cand)
        if others:
            site.reasons.append(f"another organisation number on site: {sorted(others)[:3]}")
            return 0.2, "different_orgnr_on_site", None
        if re.search(r"\bb\.?\s*i\.?\s*l\.?\b", self.ctx.get("name") or "", re.I) and "bedriftsidrett" not in low:
            return 0.3, "business_sports_club_mismatch", None
        names = []
        for d in page.structured():
            if any(t in ORG_TYPES or t.endswith("business") for t in _types(d)) or "website" in _types(d):
                for k in ("name", "legalName", "alternateName"):
                    if isinstance(d.get(k), str):
                        names.append(d[k])
        identity_parts = [page.title, page.meta.get("og:site_name", ""), page.meta.get("og:title", ""),
                          page.meta.get("application-name", ""), host_of(resp.final_url).replace("-", " ").replace(".", " "),
                          *names, *page.h1[:2]]
        core = set(self.core)
        if not core:
            return 0.3, "no_distinctive_name_tokens", None
        exact = any(core <= set(tokens(part)) for part in identity_parts if part)
        compact_host = fold(host_of(resp.final_url)).replace("-", "").replace(".", "")
        if not exact and "".join(self.core) and "".join(self.core) in compact_host:
            exact = True
        if exact and (len(core) >= 2 or len(text) >= 100):
            corro = self.corroboration(text + " " + html[:200000])
            if site.origin == "name_guess" and not corro:
                return 0.85, "name_only_needs_corroboration", None
            span = next((p for p in identity_parts if p and core <= set(tokens(p))), page.title)
            proof = (resp, f"identity text: {span[:160]}" + (f"; corroborated by {corro}" if corro else ""), "legal_name_tokens")
            return 0.95, "legal_name_on_site", proof
        overlap = core & set(tokens(" ".join(identity_parts) + " " + text[:3000]))
        if len(overlap) / len(core) >= 0.5:
            return 0.6, "partial_name_match", None
        return 0.3, "name_not_found", None

    def corroboration(self, text):
        digits = re.sub(r"\D", "", text)
        for ph in self.ctx.get("phones", []):
            d = re.sub(r"\D", "", ph)[-8:]
            if len(d) == 8 and d in digits:
                return "registered phone number"
        addr = self.ctx.get("address") or {}
        street = fold((addr.get("adresse") or [""])[0]) if addr.get("adresse") else ""
        ft = fold(text)
        if addr.get("postnummer") and addr["postnummer"] in text and street and len(street) > 4 and street[:12] in ft:
            return "registered street address and postcode"
        if addr.get("postnummer") and addr.get("poststed") and re.search(
                re.escape(addr["postnummer"]) + r"\s*,?\s*" + re.escape(fold(addr["poststed"])), ft):
            return "registered postcode and city"
        for e in self.ctx.get("emails", []):
            if e and e.lower() in text.lower():
                return "registered e-mail address"
        for person in self.ctx.get("people", [])[:6]:
            if len(person.split()) >= 2 and fold(person) in ft:
                return f"registered role holder ({person})"
        return None

    # -- crawl -------------------------------------------------------------------------
    def try_site(self, url, origin):
        site = SiteResult(url, origin)
        host = host_of(url)
        if not resolves(host) and not resolves("www." + host):
            site.verification, site.error = "dns_not_found", "domain does not resolve"
            return site
        resp = None
        for cand in dict.fromkeys([url, url.replace("https://", "http://", 1)]):
            try:
                st, resp = self.fetch_html(cand)
            except BudgetExceeded:
                raise
            except Exception as e:
                site.error = type(e).__name__
                resp = None
                continue
            if st == "blocked":
                site.verification = "blocked_by_robots_txt"
                return site
            if st == "ok":
                break
            site.http_status = resp.status if resp else None
            resp = None
        if not resp:
            if site.verification == "unreachable" and site.http_status:
                site.verification = f"http_{site.http_status}"
            return site
        page = parse(resp)
        site.pages.append(("homepage", resp, page))
        site.http_status = resp.status
        score, verdict, proof = self.identity(site, resp, page)
        site.score, site.verification, site.proof = score, verdict, proof
        if verdict in ("parked_or_empty", "different_orgnr_on_site", "business_sports_club_mismatch"):
            return site
        # look for the organisation number on contact/about pages (strongest proof)
        picks = self.pick_subpages(page, resp.final_url)
        order = [k for k in ("contact", "about") if k in picks]
        tried = set()
        for kind in order:
            sub = self.fetch_sub(site, picks[kind], kind)
            tried.add(kind)
            if sub and site.score < 1.0:
                s2, v2, p2 = self.identity(site, sub[0], sub[1], homepage=False)
                if s2 > site.score:
                    site.score, site.verification, site.proof = s2, v2, p2
                if s2 == 1.0:
                    site.score, site.verification, site.proof = s2, v2, p2
                    break
        if site.score < 0.9 and not order:
            for kind, paths in FALLBACK_PATHS.items():
                for path in paths:
                    sub = self.fetch_sub(site, urljoin(resp.final_url, path), kind)
                    if sub:
                        s2, v2, p2 = self.identity(site, sub[0], sub[1], homepage=False)
                        if s2 > site.score:
                            site.score, site.verification, site.proof = s2, v2, p2
                        break
                if site.score >= 1.0:
                    break
        if site.score < 0.9:
            return site
        # verified: crawl the remaining informative pages
        for kind in ("careers", "news", "people", "locations", "contact", "about"):
            if kind in picks and kind not in tried and self.used < self.cap - 1:
                self.fetch_sub(site, picks[kind], kind)
        if b"wp-content" in resp.body and self.used < self.cap:
            api = urljoin(resp.final_url, "/wp-json/wp/v2/posts?per_page=10&_fields=date,link,title")
            try:
                if self.allowed(api):
                    wr = self.get(api, timeout=8, retries=0)
                    if wr.status == 200 and wr.body[:1] == b"[":
                        site.pages.append(("wp_posts", wr, None))
            except BudgetExceeded:
                pass
            except Exception:
                pass
        feeds = [f for f in dict.fromkeys(page.feeds) if "comment" not in f.lower()]
        if feeds and self.used < self.cap:
            try:
                if self.allowed(feeds[0]):
                    fr = self.get(feeds[0], timeout=8, retries=0, accept="application/rss+xml,application/atom+xml,application/xml,text/xml")
                    if fr.status < 400:
                        site.pages.append(("feed", fr, None))
            except BudgetExceeded:
                pass
            except Exception:
                pass
        return site

    def fetch_sub(self, site, url, kind):
        if any(r.final_url == url or r.url == url for _, r, _ in site.pages):
            return None
        try:
            st, r = self.fetch_html(url, timeout=8)
        except BudgetExceeded:
            return None
        except Exception:
            return None
        if st != "ok":
            return None
        p = parse(r)
        site.pages.append((kind, r, p))
        return r, p

    def pick_subpages(self, page, base):
        bdom = reg_domain(host_of(base))
        best = {}
        for href, text in page.links:
            h = host_of(href)
            if not h or reg_domain(h) != bdom:
                continue
            path = urlparse(href).path.lower()
            if path in ("", "/") or re.search(r"\.(pdf|jpg|png|zip|docx?)$", path):
                continue
            label = fold(text)
            for kind, pat in SUBPAGES.items():
                if re.search(pat, path) or (label and re.fullmatch(r"(" + pat.replace("-", "[ -]") + r")[a-z ]{0,15}", label)):
                    depth = path.strip("/").count("/")
                    if kind not in best or depth < best[kind][0]:
                        best[kind] = (depth, href.split("#")[0])
        return {k: v[1] for k, v in best.items()}


def discover(client, ctx, budget=16):
    """Try candidate sites in order; return (verified SiteResult or None, attempts)."""
    cr = Crawler(client, ctx, budget)
    candidates = []
    prev = normalize_url(ctx.get("previous_website"))
    if prev:
        candidates.append((prev, "previously_verified"))
    reg = normalize_url(ctx.get("registry_website"))
    if reg:
        candidates.append((reg, "registry"))
    for e in ctx.get("emails", []):
        d = email_domain(e)
        if d:
            candidates.append((f"https://{d}", "registry_email_domain"))
    for u in ctx.get("employer_homepages") or []:
        nu = normalize_url(u)
        if nu:
            candidates.append((nu, "job_ad_employer_homepage"))
    for u in name_domains(ctx.get("name")):
        candidates.append((u, "name_guess"))
    seen, attempts = set(), []
    for url, origin in candidates:
        key = reg_domain(host_of(url))
        if key in seen:
            continue
        seen.add(key)
        try:
            site = cr.try_site(url, origin)
        except BudgetExceeded:
            break
        attempts.append(site)
        if site.score >= 0.9:
            return site, attempts, cr
        if origin == "registry" and site.verification in ("different_orgnr_on_site",):
            continue
    return None, attempts, cr


# ---- extraction from a verified site ------------------------------------------------------

def _social(url):
    p = urlparse(url)
    h = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.").removeprefix("no.")
    for platform, doms in SOCIAL.items():
        if any(h == d or h.endswith("." + d) for d in doms):
            path = p.path.strip("/")
            if not path or re.search(r"(sharer|share|intent|dialog|login|plugins|hashtag|search|watch\?|tr\?|policy|legal|help)", url, re.I):
                return None
            if platform == "linkedin" and not re.match(r"(company|school|showcase)/[^/]+", path):
                return None
            if platform == "facebook" and re.match(r"(groups|events|photo|story|permalink)", path):
                return None
            if platform == "youtube" and not re.match(r"(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)", path):
                return None
            handle = path.split("/")[1] if platform == "linkedin" and "/" in path else path.split("/")[0]
            return platform, f"https://{'www.' if platform != 'x' else ''}{doms[0]}/{path.split('?')[0].rstrip('/')}", handle
    return None


def _handle_matches(handle, name_tokens_list, brand_tokens):
    hc = fold(handle).replace("-", "").replace("_", "").replace(".", "")
    for toks in (name_tokens_list, brand_tokens):
        if toks and "".join(toks) and "".join(toks) in hc:
            return True
        if len(toks) == 1 and toks[0] in hc and len(toks[0]) >= 4:
            return True
        if len(toks) >= 2 and sum(t in hc for t in toks) / len(toks) >= 0.75:
            return True
    return False


def _date(s):
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")[:25]).date().isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


MONTHS = {m: i + 1 for i, names in enumerate([
    ("januar", "january", "jan"), ("februar", "february", "feb"), ("mars", "march", "mar"), ("april", "apr"),
    ("mai", "may"), ("juni", "june", "jun"), ("juli", "july", "jul"), ("august", "aug"),
    ("september", "sept", "sep"), ("oktober", "october", "okt", "oct"), ("november", "nov"),
    ("desember", "december", "des", "dec")]) for m in names}
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.\s?(20\d{2})(?!\d)|(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)|"
                     r"(?<!\d)(\d{1,2})\.?\s+(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?,?\s+(20\d{2})(?!\d)", re.I)


NEWS_PATH = re.compile(r"/(nyhet\w*|news|aktuelt|blogg?|artikler|artikkel|article|post|presse\w*|media|20\d\d)/", re.I)
PLACEHOLDER = re.compile(r"(testinnlegg|test ?post|hello world|hei verden|sample|eksempel|lorem ipsum|untitled|uten tittel|"
                         r"\bnr\.? ?\d+$|^\W*$)", re.I)
TRAILER = re.compile(r"\s*(les mer|lese mer|read more|view project|se mer|mer info)\b.*$", re.I)


def clean_title(t):
    t = TRAILER.sub("", " ".join(html_lib.unescape(t or "").split()))
    m = DATE_RE.search(t)
    if m and m.start() > 10:
        t = t[:m.start()]
    return t.strip(" -–|:·")


def _article_like(href, title, strict=True):
    path = urlparse(href).path.lower()
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    url_ok = not strict or NEWS_PATH.search(path + "/") or slug.count("-") >= 2
    return bool(url_ok) and len(title.split()) >= 3 and not PLACEHOLDER.search(fold(title))


def dated_links(page, own_domain, page_url, strict=True):
    """Pair article-like links with the nearest date printed next to them."""
    raw = "".join(page.text)
    out, used = [], set()
    links = [(h, clean_title(t), span) for h, t, span in page.link_pos
             if 20 <= len(t) <= 200 and reg_domain(host_of(h)) == own_domain and urlparse(h).path.strip("/")
             and h.split("#")[0].rstrip("/") != page_url.rstrip("/") and not GENERIC_LINK.search(fold(t))]
    links = [l for l in links if _article_like(l[0], l[1], strict)]
    if not links:
        return out
    dates = []
    for m in DATE_RE.finditer(raw):
        try:
            if m.group(1):
                d = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            elif m.group(4):
                d = datetime(int(m.group(4)), int(m.group(5)), int(m.group(6)))
            else:
                d = datetime(int(m.group(9)), MONTHS[m.group(8).lower()], int(m.group(7)))
        except (ValueError, KeyError):
            continue
        dates.append((m.start(), m.end(), d.date().isoformat()))
    if not dates:
        return out
    def dist(link, date):
        (ls, le), (ds, de) = link[2], date[:2]
        return 0 if ds < le and de > ls else min(abs(ds - le), abs(ls - de))

    # mutual nearest neighbours only: the date's closest link must have that date as its closest date
    for i, dt in enumerate(dates):
        li = min(range(len(links)), key=lambda k: dist(links[k], dt))
        if dist(links[li], dt) > 150:
            continue
        di = min(range(len(dates)), key=lambda k: dist(links[li], dates[k]))
        if di != i:
            continue
        h, t, (ls, le) = links[li]
        ds, de, iso = dt
        out.append((h, t, iso, " ".join(raw[max(0, min(ds, ls) - 20):max(de, le) + 20].split())[:280]))
    return out


def extract(L, site, ctx):
    """Publish claims from a verified site into ledger L."""
    home_resp, home = site.pages[0][1], site.pages[0][2]
    proof_resp, proof_span, proof_method = site.proof
    final = home_resp.final_url
    dom = reg_domain(host_of(final))
    ev_proof = L.add_evidence(proof_resp, CO, proof_span, proof_method)
    conf = site.score
    L.claim("website", "official_website", {"url": final, "domain": host_of(final), "found_via": site.origin,
                                            "identity_proof": site.verification},
            [ev_proof], confidence=conf, key=host_of(final))

    structured = [d for _, r, p in site.pages if p for d in p.structured()]
    orgs = [d for d in structured if any(t in ORG_TYPES or t.endswith("business") for t in _types(d))]

    def ev_text(resp, span, method="html_extract", selector=None):
        return L.add_evidence(resp, CO, span[:300], method, selector=selector)

    # public brand
    brand = None
    for d in orgs:
        if isinstance(d.get("name"), str) and fold(d["name"]) != fold(ctx.get("name")):
            brand = d["name"]
            break
    brand = brand or home.meta.get("og:site_name")
    if brand and re.fullmatch(r"(https?://)?(www\.)?[\w.-]+\.[a-z]{2,}/?", brand.strip().lower()):
        brand = None  # a bare domain is not a brand name
    if brand and len(brand) <= 80 and set(tokens(brand)) != set(tokens(ctx.get("name"))):
        L.claim("identity", "public_brand", brand, [ev_text(home_resp, f'site name: "{brand}"', "meta_or_jsonld")],
                confidence=conf, note="Brand/trading name used on the verified company website")
    brand_tokens = tokens(brand) if brand else []

    # description (company-owned, promotional copy)
    desc = home.meta.get("description") or home.meta.get("og:description")
    if not desc:
        for d in orgs:
            if isinstance(d.get("description"), str):
                desc = d["description"]
                break
    if desc and len(desc.strip()) >= 20:
        desc = " ".join(desc.split())[:600]
        L.claim("identity", "website_description", desc, [ev_text(home_resp, desc, "meta_description")],
                confidence=conf, note="Company's own description of its business (company-owned copy, not an independent assessment)")
    if home.title.strip():
        L.claim("website", "website_title", " ".join(home.title.split())[:200],
                [ev_text(home_resp, "<title>" + " ".join(home.title.split())[:200], "html_title")], confidence=conf)

    # social profiles
    same_as = set()
    for d in orgs:
        sa = d.get("sameAs")
        for u in (sa if isinstance(sa, list) else [sa] if isinstance(sa, str) else []):
            same_as.add(u.rstrip("/"))
    seen_platforms = {}
    rejected = 0
    for kind, resp, p in site.pages:
        if not p:
            continue
        for href, text in p.links + [(u, "sameAs") for u in same_as]:
            s = _social(href)
            if not s:
                continue
            platform, url, handle = s
            declared = href.rstrip("/") in same_as
            if not (declared or _handle_matches(handle, tokens(ctx.get("name")), brand_tokens)):
                rejected += 1
                continue
            if platform in seen_platforms:
                continue
            seen_platforms[platform] = url
            how = "declared in the site's structured data (sameAs)" if declared else "handle matches the company name"
            L.claim("social_profiles", "social_profile", {"platform": platform, "url": url, "handle": handle,
                                                          "linked_from": resp.final_url},
                    [ev_text(resp, f'link to {href}' + (f' ("{text}")' if text and text != "sameAs" else ""), "html_anchor", "a[href]")],
                    confidence=min(conf, 0.97 if declared else 0.93), key=platform, note=f"Linked from the verified website; {how}")
    if not seen_platforms:
        L.section("social_profiles", "not_available",
                  "No social profile that resolves to this company is linked from the verified website"
                  + (f" ({rejected} unrelated or unverifiable social links ignored)." if rejected else "."),
                  checked=[r.final_url for _, r, _ in site.pages])

    # contact details on the verified site
    emails, phones = {}, {}
    for kind, resp, p in site.pages:
        if not p:
            continue
        for href, text in p.links:
            if href.lower().startswith("mailto:"):
                e = href[7:].split("?")[0].strip().lower()
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e) and (reg_domain(e.split("@")[1]) == dom or e in [x.lower() for x in ctx["emails"]]):
                    emails.setdefault(e, (resp, href))
            elif href.lower().startswith("tel:"):
                d = re.sub(r"[^\d+]", "", href[4:])
                if len(re.sub(r"\D", "", d)) >= 8:
                    phones.setdefault(d, (resp, href))
    reg_emails = {x.lower() for x in ctx["emails"]}
    for e, (resp, href) in sorted(emails.items(), key=lambda kv: (kv[0] not in reg_emails, kv[0]))[:3]:
        L.claim("contact", "website_email", e, [ev_text(resp, f"mailto link: {href}", "html_anchor", "a[href^=mailto]")],
                confidence=conf, key=e)
    for ph, (resp, href) in sorted(phones.items())[:3]:
        L.claim("contact", "website_phone", ph, [ev_text(resp, f"tel link: {href}", "html_anchor", "a[href^=tel]")],
                confidence=conf, key=re.sub(r"\D", "", ph)[-8:])
    for d in orgs:
        if isinstance(d.get("telephone"), str):
            L.claim("contact", "website_phone", d["telephone"], [ev_text(home_resp, f'JSON-LD telephone: {d["telephone"]}', "jsonld")],
                    confidence=conf, key=re.sub(r"\D", "", d["telephone"])[-8:])
        if isinstance(d.get("email"), str):
            L.claim("contact", "website_email", d["email"].lower(), [ev_text(home_resp, f'JSON-LD email: {d["email"]}', "jsonld")],
                    confidence=conf, key=d["email"].lower())

    # locations declared on the site (structured data only)
    for d in orgs:
        addrs = d.get("address")
        for a in (addrs if isinstance(addrs, list) else [addrs] if isinstance(addrs, dict) else []):
            parts = [a.get("streetAddress"), " ".join(x for x in (a.get("postalCode"), a.get("addressLocality")) if x)]
            text = ", ".join(str(x) for x in parts if x)
            if text:
                L.claim("locations", "website_location", {"address": text, "name": d.get("name")},
                        [ev_text(home_resp, f"JSON-LD address: {text}", "jsonld")], confidence=conf, key=text)
        if isinstance(d.get("foundingDate"), str):
            L.claim("identity", "website_founding_date", d["foundingDate"], [ev_text(home_resp, f"JSON-LD foundingDate: {d['foundingDate']}", "jsonld")], confidence=conf)

    # hiring: careers page, external career portal and individual job ads
    careers = [(r, p) for k, r, p in site.pages if k == "careers" and p and JOB_WORDS.search(fold(p.title + " " + " ".join(p.h1)))]
    job_links, portals = {}, {}
    for kind, resp, p in site.pages:
        if not p:
            continue
        for href, text in p.links:
            title = " ".join(text.split())
            if is_job_ad_url(href, dom) and 6 <= len(title) <= 140 and not GENERIC_LINK.search(fold(title)) \
                    and len(title.split()) >= 1 and not re.search(r"(soknad|\bcv\b|\bher\b|apply|sok na)", fold(title)):
                job_links.setdefault(href.split("#")[0], (resp, title))
            elif any(a in href.lower() for a in ATS_DOMAINS) and reg_domain(host_of(href)) != dom:
                portals.setdefault(host_of(href), (resp, href, title))
    if careers:
        r, p = careers[0]
        L.claim("jobs", "careers_page", r.final_url, [ev_text(r, f"careers page title: {' '.join(p.title.split())[:120]}", "html_title")],
                confidence=conf)
    for host, (resp, href, title) in sorted(portals.items())[:2]:
        L.claim("jobs", "careers_portal", {"url": href, "provider": host}, [ev_text(resp, f'link to career portal {href}' + (f' ("{title}")' if title else ""), "html_anchor", "a[href]")],
                confidence=min(conf, 0.9), key=host, note="Recruitment portal linked from the verified website")
    for href, (resp, title) in sorted(job_links.items())[:15]:
        L.claim("jobs", "job_posting", {"title": title, "url": href, "source": "company website", "status": "listed"},
                [ev_text(resp, f'job ad link "{title}" -> {href}', "html_anchor", "a[href]")], confidence=min(conf, 0.9), key=href)

    # dated activity: RSS/Atom, article markup and JSON-LD articles
    items = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=730)).date().isoformat()

    class _Items(dict):
        def setdefault(self, key, value):  # exact dates (feeds, APIs, markup) replace heuristic ones
            cur = self.get(key)
            if cur is None or (cur[3] == "html_date_proximity" and value[3] != "html_date_proximity"):
                self[key] = value
            return self[key]
    items = _Items()
    for kind, resp, p in site.pages:
        if kind == "feed":
            try:
                root = ET.fromstring(resp.body)
            except ET.ParseError:
                continue
            for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
                def g(tag):
                    el = it.find(tag)
                    return el.text.strip() if el is not None and el.text else None
                title = g("title") or g("{http://www.w3.org/2005/Atom}title")
                link = g("link")
                if not link:
                    le = it.find("{http://www.w3.org/2005/Atom}link")
                    link = le.get("href") if le is not None else None
                date = _date(g("pubDate") or g("{http://www.w3.org/2005/Atom}published") or g("{http://www.w3.org/2005/Atom}updated"))
                if title and date and link:
                    items.setdefault(link, (resp, title, date, "rss_or_atom_item", f"<item><title>{title}</title><pubDate>{date}"))
            continue
        if kind == "wp_posts":
            try:
                posts = json.loads(resp.body.decode("utf-8"))
            except ValueError:
                continue
            for i, post in enumerate(posts if isinstance(posts, list) else []):
                title = re.sub(r"<[^>]+>", "", ((post.get("title") or {}).get("rendered") or "")).strip()
                d = _date(post.get("date"))
                if title and d and post.get("link"):
                    items.setdefault(post["link"], (resp, title, d, "wordpress_rest_api", f"/{i}/date = {post.get('date')}; title = {title[:120]}"))
            continue
        if not p:
            continue
        if kind in ("news", "homepage"):
            for href, title, d, span in dated_links(p, dom, resp.final_url, strict=(kind == "homepage")):
                items.setdefault(href.split("#")[0], (resp, title, d, "html_date_proximity", span))
        for a in p.articles:
            d = _date(a.get("date"))
            if a.get("title") and a.get("href") and d:
                items.setdefault(a["href"], (resp, a["title"], d, "html_article_time", f"<article> {a['title']} <time datetime={a['date']}>"))
        for d in p.structured():
            if any(t in ("newsarticle", "blogposting", "article", "pressrelease") for t in _types(d)):
                dt = _date(d.get("datePublished") if isinstance(d.get("datePublished"), str) else None)
                title = d.get("headline") or d.get("name")
                url = d.get("url") or (d.get("mainEntityOfPage") if isinstance(d.get("mainEntityOfPage"), str) else None) or resp.final_url
                if dt and isinstance(title, str):
                    items.setdefault(url, (resp, title, dt, "jsonld_article", f"JSON-LD {title} datePublished {dt}"))
    today = datetime.now(timezone.utc).date().isoformat()
    items = {k: (v[0], clean_title(v[1]), *v[2:]) for k, v in items.items()}
    items = {k: v for k, v in items.items() if len(v[1]) >= 8 and not PLACEHOLDER.search(fold(v[1]))}
    recent = sorted(((k, v) for k, v in items.items() if cutoff <= v[2] <= today), key=lambda kv: kv[1][2], reverse=True)
    for url, (resp, title, date, method, span) in recent[:10]:
        L.claim("activity", "company_news", {"title": " ".join(title.split())[:200], "url": url, "date": date,
                                             "publisher": "company website"},
                [ev_text(resp, span, method)], confidence=min(conf, 0.9), key=url, effective_date=date)
    if not recent:
        L.section("activity", "not_available", "No dated news, blog or feed items from the last two years on the verified website.",
                  checked=[r.final_url for _, r, _ in site.pages])
    return brand
