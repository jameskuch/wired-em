"""Joining the separate bodies of a Closed curve volume.

A cell's Closed curve surface can come apart into several bodies, and in a
wireframe each is a shell of its own.  Measured on 2026-10-16 (``sbfsem-measure``,
the visual-body count; what separates each pair of bodies read off the
annotations either side of the gap):

* at 0.25 um, the raster the app seeds for a cell with traced outlines,
  Inferior c1178 is 44 bodies and RPC1 c2628 70, and nearly every gap is a
  process too thin for the pixel: the 3x3x3 smoothing before the contour
  removes anything under about a pixel and a half in radius, so a 0.2 um
  dendrite drawn at 0.25 um breaks into beads (c1178: 42 of 43 gaps, between
  linked annotations, and 48 discs left with no surface at all; c2628: 61 of
  69, and 8 specks with no annotation of their own; Inferior c16137, 98 bodies
  at 0.25 um: 97 of 97);
* at 0.1 um c1178 is 2 bodies (one gap, 0.43 um across, between two parts
  nobody linked in Viking) and c16137 one; RPC1 c2628 is 94: 62 gaps are
  thin traced processes the smoothing still erodes, 28 specks, 2 linked
  outlines on adjacent sections that miss each other, 1 unlinked.

None of these is a skipped section (``bridge_um``) or a link over its reach
(``link_bridge_um``), and raising those changes nothing here (c2628 at 0.25
um: the same surface with a 2 um bridge): 92 of c2628's 93 gaps at 0.1 um and
20 of c1178's 43 at 0.25 um are under a micron, 34 under 2.  The voxel pathways close such gaps
with a tie wire (:func:`~sbfsem.geometry.cylinder._tie_segments`, "Tie gaps").
Here the tie is drawn into the raster itself, along the same shortest
crossing between two bodies' surfaces, and the volume is contoured again:
the surface stays one marching-cubes sheet.  A tie must survive the same
smoothing, so it is a pixel and a half in radius at least, and at most the
voxel pathways' 0.25 um tie where the pixel allows; it is extended past both
surfaces by two radii so it enters each body, and drawn like a disc link's
run, each section getting the part within one section of its plane.  A gap
between two runs of sections (a stretch left open in Z) is the bridges'
business and is not tied.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import ConvexHull, cKDTree

from .closed_curve import CurveVolume, label_code
from .cylinder import TIE_RADIUS_UM
from .mesh import Mesh
from .rasterize import rasterize_rings
from .volume import blocks_touched, curve_volume_to_mesh

log = logging.getLogger(__name__)

__all__ = ["MIN_TIE_PIXELS", "tie_bodies", "tie_radius_um"]

#: A tie's least radius in pixels: the 3x3x3 box smoothing before the contour
#: keeps a voxel whose box is at least half full, and a run two sections deep
#: whose circle covers the 3 x 3 window round its center (1.5 pixels reaches
#: the window's corners) fills 18 of its 27.
MIN_TIE_PIXELS = 1.5

#: Points on a tie's circles.
TIE_RING_POINTS = 32



def tie_radius_um(pixel_um: float) -> float:
    """The radius a tie is drawn at on a raster of this pixel."""
    return max(float(TIE_RADIUS_UM), MIN_TIE_PIXELS * float(pixel_um))


def tie_bodies(volume: CurveVolume, mesh: Mesh, max_um: float, *,
               cache: dict | None = None) -> tuple[Mesh, int]:
    """``mesh`` (the contour of ``volume``) with its separate bodies up to
    ``max_um`` apart joined, and how many ties that took.

    Every separate sheet is a candidate, however near (no touch tolerance:
    in one raster, sheets that were going to touch have merged); they are
    joined along the minimum spanning tree of their gaps, shortest first,
    as the voxel pathways' ties are (:func:`_gaps`), but only within one run
    of sections, and never the inner sheet of a cavity.  The ties are painted into ``volume``
    (which keeps them) and the volume contoured again; with nothing to tie
    the mesh comes back as it was.  ``cache`` is the block cache ``mesh``
    was contoured with (:func:`~sbfsem.geometry.volume.curve_volume_to_mesh`):
    with it only the blocks the ties reach are contoured again, and the mesh
    is the same.
    """
    if max_um <= 0 or mesh.is_empty:
        return mesh, 0
    pieces = _pieces(mesh)
    if len(pieces) < 2:
        return mesh, 0
    segments = _gaps(volume, pieces, float(max_um))
    # The pieces are a copy of the mesh: let them go before contouring again.
    del pieces
    radius = tie_radius_um(float(volume.spacing_um[0]))
    # Every tie's pixels first, then the tiles they need in one allocation,
    # then the drawing.
    drawn = [_tie_rasters(volume, a, b, radius) for a, b in segments]
    ties = sum(1 for d in drawn if d)
    if not ties:
        return mesh, 0
    strokes = [stroke for d in drawn for stroke in d]
    volume._allocate([(k, r0, r0 + m.shape[0] - 1, c0, c0 + m.shape[1] - 1)
                      for _start, k, r0, c0, m in strokes])
    code = label_code(radius, False) if volume.labels is not None else 0
    dirty: set = set()
    for start, k, r0, c0, m in strokes:
        volume._paint(k, r0, c0, m, code)
        dirty |= blocks_touched(start, (k, k), (r0, r0 + m.shape[0] - 1), (c0, c0 + m.shape[1] - 1))
    return curve_volume_to_mesh(volume, cache=cache, dirty=dirty), ties


def _gaps(volume: CurveVolume, pieces: list[Mesh], max_um: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """Where to tie: ``(a, b)`` from one piece's surface to another's, along
    the minimum spanning tree of the gaps up to ``max_um`` (Kruskal, shortest
    first), as :func:`~sbfsem.geometry.cylinder._tie_segments` finds the voxel
    pathways' -- with three differences the raster asks for.

    * **Within one run of sections.**  Each run is contoured on its own, so a
      tie cannot cross the stretch between two; a pair across it, picked
      first because it is shortest, used to count as joined, and the tie
      inside the run that was then due was never drawn.
    * **Never the inner sheet of a cavity.**  A body hollow inside has a second
      sheet round the hole, wound the other way (negative signed volume): it
      is part of the body, and a tie from it to the outer sheet ran through
      the wall and stuck out of the cell by its two radii.
    * **Every vertex, each query bounded by the reach**, and pairs whose boxes
      are farther apart than that never queried: a raster's pieces are one
      large sheet and many small ones.  Two disc stacks of 205,000 vertices
      each, 1.5 um apart, took 5.1 s unbounded and 0.34 s bounded, the same
      tie (2026-10-16, a busy machine); the review timed a 2.2 million face
      cell at 24 s.
    """
    dz = float(volume.spacing_um[2])
    starts = np.array([int(volume.sections[a]) for a, _ in volume.runs()], dtype=float)
    run = [int(np.clip(np.searchsorted(starts, np.rint(np.median(p.vertices[:, 2]) / dz),
                                       side="right") - 1, 0, None)) for p in pieces]
    live = [k for k, p in enumerate(pieces) if _signed_volume(p) > 0]
    lo = {k: pieces[k].vertices.min(axis=0) for k in live}
    hi = {k: pieces[k].vertices.max(axis=0) for k in live}
    trees: dict[int, cKDTree] = {}
    gaps: dict[tuple[int, int], tuple[float, np.ndarray, np.ndarray]] = {}
    for x, i in enumerate(live):
        for j in live[x + 1:]:
            if run[i] != run[j] or np.maximum(lo[j] - hi[i], lo[i] - hi[j]).max() > max_um:
                continue
            small, big = (i, j) if len(pieces[i].vertices) <= len(pieces[j].vertices) else (j, i)
            tree = trees.get(big)
            if tree is None:
                tree = trees[big] = cKDTree(pieces[big].vertices)
            d, idx = tree.query(pieces[small].vertices, distance_upper_bound=max_um)
            k = int(np.argmin(d))
            if np.isfinite(d[k]):
                gaps[(i, j)] = (float(d[k]), pieces[small].vertices[k], pieces[big].vertices[idx[k]])
    root = list(range(len(pieces)))

    def find(a: int) -> int:
        while root[a] != a:
            root[a] = root[root[a]]
            a = root[a]
        return a

    segments = []
    for (i, j), (d, a, b) in sorted(gaps.items(), key=lambda kv: kv[1][0]):
        if find(i) == find(j):
            continue
        root[find(i)] = find(j)
        if d > 0:
            segments.append((np.asarray(a, dtype=float), np.asarray(b, dtype=float)))
    groups = len({find(k) for k in live})
    if groups > len({run[k] for k in live}):
        log.info("closed-curve bodies: %d group(s) left apart, farther than %.1f um or in "
                 "separate runs of sections", groups, max_um)
    return segments


def _signed_volume(mesh: Mesh) -> float:
    """The volume a closed sheet encloses, negative where it is wound inward
    (the inner sheet of a cavity: marching cubes winds every sheet with the
    solid on its inside)."""
    total = 0.0
    for start in range(0, len(mesh.faces), 250_000):
        v = mesh.vertices[mesh.faces[start:start + 250_000]]
        total += float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum())
    return total / 6.0


def _pieces(mesh: Mesh) -> list[Mesh]:
    """The mesh's connected pieces, as :func:`~sbfsem.geometry.unify.split_components`
    makes them (the same pieces in the same order, vertices and faces in
    theirs), with one sort of the faces instead of a pass over all of them
    for each piece: c2628's 405 pieces at 0.1 um took 1.6 s that way."""
    faces = np.asarray(mesh.faces, dtype=np.int64)
    n = mesh.n_vertices
    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    cols = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    n_comp, labels = connected_components(graph, directed=False)
    face_label = labels[faces[:, 0]]
    order = np.argsort(face_label, kind="stable")
    edges = np.searchsorted(face_label[order], np.arange(n_comp + 1))
    out = []
    for k in range(n_comp):
        mine = faces[order[edges[k]:edges[k + 1]]]
        if not len(mine):
            continue
        used = np.unique(mine)
        out.append(Mesh(mesh.vertices[used], np.searchsorted(used, mine)))
    return out


