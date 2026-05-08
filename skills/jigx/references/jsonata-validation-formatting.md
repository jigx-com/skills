# JSONata, Validation, Formatting, Dropdowns, And Icons

## JSONata

Keep expressions readable. If an expression becomes hard to inspect, move logic into a
JavaScript function.

Common rules:

- prefer explicit null checks for required fields
- keep case consistent in JSON paths
- avoid relying on jig state for persisted business values when component state or
  screen inputs are the source of truth
- use projected datasource fields for dropdown `value`

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

## Formatting

Use native formatting configurations where available, for example currency formatting
on entity fields. If a numeric value is missing and the UI should show money, default
to zero instead of showing "not set".

## Icons

Use the SDK icon search MCP when an icon name is uncertain. Broken icons should be
treated as defects. Prefer explicit, known SDK icon names over guessed names.

