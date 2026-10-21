"""Annotations Viking's section correction pulled toward a much wider linked annotation.

There is no MATLAB counterpart.  Viking's ``CorrectStructures`` *Curve fit*
(``MorphologyGraph.CurveFitProcessChain``, the ``curvefit`` and ``all``
correction modes) fits a curve through each process using its linked
annotations as control points.  Service 1.2.0 takes a soma's, or any large
outline's, centroid as one of them, so a thin process leaving a soma is moved
about halfway to the soma's center ([Viking-Server] confirmed it in Slack #ai on
2026-10-20; their fix, leaving out a neighbor more than 4x the node's XY
extent, was not deployed yet).  On InferiorMonkey c1178 under Curve fit,
Location 1328721 (r 0.74 um) moved 10.6 um toward 1557441 (r 9.9 um), 0.495 of
the way.

The check, per annotation *i* of the cell (its own, one row per ID), with *d*
its correction move (registered to corrected, :func:`~sbfsem.analysis.shifts.stage_frame`)
and *j* the widest annotation linked to it (``neuron.edges``, both directions,
the cell's own only):

* *j* is at least ``ratio`` times as wide (``Rum``; :data:`DEFAULT_RATIO` 4);
* *d* points at *j*: the cosine between *d* and *j* - *i*, at the registered
  positions, is over :data:`MIN_COS`;
* *d* is long for the annotation: over ``max(2 r_i, 1 um)``;
* *d* does not carry it past *j*: ``frac``, how far along the line to *j* the
  move took it (the projection of *d* on that line over the line's length), is
  at most :data:`MAX_FRAC` (the user's choice, 2026-10-20).

The fit puts an annotation with two links on the line between its neighbors'
centroids, as far along it as its section is between theirs, so ``frac`` is
about ``t = (z_i - z_k) / (z_j - z_k)``, *k* the thin neighbor: 0.5 with one
neighbor a section either side, 1/3, 2/3 or 3/4 when they are not.  Measured
on production on 2026-10-20 (19 cells, 64 rows at 4x): 45 such rows in six
cells sat within 0.08 um of that point (0.1 to 0.4 um on RC2); RC1 c410's
1567770 went 0.31 of the way to a soma two sections away, t 1/3.  The five rows
over 1 were not the pull: three tips (one link, r 0.02 to 0.07 um) beside a
thin neighbor under 0.7 um away, moved just over 1 um; a tip that followed its
pulled neighbor (RPC1 c430's 197239); and a point of r 0.001 um inside an
outline, placed 3/4 of the way to its other, thin neighbor (RPC2 c787's 142616).
A flagged annotation's chain neighbors that moved the same way by about the
same amount are listed with it (``moved_with``): a thin tip whose own
neighbor is thin follows the pulled one, so the one-hop rule misses it
(c1178's 1328723 moved 10.9 um to the same place as 1328721).

:func:`scan` keeps every annotation that passes the direction, length and
``frac`` tests next to a wider one (ratio at least :data:`MIN_RATIO`); :meth:`PullScan.at`
filters them by a ratio, so a page can change it without the cell being
scanned again.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .shifts import MOVED_UM, moves_apply, stage_frame

__all__ = [
    "DEFAULT_RATIO",
    "FIT_MODES",
    "MAX_FRAC",
    "MAX_RATIO",
    "MIN_COS",
    "MIN_MOVE_UM",
    "MIN_RATIO",
    "MOVE_PER_RADIUS",
    "Pull",
    "PullScan",
    "SAME_MOVE",
    "scan",
    "scan_neuron",
]

#: The correction modes that run Viking's curve fit, where the pull happens.
FIT_MODES = ("curvefit", "all")
#: How many times wider the neighbor must be, unless asked otherwise: the
#: extent ratio [Viking-Server]'s fix leaves out (the user's choice, 2026-10-20).
DEFAULT_RATIO = 4.0
#: The smallest ratio a scan keeps, and so the smallest that can be asked for.
MIN_RATIO = 1.0
#: The largest ratio that can be asked for (a soma is rarely 50x a process).
MAX_RATIO = 50.0
#: The move must point at the wider neighbor: cos(d, j - i) over this.
MIN_COS = 0.8
#: ... and be longer than ``max(MOVE_PER_RADIUS * r_i, MIN_MOVE_UM)``.
MOVE_PER_RADIUS = 2.0
MIN_MOVE_UM = 1.0
#: ... and not carry it past the wider neighbor: ``frac`` at most this.
MAX_FRAC = 1.0
#: A chain neighbor moved with a flagged annotation when its move differs from
#: the flagged one's by at most this fraction of the flagged one's length.
SAME_MOVE = 0.25
#: A walk along the chain for ``moved_with`` stops after this many annotations.
MAX_MOVED_WITH = 64

#: The node columns read here.
_COLUMNS = ("ID", "ParentID", "Z", "Rum", "Xum", "Yum", "LiveDXnm", "LiveDYnm",
            "CorrDXnm", "CorrDYnm", "ManDXnm", "ManDYnm")


@dataclass(frozen=True)
class Pull:
    """One annotation the correction moved toward a wider linked one (um)."""

    location_id: int
    z: int
    r_um: float
    dx_um: float
    dy_um: float
    move_um: float
    neighbor_id: int
    neighbor_r_um: float
    #: ``neighbor_r_um / r_um`` (``inf`` for a zero-width annotation).
    ratio: float
    #: From this annotation to the neighbor, at the registered positions.
    distance_um: float
    #: The move's projection on the line to the neighbor, over ``distance_um``
    #: (at most :data:`MAX_FRAC`).
    frac: float
    #: The cosine between the move and the line to the neighbor.
    cos: float
    #: Chain neighbors that moved the same way by about as much (Location IDs).
    moved_with: tuple[int, ...] = ()

    def as_dict(self) -> dict:
        return {
            "location_id": self.location_id, "z": self.z, "r_um": self.r_um,
            "dx_um": self.dx_um, "dy_um": self.dy_um, "move_um": self.move_um,
            "neighbor_id": self.neighbor_id, "neighbor_r_um": self.neighbor_r_um,
            "ratio": self.ratio if np.isfinite(self.ratio) else None,
            "distance_um": self.distance_um, "frac": self.frac, "cos": self.cos,
            "moved_with": list(self.moved_with),
        }


@dataclass
class PullScan:
    """:func:`scan`'s answer for one cell."""

    #: Every annotation that passes the direction, length and ``frac`` tests
    #: beside a neighbor at least :data:`MIN_RATIO` times as wide, longest move first.
    candidates: list[Pull] = field(default_factory=list)
    #: The ratio to the widest linked neighbor of every annotation that moved
    #: (over :data:`~sbfsem.analysis.shifts.MOVED_UM`) and has one at least as
    #: wide, ascending: how many sit next to a wider one at any ratio.
    wider_ratios: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: The cell's own annotations, and how many of them the correction moved.
    n_annotations: int = 0
    n_moved: int = 0

    def at(self, ratio: float = DEFAULT_RATIO) -> list[Pull]:
        """The pulls toward a neighbor at least ``ratio`` times as wide."""
        return [p for p in self.candidates if p.ratio >= ratio]

    def n_next_to_wider(self, ratio: float = DEFAULT_RATIO) -> int:
        """How many moved annotations have a linked one at least ``ratio`` times as wide."""
        return int(len(self.wider_ratios) - np.searchsorted(self.wider_ratios, ratio, side="left"))


