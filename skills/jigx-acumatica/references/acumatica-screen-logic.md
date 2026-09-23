# Acumatica Screen Logic

Read a screen's built-in logic before designing tables, lookups, or a save on it.
This is what makes dropdowns show the right rows, saves carry the right fields in the
right order, and workflow actions fire at the right moment.

Use `jigx-acumatica-kb` to query the knowledge base. This reference explains what the
answer means, how far to trust it, and what it cannot know.

## Release First, Never Guessed

Screen logic differs between releases, so the knowledge base has no default release.

- Derive the release from the instance build, or ask. Builds are `YY.Mxx.NNNN`; the
  first digit of the minor part is the release number.
- A release the knowledge base does not cover is not a reason to ask again and not a
  reason to substitute a neighbouring release. Its cascades and combos may differ.
  Design that screen from the live schema instead.
- An instance that reports no usable build is the one case to ask the user.

## What The Knowledge Base Carries

The knowledge base is generic per release. It was built offline by reading a release's
installed binaries and UI sources, never a customer instance, so it needs no access to
the customer's system.

Per screen it holds:

- the container tree: header form, detail grids, and each grid's parent link
- each field's dropdown row source and restrictors, the default, enable and visible
  rules, required-ness, and whether the key is auto-numbered
- the cascade graph: which fields depend on which, both declared and recomputed in code
- combo values with their internal value and label
- lifecycle actions, what each creates or redirects to, and when lines are copied
- the workflow state machine: status field, states, allowed actions per state, field
  states per workflow state, and transitions with their conditions

Container roles decide how a container is written:

| Role | Meaning | Write |
| --- | --- | --- |
| `header` | The document itself | Sent with the save |
| `inlineGrid` | Lines typed with the document | Sent with the save |
| `linkGrid` | Records the screen creates through an action on another screen | Never typed; created by its `createdBy` action |
| `dialog`, `readOnly` | Not persisted | Do not write |

A container whose view filter compares to `Current<Header.field>` follows that header
pointer. Its rows belong to the record the pointer names; an opportunity's Details are
the primary quote's lines. Write them to that record and re-read them when an action
moves the pointer.

Field scope matters for lookups:

- `scope=perUser` lists depend on who is signed in: subordinates and delegates,
  ownership, anything reading the current user's contact or user id. Never feed such a
  field from a global sync of the table. Default it to the signed-in user or build a
  per-user lookup that reproduces the rule, then verify against the screen's selector.
- `scope=restrictionGroups` lists are filtered by the connection's own restriction
  groups, and a global read is correct.

Save order from the plan: leave an auto-numbered key blank and read it back, send header
fields in dependency order, then inline grid rows, save, then run any after-save
workflow action as a separate step. Never send a `neverSend` field. Honour
`requiredToSave: yes`; `unknown` means be able to supply a value. A screen-level
override of a rule wins over the DAC's own attribute.

Verified facts come first when the answer carries them. They were checked in the source
and on a live instance of that release and carry a recipe. One example: create an
opportunity's first quote before the opportunity has lines and put the lines on the
quote, so everything stays synchronous; an action that answers `202` needs polling the
Jigx runtime cannot do, which makes it a last resort.

## Reconciling With The Customer's Instance

When the project has a live connection, reconcile the knowledge base with the
customer's screen rather than trusting either alone. Live wins for structure; the
knowledge base wins for logic on fields both know. Every field then carries provenance.

| Provenance | Meaning | Treatment |
| --- | --- | --- |
| `standard` | On the screen exactly as the release ships it | Apply the knowledge base logic |
| `standardRelabelled` | Same field, changed caption | Logic unchanged; address it by its live name |
| `customerAdded` | A `Usr*` custom field | Schema only; never invent cascades or combos |
| `liveOnly` | Present live, unknown to the knowledge base | Schema only |
| `kbOnly` | In the knowledge base, absent live | Hidden by state, a dialog the API does not persist, or removed by customization; confirm live before placing on a form |

The reconcile also reports the customizations published on the instance and which item
types touch this screen. A published `Automation` or `Workflow` item means the workflow
may differ from the release's; confirm it live before designing around states. Published
code or DAC extensions with no screen can change any screen's logic; confirm cascades
and defaults live. Nothing from a tenant is ever written back to the knowledge base.

## What It Cannot Know

Be explicit about these gaps instead of guessing:

- Combo values the knowledge base marks unresolved: read them from the instance.
- A row source marked unresolved or computed in code: confirm the population on the
  instance.
- Tenant data, tenant customizations, and per-user visibility: only the live instance
  answers.
- Screens whose logic lives outside the core business assemblies, such as Field
  Services, Construction daily field reports, or Manufacturing, may be thinly covered.
  Treat a screen with few fields carrying logic as unverified, not as simple.
- Shared contact and address views imported from elsewhere can show no fields.

## Mapping To A Solution

- One lookup per selector, reproducing the row source filter, with the selector's
  parameters as inputs.
- A dependent dropdown is a lookup filtered by the parent's stored value, declared on
  both fields so the dependency is visible to whoever maintains the form.
- Workflow status is moved by actions, never written as a field. One action per
  transition, named after the transition.
- Fields the workflow disables or hides in the current state are not written in that
  state.
- Keep a compact mapping per writable field: knowledge base key and restrictions,
  actual endpoint field and type, lookup source, and instance evidence. Leave unresolved
  entries explicit rather than filled in by assumption.
