#!/usr/bin/env python3
"""Track a Jigx sync or screen load live from the simulator's SQLite database.

Standard library only. Opens the solution database and the runtime log database
read-only (`?mode=ro`) and never writes to them. Every --interval seconds it reads:

  * `_syncScope`: each scope's state and the state of each entry in `details`
  * the SDK `_commandQueue` size
  * row counts for any --table you name
  * new WARNING/ERROR runtime log lines, e.g. "Timeout for first snapshot", and the
    runtime's "Missing ID or value of entity ... using hash function" (Android: "Missing
    entity id, falling back to hash"; a datasource with no id column, so every row is hashed)

It prints one live line per change, appends every changed sample to
<out>/<label>-samples.jsonl, and stops when the load is ready (every tracked scope
synced and the queue empty for --settle seconds), failed, or timed out. It then
writes <out>/<label>-summary.json with the time from the first scope start to
ready, per-scope and per-entry durations, a waterfall, final counts and log
signals. Compare runs with compare-runs.py.

A scope in the runtime's "interrupted" state is paused, not failed: Acumatica OAuth
sign-in interrupts the identity sync while the login page waits for the user. The time
any tracked scope is interrupted is subtracted from the totals, scope and entry times
(the raw values are kept as *_raw_s), so typing the password is not counted as sync. A
"Signing out" log line or a recreated database starts the run again (a build that
reloads can sync once before the sign-out). Armed while a sync was already running, the
run ends "invalid" once that sync finishes: arm first, then sign in. While nothing has
started it says when the app is syncing another solution instead.

Each sign-in run needs a fresh sign-in: start the tracker, then sign out of the app and
sign in again (or sign in for the first time). With --scopes, names that match no scope are
reported together with the scope ids the app does have (the sync actions' instanceIds, not
the names of the progress datasources).

--copy-db OUT makes a copy of the solution database for time-queries.mjs and the row-count
check: through SQLite's backup, so rows still in the -wal file are included (a plain cp
leaves them out and gives an empty database), and switched out of WAL mode so it opens
read-only on its own.

Only entry states are kept from `details`: it also holds function parameters,
which can carry URLs and credentials. Log lines keep level, category and a
redacted message; `additionalParameters` is never read.

Android: a Google APIs emulator (not "Google Play": only those allow `adb root`). Pass
--android [serial]; the databases are read on the emulator with its own sqlite3 over
adb, read-only, about every half second. A phone's app files are not reachable, and
the debug bridge (~5 s per query) is too slow to time anything. Read
references/run-and-track.md.

Examples:
  python3 track-load.py --list
  python3 track-load.py --solution my-solution-test --label pre-1 --table orders
  python3 track-load.py --solution my-solution-test --wait --scopes sync-base,sync-lookups --label pre-2
  python3 track-load.py --db path/to/jigx.solution.___x___.<uuid>.db --scopes sync-base,sync-lookups
  python3 track-load.py --android --solution my-solution-test --wait --label pre-android-1
  python3 track-load.py --solution my-solution-test --copy-db <review>/before/device.db
"""
import argparse
import contextlib
import datetime
import glob
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time

SIMULATORS = os.path.expanduser("~/Library/Developer/CoreSimulator/Devices")
DB_GLOB = "data/Containers/Data/Application/*/Library/LocalDatabase/jigx.solution.___{name}___.*.db"
SECRET = re.compile(r"((?:Basic|Bearer)\s+)[A-Za-z0-9+/=._~-]+|((?:token|password|secret|apikey|api_key)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.I)


def redact(text):
    return SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "***", text)


def now_ms():
    return time.time() * 1000


def iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat(timespec="milliseconds")


def solution_dbs(name="*", android=None):
    if android:
        return android_dbs(android, name)
    return sorted(glob.glob(os.path.join(SIMULATORS, "*", DB_GLOB.format(name=name))), key=last_write, reverse=True)


def adb():
    sdk = os.environ.get("ANDROID_HOME") or os.path.expanduser("~/Library/Android/sdk")
    return shutil.which("adb") or os.path.join(sdk, "platform-tools", "adb")


