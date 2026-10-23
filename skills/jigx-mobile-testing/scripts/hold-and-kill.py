#!/usr/bin/env python3
# Lost-response repro: tap a button, arm hold-proxy when the Nth request to HOST goes up, then
# force-stop the app while that request's RESPONSE is held at the proxy (Acumatica has committed,
# the device never sees the reply). Run hold-proxy.py first with HOLD_HOSTS=<host> HOLD_FILE=<armfile>
# and its log going to <log>.
# Usage: hold-and-kill.py <proxy-log> <armfile> --pkg com.jigx.<app>.android --tap X Y
#                         --host <erp-host> [--arm-on 2] [--min-bytes 400] [--hold-secs 3]
#   --arm-on: 1 if the write is the first request after the tap; 2 when a guard GET precedes the PUT.
import argparse, os, re, subprocess, sys, time
ap = argparse.ArgumentParser()
ap.add_argument('log'); ap.add_argument('arm')
ap.add_argument('--pkg', required=True); ap.add_argument('--tap', nargs=2, type=int, required=True)
ap.add_argument('--host', required=True); ap.add_argument('--arm-on', type=int, default=2)
ap.add_argument('--min-bytes', type=int, default=400); ap.add_argument('--hold-secs', type=float, default=3)
a = ap.parse_args(); h = re.escape(a.host); ts = lambda: time.strftime('%H:%M:%S')
if os.path.exists(a.arm): os.remove(a.arm)
start = len(open(a.log).read()); t0 = time.time()
subprocess.run(['adb', 'shell', 'input', 'tap', str(a.tap[0]), str(a.tap[1])])
print(f'{ts()} tapped (hold NOT armed)', flush=True)
reqs = 0; armed = held = False; t_held = None
while time.time() - t0 < 40:
    new = open(a.log).read()[start:]; start += len(new)
    for line in new.splitlines():
        m = re.search(r'BYTES (' + h + r':\d+#\d+) up \+(\d+)', line)
        if m and int(m.group(2)) >= a.min_bytes:
            reqs += 1; print(f'{ts()} request #{reqs} up ({m.group(2)}B)', flush=True)
            if reqs == a.arm_on and not armed:
                open(a.arm, 'w').write('armed'); armed = True; print(f'{ts()} ARMED on request #{reqs}', flush=True)
        if armed and not held and re.search('HOLD ' + h, line):
            held = True; t_held = time.time(); print(f'{ts()} response HELD: {line.strip()}', flush=True)
    if held and time.time() - t_held >= a.hold_secs: break
    time.sleep(0.03)
subprocess.run(['adb', 'shell', 'am', 'force-stop', a.pkg])
print(f'{ts()} FORCE-STOP (requests={reqs}, armed={armed}, held={held})', flush=True)
time.sleep(1.5)
if os.path.exists(a.arm): os.remove(a.arm)
print('disarmed', flush=True)
