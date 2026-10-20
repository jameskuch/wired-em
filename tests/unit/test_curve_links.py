"""Closed curve's reaches: curved disc links, the two bridges, and joined bodies.

``sbfsem.geometry.curve_links`` draws a long link between two discs as a curve through
the chain's discs instead of a straight band (the user's screenshots of Inferior c1178 and
c16137, 2026-10-16); ``closed_curve.BRIDGE_UM`` and ``LINK_BRIDGE_UM``, constants until
then, are options; ``sbfsem.geometry.curve_ties`` joins the separate bodies of the raster
with ties.  These pin what each does on synthetic cells where the answer is known, that
each is off (or the constant it was) by default, and that a surface built at the default
is the one built before, bit for bit (CLAUDE.md ground rule 12: nothing here bumps
GEOMETRY_VERSION).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sbfsem.geometry import curve_links
from sbfsem.geometry.closed_curve import (
    BRIDGE_UM,
    LINK_BRIDGE_UM,
    ClosedCurveAnnotation,
    annotations_from_discs,
    build_curve_volume,
)
from sbfsem.geometry.curve_links import (
    BEND_RADII,
    CORRIDOR,
    MAX_TURN_DEG,
    link_curves,
    turning_angles,
)
from sbfsem.geometry.curve_ties import _pieces, tie_bodies, tie_radius_um
from sbfsem.geometry.meshdiff import visual_bodies
from sbfsem.geometry.unify import split_components
from sbfsem.geometry.volume import curve_volume_to_mesh

DZ = 0.09
SCALE = (7.5, 7.5, 90.0)


def discs(rows):
    """A node table of discs from ``(id, x, y, section, radius)`` rows."""
    table = pd.DataFrame(rows, columns=["ID", "Xum", "Yum", "Z", "Rum"])
    table["Zum"] = table["Z"] * DZ
    table["TypeCode"] = 1
    return table


def chain(ids):
    ids = list(ids)
    return list(zip(ids[:-1], ids[1:], strict=True))


def arc(n=9, bend_um=4.0, step_um=3.0, radius=0.15, first_id=1, section0=300):
    """``n`` discs ``step_um`` apart along a circle of radius ``bend_um`` in the section
    plane, one section apart: a process the annotator sampled sparsely as it curved."""
    angle = 2.0 * np.arcsin(step_um / (2.0 * bend_um))
    a = angle * np.arange(n)
    rows = [(first_id + k, 20.0 + bend_um * np.cos(a[k]), 20.0 + bend_um * np.sin(a[k]),
             section0 + k, radius) for k in range(n)]
    return discs(rows), chain(range(first_id, first_id + n))


def arrays(nodes):
    return (nodes[["Xum", "Yum"]].to_numpy(float), nodes["Zum"].to_numpy(float),
            nodes["Rum"].to_numpy(float))


def rows_of(nodes, links):
    row = {int(v): k for k, v in enumerate(nodes["ID"])}
    return [(row[a], row[b]) for a, b in links]


def same_annotations(a, b):
    return len(a) == len(b) and all(
        x.z_section == y.z_section and x.outline.shape == y.outline.shape
        and np.array_equal(x.outline, y.outline) for x, y in zip(a, b, strict=True))


# -- the curve ------------------------------------------------------------------------------

def test_a_curve_passes_through_both_disc_centers():
    nodes, links = arc()
    xy, z, r = arrays(nodes)
    curves = link_curves(xy, z, r, rows_of(nodes, links), min_step_um=1.0)
    assert curves, "an arc sampled 3 um apart is curved"
    for (lo, hi), c in curves.items():
        np.testing.assert_allclose(c.points([0.0])[0], [*xy[lo], z[lo]], atol=1e-12)
        np.testing.assert_allclose(c.points([1.0])[0], [*xy[hi], z[hi]], atol=1e-12)


def test_a_curve_through_a_sampled_circle_follows_the_circle():
    """Discs every 3 um round a 4 um bend: the chord between two cuts 0.29 um inside the
    circle, wider than the 0.15 um process, so the straight band misses the process half
    way.  The curve stays within a few hundredths of a micron of it."""
    nodes, links = arc()
    xy, z, r = arrays(nodes)
    curves = link_curves(xy, z, r, rows_of(nodes, links), min_step_um=1.0)
    center = np.array([20.0, 20.0])
    for c in curves.values():
        mid = c.points([0.5])[0, :2]
        if "curve" in c.ends[0] and "curve" in c.ends[1]:
            assert abs(np.linalg.norm(mid - center) - 4.0) < 0.05
        chord_mid = 0.5 * (c.p0[:2] + c.p1[:2])
        assert np.linalg.norm(mid - center) > np.linalg.norm(chord_mid - center)


def test_straight_at_a_chains_ends_and_at_a_branch():
    nodes, links = arc(n=4)
    xy, z, r = arrays(nodes)
    pairs = rows_of(nodes, links)
    curves = link_curves(xy, z, r, pairs, min_step_um=1.0)
    first, last = curves[(0, 1)], curves[(2, 3)]
    assert first.ends[0] == "end" and last.ends[1] == "end"
    np.testing.assert_allclose(first.m0, first.chord)       # the chord's own tangent
    np.testing.assert_allclose(last.m1, last.chord)
    # Two discs alone: nothing on either side to read a tangent from, so no curve.
    two = discs([(1, 0.0, 0.0, 10, 0.2), (2, 3.0, 0.0, 11, 0.2)])
    assert link_curves(*arrays(two), [(0, 1)], min_step_um=1.0) == {}
    # A third link at the middle disc of a chain makes it a branch: both runs keep the chord there.
    branched = pd.concat([nodes, discs([(99, float(xy[1, 0]), float(xy[1, 1]) - 3.0, 302, 0.15)])],
                         ignore_index=True)
    pairs = rows_of(branched, [*links, (2, 99)])
    curves = link_curves(*arrays(branched), pairs, min_step_um=1.0)
    for key, c in curves.items():
        for end, disc in zip(c.ends, key, strict=True):
            if disc == 1:
                assert end == "branch", key


def test_straight_where_the_chain_turns_back():
    """A hairpin (here 150 degrees) is not a bend: a curve through it would balloon."""
    turn = np.radians(150.0)
    rows = [(1, 0.0, 0.0, 10, 0.2), (2, 3.0, 0.0, 11, 0.2),
            (3, 3.0 + 3.0 * np.cos(turn), 3.0 * np.sin(turn), 12, 0.2)]
    nodes = discs(rows)
    curves = link_curves(*arrays(nodes), [(0, 1), (1, 2)], min_step_um=1.0)
    assert curves == {}, "both ends of both links keep the chord: a chain's end or the hairpin"
    rows[2] = (3, 3.0 + 3.0 * np.cos(np.radians(MAX_TURN_DEG - 30)),
               3.0 * np.sin(np.radians(MAX_TURN_DEG - 30)), 12, 0.2)
    nodes = discs([*rows, (4, 9.0, 6.0, 13, 0.2)])
    assert link_curves(*arrays(nodes), [(0, 1), (1, 2), (2, 3)], min_step_um=1.0)


def test_a_curve_never_bends_tighter_than_the_process_nor_leaves_its_corridor():
    rng = np.random.default_rng(7)
    n = 80
    xy = np.cumsum(rng.normal(0.0, 1.6, (n, 2)), axis=0)
    z = DZ * np.arange(n)
    r = rng.uniform(0.1, 1.2, n)
    pairs = chain(range(n))
    curves = link_curves(xy, z, r, pairs, min_step_um=0.5)
    assert len(curves) > 20
    for c in curves.values():
        assert c.min_bend_ratio >= BEND_RADII - 1e-9
        assert c.sagitta_um <= CORRIDOR * np.linalg.norm(c.chord) + 1e-9
        assert 0.0 < c.scale <= 1.0
    # Thick discs on a tight zigzag: the bound bites, and the curve is drawn less curved.
    assert any(c.scale < 1.0 for c in curves.values())


def test_a_curve_never_turns_back_in_z():
    """Z rises monotonically from one disc to the other, so every section between them
    gets the part of the run within one section of its plane, as a straight run's does."""
    rows = [(k, 0.05 * np.sin(k), 0.0, 100 + k, 0.4) for k in range(1, 8)]      # a stack
    rows += [(8, 4.0, 0.0, 130, 0.4)]                                             # 22 sections up
    rows += [(k, 4.0 + 0.05 * np.cos(k), 0.0, 122 + k, 0.4) for k in range(9, 16)]
    nodes = discs(rows)
    curves = link_curves(*arrays(nodes), rows_of(nodes, chain(range(1, 16))), min_step_um=1.0)
    long_link = curves[(6, 7)]
    assert long_link.ends == ("curve", "curve")
    u = np.linspace(0.0, 1.0, 401)
    assert (np.diff(long_link.points(u)[:, 2]) >= -1e-12).all()
    # It leaves the stack climbing, and arrives at the next one climbing: steeper at both ends
    # than the straight band (the S a process makes from one stack to the next).
    def steepness(v):
        return v[2] / np.hypot(v[0], v[1])

    assert steepness(long_link.m0) > 1.5 * steepness(long_link.chord)
    assert steepness(long_link.m1) > 1.5 * steepness(long_link.chord)


