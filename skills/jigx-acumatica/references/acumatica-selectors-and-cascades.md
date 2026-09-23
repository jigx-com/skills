# Acumatica Selectors, Cascades, And Value Semantics

Rules for feeding selectors correctly, finding cascades the schema will not name,
diagnosing a rejected value, and the value quirks that reject an otherwise correct
save. Every rule here was learned by proving it against a live instance.

## The Selector Stores A Column

A selector stores one specific column of its lookup entity, and that column is a
contract. A lookup that exposes the wrong column will show plausible rows and reject
every selection.

- Read the selector's `valueField` from the screen metadata. The lookup you ship for
  that selector must expose that exact column as its value.
- A selector storing `contactID` rejects the display code shown in its own dropdown.
  `acctCD` `KINLEARJOR` is not `contactID` `100904`.
- Display name, business code, REST key, and DAC id are not interchangeable. Compare
  real keys to the writable entity, not labels.
- If two sources disagree on an id for the same person or item, investigate. Never
  remap a draft choice by display name.

## Owner-Type Selectors Store ContactID

Project manager, approver, and owner fields use the owner attribute and store a numeric
`ContactID`. Standard employee screens and inquiries do not expose that column.

1. Look for an existing inquiry that already exposes `ContactID`. Jigx-prepared
   instances ship a `JIGX*` family of inquiries built for exactly this, such as
   `JIGXEmployee`. Check their schemas rather than guessing.
2. If none exists, propose creating one and get agreement first. A minimal design is
   `EPEmployee` with `ContactID`, `AcctCD`, `AcctName`, and `VStatus`.
3. Do not ship until an employee lookup round-trips against the selector.

## Finding Cascades

The schema will not say that one field depends on another. Two signals do.

- Column overlap: when a selector's lookup entity exposes a column matching another
  field on the same screen, that is a dependent dropdown. Task lookups carry the
  project id; cost codes relate to tasks. Include that column in the lookup read,
  declare a filter on it, and write the dependency into both field descriptions.
- The knowledge base names the dependency directly as `dependsOn` and `recomputes`.
  Prefer it when the release is covered; see `acumatica-screen-logic.md`.

Prove a cascade with one quick read with the parent value set. Prove the constraint
with one deliberate save that violates it: a real task from the wrong project must be
rejected. Then write the rule down so forms filter correctly.

## Diagnosing A Rejected Value

When Acumatica rejects a value your own lookup served, the value is fine and the
populations differ. Diagnose it one variable at a time.

1. Wrong column first. Check the selector's stored column against what the lookup
   exposes.
2. Probe the same value against a different parent record, and a different value
   against the same parent. If acceptance changes with the parent, the restriction is
   per-parent. If it never works, it is global: a flag, a class, or visibility.
3. Validation-mode saves are safe instruments. A rejected probe persists nothing. A
   successful commit creates a test record you note and clean up.
4. When the signal narrows it but does not name it, read the vendor's official
   documentation for that screen and selector. Restriction features are usually
   documented there. The live instance still outranks any document.

## Lookup Round-Trip Proof

The final commit test of any write must use selector values drawn from the lookups the
solution ships, taking a real row from each lookup and writing with its value column.
Values mined from existing records are for discovery only. They prove the write works,
not that the lookups feed it.

If a lookup-sourced value is rejected, the lookup is wrong: wrong column, wrong entity
population, or branch-scoped data. Fix or flag it. Never ship a lookup you have not
round-tripped.

## The Hardening Gauntlet

A write is not ready when its happy path works. It is ready when it survives what a
real form will do to it. After the first successful commit:

1. Submit with only the required fields. Forms omit everything optional, and hidden
   save-time requirements surface here.
2. Submit without the key. Creates must succeed; the system assigns ids.
3. For every enum, submit one bogus value and harvest the allowed list from the
   rejection.
4. For every cascading selector, submit a valid value that violates the cascade and
   confirm the rejection.
5. Re-run the final commit using only values from the solution's own lookups.
6. Expect the system to reveal requirements only at commit time: a required manager
   on a report, a cost code on every cost-bearing row. Treat each error as an
   instruction, add the field, wire its lookup if it is a selector, and retry.

## Mining Real Values Before Testing

Read a few existing records of the same type before testing a write. They show
realistic values, which optional-looking fields are actually filled, and what enum and
selector values look like in practice. Test with those, not invented data.

## Enums And Keys

- An enum has an internal value and a label. Write the internal value; show the label.
- Some enums encode units in their values. A time enum whose values are minutes stores
  `240` for `04:00`.
- Never invent an enum value. A field with no enum metadata needs its values proven
  another way.
- On auto-numbering screens the server replaces the submitted key and returns the real
  one. Leave it blank on create, read it back, and tell the user the created id.

## Value Semantics That Reject A Correct-Looking Save

| Field kind | Rule |
| --- | --- |
| Time fields | Time only; the date part is dropped. A span that crosses midnight is rejected |
| Span-checked fields such as working hours | Must fit inside their arrive and depart window |
| Durations | A numeric value parses as `HHMM` digits; `7200` means `72:00`, not two hours. Send `"HH:MM"` on the screen path, or the enum's internal minutes value. Contract REST takes integer minutes |
| Equipment on a project | Governed by the project's `Restrict Equipment` flag on `PM301000`. Off: any active equipment commits. On: only equipment listed on that project's Equipment tab. The rejection names the project field, not the equipment |
| Labour on a project | `Restrict Employees` works the same way |
| Any screen data | Branch-scoped. A lookup read may see a different population than the write will accept |
| Cached prices | Estimates. Let Acumatica calculate contextual pricing and discounts unless the user chose a manual override, and read the result back |

## Workflow Actions Versus Status Fields

State changes such as Activate, Complete, Hold, and customer-specific actions are not
fields. Writing a status value directly is either ignored or rejected. Create one
action call per transition, keyed by the record, and name it after the transition. If
a form shows a status dropdown, check whether the screen uses workflow actions before
writing any status field.
