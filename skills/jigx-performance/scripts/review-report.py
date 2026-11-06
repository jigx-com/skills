#!/usr/bin/env python3
"""Write the before/after report for a /jigx-performance review.

Standard library only. Reads a review folder laid out as:

  <folder>/before/static.json   check-performance.mjs output before changes
  <folder>/before/plans.json    check-plans.py output before changes
  <folder>/after/static.json    the same after changes (optional until applied)
  <folder>/after/plans.json
  <folder>/runs/comparison.json compare-runs.py output (optional)
  <folder>/runs/pre*-summary.json, post*-summary.json   track-load.py runs: pre, pre-1, pre-clean-2 …
                                (pre-android-… is the Android side)
  <folder>/before/timings.json  time-queries.mjs output (optional; --timings BEFORE AFTER for other files)
  <folder>/after/timings.json   time-queries.mjs --baseline before/timings.json (optional)
  <folder>/screens/pre.json     time-screens.py --label pre (optional)
  <folder>/screens/post.json    time-screens.py --label post --compare pre (optional)
  <folder>/runs/comparison-android.json, screens/pre-android.json, post-android.json
                                the same on Android (optional)
  <folder>/changes.json         [{"section": "B", "change": "...", "files": [...]}] (optional)

and writes <folder>/report.md (and prints it): an overview of every area, then sync,
function (sync step), query, action and screen times
before and after, findings per section A-H before and after, the changes applied per
section, and what was not measured. With only the before files it writes the overview used for the proposal.
Read references/customer-review.md.

Example:
  python3 review-report.py <review> --solution my-solution
"""
import argparse
import glob
import json
import os
import re
import statistics
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

# What each section means to the user; kept in step with SECTION_MEANING in check-performance.mjs.
SECTION_MEANING = {
    "A": "How the app is timed and compared; makes the numbers trustworthy, not the app faster.",
    "B": "How fast the app reads its local database: screens, lists and actions waiting for SQL.",
    "C": "How lists are built and drawn: lists that open late, scroll badly or redraw every row.",
    "D": "Work redone in expressions and state: screens that lag, or expressions that fail and blank a field.",
    "E": "Work done when the app starts or a screen opens: cold start and screen open.",
    "F": "How data comes from the server: sign-in, refresh and screens waiting for a sync.",
    "G": "Saving and sending records: duplicate or lost records, not speed.",
    "H": "Credentials, logging and errors: security risk or failures nobody sees.",
}
CODE_SECTION = {
    "unindexed-predicate": "B", "wrapped-predicate": "B", "execute-sql-entities": "B",
    "ddl-invalidates-datasources": "B", "index-not-idempotent": "B", "ddl-ungated": "B",
    "per-row-datasource-read": "C", "uncapped-search-list": "C",
    "datasource-not-equals": "D", "filter-inside-map": "D",
}


def read_path(path):
    return read(os.path.dirname(path) or ".", os.path.basename(path))


def read(folder, *parts):
    path = os.path.join(folder, *parts)
    if not os.path.exists(path) and parts[-1] == "static.json":
        path = os.path.join(folder, *parts[:-1], "audit.json")  # the name an older SKILL.md used
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        # A script that failed half way leaves an empty or partial file; report without it.
        print(f"Skipped {path}: not valid JSON (did the script that writes it fail?)", file=sys.stderr)
        return None


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


