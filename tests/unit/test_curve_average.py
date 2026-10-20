"""Averaging out the staircase of a closed-curve volume.

``sbfsem.geometry.curve_average`` averages a cell's annotations along their chains of
links and smooths the contoured surface over the same length, each scaled to how thick
the annotation is.  Inferior c16137's soma comes out of the Closed curve method as a
stack of plates, and a dendrite that runs along the section plane (c1178) as a ribbon
with a row of teeth down its rim; these pin what the averaging does to synthetic chains
where the answer is known, that it is off until asked for, and what it must leave alone
(a chain's ends, a thin process, the volume).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sbfsem.geometry.closed_curve import (
    ClosedCurveAnnotation,
    annotations_from_discs,
    build_curve_volume,
    decode_labels,
    label_code,
)
from sbfsem.geometry.curve_average import (
    POLISH_BUDGET,
    POLISH_MAX_PASSES,
    average_disc_nodes,
    average_outlines,
    polish_curve_mesh,
    smoothing_passes,
)
from sbfsem.geometry.mesh import EMPTY_MESH, Mesh
from sbfsem.geometry.meshdiff import crease_density, dihedral_angles
from sbfsem.geometry.volume import curve_volume_to_mesh

DZ = 0.09
SCALE = (7.5, 7.5, 90.0)
THETA = np.linspace(0.0, 2.0 * np.pi, 96, endpoint=False)


def disc_chain(n=60, radius=0.5, noise=0.15, jitter=0.0, adv=0.0, seed=3, first_id=1,
               section0=500, x0=20.0, y0=20.0):
    """``n`` discs, one a section, each linked to the next: the radius jittering by
    ``noise`` (relative), the center by ``jitter`` um, drifting ``adv`` um a section."""
    rng = np.random.default_rng(seed)
    k = np.arange(n)
    table = pd.DataFrame({
        "ID": first_id + k, "Xum": x0 + adv * k + rng.normal(0, jitter, n),
        "Yum": y0 + rng.normal(0, jitter, n), "Z": section0 + k,
        "Rum": radius * (1 + noise * rng.normal(0, 1, n)),
    })
    table["Zum"] = table["Z"] * DZ
    table["TypeCode"] = 1
    ids = list(table["ID"])
    return table, list(zip(ids[:-1], ids[1:], strict=True))


def outline_stack(n=14, radius=3.0, noise=0.06, seed=5, first_id=900, section0=1000, cx=30.0,
                  cy=30.0):
    """``n`` traced outlines, one a section, each linked to the next: circles whose radius
    jitters by ``noise`` (relative) and whose center by 0.05 um."""
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        r = radius * (1 + noise * rng.normal())
        x, y = cx + 0.05 * rng.normal(), cy + 0.05 * rng.normal()
        out.append(ClosedCurveAnnotation(
            np.column_stack([x + r * np.cos(THETA), y + r * np.sin(THETA)]),
            z_section=section0 + k, z_um=(section0 + k) * DZ, location_id=first_id + k,
            registered=True))
    ids = [a.location_id for a in out]
    return out, list(zip(ids[:-1], ids[1:], strict=True))


def second_difference(values):
    return float(np.abs(np.diff(np.asarray(values, dtype=float), 2)).mean())


# -- the label byte ----------------------------------------------------------------------
def test_a_label_byte_names_the_radius_and_whether_an_outline_drew_it():
    radii = np.array([0.02, 0.05, 0.27, 1.0, 5.0, 12.0])
    codes = np.array([label_code(r, False) for r in radii], dtype=np.uint8)
    assert list(codes) == sorted(codes) and len(set(codes.tolist())) == len(radii)
    radius, is_path = decode_labels(codes)
    assert np.allclose(radius, radii, rtol=0.07) and not is_path.any()
    paths = np.array([label_code(r, True) for r in radii], dtype=np.uint8)
    assert decode_labels(paths)[1].all()
    assert np.allclose(decode_labels(paths)[0], radii, rtol=0.07)
    # Nothing drew it; and a radius off either end of the scale stays on it.
    assert decode_labels(np.zeros(2, dtype=np.uint8))[0].tolist() == [0.0, 0.0]
    assert label_code(0.0, False) == 1 and label_code(1e9, False) == 0x7F
    assert label_code(1e9, True) == 0x7F | 0x80


# -- averaging discs along their chains --------------------------------------------------
def test_averaging_a_chain_of_discs_takes_the_wobble_out_and_keeps_its_ends():
    nodes, links = disc_chain(radius=0.6, noise=0.15, jitter=0.03)
    before = nodes.copy()
    out = average_disc_nodes(nodes, links, 1.0)
    assert out is not nodes and nodes.equals(before), "the table handed in is never changed"
    assert second_difference(out["Rum"]) < 0.4 * second_difference(nodes["Rum"])
    assert second_difference(out["Xum"]) < 0.6 * second_difference(nodes["Xum"])
    # The sections are the discs' own, and a chain's first and last disc are as traced.
    for column in ("ID", "Z", "Zum", "TypeCode"):
        assert out[column].equals(nodes[column])
    assert out.iloc[[0, -1]][["Xum", "Yum", "Rum"]].equals(nodes.iloc[[0, -1]][["Xum", "Yum", "Rum"]])
    assert np.allclose(out["Rum"].mean(), nodes["Rum"].mean(), rtol=0.05), "no net swelling or shrinking"


def test_a_disc_is_averaged_over_a_multiple_of_its_own_radius():
    """The window is the strength times the disc's radius: a 3 um disc one section from the
    next is averaged over some thirty sections, a 0.1 um disc over one.  The same
    relative wobble is taken out of the big chain far better than the small one."""
    kept = {}
    for radius in (0.1, 3.0):
        nodes, links = disc_chain(n=120, radius=radius, noise=0.1, seed=11)
        out = average_disc_nodes(nodes, links, 0.5)
        kept[radius] = second_difference(out["Rum"]) / second_difference(nodes["Rum"])
    assert kept[3.0] < 0.15 and kept[0.1] > 0.5 and kept[3.0] < 0.3 * kept[0.1], kept


def test_a_stronger_average_takes_more_out():
    nodes, links = disc_chain(n=80, radius=0.5, noise=0.2, seed=2)
    wobble = [second_difference(average_disc_nodes(nodes, links, g)["Rum"]) for g in (0.25, 1.0, 3.0)]
    assert wobble[0] > wobble[1] > wobble[2]


def test_a_steady_drift_and_taper_are_not_averaged_away():
    """The fit is a quadratic, not a mean: a process that travels and tapers keeps both."""
    n = 80
    nodes = pd.DataFrame({"ID": 1 + np.arange(n), "Xum": 20.0 + 0.25 * np.arange(n), "Yum": 20.0,
                          "Z": 500 + np.arange(n), "Rum": 0.8 - 0.005 * np.arange(n)})
    nodes["Zum"] = nodes["Z"] * DZ
    nodes["TypeCode"] = 1
    links = list(zip(nodes["ID"][:-1], nodes["ID"][1:], strict=True))
    out = average_disc_nodes(nodes, links, 1.0)
    assert np.allclose(out[["Xum", "Yum", "Rum"]], nodes[["Xum", "Yum", "Rum"]], atol=1e-3)


def test_averaging_does_not_cross_a_jump_in_radius():
    """A soma is never averaged into the dendrite that leaves it: the link across a radius
    jump over the chain's ratio is a tie, not a step of the chain."""
    soma, soma_links = disc_chain(n=40, radius=5.0, noise=0.02, first_id=1, section0=500)
    dendrite, dendrite_links = disc_chain(n=40, radius=0.2, noise=0.05, first_id=101, section0=540,
                                          seed=9)
    nodes = pd.concat([soma, dendrite], ignore_index=True)
    links = soma_links + dendrite_links + [(40, 101)]
    out = average_disc_nodes(nodes, links, 1.0)
    assert out.loc[nodes["ID"] >= 101, "Rum"].mean() == pytest.approx(0.2, rel=0.05)
    assert out.loc[nodes["ID"] <= 40, "Rum"].mean() == pytest.approx(5.0, rel=0.05)


