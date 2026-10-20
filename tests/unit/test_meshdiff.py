"""The compare view's difference measurement, checked against analytic shapes.

Concentric spheres have a known signed distance everywhere (the difference of
their radii), which pins both the magnitude and the sign convention: positive
means mesh A sits outside mesh B.
"""

from __future__ import annotations

import numpy as np
import pytest

from sbfsem.geometry.mesh import Mesh
from sbfsem.geometry.meshdiff import mesh_difference, sample_surface
from sbfsem.geometry.primitives import synapse_spheres


def sphere(radius: float) -> Mesh:
    return synapse_spheres(np.zeros((1, 3)), radius, divisions=48)


def test_concentric_spheres_measure_their_radius_gap():
    outer, inner = sphere(6.0), sphere(5.0)
    diff = mesh_difference(outer, inner, samples=50_000)

    # A polyhedral "radius 6" sphere lies slightly inside the true sphere and
    # the sample spacing adds noise, so the tolerance is loose but the value
    # is unmistakably ~1 um, not 0 and not 2.
    assert diff.mean_signed_um == pytest.approx(1.0, abs=0.1)
    assert diff.mean_abs_um == pytest.approx(1.0, abs=0.1)
    assert (diff.distance_um > 0).all(), "outside B must be positive"
    assert len(diff.distance_um) == outer.n_vertices


def test_sign_flips_with_the_direction_of_comparison():
    outer, inner = sphere(6.0), sphere(5.0)
    diff = mesh_difference(inner, outer, samples=50_000)
    assert diff.mean_signed_um == pytest.approx(-1.0, abs=0.1)
    assert (diff.distance_um < 0).all(), "inside B must be negative"


def test_identical_meshes_differ_by_sampling_noise_only():
    ball = sphere(5.0)
    diff = mesh_difference(ball, ball, samples=50_000)
    # Every vertex of A is literally a sample point of B (vertices are
    # appended to the sample set), so the distance is exactly zero.
    assert diff.max_abs_um == pytest.approx(0.0, abs=1e-9)


def test_stats_are_internally_consistent():
    diff = mesh_difference(sphere(6.0), sphere(5.0), samples=50_000)
    assert diff.p95_abs_um <= diff.max_abs_um
    assert diff.mean_abs_um <= diff.max_abs_um
    assert diff.rms_um >= abs(diff.mean_signed_um) - 1e-9
    assert diff.scale_um > 0
    assert diff.sample_spacing_um > 0
    assert diff.n_samples == 50_000 + sphere(5.0).n_vertices


def test_diff_is_deterministic():
    a, b = sphere(6.0), sphere(5.0)
    first = mesh_difference(a, b, samples=20_000)
    second = mesh_difference(a, b, samples=20_000)
    np.testing.assert_array_equal(first.distance_um, second.distance_um)


def test_empty_meshes_are_refused():
    empty = Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=int))
    with pytest.raises(ValueError):
        mesh_difference(empty, sphere(5.0))
    with pytest.raises(ValueError):
        mesh_difference(sphere(5.0), empty)
    with pytest.raises(ValueError):
        sample_surface(empty, 100)


def test_sample_surface_points_lie_on_the_sphere():
    ball = sphere(5.0)
    points, normals = sample_surface(ball, 10_000)
    radii = np.linalg.norm(points, axis=1)
    # On the polyhedral surface: never outside the circumsphere, never far in.
    assert radii.max() <= 5.0 + 1e-6
    assert radii.min() > 4.5
    lengths = np.linalg.norm(normals, axis=1)
    assert np.allclose(lengths[lengths > 0], 1.0, atol=1e-6)


def test_enclosed_points_by_ray_parity():
    """Inside a closed sphere is inside; outside is outside; the surface of a
    small sphere buried in a big one is hidden inside the union."""
    from sbfsem.geometry.mesh import concatenate
    from sbfsem.geometry.meshdiff import enclosed_points
    from sbfsem.geometry.primitives import sphere

    big = sphere(2.0, divisions=24)
    pts = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [2.5, 0.0, 0.0], [0.0, 0.0, -3.0]])
    assert enclosed_points(pts, big).tolist() == [True, True, False, False]
    small = sphere(0.5, divisions=16)
    union = concatenate([big, small])
    probe = small.vertices * 1.1
    assert enclosed_points(probe, union).all()


def test_annotation_deviation_sets_hidden_surface_aside():
    """A tube buried inside a soma-sized sphere is hidden surface: it pulls
    the raw surface mean down and is excluded from the exterior one."""
    from sbfsem.geometry.mesh import concatenate
    from sbfsem.geometry.meshdiff import annotation_deviation
    from sbfsem.geometry.primitives import cylinder, sphere

    z = np.arange(-2.0, 2.01, 0.1)
    centers = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    radii = np.sqrt(np.maximum(4.0 - z ** 2, 0.04))   # the sphere's own discs
    ball = sphere(2.0, divisions=40)
    alone = annotation_deviation(ball, centers, radii)
    tube = cylinder(0.3, 3.0, divisions=24).translate((0, 0, -1.5))
    buried = annotation_deviation(concatenate([ball, tube]), centers, radii)
    assert alone.hidden_fraction < 0.02
    assert buried.hidden_fraction > 0.1
    assert buried.surface_um < alone.surface_um
    assert abs(buried.exterior_um - alone.exterior_um) < 0.03


def _brute_distance_to_polygons(pts, polygons):
    """Every point against every edge of every outline: the definition."""
    best = np.full(len(pts), np.inf)
    for ring, z in polygons:
        a, b = ring, np.roll(ring, -1, axis=0)
        ab = b - a
        t = np.clip(np.einsum("ijk,jk->ij", pts[:, None, :2] - a[None], ab)
                    / np.einsum("ij,ij->i", ab, ab), 0.0, 1.0)
        close = a[None] + t[..., None] * ab[None]
        d_edge = np.hypot(pts[:, None, 0] - close[..., 0], pts[:, None, 1] - close[..., 1]).min(axis=1)
        cond = (a[None, :, 1] > pts[:, None, 1]) != (b[None, :, 1] > pts[:, None, 1])
        with np.errstate(divide="ignore", invalid="ignore"):
            x_at = a[None, :, 0] + (pts[:, None, 1] - a[None, :, 1]) * ab[None, :, 0] \
                / np.where(ab[None, :, 1] == 0, np.nan, ab[None, :, 1])
        inside = ((cond & (pts[:, None, 0] < x_at)).sum(axis=1) % 2) == 1
        best = np.minimum(best, np.hypot(np.where(inside, 0.0, d_edge), pts[:, 2] - z))
    return best


def test_distance_to_outlines_skips_far_outlines_without_changing_the_answer():
    """RPC1's Mueller cell c2628 (3,978 outlines, 1.4 M outline points) took
    131 s for 3,000 points tested against every outline, 0.65 s once outlines
    that cannot be nearest are skipped -- to the same distances.  Here: a
    stack of wandering, differently sized outlines, points inside, outside,
    between sections and far away."""
    from sbfsem.geometry.meshdiff import _distance_to_polygons

    rng = np.random.default_rng(3)
    theta = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    polygons = []
    for k in range(60):
        r = 1.0 + 0.6 * np.sin(k / 5.0) + 0.2 * np.cos(3 * theta)
        centre = np.array([0.15 * k, 2.0 * np.sin(k / 9.0)])
        polygons.append((np.column_stack([r * np.cos(theta), r * np.sin(theta)]) + centre,
                         10.0 + 0.07 * k))
    pts = np.column_stack([rng.uniform(-4, 13, 1500), rng.uniform(-5, 5, 1500),
                           rng.uniform(8.5, 16.0, 1500)])
    got = _distance_to_polygons(pts, polygons)
    np.testing.assert_allclose(got, _brute_distance_to_polygons(pts, polygons), atol=1e-12)
    assert (got == 0).sum() == 0 and got.min() < 0.05 and got.max() > 3
    on_plane = np.array([[polygons[7][0][:, 0].mean(), polygons[7][0][:, 1].mean(), polygons[7][1]]])
    assert _distance_to_polygons(on_plane, polygons)[0] == 0.0          # inside its outline
    assert np.isinf(_distance_to_polygons(pts, [])).all()


def test_deviation_map_is_the_deviation_point_by_point():
    """The picture and the numbers come from the same distances: a sphere
    measured against its own discs is near zero everywhere, and a sphere 0.5
    um too fat reads 0.5 um at its equator on both layers."""
    from sbfsem.geometry.meshdiff import annotation_deviation, deviation_map
    from sbfsem.geometry.primitives import sphere as ball

    z = np.arange(-1.9, 1.91, 0.1)
    centers = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    radii = np.sqrt(4.0 - z ** 2)
    good = deviation_map(ball(2.0, divisions=48), centers, radii)
    assert len(good.vertex_um) == len(ball(2.0, divisions=48).vertices)
    assert np.percentile(good.vertex_um, 95) < 0.12
    assert good.rim_xyz.shape == (len(good.rim_um), 3) and good.rim_stride == 1
    assert good.n_rims == len(good.rim_um)

    fat_mesh = ball(2.5, divisions=48)
    fat = deviation_map(fat_mesh, centers, radii)
    equator = np.abs(fat_mesh.vertices[:, 2]) < 0.1
    assert fat.vertex_um[equator].mean() == pytest.approx(0.5, abs=0.05)
    rim_equator = np.abs(fat.rim_xyz[:, 2]) < 0.1
    assert fat.rim_um[rim_equator].mean() == pytest.approx(0.5, abs=0.06)
    assert fat.scale_um > good.scale_um
    # Same rims, same surface samples: the mean of the rim layer IS annotation_um.
    dev = annotation_deviation(fat_mesh, centers, radii)
    assert fat.rim_um.mean() == pytest.approx(dev.annotation_um, rel=1e-9)
    assert deviation_map(fat_mesh, np.zeros((0, 3)), np.zeros(0)) is None


def test_a_dense_outline_is_thinned_for_the_enclosure_test_and_the_browser(monkeypatch):
    """Outline vertices arrive every ~0.016 um; past a cap every k-th stands
    for the rest in the enclosure test and in what an error map ships.  The
    averages that use every rim point do not move."""
    from sbfsem.geometry import meshdiff
    from sbfsem.geometry.primitives import cylinder

    theta = np.linspace(0, 2 * np.pi, 4000, endpoint=False)
    ring = np.column_stack([np.cos(theta), np.sin(theta)])
    polygons = [(ring, float(z)) for z in np.arange(-1.0, 1.01, 0.1)]
    mesh = cylinder(1.0, 2.0, divisions=64).translate((0, 0, -1.0))
    full = meshdiff.annotation_deviation(mesh, np.zeros((0, 3)), np.zeros(0), polygons)
    monkeypatch.setattr(meshdiff, "RIM_ENCLOSURE_MAX", 5_000)
    monkeypatch.setattr(meshdiff, "RIM_DISPLAY_MAX", 5_000)
    thinned = meshdiff.annotation_deviation(mesh, np.zeros((0, 3)), np.zeros(0), polygons)
    assert thinned.annotation_um == full.annotation_um and thinned.mean_um == full.mean_um
    assert thinned.annotation_exterior_um == pytest.approx(full.annotation_exterior_um, abs=0.01)
    shown = meshdiff.deviation_map(mesh, np.zeros((0, 3)), np.zeros(0), polygons)
    assert shown.n_rims == 84_000 and shown.rim_stride == 17 and len(shown.rim_um) <= 5_000


