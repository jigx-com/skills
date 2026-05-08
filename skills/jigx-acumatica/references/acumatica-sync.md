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

## Remote Field

Use `Remote` only for sync tracking:

- `new` - created locally and never synced
- `dirty` - previously synced but edited locally
- `remote` - in sync with Acumatica

Do not overload `Remote` to mean "draft" or "not complete". Use separate business
state fields for draft/readiness.

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

## Command Queue Retry

Failed REST calls remain in the command queue as failed commands. A retry action should
requeue failed commands for the current business record in command order.

Do not ask users to select arbitrary individual failed calls unless the business flow
can safely retry them out of order. Usually, retry all failed calls for the record in
queue order.

When starting a full send cycle again, clear or supersede old visible error records so
the user sees the current attempt.

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