def test_no_strength_no_links_or_no_chain_of_three_is_no_averaging():
    nodes, links = disc_chain(n=20)
    assert average_disc_nodes(nodes, links, 0.0) is nodes
    assert average_disc_nodes(nodes, [], 1.0) is nodes
    assert average_disc_nodes(nodes, None, 1.0) is nodes
    short = nodes.iloc[:2]
    assert average_disc_nodes(short, links[:1], 1.0) is short
    # Two-disc chains among longer ones: those two are as traced.
    pairs = [(1, 2), (3, 4)]
    again = average_disc_nodes(nodes.iloc[:4], pairs, 1.0)
    assert again[["Xum", "Yum", "Rum"]].equals(nodes.iloc[:4][["Xum", "Yum", "Rum"]])
    with pytest.raises(ValueError, match="fit"):
        average_disc_nodes(nodes, links, 1.0, fit="cubic")


def test_averaged_radii_stay_within_a_multiple_of_the_traced_ones():
    nodes, links = disc_chain(n=60, radius=0.4, noise=0.5, seed=21)
    nodes["Rum"] = nodes["Rum"].clip(lower=0.05)
    out = average_disc_nodes(nodes, links, 3.0)
    ratio = out["Rum"] / nodes["Rum"]
    assert ratio.min() >= 0.25 - 1e-12 and ratio.max() <= 4.0 + 1e-12
    assert (out["Rum"] > 0).all()


