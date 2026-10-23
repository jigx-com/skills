# Jigx field lessons

Device-proven traps from real Jigx solution work that bite in any solution. Each bullet reads
**symptom → cause → do this**. Customer ids and tenants are stripped; the mechanism is what carries over.
Placeholders: `<package>` = the branded Android package (e.g. `com.jigx.<app>.android`), `<app>` = the
branded app name passed as `targetAppName`.

Local test harness for every JSONata item below: the `jsonata` bundled with the SDK
(`$(npm root -g)/@jigx/core-sdk/node_modules/jsonata`, 1.8.x). Its `evaluate()` returns a **Promise**:
an un-awaited result is truthy and every test spuriously passes. Bind context as
`await jsonata(expr).evaluate({}, { c: data })` after rewriting `@ctx.` to `$c.`.

## JSONata traps

- **`$`-prefixed keys must be quoted.** A table sits at exactly the function's page size (e.g. 1000 rows) and
  paging never fires → `@ctx` compiles to `$$`, and `.$top` is a *variable reference*, not a key lookup, so it
  resolves to nothing with no error → quote every `$`-param, and make continuations carry `$filter` forward
  (page 2+ otherwise drops the filter).
  ```
  {"$top": $$.parameters.$top}    => {}            silently empty
  {"$top": $$.parameters."$top"}  => {"$top":1000}
  ```
  Whenever a synced count equals `$top` exactly, suspect this first.

- **`$number('')` throws.** `Unable to cast value to a number: ""` on mount → `x != null ? $number(x) : null`
  lets an empty string through → use truthiness: `x ? $number(x) : null`. Safe because `"0"` is truthy in
  JSONata (only `""` is falsy), so a stored zero still casts to `0`.

- **A comparison with an undefined operand is `false`.** A guard blocks the very first write on a fresh record →
  `@ctx.datasources.d.status != 'Complete'` is `false` when `d` returned no row → use the set form, which is
  correct for undefined, `[]`, a bare object, a one-element array and a null field:
  ```
  $count(@ctx.datasources.d[status = 'Complete']) = 0
  ```

- **`$not($boolean(x))` is undefined when `x` is missing.** A button meant to hide when nothing is selected stays
  visible → a function on a missing value returns undefined, and `$not(undefined)` is undefined → compare to
  `true` first: `when: =$boolean(x) = true`, `isHidden: =$not($boolean(x) = true)`.

- **`$exists()` is true for null.** A presence check passes on an explicit null → `$exists` tests for a key, not a
  value → use truthiness or `$count(...)` depending on the shape.

- **SQLite counts can come back as text.** A `when: = ... = 0` guard silently never fires → columns without type
  affinity (including framework tables such as `_syncScope`) return `'0'`, and `'0' = 0` is false →
  `CAST(col AS INTEGER)` in the datasource SELECT, once, at the source. Verify by replaying the query in
  `sqlite3 -json :memory:` and feeding the JSON to the SDK jsonata.

- **An apostrophe inside a `/* */` comment breaks the whole expression.** Runtime error
  `The symbol "@" cannot be used as a unary operator`, while the UI still shows success (e.g. "Form Saved")
  and nothing was written → the `@ctx` preprocessor runs before JSONata and is not comment-aware, so `doesn't`
  opens a string that never closes and raw `@ctx` reaches JSONata → no possessives or contractions in comments,
  or keep quotes balanced. Lint: flag any `=` expression where `(expr.match(/'/g)||[]).length % 2`. A host-side
  harness that rewrites `@ctx` itself **cannot** catch this.

- **A missing leading `@` resolves to undefined silently.** `ctx.datasources.x` (no `@`) evaluates to nothing; a
  no-else ternary then drops the clause (e.g. an incremental filter quietly becomes a full pull) → grep for
  `[^@]ctx\.` in expressions.

- **`false != null` is true, so a `false` reset breaks fallback chains.** A picker or datasource keyed on jig state
  returns nothing, but only on re-entry → a `set-jig-state ... : false` reset plus
  `state.x != null ? state.x : inputs.x` stops at `false`; a `go-to` with identical inputs reuses the same jig
  instance, which keeps that `false` until a cold start (a fresh mount has `null`, so it hides there) → test
  truthiness (`x ? x : ...`) in fallback chains, and always test a warm re-entry through the same route.

