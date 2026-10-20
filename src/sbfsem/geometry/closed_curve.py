"""Volumetric renders from closed-curve annotations.

Ports ``util/renders/renderClosedCurveOutlines.m`` (the working implementation),
``util/renders/renderClosedCurve.m``, ``+sbfsem/+builtin/ClosedCurve.m`` and
``+sbfsem/+render/ClosedCurve.m``.

The pipeline is: spline the control points, rasterize each section's outline
minus its cut-outs onto a common grid, stack the sections into a volume, smooth,
and marching-cubes the result (see :mod:`sbfsem.geometry.volume`).

Deviations (see DEVIATIONS.md):

* The MATLAB code multiplied every outline by 100 to get integer pixel indices
  and then never divided that factor out, so renders came back undersized in XY
  by roughly ``100 * volumeScale(1)`` with the wrong XY-to-Z aspect ratio
  (F096).  Here the grid has an explicit ``pixel_size_um`` and the mesh comes
  back in microns.
* Sections were padded on the leading edge only, translating each one by a
  different amount (F086).  Every section is rasterized against one shared
  bounding box, so no padding is needed.
* MATLAB stacked one slice per *traced* section and spaced them one section
  apart, so a structure with skipped sections came out short in Z and slid
  off its own annotations (RPC1's Mueller cell c2628: 676 sections traced
  over a span of 754, 5.46 um short at the top).  Every slice sits at its own
  section number here; short skips are interpolated, long ones stay open.
  Viking's links say which outline carries on as which across a skip, and
  each linked pair is interpolated on its own, so a branch untraced for a few
  sections is whole even where the rest of the cell was traced there.
* MATLAB refused a structure with no closed curves.  A cell traced with
  discs has its discs stand in here (:func:`annotations_from_discs`): each
  disc's circle on its own section, plus the stretch of each link that
  passes through each section between its two discs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.ndimage import distance_transform_edt
from scipy.spatial import ConvexHull

from .rasterize import rasterize_rings
from .spline import close_spline

__all__ = ["BRIDGE_UM", "LINK_BRIDGE_UM", "MAX_SURFACE", "TILE", "ClosedCurveAnnotation",
           "CurveVolume", "annotations_from_discs", "annotations_from_geometries",
           "build_curve_volume", "curve_grid_voxels", "curve_voxel_counter", "decode_labels",
           "finest_fitting_pixel", "interpolate_masks", "label_code", "repair_leading_nan",
           "surface_fitting_pixel"]

#: Default raster resolution.  MATLAB used 0.01 um implicitly (round to 2
#: decimals, times 100); 0.05 um is finer than the 0.07-0.09 um section spacing
#: and keeps volumes an order of magnitude smaller.
DEFAULT_PIXEL_SIZE_UM = 0.05

#: Widest step between two traced sections still read as one body, in
#: microns.  Annotators skip sections: 82 % of the untraced runs in the 160
#: most-traced structures of the eight volumes are one or two sections, 93 %
#: are under a micron (surveyed 2026-09-18).  Those are interpolated.  The
#: long ones are where a mixed cell's discs take over, or where nothing was
#: annotated at all, and inventing a body across them would be a guess.
BRIDGE_UM = 1.0

#: Farthest a Viking link between two outlines is followed across skipped
#: sections, in microns.  A link is the annotator saying "this outline carries
#: on as that one", so it needs no guess -- this only guards against a stray
#: link across the volume.  The 1,597 section-skipping links of 18 cells in
#: the eight volumes all span 0.56 um or less (surveyed 2026-09-18).
LINK_BRIDGE_UM = 5.0

#: Most exposed voxel faces a raster may have (:meth:`CurveVolume.exposed_faces`).
#: Marching cubes draws 1.4 to 1.9 triangles for each (RPC1 c2628 1.4 to
#: 1.5, RC1 c410 1.5, Inferior c16137's thin processes 1.7 to 1.9), and a
#: build peaks at about 120 MB plus 350 bytes a triangle (sbfsem-measure,
#: 2026-10-15; RPC1 c2628 again 2026-10-16 from 0.05 to 0.025 um, 3.4 to
#: 10.1 million triangles: 1.4 to 3.4 GB): at most some 19 million
#: triangles and 6.8 GB here.  The page then receives them as about 35
#: bytes of JSON a triangle (c2628 at 0.025 um: 351 MB, a 0.6 GB heap in the
#: tab, drawn in a second).  The limit was 3 million (5.8 million triangles,
#: 2.2 GB) until 2026-10-16, when it was raised to 10 million at the user's
#: request: c2628's finest pixel was 0.044 um, and is now 0.021 um (0.025
#: um, the menu's fine choice, is 6.8 million).  The whole-box raster's 300
#: million voxels used to bound the mesh too; tiles do not.
MAX_SURFACE = 10_000_000

#: Points on a disc's circle where it stands in for a traced outline.  A
#: 5 um soma disc's polygon then strays 6 nm from the circle, under any pixel.
DISC_RING_POINTS = 64

#: What a labelled raster remembers of the annotation that drew a voxel: one
#: byte, the radius of the process it belongs to on a logarithmic scale (the
#: low seven bits, 1 for 0.02 um up, ten steps to an octave: 7 % apart, to
#: 127 at about 120 um) and, in the top bit, whether a traced outline drew it
#: (a disc or a link between discs did not).  0 is "nothing drew it".
LABEL_RADIUS_BASE_UM = 0.02
LABEL_STEPS_PER_OCTAVE = 10
LABEL_PATH_BIT = 0x80
#: Points :meth:`CurveVolume.label_at` looks up at once.
LABEL_CHUNK = 1_000_000


def label_code(radius_um: float, is_path: bool) -> int:
    """The byte :class:`CurveVolume` keeps for an annotation of this radius."""
    radius = max(float(radius_um), LABEL_RADIUS_BASE_UM)
    step = int(np.rint(LABEL_STEPS_PER_OCTAVE * np.log2(radius / LABEL_RADIUS_BASE_UM))) + 1
    return min(max(step, 1), 0x7F) | (LABEL_PATH_BIT if is_path else 0)


def decode_labels(codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(radius_um, is_path)`` of every label byte; radius 0 where nothing drew."""
    codes = np.asarray(codes, dtype=np.uint8)
    step = (codes & 0x7F).astype(float)
    radius = np.where(step > 0, LABEL_RADIUS_BASE_UM * 2.0 ** ((step - 1.0) / LABEL_STEPS_PER_OCTAVE), 0.0)
    return radius, (codes & LABEL_PATH_BIT) != 0


@dataclass
class ClosedCurveAnnotation:
    """One closed-curve annotation: an outline plus any cut-outs, in microns."""

    outline: np.ndarray
    cutouts: list[np.ndarray] = field(default_factory=list)
    z_section: int = 0
    z_um: float = 0.0
    parent_id: int | None = None
    location_id: int | None = None
    #: The outline is Viking's registered, already-evaluated curve (its
    #: ``VolumeShape``), not control points: use it as it is.  Splining it
    #: again would only multiply its points (about nine per control interval).
    registered: bool = False
    #: A traced outline (``True``) or a disc's circle, or a stretch of a link
    #: between discs (``False``), and the radius of the process it draws in
    #: microns (0: the outline's own, ``sqrt(area / pi)``).  Only read when a
    #: raster keeps labels, for the averaging of the surface.
    is_path: bool = True
    owner_um: float = 0.0

    def __post_init__(self) -> None:
        # A point Viking left NaN (it does, as the first control point) would
        # reach the bounds and the spline and fail the whole build: ConeOutline
        # and the cut-outs below were repaired, the outline itself was not.
        self.outline = repair_leading_nan(self.outline)
        self.cutouts = [repair_leading_nan(c) for c in self.cutouts]

    def equivalent_radius(self) -> float:
        """The radius of the process this annotation draws: ``owner_um`` when
        it was given, else that of the circle with the outline's area."""
        if self.owner_um > 0:
            return float(self.owner_um)
        x, y = self.outline[:, 0], self.outline[:, 1]
        if len(x) < 3:
            return 0.0
        area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
        return float(np.sqrt(area / np.pi))

    def label_code(self) -> int:
        return label_code(self.equivalent_radius(), self.is_path)

    @property
    def local_bounds(self) -> np.ndarray:
        """``[xmin, xmax, ymin, ymax]`` of the outline."""
        return np.array(
            [
                self.outline[:, 0].min(),
                self.outline[:, 0].max(),
                self.outline[:, 1].min(),
                self.outline[:, 1].max(),
            ]
        )

    def spline(self, n: int = 100) -> np.ndarray:
        """The outline, splined and closed (as it is when ``registered``)."""
        if self.registered:
            return self.outline
        return close_spline(self.outline, n)

    def rings(self, n: int = 100) -> list[np.ndarray]:
        """Splined outline first, then splined cut-outs."""
        out = [self.spline(n)]
        for cut in self.cutouts:
            cut = repair_leading_nan(cut)
            out.append(cut if self.registered else close_spline(cut, n))
        return out


