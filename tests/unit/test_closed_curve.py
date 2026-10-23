"""Closed-curve and cone-outline pipeline.

``sbfsem.image.ConeOutline`` did not load at all in MATLAB (F164) and closed its
Catmull-Rom loop with the wrong control points (F165), so there is no MATLAB
output to compare against.  These check the geometry against analytic values
instead: a ring of known radius must enclose the right area, rasterize to the
right number of pixels, and extrude to the right volume.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import sbfsem.geometry.closed_curve as closed_curve
import sbfsem.geometry.volume as volume_module
from sbfsem.geometry.closed_curve import (
    DISC_RING_POINTS,
    ClosedCurveAnnotation,
    _curve_grid,
    annotations_from_discs,
    annotations_from_geometries,
    build_curve_volume,
    curve_grid_voxels,
    interpolate_masks,
    link_run_masks,
)
from sbfsem.geometry.cone_outline import (
    ConeOutline,
    cone_outline_mesh,
    cone_outlines_from_neuron,
)
from sbfsem.geometry.mesh import EMPTY_MESH, concatenate
from sbfsem.geometry.rasterize import rasterize_rings
from sbfsem.geometry.unify import split_components
from sbfsem.geometry.volume import curve_volume_to_mesh, volume_to_mesh
from sbfsem.io.legacy_json import read_legacy_json

THETA = np.linspace(0, 2 * np.pi, 33)[:-1]

#: app.js FINEST_PIXEL: how the page reads either size refusal.
FINEST_PIXEL = (r"would need ([\d,]+) (voxels|voxel faces).*?the limit is ([\d,]+)\. "
                r"The finest pixel that fits this structure is ([\d.]+) um")


def ring(radius, cx=10.0, cy=12.0):
    return np.column_stack([radius * np.cos(THETA) + cx, radius * np.sin(THETA) + cy])


@pytest.fixture(scope="module")
def cone_neuron(request):
    return read_legacy_json(
        request.path.parents[1] / "fixtures" / "i90000_closedcurve.json"
    )


def test_outline_area_and_diameter():
    outline = ConeOutline(ring(4.0), [ring(1.2)], 1500, 135.0)
    assert outline.area_um2 == pytest.approx(np.pi * (16 - 1.44), rel=0.01)
    expected_d = 2 * np.sqrt((np.pi * (16 - 1.44)) / np.pi)
    assert outline.equivalent_diameter_um == pytest.approx(expected_d, rel=0.01)
    np.testing.assert_allclose(outline.centroid_um, [10, 12], atol=0.05)


def test_measures_are_the_three_properties_from_one_set_of_rings():
    outline = ConeOutline(ring(4.0), [ring(1.2)], 1500, 135.0)
    area, diameter, centroid = outline.measures()
    assert area == outline.area_um2 and diameter == outline.equivalent_diameter_um
    np.testing.assert_array_equal(centroid, outline.centroid_um)
    assert outline.measures(outline.rings()) == (area, diameter, pytest.approx(centroid))


def test_spline_closes_with_the_first_control_points():
    """F165: ConeOutline appended the last three points, which does not close."""
    outline = ConeOutline(ring(4.0), [], 1, 0.09)
    curve = outline.spline()
    assert np.allclose(curve[0], curve[-1], atol=1e-9)


def test_outline_handles_too_few_points():
    # F158: MATLAB indexed x0(1:3) unconditionally.
    outline = ConeOutline(np.array([[0.0, 0.0], [1.0, 1.0]]), [], 1, 0.09)
    assert outline.spline().shape == (2, 2)
    assert outline.area_um2 == 0.0


def test_outline_repairs_a_leading_nan_cutout():
    cut = ring(1.2).copy()
    cut[0] = [np.nan, np.nan]
    outline = ConeOutline(ring(4.0), [cut], 1, 0.09)
    assert np.isfinite(np.concatenate(outline.rings())).all()


def test_mask_area():
    outline = ConeOutline(ring(4.0), [], 1, 0.09)
    mask = outline.mask((500, 500), pixel_size_um=0.05, origin=(0, 0))
    assert mask.sum() * 0.05**2 == pytest.approx(np.pi * 16, rel=0.01)


def test_from_neuron_and_mesh(cone_neuron):
    outlines = cone_outlines_from_neuron(cone_neuron)
    assert len(outlines) == 3
    assert cone_outlines_from_neuron(cone_neuron, z_section=1501)[0].z_section == 1501

    # Each outline is its own Viking annotation: it carries that Location ID,
    # not the parent structure's repeated (what the closed-curves endpoint
    # served until 2026-09-18, so every outline of a cell shared one "ID").
    ids = [o.location_id for o in outlines]
    assert ids == [int(i) for i in cone_neuron.geometries["ID"]]
    assert len(set(ids)) == 3

    mesh = cone_outline_mesh(outlines)
    # Three 0.09 um slabs of an annulus, cut-outs subtracted.
    assert mesh.volume() == pytest.approx(np.pi * (16 - 1.44) * 0.09 * 3, rel=0.02)


def test_curve_volume_render(cone_neuron):
    annotations = annotations_from_geometries(cone_neuron.geometries)
    cv = build_curve_volume(annotations, volume_scale=cone_neuron.volume_scale)
    mesh = curve_volume_to_mesh(cv)
    assert mesh.volume() == pytest.approx(np.pi * (16 - 1.44) * 0.09 * 3, rel=0.05)
    # F096: no 100x rasterization factor left in; the mesh is in microns.
    assert 5 < mesh.bounds[0, 0] < 7


def test_volume_guards_against_pixel_units():
    """Outlines in Viking pixels inflate the grid ~130x per axis."""
    pixel_ring = ring(4.0) / 0.0075
    annotation = ClosedCurveAnnotation(pixel_ring, [], 1500, 135.0)
    with pytest.raises(ValueError, match="microns"):
        build_curve_volume([annotation], volume_scale=(7.5, 7.5, 90.0))


def test_a_pixel_too_fine_for_the_structure_names_one_that_fits():
    """RPC1 c2628 at 0.025 um: the refusal says what to ask for instead."""
    stack = [ClosedCurveAnnotation(ring(4.0), [], z, z * 0.09) for z in range(100, 140)]
    with pytest.raises(ValueError, match=r"limit is 1,000,000") as refusal:
        build_curve_volume(stack, pixel_size_um=0.025, max_voxels=1_000_000)
    fits = float(re.search(r"fits this structure is ([\d.]+) um", str(refusal.value)).group(1))
    assert 0.025 < fits < 0.2
    volume = build_curve_volume(stack, pixel_size_um=fits, max_voxels=1_000_000)
    assert 0.5 < volume.allocated_voxels / 1_000_000 <= 1.0
    # The finest: a thousandth of a micron less is refused.
    with pytest.raises(ValueError, match=r"limit is 1,000,000"):
        build_curve_volume(stack, pixel_size_um=round(fits - 0.001, 3), max_voxels=1_000_000)
    finest = re.search(FINEST_PIXEL, str(refusal.value))
    assert finest and finest.group(2) == "voxels" and float(finest.group(4)) == fits
    assert int(finest.group(1).replace(",", "")) > 1_000_000


def test_empty_input_raises():
    with pytest.raises(ValueError):
        build_curve_volume([])


def test_registered_outlines_are_not_splined_again():
    """A registered outline is Viking's evaluated curve: 331 points on a c1178
    soma section against 37 control points.  Splining it again would only
    multiply the points (and the raster cost) without changing the shape."""
    outline = ring(2.0)
    plain = ClosedCurveAnnotation(outline, [ring(0.5)], z_section=3, z_um=0.27)
    kept = ClosedCurveAnnotation(outline, [ring(0.5)], z_section=3, z_um=0.27, registered=True)
    assert len(plain.rings(20)[0]) > len(outline)
    np.testing.assert_array_equal(kept.rings(20)[0], outline)
    np.testing.assert_array_equal(kept.rings(20)[1], ring(0.5))
    cone = ConeOutline(outline, [ring(0.5)], 3, 0.27, registered=True)
    np.testing.assert_array_equal(cone.spline(), outline)
    np.testing.assert_array_equal(cone.rings()[1], ring(0.5))
    assert cone.area_um2 == pytest.approx(np.pi * (4 - 0.25), rel=0.02)


def test_curve_volume_contains_and_touches():
    """A traced soma (here a 4 um ring over three sections) knows what lies
    inside it, within a tolerance, and whether a mesh piece touches it."""
    annotations = [ClosedCurveAnnotation(ring(4.0), [], z, z * 0.09) for z in (100, 101, 102)]
    cv = build_curve_volume(annotations, volume_scale=(7.5, 7.5, 90.0), pixel_size_um=0.1)
    centre = np.array([10.0, 12.0, 101 * 0.09])
    assert cv.contains([centre]).all()
    assert not cv.contains([centre + [6.0, 0, 0]]).any()          # outside the ring
    assert not cv.contains([centre + [0, 0, 5 * 0.09]]).any()      # a section never traced
    assert cv.contains([centre + [0, 0, 5 * 0.09]], tolerance_um=0.5).all()
    assert cv.contains([centre + [4.3, 0, 0]], tolerance_um=0.5).all()   # just past the rim
    assert not cv.contains([centre + [4.3, 0, 0]]).any()
    # A disc piece whose rim reaches the soma touches it; one 2 um away does not.
    rim = ring(1.0, cx=15.0, cy=12.0)
    piece = np.column_stack([rim, np.full(len(rim), centre[2])])
    assert cv.touches(piece)
    assert not cv.touches(piece + [2.0, 0, 0])
    assert not cv.touches(np.zeros((0, 3)))


def _stack(sections, radius=4.0, dz=0.07):
    return [ClosedCurveAnnotation(ring(radius), [], z, z * dz) for z in sections]


def test_skipped_sections_keep_the_stack_at_its_true_height():
    """RPC1's Mueller cell c2628 is traced on 676 of the 754 sections it
    spans.  Stacking only the traced ones, a section apart (what MATLAB
    did), left its surface 5.46 um short and sliding off its own outlines,
    0 um at the bottom and 5.46 um at the top.  Every slice sits at its own
    section number; the skipped ones are interpolated."""
    sections = [s for s in range(500, 560) if s % 7]           # every 7th skipped
    cv = build_curve_volume(_stack(sections), volume_scale=(2.176, 2.176, 70.0),
                            pixel_size_um=0.1)
    np.testing.assert_array_equal(cv.sections, np.arange(sections[0], sections[-1] + 1))
    np.testing.assert_array_equal(cv.traced, np.isin(cv.sections, sections))
    assert cv.runs() == [(0, len(cv.sections))]
    mesh = curve_volume_to_mesh(cv)
    assert mesh.bounds[0, 2] == pytest.approx(sections[0] * 0.07, abs=0.07)
    assert mesh.bounds[1, 2] == pytest.approx(sections[-1] * 0.07, abs=0.07)
    # One unbroken cylinder over the whole span.
    span = (sections[-1] - sections[0] + 1) * 0.07
    assert mesh.volume() == pytest.approx(np.pi * 16 * span, rel=0.05)
    assert len(split_components(mesh)) == 1
    # The top slice lands at the top section, not at the traced count.
    top = cv.voxel_to_um([[0, 0, len(cv.sections) - 1]])[0, 2]
    assert top == pytest.approx(sections[-1] * 0.07)


def test_a_long_skip_stays_open():
    """Two traced stretches 100 sections (7 um) apart are two bodies at
    their own heights: nothing was annotated between them, so nothing is
    drawn there -- and the upper one is not moved down to close the gap."""
    lower, upper = range(500, 510), range(610, 620)
    cv = build_curve_volume(_stack([*lower, *upper]), volume_scale=(2.176, 2.176, 70.0),
                            pixel_size_um=0.1)
    assert cv.runs() == [(0, 10), (10, 20)]
    assert cv.traced.all()
    mesh = curve_volume_to_mesh(cv)
    z = mesh.vertices[:, 2]
    assert z.min() == pytest.approx(500 * 0.07, abs=0.07)
    assert z.max() == pytest.approx(619 * 0.07, abs=0.07)
    assert not ((z > 510 * 0.07) & (z < 609 * 0.07)).any()
    assert mesh.volume() == pytest.approx(np.pi * 16 * 20 * 0.07, rel=0.06)
    assert len(split_components(mesh)) == 2
    centre = np.array([10.0, 12.0, 0.0])
    assert cv.contains([centre + [0, 0, 615 * 0.07]]).all()
    assert not cv.contains([centre + [0, 0, 560 * 0.07]]).any()
    # The slice index maps through the section numbers, open stretch included.
    np.testing.assert_allclose(
        cv.voxel_to_um([[0, 0, 9], [0, 0, 10]])[:, 2], [509 * 0.07, 610 * 0.07])


def test_the_bridge_limit_is_a_distance():
    """One micron is 14 sections at 70 nm and 11 at 90 nm."""
    far = _stack([100, 115])                                    # 1.05 um apart
    near = _stack([100, 114])                                   # 0.98 um
    scale = (2.176, 2.176, 70.0)
    assert len(build_curve_volume(far, volume_scale=scale, pixel_size_um=0.1).runs()) == 2
    bridged = build_curve_volume(near, volume_scale=scale, pixel_size_um=0.1)
    assert bridged.runs() == [(0, 15)] and int(bridged.traced.sum()) == 2
    off = build_curve_volume(near, volume_scale=scale, pixel_size_um=0.1, bridge_um=0)
    assert off.volume.shape[2] == 2 and len(off.runs()) == 2


def test_interpolated_sections_morph_between_their_neighbours():
    """Shape-based interpolation: a 2 um ring below and a 4 um ring above
    give a 3 um ring half way; an outline present on one side only tapers
    off instead of being carried across or dropped at once."""
    annotations = [ClosedCurveAnnotation(ring(2.0), [], 100, 7.0),
                   ClosedCurveAnnotation(ring(4.0), [], 104, 7.28)]
    cv = build_curve_volume(annotations, volume_scale=(2.176, 2.176, 70.0), pixel_size_um=0.05)
    areas = cv.volume.sum(axis=(0, 1)) * 0.05**2
    np.testing.assert_allclose(areas, np.pi * np.array([2.0, 2.5, 3.0, 3.5, 4.0]) ** 2, rtol=0.03)

    grid = np.zeros((80, 80), dtype=bool)
    lone = grid.copy()
    lone[30:50, 30:50] = True
    quarter, half, most = interpolate_masks(lone, grid, [0.25, 0.5, 0.75])
    assert quarter.sum() == lone.sum() and most.sum() == 0     # nothing to morph toward
    other = grid.copy()
    other[10:20, 60:70] = True                                   # elsewhere on the far side
    both = lone | other
    steps = interpolate_masks(both, other, [0.25, 0.5, 0.75])
    inside = [int(m[30:50, 30:50].sum()) for m in steps]
    assert lone.sum() > inside[0] > inside[1] >= inside[2]       # tapers
    assert all(m[10:20, 60:70].all() for m in steps)             # the shared one stays
    assert not any((m & ~both).any() for m in steps)             # never outside the union


def _branch(sections, cx, first_id, radius=2.0, dz=0.07):
    return [ClosedCurveAnnotation(ring(radius, cx=cx), [], z, z * dz, location_id=first_id + i)
            for i, z in enumerate(sections)]


def test_a_link_carries_a_branch_through_sections_the_rest_of_the_cell_was_traced_on():
    """RPC1 c2628: location 144100 on section 1121 links to 144101 on 1125,
    and other branches of the cell were traced on 1122-1124, so those are
    not skipped sections and the whole-section rule leaves them alone.  The
    branch showed a slit three sections tall.  Viking's link says the two
    outlines are one branch; the pair is interpolated on its own."""
    trunk = _branch(range(100, 111), cx=10.0, first_id=1)                 # every section
    twig = _branch([100, 101, 102, 103, 107, 108, 109, 110], cx=20.0, first_id=101)
    link = (twig[3].location_id, twig[4].location_id)                      # 103 <-> 107
    scale = (2.176, 2.176, 70.0)
    plain = build_curve_volume([*trunk, *twig], volume_scale=scale, pixel_size_um=0.1)
    linked = build_curve_volume([*trunk, *twig], volume_scale=scale, pixel_size_um=0.1,
                                links=[link, link[::-1], (1, 999_999)])   # repeats, strangers
    assert plain.traced.all() and linked.traced.all()
    at = {int(z): i for i, z in enumerate(linked.sections)}
    twig_cols = slice(int((20.0 - 2.5 - linked.origin_um[0]) / 0.1), None)
    for z in (104, 105, 106):
        assert not plain.volume[:, twig_cols, at[z]].any()
        assert linked.volume[:, twig_cols, at[z]].sum() == pytest.approx(
            linked.volume[:, twig_cols, at[103]].sum(), rel=0.02)
    # The trunk is untouched, and nothing appears outside the two branches.
    np.testing.assert_array_equal(linked.volume[:, :twig_cols.start, :],
                                  plain.volume[:, :twig_cols.start, :])
    assert len(split_components(curve_volume_to_mesh(plain))) == 3
    assert len(split_components(curve_volume_to_mesh(linked))) == 2


def test_a_link_adds_the_sections_it_crosses_and_stops_at_its_limit():
    """A linked pair 30 sections (2.1 um) apart is past the whole-section
    rule's micron, and is joined anyway: the link is a statement, not a
    guess.  Past ``link_bridge_um`` it is taken for a stray."""
    pair = _branch([100, 130], cx=10.0, first_id=1)
    scale = (2.176, 2.176, 70.0)
    alone = build_curve_volume(pair, volume_scale=scale, pixel_size_um=0.1)
    assert len(alone.runs()) == 2
    joined = build_curve_volume(pair, volume_scale=scale, pixel_size_um=0.1, links=[(1, 2)])
    assert joined.runs() == [(0, 31)] and int(joined.traced.sum()) == 2
    assert joined.volume.any(axis=(0, 1)).all()
    stray = build_curve_volume(pair, volume_scale=scale, pixel_size_um=0.1, links=[(1, 2)],
                               link_bridge_um=2.0)
    assert len(stray.runs()) == 2


def test_linked_outlines_that_do_not_overlap_are_drawn_as_a_run():
    """An oblique branch can land clear of itself a few sections on.  The
    blend of two disjoint outlines tapers to nothing half way.  A linked pair
    is drawn as a run instead: each end moved toward the other, morphing on
    the way, each section the part of it within a section of its plane."""
    grid = np.zeros((60, 120), dtype=bool)
    left, right = grid.copy(), grid.copy()
    left[20:40, 10:30] = True
    right[25:35, 90:100] = True
    span = 6
    assert not interpolate_masks(left, right, [k / span for k in range(1, span)])[2].any()
    run = link_run_masks(left, right, span)
    assert len(run) == span + 1
    assert not (left & ~run[0]).any() and not (right & ~run[-1]).any()   # the ends stay whole
    assert all((a & b).any() for a, b in zip(run[:-1], run[1:], strict=False))  # never cut
    box = np.zeros_like(grid)
    box[20:40, 10:100] = True
    assert not any((m & ~box).any() for m in run)                # within the pair's box
    # Half way: about centered between the two (the larger end's side is the
    # larger), sized between them.
    middle = run[span // 2]
    assert np.argwhere(middle).mean(axis=0) == pytest.approx([29.5, 57.0], abs=3.0)
    assert right.sum() < middle.sum()


def test_the_run_of_two_circles_is_the_disc_run():
    """Two linked discs are drawn as the hull of their circles a section
    either side of each plane (annotations_from_discs).  The run of their
    circles' masks is that hull, to the pixel: every pixel either has and
    the other lacks is on the other's rim."""
    from scipy.ndimage import binary_dilation
    from scipy.spatial import ConvexHull

    unit = np.column_stack([np.cos(np.linspace(0, 2 * np.pi, 256, endpoint=False)),
                            np.sin(np.linspace(0, 2 * np.pi, 256, endpoint=False))])
    shape = (80, 140)
    for p0, r0, p1, r1, span in (((20.3, 30.2), 6.0, (110.7, 50.4), 3.0, 6),
                                 ((15.5, 20.2), 4.0, (40.2, 35.7), 4.0, 2),
                                 ((20.0, 20.0), 8.0, (60.0, 50.0), 2.0, 22)):
        p0, p1 = np.array(p0), np.array(p1)
        run = link_run_masks(rasterize_rings([p0 + r0 * unit], shape),
                             rasterize_rings([p1 + r1 * unit], shape), span)
        for s, mask in enumerate(run):
            faces = (max(0.0, (s - 1) / span), min(1.0, (s + 1) / span))
            circles = np.vstack([p0 + t * (p1 - p0) + (r0 + t * (r1 - r0)) * unit for t in faces])
            hull = rasterize_rings([circles[ConvexHull(circles).vertices]], shape)
            assert not (mask & ~binary_dilation(hull)).any()
            assert not (hull & ~binary_dilation(mask)).any()


