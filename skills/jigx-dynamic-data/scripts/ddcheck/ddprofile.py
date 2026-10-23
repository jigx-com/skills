"""The per-solution profile: everything that is specific to one customer or solution.

ddcheck itself carries no customer knowledge. A solution repo that wants more than the generic
checks keeps a JSON profile, found in this order: --profile <path>; else `ddcheck.json` or
`docs/test-knowledge/ddcheck.json` in the current directory or any parent up to the git root.
See references/profile.md for every key.
"""
import copy
import json
import pathlib
import sys

DEFAULTS = {
    "org": None,                 # Jigx organisation id; --org / JIGX_ORG_ID override it
    "source": None,              # solution folder (holds index.jigx), relative to the profile's repo
    "out": ".ddcheck",           # dumps and reports, relative to the profile's repo (or cwd)
    "configTables": None,        # default table list for dump/diff; else declared minus data tables
    "dataTables": {"names": [], "suffixes": []},   # extra data tables beyond the derived ones
    "requiredTables": [],        # absent on the solution = fatal "missing table"
    "linkTables": None,          # tables whose rows navigate; None = any table whose rows have linkField
    "linkField": "linkTo",
    "liveWhen": {"field": "active", "equals": True},  # a row the app renders; None = every row
    "holdTables": [],            # never auto-imported by seed: per-solution by design
    "tenantNames": [],           # regexes; a row mentioning one is held back from seed
    "matchKeys": {},             # {table: [fields]} match rows by content instead of rid
    "protect": [],               # glob patterns of solutions seed must never write to
    "rules": [],                 # declarative checks, see references/profile.md
    "fatal": [],                 # extra finding severities that fail `check`
}

RULE_TYPES = {"singleRow", "configFlags", "countBy", "requiredFields", "allowedValues",
              "menuParameters"}


def _find(start):
    here = pathlib.Path(start).resolve()
    for d in (here, *here.parents):
        for rel in ("ddcheck.json", "docs/test-knowledge/ddcheck.json"):
            if (d / rel).is_file():
                return d / rel
        if (d / ".git").exists():
            return None
    return None


def load(path=None, cwd="."):
    """Profile dict with defaults filled in, plus `_root` (the profile's repo root, or cwd)."""
    prof = copy.deepcopy(DEFAULTS)
    f = pathlib.Path(path).expanduser() if path else _find(cwd)
    root = pathlib.Path(cwd).resolve()
    if f:
        if not f.is_file():
            sys.exit(f"ddcheck: profile not found: {f}")
        try:
            data = json.loads(f.read_text())
        except ValueError as e:
            sys.exit(f"ddcheck: profile {f} is not valid JSON: {e}")
        unknown = [k for k in data if k not in DEFAULTS and not k.startswith(("_", "//", "$"))]
        if unknown:
            sys.exit(f"ddcheck: unknown profile keys in {f}: {', '.join(unknown)}")
        for rule in data.get("rules", []):
            if rule.get("type") not in RULE_TYPES:
                sys.exit(f"ddcheck: unknown rule type {rule.get('type')!r} in {f} "
                         f"(known: {', '.join(sorted(RULE_TYPES))})")
        prof.update(data)
        prof["dataTables"] = {**DEFAULTS["dataTables"], **(data.get("dataTables") or {})}
        # The profile's repo root: walk up from the profile to the nearest .git.
        root = next((d for d in (f.resolve().parent, *f.resolve().parents) if (d / ".git").exists()),
                    f.resolve().parent)
    prof["_root"], prof["_file"] = root, f
    return prof


def resolve(prof, value):
    """A profile path, relative to the profile's repo root."""
    p = pathlib.Path(value).expanduser()
    return p if p.is_absolute() else prof["_root"] / p
