"""Curving the long links of a disc-traced cell's Closed curve volume.

:func:`~sbfsem.geometry.closed_curve.annotations_from_discs` draws a link
between two discs as the straight, linearly tapering run from one circle to
the other.  Where the discs sit close, as most do (Inferior c1178: half the
links step 0.24 um or less, 95 % under 1.5 um), the straight run is the
process.  Where they do not, the run is a straight band and a chain of them
a polyline with a corner at every disc: c1178 has 210 links over 1 um, 29
over 3 um, and three that cross 22 untraced sections (914 to 936) 2.4 to 5.4
um sideways, slanted bands from one stack of discs to the next (the user's
screenshots of c1178 and c16137, 2026-10-16).  A neurite is a smooth curve,
and the discs are samples of it.

Here such a link follows a curve through the discs instead: a cubic Hermite
segment from one disc center to the other, in three dimensions, its tangents
those of a Catmull-Rom spline through the chain's discs on either side, with
chordal knots (:data:`CURVE_ALPHA`).  A run that leaves a chain of discs
climbing through the sections leaves it climbing and then bends over to the
next disc; a chain along the section plane bends through its discs instead
of turning a corner at each.  Z never turns
back (the tangents' Z is held between 0 and three times the link's rise:
the condition of Fritsch & Carlson 1980 for a monotone cubic), so every
section the run crosses gets the part of the curve within one section of its
plane, flattened onto it, as the straight run does.  The radius tapers
linearly along the curve.

What a curve may do is bounded:

* **It passes through every disc center**: a Hermite segment ends at its
  two discs, whatever its tangents.
* **Straight at a chain's end and at a branch point**: a tangent needs one
  chain on the far side of the disc.  The far side is read from the first
  disc along it at least the disc's own radius away (a few discs into a
  stack, whose sideways jitter would otherwise tilt the tangent).
* **Straight where the chain turns back** by more than :data:`MAX_TURN_DEG`
  at the disc: a hairpin or a jump, through which a curve would balloon.
* **A bounded bend** (:data:`BEND_RADII`): nowhere tighter than the radius of
  the process there, as tight as a tube can turn without its inner side
  folding.
* **A bounded corridor** (:data:`CORRIDOR`): within this share of the
  chord's length of the chord.

A curve that breaks a bound is drawn less curved: both tangents are blended
toward the chord (``scale`` from 1, the Catmull-Rom curve, to 0, the straight
run) until it keeps them.  A curved run is not convex, so each section's
part of it is drawn as the union of the convex hulls of short steps along it
(:func:`curve_outline`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "BEND_RADII",
    "CORRIDOR",
    "CURVE_ALPHA",
    "MAX_TURN_DEG",
    "LinkCurve",
    "curve_outline",
    "link_curves",
    "turning_angles",
]

#: Catmull-Rom's knot exponent: 1 is the chordal spline, whose tangent at a
#: disc follows the side where the discs are dense.  That is the side the
#: process's direction was seen on: a long link leaves a chain of short steps
#: the way the chain runs.  The centripetal spline (0.5; Yuksel, Schaefer &
#: Keyser 2011) takes about the bisector of the two directions there, at about
#: half the chord's length, so a long link hugs its chord and keeps half the
#: corner: Inferior c1178, links of 1 um and over, the median turn at their
#: discs went from 29.1 degrees to 15.3 centripetal and 12.7 chordal, and the
#: longest band (22 untraced sections, 5.4 um across) bowed 0.16 um against
#: 0.32.  The loops and cusps the centripetal spline avoids are what the
#: bounds below catch (a cusp bends without limit).
CURVE_ALPHA = 1.0

#: A turn at a disc sharper than this (degrees) is a hairpin or a jump, not a
#: bend, and the runs either side keep the chord's tangent there.
MAX_TURN_DEG = 90.0

#: The tightest bend a curved run may take, in radii of the process there.
BEND_RADII = 1.0

#: How far a curved run may stray from its chord, as a share of the chord.
CORRIDOR = 0.25

#: Points along a candidate curve at which the bend and the corridor are checked.
CHECK_SAMPLES = 65

#: Halvings of ``scale`` before a curve that keeps breaking a bound is drawn straight.
SCALE_STEPS = 10

#: A curve whose ``scale`` came out under this is drawn as the straight run.
MIN_SCALE = 0.05

#: How far the steps a curved run is drawn in may cut inside the curve, um:
#: under half the finest pixel the menu offers (0.025 um).
STEP_TOLERANCE_UM = 0.01

#: Fewest and most steps a curved run is drawn in.
MIN_STEPS, MAX_STEPS = 4, 64

#: Most discs walked along a chain to find which way it runs on the far
#: side of a disc (a stack of 0.9 um discs, 90 nm apart, needs ten).
LOOK_BACK = 16

#: Points of ``z(u)`` a window of the run is looked up on.
WINDOW_SAMPLES = 257


@dataclass(frozen=True)
class LinkCurve:
    """One curved link: a cubic Hermite segment from disc ``lo`` (row index;
    the lower section) to disc ``hi``, in microns ``(x, y, z)``.

    ``m0`` and ``m1`` are its tangents at ``u`` = 0 and 1 (um per unit of
    ``u``) after the bounds; ``scale`` is how much of the Catmull-Rom curve's
    departure from the chord they keep (1 all of it).  ``ends`` says for each
    end whether it has a tangent of its own (``"curve"``) or keeps the
    chord's, and why: ``"end"`` (a chain's end), ``"branch"`` or ``"turn"``
    (the chain turns back there).  ``sagitta_um`` is the curve's largest
    distance from its chord, ``min_bend_ratio`` its tightest bend in radii of
    the process.
    """

    lo: int
    hi: int
    p0: np.ndarray
    p1: np.ndarray
    m0: np.ndarray
    m1: np.ndarray
    scale: float
    ends: tuple[str, str]
    sagitta_um: float
    min_bend_ratio: float

    @property
    def chord(self) -> np.ndarray:
        return self.p1 - self.p0

    def points(self, u) -> np.ndarray:
        """``(len(u), 3)`` points of the curve."""
        return _hermite(self.p0, self.p1, self.m0, self.m1, np.asarray(u, dtype=float))

    def window(self, f0: float, f1: float) -> tuple[float, float]:
        """The ``u`` over which the curve's Z runs from ``f0`` to ``f1`` of
        its rise (Z is monotone in ``u``)."""
        u = np.linspace(0.0, 1.0, WINDOW_SAMPLES)
        rise = self.p1[2] - self.p0[2]
        frac = np.maximum.accumulate((self.points(u)[:, 2] - self.p0[2]) / rise)
        frac[0], frac[-1] = 0.0, 1.0
        return float(np.interp(f0, frac, u)), float(np.interp(f1, frac, u))


def _hermite(p0, p1, m0, m1, u: np.ndarray) -> np.ndarray:
    u = u[:, None]
    u2, u3 = u * u, u * u * u
    return ((2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * m0
            + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * m1)


def _derivatives(p0, p1, m0, m1, u: np.ndarray):
    u = u[:, None]
    u2 = u * u
    d1 = ((6 * u2 - 6 * u) * p0 + (3 * u2 - 4 * u + 1) * m0
          + (-6 * u2 + 6 * u) * p1 + (3 * u2 - 2 * u) * m1)
    d2 = ((12 * u - 6) * p0 + (6 * u - 4) * m0 + (-12 * u + 6) * p1 + (6 * u - 2) * m1)
    return d1, d2


def _bounds(p0, p1, m0, m1, r0: float, r1: float) -> tuple[float, float]:
    """``(largest distance from the chord / chord, tightest bend / process
    radius)`` of a candidate curve."""
    u = np.linspace(0.0, 1.0, CHECK_SAMPLES)
    chord = p1 - p0
    length = float(np.linalg.norm(chord))
    rel = _hermite(p0, p1, m0, m1, u) - p0
    off = np.linalg.norm(np.cross(rel, chord / max(length, 1e-12)), axis=1)
    d1, d2 = _derivatives(p0, p1, m0, m1, u)
    speed = np.linalg.norm(d1, axis=1)
    cross = np.linalg.norm(np.cross(d1, d2), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        bend = np.where(cross > 0, speed**3 / cross, np.inf)
    bend = np.where(speed > 1e-9 * max(length, 1e-12), bend, 0.0)   # a cusp bends without limit
    radius = r0 + u * (r1 - r0)
    return float(off.max() / max(length, 1e-12)), float((bend / np.maximum(radius, 1e-9)).min())


def _neighbors(n: int, pairs) -> list[list[int]]:
    out: list[list[int]] = [[] for _ in range(n)]
    for a, b in pairs:
        if a != b and b not in out[a]:
            out[a].append(b)
            out[b].append(a)
    return out


def _far_side(i: int, j: int, adjacent, points, radius) -> tuple[int | None, str]:
    """The disc whose position says which way the chain runs into ``i``
    from the side away from ``j``: the first one along that side at least
    ``i``'s radius away, or the farthest within :data:`LOOK_BACK` discs.
    ``(None, why)`` at a chain's end or a branch."""
    if len(adjacent[i]) == 1:
        return None, "end"
    if len(adjacent[i]) > 2:
        return None, "branch"
    before, here = i, next(k for k in adjacent[i] if k != j)
    for _ in range(LOOK_BACK):
        if np.linalg.norm(points[here] - points[i]) >= radius[i]:
            break
        onward = [k for k in adjacent[here] if k != before]
        if len(onward) != 1:
            break
        before, here = here, onward[0]
    return here, ""