def test_the_discs_outlines_are_averaged_when_asked_and_not_otherwise():
    nodes, links = disc_chain(n=40, radius=0.8, noise=0.15, jitter=0.03)
    plain = annotations_from_discs(nodes, links, volume_scale=SCALE)
    same = annotations_from_discs(nodes, links, volume_scale=SCALE, average=0.0)
    assert len(plain) == len(same) and all(
        np.array_equal(a.outline, b.outline) for a, b in zip(plain, same, strict=True))
    averaged = annotations_from_discs(nodes, links, volume_scale=SCALE, average=1.0)
    assert len(averaged) == len(plain), "the same discs and the same runs"
    assert any(not np.array_equal(a.outline, b.outline) for a, b in zip(plain, averaged, strict=True))
    # A disc says what it is and how thick: a circle of a disc, or a stretch of a link.
    assert all(not a.is_path and a.owner_um > 0 for a in averaged)
    disc = next(a for a in averaged if a.location_id == 7)
    assert disc.owner_um == pytest.approx(float(np.hypot(*(disc.outline[0] - disc.outline.mean(axis=0)))), rel=0.02)


# -- averaging traced outlines along their chains ----------------------------------------
def test_averaging_outlines_along_their_chain_smooths_the_stack_and_keeps_its_ends():
    outlines, links = outline_stack(n=40)
    out = average_outlines(outlines, links, 0.3, spacing_um=0.2)
    assert len(out) == len(outlines)
    assert out[0] is outlines[0] and out[-1] is outlines[-1], "a chain's ends are as traced"
    middle = out[1:-1]
    assert all(a is not b for a, b in zip(middle, outlines[1:-1], strict=True))
    assert all(a.registered and a.is_path and len(a.outline) >= 16 for a in middle)
    assert [a.location_id for a in out] == [a.location_id for a in outlines]
    assert [a.z_section for a in out] == [a.z_section for a in outlines]
    radius = [a.equivalent_radius() for a in outlines]
    smoothed = [a.equivalent_radius() for a in out]
    assert second_difference(smoothed) < 0.5 * second_difference(radius)
    assert np.mean(smoothed) == pytest.approx(np.mean(radius), rel=0.03)


def test_the_outlines_window_is_a_distance_in_microns():
    outlines, links = outline_stack(n=30, noise=0.08)
    raw = [a.equivalent_radius() for a in outlines]
    wobble = [second_difference([a.equivalent_radius() for a in average_outlines(
        outlines, links, sigma, spacing_um=0.2)]) for sigma in (0.05, 0.2, 0.6)]
    assert wobble[0] > wobble[1] > wobble[2]
    assert wobble[0] < second_difference(raw) * 1.01


def test_outlines_keep_their_cutouts_and_those_that_cannot_be_linked():
    outlines, links = outline_stack(n=8)
    hole = np.column_stack([30 + 0.5 * np.cos(THETA), 30 + 0.5 * np.sin(THETA)])
    outlines[3] = ClosedCurveAnnotation(outlines[3].outline, [hole], z_section=outlines[3].z_section,
                                        z_um=outlines[3].z_um, location_id=outlines[3].location_id,
                                        registered=True)
    stray = ClosedCurveAnnotation(outlines[4].outline, z_section=1004, z_um=1004 * DZ, registered=True)
    out = average_outlines([*outlines, stray], links, 0.3, spacing_um=0.2)
    assert out[3] is not outlines[3] and len(out[3].cutouts) == 1
    assert np.allclose(out[3].cutouts[0], hole)
    assert out[-1] is stray, "no Location ID, no chain"


def test_no_strength_no_links_or_too_few_outlines_is_no_averaging():
    outlines, links = outline_stack(n=6)
    assert all(a is b for a, b in zip(average_outlines(outlines, links, 0.0), outlines, strict=True))
    assert all(a is b for a, b in zip(average_outlines(outlines, [], 0.3), outlines, strict=True))
    assert all(a is b for a, b in zip(average_outlines(outlines[:2], links[:1], 0.3), outlines[:2], strict=True))
    with pytest.raises(ValueError, match="fit"):
        average_outlines(outlines, links, 0.3, fit="cubic")


# -- how many passes a vertex takes ------------------------------------------------------
def test_a_vertex_takes_passes_by_the_length_it_is_averaged_over():
    radius = np.array([0.0, 0.1, 0.27, 0.6, 5.0])
    discs = np.zeros(5, dtype=bool)
    passes = smoothing_passes(radius, discs, discs=1.0, paths_um=0.0, edge_um=0.1)
    assert passes[0] == 0, "nothing drew it"
    assert passes[1] == 0, "a process a pixel thick is not smoothed"
    assert 0 < passes[2] < passes[3] <= passes[4] == POLISH_MAX_PASSES
    assert passes.dtype == np.int64
    # Strength 0 is off; half the strength is a quarter of the passes, more or less.
    assert not smoothing_passes(radius, discs, discs=0.0, paths_um=0.4, edge_um=0.1).any()
    half = smoothing_passes(np.array([0.6]), np.zeros(1, dtype=bool), discs=0.5, paths_um=0.0, edge_um=0.1)
    full = smoothing_passes(np.array([0.6]), np.zeros(1, dtype=bool), discs=1.0, paths_um=0.0, edge_um=0.1)
    assert 0 < half[0] < full[0] and half[0] <= 0.35 * full[0] + 1


