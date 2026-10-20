"""Polygon rasterization: the same pixels as ``skimage.draw.polygon``.

:func:`~sbfsem.geometry.rasterize.polygon_mask` fills by crossings per row
instead of testing every pixel against every edge, leaving only small rings to
skimage; nothing it sets may differ from skimage's, down to pixel centers on
edges and vertices.  The ``fill_only`` tests send every ring, however small,
through the fill and fail if any reaches skimage.
"""

from __future__ import annotations

import numpy as np
import pytest
from skimage.draw import polygon as sk_polygon

import sbfsem.geometry.rasterize as rasterize
from sbfsem.geometry.rasterize import polygon_mask, rasterize_rings


def skimage_mask(points, shape):
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    mask = np.zeros(shape, dtype=bool)
    if len(points) >= 3:
        rr, cc = sk_polygon(points[:, 1], points[:, 0], shape=shape)
        mask[rr, cc] = True
    return mask


def unfused_mask(points, shape):
    """skimage's rule in numpy, each product rounded on its own.

    That is what an x86_64 build of skimage computes, bit for bit (all 3,600
    random polygons below, 2026-10-20). An arm64 build fuses ``x0*y1 - x1*y0``
    into one multiply-add, rounding ``x1*y0`` alone: on GitHub's macOS runner
    the near-vertex and decimal polygons came out 11 and 1 pixels away from the
    fill, exactly the pixels ``math.fma`` predicts. numpy never fuses, so where
    skimage does the fill is held to this rule instead.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    if len(pts) < 3:
        return np.zeros(shape, dtype=bool)
    rows, cols = shape
    yy, xx = np.mgrid[0:rows, 0:cols].astype(float)
    right = np.zeros(shape, dtype=bool)
    left = np.zeros(shape, dtype=bool)
    for i in range(len(pts)):
        x0, y0 = pts[i, 0] - xx, pts[i, 1] - yy
        x1, y1 = pts[i - 1, 0] - xx, pts[i - 1, 1] - yy
        with np.errstate(divide="ignore", invalid="ignore"):
            val = (x0 * y1 - x1 * y0) / (y1 - y0)
        right ^= ((y0 > 0) != (y1 > 0)) & (val > 0)
        left ^= ((y0 < 0) != (y1 < 0)) & (val < 0)
    mask = right | left
    for vx, vy in pts:
        vc, vr = round(vx), round(vy)
        if (abs(vx - vc) < rasterize.VERTEX_EPS and abs(vy - vr) < rasterize.VERTEX_EPS
                and 0 <= vr < rows and 0 <= vc < cols):
            mask[vr, vc] = True
    return mask


# The 134th decimal polygon, which one pixel tells apart: does this build of
# skimage fuse the multiply-add?
_PROBE = np.array([[458, 246], [438, 576], [344, 93], [437, 469], [588, 339], [264, 508], [21, 161],
                   [279, 40], [188, 291], [110, 451], [577, 530], [544, 304], [65, 211], [24, 119],
                   [284, 529], [523, 200], [312, 532], [426, 483], [53, 545]]) * 0.1
SKIMAGE_FUSES = not np.array_equal(skimage_mask(_PROBE, (47, 59)), unfused_mask(_PROBE, (47, 59)))


@pytest.fixture
def fill_only(monkeypatch):
    def refuse(r, c, shape):
        raise AssertionError("ring left to skimage")

    monkeypatch.setattr(rasterize, "SKIMAGE_WORK", 0)
    monkeypatch.setattr(rasterize, "sk_polygon", refuse)


def assert_same(points, shape):
    expected = (unfused_mask if SKIMAGE_FUSES else skimage_mask)(points, shape)
    actual = polygon_mask(points, shape)
    assert actual.dtype == bool and actual.shape == expected.shape
    diff = np.argwhere(actual != expected)
    assert not len(diff), f"{len(diff)} pixels differ, first (row, col) {diff[:5].tolist()}"


def _star(rng, n, cx, cy, r0, r1):
    angle = np.sort(rng.uniform(0, 2 * np.pi, n))
    radius = rng.uniform(r0, r1, n)
    return np.column_stack([cx + radius * np.cos(angle), cy + radius * np.sin(angle)])


def _random_polygon(rng, kind):
    rows, cols = (int(v) for v in rng.integers(1, 60, size=2))
    size = max(rows, cols)
    if kind == "float":         # anything, self-intersecting
        p = rng.uniform(-5, size + 5, size=(rng.integers(3, 40), 2))
    elif kind == "integer":     # pixel centers on edges and vertices
        p = rng.integers(-3, size + 3, size=(rng.integers(3, 25), 2)).astype(float)
    elif kind == "half":        # vertices between pixel centers
        p = rng.integers(-6, 2 * size + 6, size=(rng.integers(3, 25), 2)) / 2.0
    elif kind == "concave":
        p = _star(rng, rng.integers(3, 300), cols / 2, rows / 2, 0.2 * min(rows, cols), 0.7 * size)
    elif kind == "convex":      # a smooth ring of up to ~400 points, like a registered outline
        a = np.linspace(0, 2 * np.pi, rng.integers(3, 400), endpoint=False)
        r = rng.uniform(0.1, size)
        p = np.column_stack([rng.uniform(0, cols) + r * np.cos(a),
                             rng.uniform(0, rows) + r * np.sin(a)])
    elif kind == "tiny":        # sub-pixel
        p = rng.uniform(0, size, 2) + rng.uniform(-0.8, 0.8, size=(rng.integers(3, 8), 2))
    elif kind == "near-vertex":  # vertices either side of the 1e-12 vertex tolerance
        p = rng.integers(0, size, size=(rng.integers(3, 20), 2)).astype(float)
        p += rng.choice([0, 1e-16, 5e-13, -5e-13, 1e-12, -1e-12, 2e-12, 1e-11], size=p.shape)
    elif kind == "decimal":     # tenths: edges through pixel centers, products inexact
        p = rng.integers(0, 10 * size, size=(rng.integers(3, 20), 2)) * 0.1
    elif kind == "touching":    # a figure eight through one vertex twice
        c = rng.uniform(0, size, 2)
        r = rng.uniform(1, size / 2 + 1)
        p = np.array([c, c + [r, r], c + [r, 0], c, c - [r, r], c - [0, r]])
        if rng.random() < 0.5:
            p = np.round(p)
    elif kind == "repeated":    # repeated vertices and an explicitly closed ring
        p = rng.integers(0, size, size=(rng.integers(3, 12), 2)).astype(float)
        p = np.repeat(p, rng.integers(1, 3, size=len(p)), axis=0)
        p = np.vstack([p, p[:1]])
    elif kind == "level":       # level and plumb edges on pixel rows and columns
        x = np.sort(rng.integers(0, cols + 2, 4)).astype(float)
        y = np.sort(rng.integers(0, rows + 2, 4)).astype(float)
        p = np.array([[x[0], y[0]], [x[3], y[0]], [x[3], y[2]], [x[2], y[2]],
                      [x[2], y[1]], [x[1], y[1]], [x[1], y[3]], [x[0], y[3]]])
    else:                       # partly or wholly off the grid
        p = _star(rng, rng.integers(3, 50), rng.uniform(-50, 100), rng.uniform(-50, 100), 1, 80)
    if rng.random() < 0.2:
        p = p + rng.uniform(-3, 3, 2)
    return p, (rows, cols)


KINDS = ["float", "integer", "half", "concave", "convex", "tiny", "near-vertex",
         "decimal", "touching", "repeated", "level", "off-grid"]


@pytest.mark.usefixtures("fill_only")
@pytest.mark.parametrize("kind", KINDS)
def test_random_polygons_match_skimage(kind):
    rng = np.random.default_rng(sum(map(ord, kind)))
    for _ in range(300):
        assert_same(*_random_polygon(rng, kind))


@pytest.mark.parametrize("kind", KINDS)
def test_the_unfused_rule_is_skimage_where_skimage_does_not_fuse(kind):
    if SKIMAGE_FUSES:
        pytest.skip("this build of skimage fuses x0*y1 - x1*y0; an x86_64 run checks the rule")
    rng = np.random.default_rng(sum(map(ord, kind)))
    for _ in range(300):
        points, shape = _random_polygon(rng, kind)
        assert np.array_equal(unfused_mask(points, shape), skimage_mask(points, shape))


@pytest.mark.usefixtures("fill_only")
@pytest.mark.parametrize("points", [
    [[0, 0], [4, 0], [4, 3], [0, 3]],                   # every boundary pixel on an edge
    [[0.5, 0.5], [9.5, 0.5], [9.5, 6.5], [0.5, 6.5]],   # between pixel centers
    [[2, 2], [2, 2], [5, 2], [5, 5], [2, 2]],           # repeated and closing vertex
    [[1, 1], [6, 1], [1, 1]],                           # collapsed to a line
    [[3, 3], [3, 3], [3, 3]],                           # collapsed to a point
    [[3 + 1e-13, 3 - 1e-13], [3.4, 3.1], [3.1, 3.4]],   # a vertex within 1e-12 of a center
    [[-4, -4], [-1, -4], [-1, -1]],                     # wholly off the grid
    [[-3, 2], [30, 2.5], [5, 40]],                      # partly off the grid
    [[0, 0], [5, 5], [5, 0], [0, 5]],                   # bow tie
])
def test_special_polygons_match_skimage(points):
    assert_same(points, (12, 14))


@pytest.mark.usefixtures("fill_only")
def test_registered_outline_scale_matches_skimage():
    """A ring like a registered outline at 0.05 um: a few hundred points on
    a window of a few hundred pixels, offset by an arbitrary float."""
    rng = np.random.default_rng(2628)
    for _ in range(8):
        p = _star(rng, 300, 150.3, 120.7, 60, 110) + rng.uniform(-1, 1, 2)
        assert_same(p, (260, 300))


def test_fewer_than_three_points_is_empty():
    assert not polygon_mask([[1, 1], [3, 3]], (5, 5)).any()
    assert not polygon_mask(np.empty((0, 2)), (5, 5)).any()


def test_skimage_keeps_small_and_unusual_rings(monkeypatch):
    """Rings below SKIMAGE_WORK, or not finite, or too large to bracket a
    crossing, go to skimage itself; the rest never do."""
    calls = []
    monkeypatch.setattr(rasterize, "sk_polygon",
                        lambda r, c, shape: calls.append(len(r)) or sk_polygon(r, c, shape=shape))
    small = [[1, 1], [5, 4], [6, 2]]
    unusual = ([[1, 1], [np.nan, 4], [6, 2], [5, 6]],
               [[1, 1], [np.inf, 4], [6, 2]],
               [[1, 1], [2.0**41, 4], [6, 2]])
    for points in (small, *unusual):
        assert_same(points, (8, 8))
    assert calls == [3, 4, 3, 3]
    calls.clear()
    big = _star(np.random.default_rng(1), 300, 40.2, 30.6, 10, 25)
    assert len(big) * 51 * 51 >= rasterize.SKIMAGE_WORK
    assert_same(big, (70, 90))
    assert calls == []


def test_holes_are_cut_out():
    outer = [[1, 1], [11, 1], [11, 9], [1, 9]]
    hole = [[4, 3], [8, 3], [8, 7], [4, 7]]
    expected = skimage_mask(outer, (12, 14)) & ~skimage_mask(hole, (12, 14))
    np.testing.assert_array_equal(rasterize_rings([outer, hole, None], (12, 14)), expected)
    assert not rasterize_rings([], (3, 3)).any()
