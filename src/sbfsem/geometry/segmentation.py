"""Split a neuron into non-branching segments.

Ports ``util/analysis/dendriteSegmentation.m``, ``util/analysis/getSegmentStart.m``
and the near-identical copies at ``+sbfsem/+analysis/Segment.m`` and
``+sbfsem/+render/Segment.m``.  The MATLAB code had three copies of this DFS; the
review's F088, F118, F119 and F120 apply to all three.  There is one copy here.

The algorithm is a depth-first search whose *finish* events are grouped into runs:
in a tree, consecutive post-order finishes form a maximal path from a leaf up to
the last node before a branch point.  Each such run is one segment, closed off by
appending the branch point itself so successive segments share an endpoint.

Deviations (see DEVIATIONS.md):

* The branch-point closure appended **every** remaining neighbor, so a segment's
  polyline jumped across the branch into sibling branches and every length,
  diameter and tortuosity measured from it was wrong (F119).  Only the node's DFS
  parent is appended here.
* MATLAB never asked ``dfsearch`` for ``startnode`` events, so the promised
  "annotations are not actually connected" warning could never fire (F088/F118).
  Disconnected components are reported in :attr:`SegmentationResult.components`.
* An unmatched start location ID was silently reused as a node index (F120).
  :func:`dendrite_segmentation` raises instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

__all__ = [
    "SegmentationResult",
    "dendrite_segmentation",
    "dfs_events",
    "segment_endpoints",
]


@dataclass
class SegmentationResult:
    """Segments of a neuron, keyed by Viking location ID throughout."""

    #: Each segment is an ordered array of location IDs, leaf-first, ending at
    #: the branch point (or soma) it hangs from.
    segments: list[np.ndarray]
    #: One row per segment with ``Z``, ``XYZum`` and ``Rum`` arrays.
    table: pd.DataFrame
    #: Location ID the search started from.
    start_id: int
    #: Start IDs of each additional connected component the search had to restart
    #: on.  Non-empty means the neuron's annotations are not all connected.
    components: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.segments)

    @property
    def linking_nodes(self) -> np.ndarray:
        """The branch point each segment hangs from (``getSegmentStart``)."""
        return np.array([s[-1] for s in self.segments], dtype=np.int64)

    @property
    def starting_nodes(self) -> np.ndarray:
        """The first node of each segment that is unique to it."""
        return np.array(
            [s[-2] if len(s) > 1 else s[-1] for s in self.segments], dtype=np.int64
        )

    @property
    def terminal_nodes(self) -> np.ndarray:
        """The free end of each segment."""
        return np.array([s[0] for s in self.segments], dtype=np.int64)


def dfs_events(
    graph: nx.Graph, start, restart: bool = True
) -> Iterator[tuple[str, int, int | None]]:
    """Yield ``(event, node, parent)`` for a depth-first search.

    ``event`` is ``'start'``, ``'discover'`` or ``'finish'``.  Neighbors are
    visited in ascending location-ID order so the result is deterministic; MATLAB
    used the graph's internal adjacency order, which is not reproducible here.
    """
    visited: set[int] = set()
    roots = [int(start)]
    if restart:
        roots.extend(sorted(int(n) for n in graph.nodes if int(n) != int(start)))

    for root in roots:
        if root in visited or root not in graph:
            continue
        yield ("start", root, None)
        stack: list[tuple[int, int | None, Iterator[int]]] = []
        visited.add(root)
        yield ("discover", root, None)
        stack.append((root, None, iter(sorted(int(n) for n in graph.neighbors(root)))))
        while stack:
            node, parent, neighbors = stack[-1]
            advanced = False
            for nxt in neighbors:
                if nxt in visited:
                    continue
                visited.add(nxt)
                yield ("discover", nxt, node)
                stack.append(
                    (nxt, node, iter(sorted(int(n) for n in graph.neighbors(nxt))))
                )
                advanced = True
                break
            if not advanced:
                stack.pop()
                yield ("finish", node, parent)


def dendrite_segmentation(
    neuron, *, start_node: int | None = None, graph: nx.Graph | None = None
) -> SegmentationResult:
    """Split ``neuron`` into non-branching segments.

    Parameters
    ----------
    neuron:
        A :class:`~sbfsem.core.structure.Structure`.
    start_node:
        Viking location ID to start the search from.  Defaults to the soma
        annotation when the structure has one, else the lowest location ID.
    """
    g = graph if graph is not None else neuron.graph()
    if g.number_of_nodes() == 0:
        empty = pd.DataFrame(columns=["ID", "Z", "XYZum", "Rum"])
        return SegmentationResult([], empty, -1, [])

    start = _resolve_start(neuron, g, start_node)

    segments: list[list[int]] = []
    parents: dict[int, int | None] = {}
    components: list[int] = []
    open_segment: list[int] = []
    is_open = False

    for event, node, parent in dfs_events(g, start):
        if event == "start":
            if node != start:
                components.append(node)
        elif event == "discover":
            parents[node] = parent
            if is_open:
                segments.append(open_segment)
                is_open = False
        elif event == "finish":
            if not is_open:
                is_open = True
                open_segment = []
            open_segment.append(node)
    if is_open:
        segments.append(open_segment)

    # Close each segment onto the node it branches from.  MATLAB appended every
    # remaining neighbor here (F119); the DFS parent is the only correct one.
    closed: list[np.ndarray] = []
    for seg in segments:
        parent = parents.get(seg[-1])
        ids = seg + [parent] if parent is not None else seg
        closed.append(np.asarray(ids, dtype=np.int64))

    table = _segment_table(neuron, closed)
    return SegmentationResult(closed, table, int(start), components)


def _resolve_start(neuron, graph: nx.Graph, start_node) -> int:
    if start_node is None:
        get_soma = getattr(neuron, "get_soma_id", None)
        if get_soma is not None:
            try:
                candidate = int(get_soma())
                if graph.has_node(candidate):
                    return candidate
            except (ValueError, KeyError):
                pass
        return int(min(graph.nodes))
    candidate = int(start_node)
    if not graph.has_node(candidate):
        raise ValueError(
            f"Start location ID {candidate} is not a node of this neuron's graph "
            "(it may have been omitted, or belong to a child structure)"
        )
    return candidate


def _segment_table(neuron, segments: list[np.ndarray]) -> pd.DataFrame:
    """One row per segment, its annotations' section, position and radius.

    The rows are looked up by sorted location ID with NumPy: a pandas
    ``.loc`` per segment cost half a millisecond each, 87 % of the skeleton
    reply (RPC1 c430's 218 segments, 0.19 of 0.23 s on a Mac; RC2 c74420's
    0.8 s on production, 2026-10-20).  The arrays are the ones ``.loc`` gave,
    to the bit and in the same layout (``XYZum`` an n-by-3 view of a 3-by-n
    block), so nothing summed from them changes.
    """
    nodes = neuron.nodes
    if not segments or nodes.empty or not nodes["ID"].is_unique:
        return _segment_table_loc(neuron, segments)
    ids = nodes["ID"].to_numpy(dtype=np.int64)
    order = np.argsort(ids, kind="stable")
    sorted_ids = ids[order]
    z = nodes["Z"].to_numpy()
    xyz = np.ascontiguousarray(nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float).T)
    radius = nodes["Rum"].to_numpy(dtype=float)
    rows = []
    for seg in segments:
        at = np.minimum(np.searchsorted(sorted_ids, seg), len(sorted_ids) - 1)
        found = sorted_ids[at] == seg
        if not found.all():
            raise KeyError(f"{[int(i) for i in seg[~found]]} not in index")
        take = order[at]
        rows.append(
            {
                "ID": seg,
                "Z": z[take].astype(np.int64),
                "XYZum": np.take(xyz, take, axis=1).T,
                "Rum": radius[take],
            }
        )
    return pd.DataFrame(rows, columns=["ID", "Z", "XYZum", "Rum"])


def _segment_table_loc(neuron, segments: list[np.ndarray]) -> pd.DataFrame:
    """:func:`_segment_table` by pandas label lookup, for a node table whose
    IDs repeat (a label then names every row it is on)."""
    indexed = neuron.nodes.set_index("ID")
    rows = []
    for ids in segments:
        sub = indexed.loc[ids]
        rows.append(
            {
                "ID": ids,
                "Z": sub["Z"].to_numpy(dtype=np.int64),
                "XYZum": sub[["Xum", "Yum", "Zum"]].to_numpy(dtype=float),
                "Rum": sub["Rum"].to_numpy(dtype=float),
            }
        )
    return pd.DataFrame(rows, columns=["ID", "Z", "XYZum", "Rum"])


def segment_endpoints(result: SegmentationResult):
    """Ports ``getSegmentStart``: linking, starting and terminal node IDs."""
    return result.linking_nodes, result.starting_nodes, result.terminal_nodes
