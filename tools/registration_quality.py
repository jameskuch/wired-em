"""Which registration lines cells up best, in numbers, across many cells.

    .venv/bin/python tools/registration_quality.py --url http://127.0.0.1:8765 NeitzInferiorMonkey:1178 i:21768
    .venv/bin/python tools/registration_quality.py --url http://127.0.0.1:8765 --json out.json RPC1:430 RPC1:192

For every cell named (``<volume>:<id>``; the volume as the server names it, or its
short source key) it loads each registration's copy on the server (``POST
/api/neurons``: the stored positions or the volume's current section transforms,
each with no correction, Residual field, Curve fit or Field + fit; four where the
volume's transforms are not read), asks ``GET .../registration-quality`` of it,
and prints, per cell and over all of them:

* the sideways step of the cell's links between sections at the copy's positions
  (median, p90, and how many in a thousand pass 1.5 µm): how smoothly it runs;
  the curve fit lowers it by construction;
* what the copy does to what one section image shows: the change of the offset
  between two of the cell's annotations on one section within 5 µm (p95, and the
  share over 0.25 µm), and between each synapse and the cell (likewise);
* the curve fit's pulls toward an annotation at least 4x as wide, and the traced
  outlines it moved off the outlines linked to them.

A copy the server did not hold before is unloaded again once measured
(``DELETE ...?copy=``): the server's other users keep theirs, and memory is given
back.  It needs a server that can load the cells (the network, or a cache) and
reach Viking's corrections service; on production, run it on the server against
its loopback port.  Loading is the cost: about as long as loading the cell, for
each registration.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CORRECTIONS = ("off", "neighbor", "curvefit", "all")
WORDS = {"off": "no correction", "neighbor": "Residual field", "curvefit": "Curve fit", "all": "Field + fit"}


def _call(url: str, method: str = "GET", body: dict | None = None, timeout: float = 600.0):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.load(answer)


def _held(base: str, source: str, cell: int, tag: str) -> bool:
    try:
        _call(f"{base}/api/neurons/{source}/{cell}/registration-quality?copy={urllib.parse.quote(tag)}")
        return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def label(live: bool, corrections: str, has_transforms: bool) -> str:
    where = "current" if live and has_transforms else "stored"
    return where if corrections == "off" else f"{where} + {WORDS[corrections]}"


def measure_cell(base: str, source: str, cell: int, has_transforms: bool, keep: bool = False) -> list[dict]:
    """Every registration of one cell: the server's registration-quality answer, with its label and load time."""
    out = []
    for corrections in CORRECTIONS:
        for live in ((False, True) if has_transforms else (False,)):
            tag = f"{corrections}.{int(live)}"
            was_held = _held(base, source, cell, tag)
            started = time.time()
            detail = _call(f"{base}/api/neurons", "POST", {
                "id": cell, "source": source, "include_synapses": True,
                "live_registration": live, "section_corrections": corrections})
            loaded = time.time() - started
            got = detail["summary"]["copy_tag"]
            facts = _call(f"{base}/api/neurons/{source}/{cell}/registration-quality?copy={urllib.parse.quote(got)}")
            facts.update(label=label(live, corrections, has_transforms), live=live, corrections=corrections,
                         load_s=round(loaded, 2))
            out.append(facts)
            if not was_held and not keep:
                _call(f"{base}/api/neurons/{source}/{cell}?copy={urllib.parse.quote(got)}", "DELETE")
            print(f"  {source} c{cell} {facts['label']:<30} loaded in {loaded:5.1f} s", file=sys.stderr)
    return out


def _row(f: dict) -> list[str]:
    links = f.get("links") or {}
    n = max(1, f.get("n_links") or 0)
    sc = f.get("correction") or {}
    missed = " (not applied)" if sc.get("requested") and not sc.get("applied") else ""
    return [
        f["label"] + missed,
        f"{links.get('median_um', float('nan')):.3f}", f"{links.get('p90_um', float('nan')):.3f}",
        f"{1000 * (links.get('n_over_1p5_um') or 0) / n:.1f}",
        f"{f['distortion']['p95_um']:.3f}", f"{100 * f['distortion']['share_over']:.1f}",
        f"{f['synapse_drift']['p95_um']:.3f}", f"{100 * f['synapse_drift']['share_over']:.1f}",
        "—" if f.get("pulls") is None else str(f["pulls"]),
        "—" if f.get("outline_moves") is None else str(f["outline_moves"]),
    ]


