# Datasources

## Local Tables And Storage Ownership

Choose device-local or cloud-shared storage by ownership; offline capability alone
does not mean a draft belongs in a cloud-synchronized Dynamic Data table. Keep temporary
editing copies device-local. The Acumatica skill documents the provider/table naming
contract for externally owned ERP records. A typical table stores:

- `id` as the row key
- `data` as JSON
- business fields inside `data`
- user-visible status fields inside `data`
- remote/sync metadata only when the integration requires it

## Dynamic Data Provider And Offline Prefetch

A SQLite datasource that reads Jigx Dynamic Data must use the dynamic provider and
declare each `default/<table>` it queries as an entity. A local-provider datasource
reads device-local tables; naming a Dynamic Data table in its SQL does not make that
table synchronize. Keep externally owned transactional drafts in their chosen local
tables rather than moving them to Dynamic Data merely to support offline use.

A screen datasource can trigger Dynamic Data synchronization when first used; a global
datasource can bring its declared tables in on solution load. For small shared settings
that must be present before the device goes offline, prefetch the required entities
with a Dynamic Data `sync-entities` action at a suitable entry point, such as the index
`onLoad`. Do not assume that `onLoad` runs again for an existing installation after a
solution update: give newly introduced required tables an on-demand or version-aware
recovery path. Verify the generated provider and entity names, then test first load,
update, relaunch, and offline use on the target app.

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

## Binding And Dropdown Values

Screen SQLite query parameters use `@name`, with the corresponding name in
`queryParameters`. A SQL statement using `$name` may prepare successfully in SQLite
while failing the mobile binding contract; test binding through the generated
screen datasource, not just SQLite syntax.

For a dropdown, project the selection key as a top-level `value` with the same
primitive type as `initialValue`. Bind its item value to `@ctx.current.item.value`;
titles may still use `@ctx.current.item.data.Description`. Verify selection survives
closing/reopening and the dependent query receives the selected key.

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

- Screen `onLoad` may prefetch bounded Dynamic Data needed for offline use.
- Screen `onFocus` may run small read checks when the app intentionally shows
  "new data available" indicators.
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

Project only the fields used by a list and load full records in their detail scope.
Function queries should read only dependencies used by that function; loading a
whole document cache into unrelated lookups increases parsing, rendering and debug
serialization work even when the HTTP request itself is small.

Use the parent ID passed through inputs:

```sql
SELECT id, data
FROM [childRecords]
WHERE json_extract(data, '$.parentId') = @parentId
ORDER BY json_extract(data, '$.createdAt')
```

Do not derive parent IDs from temporary jig state. Treat screen inputs as the contract.

## Replacing child partitions

`forRowsWithValues` is scalar equality, not an IN-list. An array of parent IDs is
passed to SQLite as one bound value and can fail on device. For several confirmed
parents, use scoped SQL with a JSON **string** parameter, for example:

```sql
DELETE FROM [children]
WHERE json_extract(data, '$.parentId') IN
  (SELECT value FROM json_each(@parentIds) WHERE type = 'text')
```

Then insert the mapped remote rows. Validate the complete response and mapping before
replacement; an interrupted replacement must be safely repeatable. Include only
parents whose local changes can be replaced, never unsent/dirty/deleted drafts or
unrelated partitions. Test zero, one and multiple IDs, empty remote child collections,
and retry using actual bound SQLite parameters. Do not teach a test harness to expand
arrays when the mobile runtime does not.

## File Paths

Device-local files are not portable across devices. Always guard preview and share
flows against missing files. A file path saved on iOS may not exist on Android, and a
file path saved before reinstall may no longer exist.
