---
name: jigx-performance
description: Use when a Jigx screen, list or sync is slow, stalls or logs "Timeout for first snapshot", when auditing an existing solution for sync and performance problems (a prioritized report of unread or oversized syncs, full pulls, unused datasources, slow queries and lists), when timing a load before and after a change, or when writing or changing a datasource.sqlite query, an action.execute-sql index, or a list whose items read datasources. Covers measuring on-device SQLite cost, live load tracking and before/after comparison, expression indexes and query-plan guards, shipping DDL safely, list query shape and components, state and expression cost, screen size, and verifying a change. Use together with the generic `jigx` skill.
---

# Jigx Performance

Jigx apps are local-first: screens read from an on-device SQLite database. A query
that takes more than 8 seconds on the device logs `Timeout for first snapshot` and the
screen stalls. Use this skill with the generic `jigx` skill, and with `jigx-acumatica`
when the data comes from Acumatica (its reads and sync contract decides how much data
reaches the device at all).

This skill covers the read path and how much the sync brings onto the device: tables
nothing reads, full pulls, unselected fields, payload size and sync timing. Sync order,
delta design and writes live in the `jigx` skill (`sync-orchestration.md`,
`writes-and-duplicates.md`).

## Pick the mode

Invoked as `/jigx-performance review [solution path]`, `/jigx-performance audit
[solution path]`, `/jigx-performance build` or `/jigx-performance measure [test
solution]`, use that mode. Invoked with no argument, or loaded from the task, pick by
the request:

- **Review a customer solution end to end**: baseline times, an overview, proposed
  changes by section, apply only what the user approves, measure again and report what
  improved. Follow "Review" below. This is the default for "review this solution" or
  "make this customer's app faster".

- **Audit an existing solution**: the user reports a slow screen, or wants findings
  without the full review, or a prioritized list of sync and performance problems.
  Follow "Audit" below. Default the solution path to the current project: its folder
  when it has `index.jigx`, else its `build/output.json` (build first if missing).
- **Build or change a solution**: a new or changed `datasource.sqlite`,
  `action.execute-sql`, or list. Follow "Build" below.
- **Measure a change**: the user wants before/after times or to watch a load. Follow
  "Measure" below.

## Review

Follow `references/customer-review.md`:

1. Confirm the solution and whether it is YAML or TypeScript SDK, the **test**
   solution to publish to, the sign-in sync and the 3–6 screens to time, and the test
   data.
2. **Audit**: static scan and plan check into `perf-review/before/`.
3. **Time the current solution** after publishing it to the test solution: sync
   (`track-load.py`, three runs), every query and `execute-sql` action on the test
   user's synced database (`time-queries.mjs`), and screen open times (`time-screens.py`
   with Maestro, three runs a screen). Expressions have no runtime timing; they show in
   screen time and are located by the audit.
4. **Propose** with the overview (`review-report.py`): changes grouped by section A–H,
   ordered by priority, each written as a diff in the solution's language (`.jigx` or
   `src/*.ts`, `references/fixes-by-language.md`) with file, reason, measured cost,
   expected effect, risk and the timing that should move.
5. **Stop and ask which sections to apply.** Change nothing before the user agrees.
6. Apply approved sections one commit each, rebuild and check after each, publish to the
   same test solution and **time everything again** (`post`); compare with
   `compare-runs.py`, `time-queries.mjs --baseline` and `time-screens.py --compare`, and
   show `perf-review/report.md` with feedback: what got faster, what did not move, any
   regression, what was not verified.

## Audit

1. Run the static scan on the solution: a YAML folder (with `index.jigx`) or an SDK
   `build/output.json`. Pass the device database when one exists (`track-load.py
   --list`), so sync findings are sized by real rows and bytes:

   ```sh
   node <skill-directory>/scripts/check-performance.mjs <solution> --db <device.db> --report audit.md > audit.json
   ```

   Every finding has a priority (high, medium, low), a section, where it is (file, then
   the path inside it), what is wrong and the recommended fix. `audit.md` groups them
   by priority and type. It covers sync (tables nothing reads, full pulls, missing
   `$select`, large tables), unused global datasources, per-row datasource reads,
   uncapped searches, list rules (an `id` in every list datasource, no `instanceId` on
   items, built-in items, `jig.list`, fixed height), `rewriteQuery`, unindexed and
   wrapped filters, `execute-sql` DDL risks and JSONata traps. Every finding is a lead to measure, not a verdict. Then list full scans and
   unused indexes from the query planner:

   ```sh
   python3 <skill-directory>/scripts/check-plans.py <solution> --scans-only
   ```

   Before a PR into main, audit main first and pass it as `--baseline main.json
   --fail-on high`: the scan then lists what the branch adds and exits 1 on a new high
   finding. `USAGE.md` has the steps and a CI job.
2. Read `references/measuring.md`. Get row **and byte** counts for the tables behind
   the slow screen on a real device, then export those tables.
3. Profile the slow query on the host at 1x and 10x:

   ```sh
   python3 <skill-directory>/scripts/profile-query.py device.db query.sql --param selectedId=123 --scale 10
   ```

   It prints the SQLite version, row and byte counts per table, the query plan and
   median timings. Attribute cost per CTE and per predicate before proposing a fix.
