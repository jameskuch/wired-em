"""Mesh operations: clipping, flattening, bounding boxes and mesh cleanup."""

from __future__ import annotations

import numpy as np
import pytest

from sbfsem.analysis.boundary import BoundarySurface, IPLBoundaries
from sbfsem.geometry.mesh import EMPTY_MESH, Mesh, concatenate
from sbfsem.geometry.meshops import (
    bounding_box,
    bounding_box_from_points,
    bounding_box_grid,
    bounding_box_size,
    clip_mesh,
    clip_mesh_by_stratification,
    clip_mesh_by_vertices,
    flatten_render,
    group_bounding_box,
)
from sbfsem.geometry.primitives import cuboid, sphere
from sbfsem.geometry.volume import resize_volume, smooth_volume, volume_to_mesh


@pytest.fixture
def box():
    return cuboid((2, 2, 2))


def _flat_boundaries(inl_z: float, gcl_z: float) -> IPLBoundaries:
    pts = np.linspace(-10, 10, 5)
    return IPLBoundaries(
        BoundarySurface("INL", "NeitzInferiorMonkey", np.zeros((3, 3)), pts, pts,
                        np.full((5, 5), inl_z)),
        BoundarySurface("GCL", "NeitzInferiorMonkey", np.zeros((3, 3)), pts, pts,
                        np.full((5, 5), gcl_z)),
    )


def test_clip_by_axis(box):
    top = clip_mesh(box, 0.0, keep_above=True)
    assert (top.vertices[:, 2] >= 0).all()
    bottom = clip_mesh(box, 0.0, keep_above=False)
    assert (bottom.vertices[:, 2] <= 0).all()
    left = clip_mesh(box, 0.0, keep_above=True, axis=0)
    assert (left.vertices[:, 0] >= 0).all()
    assert clip_mesh(EMPTY_MESH, 0.0).is_empty


def test_clip_by_vertices(box):
    """F089: MATLAB referenced `cutVerts`, a name from the file it was copied from."""
    clipped = clip_mesh_by_vertices(box, [0, 1])
    assert clipped.n_vertices == 6
    mask = np.zeros(box.n_vertices, dtype=bool)
    mask[:2] = True
    assert clip_mesh_by_vertices(box, mask).n_vertices == 6
    # Faces referencing a removed vertex go with it.
    assert len(clipped) < len(box)


def test_clip_by_stratification():
    mesh = Mesh(
        np.array([[0.0, 0, 0], [1, 0, 5], [0, 1, 10], [1, 1, 15]]),
        np.array([[0, 1, 2], [1, 2, 3]]),
    )
    boundaries = _flat_boundaries(inl_z=0.0, gcl_z=20.0)
    # depth = z / 20, so keeping >= 0.5 keeps z >= 10.
    kept = clip_mesh_by_stratification(mesh, boundaries, 0.5, keep_above=True)
    assert (kept.vertices[:, 2] >= 10).all()
    below = clip_mesh_by_stratification(mesh, boundaries, 0.5, keep_above=False)
    assert (below.vertices[:, 2] <= 10).all()
    assert clip_mesh_by_stratification(EMPTY_MESH, boundaries, 0.5).is_empty


def test_clip_by_stratification_drops_out_of_range_vertices():
    mesh = Mesh(
        np.array([[0.0, 0, 5], [1, 0, 5], [0, 1, 5], [500.0, 500.0, 5]]),
        np.array([[0, 1, 2], [1, 2, 3]]),
    )
    boundaries = _flat_boundaries(0.0, 20.0)
    kept = clip_mesh_by_stratification(mesh, boundaries, 0.0, keep_above=True)
    # The far vertex is outside the fitted surfaces, so its depth is NaN.
    assert kept.n_vertices == 3


def test_flatten_render():
    mesh = Mesh(
        np.array([[0.0, 0, 0], [1, 0, 10], [0, 1, 20]]), np.array([[0, 1, 2]])
    )
    flat = flatten_render(mesh, _flat_boundaries(0.0, 20.0))
    np.testing.assert_allclose(flat.vertices[:, 2], [0.0, 0.5, 1.0])
    np.testing.assert_allclose(flat.vertex_data, [0.0, 0.5, 1.0])
    # The original is untouched.
    np.testing.assert_allclose(mesh.vertices[:, 2], [0, 10, 20])


def test_bounding_boxes(c943):
    box = bounding_box(c943)
    assert box[0] < box[1] and box[2] < box[3]
    pixels = bounding_box(c943, micron=False)
    assert pixels[1] > box[1]
    w, h = bounding_box_size(box)
    assert w > 0 and h > 0
    X, Y = bounding_box_grid([0, 3, 0, 2])
    assert X.shape == (3, 4)
    np.testing.assert_array_equal(
        group_bounding_box([[0, 1, 0, 1], [-1, 0.5, 2, 3]]), [-1, 1, 0, 3]
    )


