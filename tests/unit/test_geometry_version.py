"""Ground rule 12 as a test: geometry from unchanged annotations changes only with a version bump.

The on-disk mesh key is the render options, the annotations' stamp and
``GEOMETRY_VERSION``.  A change in how annotations become geometry that does not
bump the version leaves production serving the old surface from cache for as long
as the annotations stay as they are.  Two things pin that here:

* the key really carries the version and every option (a bump forgets what is on
  disk, and an option nobody put in the key would share a mesh between settings);
* a fingerprint of two cells' surfaces (``tests/fixtures/geometry_fingerprint.json``)
  fails when a surface changes with the version where it was: c943, which has
  discs and no traced outline, under six settings, and the three traced
  outlines of the cone-pedicle fixture under three.  Each entry is the vertex
  and face counts, the area, the volume and the centroid, because a surface that
  moved as a whole (a frame, an offset, a shift of every outline) keeps every
  count, area and volume.  Not covered: a cell drawn through a registration or a
  section correction (that needs the volume's transforms), and a cell with traced
  outlines and discs together.  If the change was meant: bump ``GEOMETRY_VERSION``
  in ``service/facade.py``, then regenerate the file with
  ``python tests/unit/test_geometry_version.py --write``.  If it was not, the test
  just found a bug.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sbfsem.geometry import closed_curve
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service import facade as facade_module
from sbfsem.service.facade import GEOMETRY_VERSION, SbfsemService

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "i943.json"
#: Three traced outlines in one plane (a cone pedicle), the only fixture whose
#: surfaces are built from outline paths and not from discs.
OUTLINES_FIXTURE = ROOT / "tests" / "fixtures" / "i90000_closedcurve.json"
FINGERPRINT = ROOT / "tests" / "fixtures" / "geometry_fingerprint.json"

#: Settings fast enough to build in a test (the cell has discs only, so Closed
#: curve rasterizes them): one for each way the geometry step is reached.
SETTINGS = {
    "cylinder-original": {"method": "cylinder", "pathway": "original", "smooth_iterations": 0},
    "cylinder-adaptive": {"method": "cylinder", "pathway": "adaptive_taubin", "smooth_iterations": 1},
    "tube": {"method": "curvemesh", "pathway": "original", "smooth_iterations": 0},
    "voxel": {"method": "cylinder", "pathway": "voxel", "smooth_iterations": 1},
    "voxel2": {"method": "cylinder", "pathway": "voxel2", "smooth_iterations": 1},
    "closedcurve": {"method": "closedcurve", "pixel_size_um": 0.25},
}

#: The same for the cell traced with outlines: Closed curve at two rasters, and
#: Hybrid (its curve half is the outlines; it has no discs).
OUTLINE_SETTINGS = {
    "outlines-closedcurve": {"method": "closedcurve", "pixel_size_um": 0.25},
    "outlines-closedcurve-fine": {"method": "closedcurve", "pixel_size_um": 0.1},
    "outlines-hybrid": {"method": "hybrid", "pixel_size_um": 0.25, "pathway": "original",
                        "smooth_iterations": 0},
}


def _record(mesh) -> list:
    """[vertices, faces, area, volume, centroid x, y, z]: a changed surface changes
    them, a different BLAS does not (areas to a tenth of a µm²).  The centroid is
    there because a surface moved whole keeps all the others."""
    centroid = mesh.vertices.mean(axis=0)
    return [int(mesh.n_vertices), int(len(mesh.faces)),
            round(float(mesh.surface_area()), 1), round(float(mesh.volume()), 1),
            *(round(float(c), 1) for c in centroid)]


def fingerprint() -> dict:
    """The record of each cell's surface under each setting."""
    service = SbfsemService(offline=True)
    try:
        out = {}
        for fixture, settings in ((FIXTURE, SETTINGS), (OUTLINES_FIXTURE, OUTLINE_SETTINGS)):
            neuron = read_legacy_json(fixture)
            for name, options in settings.items():
                out[name] = _record(service.build_mesh(neuron, **options))
        return {"geometry_version": GEOMETRY_VERSION, "meshes": out}
    finally:
        service.close()


