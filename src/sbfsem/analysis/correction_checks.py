"""Annotations Viking's section correction pulled toward a much wider linked annotation,
and traced outlines it moved off the outlines linked to them.

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

Viking's service no longer runs the fit by default: 1.5.0 removed it on
2026-10-27, after an 11-cell measurement found the field alone as good, and
this viewer has asked for the residual field alone since (2.42.0).  A cell is
checked only when its correction ran the fit, now one read from a file saved
before then; every other is listed with :data:`NO_CURVE_FIT`.

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

The fit takes a traced outline's centroid as a point of the process too, so
where an outline takes in a lobe or the base of a branch its centroid steps
sideways though its membrane does not, and the next outline is moved toward
that step.  InferiorMonkey c16137's 1337465 (section 547, r 4.73 um) lies inside
546's outline, which takes in the base of a process: 546's centroid sits
1.16 um away, and the fit put 547 at the midpoint of its neighbors' centroids,
0.58 um toward the lobe (radius ratio 1.1: no ratio catches it).
:func:`scan_outlines` lists such outlines by what the move did to their overlap
with the outlines linked to them, where Viking draws them
(:func:`outline_overlaps`); the thresholds were chosen on 11 cells on
2026-10-22 (``docs/context/correction-outlines-2026-10-22.md``): from 0.25 um
three in four of the fit's outline moves lower that overlap, below 0.1 um most
raise it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import shapely

from .shifts import MOVED_UM, moves_apply, stage_frame

__all__ = [
    "DEFAULT_RATIO",
    "FIT_MODES",
    "MAX_FRAC",
    "NO_CURVE_FIT",
    "MAX_RATIO",
    "MIN_COS",
    "MIN_MOVE_UM",
    "MIN_RATIO",
    "MOVE_PER_RADIUS",
    "OUTLINE_CAUSES",
    "OUTLINE_MIN_OFF",
    "OUTLINE_MIN_MOVE_UM",
    "OUTLINE_MIN_OVERLAP",
    "OutlineMove",
    "Pull",
    "PullScan",
    "SAME_MOVE",
    "circle_loss",
    "outline_overlaps",
    "scan",
    "scan_neuron",
    "scan_outlines",
]

#: The correction modes that ran Viking's curve fit, where the pull happens:
#: since 2026-10-27 only a cell read from a file saved before then carries one.
FIT_MODES = ("curvefit", "all")
#: Why a cell corrected now is not checked: the correction log's note.
NO_CURVE_FIT = ("Not checked: corrected by the residual field alone; Viking's service "
                "no longer runs a curve fit (removed as a default on 2026-10-27)")
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

#: A traced outline is checked when the correction moved it at least this far (um) ...
OUTLINE_MIN_MOVE_UM = 0.25
#: ... where Viking draws it, it overlaps the outlines linked to it at least this
#: much (:func:`outline_overlaps`: shared area over the smaller one's) ...
OUTLINE_MIN_OVERLAP = 0.8
#: ... and the move took it off them: the overlap it lost is at least this
#: share of what the same move takes from a circle of its area lying on a copy
#: of itself (``off_share``).
OUTLINE_MIN_OFF = 0.2
#: Why a listed outline moved (:attr:`OutlineMove.cause`).
OUTLINE_CAUSES = ("shape", "slide", "other")

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


@dataclass(frozen=True)
class OutlineMove:
    """One traced outline the correction moved off the outlines linked to it (um).

    Overlaps are :func:`outline_overlaps`': shared area over the smaller
    outline's, the linked ones where Viking draws them.
    """

    location_id: int
    z: int
    r_um: float
    dx_um: float
    dy_um: float
    move_um: float
    #: Outlines linked to it on other sections.
    n_linked: int
    #: Their overlap with it where Viking draws it, and with it moved by the correction.
    overlap_traced: float
    overlap_moved: float
    #: The overlap the move took away, as a share of what the same move takes
    #: from a circle of the outline's area lying on a copy of itself: 1 is
    #: straight off them, 0 neither off nor onto them, below 0 onto them.
    off_share: float
    #: The linked outline the move points at most, and the cosine between the
    #: move and the step from this outline's centroid to that one's.
    neighbor_id: int
    neighbor_z: int
    neighbor_r_um: float
    cos: float
    #: The step between the two centroids, where Viking draws them.
    step_um: float
    #: The two outlines' overlap, as drawn and with this one slid by the step.
    overlap_neighbor: float
    overlap_slid: float
    #: ``shape``: the move goes toward the neighbor's centroid (``cos`` over
    #: :data:`MIN_COS`) and not past it (``step_um`` at least ``move_um``), and
    #: sliding onto that centroid does not raise their overlap, so the step
    #: between the centroids is the outline's change of shape; ``slide``: the
    #: same, but the slide would raise it (the neighbor is this outline
    #: displaced); ``other``: no linked outline's centroid lies that way and that
    #: far, so the fit carried it along with other annotations of the process.
    cause: str

    def as_dict(self) -> dict:
        return {
            "location_id": self.location_id, "z": self.z, "r_um": self.r_um,
            "dx_um": self.dx_um, "dy_um": self.dy_um, "move_um": self.move_um,
            "n_linked": self.n_linked, "overlap_traced": self.overlap_traced,
            "overlap_moved": self.overlap_moved, "off_share": self.off_share,
            "neighbor_id": self.neighbor_id,
            "neighbor_z": self.neighbor_z, "neighbor_r_um": self.neighbor_r_um, "cos": self.cos,
            "step_um": self.step_um, "overlap_neighbor": self.overlap_neighbor,
            "overlap_slid": self.overlap_slid, "cause": self.cause,
        }


@dataclass
class PullScan:
    """:func:`scan`'s answer for one cell, and :func:`scan_outlines`'."""

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
    #: Traced outlines the correction moved off the outlines linked to them
    #: (:func:`scan_outlines`), longest move first.
    outline_moves: list[OutlineMove] = field(default_factory=list)
    #: The cell's traced outlines linked to one on another section, and how
    #: many of them the correction moved :data:`OUTLINE_MIN_MOVE_UM` or more.
    n_outlines: int = 0
    n_outlines_moved: int = 0

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


# -- traced outlines the fit moved off their linked outlines ---------------------------
def _polygon(rings: Sequence) -> shapely.Geometry | None:
    """One annotation's outline and cut-outs as a valid area, or ``None`` without one."""
    parts = [np.asarray(r, dtype=float).reshape(-1, 2) for r in rings]
    parts = [r[np.isfinite(r).all(axis=1)] for r in parts]
    if not parts or len(parts[0]) < 3:
        return None
    polygon = shapely.Polygon(parts[0], [r for r in parts[1:] if len(r) >= 3])
    if not polygon.is_valid:
        polygon = shapely.make_valid(polygon)
    return polygon if polygon.area > 0 else None


