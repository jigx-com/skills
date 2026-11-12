# Acumatica Probe

A safe, read-only check of a customer's Acumatica test environment against what a Jigx
solution needs: endpoints, entities, fields, generic inquiries and access rights, per
company (tenant). The result is a list of what the customer's admin must change.

A manifest says what the solution needs. `scripts/probe/probe.mjs` checks it, one company
at a time, using only GETs. The owner runs it in their own terminal. The agent drafts the
manifest, reads the report and writes the admin list.

Use it before building or unblocking a phase, and whenever the question is "is the
endpoint/inquiry there", "what is missing in Acumatica" or "what do we need from the
customer's admin". `acumatica-discovery.md` covers the channels and session rules the
probe follows.

## Hard Rules

- **The owner runs it.** Never run the probe with credentials yourself. Never look for
  credentials in `.env` files, transcripts, solution settings or anywhere else. The script
  prompts for the password with echo off and never writes it.
- **Read only.** The script sends only GETs, plus `entity/auth/login` and
  `entity/auth/logout`. It refuses any other method. Do not add a write, an action or a
  PUT-style "read". For an entity that is only reached by PUT, check its schema
  (`"read": null`).
- **One failed login at most.** The first login is a check on its own. A wrong password
  stops the run there, and any later failed login stops it too, so the user cannot be
  locked out. Every session that logs in is logged out, on failure too.
- **Test environments only.** A manifest whose `environment` is not `"test"` makes the
  owner type the host name before anything is sent. Never set `"test"` on an environment
  you have not confirmed with the owner.
- **Reports stay local.** Sample rows are customer data. The report goes next to the
  manifest under the repo's local `docs/`. It is never committed, pasted into a ticket or
  copied onto the admin list.
- **Fetching the public login page is fine.** It lists the instance's companies and needs
  no credentials.

## Workflow

1. **Pin the target.** Settle the instance root (for example
   `https://host:port/Instance/`), whether it is a test environment, and which companies
   to probe. Confirm these with the owner.
2. **Draft the manifest** from what the solution actually calls:
   ```sh
   node <skill>/scripts/probe/manifest-from-solution.mjs <build/output.json | solution folder> --base <url> --name <solution> > docs/probe/<solution>.json
   ```
   Then edit it. The format is in `acumatica-probe-manifest.md`:
   - `expect` lists the fields the solution's payloads will write, such as
     `Details.ManualPrice`. A field missing from the endpoint fails the write later.
   - Use `tally` for "which values occur, and how often", and `sample` plus a
     `filter`/`select` for an open question that a few rows can answer.
   - Add the endpoints and inquiries that later phases will need, not only today's.
   - Use `locate` for an entity whose endpoint is unknown or failing.
   - List anything under `unmatched` by hand, or drop it.
3. **If you changed the scripts, run `node <skill>/scripts/probe/selftest.mjs`.** It drives
   the probe against a local fake Acumatica. It checks the GET-only rule, the one-login
   stop and the logouts, and that the password never reaches the report.
4. **Hand over the command.** The owner runs it in a separate terminal:
   ```sh
   cd <repo>
   node <skill>/scripts/probe/probe.mjs docs/probe/<solution>.json --user <acumatica-user>
   ```
   Company names often contain a backtick (``Contoso`s North``). Leave the companies to
   the manifest's `companies.match`, or pass `--companies '…'` in single quotes. Wait for
   the owner to say the report is written. Do not poll for it.
5. **Read the report.** `probe-<name>-<date>.md` is for reading and `.json` for diffing two
   runs. Classify every finding with `acumatica-probe-results.md` as one of:
   - cleared: it works;
   - our change: we can work around it in the solution;
   - admin change: rights, publish, expose, add a field;
   - question for the customer.
   Remember that the probe user's rights are not the reps' rights.
6. **Write the admin list** as a shareable page:
   - a one-sentence lead naming the instance, user, date and companies;
   - **Changes needed**: a table of #, change, companies, what the probe found, and a
     status;
   - **Questions**: a table with an empty Answer column;
   - **Already in place**: one short paragraph, so nobody redoes it.
   Findings only: no sample rows, credentials, emails or internal file paths. Keep the
   page private until the owner shares it.
7. **Record the outcome in the repo.** Update the solution's open questions and blockers,
   and any recorded fact about the instance that changed. Rerun the probe after the admin
   acts and diff the two `.json` files.

## Scripts

- `scripts/probe/probe.mjs`: the probe. Node 18+, no dependencies.
- `scripts/probe/manifest-from-solution.mjs`: drafts a manifest from a Core SDK
  `build/output.json` or a standard solution folder.
- `scripts/probe/selftest.mjs`: the safety and report checks against a fake instance.
