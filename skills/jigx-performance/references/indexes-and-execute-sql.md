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
- **An `OR` across several keys scans unless every arm has a matching index.** SQLite can
  answer `a = ? OR b = ?` with one index per arm (`MULTI-INDEX OR` in the plan), but one
  unindexed or non-matching arm turns the whole lookup into a scan. A header looked up by
  any of four ids, once per queued change, read every header row per change: seconds per
  refresh at ~2,000 headers. **First index every arm's exact expression and keep the
  query**: read the plan, and `MULTI-INDEX OR` with one `SEARCH` per arm is the fix (one
  review went 3.1 ms → 0 ms at 10x data with the query unchanged; a UNION rewrite there
  was verified on 4,128 cases and then reverted as unnecessary). Only when the plan still
  scans (an arm the planner cannot match to an index), split it into one branch per key
  and index each branch's exact expression:

  ```sql
  SELECT h.id FROM [header] AS h WHERE trim(COALESCE(h.id, '')) = @key
  UNION
  SELECT h.id FROM [header] AS h WHERE trim(json_extract(h.data, '$.Nbr')) = @key
  -- … one branch per key; UNION, not UNION ALL, when one row can match two keys
  ```

  Check the branches return the same rows as the `OR` on real data, including null and
  empty keys. When the same multi-key lookup appears in several queries, build a small
  key table (`header id, key`) during sync instead, so each lookup is one indexed
  equality and the logic lives in one place.
- **Materialise a CTE that must be computed once.** Since 3.35 a CTE referenced more than
  once is materialised by default; one referenced once may be flattened into a correlated
  part of the outer query and re-parse its JSON per row. `WITH keys AS MATERIALIZED (…)`
  makes it explicit. It needs SQLite ≥ 3.35 on the device; check the plan shows
  `MATERIALIZE keys`.
- **One index per expression.** Two indexes on the same field with slightly different
  wrapping (`json_extract(…)` and `trim(json_extract(…))`) both cost writes; usually only
  one matches the queries. Drop the one no query uses.
- **Guard the plans that matter in CI.** `scripts/check-plans.py` builds the shipped
  indexes on stub tables and runs `EXPLAIN QUERY PLAN` on every datasource and
  `execute-sql` read. A guard file fails the build when a per-row or per-change lookup
  starts scanning, or stops using its index, after someone edits the query text:

  ```json
  {"changed-orders": {"no_scan": ["orders"], "uses": ["idx_orders_ref"]}}
  ```

- **Audit indexes no query uses.** The same report lists shipped indexes the planner
  never picks. It runs on empty tables without `ANALYZE`, so treat the list as candidates:
  confirm with `--schema-from` an exported database and the device's real query before
  dropping one. Every unused index still costs every write.

### Joins through a CTE column use no index

A CTE that computes columns from one table (`locations_cte AS (SELECT
json_extract(data, '$.BAccountID') AS BAccountID, … FROM locations)`) and is then joined
on those columns gets no index lookup, even with an expression index that matches
character for character: the plan says `SCAN locations USING INDEX …` (every row, for
every outer row), not `SEARCH`. Join the table itself and write the expressions in the
`ON` clause:

```sql
LEFT JOIN locations AS loc
  ON json_extract(loc.data, '$.BAccountID') = json_extract(cust.data, '$.AccountID')
  AND trim(json_extract(loc.data, '$.LocationCD')) = trim(json_extract(opp.data, '$.Location.value'))
```

Measured on an opportunity list: the plan went from two `SCAN`s per opportunity to two
`SEARCH`es, 2.1 ms to 0.93 ms on 200 locations, identical results; the original grows
with opportunities × (customers + locations). A CTE with `DISTINCT`, `GROUP BY` or a
join is built once with its own automatic index and is not affected. The audit reports
these as `cte-join-no-index`.

## `action.execute-sql`

- **`entities:` must be a non-empty array.** Omit it and the action fails on shape before a single
  statement runs.
- **Name a throwaway entity for DDL.** Listing the real tables invalidates every datasource bound to
  them and re-runs it (~6.6 s measured on device). An index changes no data, so use a table nothing
  binds to: `entities: [default/debug]`. This holds only while that table stays unbound: point any
  datasource at it and the trick silently becomes the ~6.6 s invalidation it exists to avoid.
- **Listing a real table in `entities:` also creates it when it is missing.** An existing index step
  that names its tables may depend on that: swap the list for a throwaway entity and the first
  `CREATE INDEX` on a table the user has no rows for fails with `no such table`, and every later
  statement in the step with it (measured: 11 of 55 indexes created, an error modal at the end of
  the initial sync). Before swapping, gate every statement on its table existing (below), or keep
  the real entity for tables that may not exist yet.
- **Gate each `CREATE INDEX` on the table existing.** DYNAMIC (`default/*`) tables are created on
  first write. A step is one transaction, so one `no such table` on a fresh install takes every other
  index in that step down with it and raises error modals during the initial sync. Read
  `sqlite_master` into a datasource and gate on it:

  The `local-tables` datasource must read `sqlite_master` for the tables that exist on the device
  right now, and expose them under a `name` field, which is what the gate reads. It has to go to
  `sqlite_master` rather than `list_device_tables`, which omits the SDK system tables (see below).

  List the tables the index step indexes as its `entities`: then it re-reads when a sync
  creates one, and the gate sees the new table without a restart.

  ```yaml
  # datasources/local-tables.jigx
  type: datasource.sqlite
  options:
    provider: DATA_PROVIDER_LOCAL
    entities:              # the indexed tables: re-read when a sync creates one
      - default/orders
      - default/orderLines
    query: |
      SELECT name AS id, name FROM sqlite_master WHERE type = 'table'
  ```

  ```yaml
  # one gated step per table, in the index action
  - type: action.execute-sql
    when: ="default/orders" in @ctx.datasources.local-tables.name
    options:
      entities: [default/debug]   # a table nothing binds to: no datasource re-runs
      statements:
        - statement: |
            CREATE INDEX IF NOT EXISTS idx_orders_nbr
            ON [default/orders] (json_extract(data, '$.OrderNbr'))
  ```

  `check-plans.py` and the audit skip `sqlite_master` and other `sqlite_*` tables.

- **Keep new statements in a separate step** from proven ones, so a failure cannot drop indexes that
  already work.
- **Keep table creation out of the index step.** A `CREATE TABLE` must name its real entity so
  bound datasources refresh; an index step must not. Put each in its own action.
- **Run index DDL once per schema change, not on every sign-in or user change.** It belongs after
  the tables it indexes exist, outside the path the user waits on.
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
