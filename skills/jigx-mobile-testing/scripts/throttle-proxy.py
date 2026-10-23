#!/usr/bin/env python3
"""
Throttling forward proxy for faithful "bad connection" testing of the Jigx app.

Tunnels HTTPS via CONNECT (does NOT terminate TLS -> no cert-pinning problems) and
forwards plain HTTP. Applies, per connection:
  - handshake latency (added delay before the upstream connects)
  - bandwidth cap (per-direction, kbit/s)
  - jitter stalls + a small chance to kill a connection mid-stream (packet loss / timeouts)

Point the device at it with bad-connection.sh (sets Android's global http_proxy via adb).
Stdlib only; runs on the system python3 (3.9+). Tune via env vars or flags.

  PORT       listen port                      (default 8899)
  LATENCY_MS added connect latency, ms         (default 600)
  RATE_KBPS  per-direction cap, kbit/s; 0=off  (default 50)
  LOSS_PCT   chance to kill a live conn, %      (default 4)
  JITTER_MS  max extra random stall per chunk   (default 200)

Every CONNECT target is logged so you can confirm the app's traffic actually flows through
(look for app.jigx.com, <erp-host>, *.amazonaws.com).
"""
import os, sys, socket, threading, time, random

def envf(name, default):
    try: return float(os.environ.get(name, default))
    except ValueError: return float(default)

PORT      = int(envf("PORT", 8899))
LATENCY_MS= envf("LATENCY_MS", 600)
RATE_KBPS = envf("RATE_KBPS", 50)
LOSS_PCT  = envf("LOSS_PCT", 4)
JITTER_MS = envf("JITTER_MS", 200)
BYTES_PER_SEC = (RATE_KBPS * 1000 / 8) if RATE_KBPS > 0 else 0
CHUNK = 1460  # ~1 MSS

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def pump(src, dst, tag, stop):
    """Relay src->dst with bandwidth cap, jitter, and random mid-stream kill."""
    try:
        while not stop.is_set():
            try:
                data = src.recv(CHUNK)
            except OSError:
                break
            if not data:
                break
            # packet loss / flaky drop: kill the whole connection occasionally
            if LOSS_PCT and random.random() < (LOSS_PCT / 100.0):
                log(f"  drop {tag} (simulated loss)")
                break
            # jitter stall
            if JITTER_MS:
                time.sleep(random.uniform(0, JITTER_MS) / 1000.0)
            # bandwidth cap: pace the bytes
            if BYTES_PER_SEC:
                time.sleep(len(data) / BYTES_PER_SEC)
            try:
                dst.sendall(data)
            except OSError:
                break
    finally:
        stop.set()
        for s in (src, dst):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def tunnel(client, upstream, tag):
    stop = threading.Event()
    t1 = threading.Thread(target=pump, args=(client, upstream, tag + " up", stop), daemon=True)
    t2 = threading.Thread(target=pump, args=(upstream, client, tag + " down", stop), daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()

def read_request_line_and_headers(client):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = client.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            break
    return buf

def handle(client, addr):
    try:
        raw = read_request_line_and_headers(client)
        if not raw:
            client.close(); return
        first, _, rest = raw.partition(b"\r\n")
        parts = first.split(b" ")
        if len(parts) < 3:
            client.close(); return
        method, target, _ver = parts[0], parts[1], parts[2]

        # simulate a high-latency link establishing
        if LATENCY_MS:
            time.sleep(LATENCY_MS / 1000.0)

        if method == b"CONNECT":
            host, _, port = target.decode().partition(":")
            port = int(port or 443)
            log(f"CONNECT {host}:{port}  <- {addr[0]}")
            try:
                upstream = socket.create_connection((host, port), timeout=15)
                # Clear the connect timeout off the socket: it otherwise applies to every
                # subsequent recv(), so an idle tunnel (notably the mostly-idle jigx-debug
                # websocket) is torn down after 15s and the MCP return channel dies.
                upstream.settimeout(None)
                try:
                    client.settimeout(None)
                except OSError:
                    pass
            except OSError as e:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                log(f"  502 {host}:{port} ({e})"); client.close(); return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            tunnel(client, upstream, f"{host}:{port}")
        else:
            # plain HTTP: derive host from absolute-URI or Host header, forward verbatim
            host = None; port = 80
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
            log(f"HTTP {method.decode()} {host}:{port}  <- {addr[0]}")
            try:
                upstream = socket.create_connection((host, port), timeout=15)
                # Clear the connect timeout off the socket: it otherwise applies to every
                # subsequent recv(), so an idle tunnel (notably the mostly-idle jigx-debug
                # websocket) is torn down after 15s and the MCP return channel dies.
                upstream.settimeout(None)
                try:
                    client.settimeout(None)
                except OSError:
                    pass
            except OSError as e:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                log(f"  502 {host}:{port} ({e})"); client.close(); return
            upstream.sendall(raw)  # replay the buffered request bytes
            tunnel(client, upstream, f"{host}:{port}")
    except Exception as e:
        log(f"  err {addr[0]}: {e}")
        try: client.close()
        except OSError: pass

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(128)
    log(f"throttle-proxy on :{PORT}  latency={LATENCY_MS:.0f}ms rate={RATE_KBPS:.0f}kbps "
        f"loss={LOSS_PCT:.0f}% jitter={JITTER_MS:.0f}ms")
    log("Ctrl-C to stop. Point the device's wifi proxy here (see bad-connection.sh).")
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
