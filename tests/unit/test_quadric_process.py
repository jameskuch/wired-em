"""Quadric decimation in a process of its own (2026-10-20).

``fast_simplification`` keeps its working copy of a mesh in global vectors it
clears but never shrinks: after a capped Voxel remesh of Inferior c1178 a
build process held 452 MB where it had held 168, until it was retired, and the
next build in it peaked 187 MB higher.  :class:`~sbfsem.geometry.quadric.Decimator`
runs the same function of the same library in a child process that ends with
the decimation; what it returns must be what the call here returns, byte for
byte.
"""

from __future__ import annotations

import logging
import sys

import fast_simplification
import numpy as np
import pytest
import trimesh

from sbfsem.geometry import quadric
from sbfsem.geometry.decimate import decimate_in_chunks
from sbfsem.geometry.quadric import Decimator
from sbfsem.geometry.unify import BLOCK, unified_mesh


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()


@pytest.fixture(scope="module")
def sheet():
    """A closed whole-cell sheet of about 180,000 triangles: under ``PROCESS_FACES``."""
    t = np.linspace(0, 1, 30)[:, None]
    a = np.hstack([t * 18.0, np.zeros_like(t), t * 2.0])
    b = np.hstack([np.zeros_like(t) + 6.0, t * 12.0, t * 1.5 + 1.0])
    items = [("stretch", a, np.full(len(a), 0.45)),
             ("stretch", b, 0.3 + 0.3 * t.ravel()),
             ("coin", np.array([9.0, 2.0, 1.0]), 2.0)]
    return unified_mesh(items, voxel_um=0.04, tie_um=20.0)


