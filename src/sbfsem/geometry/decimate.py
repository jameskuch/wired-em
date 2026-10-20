"""Quadric decimation of a whole-cell sheet a chunk at a time.

:meth:`Mesh.decimate <sbfsem.geometry.mesh.Mesh.decimate>` hands the whole
surface to ``fast_simplification``, which copies it into structures of its own:
160 bytes a triangle (72 of them texture coordinates nothing here uses) and 120
a vertex, filled by ``push_back`` with no reserve, so the last growth of the
triangle array holds two copies at once.  Decimating the raw sheet of a Voxel
remesh was then the build's peak: Inferior c1178 at 0.07 um under a Face cap
of 1.2 million (2.5 million raw triangles) rose from 754 MB (smoothing) to
1,255 MB on the server, and at its finest (0.042 um, 6.9 million down to 3.4)
from 1,805 to 3,134 MB (5,615 MB on a Mac).  In chunks the same builds peak at
816 and 1,568 MB, the second the smoothing's, 5 and 9 % slower (2026-10-20).

:func:`decimate_in_chunks` cuts the sheet into chunks of at most
``CHUNK_FACES`` triangles along block planes, decimates each with its cut
edges locked (``preserve_border``: no edge touching an open border collapses,
so the vertices along a cut keep their exact coordinates in both chunks) and
welds the chunks back along the cut by those coordinates.  The decimator then
holds one chunk, not the sheet.

What it must not change is how good the surface is.  A chunk's share of the
Face cap is weighted by the sum over its edges of the initial collapse error to
the power ``WEIGHT_ALPHA`` (the decimator's own planes, unweighted unit
normals, at the better of the two ends and the midpoint), so a chunk of flat
soma gives up more than a chunk of thin arbor.  Measured against the one pass
(the distance of 300,000 to 400,000 raw vertices to the result, exact to the
nearest triangle, and the crease density): on Inferior c1178's sheet (2.1
million raw triangles at 0.076 um) kept to 1 million, 700,000, 200,000 and
100,000, and on c16137's (1.6 million at 0.05 um) kept to 68 % and 33 %,
chunks of 1 million so weighted kept the mean, 99th and 99.9th percentile error
within 3 % of the one pass's and the creases within 2 %; shared by raw
triangles alone they were up to 16 % worse in the mean and 24 % at the 99th
percentile (the single worst point of c1178 at 33 %, one of 300,000, was 110
nm either way against 79).  Deviation from the annotations and visual bodies
did not move.
On a synthetic sheet whose fat body is a flat-faced lens the weights put too
few triangles on the lens (12 to 17 % more creases), while every split stayed
under 0.2 nm from the sheet.

The one pass is no exact standard to hold the chunks to: in microns nearly
every edge of a sheet at these voxels is under the first threshold of
``fast_simplification``'s schedule (2.2e-6, against edge errors of 1e-8 to
1e-4 um^2), so it collapses in the order of the triangle array, not of the
error.  Shuffling the triangles shares the same cap out evenly and makes 11 to
21 % more creases (c1178 at 47 and 10 % kept).  A second pass over the whole
result was measured and refused as the rule: restarting the quadrics forgets
the first pass's error, and two passes (to 1.25 times the cap, then to the cap)
doubled the 99.9th percentile at 200,000 and 100,000 kept (20 to 46 nm, 61 to
124 nm).

The triangles along a cut cannot collapse, so they are kept as they were; a
chunk's target is its own such *band* plus its weighted share of the rest of
the cap.  Cuts are placed where the fewest triangles straddle a block plane,
along whichever axis crosses least: c1178's arbor is flat, so planes across z
cut through it (7,708 straddling vertices for five chunks) where planes across
x or y cross a few hundred.  Only when the bands come to more than
``BAND_FINAL_SHARE`` of the cap -- a small cap of a large sheet -- are they
given on top of it and taken off by one pass over the (then small) result.
"""

