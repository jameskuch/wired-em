"""Lamellae: profiles that are wide because they are long in the section plane.

A branch is linked to the *center* of the profile it leaves.  For a round
profile the rim correction (``skeleton_check.spoke_lengths``) removes the part
of that link inside the profile and the picture is right.  For a profile that
is 9 µm long and 3 µm wide the picture is wrong twice over: the profile is
itself cable -- membrane along its whole length, where synapses sit -- that no
link represents, and every branch leaving it starts at one point and departs
on a bearing no real process takes.

Rebecca Pfeiffer's rule (2026-09-22): where a profile's length exceeds twice
its width, put a node at the midpoint of each local width along the long
axis and chain them; branches attach to the nearest chain node; below 2:1
leave the profile a single point.  The chain follows a crescent because each
midpoint sits on the medial axis; the rounded ends are trimmed by half the
local width, which is where a medial axis stops.

Two things the measurements added (RPC1 c192, 2026-09-22):

* **A stack is one lamella.**  571 of the cell's 624 elongated profiles sit
  in stacks -- the same sheet, or a process lying flat, seen on consecutive
  sections and linked in Z.  The skeleton runs *down* such a stack, a section
  at a time, where the cable runs *along* it: over the 65 stacks of four or
  more sections the centroid path is 160 µm and the stacks' long axes 250.
  So the chain is built for the stack, from the union of its outlines, and
  replaces every link inside the stack as well as the spokes leaving it.
* **The chain is walked at the local thickness.**  The dense midpoint chain
  inherits the outline's roughness (Location 216973: 4.5 µm of sideways
  wander for 1.8 of drift); it is resampled with discs of half the local
  width -- Bradley & Withers 2016's minimal sphere network, their eq. 1
  (``sbfsem.analysis.thickness.sphere_network``) -- before it is measured.
  Until 2026-09-25 the next node was the farthest *index* whose disc still
  touched, run unbroken or not; with the paper's rule c192's 167 chains
  measure 423.06 µm, not 421.07, and the rule +50.50 µm, not +48.51.

Nothing here changes the annotations.  ``find_lamellae`` reports, and
``chain_graph`` builds the graph with every stack replaced by its chain, which
``sbfsem.export.swc`` writes with ``lamellae=True``.  Rebecca Pfeiffer's
answer to the open question (2026-09-23): chain beyond the outermost branch is
cable -- the flat ends carry contacts at 1.3 times the density of the rest --
so a chain's ends are tips and its whole length counts.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from .skeleton_check import _inside_fraction, _inside_polygon, _outlines, _tables
from .thickness import sphere_network

__all__ = ["Lamella", "LamellaReport", "chain_graph", "find_lamellae", "medial_chain"]

#: A profile is a lamella from this ratio of long to short axis.
ASPECT_MIN = 2.0
#: Along-axis sampling of the profile, microns.
STEP_UM = 0.05


@dataclass
class Lamella:
    #: The annotations of the stack (one for a single profile), in section order.
    location_ids: tuple[int, ...]
    sections: tuple[int, int]
    #: The chain, (K, 3) microns, ends trimmed; each node at the mean Z of
    #: the stack's profiles whose outlines contain it.
    chain_um: np.ndarray
    #: Extents of the stack's union outline along and across its long axis.
    long_um: float
    short_um: float
    aspect: float
    #: The chain's length in 3-D, ends trimmed.
    cable_um: float
    cable_untrimmed_um: float
    #: ``long - short``, the straight-line stand-in.
    long_minus_short_um: float
    #: What the chain replaces: every link between two members of the stack
    #: in full, and of every link leaving it the part inside the outlines.
    replaced_um: float
    #: Links leaving the stack, as (other Location ID, chain node index).
    attachments: tuple[tuple[int, int], ...] = ()
    #: The local width of the union at each chain node, microns.
    chain_widths_um: np.ndarray = field(default_factory=lambda: np.empty(0))
    #: What an SWC written with the chain holds instead (``chain_graph``):
    #: the links between members in full (``internal_um``), and every link
    #: leaving the stack in full (``leaving_um``), are gone, and each leaving
    #: link becomes one from the nearest chain node to the annotation at its
    #: other end (``joins_um``).  A link between two stacks is half each's.
    internal_um: float = 0.0
    leaving_um: float = 0.0
    joins_um: float = 0.0
    #: Of the joining links, the part inside the stack's outlines: spokes,
    #: which a rim correction would take out as it does everywhere else.
    joins_inside_um: float = 0.0
    #: Chain beyond the outermost attachment at either end, microns.
    beyond_um: float = 0.0
    #: A narrower annotation leaves the stack sideways (a branch point), as
    #: opposed to a run the skeleton merely passes through.
    is_branch_point: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def net_change_um(self) -> float:
        """Chain less what it replaces, counting no link inside the outlines."""
        return self.cable_um - self.replaced_um

    @property
    def written_change_um(self) -> float:
        """What an SWC written with the chain gains in length: the chain and the
        links joining it, less the links it takes the place of."""
        return self.cable_um + self.joins_um - self.internal_um - self.leaving_um


@dataclass
class LamellaReport:
    lamellae: list[Lamella]
    n_elongated: int
    n_stacks: int
    cable_um: float
    replaced_um: float

    @property
    def net_change_um(self) -> float:
        return self.cable_um - self.replaced_um

    @property
    def written_change_um(self) -> float:
        return float(sum(lam.written_change_um for lam in self.lamellae))


def _axes(points: np.ndarray):
    """Centroid, long-axis and short-axis unit vectors, and both extents."""
    c = points.mean(axis=0)
    d = points - c
    _, _, vt = np.linalg.svd(d, full_matrices=False)
    u, v = vt[0], vt[1]
    return c, u, v, float(np.ptp(d @ u)), float(np.ptp(d @ v))


def _crossings(outline: np.ndarray, c, u, v, t0: float) -> np.ndarray:
    """Where the line t = t0 (across the long axis) crosses one outline: the
    s-coordinates, sorted, in pairs."""
    d = outline - c
    t = d @ u
    s = d @ v
    t1 = np.roll(t, -1)
    s1 = np.roll(s, -1)
    hit = ((t <= t0) & (t1 > t0)) | ((t > t0) & (t1 <= t0))
    if not hit.any():
        return np.empty(0)
    frac = (t0 - t[hit]) / (t1[hit] - t[hit])
    out = np.sort(s[hit] + frac * (s1[hit] - s[hit]))
    return out if len(out) % 2 == 0 else out[:-1]


def _merged_chords(rings, c, u, v, t0: float) -> list[tuple[float, float]]:
    """The chords across every ring at t0, overlapping ones merged: the
    chords across the stack's union."""
    spans: list[tuple[float, float]] = []
    for ring in rings:
        s = _crossings(ring, c, u, v, t0)
        spans.extend((s[k], s[k + 1]) for k in range(0, len(s) - 1, 2))
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _sphere_sample(xy: np.ndarray, w: np.ndarray):
    """The midpoints Bradley & Withers's minimal sphere network keeps, with
    discs of half the local width (``thickness.sphere_network``).  Nothing
    finer than the local thickness is structure."""
    index = sphere_network(xy, 0.5 * np.asarray(w, dtype=float))
    return xy[index], w[index]