_OVERLAP_COLUMNS = ("ID", "Z", "r_um", "dx_um", "dy_um", "move_um", "n_linked", "overlap_traced",
                    "overlap_moved", "off_share", "neighbor_id", "neighbor_z", "neighbor_r_um", "cos",
                    "step_um", "overlap_neighbor", "overlap_slid")


def circle_loss(move_um, r_um):
    """The overlap a circle of radius ``r_um`` loses when slid ``move_um`` off a
    copy of itself: 0 for no move, 1 from two radii on."""
    u = np.clip(np.asarray(move_um, dtype=float) / (2.0 * np.maximum(r_um, 1e-12)), 0.0, 1.0)
    return 1.0 - (2.0 / np.pi) * (np.arccos(u) - u * np.sqrt(1.0 - u * u))


def outline_overlaps(frame: pd.DataFrame, rings: Mapping[int, Sequence], edges_a, edges_b, *,
                     min_move_um: float = OUTLINE_MIN_MOVE_UM) -> tuple[pd.DataFrame, int]:
    """How the correction's move of each traced outline changed its overlap with
    the outlines linked to it.

    ``frame`` is :func:`scan`'s; ``rings`` maps a Location ID to its outline and
    cut-outs where Viking draws it (the registered stage), each an ``(n, 2)``
    array in um.  The *overlap* of two outlines is their shared area over the
    smaller one's (1 when the smaller lies inside the larger, as a section of a
    soma inside the next, wider one); over several linked outlines the shared
    areas and the smaller areas are each summed.

    One row per outline moved at least ``min_move_um`` with a linked outline on
    another section (the columns of :class:`OutlineMove`, ``ID`` and ``Z`` for
    ``location_id`` and ``z``, without ``cause``), and the number of outlines
    with such a link, moved or not.  ``off_share`` scales the overlap lost by
    :func:`circle_loss` of the move at the radius of a circle of the outline's
    area, so a soma's and a thin process's moves read alike.
    """
    ids = frame["ID"].to_numpy(dtype=np.int64)
    row_of = {int(i): k for k, i in enumerate(ids) if int(i) in rings}
    # Made only for the outlines tested and their linked ones: a cell of thousands
    # of outlines moves few of them far (RPC1 c2628: 650 of 3,978 by 0.25 um).
    polygons: dict[int, shapely.Geometry | None] = {}

    def polygon(row: int) -> shapely.Geometry | None:
        key = int(ids[row])
        if key not in polygons:
            polygons[key] = _polygon(rings[key])
        return polygons[key]

    z = frame["Z"].to_numpy()
    linked: dict[int, set[int]] = {}
    for a, b in zip(np.asarray(edges_a, dtype=np.int64).tolist(), np.asarray(edges_b, dtype=np.int64).tolist(),
                    strict=True):
        ra, rb = row_of.get(a), row_of.get(b)
        if ra is None or rb is None or z[ra] == z[rb]:
            continue
        linked.setdefault(ra, set()).add(rb)
        linked.setdefault(rb, set()).add(ra)
    empty = pd.DataFrame({c: [] for c in _OVERLAP_COLUMNS})
    if not linked:
        return empty, 0
    r = np.nan_to_num(frame["Rum"].to_numpy(dtype=float), nan=0.0)
    px, py = frame["registered_x_um"].to_numpy(dtype=float), frame["registered_y_um"].to_numpy(dtype=float)
    dx, dy = frame["corr_dx_um"].to_numpy(dtype=float), frame["corr_dy_um"].to_numpy(dtype=float)
    move = np.hypot(dx, dy)
    out = []
    for i in sorted(linked):
        if move[i] < min_move_um or move[i] <= MOVED_UM:
            continue
        mine = polygon(i)
        others = [j for j in sorted(linked[i]) if polygon(j) is not None]
        if mine is None or not others:
            continue
        theirs = np.array([polygon(j) for j in others], dtype=object)
        smaller = np.minimum(mine.area, shapely.area(theirs))
        shared = shapely.area(shapely.intersection(mine, theirs))
        moved = shapely.affinity.translate(mine, dx[i], dy[i])
        shared_moved = shapely.area(shapely.intersection(moved, theirs))
        step = np.column_stack([px[others] - px[i], py[others] - py[i]])
        length = np.hypot(step[:, 0], step[:, 1])
        with np.errstate(divide="ignore", invalid="ignore"):
            cos = np.where(length > 0, (step[:, 0] * dx[i] + step[:, 1] * dy[i]) / (length * move[i]), 0.0)
        k = int(np.argmax(cos))
        j = others[k]
        slid = shapely.affinity.translate(mine, step[k, 0], step[k, 1])
        traced = float(shared.sum() / smaller.sum())
        after = float(shared_moved.sum() / smaller.sum())
        off = (traced - after) / float(circle_loss(move[i], np.sqrt(mine.area / np.pi)))
        out.append((int(ids[i]), int(z[i]), float(r[i]), float(dx[i]), float(dy[i]), float(move[i]),
                    len(others), traced, after, off, int(ids[j]), int(z[j]), float(r[j]), float(cos[k]),
                    float(length[k]), float(shared[k] / smaller[k]),
                    float(shapely.area(shapely.intersection(slid, theirs[k])) / smaller[k])))
    if not out:
        return empty, len(linked)
    return pd.DataFrame(out, columns=list(_OVERLAP_COLUMNS)), len(linked)


