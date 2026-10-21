"""Tube meshes around dendrite polylines.

Ports ``+sbfsem/+render/Cylinder.m`` (the ``Line3`` algorithm and its ``gencyl``
fallback), ``lib/gencyl.m``, ``util/renders/curveToMesh2.m`` and the three small
helpers ``getAB``, ``normalCircle`` and ``minimizeRot``.

``Line3`` builds a per-annotation tangent frame, places an ``N``-point circle in
that plane, and solves for the in-plane rotation offset with Nelder-Mead so
successive rings' vertices line up before triangulation, capping each end with a
hemisphere.  ``gencyl`` is the fallback for segments where that optimization fails
to converge, and for segments too short for it.

Deviations (see DEVIATIONS.md):

* The second convergence check set the fallback flag to 0 on failure where the
  first set it to 1, so a segment whose alignment failed silently kept its
  malformed mesh (F079).  The flag is now latching.
* Coincident consecutive annotations divided by zero and produced an all-NaN
  segment that was reported only as a printed count (F081).  Zero-length steps
  are dropped before the normals are computed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import numpy as np
from scipy.optimize import minimize

from ..progress import report
from ..util.memory import trim_memory
from .mesh import EMPTY_MESH, Mesh, concatenate
from .remesh import REMESH_MIN_RADIUS_UM
from .wrap import WRAP_BLEND, WRAP_PASSES, WRAP_REJECT_SIGMA, WRAP_SPACING_UM

__all__ = [
    "CIRCLE_POINTS",
    "CYLINDER_POINTS",
    "CURVE_POINTS",
    "get_ab",
    "normal_circle",
    "line3_mesh",
    "gencyl_mesh",
    "curve_to_mesh",
    "segment_mesh",
    "cylinder_render",
]

log = logging.getLogger(__name__)

#: ``Cylinder.CIRCLEPTS`` — vertices per ring in the Line3 algorithm.
CIRCLE_POINTS = 10

#: Target spacing between adjacent vertices around a ring, in microns, when
#: adaptive tessellation is on.  The fixed 10 points give a 10.9 um soma a
#: 6.9 um facet and a 0.18 um dendrite a 0.11 um one -- the same budget for
#: geometry 62x apart in scale, which is why somas look like prisms.
TARGET_RING_ARC_UM = 0.35
#: Below 8 a tube stops reading as round.  The ceiling exists so one soma
#: does not swallow the whole triangle budget; at 128 a 10 um soma's facets
#: are half a micron, and a 10 um soma is also where Ring detail used to
#: saturate silently -- a 0.35 um target already wants 196 vertices there, so
#: with the old cap of 64 every setting produced the identical clamped ring.
MIN_RING_POINTS = 8
MAX_RING_POINTS = 128


def adaptive_ring_points(radii, target_arc_um: float = TARGET_RING_ARC_UM) -> int:
    """Ring vertex count for a segment, from its widest radius.

    Constant within a segment: the ring-to-ring face topology assumes every
    ring has the same vertex count, so varying it along a segment would need a
    different triangulation rather than a different number.
    """
    widest = float(np.nanmax(np.abs(np.asarray(radii, dtype=float))))
    if not np.isfinite(widest) or widest <= 0:
        return CIRCLE_POINTS
    wanted = int(round(2.0 * np.pi * widest / max(target_arc_um, 1e-6)))
    return int(np.clip(wanted, MIN_RING_POINTS, MAX_RING_POINTS))
#: ``Cylinder.CYLINDERPTS`` — vertices around the gencyl fallback tube.
CYLINDER_POINTS = 20
#: ``Cylinder.BRIDGEPTS`` — steps between annotations in the gencyl fallback.
BRIDGE_POINTS = 2
#: ``Cylinder.CURVEPTS`` — corners for the variable-radius curve mesher.
CURVE_POINTS = 16

_RANDOM_V = np.array([0.57745, 0.5774, 0.57735])


def get_ab(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two orthonormal vectors spanning the plane normal to ``normal``.

    Ports ``util/renders/getAB.m`` (originally from ``plot3t``).
    """
    normal = np.asarray(normal, dtype=float).ravel()
    denom = float(_RANDOM_V @ normal)
    if denom == 0.0:
        # The reference vector is orthogonal to the normal; nudge it.
        denom = 1e-12
    a = normal - _RANDOM_V / denom
    norm_a = np.linalg.norm(a)
    if norm_a == 0.0:
        a = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        a = a - normal * (a @ normal)
        norm_a = np.linalg.norm(a)
    a = a / norm_a
    b = np.cross(normal, a)
    nb = np.linalg.norm(b)
    b = b / nb if nb else b
    return a, b