def test_the_child_returns_what_the_call_here_returns(sheet):
    targets = (len(sheet.faces) // 2, len(sheet.faces) // 5)
    for border in (False, True):
        for index in (np.int64, np.int32):
            faces = sheet.faces.astype(index)
            with Decimator(min_faces=0) as decimator:
                # Several calls, as a sheet's chunks, go to the one child.
                got = [decimator.simplify(sheet.vertices, faces, n, preserve_border=border)
                       for n in targets]
                assert decimator._proc is not None
            for n, (points, triangles) in zip(targets, got, strict=True):
                here = fast_simplification.simplify(sheet.vertices, faces, target_count=n,
                                                    preserve_border=border)
                assert _same(points, here[0]) and _same(triangles, here[1])


def test_the_child_ends_with_the_decimation(sheet):
    with Decimator(min_faces=0) as decimator:
        decimator.simplify(sheet.vertices, sheet.faces, len(sheet.faces) // 2)
        child = decimator._proc
    assert child is not None and child.returncode == 0
    assert decimator._proc is None


def test_a_small_mesh_is_decimated_here_unless_the_child_runs(sheet, monkeypatch):
    n = len(sheet.faces)
    with Decimator(min_faces=n + 1) as decimator:
        decimator.simplify(sheet.vertices, sheet.faces, n // 2)
        assert decimator._proc is None

    def here(*args, **kwargs):
        raise AssertionError("decimated in this process")

    # Once the child runs, a smaller mesh (a sheet's last chunk) goes to it too.
    with Decimator(min_faces=n) as decimator:
        decimator.simplify(sheet.vertices, sheet.faces, n // 2)
        monkeypatch.setattr(fast_simplification, "simplify", here)
        points, triangles = decimator.simplify(sheet.vertices, sheet.faces[: n // 2], n // 4)
    assert abs(len(triangles) - n // 4) <= 2 and len(points) < len(sheet.vertices)


def test_mesh_decimate_is_trimesh_s_quadric_decimation(sheet, monkeypatch):
    monkeypatch.setattr(quadric, "PROCESS_FACES", 0)
    target = round(len(sheet.faces) * 0.3)
    ref = trimesh.Trimesh(vertices=sheet.vertices, faces=sheet.faces,
                          process=False).simplify_quadric_decimation(face_count=target)
    out = sheet.decimate(0.3)
    assert _same(out.vertices, np.asarray(ref.vertices))
    assert _same(out.faces, np.asarray(ref.faces))


def test_the_chunks_decimated_in_a_child_are_the_chunks_decimated_here(sheet, monkeypatch):
    span = BLOCK * sheet.meta["voxel2_um"]
    # A third kept, and a cap small enough for the last pass over the cuts.
    for target, chunk_faces in ((len(sheet.faces) // 3, 25_000), (6_000, 25_000)):
        monkeypatch.setattr(quadric, "PROCESS_FACES", 10**12)
        here = decimate_in_chunks(sheet, target, span_um=span, chunk_faces=chunk_faces)
        monkeypatch.setattr(quadric, "PROCESS_FACES", 0)
        child = decimate_in_chunks(sheet, target, span_um=span, chunk_faces=chunk_faces)
        assert _same(child.vertices, here.vertices) and _same(child.faces, here.faces)


def test_a_failure_in_the_child_is_raised_and_the_child_answers_on(sheet):
    with Decimator(min_faces=0) as decimator:
        with pytest.raises(RuntimeError, match="target_count"):
            decimator.simplify(sheet.vertices, sheet.faces, len(sheet.faces) + 10)
        points, triangles = decimator.simplify(sheet.vertices, sheet.faces, 9_000)
    here = fast_simplification.simplify(sheet.vertices, sheet.faces, target_count=9_000)
    assert _same(points, here[0]) and _same(triangles, here[1])


def test_a_child_that_cannot_start_leaves_the_call_here(sheet, monkeypatch, caplog):
    monkeypatch.setattr(sys, "executable", "/nonexistent/python")
    with caplog.at_level(logging.WARNING, logger="sbfsem.geometry.quadric"), \
            Decimator(min_faces=0) as decimator:
        points, triangles = decimator.simplify(sheet.vertices, sheet.faces, 9_000)
    here = fast_simplification.simplify(sheet.vertices, sheet.faces, target_count=9_000)
    assert _same(points, here[0]) and _same(triangles, here[1])
    assert "decimating in this process" in caplog.text


def test_a_child_that_dies_leaves_the_call_here(sheet, caplog):
    with caplog.at_level(logging.WARNING, logger="sbfsem.geometry.quadric"), \
            Decimator(min_faces=0) as decimator:
        decimator.simplify(sheet.vertices, sheet.faces, 20_000)
        assert decimator._proc is not None
        decimator._proc.kill()
        decimator._proc.wait()
        points, triangles = decimator.simplify(sheet.vertices, sheet.faces, 9_000)
        assert decimator._proc is None
    here = fast_simplification.simplify(sheet.vertices, sheet.faces, target_count=9_000)
    assert _same(points, here[0]) and _same(triangles, here[1])
    assert "decimating in this process" in caplog.text


def test_a_chunk_that_fails_is_kept_as_it_was(sheet, monkeypatch, caplog):
    # The sheet is let go before its chunks are decimated, so a chunk the decimator
    # fails on is kept as it was (as Mesh.decimate keeps a mesh), not decimated whole.
    span = BLOCK * sheet.meta["voxel2_um"]
    real = Decimator.simplify
    calls = []

    def fails_once(self, points, faces, target_count, **kwargs):
        calls.append(len(faces))
        if len(calls) == 2:
            raise RuntimeError("no")
        return real(self, points, faces, target_count, **kwargs)

    monkeypatch.setattr(Decimator, "simplify", fails_once)
    with caplog.at_level(logging.WARNING, logger="sbfsem.geometry.decimate"):
        out = decimate_in_chunks(sheet, len(sheet.faces) // 2, span_um=span, chunk_faces=60_000)
    assert len(calls) > 2 and "decimating chunk 2 of" in caplog.text
    edges = np.sort(np.concatenate([out.faces[:, [0, 1]], out.faces[:, [1, 2]], out.faces[:, [2, 0]]]), axis=1)
    _u, counts = np.unique(edges, axis=0, return_counts=True)
    assert np.all(counts == 2)