def _outline_to_disc(apart_um, top=936):
    """Inferior c1178 in small: a traced outline on section 936 (``top``)
    linked to a 0.4 um disc on 914, ``apart_um`` sideways, nothing annotated
    between."""
    outline = ClosedCurveAnnotation(
        np.column_stack([1.2 * np.cos(THETA) + 10.0, 0.7 * np.sin(THETA) + 12.0]),
        [], top, top * 0.09, location_id=49506)
    (disc,) = annotations_from_discs(
        _discs([(224559, 10.0 + apart_um, 12.0 - 0.4 * apart_um, 914, 0.4)]),
        volume_scale=(2.18, 2.18, 90.0))
    return [outline, disc], [(49506, 224559)]


def test_an_outline_linked_to_a_disc_it_misses_is_one_body():
    """Inferior c1178, Field + fit with live registration: traced outline
    49506 on section 936 links to disc 224559 (r 0.40 um) on 914, across
    916-935 where the volume has no annotations, and the two do not overlap.
    The nearer end carried across each skipped section drew two flat-ended
    slabs, joined to nothing.  Drawn as a run, it is one body; a pair that
    overlaps keeps the plain blend, bit for bit."""
    scale = (2.18, 2.18, 90.0)
    annotations, links = _outline_to_disc(4.0)
    alone = build_curve_volume(annotations, volume_scale=scale, pixel_size_um=0.1)
    assert len(split_components(curve_volume_to_mesh(alone))) == 2
    cv = build_curve_volume(annotations, volume_scale=scale, pixel_size_um=0.1, links=links)
    assert cv.runs() == [(0, 23)] and int(cv.traced.sum()) == 2
    assert len(split_components(curve_volume_to_mesh(cv))) == 1
    # Each section's middle travels from the disc to the outline, as the link does.
    volume = cv.volume
    middles = [np.argwhere(volume[:, :, k]).mean(axis=0)[1] for k in range(volume.shape[2])]
    assert np.all(np.diff(middles) < 0)
    # The run reaches onto the traced sections only by its first and last stretch.
    traced = build_curve_volume(annotations, volume_scale=scale, pixel_size_um=0.1, bridge_um=0)
    for k in (0, -1):
        grown = int(volume[:, :, k].sum()) - int(traced.volume[:, :, k].sum())
        assert 0 < grown < 0.5 * traced.volume[:, :, k].sum()

    near, links = _outline_to_disc(0.6)
    cv = build_curve_volume(near, volume_scale=scale, pixel_size_um=0.1, links=links)
    volume = cv.volume
    plain = interpolate_masks(volume[:, :, 0], volume[:, :, -1],
                              [k / 22 for k in range(1, 22)])
    for k, mask in enumerate(plain, start=1):
        np.testing.assert_array_equal(volume[:, :, k], mask)


