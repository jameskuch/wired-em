from __future__ import annotations

import httpx
import numpy as np
import pandas as pd
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.odata import ODataClient
from sbfsem.network.graph import (
    ConnectivityNetwork,
    cytoscape_elements,
    degree_table,
    network_from_links,
)
from sbfsem.network.links import all_linked_neurons, count_linked_neurons, linked_neurons
from sbfsem.network.metrics import adjacency_matrix, laplacian_matrix, summary


@pytest.fixture
def links():
    return pd.DataFrame(
        {
            "SourceID": [1, 1, 2, 3, 3, 3],
            "TargetID": [2, 2, 3, 1, 1, 2],
            "SynapseID": [10, 11, 12, 13, 14, 15],
            "SynapseType": ["RibbonPre"] * 2 + ["ConvPre", "GapJunction", "GapJunction", "ConvPre"],
        }
    )


@pytest.fixture
def network(links):
    return network_from_links(links)


def test_network_weights_parallel_synapses(network):
    assert network.neuron_ids == [1, 2, 3]
    assert network.weight(1, 2) == 2
    assert network.weight(3, 1) == 2
    assert network.weight(2, 1) == 0  # directed
    assert set(network.graph[1][2]["types"]) == {"RibbonPre"}


def test_network_is_keyed_by_structure_id(links):
    """F017: MATLAB passed raw Viking IDs to digraph as node *indices*."""
    big = links.assign(SourceID=links["SourceID"] + 100_000,
                       TargetID=links["TargetID"] + 100_000)
    net = network_from_links(big)
    assert net.graph.number_of_nodes() == 3  # not 100_003
    assert net.neuron_ids == [100_001, 100_002, 100_003]


def test_neighbors_and_adjacency(network):
    assert network.neighbors(1) == [2, 3]
    assert network.neighbors(1, "out") == [2]
    assert network.neighbors(1, "in") == [3]
    assert network.neighbors(999) == []
    matrix, ids = network.adjacency()
    assert matrix.shape == (3, 3)
    assert matrix[ids.index(1), ids.index(2)] == 2


def test_empty_network():
    net = network_from_links(pd.DataFrame())
    assert net.neuron_ids == []
    assert degree_table(net).empty
    assert cytoscape_elements(net) == []


def test_degree_table(network):
    frame = degree_table(network)
    assert set(frame["NeuronID"]) == {1, 2, 3}
    row = frame[frame["NeuronID"] == 2].iloc[0]
    assert row["InDegree"] == 2 and row["OutDegree"] == 1
    assert row["InWeight"] == 3  # 2 from c1, 1 from c3


def test_cytoscape_elements(network):
    elements = cytoscape_elements(network, labels={1: "ON parasol"}, loaded=[1])
    nodes = [e for e in elements if "source" not in e["data"]]
    edges = [e for e in elements if "source" in e["data"]]
    assert len(nodes) == 3 and len(edges) == 4
    assert nodes[0]["data"]["label"] == "ON parasol"
    assert nodes[0]["data"]["vikingLabel"] == "ON parasol"
    assert nodes[0]["data"]["loaded"] is True
    assert nodes[1]["data"]["label"] == "c2"
    assert nodes[1]["data"]["vikingLabel"] == ""  # so a viewer can show IDs alone
    assert nodes[1]["data"]["loaded"] is False
    edge = next(e for e in edges if e["data"]["id"] == "e1-2")
    assert edge["data"]["weight"] == 2
    assert edge["data"]["primaryType"] == "RibbonPre"
    assert edge["data"]["typeCounts"] == {"RibbonPre": 2}
    assert edge["data"]["directed"] is True
    # A gap junction has no pre or post side: no arrow for the viewer to draw.
    gap = next(e for e in edges if e["data"]["id"] == "e3-1")
    assert gap["data"]["typeCounts"] == {"GapJunction": 2}
    assert gap["data"]["directedTypes"] == [] and gap["data"]["directed"] is False


