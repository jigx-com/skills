#!/usr/bin/env python3
"""
Host-targeted failure proxy for Jigx device tests.

Why this exists (see references/universal-learnings.md, "Network/proxy rigs"):
`bad-connection.sh` throttles the WHOLE link, which makes the app decide it is
*offline*. It then never dispatches the request at all, so you get
`errorText="Upload interrupted (no active sync scope)"` and NO
`execute_entity_network_error` row — i.e. the abort-before-dispatch path, not
the failure path you were trying to test.

This proxy instead leaves every host alone except the ones you name. Jigx /
Google traffic stays fast and healthy, so the app stays "online" and its sync
scope stays active, while the target host (e.g. `<erp-host>:8088`)
fails for real. That is what produced genuine `'PDF Upload'` network-error rows
and a `fileUploadQueue` row at `status='failed'`, `attemptCounter=5`.

Modes, per host list (substring match against the CONNECT / Host: target):

  KILL_HOSTS   fail these. KILL_MODE picks how:
                 midbody (default) - let the TLS handshake and request start,
                                     then tear the tunnel down after
                                     KILL_AFTER_BYTES client->upstream bytes.
                                     This is the one that reproduces a real
                                     mid-upload failure.
                 refuse            - answer CONNECT with 502 immediately.
                                     Cleaner, but some clients treat it as
                                     "no network" rather than "request failed".
  SLOW_HOSTS   bandwidth-cap these only, at SLOW_KBPS. Everything else is
               untouched. (Note: throttling alone rarely gives a usable
               "uploading" observation window - references/universal-learnings.md.)

With BOTH lists empty this is a plain transparent logging proxy, which is how
you DISCOVER the host names in the first place: start it, point the device at
it, pull-to-refresh / submit, and read the CONNECT lines.

Env vars:
  PORT              listen port                              (default 8899)
  KILL_HOSTS        comma-separated substrings, ''=none      (default '')
  KILL_MODE         midbody | refuse                         (default midbody)
  KILL_AFTER_BYTES  bytes to pass before the midbody kill    (default 2048)
  SLOW_HOSTS        comma-separated substrings, ''=none      (default '')
  SLOW_KBPS         cap for SLOW_HOSTS, kbit/s               (default 8)

Point the device at it with:
  adb shell settings put global http_proxy "$(ipconfig getifaddr en0):8899"
and clear it with:
  adb shell settings put global http_proxy :0

Stdlib only; system python3 (3.9+).
"""
import os, socket, threading, time

def env(name, default):
    return os.environ.get(name, default)

def envi(name, default):
    try: return int(os.environ.get(name, default))
    except ValueError: return int(default)

