"""Tests for track-load.py, compare-runs.py and check-plans.py.
Run: python3 -m unittest skills/jigx-performance/scripts/test_load_tools.py"""
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tl = module("track_load", "track-load.py")
cr = module("compare_runs", "compare-runs.py")
cp = module("check_plans", "check-plans.py")
rr = module("review_report", "review-report.py")


class TrackLoadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "jigx.solution.___demo-test___.0000.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE _syncScope (id TEXT PRIMARY KEY, state TEXT, details TEXT, lastStartedAt INTEGER, lastSyncedAt INTEGER)")
        conn.execute("CREATE TABLE _commandQueue (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO _syncScope VALUES ('sync-base', 'synced', '{}', 1, 2)")  # left from an earlier run
        conn.commit()
        conn.close()
        log = sqlite3.connect(os.path.join(self.dir, "jigx.log.db"))
        log.execute("CREATE TABLE _logMessages (id INTEGER PRIMARY KEY, timestamp INTEGER, state TEXT, message TEXT)")
        log.execute("INSERT INTO _logMessages VALUES (1, 1, 'read', '{\"level\":\"ERROR\",\"message\":\"old\"}')")
        log.commit()
        log.close()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def drive(self):
        """Simulate a sync: two entries in sequence, a snapshot timeout, rows arriving."""
        time.sleep(0.15)
        start = int(time.time() * 1000)
        secret = {"functionParameters": {"auth": "Basic dXNlcjpwYXNz"}}
        conn = sqlite3.connect(self.db)
        log = sqlite3.connect(os.path.join(self.dir, "jigx.log.db"))
        steps = [({"a": "syncing", "b": "notStarted"}, "syncing", None),
                 ({"a": "synced", "b": "syncing"}, "syncing", None),
                 ({"a": "synced", "b": "synced"}, "synced", 1)]
        for i, (entries, state, done) in enumerate(steps):
            details = {k: {"state": v, **secret} for k, v in entries.items()}
            synced = int(time.time() * 1000) if done else None
            conn.execute("UPDATE _syncScope SET state=?, details=?, lastStartedAt=?, lastSyncedAt=? WHERE id='sync-base'",
                         (state, json.dumps(details), start, synced))
            conn.execute("INSERT INTO orders VALUES (?, '{}')", (str(i),))
            conn.commit()
            if i == 1:
                msg = {"level": "WARNING", "category": "datasource",
                       "message": "#3 'orders-list' Timeout for first snapshot, querying initial local results... Bearer abc.def"}
                log.execute("INSERT INTO _logMessages (timestamp, state, message) VALUES (?, 'read', ?)",
                            (int(time.time() * 1000), json.dumps(msg)))
                log.commit()
            time.sleep(0.12)
        conn.close()
        log.close()

    def test_tracks_until_ready_and_never_keeps_credentials(self):
        worker = threading.Thread(target=self.drive)
        worker.start()
        out = io.StringIO()
        summary = tl.track(self.db, "pre-1", os.path.join(self.dir, "runs"), tables=["orders"],
                           interval=0.02, settle=0.2, timeout=10, stream=out)
        worker.join()
        self.assertEqual(summary["result"], "ready")
        self.assertEqual(summary["solution"], "demo-test")
        self.assertEqual(summary["final_counts"], {"orders": 3})
        self.assertEqual(summary["snapshot_timeouts"], {"orders-list": 1})
        self.assertEqual(summary["log_errors"], 0)  # the ERROR line predates arming
        self.assertGreater(summary["first_start_to_ready_s"], 0)
        a, b = summary["entries"]["sync-base/a"], summary["entries"]["sync-base/b"]
        self.assertLess(a["start_s"], b["start_s"])
        self.assertLessEqual(a["end_s"], b["end_s"])
        with open(os.path.join(self.dir, "runs", "pre-1-samples.jsonl")) as f:
            text = f.read() + out.getvalue()
        text += json.dumps(summary)
        self.assertNotIn("dXNlcjpwYXNz", text)
        self.assertNotIn("abc.def", text)
        self.assertIn("Timeout for first snapshot", out.getvalue())

    def test_failed_entry_stops_the_run(self):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE _syncScope SET state='syncing', details=?, lastStartedAt=?",
                     (json.dumps({"a": {"state": "failed"}}), int(time.time() * 1000) + 60000))
        conn.commit()
        conn.close()
        summary = tl.track(self.db, "x", os.path.join(self.dir, "runs"), interval=0.02, settle=0.1, timeout=5,
                           stream=io.StringIO())
        self.assertEqual(summary["result"], "failed")
        self.assertIsNone(summary["first_start_to_ready_s"])

    def test_status_ignores_scopes_from_before_arming(self):
        state = {"scopes": {"s": {"state": "synced", "startedAt": 5, "syncedAt": 6, "entries": {}}}, "queue": 0}
        self.assertEqual(tl.status(state, [], armed=10), "waiting")
        self.assertEqual(tl.status(state, ["s"], armed=10), "waiting")
        self.assertEqual(tl.status(state, ["s"], armed=1), "ready")
        state["queue"] = 2
        self.assertEqual(tl.status(state, ["s"], armed=1), "loading")


