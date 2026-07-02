# Jigx Acumatica Recipe Index

| Task | Read |
| --- | --- |
| Build REST PUT/GET functions or payloads | `acumatica-rest.md` |
| Implement local-first sync, offline batch create/update, queued writeback, dirty-aware refresh, `Remote` states, or retry failed calls | `acumatica-sync.md` |
| Sync lookup/reference data, choose REST/OData, use `$expand` | `acumatica-lookups-expands.md` |
| Upload files, attach PDFs, submit service orders | `acumatica-files-submit.md` |

## Generic Dependency

Use the generic `jigx` skill for:

- forms
- datasources
- tabs
- navigation
- media capture
- PDF generation
- local parent-child IDs
- UI actions

This skill only adds Acumatica-specific integration rules.

For offline-capable Acumatica work, read `acumatica-sync.md` before designing local
tables, queue rows, refresh actions, or REST functions.

## Case-Sensitive Config

Use these exact fields everywhere:

- REST base URL: `acumaticaURL`
- OData base URL: `acumaticaOdataURL`

The names must match config data, datasource projections, function path parameters,
and action parameters.
