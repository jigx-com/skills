#!/usr/bin/env node
'use strict';
/*
 * Throttling forward proxy for faithful "bad connection" testing of the Jigx app.
 * Node port of throttle-proxy.py (which stays the macOS/Python path) — this box has
 * neither Python nor WSL. Stdlib `net` only, no npm.
 *
 * Tunnels HTTPS via CONNECT (does NOT terminate TLS -> no cert-pinning problems) and
 * forwards plain HTTP. Per connection it can apply:
 *   - handshake latency before the upstream connect
 *   - bandwidth cap (per direction, kbit/s)
 *   - jitter stalls, and a chance to kill a connection mid-stream (loss / timeouts)
 *
 * Point the device at it with bad-connection.ps1 (sets Android's global http_proxy
 * over adb, via `adb reverse` so the proxy only has to bind loopback).
 *
 *   PORT             listen port                          (default 8899)
 *   LATENCY_MS       added connect latency, ms            (default 600)
 *   RATE_KBPS        per-direction cap, kbit/s; 0=off     (default 50)
 *   LOSS_PCT         chance to kill a live conn, %        (default 4)
 *   JITTER_MS        max extra random stall per chunk     (default 200)
 *
 * Added here, and the reason each exists:
 *
 *   SLOW_HOSTS       regex; ONLY matching CONNECT hosts get latency/rate/jitter/loss.
 *                    Everything else is piped through untouched.  (default '.', i.e. all)
 *                    NOT optional on this rig: the jigx_debug bridge rides the same
 *                    device network, and once that bridge drops it frequently never
 *                    re-pairs for the rest of the run (references/universal-learnings.md). Scope this to
 *                    the ERP host so *.jigx.com stays clean.
 *
 *   FAIL_HOSTS       regex; answer 502 to CONNECT without dialing upstream.  (default off)
 *                    Deterministic connect failure -> @ctx.response.status = 0 in
 *                    rest-create-pdf -> isNetworkError:true. The highest-value lever
 *                    in the rig: no dice roll, no waiting for a transfer to die.
 *
 *   KILL_AFTER_BYTES destroy the tunnel after N bytes have flowed UPSTREAM on a
 *                    throttled host; 0 = off.  A deterministic mid-PUT drop, unlike
 *                    LOSS_PCT's probability. A form PDF is tens of KB, so e.g. 8000
 *                    drops every attempt at the same point.
 *
 *   CONTROL_FILE     JSON file, re-read every 500ms, that overrides any of the above
 *                    live. Lets bad-connection.ps1 flip modes mid-run with no restart,
 *                    no adb, and above all no wifi bounce (see the launcher header).
 *
 * TWO NODE-SPECIFIC TRAPS — both were the reason a naive port would silently not
 * reproduce the bug:
 *
 *  1. CHUNKING. Node hands you up to 64KB per 'data' event; Python's recv(1460) gave
 *     one loss roll per ~1 MSS. Without re-slicing to CHUNK, LOSS_PCT=4 becomes ~45x
 *     less likely per byte and the test stops reproducing. slice() is not optional.
 *
 *  2. BACKPRESSURE. Pace with pause()/resume(), not per-chunk setTimeout in flowing
 *     mode, or a slow RATE_KBPS just buffers the whole PDF in RAM and defeats itself.
 *
 * Every CONNECT target is logged (and marked [passthrough] when unthrottled) so you can
 * confirm the app's traffic actually flows through this process.
 */

const net = require('net');
const fs = require('fs');
const os = require('os');
const path = require('path');

const num = (name, dflt) => {
  const v = Number(process.env[name]);
  return Number.isFinite(v) ? v : dflt;
};
const rx = (name, dflt) => {
  const v = process.env[name];
  if (!v) return dflt;
  try { return new RegExp(v, 'i'); } catch (e) { return dflt; }
};

const PORT = num('PORT', 8899);
const BIND = process.env.BIND || '0.0.0.0';
const CHUNK = 1460; // ~1 MSS — matches the Python recv() granularity

const CFG = {
  latencyMs: num('LATENCY_MS', 600),
  rateKbps: num('RATE_KBPS', 50),
  lossPct: num('LOSS_PCT', 4),
  jitterMs: num('JITTER_MS', 200),
  slowHosts: rx('SLOW_HOSTS', /./),
  failHosts: rx('FAIL_HOSTS', null),
  killAfterBytes: num('KILL_AFTER_BYTES', 0),
};

const CONTROL_FILE = process.env.CONTROL_FILE ||
  path.join(os.tmpdir(), 'jigx-rig', 'throttle-control.json');

const log = (m) => process.stdout.write(`[${new Date().toTimeString().slice(0, 8)}] ${m}\n`);
const bytesPerSec = () => (CFG.rateKbps > 0 ? (CFG.rateKbps * 1000) / 8 : 0);

