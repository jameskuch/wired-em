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

**Two ways to measure the depth** (``method``, 2026-10-22; DEVIATIONS.md, an
addition).  ``"column"``, the default and MATLAB's, measures straight along Z:
the point's height above the INL surface at its own X/Y over the IPL's height
there.  ``"normal"`` measures along each surface's own normal: the distance to
the nearest point of the INL surface and of the GCL surface, so the fraction is
``d_inl / (d_inl + d_gcl)``, the depth from the INL is ``d_inl`` µm and the IPL
is ``d_inl + d_gcl`` µm thick through the point.  Neither assumes a layer flat;
the column assumes Z is the layers' normal.  Measured on the five volumes with
markers, the fraction moves about 1 % of the IPL or less for 95 % of points, but
a thickness along Z reads 1/cos(tilt) too thick (2 % at 12°, 22 % at RC2's
steepest 35°).  Both read the same fitted surfaces at the same heights: only the
direction differs.

**A flattened depth axis** (:meth:`IPLBoundaries.flatten`, 2026-10-22, an
addition): each point's Z replaced by its fraction times one stated thickness,
the volume's median IPL thickness along the normals by default, so a cell can be
drawn with its layers flat at 1:1.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import RectBivariateSpline, RegularGridInterpolator

from ..core.registry import read_boundary_markers
from ..core.transforms import viking2micron
from ..core.volumes import Volumes, validate_source
from .regularize import regularize_data_3d

__all__ = ["BoundarySurface", "IPLBoundaries", "IPLDepth", "IPL_METHODS", "ipl_method",
           "load_boundary", "load_ipl_boundaries", "thickness_summary"]

log = logging.getLogger(__name__)

#: ``BoundaryMarker.SMOOTHFAC``.
SMOOTH_FACTOR = 0.005
#: ``INLBoundary``/``GCLBoundary`` call ``doAnalysis(200)``.
DEFAULT_GRID_POINTS = 200

#: Viking type IDs for the two boundary marker layers.
TYPE_IDS = {"INL": 224, "GCL": 235}

#: How an IPL depth is measured: ``column`` straight along Z (MATLAB's, the
#: library's default), ``normal`` along each surface's normal.
IPL_METHODS = ("column", "normal")

#: The nearest-point solve stops when a step moves the foot less than this, in
#: µm; a distance is stationary at its foot, so it is off by far less.
_FOOT_TOLERANCE_UM = 1e-4
#: A point whose nearest-point solve has not settled after this many steps gets
#: NaN (none of the five volumes' surfaces needs more than a handful).
_FOOT_MAX_STEPS = 40
#: Points per batch of the nearest-point solve: its temporaries stay near 50 MB.
_FOOT_BATCH = 250_000


