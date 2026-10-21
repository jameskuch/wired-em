"""Render compare's report: one cell's renderings, held to the annotations and
to each other, as a color PDF.

The viewer's Render compare tab shows a cell under several techniques and
settings side by side.  Its report puts every rendering the person chose (a
*rendering* is one layer of one pane: a technique with all of its settings, as
it was built) on pages of its own, measures every chosen pair of renderings
against each other whether or not a Difference pane shows it, and tabulates
the lot:

* **Summary.**  The renderings with their size and shape and their error
  against the annotations; four matrices of the pairwise error (the mean
  distance both ways, the Hausdorff distance, the signed mean and the 95th
  percentile one way); every pair ranked, most different first; charts of the
  error against the annotations and of volume and area.
* **One section per pane.**  The pane as it was on screen; for each of its
  renderings a page of its settings (every option the technique uses, the ones
  changed from the technique's defaults marked) and its shape, and a page of
  its error against the annotations (pictures colored by the distance to the
  nearest annotation, the distribution, the profile along Z and the worst
  places by Location ID); then, for each pair it leads, three pages: pictures
  of both surfaces colored by their signed distance to each other and of where
  their outlines differ, from each chosen viewpoint; the numbers and their
  distributions both ways; and where they differ most, as cross-sections at
  the section where they disagree most, close-ups and a table of places.
* **Addendum** (:mod:`sbfsem.export.compare_addendum`): every number's
  definition, with the formula the code computes and a figure of what it
  measures.

The measurements are the viewer's (:mod:`sbfsem.geometry.meshdiff`, through a
:class:`ReportSource`; the web service's reads its caches), so a number here is
the number the viewer shows for the same surfaces.  The pictures are drawn
here (:mod:`sbfsem.geometry.raster`) in one frame per viewpoint for the whole
report: every picture from XY1 is at the same place and scale.  Every Z is also
given as a section, and every place by its coordinates and section, in the
volume the cover names (an ID or a section means nothing without its volume).

The pages are drawn in two streams -- the body as each rendering and pair is
measured, so nothing measured is held longer than its pages take, and the front
matter last, when every number is known -- and put together with
:func:`assemble`, which adds the bookmarks, the links of the contents, the
tables and the matrices, and stores the pictures as JPEG.  Page numbers are
known before anything is drawn (:class:`Plan`).
"""

from __future__ import annotations

import contextlib
import io
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize, to_rgb
from matplotlib.patches import Circle, Polygon, Rectangle

from .. import progress
from ..analysis.compare_report import (
    FieldStats,
    PairResult,
    SurfaceFacts,
    pair_result,
    surface_facts,
)
from ..geometry.mesh import Mesh
from ..geometry.meshdiff import DeviationMap, MeshDifference
from ..geometry.raster import (
    NAMED_VIEWS,
    Frame,
    View,
    extent,
    frame_around,
    rasterize,
    section_segments,
)
from ..service.render_schema import describe_options, match_preset
from .pdfpage import (
    BACKGROUND,
    DIVERGING,
    ERROR,
    INK,
    LINK,
    MUTED,
    PAGE_SIZES,
    Column,
    Page,
    diverging_rgb,
    error_rgb,
    fmt,
    fmt_um,
    ink,
    nice_length,
)

__all__ = [
    "MAX_PAIRS",
    "MAX_RENDERINGS",
    "Plan",
    "RenderingDeviation",
    "ReportFile",
    "ReportPane",
    "ReportRendering",
    "ReportSource",
    "ReportSpec",
    "assemble",
    "write_compare_report",
]

#: What one report holds at most: Render compare's six panes of four layers,
#: and every pair of them.
MAX_RENDERINGS = 24
MAX_PAIRS = MAX_RENDERINGS * (MAX_RENDERINGS - 1) // 2
#: Pictures are drawn at this many pixels an inch of the page.
PICTURE_DPI = 170
#: The largest pane picture a report takes (the page sends them at most 1,200 px wide).
MAX_PICTURE_PIXELS = 4_000_000
#: Pictures stored as JPEG at this quality (smaller ones stay lossless).
JPEG_QUALITY = 88
JPEG_MIN_PIXELS = 128
#: Rows of a paged table.
CONTENTS_ROWS = 44
PAIR_ROWS = 40
RENDERING_ROWS = 24

#: The report's colors when the layers' own do not tell every rendering apart
#: (the first layer of every pane is yellow on screen): distinct hues, in order.
REPORT_PALETTE = ("#e6ab02", "#1f78b4", "#d95f02", "#33a02c", "#7570b3", "#e7298a", "#a6761d",
                  "#17becf", "#b15928", "#6a3d9a", "#66a61e", "#e31a1c", "#fb9a99", "#cab2d6",
                  "#b2df8a", "#a6cee3", "#fdbf6f", "#666666", "#bcbd22", "#8c564b", "#9467bd",
                  "#2ca02c", "#ff7f0e", "#1f77b4")

#: One drawing at a time in this process: matplotlib's figures are separate,
#: but its font and text caches are shared, and drawing holds the GIL anyway.
_DRAW = threading.Lock()


# -- what a report is asked for --------------------------------------------------------
@dataclass(frozen=True)
class ReportRendering:
    """One rendering: a layer of a pane as it was built.  ``options`` are the
    render options it was built with (the layer's snapshot, never what its
    panel says now); ``deviation`` asks for its page against the annotations;
    ``stale`` says its panel has been changed since (shown, not measured)."""

    label: str
    color: str
    pane: int
    layer: int
    options: Mapping[str, Any]
    deviation: bool = True
    stale: bool = False
    #: The copy of the cell it was built from (a registration's ``copy_tag``; None:
    #: the report's own), and that registration in words.
    copy: str | None = None
    registration: str = ""


@dataclass(frozen=True)
class ReportPane:
    """A pane as the page showed it: ``layers`` it held, a Difference's two
    renderings (indices into the spec's renderings), and a picture of it (PNG
    or JPEG bytes)."""

    number: int
    kind: str = "layers"
    layers: int = 0
    difference: tuple[int, int] | None = None
    snapshot: bytes | None = None


@dataclass(frozen=True)
class ReportSpec:
    """What a report holds.  ``pairs`` are indices into ``renderings``;
    ``views`` the viewpoints every picture is drawn from; ``sign`` how a
    difference decides which side a point is on (``"enclosure"`` or
    ``"normal"``, as :func:`~sbfsem.geometry.meshdiff.mesh_difference`);
    ``z_scale_nm`` the volume's section thickness, which turns a Z into a
    section number.  ``copy`` is the copy of the cell the report was asked of;
    each rendering names its own when it was built from another registration."""

    cell: int
    source: str
    volume: str
    renderings: tuple[ReportRendering, ...]
    pairs: tuple[tuple[int, int], ...] = ()
    panes: tuple[ReportPane, ...] = ()
    views: tuple[View, ...] = (NAMED_VIEWS["xy1"], NAMED_VIEWS["xz1"])
    sign: str = "enclosure"
    addendum: bool = True
    page: str = "letter"
    created: str = ""
    build: str = ""
    copy: str | None = None
    z_scale_nm: float | None = None

    def __post_init__(self) -> None:
        n = len(self.renderings)
        if not 1 <= n <= MAX_RENDERINGS:
            raise ValueError(f"a report holds 1 to {MAX_RENDERINGS} renderings, not {n}")
        if self.page not in PAGE_SIZES:
            raise ValueError(f"page must be one of {', '.join(PAGE_SIZES)}")
        if not 1 <= len(self.views) <= 4:
            raise ValueError("a report draws from 1 to 4 viewpoints")
        seen: set[tuple[int, int]] = set()
        for a, b in self.pairs:
            if not (0 <= a < n and 0 <= b < n) or a == b:
                raise ValueError(f"pair ({a}, {b}) does not name two of the {n} renderings")
            seen.add((min(a, b), max(a, b)))
        object.__setattr__(self, "pairs", tuple(sorted(seen)))

    @property
    def copies(self) -> tuple[str | None, ...]:
        """The copies of the cell (registrations) the renderings were built from, in
        the order they first appear."""
        return tuple(dict.fromkeys(r.copy if r.copy is not None else self.copy for r in self.renderings))

    def registration_of(self, index: int) -> str:
        """Rendering ``index``'s registration in words, or its copy's tag when the
        page named none."""
        rendering = self.renderings[index]
        return rendering.registration or str(rendering.copy or self.copy or "")

    def section_of(self, z_um: float) -> float | None:
        """The section a Z (µm) lies in, or ``None`` without the volume's Z scale."""
        if not self.z_scale_nm:
            return None
        return z_um * 1000.0 / float(self.z_scale_nm)

    @property
    def name(self) -> str:
        return f"c{self.cell} · {self.volume}"


@dataclass(frozen=True)
class RenderingDeviation:
    """A rendering against the annotations: the error map (per vertex and per
    rim point) and its summary, :class:`~sbfsem.service.dto.DeviationDTO`'s
    fields (``surface_um``, ``annotation_um``, ...)."""

    map: DeviationMap
    summary: Mapping[str, Any]


class ReportSource(Protocol):
    """Where a report's surfaces and measurements come from (the web service's
    caches, or meshes built in a test).  Indices are the spec's renderings.  A
    failure of one is pages that say so; a source may name, as ``fatal``, the
    exceptions that end the report instead (the service stopping under a build)."""

    def mesh(self, index: int) -> Mesh: ...

    def deviation(self, index: int) -> RenderingDeviation | None: ...

    def difference(self, a: int, b: int) -> MeshDifference: ...

    def annotations(self) -> tuple[np.ndarray, np.ndarray, list[tuple[np.ndarray, float]]]: ...

    def annotation_names(self) -> tuple[np.ndarray, np.ndarray] | None: ...


@dataclass(frozen=True)
class ReportFile:
    """A finished report: the PDF, its page count, a file name, how long it
    took and what it says about itself (renderings that could not be measured)."""

    pdf: bytes
    pages: int
    filename: str
    seconds: float
    notes: tuple[str, ...] = ()

    def meta(self) -> dict[str, Any]:
        return {"filename": self.filename, "bytes": len(self.pdf), "pages": self.pages,
                "seconds": round(self.seconds, 1), "notes": list(self.notes)}


# -- the plan: every page's number before anything is drawn ------------------------------
@dataclass
class Item:
    """A run of pages: what it is (``kind``, ``key``), how many pages, its title
    in the contents and the bookmarks, its level there, and its first page."""

    kind: str
    key: Any
    pages: int
    title: str
    level: int = 0
    first: int = 0


#: The addendum's pages, in order (compare_addendum draws them).
ADDENDUM_TITLES = (
    "A1. How this report is made",
    "A2. Distance from a point to a surface",
    "A3. Which side: the sign of a difference",
    "A4. Weighting by area",
    "A5. The summary numbers of a difference",
    "A6. Volume from distances",
    "A7. Against the annotations",
    "A8. Reading the pictures and charts",
    "A9. Limits and caveats",
)


class Plan:
    """Every run of pages and its first page number.  The front matter (cover,
    contents, summary) comes first; the body (a section per pane) and the
    addendum after it; pairs are under the pane of their first rendering, right
    after that rendering's own pages."""

    def __init__(self, spec: ReportSpec) -> None:
        self.spec = spec
        n_pairs = len(spec.pairs)
        summary = [Item("summary-renderings", None, max(1, math.ceil(len(spec.renderings) / RENDERING_ROWS)),
                        "Summary: the renderings")]
        if len(spec.copies) > 1:
            summary.append(Item("summary-registrations", None, 1, "Summary: the registrations"))
        if n_pairs:
            summary.append(Item("summary-matrices", None, 1, "Summary: pairwise error matrices"))
            summary.append(Item("summary-pairs", None, math.ceil(n_pairs / PAIR_ROWS),
                                "Summary: every pair, most different first"))
        if any(r.deviation for r in spec.renderings):
            summary.append(Item("summary-annotations", None, 1,
                                "Summary: against the annotations"))
        summary.append(Item("summary-shape", None, 1, "Summary: size and shape"))
        body: list[Item] = []
        for pane in self.panes():
            body.append(Item("pane", pane.number, 1, f"Pane {pane.number}", 0))
            for r in self.pane_renderings(pane.number):
                rendering = spec.renderings[r]
                body.append(Item("rendering", r, 1, f"R{r + 1} {rendering.label}: settings and shape", 1))
                if rendering.deviation:
                    body.append(Item("rendering-annotations", r, 1,
                                     f"R{r + 1}: against the annotations", 1))
                for a, b in spec.pairs:
                    if a == r:
                        body.append(Item("pair", (a, b), 3,
                                         f"R{a + 1} vs R{b + 1}: {spec.renderings[a].label} "
                                         f"vs {spec.renderings[b].label}", 1))
        addendum = ([Item("addendum", k, 1, title) for k, title in enumerate(ADDENDUM_TITLES)]
                    if spec.addendum else [])
        entries = len(summary) + len(body) + len(addendum)
        self.front = [Item("cover", None, 1, "Cover"),
                      Item("contents", None, max(1, math.ceil(entries / CONTENTS_ROWS)), "Contents"),
                      *summary]
        self.body = body + addendum
        page = 1
        for item in [*self.front, *self.body]:
            item.first = page
            page += item.pages
        self.total = page - 1
        self._index = {(i.kind, i.key): i for i in [*self.front, *self.body]}

    def panes(self) -> list[ReportPane]:
        """The panes, those the spec lists and any a rendering names, in order."""
        listed = {p.number: p for p in self.spec.panes}
        for r in self.spec.renderings:
            listed.setdefault(r.pane, ReportPane(number=r.pane))
        return [listed[k] for k in sorted(listed)]

    def pane_renderings(self, pane: int) -> list[int]:
        return sorted((i for i, r in enumerate(self.spec.renderings) if r.pane == pane),
                      key=lambda i: self.spec.renderings[i].layer)

    def page(self, kind: str, key: Any = None) -> int | None:
        item = self._index.get((kind, key))
        return item.first if item else None

    @property
    def front_pages(self) -> int:
        return sum(i.pages for i in self.front)


