"""Diff two solutions' Dynamic Data tables, and build the seed plan (reads only)."""
import re

import api

# Row content that almost certainly carries an environment-specific value.
_GENERIC_SUSPECT = [
    ("URL", re.compile(r"https?://", re.I)),
    ("GUID", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("email domain", re.compile(r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]


def suspect_patterns(prof):
    pats = list(_GENERIC_SUSPECT)
    names = prof.get("tenantNames") or []
    if names:
        pats.append(("tenant name", re.compile(r"\b(" + "|".join(names) + r")\b", re.I)))
    return pats


def suspect_reasons(content, patterns):
    blob = api.canon(content)
    return [label for label, rx in patterns if rx.search(blob)]


def _key_of(table, content, rid, match_keys):
    fields = match_keys.get(table)
    if not fields:
        return rid
    return tuple(str(content.get(f, "")).strip().lower() for f in fields)


def diff_table(src_rows, dst_rows, table, match_keys):
    """-> (src map, dst map, missing keys, changed keys, extra keys).

    Maps are keyed by rid, or by the profile's matchKeys fields for tables seeded separately on
    each side (so the same row has different rids). A content-keyed value carries its own rid
    under "_rid" so the seed writes onto the target's rid instead of inserting a duplicate.
    """
    def build(rows):
        m = {}
        for i in rows or []:
            rid, c = api.rid_of(i), api.content_of(i)
            k = _key_of(table, c, rid, match_keys)
            m[k] = dict(c, _rid=rid) if k != rid else c
        return m
    sm, dm = build(src_rows), build(dst_rows)
    strip = lambda c: {k: v for k, v in c.items() if k != "_rid"}
    missing = [k for k in sm if k not in dm]
    changed = [k for k in sm if k in dm and api.canon(strip(sm[k])) != api.canon(strip(dm[k]))]
    extra = [k for k in dm if k not in sm]
    return sm, dm, missing, changed, extra


def build_diff(tables, src_dump, dst_dump, src_name, dst_name, prof, include_suspect=False):
    """Returns (report lines, plan {table: {rid: content}}, held-back list, missing map)."""
    match_keys = prof.get("matchKeys") or {}
    hold = set(prof.get("holdTables") or [])
    patterns = suspect_patterns(prof)
    report = [f"# DD diff {src_name} -> {dst_name}", "",
              "| table | src rows | dst rows | missing in dst | changed | only in dst |",
              "|---|---|---|---|---|---|"]
    plan, held, missing_map = {}, [], {}
    for t in tables:
        s_rows, d_rows = src_dump.get(t), dst_dump.get(t)
        if s_rows is None:
            report.append(f"| {t} | ABSENT | {'ABSENT' if d_rows is None else len(d_rows)} | - | - | - |")
            continue
        sm, dm, missing, changed, extra = diff_table(s_rows, d_rows, t, match_keys)
        report.append(f"| {t} | {len(sm)} | {'ABSENT' if d_rows is None else len(dm)} | "
                      f"{len(missing)} | {len(changed)} | {len(extra)} |")
        if not (missing or changed):
            continue

        def plan_rid(k, matched):
            if t not in match_keys:
                return k
            return (dm if matched else sm)[k]["_rid"]
        missing_map[t] = {plan_rid(k, False) for k in missing}
        rows = {plan_rid(k, False): {x: v for x, v in sm[k].items() if x != "_rid"} for k in missing}
        rows.update({plan_rid(k, True): {x: v for x, v in sm[k].items() if x != "_rid"} for k in changed})
        if t in hold:
            held.append((t, len(rows), "per-solution by design (profile holdTables)"))
            continue
        flagged = {r: suspect_reasons(c, patterns) for r, c in rows.items()}
        flagged = {r: v for r, v in flagged.items() if v}
        if flagged and include_suspect:
            flagged = {}                       # the operator read the rows and asked for them
        if flagged:
            held.append((t, len(flagged),
                         "rows carry " + ", ".join(sorted({x for v in flagged.values() for x in v}))))
            rows = {r: c for r, c in rows.items() if r not in flagged}
        if rows:
            plan[t] = rows

    report += ["", "## Decide before copy", ""]
    if held:
        report += ["| table | rows held back | why |", "|---|---|---|"]
        report += [f"| {t} | {n} | {why} |" for t, n, why in held]
    else:
        report.append("Nothing held back.")
    return report, plan, held, missing_map


def _summary(content, limit=90):
    keys = [k for k in ("title", "name", "label", "value", "code", "linkTo") if k in content]
    keys += [k for k in content if k not in keys][:4]
    text = ", ".join(f"{k}={content[k]!r}" for k in keys)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def row_detail(tables, src_dump, dst_dump, prof):
    match_keys = prof.get("matchKeys") or {}
    lines = ["", "## Rows", ""]
    for t in tables:
        s_rows, d_rows = src_dump.get(t), dst_dump.get(t)
        if s_rows is None:
            continue
        sm, dm, missing, changed, extra = diff_table(s_rows, d_rows, t, match_keys)
        if not (missing or changed or extra):
            continue
        lines.append(f"### {t}" + (f" (matched on {'+'.join(match_keys[t])})" if t in match_keys else ""))
        show = lambda c: _summary({k: v for k, v in c.items() if k != "_rid"})
        for r in missing:
            lines.append(f"- missing in target `{sm[r].get('_rid', r)}`: {show(sm[r])}")
        for r in changed:
            keys = sorted(k for k in set(sm[r]) | set(dm[r]) if k != "_rid" and sm[r].get(k) != dm[r].get(k))
            lines.append(f"- changed `{dm[r].get('_rid', r)}` on {keys}: source "
                         f"{_summary({k: sm[r].get(k) for k in keys})} | target "
                         f"{_summary({k: dm[r].get(k) for k in keys})}")
        for r in extra:
            lines.append(f"- only in target `{dm[r].get('_rid', r)}`: {show(dm[r])}")
        lines.append("")
    return lines if len(lines) > 3 else []
