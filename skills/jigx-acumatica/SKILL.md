---
name: jigx-acumatica
description: Use when building Jigx apps that integrate with Acumatica ERP, including probing an instance and choosing between generic inquiries, the screen API and contract REST, reading a screen's built-in logic and reconciling it with the customer's instance, selector and cascade rules, on-demand and delta reads, custom endpoint creation, REST and OData functions, Acumatica config values, acuerp access tokens, .value payload wrappers, lookup sync, expand selection, local-first sync, command queue retry, file uploads, PDF attachment, and service order submission.
---

# Jigx Acumatica

Use this skill together with the generic `jigx` skill. Read the generic Jigx references
for screen, form, datasource, tab, media, and PDF patterns. Use this skill only for
Acumatica-specific integration rules.

## Workflow

1. Read `references/recipe-index.md`.
2. For a new integration, read `references/acumatica-discovery.md` first: find the
   customer's release from the instance build, pick the channel by job (inquiry for
   lists, screen API for records and actions, contract REST as the fallback), and
   keep every session bounded by a logout.
3. Before designing anything on a screen, read `references/acumatica-screen-logic.md`
   and use `jigx-acumatica-kb`. Reconcile with the live instance when a connection
   exists, and treat customer-added and unresolved fields as schema only.
4. Confirm whether the task is inbound lookup sync, local-first outbound sync, direct
   submit, file upload, or retry/error handling. For any offline/batch create or
   update, read `references/acumatica-sync.md` before designing tables or actions.
5. Confirm REST versus OData. Prefer REST when possible.
6. Confirm required `$expand` values before implementing lookups that need nested data.
7. Keep Acumatica config casing exact: `acumaticaURL` and `acumaticaOdataURL`.
8. Use `accessToken` with type/value `acuerp`; do not hardcode access tokens.
9. Preserve local-first behavior unless the app explicitly uses direct submit.
10. For every selector and every write, read `references/acumatica-selectors-and-cascades.md`
    and `references/integration-verification.md`. Resolve runtime-only selectors on
    the instance, round-trip lookup values through the writable endpoint, and run
    the hardening gauntlet before calling a write done.
11. Declare every list read per `references/acumatica-reads-and-sync-contract.md`:
    inquiry-backed, delta by a modified field, row identity for join rows, and a
    required parameter for any read that needs an address or a parent.

## Reference Map

- `references/recipe-index.md` - when to read each Acumatica reference.
- `references/acumatica-discovery.md` - probing an instance, the three channels by
  job, release from build, session and licence discipline, reading Acumatica errors.
- `references/acumatica-screen-logic.md` - what the screen knowledge base carries,
  the release rule, reconciling with the live instance, provenance, what it cannot know.
- `references/acumatica-selectors-and-cascades.md` - the stored-column contract,
  owner selectors and `ContactID`, finding cascades, diagnosing a rejected value,
  round-trip proof, the hardening gauntlet, value semantics, workflow actions.
- `references/acumatica-reads-and-sync-contract.md` - inquiries for lists, creating
  an inquiry, delta sync, addressed and parent-driven reads, row identity, the row cap.
- `references/acumatica-endpoints-and-files.md` - contract REST as fallback, detail
  collections, creating a missing endpoint, attaching files to a record.
- `references/acumatica-rest.md` - REST functions, parameters, payloads, errors.
- `references/acumatica-sync.md` - local-first sync, Remote states, command queue,
  parent-child search/replace, retry.
- `references/acumatica-lookups-expands.md` - lookup sync, OData/REST differences,
  `$expand`, force sync, dropdown fields.
- `references/acumatica-files-submit.md` - file uploads, PDF attachments, service
  order submit patterns.

- `references/integration-verification.md` - test app-populated data through the
  generated request, authorized live writes and remote readback.

## Guardrails

- Never guess or default the Acumatica release. Derive it from the build or ask; a
  release the knowledge base does not cover is designed from the live schema, never
  from a neighbouring release.
- Never sync a large list through the screen API or contract REST. Lists go through
  a Generic Inquiry.
- Never leave an Acumatica session open. Login, work, logout, on failure too.
- Never invent a dropdown's rows, an enum value, or a cascade. Reproduce the row
  source, harvest enum values from the instance, and prove a cascade with a read.
- Never ship a lookup that has not round-tripped against the writable endpoint.
- Never feed a per-user field from a global sync of its table.
- Never write workflow status as a field; move it with the screen's action.
- Never treat a rejected selector value as bad data before checking the stored column
  and the population.
- Never publish a customization package without naming the environment and getting
  explicit agreement; validate first, test before production.
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