from __future__ import annotations

import logging

import numpy as np

from ..progress import report
from .mesh import Mesh

__all__ = ["BAND_FINAL_SHARE", "CHUNK_FACES", "WEIGHT_ALPHA", "decimate_in_chunks"]

log = logging.getLogger(__name__)

#: Raw triangles a chunk may hold.  The decimator's copy of one is some 330 MB
#: at its peak (160 bytes a triangle, twice while its array last grows, and
#: 120 a vertex); fewer, larger chunks cut the surface in fewer places.
CHUNK_FACES = 1_000_000

#: The exponent on an edge's initial quadric error in a chunk's weight.  0 would
#: share the cap by raw triangles alone; 0.2 kept c1178's planes through the
#: arbor within 15 % of the one pass's share at 1 million of 2.1 (0.33 and 0.5 put
#: up to 42 and 64 % too few there).
WEIGHT_ALPHA = 0.2

#: A chunk's weight is summed over the half-edges of every this-many-th triangle.
WEIGHT_SAMPLE = 4

#: When the triangles locked along the cuts are more than this share of the
#: cap, they are given on top of it and one pass over the result takes them off.
BAND_FINAL_SHARE = 0.01

# The ten coefficients of a symmetric 4x4 quadric, as (row, column).
_QI = (0, 0, 0, 0, 1, 1, 1, 2, 2, 3)
_QJ = (0, 1, 2, 3, 1, 2, 3, 2, 3, 3)


def _edge_weight(vertices: np.ndarray, faces: np.ndarray) -> float:
    """The chunk's weight: the sum over a sample of half-edges of the initial
    collapse error to the power ``WEIGHT_ALPHA``."""
    if not len(faces):
        return 0.0
    v = vertices - vertices.mean(axis=0)          # the d terms near zero
    p0 = v[faces[:, 0]]
    n = np.cross(v[faces[:, 1]] - p0, v[faces[:, 2]] - p0)
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-300)[:, None]
    plane = (n[:, 0], n[:, 1], n[:, 2], -np.einsum("ij,ij->i", n, p0))
    del p0, n
    q = np.empty((10, len(v)))
    for c, (i, j) in enumerate(zip(_QI, _QJ, strict=True)):
        k = plane[i] * plane[j]
        q[c] = sum(np.bincount(faces[:, corner], weights=k, minlength=len(v)) for corner in range(3))
    del plane
    sample = faces[::WEIGHT_SAMPLE]
    total = 0.0
    for a_, b_ in ((0, 1), (1, 2), (2, 0)):
        a, b = sample[:, a_], sample[:, b_]
        qe = q[:, a] + q[:, b]
        best = np.full(len(a), np.inf)
        for p in (v[a], v[b], 0.5 * (v[a] + v[b])):
            x, y, z = p[:, 0], p[:, 1], p[:, 2]
            e = (qe[0] * x * x + 2.0 * qe[1] * x * y + 2.0 * qe[2] * x * z + 2.0 * qe[3] * x
                 + qe[4] * y * y + 2.0 * qe[5] * y * z + 2.0 * qe[6] * y
                 + qe[7] * z * z + 2.0 * qe[8] * z + qe[9])
            np.minimum(best, e, out=best)
        total += float(np.sum(np.maximum(best, 0.0) ** WEIGHT_ALPHA))
    return total * WEIGHT_SAMPLE


