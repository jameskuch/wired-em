"""Mesh post-processing: clipping, flattening and bounding boxes.

Ports ``util/renders/clipMesh.m``, ``clipMeshByVertices.m``,
``clipMeshByStratification.m``, ``getStrataCData.m``, ``clipStrataCData.m``,
``getBoundingBox.m``, ``xyzr2bb.m``, ``bb2size.m``, ``bb2mesh.m`` and
``groupBoundingBox.m``.

Deviations (see DEVIATIONS.md):

* ``clipMeshByVertices`` referenced ``cutVerts``, a variable that only exists in
  ``clipMesh.m`` from which it was copied, so every call errored (F089).
* All three clip functions scanned faces in a Python-style loop with
  ``intersect`` per face; these are vectorised.
* The MATLAB versions accepted either an ``FV`` struct or a live patch handle and
  mutated the handle in place.  These take and return meshes.
"""

from __future__ import annotations

import numpy as np

from .mesh import Mesh

__all__ = [
    "clip_mesh",
    "clip_mesh_by_vertices",
    "clip_mesh_by_stratification",
    "bounding_box",
    "bounding_box_from_points",
    "bounding_box_size",
    "bounding_box_grid",
    "group_bounding_box",
    "flatten_render",
]


def clip_mesh_by_vertices(mesh: Mesh, remove) -> Mesh:
    """Drop the given vertices and every face that touches one."""
    remove = np.asarray(remove)
    keep = np.ones(len(mesh.vertices), dtype=bool)
    if remove.dtype == bool:
        keep &= ~remove
    else:
        keep[remove.astype(np.int64)] = False
    return mesh._select_vertices(keep)


def clip_mesh(mesh: Mesh, cutoff: float, keep_above: bool = True, axis: int = 2) -> Mesh:
    """Remove everything above or below ``cutoff`` along one axis.

    ``axis`` is 0, 1 or 2 for X, Y or Z; the MATLAB ``dim`` argument was 1-based.
    """
    if mesh.is_empty:
        return mesh
    coord = mesh.vertices[:, int(axis)]
    remove = coord < cutoff if keep_above else coord > cutoff
    return clip_mesh_by_vertices(mesh, remove)


def clip_mesh_by_stratification(
    mesh: Mesh, boundaries, cutoff: float, keep_above: bool = True, method: str = "column"
) -> Mesh:
    """Clip by IPL depth rather than by raw Z.

    ``boundaries`` is an :class:`~sbfsem.analysis.boundary.IPLBoundaries`;
    ``method`` how the depth is measured (``"column"``, MATLAB's, or ``"normal"``).
    """
    if mesh.is_empty:
        return mesh
    depth = boundaries.micron2ipl(mesh.vertices, method)
    remove = depth < cutoff if keep_above else depth > cutoff
    remove = np.where(np.isnan(depth), True, remove)
    return clip_mesh_by_vertices(mesh, remove)


def flatten_render(mesh: Mesh, boundaries, method: str = "column") -> Mesh:
    """Replace each vertex's Z with its IPL depth.

    Ports the ``flattenRender`` helper referenced from ``RenderApp``; the file
    itself is missing from the repository, but ``getStrataCData`` and
    ``stratificationMap`` establish exactly what it computed.  ``method`` as
    :func:`clip_mesh_by_stratification`'s.
    """
    out = mesh.copy()
    depth = boundaries.micron2ipl(mesh.vertices, method)
    out.vertices = np.column_stack([mesh.vertices[:, 0], mesh.vertices[:, 1], depth])
    out.vertex_data = depth
    return out


def bounding_box_from_points(xyz, radius=None) -> np.ndarray:
    """``[xmin, xmax, ymin, ymax]`` around points, optionally inflated by radii.

    Ports ``util/renders/xyzr2bb.m``.
    """
    xyz = np.asarray(xyz, dtype=float).reshape(-1, 3 if np.asarray(xyz).ndim > 1 and np.asarray(xyz).shape[-1] == 3 else 2)
    if radius is None:
        return np.array(
            [
                np.floor(xyz[:, 0].min()),
                np.ceil(xyz[:, 0].max()),
                np.floor(xyz[:, 1].min()),
                np.ceil(xyz[:, 1].max()),
            ]
        )
    r = np.asarray(radius, dtype=float).ravel()
    if r.size != len(xyz):
        raise ValueError("xyz and radius must have the same number of rows")
    return np.array(
        [
            np.floor((xyz[:, 0] - r).min()),
            np.ceil((xyz[:, 0] + r).max()),
            np.floor((xyz[:, 1] - r).min()),
            np.ceil((xyz[:, 1] + r).max()),
        ]
    )


def bounding_box(neuron, micron: bool = True) -> np.ndarray:
    """XY extent of a neuron's annotations, inflated by their radii.

    Ports ``util/renders/getBoundingBox.m``, including closed-curve outlines,
    which the MATLAB version left as a TODO.
    """
    nodes = neuron.nodes
    if micron:
        xy = nodes[["Xum", "Yum"]].to_numpy(dtype=float)
        r = nodes["Rum"].to_numpy(dtype=float)
    else:
        xy = nodes[["VolumeX", "VolumeY"]].to_numpy(dtype=float)
        r = nodes["Radius"].to_numpy(dtype=float)
    box = np.array(
        [(xy[:, 0] - r).min(), (xy[:, 0] + r).max(), (xy[:, 1] - r).min(), (xy[:, 1] + r).max()]
    )
    geometries = getattr(neuron, "geometries", None)
    if micron and geometries is not None and len(geometries):
        for curves in geometries["Curve"]:
            for ring in curves or []:
                ring = np.asarray(ring, dtype=float)
                if ring.size == 0:
                    continue
                box = np.array(
                    [
                        min(box[0], ring[:, 0].min()),
                        max(box[1], ring[:, 0].max()),
                        min(box[2], ring[:, 1].min()),
                        max(box[3], ring[:, 1].max()),
                    ]
                )
    return box


def bounding_box_size(box) -> tuple[float, float]:
    """Ports ``util/renders/bb2size.m``."""
    box = np.asarray(box, dtype=float)
    return float(box[1] - box[0]), float(box[3] - box[2])


def bounding_box_grid(box) -> tuple[np.ndarray, np.ndarray]:
    """Ports ``util/renders/bb2mesh.m``: a unit-spaced Cartesian grid."""
    box = np.asarray(box, dtype=float)
    return np.meshgrid(np.arange(box[0], box[1] + 1), np.arange(box[2], box[3] + 1))


def group_bounding_box(boxes) -> np.ndarray:
    """Union of several ``[xmin, xmax, ymin, ymax]`` boxes."""
    stacked = np.vstack([np.asarray(b, dtype=float).ravel() for b in boxes])
    return np.array(
        [
            stacked[:, 0].min(),
            stacked[:, 1].max(),
            stacked[:, 2].min(),
            stacked[:, 3].max(),
        ]
    )