- **Manage → Data saves flags as strings.** A feature turns **on** when the admin sets it to false → editing a DD
  config flag in Manage → Data stores `"false"`, and `$boolean("false")` is true; a JSON `true` instead arrives
  as `1`, and `1 = true` is false → normalise in the datasource SELECT, not in each reader:
  ```sql
  CASE WHEN lower(json_extract(data,'$.flag')) IN ('1','true') THEN 1 ELSE 0 END AS flag
  ```
  When reverting a flag after a test, set JSON `false` or delete the key, never the string `"false"`.

## YAML and expression scripts

- **Colon-space in an inline expression breaks YAML.** `Nested mappings are not allowed in compact mappings` →
  `label: =$x ? 'A' : 'B'`, or a plain multi-line SQL scalar with `-- note: ...`, is parsed as a mapping → put
  any expression or SQL containing `: ` in a `|` block scalar. Validate with the SDK's `yaml` + `jsonata`
  before committing (the `jigx` CLI has no validate command for standard `.jigx` repos).

- **`#` inside a `value: |` block is literal text.** The parameter receives the comment block instead of the
  expression → inside a block scalar `#` is not a YAML comment, so the value no longer starts with `=` →
  put comments above the key.

- **Expression scripts run in strict mode (since ~2026-10-07).** A date/time helper returns null and every
  caller shows blank, with nothing changed in the solution → `scripts/expressions/*.js` now run strict; an
  assignment to an undeclared name throws (`Property 'x' doesn't exist`) and the **whole calling expression**
  becomes null → declare every variable. Find offenders with
  ```
  npx -y -p typescript@5 tsc --allowJs --checkJs --noEmit --target es2020 --module es2020 \
    --lib es2020,dom --skipLibCheck scripts/expressions/*.js     # look for TS2304
  ```
  Test scripts as ESM in node without pre-defined globals. In a debug capture, `EXPRESSIONERROR` and
  error-level `LOG` events carry the real JS error; plain `EXPRESSION` events only show the null.

## Runtime: actions, state, components

- **Entity writes have no `onError`.** A failed LOCAL/DYNAMIC `execute-entity` is silent → the schema has no error
  hook on `execute-entity`/`execute-entities`; don't add a fake one → guard the payload expression instead:
  `($f := @ctx.expressions.formdata; $type($f) = 'object' ? $f : {})`.

- **`action.execute-sql` needs a non-empty `entities` array.** `'entities' must be a non-empty string array`, and
  nothing runs; when it is chained at the tail of a sync, `_syncScope` still says `synced` → set `entities`
  deliberately: the real tables if data changes, a table nothing binds to (e.g. `[default/debug]`) for DDL,
  which avoids re-running every datasource on the real tables. Verify DDL via `sqlite_master`, never the
  sync scope (indexes persist across publishes, so an old `idx_%` listing looks like success).

- **`initialValue` resolves after `onFocus`.** An `onFocus` guard on a datasource-sourced jig state still sees
  null and fires (e.g. a GPS request) → `onFocus` runs at mount, before `datasource.sqlite` results land →
  rehydrating from a draft fixes the data, never the focus path. Do not add `ds.x = null` to the guard:
  unresolved is undefined and `undefined = null` is false, so it would never fire on a new record.

- **Web-view content stays stale on a reused jig.** Entity fields update but the HTML does not, until a cold start
  → `go-to` with the same inputs reuses the jig instance, and `component.web-view` holds rendered HTML in
  component state; `sync-entities` and `queryParameters` ticks refresh data, not component state → give the
  web-view an `instanceId` and run `action.reset-component-state` with `instanceIds: [<id>]` in `onFocus`
  (and after any sync/save in the same action list).

