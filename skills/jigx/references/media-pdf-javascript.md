# Media, PDFs, And JavaScript Functions

## Media Fields

For photos and signatures:

- store a local file URL in dynamic data
- save file metadata such as filename, mime type, parent ID, and created time
- keep media optional unless business rules require it
- support delete and thumbnail display where useful
- guard every preview/share/generate flow against missing local files

Device-local file URLs are not stable across devices. If a user switches devices,
files generated or captured on the previous device may not exist.

## Photos In Parent-Child Flows

Use the same parent-child ID pattern as line items:

- parent ID is generated before entering the parent workspace
- photo ID is generated before opening capture/select action
- photo row saves `id`, `parentId`, `fileUrl`, `filename`, and status fields
- photo list/gallery datasource filters by `parentId`

## JavaScript Functions

Use `app.script()` for logic that is too complex or fragile for inline JSONata:

- HTML generation
- complex formatting
- code generation from multiple fields
- deterministic validation helpers
- data transformation

Keep the JavaScript function pure where possible. It should accept data and return a
string or object without needing hidden global state.

## PDF Generation

A robust PDF flow:

1. Build HTML through a JavaScript function.
2. Save the generated HTML to a debug dynamic table when troubleshooting.
3. Generate the PDF from HTML.
4. Save the generated file path and generated timestamp.
5. Mark the PDF as not stale.
6. Show user feedback.

If the PDF is stale, warn before sharing or submitting. Offer to regenerate.

## HTML/CSS Constraints

PDF engines used in mobile apps may not match desktop browser printing. Prefer simple,
stable CSS:

- avoid complex overlapping backgrounds
- avoid heavy gradients where PDF viewers render inconsistently
- use explicit dimensions for page-like layouts
- avoid base64-embedding large images because it hurts performance
- prefer reliable remote URLs or local packaged assets where supported

For multi-page, slide-like PDFs, use explicit page containers and print page-break CSS.
Still test on target devices because mobile PDF generators may not honor all browser
print behavior.

## Missing File Guard

Before previewing or generating from local files:

- handle empty file path
- handle file path from another device
- show a "not generated yet" placeholder or alert instead of failing the screen

