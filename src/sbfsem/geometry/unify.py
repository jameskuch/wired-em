"""One implicit surface for a whole cell: the ``voxel2`` pathway.

The voxel pathway builds a cell from *pieces* -- one marching-cubes envelope
per fat cluster, rescue lofts, an explicit ring tube for every thin stretch,
tie wires -- and concatenates them.  Pieces connect only where their surfaces
happen to meet, so a dendrite leaving a soma is a tube whose capped end sits
inside the envelope: two shells that count as one visual body, but under a
wireframe or a translucent material the tube is plainly a separate object
piercing the soma (c106, Temporal Monkey: 27 topological components).  No
concatenation can be one surface.

This module makes the cell one surface by making it **one field**.  Every
piece becomes a signed-distance primitive -- the cluster and rescue fields
are kept exactly as the voxel pathway computed them (lofted discs, blurred,
volume compensated) and resampled tricubically; each thin stretch becomes a
chain of exact round cones through its discs; links, ties and stranded coins
likewise -- and the union of all of them is contoured *once*, on one uniform
grid at dendrite resolution.  Where a tube meets the soma the two fields
simply merge, so marching cubes emits a single connected sheet with a crease
along the true intersection curve (or a fillet, with ``blend_um``).

A whole-cell grid at dendrite resolution is affordable only because it is
never materialized.  The field is evaluated in ``block``-sized cubes that
some primitive's bounding box actually touches; each block is marched on its
own and discarded, and the pieces are welded along their shared faces.
Marching cubes is local to a grid cell, so two blocks that share a face and
see the same sample values there emit the same triangles there -- the weld
is exact, not a tolerance hack.  Sample coordinates are always computed from
the *global* integer index for that reason.

Resolution is chosen from the annotations (``VOXELS_PER_THIN_RADIUS`` voxels
across a low percentile of the thin radii) and then held to two budgets: an
estimated raw triangle count (at most ``MAX_RAW_FACES``; :func:`raw_budget`)
and an evaluation budget (:func:`work_budget`, ``MAX_WORK_VOXELS`` or more
for a raw budget over 3 million).  A cell whose arbor is too large for
either is contoured coarser than its thinnest dendrites deserve, and says so
in the log and in ``mesh.meta``; this pathway is for cells that fit, which
every soma-plus-arbor cell measured so far does.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import hashlib
import itertools
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from scipy.ndimage import map_coordinates
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from skimage.measure import marching_cubes

from ..progress import report
from .convolution import (
    SUPPORT_FACTOR,
    THRESHOLD,
    PointKernel,
    SegmentKernel,
    merge_short_segments,
)
from .cylinder import TIE_RADIUS_UM, _tie_segments
from .mesh import EMPTY_MESH, Mesh
from .remesh import SampledField, _tube_distance

__all__ = [
    "BLOCK",
    "CHUNKED_DECIMATION",
    "MODES",
    "Capsule",
    "DiscHull",
    "Lens",
    "MAX_RAW_FACES",
    "MAX_WORK_VOXELS",
    "RAW_FACES_KEYED_ABOVE",
    "TYPED_MIN_VOXEL_UM",
    "VOXELS_PER_THIN_RADIUS",
    "VoxelPlan",
    "WORK_PER_RAW_FACE",
    "auto_voxel_um",
    "decimates_in_chunks",
    "field_bodies",
    "hull_pairs",
    "plan_voxel",
    "raw_budget",
    "split_components",
    "unified_mesh",
    "work_budget",
]

log = logging.getLogger(__name__)

#: Background value of the field where no primitive reaches.  Finite so that
#: marching cubes, which differentiates the field, never meets an infinity.
FAR = 1e3

#: The extraction level.  A hair below zero so that a flat cap or coin face
#: landing exactly on a grid plane (values exactly 0.0) does not emit a sheet
#: of degenerate zero-area triangles.
LEVEL = -1e-6

#: Voxels across the thin radius the grid is sized for.  Two is enough for a
#: marching-cubes tube to stay connected and round (a disc of radius r always
#: holds a grid point once r >= 0.71 voxels; at two per radius the staircase
#: is one Taubin pass deep); three would cost 3.4x the triangles for a change
#: the smoothing already removes.
VOXELS_PER_THIN_RADIUS = 2.0

#: The thin radius the grid is sized for is this percentile of the thin
#: annotations' radii, not the minimum: the very thinnest annotations are
#: often a tracer's single tiny circle, and sizing a whole cell's grid for one
#: of them is the wrong trade.  Everything below still renders -- at fewer
#: voxels per radius, still connected.
THIN_RADIUS_PERCENTILE = 2.0

#: Bounds on the fine voxel, in microns.  ``MIN_VOXEL_UM`` is the floor of the
#: voxel sized from the annotations (``voxel_um`` 0); a voxel that was typed is
#: honored down to ``TYPED_MIN_VOXEL_UM`` (2026-10-16; it was 0.04 too).  The
#: raw-triangle and work budgets below still hold every build, so the floor
#: reaches only a small cell (one process a build, on a loaded machine): Temporal
#: c106 (274 um^2 of surface) typed at 0.03 um built 871,000 raw triangles in 25
#: to 39 s and peaked at 547 to 876 MB (0.04 um: 489,440, 11 to 22 s, 403 to
#: 577 MB); at 0.02 um with no Face cap 1,960,558 in 86 s, 947 MB, and under the
#: default cap it came out at 0.028 um, the raw-triangle budget's (3 for each of
#: the 400,000 kept), at 931 MB.  Inferior c1178 (4,888 um^2) typed at 0.02 or
#: at 0.04 with no cap came out at 0.0414 um either way, the 10 million budget's:
#: 7,064,612 raw triangles, 3.3 to 3.4 GB.
MIN_VOXEL_UM = 0.04
TYPED_MIN_VOXEL_UM = 0.02
MAX_VOXEL_UM = 0.3

#: Raw marching-cubes triangles before decimation: the most any build asks
#: for.  The face cap in the pathway decimates the result further, but the
#: raw sheet has to exist first.  It was 3 million until 2026-10-16.  Only a
#: request reaches the new range: Face cap 0, or a cap over a million (the
#: app's 400,000 asks for 1.2 million).  The cost is what the sheet holds:
#: Inferior c1178 (4,090 um2 of surface), every triangle kept, peaked at 310
#: MB plus 0.445 MB per 1,000 triangles -- 1,256 MB at 2.1 million (13 s),
#: 2,467 MB at 4.9 million (31 s), 3,467 MB at 7.1 million (60 s), the most it
#: holds of the 10 million -- and a Face cap added 0.37 MB per 1,000 raw
#: triangles for the decimator's copy (3.4 million kept of 7.1: 5,615 MB,
#: 78 s) until such a cap was decimated a chunk at a time (2026-10-20,
#: ``geometry.decimate``: 3,148 MB, 58 s; on the server 3,134 -> 1,568 MB).  A
#: cell that fills the whole budget is about 4.8 GB, under a cap or not
#: (``service.facade.FIELD_BYTES_PER_RAW_FACE``).
MAX_RAW_FACES = 10_000_000

#: The ceiling before 2.5.1.  A build whose raw budget is no higher than this
#: is exactly what it was, so its cache key does not name the budget; above it
#: the budget is part of the key (``service.facade``), or the same options would
#: be answered with a surface built under the old ceiling.
RAW_FACES_KEYED_ABOVE = 3_000_000

#: Raw triangles the pathway asks for by default, per triangle of its face
#: cap (``voxel2_faces``): the decimator removes the rest, so more is memory
#: spent on nothing.
RAW_PER_CAPPED_FACE = 3

#: The least raw sheet a build asks for, whatever its cap.
MIN_RAW_BUDGET = 600_000

#: Field evaluations (voxels stamped by primitives) per build.
MAX_WORK_VOXELS = 60_000_000

#: Field evaluations per triangle of a raw budget over 3 million: the figure
#: the two original limits (60 million for 3 million) were set at.  Measured on
#: c1178 at 0.0414 um, the finest voxel the 10 million ceiling allows it: 194
#: million evaluations, 19 per triangle of budget.  Raising the triangle limit
#: alone changed nothing there (0.076 -> 0.0745 um, 2.13 -> 2.29 million faces,
#: the same deviation): the evaluation budget took over at once.
WORK_PER_RAW_FACE = 20


def raw_budget(voxel2_faces: int) -> int:
    """The raw-triangle budget a Voxel remesh build asks :func:`unified_mesh` for.

    The raw sheet may be ``RAW_PER_CAPPED_FACE`` times the Face cap it is
    decimated to, never under ``MIN_RAW_BUDGET`` and never over
    ``MAX_RAW_FACES``; with no cap (0) the ceiling itself.
    """
    cap = int(voxel2_faces)
    if cap <= 0:
        return MAX_RAW_FACES
    return min(max(RAW_PER_CAPPED_FACE * cap, MIN_RAW_BUDGET), MAX_RAW_FACES)


#: The version of :func:`~sbfsem.geometry.decimate.decimate_in_chunks` in the key
#: of a surface it decimates (``service.facade._budget_token``).
CHUNKED_DECIMATION = 1


def decimates_in_chunks(voxel2_faces: int) -> bool:
    """Whether a build under this Face cap decimates its sheet a chunk at a time
    (:func:`~sbfsem.geometry.decimate.decimate_in_chunks`): a cap whose raw
    budget is over ``RAW_FACES_KEYED_ABOVE``, so only builds whose key already
    names their budget change, and none the app makes by default (2026-10-20)."""
    cap = int(voxel2_faces)
    return cap > 0 and raw_budget(cap) > RAW_FACES_KEYED_ABOVE


def work_budget(max_raw_faces: int) -> int:
    """The field evaluations a build with this raw budget may spend.

    ``MAX_WORK_VOXELS``, or ``WORK_PER_RAW_FACE`` for each triangle of a
    budget over 3 million: a raw budget the evaluation budget stopped from
    ever being reached would change nothing.  A build at 3 million or under
    keeps the 60 million it always had.
    """
    return max(MAX_WORK_VOXELS, WORK_PER_RAW_FACE * int(max_raw_faces))

#: Triangles marching cubes emits per unit of surface area over voxel^2,
#: measured on c106 (Temporal Monkey) at 0.055 um: 3.3.
FACES_PER_SURFACE_VOXEL = 3.5

#: How a block's field is put together from its leaves.  ``min`` is the exact
#: signed-distance union (voxel2); ``occupancy`` is the same union
#: binarized to inside/outside before contouring -- what a voxelizer
#: produces from proxy geometry (the Ultraliser pathway); ``sum`` adds
#: convolution-kernel potentials and contours ``T - sum`` (the convolution
#: pathway), so nothing is a distance and the field-leaf shortcuts do not
#: apply.
MODES = ("min", "occupancy", "sum")

#: Cubes per block edge.  33^3 samples per block: small enough that a block
#: deep inside a soma is cheap to discover empty, large enough that the
#: per-block Python overhead is not the cost.
BLOCK = 32

#: Voxels of positive field around every primitive's own extent, so the
#: crossing is always interpolated between two genuine distances and never
#: between a distance and the ``FAR`` background.
PAD_VOXELS = 3


class _Leaf(Protocol):
    """A primitive the block grid can stamp: a bounding box plus either a
    signed distance (``min``/``occupancy`` modes) or a potential (``sum``)."""

    def bounds(self) -> tuple[np.ndarray, np.ndarray]: ...


@dataclass
class Capsule:
    """One exact round cone from ``(a, ra)`` to ``(b, rb)``."""

    a: np.ndarray
    b: np.ndarray
    ra: float
    rb: float

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        lo = np.minimum(self.a - self.ra, self.b - self.rb)
        hi = np.maximum(self.a + self.ra, self.b + self.rb)
        return lo, hi

    def distance(self, pts: np.ndarray) -> np.ndarray:
        return _tube_distance(pts, self.a, self.b, self.ra, self.rb)


@dataclass
class DiscHull:
    """The convex hull of two discs in their own section planes: its horizontal
    slices are the discs interpolated linearly between them, so the surface is
    exactly the annotated disc at both sections and nothing reaches above the
    upper one or below the lower one (a round cone's end balls reach a radius
    past each).  For a chain of discs one or a few sections apart along a process
    that crosses the sections steeply, where those balls, a radius tall, overlap
    across many sections and their union takes the outer edge of every wobble of
    the annotated centers and radii (:func:`hull_pairs`; Exact tubes °).

    The value is the in-plane distance to the interpolated disc corner-combined
    with the distance past the pair's two sections (a :class:`Lens`'s form), so
    its zero set is exactly the hull."""

    a: np.ndarray
    b: np.ndarray
    ra: float
    rb: float

    def __post_init__(self) -> None:
        if float(self.a[2]) > float(self.b[2]):
            self.a, self.b, self.ra, self.rb = self.b, self.a, self.rb, self.ra

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        r = max(self.ra, self.rb)
        lo = np.minimum(self.a, self.b) - np.array([r, r, 0.0])
        hi = np.maximum(self.a, self.b) + np.array([r, r, 0.0])
        return lo, hi

    def distance(self, pts: np.ndarray) -> np.ndarray:
        za, zb = float(self.a[2]), float(self.b[2])
        span = max(zb - za, 1e-12)
        t = np.clip((pts[:, 2] - za) / span, 0.0, 1.0)
        cx = self.a[0] + t * (self.b[0] - self.a[0])
        cy = self.a[1] + t * (self.b[1] - self.a[1])
        r = self.ra + t * (self.rb - self.ra)
        d2 = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r
        dz = np.maximum(za - pts[:, 2], pts[:, 2] - zb)
        return (np.minimum(np.maximum(d2, dz), 0.0)
                + np.hypot(np.maximum(d2, 0.0), np.maximum(dz, 0.0)))


def hull_pairs(points: np.ndarray, radii: np.ndarray, max_tilt_deg: float,
               window: int = 3) -> np.ndarray:
    """Which consecutive pairs of a stretch are drawn as :class:`DiscHull`: on
    different sections, a step no wider than the larger disc, and the process
    within ``max_tilt_deg`` of vertical over ``window`` discs either side.

    Steep processes are where the round cones' end balls stack up; an oblique
    process is drawn right by a round cone, whose horizontal slice is the
    ellipse a slanted round process makes (its discs are drawn at its width:
    0.85 of the traced half-width at every tilt, measured at 212 outline-disc
    links).  ``max_tilt_deg`` of 0 draws every pair as a round cone."""
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    rad = np.abs(np.asarray(radii, dtype=float).ravel())
    n = len(pts)
    out = np.zeros(max(n - 1, 0), dtype=bool)
    if max_tilt_deg <= 0 or n < 2:
        return out
    cos_limit = float(np.cos(np.radians(max_tilt_deg)))
    for i in range(n - 1):
        step = pts[i + 1] - pts[i]
        if abs(float(step[2])) < 1e-9:
            continue                        # one section: a link, not a stack
        if float(np.hypot(step[0], step[1])) > max(rad[i], rad[i + 1]):
            continue                        # a step past the disc: a travelling stack
        span = pts[min(i + 1 + window, n - 1)] - pts[max(i - window, 0)]
        length = float(np.linalg.norm(span))
        if length > 1e-9 and abs(float(span[2])) / length >= cos_limit:
            out[i] = True
    return out


@dataclass
class Lens:
    """A disc in its section plane extruded ``h`` up and down: a coin.

    The honest shape of an annotation nothing else joins -- never a ball.
    """

    center: np.ndarray
    r: float
    h: float

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        pad = np.array([self.r, self.r, self.h])
        return self.center - pad, self.center + pad

    def distance(self, pts: np.ndarray) -> np.ndarray:
        d2 = np.hypot(pts[:, 0] - self.center[0], pts[:, 1] - self.center[1]) - self.r
        dz = np.abs(pts[:, 2] - self.center[2]) - self.h
        return (np.minimum(np.maximum(d2, dz), 0.0)
                + np.hypot(np.maximum(d2, 0.0), np.maximum(dz, 0.0)))


class _FieldLeaf:
    """A cluster or rescue field, resampled where the fine grid asks.

    Resampling is the expensive step, so it is spent only where it can matter.
    The coarse field is 1-Lipschitz (a distance, blurred by a probability
    kernel), so its own samples bound it between them.  Over a block whose
    coarse samples all exceed ``margin`` the field can touch no crossing and
    is skipped; one whose samples are all below ``-margin`` is deep inside the
    body, and the whole block is inside the union.  In between, each fine
    sample takes the value of its nearest coarse sample -- which has the right
    sign and lies too far from zero to be a crossing endpoint -- unless that
    coarse value is within ``margin``, and only those samples get the
    tricubic resample.  A dendrite-resolution grid over a soma is thereby
    paid for only along the soma's surface.
    """

    def __init__(self, field: SampledField) -> None:
        self.field = field
        self.values = np.asarray(field.field, dtype=np.float32) - np.float32(field.level)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.field.lo.copy(), self.field.hi.copy()

    def distance(self, pts: np.ndarray) -> np.ndarray:
        return self.field.sample(pts)

    def margin(self, grid: _BlockGrid) -> float:
        """Values farther from zero than this cannot move a crossing: a fine
        sample is within half a coarse diagonal (0.87 voxel) of its nearest
        coarse sample, a crossing endpoint is within one fine voxel of the
        surface, and a fillet reaches ``blend`` further."""
        return float(self.field.voxel + 2.0 * grid.voxel + grid.blend)

    def block_fingerprint(self, grid: _BlockGrid, key: tuple[int, int, int]) -> bytes:
        """The field as one block of ``grid`` reads it, as 16 bytes: its layout, and
        the coarse values and spline coefficients within reach of the block (the
        nearest sample, the skip and inside tests, and a tricubic's four either
        side, :meth:`stamp`).  A moved disc changes the cluster's distance near it,
        so the blocks away from it keep their triangles (the spline's coefficients
        are made over the whole field, but a change in them falls off by 0.27 a
        sample, and only a window that is the same to the bit counts as the same).
        A cluster whose level is calibrated over all of it -- a soma's -- changes
        everywhere when one of its discs moves (c1178's soma disc moved 0.2 um: its
        level 0.257128 -> 0.257103), so all of it is contoured again, as it must be."""
        sf = self.field
        layout = getattr(self, "_layout_fp", None)
        if layout is None:
            h0 = hashlib.blake2b(digest_size=16)
            for part in (sf.lo, sf.voxel, sf.level, self.values.shape):
                _hash_value(h0, part)
            layout = self._layout_fp = h0.digest()
        last = np.array(self.values.shape) - 1
        lo_um = grid.origin + np.asarray(key, dtype=float) * grid.block * grid.voxel
        hi_um = lo_um + grid.block * grid.voxel
        c0 = np.clip(np.floor((lo_um - sf.lo) / sf.voxel).astype(int) - 3, 0, last)
        c1 = np.clip(np.ceil((hi_um - sf.lo) / sf.voxel).astype(int) + 3, 0, last)
        window = tuple(slice(int(a), int(b) + 1) for a, b in zip(c0, c1, strict=True))
        h = hashlib.blake2b(layout, digest_size=16)
        h.update(np.asarray(c0, dtype=np.int64).tobytes())
        h.update(np.asarray(c1, dtype=np.int64).tobytes())
        h.update(np.ascontiguousarray(self.values[window]).tobytes())
        h.update(np.ascontiguousarray(sf.coefficients()[window]).tobytes())
        return h.digest()

    def work_voxels(self, grid: _BlockGrid) -> int:
        """Fine samples this field will actually resample: the coarse samples
        within the margin, each standing for a cube of fine ones."""
        near = int(np.count_nonzero(np.abs(self.values) <= self.margin(grid)))
        return int(near * (self.field.voxel / grid.voxel) ** 3)

    def stamp(self, grid: _BlockGrid, start: np.ndarray, s: np.ndarray, e: np.ndarray,
              temp: np.ndarray) -> str:
        """Write this field's values into ``temp[s:e]``; ``"skip"``,
        ``"inside"`` or ``"done"``."""
        sf = self.field
        margin = self.margin(grid)
        lo_um = grid.origin + (start + s) * grid.voxel
        hi_um = grid.origin + (start + e) * grid.voxel
        last = np.array(self.values.shape) - 1
        c0 = np.clip(np.floor((lo_um - sf.lo) / sf.voxel).astype(int) - 1, 0, last)
        c1 = np.clip(np.ceil((hi_um - sf.lo) / sf.voxel).astype(int) + 1, 0, last)
        sub = self.values[c0[0]:c1[0] + 1, c0[1]:c1[1] + 1, c0[2]:c1[2] + 1]
        if sub.size == 0 or float(sub.min()) > margin:
            return "skip"
        if float(sub.max()) < -margin:
            return "inside"
        pts = grid._coords(start, s, e)
        nearest = np.clip(np.rint((pts - sf.lo) / sf.voxel).astype(int), 0, last)
        out = self.values[nearest[:, 0], nearest[:, 1], nearest[:, 2]].copy()
        near = np.abs(out) <= margin
        if near.any():
            out[near] = sf.sample(pts[near], order=3).astype(np.float32, copy=False)
        region = temp[s[0]:e[0] + 1, s[1]:e[1] + 1, s[2]:e[2] + 1]
        np.minimum(region, out.reshape(tuple(e - s + 1)), out=region)
        return "done"


class _CurveLeaf:
    """A closed-curve raster (:class:`~sbfsem.geometry.closed_curve.CurveVolume`)
    as a field on the whole-cell grid: the Hybrid method's outline half, in
    the same field as its discs, so the two are one surface.

    The outline half's own surface is the 0.5 level of the raster's occupancy
    after a 3x3x3 box filter, each run of sections padded with an empty voxel
    all round (:func:`~sbfsem.geometry.volume.curve_volume_to_mesh`).  This
    leaf computes that same smoothed occupancy ``s`` where a block asks,
    interpolates it trilinearly at the fine samples, and writes
    ``(0.5 - s) * scale``: negative inside, zero exactly where the outline
    half's surface lies.  ``scale`` (three pixels) makes the value a distance
    across a section's rim, where ``s`` ramps from 0 to 1 over three pixels;
    past the ramp it holds at a pixel and a half, which is all the union
    needs (the crossing of a disc's surface is decided by the disc's own
    values, and a sample deep inside either is inside the union).

    Only blocks some allocated tile of the raster reaches are registered
    (:meth:`block_keys`), so a sparse raster -- a wide cell's processes --
    costs its own footprint, not its box.
    """

    def __init__(self, volume) -> None:
        self.volume = volume
        self.spacing = np.asarray(volume.spacing_um, dtype=float)
        self.origin = np.asarray(volume.origin_um, dtype=float)
        self.scale = float(3.0 * min(self.spacing[0], self.spacing[1]))
        self.runs = list(volume.runs())
        rows, cols, _ = volume.shape
        dz = self.spacing[2]
        sections = np.asarray(volume.sections, dtype=float)
        # The pad voxel all round: one pixel beyond the box, one section
        # beyond each run's ends.
        self._lo = np.array([self.origin[0] - self.spacing[0], self.origin[1] - self.spacing[1],
                             (sections.min() - 1.0) * dz])
        self._hi = np.array([self.origin[0] + cols * self.spacing[0],
                             self.origin[1] + rows * self.spacing[1],
                             (sections.max() + 1.0) * dz])
        # Per grid: unified_mesh builds the grid again, coarser, when the work
        # budget is spent, and a block's index means another place in it.
        self._keys: dict[tuple, set[tuple[int, int, int]]] = {}

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self._lo.copy(), self._hi.copy()

    def block_keys(self, grid: _BlockGrid) -> set[tuple[int, int, int]]:
        """The grid blocks an allocated, non-empty tile reaches (with the
        grid's pad and the filter's voxel either side)."""
        which = (grid.voxel, grid.block, grid.pad_um, *map(float, grid.origin))
        if which in self._keys:
            return self._keys[which]
        vol = self.volume
        t = vol.tile
        n_tr, n_tc = vol.tile_grid
        k, cell = np.divmod(np.asarray(vol.keys, dtype=np.int64), n_tr * n_tc)
        tr, tc = np.divmod(cell, n_tc)
        full = vol.tiles.reshape(len(vol.tiles), -1).any(axis=1) if len(vol.tiles) else np.zeros(0, bool)
        k, tr, tc = k[full], tr[full], tc[full]
        dx, dy, dz = self.spacing
        reach = grid.pad_um + 2.0 * float(max(dx, dy, dz))
        sections = np.asarray(vol.sections, dtype=float)
        lo = np.column_stack([self.origin[0] + tc * t * dx, self.origin[1] + tr * t * dy,
                              sections[k] * dz]) - reach
        hi = np.column_stack([self.origin[0] + (tc + 1) * t * dx, self.origin[1] + (tr + 1) * t * dy,
                              sections[k] * dz]) + reach
        B = grid.block
        b_lo = np.maximum(np.floor((lo - grid.origin) / grid.voxel).astype(np.int64) // B, 0)
        b_hi = np.maximum(np.ceil((hi - grid.origin) / grid.voxel).astype(np.int64) // B, 0)
        # Neighboring tiles of one section, and one tile over many sections,
        # mostly reach the same blocks: each distinct range once.
        ranges = np.unique(np.hstack([b_lo, b_hi]), axis=0) if len(b_lo) else np.zeros((0, 6), int)
        keys: set[tuple[int, int, int]] = set()
        for row in ranges:
            keys.update((int(a), int(b), int(c)) for a, b, c in itertools.product(
                *(range(int(row[d]), int(row[d + 3]) + 1) for d in range(3))))
        self._keys[which] = keys
        return keys

    def work_voxels(self, grid: _BlockGrid) -> int:
        """Every sample of every block it reaches is interpolated."""
        return len(self.block_keys(grid)) * (grid.block + 1) ** 3

    def block_fingerprint(self, grid: _BlockGrid, key: tuple[int, int, int]) -> bytes:
        """The raster as one block of ``grid`` reads it, as 16 bytes: its layout, and
        the non-empty tiles within the block's reach (:meth:`stamp` interpolates
        between two voxels after a filter of one, and a block is registered from the
        grid's pad plus two voxels away).  A move redraws a few tiles of the raster,
        so it changes the blocks those tiles reach and no other: one soma disc of
        Inferior c1178 under Hybrid, 537 of 1,833 blocks with the raster as a whole."""
        vol = self.volume
        if getattr(self, "_tile_index", None) is None:
            full = (vol.tiles.reshape(len(vol.tiles), -1).any(axis=1) if len(vol.tiles)
                    else np.zeros(0, dtype=bool))
            keys = np.asarray(vol.keys, dtype=np.int64)[full]
            digests = np.array([int.from_bytes(hashlib.blake2b(np.packbits(t).tobytes(), digest_size=8)
                                               .digest(), "little") for t in vol.tiles[full]],
                               dtype=np.uint64)
            layout = hashlib.blake2b(digest_size=16)
            for part in (vol.sections, self.spacing, self.origin, tuple(vol.shape), vol.tile,
                         tuple(vol.tile_grid)):
                _hash_value(layout, part)
            self._tile_index = (keys, digests, layout.digest())
        keys, digests, layout_fp = self._tile_index
        dx, dy, dz = (float(v) for v in self.spacing)
        reach = grid.pad_um + 3.0 * max(dx, dy, dz)
        lo = grid.origin + np.asarray(key, dtype=float) * grid.block * grid.voxel - reach
        hi = grid.origin + (np.asarray(key, dtype=float) + 1.0) * grid.block * grid.voxel + reach
        t = int(vol.tile)
        n_tr, n_tc = (int(v) for v in vol.tile_grid)
        rows, cols, _ = vol.shape
        sections = np.asarray(vol.sections, dtype=float)
        c0 = max(int(np.floor((lo[0] - self.origin[0]) / dx)) - 2, 0) // t
        c1 = min(int(np.floor((hi[0] - self.origin[0]) / dx)) + 3, cols - 1) // t
        r0 = max(int(np.floor((lo[1] - self.origin[1]) / dy)) - 2, 0) // t
        r1 = min(int(np.floor((hi[1] - self.origin[1]) / dy)) + 3, rows - 1) // t
        k0 = max(int(np.searchsorted(sections, lo[2] / dz - 1.0, "left")) - 2, 0)
        k1 = min(int(np.searchsorted(sections, hi[2] / dz + 1.0, "right")) + 2, len(sections) - 1)
        h = hashlib.blake2b(layout_fp, digest_size=16)
        if len(keys) and c0 <= c1 and r0 <= r1 and k0 <= k1:
            want = ((np.arange(k0, k1 + 1, dtype=np.int64)[:, None, None] * n_tr
                     + np.arange(r0, r1 + 1, dtype=np.int64)[None, :, None]) * n_tc
                    + np.arange(c0, c1 + 1, dtype=np.int64)[None, None, :]).ravel()
            at = np.searchsorted(keys, want)
            hit = at < len(keys)
            hit[hit] = keys[at[hit]] == want[hit]
            h.update(want[hit].tobytes())
            h.update(digests[at[hit]].tobytes())
        return h.digest()

    def _smoothed(self, start: int, stop: int, axes) -> np.ndarray | None:
        """The run's smoothed occupancy at the integer voxels ``axes`` span
        (``(row, col, slice)`` index arrays, pad indices -1 and n included,
        which are 0), or ``None`` when nothing of the run is there."""
        rows, cols, _ = self.volume.shape
        n = (rows, cols, stop - start)
        lo = [max(int(a[0]), 0) for a in axes]
        hi = [min(int(a[-1]), n[d] - 1) for d, a in enumerate(axes)]
        out = np.zeros(tuple(len(a) for a in axes), dtype=np.float32)
        if any(h < lo_d for lo_d, h in zip(lo, hi, strict=True)):
            return None
        index = [np.clip(np.arange(lo[d] - 1, hi[d] + 2), 0, n[d] - 1) for d in range(3)]
        occupied = self.volume.gather(index[0], index[1], start + index[2])
        if not occupied.any():
            return None
        from .volume import smooth_volume  # a leaf of the geometry package: no cycle

        smooth = smooth_volume(occupied)[1:-1, 1:-1, 1:-1]
        place = tuple(slice(lo[d] - int(axes[d][0]), hi[d] - int(axes[d][0]) + 1) for d in range(3))
        out[place] = smooth
        return out

    def stamp(self, grid: _BlockGrid, start: np.ndarray, s: np.ndarray, e: np.ndarray,
              temp: np.ndarray) -> str:
        """Write the raster's values into ``temp[s:e]``; ``"skip"``,
        ``"inside"`` or ``"done"`` (as :meth:`_FieldLeaf.stamp`)."""
        if (start[0] // grid.block, start[1] // grid.block, start[2] // grid.block) not in \
                self.block_keys(grid):
            return "skip"
        axes_um = [grid.origin[d] + (start[d] + np.arange(int(s[d]), int(e[d]) + 1)) * grid.voxel
                   for d in range(3)]
        dx, dy, dz = self.spacing
        col = (axes_um[0] - self.origin[0]) / dx
        row = (axes_um[1] - self.origin[1]) / dy
        sections = np.asarray(self.volume.sections, dtype=float)
        total: np.ndarray | None = None
        for r0, r1 in self.runs:
            k = axes_um[2] / dz - sections[r0]
            n = r1 - r0
            if k[-1] <= -1.0 or k[0] >= n:
                continue
            rows, cols, _ = self.volume.shape
            if row[-1] <= -1.0 or row[0] >= rows or col[-1] <= -1.0 or col[0] >= cols:
                continue
            ints = [np.arange(int(np.floor(max(c[0], -1.0))), int(np.floor(min(c[-1], top))) + 2)
                    for c, top in ((row, rows), (col, cols), (k, n))]
            smooth = self._smoothed(r0, r1, ints)
            if smooth is None:
                continue
            # Trilinear, in (row, col, slice) order, at every fine sample
            # (the fine grid is (x, y, z)-major).
            cx, ry, kz = np.meshgrid(col - ints[1][0], row - ints[0][0], k - ints[2][0],
                                     indexing="ij")
            values = map_coordinates(smooth, [ry.ravel(), cx.ravel(), kz.ravel()], order=1,
                                     mode="constant", cval=0.0, output=np.float32)
            values = values.reshape(cx.shape)
            total = values if total is None else np.maximum(total, values)
        if total is None or float(total.max()) < 0.5:
            return "skip"
        full = bool((s == 0).all() and (e == grid.block).all())
        if full and float(total.min()) > 0.5:
            return "inside"
        phi = (np.float32(0.5) - total) * np.float32(self.scale)
        region = temp[s[0]:e[0] + 1, s[1]:e[1] + 1, s[2]:e[2] + 1]
        np.minimum(region, phi, out=region)
        return "done"


class _Body:
    """One primitive or a chain of them that together are one body.

    Within a body the leaves combine with an exact minimum: consecutive
    capsules along a stretch share an end ball, and a smooth minimum between
    them would bead the tube at every annotation.  Between bodies the grid
    uses the exact minimum too unless a fillet was asked for.
    """

    def __init__(self, leaves: list[_Leaf], *, area_um2: float) -> None:
        self.leaves = leaves
        self.area_um2 = area_um2


def _smin(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    """Quadratic polynomial smooth minimum with blend radius ``k``."""
    h = np.clip(k - np.abs(a - b), 0.0, k) / k
    return np.minimum(a, b) - h * h * k * 0.25


class _BlockGrid:
    """The blocked, never-materialized fine grid."""

    def __init__(self, voxel: float, origin: np.ndarray, *, block: int, blend_um: float,
                 mode: str = "min", threshold: float = THRESHOLD) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}; got {mode!r}")
        self.mode = mode
        self.threshold = float(threshold)
        self.voxel = float(voxel)
        self.origin = np.asarray(origin, dtype=float)
        self.block = int(block)
        self.blend = float(max(blend_um, 0.0))
        self.bodies: list[_Body] = []
        self.leaf_index: list[list[tuple[np.ndarray, np.ndarray]]] = []
        self.body_blocks: dict[tuple[int, int, int], list[int]] = {}
        # Per block, the (body, leaf) pairs that reach it, in the order they were added:
        # what decides its field, for building again on this grid (block_signature).
        self.block_leaves: dict[tuple[int, int, int], list[tuple[int, int]]] = {}
        self.tris: dict[tuple[int, int, int], tuple[np.ndarray, np.ndarray]] = {}
        self.work = 0
        # The pad must also cover the fillet's reach: two surfaces blend where
        # both distances are within ``blend`` of each other, which can be
        # just outside a leaf's own extent.
        self.pad_um = PAD_VOXELS * self.voxel + self.blend

    def add(self, body: _Body) -> set[tuple[int, int, int]]:
        bid = len(self.bodies)
        self.bodies.append(body)
        index: list[tuple[np.ndarray, np.ndarray]] = []
        keys: set[tuple[int, int, int]] = set()
        B = self.block
        for li, leaf in enumerate(body.leaves):
            lo, hi = leaf.bounds()
            lo_i = np.floor((lo - self.pad_um - self.origin) / self.voxel).astype(int)
            hi_i = np.ceil((hi + self.pad_um - self.origin) / self.voxel).astype(int)
            lo_i = np.maximum(lo_i, 0)
            index.append((lo_i, hi_i))
            if isinstance(leaf, (_FieldLeaf, _CurveLeaf)):
                # Only its near band is resampled; the rest of its box is a
                # min/max over the coarse samples per block.  A raster's
                # blocks are the ones its tiles reach.
                self.work += leaf.work_voxels(self)
            else:
                self.work += int(np.prod(hi_i - lo_i + 1))
            if isinstance(leaf, _CurveLeaf):
                block_keys = sorted(leaf.block_keys(self))
            else:
                ranges = [range(int(lo_i[d]) // B, int(hi_i[d]) // B + 1) for d in range(3)]
                block_keys = [(int(a), int(b), int(c)) for a, b, c in itertools.product(*ranges)]
            for kx, ky, kz in block_keys:
                key = (int(kx), int(ky), int(kz))
                lst = self.body_blocks.setdefault(key, [])
                if not lst or lst[-1] != bid:
                    lst.append(bid)
                self.block_leaves.setdefault(key, []).append((bid, li))
                keys.add(key)
        self.leaf_index.append(index)
        return keys

    def _coords(self, start: np.ndarray, s: np.ndarray, e: np.ndarray) -> np.ndarray:
        # Global integer index times the voxel: a sample on a face two blocks
        # share gets bit-identical coordinates from both, which is what makes
        # the weld exact.
        axes = [self.origin[d] + (start[d] + np.arange(int(s[d]), int(e[d]) + 1)) * self.voxel
                for d in range(3)]
        xx, yy, zz = np.meshgrid(*axes, indexing="ij")
        return np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

    def evaluate(self, key: tuple[int, int, int]) -> np.ndarray | None:
        bids = self.body_blocks.get(key)
        if not bids:
            return None
        B = self.block
        start = np.array(key, dtype=int) * B
        shape = (B + 1, B + 1, B + 1)
        if self.mode == "sum":
            # Potentials superpose; inside is where the sum passes the
            # iso-value, so the contoured quantity is T - sum.
            total = np.zeros(shape, dtype=np.float64)
            for bid in bids:
                for leaf, (lo_i, hi_i) in zip(self.bodies[bid].leaves,
                                              self.leaf_index[bid], strict=True):
                    s = np.maximum(lo_i - start, 0)
                    e = np.minimum(hi_i - start, B)
                    if (s > e).any():
                        continue
                    pot = leaf.potential(self._coords(start, s, e))  # type: ignore[attr-defined]
                    total[s[0]:e[0] + 1, s[1]:e[1] + 1, s[2]:e[2] + 1] += pot.reshape(tuple(e - s + 1))
            return (self.threshold - total).astype(np.float32)
        field = np.full(shape, FAR, dtype=np.float32)
        for bid in bids:
            body = self.bodies[bid]
            temp: np.ndarray | None = None
            tlo = np.zeros(3, dtype=int)
            thi = np.zeros(3, dtype=int)
            for leaf, (lo_i, hi_i) in zip(body.leaves, self.leaf_index[bid], strict=True):
                s = np.maximum(lo_i - start, 0)
                e = np.minimum(hi_i - start, B)
                if (s > e).any():
                    continue
                if temp is None:
                    temp = np.full(shape, FAR, dtype=np.float32)
                    tlo, thi = s.copy(), e.copy()
                else:
                    tlo = np.minimum(tlo, s)
                    thi = np.maximum(thi, e)
                if isinstance(leaf, (_FieldLeaf, _CurveLeaf)):
                    verdict = leaf.stamp(self, start, s, e, temp)
                    if verdict == "inside":
                        # This body is deep inside throughout the block, so the
                        # union is negative everywhere here: no surface.
                        return None
                    continue
                d = leaf.distance(self._coords(start, s, e)).reshape(tuple(e - s + 1))  # type: ignore[attr-defined]
                sub = temp[s[0]:e[0] + 1, s[1]:e[1] + 1, s[2]:e[2] + 1]
                np.minimum(sub, d.astype(np.float32, copy=False), out=sub)
            if temp is None:
                continue
            region = (slice(tlo[0], thi[0] + 1), slice(tlo[1], thi[1] + 1),
                      slice(tlo[2], thi[2] + 1))
            if self.blend <= 0.0:
                np.minimum(field[region], temp[region], out=field[region])
            else:
                field[region] = _smin(field[region], temp[region], self.blend)
        if self.mode == "occupancy":
            # A voxelizer knows only inside and outside: the contour lands on
            # the half-voxel staircase, which the optimizer then smooths.
            return np.where(field <= 0.0, -1.0, 1.0).astype(np.float32)
        return field

    def march(self, key: tuple[int, int, int]) -> None:
        field = self.evaluate(key)
        if field is None or not (float(field.min()) < LEVEL < float(field.max())):
            self.tris.pop(key, None)
            return
        try:
            verts, faces, _n, _v = marching_cubes(field, level=LEVEL)
        except (ValueError, RuntimeError):
            self.tris.pop(key, None)
            return
        xyz = self.origin + (np.array(key, dtype=float) * self.block + verts) * self.voxel
        self.tris[key] = (xyz, faces)

    def march_all(self) -> None:
        keys = list(self.body_blocks)
        for i, key in enumerate(keys):
            report("contouring", i, len(keys))
            self.march(key)
        report("contouring", len(keys), len(keys))

    def mesh(self, index_out: dict | None = None) -> Mesh:
        """All blocks as one mesh, welded along their shared faces.

        ``index_out``, when given, is filled with each block's vertex rows' index in
        the mesh (-1 for a row no face kept).

        Only a vertex lying on a block face can have a twin in the neighbor
        block: marching cubes puts vertices on grid edges, and an edge inside
        a shared face belongs to both blocks.  Such a vertex has an index
        coordinate that is an exact multiple of the block size, so the weld
        deduplicates that small subset (a few percent of the vertices) and
        leaves the rest alone -- a whole-mesh ``unique`` over a million rows
        was half the pathway's memory.
        """
        if not self.tris:
            return EMPTY_MESH
        verts_parts, faces_parts = [], []
        offset = 0
        offsets: dict[tuple[int, int, int], tuple[int, int]] = {}
        for block_key, (v, f) in self.tris.items():
            verts_parts.append(v)
            faces_parts.append(f + offset)
            offsets[block_key] = (offset, len(v))
            offset += len(v)
        verts = np.vstack(verts_parts)
        faces = np.vstack(faces_parts)
        del verts_parts, faces_parts
        idx = (verts - self.origin) / self.voxel / self.block
        on_face = (np.abs(idx - np.rint(idx)) < 1e-6).any(axis=1)
        face_ids = np.flatnonzero(on_face)
        remap = np.arange(len(verts))
        if len(face_ids):
            key = np.round(verts[face_ids] / 1e-6).astype(np.int64)
            _u, first, inverse = np.unique(key, axis=0, return_index=True,
                                           return_inverse=True)
            remap[face_ids] = face_ids[first[np.asarray(inverse).ravel()]]
            faces = remap[faces]
            del key
        keep = ((faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2])
                & (faces[:, 0] != faces[:, 2]))
        faces = faces[keep]
        used = np.zeros(len(verts), dtype=bool)
        used[faces.ravel()] = True
        compact = np.cumsum(used) - 1
        if index_out is not None:
            welded = np.where(used[remap], compact[remap], -1)
            for k, (start, n) in offsets.items():
                index_out[k] = welded[start:start + n]
        del remap
        mesh = Mesh(verts[used], compact[faces])
        return mesh.fix_winding()


# -- building again on the last grid (manual moves) ---------------------------------------------

@dataclass
class GridState:
    """What one whole-cell contour leaves for the next build of the same cell on the
    same grid (2026-10-20, manual moves: "redo the portion of the render where the move
    is"): the grid (voxel, origin, block size, field mode and fillet), each block's
    signature -- the content fingerprints of the leaves that reach into it -- and its
    triangles, both from before the gaps were tied.

    A build handed one (:class:`GridReuse`) contours on the same grid, takes a block's
    triangles from it where the block's signature is unchanged and contours the rest:
    the raw sheet is then exactly what a full contour on that grid gives
    (``tests/unit/test_grid_reuse.py``), only the blocks a move touched having been
    evaluated.  ``ties`` is what its tie step decided, which the next build keeps when
    the moves cannot have changed it (:func:`_reused_ties`).  ``final_tris`` are the
    blocks after the gaps were tied (the same
    arrays but where a tie was marched in), the sheet the smoothing took, and
    ``smoothed`` each block's vertices as the Taubin smoothing left them
    (:func:`taubin_on_grid`, ``smooth_key`` its iterations and weights): the next
    build smooths again only around the blocks that changed.  Decimating still runs
    on the whole surface.
    """

    voxel: float
    origin: np.ndarray
    block: int
    blend: float
    mode: str
    threshold: float
    work: int
    signatures: dict = field(default_factory=dict)
    tris: dict = field(default_factory=dict)
    final_tris: dict = field(default_factory=dict)
    smoothed: dict = field(default_factory=dict)
    smooth_key: tuple | None = None
    #: The gaps it tied (:func:`_tie_record`): the tie settings, the pieces the untied
    #: sheet fell into (each one's blocks, and its vertices but for the largest's), the
    #: tie segments and the pieces left after tying.  None when it kept none.
    ties: dict | None = None

    def nbytes(self) -> int:
        """Roughly what it holds, bytes: the triangles, the tied blocks' own and the
        smoothed vertices."""
        held = {id(v): v.nbytes + f.nbytes for v, f in self.tris.values()}
        for v, f in self.final_tris.values():
            held.setdefault(id(v), v.nbytes + f.nbytes)
        return int(sum(held.values()) + sum(a.nbytes for a in self.smoothed.values()))


@dataclass
class GridReuse:
    """One build's say in grid reuse: ``state``, a grid to build on (None: a full
    contour); ``keep``, hand this build's own :class:`GridState` back in ``out``;
    ``recontour_all``, build on ``state``'s grid but contour every block (what a reused
    build is checked against).  ``stats`` says what happened: blocks, contoured, reused."""

    state: GridState | None = None
    keep: bool = False
    recontour_all: bool = False
    out: GridState | None = None
    stats: dict = field(default_factory=dict)
    #: The finished sheet's vertex index of each block's own vertex rows (-1: none),
    #: and its vertex count: how :func:`taubin_on_grid` finds the blocks in the mesh.
    index: dict | None = None
    n_vertices: int = 0
    calls: int = 0


_GRID_REUSE: contextvars.ContextVar[GridReuse | None] = contextvars.ContextVar(
    "sbfsem_grid_reuse", default=None)


@contextlib.contextmanager
def grid_reuse(ctx: GridReuse | None) -> Iterator[GridReuse | None]:
    """Let the whole-cell contour of the builds inside read and fill ``ctx``."""
    token = _GRID_REUSE.set(ctx)
    try:
        yield ctx
    finally:
        with contextlib.suppress(ValueError):
            _GRID_REUSE.reset(token)


def _hash_value(h, value: Any) -> None:
    a = np.ascontiguousarray(np.asarray(value))
    h.update(str(a.dtype).encode())
    h.update(str(a.shape).encode())
    h.update(a.tobytes())


def leaf_fingerprint(leaf) -> bytes | None:
    """A leaf's content as 16 bytes: two leaves with the same fingerprint give the same
    field everywhere.  None for a kind of leaf this does not know (never reused)."""
    fp = getattr(leaf, "_grid_fingerprint", None)
    if fp is not None:
        return fp
    h = hashlib.blake2b(digest_size=16)
    h.update(type(leaf).__name__.encode())
    if isinstance(leaf, _FieldLeaf):
        parts: tuple = (leaf.values, leaf.field.lo, leaf.field.voxel, leaf.field.level)
    elif isinstance(leaf, _CurveLeaf):
        v = leaf.volume
        parts = (v.tiles, v.keys, v.sections, leaf.spacing, leaf.origin, tuple(v.shape), v.tile,
                 tuple(v.tile_grid))
    elif dataclasses.is_dataclass(leaf) and not isinstance(leaf, type):
        parts = tuple(getattr(leaf, f.name) for f in dataclasses.fields(leaf))
    else:
        return None
    for part in parts:
        _hash_value(h, part)
    fp = h.digest()
    with contextlib.suppress(AttributeError, TypeError):
        leaf._grid_fingerprint = fp
    return fp


def block_signature(grid: _BlockGrid, key: tuple[int, int, int]) -> tuple | None:
    """What decides a block's field: for each body registered in it, in the order the
    block evaluates them (:meth:`_BlockGrid.evaluate`), the fingerprints of the leaves
    that reach into the block -- the ones it evaluates.  A thin stretch is one body of
    many round cones, so a move changes a few of its leaves and only the blocks they
    reach.  An outline raster is fingerprinted by the tiles each block reads
    (:meth:`_CurveLeaf.block_fingerprint`), and a cluster field by the coarse
    values and spline coefficients each block reads (:meth:`_FieldLeaf.block_fingerprint`).
    None when a leaf has no fingerprint."""
    out: list = []
    current = -1
    for bid, li in grid.block_leaves.get(key, []):
        leaf = grid.bodies[bid].leaves[li]
        local = getattr(leaf, "block_fingerprint", None)
        fp = local(grid, key) if local is not None else leaf_fingerprint(leaf)
        if fp is None:
            return None
        if bid != current:
            out.append(bid)          # a body starts: its leaves follow (the grouping a fillet reads)
            current = bid
        out.append(fp)
    # The body indices themselves differ between builds when bodies come and go
    # elsewhere: only the grouping counts, so each is a marker.
    return tuple(b"|" if isinstance(x, int) else x for x in out)


def _grid_state(grid: _BlockGrid, signatures: dict | None = None) -> GridState:
    """The grid as contoured, before any tie: its blocks' signatures and triangles."""
    sigs = signatures if signatures is not None else {key: block_signature(grid, key) for key in grid.body_blocks}
    return GridState(
        voxel=grid.voxel, origin=grid.origin.copy(), block=grid.block, blend=grid.blend,
        mode=grid.mode, threshold=grid.threshold, work=int(grid.work),
        signatures=sigs, tris=dict(grid.tris),
    )


