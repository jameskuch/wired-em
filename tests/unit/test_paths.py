"""Graffinity's connectivity matrix and intermediate node table over Viking links."""

from __future__ import annotations

import httpx
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.odata import ODataClient
from sbfsem.network.paths import (
    LinkGraph,
    NeuronEdge,
    PathQuery,
    cell_metric,
    connectivity_matrix,
    find_paths,
    intermediate_node_table,
    neuron_edges_from_structure_links,
    synapse_type_name,
)


def _link(source_child, source_parent, target_child, target_parent, *, type_id=34,
          target_type_id=35, bidirectional=False, tags=""):
    return {
        "SourceID": source_child, "TargetID": target_child, "Bidirectional": bidirectional,
        "Source": {"ID": source_child, "ParentID": source_parent, "TypeID": type_id, "Tags": tags},
        "Target": {"ID": target_child, "ParentID": target_parent, "TypeID": target_type_id,
                   "Tags": ""},
    }


# The toy connectome every test below queries:
#   c100 "CBb4w" -ConvPre-> c200 "YAC Ai" -ConvPre-> c300 "GC ON"
#   c100 "CBb4w" -RibbonPre x2-> c300 "GC ON"
#   c200 "YAC Ai" <-GapJunction-> c201 "YAC"
#   c101 "CBb3n" -ConvPre-> c200 "YAC Ai"
LABELS = {100: "CBb4w", 101: "CBb3n", 200: "YAC Ai", 201: "YAC", 300: "GC ON"}
LINKS = [
    _link(1000, 100, 2000, 200),
    _link(2001, 200, 3000, 300),
    _link(1001, 100, 3001, 300, type_id=73),
    _link(1002, 100, 3002, 300, type_id=73),
    _link(2002, 200, 2010, 201, type_id=28, target_type_id=28, bidirectional=True),
    _link(1010, 101, 2003, 200),
]


def _links_touching(ids):
    ids = set(ids)
    return [row for row in LINKS
            if row["Source"]["ParentID"] in ids or row["Target"]["ParentID"] in ids]


def _graph():
    graph = LinkGraph()
    for neuron_id, label in LABELS.items():
        graph.set_node(neuron_id, label=label, type_id=1)
    return graph


def _expander(graph, calls=None):
    def expand(ids):
        if calls is not None:
            calls.append(sorted(ids))
        graph.add_edges(neuron_edges_from_structure_links(_links_touching(ids)))
        graph.mark_expanded(ids)
        for neuron_id in graph.unlabeled():
            graph.set_node(neuron_id, label=LABELS.get(neuron_id, ""))
    return expand


# -- edges from StructureLinks ---------------------------------------------
def test_synapse_type_names_follow_the_toolbox():
    assert synapse_type_name(34) == "ConvPre"
    assert synapse_type_name(34, "GABA") == "GABAPre"
    assert synapse_type_name(73) == "RibbonPre"
    assert synapse_type_name(28) == "GapJunction"
    assert synapse_type_name(85) == "Desmosome"
    assert synapse_type_name(None) == "Unknown"
    assert synapse_type_name(257) == "DistalJunction"  # Viking's own name
    assert synapse_type_name(99999) == "Type99999"


def test_edges_collapse_links_per_pair_and_type():
    edges = {e.key: e for e in neuron_edges_from_structure_links(LINKS)}
    assert edges[(100, 300, "RibbonPre")].n_synapses == 2
    assert edges[(100, 300, "RibbonPre")].links == [(1001, 3001), (1002, 3002)]
    assert edges[(100, 200, "ConvPre")].n_synapses == 1
    # A gap junction is one link but an edge in each direction.
    assert (200, 201, "GapJunction") in edges and (201, 200, "GapJunction") in edges
    assert edges[(201, 200, "GapJunction")].links == [(2010, 2002)]
    assert edges[(201, 200, "GapJunction")].bidirectional
    # A directed synapse is not reversed.
    assert (200, 100, "ConvPre") not in edges