def test_the_curves_are_deterministic_and_ignore_the_order_of_the_links():
    nodes, links = arc(n=12)
    xy, z, r = arrays(nodes)
    pairs = rows_of(nodes, links)
    once = link_curves(xy, z, r, pairs, min_step_um=1.0)
    again = link_curves(xy, z, r, [(b, a) for a, b in reversed(pairs)], min_step_um=1.0)
    assert once.keys() == again.keys()
    for key in once:
        assert np.array_equal(once[key].m0, again[key].m0) and np.array_equal(once[key].m1, again[key].m1)


def test_turning_at_the_discs_is_smoothed():
    nodes, links = arc(n=12)
    xy, z, r = arrays(nodes)
    pairs = rows_of(nodes, links)
    curves = link_curves(xy, z, r, pairs, min_step_um=1.0)
    points = np.column_stack([xy, z])
    rows, before, after = turning_angles(points, pairs, curves)
    assert len(rows) == 10
    assert before.min() > 40.0                              # 43 degrees at every disc
    assert after.max() < 1.0                                # one tangent either side


# -- in the raster ----------------------------------------------------------------------------

def test_off_or_beyond_every_link_is_the_annotations_as_they_were():
    nodes, links = arc(n=12)
    plain = annotations_from_discs(nodes, links, volume_scale=SCALE)
    assert same_annotations(plain, annotations_from_discs(nodes, links, volume_scale=SCALE, curve_um=0.0))
    assert same_annotations(plain, annotations_from_discs(nodes, links, volume_scale=SCALE, curve_um=50.0))
    curved = annotations_from_discs(nodes, links, volume_scale=SCALE, curve_um=1.0)
    assert not same_annotations(plain, curved)
    # The discs themselves are untouched; only the runs between them follow the curve.
    assert all(np.array_equal(a.outline, b.outline) for a, b in zip(plain, curved, strict=False)
               if a.location_id)
    assert [a.z_section for a in plain] == [a.z_section for a in curved]