def run(label, total, entry, machine="m", result="ready"):
    return {"label": label, "machine": machine, "result": result, "first_start_to_ready_s": total,
            "scopes": {"sync-base": {"duration_s": total}}, "entries": {"sync-base/a": {"duration_s": entry}},
            "snapshot_timeouts": {}, "log_errors": 0}


class CompareRunsTest(unittest.TestCase):
    def test_medians_and_verdicts(self):
        before = [run("pre-1", 20, 5), run("pre-2", 22, 5), run("pre-3", 30, 5)]
        after = [run("post-1", 10, 6), run("post-2", 11, 6), run("post-3", 12, 6)]
        report = cr.compare(before, after)
        rows = {r["metric"]: r for r in report["rows"]}
        total = rows["total: first start to ready"]
        self.assertEqual((total["before_s"], total["after_s"], total["change_pct"], total["verdict"]), (22, 11, -50.0, "improved"))
        self.assertEqual(rows["entry: sync-base/a"]["verdict"], "REGRESSION")
        self.assertEqual(report["warnings"], [])
        self.assertIn("| total: first start to ready | 22 | 11 | -50.0% | improved |", cr.markdown(report))

    def test_change_below_sampling_resolution_is_same(self):
        self.assertEqual(cr.change(0.52, 0.78, 10), (50.0, "same"))
        self.assertEqual(cr.change(0.52, 1.29, 10), (148.1, "REGRESSION"))
        self.assertEqual(cr.change(0.52, 0.78, 10, min_change=0.1), (50.0, "REGRESSION"))

    def test_warns_on_few_runs_mixed_machines_and_unready_runs(self):
        report = cr.compare([run("pre-1", 20, 5)], [run("post-1", None, 5, machine="n", result="timeout")])
        text = " ".join(report["warnings"])
        self.assertIn("only 1 before run", text)
        self.assertIn("not ready: post-1", text)
        self.assertIn("different machines", text)
        self.assertEqual(report["rows"][0]["verdict"], "missing")


INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_h_row ON [header] (trim(COALESCE(id, '')))",
    "CREATE INDEX IF NOT EXISTS idx_h_nbr ON [header] (trim(json_extract(data, '$.Nbr')))",
    "CREATE INDEX IF NOT EXISTS idx_h_unused ON [header] (json_extract(data, '$.Unused'))",
    "CREATE INDEX IF NOT EXISTS idx_bad ON [header] (",
]
TMP_INDEX = "CREATE INDEX IF NOT EXISTS idx_h_tmp ON [header] (trim(json_extract(data, '$.Tmp')))"
KEYS = ["trim(COALESCE(h.id, ''))", "trim(json_extract(h.data, '$.Nbr'))", "trim(json_extract(h.data, '$.Tmp'))"]
# One OR across three keys, one of them unindexed: SQLite falls back to scanning header.
OR_LOOKUP = ("SELECT q.id FROM [queue] AS q JOIN [header] AS h ON "
             + " OR ".join(f"{k} = json_extract(q.data, '$.key')" for k in KEYS))
# One branch per key, each with an index of exactly the same expression.
UNION_LOOKUP = "\nUNION ".join(
    f"SELECT q.id FROM [queue] AS q JOIN [header] AS h ON {k} = json_extract(q.data, '$.key')" for k in KEYS)


def app(query, indexes=INDEXES):
    return {"datasources": {"changed": {"type": "datasource.sqlite", "options": {"query": query, "rewriteQuery": False}}},
            "actions": {"create-indexes": {"action": {"type": "action.execute-sql", "options": {
                "entities": ["default/debug"], "statements": [{"statement": s} for s in indexes]}}}}}