def _tie_record(pieces: list[Mesh], segments: list, grid: _BlockGrid, params: tuple) -> dict:
    """What a tie step decided, for the next build on this grid (:func:`_reused_ties`):
    each piece's blocks, the vertices of all but the largest, and the segments."""
    span = grid.voxel * grid.block
    big = max(range(len(pieces)), key=lambda i: len(pieces[i].vertices)) if pieces else -1
    recs = []
    for i, piece in enumerate(pieces):
        ijk = np.floor((piece.vertices - grid.origin) / span).astype(np.int64)
        lo = ijk.min(axis=0)
        size = ijk.max(axis=0) - lo + 1
        code = np.unique(((ijk[:, 0] - lo[0]) * size[1] + (ijk[:, 1] - lo[1])) * size[2] + (ijk[:, 2] - lo[2]))
        a, rest = np.divmod(code, size[1] * size[2])
        b, c = np.divmod(rest, size[2])
        keys = np.column_stack([a + lo[0], b + lo[1], c + lo[2]])
        recs.append({"blocks": frozenset(tuple(int(v) for v in k) for k in keys),
                     "vertices": None if i == big else np.array(piece.vertices, dtype=float)})
    return {"params": params, "pieces": recs, "host": -1,
            "segments": [(np.array(a, dtype=float), np.array(b, dtype=float)) for a, b in segments],
            "components": None}


