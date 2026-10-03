"""Refresh state: previous supported values, typed changes, preserved evidence.

The store keeps, per company, the last envelope's claims and evidence plus a
change log. A refresh compares claims by stable key and emits typed changes
(new_role, new_filing, new_job, closed_job, changed_address, ...) that carry
both the previous and the current evidence. Re-running on the same source
snapshot yields no changes (idempotent). When a volatile source (website, job
feed) fails or is skipped on a refresh, the last supported value is kept and
flagged stale instead of being reported as removed.
"""
import gzip
import hashlib
import json
from pathlib import Path

VOLATILE_SECTIONS = {"website", "social_profiles", "contact", "jobs", "activity"}
CHANGE_TYPES = {
    "chief_executive": "role", "board_chair": "role", "board_deputy_chair": "role", "board_member": "role",
    "board_deputy_member": "role", "auditor": "role", "accountant": "role", "owner": "role", "partner": "role",
    "contact_person": "role", "signatory": "role", "procuration_holder": "role", "board_observer": "role",
    "annual_accounts_filing": "filing", "job_posting": "job", "registered_workplace": "location",
    "website_location": "location", "company_news": "activity", "official_website": "website",
    "social_profile": "social_profile", "subsidiary": "subsidiary", "parent_company": "parent",
}
HIGH = {"registry_status", "legal_name", "chief_executive", "board_chair", "annual_accounts_filing",
        "registered_business_address", "parent_company", "legal_form", "owner", "auditor"}
SKIP_DIFF = {"registry_website_check"}


META_KEYS = {"found_via", "identity_proof", "linked_from", "latest"}


def _canon(v):
    """Comparison form of a value: run metadata dropped, URLs normalised."""
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items() if k not in META_KEYS}
    if isinstance(v, list):
        return [_canon(x) for x in v]
    if isinstance(v, str) and v.startswith(("http://", "https://")):
        return v.split("://", 1)[1].removeprefix("www.").rstrip("/").lower()
    return v


