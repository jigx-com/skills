# Verify the runtime journey

Use this for new solutions or changes to forms, persistence, sync or submission.
Scale verification to the affected journey; cosmetic edits do not require full
integration testing. Respect existing publication authorization; this introduces
no additional approval requirement.

## Generated artifact checks

After building, run `node <skill-directory>/scripts/check-runtime.mjs build/output.json`.
Copy the dependency-free script into the project to include it in repeatable local/CI
verification. It fails on known runtime-contract errors and reports warnings:
undeclared state, unreachable action groups, screen SQL `$name` instead of `@name`,
GET bodies, function defaults using screen datasources, incomplete continuation
maps, and custom script calls in guard evaluation. Review intentional exceptions
against the installed runtime; do not suppress all findings.

Also inspect dropdown value projections, local/cloud table ownership, datasource
dependencies and transforms. The checker cannot establish UI behavior or successful
remote writes. Resolve SDK/runtime discrepancies from actual generated artifacts
and installed runtime code; a TypeScript cast is not evidence of a supported API.

## Match the execution boundary

Use sanitized real response fixtures preserving primitive types, nesting, missing
values and empty arrays. Test generated expressions with exactly the globals and
error behavior available at their evaluation point. Do not register helpers in a
test of a mobile guard that lacks solution script metadata. A JavaScript throw may
be caught by expression evaluation and become null: it is not a reliable substitute
for a fail-closed guard before a remote write. Test that failure prevents HTTP.

## Smallest complete device journey

Exercise the affected flow in the specified app (e.g. Frontline):

1. Select parent and dependent dropdowns, close them, and confirm values persist.
2. Save locally, leave, reopen from the expected list and verify header/children.
   Include a discount and attachment when those features are being delivered.
3. Submit from the visible action and observe queued, processing, failure/success.
4. Read the remote record back and confirm the local remote id/state. Verify failed
   sends preserve work and retries of uncertain creates cannot duplicate records.

Use available debug/UI tools autonomously. If the phone cannot be operated by tools,
prepare all independent verification and request only the remaining device action.
A deployment needed for device testing is a test deployment until observed; do not
substitute a passing test count for missing device evidence.

## Evidence

Confirm pairing, solution and target app, then start capture before the action.
Capture restart may clear logs: persist useful sanitized evidence first. Correlate
record id, function, time, HTTP status and nested field errors. Never dump credentials,
request authorization headers or full debugger state into reports.

Review generated artifacts and actual failure evidence. Read back the published
configuration. Report static checks, live contract reads, local save/reopen and
remote submission/readback separately. Queue acceptance is not a successful save.
