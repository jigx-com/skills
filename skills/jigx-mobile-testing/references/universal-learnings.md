# Universal learnings: testing a Jigx solution on an Android device

Lessons from long device-testing runs (adb + `jigx_debug` MCP) that hold for any Jigx solution.
Format: **symptom → cause → what to do**. `<pkg>` = the app's Android package (branded apps have
their own, e.g. `com.jigx.<brand>.android`). Most entries cost someone an hour; read before a run.

---

## Device & adb rig

### Typing
- `input text "a b c"` lands only `a`, or leaks `b`/`c` into the next field → the device shell
  re-splits args → single-token values or `%s` for spaces; read the value back from the dump.
- Leading characters missing (`neDepot` for `TownDepot`) → typed before the tapped field took focus
  → re-dump until it is focused, then type.
- Value doubles (`8` + `8` → `88`) → the field was prefilled by draft rehydration; typing appends →
  clear first: tap → `keyevent 123` (MOVE_END) → `keyevent 67` × N (DEL) → type.
- Numeric keyboard's next-field key appends instead of advancing → tap each field explicitly.
- Values land in the wrong field when filling several from one dump → focus auto-scrolls the form →
  re-dump and re-locate by label before every field.
- `jig.list` search box never takes text (`dumpsys input_method`: `mInputShown=false`) → it cannot be
  focused via adb; a dropdown's search box can → test search there or at the SQL layer.

### Reading the screen
- Dump shows the previous screen → plain `uiautomator dump` goes stale → use `--compressed`, delete
  the host copy before each pull (a failed dump is then loud), and assert the foreground package
  (`dumpsys activity activities | grep topResumedActivity`). Do not `rm` the device-side file just
  before dumping; that made dumps fail intermittently.
- Invisible to uiautomator (dump shows the screen underneath): choice/multi-select option sheets
  (sometimes visible only from the second dump or ~3 s after the tap), the in-app camera, the Jigx
  Dev Tools error inspector, the "1 of N" error sheet behind an error toast, webviews (OAuth consent),
  most of a `jig.document` → `adb exec-out screencap -p` is ground truth.
- State attributes lie: `choice-field` `checked`/`selected` always false; every `dropdown.<id>.input`
  is `enabled="false"`; sheet submit buttons dump identically live or greyed; an `action.primary.title`
  reads enabled on a disabled button; tab `selected=` is wrong → read state from a screenshot, the
  container node, or functionally. A selected choice option can show as a narrower check icon.
- The "N questions left" counter is the cheapest oracle: no change = the write did not land.
  `component.text-field` commits on blur, so tap neutral space first.
- "No data", a blank form body, or no confirm modal at +4 s → still rendering (heavy forms 20-30 s,
  5k-row lists ~16 s) → poll ~15 s before concluding absence.
- Identically named rows → the `resource-id` carries the key (`dropdown-item.<id>`,
  `list-item.<rowId>`, whose `content-desc` often carries status tags too).
- Identify the screen by a resource-id only it has, never by screenshot size; compute taps from dump
  bounds (device px), never from a resized screenshot.
- Continuous dumping slows the app (~4 s on a 17 s save; a dump costs ~2.8 s vs ~0.7 s for
  screencap) → time waits with screencap pixel samples.

### Tapping and navigation
- `clickable="true"` node, exact centre, nothing happens → a sticky footer, toast or bottom tab bar
  overlaps it → scroll the target mid-screen; fields at the bottom edge also swallow input.
- Taps no-op, then fire 8-10 s later out of order → JS thread saturated (debug session + busy
  datasources) → one tap, wait 7-9 s, screenshot; never bursts.
- First tap after a sheet opens, a scroll, or a media pick no-ops → animation → settle, re-dump, verify.
- Scroll swipes stop working → the soft keyboard is eating them → `keyevent 111` before scroll loops.
- Never use `KEYCODE_BACK`: dirty forms raise "discard all your changes?", sheets can exit the app,
  repeated BACK lands on Profile next to Logout → use the in-app back arrow / home button. If the app
  was exited, `am start -n <pkg>/com.jigx.android.MainActivity` restores it with pairing intact.