def test_a_surface_changes_only_with_a_version_bump():
    recorded = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    now = fingerprint()
    if recorded["geometry_version"] != GEOMETRY_VERSION:
        pytest.fail(
            f"GEOMETRY_VERSION is {GEOMETRY_VERSION} and the fingerprint was taken at "
            f"{recorded['geometry_version']}: regenerate it with "
            "`python tests/unit/test_geometry_version.py --write`.")
    changed = {name: (recorded["meshes"].get(name), values)
               for name, values in now["meshes"].items() if recorded["meshes"].get(name) != values}
    assert recorded["meshes"].keys() == now["meshes"].keys(), "a setting was added or removed: regenerate it"
    assert not changed, (
        f"a surface changed under {sorted(changed)} (recorded, now: {changed}) and "
        f"GEOMETRY_VERSION is still {GEOMETRY_VERSION}. A change in how annotations become "
        "geometry must bump it (CLAUDE.md ground rule 12) or production keeps serving the old "
        "surface from the disk cache; then regenerate the fingerprint with "
        "`python tests/unit/test_geometry_version.py --write`. If you did not mean to change "
        "a surface, this is the bug.")


def test_the_disk_key_carries_the_version_and_every_option(tmp_path, monkeypatch):
    """A bump forgets what is on disk, and an option left out of the key would share
    one stored mesh between two settings."""
    keys = []
    real_put = facade_module.MeshDiskCache.put

    def spy(self, key, mesh):
        keys.append(key)
        return real_put(self, key, mesh)

    monkeypatch.setattr(facade_module.MeshDiskCache, "put", spy)

    def render(method="cylinder", **options):
        service = SbfsemService(offline=True, cache_dir=tmp_path)
        service.add_neuron(read_legacy_json(FIXTURE))
        try:
            return service.render_mesh(943, "i", method=method, smooth_iterations=0, **options)
        finally:
            service.close()

    render()
    (key,) = keys
    # (cell, method, passes, decimate, voxel, options, annotations' stamp, count, version)
    assert key[1:5] == ("cylinder", 0, 1.0, 0.05)
    # Every option is in the key -- the closed-curve averaging ones too, once they are on.
    # While they are off they are left out: a surface stored before they existed is the
    # one asked for with them off, and the deploy that added them kept every one.
    stored = {k: v for k, v in SbfsemService.MESH_OPTION_DEFAULTS.items()
              if k not in SbfsemService.UNKEYED_AT_OFF}
    assert dict(key[5]) == stored, "every option is in the key"
    # The Closed curve options of 2026-10-16 (curved links, joined bodies, and the two
    # bridges that were constants) join them, each left out at the value every surface
    # stored before it was built with: off, or the constant's own value.
    assert set(SbfsemService.UNKEYED_AT_OFF) == {
        "curve_avg_discs", "curve_avg_paths", "curve_links_um", "curve_tie_um",
        "curve_bridge_um", "curve_link_bridge_um", "surface_smooth_um", "exact_tube_deg"}
    assert all(SbfsemService.MESH_OPTION_DEFAULTS[k] == v
               for k, v in SbfsemService.UNKEYED_AT_OFF.items())
    assert SbfsemService.UNKEYED_AT_OFF["curve_bridge_um"] == closed_curve.BRIDGE_UM == 1.0
    assert SbfsemService.UNKEYED_AT_OFF["curve_link_bridge_um"] == closed_curve.LINK_BRIDGE_UM == 5.0
    assert key[8] == GEOMETRY_VERSION

    # The same render again is the stored one; after a bump it is built again, and
    # kept under a key of its own beside the first.
    keys.clear()
    render()
    assert keys == [], "the stored surface was used"
    monkeypatch.setattr(facade_module, "GEOMETRY_VERSION", GEOMETRY_VERSION + 1)
    render()
    assert len(keys) == 1 and keys[0][8] == GEOMETRY_VERSION + 1 and keys[0] != key
    # An option that changes the geometry is in the key: another tie gap is another file.
    keys.clear()
    render(tie_um=3.0)
    assert len(keys) == 1 and dict(keys[0][5])["tie_um"] == 3.0
    # The outline methods' own options change nothing a Cylinder build makes, so at any value
    # they leave its key alone: the viewer sends Join bodies at 5 um with every build since
    # 2.6.2, and every Cylinder and Voxel remesh surface stored before is still the one served.
    keys.clear()
    render(curve_avg_paths=0.25, curve_links_um=1.0, curve_tie_um=5.0,
           curve_bridge_um=2.0, curve_link_bridge_um=1.0)
    assert keys == [], "options a Cylinder build does not read are not in its key"
    # Disc avg reaches the tubes (2.7.0): on, it is another file; off, the stored one.
    keys.clear()
    render(curve_avg_discs=0.5)
    assert len(keys) == 1 and dict(keys[0][5])["curve_avg_discs"] == 0.5
    keys.clear()
    render(curve_avg_discs=0.0)
    assert keys == [], "Disc avg off is the surface stored before it reached the tubes"
    # Smooth surface and Exact tubes (2.9.0): on, another file; off, the stored one.
    keys.clear()
    render(surface_smooth_um=0.25)
    assert len(keys) == 1 and dict(keys[0][5])["surface_smooth_um"] == 0.25
    keys.clear()
    render(surface_smooth_um=0.0)
    assert keys == [], "Smooth surface off is the surface stored before it existed"
    keys.clear()
    render(exact_tube_deg=35.0)
    assert len(keys) == 1 and dict(keys[0][5])["exact_tube_deg"] == 35.0
    keys.clear()
    render(exact_tube_deg=0.0)
    assert keys == [], "Exact tubes off is the surface stored before it existed"
    # Under Closed curve the averaging once it is on is another file: another strength,
    # another file.
    keys.clear()
    render("closedcurve")
    (curve_key,) = keys
    keys.clear()
    render("closedcurve", curve_avg_discs=0.5, curve_avg_paths=0.25)
    options = dict(keys[0][5])
    assert len(keys) == 1 and options["curve_avg_discs"] == 0.5 and options["curve_avg_paths"] == 0.25
    keys.clear()
    render("closedcurve", curve_avg_discs=0.5)
    assert len(keys) == 1 and "curve_avg_paths" not in dict(keys[0][5])
    # Exact tubes draws tubes, which Closed curve has none of: at any value, its key alone.
    keys.clear()
    render("closedcurve", exact_tube_deg=35.0)
    assert keys == [], "Closed curve does not read Exact tubes"
    # A bridge at another reach, curved links and joined bodies are each another file; the
    # bridges at their constants' values are the surface stored before they were options.
    for option, value in (("curve_bridge_um", 2.0), ("curve_link_bridge_um", 1.0),
                          ("curve_links_um", 1.0), ("curve_tie_um", 2.0)):
        keys.clear()
        render("closedcurve", **{option: value})
        assert len(keys) == 1 and dict(keys[0][5])[option] == value, option
    keys.clear()
    render("closedcurve", curve_bridge_um=1.0, curve_link_bridge_um=5.0, curve_links_um=0.0,
           curve_tie_um=0.0)
    assert keys == [], "the defaults spelled out are the stored surface"
    assert "curve_tie_um" not in dict(curve_key[5])


if __name__ == "__main__":  # pragma: no cover - regenerate the fixture
    if "--write" not in sys.argv:
        raise SystemExit(__doc__)
    FINGERPRINT.write_text(json.dumps(fingerprint(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {FINGERPRINT}")
