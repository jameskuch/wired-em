"""Coordinate transforms from Viking pixels to microns.

Ports ``+sbfsem/+builtin/Transforms.m``, ``util/viking2micron.m`` and
``util/fastUM.m``.

Deviations:

* ``Transforms.scale`` had no branch for ``Nx2`` input, so it returned an
  unassigned value for the 2-column case that ``viking2micron`` explicitly
  supports (F152).  Handled here.
* ``Transforms.translate`` indexed the offset table by section number without
  bounds checking; out-of-range sections now raise a clear error instead of a
  MATLAB index error.
"""

from __future__ import annotations

from enum import Enum

import numpy as np

from .registry import read_xy_offset
from .volumes import Volumes, validate_source

__all__ = ["Transforms", "viking2micron", "MICRON_SYMBOL"]

#: ``util/fastUM.m`` returned a mojibaked micron symbol; use the real one.
MICRON_SYMBOL = "µm"


class Transforms(Enum):
    """Which XY correction to apply to incoming Viking coordinates."""

    Standard = "Standard"  # scale by volumeScale, nm -> um
    Custom = "Custom"  # per-section XY offset file, then Standard scaling
    NoTransform = "None"  # leave coordinates untouched

    @classmethod
    def from_str(cls, value: str | Transforms | None) -> Transforms:
        if isinstance(value, cls):
            return value
        key = str(value or "").strip().lower()
        if key == "standard":
            return cls.Standard
        if key in ("custom", "local"):
            return cls.Custom
        if key in ("none", ""):
            return cls.NoTransform
        raise ValueError(f"Unknown transform: {value!r}")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    # -- operations --------------------------------------------------------
    @staticmethod
    def scale(data: np.ndarray, volume_scale) -> np.ndarray:
        """Scale Viking pixel data to microns.

        ``volume_scale`` is in nm; ``data`` may be ``(N,)`` radii, ``(N, 2)``
        XY or ``(N, 3)`` XYZ.
        """
        arr = np.asarray(data, dtype=float)
        vs = np.asarray(volume_scale, dtype=float) / 1e3  # nm -> um
        if arr.ndim == 1:
            return arr * vs[0]
        ncol = arr.shape[1]
        if ncol == 1:
            return arr * vs[0]
        if ncol == 2:
            return arr * vs[:2]
        if ncol == 3:
            return arr * vs
        raise ValueError(f"Cannot scale an array with {ncol} columns")

    @staticmethod
    def translate(xyz: np.ndarray, source) -> tuple[np.ndarray, np.ndarray]:
        """Shift XY to compensate for per-section registration error.

        ``xyz`` is ``(N, 3)`` in Viking pixels with Z as the section number.
        """
        xyz = np.asarray(xyz, dtype=float)
        offsets = read_xy_offset(source)
        if offsets is None:
            raise FileNotFoundError(
                f"No XY offset file for volume {validate_source(source)}"
            )
        sections = np.rint(xyz[:, 2]).astype(int)
        lo, hi = int(offsets[:, 0].min()), int(offsets[:, 0].max())
        if sections.size and (sections.min() < lo or sections.max() > hi):
            raise IndexError(
                f"Section numbers {sections.min()}-{sections.max()} fall outside the "
                f"offset table range {lo}-{hi}"
            )
        # Offset rows are keyed by section number in column 0.
        index = {int(s): i for i, s in enumerate(offsets[:, 0])}
        rows = np.array([index[int(s)] for s in sections], dtype=int)
        return xyz[:, 0] + offsets[rows, 1], xyz[:, 1] + offsets[rows, 2]

    @staticmethod
    def nasal_monkey(xyz: np.ndarray, scale_factors) -> tuple[np.ndarray, np.ndarray]:
        """Correct nornir's off-by-one scaling drift in the Nasal volume.

        Ported and tested, and applied nowhere: no :class:`Transforms` member
        selects it, and its MATLAB source was never called either.
        """
        xyz = np.asarray(xyz, dtype=float)
        sf = np.asarray(scale_factors, dtype=float)
        z_offset = sf[2] * np.abs(xyz[:, 2] - 1177)
        x = xyz[:, 0] - (z_offset * (xyz[:, 0] / sf[0]))
        y = xyz[:, 1] - (z_offset * (xyz[:, 1] / sf[1]))
        return x, y


def viking2micron(xyz, source, volume_scale=None) -> np.ndarray:
    """Scale Viking coordinates to microns.

    Ports ``util/viking2micron.m``, which asked Viking for the scale.  This
    never does: it uses ``volume_scale`` (nm) when given, such as the live scale
    a loaded cell carries, and otherwise the volume's cached scale.
    """
    if volume_scale is None:
        volume_scale = Volumes.from_str(source).cached_scale
    return Transforms.scale(np.asarray(xyz, dtype=float), volume_scale)
