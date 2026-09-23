# Acumatica Discovery And Channels

How to probe an Acumatica instance and decide which API path a read, write, or action
should take. Learned from how Jigx Connect discovers and integrates with Acumatica.
Applies whether the solution calls Acumatica directly or through a data package.

## Three Channels, Chosen By Job

Acumatica exposes three usable channels. Pick by what the job is, not by habit.

| Job | Channel | Why |
| --- | --- | --- |
| Sync a list to the device (customers, open orders, price list, any lookup with many rows) | Generic Inquiry, exported over OData | Built for bulk paging and delta pushdown; carries `LastModifiedDateTime` on the `JIGX*` inquiries |
| Understand a screen, discover field/view/action definitions, or configure Acumatica from an agent/server process | Screen SOAP/UI protocol | Follows the screen's own logic and exposes the definitions needed to build GIs, endpoints, and mappings; not executable by Jigx mobile |
| A mobile-supported single-record read/write, detail write, or entity action | Contract-based REST | Self-describing through Swagger; use screen probe facts to create or choose the right endpoint when `Default` is insufficient |

## SOAP/UI Probes Versus Jigx Mobile Runtime

Acumatica's screen SOAP/UI protocol is an agent-time discovery and configuration
instrument, not a Jigx mobile runtime target.

- Jigx mobile cannot execute SOAP, WSDL, or SOAP-session calls. Do not generate mobile
  functions or actions that post SOAP envelopes or depend on SOAP sessions from the
  device.
- Use SOAP/UI probes to understand screen definitions and behavior: containers, views,
  DAC field names, actions, required fields, workflow affordances, and the shape needed
  to create Generic Inquiries, custom REST endpoints, or customization packages.
- Map those probe results into mobile-supported surfaces: Generic Inquiry/OData for
  list and lookup sync, contract REST endpoints for record reads and writes, supported
  file/action endpoints, or an explicitly available server-side bridge when a required
  workflow has no REST/GI equivalent.
- An AI agent running outside the mobile app may use SOAP/UI directly while discovering
  or configuring Acumatica: adding endpoints, creating Generic Inquiries, importing or
  publishing customizations, and configuring screens. Treat these as admin-time
  operations: name the instance and environment, get explicit agreement for mutations,
  keep sessions bounded, and log out on failure too.

Rules of thumb:

- Many rows go through a Generic Inquiry. The screen and REST layers are not built for
  bulk and burn API load when used that way.
- One record, an update, or an action goes through REST for Jigx mobile. Use the screen
  SOAP path only from an agent/server process that is explicitly part of the integration
  architecture.
- When REST is missing a field or action, use SOAP/UI facts to create or choose a custom
  endpoint, create a supporting Generic Inquiry, or identify that a server-side bridge
  is required.
- A data-entry screen export can regress to `<NEW>` placeholder rows when the schema
  refreshes, and it cannot push a filter or delta cut to the source. Prefer an inquiry
  for every list read; consider creating one before settling for a screen export.

## Discovery Sequence

1. Test the connection with a cookie login against `/entity/auth/login` and log out.
   The login name is `user@Tenant`; a JSON `tenant` key is silently ignored on some
   instances. A `500` here is almost always an account problem (no API access right,
   wrong tenant, or no free API session), not a URL problem.
2. Read the build from `/entity` and derive the release. Builds are `YY.Mxx.NNNN`, and
   the first digit of the minor part is the release number: `25.101.0153` is 25R1,
   `25.200.0248` is 25R2, `26.101.0225` is 26R1. Record the release before designing
   any screen integration; see `acumatica-screen-logic.md`.
3. Enumerate screens and inquiries from one Site Map export (screen `SM200520`,
   fields `ScreenID`, `Title`, `URL`, `Category`). Inquiries are recognizable by a
   `GenericInquiry.aspx` URL.
4. Fetch a screen schema only when a task needs it, and cache it. Load several
   uncached schemas inside one session rather than one login per screen.
5. For contract REST, list endpoints from `/entity`, then pull
   `/entity/<Endpoint>/<version>/swagger.json`. A customer may have built a custom
   endpoint exposing extended or bespoke entities; check for it before assuming
   `Default` is all there is.
6. Never send the user to the Acumatica UI or a portal to run discovery you can run
   yourself.

## What Each Source Can And Cannot Tell You

| Source | Gives | Does not give |
| --- | --- | --- |
| Screen SOAP `GetSchema` | Containers, fields, data views, DAC field names, actions | Combo values, selector filters, cascades |
| Contract REST Swagger | Entity fields and types, detail collections, invokable actions | Combo values, selector filters, cascades |
| Modern UI screen API `POST /ui/screen/{ScreenID}` with body `{"callback":null}` | Per-field combo `options` with `defaultValue`; selector `viewName`, `valueField`, `descriptionName`, display columns; grid column `required` | Business data; this is metadata only |
| The customer's data | Realistic values, which optional-looking fields are actually filled | Why a value is accepted or rejected |
| The screen knowledge base | The release's built-in filters, cascades, defaults, workflow | The tenant's customizations or data; see `acumatica-screen-logic.md` |

Combo values live in none of the schema sources. The UI screen API is the bounded,
metadata-only exception. Jigx mobile reads and writes still run through Generic
Inquiries/OData, contract REST, or an explicit server-side bridge; screen SOAP is only
for agent/server-side probing, configuration, or bridge code.

## Session And Licence Discipline

Every call sequence must end with a logout. An open session holds one of the
instance's concurrent API-user licences until it times out, and a customer instance
can be locked out entirely.

- Wrap login, work, and logout so the logout runs on failure too.
- Batch a multi-screen operation into one session: one login, many calls, one logout.
- Never hold a session across user think-time or between separate steps.
- A per-user OAuth bearer with the plain `api` scope runs SOAP and OData stateless,
  with no login or logout and no licence burn. Prefer it when the solution has one.
- When the instance reports `API Login Limit`, `CheckApiUsersLimits`, or
  `LicensePolicy`, the sessions are exhausted. Treat it as transient: wait and retry.

## Reading Acumatica Errors

Acumatica faults arrive as SOAP stack traces or nested REST error bodies. Translate
them before showing anything to a user, and keep the raw text in logs only.

| Raw shape | Meaning | Say |
| --- | --- | --- |
| `API Login Limit`, `CheckApiUsersLimits`, `LicensePolicy` | No free API sessions | Wait a minute and try again |
| `Invalid credentials`, `incorrect username or password`, `account is locked` | Credentials rejected | Check username, password, and tenant |
| `An error occurred during processing of the field X value V 'V' cannot be found in the system` | Selector miss: the value is not in that field's population | Choose an option from the list |
| `An error occurred during processing of the field X value V <sentence>` | Field-level rule | `X: <sentence>` so the user knows where |
| `'X' cannot be empty` | Required field | `X is required` |
| `Inserting 'Entity' record raised at least one error. Please review the errors.` alone | Rejected with no field detail | Check its required fields and business rules in Acumatica |
| `timed out`, `504` | Slow instance | Try again in a moment |
| `401`, `session expired` | Session gone | Retry; sign in fresh on each request |
| `403`, `not authorized`, `permission` | The API user lacks screen rights | Ask an administrator to check access |

REST error bodies nest field failures under `error` keys per field, including inside
detail rows. Walk the nested paths and keep them, so a failure on a line item names
the line rather than only the record. Strip exception class names, stack frames, and
wrapper sentences before showing the remainder; Acumatica often enumerates the allowed
values inside a fault, and those are worth keeping.