def test_a_traced_outlines_vertex_is_averaged_over_a_distance_and_a_discs_over_its_radius():
    radius = np.array([1.0, 1.0])
    path = np.array([True, False])
    passes = smoothing_passes(radius, path, discs=0.0, paths_um=0.3, edge_um=0.1)
    assert passes[0] > 0 and passes[1] == 0, "the disc strength does not act on an outline"
    passes = smoothing_passes(radius, path, discs=0.3, paths_um=0.0, edge_um=0.1)
    assert passes[0] == 0 and passes[1] > 0, "and the outline's does not act on a disc"
    # A coarser mesh needs fewer passes to reach the same length.
    fine = smoothing_passes(radius, path, discs=1.0, paths_um=1.0, edge_um=0.1)
    coarse = smoothing_passes(radius, path, discs=1.0, paths_um=1.0, edge_um=0.25)
    assert (coarse <= fine).all() and (coarse < fine).any()


# -- Taubin with a count for each vertex -------------------------------------------------
def noisy_sphere(seed=4):
    import trimesh

    sphere = trimesh.creation.icosphere(subdivisions=3)
    rng = np.random.default_rng(seed)
    vertices = np.asarray(sphere.vertices) * (1 + 0.02 * rng.normal(size=(len(sphere.vertices), 1)))
    return Mesh(vertices, np.asarray(sphere.faces))


def test_weighted_taubin_with_one_count_is_taubin():
    mesh = noisy_sphere()
    for k in (1, 4, 9):
        weighted = mesh.taubin_weighted(np.full(mesh.n_vertices, k))
        assert np.allclose(weighted.vertices, mesh.taubin(k).vertices, atol=1e-12)
    assert np.array_equal(weighted.faces, mesh.faces)


def test_a_vertex_with_no_passes_stays_and_the_rest_smooth():
    mesh = noisy_sphere()
    counts = np.where(np.arange(mesh.n_vertices) % 3 == 0, 0, 5)
    out = mesh.taubin_weighted(counts)
    still = counts == 0
    assert np.array_equal(out.vertices[still], mesh.vertices[still])
    assert not np.allclose(out.vertices[~still], mesh.vertices[~still])
    assert mesh.taubin_weighted(np.zeros(mesh.n_vertices, dtype=int)) is mesh
    assert EMPTY_MESH.taubin_weighted(np.zeros(0, dtype=int)) is EMPTY_MESH


def test_counts_are_a_vertex_stopping_where_it_is_and_still_counting_as_a_neighbor():
    """A vertex of count 5 among neighbors of count 2 ends where two passes and then three
    more, with those neighbors held where they stopped, put it."""
    mesh = noisy_sphere()
    counts = np.where(np.arange(mesh.n_vertices) % 2 == 0, 2, 5)
    together = mesh.taubin_weighted(counts)
    first = mesh.taubin_weighted(np.minimum(counts, 2))
    after = first.taubin_weighted(np.maximum(counts - 2, 0))
    assert np.allclose(together.vertices, after.vertices, atol=1e-12)


def rippled_strip(columns=240, rows=12, seed=2):
    """A long thin sheet of triangles (0.1 um edges) with noise in its height: a mesh whose
    slabs have something to cut."""
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(columns) * 0.1, np.arange(rows) * 0.1, indexing="ij")
    z = 0.02 * rng.normal(size=x.shape) + 0.05 * np.sin(x * 3.0)
    vertices = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    index = np.arange(columns * rows).reshape(columns, rows)
    a, b, c, d = index[:-1, :-1], index[1:, :-1], index[:-1, 1:], index[1:, 1:]
    faces = np.concatenate([np.column_stack([a.ravel(), b.ravel(), c.ravel()]),
                            np.column_stack([b.ravel(), d.ravel(), c.ravel()])])
    return Mesh(vertices, faces)


def test_a_mesh_smoothed_a_slab_at_a_time_comes_out_as_the_whole_mesh_does():
    """What a vertex's last pass reaches is in its slab's margin, so cutting the mesh up
    changes nothing -- only how much of it is in memory at once."""
    mesh = rippled_strip()
    rng = np.random.default_rng(7)
    counts = np.where(rng.random(mesh.n_vertices) < 0.2, 0, rng.integers(1, 7, mesh.n_vertices))
    whole = mesh.taubin_weighted(counts, slab_vertices=10 ** 9)
    for limit in (900, 400):
        slabbed = mesh.taubin_weighted(counts, slab_vertices=limit)
        assert not np.array_equal(slabbed.vertices, mesh.vertices)
        assert np.allclose(slabbed.vertices, whole.vertices, atol=1e-9), limit
    # With one count everywhere it is Taubin's.
    uniform = mesh.taubin_weighted(np.full(mesh.n_vertices, 4), slab_vertices=500)
    assert np.allclose(uniform.vertices, mesh.taubin(4).vertices, atol=1e-9)


