"""The skeleton check, the SWC provenance header and the bulk SWC archive."""

from __future__ import annotations

import io
import zipfile

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from sbfsem.analysis.dendrite import morphometrics
from sbfsem.analysis.skeleton_check import (
    KINDS,
    _inside_fraction,
    _inside_polygon,
    check_skeleton,
    dropped_links,
    spoke_lengths,
)
from sbfsem.core.structure import Neuron
from sbfsem.export.bulk import file_name, swc_archive
from sbfsem.export.swc import spanning_forest, swc_table, write_swc
from sbfsem.io.legacy_json import read_legacy_json

#: 1 pixel = 1 um in the plane, 50 nm sections: coordinates below read as microns.
SCALE = (1000.0, 1000.0, 50.0)


def build(points: dict[int, tuple[float, float, int, float]], links, *, geometries=None) -> Neuron:
    """``points``: Location ID -> (x um, y um, section, radius um)."""
    ids = list(points)
    nodes = pd.DataFrame({
        "ID": ids, "ParentID": [1] * len(ids),
        "VolumeX": [points[i][0] for i in ids], "VolumeY": [points[i][1] for i in ids],
        "Z": [points[i][2] for i in ids], "Radius": [points[i][3] for i in ids],
        "X": [0.0] * len(ids), "Y": [0.0] * len(ids),
        "OffEdge": [False] * len(ids), "Terminal": [False] * len(ids),
        "TypeCode": [1] * len(ids),
    })
    edges = pd.DataFrame({"ID": [1] * len(links), "A": [a for a, _ in links],
                          "B": [b for _, b in links]})
    return Neuron(1, "i", nodes=nodes, edges=edges, volume_scale=SCALE, geometries=geometries)


@pytest.fixture(scope="module")
def planted() -> Neuron:
    """One of everything the check looks for, on an otherwise straight cell."""
    points = {1: (0.0, 0.0, 0, 5.0)}                      # the soma
    links = []
    # The trunk: 2..12 along +x, one section and one micron apart.
    for k in range(11):
        points[2 + k] = (3.0 + k, 0.0, 1 + k, 0.3)
    links += [(1, 2)] + [(2 + k, 3 + k) for k in range(10)]
    # A loop: 4 - 20 - 5 closes a ring with the trunk's own 4 - 5.
    points[20] = (5.5, 0.6, 3, 0.3)
    links += [(4, 20), (20, 5)]
    # A fragment: two annotations linked only to each other, beside the trunk.
    points[30], points[31] = (10.0, 1.5, 8, 0.3), (10.0, 2.0, 9, 0.3)
    links += [(30, 31)]
    # A stub: a tip 0.5 um off a branch point whose own radius is 0.8 um.
    points[7] = (8.0, 0.0, 6, 0.8)
    points[40] = (8.0, 0.5, 7, 0.2)
    links += [(7, 40)]
    # A jump: the trunk's last link lands 6 um to the side.
    points[13] = (13.0, 6.0, 12, 0.3)
    links += [(12, 13)]
    # A spike: a 15-annotation branch off 3, one annotation 1.5 um out of line.
    for k in range(15):
        points[50 + k] = (4.0 + (1.5 if k == 7 else 0.0), 0.2 + 0.2 * k, 2 + k, 0.3)
    links += [(3, 50)] + [(50 + k, 51 + k) for k in range(14)]
    # A hairpin: a branch off 10 that runs out 1.6 um and straight back.
    out = [(11.0, -0.2 - 0.2 * k) for k in range(8)]
    back = [(11.1, -1.6 + 0.2 * k) for k in range(8)]
    for k, (x, y) in enumerate(out + back):
        points[70 + k] = (x, y, 10 + k, 0.1)
    links += [(10, 70)] + [(70 + k, 71 + k) for k in range(15)]
    return build(points, links)


def by_kind(check, kind):
    return [p for p in check.problems if p.kind == kind]


