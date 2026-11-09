# jigx-performance: how to use it

Open the solution in Claude Code (a YAML folder with `index.jigx`, or a TypeScript SDK
project) and type one of these:

| Type | What happens | Needs |
| --- | --- | --- |
| `/jigx-performance audit` | **Code check**: the findings in seconds, then it stops | Node 18+ (and Ruby for YAML) |
| `/jigx-performance` | **Review**: shows the branch and devices, audits, then asks: time it and fix it, fix the high findings, or stop | for timing, a simulator or emulator; Claude helps set it up |
| a question ("why is this screen slow?") | the skill loads on its own and answers with the relevant checks | - |

Start with the code check; it needs nothing else installed.

## The review

`/jigx-performance`:

1. shows what it found and asks before doing anything: the solution, the **branch and
   commit** checked out (with any uncommitted changes), the simulators and devices
   running with their Jigx apps, and what timing still needs. You confirm, or check out
   another branch yourself or set up a device and run it again. Claude never switches the
   branch for you
2. audits it in a few seconds and shows each confirmed problem as *what is wrong ·
   where · the fix*, by priority (full list in `before/audit.md` in the review folder,
   `~/jigx-perf-reviews/<solution>/<branch>/`: nothing is written into your repo)
3. asks what next:
   - **Time it and fix it**: times the sync, every query and action, the app's cold
     start and the screens you open most, in the iOS Simulator and on Android; proposes fixes as diffs in your language
     (`.jigx` or `src/*.ts`), grouped by section and ordered by priority; applies only
     what you approve; publishes to your **test** solution, times everything again and
     reports what got faster, what did not move, and any regression
     (`report.md` in the review folder)
   - **Fix the high findings now**, without timing
   - **Stop here**