def test_the_slabs_are_what_a_large_mesh_takes(monkeypatch):
    """A mesh over ``TAUBIN_SLAB_VERTICES`` is smoothed in slabs, a small one at once."""
    mesh = rippled_strip()
    seen = []
    real = Mesh._taubin_whole

    def spy(self, counts, lam, mu):
        seen.append(self.n_vertices)
        return real(self, counts, lam, mu)

    monkeypatch.setattr(Mesh, "_taubin_whole", spy)
    counts = np.full(mesh.n_vertices, 3)
    mesh.taubin_weighted(counts)
    assert seen == [mesh.n_vertices], "under the limit: the whole mesh at once"
    seen.clear()
    monkeypatch.setattr(Mesh, "TAUBIN_SLAB_VERTICES", 700)
    mesh.taubin_weighted(counts)
    assert len(seen) > 1 and max(seen) < mesh.n_vertices, "over it: slabs"
    # A short mesh whose margins cover all of it is not cut: one slab is the whole mesh.
    seen.clear()
    short = rippled_strip(columns=20)
    short.taubin_weighted(np.full(short.n_vertices, 6), slab_vertices=50)
    assert seen == [short.n_vertices]


def test_taubin_weighted_refuses_a_count_missing_or_an_inflating_pair():
    mesh = noisy_sphere()
    with pytest.raises(ValueError, match="one count"):
        mesh.taubin_weighted(np.ones(3, dtype=int))
    with pytest.raises(ValueError, match="mu < -lam"):
        mesh.taubin_weighted(np.ones(mesh.n_vertices, dtype=int), lam=0.5, mu=-0.4)


# -- creases -------------------------------------------------------------------------------
def test_crease_density_counts_the_edges_sharper_than_the_angle():
    import trimesh

    box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    mesh = Mesh(np.asarray(box.vertices), np.asarray(box.faces))
    angle, length = dihedral_angles(mesh)
    assert len(angle) == 18, "twelve face diagonals and the twelve cube edges, less the shared"
    assert np.isclose(angle.max(), 90.0) and np.isclose(length[angle > 45].sum(), 12.0)
    assert crease_density(mesh) == pytest.approx(12.0 / 6.0)
    assert crease_density(mesh, 95.0) == 0.0
    sphere = trimesh.creation.icosphere(subdivisions=3)
    assert crease_density(Mesh(np.asarray(sphere.vertices), np.asarray(sphere.faces))) == 0.0
    assert crease_density(EMPTY_MESH) == 0.0
    # An edge on an open boundary has one face and is no crease.
    one = Mesh(np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]), np.array([[0, 1, 2]]))
    assert len(dihedral_angles(one)[0]) == 0 and crease_density(one) == 0.0


# -- the raster and the surface ----------------------------------------------------------
def ribbon(n=80, radius=0.7, adv=0.25, pixel=0.1, keep_labels=True, noise=0.0, jitter=0.0):
    """A dendrite that runs along the section plane, a quarter of a micron a section: a ribbon,
    which with the wobble of a hand-traced one (``noise`` and ``jitter``) has a row of teeth
    down its rim."""
    nodes, links = disc_chain(n=n, radius=radius, noise=noise, jitter=jitter, adv=adv, seed=1)
    annotations = annotations_from_discs(nodes, links, volume_scale=SCALE)
    volume = build_curve_volume(annotations, volume_scale=SCALE, pixel_size_um=pixel,
                                links=None, bridge_um=0.0, keep_labels=keep_labels)
    return volume, curve_volume_to_mesh(volume)


def test_a_labelled_raster_is_the_same_raster_with_a_byte_a_voxel():
    plain, _ = ribbon(keep_labels=False)
    labelled, mesh = ribbon(keep_labels=True)
    assert plain.labels is None and labelled.labels.shape == labelled.tiles.shape
    assert np.array_equal(plain.keys, labelled.keys) and np.array_equal(plain.tiles, labelled.tiles)
    assert np.array_equal(labelled.labels > 0, labelled.tiles), "every voxel an annotation drew is labelled"
    radius, is_path = decode_labels(labelled.labels[labelled.tiles])
    assert not is_path.any() and np.allclose(radius, 0.7, rtol=0.07)
    with pytest.raises(ValueError, match="keep_labels"):
        plain.label_at(mesh.vertices)


def test_every_vertex_of_the_surface_knows_which_annotation_it_is_on():
    volume, mesh = ribbon()
    radius, is_path = decode_labels(volume.label_at(mesh.vertices))
    assert (radius > 0).all() and not is_path.any()
    assert np.allclose(radius, 0.7, rtol=0.07)
    far = volume.label_at(mesh.vertices + np.array([500.0, 0.0, 0.0]))
    assert not far.any(), "nothing drew the voxels there"
    assert len(volume.label_at(np.zeros((0, 3)))) == 0


def test_labels_looked_up_in_chunks_are_the_same(monkeypatch):
    import sbfsem.geometry.closed_curve as module

    volume, mesh = ribbon()
    whole = volume.label_at(mesh.vertices)
    monkeypatch.setattr(module, "LABEL_CHUNK", 1000)
    assert np.array_equal(volume.label_at(mesh.vertices), whole)