def test_every_planted_problem_is_found_once(planted):
    check = check_skeleton(planted)
    assert set(check.counts()) == set(KINDS)

    (loop,) = by_kind(check, "loop")
    assert set(loop.location_ids) == {4, 5, 20}
    assert loop.extra["dropped_link"] in ([4, 5], [4, 20], [5, 20])

    (fragment,) = by_kind(check, "fragment")
    assert set(fragment.location_ids) == {30, 31}
    assert fragment.extra["nearest_id"] == 9 and fragment.extra["gap_um"] == pytest.approx(1.5, abs=0.01)

    (jump,) = by_kind(check, "jump")
    assert set(jump.location_ids) == {12, 13}

    (stub,) = by_kind(check, "stub")
    assert stub.anchor_id == 40 and stub.extra["base_id"] == 7
    assert stub.location_ids == (7, 40) and stub.links in (((7, 40),), ((40, 7),))
    assert len(loop.links) == 3 and len(fragment.links) == 1

    (spike,) = by_kind(check, "spike")
    assert spike.anchor_id == 57

    (hairpin,) = by_kind(check, "hairpin")
    assert 77 in hairpin.location_ids and 78 in hairpin.location_ids
    assert hairpin.extra["tortuosity"] > 2

    # What is fixed in Viking leads the list.
    fixes = [p.fix for p in check.problems]
    assert fixes == sorted(fixes, key=["viking", "export"].index)


def test_spoke_is_the_part_of_a_link_inside_the_wide_annotation(planted):
    spokes = spoke_lengths(planted)
    # 1 -> 2: 3 um of link, all of it inside the soma's 5 um radius.
    assert spokes[(1, 2)] == pytest.approx(np.hypot(3.0, 0.05), abs=1e-6)
    check = check_skeleton(planted)
    assert [p.location_ids for p in by_kind(check, "spoke")] == [(1, 2)]
    s = check.summary
    assert s["rim_corrected_length_um"] == pytest.approx(s["total_length_um"] - s["spoke_length_um"])

    # A link that leaves the wide annotation is cut at its rim.
    far = build({1: (0, 0, 0, 2.0), 2: (5.0, 0, 1, 0.2)}, [(1, 2)])
    assert spoke_lengths(far)[(1, 2)] == pytest.approx(np.hypot(5.0, 0.05) * 2.0 / 5.0)
    # Between annotations of like size there is no spoke.
    assert spoke_lengths(build({1: (0, 0, 0, 0.5), 2: (1.0, 0, 1, 0.3)}, [(1, 2)])) == {}


def test_a_traced_outline_is_the_rim_not_the_stored_radius():
    # An ellipse 8 um by 2 um whose stored radius says 1: along x the rim is
    # 4 um out, along y it is 1.
    t = np.linspace(0, 2 * np.pi, 200, endpoint=False)
    ellipse = np.column_stack([4 * np.cos(t), 1 * np.sin(t)])
    o = np.zeros(2)
    assert _inside_fraction(o, [8.0, 0.0], 1.0, ellipse) == pytest.approx(0.5, abs=0.002)
    assert _inside_fraction(o, [0.0, 4.0], 1.0, ellipse) == pytest.approx(0.25, abs=0.002)
    assert _inside_fraction(o, [3.0, 0.0], 1.0, ellipse) == 1.0       # ends inside it
    assert _inside_fraction(o, [4.0, 0.0], 1.0, None) == 0.25         # a circle: its radius

    # A crescent's centroid lies outside it.  A link from there is inside only
    # where it actually crosses the crescent -- not from the centroid to the
    # farthest crossing, which is what a ray to "the rim" measured.
    a = np.linspace(-1.2, 1.2, 60)
    crescent = np.vstack([np.column_stack([5 * np.cos(a), 5 * np.sin(a)]),
                          np.column_stack([4 * np.cos(a[::-1]), 4 * np.sin(a[::-1])])])
    centroid = np.array([3.2, 0.0])                                   # in the hollow
    assert not _inside_polygon(centroid, crescent) and _inside_polygon([4.5, 0.0], crescent)
    assert _inside_fraction(centroid, [7.2, 0.0], 1.0, crescent) == pytest.approx(0.25, abs=0.005)
    assert _inside_fraction(centroid, [3.4, 0.0], 1.0, crescent) == 0.0

    geometries = pd.DataFrame({"ID": [1], "ParentID": [1], "Z": [0], "Zum": [0.0],
                               "Curve": [[ellipse]], "Registered": [True]})
    cell = build({1: (0, 0, 0, 1.0), 2: (3.0, 0, 1, 0.2)}, [(1, 2)], geometries=geometries)
    # By the stored radius a third of the link is inside; by the outline, all of it.
    assert spoke_lengths(cell)[(1, 2)] == pytest.approx(np.hypot(3.0, 0.05), abs=1e-6)
    m = morphometrics(cell)
    assert m.spoke_length_by_radius_um == pytest.approx(1.0)
    assert m.rim_corrected_length_um == pytest.approx(0.0, abs=1e-6)