def repair_leading_nan(points: np.ndarray) -> np.ndarray:
    """Viking occasionally emits a NaN first control point."""
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    if len(pts) and not np.isfinite(pts[0]).all():
        pts = pts.copy()
        pts[0] = pts[-1]
    return pts[np.isfinite(pts).all(axis=1)]


def annotations_from_geometries(geometries: pd.DataFrame) -> list[ClosedCurveAnnotation]:
    """Convert the ``geometries`` table into annotation objects."""
    out: list[ClosedCurveAnnotation] = []
    if geometries is None or len(geometries) == 0:
        return out
    for row in geometries.itertuples():
        curves = list(row.Curve) if row.Curve is not None else []
        if not curves:
            continue
        out.append(
            ClosedCurveAnnotation(
                outline=curves[0],
                cutouts=[np.asarray(c) for c in curves[1:]],
                z_section=int(row.Z),
                z_um=float(getattr(row, "Zum", 0.0)),
                parent_id=int(getattr(row, "ParentID", 0)),
                location_id=int(getattr(row, "ID", 0)),
                registered=bool(getattr(row, "Registered", False)),
            )
        )
    return out


def annotations_from_discs(
    nodes: pd.DataFrame,
    links=None,
    *,
    volume_scale=(7.5, 7.5, 90.0),
    link_bridge_um: float = LINK_BRIDGE_UM,
    points: int = DISC_RING_POINTS,
    average: float = 0.0,
    curve_um: float = 0.0,
) -> list[ClosedCurveAnnotation]:
    """A disc-traced cell's annotations as outlines for :func:`build_curve_volume`.

    Each disc is the circle it is in its own section plane (``Xum``, ``Yum``,
    ``Rum`` at section ``Z``), never a sphere.  A link between discs on
    different sections is the process passing through every section from one
    to the other: each of those sections also gets the part of the straight,
    linearly tapering run between the two circles that lies within one
    section of its own plane, flattened onto the plane -- the convex hull of
    the run's circles one section below and one above, never past either
    disc.  A vertical stack is unchanged; a process that moves sideways is a
    ribbon two sections thick, which the volume's 3x3x3 smoothing keeps,
    where anything one section thick is smoothed away.

    Inferior c16137 (1,933 discs; median step between linked discs 0.22 um,
    90th percentile 0.6 um; median radius 0.27 um) at 0.183 um, the finest
    raster its 128 x 176 um box fitted as one array: circles alone left the
    annotations 0.49 um unreached on average (95th percentile 0.79 um) in
    133 bodies; runs half a section either way, 0.17 um in 113; one section,
    0.08 um in 46.  Most of those bodies were processes under 1.5 pixels in
    radius that the smoothing removes: in tiles (:class:`CurveVolume`) it
    fits 0.1 um, one body, 0.063 um unreached (95th percentile 0.136 um).

    A link within one section adds nothing: it says the two discs are joined,
    not that the cell is that wide between them.  A link across skipped
    sections longer than ``link_bridge_um`` in Z is not followed, as for
    traced outlines; one to the next section always is.  Rows
    that are not discs (``TypeCode`` 1) or have no finite positive radius are
    left out.  ``links`` are ``(location_id, location_id)`` pairs.

    ``average`` (0: as traced) first averages each disc with its neighbors
    along its chain of links, over a window whose width is that multiple of
    the disc's own radius (:func:`~sbfsem.geometry.curve_average.average_disc_nodes`):
    a 5 um soma disc is averaged over microns of Z, a 0.2 um dendrite disc
    over a section or two.

    ``curve_um`` (0: every run straight) curves the runs of links whose disc
    centers are at least that far apart (in 3D, Z included): each follows a
    smooth curve through the chain's disc centers instead of the straight
    chord, Z still rising from one disc to the other, within the bounds
    :mod:`~sbfsem.geometry.curve_links`
    sets (straight at a chain's end, a branch, a hairpin, a vertical stack;
    no bend tighter than the process's radius; within a quarter of the chord
    of it).  A link it leaves straight is drawn exactly as without it.
    """
    if nodes is None or not len(nodes):
        return []
    if "TypeCode" in nodes.columns:
        nodes = nodes[nodes["TypeCode"] == 1]
    xy = nodes[["Xum", "Yum"]].to_numpy(dtype=float)
    radius = nodes["Rum"].to_numpy(dtype=float)
    ok = np.isfinite(xy).all(axis=1) & np.isfinite(radius) & (radius > 0)
    nodes, xy, radius = nodes[ok], xy[ok], radius[ok]
    if average > 0 and links:
        from .curve_average import average_disc_nodes

        nodes = average_disc_nodes(nodes, links, average)
        xy = nodes[["Xum", "Yum"]].to_numpy(dtype=float)
        radius = nodes["Rum"].to_numpy(dtype=float)
    ids = nodes["ID"].astype(int).to_numpy()
    sections = nodes["Z"].astype(int).to_numpy()
    z_um = nodes["Zum"].to_numpy(dtype=float)
    parents = (nodes["ParentID"].fillna(0).astype(int).to_numpy()
               if "ParentID" in nodes.columns else np.zeros(len(ids), dtype=int))
    angle = np.linspace(0.0, 2.0 * np.pi, int(points), endpoint=False)
    unit = np.column_stack([np.cos(angle), np.sin(angle)])

    out = [
        ClosedCurveAnnotation(
            outline=xy[k] + radius[k] * unit,
            z_section=int(sections[k]),
            z_um=float(z_um[k]),
            parent_id=int(parents[k]),
            location_id=int(ids[k]),
            # Already a polygon; splining it again would only add points.
            registered=True,
            is_path=False,
            owner_um=float(radius[k]),
        )
        for k in range(len(ids))
    ]
    if not links:
        return out

    dz = float(volume_scale[2]) / 1e3
    row = {int(v): k for k, v in enumerate(ids)}
    seen: set[tuple[int, int]] = set()
    drawn: list[tuple[int, int]] = []
    for a, b in links:
        pair = (min(int(a), int(b)), max(int(a), int(b)))
        if pair in seen or pair[0] not in row or pair[1] not in row:
            continue
        seen.add(pair)
        lo, hi = sorted((row[pair[0]], row[pair[1]]), key=lambda k: sections[k])
        span = int(sections[hi] - sections[lo])
        # A link to the next section skips nothing: ``link_bridge_um`` is how far
        # one is followed *across skipped sections*, as for traced outlines, so
        # even 0 draws it (and a disc-traced cell does not fall into beads).
        if span == 0 or (span > 1 and span * dz > link_bridge_um + 1e-9):
            continue
        drawn.append((lo, hi))
    curves = {}
    if curve_um > 0 and drawn:
        from .curve_links import link_curves

        curves = link_curves(xy, z_um, radius, [(row[a], row[b]) for a, b in seen],
                             min_step_um=float(curve_um), drawn=drawn)
    for lo, hi in drawn:
        span = int(sections[hi] - sections[lo])
        if (lo, hi) in curves:
            out.extend(_curved_run(curves[(lo, hi)], sections, z_um, parents, radius, unit))
            continue
        step, grow = xy[hi] - xy[lo], radius[hi] - radius[lo]
        for s in range(int(sections[lo]), int(sections[hi]) + 1):
            faces = (max(0.0, (s - 1 - sections[lo]) / span),
                     min(1.0, (s + 1 - sections[lo]) / span))
            ring = np.vstack([xy[lo] + t * step + (radius[lo] + t * grow) * unit
                              for t in faces])
            t = (s - sections[lo]) / span
            out.append(ClosedCurveAnnotation(
                outline=ring[ConvexHull(ring).vertices],
                z_section=s,
                z_um=float(z_um[lo] + t * (z_um[hi] - z_um[lo])),
                parent_id=int(parents[lo]),
                registered=True,
                is_path=False,
                owner_um=float(max(radius[lo], radius[hi])),
            ))
    return out


