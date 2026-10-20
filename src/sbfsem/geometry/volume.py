"""Isosurface extraction from binary volumes.

Replaces MATLAB's ``isosurface``/``isonormals``/``isocaps`` in
``util/renders/volumeRender.m`` and ``+sbfsem/+render/RenderView.m`` with
scikit-image's ``marching_cubes``, and ``smooth3`` with a uniform filter.

``lib/MarchingCubes.m`` and ``lib/SurfaceSmooth.m`` are vendored but unreferenced
in the MATLAB repository and are deliberately not ported.
"""

from __future__ import annotations

import itertools
import logging

import numpy as np
from scipy.ndimage import uniform_filter, zoom
from skimage.measure import marching_cubes

from ..progress import report
from .mesh import EMPTY_MESH, Mesh, concatenate

log = logging.getLogger(__name__)

__all__ = ["smooth_volume", "resize_volume", "volume_to_mesh", "curve_volume_to_mesh"]


def smooth_volume(volume: np.ndarray, size: int = 3) -> np.ndarray:
    """MATLAB's ``smooth3`` default: a 3x3x3 box filter.

    Computed in float32, straight from whatever dtype arrives.  A curve
    volume can reach 10^8 voxels (RC2's vessel c1 is ~145M at a 1 µm
    raster), and the float64 round trip this used to do -- astype copy, then
    the filter's own output -- peaked at several extra gigabytes, which on
    the deployed box's 2 GiB cgroup ceiling meant kernel reclaim-stall (94 %
    memory pressure, a 24 s build crawling for half an hour), not speed.
    """
    return uniform_filter(volume, size=size, mode="nearest", output=np.float32)


def resize_volume(volume: np.ndarray, factor: float) -> np.ndarray:
    if factor == 1:
        return volume
    if not 0 < factor <= 1:
        raise ValueError("resize factor must be in (0, 1]")
    return zoom(volume.astype(np.float32, copy=False), (factor, factor, 1.0),
                order=1, output=np.float32)


def volume_to_mesh(
    volume: np.ndarray,
    *,
    level: float = 0.5,
    spacing=(1.0, 1.0, 1.0),
    origin=(0.0, 0.0, 0.0),
    do_smooth: bool = True,
    resize: float = 1.0,
    cap: bool = True,
) -> Mesh:
    """Marching-cubes surface of a binary or smoothed volume.

    ``cap`` pads the volume with one empty voxel on every side so the surface is
    closed where the object touches the array boundary; MATLAB needed a separate
    ``isocaps`` call for this.
    """
    data = np.asarray(volume)
    if data.size == 0 or not data.any():
        return EMPTY_MESH
    # What one voxel of the grid contoured below is, in units of the input's:
    # a resized grid has fewer voxels over the same extent, so its voxels are
    # larger (the mesh used to come out ``resize`` times too small in x and y).
    voxel_rcs = np.ones(3)  # per array axis: (row = y, column = x, slice = z)
    if resize != 1.0:
        before = data.shape
        data = resize_volume(data, resize)
        for axis in (0, 1):
            if data.shape[axis] > 1:  # the grid's ends stay on the input's ends
                voxel_rcs[axis] = (before[axis] - 1) / (data.shape[axis] - 1)
    # float32 throughout: half the footprint of the old float64 promotion,
    # and each rebind drops the previous copy immediately.  Values are 0/1
    # occupancy (smoothed), so no precision is at stake.
    data = smooth_volume(data) if do_smooth else data.astype(np.float32, copy=False)
    pad = 1 if cap else 0
    if pad:
        data = np.pad(data, pad, mode="constant", constant_values=0.0)
    try:
        verts, faces, _normals, _values = marching_cubes(data, level=level)
    except (ValueError, RuntimeError):
        return EMPTY_MESH
    verts = verts - pad
    # marching_cubes returns (row, col, slice); the toolbox works in (x, y, z).
    xyz = np.column_stack([verts[:, 1], verts[:, 0], verts[:, 2]])
    voxel_xyz = np.array([voxel_rcs[1], voxel_rcs[0], voxel_rcs[2]])
    xyz = xyz * (np.asarray(spacing, dtype=float) * voxel_xyz) + np.asarray(origin, dtype=float)
    return Mesh(xyz, faces).fix_winding()


