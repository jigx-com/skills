#!/usr/bin/env python3
"""Profile a Jigx datasource query against an exported device SQLite database.

Standard library only. Never modifies the input database: every run works on a
temporary copy. Prints, for the copy at 1x (and optionally scaled up):

  * the SQLite version and machine, so timings are labelled
  * row and byte counts for every table the query reads, with the largest column
  * EXPLAIN QUERY PLAN, flagging full-table SCANs
  * median and min wall time over N runs, plus result row count
  * optional: the same after applying DDL (e.g. a CREATE INDEX), and a result-set
    equivalence check against a rewritten query, with non-zero row counts asserted

Host timings show relative cost, not device timings. Read references/measuring.md.

Examples:
  python3 profile-query.py device.db query.sql
  python3 profile-query.py device.db query.sql --param selectedId=123 --param search=
  python3 profile-query.py device.db query.sql --scale 10 --ddl index.sql
  python3 profile-query.py device.db query.sql --compare rewrite.sql
"""
import argparse
import json
import os
import platform
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

STRINGS = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*.*?\*/", re.S)


def masked(sql):
    return STRINGS.sub(lambda m: " " * len(m.group(0)), sql)


def strip_comments(sql):
    return re.sub(r"--[^\n]*|/\*.*?\*/", "", sql, flags=re.S)


def param_names(sql):
    return sorted({m.group(1) for m in re.finditer(r"[@:$]([A-Za-z_]\w*)", masked(sql))})


def parse_value(raw):
    """name=value binds a string; name:=<json> binds a JSON value; name= binds NULL."""
    if ":=" in raw:
        name, value = raw.split(":=", 1)
        return name, json.loads(value)
    name, value = raw.split("=", 1)
    return name, (value if value != "" else None)


def tables_read(conn, sql):
    names = set()
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][\w/]*)", sql, re.I):
        names.add(m.group(1).strip('[]"'))
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return sorted(n for n in names if n in existing)


def qident(name):
    return '"' + name.replace('"', '""') + '"'


def table_stats(conn, table):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({qident(table)})")]
    if not cols:
        return {"table": table, "rows": 0, "bytes": 0, "largest_column": None}
    sums = ", ".join(f"COALESCE(SUM(LENGTH({qident(c)})),0)" for c in cols)
    row = conn.execute(f"SELECT COUNT(*), {sums} FROM {qident(table)}").fetchone()
    by_col = dict(zip(cols, row[1:]))
    largest = max(by_col, key=by_col.get)
    return {"table": table, "rows": row[0], "bytes": sum(by_col.values()),
            "largest_column": largest, "largest_column_bytes": by_col[largest]}


def plan(conn, sql, params):
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return [r[-1] for r in rows]


def time_query(conn, sql, params, runs):
    timings, result = [], []
    for _ in range(runs):
        start = time.perf_counter()
        result = conn.execute(sql, params).fetchall()
        timings.append((time.perf_counter() - start) * 1000)
    return {"median_ms": round(statistics.median(timings), 3), "min_ms": round(min(timings), 3),
            "runs": runs, "result_rows": len(result)}, result


def scale_tables(conn, tables, factor):
    """Multiply rows in each table by `factor`, suffixing an `id` column so keys stay unique."""
    notes = []
    for table in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({qident(table)})")]
        conn.execute(f"CREATE TEMP TABLE _seed AS SELECT * FROM {qident(table)}")
        col_list = ", ".join(qident(c) for c in cols)
        for k in range(1, factor):
            select = ", ".join((f"{qident(c)} || '_x{k}'" if c.lower() == "id" else qident(c)) for c in cols)
            try:
                conn.execute(f"INSERT INTO {qident(table)} ({col_list}) SELECT {select} FROM _seed")
            except sqlite3.IntegrityError as error:
                notes.append(f"{table}: stopped scaling at {k}x ({error})")
                break
        conn.execute("DROP TABLE _seed")
    conn.commit()
    return notes


def measure(conn, sql, params, runs, label):
    tables = tables_read(conn, sql)
    timing, result = time_query(conn, sql, params, runs)
    steps = plan(conn, sql, params)
    return {"label": label, "tables": [table_stats(conn, t) for t in tables], "plan": steps,
            "full_scans": [s for s in steps if s.startswith("SCAN") and "USING" not in s and "CONSTANT" not in s],
            "timing": timing}, result


def canonical(rows):
    return sorted(json.dumps(list(r), default=str, sort_keys=True) for r in rows)


def profile(db, sql, params=None, runs=5, scale=1, ddl=None, compare=None):
    sql = strip_comments(sql).strip().rstrip(";")
    bind = {name: None for name in param_names(sql)}
    bind.update(params or {})
    report = {"machine": f"host {platform.node()} {platform.machine()} {platform.system()}",
              "sqlite_version": sqlite3.sqlite_version,
              "note": "Host timings show relative cost, not device timings. Check the device's SQLite version line.",
              "params": bind, "measurements": []}
    workdir = tempfile.mkdtemp(prefix="jigx-profile-")
    try:
        copy = os.path.join(workdir, "copy.db")
        shutil.copyfile(db, copy)
        conn = sqlite3.connect(copy)
        if scale > 1:
            report["scale_notes"] = scale_tables(conn, tables_read(conn, sql), scale)
        label = f"{scale}x" if scale > 1 else "1x"
        before, before_rows = measure(conn, sql, bind, runs, label)
        report["measurements"].append(before)
        if ddl:
            conn.executescript(ddl)
            after, after_rows = measure(conn, sql, bind, runs, f"{label} after DDL")
            report["measurements"].append(after)
            report["ddl_changed_results"] = canonical(before_rows) != canonical(after_rows)
        if compare:
            other = strip_comments(compare).strip().rstrip(";")
            other_bind = {name: bind.get(name) for name in param_names(other)}
            rewrite, rewrite_rows = measure(conn, other, other_bind, runs, f"{label} rewrite")
            report["measurements"].append(rewrite)
            a, b = canonical(before_rows), canonical(rewrite_rows)
            report["equivalence"] = {
                "original_rows": len(a), "rewrite_rows": len(b),
                "both_non_zero": bool(a) and bool(b),
                "identical": a == b,
                "verdict": ("EQUIVALENT" if a == b and a else
                            "INCONCLUSIVE: empty result on at least one side" if not (a and b) else "DIFFERENT"),
            }
        conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return report


def read_arg(value):
    if value is None:
        return None
    if value == "-":
        return sys.stdin.read()
    return open(value, encoding="utf-8").read() if os.path.exists(value) else value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("db", help="exported device SQLite database (never modified)")
    p.add_argument("query", help="SQL file, '-' for stdin, or the SQL text itself")
    p.add_argument("--param", action="append", default=[], help="name=value (string), name:=<json>, name= (NULL)")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--scale", type=int, default=1, help="multiply rows of the tables the query reads, e.g. 10")
    p.add_argument("--ddl", help="SQL file or text applied to the copy before a second measurement (e.g. CREATE INDEX)")
    p.add_argument("--compare", help="rewritten query to diff against the original")
    args = p.parse_args(argv)
    params = dict(parse_value(x) for x in args.param)
    report = profile(args.db, read_arg(args.query), params, args.runs, args.scale,
                     read_arg(args.ddl), read_arg(args.compare))
    print(json.dumps(report, indent=2))
    eq = report.get("equivalence")
    return 1 if eq and eq["verdict"] != "EQUIVALENT" else 0


if __name__ == "__main__":
    sys.exit(main())