def _run_of(volume: CurveVolume, slice_index: int) -> tuple[int, int]:
    for start, stop in volume.runs():
        if start <= slice_index < stop:
            return start, stop
    raise IndexError(slice_index)


def _tie_rasters(volume: CurveVolume, a: np.ndarray, b: np.ndarray, radius: float) -> list:
    """The pixels of the tie from ``a`` to ``b`` (um), drawn into nothing yet:
    ``(run start, slice, row0, col0, mask)`` for each slice it crosses;
    nothing if its ends lie in two runs of sections."""
    sx, sy, dz = (float(v) for v in volume.spacing_um)
    sections = volume.sections
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ka = int(np.argmin(np.abs(sections * dz - a[2])))
    kb = int(np.argmin(np.abs(sections * dz - b[2])))
    run = _run_of(volume, ka)
    if _run_of(volume, kb) != run:
        log.debug("closed-curve tie across an open stretch of sections left out")
        return []
    u = b - a
    norm = float(np.linalg.norm(u))
    u = u / norm if norm > 1e-12 else np.array([0.0, 0.0, 1.0])
    a, b = a - u * 2.0 * radius, b + u * 2.0 * radius
    first, last = int(sections[run[0]]), int(sections[run[1] - 1])
    za, zb = a[2] / dz, b[2] / dz                       # in sections
    if za > zb:
        a, b, za, zb = b, a, zb, za
    lo = int(np.clip(np.floor(za + 0.5), first, last))
    hi = int(np.clip(np.floor(zb + 0.5), first, last))
    if hi == lo:
        # Within one section: draw it on the next one too, or the smoothing removes it.
        if lo < last:
            hi = lo + 1
        elif lo > first:
            lo = lo - 1
    angle = np.linspace(0.0, 2.0 * np.pi, TIE_RING_POINTS, endpoint=False)
    unit = np.column_stack([np.cos(angle), np.sin(angle)])
    index = {int(z): i for i, z in enumerate(sections)}
    rows, cols, _ = volume.shape
    origin = np.asarray(volume.origin_um[:2], dtype=float)
    drew = []
    for s in range(lo, hi + 1):
        if zb - za > 1e-9:
            t0 = float(np.clip((s - 1 - za) / (zb - za), 0.0, 1.0))
            t1 = float(np.clip((s + 1 - za) / (zb - za), 0.0, 1.0))
        else:
            t0, t1 = 0.0, 1.0
        ends = [a[:2] + t * (b[:2] - a[:2]) for t in (t0, t1)]
        ring = np.vstack([e + radius * unit for e in ends])
        ring = ring[ConvexHull(ring).vertices]
        px = (ring - origin) / [sx, sy]
        c0, r0 = np.clip(np.floor(px.min(axis=0)).astype(int) - 1, 0, None)
        c1, r1 = np.minimum(np.ceil(px.max(axis=0)).astype(int) + 1, [cols - 1, rows - 1])
        if c1 < c0 or r1 < r0:
            continue
        mask = rasterize_rings([px - [c0, r0]], (r1 - r0 + 1, c1 - c0 + 1))
        drew.append((run[0], index[s], int(r0), int(c0), mask))
    return drew
