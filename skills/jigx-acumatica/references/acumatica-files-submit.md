# Acumatica Files, PDFs, And Submit

## File Upload URL

Acumatica file uploads usually use a `files/...` endpoint, not the entity endpoint.
The URL includes the entity screen path and the remote parent record ID or NoteID.

Example service order pattern:

```text
https://{acumaticaURL}files/PX.Objects.FS.ServiceOrderEntry/ServiceOrderRecords/{recordId}/{filename}
```

Confirm the path from:

- Swagger if it includes file links
- an actual entity response `_links`
- a known Acumatica upload URL for that entity

Ask for the full upload URL when the path cannot be derived safely.

## Upload Function Rules

- method: PUT
- `accessToken`: header, `type: acuerp`, `value: acuerp`
- `acumaticaURL`: path
- `recordId`: path, remote Acumatica `id` or `NoteID`
- `filename`: path
- `file`: body
- conversion: `local-uri` to `buffer`

Example conversion:

```yaml
conversions:
  - property: file
    from: local-uri
    to: buffer
```

Let the conversion supply the binary `file` body. An `inputTransform` that returns
`@ctx.parameters.file` can replace those bytes with the literal `file:///...` URI;
Acumatica may still respond with 204 and list the attachment. For a critical upload,
download the Acumatica attachment and compare its header, size, or hash with the
device file. A successful HTTP status alone does not prove the right bytes arrived.

## Parent Then Files

Files require the remote parent ID. For locally created parents:

1. save the parent and file rows under a stable local parent ID
2. sync/create parent in Acumatica
3. store the returned Acumatica ID/NoteID separately and resolve it for the upload URL
4. release file queue rows that were waiting for the remote parent ID
5. map over file rows and upload one by one

Keep local file relationships and queued references on the stable local ID. If an
existing app deliberately rekeys local rows, update every dependent row and queue
reference together before releasing uploads.

Record which business entity owns each file. An opportunity attachment goes to its
opportunity, while a quote attachment goes to its quote; a shared screen or parent
workspace does not change the Acumatica upload target. Upload only after that exact
owner has a confirmed remote ID, during the explicit Sync/Price/Submit flow that
includes the file. Keep files from a copied record separate unless copying them is
an explicit product requirement.

There is no batch media upload pattern. Use `executeEntities` map behavior to upload
files one at a time.

For offline-capable apps, use a file queue:

- `notStarted` for uploadable files
- `waitingParent` or `parentTmpId` while the parent or child still has a local ID
- `uploading` while an upload call is active
- `failed` for user-visible retry
- `succeeded` when the upload is confirmed

Drain data operations before file uploads. Parent create/update responses should save
the remote ID before the file queue starts. Recovery should inspect runtime
sync status before retrying an `uploading` file; if the runtime status shows success,
mark the file row succeeded instead of uploading again.

## PDF Attachment

When attaching generated PDFs:

- verify PDF exists on the current device
- regenerate if missing or stale
- save the new local PDF path
- upload after the service order exists remotely
- retry failed upload commands through the command queue

Do not treat a PDF path saved on another device as valid.

## Service Order Lines

When converting configured items into service order detail lines:

- each configured door becomes its own line
- each configured opener becomes its own line
- generated internal configuration code can go into the detail note field when required
- keep item code mapping explicit and centralized
- keep price, tax, and total per line item, then roll up to quote total

Do not send internal-only generated codes to user-facing PDF output unless the business
explicitly wants them shown.
