"""Geometry after the 2026-10-15 audit (W9): the defects it reproduced, one test each.

None of these changes a surface the product draws: they are inputs that used to
raise (a NaN control point), a library argument nothing passes (``resize``), a
mesh nobody draws on RC1 or RC2 (the cone-outline slab), and a flag nothing read.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sbfsem.geometry.closed_curve import ClosedCurveAnnotation, build_curve_volume
from sbfsem.geometry.cone_outline import (
    DEFAULT_SLAB_UM,
    ConeOutline,
    cone_outline_mesh,
    section_thickness_um,
)
from sbfsem.geometry.unify import MIN_VOXEL_UM, TYPED_MIN_VOXEL_UM, unified_mesh
from sbfsem.geometry.volume import volume_to_mesh

SQUARE = np.array([[0, 0], [4, 0], [4, 4], [0, 4], [2, 1]], dtype=float)


# -- G04: a NaN control point in a closed-curve outline -------------------------
@pytest.mark.parametrize("nan_at", [0, 2])
def test_an_outline_with_a_nan_control_point_builds(nan_at):
    """ConeOutline and the cut-outs were repaired; the outline of a
    ClosedCurveAnnotation was not, and build_curve_volume raised."""
    outline = SQUARE.copy()
    outline[nan_at] = np.nan
    annotation = ClosedCurveAnnotation(outline=outline, z_section=10, z_um=0.7)
    assert np.isfinite(annotation.outline).all()
    volume = build_curve_volume([annotation], pixel_size_um=0.1, volume_scale=(7.5, 7.5, 70.0))
    assert volume.shape[:2] == (51, 51)
    assert volume.dense().any()


def test_a_clean_outline_is_untouched_by_the_repair():
    annotation = ClosedCurveAnnotation(outline=SQUARE, cutouts=[SQUARE[:4] + 0.5],
                                       z_section=1, z_um=0.07)
    np.testing.assert_array_equal(annotation.outline, SQUARE)
    np.testing.assert_array_equal(annotation.cutouts[0], SQUARE[:4] + 0.5)


# -- G05: volume_to_mesh with resize ---------------------------------------------------
def test_a_resized_volume_gives_a_mesh_of_the_same_size():
    """The mesh came back ``resize`` times too small in x and y."""
    volume = np.zeros((60, 80, 12), dtype=bool)       # (rows = y, columns = x, slices = z)
    volume[10:50, 10:70, 3:9] = True
    whole = volume_to_mesh(volume, spacing=(0.5, 0.25, 1.0))
    half = volume_to_mesh(volume, spacing=(0.5, 0.25, 1.0), resize=0.5)
    assert not half.is_empty
    lo_w, hi_w = whole.vertices.min(axis=0), whole.vertices.max(axis=0)
    lo_h, hi_h = half.vertices.min(axis=0), half.vertices.max(axis=0)
    # Within a voxel of the resized grid (2 input voxels), not half the extent.
    np.testing.assert_allclose(hi_h[0] - lo_h[0], hi_w[0] - lo_w[0], atol=2 * 0.5)
    np.testing.assert_allclose(hi_h[1] - lo_h[1], hi_w[1] - lo_w[1], atol=2 * 0.25)
    assert hi_w[0] - lo_w[0] > 25.0 and hi_w[1] - lo_w[1] > 8.0   # 60 columns x 0.5, 40 rows x 0.25
    # z is not resized.
    np.testing.assert_allclose(hi_h[2] - lo_h[2], hi_w[2] - lo_w[2], atol=1e-6)


@pytest.mark.parametrize("factor", [0.5, 0.7, 0.25])
def test_a_resized_grid_keeps_its_ends_on_the_inputs(factor):
    """The zoomed grid's first and last samples are the input's, so one resized voxel is
    ``(n - 1) / (m - 1)`` input voxels and not ``n / m``.  A box's extent hid the 1.3 to
    1.7 % between them inside the one resized voxel the test above allows, so an iso-plane
    through the middle of a ramp is used: it must stay exactly where it was."""
    rows, cols, slices = 60, 80, 12
    spacing = (0.5, 0.25, 1.0)
    along_x = np.broadcast_to(np.linspace(0.0, 1.0, cols)[None, :, None], (rows, cols, slices)).copy()
    along_y = np.broadcast_to(np.linspace(0.0, 1.0, rows)[:, None, None], (rows, cols, slices)).copy()
    for ramp, axis in ((along_x, 0), (along_y, 1)):
        plane = volume_to_mesh(ramp, spacing=spacing, do_smooth=False, cap=False)
        resized = volume_to_mesh(ramp, spacing=spacing, do_smooth=False, cap=False, resize=factor)
        assert np.ptp(plane.vertices[:, axis]) < 1e-9, "the reference is one plane"
        np.testing.assert_allclose(resized.vertices[:, axis], plane.vertices[:, axis].mean(), atol=1e-6)


def test_an_unresized_volume_is_unchanged():
    volume = np.zeros((20, 20, 6), dtype=bool)
    volume[5:15, 5:15, 2:4] = True
    a = volume_to_mesh(volume, spacing=(0.1, 0.1, 0.07), origin=(1.0, 2.0, 3.0))
    b = volume_to_mesh(volume, spacing=(0.1, 0.1, 0.07), origin=(1.0, 2.0, 3.0), resize=1.0)
    np.testing.assert_array_equal(a.vertices, b.vertices)


# -- G06: the cone-outline slab ------------------------------------------------------------
def _outline(z_section, z_um):
    return ConeOutline(outline=SQUARE[:4] * 0.25, cutouts=[], z_section=z_section, z_um=z_um)


def test_the_slab_is_one_section_thick_whatever_the_volume():
    for dz in (0.07, 0.09, 0.075):
        outlines = [_outline(100, 100 * dz), _outline(101, 101 * dz)]
        assert section_thickness_um(outlines) == pytest.approx(dz)
        mesh = cone_outline_mesh(outlines, budget=40)
        z = mesh.vertices[:, 2]
        # Two slabs, each ``dz`` thick, centered on their sections.
        assert z.max() - z.min() == pytest.approx(dz * 2, abs=1e-6)
        assert mesh.vertices[:, 2].min() == pytest.approx(100 * dz - dz / 2, abs=1e-6)


def test_the_slab_thickness_can_still_be_asked_for_and_falls_back_to_matlab_s():
    outlines = [_outline(100, 7.0)]
    thick = cone_outline_mesh(outlines, thickness_um=0.5, budget=40)
    assert np.ptp(thick.vertices[:, 2]) == pytest.approx(0.5)
    assert section_thickness_um([_outline(0, 0.0)]) is None
    flat = cone_outline_mesh([_outline(0, 0.0)], budget=40)
    assert np.ptp(flat.vertices[:, 2]) == pytest.approx(DEFAULT_SLAB_UM)


def test_the_dead_outline_helpers_are_gone():
    import sbfsem.geometry.cone_outline as cone

    assert not hasattr(cone, "cone_outline_from_wkt")
    assert not hasattr(ConeOutline, "spline_3d")
    assert not hasattr(cone, "parse_closed_curve")


# -- G07/G10: a typed fine voxel the floor raised ---------------------------------------------
def _stretch():
    x = np.linspace(0.0, 6.0, 7)
    pts = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
    return [("stretch", pts, np.full(len(x), 1.0))]


def test_a_fine_voxel_under_the_floor_is_reported_as_coarsened():
    """0.01 was asked for and 0.04 came out, with ``voxel2_coarsened`` False.  (A typed
    voxel's floor is 0.02 um since 2026-10-16; the automatic one's stays 0.04.)"""
    mesh = unified_mesh(_stretch(), voxel_um=0.01, tie_um=0.0)
    assert mesh.meta["voxel2_um"] == pytest.approx(TYPED_MIN_VOXEL_UM)
    # The voxel sized from the annotations keeps its own floor: no surface built
    # without a typed voxel changed.
    thin = [("stretch", _stretch()[0][1], np.full(7, 0.03))]
    assert unified_mesh(thin, voxel_um=0.0, tie_um=0.0).meta["voxel2_um"] == pytest.approx(MIN_VOXEL_UM)
    assert mesh.meta["voxel2_coarsened"] is True
    # And why, for the page to say (2026-10-16): the floor, not a budget.
    assert mesh.meta["voxel2_reason"] == "floor"
    assert mesh.meta["voxel2_asked"] == pytest.approx(0.01)
    honored = unified_mesh(_stretch(), voxel_um=0.2, tie_um=0.0)
    assert honored.meta["voxel2_um"] == pytest.approx(0.2)
    assert honored.meta["voxel2_coarsened"] is False
    assert honored.meta["voxel2_reason"] == ""
    # A raw-triangle budget too small for the asked voxel coarsens it, and says so.
    budget = unified_mesh(_stretch(), voxel_um=0.05, tie_um=0.0, max_raw_faces=2_000)
    assert budget.meta["voxel2_coarsened"] is True
    assert budget.meta["voxel2_reason"] == "faces"
    assert budget.meta["voxel2_um"] > 0.05
    work = unified_mesh(_stretch(), voxel_um=0.05, tie_um=0.0, max_work=50_000)
    assert work.meta["voxel2_reason"] == "work"


# -- G20: the closed-curve overlay splines its cut-outs too ---------------------------------------
def test_the_closed_curve_dto_splines_the_cutouts_as_it_splines_the_outline(i943_path):
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    neuron = read_legacy_json(i943_path)
    outline = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    hole = np.array([[3, 3], [6, 3], [6, 6], [3, 6]], dtype=float)
    neuron.geometries = pd.DataFrame([
        {"ID": 1, "ParentID": neuron.id, "Z": 5, "Zum": 0.45, "Curve": [outline, hole],
         "Registered": False},
        {"ID": 2, "ParentID": neuron.id, "Z": 6, "Zum": 0.54, "Curve": [outline, hole],
         "Registered": True},
    ])
    svc = SbfsemService(offline=True)
    try:
        svc.add_neuron(neuron)
        control, registered = svc.closed_curves(neuron.id, neuron.source, spline_points=8)
        assert len(control.outline_um) > 4 and len(control.cutouts_um[0]) > 4
        assert len(control.cutouts_um[0]) == len(control.outline_um)   # both 4 points, both splined
        # A registered curve is already evaluated: sent as it is.
        assert len(registered.outline_um) == 4 and len(registered.cutouts_um[0]) == 4
    finally:
        svc.close()
