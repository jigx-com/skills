# JSONata, Validation, Formatting, Dropdowns, And Icons

## JSONata

Keep expressions readable. **Use JSONata for simple logic and JavaScript only for logic
JSONata cannot express clearly.** Simple means a lookup, a label, a truthiness check, a
number or date format, a trim or split: write it with built-ins (`$boolean`,
`$formatNumber`, `$substringBefore`, an object lookup `{"A": "Active"}[$s]`), not a
script function. Keep JavaScript for HTML generation, multi-field parsing, loops over
records, error-document parsing and similar (`media-pdf-javascript.md`). Do not leave
`console.log` in an expression function: it writes to the device log on every call.
Check the execution boundary in `runtime-verification.md` before moving logic for REST
configuration.

### Values that make an expression throw

A failing expression logs `solution.expression … evaluate()` as an ERROR and returns
nothing, so the field, condition or action it drives silently breaks. Behaviour
checked with jsonata 1.8.9:

| Value | String functions (`$trim`, `$length`, `$substring*`, `$split`, `$replace`, `$contains`, `$uppercase`) | `$number` | `$toMillis` | Arithmetic and `>` `<` |
| --- | --- | --- | --- | --- |
| missing (undefined) | undefined, safe | undefined | undefined | undefined |
| `null` (a NULL SQL column) | **throws T0410** | **throws** | **throws** | **throws T2001/T2010** |
| number or object (a dropdown value) | **throws T0410** | number ok | **throws** | number ok |
| `""` (an empty text field) | ok | **throws D3030** | **throws D3110** | **throws** |
| `"12,5"`, `" 3 "` (typed text) | ok | **throws D3030** | | |
| `"2026-11-04 10:00:00"` (SQLite `datetime()`) | ok | | **throws D3110** (not ISO 8601) | |

Guards that work (each tested against undefined, `null`, a number, an object, `""`,
typed text and both date formats):

In a `.jigx` file a guard's `: ` (colon, space) ends a plain YAML value: `text: =v ? v : ''`
fails with "Nested mappings are not allowed in compact mappings". Write it as a block value,
or drop the space after the colon (`=v ? v :''`; JSONata does not need it):

```yaml
text: |
  =$type(@ctx.current.item.note) = 'string' ? $trim(@ctx.current.item.note) : ''
```

- a component value: `$type(v) = 'string' ? $trim(v) : ''`, or `v ? $trim($string(v)) : ''`
  (the second turns a number into its text)
- a SQL column: `COALESCE(json_extract(data, '$.x'), '')` in the query, or `v ? … : …`
  (`null` is falsy). `$exists(null)` is **true**, so `$exists(v) ? $trim(v) : ''` still
  throws on null
- a number typed by the user: `$type(v) = 'number' ? v : ($type(v) = 'string' and
  $match(v, /^-?\d+(\.\d+)?$/) ? $number(v) : 0)`. `$match` alone throws on `null` and
  on numbers
- a date for `$toMillis`: emit ISO 8601 from SQL (`strftime('%Y-%m-%dT%H:%M:%SZ', …)`),
  or pass the picture of the stored format, `$toMillis(v, '[Y0001]-[M01]-[D01]
  [H01]:[m01]:[s01]')` for SQLite's `datetime()`. With a picture, text in another format
  returns undefined instead of throwing, so the picture must match the data
- the "no value" branch of a guard whose result feeds another function: return the empty
  sequence `()`, not `null`. `$fromMillis(v ? $toMillis(v) : null)` still throws T0410 when
  `v` is empty, because `$fromMillis(null)` throws; `$fromMillis(v ? $toMillis(v) :())`
  returns undefined (checked on jsonata 1.8.9 in a review and on 2.2.2). The same holds
  for any guard nested inside `$string`, `$number`, arithmetic or another strict call

`$string(null)` is the text `"null"`, so wrapping in `$string` alone shows "null" to the
user.

Common rules:

- prefer explicit null checks for required fields
- keep case consistent in JSON paths
- avoid relying on jig state for persisted business values when component state or
  screen inputs are the source of truth
- use projected datasource fields for dropdown `value`

## Null-Safe Expressions

String and number functions throw on a missing value. Left-joined columns and optional
fields are missing often; in one audit a single `$contains` on a left-joined status in a
list item produced hundreds of runtime errors. Guard by type and choose a fallback by
meaning:

```yaml
when: =$contains(($type(@ctx.current.item.status)='string'?@ctx.current.item.status:''), 'Open')
text: =$type(@ctx.current.item.Amount)='number' ? $formatNumber(@ctx.current.item.Amount,'#,##0.00') : '-'
```

Show "Unknown" or "-" rather than a blank. Apply the same to `$substring`, `$lowercase`,
`$uppercase`, `$trim`, `$number`, `$round` and `$length`.

## Dropdowns

Dropdowns should:

- have stable `value` fields selected by the datasource SQL
- display a title users recognize
- include description/subtitle fields when they help selection
- sort by key/value first, then description
- use search for large lists

When binding dependent fields to a selected dropdown row, prefer direct selected data:

```text
=@ctx.components.InventoryID.state.selected.UnitPrice
```

Use the exact shape returned by the datasource. If SQL selected `UnitPrice`, do not
bind to `selected.data.UnitPrice.value` unless that is actually the returned shape.

## Validation

Separate:

- field-level required state
- form validity
- business readiness
- sync/submission readiness

Do not use hidden required fields that make a save button disabled without explaining
the missing requirement to the user.

For optional boolean controls, explicitly set `isRequired: false`: an unchecked
manual-price/discount choice is valid. Do not make optional notes required because a
separate description is required. Display prices from the selected datasource's actual
shape, but distinguish cached estimates from server-confirmed pricing.

## Formatting

Use native formatting configurations where available, for example currency formatting
on entity fields. If a numeric value is missing and the UI should show money, default
to zero instead of showing "not set".

## Icons

Use the SDK icon search MCP when an icon name is uncertain. Broken icons should be
treated as defects. Prefer explicit, known SDK icon names over guessed names.
