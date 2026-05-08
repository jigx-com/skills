# Tabs, Sections, Lists, And Actions

## Tabbed Workspaces

Use `jig.tab` for workspaces with distinct logical areas. Common tabs:

- Overview
- Details or line items
- Media
- PDF or documents
- Errors or retry status, when errors exist

Pass stable parent IDs to every tab that needs parent-scoped records.

## Conditional Tabs

If a runtime supports `when` on tabs, use it for tabs such as Errors. If not, keep the
tab last and render an empty state when no records exist.

## Sections

Use sections to group logical fields. Section headings should be specific and concise.
If supported, use section-level actions for "Add" behavior within a section.

## Cards And List Items

Use cards as containers for related controls when it improves scanability. For top-level
menus, prefer a real list driven by a static datasource over multiple standalone list
item components if individual list item rendering has spacing or duplication issues.

## Tags

Completion tags should reflect business completion, not just draft existence. For line
items with required dropdowns and generated codes:

- show `Completed` only when all required selections and resulting code exist
- show `Draft` or equivalent when fields are incomplete

Avoid repeating tags from list-item configuration. If tags duplicate, inspect the YAML
and simplify the tag source to one expression or one tag item.

## Header Actions

Use header actions for less frequent or risky actions such as reset, sync, force sync,
or retry. Keep destructive bottom actions away from high-frequency primary actions when
users can accidentally tap them.

## Bottom Actions

Use bottom actions for the main task on the current screen. For PDF tabs, useful bottom
actions are:

- Share
- Regenerate

Show user feedback after long-running actions, especially generation actions.

## Swipe Actions

Use swipe actions for row-level operations such as copy and delete. Confirm destructive
or duplicating actions when accidental use would be costly.

