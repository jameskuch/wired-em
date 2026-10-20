"""MeshLab remeshing: the optional, GPL-3 pymeshlab, run in a child process.

The adapter's own behavior (the identity, the refusals, the error reporting)
is tested everywhere; what MeshLab itself returns is tested only where the
``[meshlab]`` extra is installed, against a sphere whose volume and edge
lengths are known.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

from sbfsem.geometry import meshlab
from sbfsem.geometry.primitives import sphere

needs_meshlab = pytest.mark.skipif(not meshlab.available(),
                                   reason="the optional pymeshlab extra is not installed")


def test_pymeshlab_is_never_imported_by_the_package():
    """GPL-3: the package looks for it and runs it, and that is all."""
    meshlab.available()
    meshlab.remesh(sphere(1.0, divisions=8), "none")
    assert "pymeshlab" not in sys.modules


def test_none_is_the_identity_and_bad_input_is_refused(monkeypatch):
    ball = sphere(1.0, divisions=8)
    assert meshlab.remesh(ball, "none") is ball
    with pytest.raises(ValueError, match="one of"):
        meshlab.remesh(ball, "laplacian")
    monkeypatch.setattr(meshlab, "available", lambda: True)
    with pytest.raises(ValueError, match="edge_um"):
        meshlab.remesh(ball, "isotropic", edge_um=0)
    with pytest.raises(ValueError, match="fraction"):
        meshlab.remesh(ball, "qem", fraction=1.5)


def test_without_the_extra_it_says_how_to_get_it(monkeypatch):
    monkeypatch.setattr(meshlab, "available", lambda: False)
    with pytest.raises(meshlab.MeshlabUnavailable, match=r"sbfsem\[meshlab\]"):
        meshlab.remesh(sphere(1.0, divisions=8), "isotropic")
    # A ValueError, so the HTTP layer answers 400 with the message.
    assert issubclass(meshlab.MeshlabUnavailable, ValueError)


def test_a_failed_or_silent_child_is_an_error_not_a_mesh(monkeypatch):
    monkeypatch.setattr(meshlab, "available", lambda: True)
    ball = sphere(1.0, divisions=8)

    def crashed(*args, **kwargs):
        return subprocess.CompletedProcess(args, 139, b"", b"first line\nSegmentation fault\n")

    monkeypatch.setattr(subprocess, "run", crashed)
    with pytest.raises(meshlab.MeshlabError, match="exit 139.*Segmentation fault"):
        meshlab.remesh(ball, "qem")

    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, b"not an npz", b""))
    with pytest.raises(meshlab.MeshlabError, match="returned no mesh"):
        meshlab.remesh(ball, "qem")

    def hung(*args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hung)
    with pytest.raises(meshlab.MeshlabError, match="did not finish in 5 s"):
        meshlab.remesh(ball, "qem", timeout_s=5)


def _edges(mesh):
    v, f = mesh.vertices, mesh.faces
    return np.concatenate([np.linalg.norm(v[f[:, i]] - v[f[:, (i + 1) % 3]], axis=1)
                           for i in range(3)])


@needs_meshlab
def test_isotropic_gives_uniform_edges_on_the_same_surface():
    ball = sphere(2.0, divisions=64)
    out = meshlab.remesh(ball, "isotropic", edge_um=0.25)
    edges = _edges(out)
    assert np.median(edges) == pytest.approx(0.25, rel=0.2)
    assert np.percentile(edges, 95) / np.percentile(edges, 5) < 2.2      # uniform
    assert len(out.faces) < len(ball.faces) / 3
    assert out.volume() == pytest.approx(ball.volume(), rel=0.02)
    np.testing.assert_allclose(np.linalg.norm(out.vertices, axis=1), 2.0, atol=0.03)


@needs_meshlab
def test_qem_keeps_the_asked_share_of_the_faces_and_poisson_the_shape():
    ball = sphere(2.0, divisions=64)
    quarter = meshlab.remesh(ball, "qem", fraction=0.25)
    assert len(quarter.faces) == pytest.approx(len(ball.faces) * 0.25, rel=0.02)
    assert quarter.volume() == pytest.approx(ball.volume(), rel=0.01)
    assert quarter.volume() > 0                                             # outward winding
    fitted = meshlab.remesh(ball, "poisson", depth=7)
    assert fitted.volume() == pytest.approx(ball.volume(), rel=0.01)


@needs_meshlab
def test_the_render_option_remeshes_after_the_technique(c943):
    """Through the service: the same cell, the same technique, MeshLab last --
    and a different cache entry from the plain render."""
    from sbfsem.service.facade import SbfsemService

    svc = SbfsemService(offline=True)
    svc.add_neuron(c943)
    plain = svc.mesh_object(c943.id, c943.source, smooth_iterations=0)
    remeshed = svc.mesh_object(c943.id, c943.source, smooth_iterations=0,
                               meshlab="qem", meshlab_fraction=0.5)
    assert remeshed is not plain
    assert len(remeshed.faces) == pytest.approx(len(plain.faces) * 0.5, rel=0.05)
    assert svc.mesh_object(c943.id, c943.source, smooth_iterations=0,
                           meshlab="qem", meshlab_fraction=0.5) is remeshed   # cached
    with pytest.raises(ValueError, match="unknown render option"):
        svc.build_mesh(c943, meshlab_mode="qem")
