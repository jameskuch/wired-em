"""The single entry point a frontend calls.

Everything above this line is pandas, numpy and networkx; everything below it is
pydantic DTOs.  The Qt-or-web question the plan left open is answered here: the
FastAPI layer in :mod:`sbfsem_web` calls these methods and serializes the same
DTOs, and any other frontend would do the same.

Nothing in this module — or anywhere under :mod:`sbfsem` — imports a GUI toolkit
or requires a display.  ``tests/unit/test_service.py`` asserts that.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import itertools
import logging
import multiprocessing
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import pandas as pd
from pydantic import TypeAdapter

from .. import progress
from ..analysis import dendrite as dendrite_analysis
from ..analysis import synapse as synapse_analysis
from ..analysis.boundary import IPLBoundaries, load_ipl_boundaries
from ..analysis.stats import Stats
from ..analysis.stratification import ipl_depth, synapse_stratification
from ..core.moves import ManualMove, moved_copy, moves_token, normalize_moves
from ..core.structure import Neuron
from ..core.transforms import Transforms, viking2micron
from ..core.types import UNTAGGED_POLICIES, StructureTypes
from ..core.volumes import Volumes
from ..geometry import annotation_wire as wire
from ..geometry import meshlab
from ..geometry.closed_curve import (
    BRIDGE_UM,
    LINK_BRIDGE_UM,
    MAX_SURFACE,
    CurveVolume,
    annotations_from_discs,
    annotations_from_geometries,
    build_curve_volume,
    curve_grid_voxels,
)
from ..geometry.curve_average import (
    average_disc_nodes,
    average_outlines,
    polish_curve_mesh,
    smooth_surface,
)
from ..geometry.curve_ties import tie_bodies
from ..geometry.cylinder import FIELD_PATHWAYS, cylinder_render, field_pieces
from ..geometry.mesh import EMPTY_MESH, Mesh, concatenate
from ..geometry.meshdiff import (
    AreaStats,
    add_reverse,
    annotation_deviation,
    deviation_map,
    mesh_difference,
)
from ..geometry.meshops import bounding_box, clip_mesh, clip_mesh_by_stratification
from ..geometry.primitives import synapse_ellipsoids, synapse_spheres
from ..geometry.segmentation import dendrite_segmentation
from ..geometry.unify import (
    CHUNKED_DECIMATION,
    MIN_VOXEL_UM,
    RAW_FACES_KEYED_ABOVE,
    TYPED_MIN_VOXEL_UM,
    GridReuse,
    GridState,
    decimates_in_chunks,
    grid_reuse,
    raw_budget,
)
from ..geometry.volume import curve_volume_to_mesh
from ..io.corrections import CorrectionsError, CorrectionsProvider, correction_mode
from ..io.loader import NeuronLoader
from ..io.odata import ODataClient, ODataError, odata_utc
from ..io.registration import RegistrationError, RegistrationProvider
from ..network.graph import cytoscape_elements, degree_table, network_from_links
from ..network.links import all_linked_neurons, count_linked_neurons, link_report
from ..network.paths import (
    LinkGraph,
    PathQuery,
    connectivity_matrix,
    find_paths,
    intermediate_node_table,
    neuron_edges_from_structure_links,
    path_edges,
    path_nodes,
)
from ..util.memory import resident_mb, trim_memory
from .dto import (
    AnalysisResultDTO,
    AnnotationWireDTO,
    AnnotatorsDTO,
    AreaStatsDTO,
    BinStatsDTO,
    ClosedCurveDTO,
    ConnectivityDTO,
    ConnectivityEdgeDTO,
    ConnectivityMatrixDTO,
    ConnectivityNodeDTO,
    DendriteDiameterDTO,
    DeviationAnnotationsDTO,
    DeviationBiasDTO,
    DeviationDTO,
    DeviationHistogramDTO,
    DeviationMapDTO,
    DeviationResultDTO,
    DeviationSpotDTO,
    DeviationSurfaceDTO,
    DeviationZProfileDTO,
    DiffResultDTO,
    EdgeDTO,
    FreshnessDTO,
    HullDTO,
    IERatioDTO,
    InvalidEntryDTO,
    LabelResolutionDTO,
    LinkedNeuronsDTO,
    LinkPartnerDTO,
    LinkRowDTO,
    LiveRegistrationDTO,
    LocationDTO,
    ManualMovesDTO,
    MeshDiffDTO,
    MeshDTO,
    MorphometricsDTO,
    NetworkDTO,
    NeuronDetailDTO,
    NeuronSummaryDTO,
    NodeDTO,
    NodeTableDTO,
    NodeTableRowDTO,
    ResolvedSpecDTO,
    SearchResultDTO,
    SectionCorrectionDTO,
    SegmentDTO,
    ShiftSummaryDTO,
    SkeletonCheckDTO,
    SkeletonDTO,
    SkeletonProblemDTO,
    StatsDTO,
    StratificationDTO,
    SynapseTypeCountDTO,
    VolumeInfoDTO,
)
from .estimate import CURVE_RASTER_META, CostEstimator, FieldPlanner, raster_record
from .meshcache import MeshDiskCache

if TYPE_CHECKING:
    from ..export.bulk import SwcArchive

__all__ = ["SbfsemService", "NeuronNotLoadedError", "canonical_method"]

log = logging.getLogger(__name__)


class NeuronNotLoadedError(KeyError):
    """The requested neuron is not in the service's cache."""


class BuildInterrupted(RuntimeError):
    """A build stopped because the service is stopping (a deploy or a restart),
    not because of anything in the build: it is to be asked for again once the
    service is back, not reported as a failure of its settings."""


#: How long a build whose process died waits to hear that the service is
#: stopping before it is taken for an out-of-memory kill.  systemd stops the
#: whole unit at once, so the build processes get the SIGTERM this process does,
#: and the pool breaks within a second of it, before or after this process has
#: run its handler (``SbfsemService.note_stopping``).
STOP_GRACE_S = 2.0


#: The method names a build answers to, each as the name a mesh is cached under
#: (``tube`` is ``curvemesh``, ``curve``, ``outline`` and ``volume`` are
#: ``closedcurve``, ``mixed`` is ``hybrid``).  A method outside this list used to
#: build a plain cylinder under a cache key of its own, so a mistyped one was
#: answered, and kept, as if it were real.
METHOD_NAMES = {
    "cylinder": "cylinder",
    "curvemesh": "curvemesh", "tube": "curvemesh",
    "closedcurve": "closedcurve", "curve": "closedcurve", "outline": "closedcurve",
    "volume": "closedcurve",
    "hybrid": "hybrid", "mixed": "hybrid",
}


def canonical_method(method: object) -> str:
    """The name a build and its cache know ``method`` by, or a ValueError naming the choices."""
    try:
        return METHOD_NAMES[str(method).strip().lower()]
    except KeyError:
        raise ValueError(
            f"method must be one of {', '.join(sorted(METHOD_NAMES))}, not {method!r}"
        ) from None


@dataclass(frozen=True)
class _Key:
    source: str
    neuron_id: int


#: The copy of each cell the calls in this context are about (a
#: :func:`copy_tag`).  The web layer sets it from the page's ``copy``
#: parameter; ``None`` means the copy loaded most recently.
_REQUEST_COPY: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sbfsem_request_copy", default=None)


def copy_tag(mode: str, live: bool) -> str:
    """The name of a loaded copy's setting: Viking's correction mode, then 1
    when the volume's current section transforms were asked for
    (``"curvefit.1"``, ``"off.0"``)."""
    return f"{mode}.{int(bool(live))}"


def positions_token(neuron) -> str | None:
    """What moved a cell's annotations from where Viking stores them: the token
    of each move applied (the current section transforms, then the section
    correction, then a person's own moves), ``None`` at the stored positions.

    A copy's tag says what was asked for; this says what was done.  A
    correction asked for and not applied (the service down) leaves no token, so
    a surface built from that copy is keyed with the stored positions it was
    built from, and a later load of the same tag that is corrected is keyed
    apart from it.  The disk mesh key carries it, and so does the summary, for
    the browser's own cache.
    """
    tokens = [f"{name}:{t}" for name, t in (
        ("live-registration", (getattr(neuron, "live_registration", None) or {}).get("token")),
        ("section-correction", (getattr(neuron, "section_correction", None) or {}).get("token")),
        ("manual", (getattr(neuron, "manual_moves", None) or {}).get("token")),
    ) if t]
    return "|".join(tokens) or None


def _uses_whole_cell_grid(method: str | None, options: dict) -> bool:
    """Whether a build's surface comes from the whole-cell grid (``unify``): a Voxel
    remesh, Convolution or Ultraliser pathway under any method but Closed curve,
    which reads no pathway (a Hybrid's disc half reads it).  ``method`` None:
    decided by the pathway alone."""
    if method is not None and str(method).lower() == "closedcurve":
        return False
    return str(options.get("pathway", "")).lower() in FIELD_PATHWAYS


#: The pathways whose whole-cell field takes Hybrid's outline raster in, so the
#: outlines and the discs are one surface (2.7.0).  Convolution sums kernels
#: (a raster has no potential), and the piece pathways have no field: under
#: them Hybrid is still the two halves side by side.
HYBRID_ONE_FIELD = ("voxel2", "ultraliser")


def _hybrid_token(method: str, options: dict, neuron: Neuron | None) -> str | None:
    """``hybrid:one-field`` for a Hybrid build that is now one surface: a cell
    with traced outlines under a pathway of :data:`HYBRID_ONE_FIELD`.  Until
    2.7.0 its two halves were built apart and laid side by side, and that surface
    may be on disk under the same options.  Every other surface keeps the key it
    had, so no surface but these is built again (CLAUDE.md ground rule 12, as
    :func:`_budget_token`)."""
    if str(method).lower() not in ("hybrid", "mixed"):
        return None
    if str(options.get("pathway", "")).lower() not in HYBRID_ONE_FIELD:
        return None
    if neuron is not None and not getattr(neuron, "has_closed_curves", False):
        return None
    return "hybrid:one-field"


def curve_averaging(method: str, options: Mapping[str, Any]) -> bool:
    """Whether a build averages a closed-curve raster (its labels and passes, which the
    build gate prices): Closed curve for either strength, Hybrid for Path avg alone (its
    Disc avg averages the discs before their tubes, no raster), any other method never."""
    method = canonical_method(method) if method else "cylinder"
    paths = float(options.get("curve_avg_paths") or 0.0) > 0
    if method == "closedcurve":
        return paths or float(options.get("curve_avg_discs") or 0.0) > 0
    return paths if method == "hybrid" else False


def _budget_token(method: str, options: dict) -> str | None:
    """The raw-triangle budget of a Voxel remesh build, when it is over what
    every release before 2.5.1 allowed (``unify.RAW_FACES_KEYED_ABOVE``).

    Such a build can make a finer surface than the same options made before,
    so a surface stored under them must not answer it: the budget joins its
    key.  A build at or under the old ceiling is what it always was and keeps
    the key it always had, so a deploy that raises the ceiling keeps every
    stored surface that it does not change.

    Under a Face cap such a build is also decimated a chunk at a time
    (``geometry.decimate``, 2026-10-20), which places its triangles a little
    differently from the one pass before it: the key says which version did.
    """
    if not _uses_whole_cell_grid(method, options):
        return None
    faces = options.get("voxel2_faces", 0)
    budget = raw_budget(faces)
    if budget <= RAW_FACES_KEYED_ABOVE:
        return None
    return (f"raw-budget:{budget}:chunks{CHUNKED_DECIMATION}" if decimates_in_chunks(faces)
            else f"raw-budget:{budget}")


@contextlib.contextmanager
def requested_copy(tag: str | None):
    """Answer the calls inside from the ``tag`` copy of each cell they name.

    Settings are per tab, so two tabs (or two people) can look at one cell
    with different registration or correction settings at once; each is
    answered from its own copy.
    """
    token = _REQUEST_COPY.set(tag or None)
    try:
        yield
    finally:
        # A framework may run the teardown in another context; nothing to undo there.
        with contextlib.suppress(ValueError):
            _REQUEST_COPY.reset(token)


#: Peak bytes per allocated outline-raster voxel (see ``estimate_peak_mb``):
#: the tiles are a byte a voxel, the mesh made of them the rest.  Measured
#: 32 to 49 (sbfsem-measure, RPC1 c2628, RC1 c410, Inferior c16137 at 0.05
#: to 0.18 um, 2026-10-15), and 29 to 38 on c2628 at 0.05 to 0.025 um
#: (2026-10-16); it was 16 when the raster was the whole box.
CURVE_BYTES_PER_VOXEL = 50.0
#: ``build_curve_volume``'s own ceiling on the raster's tiles.
CURVE_MAX_VOXELS = 300_000_000
#: The most a closed-curve build reaches under ``build_curve_volume``'s
#: limit on the raster's surface (``closed_curve.MAX_SURFACE``), MB above
#: the 150 MB start: 120 MB plus 350 bytes for each of the up to 1.9
#: triangles of a voxel face (the measurements are with ``MAX_SURFACE``).
#: It was 2100 for the 3 million faces it was written for; it follows the
#: limit, so raising one cannot leave the other behind.
CURVE_MAX_BUILD_MB = 120.0 + 350e-6 * 1.9 * MAX_SURFACE
#: What averaging a closed-curve surface (``curve_avg_discs``, ``curve_avg_paths``)
#: adds to a build's peak: up to this multiple of it, and never more than
#: ``CURVE_AVERAGING_MAX_MB``.  Measured (2026-10-16, sbfsem-measure's peak):
#: Inferior c16137 at 0.1 um 338 -> 456 MB (x1.35), c1178 at 0.1 um 569 -> 802
#: (x1.41), RPC1 c2628 at 0.1 um 794 -> 1,122 (x1.41), c16137 at 0.025 um (3.5
#: million faces) 1,177 -> 1,837 (x1.56), c1178 at 0.05 um (3.4 million) 1,096 ->
#: 1,820 (x1.66) and c2628 at 0.025 um (10.1 million) 3,222 -> 3,880 (x1.20): the
#: surface is smoothed in slabs past 1.5 million vertices, so what it adds stops
#: growing near 700 MB.
CURVE_AVERAGING_FACTOR = 1.7
CURVE_AVERAGING_MAX_MB = 900.0
#: What joining a closed-curve surface's bodies (``curve_tie_um``) adds to a
#: build's peak, priced by the raster like the build itself: the contour of
#: every block is kept for the second pass, which redoes only the blocks the
#: ties reach, and the first surface and its pieces are held meanwhile
#: (:func:`~sbfsem.geometry.curve_ties.tie_bodies`), all of it growing with the
#: surface.  Measured (2026-10-16, sbfsem-measure's peak, Join bodies 2 um, off ->
#: on): Inferior c1178 at 0.1 um 520 -> 912 MB (1.1 million triangles, 30 bytes a
#: raster voxel), at 0.05 um 1,074 -> 1,704 (3.4 million, 17); RPC1 c2628 at 0.05 um
#: 1,352 -> 2,260 (3.8 million, 25), at 0.025 um 3,231 -> 5,835 (11.0 million, 24;
#: 196 to 355 bytes a triangle).  The cap was 900 MB, which a surface of 12
#: million triangles passed by 1.6 GB.  It now follows ``MAX_SURFACE`` as
#: ``CURVE_MAX_BUILD_MB`` does: 260 bytes for each of up to 1.9 triangles a face.
CURVE_TIE_BYTES_PER_VOXEL = 35.0
CURVE_TIE_MAX_MB = 260e-6 * 1.9 * MAX_SURFACE
#: Peak bytes for each raw triangle a Voxel remesh build (voxel2, convolution,
#: ultraliser) marches beyond ``RAW_FACES_KEYED_ABOVE``, the 3 million its
#: per-annotation figures (``estimate_peak_mb``) were measured at.  Measured
#: (sbfsem-measure, Inferior c1178, every triangle kept, 2026-10-16): a peak
#: of 310 MB plus 445 bytes a triangle from 2.1 to 7.1 million (Convolution
#: 463, Ultraliser 406 at 2.6 and 3.4 million); priced at 500 for the geometry
#: that fills its budget, which c1178's does not (its areas overlap, so a
#: budget of 10 million holds about 7 million of its triangles).
FIELD_BYTES_PER_RAW_FACE = 500.0
#: Under a Face cap the sheet is decimated, and until 2026-10-20 the decimator held
#: a second copy of it: 356 to 384 bytes more for each raw triangle (4.3 million
#: down to 2 million peaked at 3,842 MB; 7.1 million down to 3.4 million at
#: 5,615 MB, against 3,467 with every triangle kept).  A cap whose raw budget is
#: over ``RAW_FACES_KEYED_ABOVE`` -- every build priced past it here -- is now
#: decimated a chunk at a time (``geometry.decimate``), and the decimator holds a
#: chunk of up to a million raw triangles: c1178 at 0.04 um down to 3.4 million
#: peaked at 3,148 MB, under the price of keeping every triangle, and at 0.07 um
#: down to 1.2 million (2.5 million raw) at 1,784 MB, 366 over it, a chunk and the
#: triangles kept.  Priced at the sheet with every triangle kept plus this.
FIELD_CHUNKED_DECIMATION_MB = 500.0
#: A whole-cell sheet as the measurements on c1178 above put it (2.5.1): a peak of
#: 310 MB plus 445 bytes a raw triangle, and 370 more for each when a Face cap of
#: a million or less (a raw budget of 3 million or less, decimated whole)
#: decimates it (the decimator's copy).  What a typed Fine voxel under the
#: automatic floor is priced at, for the whole raw budget it may march, and every
#: field build at the sheet it is planned to draw (``estimate_peak_mb``).
FIELD_SHEET_BASE_MB = 310.0
FIELD_SHEET_BYTES_PER_FACE = 445.0
FIELD_SHEET_BYTES_PER_DECIMATED_FACE = 370.0


def field_sheet_mb(raw_triangles: int, face_cap: int, reduce_fraction: float = 1.0, *,
                   chunked: bool | None = None) -> float:
    """The peak, MB, of a field build that draws ``raw_triangles`` (``FIELD_SHEET_*``).

    The decimator's copy is counted only when it runs: a sheet over its Face cap,
    or a Decimate under 1 (``cylinder_render``'s ``decimate``).  A sheet under its
    cap is kept as drawn: Temporal c106 at the default cap drew 323,256 and
    peaked at 380 MB, which the copy would price at 573 (2026-10-20).  A cap whose
    raw budget is over 3 million (``chunked``; by default
    ``unify.decimates_in_chunks`` of ``face_cap``) decimates the sheet a chunk at a
    time, and the decimator then holds a chunk, not a copy of the sheet
    (``FIELD_CHUNKED_DECIMATION_MB``).
    """
    decimated = 0 < int(face_cap) < int(raw_triangles) or float(reduce_fraction) < 1.0
    if chunked is None:
        chunked = decimates_in_chunks(int(face_cap))
    per_face = FIELD_SHEET_BYTES_PER_FACE
    extra = 0.0
    if decimated and chunked:
        extra = FIELD_CHUNKED_DECIMATION_MB
    elif decimated:
        per_face += FIELD_SHEET_BYTES_PER_DECIMATED_FACE
    return FIELD_SHEET_BASE_MB + int(raw_triangles) * per_face / 1e6 + extra


_CHILD_SERVICE: SbfsemService | None = None
#: In a build-pool worker: the queue its progress reports go back on.
_CHILD_PROGRESS = None


def _child_init(queue) -> None:
    """Build-pool worker start-up: keep the queue progress goes back on."""
    global _CHILD_PROGRESS
    _CHILD_PROGRESS = queue


def _child_warm() -> float | None:
    """Make a build-pool worker ready to build, and say what it holds (MB).

    Sent to the first worker of a pool made to replace one (``_retire_pool``),
    so the spawn and the imports are paid while nothing waits on them.
    """
    global _CHILD_SERVICE
    if _CHILD_SERVICE is None:
        _CHILD_SERVICE = SbfsemService(offline=True)
    return resident_mb()


def _child_build(neuron: Neuron, method: str, smooth_iterations: int,
                 reduce_fraction: float, pixel_size_um: float, options: dict,
                 progress_token: str | None = None, grid_state: GridState | None = None,
                 keep_grid: bool = False) -> tuple[Mesh, float | None, GridState | None]:
    """The pure geometry step, run inside a build-pool worker process.

    A throwaway offline service does the building: ``_build_mesh`` needs no
    Viking, no cache and no lock, only the neuron it is handed.  With a
    ``progress_token`` its stage reports go back to the parent, tagged with
    it, at most four a second.  Returns the mesh, what the worker still
    holds after it (MB, :func:`~sbfsem.util.memory.resident_mb`), its heap
    trimmed first, which the parent replaces the pool by, and, with
    ``keep_grid``, the whole-cell grid the build left (built on ``grid_state``
    when one is handed over: :class:`~sbfsem.geometry.unify.GridReuse`).
    """
    _child_warm()
    listener = None
    queue = _CHILD_PROGRESS
    if progress_token is not None and queue is not None:
        def forward(stage, done, total, _q=queue, _t=progress_token):
            _q.put((_t, stage, done, total))
        listener = progress.Throttle(forward)
    try:
        try:
            reuse = GridReuse(state=grid_state, keep=True) if keep_grid else None
            with progress.reporting(listener), grid_reuse(reuse):
                assert _CHILD_SERVICE is not None
                mesh = _CHILD_SERVICE._build_mesh(
                    neuron, method, smooth_iterations, reduce_fraction, pixel_size_um, options
                )
        except Exception as exc:
            # A failed build can leave the worker as big as a finished one: the
            # parent reads this off the exception, which travels back pickled
            # with its attributes.
            trim_memory()
            exc.held_mb = resident_mb()  # type: ignore[attr-defined]
            raise
        # What the build freed, back to the system before the worker is measured:
        # in the parent this ran after every build, and in a worker never.
        trim_memory()
        return mesh, resident_mb(), (reuse.out if reuse is not None else None)
    finally:
        # The end marker: the reports travel apart from the result, and the
        # parent waits for this one so the last of them is not dropped.
        if listener is not None and queue is not None:
            queue.put((progress_token, None, None, None))


def _drain_progress(queue, listeners: dict) -> None:
    """Hand each worker report to the build waiting on it; None stops."""
    while True:
        try:
            item = queue.get()
        except (EOFError, OSError):
            return
        if item is None:
            return
        token, stage, done, total = item
        listener = listeners.get(token)
        if listener is not None:
            # Progress must never break a build.
            with contextlib.suppress(Exception):
                listener(stage, done, total)


