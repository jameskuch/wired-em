"""What a Closed curve or Hybrid render will cost, before it is built.

The Voxel size of the two outline methods (``pixel_size_um``) is the one render
option that can ask for more than the server or the page can hold: halving it
quadruples the faces between sections, and a pixel too fine for a cell is
refused only after its outlines have been rasterized (RPC1 c2628 at 0.0125 um:
58 s of rasterizing, then 23 million voxel faces against a limit of 10 million).
This module answers, for one loaded cell and one set of render options, what the
build would be, in a fraction of a second, so the page can say so while the
size is being typed and build each cell at a size that fits.

How each number is made:

* **Voxels** of the outline raster: counted exactly, tile by tile, without
  rasterizing (:func:`~sbfsem.geometry.closed_curve.curve_voxel_counter`, the
  plan :func:`~sbfsem.geometry.closed_curve.build_curve_volume` refuses on).
* **Exposed voxel faces**, what the mesh is made of: from one raster of the
  cell at a reference pixel, scaled.  Faces in the sections' planes trace the
  outlines and go as 1 / pixel; faces between sections cover the change from
  one section's area to the next and go as 1 / pixel^2.  Measured 2026-10-16,
  each raster counted at 0.2, 0.1, 0.05 and 0.025 um (and 0.0125 on c2628):
  between sections the law holds to 0.1 % on RPC1 c2628, Inferior c16137 and
  RC1 c410; in the planes to 0.5 % on c16137 and c410, and c2628 resolves a
  little more of its outlines at every halving (x2.08, 2.03, 2.01), so the law
  from 0.1 um is 1.9 % short at 0.025 um, 1.3 % at 0.0125.  The reference is
  whatever this cell was last built at -- a build counts its raster's faces
  anyway and leaves them in the mesh's meta (:data:`CURVE_RASTER_META`) -- or,
  when it has never been built with outlines, one raster made here at 0.1 um
  or coarser and kept for the cell.
* **Triangles**: marching cubes draws 1.4 to 2 per exposed face (c2628 1.41 to
  1.49, c410 1.43 to 1.56, c16137's thin processes 1.89 to 1.95).  A cell built
  before gives its own ratio; otherwise the high end.
* **Memory**: the build's peak, ``BUILD_BASE_MB`` plus
  ``BUILD_BYTES_PER_TRIANGLE``, times the averaging's share when it is on;
  then what serving it as JSON adds in the web process,
  ``SERVE_BYTES_PER_TRIANGLE``.  Measured 2026-10-16, one process a build,
  peak resident memory with the loaded cell's own 180 to 430 MB in it:
  c2628 1.18 M triangles 857 MB, 3.41 M 1,307, 5.93 M 2,030, 10.1 M 3,307;
  c16137 0.34 M 358, 3.49 M 1,177, 8.95 M 2,455, 13.6 M 3,848; c410 0.61 M
  452, 3.08 M 1,167, 5.40 M 1,730, 9.54 M 3,087 (the build alone 269 to 299
  bytes a triangle at the large end); serving 216 to 313 bytes a triangle
  (439 on the smallest mesh).  The sum is held to the memory the server gives
  builds (``SBFSEM_MESH_MEMORY_MB``, 9,000 MB in production).
* **Seconds**: production's, calibrated on the one build timed there (c2628 at
  0.025 um: 109 s from job start to the finished JSON, 2.5 times this Mac's
  quiet 29.6 s build and 14 s serialization).
* **The page**: ``JSON_BYTES_PER_TRIANGLE`` of text (31.0 to 35.6 measured,
  rising with the mesh) and ``TAB_BYTES_PER_TRIANGLE`` of the tab's heap (c2628
  at 0.025 um: 37 -> 620 MB).  The text must fit in one JavaScript string to
  be parsed, which V8 caps at 536,870,888 characters (512 MiB less 24): in
  Chrome 148 ``fetch(...).json()`` parsed a 536,000,003-byte response and
  failed on 537,000,003 bytes with "Unexpected end of JSON input", the text cut
  at that length (2026-10-16).  ``PAGE_JSON_LIMIT_MB`` keeps 7 % below it.  The face limit does
  not keep a mesh under it: c16137 at 0.012 um is 7.0 M faces and 486 MB of
  text, and at 0.011 it would be about 8.3 M faces and 577 MB, which a tab
  cannot read.

The finest pixel that fits every limit, to the thousandth of a micron, is
worked out from the same laws with 5 % to spare, as the server's own refusal
names its pixel (``closed_curve.surface_fitting_pixel``), and checked against
the voxel plan.  Nothing here touches the mesh build queue: an estimate runs in
the request that asks for it.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from ..geometry.closed_curve import (
    MAX_SURFACE,
    build_curve_volume,
    curve_voxel_counter,
    finest_fitting_pixel,
)
from ..geometry.volume import curve_volume_to_mesh

if TYPE_CHECKING:
    from ..core.structure import Neuron
    from ..geometry.closed_curve import CurveVolume

__all__ = [
    "CURVE_METHODS",
    "CURVE_RASTER_META",
    "CostEstimator",
    "FieldPlanner",
    "RasterCount",
    "raster_record",
]

log = logging.getLogger(__name__)

#: The methods that rasterize outlines (the schema shows Voxel size for these).
CURVE_METHODS = ("closedcurve", "hybrid")

#: The key a closed-curve mesh's meta keeps its raster's cost under.
CURVE_RASTER_META = "curve_raster"

#: ``build_curve_volume``'s own ceiling on the raster's tiles.
MAX_VOXELS = 300_000_000

#: Triangles per exposed voxel face, the range measured.
TRIANGLES_PER_FACE_LOW = 1.4
TRIANGLES_PER_FACE_HIGH = 2.0
#: When the cell has not been built to say: traced outlines draw wide bodies
#: (c2628 1.41 to 1.49, c410 1.43 to 1.56), a disc-traced cell's circles and
#: links thin processes (c16137 1.89 to 1.95); a cell with both is weighted by
#: how many of its outlines are which.
TRIANGLES_PER_FACE_TRACED = 1.6
TRIANGLES_PER_FACE_DISCS = 1.95

#: A closed-curve build's peak resident memory, MB: the process, then the mesh.
BUILD_BASE_MB = 450.0
BUILD_BYTES_PER_TRIANGLE = 290.0
#: The averaging (``curve_avg_discs``, ``curve_avg_paths``) adds up to this share
#: of the build's peak, and never more than ``AVERAGING_MAX_MB`` (the facade's
#: ``CURVE_AVERAGING_FACTOR`` and ``CURVE_AVERAGING_MAX_MB``, measured there).
AVERAGING_SHARE = 0.7
AVERAGING_MAX_MB = 900.0
#: Joining a Closed curve surface's bodies (``curve_tie_um``, the viewer's
#: default since 2.6.2) keeps every block's contour for a second pass and holds
#: the first surface meanwhile: measured (2026-10-16, sbfsem-measure, Join bodies
#: 2 um, off -> on) Inferior c1178 at 0.1 um +392 MB over 1.1 million triangles
#: (356 bytes a triangle), at 0.05 um +630 over 3.4 million (185); RPC1 c2628 at
#: 0.05 um +908 over 3.8 million (239), at 0.025 um +2,604 over 11.0 million
#: (237).  Capped as the build gate caps it (``facade.CURVE_TIE_MAX_MB``: 260
#: bytes for each of up to 1.9 triangles of a face at the surface limit).  The
#: ties and the second contour took 1.4 s of c1178's build at 0.25 um and 2.5 s
#: of c2628's at 0.1 um (about a third of it), nothing measurable of c1178's at
#: 0.1 um.
JOIN_BYTES_PER_TRIANGLE = 260.0
JOIN_MAX_MB = 260e-6 * 1.9 * MAX_SURFACE
JOIN_TIME = 0.3
#: What the web process holds while it turns a finished mesh into JSON.
SERVE_BYTES_PER_TRIANGLE = 300.0

#: The page: the text it receives and the heap its tab grows by.
JSON_BYTES_PER_TRIANGLE = 36.0
TAB_BYTES_PER_TRIANGLE = 60.0
#: The longest JSON a tab can parse: V8's longest string (536,870,888
#: characters; Chrome 148 parsed 536 MB and failed at 537), less 7 % for the
#: estimate's own error.
PAGE_JSON_LIMIT_MB = 500.0

#: Production's seconds: a fixed start, each outline, each million triangles
#: built, each million serialized (this Mac's quiet build of c2628 is 2.8 s a
#: million triangles, its JSON 1.4 s; production is 2.5 times slower).
SECONDS_START = 1.2
SECONDS_PER_OUTLINE = 0.0006
SECONDS_PER_MILLION_TRIANGLES = 7.0
SERVE_SECONDS_PER_MILLION_TRIANGLES = 3.5
#: Averaging adds the chain fits, the labels and the surface passes: about a
#: fifth more for discs (c16137 at 0.1 um: 2.5 s -> 3.0 s), four fifths for
#: traced outlines (c2628: 3.0 s -> 5.5 s), as in renderoptions.js.
AVERAGING_DISCS_TIME = 0.2
AVERAGING_PATHS_TIME = 0.8
#: Hybrid's disc half, per disc on production (the browser's rate for a
#: Cylinder build: 1.55 ms locally, times the server's 2.5), and as triangles
#: (an Original or Adaptive ring of a disc and its stretch: tens of them).
DISC_SECONDS = 0.004
DISC_TRIANGLES = 60

#: The pixel a cell's reference raster is made at, or coarser while that raster
#: would pass ``REFERENCE_MAX_VOXELS``.
REFERENCE_PIXEL_UM = 0.1
#: How many times finer than a coarser count a pixel may be scaled from it.
REFERENCE_REACH = 2.5
#: What one reference may cost.  It is made in the web process, beside the
#: requests and outside the build processes and their budget, so it is bounded,
#: and only one is made at a time in the whole process (``_REFERENCE_SLOT``).
#: Measured 2026-10-16 at 0.1 um, peak resident memory over the process before
#: it: the raster is about 5 bytes a voxel (RPC1 c2628, 12.4 M voxels: +62 MB;
#: RC1 c410, 8.0 M: +65; Inferior c16137, 3.8 M: +17) and contouring it about
#: 330 bytes a triangle more (c2628, 1.18 M triangles from 838,574 faces: +386
#: MB in all; c410, 0.60 M: +231; c16137, 0.34 M: +190).  At most 20 million
#: voxels (about 100 MB) and a contour only of a raster of at most 900,000
#: faces (at most 2 triangles a face: about 600 MB) keep one reference under
#: 0.7 GB.  The production unit's 13,500 MB ceiling holds the idle web process
#: (~800 MB), the builds' 9,000 MB and one finished 10-million-triangle mesh
#: turned into JSON (~2.9 GB) with that much to spare.  A raster with more faces
#: is counted but not contoured, and its triangles per face are the rule of
#: thumb's (``TRIANGLES_PER_FACE_TRACED`` / ``_DISCS``).
REFERENCE_MAX_VOXELS = 20_000_000
REFERENCE_CONTOUR_MAX_FACES = 900_000

#: One reference raster at a time in the whole process: whoever cannot take it
#: does not wait (a request answers from the counts it has, marked approximate,
#: or says the answer is pending; a head start in the background is skipped).
_REFERENCE_SLOT = threading.Lock()

#: The share of every limit the finest pixel keeps to spare.
HEADROOM = 0.95

#: The finest pixel an estimate looks at, um (the Voxel control's floor).
FINEST_UM = 0.005


@dataclass(frozen=True)
class RasterCount:
    """An outline raster's size at one pixel, as a build counted it.

    ``plane`` and ``across`` are its exposed voxel faces in the sections'
    planes and between sections (:meth:`CurveVolume.exposed_faces`);
    ``triangles`` is the mesh made of it (0 when none was)."""

    pixel_um: float
    plane: int
    across: int
    voxels: int = 0
    triangles: int = 0
    #: Counted by a build of the cell (else by the estimate's own raster).
    built: bool = False

    @property
    def faces(self) -> int:
        return self.plane + self.across

    def faces_at(self, pixel_um: float) -> float:
        """Faces at another pixel: in-plane as 1 / pixel, across as 1 / pixel^2."""
        ratio = self.pixel_um / float(pixel_um)
        return self.plane * ratio + self.across * ratio * ratio

    def to_meta(self) -> dict[str, Any]:
        return {"pixel_um": self.pixel_um, "plane": self.plane, "across": self.across,
                "voxels": self.voxels, "triangles": self.triangles}

    @classmethod
    def from_meta(cls, meta: Any) -> RasterCount | None:
        """The record a mesh's meta keeps, or None for anything else."""
        if not isinstance(meta, dict):
            return None
        try:
            count = cls(float(meta["pixel_um"]), int(meta["plane"]), int(meta["across"]),
                        int(meta.get("voxels", 0)), int(meta.get("triangles", 0)), built=True)
        except (KeyError, TypeError, ValueError):
            return None
        return count if count.pixel_um > 0 and count.faces > 0 else None


