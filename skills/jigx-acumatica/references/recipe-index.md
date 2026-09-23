# Jigx Acumatica Recipe Index

| Task | Read |
| --- | --- |
| Start a new integration, choose GI/OData versus REST, understand SOAP/UI probe boundaries, find the release, or read an Acumatica error | `acumatica-discovery.md` |
| Design a form, lookup, save, or action on a screen; reconcile with the customer's instance | `acumatica-screen-logic.md` |
| Feed a selector, find or prove a cascade, diagnose a rejected value, harden a write, handle time, duration, equipment, or workflow values | `acumatica-selectors-and-cascades.md` |
| Declare a list read: inquiry, delta sync, row identity, on-demand or parent-driven parameters, the row cap | `acumatica-reads-and-sync-contract.md` |
| Use contract REST, write detail lines, create a missing endpoint, attach a file to a record | `acumatica-endpoints-and-files.md` |
| Build REST PUT/GET functions or payloads | `acumatica-rest.md` |
| Implement local-first sync, offline batch create/update, queued writeback, dirty-aware refresh, `Remote` states, or retry failed calls | `acumatica-sync.md` |
| Sync lookup/reference data, choose REST/OData, use `$expand` | `acumatica-lookups-expands.md` |
| Upload files, attach PDFs, submit service orders | `acumatica-files-submit.md` |
| New integrations and app-equivalent live contract tests | `integration-verification.md` |

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

Use `jigx-acumatica-kb` to query the screen knowledge base. `acumatica-screen-logic.md`
explains what its answer means and how far to trust it; it does not repeat how to call it.

These rules are package-agnostic. They describe the reads, lookups, writes, and actions
a solution performs against Acumatica, whether those land in a data package or in the
solution's own functions.

For offline-capable Acumatica work, read `acumatica-sync.md` before designing local
tables, queue rows, refresh actions, or REST functions.

## Case-Sensitive Config

Use these exact fields everywhere:

- REST base URL: `acumaticaURL`
- OData base URL: `acumaticaOdataURL`

The names must match config data, datasource projections, function path parameters,
and action parameters.
