"""Cache a volume's IPL boundary markers as package data.

    .venv/bin/python tools/cache_boundary_markers.py RC2

Writes ``src/sbfsem/data/<SOURCE>_INL.txt`` and ``_GCL.txt``: one ``X,Y,Z``
marker per line in Viking pixels and sections, the format
``data/cacheBoundaryMarkers.m`` used and ``read_boundary_markers`` reads.  The
markers are every Location of the volume's ``INL-IPL Boundary`` (TypeID 224)
and ``IPL-GCL Boundary`` (235) structures.  Re-run it when annotators add
markers; set ``has_boundary=True`` for the volume in ``core/volumes.py`` the
first time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from sbfsem.analysis.boundary import TYPE_IDS, fetch_boundary
from sbfsem.core.volumes import validate_source
from sbfsem.io.odata import ODataClient


def main(source: str) -> None:
    canonical = validate_source(source)
    out_dir = Path(__file__).resolve().parents[1] / "src" / "sbfsem" / "data"
    client = ODataClient(canonical)
    for layer in TYPE_IDS:
        markers = fetch_boundary(client, layer).marker_locations
        path = out_dir / f"{canonical.upper()}_{layer}.txt"
        np.savetxt(path, np.rint(markers).astype(int), fmt="%d", delimiter=",")
        print(f"{path.name}: {len(markers)} markers")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
