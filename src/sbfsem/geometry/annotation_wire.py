"""A wireframe drawn straight from the annotations, averaged along Z at the cell's own scale.

Every annotation is a ring in its own section plane -- a disc its circle at
the radius the annotator set, a closed curve its traced outline -- and the
rings are joined along Viking's links into a lattice: rings across, lines
along.  Nothing is meshed, voxelized or contoured, so what is drawn is the
annotations themselves, only averaged.

The averaging is the point.  An annotator's rim wobbles from section to
section, and how much of that wobble is shape depends on scale: a 9 um soma
swells and narrows over microns of Z, so a change over one or two sections
is tracing noise, while a 0.2 um dendrite really does change within a few
sections.  So each ring is replaced by a Gaussian-weighted fit over the
rings near it along its chain, and the window's width (its sigma) is
``gain`` times the ring's own radius.  At the default 0.5, a 9 um soma ring
averages over about 4.5 um of Z (50 sections at 90 nm).  A 0.2 um dendrite
ring averages over 0.1 um: a neighbor one 90 nm section away weighs 0.67,
two sections away 0.2.

The fit is a local quadratic by default (``fit="quadratic"``, the
Savitzky-Golay idea with Gaussian weights), not a mean.  A mean flattens
anything curved within its window: on c1178's soma (170 rings, 15 um tall)
it held the equator at 10 um where the annotations reach 11 to 12, and
shrank 5 % of the rings by more than 16 % (and grew 5 % by more than 14 %).
The quadratic follows the dome, keeps 90 % of the rings within 8 % of their
annotated size, and still takes the section-to-section wobble in ring size
from 0.069 to 0.004 um (median second difference).  ``fit="mean"`` is kept
for comparison.

Rules that keep the averaging honest (CLAUDE.md ground rule 2: an
annotation is a disc in its section plane):

* Rings stay in their section planes; only their outlines move.
* Distance along a chain is the Z step between sections.  Sideways movement
  counts only where it is larger than the rings themselves
  (``lat**2 / (lat + r)``).  A soma's center wanders about 0.22 um per 90 nm
  section on Inferior Monkey c1178, 2.4 times the Z step: that is tracing,
  not travel, and counting it would cut the soma's window by that much.  A
  dendrite running in-plane does travel.
* The window narrows to fit inside its chain (sigma at most half the distance
  to the nearer end), so the first and last ring of a chain -- a soma's top
  and bottom, a dendrite's tip -- stay exactly as annotated, and one-sided
  neighbors never pull a cap outward.
* Rings of different size barely mix (``size_tolerance``), and a link across
  a radius jump larger than ``break_ratio`` joins no chain at all, so a soma
  ring never averages in the dendrite that leaves it.

A chain is a run of annotations, each linked to the next.  Chains are built
greedily from the most alike links first: similar radius, centers close for
that radius.  Each annotation takes at most one link up in Z and one down
(same-section links count as neither), and no loops.  The links left over --
branches, loops, links across a radius jump -- are drawn as ties from the
smaller ring onto the larger annotation, where the smaller ring meets it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = [
    "DEFAULT_BREAK_RATIO",
    "DEFAULT_GAIN",
    "DEFAULT_LINES",
    "DEFAULT_SIZE_TOLERANCE",
    "FITS",
    "AnnotationWire",
    "WireRing",
    "annotation_wire",
    "annotation_wire_from_neuron",
]

#: Window sigma as a multiple of the ring's radius.
DEFAULT_GAIN = 0.5
#: Radius ratio at which a neighbor's weight falls to exp(-1/2) (0.61); at
#: twice that ratio's logarithm it is 0.14.  1 or below turns it off.
DEFAULT_SIZE_TOLERANCE = 1.5
#: A link between rings more than this far apart in radius is a tie, never a
#: chain step.  On c1178 the ratio across a link is 1.06 at the median, 1.68
#: at the 95th percentile and 2.88 at the 99th; 2.5 leaves 30 of its 2,428
#: links out, and a soma-to-dendrite step (about 4 and up) always.
DEFAULT_BREAK_RATIO = 2.5
#: What a ring is fitted with over its window: the polynomial degree.
FITS = {"quadratic": 2, "mean": 0}
#: Lines drawn along each chain (and ties per branch).
DEFAULT_LINES = 12
#: Target outline spacing when resampling, microns; per chain, from its
#: longest perimeter, between the two limits.
RING_ARC_UM = 0.3
MIN_RING_POINTS = 8
MAX_RING_POINTS = 128

CLOSED_CURVE = 6


@dataclass
class WireRing:
    """One annotation as a ring in its section plane."""

    location_id: int
    z_um: float
    section: int
    #: ``"disc"`` or ``"curve"``.
    kind: str
    #: The annotated radius; a closed curve's equivalent radius, sqrt(A/pi).
    radius_um: float
    #: The annotation's outline, resampled to its chain's point count and
    #: rotated to line up with the ring before it, counterclockwise, (N, 2).
    raw: np.ndarray
    #: The averaged outline, point for point with ``raw``.
    xy: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    #: The window sigma this ring was averaged with, microns (0: as annotated).
    sigma_um: float = 0.0

    @property
    def n_points(self) -> int:
        return int(len(self.raw))

    @property
    def moved_um(self) -> float:
        """Mean distance the outline moved in averaging."""
        if not len(self.raw):
            return 0.0
        return float(np.hypot(*(self.xy - self.raw).T).mean())


@dataclass
class AnnotationWire:
    """The rings, the chains they are strung on, and the ties between chains."""

    rings: list[WireRing]
    #: Ring indices in order along each chain.
    chains: list[np.ndarray]
    #: Distance along each chain at each of its rings, microns.
    positions: list[np.ndarray]
    #: ``(smaller, larger)`` ring indices of every link that is not a chain step.
    ties: list[tuple[int, int]]

    def ring_polylines(self, spacing_um: float = 0.0) -> list[tuple[float, np.ndarray]]:
        """``(z, xy)`` of the rings to draw, each closed implicitly.

        ``spacing_um`` thins a chain's rings to at least that distance apart
        along it (its first and last always drawn); 0 draws every annotation.
        """
        out: list[tuple[float, np.ndarray]] = []
        for chain, s in zip(self.chains, self.positions, strict=True):
            keep = _spaced(s, spacing_um)
            out.extend((self.rings[i].z_um, self.rings[i].xy) for i in chain[keep])
        return out

    def line_polylines(self, lines: int = DEFAULT_LINES) -> list[np.ndarray]:
        """``(M, 3)`` polylines along every chain of two or more rings.

        Every ring takes part, drawn or not, so the lines trace the averaged
        rims section by section.
        """
        out: list[np.ndarray] = []
        for chain in self.chains:
            if len(chain) < 2:
                continue
            first = self.rings[chain[0]]
            xy = np.stack([self.rings[i].xy for i in chain])     # (M, N, 2)
            z = np.array([self.rings[i].z_um for i in chain])
            for k in _line_indices(first.n_points, lines):
                out.append(np.column_stack([xy[:, k, :], z]))
        return out

    def tie_segments(self, lines: int = DEFAULT_LINES) -> np.ndarray:
        """``(K, 2, 3)`` segments from each tie's smaller ring onto the larger.

        A point of the smaller ring goes straight down (or up) to the larger
        annotation's plane where it lies over the annotation, and to the
        nearest point of its rim where it does not: where a branch's outline
        meets the filled annotation it leaves.
        """
        segments: list[np.ndarray] = []
        for small, large in self.ties:
            a = self.rings[small]
            b = self.rings[large]
            pts = a.xy[_line_indices(a.n_points, lines)]
            target = _clamp_into(pts, b.xy)
            seg = np.stack([
                np.column_stack([pts, np.full(len(pts), a.z_um)]),
                np.column_stack([target, np.full(len(pts), b.z_um)]),
            ], axis=1)
            length = np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1)
            segments.append(seg[length > 1e-9])
        if not segments:
            return np.zeros((0, 2, 3))
        return np.concatenate(segments)

    def segments(self, lines: int = DEFAULT_LINES, spacing_um: float = 0.0) -> np.ndarray:
        """Everything as ``(S, 2, 3)`` line segments: rings, lines, ties."""
        parts: list[np.ndarray] = []
        for z, xy in self.ring_polylines(spacing_um):
            pts = np.column_stack([xy, np.full(len(xy), z)])
            parts.append(np.stack([pts, np.roll(pts, -1, axis=0)], axis=1))
        for line in self.line_polylines(lines):
            parts.append(np.stack([line[:-1], line[1:]], axis=1))
        parts.append(self.tie_segments(lines))
        parts = [p for p in parts if len(p)]
        return np.concatenate(parts) if parts else np.zeros((0, 2, 3))

    def moved_um(self) -> np.ndarray:
        """How far each ring's outline moved in averaging, microns."""
        return np.array([r.moved_um for r in self.rings])