def test_cytoscape_primary_type_is_the_commonest():
    """Alphabetical order used to pick it; three adherens beat one ribbon."""
    links = pd.DataFrame(
        {
            "SourceID": [1, 1, 1, 1],
            "TargetID": [2, 2, 2, 2],
            "SynapseID": [10, 11, 12, 13],
            "SynapseType": ["RibbonPre", "Desmosome", "Desmosome", "Desmosome"],
        }
    )
    edge = [e for e in cytoscape_elements(network_from_links(links)) if "source" in e["data"]][0]
    assert edge["data"]["types"] == ["Desmosome", "RibbonPre"]
    assert edge["data"]["primaryType"] == "Desmosome"
    assert edge["data"]["typeCounts"] == {"Desmosome": 3, "RibbonPre": 1}
    # Mixed: the ribbon synapse gives the pair a direction.
    assert edge["data"]["directedTypes"] == ["RibbonPre"] and edge["data"]["directed"] is True


def test_directedness_by_name_for_the_odd_types():
    from sbfsem.network.graph import _is_directed

    assert _is_directed("PlaqueLikePre") and _is_directed("CisternPost")
    assert _is_directed("ConvPre") and _is_directed("RibbonPost")
    assert not _is_directed("GapJunction") and not _is_directed("Desmosome")
    assert not _is_directed("Touch") and not _is_directed("Unknown")
    assert not _is_directed("SomethingViking")


def test_metrics(network):
    matrix, ids = adjacency_matrix(network.graph, weighted=True)
    assert matrix.sum() == 6
    laplacian, ids = laplacian_matrix(network.graph)
    # A Laplacian's rows sum to zero.
    np.testing.assert_allclose(laplacian.sum(axis=1), 0)
    stats = summary(network.graph)
    assert stats["n_nodes"] == 3
    assert stats["n_components"] == 1
    assert summary(network_from_links(pd.DataFrame()).graph)["n_nodes"] == 0


# -- link resolution -------------------------------------------------------
def _client(handler, tmp_path):
    return ODataClient(
        "i", client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=DiskCache(tmp_path, enabled=False),
    )


def _fake_neuron(synapse_type: str):
    from sbfsem.core.structure import Neuron

    nodes = pd.DataFrame(
        {
            "ID": [1, 2], "ParentID": [100, 200],
            "VolumeX": [0.0, 1.0], "VolumeY": [0.0, 1.0], "Z": [1, 2],
            "Radius": [1.0, 1.0], "X": [0.0, 0.0], "Y": [0.0, 0.0],
            "OffEdge": [False, False], "Terminal": [False, False], "TypeCode": [1, 1],
        }
    )
    type_id = {"RibbonPre": 73, "RibbonPost": 35, "GapJunction": 28}[synapse_type]
    tags = "Bipolar;Ribbon;Glutamate" if synapse_type == "RibbonPost" else ""
    synapses = pd.DataFrame(
        {"ID": [200], "ParentID": [100], "TypeID": [type_id], "Tags": [tags], "Label": [""]}
    )
    return Neuron(100, "i", nodes=nodes, synapses=synapses)


def test_presynaptic_links_read_the_target(tmp_path):
    def handler(request):
        if "SourceID" in str(request.url):
            return httpx.Response(200, json={"value": [
                {"SourceID": 200, "TargetID": 900,
                 "Source": {"ID": 200, "ParentID": 100},
                 "Target": {"ID": 900, "ParentID": 555}},
            ]})
        return httpx.Response(200, json={"value": []})

    frame = linked_neurons(_fake_neuron("RibbonPre"), "RibbonPre", _client(handler, tmp_path))
    assert frame["NeuronID"].tolist() == [555]
    assert frame["Direction"].tolist() == ["pre"]


def test_postsynaptic_links_read_the_source(tmp_path):
    def handler(request):
        if "TargetID" in str(request.url):
            return httpx.Response(200, json={"value": [
                {"SourceID": 900, "TargetID": 200,
                 "Source": {"ID": 900, "ParentID": 777},
                 "Target": {"ID": 200, "ParentID": 100}},
            ]})
        return httpx.Response(200, json={"value": []})

    frame = linked_neurons(_fake_neuron("RibbonPost"), "RibbonPost", _client(handler, tmp_path))
    assert frame["NeuronID"].tolist() == [777]
    assert frame["Direction"].tolist() == ["post"]