def normal_circle(angles_deg, angle_offset: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """A unit circle in the plane spanned by ``a`` and ``b``.

    Ports ``util/renders/normalCircle.m``.
    """
    t = np.deg2rad(np.asarray(angles_deg, dtype=float) + angle_offset)
    return np.cos(t)[:, None] * a[None, :] + np.sin(t)[:, None] * b[None, :]


def _rotation_error(angle_offset: float, reference: np.ndarray, a, b) -> float:
    """Ports ``util/renders/minimizeRot.m``."""
    circle = normal_circle((0.0, 120.0, 240.0), float(angle_offset), a, b)
    return float(((circle - reference) ** 2).sum())


def _minimise_rotation(angle_offset: float, reference: np.ndarray, a, b) -> tuple[float, bool]:
    result = minimize(
        lambda x: _rotation_error(x[0], reference, a, b),
        x0=np.array([angle_offset], dtype=float),
        method="Nelder-Mead",
        options={"xatol": 1e-4, "fatol": 1e-4, "maxiter": 200, "disp": False},
    )
    return float(result.x[0]), bool(result.success)


def _clean_polyline(points: np.ndarray, radii: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drop repeated points, which would make the tangent ``0/0`` (F081)."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    radii = np.asarray(radii, dtype=float).ravel()
    if len(points) < 2:
        return points, radii
    step = np.linalg.norm(np.diff(points, axis=0), axis=1)
    keep = np.concatenate([[True], step > 0])
    return points[keep], radii[keep]


def line3_mesh(
    points, radii, n_circle: int = CIRCLE_POINTS
) -> tuple[Mesh, bool]:
    """Tube mesh around a polyline with aligned rings and hemispherical caps.

    Returns ``(mesh, needs_fallback)``; ``needs_fallback`` is ``True`` when any
    Nelder-Mead alignment failed to converge and the caller should use
    :func:`gencyl_mesh` instead.
    """
    data, radius = _clean_polyline(points, radii)
    n_points = len(data)
    if n_points < 2:
        return EMPTY_MESH, True

    needs_fallback = False
    n = int(n_circle)
    angles = np.arange(n) * (360.0 / n)

    step = np.linalg.norm(np.diff(data, axis=0), axis=1)
    buffer_dist = float(radius.max())
    buffer_dist = min(buffer_dist, float(step.min()) / 2.2)

    normal = np.diff(data, axis=0) / step[:, None]
    normal = np.vstack([normal, normal[-1]])

    rings: list[np.ndarray] = []
    angle_offset = 0.0

    a, b = get_ab(normal[0])
    circle = normal_circle(angles, angle_offset, a, b)

    # Leading hemisphere: 9 rings shrinking to a point.
    for j in np.arange(5.0, 0.999, -0.5):
        r = float(np.sqrt(max(0.0, 1.0 - (j / 5.0) ** 2)))
        rings.append(r * radius[0] * circle + (data[0] - (j / 5.0) * buffer_dist * normal[0]))

    reference = normal_circle((0.0, 120.0, 240.0), 0.0, a, b)

    for i in range(n_points - 1):
        i_normal, i_data = normal[i], data[i]
        j_normal, j_data = normal[i + 1], data[i + 1]

        a, b = get_ab(i_normal)
        circle = normal_circle(angles, angle_offset, a, b)
        rings.append(circle * radius[i] + (i_data + buffer_dist * i_normal))
        rings.append(circle * radius[i + 1] + (j_data - buffer_dist * i_normal))

        ij_normal = i_normal + j_normal
        ij_norm = np.linalg.norm(ij_normal)
        ij_normal = ij_normal / ij_norm if ij_norm else i_normal
        ij_data = 0.5858 * j_data + 0.4142 * (
            0.5 * ((j_data + buffer_dist * j_normal) + (j_data - buffer_dist * i_normal))
        )

        a, b = get_ab(ij_normal)
        angle_offset, converged = _minimise_rotation(angle_offset, reference, a, b)
        needs_fallback = needs_fallback or not converged

        reference = normal_circle((0.0, 120.0, 240.0), angle_offset, a, b)
        circle = normal_circle(angles, angle_offset, a, b)
        rings.append(circle * radius[i + 1] + ij_data)

        a, b = get_ab(j_normal)
        angle_offset, converged = _minimise_rotation(angle_offset, reference, a, b)
        # F079: the MATLAB copy cleared the flag here instead of setting it.
        needs_fallback = needs_fallback or not converged
        reference = normal_circle((0.0, 120.0, 240.0), angle_offset, a, b)

    # Trailing hemisphere, using the last frame's circle.
    for j in np.arange(1.0, 5.001, 0.5):
        r = float(np.sqrt(max(0.0, 1.0 - (j / 5.0) ** 2)))
        rings.append(
            r * radius[-1] * circle + (data[-1] + (j / 5.0) * buffer_dist * normal[-1])
        )

    vertices = np.vstack(rings)
    faces = _ring_faces(len(rings), n)
    return Mesh(vertices, faces), needs_fallback


def _ring_faces(n_rings: int, n: int) -> np.ndarray:
    """Triangulate consecutive rings of ``n`` vertices each.

    Reproduces the ``Fb`` construction in ``Cylinder.Line3``, converted to
    zero-based indices.
    """
    k = np.arange(n)
    lower = np.column_stack([k, k + n, k + n + 1])
    upper = np.column_stack([k + 1, k, k + n + 1])
    lower[-1, 2] = n  # wrap the seam
    upper[-1, 2] = n
    upper[-1, 0] = 0
    base = np.vstack([lower, upper])
    if n_rings < 2:
        return np.zeros((0, 3), dtype=np.int64)
    offsets = (np.arange(n_rings - 1) * n)[:, None, None]
    return (base[None, :, :] + offsets).reshape(-1, 3).astype(np.int64)


def _fan_caps(mesh: Mesh, rings: list[tuple[np.ndarray, np.ndarray]]) -> Mesh:
    """Close open tube ends with triangle fans.

    ``rings`` pairs each open boundary ring's vertex indices with the center
    point to fan to.  Each fan is wound so its normal points away from the
    other caps' centroid -- a global ``fix_winding`` flip cannot repair one
    inverted cap, so the orientation has to be right per fan.
    """
    vertices = mesh.vertices
    face_blocks = [mesh.faces]
    centers = [np.asarray(c, dtype=float) for _r, c in rings]
    for (ring_idx, _c), center in zip(rings, centers, strict=True):
        ci = len(vertices)
        vertices = np.vstack([vertices, center[None, :]])
        ring = ring_idx.astype(np.int64)
        faces = np.column_stack([
            np.full(len(ring), ci, dtype=np.int64), ring, np.roll(ring, -1),
        ])
        others = [c for c in centers if c is not center]
        if others:
            outward = center - np.mean(others, axis=0)
            a = mesh.vertices[faces[0, 1]] - center
            b = mesh.vertices[faces[0, 2]] - center
            if float(np.cross(a, b) @ outward) < 0:
                faces = faces[:, [0, 2, 1]]
        face_blocks.append(faces)
    return Mesh(vertices, np.vstack(face_blocks))


def gencyl_mesh(
    points,
    radii,
    n_between: int = BRIDGE_POINTS,
    n_around: int = CYLINDER_POINTS,
    cap_ends: bool = False,
) -> Mesh:
    """Quaternion-rotated cylinder segments around a centerline.

    Ports ``lib/gencyl.m`` plus the ``surf2patch(..., 'triangles')`` step that
    followed every call to it.
    """
    data, radius = _clean_polyline(points, radii)
    if len(data) < 2:
        return EMPTY_MESH
    n_between = max(2, int(n_between))
    n_around = max(3, int(n_around))

    directions = np.diff(data, axis=0)
    lengths = np.linalg.norm(directions, axis=1)

    n = len(data)
    if n_between > 2:
        from scipy.interpolate import make_interp_spline

        knots = np.arange(1, n + 1, dtype=float)
        target = np.linspace(1, n, n_between * (n - 1) - (n - 2))
        radius = make_interp_spline(knots, radius, k=min(3, n - 1))(target)

    z_axis = np.array([0.0, 0.0, 1.0])
    grids_x, grids_y, grids_z = [], [], []
    r_start = 0
    for i in range(n - 1):
        r_end = r_start + n_between
        seg_radii = radius[r_start : r_start + n_between]
        if len(seg_radii) < n_between:  # short interpolation tail
            seg_radii = np.resize(seg_radii, n_between)
        r_start = r_end - 1

        tx, ty, tz = _cylinder(seg_radii, n_around)
        axis = np.cross(z_axis, directions[i])
        cosang = float(np.clip(directions[i] @ z_axis / lengths[i], -1.0, 1.0))
        rotation = _rotation_matrix(axis, float(np.arccos(cosang)))
        stacked = np.vstack([tx.ravel(), ty.ravel(), lengths[i] * tz.ravel()])
        rotated = rotation @ stacked + data[i][:, None]
        shape = tx.shape
        grids_x.append(rotated[0].reshape(shape))
        grids_y.append(rotated[1].reshape(shape))
        grids_z.append(rotated[2].reshape(shape))

    x = np.vstack(grids_x)
    y = np.vstack(grids_y)
    z = np.vstack(grids_z)
    mesh = surf_to_mesh(x, y, z)
    if not cap_ends or mesh.is_empty:
        # Open ends are byte-faithful to MATLAB's gencyl, whose patches were
        # displayed double-sided so nobody ever saw the pipe's interior.  A
        # front-face-culled viewer does see it, so the modern pathways close
        # the ends with fans instead.
        return mesh
    rows_n, cols_n = x.shape
    ring0 = np.arange(cols_n - 1) * rows_n            # first grid row
    ring1 = ring0 + rows_n - 1                        # last grid row
    return _fan_caps(mesh, [(ring0, data[0]), (ring1, data[-1])])


def _cylinder(radii: np.ndarray, n_around: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MATLAB's ``cylinder(R, N)``: an ``(len(R), N+1)`` parametric surface."""
    radii = np.asarray(radii, dtype=float).ravel()
    theta = np.linspace(0.0, 2 * np.pi, n_around + 1)
    x = radii[:, None] * np.cos(theta)[None, :]
    y = radii[:, None] * np.sin(theta)[None, :]
    z = np.tile(np.linspace(0.0, 1.0, len(radii))[:, None], (1, n_around + 1))
    return x, y, z


def _rotation_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    """Quaternion rotation, matching ``gencyl``'s ``rotation_quat``.

    One deviation: a segment exactly antiparallel to +z has a zero rotation
    axis with a pi angle.  MATLAB's rotation_quat divided by zero there and
    the NaN vertices silently dropped the segment; returning the identity
    instead (an earlier port) drew the tube mirrored to the wrong side of its
    start point.  A half-turn about x maps +z to -z, which is the geometry
    the annotator drew.
    """
    norm = np.linalg.norm(axis)
    if norm == 0 or not np.isfinite(angle):
        if np.isfinite(angle) and abs(abs(angle) - np.pi) < 1e-12:
            return np.diag([1.0, -1.0, -1.0])
        return np.eye(3)
    axis = axis / norm
    half = angle / 2.0
    a = np.cos(half)
    b, c, d = axis * np.sin(half)
    return np.array(
        [
            [1 - 2 * c * c - 2 * d * d, 2 * b * c - 2 * d * a, 2 * b * d + 2 * c * a],
            [2 * b * c + 2 * d * a, 1 - 2 * b * b - 2 * d * d, 2 * c * d - 2 * b * a],
            [2 * b * d - 2 * c * a, 2 * c * d + 2 * b * a, 1 - 2 * c * c - 2 * b * b],
        ]
    )


def surf_to_mesh(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> Mesh:
    """MATLAB's ``surf2patch(x, y, z, 'triangles')``.

    MATLAB flattens the grid column-major, so the vertex order interleaves rows.
    Matching that is not cosmetic: it is what makes a gencyl segment's vertex
    buffer line up index-for-index with the MATLAB render, which is how the
    parity tests compare meshes at all.
    """
    rows, cols = x.shape
    vertices = np.column_stack(
        [x.ravel(order="F"), y.ravel(order="F"), z.ravel(order="F")]
    )
    idx = np.arange(rows * cols).reshape((rows, cols), order="F")
    tl = idx[:-1, :-1].ravel()
    tr = idx[:-1, 1:].ravel()
    bl = idx[1:, :-1].ravel()
    br = idx[1:, 1:].ravel()
    faces = np.vstack([np.column_stack([tl, bl, br]), np.column_stack([tl, br, tr])])
    return Mesh(vertices, faces)


def curve_to_mesh(points, radii, n_corners: int = CURVE_POINTS) -> Mesh:
    """Variable-radius tube from ``util/renders/curveToMesh2.m``.

    Smoothing is deliberately not applied on this path: the MATLAB code disabled
    it because it degrades the result.
    """
    curve = np.asarray(points, dtype=float).reshape(-1, 3)
    radii = np.asarray(radii, dtype=float).ravel()
    n_nodes = len(curve)
    if n_nodes < 2:
        return EMPTY_MESH

    t = np.linspace(0.0, 2 * np.pi, n_corners + 1)[:-1]
    base = np.column_stack([np.cos(t), np.sin(t), np.zeros_like(t)])

    vertices = np.empty((n_nodes * n_corners, 3), dtype=float)
    for i in range(n_nodes):
        node = curve[i]
        nxt = curve[(i + 1) % n_nodes]
        tangent = nxt - node
        norm = np.linalg.norm(tangent)
        tangent = tangent / norm if norm else np.array([0.0, 0.0, 1.0])
        # cart2sph2: theta from +Z, phi in the XY plane.
        theta = float(np.arccos(np.clip(tangent[2], -1.0, 1.0)))
        phi = float(np.arctan2(tangent[1], tangent[0]))
        rot = _rot_z(phi) @ _rot_y(theta)
        vertices[i * n_corners : (i + 1) * n_corners] = (radii[i] * base) @ rot.T + node

    quads = _quad_strip_faces(n_nodes, n_corners)
    faces = np.vstack([quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]])
    # No cap option here: the port keeps byte-parity with curveToMesh2.m,
    # whose last ring is mirrored by the wrap-around tangent and whose seam
    # band is one quad short -- fans on those rings close nothing.
    return Mesh(vertices, faces)


def _quad_strip_faces(n_nodes: int, n_corners: int) -> np.ndarray:
    n_verts = n_nodes * n_corners
    inds = np.arange(n_verts)
    add1 = np.tile(
        np.concatenate([np.ones(n_corners - 1, dtype=np.int64), [1 - n_corners]]), n_nodes
    )
    faces = np.column_stack(
        [
            inds,
            (inds + add1) % n_verts,
            (inds + n_corners + add1) % n_verts,
            (inds + n_corners) % n_verts,
        ]
    )
    return faces[: -(n_corners + 1)]


def _rot_y(angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _rot_z(angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def segment_mesh(
    points,
    radii,
    *,
    method: int = 1,
    curve_points: int = CURVE_POINTS,
    adaptive_rings: bool = False,
    target_arc_um: float = TARGET_RING_ARC_UM,
    cap_ends: bool = False,
) -> Mesh:
    """Mesh one dendrite segment, choosing the algorithm as ``Cylinder`` did.

    ``adaptive_rings`` scales the ring vertex count with the segment's radius.
    It defaults off so the MATLAB-faithful geometry is what the library
    produces unless the caller asks otherwise.
    """
    data, radius = _clean_polyline(points, radii)
    if len(data) < 2:
        return EMPTY_MESH
    if method == 2:
        n = (adaptive_ring_points(radius, target_arc_um) if adaptive_rings
             else curve_points)
        return curve_to_mesh(data, radius, n)
    n_circle = (adaptive_ring_points(radius, target_arc_um) if adaptive_rings
                else CIRCLE_POINTS)
    if len(data) > 2:
        mesh, needs_fallback = line3_mesh(data, radius, n_circle)
        if not needs_fallback and not mesh.is_empty:
            candidate = mesh.drop_nan()
            if not candidate.is_empty:
                return candidate.fix_winding()
    return gencyl_mesh(data, radius, cap_ends=cap_ends).drop_nan().fix_winding()


def _uncovered_runs(
    pts: np.ndarray, rad: np.ndarray, covered: np.ndarray
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split one row into the stretches the cluster meshes do not own.

    Each maximal stretch of finite, uncovered annotations becomes a tube
    polyline.  When the stretch abuts a covered annotation, that annotation is
    kept as an anchor -- at the *thin* neighbour's radius, not its own -- so
    the tube reaches into the envelope without capping it with a soma-sized
    ball.  A row with nothing covered comes back whole, which is also the
    fallback when a cluster declined to mesh.
    """
    if len(rad) != len(pts):
        # voxel_remesh_cell keeps such a row's slot with an all-False mask so
        # the caller's rows line up; meet it halfway instead of crashing.
        rad = np.full(len(pts), np.nan)
    finite = np.isfinite(pts).all(axis=1) & np.isfinite(rad)
    n = len(pts)
    runs: list[tuple[np.ndarray, np.ndarray]] = []
    i = 0
    while i < n:
        if covered[i] or not finite[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and finite[j + 1] and not covered[j + 1]:
            j += 1
        seg_p = [pts[k] for k in range(i, j + 1)]
        seg_r = [float(rad[k]) for k in range(i, j + 1)]
        if i - 1 >= 0 and covered[i - 1] and finite[i - 1]:
            seg_p.insert(0, pts[i - 1])
            seg_r.insert(0, min(float(rad[i - 1]), float(rad[i])))
        if j + 1 < n and covered[j + 1] and finite[j + 1]:
            seg_p.append(pts[j + 1])
            seg_r.append(min(float(rad[j + 1]), float(rad[j])))
        if len(seg_p) >= 2:
            runs.append((np.asarray(seg_p), np.asarray(seg_r)))
        i = j + 1
    return runs


#: Fat-ish stretches the cluster fields lost (or that sit just under the
#: remesh threshold) are lofted on their own small grid rather than swept:
#: stacked discs crumple a swept tube at any radius, and its cap ends read
#: as ball "bubbles" half-buried in somas.
RESCUE_MIN_RADIUS_UM = 1.0

#: A same-section pair whose disc rims are at least this far apart is a
#: branch link no matter how thin the discs are: genuine in-plane dendrite
#: steps keep their rims within ~1.4 um of each other (c33 rim-gap p99 is
#: 1.1 um), while Viking links to remote annotations measure 2-50 um.
LINK_RIM_GAP_UM = 1.5

#: Raw triangles allowed per triangle of a Hybrid outline half in the field
#: (``cylinder_render``'s ``extra_faces``).  The discs' sheet may be marched at
#: three times its Face cap (``unify.RAW_PER_CAPPED_FACE``) because a whole-cell
#: grid at dendrite resolution spends triangles on a soma the decimator takes
#: back; the outline raster is marched at about its own pixel, so its own count
#: and a quarter more holds it (c1178 at 0.1 um: 430,092 triangles alone).
EXTRA_RAW_PER_FACE = 1.25

#: A stranded single annotation (both its jumps removed) still renders --
#: as its disc extruded this far up and down, a coin, not a ball.
STRAND_COIN_HALF_UM = 0.1


def _split_fat_jumps(
    pts: np.ndarray, rad: np.ndarray, min_radius_um: float,
    strand_coins: list[tuple[np.ndarray, float]] | None = None,
) -> tuple[list[tuple[np.ndarray, np.ndarray]],
           list[tuple[np.ndarray, np.ndarray, float]]]:
    """Split a polyline at same-section lateral jumps between discs.

    Such a pair is a branch link, not a tube: sweeping it at its own girth
    draws a radius-thick sausage through unannotated tissue (and made
    neighbouring cells appear to interpenetrate).  Returns the stretches to
    sweep plus ``(a, b, radius)`` thin connectors for the removed jumps --
    the same reading the voxel field uses.  Two cases cut: fat pairs whose
    jump outruns the bigger disc, and any pair -- thin included -- whose
    disc rims are ``LINK_RIM_GAP_UM`` apart (c33 held a thin annotation
    linked 50 um across its section; sweeping that was 14 % of the cell's
    surface through unannotated tissue).  Short thin hops stay swept:
    dendrites genuinely run in-plane.  Jumps wider than ``MAX_TIE_UM`` get
    no connector at all -- at that distance a wire through unannotated
    tissue is dishonest (the tie pass draws the same line); a single
    annotation stranded by its removed jumps becomes a coin-height stretch
    so it still renders where it was annotated.  When ``strand_coins`` is a
    list, such strands are appended to it as ``(center, radius)`` instead --
    for a caller with an exact coin primitive of its own (``voxel2``).
    """
    from .remesh import DISC_STACK_FACTOR, LINK_RADIUS_UM

    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    rad = np.asarray(rad, dtype=float).ravel()
    if len(pts) < 2 or len(rad) != len(pts):
        return [(pts, rad)], []
    cuts = []
    for i in range(len(pts) - 1):
        ra, rb = abs(float(rad[i])), abs(float(rad[i + 1]))
        if not (np.isfinite(pts[i]).all() and np.isfinite(pts[i + 1]).all()
                and np.isfinite(ra) and np.isfinite(rb)):
            continue
        r_max = max(ra, rb)
        dz = abs(float(pts[i + 1, 2] - pts[i, 2]))
        if dz >= DISC_STACK_FACTOR * r_max:
            continue  # crossing sections: a genuinely descending stretch
        dxy = float(np.hypot(pts[i + 1, 0] - pts[i, 0], pts[i + 1, 1] - pts[i, 1]))
        fat_jump = min(ra, rb) >= float(min_radius_um) and dxy >= DISC_STACK_FACTOR * r_max
        far_jump = dxy - ra - rb >= LINK_RIM_GAP_UM
        if fat_jump or far_jump:
            cuts.append(i)
    if not cuts:
        return [(pts, rad)], []
    parts: list[tuple[np.ndarray, np.ndarray]] = []
    links: list[tuple[np.ndarray, np.ndarray, float]] = []
    start = 0
    for i in cuts:
        parts.append((pts[start:i + 1], rad[start:i + 1]))
        jump = float(np.linalg.norm(pts[i + 1] - pts[i]))
        if jump <= MAX_TIE_UM:
            links.append((pts[i], pts[i + 1],
                          min(LINK_RADIUS_UM, abs(float(rad[i])),
                              abs(float(rad[i + 1])))))
        else:
            log.info("dropping a %.1f um same-section link (> %.0f um): the "
                     "far side renders where its own annotations are",
                     jump, MAX_TIE_UM)
        start = i + 1
    parts.append((pts[start:], rad[start:]))
    kept: list[tuple[np.ndarray, np.ndarray]] = []
    for p, r in parts:
        if len(p) == 1 and np.isfinite(p).all() and np.isfinite(r).all() \
                and abs(float(r[0])) > 0:
            if strand_coins is not None:
                strand_coins.append((np.asarray(p[0], dtype=float).copy(),
                                     abs(float(r[0]))))
                continue
            lift = np.array([0.0, 0.0, STRAND_COIN_HALF_UM])
            p = np.vstack([p[0] - lift, p[0] + lift])
            r = np.array([abs(float(r[0]))] * 2)
        if len(p) >= 2:
            kept.append((p, r))
    return kept, links


def _link_mesh(a: np.ndarray, b: np.ndarray, radius: float, **kwargs) -> Mesh:
    """The thin connecting tube for one removed jump."""
    return segment_mesh(
        np.vstack([a, b]), np.array([radius, radius]), cap_ends=True, **kwargs,
    )


#: Two remesh-branch pieces whose surfaces come at least this close are one
#: visual body already; only wider gaps get a tie.
TIE_TOUCH_UM = 0.15

#: Gaps wider than this are left open (and logged): at that distance the
#: pieces are not "one body the render failed to join" but genuinely remote
#: geometry, and a tie would draw a long wire through unannotated tissue.
MAX_TIE_UM = 20.0

#: A tie states connectivity, it has no girth of its own -- thinner than even
#: ``LINK_RADIUS_UM``, and under the 0.3 um detached-body threshold so the
#: tie's own ring vertices always sit within it of the attachment point.
TIE_RADIUS_UM = 0.25

#: Ring spacing along a tie.  Dense on purpose: the junction must carry mesh
#: vertices no matter how coarse the surfaces it lands on are.
TIE_STEP_UM = 0.1


def _tie_mesh(a: np.ndarray, b: np.ndarray) -> Mesh:
    """A densely ringed thin capsule from ``a`` to ``b``, extended past both
    so it penetrates the surfaces it joins."""
    u = b - a
    norm = float(np.linalg.norm(u))
    u = u / norm if norm > 1e-9 else np.array([0.0, 0.0, 1.0])
    ext = 2.0 * TIE_RADIUS_UM
    a = a - u * ext
    b = b + u * ext
    length = norm + 2.0 * ext
    n = max(int(np.ceil(length / TIE_STEP_UM)) + 1, 2)
    t = np.linspace(0.0, 1.0, n)[:, None]
    pts = a[None, :] * (1.0 - t) + b[None, :] * t
    return gencyl_mesh(pts, np.full(n, TIE_RADIUS_UM),
                       cap_ends=True).drop_nan().fix_winding()


class _Vertices(Protocol):
    """What the tie search reads of a piece: its vertices (a :class:`Mesh`, or
    the Voxel remesh's pieces without the faces nothing here reads)."""

    vertices: np.ndarray

    @property
    def is_empty(self) -> bool: ...


def _tie_segments(meshes: Sequence[_Vertices], max_tie_um: float, *,
                  touch_um: float = TIE_TOUCH_UM,
                  sample: int = 20_000,
                  attached: Callable[[Any], bool] | None = None,
                  ) -> list[tuple[np.ndarray, np.ndarray]]:
    """Where to tie: the residual gaps between remesh-branch pieces.

    ``attached(piece)`` says a piece is already part of a body this build
    cannot see -- the Hybrid method's closed-curve volume, which the disc
    pieces on either side of a traced soma all enter.  Two attached pieces
    are one body through it, so their gap is united without a wire.  Without
    the check c1178's disc half laid three: two through its soma (11 and
    18 um) and one along its base (7 um).

    The voxel/wrap branch assembles a cell from many pieces -- cluster
    envelopes, rescue lofts, tubes, links -- that connect only where their
    surfaces actually meet.  Three things break that: a rescue loft consumes
    its stretch's coverage so no tube spans the boundary pair into the
    neighbouring envelope; a tube's one-annotation-deep anchor can land
    outside a surface the Envelope blur pulled inward; and Viking itself holds
    unlinked same-cell components whose nearest annotations were never joined
    (c1178 has seven, its arbor detaching 3.3 um from the soma).  Rather than
    patch each producer, close whatever gaps remain: pieces whose surfaces
    come within ``touch_um`` are one body already; the rest are joined along
    the minimum spanning tree of their surface-to-surface gaps, each tie a
    ``TIE_RADIUS_UM`` wire extended past both surfaces so it penetrates the
    bodies it joins.  Gaps wider than ``max_tie_um`` stay open and are logged.

    Pieces larger than ``sample`` vertices are searched through a random
    subsample (the voxel2 pathway's components run to half a million
    vertices); the endpoints of every accepted tie are then refined exactly.

    Returns the ``(a, b)`` endpoints of every tie; :func:`_tie_pieces` meshes
    them as wires, the ``voxel2`` pathway stamps them into its field.
    """
    from scipy.spatial import cKDTree

    from .remesh import _UnionFind

    live = [m for m in meshes if not m.is_empty]
    if len(live) < 2:
        return []
    rng = np.random.default_rng(0)
    subs = [m.vertices if len(m.vertices) <= sample
            else m.vertices[rng.choice(len(m.vertices), sample, replace=False)]
            for m in live]
    trees = [cKDTree(s) for s in subs]
    # A subsample's own spacing bounds how far the true nearest pair can sit
    # from the pair the subsample finds.
    spacing = [float(np.mean(tr.query(s, k=2)[0][:, 1])) if len(s) > 1 else 0.0
               for tr, s in zip(trees, subs, strict=True)]
    bounds = np.array([[m.vertices.min(axis=0), m.vertices.max(axis=0)]
                       for m in live])
    reach = max(float(max_tie_um), float(touch_um))

    # Nearest pair per piece pair, once.
    gaps: dict[tuple[int, int], tuple[float, np.ndarray, np.ndarray]] = {}
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            sep = np.maximum(bounds[j, 0] - bounds[i, 1],
                             bounds[i, 0] - bounds[j, 1]).max()
            if sep > reach:
                continue
            small, big = (i, j) if len(subs[i]) <= len(subs[j]) else (j, i)
            d, idx = trees[big].query(subs[small])
            k = int(np.argmin(d))
            gaps[(i, j)] = (float(d[k]), subs[small][k], subs[big][idx[k]])

    uf = _UnionFind(len(live))
    for (i, j), (d, _pa, _pb) in gaps.items():
        if d <= touch_um:
            uf.union(i, j)
    held = [bool(attached(m)) for m in live] if attached is not None else None

    segments: list[tuple[np.ndarray, np.ndarray]] = []
    # Kruskal over the gap graph: every tie is the shortest one available.
    for (i, j), (d, pa, pb) in sorted(gaps.items(), key=lambda kv: kv[1][0]):
        if uf.find(i) == uf.find(j):
            continue
        if d > max_tie_um:
            break
        if held is not None and held[i] and held[j]:
            log.debug("tie of %.1f um already made by the traced volume; skipped", d)
            uf.union(i, j)
            continue
        small, big = (i, j) if len(live[i].vertices) <= len(live[j].vertices) else (j, i)
        if len(subs[small]) < len(live[small].vertices) or \
                len(subs[big]) < len(live[big].vertices):
            # Refine on the full vertex sets, but only around the pair found:
            # the true nearest pair lies within the subsamples' spacing of it.
            reach_um = d + 2.0 * (spacing[small] + spacing[big])
            va, vb = live[small].vertices, live[big].vertices
            ca = va[np.linalg.norm(va - pb, axis=1) <= reach_um]
            cb = vb[np.linalg.norm(vb - pa, axis=1) <= reach_um]
            if len(ca) and len(cb):
                de, ie = cKDTree(cb).query(ca)
                k = int(np.argmin(de))
                pa, pb = ca[k], cb[ie[k]]
        segments.append((np.asarray(pa, dtype=float), np.asarray(pb, dtype=float)))
        uf.union(i, j)

    groups = len({uf.find(i) for i in range(len(live))})
    if groups > 1:
        widest = min((d for (i, j), (d, _a, _b) in gaps.items()
                      if uf.find(i) != uf.find(j)), default=float("inf"))
        log.warning("leaving %d piece group(s) untied: nearest gap %.1f um "
                    "exceeds %.1f um", groups, widest, max_tie_um)
    return segments


def _tie_pieces(meshes: list[Mesh], max_tie_um: float,
                attached: Callable[[Mesh], bool] | None = None) -> list[Mesh]:
    """Thin capsule wires for every gap :func:`_tie_segments` finds."""
    ties: list[Mesh] = []
    for pa, pb in _tie_segments(meshes, max_tie_um, attached=attached):
        tie = _tie_mesh(pa, pb)
        if not tie.is_empty:
            ties.append(tie)
    return ties



def _skeleton_items(
    polylines: list[tuple[np.ndarray, np.ndarray]], min_radius_um: float,
) -> list[tuple]:
    """The raw annotation skeleton as pieces, for pathways that take every
    annotation as a skeleton sample (the convolution surface, Ultraliser's
    sphere soma): each row split at same-section jumps into ``("stretch",
    points, radii)``, the jumps as ``("link", a, b, radius)`` and stranded
    annotations as ``("coin", center, radius)``."""
    items: list[tuple] = []
    for pts, rad in polylines:
        if len(rad) != len(pts):
            continue
        coins: list[tuple[np.ndarray, float]] = []
        parts, links = _split_fat_jumps(pts, rad, min_radius_um, strand_coins=coins)
        items.extend(("stretch", p, r) for p, r in parts)
        items.extend(("link", a, b, r) for a, b, r in links)
        items.extend(("coin", c, r) for c, r in coins)
    return items


def _assemble_pieces(
    polylines: list[tuple[np.ndarray, np.ndarray]],
    *,
    remesh: str,
    voxel_detail: float,
    voxel_smooth_um: float,
    voxel_min_radius_um: float,
    target_arc_um: float,
    wrap_spacing_um: float,
    wrap_blend: float,
    wrap_reject_sigma: float,
    wrap_passes: int,
) -> list[tuple]:
    """Decide what every annotation of a cell renders as, on the remesh pathways.

    Shared by ``voxel``, ``wrap`` and ``voxel2``.  One body per fat cluster
    across the WHOLE cell first, so a soma split over several segmentation
    rows never becomes a stack of per-row shells; then the fat-ish stretches
    the clusters lost, lofted on their own grid; then any stray fat annotation
    as a coin; and finally the thin remainder as tube stretches -- each
    anchored one covered annotation deep into the body it leaves, at the thin
    radius -- with same-section branch links as thin connectors.

    Returns the pieces in emission order as tagged tuples: ``("mesh", Mesh,
    origin)`` or, on ``voxel2``, ``("field", SampledField, origin)`` for the
    bodies, ``origin`` being ``"cluster"`` or ``"rescue"``;
    ``("stretch", points, radii)``; ``("link", a, b, radius)``; and on
    ``voxel2`` ``("coin", center, radius)`` for an annotation that renders as
    a lens.  ``voxel`` and ``wrap`` mesh the stretches and links with the tube
    builders; ``voxel2`` stamps everything into one field.
    """
    from .remesh import (
        flatten_cell,
        voxel_remesh,
        voxel_remesh_cell,
        voxel_remesh_cell_fields,
    )

    as_fields = remesh == "voxel2"
    body_tag = "field" if as_fields else "mesh"
    items: list[tuple] = []

    def remesh_rows(rows, min_radius: float):
        if as_fields:
            return voxel_remesh_cell_fields(
                rows, detail=voxel_detail, surface_smooth_um=voxel_smooth_um,
                min_radius_um=min_radius,
            )
        return voxel_remesh_cell(
            rows, detail=voxel_detail, surface_smooth_um=voxel_smooth_um,
            min_radius_um=min_radius,
        )

    if remesh == "wrap":
        from .wrap import cube_wrap_cell

        cluster_meshes, covered = cube_wrap_cell(
            polylines, min_radius_um=voxel_min_radius_um,
            spacing_um=wrap_spacing_um, blend=wrap_blend,
            reject_sigma=wrap_reject_sigma, passes=wrap_passes,
            detail=voxel_detail, target_arc_um=target_arc_um,
        )
        items.extend(("mesh", m, "cluster") for m in cluster_meshes)
    else:
        bodies, covered = remesh_rows(polylines, voxel_min_radius_um)
        items.extend((body_tag, b, "cluster") for b in bodies)

    # A fat-ish stretch the cluster fields lost -- or one sitting just
    # under the remesh threshold -- must not fall through to the tube
    # sweep: stacked discs crumple a swept tube at any radius, and the
    # cap balls read as "bubbles" half-buried where annotations stop.
    # Loft each such stretch on its own small grid instead, and mark
    # what the loft holds as covered so the thin remainder anchors in.
    for (pts, rad), cov in zip(polylines, covered, strict=True):
        if len(rad) != len(pts):
            continue
        finite_row = np.isfinite(pts).all(axis=1) & np.isfinite(rad)
        i = 0
        n = len(pts)
        while i < n:
            if cov[i] or not finite_row[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and finite_row[j + 1] and not cov[j + 1]:
                j += 1
            sub_rad = np.abs(rad[i:j + 1])
            if j > i and np.nanmax(sub_rad) >= RESCUE_MIN_RADIUS_UM:
                rescue_bodies, rescue_cov = remesh_rows(
                    [(pts[i:j + 1], rad[i:j + 1])], RESCUE_MIN_RADIUS_UM,
                )
                if rescue_bodies:
                    items.extend((body_tag, b, "rescue") for b in rescue_bodies)
                    cov[i:j + 1] |= rescue_cov[0]
            i = j + 1

    # A fat annotation the cluster field lost (a stack thinner than a
    # voxel, or erased by the blur) must not fall through to the tube
    # builders: a short fat capsule with hemispherical caps is a BALL,
    # which is exactly the nodule these pathways exist to avoid.  Give
    # each stray its honest coin shape instead, and mark it covered so
    # the neighbouring tubes anchor into it at their thin radius.  But
    # only inside clusters that actually produced coverage -- when a
    # whole cluster declined, per-annotation coins would replace the
    # documented whole-row tube fallback, which is always sane.
    flat = flatten_cell(polylines, min_radius_um=voxel_min_radius_um)
    alive = np.zeros(0, dtype=bool)
    if flat is not None:
        P_all, _R, _fin, _fat, _ia, _ib, _st, _ln, clusters = flat
        flat_cov = np.concatenate(covered) if covered else np.zeros(0, bool)
        alive = np.zeros(len(P_all), dtype=bool)
        for members in clusters.values():
            marr = np.array(members, dtype=int)
            if flat_cov[marr].any():
                alive[marr] = True
    offset = 0
    for (pts, rad), cov in zip(polylines, covered, strict=True):
        row_alive = (alive[offset:offset + len(pts)] if len(alive)
                     else np.zeros(len(pts), bool))
        offset += len(pts)
        if len(rad) != len(pts):
            continue
        finite_row = np.isfinite(pts).all(axis=1) & np.isfinite(rad)
        stray = (finite_row & ~cov & row_alive
                 & (np.abs(rad) >= voxel_min_radius_um))
        for k in np.nonzero(stray)[0]:
            if as_fields:
                items.append(("coin", pts[k].copy(), abs(float(rad[k]))))
                cov[k] = True
                continue
            disc = voxel_remesh(pts[k:k + 1], rad[k:k + 1],
                                detail=voxel_detail,
                                surface_smooth_um=voxel_smooth_um,
                                min_radius_um=voxel_min_radius_um)
            if not disc.is_empty:
                items.append(("mesh", disc))
                cov[k] = True
        for sub_pts, sub_rad in _uncovered_runs(pts, rad, cov):
            coins: list[tuple[np.ndarray, float]] | None = [] if as_fields else None
            parts, links = _split_fat_jumps(sub_pts, sub_rad, voxel_min_radius_um,
                                            strand_coins=coins)
            items.extend(("stretch", p, r) for p, r in parts)
            items.extend(("link", a, b, r) for a, b, r in links)
            if coins:
                items.extend(("coin", c, r) for c, r in coins)
    return items


#: Named combinations of the post-generation options.  ``original`` is the
#: MATLAB behaviour and the library/API default, so the port stays
#: parity-first; the web app defaults to ``voxel2`` (shown as "Voxel remesh"),
#: the recommended look.
#: (smooth_mode, adaptive_rings, remesh) per pathway.  A tuple rather than a
#: dict so the types stay concrete for the checker.
PATHWAYS: dict[str, tuple[str, bool, str]] = {
    "original": ("laplacian", False, "none"),
    "adaptive_taubin": ("taubin", True, "none"),
    # Taubin, not none: the disc-loft leaves sharp rims where a run of stacked
    # discs ends, and a volume-preserving pass rounds them without touching
    # anything else.
    "voxel": ("taubin", False, "voxel"),
    # The cube-fit wrap: fits a smooth outer envelope to the adaptive tube
    # mesh of each fat cluster, so it inherits the tube union's size while
    # discarding its folded interior.  Thin geometry stays adaptive tubes.
    "wrap": ("taubin", True, "wrap"),
    # Voxel remesh 2: the same cluster fields as ``voxel``, plus every thin
    # stretch as an exact round-cone field, contoured together on ONE
    # whole-cell grid at dendrite resolution -- one surface, not pieces.
    "voxel2": ("taubin", False, "voxel2"),
    # Convolution surface (Front. Neuroinform. 2022, doi:10.3389/fninf.2022.
    # 953930): quartic kernels summed along the whole skeleton and contoured
    # at the iso-value -- junctions blend where the voxel pathways crease.
    "convolution": ("taubin", False, "convolution"),
    # Ultraliser (Abdellah et al. 2023, Brief. Bioinform.): proxy geometry
    # voxelized to an occupancy grid, contoured, then smoothed with an
    # inflate step and coarsened.  It smooths itself, so the generic pass
    # is off.
    "ultraliser": ("none", False, "ultraliser"),
}

#: The pathways that contour one whole-cell field (they share the Fine
#: voxel, Face cap and Tie knobs).
FIELD_PATHWAYS = ("voxel2", "convolution", "ultraliser")


def _remesh_items(rows, use_remesh: str, *, voxel_detail: float, voxel_smooth_um: float,
                  voxel_min_radius_um: float, target_arc_um: float, wrap_spacing_um: float,
                  wrap_blend: float, wrap_reject_sigma: float, wrap_passes: int,
                  ultra_soma: str) -> list:
    """The pieces a remeshing pathway builds a cell from (the segmentation's
    ``rows``): what :func:`cylinder_render` meshes or, for a field pathway,
    contours as one field, and what :func:`field_pieces` hands an estimate."""
    polylines = [
        (np.asarray(row.XYZum, dtype=float).reshape(-1, 3),
         np.asarray(row.Rum, dtype=float).ravel())
        for row in rows
    ]
    if use_remesh == "convolution":
        # The paper convolves along the whole skeleton: every annotation
        # pair is a segment, same-section jumps are links, strands are
        # points.  No cluster fields -- the soma is the skeleton too.
        items = _skeleton_items(polylines, voxel_min_radius_um)
    else:
        items = _assemble_pieces(
            polylines, remesh="voxel2" if use_remesh == "ultraliser" else use_remesh,
            voxel_detail=voxel_detail,
            voxel_smooth_um=voxel_smooth_um, voxel_min_radius_um=voxel_min_radius_um,
            target_arc_um=target_arc_um, wrap_spacing_um=wrap_spacing_um,
            wrap_blend=wrap_blend, wrap_reject_sigma=wrap_reject_sigma,
            wrap_passes=wrap_passes,
        )
    if use_remesh == "ultraliser" and ultra_soma == "sphere":
        # Ultraliser's soma starts as an icosphere of the soma's mean
        # radius (then pulled toward the neurites); offered here so the
        # skeleton-tool reading of our soma sections can be seen.
        from .remesh import flatten_cell

        spheres: list[tuple] = []
        flat = flatten_cell(polylines, min_radius_um=voxel_min_radius_um)
        if flat is not None:
            P_all, R_all, _fin, _fat, _ia, _ib, _st, _ln, clusters = flat
            for members in clusters.values():
                marr = np.array(members, dtype=int)
                spheres.append(("sphere", P_all[marr].mean(axis=0),
                                float(R_all[marr].mean())))
        items = spheres + [it for it in items
                           if not (it[0] == "field" and len(it) > 2 and it[2] == "cluster")]
    return items


def field_pieces(
    neuron,
    *,
    pathway: str,
    segmentation=None,
    voxel_detail: float = 1.0,
    voxel_smooth_um: float = 0.5,
    voxel_min_radius_um: float = REMESH_MIN_RADIUS_UM,
    target_arc_um: float = TARGET_RING_ARC_UM,
    wrap_spacing_um: float = WRAP_SPACING_UM,
    wrap_blend: float = WRAP_BLEND,
    wrap_reject_sigma: float = WRAP_REJECT_SIGMA,
    wrap_passes: int = WRAP_PASSES,
    ultra_soma: str = "discs",
) -> list:
    """The pieces a field pathway (``voxel2``, ``convolution``, ``ultraliser``)
    contours ``neuron`` from, as :func:`cylinder_render` makes them, for sizing
    its grid before a build (:func:`~sbfsem.geometry.unify.plan_voxel`)."""
    from .segmentation import dendrite_segmentation

    if pathway not in FIELD_PATHWAYS:
        raise ValueError(f"pathway must be one of {FIELD_PATHWAYS}; got {pathway!r}")
    result = segmentation if segmentation is not None else dendrite_segmentation(neuron)
    return _remesh_items(
        list(result.table.itertuples()), pathway, voxel_detail=voxel_detail,
        voxel_smooth_um=voxel_smooth_um, voxel_min_radius_um=voxel_min_radius_um,
        target_arc_um=target_arc_um, wrap_spacing_um=wrap_spacing_um, wrap_blend=wrap_blend,
        wrap_reject_sigma=wrap_reject_sigma, wrap_passes=wrap_passes, ultra_soma=ultra_soma)


def cylinder_render(
    neuron,
    *,
    method: int = 1,
    smooth_iterations: int = 1,
    reduce_fraction: float = 1.0,
    curve_points: int = CURVE_POINTS,
    segmentation=None,
    pathway: str = "original",
    smooth_mode: str | None = None,
    adaptive_rings: bool | None = None,
    remesh: str | None = None,
    voxel_detail: float = 1.0,
    voxel_smooth_um: float = 0.5,
    voxel_min_radius_um: float = REMESH_MIN_RADIUS_UM,
    tie_um: float = MAX_TIE_UM,
    target_arc_um: float = TARGET_RING_ARC_UM,
    post_order: str = "smooth_first",
    wrap_spacing_um: float = WRAP_SPACING_UM,
    wrap_blend: float = WRAP_BLEND,
    wrap_reject_sigma: float = WRAP_REJECT_SIGMA,
    wrap_passes: int = WRAP_PASSES,
    voxel2_um: float = 0.0,
    voxel2_faces: int = 400_000,
    voxel2_blend_um: float = 0.0,
    exact_tube_deg: float = 0.0,
    conv_support: float = 2.0,
    ultra_soma: str = "discs",
    ultra_smooth: int = 15,
    ultra_lambda: float = 0.5,
    ultra_inflate: float = 0.52,
    attached_elsewhere: Callable[[Mesh], bool] | None = None,
    extra_items: list | None = None,
    extra_faces: int = 0,
    max_auto_um: float | None = None,
) -> Mesh:
    """Build a single tube mesh for a whole neuron.

    ``attached_elsewhere(piece)`` tells the tie step that a piece is already
    part of a body outside this build (the Hybrid method's closed-curve
    volume); two such pieces are one body through it, so no wire is laid
    between them.

    ``extra_items`` are more pieces for the whole-cell field of ``voxel2`` and
    ``ultraliser`` (:func:`~sbfsem.geometry.unify.unified_mesh`): the Hybrid
    method's outline half, ``("curve", CurveVolume, area_um2)``, which makes
    the outlines and the discs one surface.  ``extra_faces`` (the outline
    half's own triangles) is added to the Face cap, so the surface keeps the
    triangles the two halves had, and the raw budget widens for it
    (:func:`~sbfsem.geometry.unify.unified_mesh`'s ``curve_raw_faces``, its
    own count and a quarter more, ``EXTRA_RAW_PER_FACE``); ``max_auto_um``
    keeps an automatic voxel no coarser than the raster.  Other pathways
    refuse them.

    Ports the ``sbfsem.render.Cylinder`` constructor plus ``condense``.

    The pipeline is three stages, and only the last two can be reordered:

    1. **Generation** -- rings are laid around the centerline. ``adaptive_rings``
       scales their vertex count with the local radius. This is not a
       post-process and cannot be moved: everything after it operates on the
       mesh it produces.
    2. **Smoothing** -- ``smooth_mode`` is ``"none"``, ``"taubin"`` (volume
       preserving), or ``"laplacian"`` (matGeom's ``smoothMesh``, the MATLAB
       default, which collapses the mesh; kept for parity).
    3. **Decimation** -- ``reduce_fraction`` collapses triangles.

    ``post_order`` swaps 2 and 3. Smoothing first works on the full-resolution
    mesh and gives a better surface; decimating first is cheaper because the
    smoother then has fewer vertices to move.

    ``pathway`` names a combination of the three: ``original`` reproduces
    MATLAB, ``adaptive_taubin`` scales the ring count and smooths without
    collapsing, ``voxel`` replaces generation of the fat bodies with a
    distance field and marching cubes, and ``voxel2`` contours the *whole*
    cell as one field (``voxel2_um`` fine voxel, 0 = from the annotations;
    ``voxel2_faces`` triangle cap after smoothing; ``voxel2_blend_um``
    junction fillet).  ``convolution`` sums quartic kernels along the skeleton
    (``conv_support`` = kernel radius per neurite radius, the paper's 2) and
    ``ultraliser`` voxelizes proxies to occupancy and smooth-inflates the
    contour (``ultra_soma`` ``discs`` or ``sphere``; ``ultra_smooth``
    iterations of ``ultra_lambda`` smoothing and ``ultra_inflate``
    re-inflation); both share the ``voxel2_*`` grid and face-cap knobs.
    ``exact_tube_deg`` > 0 (``voxel2`` and ``ultraliser``) draws a thin
    stretch's pairs of discs within that many degrees of vertical as the hull
    of the two discs rather than a round cone (:class:`~sbfsem.geometry.unify.DiscHull`).
    Explicit options override the preset.
    """
    from .segmentation import dendrite_segmentation

    if pathway not in PATHWAYS:
        raise ValueError(f"pathway must be one of {tuple(PATHWAYS)}; got {pathway!r}")
    preset_smooth, preset_rings, preset_remesh = PATHWAYS[pathway]
    # An explicit argument wins over the pathway's preset, which is what lets
    # the parity tests pin one option without naming a pathway.
    use_smooth: str = preset_smooth if smooth_mode is None else smooth_mode
    use_rings: bool = preset_rings if adaptive_rings is None else adaptive_rings
    use_remesh: str = preset_remesh if remesh is None else remesh
    if use_remesh not in ("none", "voxel", "wrap", *FIELD_PATHWAYS):
        raise ValueError(
            f"remesh must be none, voxel, wrap, voxel2, convolution or ultraliser; got {use_remesh!r}")
    if ultra_soma not in ("discs", "sphere"):
        raise ValueError(f"ultra_soma must be discs or sphere; got {ultra_soma!r}")
    if use_smooth not in ("none", "taubin", "laplacian"):
        raise ValueError(f"smooth_mode must be none, taubin or laplacian; got {use_smooth!r}")
    if post_order not in ("smooth_first", "decimate_first"):
        raise ValueError(
            f"post_order must be smooth_first or decimate_first; got {post_order!r}")
    if extra_items and use_remesh not in ("voxel2", "ultraliser"):
        raise ValueError(f"extra field pieces need the voxel2 or ultraliser pathway; got {pathway!r}")
    # The Face cap of the surface: the discs' own, plus what an outline half in
    # the same field brought (0 keeps every triangle, as before).
    face_cap = int(voxel2_faces) + int(extra_faces) if voxel2_faces > 0 else 0

    result = segmentation if segmentation is not None else dendrite_segmentation(neuron)
    rows = list(result.table.itertuples())
    # Open tube ends were invisible in MATLAB's double-sided display; the
    # modern pathways close them.  ``original`` stays byte-faithful.
    caps = pathway != "original"
    meshes = []
    if use_remesh in ("voxel", "wrap", *FIELD_PATHWAYS):
        report("assembling pieces")
        items = _remesh_items(
            rows, use_remesh, voxel_detail=voxel_detail, voxel_smooth_um=voxel_smooth_um,
            voxel_min_radius_um=voxel_min_radius_um, target_arc_um=target_arc_um,
            wrap_spacing_um=wrap_spacing_um, wrap_blend=wrap_blend,
            wrap_reject_sigma=wrap_reject_sigma, wrap_passes=wrap_passes, ultra_soma=ultra_soma)
        if use_remesh in FIELD_PATHWAYS:
            # Everything -- the cluster fields, the thin stretches as exact
            # round cones, links and coins -- is one field, contoured once
            # on a whole-cell grid at dendrite resolution: one surface, so
            # a dendrite leaving the soma is part of the soma's sheet rather
            # than a tube pushed into it.  Ties are stamped into the same
            # field.  See :mod:`sbfsem.geometry.unify`.
            from .unify import raw_budget, unified_mesh

            # The raw sheet may be three times the cap it is decimated to --
            # more is memory spent on triangles the decimator removes -- and
            # never more than the module's ceiling.
            raw_faces = raw_budget(voxel2_faces)
            mode = {"voxel2": "min", "convolution": "sum", "ultraliser": "occupancy"}[use_remesh]
            # An outline half in the field is part of the one surface, so the
            # tie step has nothing outside it to ask about.
            unified = unified_mesh(
                [*items, *(extra_items or [])], voxel_um=voxel2_um,
                blend_um=voxel2_blend_um if use_remesh == "voxel2" else 0.0, tie_um=tie_um,
                lens_h=STRAND_COIN_HALF_UM, max_raw_faces=raw_faces,
                mode=mode, support=conv_support,
                attached=None if extra_items else attached_elsewhere,
                max_auto_um=max_auto_um if extra_items else None,
                curve_raw_faces=int(EXTRA_RAW_PER_FACE * extra_faces) if voxel2_faces > 0 else 0,
                hull_tilt_deg=exact_tube_deg if use_remesh != "convolution" else 0.0,
            )
            # The field's blocks and their triangles, let go once welded, stay with
            # the process as glibc heap unless handed back: here, before the
            # smoothing takes its own (on the server, c1178 at 0.042 um under a
            # Face cap of 3.4 million: 180 MB; the smoothing 955 -> 790 MB).
            trim_memory(collect=False)
            if use_remesh == "ultraliser" and not unified.is_empty:
                # Ultraliser's optimizer: Laplacian smoothing with an inflate
                # step to dampen the shrinking of thin parts, then coarsening
                # (our quadric decimation to the face cap, in the decimate
                # stage below).
                unified = unified.smooth_inflate(int(ultra_smooth), float(ultra_lambda),
                                                 float(ultra_inflate))
            if not unified.is_empty:
                meshes.append(unified)
            del unified
        else:
            for i, item in enumerate(items):
                report("meshing pieces", i, len(items))
                kind = item[0]
                if kind == "mesh":
                    meshes.append(item[1])
                elif kind == "stretch":
                    mesh = segment_mesh(
                        item[1], item[2], method=method, curve_points=curve_points,
                        adaptive_rings=use_rings, target_arc_um=target_arc_um,
                        cap_ends=True,
                    )
                    if not mesh.is_empty:
                        meshes.append(mesh)
                elif kind == "link":
                    link = _link_mesh(item[1], item[2], item[3], method=method,
                                      curve_points=curve_points,
                                      adaptive_rings=use_rings,
                                      target_arc_um=target_arc_um)
                    if not link.is_empty:
                        meshes.append(link)
            # One cell must render as one visual body: close whatever gaps the
            # piece producers left (see _tie_segments for why they exist at all).
            if tie_um > 0 and len(meshes) > 1:
                meshes.extend(_tie_pieces(meshes, tie_um, attached=attached_elsewhere))
    else:
        for i, row in enumerate(rows):
            report("sweeping tubes", i, len(rows))
            # The modern tube pathways drop the girth from same-section
            # branch links too; ``original`` keeps sweeping them, byte-
            # faithful to MATLAB.
            if caps:
                parts, links = _split_fat_jumps(
                    np.asarray(row.XYZum, dtype=float).reshape(-1, 3),
                    np.asarray(row.Rum, dtype=float).ravel(),
                    REMESH_MIN_RADIUS_UM,
                )
            else:
                parts = [(row.XYZum, row.Rum)]
                links = []
            for part_pts, part_rad in parts:
                mesh = segment_mesh(
                    part_pts, part_rad, method=method, curve_points=curve_points,
                    adaptive_rings=use_rings, target_arc_um=target_arc_um,
                    cap_ends=caps,
                )
                if not mesh.is_empty:
                    meshes.append(mesh)
            for a, b, r_link in links:
                link = _link_mesh(a, b, r_link, method=method,
                                  curve_points=curve_points,
                                  adaptive_rings=use_rings,
                                  target_arc_um=target_arc_um)
                if not link.is_empty:
                    meshes.append(link)
    if not meshes:
        log.warning("c%s produced no renderable segments", getattr(neuron, "id", "?"))
        return EMPTY_MESH

    n_segments = len(meshes)
    if use_remesh in FIELD_PATHWAYS and n_segments == 1:
        # The whole-cell sheet is this build's own: smoothing and decimating
        # make new meshes, so it needs no copy, and nothing else holds it while
        # they do (at 7 million raw triangles a copy is some 250 MB).
        combined = meshes.pop()
    else:
        combined = concatenate(meshes)
        meshes.clear()

    def smooth(m: Mesh) -> Mesh:
        if not smooth_iterations or use_smooth == "none":
            return m
        report("smoothing")
        if use_smooth == "taubin":
            # A whole-cell sheet built on the last build's grid (manual moves) is
            # smoothed again only around what changed (unify.taubin_on_grid).
            from .unify import taubin_on_grid

            # (Ultraliser's own smoothing has moved the sheet first.)
            local = (taubin_on_grid(m, smooth_iterations)
                     if use_remesh in FIELD_PATHWAYS and use_remesh != "ultraliser" else None)
            return local if local is not None else m.taubin(smooth_iterations)
        # Laplacian is what MATLAB did, and MATLAB skipped it for curveToMesh.
        return m if method == 2 else m.smooth(smooth_iterations)

    def decimate(m: Mesh) -> Mesh:
        fraction = float(reduce_fraction)
        if use_remesh in FIELD_PATHWAYS and face_cap > 0 and len(m) * fraction > face_cap:
            # A whole-cell sheet at dendrite resolution spends triangles
            # uniformly; quadric decimation takes them back off the flat
            # soma and keeps them on the thin arbor.
            fraction = float(face_cap) / len(m)
        if fraction >= 1.0:
            return m
        report("decimating")
        if use_remesh in FIELD_PATHWAYS:
            from .unify import BLOCK, decimates_in_chunks

            if decimates_in_chunks(voxel2_faces):
                # A raw budget over 3 million is decimated a chunk at a time, so
                # the decimator holds one chunk and not the sheet (geometry.decimate).
                from .decimate import decimate_in_chunks

                voxel = float(m.meta.get("voxel2_um", 0.0) or 0.0)
                target = max(4, int(round(len(m.faces) * fraction)))
                # Handed over as the only reference, so the sheet can go before
                # the chunks are joined (the build's peak).
                sheet = [m]
                del m
                return decimate_in_chunks(sheet.pop(), target, span_um=BLOCK * voxel)
        return m.decimate(fraction)

    stages = (smooth, decimate) if post_order == "smooth_first" else (decimate, smooth)
    # Each stage is handed the mesh as the only reference to it (decimate lets it go).
    held = [combined]
    del combined
    for i, stage in enumerate(stages):
        if i:
            # What the first stage freed goes back before the second, so the
            # decimator's child does not run beside it (c1178's default build:
            # the build process 460 -> 230 MB while the child works).
            trim_memory(collect=False)
        held.append(stage(held.pop()))
    combined = held.pop()

    combined.meta["neuron_id"] = getattr(neuron, "id", None)
    combined.meta["n_segments"] = n_segments
    return combined
