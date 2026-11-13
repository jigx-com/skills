# Run And Track

Prove a change made the app faster by running the real load before and after it, with
a live timeline. `scripts/track-load.py` records a run; `scripts/compare-runs.py`
compares runs. Both are read-only on the device database.

## What can be timed where

| What | How | Where |
| --- | --- | --- |
| Sync and load timeline: each scope and entry, queue size, row counts | `track-load.py`, sampling the solution database read-only (every 0.25 s; 0.5 s on Android) | iOS Simulator; Android emulator with a Google APIs image (`--android`) |
| Slow datasources and errors during the run | `track-load.py` tails the app's `jigx.log.db` for WARNING/ERROR lines such as `Timeout for first snapshot` | iOS Simulator; Android emulator (`--android`) |
| Same signals on a phone | `jigx_debug` `track_logs` before the run, read after | real device |
| Request start, finish and overlap per sync wave | a proxy capture (e.g. Proxyman HAR export) of the test solution only | simulator or device |
| Screen ready: tap to the first row visible, including datasources, expressions and rendering | `time-screens.py`, a generated Maestro flow per screen | iOS Simulator, Android emulator or Android phone |
| Cold start: launch to the first data on the home screen | `time-screens.py` with a `"start": "cold"` screen | the same |
| Every query, `execute-sql` step and index build on real data | `time-queries.mjs` on a copy of the test user's database | host |
| One query's cost at 1x and 10x | `profile-query.py` on an exported database | host |

Jigx exposes no render timing. A screen's time is tap to its ready element, measured by
the UI flow, not by the runtime. A real phone's files are not reachable from the host
and `query_device_db` takes about 5 s per call, so a device gives logs and spot checks,
not a timeline.

**Screens.** `time-screens.py` runs one Maestro flow per screen: setup steps (untimed),
then the tap, then a wait for the ready element. Pick a ready element that only
appears once the data is there (a row number, not the title), and that renders last in
the part you are timing: the time covers only what renders before it, so a per-row fix
shows only when the ready element is what the fix changes (a row's photo count, not its
number). The timed tap does not wait for the screen to settle: Maestro's settle wait once
read 8.3 s, steady, for rows visible at 1.9 s, which looked like the 8 s snapshot timeout.
The settle time is reported on its own ("Settled"). Maestro's own overhead is
about 2.5–3 s for a static iOS screen on an M2 Pro and varies by ~300 ms; two runs of an
identical build once differed by 11%. So a screen counts as faster or slower only when
every run on one side beats every run on the other and the medians differ by 250 ms,
with at least 2 successful runs a side. When the medians move by 25% or more but the runs
overlap, the verdict is "unclear": usually two groups of runs (a background sync in some);
the report shows every run. A static control screen inside the solution shows Maestro's
overhead; never use the More tab or the solution switcher for it. Cold-start screens run
after all the others, so no screen meets an app that is still starting.

