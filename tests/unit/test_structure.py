from __future__ import annotations

import numpy as np
import pytest

from sbfsem.core.structure import Neuron, SynapseNotFoundError
from sbfsem.core.types import StructureTypes
from sbfsem.io.legacy_json import read_legacy_json


def test_loads_fixture(c943):
    assert c943.id == 943
    assert c943.source == "NeitzInferiorMonkey"
    assert c943.volume_scale == (7.5, 7.5, 90.0)
    assert len(c943.nodes) == 199
    assert len(c943.edges) == 194
    assert c943.label == "ON mBC"


def test_daspect_matches_matlab(c943):
    np.testing.assert_array_equal(c943.get_daspect(), [12.0, 12.0, 1.0])


def test_cell_nodes_exclude_children(c943):
    assert len(c943.get_cell_nodes()) == 190
    assert set(c943.get_cell_nodes()["ParentID"]) == {943}


def test_soma_ignores_synapse_annotations(c943):
    # F019: the MATLAB search covered child structures too.
    soma_id = c943.get_soma_id()
    assert int(c943.nodes.loc[c943.nodes["ID"] == soma_id, "ParentID"].iloc[0]) == 943
    assert c943.get_soma_size(True) == pytest.approx(2 * c943.get_soma_size())
    assert c943.get_soma_xyz().shape == (3,)


def test_unique_marks_exactly_one_node_per_synapse(c943):
    # F016: floor(median(Z)) could match zero nodes or several.
    for syn_id in c943.synapses["ID"]:
        nodes = c943.get_synapse_nodes_by_id(int(syn_id))
        assert int(nodes["Unique"].sum()) == 1


def test_unique_marker_with_even_section_count():
    """The failing MATLAB case: Z = [10, 13] gives median 11.5, floor 11."""
    import pandas as pd

    nodes = pd.DataFrame(
        {
            "ID": [1, 2, 3],
            "ParentID": [100, 200, 200],
            "VolumeX": [0.0, 1.0, 2.0],
            "VolumeY": [0.0, 1.0, 2.0],
            "Z": [1, 10, 13],
            "Radius": [10.0, 1.0, 1.0],
            "X": [0.0, 0.0, 0.0],
            "Y": [0.0, 0.0, 0.0],
            "OffEdge": [False] * 3,
            "Terminal": [False] * 3,
            "TypeCode": [1, 1, 1],
        }
    )
    synapses = pd.DataFrame(
        {"ID": [200], "ParentID": [100], "TypeID": [35], "Tags": [""], "Label": [""]}
    )
    n = Neuron(100, "i", nodes=nodes, synapses=synapses)
    assert int(n.nodes["Unique"].sum()) == 1
    assert n.get_synapse_xyz("ConvPost").shape == (1, 3)


def test_synapse_types(c943):
    names = c943.synapse_names(as_str=True)
    assert names == ["RibbonPost", "Unknown"]
    assert c943.get_synapse_count("RibbonPost") == 2
    assert c943.get_synapse_count(StructureTypes.Unknown) == 2
    assert c943.get_synapse_count("GapJunction") == 0


def test_missing_synapse_returns_empty_not_nan(c943):
    # F131 family: MATLAB returned [NaN NaN NaN], defeating every isempty guard.
    xyz = c943.get_synapse_xyz("GapJunction")
    assert xyz.shape == (0, 3)
    with pytest.raises(SynapseNotFoundError):
        c943.get_synapse_xyz("GapJunction", strict=True)


def test_graph_is_keyed_by_location_id(c943):
    g = c943.graph()
    assert g.number_of_nodes() == 190
    assert g.number_of_edges() == 189
    assert c943.get_soma_id() in g


def test_weighted_graph_works_for_digraphs(c943):
    # F008/F009: weights were ignored for digraphs and computed unfiltered.
    g = c943.graph(directed=True, weighted=True)
    assert g.is_directed()
    weights = [d["weight"] for *_e, d in g.edges(data=True)]
    assert len(weights) == g.number_of_edges()
    assert all(w >= 0 for w in weights)


def test_graph_with_synapses_runs(c943):
    # F007: the MATLAB true-branch indexed a numeric column with a table.
    g = c943.graph(synapses=True)
    assert g.number_of_nodes() >= 190


def test_branch_nodes(c943):
    g = c943.graph()
    ends = [n for n, d in g.degree() if d == 1]
    frame = c943.get_branch_nodes(ends[0], ends[1])
    assert len(frame) > 1
    assert int(frame.iloc[0]["ID"]) == ends[0]
    with pytest.raises(ValueError):
        c943.get_branch_nodes(ends[0], -1)


def test_repeated_synapse_merge_does_not_break(i943_path):
    # F014: the second getSynapses concatenated mismatched tables.
    n = read_legacy_json(i943_path)
    synapses, child_nodes, child_edges = n.synapses.copy(), n.get_synapse_nodes(False), n.edges
    n.merge_child_data(synapses, child_nodes, child_edges)
    n.merge_child_data(synapses, child_nodes, child_edges)
    assert int(n.nodes["Unique"].sum()) == len(synapses)


def test_legacy_reader_recomputes_degenerate_coordinates(i943_path):
    stored = read_legacy_json(i943_path, trust_stored_um=True)
    fixed = read_legacy_json(i943_path)
    # Viking left X/Y at zero for child structures in the old export.
    assert np.allclose(stored.get_synapse_xyz("RibbonPost")[:, :2], 0.0)
    assert not np.allclose(fixed.get_synapse_xyz("RibbonPost")[:, :2], 0.0)
