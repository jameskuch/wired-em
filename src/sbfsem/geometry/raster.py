"""A mesh drawn to a picture without a GPU: an orthographic z-buffer.

Render compare's report (:mod:`sbfsem.export.compare_pdf`) prints what each
rendering looks like and where two of them disagree, on a server that has no
display and no OpenGL.  This draws a triangle mesh the way the viewer's
orthographic camera does -- each pixel shows the nearest surface along the view
direction -- shaded by a light near the camera, in one flat color or colored by
a value per vertex (a distance) through a color map.

The views are the viewer's (``render3d.js`` ``Renderer3D.VIEWPOINTS``): a
direction from what is looked at toward the camera and an up vector, so XY1 is
X right and Y up seen from +Z, XZ1 is X right and Z up seen from -Y and YZ1 is Y
right and Z up seen from +X.  A view taken from the page carries its Flip Z,
under which the viewer mirrors the volume's Z before it is looked at; the same
mirror is applied here, so the picture is the one on screen.

Coverage is exact: a pixel is drawn by every triangle whose projection holds the
pixel's center, and the nearest one wins.  The surfaces tile, so a dense mesh
has no holes however small its triangles are; edges are smoothed by drawing at
``supersample`` times the size and averaging.  Everything is in the mesh's own
units (microns here), and :class:`Frame` says where a micron lands, so several
meshes drawn in one frame line up pixel for pixel and a scale bar can be drawn.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

__all__ = [
    "NAMED_VIEWS",
    "Frame",
    "Picture",
    "View",
    "extent",
    "frame_around",
    "frame_for",
    "rasterize",
    "section_segments",
    "view_basis",
]


@dataclass(frozen=True)
class View:
    """Where a picture is taken from: ``direction`` points from the scene toward
    the camera, ``up`` is the picture's up (both in the volume's frame), and
    ``mirror_z`` is the viewer's Flip Z (the volume's Z mirrored first)."""

    key: str
    label: str
    direction: tuple[float, float, float]
    up: tuple[float, float, float]
    mirror_z: bool = False


#: The viewer's named viewpoints the report draws (render3d.js VIEWPOINTS).
NAMED_VIEWS: dict[str, View] = {
    "xy1": View("xy1", "XY1 (from +Z: X right, Y up)", (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "xz1": View("xz1", "XZ1 (from -Y: X right, Z up)", (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "yz1": View("yz1", "YZ1 (from +X: Y right, Z up)", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
}


def view_basis(view: View) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The picture's right, up and toward-the-camera unit vectors.

    ``up`` is made square to the direction (its part along the direction is
    dropped); an ``up`` along the direction itself is replaced by the axis least
    aligned with it, so any direction gives a picture.
    """
    toward = np.asarray(view.direction, dtype=float)
    norm = float(np.linalg.norm(toward))
    if not norm > 0:
        raise ValueError("a view needs a direction")
    toward = toward / norm
    up = np.asarray(view.up, dtype=float)
    up = up - toward * float(up @ toward)
    if not float(np.linalg.norm(up)) > 1e-9:
        axis = np.zeros(3)
        axis[int(np.argmin(np.abs(toward)))] = 1.0
        up = axis - toward * float(axis @ toward)
    up = up / float(np.linalg.norm(up))
    forward = -toward
    right = np.cross(forward, up)
    right /= float(np.linalg.norm(right))
    return right, np.cross(right, forward), toward


def _placed(vertices: np.ndarray, view: View) -> np.ndarray:
    """The vertices as the viewer places them: Z mirrored under Flip Z."""
    v = np.asarray(vertices, dtype=float).reshape(-1, 3)
    if view.mirror_z:
        v = v * np.array([1.0, 1.0, -1.0])
    return v


@dataclass(frozen=True)
class Frame:
    """Where the picture is: ``u0`` and ``v0`` are the picture coordinates
    (microns along the view's right and up) of the top-left corner,
    ``um_per_px`` the size of a pixel, and ``width`` x ``height`` the picture in
    pixels.  :meth:`project` puts 3D points in it."""

    view: View
    u0: float
    v0: float
    um_per_px: float
    width: int
    height: int

    def project(self, points: np.ndarray) -> np.ndarray:
        """``(n, 2)`` pixel positions (x right, y down; 0 at the left/top edge)
        of points in the volume's frame."""
        right, up, _ = view_basis(self.view)
        p = _placed(points, self.view)
        return np.column_stack([(p @ right - self.u0) / self.um_per_px,
                                (self.v0 - p @ up) / self.um_per_px])

    @property
    def extent_um(self) -> tuple[float, float]:
        """The picture's width and height in microns."""
        return self.width * self.um_per_px, self.height * self.um_per_px