def test_the_service_hands_the_links_to_the_curve_volume(cone_neuron):
    from sbfsem.service.facade import _location_links

    links = _location_links(cone_neuron)
    assert all(isinstance(a, int) and isinstance(b, int) for a, b in links)
    assert len(links) == len(cone_neuron.edges)

    class Bare:
        edges = None

    assert _location_links(Bare()) == []


# -- a disc-traced cell's discs as outlines ------------------------------------


def _discs(rows, dz=0.09):
    """A node table of discs from ``(id, x, y, section, radius)`` rows."""
    table = pd.DataFrame(rows, columns=["ID", "Xum", "Yum", "Z", "Rum"])
    table["Zum"] = table["Z"] * dz
    table["TypeCode"] = 1
    return table


def _chain(ids):
    return list(zip(ids[:-1], ids[1:], strict=True))


def test_a_disc_stands_in_as_its_circle_in_its_own_section():
    """Inferior c16137 is 1,933 discs and no closed curve, so Closed curve
    built nothing ("Closed curve needs traced outlines").  Each disc is the
    outline it would be if traced: its circle, in its own section."""
    (circle,) = annotations_from_discs(_discs([(7, 10.0, 12.0, 300, 1.5)]))
    assert circle.registered and circle.location_id == 7
    assert circle.z_section == 300 and circle.z_um == pytest.approx(27.0)
    assert len(circle.outline) == DISC_RING_POINTS
    np.testing.assert_allclose(np.hypot(*(circle.outline - [10.0, 12.0]).T), 1.5)
    np.testing.assert_array_equal(circle.rings()[0], circle.outline)


