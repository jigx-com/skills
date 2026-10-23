---
name: jigx-dynamic-data
description: >
  Use when checking, comparing or copying a Jigx solution's Dynamic Data (the Manage → Data
  tables: menus, config flags, lookups) through the Management API, rather than through the phone.
  Triggers include "check the DD tables", "is the config row right", "a menu tile goes nowhere",
  "diff QA and training", "what's different between these two solutions' data", "seed the new
  solution from QA", "copy the lookup tables", or a feature that is on or off for no visible
  reason. It ships `ddcheck` (whoami, dump, check, diff, seed). Every read is safe to run;
  `seed` writes, and only a person at a terminal can confirm it, so an agent must hand the seed
  command to the user instead of running it.
---

# Jigx Dynamic Data

Read, validate, compare and (with a person's confirmation) copy Dynamic Data through the Jigx
Management API. Use it instead of reading the device when the question is "what does the server
hold": the device can lag behind the server during a sync (see `jigx-mobile-testing`).

## Hard rule: writes need a person

`ddcheck seed` is the only command that writes. It prints the plan (login, org, target solution,
tables, row counts) to the terminal and writes only when a person types the target solution's
name there. It reads that answer from the controlling terminal, never from stdin, so:

- **An agent never runs `seed` and never tries to answer the prompt.** Its shell has no terminal,
  so the attempt is refused and nothing is written. Run `diff` first, show the user the report and
  the rows it would copy, then give them the exact `seed` command to run in their own terminal.
- There is no `--yes` flag and no environment variable that skips the prompt. Do not add one.
- A solution matching the profile's `protect` patterns (for example `*-prod`) is refused outright.
- Data tables (anything the app writes, plus the profile's `dataTables`) are never compared or
  copied: naming one is an error, not a skip.

Everything else (`whoami`, `dump`, `check`, `diff`) only reads and writes local files under the
output folder (`.ddcheck/` by default; git-ignore it).

## Setup

1. Log in with the `jigx_debug` MCP (`login_debug_user`) as the user who can see the org.
   `ddcheck` reuses that login and renews it when it expires (it reads the app client id from the
   installed `@jigx/jigx-debug-mcp` package; nothing is stored in this skill). Alternatively
   export `JIGX_AUTH_TOKEN=<raw IdToken>`. No password is ever read.
2. Optionally put `ddcheck` on PATH: `bash <this skill>/scripts/ddcheck/install.sh`. Otherwise run
   `python3 <this skill>/scripts/ddcheck ...`.
3. Run `ddcheck --org <org id> whoami` and confirm the login and org. The MCP login is sticky:
   after work for another customer it is still that customer's user.

## Commands

```bash
ddcheck --org <org> whoami
ddcheck --org <org> dump  --solution <name> [--source <folder>] [--tables t1 t2 | --tables file.txt]
ddcheck --org <org> check --solution <name> [--source <folder>] [--offline]
ddcheck --org <org> diff  --from <reference> --to <target> [--rows]
ddcheck --org <org> seed  --from <reference> --to <target> [--only-missing]   # a person runs this
```

- `--source` is the solution folder that holds `index.jigx` (the repo's folder, or the Builder
  extension's download). It supplies jig ids, datasource SQL and the declared table list.
- Without `--tables`, the list is the profile's `configTables`, else every table
  `databases/default.jigx` declares minus data tables.
- `check --offline` reads a previous `dump`, so rules can be tested on hand-edited dumps.
- `check` exits 1 on a fatal finding (`missing table`, `dangling linkTo`, plus the profile's
  `fatal`), so it can gate a publish or a CI job.

## What `check` finds without any profile

Derived from the solution source, so it works for any customer:

| Finding | Meaning |
| --- | --- |
| `dangling linkTo` (fatal) | A rendered row's `linkTo` names no jig: the tile dead-ends on the device |
| `latent linkTo` | The same on a row the app does not render (`active` is not `true`) |
| `bad type` | A field the datasource SQL compares to `true`/`false` or a number holds a string (e.g. `"true"` saved from Manage → Data), so the filter silently never matches the row |
| data tables | Tables an `execute-entity`/`execute-entities` action writes are treated as rep or sync data and refused |

Customer rules (expected row counts, required fields, allowed values, the config row's flags,
menu `parameters`) go in a per-repo profile: `references/profile.md`.

## Workflow

1. `whoami`, then `dump` the solution you are investigating.
2. `check` it. Fix the source or the data; never both in one step.
3. To align two solutions: `diff --rows`, read `diff.md` and the **Decide before copy** section,
   then hand the `seed` command to the user. `seed` re-diffs after writing and prints PASS only
   when nothing is missing or changed.
4. Prove the result on the device with `jigx-mobile-testing` (cold start; Dynamic Data can lag).

Load `references/recipe-index.md` for the API shapes and the profile format.
