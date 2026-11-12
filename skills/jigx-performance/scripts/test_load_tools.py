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
                hashed = {"level": "WARNING", "category": "get-entity-id",
                          "message": "Performance Issue: Missing ID or value of entity (4 times) -> using hash function"}
                failed = {"level": "ERROR", "category": "solution.expression",
                          "message": 'Failed with error: Argument 1 of function "trim" does not match function signature',
                          "additionalParameters": [{"expression": "=$trim(@ctx.components.task.state.value)",
                                                    "evaluated": {"secret": "customer data"}, "screen": {"screenId": "add-expense"}}]}
                for m in (msg, hashed, failed, failed):
                    log.execute("INSERT INTO _logMessages (timestamp, state, message) VALUES (?, 'read', ?)",
                                (int(time.time() * 1000), json.dumps(m)))
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
        self.assertEqual(summary["log_errors"], 2)  # the old ERROR line predates arming; two expression errors
        self.assertEqual(summary["hash_fallbacks"], 4)
        self.assertEqual(summary["expression_errors"], [{"expression": "=$trim(@ctx.components.task.state.value)",
                                                         "error": 'Argument 1 of function "trim" does not match function signature',
                                                         "screen": "add-expense", "count": 2}])
        self.assertGreater(summary["first_start_to_ready_s"], 0)
        a, b = summary["entries"]["sync-base/a"], summary["entries"]["sync-base/b"]
        self.assertLess(a["start_s"], b["start_s"])
        self.assertLessEqual(a["end_s"], b["end_s"])
        with open(os.path.join(self.dir, "runs", "pre-1-samples.jsonl")) as f:
            text = f.read() + out.getvalue()
        text += json.dumps(summary)
        self.assertNotIn("dXNlcjpwYXNz", text)
        self.assertNotIn("abc.def", text)
        self.assertNotIn("customer data", text)
        self.assertIn("Timeout for first snapshot", out.getvalue())

    def test_copy_includes_rows_still_in_the_wal_file(self):
        # A plain cp of a WAL database left the rows behind: time-queries saw "0 rows in 0 tables"
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.executemany("INSERT INTO orders VALUES (?, '{}')", [(str(i),) for i in range(25)])
        conn.commit()  # the rows stay in the -wal file while this connection is open
        try:
            self.assertTrue(os.path.getsize(self.db + "-wal") > 0)
            out = os.path.join(self.dir, "copy", "device.db")
            rows = tl.copy_db(self.db, out)
            self.assertEqual(rows, 25)  # data rows: the 25 orders, not the runtime's _syncScope row
            self.assertFalse(os.path.exists(out + "-wal"))
            with sqlite3.connect(f"file:{out}?mode=ro", uri=True) as ro:  # opens on its own, read-only
                self.assertEqual(ro.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 25)
                self.assertEqual(ro.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            conn.close()

    def test_scope_names_that_match_nothing_are_reported_with_the_real_ones(self):
        # Project Central: progress datasource names (identity-data) instead of the instanceIds (sync-identity)
        state = {"scopes": {"sync-identity": {"state": "syncing", "startedAt": 5, "syncedAt": None, "entries": {}},
                            "sync-data": {"state": "pending", "startedAt": None, "syncedAt": None, "entries": {}}},
                 "queue": 0, "counts": {}}
        # sync-identity exists and is still syncing: sync-data's row may simply not exist yet
        self.assertEqual(tl.unknown_scopes(state, ["sync-identity", "later-scope"], armed=1), [])
        self.assertEqual(tl.unknown_scopes(state, ["identity-data"], armed=1), ["identity-data"])
        done = {"scopes": {"sync-identity": {"state": "synced", "startedAt": 5, "syncedAt": 9, "entries": {}}}, "queue": 0, "counts": {}}
        self.assertEqual(tl.unknown_scopes(done, ["sync-identity", "sync-data"], armed=1), ["sync-data"])
        self.assertEqual(tl.unknown_scopes({"scopes": {}, "queue": 0, "counts": {}}, ["x"]), [])  # nothing synced yet
        out = io.StringIO()
        t = threading.Thread(target=self.drive)
        t.start()
        summary = tl.track(self.db, "pre-1", self.dir, scopes=["base-data"], interval=0.05, settle=0.2, timeout=1.5, stream=out,
                           unknown_wait=0.2)
        t.join()
        self.assertEqual(summary["result"], "timeout")
        self.assertIn("No scope base-data in this solution", out.getvalue())
        self.assertIn("Scopes here: sync-base", out.getvalue())
        self.assertIn("sign out of the app and sign in again", out.getvalue())

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

    def test_sqlite_master_lookup_does_not_crash(self):
        exists = "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('header', 'queue')"
        report = cp.check_plans(app(exists))
        self.assertEqual(report["indexes_failed"][0]["index"], "idx_bad")

    def test_guard_reports_missing_query_and_alias_maps_to_table(self):
        report = cp.check_plans(app(OR_LOOKUP), guard={"nope": {"no_scan": ["header"]}})
        self.assertEqual(report["guard_failures"], ["nope: no query found"])
        self.assertEqual(cp.table_refs("SELECT 1 FROM [orders] AS o JOIN json_each(o.data) j"), {"orders": "orders", "o": "orders"})


class ReviewReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for side in ("before", "after", "runs", "screens"):
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

    def test_where_to_look_says_which_side_a_note_is_from(self):
        # Time-Tracking-V2: "synced more than once" stayed next to after values where it was fixed
        run = lambda label, repeated: {"label": label, "first_start_to_ready_s": 5.0, "sync_busy_s": 9.0, "result": "ready",
                                       "repeated_scopes": repeated, "entries": {}, "scopes": {}}
        for i in range(3):
            self.write(f"runs/pre-{i}-summary.json", run(f"pre-{i}", ["sync-data"]))
            self.write(f"runs/post-{i}-summary.json", run(f"post-{i}", []))
        self.write("before/static.json", {"byPriority": {"high": 5, "medium": 33, "low": 9}, "expressionSafety": {"medium": 24, "low": 0}})
        self.write("after/static.json", {"byPriority": {"high": 0, "medium": 26, "low": 9}, "expressionSafety": {"medium": 24, "low": 0}})
        text = rr.report(self.dir, "demo")
        self.assertIn("before: synced more than once: sync-data; after: none", text)
        self.assertIn("| Audit findings | 5 high · 33 medium (+24 expression safety) | 0 high · 26 medium (+24 expression safety) |", text)
        self.assertIn("Priority before: 5 high, 33 medium, 9 low, plus 24 expression-safety", text)

    def test_report_md_keeps_the_verdicts_of_steps_and_screens(self):
        # The reviewer found report.md printing bare percentages for "not comparable" and "within noise" rows
        e = lambda name, d: {name: {"start_s": 0.0, "end_s": d, "duration_s": d}}
        pre = [{"label": f"pre-{i}", "machine": "m", "result": "ready", "first_start_to_ready_s": 5.5, "interval_s": 0.25,
                "scopes": {}, "repeated_scopes": ["sync-lookups"], "snapshot_timeouts": {}, "log_errors": 0,
                "entries": {**e("sync-lookups/earningtypes", 2.34 + i / 100), **e("sync-tc/timecard", 0.27)}} for i in range(3)]
        post = [{**r, "label": f"post-{i}", "first_start_to_ready_s": 1.7, "repeated_scopes": [],
                 "entries": {**e("sync-lookups/earningtypes", 0.26 + i / 100), **e("sync-tc/timecard", 0.78)}} for i, r in enumerate(pre)]
        self.write("runs/comparison.json", cr.compare(pre, post))
        self.write("screens/pre.json", {"device": "sim", "runs": 3, "screens": {"timecards": {"median_ms": 1660, "spread_ms": 1890, "runs": []}}})
        self.write("screens/post.json", {"device": "sim", "runs": 3, "screens": {"timecards": {"median_ms": 3490, "runs": []}},
                                         "comparison": {"rows": [{"screen": "timecards", "before_ms": 1660, "after_ms": 3490, "change_pct": 110.2,
                                                                  "verdict": "unclear (runs overlap)", "before_runs": [3530, 1660, 1640],
                                                                  "after_runs": [3620, 3490, 1550], "ready": "Week"}]}})
        text = rr.report(self.dir, "demo")
        self.assertIn("| entry: sync-lookups/earningtypes | 2.35 s | 0.27 s | -88.5% | not comparable (sync-lookups synced twice in the before runs) |", text)
        self.assertNotIn("| entry: sync-tc/timecard |", text)  # within noise: left out of report.md
        self.assertIn("| Screen: timecards | 1.66 s | 3.49 s | unclear (runs overlap): runs 3.53, 1.66, 1.64 → 3.62, 3.49, 1.55 |", text)

    def test_query_and_screen_timings(self):
        self.write("before/static.json", {"byCode": {}})
        os.makedirs(os.path.join(self.dir, "screens"), exist_ok=True)
        self.write("before/timings.json", {"meta": {"host": "M2", "rows": 900},
                                           "queries": [{"where": "datasources/a", "ms": 40, "rows": 12}], "actions": [], "indexes": []})
        self.write("screens/pre.json", {"device": "sim", "runs": 3, "screens": {"list": {"median_ms": 2100, "spread_ms": 90}}})
        baseline = rr.report(self.dir)
        self.assertIn("- Queries: 1, 40 ms in total", baseline)
        self.assertIn("| query | `datasources/a` | 40 ms | 12 |", baseline)
        self.assertIn("| list | `-` | 2.10 s | - |", baseline)
        self.write("after/timings.json", {"queries": [{"where": "datasources/a", "ms": 8}], "actions": [], "indexes": [],
                                          "comparison": [{"kind": "queries", "where": "datasources/a", "before_ms": 40, "after_ms": 8, "change_pct": -80, "verdict": "faster"}]})
        self.write("screens/post.json", {"device": "sim", "runs": 3, "screens": {}, "comparison": {"rows": [
            {"screen": "list", "before_ms": 2100, "after_ms": 1500, "change_pct": -28.6, "verdict": "faster"}]}})
        text = rr.report(self.dir)
        self.assertIn("- Queries: 1, 40 ms in total → 8 ms", text)
        self.assertIn("| query | `datasources/a` | 40 ms | 8 ms | -80% |", text)
        self.assertIn("| list | `-` | 2.10 s | 1.50 s | -28.6% | faster |", text)



class EntryTimingTest(unittest.TestCase):
    def test_wait_in_the_queue_is_not_counted_as_the_call(self):
        scope = lambda e: {"sync": {"state": "syncing", "startedAt": 10, "syncedAt": None, "entries": e}}
        samples = [{"t": 1.0, "scopes": scope({"a": "syncing", "b": "not started"})},
                   {"t": 20.0, "scopes": scope({"a": "synced", "b": "not started"})},
                   {"t": 20.5, "scopes": scope({"a": "synced", "b": "synced"})}]
        e = tl.entry_timings(samples, armed=0)
        self.assertEqual(e["sync/a"]["duration_s"], 19.0)
        self.assertEqual(e["sync/b"]["duration_s"], 0.5)  # waited 19 s, ran for at most one interval
        self.assertEqual(e["sync/b"]["start_s"], 20.0)


class RepeatedSyncTest(unittest.TestCase):
    def test_a_second_sync_after_ready_is_counted_as_work_and_warned(self):
        # onLoad syncs 1-3 s, then a screen's onFocus syncs the same scope again 4-7 s.
        st = lambda state, start, end: {"scopes": {"main": {"state": state, "startedAt": start, "syncedAt": end, "entries": {}}},
                                        "queue": 0, "counts": {}}
        samples = [{"t": 1, **st("syncing", 1000, None)}, {"t": 3, **st("synced", 1000, 3000)},
                   {"t": 4, **st("syncing", 4000, 3000)}, {"t": 7, **st("synced", 4000, 7000)}]
        s = tl.summarise("a", "x.db", samples, "ready", 0, 7000, [], samples[-1]["scopes"] and samples[-1], ["main"])
        self.assertEqual(s["scope_starts"], {"main": 2})
        self.assertEqual(s["repeated_scopes"], ["main"])
        self.assertEqual(s["sync_busy_s"], 5.0)            # 2 s + 3 s, not the 6 s span
        self.assertEqual(s["first_start_to_ready_s"], 6.0)  # from the first start, not the second
        before = [dict(s, label=f"pre-{i}") for i in range(3)]
        after = [dict(s, label=f"post-{i}", sync_busy_s=2.0, scope_starts={"main": 1}, repeated_scopes=[]) for i in range(3)]
        report = cr.compare(before, after)
        busy = next(r for r in report["rows"] if r["metric"].startswith("total: sync busy"))
        self.assertEqual((busy["before_s"], busy["after_s"], busy["verdict"]), (5.0, 2.0, "improved"))
        self.assertTrue(any("main synced more than once" in w for w in report["warnings"]))


class InterruptedAndRestartTest(unittest.TestCase):
    def test_oauth_login_pause_is_not_counted(self):
        # identity syncs 1-2 s, waits on the login page 2-12 s (interrupted), restarts and syncs 12-13 s.
        sc = lambda state, start, end, e: {"identity": {"state": state, "startedAt": start, "syncedAt": end, "entries": {"whoami": e}}}
        samples = [{"t": 1.0, "scopes": sc("syncing", 1000, None, "syncing"), "queue": 0, "counts": {}},
                   {"t": 2.0, "scopes": sc("interrupted", 1000, None, "syncing"), "queue": 0, "counts": {}},
                   {"t": 12.0, "scopes": sc("syncing", 12000, None, "syncing"), "queue": 0, "counts": {}},
                   {"t": 13.0, "scopes": sc("synced", 12000, 13000, "synced"), "queue": 0, "counts": {}}]
        self.assertEqual(tl.status(samples[1], ["identity"], 0), "paused")
        s = tl.summarise("pre-1", "x.db", samples, "ready", 0, 13000, [], samples[-1], ["identity"])
        self.assertEqual(s["interrupted_s"], 10.0)
        self.assertEqual(s["first_start_to_ready_raw_s"], 12.0)
        self.assertEqual(s["first_start_to_ready_s"], 2.0)
        self.assertEqual(s["entries"]["identity/whoami"]["duration_s"], 2.0)  # 12 s with the login page
        self.assertEqual(s["scope_starts"], {"identity": 1})                 # the restart is a resume
        self.assertEqual(s["repeated_scopes"], [])
        self.assertEqual(s["sync_busy_s"], 2.0)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "jigx.solution.___demo-test___.0000.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE _syncScope (id TEXT PRIMARY KEY, state TEXT, details TEXT, lastStartedAt INTEGER, lastSyncedAt INTEGER)")
        conn.execute("CREATE TABLE _commandQueue (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO _syncScope VALUES ('main', 'synced', '{}', 1, 2)")
        conn.commit(); conn.close()
        log = sqlite3.connect(os.path.join(self.dir, "jigx.log.db"))
        log.execute("CREATE TABLE _logMessages (id INTEGER PRIMARY KEY, timestamp INTEGER, state TEXT, message TEXT)")
        log.commit(); log.close()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def set_scope(self, state, start, end=None):
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE _syncScope SET state=?, lastStartedAt=?, lastSyncedAt=?", (state, start, end))
        conn.commit(); conn.close()

    def test_sync_before_sign_out_is_dropped_and_the_run_restarts(self):
        def drive():
            time.sleep(0.1)
            now = lambda: int(time.time() * 1000)
            t0 = now(); self.set_scope("syncing", t0)           # the reloaded build syncs before sign-out
            time.sleep(0.15)
            log = sqlite3.connect(os.path.join(self.dir, "jigx.log.db"))
            log.execute("INSERT INTO _logMessages (timestamp, state, message) VALUES (?, 'read', ?)",
                        (now(), json.dumps({"level": "INFO", "category": "use-logout", "message": "Signing out"})))
            log.commit(); log.close()
            time.sleep(0.15)
            t1 = now(); self.set_scope("syncing", t1)            # the sign-in sync
            time.sleep(0.2)
            self.set_scope("synced", t1, now())
        worker = threading.Thread(target=drive); worker.start()
        out = io.StringIO()
        s = tl.track(self.db, "pre-1", os.path.join(self.dir, "runs"), scopes=["main"], interval=0.02, settle=0.2, timeout=10, stream=out)
        worker.join()
        self.assertEqual(s["result"], "ready")
        self.assertEqual(s["restarts"], 1)
        self.assertIn("Sign-out seen", out.getvalue())
        self.assertLess(s["first_start_to_ready_s"], 0.4)       # the sign-in sync only

    def test_armed_during_a_sync_ends_invalid(self):
        self.set_scope("syncing", int(time.time() * 1000) - 5000)
        def drive():
            time.sleep(0.15)
            self.set_scope("synced", int(time.time() * 1000) - 5000, int(time.time() * 1000))
        worker = threading.Thread(target=drive); worker.start()
        out = io.StringIO()
        s = tl.track(self.db, "pre-1", os.path.join(self.dir, "runs"), scopes=["main"], interval=0.02, settle=0.1, timeout=5, stream=out)
        worker.join()
        self.assertEqual(s["result"], "invalid")
        self.assertIn("was already syncing when tracking started", out.getvalue())

    def test_other_solution_syncing_is_named(self):
        other = os.path.join(self.dir, "jigx.solution.___last-used___.1111.db")
        open(other, "w").close()
        self.assertEqual(tl.other_solutions_busy(self.db, 0), ["last-used"])
        self.assertEqual(tl.other_solutions_busy(self.db, time.time() * 1000 + 60000), [])

