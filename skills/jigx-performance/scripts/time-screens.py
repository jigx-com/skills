#!/usr/bin/env python3
"""Time how long Jigx screens take to open in the iOS Simulator or on Android, using Maestro.

Standard library only; needs Maestro (and Java) on the host. For each screen in a spec
file it runs a generated Maestro flow --runs times: untimed setup steps to reach the
starting screen, then the timed span from just before the tap to the moment the
screen's ready element is visible, then steps to go back. The time includes the
screen's datasources, expressions and rendering: Jigx exposes no render timing, so
tap-to-ready is the user's wait. Maestro adds its own overhead (about 2.5-3 s for a
static iOS screen on an M2 Pro, varying by ~300 ms run to run), so quote changes
between runs on the same machine and device, not absolute values. Add a static control
screen to the spec to see that overhead.

Android: an emulator or a phone connected over USB with USB debugging on (`adb devices`
lists it). Pass --platform android (the default when no iOS Simulator is booted and adb
sees a device) and --device <serial> when several are connected. The appId is the Android
package (`adb shell pm list packages | grep jigx`). The same spec runs on both platforms
when the texts match; put platform-only steps in "ios" / "android" keys of a step list.

A screen with "start": "cold" times the app's own start: the app is stopped, then the
span runs from launch to the ready element (start-up, solution load and the first
datasources), with no tap.

Spec (screens.json):

  {"appId": "<bundle id of the Jigx app>",
   "screens": [
     {"name": "appointments-list", "tap": "Appointments", "ready": "\\\\d{6}-\\\\d+",
      "after": [{"tap": "Home"}]},
     {"name": "cold-start", "start": "cold", "ready": "Appointments"},
     {"name": "appointment-detail", "setup": [{"tap": "Appointments"}, {"wait": "\\\\d{6}-\\\\d+"}],
      "tap": {"text": "\\\\d{6}-\\\\d+", "index": 0}, "ready": "Appointment Details",
      "after": [{"tap": "Back"}, {"tap": "Home"}]}]}

What is timed: from just before the tap to the moment the ready element is visible. The
tap itself does not wait for the screen to settle (Maestro would otherwise wait for every
animation and late image to stop, which once read 8.3 s for rows visible at 1.9 s); the
time until the screen stops moving is recorded separately as "settled". Pick a ready element
that appears last in the part you want to time: a row's number times the list, a row's
photo count or status times the per-row reads too. The report names the ready element of
every screen.

Every tap first waits (untimed, up to 30 s) for its element to be visible: tabs that hide
while a sync runs (a `when:` on the tab) no longer fail the run.

Every warm run starts from the same place: the app is brought to the front, then the spec's
optional top-level "reset" steps run (untimed), e.g. a tap on the Home tab, so a run that
failed halfway cannot break the next one. Top-level "prepare" steps run once before the
round (before the warm-up), e.g. picking the project the screens show after a sign-in
cleared it; when they fail, nothing is timed. After a failed run the app is also restarted
before the next run, and a screen that fails twice in a row is skipped for the rest of the
round. Ids beat texts for navigation: a pushed screen's back button is `<jig-id>.back.icon`
(list screens: `navigation-bar-extended.back-button.icon`), a tab is
`tab-bar.<uuid>-<name>`, and a tab's text starts with its icon glyph, so match it with
`.*Home`, not `Home`. `--ids` prints the ids and texts on the screen now (from `maestro
hierarchy`, without layout noise). Never use the More tab or the solution switcher as a
control screen: one wrong tap switches solution.

Maestro's first iOS run builds and installs its driver (a minute or two), so an untimed
warm-up run goes first (--no-warmup skips it); a run that takes longer than its timeout is
recorded as failed, and results are saved after every run, so nothing is lost.

`tap`, `ready` and `wait` take a text regex (Maestro matches text as a regex) or an
object with "text" or "id" and an optional "index"; {"gone": "<regex>"} waits until that
text has disappeared (e.g. a "Preparing your data" banner). Other steps: {"scroll": true},
{"input": "<text>"}, and {"back": true} for Android's back button. On Android a list row is
one element holding all its texts, so `ready` and `wait` match anywhere in an element there. Start every screen from the same
place (setup should get there from the app's home) and leave from it (after).

Examples:
  python3 time-screens.py screens.json --label pre --runs 3 --out <review>/screens
  python3 time-screens.py screens.json --label post --runs 3 --out <review>/screens --compare pre
  python3 time-screens.py screens.json --label pre-android --platform android --device emulator-5554
  python3 time-screens.py screens.json --print-flow appointments-list
  python3 time-screens.py --ids

Maestro needs Java 17+. When the active java is older and a Java 17+ is installed, Maestro
is run with that one (JAVA_HOME for its process only; the shell profile is not changed).
"""
import argparse
import datetime
import json
import os
import platform as platform_mod
import re
import shutil
import statistics
import subprocess
import sys
import tempfile

