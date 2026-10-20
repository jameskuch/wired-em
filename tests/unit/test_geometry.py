from __future__ import annotations

import numpy as np
import pytest

from sbfsem.geometry.closed_curve import ClosedCurveAnnotation, build_curve_volume
from sbfsem.geometry.cylinder import (
    curve_to_mesh,
    gencyl_mesh,
    get_ab,
    line3_mesh,
    normal_circle,
    segment_mesh,
)
from sbfsem.geometry.mesh import concatenate
from sbfsem.geometry.meshops import bounding_box_from_points, clip_mesh, group_bounding_box
from sbfsem.geometry.primitives import cuboid, cylinder, sphere, synapse_spheres
from sbfsem.geometry.segmentation import dendrite_segmentation
from sbfsem.geometry.spline import catmull_rom_spline, close_spline
from sbfsem.geometry.volume import volume_to_mesh

STRAIGHT = np.array([[0.0, 0, 0], [0, 0, 1], [0, 0, 2], [0, 0, 3]])
UNIT_R = np.ones(4)


def test_get_ab_is_orthonormal():
    for normal in np.eye(3).tolist() + [[1, 1, 1], [0.3, -0.5, 0.8]]:
        n = np.asarray(normal, dtype=float)
        n = n / np.linalg.norm(n)
        a, b = get_ab(n)
        assert np.isclose(np.linalg.norm(a), 1.0)
        assert np.isclose(np.linalg.norm(b), 1.0)
        assert abs(a @ b) < 1e-9
        assert abs(a @ n) < 1e-9


def test_normal_circle_lies_in_the_plane():
    n = np.array([0.0, 0.0, 1.0])
    a, b = get_ab(n)
    circle = normal_circle(np.arange(0, 360, 36), 0.0, a, b)
    assert circle.shape == (10, 3)
    assert np.allclose(np.linalg.norm(circle, axis=1), 1.0)
    assert np.allclose(circle @ n, 0.0, atol=1e-9)


def test_line3_makes_a_closed_tube():
    mesh, fallback = line3_mesh(STRAIGHT, UNIT_R, n_circle=40)
    assert not fallback
    assert mesh.n_vertices == 40 * (9 + 3 * 3 + 9)
    tube = mesh.fix_winding()
    # A unit-radius tube 3 um long, with caps reaching bufferDist beyond each end.
    assert 9.5 < tube.volume() < 12.0
    assert np.allclose(tube.bounds[:, 0], [-1, 1], atol=1e-6)


def test_line3_handles_coincident_annotations():
    # F081: MATLAB divided by zero here and emitted an all-NaN segment.
    points = np.array([[0.0, 0, 0], [0, 0, 0], [0, 0, 1], [0, 0, 2]])
    mesh = segment_mesh(points, np.ones(4))
    assert not mesh.is_empty
    assert np.isfinite(mesh.vertices).all()


def test_segment_mesh_falls_back_for_two_point_segments():
    mesh = segment_mesh(STRAIGHT[:2], UNIT_R[:2])
    assert not mesh.is_empty


def test_gencyl_matches_the_expected_extent():
    mesh = gencyl_mesh(STRAIGHT, UNIT_R, n_around=60)
    assert np.allclose(mesh.bounds[:, 2], [0, 3], atol=1e-6)
    assert np.allclose(mesh.bounds[:, 0], [-1, 1], atol=1e-3)
    # An open tube: compare lateral surface area, 2 * pi * r * L.
    assert mesh.surface_area() == pytest.approx(2 * np.pi * 3, rel=0.02)


def test_curve_to_mesh_shape():
    mesh = curve_to_mesh(STRAIGHT, UNIT_R, 16)
    assert mesh.n_vertices == 4 * 16
    assert np.allclose(mesh.bounds[:, 2], [0, 3], atol=1e-6)


def test_mesh_ops():
    box = cuboid((2, 2, 2))
    assert box.volume() == pytest.approx(8.0)
    assert box.surface_area() == pytest.approx(24.0)
    assert concatenate([box, box.translate((5, 0, 0))]).n_vertices == 16
    assert concatenate([]).is_empty
    assert concatenate([box]).n_vertices == 8  # F080: MATLAB asserted on one input
    half = clip_mesh(box, 0.0)
    assert len(half) < len(box)
    assert (half.vertices[:, 2] >= 0).all()


def test_primitive_sizes():
    s = sphere(1.0, divisions=40)
    assert s.volume() == pytest.approx(4 / 3 * np.pi, rel=0.02)
    assert s.surface_area() == pytest.approx(4 * np.pi, rel=0.02)
    c = cylinder(1.0, 2.0, divisions=64)
    assert c.volume() == pytest.approx(2 * np.pi, rel=0.01)
    assert synapse_spheres(np.zeros((0, 3))).is_empty
    assert synapse_spheres(np.array([[0.0, 0, 0], [5, 0, 0]]), 0.5).n_vertices > 0


def test_spline_closes_the_loop():
    square = np.array([[0.0, 0], [10, 0], [10, 10], [0, 10]])
    curve = close_spline(square, 20)
    assert len(curve) == 4 * 21
    assert np.allclose(curve[0], curve[-1], atol=1e-9)
    with pytest.raises(ValueError):
        catmull_rom_spline([0, 1, 2], [0, 1, 2])


def test_closed_curve_volume_reconstructs_a_cylinder():
    theta = np.linspace(0, 2 * np.pi, 25)[:-1]
    ring = np.column_stack([3 * np.cos(theta) + 20, 3 * np.sin(theta) + 20])
    anns = [
        ClosedCurveAnnotation(ring, z_section=1500 + i, z_um=(1500 + i) * 0.09)
        for i in range(10)
    ]
    cv = build_curve_volume(anns, volume_scale=(7.5, 7.5, 90.0))
    mesh = volume_to_mesh(cv.volume, spacing=cv.spacing_um, origin=cv.origin_um)
    # Ten occupied sections, each 0.09 um thick.
    expected = np.pi * 9 * (10 * 0.09)
    # No 100x magnification left in (F096): the mesh is in microns.
    assert mesh.volume() == pytest.approx(expected, rel=0.05)
    assert mesh.bounds[0, 0] == pytest.approx(17.0, abs=0.2)


def test_closed_curve_holes():
    theta = np.linspace(0, 2 * np.pi, 25)[:-1]
    outer = np.column_stack([5 * np.cos(theta) + 20, 5 * np.sin(theta) + 20])
    inner = np.column_stack([2 * np.cos(theta) + 20, 2 * np.sin(theta) + 20])
    ann = ClosedCurveAnnotation(outer, cutouts=[inner], z_section=1, z_um=0.09)
    cv = build_curve_volume([ann], pixel_size_um=0.05)
    filled = cv.volume.sum()
    area = filled * 0.05 * 0.05
    assert area == pytest.approx(np.pi * (25 - 4), rel=0.02)


def test_segmentation_covers_every_node(c943):
    result = dendrite_segmentation(c943)
    covered = np.unique(np.concatenate(result.segments))
    assert covered.size == c943.graph().number_of_nodes()
    assert result.components == []
    assert result.linking_nodes.size == len(result)
    assert result.terminal_nodes.size == len(result)


def test_segmentation_reports_disconnected_components():
    """F088/F118: MATLAB never asked dfsearch for startnode events, so the
    'these annotations are not actually connected' warning could never fire."""
    import pandas as pd

    from sbfsem.core.structure import Structure

    nodes = pd.DataFrame(
        {
            "ID": [1, 2, 3, 4],
            "ParentID": [7, 7, 7, 7],
            "VolumeX": [0.0, 1, 2, 50],
            "VolumeY": [0.0, 0, 0, 50],
            "Z": [1, 2, 3, 4],
            "Radius": [1.0, 1, 1, 1],
            "X": [0.0, 0, 0, 0],
            "Y": [0.0, 0, 0, 0],
            "OffEdge": [False] * 4,
            "Terminal": [False] * 4,
            "TypeCode": [1] * 4,
        }
    )
    edges = pd.DataFrame({"ID": [7, 7], "A": [1, 2], "B": [2, 3]})
    s = Structure(7, "i", nodes=nodes, edges=edges)
    result = dendrite_segmentation(s, start_node=1)
    assert result.components == [4]


def test_segment_never_spans_a_branch_point(c943):
    """F119: the MATLAB closure appended every remaining neighbor."""
    g = c943.graph()
    result = dendrite_segmentation(c943)
    for ids in result.segments:
        for a, b in zip(ids[:-1], ids[1:], strict=True):
            assert g.has_edge(int(a), int(b)), f"{a}-{b} is not an edge"


