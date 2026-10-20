"""The raw sheet's stages in less memory, to the bit (2026-10-20).

After the chunked decimation the peak of a big capped Voxel remesh build was the
raw sheet's own processing: the weld stacked every block and copied the stack
twice, the tie step welded again with the untied sheet alive, the smoothing
gathered the whole edge list per coordinate and the decimator joined its chunks
with the sheet still held.  Each now works a run at a time or lets go first,
and must give the arrays the old code gave, byte for byte: each test below
holds the new code to the old one, kept here as the reference.
"""

from __future__ import annotations

import weakref

import numpy as np
import pytest
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from sbfsem.geometry import unify
from sbfsem.geometry.decimate import decimate_in_chunks
from sbfsem.geometry.mesh import Mesh
from sbfsem.geometry.unify import BLOCK, count_components, split_components, unified_mesh


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()


def _items():
    t = np.linspace(0, 1, 30)[:, None]
    a = np.hstack([t * 18.0, np.zeros_like(t), t * 2.0])
    b = np.hstack([np.zeros_like(t) + 6.0, t * 12.0, t * 1.5 + 1.0])
    return [("stretch", a, np.full(len(a), 0.45)),
            ("stretch", b, 0.3 + 0.3 * t.ravel()),
            ("coin", np.array([9.0, 2.0, 1.0]), 2.0),
            # Apart from the rest: a second piece, so the tie step welds again.
            ("stretch", np.array([[30.0, 0.0, 0.0], [34.0, 0.0, 0.5]]), np.array([0.4, 0.4]))]


def _stacked_weld(grid, index_out=None) -> Mesh:
    """The weld as it was: every block stacked, then welded."""
    verts_parts, faces_parts, offsets, offset = [], [], {}, 0
    for block_key, (v, f) in grid.tris.items():
        verts_parts.append(v)
        faces_parts.append(f + offset)
        offsets[block_key] = (offset, len(v))
        offset += len(v)
    verts, faces = np.vstack(verts_parts), np.vstack(faces_parts)
    idx = (verts - grid.origin) / grid.voxel / grid.block
    face_ids = np.flatnonzero((np.abs(idx - np.rint(idx)) < 1e-6).any(axis=1))
    remap = np.arange(len(verts))
    if len(face_ids):
        key = np.round(verts[face_ids] / 1e-6).astype(np.int64)
        _u, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
        remap[face_ids] = face_ids[first[np.asarray(inverse).ravel()]]
        faces = remap[faces]
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    used = np.zeros(len(verts), dtype=bool)
    used[faces.ravel()] = True
    compact = np.cumsum(used) - 1
    if index_out is not None:
        welded = np.where(used[remap], compact[remap], -1)
        for k, (start, n) in offsets.items():
            index_out[k] = welded[start:start + n]
    return Mesh(verts[used], compact[faces]).fix_winding()


@pytest.fixture(scope="module")
def grid():
    """A contoured whole-cell grid, before its gaps are tied."""
    seen = {}
    real = unify._BlockGrid.mesh

    def keep(self, index_out=None):
        seen.setdefault("grid", (self, dict(self.tris)))
        return real(self, index_out=index_out)

    unify._BlockGrid.mesh = keep
    try:
        unified_mesh(_items(), voxel_um=0.06, tie_um=20.0)
    finally:
        unify._BlockGrid.mesh = real
    g, tris = seen["grid"]
    g.tris = tris
    assert len(tris) > 20
    return g


@pytest.mark.parametrize("run", [1, 300, 5_000, unify.WELD_RUN_VERTICES])
def test_the_weld_a_run_at_a_time_is_the_stacked_weld(grid, monkeypatch, run):
    monkeypatch.setattr(unify, "WELD_RUN_VERTICES", run)
    for keys in (list(grid.tris), list(grid.tris)[::2], list(grid.tris)[::-1]):
        sub = unify._BlockGrid(grid.voxel, grid.origin, block=grid.block, blend_um=0.0)
        sub.tris = {k: grid.tris[k] for k in keys}
        old_index, new_index = {}, {}
        old = _stacked_weld(sub, old_index)
        new = sub.mesh(index_out=new_index)
        assert _same(new.vertices, old.vertices) and _same(new.faces, old.faces)
        assert list(new_index) == list(old_index)
        assert all(_same(new_index[k], old_index[k]) for k in old_index)


def test_turning_the_winding_in_place_is_flip_faces():
    rng = np.random.default_rng(3)
    for sign in (1.0, -1.0):
        inside_out = Mesh(rng.normal(size=(60, 3)), rng.integers(0, 60, size=(200, 3)))
        signed = sum(float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum())
                     for v in (inside_out.vertices[inside_out.faces],))
        if np.sign(signed) != sign:
            inside_out = inside_out.flip_faces()
        expected = inside_out.copy().fix_winding()
        mine = inside_out.copy()
        assert mine.fix_winding(in_place=True) is mine
        assert _same(mine.faces, expected.faces) and _same(mine.vertices, expected.vertices)


