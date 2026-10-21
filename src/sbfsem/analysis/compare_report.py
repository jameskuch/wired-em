"""The numbers behind Render compare's report.

The report (:mod:`sbfsem.export.compare_pdf`) holds every chosen rendering of a
cell to the cell's annotations and every chosen pair of renderings to each
other.  The measurements themselves are :mod:`sbfsem.geometry.meshdiff`'s --
the error map (:func:`~sbfsem.geometry.meshdiff.deviation_map`), its summary
(:func:`~sbfsem.geometry.meshdiff.annotation_deviation`) and the signed
difference both ways (:func:`~sbfsem.geometry.meshdiff.mesh_difference` with
``both=True``) -- so a number in the report is the number the viewer shows for
the same surfaces.  What is added here is what a page prints beside them:

* :class:`SurfaceFacts`, a surface's own size and shape (faces, area, enclosed
  volume, open edges, visual bodies, creases);
* :class:`FieldStats`, one direction of a difference summarized over the
  *surface* (each vertex weighted by the area it stands for, as
  :func:`~sbfsem.geometry.meshdiff.vertex_areas` defines): mean, RMS, signed
  mean, percentiles, the share of the surface within set distances, a signed
  histogram, a profile along Z and the places that carry the most of it;
* :class:`PairResult`, both directions of a pair and what they make together:
  the Hausdorff distance (the larger of the two largest distances), the
  symmetric mean distance (the mean of the two area-weighted means, often
  called the Chamfer distance) and the section where the two disagree most.

Nothing here draws; every array is in microns, and a Z is turned into a section
number only by the ``section_of`` a caller passes (the volume's own Z scale).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from ..geometry.mesh import Mesh
from ..geometry.meshdiff import (
    SPOT_RADIUS_UM,
    MeshDifference,
    crease_density,
    error_spots,
    vertex_areas,
    visual_bodies,
    weighted_quantiles,
)

__all__ = [
    "SIGNED_HIST_BINS",
    "WITHIN_UM",
    "FieldStats",
    "PairResult",
    "Place",
    "SurfaceFacts",
    "ZBins",
    "boundary_edges",
    "field_stats",
    "pair_result",
    "surface_facts",
]

#: The distances the report gives the share of the surface within (microns).
WITHIN_UM = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0)
#: Bins of a signed histogram, over +/- ``HIST_REACH`` times the color scale.
SIGNED_HIST_BINS = 60
HIST_REACH = 1.5
#: Most bins a profile along Z carries (a long cell gets wider ones).
Z_BINS_MAX = 240
Z_STEP_UM = 1.0
#: Places named for one direction of a difference.
PLACES = 8


@dataclass(frozen=True)
class SurfaceFacts:
    """A surface's own size and shape.

    ``volume_um3`` is the divergence-theorem volume (:meth:`Mesh.volume`): the
    enclosed volume of a closed surface, and an approximation for an open one
    or one assembled from interpenetrating pieces (an overlap is counted once
    for each piece).  ``boundary_edges`` counts the edges only one triangle
    uses (0: closed).  ``bodies`` is :func:`~sbfsem.geometry.meshdiff.visual_bodies`
    (a correct cell is 1) and ``crease_um_per_um2``
    :func:`~sbfsem.geometry.meshdiff.crease_density` over 45 degrees.
    """

    n_vertices: int
    n_faces: int
    area_um2: float
    volume_um3: float
    boundary_edges: int
    bodies: int
    crease_um_per_um2: float
    bounds_um: tuple[tuple[float, float, float], tuple[float, float, float]]

    @property
    def closed(self) -> bool:
        return self.boundary_edges == 0


def boundary_edges(mesh: Mesh) -> int:
    """How many edges only one triangle uses (0 for a closed surface)."""
    if mesh.is_empty:
        return 0
    f = np.asarray(mesh.faces, dtype=np.int64)
    edge = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    key = np.minimum(edge[:, 0], edge[:, 1]) * mesh.n_vertices + np.maximum(edge[:, 0], edge[:, 1])
    _, counts = np.unique(key, return_counts=True)
    return int((counts == 1).sum())


def surface_facts(mesh: Mesh) -> SurfaceFacts:
    """:class:`SurfaceFacts` of ``mesh``."""
    area, volume = mesh.area_and_volume()
    lo, hi = mesh.bounds
    return SurfaceFacts(
        n_vertices=int(mesh.n_vertices),
        n_faces=int(len(mesh.faces)),
        area_um2=float(area),
        volume_um3=float(volume),
        boundary_edges=boundary_edges(mesh),
        bodies=int(visual_bodies(mesh)),
        crease_um_per_um2=float(crease_density(mesh)),
        bounds_um=(tuple(float(x) for x in lo), tuple(float(x) for x in hi)),  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class ZBins:
    """One direction of a difference along Z, in bins of ``step_um`` from
    ``z0_um``: the surface area in each, its area-weighted mean |d|, mean d and
    95th percentile of |d| (0 where a bin holds no surface)."""

    z0_um: float
    step_um: float
    area_um2: np.ndarray
    mean_abs_um: np.ndarray
    mean_signed_um: np.ndarray
    p95_abs_um: np.ndarray

    @property
    def centers_um(self) -> np.ndarray:
        return self.z0_um + (np.arange(len(self.area_um2)) + 0.5) * self.step_um


@dataclass(frozen=True)
class Place:
    """A place worth looking at: the surface within ``radius_um`` of ``xyz_um``
    (a surface vertex) carries ``share`` of the direction's area times |d|;
    its area-weighted mean d is ``mean_signed_um`` (positive: outside the other
    surface) and its largest |d| ``max_abs_um``.  ``section`` is the section
    that Z lies in, when the volume's Z scale is known."""

    xyz_um: tuple[float, float, float]
    section: float | None
    share: float
    mean_signed_um: float
    max_abs_um: float
    radius_um: float