@pytest.mark.parametrize("cell", ["c943", "c1178"])
def test_segment_table_is_the_label_lookup_to_the_bit(cell, request):
    """The NumPy lookup (2.17.9) gives every segment the arrays pandas ``.loc``
    gave, values, dtypes and layout alike, so nothing summed from them moves
    (the tube builds read this table; ground rule 12)."""
    from sbfsem.geometry.segmentation import _segment_table, _segment_table_loc

    neuron = request.getfixturevalue(cell)
    segments = dendrite_segmentation(neuron).segments
    fast, slow = _segment_table(neuron, segments), _segment_table_loc(neuron, segments)
    assert len(fast) == len(slow) == len(segments) > 10
    for a, b in zip(fast.itertuples(), slow.itertuples(), strict=True):
        for column in ("ID", "Z", "XYZum", "Rum"):
            x, y = getattr(a, column), getattr(b, column)
            assert x.dtype == y.dtype and x.shape == y.shape, column
            assert x.tobytes() == y.tobytes(), column
            assert x.flags["F_CONTIGUOUS"] == y.flags["F_CONTIGUOUS"], column
            assert x.flags["C_CONTIGUOUS"] == y.flags["C_CONTIGUOUS"], column


def test_segment_table_names_a_missing_location_and_keeps_repeated_ids(c943):
    import copy

    from sbfsem.geometry.segmentation import _segment_table

    ids = c943.nodes["ID"].to_numpy(dtype=np.int64)
    with pytest.raises(KeyError, match="123456789"):
        _segment_table(c943, [np.array([ids[0], 123456789, ids[1]], dtype=np.int64)])
    # A repeated ID is looked up by label, as before: every row it is on.
    doubled = copy.copy(c943)
    doubled.nodes = c943.nodes.iloc[[0, *range(len(c943.nodes))]].reset_index(drop=True)
    table = _segment_table(doubled, [np.array([ids[0], ids[1]], dtype=np.int64)])
    assert len(table.XYZum.iloc[0]) == 3


def test_bounding_boxes():
    pts = np.array([[0.0, 0, 0], [3.2, 4.7, 1]])
    np.testing.assert_array_equal(bounding_box_from_points(pts), [0, 4, 0, 5])
    np.testing.assert_array_equal(
        group_bounding_box([[0, 1, 0, 1], [-1, 0.5, 2, 3]]), [-1, 1, 0, 3]
    )


def test_adaptive_ring_points_scale_with_radius():
    """A soma and a thin dendrite should not get the same vertex budget."""
    from sbfsem.geometry.cylinder import (
        CIRCLE_POINTS,
        MAX_RING_POINTS,
        MIN_RING_POINTS,
        adaptive_ring_points,
    )

    soma = adaptive_ring_points([10.9])
    dendrite = adaptive_ring_points([0.18])
    assert soma > CIRCLE_POINTS > dendrite
    assert soma <= MAX_RING_POINTS
    assert dendrite >= MIN_RING_POINTS
    # Monotonic in radius, and clamped at both ends.
    counts = [adaptive_ring_points([r]) for r in (0.05, 0.5, 2.0, 8.0, 40.0)]
    assert counts == sorted(counts)
    assert adaptive_ring_points([1e6]) == MAX_RING_POINTS
    # Degenerate input falls back rather than raising.
    assert adaptive_ring_points([0.0]) == CIRCLE_POINTS
    assert adaptive_ring_points([float("nan")]) == CIRCLE_POINTS


def test_render_pipeline_order_changes_the_result(c943):
    """Smoothing before decimating is not the same as after."""
    from sbfsem.geometry.cylinder import cylinder_render

    a = cylinder_render(c943, smooth_mode="taubin", smooth_iterations=2,
                        reduce_fraction=0.3, post_order="smooth_first")
    b = cylinder_render(c943, smooth_mode="taubin", smooth_iterations=2,
                        reduce_fraction=0.3, post_order="decimate_first")
    assert a.n_vertices > 0 and b.n_vertices > 0
    assert not np.allclose(a.volume(), b.volume(), rtol=1e-6)


def test_render_pipeline_rejects_unknown_options(c943):
    from sbfsem.geometry.cylinder import cylinder_render

    with pytest.raises(ValueError):
        cylinder_render(c943, smooth_mode="bogus")
    with pytest.raises(ValueError):
        cylinder_render(c943, post_order="sideways")


def test_taubin_applies_to_the_tube_method_too(c943):
    """MATLAB skipped smoothing for curveToMesh; Taubin is safe there."""
    from sbfsem.geometry.cylinder import cylinder_render

    plain = cylinder_render(c943, method=2, smooth_mode="none")
    legacy = cylinder_render(c943, method=2, smooth_mode="laplacian", smooth_iterations=5)
    taubin = cylinder_render(c943, method=2, smooth_mode="taubin", smooth_iterations=5)
    # Legacy is a no-op on method 2, as MATLAB had it.
    np.testing.assert_allclose(plain.vertices, legacy.vertices)
    assert not np.allclose(plain.vertices, taubin.vertices)


def test_voxel_remesh_capsule_for_true_tubes():
    """Two annotations far apart relative to their radii stay a round capsule."""
    from sbfsem.geometry.remesh import voxel_remesh

    cyl = voxel_remesh(np.array([[0, 0, 0.0], [0, 0, 10.0]]),
                       np.array([2.0, 2.0]), voxels_per_radius=8,
                       surface_smooth_um=0)
    exact = np.pi * 4 * 10 + 4 / 3 * np.pi * 8          # cylinder + two caps
    assert cyl.volume() == pytest.approx(exact, rel=0.02)


def test_voxel_remesh_stacked_discs_are_lofted_not_ballooned():
    """A Viking annotation is a disc in its section plane, not a sphere.

    Stacked discs must produce a flat-capped body that ends where the
    annotations end.  The capsule reading turned every disc into a ball
    bulging a full radius in +-Z -- the "amoeba" somas -- and read this
    cylinder as 159.2 um3 instead of 125.7.
    """
    from sbfsem.geometry.remesh import voxel_remesh

    z = np.linspace(0, 10, 21)
    pts = np.column_stack([np.zeros(21), np.zeros(21), z])
    m = voxel_remesh(pts, np.full(21, 2.0), voxels_per_radius=8, surface_smooth_um=0)
    assert m.volume() == pytest.approx(np.pi * 4 * 10, rel=0.02)
    # The body ends at the annotated sections, to within a voxel.
    assert m.vertices[:, 2].min() > -0.3
    assert m.vertices[:, 2].max() < 10.3


def test_voxel_remesh_recovers_a_sphere_from_its_discs():
    """A soma annotated section-by-section must come back as the soma."""
    from sbfsem.geometry.remesh import voxel_remesh

    z = np.arange(-4.8, 4.81, 0.2)
    r = np.sqrt(np.maximum(25.0 - z * z, 1e-6))
    pts = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    m = voxel_remesh(pts, r, voxels_per_radius=8, surface_smooth_um=0)
    assert m.volume() == pytest.approx(4 / 3 * np.pi * 125, rel=0.03)


def test_voxel_remesh_wandering_stack_does_not_bubble():
    """Laterally wandering soma outlines must not sprout sphere lobes."""
    from sbfsem.geometry.remesh import voxel_remesh

    z = np.arange(0, 5.01, 0.25)
    pts = np.column_stack([1.2 * np.sin(np.arange(len(z))),
                           np.zeros_like(z), z])
    m = voxel_remesh(pts, np.full(len(z), 8.0), voxels_per_radius=6, surface_smooth_um=0)
    zs = m.vertices[:, 2]
    # The capsule reading bulged 8 um past each end; a voxel of slack is fine.
    assert zs.min() > -1.5 and zs.max() < 6.5


def test_voxel_remesh_same_section_branch_jump_links_thin():
    """A row that jumps laterally between two same-section fat discs is a
    branch link, not a tube.  Sweeping it at its own girth drew a horizontal
    radius-thick sausage through unannotated tissue: on c4115 its underside
    was the "detached knob" 2.7 um below the lowest annotation, and c1178
    carried a 22 um long, 10.5 um thick one through its soma.  The link is
    kept -- the bodies do connect -- but at attachment scale.
    """
    from sbfsem.geometry.remesh import LINK_RADIUS_UM, voxel_remesh

    # Two stacks of r=2 discs, 10 um apart in x, linked mid-row by one
    # same-z lateral jump (the segmentation's branch walk).
    z = np.arange(0, 2.01, 0.25)
    left = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    right = np.column_stack([np.full_like(z, 10.0), np.zeros_like(z), z[::-1]])
    pts = np.vstack([left, right])
    rad = np.full(len(pts), 2.0)
    m = voxel_remesh(pts, rad, voxels_per_radius=8, surface_smooth_um=0)
    assert not m.is_empty
    # Both stacks are present...
    xs = m.vertices[:, 0]
    assert (xs < 3).any() and (xs > 7).any()
    # ...and the gap carries only the thin connecting link, not a 2 um tube.
    between = m.vertices[(xs > 4.0) & (xs < 6.0)]
    assert len(between) > 0, "the bodies must stay visibly connected"
    off_axis = np.hypot(between[:, 1], between[:, 2] - 2.0)
    assert off_axis.max() < LINK_RADIUS_UM + 0.5, (
        f"link bulges to {off_axis.max():.2f} um off-axis")
    # And the bodies still end at their annotated sections (the link's own
    # small radius is the only thing allowed past the top disc's plane).
    assert m.vertices[:, 2].min() > -0.5
    assert m.vertices[:, 2].max() < 2.0 + LINK_RADIUS_UM + 0.5


