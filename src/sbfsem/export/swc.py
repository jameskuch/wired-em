"""SWC neuron morphology export.

Ports ``+sbfsem/+io/SWC.m``.  ``util/export/exportSWC.m`` is dead code — it opens
the file read-only and uses the wrong field capitalisation — and is not ported.

SWC layout: ``n T x y z R P`` where ``T`` is 0 undefined, 1 soma, 2 axon,
3 dendrite, 4 apical dendrite, 5 fork point, 6 end point, 7 custom, and ``P`` is
the parent's index or -1 for the root.

``T`` is the SWC type, not the Viking ``TypeCode`` (1 circle, 5 open curve,
6 closed curve); readers who inferred the latter asked what the 5s and 6s were
(2026-09-17).  The MATLAB exporter typed every branch point 5 and every tip 6,
the original 1990s convention; the current SWC specification (Nanda et al.
2021, neuromorpho.org) reads 5 as "custom" and 6 as "unspecified neurite", so
tools such as NeuroM or navis then treat those nodes as something other than
dendrite.  ``branch_types=False`` keeps every non-soma, non-axon node a dendrite
(3), which is what a morphometrics pipeline expects; the library default stays
MATLAB-faithful.  Either way the header spells out the codes in use.

Deviations (see DEVIATIONS.md):

* ``segmentTyping`` looped ``1:numel(segment)-1``, so the last node of every
  segment kept its initial ``NaN`` type and zero parent — the node that joins the
  segment to the rest of the tree (F049).  The traversal here types every node.
* The exporter never emitted type 1 and ignored both ``hasSoma`` and ``hasAxon``
  (F050).  ``soma_type`` and ``axon_ids`` are honoured.
* The dangling-NaN check was ``~isempty(isnan(...))``, which is always true
  (F051), and the version date used ``'YYYY-mm-DD'``, whose ``Y`` and ``D`` are
  not valid ``datestr`` symbols (F052).

Export options, none of them the default, asked for by Rebecca Pfeiffer
(2026-09-21 to 2026-09-23) so that a figure can name which skeleton it used;
each says in the header what it did:

* ``relax=f`` writes every node moved toward its neighbors, never outside
  ``f`` of its own annotation (``sbfsem.analysis.relax``).
* ``lamellae=True`` writes each lamella -- a profile at least twice as long as
  wide, or a stack of them linked on consecutive sections -- as the chain
  along it, in place of its annotations (``sbfsem.analysis.lamellae``).
* ``sampling="sphere"`` keeps only the rows Bradley & Withers's minimal sphere
  network keeps along each unbranched path (``sbfsem.analysis.thickness``);
  the default is one row per annotation, the record of what was traced.
* ``radius="modal"`` puts a closed curve's modal radius in the ``R`` column --
  a thickness, the same thing on a circle and a crescent -- and lists Viking's
  stored radius beside it as ``R_STORED`` rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from ..io.odata import odata_utc

__all__ = [
    "RADII", "SAMPLINGS", "SWC_TYPES", "provenance_lines", "spanning_forest", "swc_table",
    "type_legend", "write_swc",
]

#: What the rows are: one per annotation, or the minimal sphere network's.
SAMPLINGS = ("annotations", "sphere")
#: What the ``R`` column holds for a closed curve.
RADII = ("stored", "modal")

SWC_TYPES = {
    "undefined": 0,
    "soma": 1,
    "axon": 2,
    "dendrite": 3,
    "apical": 4,
    "fork": 5,
    "end": 6,
    "custom": 7,
}


@dataclass
class _Row:
    index: int
    swc_type: int
    xyz: np.ndarray
    radius: float
    parent: int


@dataclass
class _Build:
    """How the rows were made, for the header."""

    #: The graph the tree was taken from: Viking's, or with chains in place
    #: of the lamella stacks.
    graph: nx.Graph
    #: Links of ``graph`` the tree could not hold, as node keys.
    dropped: list[tuple[int, int]] = field(default_factory=list)
    #: A chain node's key -> the stack annotation nearest it (a chain node has
    #: no Location ID of its own).
    location_of: dict[int, int] = field(default_factory=dict)
    #: (Lamella, its chain node keys in order), when the stacks were chained.
    chains: list = field(default_factory=list)
    radius: str = "stored"
    #: Location ID -> Viking's stored radius, where the R column differs.
    stored: dict[int, float] = field(default_factory=dict)
    sampling: str = "annotations"
    #: Rows before and after sampling, and the length of the links written.
    rows_before: int = 0
    length_before_um: float = 0.0
    length_after_um: float = 0.0


def swc_table(
    neuron,
    *,
    start_node: int | None = None,
    has_soma: bool = True,
    soma_fraction: float = 0.8,
    axon_ids: Iterable[int] | None = None,
    branch_types: bool = True,
    positions: dict[int, tuple[float, float]] | None = None,
    lamellae: bool = False,
    sampling: str = "annotations",
    radius: str = "stored",
) -> pd.DataFrame:
    """Build the SWC node table for a neuron.

    Rows are ordered by a breadth-first traversal from ``start_node`` (the soma
    annotation by default), so every parent index precedes its children.

    ``branch_types`` marks branch points 5 and tips 6 as the MATLAB exporter
    did; ``False`` types them dendrite (3) like every other neurite node.

    ``positions`` (Location ID -> x, y microns) overrides where a node is
    written in the section plane; Z and radius are never touched.  See
    ``sbfsem.analysis.relax``.

    ``lamellae``, ``sampling`` and ``radius`` are the export options of the
    module docstring.  The ``ID`` column holds each row's Location ID, or for
    a chain node a negative number of its own.
    """
    return _build(neuron, start_node=start_node, has_soma=has_soma,
                  soma_fraction=soma_fraction, axon_ids=axon_ids, branch_types=branch_types,
                  positions=positions, lamellae=lamellae, sampling=sampling, radius=radius)[0]


def _build(
    neuron,
    *,
    start_node: int | None = None,
    has_soma: bool = True,
    soma_fraction: float = 0.8,
    axon_ids: Iterable[int] | None = None,
    branch_types: bool = True,
    positions: dict[int, tuple[float, float]] | None = None,
    lamellae: bool = False,
    sampling: str = "annotations",
    radius: str = "stored",
) -> tuple[pd.DataFrame, _Build]:
    if sampling not in SAMPLINGS:
        raise ValueError(f"sampling must be one of {SAMPLINGS}, not {sampling!r}")
    if radius not in RADII:
        raise ValueError(f"radius must be one of {RADII}, not {radius!r}")
    if lamellae and positions is not None:
        raise ValueError(
            "Relaxed positions and lamella chains do not combine: a chain replaces the "
            "annotations a relaxed node is held inside; ask for one or the other")
    graph = neuron.graph()
    build = _Build(graph=graph, radius=radius, sampling=sampling)
    if graph.number_of_nodes() == 0:
        return pd.DataFrame(columns=["n", "T", "x", "y", "z", "R", "P", "ID"]), build

    root = int(start_node) if start_node is not None else _default_root(neuron, graph)
    if not graph.has_node(root):
        raise ValueError(f"Start location ID {root} is not in c{neuron.id}'s graph")

    nodes = neuron.nodes.set_index("ID")
    radii = nodes["Rum"]
    soma_cut = float(_cell_radii(radii, graph).max()) * soma_fraction if has_soma \
        else float("inf")
    axon = {int(a) for a in (axon_ids or [])}

    # What the R column holds where it is not the stored radius.
    column: dict[int, float] = {}
    if radius == "modal":
        from ..analysis.thickness import modal_radii

        column.update(modal_radii(neuron))
        build.stored = {i: float(radii.loc[i]) for i in column if graph.has_node(i)}
    # Chain nodes: where they are, and the stack each stands for.
    chain_xyz: dict[int, np.ndarray] = {}
    stack_of: dict[int, tuple[int, ...]] = {}
    if lamellae:
        from ..analysis.lamellae import chain_graph

        graph, build.chains = chain_graph(neuron, graph)
        build.graph = graph
        for lam, keys in build.chains:
            centers = {i: nodes.loc[i, ["Xum", "Yum"]].to_numpy(dtype=float)
                       for i in lam.location_ids}
            for key, point, width in zip(keys, lam.chain_um, lam.chain_widths_um, strict=True):
                chain_xyz[key] = np.asarray(point, dtype=float)
                column[key] = 0.5 * float(width)
                stack_of[key] = lam.location_ids
                apart = {i: float(np.hypot(*(c - point[:2]))) for i, c in centers.items()}
                build.location_of[key] = min(apart, key=apart.__getitem__)
        if root not in graph:
            # The root was in a stack: its chain node nearest the annotation.
            here = nodes.loc[root, ["Xum", "Yum"]].to_numpy(dtype=float)
            root = min((key for key, ids in stack_of.items() if root in ids),
                       key=lambda key: float(np.hypot(*(chain_xyz[key][:2] - here))))

    order, parent_of = spanning_forest(graph, root)
    kept = {frozenset((c, p)) for c, p in parent_of.items() if p != -1}
    build.dropped = sorted(
        (min(a, b), max(a, b)) for a, b in graph.edges() if frozenset((a, b)) not in kept)

    index_of = {node: i + 1 for i, node in enumerate(order)}
    rows: list[_Row] = []
    for node in order:
        degree = graph.degree(node)
        chain = node in chain_xyz
        if node in axon or (chain and axon.intersection(stack_of[node])):
            swc_type = SWC_TYPES["axon"]
        elif has_soma and not chain and float(radii.loc[node]) >= soma_cut:
            swc_type = SWC_TYPES["soma"]
        elif branch_types and degree == 1:
            swc_type = SWC_TYPES["end"]
        elif branch_types and degree > 2:
            swc_type = SWC_TYPES["fork"]
        else:
            swc_type = SWC_TYPES["dendrite"]
        parent = parent_of.get(node, -1)
        if chain:
            xyz = chain_xyz[node]
        else:
            xyz = nodes.loc[node, ["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
            if positions is not None and node in positions:
                xyz = np.array([positions[node][0], positions[node][1], xyz[2]])
        rows.append(
            _Row(
                index=index_of[node],
                swc_type=swc_type,
                xyz=xyz,
                radius=column[node] if node in column else float(radii.loc[node]),
                parent=index_of[parent] if parent != -1 else -1,
            )
        )

    table = pd.DataFrame(
        {
            "n": [r.index for r in rows],
            "T": [r.swc_type for r in rows],
            "x": [r.xyz[0] for r in rows],
            "y": [r.xyz[1] for r in rows],
            "z": [r.xyz[2] for r in rows],
            "R": [r.radius for r in rows],
            "P": [r.parent for r in rows],
            "ID": order,
        }
    )
    build.rows_before = len(table)
    build.length_before_um = build.length_after_um = tree_length(table)
    if sampling == "sphere":
        ends = {key for pair in build.dropped for key in pair}
        table = _sphere_sampled(table, ends)
        build.length_after_um = tree_length(table)
    return table, build


def _cell_radii(radii: pd.Series, graph) -> pd.Series:
    """The radii of the annotations in ``graph`` -- the structure's own, not its
    child structures' -- which the soma cutoff is taken over.  Over every node
    a synapse annotation larger than the soma set the cutoff."""
    return radii[radii.index.isin(list(graph.nodes))]


def tree_length(table: pd.DataFrame) -> float:
    """The summed length of every row's link to its parent, microns: the
    cable an SWC reader measures."""
    if len(table) == 0:
        return 0.0
    xyz = table[["x", "y", "z"]].to_numpy(dtype=float)
    parent = table["P"].to_numpy(dtype=np.int64)
    child = np.nonzero(parent > 0)[0]
    return float(np.linalg.norm(xyz[child] - xyz[parent[child] - 1], axis=1).sum())


def _sphere_sampled(table: pd.DataFrame, keep_ids) -> pd.DataFrame:
    """The rows Bradley & Withers's minimal sphere network keeps.

    Along each unbranched path of the tree, from its end nearer the root, with
    spheres of the ``R`` column (``sbfsem.analysis.thickness.sphere_network``).
    Tips, branch points, roots and the rows in ``keep_ids`` (both ends of each
    dropped link) are kept; every kept row's parent becomes its nearest kept
    ancestor, so the rows stay in breadth-first order.
    """
    from ..analysis.thickness import sphere_network

    n = len(table)
    parent = table["P"].to_numpy(dtype=np.int64) - 1          # 0-based; -2 for a root
    xyz = table[["x", "y", "z"]].to_numpy(dtype=float)
    radius = table["R"].to_numpy(dtype=float)
    children: list[list[int]] = [[] for _ in range(n)]
    for i, p in enumerate(parent):
        if p >= 0:
            children[p].append(i)
    anchor = np.array([parent[i] < 0 or len(children[i]) != 1 for i in range(n)])
    anchor |= table["ID"].isin(list(keep_ids)).to_numpy()
    keep = anchor.copy()
    for start in np.nonzero(anchor)[0]:
        for first in children[start]:
            path = [int(start), first]
            while not anchor[path[-1]]:
                path.append(children[path[-1]][0])
            chosen = sphere_network(xyz[path], radius[path])
            keep[np.asarray(path)[chosen]] = True
    # Nearest kept ancestor, in row order (every parent precedes its child).
    up = np.empty(n, dtype=np.int64)
    for i in range(n):
        up[i] = i if keep[i] else up[parent[i]]
    rows = np.nonzero(keep)[0]
    new_index = np.full(n, -1, dtype=np.int64)
    new_index[rows] = np.arange(1, len(rows) + 1)
    out = table.iloc[rows].copy()
    out["n"] = new_index[rows]
    out["P"] = [int(new_index[up[parent[i]]]) if parent[i] >= 0 else -1 for i in rows]
    return out.reset_index(drop=True)


def spanning_forest(graph, root: int) -> tuple[list[int], dict[int, int]]:
    """The rooted forest an SWC keeps: row order, and child -> parent (-1 for a root).

    Breadth-first from ``root``, then from the lowest Location ID of every other
    connected piece, so disconnected fragments still export.  A tree holds one
    link fewer than it has nodes: each loop in the annotation graph loses the
    link that closes it, which is the one breadth-first reaches last (see
    ``sbfsem.analysis.skeleton_check.dropped_links``).

    This asked ``nx.has_path(root, n)`` of every node until 2026-09-21 -- 2.7 of
    the 3.8 s an export of RPC1 c192 (2,849 annotations) took.  Same rows, same
    parents, from the connected components instead.
    """
    order: list[int] = []
    parent_of: dict[int, int] = {}
    pieces = sorted(nx.connected_components(graph), key=min)
    pieces.sort(key=lambda piece: root not in piece)   # the root's piece first; stable
    for piece in pieces:
        start = root if root in piece else min(piece)
        parent_of[start] = -1
        order.append(start)
        for parent, child in nx.bfs_edges(graph, start):
            parent_of[child] = parent
            order.append(child)
    return order, parent_of


def _default_root(neuron, graph) -> int:
    get_soma = getattr(neuron, "get_soma_id", None)
    if get_soma is not None:
        try:
            candidate = int(get_soma())
            if graph.has_node(candidate):
                return candidate
        except (ValueError, KeyError):
            pass
    return int(min(graph.nodes))


def type_legend(table: pd.DataFrame) -> str:
    """The ``T`` codes a table uses, spelled out for the file header."""
    names = {
        SWC_TYPES["undefined"]: "undefined",
        SWC_TYPES["soma"]: "soma",
        SWC_TYPES["axon"]: "axon",
        SWC_TYPES["dendrite"]: "dendrite",
        SWC_TYPES["apical"]: "apical dendrite",
        SWC_TYPES["fork"]: "branch point",
        SWC_TYPES["end"]: "end point",
        SWC_TYPES["custom"]: "custom",
    }
    used = sorted(int(t) for t in table["T"].unique()) if len(table) else []
    return ", ".join(f"{t} {names.get(t, 'custom')}" for t in used)


def provenance_lines(neuron, table: pd.DataFrame, options: dict, relaxed=None,
                     build: _Build | None = None) -> list[str]:
    """Header lines that say what the file cannot: how the rows relate to Viking.

    Asked for by Rebecca Pfeiffer (2026-09-18, 2026-09-21), who had been
    inferring each of these: the section thickness (from the Z quantization),
    how many connected pieces the cell is in (from the roots), and which links
    of the annotation graph the tree had to drop.  An SWC is a forest, so every
    loop annotated in Viking loses the link that closes it; with every neurite
    node typed 3 nothing else in the file records that a tip was not a tip in
    Viking.  Both ends are given as Location IDs and as rows of this file.

    ``build`` is how :func:`swc_table` made the rows (``write_swc`` passes
    it): the links its tree dropped, what the ``R`` column holds, and what the
    export options did.  Without it the rows are taken to be one per
    annotation, as they are by default.
    """
    from ..analysis.skeleton_check import dropped_links

    graph = neuron.graph()
    scale = tuple(getattr(neuron, "volume_scale", None) or ())
    lines = []
    if len(scale) > 2:
        lines.append(f"SECTION_THICKNESS_UM {float(scale[2]) / 1e3:.4f}")
    stamp = odata_utc(getattr(neuron, "data_stamp", None))
    if stamp is not None:
        # The same cell exported another day, after an edit in Viking, is
        # another file with the same options.
        lines.append(f"ANNOTATIONS_STAMP {stamp.isoformat()} (the newest LastModified of the "
                     "cell's annotations; Viking writes UTC digits, read as UTC here whatever "
                     "zone its OData labels them with)")
    if len(table) == 0:
        return lines
    row_of = dict(zip(table["ID"].astype(int), table["n"].astype(int), strict=True))
    if build is None:
        location_of = {n: i for i, n in row_of.items()}
        parent_of = {
            int(i): int(location_of.get(int(p), -1))
            for i, p in zip(table["ID"], table["P"], strict=True)
        }
        dropped = dropped_links(neuron, parent_of)
    else:
        dropped = build.dropped
    chains = build.chains if build is not None else []
    location = build.location_of if build is not None else {}
    roots = int((table["P"] == -1).sum())
    pieces = nx.number_connected_components(graph)
    loops = graph.number_of_edges() - graph.number_of_nodes() + pieces
    lines.append(
        f"ANNOTATION_GRAPH {graph.number_of_nodes()} annotations, {graph.number_of_edges()} links, "
        f"{pieces} connected piece(s), {loops} loop(s)")
    lines.append(f"ROOTS {roots} (one per connected piece; the first row is the soma's)")
    # Rebecca Pfeiffer (2026-10-07): the Morphometrics dendritic length and
    # the Skeleton check's rim-corrected length each take something off for
    # the soma, and her pipeline takes off its own; the file says it takes
    # nothing, so no one subtracts a second time from a corrected figure.
    soma_rows = int((table["T"] == SWC_TYPES["soma"]).sum())
    if options.get("has_soma", True):
        fraction = float(options.get("soma_fraction", 0.8))
        radii = _cell_radii(neuron.nodes.set_index("ID")["Rum"], graph)
        lines.append(
            f"SOMA cutoff {float(radii.max()) * fraction:.3f} um ({fraction:.2f} of the largest "
            f"stored radius, {float(radii.max()):.3f} um at Location {int(radii.idxmax())}): "
            f"{soma_rows} rows typed 1 (soma); typing only, nothing trimmed: no row is moved or "
            "dropped for the soma, and every link counts in the file's length, those from the "
            "soma's center out to its edge included")
    else:
        lines.append("SOMA none (has_soma=false): no row typed 1, nothing trimmed; every link "
                     "counts in the file's length")
    if chains:
        lines.append(
            f"DROPPED_LINKS {len(dropped)} (links a tree cannot hold, one per loop: Location IDs, "
            "a chain node's being the stack annotation nearest it, then rows of this file)")
    else:
        lines.append(
            f"DROPPED_LINKS {len(dropped)} (links in Viking that a tree cannot hold, one per loop: "
            "Location IDs, then rows of this file)")
    lines.extend(
        f"DROPPED_LINK {location.get(a, a)} {location.get(b, b)} rows {row_of.get(a, -1)} "
        f"{row_of.get(b, -1)}" for a, b in dropped)
    live = getattr(neuron, "live_registration", None)
    if live:
        lines.append(
            f"SECTION_TRANSFORMS current {live.get('vikingxml')} "
            f"(Last-Modified {live.get('xml_last_modified')}): {live.get('n_moved', 0)} of "
            f"{live.get('n_annotations', 0)} annotations are away from their stored position, "
            f"median {float(live.get('median_um', 0.0)):.3f} um, largest "
            f"{float(live.get('max_um', 0.0)):.2f} um, on sections {live.get('sections_moved') or 'none'} "
            "(positions mapped from section coordinates through the volume's current stos, as "
            "Viking draws them)")
    corrected = getattr(neuron, "section_correction", None)
    if corrected:
        built = corrected.get("built_utc") or "unknown"
        what = {
            "curvefit": "Viking's curve fit along each traced process",
            "all": "Viking's published residual registration field, then its curve fit along "
                   "each traced process",
        }.get(corrected.get("mode") or "neighbor",
              "Viking's published residual registration field, as Viking's own exports apply it")
        lines.append(
            f"SECTION_CORRECTION {corrected.get('volume_name')} {corrected.get('stos_group')} "
            f"built {built}: {corrected.get('n_moved', 0)} of {corrected.get('n_annotations', 0)} "
            f"annotations moved in x and y, median {float(corrected.get('median_um', 0.0)):.3f} um, "
            f"largest {float(corrected.get('max_um', 0.0)):.2f} um ({what})")
    if relaxed is None:
        where = ("annotation centers where the volume's current section transforms put them"
                 if live else "annotation centers as Viking stores them")
        lines.append(
            "POSITIONS viking " + ("corrected " if corrected else "")
            + ("current " if live else "") + f"({where}"
            + (", each moved by the section correction above" if corrected else "")
            + "; a closed curve's is the area centroid of its traced outline)")
    else:
        held = ("its traced outline scaled about its centroid, or its circle"
                if relaxed.bound == "outline" else "a circle of its stored radius")
        lines.append(
            f"POSITIONS relaxed f={relaxed.f:.2f} bound={relaxed.bound}: every node moved toward "
            f"its neighbors to shorten the path, never outside {relaxed.f:.2f} of its own "
            f"annotation ({held}); tips fixed; z and R unchanged")
        lines.append(
            f"RELAXED_STATS {relaxed.n_moved} of {graph.number_of_nodes()} nodes moved, median "
            f"{relaxed.median_move_um:.3f} um, largest {relaxed.max_move_um:.2f} um; length over "
            f"all links {relaxed.length_before_um:.2f} -> {relaxed.length_after_um:.2f} um")
    if chains:
        from ..analysis.lamellae import ASPECT_MIN

        lams = [lam for lam, _ in chains]

        def total(name: str) -> float:
            return float(sum(getattr(lam, name) for lam in lams))

        lines.append(
            f"LAMELLAE {len(lams)} stacks written as chains in place of their "
            f"{sum(len(lam.location_ids) for lam in lams)} annotations: "
            f"{total('cable_um'):.2f} um of chain and {total('joins_um'):.2f} um of links joining "
            f"it to the cell, in place of {total('internal_um'):.2f} um of links inside the stacks "
            f"and {total('leaving_um'):.2f} um of links leaving them: "
            f"{total('written_change_um'):+.2f} um; {total('joins_inside_um'):.2f} um of the "
            "joining links lies inside the stacks' outlines (spokes); "
            f"{total('beyond_um'):.2f} um of chain lies beyond the outermost branch and counts as "
            f"cable (the Skeleton check's net, which counts no link inside the outlines, is "
            f"{total('net_change_um'):+.2f} um)")
        lines.append(
            f"LAMELLA_RULE aspect_min={ASPECT_MIN:.2f}: a traced profile whose long principal axis "
            "is at least that many times its short one; such profiles linked on consecutive "
            "sections are one stack; its chain runs through the midpoint of each chord across the "
            "union of the stack's outlines along their long axis, each end trimmed by half the "
            "local width, and keeps a node where discs of half the local width touch (Bradley & "
            "Withers 2016 eq. 1); a chain node's z is the mean of the stack's profiles containing "
            "it and its R half the local width; each link leaving the stack joins the chain node "
            "nearest the annotation at its other end; the chain's ends are tips")
        lines.extend(
            f"LAMELLA {k} locations {','.join(str(i) for i in lam.location_ids)} sections "
            f"{lam.sections[0]}-{lam.sections[1]} rows "
            f"{' '.join(str(row_of[key]) for key in keys if key in row_of)} "
            f"cable={lam.cable_um:.2f} joins={lam.joins_um:.2f} "
            f"joins_inside={lam.joins_inside_um:.2f} internal={lam.internal_um:.2f} "
            f"leaving={lam.leaving_um:.2f} replaced={lam.replaced_um:.2f} "
            f"beyond={lam.beyond_um:.2f}"
            for k, (lam, keys) in enumerate(chains, 1))
    if build is not None:
        if build.sampling == "sphere":
            lines.append(
                f"SAMPLING sphere: {len(table)} of {build.rows_before} rows kept, links "
                f"{build.length_before_um:.2f} -> {build.length_after_um:.2f} um; along each "
                "unbranched path of the tree, from its end nearer the root, the next row kept is "
                "the farthest by distance of the unbroken run of following rows whose spheres of "
                "radius R touch the kept one's (Bradley & Withers 2016 eq. 1); tips, branch points, "
                "roots and both ends of each dropped link are kept")
        else:
            lines.append("SAMPLING annotations: one row per annotation"
                         + (", a lamella stack's being its chain nodes" if chains else ""))
        if build.radius == "modal":
            from ..analysis.thickness import BANDWIDTH_UM, PEAK_WINDOW_UM

            lines.append(
                "R_COLUMN modal: a closed curve's modal radius (Bradley & Withers 2016 sec. 3.4, in "
                "the section plane: from the center of the outline's largest inscribed circle, the "
                "first peak at or above that circle's radius of the outline's length at each "
                f"distance over the distance, smoothed by a {BANDWIDTH_UM:.2f} um Gaussian, a peak "
                f"being the largest value within {PEAK_WINDOW_UM:.2f} um); a circle's stored "
                "radius; Viking's stored radius of each closed curve follows as R_STORED row r")
        else:
            lines.append(
                "R_COLUMN stored: Viking's stored radius of each annotation (a closed curve's is "
                "near the radius of the circle of its area, not equal to it)")
    shown = {"branch_types": True, "soma_fraction": 0.8, "has_soma": True, "lamellae": False,
             "radius": "stored", "sampling": "annotations", **options}
    lines.append("OPTIONS " + " ".join(
        f"{key}={str(value).lower() if isinstance(value, bool) else value}"
        for key, value in sorted(shown.items()) if key != "axon_ids" or value))
    if build is not None and build.stored:
        stored = sorted((row_of[i], r) for i, r in build.stored.items() if i in row_of)
        lines.extend(f"R_STORED {row} {r:.4f}" for row, r in stored)
    return lines


def write_swc(neuron, path: str | Path, *, relax: float | None = None,
              relax_bound: str = "outline", lamellae: bool = False,
              sampling: str = "annotations", radius: str = "stored", **kwargs) -> Path:
    """Write a neuron to an ``.swc`` file and return the path.

    Keyword arguments go to :func:`swc_table`.  The header names the volume
    and structure, the units, and what each value of the type column means.

    ``relax`` (0 to 1) writes the nodes where
    :func:`sbfsem.analysis.relax.relax_skeleton` puts them -- each moved
    toward its neighbors but never outside that fraction of its own
    annotation -- and says so in the header, with the bound (``relax_bound``:
    ``outline`` or ``radius``) and what moved.  ``None`` writes Viking's
    positions.

    ``lamellae``, ``sampling`` and ``radius`` are the export options of the
    module docstring; each is recorded in the header's ``OPTIONS`` and
    described by a line of its own.  Relaxation and lamella chains do not
    combine (``ValueError``).
    """
    return _write_swc(neuron, path, relax=relax, relax_bound=relax_bound, lamellae=lamellae,
                      sampling=sampling, radius=radius, **kwargs)[0]


def _write_swc(neuron, path: str | Path, *, relax: float | None = None,
               relax_bound: str = "outline", lamellae: bool = False,
               sampling: str = "annotations", radius: str = "stored",
               **kwargs) -> tuple[Path, pd.DataFrame]:
    """:func:`write_swc`, also returning the rows written."""
    from ..core.volumes import Volumes

    if relax is not None and lamellae:
        raise ValueError(
            "Relaxed positions and lamella chains do not combine: a chain replaces the "
            "annotations a relaxed node is held inside; ask for one or the other")
    relaxed = None
    if relax is not None:
        from ..analysis.relax import relax_skeleton

        relaxed = relax_skeleton(neuron, float(relax), bound=relax_bound)
        kwargs = {**kwargs, "positions": relaxed.positions}
    table, build = _build(neuron, lamellae=lamellae, sampling=sampling, radius=radius, **kwargs)
    path = Path(path)
    volume = Volumes.from_str(neuron.source)
    region = {
        "NeitzTemporalMonkey": "Temporal",
        "NeitzNasalMonkey": "Nasal",
        "NeitzInferiorMonkey": "Inferior",
    }.get(volume.source, "")
    try:
        soma_area = np.pi * float(neuron.get_soma_size()) ** 2
    except (AttributeError, ValueError):
        soma_area = float("nan")

    with path.open("w", encoding="utf-8") as fh:
        fh.write("# ORIGINAL_SOURCE SBFSEM-pytools\n")
        fh.write(f"# CREATURE {volume.animal}\n")
        fh.write(f"# REGION {region}\n".rstrip() + "\n")
        fh.write("# FIELD/LAYER\n# TYPE\n# CONTRIBUTOR\n")
        fh.write("# REFERENCE\n# RAW\n# EXTRAS\n")
        fh.write(f"# SOMA_AREA {soma_area:.3f}\n")
        fh.write("# SHRINKAGE_CORRECTION 1.0 1.0 1.0\n")
        fh.write("# VERSION_NUMBER 1.0\n")
        fh.write(f"# VERSION_DATE {date.today().isoformat()}\n")
        fh.write("# SCALE 1.0 1.0 1.0\n")
        # What a reader needs to interpret the columns without this code base:
        # the structure and volume the rows came from, the units, and the SWC
        # type codes actually present (they are not Viking TypeCodes).
        fh.write(f"# STRUCTURE c{neuron.id} in {volume.name}\n")
        fh.write("# COLUMNS n T x y z R P\n")
        fh.write(f"# TYPE_CODES {type_legend(table)}\n")
        fh.write("# TYPE_NOTE T is the SWC type, not the Viking TypeCode; "
                 "5 and 6 mark branch points and end points when present\n")
        fh.write("# UNITS x y z R in microns (registered volume frame; "
                 "z is the section number times the section thickness)\n")
        fh.write("# ROW_ORDER breadth-first from the soma annotation; "
                 "each parent index precedes its children\n")
        shown = {k: v for k, v in kwargs.items() if k != "positions"}
        shown.update(lamellae=bool(lamellae), sampling=sampling, radius=radius)
        if relaxed is not None:
            shown.update(relax=relaxed.f, relax_bound=relaxed.bound)
        for line in provenance_lines(neuron, table, shown, relaxed, build):
            fh.write(f"# {line}\n")
        fh.write("\n")
        for row in table.itertuples():
            fh.write(
                f"{row.n} {row.T} {row.x:.4f} {row.y:.4f} {row.z:.4f} {row.R:.4f} {row.P}\n"
            )
    return path, table