- Dropdowns: data-driven items are `dropdown-item.<row-uuid>` (only static lists use `.0`) → select
  by text; multi-select needs **Confirm**; items toggle, so re-tapping a selected or rehydrated item
  clears it; the last item can sit under the sheet padding; "Other" reveals a field (+1 to counter).
- The More tab is an app-switcher modal; More → back does not fire `onFocus`. To fire it:
  `keyevent KEYCODE_HOME; sleep 3; am start -n <pkg>/com.jigx.android.MainActivity`, and check
  `pidof <pkg>` is unchanged (a resume, so `onLoad` stays out of the experiment).
- `adb input swipe` fired `onRefresh` on some screens and nothing on others → confirm from events
  (zero events, not even `whenResult:false`, = gesture not registered) → else cold start. Grep a jig
  for `onRefresh:` before claiming it has no pull-to-refresh.

### Device state
- Location off/on: `settings put secure location_mode 0` / `3`. Count Google's prompt with a cleared
  logcat + `grep -i LocationSettingsChecker`.
- Mock position (`setLatLong` is emulator-only): `appops set com.android.shell
  android:mock_location allow`; per provider in `fused gps network`: `cmd location providers
  add-test-provider $P`, `set-test-provider-enabled $P true`, `set-test-provider-location $P
  --location <LAT>,<LNG> --accuracy 3`. A real fix overwrites it within ~1 s → re-inject at ~1 Hz;
  verify `dumpsys location | grep "last location"` ends in `mock`. Give each probe a distinct offset
  so a stale fix cannot alias onto the expected number.
- adb cannot set the clock without root → `settings put global auto_time 0` + the Date settings UI;
  restore `auto_time 1`. A skewed clock stops the debug bridge: assert by screenshot, read DB after.
- Release builds refuse `run-as` and logcat has no JS/action lines → no bridge = no DB read at all.
- Photopicker: copy an indexed tiny image to a new name, `touch`, broadcast
  `MEDIA_SCANNER_SCAN_FILE` → it tops the grid. Re-picking an attached photo is silently ignored.
- Unattended runs: `svc power stayon true` (`usb` does not apply on AC); check
  `dumpsys trust | grep deviceLocked`; a Mac host on battery sleeps and drops adb (`pmset -g batt`).
  A biometric lock cannot be bypassed from adb; never weaken lock settings without explicit
  authorisation. Stop and ask.
- `@ctx.system.isOffline` lags the radios by ~18 s after reconnect.
- For tight timing run sleep/swipe/`svc` inside one `adb shell "..."` (device-side sleep).
- A failed-queue row or orphan left "for later" gets reaped by the next resync → capture it now.

---

## jigx_debug MCP & pairing

### Session creation
- `listening`, `devicePaired:false`, scan does nothing → `targetAppName` omitted for a branded app,
  so the deeplink targets the generic Jigx app → always pass the brand; check `deeplinkUrl` contains
  `/<brand>/app` before anyone scans. Never create with an empty `targetAppName`.
- Wrong solution → the MCP binds to the active `name:` line in `index.jigx` (stacked, comment-toggled
  identities) → read `solution.name` in every create result and after every merge. A wrong solution
  looks like an unsynced device (only framework tables).
- "Published solution … not found" / "Organization … not available" after another customer's work
  → the MCP login persists → `login_debug_user` (result lists your orgs) →
  `select_debug_organization` → create with `force`. A name can map to another solutionId under
  another user; use the returned `manageUrl`.
- "must be Owner or Editor of the solution" dialog, never pairs → grant the device account the role.
- `publish=true` fails "requires built Core SDK output" → declarative `index.jigx` solutions publish
  from the VS Code Jigx extension; then create with `publish=false`. Check the org/solution first.