def ipl_method(method: str) -> str:
    """``method`` checked against :data:`IPL_METHODS`."""
    if method not in IPL_METHODS:
        raise ValueError(f"IPL depth method {method!r}: use one of {', '.join(IPL_METHODS)}")
    return method


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

    @functools.cached_property
    def _derivatives(self) -> dict[str, np.ndarray]:
        """The surface's slopes and curvatures at its grid nodes, each shaped
        like ``surface`` (rows are Y): ``fx`` = dz/dx, ``fy`` = dz/dy, ``fxx``,
        ``fxy``, ``fyy``, from the bicubic spline through the nodes, so they are
        smooth where the grid's own differences would step."""
        # RectBivariateSpline's first axis is the grid's rows, which are Y: its
        # ``dx`` is d/dy here and its ``dy`` d/dx.
        spline = RectBivariateSpline(self.y_points, self.x_points, self.surface)
        rows_y, cols_x = np.meshgrid(self.y_points, self.x_points, indexing="ij")
        return {
            "fx": spline.ev(rows_y, cols_x, dy=1),
            "fy": spline.ev(rows_y, cols_x, dx=1),
            "fxx": spline.ev(rows_y, cols_x, dy=2),
            "fxy": spline.ev(rows_y, cols_x, dx=1, dy=1),
            "fyy": spline.ev(rows_y, cols_x, dx=2),
        }

    def _between_nodes(self, x, y, names: tuple[str, ...]) -> list[np.ndarray]:
        """The named grids (``h``, the height, or a :attr:`_derivatives` key) at
        XY, each interpolated bilinearly between the four nodes around it: the
        height exactly as :meth:`xy_eval` reads it.  ``x`` and ``y`` must lie in
        the fitted rectangle."""
        grids = self._derivatives
        x0, y0 = self.x_points[0], self.y_points[0]
        step_x = self.x_points[1] - self.x_points[0]
        step_y = self.y_points[1] - self.y_points[0]
        n_rows, n_cols = self.surface.shape
        gx = (x - x0) / step_x
        gy = (y - y0) / step_y
        col = np.clip(np.floor(gx).astype(np.intp), 0, n_cols - 2)
        row = np.clip(np.floor(gy).astype(np.intp), 0, n_rows - 2)
        tx = gx - col
        ty = gy - row
        flat = row * n_cols + col
        out = []
        for name in names:
            g = (self.surface if name == "h" else grids[name]).ravel()
            low = g[flat] * (1 - tx) + g[flat + 1] * tx
            high = g[flat + n_cols] * (1 - tx) + g[flat + n_cols + 1] * tx
            out.append(low * (1 - ty) + high * ty)
        return out

    def height_and_slopes(self, x, y) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Height (as :meth:`xy_eval`), dz/dx and dz/dy at XY inside the fitted
        rectangle.  ``x`` and ``y`` must lie inside it."""
        h, fx, fy = self._between_nodes(x, y, ("h", "fx", "fy"))
        return h, fx, fy

    def nearest(self, xyz) -> tuple[np.ndarray, np.ndarray]:
        """Each point's nearest point on the surface, and its distance there.

        Returns ``(foot, distance)``: ``foot`` (N, 3) in µm, and ``distance``
        signed, positive on the surface's +Z side, measured along the surface's
        normal at the foot.  NaN where the nearest point would lie beyond the
        fitted rectangle, where the solve does not settle, or for a NaN point.

        A Newton solve for the foot ``(u, v, f(u, v))`` where the point's offset
        is parallel to the normal, started below the point: exact in one step on
        a plane.  Where the surface curves more tightly than the point is far
        from it (the Newton matrix not positive definite) a step is Gauss-Newton's.
        """
        pts = np.asarray(xyz, dtype=float).reshape(-1, 3)
        foot = np.full_like(pts, np.nan)
        distance = np.full(len(pts), np.nan)
        for start in range(0, len(pts), _FOOT_BATCH):
            part = slice(start, start + _FOOT_BATCH)
            foot[part], distance[part] = self._nearest_batch(pts[part])
        return foot, distance

    def _nearest_batch(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x_lo, x_hi = float(self.x_points[0]), float(self.x_points[-1])
        y_lo, y_hi = float(self.y_points[0]), float(self.y_points[-1])
        n = len(pts)
        ok = np.isfinite(pts).all(axis=1)
        u = np.clip(np.where(ok, pts[:, 0], x_lo), x_lo, x_hi)
        v = np.clip(np.where(ok, pts[:, 1], y_lo), y_lo, y_hi)
        h = np.full(n, np.nan)
        fx = np.full(n, np.nan)
        fy = np.full(n, np.nan)
        active = np.flatnonzero(ok)
        good = np.zeros(n, dtype=bool)
        for _ in range(_FOOT_MAX_STEPS):
            if active.size == 0:
                break
            ua, va = u[active], v[active]
            ha, fxa, fya, fxx, fxy, fyy = self._between_nodes(
                ua, va, ("h", "fx", "fy", "fxx", "fxy", "fyy"))
            h[active], fx[active], fy[active] = ha, fxa, fya
            p = pts[active]
            rx, ry, rz = p[:, 0] - ua, p[:, 1] - va, p[:, 2] - ha
            # The residual against the surface's tangents (1, 0, fx) and (0, 1, fy),
            # and the Hessian of half the squared distance: the tangents' Gram
            # matrix less the residual times the surface's curvature.
            a = rx + fxa * rz
            b = ry + fya * rz
            g11, g12, g22 = 1.0 + fxa * fxa, fxa * fya, 1.0 + fya * fya
            h11, h12, h22 = g11 - rz * fxx, g12 - rz * fxy, g22 - rz * fyy
            convex = (h11 > 0) & (h11 * h22 - h12 * h12 > 0)
            h11 = np.where(convex, h11, g11)
            h12 = np.where(convex, h12, g12)
            h22 = np.where(convex, h22, g22)
            det = h11 * h22 - h12 * h12
            du = (h22 * a - h12 * b) / det
            dv = (h11 * b - h12 * a) / det
            new_u = np.clip(ua + du, x_lo, x_hi)
            new_v = np.clip(va + dv, y_lo, y_hi)
            done = np.hypot(du, dv) < _FOOT_TOLERANCE_UM
            # A foot held at the rectangle's edge: the nearest point lies beyond the fit.
            beyond = (new_u != ua + du) | (new_v != va + dv)
            stuck = ~done & beyond & (np.hypot(new_u - ua, new_v - va) < _FOOT_TOLERANCE_UM)
            # A settled foot keeps the place its height and slopes were read at.
            moving = ~done & ~stuck
            u[active[moving]], v[active[moving]] = new_u[moving], new_v[moving]
            good[active[done]] = True
            active = active[moving]
        if active.size:
            log.info("%s nearest point: %u of %u points did not settle", self.layer, active.size, n)
        foot = np.column_stack([u, v, h])
        foot[~good] = np.nan
        offset = pts - foot
        norm = np.sqrt(1.0 + fx * fx + fy * fy)
        # Along the unit normal (-fx, -fy, 1) / norm, which points to +Z.
        distance = (-fx * offset[:, 0] - fy * offset[:, 1] + offset[:, 2]) / norm
        distance[~good] = np.nan
        return foot, distance

    def tilt_deg(self, x, y) -> np.ndarray:
        """The surface's angle from the XY plane at XY, in degrees (NaN outside)."""
        x = np.atleast_1d(np.asarray(x, dtype=float)).ravel()
        y = np.atleast_1d(np.asarray(y, dtype=float)).ravel()
        inside = (x >= self.x_points[0]) & (x <= self.x_points[-1]) \
            & (y >= self.y_points[0]) & (y <= self.y_points[-1])
        _, fx, fy = self.height_and_slopes(np.where(inside, x, self.x_points[0]),
                                           np.where(inside, y, self.y_points[0]))
        return np.where(inside, np.degrees(np.arctan(np.hypot(fx, fy))), np.nan)

    def marker_xyz_um(self) -> np.ndarray:
        """The markers in microns, as the surface was fitted to them."""
        return viking2micron(self.marker_locations, self.source)


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


