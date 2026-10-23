#!/usr/bin/env python3
"""
adb/uiautomator driving helpers for Jigx forms (any solution).

Built on a field-sales form run where filling one form by hand cost ~40 tool calls.
Import it from a scratch script:

    import sys; sys.path.insert(0, '<path to the installed jigx-mobile-testing skill>/scripts')
    from uidrive import *
    print(pick("dropdown.<fieldId>", "<visible item text>"))

Everything is pure local adb, so it all works OFFLINE (airplane mode) — only the
jigx_debug MCP needs the network. Developed on a 1080x2424 phone;
nothing is hardcoded beyond the x-centre used for swipes.

Traps these helpers exist to absorb (all cost real time on 2026-08-20/21):
  - `adb input text` silently truncates at the first space -> typed() escapes %s
  - fields near the top/bottom of the scroll swallow input -> bring2() centres them
  - a dropdown's Confirm tap jumps the scroll back to the top, so the NEXT field
    lookup must search downward again -> bring2() sweeps down first, then up
  - the "N questions left to complete" counter is the cheapest fill oracle there is
"""
import re, subprocess, time

# ---------------------------------------------------------------- primitives

def dump():
    """Current uiautomator tree as XML text."""
    subprocess.run("adb shell uiautomator dump --compressed /sdcard/w.xml",
                   shell=True, capture_output=True)
    return subprocess.run("adb shell cat /sdcard/w.xml",
                          shell=True, capture_output=True, text=True).stdout

def nodes(xml):
    """Flatten the tree to dicts with text, resource-id and a tap centre."""
    out = []
    for m in re.finditer(r'<node[^>]*>', xml):
        t = m.group(0)
        tx = re.search(r'text="([^"]*)"', t)
        rid = re.search(r'resource-id="([^"]*)"', t)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', t)
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        out.append({"text": tx.group(1) if tx else "",
                    "rid": rid.group(1) if rid else "",
                    "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                    "y1": y1, "y2": y2})
    return out

def find(xml, rid=None, text=None):
    for n in nodes(xml):
        if rid and n["rid"] == rid:
            return n
        if text and n["text"] == text:
            return n
    return None

def tap(x, y, wait=1.5):
    subprocess.run(f"adb shell input tap {x} {y}", shell=True)
    time.sleep(wait)

def swipe(y1, y2, dur=400, wait=1.5):
    """Swipe up the screen's centre column. y1>y2 scrolls DOWN the content."""
    subprocess.run(f"adb shell input swipe 540 {y1} 540 {y2} {dur}", shell=True)
    time.sleep(wait)

def typed(text):
    """`adb input text` drops everything after a space unless it is escaped."""
    return text.replace(" ", "%s")

def counter():
    """The form's 'N questions left to complete' line — the cheapest oracle."""
    for n in nodes(dump()):
        if "summary.item.title" in n["rid"]:
            return n["text"]
    return "(no counter)"

# ---------------------------------------------------------------- navigation

def bring2(rid, tries=10):
    """Locate rid in the mid-screen band, sweeping DOWN first and then UP.

    Direction matters: after a dropdown Confirm the scroll jumps back to the top,
    so a naive 'scroll up when not found' loop walks away from the target.
    """
    for _ in range(tries):
        n = find(dump(), rid=rid)
        if n and 600 < n["cy"] < 1600:
            return n
        if n is not None:
            swipe(900, 1600) if n["cy"] <= 600 else swipe(1600, 900)
        else:
            swipe(1800, 800)          # hunt downward
    for _ in range(tries):
        n = find(dump(), rid=rid)
        if n and 600 < n["cy"] < 1600:
            return n
        swipe(800, 1800)              # then back upward
    return find(dump(), rid=rid)

# ---------------------------------------------------------------- form fields

def fill(rid, value):
    """Centre a text/number field, tap it, type, and close the keyboard."""
    n = bring2(rid)
    if not n:
        return f"MISS {rid}"
    inp = find(dump(), rid=rid + ".input") or n
    tap(inp["cx"], inp["cy"], 1.2)
    subprocess.run(f"adb shell input text {typed(value)}", shell=True)
    time.sleep(0.8)
    subprocess.run("adb shell input keyevent 111", shell=True)   # ESC
    time.sleep(0.8)
    return f"filled {rid} = {value!r} | {counter()}"

def pick(rid, want_substr):
    """Open a data-driven dropdown, choose the first matching item, Confirm.

    Most dropdowns need all three taps. The customer picker is the exception —
    it commits on item tap and has no Confirm, which is why the Confirm lookup
    here is conditional rather than assumed.
    """
    n = bring2(rid)
    if not n:
        return f"MISS {rid}"
    tap(n["cx"], n["cy"], 3)
    x = dump()
    items = [q for q in nodes(x)
             if q["rid"].startswith("dropdown-item.") and q["rid"].endswith(".title")]
    tgt = next((q for q in items if want_substr.lower() in q["text"].lower()), None)
    if not tgt:
        return f"NO-OPTION {want_substr!r} in {[q['text'] for q in items][:15]}"
    tap(tgt["cx"], tgt["cy"], 1.5)
    c = find(dump(), rid="dropdown.confirm")
    if c:
        tap(c["cx"], c["cy"], 2.5)
    return f"picked {rid} -> {tgt['text']!r} | {counter()}"

def choose(label_text):
    """Tap a Yes/No or single-choice option by its visible label."""
    q = next((n for n in nodes(dump()) if n["text"] == label_text), None)
    if not q:
        return f"MISS choice {label_text!r}"
    tap(q["cx"], q["cy"], 2.5)
    return f"chose {label_text!r} | {counter()}"

def shoot(n=2):
    """Capture n photos in the in-app camera, then Done out of the sheet.

    Media fields open the IN-APP camera, not the system picker. Shutter sits at
    (540,2093) with the gallery thumb at (84,2093); the thumb badge shows the
    count. A lens-down phone gives near-black frames, which satisfy the rule.
    """
    for _ in range(n):
        tap(540, 2093, 3.5)
    tap(84, 2093, 3)                                  # gallery thumb -> sheet
    d = find(dump(), rid="gallery.done")
    if d:
        tap(d["cx"], d["cy"], 4)
    return f"captured {n} | {counter()}"

def audit():
    """Sweep the whole form and report every input plus whether it is empty.

    Cheaper than guessing which required field is still outstanding when the
    counter says N but you cannot see which N.
    """
    for _ in range(10):
        swipe(700, 1900, wait=0.7)                    # to the top
    seen = {}
    for _ in range(9):
        for q in nodes(dump()):
            if q["rid"].endswith((".input", ".label")) or "choice-field" in q["rid"]:
                seen.setdefault(q["rid"], q["text"])
        swipe(1800, 800, wait=1.0)
    return {r: v for r, v in seen.items() if r.endswith(".input")}