def test_bounding_box_includes_closed_curves(request):
    from sbfsem.io.legacy_json import read_legacy_json

    neuron = read_legacy_json(
        request.path.parents[1] / "fixtures" / "i90000_closedcurve.json"
    )
    box = bounding_box(neuron)
    # The outline reaches x = 14, well beyond the single node at x = 10.
    assert box[1] >= 13.9


def test_bounding_box_from_points_with_radii():
    xyz = np.array([[0.0, 0, 0], [10, 10, 0]])
    np.testing.assert_array_equal(
        bounding_box_from_points(xyz, [1.0, 2.0]), [-1, 12, -1, 12]
    )
    with pytest.raises(ValueError):
        bounding_box_from_points(xyz, [1.0])


# -- Mesh ------------------------------------------------------------------
def test_mesh_basics(box):
    assert not box.is_empty
    assert EMPTY_MESH.is_empty
    assert EMPTY_MESH.volume() == 0.0
    np.testing.assert_array_equal(EMPTY_MESH.bounds, np.zeros((2, 3)))
    copy = box.copy()
    copy.vertices[0] = 99
    assert box.vertices[0][0] != 99


def test_mesh_normals_and_areas(box):
    normals = box.face_normals()
    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0)
    assert box.face_areas().sum() == pytest.approx(24.0)


@pytest.mark.parametrize("chunk", [1, 777, 1 << 14, 1 << 18])
def test_area_and_volume_a_chunk_of_faces_at_a_time_are_the_same_to_the_bit(monkeypatch, chunk):
    """A finished render's area and volume (the mesh poll's header) are taken a chunk of
    faces at a time, so its corners are never all held at once; each face's number, and
    so each sum, is the one all the faces at once made."""
    from sbfsem.geometry import mesh as mesh_module

    rng = np.random.default_rng(11)
    vertices = rng.normal(scale=40.0, size=(30_000, 3)) + 1000.0
    faces = rng.integers(0, len(vertices), size=(300_001, 3))
    surface = Mesh(vertices, faces)
    corners = vertices[faces]
    areas = 0.5 * np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1)
    signed = np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2]))
    monkeypatch.setattr(mesh_module, "FACE_CHUNK", chunk)
    got = surface.face_areas()
    assert got.dtype == areas.dtype and np.array_equal(got.view(np.uint64), areas.view(np.uint64))
    assert surface.surface_area() == float(areas.sum())
    assert surface.volume() == float(abs(signed.sum() / 6.0))
    assert surface.area_and_volume() == (surface.surface_area(), surface.volume())
    empty = Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))
    assert empty.face_areas().shape == (0,) and empty.surface_area() == 0.0 and empty.volume() == 0.0
    assert empty.area_and_volume() == (0.0, 0.0)


def test_drop_nan():
    """F081: coincident annotations made the MATLAB mesher emit NaN vertices."""
    mesh = Mesh(
        np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [np.nan, 0, 0]]),
        np.array([[0, 1, 2], [0, 1, 3]]),
    )
    clean = mesh.drop_nan()
    assert clean.n_vertices == 3
    assert len(clean) == 1
    # A finite mesh is returned unchanged.
    assert cuboid((1, 1, 1)).drop_nan().n_vertices == 8


def test_merge_duplicate_vertices():
    mesh = Mesh(
        np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0.0, 0, 0]]),
        np.array([[0, 1, 2], [3, 1, 2]]),
    )
    merged = mesh.merge_duplicate_vertices()
    assert merged.n_vertices == 3
    # Both faces survive as the same triangle; welding vertices is not face dedup.
    assert len(merged) == 2
    # Degenerate faces (a repeated vertex) are dropped, though.
    degenerate = Mesh(
        np.array([[0.0, 0, 0], [1, 0, 0], [0.0, 0, 0]]), np.array([[0, 1, 2]])
    )
    assert len(degenerate.merge_duplicate_vertices()) == 0


def test_remove_unreferenced():
    mesh = Mesh(
        np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [9, 9, 9]]),
        np.array([[0, 1, 2]]),
    )
    assert mesh.remove_unreferenced().n_vertices == 3


def test_flip_and_fix_winding(box):
    flipped = box.flip_faces()
    np.testing.assert_array_equal(flipped.faces, box.faces[:, ::-1])
    inverted = Mesh(box.vertices, box.faces[:, ::-1])
    assert inverted.fix_winding().volume() == pytest.approx(box.volume())


def test_smooth_and_transform(box):
    assert box.smooth(0) is box
    smoothed = sphere(1.0, divisions=12).smooth(2)
    assert smoothed.n_vertices == sphere(1.0, divisions=12).n_vertices
    assert smoothed.volume() < sphere(1.0, divisions=12).volume()
    np.testing.assert_allclose(box.translate((5, 0, 0)).bounds[:, 0], [4, 6])
    np.testing.assert_allclose(box.scale((2, 1, 1)).bounds[:, 0], [-2, 2])


def test_decimate(box):
    assert box.decimate(1.0) is box
    with pytest.raises(ValueError):
        box.decimate(0)
    with pytest.raises(ValueError):
        box.decimate(1.5)