class CompareVerdictTest(unittest.TestCase):
    def summary(self, label, scope_first, scope_last, entries, interval=0.25):
        return {"label": label, "machine": "m", "result": "ready", "first_start_to_ready_s": 3.0, "interval_s": interval,
                "scopes": {"s": {"duration_s": scope_last, "first_duration_s": scope_first}},
                "entries": entries, "snapshot_timeouts": {}, "log_errors": 0}

    def test_scope_by_first_sync_and_parallel_entries_are_not_regressions(self):
        serial = {"s/a": {"start_s": 0.0, "end_s": 0.26, "duration_s": 0.26}, "s/b": {"start_s": 0.3, "end_s": 1.0, "duration_s": 0.7}}
        parallel = {"s/a": {"start_s": 0.0, "end_s": 1.4, "duration_s": 1.4}, "s/b": {"start_s": 0.0, "end_s": 1.0, "duration_s": 1.0}}
        jitter = {"s/a": {"start_s": 0.0, "end_s": 0.78, "duration_s": 0.78}, "s/b": {"start_s": 0.8, "end_s": 1.5, "duration_s": 0.7}}
        # before: the last start came from a repeat sync (2.0 s); its first sync took 3.0 s
        before = [self.summary(f"pre-{i}", 3.0, 2.0, serial) for i in range(3)]
        after = [self.summary(f"post-{i}", 3.0, 3.0, parallel) for i in range(3)]
        rows = {r["metric"]: r for r in cr.compare(before, after)["rows"]}
        self.assertEqual(rows["scope: s"]["verdict"], "same")
        self.assertEqual(rows["entry: s/a"]["verdict"], "parallel now")
        after = [self.summary(f"post-{i}", 3.0, 3.0, jitter) for i in range(3)]
        rows = {r["metric"]: r for r in cr.compare(before, after)["rows"]}
        self.assertEqual(rows["entry: s/a"]["verdict"], "within noise")  # 0.26 -> 0.78 s: +200%, but two sample ticks

    def test_first_sync_includes_the_resume(self):
        self.assertEqual(tl.first_sync_s([[1000, 2000, False], [12000, 13000, True], [20000, 21000, False]],
                                         [[2000, 12000]]), 2.0)
        self.assertIsNone(tl.first_sync_s([[1000, None, False]]))

