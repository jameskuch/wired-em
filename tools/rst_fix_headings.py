"""Pad every reStructuredText heading rule out to at least its title's length."""
import sys, pathlib
RULES = set("=-~^\"'`#*+")

def fix(path):
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    n = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or len(set(s)) != 1 or s[0] not in RULES:
            continue
        # A rule adjacent to a title must be at least as long as the title.
        for j in (i - 1, i + 1):
            if 0 <= j < len(lines):
                title = lines[j]
                t = title.strip()
                if not t or (len(set(t)) == 1 and t[0] in RULES):
                    continue
                if len(s) < len(title):
                    lines[i] = s[0] * len(title)
                    n += 1
                break
    pathlib.Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n

for p in sys.argv[1:]:
    print(f"{p}: {fix(p)} rule(s) lengthened")