def medial_chain(rings, z_um: float, *, step_um: float = STEP_UM):
    """The midpoint chain of one outline or of a stack of them:
    ``(chain, widths, untrimmed_chain)``, each row (x, y, z) in microns.

    Samples the long axis every ``step_um``; at each sample the chord across
    the union of the outlines that continues the chain (a U-shaped profile is
    crossed twice; the chord nearest the last midpoint is followed) gives one
    node at its midpoint and the local width as its span.  Each end is then
    trimmed by half the local width there, and the chain walked at the local
    thickness (``_sphere_sample``).
    """
    rings = [np.asarray(r, dtype=float)[:, :2]
             for r in (rings if isinstance(rings, list) else [rings])]
    rings = [r for r in rings if len(r) >= 3]
    empty = (np.empty((0, 3)), np.empty(0), np.empty((0, 3)))
    if not rings:
        return empty
    every = np.vstack(rings)
    c, u, v, long_, short = _axes(every)
    t = (every - c) @ u
    lo, hi = float(t.min()), float(t.max())
    n = max(int(np.ceil((hi - lo) / step_um)), 2)
    mids = []
    widths = []
    last: float | None = None
    for t0 in np.linspace(lo + 1e-6, hi - 1e-6, n):
        chords = _merged_chords(rings, c, u, v, t0)
        if not chords:
            continue
        if last is None:
            a, b = max(chords, key=lambda p: p[1] - p[0])
        else:
            here = last
            a, b = min(chords, key=lambda p: abs((p[0] + p[1]) / 2 - here))
        last = (a + b) / 2
        mids.append(c + t0 * u + last * v)
        widths.append(b - a)
    if len(mids) < 2:
        return empty
    dense = np.array(mids)
    w = np.array(widths)
    # Trim each end by half the local width on the dense chain, where the cut
    # is exact to a step: the medial axis of a rounded end stops that far
    # short of the tip.  "Local" is the outer fifth of the chain.
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))])
    k = max(len(w) // 5, 1)
    lo_cut = 0.5 * float(np.median(w[:k]))
    hi_cut = arc[-1] - 0.5 * float(np.median(w[-k:]))
    if hi_cut - lo_cut > step_um:
        inside = (arc >= lo_cut) & (arc <= hi_cut)
        trimmed = np.vstack([
            [np.array([np.interp(lo_cut, arc, dense[:, 0]), np.interp(lo_cut, arc, dense[:, 1])])],
            dense[inside],
            [np.array([np.interp(hi_cut, arc, dense[:, 0]), np.interp(hi_cut, arc, dense[:, 1])])],
        ])
        tw = np.concatenate([[np.interp(lo_cut, arc, w)], w[inside], [np.interp(hi_cut, arc, w)]])
    else:
        mid = len(dense) // 2
        trimmed = dense[[max(mid - 1, 0), min(mid + 1, len(dense) - 1)]]
        tw = w[[max(mid - 1, 0), min(mid + 1, len(dense) - 1)]]
    xy, tw = _sphere_sample(trimmed, tw)
    full_xy, _ = _sphere_sample(dense, w)
    return (np.column_stack([xy, np.full(len(xy), z_um)]), tw,
            np.column_stack([full_xy, np.full(len(full_xy), z_um)]))


def _arc(chain: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(chain, axis=0), axis=1).sum()) if len(chain) > 1 else 0.0