def _plan(vertices: np.ndarray, faces: np.ndarray, span: float,
          chunk_faces: int) -> tuple[np.ndarray, int, int, int, np.ndarray]:
    """Each triangle's chunk, the number of chunks, the triangles straddling the
    cuts, the axis and the cut planes' coordinates along it.

    Slabs ``span`` thick are taken along each axis; walking along it, a chunk
    is cut once the next slab would take it over ``chunk_faces``, at the plane
    in its last quarter that the fewest triangles straddle.  The axis whose
    cuts are straddled least wins.  A triangle goes to the chunk of its
    centroid, so a cut follows the triangles' own edges."""
    best: tuple[int, int, float, list[int]] | None = None
    for axis in range(3):
        x0, x1, x2 = (vertices[faces[:, corner], axis] for corner in range(3))
        base = float(min(x0.min(), x1.min(), x2.min()))
        slab = np.floor(((x0 + x1 + x2) / 3.0 - base) / span).astype(np.int64)
        top = np.floor((np.maximum(np.maximum(x0, x1), x2) - base) / span).astype(np.int64)
        bottom = np.floor((np.minimum(np.minimum(x0, x1), x2) - base) / span).astype(np.int64)
        del x0, x1, x2
        n_slabs = int(max(slab.max(), top.max())) + 1
        per = np.bincount(slab, minlength=n_slabs)
        # Straddling triangles of the plane at the bottom of slab j.
        cross = np.bincount(top[top > bottom], minlength=n_slabs + 1)
        del slab, top, bottom
        edges, start, held = [0], 0, 0
        for j in range(n_slabs):
            if held + per[j] > chunk_faces and held > 0:
                lo = max(start + 1, j - max(1, (j - start) // 4))
                candidates = np.arange(lo, j + 1)
                cut = int(candidates[np.argmin(cross[candidates])])
                edges.append(cut)
                start, held = cut, int(per[cut:j].sum())
            held += int(per[j])
        edges.append(n_slabs)
        crossing = int(sum(int(cross[e]) for e in edges[1:-1]))
        if best is None or crossing < best[0]:
            best = (crossing, axis, base, edges)
    assert best is not None
    crossing, axis, base, edges = best
    planes = base + np.asarray(edges[1:-1], dtype=float) * span
    return _assign(vertices, faces, axis, planes), len(edges) - 1, crossing, axis, planes


def _assign(vertices: np.ndarray, faces: np.ndarray, axis: int, planes: np.ndarray) -> np.ndarray:
    """Each triangle's chunk: how many cut ``planes`` (along ``axis``) its centroid's slab is above."""
    centroid = sum(vertices[faces[:, corner], axis] for corner in range(3)) / 3.0
    return np.searchsorted(planes, centroid, side="right").astype(np.int32)


def _fit(weights: np.ndarray, room: np.ndarray, total: float) -> np.ndarray:
    """``total`` shared out in proportion to ``weights``, no share over its ``room``."""
    share = np.zeros(len(room))
    free = room > 0
    left = float(total)
    for _ in range(len(room) + 1):
        w = np.where(free, weights, 0.0)
        if left <= 0 or w.sum() <= 0:
            break
        part = left * w / w.sum()
        over = free & (part >= room)
        if not over.any():
            share[free] = part[free]
            break
        share[over] = room[over]
        left -= float(room[over].sum())
        free &= ~over
    return share


def _chunk(faces: np.ndarray, mask: np.ndarray, scratch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The chunk's vertex ids, in order, and its faces in them (``scratch``: an index
    per vertex, -1 throughout on the way in and out)."""
    sub = faces[mask]
    scratch[sub.ravel()] = 0
    used = np.flatnonzero(scratch == 0)
    scratch[used] = np.arange(len(used))
    local = scratch[sub]
    scratch[used] = -1
    return used, local


def decimate_in_chunks(mesh: Mesh, face_count: int, *, span_um: float,
                       chunk_faces: int | None = None) -> Mesh:
    """``mesh`` decimated to ``face_count`` triangles a chunk at a time.

    ``span_um`` is the thickness of the slabs the cuts are placed between (a
    block of the whole-cell grid).  A mesh of no more than ``chunk_faces``
    (``CHUNK_FACES``) triangles is decimated whole, as :meth:`Mesh.decimate`.
    ``mesh`` must be closed: its own open borders would be locked like the cuts.
    """
    if chunk_faces is None:
        chunk_faces = CHUNK_FACES
    n_faces = len(mesh.faces)
    if mesh.is_empty or face_count >= n_faces:
        return mesh
    if n_faces <= chunk_faces or span_um <= 0:
        return mesh.decimate(face_count / n_faces)
    import fast_simplification
    import trimesh

    vertices, faces = mesh.vertices, mesh.faces
    report("decimating: placing the cuts")
    chunk, n_chunks, crossing, _axis, _planes = _plan(vertices, faces, float(span_um), int(chunk_faces))
    # A vertex is on a cut when its triangles are in more than one chunk.
    owner = np.full(len(vertices), -1, dtype=np.int32)
    for corner in range(3):
        owner[faces[:, corner]] = chunk
    on_cut = np.zeros(len(vertices), dtype=bool)
    for corner in range(3):
        differ = owner[faces[:, corner]] != chunk
        on_cut[faces[differ, corner]] = True
    del owner

    scratch = np.full(len(vertices), -1, dtype=np.int64)
    raw = np.zeros(n_chunks)
    band = np.zeros(n_chunks)
    weight = np.zeros(n_chunks)
    for i in range(n_chunks):
        report("decimating: weighing the chunks", i, n_chunks)
        mask = chunk == i
        used, local = _chunk(faces, mask, scratch)
        raw[i] = len(local)
        band[i] = int(on_cut[used][local].any(axis=1).sum())
        weight[i] = _edge_weight(vertices[used], local)
        del used, local, mask
    bands = float(band.sum())
    final = bands > BAND_FINAL_SHARE * face_count
    # The bands are kept; the rest of the cap goes by weight.  When the bands
    # are a large share of the cap they come on top of it, for a last pass.
    rest = float(face_count) if final else max(float(face_count) - bands, 0.0)
    targets = band + _fit(weight, raw - band, rest)

    parts_v: list[np.ndarray] = []
    parts_f: list[np.ndarray] = []
    offset = 0
    for i in range(n_chunks):
        report("decimating", i, n_chunks)
        used, local = _chunk(faces, chunk == i, scratch)
        sub_v = vertices[used]
        target = int(round(targets[i]))
        if 4 <= target < len(local):
            try:
                sub_v, local = fast_simplification.simplify(
                    sub_v, local, target_count=target, preserve_border=True)
            except Exception as exc:  # noqa: BLE001 - same fallback as Mesh.decimate
                log.warning("chunked decimation failed (%s); decimating the whole sheet", exc)
                return mesh.decimate(face_count / n_faces)
        parts_v.append(np.asarray(sub_v, dtype=float))
        parts_f.append(np.asarray(local, dtype=np.int64) + offset)
        offset += len(sub_v)
        del used, local, sub_v
    del chunk, on_cut, scratch
    report("decimating", n_chunks, n_chunks)
    # The vertices along a cut came out of both chunks with the same coordinates;
    # trimesh's processing merges them, as it does the global pass's output.
    joined = trimesh.Trimesh(vertices=np.vstack(parts_v), faces=np.vstack(parts_f))
    del parts_v, parts_f
    out_v, out_f = np.asarray(joined.vertices), np.asarray(joined.faces)
    del joined
    if final and len(out_f) > face_count:
        report("decimating: the cuts")
        out_v, out_f = fast_simplification.simplify(out_v, out_f, target_count=int(face_count))
        processed = trimesh.Trimesh(vertices=out_v, faces=out_f)
        out_v, out_f = np.asarray(processed.vertices), np.asarray(processed.faces)
    log.info("decimated %s triangles to %s in %d chunks (%d straddling the cuts, %d kept along them%s)",
             f"{n_faces:,}", f"{len(out_f):,}", n_chunks, crossing, int(bands),
             ", then the cuts" if final else "")
    return Mesh(out_v, out_f, None, dict(mesh.meta))
