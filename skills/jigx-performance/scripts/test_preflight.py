"""Tests for preflight.py. Run: python3 -m unittest discover -s skills/jigx-performance/scripts -p 'test_*.py'"""
import importlib.util
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("preflight", os.path.join(HERE, "preflight.py"))
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)


class PreflightTest(unittest.TestCase):
    def test_reports_solution_branch_and_changes(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "index.jigx"), "w") as f:
            f.write("name: demo\ntitle: Demo\n")
        git = lambda *a: subprocess.run(["git", *a], cwd=d, capture_output=True, check=True)
        git("init", "-q", "-b", "feature/x")
        git("add", "index.jigx")
        git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "start")
        with open(os.path.join(d, "new.jigx"), "w") as f:
            f.write("title: New\n")
        r = {"folder": d, "solution": pf.solution(d), "git": pf.git(d), "ios": [], "android": [],
             "tools": {"node": "v20.1.0", "node_ok": False, "maestro": True, "java": 17, "java_ok": True, "brew": True},
             "env": env()}
        self.assertEqual(r["solution"], {"type": "YAML", "name": "demo"})
        self.assertEqual((r["git"]["branch"], r["git"]["uncommitted"]), ("feature/x", 1))
        out = pf.text(r)
        self.assertIn("Branch: feature/x at", out)
        self.assertIn("1 uncommitted change(s)", out)
        self.assertIn("Devices: none booted or connected", out)
        self.assertIn("Node 22.5+", out)
        self.assertIn("fix: xcrun simctl boot SIM-1", out)

    def test_ready_machine_needs_nothing(self):
        r = report(ios=[{"name": "iPhone 18 Pro", "udid": "SIM-1", "runtime": "iOS-27-0", "jigx_apps": ["com.jigx.frontline"]}],
                   android=[emu(rootable=True)])
        self.assertEqual(pf.setup_steps(r), [])
        self.assertIn("Setup for timing: nothing missing", pf.text(r))

    def test_old_java_and_missing_maestro(self):
        r = report(tools={"java": 11, "java_ok": False, "maestro": False})
        fixes = " | ".join(s["fix"] for s in pf.setup_steps(r))
        self.assertIn("openjdk@17", fixes)
        self.assertIn("mobile-dev-inc/tap/maestro", fixes)

    def test_no_xcode_needs_sudo_select(self):
        steps = pf.setup_steps(report(env={"xcode": False}))
        self.assertIn("sudo xcode-select", steps[0]["fix"])

    def test_play_only_avd_suggests_google_apis_image(self):
        r = report(android=[], env={"avds": [{"name": "pixel-play", "image": "google_apis_playstore", "google_apis": False}]})
        step = [s for s in pf.setup_steps(r) if s["for"] == "Android"][0]
        self.assertIn("no Google APIs emulator", step["problem"])
        self.assertIn("google_apis;arm64-v8a", step["fix"])
        self.assertIn("pixel-play", step["problem"])

    def test_google_apis_avd_not_running_is_started(self):
        r = report(android=[])
        self.assertIn("emulator -avd jigx-api34", android_steps(r)[0]["fix"])

    def test_play_emulator_running_and_unauthorized_phone(self):
        r = report(android=[emu(rootable=False)], env={"android_other": [{"serial": "R5C", "state": "unauthorized"}]})
        problems = " | ".join(s["problem"] for s in pf.setup_steps(r))
        self.assertIn("Google Play image", problems)
        self.assertIn("R5C is unauthorized", problems)

    def test_device_without_jigx_app(self):
        r = report(ios=[{"name": "iPhone 18 Pro", "udid": "SIM-1", "runtime": "iOS-27-0", "jigx_apps": []}],
                   android=[emu(rootable=True, apps=[])])
        fixes = " | ".join(s["fix"] for s in pf.setup_steps(r))
        self.assertIn("xcrun simctl install SIM-1", fixes)
        self.assertIn("adb -s emulator-5554 install", fixes)

    def test_no_android_sdk(self):
        r = report(android=[], env={"android_sdk": None, "adb": False, "emulator": False, "avds": []})
        self.assertIn("android-studio", android_steps(r)[0]["fix"])

    def test_java_version_parsing(self):
        orig = pf.run
        try:
            for out, want in (('openjdk version "11.0.20.1" 2023-08-24', 11), ('java version "1.8.0_391"', 8),
                              ('openjdk version "17.0.9"', 17), ("The operation couldn't be completed. Unable to locate a Java Runtime.", None)):
                pf.run = lambda cmd, cwd=None, stderr=False, o=out: o if cmd[0] == "java" else ""
                self.assertEqual(pf.tools()["java"], want, out)
        finally:
            pf.run = orig

    def test_code_only_touches_no_device(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "index.jigx"), "w") as f:
            f.write("name: demo\n")
        calls, orig = [], pf.run
        def fake(cmd, cwd=None, stderr=False):
            calls.append(cmd[0])
            return "v22.16.0" if cmd[0] == "node" else ""
        try:
            pf.run = fake
            r = pf.code_only(d)
        finally:
            pf.run = orig
        self.assertNotIn("xcrun", calls)
        self.assertNotIn("adb", " ".join(calls))
        self.assertEqual(r["solution"]["type"], "YAML")
        out = pf.code_only_text(r)
        self.assertNotIn("Devices", out)
        self.assertIn("Branch: not a git checkout", out)

    def test_code_only_old_node(self):
        orig = pf.run
        try:
            pf.run = lambda cmd, cwd=None, stderr=False: "v16.20.0" if cmd[0] == "node" else ""
            needs = pf.audit_needs({"type": "SDK", "name": "x"})
        finally:
            pf.run = orig
        self.assertIn("Node 18+", needs[0]["problem"])

    def test_installed_java_17_is_used_not_reinstalled_and_the_profile_is_left_alone(self):
        # A tester's ~/.zshrc pinned Java 11 on purpose: the fix must not edit it
        home = "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"
        r = report(tools={"java": 11, "java_ok": False, "java_home_17": home})
        self.assertFalse([s for s in pf.setup_steps(r) if "Java" in s["problem"]])
        text = pf.text(r)
        self.assertIn(f"runs Maestro with the Java 17+ in {home}", text)
        self.assertNotIn("zshrc", text)
        no_brew = report(tools={"java": 11, "java_ok": False, "java_home_17": home, "brew": False})
        self.assertFalse([s for s in pf.setup_steps(no_brew) if "Homebrew" in s["problem"]])
        none = report(tools={"java": 11, "java_ok": False, "java_home_17": None})
        self.assertIn("brew install openjdk@17", [s for s in pf.setup_steps(none) if "Java" in s["problem"]][0]["fix"])

