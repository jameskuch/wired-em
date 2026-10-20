"""Built meshes kept on disk, so a render survives the process that built it.

The in-memory cache in the facade holds a dozen meshes; a restart, or the
thirteenth cell of a batch, throws the rest away and a session restore then
rebuilds every cell.  This layer keeps each build as one ``.npz`` keyed by the
cell, its render options and the ``data_stamp`` of the annotations it was
built from, so a cell whose annotations have not changed in Viking never
builds twice, and one that has changed gets a new entry rather than a stale
hit.  Old entries are pruned least-recently-used once the directory passes
its budget.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..geometry.mesh import Mesh

log = logging.getLogger(__name__)

__all__ = ["MeshDiskCache"]


def _env_mb(name: str, default: int) -> int:
    try:
        return max(64, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


#: Disk budget for cached renders.  A typical bipolar cell is well under a
#: megabyte; a 1.4M-face vessel volume is ~25 MB.
DEFAULT_BUDGET_MB = _env_mb("SBFSEM_MESH_CACHE_MB", 4096)


class MeshDiskCache:
    """One ``.npz`` per (cell, options, data stamp) under a confined directory."""

    def __init__(self, directory: str | os.PathLike[str], budget_mb: float | None = None):
        self.directory = Path(directory)
        self.budget_bytes = int((budget_mb or DEFAULT_BUDGET_MB) * 1024 * 1024)
        self._lock = threading.Lock()
        self._writes_since_prune = 0

    # -- keys ----------------------------------------------------------------
    @staticmethod
    def _path_for(directory: Path, key: tuple) -> Path:
        digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()
        return directory / digest[:2] / f"{digest}.npz"

    def path(self, key: tuple) -> Path:
        return self._path_for(self.directory, key)

    # -- reading -------------------------------------------------------------
    def get(self, key: tuple) -> Mesh | None:
        """The cached mesh, or ``None`` for a miss.

        A file whose bytes are bad is removed, so the rebuilt mesh replaces it;
        a read that failed for any other reason (descriptors exhausted, an I/O
        or permission fault, a network filesystem) says nothing about the file
        and leaves it for the next try.
        """
        path = self.path(key)
        try:
            before = path.stat()
        except OSError:
            return None
        if not stat.S_ISREG(before.st_mode):
            return None
        try:
            with np.load(path, allow_pickle=False) as data:
                vertices = np.asarray(data["vertices"], dtype=float)
                faces = np.asarray(data["faces"], dtype=np.int64)
                vertex_data = (
                    np.asarray(data["vertex_data"], dtype=float)
                    if "vertex_data" in data.files else None
                )
                meta = json.loads(str(data["meta"])) if "meta" in data.files else {}
        except FileNotFoundError:
            return None                      # removed between the check and the read: a miss
        except OSError as exc:
            # EMFILE, EIO, a permission or network-filesystem fault: the file may
            # be perfectly good, and a cached surface can cost minutes to rebuild.
            log.warning("could not read cached mesh %s: %s", path.name, exc)
            return None
        except MemoryError:
            raise                            # the machine's trouble, not the file's
        except Exception as exc:  # noqa: BLE001 - whatever numpy raises on damaged bytes
            # An empty or cut-off file (a write the disk did not finish, a
            # restored backup) raises EOFError or BadZipFile; damage inside the
            # npy header raises ValueError, SyntaxError, IndexError, struct.error
            # or tokenize.TokenError, depending on the byte.  They used to
            # escape, or be listed one by one and missed, and failed that render
            # every time.  A miss instead, and gone, so the rebuilt mesh
            # replaces it -- unless another request has replaced the file since
            # it was read, in which case the replacement is not ours to remove.
            log.warning("unreadable cached mesh %s: %s: %s", path.name, type(exc).__name__, exc)
            self._remove_if_unchanged(path, before)
            return None
        # Touch it: the prune is least-recently-used by mtime, and reading a
        # cached render is a use.
        try:
            now = time.time()
            os.utime(path, (now, now))
        except OSError:
            pass
        return Mesh(vertices=vertices, faces=faces, vertex_data=vertex_data, meta=meta)

    @staticmethod
    def _remove_if_unchanged(path: Path, before: os.stat_result) -> None:
        """Remove the file that was read and found bad, not one that has replaced it."""
        try:
            now = path.stat()
            if (now.st_ino, now.st_size, now.st_mtime_ns) == (
                    before.st_ino, before.st_size, before.st_mtime_ns):
                path.unlink()
        except OSError:
            pass

    # -- writing -------------------------------------------------------------
    def put(self, key: tuple, mesh: Mesh) -> None:
        path = self.path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Its own name per writer: two requests that build one mesh (two
            # copies of a cell with the same disk key) must not write one file.
            tmp = path.with_suffix(f".npz.{os.getpid()}.{threading.get_ident()}.tmp")
            arrays: dict[str, Any] = {
                "vertices": np.asarray(mesh.vertices, dtype=np.float32),
                "faces": np.asarray(mesh.faces, dtype=np.int32),
                "meta": np.array(json.dumps(mesh.meta or {}, default=str)),
            }
            if mesh.vertex_data is not None:
                arrays["vertex_data"] = np.asarray(mesh.vertex_data, dtype=np.float32)
            try:
                with open(tmp, "wb") as fh:
                    np.savez(fh, **arrays)
                tmp.replace(path)
            except BaseException:
                tmp.unlink(missing_ok=True)  # no half-written file left to fill the disk
                raise
        except OSError as exc:  # a full disk must not fail the render
            log.warning("could not cache mesh to %s: %s", path, exc)
            return
        with self._lock:
            self._writes_since_prune += 1
            due = self._writes_since_prune >= 20
            if due:
                self._writes_since_prune = 0
        if due:
            self.prune()

    def prune(self) -> int:
        """Drop least-recently-used entries until under budget.  Returns bytes freed."""
        if not self.directory.is_dir():
            return 0
        self._sweep_temporaries()
        entries = []
        total = 0
        for path in self.directory.rglob("*.npz"):
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, path))
            total += st.st_size
        freed = 0
        if total <= self.budget_bytes:
            return 0
        for _, size, path in sorted(entries):
            if total - freed <= self.budget_bytes:
                break
            try:
                path.unlink()
                freed += size
            except OSError:
                continue
        if freed:
            log.info("pruned %.0f MB of cached meshes", freed / 1e6)
        return freed

    #: A temporary file this old belongs to a write that died (a killed process):
    #: no live write takes an hour.
    TEMPORARY_MAX_AGE_S = 3600.0

    def _sweep_temporaries(self) -> None:
        """Delete the half-written files a killed writer left; the budget never counted them."""
        cutoff = time.time() - self.TEMPORARY_MAX_AGE_S
        for path in self.directory.rglob("*.tmp"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def clear(self) -> dict:
        """Delete every cached render: the Settings tab's "clear cached renders" for this server.

        Returns what went, ``{"files": n, "bytes": b}``.  A build running now
        writes its mesh afterwards, as any build does, and a temporary file is
        a write in progress, so neither is touched.
        """
        files = 0
        total = 0
        if self.directory.is_dir():
            for path in self.directory.rglob("*.npz"):
                try:
                    size = path.stat().st_size
                    path.unlink()
                except OSError:
                    continue
                files += 1
                total += size
        if files:
            log.info("cleared %d cached meshes (%.0f MB)", files, total / 1e6)
        return {"files": files, "bytes": total}

    def stats(self) -> dict:
        files = 0
        total = 0
        if self.directory.is_dir():
            for path in self.directory.rglob("*.npz"):
                try:
                    total += path.stat().st_size
                    files += 1
                except OSError:
                    continue
        return {"files": files, "bytes": total, "budget_bytes": self.budget_bytes,
                "directory": str(self.directory)}