def _outline_stack(count=40, points=300, seed=5):
    rng = np.random.default_rng(seed)
    theta = np.linspace(0, 2 * np.pi, points, endpoint=False)
    polygons = []
    for k in range(count):
        r = 1.5 + 0.5 * np.sin(k / 4.0) + 0.2 * np.cos(5 * theta)
        polygons.append((np.column_stack([r * np.cos(theta), r * np.sin(theta)])
                         + np.array([0.1 * k, 0.0]), 5.0 + 0.09 * k))
    pts = np.column_stack([rng.uniform(-3, 8, 4000), rng.uniform(-4, 4, 4000),
                           rng.uniform(4.0, 10.0, 4000)])
    return pts, polygons


def test_the_distance_kernels_give_the_same_numbers_at_any_block_size(monkeypatch):
    """The deviation of a 200,000-vertex mesh asked for 6.5 GB because a block
    of 20,000 points was tested against every edge of an outline at once.  The
    block now follows a memory budget: a budget of one point or a budget of
    the whole stack changes the memory and the speed, never a distance."""
    from sbfsem.geometry import meshdiff

    pts, polygons = _outline_stack()
    rng = np.random.default_rng(8)
    centers = np.column_stack([rng.uniform(-3, 8, 400), rng.uniform(-4, 4, 400),
                               rng.uniform(4.0, 10.0, 400)])
    radii = rng.uniform(0.05, 1.5, 400)
    big = {"polygons": meshdiff._distance_to_polygons(pts, polygons),
           "discs": meshdiff._distance_to_discs(pts, centers, radii)}
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)        # one point per block
    assert meshdiff._block(300, meshdiff.POLYGON_PAIR_BYTES) == 1
    np.testing.assert_array_equal(meshdiff._distance_to_polygons(pts, polygons), big["polygons"])
    np.testing.assert_array_equal(meshdiff._distance_to_discs(pts, centers, radii), big["discs"])
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 10 ** 12)
    assert meshdiff._block(100, meshdiff.POLYGON_PAIR_BYTES) == meshdiff.KERNEL_BLOCK_MAX
    np.testing.assert_array_equal(meshdiff._distance_to_polygons(pts, polygons), big["polygons"])


def test_the_brute_force_fallback_really_runs_in_blocks_and_matches_the_definition(monkeypatch):
    """The fallback is what ate 8 GB on RC1 c606 (8,260 discs): points the k-d
    certificate cannot settle are tested against every disc.  Here more than 64
    small discs and one wide one make every point uncertified, so the fallback
    is the answer, and it is spied on: it must run, in blocks of one point at a
    budget of one byte, and give exactly the plain definition."""
    from sbfsem.geometry import meshdiff

    rng = np.random.default_rng(11)
    centers = rng.uniform(-3, 3, (120, 3))
    radii = np.full(120, 0.15)
    radii[7] = 6.0
    pts = rng.uniform(-4, 4, (500, 3))

    dxy = np.hypot(pts[:, None, 0] - centers[None, :, 0], pts[:, None, 1] - centers[None, :, 1])
    expected = np.hypot(np.maximum(dxy - radii[None, :], 0.0),
                        np.abs(pts[:, None, 2] - centers[None, :, 2])).min(axis=1)

    shapes: list = []
    real = np.broadcast_to

    def spy(array, shape, *args, **kwargs):
        shapes.append(tuple(shape))
        return real(array, shape, *args, **kwargs)

    monkeypatch.setattr(meshdiff.np, "broadcast_to", spy)
    whole = meshdiff._distance_to_discs(pts, centers, radii)
    assert shapes, "the fallback never ran: the fixture does not reach it"
    np.testing.assert_array_equal(whole, expected)
    default_blocks = len(shapes)
    shapes.clear()
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)
    small = meshdiff._distance_to_discs(pts, centers, radii)
    assert len(shapes) > default_blocks and all(s[0] == 1 for s in shapes)
    np.testing.assert_array_equal(small, expected)


def test_a_class_too_big_to_brute_force_falls_back_on_the_points_its_tree_cannot_settle(monkeypatch):
    """The tree answers a class's points whose k nearest centers settle them;
    the rest are tested against every disc of the class.  Here the centers lie
    on a shell around the origin and the radii differ, so a point near the
    origin sees every center about equally far and nothing certifies: the
    fallback must run for those points only, in blocks of the budget, and
    leave the answer the exhaustive loop gives."""
    from sbfsem.geometry import meshdiff

    rng = np.random.default_rng(21)
    direction = rng.normal(size=(300, 3))
    centers = 3.0 * direction / np.linalg.norm(direction, axis=1)[:, None]
    radii = rng.uniform(0.05, 0.45, 300)                    # one class: all under 0.5 um
    pts = np.vstack([rng.uniform(-0.6, 0.6, (200, 3)),      # in the middle: nothing certifies
                     rng.uniform(-4, 4, (200, 3))])         # elsewhere: most do
    ref = _exhaustive_nearest(pts, centers, radii, [])

    blocks: list[int] = []
    real = meshdiff._brute_discs

    def spy(p, *args):
        blocks.append(len(p))
        return real(p, *args)

    monkeypatch.setattr(meshdiff, "_brute_discs", spy)
    got = meshdiff.nearest_annotation(pts, centers, radii)
    assert blocks and sum(blocks) < len(pts), "the fallback should run, for some of the points only"
    for field, expected in zip(got, ref, strict=True):
        np.testing.assert_array_equal(field, expected)
    blocks.clear()
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)
    small = meshdiff.nearest_annotation(pts, centers, radii)
    assert blocks and set(blocks) == {1}
    for field, expected in zip(small, ref, strict=True):
        np.testing.assert_array_equal(field, expected)


def test_the_distance_kernels_stay_inside_their_memory_budget(monkeypatch):
    """4,000 points against 40 outlines of 300 edges is 48 million pairs, 9.6
    GB if tested in one block; with the budget at 24 MB the kernel's peak must
    stay within a small multiple of it."""
    import tracemalloc

    from sbfsem.geometry import meshdiff

    pts, polygons = _outline_stack(count=3, points=1500)
    pts = pts[np.abs(pts[:, 2] - 5.1) < 0.6]
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 24 * 1024 * 1024)
    tracemalloc.start()
    try:
        meshdiff._distance_to_polygons(pts, polygons)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 3 * 24 * 1024 * 1024, peak / 1e6


def test_the_measurements_report_their_stages_in_order():
    """A job shows what the measurement is doing: stage, count of total."""
    from sbfsem import progress
    from sbfsem.geometry import meshdiff
    from sbfsem.geometry.primitives import cylinder

    pts, polygons = _outline_stack(count=12, points=120)
    heard: list[tuple] = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        meshdiff._distance_to_polygons(pts, polygons)
        meshdiff.deviation_map(cylinder(1.0, 2.0, divisions=32).translate((0, 0, -1.0)),
                               np.zeros((0, 3)), np.zeros(0), polygons[:3], samples=2000)
    stages = [s for s, *_ in heard]
    assert stages[0] == "distance to outlines"
    outline = [(d, t) for s, d, t in heard if s == "distance to outlines"][:13]
    assert [d for d, _ in outline] == sorted(d for d, _ in outline)   # counts never go back
    assert outline[-1] == (12, 12)                                    # and the last one is complete
    # Silent without a listener: nothing to import, nothing to configure.
    assert meshdiff._distance_to_polygons(pts, polygons).shape == (len(pts),)


# -- the combined kernel: nearest_annotation -----------------------------------------


def _exhaustive_nearest(pts, centers, radii, polygons):
    """The definition: every point against every disc and every edge of every outline.

    Returns ``dist, owner, radial, axial`` as :func:`nearest_annotation` does.  A disc
    is the flat region ``|p_xy - c_xy| <= r`` in its section plane (distance
    ``hypot(max(dxy - r, 0), dz)``); an outline is a filled planar region at its
    section height (in-plane distance 0 inside by the even-odd rule, else to the
    nearest edge).  Of two annotations at exactly the same distance the lower
    index wins.
    """
    n = len(pts)
    dist, owner = np.full(n, np.inf), np.full(n, -1, dtype=np.int64)
    radial, axial = np.full(n, np.inf), np.full(n, np.inf)
    if len(centers):
        dxy = np.hypot(pts[:, None, 0] - centers[None, :, 0], pts[:, None, 1] - centers[None, :, 1])
        dz = np.abs(pts[:, None, 2] - centers[None, :, 2])
        dr = np.maximum(dxy - radii[None, :], 0.0)
        d = np.hypot(dr, dz)
        j, row = d.argmin(axis=1), np.arange(n)
        dist, owner, radial, axial = d[row, j], j.astype(np.int64), dr[row, j], dz[row, j]
    for k, (ring, z) in enumerate(polygons):
        a, b = ring, np.roll(ring, -1, axis=0)
        ab = b - a
        t = np.clip(np.einsum("ijk,jk->ij", pts[:, None, :2] - a[None], ab)
                    / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-18), 0.0, 1.0)
        close = a[None] + t[..., None] * ab[None]
        d_edge = np.hypot(pts[:, None, 0] - close[..., 0], pts[:, None, 1] - close[..., 1]).min(axis=1)
        cond = (a[None, :, 1] > pts[:, None, 1]) != (b[None, :, 1] > pts[:, None, 1])
        with np.errstate(divide="ignore", invalid="ignore"):
            x_at = a[None, :, 0] + (pts[:, None, 1] - a[None, :, 1]) * ab[None, :, 0] \
                / np.where(ab[None, :, 1] == 0, np.nan, ab[None, :, 1])
        inside = ((cond & (pts[:, None, 0] < x_at)).sum(axis=1) % 2) == 1
        d_plane = np.where(inside, 0.0, d_edge)
        gap = pts[:, 2] - z
        new = np.hypot(d_plane, gap)
        better = new < dist
        dist = np.where(better, new, dist)
        owner = np.where(better, len(centers) + k, owner)
        radial = np.where(better, d_plane, radial)
        axial = np.where(better, np.abs(gap), axial)
    return dist, owner, radial, axial


def _vertex_bound(pts, polygons):
    """The k-d tree's own distance to the nearest outline vertex: part of the
    kernel's answer (see :class:`NearestAnnotation`), an upper bound that the
    exhaustive loop reaches to within one ulp."""
    from scipy.spatial import cKDTree

    vertices = np.vstack([np.column_stack([ring, np.full(len(ring), z)]) for ring, z in polygons])
    return cKDTree(vertices).query(pts)[0]


def _heavy_tailed_discs(seed=2, count=700, huge=(8.5, 9.0, 7.5, 8.8)):
    """Lognormal radii with a handful of huge discs, so the classes are very
    unequal and the widest ones spoil any single-tree certificate."""
    rng = np.random.default_rng(seed)
    radii = np.clip(rng.lognormal(np.log(0.4), 1.0, count), 0.02, 9.0)
    radii[:len(huge)] = huge
    centers = np.column_stack([rng.uniform(-3, 8, count), rng.uniform(-4, 4, count),
                               rng.uniform(4.0, 10.0, count)])
    return centers, radii