def test_edges_skip_unparented_children_and_dedupe_repeats():
    rows = [_link(1, 10, 2, None), _link(1, 10, 2, 20), _link(1, 10, 2, 20)]
    edges = neuron_edges_from_structure_links(rows)
    assert len(edges) == 1 and edges[0].n_synapses == 1


def test_undirected_types_are_reversed_even_without_the_flag():
    """MATLAB's rule: Viking's Source/Target of a Touch/Unknown is arbitrary."""
    rows = [_link(1, 10, 2, 20, type_id=229, target_type_id=229, bidirectional=False)]
    keys = {e.key for e in neuron_edges_from_structure_links(rows)}
    assert keys == {(10, 20, "Touch"), (20, 10, "Touch")}


def test_self_link_is_one_edge():
    rows = [_link(1, 10, 2, 10, type_id=28, target_type_id=28, bidirectional=True)]
    assert len(neuron_edges_from_structure_links(rows)) == 1


def test_graph_merges_parallel_synapses():
    graph = LinkGraph()
    graph.add_edges([NeuronEdge(1, 2, "ConvPre", [(10, 20)])])
    graph.add_edges([NeuronEdge(1, 2, "ConvPre", [(10, 20), (11, 21)]),
                     NeuronEdge(1, 3, "RibbonPre", [(12, 30)])])
    assert len(graph.edges) == 2
    assert graph.edges[0].n_synapses == 2
    assert [e.target for e in graph.edges_from(1)] == [2, 3]
    assert graph.n_synapses == 3
    assert graph.unlabeled() == [1, 2, 3]


# -- path search -----------------------------------------------------------
def test_two_hop_query_expands_only_what_it_needs():
    graph = _graph()
    calls = []
    query = PathQuery(hops=2, start_ids=[100])
    result = find_paths(graph, query, _expander(graph, calls))
    # hop 0 expands the start; hop 1 expands the neurons it reached.
    assert calls == [[100], [200, 300]]
    assert result.expanded == 3
    paths = sorted(p[0::2] for p in result.paths)
    assert paths == [[100, 200, 201], [100, 200, 300]]
    assert not result.truncated and not result.frontier_capped

    # A second query reuses the expanded neurons.
    calls.clear()
    find_paths(graph, PathQuery(hops=1, start_ids=[100]), _expander(graph, calls))
    assert calls == []


def test_patterns_constrain_nodes_and_edges():
    graph = _graph()
    expand = _expander(graph)
    # Only conventional synapses on the first hop, only YAC-labelled middles.
    result = find_paths(graph, PathQuery(
        hops=2, start_ids=[100, 101], node_patterns=["CBb.*", "YAC.*", ".*"],
        edge_patterns=["ConvPre", ""],
    ), expand)
    assert sorted(p[0::2] for p in result.paths) == [
        [100, 200, 201], [100, 200, 300], [101, 200, 201], [101, 200, 300],
    ]
    # The end pattern applies to the last node.
    result = find_paths(graph, PathQuery(
        hops=2, start_ids=[100], node_patterns=["", "", "GC.*"]), expand)
    assert [p[0::2] for p in result.paths] == [[100, 200, 300]]
    # Patterns are whole-string: "YAC" does not match "YAC Ai".
    result = find_paths(graph, PathQuery(
        hops=2, start_ids=[100], node_patterns=["", "YAC", ""]), expand)
    assert result.paths == []
    # ...but case-insensitive.
    result = find_paths(graph, PathQuery(
        hops=1, start_ids=[100], node_patterns=["cbb4w", "gc on"]), expand)
    # One path per neuron-level edge: the two ribbon synapses are one edge.
    assert len(result.paths) == 1
    assert graph.edges[result.paths[0][1]].synapse_type == "RibbonPre"


def test_start_pattern_over_known_nodes():
    graph = _graph()
    result = find_paths(graph, PathQuery(hops=1, node_patterns=["CBb.*"]), _expander(graph))
    assert result.anchors == [100, 101]
    assert sorted(p[0::2] for p in result.paths) == [[100, 200], [100, 300], [101, 200]]