const describe = () =>
  `latency=${CFG.latencyMs}ms rate=${CFG.rateKbps}kbps loss=${CFG.lossPct}% ` +
  `jitter=${CFG.jitterMs}ms slow=${CFG.slowHosts} ` +
  `fail=${CFG.failHosts || '(none)'} killAfter=${CFG.killAfterBytes || 'off'}`;

// ── live control ──────────────────────────────────────────────────────────────
// A partial/absent/half-written file must never crash the proxy or wedge a run —
// on any parse failure we keep the config we already have.
function reloadControl(quiet) {
  let raw;
  try { raw = fs.readFileSync(CONTROL_FILE, 'utf8'); } catch (e) { return; }
  let j;
  // Strip a UTF-8 BOM before parsing. PowerShell's Out-File/Set-Content default to
  // UTF-8 *with* BOM in this environment, and a leading U+FEFF makes JSON.parse throw —
  // which we swallow as "keep current config", i.e. the control file would silently
  // never apply. bad-connection.ps1 writes BOM-less on purpose; this is the backstop
  // for anyone editing the file by hand.
  try { j = JSON.parse(raw.replace(/^﻿/, '')); } catch (e) { return; }
  if (typeof j.latencyMs === 'number') CFG.latencyMs = j.latencyMs;
  if (typeof j.rateKbps === 'number') CFG.rateKbps = j.rateKbps;
  if (typeof j.lossPct === 'number') CFG.lossPct = j.lossPct;
  if (typeof j.jitterMs === 'number') CFG.jitterMs = j.jitterMs;
  if (typeof j.killAfterBytes === 'number') CFG.killAfterBytes = j.killAfterBytes;
  // Absent/empty => reset to the permissive default, so `set -Clear` really clears.
  CFG.slowHosts = j.slowHosts ? new RegExp(j.slowHosts, 'i') : /./;
  CFG.failHosts = j.failHosts ? new RegExp(j.failHosts, 'i') : null;
  if (!quiet) log(`control reloaded: ${describe()}`);
}

// ── relay ─────────────────────────────────────────────────────────────────────
function slice(buf) {
  if (buf.length <= CHUNK) return [buf];
  const out = [];
  for (let i = 0; i < buf.length; i += CHUNK) out.push(buf.subarray(i, i + CHUNK));
  return out;
}

function kill(st, why) {
  if (st.dead) return;
  st.dead = true;
  if (why) log(`  ${why}`);
  for (const s of st.socks) { try { s.destroy(); } catch (e) { /* already gone */ } }
}

function relay(src, dst, tag, st, throttled, isUpstream) {
  if (!throttled) {
    // Untouched path: a straight pipe. Deliberately not chunked — this carries the
    // jigx_debug bridge and any host we are not targeting, and it must stay fast.
    src.pipe(dst);
  } else {
    src.on('data', (buf) => {
      if (st.dead) return;
      src.pause();
      const parts = slice(buf);
      const step = () => {
        if (st.dead) return;
        const c = parts.shift();
        if (c === undefined) { if (!st.dead) src.resume(); return; }

        if (CFG.lossPct && Math.random() < CFG.lossPct / 100) {
          return kill(st, `drop ${tag} (simulated loss)`);
        }
        if (isUpstream) {
          st.upBytes += c.length;
          if (CFG.killAfterBytes && st.upBytes > CFG.killAfterBytes) {
            return kill(st, `kill ${tag} after ${st.upBytes}B upstream`);
          }
        }
        const bps = bytesPerSec();
        const delay = (CFG.jitterMs ? Math.random() * CFG.jitterMs : 0) +
                      (bps ? (c.length / bps) * 1000 : 0);
        try { dst.write(c); } catch (e) { return kill(st); }
        if (delay > 0) setTimeout(step, delay); else setImmediate(step);
      };
      step();
    });
    src.on('end', () => { try { dst.end(); } catch (e) { /* already closed */ } });
  }
  src.on('error', () => kill(st));
  src.on('close', () => kill(st));
}

function tunnel(client, upstream, tag, throttled) {
  const st = { dead: false, upBytes: 0, socks: [client, upstream] };
  client.setNoDelay(true);
  upstream.setNoDelay(true);
  relay(client, upstream, `${tag} up`, st, throttled, true);
  relay(upstream, client, `${tag} down`, st, throttled, false);
  client.resume();
}

