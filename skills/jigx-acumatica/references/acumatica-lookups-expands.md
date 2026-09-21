# Acumatica Lookups And Expands

## REST Versus OData

Prefer REST endpoints when possible because responses are already shaped like Acumatica
entity JSON with `.value` wrappers.

Use OData when needed for reference tables or performance, but expect flat responses
and transform them into the local shape.

| Topic | REST | OData |
| --- | --- | --- |
| Config | `acumaticaURL` | `acumaticaOdataURL` |
| Auth | `accessToken` `acuerp` | app-specific OData auth |
| Nested data | `$expand` | usually flat |
| Dates | endpoint-specific | often needs timezone handling |

## `$expand`

Acumatica does not return nested child objects unless requested. If the UI needs nested
fields, identify likely expands before implementing.

Process:

1. Inspect the schema and UI fields.
2. List nested objects needed by the UI.
3. Propose a `$expand` list.
4. Confirm ambiguity with the human or schema.
5. Pass `$expand` from the action/function parameters, not hardcoded in the function.

Example:

```text
LocationContact
MainContact/Address
BillingContact/Address
ShippingContact/Address
```

When adding a new expand to an existing lookup, old cached rows may not update through
a diff sync if their `LastModifiedDateTime` did not change. Provide a force sync or
wipe/reload option for that lookup.

## Customer Locations

For customer locations, use the location endpoint with `LocationContact` expanded when
the service order needs location contact fields.

Prefer fields in this order:

- contact name: `LocationContact.FullName`, then `DisplayName`, then `Attention`
- phone: `LocationContact.Phone1`
- email: `LocationContact.Email`
- address: location address fields on the location row

When defaulting a newly selected customer, use its authoritative default location,
contact and address relationships, validated against account scope and active state.
Do not choose the first lookup row or infer a primary from its display name. On a
customer change, invalidate dependent selections and reapply eligible defaults; keep
related local documents aligned where the business relationship requires one account.
Show that account once or read-only on the child rather than allowing conflicting edits.

Cached inventory prices are estimates. Let Acumatica calculate contextual pricing and
discounts unless the user explicitly chooses a manual override, and read back the result.
Carry override flags when copying business values, but clear remote row identities.

## Dropdown Projections

Project Acumatica values into simple datasource fields:

```sql
SELECT
  id,
  json_extract(data, '$.CustomerID.value') AS CustomerID,
  json_extract(data, '$.CustomerName.value') AS CustomerName,
  data
FROM [Customers]
ORDER BY json_extract(data, '$.CustomerID.value')
```

Use the projected key field as the dropdown value:

```text
=@ctx.current.CustomerID
```

This lets Jigx match and display the selected value reliably.

## Sort Order

Sort Acumatica dropdowns by the key users recognize:

- customer by `CustomerID`
- location by `LocationID`
- project by `ProjectID`
- inventory by `InventoryID`

Add description fields where useful.


## Verify Contact IDs Against the Writable Endpoint

A GI can return plausible contact names with IDs that differ from REST Contacts.
When Opportunity rejects a selected ContactID, compare the GI record with REST
`Contact` and `BusinessAccount/{account}?$expand=Contacts` using the same login.
Do not remap draft choices by display name. Use authoritative numeric ContactID,
account, active state and person/contact type in the local lookup, and mark the
source so old cached GI rows cannot be submitted before the refresh completes.
REST contact collections return an array, unlike OData's `body.value`; use that
array for normalization and pagination. Preserve obsolete selections in drafts
and require explicit reselection rather than silently dropping the contact.