### Pairing ladder (cheapest first)
1. Deeplink into the running app with the package flag, nested quotes, then tap ENABLE:
   `adb shell am start -a android.intent.action.VIEW -p <pkg> -d "'<deeplinkUrl>'"`. Unquoted, the
   device shell cuts the URL at `&` and the app ignores it (`logcat -d | grep capturedLink` ending at
   `isFromDevTool=true` proves it). Without `-p` a VIEW intent can open the browser. "Intent delivered
   to currently running top-most instance" is not a failure.
2. Cold start first, then `create_debug_session(force)`, then the new deeplink. A new session fired
   into a warm app has looped CONNECT/ENABLE every ~30 s until a cold start.
3. Human QR scan: budget one per run and ask early.
- ENABLE prompts and the orange banner are not proof; events flowing and a query answering are.
- A paired session often re-pairs from its own deeplink after a force-stop or republish, but not
  always: each force-stop is a coin flip. A deeplink into the foreground app keeps in-progress form
  state; force-stop destroys it.
- A deeplink fired while offline pairs on reconnect with no ENABLE prompt.
- Another tab sharing the MCP can make your session vanish → re-check `debug_status` per window.

### Is the bridge alive?
- `"The phone has not connected to the debug session yet"` = unpaired. `"Timed out waiting for device
  query result"` = paired but slow or blocked.
- `debug_status` all-green means nothing alone → check `lastEventAt` vs the clock,
  `heartbeatFailures`, `adb devices` (not `unauthorized`), and the event mix: only `MCP_SENT_PING` +
  `HEARTBEAT_FAILURE`, no EXPRESSION/ACTION = device not attached. `lifecycleStatus` carries no signal.
- Nearly half of queries time out on a healthy session and succeed on retry; a timed-out query's
  `RESULT` can arrive in the next window → two retries, warm with `list_device_tables` /
  `SELECT 1 AS ok`, reshape the query, and only then blame the channel. Never run queries in parallel.
- Queries block while a `jig.document` (PDF preview), a form/dropdown sheet, or a "Syncing Data" card
  is open → read before and after those screens.
- Failure modes: half-open (events flow, queries never answer); log channel dead (`Failed to decrypt
  … Malformed UTF-8`) while queries work; query channel dead (`Invalid PKCS#1 padded CONNECT
  payload`) after a force-stop. Dead channels need `create_debug_session(force)` + re-pair.
- The bridge rides device networking: no queries offline. A wifi cycle sometimes self-re-pairs in
  ~15 s and sometimes kills the session → design offline tests around screenshots + uiautomator; read
  the DB right before the cut and on the first beat after reconnect.
- Repeated wifi cycling once triggered Initial Sync onto a different solution while every tool
  "worked" → screenshot the orange banner every phase; recover via the app switcher.
- A long `track_logs` window (thousands of events) can kill the query channel → do a step's DB reads
  before opening its log window.
- Every MCP call fails `Unexpected non-whitespace character after JSON at position N` → corrupt MCP
  `state.json` (trailing garbage) → back up, truncate to the valid prefix
  (`json.JSONDecoder().raw_decode`); no restart needed; it can re-corrupt.
- A paired session changes app behaviour (PDF generation hangs, an `onFocus` branch not running were
  both suspected artifacts) → reproduce with no session before filing a product bug.

### query_device_db
- Double-quote table names: `[default/x]` and `[_fileStatus]` time out, `"default/x"` answers
  instantly. Name columns; `SELECT *` timed out on some tables.
- Limits: 20 rows (`save_query_result` has none); SELECT-only (`wipe_table` only deletes rows);
  `EXPLAIN QUERY PLAN` and `json_each(...)` subqueries rejected; ~5 s round trip (time on a host copy).
- Wrong-row quirks: string literals as `||` operands come back quoted (`'%'||'x'||'%'` → `%'x'%`;
  pass the term via a CTE value); a literal `'<NEW>'` (use `char(60)||'NEW'||char(62)`);
  `A AND (B OR C)`; `ORDER BY … LIMIT`. Cross-check surprising results.