def test_rows_that_are_not_discs_or_have_no_radius_are_left_out():
    nodes = _discs([(1, 0.0, 0.0, 10, 1.0), (2, 0.0, 0.0, 11, 0.0),
                    (3, 0.0, 0.0, 12, np.nan), (4, 0.0, 0.0, 13, 1.0)])
    nodes.loc[3, "TypeCode"] = 6
    assert [a.location_id for a in annotations_from_discs(nodes)] == [1]
    assert annotations_from_discs(nodes.iloc[:0]) == []


def test_a_vertical_disc_stack_is_a_cylinder():
    sections = range(100, 121)
    nodes = _discs([(z, 10.0, 12.0, z, 2.0) for z in sections])
    annotations = annotations_from_discs(nodes, _chain(list(sections)))
    cv = build_curve_volume(annotations, pixel_size_um=0.1, links=None, bridge_um=0.0)
    np.testing.assert_array_equal(cv.sections, np.arange(100, 121))
    mesh = curve_volume_to_mesh(cv)
    assert len(split_components(mesh)) == 1
    assert mesh.volume() == pytest.approx(np.pi * 4 * 21 * 0.09, rel=0.05)
    # No higher or lower than its first and last disc.
    assert mesh.bounds[0, 2] == pytest.approx(100 * 0.09, abs=0.09)
    assert mesh.bounds[1, 2] == pytest.approx(120 * 0.09, abs=0.09)