def _curved_run(curve, sections, z_um, parents, radius, unit) -> list[ClosedCurveAnnotation]:
    """A curved link's run (:func:`annotations_from_discs` with ``curve_um``):
    each section it crosses gets the part of the curve within one section of
    its plane, as the straight run does, drawn as the union of short steps
    along it (:func:`~sbfsem.geometry.curve_links.curve_outline`)."""
    from .curve_links import curve_outline

    lo, hi = curve.lo, curve.hi
    span = int(sections[hi] - sections[lo])
    drawn: dict[tuple[float, float], list[np.ndarray]] = {}
    out = []
    for s in range(int(sections[lo]), int(sections[hi]) + 1):
        faces = (max(0.0, (s - 1 - sections[lo]) / span), min(1.0, (s + 1 - sections[lo]) / span))
        if faces not in drawn:
            # A link one section long draws the same whole run on both its sections.
            drawn[faces] = curve_outline(curve, *faces, float(radius[lo]), float(radius[hi]), unit)
        t = (s - sections[lo]) / span
        for outline in drawn[faces]:
            out.append(ClosedCurveAnnotation(
                outline=outline,
                z_section=s,
                z_um=float(z_um[lo] + t * (z_um[hi] - z_um[lo])),
                parent_id=int(parents[lo]),
                registered=True,
                is_path=False,
                owner_um=float(max(radius[lo], radius[hi])),
            ))
    return out


#: Edge of the square tiles the outline raster is kept in, in pixels.  Only
#: the tiles some outline's window reaches are allocated, so a wide cell
#: costs the area its outlines cover rather than its whole bounding box.
TILE = 32


