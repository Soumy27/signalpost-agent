"""Company website enrichment.

Only the homepage is read (plus robots.txt, which is always honoured). Content
from a site is published only if the site can be tied to the company: the
organisation number appears on the page (strong) or the company's distinctive
name does (medium). Otherwise only the register-listed URL is kept.
"""
import json
import re
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urlparse

from . import orgnr as orgnr_mod
from .http import USER_AGENT, BudgetExceeded

FREEMAIL = {
    "gmail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no",
    "yahoo.com", "yahoo.no", "icloud.com", "me.com", "msn.com", "online.no", "frisurf.no",
    "start.no", "broadpark.no", "lyse.net", "altibox.no", "c2i.net", "getmail.no", "tele2.no",
    "telenor.no", "haugnett.no", "enivest.net", "ebnett.no", "bluezone.no", "loqal.no",
    "neasonline.no", "tdcnet.no", "vikenfiber.no", "aol.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.net", "windowslive.com", "hotmail.co.uk", "mail.com", "yandex.com", "googlemail.com", "post.no", "sensewave.no",
}
SOCIAL = ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "tiktok.com")
LEGAL_SUFFIXES = {"as", "asa", "ans", "da", "enk", "sa", "nuf", "ba", "ks", "sf", "stiftelse", "forening",
                  "avd", "avdeling", "og", "the", "and", "i", "norge", "norway", "holding", "group", "gruppen"}


def normalize_url(raw):
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    raw = re.sub(r"^(https?://)?www-", r"\1www.", raw)  # common typo: "www-foo.no"
    if not re.match(r"^https?://", raw):
        raw = "https://" + raw
    host = urlparse(raw).hostname
    if not host or "." not in host:
        return None
    return raw


def domain_from_email(email):
    if not email or "@" not in email:
        return None
    dom = email.rsplit("@", 1)[1].strip().lower()
    if dom in FREEMAIL or "." not in dom:
        return None
    return "https://" + dom


def name_tokens(name):
    words = re.findall(r"[a-zæøå0-9]+", (name or "").lower())
    return [w for w in words if len(w) >= 3 and w not in LEGAL_SUFFIXES]


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta = {}
        self.links = set()
        self.jsonld = []
        self.text = []
        self._in = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("title", "script", "style"):
            self._in = (tag, a.get("type", ""))
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"].strip()
        elif tag == "a" and a.get("href"):
            self.links.add(a["href"].strip())

    def handle_endtag(self, tag):
        if self._in and tag == self._in[0]:
            self._in = None

    def handle_data(self, data):
        if not self._in:
            self.text.append(data)
        elif self._in[0] == "title":
            self.title += data
        elif self._in[0] == "script" and "ld+json" in self._in[1]:
            self.jsonld.append(data)


def _robots_allows(client, url):
    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        r = client.get(robots_url, timeout=6, retries=0, accept="text/plain")
    except BudgetExceeded:
        raise
    except Exception:
        return True  # unreachable robots.txt -> no restrictions published
    if r.status >= 400:
        return True
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(r.text().splitlines())
    return rp.can_fetch(USER_AGENT, url)


def _socials(links):
    out = {}
    for href in links:
        host = (urlparse(href).hostname or "").lower()
        for s in SOCIAL:
            if host == s or host.endswith("." + s):
                key = s.split(".")[0]
                path = urlparse(href).path.strip("/")
                if path and key not in out and not re.search(r"(share|sharer|intent|login)", href):
                    out[key] = href
    return out


def inspect_site(client, url, orgnr, company_name):
    """Fetch a homepage and decide whether it belongs to the company."""
    result = {"requested_url": url, "verification": "unreachable"}
    if not _robots_allows(client, url):
        result["verification"] = "blocked_by_robots_txt"
        return result
    resp = None
    for candidate in dict.fromkeys((url, url.replace("https://", "http://", 1))):
        try:
            resp = client.get(candidate, timeout=8, retries=0, accept="text/html,application/xhtml+xml")
            if resp.status < 400:
                break
        except BudgetExceeded:
            raise
        except Exception:
            resp = None
        if not candidate.startswith("https://"):
            break
    if not resp or resp.status >= 400:
        result["http_status"] = resp.status if resp else None
        return result

    html = resp.text()
    parser = _PageParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = " ".join(" ".join(parser.text).split())
    final_host = (urlparse(resp.final_url).hostname or "").lower()

    orgnr_found = bool(orgnr_mod.pattern(orgnr).search(html))
    other_orgnrs = set()
    for m in re.finditer(r"(?<!\d)([89]\d{2})[\s. ]?(\d{3})[\s. ]?(\d{3})(?!\d)", text):
        cand = "".join(m.groups())
        if cand != orgnr and orgnr_mod.is_valid(cand) and re.search(r"(org|mva|foretaks)", text[max(0, m.start() - 40):m.start()], re.I):
            other_orgnrs.add(cand)

    tokens = name_tokens(company_name)
    haystack = " ".join([parser.title, parser.meta.get("og:site_name", ""), final_host, text[:20000]]).lower()
    name_hits = [t for t in tokens if t in haystack]
    name_match = bool(tokens) and len(name_hits) >= max(1, (len(tokens) + 1) // 2)

    if orgnr_found:
        verification = "orgnr_on_site"
    elif name_match and not other_orgnrs:
        verification = "name_on_site"
    elif other_orgnrs:
        verification = "different_orgnr_on_site"
    else:
        verification = "not_verified"

    description = parser.meta.get("description") or parser.meta.get("og:description")
    for blob in parser.jsonld:
        try:
            data = json.loads(blob)
        except Exception:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and not description and item.get("description"):
                description = str(item["description"])

    result.update({
        "final_url": resp.final_url,
        "http_status": resp.status,
        "verification": verification,
        "orgnr_found_on_page": orgnr_found,
        "other_orgnrs_on_page": sorted(other_orgnrs),
        "name_tokens_matched": name_hits,
        "title": " ".join(parser.title.split())[:200] or None,
        "description": " ".join(description.split())[:500] if description else None,
        "social_profiles": _socials(parser.links),
    })
    return result