class SbfsemService:
    """Loads, caches, renders and analyzes neurons for a frontend."""

    #: How many built meshes to keep. Each render option combination is a
    #: separate entry — dragging the decimate slider makes one per position — so
    #: an unbounded cache grows without limit in a long-running server. A large
    #: closed-curve mesh is a few hundred MB, so this ceiling is what keeps the
    #: process inside its memory budget.
    MESH_CACHE_SIZE = 12

    #: A build process that still holds more than this (MB) after a build, its
    #: heap trimmed, is let go with its pool (``_retire_pool``).  An idle worker
    #: holds about 250 MB; after RPC1 c2628 and Inferior c1178 builds at their
    #: finest, one held 2.3 GB for as long as the service ran (2026-10-16).
    BUILD_RECYCLE_MB = 1024.0

    #: The whole-cell grids kept for building a cell again where its annotations were
    #: moved by hand (:class:`~sbfsem.geometry.unify.GridState`), MB in all; the least
    #: recently used goes first.  ``SBFSEM_GRID_CACHE_MB`` sets it (0: none kept).
    #: c1178 holds 21 MB under Voxel remesh, 45 MB under Hybrid.  Kept only for a
    #: cell move mode asked for (:meth:`prepare_grid`) or one with moves.
    GRID_CACHE_MB = 512.0
    #: The cells and settings move mode asked to keep grids for, the oldest let go first.
    GRID_WANTED = 64

    #: The finished closed-curve replies kept (:meth:`closed_curves_json`), MB in
    #: all; the least recently used goes first.  RPC1 c430's 897 outlines are
    #: 7.6 MB of JSON, Inferior c1178's 155 are 3.6 MB.
    OUTLINE_REPLY_CACHE_MB = 128.0

    def __init__(self, *, offline: bool = False, cache_dir=None,
                 mesh_cache_size: int | None = None, build_processes: int = 0,
                 build_recycle_mb: float | None = None,
                 untagged_psd: str = "assume", section_corrections: bool | str = False,
                 lab_corrections: Mapping[str, bool | str] | None = None,
                 corrections_url: str | None = None, live_registration: bool = False):
        self.offline = offline
        # Viking's current section transforms (sbfsem.io.registration): what
        # a load that does not say gets.  Off here, as the library is (MATLAB
        # read the stored positions); the web app turns it on.
        self.live_registration = bool(live_registration)
        self._registrations: RegistrationProvider | None = None
        # Viking's section correction (sbfsem.io.corrections): the mode a load
        # that does not say gets -- off, neighbor (the residual field; what
        # True means), curvefit or all.  Off here, as the library is; the web
        # app uses curvefit.  ``lab_corrections`` overrides it for a lab's
        # volumes (the web app: Field + fit for the Neitz lab's).  The provider
        # is made on first use.
        self.correction_mode = correction_mode(section_corrections)
        self.lab_corrections = {lab: correction_mode(mode)
                                for lab, mode in (lab_corrections or {}).items()}
        self._corrections_url = corrections_url
        self._corrections: CorrectionsProvider | None = None
        # What a postsynapse whose Viking tags name no kind becomes (see
        # ``sbfsem.core.types.UNTAGGED_POLICIES``).  MATLAB assumed
        # conventional; the web app infers it from the linked partner.
        if untagged_psd not in UNTAGGED_POLICIES:
            raise ValueError(f"untagged_psd must be one of {UNTAGGED_POLICIES}, not {untagged_psd!r}")
        self.untagged_psd = untagged_psd
        self._cache_dir = cache_dir
        # Mesh builds hold the GIL for most of their runtime (a solve per
        # annotation), so threads stop paying past two concurrent builds.
        # With ``build_processes`` > 0 the pure geometry step runs in a pool
        # of child processes -- one per vCPU is the setting on a bigger box --
        # while the calling thread waits; caching, clipping and depth
        # coloring stay in this process.
        self._build_processes = max(0, int(build_processes))
        # 0 or less: a worker is never replaced for what it holds.
        self._build_recycle_mb = float(
            self.BUILD_RECYCLE_MB if build_recycle_mb is None else build_recycle_mb)
        # Set when this process is told to stop (note_stopping): a build process
        # that dies then was stopped with it, not killed for memory.
        self.stopping = threading.Event()
        self._process_pool: ProcessPoolExecutor | None = None
        # How many pools were replaced for what a worker held (_retire_pool).
        self.pool_recycles = 0
        # Progress from the build processes: one queue back, read by one
        # thread, handed to the listener of the build each report names.
        self._progress_queue: Any = None
        self._progress_listeners: dict[str, progress.Listener] = {}
        self._neurons: dict[_Key, Neuron] = {}
        # Every copy of a cell held, by the setting it was loaded with
        # (:func:`copy_tag`).  Settings are per tab, so two tabs looking at one
        # cell with different settings each have a copy of their own and never
        # change each other's.  ``_neurons`` points at the copy loaded most
        # recently: the one a call that names no copy is answered from.
        self._copies: dict[_Key, dict[str, Neuron]] = {}
        # Each copy of a cell put in the table gets the next number
        # (``Neuron.load_serial``).  A cell loaded again -- another
        # registration setting, an update from Viking -- is another copy, and
        # what was built from the old one must not be handed out for it.
        self._load_serials = itertools.count(1)
        self._meshes: OrderedDict[tuple, Mesh] = OrderedDict()
        # The last whole-cell grid of each cell, setting and render (_grid_key): a
        # build of the cell's annotations moved by hand contours only the blocks the
        # moves touched on it (2026-10-20).
        self._grid_states: OrderedDict[tuple, GridState] = OrderedDict()
        # The (cell, setting) pairs move mode asked for: only their builds keep a grid,
        # so a cell nobody moves pays nothing for it.
        self._grid_wanted: OrderedDict[tuple, None] = OrderedDict()
        try:
            self._grid_cache_mb = float(os.environ.get("SBFSEM_GRID_CACHE_MB", self.GRID_CACHE_MB))
        except ValueError:
            self._grid_cache_mb = self.GRID_CACHE_MB
        self._mesh_cache_size = (
            self.MESH_CACHE_SIZE if mesh_cache_size is None else max(1, mesh_cache_size)
        )
        self._clients: dict[str, ODataClient] = {}
        # One fetch per cell at a time: a batch that names a cell twice, or
        # two tabs loading it, share the Viking round trip.
        self._loading: dict[_Key, threading.Lock] = {}
        # Built meshes outlive the process and the in-memory ceiling.
        self._disk_meshes = (
            MeshDiskCache(Path(cache_dir) / "meshes") if cache_dir else None
        )
        # Annotation deviations take seconds to a minute per rendering on a
        # large cell (the error map of a 200,000-vertex Voxel remesh, measured
        # 2026-10-15: 3.5 s for RC2 c74420, 10 s for Inferior c1178, 53 s for
        # RPC1 c2628, which has 3,978 outlines; it was 90 to 412 s before the
        # distance kernel was rewritten) and depend only on (cell, options);
        # re-pressing Compare must not pay that again for panes that did not
        # change.
        self._deviations: OrderedDict[tuple, DeviationDTO | None] = OrderedDict()
        # The point-by-point halves of a deviation (an error map) and of a
        # mesh-to-mesh difference.  Each is the answer to minutes of work on a
        # large cell and a few MB of arrays; the keys end in the copy's serial
        # like a mesh's, so a reloaded cell drops them with it.
        self._deviation_maps: OrderedDict[tuple, Any] = OrderedDict()
        self._differences: OrderedDict[tuple, Any] = OrderedDict()
        # The closed-curve replies as the JSON text sent (closed_curves_json),
        # keyed by what decides them rather than by the copy, so a copy loaded
        # again at positions already seen -- a Registration switch back, an
        # update that found nothing new -- is answered from here.
        self._outline_replies: OrderedDict[tuple, bytes] = OrderedDict()
        # One build per mesh at a time: a pane's mesh job and the measurement
        # job that needs the same mesh must not each build it.
        self._flights: dict[tuple, list] = {}
        # The neuron-level link graph of each volume, grown by connectivity
        # queries one hop at a time; a partner list fetched for one query
        # answers the next.  Each graph has its own lock so a slow Viking
        # fetch does not hold up unrelated requests.
        self._link_graphs: dict[str, tuple[LinkGraph, threading.Lock]] = {}
        # What a Closed curve or Hybrid render would cost, answered before it is
        # built (sbfsem.service.estimate), and the raster counts builds leave it.
        self._estimator = CostEstimator(
            lambda neuron, discs: _curve_outlines(neuron, discs=discs),
            self._disc_half_peak_mb,
        )
        # And the Fine voxel a Voxel remesh, Convolution or Ultraliser build would
        # contour at, sized as the build sizes it, before it is built.
        self._field_planner = FieldPlanner(
            self._field_pieces,
            lambda neuron, o: self.estimate_peak_mb(
                neuron.id, neuron.source, method=str(o.get("method", "cylinder")),
                pathway=str(o["pathway"]), meshlab=str(o.get("meshlab", "none")),
                meshlab_depth=int(o.get("meshlab_depth", 9)),
                voxel2_faces=int(o["voxel2_faces"]), voxel2_um=float(o["voxel2_um"]),
                field_options=o, neuron=neuron),
            field_sheet_mb,
            self._hybrid_halves,
        )
        self._lock = threading.RLock()

    # -- infrastructure ----------------------------------------------------
    def client(self, source) -> ODataClient:
        volume = Volumes.from_str(source)
        with self._lock:
            if volume.source not in self._clients:
                from ..io.cache import DiskCache

                self._clients[volume.source] = ODataClient(
                    volume,
                    offline=self.offline,
                    cache=DiskCache(self._cache_dir) if self._cache_dir else None,
                )
            return self._clients[volume.source]

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()
            pool, self._process_pool = self._process_pool, None
            queue, self._progress_queue = self._progress_queue, None
            corrections, self._corrections = self._corrections, None
            registrations, self._registrations = self._registrations, None
        if corrections is not None:
            corrections.close()
        if registrations is not None:
            registrations.close()
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        if queue is not None:
            with contextlib.suppress(Exception):
                queue.put(None)

    def note_stopping(self) -> None:
        """This process has been told to stop: a build process dying from now on
        stopped with it (:class:`BuildInterrupted`)."""
        self.stopping.set()

    def _pool(self) -> ProcessPoolExecutor | None:
        if not self._build_processes:
            return None
        with self._lock:
            if self._process_pool is None:
                # spawn, not fork: the parent runs uvicorn's threads and the
                # job workers, and a forked child inheriting their locks is a
                # deadlock waiting to happen.  Workers import sbfsem once and
                # then persist, until a build leaves one too big (_retire_pool).
                context = multiprocessing.get_context("spawn")
                self._progress_queue = context.Queue()
                self._process_pool = ProcessPoolExecutor(
                    max_workers=self._build_processes,
                    mp_context=context,
                    initializer=_child_init,
                    initargs=(self._progress_queue,),
                )
                threading.Thread(
                    target=_drain_progress,
                    args=(self._progress_queue, self._progress_listeners),
                    name="build-progress", daemon=True,
                ).start()
            return self._process_pool

    def _drop_pool(self, pool: ProcessPoolExecutor) -> None:
        """Forget a build pool that can build no more, so the next build makes a new one.

        A build process killed from outside (the kernel's out-of-memory killer
        takes the largest) leaves a ProcessPoolExecutor broken for good: every
        later submit raises, and until 2026-10-16 every build after it failed
        until the service was restarted.  Only the pool the caller saw is
        dropped, so several builds that failed together drop it once.
        """
        with self._lock:
            if self._process_pool is not pool:
                return
            self._process_pool = None
            queue, self._progress_queue = self._progress_queue, None
        with contextlib.suppress(Exception):
            pool.shutdown(wait=False, cancel_futures=True)
        if queue is not None:
            with contextlib.suppress(Exception):
                queue.put(None)  # stops the thread reading this pool's reports

    def _retire_pool(self, pool: ProcessPoolExecutor, held_mb: float, neuron: Neuron,
                     method: str) -> None:
        """Let a pool go because a worker kept a heavy build's memory, and warm its successor.

        glibc gives back only part of what a large build frees: after heavy
        builds one worker held 2.3 GB, and the service idled at 4.7 GB instead of
        1.5 GB, for as long as it ran.  A worker cannot be replaced alone (a
        ProcessPoolExecutor whose worker exits is broken), so the next build goes
        to a new pool, and the old one is shut down from a thread of its own: the
        builds already running in it finish and report on its own queue, and its
        workers exit once the last of them is done (CPython stops none of a
        pool's workers before all its work is), the big one with them.  Then its
        queue's reader stops.  The new pool's first worker is started now, so the
        next build does not wait for the spawn and the imports.
        """
        if not self._replace_pool(pool, held_mb=held_mb):
            return  # another build in the same pool retired it first
        log.info("a build process holds %.0f MB after c%s's %s build (over %.0f MB): "
                 "its pool was replaced", held_mb, neuron.id, method, self._build_recycle_mb)

    def recycle_build_workers(self) -> bool:
        """Replace the build pool now, as a heavy build does by itself: the Settings tab's action.

        The builds running finish in the old pool (``_retire_pool`` says how);
        the next one goes to the new pool.  ``False`` when there is no pool to
        replace: builds run in this process, or none has been made yet.
        """
        with self._lock:
            pool = self._process_pool
        if pool is None or not self._replace_pool(pool):
            return False
        log.info("the build pool was replaced on request")
        return True

    def _replace_pool(self, pool: ProcessPoolExecutor, *, held_mb: float | None = None) -> bool:
        """Let ``pool`` go and start its successor; ``False`` when it is no longer the current one.

        ``held_mb`` is given when a worker's memory is the reason, and counts the
        replacement in ``pool_recycles``.
        """
        with self._lock:
            if self._process_pool is not pool:
                return False
            self._process_pool = None
            queue, self._progress_queue = self._progress_queue, None
            if held_mb is not None:
                self.pool_recycles += 1

        def wind_down() -> None:
            with contextlib.suppress(Exception):
                pool.shutdown(wait=True)
            if queue is not None:
                with contextlib.suppress(Exception):
                    queue.put(None)  # every report of its builds is in the queue before this

        threading.Thread(target=wind_down, name="build-pool-retire", daemon=True).start()
        fresh = self._pool()
        if fresh is not None:
            with contextlib.suppress(Exception):
                fresh.submit(_child_warm)
        return True

    def build_pool_report(self) -> dict:
        """The build processes as the Settings tab shows them."""
        with self._lock:
            running = self._process_pool is not None
        return {"build_processes": self._build_processes, "pool_running": running,
                "pool_recycles": self.pool_recycles, "recycle_mb": self._build_recycle_mb}

    # -- the last grid, for building again where annotations were moved ------------------
    def _grid_key(self, neuron: Neuron, method: str, smooth_iterations: int,
                  reduce_fraction: float, pixel_size_um: float, options: dict) -> tuple | None:
        """Which kept grid a build of ``neuron`` can use and leaves: the cell, the
        registration setting its copy was loaded with (manual moves taken off the tag),
        and the render; None for a technique with no whole-cell grid."""
        method = canonical_method(method)
        if not _uses_whole_cell_grid(method, options) or self._grid_cache_mb <= 0:
            return None
        base = str(getattr(neuron, "copy_tag", None) or "").split(".m", 1)[0]
        return (_Key(neuron.source, int(neuron.id)), base, method, int(smooth_iterations),
                float(reduce_fraction), float(pixel_size_um), self._options_key(options, method))

    def _grid_for(self, key: tuple | None) -> GridState | None:
        if key is None:
            return None
        with self._lock:
            state = self._grid_states.get(key)
            if state is not None:
                self._grid_states.move_to_end(key)
            return state

    def _keep_grid(self, key: tuple | None, state: GridState | None) -> None:
        """Keep ``state`` as the grid of ``key``, dropping the least recently used past
        the cap."""
        if key is None or state is None:
            return
        cap = self._grid_cache_mb * 1e6
        with self._lock:
            self._grid_states[key] = state
            self._grid_states.move_to_end(key)
            total = sum(st.nbytes() for st in self._grid_states.values())
            while total > cap and len(self._grid_states) > 1:
                _old, dropped = self._grid_states.popitem(last=False)
                total -= dropped.nbytes()
            if total > cap:
                self._grid_states.clear()

    def _want_grid(self, key: tuple) -> None:
        with self._lock:
            self._grid_wanted[key[:2]] = None
            self._grid_wanted.move_to_end(key[:2])
            while len(self._grid_wanted) > self.GRID_WANTED:
                self._grid_wanted.popitem(last=False)

    def _grid_context(self, neuron: Neuron, key: tuple | None) -> GridReuse | None:
        """A build's grid reuse: a copy with manual moves builds on the kept grid, and
        it, or a cell move mode asked for (:meth:`prepare_grid`), keeps the grid it
        makes.  A plain copy never builds on one (its surface never depends on what was
        built before it), and a cell nobody moves keeps none."""
        if key is None:
            return None
        moved = bool(getattr(neuron, "manual_moves", None))
        with self._lock:
            wanted = key[:2] in self._grid_wanted
        if not (moved or wanted):
            return None
        return GridReuse(state=self._grid_for(key) if moved else None, keep=True)

    def prepare_grid(self, neuron_id: int, source, *, neuron: Neuron | None = None, **params) -> dict:
        """Make sure a grid is kept for the cell's render (``params``: the mesh route's
        options), building it once when none is: asked when move mode starts, so the
        first Apply already contours only the blocks its moves touched.  Returns
        ``{ready, built, seconds}``."""
        if neuron is None:
            neuron = self.neuron(neuron_id, source)
        params = dict(params)
        method = canonical_method(params.pop("method", "cylinder"))
        smooth = int(params.pop("smooth_iterations", 1))
        reduce = float(params.pop("reduce_fraction", 1.0))
        pixel = float(params.pop("pixel_size_um", 0.05))
        for view in ("clip_z", "clip_ipl", "keep_above", "color_by_strata"):
            params.pop(view, None)
        options = self._mesh_options(**params)
        key = self._grid_key(neuron, method, smooth, reduce, pixel, options)
        if key is None:
            return {"ready": False, "built": False, "seconds": 0.0,
                    "reason": "this technique builds no whole-cell grid"}
        self._want_grid(key)
        if self._grid_for(key) is not None:
            return {"ready": True, "built": False, "seconds": 0.0}
        started = time.perf_counter()
        self._run_build(neuron, method, smooth, reduce, pixel, options)
        return {"ready": self._grid_for(key) is not None, "built": True,
                "seconds": round(time.perf_counter() - started, 2)}

    def _run_build(self, neuron: Neuron, method: str, smooth_iterations: int,
                   reduce_fraction: float, pixel_size_um: float, options: dict) -> Mesh:
        gkey = self._grid_key(neuron, method, smooth_iterations, reduce_fraction, pixel_size_um, options)
        reuse = self._grid_context(neuron, gkey)
        started = time.perf_counter()
        pool = self._pool()
        if pool is None:
            with grid_reuse(reuse):
                mesh = self._build_mesh(
                    neuron, method, smooth_iterations, reduce_fraction, pixel_size_um, options
                )
            self._note_grid(neuron, gkey, reuse, reuse.out if reuse else None, mesh, started)
            return mesh
        # Whoever listens to this build (a mesh job) hears the child's
        # reports through the queue, by a token of this build's own.
        listener = progress.current()
        token = uuid.uuid4().hex if listener is not None else None
        ended = threading.Event()
        if token is not None and listener is not None:
            def relay(stage, done, total, _listener=listener):
                if stage is None:
                    ended.set()
                else:
                    _listener(stage, done, total)
            self._progress_listeners[token] = relay
        try:
            def hand_over(to: ProcessPoolExecutor):
                return to.submit(_child_build, neuron, method, smooth_iterations,
                                 reduce_fraction, pixel_size_um, options, token,
                                 reuse.state if reuse else None, reuse is not None)

            try:
                future = hand_over(pool)
            except BrokenProcessPool:
                # Broken before this build was handed over (a worker killed
                # while idle, or by an earlier build): nothing of this one has
                # run, so it goes to a pool of its own.
                self._drop_pool(pool)
                pool = self._pool()
                assert pool is not None
                future = hand_over(pool)
            except RuntimeError:
                # Shut down between this build taking it and handing over: another
                # build retired it for its size (submit says "cannot schedule new
                # futures after shutdown").  Nothing of this one has run either.
                fresh = self._pool()
                if fresh is None or fresh is pool:
                    raise  # the service is closing
                pool = fresh
                future = hand_over(pool)
            try:
                mesh, held_mb, grid_out = future.result()
            except BrokenProcessPool as exc:
                # A worker died while this build was running: stopped with the
                # service (a deploy or a restart stops the whole unit), or else
                # almost always the out-of-memory killer.  Running it again would
                # likely kill the next one, so say so; the pool is made anew for
                # whatever is asked next.
                self._drop_pool(pool)
                if self.stopping.wait(STOP_GRACE_S):
                    # On 2026-10-20 a deploy at 17:42:41 UTC stopped c1178's build,
                    # and the page said the server had most likely run out of
                    # memory and to ask for a coarser setting.
                    log.info("c%s's build (%s) stopped with the service", neuron.id, method)
                    raise BuildInterrupted(
                        "the server restarted during this build (a new version was being "
                        "installed): ask for it again in a moment") from exc
                log.error("a build process died during c%s's build (%s): %s",
                          neuron.id, method, exc)
                raise RuntimeError(
                    "the build process stopped before this build finished (the server "
                    "most likely ran out of memory). It has been restarted: ask again, "
                    "with a coarser setting if this is a large cell"
                ) from exc
            except Exception as exc:
                # A build that failed in the worker can leave it as big as one that
                # finished; the worker says so on the exception (_child_build).
                failed_mb = getattr(exc, "held_mb", None)
                if failed_mb is not None and 0 < self._build_recycle_mb < failed_mb:
                    self._retire_pool(pool, failed_mb, neuron, method)
                raise
            if token is not None:
                ended.wait(1.0)
            if held_mb is not None and 0 < self._build_recycle_mb < held_mb:
                self._retire_pool(pool, held_mb, neuron, method)
            self._note_grid(neuron, gkey, reuse, grid_out, mesh, started)
            return mesh
        finally:
            if token is not None:
                self._progress_listeners.pop(token, None)

    def _note_grid(self, neuron: Neuron, key: tuple | None, reuse: GridReuse | None,
                   state: GridState | None, mesh: Mesh, started: float) -> None:
        """Keep the grid a build left, and say in the log when it built on a kept one."""
        self._keep_grid(key, state)
        reused = mesh.meta.get("voxel2_reused_blocks") if not mesh.is_empty else None
        if reuse is not None and reuse.state is not None and reused is not None:
            blocks = len(state.signatures) if state is not None else 0
            again = mesh.meta.get("voxel2_smoothed_again")
            log.info("c%s %s: built on the last grid, %d of %d blocks contoured again, ties %s, "
                     "%s smoothed again, %.2f s",
                     neuron.id, neuron.volume.display_name, max(blocks - int(reused), 0), blocks,
                     "kept" if mesh.meta.get("voxel2_ties_kept") else "found afresh",
                     f"{int(again):,} vertices" if again is not None else "the whole surface",
                     time.perf_counter() - started)

    # -- volumes -----------------------------------------------------------
    def list_volumes(self) -> list[VolumeInfoDTO]:
        return [self.volume_info(v) for v in Volumes]

    def volume_info(self, source) -> VolumeInfoDTO:
        volume = Volumes.from_str(source)
        return VolumeInfoDTO(
            name=volume.source,
            display_name=volume.display_name,
            abbrev=volume.abbrev,
            animal=volume.animal,
            is_odata=volume.is_odata,
            has_boundary=volume.has_boundary,
            has_cones=volume.has_cones,
            has_custom_transform=volume.has_custom_transform,
            n_sections=volume.n_sections,
            volume_scale=list(volume.cached_scale),
            service_root=volume.service_root if volume.is_odata else None,
            viking_name=volume.viking_name,
            has_transforms=volume.vikingxml_url is not None,
            lab=volume.lab,
            default_correction=self.default_correction(volume),
            default_registration=self.live_registration,
        )

    # -- loading -----------------------------------------------------------
    def load_neuron(
        self,
        neuron_id: int,
        source,
        *,
        include_synapses: bool = True,
        transform: str = "Standard",
        refresh: bool = False,
        section_corrections: bool | str | None = None,
        live_registration: bool | None = None,
        manual_moves: Mapping[str, Any] | None = None,
    ) -> NeuronDetailDTO:
        """Fetch a neuron from Viking (or the cache) and keep it in memory.

        The Viking round trip runs outside the service lock, so a batch of
        cells fetches in parallel; only the table insert is serialized.
        ``refresh`` skips the disk cache for this fetch (the cache never
        expires on its own), which is what "update from Viking" means.
        ``live_registration`` draws the annotations where the volume's current
        section transforms put them (``sbfsem.io.registration``) rather than
        at their stored positions; ``section_corrections`` then adds Viking's
        section correction in a mode (:func:`~sbfsem.io.corrections.correction_mode`:
        off, neighbor, curvefit or all; ``True`` is neighbor).  ``None`` takes
        the service's default for either; a loaded cell asked for with another
        setting is loaded again with it.  A refresh also re-reads the cell's
        stos files.

        ``manual_moves`` maps a setting's copy tag (``"curvefit.1"``) to the
        move set a person made under it (:func:`~sbfsem.core.moves.normalize_moves`);
        the set of the setting loaded, if any, is applied to a copy of the
        cell made from the one loaded with it, named by the set's token
        (``"curvefit.1.m<token>"``).  The sets of other settings are not
        applied: a move is made relative to where the setting drew the
        annotation.
        """
        volume = Volumes.from_str(source)
        key = _Key(volume.source, int(neuron_id))
        want = (self.default_correction(volume) if section_corrections is None
                else correction_mode(section_corrections))
        live = self.live_registration if live_registration is None else bool(live_registration)
        tag = copy_tag(want, live)
        moves = normalize_moves((manual_moves or {}).get(tag))
        want_transform = Transforms.from_str(transform)
        with self._lock:
            if not refresh and self._satisfies(key, want, live, include_synapses, want_transform):
                return self._held(key, tag, neuron_id, volume, moves)
            gate = self._loading.setdefault(key, threading.Lock())
        with gate:
            with self._lock:
                if not refresh and self._satisfies(key, want, live, include_synapses, want_transform):
                    return self._held(key, tag, neuron_id, volume, moves)
            client = self.client(volume)
            loader = NeuronLoader(volume, client=client)
            bypass = client.fresh() if refresh else contextlib.nullcontext()
            with bypass:
                neuron = loader.load(
                    int(neuron_id),
                    include_synapses=include_synapses,
                    transform=want_transform,
                    untagged_psd=self.untagged_psd,
                )
            # What this copy was loaded with, for the next load to compare with.
            neuron.loaded_synapses = bool(include_synapses)
            if live:
                neuron.registration_status = self._register(neuron, force=refresh)
            if want != "off":
                neuron.correction_status = self._correct(neuron, want)
            with self._lock:
                self._store(key, neuron, tag)
        return self._held(key, tag, neuron_id, volume, moves)

    #: Copies made with manual moves held per cell at most (each is a whole
    #: node table): a person nudging annotations makes a new set at each step.
    MOVED_COPIES = 4

    def _held(self, key: _Key, tag: str, neuron_id: int, volume,
              moves: tuple[ManualMove, ...] = ()) -> NeuronDetailDTO:
        """A load already satisfied by the ``tag`` copy: it becomes the latest.

        With ``moves``, the copy made from it with them does (made now, unless
        one made from this very copy is held).
        """
        if not moves:
            with self._lock:
                self._neurons[key] = self._copies[key][tag]
            with requested_copy(tag):
                return self.neuron_detail(neuron_id, volume)
        moved_tag = f"{tag}.m{moves_token(moves)}"
        with self._lock:
            copies = self._copies[key]
            base = copies[tag]
            held = copies.get(moved_tag)
            fresh = held is not None and held.base_serial == base.load_serial
            if held is not None and fresh:
                self._neurons[key] = held
        if not fresh:
            # Pandas work on the cell's own table, outside the lock.
            derived = moved_copy(base, moves)
            derived.base_serial = base.load_serial
            log.info("c%s %s: %d manual move(s) under %s, %d applied, longest %.2f um",
                     neuron_id, volume.display_name, len(moves), tag,
                     (derived.manual_moves or {}).get("n_moved", 0),
                     (derived.manual_moves or {}).get("max_um", 0.0))
            with self._lock:
                self._store(key, derived, moved_tag)
                self._evict_moved(key, keep=moved_tag)
        with requested_copy(moved_tag):
            return self.neuron_detail(neuron_id, volume)

    def _evict_moved(self, key: _Key, keep: str) -> None:
        """Forget the cell's copies with manual moves past the newest
        :attr:`MOVED_COPIES`, and any made from a copy since loaded again
        (call under the lock); ``keep`` stays."""
        copies = self._copies.get(key, {})
        moved = [(name, n) for name, n in copies.items() if ".m" in name and name != keep]
        stale = [name for name, n in moved
                 if copies.get(name.split(".m", 1)[0]) is None
                 or n.base_serial != copies[name.split(".m", 1)[0]].load_serial]
        live = sorted((n.load_serial or 0, name) for name, n in moved if name not in stale)
        doomed = stale + [name for _, name in live[:max(0, len(live) - (self.MOVED_COPIES - 1))]]
        for name in doomed:
            self._drop_meshes(key, serial=getattr(copies.pop(name), "load_serial", None))

    def _store(self, key: _Key, neuron: Neuron, tag: str) -> None:
        """Hold ``neuron`` as the ``tag`` copy of its cell, and as its latest.

        Call under the lock.  What was built from the copy it replaces goes;
        the other settings' copies, and their meshes, stay.
        """
        neuron.load_serial = next(self._load_serials)
        neuron.copy_tag = tag
        copies = self._copies.setdefault(key, {})
        replaced = copies.get(tag)
        copies[tag] = neuron
        self._neurons[key] = neuron
        if replaced is not None:
            self._drop_meshes(key, serial=getattr(replaced, "load_serial", None))

    def _loaded(self, key: _Key) -> Neuron | None:
        """The copy of the cell this call is about (call under the lock)."""
        tag = _REQUEST_COPY.get()
        if tag is None:
            return self._neurons.get(key)
        return self._copies.get(key, {}).get(tag)

    @staticmethod
    def _tag_of(neuron: Neuron) -> str:
        """The setting a cell built elsewhere (a file) carries, as a copy tag."""
        status = getattr(neuron, "correction_status", None) or {}
        mode = status.get("mode", "neighbor") if status.get("requested") else "off"
        reg = getattr(neuron, "registration_status", None) or {}
        return copy_tag(mode, bool(reg.get("requested")))

    # -- Viking's section corrections --------------------------------------
    def default_correction(self, source) -> str:
        """The correction mode a load of this volume gets when it names none:
        its lab's (``lab_corrections``), else the service's."""
        return self.lab_corrections.get(Volumes.from_str(source).lab or "", self.correction_mode)

    def corrections(self) -> CorrectionsProvider:
        with self._lock:
            if self._corrections is None:
                from ..io.cache import DiskCache

                self._corrections = CorrectionsProvider(
                    self._corrections_url,
                    cache=DiskCache(self._cache_dir) if self._cache_dir else None,
                    offline=self.offline,
                )
            return self._corrections

    def _satisfies(self, key: _Key, want: str, live: bool = False,
                   include_synapses: bool = False, transform: Transforms | None = None) -> bool:
        """Is the loaded cell (if any) already what a load with ``want``/``live`` asks for?

        ``want`` is a correction mode; a cell corrected in another mode is
        not.  A correction or registration asked for and not applied (a
        server down) is tried again, but not while its provider pauses after a
        failure, or holds the cell (or its volume) after a failure in the service.
        Only the copy loaded with this setting is asked: a copy another tab
        loaded with another setting is not this one.

        A load that asks for the synapses is not met by a copy loaded without
        them, nor one that names a ``transform`` by a copy in another (a
        copy that was not loaded here, from a file, is taken as it is).
        """
        neuron = self._copies.get(key, {}).get(copy_tag(want, live))
        if neuron is None:
            return False
        if neuron.loaded_synapses is not None:  # loaded here, so what it was loaded with is known
            if include_synapses and not neuron.loaded_synapses:
                return False
            if transform is not None and neuron.transform is not transform:
                return False
        status = getattr(neuron, "correction_status", None) or {}
        same_mode = status.get("mode", "neighbor") == want
        if want == "off":
            corrected = not status.get("applied")
        elif status.get("applied"):
            corrected = same_mode
        else:
            corrected = bool(status.get("requested")) and same_mode and (
                self._corrections is None
                or self._corrections.paused_for(neuron.volume.viking_name or "", want,
                                                int(neuron.id)))
        reg = getattr(neuron, "registration_status", None) or {}
        if not live:
            registered = not reg.get("requested")
        elif reg.get("applied"):
            registered = True
        else:
            url = neuron.volume.vikingxml_url
            registered = bool(reg.get("requested")) and (
                url is None or self._registrations is None or self._registrations.paused(url))
        return corrected and registered

    # -- Viking's current section transforms -------------------------------
    def registrations(self) -> RegistrationProvider:
        with self._lock:
            if self._registrations is None:
                from ..io.cache import DiskCache

                self._registrations = RegistrationProvider(
                    cache=DiskCache(self._cache_dir) if self._cache_dir else None,
                    offline=self.offline,
                )
            return self._registrations

    def _register(self, neuron: Neuron, *, force: bool = False) -> dict:
        """Apply the volume's current section transforms; the status the cell shows."""
        url = neuron.volume.vikingxml_url
        if url is None:
            return {"requested": True,
                    "note": f"{neuron.volume.display_name}'s section transforms are not read here; "
                            "stored positions"}
        try:
            self.registrations().apply(neuron, url, force=force)
        except RegistrationError as exc:
            log.warning("c%s %s: live registration not applied: %s",
                        neuron.id, neuron.volume.display_name, exc)
            return {"requested": True, "note": f"Stored positions: {exc}"}
        summary = dict(neuron.live_registration or {})
        log.info("c%s %s: current section transforms: %d of %d annotations moved from the stored "
                 "positions, max %.2f um, sections %s",
                 neuron.id, neuron.volume.display_name, summary.get("n_moved", 0),
                 summary.get("n_annotations", 0), summary.get("max_um", 0.0),
                 summary.get("sections_moved") or "none")
        return {"requested": True, "applied": True, **summary}

    def _correct(self, neuron: Neuron, mode: str = "neighbor") -> dict:
        """Apply Viking's section correction in ``mode``; the status the cell shows."""
        volume_name = neuron.volume.viking_name
        if not volume_name:
            return {"requested": True, "mode": mode,
                    "note": f"{neuron.volume.display_name} is not a Viking volume"}
        try:
            correction = self.corrections().correct(neuron, mode)
        except CorrectionsError as exc:
            log.warning("c%s %s: section correction (%s) not applied: %s",
                        neuron.id, volume_name, mode, exc)
            return {"requested": True, "mode": mode, "note": f"Not applied: {exc}"}
        if correction is None:
            return {"requested": True, "mode": mode,
                    "note": f"No section correction is published for {volume_name}"}
        summary = dict(neuron.section_correction or {})
        note = summary.pop("service_note", "")
        log.info("c%s %s: section correction %s, %s %s: %d of %d annotations moved, max %.2f um%s",
                 neuron.id, volume_name, mode, summary.get("stos_group"), summary.get("built_utc"),
                 summary.get("n_moved", 0), summary.get("n_annotations", 0),
                 summary.get("max_um", 0.0), " (offsets from disk)" if summary.get("cached") else "")
        status = {"requested": True, "applied": True, "mode": mode, **summary}
        if note:
            status["note"] = f"The service is not answering; offsets kept from before ({note})"
        return status

    def freshness(self, neuron_id: int, source) -> FreshnessDTO:
        """Has Viking edited this cell since the loaded data was fetched?

        :meth:`freshness_many` for one cell; raises what it would report.
        """
        (answer,) = self.freshness_many([(neuron_id, source, None)])
        if isinstance(answer, Exception):
            raise answer
        return answer

    def freshness_many(self, cells) -> list[FreshnessDTO | Exception]:
        """Freshness of many loaded cells, one answer per ``(id, source, copy)``.

        Viking's ``StructureFreshnessBatch`` answers a whole volume's cells in
        one request (depth 0, the cells' own annotations; depth 1 again for
        cells loaded with their child structures), where this used to cost
        one live query per cell.  Each answer is compared with what the
        loaded copy holds (:func:`cell_freshness`): a newer edit *or* a
        different count is a change, so a deleted annotation counts too, which
        the newest date alone never showed; child structures by count.  A
        volume without the action falls back to the old per-cell query.  A
        cell with no stamp -- a JSON import, a fixture -- is never stale.
        ``copy`` names which copy of a cell (``copy_tag``); ``None`` leaves the
        request's own choice (``?copy=``, else the newest copy).  A cell that
        cannot be answered gets its exception in place.
        """
        out: list[FreshnessDTO | Exception | None] = []
        by_volume: dict[str, list[tuple[int, Neuron]]] = {}
        for index, (neuron_id, source, tag) in enumerate(cells):
            try:
                with requested_copy(tag) if tag else contextlib.nullcontext():
                    neuron = self.neuron(neuron_id, source)
            except (NeuronNotLoadedError, ValueError) as exc:
                out.append(exc)
                continue
            out.append(None)
            by_volume.setdefault(neuron.source, []).append((index, neuron))

        def ask(volume: str, entries: list[tuple[int, Neuron]]) -> None:
            client = self.client(volume)
            try:
                own = client.structure_freshness([n.id for _, n in entries], depth=0)
                family_ids = [n.id for _, n in entries if getattr(n, "children_loaded", False)]
                family = client.structure_freshness(family_ids, depth=1) if family_ids else {}
            except ODataError as exc:
                log.info("StructureFreshnessBatch failed on %s (%s); asking per cell", volume, exc)
                for index, neuron in entries:
                    try:
                        out[index] = self._freshness_by_locations(neuron)
                    except ODataError as error:
                        out[index] = error
                return
            for index, neuron in entries:
                out[index] = _compare_freshness(neuron, own.get(neuron.id), family.get(neuron.id))

        # Volumes side by side: a mixed session (12 cells over 7 volumes) took
        # 0.65 s one volume after another, two requests each.
        if len(by_volume) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(by_volume))) as pool:
                for future in [pool.submit(ask, v, e) for v, e in by_volume.items()]:
                    future.result()
        else:
            for volume, entries in by_volume.items():
                ask(volume, entries)
        return [a if a is not None else ODataError("no answer") for a in out]

    def _freshness_by_locations(self, neuron: Neuron) -> FreshnessDTO:
        """The per-cell check from before ``StructureFreshnessBatch``: the newest
        Location only, so it cannot see a deletion."""
        client = self.client(neuron.source)
        with client.fresh():
            rows = client.get_last_annotations(int(neuron.id), 1)
        live = odata_utc(rows[0].get("LastModified")) if rows else None
        loaded = odata_utc(neuron.data_stamp)
        stale = bool(live is not None and loaded is not None and _later(live, loaded))
        return FreshnessDTO(loaded=loaded, live=live, stale=stale, surface_stale=stale)

    #: Rough peak resident memory of a build, MB, for the job gate.  Calibrated
    #: loosely against sbfsem-measure: a 9k-annotation cell on the voxel
    #: pathway peaks in the hundreds of MB; a 145M-voxel curve raster hit
    #: ~2.5 GB.  Over-estimating only delays a parallel start; under-estimating
    #: risks the memory ceiling, so the numbers lean high.
    #:
    #: The outline raster is counted exactly as the build allocates it
    #: (:func:`~sbfsem.geometry.closed_curve.curve_grid_voxels`): the tiles
    #: under the outlines, section by section.  It was the whole cell's x/y/z
    #: extent over the pixel size, which on production (2026-10-07) put a
    #: Hybrid of RC2 c5624 -- 8 outlines, measured peak 933 MB -- at 218,190 MB;
    #: a build over the whole budget runs alone, so another person's cell waited
    #: 30 s behind it.  A Hybrid counts only its disc annotations toward the
    #: disc half.
    #:
    #: ``field_options`` (the mesh route's options) lets a Voxel remesh,
    #: Convolution or Ultraliser build be priced by the sheet it is planned to
    #: draw as well (``FieldPlanner.raw_triangles``), whichever is more.
    def estimate_peak_mb(self, neuron_id: int, source, *, method: str = "cylinder",
                         pixel_size_um: float = 0.05, pathway: str = "original",
                         meshlab: str = "none", meshlab_depth: int = 9,
                         curve_averaging: bool = False,
                         curve_options: Mapping[str, Any] | None = None,
                         voxel2_faces: int | None = None,
                         voxel2_um: float | None = None,
                         field_options: Mapping[str, Any] | None = None,
                         neuron: Neuron | None = None) -> float:
        if neuron is None:
            neuron = self.neuron(neuron_id, source)
        cells = neuron.get_cell_nodes()
        n = max(1, len(cells))
        method = str(method).lower()
        estimate = 0.0
        voxels = 0.0
        curves = method in ("closedcurve", "curve", "outline", "volume")
        if curves or method in ("hybrid", "mixed"):
            try:
                # The bridges decide which skipped sections are filled, so the
                # raster is counted with them; the curving of disc links and the
                # ties do not change what it holds (ties are a few tiles).
                o = curve_options or {}
                annotations, how = _curve_outlines(
                    neuron, discs=curves,
                    bridge_um=float(o.get("curve_bridge_um", BRIDGE_UM)),
                    link_bridge_um=float(o.get("curve_link_bridge_um", LINK_BRIDGE_UM)))
                voxels = float(curve_grid_voxels(
                    annotations,
                    volume_scale=neuron.volume_scale,
                    pixel_size_um=max(float(pixel_size_um), 1e-3),
                    **how,
                ))
            except (KeyError, ValueError, TypeError):
                voxels = 0.0
            # Over the raster's limit the build refuses before allocating
            # anything (and the page refits the pixel), so it costs nothing.
            if voxels > CURVE_MAX_VOXELS:
                voxels = 0.0
            if voxels or curves:
                # The tiles, then the mesh: a surface over the raster's
                # limit is refused before it is contoured.  Averaging the
                # surface keeps a byte of label for each voxel and the mesh's
                # adjacency a second time (``CURVE_AVERAGING_FACTOR``).
                base = 150.0 + min(voxels * CURVE_BYTES_PER_VOXEL / 1e6, CURVE_MAX_BUILD_MB)
                estimate += base
                if curve_averaging:
                    estimate += min(base * (CURVE_AVERAGING_FACTOR - 1.0), CURVE_AVERAGING_MAX_MB)
                # Joining bodies keeps every block's contour for the second pass
                # (``CURVE_TIE_BYTES_PER_VOXEL``); only Closed curve joins them.
                if curves and float((curve_options or {}).get("curve_tie_um") or 0.0) > 0:
                    estimate += min(voxels * CURVE_TIE_BYTES_PER_VOXEL / 1e6, CURVE_TIE_MAX_MB)
            if "TypeCode" in cells.columns:
                n = int((cells["TypeCode"] == 1).sum())
        # What the outline half holds, the raster and the surface made of it: a
        # Hybrid in one field holds it through its field's sheet.
        outline_mb = estimate
        if not curves and n > 0:
            pw = str(pathway).lower()
            per = {"voxel": 0.08, "voxel2": 0.3, "convolution": 0.3,
                   "ultraliser": 0.3}.get(pw, 0.04)
            estimate += 120.0 + n * per
            if pw in FIELD_PATHWAYS:
                # The whole-cell sheet exists at full resolution before the
                # face cap decimates it (measured peaks: c106 440 MB with 321
                # annotations, c1178 1,077 MB with 2,304).
                estimate += 300.0
                # Past 3 million raw triangles the sheet is what weighs, and
                # the request, not the cell, says how many it may hold: the
                # Face cap (0 keeps every triangle) sets the budget.
                faces = self._mesh_options()["voxel2_faces"] if voxel2_faces is None else voxel2_faces
                over = raw_budget(faces) - RAW_FACES_KEYED_ABOVE
                if over > 0:
                    # Under a cap such a sheet is decimated a chunk at a time.
                    estimate += over * FIELD_BYTES_PER_RAW_FACE / 1e6
                    if decimates_in_chunks(faces):
                        estimate += FIELD_CHUNKED_DECIMATION_MB
                # A Fine voxel typed under the automatic floor (2026-10-16) can fill
                # the whole raw budget on a small cell, which the figures above,
                # sized from the cell's annotations, do not see: Temporal c106 typed
                # at 0.02 um under the default cap marched 980,542 raw triangles and
                # peaked at 931 MB, priced 511 MB here before.  Priced at the sheet
                # its budget allows instead, whichever is more.
                if 0.0 < float(voxel2_um or 0.0) < MIN_VOXEL_UM:
                    estimate = max(estimate, field_sheet_mb(raw_budget(faces), int(faces)))
                # Under 3 million raw triangles the figures above price the cell's
                # annotations, not the sheet it draws: RPC1 c430 at a Face cap of
                # 1,460,000 and Inferior c1178 at 1,100,000 drew 2.4 and 2.3
                # million and peaked at 2,582 and 2,535 MB, priced 2,546 and 1,451
                # (2026-10-20).  Priced at the sheet the build is sized to draw as
                # well, whichever is more (3,869 and 2,978).  The sizing counts
                # 1.2 to 2 times the triangles a build then draws, and the law,
                # given a drawn sheet of 0.65 to 2.4 million, comes out up to 14 %
                # under its peak, so the sheet planned is priced, not a guess at
                # the one drawn: each build measured where it sets the price
                # peaked 15 to 33 % under it.
                if field_options is not None and METHOD_NAMES.get(method) in ("cylinder", "curvemesh"):
                    estimate = max(estimate, self._planned_sheet_mb(
                        neuron, field_options, pathway=pw, voxel2_faces=int(faces),
                        voxel2_um=voxel2_um))
                # A Hybrid whose outlines and discs are one field (2.7.0) draws its
                # outlines into the discs' sheet, at the discs' voxel: priced by its
                # disc annotations it was under its peak by up to 3.7 times.  At the
                # default Face cap, RPC1 c2628 (26 discs and 3,978 outlines) drew 2.66
                # million raw triangles and peaked at 2,921 MB, priced 783; Inferior
                # c16137 1.35 million, 1,491 MB, priced 1,140; at a cap of 1,000,000
                # RC1 c410 1.16 million, 1,582 MB, priced 1,038 (2026-10-20).  Its
                # discs' price is now never less than the sheet it is sized to draw,
                # planned as the build plans it, and its outline raster is priced on
                # top as before: each build measured peaked 11 to 32 % under that.
                if (field_options is not None and METHOD_NAMES.get(method) == "hybrid"
                        and pw in HYBRID_ONE_FIELD and voxels > 0):
                    estimate = max(estimate, outline_mb + self._planned_sheet_mb(
                        neuron, field_options, pathway=pw, voxel2_faces=int(faces),
                        voxel2_um=voxel2_um, hybrid_pixel_um=max(float(pixel_size_um), 1e-3)))
        # MeshLab runs as a child of the build worker, which keeps its own
        # heap while it waits: 337 to 563 MB more at 400,000 faces.
        if str(meshlab) != "none":
            estimate += self.MESHLAB_POISSON_MB if (
                str(meshlab) == "poisson" and int(meshlab_depth) > 9) else self.MESHLAB_MB
        return float(round(estimate))

    def _planned_sheet_mb(self, neuron: Neuron, field_options: Mapping[str, Any], *,
                          pathway: str, voxel2_faces: int, voxel2_um: float | None,
                          hybrid_pixel_um: float | None = None) -> float:
        """The peak of the sheet a field build of ``neuron`` is sized to draw
        (:func:`field_sheet_mb` of ``FieldPlanner.raw_triangles``); 0 when there is
        nothing to contour or the plan fails, which leaves the gate's other price.
        ``hybrid_pixel_um``: a Hybrid in one field with its outlines rasterized at
        that pixel, whose outline half's triangles join the Face cap."""
        options = {**self._mesh_options(**field_options), "pathway": pathway,
                   "voxel2_faces": voxel2_faces}
        if voxel2_um is not None:
            options["voxel2_um"] = float(voxel2_um)
        if hybrid_pixel_um is not None:
            options.update(method="hybrid", pixel_size_um=float(hybrid_pixel_um))
        try:
            raw = self._field_planner.raw_triangles(neuron, options)
        except Exception:  # noqa: BLE001 - a plan only raises the price; never block a build
            log.warning("c%s: no sheet plan for the build gate", getattr(neuron, "id", "?"),
                        exc_info=True)
            return 0.0
        if not raw:
            return 0.0
        cap = voxel2_faces
        if hybrid_pixel_um is not None and voxel2_faces > 0:
            cap += self._field_planner.outline_triangles(neuron, options)
        # A Hybrid's outline triangles join its cap, not its raw budget: whether the
        # sheet is decimated in chunks is the discs' Face cap's to say.
        return field_sheet_mb(raw, cap, float(field_options.get("reduce_fraction", 1.0) or 1.0),
                              chunked=decimates_in_chunks(voxel2_faces))

    def _field_pieces(self, neuron: Neuron, options: dict) -> list:
        """The pieces a field pathway's build contours ``neuron`` from, under
        ``options`` (``_mesh_options``'s): what :meth:`_cylinder_mesh` hands
        :func:`~sbfsem.geometry.cylinder.cylinder_render`, Smooth discs included."""
        avg = float(options.get("curve_avg_discs", 0.0) or 0.0)
        if avg > 0:
            neuron = _averaged_discs(neuron, avg)
        return field_pieces(
            neuron, pathway=str(options["pathway"]),
            voxel_detail=options["voxel_detail"], voxel_smooth_um=options["voxel_smooth_um"],
            target_arc_um=options["ring_arc_um"], wrap_spacing_um=options["wrap_spacing_um"],
            wrap_blend=options["wrap_blend"], wrap_reject_sigma=options["wrap_reject_sigma"],
            wrap_passes=int(options["wrap_passes"]), ultra_soma=str(options["ultra_soma"]))

    def _disc_half_peak_mb(self, neuron: Neuron, options: dict) -> float:
        """The memory a Hybrid's disc half builds in (``estimate_peak_mb`` of the discs alone)."""
        discs = _disc_subset(neuron)
        if discs is None:
            return 0.0
        return self.estimate_peak_mb(
            neuron.id, neuron.source, method="cylinder",
            pathway=str(options.get("pathway", "original")),
            meshlab=str(options.get("meshlab", "none")),
            meshlab_depth=int(options.get("meshlab_depth", 9)),
            voxel2_faces=options.get("voxel2_faces"), voxel2_um=options.get("voxel2_um"),
            neuron=discs)

    def render_estimate(self, neuron_id: int, source, *, memory_limit_mb: float,
                        neuron: Neuron | None = None, **options) -> dict[str, Any]:
        """What a render with ``options`` (the mesh route's) would cost on this
        copy of the cell, before anything is built (:mod:`sbfsem.service.estimate`).

        For Closed curve and Hybrid: the raster's voxels and exposed faces, the
        triangles, the build's peak memory and what serving it adds (held to
        ``memory_limit_mb``, the memory this server gives builds), the seconds,
        the JSON the page receives and the tab's heap, which limits the options
        are over, and the finest Voxel size that fits them all.  For the other
        methods (``applies`` false) only the build gate's memory estimate.
        Answers are kept per copy of the cell and options; a first answer on a
        cell never built with outlines rasterizes it once, coarsely.
        """
        if neuron is None:
            neuron = self.neuron(neuron_id, source)
        opts = dict(options)
        method = canonical_method(opts.pop("method", "cylinder"))
        answer = self._estimator.estimate(neuron, {**opts, "method": method},
                                          memory_limit_mb=memory_limit_mb)
        if not answer.get("applies"):
            answer["peak_mb"] = self.estimate_peak_mb(
                neuron.id, neuron.source, method=method,
                pixel_size_um=float(opts.get("pixel_size_um", 0.05)),
                pathway=str(opts.get("pathway", "original")),
                meshlab=str(opts.get("meshlab", "none")),
                meshlab_depth=int(opts.get("meshlab_depth", 9)),
                voxel2_faces=opts.get("voxel2_faces"), voxel2_um=opts.get("voxel2_um"),
                field_options=opts, neuron=neuron)
            field = {**self._mesh_options(**opts), "method": method,
                     "reduce_fraction": float(opts.get("reduce_fraction", 1.0))}
            if FieldPlanner.applies(method, field):
                answer["field"] = self._field_planner.estimate(neuron, field)
        answer["cell"] = int(neuron.id)
        answer["source"] = str(neuron.source)
        answer["copy"] = getattr(neuron, "copy_tag", None)
        return answer

    def add_neuron(self, neuron: Neuron) -> NeuronDetailDTO:
        """Register an already-constructed neuron, e.g. loaded from JSON."""
        key = _Key(neuron.source, int(neuron.id))
        if getattr(neuron, "section_correction", None) and not getattr(
                neuron, "correction_status", None):
            # A file from before the modes was corrected with the residual field.
            neuron.correction_status = {
                "requested": True, "applied": True, "mode": "neighbor",
                **(neuron.section_correction or {}), "note": "As saved in the file"}
        if getattr(neuron, "live_registration", None) and not getattr(
                neuron, "registration_status", None):
            neuron.registration_status = {
                "requested": True, "applied": True, **(neuron.live_registration or {}),
                "note": "As saved in the file"}
        tag = self._tag_of(neuron)
        with self._lock:
            self._store(key, neuron, tag)
        with requested_copy(tag):
            return self.neuron_detail(neuron.id, neuron.source)

    def unload_neuron(self, neuron_id: int, source) -> bool:
        """Forget a loaded cell: the one copy the call names (the page names
        its own), or every copy when it names none."""
        key = _Key(Volumes.from_str(source).source, int(neuron_id))
        tag = _REQUEST_COPY.get()
        with self._lock:
            copies = self._copies.get(key, {})
            doomed = list(copies) if tag is None else [tag] if tag in copies else []
            for name in doomed:
                self._drop_meshes(key, serial=getattr(copies.pop(name), "load_serial", None))
            if not copies:
                self._copies.pop(key, None)
                self._neurons.pop(key, None)
            elif getattr(self._neurons.get(key), "copy_tag", None) not in copies:
                self._neurons[key] = max(copies.values(), key=lambda n: n.load_serial or 0)
            return bool(doomed)

    def loaded_neurons(self) -> list[NeuronSummaryDTO]:
        with self._lock:
            return [self._summary(n) for n in self._neurons.values()]

    def neuron(self, neuron_id: int, source) -> Neuron:
        """The backend object.  Frontends should prefer the DTO methods."""
        key = _Key(Volumes.from_str(source).source, int(neuron_id))
        with self._lock:
            neuron = self._loaded(key)
            other_copies = key in self._copies
        if neuron is None:
            tag = _REQUEST_COPY.get()
            detail = f" with the setting {tag}" if tag and other_copies else ""
            raise NeuronNotLoadedError(f"c{neuron_id} is not loaded{detail} for {key.source}")
        return neuron

    def _drop_meshes(self, key: _Key, serial: int | None = None) -> None:
        """Forget the meshes and deviations built from a cell: every copy's,
        or only the copy numbered ``serial``."""
        def doomed(cached: tuple) -> bool:
            return cached[0] == key and (serial is None or cached[-1] == serial)

        def doomed_difference(cached: tuple) -> bool:
            # A difference belongs to two copies: mesh A's (last, as everywhere)
            # and mesh B's (last of the middle tuple; the same copy for the
            # usual difference of two renderings of one copy).  Reloading either
            # one makes it a measurement of a surface that is gone.
            return cached[0] == key and (
                serial is None or serial == cached[-1] or serial == cached[1][-1])

        for cached in [k for k in self._meshes if doomed(k)]:
            self._meshes.pop(cached, None)
        for store in (self._deviations, self._deviation_maps):
            for cached in [k for k in store if doomed(k)]:
                store.pop(cached, None)
        for cached in [k for k in self._differences if doomed_difference(k)]:
            self._differences.pop(cached, None)

    def _cache_mesh(self, key: tuple, mesh: Mesh) -> None:
        """Store a built mesh, evicting the least recently used one."""
        with self._lock:
            self._meshes[key] = mesh
            self._meshes.move_to_end(key)
            while len(self._meshes) > self._mesh_cache_size:
                evicted, _ = self._meshes.popitem(last=False)
                log.debug("Evicted cached mesh %s", evicted[1:])

    def clear_renders(self, *, disk: bool = False) -> dict:
        """Forget every built surface: those held in memory, and with ``disk`` the disk cache too.

        The Settings tab's "clear cached renders" for this server.  Cells stay
        loaded; the deviations, error maps and differences go with the surfaces
        they measured.  The next request for a surface builds it again, from the
        disk cache when only memory was cleared.
        """
        with self._lock:
            held = len(self._meshes)
            self._meshes.clear()
            for store in (self._deviations, self._deviation_maps, self._differences,
                          self._outline_replies):
                store.clear()
        self._field_planner.forget()
        report = {"memory_meshes": held, "disk_files": 0, "disk_bytes": 0}
        if disk and self._disk_meshes is not None:
            gone = self._disk_meshes.clear()
            report.update(disk_files=gone["files"], disk_bytes=gone["bytes"])
        log.info("cleared cached renders on request: %s", report)
        return report

    def disk_cache_report(self) -> dict | None:
        """The disk cache of built meshes (files, bytes, budget), or ``None`` without one."""
        return self._disk_meshes.stats() if self._disk_meshes is not None else None

    def memory_report(self) -> dict:
        """Rough resident cost of what the service is holding.

        Useful for sizing an instance and for a health check that notices the
        process growing.
        """
        held = [n for copies in self._copies.values() for n in copies.values()]
        nodes = sum(len(n.nodes) for n in held)
        mesh_bytes = sum(
            m.vertices.nbytes + m.faces.nbytes
            + (m.vertex_data.nbytes if m.vertex_data is not None else 0)
            for m in self._meshes.values()
        )
        report = {
            "neurons_loaded": len(self._neurons),
            "copies_held": len(held),
            "annotations_held": nodes,
            "meshes_cached": len(self._meshes),
            "mesh_cache_limit": self._mesh_cache_size,
            "mesh_bytes": mesh_bytes,
            "outline_reply_bytes": sum(len(body) for body in self._outline_replies.values()),
            "boundaries_fitted": load_ipl_boundaries.cache_info().currsize,
        }
        try:
            import psutil

            report["process_rss_bytes"] = psutil.Process().memory_info().rss
        except ImportError:
            pass
        return report

    # -- summaries ---------------------------------------------------------
    def neuron_detail(self, neuron_id: int, source) -> NeuronDetailDTO:
        neuron = self.neuron(neuron_id, source)
        return NeuronDetailDTO(
            summary=self._summary(neuron),
            synapse_counts=self._synapse_counts(neuron),
            volume=self.volume_info(neuron.source),
        )

    def _summary(self, neuron: Neuron) -> NeuronSummaryDTO:
        try:
            soma_radius = float(neuron.get_soma_size())
            soma_xyz = [float(v) for v in neuron.get_soma_xyz()]
        except (ValueError, AttributeError):
            soma_radius, soma_xyz = None, None
        box = bounding_box(neuron) if len(neuron.nodes) else None
        geometries = getattr(neuron, "geometries", None)
        cell_nodes = neuron.get_cell_nodes()
        if "TypeCode" in cell_nodes.columns:
            n_discs = int((cell_nodes["TypeCode"] == 1).sum())
            n_curves = int((cell_nodes["TypeCode"] == 6).sum())
        else:
            n_discs, n_curves = int(len(cell_nodes)), 0
        return NeuronSummaryDTO(
            id=int(neuron.id),
            source=neuron.source,
            label=neuron.label,
            type_id=neuron.type_id,
            n_nodes=int(len(cell_nodes)),
            n_edges=int(len(neuron.edges)),
            n_synapses=int(len(getattr(neuron, "synapses", []))),
            has_geometry=geometries is not None and len(geometries) > 0,
            n_disc_annotations=n_discs,
            n_curve_annotations=n_curves,
            soma_radius_um=soma_radius,
            soma_xyz_um=soma_xyz,
            bounding_box_um=[float(v) for v in box] if box is not None else None,
            # Viking's OData labels UTC wall-clock digits Pacific (7 h late as
            # labeled); the freshness check reads them as UTC, and so must the
            # stamp a tab shows next to it, or an Update reports a load 7 h
            # before the one a plain load reports.
            last_modified=odata_utc(neuron.last_modified),
            data_stamp=odata_utc(getattr(neuron, "data_stamp", None)),
            notes=neuron.notes,
            section_correction=_correction_dto(getattr(neuron, "correction_status", None)),
            live_registration=_registration_dto(getattr(neuron, "registration_status", None)),
            manual_moves=_manual_dto(getattr(neuron, "manual_moves", None)),
            copy_tag=getattr(neuron, "copy_tag", None),
            positions=positions_token(neuron),
        )

    @staticmethod
    def _synapse_counts(neuron: Neuron) -> list[SynapseTypeCountDTO]:
        summary = neuron.synapse_summary()
        synapses = neuron.synapses
        status = synapses.get("TagStatus") if not synapses.empty else None
        out = []
        for row in summary.itertuples():
            kind = StructureTypes.from_str(row.LocalName)
            inferred = assumed = 0
            if status is not None:
                mine = synapses["LocalName"].map(lambda t: t.value) == kind.value
                mine &= synapses["VikingName"] == row.VikingName
                inferred = int((mine & (status == "inferred")).sum())
                assumed = int((mine & (status == "assumed")).sum())
            out.append(
                SynapseTypeCountDTO(
                    local_name=kind.value,
                    viking_name=row.VikingName,
                    count=int(row.Count),
                    color=list(kind.color),
                    inferred=inferred,
                    assumed=assumed,
                )
            )
        return out

    # -- tables ------------------------------------------------------------
    def last_modified_annotation(self, neuron_id: int, source) -> dict | None:
        """The most recently edited annotation of a structure.

        Ports what RenderApp's "Show Last Modified" displayed.  The OData row
        carries the timestamp and editor; the position comes from the loaded
        neuron's own nodes so it lands in the same transformed micron space
        as the render (a synapse annotation of a child structure has no such
        node and returns no position).
        """
        neuron = self.neuron(neuron_id, source)
        volume = Volumes.from_str(source)
        client = self.client(volume)
        # Asked of Viking, not of the disk cache: the cache has no expiry, so an
        # edit made since the first time anyone asked was never reported (the
        # question is "when was it last edited").  An offline server has only
        # its cache to answer from.
        with contextlib.nullcontext() if getattr(client, "offline", False) else client.fresh():
            row = client.get_last_modified_annotation(int(neuron_id))
        if not row:
            return None
        # Viking's OData labels UTC wall-clock time Pacific (and now Z): read as
        # the UTC instant it names, in a form the browser shows in its own time
        # zone.  Left as labeled, the time read 7 h late.
        stamp = odata_utc(row.get("LastModified"))
        out = {
            "id": int(row.get("ID", 0)),
            "last_modified": stamp.isoformat().replace("+00:00", "Z") if stamp else "",
            "username": (row.get("Username") or "").strip() or None,
            "xyz_um": None,
        }
        nodes = neuron.nodes
        hit = nodes[nodes["ID"] == out["id"]] if "ID" in nodes else None
        if hit is not None and len(hit):
            r = hit.iloc[0]
            out["xyz_um"] = [float(r.Xum), float(r.Yum), float(r.Zum)]
        return out

    def nodes(self, neuron_id: int, source, *, cell_only: bool = True) -> list[NodeDTO]:
        neuron = self.neuron(neuron_id, source)
        frame = neuron.get_cell_nodes() if cell_only else neuron.nodes
        # The two moves, read from the columns once rather than once per row.
        # (A cell loaded with transform "None" keeps raw pixels in Xum/Yum: no move applies.)
        from sbfsem.analysis.shifts import moves_apply

        if moves_apply(neuron):
            corrected, registered = _shifts_um(frame, "Corr"), _shifts_um(frame, "Live")
        else:
            corrected = registered = [None] * len(frame)
        manual, manual_dz = _manual_um(frame, neuron.volume_scale)
        return [
            NodeDTO(
                id=int(r.ID),
                parent_id=int(r.ParentID),
                xyz_um=[float(r.Xum), float(r.Yum), float(r.Zum)],
                radius_um=float(r.Rum),
                z_section=int(r.Z),
                off_edge=bool(r.OffEdge),
                terminal=bool(r.Terminal),
                unique=bool(getattr(r, "Unique", False)),
                type_code=int(r.TypeCode),
                shift_um=shift,
                live_shift_um=live,
                manual_shift_um=hand,
                manual_dz=dz,
            )
            for r, shift, live, hand, dz in zip(frame.itertuples(), corrected, registered, manual,
                                                manual_dz, strict=True)
        ]

    def locate(self, location_id: int, source) -> LocationDTO:
        """Where one Viking annotation is.

        A loaded cell of ``source`` that holds it answers at once, in the
        cell's own micron frame; otherwise Viking is asked for the single
        Location (one OData request) and its pixels are scaled with the
        volume's scale, the same conversion the app's readout inverts.
        Raises ``ValueError`` when neither knows the id.
        """
        volume = Volumes.from_str(source)
        with self._lock:
            # Each cell's copy for this request: the one the page named
            # (``?copy=``), else the one loaded last.  A cell held in other
            # settings only does not answer for these.
            neurons = [n for k in self._neurons if k.source == volume.source
                       for n in (self._loaded(k),) if n is not None]
        for neuron in neurons:
            frame = getattr(neuron, "nodes", None)
            if frame is None or frame.empty or "ID" not in frame.columns:
                continue
            rows = frame[frame["ID"] == int(location_id)]
            if not len(rows):
                continue
            r = rows.iloc[0]
            um = [float(r["Xum"]), float(r["Yum"]), float(r["Zum"])]
            if all(c in frame.columns for c in ("X", "Y", "Z")):
                viking = [int(round(float(r[c]))) for c in ("X", "Y", "Z")]
            else:
                viking = [int(round(v / (s / 1000.0))) for v, s in zip(um, volume.cached_scale, strict=True)]
            return LocationDTO(
                id=int(location_id),
                parent_id=int(r["ParentID"]),
                structure_id=int(neuron.id),
                xyz_um=um,
                viking=viking,
                radius_um=float(r["Rum"]),
                loaded=True,
            )
        client = self.client(volume)
        try:
            data = client.get(f"{client.base_url}Locations({int(location_id)})")
        except (ODataError, OSError, RuntimeError, KeyError, ValueError) as exc:
            raise ValueError(
                f"Location {location_id} is not known to {volume.display_name}: {exc}"
            ) from exc
        if not isinstance(data, dict) or "X" not in data:
            raise ValueError(f"Location {location_id} is not in {volume.display_name}")
        xyz = np.array([[float(data["X"]), float(data["Y"]), float(data["Z"])]])
        um = viking2micron(xyz, volume.source, volume.cached_scale)[0]
        radius = float(data.get("Radius", 0.0) or 0.0) * volume.cached_scale[0] / 1000.0
        parent = int(data.get("ParentID", 0) or 0)
        return LocationDTO(
            id=int(location_id), parent_id=parent, structure_id=parent,
            xyz_um=[float(v) for v in um],
            viking=[int(round(float(v))) for v in xyz[0]],
            radius_um=float(radius), loaded=False,
        )

    def edges(self, neuron_id: int, source) -> list[EdgeDTO]:
        neuron = self.neuron(neuron_id, source)
        return [
            EdgeDTO(structure_id=int(r.ID), a=int(r.A), b=int(r.B))
            for r in neuron.edges.itertuples()
        ]

    # -- geometry ----------------------------------------------------------
    def skeleton(self, neuron_id: int, source) -> SkeletonDTO:
        """Per-segment polylines, for a lightweight 3D view."""
        neuron = self.neuron(neuron_id, source)
        result = dendrite_segmentation(neuron)
        segments = [
            SegmentDTO(
                location_ids=np.asarray(row.ID, dtype=np.int64).tolist(),
                points_um=np.asarray(row.XYZum, dtype=float).tolist(),
                radii_um=np.asarray(row.Rum, dtype=float).tolist(),
            )
            for row in result.table.itertuples()
        ]
        try:
            soma_xyz = [float(v) for v in neuron.get_soma_xyz()]
            soma_radius = float(neuron.get_soma_size())
        except (ValueError, AttributeError):
            soma_xyz, soma_radius = None, None
        return SkeletonDTO(
            neuron_id=int(neuron.id),
            segments=segments,
            soma_xyz_um=soma_xyz,
            soma_radius_um=soma_radius,
        )

    #: Averaged annotation wireframes kept per cell, by their build settings.
    ANNOTATION_WIRE_KEEP = 4

    def annotation_wire(
        self, neuron_id: int, source, *,
        gain: float = wire.DEFAULT_GAIN,
        size_tolerance: float = wire.DEFAULT_SIZE_TOLERANCE,
        break_ratio: float = wire.DEFAULT_BREAK_RATIO,
        fit: str = "quadratic",
        lines: int = wire.DEFAULT_LINES,
        ring_spacing_um: float = 0.0,
    ) -> AnnotationWireDTO:
        """The cell's annotations as an averaged wireframe.

        See :mod:`sbfsem.geometry.annotation_wire`.  Built from the
        annotations alone, so it never waits on a surface and is not in the
        mesh cache.  The last few builds are kept on the neuron: ``lines``
        and ``ring_spacing_um`` only choose what is drawn from one build, and
        a reloaded cell is a new neuron with nothing kept.
        """
        neuron = self.neuron(neuron_id, source)
        built = self._annotation_wire_built(
            neuron, gain=gain, size_tolerance=size_tolerance,
            break_ratio=break_ratio, fit=fit,
        )

        def flat(values) -> list[float]:
            return np.round(np.asarray(values, dtype=float), 4).ravel().tolist()

        moved = built.moved_um()
        return AnnotationWireDTO(
            neuron_id=int(neuron.id),
            rings=[[round(float(z), 4), *flat(xy)]
                   for z, xy in built.ring_polylines(ring_spacing_um)],
            lines=[flat(line) for line in built.line_polylines(lines)],
            ties=flat(built.tie_segments(lines)),
            n_annotations=len(built.rings),
            n_chains=len(built.chains),
            n_ties=len(built.ties),
            moved_um=_stats_dto(moved) if len(moved) else None,
        )

    def annotation_wire_segments(
        self, neuron_id: int, source, *,
        gain: float = wire.DEFAULT_GAIN,
        size_tolerance: float = wire.DEFAULT_SIZE_TOLERANCE,
        break_ratio: float = wire.DEFAULT_BREAK_RATIO,
        fit: str = "quadratic",
        lines: int = wire.DEFAULT_LINES,
        ring_spacing_um: float = 0.0,
    ) -> np.ndarray:
        """The averaged annotation wireframe as ``(S, 2, 3)`` segments, microns.

        What :meth:`annotation_wire` draws -- rings, lines along the chains,
        ties -- from the same kept build, for export.
        """
        neuron = self.neuron(neuron_id, source)
        built = self._annotation_wire_built(
            neuron, gain=gain, size_tolerance=size_tolerance,
            break_ratio=break_ratio, fit=fit,
        )
        return built.segments(lines, ring_spacing_um)

    def _annotation_wire_built(
        self, neuron, *, gain: float, size_tolerance: float,
        break_ratio: float, fit: str,
    ) -> wire.AnnotationWire:
        """The cell's wireframe build for these settings, kept on the neuron.

        Requests run in threads, and the kept builds are one ordered dict: the
        look-up and the bookkeeping happen under the service's lock (two
        requests moving and evicting entries at once could pop an entry the
        other was about to move), and one build at a time is made for a cell
        and its settings, so a second request waits for it instead of
        repeating it.
        """
        key = (float(gain), float(size_tolerance), float(break_ratio), str(fit))

        def kept_builds() -> OrderedDict:
            kept = neuron.analysis.setdefault("annotation_wire", OrderedDict())
            assert isinstance(kept, OrderedDict)
            return kept

        with self._single_flight(("annotation-wire", id(neuron), *key)):
            with self._lock:
                kept = kept_builds()
                built = kept.get(key)
                if built is not None:
                    kept.move_to_end(key)
                    return built
            built = wire.annotation_wire_from_neuron(
                neuron, gain=gain, size_tolerance=size_tolerance,
                break_ratio=break_ratio, fit=fit,
            )
            with self._lock:
                kept = kept_builds()
                kept[key] = built
                kept.move_to_end(key)
                while len(kept) > self.ANNOTATION_WIRE_KEEP:
                    kept.popitem(last=False)
        return built

    def render_mesh(
        self,
        neuron_id: int,
        source,
        *,
        method: str = "cylinder",
        smooth_iterations: int = 1,
        reduce_fraction: float = 1.0,
        pixel_size_um: float = 0.05,
        clip_z: float | None = None,
        clip_ipl: float | None = None,
        keep_above: bool = True,
        color_by_strata: bool = False,
        pathway: str = "original",
        post_order: str = "smooth_first",
        voxel_detail: float = 1.0,
        voxel_smooth_um: float = 0.5,
        tie_um: float = 20.0,
        ring_arc_um: float = 0.35,
        wrap_spacing_um: float = 0.4,
        wrap_blend: float = 2.0,
        wrap_reject_sigma: float = 2.0,
        wrap_passes: int = 2,
        voxel2_um: float = 0.0,
        voxel2_faces: int = 400_000,
        voxel2_blend_um: float = 0.0,
        exact_tube_deg: float = 0.0,
        conv_support: float = 2.0,
        ultra_soma: str = "discs",
        ultra_smooth: int = 15,
        ultra_lambda: float = 0.5,
        ultra_inflate: float = 0.52,
        meshlab: str = "none",
        meshlab_edge_um: float = 0.25,
        meshlab_fraction: float = 0.25,
        meshlab_depth: int = 9,
        curve_avg_discs: float = 0.0,
        curve_avg_paths: float = 0.0,
        curve_links_um: float = 0.0,
        curve_bridge_um: float = BRIDGE_UM,
        curve_link_bridge_um: float = LINK_BRIDGE_UM,
        curve_tie_um: float = 0.0,
        surface_smooth_um: float = 0.0,
        neuron: Neuron | None = None,
    ) -> MeshDTO:
        """Build (and cache) a renderable mesh for a neuron.

        ``neuron`` is the copy of the cell to build from, when the caller took
        it earlier (a mesh job, keyed by that copy); otherwise the one loaded now.
        """
        if neuron is None:
            neuron = self.neuron(neuron_id, source)
        mesh = self._cached_mesh(
            neuron, method, smooth_iterations, reduce_fraction, pixel_size_um,
            {"pathway": pathway, "post_order": post_order,
             "voxel_detail": voxel_detail, "voxel_smooth_um": voxel_smooth_um,
             "tie_um": tie_um, "ring_arc_um": ring_arc_um, "wrap_spacing_um": wrap_spacing_um,
             "wrap_blend": wrap_blend, "wrap_reject_sigma": wrap_reject_sigma,
             "wrap_passes": wrap_passes, "voxel2_um": voxel2_um,
             "voxel2_faces": voxel2_faces, "voxel2_blend_um": voxel2_blend_um,
             "exact_tube_deg": exact_tube_deg, "conv_support": conv_support, "ultra_soma": ultra_soma,
             "ultra_smooth": ultra_smooth, "ultra_lambda": ultra_lambda,
             "ultra_inflate": ultra_inflate, "meshlab": meshlab,
             "meshlab_edge_um": meshlab_edge_um, "meshlab_fraction": meshlab_fraction,
             "meshlab_depth": meshlab_depth, "curve_avg_discs": curve_avg_discs,
             "curve_avg_paths": curve_avg_paths, "curve_links_um": curve_links_um,
             "curve_bridge_um": curve_bridge_um, "curve_link_bridge_um": curve_link_bridge_um,
             "curve_tie_um": curve_tie_um, "surface_smooth_um": surface_smooth_um},
        ).copy()
        if clip_z is not None:
            mesh = clip_mesh(mesh, clip_z, keep_above=keep_above)
        if clip_ipl is not None:
            mesh = clip_mesh_by_stratification(
                mesh, self.boundaries(neuron.source), clip_ipl, keep_above=keep_above
            )
        if clip_z is not None or clip_ipl is not None:
            # A cut surface is not the cached render its id names.
            mesh.meta.pop("mesh_id", None)
        if color_by_strata and not mesh.is_empty:
            mesh.vertex_data = self.boundaries(neuron.source).strata_cdata(mesh.vertices)
        return self._mesh_dto(f"c{neuron.id}", mesh)

    #: Post-generation options with their defaults. The app overrides these;
    #: the defaults keep a bare call MATLAB-faithful.
    MESH_OPTION_DEFAULTS = {
        "pathway": "original",
        "post_order": "smooth_first",
        "voxel_detail": 1.0,
        "voxel_smooth_um": 0.5,
        "tie_um": 20.0,
        "ring_arc_um": 0.35,
        "wrap_spacing_um": 0.4,
        "wrap_blend": 2.0,
        "wrap_reject_sigma": 2.0,
        "wrap_passes": 2,
        # Voxel remesh 2: fine voxel (0 = sized from the thin annotations),
        # triangle cap after smoothing, junction fillet radius.
        "voxel2_um": 0.0,
        "voxel2_faces": 400_000,
        "voxel2_blend_um": 0.0,
        # Voxel remesh and Ultraliser: a thin stretch's pairs of discs within this
        # many degrees of vertical drawn as the hull of the two discs, exact at
        # both sections, rather than a round cone (unify.DiscHull); 0 is off.
        "exact_tube_deg": 0.0,
        # Convolution surface: kernel radius per neurite radius (the paper's 2).
        "conv_support": 2.0,
        # Ultraliser: soma proxy, smoothing iterations, smoothing value, inflate.
        "ultra_soma": "discs",
        "ultra_smooth": 15,
        "ultra_lambda": 0.5,
        "ultra_inflate": 0.52,
        # MeshLab remesh after any technique (the optional [meshlab] extra):
        # none | isotropic (edge length) | qem (share of the faces kept) |
        # poisson (octree depth).  See sbfsem.geometry.meshlab.
        "meshlab": "none",
        "meshlab_edge_um": 0.25,
        "meshlab_fraction": 0.25,
        "meshlab_depth": 9,
        # Closed curve and Hybrid's outline half: averaging out the staircase
        # of the section-by-section raster.  Each disc averaged with its
        # neighbors along its chain over this multiple of its own radius, and
        # each traced outline over this many microns along its chain; the
        # surface is smoothed over the same lengths.  0 is off: the surface
        # as it has always been (see sbfsem.geometry.curve_average).
        "curve_avg_discs": 0.0,
        "curve_avg_paths": 0.0,
        # Closed curve: curve the runs of disc links at least this long (um)
        # through the chain's discs instead of drawing them straight; 0 is
        # off (see sbfsem.geometry.curve_links).
        "curve_links_um": 0.0,
        # Closed curve and Hybrid's outline half: the widest skip between
        # traced sections that is interpolated, and the longest a Viking link
        # is followed across skipped sections, um (closed_curve's BRIDGE_UM
        # and LINK_BRIDGE_UM, constants until 2026-10-16).
        "curve_bridge_um": BRIDGE_UM,
        "curve_link_bridge_um": LINK_BRIDGE_UM,
        # Closed curve: join separate bodies of the raster up to this far apart
        # with a tie along the shortest crossing (um); 0 is off.
        "curve_tie_um": 0.0,
        # Every method: smooth the finished surface over this length (um), moving
        # no annotation (sbfsem.geometry.curve_average.smooth_surface); 0 is off.
        "surface_smooth_um": 0.0,
    }

    #: Options a surface stored before they existed was built without, and the
    #: value it was built with: left out of its key at that value, so a deploy
    #: that adds one keeps every stored surface.  Named in the key otherwise.
    UNKEYED_AT_OFF: dict[str, float] = {
        "curve_avg_discs": 0.0, "curve_avg_paths": 0.0, "curve_links_um": 0.0,
        "curve_bridge_um": BRIDGE_UM, "curve_link_bridge_um": LINK_BRIDGE_UM, "curve_tie_um": 0.0,
        "surface_smooth_um": 0.0, "exact_tube_deg": 0.0,
    }

    @classmethod
    def _mesh_options(cls, **kwargs) -> dict:
        return {k: kwargs.get(k, v) for k, v in cls.MESH_OPTION_DEFAULTS.items()}

    #: The outline methods' own options and the methods that read them
    #: (``_build_geometry``): under any other method they change nothing, so they
    #: stay out of its key at any value.  The viewer sends Join bodies at 5 um
    #: with every build since 2.6.2 (its default for Closed curve); a Cylinder or
    #: Voxel remesh surface keeps the key it had.
    READ_ONLY_BY: dict[str, tuple[str, ...]] = {
        # Smooth discs reaches every method that draws discs (2.7.0): Closed curve's
        # circles, and the tubes of Cylinder, Tube and Hybrid's disc half.
        "curve_avg_discs": ("closedcurve", "cylinder", "curvemesh", "hybrid"),
        "curve_links_um": ("closedcurve",),
        "curve_tie_um": ("closedcurve",), "curve_avg_paths": ("closedcurve", "hybrid"),
        "curve_bridge_um": ("closedcurve", "hybrid"), "curve_link_bridge_um": ("closedcurve", "hybrid"),
        # Exact tubes: the tubes of the disc methods (2.9.0).
        "exact_tube_deg": ("cylinder", "curvemesh", "hybrid"),
    }

    @classmethod
    def _options_key(cls, options: dict, method: str | None = None) -> tuple:
        """The options as a mesh's key names them: sorted, without the ones in
        :data:`UNKEYED_AT_OFF` that hold the value they were built with before
        they existed.

        A Fine voxel typed under 0.04 um was built at 0.04 until 2026-10-16
        (``unify.MIN_VOXEL_UM``); it is now honored down to 0.02
        (``unify.TYPED_MIN_VOXEL_UM``), so its key names the new floor: the
        surface stored under the old key is not served for it.  Only a build whose
        surface comes from the whole-cell grid reads the Fine voxel
        (:func:`_uses_whole_cell_grid`, given ``method``): every other key, and the
        automatic voxel's, is as it was.  An outline option the method does not
        read (:data:`READ_ONLY_BY`) is left out whatever its value."""
        def unkeyed(k, v) -> bool:
            if method is not None and k in cls.READ_ONLY_BY and str(method) not in cls.READ_ONLY_BY[k]:
                return True
            if k not in cls.UNKEYED_AT_OFF:
                return False
            built = cls.UNKEYED_AT_OFF[k]
            return (not v) if not built else v == built
        key = sorted((k, v) for k, v in options.items() if not unkeyed(k, v))
        typed = options.get("voxel2_um") or 0.0
        if 0.0 < float(typed) < MIN_VOXEL_UM and _uses_whole_cell_grid(method, options):
            key.append(("voxel2_floor", TYPED_MIN_VOXEL_UM))
        return tuple(key)

    def _cached_mesh(
        self, neuron: Neuron, method: str, smooth_iterations: int,
        reduce_fraction: float, pixel_size_um: float, options: dict | None = None,
    ) -> Mesh:
        """Build a mesh, or return the cached one.

        Every path that needs a mesh goes through here — the JSON endpoint, the
        glTF stream and every binary exporter — so a ``.glb`` or ``.stl`` of a
        mesh already on screen is free rather than a second full rebuild.
        """
        method = canonical_method(method)
        options = self._mesh_options(**(options or {}))
        # Every option that changes the geometry is part of the key, or two
        # different settings would share one cached mesh.  So are the moves
        # the current section transforms and a section correction made: the
        # ones applied, not the ones asked for (positions_token).
        token = "|".join(t for t in (positions_token(neuron), _budget_token(method, options),
                                     _hybrid_token(method, options, neuron))
                         if t) or None
        key = (
            _Key(neuron.source, int(neuron.id)),
            method,
            smooth_iterations,
            reduce_fraction,
            pixel_size_um,
            self._options_key(options, method),
            token,
        )
        # In memory, a mesh belongs to the copy of the cell it was built from:
        # a build of the old copy that finishes after a reload cannot then be
        # served for the new one.  The disk key below is left as it was.
        memo = (*key, getattr(neuron, "load_serial", None))
        with self._lock:
            mesh = self._meshes.get(memo)
            if mesh is not None:
                self._meshes.move_to_end(memo)
                return mesh
        # On disk, the key also names the annotations the mesh came from, so
        # a cell refreshed from Viking cannot be served its old surface -- and
        # the geometry version, so a change in how annotations are read (the
        # annotations themselves unchanged) cannot be served the old one either.
        # The annotations are named exactly as Viking's StructureFreshness
        # reports the cell's own (depth 0): one batched request tells whether
        # a surface is on disk, a synapse edit no longer looks like a new
        # surface, and the same digits under Viking's old and new time labels
        # are one key.
        stamp, count = cell_freshness(neuron)
        disk_key: tuple = (*key[:-1], stamp.isoformat() if stamp else "unstamped", count,
                           GEOMETRY_VERSION)
        # The key before 2026-10-15: the newest Location as labeled, and the
        # cell's nodes with its children's.  Read once more on a miss and
        # stored again under the new key, so the deploy does not rebuild every
        # surface on disk.  REMOVE this read (the ``legacy`` key built below
        # and its fallback after the disk-cache miss) in the commit that next
        # bumps GEOMETRY_VERSION: the legacy key carries it, so from then on
        # nothing on disk matches it.  Waiting for the entries to age out would
        # wait for ever: the disk budget is 64 GB in production, and they are
        # not pruned before it is reached.
        legacy = neuron.data_stamp.isoformat() if neuron.data_stamp else "unstamped"
        legacy_key: tuple = (*key[:-1], legacy, int(len(neuron.nodes)), GEOMETRY_VERSION)
        # An uncorrected cell keeps the key it always had.
        if token:
            disk_key = (*disk_key, token)
            legacy_key = (*legacy_key, token)
        # Which mesh this is, for a client that holds it and wants numbers
        # attached to it: the disk key names the geometry's every input.
        mesh_id = hashlib.sha1(repr(disk_key).encode("utf-8")).hexdigest()[:12]
        # Keyed on what the build makes (the disk key), not on the copy of the
        # cell: two copies that make the same mesh share the one build, and
        # the second then reads it from disk instead of writing it again.
        with self._single_flight(("mesh", *disk_key)) as flight:
            # A request that waited here for the same mesh finds it built ...
            with self._lock:
                mesh = self._meshes.get(memo)
                if mesh is not None:
                    self._meshes.move_to_end(memo)
                    return mesh
            # ... or hears why it could not be: a build that failed after
            # minutes (a MeshLab timeout) is not run again by every waiter.
            if flight[2] is not None:
                raise flight[2]
            if self._disk_meshes is not None:
                mesh = self._disk_meshes.get(disk_key)
                if mesh is None and legacy_key != disk_key:
                    mesh = self._disk_meshes.get(legacy_key)
                    if mesh is not None:
                        self._disk_meshes.put(disk_key, mesh)
                if mesh is not None:
                    self._name_mesh(mesh, mesh_id)
                    self._cache_mesh(memo, mesh)
                    self._estimator.note_build(neuron, method, mesh.meta.get(CURVE_RASTER_META))
                    return mesh
            try:
                mesh = self._run_build(
                    neuron, method, smooth_iterations, reduce_fraction, pixel_size_um, options
                )
            except Exception as exc:
                flight[2] = exc
                raise
            self._name_mesh(mesh, mesh_id)
            self._cache_mesh(memo, mesh)
            # The raster this build counted anyway: the next estimate of the cell
            # at another Voxel size scales it instead of rasterizing.
            self._estimator.note_build(neuron, method, mesh.meta.get(CURVE_RASTER_META))
            if self._disk_meshes is not None and not mesh.is_empty:
                self._disk_meshes.put(disk_key, mesh)
            # A closed-curve render allocates and frees hundreds of megabytes; hand
            # the arenas back rather than carrying them for the process lifetime.
            # No-op off glibc.
            trim_memory()
            return mesh

    @staticmethod
    def _name_mesh(mesh: Mesh, mesh_id: str) -> None:
        """Give a cached mesh its id.  The empty mesh is one shared object for
        every cell with nothing to build, so it is never written to."""
        if not mesh.is_empty:
            mesh.meta["mesh_id"] = mesh_id

    @contextlib.contextmanager
    def _single_flight(self, key: tuple, waiting: str = "waiting for another request to build the same thing"):
        """Hold the one lock for ``key``, and say so while waiting for it.

        The lock is forgotten with its last user.  The record it yields
        (``[lock, users, failure]``) lets the holder leave the failure of its
        build for the requests that waited, which would otherwise each build
        it again, one after another.
        """
        with self._lock:
            flight = self._flights.setdefault(key, [threading.Lock(), 0, None])
            flight[1] += 1
        try:
            if not flight[0].acquire(blocking=False):
                # Someone else is building it: the job's poll should not read
                # as if nothing were happening.
                progress.report(waiting)
                flight[0].acquire()
            try:
                yield flight
            finally:
                flight[0].release()
        finally:
            with self._lock:
                flight[1] -= 1
                if flight[1] == 0:
                    self._flights.pop(key, None)

    def _build_mesh(
        self, neuron: Neuron, method: str, smooth_iterations: int,
        reduce_fraction: float, pixel_size_um: float, options: dict | None = None,
    ) -> Mesh:
        """The technique's mesh, then MeshLab's remesh of it when asked for."""
        mesh = self._build_geometry(
            neuron, method, smooth_iterations, reduce_fraction, pixel_size_um, options)
        opts = self._mesh_options(**(options or {}))
        surface_um = float(opts["surface_smooth_um"])
        if surface_um > 0 and not mesh.is_empty:
            # The finished surface alone, every annotation where it was drawn.
            progress.report("smoothing the surface")
            mesh = smooth_surface(mesh, *annotation_ground_truth(neuron), length_um=surface_um)
        if opts["meshlab"] != "none" and not mesh.is_empty:
            progress.report("MeshLab remesh")
            mesh = meshlab.remesh(
                mesh, str(opts["meshlab"]),
                edge_um=float(opts["meshlab_edge_um"]),
                fraction=float(opts["meshlab_fraction"]),
                depth=int(opts["meshlab_depth"]),
            )
        return mesh

    def _build_geometry(
        self, neuron: Neuron, method: str, smooth_iterations: int,
        reduce_fraction: float, pixel_size_um: float, options: dict | None = None,
    ) -> Mesh:
        method = method.lower()
        opts = self._mesh_options(**(options or {}))
        # The closed-curve method averages out its raster's staircase when asked
        # (``curve_avg_discs`` for the discs it draws as circles, ``curve_avg_paths``
        # for traced outlines).  Hybrid draws no circles, so only its outlines.
        avg_discs, avg_paths = float(opts["curve_avg_discs"]), float(opts["curve_avg_paths"])
        # And the reach of its bridges across skipped sections (both methods),
        # the curving of long disc links and the joining of separate bodies
        # (Closed curve alone: Hybrid's discs are the Cylinder build's).
        bridge_um = float(opts["curve_bridge_um"])
        link_bridge_um = float(opts["curve_link_bridge_um"])
        if method in ("closedcurve", "curve", "outline", "volume"):
            mesh = self._curve_mesh(neuron, pixel_size_um, avg_discs=avg_discs,
                                    avg_paths=avg_paths, curve_um=float(opts["curve_links_um"]),
                                    tie_um=float(opts["curve_tie_um"]), bridge_um=bridge_um,
                                    link_bridge_um=link_bridge_um)
            if mesh.is_empty:
                log.warning("c%s left nothing in its closed-curve volume", neuron.id)
            return mesh
        if method in ("hybrid", "mixed"):
            # Both halves of a mixed-annotation structure: the closed-curve
            # volume renders the traced outlines, the disc pathway renders the
            # circle annotations, and the union shows every annotation.  RC2's
            # c1 is the motivating case (15,834 curves + 5,922 discs); a cell
            # with only one kind degrades to that kind's plain build.
            volume = self._curve_volume(neuron, pixel_size_um, avg_paths=avg_paths,
                                        bridge_um=bridge_um, link_bridge_um=link_bridge_um)
            if volume is not None:
                progress.report("contouring outlines")
            curve = self._contour_curve(volume, 0.0, avg_paths) if volume is not None else None
            discs = _disc_subset(neuron)
            if discs is not None and volume is not None and curve is not None \
                    and not curve.is_empty and opts["pathway"] in HYBRID_ONE_FIELD:
                # Only its size is wanted from the outline half on its own: what
                # the field's budget and the Face cap allow for it, and the
                # raster's record for the next estimate.  The mesh goes before
                # the field is built.
                curve_area, curve_faces = curve.surface_area(), len(curve.faces)
                raster = curve.meta.get(CURVE_RASTER_META)
                del curve
                # A pathway that contours one whole-cell field takes the outline
                # raster into that field: the outlines and the discs are one
                # surface, not two sheets laid through each other (c1178's soma
                # top, drawn with discs above its 127 traced sections, was a
                # shell of its own sitting on the traced soma).  The outline half's
                # triangles join the Face cap, so the cell keeps the detail the
                # two halves had.
                mesh = self._cylinder_mesh(
                    discs, "cylinder", smooth_iterations, reduce_fraction, options,
                    extra_items=[("curve", volume, curve_area)],
                    extra_faces=curve_faces, max_auto_um=pixel_size_um,
                )
                if volume.labels is not None and not mesh.is_empty:
                    progress.report("averaging the surface")
                    mesh = polish_curve_mesh(mesh, volume, discs=0.0, paths_um=avg_paths)
                if not mesh.is_empty and raster is not None:
                    mesh.meta[CURVE_RASTER_META] = raster
                mesh.meta["hybrid_one_field"] = True
                return mesh
            parts = [curve] if curve is not None else []
            if discs is not None:
                # The disc graph is cut wherever a traced run interrupts it,
                # so the disc build sees a gap across every such soma and
                # would tie it shut with a wire through the outlines.  Pieces
                # that touch the traced volume are one body through it
                # already; tell the tie step so it lays no wire between them.
                attached = None
                if volume is not None:
                    def attached(piece, _v=volume):  # noqa: E306
                        return _v.touches(piece.vertices, tolerance_um=0.5)
                parts.append(self._cylinder_mesh(
                    discs, "cylinder", smooth_iterations, reduce_fraction, options,
                    attached_elsewhere=attached,
                ))
            parts = [p for p in parts if not p.is_empty]
            if not parts:
                return EMPTY_MESH
            if len(parts) == 1:
                return parts[0]
            joined = concatenate(parts)
            # The halves' own notes survive the join: the outline raster's cost and
            # the disc half's whole-cell grid (a Voxel remesh pathway).
            for part in parts:
                joined.meta.update({k: v for k, v in part.meta.items()
                                    if k == CURVE_RASTER_META or k.startswith("voxel2_")})
            return joined
        return self._cylinder_mesh(
            neuron, method, smooth_iterations, reduce_fraction, options
        )

    def _curve_volume(self, neuron: Neuron, pixel_size_um: float, *,
                      discs: bool = False, avg_discs: float = 0.0,
                      avg_paths: float = 0.0, curve_um: float = 0.0,
                      bridge_um: float = BRIDGE_UM,
                      link_bridge_um: float = LINK_BRIDGE_UM,
                      limits: Mapping[str, int] | None = None) -> CurveVolume | None:
        """The rasterized closed-curve outlines, or ``None`` without any.

        ``discs``: a cell with no traced outlines is rasterized from its discs
        instead (:func:`_curve_outlines`).  ``avg_discs`` and ``avg_paths``
        average the annotations along their chains first (and make the raster
        remember which annotation drew each voxel, for the surface's averaging
        after the contour); ``curve_um``, ``bridge_um`` and ``link_bridge_um``
        are :func:`_curve_outlines`'.  ``limits`` (``max_voxels``,
        ``max_surface``) are :func:`build_curve_volume`'s, which refuses past
        them with a ``ValueError``."""
        annotations, how = _curve_outlines(
            neuron, discs=discs, avg_discs=avg_discs, avg_paths=avg_paths,
            pixel_size_um=pixel_size_um, curve_um=curve_um, bridge_um=bridge_um,
            link_bridge_um=link_bridge_um)
        if not annotations:
            return None
        progress.report("rasterizing outlines")
        return build_curve_volume(
            annotations,
            volume_scale=neuron.volume_scale,
            pixel_size_um=pixel_size_um,
            keep_labels=bool(avg_discs > 0 or avg_paths > 0),
            **how,
            **(limits or {}),
        )

    def _hybrid_halves(self, neuron: Neuron, options: dict, max_voxels: int,
                       max_faces: int) -> tuple[list, CurveVolume, float, int] | None:
        """What a Hybrid build in one field contours ``neuron`` from, under
        ``options`` (``_mesh_options``' and ``pixel_size_um``), for its plan
        (``FieldPlanner``): the disc annotations' field pieces, the outline
        raster at the build's pixel, and the area and triangles of the surface
        the outline half makes of it alone, as :meth:`_build_geometry` measures
        them before it builds the field.  ``None`` when the build is not one
        field; a ``ValueError`` when the raster passes ``max_voxels`` or
        ``max_faces``, before it is allocated or contoured."""
        discs = _disc_subset(neuron)
        if discs is None or discs is neuron:
            return None
        volume = self._curve_volume(
            neuron, float(options["pixel_size_um"]),
            bridge_um=float(options.get("curve_bridge_um", BRIDGE_UM)),
            link_bridge_um=float(options.get("curve_link_bridge_um", LINK_BRIDGE_UM)),
            limits={"max_voxels": int(max_voxels), "max_surface": int(max_faces)})
        if volume is None:
            return None
        curve = self._contour_curve(volume, 0.0, 0.0)
        if curve.is_empty:
            return None
        return self._field_pieces(discs, options), volume, float(curve.surface_area()), \
            int(len(curve.faces))

    @staticmethod
    def _contour_curve(volume: CurveVolume, avg_discs: float, avg_paths: float,
                       tie_um: float = 0.0) -> Mesh:
        """The surface of ``volume``, its separate bodies up to ``tie_um``
        apart joined (:func:`~sbfsem.geometry.curve_ties.tie_bodies`), then
        averaged over the lengths asked for."""
        # Joining bodies contours again where the ties reach, from the blocks kept.
        blocks: dict | None = {} if tie_um > 0 else None
        mesh = curve_volume_to_mesh(volume, cache=blocks)
        if tie_um > 0 and not mesh.is_empty:
            progress.report("joining bodies")
            mesh, ties = tie_bodies(volume, mesh, tie_um, cache=blocks)
            if ties:
                mesh.meta["curve_ties"] = ties
        if volume.labels is not None and not mesh.is_empty:
            progress.report("averaging the surface")
            mesh = polish_curve_mesh(mesh, volume, discs=avg_discs, paths_um=avg_paths)
        if not mesh.is_empty and volume.exposed is not None:
            # What this raster cost, for the next estimate of the cell at another
            # pixel (sbfsem.service.estimate): the build counted it anyway.
            mesh.meta[CURVE_RASTER_META] = raster_record(volume, len(mesh.faces))
        return mesh

    def _curve_mesh(self, neuron: Neuron, pixel_size_um: float, *,
                    avg_discs: float = 0.0, avg_paths: float = 0.0, curve_um: float = 0.0,
                    bridge_um: float = BRIDGE_UM, link_bridge_um: float = LINK_BRIDGE_UM,
                    tie_um: float = 0.0) -> Mesh:
        # Hybrid's disc half is the disc build, so only Closed curve itself
        # stands a disc-traced cell's circles in for outlines.
        volume = self._curve_volume(neuron, pixel_size_um, discs=True, avg_discs=avg_discs,
                                    avg_paths=avg_paths, curve_um=curve_um, bridge_um=bridge_um,
                                    link_bridge_um=link_bridge_um)
        if volume is None:
            return EMPTY_MESH
        progress.report("contouring outlines")
        return self._contour_curve(volume, avg_discs, avg_paths, tie_um)

    def _cylinder_mesh(
        self, neuron: Neuron, method: str, smooth_iterations: int,
        reduce_fraction: float, options: dict | None,
        attached_elsewhere=None, extra_items: list | None = None, extra_faces: int = 0,
        max_auto_um: float | None = None,
    ) -> Mesh:
        opts = self._mesh_options(**(options or {}))
        avg = float(opts["curve_avg_discs"])
        if avg > 0:
            # Smooth discs: each disc averaged with its neighbors along its
            # chain of links before the tubes are built (_averaged_discs).
            progress.report("averaging discs")
            neuron = _averaged_discs(neuron, avg)
        return cylinder_render(
            neuron,
            attached_elsewhere=attached_elsewhere,
            extra_items=extra_items,
            extra_faces=extra_faces,
            max_auto_um=max_auto_um,
            method=2 if method in ("curvemesh", "tube") else 1,
            smooth_iterations=smooth_iterations,
            reduce_fraction=reduce_fraction,
            pathway=opts["pathway"],
            post_order=opts["post_order"],
            voxel_detail=opts["voxel_detail"],
            voxel_smooth_um=opts["voxel_smooth_um"],
            tie_um=opts["tie_um"],
            target_arc_um=opts["ring_arc_um"],
            wrap_spacing_um=opts["wrap_spacing_um"],
            wrap_blend=opts["wrap_blend"],
            wrap_reject_sigma=opts["wrap_reject_sigma"],
            wrap_passes=int(opts["wrap_passes"]),
            voxel2_um=float(opts["voxel2_um"]),
            voxel2_faces=int(opts["voxel2_faces"]),
            voxel2_blend_um=float(opts["voxel2_blend_um"]),
            exact_tube_deg=float(opts["exact_tube_deg"]),
            conv_support=float(opts["conv_support"]),
            ultra_soma=str(opts["ultra_soma"]),
            ultra_smooth=int(opts["ultra_smooth"]),
            ultra_lambda=float(opts["ultra_lambda"]),
            ultra_inflate=float(opts["ultra_inflate"]),
        )

    #: Marker shapes accepted by :meth:`synapse_mesh`.
    SYNAPSE_SHAPES = ("circle", "oval")

    def synapse_mesh(
        self,
        neuron_id: int,
        source,
        synapse_type: str,
        *,
        radius_um: float = 0.5,
        shape: str = "circle",
    ) -> MeshDTO:
        """Marker mesh for every synapse of one type.

        ``circle`` is a sphere per synapse, as MATLAB's ``synapseSphere`` drew
        them.  ``oval`` is an ellipsoid aligned to the annotation's own
        ``LINESTRING`` axis, which shows which way each synapse was drawn.
        """
        if shape not in self.SYNAPSE_SHAPES:
            raise ValueError(
                f"shape must be one of {self.SYNAPSE_SHAPES}, got {shape!r}"
            )
        neuron = self.neuron(neuron_id, source)
        name = f"c{neuron.id}-{synapse_type}"
        if shape == "circle":
            xyz = neuron.get_synapse_xyz(synapse_type)
            return self._mesh_dto(name, synapse_spheres(xyz, radius_um))
        centers, axes, halves = neuron.get_synapse_axes(synapse_type)
        return self._mesh_dto(
            name, synapse_ellipsoids(centers, axes, halves, radius_um)
        )

    #: Coordinates are microns and the finest voxel is 7.5 nm, so four decimals
    #: (0.1 nm) is well past the data's resolution.  Rounding here roughly halves
    #: the JSON payload of a large mesh, which is all float text.
    COORDINATE_DECIMALS = 4

    @classmethod
    def _mesh_dto(cls, name: str, mesh: Mesh) -> MeshDTO:
        vertices = np.round(mesh.vertices, cls.COORDINATE_DECIMALS)
        meta = mesh.meta or {}
        voxel = meta.get("voxel2_um")
        coarsened = meta.get("voxel2_coarsened")
        return MeshDTO(
            name=name,
            n_vertices=mesh.n_vertices,
            n_faces=len(mesh.faces),
            vertices=[float(v) for v in vertices.ravel()],
            faces=[int(v) for v in mesh.faces.ravel()],
            vertex_data=(
                [round(float(v), 6) for v in mesh.vertex_data]
                if mesh.vertex_data is not None
                else None
            ),
            bounding_box_um=[float(v) for v in mesh.bounding_box],
            surface_area_um2=mesh.surface_area(),
            volume_um3=mesh.volume(),
            mesh_id=mesh.meta.get("mesh_id"),
            voxel2_um=float(voxel) if voxel is not None else None,
            voxel2_asked_um=(float(meta["voxel2_asked"])
                             if meta.get("voxel2_asked") is not None else None),
            voxel2_coarsened=bool(coarsened) if coarsened is not None else None,
            voxel2_reason=(str(meta["voxel2_reason"]) or None) if "voxel2_reason" in meta else None,
        )

    @classmethod
    def _check_option_keys(cls, options: dict) -> None:
        """Refuse a render option this service does not have.

        A mistyped knob taken for its default builds, caches and exports a
        different surface than the one asked for, with no word of it: the
        measurement path always refused, and so now do the exports, the
        difference and the deviation (``mesh_for``).
        """
        allowed = set(cls.MESH_OPTION_DEFAULTS) | {
            "method", "smooth_iterations", "reduce_fraction", "pixel_size_um",
        }
        unknown = sorted(set(options) - allowed)
        if unknown:
            raise ValueError(
                f"unknown render option(s) {', '.join(unknown)}; "
                f"expected one of {', '.join(sorted(allowed))}"
            )

    def build_mesh(self, neuron: Neuron, **options) -> Mesh:
        """Build a mesh for a neuron object, uncached and unregistered.

        Same option keys as :meth:`render_mesh` (``method``,
        ``smooth_iterations``, ``reduce_fraction``, ``pixel_size_um`` and the
        :data:`MESH_OPTION_DEFAULTS` knobs); no clipping or coloring.  This
        is the measurement path -- ``sbfsem-measure`` times every build, so
        nothing here may come back from a cache.  Unknown keys raise rather
        than fall back silently: a mistyped knob measured at its default is
        worse than no measurement.
        """
        options = dict(options)
        self._check_option_keys(options)
        return self._build_mesh(
            neuron,
            canonical_method(options.pop("method", "cylinder")),
            int(options.pop("smooth_iterations", 1)),
            float(options.pop("reduce_fraction", 1.0)),
            float(options.pop("pixel_size_um", 0.05)),
            self._mesh_options(**options),
        )

    def mesh_object(self, neuron_id: int, source, **kwargs) -> Mesh:
        """The backend mesh, for exporters that need it (GLB, STL, DAE).

        Shares the cache with :meth:`render_mesh`, so exporting a mesh the user
        is already looking at costs nothing.  The returned mesh is the cached
        instance; exporters read it and must not mutate it.
        """
        return self.mesh_for(self.neuron(neuron_id, source), kwargs)

    def clipped_mesh(
        self,
        mesh: Mesh,
        source,
        *,
        clip_z: float | None = None,
        clip_ipl: float | None = None,
        keep_above: bool = True,
    ) -> Mesh:
        """``mesh`` cut the way :meth:`render_mesh` cuts a render, for an export
        of a render that is clipped on screen (``clip_z`` along Z, then
        ``clip_ipl`` by stratification depth).

        The mesh handed in is the cached one and is never changed; with no clip
        it comes back as it is.  Raises :class:`ValueError` for ``clip_ipl`` in a
        volume that has no IPL boundary markers.
        """
        if clip_z is not None:
            mesh = clip_mesh(mesh, clip_z, keep_above=keep_above)
        if clip_ipl is not None:
            mesh = clip_mesh_by_stratification(
                mesh, self.boundaries(source), clip_ipl, keep_above=keep_above
            )
        return mesh

    def mesh_for(self, neuron: Neuron, options: dict | None = None) -> Mesh:
        """The mesh a render with ``options`` makes of this copy of the cell.

        Cached, and shared with :meth:`render_mesh`: a measurement of a mesh a
        pane already holds is a cache hit.  The options take the same keys as
        :meth:`render_mesh`; clipping and strata coloring are not applied.
        """
        kwargs = dict(options or {})
        self._check_option_keys(kwargs)
        return self._cached_mesh(
            neuron,
            kwargs.pop("method", "cylinder"),
            kwargs.pop("smooth_iterations", 1),
            kwargs.pop("reduce_fraction", 1.0),
            kwargs.pop("pixel_size_um", 0.05),
            # Exports must match what is on screen, so they take the same
            # options rather than silently falling back to the defaults.
            self._mesh_options(**kwargs),
        )

    def mesh_diff(
        self,
        neuron_id: int,
        source,
        *,
        options_a: dict | None = None,
        options_b: dict | None = None,
        samples: int = 250_000,
        sign: str = "normal",
    ) -> MeshDiffDTO:
        """Where two renderings of one neuron disagree, in microns.

        Builds both meshes (sharing :meth:`render_mesh`'s cache, so comparing
        what is already on screen is cheap) and returns mesh A with a signed
        per-vertex distance to B's surface in ``vertex_data`` — positive where
        A is outside B.  Options dicts take the same keys as
        :meth:`render_mesh`; clipping and strata coloring are not applied.
        ``sign`` is as for :func:`sbfsem.geometry.meshdiff.mesh_difference`
        (``"enclosure"`` takes much longer and several hundred MB more; the
        compare view asks for it through a job, which the web API's blocking
        route refuses to run in a request).
        """
        neuron = self.neuron(neuron_id, source)
        mesh_a = self.mesh_for(neuron, options_a)
        mesh_b = self.mesh_for(neuron, options_b)
        if mesh_a.is_empty or mesh_b.is_empty:
            raise ValueError(
                "one of the renderings produced no mesh; there is nothing to compare"
            )
        diff = self._cached_difference(neuron, options_a, options_b, mesh_a, mesh_b, samples,
                                       sign=sign)
        deviations = [self._cached_deviation(neuron, opts, mesh)
                      for opts, mesh in ((options_a, mesh_a), (options_b, mesh_b))]
        colored = Mesh(mesh_a.vertices, mesh_a.faces, diff.distance_um)
        return MeshDiffDTO(
            mesh=self._mesh_dto(f"c{neuron.id}-diff", colored),
            mean_abs_um=diff.mean_abs_um,
            rms_um=diff.rms_um,
            p95_abs_um=diff.p95_abs_um,
            max_abs_um=diff.max_abs_um,
            mean_signed_um=diff.mean_signed_um,
            scale_um=diff.scale_um,
            sample_spacing_um=diff.sample_spacing_um,
            n_samples=diff.n_samples,
            deviation_a=deviations[0],
            deviation_b=deviations[1],
            sign_by=diff.sign_by,
        )

    def _cached_deviation(self, neuron: Neuron, options: dict | None,
                          mesh: Mesh) -> DeviationDTO | None:
        """One rendering's deviation from the annotations, measured once."""
        dev_key = self._measure_key(neuron, options)
        with self._lock:
            if dev_key in self._deviations:
                self._deviations.move_to_end(dev_key)
                return self._deviations[dev_key]
        # An error map and a difference that start together both end here;
        # the 70 CPU-seconds are spent once.
        with self._single_flight(("deviation", *dev_key)):
            with self._lock:
                if dev_key in self._deviations:
                    self._deviations.move_to_end(dev_key)
                    return self._deviations[dev_key]
            dev = self._deviation_dto(
                annotation_deviation(mesh, *self._annotation_ground_truth(neuron)))
            with self._lock:
                self._deviations[dev_key] = dev
                while len(self._deviations) > 64:
                    self._deviations.popitem(last=False)
            return dev

    def _measure_key(self, neuron: Neuron, options: dict | None,
                     *more: Any) -> tuple:
        """What a measurement of one rendering of one copy of a cell is keyed on.

        The full geometry signature -- the option set :meth:`_mesh_options`
        normalizes PLUS the arguments :meth:`_cached_mesh` keys separately
        (method and smoothing change the mesh just as much as a pathway) --
        and the copy's serial last, as a mesh's key ends: a measurement belongs
        to the copy it was made on, and :meth:`_drop_meshes` finds it by that.
        """
        o = dict(options or {})
        signature = dict(self._mesh_options(**o))
        for extra in ("method", "smooth_iterations", "reduce_fraction", "pixel_size_um"):
            if extra in o:
                signature[extra] = o[extra]
        return (_Key(neuron.source, int(neuron.id)), tuple(sorted(signature.items())),
                *more, getattr(neuron, "load_serial", None))

    def _cached_deviation_map(self, neuron: Neuron, options: dict | None, mesh: Mesh):
        """One rendering's error map (:func:`deviation_map`), measured once.

        Minutes on a large cell, and until 2026-10-15 recomputed on every
        request.  ``None`` when the cell has no annotations to measure against.
        """
        map_key = self._measure_key(neuron, options)

        def held():
            # An entry for another mesh (a rebuild that came out with another
            # vertex count) is not an answer for this one.
            with self._lock:
                found = self._deviation_maps.get(map_key)
                if found is not None and len(found.vertex_um) == mesh.n_vertices:
                    self._deviation_maps.move_to_end(map_key)
                    return found
            return None

        cached = held()
        if cached is not None:
            return cached
        with self._single_flight(("deviation-map", *map_key)):
            cached = held()
            if cached is not None:
                return cached
            found = deviation_map(mesh, *self._annotation_ground_truth(neuron))
            if found is not None:
                with self._lock:
                    self._deviation_maps[map_key] = found
                    while len(self._deviation_maps) > self.DEVIATION_MAP_CACHE_SIZE:
                        self._deviation_maps.popitem(last=False)
            return found

    def _cached_difference(self, neuron: Neuron, options_a: dict | None,
                           options_b: dict | None, mesh_a: Mesh, mesh_b: Mesh,
                           samples: int, *, sign: str = "normal", both: bool = False,
                           neuron_b: Neuron | None = None):
        """The signed per-vertex distance from mesh A to mesh B, measured once.

        Keyed on the sign rule as well as on the two renderings: the same pair
        signed by normal and by enclosure are two measurements (the second
        costs tens of seconds on a large cell).  ``both`` is not in the key.
        ``neuron_b`` is the copy of the cell mesh B was built from when it is not
        mesh A's (a surface before and after a setting that moves the
        annotations): the key then names both copies' serials, so reloading
        either one retires the measurement (:meth:`_drop_meshes`), and a pair
        across two copies never answers for the same two renderings of one.
        An entry that holds the reverse field also answers a one-way request
        (the reverse half is left off the answer, as it is not what was asked
        for), and an entry without it answers a both-ways request after the
        reverse pass alone (:func:`~sbfsem.geometry.meshdiff.add_reverse`):
        the forward field, the expensive half the person already paid for, is
        never measured twice.
        """
        key_a = self._measure_key(neuron, options_a)
        key_b = self._measure_key(neuron if neuron_b is None else neuron_b, options_b)
        if key_b[0] != key_a[0]:
            raise ValueError("the two copies are not of the same cell: "
                             f"c{key_a[0].neuron_id} {key_a[0].source} and "
                             f"c{key_b[0].neuron_id} {key_b[0].source}")
        diff_key = (key_a[0], (key_a[1], key_b[1], int(samples), str(sign), key_b[-1]),
                    key_a[-1])

        def reverse_fits(diff) -> bool:
            return (diff.distance_b_um is not None
                    and len(diff.distance_b_um) == mesh_b.n_vertices)

        def held():
            with self._lock:
                diff = self._differences.get(diff_key)
                # An entry for other meshes (a rebuild that came out with another
                # vertex count) is not an answer for these.
                if diff is not None and len(diff.distance_um) == mesh_a.n_vertices and (
                        diff.distance_b_um is None or reverse_fits(diff)):
                    self._differences.move_to_end(diff_key)
                    return diff
            return None

        def answer(diff):
            # What was asked for: the reverse half only when it was.
            if both or diff.distance_b_um is None:
                return diff
            return replace(diff, distance_b_um=None, stats_area_b=None, sign_overruled_b=0)

        cached = held()
        if cached is not None and (not both or cached.distance_b_um is not None):
            return answer(cached)
        with self._single_flight(("difference", *diff_key)):
            cached = held()
            if cached is not None and (not both or cached.distance_b_um is not None):
                return answer(cached)
            if cached is not None:
                diff = add_reverse(cached, mesh_a, mesh_b, samples=samples)
            else:
                diff = mesh_difference(mesh_a, mesh_b, samples=samples, sign=sign, both=both)
            with self._lock:
                self._differences[diff_key] = diff
                while len(self._differences) > self.DIFFERENCE_CACHE_SIZE:
                    self._differences.popitem(last=False)
            return answer(diff)

    #: Error maps and mesh-to-mesh differences kept (see ``_deviation_maps``).
    DEVIATION_MAP_CACHE_SIZE = 32
    DIFFERENCE_CACHE_SIZE = 16

    #: What a measurement job adds to the process at its peak, MB.  Measured
    #: whole-job peaks above the idle process (error map plus summary, a fresh
    #: process, 2026-10-15): 650 to 1,336 MB on the largest cells (Inferior
    #: c1178 closed curve +675, RC2 c74420 +1,127, RPC1 c2628 +1,336 with its
    #: 1.44 million rim points).  The distance kernels' byte budget
    #: (``meshdiff.KERNEL_BUDGET_BYTES``) bounds only their own pair blocks;
    #: the rest is the mesh, the surface samples, their k-d tree and the
    #: enclosure test.  The build a job may have to do first is added by
    #: :meth:`measure_weight_mb`.
    MEASURE_MB = 1500.0
    #: What a MeshLab child adds beside the build worker that waits for it
    #: (measured 337 to 563 MB at 400,000 faces; Poisson above depth 9 is
    #: unmeasured, so it is weighed as the worst case).
    MESHLAB_MB = 600.0
    MESHLAB_POISSON_MB = 1500.0

    def measure_weight_mb(self, neuron: Neuron, *options_sets: dict | None) -> float:
        """The memory gate's weight of a measurement job over these renderings.

        The builds it may have to do come one after the other, so it is the
        heaviest of them, and at least what the measurement itself needs.
        """
        weight = self.MEASURE_MB
        for options in options_sets:
            o = dict(options or {})
            try:
                weight = max(weight, self.estimate_peak_mb(
                    neuron.id, neuron.source, method=o.get("method", "cylinder"),
                    pixel_size_um=o.get("pixel_size_um", 0.05),
                    pathway=o.get("pathway", "original"),
                    meshlab=o.get("meshlab", "none"), meshlab_depth=o.get("meshlab_depth", 9),
                    curve_averaging=curve_averaging(o.get("method", "cylinder"), o),
                    voxel2_faces=o.get("voxel2_faces"), voxel2_um=o.get("voxel2_um"),
                    curve_options=o, field_options=o, neuron=neuron,
                ))
            except Exception:  # noqa: BLE001 - the estimate must never block a job
                continue
        return weight

    def deviation_result(self, neuron: Neuron, options: dict | None = None,
                         ) -> DeviationResultDTO:
        """A rendering's error map as a job's result: numbers for the mesh the
        client already holds (see :class:`DeviationResultDTO`).

        Builds the mesh if no pane did (shown as the build's own stages), then
        measures; both halves are cached, so pressing Compare again, or ticking
        error on a pane another request already measured, answers at once.
        """
        try:
            mesh = self.mesh_for(neuron, options)
            # The build's last stage is over; a failure from here on is the
            # measurement's, and its message should say so.
            progress.report("measuring against the annotations")
            if mesh.is_empty:
                raise ValueError("the rendering produced no mesh; there is nothing to measure")
            found = self._cached_deviation_map(neuron, options, mesh)
            if found is None:
                raise ValueError(f"c{neuron.id} has no annotations to hold the render to")
            deviation = self._cached_deviation(neuron, options, mesh)
            return DeviationResultDTO(
                mesh_id=mesh.meta.get("mesh_id"),
                n_vertices=mesh.n_vertices,
                n_faces=len(mesh.faces),
                vertex_um=np.round(found.vertex_um, 4).tolist(),
                rim_xyz=np.round(found.rim_xyz, 4).ravel().tolist(),
                rim_um=np.round(found.rim_um, 4).tolist(),
                rim_stride=found.rim_stride,
                n_rims=found.n_rims,
                scale_um=found.scale_um,
                deviation=deviation,
                **self._deviation_detail(found, annotation_targets(neuron)),
            )
        finally:
            # The kernels run on a job thread of the web process, each with its
            # own allocator arena: hand the freed ones back, as a build does.
            trim_memory()

    @staticmethod
    def _area_stats_dto(stats: AreaStats | None) -> AreaStatsDTO | None:
        if stats is None:
            return None
        return AreaStatsDTO(
            mean_abs_um=stats.mean_abs_um, rms_um=stats.rms_um, p95_abs_um=stats.p95_abs_um,
            p98_abs_um=stats.p98_abs_um, max_abs_um=stats.max_abs_um,
            mean_signed_um=stats.mean_signed_um)

    def diff_result(self, neuron: Neuron, options_a: dict | None = None,
                    options_b: dict | None = None, samples: int = 250_000, *,
                    sign: str = "normal", both: bool = False,
                    neuron_b: Neuron | None = None) -> DiffResultDTO:
        """Two renderings' signed difference as a job's result: numbers for
        mesh A, which the client already holds (see :class:`DiffResultDTO`).

        ``sign`` and ``both`` are as for
        :func:`sbfsem.geometry.meshdiff.mesh_difference`: ``"enclosure"`` signs
        by whether B's solid encloses the vertex, and ``both`` adds the reverse
        field (B's vertices against A).  The sign rule is part of the cache key
        and ``both`` is not: see :meth:`_cached_difference`.

        ``neuron_b`` is the copy of the cell mesh B is built from and held to,
        when it is not ``neuron``'s (a pre-correction surface against a
        post-correction one): each mesh is measured against the annotations of
        the copy it was built from, and the difference is keyed on both.
        """
        other = neuron if neuron_b is None else neuron_b
        try:
            with progress.prefixed("mesh A: "):
                mesh_a = self.mesh_for(neuron, options_a)
            with progress.prefixed("mesh B: "):
                mesh_b = self.mesh_for(other, options_b)
            if mesh_a.is_empty or mesh_b.is_empty:
                raise ValueError("one of the renderings produced no mesh; there is nothing to compare")
            progress.report("comparing mesh A with mesh B")
            diff = self._cached_difference(neuron, options_a, options_b, mesh_a, mesh_b, samples,
                                           sign=sign, both=both, neuron_b=neuron_b)
            with progress.prefixed("mesh A against the annotations: "):
                deviation_a = self._cached_deviation(neuron, options_a, mesh_a)
            with progress.prefixed("mesh B against the annotations: "):
                deviation_b = self._cached_deviation(other, options_b, mesh_b)
            return DiffResultDTO(
                mesh_id=mesh_a.meta.get("mesh_id"),
                n_vertices=mesh_a.n_vertices,
                n_faces=len(mesh_a.faces),
                distance_um=np.round(diff.distance_um, 4).tolist(),
                mean_abs_um=diff.mean_abs_um,
                rms_um=diff.rms_um,
                p95_abs_um=diff.p95_abs_um,
                max_abs_um=diff.max_abs_um,
                mean_signed_um=diff.mean_signed_um,
                scale_um=diff.scale_um,
                sample_spacing_um=diff.sample_spacing_um,
                n_samples=diff.n_samples,
                deviation_a=deviation_a,
                deviation_b=deviation_b,
                sign_by=diff.sign_by,
                stats_area=self._area_stats_dto(diff.stats_area),
                distance_b_um=(np.round(diff.distance_b_um, 4).tolist()
                               if diff.distance_b_um is not None else []),
                stats_area_b=self._area_stats_dto(diff.stats_area_b),
                mesh_id_b=mesh_b.meta.get("mesh_id"),
                n_vertices_b=mesh_b.n_vertices,
                sign_overruled=diff.sign_overruled,
                sign_overruled_b=diff.sign_overruled_b,
                copy_a=getattr(neuron, "copy_tag", None),
                copy_b=getattr(other, "copy_tag", None),
            )
        finally:
            trim_memory()

    def mesh_deviation(self, neuron_id: int, source, *,
                       options: dict | None = None) -> DeviationMapDTO:
        """One rendering held to the cell's own annotations, point by point.

        The picture behind the Render compare tab's "vs annotations" numbers:
        the mesh with each vertex's distance to the nearest annotation in
        ``vertex_data``, and points along the annotations' rims with each
        one's distance to the surface.  Shares :meth:`render_mesh`'s cache and
        :meth:`mesh_diff`'s deviation cache.
        """
        neuron = self.neuron(neuron_id, source)
        mesh = self.mesh_for(neuron, options)
        if mesh.is_empty:
            raise ValueError("the rendering produced no mesh; there is nothing to measure")
        found = self._cached_deviation_map(neuron, options, mesh)
        if found is None:
            raise ValueError(f"c{neuron.id} has no annotations to hold the render to")
        colored = Mesh(mesh.vertices, mesh.faces, found.vertex_um)
        return DeviationMapDTO(
            mesh=self._mesh_dto(f"c{neuron.id}-deviation", colored),
            rim_xyz=np.round(found.rim_xyz, 4).ravel().tolist(),
            rim_um=np.round(found.rim_um, 4).tolist(),
            rim_stride=found.rim_stride,
            n_rims=found.n_rims,
            scale_um=found.scale_um,
            deviation=self._cached_deviation(neuron, options, mesh),
        )

    @staticmethod
    def _annotation_ground_truth(neuron):
        return annotation_ground_truth(neuron)

    @staticmethod
    def _deviation_detail(found, targets: AnnotationTargets) -> dict[str, Any]:
        """How the error arises, as the extra fields of a :class:`DeviationResultDTO`.

        Distances are rounded to 0.1 nm (the vertex distances are), shares to a
        millionth.  The annotations carry their Location IDs and sections from
        ``targets``, which numbers them as the measurement did; were the two
        ever to disagree in length, the names are left out rather than
        attached to the wrong rows.
        """
        if found.axial_um is None:
            return {}

        def um(values) -> list[float]:
            return np.round(np.asarray(values, dtype=float), 4).tolist()

        def share(values) -> list[float]:
            return np.round(np.asarray(values, dtype=float), 6).tolist()

        # Never beyond the vertex's own distance, so the documented sideways part,
        # sqrt(vertex_um**2 - axial_um**2), has no negative radicand after rounding.
        detail: dict[str, Any] = {
            "axial_um": um(np.minimum(np.asarray(found.axial_um, dtype=float),
                                      np.asarray(found.vertex_um, dtype=float)))}
        if found.rim_enclosed is not None:
            detail["rim_enclosed"] = found.rim_enclosed.astype(np.int8).tolist()
        if found.surface is not None:
            surface = found.surface
            detail["surface"] = DeviationSurfaceDTO(
                area_um2=surface.area_um2,
                mean_um=surface.mean_um,
                excess_um3=surface.excess_um3,
                quantiles=um(surface.quantiles_um),
                hist=DeviationHistogramDTO(edges=share(surface.hist_edges_um),
                                           area_share=share(surface.hist_share),
                                           tail_share=surface.tail_share),
                floor_um=found.floor_um,
            )
        named = found.annotations is not None and len(targets.ids) == len(found.annotations.kind)
        if found.annotations is not None:
            table = found.annotations
            detail["annotations"] = DeviationAnnotationsDTO(
                ids=targets.ids.tolist() if named else [],
                sections=targets.sections.tolist() if named else [],
                kind=table.kind.astype(int).tolist(),
                xyz=um(table.xyz_um.ravel()),
                radius=um(table.radius_um),
                area_share=share(table.area_share),
                surface_mean=um(table.surface_mean_um),
                surface_max=um(table.surface_max_um),
                rim_mean=um(table.rim_mean_um),
                rim_max=um(table.rim_max_um),
                rim_enclosed_share=[None if np.isnan(v) else round(float(v), 6)
                                    for v in table.rim_enclosed_share],
            )
        if found.z_profile is not None:
            z = found.z_profile
            detail["z_profile"] = DeviationZProfileDTO(
                z0=z.z0_um, step=z.step_um, area=um(z.area_um2), mean=um(z.mean_um), p95=um(z.p95_um),
                rim_mean=um(z.rim_mean_um), n_annotations=z.n_annotations.astype(int).tolist())
        detail["spots"] = [
            DeviationSpotDTO(
                xyz=um(spot.xyz_um),
                section=int(targets.sections[spot.owner]) if named else None,
                annotation_id=int(targets.ids[spot.owner]) if named else None,
                excess_um3=round(spot.excess_um3, 4), share=round(spot.share, 6),
                mean=round(spot.mean_um, 4), max=round(spot.max_um, 4))
            for spot in found.spots
        ]
        if found.bias is not None:
            bias = found.bias
            detail["bias"] = DeviationBiasDTO(
                n=bias.n_tested, enclosed_share=bias.enclosed_share,
                enclosed_mean_um=bias.enclosed_mean_um, outside_mean_um=bias.outside_mean_um,
                signed_mean_um=bias.signed_mean_um)
        return detail

    @staticmethod
    def _deviation_dto(dev) -> DeviationDTO | None:
        if dev is None:
            return None
        return DeviationDTO(
            mean_um=dev.mean_um,
            surface_um=dev.surface_um,
            annotation_um=dev.annotation_um,
            p95_um=dev.p95_um,
            max_um=dev.max_um,
            excess_volume_um3=dev.excess_volume_um3,
            sample_spacing_um=dev.sample_spacing_um,
            n_discs=dev.n_discs,
            n_curves=dev.n_curves,
            exterior_um=dev.exterior_um,
            hidden_fraction=dev.hidden_fraction,
            annotation_exterior_um=dev.annotation_exterior_um,
        )

    #: Context geometry (boundaries, vasculature) is expensive to build and
    #: identical for every viewer, so it is cached per volume for the process
    #: lifetime rather than per request.
    def boundary_mesh(self, source, layer: str, *, decimate: int = 2) -> MeshDTO:
        """The fitted INL or GCL surface as a renderable sheet.

        ``RenderApp``'s "Boundary Markers" checkboxes drew exactly this: the
        smoothed surface through the layer's markers, as context under or over
        a neuron.  ``decimate`` takes every Nth grid line -- the fit is 200x200
        and a full-resolution sheet is 79k triangles of a surface with no
        detail at that scale.
        """
        layer = str(layer).upper()
        if layer not in ("INL", "GCL"):
            raise ValueError(f"layer must be INL or GCL, got {layer!r}")
        volume = Volumes.from_str(source)
        key = ("boundary", volume.source, layer, int(decimate))
        with self._lock:
            cached = self._meshes.get(key)
            if cached is not None:
                self._meshes.move_to_end(key)
                return self._mesh_dto(f"{volume.source}-{layer}", cached)

        boundaries = self.boundaries(volume.source)
        surface = boundaries.inl if layer == "INL" else boundaries.gcl
        step = max(int(decimate), 1)
        x = surface.x_points[::step]
        y = surface.y_points[::step]
        z = surface.surface[::step, ::step]
        grid_x, grid_y = np.meshgrid(x, y)
        from ..geometry.cylinder import surf_to_mesh

        mesh = surf_to_mesh(grid_x, grid_y, z).drop_nan()
        self._cache_mesh(key, mesh)
        return self._mesh_dto(f"{volume.source}-{layer}", mesh)

    def vessel_mesh(self, source, *, method: str = "cylinder",
                    smooth_iterations: int = 1,
                    reduce_fraction: float = 0.15) -> MeshDTO:
        """Every blood vessel in a volume, as one mesh.

        ``RenderApp``'s "Blood Vessels" checkbox.  Vessels are ordinary Viking
        structures (type 3), so they render through the same tube builder as a
        neuron; there are only a handful per volume and they are small.

        Decimated hard by default: vasculature is context, not the subject, and
        the full build is ~880k triangles (a 50 MB JSON payload) of smooth
        tube that looks identical at 15%.
        """
        from ..io.structure_group import Vasculature

        volume = Volumes.from_str(source)
        group = Vasculature(volume.source, client=self.client(volume))
        # The vessel list first: through the disk cache that is a read, and it is
        # asked of Viking again after ODataClient.LIST_MAX_AGE, so a vessel added
        # since is found.  The memoized mesh is keyed on the vessels it holds; a
        # key without them served the first build for as long as the process lived
        # and never reached the expiry.
        group.pull()
        key = ("vessels", volume.source, method, int(smooth_iterations),
               round(float(reduce_fraction), 4), tuple(sorted(int(i) for i in group.ids)))
        with self._lock:
            cached = self._meshes.get(key)
            if cached is not None:
                self._meshes.move_to_end(key)
                return self._mesh_dto(f"{volume.source}-vessels", cached)
        if not len(group.ids):
            return self._mesh_dto(f"{volume.source}-vessels", EMPTY_MESH)
        parts = []
        for vessel in group.load_vessels().values():
            try:
                parts.append(cylinder_render(
                    vessel, method=2 if method in ("curvemesh", "tube") else 1,
                    smooth_iterations=smooth_iterations, pathway="adaptive_taubin",
                ))
            except (ValueError, IndexError) as exc:
                # One unrenderable vessel must not cost the rest.
                log.warning("vessel %s failed to render: %s", vessel.id, exc)
        parts = [p for p in parts if not p.is_empty]
        mesh = EMPTY_MESH if not parts else (
            parts[0] if len(parts) == 1 else concatenate(parts))
        if not mesh.is_empty and 0 < reduce_fraction < 1:
            mesh = mesh.decimate(float(reduce_fraction))
        self._cache_mesh(key, mesh)
        return self._mesh_dto(f"{volume.source}-vessels", mesh)

    def closed_curves(self, neuron_id: int, source, *, z_section: int | None = None,
                      spline_points: int | None = None) -> list[ClosedCurveDTO]:
        """Splined closed-curve outlines, e.g. cone pedicle traces.

        Ports what ``sbfsem.image.ConeOutline`` displayed; that class did not
        load at all in MATLAB (F164).
        """
        return _closed_curve_dtos(self.neuron(neuron_id, source), z_section, spline_points)

    def closed_curves_json(self, neuron_id: int, source, *, z_section: int | None = None,
                           spline_points: int | None = None) -> bytes:
        """:meth:`closed_curves` as the JSON text a reply sends, kept.

        The reply was made again for every request, and its cost was the text,
        not the curves: RPC1 c430's 897 outlines are Viking's registered curves,
        drawn as they are and read in 0.01 s, then 7.6 MB of JSON, 0.45 s on a
        Mac (1.5 to 2.2 s a request on production, 2026-10-20, the floor of a
        Registration switch back).  Kept by what decides it, as the disk mesh
        key is: the cell's annotations as Viking's StructureFreshness names
        them, and the moves applied to them (:func:`positions_token`), so a
        copy loaded again at positions already seen is answered from here.  A
        cell with no stamp (a JSON import, a fixture) is made each time:
        nothing tells two of its versions apart.
        """
        neuron = self.neuron(neuron_id, source)
        stamp, count = cell_freshness(neuron)
        if stamp is None:
            return _CLOSED_CURVES_JSON.dump_json(_closed_curve_dtos(neuron, z_section, spline_points))
        key = (neuron.source, int(neuron.id), stamp.isoformat(), count, positions_token(neuron),
               z_section, spline_points)
        with self._single_flight(("closed-curves", *key), "waiting for the same outlines"):
            with self._lock:
                body = self._outline_replies.get(key)
                if body is not None:
                    self._outline_replies.move_to_end(key)
                    return body
            body = _CLOSED_CURVES_JSON.dump_json(_closed_curve_dtos(neuron, z_section, spline_points))
            budget = self.OUTLINE_REPLY_CACHE_MB * 1e6
            with self._lock:
                if len(body) <= budget:
                    self._outline_replies[key] = body
                while sum(len(kept) for kept in self._outline_replies.values()) > budget:
                    self._outline_replies.popitem(last=False)
        return body

    def cone_outline_mesh(self, neuron_id: int, source, *,
                          thickness_um: float | None = None,
                          budget: int = 200) -> MeshDTO:
        """A slab mesh through each closed-curve outline."""
        from ..geometry.cone_outline import cone_outline_mesh, cone_outlines_from_neuron

        neuron = self.neuron(neuron_id, source)
        outlines = cone_outlines_from_neuron(neuron)
        mesh = cone_outline_mesh(outlines, thickness_um=thickness_um, budget=budget)
        return self._mesh_dto(f"c{neuron.id}-outline", mesh)

    # -- analyses ----------------------------------------------------------
    def boundaries(self, source) -> IPLBoundaries:
        """The fitted INL/GCL surfaces for a volume, cached process-wide.

        The fit itself is a sparse solve over 40,000 unknowns that allocates far
        more than the 0.7 MB of surface it produces, so the arenas are released
        once it lands.
        """
        volume = Volumes.from_str(source).source
        cached = load_ipl_boundaries.cache_info().currsize
        result = load_ipl_boundaries(volume)
        if load_ipl_boundaries.cache_info().currsize != cached:
            trim_memory()
        return result

    def stratification(self, neuron_id: int, source, **kwargs) -> StratificationDTO:
        neuron = self.neuron(neuron_id, source)
        result = ipl_depth(neuron, boundaries=self.boundaries(neuron.source), **kwargs)
        return self._stratification_dto(neuron, result, neuron.label)

    def synapse_stratification(
        self, neuron_id: int, source, synapse_type: str, **kwargs
    ) -> StratificationDTO:
        neuron = self.neuron(neuron_id, source)
        result = synapse_stratification(
            neuron, synapse_type, boundaries=self.boundaries(neuron.source), **kwargs
        )
        return self._stratification_dto(neuron, result, synapse_type)

    @staticmethod
    def _stratification_dto(neuron, result, label: str) -> StratificationDTO:
        return StratificationDTO(
            neuron_id=int(neuron.id),
            label=label,
            counts=[float(v) for v in result.counts],
            centers=[float(v) for v in result.centers],
            edges=[float(v) for v in result.edges],
            stats=StatsDTO(**result.stats.as_dict()),
            mode=result.mode,
            n_omitted_nan=result.n_omitted_nan,
            n_omitted_outlier=result.n_omitted_outlier,
        )

    def dendritic_field_hull(self, neuron_id: int, source) -> HullDTO:
        neuron = self.neuron(neuron_id, source)
        hull = dendrite_analysis.dendritic_field_hull(neuron)
        return HullDTO(
            points_um=[[float(x), float(y)] for x, y in hull.points],
            area_um2=hull.area,
            perimeter_um=hull.perimeter,
            equivalent_diameter_um=hull.equivalent_diameter,
        )

    def ie_ratio(self, neuron_id: int, source) -> IERatioDTO:
        return IERatioDTO(**synapse_analysis.ie_ratio(self.neuron(neuron_id, source)).as_dict())

    def morphometrics(self, neuron_id: int, source, *, soma_fraction: float = 0.8,
                      bracket: bool = False) -> MorphometricsDTO:
        """Skeleton length, branching and extent of one cell (see
        :func:`sbfsem.analysis.dendrite.morphometrics`)."""
        neuron = self.neuron(neuron_id, source)
        result = dendrite_analysis.morphometrics(neuron, soma_fraction=soma_fraction,
                                                 bracket=bracket)
        return MorphometricsDTO(
            neuron_id=int(neuron.id),
            source=str(neuron.source),
            label=neuron.label or "",
            soma_fraction=float(soma_fraction),
            **result.as_dict(),
        )

    def annotators(
        self, neuron_id: int, source, *, tolerance_s: float | None = None
    ) -> AnnotatorsDTO:
        """Who annotated this cell, live from Viking (see
        :mod:`sbfsem.analysis.annotators`).

        Asked of Viking rather than of the loaded cell: the loaded node table
        keeps no usernames, and "who is working on this" is a question about
        the record as it stands now, not as it stood when the cell was pulled.
        The cell's child structures are included -- a synapse is annotated by
        whoever marked it, and it is part of the cell's annotation work.
        """
        from ..analysis.annotators import DEFAULT_TOLERANCE_S, annotator_report

        volume = Volumes.from_str(source)
        client = self.client(volume)
        structure_id = int(neuron_id)
        # Past the disk cache, which keeps a query's first answer for good.
        with client.fresh():
            children = client.fetch_child_provenance(structure_id)
            rows = client.fetch_location_provenance(structure_id)
            child_ids = [int(c["ID"]) for c in children if c.get("ID") is not None]
            if child_ids:
                rows += client.fetch_location_provenance_by_parents(child_ids)
            owner = str(client.get_structure(structure_id).get("Username") or "").strip()
        report = annotator_report(
            rows,
            structure_id=structure_id,
            source=volume.source,
            children=children,
            owner=owner or None,
            tolerance_s=DEFAULT_TOLERANCE_S if tolerance_s is None else float(tolerance_s),
            volume_rewrites=self._volume_rewrites(client, structure_id),
        )
        data = report.as_dict()
        data.pop("structure_id")
        return AnnotatorsDTO(neuron_id=structure_id, **data)

    #: Count queries in flight at once when a cell's sessions are checked
    #: against the rest of its volume.
    REWRITE_QUERY_WORKERS = 8

    def _volume_rewrites(self, client, structure_id: int):
        """The rest of the volume's rewrites in each window, asked in parallel
        (see :func:`sbfsem.analysis.annotators._volume_bulk`).  A window the
        volume does not answer is ``None``: its rows keep the cell's own
        verdict rather than failing the whole report."""
        def rewrites(windows):
            def one(window):
                try:
                    return client.count_rewrites(*window)
                except ODataError as exc:
                    log.warning("c%s annotators: rewrite count failed: %s", structure_id, exc)
                    return None
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=self.REWRITE_QUERY_WORKERS) as pool:
                counts = list(pool.map(one, windows))
            log.info("c%s annotators: %d sessions checked against the volume in %.1f s",
                     structure_id, len(windows), time.monotonic() - started)
            return counts
        return rewrites

    def skeleton_check(self, neuron_id: int, source) -> SkeletonCheckDTO:
        """What in the cell's annotation graph will mislead a path length (see
        :mod:`sbfsem.analysis.skeleton_check`)."""
        from ..analysis.skeleton_check import check_skeleton

        result = check_skeleton(self.neuron(neuron_id, source))
        return SkeletonCheckDTO(
            neuron_id=result.neuron_id,
            source=result.source,
            summary=result.summary,
            counts=result.counts(),
            problems=[
                SkeletonProblemDTO(
                    kind=p.kind, location_ids=list(p.location_ids), anchor_id=p.anchor_id,
                    xyz_um=list(p.xyz_um), z_section=p.z_section, length_um=p.length_um,
                    detail=p.detail, fix=p.fix, links=[list(pair) for pair in p.links],
                    extra=p.extra,
                )
                for p in result.problems
            ],
        )

    def shift_summary(self, neuron_id: int, source, pair: str = "correction") -> ShiftSummaryDTO:
        """How far the section correction and the current transforms moved a cell, and
        whether the links across sections agree better afterwards (see
        :mod:`sbfsem.analysis.shifts`).  About the copy of the cell this call is
        about (``requested_copy``).  Raises ``ValueError`` for an unknown ``pair``."""
        from ..analysis.shifts import shift_summary

        return ShiftSummaryDTO(**shift_summary(self.neuron(neuron_id, source), pair).as_dict())

    def swc_archive(self, neuron_ids, source, *, pattern: str = "c{id}",
                    branch_types: bool = False, refresh: bool = False,
                    relax: float | None = None, relax_bound: str = "outline",
                    bracket: bool = False, lamellae: bool = False,
                    sampling: str = "annotations", radius: str = "stored",
                    section_corrections: bool | str | None = None,
                    live_registration: bool | None = None,
                    time_budget_s: float | None = None) -> SwcArchive:
        """One ``.swc`` per structure and a manifest, zipped (see
        :func:`sbfsem.export.bulk.swc_archive`, which says what
        ``time_budget_s`` does to a list that runs past it, and how many
        structures the result holds).

        Every file of one archive is drawn the same way: through the
        volume's current section transforms and Viking's section correction
        as ``live_registration`` and ``section_corrections`` say, the
        service's defaults (a load's) when they do not.  A cell already
        loaded that way is written as loaded; any other is fetched for the
        archive alone -- without its synapses, and without joining the table
        of loaded cells, which every browser shares.  Until 2026-09-25 a
        loaded cell was written as loaded and the others at their stored
        positions, so one archive could hold both.
        """
        from ..export.bulk import swc_archive

        volume = Volumes.from_str(source)
        client = self.client(volume)
        want = (self.default_correction(volume) if section_corrections is None
                else correction_mode(section_corrections))
        live = self.live_registration if live_registration is None else bool(live_registration)

        def load(structure_id: int):
            key = _Key(volume.source, int(structure_id))
            with self._lock:
                loaded = self._copies.get(key, {}).get(copy_tag(want, live))
                usable = loaded is not None and not refresh and self._satisfies(key, want, live)
            if usable:
                return loaded
            bypass = client.fresh() if refresh else contextlib.nullcontext()
            with bypass:
                neuron = NeuronLoader(volume, client=client).load(
                    int(structure_id), include_synapses=False, untagged_psd=self.untagged_psd)
            if live:
                neuron.registration_status = self._register(neuron, force=refresh)
            if want != "off":
                neuron.correction_status = self._correct(neuron, want)
            return neuron

        return swc_archive(neuron_ids, load, volume=volume.source, pattern=pattern,
                           branch_types=branch_types, relax=relax, relax_bound=relax_bound,
                           bracket=bracket, lamellae=lamellae, sampling=sampling, radius=radius,
                           time_budget_s=time_budget_s)

    def tortuosity(self, neuron_id: int, source, location_a: int, location_b: int,
                   dim: int = 2) -> AnalysisResultDTO:
        neuron = self.neuron(neuron_id, source)
        value = dendrite_analysis.tortuosity(neuron, location_a, location_b, dim)
        return AnalysisResultDTO(
            name="tortuosity",
            neuron_id=int(neuron.id),
            values=[value],
            extra={"location_a": int(location_a), "location_b": int(location_b), "dim": dim},
        )

    def dendrite_diameter(
        self, neuron_id: int, source, *, bin_width: float = 5.0
    ) -> DendriteDiameterDTO:
        neuron = self.neuron(neuron_id, source)
        frame = dendrite_analysis.dendrite_diameter_by_soma_distance(
            neuron, bin_width=bin_width
        )
        return DendriteDiameterDTO(
            neuron_id=int(neuron.id),
            bin_width_um=float(bin_width),
            rows=[BinStatsDTO(**row) for row in frame.to_dict(orient="records")],
        )

    def surface_area(self, neuron_id: int, source) -> AnalysisResultDTO:
        neuron = self.neuron(neuron_id, source)
        return AnalysisResultDTO(
            name="surface_area",
            neuron_id=int(neuron.id),
            values=[dendrite_analysis.surface_area(neuron)],
            extra={"units": "um2"},
        )

    def synapse_soma_distance(
        self, neuron_id: int, source, synapse_type: str
    ) -> AnalysisResultDTO:
        neuron = self.neuron(neuron_id, source)
        values = synapse_analysis.synapse_soma_distance(neuron, synapse_type)
        return AnalysisResultDTO(
            name="synapse_soma_distance",
            neuron_id=int(neuron.id),
            values=[float(v) for v in values],
            stats=_stats_dto(values),
            extra={"synapse_type": synapse_type, "units": "um"},
        )

    def open_ended_branches(self, neuron_id: int, source, **kwargs) -> AnalysisResultDTO:
        neuron = self.neuron(neuron_id, source)
        ids = dendrite_analysis.open_ended_branches(neuron, **kwargs)
        return AnalysisResultDTO(
            name="open_ended",
            neuron_id=int(neuron.id),
            values=[float(i) for i in ids],
            extra={"location_ids": [int(i) for i in ids]},
        )

    # -- network -----------------------------------------------------------
    def links(self, neuron_id: int, source, *, refresh: bool = False) -> pd.DataFrame:
        neuron = self.neuron(neuron_id, source)
        if refresh or neuron.links is None or neuron.links.attrs.get("labels_missing"):
            neuron.links = all_linked_neurons(neuron, self.client(neuron.source))
        return neuron.links

    def linked_neurons_table(self, neuron_id: int, source, *, order: str = "neuron",
                             refresh: bool = False) -> pd.DataFrame:
        """The ``getAllLinkedNeurons`` table with synapse positions, ordered."""
        neuron = self.neuron(neuron_id, source)
        return link_report(neuron, self.links(neuron_id, source, refresh=refresh), order)

    def linked_neurons_report(self, neuron_id: int, source, *, order: str = "neuron",
                              refresh: bool = False) -> LinkedNeuronsDTO:
        """Andrea's report: every synapse, its partner and label, with counts."""
        neuron = self.neuron(neuron_id, source)
        table = self.linked_neurons_table(neuron_id, source, order=order, refresh=refresh)
        # A section correction moved the synapse annotations; say by how much,
        # so what is copied for Viking can name Viking's own position.
        moved = neuron.nodes.drop_duplicates("ID").set_index("ID") \
            if "CorrDXnm" in neuron.nodes.columns else None
        rows: list[LinkRowDTO] = []
        for row in table.itertuples(index=False):
            xyz = [float(row.SynapseX_um), float(row.SynapseY_um), float(row.SynapseZ_um)]
            shift = None
            if moved is not None and not pd.isna(row.LocationID) and int(row.LocationID) in moved.index:
                shift = _shift_um(moved.loc[int(row.LocationID)])
            rows.append(LinkRowDTO(
                neuron_id=None if pd.isna(row.NeuronID) else int(row.NeuronID),
                synapse_id=int(row.SynapseID),
                synapse_type=str(row.SynapseType),
                neuron_label=str(row.NeuronLabel or ""),
                direction=str(row.Direction or ""),
                synapse_xyz_um=None if any(np.isnan(v) for v in xyz) else xyz,
                tags=str(row.Tags or ""),
                tag_status=str(row.TagStatus or ""),
                suggested_tag=str(row.SuggestedTag or ""),
                location_id=None if pd.isna(row.LocationID) else int(row.LocationID),
                synapse_shift_um=shift,
            ))
        counts = count_linked_neurons(table)
        partners = [
            LinkPartnerDTO(neuron_id=int(r.NeuronID), neuron_label=str(r.NeuronLabel or ""),
                           synapse_type=str(r.SynapseType), count=int(r.Count))
            for r in counts.itertuples(index=False)
        ]
        linked = [r for r in rows if r.neuron_id is not None]
        return LinkedNeuronsDTO(
            neuron_id=int(neuron.id), source=neuron.source, order=order,
            rows=rows, partners=partners,
            n_synapses=len(rows), n_linked=len(linked), n_unlinked=len(rows) - len(linked),
            n_partners=len({r.neuron_id for r in linked}),
        )

    # -- connectivity (Graffinity) ----------------------------------------
    def _link_graph(self, source, *, refresh: bool = False) -> tuple[LinkGraph, threading.Lock]:
        volume = Volumes.from_str(source)
        with self._lock:
            entry = self._link_graphs.get(volume.source)
            if entry is None or refresh:
                entry = self._link_graphs[volume.source] = (LinkGraph(), threading.Lock())
            return entry

    def link_graph_size(self, source) -> dict[str, int]:
        graph, _ = self._link_graph(source)
        return {"nodes": len(graph.nodes), "edges": len(graph.edges),
                "expanded": len(graph.expanded), "synapses": graph.n_synapses}

    def connectivity_query(
        self, source, *, hops: int = 2, min_hops: int | None = None,
        start_ids: list[int] | None = None, start_pattern: str | None = None,
        node_patterns: list[str] | None = None, edge_patterns: list[str] | None = None,
        max_paths: int = 20_000, max_frontier: int = 300, direction: str = "downstream",
        allow_repeats: bool = False, refresh: bool = False,
    ) -> ConnectivityDTO:
        """Answer a Graffinity query against Viking, fetching links as needed.

        The anchor set is either explicit (``start_ids``, typically the
        loaded cells) or every cell in the volume whose label matches
        ``start_pattern``; the anchors are the starts of a ``downstream``
        query and the ends of an ``upstream`` one, and the pattern for that
        position still applies to them.  Each hop fetches the frontier's links and the labels of the
        neurons they reach in two batched requests, so a two-hop query over a
        few loaded cells costs a handful of round trips however many partners
        they have.  ``refresh`` discards the volume's cached graph first.
        """
        volume = Volumes.from_str(source)
        client = self.client(source)
        graph, lock = self._link_graph(source, refresh=refresh)
        started = time.perf_counter()

        patterns = list(node_patterns or [])
        patterns += [""] * (hops + 1 - len(patterns))
        anchor_position = 0 if direction == "downstream" else hops
        if start_pattern and not (patterns[anchor_position] or "").strip():
            patterns[anchor_position] = start_pattern
        query = PathQuery(
            hops=hops, min_hops=min_hops, node_patterns=patterns,
            edge_patterns=list(edge_patterns or []), start_ids=None,
            max_paths=max_paths, max_frontier=max_frontier, direction=direction,
            simple=not allow_repeats,
        )

        def expand(ids: list[int]) -> None:
            rows = client.fetch_links_by_parent_structures(ids)
            graph.add_edges(neuron_edges_from_structure_links(rows))
            graph.mark_expanded(ids)
            unknown = graph.unlabeled()
            if unknown:
                brief = client.get_structures_brief(unknown)
                for neuron_id in unknown:
                    info = brief.get(neuron_id)
                    graph.set_node(neuron_id, label=info["label"] if info else "",
                                   type_id=info["type_id"] if info else None)

        with lock:
            if start_ids:
                starts = [int(i) for i in dict.fromkeys(start_ids)]
                for neuron_id in starts:
                    graph.node(neuron_id)
                unknown = [n for n in starts if graph.nodes[n].label is None]
                if unknown:
                    brief = client.get_structures_brief(unknown)
                    for neuron_id in unknown:
                        info = brief.get(neuron_id)
                        graph.set_node(neuron_id, label=info["label"] if info else "",
                                       type_id=info["type_id"] if info else None)
                query.start_ids = starts
            else:
                # Every labeled parent structure in the volume, cached on disk
                # by the client; the pattern picks the starts from it.
                cells = client.query_labels()
                for row in cells:
                    if row.get("ID") is None:
                        continue
                    graph.set_node(int(row["ID"]), label=str(row.get("Label") or "").strip(),
                                   type_id=int(row["TypeID"]) if row.get("TypeID") is not None else None)
                query.start_ids = [int(r["ID"]) for r in cells if r.get("ID") is not None]
            search = find_paths(graph, query, expand)

            used_nodes: set[int] = set(search.anchors)
            used_edges: set[int] = set()
            for path in search.paths:
                used_nodes.update(path_nodes(path))
                used_edges.update(path_edges(path))
            edge_order = sorted(used_edges)
            renumber = {old: new for new, old in enumerate(edge_order)}
            paths = [
                [v if i % 2 == 0 else renumber[v] for i, v in enumerate(path)]
                for path in search.paths
            ]
            edges = [
                ConnectivityEdgeDTO(
                    source=e.source, target=e.target, type=e.synapse_type,
                    n_synapses=e.n_synapses, links=[list(pair) for pair in e.links],
                    bidirectional=e.bidirectional,
                )
                for e in (graph.edges[i] for i in edge_order)
            ]
            nodes = {
                n: ConnectivityNodeDTO(
                    id=n, label=graph.label(n), type_id=graph.nodes[n].type_id,
                    expanded=n in graph.expanded,
                )
                for n in sorted(used_nodes)
            }
            matrix = connectivity_matrix(paths)
            table = intermediate_node_table(paths)
            size = {"nodes": len(graph.nodes), "edges": len(graph.edges),
                    "synapses": graph.n_synapses}

        return ConnectivityDTO(
            source=volume.source, hops=query.hops, min_hops=int(query.min_hops or query.hops),
            node_patterns=query.node_patterns, edge_patterns=query.edge_patterns,
            nodes=nodes, edges=edges, paths=paths,
            matrix=ConnectivityMatrixDTO(rows=matrix.rows, cols=matrix.cols, cells=matrix.cells),
            node_table=NodeTableDTO(
                columns=table.columns,
                rows=[NodeTableRowDTO(id=n, cells=table.rows[n]) for n in table.ordered()],
            ),
            direction=direction, allow_repeats=allow_repeats,
            anchors=search.anchors, n_paths=search.n_paths,
            truncated=search.truncated, frontier_capped=search.frontier_capped,
            expanded=search.expanded, unexpanded=search.unexpanded,
            seconds=round(time.perf_counter() - started, 3),
            graph_nodes=size["nodes"], graph_edges=size["edges"], graph_synapses=size["synapses"],
        )

    def network(self, neuron_ids, source) -> NetworkDTO:
        """Connectivity among the given neurons and their partners."""
        rows = []
        labels: dict[int, str] = {}
        for neuron_id in neuron_ids:
            neuron = self.neuron(neuron_id, source)
            # Raw labels: the serializer falls back to "c<id>" for display
            # and keeps the empty string so a viewer can show IDs alone.
            labels[int(neuron.id)] = neuron.label or ""
            table = self.links(neuron_id, source)
            if table is None or table.empty:
                continue
            for row in table.itertuples():
                if row.NeuronID is None or (isinstance(row.NeuronID, float) and np.isnan(row.NeuronID)):
                    continue
                partner = int(row.NeuronID)
                labels.setdefault(partner, row.NeuronLabel or "")
                # The synapse's other end rides along, so a synapse between
                # two loaded cells (listed by each) is one link in the graph.
                other = getattr(row, "PartnerSynapseID", None)
                other = None if other is None or pd.isna(other) else int(other)
                if row.Direction == "pre":
                    rows.append({"SourceID": int(neuron.id), "TargetID": partner,
                                 "SynapseID": int(row.SynapseID), "PartnerSynapseID": other,
                                 "SynapseType": row.SynapseType})
                else:
                    rows.append({"SourceID": partner, "TargetID": int(neuron.id),
                                 "SynapseID": int(row.SynapseID), "PartnerSynapseID": other,
                                 "SynapseType": row.SynapseType})
        table = pd.DataFrame(rows, columns=["SourceID", "TargetID", "SynapseID",
                                            "PartnerSynapseID", "SynapseType"])
        net = network_from_links(table)
        return NetworkDTO(
            elements=cytoscape_elements(net, labels=labels,
                                        loaded=[int(i) for i in neuron_ids]),
            degrees=degree_table(net).to_dict(orient="records"),
            n_nodes=net.graph.number_of_nodes(),
            n_edges=net.graph.number_of_edges(),
        )

    # -- search ------------------------------------------------------------
    def search(self, source, label: str, *, exact: bool = False) -> list[SearchResultDTO]:
        rows = self.client(source).ids_by_label(label, exact_match=exact)
        return [
            SearchResultDTO(
                id=int(r["ID"]),
                label=str(r.get("Label") or "").strip(),
                type_id=int(r["TypeID"]) if r.get("TypeID") is not None else None,
            )
            for r in rows
        ]

    def resolve_structure_spec(self, source, text: str) -> ResolvedSpecDTO:
        """Parse a structure specification and resolve its label terms.

        The grammar is the Viking Data Export portal's — IDs, inclusive
        ranges, and label terms separated by newline, comma or semicolon —
        so the same typed strings and the same posted files work in both
        (see :mod:`sbfsem.io.spec`).  Each label term is resolved against
        this volume's parent-structure labels, case-insensitively, and
        reported separately so the caller can say which terms matched.
        """
        from ..io.spec import parse_structure_spec

        parsed = parse_structure_spec(text)
        client = self.client(source)

        labels: list[LabelResolutionDTO] = []
        merged: set[int] = set(parsed.ids)
        for term in parsed.labels:
            try:
                rows = client.resolve_label(term)
            except ODataError as exc:
                labels.append(LabelResolutionDTO(term=term, error=str(exc)))
                continue
            ids = sorted({int(r["ID"]) for r in rows if r.get("ID") is not None})
            samples: list[str] = []
            for r in rows:
                trimmed = str(r.get("Label") or "").strip()
                if trimmed and trimmed not in samples:
                    samples.append(trimmed)
                if len(samples) == 4:
                    break
            merged.update(ids)
            labels.append(LabelResolutionDTO(term=term, ids=ids, samples=samples))

        return ResolvedSpecDTO(
            ids=sorted(merged),
            numeric_ids=list(parsed.ids),
            labels=labels,
            invalid=[InvalidEntryDTO(text=e.text, reason=e.reason) for e in parsed.invalid],
        )


