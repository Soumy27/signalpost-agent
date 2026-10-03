"""Brønnøysundregistrene open data (NLOD 2.0, no key).

Enhetsregisteret: https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html
Regnskapsregisteret: https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}
"""
ER = "https://data.brreg.no/enhetsregisteret/api"
RR = "https://data.brreg.no/regnskapsregisteret/regnskap"


def get_json(client, url, **kw):
    r = client.get(url, **kw)
    data = None
    if r.status == 200:
        try:
            data = r.json()
        except ValueError:
            data = None
    return r, data


def fetch_entity(client, orgnr):
    """(kind, data, response); kind: enhet | underenhet | deleted | not_found."""
    r, data = get_json(client, f"{ER}/enheter/{orgnr}")
    if data is not None:
        deleted = data.get("respons_klasse") == "SlettetEnhet" or data.get("slettedato")
        return ("deleted" if deleted else "enhet"), data, r
    if r.status == 410:
        return "deleted", {"organisasjonsnummer": orgnr}, r
    if r.status != 404:
        raise RuntimeError(f"Enhetsregisteret HTTP {r.status}")
    r2, data2 = get_json(client, f"{ER}/underenheter/{orgnr}")
    if data2 is not None:
        deleted = str(data2.get("respons_klasse", "")).startswith("Slettet") or data2.get("slettedato")
        return ("deleted" if deleted else "underenhet"), data2, r2
    if r2.status == 410:
        return "deleted", {"organisasjonsnummer": orgnr}, r2
    return "not_found", None, r


def fetch_roles(client, orgnr):
    return get_json(client, f"{ER}/enheter/{orgnr}/roller")


def fetch_subunits(client, orgnr, size=50):
    return get_json(client, f"{ER}/underenheter?overordnetEnhet={orgnr}&size={size}")


def fetch_accounts(client, orgnr):
    return get_json(client, f"{RR}/{orgnr}")


def fetch_group(client, orgnr):
    return get_json(client, f"{ER}/konsernstruktur/{orgnr}")


def fetch_parent(client, orgnr):
    return get_json(client, f"{ER}/enheter/{orgnr}")


# ---- change feeds (used to decide which stored profiles to re-check) ----------------

def changed_entities_since(client, since_iso, max_pages=50):
    changed = {}
    url = f"{ER}/oppdateringer/enheter?dato={since_iso}&size=10000"
    for _ in range(max_pages):
        r, data = get_json(client, url, timeout=60, max_bytes=50_000_000)
        if data is None:
            break
        items = data.get("_embedded", {}).get("oppdaterteEnheter", [])
        for item in items:
            changed[item["organisasjonsnummer"]] = item.get("endringstype")
        if len(items) < 10000:
            break
        url = f"{ER}/oppdateringer/enheter?oppdateringsid={items[-1]['oppdateringsid'] + 1}&size=10000"
    return changed


def changed_roles_since(client, since_iso, max_pages=50):
    changed, after_id = set(), None
    for _ in range(max_pages):
        q = f"afterId={after_id}" if after_id else f"afterTime={since_iso}"
        r, events = get_json(client, f"{ER}/oppdateringer/roller?size=10000&{q}", timeout=60, max_bytes=50_000_000)
        if not events:
            break
        for ev in events:
            changed.add(ev.get("data", {}).get("organisasjonsnummer"))
        after_id = events[-1]["id"]
        if len(events) < 10000:
            break
    changed.discard(None)
    return changed


# ---- frozen registry snapshot (optional identity fallback) ---------------------------

BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def _nest(row):
    out = {}
    for k, v in row.items():
        if k is None or v in (None, ""):
            continue
        if v in ("true", "false"):
            v = v == "true"
        elif k in ("antallAnsatte", "kapital.antallAksjer"):
            v = int(float(v)) if v.replace(".", "", 1).isdigit() else v
        elif k == "kapital.belop":
            try:
                v = float(v)
            except ValueError:
                pass
        parts = k.split(".")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v
    for a in ("forretningsadresse", "postadresse"):
        if isinstance(out.get(a), dict) and isinstance(out[a].get("adresse"), str):
            out[a]["adresse"] = [x.strip() for x in out[a]["adresse"].split("\n") if x.strip()]
    for k in ("vedtektsfestetFormaal", "aktivitet"):
        if isinstance(out.get(k), str):
            out[k] = [out[k]]
    for k in ("registreringsdatoenhetsregisteret", "registreringsdatoantallansatteNAVAaregisteret"):
        if k in out:
            out[{"registreringsdatoenhetsregisteret": "registreringsdatoEnhetsregisteret",
                 "registreringsdatoantallansatteNAVAaregisteret": "registreringsdatoAntallAnsatteNAVAaregisteret"}[k]] = out.pop(k)
    return out


def load_bulk(path, orgnrs):
    """Rows for the requested organisation numbers from a Brreg CSV snapshot
    (.csv or .csv.gz). Returns (rows_by_orgnr, snapshot_sha256)."""
    import csv
    import gzip
    import hashlib
    wanted, rows = set(orgnrs), {}
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as f:
        sample = f.read(8192)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        for row in csv.DictReader(f, dialect=dialect):
            o = (row.get("organisasjonsnummer") or "").strip()
            if o in wanted:
                rows[o] = _nest(row)
                if len(rows) == len(wanted):
                    break
    return rows, h.hexdigest()