4. Check each high and medium finding in the source before reporting it, and drop the
   false ones (e.g. a table read only by a script). Review the sections the scripts
   cannot see (state, expressions, screen size, start-up). Give the user `audit.md`,
   high priority first, with the findings you confirmed and the ones you dropped.
5. Fix the largest measured cost first, one section and one change at a time, using
   the references below. Prefer an index to a rewrite.
6. Verify per `references/verifying.md`, time it per `references/run-and-track.md`, and
   report what was and was not reproduced.

## Build

1. Before writing a list or search datasource, read `references/query-shape-and-lists.md`.
2. For every filtered predicate on a table that can grow, ship a matching expression
   index per `references/indexes-and-execute-sql.md`.
3. Resolve datasource views once in `expressions:` rather than per list-item row.
4. Cast numeric SQL columns that feed `when:` guards.
5. Run `check-performance.mjs` and `check-plans.py` alongside the `jigx` runtime
   checker before publishing, and profile any query over a table that can exceed a few
   thousand rows at 1x and 10x with `profile-query.py`.
6. Add a `check-plans.py --guard` entry for every lookup that runs per row or per
   change, so a later edit cannot silently bring the scan back.

## Measure

1. Publish the build to a **test** solution (`jigx_debug` `create_debug_session`) in the
   iOS Simulator. Never time against production.
2. Run `scripts/track-load.py` before each load: three runs on the old build
   (`--label pre-1..3`), three on the new one (`post-1..3`), same data and simulator.
   It prints the load live and saves a summary and a waterfall per run.
3. Run `scripts/compare-runs.py` and put its `comparison.md` in the PR. Investigate any
   slowdown over 10%, new `Timeout for first snapshot` lines or changed row counts.
   Read `references/run-and-track.md`.

## Reference map

- `references/recipe-index.md` - when to read each reference.
- `references/measuring.md` - attributing cost, host vs device, bytes vs rows, the
  debug bridge, where the time goes on blob-carrying JSON columns.
- `references/customer-review.md` - the review flow: audit, time, propose by section,
  approval, apply, publish, time again, report.
- `references/fixes-by-language.md` - each finding's fix in YAML and in TypeScript SDK.
- `references/run-and-track.md` - live load timeline in the simulator, three runs a
  side, medians and the slowdown threshold, what can be timed where.
- `references/indexes-and-execute-sql.md` - expression indexes, matching predicate
  text, `OR` across keys, materialised CTEs, plan guards, unused indexes, shipping DDL
  through `action.execute-sql`, gating, verifying in `sqlite_master`.
- `references/query-shape-and-lists.md` - capped and pinned lists, sort keys, shaping
  in SQL, `rewriteQuery`, list components, per-row datasource reads, keyed lookups, SQL
  to JSONata traps.
- `references/state-and-screens.md` - unused datasources, watched tables, state,
  shared expressions, screen size, start-up work, fix order.
- `references/verifying.md` - equivalence checks, real-data diffs, what host SQLite
  cannot catch, the checklist.

## Scripts

- `scripts/check-performance.mjs` - dependency-free static scan of a YAML solution
  folder or `build/output.json`, with priorities, fixes and an optional Markdown report
  (`--report`), device sizing (`--db`), and a pre-PR gate (`--baseline`, `--fail-on`). Exits nonzero only on errors; most findings
  are warnings to investigate.
- `scripts/profile-query.py` - standard-library profiler for an exported SQLite
  database: counts, bytes, plan, timings, optional synthetic scale-up on a copy.
- `scripts/check-plans.py` - query plans for every datasource and `execute-sql` read
  against the shipped indexes: full scans, indexes used and unused, CI guard file.
- `scripts/track-load.py` - read-only live tracker of a sync or load in the iOS
  Simulator: scopes, entries, queue, row counts, slow-datasource log lines, waterfall.
- `scripts/time-queries.mjs` - every datasource query, `execute-sql` step (rolled back)
  and index build timed on a copy of a device database, parameters sampled from the
  data; `--baseline` compares with an earlier run. Node 22.5+.
- `scripts/time-screens.py` - screen open times (tap to ready element) in the iOS
  Simulator through generated Maestro flows; `--compare` flags only changes beyond run
  noise.
- `scripts/review-report.py` - the review report: sync, query, action and screen times
  before and after, findings per section, changes applied, what was not verified.
- `scripts/compare-runs.py` - medians and per-scope and per-entry change between
  before and after runs, with warnings for weak comparisons.

## Guardrails

- Do not optimise before measuring. Query size is not cost.
- Do not time queries through the debug bridge (`query_device_db` is ~5 s round trip).
- Do not rewrite a query when an index can do the job; a rewrite must be diffed against
  the original on real data with non-zero row counts on both sides.
- Do not name real tables in `entities:` of an index-only `execute-sql`.
- Do not trust a successful sync as evidence that DDL worked; check `sqlite_master`.
- Do not claim a device improvement from host timings. Label every timing with the
  machine it ran on, and say what was not reproduced.
- Do not claim a speed-up from one run, from query plans alone, or from a load that
  fetched fewer rows. Compare medians of three runs a side with equal final counts.
- Do not run timing loads against a production solution or a working user's device.
- Do not change a customer solution before the user approves the proposed sections.