def _registration_dto(status: dict | None) -> LiveRegistrationDTO | None:
    if not status:
        return None
    fields = LiveRegistrationDTO.model_fields
    return LiveRegistrationDTO(**{k: v for k, v in status.items() if k in fields})


def _manual_dto(status: dict | None) -> ManualMovesDTO | None:
    if not status:
        return None
    fields = ManualMovesDTO.model_fields
    return ManualMovesDTO(**{k: v for k, v in status.items() if k in fields})


def _correction_dto(status: dict | None) -> SectionCorrectionDTO | None:
    if not status:
        return None
    fields = SectionCorrectionDTO.model_fields
    return SectionCorrectionDTO(**{k: v for k, v in status.items() if k in fields})


#: The live move is a computed float, not stored nanometres: a picometre (1e-6 um) is far
#: below anything drawn, and 17 digits each would grow /nodes by 30% (RPC1 c2628, +45 kB gzipped).
_LIVE_SHIFT_DECIMALS = 6


def _shift_um(row, prefix: str = "Corr") -> list[float] | None:
    """A node row's move in um, or ``None`` when it did not move.

    ``prefix`` picks the move: ``"Corr"`` is Viking's section correction
    (``CorrDXnm``/``CorrDYnm``), ``"Live"`` the volume's current section
    transforms (``LiveDXnm``/``LiveDYnm``).  ``row`` is an ``itertuples`` row
    or a ``Series``; either answers ``getattr``.  A missing column, a NaN or
    a zero is no move.
    """
    dx = float(getattr(row, f"{prefix}DXnm", 0.0) or 0.0)
    dy = float(getattr(row, f"{prefix}DYnm", 0.0) or 0.0)
    if not (np.isfinite(dx) and np.isfinite(dy)) or (dx == 0.0 and dy == 0.0):
        return None
    if prefix == "Live":
        return [round(dx / 1000.0, _LIVE_SHIFT_DECIMALS), round(dy / 1000.0, _LIVE_SHIFT_DECIMALS)]
    return [dx / 1000.0, dy / 1000.0]