Shortcuts: `/jigx-performance review` goes straight to "time it and fix it";
`/jigx-performance audit` (or "just check the code") is a **code check only**: no
simulator, emulator or setup, only Node (and Ruby for YAML folders). It shows the branch
it audits, lists the findings in seconds and stops; you can still ask for the fixes or
the timing afterwards. Nothing in your solution changes until you
approve it, approved fixes go on a new branch (never the one you have checked out; in
this checkout or a worktree, as your repo's agent rules say, and Claude asks first), and
nothing is ever published to or timed against production.

## What you need

| To | You need |
| --- | --- |
| Audit | Node 18+; Ruby for YAML folders (macOS ships it) |
| Time queries and actions | Node 22.5+ and a test user signed in and synced in the iOS Simulator |
| Time screens | Maestro and Java 17+ (`brew install mobile-dev-inc/tap/maestro`, or `curl -Ls https://get.maestro.mobile.dev \| bash` when the script is allowed). An installed Java 17 is used for Maestro even when another Java is active; your shell profile is not changed |
| Time on Android | Android SDK platform-tools (`adb`) and an emulator or a phone with USB debugging; for sync timing a **Google APIs** emulator image (not Google Play), which allows `adb root` |
| Publish | SDK: the `jigx_debug` MCP server. YAML: `copy-to-test.py` makes a renamed copy, then Jigx Builder in VS Code publishes it |

You don't have to set these up first. Claude checks them before timing (`preflight.py`)
and lists each missing piece with the command that fixes it: Xcode and an iOS runtime, a
simulator, the Android SDK and a Google APIs emulator, the Jigx app on the device, Node,
Java and Maestro. It then walks you through them one at a time, runs each install after you
agree (you type `sudo` commands yourself), and when a simulator, emulator, adb or Maestro
command fails it matches the error against known fixes (`references/device-setup.md`). It
waits until the Jigx app is open with the test user signed in; it does not skip the timing
on its own. Say "set up a device first" or "help me set up Android" at any time.

## Every PR: the regression check

Each PR is checked against the branch it merges into. The check audits every Jigx
solution the PR changes, on the PR and on its target branch (main, a release branch,
a customer branch), and fails when the PR adds a high-priority finding. The comparison
is at the merge base, so only the PR's own changes count, and findings it fixes are
listed too.

**In CI (set up once per solution repo).** Copy `ci/jigx-performance.yml` from this
skill to `.github/workflows/jigx-performance.yml` on every long-lived branch PRs merge
into, then make the "Jigx performance" check required in branch protection. It runs on
every PR, however it was opened, and puts the report in the run's summary:

```text
| Solution      | Target                        | This branch                   | New | Fixed |
| AcumaticaBase | 4 high · 315 medium · 781 low | 4 high · 306 medium · 778 low |  17 |    29 |
```

**On your machine, before opening the PR** (or ask Claude: `/jigx-performance audit,
compared with main`):

```sh
node $PERF/pr-check.mjs --base origin/main --report perf-check.md
```

`$PERF` is the skill's `scripts` folder, e.g. `~/.claude/skills/jigx-performance/scripts`.
Run it from anywhere in the repo; `--base` is the branch the PR goes into.

- **Exit 0**: nothing new at high priority. Open the PR.
- **Exit 1**: the branch adds a high finding (printed with file and fix). Fix it, or
  explain in the PR why it stays.
- **Exit 2**: the check could not run (a solution failed to load or build).

`--fail-on medium` fails on new medium findings too. A finding is new when its type,
file and message are not on the target; moving a block inside a file does not make it
new. SDK projects are built on both sides (`npm ci && npx jigx build`; change it with
`--build`).

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
| Low | Only when the table is big. `audit.md` counts them by type (`--list-low` lists each one); with `--db <device database>` size-dependent findings are raised when the data is large. |

What it checks:

| Area | Finds | Priority |
| --- | --- | --- |
| Sync | tables synced that nothing reads; full pulls with no modified-date filter; OData pulls without `$select`; tables over 5 MB or 5,000 rows | medium; full pulls and `$select` low unless `--db` shows big tables |
| Sync behaviour | 4+ sync calls run one after another (parallel waves); table-change handlers with no guard, or a guard that reads datasources on sync tables; an action started by a handler and by an action that writes the handler's table; file transfers left sequential | medium (an unguarded handler on a sync table is high) |
| Unnecessary loads | global datasources nothing uses; tables a datasource watches but does not read; a sync on every screen focus | medium |
| Joins | a join on columns a CTE computes from one table (`JOIN cte ON cte.col = …`): no index is used, the table is scanned for every row | high on a list, otherwise medium |
| Indexes | index steps that re-run every bound datasource or roll back on a fresh install; filters and sorts no index matches; an `OR` across keys of one table with an unindexed key | high (medium for a one-table step); per-row `OR` high, otherwise medium; filters and sorts low unless 1,000+ rows |
| Lists | datasource reads per row; searches with no `LIMIT`; a list datasource with no `id`; `instanceId` on list items; custom components as items | medium |
| List components | `component.list` bound to data (use `jig.list`); `hasDynamicHeight`; any other datasource with no `id` | low |
| Query rewriting | `rewriteQuery: false` with `'$.field'` shorthand (wrong data); `rewriteQuery` left on | high; low |
| Expressions and state | datasource filters inside `$map`; one expression filtering datasources 10+ times; solution state as a global datasource parameter; solution state written by a table-change handler; `!=` against a datasource field | high; medium; medium; low; low |
| Expression errors | `$trim`, `$length`, `$substring…`, `$split`, `$number`, `$toMillis` and similar on a value that can be `null` or the wrong type, with no guard: a component value, a list row's column, a SQL column the query does not `COALESCE`. These are the `T0410`/`D3030`/`D3110` errors in device logs | medium (component values, list rows, dates); low (other columns) |
| JavaScript | expression functions short and simple enough for JSONata; `console.log` in expression functions; functions nothing calls | low; medium; low |

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
| Sync | the app's sync tables, sampled live, three sign-ins, on the iOS Simulator or a Google APIs Android emulator | time to ready, each scope and step; snapshot timeouts, rows hashed for lack of an id, and every expression error with its expression and screen |
| Functions | each sync step: its REST or OData call and the write of its rows | calls outside sync (save, submit) are timed as screen journeys |
| Queries and actions | every query, `execute-sql` step and index build run on a copy of the test user's database, then again at 10x its rows | host times: right for ranking and before/after, a phone is slower; the 10x run lists what grows faster than the data, which a small test user hides |
| Screens | Maestro taps and waits for an element that appears once the data is there, three runs, on the iOS Simulator or Android | includes datasources, expressions and rendering up to that element (the report names it); the time until the screen stops moving is shown apart |
| Cold start | Maestro stops the app, launches it and waits for the first data on Home | start-up, solution load and the first datasources |
| Expressions | no separate timing exists in Jigx | inside screen time; the audit finds the costly ones |

A screen counts as faster or slower only when every run on one side beats every run
on the other by 250 ms or more; smaller moves are noise. When the medians move by 25% or
more but the runs overlap, the verdict is "unclear" and the report shows every run
(often two groups: a background sync in some runs). The sync comparison also checks the
runs: a change over 10% and 0.5 s whose runs overlap is "unclear", a large percentage of
a time under 0.5 s is "within noise", a call slower only because it now runs beside
others is "parallel now", and a call whose scope synced twice in a run is "not
comparable".

## Scripts

Claude runs these for you; they are here for CI and for running by hand. All take a
YAML folder or an SDK `build/output.json`.

| Script | Does |
| --- | --- |
| `check-performance.mjs` | the audit (`--report`, `--list-low`, `--db`, `--baseline`, `--fail-on`) |
| `pr-check.mjs` | the PR regression check against the target branch (CI: `ci/jigx-performance.yml`) |
| `check-plans.py` | query plans: full scans, unused indexes, CI guards |
| `time-queries.mjs` | every query, action and index build timed on a device database copy (`--scale 10`, `--baseline`) |
| `preflight.py` | the first thing `/jigx-performance` shows: solution, branch and commit, devices and Jigx apps, and each setup step timing still needs with its fix |
| `copy-to-test.py` | a YAML solution copied to a test solution name, with the Builder organisation settings, for publishing |
| `time-screens.py` | screen and cold-start times through Maestro, iOS or Android (`--platform`, `--compare`; `--ids` prints the ids on the screen) |
| `track-load.py` / `compare-runs.py` | live sync tracking in the iOS Simulator or an Android emulator (`--android`), and a database copy for the query timings (`--copy-db`); before/after medians and every run |
| `profile-query.py` | one query at 1x and 10x data, before and after an index |
| `review-report.py` | the before/after report |

Tests: `node --test scripts/*.test.mjs` and `python3 -m unittest discover -s scripts`.