def _cause(row) -> str:
    if row.cos <= MIN_COS or row.step_um < row.move_um:
        return "other"
    return "shape" if row.overlap_slid <= row.overlap_neighbor else "slide"


def scan_outlines(frame: pd.DataFrame, rings: Mapping[int, Sequence], edges_a, edges_b) -> PullScan:
    """The traced outlines the correction moved off the outlines linked to them.

    Viking's curve fit takes an outline's centroid as a point of the process,
    so where a section's outline takes in the base of a branch (a lobe) its
    centroid steps sideways although its membrane does not, and the fit moves
    the outline on the next section toward that step.  An outline is listed
    when the correction moved it :data:`OUTLINE_MIN_MOVE_UM` or more, where
    Viking draws it its linked outlines overlap it at least
    :data:`OUTLINE_MIN_OVERLAP` (:func:`outline_overlaps`), and the move took
    it off them (``off_share`` :data:`OUTLINE_MIN_OFF` or more): the images say
    it was in place.  A fix of a misregistered section raises the overlap.

    Returns a :class:`PullScan` with only the outline fields set.
    """
    table, n_outlines = outline_overlaps(frame, rings, edges_a, edges_b)
    out = PullScan(n_outlines=n_outlines, n_outlines_moved=len(table))
    keep = table[(table["overlap_traced"] >= OUTLINE_MIN_OVERLAP) & (table["off_share"] >= OUTLINE_MIN_OFF)]
    moves = [OutlineMove(location_id=int(row.ID), z=int(row.Z), r_um=row.r_um, dx_um=row.dx_um,
                         dy_um=row.dy_um, move_um=row.move_um, n_linked=int(row.n_linked),
                         overlap_traced=row.overlap_traced, overlap_moved=row.overlap_moved,
                         off_share=row.off_share, neighbor_id=int(row.neighbor_id), neighbor_z=int(row.neighbor_z),
                         neighbor_r_um=row.neighbor_r_um, cos=row.cos, step_um=row.step_um,
                         overlap_neighbor=row.overlap_neighbor, overlap_slid=row.overlap_slid,
                         cause=_cause(row))
             for row in keep.itertuples(index=False)]
    moves.sort(key=lambda m: (-m.move_um, m.location_id))
    out.outline_moves = moves
    return out