MARK = "JIGX_SCREEN"
TIMEOUT_MS = 30000
WARMUP_S = 300
MAX_FAILS_IN_A_ROW = 2
TAP_WAIT_MS = 30000
SETTLE_MS = 15000
# Layout containers and icons: ids that never identify a screen or a row.
NOISE_ID = re.compile(r"^(?:icon\.|view\.|svg|image\.|scroll-view|container)")  # keeps <jig-id>.back.icon


def contains(regex):
    """Android merges a list row's texts into one element ("007351-1\\n2026-10-27"), and Maestro
    matches the whole text, so a ready text there matches anywhere inside the element."""
    return f"[\\s\\S]*(?:{regex})[\\s\\S]*"


def selector(value, loose=False):
    if isinstance(value, str):
        return json.dumps(contains(value) if loose else value)
    sel = {k: value[k] for k in ("text", "id", "index") if k in value}
    if loose and "text" in sel:
        sel["text"] = contains(sel["text"])
    return json.dumps(sel)


def tap_yaml(target, timed=False):
    """Wait (untimed) for the element, then tap it. The timed tap does not wait for the screen
    to settle or retry: the wait for the ready element that follows is the measurement."""
    seen = {k: v for k, v in target.items() if k != "index"} if isinstance(target, dict) else target
    wait = ["- extendedWaitUntil:", f"    visible: {selector(seen)}", f"    timeout: {TAP_WAIT_MS}"]
    if not timed:
        return wait + [f"- tapOn: {selector(target)}"], []
    sel = json.loads(selector(target))
    sel = {"text": sel} if isinstance(sel, str) else sel
    return wait, [f"- tapOn: {json.dumps({**sel, 'retryTapIfNoChange': False, 'waitToSettleTimeoutMs': 200})}"]


def step_yaml(step, platform="ios"):
    if "tap" in step:
        return tap_yaml(step["tap"])[0]
    if "wait" in step:
        w = step["wait"]
        gone = isinstance(w, dict) and "gone" in w
        loose = platform == "android"
        return ["- extendedWaitUntil:", f"    {'notVisible' if gone else 'visible'}: {selector(w['gone'] if gone else w, loose)}",
                f"    timeout: {step.get('timeout', TIMEOUT_MS)}"]
    if "scroll" in step:
        return ["- scroll"]
    if "back" in step:
        return ["- back"]  # Android's back button; iOS has none, tap the screen's Back instead
    if "input" in step:
        return [f"- inputText: {json.dumps(step['input'])}"]
    raise ValueError(f"unknown step {step}")


def steps(value, platform):
    """A step list, or {"ios": [...], "android": [...]} for steps that differ by platform."""
    if isinstance(value, dict):
        return value.get(platform, [])
    return value or []


def flow(app_id, screen, platform="ios", reset=None, restart=False):
    """The Maestro flow for one timed run of one screen. reset: steps that bring the app back to
    the start screen before every warm run; restart: stop the app first (after a failed run)."""
    if not re.fullmatch(r"[\w.-]+", screen["name"]):
        raise ValueError(f"screen name {screen['name']!r}: use letters, digits, - and _")
    lines = [f"appId: {app_id}", "---"]
    cold = screen.get("start") == "cold"
    if cold:
        lines += ["- stopApp"]
    else:
        lines += (["- stopApp"] if restart else []) + ["- launchApp:", "    stopApp: false"]
        for s in steps(reset, platform):
            lines += step_yaml(s, platform)
    for s in steps(screen.get("setup"), platform):
        lines += step_yaml(s, platform)
    if cold:
        lines += ["- evalScript: ${output.t0 = Date.now()}", "- launchApp:", "    stopApp: false"]
    else:
        wait, tap = tap_yaml(screen["tap"], timed=True)
        lines += wait + ["- evalScript: ${output.t0 = Date.now()}"] + tap
    lines += step_yaml({"wait": screen["ready"], "timeout": screen.get("timeout", TIMEOUT_MS)}, platform)
    lines += ["- evalScript: ${console.log('" + MARK + " " + screen["name"] + " ms=' + (Date.now() - output.t0))}",
              "- waitForAnimationToEnd:", f"    timeout: {SETTLE_MS}",
              "- evalScript: ${console.log('" + MARK + " " + screen["name"] + " settled=' + (Date.now() - output.t0))}"]
    for s in steps(screen.get("after"), platform):
        lines += step_yaml(s, platform)
    return "\n".join(lines) + "\n"


