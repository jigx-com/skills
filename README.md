# Jigx Skills

Installable AI skills for building Jigx solutions.

This repository contains three separate skills:

- `jigx` - generic Jigx Core SDK app-building recipes.
- `jigx-acumatica` - Acumatica-specific integration recipes for Jigx apps.
- `jigx-performance` - on-device SQLite and datasource performance: measuring,
  expression indexes, list query shape, and verifying a change.

The skills are intentionally split so generic Jigx guidance can remain reusable while
Acumatica conventions, endpoint rules, sync behavior, and ERP-specific payload shapes
stay isolated.

## Install

Install these skills with the Vercel Labs `skills` CLI:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --skill jigx-performance --agent claude-code --agent codex --global
```

Use the non-interactive shorthand when scripting installs:

```sh
npx skills add jigx-com/skills -s jigx -s jigx-acumatica -s jigx-performance -a claude-code -a codex -g -y
```

If the repository is private or you prefer SSH, use the Git URL:

```sh
npx skills add git@github.com:jigx-com/skills.git --skill jigx --skill jigx-acumatica --skill jigx-performance --agent claude-code --agent codex --global
```

### Install For One Agent

Claude Code only:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --skill jigx-performance --agent claude-code --global
```

Codex only:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --skill jigx-performance --agent codex --global
```

### Project-Local Install

Run this from the project repository when the skills should apply only to that project:

```sh
npx skills add jigx-com/skills --skill jigx --skill jigx-acumatica --skill jigx-performance --agent claude-code --agent codex
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
npx skills update jigx-performance
```

Remove installed skills:

```sh
npx skills remove jigx
npx skills remove jigx-acumatica
npx skills remove jigx-performance
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

Run it as `/jigx-performance review [solution path]`, `/jigx-performance audit [solution path]`, `/jigx-performance build` or
`/jigx-performance measure [test solution]` in Claude Code, or let it load on its own:
use `jigx-performance` with `jigx` when a screen or sync is slow or logs
`Timeout for first snapshot`, when auditing a solution, or when writing a
`datasource.sqlite` query, an `action.execute-sql` index, or a list whose items read
datasources.

## Audit a solution for performance

Get a prioritized report of sync and performance problems in any Jigx solution: a YAML
solution folder (the one with `index.jigx`) or a TypeScript SDK project's build
(`build/output.json`; build it first). For an SDK project, findings point at the
`src/*.ts` file that declares the screen, datasource or action.

Run it before every PR into a solution's main branch, compared with main, so a branch
cannot add high-priority problems unnoticed. The step-by-step guide, how to read the
report, findings you can accept and a CI job are in
[`skills/jigx-performance/USAGE.md`](skills/jigx-performance/USAGE.md):

```sh
node skills/jigx-performance/scripts/check-performance.mjs path/to/solution \
  --baseline main-audit.json --fail-on high --report audit.md > audit.json
```

In Claude Code, with the skills installed:

```text
/jigx-performance audit path/to/solution
```

Or run the scan yourself, with no install. It needs Node 18+ (22.5+ for `--db`) and,
for YAML folders, Ruby, whose built-in YAML parser it uses (macOS ships Ruby):

```sh
node skills/jigx-performance/scripts/check-performance.mjs path/to/solution \
  --report audit.md > audit.json
```

Add `--db <device database>` to size the sync findings with real row and byte counts
(`python3 skills/jigx-performance/scripts/track-load.py --list` finds the simulator
databases). Without it, size-dependent findings stay at low priority.

`audit.md` lists every finding under **High**, **Medium** or **Low**, grouped by type.
Each type shows its recommended fix once, and each finding shows where it is (the file,
then the path inside it) and what is wrong. `audit.json` holds the same data for tools.
It checks:

