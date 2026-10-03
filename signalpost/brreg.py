"""Brønnøysundregistrene open data APIs (no key required, NLOD licence).

- Enhetsregisteret:   https://data.brreg.no/enhetsregisteret/api/docs/index.html
- Regnskapsregisteret: https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}
"""
from datetime import datetime, timezone

ER = "https://data.brreg.no/enhetsregisteret/api"
RR = "https://data.brreg.no/regnskapsregisteret/regnskap"

SOURCE_NAMES = {
    "brreg_entity": "Brønnøysundregistrene – Enhetsregisteret (enhet)",
    "brreg_subunit": "Brønnøysundregistrene – Enhetsregisteret (underenhet)",
    "brreg_parent": "Brønnøysundregistrene – Enhetsregisteret (overordnet enhet)",
    "brreg_roles": "Brønnøysundregistrene – Enhetsregisteret (roller)",
    "brreg_subunits": "Brønnøysundregistrene – Enhetsregisteret (underenheter-søk)",
    "brreg_accounts": "Brønnøysundregistrene – Regnskapsregisteret",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _source(sid, resp, url=None):
    return {
        "id": sid,
        "name": SOURCE_NAMES[sid],
        "url": url or resp.url,
        "http_status": resp.status,
        "retrieved_at": now_iso(),
    }


def fetch_entity(client, orgnr):
    """Returns (kind, data, source). kind: enhet | underenhet | deleted | not_found."""
    r = client.get(f"{ER}/enheter/{orgnr}")
    if r.status == 200:
        data = r.json()
        kind = "deleted" if data.get("respons_klasse") == "SlettetEnhet" or data.get("slettedato") else "enhet"
        return kind, data, _source("brreg_entity", r)
    if r.status == 410:
        return "deleted", {"organisasjonsnummer": orgnr}, _source("brreg_entity", r)
    if r.status != 404:
        raise RuntimeError(f"Enhetsregisteret returned HTTP {r.status} for {orgnr}")
    r2 = client.get(f"{ER}/underenheter/{orgnr}")
    if r2.status == 200:
        data = r2.json()
        if data.get("respons_klasse", "").startswith("Slettet") or data.get("slettedato"):
            return "deleted", data, _source("brreg_subunit", r2)
        return "underenhet", data, _source("brreg_subunit", r2)
    if r2.status == 410:
        return "deleted", {"organisasjonsnummer": orgnr}, _source("brreg_subunit", r2)
    return "not_found", None, _source("brreg_entity", r)


def fetch_parent(client, orgnr):
    r = client.get(f"{ER}/enheter/{orgnr}")
    if r.status != 200:
        return None, None
    return r.json(), _source("brreg_parent", r)


def fetch_roles(client, orgnr):
    r = client.get(f"{ER}/enheter/{orgnr}/roller")
    if r.status != 200:
        return None, _source("brreg_roles", r)
    return r.json(), _source("brreg_roles", r)


def fetch_subunits(client, orgnr, size=20):
    url = f"{ER}/underenheter?overordnetEnhet={orgnr}&size={size}"
    r = client.get(url)
    if r.status != 200:
        return None, _source("brreg_subunits", r)
    return r.json(), _source("brreg_subunits", r)


def fetch_accounts(client, orgnr):
    r = client.get(f"{RR}/{orgnr}")
    if r.status == 404:
        return [], _source("brreg_accounts", r)
    if r.status != 200:
        return None, _source("brreg_accounts", r)
    data = r.json()
    return (data if isinstance(data, list) else [data]), _source("brreg_accounts", r)


# ---- change feeds (used by `refresh`) -------------------------------------------------

def changed_entities_since(client, since_iso, max_pages=50):
    """orgnr -> latest change type from the Enhetsregisteret update feed."""
    changed = {}
    url = f"{ER}/oppdateringer/enheter?dato={since_iso}&size=10000"
    for _ in range(max_pages):
        r = client.get(url, timeout=60, max_bytes=50_000_000)
        if r.status != 200:
            break
        items = r.json().get("_embedded", {}).get("oppdaterteEnheter", [])
        for item in items:
            changed[item["organisasjonsnummer"]] = item.get("endringstype")
        if len(items) < 10000:
            break
        # page by update id rather than page number (deep paging is capped)
        url = f"{ER}/oppdateringer/enheter?oppdateringsid={items[-1]['oppdateringsid'] + 1}&size=10000"
    return changed


def changed_roles_since(client, since_iso, max_pages=50):
    changed = set()
    after_id = None
    for _ in range(max_pages):
        url = f"{ER}/oppdateringer/roller?size=10000&" + (f"afterId={after_id}" if after_id else f"afterTime={since_iso}")
        r = client.get(url, timeout=60, max_bytes=50_000_000)
        if r.status != 200:
            break
        events = r.json()
        if not events:
            break
        for ev in events:
            changed.add(ev.get("data", {}).get("organisasjonsnummer"))
        after_id = events[-1]["id"]
        if len(events) < 10000:
            break
    changed.discard(None)
    return changed