HEAD = ["registration", "step med", "step p90", "‰ >1.5", "pairs p95", "pairs %>.25", "syn p95",
        "syn %>.25", "pulls", "outlines off"]


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(r[k]) for r in [HEAD, *rows]) for k in range(len(HEAD))]

    def line(r: list[str]) -> str:
        return "  ".join(c.ljust(w) if k == 0 else c.rjust(w) for k, (c, w) in enumerate(zip(r, widths, strict=True)))

    return "\n".join([line(HEAD), *(line(r) for r in rows)])


def summary(cells: dict[str, list[dict]]) -> list[list[str]]:
    """Over the cells: each registration's median of every cell's number."""
    by_label: dict[str, list[dict]] = {}
    for facts in cells.values():
        for f in facts:
            by_label.setdefault(f["label"], []).append(f)
    out = []
    def median_of(facts: list[dict], get) -> float:
        values = [v for v in (get(f) for f in facts) if v is not None]
        return statistics.median(values) if values else float("nan")

    for name, facts in by_label.items():
        def med(get, facts=facts):
            return median_of(facts, get)
        out.append([f"{name} ({len(facts)} cells)",
                    f"{med(lambda f: (f.get('links') or {}).get('median_um')):.3f}",
                    f"{med(lambda f: (f.get('links') or {}).get('p90_um')):.3f}",
                    f"{med(lambda f: 1000 * ((f.get('links') or {}).get('n_over_1p5_um') or 0) / max(1, f['n_links'])):.1f}",
                    f"{med(lambda f: f['distortion']['p95_um']):.3f}",
                    f"{med(lambda f: 100 * f['distortion']['share_over']):.1f}",
                    f"{med(lambda f: f['synapse_drift']['p95_um']):.3f}",
                    f"{med(lambda f: 100 * f['synapse_drift']['share_over']):.1f}",
                    f"{med(lambda f: f.get('pulls')):.1f}" if any(f.get('pulls') is not None for f in facts) else "—",
                    f"{med(lambda f: f.get('outline_moves')):.1f}"
                    if any(f.get('outline_moves') is not None for f in facts) else "—"])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cells", nargs="+", help="<volume>:<id>, e.g. NeitzInferiorMonkey:1178")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="the server")
    parser.add_argument("--json", help="also write every number to this file")
    parser.add_argument("--keep", action="store_true", help="leave the copies loaded on the server")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    volumes = {v["name"]: v for v in _call(f"{base}/api/volumes")}
    by_key = {v.get("key"): v for v in volumes.values() if v.get("key")}
    cells: dict[str, list[dict]] = {}
    for spec in args.cells:
        name, _, number = spec.rpartition(":")
        volume = volumes.get(name) or by_key.get(name)
        if volume is None:
            parser.error(f"no volume {name!r} on {base}")
        source = volume["name"]
        has_transforms = volume.get("has_transforms") is not False
        cells[f"{source}:{number}"] = measure_cell(base, source, int(number), has_transforms, keep=args.keep)
    for key, facts in cells.items():
        print(f"\n{key}  ({facts[0]['n_links']} links between sections)")
        print(_table([_row(f) for f in facts]))
    if len(cells) > 1:
        print(f"\nOver {len(cells)} cells: the median of each cell's number")
        print(_table(summary(cells)))
    print("\nstep: the sideways step of the cell's links between sections at the copy's positions (µm); "
          "the curve fit lowers it by construction.\npairs: change of the offset between two of the cell's "
          "annotations on one section within 5 µm; syn: change of a synapse's offset from the cell (µm). "
          "Zero is what the image shows.\npulls: annotations the curve fit pulled toward one at least 4x "
          "as wide; outlines off: traced outlines it moved off the outlines linked to them.")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as out:
            json.dump(cells, out, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