def _links(tris: dict, inner: set, outer: set) -> tuple[frozenset, bool] | None:
    """How the triangles of the blocks ``inner`` join the blocks ``outer`` around them:
    the vertices they share with ``outer``, grouped by which piece of ``inner`` holds
    them, and whether a piece of ``inner`` touches none of them."""
    keys = [k for k in sorted(inner) if k in tris]
    if not keys:
        return frozenset(), False
    verts = np.vstack([tris[k][0] for k in keys])
    offsets = np.cumsum([0] + [len(tris[k][0]) for k in keys])
    faces = np.vstack([tris[k][1] + offsets[i] for i, k in enumerate(keys)])
    q = np.round(verts / 1e-6).astype(np.int64)          # the weld's own rule (_BlockGrid.mesh)
    uniq, inv = np.unique(q, axis=0, return_inverse=True)
    inv = np.asarray(inv).ravel()
    f = inv[faces]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    n = len(uniq)
    if not len(f):
        return frozenset(), False
    graph = coo_matrix((np.ones(3 * len(f), dtype=np.int8),
                        (np.concatenate([f[:, 0], f[:, 1], f[:, 2]]),
                         np.concatenate([f[:, 1], f[:, 2], f[:, 0]]))), shape=(n, n))
    _n, labels = connected_components(graph, directed=False)
    used = np.zeros(n, dtype=bool)
    used[f.ravel()] = True
    around = [k for k in sorted(outer) if k in tris]
    if around:
        shared_q = np.unique(np.round(np.vstack([tris[k][0] for k in around]) / 1e-6).astype(np.int64), axis=0)
        both = np.vstack([uniq, shared_q])
        _u, tag = np.unique(both, axis=0, return_inverse=True)
        tag = np.asarray(tag).ravel()
        in_outer = np.zeros(len(_u), dtype=bool)
        in_outer[tag[n:]] = True
        on_edge = in_outer[tag[:n]] & used
    else:
        on_edge = np.zeros(n, dtype=bool)
    groups: dict[int, list] = {}
    for i in np.flatnonzero(on_edge):
        groups.setdefault(int(labels[i]), []).append(tuple(int(c) for c in uniq[i]))
    island = bool(set(np.unique(labels[used]).tolist()) - set(groups))
    return frozenset(frozenset(g) for g in groups.values()), island