def _manual_um(frame: pd.DataFrame, volume_scale) -> tuple[list[list[float] | None], list[int | None]]:
    """Each row's manual move as ``[dx, dy, dz]`` um, ``None`` for a row not moved
    by hand (:mod:`sbfsem.core.moves`), and its Z move in sections, ``None`` for
    a row not moved in Z."""
    none: list = [None] * len(frame)
    if "ManDXnm" not in frame.columns:
        return none, list(none)
    zeros = np.zeros(len(frame))
    dx = frame["ManDXnm"].to_numpy(dtype=float) / 1000.0
    dy = frame["ManDYnm"].to_numpy(dtype=float) / 1000.0 if "ManDYnm" in frame.columns else zeros
    dz = frame["ManDZ"].to_numpy(dtype=float) if "ManDZ" in frame.columns else zeros
    thickness = float(volume_scale[2]) / 1000.0 if float(volume_scale[2]) > 1 else float(volume_scale[2])
    moved = np.isfinite(dx) & np.isfinite(dy) & np.isfinite(dz) & ((dx != 0) | (dy != 0) | (dz != 0))
    shifts = np.column_stack([dx, dy, dz * thickness]).tolist()
    sections = dz.astype(int).tolist()
    return ([s if m else None for s, m in zip(shifts, moved.tolist(), strict=True)],
            [int(z) if m and z else None for z, m in zip(sections, moved.tolist(), strict=True)])


