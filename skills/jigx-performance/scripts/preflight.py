#!/usr/bin/env python3
"""What a performance review would work on, and what timing still needs: read-only.

Standard library only. Prints, for the solution folder (default: the current folder):
the solution and its type, the git branch, commit and uncommitted changes, the booted iOS
simulators and connected Android devices with the Jigx apps on them, and, numbered, each
setup step timing still needs with the command that fixes it (Xcode and an iOS runtime, a
simulator, the Android SDK, a Google APIs emulator, the Jigx app, Node 22.5+, Maestro,
Java 17+). It never installs or boots anything: Claude walks the user through the steps
with references/device-setup.md and runs each command only after the user agrees.
Run it before anything else and show the result to the user, so the branch and device are
confirmed before any work starts. With --code-only (an audit with no device) it checks only
the solution, the branch and what the audit needs (Node 18+, Ruby for YAML), and touches no
simulator or emulator. Prints JSON with --json. Read SKILL.md "Start".

Example:
  python3 preflight.py path/to/solution
  python3 preflight.py --code-only path/to/solution
"""
import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys


def run(cmd, cwd=None, stderr=False):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, cwd=cwd)
        if stderr:  # java -version writes to stderr
            return p.stdout + p.stderr
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def solution(folder):
    if os.path.exists(os.path.join(folder, "index.jigx")):
        with open(os.path.join(folder, "index.jigx"), encoding="utf-8") as f:
            m = re.search(r"^name:[ \t]*(.+)$", f.read(), re.M)
        return {"type": "YAML", "name": m.group(1).strip().strip("'\"") if m else None}
    if os.path.exists(os.path.join(folder, "src", "app.ts")):
        return {"type": "SDK", "name": os.path.basename(os.path.abspath(folder))}
    return {"type": None, "name": None}


def git(folder):
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], folder).strip()
    if not branch:
        return None
    if branch == "HEAD":
        branch = "detached HEAD"
    dirty = [l for l in run(["git", "status", "--porcelain", "--", "."], folder).splitlines() if l.strip()]
    return {"branch": branch, "commit": run(["git", "log", "-1", "--format=%h %s"], folder).strip()[:80],
            "uncommitted": len(dirty)}


def ios():
    """Booted simulators with their Jigx apps, the shut-down iPhones, and the iOS runtimes."""
    try:
        data = json.loads(run(["xcrun", "simctl", "list", "devices", "-j"]) or "{}")
    except ValueError:
        data = {}
    booted, shutdown = [], []
    for runtime, devices in data.get("devices", {}).items():
        if ".iOS-" not in runtime:
            continue
        for d in devices:
            if not d.get("isAvailable", True):
                continue
            entry = {"name": d["name"], "udid": d["udid"], "runtime": runtime.split(".")[-1]}
            if d.get("state") == "Booted":
                entry["jigx_apps"] = sorted(set(re.findall(r'CFBundleIdentifier = "?(com\.jigx\.[\w.-]+)"?;',
                                                           run(["xcrun", "simctl", "listapps", d["udid"]]))))
                booted.append(entry)
            elif d["name"].startswith("iPhone"):
                shutdown.append(entry)
    runtimes = re.findall(r"^(iOS [\d.]+)", run(["xcrun", "simctl", "list", "runtimes"]), re.M)
    return booted, shutdown, runtimes


def android_sdk():
    for p in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
              os.path.expanduser("~/Library/Android/sdk"), "/opt/homebrew/share/android-commandlinetools"):
        if p and os.path.isdir(p):
            return p
    adb = shutil.which("adb")
    return os.path.dirname(os.path.dirname(os.path.realpath(adb))) if adb else None


def sdk_tool(sdk, *parts, name):
    path = os.path.join(sdk, *parts) if sdk else None
    return path if path and os.path.exists(path) else shutil.which(name)


def avds():
    """Each AVD with whether its image allows adb root (Google APIs) or not (Google Play)."""
    out = []
    for ini in sorted(glob.glob(os.path.expanduser("~/.android/avd/*.avd/config.ini"))):
        with open(ini, encoding="utf-8", errors="replace") as f:
            cfg = f.read()
        tag = re.search(r"^tag\.id\s*=\s*(\S+)", cfg, re.M)
        out.append({"name": os.path.basename(os.path.dirname(ini))[:-4], "image": tag.group(1) if tag else None,
                    "google_apis": bool(tag) and tag.group(1) in ("google_apis", "default", "aosp_atd", "google_atd")})
    return out