def test_morphometrics_report_both_lengths(planted):
    m = morphometrics(planted)
    assert m.n_loops == 1 and m.n_components == 2
    assert m.spoke_length_um > 0
    assert m.rim_corrected_length_um == pytest.approx(m.total_length_um - m.spoke_length_um)


def reference_forest(graph, root):
    """The traversal as it was written until 2026-09-21."""
    order, parent_of = [], {root: -1}
    for start in [root] + sorted(
            n for n in graph.nodes if n != root and not nx.has_path(graph, root, n)):
        if start in parent_of and start != root:
            continue
        parent_of.setdefault(start, -1)
        for parent, child in nx.bfs_edges(graph, start):
            parent_of[child] = parent
        order.extend([start] + [n for n in nx.bfs_tree(graph, start) if n != start])
    return list(dict.fromkeys(order)), parent_of


def test_spanning_forest_is_the_old_traversal(planted, request):
    c943 = read_legacy_json(request.path.parents[1] / "fixtures" / "i943.json")
    for neuron in (planted, c943):
        graph = neuron.graph()
        root = int(neuron.get_soma_id())
        assert spanning_forest(graph, root) == reference_forest(graph, root)


def test_swc_header_names_the_dropped_links(planted, tmp_path):
    path = write_swc(planted, tmp_path / "c1.swc", branch_types=False)
    header = [line[2:] for line in path.read_text().splitlines() if line.startswith("# ")]
    assert "SECTION_THICKNESS_UM 0.0500" in header
    table = swc_table(planted, branch_types=False)
    n_nodes, n_links = planted.graph().number_of_nodes(), planted.graph().number_of_edges()
    assert f"ANNOTATION_GRAPH {n_nodes} annotations, {n_links} links, 2 connected piece(s), 1 loop(s)" in header
    assert any(line.startswith("ROOTS 2 ") for line in header)
    (a, b), = dropped_links(planted)
    row_of = dict(zip(table["ID"], table["n"], strict=True))
    assert f"DROPPED_LINK {a} {b} rows {row_of[a]} {row_of[b]}" in header
    assert any(line.startswith("OPTIONS branch_types=false") for line in header)
    # The dropped link is a link of the graph that no row of the file keeps.
    kept = {frozenset((int(i), int(table.loc[table["n"] == p, "ID"].iloc[0])))
            for i, p in zip(table["ID"], table["P"], strict=True) if p != -1}
    assert planted.graph().has_edge(a, b) and frozenset((a, b)) not in kept
    assert len(kept) + 1 == n_links


def test_swc_header_says_the_soma_is_typed_not_trimmed(planted, tmp_path):
    def soma_line(**kwargs):
        path = write_swc(planted, tmp_path / "c1.swc", branch_types=False, **kwargs)
        lines = [line[2:] for line in path.read_text().splitlines() if line.startswith("# SOMA ")]
        assert len(lines) == 1
        return lines[0]

    table = swc_table(planted, branch_types=False, soma_fraction=0.5)
    r_max = planted.nodes["Rum"].max()
    line = soma_line(soma_fraction=0.5)
    assert line.startswith(f"SOMA cutoff {0.5 * r_max:.3f} um (0.50 of the largest stored radius")
    assert f"{int((table['T'] == 1).sum())} rows typed 1 (soma)" in line
    assert "nothing trimmed" in line
    assert soma_line(has_soma=False).startswith("SOMA none (has_soma=false)")
    # Typing only: the rows and their positions are the same either way.
    off = swc_table(planted, branch_types=False, has_soma=False)
    assert table[["ID", "x", "y", "z", "P"]].equals(off[["ID", "x", "y", "z", "P"]])