# -- building ---------------------------------------------------------------


def annotation_wire_from_neuron(neuron, **kwargs) -> AnnotationWire:
    """:func:`annotation_wire` over a loaded neuron's own annotations.

    Closed curves take their outlines from the ``geometries`` table, which
    the loader, live registration and corrections keep in the frame the
    disc centers use.
    """
    from .closed_curve import annotations_from_geometries

    nodes = neuron.get_cell_nodes()
    outlines = {
        int(a.location_id): a.spline()
        for a in annotations_from_geometries(getattr(neuron, "geometries", None))
        if a.location_id
    }
    edges = getattr(neuron, "edges", None)
    links: list[tuple[int, int]] = []
    if edges is not None and len(edges) and {"A", "B"} <= set(edges.columns):
        links = list(zip(edges["A"].astype(int), edges["B"].astype(int), strict=True))
    return annotation_wire(nodes, links, outlines, **kwargs)


def annotation_wire(
    nodes: pd.DataFrame,
    links: Iterable[tuple[int, int]],
    outlines: Mapping[int, np.ndarray] | None = None,
    *,
    gain: float = DEFAULT_GAIN,
    size_tolerance: float = DEFAULT_SIZE_TOLERANCE,
    break_ratio: float = DEFAULT_BREAK_RATIO,
    fit: str = "quadratic",
    ring_arc_um: float = RING_ARC_UM,
) -> AnnotationWire:
    """Rings, chains and ties for one cell, every ring averaged along its chain.

    ``nodes`` needs ``ID``, ``Xum``, ``Yum``, ``Zum``, ``Rum`` and ``Z`` (the
    section), and ``TypeCode`` to tell closed curves (6) from discs.
    ``outlines`` maps a closed curve's Location ID to its outline in microns;
    a closed curve without one is drawn as the circle its radius makes.
    Links to annotations not in ``nodes`` are ignored.  ``gain`` 0 draws the
    annotations exactly as traced.  ``fit`` is a key of :data:`FITS`.
    """
    if fit not in FITS:
        raise ValueError(f"fit must be one of {sorted(FITS)}, not {fit!r}")
    outlines = outlines or {}
    shapes = _shapes(nodes, outlines)
    if not shapes:
        return AnnotationWire([], [], [], [])
    index = {s.location_id: k for k, s in enumerate(shapes)}
    pairs = sorted({
        (min(index[int(a)], index[int(b)]), max(index[int(a)], index[int(b)]))
        for a, b in links
        if int(a) in index and int(b) in index and int(a) != int(b)
    })

    chains, chosen = _chain_cover(shapes, pairs, break_ratio)

    rings: list[WireRing | None] = [None] * len(shapes)
    positions: list[np.ndarray] = []
    for chain in chains:
        members = [shapes[i] for i in chain]
        n = int(np.clip(np.ceil(max(m.perimeter for m in members) / ring_arc_um),
                        MIN_RING_POINTS, MAX_RING_POINTS))
        raw = [m.sample(n) for m in members]
        for k in range(1, len(raw)):
            raw[k] = np.roll(raw[k], -_best_shift(raw[k - 1], raw[k]), axis=0)
        s = _chain_positions(members)
        radii = np.array([m.radius for m in members])
        smoothed, sigma = _average(np.stack(raw), s, radii, gain, size_tolerance, FITS[fit])
        for k, (i, m) in enumerate(zip(chain, members, strict=True)):
            rings[i] = WireRing(
                location_id=m.location_id, z_um=m.z_um, section=m.section,
                kind=m.kind, radius_um=m.radius, raw=raw[k], xy=smoothed[k],
                sigma_um=float(sigma[k]),
            )
        positions.append(s)

    ties = []
    for a, b in pairs:
        if (a, b) in chosen:
            continue
        small, large = (a, b) if shapes[a].radius <= shapes[b].radius else (b, a)
        ties.append((small, large))
    # Every annotation is on exactly one chain (a lone one is a chain of one),
    # so ring k is shape k and the ties' indices hold.
    assert all(r is not None for r in rings)
    return AnnotationWire([r for r in rings if r is not None],
                          [np.asarray(c, dtype=int) for c in chains], positions, ties)


