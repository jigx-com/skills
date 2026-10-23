# Jigx Mobile Testing Recipe Index

Use this index to load the smallest reference or script needed for the task.

| Task | Read / run |
| --- | --- |
| Drive the UI with adb: find, tap, type, scroll, dropdowns, system dialogs, wait on a condition | `adb-recipes.md`, `../scripts/adb/`, `../scripts/uidrive.py` |
| Offline submit, draft rehydration, upload retry, multi-tenant scenarios | `adb-recipes.md` |
| Pair a device with `jigx_debug`, keep the bridge alive, read the device DB, read log windows | `universal-learnings.md` (jigx_debug MCP & pairing), `jigx-field-lessons.md` |
| A JSONata, YAML or expression-script trap (empty strings, undefined operands, colon-space, apostrophes) | `jigx-field-lessons.md`, `universal-learnings.md` (Jigx runtime: expressions) |
| Command queue, sync scope, Dynamic Data row ids, proving a write landed | `jigx-field-lessons.md` (Command queue and sync), `universal-learnings.md` (data, sync & command queue) |
| Forms, jig state, navigation, documents, media and uploads on device | `universal-learnings.md` |
| Slow, lossy or failing network for one host; lost-response replay | `universal-learnings.md` (Network/proxy rigs), `../scripts/README.md` |
| Validate `.jigx` YAML and every `=` expression before commit or publish | `../scripts/jigx-validate.js` |
| Inventory and prove type guards on comparisons and `$uppercase()` | `../scripts/expression-guards/` |
| Decide whether a warning is a real defect or benign platform noise | `universal-learnings.md` (Benign platform noise) |
| Start a repo's test knowledge base | `../templates/test-knowledge/` |
| Read, check or compare Dynamic Data on the server (not the phone) | the `jigx-dynamic-data` skill (`ddcheck`) |

## Skill Boundary

This skill is generic to every Jigx solution. Customer selectors, tenants, org ids, test users and
bug signatures belong in the solution repo's `docs/test-knowledge/`, never here. ERP-specific rules
belong in the matching integration skill (for example `jigx-acumatica`).
