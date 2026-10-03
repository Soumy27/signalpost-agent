"""Profile store: one JSON file per company, with change history.

When a profile is rebuilt, its facts are compared with the stored version and
every changed value is recorded with old/new value and detection time, so
consumers can see what changed and when.
"""
import json
import os
from pathlib import Path

MAX_CHANGES = 100


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / "profiles"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, orgnr):
        return self.dir / f"{orgnr}.json"

    def load(self, orgnr):
        p = self.path(orgnr)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def all_orgnrs(self):
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def save(self, profile):
        prev = self.load(profile["orgnr"])
        now = profile["generated_at"]
        hist = dict((prev or {}).get("history") or {})
        changes = diff_facts((prev or {}).get("facts") or {}, profile["facts"], now) if prev else []
        if prev and prev.get("lookup_result") != profile.get("lookup_result"):
            changes.insert(0, {"field": "lookup_result", "old": prev.get("lookup_result"),
                               "new": profile.get("lookup_result"), "detected_at": now})
        hist.setdefault("first_seen", now)
        hist["last_checked"] = now
        if changes or "last_changed" not in hist:
            hist["last_changed"] = now
        hist["changes"] = (changes + list(hist.get("changes") or []))[:MAX_CHANGES]
        hist["check_count"] = int(hist.get("check_count") or 0) + 1
        profile["history"] = hist
        # field-level freshness: when each value was first observed in its current form
        for field, fact in profile["facts"].items():
            old = ((prev or {}).get("facts") or {}).get(field)
            if old and old.get("value") == fact.get("value") and old.get("observed_since"):
                fact["observed_since"] = old["observed_since"]
            else:
                fact["observed_since"] = now
        tmp = self.path(profile["orgnr"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path(profile["orgnr"]))
        return changes

    def write_meta(self, name, data):
        (self.root / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_meta(self, name):
        p = self.root / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# Website facts depend on a live page that may be temporarily unreachable; a
# missing website fact on one run is not treated as a removal.
VOLATILE = {"website", "website_title", "website_description", "social_profiles"}


def diff_facts(old, new, when):
    changes = []
    for field in sorted(set(old) | set(new)):
        o = (old.get(field) or {}).get("value")
        n = (new.get(field) or {}).get("value")
        if o == n:
            continue
        if n is None and field in VOLATILE:
            new[field] = {**old[field], "stale": True}  # keep last verified value
            continue
        changes.append({"field": field, "old": o, "new": n, "detected_at": when,
                        "source": (new.get(field) or old.get(field) or {}).get("source")})
    return changes