def test_file_name_pattern():
    assert file_name("c{id}", structure_id=192, volume="RPC1") == "c192"
    assert file_name("{volume}_c{id}_{label}", structure_id=2628, volume="RPC1",
                     label="MC (Muller)") == "RPC1_c2628_MC-Muller"
    for bad in ("cell", "../c{id}", "c{id}/x", "c{id}{nope}", "c{id"):
        with pytest.raises(ValueError):
            file_name(bad, structure_id=1, volume="RPC1")


def test_swc_archive_writes_what_it_can_and_says_what_it_could_not(planted):
    def load(structure_id):
        if structure_id == 2:
            raise LookupError("no such structure")
        return planted

    data = swc_archive([1, 2, 1], load, volume="NeitzInferiorMonkey", pattern="cell-{id}")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert sorted(archive.namelist()) == ["cell-1.swc", "manifest.csv"]
        manifest = pd.read_csv(io.BytesIO(archive.read("manifest.csv")))
        swc = archive.read("cell-1.swc").decode()
    assert list(manifest["structure_id"]) == [1, 2]            # asked for twice, written once
    ok, failed = manifest.iloc[0], manifest.iloc[1]
    assert ok["file"] == "cell-1.swc" and ok["loops"] == 1 and ok["pieces"] == 2
    assert ok["options"] == "branch_types=false lamellae=false radius=stored sampling=annotations"
    assert pd.isna(ok["error"]) and ok["swc_rows"] == len(swc_table(planted))
    assert "LookupError" in failed["error"] and pd.isna(failed["file"])
    assert "# DROPPED_LINKS 1 " in swc and " 5 " not in {line.split()[1] for line in swc.splitlines()
                                                          if line and not line.startswith("#")}
    with pytest.raises(ValueError):
        swc_archive([1], load, volume="RPC1", pattern="no-placeholder")


# -- the length bracket (sbfsem.analysis.relax) --------------------------------

def zigzag(radius=0.5, n=21, swing=0.3):
    """A straight process traced with centers alternately ``swing`` to each side."""
    points = {k + 1: (0.5 * k, swing if k % 2 else -swing, k, radius) for k in range(n)}
    return build(points, [(k, k + 1) for k in range(1, n)])


def test_relaxation_shortens_within_each_annotation_and_no_further():
    from sbfsem.analysis.relax import relax_skeleton

    cell = zigzag()
    as_is = relax_skeleton(cell, 0.0)
    assert as_is.length_after_um == pytest.approx(as_is.length_before_um) and as_is.n_moved == 0

    half = relax_skeleton(cell, 0.5)
    floor = relax_skeleton(cell, 1.0)
    assert floor.length_after_um < half.length_after_um < as_is.length_before_um
    nodes = cell.get_cell_nodes().set_index("ID")
    for result in (half, floor):
        for i, (x, y) in result.positions.items():
            moved = np.hypot(x - nodes.loc[i, "Xum"], y - nodes.loc[i, "Yum"])
            assert moved <= result.f * 0.5 + 1e-9                     # inside f of its circle
        for tip in (1, 21):                                           # tips do not move
            assert result.positions[tip] == pytest.approx(
                (nodes.loc[tip, "Xum"], nodes.loc[tip, "Yum"]))
    # Every center can reach the axis (0.3 < 0.5): between the tips the floor
    # is the straight path, less what the fixed tips cost at each end.
    straight = float(np.hypot(10.0, 0.0))
    assert straight < floor.length_after_um < straight + 0.5
    with pytest.raises(ValueError):
        relax_skeleton(cell, 1.5)
    with pytest.raises(ValueError):
        relax_skeleton(cell, 0.5, bound="hull")


