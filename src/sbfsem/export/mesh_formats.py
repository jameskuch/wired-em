"""STL, OBJ, PLY and glTF mesh IO.

Ports ``+sbfsem/+io/STL.m`` and ``+sbfsem/+io/OBJ.m`` (import only in MATLAB) on
top of trimesh, and adds glTF/GLB, which is what the web frontend streams.

The MATLAB writers tested ``uigetdir``/``uiputfile`` cancellation with
``isempty``, but those return numeric ``0`` on cancel, so the check never fired
and a null character ended up in the path (F043, F044).  These take explicit
paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np

from ..geometry.mesh import Mesh

__all__ = ["write_stl", "write_obj", "write_ply", "write_gltf", "mesh_to_glb_bytes",
           "read_obj", "read_mesh", "mesh_edges", "segment_edges", "write_wireframe_obj",
           "write_wireframe_ply", "write_wireframe_stl",
           "write_wireframe_table", "write_wireframe_npy",
           "write_wireframe_npz", "write_wireframe_hdf5"]


def mesh_edges(mesh: Mesh) -> np.ndarray:
    """The unique undirected edges of a triangle mesh, as an (M, 2) array."""
    f = np.asarray(mesh.faces, dtype=np.int64)
    e = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    e.sort(axis=1)
    return np.unique(e, axis=0)


def segment_edges(segments) -> tuple[np.ndarray, np.ndarray]:
    """Line segments ``(S, 2, 3)`` as shared vertices ``(N, 3)`` and edges ``(M, 2)``.

    Endpoints that are the same point -- a ring's outline point, where the
    ring closes and a line along the chain leaves it -- become one vertex, so
    OBJ and PLY readers see connected polylines rather than loose pieces.  The
    match is exact on purpose: those endpoints are one value copied, never
    recomputed, and a near miss only costs a vertex.  Non-finite, zero-length
    and repeated segments are dropped.
    """
    seg = np.asarray(segments, dtype=float).reshape(-1, 2, 3)
    seg = seg[np.isfinite(seg).all(axis=(1, 2))]
    if not len(seg):
        return np.zeros((0, 3)), np.zeros((0, 2), dtype=np.int64)
    vertices, inverse = np.unique(seg.reshape(-1, 3), axis=0, return_inverse=True)
    edges = inverse.reshape(-1, 2).astype(np.int64)
    edges.sort(axis=1)
    edges = edges[edges[:, 0] != edges[:, 1]]
    return vertices, np.unique(edges, axis=0)


def _wire(mesh: Mesh | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vertices and edges of a mesh's triangle edges, or of ``(S, 2, 3)`` segments."""
    if isinstance(mesh, Mesh):
        return np.asarray(mesh.vertices, dtype=float), mesh_edges(mesh)
    return segment_edges(mesh)


def write_wireframe_obj(mesh: Mesh | np.ndarray, path: str | Path, *,
                        notes: Mapping[str, object] | None = None) -> Path:
    """The edges as OBJ line elements (``l``), not faces.

    ``mesh`` is a :class:`Mesh`, whose triangle edges are written, or
    ``(S, 2, 3)`` line segments in microns.  This is the render window's
    wireframe as real geometry: Blender and friends import ``l`` records as
    edges.  ``notes`` become ``#`` comment lines at the top.
    """
    path = Path(path)
    v, edges = _wire(mesh)
    lines = [f"# {key} {value}" for key, value in (notes or {}).items()]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in v]
    lines += [f"l {a + 1} {b + 1}" for a, b in edges]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_wireframe_ply(mesh: Mesh | np.ndarray, path: str | Path, *,
                        notes: Mapping[str, object] | None = None) -> Path:
    """The edges (of a mesh, or of segments) as an ASCII PLY with an ``edge`` element.

    ``notes`` become ``comment`` lines in the header.
    """
    path = Path(path)
    v, edges = _wire(mesh)
    header = "\n".join([
        "ply", "format ascii 1.0",
        *(f"comment {key} {value}" for key, value in (notes or {}).items()),
        f"element vertex {len(v)}",
        "property float x", "property float y", "property float z",
        f"element edge {len(edges)}",
        "property int vertex1", "property int vertex2",
        "end_header",
    ])
    body = [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in v]
    body += [f"{a} {b}" for a, b in edges]
    path.write_text(header + "\n" + "\n".join(body) + "\n", encoding="utf-8")
    return path


def write_wireframe_table(mesh: Mesh | np.ndarray, path: str | Path, *,
                          delimiter: str = ",") -> Path:
    """The edges (of a mesh, or of segments) as a flat table, one row per edge.

    Columns: the two vertex indices and both endpoints' coordinates in
    microns -- self-contained, so a spreadsheet or pandas can plot segments
    without joining a second file.
    """
    path = Path(path)
    v, edges = _wire(mesh)
    header = delimiter.join(
        ["v1", "v2", "x1_um", "y1_um", "z1_um", "x2_um", "y2_um", "z2_um"])
    rows = [header]
    for a, b in edges:
        pa, pb = v[a], v[b]
        rows.append(delimiter.join(
            [str(int(a)), str(int(b))]
            + [f"{c:.6f}" for c in (*pa, *pb)]))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def write_wireframe_npy(mesh: Mesh | np.ndarray, path: str | Path) -> Path:
    """The edges as one (M, 2, 3) float array of micron segments."""
    path = Path(path)
    v, edges = _wire(mesh)
    np.save(path, v[edges])
    return path


