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
             "tools": {"node": "v20.1.0", "node_ok": False, "maestro": True, "java": True}}
        self.assertEqual(r["solution"], {"type": "YAML", "name": "demo"})
        self.assertEqual((r["git"]["branch"], r["git"]["uncommitted"]), ("feature/x", 1))
        out = pf.text(r)
        self.assertIn("Branch: feature/x at", out)
        self.assertIn("1 uncommitted change(s)", out)
        self.assertIn("Devices: none booted or connected", out)
        self.assertIn("Node 22.5+", out)


if __name__ == "__main__":
    unittest.main()
