"""Decimating a whole-cell sheet a chunk at a time (2026-10-20).

The decimator's copy of a whole raw sheet was the Voxel remesh's peak (Inferior
c1178, 2.1 million raw triangles to 1 million: 700 MB smoothing, 1,134 MB
decimating, on the server).  Cut into chunks with the cut edges locked, the
decimator holds one chunk; the chunks are welded back along the cuts, and each
chunk's share of the Face cap follows the error its edges start with, so the
triangles go where one pass over the sheet puts them.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.spatial import cKDTree

from sbfsem.geometry import decimate as decimate_module
from sbfsem.geometry.cylinder import cylinder_render
from sbfsem.geometry.decimate import decimate_in_chunks
from sbfsem.geometry.meshdiff import visual_bodies
from sbfsem.geometry.unify import BLOCK, decimates_in_chunks, split_components, unified_mesh
from sbfsem.io.legacy_json import read_legacy_json


def _sheet(voxel=0.03):
    """A closed whole-cell sheet: a fat body, two thin processes and a link, about
    120,000 raw triangles."""
    t = np.linspace(0, 1, 40)[:, None]
    a = np.hstack([t * 24.0, np.zeros_like(t), t * 3.0])
    b = np.hstack([np.zeros_like(t) + 8.0, t * 16.0, t * 2.0 + 1.0])
    items = [("stretch", a, np.full(len(a), 0.5)),
             ("stretch", b, 0.3 + 0.4 * t.ravel()),
             ("coin", np.array([12.0, 2.0, 1.5]), 2.5)]
    return unified_mesh(items, voxel_um=voxel, tie_um=20.0)


def _edges_shared_twice(mesh):
    e = np.sort(np.concatenate([mesh.faces[:, [0, 1]], mesh.faces[:, [1, 2]], mesh.faces[:, [2, 0]]]), axis=1)
    _u, counts = np.unique(e, axis=0, return_counts=True)
    return bool(np.all(counts == 2))


@pytest.fixture(scope="module")
def sheet():
    mesh = _sheet()
    assert len(mesh.faces) > 60_000 and _edges_shared_twice(mesh)
    return mesh


def test_the_chunks_weld_back_into_one_closed_surface_at_the_cap(sheet):
    span = BLOCK * sheet.meta["voxel2_um"]
    target = len(sheet.faces) // 5
    out = decimate_in_chunks(sheet, target, span_um=span, chunk_faces=20_000)
    # Within a few triangles of the cap, never over it.
    assert target - 8 <= len(out.faces) <= target
    # The vertices along the cuts kept their coordinates in both chunks, so the
    # weld leaves no seam: every edge has two triangles, one piece, one body.
    assert _edges_shared_twice(out)
    assert len(split_components(out)) == 1
    assert visual_bodies(out) == 1
    # No vertex twice.
    assert len(np.unique(out.vertices, axis=0)) == len(out.vertices)


def test_the_chunks_keep_the_shape_one_pass_keeps(sheet):
    span = BLOCK * sheet.meta["voxel2_um"]
    smoothed = sheet.taubin(1)                     # what the pathway decimates
    raw_points = smoothed.vertices[::7]
    for keep in (2, 3):                            # the share a capped build keeps: a third and over
        target = len(smoothed.faces) // keep
        whole = smoothed.decimate(target / len(smoothed.faces))
        chunks = decimate_in_chunks(smoothed, target, span_um=span, chunk_faces=60_000)
        assert abs(chunks.volume() - whole.volume()) < 1e-4 * abs(whole.volume())
        assert abs(chunks.surface_area() - whole.surface_area()) < 1e-3 * whole.surface_area()
        # The sheet's vertices are as near a kept vertex as one pass leaves them, to 3 %
        # of the voxel (measured 13.8 and 14.3 nm at half kept, 18.8 and 19.3 at a third).
        off_whole = cKDTree(whole.vertices).query(raw_points)[0].mean()
        off_chunks = cKDTree(chunks.vertices).query(raw_points)[0].mean()
        assert off_chunks < off_whole + 0.03 * sheet.meta["voxel2_um"]


def test_a_sheet_of_one_chunk_is_decimated_whole(sheet):
    span = BLOCK * sheet.meta["voxel2_um"]
    target = len(sheet.faces) // 3
    whole = sheet.decimate(target / len(sheet.faces))
    one = decimate_in_chunks(sheet, target, span_um=span, chunk_faces=len(sheet.faces))
    assert np.array_equal(one.faces, whole.faces) and np.array_equal(one.vertices, whole.vertices)
    assert decimate_in_chunks(sheet, len(sheet.faces), span_um=span) is sheet


def test_only_a_cap_whose_raw_budget_is_over_three_million_decimates_in_chunks():
    assert not decimates_in_chunks(0)            # every triangle kept: nothing to decimate
    assert not decimates_in_chunks(400_000)      # the app's default
    assert not decimates_in_chunks(1_000_000)    # a raw budget of 3 million, as before 2.5.1
    assert decimates_in_chunks(1_000_001)
    assert decimates_in_chunks(3_400_000)


def test_the_pathway_decimates_a_large_budget_in_chunks(monkeypatch, i943_path):
    neuron = read_legacy_json(i943_path)
    calls = []
    real = decimate_module.decimate_in_chunks

    def spy(mesh, face_count, **kw):
        out = real(mesh, face_count, **kw)
        calls.append((len(mesh.faces), face_count, len(out.faces), kw["span_um"]))
        return out

    monkeypatch.setattr(decimate_module, "decimate_in_chunks", spy)
    monkeypatch.setattr(decimate_module, "CHUNK_FACES", 5_000)
    # A cap of 1.1 million asks for a raw budget of 3.3 million; Decimate 0.5 halves the sheet.
    mesh = cylinder_render(neuron, pathway="voxel2", voxel2_um=0.3, voxel2_faces=1_100_000,
                           reduce_fraction=0.5, smooth_iterations=1)
    assert len(calls) == 1
    raw, target, kept, span = calls[0]
    assert target == round(raw * 0.5) and kept == len(mesh.faces) <= target
    assert span == pytest.approx(BLOCK * mesh.meta["voxel2_um"])
    assert visual_bodies(mesh) == 1
    # The app's default cap decimates in one pass, as it always has.
    calls.clear()
    cylinder_render(neuron, pathway="voxel2", voxel2_um=0.3, voxel2_faces=400_000,
                    reduce_fraction=0.5, smooth_iterations=1)
    assert calls == []
