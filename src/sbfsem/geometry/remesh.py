"""Voxel remeshing: rebuild soma-scale bodies from their distance field.

The tube builders lay explicit rings around the centerline, so the surface is a
prism whose facet size grows with the radius -- a 10.9 um soma drawn with ten
ring vertices has 6.9 um facets.  Remeshing samples an analytic distance field
on a grid and runs marching cubes over it, so the tessellation is set by the
grid rather than by the ring count, and the soma comes out round.

**What a Viking annotation means decides the field.**  Every annotation is a
disc in its section's XY plane, not a sphere.  Where consecutive annotations
are far apart relative to their radii (a dendrite), sweeping a round cone
between them is a fair reading and the capsule field is used.  Where they are
stacked closer than their own radii -- which is 99% of the pairs inside a soma,
annotated section by section -- a capsule degenerates into a full *sphere* per
annotation, bulging an entire radius above and below its section; the union of
overlapping, laterally wandering spheres is the "amoeba" artifact.  Those runs
are instead **lofted**: the surface passes through each disc exactly as it was
drawn, interpolating center and radius between sections, with flat, gently
rounded caps where a run ends.

**One field per fat cluster, not per segment.**  A soma is not one segment: the
segmentation walks branches, so its annotations are spread over several rows
(c1178's soma spans four).  Remeshing each row alone stacked four independent
envelopes -- interpenetrating shells with a crease wherever a row happened to
end, plus rows mixing a soma with thin dendrites whose grid could never hold
the thin parts.  Instead, every annotation at soma scale
(``min_radius_um``) across the *whole cell* is clustered -- two fat
annotations join a cluster when they are adjacent in a polyline or their
balls overlap in space -- and each cluster gets exactly one grid and one
marching-cubes pass.  Runs of stacked discs are grouped by actual z-interval
and disc overlap, so a stack that crosses segmentation rows is one body with
caps only at its true extremes.  Thin geometry is never voxelized at all: the
caller renders it with the tube builders, anchored one annotation into the
cluster at the thin radius.

The grid is per cluster and sized from that cluster's own (all fat) radii.
That is what makes this affordable: a soma cluster is a ~30 um box at half a
micron per voxel, a few hundred thousand cells, where a single grid over a
whole cell -- the soma is ~60x the thinnest dendrite's radius -- needs
billions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates, spline_filter
from skimage.measure import marching_cubes

from .mesh import EMPTY_MESH, Mesh, concatenate

log = logging.getLogger(__name__)

__all__ = ["SampledField", "voxel_remesh", "voxel_remesh_cell", "voxel_remesh_cell_fields",
           "VOXELS_PER_RADIUS", "MAX_VOXELS"]

#: Voxels across the *thinnest* radius in a cluster.  Three is the point where
#: a tube stops reading as a staircase; below two, marching cubes can lose it.
VOXELS_PER_RADIUS = 3.0

#: Per-cluster ceiling.  A chain of fat annotations spread over a huge span
#: would otherwise ask for a grid far larger than the geometry justifies.
MAX_VOXELS = 6_000_000

#: Only annotations at least this wide take part in remeshing.  A tube's facet
#: is 2*pi*r/10 across, so at 1.5 um that is 0.94 um -- plainly visible.  Below
#: it the facets are a fraction of a micron and invisible at any useful zoom,
#: while the grid cost keeps climbing.
REMESH_MIN_RADIUS_UM = 1.5

#: Never spend more than this many voxels across the widest part of a cluster.
MAX_VOXELS_ACROSS_WIDEST = 48

#: A consecutive pair is treated as stacked discs (lofted) rather than a tube
#: (capsule) when both the vertical and the lateral step are smaller than this
#: multiple of the larger radius.  In the soma both ratios run ~0.02-0.2.
DISC_STACK_FACTOR = 1.0

#: Standard deviation, in microns, of the Gaussian applied to the distance
#: field before the surface is extracted, *per REF radius of body size*.  This
#: is what turns the literal stack of annotated discs into the smooth outer
#: envelope the tissue actually has.  The noise being removed scales with the
#: body: annotators eyeball their circle sizes, hold one size for a dozen
#: sections and then jump, so a 10 um soma carries ~2 um radius steps
#: ("wedding-cake" terraces) where a 2 um trunk carries fractions of a
#: micron.  A fixed blur can only bevel a step much larger than itself, so
#: the effective sigma is ``SURFACE_SMOOTH_UM * widest_radius / REF``:
#: c1178's 10.9 um soma gets ~1.1 um and comes out a smooth pear, a thin
#: trunk keeps a light touch.  Zero disables it and extracts the literal disc
#: stack, terraces and all.
SURFACE_SMOOTH_UM = 0.5

#: Body radius at which ``SURFACE_SMOOTH_UM`` applies exactly.
SMOOTH_REF_RADIUS_UM = 5.0

#: Discs below this radius are never lofted: at dendrite scale a capsule's
#: bulge equals the (small) radius and a loft squashes shallow-climbing
#: in-plane dendrites flat.  Deliberately the same scale as
#: ``REMESH_MIN_RADIUS_UM`` -- lofting and remeshing both start at soma scale.
SLAB_MIN_RADIUS_UM = REMESH_MIN_RADIUS_UM

#: A run of stacked discs whose centers travel sideways farther than the run
#: is tall -- and farther than its widest disc -- is an oblique process
#: annotated section by section, not a body: its horizontal sections are
#: the same tube seen at different heights.  Lofting it makes a slanted
#: plate (c118's trunk leaving its soma became a 2.7 um shelf; a 1.6 um
#: process of c1178 running 14 um horizontally became a plate).  The
#: ``voxel2`` pathway reads such a run as round cones instead; the
#: original voxel pathway keeps the loft so its output is unchanged.
TRAVEL_HEIGHT_RATIO = 1.0

#: Radius for a link pair's connecting tube: two same-section fat discs whose
#: lateral gap disqualifies a loft are joined at attachment scale, not at
#: their own girth.  Half the remesh threshold: clearly visible, clearly not
#: a body.
LINK_RADIUS_UM = 0.75


def _capsule_distance(grid: np.ndarray, a: np.ndarray, b: np.ndarray,
                      ra: float, rb: float) -> np.ndarray:
    """Exact signed distance to one round cone (a, ra) -> (b, rb).

    The obvious form -- distance to the axis minus the interpolated radius --
    is only correct when the radius changes slowly along the segment.  Where it
    changes faster than the length, which is exactly what a soma does, that
    form over-estimates the radius near the wide end and the surface bulges:
    on c1178 it inflated the cell from 13,600 to 20,800 um3.  This is the
    standard exact round-cone field, which projects onto the slanted surface
    rather than perpendicular to the axis.
    """
    ba = b - a
    l2 = float(ba @ ba)
    pa = grid - a
    if l2 <= 1e-18:
        return np.linalg.norm(pa, axis=1) - max(ra, rb)

    rr = ra - rb
    a2 = l2 - rr * rr
    il2 = 1.0 / l2
    y = pa @ ba
    z = y - l2
    # Perpendicular component, kept scaled by l2 to avoid a square root here.
    cross = pa * l2 - ba[None, :] * y[:, None]
    x2 = np.einsum("ij,ij->i", cross, cross)
    y2 = y * y * l2
    z2 = z * z * l2
    k = np.sign(rr) * rr * rr * x2

    out = np.empty(len(grid), dtype=float)
    beyond_b = np.sign(z) * a2 * z2 > k
    before_a = (~beyond_b) & (np.sign(y) * a2 * y2 < k)
    slanted = ~(beyond_b | before_a)
    if beyond_b.any():
        out[beyond_b] = np.sqrt(x2[beyond_b] + z2[beyond_b]) * il2 - rb
    if before_a.any():
        out[before_a] = np.sqrt(x2[before_a] + y2[before_a]) * il2 - ra
    if slanted.any():
        out[slanted] = (
            (np.sqrt(np.maximum(x2[slanted] * a2 * il2, 0.0)) + y[slanted] * rr) * il2 - ra
        )
    return out


def _tube_distance(grid: np.ndarray, a: np.ndarray, b: np.ndarray,
                   ra: float, rb: float) -> np.ndarray:
    """Distance to the convex hull of two balls, valid for every input.

    The exact round-cone form in :func:`_capsule_distance` requires
    ``|b - a| > |ra - rb|``; when one ball contains the other -- a dendrite
    annotation right at a soma's edge -- its branch predicates mis-route deep
    interior points and report them outside, which carved craters into somas.
    In that regime the hull *is* the larger ball, so use it directly.
    """
    axis = b - a
    if float(axis @ axis) <= (ra - rb) ** 2:
        if ra >= rb:
            return np.linalg.norm(grid - a, axis=1) - ra
        return np.linalg.norm(grid - b, axis=1) - rb
    return _capsule_distance(grid, a, b, ra, rb)


def _disc_at(z: float, a: np.ndarray, b: np.ndarray,
             ra: float, rb: float) -> tuple[float, float, float]:
    """Center and radius of the lofted cross-section at height ``z``.

    The interpolation parameter is clamped, so a query outside the pair's
    z-range returns the nearer end disc unchanged.
    """
    za, zb = float(a[2]), float(b[2])
    t = 0.0 if zb == za else min(max((z - za) / (zb - za), 0.0), 1.0)
    return (a[0] + t * (b[0] - a[0]),
            a[1] + t * (b[1] - a[1]),
            ra + t * (rb - ra))


def _slab_interior(grid_xy: np.ndarray, z: float, a: np.ndarray, b: np.ndarray,
                   ra: float, rb: float, run_lo: float, run_hi: float) -> np.ndarray:
    """Distance to the loft at a height the pair actually covers.

    In-plane distance to the interpolated disc, corner-combined with the z
    distance to the *run's* extent -- never the slab's own.  A slab is one
    inter-section step thin, and a per-slab cap makes the union wrong twice
    over: at shared boundary planes both neighbours report their cap's zero
    instead of the lateral distance, and interior magnitudes get clamped to
    half a step, biasing the marching-cubes crossing inward (an earlier
    version lost 7% of a test cylinder's volume to exactly this).  Using the
    run extent keeps the field a true distance right up to the flat caps, so
    the cap lands on its plane instead of half a voxel beyond it.
    """
    cx, cy, r = _disc_at(z, a, b, ra, rb)
    d2 = np.hypot(grid_xy[:, 0] - cx, grid_xy[:, 1] - cy) - r
    dzr = max(run_lo - z, z - run_hi)      # <= 0 anywhere a slab covers
    return (np.minimum(np.maximum(d2, dzr), 0.0)
            + np.hypot(np.maximum(d2, 0.0), max(dzr, 0.0)))


def _signed_volume(verts: np.ndarray, faces: np.ndarray) -> float:
    a = verts[faces[:, 0]]
    b = verts[faces[:, 1]]
    c = verts[faces[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0


def _level_volume(field: np.ndarray, level: float, voxel: float) -> float:
    try:
        verts, faces, _n, _v = marching_cubes(field, level=level)
    except (ValueError, RuntimeError):
        return 0.0
    return abs(_signed_volume(verts, faces)) * voxel ** 3


def _level_volume_area(field: np.ndarray, level: float,
                       voxel: float) -> tuple[float, float]:
    try:
        verts, faces, _n, _v = marching_cubes(field, level=level)
    except (ValueError, RuntimeError):
        return 0.0, 0.0
    cross = np.cross(verts[faces[:, 1]] - verts[faces[:, 0]],
                     verts[faces[:, 2]] - verts[faces[:, 0]])
    area = 0.5 * float(np.linalg.norm(cross, axis=1).sum()) * voxel ** 2
    return abs(_signed_volume(verts, faces)) * voxel ** 3, area


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        p = self.parent
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


@dataclass
class SampledField:
    """A cluster's distance field on its own grid, ready to be resampled.

    ``field`` is the (blurred, clamped) field :func:`_cluster_field` built and
    ``level`` the volume-compensated iso-level its surface sits at, so the
    signed distance to that surface at a point is :meth:`sample`: the
    tricubically interpolated field minus ``level``.  This is what lets the
    ``voxel2`` pathway carry a soma envelope -- exactly as the voxel pathway
    computed it -- into a finer whole-cell grid without rebuilding it.
    """

    field: np.ndarray
    lo: np.ndarray
    voxel: float
    level: float
    _coeffs: np.ndarray | None = None

    @property
    def hi(self) -> np.ndarray:
        """Position of the last sample on every axis, in microns."""
        return self.lo + (np.array(self.field.shape, dtype=float) - 1.0) * self.voxel

    def sample(self, xyz: np.ndarray, *, order: int = 3) -> np.ndarray:
        """Signed distance to the surface at ``xyz`` (``(n, 3)`` microns).

        Tricubic by default -- the field is already smooth at its own grid
        scale, and a trilinear resample would crease the surface along every
        coarse cell face.  Outside the grid the edge value repeats, which is
        the clamped positive background.
        """
        coords = ((np.asarray(xyz, dtype=float).reshape(-1, 3) - self.lo) / self.voxel).T
        if order <= 1:
            values = map_coordinates(self.field, coords, order=1, mode="nearest")
        else:
            values = map_coordinates(self.coefficients(), coords, order=3, mode="nearest",
                                     prefilter=False, output=np.float32)
        return np.asarray(values, dtype=float) - self.level

    def coefficients(self) -> np.ndarray:
        """The tricubic spline's coefficients the resample reads, made once."""
        if self._coeffs is None:
            self._coeffs = spline_filter(self.field, order=3, output=np.float32, mode="nearest")
        return self._coeffs


def _cluster_field(
    P: np.ndarray,
    R: np.ndarray,
    pair_idx: np.ndarray,
    pair_slab: np.ndarray,
    pair_link: np.ndarray,
    members: np.ndarray,
    *,
    voxels_per_radius: float,
    max_voxels: int,
    detail: float,
    surface_smooth_um: float,
    lens_h_default: float | None,
    travel_as_tube: bool = False,
) -> tuple[SampledField | None, np.ndarray]:
    """The distance field of one fat cluster, blurred and volume compensated.

    ``pair_idx`` are (i, j) annotation index pairs contributing geometry --
    lofted slabs where ``pair_slab`` is set, round-cone tubes otherwise.
    ``members`` are the cluster's fat annotations; they alone size the grid,
    which is why a cluster is affordable where a mixed segment was not.

    Returns the field (``None`` when the cluster declines: a grid that could
    not hold its bodies, or no surface) and a boolean per member saying
    whether the surface actually holds that annotation, checked by sampling
    the smoothed field at its center.  Small members near ``min_radius`` can
    be erased by the cluster-wide blur even though the body as a whole is
    fine; reporting them lets the caller fall back to a tube instead of
    losing them.

    ``travel_as_tube`` demotes runs of stacked discs that travel sideways
    (``TRAVEL_HEIGHT_RATIO``) to round-cone pairs.
    """
    scale = max(float(detail), 1e-3)
    mrad = R[members]
    voxel = float(mrad.min()) / max(voxels_per_radius * scale, 1e-6)
    # A floor tied to the widest radius: the thinnest part of a cluster should
    # not set the resolution for a soma many times its size.
    voxel = max(voxel, float(mrad.max()) / (MAX_VOXELS_ACROSS_WIDEST * scale))
    # Scaled by the cluster's widest body and capped: past ~2.5 um genuine
    # lobes start merging no matter how big the soma is.  Needed before the
    # grid is laid out, because the blur's support widens every window.
    sigma = max(float(surface_smooth_um), 0.0)
    sigma = min(sigma * float(mrad.max()) / SMOOTH_REF_RADIUS_UM, 2.5)

    touched = np.unique(np.concatenate([members, pair_idx.ravel()])) \
        if len(pair_idx) else members
    lo = (P[touched] - R[touched, None]).min(axis=0)
    hi = (P[touched] + R[touched, None]).max(axis=0)
    # Air on every side so marching cubes closes the surface, plus the blur's
    # support: beyond the evaluated windows the field is clamped background,
    # and blurring against that inside the Gaussian's reach pulls flat caps,
    # lens faces and tube ends inward (the walls stay exact, so the erosion
    # is anisotropic).
    air = 3 * voxel + 2.0 * sigma
    lo -= air
    hi += air
    span = hi - lo

    counts = np.maximum(np.ceil(span / voxel).astype(int) + 1, 2)
    total = float(np.prod(counts.astype(float)))
    if total > max_voxels:
        voxel *= (total / max_voxels) ** (1.0 / 3.0)
        counts = np.maximum(np.ceil(span / voxel).astype(int) + 1, 2)
        log.debug("voxel cluster coarsened to %.4f um (%s)", voxel, tuple(counts))
    if voxel > 2.0 * float(mrad.min()):
        # The grid can no longer hold the cluster's own bodies; marching cubes
        # would shatter them into disconnected beads.  An empty result makes
        # the caller fall back to the tube builders, which is always sane.
        log.debug("voxel cluster declining: voxel %.3f um > 2x min radius", voxel)
        return None, np.zeros(len(members), dtype=bool)

    A = P[pair_idx[:, 0]] if len(pair_idx) else np.zeros((0, 3))
    B = P[pair_idx[:, 1]] if len(pair_idx) else np.zeros((0, 3))
    Ra = R[pair_idx[:, 0]] if len(pair_idx) else np.zeros(0)
    Rb = R[pair_idx[:, 1]] if len(pair_idx) else np.zeros(0)
    z_pair = np.sort(np.column_stack([A[:, 2], B[:, 2]]), axis=1) \
        if len(pair_idx) else np.zeros((0, 2))
    pad = 3.0 * voxel + 2.0 * sigma

    # Runs of stacked-disc pairs, grouped by what they actually are -- one
    # continuous body -- rather than by their order in a segmentation row.
    # Two slab pairs share a run when their z-intervals touch AND their discs
    # overlap in plan there; consecutive pairs of one row always share their
    # common annotation's plane, and a soma split across rows merges the same
    # way.  Each run gets exactly two flat caps at its true z-extremes, which
    # is what kills the per-row "loaf" caps that used to cut a soma mid-body.
    slab_ids = np.nonzero(pair_slab)[0]
    run_lo = np.zeros(len(pair_idx))
    run_hi = np.zeros(len(pair_idx))
    in_run = np.zeros(len(P), dtype=bool)
    caps: list[tuple[float, float, float, list[int]]] = []  # (cap_z, lo, hi, pairs)
    if len(slab_ids):
        uf = _UnionFind(len(slab_ids))
        for ii in range(len(slab_ids)):
            p = slab_ids[ii]
            for jj in range(ii + 1, len(slab_ids)):
                q = slab_ids[jj]
                z0 = max(z_pair[p, 0], z_pair[q, 0])
                z1 = min(z_pair[p, 1], z_pair[q, 1])
                if z0 > z1:
                    continue
                zc = 0.5 * (z0 + z1)
                cxp, cyp, rp = _disc_at(zc, A[p], B[p], Ra[p], Rb[p])
                cxq, cyq, rq = _disc_at(zc, A[q], B[q], Ra[q], Rb[q])
                if np.hypot(cxp - cxq, cyp - cyq) < rp + rq:
                    uf.union(ii, jj)
        groups: dict[int, list[int]] = {}
        for ii in range(len(slab_ids)):
            groups.setdefault(uf.find(ii), []).append(int(slab_ids[ii]))
        if travel_as_tube:
            # A run that moves sideways more than it climbs is a tube seen
            # section by section; read its pairs as round cones.  The soma
            # itself (c118: 12 um tall, 3 um of drift) stays a loft.
            for root in list(groups):
                pair_list = groups[root]
                ids = np.unique(pair_idx[pair_list].ravel())
                order_z = ids[np.argsort(P[ids, 2], kind="stable")]
                travel = float(np.hypot(*(P[order_z[-1], :2] - P[order_z[0], :2])))
                height = float(P[order_z[-1], 2] - P[order_z[0], 2])
                if travel > max(TRAVEL_HEIGHT_RATIO * height, float(R[ids].max())):
                    pair_slab[pair_list] = False
                    del groups[root]
        for pair_list in groups.values():
            z_lo = float(min(z_pair[p, 0] for p in pair_list))
            z_hi = float(max(z_pair[p, 1] for p in pair_list))
            for p in pair_list:
                run_lo[p] = z_lo
                run_hi[p] = z_hi
                in_run[pair_idx[p, 0]] = True
                in_run[pair_idx[p, 1]] = True
            # Every pair in the run contributes its end disc to each cap, so a
            # z-non-monotone run whose extreme is shared by several pairs is
            # capped by all of them, not one arbitrary winner.
            caps.append((z_lo, z_lo - pad, z_lo, pair_list))
            caps.append((z_hi, z_hi, z_hi + pad, pair_list))

    # A same-z pair covers a zero-height window no grid slice ever samples,
    # so a laterally offset duplicate's disc would vanish from the field.
    # Materialize both discs as lenses one voxel tall; concentric duplicates
    # add nothing and are skipped.
    flat_lens: list[tuple[float, float, float, float, float]] = []
    if len(pair_idx):
        for pidx in np.nonzero(pair_slab)[0]:
            dzp = abs(float(A[pidx, 2]) - float(B[pidx, 2]))
            dxyp = float(np.hypot(A[pidx, 0] - B[pidx, 0], A[pidx, 1] - B[pidx, 1]))
            if dzp < 1e-9 and dxyp > 1e-9:
                for c, r in ((A[pidx], Ra[pidx]), (B[pidx], Rb[pidx])):
                    h = float(np.clip(voxel, voxel, float(r)))
                    flat_lens.append((float(c[0]), float(c[1]), float(r),
                                      float(c[2]), h))

    # An isolated fat annotation -- soma-scale radius, but no neighbour close
    # enough for a loft or a fat tube -- is still a disc, not a ball.  It
    # becomes a "lens": its disc extruded half a typical section step up and
    # down, capped like a one-slab run.  Without this, one such annotation put
    # a perfect 2 um sphere on top of a soma.
    paired = set(pair_idx.ravel().tolist())
    lens: list[tuple[float, float, float, float, float]] = []  # cx, cy, r, z, h
    for m in members:
        if int(m) in paired:
            continue
        h = float(np.clip(lens_h_default if lens_h_default else voxel,
                          voxel, R[m]))
        lens.append((float(P[m, 0]), float(P[m, 1]), float(R[m]),
                     float(P[m, 2]), h))
        # Adjoining tubes attach at their thin radius instead of capping the
        # lens with the annotation's full ball.
        in_run[m] = True
    lens.extend(flat_lens)

    # A tube whose endpoint annotation belongs to a lofted run does not get to
    # cap that end with the annotation's full ball -- the run's loft already IS
    # that body, and the ball would bulge a soma radius past the annotated
    # sections.  The tube's only remaining job there is attachment, at its thin
    # end's radius.
    r_a_eff = Ra.copy()
    r_b_eff = Rb.copy()
    tube_ids = np.nonzero(~pair_slab)[0] if len(pair_idx) else np.zeros(0, int)
    for p in tube_ids:
        if pair_link[p]:
            # A same-section branch link connects, it does not have girth.
            thin = min(LINK_RADIUS_UM, float(Ra[p]), float(Rb[p]))
            r_a_eff[p] = thin
            r_b_eff[p] = thin
            continue
        thin = min(float(Ra[p]), float(Rb[p]))
        if in_run[pair_idx[p, 0]]:
            r_a_eff[p] = thin
        if in_run[pair_idx[p, 1]]:
            r_b_eff[p] = thin
    r_reach = np.maximum(r_a_eff, r_b_eff) if len(pair_idx) else np.zeros(0)

    # A capsule reaches a full radius past its endpoints and needs the pad for
    # a clean gradient there.  A slab must be confined to *exactly* its own
    # z-interval: its interior field has no z term, so evaluating it even one
    # slice beyond the run extrudes the end disc that far past the cap.
    if len(pair_idx):
        win_lo = np.where(pair_slab, z_pair[:, 0], z_pair[:, 0] - r_reach - pad)
        win_hi = np.where(pair_slab, z_pair[:, 1], z_pair[:, 1] + r_reach + pad)
    else:
        win_lo = np.zeros(0)
        win_hi = np.zeros(0)

    # Each cap's discs are fixed: precompute (cx, cy, r) per contributing pair.
    cap_discs = [
        (cap_z, lo_z, hi_z,
         [_disc_at(cap_z, A[p], B[p], Ra[p], Rb[p]) for p in pair_list])
        for cap_z, lo_z, hi_z, pair_list in caps
    ]

    axes = [lo[k] + np.arange(counts[k]) * voxel for k in range(3)]
    field = np.empty(tuple(counts), dtype=np.float32)
    xx, yy = np.meshgrid(axes[0], axes[1], indexing="ij")
    flat_xy = np.column_stack([xx.ravel(), yy.ravel()])
    grid3 = np.column_stack([flat_xy, np.zeros(len(flat_xy))])
    for k, z in enumerate(axes[2]):
        # Far positive, not infinity: marching cubes differentiates the field.
        best = np.full(len(flat_xy), 1e3)
        grid3[:, 2] = z
        live = np.nonzero((win_lo <= z) & (z <= win_hi))[0]
        for p in live:
            if pair_slab[p]:
                d = _slab_interior(flat_xy, float(z), A[p], B[p],
                                   float(Ra[p]), float(Rb[p]),
                                   float(run_lo[p]), float(run_hi[p]))
            else:
                d = _tube_distance(grid3, A[p], B[p],
                                   float(r_a_eff[p]), float(r_b_eff[p]))
            np.minimum(best, d, out=best)
        for cx, cy, r_l, z0, h in lens:
            if z0 - h - pad <= z <= z0 + h + pad:
                d2 = np.hypot(flat_xy[:, 0] - cx, flat_xy[:, 1] - cy) - r_l
                dzs = abs(z - z0) - h
                d = (np.minimum(np.maximum(d2, dzs), 0.0)
                     + np.hypot(np.maximum(d2, 0.0), max(dzs, 0.0)))
                np.minimum(best, d, out=best)
        for cap_z, lo_z, hi_z, discs in cap_discs:
            if lo_z <= z <= hi_z and z != cap_z:
                d2_min = np.full(len(flat_xy), 1e3)
                for cx, cy, r in discs:
                    d2 = np.hypot(flat_xy[:, 0] - cx, flat_xy[:, 1] - cy) - r
                    np.minimum(d2_min, d2, out=d2_min)
                d = np.hypot(np.maximum(d2_min, 0.0), abs(z - cap_z))
                np.minimum(best, d, out=best)
        field[:, :, k] = best.reshape(counts[0], counts[1]).astype(np.float32)

    if not (field.min() < 0.0 < field.max()):
        return None, np.zeros(len(members), dtype=bool)
    level = -1e-4
    limit = 4.0 * voxel + 2.0 * sigma
    if sigma > 0.0:
        # Clamp first: the untouched background sits at +1e3, and blurring
        # against that would drag the surface outward near unfilled slices.
        np.clip(field, -limit, limit, out=field)
        raw = field.copy()
        field = gaussian_filter(field, sigma=sigma / voxel, mode="nearest")
        # The blur erodes convex regions by ~sigma^2 * curvature, which is
        # what read as "the soma got smaller".  Recover the pre-blur volume
        # by extracting a slightly positive level set: Newton steps using the
        # current surface area, since V(L) ~ V + A * dL.
        v0 = _level_volume(raw, -1e-4, voxel)
        if v0 > 0:
            for _ in range(3):
                v1, a1 = _level_volume_area(field, level, voxel)
                if v1 <= 0 or a1 <= 0 or abs(v1 - v0) < 0.01 * v0:
                    break
                level = float(np.clip(level + (v0 - v1) / a1, -1e-4, 2.0 * sigma))
        del raw
    else:
        # Unblurred, the background still sits at +1e3.  Clamp it so a cubic
        # resample of this field (the voxel2 pathway) cannot ring against it.
        # No crossing changes: every sample next to a negative one is a real
        # distance under ``pad``, and ``pad`` < ``limit``.
        np.clip(field, -limit, limit, out=field)
    # Which members did the surface actually keep?  Nearest-voxel sample of
    # the (smoothed) field at each center: negative means inside the body.
    idx = np.clip(np.rint((P[members] - lo) / voxel).astype(int), 0,
                  np.array(counts) - 1)
    # A run-end member sits exactly ON its cap plane, so the nearest slice can
    # land half a voxel outside; take the friendliest of the three z-neighbor
    # slices rather than reporting cap members lost at random.
    z_lo = np.clip(idx[:, 2] - 1, 0, counts[2] - 1)
    z_hi = np.clip(idx[:, 2] + 1, 0, counts[2] - 1)
    held = np.minimum(
        field[idx[:, 0], idx[:, 1], idx[:, 2]],
        np.minimum(field[idx[:, 0], idx[:, 1], z_lo],
                   field[idx[:, 0], idx[:, 1], z_hi]),
    ) < level
    return SampledField(field, np.asarray(lo, dtype=float).copy(), float(voxel),
                        float(level)), held


def _cluster_mesh(
    P: np.ndarray,
    R: np.ndarray,
    pair_idx: np.ndarray,
    pair_slab: np.ndarray,
    pair_link: np.ndarray,
    members: np.ndarray,
    *,
    voxels_per_radius: float,
    max_voxels: int,
    detail: float,
    surface_smooth_um: float,
    lens_h_default: float | None,
) -> tuple[Mesh, np.ndarray]:
    """Marching-cubes mesh of one fat cluster's distance field.

    :func:`_cluster_field` contoured at its compensated level; the same
    ``held`` mask comes back with it.
    """
    sampled, held = _cluster_field(
        P, R, pair_idx, pair_slab, pair_link, members,
        voxels_per_radius=voxels_per_radius, max_voxels=max_voxels, detail=detail,
        surface_smooth_um=surface_smooth_um, lens_h_default=lens_h_default,
    )
    if sampled is None:
        return EMPTY_MESH, held
    try:
        # A hair off the grid values: cap planes landing exactly on slices
        # would otherwise emit sheets of degenerate zero-area faces.
        verts, faces, _n, _v = marching_cubes(sampled.field, level=sampled.level)
    except (ValueError, RuntimeError):
        return EMPTY_MESH, np.zeros(len(members), dtype=bool)
    # marching_cubes indexes the array directly, and the array is already
    # (x, y, z) here, so no axis swap -- unlike the closed-curve path, whose
    # volume is (row, col, slice).
    xyz = verts * sampled.voxel + sampled.lo
    return Mesh(xyz, faces).fix_winding(), held


def flatten_cell(polylines, *, min_radius_um: float = SLAB_MIN_RADIUS_UM):
    """Flatten segmentation rows and cluster the fat annotations.

    Shared by the voxel and cube-wrap pathways.  Returns ``None`` when the
    input is empty, else ``(P, R, finite, fat, ia, ib, starts, lengths,
    clusters)`` where ``ia/ib`` are row-adjacent annotation index pairs and
    ``clusters`` maps a root to the fat annotation indices of one body
    (adjacency in a row, or ball overlap in space, joins them -- ball overlap
    is what merges a soma split across segmentation rows).
    """
    pts_list: list[np.ndarray] = []
    rad_list: list[np.ndarray] = []
    for points, radii in polylines:
        p = np.asarray(points, dtype=float).reshape(-1, 3)
        r = np.abs(np.asarray(radii, dtype=float).ravel())
        if len(r) != len(p):
            # A malformed row contributes nothing but must keep its slot so
            # the covered masks line up with the caller's rows.
            r = np.full(len(p), np.nan)
        pts_list.append(p)
        rad_list.append(r)

    lengths = [len(p) for p in pts_list]
    if not lengths or sum(lengths) == 0:
        return None
    P = np.vstack(pts_list)
    R = np.concatenate(rad_list)
    starts = np.cumsum([0] + lengths[:-1])

    finite = np.isfinite(P).all(axis=1) & np.isfinite(R) & (R > 0)
    fat = finite & (R >= float(min_radius_um))  # noqa: SIM300 -- not a comparison to a constant

    # A pair exists only between annotations that were adjacent in one row.
    # Filtering first and pairing after would bridge across a dropped bad
    # annotation and fabricate a tube through a gap the tracer never drew.
    pair_a: list[int] = []
    pair_b: list[int] = []
    for s, n in zip(starts, lengths, strict=True):
        for i in range(s, s + n - 1):
            if finite[i] and finite[i + 1]:
                pair_a.append(i)
                pair_b.append(i + 1)
    ia = np.array(pair_a, dtype=int)
    ib = np.array(pair_b, dtype=int)

    clusters: dict[int, list[int]] = {}
    if fat.any():
        fat_ids = np.nonzero(fat)[0]
        pos_of = {int(g): k for k, g in enumerate(fat_ids)}
        uf = _UnionFind(len(fat_ids))
        for a, b in zip(ia, ib, strict=True):
            if fat[a] and fat[b]:
                uf.union(pos_of[int(a)], pos_of[int(b)])
        fpts = P[fat_ids]
        frad = R[fat_ids]
        for k in range(len(fat_ids)):
            d = np.linalg.norm(fpts - fpts[k], axis=1)
            close = np.nonzero(d < frad + frad[k])[0]
            for j in close:
                if j > k:
                    uf.union(int(j), k)
        for k, g in enumerate(fat_ids):
            clusters.setdefault(uf.find(k), []).append(int(g))
    return P, R, finite, fat, ia, ib, starts, lengths, clusters


def _remesh_cell(
    polylines,
    *,
    as_fields: bool,
    travel_as_tube: bool = False,
    voxels_per_radius: float = VOXELS_PER_RADIUS,
    max_voxels: int = MAX_VOXELS,
    detail: float = 1.0,
    surface_smooth_um: float = SURFACE_SMOOTH_UM,
    min_radius_um: float = SLAB_MIN_RADIUS_UM,
) -> tuple[list, list[np.ndarray]]:
    """Shared body of :func:`voxel_remesh_cell` and
    :func:`voxel_remesh_cell_fields`: one field per fat cluster, returned as
    meshes or as the fields themselves."""
    flat = flatten_cell(polylines, min_radius_um=min_radius_um)
    if flat is None:
        return [], [np.zeros(len(np.asarray(p).reshape(-1, 3)), dtype=bool)
                    for p, _r in polylines]
    P, R, finite, fat, ia, ib, starts, lengths, clusters = flat
    covered = [np.zeros(n, dtype=bool) for n in lengths]
    if not clusters:
        return [], covered

    # Classify every adjacency pair once.  A pair is lofted as stacked discs
    # when its discs overlap heavily in plan view -- both the vertical and the
    # lateral step smaller than the LARGER radius, same-z duplicates included
    # -- AND it is at soma scale.  The radius gate matters: retinal dendrites
    # run mostly in-plane, so at dendrite scale the "stack" is only as tall as
    # its shallow climb and a loft squashes the round tube flat.  A steep pair
    # joins the field as a round cone only when BOTH ends are fat: a steep
    # fat-to-thin taper would need dendrite-scale voxels the cluster grid does
    # not have, and the tube builders already own that geometry.
    steps = P[ib] - P[ia]
    step_z = np.abs(steps[:, 2])
    step_xy = np.hypot(steps[:, 0], steps[:, 1])
    r_max_pair = np.maximum(R[ia], R[ib])
    is_slab_pair = (
        (r_max_pair >= float(min_radius_um))
        & (step_z < DISC_STACK_FACTOR * r_max_pair)
        & (step_xy < DISC_STACK_FACTOR * r_max_pair)
    )
    # A fat pair at stacked-disc z-scale that fails only the LATERAL gate is a
    # branch link between two same-section discs, not a tube: sweeping it at
    # its own girth drew a horizontal radius-thick sausage through tissue the
    # tracer never annotated (a 4 um one reached 2.7 um below c4115's lowest
    # annotation -- the "detached knob"; c1178 carried a 22 um long, 10.5 um
    # thick one through its soma).  The link still states that the two bodies
    # connect, so it is kept -- but drawn at ``LINK_RADIUS_UM``, attachment
    # scale, which also cannot protrude past the annotated extent.  Only a
    # genuinely steep fat pair -- a thick trunk actually descending -- still
    # sweeps a full capsule.
    is_steep_pair = step_z >= DISC_STACK_FACTOR * r_max_pair
    is_link_pair = fat[ia] & fat[ib] & ~is_slab_pair & ~is_steep_pair
    keep = is_slab_pair | (fat[ia] & fat[ib])

    dz_valid = step_z[step_z > 1e-9]
    lens_h_default = float(np.median(dz_valid)) / 2.0 if len(dz_valid) else None

    bodies: list = []
    flat_covered = np.zeros(len(P), dtype=bool)
    for members in clusters.values():
        members_arr = np.array(sorted(members), dtype=int)
        sel = np.nonzero(keep & (np.isin(ia, members_arr) | np.isin(ib, members_arr)))[0]
        pair_idx = np.column_stack([ia[sel], ib[sel]]) if len(sel) else np.zeros((0, 2), int)
        pair_slab = is_slab_pair[sel]
        pair_link = is_link_pair[sel]
        # Rows only loft within themselves, but a soma's stack of discs spans
        # several segmentation rows.  Without a bridge, each row boundary ends
        # in two flat caps -- terraces climbing the soma like a wedding cake.
        # Chain the cluster's members in z-order: any consecutive-in-z couple
        # that is not already a row pair, stacks like discs, and overlaps in
        # plan gets a synthetic slab pair, so the loft interpolates across the
        # boundary exactly as it does inside a row.
        existing = {frozenset(pr) for pr in pair_idx.tolist()}
        order = members_arr[np.argsort(P[members_arr, 2], kind="stable")]
        bridge: list[tuple[int, int]] = []
        # For each member, take the FIRST z-successor that stacks and overlaps
        # -- not merely the next one.  A fat annotation from another branch
        # sitting at an intermediate z but off to the side would otherwise be
        # both halves' only tested partner, fail the lateral gate, and leave
        # the soma split in two with facing caps at the row boundary.
        for k in range(len(order) - 1):
            i = order[k]
            for j in order[k + 1:k + 17]:
                if frozenset((int(i), int(j))) in existing:
                    break
                dz = abs(P[j, 2] - P[i, 2])
                dxy = float(np.hypot(P[j, 0] - P[i, 0], P[j, 1] - P[i, 1]))
                r_big = max(R[i], R[j])
                if (dz < DISC_STACK_FACTOR * r_big and dxy < DISC_STACK_FACTOR * r_big
                        and dxy < R[i] + R[j]):
                    bridge.append((int(i), int(j)))
                    break
        if bridge:
            pair_idx = np.vstack([pair_idx, np.array(bridge, dtype=int)])
            pair_slab = np.concatenate([pair_slab, np.ones(len(bridge), dtype=bool)])
            pair_link = np.concatenate([pair_link, np.zeros(len(bridge), dtype=bool)])
        if as_fields:
            sampled, held = _cluster_field(
                P, R, pair_idx, pair_slab, pair_link, members_arr,
                voxels_per_radius=voxels_per_radius, max_voxels=max_voxels,
                detail=detail, surface_smooth_um=surface_smooth_um,
                lens_h_default=lens_h_default, travel_as_tube=travel_as_tube,
            )
            if sampled is None:
                continue
            bodies.append(sampled)
        else:
            mesh, held = _cluster_mesh(
                P, R, pair_idx, pair_slab, pair_link, members_arr,
                voxels_per_radius=voxels_per_radius, max_voxels=max_voxels,
                detail=detail, surface_smooth_um=surface_smooth_um,
                lens_h_default=lens_h_default,
            )
            if mesh.is_empty:
                continue
            bodies.append(mesh)
        # A member the surface did not keep must not be marked covered, or the
        # caller skips its tube fallback and the annotation silently vanishes.
        # Small members lost to the cluster-wide blur render fine as tubes; a
        # LARGE lost member would come back as the very ball this module
        # exists to avoid, so it stays covered and is logged instead.
        small = R[members_arr] <= 2.0 * float(min_radius_um)
        lost_large = int((~held & ~small).sum())
        if lost_large:
            log.warning("voxel cluster kept its mesh but lost %d large "
                        "annotation(s) from the field", lost_large)
        flat_covered[members_arr] = held | ~small

    for row, (s, n) in enumerate(zip(starts, lengths, strict=True)):
        covered[row] = flat_covered[s:s + n]
    return bodies, covered


def voxel_remesh_cell(
    polylines,
    *,
    voxels_per_radius: float = VOXELS_PER_RADIUS,
    max_voxels: int = MAX_VOXELS,
    detail: float = 1.0,
    surface_smooth_um: float = SURFACE_SMOOTH_UM,
    min_radius_um: float = SLAB_MIN_RADIUS_UM,
) -> tuple[list[Mesh], list[np.ndarray]]:
    """Remesh every fat cluster of a cell in one pass per cluster.

    ``polylines`` is a sequence of ``(points, radii)`` segmentation rows.
    Returns ``(meshes, covered)``: the cluster meshes, and one boolean mask
    per input row marking the annotations whose body those meshes now own.
    The caller renders everything uncovered with the tube builders, anchoring
    each thin stretch one covered annotation deep (at the thin radius) so the
    tube reaches inside the envelope.

    ``detail`` scales the grid resolution: 2.0 halves the voxel and is roughly
    eight times the work, 0.5 doubles it.
    """
    return _remesh_cell(
        polylines, as_fields=False, voxels_per_radius=voxels_per_radius,
        max_voxels=max_voxels, detail=detail, surface_smooth_um=surface_smooth_um,
        min_radius_um=min_radius_um,
    )


def voxel_remesh_cell_fields(
    polylines,
    *,
    voxels_per_radius: float = VOXELS_PER_RADIUS,
    max_voxels: int = MAX_VOXELS,
    detail: float = 1.0,
    surface_smooth_um: float = SURFACE_SMOOTH_UM,
    min_radius_um: float = SLAB_MIN_RADIUS_UM,
    travel_as_tube: bool = True,
) -> tuple[list[SampledField], list[np.ndarray]]:
    """:func:`voxel_remesh_cell` without the marching cubes: the clusters'
    fields themselves, for a caller that will contour them together with
    everything else (the ``voxel2`` pathway).  Same ``covered`` semantics;
    ``travel_as_tube`` (default on here) reads sideways-travelling disc runs
    as round cones -- see ``TRAVEL_HEIGHT_RATIO``."""
    return _remesh_cell(
        polylines, as_fields=True, travel_as_tube=travel_as_tube,
        voxels_per_radius=voxels_per_radius,
        max_voxels=max_voxels, detail=detail, surface_smooth_um=surface_smooth_um,
        min_radius_um=min_radius_um,
    )


def voxel_remesh(
    points,
    radii,
    *,
    voxels_per_radius: float = VOXELS_PER_RADIUS,
    max_voxels: int = MAX_VOXELS,
    detail: float = 1.0,
    surface_smooth_um: float = SURFACE_SMOOTH_UM,
    min_radius_um: float = SLAB_MIN_RADIUS_UM,
) -> Mesh:
    """Remesh the fat clusters of a single polyline.

    A convenience wrapper over :func:`voxel_remesh_cell` for one row.  Returns
    the union of the row's cluster meshes; empty when nothing in the row is at
    remeshable scale, so callers can fall back to the tube builders.  Thin
    geometry is never part of the result -- render it with tubes.
    """
    meshes, _covered = voxel_remesh_cell(
        [(points, radii)],
        voxels_per_radius=voxels_per_radius, max_voxels=max_voxels,
        detail=detail, surface_smooth_um=surface_smooth_um,
        min_radius_um=min_radius_um,
    )
    if not meshes:
        return EMPTY_MESH
    return meshes[0] if len(meshes) == 1 else concatenate(meshes)
