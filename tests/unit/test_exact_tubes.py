"""Exact tubes °: a steep thin stretch drawn through its discs, not around them (2.9.0).

The user, 2026-10-17, chose to "fix tube inflation in the builder so every disc stays
exact".  A round cone ends in a ball a radius tall, so where a process crosses the
sections steeply its balls overlap across many sections and the union takes the outer
edge of every wobble.  ``DiscHull`` is the hull of two discs, whose slice at each of
their sections is the disc itself; ``hull_pairs`` says which pairs get one.  Off by
default: measured, it is closer to the annotations but adds a ring of creases at every
section (render_schema's help for ``exact_tube_deg`` has the numbers).
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from sbfsem.geometry.unify import DiscHull, hull_pairs, unified_mesh
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.facade import SbfsemService


def test_the_hull_is_each_disc_at_its_own_section():
    hull = DiscHull(np.array([1.0, 0.0, 0.07]), np.array([0.0, 0.0, 0.0]), 0.3, 0.5)
    # Ordered by Z: the lower disc first.
    assert hull.a[2] == 0.0 and hull.ra == 0.5 and hull.rb == 0.3
    rim = np.array([[0.5, 0.0, 0.0], [0.0, -0.5, 0.0], [1.3, 0.0, 0.07], [1.0, 0.3, 0.07]])
    assert np.allclose(hull.distance(rim), 0.0, atol=1e-12)
    # Halfway, the disc halfway between them.
    mid = np.array([[0.5 + 0.4, 0.0, 0.035]])
    assert hull.distance(mid)[0] == pytest.approx(0.0, abs=1e-12)
    # Nothing above the upper section or below the lower one: a round cone's end
    # ball would reach a radius past each.
    assert hull.distance(np.array([[1.0, 0.0, 0.07 + 0.2]]))[0] == pytest.approx(0.2)
    assert hull.distance(np.array([[0.0, 0.0, -0.1]]))[0] == pytest.approx(0.1)
    assert hull.distance(np.array([[0.1, 0.0, 0.02]]))[0] < 0


def test_which_pairs_are_drawn_as_hulls():
    z = np.arange(8) * 0.07
    steep = np.column_stack([0.02 * np.arange(8), np.zeros(8), z])
    radii = np.full(8, 0.3)
    assert hull_pairs(steep, radii, 35.0).all()
    assert not hull_pairs(steep, radii, 0.0).any(), "0 is off"
    # A process at 45 degrees is drawn right by its round cones.
    oblique = np.column_stack([z, np.zeros(8), z])
    assert not hull_pairs(oblique, radii, 35.0).any()
    # Two discs on one section are a link, and a step past the disc is a travelling stack.
    flat = steep.copy()
    flat[4, 2] = flat[3, 2]
    jump = steep.copy()
    jump[4:, 0] += 0.5
    assert not hull_pairs(flat, radii, 35.0)[3]
    assert not hull_pairs(jump, radii, 35.0)[3]


def _area_at(mesh, z: float) -> float:
    tm = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    section = tm.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    planar, _ = section.to_2D()
    return float(planar.area)


def test_a_steep_wobbly_stretch_keeps_its_discs_size():
    """The round cones' union is fatter than the discs it was drawn through; the hulls
    are the discs, within the grid's own error."""
    rng = np.random.default_rng(3)
    n = 40
    z = np.arange(n) * 0.07
    pts = np.column_stack([rng.normal(0, 0.1, n), rng.normal(0, 0.1, n), z])
    radii = 0.5 * (1 + rng.normal(0, 0.15, n))
    items = [("stretch", pts, radii)]
    cones = unified_mesh(items, voxel_um=0.03, max_raw_faces=3_000_000)
    hulls = unified_mesh(items, voxel_um=0.03, max_raw_faces=3_000_000, hull_tilt_deg=35.0)
    disc = []
    for i in range(10, 30):
        area = np.pi * radii[i] ** 2
        disc.append((_area_at(cones, z[i]) / area, _area_at(hulls, z[i]) / area))
    cone_ratio, hull_ratio = np.median(np.array(disc), axis=0)
    assert cone_ratio > 1.3
    assert hull_ratio == pytest.approx(1.0, abs=0.12)
    assert hulls.volume() < cones.volume()


def test_the_service_draws_exact_tubes_and_moves_no_annotation(i943_path):
    neuron = read_legacy_json(i943_path)
    before = neuron.nodes[["ID", "Xum", "Yum", "Zum", "Rum"]].copy()
    service = SbfsemService(offline=True)
    service.add_neuron(neuron)
    try:
        options = {"pathway": "voxel2", "voxel2_faces": 100_000}
        plain = service._build_mesh(neuron, "cylinder", 1, 1.0, 0.05, options)
        exact = service._build_mesh(neuron, "cylinder", 1, 1.0, 0.05,
                                    {**options, "exact_tube_deg": 50.0})
        assert not np.array_equal(exact.vertices, plain.vertices)
        assert exact.volume() < plain.volume()
        # Convolution sums kernels along the skeleton: it has no round cones to replace.
        conv = {"pathway": "convolution", "voxel2_faces": 100_000}
        a = service._build_mesh(neuron, "cylinder", 1, 1.0, 0.05, conv)
        b = service._build_mesh(neuron, "cylinder", 1, 1.0, 0.05, {**conv, "exact_tube_deg": 50.0})
        assert np.array_equal(a.vertices, b.vertices)
        assert before.equals(neuron.nodes[["ID", "Xum", "Yum", "Zum", "Rum"]])
    finally:
        service.close()
