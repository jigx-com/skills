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
7. Publish or deploy.

## Generated YAML

Generated YAML is the runtime truth. Inspect it when:

- a screen input is not available where expected
- an action receives `data` that is not an object
- a datasource does not refresh after save
- a tab does not see child records
- an SDK type appears behind current runtime behavior

## Public Skill Hygiene

Do not include:

- personal access tokens
- OAuth tokens
- org IDs unless they are fake examples
- customer names unless sanitized
- app-specific private paths as required instructions