def _reused_ties(grid: _BlockGrid, old: GridState, changed: set, params: tuple) -> dict | None:
    """The last build's tie step, when the moves cannot have changed what it decides;
    None otherwise (the caller ties the gaps afresh).

    It holds when the untied sheet falls into the same pieces -- the triangles around
    the changed blocks join the unchanged sheet around them in the same groups, and
    no piece lies wholly among them -- the change is in one piece, and every other
    piece is farther from the changed blocks than the longest tie (``tie_um``): then
    no gap a tie could close has moved, so the same ties are found (to the
    subsample the tie search draws, :func:`~sbfsem.geometry.cylinder._tie_segments`)."""
    rec = old.ties
    if rec is None or tuple(rec["params"]) != tuple(params) or not changed:
        return None
    if not _local_enough(len(changed), len(grid.tris)):
        return None
    if rec["components"] is None:
        return None
    every = set(grid.tris) | set(old.tris)
    inner = _dilate(changed, 1, every)
    hosts = [i for i, p in enumerate(rec["pieces"]) if p["blocks"] & inner]
    if len(hosts) != 1:
        return None
    # The cheap test first: every other piece out of a tie's reach of the change.
    span = grid.voxel * grid.block
    reach = float(params[0])
    near_blocks = int(np.ceil(reach / span)) + 2
    moved = np.array(sorted(changed), dtype=np.int64)
    boxes_lo = grid.origin + moved * span
    for i, piece in enumerate(rec["pieces"]):
        if i == hosts[0]:
            continue
        keys = np.array(sorted(piece["blocks"]), dtype=np.int64)
        apart = np.abs(keys[:, None, :] - moved[None, :, :]).max(axis=2).min()
        if apart >= near_blocks:
            continue
        verts = piece["vertices"]
        if verts is None:
            return None
        gap = np.inf
        for lo in boxes_lo:
            d = np.sqrt((np.maximum(np.maximum(lo - verts, verts - (lo + span)), 0.0) ** 2).sum(axis=1))
            gap = min(gap, float(d.min()))
        if gap <= reach:
            return None
    # Then the pieces themselves: the same around the change.
    outer = _dilate(inner, 1, every) - inner
    before = _links(old.tris, inner, outer)
    after = _links(grid.tris, inner, outer)
    if before is None or after is None or before[1] or after[1] or before[0] != after[0]:
        return None
    return {**rec, "host": hosts[0]}