def test_a_disc_stack_that_travels_stays_one_body():
    """A process running obliquely moves 0.8 um a section, wider than its
    0.6 um discs: their circles never overlap, and the 3x3x3 smoothing
    removes what is one section thick (all but the two end circles, which
    the smoothing's edge padding doubles).  The links carry the run through."""
    sections = list(range(200, 216))
    nodes = _discs([(z, 0.8 * (z - 200), 5.0, z, 0.3) for z in sections])
    alone = build_curve_volume(annotations_from_discs(nodes), pixel_size_um=0.05,
                               links=None, bridge_um=0.0)
    assert len(split_components(curve_volume_to_mesh(alone))) == 2
    linked = annotations_from_discs(nodes, _chain(sections))
    mesh = curve_volume_to_mesh(build_curve_volume(linked, pixel_size_um=0.05,
                                                   links=None, bridge_um=0.0))
    assert len(split_components(mesh)) == 1
    assert mesh.bounds[0, 0] < 0.5 and mesh.bounds[1, 0] > 0.8 * 15 - 0.5


def test_a_link_across_skipped_sections_fills_them_and_stops_at_its_limit():
    nodes = _discs([(1, 0.0, 0.0, 100, 1.0), (2, 2.0, 0.0, 104, 1.0),
                    (3, 0.0, 9.0, 100, 1.0), (4, 0.0, 9.0, 160, 1.0)])
    annotations = annotations_from_discs(nodes, [(1, 2), (3, 4)])
    runs = [a for a in annotations if a.location_id is None]
    # 1-2 crosses sections 100..104; 3-4 spans 5.4 um, past LINK_BRIDGE_UM.
    assert sorted({a.z_section for a in runs}) == [100, 101, 102, 103, 104]
    assert all(a.registered for a in runs)
    middle = [a for a in runs if a.z_section == 102][0]
    # One section either side of 102: from x = 0.5 to 1.5, each 1 um wide.
    assert middle.outline[:, 0].min() == pytest.approx(-0.5, abs=0.01)
    assert middle.outline[:, 0].max() == pytest.approx(2.5, abs=0.01)


def test_a_link_within_a_section_adds_nothing():
    """Two discs joined in one section: the link says they are connected,
    not that the cell is that wide between them."""
    nodes = _discs([(1, 0.0, 0.0, 100, 0.5), (2, 3.0, 0.0, 100, 0.5)])
    assert len(annotations_from_discs(nodes, [(1, 2), (2, 1)])) == 2


class _Cell:
    """The parts of a neuron the closed-curve path reads: its traced outlines, its
    discs and links, and its volume's scale."""

    volume_scale = (7.5, 7.5, 90.0)

    def __init__(self, geometries, nodes, edges):
        self.geometries, self.nodes, self.edges = geometries, nodes, edges

    def get_cell_nodes(self):
        return self.nodes


def _mixed_cell(cone_neuron):
    """The cone fixture's three traced outlines (x 6 to 14, sections 1500 to 1502) with a
    dendrite of eleven discs leaving to the right (x 22 to 27), linked to its first outline."""
    discs = _discs([(5000 + k, 22.0 + 0.5 * k, 12.0, 1500 + k, 1.0) for k in range(11)])
    links = pd.DataFrame({"A": [5000 + k for k in range(10)] + [5000],
                          "B": [5001 + k for k in range(10)] + [90000]})
    traced_links = cone_neuron.edges[["A", "B"]]
    return _Cell(cone_neuron.geometries, discs, pd.concat([traced_links, links], ignore_index=True))


def test_closed_curve_draws_a_cells_discs_as_well_as_its_traced_outlines(cone_neuron):
    """Inferior c16137 had four outlines added to its 1,931 discs (2026-10-16), and Closed
    curve then drew only those four: a patch 14 um wide in place of the cell.  The discs
    stand in as circles whether or not the cell has traced outlines."""
    from sbfsem.service.facade import _curve_outlines

    cell = _mixed_cell(cone_neuron)
    traced = annotations_from_geometries(cell.geometries)
    both, how = _curve_outlines(cell, discs=True)
    assert [a.location_id for a in both[:3]] == [90000, 90001, 90002]      # the traced ones first
    assert len(both) > len(traced) + 11                                    # the circles and the runs
    assert {a.location_id for a in both if a.location_id} >= {5000 + k for k in range(11)}
    # The traced outlines keep the interpolation they had, and Viking's links between them and to a
    # disc; the links between discs are already drawn as runs.
    assert sorted(how["links"]) == [(5000, 90000), (90000, 90001), (90001, 90002)]
    assert "bridge_um" not in how
    # Hybrid's curve half is the outlines alone (its discs are the disc build's).
    only, hybrid_how = _curve_outlines(cell, discs=False)
    assert [a.location_id for a in only] == [90000, 90001, 90002]
    assert len(hybrid_how["links"]) == 13


def test_a_cell_with_only_discs_or_only_outlines_is_as_it_was(cone_neuron):
    from sbfsem.service.facade import _curve_outlines

    cell = _mixed_cell(cone_neuron)
    discs_only = _Cell(cone_neuron.geometries.iloc[:0], cell.nodes, cell.edges)
    annotations, how = _curve_outlines(discs_only, discs=True)
    assert how == {"links": None, "bridge_um": 0.0}                         # nothing is interpolated
    assert not any(a.location_id in (90000, 90001, 90002) for a in annotations)
    outlines_only = _Cell(cone_neuron.geometries, cell.nodes.iloc[:0], cell.edges)
    annotations, how = _curve_outlines(outlines_only, discs=True)
    assert [a.location_id for a in annotations] == [90000, 90001, 90002]
    assert len(how["links"]) == 13