def android_serial(value):
    """The emulator to read: the one given, else the only adb device. Switches adbd to root."""
    devices = re.findall(r"^(\S+)\tdevice$", subprocess.run([adb(), "devices"], capture_output=True, text=True).stdout, re.M)
    serial = value if value not in (None, True, "") else (devices[0] if len(devices) == 1 else None)
    if not serial or serial not in devices:
        raise SystemExit(f"no Android device {value or ''} (adb devices: {', '.join(devices) or 'none'}); pass --android <serial>")
    out = subprocess.run([adb(), "-s", serial, "root"], capture_output=True, text=True).stdout
    if "cannot run as root" in out:
        raise SystemExit("this emulator cannot run adb as root: use a Google APIs system image, not Google Play")
    subprocess.run([adb(), "-s", serial, "wait-for-device"], timeout=60)
    return serial


def android_dbs(serial, name="*"):
    pattern = f"jigx.solution.___{name}___.*.db"
    out = subprocess.run([adb(), "-s", serial, "shell", f"f=$(find /data/data/com.jigx.* -name '{pattern}' 2>/dev/null); [ -n \"$f\" ] && ls -t $f"],
                         capture_output=True, text=True, timeout=30).stdout
    seen, paths = set(), []
    for line in out.split():
        if line not in seen:
            seen.add(line)
            paths.append(f"adb:{serial}:{line}")
    return paths


class Rows(list):
    def fetchone(self):
        return self[0] if self else None


class AdbDb:
    """A database on a rooted Android emulator, read with the device's sqlite3 (read-only)."""
    NULL = "<<NULL>>"

    def __init__(self, serial, path):
        self.serial, self.path = serial, path

    def execute(self, sql, params=()):
        for value in params:  # only integer parameters are used
            sql = sql.replace("?", str(int(value)), 1)
        script = f'.mode list\n.separator "\\037" "\\036"\n.nullvalue "{self.NULL}"\n{sql};\n'
        proc = subprocess.run([adb(), "-s", self.serial, "shell", "sqlite3", "-readonly", self.path],
                              input=script, capture_output=True, text=True, timeout=20)
        if proc.returncode or "Error" in proc.stderr:
            raise sqlite3.Error(proc.stderr.strip() or f"sqlite3 exited {proc.returncode}")
        return Rows(tuple(self.value(v) for v in row.split("\x1f"))
                    for row in proc.stdout.split("\x1e") if row.strip("\r\n"))

    def value(self, v):
        if v == self.NULL:
            return None
        return int(v) if re.fullmatch(r"-?\d+", v) else float(v) if re.fullmatch(r"-?\d+\.\d+", v) else v


SIGN_OUT = re.compile(r"signing out", re.I)


def copy_db(db, out):
    """A self-contained copy of a solution database: SQLite's backup (includes the -wal file),
    then out of WAL mode so it opens read-only without -wal and -shm files. Returns its row count."""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(out + suffix)
    if db.startswith("adb:"):
        serial, device_path = db[4:].split(":", 1)
        tmp = "/data/local/tmp/jigx-perf-copy.db"
        # one string: the device shell would split ".backup <file>" into two arguments
        proc = subprocess.run([adb(), "-s", serial, "shell", f"sqlite3 '{device_path}' '.backup {tmp}'"],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode or "Error" in proc.stderr:
            raise SystemExit(f"backup on the emulator failed: {proc.stderr.strip()}")
        subprocess.run([adb(), "-s", serial, "pull", tmp, out], capture_output=True, text=True, timeout=300, check=True)
        subprocess.run([adb(), "-s", serial, "shell", "rm", "-f", tmp], capture_output=True, timeout=30)
    else:
        with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as src, \
                contextlib.closing(sqlite3.connect(out)) as dst:
            src.backup(dst)
    with contextlib.closing(sqlite3.connect(out)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        # data tables only: the runtime's own (_syncScope, __migrations) exist before any data does
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' AND name NOT LIKE '\\_%' ESCAPE '\\'")]
        return sum(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables)


def unknown_scopes(state, scopes, armed=0):
    """Named --scopes that cannot be a scope of this run, so it would never reach ready. A scope's
    row may appear only when it first starts, so a missing name counts only when none of the
    named scopes exist while others have started since arming, or when every named scope that
    does exist has finished this run."""
    have = state["scopes"]
    missing = [s for s in scopes if s not in have]
    if not missing or not any(fresh(v, armed) for v in have.values()):
        return []
    present = [have[s] for s in scopes if s in have]
    if not present or all(fresh(v, armed) and v["state"] == "synced" for v in present):
        return missing
    return []


def file_id(path):
    """The database file's identity: it changes when a logout deletes and a sign-in recreates it."""
    if path.startswith("adb:"):
        return None
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def last_write(path):
    """When the database last changed: its -wal file moves first (the main file can keep one timestamp)."""
    return max((os.path.getmtime(p) for p in (path, path + "-wal") if os.path.exists(p)), default=0)


def other_solutions_busy(db, since_ms):
    """Other solutions in the same app whose database changed since arming."""
    folder, me = os.path.dirname(db), solution_name(db)
    return sorted({solution_name(p) for p in glob.glob(os.path.join(folder, "jigx.solution.___*___.*.db"))
                   if solution_name(p) != me and last_write(p) * 1000 > since_ms})


def solution_name(path):
    m = re.search(r"jigx\.solution\.___(.+?)___\.", os.path.basename(path))
    return m.group(1) if m else os.path.basename(path)


def connect(path):
    """Read-only connection that closes on exit (sqlite3's own context manager only commits)."""
    if path.startswith("adb:"):
        serial, device_path = path[4:].split(":", 1)
        return contextlib.nullcontext(AdbDb(serial, device_path))
    return contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2))


