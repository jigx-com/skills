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

Use a list or section header for adding items to that collection when the chosen UX
calls for it; reserve form Save for accepting the editing scope. Avoid adding multiple
Save/Sync/Refresh controls that perform overlapping work. Keep destructive actions
away from high-frequency primary actions when users can accidentally tap them.

Place actions according to what they affect: Add belongs with its collection, row
operations belong on the row, the current form's Save belongs in the bottom actions,
and record-level Price or Sync can live in that record's header. Keep read refresh on
the list and failed-write retry in a status or error view when those are separate
workflows. Choose placement from the screen's task rather than repeating the same
write action across scopes.

## Bottom Actions

Core SDK `screen.bottomPanel()` creates a new conditional action group on every
call. The runtime selects only the first group whose `when` is absent or true.
Create one panel per screen and append its actions with `panel.add`; do not create
one unconditional panel per action. Every Jigx, including one displayed as a modal
bottom sheet, can have multiple bottom actions. With `numberOfVisibleActions: 1`,
one action is visible and the others are in the `...` menu. With
`numberOfVisibleActions: 2`, two are visible and further actions are in that menu.
Put the primary action first and set the visible count according to which actions
need prominence.
Validate the generated screen's selected group's `children`, not merely the
presence of action labels anywhere in the output.

Name an editing action for its effect. An ambiguous `Close` button does not tell the
user whether changes are saved. If closing the screen already discards unaccepted
work, a single visible Save action can be enough. Where explicit Cancel is useful,
label it Cancel and choose whether it should be visible or in the overflow menu.

Use bottom actions for the main task on the current screen. For PDF tabs, useful bottom
actions are:

- Share
- Regenerate

Show user feedback after long-running actions, especially generation actions.

## Confirmations And Completion Feedback

For consequential submission, pricing, charging, conversion, deletion, or discard,
use a confirmation when the user needs to understand the effect before proceeding.
Name the target and describe what will be sent, changed, charged, deleted, or lost;
use a specific confirmation verb. Include material details such as the amount and
payment method for a charge. Run the action only after confirmation. A routine local
Save need not add a confirmation step.

If the action queues later work, acknowledge the request as queued or processing.
Show completion only after the required work is actually confirmed.

## Swipe Actions

Use swipe actions for row-level operations such as copy and delete. Confirm destructive
or duplicating actions when accidental use would be costly.
Give each swipe a visible label, a supported icon and contrasting foreground/background
colors; verify the rendered result in the target app, not just the configured icon name.