@dataclass
class _Shape:
    location_id: int
    z_um: float
    section: int
    kind: str
    center: np.ndarray
    radius: float
    perimeter: float
    #: A closed curve's outline, counterclockwise, starting at the point
    #: nearest the +x ray from its centroid; None for a disc.
    polygon: np.ndarray | None = None

    def sample(self, n: int) -> np.ndarray:
        """``n`` points around the rim, counterclockwise from the +x side."""
        if self.polygon is None:
            angles = 2.0 * np.pi * np.arange(n) / n
            return self.center + self.radius * np.column_stack([np.cos(angles), np.sin(angles)])
        closed = np.vstack([self.polygon, self.polygon[:1]])
        arc = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(closed, axis=0).T))])
        t = np.arange(n) * arc[-1] / n
        return np.column_stack([np.interp(t, arc, closed[:, 0]), np.interp(t, arc, closed[:, 1])])


def _shapes(nodes: pd.DataFrame, outlines: Mapping[int, np.ndarray]) -> list[_Shape]:
    if nodes is None or not len(nodes):
        return []
    types = (nodes["TypeCode"].to_numpy(dtype=int) if "TypeCode" in nodes.columns
             else np.ones(len(nodes), dtype=int))
    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    radius = nodes["Rum"].to_numpy(dtype=float)
    section = nodes["Z"].to_numpy(dtype=float)
    ids = nodes["ID"].to_numpy(dtype=np.int64)
    out: list[_Shape] = []
    for k in range(len(nodes)):
        if not np.isfinite(xyz[k]).all() or not np.isfinite(section[k]):
            continue
        loc = int(ids[k])
        polygon = None
        if types[k] == CLOSED_CURVE and loc in outlines:
            polygon = _clean_polygon(outlines[loc])
        if polygon is not None:
            area, centroid = _area_centroid(polygon)
            angle = np.abs(np.arctan2(polygon[:, 1] - centroid[1], polygon[:, 0] - centroid[0]))
            polygon = np.roll(polygon, -int(np.argmin(angle)), axis=0)
            closed = np.vstack([polygon, polygon[:1]])
            out.append(_Shape(
                loc, float(xyz[k, 2]), int(section[k]), "curve", centroid,
                float(np.sqrt(area / np.pi)),
                float(np.hypot(*np.diff(closed, axis=0).T).sum()), polygon,
            ))
            continue
        r = float(radius[k])
        if not np.isfinite(r) or r <= 0:
            continue
        out.append(_Shape(loc, float(xyz[k, 2]), int(section[k]), "disc",
                          xyz[k, :2].copy(), r, 2.0 * np.pi * r))
    return out