def test_upstream_search_anchors_the_end():
    """A ganglion cell's synapses are all inputs: ask what feeds it."""
    graph = _graph()
    calls = []
    result = find_paths(graph, PathQuery(
        hops=2, min_hops=1, start_ids=[300], direction="upstream"), _expander(graph, calls))
    assert result.anchors == [300]
    # The end is expanded first, then the neurons feeding it.
    assert calls == [[300], [100, 200]]
    # Every input to c300 within two hops: two direct, three through c200
    # (one of them over the gap junction from c201).
    assert sorted(p[0::2] for p in result.paths) == [
        [100, 200, 300], [100, 300], [101, 200, 300], [200, 300], [201, 200, 300]]
    # Paths read start -> end whichever way they were found.
    for path in result.paths:
        for i in range(1, len(path), 2):
            edge = graph.edges[path[i]]
            assert (edge.source, edge.target) == (path[i - 1], path[i + 1])
    # Patterns keep their positions: [0] is the first node, [hops] the anchor.
    result = find_paths(graph, PathQuery(
        hops=2, start_ids=[300], direction="upstream",
        node_patterns=["CBb3n", "", ""], edge_patterns=["", "ConvPre"]), _expander(graph))
    assert [p[0::2] for p in result.paths] == [[101, 200, 300]]
    with pytest.raises(ValueError):
        PathQuery(direction="sideways")


def test_paths_never_revisit_a_neuron():
    """An undirected link walked out and back is not a two-hop path."""
    graph = _graph()
    # c200 <-> c201 is a gap junction: 200 -> 201 -> 200 would otherwise count.
    result = find_paths(graph, PathQuery(hops=2, start_ids=[200]), _expander(graph))
    assert sorted(p[0::2] for p in result.paths) == []
    result = find_paths(graph, PathQuery(hops=2, min_hops=1, start_ids=[200]), _expander(graph))
    assert sorted(p[0::2] for p in result.paths) == [[200, 201], [200, 300]]
    # Upstream too: 300 <- 200 <- 201 <- 200 is refused, 300 <- 200 <- 201 kept.
    result = find_paths(graph, PathQuery(hops=3, min_hops=2, start_ids=[300],
                                         direction="upstream"), _expander(graph))
    assert [200, 201, 200, 300] not in [p[0::2] for p in result.paths]
    assert [201, 200, 300] in [p[0::2] for p in result.paths]
    # Graffinity's behavior on request: the bounce is a path again.
    result = find_paths(graph, PathQuery(hops=2, start_ids=[200], simple=False), _expander(graph))
    assert [200, 201, 200] in [p[0::2] for p in result.paths]