# -- pictures -----------------------------------------------------------------------------
class Pictures:
    """Every picture of the report, drawn in one frame per viewpoint: the box
    that holds every rendering seen from it (:meth:`add`), so the same view is
    at the same place and scale on every page."""

    def __init__(self, views: Sequence[View]) -> None:
        self.views = list(views)
        self._boxes: dict[str, list[float]] = {}

    def add(self, vertices: np.ndarray) -> None:
        for view in self.views:
            lo_u, hi_u, lo_v, hi_v = extent(vertices, view)
            box = self._boxes.get(view.key)
            if box is None:
                self._boxes[view.key] = [lo_u, hi_u, lo_v, hi_v]
            else:
                box[0], box[1] = min(box[0], lo_u), max(box[1], hi_u)
                box[2], box[3] = min(box[2], lo_v), max(box[3], hi_v)

    def frame(self, view: View, w_in: float, h_in: float,
              box: tuple[float, float, float, float] | None = None) -> Frame:
        held = box or tuple(self._boxes.get(view.key, (0.0, 1.0, 0.0, 1.0)))
        return frame_around(view, held, max(16, round(w_in * PICTURE_DPI)),  # type: ignore[arg-type]
                            max(16, round(h_in * PICTURE_DPI)), margin=0.05)

    @staticmethod
    def solid(mesh: Mesh, frame: Frame, color: str, faces: np.ndarray | None = None):
        return rasterize(mesh.vertices, mesh.faces if faces is None else faces, frame,
                         color=to_rgb(color), background=BACKGROUND)

    @staticmethod
    def signed(mesh: Mesh, frame: Frame, values: np.ndarray, scale: float,
               faces: np.ndarray | None = None):
        return rasterize(mesh.vertices, mesh.faces if faces is None else faces, frame,
                         values=values, colormap=lambda v: diverging_rgb(v, scale),
                         background=BACKGROUND)

    @staticmethod
    def error(mesh: Mesh, frame: Frame, values: np.ndarray, scale: float):
        return rasterize(mesh.vertices, mesh.faces, frame, values=values,
                         colormap=lambda v: error_rgb(v, scale), background=BACKGROUND)


def _overlap(cover_a: np.ndarray, cover_b: np.ndarray, color_a: str, color_b: str) -> np.ndarray:
    """Where two silhouettes differ: A's color where only A covers the picture,
    B's where only B does, light gray where both do."""
    both = np.minimum(cover_a, cover_b)
    a_only, b_only = cover_a - both, cover_b - both
    rest = 1.0 - np.maximum(cover_a, cover_b)
    rgb = (rest[..., None] * np.array(BACKGROUND) + a_only[..., None] * np.array(to_rgb(color_a))
           + b_only[..., None] * np.array(to_rgb(color_b)) + both[..., None] * 0.78)
    return np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8)


def _scale_bar(ax, frame: Frame) -> None:
    """A white bar of a round number of microns at the picture's bottom left."""
    width_um = frame.width * frame.um_per_px
    length = nice_length(width_um * 0.22)
    px = length / frame.um_per_px
    x0, y0 = frame.width * 0.05, frame.height * 0.93
    ax.plot([x0, x0 + px], [y0, y0], color="white", lw=1.6, solid_capstyle="butt")
    ax.text(x0 + px / 2, y0 - frame.height * 0.025, f"{fmt(length)} µm", color="white",
            fontsize=5.5, ha="center", va="bottom")


def _picture(page: Page, x: float, y: float, w: float, h: float, rgb: np.ndarray, frame: Frame,
             caption: str = "", *, bar: bool = True):
    ax = page.image(x, y, w, h, rgb)
    ax.set_xlim(-0.5, frame.width - 0.5)
    ax.set_ylim(frame.height - 0.5, -0.5)
    if bar:
        _scale_bar(ax, frame)
    if caption:
        page.text(x, y + h + 0.03, caption, size=6.3, color=MUTED)
    return ax


def _colorbar(page: Page, x: float, y: float, w: float, cmap, lo: float, hi: float, label: str,
              *, ticks: Sequence[float] | None = None, extend: str = "neither") -> float:
    """A color bar from (x, y) with its label under its ticks; returns the y below."""
    ax = page.axes(x, y, w, 0.12)
    gradient = np.linspace(0, 1, 256)[None, :]
    ax.imshow(gradient, aspect="auto", cmap=cmap, extent=(lo, hi, 0, 1), interpolation="bilinear")
    ax.set_yticks([])
    ax.tick_params(labelsize=6, colors=MUTED, length=2, width=0.5)
    if ticks is not None:
        ax.set_xticks(list(ticks))
    for spine in ax.spines.values():
        spine.set_linewidth(0.4)
    if extend != "neither":
        page.text(x + w + 0.05, y + 0.06, "beyond the ends: the end's color", size=5.5, color=MUTED,
                  va="center")
    return page.para(x, y + 0.3, label, size=6.3, color=MUTED, width=page.right - x)


def _section_text(spec: ReportSpec, z_um: float) -> str:
    s = spec.section_of(z_um)
    if s is None:
        return "—"
    return f"{round(s):d}" if abs(s - round(s)) < 0.05 else f"{s:.1f}"


# -- the writer ---------------------------------------------------------------------------
@dataclass
class _Numbers:
    """What the front matter needs, gathered while the body is drawn."""

    facts: dict[int, SurfaceFacts] = field(default_factory=dict)
    deviations: dict[int, Mapping[str, Any]] = field(default_factory=dict)
    floors: dict[int, float] = field(default_factory=dict)
    pairs: dict[tuple[int, int], PairResult] = field(default_factory=dict)
    failed: dict[Any, str] = field(default_factory=dict)
    error_scale: float = 0.0


