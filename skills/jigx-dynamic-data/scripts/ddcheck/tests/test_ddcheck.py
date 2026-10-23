"""Tests for ddcheck. No network, no login: everything runs on the demo fixture.

  python3 -m unittest discover -s skills/jigx-dynamic-data/scripts/ddcheck/tests
"""
import base64
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import api        # noqa: E402
import auth       # noqa: E402
import compare    # noqa: E402
import ddprofile  # noqa: E402
import permit     # noqa: E402
import source     # noqa: E402

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ddcheck_cli", HERE.parent / "__main__.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

FIX = HERE / "fixtures"
SRC = FIX / "demo-solution"
PROFILE = FIX / "ddcheck.json"


def run_cli(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


class FakeTTY:
    """A terminal: what ddcheck prints and what the person types are separate streams."""

    def __init__(self, typed):
        self.typed, self.shown = io.StringIO(typed), io.StringIO()

    def write(self, text):
        self.shown.write(text)

    def flush(self):
        pass

    def readline(self):
        return self.typed.readline()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_jwt(**claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class SourceTests(unittest.TestCase):
    def test_tables_the_app_writes_are_data(self):
        data = source.DataTables(SRC, ddprofile.load(PROFILE))
        self.assertIn("visit-form.jigx", data.why("visitAnswers"))
        self.assertIsNone(data.why("homeMenu"))

    def test_profile_config_tables_override_derivation(self):
        prof = ddprofile.load(PROFILE)
        prof["configTables"] = ["visitAnswers"]
        self.assertIsNone(source.DataTables(SRC, prof).why("visitAnswers"))

    def test_field_types_come_from_datasource_sql(self):
        typed = source.typed_fields(SRC)
        self.assertEqual(typed["homeMenu"]["active"][0], "boolean")
        self.assertEqual(typed["homeMenu"]["column"][0], "number")
        self.assertNotIn("active", typed.get("config", {}))

    def test_config_flags_come_from_datasources(self):
        self.assertEqual(source.config_flags(SRC, "config"), {"featureA", "featureB"})


class CheckTests(unittest.TestCase):
    def check(self, which):
        return run_cli("--profile", str(PROFILE), "--out", str(FIX / "dumps" / which),
                       "check", "--offline", "--solution", "demo", "--source", str(SRC))

    def test_clean_dump_passes(self):
        code, out = self.check("clean")
        self.assertEqual(code, 0, out)
        self.assertIn("latent linkTo", out)          # inactive row: reported, not fatal

    def test_broken_dump_fails_with_each_rule(self):
        code, out = self.check("broken")
        self.assertEqual(code, 1, out)
        for expected in ("dangling linkTo | homeMenu | m1",
                         "bad type | homeMenu | m2",
                         "dead parameter | homeMenu | m2",
                         "'featureB' is read by the app",
                         "north: 1 rows, expected 2",
                         "south: 1 rows, value not in the expected set"):
            self.assertIn(expected, out)

    def test_default_table_list_skips_data_tables(self):
        prof = ddprofile.load(PROFILE)
        tables = cli.resolve_tables(None, SRC, prof, source.DataTables(SRC, prof))
        self.assertEqual(tables, ["config", "homeMenu", "zones"])

    def test_naming_a_data_table_is_refused(self):
        prof = ddprofile.load(PROFILE)
        with self.assertRaises(SystemExit) as e:
            cli.resolve_tables(["visitAnswers"], SRC, prof, source.DataTables(SRC, prof))
        self.assertIn("refusing data tables", str(e.exception))


class DiffTests(unittest.TestCase):
    def test_suspect_and_hold_rows_never_enter_the_plan(self):
        prof = ddprofile.load(PROFILE)
        row = lambda rid, **c: {"rid": rid, "content": c}
        src = {"zones": [row("z1", region="north", name="Acme Depot"),
                         row("z2", region="north", name="https://erp.example.com"),
                         row("z3", region="north", name="Plain")],
               "config": [row("c1", featureA=True)]}
        dst = {"zones": [], "config": []}
        _, plan, held, _ = compare.build_diff(["zones", "config"], src, dst, "a", "b", prof)
        self.assertEqual(sorted(plan["zones"]), ["z3"])
        self.assertNotIn("config", plan)
        why = " ".join(h[2] for h in held)
        self.assertIn("tenant name", why)
        self.assertIn("URL", why)
        self.assertIn("holdTables", why)


class WriteGateTests(unittest.TestCase):
    def test_no_terminal_means_no_write(self):
        with mock.patch.object(permit, "_open_tty", return_value=None):
            with self.assertRaises(SystemExit) as e:
                permit.ask("demo", "sid", "org", "me", [], 3)
        self.assertIn("REFUSED", str(e.exception))

    def test_protected_solution_is_refused_even_with_a_terminal(self):
        tty = mock.MagicMock()
        with mock.patch.object(permit, "_open_tty", return_value=tty):
            with self.assertRaises(SystemExit) as e:
                permit.ask("demo-prod", "sid", "org", "me", [], 3, protect=["*-prod"])
        self.assertIn("protect", str(e.exception))
        tty.readline.assert_not_called()

    def test_wrong_answer_aborts(self):
        with mock.patch.object(permit, "_open_tty", return_value=FakeTTY("yes\n")):
            with self.assertRaises(SystemExit) as e:
                permit.ask("demo", "sid", "org", "me", [], 3)
        self.assertIn("nothing written", str(e.exception))

    def test_typing_the_solution_name_grants_a_permit_for_that_solution(self):
        tty = FakeTTY("demo\n")
        with mock.patch.object(permit, "_open_tty", return_value=tty):
            grant = permit.ask("demo", "sid-1", "org", "me", ["plan line"], 3)
        self.assertEqual((grant.solution, grant.solution_id), ("demo", "sid-1"))
        shown = tty.shown.getvalue()
        for expected in ("ddcheck WRITE", "target:   demo (sid-1)", "plan line", "Type 'demo'"):
            self.assertIn(expected, shown)

    def test_ci_never_gets_a_terminal(self):
        with mock.patch.dict(os.environ, {"CI": "true"}):
            self.assertIsNone(permit._open_tty())

    def test_a_permit_cannot_be_forged(self):
        with self.assertRaises(RuntimeError):
            permit.Permit(object(), "demo", "sid", 1)

    def test_patch_without_a_matching_permit_sends_nothing(self):
        client = api.Client.__new__(api.Client)
        client.call = mock.Mock(side_effect=AssertionError("network call made"))
        with self.assertRaises(RuntimeError):
            client.patch_rows("zones", "sid-1", {"z1": {}}, permit=None)
        with mock.patch.object(permit, "_open_tty", return_value=FakeTTY("other\n")):
            grant = permit.ask("other", "sid-2", "org", "me", [], 1)
        with self.assertRaises(RuntimeError):
            client.patch_rows("zones", "sid-1", {"z1": {}}, permit=grant)
        client.call.assert_not_called()

    def test_seed_to_a_protected_solution_stops_before_login(self):
        with mock.patch.object(auth, "token", side_effect=AssertionError("logged in")):
            with self.assertRaises(SystemExit) as e:
                run_cli("--profile", str(PROFILE), "seed", "--from", "demo", "--to", "demo-prod")
        self.assertIn("protect", str(e.exception))


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self.tmp.name)
        self.state = tmp / "state.json"
        pkg = tmp / "mcp"
        (pkg / "dist/lib").mkdir(parents=True)
        (pkg / "dist/lib/constants.js").write_text(
            "export const PROD_X = {\n region: 'eu-central-1',\n userPoolId: 'p',\n clientId: 'abc',\n};\n"
            "export const PROD_REGIONS = {\n 'eu-central-1': {\n  regionId: 'eu-central-1',\n"
            "  apiBaseUrl: 'https://eu-central-1.api.jigx.com/v2.0',\n },\n};\n")
        self.env = mock.patch.dict(os.environ, {"JIGX_DEBUG_MCP_STATE_PATH": str(self.state),
                                                "JIGX_DEBUG_MCP_DIR": str(pkg)})
        self.env.start()
        for k in ("JIGX_AUTH_TOKEN", "JIGX_API_BASE"):
            os.environ.pop(k, None)
        self.cache = mock.patch.object(auth, "CACHE_FILE", tmp / "cache.json")
        self.cache.start()

    def tearDown(self):
        self.cache.stop()
        self.env.stop()
        self.tmp.cleanup()

    def write_state(self, exp):
        self.state.write_text(json.dumps({"auth": {
            "username": "dev@example.com", "jwtToken": fake_jwt(exp=exp), "refreshToken": "r",
            "organizations": [{"organizationId": "org-eu", "regionId": "eu-central-1"}]}}))

    def test_uses_a_fresh_mcp_login(self):
        self.write_state(time.time() + 3600)
        tok, label = auth.token()
        self.assertEqual(label, "jigx_debug MCP login")
        self.assertEqual(auth.identity(), "dev@example.com")

    def test_env_token_wins(self):
        self.write_state(time.time() + 3600)
        with mock.patch.dict(os.environ, {"JIGX_AUTH_TOKEN": "raw"}):
            self.assertEqual(auth.token(), ("raw", "JIGX_AUTH_TOKEN"))

    def test_expired_login_is_refreshed_with_the_installed_client_id(self):
        self.write_state(time.time() - 60)
        with mock.patch.object(auth, "_refresh", return_value=(fake_jwt(exp=time.time() + 3600), None)) as r:
            tok, label = auth.token()
        r.assert_called_once_with("r")
        self.assertEqual(label, "jigx_debug MCP login (refreshed)")
        self.assertEqual(auth.mcp_constants()["clientId"], "abc")

    def test_no_login_exits_with_instructions(self):
        self.state.write_text("{}")
        with self.assertRaises(SystemExit) as e:
            auth.token()
        self.assertIn("login_debug_user", str(e.exception))

    def test_api_base_follows_the_org_region(self):
        self.write_state(time.time() + 3600)
        self.assertEqual(auth.api_base("org-eu"), "https://eu-central-1.api.jigx.com/v2.0")
        self.assertEqual(auth.api_base("unknown"), auth.DEFAULT_API)


class ProfileTests(unittest.TestCase):
    def test_unknown_keys_are_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"linkTabels": []}, f)
        try:
            with self.assertRaises(SystemExit) as e:
                ddprofile.load(f.name)
            self.assertIn("linkTabels", str(e.exception))
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