def hostlist(name):
    raw = env(name, "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]

PORT             = envi("PORT", 8899)
KILL_HOSTS       = hostlist("KILL_HOSTS")
KILL_MODE        = env("KILL_MODE", "midbody").lower()
KILL_AFTER_BYTES = envi("KILL_AFTER_BYTES", 2048)
SLOW_HOSTS       = hostlist("SLOW_HOSTS")
SLOW_KBPS        = envi("SLOW_KBPS", 8)
SLOW_BPS         = (SLOW_KBPS * 1000 / 8) if SLOW_KBPS > 0 else 0
CHUNK            = 1460  # ~1 MSS

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def matches(host, patterns):
    h = host.lower()
    return any(p in h for p in patterns)

HOLD_HOSTS = [h.strip().lower() for h in os.environ.get("HOLD_HOSTS","").split(",") if h.strip()]
HOLD_FILE = os.environ.get("HOLD_FILE", "hold.arm")

def pump(src, dst, tag, stop, kill_after=0, bps=0):
    """Relay src->dst. kill_after>0 tears the tunnel down mid-stream; bps caps rate."""
    sent = 0
    try:
        while not stop.is_set():
            try:
                data = src.recv(CHUNK)
            except OSError:
                break
            if not data:
                break
            if bps:
                time.sleep(len(data) / bps)
            if tag.endswith(" down") and any(h in tag.lower() for h in HOLD_HOSTS) and os.path.exists(HOLD_FILE):
                log(f"  HOLD {tag} holding {len(data)}B")
                while os.path.exists(HOLD_FILE) and not stop.is_set():
                    time.sleep(0.1)
                log(f"  RELEASE {tag}")
            try:
                dst.sendall(data)
            except OSError:
                break
            sent += len(data)
            if bps or os.environ.get("TRACE") or any(h in tag.lower() for h in HOLD_HOSTS):
                log(f"  BYTES {tag} +{len(data)} total={sent}")
            if kill_after and sent >= kill_after:
                log(f"  KILL {tag} after {sent}B")
                break
    finally:
        stop.set()
        for s in (src, dst):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def tunnel(client, upstream, tag, kill_after=0, bps=0):
    stop = threading.Event()
    # Only the client->upstream direction gets the mid-body kill: that is the
    # request body (the upload), which is what we want to sever in flight.
    t1 = threading.Thread(target=pump, args=(client, upstream, tag + " up", stop),
                          kwargs={"kill_after": kill_after, "bps": bps}, daemon=True)
    t2 = threading.Thread(target=pump, args=(upstream, client, tag + " down", stop),
                          kwargs={"bps": bps}, daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()

def read_head(client):
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            chunk = client.recv(4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            break
    return buf

def connect_upstream(host, port):
    up = socket.create_connection((host, port), timeout=15)
    # Clear the connect timeout off the socket. It otherwise applies to every
    # later recv(), so an idle tunnel - notably the mostly-idle jigx-debug
    # websocket to *.wse.jigx.com - is torn down after 15s and the MCP return
    # channel dies. This is the throttle-proxy bug from references/universal-learnings.md.
    up.settimeout(None)
    return up

def handle(client, addr):
    try:
        try: client.settimeout(None)
        except OSError: pass
        raw = read_head(client)
        if not raw:
            client.close(); return
        first, _, rest = raw.partition(b"\r\n")
        parts = first.split(b" ")
        if len(parts) < 3:
            client.close(); return
        method, target = parts[0], parts[1]

        if method == b"CONNECT":
            host, _, p = target.decode().partition(":")
            port = int(p or 443)
        else:
            tgt = target.decode()
            if tgt.startswith("http://"):
                hostport = tgt[len("http://"):].split("/", 1)[0]
            else:
                hostport = None
                for line in rest.split(b"\r\n"):
                    if line.lower().startswith(b"host:"):
                        hostport = line.split(b":", 1)[1].strip().decode(); break
            if not hostport:
                client.close(); return
            host, _, p = hostport.partition(":")
            port = int(p or 80)

        killing = matches(host, KILL_HOSTS)
        slowing = matches(host, SLOW_HOSTS)
        mark = " [KILL]" if killing else (" [SLOW]" if slowing else "")
        log(f"{method.decode()} {host}:{port}{mark}  <- {addr[0]}")

        if killing and KILL_MODE == "refuse":
            try: client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError: pass
            log(f"  502 {host}:{port} (refused by KILL_HOSTS)")
            client.close(); return

        try:
            upstream = connect_upstream(host, port)
        except OSError as e:
            try: client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError: pass
            log(f"  502 {host}:{port} ({e})"); client.close(); return

        kill_after = KILL_AFTER_BYTES if killing else 0
        bps = SLOW_BPS if slowing else 0

        if method == b"CONNECT":
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            upstream.sendall(raw)  # replay the buffered request bytes
        tunnel(client, upstream, f"{host}:{port}#{addr[1]}", kill_after=kill_after, bps=bps)
    except Exception as e:
        log(f"  err {addr[0]}: {e}")
        try: client.close()
        except OSError: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(128)
    log(f"kill-proxy on :{PORT}")
    log(f"  KILL_HOSTS={KILL_HOSTS or '(none)'} mode={KILL_MODE} after={KILL_AFTER_BYTES}B")
    log(f"  SLOW_HOSTS={SLOW_HOSTS or '(none)'} rate={SLOW_KBPS}kbps")
    if not KILL_HOSTS and not SLOW_HOSTS:
        log("  transparent mode - every host passes through untouched (host discovery)")
    try:
        while True:
            client, addr = srv.accept()
            threading.Thread(target=handle, args=(client, addr), daemon=True).start()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        srv.close()

if __name__ == "__main__":
    main()