def test_the_curved_run_covers_the_process_where_the_straight_band_misses_it():
    nodes, links = arc(n=9)
    xy, _, _ = arrays(nodes)
    angle = 2.0 * np.arcsin(3.0 / 8.0)
    halfway = np.array([[20.0 + 4.0 * np.cos(angle * (k + 0.5)), 20.0 + 4.0 * np.sin(angle * (k + 0.5)),
                         (300 + k) * DZ] for k in range(1, 7)])
    straight = build_curve_volume(annotations_from_discs(nodes, links, volume_scale=SCALE),
                                  volume_scale=SCALE, pixel_size_um=0.05, links=None, bridge_um=0.0)
    curved = build_curve_volume(annotations_from_discs(nodes, links, volume_scale=SCALE, curve_um=1.0),
                                volume_scale=SCALE, pixel_size_um=0.05, links=None, bridge_um=0.0)
    assert not straight.contains(halfway).any()
    assert curved.contains(halfway).all()
    mesh = curve_volume_to_mesh(curved)
    assert len(split_components(mesh)) == 1


def test_the_option_reaches_the_build_and_is_off_by_default(c943):
    from sbfsem.service.facade import SbfsemService

    service = SbfsemService(offline=True)
    try:
        plain = service.build_mesh(c943, method="closedcurve", pixel_size_um=0.1)
        spelled = service.build_mesh(c943, method="closedcurve", pixel_size_um=0.1, curve_links_um=0.0,
                                     curve_tie_um=0.0, curve_bridge_um=BRIDGE_UM,
                                     curve_link_bridge_um=LINK_BRIDGE_UM)
        curved = service.build_mesh(c943, method="closedcurve", pixel_size_um=0.1, curve_links_um=0.5)
    finally:
        service.close()
    # Off and the constants spelled out are the surface as it was, bit for bit.
    assert np.array_equal(plain.vertices, spelled.vertices) and np.array_equal(plain.faces, spelled.faces)
    assert not (plain.vertices.shape == curved.vertices.shape
                and np.array_equal(plain.vertices, curved.vertices))
    assert visual_bodies(curved) <= visual_bodies(plain)


