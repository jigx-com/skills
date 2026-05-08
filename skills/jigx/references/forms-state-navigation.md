# Forms, State, And Navigation

## Local-First Saves

Primary form saves should usually:

1. Snapshot form/component values.
2. Validate required business fields.
3. Save one row to the local dynamic table.
4. Set user-visible status or progress.
5. Disable discard warning.
6. Navigate back or refresh the current workspace.

Do not call a remote API from the primary save action unless the app intentionally
uses a direct submit flow.

## Dirty State And Discard Warning

Use the standard jig state action pattern:

- set dirty state when a field changes
- enable discard warning while dirty
- clear dirty state after successful save
- disable discard warning before navigating away after save

Do not leave save actions that navigate away while the discard warning remains active.

## Inputs, Not Jig State, For IDs

For parent-child flows:

1. Generate the parent ID before opening the parent workspace.
2. Save the parent row with that ID.
3. Pass the parent ID to child tabs and child create/edit screens as an input.
4. Generate child IDs before navigating to child create screens.
5. Save child rows with both `id` and `parentId`.

This avoids losing parent IDs when a component `onChange` action changes jig state.

## Create Flow

```text
list -> new parent action creates parentId -> parent workspace
parent workspace -> child tab receives parentId
child tab -> add action creates childId -> child editor
child editor -> save with childId + parentId -> return to parent workspace
```

## Copy Flow

When copying a parent record:

- generate a new parent ID
- set copied date fields to the copy date when business rules require it
- copy child rows with new child IDs and the new parent ID
- do not copy signatures, progress rows, submission rows, or remote sync records unless
  explicitly required

When copying a line item:

- offer copy first, copy last, or start clean when that improves user flow
- generate a new item ID before opening the editor
- copy business fields only

## Action Data Shape

`action.execute-entity` expects `data` to be an object. `action.execute-entities`
expects an object or array of objects. If runtime says `data must be an object`, inspect
generated YAML and confirm expressions resolve to an object, not a string or undefined.