def android_steps(r):
    return [s for s in pf.setup_steps(r) if s["for"] == "Android"]


def env(**over):
    e = {"mac": True, "arch": "arm64-v8a", "xcode": True, "ios_runtimes": ["iOS 27.0"],
         "ios_shutdown": [{"name": "iPhone 18 Pro", "udid": "SIM-1", "runtime": "iOS-27-0"}],
         "android_sdk": "/sdk", "adb": True, "emulator": True, "sdkmanager": True,
         "avds": [{"name": "jigx-api34", "image": "google_apis", "google_apis": True}], "android_other": []}
    e.update(over)
    return e


def emu(rootable, apps=("com.jigx.frontline",)):
    return {"serial": "emulator-5554", "model": "Pixel 6", "emulator": True, "rootable": rootable, "jigx_apps": list(apps)}


def report(ios=(), android=(), tools=None, env=None):
    t = {"node": "v22.16.0", "node_ok": True, "maestro": True, "java": 17, "java_ok": True, "brew": True}
    t.update(tools or {})
    return {"folder": "/x", "solution": {"type": "YAML", "name": "demo"}, "git": None, "ios": list(ios),
            "android": list(android), "tools": t, "env": globals()["env"](**(env or {}))}


if __name__ == "__main__":
    unittest.main()
