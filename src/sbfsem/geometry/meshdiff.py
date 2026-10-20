"""Signed distance between two renderings of the same neuron.

The compare view renders one cell under two techniques and asks where they
disagree.  The measurement here is per-vertex: for every vertex of mesh A,
the distance to the nearest point of mesh B's surface, signed — positive where
A sits outside B, negative where it sits inside.  Two rules decide the sign
(``sign=`` of :func:`mesh_difference`): B's outward normal at the nearest
surface sample (``"normal"``, the default, which is the library's long-standing
behavior) or whether B's solid encloses the vertex (``"enclosure"``, a ray
count over B with its open ends closed, and B's winding number where closing
an end would invent a sheet; see :func:`enclosed_points_open`).

Exact point-to-triangle distance over hundreds of thousands of faces needs an
R-tree; instead B's surface is sampled densely (area-weighted, deterministic)
and queried through a k-d tree.  The reported distance is then the distance to
the nearest *sample*, which overestimates the true surface distance by at most
about one sample spacing.  That spacing is measured and returned as
``sample_spacing_um`` so the resolution of the comparison is part of the
result, not folklore: differences well above it are real, differences below it
are noise.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import NamedTuple

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from ..progress import report
from .mesh import Mesh

__all__ = [
    "AnnotationDeviation",
    "AnnotationErrors",
    "AreaStats",
    "DeviationMap",
    "ErrorSpot",
    "MeshDifference",
    "NearestAnnotation",
    "RimBias",
    "SurfaceStats",
    "ZProfile",
    "add_reverse",
    "annotation_deviation",
    "close_open_boundaries",
    "crease_density",
    "deviation_map",
    "dihedral_angles",
    "enclosed_points",
    "enclosed_points_open",
    "mesh_difference",
    "nearest_annotation",
    "sample_surface",
    "vertex_areas",
    "visual_bodies",
    "weighted_quantiles",
]


#: How the sign of a difference is decided (see :func:`mesh_difference`).
DIFFERENCE_SIGNS = ("normal", "enclosure")


@dataclass(frozen=True)
class AreaStats:
    """A signed field's statistics over the *surface* of the mesh it lies on.

    Each vertex counts for the area it stands for (:func:`vertex_areas`), not
    once: a region meshed ten times as finely has ten times the vertices but the
    same area, and weighs the same.  The vertex-weighted figures of
    :class:`MeshDifference` stay as they were; on an unevenly tessellated mesh
    these are the ones that say how much of the *surface* disagrees.  ``max``
    is over the vertices some face uses.
    """

    mean_abs_um: float
    rms_um: float
    p95_abs_um: float
    #: 98th percentile of |d|: the color scale the area-weighted figures suggest.
    p98_abs_um: float
    max_abs_um: float
    mean_signed_um: float


@dataclass(frozen=True)
class MeshDifference:
    """Per-vertex signed distances from mesh A to mesh B, with summary stats."""

    #: Signed distance in microns for every vertex of A; positive = outside B.
    distance_um: np.ndarray
    #: Mean of |d|.
    mean_abs_um: float
    #: Root-mean-square of d.
    rms_um: float
    #: 95th percentile of |d|.
    p95_abs_um: float
    #: Largest |d|.
    max_abs_um: float
    #: Mean of d — net inflation (+) or shrinkage (−) of A relative to B.
    mean_signed_um: float
    #: Suggested symmetric color scale (98th percentile of |d|).
    scale_um: float
    #: Mean spacing between surface samples: the comparison's resolution.
    sample_spacing_um: float
    #: How many points B's surface was sampled with.
    n_samples: int
    #: What decided the sign: ``"normal"`` or ``"enclosure"``.
    sign_by: str = "normal"
    #: The statistics above, weighted by the area of A's surface.
    stats_area: AreaStats | None = None
    #: With ``both=True``: the signed distance of every vertex of B to A's
    #: surface, by the same rule (positive = B outside A), and its statistics
    #: weighted by the area of B's surface.
    distance_b_um: np.ndarray | None = None
    stats_area_b: AreaStats | None = None
    #: By ``sign="enclosure"`` against an open mesh B: how many vertices of A the
    #: winding number of B put on the other side from the count over B with its
    #: open ends capped (see :func:`enclosed_points_open`), and the same for the
    #: vertices of B against A.  Those vertices lie under a fan that would have
    #: been a sheet which B does not have.  0 for a closed B and by ``"normal"``.
    sign_overruled: int = 0
    sign_overruled_b: int = 0


def sample_surface(
    mesh: Mesh, count: int, *, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Area-weighted random points on a mesh surface, with their face normals.

    Deterministic for a given ``seed``, so a diff is reproducible.  The mesh's
    own vertices are appended (with area-weighted vertex normals) so thin or
    sparsely sampled features always contribute at least their vertices.
    """
    if mesh.is_empty:
        raise ValueError("cannot sample an empty mesh")
    areas = mesh.face_areas()
    total = float(areas.sum())
    if total <= 0:
        raise ValueError("mesh has no surface area to sample")

    rng = np.random.default_rng(seed)
    faces = rng.choice(len(areas), size=int(count), p=areas / total)
    # Uniform barycentric coordinates via the square-root trick.
    r1 = np.sqrt(rng.random(int(count)))
    r2 = rng.random(int(count))
    corners = mesh.vertices[mesh.faces[faces]]
    points = (
        (1 - r1)[:, None] * corners[:, 0]
        + (r1 * (1 - r2))[:, None] * corners[:, 1]
        + (r1 * r2)[:, None] * corners[:, 2]
    )
    normals = mesh.face_normals()[faces]

    # Vertex normals: mean of adjacent face normals, area-weighted by accumulation.
    face_normals = mesh.face_normals() * areas[:, None]
    vertex_normals = np.zeros_like(mesh.vertices)
    for corner in range(3):
        np.add.at(vertex_normals, mesh.faces[:, corner], face_normals)
    lengths = np.linalg.norm(vertex_normals, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        vertex_normals = np.where(lengths > 0, vertex_normals / lengths, 0.0)

    return (
        np.vstack([points, mesh.vertices]),
        np.vstack([normals, vertex_normals]),
    )


@dataclass(frozen=True)
class AnnotationDeviation:
    """How far a rendered surface strays from the annotations themselves.

    The annotations — discs in their section planes, plus any closed-curve
    outlines — are the closest thing to ground truth a render can be held to.
    The deviation is measured in both directions, because each direction sees
    a different failure:

    * ``surface_um`` — mean distance from points on the rendered surface to
      the nearest annotation.  Surface with no annotation anywhere near it
      (a soma cap ballooning past the last annotated section, a cone sweep
      through unannotated tissue) scores here.
    * ``annotation_um`` — mean distance from points on each annotation's rim
      to the rendered surface.  Annotations the render failed to reach
      (dropped discs, an undersized body) score here.

    Both are absolute values; an annotation poking outside the surface counts
    the same as surface ballooning past an annotation — errors never cancel.
    ``mean_um`` pools both sample sets.  ``excess_volume_um3`` turns the first
    direction into a volume: mean surface deviation times surface area, a
    first-order estimate of the volume of tissue the render claims beyond any
    annotation's support.
    """

    mean_um: float
    surface_um: float
    annotation_um: float
    p95_um: float
    max_um: float
    excess_volume_um3: float
    #: The comparison's resolution (surface sample spacing), as in
    #: :class:`MeshDifference`.
    sample_spacing_um: float
    n_discs: int
    n_curves: int
    #: ``surface_um`` over the *exterior* surface only.  A surface sample is
    #: hidden when a hair outside it along its normal is still enclosed by
    #: the mesh itself -- a tube inside a soma, one piece inside another.
    #: Such surface is invisible, yet it scores ~0 in ``surface_um`` (it sits
    #: among the annotations it is buried in), so a render assembled from
    #: interpenetrating pieces dilutes its own mean; one contoured as a single
    #: sheet has nothing to hide.  ``None`` when every sample is hidden.
    exterior_um: float | None = None
    #: Share of the surface area that is hidden.
    hidden_fraction: float = 0.0
    #: ``annotation_um`` over the rims the mesh does not enclose.  An enclosed
    #: rim (a dendrite annotation traced on through the soma, or a body the
    #: render made too fat) can only be "reached" by buried surface, which is
    #: exactly what the exterior numbers set aside; what remains measures the
    #: render's thinness -- annotations it failed to reach.  ``None`` when no
    #: rim is exterior.
    annotation_exterior_um: float | None = None


#: How far outside a surface sample the hidden-surface probe is placed.
PROBE_UM = 0.05

#: Most rim points the enclosure test is run on.  A closed-curve outline
#: arrives evaluated every ~0.016 um (RPC1 c2628: 1.44 M rim points), sixteen
#: times denser than a disc rim is sampled, and testing every one took 70 of
#: the deviation's 77 s there.  Above this many, every k-th rim point stands
#: for the rest in ``annotation_exterior_um`` alone; every other number still
#: uses them all.
RIM_ENCLOSURE_MAX = 100_000

#: Most rim points an error map carries to the browser.
RIM_DISPLAY_MAX = 50_000

#: What one block of a distance kernel may add to the process, in bytes.  The
#: kernels test a block of points against every edge (or disc) of one outline
#: set at once, so the memory they need is points x edges.  Measured
#: (tracemalloc, 2026-10-15): the old fixed block of 20,000 points against
#: c1178's longest outline (1,358 edges) needed about 1.7 GB (64 bytes a
#: pair), and the disc fallback against RC1 c606's 8,260 discs about 8 GB per
#: block (0.39 MB a point, 47 bytes a pair); the whole deviation of a
#: 200,000-vertex mesh passed 6.5 GB, where the service unit's hard limit is
#: 13.5 GB shared by every build.  A prototype of this blocking added 450 MB
#: for bitwise identical output.  Every row is independent, so the block size
#: changes the memory and the speed, never a number.  The budget bounds these
#: two kernels' pair blocks only, not the whole measurement (see
#: ``SbfsemService.MEASURE_MB``).
KERNEL_BUDGET_BYTES = 192 * 1024 * 1024
#: Bytes charged per (point, edge) pair of ``_distance_to_polygons`` and per
#: (point, disc) pair of the brute-force disc fallback: the measured 64 and
#: 47, rounded up to leave room for the temporaries the measurement missed.
POLYGON_PAIR_BYTES = 200
DISC_PAIR_BYTES = 80
#: Bytes charged per (point, triangle) pair of :func:`enclosed_points`.  Its
#: block was a fixed 512 points against every triangle of one cell, and on a
#: cell that is long in Z the triangles of a 1 um square of the section plane
#: pile up along the whole length: RPC1 c2628 (400,000 faces) tested 49,562 rim
#: points at +1,287 MB that way, and +503 MB at 64 points a block, to the
#: same answer in the same time (7.2 s, 6.8 s).
ENCLOSURE_PAIR_BYTES = 160
#: The block the kernels used before they were bounded: still the ceiling.
KERNEL_BLOCK_MAX = 20_000


def _block(pairs_per_point: int, bytes_per_pair: int) -> int:
    """How many points one block may hold within :data:`KERNEL_BUDGET_BYTES`."""
    fit = KERNEL_BUDGET_BYTES // max(1, int(pairs_per_point) * int(bytes_per_pair))
    return int(min(KERNEL_BLOCK_MAX, max(1, fit)))


#: Radius edges (um) that split the discs into classes for the nearest-disc
#: search.  One k-d tree over all the centers is certified by the *largest*
#: radius (a disc's distance is at least its center distance minus its radius),
#: and a handful of wide discs (a soma, RC1 c606's 83 discs of 2.5 um and up)
#: spoils that certificate for every point.  One tree per class has a
#: certificate sized to its own largest radius.  The disc pass alone over the
#: 200,000 vertices of a Voxel remesh (2026-10-15, load 8), one tree against
#: classes: RC1 c606 (8,260 discs) 42.0 s and 1.3 s; RC2 c74420 (10,514) 32.4 s
#: and 2.3 s; Inferior c19698 (1,489, a heavy tail of 3 to 6 um) 8.5 s and
#: 1.1 s; Inferior c1178 (2,304) 4.4 s and 0.7 s; every distance the same bit.
DISC_CLASS_EDGES = (0.5, 1.0, 2.0, 3.5)
#: Nearest centers asked of a class's tree before its certificate is checked.
DISC_CANDIDATES = 16
#: A class with this many discs or fewer is tested against every disc outright:
#: the tree costs more than the pairs it would save.
DISC_BRUTE_MAX = 128


class NearestAnnotation(NamedTuple):
    """Where each point stands against the nearest annotation.

    ``dist`` is the distance to the nearest annotation (a disc in its section
    plane, or a closed-curve outline filled at its section height).  ``owner``
    is that annotation: its index among the discs, or ``n_discs`` plus its
    index among the outlines as they were passed; -1 where there is no
    annotation at all.  ``radial`` is the in-plane part (how far past the
    disc's rim, or the outline's edge, the point projects; 0 inside) and
    ``axial`` the gap along Z, so ``hypot(radial, axial)`` is ``dist`` -- to
    one ulp, because ``dist`` keeps the k-d tree's own distance to an outline
    vertex wherever that edged out the exact one, as the distance did before
    the components were returned.
    """

    dist: np.ndarray
    owner: np.ndarray
    radial: np.ndarray
    axial: np.ndarray


def _new_nearest(count: int) -> NearestAnnotation:
    return NearestAnnotation(np.full(count, np.inf), np.full(count, -1, dtype=np.int64),
                             np.full(count, np.inf), np.full(count, np.inf))


def _disc_terms(p: np.ndarray, cand: np.ndarray,
                rad: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Distance, in-plane overshoot and Z gap from each point (m, 3) to each of
    its candidate discs (centers (m, k, 3), radii (m, k)).

    A disc is the flat region ``|p_xy - c_xy| <= r`` in its section plane, so
    the exact distance is ``hypot(max(|dxy| - r, 0), dz)``.
    """
    dxy = np.hypot(p[:, None, 0] - cand[..., 0], p[:, None, 1] - cand[..., 1])
    dz = np.abs(p[:, None, 2] - cand[..., 2])
    dr = np.maximum(dxy - rad, 0.0)
    return np.hypot(dr, dz), dr, dz


_Best = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]


def _best_candidate(d: np.ndarray, dr: np.ndarray, dz: np.ndarray,
                    ids: np.ndarray) -> _Best:
    """Each row's nearest candidate: its distance, owner and components.  Of
    candidates at exactly the same distance the lowest annotation index wins,
    so the owner does not depend on the order a tree returned them in."""
    least = d.min(axis=1)
    col = np.where(d == least[:, None], ids, np.iinfo(np.int64).max).argmin(axis=1)
    take = np.arange(len(d))
    return least, ids[take, col], dr[take, col], dz[take, col]


def _brute_discs(p: np.ndarray, cc: np.ndarray, rr: np.ndarray, sel: np.ndarray) -> _Best:
    """Every point of ``p`` against every disc of one class (``cc``, ``rr``,
    whose indexes among all the discs are ``sel``, ascending, so the first
    minimum is the lowest index)."""
    cand = np.broadcast_to(cc, (len(p), *cc.shape))
    rad = np.broadcast_to(rr, (len(p), len(rr)))
    d, dr, dz = _disc_terms(p, cand, rad)
    col = d.argmin(axis=1)
    take = np.arange(len(p))
    return d[take, col], sel[col], dr[take, col], dz[take, col]


def _merge(found: NearestAnnotation, lo: int, hi: int, d: np.ndarray, own: np.ndarray,
           dr: np.ndarray, dz: np.ndarray) -> None:
    """Keep, for points ``lo:hi``, whichever of ``found`` and the new answer is
    nearer; at exactly the same distance the lower annotation index."""
    cur = found.dist[lo:hi]
    better = (d < cur) | ((d == cur) & (own < found.owner[lo:hi]))
    if better.any():
        rows = np.flatnonzero(better) + lo
        found.dist[rows] = d[better]
        found.owner[rows] = own[better]
        found.radial[rows] = dr[better]
        found.axial[rows] = dz[better]


def _nearest_discs(pts: np.ndarray, centers: np.ndarray, radii: np.ndarray,
                   found: NearestAnnotation, *, stage: str) -> None:
    """Fold the nearest disc of every point into ``found``.

    The discs are split by radius (:data:`DISC_CLASS_EDGES`).  Within a class
    the nearest candidate *centers* come from a k-d tree, which is correct once
    no disc outside the candidate set can beat the best answer so far: a disc's
    distance is at least its center distance minus its radius, so the
    certificate is ``best <= d_kth_center - r_max`` with the class's own
    ``r_max`` (``best`` being the better of this class's candidates and what
    the classes before it found).  The points the certificate does not settle
    are tested against every disc of the class, in blocks of :func:`_block`
    points so a class of ten thousand discs cannot ask for gigabytes.  Exact
    like the exhaustive loop: every distance is the same elementwise
    arithmetic, and a minimum does not depend on the order it is taken in.

    Progress counts passes: ``done = pass * n + block`` against ``passes * n``,
    so the count only goes forward across the classes.
    """
    n = len(pts)
    if not len(centers) or not n:
        return
    which = np.digitize(radii, DISC_CLASS_EDGES)
    classes = [sel for sel in (np.flatnonzero(which == c) for c in range(len(DISC_CLASS_EDGES) + 1))
               if len(sel)]
    passes = len(classes)
    for number, sel in enumerate(classes):
        cc, rr = centers[sel], radii[sel]
        brute = _block(len(sel), DISC_PAIR_BYTES)
        if len(sel) <= DISC_BRUTE_MAX:
            for s in range(0, n, brute):
                report(stage, number * n + s, passes * n)
                hi = min(s + brute, n)
                _merge(found, s, hi, *_brute_discs(pts[s:hi], cc, rr, sel))
            continue
        tree = cKDTree(cc)
        k = min(len(sel), DISC_CANDIDATES)
        r_max = float(rr.max())
        step = min(KERNEL_BLOCK_MAX, _block(k, DISC_PAIR_BYTES))
        for s in range(0, n, step):
            report(stage, number * n + s, passes * n)
            hi = min(s + step, n)
            p = pts[s:hi]
            d_c, ix = tree.query(p, k=k, workers=-1)
            if k == 1:
                d_c, ix = d_c[:, None], ix[:, None]
            d, dr, dz = _disc_terms(p, cc[ix], rr[ix])
            least, own, dr_best, dz_best = _best_candidate(d, dr, dz, sel[ix])
            if k < len(sel):
                settled = np.minimum(least, found.dist[s:hi]) <= d_c[:, -1] - r_max
                rows = np.flatnonzero(~settled)
                for r in range(0, len(rows), brute):
                    part = rows[r:r + brute]
                    least[part], own[part], dr_best[part], dz_best[part] = \
                        _brute_discs(p[part], cc, rr, sel)
            _merge(found, s, hi, least, own, dr_best, dz_best)
    report(stage, passes * n, passes * n)


def _nearest_outlines(pts: np.ndarray, polygons: list[tuple[np.ndarray, float]],
                      found: NearestAnnotation, n_discs: int, *, stage: str) -> None:
    """Fold the nearest closed-curve outline of every point into ``found``.

    Each polygon is a planar filled region at its section height: zero
    in-plane distance inside the outline, distance to the nearest edge
    outside, corner-combined with the height difference.  ``found`` arrives
    holding the discs' answer (or nothing), and the outlines are owners
    ``n_discs`` and up, in the order given.

    Exact, but not exhaustive.  Testing every point against every edge of
    every outline is what this did until 2026-09-18, and RPC1's Mueller cell
    c2628 (3,978 outlines, 1.4 M outline points) took 131 s for 3,000 points:
    44 minutes and 9 GB for the 60,000 a deviation uses.  Almost all of that
    was outlines many sections away.  A point's distance to the nearest
    outline *vertex* (one k-d tree query) bounds its answer from above, since
    that vertex belongs to an outline; an outline farther than the bound in Z,
    or whose bounding box is farther than it in XY, cannot be the nearest and
    is skipped for that point.  The bound tightens as outlines are measured,
    and it starts from the better of that vertex and the disc already found:
    on c1178 (2,304 discs, 130 outlines) the median starting bound fell from
    43.2 um to 0.31 um, and the distances of a 200,000-vertex map from 412 s
    to 18 s (a machine at load 10), to the same bits.  Points go through an
    outline in blocks of :func:`_block`, so a long outline cannot ask for
    gigabytes either.  ``stage`` names the work, counted in outlines, for
    :func:`sbfsem.progress.report`.
    """
    rings = [(index, np.asarray(ring, dtype=float).reshape(-1, 2), float(z))
             for index, (ring, z) in enumerate(polygons) if len(ring) >= 3]
    if not rings or not len(pts):
        return
    vertices = np.vstack([np.column_stack([ring, np.full(len(ring), z)]) for _, ring, z in rings])
    vertex_ring = np.repeat([index for index, _, _ in rings], [len(ring) for _, ring, _ in rings])
    # A vertex no nearer than the farthest answer already found cannot be an
    # answer for any point, so the search stops there (inf where nothing is
    # found, which is not less than anything).
    near_vertex, at = cKDTree(vertices).query(
        pts, workers=-1, distance_upper_bound=float(found.dist.max()))
    rows = np.flatnonzero(near_vertex < found.dist)
    if len(rows):
        # An upper bound that already is an answer: this vertex's outline is at
        # least this near.  The exact pass below replaces it wherever it does
        # better; where it ties, the vertex stands.
        v = vertices[at[rows]]
        found.dist[rows] = near_vertex[rows]
        found.owner[rows] = n_discs + vertex_ring[at[rows]]
        found.radial[rows] = np.hypot(pts[rows, 0] - v[:, 0], pts[rows, 1] - v[:, 1])
        found.axial[rows] = np.abs(pts[rows, 2] - v[:, 2])

    # Points in height order, so an outline meets only the slab it could matter to.
    order = np.argsort(pts[:, 2], kind="stable")
    sorted_z = pts[order, 2]
    reach = float(found.dist.max())
    ordered = sorted(rings, key=lambda item: item[2])
    for number, (index, ring, z) in enumerate(ordered):
        report(stage, number, len(ordered))
        lo, hi = np.searchsorted(sorted_z, [z - reach, z + reach])
        if lo == hi:
            continue
        slab = order[lo:hi]
        p = pts[slab]
        bound = found.dist[slab]
        xmin, ymin = ring.min(axis=0)
        xmax, ymax = ring.max(axis=0)
        outside = np.hypot(np.maximum(np.maximum(xmin - p[:, 0], p[:, 0] - xmax), 0.0),
                           np.maximum(np.maximum(ymin - p[:, 1], p[:, 1] - ymax), 0.0))
        near = (np.abs(p[:, 2] - z) < bound) & (outside < bound)
        if not near.any():
            continue
        idx = slab[near]
        a = ring
        b = np.roll(ring, -1, axis=0)
        ab = b - a
        ab2 = np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-18)
        step = _block(len(ring), POLYGON_PAIR_BYTES)
        for s in range(0, len(idx), step):
            chunk = idx[s:s + step]
            q = pts[chunk]
            ap = q[:, None, :2] - a[None, :, :]
            t = np.clip(np.einsum("ijk,jk->ij", ap, ab) / ab2, 0.0, 1.0)
            close = a[None, :, :] + t[..., None] * ab[None, :, :]
            d_edge = np.hypot(q[:, None, 0] - close[..., 0],
                              q[:, None, 1] - close[..., 1]).min(axis=1)
            # Even-odd rule, vectorized over points x edges.
            cond = (a[None, :, 1] > q[:, None, 1]) != (b[None, :, 1] > q[:, None, 1])
            with np.errstate(divide="ignore", invalid="ignore"):
                x_at = a[None, :, 0] + (q[:, None, 1] - a[None, :, 1]) \
                    * ab[None, :, 0] / np.where(ab[None, :, 1] == 0, np.nan,
                                                ab[None, :, 1])
            inside = ((cond & (q[:, None, 0] < x_at)).sum(axis=1) % 2) == 1
            d_plane = np.where(inside, 0.0, d_edge)
            gap = q[:, 2] - z
            new = np.hypot(d_plane, gap)
            better = new < found.dist[chunk]
            if better.any():
                won = chunk[better]
                found.dist[won] = new[better]
                found.owner[won] = n_discs + index
                found.radial[won] = d_plane[better]
                found.axial[won] = np.abs(gap[better])
    report(stage, len(ordered), len(ordered))


def nearest_annotation(
    pts: np.ndarray,
    centers: np.ndarray,
    radii: np.ndarray,
    polygons: list[tuple[np.ndarray, float]] | None = None,
    *,
    stage: str = "",
) -> NearestAnnotation:
    """The nearest annotation to every point, and how far along each axis.

    The one distance kernel behind :func:`deviation_map` and
    :func:`annotation_deviation`, so the picture and the numbers are the same
    distances.  ``centers``/``radii`` are the disc annotations in microns and
    ``polygons`` the closed-curve outlines as ``(ring_xy, z_um)`` pairs; see
    :class:`NearestAnnotation` for what comes back.  ``dist`` is exactly what
    the exhaustive loop over every disc and every edge gives (the loop is the
    definition the tests hold this to), found through radius-class k-d trees
    for the discs and a disc-seeded bound for the outlines.  ``stage`` prefixes
    the progress stages ("vertices: distance to discs", then "... to outlines").
    """
    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    radii = np.asarray(radii, dtype=float).ravel()
    found = _new_nearest(len(pts))
    prefix = f"{stage}: " if stage else ""
    _nearest_discs(pts, centers, radii, found, stage=f"{prefix}distance to discs")
    if polygons:
        _nearest_outlines(pts, polygons, found, len(centers),
                          stage=f"{prefix}distance to outlines")
    return found


def _distance_to_discs(pts: np.ndarray, centers: np.ndarray,
                       radii: np.ndarray, *, stage: str = "distance to discs") -> np.ndarray:
    """Distance from each point to the nearest annotation disc (``inf`` when
    there are none): :func:`nearest_annotation`'s disc half, alone.  ``stage``
    names the work for :func:`sbfsem.progress.report`."""
    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    found = _new_nearest(len(pts))
    _nearest_discs(pts, np.asarray(centers, dtype=float).reshape(-1, 3),
                   np.asarray(radii, dtype=float).ravel(), found, stage=stage)
    return found.dist


def _distance_to_polygons(pts: np.ndarray,
                          polygons: list[tuple[np.ndarray, float]], *,
                          stage: str = "distance to outlines") -> np.ndarray:
    """Distance from each point to the nearest closed-curve outline (``inf``
    when there are none): :func:`nearest_annotation`'s outline half, alone,
    seeded by the nearest outline vertex only."""
    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    found = _new_nearest(len(pts))
    _nearest_outlines(pts, polygons, found, 0, stage=stage)
    return found.dist


def _rim_points(centers: np.ndarray, radii: np.ndarray,
                polygons: list[tuple[np.ndarray, float]],
                spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Sample points along every annotation's boundary, with each point's
    owning annotation, numbered as :func:`nearest_annotation` does: the discs
    by index, then ``len(centers)`` plus the outline's place in ``polygons``."""
    chunks: list[np.ndarray] = []
    owners: list[np.ndarray] = []
    for i, (c, r) in enumerate(zip(centers, radii, strict=True)):
        n = int(np.clip(np.ceil(2.0 * np.pi * r / max(spacing, 1e-3)), 8, 64))
        th = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
        chunks.append(np.column_stack([c[0] + r * np.cos(th),
                                       c[1] + r * np.sin(th),
                                       np.full(n, c[2])]))
        owners.append(np.full(n, i, dtype=np.int64))
    for j, (ring, z) in enumerate(polygons):
        if len(ring):
            chunks.append(np.column_stack([ring, np.full(len(ring), z)]))
            owners.append(np.full(len(ring), len(centers) + j, dtype=np.int64))
    if not chunks:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64)
    return np.vstack(chunks), np.concatenate(owners)


#: Positions closer than this (um) are one vertex when the open boundary of a
#: mesh is looked for: the pieces of an assembled pathway meet at rings that
#: were computed twice, to the last bit or two of a float64.
WELD_UM = 1e-6

#: How round a boundary piece must be for its cap to be trusted: 4 pi A / L^2 for
#: the fan to the piece's centroid, A its area and L the length of the boundary.
#: A circle is 1 and a 10-gon ring 0.97.  Measured on the 1,912 pieces of the
#: Original pathway of Inferior c1178 and c21768 and RC1 c606 (2026-10-16): 1,911
#: of them are 0.35 or more (the small degenerate rings 0.35 to 0.52); the one
#: long thin slot of c21768 (1,642 edges, 382 um round, a fan of 1,563 um2 over a
#: boundary whose vector area is 43 um2, 38 % of the surface) is 0.13.  See
#: :func:`enclosed_points_open`.
CAP_ROUNDNESS_MIN = 0.25


class _Caps(NamedTuple):
    """The fans over the open boundary pieces of a mesh (see :func:`_cap_open_boundaries`)."""

    #: The mesh and then the fans: first those of the round pieces, then the others.
    mesh: Mesh
    #: ``mesh.faces[:trusted]`` are the mesh's own faces and the round pieces' fans.
    trusted: int
    #: The centroid and the farthest boundary vertex from it of every piece whose fan is
    #: not trusted: the fan lies inside the ball that this centroid and radius give.
    hubs: np.ndarray
    radii: np.ndarray


def _cap_open_boundaries(mesh: Mesh, weld_um: float, roundness_min: float) -> _Caps:
    """``mesh`` with a fan over every open boundary piece, the round ones first.

    A piece is round when its roundness (:data:`CAP_ROUNDNESS_MIN`) is at least
    ``roundness_min``.  A mesh with nothing open comes back as it is.
    """
    nothing = np.zeros((0, 3))
    if mesh.is_empty:
        return _Caps(mesh, 0, nothing, np.zeros(0))
    with np.errstate(invalid="ignore"):              # a vertex that is not finite welds to nothing useful
        quantized = np.round(mesh.vertices / max(weld_um, 1e-12)).astype(np.int64)
    _, first, ids = np.unique(quantized, axis=0, return_index=True, return_inverse=True)
    ids = ids.ravel()
    n_ids = len(first)
    f = ids[mesh.faces]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    if not len(f):
        return _Caps(mesh, len(mesh.faces), nothing, np.zeros(0))
    # Every directed edge of every face, as (low id, high id, +1 if it runs low
    # to high else -1): an undirected edge nets to zero when it is closed.
    tail = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    head = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
    low, high = np.minimum(tail, head), np.maximum(tail, head)
    key = low * n_ids + high
    order = np.argsort(key, kind="stable")
    key, direction = key[order], np.where(tail < head, 1, -1)[order]
    run = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    net = np.add.reduceat(direction, run)
    open_edge = net != 0
    if not open_edge.any():
        return _Caps(mesh, len(mesh.faces), nothing, np.zeros(0))
    edge_key, net = key[run][open_edge], net[open_edge]
    low, high = edge_key // n_ids, edge_key % n_ids
    # The surface uses |net| more edges low -> high (net > 0) or high -> low
    # (net < 0); the cap must run the other way, as many times.
    cap_tail = np.where(net > 0, high, low)
    cap_head = np.where(net > 0, low, high)
    times = np.abs(net)
    # Connected pieces of the boundary: each gets its own centroid.
    graph = coo_matrix((np.ones(len(low), dtype=np.int8), (low, high)),
                       shape=(n_ids, n_ids))
    _, label = connected_components(graph, directed=False)
    piece = label[cap_tail]
    pieces, piece = np.unique(piece, return_inverse=True)
    members = np.unique(np.concatenate([cap_tail, cap_head]))
    member_piece = np.searchsorted(pieces, label[members])
    position = mesh.vertices[first]                             # one position per id
    centroid = np.zeros((len(pieces), 3))
    np.add.at(centroid, member_piece, position[members])
    centroid /= np.bincount(member_piece, minlength=len(pieces))[:, None]
    # How round each piece is: the fan's area against the circle of the boundary's length.
    start, stop = position[cap_tail], position[cap_head]
    fan_area = 0.5 * np.linalg.norm(np.cross(stop - start, centroid[piece] - start), axis=1) * times
    length = np.linalg.norm(stop - start, axis=1) * times
    area = np.bincount(piece, weights=fan_area, minlength=len(pieces))
    around = np.bincount(piece, weights=length, minlength=len(pieces))
    with np.errstate(invalid="ignore", divide="ignore"):
        roundness = np.where(around > 0, 4.0 * np.pi * area / around**2, 0.0)
    doubtful_piece = roundness < roundness_min
    # How far a doubtful fan reaches from its centroid.
    reach = np.zeros(len(pieces))
    for end in (start, stop):
        np.maximum.at(reach, piece, np.linalg.norm(end - centroid[piece], axis=1))
    hub = len(mesh.vertices) + piece                            # the new vertex of each edge's piece
    caps = np.repeat(np.column_stack([first[cap_tail], first[cap_head], hub]), times, axis=0)
    doubtful = np.repeat(doubtful_piece[piece], times)
    caps = caps[np.argsort(doubtful, kind="stable")]            # the round pieces' fans first
    trusted = len(mesh.faces) + int((~doubtful).sum())
    capped = Mesh(np.vstack([mesh.vertices, centroid]), np.vstack([mesh.faces, caps]))
    return _Caps(capped, trusted, centroid[doubtful_piece], reach[doubtful_piece])


def close_open_boundaries(mesh: Mesh, *, weld_um: float = WELD_UM) -> Mesh:
    """``mesh`` with every open boundary capped, so that it is a closed surface.

    The ray count of :func:`enclosed_points` reads a point's side of a surface
    from the faces an upward ray crosses.  That is the same answer from every
    direction only for a *closed* surface; where the surface has an open edge a
    ray can slip through the gap without a crossing and a point near it is
    signed by where its ray happens to escape.  The Original pathway is not
    closed (a branch that ends, a piece whose ring has no mate): after welding
    vertices by position, c1178 has 3,282 boundary edges, c21768 3,028 and
    c606 17,098, and the tube, adaptive and wrap builds have thousands too.

    The boundary is read from the directed edges: in a closed surface every
    edge is used once each way (also where interpenetrating pieces share an
    edge), so an undirected edge whose two directions are used a different
    number of times is open.  The open edges are split into connected pieces,
    and each piece is capped with a fan of triangles to its centroid, wound
    against the boundary's own direction.  The result is closed as a chain,
    whatever the shape of the boundary, which makes the ray count the same
    from every direction.  That says nothing about what lies in the gap: the
    fan to the centroid of a long thin slot is a sheet far larger than the slot
    (:data:`CAP_ROUNDNESS_MIN`), and a point under it is on the other side of
    a surface that was never there.  Sign points with
    :func:`enclosed_points_open`, which knows the difference.  A mesh that is
    already closed is returned as it is (the same object).
    """
    return _cap_open_boundaries(mesh, weld_um, 0.0).mesh


#: A triangle whose projection along the ray is thinner than this (the sine of its
#: sharpest corner) is not cast against by :func:`enclosed_points_open`.  The test of
#: :func:`enclosed_points` keeps a triangle when the squared double area of its
#: projection is over 1e-18 in absolute terms, which cancellation noise passes: a
#: triangle exactly edge-on to the ray (the horizontal disc of a cap, seen from the
#: side) came out at 3e-18, its barycentric coordinates were noise over noise, and a
#: ray 0.02 um off its plane "hit" it (a slit fixture, 2026-10-16: 10 of 5,114 points
#: counted 0 or -1 instead of 1 along one axis of four).  A real face thinner than this
#: in projection is hit by fewer than one ray in a million.
SLIVER_SINE = 1e-6


def _crossing_counts(points: np.ndarray, mesh: Mesh, first_faces: int, *, cell_um: float = 1.0,
                     chunk: int = 512, stage: str = "hidden-surface test",
                     sliver: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Signed crossings of an upward ray from each point, split over two sets of faces.

    Returns ``(first, rest)``: for every point the sum, over the faces below
    index ``first_faces`` and over the other faces, of what
    :func:`enclosed_points` adds up (+1 for a face the ray leaves a body
    through, -1 for one it enters through; integer-valued floats, so a sum is
    exact in any order).  The caller decides what a sum means; ``first > 0``
    is :func:`enclosed_points`.  The faces of a cell are visited in index
    order, so the split is one slice.  ``sliver`` is the sine of the corner
    below which a triangle is not cast against (0, the default, keeps every
    triangle :func:`enclosed_points` always kept; see :data:`SLIVER_SINE`).
    """
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    first = np.zeros(len(points))
    rest = np.zeros(len(points))
    if mesh.is_empty or not len(points):
        return first, rest
    # A ray exactly through a vertex or along an edge hits every triangle that
    # shares it and miscounts; a sub-nanometer lateral nudge makes that a
    # measure-zero event instead of a certainty for a point under a pole.
    points = points + np.array([3.7e-7, 2.3e-7, 0.0])
    tri = mesh.vertices[mesh.faces]                       # (F, 3, 3)
    lo_xy = tri[:, :, :2].min(axis=1)
    hi_xy = tri[:, :, :2].max(axis=1)
    origin = lo_xy.min(axis=0) - 1e-6
    c0 = np.floor((lo_xy - origin) / cell_um).astype(np.int64)
    c1 = np.floor((hi_xy - origin) / cell_um).astype(np.int64)
    ny = int(c1[:, 1].max()) + 1
    spans = c1 - c0 + 1
    counts = spans[:, 0] * spans[:, 1]
    tri_ids = np.repeat(np.arange(len(tri)), counts)
    k = np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)
    sx = np.repeat(spans[:, 0], counts)
    cell = ((np.repeat(c0[:, 0], counts) + k % sx) * ny
            + np.repeat(c0[:, 1], counts) + k // sx)
    order = np.argsort(cell, kind="stable")
    cell, tri_ids = cell[order], tri_ids[order]
    n_cells = int(cell[-1]) + 1
    starts = np.searchsorted(cell, np.arange(n_cells + 1))

    pc = np.floor((points[:, :2] - origin) / cell_um).astype(np.int64)
    pcell = pc[:, 0] * ny + pc[:, 1]
    valid = ((pc >= 0).all(axis=1) & (pc[:, 0] <= int(c1[:, 0].max()))
             & (pc[:, 1] < ny) & (pcell < n_cells))
    pcell = np.where(valid, pcell, -1)
    porder = np.argsort(pcell, kind="stable")
    sorted_cells = pcell[porder]
    splits = np.flatnonzero(np.diff(sorted_cells)) + 1
    groups = np.split(porder, splits)
    for number, group in enumerate(groups):
        report(stage, number, len(groups))
        c = int(pcell[group[0]])
        if c < 0:
            continue
        ids = tri_ids[starts[c]:starts[c + 1]]
        if not len(ids):
            continue
        T = tri[ids]
        A, B, C = T[:, 0], T[:, 1], T[:, 2]
        v0 = C[:, :2] - A[:, :2]
        v1 = B[:, :2] - A[:, :2]
        d00 = np.einsum("ij,ij->i", v0, v0)
        d01 = np.einsum("ij,ij->i", v0, v1)
        d11 = np.einsum("ij,ij->i", v1, v1)
        denom = d00 * d11 - d01 * d01
        ok = np.abs(denom) > 1e-18
        if sliver > 0.0:
            ok &= np.abs(denom) > sliver * sliver * d00 * d11
        if not ok.any():
            continue
        split_at = int(np.searchsorted(ids[ok], first_faces))      # ids ascend: the first set comes first
        A, B, C = A[ok], B[ok], C[ok]
        v0, v1, d00, d01, d11, denom = v0[ok], v1[ok], d00[ok], d01[ok], d11[ok], denom[ok]
        # Sign of the face normal's z component: the orientation of the
        # projected triangle (B - A) x (C - A).
        facing = np.sign(v1[:, 0] * v0[:, 1] - v1[:, 1] * v0[:, 0])
        step = max(1, min(chunk, _block(len(A), ENCLOSURE_PAIR_BYTES)))
        for s in range(0, len(group), step):
            sel = group[s:s + step]
            P = points[sel]
            v2 = P[:, None, :2] - A[None, :, :2]
            d20 = np.einsum("ijk,jk->ij", v2, v0)
            d21 = np.einsum("ijk,jk->ij", v2, v1)
            u = (d11 * d20 - d01 * d21) / denom
            v = (d00 * d21 - d01 * d20) / denom
            hit = (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0)
            z_plane = (A[None, :, 2] + u * (C[None, :, 2] - A[None, :, 2])
                       + v * (B[None, :, 2] - A[None, :, 2]))
            above = hit & (z_plane > P[:, None, 2])
            signed = above * facing[None, :]
            first[sel] = signed[:, :split_at].sum(axis=1)
            rest[sel] = signed[:, split_at:].sum(axis=1)
    report(stage, len(groups), len(groups))
    return first, rest


def enclosed_points(points: np.ndarray, mesh: Mesh, *, cell_um: float = 1.0,
                    chunk: int = 512, stage: str = "hidden-surface test") -> np.ndarray:
    """True where a point lies inside the mesh's solid.

    An upward ray from the point is cast through the mesh and each triangle
    it crosses counts +1 when the ray leaves a body through it (the face's
    normal points up) and -1 when it enters one.  Outside every body the sum
    is zero; inside k overlapping bodies it is k.  The count, not the parity:
    the assembled pathways are unions of interpenetrating closed pieces, and
    a point inside two of them crosses an even number of triangles on its way
    out.  Faces must be wound outward, which every builder here guarantees
    (:meth:`Mesh.fix_winding`).  The surface should be closed: where it has an
    open edge the count depends on the direction of the ray, and only an
    upward one is cast (:func:`enclosed_points_open` is the test for a mesh
    that may have open ends, such as the pathways).  Triangles are binned by
    their xy footprint in ``cell_um`` cells so each point is tested against
    its own cell's triangles only; ``chunk`` bounds the points-by-triangles
    block, and so does :data:`KERNEL_BUDGET_BYTES` (a block never changes an
    answer).  ``stage`` names the work, counted in cells, for
    :func:`sbfsem.progress.report`.
    """
    first, _ = _crossing_counts(points, mesh, len(mesh.faces), cell_um=cell_um, chunk=chunk, stage=stage)
    return first > 0


#: Bytes charged per (point, triangle) pair of :func:`_solid_angle_sums`.  The live
#: temporaries are 152 bytes a pair (tracemalloc, 2026-10-16: 3,000 to 17,000
#: fan triangles), but the resident size grows by about 1,200 a pair at 64-point
#: blocks and 1,500 at 613 (the allocator keeps what it freed: 126 MB and 911 MB
#: for 30,000 points against Inferior c21768's 1,642 fan triangles), and small
#: blocks are also faster (2.3 s against 4.4 s).  Charged by the resident growth.
SOLID_ANGLE_PAIR_BYTES = 1300

#: The most times a ray may cross one fan in the same direction, which bounds how much
#: of the view one fan can fill (see :func:`_fan_reach`).  The slot of Inferior
#: c21768 winds 3.1 times about the points it surrounds; nothing farther than its
#: three-layer reach has a share above 0.001 (measured, 2026-10-16).
FAN_LAYERS = 3


def _solid_angle_sums(points: np.ndarray, tri: np.ndarray, *, stage: str) -> np.ndarray:
    """Sum over the triangles ``tri`` (F, 3, 3) of the solid angle each subtends at each point.

    The signed solid angle of Van Oosterom and Strackee, as a fraction of the
    sphere: for a closed surface wound outward the sum is 1 at a point inside it
    and 0 at one outside, and for a patch of it the share of the view the patch
    fills, positive where the point is behind it.  Blocks of points are bounded by
    :data:`KERNEL_BUDGET_BYTES`; ``stage`` is counted in blocks.
    """
    out = np.zeros(len(points))
    if not len(points) or not len(tri):
        return out
    ax0, ay0, az0 = tri[:, 0, 0], tri[:, 0, 1], tri[:, 0, 2]
    bx0, by0, bz0 = tri[:, 1, 0], tri[:, 1, 1], tri[:, 1, 2]
    cx0, cy0, cz0 = tri[:, 2, 0], tri[:, 2, 1], tri[:, 2, 2]
    step = _block(len(tri), SOLID_ANGLE_PAIR_BYTES)
    blocks = range(0, len(points), step)
    for number, s in enumerate(blocks):
        report(stage, number, len(blocks))
        p = points[s:s + step]
        px, py, pz = p[:, 0:1], p[:, 1:2], p[:, 2:3]
        ax, ay, az = ax0 - px, ay0 - py, az0 - pz
        bx, by, bz = bx0 - px, by0 - py, bz0 - pz
        cx, cy, cz = cx0 - px, cy0 - py, cz0 - pz
        la = np.sqrt(ax * ax + ay * ay + az * az)
        lb = np.sqrt(bx * bx + by * by + bz * bz)
        lc = np.sqrt(cx * cx + cy * cy + cz * cz)
        triple = (ax * (by * cz - bz * cy) + ay * (bz * cx - bx * cz) + az * (bx * cy - by * cx))
        ab = ax * bx + ay * by + az * bz
        bc = bx * cx + by * cy + bz * cz
        ca = cx * ax + cy * ay + cz * az
        out[s:s + step] = (2.0 * np.arctan2(triple, la * lb * lc + ab * lc + bc * la + ca * lb)).sum(axis=1)
    report(stage, len(blocks), len(blocks))
    return out / (4.0 * np.pi)


def _fan_reach(points: np.ndarray, hubs: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """Most of the view the fans can fill at each point, an upper bound.

    A fan lies inside the ball about its centroid that reaches its farthest
    boundary vertex, and from a point at distance d outside a ball of radius r
    that ball fills at most ``(1 - sqrt(1 - r^2 / d^2)) / 2`` of the view (all of
    half of it at the surface).  A ray may cross a fan up to :data:`FAN_LAYERS` times
    the same way, so the fans' share is at most that many times the sum over the
    balls.  A point where the bound is under one half cannot have its side changed
    by the fans' own solid angle.
    """
    reach = np.zeros(len(points))
    for hub, radius in zip(hubs, radii, strict=True):
        d = np.linalg.norm(points - hub, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(d > radius, radius / d, 1.0)
        reach += 0.5 * (1.0 - np.sqrt(1.0 - ratio * ratio))
    return FAN_LAYERS * reach


def enclosed_points_open(points: np.ndarray, mesh: Mesh, *, stage: str = "hidden-surface test",
                         roundness_min: float = CAP_ROUNDNESS_MIN) -> tuple[np.ndarray, int]:
    """True where a point lies inside the solid of a mesh that may have open ends.

    Returns ``(inside, overruled)``.  The ray count of :func:`enclosed_points`
    is not reliable near an open end: a ray can slip through the gap, so the
    answer depends on which way it is cast (against the Original pathway, 0.2
    to 2.3 % of a Voxel remesh's vertices change side between the upward count
    and a sideways one).  Closing every boundary piece with a fan to its
    centroid (:func:`close_open_boundaries`) makes the surface closed and the
    count the same from every direction, but a fan is a stand-in, and the
    count cannot say whether it stands in for something real.

    * A *round* piece (:data:`CAP_ROUNDNESS_MIN`: the end of a branch, a ring
      with no mate) is closed by its fan, and a point is inside where the
      count over that surface is positive.
    * Any other piece is long and thin, and its fan is a sheet that is not
      there (Inferior c21768: a boundary 382 um round around 43 um2 gets 1,563
      um2, 38 % of the whole surface; 1,347 vertices were put inside).  A
      point that the fan could reach is placed by the *generalized winding
      number* of the surface as it is, the share of the view it fills, inside
      above one half.  That is the count of the closed surface (an integer, the
      same from every direction) minus the solid angle of the fans that were
      not believed (:func:`_solid_angle_sums`), worked out only for the points
      those fans could change (:func:`_fan_reach`); a mesh whose open pieces
      are all round costs no more than the count.

    ``overruled`` is how many points the winding number put on the other side
    from the count.  Against the winding number of the open mesh, computed
    apart from this code, the sign is wrong on 0 to 0.0005 % of a Voxel
    remesh's vertices against the Original (the bare upward count: 0.07 to
    0.8 %, one fan per piece: 0 to 0.67 %), and the same from +z, +x, +y and -z
    but for a vertex that lies on the surface; ``docs/backend.md`` has the
    table.  An open surface does not define what it encloses: where its winding
    number is between 0.2 and 0.8 (a long crack in the surface) the side is a
    convention, and this one does not depend on the direction of a ray.
    ``roundness_min`` is the roundness a piece needs for its fan to be believed
    (0 believes every fan, which is :func:`close_open_boundaries` and the count;
    infinity believes none, which is the winding number everywhere a fan could
    reach and costs about twice as much).  ``stage`` names the count and
    ``stage`` + " (open ends)" the winding numbers, both counted for
    :func:`sbfsem.progress.report`.
    """
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    caps = _cap_open_boundaries(mesh, WELD_UM, roundness_min)
    if caps.mesh is mesh:
        return enclosed_points(points, mesh, stage=stage), 0
    kept, fans = _crossing_counts(points, caps.mesh, caps.trusted, stage=stage, sliver=SLIVER_SINE)
    winding = kept + fans                          # the closed surface's: an integer, from every direction
    inside = winding > 0
    overruled = 0
    if len(caps.hubs):
        near = np.flatnonzero(_fan_reach(points, caps.hubs, caps.radii) >= 0.5)
        if len(near):
            tri = caps.mesh.vertices[caps.mesh.faces[caps.trusted:]]
            share = _solid_angle_sums(points[near], tri, stage=f"{stage} (open ends)")
            side = (winding[near] - share) > 0.5
            overruled = int((side != inside[near]).sum())
            inside[near] = side
    return inside, overruled


def annotation_deviation(
    mesh: Mesh,
    centers: np.ndarray,
    radii: np.ndarray,
    polygons: list[tuple[np.ndarray, float]] | None = None,
    *,
    samples: int = 60_000,
    seed: int = 0,
) -> AnnotationDeviation | None:
    """Absolute deviation between a rendered surface and its annotations.

    ``centers``/``radii`` are the cell's disc annotations in microns;
    ``polygons`` any closed-curve outlines as ``(ring_xy, z_um)`` pairs.
    Returns ``None`` when the cell has no annotations to hold the render to.
    """
    polygons = polygons or []
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    radii = np.asarray(radii, dtype=float).ravel()
    if mesh.is_empty or (not len(centers) and not polygons):
        return None

    # Direction 1: the rendered surface, area-uniform, against the
    # annotations.  Only the random area samples — the appended mesh vertices
    # would over-weight densely tessellated regions in an area integral.
    report("samples: sampling the surface")
    pts, normals = sample_surface(mesh, samples, seed=seed)
    area_pts = pts[:samples]
    d_surface = nearest_annotation(area_pts, centers, radii, polygons, stage="samples").dist

    # Hidden surface: a hair outside the sample, along its normal, is still
    # enclosed by the mesh -- the sample faces another part of the render,
    # not space.
    probe = area_pts + PROBE_UM * normals[:samples]
    hidden = enclosed_points(probe, mesh, stage="samples: hidden-surface test")
    exterior = d_surface[~hidden]

    # Direction 2: every annotation's rim against the surface, measured to
    # the same sample set the mesh-to-mesh diff uses.
    report("rims: distance to the surface")
    spacing = 0.5 * float(np.sqrt(mesh.surface_area() / len(pts)))
    rims, _owner = _rim_points(centers, radii, polygons, max(spacing, 0.25))
    d_rim = cKDTree(pts).query(rims, workers=-1)[0] if len(rims) \
        else np.zeros(0)
    stride = max(1, -(-len(rims) // RIM_ENCLOSURE_MAX))
    rim_hidden = enclosed_points(rims[::stride], mesh, stage="rims: hidden-surface test") \
        if len(rims) else np.zeros(0, dtype=bool)
    d_rim_ext = d_rim[::stride][~rim_hidden]

    pooled = np.concatenate([d_surface, d_rim])
    return AnnotationDeviation(
        mean_um=float(pooled.mean()),
        surface_um=float(d_surface.mean()),
        annotation_um=float(d_rim.mean()) if len(d_rim) else 0.0,
        p95_um=float(np.percentile(pooled, 95)),
        max_um=float(pooled.max()),
        excess_volume_um3=float(d_surface.mean() * mesh.surface_area()),
        sample_spacing_um=spacing,
        n_discs=int(len(centers)),
        n_curves=int(len(polygons)),
        exterior_um=float(exterior.mean()) if len(exterior) else None,
        hidden_fraction=float(hidden.mean()),
        annotation_exterior_um=float(d_rim_ext.mean()) if len(d_rim_ext) else None,
    )


def vertex_areas(mesh: Mesh) -> np.ndarray:
    """The area each vertex stands for: a third of every face around it.

    The areas add up to the mesh's surface area, so a statistic weighted by
    them is a statistic over the *surface*, not over its tessellation: a
    region meshed ten times as finely has ten times the vertices but the same
    area, and counts the same.  Vertices no face uses weigh nothing.
    """
    out = np.zeros(mesh.n_vertices)
    if not len(mesh.faces):
        return out
    third = mesh.face_areas() / 3.0
    for corner in range(3):
        out += np.bincount(mesh.faces[:, corner], weights=third, minlength=mesh.n_vertices)
    return out


def weighted_quantiles(values: np.ndarray, weights: np.ndarray, probs: np.ndarray) -> np.ndarray:
    """Quantiles of ``values`` in which each value counts for its weight.

    Each value sits at the middle of its own share of the cumulative weight and
    the quantile is read between neighbors (with equal weights that is the
    plotting-position quantile, not numpy's default, which differs by half a
    value at the ends).  Without any weight the values count equally; without
    values every quantile is 0.
    """
    values = np.asarray(values, dtype=float).ravel()
    weights = np.asarray(weights, dtype=float).ravel()
    probs = np.asarray(probs, dtype=float)
    if not len(values):
        return np.zeros(probs.shape)
    if not (weights > 0).any():
        weights = np.ones_like(values)
    held = weights > 0
    order = np.argsort(values[held], kind="stable")
    v, w = values[held][order], weights[held][order]
    return np.interp(probs, (np.cumsum(w) - 0.5 * w) / w.sum(), v)


def _group_max(groups: np.ndarray, values: np.ndarray, size: int) -> np.ndarray:
    """The largest of ``values`` in each group ``0 .. size - 1`` (0 for an empty group)."""
    out = np.zeros(size)
    if not len(values):
        return out
    order = np.argsort(groups, kind="stable")
    sorted_groups = groups[order]
    starts = np.flatnonzero(np.r_[True, np.diff(sorted_groups) != 0])
    out[sorted_groups[starts]] = np.maximum.reduceat(values[order], starts)
    return out


def _group_mean(groups: np.ndarray, values: np.ndarray, size: int,
                weights: np.ndarray | None = None) -> np.ndarray:
    """The (weighted) mean of ``values`` in each group (0 for an empty group)."""
    weight = np.ones(len(values)) if weights is None else weights
    total = np.bincount(groups, weights=weight, minlength=size)
    summed = np.bincount(groups, weights=weight * values, minlength=size)
    return np.divide(summed, total, out=np.zeros(size), where=total > 0)


#: Bins of the surface histogram, and how far it reaches in units of the
#: color scale (``DeviationMap.scale_um``, the pooled 98th percentile).
HIST_BINS = 40
HIST_REACH = 1.5
#: Height of a bin of the error-versus-Z profile (um), and the most bins one
#: carries (a longer cell gets wider bins).
Z_PROFILE_STEP_UM = 1.0
Z_PROFILE_MAX_BINS = 4096
#: Worst spots: how far apart two may be, and how many are named.
SPOT_RADIUS_UM = 2.0
SPOT_COUNT = 12
#: Most cubes looked at in the search for spots (the rest are weaker still).
SPOT_SCAN_MAX = 5000


@dataclass(frozen=True)
class SurfaceStats:
    """The surface's error, weighted by area (see :func:`vertex_areas`).

    ``quantiles_um`` holds the 0th to 100th percentile of the per-vertex
    distance, area-weighted; ``hist_share`` the share of the surface area in
    each of :data:`HIST_BINS` equal bins between ``hist_edges_um``, and
    ``tail_share`` the share beyond the last edge.  ``excess_um3`` is area
    times distance summed over the surface -- the same estimate of the volume
    the render claims beyond any annotation as ``excess_volume_um3``, from the
    vertices instead of 60,000 random samples.
    """

    area_um2: float
    mean_um: float
    excess_um3: float
    quantiles_um: np.ndarray
    hist_edges_um: np.ndarray
    hist_share: np.ndarray
    tail_share: float


@dataclass(frozen=True)
class AnnotationErrors:
    """What each annotation answers for, one row per annotation in the order
    :func:`nearest_annotation` numbers them: the discs, then the outlines.

    ``kind`` is 0 for a disc and 1 for an outline; ``xyz_um`` the disc's center
    or the outline's centroid; ``radius_um`` the disc's radius or the outline's
    equivalent (the radius of the circle of the same area).  ``area_share`` is
    the share of the surface area whose nearest annotation this is, with the
    mean and the largest distance over that surface.  ``rim_mean_um`` and
    ``rim_max_um`` are the distances from this annotation's rim to the render;
    ``rim_enclosed_share`` the share of its rim points the render encloses (NaN
    where none was tested).
    """

    kind: np.ndarray
    xyz_um: np.ndarray
    radius_um: np.ndarray
    area_share: np.ndarray
    surface_mean_um: np.ndarray
    surface_max_um: np.ndarray
    rim_mean_um: np.ndarray
    rim_max_um: np.ndarray
    rim_enclosed_share: np.ndarray


@dataclass(frozen=True)
class ZProfile:
    """Error along the cell, in bins of ``step_um`` from ``z0_um``: the surface
    area in each bin, its area-weighted mean and 95th percentile, the mean
    distance of the annotation rims there, and how many annotations sit there."""

    z0_um: float
    step_um: float
    area_um2: np.ndarray
    mean_um: np.ndarray
    p95_um: np.ndarray
    rim_mean_um: np.ndarray
    n_annotations: np.ndarray


@dataclass(frozen=True)
class ErrorSpot:
    """A place worth looking at: the surface within :data:`SPOT_RADIUS_UM` of
    ``xyz_um`` (a surface vertex) carries ``excess_um3`` of the render's excess
    volume (``share`` of all of it), at ``mean_um`` on average and ``max_um`` at
    worst; ``owner`` is the annotation that accounts for most of it."""

    xyz_um: tuple[float, float, float]
    owner: int
    excess_um3: float
    share: float
    mean_um: float
    max_um: float


@dataclass(frozen=True)
class RimBias:
    """Which way the render is wrong, from the annotation rims it was tested on.

    A rim point the render encloses lies inside the body: the render is fatter
    than that annotation there, by the point's distance to the surface.  One it
    does not enclose lies outside: the render stops short.  ``signed_mean_um``
    counts the first as positive and the second as negative, so positive means
    the render claims more than the annotations support (the convention of the
    Difference).  The surface itself has no side: a disc is flat.
    """

    n_tested: int
    enclosed_share: float
    enclosed_mean_um: float | None
    outside_mean_um: float | None
    signed_mean_um: float


def _sampling_floor(centers: np.ndarray, polygons: list[tuple[np.ndarray, float]]) -> float:
    """The error a perfect render still shows between sections: half the median
    gap between the annotated sections (the Z of a discs-only cell's discs and
    of every outline).  0 when the annotations lie in one plane."""
    z = np.unique(np.concatenate([centers[:, 2], [float(zz) for _, zz in polygons]]))
    gaps = np.diff(z)
    gaps = gaps[gaps > 0]
    return 0.5 * float(np.median(gaps)) if len(gaps) else 0.0


def _surface_stats(error: np.ndarray, area: np.ndarray, scale_um: float) -> SurfaceStats:
    total = float(area.sum())
    edges = np.linspace(0.0, HIST_REACH * scale_um, HIST_BINS + 1)
    weight = area if total > 0 else np.ones_like(area)
    wsum = float(weight.sum())
    share, _ = np.histogram(error, bins=edges, weights=weight)
    return SurfaceStats(
        area_um2=total,
        mean_um=float((weight * error).sum() / wsum) if wsum > 0 else 0.0,
        excess_um3=float((area * error).sum()),
        quantiles_um=weighted_quantiles(error, area, np.linspace(0.0, 1.0, 101)),
        hist_edges_um=edges,
        hist_share=share / wsum if wsum > 0 else share,
        tail_share=float(weight[error > edges[-1]].sum() / wsum) if wsum > 0 else 0.0,
    )


def _annotation_errors(
    centers: np.ndarray, radii: np.ndarray, polygons: list[tuple[np.ndarray, float]],
    owner: np.ndarray, error: np.ndarray, area: np.ndarray,
    rim_owner: np.ndarray, rim_error: np.ndarray,
    shown_owner: np.ndarray, shown_enclosed: np.ndarray | None,
) -> AnnotationErrors:
    n_discs, size = len(centers), len(centers) + len(polygons)
    xyz = np.zeros((size, 3))
    radius = np.zeros(size)
    xyz[:n_discs], radius[:n_discs] = centers, radii
    for k, (ring, z) in enumerate(polygons):
        ring = np.asarray(ring, dtype=float).reshape(-1, 2)
        if len(ring):
            x, y = ring[:, 0], ring[:, 1]
            shoelace = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
            xyz[n_discs + k] = (x.mean(), y.mean(), z)
            radius[n_discs + k] = float(np.sqrt(shoelace / np.pi))
        else:
            xyz[n_discs + k, 2] = z
    total = float(area.sum())
    share = np.bincount(owner, weights=area, minlength=size)
    if shown_enclosed is not None:
        tested = np.bincount(shown_owner, minlength=size)
        enclosed = np.bincount(shown_owner, weights=shown_enclosed.astype(float), minlength=size)
        enclosed_share = np.divide(enclosed, tested, out=np.full(size, np.nan), where=tested > 0)
    else:
        enclosed_share = np.full(size, np.nan)
    return AnnotationErrors(
        kind=(np.arange(size) >= n_discs).astype(np.int8),
        xyz_um=xyz,
        radius_um=radius,
        area_share=share / total if total > 0 else share,
        surface_mean_um=_group_mean(owner, error, size, weights=area),
        surface_max_um=_group_max(owner[area > 0], error[area > 0], size),
        rim_mean_um=_group_mean(rim_owner, rim_error, size),
        rim_max_um=_group_max(rim_owner, rim_error, size),
        rim_enclosed_share=enclosed_share,
    )


def _z_profile(
    vertex_z: np.ndarray, error: np.ndarray, area: np.ndarray,
    rim_z: np.ndarray, rim_error: np.ndarray, annotation_z: np.ndarray,
) -> ZProfile:
    lo = float(min(vertex_z.min(), rim_z.min() if len(rim_z) else np.inf, annotation_z.min()))
    hi = float(max(vertex_z.max(), rim_z.max() if len(rim_z) else -np.inf, annotation_z.max()))
    step = Z_PROFILE_STEP_UM
    z0 = float(np.floor(lo / step) * step)
    if (hi - z0) / step >= Z_PROFILE_MAX_BINS:
        step = (hi - z0) / (Z_PROFILE_MAX_BINS - 1)
        z0 = lo
    size = int((hi - z0) // step) + 1

    def binned(z: np.ndarray) -> np.ndarray:
        return np.clip(np.floor((z - z0) / step).astype(np.int64), 0, size - 1)

    bin_of = binned(vertex_z)
    p95 = np.zeros(size)
    order = np.argsort(bin_of, kind="stable")
    in_order = bin_of[order]
    starts = np.flatnonzero(np.r_[True, np.diff(in_order) != 0])
    for first, last in zip(starts, np.r_[starts[1:], len(order)], strict=True):
        part = order[first:last]
        p95[in_order[first]] = weighted_quantiles(error[part], area[part], np.array([0.95]))[0]
    return ZProfile(
        z0_um=z0,
        step_um=step,
        area_um2=np.bincount(bin_of, weights=area, minlength=size),
        mean_um=_group_mean(bin_of, error, size, weights=area),
        p95_um=p95,
        rim_mean_um=_group_mean(binned(rim_z), rim_error, size) if len(rim_z) else np.zeros(size),
        n_annotations=np.bincount(binned(annotation_z), minlength=size),
    )


def _error_spots(
    xyz: np.ndarray, error: np.ndarray, area: np.ndarray, owner: np.ndarray,
    *, count: int = SPOT_COUNT, radius_um: float = SPOT_RADIUS_UM,
) -> tuple[ErrorSpot, ...]:
    """The places that carry the most excess volume, no two closer than ``radius_um``.

    Each vertex's excess is its area times its distance.  The vertices are
    binned in cubes of half the radius and every cube scored by the excess in
    the 3 x 3 x 3 cubes around it; the best-scoring cube names a spot (the
    surface vertex nearest the excess-weighted centroid of those cubes), cubes
    whose spot would fall within ``radius_um`` of one already named are passed
    over, and so on until ``count`` spots.  Each spot then takes the vertices
    within ``radius_um`` of it that no better spot took.
    """
    excess = area * error
    total = float(excess.sum())
    if not len(xyz) or not total > 0:
        return ()
    h = radius_um / 2.0
    cube = np.floor((xyz - xyz.min(axis=0)) / h).astype(np.int64) + 1     # one cube of padding all round
    dims = cube.max(axis=0) + 2
    key = (cube[:, 0] * dims[1] + cube[:, 1]) * dims[2] + cube[:, 2]
    cubes, at = np.unique(key, return_inverse=True)
    mass = np.bincount(at, weights=excess, minlength=len(cubes))
    moment = [np.bincount(at, weights=excess * xyz[:, k], minlength=len(cubes)) for k in range(3)]
    score = np.zeros(len(cubes))
    centroid = np.zeros((len(cubes), 3))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                want = cubes + (dx * dims[1] + dy) * dims[2] + dz
                pos = np.minimum(np.searchsorted(cubes, want), len(cubes) - 1)
                hit = cubes[pos] == want
                score += np.where(hit, mass[pos], 0.0)
                for k in range(3):
                    centroid[:, k] += np.where(hit, moment[k][pos], 0.0)
    tree = cKDTree(xyz)
    centers: list[int] = []
    for scanned, number in enumerate(np.argsort(-score, kind="stable")):
        if len(centers) == count or not score[number] > 0 or scanned == SPOT_SCAN_MAX:
            break
        vertex = int(tree.query(centroid[number] / score[number])[1])
        if centers and np.min(np.linalg.norm(xyz[centers] - xyz[vertex], axis=1)) < radius_um:
            continue
        centers.append(vertex)
    taken = np.zeros(len(xyz), dtype=bool)
    spots: list[ErrorSpot] = []
    for vertex in centers:
        members = np.asarray(tree.query_ball_point(xyz[vertex], radius_um), dtype=np.int64)
        members = members[~taken[members]]
        taken[members] = True
        if not len(members):
            continue
        held = float(excess[members].sum())
        by_owner = np.bincount(owner[members], weights=excess[members])
        spots.append(ErrorSpot(
            xyz_um=(float(xyz[vertex, 0]), float(xyz[vertex, 1]), float(xyz[vertex, 2])),
            owner=int(by_owner.argmax()),
            excess_um3=held,
            share=held / total,
            mean_um=float(held / area[members].sum()) if area[members].sum() > 0 else 0.0,
            max_um=float(error[members].max()),
        ))
    spots.sort(key=lambda spot: -spot.excess_um3)
    return tuple(spots)


def _rim_bias(rim_error: np.ndarray, enclosed: np.ndarray) -> RimBias:
    n = len(rim_error)
    fat, thin = rim_error[enclosed], rim_error[~enclosed]
    return RimBias(
        n_tested=int(n),
        enclosed_share=float(enclosed.mean()) if n else 0.0,
        enclosed_mean_um=float(fat.mean()) if len(fat) else None,
        outside_mean_um=float(thin.mean()) if len(thin) else None,
        signed_mean_um=float((fat.sum() - thin.sum()) / n) if n else 0.0,
    )


@dataclass(frozen=True)
class DeviationMap:
    """The two halves of :class:`AnnotationDeviation`, point by point.

    ``vertex_um`` is every mesh vertex's distance to the nearest annotation
    (what ``surface_um`` averages, over area samples); ``rim_xyz`` /
    ``rim_um`` are points along the annotations' rims with each one's
    distance to the rendered surface (what ``annotation_um`` averages).
    ``rim_stride`` says how the rim points were thinned for display: every
    ``rim_stride``-th of ``n_rims``.

    The rest says how the error arises, and is empty (``None``) where the map
    was built without it.  ``axial_um`` is each vertex's gap along Z to its
    nearest annotation (the sideways part is ``sqrt(vertex_um**2 - axial_um**2)``)
    and ``owner`` which annotation that is, numbered as :func:`nearest_annotation`
    does.  ``rim_enclosed`` says, for each displayed rim point, whether the
    render encloses it (see :class:`RimBias`).  ``surface`` and ``z_profile``
    are the surface's error by area and along Z, ``annotations`` a row for each
    annotation, ``spots`` the worst places, and ``floor_um`` the error a perfect
    render still shows between sections.
    """

    vertex_um: np.ndarray
    rim_xyz: np.ndarray
    rim_um: np.ndarray
    rim_stride: int
    n_rims: int
    scale_um: float
    axial_um: np.ndarray | None = None
    owner: np.ndarray | None = None
    rim_enclosed: np.ndarray | None = None
    floor_um: float = 0.0
    surface: SurfaceStats | None = None
    annotations: AnnotationErrors | None = None
    z_profile: ZProfile | None = None
    spots: tuple[ErrorSpot, ...] = ()
    bias: RimBias | None = None


def deviation_map(
    mesh: Mesh,
    centers: np.ndarray,
    radii: np.ndarray,
    polygons: list[tuple[np.ndarray, float]] | None = None,
    *,
    samples: int = 60_000,
    seed: int = 0,
    rim_sides: bool = True,
) -> DeviationMap | None:
    """Where a rendered surface and its annotations disagree, for drawing.

    The same annotations as :func:`annotation_deviation`, in the same two
    directions, kept per point instead of averaged.  The rim half is the same
    numbers: the same rim points against the same surface samples (same
    ``samples`` and ``seed``).  The surface half is not: it is measured at every
    vertex of the mesh (so it can be colored on it), where
    :func:`annotation_deviation` measures area-uniform random samples of the
    surface: ``vertex_um`` counts every vertex alike and ``surface.mean_um``
    weights each by the area it stands for, and neither mean is
    :class:`AnnotationDeviation`'s ``surface_um``, the area-uniform one that
    is the headline.  ``scale_um`` is
    the 98th percentile of both sets pooled: a color scale that a few stray
    points do not flatten.  ``rim_sides`` also tests which displayed rim points
    the mesh encloses (seconds on a large mesh); everything else the map
    carries beyond the two distances is cheap.
    """
    polygons = polygons or []
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    radii = np.asarray(radii, dtype=float).ravel()
    if mesh.is_empty or (not len(centers) and not polygons):
        return None
    nearest = nearest_annotation(mesh.vertices, centers, radii, polygons, stage="vertices")
    vertex_um = nearest.dist

    report("rims: sampling the surface")
    pts, _normals = sample_surface(mesh, samples, seed=seed)
    spacing = 0.5 * float(np.sqrt(mesh.surface_area() / len(pts)))
    rims, rim_owner = _rim_points(centers, radii, polygons, max(spacing, 0.25))
    d_rim = cKDTree(pts).query(rims, workers=-1)[0] if len(rims) else np.zeros(0)
    stride = max(1, -(-len(rims) // RIM_DISPLAY_MAX))
    pooled = np.concatenate([vertex_um, d_rim])
    scale = float(max(np.percentile(pooled, 98), 1e-6))
    shown = rims[::stride]

    enclosed = None
    if rim_sides and len(shown):
        enclosed = enclosed_points(shown, mesh, stage="rims: which side of the surface")
    report("summarizing the error")
    area = vertex_areas(mesh)
    owner = nearest.owner
    annotation_z = np.concatenate([centers[:, 2], [float(z) for _, z in polygons]])
    return DeviationMap(
        vertex_um=vertex_um,
        rim_xyz=shown,
        rim_um=d_rim[::stride],
        rim_stride=stride,
        n_rims=int(len(rims)),
        scale_um=scale,
        axial_um=nearest.axial,
        owner=owner.astype(np.int32),
        rim_enclosed=enclosed,
        floor_um=_sampling_floor(centers, polygons),
        surface=_surface_stats(vertex_um, area, scale),
        annotations=_annotation_errors(centers, radii, polygons, owner, vertex_um, area,
                                       rim_owner, d_rim, rim_owner[::stride], enclosed),
        z_profile=_z_profile(mesh.vertices[:, 2], vertex_um, area, rims[:, 2], d_rim, annotation_z),
        spots=_error_spots(mesh.vertices, vertex_um, area, owner),
        bias=_rim_bias(d_rim[::stride], enclosed) if enclosed is not None else None,
    )


def _area_stats(signed: np.ndarray, area: np.ndarray) -> AreaStats:
    """:class:`AreaStats` of a signed field whose vertices stand for ``area``."""
    magnitude = np.abs(signed)
    # No face anywhere (a degenerate mesh): every vertex counts the same.
    weight = area if float(area.sum()) > 0 else np.ones_like(area)
    total = float(weight.sum())
    p95, p98 = weighted_quantiles(magnitude, weight, np.array([0.95, 0.98]))
    return AreaStats(
        mean_abs_um=float((weight * magnitude).sum() / total),
        rms_um=float(np.sqrt((weight * signed**2).sum() / total)),
        p95_abs_um=float(p95),
        p98_abs_um=float(p98),
        max_abs_um=float(magnitude[weight > 0].max()),
        mean_signed_um=float((weight * signed).sum() / total),
    )


def _signed_distance(
    source: Mesh, reference: Mesh, *, samples: int, seed: int, sign: str, name: str
) -> tuple[np.ndarray, float, int, int]:
    """Signed distance from every vertex of ``source`` to ``reference``'s surface.

    Returns the distances, the spacing of the reference's surface samples, how
    many samples there are, and (by ``"enclosure"``) how many vertices the
    winding number of an open reference put on the other side from the count
    over its capped surface (:func:`enclosed_points_open`; 0 by ``"normal"``).
    ``name`` is the reference's letter in the stage names the enclosure test
    reports.
    """
    report(f"difference: sampling mesh {name}")
    points, normals = sample_surface(reference, samples, seed=seed)
    distance, index = cKDTree(points).query(source.vertices, workers=-1)
    overruled = 0
    if sign == "enclosure":
        # Outside the reference's solid is positive, inside is negative.  The
        # ray count of enclosed_points handles a reference assembled from
        # interpenetrating closed pieces, which the normal of its nearest
        # sample does not (a sample on a buried piece faces the wrong way).
        # The Original pathway and the other piece-assembled builds are not
        # closed, and on an open surface the count depends on the direction of
        # the ray (0.2 to 2.3 % of a Voxel remesh's vertices flip with it
        # against the Original, 2026-10-16): enclosed_points_open closes the
        # open ends first, and places the points under a fan that would be a
        # sheet which was never there by the winding number of the surface.
        report(f"difference: closing the open ends of mesh {name}")
        inside, overruled = enclosed_points_open(
            source.vertices, reference, stage=f"difference: which side of mesh {name}")
        signed = np.where(inside, -distance, distance) + 0.0     # + 0.0: no -0.0
    else:
        # Sign from the reference's outward normal at the matched sample:
        # positive outside it.
        offset = source.vertices - points[index]
        direction = np.sign(np.einsum("ij,ij->i", offset, normals[index]))
        direction[direction == 0] = 1.0
        signed = distance * direction
    # Mean nearest-neighbour spacing of an area-uniform sample is about
    # 0.5 / sqrt(density); this is what the query can resolve.
    spacing = 0.5 * float(np.sqrt(reference.surface_area() / len(points)))
    return signed, spacing, int(len(points)), overruled


def mesh_difference(
    mesh_a: Mesh, mesh_b: Mesh, *, samples: int = 250_000, seed: int = 0,
    sign: str = "normal", both: bool = False,
) -> MeshDifference:
    """Signed distance from every vertex of ``mesh_a`` to ``mesh_b``'s surface.

    ``sign`` decides which side of ``mesh_b`` a vertex is on:

    * ``"normal"`` (the default; the numbers have not changed since the
      measurement was written): the sign of ``mesh_b``'s outward normal at the
      nearest surface sample.  Right wherever the nearest sample belongs to the
      outer surface, wrong where it belongs to a piece buried inside a
      surface assembled from interpenetrating pieces: against the original
      MATLAB pathway, 21.8 % of the Voxel remesh's vertices of Inferior c21768
      were signed the wrong way (2026-10-15).
    * ``"enclosure"``: negative where ``mesh_b``'s solid encloses the vertex,
      positive where it does not (:func:`enclosed_points_open`: a ray count
      over ``mesh_b`` with its round open ends closed, and its winding number
      where closing a long thin gap would invent a sheet; the work is reported
      as counted stages).  Needs ``mesh_b`` wound outward, which every builder
      here guarantees.  It does *not* guarantee a closed surface: the
      Original pathway (and the tube, adaptive, wrap and Voxel remesh orig
      builds) have open ends, 3,282 boundary edges on c1178, 3,028 on c21768
      and 17,098 on c606, and on an open surface the plain upward count
      depends on the direction of the ray (0.2 to 2.3 % of a Voxel remesh's
      vertices change side against the Original between the upward count and
      a sideways one).  Which side is right is not defined for an open
      surface; the independent measure used is its *generalized winding
      number* (the share of the view it fills from the vertex, inside above
      one half).  Against it the sign is wrong on 0, 0.0005 and 0.0005 % of a
      Voxel remesh's vertices of c1178, c606 and c21768 (200,000 each; the
      bare upward count 0.074, 0.091 and 0.789 %, one fan over every open
      piece 0, 0.0005 and 0.67 %), counting the vertices where the winding
      number is confident (under 0.2 or over 0.8).  The sign is the same from
      +z, +x, +y and -z on all but one vertex of c21768, which lies 0.03 nm
      from the surface.  Where the winding number is between 0.2 and 0.8 (12
      of the vertices examined on c606, 890 on c21768) the surface does not say
      which side the vertex is on, and the sign there is a convention
      (``docs/backend.md``).  The caps decide only the sign, never a distance.
      Costs seconds to tens of seconds on a 200,000-vertex mesh, so a
      service runs it as a job.

    Only the sign differs: the magnitude is the same in both.  With
    ``both=True`` the reverse field -- every vertex of ``mesh_b`` against
    ``mesh_a``, by the same rule, positive where B lies outside A -- comes with
    it (``distance_b_um``), at twice the cost.  The ``stats_area`` figures are
    the usual ones weighted by the surface area each vertex stands for.
    """
    if sign not in DIFFERENCE_SIGNS:
        raise ValueError(f"sign must be one of {', '.join(DIFFERENCE_SIGNS)}, got {sign!r}")
    if mesh_a.is_empty or mesh_b.is_empty:
        raise ValueError("both meshes must be non-empty to compare")

    signed, spacing, n_samples, overruled = _signed_distance(
        mesh_a, mesh_b, samples=samples, seed=seed, sign=sign, name="B")
    magnitude = np.abs(signed)
    forward = MeshDifference(
        distance_um=signed,
        mean_abs_um=float(magnitude.mean()),
        rms_um=float(np.sqrt(np.mean(signed**2))),
        p95_abs_um=float(np.percentile(magnitude, 95)),
        max_abs_um=float(magnitude.max()),
        mean_signed_um=float(signed.mean()),
        scale_um=float(max(np.percentile(magnitude, 98), 1e-6)),
        sample_spacing_um=spacing,
        n_samples=n_samples,
        sign_by=sign,
        stats_area=_area_stats(signed, vertex_areas(mesh_a)),
        sign_overruled=overruled,
    )
    return add_reverse(forward, mesh_a, mesh_b, samples=samples, seed=seed) if both else forward


def add_reverse(
    diff: MeshDifference, mesh_a: Mesh, mesh_b: Mesh, *, samples: int = 250_000, seed: int = 0
) -> MeshDifference:
    """``diff`` (the difference of ``mesh_a`` from ``mesh_b``) with the reverse field.

    Exactly what ``mesh_difference(..., both=True)`` adds to the one-way
    result, by the rule ``diff.sign_by`` names: every vertex of ``mesh_b``
    against ``mesh_a``'s surface (``distance_b_um``) and its area-weighted
    statistics.  A caller that holds the forward half and now wants both
    ways pays for the reverse pass alone.  ``samples`` and ``seed`` are the
    ones ``diff`` was made with.
    """
    reverse, _, _, overruled = _signed_distance(
        mesh_b, mesh_a, samples=samples, seed=seed, sign=diff.sign_by, name="A")
    return replace(diff, distance_b_um=reverse,
                   stats_area_b=_area_stats(reverse, vertex_areas(mesh_b)),
                   sign_overruled_b=overruled)


def visual_bodies(mesh: Mesh, touch_um: float = 0.3) -> int:
    """How many separate bodies the eye sees in a mesh.

    Connected components of the triangle graph, then any two components whose
    surfaces come within ``touch_um`` of each other count as one — pieces
    that touch or interpenetrate read as a single body even though no
    triangle joins them.  This is the detached-body detector from the
    2026-08-30 session (c1178 measured 8 before the tie wires, 1 after); a
    correctly rendered cell measures exactly 1.  Vertices no face references
    are invisible and are not counted.
    """
    if mesh.is_empty:
        return 0
    verts = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    n = len(verts)
    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    cols = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    n_comp, labels = connected_components(graph, directed=False)
    referenced = np.zeros(n, dtype=bool)
    referenced[faces.ravel()] = True
    visible = np.flatnonzero(np.bincount(labels[referenced], minlength=n_comp) > 0)
    if len(visible) <= 1:
        return int(len(visible))

    comps = [verts[labels == k] for k in visible]
    # Largest first: each candidate pair queries the smaller piece against
    # the larger piece's tree, so the big tree is built once and reused.
    comps.sort(key=len, reverse=True)
    lo = np.array([c.min(axis=0) for c in comps]) - touch_um
    hi = np.array([c.max(axis=0) for c in comps]) + touch_um

    roots = list(range(len(comps)))

    def find(x: int) -> int:
        while roots[x] != x:
            roots[x] = roots[roots[x]]
            x = roots[x]
        return x

    trees: dict[int, cKDTree] = {}
    for i in range(len(comps)):
        # Bounding boxes prune the pairs before any k-d query; two pieces
        # whose boxes stay apart by more than touch_um cannot touch.
        near = np.flatnonzero(
            (lo[i + 1:] <= hi[i]).all(axis=1) & (hi[i + 1:] >= lo[i]).all(axis=1)
        ) + i + 1
        for j in near:
            if find(i) == find(int(j)):
                continue
            tree = trees.get(i)
            if tree is None:
                tree = trees[i] = cKDTree(comps[i])
            d, _ = tree.query(comps[j], distance_upper_bound=touch_um)
            if np.isfinite(d).any():
                roots[find(int(j))] = find(i)
    return len({find(i) for i in range(len(comps))})


def dihedral_angles(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """``(angle_deg, length_um)`` of every edge two faces share: the angle
    between the two faces' normals (0 on a flat surface, 90 where a cube's
    faces meet) and the edge's length.  Edges on an open boundary, or shared
    by more than two faces, are left out."""
    if mesh.is_empty:
        return np.zeros(0), np.zeros(0)
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)
    normal = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    normal /= np.maximum(np.linalg.norm(normal, axis=1), 1e-30)[:, None]
    edge = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    face = np.tile(np.arange(len(f)), 3)
    lo, hi = np.minimum(edge[:, 0], edge[:, 1]), np.maximum(edge[:, 0], edge[:, 1])
    key = lo * len(v) + hi
    order = np.argsort(key, kind="stable")
    key, face, lo, hi = key[order], face[order], lo[order], hi[order]
    first = np.flatnonzero(key[1:] == key[:-1])
    # Exactly two faces on the edge: a third would also match the one before it.
    twice = np.ones(len(first), dtype=bool)
    twice[1:] &= first[1:] != first[:-1] + 1
    twice[:-1] &= first[1:] != first[:-1] + 1
    first = first[twice]
    cosine = np.einsum("ij,ij->i", normal[face[first]], normal[face[first + 1]])
    return (np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))),
            np.linalg.norm(v[lo[first]] - v[hi[first]], axis=1))


def crease_density(mesh: Mesh, min_angle_deg: float = 45.0) -> float:
    """Microns of edge sharper than ``min_angle_deg`` for every square micron
    of surface: how much of a surface is crease.

    A staircase is creases -- a tread and its riser meet at a right angle --
    and a smooth surface sampled at the pixel has almost none: Inferior c16137
    at 0.1 um, the Closed curve method's surface, measures 1.08 um/um^2 over
    45 degrees (a row of teeth down every ribbon and plates round the soma),
    0.20 with the staircase averaged out.  A dihedral under 45 degrees is
    what a smooth surface made of 0.1 um facets has round a 0.3 um tube.
    ``0`` for an empty mesh.
    """
    angle, length = dihedral_angles(mesh)
    area = float(mesh.surface_area())
    if not len(angle) or area <= 0:
        return 0.0
    return float(length[angle > min_angle_deg].sum() / area)
