#!/usr/bin/env python3
"""ddcheck: check, diff and seed a Jigx solution's Dynamic Data.

  ddcheck whoami                                   login, org and API in use (never the token)
  ddcheck dump  --solution <name>                  write each config table to <out>/<name>/
  ddcheck check --solution <name> [--offline]      validate rows against the solution source
  ddcheck diff  --from <a> --to <b> [--rows]       compare two solutions; writes diff.md + import/
  ddcheck seed  --from <a> --to <b>                diff, then PATCH the importable rows into <b>

Everything except `seed` only reads. `seed` prints the plan to the terminal and writes only when
a person types the target solution's name there; an agent's shell has no terminal and is always
refused. There is no flag that skips this.

Login: JIGX_AUTH_TOKEN, else the jigx_debug MCP's login (see auth.py). No password is read.
Org: --org, else JIGX_ORG_ID, else the profile's "org". Stdlib only.
"""
import argparse
import json
import os
import pathlib
import sys

import api
import auth
import checks
import compare
import ddprofile
import permit
import source


def _org(a, prof):
    return a.org or os.environ.get("JIGX_ORG_ID") or prof.get("org")


def _out(a, prof):
    if a.out:
        return pathlib.Path(a.out)
    return ddprofile.resolve(prof, prof.get("out") or ".ddcheck")


def _client(a, prof):
    token, _ = auth.token()
    return api.Client(_org(a, prof), token)


def _listed(names):
    out = []
    for n in names:
        p = pathlib.Path(n)
        if p.is_file():
            out += [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]
        else:
            out.append(n)
    return out


def resolve_tables(names, src, prof, data):
    """Explicit names (or files of names); else the profile's configTables; else the tables
    databases/default.jigx declares minus data tables. Data tables are refused, not skipped."""
    if names:
        listed = _listed(names)
        refused = [(t, data.why(t)) for t in listed if data.why(t)]
        if refused:
            sys.exit("refusing data tables (written by the app or reps; never compared or copied):\n"
                     + "\n".join(f"  {t}: {why}" for t, why in refused)
                     + "\nIf one is really config, add it to configTables in the profile.")
    elif prof.get("configTables"):
        listed = list(prof["configTables"])
    else:
        declared = source.declared_tables(src)
        if declared is None:
            sys.exit(f"no databases/default.jigx under {src}; pass --tables")
        listed = [t for t in declared if not data.why(t)]
    return list(dict.fromkeys(listed))


def _fetch_all(client, tables, solution, out, write=True):
    sid = client.solution_id(solution)
    d = out / solution
    shape, dump, inventory = {}, {}, {}
    for t in tables:
        rows = client.fetch_rows(t, sid, shape=shape)
        dump[t] = rows
        inventory[t] = "ABSENT" if rows is None else len(rows)
    if write:
        d.mkdir(parents=True, exist_ok=True)
        for t, rows in dump.items():
            (d / f"{t}.json").write_text(json.dumps(rows, indent=1))
        (d / "inventory.json").write_text(json.dumps(
            {"solution": solution, "solutionId": sid, "_api_shape": shape, "tables": inventory}, indent=1))
    return dump, inventory, shape


def _load_dump(tables, solution, out):
    d = out / solution
    if not d.exists():
        return None
    return {t: (json.loads((d / f"{t}.json").read_text()) if (d / f"{t}.json").exists() else None)
            for t in tables}


def cmd_whoami(a, prof):
    _, src_label = auth.token()
    org = _org(a, prof)
    print(f"login:   {auth.identity() or '(not from the MCP; identity unknown)'} via {src_label}")
    print(f"org:     {org or '(none: pass --org, JIGX_ORG_ID or profile org)'}")
    print(f"api:     {auth.api_base(org) if org else '-'}")
    print(f"profile: {prof.get('_file') or '(none; generic checks only)'}")
    return 0


def cmd_dump(a, prof):
    src = source.resolve_source(a.source, a.solution, prof)
    tables = resolve_tables(a.tables, src, prof, source.DataTables(src, prof))
    out = _out(a, prof)
    _, inventory, shape = _fetch_all(_client(a, prof), tables, a.solution, out)
    absent = [t for t, n in inventory.items() if n == "ABSENT"]
    print(f"{a.solution}: {len(tables)} tables -> {out / a.solution}")
    print(f"rows: {sum(n for n in inventory.values() if n != 'ABSENT')}; absent: {', '.join(absent) or 'none'}")
    print("api shape (first page observed): " + json.dumps(shape))
    return 0


