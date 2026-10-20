"""Averaging out the staircase of a closed-curve volume.

A closed-curve surface is built a section at a time, and two things show in it
as steps.  An annotator's rim wobbles from one section to the next (a disc's
center and radius, a traced outline's shape), and where a process crosses the
sections at a slant the surface climbs in treads one section high.  Inferior
c16137's soma comes out as a stack of plates, and a dendrite that runs along
the section plane as a ribbon with a row of teeth down its rim (c1178: one
tooth a section).  Two scales, so two steps, both off by default and both
scaled to how thick the annotation they sit on is:

**Along the chain** (:func:`average_disc_nodes`, :func:`average_outlines`).
Every annotation is replaced by a Gaussian-weighted local quadratic fit over
the annotations near it along its chain of Viking links -- the averaging of
:mod:`~sbfsem.geometry.annotation_wire`, which also says why the fit is a
quadratic and not a mean, why the window narrows to fit inside its chain (a
chain's ends stay as annotated, so a thin tip is not shortened and a one-
or two-section outline is never touched) and how chains are cut.  A disc's
window is its strength times its own radius: a 5 um soma disc averages over
microns of Z and sheds the plates, a 0.2 um dendrite disc over a section or
two.  A traced outline has no one radius, so its window is a distance, in
microns along the chain.

**Over the surface** (:func:`polish_curve_mesh`).  The teeth are creases
the width of a pixel, which no averaging of annotations reaches: they are
where the contour crosses from one section's footprint to the next.  Taubin
smoothing (:meth:`~sbfsem.geometry.mesh.Mesh.taubin`) takes them out and
moves the surface by a few hundredths of a micron -- it does not shrink, so
it holds the volume -- if each vertex gets as many passes as the length it
is to be averaged over asks for (:func:`smoothing_passes`): the same strength
times radius for a disc's surface, the same distance for a traced outline's,
and none where the process is only a pixel or two thick, which Taubin would
collapse.  A raster built with ``keep_labels`` says which annotation each
vertex belongs to.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .annotation_wire import (
    DEFAULT_BREAK_RATIO,
    FITS,
    _average,
    _best_shift,
    _chain_cover,
    _chain_positions,
    _shapes,
)
from .closed_curve import ClosedCurveAnnotation, decode_labels
from .mesh import Mesh
from .spline import close_spline

__all__ = [
    "POLISH_KAPPA",
    "POLISH_MAX_PASSES",
    "average_disc_nodes",
    "average_outlines",
    "owner_radius",
    "polish_curve_mesh",
    "smooth_surface",
    "smoothing_passes",
    "surface_passes",
]

#: The window's weighting by how alike two radii are (the annotation wire's
#: ``size_tolerance``), off: it weights neighbors by their likeness to the
#: annotation being averaged, which keeps what the averaging is there to take
#: out (a disc whose radius jumps is its own best match).  Chains still break
#: at ``DEFAULT_BREAK_RATIO``, so a soma never averages in the dendrite
#: that leaves it.
SIZE_TOLERANCE = 1.0

#: An averaged disc keeps its radius within this multiple of what was traced,
#: both ways: a window across a taper must not carry a fit past what it was
#: fitted to.
RADIUS_LIMITS = (0.25, 4.0)

#: Passes of the polish are ``kappa * (length / edge) ** 2``, rounded: Taubin's
#: passes, like a diffusion's, reach a distance as the square root of their
#: number.  0.45 is what takes a ribbon's row of teeth (c1178, 0.1 um pixel)
#: out at a strength of 1: crease length at 45 degrees and over fell from
#: 0.84 to 0.20 per um^2 with 10 to 20 passes, and the surface moved a few
#: hundredths of a micron.
POLISH_KAPPA = 0.45

#: Most passes any vertex takes.  Past ten the creases hardly fall further
#: (c16137 at 0.1 um, per um^2 of surface: 1.08 untouched, 0.32 after 5, 0.24
#: after 10, 0.20 after 20) and the time goes on adding up.
POLISH_MAX_PASSES = 20

#: Most vertex-passes one polish may spend (the sum of every vertex's passes):
#: about half a minute on the machine this was measured on (3.9 million
#: vertex-passes take 2 s on Inferior c1178 at 0.1 um), however large the cell.
#: A cell over it takes fewer passes everywhere, in proportion.
POLISH_BUDGET = 60_000_000

#: A process whose radius is under the first of these many mesh edges is left
#: alone and one over the second smoothed in full; between the two the passes
#: ramp up.  A tube two edges across is a ring of six or so vertices, which
#: Taubin pulls together (c1178 at 0.1 um, a 0.1 um tube: 0.050 um radius
#: after contouring, 0.012 after 20 passes); at three edges it holds.
THIN_EDGES = (1.6, 3.0)


def _chains(shapes, links, break_ratio):
    """The shapes' chains (lists of indices into ``shapes``), from Viking's links."""
    index = {s.location_id: k for k, s in enumerate(shapes)}
    pairs = sorted({
        (min(index[int(a)], index[int(b)]), max(index[int(a)], index[int(b)]))
        for a, b in links
        if int(a) in index and int(b) in index and int(a) != int(b)
    })
    chains, _chosen = _chain_cover(shapes, pairs, break_ratio)
    return chains


def average_disc_nodes(nodes: pd.DataFrame, links, gain: float, *,
                       break_ratio: float = DEFAULT_BREAK_RATIO,
                       fit: str = "quadratic") -> pd.DataFrame:
    """The disc annotations in ``nodes``, each averaged along its chain of links.

    ``gain`` is the strength: the Gaussian window's sigma is that multiple of
    the disc's own radius (:func:`~sbfsem.geometry.annotation_wire.annotation_wire`
    takes the same number), so the window of a 5 um soma disc is some 2.5 um
    along its chain at 0.5 and a 0.2 um dendrite's 0.1 um, one section.  A
    chain is a run of discs each linked to the next, one to a section, and
    the window never reaches past its ends: the first and last disc of every
    chain come back as traced.  Returns a copy with ``Xum``, ``Yum`` and
    ``Rum`` replaced; ``Zum`` and ``Z`` -- the sections -- are the discs'
    own.  ``nodes`` must hold discs alone.  A strength of 0, no links or a
    disc on no chain of three leave the table as it was.
    """
    if gain <= 0 or nodes is None or not len(nodes) or not links:
        return nodes
    if fit not in FITS:
        raise ValueError(f"fit must be one of {sorted(FITS)}, not {fit!r}")
    shapes = _shapes(nodes, {})
    if len(shapes) < 3:
        return nodes
    row = {int(i): k for k, i in enumerate(nodes["ID"])}
    x = nodes["Xum"].to_numpy(dtype=float).copy()
    y = nodes["Yum"].to_numpy(dtype=float).copy()
    r = nodes["Rum"].to_numpy(dtype=float).copy()
    traced_r = r.copy()
    for chain in _chains(shapes, links, break_ratio):
        if len(chain) < 3:
            continue
        members = [shapes[i] for i in chain]
        positions = _chain_positions(members)
        radii = np.array([m.radius for m in members])
        raw = np.array([[m.center[0], m.center[1], m.radius] for m in members])
        smoothed, _sigma = _average(raw, positions, radii, float(gain), SIZE_TOLERANCE, FITS[fit])
        for member, new in zip(members, smoothed, strict=True):
            k = row[member.location_id]
            x[k], y[k], r[k] = new
    low, high = RADIUS_LIMITS
    r = np.clip(r, low * traced_r, high * traced_r)
    out = nodes.copy()
    out["Xum"], out["Yum"], out["Rum"] = x, y, r
    return out


def average_outlines(annotations, links, sigma_um: float, *, spacing_um: float = 0.05,
                     spline_points: int = 20,
                     break_ratio: float = DEFAULT_BREAK_RATIO,
                     fit: str = "quadratic") -> list[ClosedCurveAnnotation]:
    """The traced outlines in ``annotations``, each averaged along its chain of links.

    A chain is a run of outlines each linked to the next -- Viking's links
    say which outline carries on as which in the next section.  Every
    outline on it is resampled to one number of points (a point every
    ``spacing_um`` round the longest, 16 to 1,024), turned to line up with
    the one before, and replaced by a Gaussian-weighted local quadratic fit
    over its neighbors: the window's sigma is ``sigma_um``, in microns along
    the chain (the Z step between sections, plus the sideways travel past
    the outline's own size).  The window never reaches past the chain's ends,
    so its first and last outline, and any with no chain of three, come back
    as the same objects.  Cut-outs are kept as traced (splined, where the
    outline was).  Annotations without a Location ID, or with an outline that
    is not a polygon, cannot be linked and are returned as they were.
    """
    annotations = list(annotations)
    if sigma_um <= 0 or not links or len(annotations) < 3:
        return annotations
    if fit not in FITS:
        raise ValueError(f"fit must be one of {sorted(FITS)}, not {fit!r}")
    outlines, rows = {}, []
    for a in annotations:
        if not a.location_id or a.location_id in outlines:
            continue
        ring = np.asarray(a.spline(spline_points), dtype=float)
        if len(ring) < 3 or not np.isfinite(ring).all():
            continue
        outlines[int(a.location_id)] = ring
        rows.append((int(a.location_id), int(a.z_section), float(a.z_um)))
    if len(rows) < 3:
        return annotations
    table = pd.DataFrame(rows, columns=["ID", "Z", "Zum"])
    table["Xum"] = [outlines[i][:, 0].mean() for i in table["ID"]]
    table["Yum"] = [outlines[i][:, 1].mean() for i in table["ID"]]
    table["Rum"] = 0.0
    table["TypeCode"] = 6
    shapes = _shapes(table, outlines)
    by_id = {int(a.location_id): a for a in annotations if a.location_id}
    replaced: dict[int, ClosedCurveAnnotation] = {}
    for chain in _chains(shapes, links, break_ratio):
        if len(chain) < 3:
            continue
        members = [shapes[i] for i in chain]
        n = int(np.clip(np.ceil(max(m.perimeter for m in members) / max(spacing_um, 1e-3)), 16, 1024))
        raw = [m.sample(n) for m in members]
        for k in range(1, len(raw)):
            raw[k] = np.roll(raw[k], -_best_shift(raw[k - 1], raw[k]), axis=0)
        positions = _chain_positions(members)
        window = np.full(len(members), float(sigma_um))
        smoothed, sigma = _average(np.stack(raw), positions, window, 1.0, SIZE_TOLERANCE, FITS[fit])
        for member, ring, used in zip(members, smoothed, sigma, strict=True):
            if used <= 1e-9:
                continue                                   # a chain's end: as traced
            original = by_id[member.location_id]
            cutouts = [np.asarray(c, dtype=float) if original.registered
                       else close_spline(np.asarray(c, dtype=float), spline_points)
                       for c in original.cutouts]
            replaced[member.location_id] = ClosedCurveAnnotation(
                outline=ring, cutouts=cutouts, z_section=original.z_section, z_um=original.z_um,
                parent_id=original.parent_id, location_id=original.location_id, registered=True,
                is_path=original.is_path, owner_um=original.owner_um,
            )
    return [replaced.get(int(a.location_id), a) if a.location_id else a for a in annotations]


def smoothing_passes(radius_um, is_path, *, discs: float, paths_um: float, edge_um: float,
                     max_passes: int = POLISH_MAX_PASSES) -> np.ndarray:
    """How many Taubin passes each vertex takes: an integer per vertex.

    ``radius_um`` and ``is_path`` are what the vertex's annotation says
    (:func:`~sbfsem.geometry.closed_curve.decode_labels`); ``edge_um`` is the
    mesh's mean edge.  The length a vertex is averaged over is ``discs``
    times its radius on a disc's surface and ``paths_um`` on a traced
    outline's; passes go as the square of that length in edges
    (:data:`POLISH_KAPPA`), at most ``max_passes``, fewer for a process too
    thin to take them (:data:`THIN_EDGES`), and none where nothing drew the
    vertex or its length is 0.
    """
    radius = np.asarray(radius_um, dtype=float)
    path = np.asarray(is_path, dtype=bool)
    length = np.where(path, float(paths_um), float(discs) * radius)
    edges = np.maximum(length, 0.0) / max(float(edge_um), 1e-9)
    passes = np.rint(POLISH_KAPPA * edges ** 2)
    low, high = THIN_EDGES
    passes *= np.clip((radius / max(float(edge_um), 1e-9) - low) / (high - low), 0.0, 1.0)
    passes = np.where(radius > 0, passes, 0.0)
    return np.minimum(np.rint(passes), max_passes).astype(np.int64)


def polish_curve_mesh(mesh: Mesh, volume, *, discs: float = 0.0, paths_um: float = 0.0) -> Mesh:
    """``mesh``, the contour of ``volume`` (built with ``keep_labels``), with
    its surface averaged: each vertex takes the Taubin passes
    :func:`smoothing_passes` gives it.  ``discs`` and ``paths_um`` are the two
    strengths; with both 0 the mesh is returned as it is.  Vertices that no
    voxel of the raster labels (none, for a mesh made of it) do not move."""
    if mesh.is_empty or (discs <= 0 and paths_um <= 0):
        return mesh
    radius, is_path = decode_labels(volume.label_at(mesh.vertices))
    v, f = mesh.vertices, mesh.faces
    edge = float(np.linalg.norm(v[f[:, 0]] - v[f[:, 1]], axis=1).mean())
    passes = smoothing_passes(radius, is_path, discs=discs, paths_um=paths_um, edge_um=edge)
    spent = int(passes.sum())
    if spent > POLISH_BUDGET:
        passes = np.floor(passes * (POLISH_BUDGET / spent)).astype(np.int64)
    return mesh.taubin_weighted(passes)


def owner_radius(points_um, centers, radii, polygons=None) -> np.ndarray:
    """The radius of the process each point is on, for the smoothing's thin-process
    guard: the disc whose rim is nearest among each point's eight nearest disc
    centers, or the traced outline (its equivalent radius) whose ring passes
    nearest, whichever is nearer.  An approximation of
    :func:`~sbfsem.geometry.meshdiff.nearest_annotation`'s owner, a k-d tree query
    or two in place of the exact distances: it only has to tell a thin process
    from a thick one."""
    pts = np.asarray(points_um, dtype=float).reshape(-1, 3)
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    radii = np.asarray(radii, dtype=float).ravel()
    gap = np.full(len(pts), np.inf)
    radius = np.zeros(len(pts))
    if len(centers):
        from scipy.spatial import cKDTree

        k = min(8, len(centers))
        dist, idx = cKDTree(centers).query(pts, k=k)
        dist, idx = dist.reshape(len(pts), k), idx.reshape(len(pts), k)
        rim = dist - radii[idx]
        best = np.argmin(rim, axis=1)
        rows = np.arange(len(pts))
        gap = rim[rows, best]
        radius = radii[idx[rows, best]]
    rings = [(np.asarray(r, dtype=float).reshape(-1, 2), float(z)) for r, z in (polygons or [])]
    rings = [(r, z) for r, z in rings if len(r) >= 3]
    if rings:
        from scipy.spatial import cKDTree

        eq = np.array([np.sqrt(0.5 * abs(float(np.dot(r[:, 0], np.roll(r[:, 1], -1))
                                               - np.dot(r[:, 1], np.roll(r[:, 0], -1)))) / np.pi)
                       for r, _z in rings])
        ring_pts = np.vstack([np.column_stack([r, np.full(len(r), z)]) for r, z in rings])
        ring_r = np.repeat(eq, [len(r) for r, _z in rings])
        dist, idx = cKDTree(ring_pts).query(pts, k=1)
        nearer = dist < gap
        radius = np.where(nearer, ring_r[idx], radius)
    return radius


def surface_passes(radius_um, *, length_um: float, edge_um: float,
                   max_passes: int = POLISH_MAX_PASSES) -> np.ndarray:
    """Smooth surface's passes per vertex: :data:`POLISH_KAPPA` times the length in
    edges, squared, as the polish counts them, but rounded up, so any length above
    0 takes at least one pass where the process is thick enough
    (:data:`THIN_EDGES`): at the polish's rounding 0.1 µm on a 0.1 µm mesh was no
    pass at all."""
    radius = np.asarray(radius_um, dtype=float)
    if length_um <= 0 or not len(radius):
        return np.zeros(len(radius), dtype=np.int64)
    edge = max(float(edge_um), 1e-9)
    want = POLISH_KAPPA * (float(length_um) / edge) ** 2
    low, high = THIN_EDGES
    thick = np.clip((radius / edge - low) / (high - low), 0.0, 1.0)
    passes = np.where(thick > 0, np.ceil(want * thick), 0.0)
    return np.minimum(passes, max_passes).astype(np.int64)


def smooth_surface(mesh: Mesh, centers, radii, polygons=None, *, length_um: float) -> Mesh:
    """``mesh`` with its finished surface smoothed over ``length_um``, and no
    annotation moved: Smooth surface µm, under every method (2026-10-17).

    The user's distinction: smoothing the inputs (Smooth discs, Smooth outlines,
    which move the annotations a surface is drawn through) is a different thing
    from smoothing the surface drawn through them.  This is the second only: the
    volume-preserving Taubin polish of :func:`polish_curve_mesh`, each vertex
    taking the passes :func:`surface_passes` gives a length of ``length_um`` on a
    surface of this mesh's mean edge, none where the process the vertex belongs
    to is only a pixel or two across (:func:`owner_radius`: the disc, or the
    traced outline's equivalent radius, nearest the vertex), so a thin dendrite
    is not eroded.  ``centers``/``radii``/``polygons`` are the cell's annotations
    (``facade.annotation_ground_truth``)."""
    if mesh.is_empty or length_um <= 0:
        return mesh
    v, f = mesh.vertices, mesh.faces
    edge = float(np.linalg.norm(v[f[:, 0]] - v[f[:, 1]], axis=1).mean())
    radius = owner_radius(v, centers, radii, polygons)
    passes = surface_passes(radius, length_um=float(length_um), edge_um=edge)
    spent = int(passes.sum())
    if not spent:
        return mesh
    if spent > POLISH_BUDGET:
        passes = np.floor(passes * (POLISH_BUDGET / spent)).astype(np.int64)
    return mesh.taubin_weighted(passes)