def test_an_outline_holds_its_node_where_a_radius_would_not():
    from sbfsem.analysis.relax import relax_skeleton

    # The middle annotation is a slot 6 um by 0.4 um along x whose stored radius
    # is its equivalent-area radius (0.87 um), and its neighbors pull it in y.
    t = np.linspace(0, 2 * np.pi, 240, endpoint=False)
    slot = np.column_stack([3.0 * np.cos(t), 0.2 * np.sin(t)])
    geometries = pd.DataFrame({"ID": [2], "ParentID": [1], "Z": [1], "Zum": [0.05],
                               "Curve": [[slot]], "Registered": [True]})
    points = {1: (0.0, 2.0, 0, 0.3), 2: (0.0, 0.0, 1, 0.87), 3: (0.0, 2.0, 2, 0.3),
              4: (0.0, 3.0, 3, 0.3), 5: (0.0, 3.0, -1, 0.3)}
    cell = build(points, [(5, 1), (1, 2), (2, 3), (3, 4)], geometries=geometries)
    by_outline = relax_skeleton(cell, 1.0, bound="outline").positions[2]
    by_radius = relax_skeleton(cell, 1.0, bound="radius").positions[2]
    assert _inside_polygon(by_outline, slot) and by_outline[1] == pytest.approx(0.2, abs=0.02)
    assert not _inside_polygon(by_radius, slot) and by_radius[1] == pytest.approx(0.87, abs=0.02)


def test_relaxed_swc_says_what_it_is(planted, tmp_path):
    plain = write_swc(planted, tmp_path / "plain.swc", branch_types=False).read_text()
    relaxed = write_swc(planted, tmp_path / "relaxed.swc", branch_types=False,
                        relax=0.5, relax_bound="radius").read_text()
    assert "# POSITIONS viking " in plain and "RELAXED_STATS" not in plain
    assert "# POSITIONS relaxed f=0.50 bound=radius:" in relaxed
    assert "# RELAXED_STATS " in relaxed and " nodes moved, median " in relaxed
    assert "relax=0.5 relax_bound=radius" in relaxed

    def rows(text):
        return np.array([line.split() for line in text.splitlines()
                         if line and not line.startswith("#")], dtype=float)

    a, b = rows(plain), rows(relaxed)
    assert a.shape == b.shape
    for column in (0, 1, 4, 5, 6):                       # n, T, z, R, P: untouched
        assert np.array_equal(a[:, column], b[:, column])
    assert not np.allclose(a[:, 2:4], b[:, 2:4])         # x, y: moved


def test_bracket_is_on_request(planted):
    assert morphometrics(planted).length_floor_um is None
    m = morphometrics(planted, bracket=True)
    assert m.length_floor_um < m.total_length_um
    assert m.length_floor_by_radius_um < m.total_length_um


# -- lamellae (sbfsem.analysis.lamellae) ---------------------------------------

def rectangle(cx, cy, long, short, n=40):
    """A long-by-short rectangle centered on (cx, cy), long along x, as a ring."""
    a, b = long / 2, short / 2
    top = np.column_stack([np.linspace(-a, a, n), np.full(n, b)])
    right = np.column_stack([np.full(n, a), np.linspace(b, -b, n)])
    bottom = np.column_stack([np.linspace(a, -a, n), np.full(n, -b)])
    left = np.column_stack([np.full(n, -a), np.linspace(-b, b, n)])
    return np.vstack([top, right, bottom, left]) + [cx, cy]


def with_outlines(points, links, rings):
    """``rings``: Location ID -> outline; every such annotation is a closed curve."""
    cell = build(points, links)
    cell.nodes.loc[cell.nodes["ID"].isin(rings), "TypeCode"] = 6
    cell.geometries = pd.DataFrame({
        "ID": list(rings), "ParentID": [1] * len(rings),
        "Z": [points[i][2] for i in rings], "Zum": [points[i][2] * 0.05 for i in rings],
        "Curve": [[r] for r in rings.values()], "Registered": [True] * len(rings),
    })
    return cell


def test_medial_chain_measures_a_slab_and_follows_a_bend():
    from sbfsem.analysis.lamellae import medial_chain

    chain, widths, full = medial_chain(rectangle(0, 0, 6.0, 1.0), 0.0)
    length = float(np.linalg.norm(np.diff(chain, axis=0), axis=1).sum())
    # Trimmed by half the width at each end: 6 - 2 * 0.5.
    assert length == pytest.approx(5.0, abs=0.15)
    assert np.allclose(chain[:, 1], 0.0, atol=1e-6) and np.all(np.abs(chain[:, 0]) <= 2.55)
    assert widths.min() == pytest.approx(1.0, abs=0.02)

    # A crescent: the chain follows the bend, so it is longer than the chord
    # and every node lies inside the profile.
    a = np.linspace(-1.2, 1.2, 80)
    crescent = np.vstack([np.column_stack([5 * np.cos(a), 5 * np.sin(a)]),
                          np.column_stack([4 * np.cos(a[::-1]), 4 * np.sin(a[::-1])])])
    chain, _, _ = medial_chain(crescent, 0.0)
    chord = float(np.linalg.norm(chain[-1] - chain[0]))
    length = float(np.linalg.norm(np.diff(chain, axis=0), axis=1).sum())
    assert length > chord * 1.05
    radii = np.linalg.norm(chain[:, :2], axis=1)
    assert np.all((radii > 4.05) & (radii < 4.95))          # on the crescent's mid-line
    assert not _inside_polygon([3.2, 0.0], crescent)         # its centroid is not


