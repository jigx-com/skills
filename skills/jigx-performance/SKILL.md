---
name: jigx-performance
description: Use when a Jigx screen, list or sync is slow, stalls or logs "Timeout for first snapshot", when auditing an existing solution for sync and performance problems (a prioritized report of unread or oversized syncs, full pulls, unused datasources, slow queries and lists), when timing a load before and after a change, or when writing or changing a datasource.sqlite query, an action.execute-sql index, or a list whose items read datasources. Covers measuring on-device SQLite cost, live load tracking and before/after comparison, expression indexes and query-plan guards, shipping DDL safely, list query shape and components, state and expression cost, screen size, and verifying a change. Use together with the generic `jigx` skill.
---

# Jigx Performance

Jigx apps are local-first: screens read from an on-device SQLite database, and a query
over 8 seconds logs `Timeout for first snapshot` and stalls the screen. This skill covers
the read path and how much the sync brings onto the device. Use it with the `jigx` skill
(sync order, delta and writes: `sync-orchestration.md`, `writes-and-duplicates.md`), and
with `jigx-acumatica` when the data comes from Acumatica.

There are three ways in. Pick one from the request:

| Request | Mode |
| --- | --- |
| `/jigx-performance audit`, "just check the code", "audit only", "no simulator", "before I commit" | **Code check** |
| `/jigx-performance`, `/jigx-performance review`, "check my solution's performance", a slow screen or sync | **Review** (starts with the code check) |
| Writing or changing a `datasource.sqlite` query, an `action.execute-sql` index or a list; timing a change the user made | **While building** |

`references/recipe-index.md` says which reference to read for each task. `USAGE.md` is the
user-facing guide: every audit rule with its priority, and every script's flags.

## Always

- **Never change the branch that is checked out.** The code check and timing are
  read-only. Approved fixes go on a new branch `perf/<short-name>` in a separate worktree
  (`git worktree add ../<repo>-perf -b perf/<short-name>`), and you say so before the
  first change.
- **Write outputs to the review folder** that `preflight.py` prints,
  `~/jigx-perf-reviews/<solution>/<branch>/` (`<review>` below), never into the repo.
- **SDK projects:** run `jigx build` first when `build/output.json` is missing or older
  than `src/`. YAML solutions are the folder with `index.jigx`.
- **Never publish to or time against production.** Change nothing in the solution before
  the user approves.

## Code check

Needs only Node 18+ (and Ruby for YAML). Do not mention devices or setup.

1. Run `scripts/preflight.py --code-only <folder>` and show its lines: the solution, the
   **branch and commit** that will be audited, and anything missing. Do not ask which
   branch; say "to audit another branch, check it out and run it again".
2. Run the audit:

   ```sh
   node <skill>/scripts/check-performance.mjs <solution> [--db <device.db>] \
     --report <review>/before/audit.md > <review>/before/static.json
   python3 <skill>/scripts/check-plans.py <solution> > <review>/before/plans.json
   ```

   `--db` (a device database, `track-load.py --list` finds them) sizes sync and index
   findings by real rows and bytes. `audit.md` lists high and medium findings and counts
   low ones by type (`--list-low` lists them all).
3. **Confirm before you report.** Check every high finding in the source. For medium
   findings, check two or three of each type; when those are false, drop the type and
   say why (e.g. a table read only by a script). Review what the scan cannot see:
   state, expressions, screen size, start-up work.
4. Show the counts by priority, then each confirmed finding as *what is wrong · where ·
   the fix*, grouped by section and headed by what the section means for the app
   (`SECTION_MEANING` in `check-performance.mjs`). Say where `audit.md` is.
5. In the code-check mode, end with one line, not a question: the findings can be fixed
   as diffs now ("fix the high findings"), and timing them needs a simulator
   (`/jigx-performance review`).

Before a PR: `pr-check.mjs --base origin/<target>` audits the branch against its target
and exits 1 on a new high finding; `ci/jigx-performance.yml` runs it on every PR.