def _tangent(k: int, i: int, j: int, points: np.ndarray) -> np.ndarray:
    """Catmull-Rom's tangent at ``i`` on the way from ``k`` to ``j`` (knots
    :data:`CURVE_ALPHA`), scaled for a Hermite segment from ``i`` to ``j``
    over ``u`` in [0, 1]."""
    a, b, c = points[k], points[i], points[j]
    d0 = max(float(np.linalg.norm(b - a)) ** CURVE_ALPHA, 1e-9)
    d1 = max(float(np.linalg.norm(c - b)) ** CURVE_ALPHA, 1e-9)
    v = (b - a) / d0 - (c - a) / (d0 + d1) + (c - b) / d1
    return v * d1


def _turn(a: np.ndarray, b: np.ndarray) -> float:
    """Degrees between two directions (0 for a zero vector)."""
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))))


def link_curves(xy, z_um, radius, links, *, min_step_um: float,
                drawn=None) -> dict[tuple[int, int], LinkCurve]:
    """The curves of the links whose discs are at least ``min_step_um``
    apart, keyed ``(lo, hi)`` by row index, for discs at ``xy`` (``(N, 2)``,
    um), ``z_um`` and ``radius``.

    ``links`` are the discs' links as row-index pairs: they say which discs
    a chain runs through, so a disc's links decide whether it is a chain's
    end or a branch.  ``drawn`` are the ``(lo, hi)`` pairs whose run is
    drawn, the lower disc first, and the only ones curved (default: all of
    ``links`` between discs at different ``z_um``).  A link both of whose
    ends keep the chord's tangent, or whose curve came out (nearly)
    straight, is left out: it is drawn as the straight run.
    """
    points = np.column_stack([np.asarray(xy, dtype=float), np.asarray(z_um, dtype=float)])
    radius = np.asarray(radius, dtype=float)
    links = sorted({(min(a, b), max(a, b)) for a, b in links if a != b})
    adjacent = _neighbors(len(points), links)
    if drawn is None:
        drawn = [(a, b) if points[a, 2] <= points[b, 2] else (b, a)
                 for a, b in links if points[a, 2] != points[b, 2]]
    out: dict[tuple[int, int], LinkCurve] = {}
    for lo, hi in drawn:
        chord = points[hi] - points[lo]
        length = float(np.linalg.norm(chord))
        rise = float(chord[2])
        if length < min_step_um or length <= 1e-9 or rise <= 0:
            continue
        ends, tangents = [], []
        for i, j, sign in ((lo, hi, 1.0), (hi, lo, -1.0)):
            k, why = _far_side(i, j, adjacent, points, radius)
            if k is not None and _turn(points[i] - points[k], points[j] - points[i]) > MAX_TURN_DEG:
                k, why = None, "turn"
            if k is None:
                ends.append(why)
                tangents.append(chord.copy())
                continue
            ends.append("curve")
            # The tangent at ``hi`` is the one on the way from ``lo``:
            # Catmull-Rom's at ``hi`` toward its far side, turned round.
            m = sign * _tangent(k, i, j, points)
            m[2] = min(max(m[2], 0.0), 3.0 * rise)      # Z never turns back
            tangents.append(m)
        if "curve" not in ends:
            continue
        m0, m1 = tangents
        r0, r1 = float(radius[lo]), float(radius[hi])
        p0, p1 = points[lo], points[hi]

        def blend(s: float, m0=m0, m1=m1, chord=chord) -> tuple[np.ndarray, np.ndarray]:
            return chord + s * (m0 - chord), chord + s * (m1 - chord)

        def keeps(s: float, p0=p0, p1=p1, r0=r0, r1=r1, blend=blend) -> bool:
            b0, b1 = blend(s)
            sag, bend = _bounds(p0, p1, b0, b1, r0, r1)
            return sag <= CORRIDOR + 1e-12 and bend >= BEND_RADII - 1e-12

        scale = 1.0
        if not keeps(1.0):
            # The largest blend that keeps both bounds, by bisection from straight.
            low, high = 0.0, 1.0
            for _ in range(SCALE_STEPS):
                mid = 0.5 * (low + high)
                low, high = (mid, high) if keeps(mid) else (low, mid)
            scale = low
        if scale < MIN_SCALE:
            continue
        n0, n1 = blend(scale)
        sag, bend = _bounds(p0, p1, n0, n1, r0, r1)
        out[(lo, hi)] = LinkCurve(lo, hi, p0.copy(), p1.copy(), n0, n1, scale,
                                  (ends[0], ends[1]), sag * length, bend)
    return out


