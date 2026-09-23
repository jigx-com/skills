# Acumatica Reads And The Sync Contract

How a read should be declared so it syncs correctly, loads on demand when it must, and
never silently returns a short slice. Package-agnostic: these rules apply to any read
the solution performs, whether it lands in a data package or a direct function.

## Prefer Generic Inquiries For Lists

For any read that pulls many rows to the device, use a Generic Inquiry.

- Inquiries export reliably and expose OData, so a filter and a delta cut push down to
  the source. Screen exports do neither and can regress to `<NEW>` placeholder rows
  when a schema refreshes.
- The `JIGX*` inquiries on Jigx-prepared instances usually carry
  `LastModifiedDateTime`, which is what delta sync keys on.
- A read that returns duplicate row identities or placeholder junk is a broken read,
  not a quirk to tolerate.

## Create An Inquiry When None Exposes The Data

When no screen or inquiry exposes what a lookup needs, create one. Typical reasons: a
per-parent population that lives in a join, a stored value column nothing exposes such
as `ContactID`, or a missing delta timestamp.

- Describe the design in business language and get explicit agreement first. It writes
  configuration to the customer's system.
- Table names are full DAC class names, such as `PX.Objects.PO.POOrder`. Relations use
  the alias, `POOrder`; join fields are alias-qualified, `POOrder.OrderType`.
- An invalid table or field name fails with a precise error and nothing persists, so
  iterate.
- Result field names become the column captions: `VendorID` becomes `Vendor`,
  `LastModifiedDateTime` becomes `LastModifiedOn`. Fetch the created inquiry's schema
  and use the captions it reports.
- Expose the inquiry through OData so delta sync can push down.
- Verify it with a schema fetch and a read before anything depends on it.

## Delta Sync By Default

Whenever a list exposes a last-modified column, include it in the read and declare it
as the modified field.

- Devices then fetch only rows changed since their newest local timestamp. On a large
  table that is the difference between a sub-second refresh and a slow full pull.
- Push the cut to the source where the API supports it. Acumatica inquiries do.
- Prefer sources that expose such a column. Skip delta only for tiny static lookups.

Delta has one trap on addressed reads. A single watermark over a whole table advances
to the newest row of whichever address was fetched last, and the next address is then
told to skip everything older, so it arrives short and stays short. Keep the watermark
per address, or do not use delta on a read that takes an address.

## Reads That Need An Address

Some reads cannot run until the caller says where to look, and some should not run
until a parent has been chosen.

- A required address: the read has no meaning without it. Mark it required and say in
  its description what it addresses.
- A parent-driven filter: the warehouse for an inventory list, the customer for its
  contacts. This is the only mechanism for a table too large to sync at startup, and it
  is a legitimate second reason to mark a parameter required.

A required parameter changes how the read is generated. It becomes on demand: the
device calls it with arguments when it needs an answer instead of syncing the table at
startup with nothing supplied. Leaving it optional produces a startup sync that calls
the read bare, which either fails or fills the table with nothing.

A missing required parameter at runtime is loud, not silent. The runtime rejects the
action before its own guard runs and raises a toast with the raw message. Design the
form so the parent value exists before the dependent read fires.

## Row Identity

When a read returns join or detail rows where no single column is unique, such as
vendor per project or order lines, set the row key to the column combination that is
unique. The device syncs by that identity, and without it rows sharing the first
column silently collapse into one.

## The Row Cap Is Silent

A read is capped at a fixed row count with no truncation signal, and delta only ever
sees rows newer than a truncated first sync. A large table synced whole is therefore
never fully fetched. Treat a list that could exceed the cap as an addressed or
parent-driven read, or page it explicitly.

## Where The Screen Read Fits

Use a screen read only when no inquiry exposes the data, and consider creating an
inquiry before settling for one. A screen read is right for a single record and for
the record's own detail lines; it is the wrong tool for a list.