# -- the two bridges -----------------------------------------------------------------------------

THETA = np.linspace(0.0, 2.0 * np.pi, 33)[:-1]


def outline(z, cx=10.0, radius=2.0, location_id=None):
    ring = np.column_stack([cx + radius * np.cos(THETA), 12.0 + radius * np.sin(THETA)])
    return ClosedCurveAnnotation(ring, [], z, z * 0.07, location_id=location_id)


def test_the_skip_bridge_reaches_as_far_as_it_is_told():
    """Two traced stretches 1.4 um apart (20 sections at 70 nm): past the 1 um default
    they stay two bodies; a 2 um bridge interpolates the skip and makes them one."""
    stack = [outline(z) for z in (*range(100, 105), *range(125, 130))]
    scale = (2.176, 2.176, 70.0)
    default = build_curve_volume(stack, volume_scale=scale, pixel_size_um=0.1)
    explicit = build_curve_volume(stack, volume_scale=scale, pixel_size_um=0.1, bridge_um=BRIDGE_UM)
    wide = build_curve_volume(stack, volume_scale=scale, pixel_size_um=0.1, bridge_um=2.0)
    assert len(default.runs()) == 2 and np.array_equal(default.tiles, explicit.tiles)
    assert wide.runs() == [(0, 30)]
    assert len(split_components(curve_volume_to_mesh(wide))) == 1


def test_the_link_bridge_reaches_as_far_as_it_is_told():
    """A linked pair of outlines 2.1 um apart is followed under the default 5 um and not
    under 2 um; a disc link 22 sections long likewise."""
    pair = [outline(100, location_id=1), outline(130, location_id=2)]
    scale = (2.176, 2.176, 70.0)
    assert len(build_curve_volume(pair, volume_scale=scale, pixel_size_um=0.1, links=[(1, 2)]).runs()) == 1
    assert len(build_curve_volume(pair, volume_scale=scale, pixel_size_um=0.1, links=[(1, 2)],
                                  link_bridge_um=2.0).runs()) == 2
    nodes = discs([(1, 0.0, 0.0, 100, 0.5), (2, 3.0, 0.0, 122, 0.5)])
    runs = [a for a in annotations_from_discs(nodes, [(1, 2)], volume_scale=SCALE) if not a.location_id]
    assert len(runs) == 23
    assert not [a for a in annotations_from_discs(nodes, [(1, 2)], volume_scale=SCALE, link_bridge_um=1.5)
                if not a.location_id]


