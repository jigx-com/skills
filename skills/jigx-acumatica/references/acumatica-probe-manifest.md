# Probe manifest

One JSON file per solution. It lives in the solution repo at `docs/probe/<solution>.json`,
which stays local. `scripts/probe/manifest-from-solution.mjs` drafts it; a person finishes it.

```json
{
  "name": "field-sales",
  "base": "https://erp.example.com/AcumaticaTest/",
  "environment": "test",
  "companies": { "match": "north|south" },
  "interesting": "zone|route",
  "endpoints": [
    {
      "name": "JigxMobile",
      "version": "23.200.001",
      "entities": [
        { "name": "SalesOrder", "why": "orders", "read": "$top=1&$select=OrderNbr", "expect": ["CustomerID", "Details.ManualPrice"] },
        { "name": "Warehouse", "why": "warehouse list", "read": "$top=500", "sample": 3 },
        { "name": "RepAssignments", "why": "PUT-driven read", "read": null }
      ]
    }
  ],
  "locate": ["RepAssignments"],
  "inquiries": [
    { "name": "JIGXARCustomers", "why": "customers", "top": 1, "expect": ["CustomerClass"] },
    { "name": "JIGXARSalesPrice", "why": "customer prices", "top": 5, "sample": 5, "filter": "PriceType eq 'Customer'", "select": "PriceType,CustomerPriceClass" }
  ]
}
```

| Key | Meaning |
|---|---|
| `base` | The instance root, ending in `/`. `--base` overrides it. |
| `environment` | `"test"` runs straight away. Anything else makes the owner type the host name first. |
| `companies.match` | A case-insensitive regex over the companies on the login page. Use `companies.list` for exact names. `--companies` overrides both. |
| `interesting` | A regex. Matching custom fields on each entity, and matching inquiry names in the OData list, are called out in the report. |
| `endpoints[].entities[]` | Each one gets an `$adHocSchema` GET. `read` is the query string for one GET of rows; `null` means schema only, which is right for anything reached by PUT. `expect` lists field paths that must be in the schema (`Details.X` looks inside the detail rows). `sample` is the number of rows to copy into the report. |
| `locate` | Entity names to look for on every published endpoint. Use it when the endpoint is unknown, or failing somewhere the manifest does not name. |
| `tally` | On an entity or inquiry: the name of one column. The report gives the rows read and a count per value of that column, and copies no rows. Use it for "which values occur, and how often". A read that returns `$top` rows is flagged as possibly incomplete. |
| `inquiries[]` | Generic inquiries read over OData `odata/<company>/<name>` with `$top`, plus optional `filter` and `select` (OData v3 syntax). `expect` lists columns that must come back. `sample` works as for entities. The same inquiry may appear twice with different `why`, `filter` and `sample` values. |

Keep samples small (10 rows or fewer) and select only the columns the question needs. Prefer `tally` to `sample` when the question is about which values occur.
