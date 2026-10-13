# Jigx Performance Recipe Index

Use this index to load the smallest reference needed for the task.

| Task | Read |
| --- | --- |
| A screen is slow or times out; profiling a query; host vs device numbers | `measuring.md` |
| Adding or changing an expression index; shipping DDL with `action.execute-sql` | `indexes-and-execute-sql.md` |
| Writing a list or search datasource; list items that read datasources; JSONata guards over SQL results | `query-shape-and-lists.md` |
| Proving a rewrite or index is equivalent and effective; the end-to-end checklist | `verifying.md` |

## Core Principles

- Measure first, on realistic data, and attribute cost before changing anything.
- Prefer an index to a rewrite. An index cannot change a result set.
- Cap what a list can return and resolve datasource views once, not per row.
- DDL fails silently. Verify indexes by name and expression in `sqlite_master`.
- Label every timing with the machine it ran on, and say what was not reproduced.

## Skill Boundary

This skill is generic to Jigx local SQLite. How much data reaches the device (inquiry
reads, delta sync, the row cap, parent-driven reads) is decided by the integration;
for Acumatica read `jigx-acumatica` `references/acumatica-reads-and-sync-contract.md`.
