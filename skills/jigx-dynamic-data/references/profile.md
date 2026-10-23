# ddcheck profile

A profile holds what is specific to one customer or solution. It is JSON, kept in the solution
repo, found as `--profile <path>`, else `ddcheck.json` or `docs/test-knowledge/ddcheck.json` in
the current directory or a parent (up to the git root). Paths in it are relative to that repo.
Unknown keys are an error (typos must not silently disable a check); keys starting with `_` are
comments.

```json
{
  "_comment": "What this profile is for, and who owns it.",
  "org": "<organisation id>",
  "source": "path/to/solution-folder",
  "out": ".ddcheck",
  "configTables": ["config", "homeMenu"],
  "dataTables": {"names": ["comments"], "suffixes": ["Answers", "Images"]},
  "requiredTables": ["config"],
  "linkTables": ["homeMenu"],
  "linkField": "linkTo",
  "liveWhen": {"field": "active", "equals": true},
  "holdTables": ["config"],
  "tenantNames": ["acme", "acme ?test"],
  "matchKeys": {"regions": ["country", "name"]},
  "protect": ["*-prod", "*-live"],
  "fatal": ["missing flag"],
  "rules": []
}
```

| Key | Default | Meaning |
| --- | --- | --- |
| `org` | none | Organisation id; `--org` and `JIGX_ORG_ID` override it |
| `source` | cwd / `$JIGX_WORKSPACE/<solution>` | Solution folder holding `index.jigx` |
| `out` | `.ddcheck` | Dumps, `diff.md`, `import/` |
| `configTables` | declared minus data | Default table list; also marks these as config even if the app writes them |
| `dataTables` | none | Extra data tables beyond the ones the app writes (derived) |
| `requiredTables` | none | Absent on the solution = fatal `missing table` (rule tables count too) |
| `linkTables` | auto | Tables whose rows navigate; auto = any table whose rows carry `linkField` |
| `liveWhen` | `active` is `true` | Which rows the app renders; a dangling link elsewhere is only `latent`. `null` = all rows |
| `holdTables` | none | Never auto-imported by `seed`: per-solution by design |
| `tenantNames` | none | Regexes; a row mentioning one is held back from `seed` (URLs, GUIDs and emails always are) |
| `matchKeys` | none | Match rows by these fields instead of `rid`, for tables seeded separately on each side |
| `protect` | none | Glob patterns of solutions `seed` refuses to write to |
| `fatal` | none | Extra severities that make `check` exit 1 |

## Rules

| `type` | Fields | Finding |
| --- | --- | --- |
| `singleRow` | `table` | `count mismatch` unless the table has exactly one row |
| `configFlags` | `table` | `missing flag` for every `$.<flag>` a datasource reading that table extracts but the row lacks |
| `countBy` | `table`, `field`, `expect` {value: count}, `exhaustive` (default true) | `count mismatch` per value; with `exhaustive`, values not in `expect` too |
| `requiredFields` | `table`, `fields` | `missing field` for each empty field |
| `allowedValues` | `table`, `field`, `values` | `bad value` for any other non-empty value |
| `menuParameters` | none | For link tables whose `parameters` object reaches the target jig as one input named `parameters`: `bad parameters` when the jig cannot accept it, `dead parameter` for keys it never reads |

Every rule may carry a `_why` comment. Put the source of each expected number there (who ruled,
which solution is the source of truth, the date), so the next person can tell a stale rule from a
real regression.
