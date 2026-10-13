# Verifying A Change

Ship one change at a time and prove it with evidence a reviewer can check.

## Verifying a change

- **Assert a non-zero row count on both sides first.** An equivalence check that passes on empty
  output proves nothing.
- **Bound time-dependent columns.** A column derived from `strftime('%s','now')` makes two runs of the
  same query disagree, so a raw `sqlite3 -csv | diff` reports spurious failures. Diff the stable
  columns strictly.
- **Diff against real data, not a fixture alone.** Fixtures miss real nulls — in our case 17% of one
  table carried a NULL status that had to keep rendering as synced.
- **Replay the datasource's real query through `query_device_db`.** Strip the `--` comment lines; they
  are inert. This validates a whole datasource — including that the entity names bind — without
  publishing, tapping or writing anything.
- **Host SQLite cannot catch an action-schema bug.** `sqlite3` validates SQL; it says nothing about an
  `execute-sql` that ran zero statements because `entities:` was missing.
- **Say what was not reproduced.** A fast dev device with a tenth of the data will not reproduce a
  user's timeout; a query plan is honest evidence where a device timing is not.

## Checklist

1. Count rows **and bytes** on a real device. Confirm the path is reachable.
2. Rebuild on a host copy at 1x and 10x; read the query plan there.
3. Attribute cost per CTE and per predicate before proposing anything.
4. Prefer an index. If a rewrite is unavoidable, diff it against the current query on a fixture and on
   real data, with non-zero row counts asserted both sides.
5. Ship one change at a time. Verify the index by name and expression in `sqlite_master`, the query's
   output before and after, and a live write through the indexed table.