def registered_rings(neuron, own: pd.DataFrame, frame: pd.DataFrame) -> dict[int, list[np.ndarray]]:
    """Each of the cell's traced outlines where Viking draws it (the registered stage), um.

    ``own`` is the cell's own node rows (one per ID), ``frame`` their
    :func:`~sbfsem.analysis.shifts.stage_frame`.  The outlines were moved with
    their annotations (``sbfsem.io.corrections.apply_offsets``,
    ``sbfsem.core.moves``), so each is moved back by its annotation's
    ``Xum`` - ``registered_x_um``.
    """
    geometries = getattr(neuron, "geometries", None)
    if geometries is None or not len(geometries):
        return {}
    back = pd.DataFrame({
        "dx": own["Xum"].to_numpy(dtype=float) - frame["registered_x_um"].to_numpy(dtype=float),
        "dy": own["Yum"].to_numpy(dtype=float) - frame["registered_y_um"].to_numpy(dtype=float),
    }, index=own["ID"].to_numpy(dtype=np.int64))
    out: dict[int, list[np.ndarray]] = {}
    for row in geometries.itertuples():
        key = int(row.ID)
        if key not in back.index or not row.Curve:
            continue
        shift = back.loc[key].to_numpy(dtype=float)
        out[key] = [np.asarray(c, dtype=float).reshape(-1, 2) - shift for c in row.Curve]
    return out


def scan_neuron(neuron) -> PullScan:
    """:func:`scan` and :func:`scan_outlines` over a loaded cell's own annotations.

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
        a = b = np.zeros(0, np.int64)
    else:
        a, b = edges["A"].to_numpy(), edges["B"].to_numpy()
    found = scan(frame, a, b)
    outlines = scan_outlines(frame, registered_rings(neuron, own, frame), a, b)
    found.outline_moves = outlines.outline_moves
    found.n_outlines, found.n_outlines_moved = outlines.n_outlines, outlines.n_outlines_moved
    return found
