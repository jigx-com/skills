"""Tests for copy-to-test.py. Run: python3 -m unittest discover -s skills/jigx-performance/scripts -p 'test_*.py'"""
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("copy_to_test", os.path.join(HERE, "copy-to-test.py"))
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


class CopyToTestTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, ".git"))
        os.makedirs(os.path.join(self.root, ".vscode"))
        with open(os.path.join(self.root, ".vscode", "settings.json"), "w") as f:
            f.write('{"jigx.workspace.organizationId": "org"}')
        self.src = os.path.join(self.root, "Solution")
        os.makedirs(os.path.join(self.src, "jigs"))
        with open(os.path.join(self.src, "index.jigx"), "w") as f:
            f.write("name: field-services\ntitle: Field Services\ncategory: business\nhome:\n  name: x\n")
        with open(os.path.join(self.src, "jigs", "a.jigx"), "w") as f:
            f.write("title: A\n")

    def test_copy_gets_the_test_name_and_the_organisation(self):
        out = os.path.join(self.root, "copy")
        ct.copy_to_test(self.src, out, "field-services-test")
        with open(os.path.join(out, "index.jigx")) as f:
            text = f.read()
        self.assertIn("name: field-services-test\ntitle: field-services-test\n", text)
        self.assertIn("home:\n  name: x\n", text)  # nested name: untouched
        self.assertTrue(os.path.exists(os.path.join(out, "jigs", "a.jigx")))
        self.assertTrue(os.path.exists(os.path.join(out, ".vscode", "settings.json")))
        with open(os.path.join(self.src, "index.jigx")) as f:
            self.assertIn("name: field-services\n", f.read())  # original untouched

    def test_refuses_the_original_name(self):
        with self.assertRaises(SystemExit):
            ct.copy_to_test(self.src, os.path.join(self.root, "copy"), "Field-Services")


if __name__ == "__main__":
    unittest.main()
