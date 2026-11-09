# App Architecture

## Recommended Project Layout

```text
src/
  app.ts
  common.ts
  database.ts
  datasources.ts
  schemas/
  functions/
  actions/
  screens/
  scripts/
```

## Constants

Keep IDs in `common.ts` or equivalent modules:

- screen and jig IDs
- datasource IDs
- entity/table names
- action IDs
- function IDs
- shared colors, tags, and static option values

Use the same casing everywhere. Jigx expressions, SQL JSON paths, table names,
component instance IDs, and generated YAML are case-sensitive.

## Build Order

The usual build order is:

1. Define tables and schemas.
2. Define app-scoped datasources.
3. Define reusable functions and actions.
4. Define screens and jigs.
5. Assemble in `app.ts`.
6. Generate JSON/YAML.
7. Run generated runtime checks and the affected contract tests (`runtime-verification.md`).
8. Publish within authorization, read back the artifact, and verify the affected device journey.

## Generated YAML

Generated YAML is the runtime truth. Inspect it when:

- a screen input is not available where expected
- an action receives `data` that is not an object
- a datasource does not refresh after save
- a tab does not see child records
- an SDK type appears behind current runtime behavior

## Credentials

Keep credentials in solution settings and read them as
`=@ctx.solution.settings.custom.<Name>`; never write a literal `Basic` header, API key or
token into a function or action. When one is found in source or logs: rotate it with its
owner first, create the setting, replace the literal one function at a time, stop
Authorization headers and request parameters reaching error records, then confirm the old
value no longer works. Report credentials by file, line and a masked hint only.

## Known SDK Builder Gaps

Found porting production YAML solutions to the SDK; recheck against the installed
version before working around them:

- REST `url` cannot be an expression (`toNonExpression`); the platform supports it.
- `continuation.parameters` has no builder path and passes through raw.
- REST parameters have no `default`.
- `FunctionErrorBuilder.title()` copies the title into a missing `description`, inventing
  user-visible text. Check generated error blocks.
- The expression validator rejects `@ctx.error.name`, which the runtime accepts.
- The SQL validator creates tables with only `id` and `data` (a `timestamp` reference is
  refused), rejects double-quoted literals, and mangles hyphenated table names.
- Datasource builders always emit `instanceId`; no builder models `style`; there is no
  reusable component collection, so shared UI is a TypeScript function that emits builders.

## Public Skill Hygiene

Do not include:

- personal access tokens
- OAuth tokens
- org IDs unless they are fake examples
- customer names unless sanitized
- app-specific private paths as required instructions