def read_state(path, tables):
    with connect(path) as conn:
        scopes = {}
        for sid, state, details, started, synced in conn.execute(
                "SELECT id, state, details, lastStartedAt, lastSyncedAt FROM _syncScope"):
            try:
                entries = {k: (v or {}).get("state") for k, v in json.loads(details or "{}").items()}
            except (ValueError, AttributeError):
                entries = {}
            scopes[sid] = {"state": state, "startedAt": started, "syncedAt": synced, "entries": entries}
        queue = conn.execute("SELECT COUNT(*) FROM _commandQueue").fetchone()[0]
        counts = {}
        for table in tables:
            try:
                counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
    return {"scopes": scopes, "queue": queue, "counts": counts}


def read_logs(path, after_id, since_ms):
    """New WARNING/ERROR log lines from jigx.log.db; returns (rows, last id)."""
    rows, last = [], after_id
    try:
        with connect(path) as conn:
            for rid, ts, message in conn.execute(
                    "SELECT id, timestamp, message FROM _logMessages WHERE id > ? AND timestamp >= ? ORDER BY id",
                    (after_id, since_ms)):
                last = rid
                try:
                    m = json.loads(message)
                except ValueError:
                    continue
                row = {"at": iso(ts), "level": m.get("level"), "category": m.get("category"),
                       "message": redact(str(m.get("message", "")))[:300]}
                if m.get("category") == "solution.expression":
                    # Keep the failing expression (solution code) and its screen; never the evaluated data.
                    ctx = next((p for p in m.get("additionalParameters") or [] if isinstance(p, dict) and "expression" in p), {})
                    screen = ctx.get("screen")
                    row["expression"] = redact(str(ctx.get("expression", "")))[:300]
                    row["screen"] = screen.get("screenId") if isinstance(screen, dict) else None
                rows.append(row)
    except sqlite3.Error:
        pass
    return rows, last


def tracked(state, scopes, armed):
    """Scopes that count for this run: the named ones, or every scope started after arming."""
    all_scopes = state["scopes"]
    if scopes:
        return {s: all_scopes.get(s) for s in scopes}
    return {s: v for s, v in all_scopes.items() if (v["startedAt"] or 0) >= armed}


def fresh(v, armed):
    return bool(v) and (v["startedAt"] or 0) >= armed


def status(state, scopes, armed):
    run = tracked(state, scopes, armed)
    started = [v for v in run.values() if fresh(v, armed)]
    if not started:
        return "waiting"
    if any(v["state"] == "failed" or "failed" in v["entries"].values() for v in started):
        return "failed"
    if any(v["state"] == "interrupted" for v in started):
        return "paused"  # e.g. the OAuth login page is waiting for the user
    if len(started) == len(run) and state["queue"] == 0 and all(
            v["state"] == "synced" and all(e == "synced" for e in v["entries"].values()) for v in started):
        return "ready"
    return "loading"


