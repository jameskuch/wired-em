"""Colormap registry.

Ports ``+sbfsem/+ui/ColorMaps.m`` (18 maps).  The MATLAB class pulled each one
from a different vendored file — ``crameri.m``, ``pmkmp.m``, ``othercolor.m``,
``lbmap.m``, ``haxby.m``, ``viridis.m``, ``antijet.m``, ``isolum.m``,
``ametrine.m`` — and all of those are pure data, so the 256-entry table each
produced under MATLAB R2023b is captured by
``tests/fixtures/capture/capture_colormaps.m`` and shipped as
``sbfsem/data/colormaps.json``.  Other lengths resample that table linearly,
which is what every vendored file did itself; the three MATLAB builtins
(``gray``, ``bone``, ``hsv``) are recomputed from their formulas so any length
matches exactly.

The web app colors from the same tables: ``static/js/colormap-tables.js`` is
written by ``python -m sbfsem.util.colormaps`` and a unit test keeps it in step.

Deviations (see DEVIATIONS.md §6.4):

* ``ColorMaps.setMap`` called ``obj.getMap(N)``; the method is ``getColormap``
  (F210).
* The ``otherwise`` branch warned and left the output unassigned, so an unknown
  map errored on the caller's side rather than at the point of failure (F211).
  Unknown names raise here.
* ``colorblind`` tested ``ismember(obj, {ColorMaps.Isolum, ...})`` with a *cell*
  of enumeration members, which does not match; the check works now.
* ``ColorMaps.RedBlue`` applied ``fliplr`` to an Nx3 colormap, which swaps the R
  and B channels rather than reversing the map.  The map is reversed here, as
  ``FigureView.setColormap('redblue')`` did with ``flipud``.
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from functools import cache
from importlib import resources

import numpy as np

__all__ = [
    "COLORBLIND_SAFE",
    "DEFAULT_COLORMAP",
    "TABLE_LENGTH",
    "ColorMaps",
    "colormap",
    "js_tables_source",
]

#: Entries in every shipped table: the ``getColormap`` default N.
TABLE_LENGTH = 256

#: The maps that survive red-green color blindness.
COLORBLIND_SAFE = frozenset({"Isolum", "Ametrine", "Viridis", "Batlow", "Devon", "Parula"})


class ColorMaps(Enum):
    """The 18 colormaps offered by the MATLAB interface, in menu order."""

    Spectral = "Spectral"
    Bone = "Bone"
    CubicL = "CubicL"
    RdYlGn = "RdYlGn"
    Gray = "Gray"
    Haxby = "Haxby"
    Hsv = "Hsv"
    Roma = "Roma"
    Batlow = "Batlow"
    Hawaii = "Hawaii"
    Devon = "Devon"
    AntiJet = "AntiJet"
    Parula = "Parula"
    RedBlue = "RedBlue"
    Viridis = "Viridis"
    StepSeq = "StepSeq"
    Ametrine = "Ametrine"
    Isolum = "Isolum"

    @classmethod
    def from_str(cls, name: str | ColorMaps) -> ColorMaps:
        if isinstance(name, cls):
            return name
        key = str(name).strip().lower()
        for member in cls:
            if member.value.lower() == key:
                return member
        raise ValueError(f"Unknown colormap: {name!r}")

    @property
    def colorblind_safe(self) -> bool:
        return self.value in COLORBLIND_SAFE

    def colors(self, n: int = TABLE_LENGTH) -> np.ndarray:
        """``(n, 3)`` RGB array in 0-1."""
        return colormap(self, n)

    def hex_colors(self, n: int = TABLE_LENGTH) -> list[str]:
        return ["#" + "".join(f"{c:02x}" for c in row) for row in _bytes(self.colors(n))]


#: What ``RenderApp`` opened with: ``othercolor('Spectral10', 256)``.
DEFAULT_COLORMAP = ColorMaps.Spectral


@cache
def _tables() -> dict[str, np.ndarray]:
    path = resources.files("sbfsem").joinpath("data", "colormaps.json")
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    tables = {name: np.asarray(rows, dtype=float) for name, rows in raw.items()}
    missing = {m.value for m in ColorMaps} - tables.keys()
    if missing:
        raise RuntimeError(f"colormaps.json lacks {sorted(missing)}")
    return tables


# -- the MATLAB builtins, as MATLAB computes them for any length -------------
def _gray(n: int) -> np.ndarray:
    return np.repeat((np.arange(n) / max(n - 1, 1))[:, None], 3, axis=1)


def _hot(n: int) -> np.ndarray:
    k = (3 * n) // 8
    ramp = np.arange(1, k + 1) / max(k, 1)
    r = np.concatenate([ramp, np.ones(n - k)])
    g = np.concatenate([np.zeros(k), ramp, np.ones(n - 2 * k)])
    b = np.concatenate([np.zeros(2 * k), np.arange(1, n - 2 * k + 1) / max(n - 2 * k, 1)])
    return np.stack([r, g, b], axis=1)


def _bone(n: int) -> np.ndarray:
    # ``(7*gray(m) + fliplr(hot(m)))/8``
    return (7 * _gray(n) + _hot(n)[:, ::-1]) / 8


def _hsv(n: int) -> np.ndarray:
    # ``hsv2rgb([h ones ones])`` with ``h = (0:m-1)'/max(m,1)``
    k = np.arange(n) / max(n, 1) * 6
    sector = np.floor(k).astype(int)
    f = k - sector
    r = np.select([sector == 0, sector == 1, sector == 4, sector == 5], [1, 1 - f, f, 1], 0)
    g = np.select([sector == 0, sector == 1, sector == 2, sector == 3], [f, 1, 1, 1 - f], 0)
    b = np.select([sector == 2, sector == 3, sector == 4, sector == 5], [f, 1, 1, 1 - f], 0)
    return np.stack([r, g, b], axis=1)


_BUILTINS = {"Gray": _gray, "Bone": _bone, "Hsv": _hsv}


def colormap(name: str | ColorMaps, n: int = TABLE_LENGTH) -> np.ndarray:
    """Sample a colormap to ``(n, 3)`` RGB in 0-1, as ``getColormap(N)`` did."""
    member = ColorMaps.from_str(name)
    n = max(2, int(n))
    builtin = _BUILTINS.get(member.value)
    if builtin is not None:
        return builtin(n)
    table = _tables()[member.value]
    if n == len(table):
        return table.copy()
    x = np.linspace(0, 1, len(table))
    xi = np.linspace(0, 1, n)
    return np.stack([np.interp(xi, x, table[:, c]) for c in range(3)], axis=1)


def _bytes(colors: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(colors * 255), 0, 255).astype(int)


def js_tables_source() -> str:
    """The ES module the web app colors from: every table as 8-bit hex."""
    lines = [
        "// Generated by `python -m sbfsem.util.colormaps > src/sbfsem_web/static/js/colormap-tables.js`.",
        "// Do not edit by hand: tests/unit/test_web.py asserts it matches the Python registry.",
        "//",
        "// The 18 colormaps of the MATLAB RenderApp's Colormap menu, in menu order, as",
        "// the 256 RGB entries MATLAB R2023b produced for each, six hex digits per entry.",
        "",
        f"export const DEFAULT_COLORMAP = '{DEFAULT_COLORMAP.value}';",
        "",
        "export const COLORMAP_TABLES = {",
    ]
    for member in ColorMaps:
        hexes = "".join(f"{c:02x}" for row in _bytes(colormap(member)) for c in row)
        lines.append(f"  {member.value}:")
        lines.append(f"    '{hexes}',")
    lines.append("};")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.write(js_tables_source())
