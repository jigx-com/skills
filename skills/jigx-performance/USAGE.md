# jigx-performance: how to use it

## The one command

Open the solution in Claude Code (a YAML folder with `index.jigx`, or a TypeScript SDK
project) and type:

```text
/jigx-performance
```

Claude:

1. finds the solution (YAML or SDK; builds an SDK project if its build is stale)
2. audits it in a few seconds and shows each confirmed problem as *what is wrong ·
   where · the fix*, by priority (full list in `perf-review/before/audit.md`)
3. asks what next:
   - **Time it and fix it**: times the sync, every query and action, and the screens
     you open most in the iOS Simulator; proposes fixes as diffs in your language
     (`.jigx` or `src/*.ts`), grouped by section and ordered by priority; applies only
     what you approve; publishes to your **test** solution, times everything again and
     reports what got faster, what did not move, and any regression
     (`perf-review/report.md`)
   - **Fix the high findings now**, without timing
   - **Stop here**

Shortcuts: `/jigx-performance review` goes straight to "time it and fix it";
`/jigx-performance audit` only audits. Nothing in your solution changes until you
approve it, and nothing is ever published to or timed against production.

## What you need

| To | You need |
| --- | --- |
| Audit | Node 18+; Ruby for YAML folders (macOS ships it) |
| Time queries and actions | Node 22.5+ and a test user signed in and synced in the iOS Simulator |
| Time screens | Maestro and Java 17+ (`curl -Ls https://get.maestro.mobile.dev \| bash`) |
| Publish | SDK: the `jigx_debug` MCP server. YAML: Jigx Builder in VS Code |

Claude checks these before timing and tells you what is missing.

## Before every PR into main

From the solution repo, on your branch (`$PERF` is the skill's `scripts` folder, e.g.
`~/.claude/skills/jigx-performance/scripts`):

```sh
# 1. Audit main, as the baseline
git fetch origin
git worktree add ../perf-base origin/main
node $PERF/check-performance.mjs ../perf-base/<solution folder> > /tmp/perf-base.json
git worktree remove ../perf-base

# 2. Audit your branch against it, failing on new high findings
node $PERF/check-performance.mjs <solution folder> \
  --baseline /tmp/perf-base.json --fail-on high --report audit.md > audit.json
```

For an SDK project, build both checkouts first and pass `build/output.json` instead of
the folder. Or ask Claude: `/jigx-performance audit, compared with main`.

- **Exit 0**: nothing new at high priority. Open the PR.
- **Exit 1**: the branch adds a high finding (printed with file and fix). Fix it, or
  explain in the PR why it stays.
- **Exit 2**: the scan could not run (bad path, missing build).

`--fail-on medium` shows the new medium findings too; list any you keep in the PR
description. A finding is new when its type, file and message are not on main; moving
a block inside a file does not make it new.

## Reading the audit

Findings are grouped by priority, then by type. Each type shows the fix once; each
finding shows where it is (file, then the path inside it) and what is wrong:

```text
### unused-datasource (5) · E Screens and start-up
**Fix:** Delete it, or move it into the one screen that needs it.
- `src/datasources/file-upload-errors.ts`: Global datasource file-upload-errors is not referenced by any screen, expression or action.
```

| Priority | Act on it |
| --- | --- |
| High | Before merging. These cost seconds on every load, grow O(n²), or return wrong data. |
| Medium | Confirm in the source, then fix or note in the PR why it stays. |
| Low | Only when the table is big. With `--db <device database>` size-dependent findings are raised when the data is large. |

What it checks:

| Area | Finds | Priority |
| --- | --- | --- |
| Sync | tables synced that nothing reads; full pulls with no modified-date filter; OData pulls without `$select`; tables over 5 MB or 5,000 rows | medium; full pulls and `$select` low unless `--db` shows big tables |
| Sync behaviour | 4+ sync calls run one after another (parallel waves); table-change handlers with no guard, or a guard that reads datasources on sync tables; an action started by a handler and by an action that writes the handler's table; file transfers left sequential | medium (an unguarded handler on a sync table is high) |
| Unnecessary loads | global datasources nothing uses; tables a datasource watches but does not read; a sync on every screen focus | medium |
| Indexes | index steps that re-run every bound datasource or roll back on a fresh install; filters and sorts no index matches; an `OR` across keys of one table with an unindexed key | high (medium for a one-table step); per-row `OR` high, otherwise medium; filters and sorts low unless 1,000+ rows |
| Lists | datasource reads per row; searches with no `LIMIT`; a list datasource with no `id`; `instanceId` on list items; custom components as items | medium |
| List components | `component.list` bound to data (use `jig.list`); `hasDynamicHeight`; any other datasource with no `id` | low |
| Query rewriting | `rewriteQuery: false` with `'$.field'` shorthand (wrong data); `rewriteQuery` left on | high; low |
| Expressions and state | datasource filters inside `$map`; one expression filtering datasources 10+ times; solution state as a global datasource parameter; solution state written by a table-change handler; `!=` against a datasource field | high; medium; medium; low; low |

The sync-behaviour, `OR` and expression checks come from measured fixes: parallel
waves took an initial sync from 35 s to 14 s, splitting an `OR` lookup per key took a
pre-sync step from 6.5 s to 30 ms, and scheduling file downloads once and in parallel
took them from 117 s to 24 s.

## Findings you can accept

- **`ddl-invalidates-datasources` at medium on a one-table index step.** One table per
  step is the documented trade-off; a step listing many tables is high and needs fixing.
- **`unused-datasource` on screens still built on sample data.** Expected until the
  screen is wired up; re-check before release.
- **`check-plans.py` errors on `_syncStatus` ("no such column")** come from the host's
  stub schema, not the solution.
- **A synced table read only by a script or a function.** The scan cannot see that use.

## What the timing measures

| Area | How | Note |
| --- | --- | --- |
| Sync | the simulator's sync tables, sampled live, three sign-ins | time to ready, each scope and step |
| Queries and actions | every query, `execute-sql` step and index build run on a copy of the test user's database | host times: right for ranking and before/after, a phone is slower |
| Screens | Maestro taps and waits for an element that appears once the data is there, three runs | includes datasources, expressions and rendering |
| Expressions | no separate timing exists in Jigx | inside screen time; the audit finds the costly ones |

A screen counts as faster or slower only when every run on one side beats every run
on the other by 250 ms or more; smaller moves are noise.

## In CI (optional)

The pre-PR check as a GitHub Actions job on every PR into main:

```yaml
name: Performance audit
on:
  pull_request:
    branches: [main]
jobs:
  audit:
    runs-on: ubuntu-latest
    env:
      SOLUTION: path/to/solution   # the folder with index.jigx
      PERF: .jigx-skills/skills/jigx-performance/scripts
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: actions/checkout@v4
        with: {repository: jigx-com/skills, path: .jigx-skills}
      - uses: actions/setup-node@v4
        with: {node-version: 22}
      - run: |
          git worktree add ../perf-base origin/${{ github.base_ref }}
          node $PERF/check-performance.mjs ../perf-base/$SOLUTION > ../perf-base.json
          node $PERF/check-performance.mjs $SOLUTION --baseline ../perf-base.json \
            --fail-on high --report audit.md > audit.json
      - if: always()
        run: cat audit.md >> "$GITHUB_STEP_SUMMARY"
```

For an SDK project, add the install and build steps for both checkouts and point
`SOLUTION` at `build/output.json`.

## Scripts

Claude runs these for you; they are here for CI and for running by hand. All take a
YAML folder or an SDK `build/output.json`.

| Script | Does |
| --- | --- |
| `check-performance.mjs` | the audit (`--report`, `--db`, `--baseline`, `--fail-on`) |
| `check-plans.py` | query plans: full scans, unused indexes, CI guards |
| `time-queries.mjs` | every query, action and index build timed on a device database copy (`--baseline`) |
| `time-screens.py` | screen open times through Maestro (`--compare`) |
| `track-load.py` / `compare-runs.py` | live sync tracking in the simulator; before/after medians |
| `profile-query.py` | one query at 1x and 10x data, before and after an index |
| `review-report.py` | the before/after report |

Tests: `node --test scripts/*.test.mjs` and `python3 -m unittest discover -s scripts`.
