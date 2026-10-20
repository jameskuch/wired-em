"""The half of :mod:`sbfsem.geometry.meshlab` that touches pymeshlab.

Run as ``python -m sbfsem.geometry._meshlab_worker`` by the adapter, never
imported by it: pymeshlab is GPL-3, and this process is the only place it is
loaded.  A job arrives on stdin as an ``.npz`` (``vertices``, ``faces``,
``job`` -- a JSON string) and the result leaves on stdout the same way, so
nothing needs a writable directory (the service runs ``ProtectSystem=strict``).
One ``MeshSet`` per process: a MeshSet is not thread-safe, and a process per
job is what keeps a crash in MeshLab's C++ out of the server.
"""

from __future__ import annotations

import io
import json
import sys

import numpy as np


def run(vertices: np.ndarray, faces: np.ndarray, job: dict) -> tuple[np.ndarray, np.ndarray]:
    import pymeshlab

    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=np.ascontiguousarray(vertices, dtype=np.float64),
                               face_matrix=np.ascontiguousarray(faces, dtype=np.int32)))
    # Marching cubes and piece assembly both leave coincident vertices, and the
    # remeshers want one connected sheet where there is one.
    ms.meshing_remove_duplicate_vertices()
    ms.meshing_remove_unreferenced_vertices()
    mode = job["mode"]
    if mode == "isotropic":
        ms.meshing_isotropic_explicit_remeshing(
            targetlen=pymeshlab.PureValue(float(job["edge_um"])),
            iterations=int(job.get("iterations", 3)),
            adaptive=False,
            # How far the new surface may stray from the old: a tenth of an edge.
            maxsurfdist=pymeshlab.PureValue(float(job["edge_um"]) * 0.1),
            checksurfdist=True,
        )
    elif mode == "qem":
        ms.meshing_decimation_quadric_edge_collapse(
            targetfacenum=max(4, int(round(len(faces) * float(job["fraction"])))),
            preservenormal=True, preservetopology=True, preserveboundary=True,
            optimalplacement=True, planarquadric=True, autoclean=True,
        )
    elif mode == "poisson":
        ms.compute_normal_per_vertex()
        ms.generate_surface_reconstruction_screened_poisson(
            depth=int(job["depth"]), preclean=True)
        # The reconstruction is a new mesh in the set, and the current one.
    else:
        raise ValueError(f"unknown MeshLab mode {mode!r}")
    ms.meshing_remove_unreferenced_vertices()
    out = ms.current_mesh()
    return out.vertex_matrix(), out.face_matrix()


def main() -> int:
    with np.load(io.BytesIO(sys.stdin.buffer.read()), allow_pickle=False) as data:
        vertices, faces = data["vertices"], data["faces"]
        job = json.loads(str(data["job"]))
    new_vertices, new_faces = run(vertices, faces, job)
    buffer = io.BytesIO()
    np.savez(buffer, vertices=np.asarray(new_vertices, dtype=np.float64),
             faces=np.asarray(new_faces, dtype=np.int64))
    sys.stdout.buffer.write(buffer.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(main())