def parse_ms(text, name, what="ms"):
    m = re.search(rf"{MARK} {re.escape(name)} {what}=(\d+)", text)
    return int(m.group(1)) if m else None


def prepare_flow(app_id, prepare, platform="ios"):
    """The spec's top-level "prepare" steps as one flow, run once before a round."""
    lines = [f"appId: {app_id}", "---", "- launchApp:", "    stopApp: false"]
    for s in steps(prepare, platform):
        lines += step_yaml(s, platform)
    return "\n".join(lines) + "\n"


def screen_ids(hierarchy):
    """(id, text) of every element with an id in `maestro hierarchy` JSON, layout noise dropped."""
    out, seen = [], set()

    def visit(node):
        if isinstance(node, dict):
            attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
            rid = attrs.get("resource-id") or attrs.get("identifier") or ""
            text = next((str(attrs[k]) for k in ("text", "accessibilityText", "title", "value", "hintText")
                         if attrs.get(k) not in (None, "")), "")
            if rid and not NOISE_ID.match(rid) and (rid, text) not in seen:
                seen.add((rid, text))
                out.append((rid, text))
            for child in node.get("children") or []:
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(hierarchy)
    return out


_ENV = {}


def maestro_env():
    """Maestro's environment: Java 17+ for its process when the active java is older (looked up once)."""
    if "env" in _ENV:
        return _ENV["env"]
    env = dict(os.environ, MAESTRO_CLI_NO_ANALYTICS="1")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("preflight", os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflight.py"))
        pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pf)
        # The Java Maestro's launcher uses: $JAVA_HOME/bin/java first, else java on the PATH.
        java = os.path.join(os.environ["JAVA_HOME"], "bin", "java") if os.environ.get("JAVA_HOME") else "java"
        active = pf.java_version(pf.run([java, "-version"], stderr=True))
        home = None if active and active >= 17 else pf.installed_java_17()
    except Exception:  # never block timing on the Java lookup
        home = None
    if home:
        env["JAVA_HOME"] = home
        env["PATH"] = os.path.join(home, "bin") + os.pathsep + env.get("PATH", "")
        print(f"Maestro runs with the Java 17+ in {home} (the active java is older; nothing else changes)",
              file=sys.stderr, flush=True)
    _ENV["env"] = env
    return env