class CheckPlansTest(unittest.TestCase):
    def test_or_across_keys_scans_and_union_per_key_uses_indexes(self):
        guard = {"changed": {"no_scan": ["header"], "uses": ["idx_h_row", "idx_h_nbr"]}}
        before = cp.check_plans(app(OR_LOOKUP), guard=guard)
        self.assertIn("$.datasources.changed: full scan of header", before["guard_failures"])
        after = cp.check_plans(app(UNION_LOOKUP, INDEXES + [TMP_INDEX]), guard=guard)
        self.assertEqual(after["guard_failures"], [])
        self.assertEqual(after["indexes_unused"], ["idx_h_unused"])
        self.assertEqual([f["index"] for f in after["indexes_failed"]], ["idx_bad"])

    def test_guard_reports_missing_query_and_alias_maps_to_table(self):
        report = cp.check_plans(app(OR_LOOKUP), guard={"nope": {"no_scan": ["header"]}})
        self.assertEqual(report["guard_failures"], ["nope: no query found"])
        self.assertEqual(cp.table_refs("SELECT 1 FROM [orders] AS o JOIN json_each(o.data) j"), {"orders": "orders", "o": "orders"})


class ReviewReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for side in ("before", "after", "runs"):
            os.makedirs(os.path.join(self.dir, side))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, rel, value):
        with open(os.path.join(self.dir, rel), "w") as f:
            json.dump(value, f)

    def test_baseline_overview_then_before_after_report(self):
        self.write("before/static.json", {"byCode": {"unindexed-predicate": 4, "per-row-datasource-read": 2}})
        self.write("before/plans.json", {"queries_with_full_scans": 5, "indexes_unused": ["a"], "indexes_failed": []})
        baseline = rr.report(self.dir, "demo")
        self.assertIn("Baseline only", baseline)
        self.assertIn("| C | Lists and rendering | per-row-datasource-read | 2 |", baseline)
        self.assertIn("No before/after load timing", baseline)

        self.write("after/static.json", {"byCode": {"unindexed-predicate": 1}})
        self.write("after/plans.json", {"queries_with_full_scans": 1, "indexes_unused": [], "indexes_failed": []})
        self.write("changes.json", [{"section": "B", "change": "Index orders by reference", "files": ["x.jigx"]}])
        self.write("runs/comparison.json", cr.compare(
            [run("pre-1", 20, 5), run("pre-2", 22, 5), run("pre-3", 30, 5)],
            [run("post-1", 10, 2), run("post-2", 11, 2), run("post-3", 12, 2)]))
        text = rr.report(self.dir, "demo")
        self.assertIn("**Time to ready: 22 s → 11 s (-50.0%, improved)**", text)
        self.assertIn("| C | Lists and rendering | per-row-datasource-read | 2 | 0 |", text)
        self.assertIn("| B | Queries and indexes | queries with full scans | 5 | 1 |", text)
        self.assertIn("- Index orders by reference (x.jigx)", text)
        self.assertIn("| entry: sync-base/a | 5 s | 2 s | -60.0% |", text)
        self.assertIn("Screen open times were not compared", text)

    def test_query_and_screen_timings(self):
        self.write("before/static.json", {"byCode": {}})
        os.makedirs(os.path.join(self.dir, "screens"))
        self.write("before/timings.json", {"meta": {"host": "M2", "rows": 900},
                                           "queries": [{"where": "datasources/a", "ms": 40, "rows": 12}], "actions": [], "indexes": []})
        self.write("screens/pre.json", {"device": "sim", "runs": 3, "screens": {"list": {"median_ms": 2100, "spread_ms": 90}}})
        baseline = rr.report(self.dir)
        self.assertIn("- Queries: 1, 40 ms in total", baseline)
        self.assertIn("| query | `datasources/a` | 40 ms | 12 |", baseline)
        self.assertIn("| list | 2.10 s | 90 ms |", baseline)
        self.write("after/timings.json", {"queries": [{"where": "datasources/a", "ms": 8}], "actions": [], "indexes": [],
                                          "comparison": [{"kind": "queries", "where": "datasources/a", "before_ms": 40, "after_ms": 8, "change_pct": -80, "verdict": "faster"}]})
        self.write("screens/post.json", {"device": "sim", "runs": 3, "screens": {}, "comparison": {"rows": [
            {"screen": "list", "before_ms": 2100, "after_ms": 1500, "change_pct": -28.6, "verdict": "faster"}]}})
        text = rr.report(self.dir)
        self.assertIn("- Queries: 1, 40 ms in total → 8 ms", text)
        self.assertIn("| query | `datasources/a` | 40 ms | 8 ms | -80% |", text)
        self.assertIn("| list | 2.10 s | 1.50 s | -28.6% | faster |", text)


if __name__ == "__main__":
    unittest.main()