def test_lamellae_are_found_stacked_and_measured_against_what_they_replace():
    from sbfsem.analysis.lamellae import find_lamellae

    # A 6 x 1 um slab on three consecutive sections (one lamella), linked
    # down through its centers; a thin branch leaves each end; the soma
    # below; a round profile (not elongated) beside it.
    points = {1: (0.0, 0.0, 0, 3.0), 10: (0.0, 5.0, 4, 0.9), 11: (0.0, 5.0, 5, 0.9),
              12: (0.0, 5.0, 6, 0.9), 20: (4.0, 5.0, 7, 0.2), 21: (-4.0, 5.0, 7, 0.2),
              30: (0.0, 9.0, 8, 0.6), 31: (0.0, 9.0, 9, 0.6)}
    links = [(1, 10), (10, 11), (11, 12), (12, 20), (12, 21), (12, 30), (30, 31)]
    rings = {i: rectangle(0.0, 5.0, 6.0, 1.0) for i in (10, 11, 12)}
    theta = np.linspace(0, 2 * np.pi, 60, endpoint=False)
    rings[30] = np.column_stack([0.6 * np.cos(theta), 9 + 0.6 * np.sin(theta)])
    rings[31] = rings[30].copy()
    cell = with_outlines(points, links, rings)

    report = find_lamellae(cell)
    assert report.n_elongated == 3 and len(report.lamellae) == 1 and report.n_stacks == 1
    (lam,) = report.lamellae
    assert lam.location_ids == (10, 11, 12) and lam.sections == (4, 6)
    assert lam.aspect == pytest.approx(6.0, abs=0.05) and lam.is_branch_point
    assert lam.cable_um == pytest.approx(5.0, abs=0.15)
    # Replaced: the two 0.05 um links inside the stack, the part of each
    # branch link inside the slab (3 of its 4 um, times the 3-D length), and
    # the links to the soma and the round profile (start inside the slab).
    assert lam.replaced_um > 6.0
    attached = dict(lam.attachments)
    assert attached[20] == len(lam.chain_um) - 1 and attached[21] == 0   # each branch to its own end
    assert lam.beyond_um == pytest.approx(0.0, abs=1e-6)
    assert lam.net_change_um < 0             # a slab with branches at both ends: the chain is shorter than the spokes were


def test_the_check_lists_lamellae(planted):
    check = check_skeleton(planted)
    assert "lamella" in check.counts() and "lamella_net_um" in check.summary
    assert check.summary["n_elongated_profiles"] == 0      # circles only


# -- the modal radius and the sphere network (sbfsem.analysis.thickness) -------

def crescent(outer=5.0, inner=4.0, reach=1.2, n=80):
    a = np.linspace(-reach, reach, n)
    return np.vstack([np.column_stack([outer * np.cos(a), outer * np.sin(a)]),
                      np.column_stack([inner * np.cos(a[::-1]), inner * np.sin(a[::-1])])])