def curve_volume_to_mesh(curve_volume, *, cache: dict | None = None,
                         dirty: set | None = None, **kwargs) -> Mesh:
    """Marching cubes over a :class:`~sbfsem.geometry.closed_curve.CurveVolume`.

    The result is in microns, with the 100x rasterization factor that MATLAB
    never divided out (F096) absent by construction.

    Each run of consecutive sections is contoured on its own and placed at
    its own section numbers, so a stretch left open (skipped sections too
    far apart to bridge) is open in the mesh too, not closed up by moving
    everything above it down.

    A run is smoothed and contoured in blocks of ``CONTOUR_BLOCK`` cubes, and
    only where some tile holds an outline (:func:`_contour_run`): the mesh is
    the one :func:`volume_to_mesh` makes of the whole run, vertex for vertex
    and face for face, without the run's box ever being one array.
    ``kwargs`` (:func:`volume_to_mesh`'s ``level``, ``do_smooth``,
    ``resize``, ``cap``) contour each run whole instead.

    ``cache`` (a dict) keeps every block's contour as it is made, keyed by its
    run's first slice and its block index; a later call with the same dict
    reuses each one not named in ``dirty`` (the same keys) and contours the
    rest, so a volume drawn into after its contour (the ties of
    :func:`~sbfsem.geometry.curve_ties.tie_bodies`) costs only the blocks the
    drawing reached.  The mesh is the one a fresh call makes, vertex for vertex.
    """
    dz = float(curve_volume.spacing_um[2])
    dense = curve_volume.dense() if kwargs else None
    runs = curve_volume.runs()
    blocks = [] if kwargs else [_blocks_to_contour(curve_volume, start, stop, CONTOUR_BLOCK)
                                for start, stop in runs]
    progress = [0, sum(len(b) for b in blocks)]
    parts = []
    for i, (start, stop) in enumerate(runs):
        origin = np.array(curve_volume.origin_um, dtype=float)
        origin[2] = float(curve_volume.sections[start]) * dz
        if dense is not None:
            parts.append(volume_to_mesh(
                dense[:, :, start:stop],
                spacing=curve_volume.spacing_um,
                origin=origin,
                **kwargs,
            ))
        else:
            parts.append(_contour_run(curve_volume, start, stop, blocks[i],
                                      spacing=curve_volume.spacing_um, origin=origin,
                                      block=CONTOUR_BLOCK, progress=progress,
                                      cache=cache, dirty=dirty))
    if not kwargs:
        report("contouring outlines", progress[1], progress[1])
    parts = [part for part in parts if not part.is_empty]
    if not parts:
        return EMPTY_MESH
    return parts[0] if len(parts) == 1 else concatenate(parts)


#: Cubes along each axis of one block of :func:`_contour_run`.
CONTOUR_BLOCK = 64

#: scikit-image's marching cubes (Lewiner) weights each corner of an edge by
#: ``1 / (eps + |value - level|)``, in float64 with this ``eps``, and stores
#: the vertex as float32 at its index in the array it was given.
_MC_EPS = float(np.spacing(1.0))

#: Corners ``(axis0, axis1, axis2)`` of a cube in Lewiner's order ``v0..v7``
#: (his x is the last axis), from which a cube's centre vertex is placed.
_CUBE_CORNERS = np.array([(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0),
                          (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)])