def test_the_closed_curve_mesh_of_a_mixed_cell_reaches_its_dendrite(cone_neuron):
    from sbfsem.service.facade import SbfsemService

    service = SbfsemService(offline=True)
    cell = _mixed_cell(cone_neuron)
    mesh = service._curve_mesh(cell, 0.1)
    assert mesh.bounds[0, 0] < 7.0 and mesh.bounds[1, 0] > 26.0              # soma to dendrite tip
    assert len(split_components(mesh)) == 2                                  # the two, 8 um apart
    outlines_only = service._curve_mesh(_Cell(cell.geometries, cell.nodes.iloc[:0], cell.edges), 0.1)
    assert outlines_only.bounds[1, 0] < 15.0 and mesh.volume() > outlines_only.volume() * 1.05


def test_windowed_rasterization_matches_whole_section_masks():
    """Each outline is rasterized on its own window, not the whole section;
    the pixels are the same."""
    rng = np.random.default_rng(3)
    annotations = [
        ClosedCurveAnnotation(ring(r, cx, cy), [ring(r / 3, cx, cy)], z, z * 0.09)
        for r, cx, cy, z in zip(rng.uniform(0.2, 3, 40), rng.uniform(0, 30, 40),
                                rng.uniform(0, 30, 40), rng.integers(10, 14, 40), strict=True)
    ]
    cv = build_curve_volume(annotations, pixel_size_um=0.07, bridge_um=0.0)
    rows, cols, _ = cv.volume.shape
    xmin, ymin = cv.origin_um[:2]
    expected = np.zeros_like(cv.volume)
    for a in annotations:
        rings = [np.column_stack([(r[:, 0] - xmin) / 0.07, (r[:, 1] - ymin) / 0.07])
                 for r in a.rings(20)]
        k = int(np.flatnonzero(cv.sections == a.z_section)[0])
        expected[:, :, k] |= rasterize_rings(rings, (rows, cols))
    np.testing.assert_array_equal(cv.volume, expected)



# -- tiles and blocks: the same pixels and surface without the box -------------


def _box_raster(annotations, *, volume_scale=(7.5, 7.5, 90.0), pixel_size_um,
                bridge_um=closed_curve.BRIDGE_UM, links=None,
                link_bridge_um=closed_curve.LINK_BRIDGE_UM):
    """The raster as it was built until 2026-10-15: one array over the whole
    box, every outline on its whole section."""
    grid = _curve_grid(annotations, volume_scale=volume_scale, pixel_size_um=pixel_size_um,
                       margin_um=0.5, bridge_um=bridge_um, links=links,
                       link_bridge_um=link_bridge_um)
    shape = (grid.rows, grid.cols)
    volume = np.zeros((*shape, len(grid.sections)), dtype=bool)
    at = {int(z): i for i, z in enumerate(grid.sections)}

    def pixels(a):
        return [np.column_stack([(r[:, 0] - grid.xmin) / pixel_size_um,
                                 (r[:, 1] - grid.ymin) / pixel_size_um]) for r in a.rings(20)]

    for a in annotations:
        volume[:, :, at[int(a.z_section)]] |= rasterize_rings(pixels(a), shape)
    for lo, hi in grid.bridges:
        between = interpolate_masks(volume[:, :, at[lo]], volume[:, :, at[hi]],
                                    [(z - lo) / (hi - lo) for z in range(lo + 1, hi)])
        for z, mask in zip(range(lo + 1, hi), between, strict=True):
            volume[:, :, at[z]] = mask
    ends = []
    for below, above in grid.spans:
        lo, hi = int(below.z_section), int(above.z_section)
        mask_lo, mask_hi = rasterize_rings(pixels(below), shape), rasterize_rings(pixels(above), shape)
        between = interpolate_masks(mask_lo, mask_hi,
                                    [(z - lo) / (hi - lo) for z in range(lo + 1, hi)])
        if mask_lo.any() and mask_hi.any() and not all(m.any() for m in between):
            run = link_run_masks(mask_lo, mask_hi, hi - lo)
            between = run[1:-1]
            ends += [(at[lo], run[0]), (at[hi], run[-1])]
        for z, mask in zip(range(lo + 1, hi), between, strict=True):
            volume[:, :, at[z]] |= mask
    for k, mask in ends:
        volume[:, :, k] |= mask
    return volume


def _box_mesh(box, cv):
    """Marching cubes over each whole run of the box, as until 2026-10-15."""
    parts = []
    for start, stop in cv.runs():
        origin = np.array(cv.origin_um, dtype=float)
        origin[2] = float(cv.sections[start]) * float(cv.spacing_um[2])
        parts.append(volume_to_mesh(box[:, :, start:stop], spacing=cv.spacing_um, origin=origin))
    parts = [p for p in parts if not p.is_empty]
    return EMPTY_MESH if not parts else parts[0] if len(parts) == 1 else concatenate(parts)


def _sprawl():
    """A small cell with everything the raster does: branches traced on
    most sections, cut-outs, short skips (interpolated, 131-139 between two
    different shapes), a long one (two runs), a link across sections other
    branches were traced on, and a disc-traced process with links."""
    trunk = [ClosedCurveAnnotation(ring(2.0, 10, 12), [ring(0.6, 10.5, 12)], z, z * 0.07)
             for z in range(100, 131) if z % 6]
    twig = _branch([100, 101, 102, 103, 107, 108, 109, 110], cx=16.0, first_id=501, radius=0.9)
    late = [ClosedCurveAnnotation(ring(1.5, 30, 4), [], z, z * 0.07) for z in range(170, 176)]
    nodes = _discs([(900 + i, 4.0 + 0.45 * i, 25.0 - 0.3 * i, 140 + i, 0.35) for i in range(14)],
                   dz=0.07)
    discs = annotations_from_discs(nodes, _chain(list(nodes["ID"])),
                                   volume_scale=(2.176, 2.176, 70.0))
    links = [(twig[3].location_id, twig[4].location_id)]
    return [*trunk, *twig, *late, *discs], links