def test_a_traced_outline_and_a_disc_are_told_apart_in_the_raster():
    stack, links = outline_stack(n=6, radius=2.0)
    nodes, disc_links = disc_chain(n=6, radius=0.5, noise=0.0, x0=40.0, y0=30.0, first_id=50,
                                   section0=1000)
    annotations = stack + annotations_from_discs(nodes, disc_links, volume_scale=SCALE)
    volume = build_curve_volume(annotations, volume_scale=SCALE, pixel_size_um=0.1, links=links,
                                keep_labels=True)
    mesh = curve_volume_to_mesh(volume)
    radius, is_path = decode_labels(volume.label_at(mesh.vertices))
    left = mesh.vertices[:, 0] < 35.0
    assert is_path[left].all() and not is_path[~left].any()
    assert np.allclose(radius[left], 2.0, rtol=0.25) and np.allclose(radius[~left], 0.5, rtol=0.07)


def test_polishing_the_surface_takes_the_teeth_out_of_a_ribbon_and_holds_its_volume():
    """Measured: a 0.7 um ribbon traced with a tenth of radius wobble and 0.03 um of center
    wobble is 0.25 um of crease for every square micron (0.04 to 0.07 once polished, 5 to 8
    times fewer), and no vertex moves 70 nm."""
    volume, mesh = ribbon(noise=0.1, jitter=0.03)
    before = crease_density(mesh)
    assert before > 0.2, "the ribbon does have teeth"
    last = before
    for strength in (0.5, 1.0):
        polished = polish_curve_mesh(mesh, volume, discs=strength)
        assert np.array_equal(polished.faces, mesh.faces)
        moved = np.linalg.norm(polished.vertices - mesh.vertices, axis=1)
        assert moved.max() < 0.08, "a few hundredths of a micron"
        assert polished.volume() == pytest.approx(mesh.volume(), rel=0.015)
        assert crease_density(polished) < last, "a stronger average takes more out"
        last = crease_density(polished)
    assert last < 0.3 * before


def test_polishing_with_both_strengths_off_is_the_surface_as_it_was():
    volume, mesh = ribbon(noise=0.1, jitter=0.03)
    assert polish_curve_mesh(mesh, volume) is mesh
    assert polish_curve_mesh(mesh, volume, discs=0.0, paths_um=0.0) is mesh
    # A strength for outlines does nothing on a cell drawn with discs alone.
    assert np.array_equal(polish_curve_mesh(mesh, volume, paths_um=0.5).vertices, mesh.vertices)
    assert polish_curve_mesh(EMPTY_MESH, volume, discs=1.0) is EMPTY_MESH


def test_a_process_a_pixel_across_is_left_alone():
    """Taubin pulls the ring of six vertices round a 0.1 um tube at 0.1 um together (its
    radius 0.050 um after contouring, 0.012 after twenty passes): thin processes are
    not smoothed."""
    # Whether a 0.1 um tube survives a 0.1 um raster at all depends on where its grid falls
    # on it: the first offset that leaves a surface is the one to polish.
    for x0 in (20.0, 20.03, 20.05, 20.07):
        nodes, links = disc_chain(n=60, radius=0.1, noise=0.0, x0=x0)
        volume = build_curve_volume(annotations_from_discs(nodes, links, volume_scale=SCALE),
                                    volume_scale=SCALE, pixel_size_um=0.1, links=None,
                                    bridge_um=0.0, keep_labels=True)
        mesh = curve_volume_to_mesh(volume)
        if not mesh.is_empty:
            break
    assert not mesh.is_empty
    assert np.array_equal(polish_curve_mesh(mesh, volume, discs=3.0).vertices, mesh.vertices)