#: More of a sheet's blocks changed than this share: smoothing it all at once, and
#: searching its gaps afresh, is the cheaper way (a moved soma disc of c1178 changes
#: a third of them, where the local path cost 0.73 s against the whole sheet's 0.55).
LOCAL_SMOOTH_MAX_SHARE = 0.15
#: A change of this many blocks or fewer is always taken locally, whatever its share.
LOCAL_SMOOTH_MIN_BLOCKS = 16


def _local_enough(changed: int, total: int) -> bool:
    """Whether a change of ``changed`` blocks of ``total`` is cheaper to redo locally."""
    return changed <= max(LOCAL_SMOOTH_MAX_SHARE * total, LOCAL_SMOOTH_MIN_BLOCKS)


def _dilate(keys, by: int, within) -> set:
    """``keys`` and every block up to ``by`` blocks away (26-neighborhood), of those in ``within``."""
    steps = range(-by, by + 1)
    out = set()
    for a, b, c in keys:
        for i in steps:
            for j in steps:
                for k in steps:
                    key = (a + i, b + j, c + k)
                    if key in within:
                        out.add(key)
    return out


def taubin_on_grid(mesh: Mesh, iterations: int, lam: float = 0.5, mu: float = -0.53) -> Mesh | None:
    """:meth:`Mesh.taubin` of the sheet the whole-cell contour just made, smoothing again
    only around the blocks that changed when the build was made on the last one's grid
    (:class:`GridReuse`); None when this is not that sheet, and the caller smooths it
    whole.

    A Taubin iteration moves a vertex by its neighbors' mean, twice, so after
    ``iterations`` a vertex depends only on the sheet within ``2 * iterations`` edges
    of it, and a marching-cubes edge is at most a voxel's diagonal.  The blocks whose
    triangles are not the last build's arrays (contoured again, tied, gone) are the
    change; the vertices within a margin of blocks around it are smoothed again, on
    the faces within twice that margin, and every other vertex keeps the place the
    last build smoothed it to.  The neighbor sums run in vertex-index order
    (:meth:`Mesh._neighbor_mean`), and the part is numbered in the sheet's own order,
    so a vertex smoothed again sums exactly as a smoothing of the whole sheet would.
    The positions it keeps were summed in the last sheet's order, which is the same
    wherever the blocks around a vertex are in the same order: in all, the same as
    smoothing the whole sheet to within float rounding (``tests/unit/test_grid_reuse.py``).
    """
    ctx = _GRID_REUSE.get()
    if (ctx is None or ctx.index is None or iterations <= 0 or mesh.is_empty
            or len(mesh.vertices) != ctx.n_vertices or not (ctx.keep or ctx.state is not None)):
        return None
    key_s = (int(iterations), float(lam), float(mu))
    index = ctx.index
    old = ctx.state if ctx.stats.get("reused") else None
    smoothed = None
    if (old is not None and old.smooth_key == key_s and old.smoothed and old.final_tris
            and not ctx.recontour_all):
        new_tris = ctx.out.final_tris if ctx.out is not None else None
        if new_tris:
            changed = {k for k, arrays in new_tris.items() if old.final_tris.get(k) is not arrays}
            changed |= {k for k in old.final_tris if k not in new_tris}
            if _local_enough(len(changed), len(new_tris)):
                smoothed = _taubin_around(mesh, index, old, new_tris, changed, key_s)
    meta = dict(mesh.meta)
    if smoothed is None:
        smoothed = mesh.taubin(iterations, lam, mu).vertices
        ctx.stats["smoothed_again"] = len(smoothed)
    elif "voxel2_reused_blocks" in meta:
        meta["voxel2_smoothed_again"] = int(ctx.stats.get("smoothed_again", 0))
    if ctx.out is not None:
        ctx.out.smooth_key = key_s
        ctx.out.smoothed = {k: np.where((rows >= 0)[:, None], smoothed[np.maximum(rows, 0)], np.nan)
                            for k, rows in index.items()}
    return Mesh(np.asarray(smoothed), mesh.faces.copy(),
                None if mesh.vertex_data is None else mesh.vertex_data.copy(), meta)


