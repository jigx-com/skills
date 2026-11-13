# Measuring

Measure before changing anything. Query plans and equivalence diffs here were run on host `sqlite3`; millisecond timings are indicative of relative cost, not device figures. Two numbers are device measurements: the 8 s `Timeout for first snapshot` threshold and the ~6.6 s datasource invalidation. A fast dev device with a tenth of the data will not reproduce a user's timeout.

Use `scripts/profile-query.py` to get counts, bytes, the plan and timings from an exported database in one run.

## Measuring

- **Attribute cost per CTE and per predicate before changing anything.** Query size is not cost. A
  23-way union with 45 `json_extract` calls can be free while one predicate is the entire runtime.
- **Read query plans on a host `sqlite3`** of the same version line as the device. `EXPLAIN QUERY
  PLAN` and DDL are both rejected by the on-device SQL parser.
- **Never time a query through the debug bridge.** A `query_device_db` round trip (`jigx_debug` MCP)
  is ~5 s host to device and back, whatever the query. Export the tables and time them on the host.
- **Record the machine next to every timing.** Host and device numbers stop being comparable the
  moment you write them down without a label, and a week later nobody can tell them apart.
- **`query_device_db` caps at 20 rows; `save_query_result` does not.**
- **Count bytes, not just rows** (`LENGTH(col)`) — a JSON column may carry a blob far larger than the
  field you filter on.
- **Measure at 1x and 10x.** Cost that is linear in row count is what breaks the user with three
  times your data.
- **Confirm the path is reachable before optimising it.** Code-verified is not reachability-verified:
  tables behind dead features hold 0 rows.

## Where the time goes

**When a JSON column carries a blob (a base64 thumbnail or similar), filtering it costs page reads,
not JSON parsing.** Every row is read in full to reach an 8-byte `status`.

On such a column this rules out the whole family of "parse less" rewrites: `->>` instead of
`json_extract`, one extract instead of two, a `LIKE '%"PENDING"%'` prefilter. Measured on a
blob-carrying column, the no-parse `LIKE` was the *slowest* of them. The only lever is not reading
the rows: index the predicate, or filter earlier.

On a narrow JSON column with no blob this finding does not apply, and the parse-less rewrites are
worth measuring rather than ruling out.