def _shifts_um(frame: pd.DataFrame, prefix: str = "Corr") -> list[list[float] | None]:
    """:func:`_shift_um` for every row of ``frame`` at once, in row order.

    The same answer as the row-by-row call (a missing column, a NaN, a
    non-finite value or a zero is ``None``), read from the columns in one pass.
    """
    names = (f"{prefix}DXnm", f"{prefix}DYnm")
    none: list[list[float] | None] = [None] * len(frame)
    if not any(name in frame.columns for name in names):
        return none
    # A column of the pair that is missing is zeros, as the row-by-row call reads it.
    zeros = np.zeros(len(frame))
    dx = frame[names[0]].to_numpy(dtype=float) if names[0] in frame.columns else zeros
    dy = frame[names[1]].to_numpy(dtype=float) if names[1] in frame.columns else zeros
    moved = np.isfinite(dx) & np.isfinite(dy) & ((dx != 0.0) | (dy != 0.0))
    if not moved.any():
        return none
    shifts = np.column_stack([dx, dy]) / 1000.0
    if prefix == "Live":
        shifts = np.round(shifts, _LIVE_SHIFT_DECIMALS)
    shifts = shifts.tolist()
    return [s if m else None for s, m in zip(shifts, moved.tolist(), strict=True)]


#: Bump when a change to how annotations become geometry changes a mesh
#: without changing the annotations: it is part of the on-disk mesh key.
#: 2 (2026-09-11): closed-curve rings placed in the volume frame; earlier
#: meshes drew them in the section mosaic, up to 20 um from the discs.
#: 3 (2026-09-11): Viking's registered ``VolumeShape`` used for the rings,
#: and Hybrid lays no tie wire between disc pieces the traced volume joins.
#: 4 (2026-09-18): closed-curve slices sit at their own section numbers;
#: earlier meshes closed up every skipped section, so a traced body came out
#: short in Z (RPC1 c2628 by 5.46 um, RPC2 c2968 by 54 um).
#: 5 (2026-09-18): outlines joined by a Viking link are interpolated through
#: the sections the link skips; earlier meshes left a slit across the branch.
#: Not bumped (2026-10-15): Closed curve on a cell with no traced outline
#: rasterizes its discs.  It built the empty mesh before, which is never
#: written to disk, so no stored surface can be the old one.
#: 6 (2026-10-16): Closed curve on a cell with traced outlines AND discs
#: rasterizes the discs as well; earlier meshes drew only the outlines (a
#: dendrite traced with discs under a traced soma was missing).
GEOMETRY_VERSION = 6


