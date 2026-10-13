---
name: jigx-performance
description: Use when a Jigx screen or list is slow, stalls or logs "Timeout for first snapshot", when auditing an existing solution for performance, or when writing or changing a datasource.sqlite query, an action.execute-sql index, or a list whose items read datasources. Covers measuring on-device SQLite cost, expression indexes, shipping DDL safely, list query shape, per-row datasource reads, SQL-to-JSONata traps and verifying a change. Use together with the generic `jigx` skill.
---

# Jigx Performance

Jigx apps are local-first: screens read from an on-device SQLite database. A query
that takes more than 8 seconds on the device logs `Timeout for first snapshot` and the
screen stalls. Use this skill with the generic `jigx` skill, and with `jigx-acumatica`
when the data comes from Acumatica (its reads and sync contract decides how much data
reaches the device at all).

This skill covers the read path. The sync and write path is out of scope except where
an index adds to write cost.

## Pick the mode

Invoked as `/jigx-performance audit [solution path]` or `/jigx-performance build`, use
that mode. Invoked with no argument, or loaded from the task, pick by the request:

- **Audit an existing solution**: the user reports a slow screen, or asks for a
  performance review. Follow "Audit" below. Default the solution path to the current
  project and its `build/output.json`; build first if that file is missing.
- **Build or change a solution**: a new or changed `datasource.sqlite`,
  `action.execute-sql`, or list. Follow "Build" below.

## Audit

1. Build the solution and run the static scan on the generated output:

   ```sh
   node <skill-directory>/scripts/check-performance.mjs build/output.json
   ```

   It reports per-row datasource reads in list items, uncapped search lists, filter
   predicates that no shipped index matches, `execute-sql` shape and DDL risks, and
   JSONata comparison traps. Every finding is a lead to measure, not a verdict.
2. Read `references/measuring.md`. Get row **and byte** counts for the tables behind
   the slow screen on a real device, then export those tables.
3. Profile the slow query on the host at 1x and 10x:

   ```sh
   python3 <skill-directory>/scripts/profile-query.py device.db query.sql --param selectedId=123 --scale 10
   ```

   It prints the SQLite version, row and byte counts per table, the query plan and
   median timings. Attribute cost per CTE and per predicate before proposing a fix.
4. Fix the largest measured cost first, one change at a time, using the references
   below. Prefer an index to a rewrite.
5. Verify per `references/verifying.md` and report what was and was not reproduced.

## Build

1. Before writing a list or search datasource, read `references/query-shape-and-lists.md`.
2. For every filtered predicate on a table that can grow, ship a matching expression
   index per `references/indexes-and-execute-sql.md`.
3. Resolve datasource views once in `expressions:` rather than per list-item row.
4. Cast numeric SQL columns that feed `when:` guards.
5. Run `check-performance.mjs` alongside the `jigx` runtime checker before publishing,
   and profile any query over a table that can exceed a few thousand rows at 1x and
   10x with `profile-query.py`.

## Reference map

- `references/recipe-index.md` - when to read each reference.
- `references/measuring.md` - attributing cost, host vs device, bytes vs rows, the
  debug bridge, where the time goes on blob-carrying JSON columns.
- `references/indexes-and-execute-sql.md` - expression indexes, matching predicate
  text, shipping DDL through `action.execute-sql`, gating, verifying in `sqlite_master`.
- `references/query-shape-and-lists.md` - capped and pinned lists, sort keys, per-row
  datasource reads, keyed lookups, SQL to JSONata traps.
- `references/verifying.md` - equivalence checks, real-data diffs, what host SQLite
  cannot catch, the checklist.

## Scripts

- `scripts/check-performance.mjs` - dependency-free static scan of `build/output.json`.
  Exits nonzero only on errors; most findings are warnings to investigate.
- `scripts/profile-query.py` - standard-library profiler for an exported SQLite
  database: counts, bytes, plan, timings, optional synthetic scale-up on a copy.

## Guardrails

- Do not optimise before measuring. Query size is not cost.
- Do not time queries through the debug bridge (`query_device_db` is ~5 s round trip).
- Do not rewrite a query when an index can do the job; a rewrite must be diffed against
  the original on real data with non-zero row counts on both sides.
- Do not name real tables in `entities:` of an index-only `execute-sql`.
- Do not trust a successful sync as evidence that DDL worked; check `sqlite_master`.
- Do not claim a device improvement from host timings. Label every timing with the
  machine it ran on, and say what was not reproduced.
