---
name: jigx-investigation
description: >
  Use this skill whenever the user mentions investigating a bug, issue, or unexpected behavior
  in a Jigx app. Triggers include "investigate this", "look into this bug", "create an
  investigation prompt", "something isn't working", "I need to debug", or any description of
  a Jigx UI or action bug. Always use this skill before writing
  any investigation prompt — it ensures the prompt follows the correct structure, saves a plan
  to an .md file, and provides the follow-up instance prompt automatically.
---

# Jigx Investigation Skill

When the user describes a bug or asks for an investigation prompt for their Jigx app, follow this workflow every time without being asked.

## Hard rule: unsure → `jigx-research`

Never write a Jigx behaviour, property, action type or default into a prompt, a question or a
plan as fact unless you can cite it: an installed `jigx*` skill reference, the builder schema
(`~/.vscode/extensions/jigx.jigx-builder-*/assets/schemas/`, `file:line`), or a working precedent
in the repo (`file:line`). If you are unsure, run the `jigx-research` agent first with numbered
questions (R1, R2…) and put its cited answers in the prompt's **Context**. An answer of
"Not confirmed" goes into the prompt as an open question for the investigator, not as a fact.

Before writing, load the installed Jigx best-practice skills (list them with
`find .claude/skills .agents/skills ~/.claude/skills ~/.codex/skills -maxdepth 1 -name 'jigx*' 2>/dev/null`;
read each `SKILL.md` and `references/recipe-index.md`) so the questions target known traps.

---

## Step 1 — Gather context

Extract from the conversation (ask only for what's missing):

- **Bug description** — what the user sees happening vs. what should happen
- **Steps to reproduce** — the sequence of actions that triggers the issue
- **Files involved** — which `.jigx` / YAML files are relevant (e.g. `appointment-actions`, a specific form jig)
- **Related context** — any known similar bugs already fixed, recent changes, or relevant architectural patterns (e.g. offline-first, ERP REST calls, datasource refresh)
- **Research** — cited `jigx-research` answers for anything you were unsure of (see the hard rule)

---

## Step 2 — Write the investigation prompt

Structure every investigation prompt with these sections:

```
**Steps to reproduce:**
[exact sequence]

**Context:**
[any related bugs, architectural notes, or patterns to cross-reference, plus the cited
jigx-research answers]

**Rule for the investigator:**
If you are unsure how a Jigx component, action, datasource or expression behaves, run the
jigx-research agent and cite its answer (skill reference, schema file:line, docs.jigx.com URL or
repo file:line). Do not guess, and do not present an uncited Jigx behaviour as a root cause.

**Files to investigate:**
[list of file names / paths]

**Please investigate and report back on:**
[numbered questions targeting root cause — see patterns below]

**Report back with:**
- Reproduced on current build: YES | NO (with the evidence: log signature, DB rows, screenshot)
- File paths and relevant code snippets
- Root cause explanation
- Recommended fix with before/after snippets where applicable
```

### Common investigation question patterns for Jigx bugs:

**UI state / button enabled state not updating:**
1. How is `isDisabled` currently determined — bound datasource field, local state, `when:` condition?
2. After the triggering action completes, is there an `onSuccess` handler? (In the builder schema it exists on `execute-entity`, `execute-entities`, `submit-form` and `open-scanner`; there is no `onCompleted`.)
3. Does that handler refresh the datasource driving the button state?
4. Is the datasource re-fetching after the action, or is the UI still showing stale state?

**Status not updating after an action:**
1. What API call or execute-entity fires during the action?
2. What fields/status values does it write?
3. Is the status field included in the payload, or is it omitted/inferred?
4. Is there a missing explicit status write or a missing datasource refresh after completion?

**Draft rehydration / offline issues:**
1. Is data being read from DYNAMIC or LOCAL storage?
2. Is the correct storage layer being written to on save?
3. Are offline dropdowns populated from a local SQLite datasource?

**General patterns to always check:**
- Is there a missing `onSuccess` refresh?
- Is a datasource field being read before it has re-fetched?
- Is a status value hardcoded, or inferred by the backend/ERP rather than explicitly written?
- Are there offline edge cases (field reps on low connectivity)?

---

## Step 3 — Append the plan-save instruction

Always append this block at the end of every investigation prompt, adjusting the filename to match the bug:

```
---

Once investigation is complete and the root cause and fix are identified, save a full implementation plan to:
`docs/plans/<kebab-case-bug-name>.md`

The plan should include:
- Root cause summary, with the citation for every Jigx behaviour it relies on
- Files to modify (with paths)
- Exact changes to make (with before/after snippets where possible)
- Any edge cases to watch for (e.g. offline scenarios, already in-progress records, multi-tenant routing)

Do not implement anything. Save the plan and stop.
```

---

## Step 4 — Provide the follow-up instance prompt

After writing the investigation prompt, always output this follow-up block so the user has it ready to paste into a new Claude Code instance:

---

**Once investigation is done, start a new Claude Code instance and open with:**

```
Read docs/plans/<same-filename>.md and implement the plan exactly as described.
Start in Plan mode so I can review before any changes are made.
```

---

## Naming convention for plan files

Derive the filename from the bug description in kebab-case:

| Bug | Filename |
|-----|----------|
| Pause button not re-enabling after Resume | `pause-button-state-fix.md` |
| Depart/Arrive not setting status to In Progress | `depart-arrive-status-fix.md` |
| Draft not rehydrating on reopen | `draft-rehydration-fix.md` |


