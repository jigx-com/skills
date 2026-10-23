---
name: jigx-mobile-testing
description: >
  Use this skill whenever the user wants to run, automate, or design an automated test of a
  Jigx mobile solution on a paired device — driving the app with adb and observing it through
  the jigx_debug MCP (live logs + on-device SQLite). Triggers include "test this form",
  "run a test on the solution", "automate testing", "QA this jig", "regression test",
  "set up a test for <form>", or any request to exercise app behaviour on the phone and verify
  it. Always use this skill before starting any device test — it loads the accumulated test
  knowledge first, enforces the verify-against-ground-truth loop, and writes learnings back so
  every run is sharper than the last. Use it even if the user just says "test it" without
  mentioning adb or the MCP explicitly.
---

# Jigx Mobile Testing Skill

Automated testing of a Jigx solution on a paired Android device. The rig has three parts and
this skill is the brain that orchestrates them:

- **Eyes** — the `jigx_debug` MCP: live normalised logs (`track_logs` / `stop_collecting`),
  on-device SQLite ground truth (`list_device_tables`, `query_device_db`, `save_query_result`),
  and session control.
- **Hands** — `adb` (uiautomator + `input`): taps, text entry, swipes, network toggles,
  app lifecycle.
- **Memory** — the test-knowledge base in `docs/test-knowledge/` (see Step 0). This is the part
  that makes the agent improve over time. There is no learning without the write-back step.

The agent does **not** retain anything between runs. "Getting smarter" means reading the
knowledge base at the start of every run and distilling durable findings back into it at the
end. Treat that loop as non-optional.

---

## Step 0 — Load the knowledge base (always first)

Before touching the device, read the live knowledge base so the run starts from everything
learned so far. There are two tiers:

- **Universal** (this skill, every solution): `references/jigx-field-lessons.md` (Jigx runtime,
  JSONata/YAML and debug-MCP traps) and `references/universal-learnings.md` (device rig, pairing,
  network rigs, benign platform noise). Read both once per session.
- **Per repo** (this solution only): `docs/test-knowledge/` in the repo under test. **If it does
  not exist, create it from `templates/test-knowledge/`** (copy the folder, then fill the
  solution model with the user before testing). Never copy another customer's knowledge base
  into a repo: their selectors, tenants and bug signatures are noise here.

Per-repo files:

1. `docs/test-knowledge/solution-model.md` — stable facts: package name, `targetAppName`,
   `solutionPath`, org-id → tenant map, device DB table reference, navigation map, and the
   per-screen **selector map** + **ready-signals**. This is what stops the agent re-deriving
   how to reach a screen every run.