def test_undirected_links_match_from_both_ends(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"value": [
            {"SourceID": 200, "TargetID": 900,
             "Source": {"ID": 200, "ParentID": 100},
             "Target": {"ID": 900, "ParentID": 321}},
        ]})

    frame = linked_neurons(
        _fake_neuron("GapJunction"), "GapJunction", _client(handler, tmp_path)
    )
    assert 321 in frame["NeuronID"].tolist()


def test_unlinked_synapses_are_reported(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"value": []})

    client = _client(handler, tmp_path)
    frame = linked_neurons(_fake_neuron("RibbonPre"), "RibbonPre", client)
    assert frame["NeuronID"].isna().all()
    assert frame["SynapseID"].tolist() == [200]
    assert linked_neurons(
        _fake_neuron("RibbonPre"), "RibbonPre", client, include_unlinked=False
    ).empty


def test_missing_synapse_type_returns_empty(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"value": []})

    assert linked_neurons(
        _fake_neuron("RibbonPre"), "ConvPost", _client(handler, tmp_path)
    ).empty


def test_all_linked_neurons_on_a_synapse_free_neuron(tmp_path):
    """F021: MATLAB assigned a struct field to [] and errored before the guard."""
    from sbfsem.core.structure import Neuron

    nodes = pd.DataFrame(
        {
            "ID": [1], "ParentID": [100], "VolumeX": [0.0], "VolumeY": [0.0], "Z": [1],
            "Radius": [1.0], "X": [0.0], "Y": [0.0],
            "OffEdge": [False], "Terminal": [False], "TypeCode": [1],
        }
    )

    def handler(request):
        return httpx.Response(200, json={"value": []})

    frame = all_linked_neurons(Neuron(100, "i", nodes=nodes), _client(handler, tmp_path))
    assert frame.empty
    assert list(frame.columns)[:2] == ["NeuronID", "SynapseID"]


def test_labels_are_batched(tmp_path):
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append(url)
        if "StructureLinks" in url and "SourceID" in url:
            return httpx.Response(200, json={"value": [
                {"SourceID": 200, "TargetID": 900,
                 "Source": {"ID": 200, "ParentID": 100},
                 "Target": {"ID": 900, "ParentID": 555}},
            ]})
        if "Structures?$filter" in url:
            return httpx.Response(200, json={"value": [{"ID": 555, "Label": "ON parasol"}]})
        return httpx.Response(200, json={"value": []})

    frame = all_linked_neurons(_fake_neuron("RibbonPre"), _client(handler, tmp_path))
    assert frame["NeuronLabel"].tolist() == ["ON parasol"]
    label_calls = [c for c in calls if "Structures?$filter" in c]
    assert len(label_calls) == 1  # one batched query, not one per partner


def test_count_linked_neurons():
    links = pd.DataFrame(
        {
            "NeuronID": [1.0, 1.0, 2.0, np.nan],
            "SynapseID": [10, 11, 12, 13],
            "SynapseType": ["RibbonPre"] * 3 + ["ConvPre"],
            "NeuronLabel": ["a", "a", "b", ""],
            "Direction": ["pre"] * 4,
        }
    )
    counts = count_linked_neurons(links)
    assert counts.iloc[0]["Count"] == 2
    assert counts.iloc[0]["NeuronID"] == 1
    assert len(counts) == 2  # the NaN partner is dropped
    assert count_linked_neurons(pd.DataFrame()).empty


def test_connectivity_network_dataclass_is_constructible():
    import networkx as nx

    net = ConnectivityNetwork(nx.DiGraph())
    assert net.neuron_ids == []
    assert net.to_undirected().number_of_nodes() == 0