def write_wireframe_npz(mesh: Mesh | np.ndarray, path: str | Path) -> Path:
    """Vertices (N, 3) and edge indices (M, 2), compressed."""
    path = Path(path)
    v, edges = _wire(mesh)
    np.savez_compressed(path, vertices=v, edges=edges)
    return path


def write_wireframe_hdf5(mesh: Mesh | np.ndarray, path: str | Path, *,
                         name: str | None = None,
                         notes: Mapping[str, object] | None = None) -> Path:
    """Vertices and edge indices as HDF5 datasets, units recorded.

    ``notes`` become file attributes beside ``units`` and ``name``.
    """
    import h5py

    path = Path(path)
    v, edges = _wire(mesh)
    with h5py.File(path, "w") as fh:
        fh.create_dataset("vertices", data=v)
        fh.create_dataset("edges", data=edges)
        fh.attrs["units"] = "micron"
        if name:
            fh.attrs["name"] = name
        for key, value in (notes or {}).items():
            fh.attrs[key] = value
    return path


def write_wireframe_stl(mesh: Mesh | np.ndarray, path: str | Path, *,
                        thickness_um: float = 0.05) -> Path:
    """The edges (of a mesh, or of segments) as thin triangular rods, since STL has no lines.

    Each edge becomes a closed three-sided prism of the given radius, which is
    what makes the wireframe printable and viewable in STL-only tools.
    """
    path = Path(path)
    v, edges = _wire(mesh)
    a = v[edges[:, 0]]
    b = v[edges[:, 1]]
    d = b - a
    ln = np.linalg.norm(d, axis=1, keepdims=True)
    ln[ln == 0] = 1.0
    d = d / ln
    # A perpendicular frame per edge, robust to any orientation.
    helper = np.where(np.abs(d[:, 2:3]) < 0.9,
                      np.array([[0.0, 0.0, 1.0]]), np.array([[1.0, 0.0, 0.0]]))
    u = np.cross(d, helper)
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    w = np.cross(d, u)
    t = float(thickness_um)
    offsets = [u * t,
               (-0.5 * u + 0.8660254 * w) * t,
               (-0.5 * u - 0.8660254 * w) * t]
    n_e = len(edges)
    verts = np.empty((n_e * 6, 3))
    for k, off in enumerate(offsets):
        verts[k::6] = a + off          # ring at a: local 0, 1, 2
        verts[3 + k::6] = b + off      # ring at b: local 3, 4, 5
    local = np.array([
        [0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4], [2, 0, 3], [2, 3, 5],
        [0, 2, 1], [3, 4, 5],           # end caps close each rod
    ], dtype=np.int64)
    faces = (local[None, :, :] + (np.arange(n_e) * 6)[:, None, None])
    return write_stl(Mesh(verts, faces.reshape(-1, 3)), path)


def _to_trimesh(mesh: Mesh):
    import trimesh

    return trimesh.Trimesh(vertices=mesh.vertices, faces=mesh.faces, process=False)


def write_stl(mesh: Mesh, path: str | Path, *, binary: bool = True) -> Path:
    path = Path(path)
    tm = _to_trimesh(mesh)
    data = tm.export(file_type="stl" if binary else "stl_ascii")
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with path.open(mode, encoding=None if "b" in mode else "utf-8") as fh:
        fh.write(data)
    return path


def write_obj(mesh: Mesh, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(_to_trimesh(mesh).export(file_type="obj"), encoding="utf-8")
    return path


def write_ply(mesh: Mesh, path: str | Path) -> Path:
    path = Path(path)
    path.write_bytes(_to_trimesh(mesh).export(file_type="ply"))
    return path


def write_gltf(meshes, path: str | Path) -> Path:
    """Write one or more meshes to ``.glb`` (binary glTF) or ``.gltf``."""
    import trimesh

    path = Path(path)
    scene = trimesh.Scene()
    if isinstance(meshes, Mesh):
        meshes = {path.stem: meshes}
    items = meshes.items() if isinstance(meshes, Mapping) else list(meshes)
    for name, mesh in items:
        if mesh is None or mesh.is_empty:
            continue
        scene.add_geometry(_to_trimesh(mesh), node_name=str(name), geom_name=str(name))
    file_type = "glb" if path.suffix.lower() == ".glb" else "gltf"
    data = scene.export(file_type=file_type)
    if isinstance(data, dict):  # gltf returns a name -> bytes mapping
        for filename, payload in data.items():
            target = path if filename.endswith(".gltf") else path.with_name(filename)
            mode = "wb" if isinstance(payload, (bytes, bytearray)) else "w"
            with target.open(mode, encoding=None if "b" in mode else "utf-8") as fh:
                fh.write(payload)
    else:
        path.write_bytes(data)
    return path


def mesh_to_glb_bytes(mesh: Mesh, name: str = "mesh") -> bytes:
    """Serialize a mesh to GLB in memory, for streaming over HTTP."""
    import trimesh

    scene = trimesh.Scene()
    scene.add_geometry(_to_trimesh(mesh), node_name=name, geom_name=name)
    return scene.export(file_type="glb")


def read_obj(path: str | Path) -> Mesh:
    """Load a Wavefront OBJ.  Ports ``+sbfsem/+io/OBJ.m`` and ``lib/readObj.m``."""
    return read_mesh(path)


def read_mesh(path: str | Path) -> Mesh:
    """Load any mesh format trimesh understands."""
    import trimesh

    loaded = trimesh.load(str(path), force="mesh", process=False)
    if not isinstance(loaded, trimesh.Trimesh):
        raise ValueError(f"{path} did not load as a triangle mesh")
    return Mesh(np.asarray(loaded.vertices), np.asarray(loaded.faces))