def test_voxel_remesh_steep_fat_pair_still_sweeps_a_capsule():
    """A thick trunk actually descending -- z-step past the stack gate --
    must keep its round-cone field, or the trunk detaches from the soma."""
    from sbfsem.geometry.remesh import voxel_remesh

    pts = np.array([[0, 0, 0.0], [0, 0, 6.0]])
    rad = np.array([2.0, 2.0])
    m = voxel_remesh(pts, rad, voxels_per_radius=8, surface_smooth_um=0)
    assert not m.is_empty
    # One connected sweep spanning the gap, not two lenses.
    mid = m.vertices[(m.vertices[:, 2] > 2.5) & (m.vertices[:, 2] < 3.5)]
    assert len(mid) > 0


def test_split_fat_jumps_cuts_only_fat_lateral_jumps():
    from sbfsem.geometry.cylinder import _split_fat_jumps

    z = np.arange(0, 1.01, 0.25)
    left = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    right = np.column_stack([np.full_like(z, 10.0), np.zeros_like(z), z[::-1]])
    pts = np.vstack([left, right])

    parts, links = _split_fat_jumps(pts, np.full(len(pts), 2.0), 1.5)
    assert len(parts) == 2 and len(links) == 1
    a, b, r = links[0]
    assert r <= 0.75 and abs(float(a[0]) - float(b[0])) > 9

    # A thin pair jumping far in-section is a link too (c33's 50 um sweep);
    # the wire keeps the discs' own thin radius.
    parts_thin, links_thin = _split_fat_jumps(pts, np.full(len(pts), 0.3), 1.5)
    assert len(parts_thin) == 2 and len(links_thin) == 1
    assert links_thin[0][2] <= 0.3

    # Short thin hops stay swept: dendrites genuinely run in-plane.
    hop = np.array([[0, 0, 0.0], [0.8, 0, 0.0], [1.6, 0, 0.0]])
    parts_hop, links_hop = _split_fat_jumps(hop, np.full(3, 0.3), 1.5)
    assert len(parts_hop) == 1 and not links_hop

    # A steep fat pair is a real descending trunk and stays one sweep.
    parts_steep, links_steep = _split_fat_jumps(
        np.array([[0, 0, 0.0], [0, 0, 6.0]]), np.array([2.0, 2.0]), 1.5)
    assert len(parts_steep) == 1 and not links_steep


def test_split_fat_jumps_drops_wires_beyond_tie_range_and_keeps_strands():
    """c33 (NeitzCPED) regression: one thin annotation linked 50 um across
    its own section made the sweep draw two 50 um tubes through unannotated
    tissue -- 14 % of the cell's surface at up to 20 um from any disc.  The
    jump must be cut, no connector drawn at that distance (MAX_TIE_UM is the
    honesty bound the tie pass already obeys), and the stranded annotation
    must still render as a coin where it was annotated."""
    from sbfsem.geometry.cylinder import (
        MAX_TIE_UM,
        STRAND_COIN_HALF_UM,
        _split_fat_jumps,
    )

    # out-and-back: ... -> A(19,37,37.4) -> remote(70,37,37.4) -> B(19,37,37.4) ...
    pts = np.array([
        [18.0, 37.0, 37.4], [19.0, 37.0, 37.4],
        [70.0, 37.0, 37.4],
        [19.0, 36.9, 37.4], [18.0, 36.9, 37.4],
    ])
    rad = np.full(len(pts), 0.24)
    parts, links = _split_fat_jumps(pts, rad, 1.5)
    assert not links, "a 50 um wire through unannotated tissue is dishonest"
    assert len(parts) == 3
    coin = next(p for p, _ in parts if len(p) == 2
                and abs(float(p[0, 0]) - 70.0) < 1e-9)
    # The strand renders as its disc extruded a coin height, not dropped.
    assert np.allclose(coin[:, 2], [37.4 - STRAND_COIN_HALF_UM,
                                    37.4 + STRAND_COIN_HALF_UM])

    # A cut jump within tie range still gets its connector.
    near = np.array([[0, 0, 0.0], [10.0, 0, 0.0], [10.5, 0, 0.0]])
    _parts_near, links_near = _split_fat_jumps(near, np.full(3, 0.3), 1.5)
    assert len(links_near) == 1
    assert float(np.linalg.norm(near[1] - near[0])) <= MAX_TIE_UM


def test_adaptive_pathway_drops_fat_jump_girth():
    import types

    import pandas as pd

    from sbfsem.geometry.cylinder import cylinder_render

    z = np.arange(0, 2.01, 0.25)
    left = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    right = np.column_stack([np.full_like(z, 10.0), np.zeros_like(z), z[::-1]])
    pts = np.vstack([left, right])
    rad = np.full(len(pts), 2.0)
    seg = types.SimpleNamespace(table=pd.DataFrame({"XYZum": [pts], "Rum": [rad]}))
    neuron = types.SimpleNamespace(id=1)

    m = cylinder_render(neuron, pathway="adaptive_taubin", segmentation=seg)
    # A 2-point tube keeps its vertices near the ends, so sample the faces.
    samples = np.vstack([m.vertices, m.vertices[m.faces].mean(axis=1)])
    between = samples[(samples[:, 0] > 2.5) & (samples[:, 0] < 7.5)]
    assert len(between) > 0, "the link must keep the bodies connected"
    off = np.hypot(between[:, 1], between[:, 2] - 2.0)
    assert off.max() < 1.5, f"jump swept at girth: {off.max():.2f} um off-axis"

    # ``original`` stays byte-faithful to MATLAB and still sweeps it.
    mo = cylinder_render(neuron, pathway="original", segmentation=seg)
    so = np.vstack([mo.vertices, mo.vertices[mo.faces].mean(axis=1)])
    between_o = so[(so[:, 0] > 2.5) & (so[:, 0] < 7.5)]
    assert np.hypot(between_o[:, 1], between_o[:, 2] - 2.0).max() > 1.2


def test_voxel_pathway_rescues_sub_threshold_stacks():
    """A wandering stack of 1.2 um discs sits under the remesh threshold; a
    tube sweep tilts its rings toward vertical on the lateral steps and pokes
    a radius past the annotated sections (the crumpled 'bubble').  The rescue
    lofts it instead, so the body ends where the annotations end."""
    import types

    import pandas as pd

    from sbfsem.geometry.cylinder import cylinder_render

    z = np.arange(0, 4.01, 0.1)
    pts = np.column_stack([
        0.9 * np.sin(np.arange(len(z)) * 0.7), np.zeros_like(z), z,
    ])
    rad = np.full(len(pts), 1.2)
    seg = types.SimpleNamespace(table=pd.DataFrame({"XYZum": [pts], "Rum": [rad]}))
    neuron = types.SimpleNamespace(id=1)

    m = cylinder_render(neuron, pathway="voxel", segmentation=seg)
    assert not m.is_empty
    assert m.vertices[:, 2].min() > -0.6
    assert m.vertices[:, 2].max() < 4.6


def test_wrap_drops_fat_jump_girth_and_stays_disc_faithful():
    from sbfsem.geometry.wrap import cube_wrap_cell

    z = np.arange(0, 2.01, 0.25)
    left = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    right = np.column_stack([np.full_like(z, 10.0), np.zeros_like(z), z[::-1]])
    pts = np.vstack([left, right])
    rad = np.full(len(pts), 2.0)

    meshes, _covered = cube_wrap_cell([(pts, rad)])
    assert meshes
    # A 2-point link tube keeps its vertices near the ends: sample faces too.
    samples = np.vstack([np.vstack([m.vertices, m.vertices[m.faces].mean(axis=1)])
                         for m in meshes])
    between = samples[(samples[:, 0] > 2.5) & (samples[:, 0] < 7.5)]
    assert len(between) > 0, "the link must keep the bodies connected"
    off = np.hypot(between[:, 1], between[:, 2] - 2.0)
    assert off.max() < 1.5, f"jump swept at girth: {off.max():.2f} um off-axis"
    # Nothing a full radius below the annotated sections.
    assert samples[:, 2].min() > -1.2


def test_voxel_remesh_matches_the_lofted_frusta():
    """Stacked discs of varying radius integrate to the frustum volume."""
    from sbfsem.geometry.remesh import voxel_remesh

    pts = np.array([[0, 0, -3.0], [0, 0, 0.0], [0, 0, 3.0]])
    rad = np.array([6.0, 10.9, 6.0])
    frusta = sum(np.pi * 3 / 3 * (r1 * r1 + r1 * r2 + r2 * r2)
                 for r1, r2 in ((6.0, 10.9), (10.9, 6.0)))
    # At the default 3 voxels per radius this shape's voxel is 2 um and the
    # discretization alone is ~9%; the assertion is about the field, so pin a
    # finer grid.
    m = voxel_remesh(pts, rad, voxels_per_radius=8, surface_smooth_um=0)
    assert m.volume() == pytest.approx(frusta, rel=0.03)