# -- the linked-neurons report ---------------------------------------------
def _report_neuron():
    """c100 with two synapses: c200 (ribbon, at a known position) and c201 (conv)."""
    from sbfsem.core.structure import Neuron

    nodes = pd.DataFrame(
        {
            "ID": [1, 2, 3], "ParentID": [100, 200, 201],
            "VolumeX": [0.0, 400.0, 800.0], "VolumeY": [0.0, 800.0, 1600.0], "Z": [1, 4, 8],
            "Radius": [1.0, 1.0, 1.0], "X": [0.0, 0.0, 0.0], "Y": [0.0, 0.0, 0.0],
            "OffEdge": [False] * 3, "Terminal": [False] * 3, "TypeCode": [1, 1, 1],
        }
    )
    synapses = pd.DataFrame(
        {"ID": [200, 201], "ParentID": [100, 100], "TypeID": [73, 35],
         "Tags": ["", ""], "Label": ["", ""]}
    )
    return Neuron(100, "i", nodes=nodes, synapses=synapses)


def _report_links():
    return pd.DataFrame(
        {
            "NeuronID": [555.0, float("nan"), 777.0],
            "SynapseID": [200, 200, 201],
            "SynapseType": ["RibbonPre", "RibbonPre", "ConvPost"],
            "NeuronLabel": ["ON parasol", "", "AC"],
            "Direction": ["pre", "pre", "post"],
        }
    )


def test_report_attaches_synapse_positions_in_microns():
    from sbfsem.network.links import REPORT_COLUMNS, link_report

    neuron = _report_neuron()
    table = link_report(neuron, _report_links())
    assert list(table.columns) == REPORT_COLUMNS
    row = table[table["SynapseID"] == 200].iloc[0]
    node = neuron.nodes[neuron.nodes["ParentID"] == 200].iloc[0]
    assert row["SynapseX_um"] == pytest.approx(node["Xum"])
    assert row["SynapseY_um"] == pytest.approx(node["Yum"])
    assert row["SynapseZ_um"] == pytest.approx(node["Zum"])
    assert row["SynapseX_um"] > 0  # VolumeX 400 px is a real distance, not 0


def test_report_orders_like_matlab_or_like_the_sorted_script():
    from sbfsem.network.links import link_report, sort_links

    neuron = _report_neuron()
    by_neuron = link_report(neuron, _report_links(), "neuron")
    # getAllLinkedNeurons: by partner, unlinked (NaN) last.
    assert by_neuron["NeuronID"].tolist()[:2] == [555.0, 777.0]
    assert pd.isna(by_neuron["NeuronID"].iloc[-1])
    by_type = link_report(neuron, _report_links(), "type")
    # getAllLinkedNeurons_sorted: SynapseType, NeuronLabel, NeuronID, SynapseID.
    assert by_type["SynapseType"].tolist() == ["ConvPost", "RibbonPre", "RibbonPre"]
    assert by_type["NeuronLabel"].tolist() == ["AC", "", "ON parasol"]
    with pytest.raises(ValueError):
        sort_links(_report_links(), "random")


def test_report_files_round_trip(tmp_path):
    import io

    from sbfsem.network.links import link_report, links_to_csv, links_to_xlsx

    table = link_report(_report_neuron(), _report_links())
    csv = links_to_csv(table)
    lines = csv.splitlines()
    # The MATLAB columns first and in their order; the tag columns appended.
    assert lines[0] == ("NeuronID,SynapseID,SynapseType,NeuronLabel,SynapseX_um,SynapseY_um,"
                        "SynapseZ_um,Direction,Tags,TagStatus,SuggestedTag,LocationID")
    assert lines[1].startswith("555,200,RibbonPre,ON parasol,")
    assert any(line.startswith(",200,RibbonPre,,") for line in lines)  # unlinked: blank, not nan
    back = pd.read_csv(io.StringIO(csv))
    assert len(back) == 3

    xlsx = links_to_xlsx(table, sheet_name="c100 NeitzInferiorMonkey")
    assert xlsx[:2] == b"PK"
    sheet = pd.read_excel(io.BytesIO(xlsx))
    assert list(sheet.columns) == list(table.columns)
    assert sheet["SynapseID"].tolist() == table["SynapseID"].tolist()


def test_empty_report_keeps_its_columns():
    from sbfsem.network.links import LINK_COLUMNS, REPORT_COLUMNS, link_report, links_to_csv

    table = link_report(_report_neuron(), pd.DataFrame(columns=LINK_COLUMNS))
    assert list(table.columns) == REPORT_COLUMNS and table.empty
    assert links_to_csv(table).splitlines() == [",".join(REPORT_COLUMNS)]
