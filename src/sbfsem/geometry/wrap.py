"""Cube-fit wrap: a smooth outer envelope fitted to the tube mesh itself.

This is the user-designed pathway: instead of building a distance field from
the annotation discs (the voxel pathway), take the *tube mesh* of the fat
geometry -- whose overall size the adaptive pathway already gets right -- and
fit a smooth outer surface through its vertices with local, robust plane fits.

The algorithm, as designed:

1. **Cubes aligned with the global XYZ axes, only where they interact with
   the mesh.**  A uniform evaluation grid covers the fat cluster; a point is
   evaluated only when tube-mesh samples fall inside its neighborhood cube,
   and the pass is repeated coarse-to-fine, the finer pass confined to the
   band where the coarse pass found the surface ("repeat the process with
   smaller cubes").
2. **Vertex density.**  Each evaluation point weights the samples inside its
   cube by distance; a cube with too few samples abstains.
3. **An initial plane for the vertices inside the cube.**  The signed offset
   from each sample's tangent plane, blended over the cube, is the implicit
   moving-least-squares surface -- the local average plane, bending with the
   curvature the samples trace around dendrites and somas.
4. **Reject vertices too far from the fit and refit.**  Robust reweighting:
   samples whose residual exceeds ``reject_sigma`` standard deviations lose
   their vote, and the plane is refit (``passes`` times).  Both knobs are
   exposed.
5. **The final surface becomes the new wireframe.**  Marching cubes over the
   fitted implicit; interior pockets (tube folds inside a soma) are removed
   by flood-filling the outside first, so only the outer envelope survives.

Thin geometry never enters the fit: like the voxel pathway, everything under
``min_radius_um`` renders with the tube builders, anchored into the wrap.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter, label
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes

from .mesh import EMPTY_MESH, Mesh
from .remesh import SLAB_MIN_RADIUS_UM, flatten_cell

log = logging.getLogger(__name__)

__all__ = ["cube_wrap_cell"]

#: Evaluation grid spacing in microns (before ``detail`` scaling).  Half the
#: adaptive pathway's ring-arc default: the wrap should not be the resolution
#: bottleneck of the mesh it fits.
WRAP_SPACING_UM = 0.4

#: Neighborhood ("cube") half-size, as a multiple of the grid spacing.
WRAP_BLEND = 2.0

#: Samples whose residual from the local plane exceeds this many standard
#: deviations are rejected before the refit.
WRAP_REJECT_SIGMA = 2.0

#: Robust refit passes (step 4 above).
WRAP_PASSES = 2

#: A cube with fewer samples than this abstains rather than guess.
MIN_CUBE_POINTS = 6

#: Grid ceiling per cluster, matching the voxel pathway's budget.
MAX_WRAP_VOXELS = 6_000_000


#: Half-height of the coin an isolated fat annotation is read as, in microns.
#: Matches the z-pad the capsule priors clamp to: an annotation is a disc in
#: its section plane, never a ball.
COIN_HALF_HEIGHT_UM = 0.2


def _coin_samples(center: np.ndarray, radius: float, *,
                  spacing: float = WRAP_SPACING_UM,
                  half_height: float = COIN_HALF_HEIGHT_UM,
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rim-and-cap samples of one disc extruded ``half_height`` up and down.

    The honest reading of an isolated fat annotation.  Sampling its BALL here
    (as an earlier version did) put a full-radius sphere into the fit -- a
    4.8 um annotation at a soma's bottom grew the wrap 4.8 um past the last
    annotated section, the "bubble" under the soma.
    """
    h = float(half_height)
    n_rim = int(np.clip(2 * np.pi * radius / max(spacing * 0.5, 1e-6), 16, 512))
    theta = np.linspace(0.0, 2 * np.pi, n_rim, endpoint=False)
    ring = np.column_stack([np.cos(theta), np.sin(theta), np.zeros(n_rim)])
    levels = (-h, 0.0, h)
    rim_pts = np.concatenate([
        center[None, :] + radius * ring + np.array([0.0, 0.0, z]) for z in levels
    ])
    rim_nrm = np.tile(ring, (len(levels), 1))
    rim_area = np.full(len(rim_pts), 2 * np.pi * radius * 2 * h / max(len(rim_pts), 1))

    n_cap = int(np.clip(12.0 * (radius / max(spacing, 1e-6)) ** 2, 32, 2000))
    k = np.arange(n_cap) + 0.5
    rr = radius * np.sqrt(k / n_cap)
    th = np.pi * (1 + 5 ** 0.5) * k
    cap_xy = np.column_stack([rr * np.cos(th), rr * np.sin(th)])
    cap_area = np.full(n_cap, np.pi * radius ** 2 / n_cap)
    top = center[None, :] + np.column_stack([cap_xy, np.full(n_cap, h)])
    bottom = center[None, :] + np.column_stack([cap_xy, np.full(n_cap, -h)])
    up = np.repeat([[0.0, 0.0, 1.0]], n_cap, axis=0)

    pts = np.vstack([rim_pts, top, bottom])
    nrm = np.vstack([rim_nrm, up, -up])
    area = np.concatenate([rim_area, cap_area, cap_area])
    return pts, nrm, area