@dataclass(frozen=True)
class IPLDepth:
    """Each point's place in the IPL, measured one way (:data:`IPL_METHODS`).

    ``fraction`` is 0 at the INL/IPL border and 1 at the IPL/GCL border, below 0
    or above 1 outside the IPL; ``depth_um`` is the distance from the INL border
    toward the GCL, in µm (negative on the INL's far side), and ``thickness_um``
    the IPL's thickness through the point, so ``fraction = depth_um /
    thickness_um``.  Along a ``column`` both are measured along Z; along the
    ``normal`` the depth is the distance to the INL surface and the thickness
    the sum of the distances to both.  NaN where the point has no depth.
    """

    method: str
    fraction: np.ndarray
    depth_um: np.ndarray
    thickness_um: np.ndarray


@dataclass
class IPLBoundaries:
    """The INL and GCL surfaces together, and the depth conversion between them."""

    inl: BoundarySurface
    gcl: BoundarySurface

    @property
    def source(self) -> str:
        return self.inl.source

    @functools.cached_property
    def gcl_side(self) -> float:
        """+1 when the GCL lies on the INL's +Z side, -1 when below it: the sign
        of the two surfaces' mean separation where both are fitted."""
        x_lo = max(self.inl.x_points[0], self.gcl.x_points[0])
        x_hi = min(self.inl.x_points[-1], self.gcl.x_points[-1])
        y_lo = max(self.inl.y_points[0], self.gcl.y_points[0])
        y_hi = min(self.inl.y_points[-1], self.gcl.y_points[-1])
        if x_lo >= x_hi or y_lo >= y_hi:
            raise ValueError(f"{self.source}: the INL and GCL surfaces do not overlap")
        x, y = np.meshgrid(np.linspace(x_lo, x_hi, 50), np.linspace(y_lo, y_hi, 50))
        gap = np.nanmean(self.gcl.xy_eval(x, y) - self.inl.xy_eval(x, y))
        return 1.0 if gap >= 0 else -1.0

    def micron2ipl(self, xyz, method: str = "column") -> np.ndarray:
        """IPL depth fraction for each XYZ point, in microns.

        0 is the INL/IPL border, 1 the IPL/GCL border.  Points outside the
        fitted surfaces, or where the two surfaces coincide, come back NaN.
        ``method`` is ``"column"`` (MATLAB's, straight along Z) or ``"normal"``
        (along each surface's normal): see :meth:`depth`.
        """
        if ipl_method(method) == "normal":
            return self.depth(xyz, method).fraction
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

    def depth(self, xyz, method: str = "column") -> IPLDepth:
        """The fraction, the depth from the INL in µm and the IPL's thickness in
        µm through each XYZ point (microns), measured by ``method``.

        ``"column"``: along Z, between the two surfaces' heights at the point's
        own X/Y; the fraction is :meth:`micron2ipl`'s, MATLAB's.  ``"normal"``:
        along each surface's normal, from the point's nearest point on the INL
        surface and on the GCL surface (:meth:`BoundarySurface.nearest`); NaN
        where either lies beyond its fit, or the two distances leave no IPL
        between them (the surfaces cross).
        """
        method = ipl_method(method)
        pts = np.asarray(xyz, dtype=float).reshape(-1, 3)
        if method == "column":
            fraction = self.micron2ipl(pts)
            v_gcl = self.gcl.xy_eval(pts[:, 0], pts[:, 1])
            v_inl = self.inl.xy_eval(pts[:, 0], pts[:, 1])
            thickness = np.abs(v_gcl - v_inl)
            thickness = np.where(np.isfinite(fraction), thickness, np.nan)
            return IPLDepth(method, fraction, fraction * thickness, thickness)
        _, up_inl = self.inl.nearest(pts)
        _, up_gcl = self.gcl.nearest(pts)
        # Each distance turned to point into the IPL: from the INL toward the GCL,
        # and from the GCL toward the INL.
        from_inl = self.gcl_side * up_inl
        from_gcl = -self.gcl_side * up_gcl
        thickness = from_inl + from_gcl
        with np.errstate(divide="ignore", invalid="ignore"):
            fraction = np.where(thickness > 0, from_inl / thickness, np.nan)
        thickness = np.where(np.isfinite(fraction), thickness, np.nan)
        return IPLDepth(method, fraction, np.where(np.isfinite(fraction), from_inl, np.nan), thickness)

    @functools.cached_property
    def median_thickness_um(self) -> float:
        """The volume's median IPL thickness along the normals, in µm
        (:func:`thickness_summary`'s, with its defaults): the thickness a
        flattened depth axis is scaled by when none is given (:meth:`flatten`).
        NaN when no column has markers of both layers near it."""
        median = thickness_summary(self)["normal_um"]["median"]
        return float("nan") if median is None else float(median)

    def flatten(self, xyz, method: str = "column", thickness_um: float | None = None) -> np.ndarray:
        """Each XYZ point (microns) on a flattened IPL depth axis: X and Y as they
        are, Z replaced by ``fraction * thickness_um``, the fraction measured by
        ``method`` (:meth:`depth`).

        The INL sits at Z = 0 and the GCL at Z = ``thickness_um`` in every volume,
        whichever side of the INL its GCL lies on, so a point's Z is its depth
        from the INL on an IPL of that one thickness, at 1:1 with X and Y.
        ``thickness_um`` defaults to :attr:`median_thickness_um`.  Z is NaN where
        the point has no depth.  An addition (2026-10-22, James Anderson's
        request; DEVIATIONS.md): MATLAB's ``flattenRender`` put the fraction
        itself on Z (:func:`~sbfsem.geometry.meshops.flatten_render`).
        """
        if thickness_um is None:
            thickness_um = self.median_thickness_um
        thickness_um = float(thickness_um)
        if not (np.isfinite(thickness_um) and thickness_um > 0):
            raise ValueError(f"{self.source}: a flattened IPL needs a thickness above 0 µm, "
                             f"not {thickness_um!r}")
        pts = np.asarray(xyz, dtype=float).reshape(-1, 3)
        out = pts.copy()
        out[:, 2] = self.micron2ipl(pts, method) * thickness_um
        return out

    def strata_cdata(self, vertices, fill_nan: bool = True, method: str = "column") -> np.ndarray:
        """Per-vertex IPL depth for coloring a render.  Ports ``getStrataCData``."""
        depth = self.micron2ipl(vertices, method)
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


