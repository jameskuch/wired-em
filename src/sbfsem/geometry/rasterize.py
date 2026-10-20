"""Polygon rasterization for closed-curve annotations.

Replaces two MATLAB implementations at once:

* ``+sbfsem/+render/BinaryView.m`` and ``ClosedCurve.binarize``, which drew white
  polygons into an offscreen figure, called ``getframe``, and thresholded the
  screen capture.  That is the single biggest obstacle to headless operation and
  has no Python equivalent, so it is gone.
* ``lib/mpoly2mask.m``, used by ``util/renders/renderClosedCurveOutlines.m``,
  which is the cleaner of the two and is the model followed here.

Holes are handled explicitly: a pixel is inside the shape when it is inside the
outer ring and outside every cut-out.

The pixels are ``skimage.draw.polygon``'s, bit for bit.  skimage tests every
pixel center of the ring's bounding box against every edge
(``point_in_polygon`` in ``skimage/_shared/geometry.pxd``): with ~300-point
registered outlines that was 73 of the 80 s RPC1 c2628 took at 0.05 um
(2026-10-15).  :func:`polygon_mask` evaluates the same float64 expression only
in the few columns where an edge meets a pixel row and fills the rest of the
row by parity; skimage keeps the rings too small for that to pay.
"""

from __future__ import annotations

import math

import numpy as np
from skimage.draw import polygon as sk_polygon

__all__ = ["polygon_mask", "rasterize_rings"]

#: How near a pixel center must be to a vertex, in pixels, to count as on it
#: (skimage's ``point_in_polygon``).
VERTEX_EPS = 1e-12

#: Below this many vertices times pixels of the bounding box, skimage itself
#: is quicker: it costs ~25 us plus ~4 ns per vertex-pixel, :func:`_fill`
#: ~110-170 us whatever the size.  Measured on every ring of RPC1 c2628 (0.1,
#: 0.05 um), RC1 c410 (0.1 um) and Inferior c16137 (0.25 um, 5,812 disc
#: circles); 5e4 was the quickest of 1e4 to 2e5 on all four.
SKIMAGE_WORK = 50_000

#: Rings with a coordinate this large, or not finite, are left to skimage:
#: four columns round each crossing are only certain to bracket it while
#: float64 resolves a pixel with room to spare.
_FAST_LIMIT = 2.0**40