@pytest.mark.parametrize(("tile", "block"), [(32, 64), (8, 5), (5, 3)])
@pytest.mark.parametrize("pixel", [0.1, 0.06])
def test_tiles_and_blocks_give_the_box_raster_and_its_surface_exactly(
        monkeypatch, tile, block, pixel):
    """Only the tiles under some outline are allocated, and each run is
    contoured in blocks; the pixels, and the mesh's vertex and face arrays,
    are bit for bit the whole box's.  Small tiles and blocks put block
    seams through everything, faces flat on a cube's side included."""
    monkeypatch.setattr(closed_curve, "TILE", tile)
    monkeypatch.setattr(volume_module, "CONTOUR_BLOCK", block)
    annotations, links = _sprawl()
    scale = (2.176, 2.176, 70.0)
    cv = build_curve_volume(annotations, volume_scale=scale, pixel_size_um=pixel, links=links)
    box = _box_raster(annotations, volume_scale=scale, pixel_size_um=pixel, links=links)
    assert len(cv.runs()) == 2 and int((~cv.traced).sum()) > 3
    np.testing.assert_array_equal(cv.dense(), box)
    assert cv.allocated_voxels == curve_grid_voxels(annotations, volume_scale=scale,
                                                    pixel_size_um=pixel, links=links)
    assert cv.allocated_voxels < box.size
    mesh, expected = curve_volume_to_mesh(cv), _box_mesh(box, cv)
    assert len(mesh.faces) > 1000
    np.testing.assert_array_equal(mesh.vertices, expected.vertices)
    np.testing.assert_array_equal(mesh.faces, expected.faces)
    # contains() reads the tiles as the box.
    rng = np.random.default_rng(5)
    probe = cv.voxel_to_um(rng.uniform(0, 1, (4000, 3)) * (np.array(box.shape) - 1))
    rows = np.round((probe[:, 1] - cv.origin_um[1]) / pixel).astype(int)
    cols = np.round((probe[:, 0] - cv.origin_um[0]) / pixel).astype(int)
    section = np.round(probe[:, 2] / cv.spacing_um[2]).astype(int)
    k = np.minimum(np.searchsorted(cv.sections, section), len(cv.sections) - 1)
    on_slice = cv.sections[k] == section          # not in the gap between the runs
    assert 0 < int((~on_slice).sum()) < len(probe)
    np.testing.assert_array_equal(cv.contains(probe), box[rows, cols, k] & on_slice)


@pytest.mark.parametrize(("tile", "block"), [(32, 64), (8, 5), (5, 3)])
def test_a_run_onto_its_traced_sections_is_the_box_raster_exactly(monkeypatch, tile, block):
    """A linked pair drawn as a run reaches onto its two traced sections,
    past the tiles the plan gave them: those are allocated after the plan,
    and the pixels and the surface are still the whole box's."""
    monkeypatch.setattr(closed_curve, "TILE", tile)
    monkeypatch.setattr(volume_module, "CONTOUR_BLOCK", block)
    pair, links = _outline_to_disc(2.0, top=916)              # half the jump on each end
    trunk = [ClosedCurveAnnotation(ring(0.8, 6.0, 9.0), [], z, z * 0.09) for z in range(910, 940)]
    annotations, scale = [*pair, *trunk], (2.18, 2.18, 90.0)
    cv = build_curve_volume(annotations, volume_scale=scale, pixel_size_um=0.1, links=links)
    box = _box_raster(annotations, volume_scale=scale, pixel_size_um=0.1, links=links)
    np.testing.assert_array_equal(cv.dense(), box)
    planned = curve_grid_voxels(annotations, volume_scale=scale, pixel_size_um=0.1, links=links)
    assert cv.allocated_voxels > planned
    mesh, expected = curve_volume_to_mesh(cv), _box_mesh(box, cv)
    np.testing.assert_array_equal(mesh.vertices, expected.vertices)
    np.testing.assert_array_equal(mesh.faces, expected.faces)


def test_the_cone_fixture_is_the_box_raster_and_surface_exactly(cone_neuron):
    annotations = annotations_from_geometries(cone_neuron.geometries)
    scale = cone_neuron.volume_scale
    cv = build_curve_volume(annotations, volume_scale=scale)
    box = _box_raster(annotations, volume_scale=scale, pixel_size_um=0.05)
    np.testing.assert_array_equal(cv.dense(), box)
    mesh, expected = curve_volume_to_mesh(cv), _box_mesh(box, cv)
    np.testing.assert_array_equal(mesh.vertices, expected.vertices)
    np.testing.assert_array_equal(mesh.faces, expected.faces)
    # The dense path for volume_to_mesh's own options is the same surface too.
    np.testing.assert_array_equal(curve_volume_to_mesh(cv, level=0.5).faces, expected.faces)


def test_a_wide_structure_costs_its_outlines_not_its_box():
    """Inferior c16137 is thin processes spread over 128 x 176 um: its box
    at 0.1 um is a billion voxels.  Two small rings 150 um apart stand in."""
    far = [ClosedCurveAnnotation(ring(0.5, cx, cy), [], z, z * 0.09)
           for cx, cy in ((0.0, 0.0), (150.0, 150.0)) for z in (10, 11, 12)]
    cv = build_curve_volume(far, pixel_size_um=0.05, max_voxels=1_000_000)
    assert cv.shape[0] * cv.shape[1] * cv.shape[2] > 25_000_000
    assert cv.allocated_voxels <= 2 * 3 * 4 * closed_curve.TILE ** 2
    assert len(split_components(curve_volume_to_mesh(cv))) == 2


@pytest.mark.parametrize("seed", [32, 188, 498])
def test_faces_lying_on_a_cube_side_keep_the_whole_run_order(monkeypatch, seed):
    """Lewiner's tables sometimes draw a triangle flat on the side two cubes
    share (RPC1 c2628 at 0.1 um has some): its vertices do not say which
    cube drew it, so the order of the faces needs that cube's own marching
    cubes.  These random volumes have such faces."""
    calls = []
    resolve = volume_module._emitting_cube
    monkeypatch.setattr(volume_module, "_emitting_cube",
                        lambda *a, **k: calls.append(1) or resolve(*a, **k))
    monkeypatch.setattr(volume_module, "CONTOUR_BLOCK", 4)
    rng = np.random.default_rng(seed)
    occupied = rng.random((12, 12, 12)) < rng.uniform(0.35, 0.75)
    origin, spacing = np.array([3.0, 4.0, 100 * 0.07]), np.array([0.1, 0.1, 0.07])
    cv = closed_curve.CurveVolume.from_dense(occupied, origin, spacing, np.arange(100, 112),
                                             tile=5)
    np.testing.assert_array_equal(cv.dense(), occupied)
    mesh = curve_volume_to_mesh(cv)
    expected = volume_to_mesh(occupied, spacing=spacing, origin=origin)
    assert calls
    np.testing.assert_array_equal(mesh.vertices, expected.vertices)
    np.testing.assert_array_equal(mesh.faces, expected.faces)