def _contour_run(curve_volume, start: int, stop: int, blocks: np.ndarray, *, spacing,
                 origin, level: float = 0.5, block: int = CONTOUR_BLOCK,
                 progress: list[int] | None = None, cache: dict | None = None,
                 dirty: set | None = None) -> Mesh:
    """``volume_to_mesh`` of slices ``start:stop``, computed block by block.

    The whole run would be: smooth (3x3x3 box, edges clamped), pad one empty
    voxel all round, marching cubes, shift back by the pad.  Here each block
    of ``block`` cubes reads the occupancy it needs plus one voxel of halo
    (clamped at the run's edges as the box filter clamps them), smooths
    that, and zeroes the samples that are the pad.  The filter's float32
    values come out the same as the whole run's: its running sums cancel
    exactly on 0/1 input (checked bit for bit on RPC1 c2628's 754-section
    run).  ``blocks`` are the ones an outline reaches
    (:func:`_blocks_to_contour`); one with none of the level inside it is
    skipped too.  ``progress`` is ``[blocks done, blocks in all runs]``.

    Marching cubes rounds each vertex to float32 at its index in the array
    it is given, so a block's vertices are placed again at their index in
    the whole run with the same arithmetic (:func:`_place`).  Vertices are
    then welded by the grid edge they lie on, faces put in the order a
    whole-run pass emits them (cube by cube), and vertices numbered in the
    order that pass creates them: the arrays are the whole run's.
    """
    rows, cols, _ = curve_volume.shape
    n = np.array([rows, cols, stop - start], dtype=np.int64)
    progress = progress if progress is not None else [0, len(blocks)]
    pieces = []
    for b in blocks:
        report("contouring outlines", progress[0], progress[1])
        progress[0] += 1
        key = (start, int(b[0]), int(b[1]), int(b[2]))
        if cache is not None and key in cache and not (dirty and key in dirty):
            piece = cache[key]
        else:
            piece = _contour_block(curve_volume, start, n, b * block, block, level)
            if cache is not None:
                cache[key] = piece
        if piece is not None:
            pieces.append(piece)
    if not pieces:
        return EMPTY_MESH

    coords = np.vstack([p[0] for p in pieces])
    keys = np.concatenate([p[1] for p in pieces])
    offsets = np.cumsum([0] + [len(p[0]) for p in pieces[:-1]])
    faces = np.vstack([p[2] + off for p, off in zip(pieces, offsets, strict=True)])
    cubes = np.concatenate([p[3] for p in pieces])
    del pieces
    # A whole-run pass emits faces cube by cube, axis 0 slowest; a block
    # emits its own cubes' faces in that same order.
    faces = faces[np.argsort(cubes, kind="stable")]
    del cubes
    # Weld: a vertex on a face two blocks share was placed by both.
    welded, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    faces = np.asarray(inverse).ravel()[faces]
    # Number vertices as the whole-run pass creates them: at first use, in
    # the face stream before scikit-image reverses each face.
    used, seen = np.unique(faces[:, ::-1].ravel(), return_index=True)
    created = used[np.argsort(seen)]
    number = np.empty(len(welded), dtype=np.int64)
    number[created] = np.arange(len(created))
    faces = number[faces]
    verts = coords[first[created]]
    del coords, keys, inverse, number
    # What volume_to_mesh does with the whole run's vertices, step for step.
    verts = verts - 1
    xyz = np.column_stack([verts[:, 1], verts[:, 0], verts[:, 2]])
    xyz = xyz * np.asarray(spacing, dtype=float) + np.asarray(origin, dtype=float)
    return Mesh(xyz, faces).fix_winding()