def raster_record(volume: CurveVolume, triangles: int) -> dict[str, Any]:
    """The meta a closed-curve mesh carries about the raster it was made of."""
    plane, across = volume.exposed or (0, 0)
    return RasterCount(float(volume.spacing_um[0]), int(plane), int(across),
                       int(volume.allocated_voxels), int(triangles)).to_meta()


def _thousandths(pixel_um: float) -> int:
    return int(math.ceil(round(float(pixel_um) * 1e3, 6)))


def finest_by(fits: Callable[[float], bool], start_um: float = FINEST_UM) -> float | None:
    """The finest pixel to the thousandth of a micron, from ``start_um`` up to
    1 um, where ``fits``; ``fits`` must only ever turn true as the pixel grows.
    None when not even 1 um fits."""
    lo, hi = max(1, _thousandths(start_um)), 1000
    if not fits(hi * 1e-3):
        return None
    if fits(lo * 1e-3):
        return round(lo * 1e-3, 3)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fits(mid * 1e-3):
            hi = mid
        else:
            lo = mid
    return round(hi * 1e-3, 3)


def _traced_share(outlines) -> float:
    """The share of the outlines' length that was traced, rather than a disc's
    circle or the run of a link between discs: what the surface is mostly made
    of.  By length, not by count: RC1 c410's traced outlines are far fewer than
    its circles and links and draw most of its surface."""
    traced = total = 0.0
    for a in outlines:
        points = np.asarray(a.outline, dtype=float)
        if len(points) < 2:
            continue
        length = float(np.abs(np.diff(points, axis=0)).sum())
        total += length
        if getattr(a, "is_path", True):
            traced += length
    return traced / total if total > 0 else 1.0