def extent(vertices: np.ndarray, view: View) -> tuple[float, float, float, float]:
    """Where ``vertices`` lie in the picture's coordinates: ``(lo_u, hi_u, lo_v,
    hi_v)`` in microns along the view's right and up."""
    right, up, _ = view_basis(view)
    p = _placed(vertices, view)
    if not len(p):
        return (0.0, 0.0, 0.0, 0.0)
    u, v = p @ right, p @ up
    return float(u.min()), float(u.max()), float(v.min()), float(v.max())


def frame_for(meshes: Sequence[np.ndarray], view: View, width: int, height: int, *,
              margin: float = 0.04) -> Frame:
    """A frame that holds every vertex of ``meshes`` (vertex arrays) with
    ``margin`` of the picture spare on each side, the aspect kept.  Drawing
    several meshes in one frame puts them at the same place and scale."""
    spans = [extent(v, view) for v in meshes if len(v)]
    if not spans:
        return Frame(view, -1.0, 1.0, 2.0 / max(width, height), int(width), int(height))
    box = np.array(spans)
    return frame_around(view, (float(box[:, 0].min()), float(box[:, 1].max()),
                               float(box[:, 2].min()), float(box[:, 3].max())),
                        width, height, margin=margin)


def frame_around(view: View, box: tuple[float, float, float, float], width: int, height: int,
                 *, margin: float = 0.04) -> Frame:
    """A ``width`` x ``height`` frame centered on ``box`` (an :func:`extent`)
    that holds it with ``margin`` of the picture spare on each side."""
    lo_u, hi_u, lo_v, hi_v = box
    span_u = max(hi_u - lo_u, 1e-6)
    span_v = max(hi_v - lo_v, 1e-6)
    usable = 1.0 - 2.0 * margin
    scale = max(span_u / (width * usable), span_v / (height * usable))
    cu, cv = 0.5 * (lo_u + hi_u), 0.5 * (lo_v + hi_v)
    return Frame(view, cu - 0.5 * width * scale, cv + 0.5 * height * scale, scale,
                 int(width), int(height))


@dataclass(frozen=True)
class Picture:
    """A drawn mesh: ``rgb`` (height, width, 3) in 0..1 over ``background``,
    ``coverage`` the share of each pixel the surface covers (0..1), and
    ``depth`` the nearest surface's depth toward the camera at each pixel (NaN
    where none; at the picture's own resolution, from the supersampled center)."""

    rgb: np.ndarray
    coverage: np.ndarray
    depth: np.ndarray

    def to_uint8(self) -> np.ndarray:
        return np.clip(np.round(self.rgb * 255.0), 0, 255).astype(np.uint8)


#: Most candidate pixels one block of triangles tests at once (memory: about 80
#: bytes each, so 4 M is 320 MB at the peak of a block).
BLOCK_PIXELS = 2_000_000
#: Bounding-box sizes (pixels) triangles are grouped under: a triangle is tested
#: on the grid of the smallest size that holds its box.
_SIZES = np.array([1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512,
                   768, 1024, 1536, 2048, 3072, 4096, 6144, 8192])


