#!/bin/zsh
# pair.sh <package> '<deeplinkUrl>' : cold-start the branded app on a jigx_debug deeplink and tap ENABLE.
# The -p <package> flag is what makes the intent reach the app instead of Chrome; the app must be
# force-stopped first or the deeplink is delivered but does not pair.
here=${0:A:h}; pkg=${1:?package e.g. com.jigx.<app>.android}; dl=${2:?deeplink url}
adb shell am force-stop "$pkg"
adb shell am start -a android.intent.action.VIEW -p "$pkg" -d "'$dl'" >/dev/null
for i in $(seq 1 20); do sleep 1.5; "$here/dump.sh" /tmp/jigx-pair.xml
  if grep -q 'text="ENABLE"' /tmp/jigx-pair.xml; then python3 "$here/tapnode.py" /tmp/jigx-pair.xml ENABLE; exit 0; fi; done
echo "no ENABLE dialog seen (already enabled, or not paired)"; exit 1
