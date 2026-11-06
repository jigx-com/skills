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

Work on a branch in the customer repo, made without touching the checkout: `git worktree
add ../<repo>-perf -b perf/<short-name>` gives a separate folder on a new branch, and the
user's checkout and its uncommitted changes stay as they are. `copy-to-test.py` finds the
Builder settings (`.vscode/settings.json`, usually not committed) in the main checkout when
it runs in a worktree. Commit nothing until the user approves changes.

## 1. Audit

```sh
mkdir -p <review>/before
node <skill-directory>/scripts/check-performance.mjs <solution> --report <review>/before/audit.md > <review>/before/static.json
python3 <skill-directory>/scripts/check-plans.py <solution> > <review>/before/plans.json
```

`<solution>` is the YAML folder or the SDK `build/output.json`. Check every high and
medium finding in the source and drop the false ones before proposing anything.

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
# after the sync: the database the timings run on
python3 <skill-directory>/scripts/track-load.py --list
node <skill-directory>/scripts/time-queries.mjs <solution> --db <test-solution database> --scale 10 \
  --report <review>/before/timings.md > <review>/before/timings.json
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --label pre --out <review>/screens
# Android, same spec
python3 <skill-directory>/scripts/track-load.py --android --solution <test-solution> --wait --out <review>/runs --label pre-android-1 --scopes <scopes>
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --platform android --label pre-android --out <review>/screens
```

- **Sync**: arm the tracker, then sign in; repeat for `pre-2`, `pre-3`. Drive the
  simulator if a UI tool is available, otherwise ask the user to sign in while it runs.
- **Queries and actions** run on a copy of the test user's database after the sync, so
  they see real row counts. `--scale 10` repeats every timing on a copy with ten times
  the rows (keys that link tables kept apart per copy) and lists what grows faster than
  the data: a test user's small data set hides those, and they are the ones that get slow
  for a big customer. Read each one's plan with `profile-query.py` before proposing a fix. Parameters are sampled from the data; pass
  `--params params.json` for the ids the key screens open with. A phone is several
  times slower than the host: rank by these numbers, do not quote them as device time.
- **Screens**: write `<review>/screens.json` once (format in `time-screens.py`):
  for each screen the tap that opens it and a text or id that is only visible once its
  data has loaded (a row number, not the title). Read the screen's YAML or TS for the
  label and ready element, and confirm them in the simulator. Add one static screen as
  a control; its time is Maestro's overhead. Needs Maestro and Java on the host.

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

- make the changes in the solution's language (`fixes-by-language.md`) with the
  section's reference, one commit per section
- for a sync change, trace what each step reads through every action it calls before
  reordering or parallelising it (`jigx` `sync-orchestration.md`)
- keep every index or rewrite equivalence-checked (`verifying.md`)
- add a `check-plans.py` guard for each per-row or per-change lookup you fixed
- append each change to `<review>/changes.json` as
  `{"section": "B", "change": "Index orders by reference", "files": ["actions/create-indexes.jigx"]}`
- check it still loads, and stop and report if it does not. SDK: `jigx build`, then the `jigx`
  runtime checker (`check-runtime.mjs` reads `build/output.json` only). YAML has no build: run
  the audit again and check it reports no `parseErrors`, and parse every expression you changed
  with jsonata (each `=` value must parse)

Then audit again, publish to the same test solution and repeat every timing from step 2
with the same journeys, data and simulator:

```sh
mkdir -p <review>/after
node <skill-directory>/scripts/check-performance.mjs <solution> --report <review>/after/audit.md > <review>/after/static.json
python3 <skill-directory>/scripts/check-plans.py <solution> > <review>/after/plans.json
python3 <skill-directory>/scripts/track-load.py --solution <test-solution> --wait --out <review>/runs --label post-1 --scopes <scopes>
node <skill-directory>/scripts/time-queries.mjs <solution> --db <test-solution database> --scale 10 \
  --baseline <review>/before/timings.json --report <review>/after/timings.md > <review>/after/timings.json
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --label post --out <review>/screens --compare pre
python3 <skill-directory>/scripts/time-screens.py <review>/screens.json --platform android --label post-android --out <review>/screens --compare pre-android
python3 <skill-directory>/scripts/compare-runs.py <review>/runs --before pre --after post
python3 <skill-directory>/scripts/review-report.py <review> --solution <name>
```

Compare the row count of every table with a copy of the database taken after a pre
run, not only the watched tables; a faster load that fetched less is a regression. A
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
