#!/bin/bash
# Faithful "bad connection" for Jigx device testing: run a throttling proxy on the Mac and
# point the paired Pixel's wifi proxy at it. Device stays CONNECTED but traffic is slow/lossy
# (latency + bandwidth cap + mid-flight drops) — what an offline-first app actually chokes on,
# unlike a clean wifi off/on.
#
# Usage:
#   ./bad-connection.sh start [LATENCY_MS RATE_KBPS LOSS_PCT]   # defaults 600 50 4
#   ./bad-connection.sh stop                                    # kill proxy + clear device proxy
#   ./bad-connection.sh status
#
# Requires: the device paired over adb, and the Mac + Pixel on the same wifi.
set -u
ADB=${ADB:-$(command -v adb || echo /Applications/Vysor.app/Contents/Resources/app.asar.unpacked/native/darwin/adb)}
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PY="$HERE/throttle-proxy.py"
PORT=${PORT:-8899}
PIDFILE="/tmp/throttle-proxy.pid"
LOGFILE="/tmp/throttle-proxy.log"

mac_ip() { ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null; }

start() {
  local lat=${1:-600} rate=${2:-50} loss=${3:-4}
  local ip; ip=$(mac_ip)
  [ -z "$ip" ] && { echo "could not determine Mac wifi IP (en0/en1)"; exit 1; }
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "proxy already running (pid $(cat "$PIDFILE"))"
  else
    PORT="$PORT" LATENCY_MS="$lat" RATE_KBPS="$rate" LOSS_PCT="$loss" \
      nohup python3 "$PROXY_PY" >"$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    echo "proxy started (pid $(cat "$PIDFILE")) on $ip:$PORT  [latency=${lat}ms rate=${rate}kbps loss=${loss}%]"
  fi
  echo "pointing device wifi proxy -> $ip:$PORT"
  "$ADB" shell settings put global http_proxy "$ip:$PORT"
  # nudge the app's HTTP stack to re-read the proxy
  "$ADB" shell svc wifi disable; sleep 1; "$ADB" shell svc wifi enable
  echo
  echo "device http_proxy now: $("$ADB" shell settings get global http_proxy)"
  echo "watch traffic flow through:  tail -f $LOGFILE"
  echo "VERIFY the app honors it: you should see CONNECT app.jigx.com / <erp-host> lines."
  echo "If NOTHING logs while the app makes requests, the app is bypassing the system proxy ->"
  echo "fall back to router QoS (see scripts/README.md)."
}

stop() {
  echo "clearing device proxy"
  "$ADB" shell settings put global http_proxy :0
  "$ADB" shell settings delete global global_http_proxy_host 2>/dev/null || true
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null && echo "proxy stopped (pid $(cat "$PIDFILE"))"
    rm -f "$PIDFILE"
  else
    pkill -f throttle-proxy.py 2>/dev/null && echo "proxy stopped" || echo "no proxy running"
  fi
  "$ADB" shell svc wifi disable; sleep 1; "$ADB" shell svc wifi enable
  echo "device http_proxy now: $("$ADB" shell settings get global http_proxy)"
}

status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "proxy: running (pid $(cat "$PIDFILE")) on $(mac_ip):$PORT"
  else
    echo "proxy: not running"
  fi
  echo "device http_proxy: $("$ADB" shell settings get global http_proxy 2>/dev/null)"
}

case "${1:-}" in
  start)  shift; start "$@";;
  stop)   stop;;
  status) status;;
  *) echo "usage: $0 {start [LATENCY_MS RATE_KBPS LOSS_PCT] | stop | status}"; exit 1;;
esac