@dataclass
class _Cell:
    """What the estimator keeps for one copy of a cell and one kind of raster."""

    outlines: list
    how: dict
    voxels_at: Callable[[float], int]
    n_outlines: int
    #: Rasters counted at known pixels: builds of the cell, or the reference.
    counts: dict[float, RasterCount]
    voxels: dict[float, int]
    lock: threading.Lock
    #: The share of the outlines that were traced (the rest are discs' circles
    #: and the links between them).
    traced: float = 1.0
    #: The cell's reference raster has been made (or tried): final for this copy
    #: of the cell, at whatever pixel ``REFERENCE_MAX_VOXELS`` allowed.
    reference_made: bool = False

    def snapshot(self) -> list[RasterCount]:
        """The counts as they are now (a build or the head start may add one)."""
        with self.lock:
            return list(self.counts.values())

    def triangles_per_face(self, pixel_um: float) -> float:
        """The cell's own, from its contoured raster at 0.1 um or finer nearest
        ``pixel_um`` (at 0.25 um a cell draws fewer per face: c16137 1.47 against
        1.89 to 1.95 from 0.1 down); else what its kind of outlines measured."""
        built = [c for c in self.snapshot()
                 if c.triangles and c.faces and c.pixel_um <= REFERENCE_PIXEL_UM + 1e-9]
        if built:
            near = min(built, key=lambda c: (abs(math.log(c.pixel_um / pixel_um)), c.pixel_um))
            return near.triangles / near.faces
        return (TRIANGLES_PER_FACE_TRACED * self.traced
                + TRIANGLES_PER_FACE_DISCS * (1.0 - self.traced))