2. `docs/test-knowledge/learnings.md` — curated gotchas, **bug signatures** (log/DB patterns of
   past defects, for instant regression recognition), and **benign noise** (warnings that look
   like errors but aren't — this is what keeps false-positive rate low).
3. The relevant `docs/test-knowledge/forms/<form>.md` — the test-case checklist for the form
   under test. If none exists, copy `docs/test-knowledge/forms/_TEMPLATE.md` to a new file for
   this form and fill in what's known.

If the solution model is missing the package name, `targetAppName`, table names, or org UUIDs,
resolve them first (run `list_device_tables` once; ask the user for the rest) and write them in
before testing. Never guess a UUID or a table name.

---

## Step 1 — Preflight (know exactly what is under test)

A failure is meaningless if you can't say what was running when it failed. Establish and record
the run context:

1. Confirm the session is live from the `create_debug_session` result (`devicePaired: true`,
   `solution.name` matches the orange banner) and one cheap query (`SELECT 1 AS ok`). Avoid
   `debug_status`: it re-runs the handshake and re-raises the ENABLE dialog (see
   `references/jigx-field-lessons.md`). If stale, recreate with `create_debug_session(..., force=true)`.
2. Confirm the build under test. `create_debug_session` defaults to `publish=false` (tests the
   currently-published solution). `publish=true` works only for Core SDK projects; a declarative
   `index.jigx` solution is published from the VS Code Jigx extension first. Record which it was.
3. Confirm the **org/tenant** via `select_debug_organization` using the map in the solution
   model. For a multi-tenant test, this is the axis you iterate.
4. `adb devices` — confirm exactly one target device is attached.

Open a new run-journal entry now (see `docs/test-knowledge/run-journal.md`) and record: date,
form, tenant/org, build mode (published vs publish=true), commit/branch if known, device.

**Credential boundary:** never put a Jigx password into the skill, the journal, a saved query
file, or any logged command. Authentication is done out-of-band by the user; the agent only ever
inspects an already-authenticated session.

---

## Step 2 — The closed-loop step procedure

Every discrete interaction follows the same six-beat loop. Bracketing each step in its own log
window is the single biggest precision win the rig offers — it makes each action's events
cleanly attributable instead of one tangled stream.

1. **Locate** — `uiautomator dump`, find the target element by `text` / `content-desc` /
   `resource-id`, compute the tap point from its `bounds`. Never hardcode coordinates; the soft
   keyboard and scroll position shift everything. Prefer the stable locator recorded in the
   selector map; if the element moved, that's a selector-map update for distillation.
2. **Open the window** — `track_logs` (this clears prior events and starts collecting).
3. **Act** — the adb interaction (`input tap` / `input text` / swipe / lifecycle). See
   `references/adb-recipes.md` for keyboard, scroll-to-element, and system-dialog handling.
4. **Wait on a condition, not a clock** — poll the uiautomator tree for the next screen's
   ready-signal, or watch for the completion event in the log window. Fixed `sleep` is the
   number-one source of flake. Each screen's ready-signal lives in the solution model.
5. **Close the window** — `stop_collecting`, then analyse just that window. Compare against the
   known-good log baseline for this step if one exists.
6. **Verify ground truth** — `query_device_db` (≤20 rows; fine for spot-checks) or
   `save_query_result` to a JSON file for anything larger, then diff against the stored baseline
   snapshot. The screen and a green toast are not proof; the SQLite row is. This is what makes a
   pass a *real* pass.

Record per-step: the action, the log-window verdict, and the DB verdict, with explicit
pass/fail against the checklist criterion — not a vibe.

---

## Step 3 — Cover the dimensions that actually break

Organise every form's checklist around the same bug categories the `jigx-investigation` skill
uses, so test findings map straight onto investigation patterns. For each form, exercise:

| Dimension | What to assert | Recipe |
|-----------|----------------|--------|
| Field validation | required/format rules block submit; valid input passes | inline |
| `isDirty` / draft save | partial entry persists; reopen rehydrates from LOCAL storage | `references/adb-recipes.md` — draft rehydration |
| Offline submit | row persists locally with pending status while offline | `references/adb-recipes.md` — offline submit |
| Sync | on reconnect, pending row flips to synced + the backend write lands (re-read the record) | `references/adb-recipes.md` — offline submit |
| Upload progress / retry | attachment upload resumes/retries after network drop | `references/adb-recipes.md` — upload retry |
| PDF generation | PDF generates with locally-staged images present | inline + DB check |
| Multi-tenant routing | record routes to the correct tenant for the selected org | `references/adb-recipes.md` — multi-tenant |
| UI state | buttons enable/disable correctly after the action completes | inline |

Skip dimensions that don't apply to a given form, but record *why* they were skipped so the
checklist stays honest.

---

## Step 4 — On failure: hand off, don't diagnose

This skill **finds and characterises** defects; it does not root-cause them. When a step fails,
stop investigating and emit a pre-filled investigation prompt for the `jigx-investigation` skill,
categorised by the dimension that failed. Include:

- the exact repro steps (the adb sequence that triggered it),
- the captured **log signature** from the failing window,
- the **DB state** observed vs expected,
- the tenant/org and build under test.

This makes test → investigate → plan → implement → re-test one closed system across the two
skills. The investigation skill will save its plan to `docs/plans/<kebab-name>.md`; reference
that filename in the run journal so the loop is traceable.

---

## Step 5 — Distil (the step that creates learning)

Never end a run without this. Promote only the **durable** findings from this run's journal
entry into the curated knowledge base:

- **New/changed selector or ready-signal** → update `solution-model.md`. (UI shifted; future
  runs shouldn't rediscover it.)
- **A real defect's signature** → add to `learnings.md` under bug signatures, so the next run
  recognises a regression instantly instead of re-investigating it.
- **A false alarm** → add the benign log/warning pattern to `learnings.md` benign-noise, so it
  stops being flagged. Recording negative knowledge is what makes the agent trustworthy over
  time.
- **A new clean baseline** (log window or DB snapshot) → save it and reference it from the form
  file.
- **A new edge case worth testing** → add it to the form's checklist.

Leave the raw, noisy detail in the run journal; only the distilled, reusable knowledge graduates
to the curated tiers. That separation is what stops the KB rotting into an unreadable log pile.

---

## Step 6 — Teardown (carefully)

- `stop_collecting` if a window is still open.
- For test isolation, `wipe_table` (returns before/after counts as a free assertion) or
  `pm clear` for a full reset. **Guardrail:** never wipe a table that holds un-synced pending
  writes — you'll destroy a pending submission and invalidate the sync test. Confirm sync state
  in the DB *before* any wipe.
- Re-enable network if a recipe disabled it (`svc wifi enable`, `svc data enable`).
- `close_debug_session` only if the user is done; otherwise leave it live for the next run.

---

## Reference files

Read these as needed — don't inline their full content here:

- `references/adb-recipes.md` — adb mechanics (selector resolution, keyboard/scroll, system
  dialogs, wait-on-condition) and the scenario recipes (offline submit, draft rehydration,
  upload retry, multi-tenant) referenced in Step 3.
- `references/jigx-field-lessons.md` — universal Jigx/JSONata/debug-MCP traps (symptom → cause → fix).
- `references/universal-learnings.md` — universal device-rig lessons and benign platform noise.
- `references/recipe-index.md` — which reference or script to load for the task at hand.
- `scripts/README.md` — the bundled scripts (in this skill's `scripts/` folder): adb UI helpers,
  `.jigx` validator, proxy rigs (throttle, hold, kill), expression-guard analysis.
- `templates/test-knowledge/` — the skeleton to create a repo's knowledge base from.

For an Acumatica-backed solution, also load the `jigx-acumatica` skill: it owns ERP-side verification.

When a lesson learned in a run is true for **any** Jigx solution, propose it to this skill's
universal references (a PR to `jigx-com/skills`); customer-specific findings go to the repo's
`docs/test-knowledge/learnings.md`. Never put customer names, org ids, tenant URLs, emails or
credentials into this skill.

The live knowledge base (read every run, written back every run) lives in the repo at:

- `docs/test-knowledge/solution-model.md`
- `docs/test-knowledge/learnings.md`
- `docs/test-knowledge/run-journal.md`
- `docs/test-knowledge/forms/<form>.md` (one per form; `_TEMPLATE.md` is the starting point)
