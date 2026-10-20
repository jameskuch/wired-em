"""Re-lay every reStructuredText simple table so its columns fit its content."""
import re, sys, pathlib

def fix(path):
    lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    out, i, tables = [], 0, 0
    border = re.compile(r"^=+(  +=+)+$")
    while i < len(lines):
        if not border.match(lines[i]):
            out.append(lines[i]); i += 1; continue
        # Collect the table: border, rows (with an inner border), border.
        widths = [len(seg) for seg in lines[i].split("  ") if seg]
        starts, acc = [], 0
        for w in widths:
            starts.append(acc); acc += w + 2
        block, j = [], i + 1
        while j < len(lines) and lines[j].strip():
            block.append(lines[j]); j += 1
        rows, rule_at = [], None
        for k, row in enumerate(block[:-1]):
            if border.match(row):
                rule_at = len(rows)
            else:
                rows.append([row[s:e].strip() for s, e in
                             zip(starts, starts[1:] + [len(row) + 9999])])
        need = [max(len(r[c]) for r in rows) for c in range(len(widths))]
        # The last column may overflow its rule; the others must not.
        rule = "  ".join("=" * w for w in need)
        out.append(rule)
        for k, cells in enumerate(rows):
            if k == rule_at:
                out.append(rule)
            line = ""
            for c, (cell, w) in enumerate(zip(cells, need)):
                line = line.ljust(sum(need[:c]) + 2 * c) + cell
            out.append(line.rstrip())
        out.append(rule)
        tables += 1
        i = j
    pathlib.Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
    return tables

for p in sys.argv[1:]:
    print(f"{p}: {fix(p)} table(s) re-laid")