def test_voxel_remesh_declines_degenerate_input():
    """Returning empty lets the caller fall back to the tube builders."""
    from sbfsem.geometry.remesh import voxel_remesh

    assert voxel_remesh(np.zeros((1, 3)), np.array([1.0])).is_empty
    assert voxel_remesh(np.zeros((2, 3)), np.array([0.0, 0.0])).is_empty
    assert voxel_remesh(np.full((2, 3), np.nan), np.array([1.0, 1.0])).is_empty
    # A segment that is nothing but a sub-voxel stack of huge discs cannot be
    # resolved by its own grid; empty is correct, and the caller falls back.
    thin = voxel_remesh(np.array([[0, 0, 5.0], [0, 0, 5.09], [0, 0, 5.18]]),
                        np.array([8.0, 8.0, 8.0]))
    # Either a thin coin (if a grid slice lands inside) or empty -- never a
    # ball: nothing may extend a radius past the annotated sections.
    if not thin.is_empty:
        assert thin.vertices[:, 2].min() > 4.0
        assert thin.vertices[:, 2].max() < 6.2


def test_pathways_are_distinct_and_original_is_the_default(c943):
    from sbfsem.geometry.cylinder import PATHWAYS, cylinder_render

    assert set(PATHWAYS) == {"original", "adaptive_taubin", "voxel", "wrap", "voxel2",
                             "convolution", "ultraliser"}
    default = cylinder_render(c943)
    original = cylinder_render(c943, pathway="original")
    np.testing.assert_allclose(default.vertices, original.vertices)

    adaptive = cylinder_render(c943, pathway="adaptive_taubin")
    assert adaptive.n_vertices != original.n_vertices

    with pytest.raises(ValueError):
        cylinder_render(c943, pathway="nonsense")


def test_voxel_remesh_contained_ball_pair_is_not_carved():
    """Adversarial-review regression: |ra - rb| > |b - a| broke the cone SDF.

    The exact round-cone formula is invalid when one endpoint ball contains
    the other; its branch predicates invert and 29% of a soma ball was
    reported outside ("craters").  Such pairs are now lofted (disc semantics)
    or, on the guarded tube path, fall back to the larger ball.
    """
    from sbfsem.geometry.remesh import _tube_distance, voxel_remesh

    # The function-level guard: hull of contained balls is the larger ball.
    rng = np.random.default_rng(1)
    grid = rng.uniform(-6, 6, (2000, 3))
    d = _tube_distance(grid, np.array([0, 0, 0.0]), np.array([0, 0, 2.0]), 5.0, 1.0)
    true = np.linalg.norm(grid, axis=1) - 5.0
    assert np.abs(d - true).max() < 1e-9

    # End to end, the pair lofts to the annotated frustum, bounded in z.
    # The grid is sized from the fat radii alone (5 um here), so a finer
    # voxels_per_radius stands in for what min-radius sizing used to give.
    m = voxel_remesh(np.array([[0, 0, 0.0], [0, 0, 2.0]]),
                     np.array([5.0, 1.0]), voxels_per_radius=40,
                     surface_smooth_um=0)
    assert m.volume() == pytest.approx(np.pi / 3 * 2 * (25 + 5 + 1), rel=0.03)
    assert m.vertices[:, 2].min() > -0.3 and m.vertices[:, 2].max() < 2.3


def test_voxel_remesh_same_z_duplicate_does_not_balloon():
    """Adversarial-review regression: a duplicated annotation re-inflated 4.5x."""
    from sbfsem.geometry.remesh import voxel_remesh

    z = np.arange(0, 2.971, 0.09)
    pts = np.column_stack([0.05 * np.arange(len(z)), np.zeros_like(z), z])
    base = voxel_remesh(pts, np.full(len(z), 8.0)).volume()
    dup = np.insert(pts, 17, pts[16], axis=0)
    m = voxel_remesh(dup, np.full(len(dup), 8.0))
    assert m.volume() == pytest.approx(base, rel=0.05)


def test_voxel_remesh_does_not_bridge_dropped_annotations():
    """Adversarial-review regression: NaN rows were filtered, then the
    survivors paired up across the gap and a tube was fabricated."""
    from sbfsem.geometry.remesh import voxel_remesh

    pts = np.array([[0, 0, 0.0], [0, 0, 3.0], [np.nan] * 3,
                    [0, 0, 30.0], [0, 0, 33.0]])
    m = voxel_remesh(pts, np.array([2.0] * 5))
    in_gap = (m.vertices[:, 2] > 8) & (m.vertices[:, 2] < 25)
    assert in_gap.sum() == 0


def test_voxel_remesh_declines_unresolvable_spans():
    """Adversarial-review regression: over-coarsening shattered a long thin
    segment into disconnected beads; declining lets the tubes take it."""
    from sbfsem.geometry.remesh import voxel_remesh

    t = np.linspace(0, 1, 60)
    pts = np.column_stack([1400 * t, 1400 * t, 100 * t])
    assert voxel_remesh(pts, np.full(60, 1.6)).is_empty


def test_voxel_remesh_surface_smoothing_irons_annotation_jitter():
    """The blur removes section-to-section ribbing at soma scale, cheaply.

    This is the control the user reads as "smooth the outside body": the
    literal disc stack is the annotators' data, and the smoothed envelope is
    the tissue.  Volume must survive the smoothing at soma radii.
    """
    from sbfsem.geometry.remesh import voxel_remesh

    rng = np.random.default_rng(7)
    z = np.arange(0, 12.01, 0.25)
    r = 8 + rng.normal(0, 0.35, len(z))
    cx = np.cumsum(rng.normal(0, 0.25, len(z)))
    pts = np.column_stack([cx, np.zeros_like(z), z])

    def facet(m):
        import scipy.sparse as sp
        V, F = m.vertices, m.faces
        a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
        n = np.cross(b - a, c - a)
        length = np.linalg.norm(n, axis=1, keepdims=True)
        length[length == 0] = 1
        n = n / length
        idx = np.arange(len(F))
        rows = np.concatenate([F[:, 0], F[:, 1], F[:, 2]])
        cols = np.concatenate([idx, idx, idx])
        m2 = sp.coo_matrix((np.ones(len(rows)), (rows, cols)),
                           shape=(len(V), len(F))).tocsr()
        adj = (m2.T @ m2).tocoo()
        pair = (adj.row < adj.col) & (adj.data >= 2)
        cos = np.clip((n[adj.row[pair]] * n[adj.col[pair]]).sum(1), -1, 1)
        return float(np.degrees(np.arccos(cos)).mean())

    # The grid is sized from the fat radii (~8 um); ask for voxels fine
    # enough that the 0.25 um section ribbing exists to be ironed at all.
    literal = voxel_remesh(pts, r, voxels_per_radius=64, surface_smooth_um=0)
    smoothed = voxel_remesh(pts, r, voxels_per_radius=64, surface_smooth_um=0.5)
    assert facet(smoothed) < facet(literal) * 0.5
    # The default sigma scales with the body (an 8 um soma gets ~0.9 um), so
    # a jittered stack gives up a bit under a tenth of its volume for the
    # ironing -- the shape survives, the ribs do not.
    assert smoothed.volume() == pytest.approx(literal.volume(), rel=0.10)


def test_voxel_remesh_isolated_fat_disc_is_a_lens_not_a_ball():
    """One soma-scale annotation between distant thin ones stays a disc."""
    from sbfsem.geometry.remesh import voxel_remesh

    pts = np.array([[0, 0, 0.0], [0, 0, 4.0], [0, 0, 8.0]])
    m = voxel_remesh(pts, np.array([0.4, 3.0, 0.4]), surface_smooth_um=0)
    wide = m.vertices[np.hypot(m.vertices[:, 0], m.vertices[:, 1]) > 1.0]
    # A ball reading would span z = 1..7 around the fat annotation at z=4.
    assert wide[:, 2].min() > 1.5
    assert wide[:, 2].max() < 6.5


def _mesh_components(m):
    """Number of connected components, via union-find over faces."""
    parent = list(range(m.n_vertices))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, c in m.faces:
        parent[find(int(a))] = find(int(b))
        parent[find(int(b))] = find(int(c))
    used = {find(int(v)) for v in np.unique(m.faces)}
    return len(used)


def test_voxel_remesh_cell_merges_a_soma_split_across_rows():
    """A soma's discs live in several segmentation rows; the field must be
    one body with caps only at the true extremes, not a stack of per-row
    'loaves' creased wherever a row happened to end."""
    from sbfsem.geometry.remesh import voxel_remesh_cell

    z = np.arange(0, 10.01, 0.25)
    pts = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    rad = np.full(len(z), 4.0)
    # Alternate sections between two rows, as branch-walking segmentation does.
    rows = [(pts[0::2], rad[0::2]), (pts[1::2], rad[1::2])]
    meshes, covered = voxel_remesh_cell(rows, voxels_per_radius=8,
                                        surface_smooth_um=0)
    assert len(meshes) == 1
    assert all(mask.all() for mask in covered)
    m = meshes[0]
    assert _mesh_components(m) == 1
    # One cylinder, not two interleaved coin stacks with caps everywhere.
    assert m.volume() == pytest.approx(np.pi * 16 * 10, rel=0.03)
    assert m.vertices[:, 2].min() > -0.5
    assert m.vertices[:, 2].max() < 10.5


