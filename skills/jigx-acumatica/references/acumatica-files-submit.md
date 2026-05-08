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

## Parent Then Files

Files require the remote parent ID. For locally created parents:

1. save parent locally with temporary ID
2. sync/create parent in Acumatica
3. replace the temporary ID in local file rows with the remote Acumatica ID/NoteID
4. map over file rows and upload one by one

There is no batch media upload pattern. Use `executeEntities` map behavior to upload
files one at a time.

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

