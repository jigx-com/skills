# Rig tools (any Jigx solution)

These ship in the `scripts/` folder of the installed `jigx-mobile-testing` skill (project
`.claude/skills/` or global `~/.claude/skills/`). Customer-specific rigs (fixtures, form fillers
for one customer's forms) stay in that solution repo's `docs/test-knowledge/tools/`.

| Tool | What it does | Host |
|---|---|---|
| `jigx-validate.js <files…>` | YAML parse + every `=` expression through the real JSONata parser. Exit 1 on any failure; use before commit/publish. | node (uses `@jigx/core-sdk`'s yaml + jsonata; `JIGX_NODE_MODULES` overrides) |
| `adb/pair.sh <pkg> '<deeplink>'` | Force-stop, fire the `jigx_debug` deeplink with `-p <pkg>`, tap ENABLE. | zsh |
| `adb/dump.sh <out.xml>` | uiautomator dump to the host, 20 s timeout (a dump can hang mid-scroll). | zsh |
| `adb/nodes.py <xml> [regex]` | Labelled nodes (text / content-desc) with bounds; marks scrollables (`HorizontalScrollView` etc.). | python3 |
| `adb/tapnode.py <xml> <label> [--dry]` | Tap the centre of a node by text / content-desc. | python3 |
| `uidrive.py` | Form-driving library: `typed()` (escapes spaces), `bring2()` (centre a field), `pick()` (dropdowns), counters. Import from a scratch script. | python3 |
| `logscan.js <state.json>` | Summarise a captured jigx_debug event window: totals, error types, JSONata/Error-Boundary hits. | node |
| `hold-proxy.py` / `kill-proxy.py` | CONNECT proxy that fails or holds only the hosts you name (`KILL_HOSTS`, `HOLD_HOSTS`, `SLOW_HOSTS`), so the app stays online while the ERP call fails for real. Empty lists = logging proxy for host discovery. | python3 |
| `hold-and-kill.py` | Lost-response repro: tap, arm the hold on the Nth request, force-stop while the response is held. | python3 |
| `throttle-proxy.py` + `bad-connection.sh` | Whole-link latency / bandwidth / loss. Note: throttling everything makes the app think it is offline. | macOS |
| `throttle-proxy.js` + `bad-connection.ps1` | Same, Windows (node, `adb reverse`, `-SlowHosts` mandatory, keep the .ps1 pure ASCII). | Windows |
| `expression-guards/*.mjs` | Inventory every `$uppercase()` and `< <= > >=` site in a solution, classify operand types, prove a guard pass never throws (`enumerate` → `classify` → `make-inventory`; `probe-types`, `prove-fixes`). | node |

## Rules that make these work

- Set any device proxy (`adb shell settings put global http_proxy <host:port>`) **before** the
  cold start; an app already running keeps going direct. Clear with `:0`.
- Scope throttles/failures to the ERP host. The jigx_debug bridge rides the same network and
  rarely re-pairs once it drops.
- `adb shell wm density <n>` emulates smaller phones (1080 px at 480 ≈ 360 dp, 540 ≈ 320 dp) but
  recreates the app back to Home and drops the debug query channel; always `wm density reset`.
- Start horizontal swipes below y≈300 px or they open the notification shade.