def test_nearest_annotation_is_the_exhaustive_loop_over_discs():
    """Radius-class trees are exact: on a plain random field and on a heavy tail
    (lognormal radii plus a few discs of 8 um and up) every distance, owner and
    component equals the loop over every disc, bit for bit."""
    from sbfsem.geometry.meshdiff import DISC_CLASS_EDGES, nearest_annotation

    pts, _ = _outline_stack()
    rng = np.random.default_rng(1)
    plain = (np.column_stack([rng.uniform(-3, 8, 400), rng.uniform(-4, 4, 400),
                              rng.uniform(4.0, 10.0, 400)]), rng.uniform(0.05, 1.5, 400))
    heavy = _heavy_tailed_discs()
    sizes = np.bincount(np.digitize(heavy[1], DISC_CLASS_EDGES))
    assert sizes.max() > 128 and sizes.min() < 20, "the fixture should mix big and tiny classes"
    for centers, radii in (plain, heavy):
        got = nearest_annotation(pts, centers, radii)
        expected = _exhaustive_nearest(pts, centers, radii, [])
        for field, want in zip(got, expected, strict=True):
            np.testing.assert_array_equal(field, want)
        assert got.owner.min() >= 0 and got.owner.max() < len(centers)
        np.testing.assert_array_equal(np.hypot(got.radial, got.axial), got.dist)


def test_of_two_annotations_at_the_same_distance_the_lower_index_wins():
    """Duplicated discs (same center and radius at index i and i + n): the owner is the
    first copy, on the brute-force path (a small class) and on the k-d tree path (a big one)."""
    from sbfsem.geometry.meshdiff import nearest_annotation

    rng = np.random.default_rng(7)
    for n in (20, 300):
        centers = np.column_stack([rng.uniform(-3, 8, n), rng.uniform(-4, 4, n), rng.uniform(4.0, 10.0, n)])
        radii = rng.uniform(0.2, 0.4, n)              # one radius class
        centers, radii = np.vstack([centers, centers]), np.concatenate([radii, radii])
        pts = np.column_stack([rng.uniform(-3, 8, 200), rng.uniform(-4, 4, 200), rng.uniform(4.0, 10.0, 200)])
        got = nearest_annotation(pts, centers, radii)
        assert (got.owner < n).all(), n
        want = _exhaustive_nearest(pts, centers, radii, [])
        for field, expected in zip(got, want, strict=True):
            np.testing.assert_array_equal(field, expected)


def test_nearest_annotation_over_discs_and_outlines_together():
    """Outlines are tested against the disc distance first.  The answer is the
    exhaustive loop's (plus the k-d tree's own distance to the nearest outline
    vertex, an upper bound the loop reaches to within one ulp), bit for bit; and
    against the bare loop it is within 1e-12."""
    from sbfsem.geometry.meshdiff import nearest_annotation

    pts, polygons = _outline_stack()
    pts = pts[:1200]
    centers, radii = _heavy_tailed_discs(seed=4, count=500)
    for with_discs in (False, True):
        c, r = (centers, radii) if with_discs else (np.zeros((0, 3)), np.zeros(0))
        got = nearest_annotation(pts, c, r, polygons)
        expected = _exhaustive_nearest(pts, c, r, polygons)
        np.testing.assert_array_equal(got.dist, np.minimum(expected[0], _vertex_bound(pts, polygons)))
        np.testing.assert_allclose(got.dist, expected[0], rtol=0, atol=1e-12)
        # The owner is a real annotation: its discs first, its outlines after.
        assert got.owner.min() >= 0 and got.owner.max() < len(c) + len(polygons)
        assert (got.owner >= len(c)).any() and (not with_discs or (got.owner < len(c)).any())
        np.testing.assert_array_equal(got.owner, expected[1])
        # dist is the hypotenuse of its two components, to the ulp the vertex bound allows.
        np.testing.assert_allclose(np.hypot(got.radial, got.axial), got.dist, rtol=4e-16, atol=0)
        np.testing.assert_allclose(got.axial, expected[3], rtol=0, atol=1e-12)


def test_the_owner_alone_gives_the_distance():
    """A point's owner is the annotation that is nearest to it: measured against
    that annotation alone, the point is exactly ``dist`` away."""
    from sbfsem.geometry.meshdiff import nearest_annotation

    pts, polygons = _outline_stack(count=12, points=60)
    centers, radii = _heavy_tailed_discs(seed=6, count=60)
    got = nearest_annotation(pts, centers, radii, polygons)
    for i in range(0, len(pts), 37):
        own = int(got.owner[i])
        if own < len(centers):
            alone = _exhaustive_nearest(pts[i:i + 1], centers[own:own + 1], radii[own:own + 1], [])
        else:
            alone = _exhaustive_nearest(pts[i:i + 1], np.zeros((0, 3)), np.zeros(0),
                                        [polygons[own - len(centers)]])
        assert alone[0][0] == pytest.approx(got.dist[i], abs=1e-12)
        assert alone[2][0] == pytest.approx(got.radial[i], abs=1e-12)
        assert alone[3][0] == pytest.approx(got.axial[i], abs=1e-12)


def test_nearest_annotation_gives_the_same_answer_at_any_block_size(monkeypatch):
    """The memory budget sets how many points a block holds, never an answer: a
    budget of one point and a budget of everything give all four fields exactly."""
    from sbfsem.geometry import meshdiff

    pts, polygons = _outline_stack(count=15, points=80)
    pts = pts[:900]
    centers, radii = _heavy_tailed_discs(seed=8, count=400)
    whole = meshdiff.nearest_annotation(pts, centers, radii, polygons)
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)
    one = meshdiff.nearest_annotation(pts, centers, radii, polygons)
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 10 ** 12)
    every = meshdiff.nearest_annotation(pts, centers, radii, polygons)
    for variant in (one, every):
        for field, want in zip(variant, whole, strict=True):
            np.testing.assert_array_equal(field, want)


def test_nearest_annotation_with_nothing_to_measure():
    from sbfsem.geometry.meshdiff import nearest_annotation

    pts = np.random.default_rng(0).uniform(0, 1, (5, 3))
    none = nearest_annotation(pts, np.zeros((0, 3)), np.zeros(0))
    assert np.isinf(none.dist).all() and (none.owner == -1).all()
    nothing = nearest_annotation(np.zeros((0, 3)), np.ones((3, 3)), np.ones(3), [(np.ones((4, 2)), 0.0)])
    assert all(len(field) == 0 for field in nothing)
    # A ring too short to be a region is no annotation, but it keeps its place in the numbering.
    thin = nearest_annotation(pts, np.zeros((0, 3)), np.zeros(0),
                              [(np.ones((2, 2)), 0.0), (np.array([[0, 0], [1, 0], [1, 1.0]]), 0.5)])
    assert (thin.owner == 1).all()


def test_the_picture_and_the_numbers_share_one_kernel(monkeypatch):
    """deviation_map's vertex distances ARE the kernel's for those vertices, and
    annotation_deviation measures its surface samples with the same kernel."""
    from sbfsem.geometry import meshdiff
    from sbfsem.geometry.primitives import sphere as ball

    z = np.arange(-1.9, 1.91, 0.1)
    centers = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    radii = np.sqrt(4.0 - z ** 2)
    mesh = ball(2.3, divisions=40)
    shown = meshdiff.deviation_map(mesh, centers, radii)
    np.testing.assert_array_equal(shown.vertex_um,
                                  meshdiff.nearest_annotation(mesh.vertices, centers, radii).dist)
    heard = []
    real = meshdiff.nearest_annotation

    def spy(points, *args, **kwargs):
        heard.append((len(points), kwargs.get("stage")))
        return real(points, *args, **kwargs)

    monkeypatch.setattr(meshdiff, "nearest_annotation", spy)
    meshdiff.annotation_deviation(mesh, centers, radii, samples=2000)
    assert heard == [(2000, "samples")]


def test_the_combined_kernel_reports_stages_that_only_move_forward():
    """The disc stage counts passes over the points (``pass * n + block`` of
    ``passes * n``), the outline stage counts outlines; neither goes back, and
    each ends complete."""
    from sbfsem import progress
    from sbfsem.geometry import meshdiff

    pts, polygons = _outline_stack(count=12, points=60)
    pts = pts[:600]
    centers, radii = _heavy_tailed_discs(seed=3, count=300)
    heard: list[tuple] = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        meshdiff.nearest_annotation(pts, centers, radii, polygons, stage="vertices")
    stages = list(dict.fromkeys(s for s, *_ in heard))
    assert stages == ["vertices: distance to discs", "vertices: distance to outlines"]
    for stage in stages:
        counts = [(d, t) for s, d, t in heard if s == stage]
        assert [d for d, _ in counts] == sorted(d for d, _ in counts)
        assert len({t for _, t in counts}) == 1
        assert counts[-1][0] == counts[-1][1]
    classes = len({int(c) for c in np.digitize(radii, meshdiff.DISC_CLASS_EDGES)})
    assert [t for s, _, t in heard if s == stages[0]][0] == classes * len(pts)
    # Silent without a listener.
    quiet = meshdiff.nearest_annotation(pts, centers, radii, polygons)
    assert quiet.dist.shape == (len(pts),)


# -- how the error arises: the extra fields of the error map -------------------------


def _flat_grid(x0, x1, spacing, y1=1.0, z=0.0):
    """A flat patch [x0, x1] x [0, y1] in the plane z, in squares of ``spacing``."""
    xs = np.linspace(x0, x1, int(round((x1 - x0) / spacing)) + 1)
    ys = np.linspace(0.0, y1, int(round(y1 / spacing)) + 1)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    vertices = np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, z)])
    ny = len(ys)
    faces = []
    for i in range(len(xs) - 1):
        for j in range(ny - 1):
            a = i * ny + j
            faces += [[a, a + ny, a + ny + 1], [a, a + ny + 1, a + 1]]
    return Mesh(vertices, np.asarray(faces, dtype=np.int64))


def test_vertex_areas_add_up_to_the_surface_and_weigh_regions_by_area_not_by_vertices():
    from sbfsem.geometry.mesh import concatenate
    from sbfsem.geometry.meshdiff import vertex_areas

    coarse, fine = _flat_grid(0.0, 1.0, 0.5), _flat_grid(1.0, 2.0, 0.125)
    mesh = concatenate([coarse, fine])
    area = vertex_areas(mesh)
    assert area.sum() == pytest.approx(mesh.surface_area()) == pytest.approx(2.0)
    assert area[:coarse.n_vertices].sum() == pytest.approx(1.0)
    assert area[coarse.n_vertices:].sum() == pytest.approx(1.0)
    assert fine.n_vertices > 5 * coarse.n_vertices      # a patch of the same area, many more vertices
    # An unreferenced vertex stands for nothing.
    loose = Mesh(np.vstack([mesh.vertices, [[9.0, 9.0, 9.0]]]), mesh.faces)
    assert vertex_areas(loose)[-1] == 0.0
    assert vertex_areas(Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))).shape == (0,)


