# Jigx Acumatica Recipe Index

| Task | Read |
| --- | --- |
| Build REST PUT/GET functions or payloads | `acumatica-rest.md` |
| Implement local-first sync or retry failed calls | `acumatica-sync.md` |
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

## Case-Sensitive Config

Use these exact fields everywhere:

- REST base URL: `acumaticaURL`
- OData base URL: `acumaticaOdataURL`

The names must match config data, datasource projections, function path parameters,
and action parameters.

