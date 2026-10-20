"""Smooth surface µm: the finished surface smoothed, no annotation moved (2.9.0).

The user, 2026-10-17: smoothing the annotations a surface is drawn through
(Smooth discs, Smooth outlines) is not the same as smoothing the surface drawn
through them, and the second is what they had asked for.  ``smooth_surface`` is
the second alone: volume-preserving Taubin passes on the finished mesh, none where
a process is only a pixel or two across.
"""

from __future__ import annotations

import numpy as np
import pytest

from sbfsem.geometry.curve_average import owner_radius, smooth_surface, surface_passes
from sbfsem.geometry.mesh import Mesh
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.facade import SbfsemService


def _tube(radius: float, length: float, ring: int, rows: int) -> Mesh:
    """A closed tube along Z: ``rows`` rings of ``ring`` vertices, capped."""
    t = np.linspace(0, 2 * np.pi, ring, endpoint=False)
    z = np.linspace(0, length, rows)
    verts = [np.column_stack([radius * np.cos(t), radius * np.sin(t), np.full(ring, zz)]) for zz in z]
    v = np.vstack([*verts, [[0, 0, 0]], [[0, 0, length]]])
    faces = []
    for r in range(rows - 1):
        for i in range(ring):
            a, b = r * ring + i, r * ring + (i + 1) % ring
            c, d = a + ring, b + ring
            faces += [[a, b, d], [a, d, c]]
    bottom, top = len(v) - 2, len(v) - 1
    for i in range(ring):
        faces.append([bottom, (i + 1) % ring, i])
        faces.append([top, (rows - 1) * ring + i, (rows - 1) * ring + (i + 1) % ring])
    return Mesh(v, np.array(faces)).fix_winding()


def _wobbly(mesh: Mesh, amount: float, seed: int = 0) -> Mesh:
    rng = np.random.default_rng(seed)
    return Mesh(mesh.vertices + rng.normal(0, amount, mesh.vertices.shape), mesh.faces)


def test_off_is_the_mesh_itself():
    mesh = _tube(1.0, 5.0, 48, 60)
    assert smooth_surface(mesh, [[0, 0, 2.5]], [1.0], length_um=0.0) is mesh


def test_a_thick_surface_is_smoothed_and_keeps_its_volume():
    clean = _tube(1.0, 5.0, 64, 80)
    rough = _wobbly(clean, 0.02)
    discs = np.column_stack([np.zeros(11), np.zeros(11), np.linspace(0, 5, 11)])
    smooth = smooth_surface(rough, discs, np.ones(11), length_um=1.0)
    # Closer to the clean tube than the rough one was, and the same volume.
    # The middle of the tube: its ends are rounded toward the caps, as they should be.
    middle = slice(10 * 64, 70 * 64)

    def err(m: Mesh) -> float:
        return float(np.mean(np.abs(np.hypot(m.vertices[middle, 0], m.vertices[middle, 1]) - 1.0)))
    assert err(smooth) < 0.5 * err(rough)
    assert smooth.volume() == pytest.approx(rough.volume(), rel=0.01)
    assert len(smooth.faces) == len(rough.faces) and smooth.n_vertices == rough.n_vertices


def test_a_process_a_pixel_or_two_across_is_left_alone():
    """Under 1.6 mean edges in radius no vertex takes a pass (THIN_EDGES)."""
    thin = _wobbly(_tube(0.05, 2.0, 6, 40), 0.003)
    discs = np.column_stack([np.zeros(5), np.zeros(5), np.linspace(0, 2, 5)])
    edge = float(np.linalg.norm(thin.vertices[thin.faces[:, 0]] - thin.vertices[thin.faces[:, 1]],
                                axis=1).mean())
    assert 0.05 / edge < 1.6
    out = smooth_surface(thin, discs, np.full(5, 0.05), length_um=1.0)
    assert np.array_equal(out.vertices, thin.vertices)


def test_any_length_above_zero_takes_a_pass_where_the_process_is_thick():
    passes = surface_passes([0.05, 0.2, 1.0], length_um=0.05, edge_um=0.1)
    assert passes.tolist() == [0, 1, 1]
    assert surface_passes([1.0], length_um=0.0, edge_um=0.1).tolist() == [0]
    assert surface_passes([1.0], length_um=0.25, edge_um=0.1).tolist() == [3]
    assert surface_passes([1.0], length_um=10.0, edge_um=0.1).tolist() == [20]


def test_owner_radius_reads_the_nearest_disc_rim_or_outline():
    centers = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    radii = np.array([5.0, 0.3])
    square = np.array([[20, -1], [22, -1], [22, 1], [20, 1]], dtype=float)
    pts = np.array([[4.9, 0.0, 0.0],      # on the soma's rim: the soma, though the dendrite is nearer
                    [10.3, 0.0, 0.0],     # on the dendrite
                    [22.0, 0.0, 0.0]])    # on the traced outline
    r = owner_radius(pts, centers, radii, [(square, 0.0)])
    assert r[0] == 5.0 and r[1] == pytest.approx(0.3)
    assert r[2] == pytest.approx(np.sqrt(4.0 / np.pi))


def test_the_service_smooths_the_surface_and_moves_no_annotation(i943_path):
    neuron = read_legacy_json(i943_path)
    before = neuron.nodes[["ID", "Xum", "Yum", "Zum", "Rum"]].copy()
    service = SbfsemService(offline=True)
    service.add_neuron(neuron)
    try:
        options = {"pathway": "voxel2", "voxel2_faces": 100_000}
        plain = service._build_mesh(neuron, "cylinder", 1, 1.0, 0.05, options)
        smooth = service._build_mesh(neuron, "cylinder", 1, 1.0, 0.05,
                                     {**options, "surface_smooth_um": 0.5})
        assert smooth.n_vertices == plain.n_vertices
        assert not np.array_equal(smooth.vertices, plain.vertices)
        assert smooth.volume() == pytest.approx(plain.volume(), rel=0.02)
        after = neuron.nodes[["ID", "Xum", "Yum", "Zum", "Rum"]]
        assert before.equals(after), "the annotations are where they were drawn"
    finally:
        service.close()