def _clean_polygon(points) -> np.ndarray | None:
    """Finite points, closing duplicate dropped, counterclockwise; None if degenerate."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) > 1 and np.hypot(*(pts[0] - pts[-1])) < 1e-9:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    area, _ = _area_centroid(pts, signed=True)
    if abs(area) < 1e-12:
        return None
    return pts[::-1].copy() if area < 0 else pts


def _area_centroid(pts: np.ndarray, *, signed: bool = False) -> tuple[float, np.ndarray]:
    x, y = pts[:, 0], pts[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    area = cross.sum() / 2.0
    if abs(area) < 1e-12:
        return float(area), pts.mean(axis=0)
    cx = ((x + xn) * cross).sum() / (6.0 * area)
    cy = ((y + yn) * cross).sum() / (6.0 * area)
    return (float(area) if signed else abs(float(area))), np.array([cx, cy])


def _chain_cover(shapes: list[_Shape], pairs: list[tuple[int, int]],
                 break_ratio: float) -> tuple[list[list[int]], set[tuple[int, int]]]:
    """Split the link graph into chains, most alike links first."""
    candidates = []
    for a, b in pairs:
        ra, rb = shapes[a].radius, shapes[b].radius
        ratio = max(ra, rb) / min(ra, rb)
        if ratio > break_ratio:
            continue
        lateral = float(np.hypot(*(shapes[a].center - shapes[b].center)))
        candidates.append((np.log(ratio) + lateral / (ra + rb), a, b))
    candidates.sort()

    parent = list(range(len(shapes)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    taken = [0] * len(shapes)
    up = [False] * len(shapes)
    down = [False] * len(shapes)
    neighbors: list[list[int]] = [[] for _ in shapes]
    chosen: set[tuple[int, int]] = set()
    for _cost, a, b in candidates:
        if taken[a] >= 2 or taken[b] >= 2:
            continue
        ra, rb = root(a), root(b)
        if ra == rb:
            continue
        step = shapes[b].section - shapes[a].section
        # a's link to b goes up for a and down for b (or the reverse); a chain
        # never turns back on itself through an annotation.
        if step > 0 and (up[a] or down[b]):
            continue
        if step < 0 and (down[a] or up[b]):
            continue
        if step > 0:
            up[a] = down[b] = True
        elif step < 0:
            down[a] = up[b] = True
        parent[ra] = rb
        taken[a] += 1
        taken[b] += 1
        neighbors[a].append(b)
        neighbors[b].append(a)
        chosen.add((a, b))

    seen = [False] * len(shapes)
    chains: list[list[int]] = []
    # Walk each path from its lower end (by section, then Location ID), so the
    # result does not depend on the order the annotations arrived in.
    starts = sorted((i for i in range(len(shapes)) if taken[i] <= 1),
                    key=lambda i: (shapes[i].section, shapes[i].location_id))
    for start in starts:
        if seen[start]:
            continue
        chain = [start]
        seen[start] = True
        while True:
            nxt = [j for j in neighbors[chain[-1]] if not seen[j]]
            if not nxt:
                break
            seen[nxt[0]] = True
            chain.append(nxt[0])
        chains.append(chain)
    return chains, chosen


def _chain_positions(members: list[_Shape]) -> np.ndarray:
    """Distance along a chain: Z steps, plus sideways travel beyond the rings' size."""
    s = np.zeros(len(members))
    for k in range(1, len(members)):
        a, b = members[k - 1], members[k]
        lateral = float(np.hypot(*(b.center - a.center)))
        r = (a.radius + b.radius) / 2.0
        travel = lateral * lateral / (lateral + r) if lateral > 0 else 0.0
        s[k] = s[k - 1] + np.hypot(b.z_um - a.z_um, travel)
    return s