def test_area_weighted_statistics_differ_from_vertex_weighted_ones_by_the_density():
    """Error 1 on a coarse patch and 0 on an equal-area patch of many more
    vertices: by area half the surface is wrong (mean 0.5, 75th percentile 1);
    counting vertices says almost none of it is."""
    from sbfsem.geometry.mesh import concatenate
    from sbfsem.geometry.meshdiff import _surface_stats, vertex_areas, weighted_quantiles

    coarse, fine = _flat_grid(0.0, 1.0, 0.5), _flat_grid(1.0, 2.0, 0.125)
    mesh = concatenate([coarse, fine])
    error = np.r_[np.ones(coarse.n_vertices), np.zeros(fine.n_vertices)]
    area = vertex_areas(mesh)
    stats = _surface_stats(error, area, scale_um=1.0)
    assert stats.mean_um == pytest.approx(0.5)
    vertex_share = coarse.n_vertices / mesh.n_vertices
    assert vertex_share < 0.15 and error.mean() == pytest.approx(vertex_share)     # the count says 10%
    assert stats.quantiles_um[75] == pytest.approx(1.0) and stats.quantiles_um[0] == 0.0
    assert stats.quantiles_um[100] == 1.0 and len(stats.quantiles_um) == 101
    assert weighted_quantiles(error, np.ones_like(error), [0.75])[0] == 0.0         # by vertex: nothing
    assert stats.area_um2 == pytest.approx(2.0) and stats.excess_um3 == pytest.approx(1.0)
    # Histogram: all of the area is in the bin of 0 or the bin of 1; the edges reach 1.5 scales.
    assert stats.hist_edges_um[-1] == pytest.approx(1.5) and len(stats.hist_share) == 40
    assert stats.hist_share.sum() + stats.tail_share == pytest.approx(1.0)
    assert stats.hist_share[0] == pytest.approx(0.5) and stats.hist_share[26] == pytest.approx(0.5)
    assert stats.tail_share == 0.0
    assert _surface_stats(error, area, scale_um=0.5).tail_share == pytest.approx(0.5)   # the ones lie past 0.75


def test_weighted_quantiles_by_hand():
    from sbfsem.geometry.meshdiff import weighted_quantiles

    v = np.array([4.0, 1.0, 3.0, 2.0])
    # Equal weights: the plotting-position quantiles of 1, 2, 3, 4 at 1/8, 3/8, 5/8, 7/8.
    np.testing.assert_allclose(weighted_quantiles(v, np.ones(4), [0.0, 0.125, 0.5, 1.0]),
                               [1.0, 1.0, 2.5, 4.0])
    # The value 1 weighs three times the others: it owns the first half of the scale (centered at
    # 1/4), 2, 3 and 4 sit at 7/12, 9/12 and 11/12.
    w = np.array([1.0, 3.0, 1.0, 1.0])
    np.testing.assert_allclose(weighted_quantiles(v, w, [0.1, 0.25, 0.5, 0.75, 0.95]),
                               [1.0, 1.0, 1.0 + 0.75, 3.0, 4.0])
    # Zero weights are left out; no weight at all counts every value once; no values give zeros.
    assert weighted_quantiles(v, np.array([0.0, 1.0, 1.0, 1.0]), [0.0, 1.0]).tolist() == [1.0, 3.0]
    assert weighted_quantiles(v, np.zeros(4), [1.0])[0] == 4.0
    np.testing.assert_array_equal(weighted_quantiles([], [], [0.1, 0.9]), [0.0, 0.0])


def test_axial_and_sideways_error_on_a_cylinder_over_discs():
    """Discs of radius 1 at z = 0 and 2; a cylinder of radius 1.3 from z = 0 to
    2.6.  Its bottom ring is 0.3 too wide in the plane of a disc (all sideways,
    nothing along Z); its top ring is 0.3 too wide and 0.6 above the last disc;
    the center of its top lies 0.6 above the disc and inside it."""
    from sbfsem.geometry.meshdiff import deviation_map
    from sbfsem.geometry.primitives import cylinder

    mesh = cylinder(1.3, 2.6, divisions=32)
    centers = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 2.0]])
    found = deviation_map(mesh, centers, np.array([1.0, 1.0]))
    n = 32
    bottom, top = slice(0, n), slice(n, 2 * n)
    np.testing.assert_allclose(found.vertex_um[bottom], 0.3, atol=1e-12)
    np.testing.assert_allclose(found.axial_um[bottom], 0.0, atol=1e-12)
    np.testing.assert_allclose(found.axial_um[top], 0.6, atol=1e-6)
    np.testing.assert_allclose(found.vertex_um[top], np.hypot(0.3, 0.6), atol=1e-12)
    assert found.vertex_um[2 * n] == 0.0 and found.vertex_um[2 * n + 1] == pytest.approx(0.6)   # cap centers
    lateral = np.sqrt(np.maximum(found.vertex_um.astype(float) ** 2 - found.axial_um.astype(float) ** 2, 0.0))
    np.testing.assert_allclose(lateral[bottom], found.vertex_um[bottom], atol=1e-6)
    np.testing.assert_allclose(lateral[top], 0.3, atol=1e-6)
    # axial stays float64 beside vertex_um, so the sideways part never has a negative radicand
    assert found.axial_um.dtype == np.float64 and found.owner.dtype == np.int32
    assert (found.axial_um <= found.vertex_um).all()
    assert set(found.owner[bottom]) == {0} and set(found.owner[top]) == {1}
    assert found.floor_um == pytest.approx(1.0)              # annotated sections are 2 um apart: half of that


def test_rim_sides_are_enclosed_for_a_fat_render_and_outside_for_a_thin_one():
    """Annotations of a sphere of radius 2: a sphere of 2.5 encloses every rim
    point (the render is fatter, by 0.5), a sphere of 1.5 none (it stops short)."""
    from sbfsem.geometry.meshdiff import deviation_map
    from sbfsem.geometry.primitives import sphere as ball

    z = np.arange(-1.9, 1.91, 0.1)
    centers = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    radii = np.sqrt(4.0 - z ** 2)
    fat = deviation_map(ball(2.5, divisions=48), centers, radii)
    thin = deviation_map(ball(1.5, divisions=48), centers, radii)
    assert fat.rim_enclosed.dtype == bool and len(fat.rim_enclosed) == len(fat.rim_um)
    assert fat.rim_enclosed.all() and not thin.rim_enclosed.any()
    assert fat.bias.enclosed_share == 1.0 and fat.bias.outside_mean_um is None
    assert fat.bias.signed_mean_um == pytest.approx(0.5, abs=0.05)
    assert fat.bias.signed_mean_um == pytest.approx(fat.bias.enclosed_mean_um)
    assert thin.bias.enclosed_share == 0.0 and thin.bias.enclosed_mean_um is None
    assert thin.bias.signed_mean_um == pytest.approx(-0.5, abs=0.05)
    assert thin.bias.n_tested == len(thin.rim_um)
    assert (fat.annotations.rim_enclosed_share == 1.0).all() and (thin.annotations.rim_enclosed_share == 0.0).all()
    # Skipping the test leaves the sides unsaid, and nothing else changes.
    plain = deviation_map(ball(2.5, divisions=48), centers, radii, rim_sides=False)
    assert plain.rim_enclosed is None and plain.bias is None and np.isnan(plain.annotations.rim_enclosed_share).all()
    np.testing.assert_array_equal(plain.vertex_um, fat.vertex_um)
    np.testing.assert_array_equal(plain.rim_um, fat.rim_um)


def test_the_annotation_table_accounts_for_the_whole_surface():
    from sbfsem.geometry.meshdiff import annotation_deviation, deviation_map
    from sbfsem.geometry.primitives import sphere as ball

    z = np.arange(-1.9, 1.91, 0.1)
    centers = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    radii = np.sqrt(4.0 - z ** 2)
    ring = np.column_stack([np.cos(np.linspace(0, 2 * np.pi, 80, endpoint=False)),
                            np.sin(np.linspace(0, 2 * np.pi, 80, endpoint=False))])
    mesh = ball(2.5, divisions=48)
    found = deviation_map(mesh, centers, radii, [(1.5 * ring + [0.2, 0.0], 2.45)])
    table = found.annotations
    size = len(centers) + 1
    assert all(len(getattr(table, name)) == size for name in
               ("kind", "radius_um", "area_share", "surface_mean_um", "surface_max_um",
                "rim_mean_um", "rim_max_um", "rim_enclosed_share"))
    assert table.xyz_um.shape == (size, 3)
    assert table.area_share.sum() == pytest.approx(1.0)
    assert table.kind.tolist() == [0] * len(centers) + [1]
    np.testing.assert_allclose(table.xyz_um[-1], [0.2, 0.0, 2.45], atol=1e-12)
    assert table.radius_um[-1] == pytest.approx(1.5, rel=2e-3)        # a polygon of 80 sides of radius 1.5
    np.testing.assert_array_equal(table.radius_um[:len(centers)], radii)
    assert (table.surface_max_um >= table.surface_mean_um - 1e-12).all()
    assert (table.rim_max_um >= table.rim_mean_um - 1e-12).all()
    # Area share times mean error, over the annotations, is the surface's excess volume ...
    excess = (table.area_share * table.surface_mean_um).sum() * found.surface.area_um2
    assert excess == pytest.approx(found.surface.excess_um3, rel=1e-9)
    # ... which the sampled estimate of annotation_deviation reproduces within sampling noise.
    sampled = annotation_deviation(mesh, centers, radii, [(1.5 * ring + [0.2, 0.0], 2.45)])
    assert found.surface.excess_um3 == pytest.approx(sampled.excess_volume_um3, rel=0.05)
    assert found.surface.mean_um == pytest.approx(sampled.surface_um, rel=0.05)
    assert found.surface.area_um2 == pytest.approx(mesh.surface_area())


def test_the_z_profile_follows_the_cell():
    from sbfsem.geometry.meshdiff import deviation_map
    from sbfsem.geometry.primitives import cylinder

    mesh = cylinder(1.3, 2.6, divisions=32)
    found = deviation_map(mesh, np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 2.0]]), np.array([1.0, 1.0]))
    prof = found.z_profile
    assert prof.z0_um == 0.0 and prof.step_um == 1.0 and len(prof.area_um2) == 3
    assert prof.area_um2.sum() == pytest.approx(mesh.surface_area())
    assert prof.area_um2[1] == 0.0                              # nothing of the cylinder's surface has its vertices there
    from sbfsem.geometry.meshdiff import vertex_areas

    area, low = vertex_areas(mesh), mesh.vertices[:, 2] < 1.0
    assert prof.mean_um[0] == pytest.approx(np.average(found.vertex_um[low], weights=area[low]))
    assert prof.mean_um[2] == pytest.approx(np.average(found.vertex_um[~low], weights=area[~low]))
    assert prof.n_annotations.tolist() == [1, 0, 1]
    assert 0.6 - 1e-9 <= prof.p95_um[2] <= np.hypot(0.3, 0.6) + 1e-9 and prof.p95_um[1] == 0.0
    assert prof.rim_mean_um[0] > 0 and prof.rim_mean_um[2] > 0 and prof.rim_mean_um[1] == 0.0