- `LIKE '%…%'` over ~10k rows always times out → filter by id. The `tableName:` shortcut assumes an
  `(id, data)` table.
- First `COUNT(*)` after re-pairing returned 0 (stale handle) → re-issue. During (re)sync, local and
  DYNAMIC tables read 0 with no error while lookup tables look healthy → corroborate with a row you
  know exists before trusting any zero.
- `list_device_tables` omits `_`-prefixed system tables and DYNAMIC tables not yet synced → use
  `sqlite_master`. System set: `__migrations _settings _commandQueue _lastSync _fileStatus _syncScope
  _syncStatus`.
- Validate a LOCAL datasource twice, free: replay its SQL (extracted from the `.jigx`, not retyped)
  against a host `sqlite3` fixture covering every join/exclusion, then paste it (minus `--` lines)
  into `query_device_db` to prove the entity names bind.

### Log windows (track_logs / stop_collecting)
- `recent[]` is the last ~20 events; `totals` is reliable. You cannot count or time actions from
  `recent`; noisy handlers own the tail → bracket the action in a short window and still expect a miss.
- The full window (up to ~10k events) is in the MCP `state.json` `capturedEvents`, including
  `EXECUTE` events with a REST function's request and full response, the only proof of a write whose
  row will not sync back. It also holds bearer tokens and settings: extract only what you need.
- `errors[]` can carry an error from a previous window despite `cleared:true`; identical totals and
  timestamps across windows = stale buffer → check `sentAt`. `warnings`/`errors` omit some event types,
  so empty arrays do not prove a clean device. `jq` the saved file; never read a big window inline.
- A cold start shreds the window (decrypt errors only) → open windows after the app settles.
- EXPRESSION events carry `expression`, `evaluated` (compiled) and `context.datasources` with rows
  verbatim: proof a config row reached an expression without mutating config. They list only
  datasources named in that expression's own text (not ones read via `@ctx.expressions.fn(...)`).
  DATASOURCE events carry SQL verbatim, which identifies the published build.
- UI stuck: re-arm logs. ACTION count climbing = loop; zero ACTION = an await that never resolves.
- For timings the log cannot give: write `$millis()` markers into a LOCAL table from the action list,
  or diff `_syncScope.lastSyncedAt` against a written row's `timestamp`.

---

## Jigx runtime: expressions (JSONata/YAML)

- `@ctx` compiles to `$$` (JSONata root), so it survives a predicate's context switch; hyphenated
  datasource names are backtick-quoted by the compiler.
- `The symbol "@" cannot be used as a unary operator` at runtime → an unbalanced `'` in a
  `/* comment */` (`doesn't`, `user's`); the `@ctx` preprocessor is not comment-aware and swallows the
  rest. The UI can still report success while nothing ran → no apostrophes in comments; flag any
  `=`-expression with an odd `'` count before publishing.
- Comparisons with an undefined operand are false, for `=` and `!=` alike: `ds.status != 'Complete'`
  is false when no row exists; `ds.id = null` is false on an empty datasource (a create guarded that
  way is silently skipped) → `$count(ds[status='Complete']) = 0`, `$not($exists(ds.id))`,
  `$not(x = true)`. `$count` is also shape-proof (object vs 1-row array).
- Comparisons throw unless both sides share a comparable type: `"" >= 7`, `null >= 7`, `"7" >= 7`
  throw (T2009/T2010), and the throw blanks whatever the expression feeds (a `missingCount` sum, a
  summary card, a `when:`) → `$type(x) = 'number' and x >= 7` (`and` binds tighter than `? :`).
  `$number("")`/`(null)`/`({})` throw; `$uppercase` takes only string or undefined; truthiness guards
  do not guard type (boolean `true` passes).
- "Missing required parameter data for action X" → a sub-expression of the parameter threw (`$merge`
  throws on a `null` arg, drops a missing one) → `($d := …; $type($d) = 'object' ? $d : {})`.
- A one-row predicate result is a bare object, but `component.list` `data:` needs an array → fence
  it: `[@ctx.expressions.rows[...]]` (0 rows → `[]`).