def find_lamellae(neuron, *, aspect_min: float = ASPECT_MIN,
                  step_um: float = STEP_UM) -> LamellaReport:
    """Every elongated traced profile of the cell, stacked where they are
    linked on consecutive sections, each stack chained once.  Kept on the
    neuron, which the Skeleton check and the export both ask for."""
    cached = getattr(neuron, "_lamellae", None)
    if cached is None:
        cached = {}
        with contextlib.suppress(AttributeError):
            neuron._lamellae = cached
    key = (round(float(aspect_min), 6), round(float(step_um), 6))
    if key not in cached:
        cached[key] = _find_lamellae(neuron, aspect_min, step_um)
    return cached[key]


def _find_lamellae(neuron, aspect_min: float, step_um: float) -> LamellaReport:
    nodes, xyz, radius, section = _tables(neuron)
    outlines = _outlines(neuron)
    graph = neuron.graph()

    shape = {}
    for i, ring in outlines.items():
        if i not in xyz or len(ring) < 4:
            continue
        _, _, _, long_, short = _axes(ring)
        if short > 1e-9:
            shape[i] = (long_, short, long_ / short)
    elongated = {i for i, (_, _, a) in shape.items() if a >= aspect_min}

    stack_graph = nx.Graph()
    stack_graph.add_nodes_from(elongated)
    for a, b in graph.edges():
        if a in elongated and b in elongated and abs(section[a] - section[b]) == 1:
            stack_graph.add_edge(a, b)

    # First every stack's chain, so that a link between two stacks can be
    # joined chain to chain.
    stacks = []
    for members in nx.connected_components(stack_graph):
        ids = tuple(sorted(members, key=lambda i: (section[i], i)))
        rings = [outlines[i] for i in ids]
        z = float(np.mean([xyz[i][2] for i in ids]))
        chain, widths, full = medial_chain(rings, z, step_um=step_um)
        if len(chain) < 2:
            continue
        # A process lying flat descends along its chain; a sheet seen on
        # several sections stays at mid-thickness.  So each node sits at the
        # mean Z of the profiles whose outlines contain it (the nearest
        # profile's, where none does).  Flat at the mean Z until 2026-09-25,
        # a chain made every link leaving the top or bottom of a tall stack
        # climb half the stack.
        zs = np.array([xyz[i][2] for i in ids])
        centers = np.array([xyz[i][:2] for i in ids])
        for k, point in enumerate(chain):
            holding = [m for m, i in enumerate(ids) if _inside_polygon(point[:2], outlines[i])]
            chain[k, 2] = float(zs[holding].mean()) if holding else float(
                zs[int(np.argmin(np.linalg.norm(centers - point[:2], axis=1)))])
        stacks.append((ids, rings, chain, widths, full, z))
    member = {i: k for k, stack in enumerate(stacks) for i in stack[0]}

    def nearest(k: int, point) -> int:
        return int(np.argmin(np.linalg.norm(stacks[k][2][:, :2] - point[:2], axis=1)))

    lamellae: list[Lamella] = []
    for k, (ids, rings, chain, widths, full, z) in enumerate(stacks):
        _, _, _, long_, short = _axes(np.vstack(rings))
        replaced = internal = leaving = joins = joins_inside = 0.0
        attachments = []
        joined: set[tuple[int, int, int]] = set()
        branch = False
        for i in ids:
            for other in graph[i]:
                if other not in xyz:
                    continue
                length = float(np.linalg.norm(xyz[other] - xyz[i]))
                if member.get(other) == k:
                    replaced += length / 2          # each internal link is seen from both ends
                    internal += length / 2
                    continue
                inside = max(
                    _inside_fraction(xyz[i][:2], xyz[other][:2], radius[i], outlines.get(j))
                    for j in ids)
                replaced += length * inside
                j = nearest(k, xyz[other])
                attachments.append((int(other), j))
                # The SWC's link: this chain's node to the other annotation, or
                # to the other stack's chain node nearest this end.
                share = 0.5 if other in member else 1.0
                leaving += share * length
                far = (stacks[member[other]][2][nearest(member[other], xyz[i])]
                       if other in member else xyz[other])
                end = (j, member.get(other, -1),
                       nearest(member[other], xyz[i]) if other in member else int(other))
                if end not in joined:                # two members linked to one annotation
                    joined.add(end)                  # near one chain node: one link
                    span = float(np.linalg.norm(chain[j] - far))
                    joins += share * span
                    # Its part inside the stack's outlines; between two
                    # stacks, inside either's, half to each (two stacks can
                    # lie one over the other).
                    inside = max(_inside_fraction(chain[j][:2], far[:2], 0.0, outlines.get(m))
                                 for m in ids)
                    if other in member:
                        inside = min(1.0, inside + max(
                            _inside_fraction(far[:2], chain[j][:2], 0.0, outlines.get(m))
                            for m in stacks[member[other]][0]))
                    joins_inside += share * span * inside
                if radius[other] < radius[i] and not _inside_polygon(xyz[other][:2], outlines[i]):
                    branch = True
        arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(chain, axis=0), axis=1))])
        if attachments:
            ks = [j for _, j in attachments]
            beyond = float(arc[min(ks)] + (arc[-1] - arc[max(ks)]))
        else:
            beyond = float(arc[-1])
        lamellae.append(Lamella(
            location_ids=ids, sections=(section[ids[0]], section[ids[-1]]),
            chain_um=chain, chain_widths_um=np.asarray(widths, dtype=float),
            long_um=long_, short_um=short,
            aspect=long_ / short if short > 1e-9 else float("inf"),
            cable_um=_arc(chain), cable_untrimmed_um=_arc(full),
            long_minus_short_um=long_ - short, replaced_um=replaced,
            attachments=tuple(attachments), beyond_um=beyond, is_branch_point=branch,
            internal_um=internal, leaving_um=leaving, joins_um=joins,
            joins_inside_um=joins_inside,
            extra={"widest_id": int(max(ids, key=lambda i: shape[i][0])), "z_um": z},
        ))
    lamellae.sort(key=lambda lam: -lam.net_change_um)
    return LamellaReport(
        lamellae=lamellae,
        n_elongated=len(elongated),
        n_stacks=sum(1 for lam in lamellae if len(lam.location_ids) > 1),
        cable_um=float(sum(lam.cable_um for lam in lamellae)),
        replaced_um=float(sum(lam.replaced_um for lam in lamellae)),
    )