def load_runs(folder, prefix):
    """track-load.py summaries labelled <prefix> or <prefix>-<anything> in <folder>/runs (pre-1, pre-clean-2),
    but not <prefix>-android-…, which is the Android side."""
    label = re.compile(rf"{re.escape(prefix)}(?:-(?!android)[\w.]+(?:-[\w.]+)*)?-summary\.json")
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "runs", f"{prefix}*-summary.json"))):
        if label.fullmatch(os.path.basename(path)):
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def med(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def entry_medians(runs):
    names = {n for r in runs for n in r.get("entries", {})}
    return {n: med([r["entries"].get(n, {}).get("duration_s") for r in runs]) for n in names}


def function_lines(pre, post):
    """Each sync step runs one function (REST or OData call plus the write to the local table)."""
    if not pre:
        return ["Not measured. Run track-load.py for the sign-in sync (references/run-and-track.md)."]
    before, after = entry_medians(pre), entry_medians(post) if post else {}
    rows = sorted(((n, v) for n, v in before.items() if v is not None), key=lambda x: -x[1])[:12]
    lines = (["| Function (sync step) | Before | After |", "| --- | ---: | ---: |"] if post
             else ["| Function (sync step) | Median |", "| --- | ---: |"])
    for n, v in rows:
        lines.append(f"| {n} | {v} s | {after.get(n, '-')} s |" if post else f"| {n} | {v} s |")
    return lines + ["", f"Median of {len(pre)} run(s){f' before and {len(post)} after' if post else ''}; a step's time is "
                        "its call and the write of its rows, with the sampling interval's resolution. Calls outside sync "
                        "(create, update, submit) are timed as screen journeys: tap the button, ready = the confirmation."]


def timing_lines(comparison, pre=None):
    if not comparison and pre:
        ready = med([r.get("first_start_to_ready_s") for r in pre])
        timeouts = sum(sum(r.get("snapshot_timeouts", {}).values()) for r in pre)
        busy = med([r.get("sync_busy_s") for r in pre])
        starts = med([sum(r["scope_starts"].values()) for r in pre if "scope_starts" in r])
        repeated = sorted({sid for r in pre for sid in r.get("repeated_scopes", [])})
        lines = [f"**Time to ready: {ready} s** (median of {len(pre)} run(s) on {pre[0].get('machine', '?')})", ""]
        if busy is not None:
            lines += [f"Sync work, also after ready: {busy} s in {starts} scope start(s)."
                      + (f" **{', '.join(repeated)} synced more than once in a run**: look for an onFocus or onLoad "
                         "starting the same sync again." if repeated else ""), ""]
        return lines + [
                f"Timeout for first snapshot: {timeouts}. Runtime log errors: {sum(r.get('log_errors', 0) for r in pre)}. "
                f"Expression errors: {sum(e['count'] for r in pre for e in r.get('expression_errors', []))}. "
                f"Rows hashed for lack of an id: {sum(r.get('hash_fallbacks', 0) for r in pre)}."]
    if not comparison:
        return ["Not measured. Run track-load.py and compare-runs.py (references/run-and-track.md)."]
    rows = comparison["rows"]
    total = rows[0]
    pct = "-" if total["change_pct"] is None else f"{total['change_pct']}%"
    lines = [f"**Time to ready: {total['before_s']} s → {total['after_s']} s ({pct}, {total['verdict']})**", ""]
    for r in rows[1:3]:
        if r["metric"].startswith("total:") and r["before_s"] is not None:
            unit = "" if "count" in r["metric"] else " s"
            lines += [f"{r['metric'][7:].capitalize()}: {r['before_s']}{unit} → {r['after_s']}{unit} "
                      f"({'-' if r['change_pct'] is None else str(r['change_pct']) + '%'}, {r['verdict']})", ""]
    lines += [
             f"Medians of {len(comparison['before_runs'])} runs before and {len(comparison['after_runs'])} after "
             f"on {'; '.join(comparison['machine'])}.", ""]
    moved = [r for r in rows[1:] if not r["metric"].startswith("total:") and r["change_pct"] is not None and r["verdict"] != "same"]
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
                    if "hash_fallbacks" in comparison else "")
                 + (f" Expression errors: {comparison['expression_errors']['before']} → {comparison['expression_errors']['after']}."
                    if "expression_errors" in comparison else ""))
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
    grow = [r for r in before.get("growth", []) if r["verdict"] == "GROWS FASTER"]
    if before.get("growth"):
        lines += ["", f"**At {meta.get('scale', 10)}x data: {len(grow)} grow faster than the data.**"]
        if grow:
            lines += ["", "| What | Where | 1x | Scaled | Time |", "| --- | --- | ---: | ---: | ---: |"]
            lines += [f"| {KIND[r['kind']]} | `{r['where']}` | {r['ms_1x']} ms | {r['ms_nx']} ms | {r.get('time_x')}x |" for r in grow[:10]]
    return lines


def slowest(timings, kind):
    rows = [r for r in (timings or {}).get(kind, []) if r.get("ms") is not None]
    return max(rows, key=lambda r: r["ms"]) if rows else None


