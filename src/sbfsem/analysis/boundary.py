"""IPL boundary surfaces and depth conversion.

Ports ``+sbfsem/+core/BoundaryMarker.m``, ``+sbfsem/+builtin/INLBoundary.m``,
``+sbfsem/+builtin/GCLBoundary.m``, ``util/micron2ipl.m`` and
``util/renders/getStrataCData.m``.

**The single most consequential fix in the port.**  ``util/micron2ipl.m:29``
built the INL interpolation grid as ``meshgrid(INL.newYPts, INL.newYPts)`` — the
Y vector on both axes.  ``micron2ipl`` is the only entry point converting a
micron Z coordinate into an IPL depth fraction, and its output flows into
``iplDepth``, ``getSynapseStratification``, ``singleDendriteStratification``,
``clipMeshByStratification``, ``getStrataCData``, ``RenderApp``'s stratification
histogram and ``IPLDepthApp``.  Four correct near-duplicates of the same three
lines exist elsewhere in the MATLAB repository, which is what establishes it as a
typo rather than intent.  Any IPL depth, stratification profile or ON/OFF
classification produced by the MATLAB toolbox through this path should be
recomputed.  See F214 and DEVIATIONS.md.

The second, smaller defect on the same line is ``+ eps`` in the denominator,
which turned a zero-thickness IPL into a very large finite number instead of
NaN.  A zero denominator now yields NaN.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from ..core.registry import read_boundary_markers
from ..core.transforms import viking2micron
from ..core.volumes import Volumes, validate_source
from .regularize import regularize_data_3d

__all__ = ["BoundarySurface", "IPLBoundaries", "load_boundary", "load_ipl_boundaries"]

log = logging.getLogger(__name__)

#: ``BoundaryMarker.SMOOTHFAC``.
SMOOTH_FACTOR = 0.005
#: ``INLBoundary``/``GCLBoundary`` call ``doAnalysis(200)``.
DEFAULT_GRID_POINTS = 200

#: Viking type IDs for the two boundary marker layers.
TYPE_IDS = {"INL": 224, "GCL": 235}


@dataclass
class BoundarySurface:
    """A fitted IPL boundary surface in microns."""

    layer: str
    source: str
    marker_locations: np.ndarray  # (N, 3) in Viking pixels
    x_points: np.ndarray
    y_points: np.ndarray
    surface: np.ndarray  # (len(y_points), len(x_points))

    @functools.cached_property
    def _interpolator(self) -> RegularGridInterpolator:
        return RegularGridInterpolator(
            (self.y_points, self.x_points),
            self.surface,
            bounds_error=False,
            fill_value=np.nan,
        )

    def xy_eval(self, x, y) -> np.ndarray:
        """Surface height at arbitrary XY, in microns.  Ports ``xyEval``."""
        x = np.atleast_1d(np.asarray(x, dtype=float))
        y = np.atleast_1d(np.asarray(y, dtype=float))
        return self._interpolator(np.column_stack([y.ravel(), x.ravel()]))


def _fit_surface(
    layer: str,
    source: str,
    markers: np.ndarray,
    n_points: int = DEFAULT_GRID_POINTS,
    smoothness: float = SMOOTH_FACTOR,
    units: str = "microns",
) -> BoundarySurface:
    markers = np.asarray(markers, dtype=float).reshape(-1, 3)
    if len(markers) < 3:
        raise ValueError(f"{layer} boundary needs at least 3 markers, got {len(markers)}")
    xyz = viking2micron(markers, source) if units == "microns" else markers

    # F012: MATLAB's [min(...); max(...)] collapsed to a scalar for one marker.
    lo = np.floor(xyz.min(axis=0))
    hi = np.ceil(xyz.max(axis=0))
    x_points = np.linspace(lo[0], hi[0], n_points)
    y_points = np.linspace(lo[1], hi[1], n_points)

    surface, _, _ = regularize_data_3d(
        xyz[:, 0], xyz[:, 1], xyz[:, 2], x_points, y_points,
        interp="bicubic", smoothness=smoothness,
    )
    return BoundarySurface(layer, validate_source(source), markers, x_points, y_points, surface)


@functools.lru_cache(maxsize=16)
def load_boundary(
    source: str, layer: str, n_points: int = DEFAULT_GRID_POINTS
) -> BoundarySurface:
    """Fit a boundary surface from the cached markers shipped with the package."""
    layer = layer.upper()
    canonical = validate_source(source)
    markers = read_boundary_markers(canonical, layer)
    if markers is None:
        raise FileNotFoundError(
            f"No cached {layer} boundary markers for {canonical}. "
            "Query Viking with ODataClient.structures_by_type() and cache them."
        )
    return _fit_surface(layer, canonical, markers, n_points)


def fetch_boundary(client, layer: str, n_points: int = DEFAULT_GRID_POINTS) -> BoundarySurface:
    """Query Viking for boundary markers and fit the surface.

    Ports ``BoundaryMarker.pull``.  The MATLAB version issued one request per
    marker structure; this batches them.
    """
    layer = layer.upper()
    structures = client.structures_by_type(TYPE_IDS[layer])
    ids = [int(row["ID"]) for row in structures]
    if not ids:
        raise ValueError(f"No {layer} boundary markers found in {client.source}")
    rows = client.fetch_locations_by_parents(ids)
    markers = np.array([[r["X"], r["Y"], r["Z"]] for r in rows], dtype=float)
    return _fit_surface(layer, client.source, markers, n_points)


@dataclass
class IPLBoundaries:
    """The INL and GCL surfaces together, and the depth conversion between them."""

    inl: BoundarySurface
    gcl: BoundarySurface

    @property
    def source(self) -> str:
        return self.inl.source

    def micron2ipl(self, xyz) -> np.ndarray:
        """IPL depth fraction for each XYZ point, in microns.

        0 is the INL/IPL border, 1 the IPL/GCL border.  Points outside the
        fitted surfaces, or where the two surfaces coincide, come back NaN.
        """
        pts = np.asarray(xyz, dtype=float).reshape(-1, 3)
        v_gcl = self.gcl.xy_eval(pts[:, 0], pts[:, 1])
        # F214: the MATLAB line evaluated the INL surface on meshgrid(Y, Y).
        v_inl = self.inl.xy_eval(pts[:, 0], pts[:, 1])
        denominator = v_gcl - v_inl
        with np.errstate(divide="ignore", invalid="ignore"):
            depth = (pts[:, 2] - v_inl) / denominator
        # F214 (second part): MATLAB added eps, turning a zero-thickness IPL into
        # a huge finite number rather than NaN.
        return np.where(denominator == 0, np.nan, depth)

    def strata_cdata(self, vertices, fill_nan: bool = True) -> np.ndarray:
        """Per-vertex IPL depth for coloring a render.  Ports ``getStrataCData``."""
        depth = self.micron2ipl(vertices)
        if fill_nan and np.isnan(depth).any():
            count = int(np.isnan(depth).sum())
            log.info("IPL depth found %u NaNs", count)
            finite = depth[~np.isnan(depth)]
            depth = np.where(np.isnan(depth), finite.mean() if finite.size else 0.0, depth)
        return depth


@functools.lru_cache(maxsize=8)
def load_ipl_boundaries(source: str, n_points: int = DEFAULT_GRID_POINTS) -> IPLBoundaries:
    """Load and fit both cached boundary surfaces for a volume."""
    volume = Volumes.from_str(source)
    if not volume.has_boundary:
        raise ValueError(f"{volume.source} has no IPL boundary markers")
    return IPLBoundaries(
        inl=load_boundary(volume.source, "INL", n_points),
        gcl=load_boundary(volume.source, "GCL", n_points),
    )
