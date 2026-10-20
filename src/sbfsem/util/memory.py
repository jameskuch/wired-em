"""Returning freed memory to the operating system.

The expensive steps here — the sparse solve behind the IPL boundary fit,
marching cubes over a rasterized volume — allocate hundreds of megabytes and
free it again immediately.  glibc keeps those arenas rather than returning them,
so a server that has fitted one boundary surface sits at a few hundred megabytes
of resident memory holding less than a megabyte of actual result.

That matters for a deployment: resident memory is what an instance is sized and
billed for, and what a systemd ``MemoryMax`` measures.  ``malloc_trim`` asks
glibc to give the arenas back.

This is glibc-specific.  On macOS and on musl there is no equivalent and these
functions are no-ops, so a development machine behaves as before.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import functools
import gc
import logging
import os
import platform

__all__ = ["trim_memory", "malloc_trim_available", "resident_mb"]

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _malloc_trim():
    """Resolve glibc's ``malloc_trim``, or ``None`` where it does not exist."""
    if platform.system() != "Linux":
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fn = libc.malloc_trim
    except (OSError, AttributeError):
        return None  # musl, or a libc without it
    fn.argtypes = [ctypes.c_size_t]
    fn.restype = ctypes.c_int
    return fn


def malloc_trim_available() -> bool:
    """Whether this platform can return freed arenas to the OS."""
    return _malloc_trim() is not None


def trim_memory(*, collect: bool = True) -> bool:
    """Return freed heap arenas to the OS.

    Returns ``True`` if memory was actually released.  Safe and cheap to call
    anywhere; a no-op off glibc.
    """
    if collect:
        gc.collect()
    fn = _malloc_trim()
    if fn is None:
        return False
    try:
        return bool(fn(0))
    except OSError:  # pragma: no cover - defensive
        log.debug("malloc_trim failed", exc_info=True)
        return False


def resident_mb() -> float | None:
    """This process's resident memory now, in MB (2**20 bytes).

    Read from ``/proc/self/statm`` on Linux.  Where there is none (macOS) it is
    the peak instead (``getrusage``: bytes on macOS, kilobytes elsewhere), which
    never falls; ``None`` if neither can be read (Windows).
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 2**20
    except (OSError, ValueError, IndexError):
        pass
    try:
        import resource  # not on Windows

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):  # pragma: no cover - defensive
        return None
    return peak / 2**20 if platform.system() == "Darwin" else peak / 1024