def cmd_check(a, prof):
    src = source.resolve_source(a.source, a.solution, prof)
    if not src.exists():
        sys.exit(f"no solution source at {src}")
    data = source.DataTables(src, prof)
    tables = resolve_tables(a.tables, src, prof, data)
    if not a.tables:
        # A declaration file must never hide a rule or required table from check.
        tables = list(dict.fromkeys(tables + checks.required_tables(prof)))
    out = _out(a, prof)
    dump = _load_dump(tables, a.solution, out) if a.offline else None
    if dump is None:
        if a.offline:
            sys.exit(f"no dump at {out / a.solution}; run dump first")
        dump, _, _ = _fetch_all(_client(a, prof), tables, a.solution, out)
    findings = checks.run_checks(dump, src, prof)
    print(f"# check {a.solution} against {src}\n")
    print(checks.render(findings))
    by = {}
    for sev, *_ in findings:
        by[sev] = by.get(sev, 0) + 1
    print("findings %d (%s)" % (len(findings), ", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "none"))
    return 1 if checks.failed(findings, prof) else 0


def cmd_diff(a, prof, client=None):
    if a.src == a.dst:
        sys.exit("--from and --to are the same solution")
    client = client or _client(a, prof)
    src_path = source.resolve_source(a.source, a.src, prof)
    tables = resolve_tables(a.tables, src_path, prof, source.DataTables(src_path, prof))
    out = _out(a, prof)
    src_dump, _, _ = _fetch_all(client, tables, a.src, out)
    dst_dump, _, _ = _fetch_all(client, tables, a.dst, out)
    report, plan, _, missing_map = compare.build_diff(
        tables, src_dump, dst_dump, a.src, a.dst, prof,
        include_suspect=getattr(a, "include_suspect", False))
    if getattr(a, "only_missing", False):
        plan = {t: {r: c for r, c in rows.items() if r in missing_map.get(t, ())} for t, rows in plan.items()}
        plan = {t: rows for t, rows in plan.items() if rows}
    (out / "import").mkdir(parents=True, exist_ok=True)
    for t, rows in plan.items():
        (out / "import" / f"{t}.json").write_text(json.dumps(rows, indent=1))
    if getattr(a, "rows", False):
        report += compare.row_detail(tables, src_dump, dst_dump, prof)
    (out / "diff.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    total = sum(len(v) for v in plan.values())
    print(f"\nimportable: {len(plan)} tables, {total} rows -> {out}/import/")
    return plan, total


def cmd_seed(a, prof):
    # Refuse a protected target before reading anything.
    hit = permit.protected(a.dst, prof.get("protect"))
    if hit:
        sys.exit(f"REFUSED: '{a.dst}' matches the profile's protect pattern '{hit}'.")
    client = _client(a, prof)
    plan, total = cmd_diff(a, prof, client)
    if not plan:
        print("nothing to seed.")
        return 0
    dst_sid = client.solution_id(a.dst)
    lines = [f"from:     {a.src}", f"rows:     {total} (PATCH = merge into existing rids)"]
    lines += [f"  {t}: {len(rows)} rows" for t, rows in sorted(plan.items())]
    lines.append(f"files:    {_out(a, prof)}/import/ and diff.md (read them before you confirm)")
    grant = permit.ask(a.dst, dst_sid, client.org, auth.identity(), lines, total, prof.get("protect"))
    for t, rows in sorted(plan.items()):
        written, errors = client.patch_rows(t, dst_sid, rows, grant)
        print(f"{t}: {written}/{len(rows)} written")
        for e in errors:
            print("  " + e)
    print("\n--- re-diff after seeding ---")
    a.tables = sorted(plan)
    _, total2 = cmd_diff(a, prof, client)
    ok = total2 == 0
    print(f"\nre-diff missing=0 changed=0: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ddcheck", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", help="profile JSON (default: ddcheck.json or docs/test-knowledge/ddcheck.json)")
    ap.add_argument("--org", help="Jigx organisation id (default: JIGX_ORG_ID, then the profile)")
    ap.add_argument("--out", help="dump/report directory (default: the profile's out, else .ddcheck)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="show the login, org and API in use")

    d = sub.add_parser("dump", help="write every listed table to <out>/<solution>/")
    d.add_argument("--solution", required=True)
    d.add_argument("--source", help="solution folder holding index.jigx")
    d.add_argument("--tables", nargs="*", help="table names, or a file of them")

    c = sub.add_parser("check", help="validate rows against the solution source")
    c.add_argument("--solution", required=True)
    c.add_argument("--source")
    c.add_argument("--tables", nargs="*")
    c.add_argument("--offline", action="store_true", help="use the dump under <out> instead of the API")

    f = sub.add_parser("diff", help="compare two solutions; write diff.md and import/")
    f.add_argument("--from", dest="src", required=True)
    f.add_argument("--to", dest="dst", required=True)
    f.add_argument("--source", help="source folder of --from, for the default table list")
    f.add_argument("--tables", nargs="*")
    f.add_argument("--rows", action="store_true", help="list each missing/changed/extra row")

    s = sub.add_parser("seed", help="diff, then PATCH the importable rows into --to (asks first)")
    s.add_argument("--from", dest="src", required=True)
    s.add_argument("--to", dest="dst", required=True)
    s.add_argument("--source")
    s.add_argument("--tables", nargs="*")
    s.add_argument("--only-missing", action="store_true")
    s.add_argument("--include-suspect", action="store_true",
                   help="also import rows held back for a URL/GUID/email/tenant name "
                        "(holdTables stay held)")

    a = ap.parse_args(argv)
    prof = ddprofile.load(a.profile)
    if a.cmd == "diff":
        cmd_diff(a, prof)
        return 0
    return {"whoami": cmd_whoami, "dump": cmd_dump, "check": cmd_check, "seed": cmd_seed}[a.cmd](a, prof)


if __name__ == "__main__":
    sys.exit(main())
