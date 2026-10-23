# Jigx Skills

Installable AI skills for building Jigx solutions.

This repository contains these skills:

- `jigx` - generic Jigx Core SDK app-building recipes.
- `jigx-acumatica` - Acumatica-specific integration recipes for Jigx apps.
- `jigx-mobile-testing` - device testing of a Jigx solution with adb and the `jigx_debug`
  MCP, a per-repo test knowledge base, field lessons, and rig scripts (adb helpers, proxy
  rigs for slow or failing hosts, a `.jigx` YAML/JSONata validator).
- `jigx-investigation` - turns a Jigx bug report into a structured investigation prompt and
  a saved plan.
- `jigx-dynamic-data` - `ddcheck`: read, check, diff and seed a solution's Dynamic Data
  through the Management API. Writes need a person at the terminal.

It also contains two Claude Code agents in `agents/` (see [Agents](#agents)).

The skills are intentionally split so generic Jigx guidance can remain reusable while
Acumatica conventions, endpoint rules, sync behavior, and ERP-specific payload shapes
stay isolated.

## Install

Install these skills with the Vercel Labs `skills` CLI:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --agent claude-code --agent codex --global
```

Use the non-interactive shorthand when scripting installs:

```sh
npx skills add jigx-com/skills -s jigx -s jigx-acumatica -a claude-code -a codex -g -y
```

If the repository is private or you prefer SSH, use the Git URL:

```sh
npx skills add git@github.com:jigx-com/skills.git --skill jigx --skill jigx-acumatica --agent claude-code --agent codex --global
```

### Install For One Agent

Claude Code only:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --agent claude-code --global
```

Codex only:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --agent codex --global
```

### Project-Local Install

Run this from the project repository when the skills should apply only to that project:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --agent claude-code --agent codex
```

Project-local installs are useful when a repository needs pinned skill content checked
into the project workspace rather than installed globally for every agent session.

### Testing and debugging skills

```sh
npx skills add jigx-com/skills --skill jigx-mobile-testing --skill jigx-investigation --skill jigx-dynamic-data --agent claude-code --agent codex --global
```

## Agents

`agents/` holds two Claude Code subagents. The `skills` CLI installs skills only, so copy
them by hand (`.claude/agents/` for one project, `~/.claude/agents/` for every project):

```sh
mkdir -p ~/.claude/agents && cp agents/*.md ~/.claude/agents/
```

- `jigx-code-reviewer` - reviews the current diff (`.jigx` YAML, JSONata, datasources,
  actions) and returns ranked corrections; it never edits files.
- `jigx-research` - answers "how do I do this in Jigx" from the installed skills, the
  builder schemas, docs.jigx.com and the repo, with a citation for every claim.

Both load every installed `jigx*` skill at the start of each run and follow its
`references/recipe-index.md`, so a new skill or reference added here is picked up without
editing the agents. When the reviewer, or an agent following `jigx-investigation`, is unsure
how Jigx behaves, it must not guess: the question goes to `jigx-research`, and the answer
comes back with a source.

## Manage Installed Skills

List installed skills:

```sh
npx skills list
```

Update installed skills from this repository:

```sh
npx skills update jigx
npx skills update jigx-acumatica
npx skills update jigx-mobile-testing
npx skills update jigx-investigation
npx skills update jigx-dynamic-data
```

Remove installed skills:

```sh
npx skills remove jigx
npx skills remove jigx-acumatica
npx skills remove jigx-mobile-testing
npx skills remove jigx-investigation
npx skills remove jigx-dynamic-data
```

## Install Locations

Global installs are written to the agent's global skill directory:

- Claude Code: `~/.claude/skills/`
- Codex: `~/.codex/skills/`

Project-local installs are written under the current repository:

- Claude Code: `.claude/skills/`
- Codex: `.agents/skills/`

## Usage Guidance

Use `jigx` for all generic Jigx app work: screens, forms, datasources, navigation,
tabs, media, PDFs, local data, validation, generated YAML, and build/deploy workflows.

Use `jigx-acumatica` together with `jigx` when the app integrates with Acumatica:
REST/OData functions, `acuerp` tokens, `acumaticaURL` casing, lookup expands,
local-first sync, command queue retry, file upload, PDF attachment, and service order
submission patterns.

Use `jigx-mobile-testing` before any test on a paired device: it loads the test knowledge
base first, verifies every step against the device database, and writes what it learned
back. Customer selectors, tenants and test users stay in the solution repo's
`docs/test-knowledge/`, never in the skill.

Use `jigx-investigation` when a bug needs a root cause: it writes the investigation prompt,
the plan file, and the follow-up prompt for the implementing session.

Use `jigx-dynamic-data` when the question is what the server holds (menus, config flags,
lookup tables), or to align two solutions. Customer rules go in a `ddcheck.json` profile in
the solution repo. Only a person at a terminal can confirm a write.

## Verify generated solutions

The Jigx skill includes a dependency-free checker for known mobile runtime
contracts. Run it against the generated application before publishing:

```sh
node skills/jigx/scripts/check-runtime.mjs /path/to/solution/build/output.json
node --test skills/jigx/scripts/check-runtime.test.mjs
```

The checker exits nonzero on errors and reports warnings separately. It catches
undeclared state, hidden action groups, SQL binding mistakes, and local REST
context/pagination errors. It does not validate live business data or prove that
a device submission succeeded.

Before committing `.jigx` changes, parse the YAML and every `=` expression with the
JSONata bundled in `@jigx/core-sdk`:

```sh
node skills/jigx-mobile-testing/scripts/jigx-validate.js path/to/changed.jigx
```

The Dynamic Data tool has its own tests, run in CI:

```sh
python3 -m unittest discover -s skills/jigx-dynamic-data/scripts/ddcheck/tests
```

For Acumatica integrations, combine the
[screen KB and its companion skill](https://github.com/jigx-com/acumatica-kb-mcp)
with actual app-populated lookup
values, the generated request, authorized test writes and remote readback. The
verification references explain what evidence is needed and how to preserve draft
selections when a lookup source changes.
