#!/usr/bin/env python3
"""Time how long Jigx screens take to open in the iOS Simulator, using Maestro.

Standard library only; needs Maestro (and Java) on the host. For each screen in a spec
file it runs a generated Maestro flow --runs times: untimed setup steps to reach the
starting screen, then the timed span from just before the tap to the moment the
screen's ready element is visible, then steps to go back. The time includes the
screen's datasources, expressions and rendering: Jigx exposes no render timing, so
tap-to-ready is the user's wait. Maestro adds its own overhead (about 2.5-3 s for a
static iOS screen on an M2 Pro, varying by ~300 ms run to run), so quote changes
between runs on the same machine, not absolute values. Add a static control screen to
the spec to see that overhead.

Spec (screens.json):

  {"appId": "<bundle id of the Jigx app>",
   "screens": [
     {"name": "appointments-list", "tap": "Appointments", "ready": "\\\\d{6}-\\\\d+",
      "after": [{"tap": "Home"}]},
     {"name": "appointment-detail", "setup": [{"tap": "Appointments"}, {"wait": "\\\\d{6}-\\\\d+"}],
      "tap": {"text": "\\\\d{6}-\\\\d+", "index": 0}, "ready": "Appointment Details",
      "after": [{"tap": "Back"}, {"tap": "Home"}]}]}

`tap`, `ready` and `wait` take a text regex (Maestro matches text as a regex) or an
object with "text" or "id" and an optional "index". Start every screen from the same
place (setup should get there from the app's home) and leave from it (after).

Examples:
  python3 time-screens.py screens.json --label pre --runs 3 --out perf-review/screens
  python3 time-screens.py screens.json --label post --runs 3 --out perf-review/screens --compare pre
  python3 time-screens.py screens.json --print-flow appointments-list
"""
import argparse
import datetime
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile

MARK = "JIGX_SCREEN"
TIMEOUT_MS = 30000


def selector(value):
    if isinstance(value, str):
        return json.dumps(value)
    sel = {k: value[k] for k in ("text", "id", "index") if k in value}
    return json.dumps(sel)


def step_yaml(step):
    if "tap" in step:
        return [f"- tapOn: {selector(step['tap'])}"]
    if "wait" in step:
        return ["- extendedWaitUntil:", f"    visible: {selector(step['wait'])}", f"    timeout: {step.get('timeout', TIMEOUT_MS)}"]
    if "scroll" in step:
        return ["- scroll"]
    if "input" in step:
        return [f"- inputText: {json.dumps(step['input'])}"]
    raise ValueError(f"unknown step {step}")


def flow(app_id, screen):
    """The Maestro flow for one timed run of one screen."""
    if not re.fullmatch(r"[\w.-]+", screen["name"]):
        raise ValueError(f"screen name {screen['name']!r}: use letters, digits, - and _")
    lines = [f"appId: {app_id}", "---", "- launchApp:", "    stopApp: false"]
    for s in screen.get("setup", []):
        lines += step_yaml(s)
    lines += ["- evalScript: ${output.t0 = Date.now()}"]
    lines += step_yaml({"tap": screen["tap"]})
    lines += step_yaml({"wait": screen["ready"], "timeout": screen.get("timeout", TIMEOUT_MS)})
    lines += ["- evalScript: ${console.log('" + MARK + " " + screen["name"] + " ms=' + (Date.now() - output.t0))}"]
    for s in screen.get("after", []):
        lines += step_yaml(s)
    return "\n".join(lines) + "\n"


def parse_ms(text, name):
    m = re.search(rf"{MARK} {re.escape(name)} ms=(\d+)", text)
    return int(m.group(1)) if m else None


def run_once(app_id, screen, maestro, device=None):
    with tempfile.TemporaryDirectory(prefix="jigx-screen-") as tmp:
        path = os.path.join(tmp, "flow.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(flow(app_id, screen))
        cmd = [maestro] + (["--device", device] if device else []) + ["test", path, "--debug-output", os.path.join(tmp, "debug")]
        env = dict(os.environ, MAESTRO_CLI_NO_ANALYTICS="1")
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=TIMEOUT_MS / 1000 * 4)
        text = proc.stdout + proc.stderr
        # Maestro nests its logs under a hidden .maestro/ folder, which glob skips.
        for root, _, files in os.walk(os.path.join(tmp, "debug")):
            if "maestro.log" in files:
                with open(os.path.join(root, "maestro.log"), encoding="utf-8", errors="replace") as f:
                    text += f.read()
        ms = parse_ms(text, screen["name"])
        if proc.returncode != 0 or ms is None:
            failed = re.findall(r"(?:FAILED|Assertion is false|Element not found)[^\n]*", text)
            return None, (failed[0] if failed else f"maestro exited {proc.returncode}")[:200]
        return ms, None


def summarise(results):
    out = {}
    for name, runs in results.items():
        ok = [r["ms"] for r in runs if r["ms"] is not None]
        out[name] = {"runs": runs, "median_ms": statistics.median(ok) if ok else None,
                     "spread_ms": (max(ok) - min(ok)) if len(ok) > 1 else None,
                     "failed": sum(1 for r in runs if r["ms"] is None)}
    return out