def test_modal_radius_is_a_thickness_on_a_circle_a_slab_and_a_crescent():
    from sbfsem.analysis.thickness import inscribed_circle, modal_radius

    theta = np.linspace(0, 2 * np.pi, 60, endpoint=False)
    assert modal_radius(np.column_stack([0.6 * np.cos(theta), 0.6 * np.sin(theta)])) \
        == pytest.approx(0.6, abs=0.01)
    assert modal_radius(rectangle(0, 0, 6.0, 1.0)) == pytest.approx(0.5, abs=0.02)
    # A crescent 1 um thick bent round a 4.5 um radius: its half-thickness,
    # not its bend.  Its centroid lies outside it; the inscribed circle's
    # center does not.
    ring = crescent()
    center, r_in = inscribed_circle(ring)
    assert _inside_polygon(center, ring) and r_in == pytest.approx(0.5, abs=0.01)
    assert not _inside_polygon(ring.mean(axis=0), ring)
    assert modal_radius(ring) == pytest.approx(0.5, abs=0.02)
    # The smoothing is not what decides it.
    assert modal_radius(rectangle(0, 0, 6.0, 1.0), bandwidth_um=0.01) == pytest.approx(
        modal_radius(rectangle(0, 0, 6.0, 1.0), bandwidth_um=0.03), abs=0.02)


def test_sphere_network_keeps_the_farthest_of_an_unbroken_run():
    from sbfsem.analysis.thickness import sphere_network

    line = np.column_stack([np.arange(21) * 0.1, np.zeros(21), np.zeros(21)])
    kept = sphere_network(line, np.full(21, 0.25))            # touching up to 0.5 um apart
    assert list(kept) == [0, 5, 10, 15, 20]
    # Out and back within one sphere: the farthest point by distance is the
    # turn, which is kept, so the excursion is not cut off (the farthest
    # touching *index* would have jumped straight to the end).
    out = [(0.1 * k, 0.0) for k in range(11)] + [(1.0 - 0.1 * k, 0.05) for k in range(1, 11)]
    path = np.column_stack([np.array(out), np.zeros(len(out))])
    assert 10 in sphere_network(path, np.full(len(out), 0.6))
    # Spheres that do not touch keep every point.
    assert list(sphere_network(line, np.full(21, 0.01))) == list(range(21))


# -- the export options (2026-09-25) --------------------------------------------

def slab_cell(branch_mid=False):
    """A 6 x 1 um slab on sections 4-6 over the soma; either a thin branch off
    each end, or (``branch_mid``) one process leaving the middle upward, which
    leaves both ends of the chain free."""
    points = {1: (0.0, 0.0, 0, 3.0), 10: (0.0, 5.0, 4, 0.9), 11: (0.0, 5.0, 5, 0.9),
              12: (0.0, 5.0, 6, 0.9)}
    links = [(1, 10), (10, 11), (11, 12)]
    rings = {i: rectangle(0.0, 5.0, 6.0, 1.0) for i in (10, 11, 12)}
    if branch_mid:
        points.update({40: (0.2, 5.1, 7, 0.2), 41: (0.2, 5.1, 8, 0.2)})
        links += [(12, 40), (40, 41)]
    else:
        points.update({20: (4.0, 5.0, 7, 0.2), 21: (-4.0, 5.0, 7, 0.2),
                       30: (0.0, 9.0, 8, 0.6), 31: (0.0, 9.0, 9, 0.6)})
        links += [(12, 20), (12, 21), (12, 30), (30, 31)]
        theta = np.linspace(0, 2 * np.pi, 60, endpoint=False)
        rings[30] = np.column_stack([0.6 * np.cos(theta), 9 + 0.6 * np.sin(theta)])
        rings[31] = rings[30].copy()
    return with_outlines(points, links, rings)


def rows_of(text):
    return np.array([line.split() for line in text.splitlines()
                     if line and not line.startswith("#")], dtype=float)