@pytest.mark.parametrize("tile", [32, 5, 4])
def test_exposed_faces_count_the_boundary_between_inside_and_outside(tile):
    rng = np.random.default_rng(11)
    occupied = rng.random((20, 23, 12)) < 0.4
    occupied[:, 10:15, :] = False                                    # tiles left out
    sections = np.array([*range(50, 56), *range(60, 66)])            # two runs
    cv = closed_curve.CurveVolume.from_dense(occupied, np.zeros(3), np.array([0.1, 0.1, 0.07]),
                                             sections, tile=tile)
    padded = np.pad(occupied, 1)
    plane = (np.count_nonzero(padded[1:, :, 1:-1] != padded[:-1, :, 1:-1])
             + np.count_nonzero(padded[:, 1:, 1:-1] != padded[:, :-1, 1:-1]))
    across = sum(np.count_nonzero(np.diff(np.pad(occupied[:, :, a:b], ((0, 0), (0, 0), (1, 1))),
                                          axis=2))
                 for a, b in ((0, 6), (6, 12)))
    assert cv.exposed_faces() == (plane, across)


def test_a_cells_surface_may_have_ten_million_voxel_faces():
    """The limit was 3 million until 2026-10-16, when it was raised to 10 million
    at the user's request (RPC1 c2628 at its 0.025 um fine pixel is 6.8 million
    voxel faces, 10.1 million triangles, and was refused: its finest pixel was
    0.044 um).  A build that takes no limit of its own is held to this one."""
    import inspect

    assert closed_curve.MAX_SURFACE == 10_000_000
    default = inspect.signature(build_curve_volume).parameters["max_surface"].default
    assert default == closed_curve.MAX_SURFACE


def test_a_surface_too_big_for_memory_names_a_pixel_that_fits():
    """Tiles fit a wide cell at a fine pixel; its mesh might not fit in
    memory.  The raster's exposed faces say so before marching cubes, and
    the refusal reads like the voxel limit's."""
    stack = [ClosedCurveAnnotation(ring(4.0), [ring(1.0)], z, z * 0.09) for z in range(100, 130)]
    limit = sum(build_curve_volume(stack, pixel_size_um=0.05).exposed_faces()) // 3
    with pytest.raises(ValueError, match="voxel faces") as refusal:
        build_curve_volume(stack, pixel_size_um=0.05, max_surface=limit)
    finest = re.search(FINEST_PIXEL, str(refusal.value))
    assert finest and finest.group(2) == "voxel faces"
    assert int(finest.group(3).replace(",", "")) == limit
    fits = float(finest.group(4))
    assert 0.05 < fits < 0.2
    assert sum(build_curve_volume(stack, pixel_size_um=fits,
                                  max_surface=limit).exposed_faces()) <= limit


# -- the outlines' reply, kept by what decides it -------------------------------------------------
@pytest.fixture
def outline_service(request):
    from sbfsem.service.facade import SbfsemService

    neuron = read_legacy_json(request.path.parents[1] / "fixtures" / "i90000_closedcurve.json")
    neuron.data_stamp = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    svc = SbfsemService(offline=True)
    svc.add_neuron(neuron)
    yield svc, neuron
    svc.close()


def _shift_outlines(neuron, dx_um: float) -> None:
    frame = neuron.geometries.copy()
    frame["Curve"] = pd.Series([[np.asarray(c) + [dx_um, 0.0] for c in curves]
                                for curves in frame["Curve"]], index=frame.index, dtype=object)
    neuron.geometries = frame


def test_the_outline_reply_is_the_outlines_and_their_measures_as_json(outline_service):
    from sbfsem.service.dto import ClosedCurveDTO
    from sbfsem_web.json_response import SafeJSONResponse

    svc, neuron = outline_service
    dtos = svc.closed_curves(neuron.id, neuron.source)
    outlines = cone_outlines_from_neuron(neuron)
    assert len(dtos) == len(outlines) == 3
    for dto, outline in zip(dtos, outlines, strict=True):
        assert ClosedCurveDTO(**dto.model_dump()) == dto        # built as the fields' own types
        assert (dto.area_um2, dto.equivalent_diameter_um) == (outline.area_um2, outline.equivalent_diameter_um)
        assert dto.centroid_um == outline.centroid_um.tolist()
        assert dto.outline_um == outline.rings()[0].tolist()
    # The bytes Python's encoder made of the same models: what the route sent before.
    body = svc.closed_curves_json(neuron.id, neuron.source)
    assert body == SafeJSONResponse([d.model_dump(mode="json") for d in dtos]).body


def test_an_outline_reply_is_kept_until_the_annotations_or_their_positions_change(outline_service):
    svc, neuron = outline_service
    first = svc.closed_curves_json(neuron.id, neuron.source)
    assert svc.closed_curves_json(neuron.id, neuron.source) is first
    assert svc.closed_curves_json(neuron.id, neuron.source, spline_points=8) != first
    # Moved (a Registration setting): a positions token of its own, so made again.
    _shift_outlines(neuron, 1.0)
    neuron.live_registration = {"token": "moved"}
    moved = svc.closed_curves_json(neuron.id, neuron.source)
    x0 = json.loads(first)[0]["outline_um"][0][0]
    assert json.loads(moved)[0]["outline_um"][0][0] == pytest.approx(x0 + 1.0)
    # Back at the stored positions: the first reply, not made again.
    _shift_outlines(neuron, -1.0)
    neuron.live_registration = None
    assert svc.closed_curves_json(neuron.id, neuron.source) is first
    # Edited in Viking (a later stamp): made again.
    _shift_outlines(neuron, 2.0)
    neuron.data_stamp += timedelta(seconds=1)
    edited = svc.closed_curves_json(neuron.id, neuron.source)
    assert json.loads(edited)[0]["outline_um"][0][0] == pytest.approx(x0 + 2.0)


def test_an_unstamped_cells_outline_reply_is_made_each_time(outline_service):
    svc, neuron = outline_service
    neuron.data_stamp = None
    first = svc.closed_curves_json(neuron.id, neuron.source)
    _shift_outlines(neuron, 1.0)
    assert svc.closed_curves_json(neuron.id, neuron.source) != first
    assert svc.memory_report()["outline_reply_bytes"] == 0


def test_outline_replies_stay_within_their_budget_and_clear_with_the_renders(outline_service,
                                                                              monkeypatch):
    svc, neuron = outline_service
    one = len(svc.closed_curves_json(neuron.id, neuron.source))
    monkeypatch.setattr(svc, "OUTLINE_REPLY_CACHE_MB", 1.5 * one / 1e6)
    svc.closed_curves_json(neuron.id, neuron.source, spline_points=8)
    assert 0 < svc.memory_report()["outline_reply_bytes"] <= 1.5 * one
    svc.clear_renders()
    assert svc.memory_report()["outline_reply_bytes"] == 0
