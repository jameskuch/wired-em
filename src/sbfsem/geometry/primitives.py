"""Primitive meshes.

Replaces ``+sbfsem/+render/Primitives.m``.  The MATLAB class emitted VTK-style
``(cellList, pointList)`` pairs of polyline cells for ``vtkwrite``; nothing in
the repository consumed them except its own unit test.  Frontends here consume
triangle meshes, so the primitives are triangle meshes.

``synapse_spheres`` is the one genuinely used capability, from
``util/renders/synapseSphere.m``.
"""

from __future__ import annotations

import numpy as np

from .mesh import Mesh, concatenate

__all__ = ["sphere", "cuboid", "cylinder", "synapse_spheres", "synapse_ellipsoids"]


def sphere(radius: float = 1.0, center=(0.0, 0.0, 0.0), divisions: int = 21) -> Mesh:
    """UV sphere, matching MATLAB's ``sphere(21)`` default in ``synapseSphere``."""
    n = max(3, int(divisions))
    phi = np.linspace(0.0, np.pi, n + 1)
    theta = np.linspace(0.0, 2 * np.pi, n + 1)
    pp, tt = np.meshgrid(phi, theta, indexing="ij")
    x = np.sin(pp) * np.cos(tt)
    y = np.sin(pp) * np.sin(tt)
    z = np.cos(pp)
    from .cylinder import surf_to_mesh

    mesh = surf_to_mesh(x, y, z)
    mesh.vertices = mesh.vertices * float(radius) + np.asarray(center, dtype=float)
    return mesh.merge_duplicate_vertices(1e-9).fix_winding()


def cuboid(size=(1.0, 1.0, 1.0), center=(0.0, 0.0, 0.0)) -> Mesh:
    """Axis-aligned box, centered on ``center``."""
    s = np.asarray(size, dtype=float) / 2.0
    v = np.array(
        [
            [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
            [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
        ],
        dtype=float,
    ) * s + np.asarray(center, dtype=float)
    f = np.array(
        [
            [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6],
            [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3],
        ],
        dtype=np.int64,
    )
    return Mesh(v, f)


def cylinder(radius: float = 1.0, height: float = 1.0, divisions: int = 8,
             top_radius: float | None = None) -> Mesh:
    """Capped cylinder or truncated cone along +Z."""
    n = max(3, int(divisions))
    top_radius = radius if top_radius is None else top_radius
    theta = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    bottom = np.column_stack([radius * np.cos(theta), radius * np.sin(theta), np.zeros(n)])
    top = np.column_stack(
        [top_radius * np.cos(theta), top_radius * np.sin(theta), np.full(n, float(height))]
    )
    center_b = np.array([[0.0, 0.0, 0.0]])
    center_t = np.array([[0.0, 0.0, float(height)]])
    vertices = np.vstack([bottom, top, center_b, center_t])
    ib, it = 2 * n, 2 * n + 1
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces += [[i, j, n + j], [i, n + j, n + i], [ib, j, i], [it, n + i, n + j]]
    return Mesh(vertices, np.asarray(faces, dtype=np.int64))


def synapse_spheres(xyz, radius: float = 0.5, divisions: int = 12) -> Mesh:
    """One sphere per synapse location, merged into a single mesh.

    Ports ``util/renders/synapseSphere.m``, whose sibling ``synapseMarker.m``
    got the coordinate-matrix dispatch right where ``synapseSphere`` did not.
    """
    points = np.asarray(xyz, dtype=float).reshape(-1, 3)
    if points.size == 0:
        from .mesh import EMPTY_MESH

        return EMPTY_MESH
    unit = sphere(radius, (0.0, 0.0, 0.0), divisions)
    meshes = [Mesh(unit.vertices + p, unit.faces) for p in points]
    return concatenate(meshes)


def synapse_ellipsoids(
    xyz,
    directions,
    half_lengths,
    radius: float = 0.5,
    divisions: int = 12,
) -> Mesh:
    """One ellipsoid per synapse, elongated along the annotation's own axis.

    Viking stores a synapse annotation as a ``LINESTRING`` -- two endpoints in
    the section plane -- not as a disc.  ``directions`` are the unit vectors
    along those segments and ``half_lengths`` their half-extents in microns, so
    the marker reproduces how the synapse was actually drawn instead of
    replacing it with an isotropic blob.

    ``radius`` sets the two perpendicular semi-axes (the marker's thickness);
    the long semi-axis is the annotation's own half-length, used as measured.
    Real annotations run about 0.1-0.25 um half-length, so a marker thickness
    near 0.1 um shows the true proportions -- at the 0.5 um default used for
    round markers the ellipsoid is wider than the synapse is long, and reads as
    a disc facing along the axis rather than as an oval.
    """
    points = np.asarray(xyz, dtype=float).reshape(-1, 3)
    if points.size == 0:
        from .mesh import EMPTY_MESH

        return EMPTY_MESH

    dirs = np.asarray(directions, dtype=float).reshape(-1, 3)
    halves = np.asarray(half_lengths, dtype=float).ravel()
    if len(dirs) != len(points) or len(halves) != len(points):
        raise ValueError("directions and half_lengths must match xyz in length")

    unit = sphere(1.0, (0.0, 0.0, 0.0), divisions)
    meshes = []
    for point, direction, half in zip(points, dirs, halves, strict=True):
        norm = float(np.linalg.norm(direction))
        # Only a missing axis falls back to isotropic; a genuinely short
        # annotation stays short rather than being inflated to the radius.
        long_axis = float(half) if half > 0 else radius
        if norm < 1e-12:
            # No usable axis: an isotropic marker is the honest fallback.
            meshes.append(Mesh(unit.vertices * radius + point, unit.faces))
            continue
        axis = direction / norm
        # Any pair of vectors completing a right-handed frame will do; the
        # ellipsoid is rotationally symmetric about `axis`.
        helper = np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(helper, axis))) > 0.9:
            helper = np.array([1.0, 0.0, 0.0])
        u = np.cross(axis, helper)
        u /= np.linalg.norm(u)
        v = np.cross(axis, u)
        basis = np.column_stack([axis * long_axis, u * radius, v * radius])
        meshes.append(Mesh(unit.vertices @ basis.T + point, unit.faces))
    return concatenate(meshes)