def compare(before, after, min_ms=250):
    """Per screen: medians before and after. Faster or SLOWER only when the runs do not overlap
    (every after run beyond every before run) and the medians differ by min_ms; identical
    builds measured twice differed by 11% (300 ms) in one calibration, so a median alone is noise."""
    rows = []
    for name, a in after["screens"].items():
        b = before["screens"].get(name)
        if not b or b["median_ms"] is None or a["median_ms"] is None:
            rows.append({"screen": name, "before_ms": b and b["median_ms"], "after_ms": a["median_ms"], "change_pct": None, "verdict": "not comparable"})
            continue
        delta = a["median_ms"] - b["median_ms"]
        change = round(delta / b["median_ms"] * 100, 1) if b["median_ms"] else None
        bs = [r["ms"] for r in b["runs"] if r["ms"] is not None]
        as_ = [r["ms"] for r in a["runs"] if r["ms"] is not None]
        apart = max(as_) < min(bs) if delta < 0 else min(as_) > max(bs)
        rows.append({"screen": name, "before_ms": b["median_ms"], "after_ms": a["median_ms"], "change_pct": change,
                     "verdict": ("faster" if delta < 0 else "SLOWER") if apart and abs(delta) >= min_ms else "same"})
    return rows


def report(result, comparison=None):
    lines = [f"# Screen open times: {result['label']}", "",
             f"{result['device']}, app {result['appId']}, Maestro {result['maestro']}, {result['runs']} runs a screen. "
             "Tap to ready element; includes datasources, expressions and rendering, plus a near-constant Maestro overhead.", ""]
    if comparison:
        lines += [f"## Before and after (vs {comparison['before_label']})", "",
                  "| Screen | Before | After | Change | |", "| --- | ---: | ---: | ---: | --- |"]
        for r in comparison["rows"]:
            fmt = lambda v: "-" if v is None else f"{v / 1000:.2f} s"
            lines.append(f"| {r['screen']} | {fmt(r['before_ms'])} | {fmt(r['after_ms'])} | "
                         f"{'-' if r['change_pct'] is None else str(r['change_pct']) + '%'} | {r['verdict']} |")
        lines += ["", "A change counts when every run on one side beats every run on the other and the medians differ by "
                  "250 ms or more; anything less is Maestro and simulator noise.", ""]
    lines += ["## Medians", "", "| Screen | Median | Spread | Failed runs |", "| --- | ---: | ---: | ---: |"]
    for name, s in result["screens"].items():
        med = "-" if s["median_ms"] is None else f"{s['median_ms'] / 1000:.2f} s"
        spread = "-" if s["spread_ms"] is None else f"{s['spread_ms']} ms"
        lines.append(f"| {name} | {med} | {spread} | {s['failed']} |")
    errors = [(n, r["error"]) for n, s in result["screens"].items() for r in s["runs"] if r.get("error")]
    if errors:
        lines += ["", "## Failed runs", ""] + [f"- {n}: {e}" for n, e in errors[:20]]
    return "\n".join(lines) + "\n"


def detect_app():
    out = subprocess.run(["xcrun", "simctl", "listapps", "booted"], capture_output=True, text=True).stdout
    ids = re.findall(r'CFBundleIdentifier = "?(com\.jigx\.[\w.-]+)"?;', out)
    return ids[0] if len(ids) == 1 else None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec")
    p.add_argument("--label", default="run")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--out", default="perf-review/screens")
    p.add_argument("--only", help="comma-separated screen names")
    p.add_argument("--compare", help="label of an earlier run in --out to compare with")
    p.add_argument("--app-id", help="bundle id; default: the spec's appId, else the one com.jigx.* app on the booted simulator")
    p.add_argument("--device", help="simulator UDID when several are booted")
    p.add_argument("--print-flow", metavar="SCREEN", help="print the generated Maestro flow for one screen and exit")
    args = p.parse_args(argv)
    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    screens = spec["screens"]
    if args.only:
        wanted = set(args.only.split(","))
        screens = [s for s in screens if s["name"] in wanted]
    app_id = args.app_id or spec.get("appId") or detect_app()
    if args.print_flow:
        print(flow(app_id or "<appId>", next(s for s in spec["screens"] if s["name"] == args.print_flow)), end="")
        return 0
    if not app_id:
        p.error("no --app-id, no appId in the spec, and not exactly one com.jigx.* app on the booted simulator")
    maestro = shutil.which("maestro") or os.path.expanduser("~/.maestro/bin/maestro")
    if not os.path.exists(maestro):
        p.error("Maestro is not installed: curl -Ls https://get.maestro.mobile.dev | bash (needs Java 17+)")
    version = subprocess.run([maestro, "--version"], capture_output=True, text=True).stdout.strip().splitlines()[-1:] or ["?"]
    results = {s["name"]: [] for s in screens}
    for i in range(args.runs):
        for s in screens:
            ms, err = run_once(app_id, s, maestro, args.device)
            results[s["name"]].append({"run": i + 1, "ms": ms, **({"error": err} if err else {})})
            print(f"{s['name']} run {i + 1}: " + (f"{ms} ms" if ms is not None else f"failed ({err})"), file=sys.stderr, flush=True)
    result = {"label": args.label, "appId": app_id, "maestro": version[0], "runs": args.runs,
              "device": f"iOS Simulator on {platform.node()} ({platform.machine()})",
              "recorded": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "screens": summarise(results)}
    os.makedirs(args.out, exist_ok=True)
    comparison = None
    if args.compare:
        with open(os.path.join(args.out, f"{args.compare}.json"), encoding="utf-8") as f:
            before = json.load(f)
        if before.get("device") != result["device"]:
            print(f"Warning: {args.compare} ran on {before.get('device')}, this run on {result['device']}", file=sys.stderr)
        comparison = {"before_label": args.compare, "rows": compare(before, result)}
        result["comparison"] = comparison
    with open(os.path.join(args.out, f"{args.label}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    text = report(result, comparison)
    with open(os.path.join(args.out, f"{args.label}.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    failed = sum(s["failed"] for s in result["screens"].values())
    return 1 if failed or (comparison and any(r["verdict"] == "SLOWER" for r in comparison["rows"])) else 0


if __name__ == "__main__":
    sys.exit(main())