def live_line(elapsed, state, scopes, armed):
    parts = []
    for sid, v in sorted(tracked(state, scopes, armed).items()):
        if not v:
            parts.append(f"{sid}: -")
            continue
        entries = list(v["entries"].values())
        parts.append(f"{sid}: {v['state']} {entries.count('synced')}/{len(entries)}"
                     + (f" ({entries.count('failed')} failed)" if "failed" in entries else "")
                     + ("" if fresh(v, armed) else " (from before arming)"))
    counts = " ".join(f"{t}={c}" for t, c in state["counts"].items())
    return f"{elapsed:7.2f}s  " + " | ".join(parts or ["no scope started yet"]) + f"  queue={state['queue']}  {counts}"


WAITING = {"not started", "pending", "queued", None}


def entry_timings(samples, armed):
    """From the first sample an entry was seen syncing to the first sample it was synced. An
    entry that went from waiting to synced between two samples starts at its last waiting
    sample: its time is at most one interval, and the wait before it is not counted."""
    first_seen, started, waited, finished, failed = {}, {}, {}, {}, set()
    for s in samples:
        t = s["t"]
        for sid, v in s["scopes"].items():
            if not fresh(v, armed):
                continue
            for key, st in v["entries"].items():
                name = f"{sid}/{key}"
                first_seen.setdefault(name, t)
                if st in WAITING and name not in finished:
                    waited[name] = t
                if st == "syncing":
                    started.setdefault(name, t)
                if st == "synced" and name not in finished:
                    finished[name] = t
                if st == "failed":
                    failed.add(name)
    out = {}
    for name in first_seen:
        start = started.get(name, waited.get(name, first_seen[name]))
        end = finished.get(name)
        out[name] = {"start_s": round(start, 2), "end_s": round(end, 2) if end is not None else None,
                     "duration_s": round(end - start, 2) if end is not None else None,
                     "failed": name in failed}
    return dict(sorted(out.items(), key=lambda kv: (kv[1]["start_s"], kv[0])))


def serial_chain(entries, slack=0.3):
    """The longest run of entries where each starts only after the one before it ended (within
    one sample): calls in strict series, which a parallel wave could overlap."""
    done = sorted((e["start_s"], e["end_s"], n) for n, e in entries.items() if e["end_s"] is not None)
    best, cur = [], []
    for start, end, name in done:
        if cur and start >= cur[-1][1] - slack:
            cur.append((start, end, name))
        else:
            cur = [(start, end, name)]
        if len(cur) > len(best):
            best = list(cur)
    return [n for _, _, n in best] if len(best) >= 4 else []


def waterfall(entries, width=40):
    ends = [e["end_s"] for e in entries.values() if e["end_s"] is not None]
    span = max(ends or [1]) or 1
    lines = []
    for name, e in entries.items():
        end = e["end_s"] if e["end_s"] is not None else span
        a, b = int(e["start_s"] / span * width), max(int(end / span * width), int(e["start_s"] / span * width) + 1)
        mark = "x" if e["failed"] else "#"
        dur = f"{e['duration_s']:.2f}s" if e["duration_s"] is not None else "unfinished"
        lines.append(f"{name[:48]:48} {' ' * a}{mark * (b - a)}{' ' * (width - b)} {e['start_s']:6.2f}s +{dur}")
    return lines


def expression_errors(logs):
    """Expression failures grouped by expression and error, most frequent first."""
    groups = {}
    for r in logs:
        if r.get("category") != "solution.expression":
            continue
        key = (r.get("expression") or "?", r["message"].replace("Failed with error: ", ""), r.get("screen"))
        groups[key] = groups.get(key, 0) + 1
    return [{"expression": e, "error": err, "screen": sc, "count": n}
            for (e, err, sc), n in sorted(groups.items(), key=lambda kv: -kv[1])]


def scope_runs(samples, armed):
    """Every sync of every scope started after arming: {scope: [[start_ms, end_ms or None], ...]}.
    A scope that syncs again (a screen's onFocus after the sign-in sync) shows as a second run,
    which the final state alone hides: it keeps only the last start.
    Each span is [start_ms, end_ms or None, resumed]: resumed when the run before it was
    interrupted (the runtime restarts a scope after the OAuth login), so it is not a repeat."""
    runs, interrupted = {}, set()
    for sample in samples:
        for sid, v in sample["scopes"].items():
            start, end = v.get("startedAt"), v.get("syncedAt")
            if not start or start < armed:
                continue
            spans = runs.setdefault(sid, [])
            if not spans or spans[-1][0] != start:
                if spans and spans[-1][1] is None:
                    spans[-1][1] = start  # an unfinished run (interrupted) ends where the next one starts
                spans.append([start, None, sid in interrupted])
                interrupted.discard(sid)
            if v.get("state") == "interrupted":
                interrupted.add(sid)
            if end and end >= start:
                spans[-1][1] = end
    return runs