class _Writer:
    def __init__(self, spec: ReportSpec, source: ReportSource, *,
                 preview: Callable[[Page], None] | None = None) -> None:
        self.spec = spec
        self.source = source
        self.plan = Plan(spec)
        self.size = PAGE_SIZES[spec.page]
        self.numbers = _Numbers()
        self.pictures = Pictures(spec.views)
        self.colors = report_colors(spec.renderings)
        self.preview = preview
        self.links: dict[int, list[tuple[tuple[float, float, float, float], int]]] = {}
        copies = len(spec.copies)
        self.footer = (f"Render compare report · c{spec.cell} · {spec.volume}"
                       + (f" · {copies} registrations" if copies > 1
                          else f" · copy {spec.copies[0]}" if spec.copies and spec.copies[0] else "")
                       + (f" · {spec.created}" if spec.created else ""))
        centers, radii, polygons = source.annotations()
        self.centers = np.asarray(centers, dtype=float).reshape(-1, 3)
        self.radii = np.asarray(radii, dtype=float).ravel()
        self.polygons = list(polygons)
        names = source.annotation_names()
        self.names = names
        #: Each copy's annotations and names, for a rendering on another registration
        #: (sources that hold several copies say them through ``annotations_of``).
        self._own_annotations: dict[Any, tuple] = {}
        self._own_names: dict[Any, Any] = {}
        #: What the source raises that ends the whole report, not a page of it.
        self.fatal: tuple[type[BaseException], ...] = tuple(getattr(source, "fatal", ()))
        # One step for each rendering measured, each rendering's pages, each pair, and the front.
        self._steps = len(spec.renderings) * 2 + len(spec.pairs) + 1
        self._step = 0

    # -- progress -----------------------------------------------------------------------
    def _fatal(self, exc: Exception) -> None:
        """Raise ``exc`` again when it ends the report rather than one page: a
        stop, or what the source names (the service stopping under a build)."""
        if isinstance(exc, (progress.Stopped, *self.fatal)):
            raise exc

    def _progress(self, stage: str) -> None:
        progress.check_stop()
        progress.report(stage, min(self._step, self._steps), self._steps)

    def _advance(self) -> None:
        self._step += 1

    @contextlib.contextmanager
    def _within(self, label: str):
        """Report the work inside as part of this step: its stage and count go in
        the text after ``label``, and the count the listener hears stays the
        report's own (step k of n), so a progress bar follows the whole report."""
        outer = progress.current()
        if outer is None:
            yield
            return
        step, steps = min(self._step, self._steps), self._steps

        def relay(stage: str, done: int | None = None, total: int | None = None) -> None:
            count = f" {done:,} of {total:,}" if done is not None and total else ""
            outer(f"{label}{stage}{count}", step, steps)

        with progress.reporting(relay):
            yield

    # -- pages --------------------------------------------------------------------------
    def _page(self, number: int) -> Page:
        return Page(self.size, number, footer=self.footer)

    def _save(self, pdf: PdfPages, page: Page) -> None:
        page.finish()
        if self.preview is not None:
            self.preview(page)
        pdf.savefig(page.fig)
        if page.links:
            self.links.setdefault(page.number, []).extend(page.links)
        page.fig.clear()

    def _name(self, r: int) -> str:
        return f"R{r + 1}"

    def _title(self, r: int) -> str:
        rendering = self.spec.renderings[r]
        return f"R{r + 1} {rendering.label}"

    def _where(self, r: int) -> str:
        rendering = self.spec.renderings[r]
        return f"pane {rendering.pane}, layer {rendering.layer}"

    def _copy_key(self, r: int) -> Any:
        rendering = self.spec.renderings[r]
        return rendering.copy if rendering.copy is not None else self.spec.copy

    def _annotations_for(self, r: int) -> tuple[np.ndarray, np.ndarray, list]:
        """The annotations of the copy rendering ``r`` was built from: centers, radii, outlines."""
        key = self._copy_key(r)
        if key == self.spec.copy or not hasattr(self.source, "annotations_of"):
            return self.centers, self.radii, self.polygons
        if key not in self._own_annotations:
            centers, radii, polygons = self.source.annotations_of(r)
            self._own_annotations[key] = (np.asarray(centers, dtype=float).reshape(-1, 3),
                                          np.asarray(radii, dtype=float).ravel(), list(polygons))
        return self._own_annotations[key]

    def _names_for(self, r: int):
        """The Location IDs and sections of rendering ``r``'s copy's annotations, in its order."""
        key = self._copy_key(r)
        if key == self.spec.copy or not hasattr(self.source, "annotation_names_of"):
            return self.names
        if key not in self._own_names:
            self._own_names[key] = self.source.annotation_names_of(r)
        return self._own_names[key]

    # -- pass 1: every surface, its facts and its error against the annotations ----------
    def measure_renderings(self) -> None:
        scales = []
        for r, rendering in enumerate(self.spec.renderings):
            self._progress(f"{self._name(r)} {rendering.label}: the surface")
            with self._within(f"{self._name(r)} {rendering.label}: "):
                try:
                    mesh = self.source.mesh(r)
                except Exception as exc:  # noqa: BLE001 - a failed rendering is a page that says so
                    self._fatal(exc)
                    self.numbers.failed[("mesh", r)] = str(exc)
                    mesh = None
            if mesh is None or mesh.is_empty:
                self.numbers.failed.setdefault(("mesh", r), "the rendering produced no surface")
                self._advance()
                continue
            self.pictures.add(mesh.vertices)
            self.numbers.facts[r] = surface_facts(mesh)
            if rendering.deviation:
                self._progress(f"{self._name(r)} {rendering.label}: against the annotations")
                with self._within(f"{self._name(r)} against the annotations: "):
                    try:
                        found = self.source.deviation(r)
                    except Exception as exc:  # noqa: BLE001 - as a failed rendering
                        self._fatal(exc)
                        self.numbers.failed[("deviation", r)] = str(exc)
                        found = None
                if found is not None:
                    self.numbers.deviations[r] = dict(found.summary)
                    self.numbers.floors[r] = float(found.map.floor_um)
                    scales.append(float(found.map.scale_um))
                elif ("deviation", r) not in self.numbers.failed:
                    self.numbers.failed[("deviation", r)] = "the cell has no annotations to hold it to"
            self._advance()
        # One scale for every error picture, the largest any rendering suggests,
        # as the viewer's error toggle shares one: the same color is the same microns.
        self.numbers.error_scale = max(scales) if scales else 1.0

    # -- pass 2: the body ------------------------------------------------------------------
    def write_body(self, pdf: PdfPages) -> None:
        for item in self.plan.body:
            if item.kind == "pane":
                self._pane_page(pdf, item)
            elif item.kind == "rendering":
                self._rendering_page(pdf, item)
            elif item.kind == "rendering-annotations":
                self._deviation_page(pdf, item)
            elif item.kind == "pair":
                self._pair_pages(pdf, item)
            elif item.kind == "addendum":
                from .compare_addendum import draw_addendum

                progress.check_stop()
                with _DRAW:
                    page = self._page(item.first)
                    draw_addendum(item.key, page, ADDENDUM_TITLES[item.key], self.spec)
                    self._save(pdf, page)

    def _pane_page(self, pdf: PdfPages, item: Item) -> None:
        spec, plan = self.spec, self.plan
        pane = next(p for p in plan.panes() if p.number == item.key)
        members = plan.pane_renderings(pane.number)
        with _DRAW:
            page = self._page(item.first)
            kind = "Difference" if pane.kind == "difference" else (
                f"{pane.layers or len(members)} layer{'s' if (pane.layers or len(members)) != 1 else ''}")
            y = page.header(f"Pane {pane.number}", f"{kind} · {spec.name}")
            if pane.snapshot:
                rgb = _png_rgb(pane.snapshot)
                if rgb is not None:
                    h_img = min(4.6, page.usable * rgb.shape[0] / max(rgb.shape[1], 1))
                    w_img = h_img * rgb.shape[1] / max(rgb.shape[0], 1)
                    page.image(page.left + (page.usable - w_img) / 2, y, w_img, h_img, rgb)
                    page.text(page.left, y + h_img + 0.04,
                              "The pane as it was on screen when the report was asked for "
                              "(its own camera, colors and opacity).", size=6.5, color=MUTED)
                    y += h_img + 0.3
            else:
                y = page.para(page.left, y, "No picture of the pane was sent with the request.",
                              size=8, color=MUTED) + 0.1
            if pane.kind == "difference" and pane.difference:
                a, b = pane.difference
                pair = (min(a, b), max(a, b))
                target = plan.page("pair", pair)
                y = page.section(y, "What it shows")
                text = (f"This pane colored pane {spec.renderings[a].pane}'s surface "
                        f"({self._title(a)}) by its signed distance to pane "
                        f"{spec.renderings[b].pane}'s ({self._title(b)}). ")
                text += (f"The pair is measured both ways on page {target}." if target else
                         "That pair was left out of this report.")
                y = page.para(page.left, y, text, size=8.5)
                if target:
                    page.link(page.left, y - 0.15, page.usable, 0.15, target)
                y += 0.1
            if members:
                y = page.section(y, "Its renderings in this report")
                rows, swatches, links = [], [], []
                for r in members:
                    rendering = spec.renderings[r]
                    facts = self.numbers.facts.get(r)
                    rows.append([self._name(r), rendering.label, f"layer {rendering.layer}",
                                 _differing_text(spec, r), fmt(facts.n_faces) if facts else "—",
                                 plan.page("rendering", r)])
                    swatches.append(self.colors[r])
                    links.append(plan.page("rendering", r))
                y = page.table(page.left, y, [
                    Column("", 0.45), Column("Technique", 1.9), Column("Layer", 0.6),
                    Column("Settings that set it apart", 3.1), Column("Faces", 0.75, "right"),
                    Column("Page", 0.55, "right")], rows, swatches=swatches, links=links) + 0.18
            involved = [(a, b) for a, b in spec.pairs
                        if spec.renderings[a].pane == pane.number or spec.renderings[b].pane == pane.number]
            if involved:
                y = page.section(y, "Comparisons that involve this pane")
                rows, links = [], []
                for a, b in involved[:28]:
                    rows.append([f"R{a + 1} vs R{b + 1}",
                                 f"{spec.renderings[a].label} (pane {spec.renderings[a].pane}) vs "
                                 f"{spec.renderings[b].label} (pane {spec.renderings[b].pane})",
                                 plan.page("pair", (a, b))])
                    links.append(plan.page("pair", (a, b)))
                y = page.table(page.left, y, [Column("Pair", 0.9), Column("Renderings", 5.85),
                                              Column("Page", 0.55, "right")], rows, links=links,
                               max_y=page.bottom)
                if len(involved) > 28:
                    page.text(page.left, y + 0.05, f"… and {len(involved) - 28} more: see the "
                              "summary's table of every pair.", size=7, color=MUTED)
            self._save(pdf, page)

    def _rendering_page(self, pdf: PdfPages, item: Item) -> None:
        r = item.key
        spec = self.spec
        rendering = spec.renderings[r]
        self._progress(f"{self._name(r)}: its pages")
        failure = self.numbers.failed.get(("mesh", r))
        mesh = None if failure else self.source.mesh(r)
        with _DRAW:
            page = self._page(item.first)
            y = page.header(f"{self._title(r)}", f"{self._where(r).capitalize()} · {spec.name}"
                            + (f" · {spec.registration_of(r)}" if len(spec.copies) > 1 else ""),
                            right="settings and shape")
            page.swatch(page.right - 0.18, 0.2, self.colors[r], size=0.16)
            if str(rendering.color).lower() != self.colors[r]:
                page.swatch(page.right - 0.4, 0.2, rendering.color, size=0.16)
                page.text(page.right - 0.44, 0.28, "on screen · in this report", size=5.5,
                          color=MUTED, ha="right", va="center")
            if rendering.stale:
                y = page.para(page.left, y, "This layer's panel was changed after it was built. "
                              "The report measures the surface it holds, built with the settings "
                              "below; press Compare to build the new ones.", size=7.5,
                              color="#a3471a") + 0.05
            if failure or mesh is None:
                page.para(page.left, y, f"This rendering could not be built: {failure}", size=9,
                          color="#a3471a")
                self._save(pdf, page)
                self._advance()
                return
            # Its appearance from every viewpoint, in its own color.
            n = len(spec.views)
            gap = 0.12
            w = (page.usable - gap * (n - 1)) / n
            h = min(2.3, w * 0.8)
            for k, view in enumerate(spec.views):
                frame = self.pictures.frame(view, w, h)
                pic = Pictures.solid(mesh, frame, self.colors[r])
                _picture(page, page.left + k * (w + gap), y, w, h, pic.to_uint8(), frame,
                         view.label)
            y += h + 0.32
            # Settings on the left, facts on the right.
            y_top = y
            rows = describe_options(rendering.options)
            y = page.section(y, "Settings")
            table_rows, colors = [], []
            for row in rows:
                flag = "changed" if row.changed else ""
                if row.inert:
                    flag = (flag + ", " if flag else "") + "ignored here"
                table_rows.append([row.label, row.text, row.default_text if row.changed else "", flag])
                colors.append("#8a8f96" if row.inert else ("#1d4f9e" if row.changed else None))
            y = page.table(page.left, y, [Column("Option", 1.45), Column("Value", 1.25),
                                          Column("Default", 0.9), Column("", 0.75)], table_rows,
                           colors=colors, size=6.8, max_y=page.bottom - 0.4)
            y = page.para(page.left, y + 0.08,
                          "Blue: changed from the technique's default as the app starts it "
                          "(a person's saved technique defaults are not known here). Gray: "
                          "shown by the panel but not used by this technique.", size=6.2,
                          color=MUTED, width=4.3)
            facts = self.numbers.facts[r]
            x2 = page.left + 4.55
            y2 = y_top
            page.text(x2, y2, "Shape", size=10, weight="bold")
            y2 += 10 * 1.6 / 72.0
            (lo, hi) = facts.bounds_um
            fact_rows = [
                ["Vertices", fmt(facts.n_vertices)],
                ["Triangles", fmt(facts.n_faces)],
                ["Surface area", f"{fmt(facts.area_um2, 4)} µm²"],
                ["Enclosed volume", f"{fmt(facts.volume_um3, 4)} µm³"],
                ["Open edges", fmt(facts.boundary_edges) + (" (closed)" if facts.closed else "")],
                ["Visual bodies", fmt(facts.bodies)],
                ["Creases > 45°", f"{fmt(facts.crease_um_per_um2)} µm/µm²"],
                ["X span", f"{fmt(lo[0], 4)} to {fmt(hi[0], 4)} µm"],
                ["Y span", f"{fmt(lo[1], 4)} to {fmt(hi[1], 4)} µm"],
                ["Z span", f"{fmt(lo[2], 4)} to {fmt(hi[2], 4)} µm"],
                ["Sections", f"{_section_text(spec, lo[2])} to {_section_text(spec, hi[2])}"],
            ]
            y2 = page.table(x2, y2, [Column("", 1.15), Column("", 1.7, "right")], fact_rows,
                            header=False, size=7)
            y2 = page.para(x2, y2 + 0.08, "Volume by the divergence theorem: exact for a "
                           "closed surface; a surface assembled from overlapping pieces counts "
                           "each overlap once per piece. Visual bodies: separate pieces that do "
                           "not come within 0.3 µm of each other (a correct cell is 1).",
                           size=6.2, color=MUTED, width=2.85)
            dev = self.numbers.deviations.get(r)
            if dev:
                y2 += 0.1
                page.text(x2, y2, "Against the annotations", size=10, weight="bold")
                y2 += 10 * 1.6 / 72.0
                target = self.plan.page("rendering-annotations", r)
                y2 = page.table(x2, y2, [Column("", 1.75), Column("", 1.1, "right")], [
                    ["Surface → annotation, mean", fmt_um(dev.get("surface_um"))],
                    ["Annotation rim → surface, mean", fmt_um(dev.get("annotation_um"))],
                    ["Both, pooled mean", fmt_um(dev.get("mean_um"))],
                    ["95th percentile", fmt_um(dev.get("p95_um"))],
                    ["Excess volume", f"{fmt(dev.get('excess_volume_um3'), 4)} µm³"],
                ], header=False, size=7)
                if target:
                    page.text(x2, y2 + 0.06, f"In full on page {target}.", size=7, color=LINK)
                    page.link(x2, y2 + 0.02, 1.6, 0.16, target)
            self._save(pdf, page)
        self._advance()

    def _deviation_page(self, pdf: PdfPages, item: Item) -> None:
        r = item.key
        spec = self.spec
        failure = self.numbers.failed.get(("mesh", r)) or self.numbers.failed.get(("deviation", r))
        mesh = found = None
        if not failure:
            mesh = self.source.mesh(r)
            found = self.source.deviation(r)
            if found is None:
                failure = "the cell has no annotations to hold it to"
        with _DRAW:
            page = self._page(item.first)
            y = page.header(f"{self._title(r)}: against the annotations",
                            f"{self._where(r).capitalize()} · {spec.name}"
                            + (f" · {spec.registration_of(r)}" if len(spec.copies) > 1 else ""),
                            right="error map")
            page.swatch(page.right - 0.18, 0.2, self.colors[r], size=0.16)
            if failure or mesh is None or found is None:
                page.para(page.left, y, f"Not measured: {failure}.", size=9, color="#a3471a")
                self._save(pdf, page)
                return
            dmap, dev = found.map, found.summary
            scale = self.numbers.error_scale
            n = len(spec.views)
            gap = 0.12
            w = (page.usable - gap * (n - 1)) / n
            h = min(2.3, w * 0.8)
            for k, view in enumerate(spec.views):
                frame = self.pictures.frame(view, w, h)
                pic = Pictures.error(mesh, frame, dmap.vertex_um, scale)
                ax = _picture(page, page.left + k * (w + gap), y, w, h, pic.to_uint8(), frame,
                              view.label)
                if k == 0:
                    _mark_places(ax, frame, [s.xyz_um for s in dmap.spots[:8]])
            y += h + 0.28
            y = _colorbar(page, page.left, y, 3.0, ERROR, 0, scale,
                          "Distance from the nearest annotation (µm). The scale is shared by every "
                          "error picture of this report. Numbered rings: the worst places (below).",
                          extend="max") + 0.08
            # The headline numbers.
            hidden = dev.get("hidden_fraction") or 0.0
            bias = dmap.bias
            rows = [
                ["Surface → nearest annotation, mean (area-uniform samples)", fmt_um(dev.get("surface_um"))],
                ["… over the surface that can be seen", fmt_um(dev.get("exterior_um"))],
                ["Hidden surface (buried inside the render itself)", f"{fmt(100 * hidden)} %"],
                ["Annotation rim → surface, mean", fmt_um(dev.get("annotation_um"))],
                ["… over the rims the render does not enclose", fmt_um(dev.get("annotation_exterior_um"))],
                ["Both directions pooled: mean / 95th percentile / largest",
                 f"{fmt(dev.get('mean_um'))} / {fmt(dev.get('p95_um'))} / {fmt_um(dev.get('max_um'))}"],
                ["Excess volume (surface mean × area)", f"{fmt(dev.get('excess_volume_um3'), 4)} µm³"],
                ["Sampling floor (half the median gap between sections)", fmt_um(dmap.floor_um)],
                ["Resolution (surface sample spacing)", fmt_um(dev.get("sample_spacing_um"))],
                ["Annotations held to: discs / traced outlines",
                 f"{fmt(dev.get('n_discs'))} / {fmt(dev.get('n_curves'))}"],
            ]
            if bias is not None:
                rows.append(["Rims inside the render (it is fatter there) / mean d there",
                             f"{fmt(100 * bias.enclosed_share)} % / {fmt_um(bias.enclosed_mean_um)}"])
                rows.append(["Rims outside it (it stops short) / mean d there",
                             f"{fmt(100 * (1 - bias.enclosed_share))} % / {fmt_um(bias.outside_mean_um)}"])
                rows.append(["Signed rim mean (+ fatter, − thinner)", fmt_um(bias.signed_mean_um, signed=True)])
            y = page.table(page.left, y, [Column("Measure", 5.1), Column("Value", 2.3, "right")],
                           rows, size=6.9) + 0.15
            # Distribution and profile.
            cw = (page.usable - 0.5) / 2
            ch = 1.75
            surface = dmap.surface
            if surface is not None:
                ax = page.chart(page.left + 0.3, y + 0.05, cw - 0.3, ch,
                                title="Surface area by distance from the annotations",
                                xlabel="distance (µm)", ylabel="share of the area (%)")
                edges = surface.hist_edges_um
                ax.bar(edges[:-1], 100 * surface.hist_share, width=np.diff(edges), align="edge",
                       color=ink(self.colors[r]), alpha=0.75, lw=0)
                for p, style in ((50, ":"), (95, "--")):
                    q = float(np.interp(p, np.arange(101), surface.quantiles_um))
                    ax.axvline(q, color=INK, lw=0.7, ls=style)
                    ax.text(q, ax.get_ylim()[1] * 0.95, f" p{p} {fmt(q)}", fontsize=5.5, color=INK,
                            va="top")
                if surface.tail_share > 0:
                    ax.text(0.98, 0.8, f"beyond {fmt(edges[-1])} µm: {fmt(100 * surface.tail_share)} %",
                            transform=ax.transAxes, fontsize=5.5, ha="right", color=MUTED)
            zp = dmap.z_profile
            if zp is not None and len(zp.mean_um):
                ax = page.chart(page.left + cw + 0.65, y + 0.05, cw - 0.3, ch,
                                title="Error along Z", xlabel="Z (µm)", ylabel="µm")
                zc = zp.z0_um + (np.arange(len(zp.mean_um)) + 0.5) * zp.step_um
                held = zp.area_um2 > 0
                ax.plot(zc[held], zp.mean_um[held], color=ink(self.colors[r]), lw=0.9, label="surface mean")
                ax.plot(zc[held], zp.p95_um[held], color=ink(self.colors[r]), lw=0.6, ls="--",
                        label="surface p95")
                rim = zp.rim_mean_um > 0
                ax.plot(zc[rim], zp.rim_mean_um[rim], color="#7a3f9d", lw=0.8, label="rim mean")
                if dmap.floor_um > 0:
                    ax.axhline(dmap.floor_um, color=MUTED, lw=0.6, ls=":", label="sampling floor")
                ax.legend(fontsize=5.3, frameon=False, loc="upper right")
                _section_axis(ax, spec)
            y += ch + 0.55
            # The worst places.
            spots = dmap.spots[:8]
            if spots and y < page.bottom - 0.6:
                y = page.section(y, f"The worst places (numbered on the first picture; Location IDs "
                                 f"and sections of {spec.volume})", size=9)
                ids = sections = None
                names = self._names_for(r)
                if names is not None:
                    ids, sections = names
                rows = []
                for k, spot in enumerate(spots):
                    owner = spot.owner
                    loc = (f"{int(ids[owner])}" if ids is not None and 0 <= owner < len(ids) else "—")
                    sec = (f"{int(sections[owner])}" if sections is not None and 0 <= owner < len(sections)
                           else _section_text(spec, spot.xyz_um[2]))
                    rows.append([f"{k + 1}", fmt(spot.xyz_um[0], 4), fmt(spot.xyz_um[1], 4),
                                 fmt(spot.xyz_um[2], 4), _section_text(spec, spot.xyz_um[2]),
                                 loc, sec, f"{fmt(100 * spot.share)} %", fmt(spot.mean_um), fmt(spot.max_um)])
                page.table(page.left, y, [
                    Column("#", 0.3, "right"), Column("X µm", 0.75, "right"), Column("Y µm", 0.75, "right"),
                    Column("Z µm", 0.7, "right"), Column("Section", 0.75, "right"),
                    Column("Nearest annotation", 1.2, "right"),
                    Column("Its section", 0.75, "right"), Column("Excess", 0.65, "right"),
                    Column("Mean µm", 0.6, "right"), Column("Max µm", 0.7, "right")], rows, size=6.6,
                    max_y=page.bottom)
            self._save(pdf, page)

    def _pair_pages(self, pdf: PdfPages, item: Item) -> None:
        a, b = item.key
        spec = self.spec
        ra, rb = spec.renderings[a], spec.renderings[b]
        self._progress(f"R{a + 1} vs R{b + 1}: measuring both ways")
        failure = self.numbers.failed.get(("mesh", a)) or self.numbers.failed.get(("mesh", b))
        result = None
        mesh_a = mesh_b = diff = None
        if not failure:
            try:
                mesh_a, mesh_b = self.source.mesh(a), self.source.mesh(b)
                with self._within(f"R{a + 1} vs R{b + 1}: "):
                    diff = self.source.difference(a, b)
                result = pair_result(a, b, mesh_a, mesh_b, diff, section_of=spec.section_of)
                self.numbers.pairs[(a, b)] = result
            except Exception as exc:  # noqa: BLE001 - a pair that fails is pages that say so
                self._fatal(exc)
                failure = str(exc)
                self.numbers.failed[("pair", (a, b))] = failure
        self._progress(f"R{a + 1} vs R{b + 1}: drawing")
        title = f"R{a + 1} vs R{b + 1}"
        across = self._copy_key(a) != self._copy_key(b)

        def reg(k: int) -> str:
            return f", {_short_registration(spec.registration_of(k))}" if across else ""

        sub = f"{ra.label} ({self._where(a)}{reg(a)}) vs {rb.label} ({self._where(b)}{reg(b)}) · {spec.name}"
        if failure or result is None or diff is None or mesh_a is None or mesh_b is None:
            for k in range(3):
                with _DRAW:
                    page = self._page(item.first + k)
                    y = page.header(title, sub, right=("pictures", "numbers", "where they differ")[k])
                    page.para(page.left, y, f"This pair could not be measured: {failure}.", size=9,
                              color="#a3471a")
                    self._save(pdf, page)
            self._advance()
            return
        with _DRAW:
            self._pair_pictures(pdf, item.first, title, sub, a, b, mesh_a, mesh_b, diff, result)
        with _DRAW:
            self._pair_numbers(pdf, item.first + 1, title, sub, a, b, result)
        with _DRAW:
            self._pair_places(pdf, item.first + 2, title, sub, a, b, mesh_a, mesh_b, diff, result)
        self._advance()

    def _pair_heading(self, page: Page, title: str, sub: str, right: str, a: int, b: int) -> float:
        y = page.header(title, sub, right=right)
        page.swatch(page.right - 0.38, 0.2, self.colors[a], size=0.16)
        page.swatch(page.right - 0.18, 0.2, self.colors[b], size=0.16)
        return y

    def _pair_pictures(self, pdf, number, title, sub, a, b, mesh_a, mesh_b, diff, result) -> None:
        spec = self.spec
        ca, cb = self.colors[a], self.colors[b]
        page = self._page(number)
        y = self._pair_heading(page, title, sub, "pictures", a, b)
        y = page.para(page.left, y, (
            f"Left: A = R{a + 1}, every point of its surface colored by its signed distance to "
            f"B = R{b + 1}'s surface (red: A is outside B; blue: inside; white: they agree). "
            f"Middle: B the same way against A. Right: where their outlines differ seen from that "
            f"viewpoint: R{a + 1}'s color where only A covers the picture, R{b + 1}'s where only B "
            "does, gray where both do."), size=7.3) + 0.05
        scale = result.scale_um
        n = len(spec.views)
        gap = 0.1
        w = (page.usable - 2 * gap) / 3
        room = page.bottom - y - 0.95
        h = min(w * 0.82, room / n - 0.22)
        for view in spec.views:
            frame = self.pictures.frame(view, w, h)
            pa = Pictures.signed(mesh_a, frame, diff.distance_um, scale)
            pb = Pictures.signed(mesh_b, frame, diff.distance_b_um, scale)
            x = page.left
            _picture(page, x, y, w, h, pa.to_uint8(), frame, f"A = R{a + 1} against B · {view.label}")
            _picture(page, x + w + gap, y, w, h, pb.to_uint8(), frame, f"B = R{b + 1} against A")
            over = _overlap(pa.coverage, pb.coverage, ca, cb)
            _picture(page, x + 2 * (w + gap), y, w, h, over, frame, "where the outlines differ")
            px_um2 = frame.um_per_px ** 2
            only_a = float(np.clip(pa.coverage - pb.coverage, 0, None).sum() * px_um2)
            only_b = float(np.clip(pb.coverage - pa.coverage, 0, None).sum() * px_um2)
            union = float(np.maximum(pa.coverage, pb.coverage).sum() * px_um2)
            page.text(x + 2 * (w + gap) + w, y + h + 0.03,
                      f"A only {fmt(100 * only_a / union if union else 0)} %, "
                      f"B only {fmt(100 * only_b / union if union else 0)} %",
                      size=6, color=MUTED, ha="right")
            y += h + 0.24
        _colorbar(page, page.left, y + 0.02, 3.2, DIVERGING, -scale, scale,
                  "Signed distance (µm): blue inside the other surface, red outside, white where they "
                  "agree; ± the larger 98th percentile of |d| of the two directions.", extend="both")
        page.swatch(page.left + 4.6, y + 0.0, ca)
        page.text(page.left + 4.78, y + 0.065, f"R{a + 1} only", size=6.5, va="center")
        page.swatch(page.left + 5.5, y + 0.0, cb)
        page.text(page.left + 5.68, y + 0.065, f"R{b + 1} only", size=6.5, va="center")
        page.rect(page.left + 6.4, y + 0.0, 0.13, 0.13, face=(0.78, 0.78, 0.78), edge="#333", lw=0.5)
        page.text(page.left + 6.58, y + 0.065, "both", size=6.5, va="center")
        self._save(pdf, page)

    def _pair_numbers(self, pdf, number, title, sub, a, b, result: PairResult) -> None:
        spec = self.spec
        ab, ba = result.ab, result.ba
        page = self._page(number)
        y = self._pair_heading(page, title, sub, "numbers", a, b)
        est = 0.5 * (ab.integral_signed_um3 - ba.integral_signed_um3)
        dv = result.volume_a_um3 - result.volume_b_um3

        def both(f: Callable[[FieldStats], Any], *, um: bool = True, signed: bool = False) -> list[str]:
            fn = fmt_um if um else fmt
            return [fn(f(ab), signed=signed), fn(f(ba), signed=signed)]

        rows: list[list[str]] = [
            ["Mean |d| over the surface (area-weighted)", *both(lambda s: s.mean_abs_um),
             fmt_um(result.chamfer_um)],
            ["RMS of d", *both(lambda s: s.rms_um), fmt_um(result.chamfer_rms_um)],
            ["Mean d (+: outside the other surface)", *both(lambda s: s.mean_signed_um, signed=True), ""],
            ["Median |d|", *both(lambda s: s.percentile(50)), ""],
            ["90th percentile of |d|", *both(lambda s: s.percentile(90)), ""],
            ["95th percentile of |d|", *both(lambda s: s.percentile(95)), ""],
            ["99th percentile of |d|", *both(lambda s: s.percentile(99)), ""],
            ["Largest |d|", *both(lambda s: s.max_abs_um), fmt_um(result.hausdorff_um)],
            ["Share of the surface outside the other",
             f"{fmt(100 * ab.outside_share)} %", f"{fmt(100 * ba.outside_share)} %", ""],
        ]
        for k, (t, _share) in enumerate(ab.within):
            rows.append([f"Share of the surface within {fmt(t)} µm",
                         f"{fmt(100 * ab.within[k][1])} %", f"{fmt(100 * ba.within[k][1])} %", ""])
        rows += [
            ["∫ d dA over the surface", f"{fmt(ab.integral_signed_um3, 4, signed=True)} µm³",
             f"{fmt(ba.integral_signed_um3, 4, signed=True)} µm³", ""],
            ["Surface area", f"{fmt(ab.area_um2, 4)} µm²", f"{fmt(ba.area_um2, 4)} µm²", ""],
            ["Vertices measured", fmt(ab.n_vertices), fmt(ba.n_vertices), ""],
        ]
        col = [Column("Measure", 3.35), Column(f"A = R{a + 1} → B", 1.35, "right"),
               Column(f"B = R{b + 1} → A", 1.35, "right"), Column("Both", 1.35, "right")]
        y = page.table(page.left, y, col, rows, size=6.9,
                       bold=[r[0].startswith(("Mean |d|", "Largest")) for r in rows]) + 0.1
        extra = [
            ["Symmetric mean distance (the mean of the two means; 'Chamfer')", fmt_um(result.chamfer_um)],
            ["Hausdorff distance (the larger of the two largest)", fmt_um(result.hausdorff_um)],
            ["Volume A − volume B (divergence theorem)", f"{fmt(dv, 4, signed=True)} µm³"],
            ["… estimated from the distances: ½(∫_A d dA − ∫_B d dA)", f"{fmt(est, 4, signed=True)} µm³"],
            ["Sign decided by", "enclosure (B's solid encloses the point)" if result.sign_by == "enclosure"
             else "the outward normal of the nearest sample"],
            ["Resolution: spacing of the surface samples", fmt_um(result.sample_spacing_um)],
            ["Samples on each surface", fmt(result.n_samples)],
        ]
        if result.overruled_ab or result.overruled_ba:
            extra.append(["Signs set by the winding number (open surface; see A3)",
                          f"{fmt(result.overruled_ab)} of A, {fmt(result.overruled_ba)} of B"])
        y = page.table(page.left, y, [Column("", 4.9), Column("", 2.5, "right")], extra,
                       header=False, size=6.9) + 0.15
        cw = (page.usable - 0.55) / 2
        ch = min(1.6, (page.bottom - y - 1.35) / 2)
        ia, ib = ink(self.colors[a]), ink(self.colors[b])
        # The signed distributions.
        y += 0.12
        ax = page.chart(page.left + 0.32, y, cw - 0.32, ch, title="Signed distance, both ways",
                        xlabel="d (µm)", ylabel="share of the area (%)")
        for stats, color, name in ((ab, ia, f"R{a + 1} → R{b + 1}"), (ba, ib, f"R{b + 1} → R{a + 1}")):
            e = stats.hist_edges_um
            ax.stairs(100 * stats.hist_share, e, color=color, lw=0.9, label=name, fill=False)
            ax.stairs(100 * stats.hist_share, e, color=color, alpha=0.18, fill=True)
        ax.axvline(0, color=INK, lw=0.5)
        ax.legend(fontsize=5.5, frameon=False)
        # How much of each surface lies within a distance.
        ax = page.chart(page.left + cw + 0.55 + 0.32, y, cw - 0.32, ch,
                        title="Share of the surface within a distance",
                        xlabel="|d| (µm)", ylabel="share of the area (%)")
        for stats, color, name in ((ab, ia, f"R{a + 1} → R{b + 1}"), (ba, ib, f"R{b + 1} → R{a + 1}")):
            ax.plot(stats.quantiles_abs_um, np.arange(101), color=color, lw=0.9, label=name)
        ax.set_ylim(0, 100)
        ax.set_xlim(0, max(result.scale_um * 1.6, 1e-3))
        ax.legend(fontsize=5.5, frameon=False, loc="lower right")
        y += ch + 0.85
        # Along Z.
        ax = page.chart(page.left + 0.32, y, cw - 0.32, ch, title="Mean |d| along Z",
                        xlabel="Z (µm)", ylabel="µm")
        for stats, color, name in ((ab, ia, f"R{a + 1} → R{b + 1}"), (ba, ib, f"R{b + 1} → R{a + 1}")):
            held = stats.z.area_um2 > 0
            ax.plot(stats.z.centers_um[held], stats.z.mean_abs_um[held], color=color, lw=0.85, label=name)
            ax.plot(stats.z.centers_um[held], stats.z.p95_abs_um[held], color=color, lw=0.5, ls="--")
        if result.worst_z_um is not None:
            ax.axvline(result.worst_z_um, color="#b23a3a", lw=0.6, ls=":")
        ax.legend(fontsize=5.5, frameon=False)
        _section_axis(ax, spec)
        ax = page.chart(page.left + cw + 0.55 + 0.32, y, cw - 0.32, ch, title="Mean d along Z",
                        xlabel="Z (µm)", ylabel="µm (+ outside the other)")
        for stats, color, name in ((ab, ia, f"R{a + 1} → R{b + 1}"), (ba, ib, f"R{b + 1} → R{a + 1}")):
            held = stats.z.area_um2 > 0
            ax.plot(stats.z.centers_um[held], stats.z.mean_signed_um[held], color=color, lw=0.85,
                    label=name)
        ax.axhline(0, color=INK, lw=0.5)
        ax.legend(fontsize=5.5, frameon=False)
        _section_axis(ax, spec)
        page.text(page.left, y + ch + 0.4, "Dashed: the 95th percentile of |d| in each Z bin. "
                  "Dotted red: the Z where the two disagree most together (cut on the next page).",
                  size=6.2, color=MUTED)
        self._save(pdf, page)

    def _pair_places(self, pdf, number, title, sub, a, b, mesh_a, mesh_b, diff, result) -> None:
        spec = self.spec
        page = self._page(number)
        y = self._pair_heading(page, title, sub, "where they differ", a, b)
        cuts = []
        if result.worst_z_um is not None:
            cuts.append(("where they disagree most", self._snap_z(result.worst_z_um)))
        if result.ab.places:
            z = self._snap_z(result.ab.places[0].xyz_um[2])
            if not cuts or abs(z - cuts[0][1]) > 1e-6:
                cuts.append((f"through R{a + 1}'s worst place", z))
        if result.ba.places and len(cuts) < 2:
            z = self._snap_z(result.ba.places[0].xyz_um[2])
            if not cuts or abs(z - cuts[0][1]) > 1e-6:
                cuts.append((f"through R{b + 1}'s worst place", z))
        cw = (page.usable - 0.3) / 2
        ch = 3.0
        for k, (why, z) in enumerate(cuts[:2]):
            self._cut(page, page.left + k * (cw + 0.3), y, cw, ch, z, why, a, b, mesh_a, mesh_b)
        y += ch + 0.5
        # Close-ups of the worst place each way, in the first view.
        view = spec.views[0]
        zw = (page.usable - 3 * 0.1) / 4
        zh = zw * 0.9
        x = page.left
        for stats, name in ((result.ab, f"R{a + 1} → R{b + 1}"), (result.ba, f"R{b + 1} → R{a + 1}")):
            if not stats.places:
                continue
            centre = np.array(stats.places[0].xyz_um)
            half = max(4.0, 3.0 * stats.places[0].radius_um)
            box = _box_around(centre, half, view)
            frame = self.pictures.frame(view, zw, zh, box=box)
            for mesh, values, label in ((mesh_a, diff.distance_um, f"R{a + 1}"),
                                        (mesh_b, diff.distance_b_um, f"R{b + 1}")):
                faces = _faces_near(mesh, centre, half * 1.2)
                pic = Pictures.signed(mesh, frame, values, result.scale_um, faces=faces)
                _picture(page, x, y, zw, zh, pic.to_uint8(), frame,
                         f"{label}, close-up of {name}'s worst place")
                x += zw + 0.1
        y += zh + 0.3
        # The places, both ways.
        for stats, name in ((result.ab, f"R{a + 1} → R{b + 1}: the places of A farthest from B"),
                            (result.ba, f"R{b + 1} → R{a + 1}: the places of B farthest from A")):
            if y > page.bottom - 0.5:
                break
            y = page.section(y, name, size=8.5)
            rows = [[f"{k + 1}", fmt(p.xyz_um[0], 4), fmt(p.xyz_um[1], 4), fmt(p.xyz_um[2], 4),
                     _section_text(spec, p.xyz_um[2]), fmt(p.mean_signed_um, signed=True),
                     fmt(p.max_abs_um), f"{fmt(100 * p.share)} %"]
                    for k, p in enumerate(stats.places[:6])]
            y = page.table(page.left, y, [
                Column("#", 0.3, "right"), Column("X µm", 0.95, "right"), Column("Y µm", 0.95, "right"),
                Column("Z µm", 0.85, "right"), Column(f"Section ({spec.volume})", 1.6, "right"),
                Column("Mean d µm", 0.9, "right"), Column("Max |d| µm", 0.9, "right"),
                Column("Share of ∫|d|dA", 0.95, "right")], rows, size=6.6,
                max_y=page.bottom) + 0.12
        self._save(pdf, page)

    def _snap_z(self, z: float) -> float:
        """The annotated section's Z nearest ``z`` (cut where annotations lie), or ``z``."""
        zs = np.concatenate([self.centers[:, 2], [float(zz) for _, zz in self.polygons]])
        if not len(zs):
            return float(z)
        return float(zs[int(np.argmin(np.abs(zs - z)))])

    def _cut(self, page: Page, x: float, y: float, w: float, h: float, z: float, why: str,
             a: int, b: int, mesh_a: Mesh, mesh_b: Mesh) -> None:
        spec = self.spec
        ax = page.chart(x + 0.3, y + 0.2, w - 0.3, h - 0.6,
                        title=f"Cut at Z {fmt(z, 4)} µm, section {_section_text(spec, z)} ({why})",
                        xlabel="X (µm)", ylabel="Y (µm)")
        tol = 0.5 * (spec.z_scale_nm or 100.0) / 1000.0
        pts: list[np.ndarray] = []
        # The annotations of A's copy, filled; of B's, as dashed outlines in B's color
        # when B was built from another registration (they sit where it put them).
        sets: list[tuple[tuple, dict[str, Any]]] = [
            (self._annotations_for(a), {"facecolor": "#d9dde3", "edgecolor": "#6b7380", "lw": 0.5})]
        across = self._copy_key(a) != self._copy_key(b)
        if across:
            sets.append((self._annotations_for(b), {"facecolor": "none", "edgecolor": ink(self.colors[b]),
                                                    "lw": 0.6, "linestyle": (0, (2, 1.5))}))
        for (centers, radii, polygons), style in sets:
            near = np.abs(centers[:, 2] - z) <= tol
            for c, r in zip(centers[near], radii[near], strict=True):
                ax.add_patch(Circle((c[0], c[1]), r, zorder=1, **style))
                pts.append(np.array([[c[0] - r, c[1] - r], [c[0] + r, c[1] + r]]))
            for ring, zz in polygons:
                if abs(float(zz) - z) <= tol and len(ring) >= 3:
                    ax.add_patch(Polygon(np.asarray(ring)[:, :2], closed=True, zorder=1, **style))
                    pts.append(np.asarray(ring)[:, :2])
        for mesh, color, name in ((mesh_a, ink(self.colors[a]), f"R{a + 1}"),
                                  (mesh_b, ink(self.colors[b]), f"R{b + 1}")):
            seg = section_segments(mesh.vertices, mesh.faces, z + 1e-7)
            if len(seg):
                ax.add_collection(LineCollection(list(seg), colors=color, linewidths=0.9, zorder=3,
                                                 label=name))
                pts.append(seg.reshape(-1, 2))
        if pts:
            allp = np.vstack(pts)
            lo, hi = allp.min(axis=0), allp.max(axis=0)
            pad = 0.06 * max(hi - lo) + 0.5
            ax.set_xlim(lo[0] - pad, hi[0] + pad)
            ax.set_ylim(lo[1] - pad, hi[1] + pad)
        ax.set_aspect("equal", adjustable="box")
        ax.legend(fontsize=5.5, frameon=False, loc="upper right")
        page.para(x + 0.3, y + h - 0.2, "Gray: the annotations in this section (discs, traced "
                  "outlines)" + (f" as R{a + 1}'s registration places them; dashed, as R{b + 1}'s"
                                 if across else "") + ". Lines: where each surface crosses the section plane.",
                  size=5.8, color=MUTED, width=w - 0.3)

    # -- pass 3: the front matter ------------------------------------------------------------
    def write_front(self, pdf: PdfPages) -> None:
        for item in self.plan.front:
            with _DRAW:
                if item.kind == "cover":
                    self._cover(pdf, item)
                elif item.kind == "contents":
                    self._contents(pdf, item)
                elif item.kind == "summary-renderings":
                    self._summary_renderings(pdf, item)
                elif item.kind == "summary-registrations":
                    self._summary_registrations(pdf, item)
                elif item.kind == "summary-matrices":
                    self._summary_matrices(pdf, item)
                elif item.kind == "summary-pairs":
                    self._summary_pairs(pdf, item)
                elif item.kind == "summary-annotations":
                    self._summary_annotations(pdf, item)
                elif item.kind == "summary-shape":
                    self._summary_shape(pdf, item)

    def _cover(self, pdf: PdfPages, item: Item) -> None:
        spec, plan = self.spec, self.plan
        page = self._page(item.first)
        page.text(page.left, 0.75, "Render compare report", size=24, weight="bold")
        page.text(page.left, 1.2, f"c{spec.cell} · {spec.volume}", size=15, color="#2b3440")
        y = 1.6
        lines = [
            f"Volume: {spec.volume} ({spec.source})"
            + (f" · {len(spec.copies)} registrations of the cell (below)" if len(spec.copies) > 1
               else f" · copy {spec.copy}" if spec.copy else ""),
            f"Made: {spec.created or 'not given'}" + (f" · SBFSEM-tools {spec.build}" if spec.build else ""),
            f"{len(spec.renderings)} rendering{'s' if len(spec.renderings) != 1 else ''} in "
            f"{len(plan.panes())} pane{'s' if len(plan.panes()) != 1 else ''} · "
            f"{len(spec.pairs)} pair{'s' if len(spec.pairs) != 1 else ''} measured both ways · "
            f"{plan.total} pages",
            "Viewpoints: " + "; ".join(v.label for v in spec.views),
            "Sign of a difference: " + ("enclosure (exact: whether the other surface's solid "
                                        "encloses the point)" if spec.sign == "enclosure"
                                        else "the outward normal of the nearest sample (faster)"),
        ]
        for line in lines:
            page.text(page.left, y, line, size=9, color=INK)
            y += 0.2
        if len(spec.copies) > 1:
            names = dict.fromkeys(spec.registration_of(r) for r in range(len(spec.renderings)))
            y = page.para(page.left, y, "Registrations: " + "; ".join(names) + ". Each rendering is "
                          "built from, and held to, the annotations of its own registration of the cell; "
                          "a pair across two registrations holds each surface to its own.", size=8.5) + 0.02
        y += 0.1
        y = page.para(page.left, y, (
            "Every rendering is a layer of a Render compare pane, measured as it was built. "
            "Each is held to the cell's annotations (the discs in their section planes and the traced "
            "outlines), and every chosen pair to each other both ways, whether or not a Difference "
            "pane showed it. Distances are in microns; every Z is also given as a section of the "
            "volume named above. The addendum defines every number with the formula the code "
            "computes."), size=8.5) + 0.1
        y = page.section(y, "Renderings")
        rows, swatches, links = [], [], []
        for r, rendering in enumerate(spec.renderings):
            facts = self.numbers.facts.get(r)
            rows.append([self._name(r), rendering.label, f"{rendering.pane}·{rendering.layer}",
                         _differing_text(spec, r), fmt(facts.n_faces) if facts else "failed",
                         plan.page("rendering", r)])
            swatches.append(self.colors[r])
            links.append(plan.page("rendering", r))
        y = page.table(page.left, y, [Column("", 0.45), Column("Technique", 2.0),
                                      Column("Pane·layer", 0.75), Column("Settings that set it apart", 2.9),
                                      Column("Faces", 0.75, "right"), Column("Page", 0.55, "right")],
                       rows, swatches=swatches, links=links, size=7, max_y=page.bottom - 2.6) + 0.15
        shots = [(p.number, rgb) for p in plan.panes()
                 if p.snapshot and (rgb := _png_rgb(p.snapshot)) is not None]
        if shots and y < page.bottom - 1.2:
            cols = 3 if len(shots) > 4 else min(2, len(shots)) if len(shots) > 1 else 1
            rows_n = math.ceil(len(shots) / cols)
            gw = (page.usable - 0.1 * (cols - 1)) / cols
            gh = min((page.bottom - y - 0.2) / rows_n - 0.2, gw * 0.7)
            for k, (n, rgb) in enumerate(shots):
                cx = page.left + (k % cols) * (gw + 0.1)
                cy = y + (k // cols) * (gh + 0.2)
                fit_h = min(gh, gw * rgb.shape[0] / max(rgb.shape[1], 1))
                fit_w = fit_h * rgb.shape[1] / max(rgb.shape[0], 1)
                page.image(cx + (gw - fit_w) / 2, cy, fit_w, fit_h, rgb)
                page.text(cx, cy + fit_h + 0.02, f"Pane {n}", size=6.5, color=MUTED)
                target = plan.page("pane", n)
                if target:
                    page.link(cx, cy, gw, fit_h, target)
        self._save(pdf, page)

    def _contents(self, pdf: PdfPages, item: Item) -> None:
        plan = self.plan
        entries = [i for i in [*plan.front, *plan.body] if i.kind not in ("cover", "contents")]
        for k in range(item.pages):
            page = self._page(item.first + k)
            y = page.header("Contents" + (" (continued)" if k else ""), self.spec.name)
            row_h = 0.185
            for entry in entries[k * CONTENTS_ROWS:(k + 1) * CONTENTS_ROWS]:
                indent = 0.25 * entry.level
                bold = entry.level == 0 and entry.kind in ("pane",)
                page.text(page.left + indent, y, entry.title, size=8, weight="bold" if bold else "normal")
                page.text(page.right, y, f"{entry.first}", size=8, color=LINK, ha="right")
                page.link(page.left, y - 0.02, page.usable, row_h - 0.01, entry.first)
                y += row_h
            self._save(pdf, page)

    def _summary_renderings(self, pdf: PdfPages, item: Item) -> None:
        spec, plan = self.spec, self.plan
        for k in range(item.pages):
            page = self._page(item.first + k)
            y = page.header("Summary: the renderings" + (" (continued)" if k else ""), spec.name)
            if not k:
                y = page.para(page.left, y, (
                    "Size and shape of each rendering, and its error against the annotations: "
                    "'surface' is the mean distance from its surface to the nearest annotation "
                    "(surface the annotations do not support), 'rims' the mean distance from the "
                    "annotations' rims to its surface (annotations it does not reach). Lower is "
                    "closer to the annotations in both."), size=7.8) + 0.08
            rows, swatches, links = [], [], []
            for r in range(k * RENDERING_ROWS, min(len(spec.renderings), (k + 1) * RENDERING_ROWS)):
                rendering = spec.renderings[r]
                facts = self.numbers.facts.get(r)
                dev = self.numbers.deviations.get(r, {})
                rows.append([self._name(r), rendering.label, f"{rendering.pane}·{rendering.layer}",
                             fmt(facts.n_faces) if facts else "failed",
                             fmt(facts.area_um2, 4) if facts else "—",
                             fmt(facts.volume_um3, 4) if facts else "—",
                             fmt(facts.bodies) if facts else "—",
                             fmt(dev.get("surface_um")), fmt(dev.get("annotation_um")),
                             fmt(dev.get("excess_volume_um3"), 4),
                             plan.page("rendering", r)])
                swatches.append(self.colors[r])
                links.append(plan.page("rendering", r))
            y = page.table(page.left, y, [
                Column("", 0.45), Column("Technique", 1.6), Column("Pane·layer", 0.65),
                Column("Faces", 0.65, "right"), Column("Area µm²", 0.7, "right"),
                Column("Volume µm³", 0.75, "right"), Column("Bodies", 0.45, "right"),
                Column("Surface µm", 0.62, "right"), Column("Rims µm", 0.55, "right"),
                Column("Excess µm³", 0.63, "right"), Column("Page", 0.35, "right")],
                rows, swatches=swatches, links=links, size=6.7) + 0.15
            if not k:
                y = page.section(y, "Settings that set the renderings apart", size=9)
                for r in range(len(spec.renderings)):
                    if y > page.bottom - 0.2:
                        break
                    page.swatch(page.left, y + 0.01, self.colors[r], size=0.11)
                    y = page.para(page.left + 0.18, y, f"{self._name(r)} {spec.renderings[r].label}: "
                                  f"{_settings_line(spec.renderings[r].options)}", size=6.6,
                                  width=page.usable - 0.18) + 0.02
            self._save(pdf, page)

    def _matrix(self, page: Page, x: float, y: float, side: float, title: str, values: np.ndarray,
                cmap, norm, *, number: Callable[[float], str], note: str) -> None:
        n = values.shape[0]
        plan = self.plan
        ax = page.axes(x + 0.35, y + 0.42, side, side)
        ax.set_xlim(0, n)
        ax.set_ylim(n, 0)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_linewidth(0.4)
        cell = side / n
        size = 6.4 if n <= 6 else 5.4 if n <= 10 else 4.2 if n <= 16 else 0
        for i in range(n):
            for j in range(n):
                v = values[i, j]
                if i == j:
                    ax.add_patch(Rectangle((j, i), 1, 1, facecolor="#eceef1", edgecolor="white", lw=0.4))
                    continue
                if not np.isfinite(v):
                    ax.add_patch(Rectangle((j, i), 1, 1, facecolor="white", edgecolor="#e5e7eb", lw=0.4,
                                           hatch="////" if n <= 12 else None))
                    continue
                rgba = cmap(norm(v))
                ax.add_patch(Rectangle((j, i), 1, 1, facecolor=rgba, edgecolor="white", lw=0.4))
                if size:
                    lum = 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]
                    ax.text(j + 0.5, i + 0.5, number(v), fontsize=size, ha="center", va="center",
                            color="white" if lum < 0.5 else INK)
                target = plan.page("pair", (min(i, j), max(i, j)))
                if target:
                    page.link(x + 0.35 + j * cell, y + 0.42 + i * cell, cell, cell, target)
        label = 5.5 if n <= 12 else 4.3
        for i in range(n):
            color = ink(self.colors[i])
            ax.text(-0.15, i + 0.5, f"R{i + 1}", fontsize=label, ha="right", va="center", color=color,
                    clip_on=False, fontweight="bold")
            ax.text(i + 0.5, -0.15, f"R{i + 1}", fontsize=label, ha="center", va="bottom", color=color,
                    clip_on=False, fontweight="bold", rotation=90 if n > 12 else 0)
        page.text(x, y, title, size=8, weight="bold")
        cax = page.axes(x + 0.35, y + 0.5 + side, side, 0.09)
        gradient = np.linspace(norm.vmin, norm.vmax, 256)[None, :]
        cax.imshow(gradient, aspect="auto", cmap=cmap, norm=norm,
                   extent=(norm.vmin, norm.vmax, 0, 1), interpolation="bilinear")
        cax.set_yticks([])
        cax.tick_params(labelsize=5.5, colors=MUTED, length=1.5, width=0.4)
        page.text(x + 0.35, y + 0.82 + side, note, size=5.8, color=MUTED)

    def _summary_matrices(self, pdf: PdfPages, item: Item) -> None:
        spec = self.spec
        n = len(spec.renderings)
        page = self._page(item.first)
        y = page.header("Summary: pairwise error", spec.name)
        y = page.para(page.left, y, (
            "Each cell is a pair of renderings (row, column); a click on it opens the pair's pages. "
            "Hatched: not measured in this report. The left two are symmetric. In the right two "
            "the row is measured against the column: the row's surface points, each one's distance "
            "to the column's surface."), size=7.6) + 0.05
        sym = np.full((n, n), np.nan)
        haus = np.full((n, n), np.nan)
        signed = np.full((n, n), np.nan)
        p95 = np.full((n, n), np.nan)
        for (a, b), res in self.numbers.pairs.items():
            sym[a, b] = sym[b, a] = res.chamfer_um
            haus[a, b] = haus[b, a] = res.hausdorff_um
            signed[a, b], signed[b, a] = res.ab.mean_signed_um, res.ba.mean_signed_um
            p95[a, b], p95[b, a] = res.ab.percentile(95), res.ba.percentile(95)
        side = min(2.75, (page.usable - 1.2) / 2, (page.bottom - y - 2.6) / 2)
        import matplotlib

        seq = matplotlib.colormaps["YlOrRd"]

        def norm_of(values: np.ndarray, *, centred: bool = False) -> Normalize:
            finite = values[np.isfinite(values)]
            top = float(np.abs(finite).max()) if len(finite) else 1.0
            top = top if top > 0 else 1.0
            return Normalize(-top, top) if centred else Normalize(0, top)

        x1, x2 = page.left, page.left + page.usable / 2 + 0.1
        self._matrix(page, x1, y, side, "Symmetric mean distance (µm)", sym, seq, norm_of(sym),
                     number=lambda v: fmt(v, 2), note="½(mean |d| A→B + mean |d| B→A), area-weighted")
        self._matrix(page, x2, y, side, "Hausdorff distance (µm)", haus, seq, norm_of(haus),
                     number=lambda v: fmt(v, 2), note="the largest distance either way")
        y += side + 1.25
        self._matrix(page, x1, y, side, "Mean signed distance, row against column (µm)", signed,
                     DIVERGING, norm_of(signed, centred=True), number=lambda v: fmt(v, 2, signed=True),
                     note="red: the row's surface lies outside the column's on average")
        self._matrix(page, x2, y, side, "95th percentile of |d|, row against column (µm)", p95, seq,
                     norm_of(p95), number=lambda v: fmt(v, 2), note="95 % of the row's surface is closer")
        self._save(pdf, page)

    def _summary_pairs(self, pdf: PdfPages, item: Item) -> None:
        spec, plan = self.spec, self.plan
        ranked = sorted(spec.pairs, key=lambda p: -(self.numbers.pairs[p].chamfer_um
                                                    if p in self.numbers.pairs else -1.0))
        for k in range(item.pages):
            page = self._page(item.first + k)
            y = page.header("Summary: every pair, most different first" + (" (continued)" if k else ""),
                            spec.name)
            rows: list[list[Any]] = []
            links: list[int | None] = []
            swatches: list[str | None] = []
            for rank, (a, b) in enumerate(ranked[k * PAIR_ROWS:(k + 1) * PAIR_ROWS], start=k * PAIR_ROWS + 1):
                res = self.numbers.pairs.get((a, b))
                if res is None:
                    rows.append([f"{rank}", f"R{a + 1}–R{b + 1}", spec.renderings[a].label,
                                 spec.renderings[b].label, "failed", "", "", "", "", "",
                                 plan.page("pair", (a, b))])
                else:
                    est = 0.5 * (res.ab.integral_signed_um3 - res.ba.integral_signed_um3)
                    rows.append([f"{rank}", f"R{a + 1}–R{b + 1}", spec.renderings[a].label,
                                 spec.renderings[b].label, fmt(res.chamfer_um), fmt(res.hausdorff_um),
                                 fmt(res.ab.mean_signed_um, signed=True), fmt(res.ab.percentile(95)),
                                 fmt(res.ba.percentile(95)), fmt(est, 3, signed=True),
                                 plan.page("pair", (a, b))])
                links.append(plan.page("pair", (a, b)))
                swatches.append(None)
            page.table(page.left, y, [
                Column("#", 0.3, "right"), Column("Pair", 0.7), Column("A", 1.45), Column("B", 1.45),
                Column("Mean µm", 0.55, "right"), Column("Hausd.", 0.5, "right"),
                Column("d A→B", 0.55, "right"), Column("p95 A→B", 0.55, "right"),
                Column("p95 B→A", 0.55, "right"), Column("ΔV µm³", 0.55, "right"),
                Column("Page", 0.25, "right")], rows, links=links, size=6.4)
            page.text(page.left, page.bottom - 0.05, "Mean: the symmetric mean distance. Hausd.: the "
                      "Hausdorff distance. d A→B: A's mean signed distance to B (+: A outside). ΔV: "
                      "½(∫_A d dA − ∫_B d dA), A's volume minus B's estimated from the distances.",
                      size=6, color=MUTED, va="bottom")
            self._save(pdf, page)

    def _summary_annotations(self, pdf: PdfPages, item: Item) -> None:
        spec, plan = self.spec, self.plan
        page = self._page(item.first)
        y = page.header("Summary: against the annotations", spec.name)
        held = [r for r in range(len(spec.renderings)) if r in self.numbers.deviations]
        y = page.para(page.left, y, (
            "Lower is closer to the annotations. 'Surface → annotation' scores surface no "
            "annotation supports (a bulge past the last section, a sweep through unannotated "
            "tissue); 'rim → surface' scores annotations the surface does not reach (a dropped "
            "disc, a body too thin). The sampling floor is what a perfect render still shows "
            "between sections."), size=7.8) + 0.1
        if held:
            ch = min(0.22 * len(held) + 0.6, 4.2)
            y += 0.15
            ax = page.chart(page.left + 1.2, y, page.usable - 1.3, ch,
                            title="Mean distance (µm)", xlabel="µm")
            pos = np.arange(len(held))
            surface = [self.numbers.deviations[r].get("surface_um") or 0.0 for r in held]
            rims = [self.numbers.deviations[r].get("annotation_um") or 0.0 for r in held]
            colors = [self.colors[r] for r in held]
            ax.barh(pos - 0.2, surface, height=0.38, color=colors, edgecolor=[ink(c) for c in colors],
                    lw=0.5, label="surface → annotation")
            ax.barh(pos + 0.2, rims, height=0.38, color="white", edgecolor=[ink(c) for c in colors],
                    lw=0.8, hatch="////", label="annotation rim → surface")
            floor = max(self.numbers.floors.get(r, 0.0) for r in held)
            if floor > 0:
                ax.axvline(floor, color=MUTED, lw=0.6, ls=":", label="sampling floor")
            ax.set_yticks(pos)
            ax.set_yticklabels([f"R{r + 1} {spec.renderings[r].label}" for r in held], fontsize=6)
            ax.invert_yaxis()
            ax.legend(fontsize=5.8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.32),
                      ncol=3)
            y += ch + 0.75
            rows, links, swatches = [], [], []
            for r in sorted(held, key=lambda i: self.numbers.deviations[i].get("mean_um") or 0.0):
                dev = self.numbers.deviations[r]
                rows.append([self._name(r), spec.renderings[r].label, fmt(dev.get("surface_um")),
                             fmt(dev.get("exterior_um")), f"{fmt(100 * (dev.get('hidden_fraction') or 0))} %",
                             fmt(dev.get("annotation_um")), fmt(dev.get("p95_um")), fmt(dev.get("max_um")),
                             fmt(dev.get("excess_volume_um3"), 4),
                             plan.page("rendering-annotations", r)])
                links.append(plan.page("rendering-annotations", r))
                swatches.append(self.colors[r])
            page.table(page.left, y, [
                Column("", 0.45), Column("Technique", 1.75), Column("Surface", 0.6, "right"),
                Column("Seen", 0.55, "right"), Column("Hidden", 0.6, "right"),
                Column("Rims", 0.55, "right"), Column("p95", 0.55, "right"), Column("Max", 0.55, "right"),
                Column("Excess µm³", 0.8, "right"), Column("Page", 0.5, "right")],
                rows, links=links, swatches=swatches, size=6.7, max_y=page.bottom)
        self._save(pdf, page)

    def _summary_registrations(self, pdf: PdfPages, item: Item) -> None:
        """The registrations the renderings were built from, side by side: what each moved
        of the cell's own annotations, and how far linked annotations on neighboring
        sections step sideways at its positions (``source.registration_facts``)."""
        spec = self.spec
        page = self._page(item.first)
        y = page.header("Summary: the registrations", spec.name)
        y = page.para(page.left, y, (
            f"The renderings were built from {len(spec.copies)} registrations of the cell. A registration "
            "places Viking's annotations at their stored positions or through the volume's current section "
            "transforms, then moves them by Viking's section correction (none, Residual field, Curve fit or "
            "Field + fit). Each rendering is held to its own registration's annotations. Below: what each "
            "registration moved of this cell's own annotations, and how far each link between two of them on "
            "different sections steps sideways at its positions."), size=7.8) + 0.08
        facts_of = getattr(self.source, "registration_facts", None)
        try:
            facts = facts_of() if facts_of is not None else None
        except Exception as exc:  # noqa: BLE001 - a summary that fails is a line that says so
            self._fatal(exc)
            facts = None
            y = page.para(page.left, y, f"Not measured: {exc}.", size=9, color="#a3471a")
        if not facts:
            if facts_of is None:
                page.para(page.left, y, "This report's source gives no registration facts.", size=9,
                          color=MUTED)
            self._save(pdf, page)
            return

        def stats(pair: Mapping[str, Any] | None) -> list[str]:
            if not pair or not pair.get("n_moved"):
                return ["0", "—", "—", "—"]
            return [fmt(pair.get("n_moved")), fmt(pair.get("median_um")), fmt(pair.get("p95_um")),
                    fmt(pair.get("max_um"))]

        def name(entry: Mapping[str, Any]) -> str:
            first = entry["renderings"][0]
            text = spec.registration_of(first)
            status = entry.get("correction") or {}
            if status.get("requested") and not status.get("applied"):
                text += " (correction not applied)"
            return text

        def renderings(entry: Mapping[str, Any]) -> str:
            return ", ".join(f"R{k + 1}" for k in entry["renderings"])

        y = page.section(y, "What each registration moved (the cell's own annotations, µm)", size=9)
        rows = []
        for entry in facts:
            pairs = entry.get("pairs") or {}
            rows.append([name(entry), renderings(entry), *stats(pairs.get("transforms")),
                         *stats(pairs.get("correction"))])
        y = page.table(page.left, y, [
            Column("Registration", 2.05), Column("Renderings", 0.8),
            Column("Transforms n", 0.8, "right"), Column("median", 0.5, "right"),
            Column("p95", 0.45, "right"), Column("max", 0.45, "right"),
            Column("Correction n", 0.8, "right"), Column("median", 0.5, "right"),
            Column("p95", 0.45, "right"), Column("max", 0.45, "right")], rows, size=6.6) + 0.04
        y = page.para(page.left, y, "n: how many of the cell's own annotations each moved (by more than "
                      "0.5 nm); then the median, 95th percentile and largest of those moves.", size=6.6,
                      color=MUTED) + 0.1

        y = page.section(y, "Along the links: how far each link steps sideways between its two sections (µm)",
                         size=9)
        rows = []
        steps: list[tuple[str, float, float]] = []
        for entry in facts:
            links = entry.get("links") or {}
            at = (links.get("stages") or {}).get("corrected") or {}
            pulls = entry.get("pulls")
            rows.append([name(entry), fmt(links.get("n_links")), fmt(at.get("median_um")), fmt(at.get("p90_um")),
                         fmt(at.get("p99_um")), fmt(at.get("max_um")), fmt(at.get("n_over_1p5_um")),
                         fmt(entry.get("coherent_sections")), "—" if pulls is None else fmt(pulls)])
            if at:
                steps.append((renderings(entry), float(at.get("median_um", 0.0)), float(at.get("p90_um", 0.0))))
        y = page.table(page.left, y, [
            Column("Registration", 2.15), Column("Links", 0.55, "right"), Column("Median", 0.6, "right"),
            Column("p90", 0.55, "right"), Column("p99", 0.55, "right"), Column("Max", 0.55, "right"),
            Column("Over 1.5", 0.6, "right"), Column("Sections as one", 0.95, "right"),
            Column("Fit pulls", 0.7, "right")], rows, size=6.6) + 0.12
        if steps and y < page.bottom - 2.4:
            ax = page.chart(page.left + 0.9, y + 0.15, page.usable - 1.1, min(2.0, 0.3 * len(steps) + 0.7),
                            title="Link steps at each registration's positions (µm): median and 90th percentile")
            pos = np.arange(len(steps))
            ax.barh(pos - 0.18, [m for _, m, _ in steps], height=0.34, color="#4c78a8", label="median")
            ax.barh(pos + 0.18, [p for _, _, p in steps], height=0.34, color="#f2a541", label="p90")
            ax.set_yticks(pos)
            ax.set_yticklabels([label for label, _, _ in steps], fontsize=6)
            ax.invert_yaxis()
            ax.legend(fontsize=5.5, frameon=False, loc="lower right")
            y += min(2.0, 0.3 * len(steps) + 0.7) + 0.6
        page.para(page.left, y, (
            "Lower steps mean linked annotations on neighboring sections line up better. Read them with "
            "care: the curve fit moves each annotation onto the path through its neighbors, so it lowers "
            "these steps by construction, and a lower number under Curve fit or Field + fit is partly what "
            "the fit does, not proof on its own that the registration is truer. 'Sections as one' counts "
            "sections whose annotations of this cell all moved together (at least 4, one way, 0.2 µm or "
            "more on average), the mark of a whole section re-aligned. 'Fit pulls' are annotations the fit "
            f"pulled toward a linked annotation at least {fmt(facts[0].get('pull_ratio'))}× as wide, such as a "
            "thin process dragged toward a soma (the Settings tab's Correction log); — where no fit ran."),
            size=7.2, color=MUTED)
        self._save(pdf, page)

    def _summary_shape(self, pdf: PdfPages, item: Item) -> None:
        spec = self.spec
        page = self._page(item.first)
        y = page.header("Summary: size and shape", spec.name)
        held = [r for r in range(len(spec.renderings)) if r in self.numbers.facts]
        if held:
            cw = (page.usable - 0.3) / 2
            ch = min(0.2 * len(held) + 0.7, 3.6)
            colors = [self.colors[r] for r in held]
            names = [f"R{r + 1}" for r in held]
            charts: tuple[tuple[str, list[float]], ...] = (
                ("Enclosed volume (µm³)", [self.numbers.facts[r].volume_um3 for r in held]),
                ("Surface area (µm²)", [self.numbers.facts[r].area_um2 for r in held]),
                ("Triangles", [self.numbers.facts[r].n_faces for r in held]),
                ("Creases > 45° (µm/µm²)", [self.numbers.facts[r].crease_um_per_um2 for r in held]),
            )
            for k, (title, values) in enumerate(charts):
                cx = page.left + 0.45 + (k % 2) * (cw + 0.3)
                cy = y + (k // 2) * (ch + 0.55)
                ax = page.chart(cx, cy, cw - 0.45, ch, title=title)
                pos = np.arange(len(held))
                ax.barh(pos, values, color=colors, edgecolor=[ink(c) for c in colors], lw=0.5)
                ax.set_yticks(pos)
                ax.set_yticklabels(names, fontsize=6)
                ax.invert_yaxis()
                for p, v in zip(pos, values, strict=True):
                    ax.text(v, p, f" {fmt(v, 4)}", fontsize=5.2, va="center", color=INK)
            y += 2 * (ch + 0.55) + 0.05
            rows = []
            for r in held:
                f = self.numbers.facts[r]
                rows.append([self._name(r), spec.renderings[r].label, fmt(f.n_vertices), fmt(f.n_faces),
                             fmt(f.boundary_edges), fmt(f.bodies), fmt(f.crease_um_per_um2)])
            page.table(page.left, y, [
                Column("", 0.45), Column("Technique", 2.3), Column("Vertices", 0.9, "right"),
                Column("Triangles", 0.9, "right"), Column("Open edges", 0.9, "right"),
                Column("Bodies", 0.7, "right"), Column("Creases", 0.9, "right")],
                rows, swatches=[self.colors[r] for r in held], size=6.7, max_y=page.bottom)
        self._save(pdf, page)


# -- helpers -----------------------------------------------------------------------------
def report_colors(renderings: Sequence[ReportRendering]) -> list[str]:
    """One color per rendering: the layers' own when no two share one, else
    :data:`REPORT_PALETTE` in order (each rendering's page shows its color on
    screen beside it)."""
    own = [str(r.color).lower() for r in renderings]
    if len(set(own)) == len(own):
        return own
    return [REPORT_PALETTE[k % len(REPORT_PALETTE)] for k in range(len(renderings))]


def _png_rgb(data: bytes | None) -> np.ndarray | None:
    """A PNG's or JPEG's pixels as RGB uint8 (alpha over the viewer's background),
    or None for one that will not open or is larger than :data:`MAX_PICTURE_PIXELS`
    (the size is read from its header before anything is decoded)."""
    if not data:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PICTURE_PIXELS:
                return None
            rgba = np.asarray(img.convert("RGBA"))
    except Exception:  # noqa: BLE001 - a picture that will not open is left out
        return None
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgba[..., :3].astype(np.float32) / 255.0 * alpha + np.array(BACKGROUND, np.float32) * (1 - alpha)
    return np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8)


