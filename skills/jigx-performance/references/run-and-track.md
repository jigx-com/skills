# Run And Track

Prove a change made the app faster by running the real load before and after it, with
a live timeline. `scripts/track-load.py` records a run; `scripts/compare-runs.py`
compares runs. Both are read-only on the device database.

## What can be timed where

| What | How | Where |
| --- | --- | --- |
| Sync and load timeline: each scope and entry, queue size, row counts | `track-load.py`, sampling the solution database read-only every 0.25 s | iOS Simulator |
| Slow datasources and errors during the run | `track-load.py` tails the simulator's `jigx.log.db` for WARNING/ERROR lines such as `Timeout for first snapshot` | iOS Simulator |
| Same signals on a phone | `jigx_debug` `track_logs` before the run, read after | real device |
| Request start, finish and overlap per sync wave | a proxy capture (e.g. Proxyman HAR export) of the test solution only | simulator or device |
| Screen ready: tap to the first row visible, including datasources, expressions and rendering | `time-screens.py`, a generated Maestro flow per screen | simulator |
| Every query, `execute-sql` step and index build on real data | `time-queries.mjs` on a copy of the test user's database | host |
| One query's cost at 1x and 10x | `profile-query.py` on an exported database | host |

Jigx exposes no render timing. A screen's time is tap to its ready element, measured by
the UI flow, not by the runtime. A real phone's files are not reachable from the host
and `query_device_db` takes about 5 s per call, so a device gives logs and spot checks,
not a timeline.

**Screens.** `time-screens.py` runs one Maestro flow per screen: setup steps (untimed),
then the tap, then a wait for the ready element. Pick a ready element that only
appears once the data is there (a row number, not the title). Maestro's own overhead is
about 2.5–3 s for a static iOS screen on an M2 Pro and varies by ~300 ms; two runs of an
identical build once differed by 11%. So a screen counts as faster or slower only when
every run on one side beats every run on the other and the medians differ by 250 ms.
Put a static screen in the spec as a control.

**Queries and actions.** `time-queries.mjs` copies the database with `VACUUM INTO`
(read-only on the original), builds the shipped indexes on the copy, then times every
query and every `execute-sql` step inside a transaction it rolls back. Each parameter
compared with `= @p` gets a real value sampled from that column; others are NULL and
marked. Run it on the database the test user's sync just filled, the same one before
and after: on a near-empty database every query takes a fraction of a millisecond and
nothing ranks.

## Run

1. **Publish to a test solution only.** Never time against production or a working
   user's phone. Same simulator, fixture data, network and build settings for every run.
   - SDK solutions: `jigx_debug` `create_debug_session` with `publish: true`.
   - YAML solutions: `create_debug_session` cannot build them. Copy the solution, set the
     copy's `index.jigx` `name:` and `title:` to the test solution, and publish the copy
     with Jigx Builder in VS Code. The customer checkout stays untouched.
   - Builder publishes to the organisation in `.vscode/settings.json`
     (`jigx.workspace.organizationId`); point `jigx_debug` at the same one
     (`select_debug_organization`), or it reports the solution as not found.
   - A branded app needs `targetAppName` (the branded app's name); without it the link opens the
     standard app.
2. **Get a simulator you can see.** In Xcode 27 the simulator window is DeviceHub
   (`Xcode.app/Contents/Applications/DeviceHub.app`); Simulator.app is gone. A branded
   app's simulator build installs with `xcrun simctl install <udid> <path>.app`. Time on
   the simulator, not a phone: the phone debug connection can stall after sign-in and
   never answer queries.
3. **Arm, then start the load.** Arm before signing in, refreshing or opening the screen:

   ```sh
   python3 <skill-directory>/scripts/track-load.py --solution <test-solution> --wait \
     --label pre-1 --scopes <scope-a>,<scope-b> --table <main-table>
   ```

   - `--wait` waits for the database to appear: on a first sign-in it does not exist yet,
     and a logout may delete it (or keep it; both happened in one session).
   - **Always name `--scopes` for a sign-in or refresh journey.** Without it every scope
     started after arming counts, so a periodic background sync (an appointment-changes
     pull every few minutes) ends the run early with a one-second "ready".
   - It prints a line per change, stops when every tracked scope is synced and
     `_commandQueue` is empty for `--settle` seconds (or on a failed entry, or
     `--timeout`), then prints a waterfall and writes `perf-runs/<label>-summary.json`
     and `-samples.jsonl`.
   - Time from the first scope start, not from arming: the wait before it includes the
     user typing credentials.
   - After the first baseline run, copy the database for the data check below:
     `sqlite3 "file:<db>?mode=ro" ".backup 'baseline.db'"`.
4. **Three runs per side.** `pre-1..3` on the old build, `post-1..3` on the new one, each
   from the same starting state (fresh login, or the same refresh).
5. **Compare.**

   ```sh
   python3 <skill-directory>/scripts/compare-runs.py perf-runs --before pre --after post
   ```

   It reports medians for first start to ready, each scope and each entry, flags any
   slowdown over 10% and 0.5 s (a review threshold, not an SLA; smaller steps are one or
   two samples of noise), and warns on fewer than three runs a side, runs that did not
   reach ready, or runs from different machines. It writes `comparison.md` for the PR.
   Leave a cold first run (solution download, first sign-in) out of the medians, or
   report it separately.

## Reading the result

- **First start to ready** is the user's wait. Scope durations use the runtime's own
  `lastStartedAt`/`lastSyncedAt`; entry times have the sampling interval's resolution.
- **The waterfall shows waves.** Entries that should overlap but run one after another
  point at a sequential action; an entry that starts only after an unrelated one ends
  points at a needless dependency (`sync-orchestration.md` in the `jigx` skill).
- **A faster total with new `Timeout for first snapshot` lines or errors is not an
  improvement.** Compare the log signals as well as the times.
- **Check the data, not only the time.** Compare the row count of every table with the
  baseline copy, not only the `--table`s you watched; a load that is fast because it
  fetched less is a regression. One parallel-sync change looked 10 s faster with two
  reference tables empty; only the full comparison caught it.

## Rules

- Record build, simulator, solution and data set with every run; `compare-runs.py`
  refuses to hide a machine change, but it cannot see a data change.
- Median of three or more, never one run. Report the spread when runs disagree widely.
- Simulator timings show relative change on that machine. Do not quote them as device
  timings; confirm a large win on the slowest supported device.
- Keep evidence sanitised: the scripts keep only scope and entry states, counts and
  redacted log lines. Do not add request bodies or headers to saved runs.
