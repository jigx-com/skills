# Jigx Performance Recipe Index

Use this index to load the smallest reference needed for the task.

| Task | Read |
| --- | --- |
| A screen is slow or times out; profiling a query; host vs device numbers | `measuring.md` |
| Reviewing a customer solution end to end: baseline, proposal, approval, apply, report | `customer-review.md` |
| Timing a load before and after a change; live sync timeline; comparing runs | `run-and-track.md` |
| Setting up a simulator, emulator, the Jigx app, Node, Java or Maestro; a setup command fails | `device-setup.md` |
| Adding or changing an expression index; `OR` lookups; plan guards; shipping DDL with `action.execute-sql` | `indexes-and-execute-sql.md` |
| Writing a list or search datasource; list components; list items that read datasources; JSONata guards over SQL results | `query-shape-and-lists.md` |
| Unused datasources, watched tables, state, shared expressions, screen size, start-up work | `state-and-screens.md` |
| Proving a rewrite or index is equivalent and effective; the end-to-end checklist | `verifying.md` |

## Improvements by section

Every improvement belongs to one section, so work can be planned, reviewed and measured
one section at a time. Sections A–E are in this skill; F–H are in the `jigx` skill.

| § | Section | Improvements | Read | Found by |
| --- | --- | --- | --- | --- |
| A | Measure and track | row and byte counts, 1x/10x profiles, live load timeline, three runs a side, medians, slowdown threshold, log signals, labelled machine | `measuring.md`, `run-and-track.md` | `profile-query.py`, `track-load.py`, `compare-runs.py` |
| B | Queries and indexes | matching expression indexes, no wrapped predicates, one branch per key instead of `OR`, materialised CTEs, key tables, precomputed joins, shape data in SQL (DS-04), `rewriteQuery` (DS-03), plan guards, unused and duplicate indexes, safe index DDL | `indexes-and-execute-sql.md`, `query-shape-and-lists.md` | `check-performance.mjs`, `check-plans.py` |
| C | Lists and rendering | capped and pinned search lists, `jig.list` with unique `id` (DS-02, CO-02), no `instanceId` (CO-04), no custom list items (CO-01), fixed height (CO-03), datasource reads resolved once per screen, `[ ]` fence, `$string()` ids | `query-shape-and-lists.md` | `check-performance.mjs` |
| D | State and expressions | no solution state (ST-01, ST-02), derived flags, shared expressions (EX-01, EX-02), `$count` in `when`, numeric casts for guards, null-safe expressions | `state-and-screens.md`, `query-shape-and-lists.md` | `check-performance.mjs` (traps); review |
| E | Screens and start-up | unused datasources (DS-01), watched tables only, jig size (JG-01), light `onLoad`, no lazy loading, fix order | `state-and-screens.md` | review; `track-load.py` for start-up |
| F | Sync | context gate, sync tiers and waves, delta, interval refresh, online guard, idempotent init, no 404 endpoints, hard reset | `jigx` `sync-orchestration.md` | `track-load.py` waterfall; production logs |
| G | Writes and duplicates | one id per record, idempotency key, queue claims, lookup before create, retry by status, double tap, runtime replay, stable read ids | `jigx` `writes-and-duplicates.md` | `jigx` `check-runtime.mjs`; test matrix |
| H | Security and logs | credentials in settings, production log audit, app version spread, SDK builder gaps, error blocks (FN-01) | `jigx` `app-architecture.md`, `runtime-verification.md` | log audit |

Report findings grouped by section, fix one section at a time, and re-run the section's
checks and a before/after timing (`run-and-track.md`) before moving on.

## Core Principles

- Measure first, on realistic data, and attribute cost before changing anything.
- Prefer an index to a rewrite. An index cannot change a result set.
- Cap what a list can return and resolve datasource views once, not per row.
- DDL fails silently. Verify indexes by name and expression in `sqlite_master`.
- Label every timing with the machine it ran on, and say what was not reproduced.
- A change is done when the measured load or screen improves, not when it builds.

## Skill Boundary

This skill is generic to Jigx local SQLite. How much data reaches the device (inquiry
reads, delta sync, the row cap, parent-driven reads) is decided by the integration;
for Acumatica read `jigx-acumatica` `references/acumatica-reads-and-sync-contract.md`.