- `{"value": undefined}` drops the key and sends `{}`, which an API may read as "set null".
- A `data:` fed from an `expressions:` entry re-renders when a datasource named only inside it
  changes; hoisting per-row reads into one expression costs no reactivity.
- Settle expressions locally with the jsonata pinned under `@jigx/core-sdk` (await `evaluate()`, or
  every test passes); rewrite `@ctx` → `$c` for the harness. Evaluating `git show HEAD:` vs the
  working copy and diffing outputs gives a differential without a device. It cannot catch
  preprocessor bugs.
- YAML: an inline `=` expression with `: ` breaks parsing → use a `|` block; round-trip with `yq`.
  Never global search-replace across a `.jigx` (one replace made a state hoist read its own null).
- Schema absence ≠ invalid: check the shipped builder schema and bundled templates before filing.
- `@ctx.user` and the backend who-am-I identity can differ → key ownership off the employee id.

### Geolocation
- `@ctx.system.geolocation` in expressions/component config is null-safe but still raises Android's
  location prompt when location is off.
- On the action path (`inputs`, `parameters`, `data`, `options`, even the action's own `when:`) it
  throws `ERR_LOCATION_SETTINGS_UNSATISFIED` and aborts the list (footer stuck on "Generating…",
  inert button). Ternaries and `when:` guards do not help.
- `isLocationSharingEnabled` is the location-sharing feature, not GPS availability (false with GPS
  on) → guard on `coords.latitude != null and coords.longitude != null`.
- Live reads are slow and variable (one 5-step list: 6-22 s) → hoist once into declared jig state. A
  screen that must stay silent with location off needs the read moved behind an opt-in screen.
- iOS offline returned no coords with permission and services on (observed 2026-09): a null fix is
  not proof of a permission/services problem.

### SQLite in datasources
- `json_extract` cannot tell an array from a stringified one → `json_type`. Array fields need
  `jsonProperties` on the reading datasource or JS/PDF code receives a JSON string.
- "bad JSON path" from `json_each(t.data, json_extract(...))` → the second arg is a path literal.
- `=` never matches NULL join keys → `IS`.
- Offset-suffixed times (`+02:00`) are converted to UTC before modifiers, so `date(ts, offset)` can
  shift a day; `date()` truncates to midnight, `datetime()` keeps time → replay with the real offset.
- Time-relative columns make raw diffs flaky → tolerate ±1, assert non-zero row counts on both sides.
- Counts/aggregates can arrive as text in JSONata; integer-literal `CASE` columns arrive as numbers;
  Manage → Data booleans have been seen as JSON booleans and as strings → read rows before assuming.
- `sqlite_master` is queryable from a `datasource.sqlite` (`rewriteQuery: false`) → gate DDL on
  table existence with it.
- Icons named only inside expressions (`icon: "=c ? 'a' : 'b'"`) get pruned from the auto-generated
  block of `assets/icons.jigx` → pin them in the manual block above it.

---

## Jigx runtime: data, sync & command queue

### Command queue
- `_commandQueue` = `(id, queue, type, payload, state, error, retryCount, retryAfter)`; the body is
  in `payload` (no `entity`/`data` columns).
- Success deletes the command: no row after an online submit is the success signature. `failed` rows
  park forever; `retryCount`/`retryAfter` stay NULL. Prove a write by syncing and reading
  `LastModifiedDateTime`/the field back. Retries resend the stored payload; nothing re-evaluates.
- A singular REST `execute-entity` reports `.successful` before any I/O, so `successful = false`
  retry branches never fire (an `isOffline` guard hides it in offline tests).
- DYNAMIC saves apply locally at once (offline too) and queue as `type='save'`,
  `queue='DATA_PROVIDER_DYNAMIC'` (`$.data` = written fields). They drain one at a time (~6-7 s), so a
  small offline batch is gone before the bridge returns → queue extra work (image writes) to widen
  the window, read `substr(payload,1,400)` on the first beat, never force-stop mid-drain.
- `_commandQueue.error` keeps only the status line; the full REST response is in the in-app error
  sheet (tap the toast, screenshot, scroll gently) and in `EXECUTE` events.
- Per-attempt error handlers write one row per attempt (25 for one failed upload) → count-agnostic rules.
- Status 0/503/504/empty-200 are ambiguous (the write may have committed); 401/429 are
  rejected-before-processing. Never auto-retry a non-idempotent create on the ambiguous ones.
- In a REST step the wire body is what the function's `inputTransform` builds (e.g.
  `functionParameters.msgBody`), not the sibling `data:` (the optimistic local row).