def android(adb):
    """Ready devices with their Jigx apps, and devices adb sees but cannot use yet."""
    if not adb:
        return [], []
    listing = run([adb, "devices"])
    out = []
    for serial in re.findall(r"^(\S+)\tdevice$", listing, re.M):
        sh = lambda *c: run([adb, "-s", serial, "shell", *c]).strip()
        apps = re.findall(r"^package:(com\.jigx\.[\w.]+)$", sh("pm", "list", "packages", "com.jigx"), re.M)
        out.append({"serial": serial, "model": sh("getprop", "ro.product.model"), "emulator": sh("getprop", "ro.kernel.qemu") == "1",
                    # adb root, which sync timing needs, works on debuggable builds (Google APIs
                    # images are userdebug; Google Play images are user builds and refuse it).
                    "rootable": sh("getprop", "ro.build.type") in ("userdebug", "eng"),
                    "jigx_apps": apps})
    other = [{"serial": s, "state": st} for s, st in re.findall(r"^(\S+)\t(offline|unauthorized|no permissions.*)$", listing, re.M)]
    return out, other


def tools():
    node = run(["node", "--version"]).strip()
    m = re.match(r"v(\d+)\.(\d+)", node)
    maestro = shutil.which("maestro") or (os.path.expanduser("~/.maestro/bin/maestro") if os.path.exists(os.path.expanduser("~/.maestro/bin/maestro")) else None)
    # /usr/bin/java on macOS is a stub that exists even with no JDK installed.
    java = java_version(run(["java", "-version"], stderr=True))
    ok = bool(java) and java >= 17
    return {"node": node or None, "node_ok": bool(m) and (int(m.group(1)), int(m.group(2))) >= (22, 5),
            "maestro": bool(maestro), "java": java, "java_ok": ok,
            # A Java 17+ that is installed but not the one on the path (Homebrew's openjdk is keg-only,
            # and a JAVA_HOME pinned in ~/.zshrc wins): point JAVA_HOME at it instead of installing.
            "java_home_17": None if ok else installed_java_17(),
            "brew": bool(shutil.which("brew"))}


def java_version(text):
    jv = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    return None if not jv else (int(jv.group(2)) if jv.group(1) == "1" else int(jv.group(1)))


def installed_java_17():
    """A JDK 17 or newer home on this Mac that is not the active java, if there is one."""
    homes = []
    out = run(["/usr/libexec/java_home", "-V"], stderr=True)
    homes += re.findall(r'^\s*(\d+)[^\n]*?(/\S+/Contents/Home)\s*$', out, re.M)
    prefix = run(["brew", "--prefix"]).strip() or "/opt/homebrew"
    for keg in ("openjdk@17", "openjdk@21", "openjdk"):
        home = os.path.join(prefix, "opt", keg, "libexec", "openjdk.jdk", "Contents", "Home")
        java = os.path.join(home, "bin", "java")
        if os.path.exists(java):
            v = java_version(run([java, "-version"], stderr=True))
            if v:
                homes.append((str(v), home))
    good = [h for v, h in homes if int(v) >= 17]
    return good[0] if good else None


def audit_needs(sol):
    """What the code-only audit needs: Node 18+, and Ruby to read a YAML folder."""
    node = run(["node", "--version"]).strip()
    m = re.match(r"v(\d+)", node)
    missing = []
    if not (m and int(m.group(1)) >= 18):
        missing.append({"for": "audit", "problem": f"Node 18+ needed (have {node or 'none'})", "fix": "brew install node@22 (or: nvm install 22)"})
    if sol["type"] == "YAML" and not shutil.which("ruby"):
        missing.append({"for": "audit", "problem": "Ruby needed to read the YAML files", "fix": "brew install ruby"})
    return missing


def review_folder(folder, sol, g):
    """Where the review's files go: outside the solution repo, one folder per solution and branch."""
    name = sol["name"] or os.path.basename(os.path.abspath(folder))
    branch = g["branch"] if g and g["branch"] != "detached HEAD" else (g["commit"].split()[0] if g and g["commit"] else "no-git")
    slug = lambda v: re.sub(r"[^\w.-]+", "-", v).strip("-") or "x"
    return os.path.join(os.path.expanduser("~/jigx-perf-reviews"), slug(name), slug(branch))


