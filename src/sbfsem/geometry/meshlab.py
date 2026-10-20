"""Remeshing through MeshLab, as an optional step after any render.

pymeshlab (the ``[meshlab]`` extra) gives three things this code base has no
equal of: an isotropic remesher that turns marching-cubes slivers into
uniform, well-shaped triangles; quadric-error decimation that keeps fidelity
at a quarter of the faces; and screened Poisson reconstruction, the step Becca
Pfeiffer applies to VikingMesh output before Blender.  Measured on voxel2
meshes with the disc metric (2026-09-17): c943 isotropic at 0.25 um, 147,700
-> 8,380 faces in 0.6 s, surface deviation 0.546 -> 0.578 um; QEM to 25 %,
0.546 -> 0.547 um.  The gain is triangle quality and face count, never
fidelity to the annotations -- that is set by the render technique.

pymeshlab is GPL-3, so it is never imported here.  It is an optional
dependency, looked for with ``find_spec`` (which does not load it) and run in
a child process (:mod:`sbfsem.geometry._meshlab_worker`) that receives the
mesh on stdin and returns it on stdout.  Without the extra installed,
:func:`available` is false and :func:`remesh` says how to get it.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys

import numpy as np

from .mesh import Mesh

__all__ = ["MODES", "MeshlabError", "MeshlabUnavailable", "available", "remesh"]

#: What :func:`remesh` can do.  ``none`` is the identity, so callers can pass
#: the option straight through.
MODES = ("none", "isotropic", "qem", "poisson")

def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


#: A MeshLab run that has not come back by now is killed.  It was 600 s when
#: the largest run measured took 7.5 s; on 2026-10-15, with one core and the
#: load of a busy box, isotropic took 115 CPU-seconds on c1178 and Poisson
#: 174, which is 225 to 520 s on the server.  No HTTP request waits on it
#: any more (renders and measurements are jobs), so the ceiling is generous:
#: ``SBFSEM_MESHLAB_TIMEOUT_S`` sets it.
TIMEOUT_S = _env_seconds("SBFSEM_MESHLAB_TIMEOUT_S", 1800.0)


class MeshlabError(ValueError):
    """MeshLab ran and failed, or returned nothing usable.

    A ``ValueError`` so the HTTP layer reports it as it reports every other
    render option that cannot be honored: a 400 with the message.
    """


class MeshlabUnavailable(MeshlabError):
    """pymeshlab is not installed in this environment."""


def available() -> bool:
    """Whether pymeshlab can be found -- without importing it."""
    try:
        return importlib.util.find_spec("pymeshlab") is not None
    except (ImportError, ValueError):
        return False


def remesh(
    mesh: Mesh,
    mode: str = "none",
    *,
    edge_um: float = 0.25,
    fraction: float = 0.25,
    depth: int = 9,
    timeout_s: float = TIMEOUT_S,
) -> Mesh:
    """``mesh`` remeshed by MeshLab in a child process.

    ``isotropic``: uniform triangles with edges about ``edge_um`` long, kept
    within a tenth of that of the input surface.  ``qem``: quadric edge
    collapse down to ``fraction`` of the faces, topology and normals
    preserved.  ``poisson``: screened Poisson reconstruction at octree
    ``depth`` -- a new watertight surface fitted to the vertices and normals,
    which rounds sharp features and closes holes; the only mode that moves
    the surface by more than a few hundredths of a micron.  Per-vertex data
    does not survive any of them.
    """
    if mode not in MODES:
        raise ValueError(f"meshlab mode must be one of {MODES}; got {mode!r}")
    if mode == "none" or mesh.is_empty:
        return mesh
    if not available():
        raise MeshlabUnavailable(
            "MeshLab remeshing needs the optional pymeshlab package: "
            "pip install 'sbfsem[meshlab]' (GPL-3, about 300 MB installed)"
        )
    if mode == "isotropic" and not edge_um > 0:
        raise ValueError("edge_um must be positive")
    if mode == "qem" and not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    job = {"mode": mode, "edge_um": float(edge_um), "fraction": float(fraction),
           "depth": int(depth)}
    payload = io.BytesIO()
    np.savez(payload, vertices=np.asarray(mesh.vertices, dtype=np.float64),
             faces=np.asarray(mesh.faces, dtype=np.int64), job=np.array(json.dumps(job)))
    try:
        done = subprocess.run(
            [sys.executable, "-m", "sbfsem.geometry._meshlab_worker"],
            input=payload.getvalue(), capture_output=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MeshlabError(f"MeshLab {mode} did not finish in {timeout_s:.0f} s") from exc
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise MeshlabError(f"MeshLab {mode} failed (exit {done.returncode}): " + " | ".join(tail))
    try:
        with np.load(io.BytesIO(done.stdout), allow_pickle=False) as data:
            vertices, faces = data["vertices"], data["faces"]
    except Exception as exc:  # noqa: BLE001 - whatever came back was not a mesh
        raise MeshlabError(f"MeshLab {mode} returned no mesh") from exc
    if not len(faces):
        raise MeshlabError(f"MeshLab {mode} returned an empty mesh")
    return Mesh(vertices, faces).fix_winding()
