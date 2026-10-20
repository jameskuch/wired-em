"""Connectivity graphs between neurons.

Ports ``Network.m``, ``util/network/`` (``getLinkedNeurons``,
``getAllLinkedNeurons``, ``getLinkedNeuronsByType``, ``countLinkedNeurons``,
``addToNetwork``, ``neuronGraphPlot``, ``plotNetwork``, ``degreePlot``) and
``+sbfsem/+core/NeuronGraph.m``.

``NeuronGraph`` had four independent defects — missing ``obj`` arguments on all
four accessors, an undefined ``G`` in ``degreePlot``, a never-assigned
``obj.neuron`` and a discarded ``isDirected`` argument — which together mean the
MATLAB class never successfully ran (F002-F006).  Its capabilities are folded
into :class:`sbfsem.core.structure.Structure.graph` and the helpers here.

``Network.synapseGraph`` passed raw Viking structure IDs to ``digraph`` as node
*indices*, creating a graph with ``max(ID)`` nodes — tens of thousands of
isolated nodes — misaligned with its own ``idMap`` (F017).  networkx keys nodes
by whatever object you give it, so that class of bug is gone by construction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

from ..core.types import StructureTypes

__all__ = ["ConnectivityNetwork", "network_from_links", "degree_table",
           "cytoscape_elements"]


@dataclass
class ConnectivityNetwork:
    """A directed, weighted graph of neuron-to-neuron connectivity."""

    graph: nx.DiGraph
    #: One row per synapse: SourceID, TargetID, SynapseID, SynapseType.
    links: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def neuron_ids(self) -> list[int]:
        return sorted(int(n) for n in self.graph.nodes)

    def neighbors(self, neuron_id: int, direction: str = "both") -> list[int]:
        node = int(neuron_id)
        if node not in self.graph:
            return []
        if direction == "out":
            return sorted(int(n) for n in self.graph.successors(node))
        if direction == "in":
            return sorted(int(n) for n in self.graph.predecessors(node))
        return sorted({int(n) for n in self.graph.successors(node)}
                      | {int(n) for n in self.graph.predecessors(node)})

    def weight(self, source: int, target: int) -> int:
        data = self.graph.get_edge_data(int(source), int(target))
        return int(data["weight"]) if data else 0

    def to_undirected(self) -> nx.Graph:
        return self.graph.to_undirected(reciprocal=False)

    def adjacency(self) -> tuple[np.ndarray, list[int]]:
        ids = self.neuron_ids
        return nx.to_numpy_array(self.graph, nodelist=ids, weight="weight"), ids


def _one_row_per_link(links: pd.DataFrame) -> pd.DataFrame:
    """One row for each StructureLink, however many of its cells listed it.

    A synapse between two loaded cells is listed by both: by the presynaptic
    cell as its own structure (``SynapseID``) with the other cell's as
    ``PartnerSynapseID``, and by the postsynaptic cell the other way round.
    Counted as rows that is two synapses.  The two rows name the same pair of
    structures, so they are one link; the row of the presynaptic side (a
    ``...Pre`` type) is kept, as that side names what the synapse is.  Rows
    without a ``PartnerSynapseID`` (a hand-made table, an unlinked synapse)
    are all kept.
    """
    if "PartnerSynapseID" not in links.columns:
        return links
    keep: list[int] = []
    seen: dict[tuple, int] = {}
    for i, row in enumerate(links.itertuples()):
        other = row.PartnerSynapseID
        if other is None or pd.isna(other):
            keep.append(i)
            continue
        key = (int(row.SourceID), int(row.TargetID),
               *sorted((int(row.SynapseID), int(other))))
        at = seen.get(key)
        if at is None:
            seen[key] = len(keep)
            keep.append(i)
        elif (str(links.iloc[keep[at]]["SynapseType"]).endswith("Post")
              and str(row.SynapseType).endswith("Pre")):
            keep[at] = i
    return links.iloc[keep]


def network_from_links(links: pd.DataFrame) -> ConnectivityNetwork:
    """Build a connectivity network from a link table.

    ``links`` needs ``SourceID``, ``TargetID`` and ``SynapseType`` columns.
    Parallel synapses become an edge ``weight``; the synapse types contributing to
    each edge are kept in a ``types`` attribute.  A table that carries
    ``PartnerSynapseID`` (the link tables of :mod:`sbfsem.network.links` do) counts
    a synapse listed by both of its cells once.
    """
    graph = nx.DiGraph()
    if links is None or links.empty:
        return ConnectivityNetwork(graph, pd.DataFrame())
    links = _one_row_per_link(links)

    for row in links.itertuples():
        source, target = int(row.SourceID), int(row.TargetID)
        graph.add_node(source)
        graph.add_node(target)
        if graph.has_edge(source, target):
            edge = graph[source][target]
            edge["weight"] += 1
            edge["types"].append(str(row.SynapseType))
        else:
            graph.add_edge(source, target, weight=1, types=[str(row.SynapseType)])
    return ConnectivityNetwork(graph, links.reset_index(drop=True))


def degree_table(network: ConnectivityNetwork) -> pd.DataFrame:
    """In/out degree and weighted degree per neuron.  Ports ``degreePlot``."""
    graph = network.graph
    rows = []
    for node in network.neuron_ids:
        rows.append(
            {
                "NeuronID": node,
                "InDegree": graph.in_degree(node),
                "OutDegree": graph.out_degree(node),
                "InWeight": graph.in_degree(node, weight="weight"),
                "OutWeight": graph.out_degree(node, weight="weight"),
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["Degree"] = frame["InDegree"] + frame["OutDegree"]
        frame = frame.sort_values("Degree", ascending=False).reset_index(drop=True)
    return frame


def _is_directed(synapse_type: str) -> bool:
    """Whether a link type has a pre and a post side.

    Gap junctions, adherens (``Desmosome``), touches and ``Unknown`` do not:
    Viking's Source/Target for them is whichever end was annotated first, so
    the browser draws them without an arrowhead.

    This is one of three answers to "is this link directed".  A link table
    (:func:`sbfsem.network.links.linked_neurons`) matches a type from the side
    its ``is_pre``/``is_post`` names, and the path graph
    (:func:`sbfsem.network.paths.neuron_edges_from_structure_links`) calls a
    type undirected only if it is one of ``StructureTypes.is_undirected``'s four
    or Viking flags the link ``Bidirectional``.  They agree on the ``...Pre`` and
    ``...Post`` types, the basal types and the four undirected ones, and differ
    on 39 other members -- ``Cell``, ``Axon``, ``Mitochondria``,
    ``AnnularGapJunction``, ``FlatContact``, ``GJEndo`` ... -- which this
    leaves arrowless and the path graph follows from source to target
    (measured 2026-10-16).
    """
    # The enum's pre/post sets miss a few members (PlaqueLikePre, CisternPost);
    # the name says which side such a structure is.
    if synapse_type.endswith(("Pre", "Post")):
        return True
    try:
        kind = StructureTypes.from_str(synapse_type)
    except ValueError:
        return False
    return kind.is_pre or kind.is_post


def cytoscape_elements(
    network: ConnectivityNetwork, *, labels: dict[int, str] | None = None,
    loaded: Iterable[int] | None = None,
) -> list[dict]:
    """Serialize the network as Cytoscape.js ``elements`` JSON.

    The same payload drives the browser network view; keeping the conversion in
    the backend means any future frontend gets the graph for free.  A node
    carries the raw Viking label (``vikingLabel``, possibly empty) beside the
    display ``label`` and says whether it is one of the ``loaded`` cells; an
    edge carries its synapse count per type (``typeCounts``, largest first),
    which types are directed, and ``primaryType``, the type with the most
    synapses — so a viewer can hide a type and recompute weights itself.
    """
    labels = labels or {}
    loaded_ids = {int(i) for i in loaded} if loaded is not None else set()
    elements: list[dict] = []
    for node in network.neuron_ids:
        raw = (labels.get(node) or "").strip()
        elements.append(
            {
                "data": {
                    "id": str(node),
                    "label": raw or f"c{node}",
                    "vikingLabel": raw,
                    "neuronId": node,
                    "degree": network.graph.degree(node),
                    "loaded": node in loaded_ids,
                }
            }
        )
    for source, target, data in network.graph.edges(data=True):
        counts = Counter(str(t) for t in data.get("types", []))
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        types = [t for t, _ in ordered]
        directed = [t for t in types if _is_directed(t)]
        elements.append(
            {
                "data": {
                    "id": f"e{source}-{target}",
                    "source": str(source),
                    "target": str(target),
                    "weight": int(data.get("weight", 1)),
                    "types": types,
                    "typeCounts": dict(ordered),
                    "directedTypes": directed,
                    "directed": bool(directed),
                    "primaryType": types[0] if types else "",
                }
            }
        )
    return elements