def blocks_touched(start: int, slices, rows, cols, block: int = CONTOUR_BLOCK) -> set:
    """The keys of :func:`curve_volume_to_mesh`'s ``cache`` whose contour a
    change to the voxels ``rows`` x ``cols`` x ``slices`` (inclusive
    ``(first, last)`` pairs; slices counted in the volume, in the run that
    starts at slice ``start``) can alter: a voxel ``v`` is smoothed into the
    padded samples ``v`` to ``v + 2``, which the blocks ``(v - 1) // block``
    to ``(v + 2) // block`` hold."""
    ranges = [range(max((lo - 1) // block, 0), (hi + 2) // block + 1)
              for lo, hi in ((slices[0] - start, slices[1] - start), rows, cols)]
    return {(start, b0, b1, b2) for b2 in ranges[0] for b0 in ranges[1] for b1 in ranges[2]}


def _blocks_to_contour(curve_volume, start: int, stop: int, block: int) -> np.ndarray:
    """``(m, 3)`` block indices, sorted, that occupied voxels of the run reach.

    An occupied voxel ``u`` is padded sample ``u + 1``; smoothing spreads it
    to samples ``u`` to ``u + 2``, which belong to cubes ``u - 1`` to
    ``u + 2``."""
    t = curve_volume.tile
    n = np.array([*curve_volume.shape[:2], stop - start], dtype=np.int64)
    n_tr, n_tc = curve_volume.tile_grid
    k, cell = np.divmod(curve_volume.keys, n_tr * n_tc)
    tr, tc = np.divmod(cell, n_tc)
    tiles = curve_volume.tiles
    pick = np.flatnonzero((k >= start) & (k < stop) & tiles.any(axis=(1, 2)))
    if not len(pick):
        return np.zeros((0, 3), dtype=np.int64)
    rows_any = tiles[pick].any(axis=2)
    cols_any = tiles[pick].any(axis=1)
    lo = np.column_stack([tr[pick] * t + rows_any.argmax(axis=1),
                          tc[pick] * t + cols_any.argmax(axis=1), k[pick] - start])
    hi = np.column_stack([tr[pick] * t + t - 1 - rows_any[:, ::-1].argmax(axis=1),
                          tc[pick] * t + t - 1 - cols_any[:, ::-1].argmax(axis=1),
                          k[pick] - start])
    last = n // block                      # cubes 0..n, so blocks 0..n // block
    b_lo = np.maximum((lo - 1) // block, 0)
    b_hi = np.minimum((hi + 2) // block, last)
    reach = (b_hi - b_lo).max(axis=0) + 1
    found = []
    for d0 in range(int(reach[0])):
        for d1 in range(int(reach[1])):
            for d2 in range(int(reach[2])):
                b = b_lo + (d0, d1, d2)
                found.append(b[(b <= b_hi).all(axis=1)])
    return np.unique(np.vstack(found), axis=0)


def _contour_block(curve_volume, start: int, n: np.ndarray, s: np.ndarray, block: int,
                   level: float):
    """One block's vertices (placed in the run, :func:`_place`), their edge
    keys, its faces, and each face's cube; ``None`` without a surface.

    The block's samples are padded indices ``s`` to ``e``: run voxels
    ``s - 1`` to ``e - 1``, read with a voxel of halo either side."""
    e = np.minimum(s + block, n + 1)
    index = [np.clip(np.arange(s[d] - 2, e[d] + 1), 0, n[d] - 1) for d in range(3)]
    occupied = curve_volume.gather(index[0], index[1], start + index[2])
    if not occupied.any():
        return None
    field = smooth_volume(occupied)[1:-1, 1:-1, 1:-1]
    del occupied
    for d in range(3):
        # The pad: sample 0 and sample n + 1 are empty.
        if s[d] == 0:
            field[(slice(None),) * d + (0,)] = 0.0
        if e[d] == n[d] + 1:
            field[(slice(None),) * d + (-1,)] = 0.0
    if not float(field.min()) < level < float(field.max()):
        return None
    try:
        verts, faces, _normals, _values = marching_cubes(field, level=level)
    except (ValueError, RuntimeError):
        return None
    coords, keys, low, high = _place(verts, field, s, n + 2, level)
    # The cube that emitted a face is the one whose edges hold all three of
    # its vertices -- unless the face lies flat on the side two cubes share,
    # which Lewiner's tables sometimes draw.
    cube = np.max(low[faces], axis=1)
    top = np.min(high[faces], axis=1)
    for i in np.flatnonzero((cube != top).any(axis=1)):
        cube[i] = _emitting_cube(field, keys[faces[i]], cube[i], top[i], s, n + 2, level)
    cube = cube + s
    cubes = n + 1
    order = (cube[:, 0] * cubes[1] + cube[:, 1]) * cubes[2] + cube[:, 2]
    return coords, keys, faces.astype(np.int64), order


def _emitting_cube(field: np.ndarray, face_keys: np.ndarray, low: np.ndarray,
                   high: np.ndarray, s: np.ndarray, samples: np.ndarray,
                   level: float) -> np.ndarray:
    """Which of the cubes from ``low`` to ``high`` (block indices) drew the
    face whose vertices are ``face_keys``, in its winding: the one whose own
    marching cubes has that triangle.  A cube's triangles depend on its
    eight corners alone.  Both cubes on a shared side can draw a triangle on
    the same three edges; they wind it opposite ways."""
    want = [tuple(np.roll(face_keys, r)) for r in range(3)]
    for corner in itertools.product(*(range(int(a), int(b) + 1)
                                      for a, b in zip(low, high, strict=True))):
        c = np.array(corner)
        try:
            verts, faces, _n, _v = marching_cubes(
                field[c[0]:c[0] + 2, c[1]:c[1] + 2, c[2]:c[2] + 2], level=level)
        except (ValueError, RuntimeError):
            continue
        p, code = _on_grid(verts)
        keys = _vertex_keys(p + c + s, code, samples)
        if any(tuple(keys[f]) in want for f in faces):
            return c
    log.warning("closed-curve contour: no cube drew a face on a shared side; "
                "faces may not be in a whole-run pass's order")
    return low


def _on_grid(verts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each marching-cubes vertex's grid corner, and what it lies on there:
    0 to 2 the edge along that axis, 3 the inside of the cube."""
    lower = np.floor(verts)
    fractional = verts != lower
    code = np.where(fractional.sum(axis=1) == 1, np.argmax(fractional, axis=1), 3)
    return lower.astype(np.int64), code


def _vertex_keys(corner: np.ndarray, code: np.ndarray, samples: np.ndarray) -> np.ndarray:
    """One integer per edge or cube of the run's padded grid."""
    return ((corner[:, 0] * samples[1] + corner[:, 1]) * samples[2] + corner[:, 2]) * 4 + code


_DRIFT_TOLD: list[bool] = []


def _drifted() -> None:
    # A scikit-image whose marching cubes places vertices with other
    # arithmetic: the block's own placement, shifted, is within a float32
    # rounding of the run's.  Said once a process, not once a block.
    if not _DRIFT_TOLD:
        _DRIFT_TOLD.append(True)
        log.warning("closed-curve contour: marching cubes no longer places vertices as "
                    "_place does; vertices may differ from a whole-run pass by a rounding")


def _strength(values: np.ndarray, level: float) -> np.ndarray:
    return 1.0 / (_MC_EPS + np.abs(values.astype(np.float64) - level))


def _place(verts: np.ndarray, field: np.ndarray, s: np.ndarray, samples: np.ndarray,
           level: float):
    """Vertices of a block's marching cubes, placed at their index in the run.

    Lewiner puts a vertex on the edge from corner ``p`` along axis ``a`` at
    ``p[a] + t1 / (t0 + t1)`` with ``t = 1 / (eps + |value - level|)`` in
    float64, then stores it as float32; a vertex inside a cube (a cube whose
    ambiguity his tables resolve with one) at the weighted mean of its eight
    corners, summed in his order.  Doing the same arithmetic at the run's
    index gives the run's float32 coordinates; doing it at the block's must
    give back what marching cubes returned, which is checked (a vertex that
    does not is placed by shifting the block's own, with a warning).

    Returns the coordinates (float32, padded run indices), each vertex's key
    (its edge or cube), and the lowest and highest cube each vertex could
    belong to."""
    lower = np.floor(verts)
    fractional = verts != lower
    p = lower.astype(np.int64)
    kind = fractional.sum(axis=1)
    coords = np.empty(verts.shape, dtype=np.float32)
    code = np.full(len(verts), 3, dtype=np.int64)
    low, high = p.copy(), p.copy()

    edge = np.flatnonzero(kind == 1)
    if len(edge):
        at = np.arange(len(edge))
        axis = np.argmax(fractional[edge], axis=1)
        pe = p[edge]
        q = pe.copy()
        q[at, axis] += 1
        t0 = _strength(field[pe[:, 0], pe[:, 1], pe[:, 2]], level)
        t1 = _strength(field[q[:, 0], q[:, 1], q[:, 2]], level)
        w = t1 / (t0 + t1)
        placed = (pe + s).astype(np.float32)
        placed[at, axis] = ((pe[at, axis] + s[axis]) + w).astype(np.float32)
        coords[edge] = placed
        drift = (pe[at, axis] + w).astype(np.float32) != verts[edge, axis]
        if drift.any():
            off = edge[drift]
            coords[off] = (verts[off].astype(np.float64) + s).astype(np.float32)
            _drifted()
        code[edge] = axis
        # The four cubes around an edge: one below it on each other axis.
        below = pe - 1
        below[at, axis] = pe[at, axis]
        low[edge] = below

    centre = np.flatnonzero(kind == 3)
    if len(centre):
        pc = p[centre]
        sv = [_strength(field[pc[:, 0] + dz, pc[:, 1] + dy, pc[:, 2] + dx], level)
              for dz, dy, dx in _CUBE_CORNERS]
        fx = ((sv[1] + sv[2]) + sv[5]) + sv[6]
        fy = ((sv[2] + sv[3]) + sv[6]) + sv[7]
        fz = ((sv[4] + sv[5]) + sv[6]) + sv[7]
        ff = sv[0]
        for v in sv[1:]:
            ff = ff + v
        frac = np.column_stack([fz / ff, fy / ff, fx / ff])
        coords[centre] = ((pc + s) + frac).astype(np.float32)
        missed = ((pc + frac).astype(np.float32) != verts[centre]).any(axis=1)
        if missed.any():
            off = centre[missed]
            coords[off] = (verts[off].astype(np.float64) + s).astype(np.float32)
            _drifted()

    if len(edge) + len(centre) != len(verts):
        # Corner values of a smoothed 0/1 field are k/27, never within 0.018
        # of the level, so every vertex is strictly inside its edge or cube.
        raise RuntimeError("marching cubes vertex on neither an edge nor in a cube")
    return coords, _vertex_keys(p + s, code, samples), low, high
