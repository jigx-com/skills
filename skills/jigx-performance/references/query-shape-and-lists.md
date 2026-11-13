# Query Shape And Lists

Lists are where per-row costs multiply. `scripts/check-performance.mjs` flags datasource reads inside list items, uncapped search lists, and `!=` comparisons against datasource fields.

## Query shape

**Cap what a list can return, and pin the current selection.** An unbounded `LIKE '%…%'` over
thousands of rows, sorted and rendered in full on open, will stall — but a naive `LIMIT` breaks any
screen that resumes a selection outside the first page. Union a pinned arm with a capped arm (sketch;
column lists and sort key elided):

```sql
SELECT * FROM (
  -- pinned: the current selection, outside the search and status filters
  SELECT … FROM [customers] AS jig
  WHERE @selectedId IS NOT NULL AND json_extract(jig.data,'$.CustomerID.value') = @selectedId
  UNION ALL
  SELECT * FROM (
    SELECT … FROM [customers] AS jig
    WHERE (… LIKE '%' || @search || '%' OR @search IS NULL)
      AND json_extract(jig.data,'$.Status.value') = 'Active'
      AND (@selectedId IS NULL OR json_extract(jig.data,'$.CustomerID.value') != @selectedId)
    ORDER BY <sort key> COLLATE NOCASE ASC
    LIMIT 100
  )
)
ORDER BY <sort key> COLLATE NOCASE ASC
```

The inner `ORDER BY` + `LIMIT` caps the scan and the sort; the outer `ORDER BY` restores the pinned
row's position; the `!=` stops it appearing twice. Search still reaches every row in the table.

- **Normalise the sort key** (`REPLACE(REPLACE(…TRIM(name)…))`) so double-spaced ERP values interleave
  instead of clumping.
- **When wrapping a `GROUP BY` for ordering, put the `ORDER BY` in the outer SELECT.** Ordering on a
  raw CTE column picks an arbitrary row per group.
- **Shape data in SQL, not in expressions (DS-04).** SQL runs once per datasource change; JSONata
  re-evaluates on every render that touches it, including list scrolls. Filter, count, sum, join,
  sort and derive per-row columns in the query; expressions only decide what to show.
- **Precompute heavy joins.** A read-path query with four or more joins over JSON columns is a
  candidate for a view table built once with `execute-sql` during sync. Every bound parameter must
  be a scalar; join-heavy queries also produced intermittent `row value misused` errors on device.
- **Write `json_extract` in full, then set `rewriteQuery: false` (DS-03).** The rewriter turns the
  `'$.field'` shorthand into `json_extract(data, '$.field')`. Verified on a device: with
  `rewriteQuery: false`, `SELECT '$.name' AS name` returns the literal text `$.name`. Never set the
  option on a query that still uses the shorthand anywhere, select list or `WHERE`: convert every
  reference, set it, retest. The rewrite cost is paid on the first run and cached, so this is a
  small gain; do it after the items that change how much work SQLite does.

## List components

- **A `jig.list` datasource returns a unique `id` (DS-02).** The `id` is the item's instance id;
  without it the runtime hashes every row object on every render, and duplicates collide item state.
- **`jig.list` for data-driven lists (CO-02).** `component.list` mounts every row at once;
  `jig.list` virtualises. Use it for any datasource list that can exceed about ten rows. A
  `maximumItemsToRender` cap hides rows; pair any cap with search.
- **Never set `instanceId` on list items (CO-04).** The list manages it from the `id`.
- **No custom components in list items (CO-01).** Each primitive in a custom component adds its own
  renderer wrappers, multiplied per row; long lists of them fail to render, especially on Android.
  Use built-in items and request a missing component.
- **Prefer fixed-height items (CO-03).** `hasDynamicHeight` measures every item.

## Datasource reads in lists

**A `@ctx.datasources.X` read inside a `component.list-item` property registers one subscription per
rendered row.** Twenty rows whose icon, colour, tags and label each read a view is dozens of live
subscriptions from one render; we observed 36 concurrent copies of a single view on device. Against
an indexed table that is survivable; against an unindexed one the per-row subscription cost reaches
the timeout.

Resolve the views **once** in an `expressions:` entry that builds the rows, feed `data:` from it, and
have every list-item property read `@ctx.current.item.*`.

- **Key a large view into a map rather than scanning it per row.** A `view[id = $f.id]` predicate
  inside a `$map` is an O(n²) walk. Build the lookup once:

  ```
  $byId := $merge($append([{}], $map($view, function($s) { { $string($s.formId): $s } })));
  ```

  `$append([{}], …)` is load-bearing: `$map` over a single row returns an object, not a one-element
  array, and `$merge` needs the array. Small views are fine as flat id arrays.

- **Fence `data:` with `[ ]`.** A predicate matching exactly one row collapses to a bare object, and
  `component.list` requires an array.
- **`$string()` both sides of every id comparison.**
- **Reactivity survives the hoist.** A `data:` fed from an `expressions:` entry still re-renders when
  a view named only inside that expression changes.
- **The same applies to handlers.** An `onChange` running eleven datasource reads per sync-status
  change is the same defect elsewhere; gate it on one cheap "is there work" view.

## SQL → JSONata

- **`CAST(col AS INTEGER)` any numeric column feeding a `when:` guard.** Columns without a type
  affinity come back as text, and in JSONata `'0' = 0` is **false** — the guard evaluates false
  forever with no error, no log line and nothing on screen. Cast at the source; one cast fixes every
  consumer.
- **`$number(x)` before comparing anything out of SQLite.** JSONata throws on a text-vs-number
  comparison, and a throw inside a list's `data:` builder blanks the whole list, not one row.
- **A missing table resolves the datasource to null**, and `$count(…) > 0` reads null as "no work".
  Split gates by table family so one missing table nulls only its own arm.
- **`$count(ds[field='X']) = 0`, not `ds.field != 'X'`.** Comparison against undefined is false, so the
  `!=` form silently blocks the no-row case.
