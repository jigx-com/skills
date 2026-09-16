# Jigx Skills

Installable AI skills for building Jigx solutions.

This repository contains two separate skills:

- `jigx` - generic Jigx Core SDK app-building recipes.
- `jigx-acumatica` - Acumatica-specific integration recipes for Jigx apps.

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

## Manage Installed Skills

List installed skills:

```sh
npx skills list
```

Update installed skills from this repository:

```sh
npx skills update jigx
npx skills update jigx-acumatica
```

Remove installed skills:

```sh
npx skills remove jigx
npx skills remove jigx-acumatica
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

For Acumatica integrations, combine the
[screen KB and its companion skill](https://github.com/jigx-com/acumatica-kb-mcp)
with actual app-populated lookup
values, the generated request, authorized test writes and remote readback. The
verification references explain what evidence is needed and how to preserve draft
selections when a lookup source changes.