def _best_shift(previous: np.ndarray, current: np.ndarray) -> int:
    """Rotation of ``current``'s points that best lines its shape up with ``previous``.

    Maximizes sum_i a[i] . b[i + shift] over the centered outlines (the
    circular cross-correlation), so lines along a chain do not twist.
    """
    a = previous - previous.mean(axis=0)
    b = current - current.mean(axis=0)
    n = len(a)
    corr = np.fft.irfft(np.conj(np.fft.rfft(a, axis=0)) * np.fft.rfft(b, axis=0),
                        n=n, axis=0).sum(axis=1)
    return int(np.argmax(corr))


def _average(raw: np.ndarray, s: np.ndarray, radii: np.ndarray, gain: float,
             size_tolerance: float, degree: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Each ring as a weighted fit over its chain's rings; returns rings and sigmas."""
    m = len(raw)
    out = raw.copy()
    sigma = np.zeros(m)
    if m < 3 or gain <= 0:
        return out, sigma
    to_end = np.minimum(s - s[0], s[-1] - s)
    sigma = np.minimum(gain * radii, to_end / 2.0)
    log_r = np.log(radii)
    tau = np.log(size_tolerance) if size_tolerance > 1 else 0.0
    for k in range(m):
        sg = sigma[k]
        if sg <= 1e-9:
            continue
        lo = int(np.searchsorted(s, s[k] - 3.0 * sg, side="left"))
        hi = int(np.searchsorted(s, s[k] + 3.0 * sg, side="right"))
        d = (s[lo:hi] - s[k]) / sg
        w = np.exp(-0.5 * d ** 2)
        if tau > 0:
            w *= np.exp(-0.5 * ((log_r[lo:hi] - log_r[k]) / tau) ** 2)
        out[k] = np.tensordot(_fit_weights(d, w, degree), raw[lo:hi], axes=1)
    return out, sigma


def _fit_weights(d: np.ndarray, w: np.ndarray, degree: int) -> np.ndarray:
    """Weights whose sum over the window is a weighted polynomial fit's value at 0.

    Degree 0 is the weighted mean.  Degree 2 is a local quadratic (the
    Savitzky-Golay idea with Gaussian weights): it passes a swelling or a
    taper through unchanged where a mean would flatten it, and removes only
    what wobbles faster.  Falls back a degree when the window cannot hold it.
    """
    for deg in range(degree, 0, -1):
        if len(d) <= deg + 1:
            continue
        x = np.vander(d, deg + 1, increasing=True)                  # (J, deg+1)
        a = x.T @ (w[:, None] * x)
        if np.linalg.cond(a) > 1e8:
            continue
        return w * (x @ np.linalg.solve(a, np.eye(deg + 1)[0]))
    return w / w.sum()


def _spaced(s: np.ndarray, spacing_um: float) -> np.ndarray:
    """Indices into a chain at least ``spacing_um`` apart, both ends kept."""
    if spacing_um <= 0 or len(s) <= 2:
        return np.arange(len(s))
    keep = [0]
    for k in range(1, len(s) - 1):
        if s[k] - s[keep[-1]] >= spacing_um:
            keep.append(k)
    keep.append(len(s) - 1)
    return np.asarray(keep)


def _line_indices(n_points: int, lines: int) -> np.ndarray:
    """``lines`` evenly spaced points of an ``n_points`` ring (all, if fewer)."""
    if lines >= n_points:
        return np.arange(n_points)
    return np.unique(np.round(np.arange(lines) * n_points / lines).astype(int) % n_points)


def _clamp_into(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Each point if it lies inside ``polygon``, else the nearest point of its rim."""
    inside = _inside(points, polygon)
    out = points.copy()
    if inside.all():
        return out
    a = polygon
    b = np.roll(polygon, -1, axis=0)
    ab = b - a                                                     # (E, 2)
    p = points[~inside][:, None, :]                                # (P, 1, 2)
    t = np.clip(((p - a) * ab).sum(axis=2) / np.maximum((ab * ab).sum(axis=1), 1e-18), 0, 1)
    nearest = a + t[..., None] * ab                                # (P, E, 2)
    d = np.hypot(*(nearest - p).transpose(2, 0, 1))
    out[~inside] = nearest[np.arange(len(p)), np.argmin(d, axis=1)]
    return out


def _inside(points: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    """Even-odd point-in-polygon test, vectorized over points."""
    x, y = points[:, 0][:, None], points[:, 1][:, None]
    x0, y0 = polygon[:, 0], polygon[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    crosses = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return (crosses & (x < at)).sum(axis=1) % 2 == 1
