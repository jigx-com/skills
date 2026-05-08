# Acumatica REST

## Standard REST Function Rules

- Use `DATA_PROVIDER_REST`.
- Use `useLocalCall: true`.
- Use PUT for create/update unless the endpoint explicitly requires another method.
- Use `https://{acumaticaURL}EntityName` as the URL pattern.
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