def polygon_mask(points, shape: tuple[int, int]) -> np.ndarray:
    """Boolean mask of one polygon on a ``(rows, cols)`` grid.

    ``points`` is ``(N, 2)`` in ``(x, y)`` order, in pixel coordinates.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    mask = np.zeros(shape, dtype=bool)
    if len(pts) < 3:
        return mask
    x = np.ascontiguousarray(pts[:, 0])
    y = np.ascontiguousarray(pts[:, 1])
    xmin, xmax, ymin, ymax = float(x.min()), float(x.max()), float(y.min()), float(y.max())
    if math.isfinite(xmin + xmax + ymin + ymax) and max(-xmin, xmax, -ymin, ymax) < _FAST_LIMIT:
        # skimage's own loop bounds (_polygon in skimage/draw/_draw.pyx).
        r_lo, r_hi = int(max(0.0, ymin)), min(shape[0] - 1, math.ceil(ymax))
        c_lo, c_hi = int(max(0.0, xmin)), min(shape[1] - 1, math.ceil(xmax))
        if r_hi < r_lo or c_hi < c_lo:
            return mask
        box = (r_lo, c_lo, r_hi - r_lo + 1, c_hi - c_lo + 1)
        if len(pts) * box[2] * box[3] >= SKIMAGE_WORK and _fill(x, y, mask, *box):
            return mask
    rr, cc = sk_polygon(y, x, shape=shape)
    mask[rr, cc] = True
    return mask


def _fill(xp: np.ndarray, yp: np.ndarray, mask: np.ndarray,
          r_lo: int, c_lo: int, h: int, w: int) -> bool:
    """Set ``skimage.draw.polygon``'s pixels of the ring ``(xp, yp)`` (x is the
    column, y the row) in ``mask``, on the ``h`` x ``w`` box from row ``r_lo``,
    column ``c_lo``.

    skimage counts a pixel center ``(x, y)`` in when it is within
    :data:`VERTEX_EPS` of a vertex, or when an odd number of edges cross its
    row to its right (R), or to its left (L); an odd count on one side only
    puts it on an edge, which is in too.  Edge ``i`` joins vertex ``i``, at
    ``(x0, y0)`` from the pixel center, to vertex ``i - 1`` (the last for
    ``i = 0``), at ``(x1, y1)``, and crosses on the right when one end is
    above the row and the other is not and ``(x0*y1 - x1*y0) / (y1 - y0) > 0``;
    on the left when one end is below and the other is not and that is
    ``< 0``.  For a given edge and row the expression never rises from one
    column to the next (every rounding step is monotonic), so evaluating it in
    skimage's own order at four columns round the crossing settles every
    column of the row.

    Returns False, with ``mask`` untouched, when the four columns do not
    bracket a crossing, which float64 rules out below :data:`_FAST_LIMIT`.
    """
    xq, yq = np.concatenate((xp[-1:], xp[:-1])), np.concatenate((yp[-1:], yp[:-1]))
    # The integer rows an edge crosses: R needs lo <= y < hi and L lo < y <= hi
    # (lo, hi its ends' rows), so every row from ceil(lo) to floor(hi) is one
    # or both; a level edge crosses none.
    lo, hi = np.minimum(yp, yq), np.maximum(yp, yq)
    first = np.maximum(np.ceil(lo), r_lo).astype(np.int64)
    last = np.minimum(np.floor(hi), r_lo + h - 1).astype(np.int64)
    count = np.where(lo < hi, np.maximum(last - first + 1, 0), 0)
    total = int(count.sum())
    # +1 where a run of inside pixels starts and -1 past where it ends, row
    # after row; the last row's runs may end one past its last pixel.
    steps = np.zeros(h * w + 1, dtype=np.int8)
    if total:
        edge = np.repeat(np.arange(len(xp)), count)
        row = np.arange(total) - np.repeat(np.cumsum(count) - count - first, count)
        y = row.astype(float)
        y0, y1 = yp[edge] - y, yq[edge] - y
        xi, xj = xp[edge], xq[edge]
        # The column where the edge meets the row, to within a pixel, then
        # skimage's expression at columns f - 1 .. f + 2.
        f = np.floor(xi + y0 / (y0 - y1) * (xj - xi)).astype(np.int64)
        x = (f[:, None] + np.arange(-1, 3)).astype(float)
        x0, x1 = xi[:, None] - x, xj[:, None] - x
        val = (x0 * y1[:, None] - x1 * y0[:, None]) / (y1 - y0)[:, None]
        if not ((val[:, 0] > 0).all() and (val[:, 3] < 0).all()):
            return False
        # An R crossing counts for the columns before f - 1 + (positives), an L
        # crossing for those from f + 3 - (negatives) on: each moves the row's
        # parity at that one column, as an offset into the box.
        at = (row - r_lo) * w
        r_at = at + np.minimum(np.maximum(f - 1 + np.count_nonzero(val > 0, axis=1) - c_lo, 0), w)
        l_at = at + np.minimum(np.maximum(f + 3 - np.count_nonzero(val < 0, axis=1) - c_lo, 0), w)
        for moves in (r_at[(y0 > 0) != (y1 > 0)], l_at[(y0 < 0) != (y1 < 0)]):
            # A closed ring crosses each row an even number of times on each
            # side, so sorted moves pair up into runs of odd parity; dropping
            # the empty ones leaves starts and ends without repeats.
            moves.sort()
            on, off = moves[0::2], moves[1::2]
            run = on < off
            steps[on[run]] += 1
            steps[off[run]] -= 1
    inside = (np.cumsum(steps[:-1], dtype=np.int8) > 0).reshape(h, w)

    # Pixel centers on a vertex.
    vc, vr = np.round(xp), np.round(yp)
    on_vertex = ((np.abs(xp - vc) < VERTEX_EPS) & (np.abs(yp - vr) < VERTEX_EPS)
                 & (vr >= r_lo) & (vr < r_lo + h) & (vc >= c_lo) & (vc < c_lo + w))
    if on_vertex.any():
        inside[vr[on_vertex].astype(np.int64) - r_lo,
               vc[on_vertex].astype(np.int64) - c_lo] = True
    mask[r_lo:r_lo + h, c_lo:c_lo + w] = inside
    return True


def rasterize_rings(rings, shape: tuple[int, int]) -> np.ndarray:
    """Rasterize an outer ring plus any number of cut-outs.

    ``rings[0]`` is the outer boundary; the rest are holes.
    """
    rings = [np.asarray(r, dtype=float).reshape(-1, 2) for r in rings if r is not None]
    if not rings:
        return np.zeros(shape, dtype=bool)
    mask = polygon_mask(rings[0], shape)
    for hole in rings[1:]:
        mask &= ~polygon_mask(hole, shape)
    return mask