def _vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Area-weighted vertex normals (unit; zero where no face is)."""
    corners = vertices[faces]
    face_n = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    out = np.zeros_like(vertices)
    for k in range(3):
        np.add.at(out, faces[:, k], face_n)
    length = np.linalg.norm(out, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(length > 0, out / length, 0.0)


def rasterize(
    vertices: np.ndarray,
    faces: np.ndarray,
    frame: Frame,
    *,
    color: Sequence[float] = (0.8, 0.8, 0.8),
    values: np.ndarray | None = None,
    colormap: Callable[[np.ndarray], np.ndarray] | None = None,
    supersample: int = 2,
    ambient: float = 0.32,
    background: Sequence[float] = (1.0, 1.0, 1.0),
) -> Picture:
    """Draw a triangle mesh in ``frame``.

    Every triangle is ``color`` (RGB in 0..1), or, with ``values`` (one per
    vertex) and ``colormap`` (an array of values to ``(n, 3)`` RGB), colored by
    the value at each pixel, interpolated across the triangle before it is
    mapped, so a coarse mesh is colored as finely as a dense one.  Shading is
    two-sided Lambert from a light just above and to the right of the camera,
    ``ambient`` of it everywhere, on the interpolated vertex normals.
    """
    if values is not None and colormap is None:
        raise ValueError("values need a colormap")
    ss = max(1, int(supersample))
    W, H = frame.width * ss, frame.height * ss
    size = W * H
    zbuf = np.full(size, -np.inf)
    shade = np.zeros(size, dtype=np.float32)
    vbuf = np.zeros(size, dtype=np.float64) if values is not None else None

    verts = _placed(vertices, frame.view)
    tri = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if len(verts) and len(tri):
        right, up, toward = view_basis(frame.view)
        step = frame.um_per_px / ss
        # Pixel centers sit at whole numbers: the top-left pixel's center is (0, 0).
        px = (verts @ right - frame.u0) / step - 0.5
        py = (frame.v0 - verts @ up) / step - 0.5
        depth = verts @ toward
        normal = _vertex_normals(verts, tri)
        light = toward + 0.35 * up + 0.25 * right
        light /= float(np.linalg.norm(light))
        lit = np.abs(normal @ light)
        val = None if values is None else np.asarray(values, dtype=float).ravel()
        if val is not None and len(val) != len(verts):
            raise ValueError(f"{len(val)} values for {len(verts)} vertices")
        _draw(tri, px, py, depth, lit, val, W, H, zbuf, shade, vbuf)

    hit = np.isfinite(zbuf)
    lum = np.where(hit, ambient + (1.0 - ambient) * shade, 0.0)
    bg = np.asarray(background, dtype=float)
    if vbuf is not None and colormap is not None:
        base = np.tile(bg, (size, 1))
        if hit.any():
            base[hit] = np.asarray(colormap(vbuf[hit]), dtype=float).reshape(-1, 3)[:, :3]
    else:
        base = np.tile(np.asarray(color, dtype=float)[:3], (size, 1))
    rgb = np.where(hit[:, None], base * lum[:, None], bg[None, :])
    rgb = rgb.reshape(H, W, 3)
    cover = hit.reshape(H, W).astype(float)
    centre = zbuf.reshape(H, W)[ss // 2::ss, ss // 2::ss]
    if ss > 1:
        rgb = rgb.reshape(frame.height, ss, frame.width, ss, 3).mean(axis=(1, 3))
        cover = cover.reshape(frame.height, ss, frame.width, ss).mean(axis=(1, 3))
    return Picture(rgb=np.clip(rgb, 0.0, 1.0), coverage=cover,
                   depth=np.where(np.isfinite(centre), centre, np.nan))


def _draw(tri, px, py, depth, lit, val, W, H, zbuf, shade, vbuf) -> None:
    """Test every triangle on the pixel grid of its bounding box and keep the
    nearest fragment at each pixel (into ``zbuf``, ``shade`` and ``vbuf``)."""
    x = px[tri]
    y = py[tri]
    xmin = np.ceil(x.min(axis=1)).astype(np.int64)
    xmax = np.floor(x.max(axis=1)).astype(np.int64)
    ymin = np.ceil(y.min(axis=1)).astype(np.int64)
    ymax = np.floor(y.max(axis=1)).astype(np.int64)
    np.maximum(xmin, 0, out=xmin)
    np.maximum(ymin, 0, out=ymin)
    np.minimum(xmax, W - 1, out=xmax)
    np.minimum(ymax, H - 1, out=ymax)
    area2 = ((x[:, 1] - x[:, 0]) * (y[:, 2] - y[:, 0])
             - (x[:, 2] - x[:, 0]) * (y[:, 1] - y[:, 0]))
    # Off the picture, between pixel centers, or seen edge on: nothing to draw.
    live = (xmax >= xmin) & (ymax >= ymin) & (np.abs(area2) > 1e-12)
    if not live.any():
        return
    keep = np.flatnonzero(live)
    bw = np.searchsorted(_SIZES, xmax[keep] - xmin[keep] + 1)
    bh = np.searchsorted(_SIZES, ymax[keep] - ymin[keep] + 1)
    bw = np.minimum(bw, len(_SIZES) - 1)
    bh = np.minimum(bh, len(_SIZES) - 1)
    group = bw * len(_SIZES) + bh
    order = np.argsort(group, kind="stable")
    starts = np.flatnonzero(np.r_[True, np.diff(group[order]) != 0])
    ends = np.r_[starts[1:], len(order)]
    for first, last in zip(starts, ends, strict=True):
        members = keep[order[first:last]]
        gw = int(_SIZES[bw[order[first]]])
        gh = int(_SIZES[bh[order[first]]])
        cells = gw * gh
        per = max(1, BLOCK_PIXELS // cells)
        oy, ox = np.divmod(np.arange(cells, dtype=np.int64), gw)
        for at in range(0, len(members), per):
            f = members[at:at + per]
            gx = xmin[f, None] + ox[None, :]
            gy = ymin[f, None] + oy[None, :]
            x0, x1, x2 = x[f, 0, None], x[f, 1, None], x[f, 2, None]
            y0, y1, y2 = y[f, 0, None], y[f, 1, None], y[f, 2, None]
            denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            l0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / denom
            l1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / denom
            l2 = 1.0 - l0 - l1
            eps = -1e-9
            inside = ((l0 >= eps) & (l1 >= eps) & (l2 >= eps)
                      & (gx <= xmax[f, None]) & (gy <= ymax[f, None]))
            if not inside.any():
                continue
            row, col = np.nonzero(inside)
            a, b, c = l0[row, col], l1[row, col], l2[row, col]
            corners = tri[f[row]]
            d = a * depth[corners[:, 0]] + b * depth[corners[:, 1]] + c * depth[corners[:, 2]]
            pix = gy[row, col] * W + gx[row, col]
            # The nearest fragment of this block at each pixel, then against the buffer.
            pick = np.lexsort((-d, pix))
            sorted_pix = pix[pick]
            firsts = pick[np.r_[True, sorted_pix[1:] != sorted_pix[:-1]]]
            at_pix = pix[firsts]
            nearer = d[firsts] > zbuf[at_pix]
            if not nearer.any():
                continue
            won = firsts[nearer]
            where = pix[won]
            zbuf[where] = d[won]
            wa, wb, wc = a[won], b[won], c[won]
            wcorn = corners[won]
            shade[where] = (wa * lit[wcorn[:, 0]] + wb * lit[wcorn[:, 1]]
                            + wc * lit[wcorn[:, 2]])
            if vbuf is not None and val is not None:
                vbuf[where] = (wa * val[wcorn[:, 0]] + wb * val[wcorn[:, 1]]
                               + wc * val[wcorn[:, 2]])


def section_segments(vertices: np.ndarray, faces: np.ndarray, z: float) -> np.ndarray:
    """Where the plane at height ``z`` cuts the mesh: ``(n, 2, 2)`` segments in
    XY, one for each triangle that crosses it (a vertex on the plane counts as
    above it, so a triangle touching the plane at a corner adds nothing)."""
    v = np.asarray(vertices, dtype=float).reshape(-1, 3)
    tri = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if not len(tri):
        return np.zeros((0, 2, 2))
    above = v[:, 2] >= z
    side = above[tri]
    crossing = side.any(axis=1) & ~side.all(axis=1)
    t3 = tri[crossing]
    s3 = side[crossing]
    if not len(t3):
        return np.zeros((0, 2, 2))
    points = np.zeros((len(t3), 2, 2))
    filled = np.zeros(len(t3), dtype=np.int64)
    for i, j in ((0, 1), (1, 2), (2, 0)):
        cut = s3[:, i] != s3[:, j]
        pa, pb = v[t3[cut, i]], v[t3[cut, j]]
        t = (z - pa[:, 2]) / (pb[:, 2] - pa[:, 2])
        hit = pa[:, :2] + t[:, None] * (pb[:, :2] - pa[:, :2])
        rows = np.flatnonzero(cut)
        points[rows, filled[rows]] = hit
        filled[rows] += 1
    return points