def test_spots_are_the_worst_places_and_no_two_are_closer_than_the_radius():
    from sbfsem.geometry.meshdiff import SPOT_COUNT, SPOT_RADIUS_UM, _error_spots, vertex_areas

    mesh = _flat_grid(0.0, 30.0, 0.25, y1=30.0)
    xyz = mesh.vertices
    area = vertex_areas(mesh)
    hot = np.array([[5.0, 5.0], [25.0, 8.0], [14.0, 24.0], [6.0, 21.0]])
    weight = np.array([3.0, 2.0, 1.0, 0.5])
    error = 0.01 + sum(w * np.exp(-((xyz[:, 0] - x) ** 2 + (xyz[:, 1] - y) ** 2) / (2 * 0.8 ** 2))
                       for w, (x, y) in zip(weight, hot, strict=True))
    owner = (xyz[:, 0] > 15).astype(np.int32)
    spots = _error_spots(xyz, error, area, owner)
    assert 4 <= len(spots) <= SPOT_COUNT
    centers = np.array([s.xyz_um for s in spots])
    apart = np.linalg.norm(centers[:, None] - centers[None], axis=2)[np.triu_indices(len(spots), 1)]
    assert apart.min() >= SPOT_RADIUS_UM
    # The four hot places head the list, in order of how much they carry.
    for spot, (x, y) in zip(spots[:4], hot, strict=True):
        assert np.hypot(spot.xyz_um[0] - x, spot.xyz_um[1] - y) < 1.0
    assert [s.excess_um3 for s in spots] == sorted((s.excess_um3 for s in spots), reverse=True)
    assert sum(s.share for s in spots) <= 1.0 + 1e-9 and spots[0].share > 0.3
    assert spots[0].max_um > spots[0].mean_um > 0 and spots[0].owner == 0 and spots[1].owner == 1
    # What the first spot holds is what its disc of the surface adds up to.
    near = np.hypot(xyz[:, 0] - spots[0].xyz_um[0], xyz[:, 1] - spots[0].xyz_um[1]) <= SPOT_RADIUS_UM
    assert spots[0].excess_um3 == pytest.approx((area * error)[near].sum())
    assert _error_spots(xyz, np.zeros_like(error), area, owner) == ()
    assert _error_spots(xyz[:0], error[:0], area[:0], owner[:0]) == ()


def test_the_map_names_its_new_stages():
    from sbfsem import progress
    from sbfsem.geometry.meshdiff import deviation_map
    from sbfsem.geometry.primitives import cylinder

    heard: list[tuple] = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        deviation_map(cylinder(1.3, 2.6, divisions=32), np.array([[0.0, 0.0, 0.0]]), np.array([1.0]))
    stages = list(dict.fromkeys(s for s, *_ in heard))
    assert stages.index("rims: which side of the surface") < stages.index("summarizing the error")
    assert stages[0] == "vertices: distance to discs"


def test_the_enclosure_test_follows_the_memory_budget_and_never_changes_an_answer(monkeypatch):
    """Rim points of a cell that is long in Z meet tens of thousands of triangles
    in one square of the section plane; a block of 512 points against all of them
    asked for 1.3 GB on RPC1 c2628.  The block follows the budget now: at a
    budget of one byte it is one point, and the answers are the same at any size."""
    import tracemalloc

    from sbfsem.geometry import meshdiff
    from sbfsem.geometry.mesh import concatenate
    from sbfsem.geometry.primitives import cylinder, sphere

    union = concatenate([sphere(2.0, divisions=24), sphere(0.5, divisions=16)])
    pts = np.random.default_rng(3).uniform(-2.5, 2.5, (1500, 3))
    whole = meshdiff.enclosed_points(pts, union)
    assert whole.any() and not whole.all()
    blocks: list[int] = []
    real = np.einsum

    def spy(subscripts, *operands, **kwargs):
        if subscripts == "ijk,jk->ij":
            blocks.append(len(operands[0]))
        return real(subscripts, *operands, **kwargs)

    monkeypatch.setattr(meshdiff.np, "einsum", spy)
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)
    one = meshdiff.enclosed_points(pts, union)
    assert blocks and set(blocks) == {1}
    np.testing.assert_array_equal(one, whole)
    monkeypatch.undo()

    # A tall stack of short closed bodies over one spot: 300 x 128 triangles in the same cells.
    stack = concatenate([cylinder(0.4, 0.5, divisions=32).translate((0, 0, 0.6 * k)) for k in range(300)])
    inside = np.random.default_rng(4).uniform(-0.25, 0.25, (200, 3)) * [1, 1, 0] + [0, 0, 0.25]
    inside[:, 2] += 0.6 * np.random.default_rng(5).integers(0, 300, 200)
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 24 * 1024 * 1024)
    tracemalloc.start()
    try:
        held = meshdiff.enclosed_points(inside, stack)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert held.all()
    assert peak < 6 * 24 * 1024 * 1024, peak / 1e6


# -- the sign of a difference: by normal (the default) and by enclosure --------------


def _reference_difference(mesh_a, mesh_b, *, samples, seed=0):
    """``mesh_difference`` as it was written before ``sign`` existed (2026-10-15),
    kept here as the definition of "the default has not changed"."""
    from scipy.spatial import cKDTree

    points, normals = sample_surface(mesh_b, samples, seed=seed)
    distance, index = cKDTree(points).query(mesh_a.vertices, workers=-1)
    offset = mesh_a.vertices - points[index]
    sign = np.sign(np.einsum("ij,ij->i", offset, normals[index]))
    sign[sign == 0] = 1.0
    signed = distance * sign
    magnitude = np.abs(signed)
    return {
        "distance_um": signed,
        "mean_abs_um": float(magnitude.mean()),
        "rms_um": float(np.sqrt(np.mean(signed**2))),
        "p95_abs_um": float(np.percentile(magnitude, 95)),
        "max_abs_um": float(magnitude.max()),
        "mean_signed_um": float(signed.mean()),
        "scale_um": float(max(np.percentile(magnitude, 98), 1e-6)),
        "sample_spacing_um": 0.5 * float(np.sqrt(mesh_b.surface_area() / len(points))),
        "n_samples": int(len(points)),
    }


def _buried_piece():
    """B: a ball with a small one wholly inside it (what a body assembled from
    interpenetrating pieces looks like to the nearest-sample normal).  A: a
    sphere between the two, inside the big ball, outside the small one."""
    from sbfsem.geometry.mesh import concatenate
    from sbfsem.geometry.primitives import sphere as ball

    union = concatenate([ball(2.0, divisions=24), ball(0.5, divisions=16)])
    return ball(1.0, divisions=24), union


def test_the_default_sign_and_every_number_of_it_are_what_they_were():
    a, b = sphere(6.0), sphere(5.0)
    reference = _reference_difference(a, b, samples=20_000)
    diff = mesh_difference(a, b, samples=20_000)
    assert diff.sign_by == "normal"
    for name, value in reference.items():
        if name == "distance_um":
            np.testing.assert_array_equal(diff.distance_um, value)
        else:
            assert getattr(diff, name) == value, name
    # ... and naming the default changes nothing.
    named = mesh_difference(a, b, samples=20_000, sign="normal")
    np.testing.assert_array_equal(named.distance_um, diff.distance_um)
    assert diff.distance_b_um is None and diff.stats_area_b is None      # only when asked


def test_an_unknown_sign_is_refused():
    with pytest.raises(ValueError, match="sign must be one of"):
        mesh_difference(sphere(6.0), sphere(5.0), sign="outward")


def test_by_the_normal_a_piece_buried_in_b_gets_the_sign_wrong_and_by_enclosure_it_does_not():
    """The failure measured on Inferior c21768 (21.8 % of the Voxel remesh's
    vertices against the original), reproduced with spheres.  Every vertex of A
    is inside B's big ball, so every distance should be negative; the nearest B
    sample is on the buried ball, whose outward normal points away from A."""
    a, union = _buried_piece()
    by_normal = mesh_difference(a, union, samples=20_000)
    by_enclosure = mesh_difference(a, union, samples=20_000, sign="enclosure")
    assert (by_normal.distance_um > 0).all(), "the normal's sign is wrong here: that is the bug"
    assert (by_enclosure.distance_um < 0).all(), "inside the union is negative"
    # Only the sign differs.
    np.testing.assert_array_equal(np.abs(by_enclosure.distance_um), np.abs(by_normal.distance_um))
    assert by_enclosure.sign_by == "enclosure" and by_enclosure.mean_signed_um < -0.4
    # A vertex outside every piece is positive by either rule.
    from sbfsem.geometry.primitives import sphere as ball

    outside = mesh_difference(ball(3.0, divisions=24), union, samples=20_000, sign="enclosure")
    assert (outside.distance_um > 0).all() and outside.mean_signed_um == pytest.approx(1.0, abs=0.1)


def test_where_the_normal_is_right_the_two_signs_agree():
    """Concentric spheres, both ways round: the sign of the enclosure test is the
    sign of the normal, to the digit."""
    outer, inner = sphere(6.0), sphere(5.0)
    for a, b, expected in ((outer, inner, 1.0), (inner, outer, -1.0)):
        by_normal = mesh_difference(a, b, samples=20_000)
        by_enclosure = mesh_difference(a, b, samples=20_000, sign="enclosure")
        np.testing.assert_array_equal(by_enclosure.distance_um, by_normal.distance_um)
        assert by_enclosure.mean_signed_um == pytest.approx(expected, abs=0.1)


def test_identical_meshes_are_zero_by_either_sign():
    ball = sphere(5.0)
    for sign in ("normal", "enclosure"):
        diff = mesh_difference(ball, ball, samples=20_000, sign=sign)
        assert diff.max_abs_um == pytest.approx(0.0, abs=1e-9)
        assert diff.stats_area is not None
        assert diff.stats_area.max_abs_um == pytest.approx(0.0, abs=1e-9)
        assert diff.stats_area.mean_signed_um == pytest.approx(0.0, abs=1e-9)
        assert not np.signbit(diff.distance_um).any() or sign == "normal"      # no -0.0 from the enclosure


def test_both_ways_gives_the_reverse_field_by_the_same_rule():
    a, union = _buried_piece()
    both = mesh_difference(a, union, samples=20_000, sign="enclosure", both=True)
    reverse = mesh_difference(union, a, samples=20_000, sign="enclosure")
    np.testing.assert_array_equal(both.distance_b_um, reverse.distance_um)
    assert len(both.distance_b_um) == union.n_vertices
    assert both.stats_area_b == reverse.stats_area
    # The forward half is what it is without the reverse.
    np.testing.assert_array_equal(
        both.distance_um, mesh_difference(a, union, samples=20_000, sign="enclosure").distance_um)
    # B's big ball lies outside A, its small one inside: both signs occur.
    assert (both.distance_b_um > 0).any() and (both.distance_b_um < 0).any()