class CostEstimator:
    """Estimates for the cells a service holds, and what it keeps between them.

    ``outlines_of(neuron, discs)`` is the facade's ``_curve_outlines`` (the
    outlines a build of that kind rasterizes and the keywords it passes);
    ``disc_peak_mb(neuron, options)`` is the facade's memory estimate for a
    Hybrid's disc half.
    """

    #: Cells (copy and kind) and answers kept.
    CELLS_KEPT = 32
    ANSWERS_KEPT = 512

    def __init__(self, outlines_of: Callable[[Neuron, bool], tuple[list, dict]],
                 disc_peak_mb: Callable[[Neuron, dict], float]) -> None:
        self._outlines_of = outlines_of
        self._disc_peak_mb = disc_peak_mb
        self._cells: OrderedDict[tuple, _Cell] = OrderedDict()
        self._answers: OrderedDict[tuple, dict] = OrderedDict()
        # Builds of a cell seen before its first estimate, kept for it.
        self._seen: OrderedDict[tuple, dict[float, RasterCount]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _cell_key(neuron: Neuron, discs: bool) -> tuple:
        return (str(neuron.source), int(neuron.id), getattr(neuron, "load_serial", None),
                bool(discs))

    def note_build(self, neuron: Neuron, method: str, meta: Any) -> None:
        """Keep what a build of ``neuron`` counted (its mesh's meta), for the
        next estimate at another pixel."""
        count = RasterCount.from_meta(meta)
        if count is None or method not in CURVE_METHODS:
            return
        key = self._cell_key(neuron, method == "closedcurve")
        with self._lock:
            cell = self._cells.get(key)
            if cell is not None:
                with cell.lock:
                    known = cell.counts.get(count.pixel_um)
            else:
                known = self._seen.get(key, {}).get(count.pixel_um)
            if known == count:
                return
            # What was answered for this copy of the cell was scaled from less.
            for answered in [k for k in self._answers if k[:3] == key[:3]]:
                del self._answers[answered]
            if cell is not None:
                with cell.lock:
                    cell.counts[count.pixel_um] = count
                return
            # Kept until the cell's outlines are needed: an estimate makes them.
            self._seen.setdefault(key, {})[count.pixel_um] = count
            self._seen.move_to_end(key)
            while len(self._seen) > self.CELLS_KEPT:
                self._seen.popitem(last=False)

    def _cell(self, neuron: Neuron, discs: bool) -> _Cell:
        key = self._cell_key(neuron, discs)
        with self._lock:
            cell = self._cells.get(key)
            if cell is not None:
                self._cells.move_to_end(key)
                return cell
        outlines, how = self._outlines_of(neuron, discs)
        voxels_at = curve_voxel_counter(outlines, volume_scale=neuron.volume_scale, **how)
        with self._lock:
            cell = self._cells.get(key)
            if cell is None:
                traced = _traced_share(outlines)
                cell = _Cell(outlines, how, voxels_at, len(outlines),
                             dict(self._seen.pop(key, {})), {}, threading.Lock(), traced)
                self._cells[key] = cell
                while len(self._cells) > self.CELLS_KEPT:
                    self._cells.popitem(last=False)
            return cell

    def _voxels(self, cell: _Cell, pixel_um: float) -> int:
        pixel_um = round(float(pixel_um), 6)
        if pixel_um not in cell.voxels:
            cell.voxels[pixel_um] = int(cell.voxels_at(pixel_um))
        return cell.voxels[pixel_um]

    @staticmethod
    def _nearest(counts: list[RasterCount], pixel_um: float) -> RasterCount | None:
        if not counts:
            return None
        return min(counts, key=lambda c: (abs(math.log(c.pixel_um / pixel_um)), c.pixel_um))

    def _reference(self, neuron: Neuron, cell: _Cell,
                   pixel_um: float) -> tuple[RasterCount | None, str, bool]:
        """The raster count to scale for ``pixel_um``, what it is (``built`` or
        ``reference``), and whether the answer is approximate.

        A count coarser than ``REFERENCE_PIXEL_UM`` and more than
        ``REFERENCE_REACH`` times the pixel asked for is not scaled that far: a
        traced cell resolves more of its outlines in the plane at every halving
        (c2628 from its 0.25 um build: 8 % short at 0.05 um, 6 % at 0.025), so
        a raster at 0.1 um is made once for the cell and scaled instead (2 %
        short).  It is made here only when no other is being made in the process
        (``_REFERENCE_SLOT``); otherwise the nearest count answers, approximate,
        and a cell with no count at all has no answer yet (``None``: pending).
        Nothing here waits on another request's raster."""
        counts = cell.snapshot()
        best = self._nearest(counts, pixel_um)
        kind = "built" if best is not None and best.built else "reference"
        if best is not None and (cell.reference_made
                                 or best.pixel_um <= REFERENCE_PIXEL_UM + 1e-9
                                 or best.pixel_um <= REFERENCE_REACH * pixel_um):
            return best, kind, False
        if not _REFERENCE_SLOT.acquire(blocking=False):
            return best, kind, True
        try:
            if cell.reference_made:     # made while this request looked
                again = self._nearest(cell.snapshot(), pixel_um)
                return again, ("built" if again is not None and again.built else "reference"), False
            made = self._make_reference(neuron, cell, max(float(pixel_um), REFERENCE_PIXEL_UM))
        finally:
            _REFERENCE_SLOT.release()
        return made, "reference", False

    def _make_reference(self, neuron: Neuron, cell: _Cell, start: float) -> RasterCount:
        """Rasterize the cell at ``start`` (coarser while that raster would pass
        ``REFERENCE_MAX_VOXELS``), count its faces, contour it when its faces
        allow (``REFERENCE_CONTOUR_MAX_FACES``), and keep the count.  Made at
        ``REFERENCE_PIXEL_UM`` it is the cell's reference, final for this copy of
        it however it ends (at a coarser pixel when the voxels capped it, or not at
        all after an error): it is not tried again.  Call holding
        ``_REFERENCE_SLOT``; the cell's lock is taken only to store."""
        final = start <= REFERENCE_PIXEL_UM + 1e-9
        try:
            ref_px = start
            if self._voxels(cell, start) > REFERENCE_MAX_VOXELS:
                ref_px = finest_fitting_pixel(lambda px: self._voxels(cell, px), start,
                                              REFERENCE_MAX_VOXELS)
            volume = build_curve_volume(cell.outlines, volume_scale=neuron.volume_scale,
                                        pixel_size_um=ref_px, max_voxels=2 * REFERENCE_MAX_VOXELS,
                                        max_surface=2**62, **cell.how)
            plane, across = volume.exposed or volume.exposed_faces()
            # Contoured too, for the cell's own triangles per face: no rule of
            # thumb holds across cells (traced RPC1 c2628 1.46, a traced cone
            # pedicle 2.0, disc-traced c16137 1.95), and at 0.1 um a cell's own
            # is within 9 % of what it is at finer pixels (c2628 1.41 against
            # 1.46 to 1.49, c16137 1.89 against 1.92 to 1.95, c410 1.56 against
            # 1.43 to 1.53); at 0.25 um it is 20 % off.
            contour = (ref_px <= REFERENCE_PIXEL_UM + 1e-9
                       and plane + across <= REFERENCE_CONTOUR_MAX_FACES)
            triangles = len(curve_volume_to_mesh(volume).faces) if contour else 0
            count = RasterCount(ref_px, int(plane), int(across), int(volume.allocated_voxels),
                                int(triangles))
            with cell.lock:
                cell.counts[ref_px] = count
            return count
        finally:
            if final:
                cell.reference_made = True

    def estimate(self, neuron: Neuron, options: dict, *, memory_limit_mb: float) -> dict:
        """What building ``neuron`` with ``options`` (the mesh route's options)
        would cost, and the finest Voxel size that fits every limit."""
        method = str(options.get("method", "cylinder"))
        pixel = float(options.get("pixel_size_um", 0.05))
        avg_discs = float(options.get("curve_avg_discs", 0.0) or 0.0)
        avg_paths = float(options.get("curve_avg_paths", 0.0) or 0.0)
        if method == "hybrid":
            # Hybrid draws no circles: its Disc avg averages the discs before their tubes,
            # which costs the raster nothing, and only the outlines average the raster.
            avg_discs = 0.0
        # Only Closed curve joins its bodies (Hybrid's disc half ties its own pieces).
        joins = method == "closedcurve" and float(options.get("curve_tie_um", 0.0) or 0.0) > 0
        answer_key = (str(neuron.source), int(neuron.id), getattr(neuron, "load_serial", None),
                      method, round(pixel, 6), avg_discs > 0, avg_paths > 0, joins,
                      str(options.get("pathway", "original")), str(options.get("meshlab", "none")),
                      int(options.get("meshlab_depth", 9)), float(memory_limit_mb))
        with self._lock:
            cached = self._answers.get(answer_key)
            if cached is not None:
                self._answers.move_to_end(answer_key)
                return dict(cached)
        t0 = time.perf_counter()
        answer = self._estimate(neuron, method, pixel, avg_discs, avg_paths, options,
                                memory_limit_mb)
        answer["estimate_s"] = round(time.perf_counter() - t0, 3)
        # An approximate or pending answer is not kept: asked again, it is made
        # from the reference once there is one.
        if not (answer.get("approximate") or answer.get("pending")):
            with self._lock:
                self._answers[answer_key] = answer
                while len(self._answers) > self.ANSWERS_KEPT:
                    self._answers.popitem(last=False)
        if answer.get("applies") and not answer.get("empty"):
            self._prepare_finer(neuron, method)
        return dict(answer)

    def _prepare_finer(self, neuron: Neuron, method: str) -> None:
        """Make the cell's 0.1 um reference now, in the background, when it has none.

        Someone looking at a Closed curve or Hybrid estimate of a cell at a
        coarse size (a traced cell is first drawn at 0.25 um, and its build is the
        only count kept) is choosing a size, and the next one asked for is likely
        finer than that count may be scaled to: the raster it then needs (3 to 7
        s on RPC1 c2628, Inferior c16137 and RC1 c410, 2026-10-16) is made while
        they look, and the estimate after it answers in a tenth of a second.  Only
        when no other reference is being made in the process: a head start is
        skipped, never queued, so selecting twenty traced cells starts one raster,
        not twenty.
        """
        cell = self._cells.get(self._cell_key(neuron, method == "closedcurve"))
        if cell is None or cell.reference_made or any(
                c.pixel_um <= REFERENCE_PIXEL_UM + 1e-9 for c in cell.snapshot()):
            return
        if not _REFERENCE_SLOT.acquire(blocking=False):
            return

        def prepare() -> None:
            try:
                if not cell.reference_made:
                    self._make_reference(neuron, cell, REFERENCE_PIXEL_UM)
            except Exception as exc:  # noqa: BLE001 - only a head start; nothing waits on it
                log.info("c%s: no reference raster made ahead (%s)", neuron.id, exc)
            finally:
                _REFERENCE_SLOT.release()

        try:
            threading.Thread(target=prepare, name="estimate-reference", daemon=True).start()
        except RuntimeError:
            _REFERENCE_SLOT.release()

    def _estimate(self, neuron: Neuron, method: str, pixel: float, avg_discs: float,
                  avg_paths: float, options: dict, memory_limit_mb: float) -> dict:
        if not (math.isfinite(pixel) and FINEST_UM <= pixel <= 1.0):
            raise ValueError(f"pixel_size_um must be between {FINEST_UM} and 1 um for an estimate, "
                             f"not {pixel!r}")
        limits = {"voxels": MAX_VOXELS, "faces": MAX_SURFACE,
                  "memory_mb": float(memory_limit_mb), "json_mb": PAGE_JSON_LIMIT_MB}
        base: dict[str, Any] = {"method": method, "pixel_size_um": pixel, "limits": limits}
        if method not in CURVE_METHODS:
            return {**base, "applies": False}
        discs = method == "closedcurve"
        cell = self._cell(neuron, discs)
        if not cell.n_outlines:
            return {**base, "applies": True, "empty": True, "voxels": 0, "faces": 0,
                    "triangles": 0, "peak_mb": 0.0, "serve_mb": 0.0, "server_mb": 0.0,
                    "seconds": 0.0, "json_mb": 0.0, "tab_mb": 0.0, "over": [],
                    "finest_um": None, "binding": None}
        ref, basis, approximate = self._reference(neuron, cell, pixel)
        if ref is None:
            # Another cell's reference raster is being made, and this one has no
            # count yet: ask again in a moment.
            return {**base, "applies": True, "pending": True, "approximate": True,
                    "voxels": int(self._voxels(cell, pixel)), "over": [], "finest_um": None,
                    "binding": None, "outlines": cell.n_outlines}
        built = [c for c in cell.snapshot()
                 if c.triangles and c.faces and c.pixel_um <= REFERENCE_PIXEL_UM + 1e-9]
        per_face = cell.triangles_per_face(pixel)
        hybrid_discs = 0
        disc_mb = 0.0
        if method == "hybrid":
            nodes = neuron.get_cell_nodes()
            hybrid_discs = (int((nodes["TypeCode"] == 1).sum()) if "TypeCode" in nodes.columns
                            else len(nodes))
            if hybrid_discs:
                disc_mb = float(self._disc_peak_mb(neuron, options))
        averaging = avg_discs > 0 or avg_paths > 0
        joins = method == "closedcurve" and float(options.get("curve_tie_um", 0.0) or 0.0) > 0

        def triangles_at(px: float) -> float:
            return per_face * ref.faces_at(px) + hybrid_discs * DISC_TRIANGLES

        def build_mb(tri: float, averaging: bool = averaging, joins: bool = joins) -> float:
            curve = tri - hybrid_discs * DISC_TRIANGLES
            peak = BUILD_BASE_MB + BUILD_BYTES_PER_TRIANGLE * curve / 1e6
            if averaging:
                peak += min(peak * AVERAGING_SHARE, AVERAGING_MAX_MB)
            if joins:
                peak += min(JOIN_BYTES_PER_TRIANGLE * curve / 1e6, JOIN_MAX_MB)
            return peak + disc_mb

        def costs(px: float, averaging: bool = averaging, joins: bool = joins) -> dict[str, float]:
            tri = triangles_at(px)
            peak = build_mb(tri, averaging, joins)
            serve = SERVE_BYTES_PER_TRIANGLE * tri / 1e6
            return {"faces": ref.faces_at(px), "triangles": tri, "peak_mb": peak,
                    "serve_mb": serve, "server_mb": peak + serve,
                    "json_mb": JSON_BYTES_PER_TRIANGLE * tri / 1e6,
                    "tab_mb": TAB_BYTES_PER_TRIANGLE * tri / 1e6}

        def over_at(px: float, share: float = 1.0, averaging: bool = averaging,
                    joins: bool = joins) -> list[str]:
            c = costs(px, averaging, joins)
            over = []
            if c["faces"] > share * MAX_SURFACE:
                over.append("faces")
            if c["server_mb"] > share * memory_limit_mb:
                over.append("memory")
            if c["json_mb"] > share * PAGE_JSON_LIMIT_MB:
                over.append("page")
            return over

        now = costs(pixel)
        voxels = self._voxels(cell, pixel)
        over = over_at(pixel)
        if voxels > MAX_VOXELS:
            over.insert(0, "voxels")
        # The finest pixel every limit holds with HEADROOM to spare: the analytic
        # ones first, then the voxel plan at that pixel (searched only when over).
        finest = finest_by(lambda px: not over_at(px, HEADROOM))
        # Which limit set it: the one a thousandth finer would break.
        binding: str | None = None
        if finest is not None and finest > FINEST_UM:
            broken = over_at(round(finest - 1e-3, 3), HEADROOM)
            binding = broken[0] if broken else None
        if finest is not None and over and self._voxels(cell, finest) > MAX_VOXELS:
            finest = finest_fitting_pixel(lambda px: self._voxels(cell, px), finest, MAX_VOXELS)
            binding = "voxels"
        # Past the memory with the averaging or the joining of bodies on, both of
        # which only add memory: the finest Voxel size with each of them off, when
        # it is finer than with them on (the faces, the voxels and the page do
        # not change with either).
        alternatives: list[dict[str, Any]] = []
        if over and finest is not None:
            turned = []
            if joins:
                turned.append(("joins", {"curve_tie_um": 0.0}, averaging, False))
            if averaging:
                off = {k: 0.0 for k, v in (("curve_avg_discs", avg_discs),
                                           ("curve_avg_paths", avg_paths)) if v > 0}
                turned.append(("averaging", off, False, joins))
            if joins and averaging:
                turned.append(("both", {"curve_tie_um": 0.0, **turned[1][1]}, False, False))
            for name, sets, with_avg, with_joins in turned:
                def fits_without(px: float, a: bool = with_avg, j: bool = with_joins) -> bool:
                    return not over_at(px, HEADROOM, a, j)

                alt = finest_by(fits_without)
                if alt is not None and self._voxels(cell, alt) > MAX_VOXELS:
                    alt = finest_fitting_pixel(lambda px: self._voxels(cell, px), alt, MAX_VOXELS)
                if alt is not None and alt < finest - 1e-9:
                    alternatives.append({"off": name, "set": sets, "finest_um": alt,
                                         "fits": not over_at(pixel, 1.0, with_avg, with_joins)
                                         and voxels <= MAX_VOXELS})
        seconds = (SECONDS_START + SECONDS_PER_OUTLINE * cell.n_outlines
                   + SECONDS_PER_MILLION_TRIANGLES * (now["triangles"] - hybrid_discs * DISC_TRIANGLES) / 1e6)
        seconds *= 1.0 + (AVERAGING_DISCS_TIME if avg_discs > 0 else 0.0) + (
            AVERAGING_PATHS_TIME if avg_paths > 0 else 0.0) + (JOIN_TIME if joins else 0.0)
        seconds += DISC_SECONDS * hybrid_discs
        seconds += SERVE_SECONDS_PER_MILLION_TRIANGLES * now["triangles"] / 1e6
        tri_low = TRIANGLES_PER_FACE_LOW * now["faces"] + hybrid_discs * DISC_TRIANGLES
        tri_high = TRIANGLES_PER_FACE_HIGH * now["faces"] + hybrid_discs * DISC_TRIANGLES
        if built:
            tri_low = tri_high = now["triangles"]
        return {
            **base,
            "applies": True,
            "voxels": int(voxels),
            "faces": int(round(now["faces"])),
            "faces_plane": int(round(ref.plane * ref.pixel_um / pixel)),
            "faces_across": int(round(ref.across * (ref.pixel_um / pixel) ** 2)),
            "triangles": int(round(now["triangles"])),
            "triangles_range": [int(round(tri_low)), int(round(tri_high))],
            "triangles_per_face": round(per_face, 3),
            "peak_mb": round(now["peak_mb"]),
            "serve_mb": round(now["serve_mb"]),
            "server_mb": round(now["server_mb"]),
            "json_mb": round(now["json_mb"], 1),
            "tab_mb": round(now["tab_mb"]),
            "seconds": round(seconds, 1),
            "outlines": cell.n_outlines,
            "hybrid_discs": hybrid_discs,
            "averaging": averaging,
            "joins": joins,
            "basis": {"kind": basis, "pixel_um": ref.pixel_um},
            # Scaled further than a count should be, while the cell's reference
            # raster waits for another one to finish: not kept, asked again.
            "approximate": approximate,
            "over": over,
            "finest_um": finest,
            "binding": binding,
            "alternatives": alternatives,
        }


# -- the whole-cell field's Fine voxel (Voxel remesh, Convolution, Ultraliser) --------

#: The field each field pathway contours (``unify.MODES``).
FIELD_MODES = {"voxel2": "min", "convolution": "sum", "ultraliser": "occupancy"}

#: A Face cap the estimate suggests is rounded up to this many triangles.
CAP_STEP = 10_000

#: The options that decide a cell's field pieces (``cylinder.field_pieces``) and
#: so its bodies: anything else (the Fine voxel, the Face cap, the fillet) only
#: sizes the grid they are contoured on.
FIELD_PIECE_KEYS = ("pathway", "voxel_detail", "voxel_smooth_um", "ring_arc_um",
                    "wrap_spacing_um", "wrap_blend", "wrap_reject_sigma", "wrap_passes",
                    "ultra_soma", "exact_tube_deg", "conv_support", "curve_avg_discs")


class FieldPlanner:
    """What Fine voxel a field pathway's build would contour a cell at, before it is built.

    A Voxel remesh, Convolution or Ultraliser build sizes its whole-cell grid
    first (:func:`~sbfsem.geometry.unify.plan_voxel`): the Fine voxel typed, or
    the annotations' own when it is automatic, then held to the raw-triangle
    budget its Face cap gives (three raw triangles a kept one, at most 10
    million; :func:`~sbfsem.geometry.unify.raw_budget`) and to the field
    evaluations that budget may spend.  A voxel finer than they allow is built
    coarser, and until 2.18.0 the page learned so only from the finished mesh.
    Here the same sizing runs on the same pieces without contouring (0.04 to
    0.45 s to make a cell's pieces, measured 2026-10-20 on Inferior c1178 and
    c16137 and RPC1 c430; 0.04 to 0.11 s a plan), and says what a build would
    use, why, the least Face cap that builds the voxel asked for, the voxel
    Face cap 0 builds, and the finest any budget allows.

    ``pieces_of(neuron, options)`` makes the pieces as a build would (the
    facade's ``_field_pieces``); ``peak_mb(neuron, options)`` is the build
    gate's memory price for those options, which prices the whole raw budget
    a Face cap allows; ``sheet_mb(raw_triangles, capped)`` is what a build
    that draws that many raw triangles peaks at (measured on Inferior c1178,
    the facade's ``FIELD_SHEET_*``; c16137 at 0.04 um with every triangle
    kept, 2.52 million: 1,422 MB from it, 1,429 MB measured on 2026-10-20),
    which is what the page is told.
    """

    #: Cells (copy and pieces) and answers kept.
    CELLS_KEPT = 8
    ANSWERS_KEPT = 256

    def __init__(self, pieces_of: Callable[[Neuron, dict], list],
                 peak_mb: Callable[[Neuron, dict], float],
                 sheet_mb: Callable[[int, bool], float]) -> None:
        self._pieces_of = pieces_of
        self._peak_mb = peak_mb
        self._sheet_mb = sheet_mb
        self._bodies: OrderedDict[tuple, tuple[list, list]] = OrderedDict()
        self._answers: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def applies(method: str, options: dict) -> bool:
        """A Cylinder or Tube build whose pathway contours one whole-cell field."""
        return method in ("cylinder", "curvemesh") and options.get("pathway") in FIELD_MODES

    def _cell(self, neuron: Neuron, options: dict) -> tuple[list, list]:
        from ..geometry.cylinder import STRAND_COIN_HALF_UM
        from ..geometry.unify import field_bodies

        key = self._cell_key(neuron, options)
        with self._lock:
            held = self._bodies.get(key)
            if held is not None:
                self._bodies.move_to_end(key)
                return held
        mode = FIELD_MODES[str(options["pathway"])]
        made = field_bodies(self._pieces_of(neuron, options), mode=mode,
                            lens_h=STRAND_COIN_HALF_UM,
                            support=float(options.get("conv_support", 2.0)),
                            hull_tilt_deg=(float(options.get("exact_tube_deg", 0.0) or 0.0)
                                           if mode != "sum" else 0.0))
        with self._lock:
            self._bodies[key] = made
            while len(self._bodies) > self.CELLS_KEPT:
                self._bodies.popitem(last=False)
        return made

    @staticmethod
    def _cell_key(neuron: Neuron, options: dict) -> tuple:
        return (str(neuron.source), int(neuron.id), getattr(neuron, "load_serial", None),
                *(repr(options.get(k)) for k in FIELD_PIECE_KEYS))

    def forget(self) -> None:
        with self._lock:
            self._bodies.clear()
            self._answers.clear()

    def estimate(self, neuron: Neuron, options: dict) -> dict[str, Any]:
        """The Fine voxel a build of ``neuron`` with ``options`` (the mesh route's,
        method already canonical) would contour at, and what would change it."""
        key = (*self._cell_key(neuron, options), str(options.get("method")),
               *(repr(options.get(k)) for k in ("voxel2_um", "voxel2_faces", "voxel2_blend_um",
                                                "meshlab", "meshlab_depth")))
        with self._lock:
            held = self._answers.get(key)
            if held is not None:
                self._answers.move_to_end(key)
                return dict(held)
        answer = self._estimate(neuron, options)
        with self._lock:
            self._answers[key] = answer
            while len(self._answers) > self.ANSWERS_KEPT:
                self._answers.popitem(last=False)
        return dict(answer)

    def _estimate(self, neuron: Neuron, options: dict) -> dict[str, Any]:
        from ..geometry.unify import (
            FACES_PER_SURFACE_VOXEL,
            MAX_RAW_FACES,
            MAX_WORK_VOXELS,
            RAW_PER_CAPPED_FACE,
            TYPED_MIN_VOXEL_UM,
            WORK_PER_RAW_FACE,
            plan_voxel,
            raw_budget,
        )

        t0 = time.perf_counter()
        pathway = str(options["pathway"])
        bodies, thin = self._cell(neuron, options)
        typed = float(options.get("voxel2_um", 0.0) or 0.0)
        cap = int(options.get("voxel2_faces", 0) or 0)
        blend = float(options.get("voxel2_blend_um", 0.0) or 0.0) if pathway == "voxel2" else 0.0
        mode = FIELD_MODES[pathway]

        def plan(voxel_um: float, faces: int):
            return plan_voxel(bodies, thin, voxel_um=voxel_um, max_raw_faces=raw_budget(faces),
                              blend_um=blend, mode=mode)

        now = plan(typed, cap)
        if now is None:
            return {"applies": True, "empty": True, "pathway": pathway}

        def kept(p, faces: int) -> int:
            raw = min(p.raw_faces, p.raw_budget)
            return min(raw, faces) if faces > 0 else raw

        def option(p, faces: int) -> dict[str, Any]:
            raw = int(min(p.raw_faces, p.raw_budget))
            return {"voxel2_faces": int(faces), "voxel2_um": typed,
                    "voxel_um": round(p.voxel_um, 4), "reason": p.reason,
                    "raw_triangles": raw, "triangles": kept(p, faces),
                    "peak_mb": round(float(self._sheet_mb(raw, faces > 0))),
                    "gate_mb": round(float(self._peak_mb(
                        neuron, {**options, "voxel2_faces": int(faces), "voxel2_um": typed})))}

        # What the build is held to: the voxel typed (within the floor and the
        # ceiling), or the one the annotations ask for.
        target = now.asked_um
        uncapped = plan(typed, 0) if cap > 0 else now
        cap_for: dict[str, Any] | None = None
        if cap > 0 and now.voxel_um > target + 1e-9 and uncapped.voxel_um <= target + 1e-9:
            # The least Face cap whose budgets hold the target: the raw triangles
            # it draws there, and the evaluations it spends there (a raw budget
            # over 3 million may spend 20 a triangle), then checked by planning.
            raw = FACES_PER_SURFACE_VOXEL * now.area_um2 / target ** 2
            if uncapped.work > MAX_WORK_VOXELS:
                raw = max(raw, uncapped.work / WORK_PER_RAW_FACE)
            faces = int(math.ceil(raw / RAW_PER_CAPPED_FACE / CAP_STEP) * CAP_STEP)
            for _ in range(6):
                if RAW_PER_CAPPED_FACE * faces >= MAX_RAW_FACES:
                    faces = 0
                    break
                if plan(typed, faces).voxel_um <= target + 1e-9:
                    break
                faces = int(math.ceil(faces * 1.05 / CAP_STEP) * CAP_STEP)
            cap_for = option(plan(typed, faces), faces) if faces else None
        finest = plan(TYPED_MIN_VOXEL_UM, 0)
        finest_cap = plan(TYPED_MIN_VOXEL_UM, cap) if cap > 0 else finest
        assert finest is not None and finest_cap is not None and uncapped is not None
        answer = {
            "applies": True,
            "pathway": pathway,
            "typed": typed > 0,
            "typed_um": round(now.typed_um, 4),
            "asked_um": round(now.asked_um, 4),
            "floor_um": now.floor_um,
            "voxel_um": round(now.voxel_um, 4),
            "coarsened": bool(now.coarsened),
            "reason": now.reason,
            "area_um2": round(now.area_um2, 1),
            "voxel2_faces": cap,
            "raw_budget": int(now.raw_budget),
            "raw_triangles": int(min(now.raw_faces, now.raw_budget)),
            "triangles": kept(now, cap),
            "work": int(now.work),
            "work_budget": int(now.work_budget),
            "peak_mb": round(float(self._sheet_mb(int(min(now.raw_faces, now.raw_budget)), cap > 0))),
            "gate_mb": round(float(self._peak_mb(neuron, options))),
            # The least Face cap that builds the target voxel; None when the
            # build is not held back by its cap, or no cap reaches the target.
            "cap_for_asked": cap_for,
            # Face cap 0 (every triangle kept, the 10 million budget).
            "uncapped": option(uncapped, 0) if cap > 0 else None,
            # The finest voxel any Face cap allows this cell, and this one.
            "finest_um": round(finest.voxel_um, 4),
            "finest_at_cap_um": round(finest_cap.voxel_um, 4),
        }
        answer["estimate_s"] = round(time.perf_counter() - t0, 3)
        return answer
