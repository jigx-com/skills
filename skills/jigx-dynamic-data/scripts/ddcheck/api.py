"""Jigx Management API client for Dynamic Data (stdlib only).

Endpoints (from the Builder extension's Manage -> Data client and @jigx/jigx-debug-mcp):

  GET    {API}/strata/organizations/{org}/solutions/{name}              -> {solutionId, title, ...}
  GET    {API}/data/organizations/{org}/solutions/{sid}/databases/default/tables/{t}/rows
         ?limit=N&continuationToken=...                                 -> {items, hasMore, ...}
  PATCH  .../tables/{t}/rows   body {"<rid>": {"content": {...}}}      -> batch merge

The token goes raw in Authorization (no "Bearer" prefix). See auth.py for where it comes from.
Only reads and the permit-gated batch merge are implemented: no delete, no single-row put.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

import auth
import permit as permit_mod

PAGE = 500
BATCH = 50

# Keys the API attaches to every row; never part of the content we diff or copy.
SKIP_KEYS = {"rid", "id", "authorized", "file", "thumbnail", "__metadata"}


class Client:
    def __init__(self, org, token):
        if not org:
            sys.exit("ddcheck: no org. Pass --org, set JIGX_ORG_ID, or put \"org\" in the profile.")
        self.org, self.token, self.base = org, token, auth.api_base(org)

    def call(self, method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-client-type": "management",
            "x-correlation-id": "mgt:" + uuid.uuid4().hex[:12],
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")[:300]
        except urllib.error.URLError as e:
            sys.exit(f"network error calling {url}: {e.reason}")

    def solution_id(self, name):
        url = f"{self.base}/strata/organizations/{self.org}/solutions/{urllib.parse.quote(name)}"
        status, body = self.call("GET", url)
        if status != 200:
            sys.exit(f"solution lookup {name}: HTTP {status} {body}")
        return body["solutionId"]

    def rows_url(self, sid, table):
        return (f"{self.base}/data/organizations/{self.org}/solutions/{sid}"
                f"/databases/default/tables/{urllib.parse.quote(table)}/rows")

    def fetch_rows(self, table, sid, shape=None):
        """All rows of one table, or None when the table is absent (404).

        `shape`, when a dict, records what the first page looked like (envelope and item keys,
        paging signal) so a dump documents the real API shape instead of assuming it.
        """
        items, cont, first = [], None, True
        while True:
            url = self.rows_url(sid, table) + f"?limit={PAGE}"
            if cont:
                url += "&continuationToken=" + urllib.parse.quote(cont)
            status, body = self.call("GET", url)
            if status == 404:
                return None
            if status != 200:
                sys.exit(f"rows {table}: HTTP {status} {body}")
            page = body.get("items") or []
            if first and shape is not None and not shape:
                shape.update({
                    "table": table,
                    "envelope_keys": sorted(body.keys()),
                    "item_keys": sorted(page[0].keys()) if page else [],
                    "hasMore": body.get("hasMore"),
                    "continuationToken_present": bool(body.get("continuationToken")),
                    "first_page_rows": len(page),
                })
            first = False
            items += page
            cont = body.get("continuationToken")
            # hasMore is advisory; the continuation token is what drives paging.
            if not cont:
                return items

    def patch_rows(self, table, sid, rows, permit):
        """Batch-merge {rid: content} into a table. Needs the Permit from permit.ask() for this
        exact solution. Returns (written, [errors])."""
        if not isinstance(permit, permit_mod.Permit) or permit.solution_id != sid:
            raise RuntimeError("refusing to write: no permit for this solution (see permit.ask)")
        rids, written, errors = list(rows), 0, []
        for i in range(0, len(rids), BATCH):
            # The server validates each value as {content: {...}}; a bare content object is a 400.
            chunk = {r: {"content": rows[r]} for r in rids[i:i + BATCH]}
            status, body = self.call("PATCH", self.rows_url(sid, table), chunk)
            if status in (200, 201, 204):
                written += len(chunk)
            else:
                errors.append(f"{table} batch {i // BATCH}: HTTP {status} {body}")
        return written, errors


def rid_of(item):
    return item.get("rid") or item.get("id") or (item.get("content") or {}).get("rid")


def content_of(item):
    """Row content, tolerating both {rid, content:{...}} and a flattened row."""
    c = item.get("content") if isinstance(item.get("content"), dict) else item
    return {k: v for k, v in c.items() if k not in SKIP_KEYS}


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))