def _taubin_around(mesh: Mesh, index: dict, old: GridState, new_tris: dict, changed: set,
                   key_s: tuple) -> np.ndarray | None:
    """The smoothed vertices of ``mesh``: the last build's where nothing near changed,
    smoothed again around ``changed`` (:func:`taubin_on_grid`); None when that cannot
    be done here (the passes reach past a block, or a vertex is left without a place:
    the caller then smooths it all)."""
    iterations, lam, mu = key_s
    hops = 2 * iterations
    voxel = float(old.voxel)
    edge = float(np.sqrt(3.0)) * voxel                  # the longest a marching-cubes edge can be
    near_um = (hops + 1) * edge                         # a vertex this close to a change is smoothed again
    around_um = near_um + (hops + 1) * edge             # and on every face this close
    span = old.block * voxel
    if around_um >= span:
        return None
    n = len(mesh.vertices)
    out = np.full((n, 3), np.nan)
    for key, rows in index.items():
        if key in changed:
            continue
        kept = old.smoothed.get(key)
        if kept is None or len(kept) != len(rows):
            return None
        ok = rows >= 0
        out[rows[ok]] = kept[ok]
    # Distance from each vertex of the blocks around a change to the changed block.
    gap = np.full(n, np.inf)
    blocks = set(new_tris)
    origin = np.asarray(old.origin, dtype=float)
    verts = mesh.vertices
    for key in changed:
        lo = origin + np.asarray(key, dtype=float) * span
        hi = lo + span
        rows = [index[k] for k in _dilate([key], 1, blocks) if k in index]
        if not rows:
            continue
        cand = np.unique(np.concatenate(rows))
        cand = cand[cand >= 0]
        p = verts[cand]
        d = np.sqrt((np.maximum(np.maximum(lo - p, p - hi), 0.0) ** 2).sum(axis=1))
        np.minimum.at(gap, cand, d)
    redo = gap <= near_um
    within = gap <= around_um
    faces = mesh.faces
    part = faces[within[faces].any(axis=1)]
    if not len(part):
        return out if not np.isnan(out).any() else None
    sub = np.unique(part)                                 # the sheet's own order
    local = np.searchsorted(sub, part)
    piece = Mesh(verts[sub], local).taubin(iterations, lam, mu).vertices
    redo_here = redo[sub]
    out[sub[redo_here]] = piece[redo_here]
    if np.isnan(out).any():
        return None
    ctx = _GRID_REUSE.get()
    if ctx is not None:
        ctx.stats["smoothed_again"] = int(redo_here.sum())
    return out


def split_components(mesh: Mesh) -> list[Mesh]:
    """The mesh's topological pieces (connected components of its triangles)."""
    if mesh.is_empty:
        return []
    faces = mesh.faces
    n = mesh.n_vertices
    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]]).astype(np.int32)
    cols = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]]).astype(np.int32)
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    del rows, cols
    n_comp, labels = connected_components(graph, directed=False)
    del graph
    face_label = labels[faces[:, 0]]
    out: list[Mesh] = []
    for k in range(n_comp):
        sel = face_label == k
        if not sel.any():
            continue
        keep = np.zeros(n, dtype=bool)
        keep[np.unique(faces[sel])] = True
        remap = -np.ones(n, dtype=np.int64)
        remap[keep] = np.arange(int(keep.sum()))
        out.append(Mesh(mesh.vertices[keep], remap[faces[sel]]))
    return out


def _capsule_area(a: np.ndarray, b: np.ndarray, ra: float, rb: float) -> float:
    return float(np.pi * (ra + rb) * np.linalg.norm(b - a))


def _field_area(sf: SampledField) -> float:
    """Rough surface area of a sampled field from its sign changes."""
    inside = (sf.field - sf.level) < 0.0
    crossings = sum(int(np.count_nonzero(np.diff(inside, axis=d))) for d in range(3))
    # A surface cuts ~1.5-2 axis edges per voxel of area, depending on tilt.
    return crossings * sf.voxel ** 2 / 1.7


def auto_voxel_um(thin_radii, area_um2: float, *,
                  per_radius: float = VOXELS_PER_THIN_RADIUS,
                  percentile: float = THIN_RADIUS_PERCENTILE,
                  max_raw_faces: int = MAX_RAW_FACES) -> float:
    """The fine voxel a cell's thin annotations ask for, within the face budget."""
    radii = np.asarray(thin_radii, dtype=float)
    radii = radii[np.isfinite(radii) & (radii > 0)]
    r = float(np.percentile(radii, percentile)) if len(radii) else 0.3
    v = r / max(per_radius, 0.5)
    if area_um2 > 0:
        v = max(v, float(np.sqrt(FACES_PER_SURFACE_VOXEL * area_um2 / max_raw_faces)))
    return float(np.clip(v, MIN_VOXEL_UM, MAX_VOXEL_UM))