def paused_spans(samples, armed, only=None):
    """[start_ms, end_ms] while any fresh scope (or the scope `only`) was interrupted, from
    the first sample showing it to the first sample after it that does not."""
    spans, cur = [], None
    for sample in samples:
        at = armed + sample["t"] * 1000
        hit = any(v and v.get("state") == "interrupted" and (v.get("startedAt") or 0) >= armed
                  for sid, v in sample["scopes"].items() if only in (None, sid))
        if hit and cur is None:
            cur = [at, None]
        elif not hit and cur is not None:
            cur[1] = at
            spans.append(cur)
            cur = None
    if cur is not None:
        cur[1] = armed + samples[-1]["t"] * 1000
        spans.append(cur)
    return spans


def first_sync_s(spans, paused=()):
    """Seconds of a scope's first sync: its first start to the end of the last resume of it."""
    if not spans:
        return None
    start, end, i = spans[0][0], spans[0][1], 1
    while i < len(spans) and spans[i][2]:  # resumed after an interruption: still the first sync
        end, i = spans[i][1], i + 1
    if end is None:
        return None
    return round((end - start - overlap_ms(start, end, paused)) / 1000, 2)


def overlap_ms(a, b, spans):
    return sum(max(0, min(b, y) - max(a, x)) for x, y in spans) if a is not None and b is not None else 0


def busy_seconds(runs, until=None, paused=()):
    """Time at least one scope was syncing: the union of every run, an unfinished one up to
    `until`, less the time a scope was interrupted (paused)."""
    spans = sorted([a, b or until] for spans in runs.values() for a, b, *_ in spans if b or until)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    total = sum(b - a - overlap_ms(a, b, paused) for a, b in merged)
    return round(total / 1000, 2)


