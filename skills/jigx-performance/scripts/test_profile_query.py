"""Tests for profile-query.py. Run: python3 -m unittest skills/jigx-performance/scripts/test_profile_query.py"""
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("profile_query", os.path.join(HERE, "profile-query.py"))
pq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pq)

QUERY = """-- pending uploads
SELECT id FROM [fileUploadQueue] AS q
WHERE LOWER(COALESCE(json_extract(q.data, '$.status'), '')) = 'pending' AND (@selectedId IS NULL OR id != @selectedId)"""
INDEX = "CREATE INDEX idx_fileUploadQueue_status ON [fileUploadQueue] (LOWER(COALESCE(json_extract(data, '$.status'), '')));"


class ProfileQueryTest(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE fileUploadQueue (id TEXT PRIMARY KEY, data TEXT)")
        rows = [(str(i), json.dumps({"status": "PENDING" if i % 4 == 0 else "synced", "thumb": "x" * 200}))
                for i in range(400)]
        conn.executemany("INSERT INTO fileUploadQueue VALUES (?, ?)", rows)
        conn.commit()
        conn.close()
        with open(self.db, "rb") as f:
            self.original = f.read()

    def tearDown(self):
        os.remove(self.db)

    def test_counts_plan_scale_and_index_without_touching_input(self):
        report = pq.profile(self.db, QUERY, runs=2, scale=3, ddl=INDEX)
        before, after = report["measurements"]
        self.assertEqual(before["tables"][0]["rows"], 1200)
        self.assertEqual(before["tables"][0]["largest_column"], "data")
        self.assertEqual(before["timing"]["result_rows"], 300)
        self.assertTrue(before["full_scans"])
        self.assertFalse(after["full_scans"])
        self.assertTrue(any("idx_fileUploadQueue_status" in s for s in after["plan"]))
        self.assertFalse(report["ddl_changed_results"])
        self.assertIsNone(report["params"]["selectedId"])
        with open(self.db, "rb") as f:
            self.assertEqual(f.read(), self.original)

    def test_equivalence_verdicts(self):
        same = "SELECT id FROM fileUploadQueue WHERE json_extract(data,'$.status') = 'PENDING'"
        self.assertEqual(pq.profile(self.db, QUERY, runs=1, compare=same)["equivalence"]["verdict"], "EQUIVALENT")
        empty = "SELECT id FROM fileUploadQueue WHERE 0"
        self.assertTrue(pq.profile(self.db, QUERY, runs=1, compare=empty)["equivalence"]["verdict"].startswith("INCONCLUSIVE"))
        different = "SELECT id FROM fileUploadQueue"
        self.assertEqual(pq.profile(self.db, QUERY, runs=1, compare=different)["equivalence"]["verdict"], "DIFFERENT")

    def test_param_parsing(self):
        self.assertEqual(pq.parse_value("a=1"), ("a", "1"))
        self.assertEqual(pq.parse_value("a:=1"), ("a", 1))
        self.assertEqual(pq.parse_value("a="), ("a", None))
        self.assertEqual(pq.param_names("SELECT '@nope' WHERE x=@yes AND y=$also"), ["also", "yes"])


class ScaleTest(unittest.TestCase):
    def test_scaling_keeps_each_copy_joined_to_its_own_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE h (id TEXT PRIMARY KEY, data TEXT)")
        conn.execute("CREATE TABLE l (id TEXT PRIMARY KEY, data TEXT)")
        conn.executemany("INSERT INTO h VALUES (?, ?)", [(f"h{i}", json.dumps({"Nbr": f"A{i}  ", "Status": "Open"})) for i in range(32)])
        conn.executemany("INSERT INTO l VALUES (?, ?)", [(f"l{i}", json.dumps({"Nbr": f"A{i % 32}"})) for i in range(320)])
        sql = ("SELECT count(*) FROM h JOIN l ON trim(json_extract(h.data, '$.Nbr')) = json_extract(l.data, '$.Nbr') "
               "WHERE json_extract(h.data, '$.Status') = 'Open'")
        pq.scale_tables(conn, ["h", "l"], 10, pq.sql_literals(sql))
        self.assertEqual(conn.execute(sql).fetchone()[0], 3200)  # 10x, not 100x


if __name__ == "__main__":
    unittest.main()
