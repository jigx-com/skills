#!/usr/bin/env python3
"""Check which indexes the SQLite planner picks for every query in a Jigx solution.

Standard library only. Reads build/output.json or a YAML solution folder (loaded
through check-performance.mjs, which needs Node and Ruby), builds an in-memory database with a
stub `(id, data, timestamp)` table for every table the SQL names (or the real
schema, with --schema-from an exported device database), applies every shipped
CREATE INDEX, and runs EXPLAIN QUERY PLAN on each `datasource.sqlite` query and
each `action.execute-sql` statement that reads rows. It reports:

  * full-table scans per query, mapped from alias to table
  * the indexes each query uses
  * shipped indexes no query uses (planner level: empty tables, no ANALYZE)
  * index DDL that failed to apply, and queries the planner could not read

With --guard FILE it fails (exit 1) when a named query scans a table it must not,
or stops using an index it must. Guard file:

  {"changed-orders": {"no_scan": ["orders"], "uses": ["idx_orders_ref"]}}

Keys match a datasource or action id, or a path suffix from the report. Use it in
CI for any lookup that runs per row or per change. Plans come from the host's
SQLite; check the device's version line. Read references/indexes-and-execute-sql.md.

Examples:
  python3 check-plans.py build/output.json
  python3 check-plans.py path/to/yaml-solution --scans-only
  python3 check-plans.py build/output.json --schema-from device.db --guard perf-guard.json
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys

MASK = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/", re.S)
TABLE_REF = re.compile(r"\b(FROM|JOIN|UPDATE|INTO)\s+(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][\w/.-]*)(\s*\()?"
                       r"(?:\s+(?:AS\s+)?(?!ON\b|WHERE\b|JOIN\b|LEFT\b|INNER\b|CROSS\b|GROUP\b|ORDER\b|LIMIT\b|SET\b|USING\b|UNION\b|SELECT\b|VALUES\b)([A-Za-z_]\w*))?", re.I)
CREATE_INDEX = re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)\s+ON\s+(\[[^\]]+\]|\"[^\"]+\"|[^\s(]+)", re.I)
SDK_TABLES = {
    "_syncScope": "id TEXT PRIMARY KEY, timestamp INTEGER, state TEXT, reason TEXT, notStarted INTEGER, syncing INTEGER, "
                  "synced INTEGER, failed INTEGER, interrupted INTEGER, lastStartedAt INTEGER, lastFailedAt INTEGER, "
                  "lastSyncedAt INTEGER, details TEXT",
    "_commandQueue": "id INTEGER PRIMARY KEY, queue TEXT, type TEXT, payload TEXT, state TEXT, error TEXT, "
                     "retryCount INTEGER, retryAfter INTEGER",
}


def masked(sql):
    return MASK.sub(lambda m: m.group(0) if m.group(0)[0] in "[\"" else " " * len(m.group(0)), sql)


def unquote(name):
    return name.strip('[]"')


def table_refs(sql):
    """{alias or table: table} for every table the SQL reads or writes; table functions skipped."""
    refs = {}
    for m in TABLE_REF.finditer(masked(sql)):
        if m.group(3):  # json_each( … ) and other table-valued functions
            continue
        table = unquote(m.group(2))
        refs[table] = table
        if m.group(4):
            refs[m.group(4)] = table
    return refs


def cte_names(sql):
    return {m.group(1) for m in re.finditer(r"(?:\bWITH(?:\s+RECURSIVE)?|,)\s*([A-Za-z_]\w*)\s+AS\s+(?:NOT\s+)?(?:MATERIALIZED\s+)?\(", masked(sql), re.I)}


def walk(node, path="$"):
    yield node, path
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}.{i}")


def statement_text(s):
    return s if isinstance(s, str) else (s or {}).get("statement") or (s or {}).get("sql") or ""


def collect(app):
    queries, indexes = [], []
    for node, path in walk(app):
        if not isinstance(node, dict):
            continue
        if node.get("type") == "datasource.sqlite" and isinstance((node.get("options") or {}).get("query"), str):
            opts = node["options"]
            queries.append({"path": path, "sql": opts["query"],
                            "shorthand": opts.get("rewriteQuery") is not False and "'$." in opts["query"]})
        if node.get("type") == "action.execute-sql":
            opts = node.get("options") or node
            for i, s in enumerate(opts.get("statements") or []):
                sql = statement_text(s)
                if CREATE_INDEX.search(sql):
                    m = CREATE_INDEX.search(sql)
                    indexes.append({"name": unquote(m.group(1)), "table": unquote(m.group(2)), "sql": sql,
                                    "path": f"{path}.options.statements.{i}"})
                elif re.search(r"\b(WHERE|JOIN)\b", masked(sql), re.I) and not re.match(r"\s*(CREATE|DROP|ALTER|PRAGMA)\b", sql, re.I):
                    queries.append({"path": f"{path}.options.statements.{i}", "sql": sql, "shorthand": False})
    return queries, indexes


def build_db(queries, indexes, schema_from=None):
    conn = sqlite3.connect(":memory:")
    real = {}
    if schema_from:
        src = sqlite3.connect(f"file:{schema_from}?mode=ro", uri=True)
        real = {n: s for n, s in src.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")}
        src.close()
    tables = {i["table"] for i in indexes}
    for q in queries:
        tables |= set(table_refs(q["sql"]).values()) - cte_names(q["sql"])
    # SQLite's own tables (sqlite_master, read to test whether a local table exists) are
    # built in and cannot be created.
    tables = {t for t in tables if not t.lower().startswith("sqlite_")}
    for t in sorted(tables):
        if t in real:
            conn.execute(real[t])
        else:
            cols = SDK_TABLES.get(t, "id TEXT PRIMARY KEY, data TEXT, timestamp INTEGER")
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{t}" ({cols})')
    failed = []
    for i in indexes:
        try:
            conn.execute(i["sql"])
        except sqlite3.Error as e:
            failed.append({"index": i["name"], "path": i["path"], "error": str(e)})
    return conn, tables, failed


def explain(conn, q, tables):
    sql = re.sub(r"--[^\n]*", "", q["sql"]).strip().rstrip(";")
    params = {m.group(1): None for m in re.finditer(r"[@:$]([A-Za-z_]\w*)", masked(sql))}
    try:
        steps = [r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
    except sqlite3.Error as e:
        return {"path": q["path"], "error": str(e)}
    refs, ctes = table_refs(sql), cte_names(sql)
    scans, used = set(), set()
    for s in steps:
        for m in re.finditer(r"USING (?:COVERING )?INDEX (\S+)", s):
            used.add(m.group(1))
        m = re.match(r"SCAN (\S+)(.*)", s)
        if m and "USING" not in m.group(2) and "VIRTUAL TABLE" not in m.group(2) and m.group(1) not in ctes:
            table = refs.get(m.group(1), m.group(1))
            if table in tables:
                scans.add(table)
    out = {"path": q["path"], "full_scans": sorted(scans), "indexes_used": sorted(used), "plan": steps}
    if q["shorthand"]:
        out["note"] = "uses '$.field' shorthand with rewriteQuery on; the plan here does not see the rewrite"
    return out


def matches(key, path):
    return path == key or path.endswith("." + key) or f".{key}." in path


def check_guard(results, guard):
    failures = []
    for key, rule in guard.items():
        hits = [r for r in results if matches(key, r["path"])]
        if not hits:
            failures.append(f"{key}: no query found")
            continue
        for r in hits:
            if "error" in r:
                failures.append(f"{r['path']}: plan failed: {r['error']}")
                continue
            for t in rule.get("no_scan", []):
                if t in r["full_scans"]:
                    failures.append(f"{r['path']}: full scan of {t}")
            for name in rule.get("uses", []):
                if name not in r["indexes_used"]:
                    failures.append(f"{r['path']}: does not use {name}")
    return failures


def check_plans(app, schema_from=None, guard=None):
    queries, indexes = collect(app)
    conn, tables, failed = build_db(queries, indexes, schema_from)
    results = [explain(conn, q, tables) for q in queries]
    conn.close()
    used = {n for r in results for n in r.get("indexes_used", [])}
    report = {
        "sqlite_version": sqlite3.sqlite_version,
        "queries": len(results),
        "queries_with_full_scans": sum(1 for r in results if r.get("full_scans")),
        "plan_errors": [{"path": r["path"], "error": r["error"]} for r in results if "error" in r],
        "indexes_shipped": len(indexes),
        "indexes_failed": failed,
        "indexes_unused": sorted({i["name"] for i in indexes} - used - {f["index"] for f in failed}),
        "results": [r for r in results if "error" not in r],
        "limits": "Planner on empty stub tables without ANALYZE. Shows whether an index can be used, not device timing.",
    }
    if guard is not None:
        report["guard_failures"] = check_guard(results, guard)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("output", nargs="?", default="build/output.json")
    p.add_argument("--schema-from", help="exported device database whose table schema to use (read-only)")
    p.add_argument("--guard", help="JSON file of per-query rules; failures exit 1")
    p.add_argument("--scans-only", action="store_true", help="list only queries with full scans")
    args = p.parse_args(argv)
    if os.path.isdir(args.output):
        scanner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-performance.mjs")
        run = subprocess.run(["node", scanner, args.output, "--dump-app"], capture_output=True, text=True)
        if run.returncode:
            p.error(run.stderr.strip() or "could not load the solution folder")
        app = json.loads(run.stdout)
    else:
        with open(args.output, encoding="utf-8") as f:
            app = json.load(f)
    guard = None
    if args.guard:
        with open(args.guard, encoding="utf-8") as f:
            guard = json.load(f)
    report = check_plans(app, args.schema_from, guard)
    if args.scans_only:
        report["results"] = [r for r in report["results"] if r["full_scans"]]
    print(json.dumps(report, indent=2))
    return 1 if report.get("guard_failures") else 0


if __name__ == "__main__":
    sys.exit(main())