### Sync bookkeeping
- `_syncStatus` tracks DYNAMIC syncs, not REST sends. `_lastSync(provider, entity, timestamp, token)`
  answers "was it pulled, when". DYNAMIC row `timestamp`s move only on content change and are rewritten
  by the sync → record wall-clock save times yourself.
- `_syncScope`: a declared `instanceId` that never fires leaves the scope `syncing` forever. A drop
  mid-sync leaves `state='syncing'`, `syncing=0`, `notStarted>0`: no error path sees it; only
  pull-to-refresh or a cold start recovers. A foreground resume rewrites it to `interrupted`
  (`foreground_activation`) in ~100 ms → do not background mid-sync in a test that needs the sync.
- Leg order in `_syncScope` identifies which build wrote it (rows can predate the publish).
- `action.execute-sql` without `entities:` is rejected before any statement runs → list a table
  nothing binds to. `CREATE INDEX` on a not-yet-created table aborts the transaction (`default/*`
  tables appear on first write). Failures are invisible in `_syncScope` (still `synced`) and indexes
  persist across publishes → check `sqlite_master` for the specific index names.
- A VS Code publish hot-reloads the running app but skips launch actions (`onLoad`, index builds) →
  cold-start before verifying them; the banner version proves nothing about launch actions.
- Form tables declared only in the database file are pulled by the Dynamic Data provider on its own
  schedule; `sync-entities`/`onRefresh`/`onFocus` cannot force them, only a cold start. After a
  reinstall a table can stay empty until its form is first opened.
- `wipe_table` on a server-backed DYNAMIC table re-syncs on the next online launch (allow 20-30 s) →
  assert count deltas or specific ids, not empty tables.
- Manage → Data edits can lag on device, even across a cold start → wait and re-read before wiping.
- Paged syncs must carry filters on every page: `value: =@ctx.parameters."$filter"` is the proven
  idiom. Probe: `COUNT(DISTINCT <filtered field>)` > 1 = a page lost the filter. Quote `$`-prefixed
  params (`."$top"`), or they silently resolve to nothing.

### Action lists and gates
- In `isSequential: true`, a falsy `when:` skips only that action; the list continues. A nested
  `isSequential: false` list is not awaited (cheap fire-and-forget send). A child `execute-action`
  whose work is a sync scope is awaited.
- Calling the same action id twice in one sequential list has deadlocked → suspect that first.
- `set-solution-state`/`set-jig-state` `changes:` accept only keys declared in `state:`. Solution
  state is in memory; prove a write via a downstream row.
- A message inside a card gated `=(@ctx.datasources.X.state) ? … : false` never renders on a cold
  start → hoist it to a root child; test force-stop → offline → cold launch, no refresh.
- A status ladder whose all-clear is also its null fallback (`$s ? $s : 'synced'`) stays green on a
  dead datasource → only an offline run proves it alive.
- `whenResult:false` with `"<ds>": null` in the ACTION context = a missing subscription value.
- Ungating a worker: `onTableChanged` handlers are the copies that matter at runtime → grep and
  classify by enclosing block. Audit recovery with the failure state applied (self-gated recovery is
  not recovery). One missing bootstrap flag can close dozens of gates and drop a whole tab.
- After a queue drain the app has navigated itself into a form/document jig → re-assert the screen
  right before each trigger.

---

## Forms, state & navigation