def test_the_service_hands_the_bridges_to_the_raster_only_when_they_are_not_the_constants():
    from sbfsem.service.facade import _curve_outlines

    class Cell:
        volume_scale = SCALE
        geometries = pd.DataFrame({"ID": [1, 2], "ParentID": 0, "Z": [100, 130], "Zum": [9.0, 11.7],
                                   "Curve": [[outline(100).outline], [outline(130).outline]],
                                   "Registered": True})
        nodes = discs([(7, 30.0, 30.0, 100, 0.5), (8, 31.0, 30.0, 101, 0.5)])
        edges = pd.DataFrame({"A": [1, 7], "B": [2, 8]})

        def get_cell_nodes(self):
            return self.nodes

    cell = Cell()
    _, plain = _curve_outlines(cell, discs=True)
    assert "bridge_um" not in plain and "link_bridge_um" not in plain
    _, same = _curve_outlines(cell, discs=True, bridge_um=BRIDGE_UM, link_bridge_um=LINK_BRIDGE_UM)
    assert same == plain
    _, wide = _curve_outlines(cell, discs=True, bridge_um=3.0, link_bridge_um=0.5)
    assert wide["bridge_um"] == 3.0 and wide["link_bridge_um"] == 0.5


# -- joining bodies --------------------------------------------------------------------------------

def two_blobs(gap_um=0.6, pixel=0.1):
    """Two vertical stacks of discs side by side, ``gap_um`` apart at their rims, nobody linked."""
    left = [(k, 10.0, 10.0, 200 + k, 1.0) for k in range(1, 11)]
    right = [(100 + k, 12.0 + gap_um, 10.0, 200 + k, 1.0) for k in range(1, 11)]
    nodes = discs(left + right)
    links = chain(range(1, 11)) + chain(range(101, 111))
    volume = build_curve_volume(annotations_from_discs(nodes, links, volume_scale=SCALE),
                                volume_scale=SCALE, pixel_size_um=pixel, links=None, bridge_um=0.0)
    return volume


def test_bodies_within_reach_are_joined_by_a_tie_and_farther_ones_left():
    volume = two_blobs(gap_um=0.6)
    cache: dict = {}
    mesh = curve_volume_to_mesh(volume, cache=cache)
    assert len(split_components(mesh)) == 2
    near, ties = tie_bodies(volume, mesh, 1.0, cache=cache)
    assert ties == 1 and len(split_components(near)) == 1
    # The tie entered the raster: a fresh contour of it is the same surface, bit for bit.
    fresh = curve_volume_to_mesh(volume)
    assert np.array_equal(near.vertices, fresh.vertices) and np.array_equal(near.faces, fresh.faces)
    far = two_blobs(gap_um=0.6)
    mesh = curve_volume_to_mesh(far)
    kept, ties = tie_bodies(far, mesh, 0.5)
    assert ties == 0 and kept is mesh
    off, ties = tie_bodies(far, mesh, 0.0)
    assert ties == 0 and off is mesh


def test_a_tie_survives_the_smoothing_on_a_coarse_pixel():
    """The 3x3x3 smoothing removes anything under about a pixel and a half: at 0.25 um a
    0.25 um tie would vanish, so it is drawn a pixel and a half thick."""
    assert tie_radius_um(0.1) == 0.25 and tie_radius_um(0.25) == pytest.approx(0.375)
    volume = two_blobs(gap_um=1.0, pixel=0.25)
    mesh = curve_volume_to_mesh(volume)
    tied, ties = tie_bodies(volume, mesh, 2.0)
    assert ties == 1 and len(split_components(tied)) == 1


def test_the_pieces_are_split_components_pieces():
    volume = two_blobs(gap_um=0.6)
    mesh = curve_volume_to_mesh(volume)
    ours, theirs = _pieces(mesh), split_components(mesh)
    assert len(ours) == len(theirs) == 2
    for a, b in zip(ours, theirs, strict=True):
        assert np.array_equal(a.vertices, b.vertices) and np.array_equal(a.faces, b.faces)


