---
name: jigx
description: Use when building or modifying Jigx Core SDK apps, including screens, forms, datasources, tabs, navigation, local dynamic data, media, PDFs, JavaScript functions, validation, icons, and build/deploy workflows. This skill is generic and should be used before writing Jigx app code.
---

# Jigx Core SDK

Use this skill before writing or changing Jigx app code. Read only the reference files
needed for the task.

## Workflow

1. Identify the app area being changed: datasource, form, navigation, tab, media,
   PDF, JavaScript function, validation, or build/deploy.
2. Read `references/recipe-index.md`, then the specific reference file for that area.
3. Preserve local-first behavior: save user work locally first, then trigger remote or
   generated work from explicit actions.
4. Use stable local IDs for parent-child flows. Generate the parent ID before opening
   child screens and pass it through inputs.
5. Keep generated YAML and TypeScript aligned. If the SDK output is ambiguous, inspect
   the generated YAML before assuming runtime behavior.
6. When a new pattern is learned, update the relevant reference before or with the
   implementation.

## Reference Map

- `references/recipe-index.md` - when to read each reference.
- `references/app-architecture.md` - project layout, constants, build/deploy order.
- `references/datasources.md` - local tables, SQL, JSON extraction, search, refresh.
- `references/forms-state-navigation.md` - forms, save actions, dirty state, inputs,
  parent-child IDs, copy flows.
- `references/tabs-sections-lists.md` - tabbed workspaces, sections, list items,
  swipe actions, header actions, bottom actions.
- `references/media-pdf-javascript.md` - media fields, file paths, HTML/PDF generation,
  scripts, share/regenerate flows.
- `references/jsonata-validation-formatting.md` - JSONata, validation, formatting,
  dropdowns, icons, and field conventions.

## Guardrails

- Do not hardcode secrets, tokens, org IDs, or customer-specific credentials.
- Do not make external API calls from a primary save action unless the app explicitly
  uses a direct submit flow.
- Do not blur read refresh and writeback boundaries; offline-capable writes should
  drain from a visible user action.
- Do not store device-local file paths as if they are portable across devices.
- Do not use jig state as a substitute for stable screen inputs when saving parent or
  child records.
- Do not assume generated YAML matches intent. Inspect it when behavior is unclear.
