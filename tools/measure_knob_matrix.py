"""Which render options change a surface, measured: the evidence behind the panel's show and grey-out rules.

    .venv/bin/python tools/measure_knob_matrix.py                # measure, print
    .venv/bin/python tools/measure_knob_matrix.py --write        # and rewrite the fixture
    .venv/bin/python tools/measure_knob_matrix.py ultraliser     # only the rows naming it

Every row builds one cell twice, with the same options except one, and compares
the two surfaces (a hash of the vertices and faces).  A knob that gives the same
surface twice is DEAD for that method and pathway and must be hidden or greyed
out in the panel; one that gives two surfaces is LIVE and must be shown and
usable.  ``tests/fixtures/render_knob_matrix.json`` keeps the verdicts and
``tests/unit/test_knob_matrix.py`` holds the schema's ``show`` and ``inert``
rules to them, so a rule cannot go stale unnoticed (the audit of 2026-10-15
found Passes live under Ultraliser's neighbors and dead under Ultraliser).

Measure with extreme perturbations: Decimate 0.5 looked inert on Voxel remesh
only because both builds hit the 400,000-face cap, and 0.1 acts.  The cell is
Inferior c1178 from the offline cache (SBFSEM_OFFLINE=1 or a warm cache): one
disc-traced cell with a soma, a thin arbor and seven unlinked branches, which
builds in seconds on every pathway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from sbfsem.service.facade import SbfsemService

CELL = (1178, "i")
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "render_knob_matrix.json"

#: (method, pathway, option, value a, value b, the other options that make it measurable)
ROWS: list[tuple[str, str, str, Any, Any, dict[str, Any]]] = [
    ("cylinder", "ultraliser", "smooth_iterations", 1, 6, {}),
    ("cylinder", "ultraliser", "post_order", "smooth_first", "decimate_first",
     {"reduce_fraction": 0.1}),
    ("cylinder", "ultraliser", "reduce_fraction", 1.0, 0.1, {}),
    ("cylinder", "ultraliser", "voxel_smooth_um", 0.5, 1.5, {}),
    ("cylinder", "ultraliser", "tie_um", 0.0, 20.0, {}),
    ("cylinder", "voxel2", "smooth_iterations", 1, 6, {}),
    ("cylinder", "voxel2", "post_order", "smooth_first", "decimate_first",
     {"reduce_fraction": 0.1}),
    ("cylinder", "wrap", "voxel_smooth_um", 0.5, 1.5, {}),
    ("cylinder", "wrap", "ring_arc_um", 0.35, 0.7, {}),
    ("cylinder", "convolution", "tie_um", 0.0, 20.0, {}),
    ("cylinder", "convolution", "voxel_smooth_um", 0.5, 1.5, {}),
    ("hybrid", "ultraliser", "smooth_iterations", 1, 6, {"pixel_size_um": 0.25}),
    ("hybrid", "voxel", "smooth_iterations", 1, 6, {"pixel_size_um": 0.25}),
    ("curvemesh", "original", "smooth_iterations", 1, 5, {}),
    ("curvemesh", "adaptive_taubin", "smooth_iterations", 1, 5, {}),
    ("curvemesh", "voxel", "smooth_iterations", 1, 5, {}),
    ("curvemesh", "wrap", "smooth_iterations", 1, 5, {}),
    # Closed curve's averaging (2026-10-16): the discs' strength acts under Closed curve alone
    # (Hybrid's discs are the Cylinder build's); the outlines' under both.
    ("closedcurve", "original", "curve_avg_discs", 0.0, 1.0, {"pixel_size_um": 0.25}),
    ("closedcurve", "original", "curve_avg_paths", 0.0, 0.5, {"pixel_size_um": 0.25}),
    ("hybrid", "voxel", "curve_avg_discs", 0.0, 1.0, {"pixel_size_um": 0.25}),
    ("hybrid", "voxel", "curve_avg_paths", 0.0, 0.5, {"pixel_size_um": 0.25}),
    # Closed curve's reaches (2026-10-16): curved links and joined bodies act under Closed curve
    # alone (Hybrid's discs are the Cylinder build's, which ties its own pieces).  c1178's three
    # disc links across 22 untraced sections are what Follow links 1.5 um drops.  Its 130 traced
    # outlines skip no section and no link between them crosses one, so Bridge skips (either
    # method) and Follow links under Hybrid build the same surface on it at any value and have
    # no row here; RPC1 c2628 is where they act (Bridge skips 0 um: 312,464 -> 312,244
    # triangles at 0.25 um, 70 -> 71 bodies).
    ("closedcurve", "original", "curve_links_um", 0.0, 1.0, {"pixel_size_um": 0.25}),
    ("closedcurve", "original", "curve_tie_um", 0.0, 5.0, {"pixel_size_um": 0.25}),
    ("closedcurve", "original", "curve_link_bridge_um", 5.0, 1.5, {"pixel_size_um": 0.25}),
    ("hybrid", "voxel", "curve_links_um", 0.0, 1.0, {"pixel_size_um": 0.25}),
    ("hybrid", "voxel", "curve_tie_um", 0.0, 5.0, {"pixel_size_um": 0.25}),
]


def _build(service: SbfsemService, neuron, options: dict[str, Any]) -> tuple[str, int]:
    mesh = service.build_mesh(neuron, **options)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    return hashlib.sha256(vertices.tobytes() + faces.tobytes()).hexdigest()[:12], len(faces)


def measure(only: list[str]) -> list[dict[str, Any]]:
    service = SbfsemService(offline=True)
    service.load_neuron(CELL[0], CELL[1], include_synapses=False)
    neuron = service.neuron(*CELL)
    rows = []
    for method, pathway, option, a, b, extra in ROWS:
        name = f"{method}/{pathway}/{option}"
        if only and not any(word in name for word in only):
            continue
        base = {"method": method, "pathway": pathway, **extra}
        (ha, fa), (hb, fb) = (_build(service, neuron, {**base, option: v}) for v in (a, b))
        row = {
            "method": method, "pathway": pathway, "option": option, "a": a, "b": b,
            "extra": extra, "faces": [fa, fb], "acts": ha != hb,
        }
        print(f"{name:40s} {a!s:>13} vs {b!s:<14} {'acts' if row['acts'] else 'DEAD'}"
              f"   faces {fa:,} / {fb:,}", flush=True)
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("only", nargs="*", help="measure only the rows whose name contains one of these")
    parser.add_argument("--write", action="store_true", help=f"rewrite {FIXTURE.name} (all rows only)")
    args = parser.parse_args(argv)
    rows = measure(args.only)
    if args.write:
        if args.only:
            parser.error("--write rewrites the whole fixture: measure every row")
        document = {
            "cell": f"c{CELL[0]} ({CELL[1]}, Inferior Monkey)",
            "how": "two builds that differ only in `option` (a, b), the other options as in `extra`; "
                   "`acts` is whether the two surfaces differ (sha256 of the vertices and faces)",
            "rows": rows,
        }
        FIXTURE.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
