"""Hybrid as one surface, and Disc avg on the tubes (2.7.0).

The user, 2026-10-17, on Inferior c1178 under Hybrid / Voxel remesh: "the top of the
soma (when it switches back to disc) is a separate wireframe body".  Hybrid built its
outline half and its disc half apart and laid them side by side: five sheets, the
disc-drawn soma top a closed shell sitting on the traced soma.  Under a pathway that
contours one whole-cell field the outline raster now joins that field
(``unify._CurveLeaf``), so the two halves are one surface.

And: "why the discs create such extra volume".  A tube is the union of round cones
through each annotated disc, so it takes the outer edge of every wobble of the centers
and radii; Disc avg averages the discs along their chains first.
"""

from __future__ import annotations

import numpy as np
import pytest

from sbfsem.geometry.closed_curve import ClosedCurveAnnotation, build_curve_volume
from sbfsem.geometry.unify import split_components, unified_mesh
from sbfsem.geometry.volume import curve_volume_to_mesh
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service import facade as facade_module
from sbfsem.service.facade import SbfsemService, _averaged_discs, _hybrid_token


def _ellipse(cx, cy, a, b, n=64):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([cx + a * np.cos(t), cy + b * np.sin(t)])


def _soma_volume(pixel=0.1):
    """A traced body: ellipses over 30 sections, widest in the middle."""
    annotations = []
    for i, z in enumerate(range(100, 130)):
        scale = np.sin(np.pi * (i + 0.5) / 30)
        annotations.append(ClosedCurveAnnotation(
            outline=_ellipse(10.0, 10.0, 0.5 + 4.0 * scale, 0.5 + 2.5 * scale), z_section=z,
            location_id=1000 + i, registered=True))
    return build_curve_volume(annotations, volume_scale=(7.5, 7.5, 90.0), pixel_size_um=pixel)


def test_the_curve_leaf_reproduces_the_outline_half():
    """Contoured alone in the whole-cell field, the raster is the surface the outline
    half makes of it: the same volume and area within a few percent, every vertex
    within a pixel of it."""
    volume = _soma_volume()
    alone = curve_volume_to_mesh(volume)
    field = unified_mesh([("curve", volume, alone.surface_area())], voxel_um=0.1, tie_um=0)
    assert len(split_components(field)) == 1
    assert field.volume() == pytest.approx(alone.volume(), rel=0.03)
    assert field.surface_area() == pytest.approx(alone.surface_area(), rel=0.05)
    from scipy.spatial import cKDTree

    gap, _ = cKDTree(alone.vertices).query(field.vertices)
    assert float(np.percentile(gap, 99)) < 0.1


def test_a_tube_into_the_raster_is_one_surface():
    """A round-cone chain leaving the traced body merges into its sheet: one piece,
    where the two built apart and laid side by side are two."""
    volume = _soma_volume()
    alone = curve_volume_to_mesh(volume)
    stretch = ("stretch", np.array([[10.0, 10.0, 10.0], [10.0, 10.0, 13.5], [10.5, 10.0, 16.0]]),
               np.array([0.6, 0.5, 0.4]))
    tube = unified_mesh([stretch], voxel_um=0.1, tie_um=0)
    both = unified_mesh([stretch, ("curve", volume, alone.surface_area())], voxel_um=0.1, tie_um=0)
    assert len(split_components(both)) == 1
    assert len(split_components(alone)) + len(split_components(tube)) == 2
    # The union holds both and no more: nothing of the raster is lost under the tube.
    assert both.volume() < alone.volume() + tube.volume()
    assert both.volume() > max(alone.volume(), tube.volume())


def test_convolution_refuses_a_raster():
    volume = _soma_volume(pixel=0.25)
    with pytest.raises(TypeError, match="curve raster"):
        unified_mesh([("curve", volume, 100.0)], mode="sum")


@pytest.fixture(scope="module")
def mixed_service(c1178):
    service = SbfsemService(offline=True)
    service.add_neuron(c1178)
    yield service
    service.close()