@dataclass(frozen=True)
class FieldStats:
    """One direction of a difference (every vertex of one surface against the
    other), summarized over the surface.

    Each vertex counts for the area it stands for (``w``, a third of the area of
    the triangles around it), so ``mean_abs_um`` is the surface's mean distance
    and not its tessellation's.  ``quantiles_abs_um`` are the area-weighted 0th
    to 100th percentiles of |d|; ``within`` the share of the area within each
    of :data:`WITHIN_UM`; ``outside_share`` the share with d > 0 (outside the
    other surface).  ``integral_signed_um3`` is the sum of w d over the surface:
    to first order the volume of this surface's solid outside the other's minus
    the volume inside it.  ``hist_share`` is the share of the area in each bin
    of the signed histogram between ``hist_edges_um``, and ``below_share`` /
    ``above_share`` the share beyond its ends.
    """

    n_vertices: int
    area_um2: float
    mean_abs_um: float
    rms_um: float
    mean_signed_um: float
    max_abs_um: float
    quantiles_abs_um: np.ndarray
    within: tuple[tuple[float, float], ...]
    outside_share: float
    integral_signed_um3: float
    integral_abs_um3: float
    hist_edges_um: np.ndarray
    hist_share: np.ndarray
    below_share: float
    above_share: float
    z: ZBins
    places: tuple[Place, ...]

    def percentile(self, p: float) -> float:
        """The area-weighted ``p``-th percentile of |d| (``p`` in 0..100)."""
        return float(np.interp(p, np.arange(len(self.quantiles_abs_um)), self.quantiles_abs_um))