def summarise(label, db, samples, result, armed, ready_at, logs, final, scopes):
    run = tracked(final, scopes, armed) if final else {}
    runs = scope_runs(samples, armed)
    starts = [v["startedAt"] for v in run.values() if v and v["startedAt"]]
    # The first start of a tracked scope, not its last: a second sync moves the final startedAt.
    starts += [spans[0][0] for sid, spans in runs.items() if sid in run]
    first_start = min(starts) if starts else None
    paused = paused_spans(samples, armed) if samples else []
    entries = entry_timings(samples, armed)
    for e in entries.values():  # entry times are seconds since arming
        if e["duration_s"] is not None and paused:
            cut = overlap_ms(armed + e["start_s"] * 1000, armed + e["end_s"] * 1000, paused) / 1000
            if cut:
                e["duration_raw_s"], e["duration_s"] = e["duration_s"], round(e["duration_s"] - cut, 2)
    total_raw = (ready_at - first_start) / 1000 if ready_at and first_start else None
    interrupted = overlap_ms(first_start, ready_at, paused) / 1000 if total_raw is not None else 0

    def scope_duration(sid, v):
        if not (v and v["syncedAt"] and v["startedAt"] and v["syncedAt"] >= v["startedAt"]):
            return None
        cut = overlap_ms(v["startedAt"], v["syncedAt"], paused_spans(samples, armed, sid)) if samples else 0
        return round((v["syncedAt"] - v["startedAt"] - cut) / 1000, 2)
    snapshot = {}
    for row in logs:
        m = re.search(r"'([^']+)' Timeout for first snapshot", row["message"])
        if m:
            snapshot[m.group(1)] = snapshot.get(m.group(1), 0) + 1
    hashed = sum(int(m.group(1) or 1) for r in logs
                 for m in [re.search(r"(?:Missing ID or value of entity|Missing entity id)(?: \((\d+) times\))?", r["message"])] if m)
    return {
        "label": label,
        "machine": (f"Android emulator {db.split(':')[1]} on host {platform.node()} {platform.machine()}" if db.startswith("adb:")
                    else f"host {platform.node()} {platform.machine()} {platform.system()} (iOS Simulator)"),
        "solution": solution_name(db),
        "armed_at": iso(armed),
        "result": result,
        "first_start_to_ready_s": round(total_raw - interrupted, 2) if total_raw is not None else None,
        # Time a scope sat interrupted (the OAuth login page), already taken out of the values above.
        "interrupted_s": round(sum(b - a for a, b in paused) / 1000, 2),
        "first_start_to_ready_raw_s": round(total_raw, 2) if total_raw is not None else None,
        "arm_to_ready_s": round((ready_at - armed) / 1000, 2) if ready_at else None,
        # All sync work, also after ready: a second sync of the same scope halves nothing in
        # first_start_to_ready_s but doubles this.
        "sync_busy_s": busy_seconds(runs, now_ms() if result != "ready" else None, paused) if runs else None,
        # A restart after an interruption is the same sync resuming, not a second one.
        "scope_starts": {sid: sum(1 for sp in spans if not sp[2]) for sid, spans in sorted(runs.items())},
        "repeated_scopes": sorted(sid for sid, spans in runs.items() if sum(1 for sp in spans if not sp[2]) > 1),
        "scopes": {sid: {"state": v["state"], "duration_s": scope_duration(sid, v),
                         # The scope's first sync (with any resume after an interruption): what the
                         # sign-in waits for. duration_s is its last sync, which a repeat moves.
                         "first_duration_s": first_sync_s(runs.get(sid), paused_spans(samples, armed, sid))}
                   for sid, v in run.items() if v},
        "entries": entries,
        "serial_entries": serial_chain(entries),
        "slowest_entries": sorted(((n, e["duration_s"]) for n, e in entries.items() if e["duration_s"] is not None),
                                  key=lambda x: -x[1])[:10],
        "final_counts": final["counts"] if final else {},
        "snapshot_timeouts": snapshot,
        "log_errors": sum(1 for r in logs if r["level"] == "ERROR"),
        "hash_fallbacks": hashed,
        "expression_errors": expression_errors(logs),
        "log_warnings": sum(1 for r in logs if r["level"] == "WARNING"),
        "sample_interval_note": "Entry times have the resolution of the sampling interval; scope durations use the runtime's own timestamps.",
    }


