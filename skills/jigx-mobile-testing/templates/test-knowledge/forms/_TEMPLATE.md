# Test Case — <Form Name>

Copy this file to `forms/<form-kebab>.md` for each form. The checklist is organised by the same
bug categories as the `jigx-investigation` skill, so a failure maps straight onto an
investigation pattern. Delete dimensions that don't apply, but note *why* they were dropped.

## At a glance
- Solution: <solution-name> (from index.jigx name:)
- Form / jig file(s): `<.jigx / YAML path>`
- Applies to tenants: `<all | subset>`
- Backend target: `<ERP entity / endpoint / Dynamic Data table the submit writes to>`
- Baselines: `<links to saved log/DB baselines, once captured>`

---

## Checklist

Mark each: PASS / FAIL / N/A (+reason). Verify ground truth in the DB, not just the screen.

### Field validation
- [ ] Required fields block submit when empty (no row written)
- [ ] Format rules reject bad input
- [ ] Valid input passes and writes the expected fields

### `isDirty` / draft save
- [ ] Partial entry triggers draft save to **LOCAL** storage (not DYNAMIC)
- [ ] `am force-stop` + relaunch → form rehydrates partial values intact

### Offline submit
- [ ] Submit while offline persists a row with **pending/unsynced** status
- [ ] No phantom "success" without a local row

### Sync
- [ ] On reconnect, pending row flips to **synced**
- [ ] The backend write actually lands (re-read the record, not just a local flip)

### Upload progress / retry  *(if the form has attachments)*
- [ ] Upload shows progress; network drop mid-upload → retry/pending, no phantom success
- [ ] Reconnect resumes/completes the upload

### PDF generation  *(if applicable)*
- [ ] PDF generates with locally-staged images present
- [ ] Generated artifact recorded/referenced in the expected table

### Multi-tenant routing
- [ ] For each applicable tenant, record routes to the correct tenant (DB + backend target)
- [ ] Assert per-tenant — a passing tenant does not imply the others pass

### UI state
- [ ] Buttons enable/disable correctly *after* the action completes (no stale state)
- [ ] Any `onSuccess`/datasource refresh fires as expected

---

## Known edge cases for this form
Grows via distillation (Step 5). Add scenarios worth re-testing as they're discovered.

- `<edge case>`

## Open questions / flaky spots
- `<anything intermittent worth watching>`
