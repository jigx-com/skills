# State, Expressions And Screens

What a screen costs before and after its queries run: datasources it mounts, tables it
watches, state that re-evaluates expressions, and work done at start. Rule ids
(DS-01, ST-01, …) are Jigx's best-practice ids. These rules apply to the generated YAML:
check `build/output.json` or the exported solution, not only the TypeScript.

## Datasources a screen mounts

- **Remove unused datasources (DS-01).** Every declared datasource, jig-level or global,
  mounts a live listener and runs its query before the jig renders, whether or not
  anything reads it. Search the whole solution for `@ctx.datasources.<name>` (components,
  expressions, actions, `queryParameters`, other datasources) before deleting.
- **Watch only the tables you read.** A change to any table in a datasource's `entities`
  re-runs the query. A product list watching eight tables, including the cart, re-queried
  on every cart tap.

## State

- **Avoid solution state (ST-01).** A change re-evaluates every expression in the
  solution that reads it. Use jig state and pass values through inputs.
- **Never put solution state in a global datasource's `queryParameters` (ST-02).**
  Global datasources are always listening; every state change re-queries and re-renders
  every consumer.
- **Derive, do not mirror.** Groups of flags set and reset together
  (`customer-completed`, `address-completed`, …) cost renders and drift apart. Derive one
  value from the data.

## Expressions

- **Shared expressions (EX-01).** Entries under `expressions:` (jig or solution) are
  evaluated once and cached. Extract a condition used in two or more `when:` nodes, a
  value used in a display field and a `queryParameter`, and a long record path repeated
  across jigs.
- **Bind variables in long expressions (EX-02).** Use `$rows := …;` inside `=( … )` with
  a `|` block scalar once an expression chains more than one operation or exceeds about
  80 characters.
- **`$count` in `when`.** A count walks the rows every time the condition is evaluated.
  Use a count the SQL already returns, or `$exists` when only presence matters. Where a
  missing table nulls the datasource, keep the safe `$count(ds[…]) = 0` form from
  `query-shape-and-lists.md` and move the work into SQL instead.
- **Guard expressions against missing values.** String and number functions throw on a
  missing value, and a throw in a list's `data:` blanks the list. See "Null-Safe
  Expressions" in the `jigx` skill's `jsonata-validation-formatting.md`.

## Screens and start-up

- **Keep jigs small (JG-01).**

  | Metric | Good | Warning | Rethink |
  | --- | --- | --- | --- |
  | Datasources per jig | < 8 | 8–16 | > 16 |
  | Components per jig | < 15 | 15–30 | > 30 |
  | YAML lines per jig | < 500 | 500–2000 | > 2000 |

  A component behind `when: false` still mounts and evaluates its condition. Split with
  inputs, tabs or bottom sheets.
- **No lazy loading.** Jigx does not defer screens or tabs; keep each tab light rather
  than recommending deferred loading.
- **Light `onLoad`/`onFocus`.** Do only what the first screen needs; trigger the rest
  from the screen that needs it. A long `onLoad` chain delays the first usable screen.
  Sync tiers and the context gate are in the `jigx` skill's `sync-orchestration.md`.

## Fix order

1. Critical: DS-01, DS-02, DS-04, CO-02, CO-04, then DS-03 with its caveat.
2. Sync scope and start-up work.
3. Shared expressions, `jig.list` for long lists, simpler `when`, trimmed `entities`,
   less state, smaller jigs.
4. Structural: splitting heavy screens, precomputing joins at sync time.

Measure the screen before and after each step (`run-and-track.md`) and stop when it is
fast enough. Structural fixes come last.
