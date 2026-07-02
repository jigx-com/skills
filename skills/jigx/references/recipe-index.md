# Jigx Core SDK Recipe Index

Use this index to load the smallest reference needed for the task.

| Task | Read |
| --- | --- |
| Create or reorganize an app | `app-architecture.md` |
| Add local tables, SQL datasources, search, or refresh behavior | `datasources.md` |
| Build forms, save actions, queued local saves, dirty state, required fields, copy flows | `forms-state-navigation.md` |
| Add tabs, sections, list items, header actions, bottom actions, swipe actions | `tabs-sections-lists.md` |
| Add photos, signatures, local files, HTML/PDF generation, JS functions | `media-pdf-javascript.md` |
| Work with JSONata, validation, dropdowns, formatting, icons | `jsonata-validation-formatting.md` |

## Core Principles

- Save locally first. The app should be usable offline wherever the business flow
  allows it.
- Keep read refresh and outbound writeback as separate user-visible boundaries.
- Generate stable IDs at navigation boundaries and pass them as inputs.
- Use datasources as the source of truth for rendered lists.
- Include the entity being changed under the datasource `entities` list so refreshes
  happen after local saves.
- Keep user-visible completion state separate from sync or remote state.
- Use explicit actions for generated artifacts such as PDFs and remote submissions.
- After changing a recipe-sensitive pattern, inspect generated YAML.

## Skill Boundary

This skill is generic. It may mention "remote systems", but it must not encode
Acumatica endpoint rules, `.value` wrapper rules, `acuerp`, OData syntax, or Acumatica
file-upload paths. Use the `jigx-acumatica` skill for those.