#: A column is in the thickness summary when both layers have a marker within
#: this many µm of it in XY: farther out the fits are extrapolations.
THICKNESS_NEAR_UM = 25.0
#: The thickness summary's columns along each side of the two fits' overlap.
THICKNESS_GRID = 60


def _stats(values: np.ndarray) -> dict:
    """Median, 5th and 95th percentiles, least and most of the finite values."""
    v = values[np.isfinite(values)]
    if v.size == 0:
        return {"median": None, "p5": None, "p95": None, "min": None, "max": None}
    p5, median, p95 = np.percentile(v, [5, 50, 95])
    return {"median": float(median), "p5": float(p5), "p95": float(p95),
            "min": float(v.min()), "max": float(v.max())}


def thickness_summary(boundaries: IPLBoundaries, *, grid: int = THICKNESS_GRID,
                      near_um: float = THICKNESS_NEAR_UM) -> dict:
    """How thick a volume's IPL is, along the layers' normals and along Z.

    Columns on a ``grid`` x ``grid`` lattice over the overlap of the two fits;
    each is measured at its midpoint between the surfaces, as an annotation
    there would be (:meth:`IPLBoundaries.depth`).  Only columns with a marker of
    each layer within ``near_um`` in XY are counted (``near``); the map keeps the
    rest as None.  Returns plain numbers: the normal and column thickness
    statistics in µm, their ratio, where the IPL is thinnest and thickest, each
    layer's tilt and the angle between the two layers' normals in degrees, and
    the map (``x_um``, ``y_um``, ``thickness_um`` rows by Y).
    """
    inl, gcl = boundaries.inl, boundaries.gcl
    x_lo = max(inl.x_points[0], gcl.x_points[0])
    x_hi = min(inl.x_points[-1], gcl.x_points[-1])
    y_lo = max(inl.y_points[0], gcl.y_points[0])
    y_hi = min(inl.y_points[-1], gcl.y_points[-1])
    xs = np.linspace(x_lo, x_hi, int(grid))
    ys = np.linspace(y_lo, y_hi, int(grid))
    gx, gy = np.meshgrid(xs, ys)  # rows are Y
    x, y = gx.ravel(), gy.ravel()
    z_inl = inl.xy_eval(x, y)
    z_gcl = gcl.xy_eval(x, y)
    mid = np.column_stack([x, y, (z_inl + z_gcl) / 2])

    from scipy.spatial import cKDTree

    near = np.ones(len(x), dtype=bool)
    for surface in (inl, gcl):
        markers = surface.marker_xyz_um()[:, :2]
        gap, _ = cKDTree(markers).query(np.column_stack([x, y]))
        near &= gap <= near_um

    normal = boundaries.depth(mid, "normal").thickness_um
    column = np.abs(z_gcl - z_inl)
    normal_near = np.where(near, normal, np.nan)
    column_near = np.where(near, column, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = column_near / normal_near

    _, inl_fx, inl_fy = inl.height_and_slopes(np.clip(x, inl.x_points[0], inl.x_points[-1]),
                                              np.clip(y, inl.y_points[0], inl.y_points[-1]))
    _, gcl_fx, gcl_fy = gcl.height_and_slopes(np.clip(x, gcl.x_points[0], gcl.x_points[-1]),
                                              np.clip(y, gcl.y_points[0], gcl.y_points[-1]))
    n_inl = np.column_stack([-inl_fx, -inl_fy, np.ones_like(x)])
    n_gcl = np.column_stack([-gcl_fx, -gcl_fy, np.ones_like(x)])
    cos = (n_inl * n_gcl).sum(axis=1) / np.linalg.norm(n_inl, axis=1) / np.linalg.norm(n_gcl, axis=1)
    between = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    inl_tilt = np.degrees(np.arctan(np.hypot(inl_fx, inl_fy)))
    gcl_tilt = np.degrees(np.arctan(np.hypot(gcl_fx, gcl_fy)))

    def place(index: int) -> dict:
        return {"x_um": float(x[index]), "y_um": float(y[index]),
                "thickness_um": float(normal[index]), "column_um": float(column[index])}

    finite = np.isfinite(normal_near)
    thinnest = place(int(np.nanargmin(normal_near))) if finite.any() else None
    thickest = place(int(np.nanargmax(normal_near))) if finite.any() else None
    rows = np.where(finite, normal_near, np.nan).reshape(len(ys), len(xs))
    return {
        "source": boundaries.source,
        "grid": int(grid),
        "near_um": float(near_um),
        "columns": int(len(x)),
        "near_columns": int(finite.sum()),
        "normal_um": _stats(normal_near),
        "column_um": _stats(column_near),
        "column_over_normal": _stats(ratio),
        "thinnest": thinnest,
        "thickest": thickest,
        "inl_tilt_deg": _stats(np.where(near, inl_tilt, np.nan)),
        "gcl_tilt_deg": _stats(np.where(near, gcl_tilt, np.nan)),
        "normals_angle_deg": _stats(np.where(near, between, np.nan)),
        "map": {
            "x_um": [float(v) for v in xs],
            "y_um": [float(v) for v in ys],
            "thickness_um": [[None if not np.isfinite(v) else round(float(v), 4) for v in row]
                             for row in rows],
        },
    }