def stacks(rows, pixel=0.1):
    """A raster of vertical disc stacks, each ``(first id, x, first section, sections, radius)``,
    each stack linked up its sections and none to another."""
    nodes, links = [], []
    for first, x, section0, n, radius in rows:
        nodes += [(first + k, x, 10.0, section0 + k, radius) for k in range(n)]
        links += chain(range(first, first + n))
    return build_curve_volume(annotations_from_discs(discs(nodes), links, volume_scale=SCALE),
                              volume_scale=SCALE, pixel_size_um=pixel, links=None, bridge_um=0.0)


def test_a_pair_across_two_runs_of_sections_does_not_use_up_a_tie_inside_one():
    """Two stacks 2 um apart in one run of sections, and a wide one 1.9 um above both in a run of
    its own (the review's case, 2026-10-16).  The two pairs across the runs are the shortest
    gaps, but no tie can cross the open stretch between runs: picked first, they joined all three
    on paper, and the tie the two stacks below needed was never drawn (0 ties, 3 bodies)."""
    volume = stacks([(1, 10.0, 200, 10, 1.0), (100, 14.0, 200, 10, 1.0), (200, 12.0, 230, 10, 3.0)])
    assert len(volume.runs()) == 2
    mesh = curve_volume_to_mesh(volume)
    assert len(split_components(mesh)) == 3
    tied, ties = tie_bodies(volume, mesh, 3.0)
    assert ties == 1 and len(split_components(tied)) == 2
    # The two below are one body now; the one above is left, in its own run.
    upper = [p for p in split_components(tied) if p.vertices[:, 2].min() > 225 * DZ]
    assert len(upper) == 1


def test_a_cavity_is_not_tied_to_the_body_round_it():
    """A body hollow inside has a second sheet round the hole, wound the other way.  It is not a
    separate body: tied to the outer sheet, the tie ran through the wall and, extended two radii
    past each end, stuck 0.44 um out of the cell (the review's solid box with a 14 x 14 x 10
    voxel cavity, Join 3 um)."""
    from sbfsem.geometry.closed_curve import CurveVolume

    solid = np.zeros((40, 40, 24), dtype=bool)
    solid[4:36, 4:36, 2:22] = True
    solid[13:27, 13:27, 7:17] = False
    volume = CurveVolume.from_dense(solid, origin_um=[0.0, 0.0, 300 * DZ],
                                    spacing_um=[0.1, 0.1, DZ], sections=np.arange(300, 324))
    mesh = curve_volume_to_mesh(volume)
    assert len(split_components(mesh)) == 2
    tied, ties = tie_bodies(volume, mesh, 3.0)
    assert ties == 0 and tied is mesh
    assert np.array_equal(curve_volume_to_mesh(volume).vertices, mesh.vertices), "nothing was drawn"


def test_the_ties_tiles_are_allocated_once_and_keep_what_was_drawn(monkeypatch):
    """Each tie used to allocate its tiles as it was drawn, concatenating and reordering every
    tile each time: RPC1 c2628 at 0.025 um (111 MB of tiles, 330 ties) peaked at 10,067 MB
    against 5,893 with one allocation for them all (2026-10-16)."""
    from sbfsem.geometry.closed_curve import CurveVolume

    calls = []
    allocate = CurveVolume._allocate

    def counting(self, windows):
        calls.append(len(list(windows)))
        return allocate(self, windows)

    monkeypatch.setattr(CurveVolume, "_allocate", counting)
    volume = stacks([(1, 10.0, 200, 10, 1.0), (100, 13.0, 200, 10, 1.0), (200, 16.0, 200, 10, 1.0)])
    tied, ties = tie_bodies(volume, curve_volume_to_mesh(volume), 2.0)
    assert ties == 2 and len(calls) == 1 and calls[0] >= 2 * 2

    # What a batch allocates is empty, what was there stays, and the keys stay sorted.
    labelled = build_curve_volume(annotations_from_discs(discs([(1, 10.0, 10.0, 200, 1.0),
                                                               (2, 10.0, 10.0, 201, 1.0)]), [(1, 2)],
                                                         volume_scale=SCALE),
                                  volume_scale=SCALE, pixel_size_um=0.1, links=None, bridge_um=0.0,
                                  keep_labels=True)
    before, labels = labelled.dense(), labelled.labels.copy()
    rows, cols, _ = labelled.shape
    allocate(labelled, [(0, 0, rows - 1, 0, cols - 1), (1, 0, 5, 0, 5), (1, 0, 5, 0, 5)])
    assert np.array_equal(labelled.dense(), before)
    assert (np.diff(labelled.keys) > 0).all()
    assert labelled.labels.shape == labelled.tiles.shape and labelled.labels.sum() == labels.sum()


