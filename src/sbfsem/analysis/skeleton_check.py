"""Skeleton check: what in a cell's annotation graph will mislead a path length.

There is no MATLAB counterpart.  Added 2026-09-21 after Rebecca Pfeiffer's
notes on the SWC export (``docs/context/to-becca-skeleton-2026-09-18.rst``) and
her PLA_Pipeline (MIT), whose detectors work from an SWC and a mesh.  Here the
annotation graph, the annotation types and the traced outlines are in hand, so
most of what her pipeline infers is exact:

============== =============================================== ==========
kind           test                                            fixed in
============== =============================================== ==========
loop           a link the annotation graph has beyond a tree   Viking
fragment       a connected component without the soma          Viking
jump           a link far longer than its two radii allow      Viking
hairpin        a sustained reversal along an unbranched path   Viking
spike          a node far from the mean of its neighbours      Viking
spoke          a link running inside a much wider annotation   the export
stub           a tip branch no longer than its parent's radius the export
lamella        an elongated profile, or a stack of them, whose the export
               cable a chain along it would measure differently
============== =============================================== ==========

*Fixed in Viking* means an annotation error: correct it once at the source and
every consumer gets it.  *Fixed in the export* means a property of reading a
contour stack as a centreline, which no annotator should be asked to change.

A **spoke** is the part of a link that lies inside the wider of its two
annotations: a thin process leaving a wide profile is linked to that profile's
*centre*, so the link starts a radius or more before the process does.  The
rim is the wide annotation's outline along the direction of the link -- its
radius for a circle, the traced polygon for a closed curve (Becca's
definition, 2026-09-21: "distance from the centre to the outline along the
direction of the departing link"), and the link is clipped against that
outline exactly.  On RPC1 c192 spokes are 14 % of the cell's length by that
rule, 11 % with the stored radius as the rim.

The hairpin and spike tests are hers (``find_hairpins``, ``find_spikes``),
with the absolute-micron floors restated in section thicknesses so they carry
between volumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import numpy as np

__all__ = [
    "KINDS",
    "Problem",
    "SkeletonCheck",
    "check_skeleton",
    "dropped_links",
    "spoke_lengths",
]

#: Kind -> where it is fixed.
KINDS = {
    "loop": "viking",
    "fragment": "viking",
    "jump": "viking",
    "hairpin": "viking",
    "spike": "viking",
    "spoke": "export",
    "stub": "export",
    "lamella": "export",
}


@dataclass(frozen=True)
class Problem:
    """One thing to look at, addressed by Viking Location IDs."""

    kind: str
    #: Every annotation involved, in path order where there is one.
    location_ids: tuple[int, ...]
    #: The annotation to go to (in the viewer, and in Viking).
    anchor_id: int
    xyz_um: tuple[float, float, float]
    z_section: int
    #: Cable length involved, microns.
    length_um: float
    detail: str
    #: ``viking`` or ``export`` (see :data:`KINDS`).
    fix: str
    #: The links among ``location_ids``, for drawing the problem.
    links: tuple[tuple[int, int], ...] = ()
    extra: dict = field(default_factory=dict)


@dataclass
class SkeletonCheck:
    neuron_id: int
    source: str
    summary: dict
    problems: list[Problem]

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(KINDS, 0)
        for p in self.problems:
            out[p.kind] += 1
        return out


# -- helpers ------------------------------------------------------------------

def _tables(neuron):
    nodes = neuron.get_cell_nodes()
    ids = nodes["ID"].astype(int).to_numpy()
    xyz = dict(zip(ids, nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float), strict=True))
    radius = dict(zip(ids, nodes["Rum"].astype(float), strict=True))
    section = dict(zip(ids, nodes["Z"].astype(int), strict=True))
    return nodes, xyz, radius, section


def _outlines(neuron) -> dict[int, np.ndarray]:
    """Location ID -> the traced outer ring (N, 2) in microns, where there is one."""
    frame = getattr(neuron, "geometries", None)
    if frame is None or len(frame) == 0:
        return {}
    out: dict[int, np.ndarray] = {}
    for row in frame.itertuples():
        curves = list(row.Curve) if row.Curve is not None else []
        if curves and len(curves[0]) >= 3:
            out[int(row.ID)] = np.asarray(curves[0], dtype=float)[:, :2]
    return out


def _inside_polygon(point, outline) -> bool:
    """Even-odd test of one XY point against a closed ring."""
    x, y = float(point[0]), float(point[1])
    x0, y0 = outline[:, 0], outline[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    crosses = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return bool(np.count_nonzero(crosses & (x < at)) % 2)


def _inside_fraction(start, end, radius, outline) -> float:
    """The fraction of the XY segment ``start -> end`` inside the annotation
    centered on ``start``: its circle, or its traced outline.

    The outline is clipped exactly -- every crossing, inside and outside taken
    in turn -- rather than by a ray from the centre to "the rim".  A lobed or
    curved profile is crossed more than once, and its centroid can lie outside
    it altogether: RPC1 c192's Location 216973 is a 9 by 3 um crescent whose
    centroid is 0.24 um off its own outline, and a ray from there to the
    farthest crossing counted the gap as inside (found 2026-09-22 on Rebecca
    Pfeiffer's in-plane branch point).
    """
    delta = np.asarray(end, dtype=float) - np.asarray(start, dtype=float)
    span = float(np.hypot(delta[0], delta[1]))
    if span < 1e-12:
        return 0.0
    if outline is None:
        return min(1.0, radius / span)
    a = outline - start
    e = np.roll(outline, -1, axis=0) - outline
    # start + t*delta meets a + s*e:  t*delta - s*e = a.
    den = delta[0] * (-e[:, 1]) - delta[1] * (-e[:, 0])
    ok = np.abs(den) > 1e-15
    den = np.where(ok, den, 1.0)
    t = (a[:, 0] * (-e[:, 1]) - a[:, 1] * (-e[:, 0])) / den
    u = (delta[0] * a[:, 1] - delta[1] * a[:, 0]) / den
    cuts = np.sort(t[ok & (t > 0) & (t < 1) & (u >= 0) & (u < 1)])
    edges = np.concatenate([[0.0], cuts, [1.0]])
    inside = _inside_polygon(start, outline)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        if inside:
            total += hi - lo
        inside = not inside
    return float(total)


def spoke_lengths(neuron, *, spoke_ratio: float = 3.0) -> dict[tuple[int, int], float]:
    """Link ``(wide, thin)`` -> the length of it inside the wide annotation.

    Only links whose wider end has at least ``spoke_ratio`` times the radius
    of the other are spokes; between annotations of like size the link *is*
    the centreline.
    """
    _, xyz, radius, _ = _tables(neuron)
    outlines = _outlines(neuron)
    out: dict[tuple[int, int], float] = {}
    for a, b in neuron.graph().edges():
        if a not in xyz or b not in xyz:
            continue
        wide, thin = (a, b) if radius[a] >= radius[b] else (b, a)
        if radius[wide] < spoke_ratio * max(radius[thin], 1e-9):
            continue
        delta = xyz[thin] - xyz[wide]
        length = float(np.linalg.norm(delta))
        dxy = float(np.hypot(delta[0], delta[1]))
        if length <= 0:
            continue
        if dxy < 1e-9:
            out[(wide, thin)] = 0.0   # straight along Z: nothing of it is in-plane
            continue
        # The part of the link whose XY projection lies inside the wide profile.
        out[(wide, thin)] = length * _inside_fraction(
            xyz[wide][:2], xyz[thin][:2], radius[wide], outlines.get(wide))
    return out


def dropped_links(neuron, parent_of: dict[int, int] | None = None) -> list[tuple[int, int]]:
    """Links of the annotation graph that a rooted tree cannot keep.

    A tree holds one fewer link than it has nodes, so every loop costs the SWC
    one link.  ``parent_of`` is the exported tree (child -> parent Location ID,
    -1 for a root); without it the same breadth-first tree the exporter builds
    is used.
    """
    graph = neuron.graph()
    if parent_of is None:
        from ..export.swc import _default_root, spanning_forest

        if graph.number_of_nodes() == 0:
            return []
        _, parent_of = spanning_forest(graph, _default_root(neuron, graph))
    kept = {frozenset((c, p)) for c, p in parent_of.items() if p != -1}
    return sorted(
        (min(a, b), max(a, b)) for a, b in graph.edges() if frozenset((a, b)) not in kept
    )


def _paths(graph: nx.Graph) -> list[list[int]]:
    """Unbranched runs between anchors (nodes whose degree is not 2)."""
    anchors = {n for n, d in graph.degree() if d != 2}
    seen: set[frozenset] = set()
    out: list[list[int]] = []
    for start in anchors:
        for nxt in graph[start]:
            if frozenset((start, nxt)) in seen:
                continue
            path = [start, nxt]
            seen.add(frozenset((start, nxt)))
            while path[-1] not in anchors:
                step = [n for n in graph[path[-1]] if n != path[-2]]
                if not step:
                    break
                seen.add(frozenset((path[-1], step[0])))
                path.append(step[0])
            out.append(path)
    return out


def _runs(flags: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for k in flags:
        if runs and k == runs[-1][-1] + 1:
            runs[-1].append(k)
        else:
            runs.append([k])
    return runs


# -- the check ----------------------------------------------------------------

def check_skeleton(
    neuron,
    *,
    soma_fraction: float = 0.8,
    spoke_ratio: float = 3.0,
    spoke_list_um: float = 1.0,
    stub_factor: float = 1.0,
    lamella_list_um: float = 0.5,
    jump_um: float = 2.0,
    jump_outlier: float = 4.0,
    hairpin_angle: float = 120.0,
    hairpin_window: int = 3,
    spike_factor: float = 4.0,
    spike_window: int = 4,
) -> SkeletonCheck:
    """Find the loops, fragments, jumps, hairpins, spikes, spokes, stubs and lamellae.

    Every spoke counts toward the summary's ``spoke_length_um``; only those
    with at least ``spoke_list_um`` inside the wide annotation are listed,
    since a wide cell has hundreds.  Lamellae (``sbfsem.analysis.lamellae``)
    are listed when the chain along them would change the cable by at least
    ``lamella_list_um`` either way; every one counts in the summary.
    """
    nodes, xyz, radius, section = _tables(neuron)
    graph = neuron.graph()
    for a, b in graph.edges():
        graph.edges[a, b]["weight"] = (
            float(np.linalg.norm(xyz[a] - xyz[b])) if a in xyz and b in xyz else 0.0
        )
    thickness = float(neuron.volume_scale[2]) / 1e3 if len(neuron.volume_scale) > 2 else 0.0
    problems: list[Problem] = []

    def problem(kind, ids, anchor, length, detail, **extra):
        p = xyz[anchor]
        members = tuple(int(i) for i in ids)
        problems.append(Problem(
            kind=kind, location_ids=members, anchor_id=int(anchor),
            xyz_um=(float(p[0]), float(p[1]), float(p[2])), z_section=int(section[anchor]),
            length_um=float(length), detail=detail, fix=KINDS[kind],
            links=tuple((int(a), int(b)) for a, b in graph.subgraph(members).edges()),
            extra=extra,
        ))

    def span(ids):
        zs = [section[i] for i in ids]
        return f"section {min(zs)}" if min(zs) == max(zs) else f"sections {min(zs)}-{max(zs)}"

    # Loops: exact.  A tree holds one link fewer than it has nodes, so each
    # loop costs the export one link; the loop shown for a dropped link is the
    # shortest way round without it.  (A cycle basis has the same count, but
    # its rings can be long detours through one another: RPC1 c2628's was 64
    # annotations where the ring that matters is 4.)
    dropped = dropped_links(neuron) if graph.number_of_nodes() else []
    for a, b in dropped:
        w = graph[a][b]["weight"]
        graph.remove_edge(a, b)
        try:
            ring = nx.shortest_path(graph, a, b, weight="weight")
        except nx.NetworkXNoPath:    # cannot happen for a dropped link
            ring = [a, b]
        graph.add_edge(a, b, weight=w)
        perimeter = w + sum(graph[ring[i]][ring[i + 1]]["weight"] for i in range(len(ring) - 1))
        problem("loop", ring, a, perimeter,
                f"{len(ring)} annotations linked in a ring, {span(ring)}, {perimeter:.2f} um round; "
                f"a tree cannot hold it, and the export drops the link {a}-{b} ({w:.2f} um)",
                dropped_link=[int(a), int(b)])

    # Fragments: exact.  The piece holding the soma is the cell.
    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    try:
        soma_id = int(neuron.get_soma_id())
    except (ValueError, KeyError):
        soma_id = next(iter(components[0])) if components else -1
    main = next((c for c in components if soma_id in c), components[0] if components else set())
    if len(components) > 1:
        from scipy.spatial import cKDTree

        main_ids = list(main)
        tree = cKDTree(np.array([xyz[i] for i in main_ids]))
        for piece in components:
            if piece is main:
                continue
            ids = sorted(piece, key=lambda i: section[i])
            d, j = tree.query(np.array([xyz[i] for i in ids]))
            k = int(np.argmin(d))
            own = sum(w for *_, w in graph.subgraph(piece).edges(data="weight"))
            what = "a single annotation with no links" if len(ids) == 1 else (
                f"{len(ids)} annotations linked to each other and to nothing else")
            problem("fragment", ids, ids[k], own,
                    f"{what}, {span(ids)}; nearest annotation of the cell is "
                    f"{main_ids[int(j[k])]}, {d[k]:.2f} um away",
                    nearest_id=int(main_ids[int(j[k])]), gap_um=float(d[k]))

    # Spokes and jumps, link by link.
    spokes = spoke_lengths(neuron, spoke_ratio=spoke_ratio)
    for (wide, thin), inside in spokes.items():
        if inside >= spoke_list_um:
            problem("spoke", (wide, thin), thin, inside,
                    f"{inside:.2f} um of this {graph[wide][thin]['weight']:.2f} um link lies inside "
                    f"annotation {wide} (r {radius[wide]:.2f} um); the process (r {radius[thin]:.2f} um) "
                    "starts at its rim",
                    wide_id=int(wide))
    spoke_pairs = {frozenset(pair) for pair in spokes}
    # One misplaced annotation trips several tests at once -- the jump to it,
    # a hairpin through it, a spike at each neighbour (Inferior c943: one
    # 10 um link, seven flags).  As in PLA_Pipeline, a test skips what an
    # earlier one has claimed, so each cause is listed once.
    claimed: set[int] = set()
    # A jump is a link whose two annotations stand apart, edge to edge, by far
    # more than this cell's links do.  The floor alone is not a test: a
    # dendrite running in the section plane is traced as circles microns
    # apart, and RC1 c606 has 195 such links over 2 um (90th percentile
    # 2.2 um, largest 7.2) with nothing wrong with any of them.
    gaps = {
        (a, b): float(np.hypot(*(xyz[a][:2] - xyz[b][:2]))) - radius[a] - radius[b]
        for a, b in graph.edges() if frozenset((a, b)) not in spoke_pairs
    }
    apart = [v for v in gaps.values() if v > 0]
    jump_cut = max(jump_um, jump_outlier * float(np.percentile(apart, 90))) if len(apart) >= 20 \
        else jump_um
    for (a, b), excess in gaps.items():
        w = graph[a][b]["weight"]
        gap = abs(section[a] - section[b]) * thickness
        if excess > jump_cut:
            problem("jump", (a, b), a if radius[a] <= radius[b] else b, w,
                    f"linked annotations {excess:.2f} um apart edge to edge, "
                    f"{abs(section[a] - section[b])} section(s) ({gap:.2f} um) between them",
                    excess_um=excess)
            for end in (a, b):
                claimed.update(nx.single_source_shortest_path_length(
                    graph, end, cutoff=spike_window))

    # Stubs: a tip branch that never leaves the annotation it branches from.
    paths = _paths(graph)
    degree = dict(graph.degree())
    for path in paths:
        for run in (path, path[::-1]):
            base, tip = run[0], run[-1]
            if degree[tip] != 1 or degree[base] < 3 or tip == soma_id:
                continue
            length = sum(graph[run[i]][run[i + 1]]["weight"] for i in range(len(run) - 1))
            if length <= stub_factor * radius[base]:
                problem("stub", run, tip, length,
                        f"a {len(run) - 1}-annotation tip branch, {length:.2f} um long, off annotation "
                        f"{base} whose radius is {radius[base]:.2f} um",
                        base_id=int(base))

    # Hairpins and spikes along unbranched paths (after PLA_Pipeline).
    floor = 10.0 * thickness if thickness > 0 else 0.75
    for path in paths:
        pts = np.array([xyz[i] for i in path])
        rad = np.array([radius[i] for i in path])
        n = len(path)
        if n >= 2 * hairpin_window + 2:
            flags = []
            for k in range(hairpin_window, n - hairpin_window):
                u = pts[k] - pts[k - hairpin_window]
                v = pts[k + hairpin_window] - pts[k]
                den = np.linalg.norm(u) * np.linalg.norm(v)
                if den > 0 and np.degrees(np.arccos(np.clip(u @ v / den, -1, 1))) >= hairpin_angle:
                    flags.append(k)
            for run in _runs(flags):
                if len(run) < 2:
                    continue
                lo, hi = max(run[0] - hairpin_window, 0), min(run[-1] + hairpin_window, n - 1)
                ids = path[lo:hi + 1]
                length = float(np.linalg.norm(np.diff(pts[lo:hi + 1], axis=0), axis=1).sum())
                chord = float(np.linalg.norm(pts[hi] - pts[lo]))
                if claimed.intersection(ids):
                    continue
                # Twice the cable for the progress made: a sparsely traced
                # in-plane dendrite zigzags between its circles without
                # doubling back, and is not a hairpin.
                if length >= max(0.3, 2 * float(np.median(rad[lo:hi + 1]))) \
                        and length >= 2.0 * chord:
                    claimed.update(ids)
                    problem("hairpin", ids, path[run[len(run) // 2]], length,
                            f"the path doubles back: {length:.2f} um of cable for {chord:.2f} um of "
                            f"progress, {span(ids)}", tortuosity=length / max(chord, 1e-9))
        if n >= 2 * spike_window + 3:
            # Far, for this path: a few of its own steps.  Where annotations
            # are microns apart, a micron off the local mean is the tracing.
            steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
            for k in range(1, n - 1):
                if path[k] in claimed:
                    continue
                idx = [j for j in range(max(0, k - spike_window), min(n, k + spike_window + 1)) if j != k]
                dev = float(np.linalg.norm(pts[k] - pts[idx].mean(axis=0)))
                local = float(np.median(steps[max(0, k - spike_window):k + spike_window]))
                if dev > max(spike_factor * max(rad[k], 0.01), floor, 3.0 * local):
                    problem("spike", (path[k],), path[k], dev,
                            f"{dev:.2f} um from the mean of its {len(idx)} neighbours along the "
                            f"path (its radius is {rad[k]:.2f} um)", deviation_um=dev)

    # Lamellae: elongated profiles and stacks of them, chained once each.
    from .lamellae import find_lamellae

    lam = find_lamellae(neuron)
    for entry in lam.lamellae:
        if abs(entry.net_change_um) < lamella_list_um:
            continue
        stack = list(entry.location_ids)
        anchor = entry.extra["widest_id"]
        what = (f"{len(stack)} profiles on sections {entry.sections[0]}-{entry.sections[1]}"
                if len(stack) > 1 else f"one profile on section {entry.sections[0]}")
        problem("lamella", stack, anchor, abs(entry.net_change_um),
                f"{entry.long_um:.2f} by {entry.short_um:.2f} um ({what}); a chain along it "
                f"measures {entry.cable_um:.2f} um and replaces {entry.replaced_um:.2f} um of "
                f"links: {entry.net_change_um:+.2f} um, of which {entry.beyond_um:.2f} um lies "
                f"beyond the outermost branch; written into an SWC with the links joining it, "
                f"{entry.written_change_um:+.2f} um"
                + ("; a branch leaves it" if entry.is_branch_point else "; the skeleton runs through it"),
                net_um=entry.net_change_um, cable_um=entry.cable_um, replaced_um=entry.replaced_um,
                beyond_um=entry.beyond_um, chain_um=entry.chain_um.tolist(),
                written_um=entry.written_change_um, joins_um=entry.joins_um,
                joins_inside_um=entry.joins_inside_um,
                is_branch_point=entry.is_branch_point, n_profiles=len(stack))

    # A node can be flagged by several tests; the list leads with what to fix
    # in Viking, largest first within a kind.
    order = list(KINDS)
    problems.sort(key=lambda p: (order.index(p.kind), -p.length_um))

    total = float(sum(w for *_, w in graph.edges(data="weight")))
    lost = float(sum(graph[a][b]["weight"] for a, b in dropped))
    spoke_total = float(sum(spokes.values()))
    # The same links with the wide annotation's stored radius as its rim: what
    # can be computed from an SWC alone, kept so the two can be reconciled.  A
    # closed curve's stored radius understates how far its outline reaches
    # (c192: 84 um by radius, 110 um by outline).
    spoke_by_radius = float(sum(
        min(graph[wide][thin]["weight"], radius[wide]) for wide, thin in spokes))
    r_max = float(nodes["Rum"].max()) if len(nodes) else float("nan")
    summary = {
        "n_annotations": int(graph.number_of_nodes()),
        "n_links": int(graph.number_of_edges()),
        "n_components": len(components),
        "n_loops": len(dropped),
        "n_tips": int(sum(1 for d in degree.values() if d == 1)),
        "n_branch_points": int(sum(1 for d in degree.values() if d > 2)),
        "n_multifurcations": int(sum(1 for d in degree.values() if d > 3)),
        "n_isolated": int(sum(1 for d in degree.values() if d == 0)),
        "n_soma_annotations": int((nodes["Rum"] >= r_max * soma_fraction).sum()) if len(nodes) else 0,
        "total_length_um": total,
        "swc_length_um": total - lost,
        "n_spokes": len(spokes),
        "spoke_length_um": spoke_total,
        "rim_corrected_length_um": total - spoke_total,
        "spoke_length_by_radius_um": spoke_by_radius,
        "spoke_ratio": float(spoke_ratio),
        "jump_threshold_um": float(jump_cut),
        "n_elongated_profiles": lam.n_elongated,
        "n_lamellae": len(lam.lamellae),
        "n_lamella_stacks": lam.n_stacks,
        "n_lamella_branch_points": sum(1 for e in lam.lamellae if e.is_branch_point),
        "lamella_cable_um": lam.cable_um,
        "lamella_replaced_um": lam.replaced_um,
        "lamella_net_um": lam.net_change_um,
        "lamella_beyond_um": float(sum(e.beyond_um for e in lam.lamellae)),
        # What an SWC written with the chains gains (``swc_lamellae``): the
        # links joining each chain to the cell count, and of them the part
        # inside the outlines is the joins' spokes.
        "lamella_written_net_um": lam.written_change_um,
        "lamella_joins_inside_um": float(sum(e.joins_inside_um for e in lam.lamellae)),
        "section_thickness_um": thickness,
        "dropped_links": [list(pair) for pair in dropped],
    }
    return SkeletonCheck(int(neuron.id), str(neuron.source), summary, problems)
