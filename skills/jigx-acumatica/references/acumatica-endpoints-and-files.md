# Acumatica Contract REST, Custom Endpoints, And File Attachment

Contract-based REST is the fallback channel: for an entity with no usable screen, a
field a screen will not expose, or a plain single-record read or write. Never the bulk
channel. This reference also covers creating an endpoint that does not exist and
attaching files to records.

## Discovery And Shape

- Endpoints: `GET /entity`. Each endpoint has a version; use the newest.
- Contract: `GET /entity/<Endpoint>/<version>/swagger.json`.
- Read: `GET /entity/<Endpoint>/<version>/<Entity>` with `$select`, `$filter`, `$top`,
  and `$expand`.
- Write: `PUT /entity/<Endpoint>/<version>/<Entity>` creates and updates with a keyed
  body.
- Fields are `{ "value": ... }` wrappers. Flatten on read; wrap on write. See
  `acumatica-rest.md` for the payload rules a solution's functions must follow.

Check for a customer-built custom endpoint before assuming `Default` is all there is.
It may expose extended or bespoke entities that `Default` lacks.

## Detail Collections

A REST entity's schema carries more than scalar fields.

- Nested collections are line-item arrays: a quote's `Details`, an order's `Shipments`.
  Write them with the header in one `PUT`. The server associates the lines and returns
  their identities. One document, one call.
- Editing lines: a row with its line id updates that line; a row with the id and
  `delete: true` removes it; a row with no id inserts. Omission is not deletion.
- Reading a record with its lines: a plain read drops nested arrays because they cannot
  fit a flat table. Flatten on the collection name to get one row per line, with the
  header fields repeated and the line id attached. Use expand instead when the nested
  data only needs to be present.
- Actions: side-effect operations such as `ConvertToOrder` or `PrepareSalesInvoice` are
  invoked with a `POST` to the entity's action path, the record key as the selector and
  other fields as parameters. A long-running action answers `202`; it needs polling the
  Jigx runtime cannot do, so prefer a synchronous design.

## Creating An Endpoint That Does Not Exist

When the customer needs an entity the `Default` endpoint does not expose, and no custom
endpoint has it, a customization package can add one. Sales Quotes on `CR304500` is
the classic case.

- Ground every field mapping in the live screen schema first: the exposed field name,
  its `*Value` wrapper type, the screen's data-view object, and the DAC field. A package
  built from guessed mappings fails on publish or silently maps the wrong column.
- The package is one `.zip` with `project.xml` at the root, declaring an endpoint that
  extends `Default` with a top-level entity per screen, its fields and mappings, any
  detail collections, and mapped actions.
- Publishing goes through the CustomizationApi with cookie auth: import the package,
  begin publish, then poll until the completion flag is set. Validation-only mode is a
  full dry run that changes nothing and is worth running first.
- Publishing modifies the customer's Acumatica and briefly puts the site in
  maintenance. Name the environment and instance, say plainly if it is production, get
  explicit agreement, and prefer the test environment first.
- If the API account lacks customization rights, hand over the `.zip` to install through
  `SM204505` with Import then Publish.
- If the publish reports a package error, read its log and fix the package.
- There is no delete or export API for customizations, and unpublishing is
  all-or-nothing across every published project. Never unpublish on a shared instance.
- After a successful publish, rediscover the endpoint so the new entity is known.

Not every screen grounds. Some CRM screens return an empty schema through the API. Say
so, and offer to specify the fields explicitly, use a different screen, or build the
entity through an explicit server-side screen bridge instead.

## Attaching Files To A Record

Attachment is create-then-attach. The record must exist first, and the attach takes
its key from the create.

- Path: `PUT /entity/<Endpoint>/<version>/<Entity>/<key>/files/<filename>` with a
  binary body.
- The key is the record's real key or note id returned by the create, never a local
  temporary id. See `acumatica-files-submit.md` for the queue shape that waits on it.
- Auth mirrors the read path: a per-user bearer when present, otherwise an explicit
  login, the call, then logout.
- A validation-mode attach proves the endpoint and entity resolve without writing. Use
  it before committing the first real file.
- Files stay separate from the record save. A single aggregate call can carry a
  document and its lines, but file endpoints need the remote id that call returns.
