# Acumatica REST

## Standard REST Function Rules

- Use `DATA_PROVIDER_REST`.
- Use `useLocalCall: true`.
- Use PUT for create/update unless the endpoint explicitly requires another method.
- Compose the URL from the actual config contract. For a full absolute endpoint
  without a trailing slash, use `{acumaticaURL}/EntityName`; do not blindly prepend
  `https://`. Verify the resolved URL and slash handling before the request.
- Use `accessToken` header parameter with `type: acuerp` and `value: acuerp`.
- Pass `$expand` as an optional query parameter when nested response data is needed.

Example parameter conventions:

```yaml
parameters:
  accessToken:
    location: header
    required: true
    type: acuerp
    value: acuerp
  acumaticaURL:
    location: path
    required: true
    type: string
  $expand:
    location: query
    required: false
    type: string
```

## Config Datasource

Use a global datasource that selects the config row:

```text
=@ctx.datasources.data-select-config.data.acumaticaURL
```

Do not hardcode URL values in actions or functions.

Resolve app/screen datasource expressions in the calling action's `parameters`.
REST functions run in an isolated context without the screen's `@ctx.datasources`;
do not put a datasource expression in a function parameter's default `value`.
Declare URL parameters as required strings, pass their evaluated values from each
caller (including batch/retry actions), and forward the OData URL explicitly into
nested guard functions. Match the datasource's SQL result shape: projected columns
are direct fields; use `.data` only when the query selects a `data` object.

## Payload Shape

Acumatica REST fields are usually wrapped:

```json
{
  "CustomerID": { "value": "ABC" },
  "CustomerName": { "value": "ABC Customer" }
}
```

Strip Jigx-only fields before sending:

- local UI state
- completion state
- PDF stale flags unless Acumatica explicitly has that field
- sync metadata such as `Remote`
- local-only file URLs

For new records, strip fields Acumatica assigns when required by the endpoint. For
updates, include the remote `id` or key fields needed to identify the record.

## Queries In Functions

Preferred outbound functions receive only the record ID and re-query the local table
when the command runs. This avoids stale screen snapshots:

1. action passes `{ id, remote }`
2. function query selects latest local row
3. input transform builds the Acumatica JSON body
4. function executes
5. operations update local data

## Error Handlers

Every Acumatica REST function should have standard handlers for:

- authentication
- permission denied
- validation errors
- not found
- timeout
- throttling
- empty or malformed response
- server unavailable

Errors must be visible to the user through a retry/error surface or command queue
status. Do not let failed submit actions disappear silently.


## Local REST Continuation

The mobile local REST provider shallow-merges the continuation into the function
configuration. A continuation `parameters` map therefore replaces the complete
parameter definition map. Include authentication, path and all query definitions
on every page, not just the offset. Test a second and third generated request,
including OAuth, URL, filters, ordering and pagination.

Do not depend on screen datasource refresh timing for a filter whose identity was
just synchronized. Resolve authenticated identity in the request flow and validate
it before constructing an owner filter. Keep old cached records until the first
replacement response succeeds.

## Internal REST Function Parameters

Declare local-only IDs, filenames used by result operations, and forwarded config
as `params.output(name, { type: 'string', required: true })` when they are not part
of the HTTP request. The runtime keeps them in `@ctx.parameters` but excludes them
from request construction. Declaring such values as body parameters on a GET
causes request generation to fail unless there is an input transform. Do not add
a dummy GET body to work around this. PUT functions can retain their explicit
input transform, and actual upload file parameters remain in the body.

## Guard Script Context

The mobile guard evaluator omits `metadata.solutionId`, so solution JavaScript
helpers are unavailable in guard result/when/parameter expressions. Compute custom
validation in the called function's `output` (which has solution metadata), then
return a typed result such as `{records, allowed}`. Use native JSONata in the
outer guard and require `allowed = true` plus the expected records type. Keep
error handling fail-closed. Guard table operations do have script metadata.
Test the generated guard without registering solution functions, and test the
called output with its functions registered.

## Validation Must Stop the Request

Do not rely solely on throwing inside an input-transform helper. The mobile
expression evaluator can catch the error and return null; the REST provider may
then serialize `null` and send it, producing an unrelated server parsing error.
Prepare and validate the payload in the preflight function's script-enabled output.
Return a typed result such as `{allowed, payload, validationMessage}`; the outer
native guard requires `allowed=true` and an object payload before HTTP. The input
transform consumes that prepared payload. Keep local field-validation failures
separate from concurrency/recovered-create failures so they do not set NeedsReload.
Test both new and existing unchanged records with invalid selections and assert
that no write occurs and the draft remains editable.
