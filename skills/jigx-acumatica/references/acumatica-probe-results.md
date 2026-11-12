# Reading a probe report

What a result means and who acts on it. "Admin" means the customer's Acumatica admin.

| Result | Meaning | Who acts |
|---|---|---|
| Login `500` on the check | An account problem: wrong password, no API access right, wrong company, or no free API session (`API Login Limit`). It is not a URL problem. | Owner checks the user. Wait and retry on a licence limit. |
| Login `500` on one later company | The user has no access to that company. | Admin, if that company is in scope |
| Endpoint listed under another casing (`JIGXMOBILE`) | Fine. Endpoint names are case-insensitive. | Nobody |
| `Endpoint X is not published` | The endpoint is missing on that company. | Admin publishes it, or we use another endpoint |
| Schema `404: Entity X not found` | The entity is not on that endpoint. Use `locate` to find where it is. | Us (wrong endpoint) or admin (add it) |
| Schema `403: You have insufficient rights to access the X (SCREENID) form` | The probe user's role lacks that screen. The screen ID is in the message. | Admin grants the role. Name the screen ID. |
| Schema `500: The type is not found: …` | A customization behind the entity is broken or unpublished. | Admin republishes the customization |
| `Missing fields` on an entity | The endpoint does not expose a field the solution writes or reads. | Admin extends the endpoint, or we change the payload |
| Inquiry `In OData list: no` and `404` | The inquiry does not exist, or is not exposed via OData. | Admin creates it or ticks "Expose via OData", or we read the contract entity instead |
| Inquiry in the list but `403: You do not have access to this entity set` | The inquiry exists; the role lacks access to it or to its screens. | Admin grants access |
| Inquiry OK but `Missing columns` | The inquiry lacks a column the solution needs. | Admin adds the column |
| Rows `0` | The read works but the company has no data. This can be correct (no salespersons set up). | Ask before treating it as a fault |

## Limits

- **The probe user is not the reps.** When the app signs in as each rep (per-user OAuth),
  the reps' roles need the same rights. An admin user passing does not mean a rep passes.
  Put "the app's role and the reps' roles" on every rights item.
- **Write rights cannot be read.** Creating a record or running an action (Remove Hold)
  would be needed to prove them. Put them on the admin list as a confirmation.
- **PUT-driven reads are schema-only.** Inquiries that take a body and
  availability calls take a body. The probe checks their schema and never calls them.
- **Test is not production.** A gap on the test database may not exist in production,
  and the other way round. Say which environment every finding comes from.
- **An entity the app reaches through a separate URL setting** (a second instance or
  endpoint held in solution settings) is only covered when the manifest names that
  endpoint, or `locate` finds it.

## Turning findings into the admin list

- **Changes needed:** one row per change, not per tenant. List the tenants in a column,
  and quote the error as the probe saw it, with the screen ID.
- **Struck off:** anything we can work around ourselves, such as reading the contract
  `Warehouse` entity instead of a missing inquiry. Mention it in "Already in place", so the
  admin does not chase it.
- **Questions:** anything only the customer can decide (hold or release, manual price,
  which field holds a value). Asking is cheaper than guessing.