def test_hybrid_on_c1178_is_one_surface(mixed_service):
    """c1178's soma is 130 traced outlines with a disc-drawn top; its arbor is discs.
    Hybrid under Voxel remesh is one sheet (it was five), the outline half's triangles
    join the Face cap, and the piece pathways still lay the halves side by side."""
    neuron = mixed_service.neuron(1178, "i")
    options = {"pathway": "voxel2", "voxel2_faces": 150_000}
    one = mixed_service._build_geometry(neuron, "hybrid", 1, 1.0, 0.25, options)
    assert one.meta.get("hybrid_one_field") is True
    assert len(split_components(one)) == 1
    curve = mixed_service._contour_curve(mixed_service._curve_volume(neuron, 0.25), 0.0, 0.0)
    assert len(one.faces) <= 150_000 + len(curve.faces)
    assert len(one.faces) > 150_000
    apart = mixed_service._build_geometry(neuron, "hybrid", 0, 1.0, 0.25,
                                          {"pathway": "adaptive_taubin"})
    assert "hybrid_one_field" not in apart.meta
    assert len(split_components(apart)) > 1


def test_only_a_one_field_hybrid_of_a_traced_cell_changes_its_key(c943, c1178):
    """The surfaces built before 2.7.0 are kept under every other key."""
    assert _hybrid_token("hybrid", {"pathway": "voxel2"}, c1178) == "hybrid:one-field"
    assert _hybrid_token("hybrid", {"pathway": "ultraliser"}, c1178) == "hybrid:one-field"
    assert _hybrid_token("hybrid", {"pathway": "convolution"}, c1178) is None
    assert _hybrid_token("hybrid", {"pathway": "original"}, c1178) is None
    assert _hybrid_token("hybrid", {"pathway": "voxel2"}, c943) is None
    assert _hybrid_token("cylinder", {"pathway": "voxel2"}, c1178) is None
    assert facade_module.HYBRID_ONE_FIELD == ("voxel2", "ultraliser")


def test_disc_avg_off_leaves_the_cell_as_it_is(c943):
    assert _averaged_discs(c943, 0.0) is c943


def test_disc_avg_averages_the_discs_along_their_chains(i943_path):
    """On, the discs a tube runs through are averaged (centers and radii) and the rest
    of the cell is untouched; the tube built through them is a different, slimmer one."""
    neuron = read_legacy_json(i943_path)
    averaged = _averaged_discs(neuron, 1.0)
    assert averaged is not neuron and averaged.nodes is not neuron.nodes
    before, after = neuron.nodes.set_index("ID"), averaged.nodes.set_index("ID")
    assert (before["Zum"] == after["Zum"]).all(), "a disc stays in its own section"
    moved = (before[["Xum", "Yum", "Rum"]] - after[["Xum", "Yum", "Rum"]]).abs().max(axis=1) > 1e-9
    assert 0 < int(moved.sum()) < len(before)
    assert neuron.analysis is not averaged.analysis
    service = SbfsemService(offline=True)
    service.add_neuron(neuron)
    try:
        options = {"pathway": "voxel2", "voxel2_faces": 100_000}
        plain = service._build_geometry(neuron, "cylinder", 1, 1.0, 0.05, options)
        smooth = service._build_geometry(neuron, "cylinder", 1, 1.0, 0.05,
                                         {**options, "curve_avg_discs": 1.0})
        assert smooth.volume() < plain.volume()
    finally:
        service.close()


def test_a_raster_coarsened_for_the_work_budget_is_still_whole():
    """The grid is built again, coarser, when the evaluation budget is spent; the
    raster's blocks are looked up for each grid, not kept from the first."""
    volume = _soma_volume()
    alone = curve_volume_to_mesh(volume)
    fine = unified_mesh([("curve", volume, alone.surface_area())], voxel_um=0.05, tie_um=0)
    tight = unified_mesh([("curve", volume, alone.surface_area())], voxel_um=0.05, tie_um=0,
                         max_work=300_000)
    assert tight.meta["voxel2_reason"] == "work" and tight.meta["voxel2_um"] > 0.05
    assert len(split_components(tight)) == 1
    assert tight.volume() == pytest.approx(fine.volume(), rel=0.05)
