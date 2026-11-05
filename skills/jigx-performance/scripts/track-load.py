#!/usr/bin/env python3
"""Track a Jigx sync or screen load live from the simulator's SQLite database.

Standard library only. Opens the solution database and the runtime log database
read-only (`?mode=ro`) and never writes to them. Every --interval seconds it reads:

  * `_syncScope`: each scope's state and the state of each entry in `details`
  * the SDK `_commandQueue` size
  * row counts for any --table you name
  * new WARNING/ERROR runtime log lines, e.g. "Timeout for first snapshot", and the
    runtime's "Missing ID or value of entity ... using hash function" (a datasource with
    no id column, so every row is hashed)

It prints one live line per change, appends every changed sample to
<out>/<label>-samples.jsonl, and stops when the load is ready (every tracked scope
synced and the queue empty for --settle seconds), failed, or timed out. It then
writes <out>/<label>-summary.json with the time from the first scope start to
ready, per-scope and per-entry durations, a waterfall, final counts and log
signals. Compare runs with compare-runs.py.

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
    return sorted(glob.glob(os.path.join(SIMULATORS, "*", DB_GLOB.format(name=name))), key=os.path.getmtime, reverse=True)


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
    if any(v["state"] in ("failed", "interrupted") or "failed" in v["entries"].values() for v in started):
        return "failed"
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


def entry_timings(samples, armed):
    """First sample an entry was seen syncing (or any state) to the first sample it was synced."""
    first_seen, started, finished, failed = {}, {}, {}, set()
    for s in samples:
        t = s["t"]
        for sid, v in s["scopes"].items():
            if not fresh(v, armed):
                continue
            for key, st in v["entries"].items():
                name = f"{sid}/{key}"
                first_seen.setdefault(name, t)
                if st == "syncing":
                    started.setdefault(name, t)
                if st == "synced" and name not in finished:
                    finished[name] = t
                if st == "failed":
                    failed.add(name)
    out = {}
    for name in first_seen:
        start = started.get(name, first_seen[name])
        end = finished.get(name)
        out[name] = {"start_s": round(start, 2), "end_s": round(end, 2) if end is not None else None,
                     "duration_s": round(end - start, 2) if end is not None else None,
                     "failed": name in failed}
    return dict(sorted(out.items(), key=lambda kv: (kv[1]["start_s"], kv[0])))


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


def summarise(label, db, samples, result, armed, ready_at, logs, final, scopes):
    run = tracked(final, scopes, armed) if final else {}
    starts = [v["startedAt"] for v in run.values() if v and v["startedAt"]]
    first_start = min(starts) if starts else None
    entries = entry_timings(samples, armed)
    snapshot = {}
    for row in logs:
        m = re.search(r"'([^']+)' Timeout for first snapshot", row["message"])
        if m:
            snapshot[m.group(1)] = snapshot.get(m.group(1), 0) + 1
    hashed = sum(int(m.group(1) or 1) for r in logs
                 for m in [re.search(r"Missing ID or value of entity(?: \((\d+) times\))?", r["message"])] if m)
    return {
        "label": label,
        "machine": (f"Android emulator {db.split(':')[1]} on host {platform.node()} {platform.machine()}" if db.startswith("adb:")
                    else f"host {platform.node()} {platform.machine()} {platform.system()} (iOS Simulator)"),
        "solution": solution_name(db),
        "armed_at": iso(armed),
        "result": result,
        "first_start_to_ready_s": round((ready_at - first_start) / 1000, 2) if ready_at and first_start else None,
        "arm_to_ready_s": round((ready_at - armed) / 1000, 2) if ready_at else None,
        "scopes": {sid: {"state": v["state"],
                         "duration_s": round((v["syncedAt"] - v["startedAt"]) / 1000, 2)
                         if v and v["syncedAt"] and v["startedAt"] and v["syncedAt"] >= v["startedAt"] else None}
                   for sid, v in run.items() if v},
        "entries": entries,
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


def track(db, label, out, tables=(), scopes=(), interval=0.25, settle=5.0, timeout=900.0, stream=sys.stdout):
    os.makedirs(out, exist_ok=True)
    log_db = os.path.join(os.path.dirname(db), "jigx.log.db")
    armed = now_ms()
    samples, logs, last_sig, last_log = [], [], None, 0
    # skip log lines written before arming
    _, last_log = read_logs(log_db, 0, armed)
    result, ready_since, ready_at, final = "timeout", None, None, None
    print(f"Tracking {solution_name(db)} as '{label}'. Start the load now (log in, refresh or open the screen).",
          file=stream, flush=True)
    with open(os.path.join(out, f"{label}-samples.jsonl"), "w", encoding="utf-8") as jsonl:
        while now_ms() - armed < timeout * 1000:
            t = (now_ms() - armed) / 1000
            try:
                state = read_state(db, tables)
            except sqlite3.Error:
                time.sleep(interval)
                continue
            final = state
            new_logs, last_log = read_logs(log_db, last_log, armed)
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
            st = status(state, scopes, armed)
            if st == "failed":
                result = "failed"
                break
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
    with open(os.path.join(out, f"{label}-summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    total = summary["first_start_to_ready_s"]
    print("\n".join(["", f"{label}: {result}, first start to ready {'n/a' if total is None else f'{total}s'}", ""]
                    + waterfall(summary["entries"])), file=stream)
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
    p.add_argument("--out", default="perf-runs")
    p.add_argument("--table", action="append", default=[], help="table to count each sample; repeatable")
    p.add_argument("--scopes", default="", help="comma-separated sync scope ids that define ready; default: every scope "
                   "started after arming. Name them for a login journey, or a periodic background sync ends the run early")
    p.add_argument("--wait", action="store_true",
                   help="with --solution: wait for the database to appear (first sign-in, or a logout that removed it)")
    p.add_argument("--interval", type=float, default=0.25)
    p.add_argument("--settle", type=float, default=5.0, help="seconds ready must hold before stopping")
    p.add_argument("--timeout", type=float, default=900.0)
    args = p.parse_args(argv)
    android = android_serial(args.android) if args.android else None
    if android and args.interval == 0.25:
        args.interval = 0.5  # each read is an adb round trip
    if args.list:
        for path in solution_dbs(android=android):
            when = "" if android else f"{datetime.datetime.fromtimestamp(os.path.getmtime(path)):%Y-%m-%d %H:%M}"
            print(f"{solution_name(path):40} {when:16}  {path}")
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
            p.error(f"no simulator database for solution '{args.solution}'; run --list, or pass --wait")
        db = found[0]
    if not db:
        p.error("give --db or --solution (see --list)")
    scopes = [s for s in args.scopes.split(",") if s]
    summary = track(db, args.label, args.out, args.table, scopes, args.interval, args.settle, args.timeout)
    return 0 if summary["result"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
