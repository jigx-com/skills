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

    def test_parses_the_logged_time(self):
        log = "Orchestra: JsConsole: JIGX_SCREEN detail ms=2121\nRun ${console.log('JIGX_SCREEN detail ms=' ...)}"
        self.assertEqual(ts.parse_ms(log, "detail"), 2121)
        self.assertIsNone(ts.parse_ms(log, "list"))

    def test_verdict_needs_runs_apart_and_a_real_difference(self):
        before = result({"same": [3082, 2934, 2868], "fast": [2400, 2500, 2450], "slow": [2000, 2050, 2100], "tiny": [1000, 1010, 1020]})
        after = result({"same": [3254, 3440, 2978], "fast": [1900, 1950, 2000], "slow": [2500, 2600, 2550], "tiny": [1100, 1110, 1120]})
        verdicts = {r["screen"]: r["verdict"] for r in ts.compare(before, after)}
        self.assertEqual(verdicts, {"same": "same", "fast": "faster", "slow": "SLOWER", "tiny": "same"})


if __name__ == "__main__":
    unittest.main()
