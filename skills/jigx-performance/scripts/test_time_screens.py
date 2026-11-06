import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location("time_screens", os.path.join(os.path.dirname(__file__), "time-screens.py"))
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


def result(runs):
    return {"screens": {name: ts.summarise({name: [{"run": i, "ms": ms} for i, ms in enumerate(v)]})[name] for name, v in runs.items()}}


class TimeScreensTest(unittest.TestCase):
    def test_flow_times_only_the_tap_to_ready_span(self):
        text = ts.flow("com.jigx.app", {"name": "detail", "setup": [{"tap": "Appointments"}, {"wait": r"\d{6}"}],
                                        "tap": {"text": r"\d{6}", "index": 0}, "ready": {"id": "details"}, "after": [{"tap": "Back"}]})
        lines = text.splitlines()
        start = lines.index("- evalScript: ${output.t0 = Date.now()}")
        self.assertLess(lines.index('- tapOn: "Appointments"'), start)
        self.assertEqual(lines[start + 1], '- tapOn: {"text": "\\\\d{6}", "index": 0}')
        self.assertEqual(lines[start + 3], '    visible: {"id": "details"}')
        self.assertIn("JIGX_SCREEN detail ms=", lines[start + 5])
        self.assertEqual(lines[-1], '- tapOn: "Back"')
        with self.assertRaises(ValueError):
            ts.flow("x", {"name": "bad name", "tap": "a", "ready": "b"})

    def test_android_flow_matches_inside_elements_and_cold_start_times_the_launch(self):
        screen = {"name": "list", "setup": [{"wait": "Home"}], "tap": "View Appointments", "ready": r"\d{6}-\d+",
                  "after": {"android": [{"back": True}], "ios": [{"tap": "Back"}]}}
        android = ts.flow("com.jigx.app", screen, "android").splitlines()
        self.assertIn('    visible: "[\\\\s\\\\S]*(?:\\\\d{6}-\\\\d+)[\\\\s\\\\S]*"', android)
        self.assertIn('- tapOn: "View Appointments"', android)  # taps stay exact
        self.assertEqual(android[-1], "- back")
        ios = ts.flow("com.jigx.app", screen, "ios").splitlines()
        self.assertIn('    visible: "\\\\d{6}-\\\\d+"', ios)
        self.assertEqual(ios[-1], '- tapOn: "Back"')
        cold = ts.flow("com.jigx.app", {"name": "start", "start": "cold", "ready": {"gone": "Preparing"}}).splitlines()
        self.assertEqual(cold[2], "- stopApp")
        start = cold.index("- evalScript: ${output.t0 = Date.now()}")
        self.assertEqual(cold[start + 1:start + 4], ["- launchApp:", "    stopApp: false", "- extendedWaitUntil:"])
        self.assertEqual(cold[start + 4], '    notVisible: "Preparing"')

    def test_parses_the_logged_time(self):
        log = "Orchestra: JsConsole: JIGX_SCREEN detail ms=2121\nRun ${console.log('JIGX_SCREEN detail ms=' ...)}"
        self.assertEqual(ts.parse_ms(log, "detail"), 2121)
        self.assertIsNone(ts.parse_ms(log, "list"))

    def test_verdict_needs_runs_apart_and_a_real_difference(self):
        before = result({"same": [3082, 2934, 2868], "fast": [2400, 2500, 2450], "slow": [2000, 2050, 2100], "tiny": [1000, 1010, 1020]})
        after = result({"same": [3254, 3440, 2978], "fast": [1900, 1950, 2000], "slow": [2500, 2600, 2550], "tiny": [1100, 1110, 1120]})
        verdicts = {r["screen"]: r["verdict"] for r in ts.compare(before, after)}
        self.assertEqual(verdicts, {"same": "same", "fast": "faster", "slow": "SLOWER", "tiny": "same"})

    def test_one_successful_run_gives_no_verdict(self):
        before = result({"control": [2000, 2050, 2100]})
        after = result({"control": [3400, None, None]})
        self.assertTrue(ts.compare(before, after)[0]["verdict"].startswith("not comparable"))

    def test_reset_and_restart_bring_every_run_back_to_the_start(self):
        screen = {"name": "list", "tap": "Schedule", "ready": r"\d{6}"}
        text = ts.flow("com.jigx.app", screen, "ios", reset=[{"tap": {"id": "tab-bar.1-home"}}], restart=True)
        lines = text.splitlines()
        self.assertEqual(lines[2], "- stopApp")
        self.assertLess(lines.index('- tapOn: {"id": "tab-bar.1-home"}'), lines.index("- evalScript: ${output.t0 = Date.now()}"))
        self.assertNotIn("- stopApp", ts.flow("com.jigx.app", screen, "ios"))

    def test_timeout_is_a_failed_run_and_results_survive(self):
        import json, subprocess, tempfile
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[1:2] == ["--version"]:
                return subprocess.CompletedProcess(cmd, 0, "2.12.1\n", "")
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        d = tempfile.mkdtemp()
        spec_path = os.path.join(d, "screens.json")
        with open(spec_path, "w") as f:
            json.dump({"appId": "com.jigx.app", "screens": [{"name": "a", "tap": "A", "ready": "x"}, {"name": "b", "tap": "B", "ready": "y"}]}, f)
        maestro = os.path.join(d, "maestro"); open(maestro, "w").close()
        orig = (ts.subprocess.run, ts.shutil.which, ts.describe_device)
        try:
            ts.subprocess.run = fake_run
            ts.shutil.which = lambda name: maestro
            ts.describe_device = lambda plat, dev=None: "test device"
            code = ts.main([spec_path, "--label", "pre", "--runs", "3", "--out", d, "--platform", "ios"])
        finally:
            ts.subprocess.run, ts.shutil.which, ts.describe_device = orig
        self.assertEqual(code, 1)
        with open(os.path.join(d, "pre.json")) as f:
            saved = json.load(f)
        runs = saved["screens"]["a"]["runs"]
        self.assertIn("did not finish within", runs[0]["error"])
        self.assertIn("skipped after 2", runs[2]["error"])
        self.assertFalse(os.path.exists(os.path.join(d, "pre.partial.json")))
        # warm-up + 2 tried runs per screen (the third is skipped), plus --version
        self.assertEqual(len(calls), 1 + 1 + 2 * 2)

if __name__ == "__main__":
    unittest.main()