def test_the_gap_search_queries_only_within_reach(monkeypatch):
    """Two large sheets: every vertex of the smaller was looked up against the larger with no
    limit (2.2 million faces 24 s).  Each lookup is bounded by the reach now, and a pair whose
    boxes are farther apart than that is never looked up at all."""
    import sbfsem.geometry.curve_ties as curve_ties

    bounds = []

    class Tree(curve_ties.cKDTree):
        def query(self, x, *args, **kwargs):
            bounds.append(kwargs.get("distance_upper_bound"))
            return super().query(x, *args, **kwargs)

    monkeypatch.setattr(curve_ties, "cKDTree", Tree)
    near = two_blobs(gap_um=0.6)
    assert tie_bodies(near, curve_volume_to_mesh(near), 1.0)[1] == 1
    assert bounds == [1.0]
    bounds.clear()
    far = two_blobs(gap_um=5.0)
    mesh = curve_volume_to_mesh(far)
    assert tie_bodies(far, mesh, 1.0) == (mesh, 0) and bounds == []


def test_following_links_0_um_still_draws_a_link_to_the_next_section():
    """Follow links is how far a link is followed across skipped sections, for discs as for traced
    outlines.  At 0 it dropped every disc link, the next section's included, and a 30-disc dendrite
    running across the sections fell into 29 beads."""
    rows = [(k, 0.8 * k, 5.0, 200 + k, 0.3) for k in range(30)]
    nodes, links = discs(rows), chain(range(30))
    for reach, bodies in ((LINK_BRIDGE_UM, 1), (0.5, 1), (0.0, 1)):
        annotations = annotations_from_discs(nodes, links, volume_scale=SCALE, link_bridge_um=reach)
        mesh = curve_volume_to_mesh(build_curve_volume(annotations, volume_scale=SCALE, pixel_size_um=0.05,
                                                       links=None, bridge_um=0.0))
        assert visual_bodies(mesh) == bodies, reach
    # A link across two skipped sections (0.27 um) is followed at 0.5 um and not at 0.
    skip = discs([(1, 0.0, 0.0, 100, 0.3), (2, 2.0, 0.0, 103, 0.3)])
    assert [a for a in annotations_from_discs(skip, [(1, 2)], volume_scale=SCALE, link_bridge_um=0.5)
            if not a.location_id]
    assert not [a for a in annotations_from_discs(skip, [(1, 2)], volume_scale=SCALE, link_bridge_um=0.0)
                if not a.location_id]


def test_joined_bodies_reach_the_build_through_the_service():
    from sbfsem.service.facade import SbfsemService

    left = [(k, 10.0, 10.0, 200 + k, 1.0) for k in range(1, 11)]
    right = [(100 + k, 12.6, 10.0, 200 + k, 1.0) for k in range(1, 11)]

    class Cell:
        volume_scale = SCALE
        geometries = None
        nodes = discs(left + right)
        edges = pd.DataFrame(chain(range(1, 11)) + chain(range(101, 111)), columns=["A", "B"])

        def get_cell_nodes(self):
            return self.nodes

    service = SbfsemService(offline=True)
    try:
        apart = service.build_mesh(Cell(), method="closedcurve", pixel_size_um=0.1)
        joined = service.build_mesh(Cell(), method="closedcurve", pixel_size_um=0.1, curve_tie_um=1.0)
    finally:
        service.close()
    assert len(split_components(apart)) == 2 and len(split_components(joined)) == 1
    assert joined.meta.get("curve_ties") == 1