def test_the_area_statistics_weigh_the_surface_and_not_the_vertices():
    """A: a coarse patch one unit above the plane B and a fine patch of the same area
    lying in it.  Half the surface of A is 1 off; the vertex count says a tenth."""
    from sbfsem.geometry.mesh import concatenate

    coarse, fine = _flat_grid(0.0, 1.0, 0.5, z=1.0), _flat_grid(1.0, 2.0, 0.125)
    a = concatenate([coarse, fine])
    b = _flat_grid(0.0, 2.0, 0.125)
    diff = mesh_difference(a, b, samples=20_000)
    share = coarse.n_vertices / a.n_vertices
    assert share < 0.11
    assert diff.mean_abs_um == pytest.approx(share, abs=0.01)             # by vertex: a tenth
    stats = diff.stats_area
    assert stats.mean_abs_um == pytest.approx(0.5, abs=0.01)              # by area: half
    assert stats.mean_signed_um == pytest.approx(0.5, abs=0.01)
    assert stats.rms_um == pytest.approx(np.sqrt(0.5), abs=0.01)
    assert stats.p95_abs_um == pytest.approx(1.0, abs=0.01) and stats.p98_abs_um == pytest.approx(1.0, abs=0.01)
    assert stats.max_abs_um == pytest.approx(1.0, abs=0.01)
    # An unreferenced vertex neither weighs anything nor sets the maximum.
    loose = Mesh(np.vstack([a.vertices, [[0.5, 0.5, 40.0]]]), a.faces)
    assert mesh_difference(loose, b, samples=20_000).stats_area.max_abs_um == pytest.approx(1.0, abs=0.01)
    assert diff.max_abs_um == pytest.approx(1.0, abs=0.01)


def test_the_enclosure_sign_reports_its_stage_and_obeys_the_memory_budget(monkeypatch):
    from sbfsem import progress
    from sbfsem.geometry import meshdiff

    a, union = _buried_piece()
    heard: list[tuple] = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        whole = mesh_difference(a, union, samples=5_000, sign="enclosure", both=True)
    stages = list(dict.fromkeys(s for s, *_ in heard))
    assert stages == ["difference: sampling mesh B", "difference: closing the open ends of mesh B",
                      "difference: which side of mesh B",
                      "difference: sampling mesh A", "difference: closing the open ends of mesh A",
                      "difference: which side of mesh A"]
    side = [(d, t) for s, d, t in heard if s == "difference: which side of mesh B"]
    assert [d for d, _ in side] == sorted(d for d, _ in side) and side[-1][0] == side[-1][1]
    # By normal there is no enclosure test to wait for.
    heard.clear()
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        mesh_difference(a, union, samples=5_000)
    assert [s for s, *_ in heard] == ["difference: sampling mesh B"]
    # A block of one point at a time (a budget of one byte) gives the same signs.
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)
    small = mesh_difference(a, union, samples=5_000, sign="enclosure", both=True)
    np.testing.assert_array_equal(small.distance_um, whole.distance_um)
    np.testing.assert_array_equal(small.distance_b_um, whole.distance_b_um)


def test_the_area_statistics_percentiles_by_hand():
    """The color scale the server suggests is the 98th percentile of |d| by area, and
    nothing else pins it: ``p95_abs_um`` and ``p98_abs_um`` must be told apart.  A ramp
    1 .. 100 over equal areas, then the same ramp whose top ten values stand for a tenth
    of the area each (so they hardly count)."""
    from sbfsem.geometry.meshdiff import _area_stats

    ramp = np.arange(1.0, 101.0)
    signed = np.where(np.arange(100) % 2 == 0, ramp, -ramp)          # |d| is the ramp, the sign alternates
    stats = _area_stats(signed, np.ones(100))
    # Each value sits at the middle of its share of the weight, (i + 0.5) / 100, so the 95th
    # percentile is halfway between 95 and 96 and the 98th halfway between 98 and 99.
    assert stats.p95_abs_um == pytest.approx(95.5)
    assert stats.p98_abs_um == pytest.approx(98.5)
    assert stats.max_abs_um == 100.0 and stats.mean_abs_um == pytest.approx(50.5)
    assert stats.mean_signed_um == pytest.approx(-0.5)               # 50 positive and 50 negative values
    assert stats.rms_um == pytest.approx(np.sqrt(np.sum(ramp**2) / 100.0))

    area = np.where(ramp > 90, 0.1, 1.0)                              # 90 + 10 x 0.1 = 91
    thin = _area_stats(signed, area)
    # Position (value - 0.5) / 91 for the first ninety, so 0.95 x 91 = 86.45 lies 0.95 past the
    # centre of 86, and 0.98 x 91 = 89.18 lies 0.68 past the centre of 89.
    assert thin.p95_abs_um == pytest.approx(86.95)
    assert thin.p98_abs_um == pytest.approx(89.68)
    assert thin.p98_abs_um < stats.p98_abs_um - 8.0, "the area, not the vertex count, sets the scale"
    assert thin.max_abs_um == 100.0
    assert thin.mean_abs_um == pytest.approx(np.sum(area * ramp) / 91.0)


# -- an open surface is closed before the enclosure test ---------------------------------


def _open_tube(height=4.0, z0=0.0, radius=1.0, divisions=24):
    """The side wall of a cylinder along Z, wound outward, open at both ends: what a
    branch of the Original pathway looks like where it stops."""
    from sbfsem.geometry.primitives import cylinder

    tube = cylinder(radius, height, divisions=divisions)
    side = tube.faces[(tube.faces < 2 * divisions).all(axis=1)]
    return Mesh(tube.vertices[:2 * divisions] + [0.0, 0.0, z0], side)


def _unmated_edges(mesh):
    """How many edges are used a different number of times in their two directions,
    vertices welded by position: counted from the definition, not with the code under test."""
    from collections import Counter

    ids: dict = {}
    welded = np.array([ids.setdefault(tuple(np.round(v / 1e-6).astype(np.int64)), len(ids))
                       for v in mesh.vertices])
    used: Counter = Counter()
    for a, b, c in welded[mesh.faces]:
        for u, w in ((a, b), (b, c), (c, a)):
            used[(u, w)] += 1
    return len({frozenset(edge) for edge, k in used.items() if k != used.get(edge[::-1], 0)})


def _inside_along(points, mesh, axis, test=None):
    """``enclosed_points`` (or ``test``, called as ``test(points, mesh)``, which returns the sides)
    with the ray cast along +x, +y or -z instead of +z: the points and the mesh turned by a
    proper rotation, so the faces keep their winding."""
    from sbfsem.geometry.meshdiff import enclosed_points

    test = test or enclosed_points
    if axis == "-z":
        flip = np.array([1.0, -1.0, -1.0])
        return test(points * flip, Mesh(mesh.vertices * flip, mesh.faces))
    cols = {"z": [0, 1, 2], "x": [1, 2, 0], "y": [2, 0, 1]}[axis]
    return test(points[:, cols], Mesh(mesh.vertices[:, cols], mesh.faces))


def test_a_closed_mesh_needs_no_caps():
    from sbfsem.geometry.meshdiff import close_open_boundaries
    from sbfsem.geometry.primitives import cylinder

    capped_cylinder = cylinder(1.0, 2.0, divisions=16)
    assert _unmated_edges(capped_cylinder) == 0
    assert close_open_boundaries(capped_cylinder) is capped_cylinder
    empty = Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=int))
    assert close_open_boundaries(empty) is empty


def test_the_open_ends_of_a_tube_are_capped_by_one_fan_each_and_nothing_else_changes():
    from sbfsem.geometry.meshdiff import close_open_boundaries

    n = 24
    tube = _open_tube(4.0, z0=1.0, divisions=n)
    assert _unmated_edges(tube) == 2 * n
    closed = close_open_boundaries(tube)
    assert _unmated_edges(closed) == 0
    assert closed.n_vertices == tube.n_vertices + 2 and len(closed.faces) == len(tube.faces) + 2 * n
    np.testing.assert_array_equal(closed.vertices[:tube.n_vertices], tube.vertices)
    np.testing.assert_array_equal(closed.faces[:len(tube.faces)], tube.faces)
    hubs = closed.vertices[tube.n_vertices:]
    np.testing.assert_allclose(hubs[np.argsort(hubs[:, 2])], [[0.0, 0.0, 1.0], [0.0, 0.0, 5.0]], atol=1e-12)
    # Wound outward at both ends, or the signed volume would not be the prism's: the side wall
    # carries two thirds of it and the cap at z = 5 a third more, minus the one at z = 1.
    prism = 0.5 * n * np.sin(2.0 * np.pi / n) * 4.0
    assert closed.volume() == pytest.approx(prism)
    # The open edge is a property of the mesh, not of how it was numbered: the same tube with
    # its vertices permuted gets the same caps.
    order = np.random.default_rng(1).permutation(tube.n_vertices)
    inverse = np.argsort(order)
    shuffled = Mesh(tube.vertices[order], inverse[tube.faces])
    assert close_open_boundaries(shuffled).volume() == pytest.approx(prism)


def test_pieces_that_meet_at_a_ring_are_closed_there_and_open_only_at_their_far_ends():
    from sbfsem.geometry.mesh import concatenate
    from sbfsem.geometry.meshdiff import close_open_boundaries

    n = 24
    stack = concatenate([_open_tube(2.0, z0=0.0, divisions=n), _open_tube(2.0, z0=2.0, divisions=n)])
    assert stack.n_vertices == 4 * n, "the shared ring is two sets of vertices"
    assert _unmated_edges(stack) == 2 * n
    closed = close_open_boundaries(stack)
    assert closed.n_vertices == stack.n_vertices + 2 and len(closed.faces) == len(stack.faces) + 2 * n
    assert _unmated_edges(closed) == 0
    # Two bodies on the same ring are capped twice: one fan per use of an edge.
    tube = _open_tube(divisions=n)
    doubled = close_open_boundaries(concatenate([tube, tube]))
    assert len(doubled.faces) == 2 * len(tube.faces) + 2 * (2 * n)
    assert _unmated_edges(doubled) == 0 and doubled.n_vertices == 2 * tube.n_vertices + 2


def test_the_ray_count_of_an_open_tube_needs_its_ends_closed_to_be_the_same_from_every_direction():
    """The bug: an upward ray from inside an open tube leaves through the open end, crosses
    nothing, and the point reads as outside; a ray along x crosses the wall and reads inside."""
    from sbfsem.geometry.meshdiff import close_open_boundaries, enclosed_points

    tube = _open_tube(4.0, z0=1.0)                            # side wall, z from 1 to 5
    rng = np.random.default_rng(7)
    pts = np.column_stack([rng.uniform(-1.6, 1.6, 6000), rng.uniform(-1.6, 1.6, 6000),
                           rng.uniform(-1.0, 7.0, 6000)])
    radius = np.hypot(pts[:, 0], pts[:, 1])
    # Off the wall (the prism has a 24-gon's inradius of 0.9914) and off the planes of the caps.
    pts = pts[((radius < 0.9) | (radius > 1.1))
              & (np.abs(pts[:, 2] - 1.0) > 0.05) & (np.abs(pts[:, 2] - 5.0) > 0.05)]
    truth = (np.hypot(pts[:, 0], pts[:, 1]) < 1.0) & (pts[:, 2] > 1.0) & (pts[:, 2] < 5.0)
    assert 500 < truth.sum() < len(pts) - 500

    bare_up = enclosed_points(pts, tube)
    assert not bare_up.any(), "every upward ray from the lumen leaves through the open top"
    bare_x = _inside_along(pts, tube, "x")
    assert bare_x.any() and (bare_x != bare_up).sum() > 300, "and the open surface depends on the direction"

    closed = close_open_boundaries(tube)
    for axis in ("z", "x", "y", "-z"):
        np.testing.assert_array_equal(_inside_along(pts, closed, axis), truth, err_msg=axis)