def curve_outline(curve: LinkCurve, f0: float, f1: float, r0: float, r1: float,
                  unit: np.ndarray) -> list[np.ndarray]:
    """The part of ``curve`` whose Z runs from ``f0`` to ``f1`` of its rise,
    as outlines in the section plane: the union of the convex hulls of the
    circles at short steps along it (radius ``r0`` at ``u`` = 0 to ``r1`` at
    1; ``unit`` the unit circle's points).  One outline, unless the union
    falls apart (it does not for a curve that keeps :data:`BEND_RADII`)."""
    from scipy.spatial import ConvexHull
    from shapely import Polygon, union_all

    u0, u1 = curve.window(f0, f1)
    length = float(np.linalg.norm(curve.chord))
    bend = max(curve.min_bend_ratio * min(r0, r1), 1e-6)
    # A step of length h cuts h^2 / (8 R) inside a bend of radius R.
    steps = int(np.clip(np.ceil(length / np.sqrt(8.0 * STEP_TOLERANCE_UM * bend)),
                        MIN_STEPS, MAX_STEPS))
    grid = np.linspace(0.0, 1.0, steps + 1)
    u = np.unique(np.concatenate([[u0], grid[(grid > u0) & (grid < u1)], [u1]]))
    centers = curve.points(u)[:, :2]
    radii = r0 + u * (r1 - r0)
    if len(u) < 2:
        centers, radii = np.vstack([centers, centers]), np.concatenate([radii, radii])
    hulls = []
    for k in range(len(centers) - 1):
        ring = np.vstack([centers[k] + radii[k] * unit, centers[k + 1] + radii[k + 1] * unit])
        hulls.append(Polygon(ring[ConvexHull(ring).vertices]))
    shape = union_all(hulls)
    parts = list(getattr(shape, "geoms", [shape]))
    return [np.asarray(p.exterior.coords, dtype=float)[:-1] for p in parts if not p.is_empty]