class PeterRunsTest(unittest.TestCase):
    """The verdicts two review runs reported as wrong (Time-Tracking-V2, Project Central)."""

    def summary(self, label, entries, repeated=(), total=10.0):
        return {"label": label, "machine": "m", "result": "ready", "first_start_to_ready_s": total, "interval_s": 0.25,
                "scopes": {}, "entries": entries, "repeated_scopes": list(repeated), "snapshot_timeouts": {}, "log_errors": 0}

    def test_a_call_that_varies_is_unclear_not_a_regression(self):
        # rest-get-whoami: 3.3-4.4 s before, 1.8-5.6 s after; nothing changed it
        e = lambda d: {"sync-identity/whoami": {"start_s": 0.0, "end_s": d, "duration_s": d}}
        before = [self.summary(f"pre-{i}", e(d)) for i, d in enumerate((3.3, 3.6, 4.4))]
        after = [self.summary(f"post-{i}", e(d)) for i, d in enumerate((1.8, 5.64, 5.6))]
        row = {r["metric"]: r for r in cr.compare(before, after)["rows"]}["entry: sync-identity/whoami"]
        self.assertEqual(row["verdict"], "unclear (runs overlap)")
        self.assertEqual(row["after_runs"], [1.8, 5.64, 5.6])
        self.assertIn("3.3, 3.6, 4.4 → 1.8, 5.64, 5.6", cr.markdown(cr.compare(before, after)))

    def test_an_entry_of_a_scope_that_synced_twice_is_not_comparable(self):
        # rest-get-jxttearningtypes 2.34 -> 0.26 s: slow before only because the second sync restarted it
        e = lambda d: {"sync-lookups/earningtypes": {"start_s": 0.0, "end_s": d, "duration_s": d}}
        before = [self.summary(f"pre-{i}", e(2.34 + i / 100), repeated=["sync-lookups"]) for i in range(3)]
        after = [self.summary(f"post-{i}", e(0.26 + i / 100)) for i in range(3)]
        row = {r["metric"]: r for r in cr.compare(before, after)["rows"]}["entry: sync-lookups/earningtypes"]
        self.assertEqual(row["verdict"], "not comparable (sync-lookups synced twice in the before runs)")

    def test_a_scope_slower_beside_more_calls_is_parallel_now(self):
        # sync-lookups +15%: its calls now share the network with the project calls that used to wait
        serial = {"sync-lookups/a": {"start_s": 0.0, "end_s": 2.0, "duration_s": 2.0},
                  "sync-data/p": {"start_s": 2.1, "end_s": 4.0, "duration_s": 1.9}}
        parallel = {"sync-lookups/a": {"start_s": 0.0, "end_s": 2.6, "duration_s": 2.6},
                    "sync-data/p": {"start_s": 0.0, "end_s": 2.0, "duration_s": 2.0}}
        mk = lambda label, entries, d: {**self.summary(label, entries), "scopes": {"sync-lookups": {"first_duration_s": d}}}
        before = [mk(f"pre-{i}", serial, 4.0 + i / 10) for i in range(3)]
        after = [mk(f"post-{i}", parallel, 4.6 + i / 10) for i in range(3)]
        rows = {r["metric"]: r for r in cr.compare(before, after)["rows"]}
        self.assertEqual(rows["scope: sync-lookups"]["verdict"], "parallel now")

    def test_runs_apart_still_count(self):
        e = lambda d: {"s/a": {"start_s": 0.0, "end_s": d, "duration_s": d}}
        before = [self.summary(f"pre-{i}", e(d)) for i, d in enumerate((3.0, 3.1, 3.2))]
        after = [self.summary(f"post-{i}", e(d)) for i, d in enumerate((1.0, 1.1, 1.2))]
        self.assertEqual({r["metric"]: r for r in cr.compare(before, after)["rows"]}["entry: s/a"]["verdict"], "improved")