Navigate by id, not text or screen coordinates, and give the spec a top-level `reset` that
returns to the start screen; it runs before every warm run, and after a failed run the app
is restarted too, so one failed back step cannot break the rest (one review lost 13 of 21
runs to that). Every tap waits up to 30 s for its element first, so a tab hidden while an
`onFocus` sync runs (a `when:` on the tab) no longer fails the run. Top-level `prepare`
steps run once before the round, for state the screens need (the project to show, which a
sign-in clears). Ids that work in Jigx apps (`time-screens.py --ids` prints the ids and
texts on the current screen; with the spec it runs `prepare` first, and `--at <screen>` follows
that screen's setup and tap, so one call lists the ids of the screen to time):

| Element | Id |
|---|---|
| Back button of a pushed screen | `<jig-id>.back.icon` |
| Back button of a list screen | `navigation-bar-extended.back-button.icon` |
| A tab | `tab-bar.<uuid>-<name>` (e.g. `tab-bar.<uuid>-home`) |

A tab's text starts with its icon glyph (`\ueac1, Home`), so match tab text with `.*Home`.
`{"back": true}` is Android's back button only; on iOS tap the back button's id. The
control screen also needs an id-based ready element: its static text can match before
the screen has moved. Time each screen on the record with the most rows for it (the
project with the most photos, drawings and notes), not the first one in the list: a
screen with no rows times nothing.

**Queries and actions.** `time-queries.mjs` copies the database with `VACUUM INTO`
(read-only on the original), builds the shipped indexes on the copy, then times every
query and every `execute-sql` step inside a transaction it rolls back. Each parameter
compared with `= @p` gets a real value sampled from that column; one the query reads
another way (solution state, `instr`) is guessed from a column with the same name, taking
the value with the most rows, and listed as a guess; the rest are NULL and marked "not
measured". Run it on a copy of the database the test user's sync just filled
(`track-load.py --solution <name> --copy-db <review>/before/device.db`), the same copy
before and after: on a near-empty database every query takes a fraction of a millisecond
and nothing ranks. A plain `cp` of the database leaves the rows in its `-wal` file; the
copy then looks empty, and `time-queries.mjs` stops rather than time nothing.

**Android.** Time Android as well as iOS: rendering, lists and expressions cost
differently there, and low-end Android phones are the slowest devices most customers
use.

- Screens: the same spec runs with `--platform android` on an emulator or a phone with
  USB debugging (`adb devices`). A list row is one element on Android holding all its
  texts, so the script matches `ready` texts anywhere in an element there; pick a text
  only the target screen shows (a row's date format, not an id that Home also shows).
  `{"back": true}` presses Android's back button; per-platform steps go in
  `{"ios": [...], "android": [...]}`.
- Sync: `track-load.py --android` reads the app's databases on the emulator with the
  emulator's own `sqlite3`, read-only. It needs `adb root`, which only **Google APIs**
  system images allow (not "Google Play" ones): `sdkmanager
  "system-images;android-31;google_apis;arm64-v8a"`, then an AVD from it, and install the
  Jigx APK (`adb install`; pull it from a Play emulator with `adb shell pm path <package>`
  and `adb pull`). A phone's app files are not reachable, so on a phone time the
  sign-in with a cold-start screen instead. Each sample is an adb round trip, so entry times have about
  0.6 s resolution (scope times use the runtime's own timestamps). Android words the
  hash warning "Missing entity id, falling back to hash"; both wordings are counted.
- Queries: `track-load.py --android --solution <name> --copy-db <file>` copies the
  database off the emulator after the sync (SQLite's backup on the device, then `adb
  pull`); run `time-queries.mjs` on the copy.
- Run one platform at a time and never compare an Android run with an iOS one; the
  report records the device.

## Run

1. **Publish to a test solution only.** Never time against production or a working
   user's phone. Same simulator, fixture data, network and build settings for every run.
   - SDK solutions: `jigx_debug` `create_debug_session` with `publish: true`.
   - YAML solutions: `create_debug_session` cannot build them. Make a renamed copy and
     publish that with Jigx Builder in VS Code; the customer checkout stays untouched:

     ```sh
     python3 <skill-directory>/scripts/copy-to-test.py <solution-folder> --name <test-solution> --out <new-folder>
     ```

     It sets the copy's `index.jigx` `name:` and `title:`, copies the nearest
     `.vscode/settings.json`, and refuses the original solution's name.
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
3. **Arm, then start the load.** Arm before signing in, refreshing or opening the screen.
   Every sign-in run needs a fresh sign-in: start the tracker, then sign out of the app and
   sign in again. Tell the user each time; the tracker prints it too:

   ```sh
   python3 <skill-directory>/scripts/track-load.py --solution <test-solution> --wait \
     --label pre-1 --scopes <scope-a>,<scope-b> --table <main-table>
   ```

   - `--wait` waits for the database to appear: on a first sign-in it does not exist yet,
     and a logout may delete it (or keep it; both happened in one session).
   - **Always name `--scopes` for a sign-in or refresh journey.** Without it every scope
     started after arming counts, so a periodic background sync (an appointment-changes
     pull every few minutes) ends the run early with a one-second "ready". The names are
     the sync actions' `instanceId`s (`sync-identity`, `sync-data`), not the progress
     datasources' names (`identity-data`); a name that matches no scope is reported with
     the scopes the app has.
   - It prints a line per change, stops when every tracked scope is synced and
     `_commandQueue` is empty for `--settle` seconds (or on a failed entry, or
     `--timeout`), then prints a waterfall and writes `<review>/runs/<label>-summary.json`
     and `-samples.jsonl`. While nothing has started and no sign-out was seen it says so
     every 3 minutes, and a run that times out that way says "no sign-out seen".
   - The runtime log is shared by every solution in the app: lines another solution wrote
     (its snapshot timeouts, its errors) are left out. The first sign-in after installing
     the app is marked (`first_sign_in_after_install`), as is a run during which another
     solution synced; `compare-runs.py` names those and any run over twice the others on
     its side, and `--exclude <label>` leaves one out. Run it again rather than keep it.
   - Time from the first scope start, not from arming: the wait before it includes the
     user typing credentials.
   - **Acumatica OAuth sign-in** interrupts the identity sync while the login page waits;
     the tracker treats `interrupted` as a pause, takes that time out of every total,
     scope and entry (`interrupted_s`; raw values in `*_raw_s`), and does not count the
     restart after it as a second sync.
   - **After sign-in, switch the app to the test solution.** It opens the user's last
     solution; while nothing starts, the tracker says which other solution is syncing.
   - **Arm before the load, never during it.** A "Signing out" log line or a recreated
     database starts the run again (a reloaded build can sync once before the sign-out);
     a run armed while the sync was already going ends `invalid`.
   - After the first baseline run, copy the database for the data check below and for
     the query timings: `track-load.py --solution <name> --copy-db <review>/before/device.db`
     (it includes the `-wal` file and opens read-only on its own).
4. **Three runs per side.** `pre-1..3` on the old build, `post-1..3` on the new one, each
   from the same starting state (fresh login, or the same refresh).
5. **Compare.**

   ```sh
   python3 <skill-directory>/scripts/compare-runs.py <review>/runs --before pre --after post
   ```

   It reports medians and every run for first start to ready, the sync work (time any
   scope is syncing, also after ready), the number of scope starts, each scope and each
   entry, and warns on fewer than three runs a side, runs that did not reach ready, or
   runs from different machines. It writes `comparison.md` for the PR. Verdicts:

   | Verdict | Means |
   | --- | --- |
   | improved / REGRESSION | over 10% and 0.5 s (three sampling intervals for an entry), and every run on one side beats every run on the other |
   | same | under 10% |
   | within noise | over 10%, but a time under the noise floor (0.26 → 0.78 s is two samples) |
   | unclear | the medians moved, but the runs overlap: a call whose time varies (an OAuth login call: 1.8–5.6 s) |
   | parallel now | slower because it now runs beside other calls; the total says whether that paid off |
   | not comparable | an entry of a scope that synced twice in a run: the repeat restarted it |
   Leave a cold first run (solution download, first sign-in) out of the medians, or
   report it separately.

## Reading the result

- **Sync work and scope starts catch a second sync.** A screen's `onFocus` that syncs
  again after the sign-in sync leaves "first start to ready" unchanged, since the extra
  sync comes after ready, but doubles the sync work and the starts. `track-load.py` warns
  when a scope syncs more than once in a run. Use `--settle 15` (or more) on a sign-in so the
  run lasts long enough to see the home screen's `onFocus` sync.
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
- Simulator and emulator timings show relative change on that machine. Do not quote them as device
  timings; confirm a large win on the slowest supported device.
- Keep evidence sanitised: the scripts keep only scope and entry states, counts and
  redacted log lines. Do not add request bodies or headers to saved runs.
