# Solution Model — <customer> (<solution-name>)

Stable facts the agent must not re-derive every run. Fill each section with the user before the
first test; update it when the UI or build changes (Step 5 of the skill).

## App / session identity
- Solution name (index.jigx `name:`): `<solution-name>` — the debug session binds to THIS line.
- Branded app / `targetAppName`: `<branded app name>` — never empty.
- Android package: `com.jigx.<app>.android`
- Org (jigx_debug `select_debug_organization`): `<org name>` `<org uuid>`
- Login / test user: `<email>` (password from the owner, never stored)
- Test data that works: `<customer / branch / record ids>`
- Device: `<model, resolution, density>`; adb serial `<serial>`

## Org → tenant map
| Org / email domain | Tenant | ERP URL | Notes |
|---|---|---|---|

## Device DB table reference
| Table | Holds | Key columns |
|---|---|---|

## Navigation map
How to reach each screen under test from a cold start (taps, ready-signal at each hop).

## Selector map (per screen)
### Screen: <name> (`<jig file>`)
| Element | Locator (text / content-desc / resource-id) | Notes |
|---|---|---|

## Ready-signals (per screen)
| Screen | Ready when |
|---|---|

## System-dialog handling (OS UI, per Android version)