def test_voxel_remesh_cell_leaves_thin_geometry_to_the_tubes():
    """A row mixing a soma with a thin dendrite must cover only the soma;
    the dendrite is the tube builders' job, so the old per-segment failure
    mode -- the whole row declining because the grid could not hold the thin
    tail -- cannot happen at all."""
    from sbfsem.geometry.remesh import voxel_remesh_cell

    z = np.arange(0, 5.01, 0.25)
    soma_p = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    soma_r = np.full(len(z), 5.0)
    tail_p = np.array([[6.0, 0, 2.0], [16.0, 0, 2.0], [26.0, 0, 2.0]])
    tail_r = np.array([0.3, 0.3, 0.3])
    rows = [(np.vstack([soma_p, tail_p]), np.concatenate([soma_r, tail_r]))]
    meshes, covered = voxel_remesh_cell(rows, voxels_per_radius=8,
                                        surface_smooth_um=0)
    assert len(meshes) == 1
    assert covered[0][:len(z)].all()
    assert not covered[0][len(z):].any()
    # Nothing of the thin tail may appear in the cluster mesh.
    assert meshes[0].vertices[:, 0].max() < 8.0


def test_voxel_remesh_cell_bridges_past_an_interloper():
    """Review regression: a fat annotation from another branch sitting at an
    intermediate z but off to the side must not block the row-boundary bridge
    -- the soma would split into bodies with facing caps."""
    from sbfsem.geometry.remesh import voxel_remesh_cell

    def col(zs):
        return (np.column_stack([np.zeros(len(zs)), np.zeros(len(zs)), zs]),
                np.full(len(zs), 4.0))
    rows = [col(np.array([0.4, 1.2, 2.0, 2.8])),
            col(np.array([4.4, 5.2, 6.0, 6.8])),
            (np.array([[5.9, 0, 3.6]]), np.array([2.0]))]  # the interloper
    meshes, covered = voxel_remesh_cell(rows, voxels_per_radius=8,
                                        surface_smooth_um=0)
    assert len(meshes) == 1
    m = meshes[0]
    # The column plus its lens neighbour: 2 components at most (the lens may
    # merge with the column), never 3 (a split column).
    assert _mesh_components(m) <= 2
    # The column itself is continuous: some surface exists in the gap band.
    zs = m.vertices[:, 2]
    near_axis = np.hypot(m.vertices[:, 0], m.vertices[:, 1]) < 4.5
    assert ((zs > 3.0) & (zs < 4.2) & near_axis).any()


def test_voxel_remesh_same_z_offset_duplicate_is_not_lost():
    """Review regression: a laterally offset same-z duplicate covers a
    zero-height window no slice samples; its disc must still materialize."""
    from sbfsem.geometry.remesh import voxel_remesh

    pts = np.array([[0, 0, 0.0], [0, 0, 1.0], [0, 0, 2.0], [0, 0, 3.0],
                    [3.5, 0, 3.0]])
    m = voxel_remesh(pts, np.full(5, 4.0), voxels_per_radius=8,
                     surface_smooth_um=0)
    # The duplicate's disc reaches x = 7.5; a voxel of slack.
    assert m.vertices[:, 0].max() > 7.5 - 0.75


def test_voxel_remesh_cell_reports_blur_erased_members_as_uncovered():
    """Review regression: the cluster-wide sigma can erase a small member
    entirely; it must come back uncovered so the tubes render it."""
    from sbfsem.geometry.remesh import voxel_remesh_cell

    z = np.arange(0, 10.01, 0.5)
    soma = (np.column_stack([np.zeros_like(z), np.zeros_like(z), z]),
            np.full(len(z), 5.0))
    den = (np.array([[4.0, 0, 5.0], [6.4, 0, 5.13], [6.4, 0, 5.22],
                     [9.0, 0, 5.3]]),
           np.array([0.3, 1.6, 1.6, 0.3]))
    meshes, covered = voxel_remesh_cell([soma, den])
    assert len(meshes) == 1
    reaches = meshes[0].vertices[:, 0].max() > 7.0
    # Either the swelling made it into the envelope, or it is honestly
    # uncovered and the tube builders take it -- never covered-but-absent.
    assert reaches or not covered[1][1:3].any()


def test_uncovered_runs_survives_malformed_rows():
    """Review regression: a row with mismatched lengths crashed the render."""
    from sbfsem.geometry.cylinder import _uncovered_runs

    pts = np.zeros((4, 3))
    pts[:, 2] = np.arange(4)
    out = _uncovered_runs(pts, np.array([1.0, 1.0, 1.0]), np.zeros(4, bool))
    assert out == []  # nothing renderable, but no crash


def test_cube_wrap_recovers_a_tube_union_envelope():
    """The wrap must reproduce the tube union's size, minus its folds."""
    from sbfsem.geometry.wrap import cube_wrap_cell

    z = np.arange(0, 10.01, 0.5)
    rows = [(np.column_stack([np.zeros_like(z), np.zeros_like(z), z]),
             np.full(len(z), 3.0))]
    meshes, covered = cube_wrap_cell(rows, spacing_um=0.5)
    assert len(meshes) == 1
    m = meshes[0]
    assert covered[0].all()
    # The wrap's contract is the size of the tube mesh it fits (line3's end
    # caps are shallow, so this is a bit under cylinder + two hemispheres).
    from sbfsem.geometry.cylinder import segment_mesh
    tube = segment_mesh(*rows[0], adaptive_rings=True, cap_ends=True)
    assert m.volume() == pytest.approx(abs(tube.volume()), rel=0.10)
    assert _mesh_components(m) == 1


def test_cube_wrap_discards_interior_folds():
    """Two fat rows crossing through each other must wrap into ONE outer
    envelope with no interior sheets -- the soma-fold cure.

    Every annotation here sits in the SAME section, so the disc reading is a
    flat in-plane cross, not two 2.5 um cylinders: the wrap now agrees with
    the voxel pathway on that (an earlier version fitted the tube union and
    pinned the cylinder volume here, which is the ball-reading this project
    keeps killing).
    """
    from sbfsem.geometry.wrap import cube_wrap_cell

    t = np.linspace(-5, 5, 21)
    a = (np.column_stack([t, np.zeros_like(t), np.zeros_like(t)]),
         np.full(len(t), 2.5))
    b = (np.column_stack([np.zeros_like(t), t, np.zeros_like(t)]),
         np.full(len(t), 2.5))
    meshes, _cov = cube_wrap_cell([a, b], spacing_um=0.5)
    assert len(meshes) == 1
    m = meshes[0]
    assert _mesh_components(m) == 1
    # A flat cross: real volume, far less than the two-cylinder union, and
    # confined near the annotated section instead of bulging a radius in z.
    capsule = np.pi * 2.5**2 * 10 + 4 / 3 * np.pi * 2.5**3
    assert 50 < m.volume() < capsule
    assert np.abs(m.vertices[:, 2]).max() < 1.8


def test_cube_wrap_leaves_thin_rows_uncovered():
    from sbfsem.geometry.wrap import cube_wrap_cell

    z = np.arange(0, 5.01, 0.5)
    rows = [(np.column_stack([np.zeros_like(z), np.zeros_like(z), z]),
             np.full(len(z), 0.4))]
    meshes, covered = cube_wrap_cell(rows)
    assert meshes == []
    assert not covered[0].any()


def test_gencyl_antiparallel_z_segment_is_not_mirrored():
    """Review regression: a straight-down segment came back drawn upward."""
    from sbfsem.geometry.cylinder import gencyl_mesh

    m = gencyl_mesh(np.array([[0, 0, 0.0], [0, 0, -5.0]]), np.array([1.0, 1.0]))
    assert m.vertices[:, 2].min() < -4.5
    assert m.vertices[:, 2].max() < 0.5


def test_gencyl_fan_caps_point_outward():
    """Review regression: both fans wound the same way, one always inward."""
    from sbfsem.geometry.cylinder import gencyl_mesh

    m = gencyl_mesh(np.array([[0, 0, 0.0], [0, 0, 5.0], [0, 0, 10.0]]),
                    np.array([1.0, 1.0, 1.0]), cap_ends=True)
    V, F = m.vertices, m.faces
    def cap_normal_z(center_z):
        picked = [f for f in F if np.isclose(V[f[0], 2], center_z) and
                  np.isclose(V[f[0], 0], 0) and np.isclose(V[f[0], 1], 0)]
        n = np.zeros(3)
        for f in picked:
            n += np.cross(V[f[1]] - V[f[0]], V[f[2]] - V[f[0]])
        return n[2]
    assert cap_normal_z(0.0) < 0     # bottom cap faces down
    assert cap_normal_z(10.0) > 0    # top cap faces up


