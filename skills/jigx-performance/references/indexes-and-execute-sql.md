# Indexes And action.execute-sql

Prefer an index to a rewrite, and ship it so a failure cannot go unnoticed. `scripts/check-performance.mjs` flags predicates no shipped index matches, `execute-sql` actions with no `entities:`, and index DDL that names real tables.

## Indexes

- **Prefer an index to a rewrite.** An index cannot change a result set, so equivalence is structural
  rather than tested. Expression indexes took our worst query from 17 ms to 4 ms with no query change
  at all, for 82 KB of index and +0.29 ms per row written.
- **Match the predicate text exactly.** SQLite uses an expression index only when the indexed
  expression matches the query's. The table alias does not matter (`q.data` and `data` resolve to the
  same column); the function name, argument order, path string and any wrapping function do. Copy the
  predicate out of the datasource rather than retyping it.

  ```sql
  -- datasource: WHERE LOWER(COALESCE(json_extract(q.data, '$.status'), '')) = 'pending'
  CREATE INDEX IF NOT EXISTS idx_fileUploadQueue_status
  ON [fileUploadQueue] (LOWER(COALESCE(json_extract(data, '$.status'), '')))
  ```

- **Index the column the query reads.** An index on `json_extract(data, …)` does nothing for a filter
  on a `file` column.
- **An expression index must be deterministic.** SQLite rejects `CREATE INDEX` over a
  non-deterministic expression, so a column derived from `strftime('%s','now')` or similar (see
  `verifying.md`) cannot be indexed.
- **Don't wrap a predicate in a function.** `TRIM(COALESCE(json_extract(…'$.A'), json_extract(…'$.B')))`
  cannot use an index on either arm — 12 ms scan versus under 1 ms seek over 13,104 rows. Defensive
  `COALESCE` is not free.
- **Check the planner isn't already doing it.** Pushing a filter into each arm of a union, and fencing
  a multi-reference CTE with `LIMIT 2147483647`, are both things SQLite already does (the fence is a
  no-op on ≥ 3.35). Read the plan before writing the optimisation.

## `action.execute-sql`

- **`entities:` must be a non-empty array.** Omit it and the action fails on shape before a single
  statement runs.
- **Name a throwaway entity for DDL.** Listing the real tables invalidates every datasource bound to
  them and re-runs it (~6.6 s measured on device). An index changes no data, so use a table nothing
  binds to: `entities: [default/debug]`. This holds only while that table stays unbound: point any
  datasource at it and the trick silently becomes the ~6.6 s invalidation it exists to avoid.
- **Gate each `CREATE INDEX` on the table existing.** DYNAMIC (`default/*`) tables are created on
  first write. A step is one transaction, so one `no such table` on a fresh install takes every other
  index in that step down with it and raises error modals during the initial sync. Read
  `sqlite_master` into a datasource and gate on it:

  The `local-tables` datasource must read `sqlite_master` for the tables that exist on the device
  right now, and expose them under a `name` field, which is what the gate reads. It has to go to
  `sqlite_master` rather than `list_device_tables`, which omits the SDK system tables (see below).

  ```yaml
  when: ="default/growerVisitImages" in @ctx.datasources.local-tables.name
  ```

- **Keep new statements in a separate step** from proven ones, so a failure cannot drop indexes that
  already work.
- **DDL failure is silent.** When an index build called at the tail of a sync fails, the sync scope
  still reads `state='synced'`, `failed = 0`. The sync scope gates nothing that DDL does.
- **Verify in `sqlite_master`, and nowhere else.** Indexes persist across publishes, so a non-empty
  listing from an older build reads exactly like success:

  ```sql
  SELECT name, sql FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%';
  ```

  Remedy: either version the index name per build (`idx_<table>_<predicate>_v2`), so an old build's
  index cannot be mistaken for this build's, or `DROP INDEX IF EXISTS` immediately before the
  `CREATE` **in the same step**. Same step matters: a step is one transaction, so a drop that lands
  in an earlier step than its create leaves the table unindexed if the create step then fails.

- **`list_device_tables` omits the SDK system tables** (`_syncStatus`, `_syncScope`, `_fileStatus`,
  `_lastSync`). They exist and query fine. Use `sqlite_master` for inventory; `list_device_tables` is
  a liveness probe for when a query times out.