def _bodies_from_items(items, *, lens_h: float, mode: str = "min",
                       support: float = SUPPORT_FACTOR,
                       hull_tilt_deg: float = 0.0) -> tuple[list[_Body], list[float]]:
    """Turn the pathway's ordered pieces into bodies; also the thin radii.

    In ``sum`` mode every piece becomes convolution kernels: a stretch is one
    :class:`SegmentKernel` per consecutive pair, a link one segment at the
    link radius, a coin a :class:`PointKernel` (a sphere -- the paper's
    skeletons have no stranded discs).  Sampled fields have no potential
    and are refused there.
    """
    bodies: list[_Body] = []
    thin: list[float] = []
    kernels = mode == "sum"
    for item in items:
        kind = item[0]
        if kind == "field":
            if kernels:
                raise TypeError("convolution surfaces take skeleton pieces, not sampled fields")
            sf: SampledField = item[1]
            bodies.append(_Body([_FieldLeaf(sf)], area_um2=_field_area(sf)))
        elif kind == "curve":
            # A closed-curve raster and the area of its own surface (the
            # outline half Hybrid would have contoured on its own).
            if kernels:
                raise TypeError("convolution surfaces take skeleton pieces, not a curve raster")
            bodies.append(_Body([_CurveLeaf(item[1])], area_um2=float(item[2])))
        elif kind == "stretch":
            pts = np.asarray(item[1], dtype=float).reshape(-1, 3)
            rad = np.abs(np.asarray(item[2], dtype=float).ravel())
            if kernels:
                # Thin radii are read before merging: the grid is sized for
                # what was annotated, the kernels for what is affordable.
                thin.extend(float(r) for r in rad if np.isfinite(r) and r > 0)
                pts, rad = merge_short_segments(pts, rad, support=support)
            leaves: list[_Leaf] = []
            area = 0.0
            hulls = hull_pairs(pts, rad, hull_tilt_deg) if not kernels else np.zeros(len(pts), bool)
            for i in range(len(pts) - 1):
                if not (np.isfinite(pts[i]).all() and np.isfinite(pts[i + 1]).all()
                        and np.isfinite(rad[i]) and np.isfinite(rad[i + 1])):
                    continue
                if np.linalg.norm(pts[i + 1] - pts[i]) <= 1e-9 and abs(rad[i] - rad[i + 1]) < 1e-9:
                    continue
                if kernels:
                    leaves.append(SegmentKernel.from_pair(pts[i], pts[i + 1], float(rad[i]),
                                                          float(rad[i + 1]), support=support))
                elif hulls[i]:
                    leaves.append(DiscHull(pts[i], pts[i + 1], float(rad[i]), float(rad[i + 1])))
                else:
                    leaves.append(Capsule(pts[i], pts[i + 1], float(rad[i]), float(rad[i + 1])))
                area += _capsule_area(pts[i], pts[i + 1], float(rad[i]), float(rad[i + 1]))
            if not kernels:
                thin.extend(float(r) for r in rad if np.isfinite(r) and r > 0)
            if leaves:
                bodies.append(_Body(leaves, area_um2=area))
        elif kind == "link":
            a, b, r = np.asarray(item[1], float), np.asarray(item[2], float), float(item[3])
            link_leaf: _Leaf = (SegmentKernel.from_pair(a, b, r, r, support=support) if kernels
                                else Capsule(a, b, r, r))
            bodies.append(_Body([link_leaf], area_um2=_capsule_area(a, b, r, r)))
        elif kind == "coin":
            c, r = np.asarray(item[1], dtype=float), abs(float(item[2]))
            coin_leaf: _Leaf = (PointKernel.from_disc(c, r, support=support) if kernels
                                else Lens(c, r, float(lens_h)))
            bodies.append(_Body([coin_leaf],
                                area_um2=2.0 * np.pi * r * r + 2.0 * np.pi * r * 2 * lens_h))
        elif kind == "sphere":
            # A soma as a skeleton tool sees it: one ball of the mean radius.
            c, r = np.asarray(item[1], dtype=float), abs(float(item[2]))
            ball: _Leaf = (PointKernel.from_disc(c, r, support=support) if kernels
                           else Capsule(c, c, r, r))
            bodies.append(_Body([ball], area_um2=4.0 * np.pi * r * r))
        elif kind == "mesh":
            raise TypeError("unified_mesh needs fields, not meshes; assemble with as_fields=True")
        else:
            raise ValueError(f"unknown piece kind {kind!r}")
    return bodies, thin


@dataclass(frozen=True)
class VoxelPlan:
    """The fine voxel a build contours at, decided before anything is contoured.

    ``voxel_um`` is the voxel used; ``typed_um`` what was typed (or the
    annotations' own, for an automatic Fine voxel); ``asked_um`` that held to
    the floor and the ceiling; ``reason`` why ``voxel_um`` is coarser than
    ``typed_um`` (``"floor"``, ``"faces"`` for the raw-triangle budget,
    ``"work"`` for the evaluation budget; empty when it is not).  ``raw_budget``
    is the raw-triangle budget after an outline half widened it, ``work`` the
    field evaluations at ``voxel_um`` and ``work_budget`` what the build may
    spend (0 for both from :func:`_size_voxel` alone)."""

    voxel_um: float
    typed_um: float
    asked_um: float
    floor_um: float
    coarsened: bool
    reason: str
    area_um2: float
    raw_budget: int
    work: int = 0
    work_budget: int = 0

    @property
    def raw_faces(self) -> int:
        """The raw triangles marching cubes is expected to draw at ``voxel_um``."""
        return int(FACES_PER_SURFACE_VOXEL * self.area_um2 / self.voxel_um ** 2)


def _size_voxel(bodies: list[_Body], thin: list[float], *, voxel_um: float, max_raw_faces: int,
                max_auto_um: float | None, curve_raw_faces: int) -> VoxelPlan:
    """The voxel the annotations and the raw-triangle budget ask for (no work budget yet)."""
    area = float(sum(b.area_um2 for b in bodies))
    curve_area = float(sum(b.area_um2 for b in bodies
                           if any(isinstance(leaf, _CurveLeaf) for leaf in b.leaves)))
    if curve_area > 0:
        rest = max(area - curve_area, 1e-9)
        widen = max(int(curve_raw_faces), int(max_raw_faces * curve_area / rest))
        max_raw_faces = int(min(max_raw_faces + widen, MAX_RAW_FACES))
    # What the geometry asks for, before any budget: the user's voxel, or the
    # one the thin annotations want.
    if voxel_um and voxel_um > 0:
        typed = float(voxel_um)
        floor = TYPED_MIN_VOXEL_UM
        asked = float(np.clip(typed, floor, MAX_VOXEL_UM))
    else:
        asked = typed = auto_voxel_um(thin, 0.0)
        if max_auto_um and max_auto_um > 0:
            # A raster in the field (Hybrid's outlines) is not to be contoured
            # coarser than it was drawn.
            asked = typed = float(max(min(asked, max_auto_um), MIN_VOXEL_UM))
        floor = MIN_VOXEL_UM
    # The raw-face budget applies to both.
    v_faces = float(np.sqrt(FACES_PER_SURFACE_VOXEL * max(area, 0.0) / max_raw_faces))
    voxel = float(np.clip(max(asked, v_faces), floor, MAX_VOXEL_UM))
    # Coarser than what was typed counts, the floor's raise of a value under
    # it included: a 0.01 asked for came out as 0.04 and said nothing.
    coarsened = voxel > typed + 1e-9
    # Why, for the page to say (2026-10-16): the raw-face budget, else the floor.
    # The work budget overrides both when it steps in (_fit_work).
    reason = ("faces" if v_faces > asked + 1e-9 else "floor") if coarsened else ""
    return VoxelPlan(voxel, typed, asked, floor, coarsened, reason, area, int(max_raw_faces))


def _grid_maker(bodies: list[_Body], lo_all: np.ndarray, *, blend_um: float, block: int,
                mode: str) -> Callable[[float], _BlockGrid]:
    """A function making the whole-cell grid at a voxel, every body added (nothing evaluated)."""
    def build(v: float) -> _BlockGrid:
        pad = PAD_VOXELS * v + max(blend_um, 0.0)
        origin = np.floor((lo_all - pad) / v) * v - 2.0 * v
        grid = _BlockGrid(v, origin, block=block, blend_um=blend_um, mode=mode)
        for body in bodies:
            grid.add(body)
        return grid
    return build


def _fit_work(build: Callable[[float], _BlockGrid], grid: _BlockGrid, voxel: float,
              limit: int) -> tuple[_BlockGrid, float, bool]:
    """Coarsen ``voxel`` until the grid's field evaluations fit ``limit``: the grid,
    the voxel, and whether the work budget coarsened it."""
    worked = False
    for _ in range(4):
        if grid.work <= limit:
            break
        # Work scales roughly with 1/voxel^3 for the padded boxes of thin
        # geometry; step and re-measure rather than trust the exponent.
        voxel = float(min(voxel * (grid.work / limit) ** (1.0 / 3.0) * 1.05, MAX_VOXEL_UM))
        worked = True
        grid = build(voxel)
        if voxel >= MAX_VOXEL_UM:
            break
    return grid, voxel, worked