def _capsule_union_sdf(pts: np.ndarray, paths,
                       z_pad: float = COIN_HALF_HEIGHT_UM) -> np.ndarray:
    """Signed distance to the union of Z-CLAMPED swept capsule chains.

    The union MINIMUM is what separates a concave patch of genuine outer
    surface (distance ~0: it lies ON the union boundary, however deep the
    valley) from a fold's buried wall (strictly negative: covered by some
    other stretch).  Each pair's capsule is clamped to its own z-interval
    (corner-combined, so it stays a true distance): an unclamped capsule
    bulges a full radius above and below every annotation, and that ball
    union is the amoeba this project keeps having to kill.
    """
    best = np.full(len(pts), np.inf)
    if not len(pts):
        return best
    for pp, rr in paths:
        for i in range(len(pp) - 1):
            a, b = pp[i], pp[i + 1]
            ra, rb = float(rr[i]), float(rr[i + 1])
            if not (np.isfinite(a).all() and np.isfinite(b).all()
                    and np.isfinite(ra) and np.isfinite(rb)):
                continue
            ab = b - a
            l2 = float(ab @ ab)
            t = np.clip((pts - a) @ ab / max(l2, 1e-12), 0.0, 1.0)
            proj = a[None, :] + t[:, None] * ab[None, :]
            d = np.linalg.norm(pts - proj, axis=1) - (ra + t * (rb - ra))
            z_lo = min(a[2], b[2]) - z_pad
            z_hi = max(a[2], b[2]) + z_pad
            dz = np.maximum(z_lo - pts[:, 2], pts[:, 2] - z_hi)
            d = (np.minimum(np.maximum(d, dz), 0.0)
                 + np.hypot(np.maximum(d, 0.0), np.maximum(dz, 0.0)))
            np.minimum(best, d, out=best)
    return best


def _buried_samples(pts: np.ndarray, paths, spacing: float) -> np.ndarray:
    """Samples radially deep inside some segment's capsule at its own z."""
    buried = np.zeros(len(pts), dtype=bool)
    if not len(pts):
        return buried
    depth = 0.5 * spacing
    for pp, rr in paths:
        for i in range(len(pp) - 1):
            a, b = pp[i], pp[i + 1]
            ra, rb = float(rr[i]), float(rr[i + 1])
            if not (np.isfinite(a).all() and np.isfinite(b).all()
                    and np.isfinite(ra) and np.isfinite(rb)):
                continue
            ab = b - a
            l2 = float(ab @ ab)
            t = np.clip((pts - a) @ ab / max(l2, 1e-12), 0.0, 1.0)
            proj = a[None, :] + t[:, None] * ab[None, :]
            d = np.linalg.norm(pts - proj, axis=1) - (ra + t * (rb - ra))
            z_lo = min(a[2], b[2])
            z_hi = max(a[2], b[2])
            dz = np.maximum(np.maximum(z_lo - pts[:, 2], pts[:, 2] - z_hi), 0.0)
            buried |= (d < -depth) & (dz <= depth)
    return buried


