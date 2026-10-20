"""A process's thickness, measured and used as Bradley & Withers 2016 do.

Bradley & Withers, *Post-processing techniques for making reliable
measurements from curve-skeletons*, Computers in Biology and Medicine
72:120-131 (``docs/bradley-withers-2016-curve-skeleton-post-processing.pdf``),
brought here by Rebecca Pfeiffer on 2026-09-22.  Two of its tools:

* **The modal radius** (their §3.4): from a point on the centre line, the
  distance at which the object's boundary is densest -- the first peak of the
  boundary's weight with distance, taken above the inscribed radius, so a
  lobe or a notch does not decide it the way it decides the largest inscribed
  circle.  A traced outline is already a cross-section, so here it is
  measured in the section plane, from the center of the outline's largest
  inscribed circle (the deepest point of the profile, on its medial axis).
  On a circle it is the radius; on a lamella, its half-width; on a crescent,
  the half-width at its thickest.

  Measured instead from a closed curve's area centroid, as it was on
  2026-09-22, it is not a thickness: a crescent's centroid can lie outside
  its own outline (RPC1 c192 Location 98799, 5.37 by 1.23 um, read 2.26 um),
  and the peak moved with the smoothing (Location 106268: 0.66 um at a
  0.1 um kernel, 1.77 at 0.02).  From the deepest point, a kernel of 0.01 or
  0.03 um instead of 0.02 moves c192's 1,170 modal radii by a median
  0.008 um.  The smoothing does push the peak of a flat side outward, by
  about three quarters of the kernel: a 1 um slab reads 0.515 um, a circle
  exactly its radius.

* **The minimal sphere network** (their §3.2, eq. 1): along one unbranched
  path, from a kept point the next kept point is the farthest, *by
  distance*, of the run of following points whose spheres all touch the kept
  one.  The run must be unbroken ("so that for a closed loop path the
  sphere-network cannot miss out part of the path"), so a path that doubles
  back within a sphere keeps its excursion.  Nothing finer than the local
  thickness is kept, which is their argument: below it, the centre line's
  wander is surface detail and noise.
"""

from __future__ import annotations

import contextlib

import numpy as np
import shapely
from shapely.geometry import Polygon

__all__ = [
    "BANDWIDTH_UM", "PEAK_WINDOW_UM", "inscribed_circle", "modal_radii", "modal_radius",
    "sphere_network",
]

#: The Gaussian that smooths the boundary's weight with distance, microns:
#: their 0.25 to 0.75 voxels, here well under a traced outline's own wander.
#: At 0.05 a 1 um slab read 0.538 um.
BANDWIDTH_UM = 0.02
#: A peak is the largest value this far either side of it, microns, so a
#: ripple of the binning or of the tracing is not one.
PEAK_WINDOW_UM = 0.05


def _parts(geometry):
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _parts(part)
    else:
        yield geometry


def _largest_polygon(ring: np.ndarray):
    """The outline as a valid polygon: a self-crossing trace is repaired, and
    of the pieces that makes the largest is kept."""
    polygon = Polygon(ring)
    if polygon.is_valid:
        return polygon
    pieces = [g for g in _parts(shapely.make_valid(polygon))
              if g.geom_type == "Polygon" and g.area > 0]
    return max(pieces, key=lambda g: g.area) if pieces else None


def inscribed_circle(ring) -> tuple[np.ndarray | None, float]:
    """Center (x, y) and radius of the largest circle inside a closed ring:
    the profile's deepest point, which lies on its medial axis."""
    ring = np.asarray(ring, dtype=float)[:, :2]
    if len(ring) < 3:
        return None, 0.0
    polygon = _largest_polygon(ring)
    if polygon is None:
        return None, 0.0
    tolerance = max(1e-4, 1e-3 * float(np.sqrt(polygon.area)))
    line = shapely.maximum_inscribed_circle(polygon, tolerance)
    center, edge = np.asarray(line.coords, dtype=float)[:2]
    return center, float(np.linalg.norm(edge - center))