- Component state survives a modal sheet's dismissal and submit for the app session (keyed by jig +
  inputs); only a restart clears it. `initialValue` covers only the first mount → compare state to the
  persisted value (default it to `''`) instead of relying on seed suppression.
- Jig state survives back-out and re-entry (a stale saved-id state outranked a navigation input) →
  assert gates that read component/jig state on a cold-started app.
- Same inputs reuse the jig instance; `component.web-view` keeps rendered `content` in component
  state → give it an `instanceId` and `reset-component-state` in `onFocus` (data refreshes do not help).
- A hydrated `initialValue` fires no `onChange`; `onChange` sees the new selection.
- `Failed to execute 'action.go-to' on '<jigId>'` names the jig owning the action, not the target;
  `additionalParameters[0].configuration` holds the full failing action. "'jigId' must not be empty"
  = the `linkTo` lookup returned nothing (often a strict string mismatch against a config table).
- `info-modal` is strictly modal (BACK, drag, backdrop do nothing); actions after it run on Close →
  time assertions from Close.
- `jig.document` slots: `source`, `summary` (one `component.summary`), `preview`, `placeholders`,
  `widgets`, `bottomSheet`; no free text slot → put hints in an action. `numberOfVisibleActions` caps
  at 2; the first child is primary and ignores `icon`.
- The SDK action schema is the authority: no `action.open-document`; `retry-queue-command`,
  `delete-queue-command`, `clear-queue` exist; `sync-entities`' `DATA_PROVIDER_DYNAMIC_FILE` is unproven.
- Horizontal scroll: only top-level `component.list` + `isHorizontal: true` (view `overflow: scroll`
  clips on Android; inside a custom component the list gets double padding).
- Missing `jig.document` action bar (observed 2026-07) and a whole footer hidden offline on one beta
  runtime (observed 2026-07) were app-runtime bugs → confirm a non-document jig's bar renders and
  compare `dumpsys package <pkg> | grep versionName` to a known-good build before editing YAML.
- With no DB access: a rendered action word reads whatever its guard reads, and a modal appearing
  proves every clause its `when:` ANDs → observe the narrowest conjunction.
- Save returns to whichever screen opened the form (dashboard vs list).

---

## Media & uploads

- `media-field` opens the native in-app camera (invisible to uiautomator); the bottom-left gallery
  thumb opens the photopicker → confirm with screencap or `dumpsys window | grep mCurrentFocus`.
- After the photopicker the app can show Home with a stale version and a floating gallery sheet, or
  a Dev Tools inspector with an old cached error → tap the sheet's own **Done** (X discards the
  attach; a drag-down also dismisses the inspector).
- Large gallery images hang PDF generation for minutes → test with tiny images.
- Media rows keep the blob in a separate `file` column (`json_extract(data,'$.file…')` is always
  null): JSON with `status` `PENDING`→`UPLOADED`, `localPath` (no scheme; `data.localPath` keeps
  `file://`), `sha1`, and a base64 `thumbnail` of tens of KB, so any WHERE on `file` reads every blob
  (an expression index fixes it). `file` is NULL or valid JSON; `localPath` survives upload.
- The thumbnail is backfilled server-side after blob sync → gate saves on the local path, never on it.
- `action.share` with a bare stored path silently becomes a text share (a `jig.document` renders the
  same path fine). `file://` alone is not enough: since an app update (observed 2026-09) persisted
  files sit under `files/`, which react-native-share's FileProvider cannot serve → run `generate-pdf`
  then `share` (no `fileUri`) in one sequential list. Diagnose: `logcat | grep RNSharePathUtil`.
- `generate-pdf` creates intermediate directories (`/` in a name is fine); a filename with no unique
  part overwrites same-day files.
- `_fileStatus` rows exist only once a blob sync starts (none offline), and their `id` may be the
  owning row's UUID, not your form key → join via the owning row.
- Kill wifi ~2 s after Save for a real Dynamic Data blob failure; killing it before dispatch only
  exercises the offline path.
