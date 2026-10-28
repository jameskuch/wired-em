"""Where a cell crosses its volume's damaged sections (sbfsem.analysis.damaged)."""

from __future__ import annotations

import pandas as pd
import pytest

from sbfsem.analysis.damaged import DamagedCrossing, crossings, find
from sbfsem.core.structure import Neuron


def cell(z, links, child_z=(), xy=None, radius=None) -> Neuron:
    """Annotations 1.. of c9 on sections ``z``, then a child structure's on ``child_z``."""
    n, m = len(z), len(child_z)
    nodes = pd.DataFrame({
        "ID": list(range(1, n + m + 1)),
        "ParentID": [9] * n + [77] * m,
        "VolumeX": [p[0] for p in xy] if xy else [0.0] * (n + m),
        "VolumeY": [p[1] for p in xy] if xy else [0.0] * (n + m),
        "Z": list(z) + list(child_z),
        "Radius": list(radius) if radius is not None else [10.0] * (n + m),
        "X": [0.0] * (n + m), "Y": [0.0] * (n + m),
        "OffEdge": [False] * (n + m), "Terminal": [False] * (n + m),
        "TypeCode": [1] * (n + m),
    })
    a, b = zip(*links, strict=True) if links else ((), ())
    edges = pd.DataFrame({"ID": [9] * len(a), "A": list(a), "B": list(b)})
    return Neuron(9, "i", nodes=nodes, edges=edges, volume_scale=(7.5, 7.5, 90.0), omitted_ids=[])


def test_an_annotation_on_a_damaged_section_crosses_it():
    neuron = cell([10, 11, 11, 12], [(1, 2), (2, 4), (1, 3)])
    assert crossings(neuron, [11, 40]) == [DamagedCrossing(11, 2, 0)]


def test_a_link_over_a_damaged_section_crosses_it_without_an_annotation():
    # InferiorMonkey's hole: no annotation on 916-935, a link of 21 sections over it.
    neuron = cell([914, 915, 936, 937], [(1, 2), (2, 3), (3, 4)])
    found = crossings(neuron, range(915, 936))
    assert found[0] == DamagedCrossing(915, 1, 0)
    assert [c.z for c in found[1:]] == list(range(916, 936))
    assert {(c.n_annotations, c.n_links_over) for c in found[1:]} == {(0, 1)}


def test_a_link_stored_both_ways_counts_once_and_its_ends_are_not_over():
    neuron = cell([10, 13], [(1, 2), (2, 1)])
    assert crossings(neuron, [10, 11, 12, 13]) == [
        DamagedCrossing(10, 1, 0), DamagedCrossing(11, 0, 1),
        DamagedCrossing(12, 0, 1), DamagedCrossing(13, 1, 0)]


def test_a_damaged_section_the_cell_only_spans_is_not_crossed():
    # Two branches: 10-12 and 20-22, unlinked; 15 lies between them.
    neuron = cell([10, 12, 20, 22], [(1, 2), (3, 4)])
    assert crossings(neuron, [15]) == []
    assert crossings(neuron, [11, 21]) == [DamagedCrossing(11, 0, 1), DamagedCrossing(21, 0, 1)]


def test_a_child_structures_annotations_do_not_count():
    neuron = cell([10, 12], [(1, 2)], child_z=[30])
    assert crossings(neuron, [30]) == []


def test_no_list_or_no_annotations_crosses_nothing():
    assert crossings(cell([10, 11], [(1, 2)]), []) == []
    assert crossings(cell([], []), [10]) == []


def test_a_mark_is_the_annotation_on_the_section_or_the_link_where_it_passes():
    # 7.5 nm pixels, 90 nm sections: c9 on 10 at x 0, on 14 at x 4 um, radius 0.3 then 0.7 um.
    neuron = cell([10, 14], [(1, 2)], xy=[(0.0, 0.0), (4000 / 7.5, 0.0)],
                  radius=[300 / 7.5, 700 / 7.5])
    found, marks = find(neuron, [10, 11, 13])
    assert [(c.z, c.n_annotations, c.n_links_over) for c in found] == [(10, 1, 0), (11, 0, 1), (13, 0, 1)]
    assert [(m.z, m.over) for m in marks] == [(10, False), (11, True), (13, True)]
    on, first, last = marks
    assert on.xyz_um == pytest.approx((0.0, 0.0, 10 * 0.09))
    assert on.radius_um == pytest.approx(0.3)
    # A quarter of the way from 10 to 14, then three quarters: in the section's plane.
    assert first.xyz_um == pytest.approx((1.0, 0.0, 11 * 0.09))
    assert first.radius_um == pytest.approx(0.4)
    assert last.xyz_um == pytest.approx((3.0, 0.0, 13 * 0.09))
    assert last.radius_um == pytest.approx(0.6)
    # As many marks as the counts say.
    assert len(marks) == sum(c.n_annotations + c.n_links_over for c in found)