def chain_graph(neuron, graph=None, report: LamellaReport | None = None):
    """The annotation graph with each lamella stack replaced by its chain.

    Returns ``(graph, chains)``.  Every other annotation keeps its node and its
    links, in Viking's order.  A stack's annotations give way to its chain's
    nodes -- integers from -2 down, linked in chain order -- and each link that
    left the stack now leaves the chain node nearest the annotation at its
    other end (at both ends, where two stacks are linked), as
    ``Lamella.attachments`` counts it.  ``chains`` pairs each
    :class:`Lamella` with its node keys in chain order.  Nothing is dropped
    past the outermost branch: a chain's ends are tips, as Rebecca Pfeiffer
    decided (2026-09-23).
    """
    report = report if report is not None else find_lamellae(neuron)
    graph = (graph if graph is not None else neuron.graph()).copy()
    _, xyz, _, _ = _tables(neuron)
    member = {i: k for k, lam in enumerate(report.lamellae) for i in lam.location_ids}
    # Chain nodes count down from -2: -1 is a root's parent in an SWC and in
    # ``spanning_forest``, and a branch hung from a node -1 became a root.
    keys: list[list[int]] = []
    first = -2
    for lam in report.lamellae:
        keys.append(list(range(first, first - len(lam.chain_um), -1)))
        first -= len(lam.chain_um)

    def nearest(k: int, point) -> int:
        chain = report.lamellae[k].chain_um
        return keys[k][int(np.argmin(np.linalg.norm(chain[:, :2] - point[:2], axis=1)))]

    leaving = [(a, b) for a, b in graph.edges()
               if (a in member or b in member) and member.get(a, -1) != member.get(b, -1)]
    graph.remove_nodes_from(member)
    for chain in keys:
        graph.add_nodes_from(chain)
        graph.add_edges_from(zip(chain[:-1], chain[1:], strict=False))
    for a, b in leaving:
        graph.add_edge(a if a not in member else nearest(member[a], xyz[b]),
                       b if b not in member else nearest(member[b], xyz[a]))
    return graph, list(zip(report.lamellae, keys, strict=True))
