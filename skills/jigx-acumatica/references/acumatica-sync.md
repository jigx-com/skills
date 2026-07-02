# Acumatica Sync

## Local-First Default

The default architecture is:

1. Save locally.
2. Mark sync state.
3. Let a centralized sync or submit action select pending records.
4. Call Acumatica.
5. Merge responses locally.
6. Mark records remote/synced.

Do not make Acumatica the primary save path unless the app intentionally uses a direct
"Send to Acumatica" submit flow.

Separate the lifecycle into explicit blocks:

```text
initial hydration
  -> Acumatica reads populate local tables

local work
  -> user edits local tables and queue intent rows only

safe inbound refresh
  -> Acumatica reads update clean local rows and show change indicators

explicit outbound sync/submit
  -> queued Acumatica writes drain in order
```

Do not let normal screen open/focus or ordinary local saves create hidden Acumatica
writes. If a screen needs latest server information during the day, put that behind a
read-only refresh action and protect dirty local rows.

## Offline Batch Create/Update Rule

When the user asks for "offline batch", "save offline", "upload pending", "sync later",
or similar wording for an Acumatica entity, use one local table for that entity by
default.

For example, if the app already syncs Acumatica customers into `customers`, then new
local customers must also be saved into `customers`:

- save new local rows into the same entity table with `Remote: "new"`
- save edits to previously synced rows into the same entity table with `Remote: "dirty"`
- select pending rows from that table where `Remote != "remote"`
- call Acumatica from a centralized sync/upload action with `executeEntities`
- on successful response, `upsert-merge` the response back into the same table with
  `Remote: "remote"` and a sync timestamp

Do not introduce a separate table such as `customerCreateQueue`, `customer-create-queue`,
or `pendingCustomers` for the same entity unless the business process requires a
different record shape that is not the entity cache. If a separate table is used, state
the reason before implementing it.

Inbound sync for the same table must normally use `upsert-merge`, not `delete-insert`,
because `delete-insert` can remove local `Remote: "new"` or `Remote: "dirty"` rows that
have not been sent yet.

For larger parent-child entities, prefer normalized local tables over one large cached
JSON document when users edit children offline. REST functions can assemble the
Acumatica JSON shape from normalized local rows at sync time.

## Remote Field

Use `Remote` only for sync tracking:

- `new` - created locally and never synced
- `dirty` - previously synced but edited locally
- `remote` - in sync with Acumatica

Do not overload `Remote` to mean "draft" or "not complete". Use separate business
state fields for draft/readiness.

Default missing `Remote` values deliberately. For existing cached rows that came from
Acumatica before the app tracked `Remote`, default missing values to `remote` in pending
selectors so old synced rows are not accidentally uploaded as new records.

```sql
WHERE COALESCE(json_extract(data, '$.Remote'), 'remote') != 'remote'
```

If the project stores wrapped data shapes, include the actual path used by the table,
for example `json_extract(data, '$.data.Remote')`.

Some legacy Jigx apps use boolean `Remote` markers during refresh. When adding dirty
guards to an existing project, inspect how the table currently stores `Remote` before
writing selectors. Treat `LocalDirty`, `SyncStatus`, `data_location`, temp IDs, and
explicit delete markers as stronger signals than a legacy refresh marker.

## Dirty-Aware Inbound Refresh

Inbound refresh is allowed to read Acumatica while the user has connectivity, but it
must not erase work collected locally.

Use guards like:

```sql
COALESCE(json_extract(data, '$.LocalDirty'), 0) != 1
AND COALESCE(json_extract(data, '$.SyncStatus'), '') NOT IN ('pending', 'failed')
AND COALESCE(json_extract(data, '$.data_location'), '') NOT IN ('local', 'deleted')
AND id NOT LIKE '\_tmp%' ESCAPE '\'
```

Recommended pattern:

1. Pull a lightweight change indicator or timestamp list.
2. Compare it to a committed read watermark that is not derived from dirty local rows.
3. Show the user that server changes exist.
4. Refresh only safe records or safe child rows.
5. Keep the old committed watermark when dirty local work blocks a server change.
6. Make blocked changes visible to the user instead of hiding the banner.

If cleanup removes remote records that are now inactive or deleted, first identify the
safe parent set that has no dirty children, pending commands, or pending files. Delete
children only for that safe set, then delete the parents.

## Command Queue Shape

Use an operation queue when writes must be ordered, replayed, retried, or combined with
file uploads. Queue rows should store intent, not payload snapshots:

```json
{
  "operationType": "entity-upsert",
  "operationFamily": "entity",
  "scopeType": "parent",
  "scopeId": "local-or-remote-id",
  "sequenceMillis": 1780000000000,
  "status": "notStarted",
  "attemptCounter": 0,
  "payloadStrategy": "justInTime",
  "payloadSnapshot": null
}
```

Coalesce data update operations for the same parent when possible. For example, many
local child edits can become one queued parent data upsert that builds the current
body from local tables later. Keep business actions that must preserve timeline order
as separate queue operations with their original mobile timestamp.

## Just-In-Time Payloads

Queued REST functions should receive the minimum identifiers needed to find local data.
Inside the function:

1. query the queue row
2. query the latest parent row
3. query child rows and related local tables
4. build Acumatica `.value` wrapped JSON in `inputTransform`
5. send the request
6. merge the Acumatica response back into local tables
7. clear dirty markers only for rows covered by that response

Do not build and store the full REST body when the user presses Save. That creates a
stale payload maintenance problem when the user edits the same record multiple times
before syncing.

## Syncable State

Only sync records that satisfy business readiness. For example, a parent order may
require minimum header fields and at least one line item.

## Parent-Child Replacement

When Acumatica assigns the real parent key:

1. sync the parent first
2. read the remote key from the response
3. run find-replace operations to update child rows from the temporary local ID/key to
   the remote key
4. sync children after their references have been updated

If queued commands still reference temporary IDs, include command queue replacement
when required.

When Acumatica can accept parent plus children in one aggregate REST call and returns
enough child data to correlate safely, prefer that single call. Keep files separate
because file upload endpoints usually require real remote IDs or NoteIDs.

Use a local correlator for new child rows when remote IDs are not known yet. After the
parent response:

- update child rows with remote IDs and parent keys
- update file queue rows waiting on that parent or child
- update operation queue rows that still contain temp IDs
- leave unresolved children/files queued if correlation is ambiguous

Never overwrite a temp ID in one table without replacing references in dependent
tables and queued commands.

## Command Queue Retry

Failed REST calls remain in the command queue as failed commands. A retry action should
requeue failed commands for the current business record in command order.

Do not ask users to select arbitrary individual failed calls unless the business flow
can safely retry them out of order. Usually, retry all failed calls for the record in
queue order.

When starting a full send cycle again, clear or supersede old visible error records so
the user sees the current attempt.

Queue drain best practices:

- Start the drain from a visible user action such as Sync now, Submit, or Send.
- Mark one command `syncing` before starting its REST call.
- Use `_syncStatus` or an equivalent runtime status table to detect interrupted calls.
- Block later commands while one command is failed, terminal failed, or still syncing.
- On success, mark the command succeeded and trigger the next command.
- On failure, preserve the command, write a user-visible error row, and stop the drain.
- Keep progress visible while the drain is active; do not remove and re-add banners as
  each individual command changes state.

For operations where a lost success response could create duplicates, run an
idempotency or "find existing remote record" preflight before retrying the create. If
the remote record already exists, reconcile local state and mark the command succeeded
instead of creating a duplicate.

## Error UX And Repair

Acumatica may return useful details inside 400/500 response bodies. Parse and store:

- a short user message
- the target entity or line when available
- the original technical body for diagnostics
- a repair hint or nearest screen route

The app should stop dependent sync work after an error and take the user to the
nearest screen where the local data can be fixed. Retrying should use the corrected
local rows and rebuild the payload just in time.

Network errors should be user friendly. Store the raw technical message separately,
but show guidance such as "Run Sync again when the connection is stable."

## Direct Submit Flow

Some apps intentionally use a direct "Send to Acumatica" action rather than background
sync. In that case:

- keep regular editing local-first
- guard submit with business readiness checks
- verify the PDF or generated artifacts are current
- warn and offer regenerate if artifacts are stale
- write a submission status row after successful send
- preserve retry information for failed calls

Only one quote/order should be marked as the active order when the business requires
single-order submission. If the selected quote is not active, ask whether to make it
active, update sibling quotes, then submit.

## Validation Checklist

Before deploy, inspect generated YAML and confirm:

- the local create/edit form saves to the same entity table used by inbound sync
- new rows set `Remote: "new"` and edited synced rows set `Remote: "dirty"`
- the pending datasource selects from that same table with `Remote != "remote"`
- inbound sync uses `upsert-merge` for local-first tables that can contain unsynced rows
- Acumatica response operations `upsert-merge` back into the same table with
  `Remote: "remote"`
- datasource SQL declares every table it references under `entities`