def test_gencyl_cap_ends_closes_the_tube():
    """MATLAB displayed patches double-sided, so gencyl's open ends were
    invisible; a front-face-culled viewer sees straight down the pipe.  The
    modern pathways close the ends; the default stays byte-faithful."""
    from sbfsem.geometry.cylinder import gencyl_mesh

    def boundary_edges(m):
        # gencyl duplicates its seam column and junction rings (MATLAB
        # heritage), so merge coincident vertices before counting.
        key = np.round(m.vertices, 6)
        _uniq, remap = np.unique(key, axis=0, return_inverse=True)
        f = remap[m.faces]
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        e = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        e.sort(axis=1)
        _uniq, counts = np.unique(e, axis=0, return_counts=True)
        return int((counts == 1).sum())

    pts = np.array([[0, 0, 0.0], [0, 0, 5.0], [1, 0, 10.0]])
    rad = np.array([1.0, 1.0, 1.0])
    open_mesh = gencyl_mesh(pts, rad)
    capped = gencyl_mesh(pts, rad, cap_ends=True)
    assert boundary_edges(open_mesh) > 0
    assert boundary_edges(capped) == 0


def test_tie_pieces_skips_gaps_already_joined_elsewhere():
    """The Hybrid method's disc half sees a gap across every traced soma; the
    closed-curve volume already joins those pieces, so no wire is laid."""
    from sbfsem.geometry.cylinder import _tie_pieces, _tie_segments

    def tube(x0: float, x1: float, r: float = 0.5):
        x = np.arange(x0, x1 + 1e-9, 0.25)
        pts = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
        return segment_mesh(pts, np.full(len(x), r), cap_ends=True)

    near = [tube(0, 10), tube(12, 20)]  # ~2 um gap
    assert len(_tie_segments(near, 20.0)) == 1
    assert _tie_segments(near, 20.0, attached=lambda m: True) == []
    assert _tie_pieces(near, 20.0, attached=lambda m: True) == []
    # Only one of the two attached: the wire is still needed.
    first = near[0]
    assert len(_tie_segments(near, 20.0, attached=lambda m: m is first)) == 1
    # Three in a row, the outer two attached and the middle one not: the
    # middle piece still gets tied in, but no wire joins the two outer ones.
    three = [tube(0, 10), tube(12, 20), tube(22, 30)]
    outer = (three[0], three[2])
    segs = _tie_segments(three, 20.0, attached=lambda m: any(m is o for o in outer))
    assert len(segs) == 2


def test_tie_pieces_closes_small_gaps_and_leaves_wide_ones():
    from sbfsem.geometry.cylinder import _tie_pieces
    from sbfsem.geometry.meshdiff import visual_bodies

    def tube(x0: float, x1: float, r: float = 0.5):
        x = np.arange(x0, x1 + 1e-9, 0.25)
        pts = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
        return segment_mesh(pts, np.full(len(x), r), cap_ends=True)

    near = [tube(0, 10), tube(12, 20)]  # ~2 um gap
    ties = _tie_pieces(near, max_tie_um=20.0)
    assert len(ties) == 1
    both = concatenate(near + ties)
    assert visual_bodies(both) == 1
    # A gap past the ceiling stays honest: no wire through 25 um of nothing.
    far = [tube(0, 10), tube(35, 45)]
    assert _tie_pieces(far, max_tie_um=20.0) == []


def test_voxel_render_ties_a_detached_dendrite_to_the_soma():
    """A thin row that stops short of the envelope (an unlinked Viking
    component, or an anchor the blur pulled the surface away from) must not
    float: the render draws an attachment-scale tie across the gap."""
    import pandas as pd

    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.meshdiff import visual_bodies
    from sbfsem.geometry.segmentation import SegmentationResult

    z = np.arange(0.0, 10.01, 0.5)
    soma_pts = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    den_x = np.arange(8.0, 20.01, 0.25)
    den_pts = np.column_stack([den_x, np.zeros_like(den_x),
                               np.full_like(den_x, 5.0)])
    table = pd.DataFrame({
        "ID": [1, 2],
        "XYZum": [soma_pts, den_pts],
        "Rum": [np.full(len(z), 5.0), np.full(len(den_x), 0.3)],
    })
    seg = SegmentationResult([], table, -1, [])

    class _N:
        id = 999

    untied = cylinder_render(_N(), pathway="voxel", segmentation=seg, tie_um=0.0)
    assert visual_bodies(untied) > 1  # the scenario really is detached
    tied = cylinder_render(_N(), pathway="voxel", segmentation=seg)
    assert visual_bodies(tied) == 1


def test_c1178_voxel_renders_as_one_visual_body(c1178):
    """Session 2026-08-30 regression: the production voxel render of c1178
    came apart into 8 visual bodies (soma island, arbor 3.3 um adrift, two
    unlinked 197-annotation branches).  One cell, one body."""
    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.meshdiff import visual_bodies

    mesh = cylinder_render(c1178, pathway="voxel", voxel_smooth_um=0.5)
    assert visual_bodies(mesh) == 1


def test_annotation_deviation_measures_against_the_discs():
    """The annotations are ground truth: a surface that matches its discs
    scores near zero, unsupported surface and unreached discs both score
    positive, and nothing ever cancels."""
    from sbfsem.geometry.meshdiff import annotation_deviation
    from sbfsem.geometry.primitives import cylinder, sphere

    z = np.arange(0, 2.01, 0.1)
    centers = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    radii = np.ones(len(z))

    cyl = cylinder(1.0, 2.0, divisions=64)
    cyl = cyl.translate((0, 0, -float(cyl.vertices[:, 2].min())))
    faithful = annotation_deviation(cyl, centers, radii)
    assert faithful.mean_um < 0.05
    assert faithful.n_discs == len(z)

    # A fat sphere against one small disc: both directions must show it, and
    # the excess volume must dwarf the faithful case's.
    ball = annotation_deviation(sphere(2.0, divisions=40), centers[:1], radii[:1])
    assert ball.surface_um > 1.0       # surface with no annotated support
    assert ball.annotation_um > 0.5    # the disc rim is far from the surface
    assert ball.mean_um > 0
    assert ball.excess_volume_um3 > 10 * faithful.excess_volume_um3

    # No annotations at all: nothing to hold the render to.
    assert annotation_deviation(cyl, np.zeros((0, 3)), np.zeros(0)) is None


def test_annotation_deviation_reads_closed_curve_outlines():
    from sbfsem.geometry.meshdiff import annotation_deviation
    from sbfsem.geometry.primitives import cylinder

    cyl = cylinder(1.0, 2.0, divisions=64)
    cyl = cyl.translate((0, 0, -float(cyl.vertices[:, 2].min())))
    theta = np.linspace(0, 2 * np.pi, 33)[:-1]
    ring = np.column_stack([np.cos(theta), np.sin(theta)])
    # One ring mid-height: the wall near z=1 is supported (rim distance ~0);
    # the rest of the 2 um cylinder honestly averages below the cap height.
    hugging = annotation_deviation(cyl, np.zeros((0, 3)), np.zeros(0),
                                   [(ring, 1.0)])
    assert hugging is not None
    assert hugging.annotation_um < 0.05  # the outline itself is on the wall
    assert hugging.surface_um < 1.0
    # The same outline 10 um off to the side: everything is far, both ways.
    far = annotation_deviation(cyl, np.zeros((0, 3)), np.zeros(0),
                               [(ring + 10.0, 1.0)])
    assert far.mean_um > 5.0 > hugging.mean_um


# -- voxel remesh 2: one field, one surface -----------------------------------

def _boundary_edges(m) -> int:
    """Edges owned by exactly one face: zero for a watertight sheet."""
    key = np.round(m.vertices, 6)
    _uniq, remap = np.unique(key, axis=0, return_inverse=True)
    f = remap[m.faces]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    e = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    e.sort(axis=1)
    _uniq, counts = np.unique(e, axis=0, return_counts=True)
    return int((counts == 1).sum())


def _soma_and_dendrite():
    """A lofted 4 um soma with a 0.4 um dendrite leaving it: the c106 shape."""
    import pandas as pd

    from sbfsem.geometry.segmentation import SegmentationResult

    z = np.arange(0.0, 8.01, 0.25)
    soma = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    x = np.arange(0.0, 12.01, 0.5)
    den = np.column_stack([x, np.zeros_like(x), np.full_like(x, 4.0)])
    table = pd.DataFrame({
        "ID": [1, 2], "XYZum": [soma, den],
        "Rum": [np.full(len(z), 4.0), np.full(len(x), 0.4)],
    })
    return SegmentationResult([], table, -1, [])


class _Cell:
    id = 7