class LabelTest(unittest.TestCase):
    def test_re_runs_with_any_suffix_count_but_not_android(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runs"))
        for label in ("pre-1", "pre-clean-2", "pre-android-1", "post-1"):
            with open(os.path.join(d, "runs", f"{label}-summary.json"), "w") as f:
                json.dump({"label": label}, f)
        self.assertEqual([r["label"] for r in rr.load_runs(d, "pre")], ["pre-1", "pre-clean-2"])
        self.assertEqual([r["label"] for r in cr.load(os.path.join(d, "runs"), "pre")], ["pre-1", "pre-clean-2"])
        self.assertEqual([r["label"] for r in cr.load(os.path.join(d, "runs"), "pre-android")], ["pre-android-1"])

class SerialTest(unittest.TestCase):
    def test_strict_series_is_named_and_overlap_is_not(self):
        e = lambda a, b: {"start_s": a, "end_s": b}
        series = {"s/a": e(0, 1), "s/b": e(1.1, 2), "s/c": e(2, 3), "s/d": e(3.2, 4)}
        self.assertEqual(tl.serial_chain(series), ["s/a", "s/b", "s/c", "s/d"])
        waves = {"s/a": e(0, 1), "s/b": e(0, 2), "s/c": e(0.1, 3), "s/d": e(0.2, 4)}
        self.assertEqual(tl.serial_chain(waves), [])

class AndroidReadTest(unittest.TestCase):
    def test_android_hash_warning_counts(self):
        logs = [{"level": "WARNING", "category": "get-entity-id", "message": "Missing entity id, falling back to hash"},
                {"level": "WARNING", "category": "x", "message": "Performance Issue: Missing ID or value of entity (3 times) -> using hash function"}]
        self.assertEqual(tl.summarise("a", "x.db", [], "ready", 0, None, logs, None, [])["hash_fallbacks"], 4)

    def test_ios_and_android_runs_are_not_mixed(self):
        folder = tempfile.mkdtemp()
        for label in ("pre-1", "pre-2", "pre-android-1", "prefix-1", "pre"):
            with open(os.path.join(folder, f"{label}-summary.json"), "w") as f:
                json.dump({"label": label}, f)
        self.assertEqual([r["label"] for r in cr.load(folder, "pre")], ["pre-1", "pre-2", "pre"])
        self.assertEqual([r["label"] for r in cr.load(folder, "pre-android")], ["pre-android-1"])

    def test_device_sqlite_output_is_parsed_into_rows(self):
        class Done:
            returncode, stderr = 0, ""
            stdout = "s1\x1fsynced\x1f{\"a,b\": {\"state\": \"synced\"}}\n\x1f1730790000000\x1f<<NULL>>\x1e\n"
        calls = []
        orig = tl.subprocess.run
        tl.subprocess.run = lambda cmd, **kw: (calls.append((cmd, kw["input"])), Done)[1]
        try:
            rows = tl.AdbDb("emulator-5556", "/data/x.db").execute("SELECT * FROM t WHERE id > ? AND ts >= ?", (5, 7.9))
        finally:
            tl.subprocess.run = orig
        self.assertEqual(rows, [("s1", "synced", '{"a,b": {"state": "synced"}}\n', 1730790000000, None)])
        self.assertEqual(rows.fetchone()[0], "s1")
        self.assertIn("-readonly", calls[0][0])
        self.assertIn("WHERE id > 5 AND ts >= 7;", calls[0][1])


if __name__ == "__main__":
    unittest.main()


class ProjectCentralRerunTest(unittest.TestCase):
    """Peter's Project Central rerun (11 Nov 2026): a first-install baseline run, another solution's
    log lines, and a run that waited 20 minutes for a sign-in that never came."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.folder = os.path.join(self.dir, "Containers", "Data", "Application", "APP-1", "Library", "LocalDatabase")
        os.makedirs(self.folder)
        self.sol = "8a00cc3c-6c1c-40ca-8439-81c9ead89754"
        self.db = os.path.join(self.folder, f"jigx.solution.___project-central-dev___.{self.sol}.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE _syncScope (id TEXT PRIMARY KEY, state TEXT, details TEXT, lastStartedAt INTEGER, lastSyncedAt INTEGER)")
        conn.execute("CREATE TABLE _commandQueue (id INTEGER PRIMARY KEY)")
        conn.commit(); conn.close()
        self.log = os.path.join(self.folder, "jigx.log.db")
        log = sqlite3.connect(self.log)
        log.execute("CREATE TABLE _logMessages (id INTEGER PRIMARY KEY, timestamp INTEGER, state TEXT, message TEXT)")
        log.commit(); log.close()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def add_log(self, message, solution=None, at=None):
        m = {"level": "WARNING", "category": "datastore", "message": message, **({"solutionId": solution} if solution else {})}
        with contextlib_closing(sqlite3.connect(self.log)) as log:
            log.execute("INSERT INTO _logMessages (timestamp, state, message) VALUES (?, 'read', ?)",
                        (at or int(time.time() * 1000), json.dumps(m)))
            log.commit()

    def test_log_lines_of_another_solution_are_left_out(self):
        self.assertEqual(tl.solution_id(self.db), self.sol)
        self.add_log("#25 'settings' Timeout for first snapshot", "a99d1f92-7c6c-4252-9fcb-7243b8ad7fea")
        self.add_log("#3 'photos' Timeout for first snapshot", self.sol)
        self.add_log("app-level line, no solution")
        rows, _ = tl.read_logs(self.log, 0, 0, tl.solution_id(self.db))
        self.assertEqual([r["message"][:10] for r in rows], ["#3 'photos", "app-level "])
        self.assertEqual(len(tl.read_logs(self.log, 0, 0)[0]), 3)

    def test_a_silent_wait_reminds_and_says_why_it_timed_out(self):
        out = io.StringIO()
        s = tl.track(self.db, "post-2", os.path.join(self.dir, "runs"), interval=0.02, settle=0.1, timeout=0.5,
                     stream=out, remind_after=0.15)
        self.assertEqual(s["result"], "timeout")
        self.assertEqual(s["timeout_reason"], "no sign-out seen and nothing started")
        self.assertIn("Nothing has started and no sign-out seen", out.getvalue())
        self.assertIn("post-2: timeout (no sign-out seen and nothing started)", out.getvalue())

    def test_first_sign_in_after_install(self):
        armed = int(time.time() * 1000) + 1000
        self.assertTrue(tl.first_sign_in_after_install(self.db, armed))  # the container was just created
        self.add_log("Signing out", at=armed - 500)
        self.assertFalse(tl.first_sign_in_after_install(self.db, armed))  # a run after a sign-out
        self.assertFalse(tl.first_sign_in_after_install(self.db, armed + 7200 * 1000))  # installed long before
        self.assertIsNone(tl.first_sign_in_after_install("adb:emulator-5554:/data/x.db", armed))

    def test_an_unusual_baseline_run_is_named_and_can_be_left_out(self):
        pre = [dict(run("pre-1", 35.3, 5), first_sign_in_after_install=True, other_solutions_synced=["acumatica-lifeway-dev-peter"]),
               run("pre-2", 12.1, 5), run("pre-3", 14.5, 5)]
        post = [run("post-1", 6.9, 5), run("post-2", 7.6, 5), run("post-3", 10.5, 5)]
        text = " ".join(cr.compare(pre, post)["warnings"])
        self.assertIn("before run pre-1 is not like the others (the first sign-in after installing the app; "
                      "acumatica-lifeway-dev-peter synced during it; 35.3s against 12.1, 14.5 for the others)", text)
        self.assertIn("--exclude pre-1", text)
        self.assertNotIn("post-", text)
        folder = os.path.join(self.dir, "runs")
        os.makedirs(folder, exist_ok=True)
        for r in pre + post:
            with open(os.path.join(folder, f"{r['label']}-summary.json"), "w") as f:
                json.dump(r, f)
        import contextlib as _c
        with _c.redirect_stdout(io.StringIO()):
            cr.main([folder, "--exclude", "pre-1"])
        with open(os.path.join(folder, "comparison.json")) as f:
            saved = json.load(f)
        self.assertEqual(saved["before_runs"], ["pre-2", "pre-3"])
        self.assertEqual(saved["rows"][0]["before_s"], 13.3)
        self.assertIn("left out: pre-1", saved["warnings"])


from contextlib import closing as contextlib_closing  # noqa: E402