def _mark_places(ax, frame: Frame, points: Sequence[Sequence[float]]) -> None:
    """Numbered rings at ``points`` on a picture."""
    if not len(points):
        return
    xy = frame.project(np.asarray(points, dtype=float))
    for k, (px, py) in enumerate(xy):
        ax.add_patch(Circle((px, py), radius=max(frame.width, frame.height) * 0.022, fill=False,
                            edgecolor="white", lw=0.8))
        ax.text(px + frame.width * 0.028, py, f"{k + 1}", color="white", fontsize=5.5,
                va="center", fontweight="bold")


def _section_axis(ax, spec: ReportSpec) -> None:
    """Section numbers along the top of a chart whose x axis is Z (µm)."""
    if not spec.z_scale_nm:
        return
    k = 1000.0 / float(spec.z_scale_nm)
    top = ax.secondary_xaxis("top", functions=(lambda z: z * k, lambda s: s / k))
    top.tick_params(labelsize=5.5, colors=MUTED, length=2, width=0.4)
    top.set_xlabel("section", fontsize=5.5, color=MUTED, labelpad=1)


def _box_around(centre: np.ndarray, half: float, view: View) -> tuple[float, float, float, float]:
    corners = np.array([[centre[0] + sx * half, centre[1] + sy * half, centre[2] + sz * half]
                        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    return extent(corners, view)


def _faces_near(mesh: Mesh, centre: np.ndarray, half: float) -> np.ndarray:
    """The triangles with every corner within ``half`` of ``centre`` on each axis."""
    inside = np.all(np.abs(mesh.vertices - centre) <= half, axis=1)
    return mesh.faces[inside[mesh.faces].all(axis=1)]


def _short_registration(text: str) -> str:
    """A registration in a few words, as a layer's chip says it: "Current transforms +
    Field + fit" is "current + field + fit", "Stored positions, no correction" "stored"."""
    words = (str(text).replace("Current transforms", "current").replace("Stored positions", "stored")
             .replace(", no correction", "").replace("Residual field", "field").replace("Curve fit", "fit")
             .replace("Field + fit", "field + fit").replace(", with manual moves", " + moves"))
    return words


def _settings_line(options: Mapping[str, Any]) -> str:
    """Every option a rendering's technique uses that is not its default, in one line."""
    changed = [f"{row.label} {row.text}" for row in describe_options(options)
               if row.changed and not row.inert]
    return "; ".join(changed) if changed else "the technique's defaults"


def _differing_text(spec: ReportSpec, r: int) -> str:
    """What sets rendering ``r`` apart from the others of its technique: the
    options in which they differ, with this one's values; "" when it is the
    only one of its technique, or none differ."""
    options = spec.renderings[r].options
    preset = match_preset(options)
    same = [k for k, other in enumerate(spec.renderings)
            if k != r and match_preset(other.options) == preset]

    def copy_of(k: int) -> str | None:
        return spec.renderings[k].copy if spec.renderings[k].copy is not None else spec.copy

    if not same:
        changed = [f"{row.label} {row.text}" for row in describe_options(options)
                   if row.changed and not row.inert]
        if len(spec.copies) > 1:
            changed.insert(0, spec.registration_of(r))
        return ", ".join(changed[:4]) if changed else "defaults"
    mine = {row.key: row for row in describe_options(options)}
    differ: list[str] = []
    # Built from another registration than one of its technique: that comes first.
    if any(copy_of(k) != copy_of(r) for k in same):
        differ.append(spec.registration_of(r))
    for k in same:
        theirs = {row.key: row for row in describe_options(spec.renderings[k].options)}
        for key, row in mine.items():
            if key in theirs and theirs[key].value != row.value and not row.inert:
                text = f"{row.label} {row.text}"
                if text not in differ:
                    differ.append(text)
    return ", ".join(differ[:4]) if differ else "same settings as another layer"


# -- putting it together ---------------------------------------------------------------------
def assemble(parts: Sequence[bytes], plan: Plan,
             links: Mapping[int, Sequence[tuple[tuple[float, float, float, float], int]]],
             *, title: str, subject: str, creator: str, jpeg: bool = True) -> bytes:
    """One PDF from ``parts`` (each a PDF, in page order), with the plan's
    bookmarks, the links drawn on the pages, the pictures stored as JPEG and the
    document's title."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Link

    writer = PdfWriter()
    for part in parts:
        writer.append(PdfReader(io.BytesIO(part)))
    total = len(writer.pages)
    for number, rects in links.items():
        if not 1 <= number <= total:
            continue
        for rect, target in rects:
            if 1 <= target <= total:
                writer.add_annotation(page_number=number - 1,
                                      annotation=Link(rect=rect, target_page_index=target - 1))
    # Bookmarks: the summary, each pane with its renderings and pairs, the addendum.
    summary = [i for i in plan.front if i.kind.startswith("summary")]
    if summary:
        top = writer.add_outline_item("Summary", summary[0].first - 1)
        for i in summary:
            writer.add_outline_item(i.title.replace("Summary: ", ""), i.first - 1, parent=top)
    parent = None
    addendum = None
    for i in plan.body:
        if i.kind == "pane":
            parent = writer.add_outline_item(i.title, i.first - 1)
        elif i.kind == "addendum":
            if addendum is None:
                addendum = writer.add_outline_item("Addendum: the mathematics", i.first - 1)
            writer.add_outline_item(i.title, i.first - 1, parent=addendum)
        else:
            writer.add_outline_item(i.title, i.first - 1, parent=parent)
    if jpeg:
        for page in writer.pages:
            for image in page.images:
                try:
                    pil = image.image
                    if pil is None or pil.mode != "RGB" or min(pil.size) < JPEG_MIN_PIXELS:
                        continue
                    image.replace(pil, quality=JPEG_QUALITY)
                except Exception:  # noqa: BLE001, S112 - an image kept lossless is still right
                    continue
    writer.add_metadata({"/Title": title, "/Subject": subject, "/Creator": creator})
    writer.page_mode = "/UseOutlines"
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def write_compare_report(spec: ReportSpec, source: ReportSource, *,
                         preview: Callable[[Page], None] | None = None,
                         jpeg: bool = True) -> ReportFile:
    """The report ``spec`` asks for, measured through ``source``.

    Reports its stages (:mod:`sbfsem.progress`) and can be stopped between
    them (:func:`sbfsem.progress.check_stop`).  ``preview`` is shown every page
    before it is saved (a test or a tool draws it to a PNG).  A rendering or a
    pair that cannot be measured gets pages that say why; the report is never
    cut short by one.
    """
    started = time.time()
    writer = _Writer(spec, source, preview=preview)
    writer.measure_renderings()
    body = io.BytesIO()
    with PdfPages(body, keep_empty=False) as pdf:
        writer.write_body(pdf)
    progress.check_stop()
    progress.report("the summary and the contents", writer._steps - 1, writer._steps)
    front = io.BytesIO()
    with PdfPages(front, keep_empty=False) as pdf:
        writer.write_front(pdf)
    progress.report("putting the pages together", writer._steps, writer._steps)
    from .. import __version__

    pdf_bytes = assemble([front.getvalue(), body.getvalue()], writer.plan, writer.links,
                         title=f"Render compare report: c{spec.cell}, {spec.volume}",
                         subject=f"{len(spec.renderings)} renderings, {len(spec.pairs)} pairs",
                         creator=f"SBFSEM-tools {spec.build or __version__}", jpeg=jpeg)
    notes = []
    for (kind, key), why in writer.numbers.failed.items():
        if kind == "mesh":
            notes.append(f"R{key + 1} could not be built: {why}")
        elif kind == "deviation":
            notes.append(f"R{key + 1} was not measured against the annotations: {why}")
        else:
            notes.append(f"R{key[0] + 1} vs R{key[1] + 1} could not be measured: {why}")
    stamp = "".join(ch for ch in spec.created[:10] if ch.isdigit() or ch == "-") or time.strftime("%Y-%m-%d")
    filename = f"sbfsem-compare-c{spec.cell}-{spec.source}-{stamp}.pdf"
    return ReportFile(pdf=pdf_bytes, pages=writer.plan.total, filename=filename,
                      seconds=time.time() - started, notes=tuple(notes))
