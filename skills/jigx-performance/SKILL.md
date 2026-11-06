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

## Start

**`/jigx-performance` with no argument, or "check my solution's performance"**: guide
the user; ask as little as possible.

1. **Show where you are, then ask once.** Run `scripts/preflight.py <folder>` (the
   current folder, or the path given; read-only, a few seconds) and show what it prints:
   the solution and type (YAML when it has `index.jigx`, SDK when it has `src/app.ts`),
   the **branch and commit** with any uncommitted changes, the booted or connected devices
   with their Jigx apps, and the numbered setup steps timing still needs, each with its
   fix. Then ask one question: **review this
   branch** (recommended), **another branch or folder**, or **set up a device first**.
   Do nothing else before the answer: the branch checked out is often not the one the
   user means. Ask for the path only if no solution is found. For SDK, run `jigx build`
   when `build/output.json` is missing or older than `src/`.
2. **Audit it** (read-only, seconds; "Audit" steps 1 and 4). Show in plain words: the
   counts by priority, then each high and medium finding you confirmed in the source,
   as "what is wrong · where · the fix", grouped by section, each section headed by its
   name and what it means for the app (`SECTION_MEANING` in `check-performance.mjs`,
   e.g. "F Sync: how data comes from the server; slow here means long sign-in"). Say
   plainly that every confirmed high and medium finding needs fixing. Put the full list in
   `<review>/before/audit.md` and say where it is.
3. **Ask one question** with these choices:
   - **Time it and fix it**: the full review below (timings, fixes you approve,
     timings again). Recommend this when there are high or medium findings.
   - **Fix the high findings now** without timing, as diffs in the solution's language
     (`references/fixes-by-language.md`), applied only after the user agrees.
   - **Stop here**: the report is enough.

**`/jigx-performance review [path]`**: go straight to "Review".

**Code check only: `/jigx-performance audit [path]`**, or the user asks for the audit
alone ("just check the code", "audit only", "no simulator", "static check", "a quick
check before I commit"). It needs no device, so do not mention devices or setup:

1. Run `scripts/preflight.py --code-only <folder>` (no simulator or emulator is touched)
   and show its lines: the solution, the **branch and commit** that will be audited, and
   anything the audit itself needs (Node 18+; Ruby for YAML). Do not ask which branch:
   the audit is read-only and takes seconds. Say "to audit another branch, check it out
   and run it again".
2. Step 2 above (the audit and `<review>/before/audit.md`), with no plan or timing
   questions. For SDK, run `jigx build` first when `build/output.json` is missing or
   older than `src/`.
3. End with one line, not a question: high and medium findings can be fixed as diffs
   now ("fix the high findings"), and timing them needs a simulator or emulator
   (`/jigx-performance review`). Only when the user picks one, continue there.

**Never change the branch that is checked out.** The audit and timing are read-only.
Approved fixes go on a new branch made from it (`perf/<short-name>`) or in a copy, and
you say which before the first change.

**Timing needs a device; set it up with the user, do not skip it.** Before any timing
step, rerun `preflight.py`. When it lists setup steps, or "set up a device first" was
chosen, or a simulator, emulator, adb or Maestro command fails, follow
`references/device-setup.md`: take the steps one at a time, ask before each install,
download or boot, have the user run `sudo` commands themselves with `!`, match any error
in its "Problems and fixes" table, and rerun `preflight.py` after each step until the
device shows a Jigx app with the test user signed in. For SDK projects also check the
`jigx_debug` MCP server. Only a step the user declines is skipped (Android is optional
when they time iOS only), and the report says that area was not measured. Time both
platforms when both are available, one at a time; close an emulator before starting
another.

Loaded from the task instead of a command: when the user writes or changes a
`datasource.sqlite` query, an `action.execute-sql` index or a list, follow "Build";
when they ask to time a change they made, follow "Measure".

## Review

Follow `references/customer-review.md`:

1. Confirm the solution and whether it is YAML or TypeScript SDK, the **test**
   solution to publish to, the sign-in sync and the 3–6 screens to time, and the test
   data.
2. **Audit**: static scan and plan check into `<review>/before/`.
3. **Time the current solution** after publishing it to the test solution (YAML:
   `copy-to-test.py`, then Jigx Builder; SDK: `jigx_debug`): sync
   (`track-load.py`, three runs; `--android` on an Android emulator), every query and
   `execute-sql` action on the test user's synced database and at 10x its data
   (`time-queries.mjs --scale 10`), each function from the sync steps, and the
   cold start and screen open times (`time-screens.py` with Maestro, three runs a screen,
   iOS and Android). Expressions have no runtime timing; they show in
   screen time and are located by the audit.
4. **Propose** with the overview (`review-report.py`): changes grouped by section A–H,
   ordered by priority, each section headed by what it means for the app, each change
   written as a diff in the solution's language (`.jigx` or `src/*.ts`,
   `references/fixes-by-language.md`) with file, reason, measured cost, expected effect,
   risk and the timing that should move. Mark every change **Needs fixing** (a confirmed
   high or medium finding, a measured slow step, an expression error) or **Optional**
   (low), and list what is left alone and why, so nothing relevant is missed.
