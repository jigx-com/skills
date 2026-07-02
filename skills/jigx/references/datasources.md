# Datasources

## Local Dynamic Tables

Use local dynamic data for draftable, offline-capable records. A typical table stores:

- `id` as the row key
- `data` as JSON
- business fields inside `data`
- user-visible status fields inside `data`
- remote/sync metadata only when the integration requires it

## Local-First Transactional Tables

For offline-capable transactional work, use the same local table for inbound data and
local edits. Avoid switching between a local table and `default/<table>` for the same
transactional entity.

- Write user edits to the app's local entity table first.
- Keep Dynamic Data `default/<table>` names for surfaces that intentionally use the
  Dynamic Data provider.
- Add user-facing state such as draft/complete/error separately from sync state.
- Add sync metadata only when the integration needs it, for example `LocalDirty`,
  `SyncStatus`, `Remote`, timestamps, and last error fields.

Do not store remote request bodies in UI state or screen snapshots. Save normalized
local rows and let the outbound sync action re-query the latest local data.

## Select Shape

For list and dropdown datasources, project fields out of JSON so components can bind
to simple fields:

```sql
SELECT
  id,
  json_extract(data, '$.name') AS name,
  json_extract(data, '$.status') AS status,
  data
FROM [records]
ORDER BY json_extract(data, '$.name')
```

Use the projected field as the dropdown `value` when Jigx needs to match selected
items. If the value only exists inside raw JSON and is not selected as a field, selected
values may not display correctly.

## Search

Search dropdowns use the same pattern as lists:

```sql
SELECT id, json_extract(data, '$.name') AS name, data
FROM [records]
WHERE @searchText IS NULL
   OR json_extract(data, '$.name') LIKE '%' || @searchText || '%'
ORDER BY json_extract(data, '$.name')
```

Sort dropdowns by the key users recognize, then by description when useful.

## Refresh After Save

If a datasource backs a list or gallery that should refresh after a save, include the
saved table under its `entities` configuration. This prevents timing issues where a
record is saved but not visible until a full screen reload.

## Refresh Boundaries

Keep read refresh and writeback separate.

- Screen `onLoad`/`onFocus` may run small read checks only when the app intentionally
  shows "new data available" indicators.
- Pull-to-refresh or a refresh button may fetch latest read data, but should not drain
  outbound write queues unless the product explicitly defines that refresh as a submit
  action.
- Primary save actions should not make remote writes in offline-capable flows.
- A visible explicit action such as Sync, Submit, or Send should be the boundary that
  starts outbound writes.

When a remote read refresh can touch data the user edits locally, protect local rows:

```sql
WHERE COALESCE(json_extract(data, '$.LocalDirty'), 0) != 1
  AND COALESCE(json_extract(data, '$.SyncStatus'), '') NOT IN ('pending', 'failed')
  AND COALESCE(json_extract(data, '$.data_location'), '') NOT IN ('local', 'deleted')
```

If a remote change exists but local dirty rows block refresh, keep the change visible
to the user. Do not silently hide the indicator just because the safe refresh set is
empty.

## Parent-Child Queries

Use the parent ID passed through inputs:

```sql
SELECT id, data
FROM [childRecords]
WHERE json_extract(data, '$.parentId') = @parentId
ORDER BY json_extract(data, '$.createdAt')
```

Do not derive parent IDs from temporary jig state. Treat screen inputs as the contract.

## File Paths

Device-local files are not portable across devices. Always guard preview and share
flows against missing files. A file path saved on iOS may not exist on Android, and a
file path saved before reinstall may no longer exist.
