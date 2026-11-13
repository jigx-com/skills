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
        # the element is waited for before the clock starts; the timed tap does not wait to settle
        self.assertEqual(lines[start - 2], '    visible: {"text": "\\\\d{6}"}')
        self.assertEqual(lines[start + 1], '- tapOn: {"text": "\\\\d{6}", "index": 0, "retryTapIfNoChange": false, "waitToSettleTimeoutMs": 200}')
        self.assertEqual(lines[start + 3], '    visible: {"id": "details"}')
        self.assertIn("JIGX_SCREEN detail ms=", lines[start + 5])
        self.assertEqual(lines[start + 6], "- waitForAnimationToEnd:")
        self.assertIn("JIGX_SCREEN detail settled=", lines[start + 8])
        self.assertEqual(lines[-1], '- tapOn: "Back"')
        self.assertEqual(lines[lines.index('- tapOn: "Back"') - 2], '    visible: "Back"')
        with self.assertRaises(ValueError):
            ts.flow("x", {"name": "bad name", "tap": "a", "ready": "b"})

    def test_android_flow_matches_inside_elements_and_cold_start_times_the_launch(self):
        screen = {"name": "list", "setup": [{"wait": "Home"}], "tap": "View Appointments", "ready": r"\d{6}-\d+",
                  "after": {"android": [{"back": True}], "ios": [{"tap": "Back"}]}}
        android = ts.flow("com.jigx.app", screen, "android").splitlines()
        self.assertIn('    visible: "[\\\\s\\\\S]*(?:\\\\d{6}-\\\\d+)[\\\\s\\\\S]*"', android)
        self.assertIn('- tapOn: {"text": "View Appointments", "retryTapIfNoChange": false, "waitToSettleTimeoutMs": 200}', android)  # taps stay exact
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
        self.assertEqual(ts.parse_ms(log + "\nJsConsole: JIGX_SCREEN detail settled=8280", "detail", "settled"), 8280)
        self.assertIsNone(ts.parse_ms(log, "list"))

    def test_verdict_needs_runs_apart_and_a_real_difference(self):
        before = result({"same": [3082, 2934, 2868], "fast": [2400, 2500, 2450], "slow": [2000, 2050, 2100], "tiny": [1000, 1010, 1020]})
        after = result({"same": [3254, 3440, 2978], "fast": [1900, 1950, 2000], "slow": [2500, 2600, 2550], "tiny": [1100, 1110, 1120]})
        verdicts = {r["screen"]: r["verdict"] for r in ts.compare(before, after)}
        self.assertEqual(verdicts, {"same": "same", "fast": "faster", "slow": "SLOWER", "tiny": "same"})

    def test_runs_in_two_groups_are_unclear_not_same(self):
        # Time-Tracking-V2 timecards list: a focus sync in some runs
        before = result({"timecards": [3530, 1660, 1640]})
        after = result({"timecards": [3620, 3490, 1550]})
        row = ts.compare(before, after)[0]
        self.assertEqual(row["verdict"], "unclear (runs overlap)")
        self.assertEqual(row["after_runs"], [3620, 3490, 1550])
        text = "\n".join(ts.comparison_lines([row], "## x"))
        self.assertIn("3.53, 1.66, 1.64 → 3.62, 3.49, 1.55", text)

    def test_report_names_what_each_screen_is_timed_until(self):
        res = {"label": "pre", "device": "d", "appId": "a", "maestro": "2", "runs": 1,
               "screens": ts.summarise({"logs": [{"run": 1, "ms": 1900, "settled_ms": 8280}]},
                                       [{"name": "logs", "ready": {"id": "photo-log.number"}}])}
        text = ts.report(res)
        self.assertIn("| logs | `id photo-log.number` | 1.90 s | 1.90 | 8.28 s | 0 |", text)

    def test_a_screen_that_never_settles_says_so(self):
        res = {"label": "pre", "device": "d", "appId": "a", "maestro": "2", "runs": 1,
               "screens": ts.summarise({"s": [{"run": 1, "ms": 1900, "never_settled": True}]}, [{"name": "s", "ready": "x"}])}
        self.assertIn("did not settle in 15 s", ts.report(res))

    def test_ids_drop_layout_noise(self):
        tree = {"attributes": {"resource-id": "view.root"}, "children": [
            {"attributes": {"resource-id": "tab-bar.1-home", "accessibilityText": "Home"}, "children": []},
            {"attributes": {"resource-id": "icon.chevron"}},
            {"attributes": {"resource-id": "add-time.back.icon"}},
            {"attributes": {"resource-id": "timecard.card-1", "text": "Week 45"}, "children": [
                {"attributes": {"resource-id": "timecard.card-1", "text": "Week 45"}}]},
            {"attributes": {"text": "no id"}}]}
        self.assertEqual(ts.screen_ids(tree), [("tab-bar.1-home", "Home"), ("add-time.back.icon", ""), ("timecard.card-1", "Week 45")])

    def test_prepare_runs_once_as_its_own_flow(self):
        text = ts.prepare_flow("com.jigx.app", [{"tap": {"id": "project.BUDGETBYM"}}])
        self.assertIn('- tapOn: {"id": "project.BUDGETBYM"}', text)
        self.assertTrue(text.startswith("appId: com.jigx.app\n---\n- launchApp:"))

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
        orig = (ts.subprocess.run, ts.shutil.which, ts.describe_device, ts.maestro_env)
        try:
            ts.maestro_env = lambda: {}
            ts.subprocess.run = fake_run
            ts.shutil.which = lambda name: maestro
            ts.describe_device = lambda plat, dev=None: "test device"
            code = ts.main([spec_path, "--label", "pre", "--runs", "3", "--out", d, "--platform", "ios"])
        finally:
            ts.subprocess.run, ts.shutil.which, ts.describe_device, ts.maestro_env = orig
        self.assertEqual(code, 1)
        with open(os.path.join(d, "pre.json")) as f:
            saved = json.load(f)
        runs = saved["screens"]["a"]["runs"]
        self.assertIn("did not finish within", runs[0]["error"])
        self.assertIn("skipped after 2", runs[2]["error"])
        self.assertFalse(os.path.exists(os.path.join(d, "pre.partial.json")))
        # warm-up + 2 tried runs per screen (the third is skipped), plus --version
        self.assertEqual(len(calls), 1 + 1 + 2 * 2)

    def test_failed_prepare_times_nothing(self):
        import json, subprocess, tempfile
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[1:2] == ["--version"]:
                return subprocess.CompletedProcess(cmd, 0, "2.12.1\n", "")
            return subprocess.CompletedProcess(cmd, 1, "", "Element not found")
        d = tempfile.mkdtemp()
        spec_path = os.path.join(d, "screens.json")
        with open(spec_path, "w") as f:
            json.dump({"appId": "com.jigx.app", "prepare": [{"tap": {"id": "project.1"}}],
                       "screens": [{"name": "a", "tap": "A", "ready": "x"}]}, f)
        maestro = os.path.join(d, "maestro"); open(maestro, "w").close()
        orig = (ts.subprocess.run, ts.shutil.which, ts.describe_device, ts.maestro_env)
        try:
            ts.maestro_env = lambda: {}
            ts.subprocess.run = fake_run
            ts.shutil.which = lambda name: maestro
            ts.describe_device = lambda plat, dev=None: "test device"
            code = ts.main([spec_path, "--label", "pre", "--out", d, "--platform", "ios"])
        finally:
            ts.subprocess.run, ts.shutil.which, ts.describe_device, ts.maestro_env = orig
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2)  # --version, then the prepare flow; no warm-up, no screen
        self.assertFalse(os.path.exists(os.path.join(d, "pre.json")))

    def test_ids_can_follow_prepare_a_screen_path_and_taps(self):
        # Project Central: a sign-in clears the active project, so ids are only on screen after picking one.
        spec = {"appId": "com.jigx.app", "prepare": [{"tap": {"id": "project.BUDGETBYM"}}],
                "screens": [{"name": "photo-logs", "setup": [{"tap": "Photos"}], "tap": {"text": "Log \\d+", "index": 0}, "ready": "x"}]}
        lines = ts.path_flow("com.jigx.app", spec, "ios", at="photo-logs", taps=["id:photo-log.camera", "Save"]).splitlines()
        order = [lines.index('- tapOn: {"id": "project.BUDGETBYM"}'), lines.index('- tapOn: "Photos"'),
                 lines.index('- tapOn: {"text": "Log \\\\d+", "index": 0}'), lines.index('- tapOn: {"id": "photo-log.camera"}'),
                 lines.index('- tapOn: "Save"')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(lines[-2], "- waitForAnimationToEnd:")
        self.assertNotIn("evalScript", "\n".join(lines))  # nothing is timed
        with self.assertRaises(ValueError):
            ts.path_flow("com.jigx.app", spec, at="nope")

    def test_cold_start_reads_the_runtime_log(self):
        # Project Central: a 20 s cold start with no sync; the log said why (a gate, expression errors).
        import json, sqlite3, tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "jigx.log.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE _logMessages (id INTEGER PRIMARY KEY, timestamp INTEGER, state TEXT, message TEXT)")
        rows = [(1000, {"level": "INFO", "message": "started"}),
                (2000, {"level": "WARNING", "message": "Gate is still blocking after 18.5 s"}),
                (3000, {"level": "ERROR", "message": "Failed with error: Bearer abc.def T0410"}),
                (9000, {"level": "ERROR", "message": "after the run"})]
        for at, m in rows:
            conn.execute("INSERT INTO _logMessages (timestamp, state, message) VALUES (?, 'read', ?)", (at, json.dumps(m)))
        conn.commit(); conn.close()
        lines = ts.log_lines(path, 1500, 5000)
        self.assertEqual([x["level"] for x in lines], ["WARNING", "ERROR"])
        self.assertNotIn("abc.def", json.dumps(lines))
        res = {"label": "pre", "device": "d", "appId": "a", "maestro": "2", "runs": 2,
               "screens": ts.summarise({"cold-start": [{"run": 1, "ms": 20720, "log": lines}, {"run": 2, "ms": 19260, "log": lines[:1]}]},
                                       [{"name": "cold-start", "start": "cold", "ready": "Photos"}])}
        text = ts.report(res)
        self.assertIn("## Runtime log during cold-start", text)
        self.assertIn("Errors per run: 1, 0; warnings per run: 1, 1", text)
        self.assertIn("- WARNING x2: Gate is still blocking after 18.5 s", text)
        self.assertEqual(ts.log_lines(None, 0, 1), [])


if __name__ == "__main__":
    unittest.main()