def code_only(folder):
    sol, g = solution(folder), git(folder)
    return {"folder": os.path.abspath(folder), "solution": sol, "git": g, "audit_needs": audit_needs(sol),
            "review_folder": review_folder(folder, sol, g)}


def preflight(folder):
    mac = sys.platform == "darwin"
    booted, shutdown, runtimes = ios() if mac else ([], [], [])
    sdk = android_sdk()
    adb = sdk_tool(sdk, "platform-tools", "adb", name="adb")
    devices, other = android(adb)
    env = {"mac": mac, "arch": "arm64-v8a" if platform.machine() in ("arm64", "aarch64") else "x86_64",
           "xcode": mac and "Xcode" in run(["xcode-select", "-p"]),
           "ios_runtimes": runtimes, "ios_shutdown": shutdown, "android_sdk": sdk, "adb": bool(adb),
           "emulator": bool(sdk_tool(sdk, "emulator", "emulator", name="emulator")),
           "sdkmanager": bool(sdk_tool(sdk, "cmdline-tools", "latest", "bin", "sdkmanager", name="sdkmanager")),
           "avds": avds(), "android_other": other}
    sol, g = solution(folder), git(folder)
    return {"folder": os.path.abspath(folder), "solution": sol, "git": g, "review_folder": review_folder(folder, sol, g),
            "ios": booted, "android": devices, "tools": tools(), "env": env}


