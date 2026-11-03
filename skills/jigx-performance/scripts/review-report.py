#!/usr/bin/env python3
"""Write the before/after report for a /jigx-performance review.

Standard library only. Reads a review folder laid out as:

  <folder>/before/static.json   check-performance.mjs output before changes
  <folder>/before/plans.json    check-plans.py output before changes
  <folder>/after/static.json    the same after changes (optional until applied)
  <folder>/after/plans.json
  <folder>/runs/comparison.json compare-runs.py output (optional)
  <folder>/before/timings.json  time-queries.mjs output (optional)
  <folder>/after/timings.json   time-queries.mjs --baseline before/timings.json (optional)
  <folder>/screens/pre.json     time-screens.py --label pre (optional)
  <folder>/screens/post.json    time-screens.py --label post --compare pre (optional)
  <folder>/changes.json         [{"section": "B", "change": "...", "files": [...]}] (optional)

and writes <folder>/report.md (and prints it): sync, query, action and screen times
before and after, findings per section A-H before and after, the changes applied per
section, and what was not measured. With only the before files it writes the overview used for the proposal.
Read references/customer-review.md.

Example:
  python3 review-report.py perf-review --solution my-solution
"""
import argparse
import json
import os
import sys

SECTIONS = {
    "A": "Measure and track",
    "B": "Queries and indexes",
    "C": "Lists and rendering",
    "D": "State and expressions",
    "E": "Screens and start-up",
    "F": "Sync",
    "G": "Writes and duplicates",
    "H": "Security and logs",
}
CODE_SECTION = {
    "unindexed-predicate": "B", "wrapped-predicate": "B", "execute-sql-entities": "B",
    "ddl-invalidates-datasources": "B", "index-not-idempotent": "B", "ddl-ungated": "B",
    "per-row-datasource-read": "C", "uncapped-search-list": "C",
    "datasource-not-equals": "D", "filter-inside-map": "D",
}


def read(folder, *parts):
    path = os.path.join(folder, *parts)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def findings(static, plans):
    """{section: {label: count}} from the static scan and the plan check."""
    out = {}
    if static and static.get("findings") is not None:
        for f in static["findings"]:
            section = out.setdefault(f.get("section") or CODE_SECTION.get(f["code"], "B"), {})
            section[f["code"]] = section.get(f["code"], 0) + 1
    elif static:
        for code, n in (static.get("byCode") or {}).items():
            out.setdefault(CODE_SECTION.get(code, "B"), {})[code] = n
    if plans:
        b = out.setdefault("B", {})
        b["queries with full scans"] = plans.get("queries_with_full_scans", 0)
        b["unused indexes"] = len(plans.get("indexes_unused", []))
        b["index DDL that fails"] = len(plans.get("indexes_failed", []))
    return out


def section_rows(before, after):
    rows = []
    for s in SECTIONS:
        labels = sorted(set(before.get(s, {})) | set(after.get(s, {}) if after else set()))
        for label in labels:
            b = before.get(s, {}).get(label, 0)
            a = after.get(s, {}).get(label, 0) if after is not None else None
            rows.append((s, label, b, a))
    return rows


def timing_lines(comparison):
    if not comparison:
        return ["Not measured. Run track-load.py and compare-runs.py (references/run-and-track.md)."]
    rows = comparison["rows"]
    total = rows[0]
    pct = "-" if total["change_pct"] is None else f"{total['change_pct']}%"
    lines = [f"**Time to ready: {total['before_s']} s → {total['after_s']} s ({pct}, {total['verdict']})**", "",
             f"Medians of {len(comparison['before_runs'])} runs before and {len(comparison['after_runs'])} after "
             f"on {'; '.join(comparison['machine'])}.", ""]
    moved = [r for r in rows[1:] if r["change_pct"] is not None and r["verdict"] != "same"]
    moved.sort(key=lambda r: r["change_pct"])
    if moved:
        lines += ["| Step | Before | After | Change |", "| --- | ---: | ---: | ---: |"]
        for r in moved[:8] + [r for r in moved[8:] if r["verdict"] == "REGRESSION"]:
            lines.append(f"| {r['metric']} | {r['before_s']} s | {r['after_s']} s | {r['change_pct']}% |")
        lines.append("")
    lines.append(f"Timeout for first snapshot: {comparison['snapshot_timeouts']['before']} → "
                 f"{comparison['snapshot_timeouts']['after']}. Runtime log errors: "
                 f"{comparison['log_errors']['before']} → {comparison['log_errors']['after']}."
                 + (f" Rows hashed for lack of an id: {comparison['hash_fallbacks']['before']} → {comparison['hash_fallbacks']['after']}."
                    if "hash_fallbacks" in comparison else ""))
    for w in comparison.get("warnings", []):
        lines.append(f"- Warning: {w}")
    return lines


KIND = {"queries": "query", "actions": "execute-sql", "indexes": "index"}


