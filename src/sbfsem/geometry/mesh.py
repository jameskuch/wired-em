"""A minimal triangle mesh type and the operations the toolbox performs on it.

Replaces the MATLAB ``FV`` struct (``faces``/``vertices``) and the matGeom
helpers ``concatenateMeshes``, ``smoothMesh`` and ``trimMesh``, plus MATLAB's
``reducepatch``.

The MATLAB versions of ``clipMesh`` and friends accepted either an ``FV`` struct
or a live graphics handle and mutated the handle in place.  Here they take and
return meshes; a frontend applies the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

__all__ = ["Mesh", "concatenate", "EMPTY_MESH"]


@dataclass
class Mesh:
    """Triangles only: ``vertices`` is ``(N, 3)``, ``faces`` is ``(M, 3)`` int."""

    vertices: np.ndarray
    faces: np.ndarray
    #: Optional per-vertex scalar used for color mapping (IPL depth, strata).
    vertex_data: np.ndarray | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=float).reshape(-1, 3)
        self.faces = np.asarray(self.faces, dtype=np.int64).reshape(-1, 3)
        if self.vertex_data is not None:
            self.vertex_data = np.asarray(self.vertex_data, dtype=float).ravel()

    # -- basics ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.faces)

    @property
    def is_empty(self) -> bool:
        return self.vertices.size == 0 or self.faces.size == 0

    @property
    def n_vertices(self) -> int:
        return len(self.vertices)

    @property
    def bounds(self) -> np.ndarray:
        """``(2, 3)`` array of ``[min; max]``."""
        if self.is_empty:
            return np.zeros((2, 3))
        return np.vstack([self.vertices.min(axis=0), self.vertices.max(axis=0)])

    @property
    def bounding_box(self) -> np.ndarray:
        """``[xmin, xmax, ymin, ymax, zmin, zmax]``, as ``getBoundingBox``."""
        b = self.bounds
        return np.array([b[0, 0], b[1, 0], b[0, 1], b[1, 1], b[0, 2], b[1, 2]])

    def copy(self) -> Mesh:
        return Mesh(
            self.vertices.copy(),
            self.faces.copy(),
            None if self.vertex_data is None else self.vertex_data.copy(),
            dict(self.meta),
        )

    # -- geometry ----------------------------------------------------------
    def face_normals(self) -> np.ndarray:
        v = self.vertices[self.faces]
        n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        lengths = np.linalg.norm(n, axis=1, keepdims=True)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(lengths > 0, n / lengths, 0.0)

    def face_areas(self) -> np.ndarray:
        v = self.vertices[self.faces]
        return 0.5 * np.linalg.norm(
            np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1
        )

    def surface_area(self) -> float:
        return float(self.face_areas().sum())

    def volume(self) -> float:
        """Signed volume by the divergence theorem; absolute value returned."""
        if self.is_empty:
            return 0.0
        v = self.vertices[self.faces]
        return float(abs(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6.0))

    # -- cleanup -----------------------------------------------------------
    def drop_nan(self) -> Mesh:
        """Remove vertices that are not finite, and any face touching them.

        Coincident annotations made the MATLAB cylinder mesher divide by zero and
        emit NaN vertices, which were only reported as a printed count (F081).
        """
        good = np.isfinite(self.vertices).all(axis=1)
        if good.all():
            return self
        return self._select_vertices(good)

    def remove_unreferenced(self) -> Mesh:
        used = np.zeros(len(self.vertices), dtype=bool)
        used[np.unique(self.faces)] = True
        return self._select_vertices(used)

    def merge_duplicate_vertices(self, tol: float = 1e-9) -> Mesh:
        if self.is_empty:
            return self
        quantised = np.round(self.vertices / max(tol, 1e-12)).astype(np.int64)
        _, first, inverse = np.unique(quantised, axis=0, return_index=True, return_inverse=True)
        order = np.argsort(first)
        remap = np.empty(len(first), dtype=np.int64)
        remap[order] = np.arange(len(first))
        vertices = self.vertices[first[order]]
        faces = remap[inverse[self.faces]]
        keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
        data = None
        if self.vertex_data is not None:
            data = self.vertex_data[first[order]]
        return Mesh(vertices, faces[keep], data, dict(self.meta))

    def _select_vertices(self, keep: np.ndarray) -> Mesh:
        remap = -np.ones(len(self.vertices), dtype=np.int64)
        remap[keep] = np.arange(int(keep.sum()))
        faces = self.faces[keep[self.faces].all(axis=1)]
        data = None if self.vertex_data is None else self.vertex_data[keep]
        return Mesh(self.vertices[keep], remap[faces], data, dict(self.meta))

    # -- processing --------------------------------------------------------
    def smooth(self, iterations: int = 1) -> Mesh:
        """Laplacian smoothing over the vertex adjacency, as ``smoothMesh``."""
        if iterations <= 0 or self.is_empty:
            return self
        neighbor_mean = self._neighbor_mean()
        vertices = self.vertices
        for _ in range(int(iterations)):
            vertices = neighbor_mean(vertices)
        return Mesh(np.asarray(vertices), self.faces.copy(),
                    None if self.vertex_data is None else self.vertex_data.copy(),
                    dict(self.meta))

    def taubin(self, iterations: int = 2, lam: float = 0.5, mu: float = -0.53) -> Mesh:
        """Taubin (lambda/mu) smoothing: smooths without collapsing the mesh.

        :meth:`smooth` is matGeom's ``smoothMesh`` -- one full-weight Laplacian
        pass per iteration.  Every pass pulls each vertex all the way to the
        average of its neighbours, which on these tube meshes discards 37% of
        the volume on the first pass and 96% by the tenth.

        Taubin alternates a positive step (``lam``, shrinking) with a slightly
        larger negative one (``mu``, re-expanding).  The pair cancels the
        low-frequency shrinkage while still attenuating high-frequency facets,
        so the same neuron keeps 99% of its volume after one iteration and 94%
        after five.  Defaults are Taubin's own recommendation; the requirement
        is ``mu < -lam`` so the passes do not sum to a net contraction.
        """
        if iterations <= 0 or self.is_empty:
            return self
        if not -1.0 < mu < -lam < 1.0:
            raise ValueError("Taubin requires -1 < mu < -lam < 1")
        neighbor_mean = self._neighbor_mean()
        vertices = self.vertices
        for _ in range(int(iterations)):
            vertices = vertices + lam * (neighbor_mean(vertices) - vertices)
            vertices = vertices + mu * (neighbor_mean(vertices) - vertices)
        return Mesh(np.asarray(vertices), self.faces.copy(),
                    None if self.vertex_data is None else self.vertex_data.copy(),
                    dict(self.meta))

    def smooth_inflate(self, iterations: int = 15, smooth: float = 0.5,
                       inflate: float = 0.5) -> Mesh:
        """Laplacian smoothing with an inflate step, as a voxel-mesh optimizer
        does it: each iteration moves every vertex ``smooth`` of the way to
        its neighbor mean, then ``inflate`` of the way back out from the new
        mean.  Equal values leave a fine mesh's volume almost where it was
        while ironing the half-voxel staircase of a binarized contour;
        ``inflate`` 0 is plain Laplacian shrinkage.  Unlike :meth:`taubin`
        the pair is not required to expand on net."""
        if iterations <= 0 or self.is_empty:
            return self
        neighbor_mean = self._neighbor_mean()
        vertices = self.vertices
        for _ in range(int(iterations)):
            vertices = vertices + float(smooth) * (neighbor_mean(vertices) - vertices)
            if inflate:
                vertices = vertices - float(inflate) * (neighbor_mean(vertices) - vertices)
        return Mesh(np.asarray(vertices), self.faces.copy(),
                    None if self.vertex_data is None else self.vertex_data.copy(),
                    dict(self.meta))

    def _directed_edges(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The vertex adjacency as a directed edge list (every undirected edge
        once each way): ``rows``, ``cols`` and ``1 / (neighbors + 1)`` per
        vertex, the weight of the smoothers' mean (the vertex itself counts)."""
        n = len(self.vertices)
        f = self.faces.astype(np.int64, copy=False)
        a = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
        b = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
        packed = np.unique(np.minimum(a, b) * n + np.maximum(a, b))   # undirected, once
        del a, b
        lo = (packed // n).astype(np.int32)
        hi = (packed % n).astype(np.int32)
        del packed
        keep = lo != hi
        lo, hi = lo[keep], hi[keep]
        rows = np.concatenate([lo, hi])
        cols = np.concatenate([hi, lo])
        del lo, hi
        degree = np.bincount(rows, minlength=n).astype(float) + 1.0   # + self
        return rows, cols, 1.0 / degree

    #: Vertices above which :meth:`taubin_weighted` takes the mesh a slab at a
    #: time, and what a slab and its margins may hold.  Its adjacency, the matrix
    #: of the edges among the vertices still smoothing and their copies come to
    #: some 500 bytes a vertex (Inferior c1178 at 0.1 um: 553,000 vertices, 370
    #: MB over the mesh), which a mesh of five million (RPC1 c2628 at 0.025 um)
    #: would pay in full.
    TAUBIN_SLAB_VERTICES = 1_500_000

    def taubin_weighted(self, passes, lam: float = 0.5, mu: float = -0.53, *,
                        slab_vertices: int | None = None) -> Mesh:
        """:meth:`taubin`, with its own number of passes for each vertex.

        ``passes`` is one integer per vertex: vertex ``i`` takes part in the
        first ``passes[i]`` lambda/mu pairs and is left where it is for the
        rest, so a vertex with 0 never moves and one with ``k`` ends where
        ``taubin(k)`` would put it were it alone.  A vertex that has stopped
        still counts as a neighbor, at the place it stopped.  With every
        count equal to ``k`` the result is :meth:`taubin`'s, vertex for vertex.

        The vertices still smoothing in a stage are a part of the stage
        before's, so the matrix of the edges among them is cut down from that
        stage's rather than from the whole mesh's, which is let go after the
        first; and the neighbors that have stopped are summed once for the
        passes between two counts (they do not move meanwhile).  The work is
        the sum of the counts times the edges among the vertices they belong
        to, not the largest count times the whole mesh.

        A mesh of more than ``slab_vertices`` vertices (default
        :attr:`TAUBIN_SLAB_VERTICES`) is smoothed a slab of it at a time, along
        its longest axis, each with a margin of the neighbors a vertex's last
        pass can reach: the vertices a slab owns come out as they would from
        the whole mesh, and the memory is a slab's, not the mesh's.
        """
        counts = np.asarray(passes, dtype=np.int64).ravel()
        if counts.shape != (len(self.vertices),):
            raise ValueError("passes needs one count for each vertex")
        if self.is_empty or not (counts > 0).any():
            return self
        if not -1.0 < mu < -lam < 1.0:
            raise ValueError("Taubin requires -1 < mu < -lam < 1")
        limit = self.TAUBIN_SLAB_VERTICES if slab_vertices is None else int(slab_vertices)
        if len(self.vertices) > limit:
            vertices = self._taubin_in_slabs(counts, lam, mu, limit)
        else:
            vertices = self._taubin_whole(counts, lam, mu)
        return Mesh(vertices, self.faces.copy(),
                    None if self.vertex_data is None else self.vertex_data.copy(),
                    dict(self.meta))

    def _taubin_whole(self, counts: np.ndarray, lam: float, mu: float) -> np.ndarray:
        """The vertices after :meth:`taubin_weighted`'s passes, the mesh at once."""
        from scipy.sparse import csr_matrix

        n = len(self.vertices)
        rows, cols, inv_degree = self._directed_edges()
        order = np.argsort(rows, kind="stable")
        indptr = np.concatenate([[0], np.cumsum(np.bincount(rows, minlength=n))])
        adjacency = csr_matrix((np.ones(len(rows)), cols[order], indptr), shape=(n, n))
        del rows, cols, order
        vertices = self.vertices.copy()
        levels = np.unique(counts[counts > 0])
        active = np.flatnonzero(counts >= levels[0])
        if len(active) == n:
            among, stopped = adjacency, np.zeros((n, 3))
        else:
            # What the vertices that are not smoothing add to each one's neighbor
            # sum: they stand where they are.
            held = vertices.copy()
            held[active] = 0.0
            part = adjacency[active]
            stopped = part @ held
            del held
            among = part[:, active]
            del part
        del adjacency
        here = vertices[active]
        done = 0
        for stage, level in enumerate(levels):
            if stage:
                # The vertices whose count was the last level stop here, where they stand.
                keep = np.flatnonzero(counts[active] >= level)
                leaving = np.flatnonzero(counts[active] < level)
                kept = among[keep]
                stopped = stopped[keep] + kept[:, leaving] @ here[leaving]
                vertices[active[leaving]] = here[leaving]
                among = kept[:, keep]
                del kept
                here, active = here[keep], active[keep]
            weight = inv_degree[active][:, None]
            for _ in range(int(level) - done):
                for step in (lam, mu):
                    mean = (among @ here + stopped + here) * weight
                    here += step * (mean - here)
            done = int(level)
        vertices[active] = here
        return vertices

    def _taubin_in_slabs(self, counts: np.ndarray, lam: float, mu: float, limit: int) -> np.ndarray:
        """:meth:`_taubin_whole` a slab of the mesh at a time.

        The vertices are cut into runs along the mesh's longest axis; a run is
        smoothed with every vertex within ``(2 * passes + 2)`` of the longest
        edge beyond its ends, and its own come out as the whole mesh's would.
        Each step moves what it knows one edge on and a pass is two steps, so
        what is wrong at a slab's outer face (its vertices there lack
        neighbors) has not reached a vertex the slab owns.  A run is as long
        as keeps it and its margins within ``limit`` vertices; a mesh whose
        margins would be most of what it cuts (a thin tree, many passes) gets
        a larger limit rather than slabs that are all margin.
        """
        vertices, faces = self.vertices, self.faces
        n = len(vertices)
        longest = 0.0
        for start in range(0, len(faces), 200_000):
            part = vertices[faces[start:start + 200_000]]
            longest = max(longest, *(float(np.linalg.norm(part[:, i] - part[:, (i + 1) % 3], axis=1).max())
                                     for i in range(3)))
        reach = (2 * int(counts.max()) + 2) * longest
        axis = int(np.argmax(np.ptp(vertices, axis=0)))
        coordinate = vertices[:, axis]
        low_end, extent = float(coordinate.min()), float(np.ptp(coordinate))
        bins = int(min(max(extent / max(reach / 8.0, 1e-9), 1), 4096))
        width = extent / bins
        margin = int(np.ceil(reach / width)) + 1
        bin_of = np.minimum(((coordinate - low_end) / width).astype(np.int64), bins - 1)
        cumulative = np.concatenate([[0], np.cumsum(np.bincount(bin_of, minlength=bins))])
        cuts: list[tuple[int, int]] = []
        for allowed in (limit, 2 * limit, 4 * limit, 8 * limit):
            cuts, first, work = [], 0, 0
            while first < bins:
                # The furthest end whose slab and margins hold no more than `allowed`.
                end = first + 1
                while end < bins and (cumulative[min(end + 1 + margin, bins)]
                                      - cumulative[max(first - margin, 0)]) <= allowed:
                    end += 1
                cuts.append((first, end))
                work += int(cumulative[min(end + margin, bins)] - cumulative[max(first - margin, 0)])
                first = end
            if len(cuts) == 1 or work <= 2.5 * n:
                break
        if len(cuts) == 1:
            return self._taubin_whole(counts, lam, mu)
        out = vertices.copy()
        for first, end in cuts:
            owned = (bin_of >= first) & (bin_of < end)
            inside = (bin_of >= first - margin) & (bin_of < end + margin)
            ids = np.flatnonzero(inside)
            if not owned.any() or not (counts[ids] > 0).any():
                continue
            renumber = np.full(n, -1, dtype=np.int64)
            renumber[ids] = np.arange(len(ids))
            sub_faces = renumber[faces[inside[faces].all(axis=1)]]
            del renumber
            smoothed = Mesh(vertices[ids], sub_faces)._taubin_whole(counts[ids], lam, mu)
            mine = owned[ids]
            out[ids[mine]] = smoothed[mine]
        return out

    def _neighbor_mean(self):
        """The smoothers' operator: each vertex to the mean of itself and its
        distinct neighbors (the row-normalized adjacency with self loops).

        Applied through ``bincount`` over the directed edge list rather than a
        sparse matrix.  Same weights -- one over the number of distinct
        neighbors plus one -- but the matrix route (a COO with duplicates,
        summed into CSR) peaked at half a gigabyte on a two-million-face
        sheet, and this needs the edge list alone.
        """
        n = len(self.vertices)
        rows, cols, inv_degree = self._directed_edges()

        def apply(v: np.ndarray) -> np.ndarray:
            v = np.asarray(v, dtype=float)
            out = np.empty_like(v)
            for c in range(v.shape[1]):
                acc = np.bincount(rows, weights=v[cols, c], minlength=n)
                out[:, c] = (acc + v[:, c]) * inv_degree
            return out

        return apply

    def decimate(self, fraction: float) -> Mesh:
        """Reduce to ``fraction`` of the faces, as ``reducepatch``.

        ``fraction`` of 1 returns the mesh unchanged, matching the MATLAB
        ``reduceFac`` convention (which its own validator rejected, F053).
        """
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        if fraction == 1 or self.is_empty:
            return self
        import trimesh

        tm = trimesh.Trimesh(vertices=self.vertices, faces=self.faces, process=False)
        target = max(4, int(round(len(self.faces) * fraction)))
        try:
            reduced = tm.simplify_quadric_decimation(face_count=target)
        except Exception as exc:  # noqa: BLE001 - backend may be absent or fail
            # Returning the mesh unreduced is the only sensible fallback, but it
            # must not be silent: the previous version swallowed a missing
            # `fast_simplification` and the Decimate control did nothing at all,
            # with no error anywhere to say so.
            _warn_decimation_unavailable(exc)
            return self
        return Mesh(np.asarray(reduced.vertices), np.asarray(reduced.faces), None, dict(self.meta))

    def flip_faces(self) -> Mesh:
        """Reverse the winding of every face, flipping the normals."""
        out = self.copy()
        out.faces = out.faces[:, ::-1].copy()
        return out

    def fix_winding(self) -> Mesh:
        """Orient faces outward.

        The Line3 ring triangulation winds inward, so lighting and glTF
        back-face culling see the inside of every tube.  A negative signed
        volume means the whole mesh is inverted.
        """
        if self.is_empty:
            return self
        signed = 0.0
        # Chunked: the (faces, 3, 3) gather is nine floats per face, which on
        # a two-million-face sheet is a 400 MB temporary for one number.
        for start in range(0, len(self.faces), 250_000):
            v = self.vertices[self.faces[start:start + 250_000]]
            signed += float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum())
        return self.flip_faces() if signed / 6.0 < 0 else self

    def to_trimesh(self):
        import trimesh

        return trimesh.Trimesh(vertices=self.vertices, faces=self.faces, process=False)

    @classmethod
    def from_trimesh(cls, tm) -> Mesh:
        return cls(np.asarray(tm.vertices), np.asarray(tm.faces))

    def translate(self, offset) -> Mesh:
        out = self.copy()
        out.vertices = out.vertices + np.asarray(offset, dtype=float).reshape(1, 3)
        return out

    def scale(self, factor) -> Mesh:
        out = self.copy()
        out.vertices = out.vertices * np.asarray(factor, dtype=float).reshape(1, -1)
        return out