## Review

Follow `references/customer-review.md` for the details of each step.

1. **Show where you are, ask once.** Run `scripts/preflight.py <folder>` and show what it
   prints: solution and type, **branch and commit** with uncommitted changes, devices with
   their Jigx apps, and numbered setup steps with their fixes. Ask one question: **review
   this branch** (recommended), **another branch or folder**, or **set up a device
   first**. Do nothing before the answer.
2. **Code check**, steps 2–4 above. Then ask: **time it and fix it** (recommend it when
   there are high or medium findings), **fix the high findings now** (diffs per
   `references/fixes-by-language.md`, applied after the user agrees), or **stop here**.
3. **Set up a device if needed.** When preflight lists setup steps or a simulator,
   emulator, adb or Maestro command fails, follow `references/device-setup.md`: one step at
   a time, ask before each install or boot, the user runs `sudo` themselves with `!`,
   rerun `preflight.py` after each step. Skip only what the user declines (Android is
   optional), and the report says what was not measured.
4. **Confirm the plan:** the **test** solution to publish to, the sign-in sync, the 3–6
   screens to time and the test data.
5. **Time the current solution** on the test solution (YAML: `copy-to-test.py`, then Jigx
   Builder; SDK: `jigx_debug`), per `references/run-and-track.md`:
   - sync: `track-load.py`, three runs (`--android` on an emulator)
   - queries and actions: `time-queries.mjs --scale 10` on the test user's database
   - screens and cold start: `time-screens.py`, three runs a screen

   Time iOS and Android when both are set up, one at a time; close an emulator before
   starting another.
6. **Propose** with `review-report.py`: changes by section A–H, by priority, each a diff
   in the solution's language with file, reason, measured cost, expected effect, risk
   and the timing that should move. Mark each **Needs fixing** (confirmed high or medium,
   a measured slow step, an expression error) or **Optional**, and list what is left
   alone and why.
7. **Stop and ask.** Recommend every **Needs fixing** change; the user may pick sections.
8. **Apply** approved sections one commit each on the perf branch, rebuild and check
   after each, publish to the same test solution and **time everything again** (`post`).
   Compare with `compare-runs.py`, `time-queries.mjs --baseline` and
   `time-screens.py --compare`, and show `<review>/report.md`: what got faster, what did
   not move, any regression, what was not verified.

## While building

1. Before writing a list or search datasource, read `references/query-shape-and-lists.md`.
2. For every filtered predicate on a table that can grow, ship a matching expression
   index per `references/indexes-and-execute-sql.md`.
3. Resolve datasource views once in `expressions:`, not per list-item row. Cast numeric
   SQL columns that feed `when:` guards.
4. Before publishing, run `check-performance.mjs` and `check-plans.py` with the `jigx`
   runtime checker. Profile any query over a table that can pass a few thousand rows
   with `profile-query.py --scale 10`, and add a `check-plans.py --guard` entry for every
   lookup that runs per row or per change.
5. To time a change: three `track-load.py` runs on the old build (`--label pre-1..3`) and
   three on the new (`post-1..3`), same data and simulator, then `compare-runs.py`; put
   its `comparison.md` in the PR (`references/run-and-track.md`).

## Guardrails

- Do not optimise before measuring. Query size is not cost.
- Do not time queries through the debug bridge (`query_device_db` is ~5 s round trip).
- Prefer an index to a rewrite; a rewrite must be diffed against the original on real
  data with non-zero row counts on both sides.
- Do not name real tables in `entities:` of an index-only `execute-sql`.
- Do not trust a successful sync as evidence that DDL worked; check `sqlite_master`.
- Do not claim a device improvement from host timings, from one run, from query plans
  alone, or from a load that fetched fewer rows. Compare medians of three runs a side
  with equal final counts, label each timing with its machine, and say what was not
  reproduced.
- Do not run timing loads against a production solution or a working user's device.