def test_sampled_field_is_the_cluster_field_the_voxel_pathway_contours():
    """The fields voxel2 resamples are exactly what voxel marches: the marched
    surface reads ~0 through the tricubic resample, the center reads deep
    inside, the air outside stays positive, and the coverage masks agree."""
    from sbfsem.geometry.remesh import voxel_remesh_cell, voxel_remesh_cell_fields

    z = np.arange(0.0, 6.01, 0.25)
    pts = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    rad = np.full(len(z), 3.0)
    meshes, covered = voxel_remesh_cell([(pts, rad)])
    fields, covered2 = voxel_remesh_cell_fields([(pts, rad)])
    assert len(meshes) == len(fields) == 1
    assert (covered[0] == covered2[0]).all()
    sf = fields[0]
    on_surface = sf.sample(meshes[0].vertices)
    assert np.abs(on_surface).mean() < 0.2 * sf.voxel
    assert sf.sample(np.array([[0.0, 0.0, 3.0]]))[0] < -2.0
    assert sf.sample(np.array([[8.0, 0.0, 3.0]]))[0] > 1.0
    # Trilinear agrees with tricubic to well under a voxel.
    lin = sf.sample(meshes[0].vertices, order=1)
    assert np.abs(lin - on_surface).max() < 0.5 * sf.voxel


def test_voxel2_makes_a_dendrite_and_its_soma_one_surface():
    """The pathway's reason to exist (c106, Temporal Monkey): a tube leaving
    a lofted soma is part of the soma's sheet, not a second shell pushed into
    it -- one topological component, watertight, and still the right size."""
    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.meshdiff import visual_bodies
    from sbfsem.geometry.unify import split_components

    seg = _soma_and_dendrite()
    pieces = cylinder_render(_Cell(), pathway="voxel", segmentation=seg)
    one = cylinder_render(_Cell(), pathway="voxel2", segmentation=seg, voxel2_um=0.15)
    assert len(split_components(pieces)) >= 2      # the scenario: separate shells
    assert len(split_components(one)) == 1
    assert one.meta["voxel2_components"] == 1
    assert visual_bodies(one) == 1
    assert _boundary_edges(one) == 0
    # The dendrite is really there, at its own girth, and the soma is a soma.
    assert one.vertices[:, 0].max() > 11.5
    tube = one.vertices[one.vertices[:, 0] > 6.0]
    assert 0.3 < np.hypot(tube[:, 1], tube[:, 2] - 4.0).max() < 0.6
    assert abs(one.volume() - pieces.volume()) / pieces.volume() < 0.08


def test_voxel2_face_cap_and_fillet():
    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.unify import split_components

    seg = _soma_and_dendrite()
    capped = cylinder_render(_Cell(), pathway="voxel2", segmentation=seg,
                             voxel2_um=0.15, voxel2_faces=4000)
    assert len(capped.faces) <= 4200
    assert len(split_components(capped)) == 1
    sharp = cylinder_render(_Cell(), pathway="voxel2", segmentation=seg, voxel2_um=0.15)
    rounded = cylinder_render(_Cell(), pathway="voxel2", segmentation=seg,
                              voxel2_um=0.15, voxel2_blend_um=0.5)
    # A fillet adds material at the root and nowhere else.
    assert rounded.volume() > sharp.volume()
    assert rounded.volume() < 1.02 * sharp.volume()
    assert len(split_components(rounded)) == 1


def test_voxel2_asks_for_a_raw_sheet_three_times_its_face_cap(monkeypatch):
    """The raw sheet the pathway marches is ``RAW_PER_CAPPED_FACE`` (3) times the face cap it is
    decimated to, never under 600,000 and never over the module's ceiling.  The constant was dead
    until the budget was linked to it, and 4 in its place kept 220 tests green."""
    from sbfsem.geometry import unify
    from sbfsem.geometry.cylinder import cylinder_render

    asked = []
    real = unify.unified_mesh

    def spy(items, **kwargs):
        asked.append(kwargs["max_raw_faces"])
        return real(items, **kwargs)

    monkeypatch.setattr(unify, "unified_mesh", spy)
    seg = _soma_and_dendrite()
    caps = (400_000, 100_000, 2_000_000, 4_000_000, 0)
    for faces in caps:
        cylinder_render(_Cell(), pathway="voxel2", segmentation=seg, voxel2_um=0.5, voxel2_faces=faces)
    assert unify.RAW_PER_CAPPED_FACE == 3
    assert asked == [1_200_000, 600_000, 6_000_000, unify.MAX_RAW_FACES, unify.MAX_RAW_FACES]
    assert asked == [unify.raw_budget(faces) for faces in caps]


def test_unified_mesh_blocks_weld_exactly():
    """A tube crossing many blocks comes out watertight, in one piece, at the
    analytic volume: shared-face samples are bit-identical across blocks."""
    from sbfsem.geometry.unify import split_components, unified_mesh

    x = np.arange(0.0, 20.01, 1.0)
    pts = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
    m = unified_mesh([("stretch", pts, np.full(len(x), 0.5))], voxel_um=0.1, tie_um=0)
    assert m.meta["voxel2_blocks"] > 4
    assert len(split_components(m)) == 1
    assert _boundary_edges(m) == 0
    expected = np.pi * 0.5 ** 2 * 20.0 + 4.0 / 3.0 * np.pi * 0.5 ** 3
    assert abs(m.volume() - expected) / expected < 0.05


def test_unified_mesh_ties_are_part_of_the_field():
    from sbfsem.geometry.unify import split_components, unified_mesh

    def stretch(x0: float, x1: float):
        x = np.arange(x0, x1 + 1e-9, 0.5)
        return ("stretch", np.column_stack([x, np.zeros_like(x), np.zeros_like(x)]),
                np.full(len(x), 0.4))

    items = [stretch(0.0, 5.0), stretch(7.0, 12.0)]   # 1.2 um between the surfaces
    apart = unified_mesh(items, voxel_um=0.1, tie_um=0.0)
    assert len(split_components(apart)) == 2
    tied = unified_mesh(items, voxel_um=0.1, tie_um=20.0)
    assert tied.meta["voxel2_ties"] == 1
    assert len(split_components(tied)) == 1
    assert _boundary_edges(tied) == 0


def test_unified_mesh_coin_is_a_coin_and_link_is_thin():
    from sbfsem.geometry.unify import unified_mesh

    coin = unified_mesh([("coin", np.array([0.0, 0.0, 0.0]), 2.0)],
                        voxel_um=0.1, tie_um=0.0, lens_h=0.1)
    zs = coin.vertices[:, 2]
    assert zs.max() - zs.min() < 0.5            # 0.2 um tall, never a 4 um ball
    assert np.hypot(coin.vertices[:, 0], coin.vertices[:, 1]).max() > 1.9

    link = unified_mesh([("link", np.zeros(3), np.array([3.0, 0.0, 0.0]), 0.75)],
                        voxel_um=0.1, tie_um=0.0)
    assert np.hypot(link.vertices[:, 1], link.vertices[:, 2]).max() < 0.9


def test_unified_mesh_sizes_its_grid_and_keeps_its_budgets():
    from sbfsem.geometry.unify import (
        MAX_VOXEL_UM,
        MIN_VOXEL_UM,
        auto_voxel_um,
        unified_mesh,
    )

    # Two voxels across the thin radius, within the bounds.
    assert abs(auto_voxel_um([0.3, 0.3, 0.3], 0.0) - 0.15) < 1e-9
    assert auto_voxel_um([0.01], 0.0) == MIN_VOXEL_UM
    assert auto_voxel_um([5.0], 0.0) == MAX_VOXEL_UM
    # A huge surface is coarsened to the raw-face budget.
    assert auto_voxel_um([0.3], 1e6) > 0.15

    x = np.arange(0.0, 10.01, 0.5)
    items = [("stretch", np.column_stack([x, np.zeros_like(x), np.zeros_like(x)]),
              np.full(len(x), 0.3))]
    m = unified_mesh(items, voxel_um=0.05, tie_um=0.0, max_work=20_000)
    assert m.meta["voxel2_coarsened"] is True
    assert m.meta["voxel2_um"] > 0.05
    exact = unified_mesh(items, voxel_um=0.05, tie_um=0.0)
    assert exact.meta["voxel2_coarsened"] is False
    assert abs(exact.meta["voxel2_um"] - 0.05) < 1e-9


def test_the_raw_triangle_limit_is_ten_million_and_follows_the_face_cap():
    """The ceiling was 3 million until 2026-10-16.  A build asks for three times its Face cap
    (the app's 400,000 asks for 1.2 million), at least 600,000 and at most the ceiling; with no
    cap (0) it asks for the ceiling itself."""
    from sbfsem.geometry import unify

    assert unify.MAX_RAW_FACES == 10_000_000
    assert unify.RAW_FACES_KEYED_ABOVE == 3_000_000
    assert unify.raw_budget(400_000) == 1_200_000
    assert unify.raw_budget(100_000) == unify.MIN_RAW_BUDGET == 600_000
    assert unify.raw_budget(1_000_000) == unify.RAW_FACES_KEYED_ABOVE
    assert unify.raw_budget(2_000_000) == 6_000_000
    assert unify.raw_budget(3_400_000) == unify.MAX_RAW_FACES
    assert unify.raw_budget(5_000_000) == unify.MAX_RAW_FACES      # the Face cap's own maximum
    assert unify.raw_budget(0) == unify.MAX_RAW_FACES


