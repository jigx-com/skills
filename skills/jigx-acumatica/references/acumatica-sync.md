# Acumatica Sync

## Device-Local Acumatica Storage

For device-local drafts and caches whose remote owner is Acumatica, use unprefixed
table names and the local provider. `default/*` identifies Jigx Dynamic Data: even REST result operations or
local-provider actions on these names generate cloud synchronization commands.
Use local SQLite datasources, `.local(table, 'save')` execute actions, and
`provider: 'DATA_PROVIDER_LOCAL'` plus entity/method on submit-form actions.
Keep intentional Jigx-shared data in Dynamic Data; determine ownership per table
rather than moving every table to local storage.

When correcting an existing default-table implementation, migrate existing rows
before sync without changing IDs. Preserve draft/signature/file data and storage
metadata. Write an idempotent migration marker after all copies. Block submission
until the marker exists and return a visible not-ready result from preflight. Do not
rely on a false REST `when` or a thrown expression: either can become a successful
no-op in the runtime. Archive only obsolete Dynamic Data commands before removing
those exact commands; preserve REST commands and in-flight work.

The intended flow is local save, pending-state tracking, explicit Acumatica submit,
then merge the server response into the same local table.

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

`upsert-merge` alone does not protect a matching dirty row. Exclude local new,
dirty, pending-delete and actively edited rows from inbound updates at the point
of persistence, including each child table. Preserve their stable IDs and dirty
markers until the matching outbound save is confirmed. A clean parent flag does
not prove its children are safe to replace. For isolated temporary editing scopes,
protect the saved aggregate while that scope is active without marking an untouched
record as pending submission; release that protection on Save/Cancel or recovery
of an abandoned editing scope.

Bound document refresh by the product's chosen date field and window (for example,
documents created in the last four weeks). Capture the cutoff once per refresh and
reuse it across pages; fetch related rows for that parent set, not unrelated history.
Creation-date windows intentionally exclude older documents modified recently. Use
a modification watermark only when that is the requested policy. Reference lookups
may need their own scope. A row absent from a page or date window is not evidence
of remote deletion: retain local new/dirty records regardless of age, and perform
any cache eviction or confirmed remote-deletion reconciliation separately with
the same edit, child and pending-command protections.
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

## Stable Local And Remote Keys

Preserve the local parent ID and store the returned remote key separately when
children and queued commands already use that local ID. Build API references from
the stored remote key after the parent save succeeds.

If an existing app deliberately replaces local keys, update child rows and queued
references together before allowing dependent writes. Never replace only the parent
row ID and leave children or retry commands pointing at the old ID.

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

For native actions that create linked records asynchronously, persist intent and the
pre-action identity/revision snapshot before sending. Store the returned status URL
and local-to-remote mapping durably. A 202 acknowledges acceptance, not completion:
poll the documented status resource with bounded attempts and resume after restart.
Only forward authentication to a validated status URL under the configured endpoint.

After a lost response, reconcile before repeating a create. An unseen child number or
primary flag alone is insufficient under concurrent creation: require an unambiguous
parent relationship plus a matching baseline/snapshot or a server-supported correlation
key. Preserve local edits while adopting remote identity. An uncertain create with no
provable match stays blocked for reconciliation; absence alone is not proof it failed.
Use an approved external-reference field for correlation when available; a marker in
user-visible notes is a product tradeoff, not a universal integration requirement.

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
## Send Feedback

REST execute-action `onSuccess` confirms local queue acceptance, not server success.
Acknowledge it as a send request. Derive completion from a successful REST response
persisting the remote ID and `Remote: "remote"`. For a multi-document submission,
the parent may already be remote while a child or readback still failed: derive overall
completion from the whole submission, including required primary actions and readback.
Show a completion result/timestamp after retry instead of merely removing the failed
row. For live queue status, watch
`_commandQueue`, filter by provider, `type='function'`, function ID, and
the command's declared parent reference as well as `parameters.id` (a quote command's
`id` may identify the child). States are queued, processing, waiting,
and failed; a successful command is removed. Do not let obsolete failed commands
override a newer confirmed save with no current error. Never display raw payloads
or function execution contexts; they may contain authentication data.

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