- An upload started between the radio drop and `isOffline` flipping parks `failed`; on retry a queued
  status-only patch raced the row write and failed `500 … ConditionalCheckFailedException` for good.
- `patchDataRow … 400 Invalid rid` → the composite row id exceeds Dynamic Data's rid length cap.
- Captured images live in app-private storage; adb cannot read them → check in Manage → Data.

---

## Network/proxy rigs

- Airplane mode/`svc wifi disable` sets `isOffline` and tests only offline branches; a dead proxy
  port reads as offline too. For an online-and-failing ERP call, proxy everything except the ERP host
  (`settings put global http_proxy <host-ip>:<port>`; CONNECT honoured). Find the ERP host by running
  the proxy transparently and refreshing; it comes from runtime config.
- Packet-loss throttling makes the app go offline instead of failing an upload; bandwidth caps time
  out or finish before "uploading" is observable.
- `settings put global http_proxy` wipes `global_http_proxy_exclusion_list`, and the app ignores the
  list anyway → every proxy window costs the debug session; read the DB before arming and after
  clearing + re-pairing.
- Set `http_proxy` before a cold start (a running app kept going direct); toggle wifi after changes.
- Python tunnel with `create_connection(timeout=15)` keeps the timeout on the socket and kills idle
  tunnels (the debug websocket) every ~15 s → `settimeout(None)` on both sockets. Bandwidth caps and
  loss still starve the websocket.
- Lost-response replay (server commits, device never sees the response): hold downstream bytes from
  one host while an arm file exists, arm on the Nth upstream request, force-stop. Hand-timed kills
  rarely land in the ~2 s window. Count duplicates with a backend GET, not the device list.

---

## Benign platform noise (do not file)

- `MCP_INTERNAL_ERROR EPERM … rename state.json` bursts on Windows hosts (the MCP's file lock).
- Occasional `HEARTBEAT_FAILURE`, and query timeouts right after an app restart.
- `onQueryCollectionSnapshot() … Timeout for first snapshot` warnings on busy screens.
- Polling handlers flooding a window with `whenResult:false` ACTION + EXPRESSION events.
- "Activity not started, intent has been delivered to currently running top-most instance".
- "Generating PDF" hanging with a debug session attached → confirm with no session first.
- Stale Dev Tools error cards from older versions after the photopicker.
- IDE flagging `rewriteQuery` on a LOCAL sqlite datasource (it is required).
- **Not noise:** `EXPRESSIONERROR` T2009/T2010 type throws on empty fields. Once filed as benign,
  they were the top production error → fix with a type guard.

---

## Acumatica (any tenant)

- Check which endpoint a hand-test hit (`_links.self`): a field mapped on one endpoint can be absent
  on the app's, same host and tenant.
- `$custom` has three states: node absent (not requested), `{"type":"CustomStringField"}` with no
  `value` (resolved, empty), populated. A wrong `<View>.<Field>` is a 400 that fails the whole request
  (a sync returns nothing) → find the name via `…/<Entity>/$adHocSchema` or Element Properties first;
  a 400 falsifies that identifier, not the route. `type` stays `CustomStringField` for selectors.
- `{}` on a field means "set null"; omit the key to get the default.
- Selector fields validate: an unknown code is 422 with a per-field `error` in the body, while the
  app's optimistic local write looks successful.
- An auto-number create (`"<NEW>"` key) is non-idempotent: every re-send makes a new record.
- `LastModifiedDateTime` on the synced row proves a write landed if the local patch never writes it;
  response echoes can round values (coords to 6 dp), another server-truth tell.
- CustomerLocation coordinates are top-level and need `AddressOverride: true` (else 200 and nothing
  written); under a Customer contact `Address` they exist only if the endpoint adds them.
- Attribute/UDF text is captured at record save time → read the record's stored value.
- Names carry leading and double spaces → normalise before sorting or exact matching.
- A first login can park an OAuth consent page in a Chrome custom tab that stalls Initial Sync.
- A filtered GET with a bare `$expand` can 500 on an optimised export; a narrow `$select` fixed it.