@dataclass
class CurveVolume:
    """A rasterized stack of closed-curve sections plus its world placement.

    The occupancy is kept in ``TILE`` x ``TILE`` squares of each section,
    allocated only where an outline's window reaches (which
    :func:`curve_grid_voxels` counts before anything is rasterized).  A
    wide-field cell's box is nearly all empty: Inferior c16137's discs, 128
    x 176 um over 436 sections, are 1.0 billion voxels as one array at 0.1
    um and 3.8 million in tiles.
    """

    #: ``(rows, cols, slices)`` of the grid the tiles cut up.
    shape: tuple[int, int, int]
    #: Each allocated tile's ``(slice * tile_rows + tile_row) * tile_cols +
    #: tile_col``, ascending.
    keys: np.ndarray
    #: ``(len(keys), tile, tile)`` boolean occupancy, one square per key.
    tiles: np.ndarray
    #: World-space origin of voxel ``(0, 0, 0)``, in microns.
    origin_um: np.ndarray
    #: Voxel size ``(dx, dy, dz)`` in microns.
    spacing_um: np.ndarray
    #: Section numbers, ascending, one per Z slice.  Consecutive within a
    #: run; a jump marks a stretch too long to bridge, left open.
    sections: np.ndarray
    #: Per slice: traced (``True``) or interpolated across skipped sections.
    traced: np.ndarray | None = None
    #: With ``build_curve_volume(keep_labels=True)``: one byte for each voxel of
    #: ``tiles`` (the same layout), the largest :func:`label_code` of any
    #: annotation that drew it.  What the averaging of the surface asks about
    #: each vertex: how thick the process it is on, and whether a disc or a
    #: traced outline drew it.  ``None`` otherwise, which is most builds.
    labels: np.ndarray | None = None
    #: ``(in the sections' planes, between sections)``: the raster's exposed
    #: voxel faces, as :func:`build_curve_volume` counted them for its limit
    #: (:meth:`exposed_faces`).  ``None`` for a volume made any other way.
    #: What a build leaves for the next estimate of the same cell
    #: (:mod:`sbfsem.service.estimate`): faces in the plane go as 1 / pixel,
    #: faces between sections as 1 / pixel^2.
    exposed: tuple[int, int] | None = None

    @classmethod
    def from_dense(cls, volume: np.ndarray, origin_um, spacing_um, sections,
                   traced: np.ndarray | None = None, *, tile: int | None = None) -> CurveVolume:
        """A ``(rows, cols, slices)`` boolean array in tiles: those with
        anything in them."""
        volume = np.asarray(volume, dtype=bool)
        t = int(tile or TILE)
        rows, cols, n = volume.shape
        n_tr, n_tc = -(-rows // t), -(-cols // t)
        padded = np.zeros((n_tr * t, n_tc * t, n), dtype=bool)
        padded[:rows, :cols] = volume
        # (tile_row, row, tile_col, col, slice) -> (slice, tile_row, tile_col, row, col)
        squares = padded.reshape(n_tr, t, n_tc, t, n).transpose(4, 0, 2, 1, 3).reshape(-1, t, t)
        keep = np.flatnonzero(squares.any(axis=(1, 2)))
        return cls((rows, cols, n), keep.astype(np.int64), squares[keep].copy(),
                   np.asarray(origin_um, dtype=float), np.asarray(spacing_um, dtype=float),
                   np.asarray(sections, dtype=np.int64), traced)

    @property
    def tile(self) -> int:
        return int(self.tiles.shape[1])

    @property
    def tile_grid(self) -> tuple[int, int]:
        """Tiles per section, down and across."""
        return -(-self.shape[0] // self.tile), -(-self.shape[1] // self.tile)

    @property
    def allocated_voxels(self) -> int:
        """Voxels held in memory: the allocated tiles, not the box."""
        return int(self.tiles.size)

    @property
    def volume(self) -> np.ndarray:
        """The whole ``(rows, cols, slices)`` box as one boolean array.

        Built anew on every access, for tests and small structures: the box
        is what the tiles are there to avoid."""
        return self.dense()

    def dense(self) -> np.ndarray:
        """The occupancy as one ``(rows, cols, slices)`` boolean array."""
        rows, cols, _ = self.shape
        t = self.tile
        out = np.zeros(self.shape, dtype=bool)
        n_tr, n_tc = self.tile_grid
        k, rest = np.divmod(self.keys, n_tr * n_tc)
        tr, tc = np.divmod(rest, n_tc)
        for i in np.flatnonzero(self.tiles.any(axis=(1, 2))):
            r0, c0 = int(tr[i]) * t, int(tc[i]) * t
            r1, c1 = min(r0 + t, rows), min(c0 + t, cols)
            out[r0:r1, c0:c1, k[i]] = self.tiles[i, :r1 - r0, :c1 - c0]
        return out

    def gather(self, rows, cols, slices) -> np.ndarray:
        """The ``(len(rows), len(cols), len(slices))`` block of occupancy at
        every combination of three index arrays.  Indices may repeat (a
        clamped edge); each must lie on the grid."""
        rows, cols, slices = (np.asarray(v, dtype=np.int64) for v in (rows, cols, slices))
        if not len(self.keys):
            return np.zeros((len(rows), len(cols), len(slices)), dtype=bool)
        t = self.tile
        n_tr, n_tc = self.tile_grid
        tr, ir = np.divmod(rows, t)
        tc, ic = np.divmod(cols, t)
        utr, tr_at = np.unique(tr, return_inverse=True)
        utc, tc_at = np.unique(tc, return_inverse=True)
        uk, k_at = np.unique(slices, return_inverse=True)
        found = self._find((uk[:, None, None] * n_tr + utr[None, :, None]) * n_tc
                           + utc[None, None, :])
        which = found[k_at[None, None, :], tr_at[:, None, None], tc_at[None, :, None]]
        out = self.tiles[np.maximum(which, 0), ir[:, None, None], ic[None, :, None]]
        out &= which >= 0
        return out

    def gather_labels(self, rows, cols, slices) -> np.ndarray:
        """:meth:`gather` for the label bytes (0 where no tile is allocated)."""
        if self.labels is None:
            raise ValueError("this raster kept no labels (build it with keep_labels=True)")
        rows, cols, slices = (np.asarray(v, dtype=np.int64) for v in (rows, cols, slices))
        if not len(self.keys):
            return np.zeros((len(rows), len(cols), len(slices)), dtype=np.uint8)
        t = self.tile
        n_tr, n_tc = self.tile_grid
        tr, ir = np.divmod(rows, t)
        tc, ic = np.divmod(cols, t)
        utr, tr_at = np.unique(tr, return_inverse=True)
        utc, tc_at = np.unique(tc, return_inverse=True)
        uk, k_at = np.unique(slices, return_inverse=True)
        found = self._find((uk[:, None, None] * n_tr + utr[None, :, None]) * n_tc
                           + utc[None, None, :])
        which = found[k_at[None, None, :], tr_at[:, None, None], tc_at[None, :, None]]
        out = self.labels[np.maximum(which, 0), ir[:, None, None], ic[None, :, None]]
        out[which < 0] = 0
        return out

    def label_at(self, points_um) -> np.ndarray:
        """The label byte at each of ``points_um`` (``(N, 3)``): the largest
        of the eight voxels round it, so a mesh vertex on the surface (between
        an occupied voxel and an empty one) takes the annotation that drew the
        occupied side.  A point none of those eight was drawn looks a voxel
        further each way (the cap of a run: its surface stands out of the last
        section drawn); 0 where there is still none, which is anywhere but
        beside the raster."""
        if self.labels is None:
            raise ValueError("this raster kept no labels (build it with keep_labels=True)")
        pts = np.asarray(points_um, dtype=float).reshape(-1, 3)
        out = np.zeros(len(pts), dtype=np.uint8)
        if not len(pts) or not len(self.keys):
            return out
        if len(pts) > LABEL_CHUNK:
            # A surface of millions of vertices a chunk at a time: the lookups
            # hold a dozen arrays of the chunk's length.
            for start in range(0, len(pts), LABEL_CHUNK):
                out[start:start + LABEL_CHUNK] = self.label_at(pts[start:start + LABEL_CHUNK])
            return out
        sx, sy, sz = (float(v) for v in self.spacing_um)
        n_rows, n_cols, _ = self.shape
        col0 = np.floor((pts[:, 0] - self.origin_um[0]) / sx).astype(np.int64)
        row0 = np.floor((pts[:, 1] - self.origin_um[1]) / sy).astype(np.int64)
        sec0 = np.floor(pts[:, 2] / sz).astype(np.int64)
        last = len(self.sections) - 1
        t = self.tile
        n_tr, n_tc = self.tile_grid
        labels = self.labels

        def look(which: np.ndarray, offsets) -> None:
            """The largest label at each offset (section, row, column) from the
            floor voxel of the points ``which``, into ``out``."""
            for ds, dr, dc in offsets:
                sec = sec0[which] + ds
                k = np.minimum(np.searchsorted(self.sections, sec), last)
                r, c = row0[which] + dr, col0[which] + dc
                m = (self.sections[k] == sec) & (r >= 0) & (r < n_rows) & (c >= 0) & (c < n_cols)
                if not m.any():
                    continue
                tile = self._find((k[m] * n_tr + r[m] // t) * n_tc + c[m] // t)
                hit = tile >= 0
                if not hit.any():
                    continue
                at = which[np.flatnonzero(m)[hit]]
                out[at] = np.maximum(out[at], labels[tile[hit], r[m][hit] % t, c[m][hit] % t])

        everyone = np.arange(len(pts))
        look(everyone, [(ds, dr, dc) for ds in (0, 1) for dr in (0, 1) for dc in (0, 1)])
        missed = np.flatnonzero(out == 0)
        if len(missed):
            look(missed, [(ds, dr, dc) for ds in (-1, 0, 1, 2) for dr in (-1, 0, 1, 2)
                          for dc in (-1, 0, 1, 2)])
        return out

    def exposed_faces(self) -> tuple[int, int]:
        """Voxel faces between an occupied voxel and an empty one: in the
        sections' planes, and between consecutive sections of a run (a run's
        ends, and the grid's edges, border on empty).  The count of the whole
        box, whatever the tiles.

        What the mesh will cost, before it is made: marching cubes draws 1.4
        to 1.9 triangles per exposed face of this raster (RPC1 c2628, RC1
        c410 and Inferior c16137 at 0.05 to 0.18 um)."""
        tiles, chunk = self.tiles, 4096
        n_tr, n_tc = self.tile_grid
        k, cell = np.divmod(self.keys, n_tr * n_tc)
        tr, tc = np.divmod(cell, n_tc)

        def against(step: int, has: np.ndarray, near, far) -> int:
            """Faces between each tile's ``near`` side and the ``far`` side of
            its neighbour ``step`` keys on; a side with no neighbour borders
            on empty."""
            j = np.where(has, self._find(self.keys + step), -1)
            paired = np.flatnonzero(j >= 0)
            met = np.zeros(len(self.keys), dtype=bool)
            met[j[paired]] = True
            count = (int(np.count_nonzero(near(np.flatnonzero(j < 0))))
                     + int(np.count_nonzero(far(np.flatnonzero(~met)))))
            for i in range(0, len(paired), chunk):
                mine = paired[i:i + chunk]
                count += int(np.count_nonzero(near(mine) != far(j[mine])))
            return count

        plane = 0
        for i in range(0, len(self.keys), chunk):
            part = tiles[i:i + chunk]
            plane += int(np.count_nonzero(part[:, 1:, :] != part[:, :-1, :]))
            plane += int(np.count_nonzero(part[:, :, 1:] != part[:, :, :-1]))
        plane += against(n_tc, tr + 1 < n_tr, lambda i: tiles[i, -1, :], lambda i: tiles[i, 0, :])
        plane += against(1, tc + 1 < n_tc, lambda i: tiles[i, :, -1], lambda i: tiles[i, :, 0])
        # The tile one section up, where that section continues the run.
        continues = np.append(np.diff(self.sections) == 1, False)
        across = against(n_tr * n_tc, continues[k], lambda i: tiles[i], lambda i: tiles[i])
        return plane, across

    def _find(self, keys) -> np.ndarray:
        """Each key's tile index, or -1 where that tile is not allocated."""
        keys = np.asarray(keys, dtype=np.int64)
        if not len(self.keys):
            return np.full(keys.shape, -1, dtype=np.int64)
        at = np.minimum(np.searchsorted(self.keys, keys), len(self.keys) - 1)
        return np.where(self.keys[at] == keys, at, -1)

    def _at(self, rows: np.ndarray, cols: np.ndarray, slices: np.ndarray) -> np.ndarray:
        """Occupancy at each ``(row, col, slice)`` (equal-length arrays on the grid)."""
        t = self.tile
        n_tr, n_tc = self.tile_grid
        tile = self._find((slices * n_tr + rows // t) * n_tc + cols // t)
        out = np.zeros(len(tile), dtype=bool)
        hit = tile >= 0
        out[hit] = self.tiles[tile[hit], rows[hit] % t, cols[hit] % t]
        return out

    def _paint(self, k: int, r0: int, c0: int, mask: np.ndarray, code: int = 0) -> None:
        """OR ``mask`` into slice ``k`` with its corner at ``(r0, c0)``.  A
        pixel set where no tile was allocated means the plan was wrong.  With
        labels kept, the pixels also take ``code`` if it is larger than what
        drew them before."""
        t = self.tile
        n_tr, n_tc = self.tile_grid
        h, w = mask.shape
        for tr in range(r0 // t, (r0 + h - 1) // t + 1):
            ra, rb = max(r0, tr * t), min(r0 + h, tr * t + t)
            for tc in range(c0 // t, (c0 + w - 1) // t + 1):
                ca, cb = max(c0, tc * t), min(c0 + w, tc * t + t)
                part = mask[ra - r0:rb - r0, ca - c0:cb - c0]
                i = int(self._find((k * n_tr + tr) * n_tc + tc))
                if i >= 0:
                    self.tiles[i, ra - tr * t:rb - tr * t, ca - tc * t:cb - tc * t] |= part
                    if code and self.labels is not None and part.any():
                        mine = self.labels[i, ra - tr * t:rb - tr * t, ca - tc * t:cb - tc * t]
                        np.maximum(mine, np.where(part, np.uint8(code), np.uint8(0)), out=mine)
                elif part.any():
                    raise RuntimeError(f"slice {k}: outline pixels in unallocated tile "
                                       f"({tr}, {tc})")

    def _allocate(self, windows) -> None:
        """Allocate, empty, the tiles under every ``(slice, row0, row1, col0,
        col1)`` window (inclusive) that are not yet: for drawing after the
        plan (:func:`~sbfsem.geometry.curve_ties.tie_bodies`).  All at once,
        the tiles copied once into an array of the new size: one allocation a
        window, each a concatenation and a reordering of every tile, left
        RPC1 c2628 at 0.025 um (111 MB of tiles, 330 ties) resident at 4.4 GB
        more than it began (2026-10-16)."""
        t = self.tile
        n_tr, n_tc = self.tile_grid
        wanted = []
        for k, r0, r1, c0, c1 in windows:
            rows = np.arange(r0 // t, r1 // t + 1, dtype=np.int64)
            cols = np.arange(c0 // t, c1 // t + 1, dtype=np.int64)
            wanted.append(((k * n_tr + rows[:, None]) * n_tc + cols[None, :]).ravel())
        if not wanted:
            return
        keys = np.unique(np.concatenate(wanted))
        new = keys[self._find(keys) < 0]
        if not len(new):
            return
        keys = np.concatenate([self.keys, new])
        order = np.argsort(keys, kind="stable")
        place = np.empty(len(keys), dtype=np.int64)
        place[order] = np.arange(len(keys))
        old = place[:len(self.keys)]
        tiles = np.zeros((len(keys), t, t), dtype=bool)
        tiles[old] = self.tiles
        self.tiles = tiles
        if self.labels is not None:
            labels = np.zeros((len(keys), t, t), dtype=np.uint8)
            labels[old] = self.labels
            self.labels = labels
        self.keys = keys[order]

    def _window(self, k: int, r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
        """Slice ``k`` from row ``r0`` to ``r1`` and column ``c0`` to ``c1``, inclusive."""
        return self.gather(np.arange(r0, r1 + 1), np.arange(c0, c1 + 1), [k])[:, :, 0]

    def runs(self) -> list[tuple[int, int]]:
        """``(start, stop)`` slice ranges of consecutive section numbers."""
        if not len(self.sections):
            return []
        cuts = np.flatnonzero(np.diff(self.sections) != 1) + 1
        edges = [0, *cuts.tolist(), len(self.sections)]
        return list(zip(edges[:-1], edges[1:], strict=True))

    def voxel_to_um(self, ijk: np.ndarray) -> np.ndarray:
        """Map ``(row, col, slice)`` coordinates to microns ``(x, y, z)``.

        A slice's height is its section number, not its index: the stack
        skips whatever was left open."""
        ijk = np.asarray(ijk, dtype=float).reshape(-1, 3)
        section = np.interp(ijk[:, 2], np.arange(len(self.sections)),
                            self.sections.astype(float))
        return np.column_stack([
            ijk[:, 1] * self.spacing_um[0] + self.origin_um[0],
            ijk[:, 0] * self.spacing_um[1] + self.origin_um[1],
            section * self.spacing_um[2],
        ])

    def contains(self, points_um, *, tolerance_um: float = 0.0) -> np.ndarray:
        """Which of ``points_um`` (``(N, 3)``) lie in an occupied voxel, or
        within ``tolerance_um`` of one along each axis.

        Sections are looked up by number, so a gap in the traced run is a
        gap here too rather than the next section's outline.  Vectorized
        over the points: 200,000 vertices take a fifth of a second.
        """
        pts = np.asarray(points_um, dtype=float).reshape(-1, 3)
        out = np.zeros(len(pts), dtype=bool)
        if not self.allocated_voxels or not len(pts):
            return out
        sx, sy, sz = (float(v) for v in self.spacing_um)
        n_rows, n_cols, _ = self.shape
        cols = np.round((pts[:, 0] - self.origin_um[0]) / sx).astype(int)
        rows = np.round((pts[:, 1] - self.origin_um[1]) / sy).astype(int)
        secs = np.round(pts[:, 2] / sz).astype(int)
        tol_c = int(np.ceil(tolerance_um / sx))
        tol_r = int(np.ceil(tolerance_um / sy))
        tol_s = int(np.ceil(tolerance_um / sz))
        last = len(self.sections) - 1
        for ds in range(-tol_s, tol_s + 1):
            sec = secs + ds
            k = np.minimum(np.searchsorted(self.sections, sec), last)
            on_slice = self.sections[k] == sec
            if not on_slice.any():
                continue
            for dr in range(-tol_r, tol_r + 1):
                r = rows + dr
                for dc in range(-tol_c, tol_c + 1):
                    c = cols + dc
                    m = on_slice & ~out & (r >= 0) & (r < n_rows) & (c >= 0) & (c < n_cols)
                    if m.any():
                        out[m] = self._at(r[m], c[m], k[m])
        return out

    def touches(self, points_um, *, tolerance_um: float = 0.5) -> bool:
        """Whether any of ``points_um`` lies in, or within ``tolerance_um``
        of, the traced volume -- a mesh piece's vertices, to ask whether the
        piece is already part of the traced body."""
        return bool(self.contains(points_um, tolerance_um=tolerance_um).any())


@dataclass(frozen=True)
class _CurveGrid:
    xmin: float
    ymin: float
    rows: int
    cols: int
    bridges: list
    spans: list
    sections: np.ndarray
    traced: list


def _curve_grid(annotations, *, volume_scale, pixel_size_um: float, margin_um: float,
                bridge_um: float, links, link_bridge_um: float) -> _CurveGrid:
    """The grid :func:`build_curve_volume` rasterizes on: the outlines' own
    box in x and y, and one layer per traced or filled section."""
    bounds = np.vstack([a.local_bounds for a in annotations])
    xmin = float(bounds[:, 0].min()) - margin_um
    xmax = float(bounds[:, 1].max()) + margin_um
    ymin = float(bounds[:, 2].min()) - margin_um
    ymax = float(bounds[:, 3].max()) + margin_um

    cols = max(2, int(np.ceil((xmax - xmin) / pixel_size_um)) + 1)
    rows = max(2, int(np.ceil((ymax - ymin) / pixel_size_um)) + 1)

    dz = float(volume_scale[2]) / 1e3
    traced = sorted({int(a.z_section) for a in annotations})
    bridges = [
        (lo, hi) for lo, hi in zip(traced[:-1], traced[1:], strict=True)
        if hi - lo > 1 and (hi - lo) * dz <= bridge_um + 1e-9
    ]
    filled = {z for lo, hi in bridges for z in range(lo + 1, hi)}
    spans = _linked_spans(annotations, links, link_bridge_um / dz)
    filled.update(z for below, above in spans
                  for z in range(below.z_section + 1, above.z_section))
    sections = np.array(sorted(filled.union(traced)), dtype=np.int64)
    return _CurveGrid(xmin, ymin, rows, cols, bridges, spans, sections, traced)


@dataclass(frozen=True)
class _CurvePlan:
    """Where :func:`build_curve_volume` draws each outline, and the tiles it
    allocates for them."""

    grid: _CurveGrid
    #: Per annotation, the pixel window its rings are rasterized on
    #: (:func:`_window`); per linked span, the window both outlines share.
    windows: list
    span_windows: list
    #: Allocated voxels: tiles times ``tile`` squared.
    voxels: int
    #: The tile keys (as :attr:`CurveVolume.keys`), or ``None`` once the
    #: count passed the ``keep`` it was planned with.
    keys: np.ndarray | None


def _ring_bounds(rings_um) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per annotation: the low and high ``(x, y)`` of all its rings, and
    whether every point is finite (else :func:`_window` gives up on it)."""
    n = len(rings_um)
    lo = np.full((n, 2), np.nan)
    hi = np.full((n, 2), np.nan)
    finite = np.zeros(n, dtype=bool)
    for i, rings in enumerate(rings_um):
        points = np.vstack(rings)
        if len(points) and np.isfinite(points).all():
            lo[i], hi[i], finite[i] = points.min(axis=0), points.max(axis=0), True
    return lo, hi, finite


def _windows(lo, hi, finite, grid: _CurveGrid, pixel_size_um: float) -> list:
    """:func:`_window` of each annotation, from its bounds in microns.

    Pixel coordinates are ``(x - xmin) / pixel``, which rounds monotonically,
    so the bounds' pixels are the extreme pixels of the rings themselves."""
    origin = np.array([grid.xmin, grid.ymin])
    low = np.floor((lo - origin) / pixel_size_um) - 1
    high = np.ceil((hi - origin) / pixel_size_um) + 1
    out: list = []
    for i in range(len(lo)):
        if not finite[i]:
            out.append(None)
            continue
        c0, r0 = max(int(low[i, 0]), 0), max(int(low[i, 1]), 0)
        c1, r1 = min(int(high[i, 0]), grid.cols - 1), min(int(high[i, 1]), grid.rows - 1)
        out.append(() if c1 < c0 or r1 < r0 else (r0, r1, c0, c1))
    return out


def _curve_plan(annotations, bounds, *, volume_scale, pixel_size_um: float, margin_um: float,
                bridge_um: float, links, link_bridge_um: float, tile: int = TILE,
                keep: int | None = None) -> _CurvePlan:
    """The tiles a build at ``pixel_size_um`` allocates, counted section by
    section without rasterizing.

    A slice gets the tiles under each of its outlines' windows (the whole
    section for an outline with a point that is not finite); a slice filled
    between two traced ones gets both of theirs, since shape-based
    interpolation never leaves the union of its two masks; and each slice a
    link crosses gets the tiles under that pair's shared window.  Keys are
    kept while the count is at most ``keep`` tiles (all of them by default).
    """
    grid = _curve_grid(annotations, volume_scale=volume_scale, pixel_size_um=pixel_size_um,
                       margin_um=margin_um, bridge_um=bridge_um, links=links,
                       link_bridge_um=link_bridge_um)
    lo, hi, finite = bounds
    windows = _windows(lo, hi, finite, grid, pixel_size_um)
    n_tr, n_tc = -(-grid.rows // tile), -(-grid.cols // tile)
    slot = {int(z): i for i, z in enumerate(grid.sections)}

    def rect(window):
        if window is None:
            return 0, n_tr - 1, 0, n_tc - 1
        r0, r1, c0, c1 = window
        return r0 // tile, r1 // tile, c0 // tile, c1 // tile

    drawn: dict[int, list] = {}
    for annotation, window in zip(annotations, windows, strict=True):
        if window != ():
            drawn.setdefault(slot[int(annotation.z_section)], []).append(rect(window))
    filled: dict[int, list] = {}
    for lo_z, hi_z in grid.bridges:
        both = drawn.get(slot[lo_z], []) + drawn.get(slot[hi_z], [])
        for z in range(lo_z + 1, hi_z):
            filled.setdefault(slot[z], []).extend(both)
    index = {id(a): i for i, a in enumerate(annotations)}
    span_windows = []
    for below, above in grid.spans:
        # The window of both outlines' rings together, as the build draws them.
        i, j = index[id(below)], index[id(above)]
        window = _windows(np.fmin(lo[i], lo[j])[None], np.fmax(hi[i], hi[j])[None],
                          np.array([finite[i] and finite[j]]), grid, pixel_size_um)[0]
        span_windows.append(window)
        if window:
            for z in range(below.z_section + 1, above.z_section):
                filled.setdefault(slot[z], []).append(rect(window))

    count = 0
    keys: list[np.ndarray] | None = []
    for k in sorted(drawn.keys() | filled.keys()):
        occupied = np.zeros((n_tr, n_tc), dtype=bool)
        for tr0, tr1, tc0, tc1 in drawn.get(k, []) + filled.get(k, []):
            occupied[tr0:tr1 + 1, tc0:tc1 + 1] = True
        flat = np.flatnonzero(occupied)
        count += len(flat)
        if keys is not None:
            if keep is not None and count > keep:
                keys = None
            else:
                keys.append(k * n_tr * n_tc + flat)
    planned = None if keys is None else (
        np.concatenate(keys).astype(np.int64) if keys else np.zeros(0, dtype=np.int64))
    return _CurvePlan(grid, windows, span_windows, count * tile * tile, planned)


def curve_grid_voxels(
    annotations,
    *,
    volume_scale=(7.5, 7.5, 90.0),
    pixel_size_um: float = DEFAULT_PIXEL_SIZE_UM,
    spline_points: int = 20,
    margin_um: float = 0.5,
    bridge_um: float = BRIDGE_UM,
    links=None,
    link_bridge_um: float = LINK_BRIDGE_UM,
) -> int:
    """How many voxels :func:`build_curve_volume` would allocate, without
    rasterizing: what a memory estimate should be made of.  0 without
    outlines."""
    return curve_voxel_counter(
        annotations, volume_scale=volume_scale, spline_points=spline_points,
        margin_um=margin_um, bridge_um=bridge_um, links=links, link_bridge_um=link_bridge_um,
    )(pixel_size_um)


def curve_voxel_counter(
    annotations,
    *,
    volume_scale=(7.5, 7.5, 90.0),
    spline_points: int = 20,
    margin_um: float = 0.5,
    bridge_um: float = BRIDGE_UM,
    links=None,
    link_bridge_um: float = LINK_BRIDGE_UM,
) -> Callable[[float], int]:
    """:func:`curve_grid_voxels` at any pixel, the outlines' rings and bounds
    worked out once: what an estimate that tries several pixels on one cell
    calls (RPC1 c2628: 0.27 s for the bounds, then 0.25 to 0.45 s a pixel)."""
    annotations = list(annotations)
    if not annotations:
        return lambda _pixel: 0
    bounds = _ring_bounds([a.rings(spline_points) for a in annotations])

    def voxels_at(pixel_size_um: float) -> int:
        return _curve_plan(annotations, bounds, volume_scale=volume_scale,
                           pixel_size_um=pixel_size_um, margin_um=margin_um,
                           bridge_um=bridge_um, links=links, link_bridge_um=link_bridge_um,
                           tile=TILE, keep=0).voxels

    return voxels_at


def finest_fitting_pixel(voxels_at: Callable[[float], int], pixel_size_um: float,
                         max_voxels: int) -> float:
    """The finest pixel, to the thousandth of a micron and coarser than
    ``pixel_size_um``, whose raster ``voxels_at`` counts at most ``max_voxels``
    (the search :func:`build_curve_volume`'s refusal names its pixel with)."""
    return _finest_fitting_pixel(voxels_at, pixel_size_um, voxels_at(pixel_size_um), max_voxels)


def surface_fitting_pixel(pixel_size_um: float, plane: int, across: int,
                          max_surface: int = MAX_SURFACE) -> float:
    """The pixel at which a raster with ``plane`` and ``across`` exposed faces
    at ``pixel_size_um`` comes under ``max_surface`` with 5 % to spare: the one
    :func:`build_curve_volume`'s refusal names (:func:`_surface_fitting_pixel`)."""
    return _surface_fitting_pixel(pixel_size_um, plane, across, max_surface)


def _finest_fitting_pixel(voxels_at, pixel_size_um: float, voxels: int,
                          max_voxels: int) -> float:
    """The finest pixel, to the thousandth of a micron and coarser than
    ``pixel_size_um``, whose raster is at most ``max_voxels``.

    A box's voxels go as 1 / pixel^2; tiles under small outlines do not
    shrink with the pixel, so that guess is only where the search starts."""
    def fits(thousandths: int) -> bool:
        return voxels_at(round(thousandths * 1e-3, 3)) <= max_voxels

    lo = int(np.floor(pixel_size_um * 1e3))
    hi = max(lo + 1, int(np.ceil(pixel_size_um * np.sqrt(voxels / max_voxels) * 1e3)))
    for _ in range(80):
        if fits(hi):
            break
        lo, hi = hi, int(np.ceil(hi * 1.25))
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fits(mid):
            hi = mid
        else:
            lo = mid
    return round(hi * 1e-3, 3)


def build_curve_volume(
    annotations,
    *,
    volume_scale=(7.5, 7.5, 90.0),
    pixel_size_um: float = DEFAULT_PIXEL_SIZE_UM,
    spline_points: int = 20,
    margin_um: float = 0.5,
    max_voxels: int = 300_000_000,
    bridge_um: float = BRIDGE_UM,
    links=None,
    link_bridge_um: float = LINK_BRIDGE_UM,
    max_surface: int = MAX_SURFACE,
    keep_labels: bool = False,
) -> CurveVolume:
    """Rasterize closed-curve annotations into a boolean volume.

    ``volume_scale`` is the volume's nm-per-pixel triple; only its Z component is
    used, to convert section numbers to microns.

    Skipped sections between two traced ones at most ``bridge_um`` apart are
    filled by shape-based interpolation (:func:`interpolate_masks`); a longer
    skip stays open and the stack carries on at its true section number.

    ``links`` are Viking's location links, ``(location_id, location_id)``
    pairs (pairs naming anything but two of these annotations are ignored).
    Two linked outlines more than a section apart are the same branch seen
    either side of sections nobody could trace, so the pair is interpolated
    through them -- whether or not some other part of the structure was
    traced there, which is what the whole-section rule above cannot see
    (RPC1 c2628: location 144100 on section 1121 links to 144101 on 1125,
    and other branches occupy 1122-1124; the branch showed a slit).

    The volume is allocated in tiles (:class:`CurveVolume`), and
    ``max_voxels`` caps the tiles' voxels, not the box's.  The pixels are
    the ones a whole-box raster would have.  ``max_surface`` caps the
    raster's exposed voxel faces (:meth:`CurveVolume.exposed_faces`), which
    is what the mesh made of it will cost.  Either refusal names the finest
    pixel that fits.

    ``keep_labels`` also records, for every voxel, which kind of annotation
    drew it and how thick a process it is (:attr:`CurveVolume.labels`): a
    byte for each voxel, so as many bytes again as the raster itself, and
    what the averaging of the surface reads (:func:`~sbfsem.geometry.curve_average.polish_curve_mesh`).
    The occupancy is the same either way.
    """
    annotations = list(annotations)
    if not annotations:
        raise ValueError("No closed-curve annotations to render")

    rings_um = [a.rings(spline_points) for a in annotations]
    bounds = _ring_bounds(rings_um)
    how = {"volume_scale": volume_scale, "margin_um": margin_um, "bridge_um": bridge_um,
           "links": links, "link_bridge_um": link_bridge_um, "tile": TILE}
    plan = _curve_plan(annotations, bounds, pixel_size_um=pixel_size_um,
                       keep=max_voxels // (TILE * TILE), **how)
    grid = plan.grid
    xmin, ymin, rows, cols = grid.xmin, grid.ymin, grid.rows, grid.cols
    bridges, spans, sections, traced = grid.bridges, grid.spans, grid.sections, grid.traced
    dz = float(volume_scale[2]) / 1e3
    if plan.voxels > max_voxels or plan.keys is None:
        # Either a large structure asked for a fine pixel (RPC1 c2628, 24 x
        # 28 um over 754 sections, was 828M voxels as a box at 0.025 um), or
        # a unit mistake: outlines in Viking pixels rather than microns
        # inflate every window ~130x per axis.
        fits = _finest_fitting_pixel(
            lambda px: _curve_plan(annotations, bounds, pixel_size_um=px, keep=0, **how).voxels,
            pixel_size_um, plan.voxels, max_voxels)
        raise ValueError(
            f"Closed-curve volume would need {plan.voxels:,} voxels "
            f"({plan.voxels // (TILE * TILE):,} tiles of {TILE} x {TILE} pixels over a "
            f"{rows} x {cols} x {len(sections)} grid) at {pixel_size_um} um/pixel; "
            f"the limit is {max_voxels:,}. The finest pixel that fits this "
            f"structure is {fits:g} um. (Thousands of times over the limit "
            "means outlines in Viking pixels, not microns.)"
        )
    volume = CurveVolume(
        (rows, cols, len(sections)), plan.keys,
        np.zeros((len(plan.keys), TILE, TILE), dtype=bool),
        np.array([xmin, ymin, float(sections.min()) * dz]),
        np.array([pixel_size_um, pixel_size_um, dz]),
        sections, np.isin(sections, traced),
    )
    if keep_labels:
        volume.labels = np.zeros(volume.tiles.shape, dtype=np.uint8)
    section_index = {int(z): i for i, z in enumerate(sections)}

    def to_pixels(rings: list[np.ndarray]) -> list[np.ndarray]:
        return [
            np.column_stack([(r[:, 0] - xmin) / pixel_size_um, (r[:, 1] - ymin) / pixel_size_um])
            for r in rings
        ]

    for annotation, rings, window in zip(annotations, rings_um, plan.windows, strict=True):
        # Each outline on a window just big enough for it, not the whole
        # section: a disc-traced cell is thousands of small circles over a
        # wide box (Inferior c16137: 5,812 rings on 700 x 960 pixels, 60 s
        # as whole-section masks).
        k = section_index[int(annotation.z_section)]
        code = annotation.label_code() if keep_labels else 0
        if window is None:
            volume._paint(k, 0, 0, rasterize_rings(to_pixels(rings), (rows, cols)), code)
        elif window:
            r0, r1, c0, c1 = window
            shift = np.array([c0, r0], dtype=float)
            volume._paint(k, r0, c0, rasterize_rings(
                [r - shift for r in to_pixels(rings)], (r1 - r0 + 1, c1 - c0 + 1)), code)

    t = TILE
    n_tr, n_tc = volume.tile_grid
    tile_slice, tile_cell = np.divmod(volume.keys, n_tr * n_tc)
    for lo, hi in bridges:
        # Both sections on the box of their tiles: interpolation stays inside
        # the two masks' union, and is the same on any window holding it.
        ends = [section_index[lo], section_index[hi]]
        mine = tile_cell[np.isin(tile_slice, ends)]
        if not len(mine):
            continue
        tr, tc = np.divmod(mine, n_tc)
        r0, r1 = int(tr.min()) * t, min(int(tr.max()) * t + t, rows) - 1
        c0, c1 = int(tc.min()) * t, min(int(tc.max()) * t + t, cols) - 1
        fractions = [(z - lo) / (hi - lo) for z in range(lo + 1, hi)]
        between = interpolate_masks(volume._window(ends[0], r0, r1, c0, c1),
                                    volume._window(ends[1], r0, r1, c0, c1), fractions)
        # Drawn by neither end alone: the thicker of the two it lies between.
        code = max(int(volume.gather_labels(np.arange(r0, r1 + 1), np.arange(c0, c1 + 1),
                                            [k])[:, :, 0].max()) for k in ends) if keep_labels else 0
        for z, mask in zip(range(lo + 1, hi), between, strict=True):
            volume._paint(section_index[z], r0, c0, mask, code)

    index = {id(a): i for i, a in enumerate(annotations)}
    for (below, above), bounds_px in zip(spans, plan.span_windows, strict=True):
        # The two outlines alone, on a window just big enough for both: the
        # rest of the section is other branches' business.
        if not bounds_px:
            continue
        r0, r1, c0, c1 = bounds_px
        size = (r1 - r0 + 1, c1 - c0 + 1)
        shift = np.array([c0, r0], dtype=float)
        rings_lo = to_pixels(rings_um[index[id(below)]])
        rings_hi = to_pixels(rings_um[index[id(above)]])
        lo, hi = int(below.z_section), int(above.z_section)
        between = interpolate_masks(
            rasterize_rings([r - shift for r in rings_lo], size),
            rasterize_rings([r - shift for r in rings_hi], size),
            [(z - lo) / (hi - lo) for z in range(lo + 1, hi)],
            keep=True,
        )
        code = max(below.label_code(), above.label_code()) if keep_labels else 0
        for z, mask in zip(range(lo + 1, hi), between, strict=True):
            volume._paint(section_index[z], r0, c0, mask, code)

    plane, across = volume.exposed_faces()
    if plane + across > max_surface:
        # The box no longer bounds the mesh: a wide cell's tiles fit a fine
        # pixel whose surface would not fit in memory (RPC1 c2628 at 0.02 um).
        fits = _surface_fitting_pixel(pixel_size_um, plane, across, max_surface)
        raise ValueError(
            f"Closed-curve surface would need {plane + across:,} voxel faces "
            f"({plane:,} in the sections' planes, {across:,} between sections) at "
            f"{pixel_size_um} um/pixel; the limit is {max_surface:,}. The finest pixel "
            f"that fits this structure is {fits:g} um."
        )
    volume.exposed = (int(plane), int(across))
    return volume


def _surface_fitting_pixel(pixel_size_um: float, plane: int, across: int,
                           max_surface: int) -> float:
    """A pixel, to the thousandth of a micron, at which ``plane + across``
    exposed faces come under ``max_surface``, with 5 % to spare.

    Faces in a section's plane trace the outlines, so they go as 1 / pixel;
    faces between sections cover the change from one section's area to the
    next, as 1 / pixel^2 (both exactly so on RPC1 c2628, RC1 c410 and
    Inferior c16137 from 0.184 to 0.025 um)."""
    target = 0.95 * max_surface
    if across:
        ratio = (np.sqrt(plane * plane + 4.0 * across * target) - plane) / (2.0 * across)
    else:
        ratio = target / max(plane, 1)
    finest = int(np.floor(pixel_size_um * 1e3)) + 1
    return round(max(finest, int(np.ceil(pixel_size_um / ratio * 1e3))) * 1e-3, 3)


def _window(rings, rows: int, cols: int) -> tuple[int, int, int, int] | tuple[()] | None:
    """``(r0, r1, c0, c1)``, the inclusive pixel window that holds ``rings``
    (pixel coordinates) with a pixel to spare; ``()`` when they lie off the
    grid; ``None`` when a point is not finite, to rasterize on the whole
    section as before."""
    points = np.vstack(rings)
    if not len(points) or not np.isfinite(points).all():
        return None
    c0, r0 = np.clip(np.floor(points.min(axis=0)).astype(int) - 1, 0, None)
    c1, r1 = np.minimum(np.ceil(points.max(axis=0)).astype(int) + 1, [cols - 1, rows - 1])
    if c1 < c0 or r1 < r0:
        return ()
    return int(r0), int(r1), int(c0), int(c1)


def _linked_spans(annotations, links, max_sections: float):
    """``(below, above)`` annotation pairs a link joins across skipped sections."""
    if links is None:
        return []
    by_id = {int(a.location_id): a for a in annotations if a.location_id}
    spans, seen = [], set()
    for a, b in links:
        pair = (min(int(a), int(b)), max(int(a), int(b)))
        if pair in seen or pair[0] not in by_id or pair[1] not in by_id:
            continue
        seen.add(pair)
        below, above = sorted((by_id[pair[0]], by_id[pair[1]]), key=lambda x: x.z_section)
        if 1 < above.z_section - below.z_section <= max_sections + 1e-9:
            spans.append((below, above))
    return spans


def interpolate_masks(below: np.ndarray, above: np.ndarray, fractions, *,
                      keep: bool = False) -> list[np.ndarray]:
    """Masks part of the way from ``below`` to ``above``, one per fraction.

    Shape-based interpolation (Raya & Udupa 1990): blend the two signed
    distance maps and keep what is still inside.  An outline present on both
    sides morphs from one to the other; one present on a single side tapers
    off toward the other.  The result never leaves the union of the two
    masks, so the work is done on the union's bounding box alone.

    Two outlines that do not overlap taper toward each other and can leave
    nothing in the middle.  ``keep`` is for a pair known to be one branch
    (a Viking link): where the blend comes out empty the nearer outline is
    carried across instead, so the branch is never cut.  Tried and dropped:
    sliding the two outlines onto a common centroid first.  Leaving a traced
    outline out of 1,500 linked chains of RPC1 c2628 and rebuilding it from
    its neighbours scored IoU 0.711 plain against 0.682 aligned (0.698 /
    0.676 on RC1 c410, 0.788 / 0.772 on RPC2 c774): these outlines change
    shape from section to section more than they travel.
    """
    fractions = [float(t) for t in fractions]
    if not below.any() or not above.any():
        # Nothing to measure a distance to: carry the nearer side across.
        return [(below if t < 0.5 else above).copy() for t in fractions]
    union = below | above
    r = np.flatnonzero(union.any(axis=1))
    c = np.flatnonzero(union.any(axis=0))
    window = (slice(r[0], r[-1] + 1), slice(c[0], c[-1] + 1))

    def signed(mask: np.ndarray) -> np.ndarray:
        # One empty pixel all round, so an outline filling the window still
        # has an outside to measure to.  Negative inside.
        m = np.pad(mask[window], 1)
        return (distance_transform_edt(~m) - distance_transform_edt(m)).astype(np.float32)

    d0, d1 = signed(below), signed(above)
    out = []
    for t in fractions:
        mask = np.zeros_like(below)
        mask[window] = (((1.0 - t) * d0 + t * d1) < 0)[1:-1, 1:-1]
        if keep and not mask.any():
            mask = (below if t < 0.5 else above).copy()
        out.append(mask)
    return out