def overview(folder, pre, post, timings_before, timings_after, screens, static_before, static_after):
    """One row per area: the number that matters before, after, and where to look."""
    fmt_s = lambda v: "-" if v is None else f"{v} s"
    rows = []
    for label, p, q in (("Sync, time to ready", pre, post), ("Sync on Android", load_runs(folder, "pre-android"), load_runs(folder, "post-android"))):
        if p:
            b, a = med([r.get("first_start_to_ready_s") for r in p]), med([r.get("first_start_to_ready_s") for r in q]) if q else None
            top = sorted(((n, v) for n, v in entry_medians(p).items() if v is not None), key=lambda x: -x[1])[:1]
            rows.append((label, fmt_s(b), fmt_s(a), f"slowest function: {top[0][0]} {top[0][1]} s" if top else ""))
            busy = med([r.get("sync_busy_s") for r in p])
            if busy is not None:
                repeated = sorted({sid for r in p for sid in r.get("repeated_scopes", [])})
                rows.append((f"{label.split(',')[0]}, all sync work (also after ready)", fmt_s(busy),
                             fmt_s(med([r.get("sync_busy_s") for r in q])) if q else "-",
                             f"synced more than once: {', '.join(repeated)}" if repeated else ""))
    for kind, label in (("queries", "Queries"), ("actions", "Actions (execute-sql)")):
        if timings_before:
            tot = lambda t: round(sum(r.get("ms") or 0 for r in (t or {}).get(kind, [])), 1)
            top = slowest(timings_before, kind)
            grow = sum(1 for r in timings_before.get("growth", []) if r["kind"] == kind and r["verdict"] == "GROWS FASTER")
            rows.append((f"{label}, {len(timings_before.get(kind, []))} in total", f"{tot(timings_before)} ms",
                         f"{tot(timings_after)} ms" if timings_after else "-",
                         (f"slowest: `{top['where']}` {top['ms']} ms" if top else "") + (f"; {grow} grow faster than the data" if grow else "")))
    for label, (p, q) in screens.items():
        if p:
            for name, s in p["screens"].items():
                after = q["screens"].get(name, {}).get("median_ms") if q else None
                rows.append((f"{label}: {name}", "-" if s["median_ms"] is None else f"{s['median_ms'] / 1000:.2f} s",
                             "-" if after is None else f"{after / 1000:.2f} s", ""))
    if static_before:
        pri = static_before.get("byPriority", {})
        pa = (static_after or {}).get("byPriority")
        rows.append(("Audit findings", f"{pri.get('high', 0)} high · {pri.get('medium', 0)} medium",
                     f"{pa['high']} high · {pa['medium']} medium" if pa else "-", "by section below; fixes in audit.md"))
    if not rows:
        return []
    return (["## Overview", "", "| Area | Before | After | Where to look |", "| --- | ---: | ---: | --- |"]
            + [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
            + ["", "Expressions are inside screen times; Jigx exposes no separate expression timing.", ""])


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


def report(folder, solution=None, timings=(None, None)):
    before = findings(read(folder, "before", "static.json"), read(folder, "before", "plans.json"))
    after_static, after_plans = read(folder, "after", "static.json"), read(folder, "after", "plans.json")
    after = findings(after_static, after_plans) if (after_static or after_plans) else None
    comparison = read(folder, "runs", "comparison.json")
    changes = read(folder, "changes.json") or []
    title = "Performance review" + (f": {solution}" if solution else "")
    lines = [f"# {title}", ""]
    timings_before = read_path(timings[0]) if timings[0] else read(folder, "before", "timings.json")
    timings_after = read_path(timings[1]) if timings[1] else read(folder, "after", "timings.json")
    screens_pre, screens_post = read(folder, "screens", "pre.json"), read(folder, "screens", "post.json")
    pre, post = load_runs(folder, "pre"), load_runs(folder, "post")
    android_screens = (read(folder, "screens", "pre-android.json"), read(folder, "screens", "post-android.json"))
    lines += overview(folder, pre, post, timings_before, timings_after,
                      {"Screen": (screens_pre, screens_post), "Android screen": android_screens},
                      read(folder, "before", "static.json"), after_static)
    lines += ["## Sync", ""] + timing_lines(comparison, pre) + [""]
    lines += ["## Functions", ""] + function_lines(pre, post) + [""]
    lines += ["## Queries and actions", ""] + query_lines(timings_before, timings_after) + [""]
    lines += ["## Screens", ""] + screen_lines(screens_pre, screens_post) + [""]
    # Android, when it was timed too (track-load.py --android, time-screens.py --platform android).
    android_sync = read(folder, "runs", "comparison-android.json")
    android_pre, android_post = read(folder, "screens", "pre-android.json"), read(folder, "screens", "post-android.json")
    android_pre_runs = load_runs(folder, "pre-android")
    if android_sync or android_pre_runs:
        lines += ["## Sync (Android)", ""] + timing_lines(android_sync, android_pre_runs) + [""]
        lines += ["## Functions (Android)", ""] + function_lines(android_pre_runs, load_runs(folder, "post-android")) + [""]
    if android_pre:
        lines += ["## Screens (Android)", ""] + screen_lines(android_pre, android_post) + [""]
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
    shown = sorted({s for s, _, _, _ in section_rows(before, after)})
    if shown:
        lines += ["", "What the sections mean:", ""] + [f"- **{s} {SECTIONS[s]}**: {SECTION_MEANING[s]}" for s in shown]
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
    p.add_argument("folder", nargs="?", default=os.path.expanduser("~/jigx-perf-reviews"))
    p.add_argument("--solution")
    p.add_argument("--timings", nargs=2, metavar=("BEFORE", "AFTER"),
                   help="time-queries.mjs JSON files to use instead of before/ and after/timings.json")
    args = p.parse_args(argv)
    if not any(os.path.exists(os.path.join(args.folder, "before", n)) for n in ("static.json", "audit.json")):
        p.error(f"{args.folder}/before/static.json is missing; capture the baseline first")
    text = report(args.folder, args.solution, tuple(args.timings or (None, None)))
    with open(os.path.join(args.folder, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