def _h(v):
    return hashlib.sha256(json.dumps(_canon(v), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _classes(claim, ev):
    return {ev[e].get("source_class") for e in claim.get("evidence_ids", []) if e in ev}


def _label(c):
    v = c.get("value")
    if isinstance(v, dict):
        for k in ("name", "title", "url", "text", "address"):
            if v.get(k):
                return str(v[k])
        if "amount" in v:
            return f"{v.get('currency', '')} {v['amount']:,.0f} ({v.get('fiscal_year')})"
        if "fiscal_year" in v:
            return f"{v.get('scope', '')} accounts {v['fiscal_year']}"
    return str(v)


def _type(field, kind):
    base = CHANGE_TYPES.get(field)
    if kind == "added":
        return {"job": "new_job", "filing": "new_filing", "role": "new_role", "location": "new_location",
                "activity": "new_activity", "website": "new_website", "social_profile": "new_social_profile",
                "subsidiary": "new_subsidiary", "parent": "changed_parent"}.get(base, f"new_{field}")
    if kind == "removed":
        return {"job": "closed_job", "role": "removed_role", "location": "closed_location",
                "website": "removed_website", "social_profile": "removed_social_profile",
                "subsidiary": "removed_subsidiary", "parent": "changed_parent", "activity": "activity_expired"}.get(base, f"removed_{field}")
    return {"role": "changed_role", "website": "changed_website", "location": "changed_location"}.get(base, f"changed_{field}")


class Store:
    def __init__(self, root):
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, orgnr):
        return self.dir / f"{orgnr}.json.gz"

    def load(self, orgnr):
        p = self.path(orgnr)
        if not p.exists():
            return None
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def all(self):
        return sorted(p.name.split(".")[0] for p in self.dir.glob("*.json.gz"))

    def save(self, orgnr, state):
        p = self.path(orgnr)
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(p)


def kind_of(ov, nv):
    return "added" if ov is None else "removed" if nv is None else "changed"


def refresh(store, env, run_id, now):
    """Diff env against the stored previous envelope; mutate env in place
    (changes, first/last observed, carried-forward stale claims, refresh
    metadata) and persist the new state."""
    orgnr = env["organisation_number"]
    prev = store.load(orgnr) or {}
    pclaims = {c["key"]: c for c in prev.get("claims", [])}
    pev = {e["id"]: e for e in prev.get("evidence", [])}
    first_seen = dict(prev.get("first_seen") or {})
    sections = env.get("sections", {})
    cur = {c["key"]: c for c in env["claims"]}
    changes = []

    # Carry forward volatile values instead of reporting removals that may be transient:
    #  * company-owned values when the website could not be checked on this run;
    #  * job-feed values when the job source failed;
    #  * any company-owned value missing for the first time (removal needs two consecutive misses).
    site_state = sections.get("website", {}).get("state")
    jobs_state = sections.get("jobs", {}).get("state")
    for key, pc in pclaims.items():
        if key in cur or pc.get("availability") != "available":
            continue
        cls = _classes(pc, pev)
        if "company_owned" in cls:
            if site_state in ("failed", "blocked", None):
                why = f"website not re-checked on this run ({site_state or 'unchecked'})"
            elif not pc.get("pending_removal"):
                why = "not seen on this run; removal is reported only after a second consecutive miss"
            else:
                continue
        elif "public_api" in cls and jobs_state in ("failed", None):
            why = f"job source not re-checked on this run ({jobs_state or 'unchecked'})"
        else:
            continue
        carried = dict(pc)
        carried["stale"] = True
        if "second consecutive" in why:
            carried["pending_removal"] = True
        carried["note"] = f"Last supported value kept: {why}."
        new_ids = []
        for eid in pc.get("evidence_ids", []):
            if eid in pev:
                e = dict(pev[eid])
                e["id"] = f"ev-{len(env['evidence']) + 1}"
                e.setdefault("carried_from_run", prev.get("run_id"))
                env["evidence"].append(e)
                new_ids.append(e["id"])
        carried["evidence_ids"] = new_ids
        carried["id"] = f"cl-{len(env['claims']) + 1}"
        env["claims"].append(carried)
        cur[key] = carried

    cev = {e["id"]: e for e in env["evidence"]}
    if prev:
        for key in sorted(set(pclaims) | set(cur)):
            o, n = pclaims.get(key), cur.get(key)
            field = (n or o)["field"]
            if field in SKIP_DIFF:
                continue
            ov = o["value"] if o and o.get("availability") == "available" else None
            nv = n["value"] if n and n.get("availability") == "available" else None
            if _h(ov) == _h(nv):
                continue
            if kind_of(ov, nv) == "removed" and n is not None and n.get("stale"):
                continue
            kind = "added" if ov is None else "removed" if nv is None else "changed"
            psecs = prev.get("sections") or {}
            ncls = _classes(n, cev) if n else set()
            governing = "website" if "company_owned" in ncls else "jobs" if "public_api" in ncls else (n or o)["section"]
            if kind == "added" and psecs.get(governing, {}).get("state") in ("failed", "blocked"):
                continue  # first successful observation after an unchecked run is not a change
            ctype = _type(field, kind)
            label_o, label_n = (_label(o) if ov is not None else None), (_label(n) if nv is not None else None)
            desc = {"added": f"{field.replace('_', ' ')} added: {label_n}",
                    "removed": f"{field.replace('_', ' ')} no longer listed: {label_o}",
                    "changed": f"{field.replace('_', ' ')} changed from {label_o} to {label_n}"}[kind]
            changes.append({
                "change_type": ctype,
                "claim_key": key,
                "field": field,
                "section": (n or o)["section"],
                "materiality": "high" if field in HIGH else "medium" if CHANGE_TYPES.get(field) else "low",
                "previous_value": ov,
                "current_value": nv,
                "previous_evidence": [pev[e] for e in (o or {}).get("evidence_ids", []) if e in pev],
                "current_evidence_ids": (n or {}).get("evidence_ids", []),
                "previous_observed_at": prev.get("completed_at"),
                "detected_at": now,
                "description": desc,
            })
    for key, c in cur.items():
        first_seen.setdefault(key, now)
        c["first_observed_at"] = first_seen[key]
        c["last_observed_at"] = (c.get("last_observed_at") or prev.get("completed_at")) if c.get("stale") else now
    order = {"high": 0, "medium": 1, "low": 2}
    changes.sort(key=lambda ch: (order[ch["materiality"]], ch["field"]))
    env["changes"] = changes

    hist = list(prev.get("change_log") or [])
    seen = {(h["claim_key"], h["change_type"], _h(h["current_value"])) for h in hist}
    for ch in changes:
        sig = (ch["claim_key"], ch["change_type"], _h(ch["current_value"]))
        if sig not in seen:  # idempotent: the same snapshot never duplicates a record
            hist.append({k: v for k, v in ch.items() if k != "previous_evidence"})
            seen.add(sig)
    snapshot = _h(sorted((k, _h(c["value"])) for k, c in cur.items() if c.get("availability") == "available"))
    env["refresh"] = {
        "previous_run_id": prev.get("run_id"),
        "previous_completed_at": prev.get("completed_at"),
        "first_seen_at": prev.get("first_seen_at") or now,
        "last_checked_at": now,
        "check_count": int(prev.get("check_count") or 0) + 1,
        "changes_this_run": len(changes),
        "claims_snapshot_sha256": snapshot,
        "unchanged_since_previous": bool(prev) and snapshot == prev.get("claims_snapshot_sha256"),
        "change_log_size": len(hist),
    }
    store.save(orgnr, {
        "organisation_number": orgnr, "run_id": run_id, "completed_at": now,
        "first_seen_at": env["refresh"]["first_seen_at"], "check_count": env["refresh"]["check_count"],
        "claims": env["claims"], "evidence": env["evidence"], "first_seen": first_seen,
        "sections": {k: {"state": (v or {}).get("state")} for k, v in (env.get("sections") or {}).items()},
        "claims_snapshot_sha256": snapshot, "change_log": hist[-500:],
    })
    return changes