def test_by_enclosure_a_vertex_in_the_lumen_of_an_open_tube_is_inside_it():
    """The sign of the enclosure rule against a reference that is open: every vertex of a ball
    held in an open tube is inside it (negative), one outside it is positive, and the
    magnitudes are the nearest-sample distances the normal rule gives."""
    from sbfsem.geometry.primitives import sphere as ball

    tube = _open_tube(4.0, z0=1.0)
    held = ball(0.5, center=(0.0, 0.0, 3.0), divisions=16)
    away = ball(0.5, center=(3.0, 0.0, 3.0), divisions=16)
    by_enclosure = mesh_difference(held, tube, samples=20_000, sign="enclosure")
    assert (by_enclosure.distance_um < 0).all(), "the upward ray leaves through the open end of the reference"
    np.testing.assert_array_equal(
        np.abs(by_enclosure.distance_um), np.abs(mesh_difference(held, tube, samples=20_000).distance_um))
    outside = mesh_difference(away, tube, samples=20_000, sign="enclosure")
    assert (outside.distance_um > 0).all()
    # The reverse field too: the tube's own vertices against the ball inside it are outside it.
    both = mesh_difference(held, tube, samples=20_000, sign="enclosure", both=True)
    assert (both.distance_b_um > 0).all()
    # A closed reference is untouched by all of this.
    closed = mesh_difference(ball(0.5, divisions=16), sphere(2.0), samples=20_000, sign="enclosure")
    assert (closed.distance_um < 0).all()


def test_the_reverse_pass_alone_adds_exactly_what_both_ways_computes():
    from sbfsem.geometry.meshdiff import add_reverse

    a, union = _buried_piece()
    for sign in ("normal", "enclosure"):
        forward = mesh_difference(a, union, samples=20_000, sign=sign)
        extended = add_reverse(forward, a, union, samples=20_000)
        both = mesh_difference(a, union, samples=20_000, sign=sign, both=True)
        assert forward.distance_b_um is None, "the frozen forward result is not touched"
        np.testing.assert_array_equal(extended.distance_b_um, both.distance_b_um)
        np.testing.assert_array_equal(extended.distance_um, both.distance_um)
        assert extended.stats_area_b == both.stats_area_b and extended.stats_area == both.stats_area
        assert extended.sign_by == sign


def test_the_reverse_pass_signs_by_the_rule_of_the_result_it_extends():
    """``add_reverse`` against values that do not come from ``mesh_difference``.  The reference of
    the reverse pass is the buried-piece body and the vertices measured against it are the ball
    between its two pieces: every one is inside the big ball (enclosure: negative) while the
    nearest sample of the body is on the buried piece, whose normal faces away from them (normal:
    positive, the bug the enclosure rule exists to avoid).  A reverse pass that used the wrong
    rule, or never took the rule of the result it extends, gets these signs wrong."""
    from sbfsem.geometry.meshdiff import _signed_distance, add_reverse

    ball_between, union = _buried_piece()
    for sign, expected in (("enclosure", -1.0), ("normal", 1.0)):
        forward = mesh_difference(union, ball_between, samples=20_000, sign=sign)
        extended = add_reverse(forward, union, ball_between, samples=20_000)
        reverse = extended.distance_b_um                   # the ball's vertices against the union
        assert reverse is not None and len(reverse) == ball_between.n_vertices
        assert (np.sign(reverse) == expected).all(), sign
        np.testing.assert_allclose(np.abs(reverse), 0.5, atol=0.05)     # 0.5 from the buried sphere, 1.0 from the outer
        assert extended.stats_area_b.mean_signed_um == pytest.approx(0.5 * expected, abs=0.05)
        # The same numbers a direct call gives, which does not go through mesh_difference.
        direct, _, _, _ = _signed_distance(ball_between, union, samples=20_000, seed=0, sign=sign, name="A")
        np.testing.assert_array_equal(reverse, direct)
        assert extended.sign_by == sign


# -- an open surface whose closing fan would be a sheet that is not there ----------------


def _slit_tube(radius=1.0, height=4.0, divisions=24, rows=100):
    """A closed cylinder along Z with a thin staircase slit cut through its wall, two quads wide,
    winding four times around from the bottom to the top.  The slit is one boundary piece 61 long
    whose fan to the centroid is 43 in area: a sheet through the middle of the tube that the
    surface does not have (roundness 0.15, where a ring is 0.97)."""
    angle = 2.0 * np.pi * np.arange(divisions) / divisions
    ring = np.column_stack([radius * np.cos(angle), radius * np.sin(angle), np.zeros(divisions)])
    heights = height * np.arange(rows + 1) / rows
    vertices = np.vstack([ring + [0.0, 0.0, z] for z in heights] + [[[0.0, 0.0, 0.0]], [[0.0, 0.0, height]]])
    bottom, top = (rows + 1) * divisions, (rows + 1) * divisions + 1
    cut = {(k, c % divisions) for k in range(rows) for c in (k, k + 1)}
    faces = []
    for k in range(rows):
        for i in range(divisions):
            if (k, i) in cut:
                continue
            a, b = k * divisions + i, k * divisions + (i + 1) % divisions
            c, d = (k + 1) * divisions + i, (k + 1) * divisions + (i + 1) % divisions
            faces += [[a, b, d], [a, d, c]]
    for i in range(divisions):
        j = (i + 1) % divisions
        faces += [[bottom, j, i], [top, rows * divisions + i, rows * divisions + j]]
    return Mesh(vertices, np.asarray(faces, dtype=np.int64))


def _slit_tube_points(height=4.0):
    """Points off the wall and off the caps of ``_slit_tube``, and the cylinder's own answer for them
    (a thin slit does not change what the solid is)."""
    rng = np.random.default_rng(3)
    pts = np.column_stack([rng.uniform(-1.6, 1.6, 6000), rng.uniform(-1.6, 1.6, 6000),
                           rng.uniform(-1.0, height + 1.0, 6000)])
    radius = np.hypot(pts[:, 0], pts[:, 1])
    pts = pts[((radius < 0.9) | (radius > 1.1)) & (np.abs(pts[:, 2]) > 0.05) & (np.abs(pts[:, 2] - height) > 0.05)]
    truth = (np.hypot(pts[:, 0], pts[:, 1]) < 1.0) & (pts[:, 2] > 0.0) & (pts[:, 2] < height)
    return pts, truth


def _winding_number(points, mesh, chunk=40):
    """The generalized winding number of a triangle mesh (Van Oosterom and Strackee): the share of the
    view the surface fills from each point, 1 inside a closed outward-wound surface and 0 outside it.
    Written out here by brute force, so that it shares nothing with the code under test."""
    tri = mesh.vertices[mesh.faces]
    out = np.zeros(len(points))
    for s in range(0, len(points), chunk):
        a = tri[None, :, 0, :] - points[s:s + chunk, None, :]
        b = tri[None, :, 1, :] - points[s:s + chunk, None, :]
        c = tri[None, :, 2, :] - points[s:s + chunk, None, :]
        la, lb, lc = (np.linalg.norm(v, axis=2) for v in (a, b, c))
        triple = np.einsum("ijk,ijk->ij", a, np.cross(b, c))
        denominator = (la * lb * lc + np.einsum("ijk,ijk->ij", a, b) * lc
                       + np.einsum("ijk,ijk->ij", b, c) * la + np.einsum("ijk,ijk->ij", c, a) * lb)
        out[s:s + chunk] = (2.0 * np.arctan2(triple, denominator)).sum(axis=1) / (4.0 * np.pi)
    return out


def test_a_fan_over_a_slit_is_a_sheet_that_is_not_there_and_a_fan_over_a_ring_is_a_cap():
    from sbfsem.geometry import meshdiff

    ring = _open_tube()
    caps = meshdiff._cap_open_boundaries(ring, meshdiff.WELD_UM, meshdiff.CAP_ROUNDNESS_MIN)
    assert caps.trusted == len(caps.mesh.faces) and len(caps.hubs) == 0, "both ends of the tube are round"
    slit = _slit_tube()
    caps = meshdiff._cap_open_boundaries(slit, meshdiff.WELD_UM, meshdiff.CAP_ROUNDNESS_MIN)
    assert caps.trusted == len(slit.faces), "no round piece: the slit is the only one"
    assert len(caps.hubs) == 1 and len(caps.mesh.faces) > len(slit.faces)
    # How round it is, from the fan and the boundary themselves.
    fan = caps.mesh.vertices[caps.mesh.faces[len(slit.faces):]]
    area = 0.5 * np.linalg.norm(np.cross(fan[:, 1] - fan[:, 0], fan[:, 2] - fan[:, 0]), axis=1).sum()
    length = np.linalg.norm(fan[:, 1] - fan[:, 0], axis=1).sum()
    assert 4.0 * np.pi * area / length**2 < meshdiff.CAP_ROUNDNESS_MIN / 1.5
    # The farthest boundary vertex from the centroid is the reach of the fan.
    boundary = np.unique(np.vstack([fan[:, 0], fan[:, 1]]), axis=0)
    assert caps.radii[0] == pytest.approx(np.linalg.norm(boundary - caps.hubs[0], axis=1).max())
    # A looser rule trusts the slit's fan, a stricter one distrusts the rings.
    assert meshdiff._cap_open_boundaries(slit, meshdiff.WELD_UM, 0.1).trusted > len(slit.faces)
    assert len(meshdiff._cap_open_boundaries(ring, meshdiff.WELD_UM, 1.5).hubs) == 2


def test_the_side_of_a_vertex_near_a_slit_is_the_winding_number_not_the_fan_and_not_a_ray():
    """The case the previous revision got wrong and no compact ring could show.  A thin slit winds
    through the wall of a closed cylinder.  The solid is still the cylinder: the points inside are
    inside by the cylinder's own geometry and by the winding number of the slit tube (0.86 to 0.93
    there).  A ray along +z never meets the wall and counts them right; a ray along +x slips
    through the slit; the one fan that closes the slit is a sheet through the tube and puts 5 % of
    its inside on the wrong side.  ``enclosed_points_open`` is right from every direction."""
    from sbfsem.geometry.meshdiff import (
        close_open_boundaries,
        enclosed_points,
        enclosed_points_open,
    )

    tube = _slit_tube()
    pts, truth = _slit_tube_points()
    assert 800 < truth.sum() < len(pts) - 800
    bare_up = enclosed_points(pts, tube)
    assert (bare_up == truth).all(), "the upward ray leaves through the top cap, which the slit does not reach"
    assert (_inside_along(pts, tube, "x") != truth).sum() > 30, "but a ray sideways slips through the slit"
    capped = enclosed_points(pts, close_open_boundaries(tube))
    assert (capped != truth).sum() > 30 and not capped[~truth].any(), \
        "the fan is inside the tube: points of the tube under it are signed outside"

    inside, overruled = enclosed_points_open(pts, tube)
    np.testing.assert_array_equal(inside, truth)
    assert overruled == (capped != truth).sum() > 30, "the winding number overruled exactly the fan's mistakes"
    for axis in ("x", "y", "-z"):
        np.testing.assert_array_equal(
            _inside_along(pts, tube, axis, test=lambda p, m: enclosed_points_open(p, m)[0]), truth, err_msg=axis)
    # The independent check: the winding number by brute force, on both sides of one half.
    sample = np.random.default_rng(5).choice(len(pts), 250, replace=False)
    winding = _winding_number(pts[sample], tube)
    assert winding[truth[sample]].min() > 0.8 and winding[~truth[sample]].max() < 0.1
    np.testing.assert_array_equal(inside[sample], winding > 0.5)