| Area | What it finds | Priority |
| --- | --- | --- |
| Sync | tables synced that nothing reads; full pulls with no modified-date filter; OData pulls without `$select`; tables over 5 MB or 5,000 rows on the device | medium; full pulls and missing `$select` low unless `--db` shows big tables |
| Unnecessary loads | global datasources nothing references | medium |
| Indexes | index DDL that re-runs every bound datasource or rolls back on a fresh install; filters no shipped index matches | high (medium for a one-table step); unmatched filters low unless the table has 1,000+ rows |
| Lists and screens | datasource reads per list row; contains-searches with no `LIMIT`; a list whose datasource selects no `id`; `instanceId` set on a list item; custom components as list items | medium |
| List components | `component.list` bound to a datasource (use `jig.list`); `hasDynamicHeight` items | low |
| Query rewriting | `rewriteQuery: false` on a query that still uses `'$.field'` shorthand (returns the literal text); `rewriteQuery` left on | high (an error); low |
| Expressions | datasource filters inside `$map` (O(n²)); `!=` against a datasource field | high; low |

Findings are leads, not verdicts: confirm the ones you act on with
`check-plans.py` (query plans), `profile-query.py` (timings on exported data) or a timed
load (below). On a Field Services solution (about 600 files) the scan takes about 4
seconds. It found 3 high, 77 medium and 477 low findings. The medium ones include two
synced tables nothing reads, three lists whose datasource has no `id` and three lists of
custom components. On the Expenses app rewritten to the SDK it found 1 high, 16 medium
and 130 low, including five global datasources nothing uses and two lists with no `id`.

## Review a customer solution for performance

Install the skills (above), open the customer's solution project in Claude Code and
run:

```text
/jigx-performance review path/to/solution
```

Claude asks once for the **test** solution to publish to, the journey to time (sign-in
and initial sync, a refresh, or a screen) and the test data, then:

1. **Measures the baseline.** It scans the generated solution and records three timed
   loads in the iOS Simulator, showing the sync live as it runs.
2. **Gives an overview and proposes changes.** Time to ready, the slowest sync steps,
   and findings grouped into sections A–H (measuring, queries and indexes, lists, state
   and expressions, screens and start-up, sync, writes and duplicates, security and
   logs). Each proposed change says where, why, the expected effect and the risk.
3. **Waits for your approval.** Nothing changes until you say which sections to apply.
4. **Applies the approved sections**, one commit per section, rebuilding and checking
   after each.
5. **Measures again and reports.** Three more timed loads with the same data, then
   `perf-review/report.md`: time to ready before and after, which steps got faster or
   slower, slow-datasource warnings and errors, findings per section before and after,
   and what was not verified.

Requirements: Node 18+, Python 3.9+, Xcode with an iOS Simulator, the Jigx app in the
simulator, and the `jigx_debug` MCP server for publishing to the test solution. Timing
runs never touch production; simulator times show relative change, so confirm big wins
on a real device. Results stay in `perf-review/` in the solution project.

Other modes: `/jigx-performance audit` (findings only), `/jigx-performance build` (while
writing new queries and lists) and `/jigx-performance measure` (time a change you made
yourself).

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

### Performance checks

Scan the generated application for performance leads, then profile the slow query
against an exported device database:

```sh
node skills/jigx-performance/scripts/check-performance.mjs /path/to/solution --report audit.md
python3 skills/jigx-performance/scripts/profile-query.py device.db query.sql --scale 10 --ddl index.sql
python3 skills/jigx-performance/scripts/check-plans.py /path/to/solution --scans-only
node --test skills/jigx-performance/scripts/check-performance.test.mjs
python3 -m unittest skills/jigx-performance/scripts/test_profile_query.py
python3 -m unittest skills/jigx-performance/scripts/test_load_tools.py
python3 skills/jigx-performance/scripts/review-report.py perf-review --solution <name>
```

Both scripts take a YAML solution folder or a `build/output.json`. The scan's checks
and priorities are listed under "Audit a solution for performance" above. Its findings
are leads to measure, not verdicts. The
profiler works on a copy, reports row and byte counts, the query plan and timings
at 1x and scaled data, before and after DDL, and can diff a rewrite against the
original. Host timings show relative cost; they are not device timings. The plan
check lists full scans and unused indexes, and fails CI through a guard file when a
named lookup starts scanning.

Time a change by tracking the real load in the iOS Simulator, three runs before and
three after, then compare the medians:

```sh
python3 skills/jigx-performance/scripts/track-load.py --solution <test-solution> --label pre-1
python3 skills/jigx-performance/scripts/compare-runs.py perf-runs --before pre --after post
```

The tracker reads the simulator's database read-only, prints the load live, and saves
a summary with a waterfall of sync entries. The comparison flags slowdowns over 10%.
