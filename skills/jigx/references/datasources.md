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

## Parent-Child Queries

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
