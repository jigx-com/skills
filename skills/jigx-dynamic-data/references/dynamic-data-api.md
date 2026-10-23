# Dynamic Data through the Management API

What `ddcheck` relies on, recorded so nobody has to rediscover it. These endpoints are what the
VS Code Builder extension's Manage → Data screen and the `jigx_debug` MCP use; they are not a
published, versioned API, so re-verify after an extension or MCP update (a `dump` records the
observed shape in `inventory.json` under `_api_shape`).

## Base and headers

- Base: `https://<region>.api.jigx.com/v2.0` (`us-east-1`, `eu-central-1`, `ap-southeast-2`). The
  org's region is in the `jigx_debug` MCP login; `JIGX_API_BASE` overrides it.
- `Authorization: <IdToken>`: the raw token, **no `Bearer` prefix**. This is the detail most
  likely to be guessed wrong.
- `x-client-type: management`, `x-correlation-id: mgt:<random>`, JSON accept and content type.

## Endpoints

Let `D` = `{base}/data/organizations/{org}/solutions/{solutionId}/databases/default`.

| Call | Endpoint | Used by ddcheck |
| --- | --- | --- |
| Solution id by name | `GET {base}/strata/organizations/{org}/solutions/{name}` → `{solutionId, title, …}` | yes |
| Read rows | `GET {D}/tables/{table}/rows?limit=N&continuationToken=…` | yes |
| Batch merge | `PATCH {D}/tables/{table}/rows` body `{"<rid>": {"content": {…}}, …}` | `seed` only, after confirmation |
| Read one row | `GET {D}/tables/{table}/rows/{rid}` (404 when absent) | no |
| Single merge | `PUT {D}/tables/{table}/rows/{rid}/` body `{"content": {…}}` | no |
| Delete rows / table | `DELETE {D}/tables/{table}/rows` body `{"rids": […]}` / `DELETE {D}/tables/{table}` | no, never |

A published solution's full definition (to prove a publish) is
`GET {base}/strata/organizations/{org}/solutions/{name}?expandContent=FULL`.

## Shapes (observed)

- Rows page: `{count, hasMore, items}`; each item `{rid, content: {…}, createdAt, createdBy,
  updatedAt, updatedBy}`. `continuationToken` has not been seen on pages up to 500 rows;
  `ddcheck` follows it when present.
- PATCH values must be wrapped as `{"content": {…}}`; a bare content object is a 400
  ("does not match any of the allowed types").
- An absent table is a 404 on the rows endpoint, not an empty list.

## Traps

- Manage → Data saves edited flags as strings: `"false"` is truthy in JSONata and never matches
  `json_extract(...) = true` in SQL. Store JSON `true`/`false` (`ddcheck check` reports `bad type`).
- Dynamic Data row ids have a length cap (between 59 and 64 characters observed); longer ids are
  rejected with `400 Invalid rid`.
- The token's user is whoever last logged in to the `jigx_debug` MCP. Run `ddcheck whoami` first.