5. **Stop and ask.** Recommend applying every **Needs fixing** change; the user may
   pick sections instead. Change nothing before the user agrees.
6. Apply approved sections one commit each on a new branch (never the checked-out one),
   rebuild and check after each, publish to the
   same test solution and **time everything again** (`post`); compare with
   `compare-runs.py`, `time-queries.mjs --baseline` and `time-screens.py --compare`, and
   show `<review>/report.md` with feedback: what got faster, what did not move, any
   regression, what was not verified.

## Audit

1. Run the static scan on the solution: a YAML folder (with `index.jigx`) or an SDK
   `build/output.json`. Pass the device database when one exists (`track-load.py
   --list`), so sync findings are sized by real rows and bytes:

   ```sh
   node <skill-directory>/scripts/check-performance.mjs <solution> --db <device.db> \
     --report <review>/before/audit.md > <review>/before/static.json
   ```

   `<review>` is the review folder `preflight.py` prints, outside the solution repo
   (`~/jigx-perf-reviews/<solution>/<branch>/`); never write into the checkout. The file
   names are the ones `review-report.py` reads (`references/customer-review.md`).

   Every finding has a priority (high, medium, low), a section, where it is (file, then
   the path inside it), what is wrong and the recommended fix. `audit.md` groups them
   by priority and type. It covers sync (tables nothing reads, full pulls, missing
   `$select`, large tables), sync behaviour (sequential calls that could run in waves,
   table-change handlers and their guards, actions started twice, sequential file
   transfers, syncs on every focus), unused datasources and watched tables, per-row
   datasource reads, uncapped searches, list rules (an `id` in every datasource, no
   `instanceId` on items, built-in items, `jig.list`, fixed height), `rewriteQuery`,
   unindexed, wrapped and `OR`-across-keys filters, joins on CTE columns no index serves, unindexed sorts, `execute-sql` DDL
   risks, heavy expressions, solution state and JSONata traps (`USAGE.md` lists them
   with priorities). Every finding is a lead to measure, not a verdict. Then list full
   scans and unused indexes from the query planner:

   ```sh
   python3 <skill-directory>/scripts/check-plans.py <solution> > <review>/before/plans.json
   python3 <skill-directory>/scripts/check-plans.py <solution> --scans-only
   ```

   Before a PR, run `pr-check.mjs --base origin/<target branch>`: it audits every
   solution the branch changes on the branch and on its target, lists what the branch
   adds and fixes, and exits 1 on a new high finding. `ci/jigx-performance.yml` runs it
   on every PR in GitHub Actions (`USAGE.md`).
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
- `references/device-setup.md` - setting up the iOS Simulator, an Android emulator,
  the Jigx app, Node, Java and Maestro with the user, and fixes for common setup errors.
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

- `scripts/pr-check.mjs` - the PR regression check: audits each changed solution on the
  branch and on the branch it merges into, fails on new findings at `--fail-on`; run in
  CI by `ci/jigx-performance.yml`.
- `scripts/check-performance.mjs` - dependency-free static scan of a YAML solution
  folder or `build/output.json`, with priorities, fixes and an optional Markdown report
  (`--report`), device sizing (`--db`), and a pre-PR gate (`--baseline`, `--fail-on`). Exits nonzero only on errors; most findings
  are warnings to investigate.
- `scripts/profile-query.py` - standard-library profiler for an exported SQLite
  database: counts, bytes, plan, timings, optional synthetic scale-up on a copy (join keys
  kept apart per copy, as in `time-queries.mjs`).
- `scripts/check-plans.py` - query plans for every datasource and `execute-sql` read
  against the shipped indexes: full scans, indexes used and unused, CI guard file.
- `scripts/track-load.py` - read-only live tracker of a sync or load in the iOS
  Simulator or a rooted Android emulator (`--android`): scopes, entries, queue, row counts, slow-datasource log lines, waterfall,
  total sync work also after ready, and a warning when a scope syncs twice in one run.
- `scripts/time-queries.mjs` - every datasource query, `execute-sql` step (rolled back)
  and index build timed on a copy of a device database, parameters sampled from the
  data; `--scale 10` repeats it at ten times the data and lists what grows faster than
  the data; `--baseline` compares with an earlier run. Node 22.5+.
- `scripts/time-screens.py` - screen open and cold-start times (to a ready element) in
  the iOS Simulator or on Android through generated Maestro flows; `--compare` flags only changes beyond run
  noise.
- `scripts/review-report.py` - the review report: an overview of every area, then sync,
  function, query, action and screen times
  before and after, findings per section, changes applied, what was not verified.
- `scripts/compare-runs.py` - medians and per-scope and per-entry change between
  before and after runs, with warnings for weak comparisons.
- `scripts/preflight.py` - read-only start check: the solution and type, branch, commit
  and uncommitted changes, booted simulators and connected Android devices with their
  Jigx apps, and the tools timing still needs.
- `scripts/copy-to-test.py` - a YAML solution copied under a test solution name (and
  the Builder organisation settings) for publishing with Jigx Builder; refuses the
  original's name.

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