def test_the_evaluation_budget_follows_a_raw_budget_over_three_million():
    """Raising the triangle limit alone changed nothing measured: Inferior c1178 came out at
    0.0745 um, not 0.076, with the same deviation, because the 60 million evaluations took over at
    once (it needs 194 million at 0.0414 um, the finest voxel the new ceiling allows it).  The
    evaluation budget keeps its 20 per triangle above 3 million, and a build at or under 3 million
    keeps the 60 million it always had."""
    from sbfsem.geometry import unify

    assert unify.MAX_WORK_VOXELS == 60_000_000
    assert unify.work_budget(1_200_000) == 60_000_000
    assert unify.work_budget(unify.RAW_FACES_KEYED_ABOVE) == 60_000_000
    assert unify.work_budget(6_000_000) == 120_000_000
    assert unify.work_budget(unify.MAX_RAW_FACES) == 200_000_000


def test_unified_mesh_takes_its_evaluation_budget_from_its_raw_budget(monkeypatch):
    from sbfsem.geometry import unify
    from sbfsem.geometry.unify import unified_mesh

    x = np.arange(0.0, 10.01, 0.5)
    items = [("stretch", np.column_stack([x, np.zeros_like(x), np.zeros_like(x)]),
              np.full(len(x), 0.3))]
    # Scale the budgets down to a stretch that fits in a test: 20,000 evaluations
    # would coarsen it (as in test_unified_mesh_sizes_its_grid_and_keeps_its_budgets).
    monkeypatch.setattr(unify, "MAX_WORK_VOXELS", 20_000)
    monkeypatch.setattr(unify, "WORK_PER_RAW_FACE", 0)
    held = unified_mesh(items, voxel_um=0.05, tie_um=0.0, max_raw_faces=1_000_000)
    assert held.meta["voxel2_coarsened"] is True and held.meta["voxel2_um"] > 0.05
    monkeypatch.setattr(unify, "WORK_PER_RAW_FACE", 1)
    followed = unified_mesh(items, voxel_um=0.05, tie_um=0.0, max_raw_faces=1_000_000)
    assert followed.meta["voxel2_coarsened"] is False
    assert abs(followed.meta["voxel2_um"] - 0.05) < 1e-9
    # An explicit work budget is honored as given.
    given = unified_mesh(items, voxel_um=0.05, tie_um=0.0, max_raw_faces=1_000_000, max_work=20_000)
    assert given.meta["voxel2_coarsened"] is True


def test_c1178_voxel2_renders_as_one_surface(c1178):
    """The cell whose connectivity keeps regressing, as one topological sheet:
    every piece the voxel pathway assembles -- the four-row soma, rescue
    lofts, tubes, ties across Viking's unlinked components -- is one field."""
    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.meshdiff import visual_bodies

    mesh = cylinder_render(c1178, pathway="voxel2", voxel2_um=0.12)
    assert visual_bodies(mesh) == 1
    assert mesh.meta["voxel2_components"] == 1


def test_voxel2_reads_a_travelling_disc_stack_as_a_tube():
    """Eight 1.2 um discs that shift 0.6 um sideways per 0.075 um section are
    an oblique trunk seen section by section (c118's soma exit), not a
    0.5 um tall plate.  voxel2 contours them as a tube about two radii tall;
    voxel (orig) keeps the loft, and its output is unchanged."""
    import pandas as pd

    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.segmentation import SegmentationResult

    n = 8
    pts = np.column_stack([0.6 * np.arange(n), np.zeros(n), 10.0 + 0.075 * np.arange(n)])
    table = pd.DataFrame({"ID": [1], "XYZum": [pts], "Rum": [np.full(n, 1.2)]})
    seg = SegmentationResult([], table, -1, [])

    from sbfsem.geometry.unify import split_components

    tube = cylinder_render(_Cell(), pathway="voxel2", segmentation=seg, voxel2_um=0.1)
    assert np.ptp(tube.vertices[:, 2]) > 2.0          # ~2 r: a tube
    assert len(split_components(tube)) == 1
    # voxel (orig) is deliberately untouched: its rescue loft holds only the
    # discs its slices happen to cut (four of eight here) and the rest fall
    # to a ring tube -- the plate-plus-bulb this rule exists to replace.
    plate = cylinder_render(_Cell(), pathway="voxel", segmentation=seg)
    assert not plate.is_empty
    # A genuine stack -- the same discs with no sideways travel -- stays lofted
    # on both pathways: the body ends at its annotated sections.
    stack = np.column_stack([np.zeros(n), np.zeros(n), 10.0 + 0.075 * np.arange(n)])
    table2 = pd.DataFrame({"ID": [1], "XYZum": [stack], "Rum": [np.full(n, 1.2)]})
    seg2 = SegmentationResult([], table2, -1, [])
    stacked = cylinder_render(_Cell(), pathway="voxel2", segmentation=seg2, voxel2_um=0.1)
    assert np.ptp(stacked.vertices[:, 2]) < 1.6


# -- convolution surface and Ultraliser pathways ------------------------------

def test_convolution_kernel_reproduces_a_straight_neurite():
    """The paper's weight makes an infinitely long straight neurite of radius d
    the T iso-surface: mid-length along a long chain the field reads T at
    rho = d, and the contoured surface sits at radius d."""
    from sbfsem.geometry.convolution import (
        THRESHOLD,
        SegmentKernel,
        merge_short_segments,
        segment_weight,
    )
    from sbfsem.geometry.unify import split_components, unified_mesh

    d = 0.5
    R = 2.0 * d
    lam = segment_weight(d, R)
    assert lam == pytest.approx(15 * THRESHOLD * R ** 4 / (16 * (R * R - d * d) ** 2.5))
    z = np.arange(0.0, 40.01, 1.0)
    pts = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    segs = [SegmentKernel.from_pair(pts[i], pts[i + 1], d, d) for i in range(len(z) - 1)]
    probe = np.array([[rho, 0.0, 20.0] for rho in (0.0, 0.5, 0.9)])
    field = sum(s.potential(probe) for s in segs)
    assert field[0] > THRESHOLD > field[2]
    assert field[1] == pytest.approx(THRESHOLD, abs=1e-6)

    mesh = unified_mesh([("stretch", pts, np.full(len(z), d))], voxel_um=0.1, tie_um=0.0,
                        mode="sum")
    assert len(split_components(mesh)) == 1
    mid = mesh.vertices[(mesh.vertices[:, 2] > 10) & (mesh.vertices[:, 2] < 30)]
    rho = np.hypot(mid[:, 0], mid[:, 1])
    assert abs(rho.mean() - d) < 0.03

    # Section-by-section samples merge into segments a fraction of the kernel
    # radius long; both ends survive and the radius is carried along.
    dense = np.column_stack([np.zeros(101), np.zeros(101), np.linspace(0, 10, 101)])
    merged_p, merged_r = merge_short_segments(dense, np.full(101, 2.0))
    assert 3 <= len(merged_p) < 30
    assert np.allclose(merged_p[0], dense[0]) and np.allclose(merged_p[-1], dense[-1])
    assert np.allclose(merged_r, 2.0)


def test_convolution_pathway_blends_soma_and_dendrite_into_one_sheet():
    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.unify import split_components

    seg = _soma_and_dendrite()
    one = cylinder_render(_Cell(), pathway="convolution", segmentation=seg, voxel2_um=0.15)
    assert one.meta["field_mode"] == "sum"
    assert len(split_components(one)) == 1
    assert _boundary_edges(one) == 0
    # The dendrite keeps its radius away from the soma, and it is all there.
    tube = one.vertices[one.vertices[:, 0] > 7.0]
    assert 0.3 < np.hypot(tube[:, 1], tube[:, 2] - 4.0).max() < 0.7
    assert one.vertices[:, 0].max() > 11.0


def test_ultraliser_pathway_voxelizes_smooths_and_stays_one_sheet():
    from sbfsem.geometry.cylinder import cylinder_render
    from sbfsem.geometry.unify import split_components

    seg = _soma_and_dendrite()
    ref = cylinder_render(_Cell(), pathway="voxel2", segmentation=seg, voxel2_um=0.15)
    ultra = cylinder_render(_Cell(), pathway="ultraliser", segmentation=seg, voxel2_um=0.15)
    assert ultra.meta["field_mode"] == "occupancy"
    assert len(split_components(ultra)) == 1
    assert _boundary_edges(ultra) == 0
    # Same solid, binarized then smoothed: the volume agrees to a few percent.
    assert abs(ultra.volume() - ref.volume()) / ref.volume() < 0.08
    # Without smoothing the contour is the half-voxel staircase: more area.
    raw = cylinder_render(_Cell(), pathway="ultraliser", segmentation=seg, voxel2_um=0.15,
                          ultra_smooth=0)
    assert raw.surface_area() > ultra.surface_area()
    # Ultraliser's own soma: an icosphere of the mean radius, still one sheet.
    ball = cylinder_render(_Cell(), pathway="ultraliser", segmentation=seg, voxel2_um=0.15,
                           ultra_soma="sphere")
    assert len(split_components(ball)) == 1
    with pytest.raises(ValueError):
        cylinder_render(_Cell(), pathway="ultraliser", segmentation=seg, ultra_soma="cube")