def _annotation_rows(neuron: Neuron):
    """The rows the cell's annotations are measured from, in the order they
    are measured: the disc annotations (a table), then the closed-curve
    outlines (each a table row and its ring).  One selection for
    :func:`annotation_ground_truth` and :func:`annotation_targets`, so what a
    measurement numbers and what names the numbers cannot drift apart."""
    nodes = neuron.get_cell_nodes()
    discs = None
    if len(nodes) and {"Xum", "Yum", "Zum", "Rum"} <= set(nodes.columns):
        rows = nodes[nodes["TypeCode"] != 6] if "TypeCode" in nodes.columns \
            else nodes
        xyz = rows[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
        r = rows["Rum"].to_numpy(dtype=float)
        ok = np.isfinite(xyz).all(axis=1) & np.isfinite(r) & (r > 0)
        discs = rows[ok]
    curves: list[tuple[Any, np.ndarray]] = []
    geometries = getattr(neuron, "geometries", None)
    if geometries is not None and len(geometries):
        for row in geometries.itertuples():
            found = getattr(row, "Curve", None) or []
            if len(found):
                ring = np.asarray(found[0], dtype=float)
                if ring.ndim == 2 and ring.shape[1] == 2 and len(ring) >= 3:
                    curves.append((row, ring))
    return discs, curves


def annotation_ground_truth(
    neuron: Neuron,
) -> tuple[np.ndarray, np.ndarray, list[tuple[np.ndarray, float]]]:
    """The cell's annotations as measurement targets, in microns.

    Disc annotations become (center, radius) pairs; closed-curve
    annotations (TypeCode 6) have no meaningful radius and contribute
    their traced outlines instead.
    """
    discs, curves = _annotation_rows(neuron)
    centers = np.zeros((0, 3))
    radii = np.zeros(0)
    if discs is not None:
        centers = discs[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
        radii = discs["Rum"].to_numpy(dtype=float)
    # The loader already places closed-curve rings in the volume frame the
    # disc centres use (``sbfsem.io.wkt.volume_frame_shift_um``), so the
    # mesh and this ground truth see the same outlines.
    return centers, radii, [(ring, float(row.Zum)) for row, ring in curves]


class AnnotationTargets(NamedTuple):
    """Who the annotations of :func:`annotation_ground_truth` are, entry for
    entry: Location IDs (numbered per volume), section numbers, and the kind
    (0 a disc, 1 a closed-curve outline) -- the discs first, then the outlines,
    which is the numbering a measurement's ``owner`` uses."""

    ids: np.ndarray
    sections: np.ndarray
    kind: np.ndarray


def annotation_targets(neuron: Neuron) -> AnnotationTargets:
    """Location IDs, sections and kind of the cell's measured annotations, in
    the order of :func:`annotation_ground_truth`."""
    discs, curves = _annotation_rows(neuron)
    # Hand-built tables and older exports may lack ID or Z: the measurement does not need
    # them, so a cell without them is measured with no names, never failed after the fact.
    ids: list = [] if discs is None or "ID" not in discs else discs["ID"].to_list()
    sections: list = [] if discs is None or "Z" not in discs else discs["Z"].to_list()
    kind = [0] * len(ids)
    for row, _ring in curves:
        ids.append(getattr(row, "ID", 0))
        sections.append(getattr(row, "Z", 0))
        kind.append(1)
    return AnnotationTargets(np.asarray(ids, dtype=np.int64), np.asarray(sections, dtype=np.int64),
                             np.asarray(kind, dtype=np.int8))


#: A list of outlines as JSON, by pydantic's own encoder: the bytes the reply's
#: ``json.dumps`` made from the same numbers (c430 and c1178 compared), at a
#: seventh of the time.
_CLOSED_CURVES_JSON: TypeAdapter[list[ClosedCurveDTO]] = TypeAdapter(list[ClosedCurveDTO])


def _closed_curve_dtos(neuron, z_section: int | None,
                       spline_points: int | None) -> list[ClosedCurveDTO]:
    """The cell's closed-curve outlines (one section's with ``z_section``), each
    splined once.  The numbers are made here as the fields' own types, so the
    models are built without being checked again."""
    from ..geometry.cone_outline import cone_outlines_from_neuron

    out = []
    for outline in cone_outlines_from_neuron(neuron, z_section=z_section):
        # The outline and its cut-outs are splined alike (``rings``); the
        # cut-outs used to go out as raw control points beside a splined
        # outline.  A registered curve is sent as it is, both ways.
        rings = outline.rings(spline_points)
        area, diameter, centroid = outline.measures(rings if spline_points is None else None)
        curve, *cutouts = rings
        out.append(
            ClosedCurveDTO.model_construct(
                # The annotation's own ID; this used to repeat the parent
                # structure's, so every outline of a cell shared one "ID".
                location_id=int(outline.location_id or 0),
                parent_id=int(outline.parent_id or 0),
                z_section=int(outline.z_section),
                z_um=float(outline.z_um),
                outline_um=np.asarray(curve, dtype=float).tolist(),
                cutouts_um=[np.asarray(cut, dtype=float).tolist() for cut in cutouts],
                area_um2=area,
                equivalent_diameter_um=diameter,
                centroid_um=np.asarray(centroid, dtype=float).tolist(),
            )
        )
    return out


def cell_freshness(neuron) -> tuple[datetime | None, int]:
    """The cell's own annotations as Viking's ``StructureFreshness`` (depth 0)
    names them: the newest of its structure row and its Locations, in UTC, and
    how many Locations it has.  ``(None, n)`` for data with no stamp (a JSON
    import, a fixture), which are never compared."""
    count = int(len(neuron.get_cell_nodes())) if hasattr(neuron, "get_cell_nodes") \
        else int(len(neuron.nodes))
    newest = odata_utc(getattr(neuron, "data_stamp", None))
    if newest is None:
        return None, count
    row = odata_utc((getattr(neuron, "viking", None) or {}).get("LastModified"))
    return (max(newest, row) if row is not None else newest), count


#: Closer than this, two Viking timestamps are one edit.  The service keeps
#: milliseconds (SQL ``datetime`` ticks at 3.33 ms) and both sides read the
#: same column.
_SAME_EDIT = timedelta(milliseconds=4)


def _later(live: datetime, loaded: datetime) -> bool:
    return live - loaded > _SAME_EDIT


def _compare_freshness(neuron, own, family) -> FreshnessDTO:
    """One cell's loaded copy against Viking's ``(LastModified, count)`` answers."""
    loaded, loaded_count = cell_freshness(neuron)
    if loaded is None or own is None:
        # Unstamped data is never stale; a structure Viking no longer knows has
        # nothing newer to offer.
        return FreshnessDTO(loaded=loaded, live=own[0] if own else None,
                            loaded_count=loaded_count, live_count=own[1] if own else None)
    surface = _later(own[0], loaded) or own[1] != loaded_count
    # The children by count only: added or removed synapse annotations.  Their
    # newest edit cannot be compared -- Viking's depth-1 date also covers the
    # child structure rows, which are not fetched, and the rows of deleted
    # synapses Viking keeps with no annotations; Temporal c108, just
    # fetched, read 28 min "newer" than its own annotations (2026-10-15).
    children = bool(family is not None and getattr(neuron, "children_loaded", False)
                    and family[1] != len(neuron.nodes))
    live = own[0]
    return FreshnessDTO(
        loaded=loaded, live=live, stale=surface or children,
        loaded_count=loaded_count, live_count=own[1],
        surface_stale=surface, children_stale=children and not surface,
    )


def _location_links(neuron: Neuron) -> list[tuple[int, int]]:
    """Viking's location links as ``(A, B)`` pairs; none when there are none."""
    edges = getattr(neuron, "edges", None)
    if edges is None or not len(edges) or not {"A", "B"} <= set(edges.columns):
        return []
    return list(zip(edges["A"].astype(int), edges["B"].astype(int), strict=True))


def _curve_outlines(neuron: Neuron, *, discs: bool = False, avg_discs: float = 0.0,
                    avg_paths: float = 0.0, pixel_size_um: float = 0.05,
                    curve_um: float = 0.0, bridge_um: float = BRIDGE_UM,
                    link_bridge_um: float = LINK_BRIDGE_UM) -> tuple[list, dict]:
    """The outlines a cell's closed-curve volume is rasterized from, and the
    keywords :func:`~sbfsem.geometry.closed_curve.build_curve_volume` takes
    them with.

    The traced outlines and Viking's links.  With ``discs``, the cell's discs
    stand in as outlines too
    (:func:`~sbfsem.geometry.closed_curve.annotations_from_discs`): each
    disc's circle, and the links drawn through the sections they cross.

    * A cell with no traced outline is rasterized from its discs alone, the
      links already drawn through the sections they cross: nothing is left to
      interpolate, and a gap no link crosses stays open -- a disc-traced
      cell's links are its annotator's own word on what continues where
      (Inferior c16137, 2026-10-15: "Closed curve needs traced outlines"
      until then).
    * A cell with both is the two together (Inferior c16137 again, 2026-10-16:
      four traced outlines were added to its 1,931 discs, and Closed curve
      drew only those four, a patch 14 um wide).  The traced outlines keep
      what they had: Viking's links between them, and the interpolation across
      skipped sections.  Links that join a disc to an outline are followed
      too; links between discs are already drawn as runs.

    ``avg_discs`` and ``avg_paths`` (0: as traced) average the discs, as a
    multiple of their radius, and the traced outlines, in microns, along their
    chains of links before anything is drawn
    (:mod:`~sbfsem.geometry.curve_average`); the outlines are resampled to a
    point every half pixel at most, so the raster loses nothing of them.

    ``curve_um`` (0: straight) curves the runs of disc links at least that
    long (:mod:`~sbfsem.geometry.curve_links`).  ``bridge_um`` is the widest
    skip between traced sections that is interpolated, and ``link_bridge_um``
    the longest a link is followed across skipped sections, by a disc's run
    or a traced outline's interpolation; at their defaults the keywords are
    left out, so the build is the one they were constants of.  A cell traced
    with discs alone has nothing to interpolate whole sections between: its
    links are drawn as runs, so ``bridge_um`` does not reach it.
    """
    annotations = annotations_from_geometries(getattr(neuron, "geometries", None))
    links = _location_links(neuron)
    if avg_paths > 0 and annotations:
        annotations = average_outlines(annotations, links, avg_paths,
                                       spacing_um=max(0.5 * float(pixel_size_um), 0.02))
    bridges: dict = {}
    if float(bridge_um) != BRIDGE_UM:
        bridges["bridge_um"] = float(bridge_um)
    if float(link_bridge_um) != LINK_BRIDGE_UM:
        bridges["link_bridge_um"] = float(link_bridge_um)
    if not discs:
        return annotations, {"links": links, **bridges}
    stand_ins = annotations_from_discs(neuron.get_cell_nodes(), links,
                                       volume_scale=neuron.volume_scale, average=avg_discs,
                                       link_bridge_um=float(link_bridge_um),
                                       curve_um=float(curve_um))
    if not annotations:
        # The links are drawn as runs already (``link_bridge_um`` went to them).
        return stand_ins, {"links": None, "bridge_um": 0.0}
    if not stand_ins:
        return annotations, {"links": links, **bridges}
    traced = {a.location_id for a in annotations if a.location_id}
    return (annotations + stand_ins,
            {"links": [(a, b) for a, b in links if a in traced or b in traced], **bridges})


def _disc_subset(neuron: Neuron) -> Neuron | None:
    """A view of the neuron holding only its disc (TypeCode 1) annotations.

    ``None`` when there are no discs at all; the neuron itself when there is
    nothing else, so the common all-disc cell pays for no copy.  The copy is
    shallow with its own node/edge frames and a fresh ``analysis`` dict --
    sharing that cache would let a disc-only segmentation poison the full
    neuron's, or the reverse.
    """
    nodes = neuron.nodes
    if nodes.empty or "TypeCode" not in nodes.columns:
        return neuron
    keep = nodes["TypeCode"] == 1
    if bool(keep.all()):
        return neuron
    if not bool(keep.any()):
        return None
    sub = copy.copy(neuron)
    sub.nodes = nodes[keep].reset_index(drop=True)
    ids = set(sub.nodes["ID"].astype(int).tolist())
    edges = neuron.edges
    if edges is not None and not edges.empty:
        sub.edges = edges[edges["A"].isin(ids) & edges["B"].isin(ids)].reset_index(drop=True)
    sub.analysis = {}
    sub.model = None
    return sub


def _averaged_discs(neuron: Neuron, gain: float) -> Neuron:
    """A view of the neuron whose discs are averaged along their chains of
    links (:func:`~sbfsem.geometry.curve_average.average_disc_nodes`, the
    same averaging Closed curve's Disc avg gives its circles), the rest of its
    annotations as they are.

    Why the disc pathways want it: a tube is the union of round cones from
    each annotated disc to the next, so it takes the outer envelope of every
    wobble of an annotator's centers and radii.  A straight process drawn
    with centers 0.1 um off and radii 15 % off comes out 1.4 to 1.9 times
    its own cross-section (a synthetic one, at 0.3 to 1.5 um); Inferior
    c1178's thin discs come out a median 1.2 times their own area under
    Voxel remesh.  Averaging first puts the tube through the run of discs,
    not around its outliers.  The copy is shallow with its own node frame and
    a fresh ``analysis`` dict, as :func:`_disc_subset`'s.
    """
    nodes = neuron.nodes
    if gain <= 0 or nodes is None or nodes.empty or "TypeCode" not in nodes.columns:
        return neuron
    is_disc = (nodes["TypeCode"] == 1) & (nodes["ParentID"] == neuron.id) \
        if "ParentID" in nodes.columns else nodes["TypeCode"] == 1
    if not bool(is_disc.any()):
        return neuron
    discs = nodes[is_disc]
    averaged = average_disc_nodes(discs, _location_links(neuron), float(gain))
    if averaged is discs or averaged is None:
        return neuron
    out = nodes.copy()
    out.loc[is_disc, ["Xum", "Yum", "Rum"]] = averaged[["Xum", "Yum", "Rum"]].to_numpy()
    sub = copy.copy(neuron)
    sub.nodes = out
    sub.analysis = {}
    sub.model = None
    return sub


def _stats_dto(values) -> StatsDTO | None:
    from ..analysis.stats import describe

    array = np.asarray(values, dtype=float).ravel()
    if array.size == 0:
        return None
    stats: Stats = describe(array)
    return StatsDTO(**stats.as_dict())