def test_shorter_paths_when_min_hops_is_lower():
    graph = _graph()
    result = find_paths(graph, PathQuery(
        hops=2, min_hops=1, start_ids=[100], node_patterns=["", "", "GC.*"]), _expander(graph))
    lengths = sorted((len(p) - 1) // 2 for p in result.paths)
    assert lengths == [1, 2]  # 100->300 directly, and 100->200->300


def test_path_cap_and_frontier_cap_are_reported():
    graph = _graph()
    result = find_paths(graph, PathQuery(hops=2, start_ids=[100], max_paths=1), _expander(graph))
    assert result.truncated and len(result.paths) == 1
    # The cap bounds the paths still growing too, and says so.
    result = find_paths(graph, PathQuery(hops=2, min_hops=1, start_ids=[100], max_paths=3),
                        _expander(graph))
    assert result.truncated and len(result.paths) == 3
    result = find_paths(graph, PathQuery(hops=2, min_hops=1, start_ids=[100]), _expander(graph))
    assert not result.truncated and len(result.paths) == 4

    graph = _graph()
    calls = []
    result = find_paths(graph, PathQuery(hops=2, start_ids=[100], max_frontier=1),
                        _expander(graph, calls))
    # Hop 1 wanted 200 and 300 but could expand only one of them.
    assert calls == [[100], [200]]
    assert result.frontier_capped and result.unexpanded == 1
    assert sorted(p[0::2] for p in result.paths) == [[100, 200, 201], [100, 200, 300]]


def test_query_validation():
    with pytest.raises(ValueError):
        PathQuery(hops=0)
    with pytest.raises(ValueError):
        PathQuery(hops=2, min_hops=3)
    query = PathQuery(hops=3, node_patterns=["A"], edge_patterns=["", "Conv.*"])
    assert query.node_patterns == ["A", ".*", ".*", ".*"]
    assert query.edge_patterns == [".*", "Conv.*", ".*"]
    assert query.node_matches(0, "a") and not query.node_matches(0, "ab")
    assert query.edge_matches(1, "ConvPre") and not query.edge_matches(1, "RibbonPre")


# -- overviews -------------------------------------------------------------
def test_matrix_and_node_table():
    graph = _graph()
    result = find_paths(graph, PathQuery(hops=2, min_hops=1, start_ids=[100, 101]),
                        _expander(graph))
    matrix = connectivity_matrix(result.paths)
    assert matrix.rows == [100, 101]
    assert set(matrix.cols) == {200, 300, 201}
    # c100 -> c300: the direct ribbon edge and the path through c200.
    assert len(matrix.cell(100, 300)) == 2
    assert cell_metric(result.paths, matrix.cell(100, 300), graph, "paths") == 2
    assert cell_metric(result.paths, matrix.cell(100, 300), graph, "min_length") == 1
    # Distinct edges used: 100->300 ribbon (2 synapses), 100->200 (1), 200->300 (1).
    assert cell_metric(result.paths, matrix.cell(100, 300), graph, "synapses") == 4
    assert cell_metric(result.paths, [], graph) == 0
    with pytest.raises(ValueError):
        cell_metric(result.paths, matrix.cell(100, 300), graph, "bogus")

    table = intermediate_node_table(result.paths)
    assert table.columns == ["2:1"]
    assert table.ordered()[0] == 200
    assert len(table.rows[200]["2:1"]) == 4  # both starts, both ends via c200


def test_empty_results_make_empty_overviews():
    matrix = connectivity_matrix([])
    assert matrix.rows == [] and matrix.cols == [] and matrix.cells == []
    table = intermediate_node_table([])
    assert table.columns == [] and table.rows == {} and table.ordered() == []


# -- the OData queries behind it -------------------------------------------
def test_links_by_parent_structures_queries_both_ends(tmp_path):
    seen = []

    def handler(request):
        url = httpx.URL(str(request.url))
        seen.append(url.params.get("$filter", ""))
        if "Source/ParentID" in seen[-1]:
            value = [_link(1000, 100, 2000, 200), _link(1001, 100, 3001, 300)]
        elif "Target/ParentID" in seen[-1]:
            value = [_link(1001, 100, 3001, 300), _link(2001, 200, 3000, 300)]
        elif "Structures" in str(url):
            value = [{"ID": 100, "Label": "CBb4w", "TypeID": 1, "ParentID": None},
                     {"ID": 200, "Label": None, "TypeID": 1, "ParentID": None}]
        else:
            value = []
        return httpx.Response(200, json={"value": value})

    client = ODataClient(
        "i", client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=DiskCache(tmp_path, enabled=False),
    )
    rows = client.fetch_links_by_parent_structures([100, 300])
    assert seen[0].startswith("Source/ParentID in (100,300)")
    assert seen[1].startswith("Target/ParentID in (100,300)")
    # The link seen from both ends appears once.
    assert sorted((r["SourceID"], r["TargetID"]) for r in rows) == [
        (1000, 2000), (1001, 3001), (2001, 3000)]
    assert client.fetch_links_by_parent_structures([]) == []

    brief = client.get_structures_brief([100, 200])
    assert brief == {
        100: {"label": "CBb4w", "type_id": 1, "parent_id": None},
        200: {"label": "", "type_id": 1, "parent_id": None},
    }