def query_lines(before, after):
    if not before:
        return ["Not measured. Run time-queries.mjs on the test user's database (references/customer-review.md)."]
    total = lambda t, kind: round(sum(r.get("ms") or 0 for r in t.get(kind, [])), 1)
    lines = []
    for kind, label in (("queries", "Queries"), ("actions", "execute-sql steps"), ("indexes", "Index builds")):
        b = total(before, kind)
        lines.append(f"- {label}: {len(before.get(kind, []))}, {b} ms in total" + (f" → {total(after, kind)} ms" if after else ""))
    meta = before.get("meta", {})
    lines += ["", f"Host timings on {meta.get('host', 'the host')} against {meta.get('rows', '?')} rows; a phone is several times slower."]
    moved = [r for r in (after or {}).get("comparison", []) if r["verdict"] != "same"]
    if after:
        lines.append(f"\n{sum(r['verdict'] == 'faster' for r in moved)} faster, {sum(r['verdict'] == 'SLOWER' for r in moved)} slower, "
                     f"{len(after.get('comparison', [])) - len(moved)} the same.")
        rows = moved[:12]
        head = "| What | Where | Before | After | Change |"
        body = [f"| {KIND[r['kind']]} | `{r['where']}` | {r['before_ms']} ms | {r['after_ms']} ms | {r['change_pct']}% |" for r in rows]
    else:
        rows = sorted([dict(r, kind=k) for k in ("queries", "actions") for r in before.get(k, []) if r.get("ms") is not None],
                      key=lambda r: -r["ms"])[:8]
        head = "| What | Where | Median | Rows |"
        body = [f"| {KIND[r['kind']]} | `{r['where']}` | {r['ms']} ms | {r.get('rows', '-')} |" for r in rows]
    if rows:
        lines += ["", head, "| --- | --- | ---: | ---: |" + (" ---: |" if after else "")] + body
    return lines


def screen_lines(pre, post):
    if not pre:
        return ["Not measured. Run time-screens.py with a screens.json (references/customer-review.md)."]
    fmt = lambda v: "-" if v is None else f"{v / 1000:.2f} s"
    if post and post.get("comparison"):
        lines = ["| Screen | Before | After | Change | |", "| --- | ---: | ---: | ---: | --- |"]
        lines += [f"| {r['screen']} | {fmt(r['before_ms'])} | {fmt(r['after_ms'])} | "
                  f"{'-' if r['change_pct'] is None else str(r['change_pct']) + '%'} | {r['verdict']} |" for r in post["comparison"]["rows"]]
    else:
        lines = ["| Screen | Median | Spread |", "| --- | ---: | ---: |"]
        lines += [f"| {n} | {fmt(s['median_ms'])} | {'-' if s['spread_ms'] is None else str(s['spread_ms']) + ' ms'} |" for n, s in pre["screens"].items()]
    return lines + ["", f"Tap to ready element on {pre['device']}, {pre['runs']} runs a screen; includes datasources, "
                        "expressions and rendering, plus Maestro's overhead."]


def report(folder, solution=None):
    before = findings(read(folder, "before", "static.json"), read(folder, "before", "plans.json"))
    after_static, after_plans = read(folder, "after", "static.json"), read(folder, "after", "plans.json")
    after = findings(after_static, after_plans) if (after_static or after_plans) else None
    comparison = read(folder, "runs", "comparison.json")
    changes = read(folder, "changes.json") or []
    title = "Performance review" + (f": {solution}" if solution else "")
    lines = [f"# {title}", ""]
    timings_before, timings_after = read(folder, "before", "timings.json"), read(folder, "after", "timings.json")
    screens_pre, screens_post = read(folder, "screens", "pre.json"), read(folder, "screens", "post.json")
    lines += ["## Sync", ""] + timing_lines(comparison) + [""]
    lines += ["## Queries and actions", ""] + query_lines(timings_before, timings_after) + [""]
    lines += ["## Screens", ""] + screen_lines(screens_pre, screens_post) + [""]
    lines += ["## Findings by section", ""]
    pri = (read(folder, "before", "static.json") or {}).get("byPriority")
    if pri:
        after_pri = (after_static or {}).get("byPriority")
        lines += [f"Priority before: {pri['high']} high, {pri['medium']} medium, {pri['low']} low"
                  + (f"; after: {after_pri['high']} high, {after_pri['medium']} medium, {after_pri['low']} low" if after_pri else "")
                  + ". The full list with locations and fixes is in `audit.md` (`check-performance.mjs --report`).", ""]
    if after is None:
        lines += ["Baseline only; nothing has been changed yet.", "",
                  "| § | Section | Finding | Count |", "| --- | --- | --- | ---: |"]
        lines += [f"| {s} | {SECTIONS[s]} | {label} | {b} |" for s, label, b, _ in section_rows(before, None)]
    else:
        lines += ["| § | Section | Finding | Before | After |", "| --- | --- | --- | ---: | ---: |"]
        lines += [f"| {s} | {SECTIONS[s]} | {label} | {b} | {a} |" for s, label, b, a in section_rows(before, after)]
    lines += ["", "Static counts are leads, not timings; sections D-H also need review the scripts cannot do.", ""]
    if changes:
        lines += ["## Changes applied", ""]
        for s in SECTIONS:
            items = [c for c in changes if c.get("section") == s]
            if items:
                lines.append(f"**{s} · {SECTIONS[s]}**")
                lines += [f"- {c['change']}" + (f" ({', '.join(c['files'])})" if c.get("files") else "") for c in items]
                lines.append("")
    lines += ["## Not verified", "",
              "- Simulator timings show relative change on that machine, not timings on a customer's device.",
              "- Confirm large wins on the slowest supported device with production-sized data."]
    if not comparison:
        lines.append("- No before/after load timing was recorded.")
    if not timings_after:
        lines.append("- Query and action times were not compared before and after.")
    if not (screens_post and screens_post.get("comparison")):
        lines.append("- Screen open times were not compared before and after.")
    lines.append("- Expressions have no runtime timing; their cost shows only inside screen open time.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", nargs="?", default="perf-review")
    p.add_argument("--solution")
    args = p.parse_args(argv)
    if not os.path.exists(os.path.join(args.folder, "before", "static.json")):
        p.error(f"{args.folder}/before/static.json is missing; capture the baseline first")
    text = report(args.folder, args.solution)
    with open(os.path.join(args.folder, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
