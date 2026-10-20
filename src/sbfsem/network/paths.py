"""Path-based connectivity overviews: Graffinity's two techniques.

Implements the *connectivity matrix* and the *intermediate node table* of
Kerzner, Lex, Sigulinsky, Urness, Jones, Marc and Meyer, "Graffinity:
Visualizing Connectivity in Large Graphs", Computer Graphics Forum 36(3)
(EuroVis 2017), over the neuron-to-neuron graph that Viking's
``StructureLinks`` define.  The query grammar is the one Graffinity's
client-only release (visdesignlab/graffinity 0.1, Zenodo 10.5281/zenodo.1422851)
settled on: a path length, one label pattern per node position and one
synapse-type pattern per hop, all matched as whole-string regular
expressions.

Nothing here is a port of the Graffinity source (AngularJS 1.5 + D3 3, whose
README recommends re-implementing the techniques); the definitions come from
the paper, sections 4.1 and 4.2:

* a **connectivity set** ``C(start, end)`` is every query path from ``start``
  to ``end``; the matrix has one row per start node, one column per end node,
  and a metric of the set in each cell;
* an **intermediate node set** ``I(n, (j, l))`` is every path of length ``l``
  whose ``j``-th node is ``n``; the table has one row per intermediate node and
  one column per ``(length, position)``.

Paths are stored the way Graffinity stored them, as ``[n0, e0, n1, e1, …]``:
node IDs at even positions, indices into the edge list at odd positions.
The graph itself is expanded lazily, one hop at a time, through an ``expand``
callback that fetches the next frontier's links from Viking, so a query never
needs the whole volume's network up front.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..core.types import StructureTypes, VikingStructureTypes

__all__ = [
    "ANY",
    "NeuronNode",
    "NeuronEdge",
    "LinkGraph",
    "PathQuery",
    "PathSearch",
    "ConnectivityMatrix",
    "IntermediateNodeTable",
    "synapse_type_name",
    "neuron_edges_from_structure_links",
    "find_paths",
    "connectivity_matrix",
    "intermediate_node_table",
    "cell_metric",
]

#: The pattern that matches every label or synapse type.
ANY = ".*"


@dataclass
class NeuronNode:
    """A parent structure (usually a cell) in the link graph.

    ``label`` is ``None`` until Viking has been asked for it; the empty string
    means Viking answered and the structure has no label.
    """

    id: int
    label: str | None = None
    type_id: int | None = None

    @property
    def label_text(self) -> str:
        return self.label or ""


@dataclass
class NeuronEdge:
    """Every synapse of one type from one neuron to another.

    ``links`` lists the child structures that make the connection: ``(source
    child ID, target child ID)`` per StructureLink, so ``len(links)`` is the
    synapse count and each pair is an ID that can be looked up in Viking.
    """

    source: int
    target: int
    synapse_type: str
    links: list[tuple[int, int]] = field(default_factory=list)
    bidirectional: bool = False

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.source, self.target, self.synapse_type)

    @property
    def n_synapses(self) -> int:
        return len(self.links)


def synapse_type_name(type_id: Any, tags: str | None = None) -> str:
    """The toolbox's name for a child structure's Viking type.

    ``ConvPre``, ``RibbonPre``, ``GapJunction``, … — the names the rest of the
    application uses for synapses, so one vocabulary serves the Synapses
    panel, the Network tab and a connectivity query.  A Viking type the
    toolbox does not model keeps Viking's own name.
    """
    if type_id is None:
        return "Unknown"
    try:
        return StructureTypes.from_viking(int(type_id), tags or "").value
    except ValueError:
        try:
            return VikingStructureTypes(int(type_id)).name
        except ValueError:
            return f"Type{int(type_id)}"


def _undirected(name: str) -> bool:
    try:
        return StructureTypes.from_str(name).is_undirected
    except ValueError:
        return False


def neuron_edges_from_structure_links(rows: Iterable[dict[str, Any]]) -> list[NeuronEdge]:
    """Collapse child-to-child StructureLinks into neuron-to-neuron edges.

    Each row needs ``Source`` and ``Target`` expanded with ``ID``, ``ParentID``,
    ``TypeID`` and (optionally) ``Tags``, which is what
    :meth:`~sbfsem.io.odata.ODataClient.fetch_links_by_parent_structures`
    returns.  A link is one synapse; the edge's type is the *source* child's
    type, i.e. the presynaptic side for a directed synapse.

    Viking marks gap junctions and the like ``Bidirectional``; the MATLAB
    toolbox additionally treated ``Unknown``, ``Touch``, ``GapJunction`` and
    ``Desmosome`` as undirected because their Source/Target assignment is
    arbitrary (``getLinkedNeurons.m``).  Both rules apply: such a link yields
    an edge in each direction, as Graffinity's loader did.
    """
    edges: dict[tuple[int, int, str], NeuronEdge] = {}

    def add(source: int, target: int, kind: str, pair: tuple[int, int], bidir: bool) -> None:
        key = (source, target, kind)
        edge = edges.get(key)
        if edge is None:
            edge = edges[key] = NeuronEdge(source, target, kind, bidirectional=bidir)
        if pair not in edge.links:
            edge.links.append(pair)

    for row in rows:
        src = row.get("Source") or {}
        tgt = row.get("Target") or {}
        s_parent, t_parent = src.get("ParentID"), tgt.get("ParentID")
        if s_parent is None or t_parent is None:
            continue
        s_child = row.get("SourceID", src.get("ID"))
        t_child = row.get("TargetID", tgt.get("ID"))
        if s_child is None or t_child is None:
            continue
        s_parent, t_parent = int(s_parent), int(t_parent)
        pair = (int(s_child), int(t_child))
        s_type = synapse_type_name(src.get("TypeID"), src.get("Tags"))
        bidir = bool(row.get("Bidirectional")) or _undirected(s_type)
        add(s_parent, t_parent, s_type, pair, bidir)
        if bidir and s_parent != t_parent:
            t_type = synapse_type_name(tgt.get("TypeID"), tgt.get("Tags"))
            add(t_parent, s_parent, t_type, (pair[1], pair[0]), True)
    return list(edges.values())


@dataclass
class LinkGraph:
    """The neuron-level link graph of one volume, grown one frontier at a time."""

    nodes: dict[int, NeuronNode] = field(default_factory=dict)
    edges: list[NeuronEdge] = field(default_factory=list)
    #: neuron -> indices into ``edges`` of the edges leaving it
    out_edges: dict[int, list[int]] = field(default_factory=dict)
    #: neuron -> indices into ``edges`` of the edges arriving at it
    in_edges: dict[int, list[int]] = field(default_factory=dict)
    #: neurons whose links have been fetched; an unexpanded neuron has no
    #: known out-edges, which is not the same as having none.
    expanded: set[int] = field(default_factory=set)
    _index: dict[tuple[int, int, str], int] = field(default_factory=dict, repr=False)

    def node(self, neuron_id: int) -> NeuronNode:
        node = self.nodes.get(neuron_id)
        if node is None:
            node = self.nodes[neuron_id] = NeuronNode(neuron_id)
        return node

    def set_node(self, neuron_id: int, *, label: str | None = None,
                 type_id: int | None = None) -> NeuronNode:
        node = self.node(neuron_id)
        if label is not None:
            node.label = label
        if type_id is not None:
            node.type_id = type_id
        return node

    def label(self, neuron_id: int) -> str:
        node = self.nodes.get(neuron_id)
        return node.label_text if node else ""

    def unlabeled(self) -> list[int]:
        """Neurons Viking has not yet been asked to label."""
        return sorted(n for n, node in self.nodes.items() if node.label is None)

    def add_edges(self, edges: Iterable[NeuronEdge]) -> None:
        for edge in edges:
            self.node(edge.source)
            self.node(edge.target)
            index = self._index.get(edge.key)
            if index is None:
                index = len(self.edges)
                self.edges.append(NeuronEdge(edge.source, edge.target, edge.synapse_type,
                                             list(edge.links), edge.bidirectional))
                self._index[edge.key] = index
                self.out_edges.setdefault(edge.source, []).append(index)
                self.in_edges.setdefault(edge.target, []).append(index)
            else:
                known = self.edges[index]
                for pair in edge.links:
                    if pair not in known.links:
                        known.links.append(pair)

    def mark_expanded(self, neuron_ids: Iterable[int]) -> None:
        for neuron_id in neuron_ids:
            self.node(neuron_id)
            self.expanded.add(int(neuron_id))

    def edges_from(self, neuron_id: int) -> list[NeuronEdge]:
        return [self.edges[i] for i in self.out_edges.get(neuron_id, ())]

    @property
    def n_synapses(self) -> int:
        return sum(edge.n_synapses for edge in self.edges)


def _compile(pattern: str | None) -> re.Pattern[str]:
    text = (pattern or "").strip() or ANY
    return re.compile(text, re.IGNORECASE)


@dataclass
class PathQuery:
    """What Graffinity's query panel asks for.

    ``node_patterns`` has ``hops + 1`` entries (start, intermediates…, end) and
    ``edge_patterns`` ``hops`` entries; missing entries mean "anything".  With
    ``min_hops < hops`` a path may finish early: a node reached before the
    last hop is kept as an end when it matches the end pattern and continued
    when it matches its position's pattern — the paper's "paths of length
    ≤ L".  Patterns are whole-string, case-insensitive regular expressions
    (``CBb.*``, ``ConvPre|RibbonPre``); an invalid one raises ``re.error``.

    ``direction`` says which end the search grows from.  ``downstream`` is
    Graffinity's: the anchors are the start nodes and paths follow synapses
    forward.  ``upstream`` anchors the *end* nodes and walks synapses
    backward — the query a ganglion cell needs, since all its synapses are
    inputs — and the patterns keep their meaning: ``node_patterns[0]`` is
    still the first node of every path and ``node_patterns[hops]`` the last,
    so the anchors are matched against the end pattern.

    ``max_paths`` caps the finished paths and, separately, the paths still
    being extended at each hop; hitting either marks the result truncated.

    Paths are simple by default: no neuron appears twice in one.
    Graffinity's own search did not check, and an undirected link walked
    out and back (``A → B → A``) then made up most of a three-hop answer.
    ``simple=False`` restores that behavior, which is how a cell linked to
    itself — usually an annotation slip — shows up as a path.
    """

    hops: int = 2
    node_patterns: list[str] = field(default_factory=list)
    edge_patterns: list[str] = field(default_factory=list)
    #: Explicit start neurons.  ``None`` means every known node whose label
    #: matches the start pattern.
    start_ids: list[int] | None = None
    min_hops: int | None = None
    max_paths: int = 20_000
    #: Neurons expanded per hop before the search is cut short.
    max_frontier: int = 300
    direction: str = "downstream"
    simple: bool = True

    def __post_init__(self) -> None:
        if self.hops < 1:
            raise ValueError("hops must be at least 1")
        if self.direction not in ("downstream", "upstream"):
            raise ValueError("direction must be 'downstream' or 'upstream'")
        if self.min_hops is None:
            self.min_hops = self.hops
        if not 1 <= self.min_hops <= self.hops:
            raise ValueError("min_hops must be between 1 and hops")
        nodes = list(self.node_patterns) + [ANY] * (self.hops + 1 - len(self.node_patterns))
        edges = list(self.edge_patterns) + [ANY] * (self.hops - len(self.edge_patterns))
        self.node_patterns = [p if (p or "").strip() else ANY for p in nodes[: self.hops + 1]]
        self.edge_patterns = [p if (p or "").strip() else ANY for p in edges[: self.hops]]
        self._node_re = [_compile(p) for p in self.node_patterns]
        self._edge_re = [_compile(p) for p in self.edge_patterns]

    def node_matches(self, position: int, label: str) -> bool:
        return self._node_re[position].fullmatch(label or "") is not None

    def edge_matches(self, hop: int, synapse_type: str) -> bool:
        return self._edge_re[hop].fullmatch(synapse_type or "") is not None


@dataclass
class PathSearch:
    """The paths a query found, and how completely it looked."""

    paths: list[list[int]] = field(default_factory=list)
    #: The neurons the search grew from: the starts of a downstream query,
    #: the ends of an upstream one.
    anchors: list[int] = field(default_factory=list)
    #: ``max_paths`` was reached; the result is a prefix of the full answer.
    truncated: bool = False
    #: A hop's frontier held more neurons than ``max_frontier``; paths through
    #: the neurons left unexpanded are missing.
    frontier_capped: bool = False
    #: Neurons whose links were fetched during this search.
    expanded: int = 0
    #: Neurons that were never expanded because of the cap.
    unexpanded: int = 0

    @property
    def n_paths(self) -> int:
        return len(self.paths)


def find_paths(
    graph: LinkGraph,
    query: PathQuery,
    expand: Callable[[list[int]], None] | None = None,
) -> PathSearch:
    """Breadth-first path search with per-position constraints.

    ``expand(ids)`` is called before each hop with the frontier neurons whose
    links are not in ``graph`` yet; it must add their edges (and the labels of
    the neurons those edges reach) and mark them expanded.  Without it the
    search runs over what the graph already holds.  This is Graffinity's
    ``fillInPathsByRegex`` with the graph fetched on demand, run forward from
    the starts or backward from the ends (see :class:`PathQuery`).
    """
    result = PathSearch()
    downstream = query.direction == "downstream"
    anchor_position = 0 if downstream else query.hops
    far_position = query.hops if downstream else 0
    min_hops = query.min_hops or 1

    if query.start_ids is not None:
        candidates = [int(i) for i in dict.fromkeys(query.start_ids)]
    else:
        candidates = sorted(graph.nodes)
    anchors = [n for n in candidates if query.node_matches(anchor_position, graph.label(n))]
    if len(anchors) > query.max_frontier:
        result.frontier_capped = True
        result.unexpanded += len(anchors) - query.max_frontier
        anchors = anchors[: query.max_frontier]
    result.anchors = anchors

    def tip(path: list[int]) -> int:
        return path[-1] if downstream else path[0]

    frontier: list[list[int]] = [[a] for a in anchors]
    finished: list[list[int]] = []

    for step in range(1, query.hops + 1):
        pending = list(dict.fromkeys(tip(p) for p in frontier))
        need = [n for n in pending if n not in graph.expanded]
        if need and expand is not None:
            if len(need) > query.max_frontier:
                result.frontier_capped = True
                result.unexpanded += len(need) - query.max_frontier
                need = need[: query.max_frontier]
            expand(need)
            result.expanded += len(need)

        # Where the node reached at this step sits in a full-length path, and
        # which hop's edge pattern the synapse into it must satisfy.
        position = step if downstream else query.hops - step
        hop = step - 1 if downstream else query.hops - step
        next_frontier: list[list[int]] = []
        for path in frontier:
            current = tip(path)
            if current not in graph.expanded:
                continue
            indices = graph.out_edges.get(current, ()) if downstream \
                else graph.in_edges.get(current, ())
            for index in indices:
                edge = graph.edges[index]
                if not query.edge_matches(hop, edge.synapse_type):
                    continue
                nxt = edge.target if downstream else edge.source
                if query.simple and nxt in path[0::2]:
                    continue
                label = graph.label(nxt)
                as_end = step >= min_hops and query.node_matches(far_position, label)
                as_mid = step < query.hops and query.node_matches(position, label)
                if not (as_end or as_mid):
                    continue
                extended = [*path, index, nxt] if downstream else [nxt, index, *path]
                if as_end:
                    finished.append(extended)
                    if len(finished) >= query.max_paths:
                        result.truncated = True
                        break
                if as_mid:
                    next_frontier.append(extended)
            if result.truncated:
                break
        if result.truncated:
            break
        if len(next_frontier) > query.max_paths:
            result.truncated = True
            next_frontier = next_frontier[: query.max_paths]
        frontier = next_frontier

    result.paths = finished[: query.max_paths]
    return result


def path_length(path: list[int]) -> int:
    return (len(path) - 1) // 2


def path_nodes(path: list[int]) -> list[int]:
    return path[0::2]


def path_edges(path: list[int]) -> list[int]:
    return path[1::2]


@dataclass
class ConnectivityMatrix:
    """Rows are start nodes, columns end nodes, cells the path indices."""

    rows: list[int]
    cols: list[int]
    cells: list[list[list[int]]]

    def cell(self, row: int, col: int) -> list[int]:
        return self.cells[self.rows.index(row)][self.cols.index(col)]


def connectivity_matrix(paths: list[list[int]]) -> ConnectivityMatrix:
    """Group paths by (start, end).  Section 4.1 of the paper."""
    rows: list[int] = []
    cols: list[int] = []
    row_of: dict[int, int] = {}
    col_of: dict[int, int] = {}
    for path in paths:
        if path[0] not in row_of:
            row_of[path[0]] = len(rows)
            rows.append(path[0])
        if path[-1] not in col_of:
            col_of[path[-1]] = len(cols)
            cols.append(path[-1])
    cells: list[list[list[int]]] = [[[] for _ in cols] for _ in rows]
    for index, path in enumerate(paths):
        cells[row_of[path[0]]][col_of[path[-1]]].append(index)
    return ConnectivityMatrix(rows, cols, cells)


@dataclass
class IntermediateNodeTable:
    """One row per intermediate node, one column per (length, position)."""

    #: ``"l:j"`` keys — paths of length ``l`` at position ``j`` (1-based).
    columns: list[str]
    #: node id -> column key -> path indices
    rows: dict[int, dict[str, list[int]]]

    def ordered(self) -> list[int]:
        """Node ids by total path count, descending."""
        return sorted(self.rows, key=lambda n: -sum(len(v) for v in self.rows[n].values()))


def intermediate_node_table(paths: list[list[int]]) -> IntermediateNodeTable:
    """Group paths by the node at each interior position.  Section 4.2."""
    rows: dict[int, dict[str, list[int]]] = {}
    columns: set[tuple[int, int]] = set()
    for index, path in enumerate(paths):
        length = path_length(path)
        nodes = path_nodes(path)
        for position in range(1, length):
            key = (length, position)
            columns.add(key)
            rows.setdefault(nodes[position], {}).setdefault(f"{length}:{position}", []).append(index)
    ordered = [f"{length}:{position}" for length, position in sorted(columns)]
    return IntermediateNodeTable(ordered, rows)


def cell_metric(paths: list[list[int]], indices: Iterable[int], graph: LinkGraph,
                metric: str = "paths") -> float:
    """A summary of one connectivity set.

    ``paths`` counts them; ``min_length`` is the shortest one; ``synapses``
    sums the synapse counts of the distinct neuron-level edges the set uses,
    so a pair joined by three ribbon synapses scores 3 on a one-hop query.
    """
    chosen = [paths[i] for i in indices]
    if not chosen:
        return 0.0
    if metric == "paths":
        return float(len(chosen))
    if metric == "min_length":
        return float(min(path_length(p) for p in chosen))
    if metric == "synapses":
        used = {index for p in chosen for index in path_edges(p)}
        return float(sum(graph.edges[i].n_synapses for i in used))
    raise ValueError(f"unknown metric {metric!r}")