def test_the_estimate_prices_joined_bodies_by_the_surface(c943, monkeypatch):
    """Joining bodies keeps every block's contour for the second pass, and the first surface
    meanwhile: it grows with the surface (c2628 at 0.025 um peaked 3,231 -> 5,835 MB), so it is
    priced by the raster, up to what the largest surface allowed could take, not at a fixed 900
    MB (a 12-million-triangle surface took 2.5 GB).  Only Closed curve joins bodies, so Hybrid's
    estimate does not move."""
    import sbfsem.service.facade as facade
    from sbfsem.service.facade import (
        CURVE_BYTES_PER_VOXEL,
        CURVE_TIE_BYTES_PER_VOXEL,
        CURVE_TIE_MAX_MB,
        SbfsemService,
    )

    service = SbfsemService(offline=True)
    service.add_neuron(c943)
    try:
        def estimate(method, **options):
            return service.estimate_peak_mb(943, "i", method=method, pixel_size_um=0.1,
                                            curve_options=options)

        plain, tied = estimate("closedcurve"), estimate("closedcurve", curve_tie_um=2.0)
        voxels = (plain - 150.0) / CURVE_BYTES_PER_VOXEL * 1e6
        assert tied - plain == pytest.approx(voxels * CURVE_TIE_BYTES_PER_VOXEL / 1e6, abs=0.2)  # rounded
        assert estimate("hybrid", curve_tie_um=2.0) == estimate("hybrid")
        # A raster at the surface limit: the tie's part is the cap, which follows the limit.
        monkeypatch.setattr(facade, "curve_grid_voxels", lambda *args, **kwargs: 290_000_000)
        assert estimate("closedcurve", curve_tie_um=2.0) - estimate("closedcurve") == pytest.approx(
            CURVE_TIE_MAX_MB)
        assert CURVE_TIE_MAX_MB > 4000
        assert abs(CURVE_TIE_MAX_MB - 260e-6 * 1.9 * facade.MAX_SURFACE) < 1e-6
        weight = service.measure_weight_mb(c943, {"method": "closedcurve", "pixel_size_um": 0.1,
                                                  "curve_tie_um": 2.0})
    finally:
        service.close()
    assert weight >= tied


def test_curve_links_module_names_its_bounds():
    assert curve_links.MAX_TURN_DEG == 90.0 and curve_links.BEND_RADII == 1.0 and curve_links.CORRIDOR == 0.25
    assert curve_links.CURVE_ALPHA == 1.0, "chordal knots: a long link leaves a chain the way it runs"


def test_the_reaches_and_the_raw_budget_are_keyed_side_by_side(c943):
    """2.5.1 named a Voxel remesh build's raw-triangle budget in its surface's key when it is over
    the old ceiling.  The four Closed curve reaches are keyed by their values beside it: a Hybrid
    (whose disc half is a Voxel remesh) with a budget and a bridge of its own carries both, and a
    bridge at its constant's value carries the budget alone."""
    import sbfsem.service.facade as facade
    from sbfsem.service.facade import SbfsemService

    class Disk:
        def __init__(self):
            self.keys = []

        def get(self, key):
            return None

        def put(self, key, mesh):
            self.keys.append(key)

    service = SbfsemService(offline=True)
    service.add_neuron(c943)
    service._disk_meshes = Disk()
    try:
        for bridge in (BRIDGE_UM, 2.0):
            service.mesh_object(943, "i", method="hybrid", pathway="voxel2", voxel2_um=0.3,
                                voxel2_faces=0, pixel_size_um=0.25, curve_bridge_um=bridge)
    finally:
        service.close()
    plain, bridged = service._disk_meshes.keys
    assert plain[-1] == bridged[-1] == "raw-budget:10000000"
    assert "curve_bridge_um" not in dict(plain[5]) and dict(bridged[5])["curve_bridge_um"] == 2.0
    # Closed curve never has a raw budget, whatever the pathway's controls say.
    assert facade._budget_token("closedcurve", {"pathway": "voxel2", "voxel2_faces": 0,
                                                "curve_tie_um": 2.0}) is None