def track(db, label, out, tables=(), scopes=(), interval=0.25, settle=5.0, timeout=900.0, stream=sys.stdout, unknown_wait=10.0):
    os.makedirs(out, exist_ok=True)
    log_db = os.path.join(os.path.dirname(db), "jigx.log.db")
    armed = now_ms()
    samples, logs, last_sig, last_log = [], [], None, 0
    # skip log lines written before arming
    _, last_log = read_logs(log_db, 0, armed)
    result, ready_since, ready_at, final = "timeout", None, None, None
    started_at, restarts, before_arming, idle_since, others_checked = armed, 0, set(), None, 0
    announced, unknown_told, unknown_since = set(), set(), None
    inode = file_id(db)
    print(f"Tracking {solution_name(db)} as '{label}'. Start the load now: for a sign-in, sign out of the app and "
          "sign in again (every run needs a fresh sign-in); otherwise refresh or open the screen.",
          file=stream, flush=True)
    with open(os.path.join(out, f"{label}-samples.jsonl"), "w", encoding="utf-8") as jsonl:
        while now_ms() - started_at < timeout * 1000:
            t = (now_ms() - armed) / 1000
            try:
                state = read_state(db, tables)
            except sqlite3.Error:
                time.sleep(interval)
                continue
            final = state
            new_logs, last_log = read_logs(log_db, last_log, armed)
            recreated = file_id(db) != inode
            signed_out = any(SIGN_OUT.search(r["message"]) for r in new_logs)
            if signed_out or recreated:
                # A build that reloads can sync once before the sign-out: start this run again.
                inode, restarts = file_id(db), restarts + 1
                armed, samples, logs, last_sig, ready_since, ready_at = now_ms(), [], [], None, None, None
                before_arming.clear()
                jsonl.write(json.dumps({"at": iso(armed), "restart": "sign-out" if signed_out else "database recreated"}) + "\n")
                print(f"{t:7.2f}s  {'Sign-out seen' if signed_out else 'Database recreated'}: the run starts again from here.",
                      file=stream, flush=True)
                time.sleep(interval)
                continue
            for row in new_logs:
                logs.append(row)
                print(f"{t:7.2f}s  LOG {row['level']} {row['category']}: {row['message'][:140]}", file=stream, flush=True)
            sig = json.dumps(state, sort_keys=True)
            if sig != last_sig:
                last_sig = sig
                samples.append({"t": t, **state})
                jsonl.write(json.dumps({"at": iso(now_ms()), "t": round(t, 3), **state}) + "\n")
                jsonl.flush()
                print(live_line(t, state, scopes, armed), file=stream, flush=True)
            # A named scope's row can appear only when it starts (the one after it in a chain):
            # warn only when nothing is syncing and the names still match nothing after a while.
            idle = not any(v["state"] in ("syncing", "interrupted") for v in state["scopes"].values())
            missing = [s for s in unknown_scopes(state, scopes, armed) if s not in unknown_told] if idle else []
            unknown_since = (unknown_since or now_ms()) if missing else None
            if missing and now_ms() - unknown_since >= unknown_wait * 1000:
                unknown_told.update(missing)
                print(f"{t:7.2f}s  No scope {', '.join(missing)} in this solution, so the run cannot reach ready. "
                      f"Scopes here: {', '.join(sorted(state['scopes']))}. Stop (Ctrl-C) and pass those to --scopes.",
                      file=stream, flush=True)
            st = status(state, scopes, armed)
            if st == "failed":
                result = "failed"
                break
            if st == "waiting":
                pool = {s: state["scopes"].get(s) for s in scopes} if scopes else state["scopes"]
                running = {s for s, v in pool.items() if v and v["state"] in ("syncing", "interrupted") and (v["startedAt"] or 0) < armed}
                for s in running - before_arming:
                    print(f"{t:7.2f}s  {s} was already syncing when tracking started; it cannot be timed in this run.",
                          file=stream, flush=True)
                before_arming |= running
                if before_arming and not running:
                    idle_since = idle_since or now_ms()
                    if now_ms() - idle_since >= settle * 2000:
                        result = "invalid"
                        print("Everything synced before tracking started. Arm first (sign out, start track-load), "
                              "then sign in.", file=stream, flush=True)
                        break
                if not db.startswith("adb:") and now_ms() - others_checked > 2000:
                    others_checked = now_ms()
                    busy = [n for n in other_solutions_busy(db, armed) if n not in announced]
                    announced.update(busy)
                    if busy:
                        print(f"{t:7.2f}s  The app is syncing {', '.join(busy)}, not {solution_name(db)}: "
                              "switch to the test solution in the app.", file=stream, flush=True)
            else:
                idle_since = None
            if st == "ready":
                if ready_since is None:
                    ready_since = now_ms()
                    run = tracked(state, scopes, armed)
                    ready_at = max([v["syncedAt"] or 0 for v in run.values()] + [0]) or ready_since
                if now_ms() - ready_since >= settle * 1000:
                    result = "ready"
                    break
            else:
                ready_since, ready_at = None, None
            time.sleep(interval)
    summary = summarise(label, db, samples, result, armed, ready_at if result == "ready" else None, logs, final, scopes)
    summary["restarts"] = restarts
    summary["interval_s"] = interval
    with open(os.path.join(out, f"{label}-summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    total = summary["first_start_to_ready_s"]
    print("\n".join(["", f"{label}: {result}, first start to ready {'n/a' if total is None else f'{total}s'}", ""]
                    + waterfall(summary["entries"])), file=stream)
    print(f"Sync busy {summary['sync_busy_s']}s, {sum(summary['scope_starts'].values())} scope start(s)", file=stream)
    if summary["serial_entries"]:
        print(f"{len(summary['serial_entries'])} calls ran strictly one after another: "
              f"{', '.join(summary['serial_entries'][:6])}{' …' if len(summary['serial_entries']) > 6 else ''}; "
              "if they do not read each other's tables they can run in one wave", file=stream)
    if summary["interrupted_s"]:
        print(f"Interrupted (e.g. the login page) {summary['interrupted_s']}s, not counted; "
              f"first start to ready with it: {summary['first_start_to_ready_raw_s']}s", file=stream)
    for sid in summary["repeated_scopes"]:
        print(f"WARNING: {sid} synced {summary['scope_starts'][sid]} times in one run; look for an onFocus or "
              "onLoad that starts the same sync again", file=stream)
    if summary["snapshot_timeouts"]:
        print(f"Timeout for first snapshot: {summary['snapshot_timeouts']}", file=stream)
    if summary.get("hash_fallbacks"):
        print(f"Rows hashed for lack of an id: {summary['hash_fallbacks']}", file=stream)
    for e in summary.get("expression_errors", [])[:10]:
        print(f"Expression error x{e['count']}{' on ' + e['screen'] if e['screen'] else ''}: {e['error']}: {e['expression']}", file=stream)
    print(f"Saved {os.path.join(out, label + '-summary.json')}", file=stream, flush=True)
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="list solution databases in local simulators, newest first")
    p.add_argument("--android", nargs="?", const=True, metavar="SERIAL",
                   help="read a rooted Android emulator (Google APIs image) instead of the iOS Simulator")
    p.add_argument("--db", help="path to a jigx.solution.___<name>___.<uuid>.db file")
    p.add_argument("--solution", help="solution name; finds the newest matching simulator database")
    p.add_argument("--label", default=datetime.datetime.now().strftime("run-%H%M%S"),
                   help="run label; prefix with pre-/post- for compare-runs.py")
    p.add_argument("--out", default=os.path.expanduser("~/jigx-perf-reviews/runs"), help="the review folder's runs/ (preflight.py prints the review folder); never inside the solution repo")
    p.add_argument("--table", action="append", default=[], help="table to count each sample; repeatable")
    p.add_argument("--scopes", default="", help="comma-separated sync scope ids that define ready; default: every scope "
                   "started after arming. Name them for a login journey, or a periodic background sync ends the run early")
    p.add_argument("--wait", action="store_true",
                   help="with --solution: wait for the database to appear (first sign-in, or a logout that removed it)")
    p.add_argument("--interval", type=float, default=0.25)
    p.add_argument("--settle", type=float, default=5.0, help="seconds ready must hold before stopping")
    p.add_argument("--timeout", type=float, default=900.0)
    p.add_argument("--copy-db", metavar="OUT", help="copy the solution database to OUT (rows in the -wal file included) and exit")
    args = p.parse_args(argv)
    android = android_serial(args.android) if args.android else None
    if android and args.interval == 0.25:
        args.interval = 0.5  # each read is an adb round trip
    if args.list:
        paths = solution_dbs(android=android)
        if not paths:
            print("No solution databases yet: sign in to the app first.")
        seen = {}
        for path in paths:  # newest first; older copies (other simulators, reinstalls) are counted, not listed
            seen.setdefault(solution_name(path), []).append(path)
        for name, copies in seen.items():
            when = "" if android else f"{datetime.datetime.fromtimestamp(last_write(copies[0])):%Y-%m-%d %H:%M:%S}"
            mark = "*" if args.solution and name == args.solution else " "
            more = f"  (+{len(copies) - 1} older)" if len(copies) > 1 else ""
            print(f"{mark} {name:40} {when:19}  {copies[0]}{more}")
        return 0
    db = args.db
    if not db and args.solution:
        found = solution_dbs(args.solution, android)
        if not found and args.wait:
            print(f"Waiting for a database for '{args.solution}'. Sign in now.", flush=True)
            deadline = time.time() + args.timeout
            while not found and time.time() < deadline:
                time.sleep(0.5)
                found = solution_dbs(args.solution, android)
        if not found:
            p.error(f"no {'Android emulator' if android else 'simulator'} database for solution '{args.solution}'"
                    + ("; nobody signed in before --timeout" if args.wait else "; run --list, or pass --wait"))
        db = found[0]
    if not db:
        p.error("give --db or --solution (see --list)")
    if args.copy_db:
        rows = copy_db(db, args.copy_db)
        print(f"Copied {solution_name(db)} to {args.copy_db}: {rows} data rows")
        if not rows:
            print("The copy has no data rows: sign in and let the sync finish first.", file=sys.stderr)
            return 1
        return 0
    scopes = [s for s in args.scopes.split(",") if s]
    summary = track(db, args.label, args.out, args.table, scopes, args.interval, args.settle, args.timeout)
    return 0 if summary["result"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