def _sample_mesh(mesh: Mesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Face centroids, outward unit normals, and face areas of a tube mesh."""
    v, f = mesh.vertices, mesh.faces
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, c - a)
    twice_area = np.linalg.norm(n, axis=1)
    ok = twice_area > 1e-12
    centers = (a + b + c)[ok] / 3.0
    normals = n[ok] / twice_area[ok, None]
    return centers, normals, twice_area[ok] / 2.0


def _imls(grid: np.ndarray, tree: cKDTree, pts: np.ndarray, nrm: np.ndarray,
          area: np.ndarray, radius: float, reject_sigma: float,
          passes: int) -> np.ndarray:
    """Robust implicit MLS: signed offset from blended local planes.

    Returns NaN where a cube abstains (too few samples).
    """
    out = np.full(len(grid), np.nan)
    hits = tree.query_ball_point(grid, r=radius, workers=-1)
    inv2s2 = 1.0 / (2.0 * (radius / 2.0) ** 2)
    for g, idx in enumerate(hits):
        if len(idx) < MIN_CUBE_POINTS:
            continue
        ii = np.asarray(idx)
        d = grid[g] - pts[ii]
        dist2 = np.einsum("ij,ij->i", d, d)
        w = area[ii] * np.exp(-dist2 * inv2s2)
        off = np.einsum("ij,ij->i", nrm[ii], d)
        f = float((w * off).sum() / w.sum())
        for _ in range(max(int(passes), 0)):
            resid = off - f
            mean = float((w * resid).sum() / w.sum())
            var = float((w * (resid - mean) ** 2).sum() / w.sum())
            sigma = max(np.sqrt(var), 1e-6)
            keepw = w * np.exp(-(resid / (reject_sigma * sigma)) ** 2)
            if keepw.sum() <= 1e-12:
                break
            w = keepw
            f = float((w * off).sum() / w.sum())
        out[g] = f
    return out


def _drop_crumbs(verts: np.ndarray, faces: np.ndarray,
                 keep_fraction: float = 0.02) -> tuple[np.ndarray, np.ndarray]:
    """Keep the components holding at least ``keep_fraction`` of the volume."""
    parent = np.arange(len(verts))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, c in faces:
        ra, rb, rc = find(int(a)), find(int(b)), find(int(c))
        parent[ra] = rb
        parent[find(int(rb))] = rc
    roots = np.array([find(int(f[0])) for f in faces])
    vols: dict[int, float] = {}
    for root in np.unique(roots):
        sel = faces[roots == root]
        a, b, c = verts[sel[:, 0]], verts[sel[:, 1]], verts[sel[:, 2]]
        vols[int(root)] = abs(float(
            np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)
    top = max(vols.values())
    good = {r for r, v in vols.items() if v >= keep_fraction * top}
    keep = np.array([int(r) in good for r in roots])
    return verts, faces[keep]


def _wrap_one(samples: tuple[np.ndarray, np.ndarray, np.ndarray],
              balls: tuple[np.ndarray, np.ndarray], paths, *, spacing: float,
              blend: float, reject_sigma: float, passes: int,
              max_voxels: int) -> tuple[Mesh, np.ndarray]:
    """Wrap one cluster's samples; report which annotation balls are inside.

    Deep inside a soma (or along a fat tube's core) no sample is within reach
    and the fit is undefined, so a prior decides the background sign --
    without it the hollow cores read as "outside", the flood fill leaks in
    through any tube touching the grid edge, and the surface grows an inner
    wall.  The prior is the swept capsule union of ``paths`` (a ball per
    annotation would bulge a full radius above and below each section -- the
    amoeba, again); ``balls`` are only the coverage query points.
    """
    pts, nrm, area = samples
    centers, radii = balls
    held = np.zeros(len(centers), dtype=bool)
    if len(pts) < MIN_CUBE_POINTS:
        return EMPTY_MESH, held

    h = float(spacing)
    lo = pts.min(axis=0) - 3 * h
    hi = pts.max(axis=0) + 3 * h
    if len(centers):
        # The capsule prior reaches an annotation radius past the last sample
        # in-plane; a grid sized from samples alone would cut that region at
        # the wall and marching cubes would leave the surface open there.
        lo = np.minimum(lo, (centers - radii[:, None]).min(axis=0) - 3 * h)
        hi = np.maximum(hi, (centers + radii[:, None]).max(axis=0) + 3 * h)
    span = hi - lo
    counts = np.maximum(np.ceil(span / h).astype(int) + 1, 2)
    total = float(np.prod(counts.astype(float)))
    if total > max_voxels:
        h *= (total / max_voxels) ** (1.0 / 3.0)
        counts = np.maximum(np.ceil(span / h).astype(int) + 1, 2)
        log.debug("cube wrap coarsened to %.3f um (%s)", h, tuple(counts))

    tree = cKDTree(pts)
    radius = blend * h

    # The evaluation band: cells holding samples, dilated a neighborhood out.
    # The fit runs only there -- "cubes only where they interact with the
    # mesh" -- and the finer sweep is exactly this band at full resolution.
    occ = np.zeros(tuple(counts), dtype=bool)
    oidx = np.clip(np.rint((pts - lo) / h).astype(int), 0, counts - 1)
    occ[oidx[:, 0], oidx[:, 1], oidx[:, 2]] = True
    band_f = binary_dilation(occ, iterations=int(np.ceil(blend)) + 1)

    # Background sign from the z-clamped swept capsule chains (slightly
    # shrunk so the fitted surface, not the prior, decides the boundary).
    inside_f = np.zeros(tuple(counts), dtype=bool)
    for pp, rr_arr in paths:
        for k in range(len(pp) - 1):
            a, b = pp[k], pp[k + 1]
            ra, rb = 0.9 * float(rr_arr[k]), 0.9 * float(rr_arr[k + 1])
            if not (np.isfinite(a).all() and np.isfinite(b).all()):
                continue
            rmx = max(ra, rb)
            lo_c = np.minimum(a, b) - rmx
            hi_c = np.maximum(a, b) + rmx
            lo_c[2] = min(a[2], b[2]) - 0.2
            hi_c[2] = max(a[2], b[2]) + 0.2
            i0 = np.maximum(np.floor((lo_c - lo) / h).astype(int), 0)
            i1 = np.minimum(np.ceil((hi_c - lo) / h).astype(int) + 1, counts)
            if (i0 >= i1).any():
                continue
            ax = [lo[m] + np.arange(i0[m], i1[m]) * h for m in range(3)]
            gx, gy, gz = np.meshgrid(*ax, indexing="ij")
            q = np.stack([gx, gy, gz], axis=-1)
            ab = b - a
            l2 = float(ab @ ab)
            t = np.clip((q - a) @ ab / max(l2, 1e-12), 0.0, 1.0)
            proj = a + t[..., None] * ab
            d = np.linalg.norm(q - proj, axis=-1)
            rloc = ra + t * (rb - ra)
            inside_f[i0[0]:i1[0], i0[1]:i1[1], i0[2]:i1[2]] |= d <= rloc

    axes = [lo[k] + np.arange(counts[k]) * h for k in range(3)]
    field = np.full(tuple(counts), np.nan, dtype=np.float32)
    sel = np.nonzero(band_f)
    if len(sel[0]):
        fgrid = np.column_stack([axes[0][sel[0]], axes[1][sel[1]], axes[2][sel[2]]])
        fitted = _imls(fgrid, tree, pts, nrm, area, radius,
                       reject_sigma, passes)
        # The capsule union is the fit's lower bound: where junction data is
        # weak or contradictory the fit tears craters into the body, and the
        # union of swept tubes -- the geometry the adaptive pathway already
        # shows -- is the least the wrap is allowed to be.  The fit only ever
        # refines OUTWARD from it, bridging folds and creases smoothly.
        caps = _capsule_union_sdf(fgrid, paths)
        field[sel] = np.fmin(fitted, caps).astype(np.float32)

    background = np.where(inside_f, -4.0 * h, 4.0 * h).astype(np.float32)
    real_neg = ~np.isnan(field) & (field < 0)
    field = np.where(np.isnan(field), background, field)

    # The ball prior reaches a full annotation radius past what the tube mesh
    # actually covers (a line3 end cap is shallow), which would pinch off a
    # phantom bubble beyond every tube tip.  Interior is only interior when
    # its negative region is anchored in genuinely fitted negative cells.
    neg_lab, _n_neg = label(field < 0)
    anchored = np.unique(neg_lab[real_neg])
    phantom = (field < 0) & ~np.isin(neg_lab, anchored[anchored > 0])
    field[phantom] = 4.0 * h

    # Step 5's cleanup: anything positive that cannot be reached from the
    # grid boundary is a pocket inside the body (a tube folding through the
    # soma's interior); flip it inside so only the outer envelope remains.
    positive = field > 0
    lab, _n = label(positive)
    border = np.zeros_like(positive)
    border[0, :, :] = border[-1, :, :] = True
    border[:, 0, :] = border[:, -1, :] = True
    border[:, :, 0] = border[:, :, -1] = True
    outside_labels = np.unique(lab[border & positive])
    outside = np.isin(lab, outside_labels[outside_labels > 0])
    field[positive & ~outside] = -h

    if not (field.min() < 0 < field.max()):
        return EMPTY_MESH, held
    # A light blur melts the sub-cell flakes left where the fit and the
    # capsule bound disagree by noise; at 0.8 cells it cannot move any
    # genuine feature by more than a fraction of the grid spacing.
    np.clip(field, -4.0 * h, 4.0 * h, out=field)
    field = gaussian_filter(field, sigma=0.8, mode="nearest")
    try:
        verts, faces, _nn, _vv = marching_cubes(field, level=-1e-4)
    except (ValueError, RuntimeError):
        return EMPTY_MESH, held
    # Robust fits shed debris: small closed shells where the evaluation band
    # meets abstaining cubes.  A cluster is one connected body by
    # construction, so keep only components carrying real volume.
    verts, faces = _drop_crumbs(verts, faces)

    if len(centers):
        idx = np.clip(np.rint((centers - lo) / h).astype(int), 0,
                      counts - 1)
        held = ~outside[idx[:, 0], idx[:, 1], idx[:, 2]]
    return Mesh(verts * h + lo, faces).fix_winding(), held


def cube_wrap_cell(
    polylines,
    *,
    min_radius_um: float = SLAB_MIN_RADIUS_UM,
    spacing_um: float = WRAP_SPACING_UM,
    blend: float = WRAP_BLEND,
    reject_sigma: float = WRAP_REJECT_SIGMA,
    passes: int = WRAP_PASSES,
    detail: float = 1.0,
    target_arc_um: float | None = None,
    max_voxels: int = MAX_WRAP_VOXELS,
) -> tuple[list[Mesh], list[np.ndarray]]:
    """Wrap every fat cluster of a cell; same contract as voxel_remesh_cell.

    The input to each fit is the *adaptive tube mesh* of the cluster's fat
    stretches -- so the wrap inherits the tube union's overall size, which is
    the size the adaptive pathway shows, and only replaces its folded surface
    with the fitted outer envelope.
    """
    from .cylinder import TARGET_RING_ARC_UM, _link_mesh, _split_fat_jumps, segment_mesh

    flat = flatten_cell(polylines, min_radius_um=min_radius_um)
    if flat is None:
        return [], [np.zeros(len(np.asarray(p).reshape(-1, 3)), dtype=bool)
                    for p, _r in polylines]
    P, R, finite, fat, ia, ib, starts, lengths, clusters = flat
    covered = [np.zeros(n, dtype=bool) for n in lengths]
    if not clusters:
        return [], covered

    spacing = float(spacing_um) / max(float(detail), 1e-3)
    arc = TARGET_RING_ARC_UM if target_arc_um is None else float(target_arc_um)

    # Fat stretches per row, as (global_start, points, radii) tube inputs.
    member_set = {m for ms in clusters.values() for m in ms}
    stretches: list[tuple[int, np.ndarray, np.ndarray]] = []
    for s, n in zip(starts, lengths, strict=True):
        i = s
        while i < s + n:
            if i in member_set:
                j = i
                while j + 1 < s + n and (j + 1) in member_set:
                    j += 1
                stretches.append((i, P[i:j + 1], R[i:j + 1]))
                i = j + 1
            else:
                i += 1

    flat_covered = np.zeros(len(P), dtype=bool)
    meshes: list[Mesh] = []
    for members in clusters.values():
        mset = set(members)
        own = [st for st in stretches if st[0] in mset]
        parts = []
        in_stretch: set[int] = set()
        link_meshes: list[Mesh] = []
        split_paths: list[tuple[np.ndarray, np.ndarray]] = []
        for g, pp, rr in own:
            if len(pp) < 2:
                continue
            # A same-section lateral jump between fat discs is a branch
            # link, not a tube: swept at girth it fed the fit a sausage
            # through unannotated tissue and the wrap faithfully wrapped
            # it.  Split there and connect at attachment scale instead.
            pieces, jump_links = _split_fat_jumps(pp, rr, min_radius_um)
            for piece_pts, piece_rad in pieces:
                m = segment_mesh(piece_pts, piece_rad, adaptive_rings=True,
                                 target_arc_um=arc, cap_ends=True)
                if not m.is_empty:
                    parts.append(m)
                    split_paths.append((piece_pts, piece_rad))
            for a, b, r_link in jump_links:
                link = _link_mesh(a, b, r_link, adaptive_rings=True,
                                  target_arc_um=arc)
                if not link.is_empty:
                    link_meshes.append(link)
            in_stretch.update(range(g, g + len(pp)))
        if not parts:
            continue
        sampled = [_sample_mesh(m) for m in parts]
        stretch_paths = split_paths
        # A fat annotation alone between thin neighbours has no tube of its
        # own; sample its coin directly so the wrap absorbs it instead of
        # leaving a barnacle of fallback geometry on the soma.  A degenerate
        # path keeps the priors (and the honesty filter below) aware of it.
        for m0 in members:
            if m0 in in_stretch:
                continue
            sp, sn, sa = _coin_samples(P[m0], float(R[m0]), spacing=spacing)
            sampled.append((sp, sn, sa))
            split_paths.append((np.vstack([P[m0], P[m0]]),
                                np.array([float(R[m0]), float(R[m0])])))
        pts = np.vstack([t[0] for t in sampled])
        nrm = np.vstack([t[1] for t in sampled])
        area = np.concatenate([t[2] for t in sampled])
        # A fold puts whole tube walls INSIDE the union; fitting those buried
        # samples turns the deep field into noise (phantom pockets, and
        # channels the outside flood fill can follow into the soma).  Buried
        # means radially deep inside some OTHER segment's capsule while at
        # that segment's own z -- the z gate is what keeps a soma's top cap,
        # which is radially deep but above every covering interval, out of
        # the prune.  Genuine surface sits at radial distance ~0 everywhere
        # and survives.
        buried = _buried_samples(pts, stretch_paths, spacing)
        pts, nrm, area = pts[~buried], nrm[~buried], area[~buried]
        # Vertical rings: where the centerline steps mostly sideways between
        # stacked sections, the tube's rings tilt toward vertical and reach
        # up to a full radius above and below the annotated sections -- the
        # bulge that hung ball "bubbles" under somas and made neighbouring
        # cells appear to interpenetrate.  The z-clamped capsule union is the
        # honest extent, so samples far outside it lose their vote.
        excess = _capsule_union_sdf(pts, stretch_paths)
        honest = excess <= max(0.5, 1.25 * spacing)
        pts, nrm, area = pts[honest], nrm[honest], area[honest]
        marr0 = np.array(members, dtype=int)
        mesh, held = _wrap_one((pts, nrm, area), (P[marr0], R[marr0]),
                               stretch_paths, spacing=spacing, blend=blend,
                               reject_sigma=reject_sigma, passes=passes,
                               max_voxels=max_voxels)
        if mesh.is_empty:
            continue
        meshes.append(mesh)
        # The thin connectors render alongside the envelope, exactly as the
        # voxel field draws its links.
        meshes.extend(link_meshes)
        marr = np.array(members, dtype=int)
        flat_covered[marr[held]] = True

    for row, (s, n) in enumerate(zip(starts, lengths, strict=True)):
        covered[row] = flat_covered[s:s + n]
    return meshes, covered
