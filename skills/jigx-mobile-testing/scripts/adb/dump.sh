#!/bin/zsh
# dump.sh <out.xml> : uiautomator dump pulled to the host. Times out after 20 s (a dump can hang mid-scroll).
perl -e 'alarm 20; exec @ARGV' adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 && adb pull /sdcard/ui.xml "$1" >/dev/null 2>&1
