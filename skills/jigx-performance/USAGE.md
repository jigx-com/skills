# Performance audit: how to run it

Run the audit **before you open a PR into the solution's main branch**. It takes a few
seconds, needs no device, and tells you whether your branch adds sync or performance
problems that main does not already have.

It works on a YAML solution folder (the one with `index.jigx`) and on a TypeScript SDK
project's `build/output.json`. For SDK projects, build first; findings point at the
`src/*.ts` file.

## Requirements

- Node 18+ (22.5+ only for `--db`).
- Ruby, for YAML folders (macOS and GitHub's Ubuntu runners ship it).
- The scripts: install the skill (see the README) or clone this repo. Below,
  `$PERF` is the scripts folder, e.g. `~/.claude/skills/jigx-performance/scripts` or
  `skills/jigx-performance/scripts` in a clone.

## Before every PR into main

From the solution repo, on your branch:

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
the folder.

- **Exit 0**: nothing new at high priority. Open the PR.
- **Exit 1**: the branch adds a high finding (printed with file and fix). Fix it, or
  explain in the PR why it stays.
- **Exit 2**: the scan could not run (bad path, missing build).

The stderr line `N finding(s) not in the baseline` counts everything new. Use
`--fail-on medium` to see the new medium findings too, and list any you keep in the PR
description. A finding counts as new when its type, file and message are not on main;
moving a block inside a file does not make it new.

In Claude Code you can instead say: `/jigx-performance audit <solution>, compared with
main`.

## Reading audit.md

Findings are grouped by priority, then by type. Each type shows the fix once; each
finding shows where it is (file, then the path inside it) and what is wrong:

```text
### unused-datasource (5) · E Screens and start-up
**Fix:** Delete it, or move it into the one screen that needs it.
- `src/datasources/file-upload-errors.ts`: Global datasource file-upload-errors is not referenced by any screen, expression or action.
```

| Priority | Act on it |
| --- | --- |
| High | Before merging. These cost seconds on every load or grow O(n²). |
| Medium | Confirm in the source, then fix or note in the PR why it stays. |
| Low | Only when the table is big. Add `--db <device database>` to size them; without it, size-dependent findings stay low. |

Findings are leads, not verdicts. Before a bigger change, confirm with
`check-plans.py` (query plans) or a timed load (`/jigx-performance measure`).

## Findings you can accept

- **`ddl-invalidates-datasources` at medium on a one-table index step.** One table per
  step is the documented trade-off; a step listing many tables is high and needs fixing.
- **`unused-datasource` on screens still built on sample data.** Expected until the
  screen is wired up; re-check before release.
- **`check-plans.py` errors on `_syncStatus` ("no such column")** come from the host's
  stub schema, not the solution. Pass `--schema-from` with a device database to check.
- **A synced table read only by a script or a function.** The scan cannot see that use;
  drop the finding and say so.

## In CI (optional)

The same gate as a GitHub Actions job on every PR into main:

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

## Other modes

- `/jigx-performance review <solution>`: audit, time the sync, queries, actions and
  screens, propose fixes in YAML or TypeScript by section and priority, apply what you
  approve, publish and time again (README, "Review a customer solution").
- `/jigx-performance measure <test solution>`: time one change in the simulator.
- `/jigx-performance build`: guidance while writing new queries and lists.