EMPTY_MESH = Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64))


def concatenate(meshes) -> Mesh:
    """Merge meshes, offsetting face indices.

    ``concatenateMeshes`` asserted on fewer than two inputs, so a neuron with one
    surviving segment errored out (F080); the COLLADA scene exporter concatenated
    faces without offsetting vertex indices at all (F040).
    """
    meshes = [m for m in meshes if m is not None and not m.is_empty]
    if not meshes:
        return EMPTY_MESH
    if len(meshes) == 1:
        return meshes[0].copy()
    vertices, faces, data = [], [], []
    offset = 0
    has_data = all(m.vertex_data is not None for m in meshes)
    for mesh in meshes:
        vertices.append(mesh.vertices)
        faces.append(mesh.faces + offset)
        if has_data:
            data.append(mesh.vertex_data)
        offset += len(mesh.vertices)
    return Mesh(
        np.vstack(vertices),
        np.vstack(faces),
        np.concatenate(data) if has_data else None,
    )


#: Set once the fallback has been reported, so a per-neuron render loop does not
#: repeat the same warning for every segment.
_decimation_warned = False


def _warn_decimation_unavailable(exc: BaseException) -> None:
    """Warn once per process that decimation fell back to the full mesh."""
    global _decimation_warned
    if _decimation_warned:
        return
    _decimation_warned = True
    log.warning(
        "mesh decimation unavailable (%s: %s) -- returning the full-resolution "
        "mesh. Install the 'fast-simplification' package to enable it.",
        type(exc).__name__, exc,
    )
