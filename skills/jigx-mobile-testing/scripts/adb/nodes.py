#!/usr/bin/env python3
# nodes.py <dump.xml> [regex] : list labelled nodes (text or content-desc) with bounds; marks scrollables.
import re, sys
xml = open(sys.argv[1]).read(); pat = sys.argv[2] if len(sys.argv) > 2 else ''
for m in re.finditer(r'<node [^>]*>', xml):
    n = m.group(0)
    t = re.search(r' text="([^"]*)"', n).group(1); d = re.search(r'content-desc="([^"]*)"', n).group(1)
    b = re.search(r'bounds="([^"]+)"', n).group(1); lab = t or d
    if lab and (pat == '' or re.search(pat, lab)):
        print(repr(lab[:60]), b, 'scroll' if 'scrollable="true"' in n else '')
