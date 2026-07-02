---
name: jigx-acumatica
description: Use when building Jigx apps that integrate with Acumatica ERP, including REST and OData functions, Acumatica config values, acuerp access tokens, .value payload wrappers, lookup sync, expand selection, local-first sync, command queue retry, file uploads, PDF attachment, and service order submission.
---

# Jigx Acumatica

Use this skill together with the generic `jigx` skill. Read the generic Jigx references
for screen, form, datasource, tab, media, and PDF patterns. Use this skill only for
Acumatica-specific integration rules.

## Workflow

1. Read `references/recipe-index.md`.
2. Confirm whether the task is inbound lookup sync, local-first outbound sync, direct
   submit, file upload, or retry/error handling. For any offline/batch create or
   update, read `references/acumatica-sync.md` before designing tables or actions.
3. Confirm REST versus OData. Prefer REST when possible.
4. Confirm required `$expand` values before implementing lookups that need nested data.
5. Keep Acumatica config casing exact: `acumaticaURL` and `acumaticaOdataURL`.
6. Use `accessToken` with type/value `acuerp`; do not hardcode access tokens.
7. Preserve local-first behavior unless the app explicitly uses direct submit.

## Reference Map

- `references/recipe-index.md` - when to read each Acumatica reference.
- `references/acumatica-rest.md` - REST functions, parameters, payloads, errors.
- `references/acumatica-sync.md` - local-first sync, Remote states, command queue,
  parent-child search/replace, retry.
- `references/acumatica-lookups-expands.md` - lookup sync, OData/REST differences,
  `$expand`, force sync, dropdown fields.
- `references/acumatica-files-submit.md` - file uploads, PDF attachments, service
  order submit patterns.

## Guardrails

- Never hardcode Acumatica access tokens.
- Never change `acumaticaURL` or `acumaticaOdataURL` casing.
- Never assume nested fields are present without `$expand`.
- Never send Jigx internal fields to Acumatica.
- Never build queued Acumatica payloads in the screen save action; pass identifiers
  and build the body from local tables when the queue drains.
- Never create a separate same-entity queue table for local-first create/update unless
  the business flow explicitly needs a different table. Use the entity's local table
  plus `Remote: "new" | "dirty" | "remote"` as the default.
- Never lose failed commands. Use command queue retry or explicit error records.
- Never overwrite local IDs in child records without replacing queued references when
  the queue still contains temporary IDs.
