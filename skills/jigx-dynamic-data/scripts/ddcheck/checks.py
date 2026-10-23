"""Validate Dynamic Data rows against the solution source.

Every finding is (severity, table, rid, detail). Generic checks, derived from the solution:

- `dangling linkTo`: a rendered row whose link names no jig (a tile that dead-ends). Fatal.
- `latent linkTo`: the same on a row the app does not render (liveWhen is false).
- `bad type`: a field a datasource compares to true/false or to a number holds a string, so the
  SQL filter silently never matches the row.
- `missing table`: a required or rule table is absent on the solution. Fatal.

Everything else comes from the profile's `rules` (see references/profile.md).
"""
import json

import api
import source

FATAL = {"missing table", "dangling linkTo"}

ORDER = ["missing table", "dangling linkTo", "bad parameters", "missing flag", "count mismatch",
         "missing field", "bad value", "bad type", "latent linkTo", "dead parameter"]


def _as_dict(value):
    if isinstance(value, dict):
        return value, None
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError as e:
            return None, str(e)
        if not isinstance(parsed, dict):
            return None, f"parsed to {type(parsed).__name__}, expected object"
        return parsed, None
    return None, "empty"


def is_live(content, live_when):
    """Whether the app renders this row. A row without the liveWhen field counts as live."""
    if not live_when:
        return True
    field, want = live_when.get("field"), live_when.get("equals")
    if field not in content:
        return True
    v = content[field]
    return v == want and type(v) is type(want)


def link_tables(dump, prof):
    if prof.get("linkTables") is not None:
        return list(prof["linkTables"])
    field = prof.get("linkField", "linkTo")
    return [t for t, rows in dump.items()
            if rows and any(field in api.content_of(r) for r in rows)]


def check_links(table, rows, jigs, prof, findings, params=False):
    field = prof.get("linkField", "linkTo")
    for item in rows:
        rid, c = api.rid_of(item), api.content_of(item)
        link = c.get(field)
        if not link:
            continue                          # no link: a label, not a broken link
        live = is_live(c, prof.get("liveWhen"))
        if link not in jigs:
            findings.append(("dangling linkTo" if live else "latent linkTo", table, rid,
                             f"{field} '{link}' is not a jig in the source"
                             + ("" if live else " (row is not rendered)")))
            continue
        if params:
            _check_parameters(table, rid, c, link, jigs[link], findings)


def _check_parameters(table, rid, c, link, target, findings):
    """The menuParameters rule: the row's `parameters` object arrives as ONE input named
    `parameters` (a catch-all go-to), so the target jig must accept it and read its keys."""
    raw = c.get("parameters")
    if raw in (None, "", {}):
        return
    params, err = _as_dict(raw)
    if params is None:
        findings.append(("bad parameters", table, rid, f"parameters is not a JSON object ({err})"))
        return
    declared = source.jig_inputs(target)
    if declared is not None and "parameters" not in declared:
        findings.append(("bad parameters", table, rid,
                         f"passes parameters {sorted(params)} but jig '{link}' declares inputs "
                         f"{declared} without `parameters`; the object is dropped"))
        return
    read = source.jig_parameter_keys(target)
    dead = [k for k in params if k not in read]
    if dead:
        findings.append(("dead parameter", table, rid,
                         f"jig '{link}' never reads parameters {dead}; it reads {sorted(read) or 'none'}"))


def check_types(table, rows, typed, findings):
    for item in rows:
        rid, c = api.rid_of(item), api.content_of(item)
        for field, (kind, where) in typed.items():
            if field not in c or c[field] is None:
                continue
            v = c[field]
            if isinstance(v, str):
                findings.append(("bad type", table, rid,
                                 f"{field} is the string {v!r}, but {where} compares it as a {kind}; "
                                 f"the filter never matches this row"))


def _rule_rows(dump, rule):
    return dump.get(rule["table"])


def run_rules(dump, src, prof, jigs, findings):
    for rule in prof.get("rules", []):
        kind, table = rule["type"], rule.get("table")
        rows = _rule_rows(dump, rule) if table else None
        if kind == "menuParameters":
            continue                          # handled with the link check
        if rows is None:
            continue                          # absent tables are reported as missing table
        if kind == "singleRow" and len(rows) != 1:
            findings.append(("count mismatch", table, None, f"{len(rows)} rows, expected exactly 1"))
        elif kind == "configFlags":
            present = set()
            for item in rows:
                present.update(api.content_of(item))
            for flag in sorted(source.config_flags(src, table)):
                if flag not in present:
                    findings.append(("missing flag", table, None,
                                     f"'{flag}' is read by the app but absent from the row (reads as off)"))
        elif kind == "countBy":
            field, expect = rule["field"], rule["expect"]
            counts = {}
            for item in rows:
                k = api.content_of(item).get(field)
                counts[k] = counts.get(k, 0) + 1
            for key, n in expect.items():
                if counts.get(key, 0) != n:
                    findings.append(("count mismatch", table, None,
                                     f"{key}: {counts.get(key, 0)} rows, expected {n}"))
            if rule.get("exhaustive", True):
                for key in sorted(k for k in counts if k not in expect):
                    findings.append(("count mismatch", table, None,
                                     f"{key}: {counts[key]} rows, value not in the expected set"))
        elif kind == "requiredFields":
            for item in rows:
                c = api.content_of(item)
                for f in rule["fields"]:
                    if not c.get(f):
                        findings.append(("missing field", table, api.rid_of(item), f"missing '{f}'"))
        elif kind == "allowedValues":
            allowed = set(rule["values"])
            for item in rows:
                v = api.content_of(item).get(rule["field"])
                if v and v not in allowed:
                    findings.append(("bad value", table, api.rid_of(item),
                                     f"{rule['field']} '{v}' not in {sorted(allowed)}"))


def required_tables(prof):
    req = list(prof.get("requiredTables") or [])
    req += [r["table"] for r in prof.get("rules", []) if r.get("table")]
    if prof.get("linkTables"):
        req += list(prof["linkTables"])
    return list(dict.fromkeys(req))


def run_checks(dump, src, prof):
    """`dump` maps table name -> rows, or None when the table is absent on the solution."""
    findings = []
    for t in required_tables(prof):
        if t in dump and dump[t] is None:
            findings.append(("missing table", t, None, "table absent on this solution"))
    jigs = source.jig_paths(src)
    params = any(r["type"] == "menuParameters" for r in prof.get("rules", []))
    for t in link_tables(dump, prof):
        if dump.get(t):
            check_links(t, dump[t], jigs, prof, findings, params=params)
    typed = source.typed_fields(src)
    for t, rows in dump.items():
        if rows and t in typed:
            check_types(t, rows, typed[t], findings)
    run_rules(dump, src, prof, jigs, findings)
    return findings


def render(findings):
    if not findings:
        return "No findings.\n"
    rank = {s: i for i, s in enumerate(ORDER)}
    out = ["| severity | table | rid | detail |", "|---|---|---|---|"]
    for sev, table, rid, detail in sorted(findings, key=lambda f: (rank.get(f[0], 99), f[1])):
        out.append(f"| {sev} | {table} | {rid or '-'} | {detail} |")
    return "\n".join(out) + "\n"


def failed(findings, prof):
    fatal = FATAL | set(prof.get("fatal") or [])
    return any(f[0] in fatal for f in findings)