def modal_radius(ring, *, bandwidth_um: float = BANDWIDTH_UM) -> float:
    """The modal radius of one traced outline, microns (NaN if it has no inside).

    From the center of the largest inscribed circle, every stretch of the
    outline is binned by its distance ``R`` from there, weighted by its
    length over ``R`` (a circle of radius ``R`` has that much more boundary
    to offer: their ``1/R``), and the result smoothed by a Gaussian of
    ``bandwidth_um``.  The modal radius is the first peak (the largest value
    within :data:`PEAK_WINDOW_UM` either side) at or above the inscribed
    radius.  Their angular weight, which keeps only the boundary in the plane
    across the path, is 1 for every point of a section's outline.
    """
    ring = np.asarray(ring, dtype=float)[:, :2]
    center, r_in = inscribed_circle(ring)
    if center is None or r_in <= 0:
        return float("nan")
    # The outline sampled every fifth of a bandwidth, each sample carrying
    # its share of the segment's length.
    start = ring
    delta = np.roll(ring, -1, axis=0) - ring
    length = np.linalg.norm(delta, axis=1)
    count = np.maximum(np.ceil(length / (bandwidth_um / 5)).astype(np.int64), 1)
    which = np.repeat(np.arange(len(ring)), count)
    offset = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
    frac = (offset + 0.5) / count[which]
    points = start[which] + frac[:, None] * delta[which]
    weight = (length / count)[which]
    r = np.linalg.norm(points - center, axis=1)

    step = bandwidth_um / 10
    lo = max(r_in - 4 * bandwidth_um, 0.0)
    edges = np.arange(lo, float(r.max()) + 4 * bandwidth_um + step, step)
    hist, _ = np.histogram(r, bins=edges, weights=weight / np.maximum(r, 1e-12))
    x = np.arange(-40, 41) * step
    s = np.convolve(hist, np.exp(-0.5 * (x / bandwidth_um) ** 2), mode="same")
    mids = 0.5 * (edges[:-1] + edges[1:])
    above = np.nonzero(mids >= r_in)[0]
    if not len(above):
        return float(r_in)
    half = max(int(round(PEAK_WINDOW_UM / step)), 1)
    for i in above:
        if s[i] > 0 and s[i] >= s[max(i - half, 0):i + half + 1].max():
            return float(mids[i])
    return float(mids[above[int(np.argmax(s[above]))]])


def modal_radii(neuron, *, bandwidth_um: float = BANDWIDTH_UM) -> dict[int, float]:
    """Location ID -> modal radius, microns, for every annotation with a
    traced outline.  A circle has none; its stored radius is its modal
    radius.  Kept on the neuron: RPC1 c192's 1,170 outlines take under a
    second."""
    from .skeleton_check import _outlines

    cached = getattr(neuron, "_modal_radii", None)
    if cached is None:
        cached = {}
        with contextlib.suppress(AttributeError):
            neuron._modal_radii = cached
    key = round(float(bandwidth_um), 6)
    if key not in cached:
        values = {int(i): modal_radius(ring, bandwidth_um=bandwidth_um)
                  for i, ring in _outlines(neuron).items()}
        cached[key] = {i: r for i, r in values.items() if np.isfinite(r)}
    return cached[key]


def sphere_network(points, radii) -> np.ndarray:
    """Indices kept along one ordered path by Bradley & Withers's eq. 1.

    From the kept point ``k``, the next is the farthest (by distance) of the
    unbroken run of following points whose spheres touch ``k``'s,
    ``|p_j - p_k| <= r_j + r_k``; where the very next sphere does not touch,
    the next point is kept.  The first and last points are always kept.
    """
    p = np.asarray(points, dtype=float)
    r = np.asarray(radii, dtype=float)
    n = len(p)
    if n <= 2:
        return np.arange(n)
    keep = [0]
    k = 0
    while k < n - 1:
        d = np.linalg.norm(p[k + 1:] - p[k], axis=1)
        touching = d <= r[k] + r[k + 1:]
        run = len(touching) if touching.all() else int(np.argmin(touching))
        k += 1 + (int(np.argmax(d[:run])) if run else 0)
        keep.append(k)
    return np.asarray(keep, dtype=np.int64)
