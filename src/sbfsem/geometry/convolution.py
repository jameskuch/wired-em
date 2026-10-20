"""Convolution surfaces along the annotation skeleton.

Implements the potential field of *Robust quasi-uniform surface meshing of
neuronal morphology using line skeleton-based progressive convolution
approximation* (Frontiers in Neuroinformatics 16:953930, 2022,
doi:10.3389/fninf.2022.953930), the ``convolution`` pathway.

A convolution surface (Bloomenthal & Shoemake 1991) is the iso-surface of a
kernel integrated along a skeleton.  The paper's kernel is the finitely
supported quartic

    f(r) = (1 - r^2 / R^2)^2   for r <= R,   0 beyond,

convolved along every skeleton segment ``i`` with an effective radius
``R_i = 2 d_i`` (``d_i`` the segment's neurite radius) and a weight ``lambda_i``
chosen so that an infinitely long straight neurite of radius ``d_i`` is
reproduced exactly at the iso-value ``T``:

    F_line(rho) = 2 lambda int_0^a ((a^2 - x^2) / R^2)^2 dx
               = 16 lambda (R^2 - rho^2)^(5/2) / (15 R^4),     a^2 = R^2 - rho^2,
    lambda_i   = 15 T R_i^4 / (16 (R_i^2 - d_i^2)^(5/2)).

The total field is the plain sum ``S(p) = sum_i lambda_i F_i(p)`` and the
surface is ``S = T``: contributions superpose, so branch points and
close-passing processes blend into crease-free junctions without any
explicit union operator -- the property the paper wants, and the one that
distinguishes this pathway from the exact min-union of the voxel pathways.

The segment integral is evaluated in closed form here (the kernel is a
quartic polynomial in the arc-length parameter, clipped to its support), so
densely spaced annotations do not inflate the field: a segment contributes
in proportion to its length, not its sample count.  The paper's *meshing* --
progressive deformation of a sphere along the skeleton with remeshing --
is replaced by the block-wise marching cubes the pathway shares with
``voxel2``; the surface is the paper's, the triangulation is ours.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "MAX_PANEL_SUPPORT",
    "MERGE_EPSILON",
    "SUPPORT_FACTOR",
    "THRESHOLD",
    "PointKernel",
    "SegmentKernel",
    "merge_short_segments",
    "segment_weight",
    "point_weight",
]

#: ``R_i = SUPPORT_FACTOR * d_i``: the paper's empirical choice is 2.
#:
#: The weight makes an infinitely long neurite keep its radius, so a stretch
#: shorter than its kernel's reach (``2 sqrt(R^2 - d^2)`` along its axis) sums
#: to less than ``T``: ends taper, and a body about as tall as it is wide -- a
#: soma drawn as stacked discs -- shrinks as ``R / d`` grows.  At mid-height of
#: such a stack (height = diameter) the field on its axis is 1.63 T at 2, 1.04 T
#: at 2.5 and 0.78 T at 3, where nothing of it reaches the surface; Temporal
#: c118's soma is gone at 4.  The viewer's Kernel R/d stops at 2.5
#: (``MAX_PANEL_SUPPORT``); the library and the API take up to 4.
SUPPORT_FACTOR = 2.0

#: The widest kernel the viewer offers: the last at which a stack as tall as it
#: is wide still reaches the iso-value on its axis.
MAX_PANEL_SUPPORT = 2.5

#: The iso-value ``T``.  A pure scale (``lambda_i`` is proportional to it),
#: so 1 is as good as any other value.
THRESHOLD = 1.0

#: The paper eliminates trivial segments shorter than ``epsilon * R``.  Our
#: annotations come one per section, so a soma is 150 samples 0.075 um apart
#: with kernels 8 um wide; merging consecutive samples into segments at least
#: this fraction of their kernel radius long is that step, and it is also
#: what makes the field affordable (each kernel costs its support cube).
MERGE_EPSILON = 0.25


def merge_short_segments(points, radii, *, support: float = SUPPORT_FACTOR,
                         epsilon: float = MERGE_EPSILON) -> tuple[np.ndarray, np.ndarray]:
    """Merge consecutive samples into segments at least ``epsilon * R`` long.

    Walks the polyline accumulating chord length until it reaches
    ``epsilon * support * r`` for the running mean radius ``r``, then emits
    one segment endpoint.  The radius of a merged stretch is the mean of the
    samples it absorbed.  Returns the reduced ``(points, radii)`` polyline,
    always keeping both ends.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    rad = np.abs(np.asarray(radii, dtype=float).ravel())
    if len(pts) < 3:
        return pts, rad
    out_p = [pts[0]]
    out_r: list[float] = []
    acc_r: list[float] = [float(rad[0])]
    run = 0.0
    for i in range(1, len(pts)):
        run += float(np.linalg.norm(pts[i] - pts[i - 1]))
        acc_r.append(float(rad[i]))
        mean_r = float(np.mean(acc_r))
        if run >= epsilon * support * mean_r or i == len(pts) - 1:
            out_p.append(pts[i])
            out_r.append(mean_r)
            acc_r = [float(rad[i])]
            run = 0.0
    # Segment radii are per merged stretch; report them per endpoint as the
    # mean of the stretches meeting there so the caller's pair radii average
    # back to the stretch radius.
    seg_r = np.array(out_r, dtype=float)
    node_r = np.empty(len(out_p))
    node_r[0] = seg_r[0]
    node_r[-1] = seg_r[-1]
    node_r[1:-1] = 0.5 * (seg_r[:-1] + seg_r[1:])
    return np.vstack(out_p), node_r