def test_decimate_actually_reduces():
    """Decimation must reduce the face count, not merely not increase it.

    The earlier assertion was ``<=``, which a no-op satisfies.  trimesh
    delegates to the optional ``fast_simplification`` package and the wrapper
    caught the resulting ImportError, so with that package absent the Decimate
    control silently returned the full mesh and every test still passed.
    """
    full = sphere(1.0, divisions=30)
    reduced = full.decimate(0.3)
    assert len(reduced) < len(full)
    # Within a face of the target: the backend cannot always split evenly.
    assert abs(len(reduced) - round(len(full) * 0.3)) <= 1
    # A decimated sphere is still a sphere, not a cloud of stray vertices.
    assert np.allclose(np.linalg.norm(reduced.vertices, axis=1), 1.0, atol=0.05)


def test_trimesh_roundtrip(box):
    assert Mesh.from_trimesh(box.to_trimesh()).n_vertices == box.n_vertices


def test_concatenate_vertex_data():
    a = Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]]), vertex_data=np.array([1.0, 2, 3]))
    merged = concatenate([a, a])
    assert merged.vertex_data.shape == (6,)
    b = Mesh(np.zeros((3, 3)), np.array([[0, 1, 2]]))
    assert concatenate([a, b]).vertex_data is None


# -- volume ----------------------------------------------------------------
def test_volume_helpers():
    volume = np.zeros((10, 10, 4))
    volume[3:7, 3:7, :] = 1.0
    assert smooth_volume(volume).max() <= 1.0
    assert resize_volume(volume, 1.0) is volume
    assert resize_volume(volume, 0.5).shape == (5, 5, 4)
    with pytest.raises(ValueError):
        resize_volume(volume, 2.0)


def test_volume_to_mesh_of_an_empty_volume():
    assert volume_to_mesh(np.zeros((5, 5, 5))).is_empty
    assert volume_to_mesh(np.zeros((0, 0, 0))).is_empty


def test_volume_to_mesh_is_closed_by_padding():
    """MATLAB needed a separate isocaps call where the object met the boundary."""
    volume = np.ones((6, 6, 6))
    mesh = volume_to_mesh(volume, do_smooth=False)
    assert not mesh.is_empty
    assert mesh.to_trimesh().is_watertight


def test_synapse_ellipsoid_aligns_with_its_axis():
    """The long semi-axis follows the annotation's LINESTRING direction."""
    from sbfsem.geometry.primitives import synapse_ellipsoids

    diagonal = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
    mesh = synapse_ellipsoids([[0, 0, 0]], [diagonal], [1.0], radius=0.1)
    # The furthest vertex from the center must lie along the requested axis.
    furthest = mesh.vertices[np.argmax(np.linalg.norm(mesh.vertices, axis=1))]
    unit = furthest / np.linalg.norm(furthest)
    assert abs(float(np.dot(unit, diagonal))) > 0.999
    assert np.linalg.norm(furthest) == pytest.approx(1.0, rel=1e-3)


def test_synapse_ellipsoid_without_an_axis_is_a_sphere():
    """A missing or degenerate LINESTRING must not collapse the marker."""
    from sbfsem.geometry.primitives import synapse_ellipsoids

    mesh = synapse_ellipsoids([[0, 0, 0]], [[0, 0, 0]], [5.0], radius=0.3)
    radii = np.linalg.norm(mesh.vertices, axis=1)
    assert np.allclose(radii, 0.3, atol=1e-9)


def test_taubin_preserves_volume_where_laplacian_collapses():
    """The whole point of Taubin: smoothing that does not shrink the mesh.

    matGeom's Laplacian moves every vertex all the way to its neighbours'
    average, which on a closed surface contracts it toward the centroid.
    """
    original = sphere(1.0, divisions=24)
    v0 = original.volume()
    # Ten passes: a clean sphere resists contraction far better than the tube
    # meshes this is really for, where five passes leave 16% of the volume.
    laplacian = original.smooth(10).volume() / v0
    taubin = original.taubin(10).volume() / v0
    assert laplacian < 0.65, f"Laplacian should collapse it, kept {laplacian:.3f}"
    assert 0.95 < taubin < 1.05, f"Taubin should hold its volume, kept {taubin:.3f}"


def test_taubin_still_smooths():
    """Volume preservation must not mean doing nothing."""
    rng = np.random.default_rng(0)
    s = sphere(1.0, divisions=24)
    noisy = Mesh(s.vertices + rng.normal(0, 0.02, s.vertices.shape), s.faces)

    def roughness(m):
        radii = np.linalg.norm(m.vertices, axis=1)
        return float(radii.std())

    assert roughness(noisy.taubin(5)) < roughness(noisy) * 0.7


def test_taubin_rejects_a_non_shrinking_pair():
    with pytest.raises(ValueError):
        sphere(1.0, divisions=8).taubin(1, lam=0.5, mu=-0.4)   # |mu| < lam
    with pytest.raises(ValueError):
        sphere(1.0, divisions=8).taubin(1, lam=0.5, mu=0.5)    # mu positive


def test_taubin_zero_iterations_is_a_no_op():
    s = sphere(1.0, divisions=8)
    assert s.taubin(0) is s