def field_bodies(items, *, mode: str = "min", lens_h: float = 0.1,
                 support: float = SUPPORT_FACTOR,
                 hull_tilt_deg: float = 0.0) -> tuple[list[_Body], list[float]]:
    """The bodies and thin radii :func:`unified_mesh` would make of ``items``,
    for :func:`plan_voxel` to size more than one Fine voxel and Face cap from."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}; got {mode!r}")
    return _bodies_from_items(items, lens_h=lens_h, mode=mode, support=support,
                              hull_tilt_deg=float(hull_tilt_deg))


def plan_voxel(bodies: list[_Body], thin: list[float], *, voxel_um: float = 0.0,
               max_raw_faces: int = MAX_RAW_FACES, max_work: int | None = None,
               blend_um: float = 0.0, block: int = BLOCK, mode: str = "min",
               max_auto_um: float | None = None, curve_raw_faces: int = 0) -> VoxelPlan | None:
    """The voxel :func:`unified_mesh` would contour ``bodies`` at, and why, without
    contouring: the same sizing, then the grid made (blocks counted, no field
    evaluated) and coarsened until its work fits, as a build does.  ``None``
    when there is nothing to contour.  What the render estimate says about a
    Fine voxel and a Face cap before a build (``service.estimate``)."""
    if not bodies:
        return None
    size = _size_voxel(bodies, thin, voxel_um=voxel_um, max_raw_faces=max_raw_faces,
                       max_auto_um=max_auto_um, curve_raw_faces=curve_raw_faces)
    lo_all = np.min([leaf.bounds()[0] for b in bodies for leaf in b.leaves], axis=0)
    build = _grid_maker(bodies, lo_all, blend_um=blend_um, block=block, mode=mode)
    limit = work_budget(size.raw_budget) if max_work is None else int(max_work)
    grid, voxel, worked = _fit_work(build, build(size.voxel_um), size.voxel_um, limit)
    return dataclasses.replace(
        size, voxel_um=voxel, coarsened=size.coarsened or worked,
        reason="work" if worked else size.reason, work=int(grid.work), work_budget=int(limit))


def unified_mesh(
    items,
    *,
    voxel_um: float = 0.0,
    blend_um: float = 0.0,
    tie_um: float = 20.0,
    lens_h: float = 0.1,
    block: int = BLOCK,
    max_raw_faces: int = MAX_RAW_FACES,
    max_work: int | None = None,
    mode: str = "min",
    support: float = SUPPORT_FACTOR,
    attached: Callable[[Mesh], bool] | None = None,
    max_auto_um: float | None = None,
    curve_raw_faces: int = 0,
    hull_tilt_deg: float = 0.0,
) -> Mesh:
    """Contour the union of a cell's pieces as one surface.

    ``items`` is the ordered piece list :func:`~sbfsem.geometry.cylinder._assemble_pieces`
    produces with ``as_fields=True``: ``("field", SampledField)``,
    ``("stretch", points, radii)``, ``("link", a, b, radius)`` and
    ``("coin", center, radius)``; the Hybrid method adds its outline half as
    ``("curve", CurveVolume, area_um2)`` (:class:`_CurveLeaf`).  ``voxel_um``
    of 0 sizes the grid from the thin annotations (:func:`auto_voxel_um`; no
    coarser than ``max_auto_um`` when that is given); an explicit value is
    honored within the work budget.  An outline half widens the raw budget by
    what it needs (``curve_raw_faces``, its own triangles and a margin) or by
    its share of the area, whichever is more, so the discs keep the grid they
    would have had alone.  ``blend_um`` > 0 fillets every junction between
    bodies with a smooth minimum of that radius.  Gaps between the surface's
    topological pieces up to ``tie_um`` are closed by stamping tie capsules
    into the field and re-marching only the blocks they touch, so a tie is
    part of the one surface rather than a wire laid over it.  ``hull_tilt_deg``
    > 0 draws a thin stretch's pairs of discs where the process runs within
    that many degrees of vertical as :class:`DiscHull` (Exact tubes °,
    :func:`hull_pairs`), so each disc is the tube's slice at its section; it
    does nothing in ``sum`` mode, whose stretches are kernels.

    The returned mesh carries ``meta['voxel2_um']`` (the voxel actually used),
    ``meta['voxel2_asked']`` (the one typed, or the annotations' own when
    ``voxel_um`` is 0), ``meta['voxel2_coarsened']`` (a budget forced it
    coarser than asked or than the annotations wanted, or the floor raised a
    smaller value that was typed: ``TYPED_MIN_VOXEL_UM``) and ``meta['voxel2_reason']`` (why:
    ``"floor"``, ``"faces"`` for the raw-face budget, ``"work"`` for the
    evaluation budget; empty when it was not), ``meta['voxel2_raw_faces']``,
    ``meta['voxel2_blocks']``, ``meta['voxel2_ties']`` and
    ``meta['voxel2_components']`` (pieces left after tying), plus ``meta['field_mode']``.

    ``mode`` picks the field (see :data:`MODES`); ``support`` is the
    convolution kernel's radius per unit of neurite radius (``sum`` mode).

    ``max_raw_faces`` is the raw-triangle budget (:func:`raw_budget` makes it
    from the face cap) and ``max_work`` the evaluation budget, which is
    :func:`work_budget` of the raw budget unless given.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}; got {mode!r}")
    bodies, thin = _bodies_from_items(items, lens_h=lens_h, mode=mode, support=support,
                                      hull_tilt_deg=float(hull_tilt_deg))
    if not bodies:
        return EMPTY_MESH
    size = _size_voxel(bodies, thin, voxel_um=voxel_um, max_raw_faces=max_raw_faces,
                       max_auto_um=max_auto_um, curve_raw_faces=curve_raw_faces)
    area, max_raw_faces, typed = size.area_um2, size.raw_budget, size.typed_um
    voxel, coarsened, reason = size.voxel_um, size.coarsened, size.reason
    lo_all = np.min([leaf.bounds()[0] for b in bodies for leaf in b.leaves], axis=0)
    build = _grid_maker(bodies, lo_all, blend_um=blend_um, block=block, mode=mode)

    # The evaluation budget follows the raw budget above 3 million (work_budget).
    limit = work_budget(max_raw_faces) if max_work is None else int(max_work)

    # Manual moves: build on the grid the last build of this cell left (GridReuse), when
    # it is the same kind of grid and still holds every body, so only the blocks a move
    # touched are contoured again.  One whole-cell contour a build; a second asks for none.
    ctx = _GRID_REUSE.get()
    reuse: GridState | None = None
    if ctx is not None:
        ctx.calls += 1
        if ctx.calls > 1:
            ctx = None
    if ctx is not None and ctx.state is not None:
        st = ctx.state
        if (st.block == int(block) and st.mode == mode and abs(st.blend - max(blend_um, 0.0)) < 1e-12
                and st.voxel > 0):
            pad = PAD_VOXELS * st.voxel + max(blend_um, 0.0)
            wanted = np.floor((lo_all - pad) / st.voxel) * st.voxel - 2.0 * st.voxel
            # The old origin holds every body when none reaches below it.
            if np.all(wanted >= st.origin - 1e-9):
                reuse = st

    report("building the field")
    grid = None
    if reuse is not None:
        grid = _BlockGrid(reuse.voxel, reuse.origin, block=block, blend_um=blend_um, mode=mode)
        for body in bodies:
            grid.add(body)
        if grid.work > max(limit, reuse.work) * 1.25:
            # The moves made the cell much more work than its grid was sized for: start over.
            grid, reuse = None, None
        else:
            voxel = reuse.voxel
    if grid is None:
        grid = build(voxel)
    if reuse is None:
        grid, voxel, worked = _fit_work(build, grid, voxel, limit)
        if worked:
            coarsened, reason = True, "work"
    if coarsened:
        log.warning("voxel2: contouring at %.3f um (%s voxel evaluations, ~%.0f um2 of "
                    "surface) -- coarser than asked",
                    voxel, f"{grid.work:,}", area)

    if reuse is None or (ctx is not None and ctx.recontour_all):
        grid.march_all()
        contoured = len(grid.body_blocks)
    else:
        # The blocks whose bodies are as they were keep their triangles, in the order a
        # full contour would put them; the others are contoured.
        keys = list(grid.body_blocks)
        contoured = 0
        signatures = {}
        for i, key in enumerate(keys):
            report("contouring", i, len(keys))
            sig = signatures[key] = block_signature(grid, key)
            if sig is not None and reuse.signatures.get(key) == sig:
                old_tris = reuse.tris.get(key)
                if old_tris is not None:
                    grid.tris[key] = old_tris
            else:
                grid.march(key)
                contoured += 1
        report("contouring", len(keys), len(keys))
    if ctx is not None:
        ctx.stats = {"reused": reuse is not None, "blocks": len(grid.body_blocks), "contoured": contoured}
        if ctx.keep:
            ctx.out = _grid_state(grid, signatures if reuse is not None and not ctx.recontour_all else None)
    # Where each block's vertices are in the sheet, for the smoothing to come (taubin_on_grid).
    index: dict | None = {} if ctx is not None else None
    mesh = grid.mesh(index_out=index)
    if mesh.is_empty:
        return mesh
    raw_faces = int(len(mesh.faces))

    ties = 0
    # A build on the last grid: the blocks whose untied triangles are not the last
    # build's arrays (contoured again, or come or gone).
    changed: set | None = None
    if reuse is not None and ctx is not None and not ctx.recontour_all:
        changed = {k for k, arrays in grid.tris.items() if reuse.tris.get(k) is not arrays}
        changed |= {k for k in reuse.tris if k not in grid.tris}
    tie_params = (float(tie_um), mode, float(support))
    kept_ties: dict | None = None        # the last build's tie step, when it still holds
    record: dict | None = None           # this build's, for the next
    components: int | None = None
    if tie_um > 0:
        if changed is not None and attached is None and reuse is not None:
            kept_ties = _reused_ties(grid, reuse, changed, tie_params)
        if kept_ties is not None:
            pieces_n = len(kept_ties["pieces"])
            segments = [(np.asarray(a, dtype=float), np.asarray(b, dtype=float))
                        for a, b in kept_ties["segments"]]
        else:
            pieces = split_components(mesh)
            pieces_n = len(pieces)
            segments = (_tie_segments(pieces, tie_um, touch_um=0.0, attached=attached)
                        if len(pieces) > 1 else [])
            if ctx is not None and ctx.keep and attached is None:
                record = _tie_record(pieces, segments, grid, tie_params)
            del pieces
        if pieces_n > 1:
            # Every separate sheet of one cell gets tied (no touch
            # tolerance): in one field, pieces that were going to touch have
            # already merged, so anything still apart is a real gap.
            affected: set[tuple[int, int, int]] = set()
            for pa, pb in segments:
                u = pb - pa
                norm = float(np.linalg.norm(u))
                u = u / norm if norm > 1e-9 else np.array([0.0, 0.0, 1.0])
                ext = 2.0 * TIE_RADIUS_UM
                tie_leaf: _Leaf = (
                    SegmentKernel.from_pair(pa - u * ext, pb + u * ext, TIE_RADIUS_UM,
                                            TIE_RADIUS_UM, support=support)
                    if mode == "sum" else
                    Capsule(pa - u * ext, pb + u * ext, TIE_RADIUS_UM, TIE_RADIUS_UM))
                affected |= grid.add(_Body([tie_leaf], area_um2=0.0))
                ties += 1
            # The set's own order, as before: a new block's place in the
            # mesh follows the order it is marched in.
            touched = list(affected)
            for i, key in enumerate(touched):
                report("tying gaps", i, len(touched))
                if (kept_ties is not None and changed is not None and key not in changed
                        and reuse is not None and key in reuse.final_tris):
                    # The same ties through a block whose field is as it was: the same triangles.
                    grid.tris[key] = reuse.final_tris[key]
                else:
                    grid.march(key)
            if ties:
                index = {} if ctx is not None else None
                mesh = grid.mesh(index_out=index)
        if kept_ties is not None and changed is not None:
            record = dict(kept_ties)
            record["pieces"] = [dict(p) for p in kept_ties["pieces"]]
            host = record["pieces"][kept_ties["host"]]
            host["blocks"] = host["blocks"] | changed
            host["vertices"] = None
            if not (set(affected if pieces_n > 1 else ()) & changed):
                # The same pieces, tied the same way through blocks the moves left alone.
                components = int(kept_ties["components"])
    if components is None:
        components = len(split_components(mesh))
    if record is not None:
        record["components"] = components
    if ctx is not None:
        ctx.index, ctx.n_vertices = index, int(len(mesh.vertices))
        if ctx.out is not None:
            ctx.out.final_tris = dict(grid.tris)
            ctx.out.ties = record

    mesh.meta.update({
        "voxel2_um": voxel,
        "voxel2_asked": float(typed),
        "voxel2_coarsened": bool(coarsened),
        "voxel2_reason": reason,
        "voxel2_raw_faces": raw_faces,
        "voxel2_blocks": len(grid.tris),
        "voxel2_ties": ties,
        "voxel2_components": components,
        "field_mode": mode,
    })
    if reuse is not None:
        # Built on the last build's grid (manual moves): how many of its blocks were contoured,
        # and whether the last build's ties held.
        mesh.meta["voxel2_reused_blocks"] = len(grid.body_blocks) - (ctx.stats["contoured"] if ctx else 0)
        mesh.meta["voxel2_ties_kept"] = kept_ties is not None
    if ctx is not None:
        ctx.stats["ties_kept"] = kept_ties is not None
    return mesh
