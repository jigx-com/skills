# Customer Review

The end-to-end flow for `/jigx-performance review <solution path>`:

1. **Audit** the solution for improvements.
2. **Time** the current solution: sync, queries, actions, screen opening (which
   includes expressions and rendering).
3. **Propose fixes** in the solution's language (YAML or TypeScript), grouped into
   sections A–H and ordered by priority. Apply only what the user approves.
4. **Publish and time again**, compare with the baseline, and report what improved.

Everything lives in the review folder `<review>`, outside the solution repo so nothing is
written into the checkout: `~/jigx-perf-reviews/<solution>/<branch>/`. `preflight.py` prints
its exact path ("Review folder"); use that path wherever `<review>` appears below.

## 0. Before starting

Confirm with the user, once:

- the solution and its kind: a **YAML** folder (with `index.jigx`) or a **TypeScript
  SDK** project (`src/app.ts`; build it with `jigx build`)
- the **test** solution to publish to, its organisation, and the app to run it in
  (branded apps need `targetAppName`). Never publish to or time against production or a
  working user's phone
- the journeys to time: the sign-in sync (always), the cold start, and the screens
  users open most (3–6, e.g. the main list, a detail screen, a form), each on the record
  with the most rows for it (the project with the most photos, drawings and notes, the
  busiest customer): a screen opened on an empty record times nothing
- the platforms: iOS Simulator and Android (an emulator; a Google APIs image for sync
  timing). Time both when the customer's users have both; run one at a time
- realistic test data: a load that is fast because the test user has ten rows proves
  nothing

Read the repo's own agent rules first (`AGENTS.md` / `CLAUDE.md` and what they route to):
they decide where fixes go, how branches are named, what to install and which checks to
run, and they win over this file. Fixes never go on the checked-out branch. Ask once and
recommend what the repo allows:

- **A new branch in this checkout** (`git switch -c perf/<ticket>-<what>` from the
  checked-out branch, named the repo's way). Use it when the repo says to work in the
  current checkout (jigx-com/acumatica does). Uncommitted changes come along: stop and ask
  if there are unrelated ones.
- **A separate worktree** (`git worktree add ../<repo>-perf -b perf/<short-name>`), when
  the repo allows it or the user asks: the checkout and its uncommitted changes stay as
  they are. `copy-to-test.py` finds the Builder settings (`.vscode/settings.json`, usually
  not committed) in the main checkout when it runs in a worktree.

After the checkout, install dependencies if the repo says so (e.g. `yarn install
--frozen-lockfile`). Commit nothing until the user approves changes.

## 1. Audit

```sh
mkdir -p <review>/before
node <skill-directory>/scripts/check-performance.mjs <solution> --report <review>/before/audit.md > <review>/before/static.json
python3 <skill-directory>/scripts/check-plans.py <solution> > <review>/before/plans.json
```

`<solution>` is the YAML folder or the SDK `build/output.json`. Check every high finding
in the source, and two or three of each medium type; drop a type whose samples are
false, and say why, before proposing anything.

## 2. Time the current solution

Publish the current build to the test solution, then record each area. Use the same
simulator, user and data for every run, before and after.

- YAML: copy the solution, set the copy's `index.jigx` `name:` and `title:` to the test
  solution and publish it with Jigx Builder (`run-and-track.md`). `jigx_debug` cannot
  build YAML.
- SDK: `jigx_debug` `create_debug_session` with `publish: true`.

| Area | Tool | Output |
| --- | --- | --- |
| Sync: time to ready, each scope and step, queue, timeouts | `track-load.py`, three runs (`pre-1..3`; on Android `--android`, labels `pre-android-1..3`) | `<review>/runs/` |
| Queries: every datasource query on the device's real data, and at 10x data (`--scale 10`: what grows faster than the data) | `time-queries.mjs` | `<review>/before/timings.{json,md}` |
| Functions: every sync step's call and write | `track-load.py` (same runs as the sync) | `<review>/runs/` |
| Actions: every `execute-sql` step (rolled back) and index build | `time-queries.mjs` (same run) | the same files |
| Screens and cold start: to the ready element, including datasources, expressions and rendering | `time-screens.py`, three runs a screen, per platform | `<review>/screens/pre.{json,md}`, `pre-android.{json,md}` |
| Expressions | no runtime timing exists; measured inside screen time, located by the audit (`per-row-datasource-read`, `filter-inside-map`) | — |

```sh
python3 <skill-directory>/scripts/track-load.py --solution <test-solution> --wait --out <review>/runs --label pre-1 --scopes <scopes>
# after the sync: a copy of the database the timings and the row-count check run on
python3 <skill-directory>/scripts/track-load.py --solution <test-solution> --copy-db <review>/before/device.db
node <skill-directory>/scripts/time-queries.mjs <solution> --db <review>/before/device.db --scale 10 \
  --report <review>/before/timings.md > <review>/before/timings.json
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --label pre --out <review>/screens
# Android, same spec
python3 <skill-directory>/scripts/track-load.py --android --solution <test-solution> --wait --out <review>/runs --label pre-android-1 --scopes <scopes>
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --platform android --label pre-android --out <review>/screens
```

- **Sync**: every run is a fresh sign-in. Start the tracker, then sign out of the app and
  sign in again; repeat for `pre-2`, `pre-3`. Drive the simulator if a UI tool is
  available, otherwise tell the user each time to sign out and sign in while it runs.
  `--scopes` takes the sync actions' `instanceId`s (`sync-identity`), not the progress
  datasources' names; the tracker lists the real ones when a name matches nothing.
- **Queries and actions** run on a copy of the test user's database after the sync, so
  they see real row counts. `--scale 10` repeats every timing on a copy with ten times
  the rows (keys that link tables kept apart per copy) and lists what grows faster than
  the data: a test user's small data set hides those, and they are the ones that get slow
  for a big customer. Read each one's plan with `profile-query.py` before proposing a fix.
  Parameters are sampled from the column the query compares them with, else guessed from
  a column with the same name (the value with the most rows: the busiest project); the
  report lists every guess. Check them, and pin others with `--params params.json`. Always
  run it on a `--copy-db` copy: a plain `cp` leaves the rows in the `-wal` file, and an
  empty database stops the run. A phone is several times slower than the host: rank by
  these numbers, do not quote them as device time.
- **Screens**: write `<review>/screens.json` once (format in `time-screens.py`):
  for each screen the tap that opens it and a text or id that is only visible once its
  data has loaded (a row number, not the title). Read the screen's YAML or TS for the
  ids, then confirm them on the simulator with `time-screens.py --ids`, which prints the
  ids and texts on the current screen. Navigate by id, never by screen coordinates.
  - **The time covers what renders before the ready element.** A row's number times the
    list; to time a per-row fix, pick what the fix changes (a row's photo count or
    status), or the change cannot show. The report names each screen's ready element.
  - **State the screens need goes in `prepare`**: steps run once before the round, e.g.
    picking the project the project screens show (a sign-in clears it).
  - **A control screen** (a static screen inside the solution; its time is Maestro's
    overhead) is optional. Never the More tab or the solution switcher: on a simulator
    that also holds production solutions, one wrong tap switches solution.
  - **A screen whose `onFocus` syncs** (the audit's `heavy-focus`) can hide tabs or rows
    while the sync runs. Every tap waits for its element, but a ready element on such a
    screen can appear before or after the sync: expect two groups of times and fix the
    focus sync first.
  Needs Maestro and Java 17+ on the host (`time-screens.py` finds an installed Java 17
  itself).

If an area cannot be recorded (no simulator, no Maestro, no populated database), say
which and continue with the rest.

## 3. Overview and proposal

Run `review-report.py <review> --solution <name>` for the baseline overview: load
time, the slowest queries, actions and screens, and the findings per section. Read the
references for each section and review what the scripts cannot see: state, expressions,
screen size, start-up, sync shape, writes, credentials. Then present:

1. **Overview**: the timings from step 2 (sync time to ready and the slowest steps, the
   slowest queries and actions, each screen's open time) and the findings by section.
2. **Proposed changes, grouped by section A–H** (`recipe-index.md`), sections ordered
   by the highest priority they contain, then by measured cost. Head each section with
   its name and one line on what it means for the app (`SECTION_MEANING` in
   `check-performance.mjs`; the audit report has the same table), so a reader who has
   never seen the letters knows why the group matters. Within a section, high first.
   Mark each change **Needs fixing** (a confirmed high or medium finding, a measured slow
   step or a logged expression error) or **Optional** (low, or a small measured gain).
   Every relevant finding appears in one of the two lists or under "Not proposed"; none
   is dropped silently. For each change:
   - **what and where**: the file and line in the solution's language, as a short diff
     (`.jigx` for YAML, `src/*.ts` for SDK; `fixes-by-language.md`)
   - **why**: the finding and the time it costs (from step 2)
   - **expected effect** and **risk**
   - **how it will be verified**: which timing should move
3. **Not proposed**: findings left alone and why (unreachable path, needs the API owner,
   needs a product decision, accepted pattern).

Propose a fix for **every expression error the runs logged** (`track-load.py` lists each
with its expression and screen), not only the slow parts: a failing expression blanks
the field or condition it drives. Then stop and ask, recommending every **Needs fixing**
change; the user may pick sections or single changes instead. Do not change any file before the
user agrees. Approval for one section does not cover another.

## 4. Apply, publish and time again

For each approved section, in order:

- before changing sync, guard or write behaviour, read the repo's application guidance
  (e.g. `docs/agents/applications.md`): what each step reads, logout and retry behaviour
- make the changes in the solution's language (`fixes-by-language.md`) with the
  section's reference, one commit per section
- for a sync change, trace what each step reads through every action it calls before
  reordering or parallelising it (`jigx` `sync-orchestration.md`)
- keep every index or rewrite equivalence-checked (`verifying.md`)
- add a `check-plans.py` guard for each per-row or per-change lookup you fixed
- append each change to `<review>/changes.json` as
  `{"section": "B", "change": "Index orders by reference", "files": ["actions/create-indexes.jigx"]}`
- check it still loads, and stop and report if it does not. First the repo's own checks on
  the changed files, when it has them (its README lists them, e.g. `yarn validate
  <file.jigx>` and `yarn cc:changed --base <base>` in jigx-com/acumatica). SDK: `jigx build`,
  then the `jigx` runtime checker (`check-runtime.mjs` reads `build/output.json` only). YAML
  has no build: run the audit again and check it reports no `parseErrors`, and parse every
  expression you changed with jsonata (each `=` value must parse)

Then audit again, publish to the same test solution and repeat every timing from step 2
with the same journeys, data and simulator:

```sh
mkdir -p <review>/after
node <skill-directory>/scripts/check-performance.mjs <solution> --report <review>/after/audit.md > <review>/after/static.json
python3 <skill-directory>/scripts/check-plans.py <solution> > <review>/after/plans.json
python3 <skill-directory>/scripts/track-load.py --solution <test-solution> --wait --out <review>/runs --label post-1 --scopes <scopes>
python3 <skill-directory>/scripts/track-load.py --solution <test-solution> --copy-db <review>/after/device.db  # row-count check
# the same data as before, so the timing measures the change, not the data
node <skill-directory>/scripts/time-queries.mjs <solution> --db <review>/before/device.db --scale 10 \
  --baseline <review>/before/timings.json --report <review>/after/timings.md > <review>/after/timings.json
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --label post --out <review>/screens --compare pre
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --platform android --label post-android --out <review>/screens --compare pre-android
python3 <skill-directory>/scripts/compare-runs.py <review>/runs --before pre --after post
python3 <skill-directory>/scripts/review-report.py <review> --solution <name>
```

Compare the row count of every table in `<review>/before/device.db` and
`<review>/after/device.db`, not only the watched tables; a faster load that fetched less is a regression. A
change that reorders or parallelises steps needs this most: it can run a step before the
data it filters by exists. Time the queries on the same data before and after (the
same sync of the same user), or the comparison measures the data, not the change.

## 5. Report

Show the user `<review>/report.md`:

- **Sync**: time to ready before and after, the steps that got faster or slower,
  snapshot timeouts and errors
- **Queries and actions**: the ones that moved, with before and after times
- **Screens**: each screen's open time before and after, faster, slower or the same
- **Findings** per section before and after, and the changes applied
- **Feedback**: which changes paid off, which did not move anything, any regression and
  its likely cause, and what to try next
- **Not verified**: what could not be measured, and that simulator and host timings show
  relative change, not a customer's device

Offer it as a page for the customer. Say plainly when a step was not run or a number got
worse.