def turning_angles(points, links, curves: dict[tuple[int, int], LinkCurve],
                   ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """At every disc with two links, at least one of them curved: the turn
    between its two runs, degrees, as straight runs (``before``) and as drawn
    (``after``: each run's own tangent at the disc, the chord where it is
    straight).  ``points`` are the discs' ``(x, y, z)`` in um.  Returns the
    discs' row indices, ``before`` and ``after``."""
    points = np.asarray(points, dtype=float)
    adjacent = _neighbors(len(points), sorted({(min(a, b), max(a, b)) for a, b in links if a != b}))

    def heading(i: int, j: int) -> np.ndarray:
        """Direction of travel leaving ``i`` toward ``j``, as drawn."""
        curve = curves.get((i, j)) or curves.get((j, i))
        if curve is None:
            return points[j] - points[i]
        return curve.m0 if i == curve.lo else -curve.m1

    rows, before, after = [], [], []
    for i, near in enumerate(adjacent):
        if len(near) != 2:
            continue
        k, j = near
        if not any((i, x) in curves or (x, i) in curves for x in (k, j)):
            continue
        rows.append(i)
        before.append(_turn(points[i] - points[k], points[j] - points[i]))
        after.append(_turn(-heading(i, k), heading(i, j)))
    return np.asarray(rows, dtype=int), np.asarray(before), np.asarray(after)