def test_lamella_swc_writes_the_chain_in_place_of_the_stack(tmp_path):
    from sbfsem.analysis.lamellae import find_lamellae
    from sbfsem.export.swc import tree_length

    cell = slab_cell()
    (lam,) = find_lamellae(cell).lamellae
    plain = swc_table(cell, branch_types=False)
    chained = swc_table(cell, branch_types=False, lamellae=True)
    ids = set(chained["ID"])
    assert not ids & {10, 11, 12} and {1, 20, 21, 30, 31} <= ids
    assert (chained["ID"] < 0).sum() == len(lam.chain_um)
    assert (chained.loc[chained["P"] != -1, "P"] < chained.loc[chained["P"] != -1, "n"]).all()
    # What the file gains is what the lamella says an SWC gains.
    assert tree_length(chained) - tree_length(plain) == pytest.approx(lam.written_change_um)
    # Each branch leaves its own end of the chain; the round profile, the
    # chain node nearest it.
    parent = dict(zip(chained["n"], chained["ID"], strict=True))
    by_id = chained.set_index("ID")
    assert parent[by_id.loc[20, "P"]] < 0 and parent[by_id.loc[21, "P"]] < 0
    chain_rows = chained[chained["ID"] < 0]
    assert chain_rows["R"].to_numpy() == pytest.approx(0.5, abs=0.02)     # half the width

    text = write_swc(cell, tmp_path / "c.swc", branch_types=False, lamellae=True).read_text()
    assert "# LAMELLAE 1 stacks written as chains in place of their 3 annotations: " in text
    assert "# LAMELLA_RULE aspect_min=2.00: " in text
    assert "# LAMELLA 1 locations 10,11,12 sections 4-6 rows " in text
    assert "# SAMPLING annotations: one row per annotation, a lamella stack's being its chain" in text
    assert "lamellae=true" in text
    with pytest.raises(ValueError):
        write_swc(cell, tmp_path / "x.swc", lamellae=True, relax=0.5)


def test_a_chain_past_its_last_branch_ends_in_tips():
    cell = slab_cell(branch_mid=True)
    chained = swc_table(cell, branch_types=True, lamellae=True)
    children = chained["P"].value_counts()
    tips = chained[~chained["n"].isin(children.index)]
    # The process's own tip, and both flat ends of the chain.
    assert len(tips) == 3 and (tips["ID"] < 0).sum() == 2
    assert (tips["T"] == 6).all()


def test_sphere_sampled_swc_keeps_tips_branch_points_and_dropped_link_ends(planted, tmp_path):
    from sbfsem.export.swc import tree_length

    plain = swc_table(planted, branch_types=False)
    thin = swc_table(planted, branch_types=False, sampling="sphere")
    assert len(thin) < len(plain)
    assert (thin.loc[thin["P"] != -1, "P"] < thin.loc[thin["P"] != -1, "n"]).all()
    assert (thin["P"] == -1).sum() == (plain["P"] == -1).sum()

    def tips(table):
        return set(table.loc[~table["n"].isin(table["P"]), "ID"])

    assert tips(thin) == tips(plain)
    (a, b), = dropped_links(planted)
    assert {a, b} <= set(thin["ID"])
    assert tree_length(thin) <= tree_length(plain)

    text = write_swc(planted, tmp_path / "c.swc", branch_types=False, sampling="sphere").read_text()
    assert f"# SAMPLING sphere: {len(thin)} of {len(plain)} rows kept" in text
    row_of = dict(zip(thin["ID"], thin["n"], strict=True))
    assert f"# DROPPED_LINK {a} {b} rows {row_of[a]} {row_of[b]}" in text
    assert "sampling=sphere" in text


def test_modal_radius_swc_lists_the_stored_radius_beside_it(tmp_path):
    cell = slab_cell()
    plain = write_swc(cell, tmp_path / "a.swc", branch_types=False).read_text()
    modal = write_swc(cell, tmp_path / "b.swc", branch_types=False, radius="modal").read_text()
    assert "# R_COLUMN stored: " in plain and "R_STORED" not in plain
    assert "# R_COLUMN modal: " in modal and "radius=modal" in modal
    table = swc_table(cell, branch_types=False, radius="modal").set_index("ID")
    assert table.loc[[10, 11, 12], "R"].to_numpy() == pytest.approx(0.5, abs=0.02)
    assert table.loc[30, "R"] == pytest.approx(0.6, abs=0.01)
    assert table.loc[[1, 20, 21], "R"].tolist() == [3.0, 0.2, 0.2]          # circles: stored
    stored = {int(line.split()[2]): float(line.split()[3])
              for line in modal.splitlines() if line.startswith("# R_STORED ")}
    assert stored == {int(table.loc[i, "n"]): pytest.approx(r)
                      for i, r in ((10, 0.9), (11, 0.9), (12, 0.9), (30, 0.6), (31, 0.6))}
    # Positions, types and parents are the default export's.
    a, b = rows_of(plain), rows_of(modal)
    for column in (0, 1, 2, 3, 4, 6):
        assert np.array_equal(a[:, column], b[:, column])