def test_enclosed_points_open_is_the_plain_count_where_nothing_is_open_and_the_capped_count_at_a_ring():
    from sbfsem.geometry.meshdiff import (
        close_open_boundaries,
        enclosed_points,
        enclosed_points_open,
    )

    a, union = _buried_piece()
    rng = np.random.default_rng(2)
    pts = rng.uniform(-2.6, 2.6, (3000, 3))
    inside, overruled = enclosed_points_open(pts, union)
    np.testing.assert_array_equal(inside, enclosed_points(pts, union))
    assert overruled == 0
    empty, none = enclosed_points_open(np.zeros((0, 3)), union)
    assert len(empty) == 0 and none == 0
    # An open tube: both ends are round, so the point is signed by the capped count, from every direction.
    tube = _open_tube(4.0, z0=1.0)
    pts = np.column_stack([rng.uniform(-1.6, 1.6, 4000), rng.uniform(-1.6, 1.6, 4000), rng.uniform(-1.0, 7.0, 4000)])
    radius = np.hypot(pts[:, 0], pts[:, 1])
    pts = pts[((radius < 0.9) | (radius > 1.1)) & (np.abs(pts[:, 2] - 1.0) > 0.05) & (np.abs(pts[:, 2] - 5.0) > 0.05)]
    truth = (np.hypot(pts[:, 0], pts[:, 1]) < 1.0) & (pts[:, 2] > 1.0) & (pts[:, 2] < 5.0)
    inside, overruled = enclosed_points_open(pts, tube)
    np.testing.assert_array_equal(inside, truth)
    np.testing.assert_array_equal(inside, enclosed_points(pts, close_open_boundaries(tube)))
    assert overruled == 0
    # Believing no fan at all puts every vertex by the winding number of the open tube, which is the same
    # answer here (0.51 and over inside it, 0.45 and under outside), and the winding number is the brute-force one.
    nothing_believed, changed = enclosed_points_open(pts, tube, roundness_min=float("inf"))
    np.testing.assert_array_equal(nothing_believed, truth)
    assert changed == 0
    sample = rng.choice(len(pts), 200, replace=False)
    np.testing.assert_array_equal(truth[sample], _winding_number(pts[sample], tube) > 0.5)
    for axis in ("x", "y", "-z"):
        np.testing.assert_array_equal(
            _inside_along(pts, tube, axis, test=lambda p, m: enclosed_points_open(p, m)[0]), truth, err_msg=axis)


def test_a_ray_count_split_over_two_sets_of_faces_adds_up_to_the_whole_count():
    from sbfsem.geometry import meshdiff
    from sbfsem.geometry.primitives import sphere as ball

    big, small = ball(2.0, divisions=24), ball(0.5, divisions=16)
    union = Mesh(np.vstack([big.vertices, small.vertices]),
                 np.vstack([big.faces, small.faces + big.n_vertices]))
    pts = np.random.default_rng(4).uniform(-2.5, 2.5, (2000, 3))
    radius = np.linalg.norm(pts, axis=1)
    keep = (np.abs(radius - 2.0) > 0.1) & (np.abs(radius - 0.5) > 0.1)          # off the two shells
    pts, radius = pts[keep], radius[keep]
    whole, nothing = meshdiff._crossing_counts(pts, union, len(union.faces))
    assert not nothing.any()
    np.testing.assert_array_equal(whole > 0, meshdiff.enclosed_points(pts, union))
    for split in (0, 1, len(big.faces) // 2, len(union.faces) - 1):
        first, rest = meshdiff._crossing_counts(pts, union, split)
        np.testing.assert_array_equal(first + rest, whole, err_msg=f"split {split}")
    # Split between the two balls: each count is that ball's own, 1 inside it and 0 outside.
    first, rest = meshdiff._crossing_counts(pts, union, len(big.faces))
    np.testing.assert_array_equal(first, (radius < 2.0).astype(float))
    np.testing.assert_array_equal(rest, (radius < 0.5).astype(float))


def test_the_solid_angle_of_a_triangle_a_sphere_and_the_budget(monkeypatch):
    from sbfsem import progress
    from sbfsem.geometry import meshdiff

    # One octant of the sphere about the origin, wound outward: an eighth of the view.
    octant = np.array([[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]])
    origin = np.zeros((1, 3))
    np.testing.assert_allclose(meshdiff._solid_angle_sums(origin, octant, stage="t"), [0.125], atol=1e-12)
    # Wound the other way round it is minus an eighth; far away it is the small-angle limit area / (4 pi r^2).
    np.testing.assert_allclose(meshdiff._solid_angle_sums(origin, octant[:, ::-1], stage="t"), [-0.125], atol=1e-12)
    unit = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]])      # facing +z, area one half
    np.testing.assert_allclose(meshdiff._solid_angle_sums(np.array([[0.0, 0.0, 1000.0]]), unit, stage="t"),
                               [-0.5 / (4.0 * np.pi * 1000.0**2)], rtol=1e-3)     # from the front: minus
    np.testing.assert_allclose(meshdiff._solid_angle_sums(np.array([[0.0, 0.0, -1000.0]]), unit, stage="t"),
                               [0.5 / (4.0 * np.pi * 1000.0**2)], rtol=1e-3)      # from behind: plus
    # A closed outward sphere: 1 inside, 0 outside, the same in any block size.
    ball = sphere(2.0)
    tri = ball.vertices[ball.faces]
    pts = np.array([[0.0, 0.0, 0.0], [0.3, -0.4, 0.5], [1.0, 1.0, 1.0], [3.0, 0.0, 0.0], [0.0, -5.0, 2.0]])
    whole = meshdiff._solid_angle_sums(pts, tri, stage="t")
    np.testing.assert_allclose(whole, [1.0, 1.0, 1.0, 0.0, 0.0], atol=1e-9)
    heard: list[tuple] = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        meshdiff._solid_angle_sums(pts, tri, stage="winding")
    assert heard and heard[-1][1] == heard[-1][2] and {s for s, *_ in heard} == {"winding"}
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)                    # one point at a time
    np.testing.assert_allclose(meshdiff._solid_angle_sums(pts, tri, stage="t"), whole, atol=1e-12)
    assert meshdiff._solid_angle_sums(pts[:0], tri, stage="t").shape == (0,)
    assert (meshdiff._solid_angle_sums(pts, tri[:0], stage="t") == 0).all()


def test_the_reach_of_a_fan_never_misses_a_vertex_it_could_change():
    from sbfsem.geometry import meshdiff

    slit = _slit_tube()
    caps = meshdiff._cap_open_boundaries(slit, meshdiff.WELD_UM, meshdiff.CAP_ROUNDNESS_MIN)
    pts, _ = _slit_tube_points()
    pts = np.vstack([pts, [[0.0, 0.0, 2.0], [50.0, 0.0, 2.0], [0.0, 0.0, 500.0]]])
    reach = meshdiff._fan_reach(pts, caps.hubs, caps.radii)
    fan = caps.mesh.vertices[caps.mesh.faces[caps.trusted:]]
    share = meshdiff._solid_angle_sums(pts, fan, stage="t")
    assert (np.abs(share) >= 0.5).any(), "the fixture has points the fan fills half the view of"
    assert (reach[np.abs(share) >= 0.5] >= 0.5).all(), "every vertex the fan could change is looked at"
    assert reach[-2] < 0.05 and reach[-1] < 1e-4, "far away the fan fills nothing"
    assert (reach >= 0.5).sum() < len(pts), "and not every vertex is"


def test_by_enclosure_a_vertex_under_a_slit_is_counted_as_overruled_and_a_closed_reference_has_none():
    from sbfsem.geometry.meshdiff import close_open_boundaries, enclosed_points
    from sbfsem.geometry.primitives import sphere as ball

    tube = _slit_tube()
    held = ball(0.5, center=(0.2, 0.1, 1.5), divisions=16)
    diff = mesh_difference(held, tube, samples=20_000, sign="enclosure", both=True)
    assert (diff.distance_um < 0).all(), "a ball inside a tube is inside it, slit or no slit"
    wrong_by_the_fan = int((~enclosed_points(held.vertices, close_open_boundaries(tube))).sum())
    assert wrong_by_the_fan > 5, "the fan would have put some of the ball outside"
    assert diff.sign_overruled == wrong_by_the_fan
    assert diff.sign_overruled_b == 0 and (diff.distance_b_um > 0).all(), "the tube's vertices against the ball: closed"
    # The magnitudes are the nearest-sample distances of the normal rule, whatever decides the sign.
    by_normal = mesh_difference(held, tube, samples=20_000)
    np.testing.assert_array_equal(np.abs(diff.distance_um), np.abs(by_normal.distance_um))
    # By the normal nothing is overruled; against a closed reference there is nothing to overrule.
    assert by_normal.sign_overruled == 0
    assert mesh_difference(held, sphere(2.0), samples=20_000, sign="enclosure").sign_overruled == 0
    # The reverse pass keeps its own count.
    swapped = mesh_difference(tube, held, samples=20_000, sign="enclosure", both=True)
    assert swapped.sign_overruled == 0 and swapped.sign_overruled_b == wrong_by_the_fan


def test_the_winding_number_stage_is_reported_only_when_a_slit_needs_it(monkeypatch):
    from sbfsem import progress
    from sbfsem.geometry import meshdiff
    from sbfsem.geometry.primitives import sphere as ball

    held = ball(0.5, center=(0.2, 0.1, 1.5), divisions=16)
    heard: list[tuple] = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        whole = mesh_difference(held, _slit_tube(), samples=5_000, sign="enclosure")
    stages = list(dict.fromkeys(s for s, *_ in heard))
    assert stages == ["difference: sampling mesh B", "difference: closing the open ends of mesh B",
                      "difference: which side of mesh B", "difference: which side of mesh B (open ends)"]
    last = [(d, t) for s, d, t in heard if s.endswith("(open ends)")][-1]
    assert last[0] == last[1]
    # A block of one point at a time (a budget of one byte) gives the same signs.
    monkeypatch.setattr(meshdiff, "KERNEL_BUDGET_BYTES", 1)
    small = mesh_difference(held, _slit_tube(), samples=5_000, sign="enclosure")
    np.testing.assert_array_equal(small.distance_um, whole.distance_um)
    assert small.sign_overruled == whole.sign_overruled