def segment_weight(d: float, R: float, T: float = THRESHOLD) -> float:
    """``lambda_i`` for a segment of neurite radius ``d`` and kernel radius ``R``.

    Makes an infinitely long straight neurite of radius ``d`` the ``T``
    iso-surface of the field.  Requires ``R > d``.
    """
    d = float(d)
    R = float(R)
    if not R > d > 0:
        raise ValueError(f"kernel radius R={R} must exceed the neurite radius d={d} > 0")
    return 15.0 * T * R ** 4 / (16.0 * (R * R - d * d) ** 2.5)


def point_weight(d: float, R: float, T: float = THRESHOLD) -> float:
    """Weight for an isolated point source so that its ``T`` iso-surface is
    a sphere of radius ``d``: ``lambda f(d / R) = T``."""
    d = float(d)
    R = float(R)
    if not R > d > 0:
        raise ValueError(f"kernel radius R={R} must exceed the radius d={d} > 0")
    return T / (1.0 - d * d / (R * R)) ** 2


@dataclass
class SegmentKernel:
    """The quartic kernel integrated along one skeleton segment."""

    a: np.ndarray
    b: np.ndarray
    d: float          # neurite radius the segment stands for
    R: float          # kernel support radius (SUPPORT_FACTOR * d)
    lam: float        # weight from :func:`segment_weight`

    @classmethod
    def from_pair(cls, a, b, ra: float, rb: float, *,
                  support: float = SUPPORT_FACTOR, T: float = THRESHOLD) -> SegmentKernel:
        a = np.asarray(a, dtype=float)
        b = np.asarray(b, dtype=float)
        d = 0.5 * (abs(float(ra)) + abs(float(rb)))
        R = max(float(support), 1.05) * d
        return cls(a, b, d, R, segment_weight(d, R, T))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return np.minimum(self.a, self.b) - self.R, np.maximum(self.a, self.b) + self.R

    def potential(self, pts: np.ndarray) -> np.ndarray:
        """``lambda_i * int_segment f(|p - q(s)|) ds`` for every point, exactly.

        With ``u`` the arc-length coordinate relative to the foot of ``p`` on
        the segment's line and ``rho`` the perpendicular distance, the kernel
        along the line is ``((a^2 - u^2) / R^2)^2`` with ``a^2 = R^2 - rho^2``,
        supported on ``|u| <= a``; the integral over the clipped interval is
        the polynomial ``[a^4 u - 2 a^2 u^3 / 3 + u^5 / 5] / R^4``.
        """
        pts = np.asarray(pts, dtype=float).reshape(-1, 3)
        e = self.b - self.a
        L = float(np.linalg.norm(e))
        R2 = self.R * self.R
        out = np.zeros(len(pts), dtype=float)
        if L <= 1e-12:
            return out
        e = e / L
        rel = pts - self.a
        t = rel @ e                                   # foot coordinate along the line
        rho2 = np.einsum("ij,ij->i", rel, rel) - t * t
        a2 = R2 - rho2
        live = a2 > 0.0
        if not live.any():
            return out
        a2 = a2[live]
        t = t[live]
        a = np.sqrt(a2)
        lo = np.maximum(-t, -a)                       # segment start, in u
        hi = np.minimum(L - t, a)                     # segment end, in u
        span = hi > lo
        if not span.any():
            return out

        def prim(u: np.ndarray, a2v: np.ndarray) -> np.ndarray:
            return a2v * a2v * u - (2.0 / 3.0) * a2v * u ** 3 + u ** 5 / 5.0

        val = np.zeros(len(a2))
        val[span] = (prim(hi[span], a2[span]) - prim(lo[span], a2[span])) / (R2 * R2)
        out[live] = self.lam * val
        return out


@dataclass
class PointKernel:
    """The kernel at a single annotation nothing else joins: a sphere of the
    annotation's radius (the paper's skeletons have no such strays)."""

    c: np.ndarray
    d: float
    R: float
    lam: float

    @classmethod
    def from_disc(cls, c, r: float, *, support: float = SUPPORT_FACTOR,
                  T: float = THRESHOLD) -> PointKernel:
        c = np.asarray(c, dtype=float)
        d = abs(float(r))
        R = max(float(support), 1.05) * d
        return cls(c, d, R, point_weight(d, R, T))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.c - self.R, self.c + self.R

    def potential(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=float).reshape(-1, 3)
        r2 = np.einsum("ij,ij->i", pts - self.c, pts - self.c)
        inside = r2 < self.R * self.R
        out = np.zeros(len(pts), dtype=float)
        out[inside] = self.lam * (1.0 - r2[inside] / (self.R * self.R)) ** 2
        return out
