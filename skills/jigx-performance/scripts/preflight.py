#!/usr/bin/env python3
"""What a performance review would work on, and what timing still needs: read-only.

Standard library only. Prints, for the solution folder (default: the current folder):
the solution and its type, the git branch, commit and uncommitted changes, the booted iOS
simulators and connected Android devices with the Jigx apps on them, and the tools each
timing step needs (Node 22.5+, Maestro, Java). Run it before anything else and show the
result to the user, so the branch and device are confirmed before any work starts.
Prints JSON with --json. Read SKILL.md "Start".

Example:
  python3 preflight.py path/to/solution
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys


def run(cmd, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, cwd=cwd)
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
    try:
        data = json.loads(run(["xcrun", "simctl", "list", "devices", "booted", "-j"]) or "{}")
    except ValueError:
        data = {}
    out = []
    for runtime, devices in data.get("devices", {}).items():
        for d in devices:
            apps = re.findall(r'CFBundleIdentifier = "?(com\.jigx\.[\w.-]+)"?;', run(["xcrun", "simctl", "listapps", d["udid"]]))
            out.append({"name": d["name"], "udid": d["udid"], "runtime": runtime.split(".")[-1], "jigx_apps": apps})
    return out


def android():
    adb = shutil.which("adb") or os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    if not os.path.exists(adb):
        return []
    out = []
    for serial in re.findall(r"^(\S+)\tdevice$", run([adb, "devices"]), re.M):
        sh = lambda *c: run([adb, "-s", serial, "shell", *c]).strip()
        apps = re.findall(r"^package:(com\.jigx\.[\w.]+)$", sh("pm", "list", "packages", "com.jigx"), re.M)
        out.append({"serial": serial, "model": sh("getprop", "ro.product.model"), "emulator": sh("getprop", "ro.kernel.qemu") == "1",
                    # adb root, which sync timing needs, works on debuggable builds (Google APIs
                    # images are userdebug; Google Play images are user builds and refuse it).
                    "rootable": sh("getprop", "ro.build.type") in ("userdebug", "eng"),
                    "jigx_apps": apps})
    return out


def tools():
    node = run(["node", "--version"]).strip()
    m = re.match(r"v(\d+)\.(\d+)", node)
    maestro = shutil.which("maestro") or (os.path.expanduser("~/.maestro/bin/maestro") if os.path.exists(os.path.expanduser("~/.maestro/bin/maestro")) else None)
    return {"node": node or None, "node_ok": bool(m) and (int(m.group(1)), int(m.group(2))) >= (22, 5),
            "maestro": bool(maestro), "java": bool(run(["java", "-version"]) or shutil.which("java"))}


def preflight(folder):
    return {"folder": os.path.abspath(folder), "solution": solution(folder), "git": git(folder),
            "ios": ios(), "android": android(), "tools": tools()}


def text(r):
    s, g, t = r["solution"], r["git"], r["tools"]
    lines = [f"Solution: {s['name'] or '?'} ({s['type'] or 'no index.jigx or src/app.ts here'}) in {r['folder']}"]
    lines.append(f"Branch: {g['branch']} at {g['commit']}" + (f", {g['uncommitted']} uncommitted change(s)" if g["uncommitted"] else ", clean")
                 if g else "Branch: not a git checkout")
    devices = [f"iOS {d['name']} ({d['runtime']}): " + (", ".join(d["jigx_apps"]) or "no Jigx app installed") for d in r["ios"]]
    devices += [f"Android {d['model']} {d['serial']}" + (" emulator" if d["emulator"] else " phone") + ": "
                + (", ".join(d["jigx_apps"]) or "no Jigx app installed")
                + ("" if d["rootable"] or not d["emulator"] else " (Google Play image: no adb root, so screens only, no sync timing)") for d in r["android"]]
    lines.append("Devices: " + ("; ".join(devices) if devices else "none booted or connected"))
    missing = []
    if not t["node_ok"]:
        missing.append(f"Node 22.5+ for query timing (have {t['node'] or 'none'})")
    if not t["maestro"]:
        missing.append("Maestro for screen timing: brew install mobile-dev-inc/tap/maestro")
    if not t["java"]:
        missing.append("Java 17+ for Maestro")
    if not devices:
        missing.append("a device for sync and screen timing: boot an iOS Simulator (DeviceHub in Xcode 27) or an Android emulator, with the Jigx app")
    lines.append("Missing for timing: " + ("; ".join(missing) if missing else "nothing"))
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("folder", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    r = preflight(args.folder)
    print(json.dumps(r, indent=2) if args.json else text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
