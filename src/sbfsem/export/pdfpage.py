"""A page of a PDF report, laid out in inches from the top-left corner.

Render compare's report (:mod:`sbfsem.export.compare_pdf`) is made of pages of
text, tables, charts and pictures.  :class:`Page` is one of them: a matplotlib
:class:`~matplotlib.figure.Figure` the size of the paper, with helpers that place
things the way a page is read (inches across and down from the top-left), wrap
paragraphs, draw tables and remember which areas link to which page (a PDF's
links are added when the pages are put together, by
:func:`sbfsem.export.compare_pdf.assemble`).

The color maps are the viewer's own (``compare.js`` ``divergingColors`` and
``errorColors``), so a color in the report means what it means on screen: a
signed difference runs from blue (inside the other surface) through white (they
agree) to red (outside), and a distance from the annotations from light gray
(agrees) through amber to red at the scale and beyond.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import matplotlib
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

# TrueType fonts in the PDF: the text can be searched and copied.  Read when a
# page is saved, by the PDF backend only; nothing else here writes PDF.
matplotlib.rcParams["pdf.fonttype"] = 42

__all__ = [
    "BACKGROUND",
    "DIVERGING",
    "ERROR",
    "INK",
    "MUTED",
    "PAGE_SIZES",
    "Column",
    "Page",
    "diverging_rgb",
    "error_rgb",
    "fmt",
    "fmt_um",
    "ink",
    "nice_length",
    "text_width",
]

#: Paper sizes, inches (portrait).
PAGE_SIZES = {"letter": (8.5, 11.0), "a4": (8.27, 11.69)}
MARGIN_IN = 0.55

INK = "#1f2329"
MUTED = "#5f6670"
RULE = "#c9ced6"
ZEBRA = "#f2f4f7"
LINK = "#1f5fbf"
#: Behind every picture: the viewer's dark viewport, so a white (agreeing)
#: surface and a yellow layer read as they do on screen.
BACKGROUND = (0.118, 0.129, 0.153)

# The viewer's colors (compare.js).
_NEG_RGB = (0.23, 0.45, 0.92)   # inside the other surface
_POS_RGB = (0.86, 0.22, 0.22)   # outside it
_NEG, _POS = np.array(_NEG_RGB), np.array(_POS_RGB)
_ERROR_RGB: tuple[tuple[float, float, float], ...] = ((0.85, 0.87, 0.90), (1.0, 0.69, 0.0), (0.85, 0.12, 0.23))
_ERROR_STOPS = np.array(_ERROR_RGB)

#: The same maps for matplotlib's color bars and charts.
DIVERGING = LinearSegmentedColormap.from_list("sbfsem-diverging", [_NEG_RGB, (1.0, 1.0, 1.0), _POS_RGB])
ERROR = LinearSegmentedColormap.from_list("sbfsem-error", list(_ERROR_RGB))


def diverging_rgb(values: np.ndarray, scale: float) -> np.ndarray:
    """Blue-white-red for signed distances at +/- ``scale`` (``divergingColors``)."""
    t = np.clip(np.asarray(values, dtype=float) / max(float(scale), 1e-12), -1.0, 1.0)
    end = np.where(t[:, None] < 0, _NEG[None, :], _POS[None, :])
    return 1.0 + (end - 1.0) * np.abs(t)[:, None]


def error_rgb(values: np.ndarray, scale: float) -> np.ndarray:
    """Gray-amber-red for distances 0 to ``scale`` (``errorColors``)."""
    t = np.clip(np.asarray(values, dtype=float) / max(float(scale), 1e-12), 0.0, 1.0) * 2.0
    low = t < 1.0
    w = np.where(low, t, t - 1.0)[:, None]
    start = np.where(low[:, None], _ERROR_STOPS[0], _ERROR_STOPS[1])
    stop = np.where(low[:, None], _ERROR_STOPS[1], _ERROR_STOPS[2])
    return start + (stop - start) * w


def ink(color: str) -> str:
    """``color`` dark enough to read as a line or text on white paper: a light
    color (the first layer's yellow) is taken toward black until its
    luminance is at most 0.45; a dark one is returned as it is."""
    rgb = np.array(to_rgb(color))
    lum = float(rgb @ np.array([0.2126, 0.7152, 0.0722]))
    if lum <= 0.45:
        return color
    rgb = rgb * (0.45 / lum)
    return "#" + "".join(f"{int(round(c * 255)):02x}" for c in np.clip(rgb, 0, 1))


def fmt(value: float | None, digits: int = 3, *, signed: bool = False) -> str:
    """A number for a table: ``digits`` significant figures without an exponent
    between 0.001 and 10,000, thousands separated above; "—" for none."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    v = float(value)
    sign = "+" if signed and v > 0 else ""
    a = abs(v)
    if a == 0:
        return "0"
    if a >= 10_000:
        return f"{sign}{v:,.0f}"
    if a < 0.001:
        return f"{sign}{v:.2e}"
    decimals = max(0, digits - 1 - int(math.floor(math.log10(a))))
    text = f"{v:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return sign + text


def fmt_um(value: float | None, digits: int = 3, *, signed: bool = False) -> str:
    """:func:`fmt` with " µm" after it (or "—")."""
    text = fmt(value, digits, signed=signed)
    return text if text == "—" else f"{text} µm"


@lru_cache(maxsize=1)
def _measurer():
    from matplotlib.textpath import TextToPath

    return TextToPath(), FontProperties(size=10)


@lru_cache(maxsize=65536)
def _width_pt(text: str, bold: bool) -> float:
    """The width of ``text`` at 10 points, in points (the default font)."""
    measurer, prop = _measurer()
    if bold:
        prop = FontProperties(size=10, weight="bold")
    width, _, _ = measurer.get_text_width_height_descent(text, prop, ismath=False)
    return float(width)


def text_width(text: str, size: float, *, bold: bool = False) -> float:
    """How wide ``text`` is at ``size`` points, in inches."""
    return _width_pt(text, bold) * size / 10.0 / 72.0


def nice_length(target: float) -> float:
    """The 1, 2 or 5 times a power of ten nearest under ``target`` (a scale bar)."""
    if not target > 0:
        return 1.0
    power = 10 ** math.floor(math.log10(target))
    for step in (5, 2, 1):
        if step * power <= target:
            return step * power
    return power


@dataclass
class Column:
    """One column of a :meth:`Page.table`: its title, width (inches) and
    alignment ("left", "right" or "center")."""

    title: str
    width: float
    align: str = "left"


@dataclass
class Page:
    """One page: a figure the size of the paper, with the page's number (1-based,
    for the footer) and the links drawn on it so far: ``(x0, y0, x1, y1)`` in PDF
    points from the bottom-left, and the 1-based page each goes to."""

    size: tuple[float, float]
    number: int
    footer: str = ""
    fig: Figure = field(init=False)
    links: list[tuple[tuple[float, float, float, float], int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fig = Figure(figsize=self.size)
        self.fig.patch.set_facecolor("white")

    @property
    def width(self) -> float:
        return self.size[0]

    @property
    def height(self) -> float:
        return self.size[1]

    @property
    def left(self) -> float:
        return MARGIN_IN

    @property
    def right(self) -> float:
        return self.size[0] - MARGIN_IN

    @property
    def usable(self) -> float:
        return self.size[0] - 2 * MARGIN_IN

    @property
    def bottom(self) -> float:
        """The lowest a page's content may reach (inches from the top), above the footer."""
        return self.size[1] - 0.6

    # -- placing --------------------------------------------------------------
    def _fx(self, x: float) -> float:
        return x / self.size[0]

    def _fy(self, y: float) -> float:
        return 1.0 - y / self.size[1]

    def text(self, x: float, y: float, s: str, *, size: float = 9, weight: str = "normal",
             color: str = INK, ha: str = "left", va: str = "top", style: str = "normal",
             family: str | None = None, rotation: float = 0, **kw: Any):
        return self.fig.text(self._fx(x), self._fy(y), s, fontsize=size, fontweight=weight,
                             color=color, ha=ha, va=va, fontstyle=style, family=family,
                             rotation=rotation, **kw)

    def wrap(self, s: str, width: float, size: float, *, bold: bool = False) -> list[str]:
        """``s`` broken into lines that fit ``width`` inches at ``size`` points,
        measured word by word in the page's font (a paragraph break is kept)."""
        lines: list[str] = []
        space = text_width(" ", size, bold=bold)
        for part in s.split("\n"):
            line, used = "", 0.0
            for word in part.split():
                w = text_width(word, size, bold=bold)
                if line and used + space + w > width:
                    lines.append(line)
                    line, used = word, w
                else:
                    used += (space if line else 0.0) + w
                    line = f"{line} {word}" if line else word
            lines.append(line)
        return lines

    def para(self, x: float, y: float, s: str, *, width: float | None = None, size: float = 9,
             color: str = INK, weight: str = "normal", style: str = "normal",
             leading: float = 1.38) -> float:
        """A wrapped paragraph from (x, y); returns the y below its last line."""
        width = self.right - x if width is None else width
        step = size * leading / 72.0
        for line in self.wrap(s, width, size, bold=weight == "bold"):
            self.text(x, y, line, size=size, color=color, weight=weight, style=style)
            y += step
        return y

    def math(self, x: float, y: float, s: str, *, size: float = 10.5, color: str = INK,
             ha: str = "left", tall: bool | None = None) -> float:
        """One line of mathtext (``$...$``), set on its own line with room above
        and below for sums and fractions; returns the y below it."""
        tall = ("\\sum" in s or "\\frac" in s or "\\int" in s or "\\sqrt" in s) if tall is None else tall
        self.text(x, y + (0.06 if tall else 0.0), s, size=size, color=color, ha=ha)
        return y + size * (2.9 if tall else 1.9) / 72.0

    def rule(self, y: float, x0: float | None = None, x1: float | None = None, *,
             color: str = RULE, lw: float = 0.6) -> None:
        x0 = self.left if x0 is None else x0
        x1 = self.right if x1 is None else x1
        self.fig.add_artist(Line2D(
            [self._fx(x0), self._fx(x1)], [self._fy(y), self._fy(y)], color=color, lw=lw))

    def rect(self, x: float, y: float, w: float, h: float, *, face: Any = "none",
             edge: Any = "none", lw: float = 0.6, alpha: float = 1.0) -> None:
        self.fig.add_artist(Rectangle(
            (self._fx(x), self._fy(y + h)), w / self.size[0], h / self.size[1],
            facecolor=face, edgecolor=edge, lw=lw, alpha=alpha, transform=self.fig.transFigure))

    def swatch(self, x: float, y: float, color: str, size: float = 0.13) -> None:
        self.rect(x, y, size, size, face=color, edge="#333333", lw=0.5)

    def axes(self, x: float, y: float, w: float, h: float, **kw: Any):
        """Axes covering (x, y) to (x + w, y + h), inches from the top-left."""
        ax = self.fig.add_axes((self._fx(x), self._fy(y + h), w / self.size[0], h / self.size[1]),
                               **kw)
        return ax

    def chart(self, x: float, y: float, w: float, h: float, *, title: str = "",
              xlabel: str = "", ylabel: str = ""):
        """Axes for a chart, in the report's style (small type, light grid)."""
        ax = self.axes(x, y, w, h)
        ax.tick_params(labelsize=6.5, colors=MUTED, length=2.5, width=0.5)
        for spine in ax.spines.values():
            spine.set_linewidth(0.5)
            spine.set_color("#9aa1ab")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, color="#e3e6ea", lw=0.5)
        ax.set_axisbelow(True)
        if title:
            ax.set_title(title, fontsize=7.5, color=INK, loc="left", pad=4, fontweight="bold")
        if xlabel:
            ax.set_xlabel(xlabel, fontsize=6.5, color=MUTED, labelpad=2)
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=6.5, color=MUTED, labelpad=2)
        return ax

    def image(self, x: float, y: float, w: float, h: float, rgb: np.ndarray):
        """A picture filling (x, y, w, h), drawn at its own pixels (no
        resampling in the PDF); returns its axes, in pixel coordinates."""
        ax = self.axes(x, y, w, h)
        ax.imshow(rgb, interpolation="none", aspect="auto")
        ax.set_axis_off()
        return ax

    def link(self, x: float, y: float, w: float, h: float, page: int) -> None:
        """Make (x, y, w, h) a link to page ``page`` (1-based)."""
        H = self.size[1]
        self.links.append(((x * 72.0, (H - y - h) * 72.0, (x + w) * 72.0, (H - y) * 72.0), int(page)))

    # -- the furniture of every page -------------------------------------------
    def header(self, title: str, subtitle: str = "", *, right: str = "") -> float:
        """The page's title (and a line under it); returns the y below it."""
        self.text(self.left, 0.42, title, size=14, weight="bold")
        if right:
            self.text(self.right, 0.47, right, size=7.5, color=MUTED, ha="right")
        y = 0.42 + 14 * 1.35 / 72.0
        if subtitle:
            self.text(self.left, y + 0.02, subtitle, size=8.5, color=MUTED)
            y += 8.5 * 1.5 / 72.0
        self.rule(y + 0.06, color="#8d95a1", lw=0.8)
        return y + 0.2

    def finish(self) -> None:
        """The footer: what the report is, and the page number."""
        y = self.size[1] - 0.38
        self.rule(y - 0.08)
        if self.footer:
            self.text(self.left, y, self.footer, size=6.5, color=MUTED)
        self.text(self.right, y, f"{self.number}", size=7, color=MUTED, ha="right")

    def section(self, y: float, title: str, *, size: float = 10) -> float:
        """A heading inside a page; returns the y below it."""
        self.text(self.left, y, title, size=size, weight="bold")
        return y + size * 1.6 / 72.0

    # -- tables ------------------------------------------------------------------
    def table(self, x: float, y: float, columns: Sequence[Column], rows: Sequence[Sequence[Any]], *,
              size: float = 7.2, header: bool = True, zebra: bool = True,
              swatches: Sequence[str | None] | None = None,
              links: Sequence[int | None] | None = None,
              bold: Sequence[bool] | None = None,
              colors: Sequence[str | None] | None = None,
              max_y: float | None = None) -> float:
        """Rows of text in ``columns`` from (x, y); returns the y below the last row.

        ``swatches`` puts a colored square before the first cell of a row,
        ``links`` makes a row a link to that page (its last cell shows the
        number), ``bold`` and ``colors`` style whole rows.  Rows that would pass
        ``max_y`` are left out (the caller pages them).
        """
        row_h = size * 1.62 / 72.0
        total_w = sum(c.width for c in columns)
        pad = 0.04

        def cell_x(i: int, col: Column) -> tuple[float, str]:
            x0 = x + sum(c.width for c in columns[:i])
            if col.align == "right":
                return x0 + col.width - pad, "right"
            if col.align == "center":
                return x0 + col.width / 2, "center"
            return x0 + pad, "left"

        if header:
            for i, col in enumerate(columns):
                cx, ha = cell_x(i, col)
                self.text(cx, y + row_h / 2, col.title, size=size - 0.4, weight="bold",
                          color=MUTED, ha=ha, va="center")
            y += row_h
            self.rule(y, x, x + total_w, color="#8d95a1", lw=0.6)
        for r, row in enumerate(rows):
            if max_y is not None and y + row_h > max_y:
                break
            if zebra and r % 2 == 1:
                self.rect(x, y, total_w, row_h, face=ZEBRA)
            weight = "bold" if bold and bold[r] else "normal"
            color = (colors[r] or INK) if colors else INK
            for i, (col, value) in enumerate(zip(columns, row, strict=False)):
                cx, ha = cell_x(i, col)
                text = "" if value is None else str(value)
                if i == 0 and swatches and swatches[r]:
                    self.swatch(cx, y + (row_h - 0.11) / 2, swatches[r] or "#888888", size=0.11)
                    cx += 0.15
                link_cell = bool(links and links[r] and i == len(columns) - 1)
                self.text(cx, y + row_h / 2, text, size=size, weight=weight,
                          color=LINK if link_cell else color, ha=ha, va="center")
            if links and links[r]:
                self.link(x, y, total_w, row_h, int(links[r] or 0))
            y += row_h
        return y