def _neighbor_pairs(ids: np.ndarray, edges_a: np.ndarray, edges_b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(src, dst)`` rows of every link between two of ``ids``, both directions, no self-links."""
    index = pd.Index(ids)
    row_a, row_b = index.get_indexer(edges_a), index.get_indexer(edges_b)
    keep = (row_a >= 0) & (row_b >= 0) & (row_a != row_b)
    row_a, row_b = row_a[keep], row_b[keep]
    return np.concatenate([row_a, row_b]), np.concatenate([row_b, row_a])


def scan(frame: pd.DataFrame, edges_a, edges_b) -> PullScan:
    """The pulls in one cell's annotations (see the module's docstring).

    ``frame`` has one row per annotation: ``ID``, ``Z``, ``Rum`` and the
    columns of :func:`~sbfsem.analysis.shifts.stage_frame` (``corr_dx_um``,
    ``corr_dy_um``, ``registered_x_um``, ``registered_y_um``).  ``edges_a`` and
    ``edges_b`` are the cell's links as Location IDs; a link to an ID not in
    ``frame`` is ignored.
    """
    ids = frame["ID"].to_numpy(dtype=np.int64)
    n = len(ids)
    r = np.nan_to_num(frame["Rum"].to_numpy(dtype=float), nan=0.0)
    px, py = frame["registered_x_um"].to_numpy(dtype=float), frame["registered_y_um"].to_numpy(dtype=float)
    dx, dy = frame["corr_dx_um"].to_numpy(dtype=float), frame["corr_dy_um"].to_numpy(dtype=float)
    z = frame["Z"].to_numpy()
    move = np.hypot(dx, dy)
    moved = move > MOVED_UM
    src, dst = _neighbor_pairs(ids, np.asarray(edges_a, dtype=np.int64), np.asarray(edges_b, dtype=np.int64))
    out = PullScan(n_annotations=n, n_moved=int(moved.sum()))
    if not len(src):
        return out

    # The widest neighbor of each annotation: sorted by (src, width, ID), the last of each src.
    order = np.lexsort((ids[dst], r[dst], src))
    src_sorted, dst_sorted = src[order], dst[order]
    last = np.flatnonzero(np.r_[src_sorted[1:] != src_sorted[:-1], True])
    who, widest = src_sorted[last], dst_sorted[last]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(r[who] > 0, r[widest] / np.where(r[who] > 0, r[who], 1.0),
                         np.where(r[widest] > 0, np.inf, 0.0))
    wider = moved[who] & (ratio >= MIN_RATIO)
    out.wider_ratios = np.sort(ratio[wider])

    vx, vy = px[widest] - px[who], py[widest] - py[who]
    distance = np.hypot(vx, vy)
    with np.errstate(divide="ignore", invalid="ignore"):
        toward = np.where(distance > 0, (dx[who] * vx + dy[who] * vy) / distance, 0.0)
        cos = np.where(move[who] > 0, toward / move[who], 0.0)
        frac = np.where(distance > 0, toward / distance, 0.0)
    long_move = move[who] > np.maximum(MOVE_PER_RADIUS * r[who], MIN_MOVE_UM)
    keep = np.flatnonzero(wider & (cos > MIN_COS) & long_move & (distance > 0) & (frac <= MAX_FRAC))
    if not len(keep):
        return out

    # Adjacency for the walk along the chain.
    by_src = np.argsort(src, kind="stable")
    starts = np.searchsorted(src[by_src], np.arange(n + 1))
    neighbors = dst[by_src]

    def moved_with(i: int, j: int) -> tuple[int, ...]:
        limit = SAME_MOVE * move[i]
        seen = {i, j}
        found: list[int] = []
        queue = deque([i])
        while queue and len(found) < MAX_MOVED_WITH:
            k = queue.popleft()
            for m in neighbors[starts[k]:starts[k + 1]]:
                m = int(m)
                if m in seen:
                    continue
                seen.add(m)
                if np.hypot(dx[m] - dx[i], dy[m] - dy[i]) <= limit:
                    found.append(m)
                    queue.append(m)
        return tuple(sorted(int(ids[m]) for m in found))

    pulls = []
    for k in keep:
        i, j = int(who[k]), int(widest[k])
        pulls.append(Pull(
            location_id=int(ids[i]), z=int(z[i]), r_um=float(r[i]),
            dx_um=float(dx[i]), dy_um=float(dy[i]), move_um=float(move[i]),
            neighbor_id=int(ids[j]), neighbor_r_um=float(r[j]), ratio=float(ratio[k]),
            distance_um=float(distance[k]), frac=float(frac[k]), cos=float(cos[k]),
            moved_with=moved_with(i, j)))
    pulls.sort(key=lambda p: (-p.move_um, p.location_id))
    out.candidates = pulls
    return out


def scan_neuron(neuron) -> PullScan:
    """:func:`scan` over a loaded cell's own annotations.

    Empty for a cell whose move columns do not apply
    (:func:`~sbfsem.analysis.shifts.moves_apply`) or that was not corrected.
    """
    nodes = neuron.nodes
    if not moves_apply(neuron) or "CorrDXnm" not in nodes.columns:
        return PullScan()
    columns = [c for c in _COLUMNS if c in nodes.columns]
    own = nodes.loc[nodes["ParentID"] == neuron.id, columns].drop_duplicates("ID")
    frame = stage_frame(own)
    frame["Rum"] = own["Rum"].to_numpy(dtype=float) if "Rum" in own.columns else 0.0
    edges = neuron.edges
    if edges is None or not len(edges):
        return scan(frame, np.zeros(0, np.int64), np.zeros(0, np.int64))
    return scan(frame, edges["A"].to_numpy(), edges["B"].to_numpy())
