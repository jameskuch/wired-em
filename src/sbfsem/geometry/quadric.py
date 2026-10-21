"""Quadric decimation in a process of its own, so its working copy goes when it ends.

``fast_simplification`` (0.2.0) copies a mesh into global ``std::vector``\\ s
(``Simplify::triangles``, ``vertices``, ``refs``) that it clears before each
call and never shrinks, so whatever they grew to stays in the process after
it returns: in a build worker, until the worker is retired.  On the server a
capped Voxel remesh of Inferior c1178 (6.9 million raw triangles decimated a
million at a time) left its build process holding 452 MB where it had held
168, and the same build again peaked 1,288 MB where the first had peaked
1,092; a one-pass decimation of 2.1 million triangles then raised what it
held from 453 to 760 MB.

:class:`Decimator` runs :func:`fast_simplification.simplify` in a child
process started for one decimation (several calls, such as a sheet's chunks,
go to the same child) and ended with it.  The child runs this file by path,
which imports numpy and ``fast_simplification`` and nothing of the package,
with the same interpreter and the same library, so the result is the same to
the bit.  Starting it (0.23 s and 32 MB on the server) and faulting in fresh
pages cost 0.3 s on the default build's 842,000 triangles and 0.2 s on the
capped one's 6.9 million (2026-10-20).  A mesh under ``PROCESS_FACES``
triangles is decimated here: its copy is small and the child's start costs
more.  When the child cannot be started or dies, the call is made here
instead.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import numpy as np

__all__ = ["PROCESS_FACES", "Decimator"]

log = logging.getLogger(__name__)

#: Triangles from which a decimation runs in a child process.
PROCESS_FACES = 200_000

#: Triangles sent to the child at a time when their indices are narrowed to int32.
_SEND_ROWS = 1 << 20


def _read_into(stream, array: np.ndarray) -> bool:
    """Fill ``array`` from ``stream``; ``False`` at the end of the stream before its first byte."""
    view = memoryview(array).cast("B")
    done = 0
    while done < len(view):
        got = stream.readinto(view[done:])
        if not got:
            if done == 0:
                return False
            raise EOFError("the stream ended part way")
        done += got
    return True


class _ChildFailed(Exception):
    """The child process could not be started, or stopped answering."""


class Decimator:
    """:func:`fast_simplification.simplify` in a child process that ends with this object.

    Use it as a context manager around one decimation's calls.  ``min_faces``
    (``PROCESS_FACES``) is the size of mesh from which a call goes to the
    child.
    """

    def __init__(self, min_faces: int | None = None) -> None:
        self._min_faces = PROCESS_FACES if min_faces is None else int(min_faces)
        self._proc: subprocess.Popen | None = None
        self._broken = False

    def __enter__(self) -> Decimator:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def simplify(self, points: np.ndarray, faces: np.ndarray, target_count: int, *,
                 preserve_border: bool = False, agg: float = 7.0) -> tuple[np.ndarray, np.ndarray]:
        """``fast_simplification.simplify(points, faces, target_count=..., agg=...,
        preserve_border=...)``: the points (float64) and triangles (int32) it returns."""
        points = np.ascontiguousarray(points, dtype=np.float64)
        faces = np.ascontiguousarray(faces)
        if faces.dtype != np.int32 and faces.dtype != np.int64:
            faces = faces.astype(np.int64)
        # Once the child runs, a smaller mesh (a sheet's last chunk) goes to it too.
        if (self._proc is not None or len(faces) >= self._min_faces) and not self._broken:
            try:
                return self._in_child(points, faces, int(target_count), preserve_border, float(agg))
            except _ChildFailed as exc:
                log.warning("decimating in this process (%s)", exc)
                self._broken = True
                self.close()
        import fast_simplification

        return fast_simplification.simplify(points, faces, target_count=int(target_count), agg=float(agg),
                                            preserve_border=preserve_border)

    def close(self) -> None:
        """End the child, and with it its copy of the last mesh."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()
        finally:
            if proc.stdout is not None:
                proc.stdout.close()

    def _start(self) -> subprocess.Popen:
        if not sys.executable:
            raise _ChildFailed("no interpreter to start")
        try:
            # -P: the script's own folder is not put on the path, so numpy and
            # fast_simplification are the ones this process imported.
            proc = subprocess.Popen([sys.executable, "-P", os.path.abspath(__file__)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except OSError as exc:
            raise _ChildFailed(f"the child could not be started: {exc}") from exc
        ready = np.empty(1, dtype=np.int64)
        try:
            ok = _read_into(proc.stdout, ready)
        except (OSError, EOFError):
            ok = False
        if not ok or ready[0] != 0:
            proc.kill()
            proc.wait()
            raise _ChildFailed("the child did not start")
        return proc

    def _in_child(self, points: np.ndarray, faces: np.ndarray, target: int,
                  preserve_border: bool, agg: float) -> tuple[np.ndarray, np.ndarray]:
        if self._proc is None:
            self._proc = self._start()
        proc = self._proc
        # int64 indices go as int32, a block at a time: the library reads either
        # into an int, and the child's copy is half the size.
        narrow = faces.dtype == np.int64 and len(points) < 2**31
        head = np.array([len(points), len(faces), target, int(preserve_border),
                         4 if narrow else faces.itemsize, 0], dtype=np.int64)
        head[5:].view(np.float64)[0] = agg
        reply = np.empty(3, dtype=np.int64)
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(head.tobytes())
            proc.stdin.write(memoryview(points).cast("B"))
            if narrow:
                for start in range(0, len(faces), _SEND_ROWS):
                    proc.stdin.write(memoryview(faces[start:start + _SEND_ROWS].astype(np.int32)).cast("B"))
            else:
                proc.stdin.write(memoryview(faces).cast("B"))
            proc.stdin.flush()
            if not _read_into(proc.stdout, reply):
                raise EOFError("no reply")
            status, a, b = (int(x) for x in reply)
            if status != 0:
                text = np.empty(a, dtype=np.uint8)
                _read_into(proc.stdout, text)
                raise RuntimeError(text.tobytes().decode(errors="replace"))
            out_points = np.empty((a, 3), dtype=np.float64)
            out_faces = np.empty((b, 3), dtype=np.int32)
            if not (_read_into(proc.stdout, out_points) and _read_into(proc.stdout, out_faces)):
                raise EOFError("the reply ended part way")
        except (OSError, EOFError, ValueError) as exc:
            raise _ChildFailed(f"the child stopped: {exc}") from exc
        return out_points, out_faces


def _child_main() -> None:
    """The child: answer requests on stdin until it ends."""
    import fast_simplification

    requests = sys.stdin.buffer
    replies = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # whatever else is printed goes to stderr, not into the replies
    replies.write(np.zeros(1, dtype=np.int64).tobytes())
    replies.flush()
    head = np.empty(6, dtype=np.int64)
    while _read_into(requests, head):
        n_points, n_faces, target, border, index_bytes = (int(x) for x in head[:5])
        agg = float(head[5:].view(np.float64)[0])
        points = np.empty((n_points, 3), dtype=np.float64)
        faces = np.empty((n_faces, 3), dtype=np.int32 if index_bytes == 4 else np.int64)
        if not (_read_into(requests, points) and _read_into(requests, faces)):
            raise EOFError("the request ended part way")
        try:
            out_points, out_faces = fast_simplification.simplify(
                points, faces, target_count=target, agg=agg, preserve_border=bool(border))
        except Exception as exc:  # noqa: BLE001 - every failure goes back to the caller
            text = f"{type(exc).__name__}: {exc}".encode()
            replies.write(np.array([1, len(text), 0], dtype=np.int64).tobytes())
            replies.write(text)
            replies.flush()
            continue
        del points, faces
        out_points = np.ascontiguousarray(out_points, dtype=np.float64)
        out_faces = np.ascontiguousarray(out_faces, dtype=np.int32)
        replies.write(np.array([0, len(out_points), len(out_faces)], dtype=np.int64).tobytes())
        replies.write(memoryview(out_points).cast("B"))
        replies.write(memoryview(out_faces).cast("B"))
        replies.flush()
        del out_points, out_faces


if __name__ == "__main__":
    _child_main()