def run_once(app_id, screen, maestro, device=None, platform="ios", reset=None, restart=False, timeout_s=None):
    with tempfile.TemporaryDirectory(prefix="jigx-screen-") as tmp:
        path = os.path.join(tmp, "flow.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(flow(app_id, screen, platform, reset, restart))
        cmd = [maestro] + (["--device", device] if device else []) + ["test", path, "--debug-output", os.path.join(tmp, "debug")]
        env = maestro_env()
        timeout_s = timeout_s or (TAP_WAIT_MS + TIMEOUT_MS + SETTLE_MS) / 1000 * 2
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None, f"Maestro did not finish within {timeout_s:.0f} s"
        text = proc.stdout + proc.stderr
        # Maestro nests its logs under a hidden .maestro/ folder, which glob skips.
        for root, _, files in os.walk(os.path.join(tmp, "debug")):
            if "maestro.log" in files:
                with open(os.path.join(root, "maestro.log"), encoding="utf-8", errors="replace") as f:
                    text += f.read()
        ms = parse_ms(text, screen["name"])
        if proc.returncode != 0 or ms is None:
            failed = re.findall(r"[^\n]*(?:\.\.\. FAILED|Assertion is false|Element not found)[^\n]*", text)
            return None, (failed[0].strip() if failed else f"maestro exited {proc.returncode}")[:200]
        settled = parse_ms(text, screen["name"], "settled")
        if settled is not None and settled - ms >= SETTLE_MS - 250:
            settled = -1  # waitForAnimationToEnd timed out (a spinner or a looping animation): no settle time
        return (ms, settled), None


def summarise(results, screens=()):
    ready = {s["name"]: s.get("ready") for s in screens}
    out = {}
    for name, runs in results.items():
        ok = [r["ms"] for r in runs if r["ms"] is not None]
        settled = [r["settled_ms"] for r in runs if r.get("settled_ms") is not None]
        out[name] = {"runs": runs, "median_ms": statistics.median(ok) if ok else None,
                     "spread_ms": (max(ok) - min(ok)) if len(ok) > 1 else None,
                     "settled_median_ms": statistics.median(settled) if settled else None,
                     "never_settled": sum(1 for r in runs if r.get("never_settled")),
                     "ready": ready.get(name), "failed": sum(1 for r in runs if r["ms"] is None)}
    return out


def ready_text(ready):
    """The ready element as the report shows it: the text regex or the id."""
    if isinstance(ready, dict):
        return f"id {ready['id']}" if "id" in ready else str(ready.get("text", ready))
    return str(ready) if ready else "-"


def compare(before, after, min_ms=250, min_runs=2):
    """Per screen: medians before and after. Faster or SLOWER only when each side has min_runs
    successful runs, the runs do not overlap (every after run beyond every before run) and the
    medians differ by min_ms; identical builds measured twice differed by 11% (300 ms) in one
    calibration, so a median alone is noise, and one run is no median."""
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
        if min(len(bs), len(as_)) < min_runs:
            rows.append({"screen": name, "before_ms": b["median_ms"], "after_ms": a["median_ms"], "change_pct": change,
                         "verdict": f"not comparable ({len(bs)} and {len(as_)} successful runs)"})
            continue
        apart = max(as_) < min(bs) if delta < 0 else min(as_) > max(bs)
        if apart and abs(delta) >= min_ms:
            verdict = "faster" if delta < 0 else "SLOWER"
        elif abs(delta) >= min_ms and change is not None and abs(change) >= 25:
            # The medians moved by more than twice the measured noise (11%) but the runs overlap:
            # often two groups (a background sync in some runs). The runs are the result.
            verdict = "unclear (runs overlap)"
        else:
            verdict = "same"
        rows.append({"screen": name, "before_ms": b["median_ms"], "after_ms": a["median_ms"], "change_pct": change,
                     "verdict": verdict, "before_runs": bs, "after_runs": as_, "ready": ready_text(a.get("ready"))})
    return rows


def report(result, comparison=None):
    lines = [f"# Screen open times: {result['label']}", "",
             f"{result['device']}, app {result['appId']}, Maestro {result['maestro']}, {result['runs']} runs a screen. "
             "Tap to ready element; includes datasources, expressions and rendering, plus a near-constant Maestro overhead.", ""]
    if comparison:
        lines += comparison_lines(comparison["rows"], f"## Before and after (vs {comparison['before_label']})")
    lines += ["## Medians", "", "| Screen | Timed until | Median | Runs | Settled | Failed runs |",
              "| --- | --- | ---: | --- | ---: | ---: |"]
    for name, s in result["screens"].items():
        med = "-" if s["median_ms"] is None else f"{s['median_ms'] / 1000:.2f} s"
        runs = ", ".join(f"{r['ms'] / 1000:.2f}" for r in s["runs"] if r["ms"] is not None) or "-"
        settled = (f"did not settle in {SETTLE_MS // 1000} s" if s.get("never_settled") and s.get("settled_median_ms") is None
                   else "-" if s.get("settled_median_ms") is None else f"{s['settled_median_ms'] / 1000:.2f} s")
        lines.append(f"| {name} | `{ready_text(s.get('ready'))}` | {med} | {runs} | {settled} | {s['failed']} |")
    lines += ["", "Timed until: the ready element; the time covers what renders before it, not what loads after it. "
              "Settled: until the screen stops moving (animations, late images), not part of the time."]
    errors = [(n, r["error"]) for n, s in result["screens"].items() for r in s["runs"] if r.get("error")]
    if errors:
        lines += ["", "## Failed runs", ""] + [f"- {n}: {e}" for n, e in errors[:20]]
    return "\n".join(lines) + "\n"


def comparison_lines(rows, heading):
    """The before/after table, with each side's runs: a median alone hides runs in two groups."""
    fmt = lambda v: "-" if v is None else f"{v / 1000:.2f} s"
    runs = lambda vs: ", ".join(f"{v / 1000:.2f}" for v in vs or []) or "-"
    lines = [heading, "", "| Screen | Timed until | Before | After | Change | | Runs before → after |",
             "| --- | --- | ---: | ---: | ---: | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['screen']} | `{r.get('ready', '-')}` | {fmt(r['before_ms'])} | {fmt(r['after_ms'])} | "
                     f"{'-' if r['change_pct'] is None else str(r['change_pct']) + '%'} | {r['verdict']} | "
                     f"{runs(r.get('before_runs'))} → {runs(r.get('after_runs'))} |")
    return lines + ["", "faster / SLOWER: each side has 2 or more successful runs, every run on one side beats every run on "
                    "the other, and the medians differ by 250 ms or more. unclear: the medians moved by 25% or more but the "
                    "runs overlap (often two groups, e.g. a background sync in some runs); read the runs. same: anything less, which "
                    "is Maestro and simulator noise.", ""]


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def adb():
    sdk = os.environ.get("ANDROID_HOME") or os.path.expanduser("~/Library/Android/sdk")
    return shutil.which("adb") or os.path.join(sdk, "platform-tools", "adb")


def android_devices():
    return re.findall(r"^(\S+)\tdevice$", run([adb(), "devices"]), re.M)


def detect_platform(device=None):
    if device and (device in android_devices()):
        return "android"
    if "Booted" in run(["xcrun", "simctl", "list", "devices", "booted"]):
        return "ios"
    return "android" if android_devices() else "ios"


def adb_shell(device, *cmd):
    return run([adb()] + (["-s", device] if device else []) + ["shell", *cmd]).strip()


def detect_app(platform="ios", device=None):
    if platform == "android":
        ids = re.findall(r"^package:(com\.jigx\.[\w.]+)$", adb_shell(device, "pm", "list", "packages", "com.jigx"), re.M)
    else:
        out = run(["xcrun", "simctl", "listapps", device or "booted"])
        ids = re.findall(r'CFBundleIdentifier = "?(com\.jigx\.[\w.-]+)"?;', out)
    return ids[0] if len(ids) == 1 else None


def describe_device(platform, device=None):
    host = f"{platform_mod.node()} ({platform_mod.machine()})"
    if platform != "android":
        return f"iOS Simulator on {host}"
    model, sdk = adb_shell(device, "getprop", "ro.product.model"), adb_shell(device, "getprop", "ro.build.version.sdk")
    if adb_shell(device, "getprop", "ro.kernel.qemu") == "1":
        return f"Android emulator {model} API {sdk} on {host}"
    return f"Android {adb_shell(device, 'getprop', 'ro.product.manufacturer')} {model} API {sdk}"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec", nargs="?")
    p.add_argument("--label", default="run")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--out", default=os.path.expanduser("~/jigx-perf-reviews/screens"), help="the review folder's screens/ (preflight.py prints the review folder); never inside the solution repo")
    p.add_argument("--only", help="comma-separated screen names")
    p.add_argument("--compare", help="label of an earlier run in --out to compare with")
    p.add_argument("--app-id", help="bundle id or Android package; default: the spec's appId (or appIdAndroid), else the one com.jigx.* app on the device")
    p.add_argument("--platform", choices=["ios", "android"], help="default: android when --device is an adb serial or no iOS Simulator is booted and adb sees a device")
    p.add_argument("--device", help="simulator UDID or adb serial when several are connected")
    p.add_argument("--print-flow", metavar="SCREEN", help="print the generated Maestro flow for one screen and exit")
    p.add_argument("--no-warmup", action="store_true", help="skip the untimed first run that lets Maestro install its driver")
    p.add_argument("--ids", action="store_true", help="print the ids and texts on the screen now, to pick tap and ready elements")
    args = p.parse_args(argv)
    maestro = shutil.which("maestro") or os.path.expanduser("~/.maestro/bin/maestro")
    if args.ids:
        if not os.path.exists(maestro):
            p.error("Maestro is not installed: brew install mobile-dev-inc/tap/maestro")
        out = subprocess.run([maestro] + (["--device", args.device] if args.device else []) + ["hierarchy"],
                             capture_output=True, text=True, env=maestro_env(), timeout=120).stdout
        try:
            tree = json.loads(out[out.index("{"):])
        except ValueError:
            p.error("maestro hierarchy printed no JSON: " + out.strip()[-300:])
        for rid, text in screen_ids(tree):
            print(f"{rid:60} {text[:60]}")
        return 0
    if not args.spec:
        p.error("give a spec file (or --ids)")
    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    screens = spec["screens"]
    if args.only:
        wanted = set(args.only.split(","))
        screens = [s for s in screens if s["name"] in wanted]
    plat = args.platform or spec.get("platform") or (None if args.print_flow else detect_platform(args.device)) or "ios"
    app_id = args.app_id or (spec.get("appIdAndroid") if plat == "android" else None) or spec.get("appId")
    if args.print_flow:
        print(flow(app_id or "<appId>", next(s for s in spec["screens"] if s["name"] == args.print_flow), plat, spec.get("reset")), end="")
        return 0
    app_id = app_id or detect_app(plat, args.device)
    if not app_id:
        p.error(f"no --app-id, no appId in the spec, and not exactly one com.jigx.* app on the {plat} device")
    if not os.path.exists(maestro):
        p.error("Maestro is not installed: brew install mobile-dev-inc/tap/maestro, or "
                "curl -Ls https://get.maestro.mobile.dev | bash (needs Java 17+)")
    print(f"Timing on {plat} ({args.device or 'the only device'}), app {app_id}", file=sys.stderr, flush=True)
    version = subprocess.run([maestro, "--version"], capture_output=True, text=True, env=maestro_env()).stdout.strip().splitlines()[-1:] or ["?"]
    os.makedirs(args.out, exist_ok=True)
    reset = spec.get("reset")
    results = {s["name"]: [] for s in screens}
    device_desc = describe_device(plat, args.device)

    def result_now():
        return {"label": args.label, "appId": app_id, "maestro": version[0], "runs": args.runs, "platform": plat,
                "device": device_desc, "recorded": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "screens": summarise(results, screens)}

    if spec.get("prepare"):
        print("Prepare steps (untimed, once)", file=sys.stderr, flush=True)
        with tempfile.TemporaryDirectory(prefix="jigx-prepare-") as tmp:
            path = os.path.join(tmp, "prepare.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(prepare_flow(app_id, spec["prepare"], plat))
            try:
                proc = subprocess.run([maestro] + (["--device", args.device] if args.device else []) + ["test", path],
                                      capture_output=True, text=True, env=maestro_env(), timeout=WARMUP_S)
                failed = proc.returncode != 0
            except subprocess.TimeoutExpired:
                failed = True
        if failed:
            print("The prepare steps failed, so nothing was timed: check the spec's \"prepare\" steps against the "
                  "screen (--ids prints its ids).", file=sys.stderr)
            return 1
    warm = [s for s in screens if s.get("start") != "cold"]
    cold = [s for s in screens if s.get("start") == "cold"]
    if warm and not args.no_warmup:
        print("Warm-up run (untimed; Maestro's first iOS run installs its driver)", file=sys.stderr, flush=True)
        _, err = run_once(app_id, warm[0], maestro, args.device, plat, reset, timeout_s=WARMUP_S)
        if err:
            print(f"warm-up: {err}", file=sys.stderr, flush=True)
    restart = False
    # Cold starts run after every other screen: a screen timed right after one meets an app
    # that is still starting, and fails or reads slow.
    for group in (warm, cold):
        for i in range(args.runs):
            for s in group:
                runs = results[s["name"]]
                if len(runs) >= MAX_FAILS_IN_A_ROW and all(r["ms"] is None for r in runs[-MAX_FAILS_IN_A_ROW:]):
                    runs.append({"run": i + 1, "ms": None, "error": f"skipped after {MAX_FAILS_IN_A_ROW} failed runs in a row"})
                    continue
                timing, err = run_once(app_id, s, maestro, args.device, plat, reset, restart)
                ms, settled = timing if timing else (None, None)
                restart = ms is None
                runs.append({"run": i + 1, "ms": ms,
                             **({"never_settled": True} if settled == -1 else {"settled_ms": settled} if settled is not None else {}),
                             **({"error": err} if err else {})})
                print(f"{s['name']} run {i + 1}: " + (f"{ms} ms" if ms is not None else f"failed ({err})"), file=sys.stderr, flush=True)
                with open(os.path.join(args.out, f"{args.label}.partial.json"), "w", encoding="utf-8") as f:
                    json.dump(result_now(), f, indent=2)
    result = result_now()
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
    try:
        os.remove(os.path.join(args.out, f"{args.label}.partial.json"))
    except OSError:
        pass
    text = report(result, comparison)
    with open(os.path.join(args.out, f"{args.label}.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    failed = sum(s["failed"] for s in result["screens"].values())
    return 1 if failed or (comparison and any(r["verdict"] == "SLOWER" for r in comparison["rows"])) else 0


if __name__ == "__main__":
    sys.exit(main())
