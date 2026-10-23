#!/usr/bin/env python3
# tapnode.py <dump.xml> <label> [--dry] : tap the centre of the first node whose text or content-desc equals <label>.
import re, sys, subprocess
xml = open(sys.argv[1]).read(); key = sys.argv[2]
for m in re.finditer(r'<node [^>]*>', xml):
    n = m.group(0)
    if f'text="{key}"' in n or f'content-desc="{key}"' in n:
        b = list(map(int, re.findall(r'\d+', re.search(r'bounds="([^"]+)"', n).group(1))))
        x, y = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        print(key, b, x, y)
        if '--dry' not in sys.argv: subprocess.run(['adb', 'shell', 'input', 'tap', str(x), str(y)])
        sys.exit(0)
print('NOTFOUND', key); sys.exit(1)
