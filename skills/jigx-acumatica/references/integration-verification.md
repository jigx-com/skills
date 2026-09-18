# Verify with data populated by the app

## Before implementation

Use the screen KB with the confirmed release before designing mappings. Keep a
compact project contract: screen/container, dependency, stored key, actual endpoint
field/type, lookup source/restrictions and live evidence. The KB supplies screen
logic, not proof of tenant data or endpoint customization. Resolve computed and
unresolved selectors on the instance before treating them as verified.

## App-equivalent contract test

The AI should complete this before handing over a new integration, using available
authorized API tools or an authenticated test harness:

1. Fetch actual lookup rows from the same source and connection/tenant as the app.
2. Normalize those responses with the app's actual mapping; populate the local
   header and children as the form would. Include dependent/optional fields the
   delivered UI supports, especially contact/location, pricing and discounts.
3. Evaluate the generated queries, guard outputs and input transform using the
   runtime's context, types and error semantics. Do not handcraft a simpler payload.
4. Verify selected ids/codes against the writable endpoint (or its expanded parent).
   The DAC id, REST key, business code and display name are not interchangeable.
5. Within authorized test-write scope, execute the generated request against a
   designated record, read it back, and compare persisted header/child values and
   returned identifiers. Record which optional branches remain unverified.
6. Test failures with representative fixtures: absent config/identity, invalid
   selection, HTTP rejection, empty results, pagination and uncertain-create retry.
   A rejected transform must prevent HTTP rather than send null or partial data.

A successful GI read, token presence, preflight200, queue insertion, or payload
omitting a field does not verify a working submission. Confirm OAuth through an
authenticated read. Preserve full path/auth/query definitions on continuation and
forward resolved config into guards; do not evaluate screen datasources inside
isolated functions.

## Cache and lookup identity

Apply account scope, active/type restrictions and per-user rules using the same
connection as writes. Compare real keys to REST entities, not just display labels.
If sources disagree, investigate; do not silently remap by name. Mark refreshed
source/key versions when replacing a lookup so old cache rows cannot be selected
or sent. Preserve existing draft choices and require explicit reselection where
identity is uncertain.

## Evidence and boundaries

Persist sanitized request/response fixtures and the build identity, screen/release,
selected lookup keys, expected/observed remote values and open verification gaps.
Never persist tokens. Inspect nested error field paths, not only the root message.
Use designated test records within existing authorization, preserve user drafts,
and do not silently change chosen business values to make a test pass.

Finish with the generic Jigx device journey. API round trips should remove most
integration uncertainty before device handoff; they cannot prove mobile controls,
queue timing or action visibility. State both kinds of evidence separately.