def test_a_polish_over_its_budget_takes_fewer_passes_everywhere(monkeypatch):
    import sbfsem.geometry.curve_average as module

    volume, mesh = ribbon(n=40, noise=0.1, jitter=0.03)
    seen = []
    real = Mesh.taubin_weighted

    def spy(self, passes, *args, **kwargs):
        seen.append(np.asarray(passes).copy())
        return real(self, passes, *args, **kwargs)

    monkeypatch.setattr(Mesh, "taubin_weighted", spy)
    polish_curve_mesh(mesh, volume, discs=1.0)
    full = seen[-1]
    assert full.sum() > 1000
    monkeypatch.setattr(module, "POLISH_BUDGET", int(full.sum() // 4))
    polish_curve_mesh(mesh, volume, discs=1.0)
    scaled = seen[-1]
    assert 0 < scaled.sum() <= full.sum() // 4 and scaled.max() <= full.max()
    assert POLISH_BUDGET >= 10_000_000


# -- the service ---------------------------------------------------------------------------
class Cell:
    """The parts of a neuron the closed-curve path reads: its traced outlines, its discs and
    links, and its volume's scale."""

    volume_scale = SCALE

    def __init__(self, geometries, nodes, edges):
        self.geometries, self.nodes, self.edges = geometries, nodes, edges

    def get_cell_nodes(self):
        return self.nodes


def mixed_cell():
    """A soma traced with 30 outlines (x 28 to 33) and a dendrite of 60 discs leaving it a
    long way to the right (x 40 to 56): the two parts never touch, so what the averaging does
    to one can be told from what it does to the other."""
    outlines, outline_links = outline_stack(n=30, radius=2.0, noise=0.08)
    geometries = pd.DataFrame({
        "ID": [a.location_id for a in outlines], "ParentID": 0,
        "Z": [a.z_section for a in outlines], "Zum": [a.z_um for a in outlines],
        "Curve": [[a.outline] for a in outlines], "Registered": True,
    })
    nodes, disc_links = disc_chain(n=60, radius=0.6, noise=0.12, jitter=0.03, adv=0.25, x0=40.0,
                                   y0=30.0, first_id=50, section0=1000)
    return Cell(geometries, nodes, pd.DataFrame(outline_links + disc_links, columns=["A", "B"]))


def same_points(a, b):
    a, b = np.asarray(a.vertices), np.asarray(b.vertices)
    return a.shape == b.shape and np.array_equal(a[np.lexsort(a.T)], b[np.lexsort(b.T)])


def test_each_strength_averages_its_own_annotations_and_leaves_the_others():
    from sbfsem.service.facade import _curve_outlines

    cell = mixed_cell()
    plain, _ = _curve_outlines(cell, discs=True)
    discs, _ = _curve_outlines(cell, discs=True, avg_discs=1.0)
    paths, _ = _curve_outlines(cell, discs=True, avg_paths=0.3, pixel_size_um=0.1)
    assert len(plain) == len(discs) == len(paths)

    def moved(after):
        return [not np.array_equal(a.outline, b.outline) for a, b in zip(plain, after, strict=True)]

    traced = [a.is_path for a in plain]
    # The discs' strength moves discs and no outline; the outlines' moves outlines and no disc.
    assert any(traced) and not all(traced)
    assert not any(m for m, t in zip(moved(discs), traced, strict=True) if t)
    assert any(m for m, t in zip(moved(discs), traced, strict=True) if not t)
    assert not any(m for m, t in zip(moved(paths), traced, strict=True) if not t)
    assert any(m for m, t in zip(moved(paths), traced, strict=True) if t)
    # A strength of 0 is the annotations as traced: the very same objects.
    again, _ = _curve_outlines(cell, discs=True, avg_discs=0.0, avg_paths=0.0)
    assert all(a.outline.shape == b.outline.shape and np.array_equal(a.outline, b.outline)
               for a, b in zip(plain, again, strict=True))


def test_the_surface_of_each_kind_of_annotation_is_averaged_by_its_own_strength():
    """One raster with a traced soma and a disc dendrite: a strength for discs moves the
    dendrite's vertices and not the soma's, a strength for outlines the reverse."""
    from sbfsem.service.facade import _curve_outlines

    cell = mixed_cell()
    annotations, how = _curve_outlines(cell, discs=True)
    volume = build_curve_volume(annotations, volume_scale=SCALE, pixel_size_um=0.1, keep_labels=True,
                                **how)
    mesh = curve_volume_to_mesh(volume)
    soma = mesh.vertices[:, 0] < 35.0
    assert soma.any() and (~soma).any()
    by_discs = polish_curve_mesh(mesh, volume, discs=1.0)
    by_paths = polish_curve_mesh(mesh, volume, paths_um=0.3)
    both = polish_curve_mesh(mesh, volume, discs=1.0, paths_um=0.3)
    for out, soma_moves, dendrite_moves in ((by_discs, False, True), (by_paths, True, False),
                                            (both, True, True)):
        assert (not np.array_equal(out.vertices[soma], mesh.vertices[soma])) is soma_moves
        assert (not np.array_equal(out.vertices[~soma], mesh.vertices[~soma])) is dendrite_moves
    assert np.array_equal(both.vertices[soma], by_paths.vertices[soma])
    assert np.array_equal(both.vertices[~soma], by_discs.vertices[~soma])
    assert crease_density(both) < crease_density(by_discs) < crease_density(mesh)
    assert crease_density(both) < crease_density(by_paths)


def test_the_option_reaches_the_build_by_every_way_in():
    from sbfsem.service.facade import SbfsemService

    service = SbfsemService(offline=True)
    cell = mixed_cell()
    direct = service._curve_mesh(cell, 0.1, avg_discs=1.0, avg_paths=0.3)
    built = service.build_mesh(cell, method="closedcurve", pixel_size_um=0.1,
                               curve_avg_discs=1.0, curve_avg_paths=0.3)
    assert same_points(built, direct)
    assert not same_points(service.build_mesh(cell, method="closedcurve", pixel_size_um=0.1), direct)
    with pytest.raises(ValueError, match="curve_avg_dics"):
        service.build_mesh(cell, method="closedcurve", curve_avg_dics=1.0)


def test_hybrid_averages_only_its_outlines(monkeypatch):
    """Hybrid draws no circles: its discs are the cylinder build's, so the discs' strength has
    nothing to act on there, and the outlines' does."""
    from sbfsem.service.facade import SbfsemService

    service = SbfsemService(offline=True)
    monkeypatch.setattr(service, "_cylinder_mesh", lambda *args, **kwargs: EMPTY_MESH)
    cell = mixed_cell()

    def hybrid(**options):
        return service._build_geometry(cell, "hybrid", 1, 1.0, 0.1, options)

    plain = hybrid()
    assert not plain.is_empty and plain.vertices[:, 0].max() < 35.0, "the outlines alone: no circles"
    assert same_points(hybrid(curve_avg_discs=2.0), plain)
    averaged = hybrid(curve_avg_paths=0.3)
    assert not same_points(averaged, plain) and crease_density(averaged) < crease_density(plain)


def test_the_cache_key_of_a_surface_without_averaging_is_the_one_it_had():
    from sbfsem.service.facade import SbfsemService

    off = SbfsemService._mesh_options()
    assert "curve_avg_discs" not in dict(SbfsemService._options_key(off))
    assert dict(SbfsemService._options_key(off)).keys() == (
        SbfsemService.MESH_OPTION_DEFAULTS.keys() - set(SbfsemService.UNKEYED_AT_OFF))
    on = SbfsemService._mesh_options(curve_avg_discs=0.5)
    assert dict(SbfsemService._options_key(on))["curve_avg_discs"] == 0.5
    assert "curve_avg_paths" not in dict(SbfsemService._options_key(on))


def test_a_cell_with_the_averaging_off_is_hash_for_hash_the_mesh_it_was(c943):
    """The averaging is off until asked for, and an explicit 0 is off too: the same vertices
    and faces, bit for bit (ground rule 12: nothing here bumps GEOMETRY_VERSION)."""
    from sbfsem.service.facade import SbfsemService

    service = SbfsemService(offline=True)
    try:
        plain = service.build_mesh(c943, method="closedcurve", pixel_size_um=0.25)
        zero = service.build_mesh(c943, method="closedcurve", pixel_size_um=0.25,
                                  curve_avg_discs=0.0, curve_avg_paths=0.0)
        assert np.array_equal(plain.vertices, zero.vertices) and np.array_equal(plain.faces, zero.faces)
    finally:
        service.close()


def test_averaging_c943_takes_the_creases_out_and_stays_within_a_few_hundredths_of_a_micron(c943):
    from sbfsem.geometry.meshdiff import annotation_deviation, visual_bodies
    from sbfsem.service.facade import SbfsemService, annotation_ground_truth

    service = SbfsemService(offline=True)
    try:
        plain = service.build_mesh(c943, method="closedcurve", pixel_size_um=0.1)
        averaged = service.build_mesh(c943, method="closedcurve", pixel_size_um=0.1, curve_avg_discs=1.0)
    finally:
        service.close()
    assert not same_points(plain, averaged)
    assert crease_density(averaged) < 0.8 * crease_density(plain)
    assert visual_bodies(averaged) <= visual_bodies(plain)
    truth = annotation_ground_truth(c943)
    before = annotation_deviation(plain, *truth, samples=20_000)
    after = annotation_deviation(averaged, *truth, samples=20_000)
    assert after.mean_um - before.mean_um < 0.03
    assert after.annotation_um - before.annotation_um < 0.03
    # Averaging takes the wobble out, and the union of wobbling circles is fatter than their
    # mean: the thin processes of this small cell slim by about the wobble (13 %).
    assert averaged.volume() == pytest.approx(plain.volume(), rel=0.2)


def test_render_mesh_takes_the_averaging_and_says_so_in_the_surface(c943):
    from sbfsem.service.facade import SbfsemService

    service = SbfsemService(offline=True)
    service.add_neuron(c943)
    try:
        plain = service.render_mesh(943, "i", method="closedcurve", pixel_size_um=0.25)
        averaged = service.render_mesh(943, "i", method="closedcurve", pixel_size_um=0.25,
                                       curve_avg_discs=1.0)
        again = service.render_mesh(943, "i", method="closedcurve", pixel_size_um=0.25,
                                    curve_avg_discs=1.0)
    finally:
        service.close()
    assert plain.vertices != averaged.vertices and averaged.vertices == again.vertices
    assert plain.mesh_id != averaged.mesh_id and averaged.mesh_id == again.mesh_id


def test_the_estimate_leaves_room_for_the_averaging(c943):
    from sbfsem.service.facade import (
        CURVE_AVERAGING_FACTOR,
        CURVE_AVERAGING_MAX_MB,
        SbfsemService,
    )

    service = SbfsemService(offline=True)
    service.add_neuron(c943)
    try:
        plain = service.estimate_peak_mb(943, "i", method="closedcurve", pixel_size_um=0.1)
        averaged = service.estimate_peak_mb(943, "i", method="closedcurve", pixel_size_um=0.1,
                                            curve_averaging=True)
        weight = service.measure_weight_mb(c943, {"method": "closedcurve", "pixel_size_um": 0.1,
                                                  "curve_avg_discs": 1.0})
    finally:
        service.close()
    assert plain < averaged <= plain + min(plain * (CURVE_AVERAGING_FACTOR - 1.0), CURVE_AVERAGING_MAX_MB) + 1
    assert weight >= service.MEASURE_MB