def setup_steps(r):
    """Each missing piece as {for, problem, fix}: the fix is a command or a short instruction.
    Steps the user needs sudo or a GUI for say so; device-setup.md has the detail."""
    t, e, steps = r["tools"], r["env"], []
    add = lambda area, problem, fix: steps.append({"for": area, "problem": problem, "fix": fix})
    java_missing = not t["java_ok"] and not t.get("java_home_17")
    if not t.get("brew") and e["mac"] and (not t["node_ok"] or not t["maestro"] or java_missing):
        add("installs", "Homebrew is not installed",
            '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
    if not t["node_ok"]:
        add("query timing", f"Node 22.5+ needed (have {t['node'] or 'none'})", "brew install node@22 (or: nvm install 22)")
    # A Java 17+ that is installed but not active is no step: time-screens.py runs Maestro with it
    # (JAVA_HOME for that process only). The shell profile may pin the old Java on purpose.
    if not t["java_ok"] and not t.get("java_home_17"):
        add("screen timing", f"Java 17+ needed for Maestro (have {t['java'] or 'none'})",
            "brew install openjdk@17, then: export JAVA_HOME=$(/usr/libexec/java_home -v 17)")
    if not t["maestro"]:
        add("screen timing", "Maestro is not installed", "brew install mobile-dev-inc/tap/maestro")

    # iOS
    if e["mac"]:
        if not e["xcode"]:
            add("iOS", "Xcode is not installed or not selected",
                "install Xcode from the App Store, then (needs your password): sudo xcode-select -s "
                "/Applications/Xcode.app/Contents/Developer && sudo xcodebuild -runFirstLaunch")
        elif not e["ios_runtimes"]:
            add("iOS", "no iOS Simulator runtime installed", "xcodebuild -downloadPlatform iOS")
        elif not r["ios"]:
            sim = e["ios_shutdown"][0] if e["ios_shutdown"] else None
            add("iOS", "no iOS Simulator booted",
                f"xcrun simctl boot {sim['udid']}  ({sim['name']}, {sim['runtime']}); then open DeviceHub to see it: "
                "open /Applications/Xcode.app/Contents/Applications/DeviceHub.app" if sim else
                'xcrun simctl create "iPhone for Jigx" "iPhone 17 Pro", then boot it with xcrun simctl boot <udid>')
        for d in r["ios"]:
            if not d["jigx_apps"]:
                add("iOS", f"{d['name']} has no Jigx app",
                    f"xcrun simctl install {d['udid']} <the Jigx simulator build>.app, then sign in with the test user")

    # Android
    if not e["android_sdk"]:
        add("Android", "Android SDK not found",
            "brew install --cask android-studio, open it once and finish the setup wizard (it installs the SDK in ~/Library/Android/sdk)")
    else:
        if not e["adb"]:
            add("Android", "adb (platform-tools) not found", 'sdkmanager "platform-tools"')
        image = f"system-images;android-34;google_apis;{e['arch']}"
        good = [a for a in e["avds"] if a["google_apis"]]
        running_emus = [d for d in r["android"] if d["emulator"]]
        if not e["emulator"]:
            add("Android", "Android emulator not installed", f'sdkmanager "emulator" "{image}"')
        elif not good:
            fix = (f'sdkmanager "{image}" && avdmanager create avd -n jigx-api34 -k "{image}" -d pixel_6'
                   if e["sdkmanager"] else
                   "in Android Studio: Device Manager > Create device > Pixel 6 > a Google APIs image "
                   "(not Google Play), API 34")
            add("Android", "no Google APIs emulator (sync timing needs adb root; Google Play images refuse it)"
                + (f"; existing: {', '.join(a['name'] for a in e['avds'])}" if e["avds"] else ""), fix)
        elif not r["android"]:
            add("Android", "no Android emulator running",
                f"emulator -avd {good[0]['name']} -no-snapshot-load &  (then wait for the home screen)")
        for d in running_emus:
            if not d["rootable"]:
                add("Android", f"emulator {d['serial']} is a Google Play image: screens only, no sync timing",
                    f"close it and start a Google APIs one" + (f": emulator -avd {good[0]['name']} &" if good else ""))
    for o in e["android_other"]:
        add("Android", f"{o['serial']} is {o['state']}",
            "accept the USB debugging prompt on the phone" if o["state"] == "unauthorized"
            else "adb kill-server && adb start-server (an emulator: close it and cold boot with -no-snapshot-load)")
    for d in r["android"]:
        if not d["jigx_apps"]:
            add("Android", f"{d['model']} {d['serial']} has no Jigx app",
                "adb -s " + d["serial"] + " install <jigx>.apk (an .aab: convert it with bundletool first, "
                "device-setup.md Android step 4), then sign in with the test user")
    return steps


def head(r):
    s, g = r["solution"], r["git"]
    lines = [f"Solution: {s['name'] or '?'} ({s['type'] or 'no index.jigx or src/app.ts here'}) in {r['folder']}"]
    lines.append(f"Branch: {g['branch']} at {g['commit']}" + (f", {g['uncommitted']} uncommitted change(s)" if g["uncommitted"] else ", clean")
                 if g else "Branch: not a git checkout")
    if r.get("review_folder"):
        lines.append(f"Review folder: {r['review_folder']} (outside the repo; nothing is written into the checkout)")
    return lines


def code_only_text(r):
    lines = head(r)
    needs = r["audit_needs"]
    lines.append("Audit needs: " + ("nothing missing (code check only: no simulator or emulator)" if not needs else
                                    "; ".join(f"{n['problem']}, fix: {n['fix']}" for n in needs)))
    return "\n".join(lines)


def text(r):
    lines = head(r)
    devices = [f"iOS {d['name']} ({d['runtime']}): " + (", ".join(d["jigx_apps"]) or "no Jigx app installed") for d in r["ios"]]
    devices += [f"Android {d['model']} {d['serial']}" + (" emulator" if d["emulator"] else " phone") + ": "
                + (", ".join(d["jigx_apps"]) or "no Jigx app installed")
                + ("" if d["rootable"] or not d["emulator"] else " (Google Play image: no adb root, so screens only, no sync timing)") for d in r["android"]]
    lines.append("Devices: " + ("; ".join(devices) if devices else "none booted or connected"))
    t = r["tools"]
    if not t["java_ok"] and t.get("java_home_17"):
        lines.append(f"Java: {t['java'] or 'none'} is active; time-screens.py runs Maestro with the Java 17+ in "
                     f"{t['java_home_17']} (nothing to change; to run maestro yourself: JAVA_HOME=\"{t['java_home_17']}\" maestro ...)")
    steps = setup_steps(r)
    if not steps:
        lines.append("Setup for timing: nothing missing")
    else:
        lines.append(f"Setup for timing: {len(steps)} step(s), see references/device-setup.md")
        lines += [f"  {i}. [{st['for']}] {st['problem']}\n     fix: {st['fix']}" for i, st in enumerate(steps, 1)]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--code-only", action="store_true", help="solution, branch and audit needs only; no device checks")
    args = p.parse_args(argv)
    if args.code_only:
        r = code_only(args.folder)
        print(json.dumps(r, indent=2) if args.json else code_only_text(r))
        return 0
    r = preflight(args.folder)
    if args.json:
        r["setup"] = setup_steps(r)
    print(json.dumps(r, indent=2) if args.json else text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