def _edge_list_mean(mesh: Mesh, v: np.ndarray) -> np.ndarray:
    """The neighbor mean as it was: one bincount over the whole directed edge list."""
    rows, cols, inv_degree = mesh._directed_edges()
    out = np.empty_like(v)
    for c in range(v.shape[1]):
        acc = np.bincount(rows, weights=v[cols, c], minlength=len(mesh.vertices))
        out[:, c] = (acc + v[:, c]) * inv_degree
    return out


def test_the_neighbor_mean_by_rows_sums_as_the_edge_list_did(grid, monkeypatch):
    sheet = _stacked_weld(grid)
    rng = np.random.default_rng(5)
    # A mesh with degenerate triangles (self loops) and unused vertices as well.
    odd = Mesh(rng.normal(size=(500, 3)) * 1e3, rng.integers(0, 400, size=(900, 3)))
    for mesh in (sheet, odd):
        for rows_at_a_time in (7, 1000, Mesh.NEIGHBOR_ROWS):
            monkeypatch.setattr(Mesh, "NEIGHBOR_ROWS", rows_at_a_time)
            v = mesh.vertices + rng.normal(size=mesh.vertices.shape) * 1e-3
            assert _same(mesh._neighbor_mean()(v), _edge_list_mean(mesh, v))
        _r, _c, inv = mesh._directed_edges()
        assert _same(mesh._adjacency()[2], inv)


def test_smoothing_where_the_vertices_lie_is_the_smoothing_it_was(grid):
    sheet = _stacked_weld(grid)
    v = sheet.vertices
    for _ in range(2):
        for step in (0.5, -0.53):
            v = v + step * (_edge_list_mean(sheet, v) - v)
    smoothed = sheet.taubin(2)
    assert _same(smoothed.vertices, v) and _same(sheet.vertices, _stacked_weld(grid).vertices)
    v = sheet.vertices
    for _ in range(3):
        v = v + 0.4 * (_edge_list_mean(sheet, v) - v)
        v = v - 0.6 * (_edge_list_mean(sheet, v) - v)
    assert _same(sheet.smooth_inflate(3, 0.4, 0.6).vertices, v)


def _old_split(mesh: Mesh) -> list[Mesh]:
    faces, n = mesh.faces, mesh.n_vertices
    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]]).astype(np.int32)
    cols = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]]).astype(np.int32)
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    n_comp, labels = connected_components(graph, directed=False)
    face_label = labels[faces[:, 0]]
    out = []
    for k in range(n_comp):
        sel = face_label == k
        if not sel.any():
            continue
        keep = np.zeros(n, dtype=bool)
        keep[np.unique(faces[sel])] = True
        remap = -np.ones(n, dtype=np.int64)
        remap[keep] = np.arange(int(keep.sum()))
        out.append(Mesh(mesh.vertices[keep], remap[faces[sel]]))
    return out


def test_the_pieces_and_their_count_are_as_they_were(grid):
    rng = np.random.default_rng(11)
    sheet = _stacked_weld(grid)
    # Many small pieces, isolated vertices and a vertex order that is not the pieces'.
    scattered = Mesh(rng.normal(size=(3000, 3)), rng.integers(0, 3000, size=(1500, 3)))
    for mesh in (sheet, scattered):
        old, new = _old_split(mesh), split_components(mesh)
        assert len(new) == len(old) == count_components(mesh) > 1
        for a, b in zip(old, new, strict=True):
            assert _same(a.vertices, b.vertices) and _same(a.faces, b.faces)
        # What the tie search reads: the same pieces' vertices, without their faces.
        pieces = unify._piece_vertices(mesh)
        assert len(pieces) == len(old)
        assert all(_same(a.vertices, b.vertices) for a, b in zip(old, pieces, strict=True))


def test_the_chunked_decimator_lets_a_handed_over_sheet_go_before_the_join(monkeypatch):
    sheet = unified_mesh(_items()[:3], voxel_um=0.04, tie_um=20.0)
    span = BLOCK * sheet.meta["voxel2_um"]
    target = len(sheet.faces) // 4
    expected = decimate_in_chunks(sheet, target, span_um=span, chunk_faces=15_000)
    alive = []
    import trimesh

    real = trimesh.Trimesh

    def join(*args, **kwargs):
        alive.append(gone() is not None)
        return real(*args, **kwargs)

    gone = weakref.ref(sheet)
    held = [sheet]
    del sheet
    monkeypatch.setattr(trimesh, "Trimesh", join)
    out = decimate_in_chunks(held.pop(), target, span_um=span, chunk_faces=15_000)
    # The join, and the last pass over the cuts when there is one.
    assert alive and not any(alive)
    assert _same(out.vertices, expected.vertices) and _same(out.faces, expected.faces)
    assert out.meta == expected.meta
