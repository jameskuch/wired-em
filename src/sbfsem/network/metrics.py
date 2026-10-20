"""Graph metrics over connectivity networks.

Ports ``util/analysis/helpers/adjacencyMatrix.m``,
``weightedAdjacencyMatrix.m``, ``util/network/degreePlot.m`` and the
``laplacian`` method of ``+sbfsem/+core/NeuronGraph.m`` (which returned an
unassigned output on its directed path, F006).
"""

from __future__ import annotations

import networkx as nx
import numpy as np

__all__ = ["adjacency_matrix", "laplacian_matrix", "summary"]


def adjacency_matrix(graph: nx.Graph, *, weighted: bool = False, nodelist=None):
    """Adjacency matrix and its node order."""
    nodes = list(nodelist) if nodelist is not None else sorted(graph.nodes)
    matrix = nx.to_numpy_array(graph, nodelist=nodes, weight="weight" if weighted else None)
    return matrix, nodes


def laplacian_matrix(graph: nx.Graph, *, nodelist=None) -> tuple[np.ndarray, list]:
    """Graph Laplacian.  Directed graphs are symmetrised first."""
    nodes = list(nodelist) if nodelist is not None else sorted(graph.nodes)
    base = graph.to_undirected() if graph.is_directed() else graph
    return nx.laplacian_matrix(base, nodelist=nodes).toarray(), nodes


def summary(graph: nx.Graph) -> dict:
    """A few structural statistics, safe on empty and disconnected graphs."""
    n = graph.number_of_nodes()
    m = graph.number_of_edges()
    base = graph.to_undirected() if graph.is_directed() else graph
    components = list(nx.connected_components(base)) if n else []
    degrees = [d for _, d in graph.degree()]
    return {
        "n_nodes": n,
        "n_edges": m,
        "n_components": len(components),
        "largest_component": max((len(c) for c in components), default=0),
        "mean_degree": float(np.mean(degrees)) if degrees else 0.0,
        "max_degree": int(max(degrees)) if degrees else 0,
        "density": float(nx.density(graph)) if n > 1 else 0.0,
    }
