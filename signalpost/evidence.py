"""Claims and evidence ledger for one company envelope.

A claim is one supported fact (or an explicit absence). Every published claim
points to evidence records holding the source URL, final URL and redirect
chain, retrieval time, SHA-256 of the captured body, the exact span or JSON
pointer the value was read from, the extraction method and, where relevant,
the reporting period.
"""
import json
import re

from . import __version__

STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")

SOURCE_CLASSES = {
    "official_registry": "Brønnøysundregistrene – Enhetsregisteret (NLOD 2.0)",
    "official_accounts": "Brønnøysundregistrene – Regnskapsregisteret (NLOD 2.0)",
    "company_owned": "Company-owned website (identity-verified)",
    "public_api": "NAV arbeidsplassen public job-vacancy feed",
}

# Section order follows the participant brief's required envelope sections.
SECTIONS = [
    ("identity", "Legal identity and public brand"),
    ("financials", "Latest annual accounts and history"),
    ("leadership", "Leadership and roles"),
    ("locations", "Registered workplaces and locations"),
    ("group", "Group relationships"),
    ("website", "Verified official website"),
    ("social_profiles", "Company-owned social profiles"),
    ("contact", "Contact details"),
    ("jobs", "Hiring"),
    ("activity", "Dated public activity"),
]


def pointer_value(data, pointer):
    node = data
    for part in [p for p in pointer.split("/") if p]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def short_json(value, limit=300):
    s = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= limit else s[: limit - 1] + "…"


def norm_key(s):
    return re.sub(r"\s+", " ", str(s or "").strip().casefold())


class Ledger:
    def __init__(self, orgnr):
        self.orgnr = orgnr
        self.claims = []
        self.evidence = []
        self.errors = []
        self.sections = {}
        self._keys = set()

    # -- evidence -------------------------------------------------------------------
    def add_evidence(self, resp, source_class, span, method, selector=None, period=None, url=None):
        ev = {
            "id": f"ev-{len(self.evidence) + 1}",
            "source_url": url or resp.url,
            "final_url": resp.final_url,
            "source_class": source_class,
            "source_name": SOURCE_CLASSES.get(source_class),
            "http_status": resp.status,
            "retrieved_at": resp.retrieved_at,
            "content_sha256": resp.sha256,
            "claim_span": span,
            "extraction_method": method,
            "extractor_version": __version__,
        }
        if resp.redirects:
            ev["redirect_chain"] = resp.redirects
        if selector:
            ev["selector"] = selector
        if period:
            ev["reporting_period"] = period
        self.evidence.append(ev)
        return ev["id"]

    def json_evidence(self, resp, data, pointer, source_class, period=None):
        """Evidence for a value read from a JSON API response at `pointer`."""
        try:
            val = pointer_value(data, pointer)
        except (KeyError, IndexError, TypeError):
            val = None
        return self.add_evidence(resp, source_class, f"{pointer} = {short_json(val)}", "json_pointer",
                                 selector=pointer, period=period)

    # -- claims ---------------------------------------------------------------------
    def claim(self, section, field, value, evidence_ids, confidence=1.0, key=None, period=None,
              effective_date=None, note=None, availability="available", **extra):
        if value is None or value == "" or value == [] or value == {}:
            return None
        ckey = f"{field}:{norm_key(key if key is not None else '')}"
        if ckey in self._keys:
            return None
        self._keys.add(ckey)
        c = {
            "id": f"cl-{len(self.claims) + 1}",
            "key": ckey,
            "section": section,
            "field": field,
            "value": value,
            "availability": availability,
            "confidence": round(confidence, 3),
            "evidence_ids": list(evidence_ids),
        }
        if period:
            c["reporting_period"] = period
        if effective_date:
            c["effective_date"] = effective_date
        if note:
            c["note"] = note
        c.update({k: v for k, v in extra.items() if v is not None})
        self.claims.append(c)
        self.section(section, "available")
        return c

    def absent(self, section, field, availability, reason, evidence_ids=()):
        """An explicit, checked absence (never a silent zero)."""
        ckey = f"{field}:"
        if ckey in self._keys:
            return None
        self._keys.add(ckey)
        c = {"id": f"cl-{len(self.claims) + 1}", "key": ckey, "section": section, "field": field, "value": None,
             "availability": availability, "confidence": 1.0, "evidence_ids": list(evidence_ids), "note": reason}
        self.claims.append(c)
        return c

    def section(self, name, state, reason=None, checked=None, force=False):
        assert state in STATES, state
        cur = self.sections.get(name)
        if cur and cur["state"] == "available" and not force:
            return
        s = {"state": state}
        if reason:
            s["reason"] = reason
        if checked:
            s["sources_checked"] = checked
        self.sections[name] = s

    def error(self, source, err, url=None, retryable=True):
        self.errors.append({"source": source, "url": url, "error": str(err)[:300], "retryable": retryable})

    def values(self, field):
        return [c["value"] for c in self.claims if c["field"] == field and c["availability"] == "available"]

    def first(self, field):
        v = self.values(field)
        return v[0] if v else None