- **Dynamic Data row ids have a length cap.** A DYNAMIC save parks as `failed`:
  `patchDataRow(): Failed to patch row '<id>' ... 400 {"message":"Invalid rid"}` → observed: 50 and 58 chars
  sync, 65 and 68 are rejected (cap somewhere in 59–64) → count the worst-case composite id (longest parent key,
  e.g. a pending/offline-created record's temporary key, plus suffixes like `_image_<n>`) before adding one.
  Treat 400 Invalid rid as id length first.

- **Row identity of a form session is not a single id.** If an id is minted once per mounted form and the user
  can switch the subject record inside the form, several rows share that id → key per-row reads on the full
  composite (subject key + session id + user id), normalised the same way the write site builds it.

## Command queue and sync

- **The command queue does not re-evaluate payloads.** A retry cannot recapture GPS, a timestamp or any other
  value → the evaluated payload is stored in `_commandQueue` and re-sent verbatim (one failed submit produced
  25 identical retry rows) → fix capture-timing bugs where the payload is built, not in the retry path.

- **Successful commands are deleted from `_commandQueue`.** "I submitted twice and the queue still holds the same
  two `failed` rows" → there is no `synced` state; a success is deleted, so only `queued`/`processing`/`failed`
  are ever visible and an unchanged queue is the success signature → prove a write landed from the synced
  entity row's `LastModifiedDateTime` and field value. Parked `failed` rows are stale leftovers nothing clears.

- **A command can park at `queued` with `error` NULL.** Nothing that keys on a non-empty `error` sees it →
  `getTokenCredentials() Retry count exceeded` (the app cannot fetch the access token) → `am force-stop` and
  re-fire the session deeplink; it clears without a QR scan.

- **Solution state is not queryable, but a parked command shows it.** `_settings` reads empty and
  `@ctx.solution.state` is in-memory → `SELECT id, queue, type, payload, state, error, retryCount FROM _commandQueue`
  (there is no `data` column): a REST row's `parameters` carry the resolved credential name and base URL; a DD
  row carries `@ctx.user.email`, the **app** login, which is not the debug MCP login. To force a row to exist,
  go offline (`adb shell svc wifi disable`), do the writes, and read the queue through the UI while offline;
  online, small commands drain faster than the bridge reconnects.

- **"Stuck on Fetching Data" is a stalled `_syncScope`, not `interrupted`.** First check:
  ```sql
  SELECT id, state, notStarted, syncing, synced, failed, interrupted FROM [_syncScope]
  ```
  `state='syncing'` with `syncing=0` and `notStarted>0` is the tell: connectivity dropped mid-sync, the remaining
  `execute-action` steps never dispatched, and no `failed`/`interrupted` handler recognises it. Reconnect and
  `onFocus` do not recover it; pull-to-refresh on the sync screen or a cold start does.

- **Error tables are created lazily.** `no such table: <errors-table>` is genuine evidence that no error was ever
  written, not a broken query.

- **Reads taken during a sync are junk.** `_fileStatus` / `_commandQueue` read 0 rows mid-sync. Wait for the
  scope to settle before reading.

## jigx_debug MCP and pairing

- **Never create a session with an empty `targetAppName`.** The session sits at `listening`, `devicePaired: false`,
  and `list_device_tables` says the phone has not connected, which reads like a scan that has not happened →
  an empty value builds `app.jigx.com/app?...&appName=` and opens the generic Jigx app → always pass `<app>`, then
  read `deeplinkUrl` back and confirm it contains `/<app>/app` and `appName=<app>` before anyone scans.
  Recreating a never-paired session is free; a wrong scan is not.

- **Deeplink pairing needs `-p <package>`.** A bare VIEW intent opens Chrome (app.jigx.com links are not verified
  app links) and pairing silently fails → deliver it to the app:
  ```
  adb shell am force-stop <package>
  adb shell am start -a android.intent.action.VIEW -p <package> -d "'<deeplinkUrl>'"
  ```
  The nested quotes keep `&` away from the device shell. Firing into an already-running app delivers the intent
  but does not pair, so force-stop first. Then tap **ENABLE** in the "Jigx Dev tools" dialog via uiautomator.
  This pairs even a fresh session with no QR scan; a VS Code publish does not break it.

- **The session binds to `index.jigx`'s `name:` line.** `list_device_tables` → only `_commandQueue`;
  `sqlite_master` → only the framework tables (`__migrations`, `_settings`, `_lastSync`, `_syncScope`,
  `_syncStatus`, `_fileStatus`, `_commandQueue`); it looks like an unsynced device → `create_debug_session` has
  no solution parameter and resolves it from `build/output.json`, `output.json` or the uncommented `name:` line
  in `index.jigx`, which may differ from what the phone runs (the orange banner shows the running one) →
  read the name line first, compare `solution.name` on the create result with the banner, and if they differ
  fix the (uncommitted) name line, close, recreate and re-pair.

- **`debug_status` is expensive and disruptive.** Each call (like `create_debug_session`) returns a full base64 QR
  PNG, and it re-runs the device handshake, re-raising the ENABLE dialog; repeated calls can lock the app in a
  prompt loop that eats every adb tap → call it almost never; if the dialog loops, close the session.

- **`stop_collecting` shows only the last 20 events.** The full window (up to 10k) is in
  `~/Library/Application Support/jigx-debug-mcp/state.json` under `capturedEvents`; `PROVIDER` events list every
  local entity write with its payload (write order and timing for races; local only, never server results).
  The file holds live access tokens: filter with `jq`, quote only needed fields, never attach it.

- **Slow channel vs dead channel.** A `query_device_db` timeout alone means nothing; results often land late, in
  the next `stop_collecting` window. Alive = any `RESULT`/`REPLY`/`EXPRESSION`/`ACTION` events. Dead = only
  `MCP_SENT_PING` + `HEARTBEAT_FAILURE`, or `Failed to decrypt CONNECT payload ... Invalid PKCS#1` with
  `lifecycleStatus` stuck at `handshake-sent` (while `connected`/`devicePaired` still claim true). Try
  `am force-stop` + the same deeplink before spending a `force: true` recreate.

- **Things that kill or stall the bridge.** An HTTP proxy kills it for good (the app ignores the exclusion list).
  `svc wifi disable` sometimes costs a re-pair; plan offline windows for the bad case, with screenshots and
  uiautomator as evidence (no DB reads while offline). Backgrounding the app (HOME + resume) can kill the
  query channel. `KEYCODE_BACK` out of a root jig exits to the launcher; recover with
  `am start -n <package>/com.jigx.android.MainActivity` (keeps pairing). `query_device_db` times out while a
  `preview-pdf`/document screen is open and recovers once you navigate away.

- **`query_device_db` is SELECT-only.** No `UPDATE` fixtures; the branded app is not debuggable, so `adb run-as` is
  out too. Build fixtures through the UI or a test-only branch.

- **`wipe_table` can close a gate instead of opening a repro.** Wiping a table that a sync function's `when:` gate
  counts skips the function entirely, so you observe zero requests and prove the gate, not the logic behind it.
  Evaluate the expression locally with the SDK jsonata instead.

- **`login_debug_user` can fail with `Invalid UUID at userId` and still succeed.** Some Cognito user ids are not
  RFC-4122, so the tool's output schema rejects them; `debug_status` shows `hasAuth: true` but you never get the
  org list → read the org id from `state.json` (`auth.organizations`) and call `select_debug_organization`.

- **The MCP login is not the app login, and both are sticky.** After testing another org, the MCP is still logged
  in as that user and errors never say so. Check `state.json` (`auth.username`,
  `selectedOrganization.organizationName`, `solution.solutionName`) at the start of every run.

- **MCP servers added mid-session are not callable until Claude Code restarts**, even when `claude mcp list` says
  Connected.

- **Verify a published solution read-only.** GET
  `{API}/strata/organizations/{orgId}/solutions/{name}?expandContent=FULL` with a Management API token: it
  returns `version`, `updatedAt` and the full definition (`jigs`, `actions`, `datasources`). Check one marker per
  PR instead of trusting the banner. Never use this path to write.

## Acumatica side (any tenant)

- **A 200 that writes nothing.** `CustomerLocation` Latitude/Longitude are top-level, and persist only with
  `AddressOverride: {"value": true}` in the same PUT (without it the location has no address record of its own).
  Status codes prove nothing: re-GET and check `LastModifiedDateTime` moved.
- **`PUT .../<Entity>/{id}` is not an update route.** It routes to the file controller and 500s "Invalid uri
  structure". Update by key fields in the body (GET-by-id is fine).
- **`$custom` naming one field the tenant lacks 500s the whole request**, and the error names only the first
  missing column. Keep per-tenant custom field lists.
- **OData v3 `datetime'...'` has no offset field.** An incremental watermark with `+02:00` in it is skewed or
  dropped; use `datetimeoffset'...'`, and never append a fake offset to naive GI columns.
- **"Cannot be found in the system" on a record that exists locally** is a tenant/company-context symptom first:
  reads and writes may resolve their credentials from different places.