def _z_bins(z: np.ndarray, signed: np.ndarray, area: np.ndarray,
            z_range: tuple[float, float]) -> ZBins:
    lo, hi = z_range
    step = Z_STEP_UM
    z0 = float(np.floor(lo / step) * step)
    if (hi - z0) / step >= Z_BINS_MAX:
        step = (hi - z0) / (Z_BINS_MAX - 1)
        z0 = float(lo)
    size = int((hi - z0) // step) + 1
    which = np.clip(np.floor((z - z0) / step).astype(np.int64), 0, size - 1)
    magnitude = np.abs(signed)
    weight = np.bincount(which, weights=area, minlength=size)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_abs = np.where(weight > 0, np.bincount(which, weights=area * magnitude,
                                                    minlength=size) / weight, 0.0)
        mean_signed = np.where(weight > 0, np.bincount(which, weights=area * signed,
                                                       minlength=size) / weight, 0.0)
    p95 = np.zeros(size)
    order = np.argsort(which, kind="stable")
    in_order = which[order]
    starts = np.flatnonzero(np.r_[True, np.diff(in_order) != 0])
    for first, last in zip(starts, np.r_[starts[1:], len(order)], strict=True):
        part = order[first:last]
        p95[in_order[first]] = weighted_quantiles(magnitude[part], area[part], np.array([0.95]))[0]
    return ZBins(z0_um=z0, step_um=float(step), area_um2=weight, mean_abs_um=mean_abs,
                 mean_signed_um=mean_signed, p95_abs_um=p95)


def field_stats(
    mesh: Mesh,
    signed: np.ndarray,
    *,
    scale_um: float,
    z_range: tuple[float, float] | None = None,
    section_of: Callable[[float], float | None] | None = None,
) -> FieldStats:
    """:class:`FieldStats` of a signed distance per vertex of ``mesh``.

    ``scale_um`` sets the signed histogram's reach (``HIST_REACH`` times it each
    way); ``z_range`` the span the Z profile covers (both directions of a pair
    are binned alike when they are given the same one); ``section_of`` turns a
    Z in microns into a section number for the places.
    """
    signed = np.asarray(signed, dtype=float).ravel()
    if len(signed) != mesh.n_vertices:
        raise ValueError(f"{len(signed)} distances for {mesh.n_vertices} vertices")
    area = vertex_areas(mesh)
    weight = area if float(area.sum()) > 0 else np.ones_like(area)
    total = float(weight.sum())
    magnitude = np.abs(signed)
    held = weight > 0
    reach = HIST_REACH * max(float(scale_um), 1e-6)
    edges = np.linspace(-reach, reach, SIGNED_HIST_BINS + 1)
    share, _ = np.histogram(signed, bins=edges, weights=weight)
    z = mesh.vertices[:, 2]
    span = z_range if z_range is not None else (float(z.min()), float(z.max()))
    spots = error_spots(mesh.vertices, magnitude, area, count=PLACES)
    places: list[Place] = []
    if spots:
        tree = cKDTree(mesh.vertices)
        for spot in spots:
            near = np.asarray(tree.query_ball_point(spot.xyz_um, SPOT_RADIUS_UM), dtype=np.int64)
            w = weight[near]
            mean_signed = float((w * signed[near]).sum() / w.sum()) if w.sum() > 0 else 0.0
            places.append(Place(
                xyz_um=spot.xyz_um,
                section=section_of(spot.xyz_um[2]) if section_of else None,
                share=spot.share,
                mean_signed_um=mean_signed,
                max_abs_um=spot.max_um,
                radius_um=SPOT_RADIUS_UM,
            ))
    return FieldStats(
        n_vertices=int(mesh.n_vertices),
        area_um2=float(area.sum()),
        mean_abs_um=float((weight * magnitude).sum() / total),
        rms_um=float(np.sqrt((weight * signed**2).sum() / total)),
        mean_signed_um=float((weight * signed).sum() / total),
        max_abs_um=float(magnitude[held].max()) if held.any() else 0.0,
        quantiles_abs_um=weighted_quantiles(magnitude, weight, np.linspace(0.0, 1.0, 101)),
        within=tuple((t, float(weight[magnitude <= t].sum() / total)) for t in WITHIN_UM),
        outside_share=float(weight[signed > 0].sum() / total),
        integral_signed_um3=float((area * signed).sum()),
        integral_abs_um3=float((area * magnitude).sum()),
        hist_edges_um=edges,
        hist_share=share / total,
        below_share=float(weight[signed < edges[0]].sum() / total),
        above_share=float(weight[signed > edges[-1]].sum() / total),
        z=_z_bins(z, signed, weight, span),
        places=tuple(places),
    )


@dataclass(frozen=True)
class PairResult:
    """Two renderings, ``a`` and ``b`` (their numbers in the report), against
    each other both ways.

    ``ab`` is every vertex of A against B's surface (positive where A is outside
    B) and ``ba`` every vertex of B against A's.  ``hausdorff_um`` is the larger
    of the two largest distances: no point of either surface is farther than
    that from the other.  ``chamfer_um`` is the mean of the two area-weighted
    mean distances, a symmetric "how far apart are they on average", and
    ``chamfer_rms_um`` the same for the RMS.  ``scale_um`` is the color scale
    both pictures of the pair share (the larger 98th percentile of |d|), and
    ``worst_z_um`` the middle of the Z bin where the two surfaces together
    disagree most (bins holding under 2 % of the largest bin's area are passed
    over, so a thin tip does not stand for the cell).
    """

    a: int
    b: int
    sign_by: str
    sample_spacing_um: float
    n_samples: int
    overruled_ab: int
    overruled_ba: int
    ab: FieldStats
    ba: FieldStats
    hausdorff_um: float
    chamfer_um: float
    chamfer_rms_um: float
    scale_um: float
    volume_a_um3: float
    volume_b_um3: float
    worst_z_um: float | None


def pair_result(
    a: int, b: int, mesh_a: Mesh, mesh_b: Mesh, diff: MeshDifference, *,
    section_of: Callable[[float], float | None] | None = None,
) -> PairResult:
    """:class:`PairResult` of ``diff``, a both-ways :class:`MeshDifference` of
    ``mesh_a`` against ``mesh_b`` (``mesh_difference(..., both=True)``)."""
    if diff.distance_b_um is None:
        raise ValueError("a pair needs the difference both ways (mesh_difference(both=True))")
    za, zb = mesh_a.vertices[:, 2], mesh_b.vertices[:, 2]
    span = (float(min(za.min(), zb.min())), float(max(za.max(), zb.max())))
    p98 = [float(weighted_quantiles(np.abs(d), vertex_areas(m), np.array([0.98]))[0])
           for d, m in ((diff.distance_um, mesh_a), (diff.distance_b_um, mesh_b))]
    scale = max(max(p98), 1e-6)
    ab = field_stats(mesh_a, diff.distance_um, scale_um=scale, z_range=span, section_of=section_of)
    ba = field_stats(mesh_b, diff.distance_b_um, scale_um=scale, z_range=span,
                     section_of=section_of)
    # Where the two disagree most together: the area-weighted mean |d| of both
    # surfaces in each Z bin (they share the bins: one span, one step).
    weight = ab.z.area_um2 + ba.z.area_um2
    with np.errstate(invalid="ignore", divide="ignore"):
        joint = np.where(weight > 0, (ab.z.area_um2 * ab.z.mean_abs_um
                                      + ba.z.area_um2 * ba.z.mean_abs_um) / weight, 0.0)
    worst = None
    if weight.max() > 0:
        ok = weight >= 0.02 * weight.max()
        worst = float(ab.z.centers_um[int(np.argmax(np.where(ok, joint, -1.0)))])
    return PairResult(
        a=int(a), b=int(b),
        sign_by=diff.sign_by,
        sample_spacing_um=float(diff.sample_spacing_um),
        n_samples=int(diff.n_samples),
        overruled_ab=int(diff.sign_overruled),
        overruled_ba=int(diff.sign_overruled_b),
        ab=ab, ba=ba,
        hausdorff_um=max(ab.max_abs_um, ba.max_abs_um),
        chamfer_um=0.5 * (ab.mean_abs_um + ba.mean_abs_um),
        chamfer_rms_um=float(np.sqrt(0.5 * (ab.rms_um**2 + ba.rms_um**2))),
        scale_um=scale,
        volume_a_um3=float(mesh_a.volume()),
        volume_b_um3=float(mesh_b.volume()),
        worst_z_um=worst,
    )
