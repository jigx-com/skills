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
