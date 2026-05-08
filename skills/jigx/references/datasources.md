# Datasources

## Local Dynamic Tables

Use local dynamic data for draftable, offline-capable records. A typical table stores:

- `id` as the row key
- `data` as JSON
- business fields inside `data`
- user-visible status fields inside `data`
- remote/sync metadata only when the integration requires it

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

