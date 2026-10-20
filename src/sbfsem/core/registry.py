"""On-disk data files shipped with the package.

Ports ``util/registration/omitLocations.m``, ``util/registration/omitSections.m``
and the file-reading half of ``+sbfsem/+builtin/Transforms.translate``.

All files live in ``sbfsem/data`` and are keyed by the upper-cased canonical
volume name, exactly as the MATLAB code expected them.

Each reader is read once per canonical volume however the caller spelled it
(``"i"``, ``"inferior"`` and ``"NeitzInferiorMonkey"`` are one file), and hands
out arrays that cannot be written to: the arrays are shared between callers, so
one that edited its copy in place would have edited everyone's.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Generic, TypeVar

import numpy as np

from .volumes import validate_source

_R = TypeVar("_R")

__all__ = [
    "data_path",
    "read_xy_offset",
    "read_omitted_ids",
    "read_omitted_sections",
    "read_boundary_markers",
]


class _ByVolume(Generic[_R]):
    """``func(canonical_volume, *rest)``, cached, taking any spelling of the volume.

    Takes its arguments as ``func`` does, by position or by name (they were
    ``functools.cache`` functions, which take either; a wrapper that accepted
    only positional ones broke ``read_boundary_markers('i', layer='GCL')``), and
    a call by name and a call by position are one cached read.

    Keeps ``cache_clear`` and ``cache_info``, for the code that rewrites a data
    file and must have it read again.
    """

    def __init__(self, func: Callable[..., _R]):
        self._signature = inspect.signature(func)
        self._cached = functools.cache(func)
        functools.update_wrapper(self, func)

    def __call__(self, *args, **kwargs) -> _R:
        bound = self._signature.bind(*args, **kwargs)   # a call func would refuse is a TypeError
        bound.apply_defaults()
        arguments = list(bound.arguments.values())
        arguments[0] = validate_source(arguments[0])
        return self._cached(*arguments)

    def cache_clear(self) -> None:
        self._cached.cache_clear()

    def cache_info(self):
        return self._cached.cache_info()


def _frozen(array: np.ndarray) -> np.ndarray:
    array.setflags(write=False)
    return array


def data_path(filename: str) -> Path | None:
    """Return the path to a packaged data file, or ``None`` if absent.

    Resolved through ``sbfsem`` rather than ``sbfsem.data`` so the data
    directory does not need to be an importable package in a built wheel.
    """
    try:
        candidate = resources.files("sbfsem").joinpath("data", filename)
    except ModuleNotFoundError:  # pragma: no cover - packaging accident
        return None
    return Path(str(candidate)) if candidate.is_file() else None


@_ByVolume
def read_xy_offset(source: str) -> np.ndarray | None:
    """Per-section XY registration offsets.

    Returns a read-only ``(nSections, 3)`` array of ``[section, dx, dy]`` in
    Viking pixels, or ``None`` when the volume has no offset file.
    """
    p = data_path(f"XY_OFFSET_{source.upper()}.txt")
    if p is None:
        return None
    return _frozen(np.loadtxt(p, delimiter=",", ndmin=2))


@_ByVolume
def _omitted_ids_table(source: str) -> np.ndarray | None:
    p = data_path(f"OMITTED_IDS_{source.upper()}.txt")
    if p is None:
        return None
    return _frozen(np.loadtxt(p, delimiter=",", ndmin=2))


def read_omitted_ids(cell_id: int, source: str) -> np.ndarray:
    """Location IDs to drop for one structure, as ``omitLocations`` did."""
    table = _omitted_ids_table(source)
    if table is None or table.size == 0:
        return np.empty(0, dtype=np.int64)
    return table[table[:, 0] == cell_id, 1].astype(np.int64)


@_ByVolume
def read_omitted_sections(source: str) -> np.ndarray:
    """Z sections to drop for a volume, as ``omitSections`` did.

    No volume has an ``OMITTED_SECTIONS_*.txt`` file today, so this is always
    empty; the reader stays for the MATLAB format (``omitSections.m``) should
    one be added.
    """
    p = data_path(f"OMITTED_SECTIONS_{source.upper()}.txt")
    if p is None:
        return _frozen(np.empty(0, dtype=np.int64))
    return _frozen(np.loadtxt(p, delimiter=",", ndmin=1).astype(np.int64).ravel())


@_ByVolume
def read_boundary_markers(source: str, layer: str) -> np.ndarray | None:
    """Cached IPL boundary marker locations.

    ``layer`` is ``'INL'`` or ``'GCL'``.  Returns a read-only ``(N, 3)`` array of
    ``[X, Y, Z]`` in Viking pixels/sections, matching ``data/cacheBoundaryMarkers.m``.
    """
    layer = layer.upper()
    if layer not in ("INL", "GCL"):
        raise ValueError("layer must be 'INL' or 'GCL'")
    p = data_path(f"{source.upper()}_{layer}.txt")
    if p is None:
        return None
    return _frozen(np.loadtxt(p, delimiter=",", ndmin=2))
