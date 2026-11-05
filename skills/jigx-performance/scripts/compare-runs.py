#!/usr/bin/env python3
"""Compare before and after load runs recorded by track-load.py.

Standard library only. Reads <folder>/*-summary.json, groups runs by label prefix
(default pre- and post-), and reports medians for the time from first scope start
to ready, each scope, and each sync entry, with the change in percent. A slowdown
over --threshold percent (default 10) is flagged for investigation; it is a review
threshold, not an SLA. A change smaller than --min-change seconds (default 0.5, two
samples at track-load.py's 0.25 s interval) is reported as "same": entry times are
only as precise as the sampling, so a one-sample step is not a regression.

Writes <folder>/comparison.md and comparison.json, and prints the markdown. Android runs
(--before pre-android --after post-android) write comparison-android.md and .json.
Exits 1 when the total time regressed past the threshold or any run did not reach
ready. Read references/run-and-track.md.

Example:
  python3 compare-runs.py perf-runs --before pre --after post
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys


def load(folder, prefix):
    """Runs labelled <prefix> or <prefix>-<n>: "pre" takes pre-1..3, not pre-android-1."""
    runs = []
    label = re.compile(rf"{re.escape(prefix)}(?:-\d+)?-summary\.json")
    for path in sorted(p for p in glob.glob(os.path.join(folder, f"{prefix}*-summary.json")) if label.fullmatch(os.path.basename(p))):
        with open(path, encoding="utf-8") as f:
            runs.append(json.load(f))
    return runs


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def change(before, after, threshold, min_change=0.5):
    if before is None or after is None:
        return None, "missing"
    if before == 0:
        return None, "same" if after == 0 else "new"
    pct = round((after - before) / before * 100, 1)
    if abs(after - before) < min_change:
        return pct, "same"
    return pct, "REGRESSION" if pct > threshold else "improved" if pct < -threshold else "same"


def metrics(runs):
    """{metric: [value per run]} for total, each scope and each entry."""
    out = {"total: first start to ready": [r.get("first_start_to_ready_s") for r in runs]}
    for key, section in (("scope", "scopes"), ("entry", "entries")):
        names = sorted({n for r in runs for n in r.get(section, {})})
        for n in names:
            out[f"{key}: {n}"] = [r.get(section, {}).get(n, {}).get("duration_s") for r in runs]
    return out


def compare(before, after, threshold=10.0, min_change=0.5):
    b, a = metrics(before), metrics(after)
    rows = []
    for name in list(dict.fromkeys(list(b) + list(a))):
        mb, ma = median(b.get(name, [])), median(a.get(name, []))
        pct, verdict = change(mb, ma, threshold, min_change)
        rows.append({"metric": name, "before_s": mb, "after_s": ma, "change_pct": pct, "verdict": verdict})
    warnings = []
    for side, runs in (("before", before), ("after", after)):
        if len(runs) < 3:
            warnings.append(f"only {len(runs)} {side} run(s); use at least 3 per side")
        bad = [r["label"] for r in runs if r.get("result") != "ready"]
        if bad:
            warnings.append(f"{side} runs not ready: {', '.join(bad)}")
    machines = {r.get("machine") for r in before + after}
    if len(machines) > 1:
        warnings.append("runs come from different machines: " + "; ".join(sorted(m or "?" for m in machines)))
    timeouts = {side: sum(sum(r.get("snapshot_timeouts", {}).values()) for r in runs)
                for side, runs in (("before", before), ("after", after))}
    errors = {side: sum(r.get("log_errors", 0) for r in runs) for side, runs in (("before", before), ("after", after))}
    hashed = {side: sum(r.get("hash_fallbacks", 0) for r in runs) for side, runs in (("before", before), ("after", after))}
    expr = {side: sum(e["count"] for r in runs for e in r.get("expression_errors", [])) for side, runs in (("before", before), ("after", after))}
    return {"threshold_pct": threshold, "min_change_s": min_change, "before_runs": [r["label"] for r in before],
            "after_runs": [r["label"] for r in after], "rows": rows, "warnings": warnings,
            "snapshot_timeouts": timeouts, "log_errors": errors, "hash_fallbacks": hashed, "expression_errors": expr,
            "machine": sorted(m or "?" for m in machines)}


def fmt(v, suffix=""):
    return "-" if v is None else f"{v}{suffix}"


def markdown(report):
    lines = ["# Load comparison", "",
             f"Before: {', '.join(report['before_runs']) or 'none'}  ",
             f"After: {', '.join(report['after_runs']) or 'none'}  ",
             f"Machine: {'; '.join(report['machine'])}  ",
             f"Medians in seconds; slowdowns over {report['threshold_pct']}% and "
             f"{report.get('min_change_s', 0.5)} s are flagged.", ""]
    if report["warnings"]:
        lines += ["**Warnings**", ""] + [f"- {w}" for w in report["warnings"]] + [""]
    lines += ["| Metric | Before | After | Change | Verdict |", "| --- | ---: | ---: | ---: | --- |"]
    for r in report["rows"]:
        lines.append(f"| {r['metric']} | {fmt(r['before_s'])} | {fmt(r['after_s'])} | "
                     f"{fmt(r['change_pct'], '%')} | {r['verdict']} |")
    lines += ["", f"Timeout for first snapshot: before {report['snapshot_timeouts']['before']}, "
                  f"after {report['snapshot_timeouts']['after']}. "
                  f"Log errors: before {report['log_errors']['before']}, after {report['log_errors']['after']}. "
                  f"Rows hashed for lack of an id: before {report['hash_fallbacks']['before']}, after {report['hash_fallbacks']['after']}. "
                  f"Expression errors: before {report['expression_errors']['before']}, after {report['expression_errors']['after']}.",
              "", "Simulator timings show relative change on this machine, not device timings."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", nargs="?", default="perf-runs")
    p.add_argument("--before", default="pre")
    p.add_argument("--after", default="post")
    p.add_argument("--threshold", type=float, default=10.0, help="percent slowdown to flag")
    p.add_argument("--min-change", type=float, default=0.5,
                   help="seconds; smaller changes are reported as same (sampling resolution)")
    args = p.parse_args(argv)
    before, after = load(args.folder, args.before), load(args.folder, args.after)
    if not before or not after:
        p.error(f"need {args.before}*-summary.json and {args.after}*-summary.json in {args.folder}")
    report = compare(before, after, args.threshold, args.min_change)
    md = markdown(report)
    name = "comparison-android" if args.before.endswith("-android") else "comparison"
    with open(os.path.join(args.folder, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(args.folder, f"{name}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    total = report["rows"][0]
    failed = total["verdict"] in ("REGRESSION", "missing") or any("not ready" in w for w in report["warnings"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