// ── request handling ──────────────────────────────────────────────────────────
function handle(client, raw, peer) {
  const sep = raw.indexOf('\r\n\r\n');
  const headEnd = sep === -1 ? raw.length : sep + 4;
  const head = raw.subarray(0, headEnd).toString('latin1');
  const early = raw.subarray(headEnd); // bytes the client sent ahead of our reply

  const firstLine = head.split('\r\n')[0] || '';
  const parts = firstLine.split(' ');
  if (parts.length < 3) { client.destroy(); return; }
  const method = parts[0];
  const target = parts[1];

  const connectUpstream = (host, port, throttled, onReady) => {
    const upstream = net.createConnection({ host, port }, () => {
      upstream.setTimeout(0);
      onReady(upstream);
    });
    upstream.setTimeout(15000, () => { upstream.destroy(new Error('connect timeout')); });
    upstream.on('error', (e) => {
      log(`  502 ${host}:${port} (${e.message})`);
      try { client.end('HTTP/1.1 502 Bad Gateway\r\n\r\n'); } catch (_) { /* gone */ }
    });
    return upstream;
  };

  const go = () => {
    if (method === 'CONNECT') {
      const idx = target.lastIndexOf(':');
      const host = idx === -1 ? target : target.slice(0, idx);
      const port = idx === -1 ? 443 : Number(target.slice(idx + 1)) || 443;
      const throttled = CFG.slowHosts.test(host);

      if (CFG.failHosts && CFG.failHosts.test(host)) {
        log(`CONNECT ${host}:${port}  <- ${peer}  -> FORCED 502`);
        try { client.end('HTTP/1.1 502 Bad Gateway\r\n\r\n'); } catch (_) { /* gone */ }
        return;
      }
      log(`CONNECT ${host}:${port}  <- ${peer}${throttled ? '' : '  [passthrough]'}`);
      connectUpstream(host, port, throttled, (upstream) => {
        client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
        if (early.length) upstream.write(early);
        tunnel(client, upstream, `${host}:${port}`, throttled);
      });
      return;
    }

    // plain HTTP: host from absolute-URI, else the Host header. Forward verbatim.
    let hostport = null;
    if (/^http:\/\//i.test(target)) {
      hostport = target.slice('http://'.length).split('/')[0];
    } else {
      for (const line of head.split('\r\n')) {
        if (/^host:/i.test(line)) { hostport = line.slice(line.indexOf(':') + 1).trim(); break; }
      }
    }
    if (!hostport) { client.destroy(); return; }
    const hIdx = hostport.lastIndexOf(':');
    const host = hIdx === -1 ? hostport : hostport.slice(0, hIdx);
    const port = hIdx === -1 ? 80 : Number(hostport.slice(hIdx + 1)) || 80;
    const throttled = CFG.slowHosts.test(host);

    if (CFG.failHosts && CFG.failHosts.test(host)) {
      log(`HTTP ${method} ${host}:${port}  <- ${peer}  -> FORCED 502`);
      try { client.end('HTTP/1.1 502 Bad Gateway\r\n\r\n'); } catch (_) { /* gone */ }
      return;
    }
    log(`HTTP ${method} ${host}:${port}  <- ${peer}${throttled ? '' : '  [passthrough]'}`);
    connectUpstream(host, port, throttled, (upstream) => {
      upstream.write(raw); // replay the buffered request bytes
      tunnel(client, upstream, `${host}:${port}`, throttled);
    });
  };

  // simulate a high-latency link establishing
  if (CFG.latencyMs > 0) setTimeout(go, CFG.latencyMs); else go();
}

// ── server ────────────────────────────────────────────────────────────────────
const server = net.createServer((client) => {
  const peer = client.remoteAddress || '?';
  client.setNoDelay(true);
  let buf = Buffer.alloc(0);
  const onData = (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    if (buf.indexOf('\r\n\r\n') !== -1 || buf.length > 65536) {
      client.removeListener('data', onData);
      client.pause(); // resumed in tunnel() once upstream is wired
      handle(client, buf, peer);
    }
  };
  client.on('data', onData);
  client.on('error', () => { try { client.destroy(); } catch (e) { /* already gone */ } });
});

server.on('error', (e) => {
  log(`FATAL ${e.message}`);
  if (e.code === 'EADDRINUSE') log(`port ${PORT} is already in use — bad-connection.ps1 stop first`);
  process.exit(1);
});

try { fs.mkdirSync(path.dirname(CONTROL_FILE), { recursive: true }); } catch (e) { /* exists */ }
reloadControl(true);
fs.watchFile(CONTROL_FILE, { interval: 500 }, () => reloadControl(false));

server.listen(PORT, BIND, () => {
  log(`throttle-proxy on ${BIND}:${PORT}  ${describe()}`);
  log(`control file: ${CONTROL_FILE}`);
  log('Ctrl-C to stop. Point the device http_proxy here (see bad-connection.ps1).');
});

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { log('shutting down'); server.close(); process.exit(0); });
}
