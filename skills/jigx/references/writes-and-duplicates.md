# Writes, Retries And Duplicate Prevention

A duplicate record is almost always one of four things: the same user action ran twice
(double tap, two entry points), the same request was sent twice (an automatic retry after
the server already did it), the same record got two identities (an id made per tap, or a
hash id that changes), or the runtime replayed a request after the app was killed. These
rules close all four. Queue structure and local-first saves are in
`forms-state-navigation.md`; ERP-specific queues are in the integration skill.

## Rules

- **One id per business record, created once** where the flow opens, and passed down as
  an input. An id generated inside a save, submit or queue action is new on every run.
  Derived rows use a deterministic id from the parent (`"line-" & $string(parentId) & "-" & n`),
  never `$uuid()`.
- **Never derive a key from `$random()` in an `initialValue`.** The field can re-evaluate
  on re-render or return, and the same cart is sent under a second reference.
- **The same id is the local row id, the queue row key and the server's idempotency key.**
  Generate it in the form the server stores it (case, separators) and compare in that form;
  a server that uppercases the key silently breaks a case-sensitive check.
- **Upsert local rows** (`ON CONFLICT(id) DO UPDATE`, `INSERT OR REPLACE`) so a repeat
  updates the same row.
- **Claim one queue row at a time** with a token, moving `notStarted` to `sending` in one
  `UPDATE … WHERE status = 'notStarted'`, and send only when that update changed a row.
  Rows still `sending` at app start are `unknown`.
- **Look the record up by its key before a create or a resend.** Create only when a
  successful lookup found nothing; a failed lookup is `unknown`, not missing. Treat a
  "not found" as fresh for a short window only.
- **Screens write local rows; one dispatcher sends.** User-facing screens do not call
  outbound writes directly in offline-capable flows.
- **A server absence never deletes local work** that is dirty, pending, failed, temporary
  or in flight.

## Retry policy for any function that creates or changes data

| Outcome | Meaning | Do |
| --- | --- | --- |
| 2xx | done | mark sent, store the server id |
| 429 | refused before processing | retry the same row after a pause |
| 400, 404, 422 | request is wrong | failed; show the user; retry after correction |
| 401 | not signed in | hold until a fresh sign-in, then rearm the same row |
| 500, 502, 503, 504, timeout, no response | **uncertain**, the server may have committed | `unknown`; look up by key; resend only if missing |
| unrecognised | | quarantine for a person; never guess |

In a write function's `error:` block keep `retry:` only on 429. Remove it from 5xx and
no-response entries and notify the user instead:

```yaml
error:
  - when: =@ctx.response.status = 429
    retry: { delay: 5000, maxRetries: 3 }
  - when: =@ctx.response.status >= 500 or @ctx.response.status < 200   # 5xx or no response
    title: Not confirmed
    description: We could not confirm this was saved. It will be checked before it is sent again.
    notification: true
```

Retrying on 5xx is safe only when the endpoint is idempotent by key (a repeated key returns
the first result). Retries start from an explicit action and reuse the same row and
identity; no timer-based retries. In one audit, 20 of 49 write functions retried on 500 or
504, including the order header and its lines.

## Double tap and concurrent runs

- A claimed queue row is the strongest guard: a second dispatcher finds nothing to claim.
- Without a queue, set an `isSubmitting` jig state key before the first call, disable the
  submit control with it, and reset it when the flow ends. Declare the key on the screen.
- Do not rely on navigation removing the button; a second tap lands first.

## The runtime replay problem

The runtime replays a REST command that was `processing` when the app was force-killed.
If the server had committed it, the replay creates a second record, and the solution cannot
tell. Leave a persisted `processing` command to the runtime; never turn it into a new
invocation. The protection has to be server-side: an idempotency key honoured across
replay, or a lookup before create.

## Read side: stable ids

A REST read whose output transform sets no `id` makes the runtime key rows by a hash
(log warning: "entity ID missing, falling back to hash"). When the record changes, the
hash changes and the old row can sit beside the new one. Map a stable business key, or a
composite, to `id` in every output transform:

```yaml
outputTransform: |
  { "items": $map(value, function($i) {
      $merge([$i, {"id": $string($i.AccountNumber) & '-' & $string($i.BranchCode)}])
  }) }
```

## What to ask the API owner for

- An idempotency key on every create, with a unique constraint; a repeat returns the
  first result.
- A lookup by that key.
- Child rows created in the same call as the parent, or keyed to it.
- Whether a 504 from their gateway means the record was created (check their logs for one).

Asking for these is a request to the API owner; do not patch around a production endpoint
in the app.

## Finding existing duplicates

```sql
SELECT json_extract(data, '$.Reference') AS ref, COUNT(*) AS n
FROM [orders] GROUP BY ref HAVING n > 1
```

Report them. Removing them is the record owner's decision.

## Test matrix: the server must end with exactly one record

| Test | How |
| --- | --- |
| Double tap | tap submit twice quickly |
| Slow success | server commits, then returns 504 or drops the response |
| Kill during send | force-quit after the server commits, relaunch, retry three times |
| Offline then online | submit offline, reconnect, drain, drain again |
| Two entry points | start the app and press refresh together |
| Lookup fails | block the lookup; the row stays `unknown` and is not created |
| Same data twice | two genuinely different records with equal details both exist |
