"""How short can a cell's skeleton be and still pass through every annotation?

The exported skeleton joins annotation centers, and a center wanders from one
section to the next: by a hand's placement of a circle, by the noise in a
hand-traced outline (1.7 times a circle's, at matched size, on RPC1 c192), by
section-to-section registration.  Every sideways step is added to the path
length, so the path through the centers over-measures the cable.

The true centerline is not known, but it passes through every cross-section
that was annotated.  So the *shortest* path that still passes through every
annotation is a floor under the cable length that needs no calibration, and
the path through the centers is, in practice, a ceiling: a bracket, not an
estimate.  Between them one parameter, ``f``: each node may move toward its
neighbors but never outside ``f`` of its own annotation -- its circle of
radius ``f * r``, or its traced outline scaled by ``f`` about its centroid.
``f = 0`` is the export as it is, ``f = 1`` the floor.  Unlike a smoothing
spline it cannot leave the cell: a node stays inside the contour that was
traced for it.  Tips and unlinked annotations do not move, so no branch is
shortened from its end.

Proposed to Rebecca Pfeiffer on 2026-09-21, who asked for both offerings: the
two bounds as numbers, and an SWC written at a chosen ``f`` with ``f`` *and the
bound* in its header.  Her hand-traced reference branches are what will set
``f``.

Branch angles, tortuosity and the bearing on which a process leaves a junction
are not corrected by this or by the rim correction: a branch still starts at
its parent's center.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp

from .skeleton_check import _inside_polygon, _outlines, _tables

__all__ = ["BOUNDS", "Relaxed", "length_bracket", "relax_skeleton"]

#: What holds a node: its traced outline where it has one (else its circle),
#: or the stored radius for every annotation -- all an SWC alone allows.
BOUNDS = ("outline", "radius")

#: Bearings from a centroid along which an outline's reach is measured.
_RAYS = 180


@dataclass
class Relaxed:
    f: float
    bound: str
    #: Location ID -> (x, y) microns; Z never changes.
    positions: dict[int, tuple[float, float]]
    length_before_um: float
    length_after_um: float
    n_moved: int
    median_move_um: float
    max_move_um: float
    iterations: int


def _reach(center, outline) -> np.ndarray:
    """How far the outline reaches from ``center`` along each bearing: the
    distance to the FIRST crossing, so what counts is the part of the outline
    that can be seen from its centroid.  All zeros when the centroid lies
    outside its own outline (a crescent): that node stays put.
    """
    if not _inside_polygon(center, outline):
        return np.zeros(_RAYS)
    angles = np.arange(_RAYS) * (2 * np.pi / _RAYS)
    d = np.column_stack([np.cos(angles), np.sin(angles)])          # (R, 2)
    a = outline - center                                           # (E, 2)
    e = np.roll(outline, -1, axis=0) - outline
    den = d[:, :1] * (-e[:, 1]) - d[:, 1:] * (-e[:, 0])            # (R, E)
    ok = np.abs(den) > 1e-15
    den = np.where(ok, den, 1.0)
    t = (a[:, 0] * (-e[:, 1]) - a[:, 1] * (-e[:, 0])) / den
    u = (d[:, :1] * a[:, 1] - d[:, 1:] * a[:, 0]) / den
    t = np.where(ok & (t > 0) & (u >= 0) & (u <= 1), t, np.inf)
    reach = t.min(axis=1)
    return np.where(np.isfinite(reach), reach, 0.0)


def relax_skeleton(neuron, f: float = 1.0, *, bound: str = "outline",
                   settle_um: float = 0.01, max_iterations: int = 6000) -> Relaxed:
    """Shorten the skeleton as far as ``f`` of each annotation allows.

    Minimizes the summed 3-D link length over the nodes' XY positions: each
    node goes to the mean of its neighbors weighted by the inverse of the
    link's length (Weiszfeld's step for a sum of distances, which is what a
    path length is; an unweighted mean minimizes the *squared* lengths and
    stops short), and is put back inside its bound after every step.  It ends
    when 200 further steps shorten the cell by less than ``settle_um``.  Long
    unbranched paths settle slowly: 60 steps leave RPC1 c192 18 um above where
    4,000 put it.

    An outline is held as its reach from the centroid along 180 bearings, the
    smaller of the two beside a node's bearing being its limit, and every
    traced node is checked against its real outline at the end.
    """
    if bound not in BOUNDS:
        raise ValueError(f"bound must be one of {BOUNDS}, not {bound!r}")
    if not 0.0 <= f <= 1.0:
        raise ValueError("f must lie between 0 (the export as it is) and 1 (anywhere inside)")
    cached = getattr(neuron, "_relaxed", None)
    if cached is None:
        cached = {}
        with contextlib.suppress(AttributeError):
            neuron._relaxed = cached
    key = (round(float(f), 6), bound)
    if key in cached:
        return cached[key]

    _, xyz, radius, _ = _tables(neuron)
    graph = neuron.graph()
    ids = [i for i in graph.nodes if i in xyz]
    index = {v: k for k, v in enumerate(ids)}
    n = len(ids)
    start = np.array([xyz[i][:2] for i in ids], dtype=float).reshape(-1, 2)
    z = np.array([xyz[i][2] for i in ids], dtype=float)
    links = np.array([(index[a], index[b]) for a, b in graph.edges() if a in index and b in index],
                     dtype=np.int64).reshape(-1, 2)

    def length(points) -> float:
        if not len(links):
            return 0.0
        d = points[links[:, 0]] - points[links[:, 1]]
        return float(np.sqrt((d ** 2).sum(axis=1) + (z[links[:, 0]] - z[links[:, 1]]) ** 2).sum())

    before = length(start)
    degree = np.bincount(links.ravel(), minlength=n) if len(links) else np.zeros(n, dtype=np.int64)
    free = degree >= 2
    # Every node's limit by bearing; a circle's is its radius all the way round.
    reach = np.repeat(np.array([radius[i] for i in ids], dtype=float).reshape(-1, 1), _RAYS, axis=1)
    outlines = {index[i]: ring for i, ring in _outlines(neuron).items() if i in index} \
        if bound == "outline" else {}
    for k, ring in outlines.items():
        reach[k] = _reach(start[k], ring)
    free &= reach.max(axis=1) > 0

    points = start.copy()
    iterations = 0
    if f > 0 and len(links) and free.any():
        rows = np.concatenate([links[:, 0], links[:, 1]])
        cols = np.concatenate([links[:, 1], links[:, 0]])
        dz2 = np.tile((z[links[:, 0]] - z[links[:, 1]]) ** 2, 2)
        who = np.arange(n)
        mark = length(points)
        for iterations in range(1, max_iterations + 1):   # noqa: B007 - reported
            d = points[rows] - points[cols]
            weight = 1.0 / np.maximum(np.sqrt((d ** 2).sum(axis=1) + dz2), 1e-9)
            w = sp.csr_matrix((weight, (rows, cols)), shape=(n, n))
            target = (w @ points) / np.maximum(np.asarray(w.sum(axis=1)), 1e-18)
            new = np.where(free[:, None], 0.5 * points + 0.5 * target, points)
            offset = new - start
            dist = np.linalg.norm(offset, axis=1)
            bearing = np.arctan2(offset[:, 1], offset[:, 0]) % (2 * np.pi)
            ray = (bearing * (_RAYS / (2 * np.pi))).astype(np.int64) % _RAYS
            limit = f * np.minimum(reach[who, ray], reach[who, (ray + 1) % _RAYS])
            over = dist > limit
            new[over] = start[over] + offset[over] * (limit[over] / dist[over])[:, None]
            points = new
            if iterations % 200 == 0:
                now = length(points)
                if mark - now < settle_um:
                    break
                mark = now
        # The bearings are a sampling; the outline itself has the last word.
        for k, ring in outlines.items():
            offset = points[k] - start[k]
            if free[k] and offset.any() and not _inside_polygon(start[k] + offset / f, ring):
                lo, hi = 0.0, 1.0
                for _ in range(20):
                    mid = 0.5 * (lo + hi)
                    if _inside_polygon(start[k] + offset * (mid / f), ring):
                        lo = mid
                    else:
                        hi = mid
                points[k] = start[k] + offset * lo

    move = np.linalg.norm(points - start, axis=1)
    shifted = move > 1e-9
    result = Relaxed(
        f=float(f), bound=bound,
        positions={ids[k]: (float(points[k, 0]), float(points[k, 1])) for k in range(n)},
        length_before_um=before, length_after_um=length(points),
        n_moved=int(shifted.sum()),
        median_move_um=float(np.median(move[shifted])) if shifted.any() else 0.0,
        max_move_um=float(move.max()) if len(move) else 0.0,
        iterations=iterations,
    )
    cached[key] = result
    return result


def length_bracket(neuron) -> dict[str, float]:
    """The cable length's ceiling and floors, microns.

    ``centers``: the path through the annotation centers, as exported.
    ``floor``: the shortest path through every annotation, a node held inside
    its traced outline (or its circle).  ``floor_by_radius``: the same with the
    stored radius as every node's bound.
    """
    outline = relax_skeleton(neuron, 1.0, bound="outline")
    by_radius = relax_skeleton(neuron, 1.0, bound="radius")
    return {
        "centers": outline.length_before_um,
        "floor": outline.length_after_um,
        "floor_by_radius": by_radius.length_after_um,
    }
