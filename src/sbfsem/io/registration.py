"""Viking's current section registration, read from the volume's own files.

Viking draws an annotation by mapping its section coordinates (a Location's
``X``/``Y``, a closed curve's ``MosaicShape``) through the volume's current
section-to-volume transforms.  The OData columns ``VolumeX``/``VolumeY``/
``VolumeShape`` are that mapping as it was when the annotation was last saved;
a transform edited afterwards leaves them behind until VikingAU recomputes
them.  On 2026-09-22 the stos files of Inferior Monkey sections 937–1835 were
rewritten to fix two large XY jumps, and nothing reading the stored columns
could show it.

This module reads what Viking reads.  The volume's VikingXML lists one
``<stos mappedSection controlSection path pixelspacing>`` per section; in a
*flat* registration every section maps straight to one control section (the
volume frame), so a section's map is its own stos file and nothing needs
composing.  The stos transforms are parsed as Viking's
``TransformFactory.ParseStos`` parses them:

* ``MeshTransform_double_2_2``: control points given as (mapped, normalized to
  the image box in the fixed parameters) and (control, absolute) pairs,
  triangulated (Delaunay) on the mapped side.
* ``GridTransform_double_2_2``: a regular grid over the mapped image, control
  points in row order, each cell split along its bottom-right/top-left
  diagonal.

A point is mapped by barycentric interpolation in the triangle holding it.  A
point outside every triangle is left where it is stored (Viking falls back to
an RBF there; nothing here imitates it).  Measured where the stored positions
are newer than their stos, the live ones reproduce them: Inferior Monkey
c21768 to 0.005 um (1,132 annotations), Cped c934 to 0.002 um, RPC1 c192 and
RPC2 c2968 to a median of 0.  Where they are older, it moves them to the
present registration: on RPC1 section 885 (stos of 2018-12-07) the 27
annotations saved before 2019-02 move a median (-0.59, -0.10) um and the 73
saved since do not move at all.

Chained registrations (a section registered to its neighbor, composed toward
the root, as RC1's ``grid16``) are not supported: :meth:`VolumeRegistration.load`
reports them and nothing moves.
"""

from __future__ import annotations

import concurrent.futures as cf
import email.utils
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import numpy as np
import pandas as pd

__all__ = [
    "RegistrationError",
    "StosEntry",
    "SectionTransform",
    "VolumeRegistration",
    "parse_stos",
    "parse_vikingxml",
    "LiveRegistrationResult",
    "live_offsets",
    "apply_registration",
    "RegistrationProvider",
    "MOVED_NM",
]

log = logging.getLogger(__name__)


class RegistrationError(RuntimeError):
    """The volume's registration could not be read or is not one this module handles."""


#: A live position this far from the stored one counts as moved.  Below it is
#: rounding: Viking rounds transform points (``Global.TransformSignificantDigits``),
#: and fresh positions reproduce to about 5 nm.
MOVED_NM = 10.0


# -- the files --------------------------------------------------------------
@dataclass(frozen=True)
class StosEntry:
    mapped: int
    control: int
    path: str
    pixel_spacing: float
    group: str


def parse_vikingxml(text: str) -> tuple[str, list[StosEntry]]:
    """``(volume path, stos entries)`` from a VikingXML document."""
    head = re.search(r"<Volume\b[^>]*>", text)
    if head is None:
        raise RegistrationError("no <Volume> element")
    path = re.search(r'\bpath="([^"]*)"', head.group(0))
    if path is None:
        raise RegistrationError("the <Volume> element has no path")
    entries = []
    for attrs in re.findall(r"<stos\b([^>]*)>", text, flags=re.IGNORECASE):
        def attr(name, default=None, attrs=attrs):
            m = re.search(rf'\b{name}="([^"]*)"', attrs, flags=re.IGNORECASE)
            return m.group(1) if m else default
        try:
            entries.append(StosEntry(
                mapped=int(attr("mappedSection")), control=int(attr("controlSection")),
                path=str(attr("path")).replace("\\", "/"),
                pixel_spacing=float(attr("pixelspacing", "1") or 1),
                group=str(attr("GroupName", "") or "")))
        except (TypeError, ValueError):
            continue
    return path.group(1).rstrip("/"), entries


class SectionTransform:
    """A piecewise-linear map from section (mapped) to volume (control) pixels.

    ``grid`` gives the regular grid's shape ``(columns, rows, width, height)``
    so a point finds its cell by arithmetic; otherwise the triangles are
    searched with scipy's Delaunay point location.  Either way a point is
    interpolated barycentrically in its triangle, and one outside every
    triangle maps to NaN.
    """

    def __init__(self, kind: str, mapped: np.ndarray, control: np.ndarray,
                 simplices: np.ndarray, *, grid: tuple[int, int, float, float] | None = None,
                 delaunay=None):
        self.kind = kind
        self.mapped = np.asarray(mapped, dtype=float)
        self.control = np.asarray(control, dtype=float)
        self.simplices = np.asarray(simplices, dtype=np.int64)
        self.grid = grid
        self.delaunay = delaunay

    def _locate(self, pts: np.ndarray) -> np.ndarray:
        """Index into ``simplices`` of the triangle holding each point; -1 outside."""
        if self.grid is not None:
            cols, rows, width, height = self.grid
            cw, ch = width / (cols - 1), height / (rows - 1)
            fx, fy = pts[:, 0] / cw, pts[:, 1] / ch
            ix = np.clip(np.floor(fx), 0, cols - 2).astype(np.int64)
            iy = np.clip(np.floor(fy), 0, rows - 2).astype(np.int64)
            upper = (fx - ix) + (fy - iy) > 1.0  # beyond the bottom-right/top-left diagonal
            tri = (iy * (cols - 1) + ix) * 2 + upper
            eps = 1e-9
            outside = (fx < -eps) | (fy < -eps) | (fx > cols - 1 + eps) | (fy > rows - 1 + eps)
            return np.where(outside, -1, tri)
        if self.delaunay is not None:
            return np.asarray(self.delaunay.find_simplex(pts), dtype=np.int64)
        return self._locate_slowly(pts)

    def _locate_slowly(self, pts: np.ndarray) -> np.ndarray:
        found = np.full(len(pts), -1, dtype=np.int64)
        for k, tri in enumerate(self.simplices):
            todo = found < 0
            if not todo.any():
                break
            lam = self._barycentric(pts[todo], tri)
            inside = (lam[:, 0] >= -1e-9) & (lam[:, 1] >= -1e-9) & (lam.sum(axis=1) <= 1 + 1e-9)
            found[np.flatnonzero(todo)[inside]] = k
        return found

    def _barycentric(self, pts: np.ndarray, tri: np.ndarray) -> np.ndarray:
        a, b, c = self.mapped[tri]
        basis = np.column_stack([b - a, c - a])
        if abs(np.linalg.det(basis)) < 1e-12:
            return np.full((len(pts), 2), np.inf)
        return (pts - a) @ np.linalg.inv(basis).T

    def __call__(self, points) -> np.ndarray:
        """Volume pixels for ``(N, 2)`` section pixels; NaN outside every triangle.

        A point that is not finite is NaN in the answer and nothing more: it
        is not looked up, and it does not take the points that share its
        triangle down with it.
        """
        pts = np.atleast_2d(np.asarray(points, dtype=float))
        out = np.full(pts.shape, np.nan)
        usable = np.isfinite(pts).all(axis=1)
        which = np.full(len(pts), -1, dtype=np.int64)
        if usable.any():
            which[usable] = self._locate(pts[usable])
        for k in np.unique(which[which >= 0]):
            rows = np.flatnonzero(which == k)
            tri = self.simplices[k]
            weights = self._barycentric(pts[rows], tri)
            good = np.isfinite(weights).all(axis=1)  # a degenerate triangle maps none
            if not good.any():
                continue
            rows, weights = rows[good], weights[good]
            ca, cb, cc = self.control[tri]
            out[rows] = ca + weights[:, :1] * (cb - ca) + weights[:, 1:2] * (cc - ca)
        return out


def _parameters(line: str) -> tuple[str, np.ndarray, np.ndarray]:
    parts = line.split()
    name = parts[0]

    def block(tag: str) -> np.ndarray:
        lowered = [p.lower() for p in parts]
        if tag not in lowered:
            return np.empty(0)
        i = lowered.index(tag)
        n = int(float(parts[i + 1]))
        return np.array(parts[i + 2:i + 2 + n], dtype=float)

    return name, block("vp"), block("fp")


def parse_stos(text: str, pixel_spacing: float = 1.0) -> SectionTransform:
    """One stos file, as Viking's ``TransformFactory.ParseStos`` reads it.

    A file that cannot be read -- a blank transform line, a count that runs
    past the numbers, text where a number belongs, points the triangulation
    cannot use -- is a :class:`RegistrationError` like any other unusable
    file, which the caller skips with the section it belongs to.  It used to
    escape as an ``IndexError``, ``ValueError`` or ``QhullError`` and failed
    the load of every cell that crossed the section.
    """
    import scipy.spatial

    # A RuntimeError subclass; public as scipy.spatial.QhullError.
    qhull_error = getattr(scipy.spatial, "QhullError", RuntimeError)
    try:
        return _parse_stos(text, pixel_spacing)
    except RegistrationError:
        raise
    except (IndexError, ValueError, TypeError, ArithmeticError, qhull_error,
            np.linalg.LinAlgError) as exc:
        raise RegistrationError(f"a stos file that cannot be read ({type(exc).__name__}: {exc})") from exc


def _parse_stos(text: str, pixel_spacing: float) -> SectionTransform:
    lines = text.splitlines()
    if len(lines) < 7:
        raise RegistrationError("a stos file has fewer than seven lines")
    if "nan" in lines[6].lower():
        raise RegistrationError("the stos transform holds NaN")
    name, vp, fp = _parameters(lines[6])
    kind = name.lower()
    ps = float(pixel_spacing)
    if kind == "meshtransform_double_2_2":
        from scipy.spatial import Delaunay

        left, bottom, width, height = fp[3] * ps, fp[4] * ps, fp[5] * ps, fp[6] * ps
        v = vp.reshape(-1, 4)
        mapped = np.column_stack([v[:, 0] * width + left, v[:, 1] * height + bottom])
        control = v[:, 2:4] * ps
        tri = Delaunay(mapped)
        return SectionTransform(name, mapped, control, tri.simplices, delaunay=tri)
    if kind == "gridtransform_double_2_2":
        grid_w, grid_h = int(round(fp[2])) + 1, int(round(fp[1])) + 1
        width, height = round(fp[5] * ps), round(fp[6] * ps)
        control = vp.reshape(-1, 2) * ps
        if len(control) != grid_w * grid_h:
            raise RegistrationError(
                f"a {grid_w}x{grid_h} grid transform with {len(control)} points")
        mapped = np.array([[x / (grid_w - 1) * width, y / (grid_h - 1) * height]
                           for y in range(grid_h) for x in range(grid_w)])
        simplices = []
        for y in range(grid_h - 1):
            for x in range(grid_w - 1):
                bl, br = x + y * grid_w, x + 1 + y * grid_w
                tl, tr = x + (y + 1) * grid_w, x + 1 + (y + 1) * grid_w
                simplices += [[bl, br, tl], [br, tr, tl]]
        return SectionTransform(name, mapped, control, np.array(simplices),
                                grid=(grid_w, grid_h, float(width), float(height)))
    raise RegistrationError(f"stos transform {name} is not supported")


# -- one volume -----------------------------------------------------------------
def _when(header: str | None) -> datetime | None:
    if not header:
        return None
    try:
        return email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None


@dataclass
class _Stos:
    text: str
    last_modified: str | None
    #: When it was last validated, and the VikingXML's Last-Modified then.
    checked: float
    xml: str | None = None


class VolumeRegistration:
    """A volume's flat section-to-volume registration, fetched lazily and cached.

    The VikingXML is asked again after ``xml_ttl_s``.  A stos file is asked
    again (conditionally, so an unchanged one costs a 304) when the VikingXML's
    ``Last-Modified`` has moved since it was checked (a re-registration
    rewrote Inferior Monkey's stos files and then its VikingXML, 2026-09-22),
    when forced (a refresh from Viking), or after ``stos_ttl_s`` if given.
    ``cache`` (a :class:`~sbfsem.io.cache.DiskCache`) keeps both across
    processes.
    """

    def __init__(self, url: str, *, cache=None, timeout: float = 20.0,
                 xml_ttl_s: float = 300.0, stos_ttl_s: float | None = None,
                 workers: int = 12, client: httpx.Client | None = None):
        self.url = url
        self.cache = cache
        self.timeout = timeout
        self.xml_ttl_s = xml_ttl_s
        self.stos_ttl_s = stos_ttl_s
        self.workers = workers
        self.base = ""
        self.entries: dict[int, StosEntry] = {}
        self.control: int | None = None
        self.group = ""
        self.xml_last_modified: str | None = None
        #: Why the files in use may be old (the VikingXML host did not answer).
        self.stale_note = ""
        self._xml_checked = 0.0
        self._stos: dict[int, _Stos] = {}
        self._parsed: dict[tuple[int, str | None], SectionTransform] = {}
        self._lock = threading.RLock()
        self._client: httpx.Client | None = client
        self._owns_client = client is None

    # -- plumbing -------------------------------------------------------------
    def _http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(timeout=self.timeout, follow_redirects=True)
            return self._client

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None and self._owns_client:
            client.close()

    def _get(self, url: str, last_modified: str | None = None) -> httpx.Response:
        headers = {"If-Modified-Since": last_modified} if last_modified else {}
        try:
            return self._http().get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise RegistrationError(f"{url}: {exc}") from exc

    # -- the VikingXML ------------------------------------------------------------
    def load(self, *, force: bool = False) -> None:
        """Read (or re-validate) the VikingXML and check the registration is flat."""
        now = time.time()
        with self._lock:
            if not force and self.entries and now - self._xml_checked < self.xml_ttl_s:
                return
        key = f"registration:{self.url}"
        stored = self.cache.get(key) if self.cache is not None else None
        known = stored.get("last_modified") if stored and self.entries else None
        try:
            response: httpx.Response | None = self._get(self.url, known)
        except RegistrationError:
            if not stored:
                raise
            response = None
        if response is not None and response.status_code == 304 and self.entries:
            with self._lock:
                self._xml_checked = now
            return
        if response is None or response.status_code != 200:
            if not stored:
                raise RegistrationError(f"{self.url} answered HTTP {response.status_code}"
                                        if response is not None else f"{self.url} not reached")
            if self.entries:  # keep what is loaded; ask again after the ttl
                with self._lock:
                    self._xml_checked = now
                return
            text, last_modified = stored["text"], stored.get("last_modified")
            log.warning("%s not read; using the copy kept from %s", self.url, last_modified)
            self.stale_note = f"the VikingXML could not be read; using the copy of {last_modified}"
        else:
            text, last_modified = response.text, response.headers.get("last-modified")
            self.stale_note = ""
            if self.cache is not None:
                self.cache.set(key, {"text": text, "last_modified": last_modified})
        base, entries = parse_vikingxml(text)
        if not entries:
            raise RegistrationError(f"{self.url} lists no stos transforms")
        groups = sorted({e.group for e in entries})
        if len(groups) > 1:
            raise RegistrationError(f"{self.url} has several stos groups: {groups}")
        controls = {e.control for e in entries}
        mapped = {e.mapped for e in entries}
        if len(controls) != 1 or controls & mapped:
            raise RegistrationError(
                f"{self.url}: the registration is chained ({len(controls)} control sections); "
                "only a flat section-to-volume registration is read")
        with self._lock:
            self.base = base
            self.entries = {e.mapped: e for e in entries}
            self.control = controls.pop()
            self.group = groups[0]
            self.xml_last_modified = last_modified
            self._xml_checked = now

    # -- stos files -----------------------------------------------------------------
    def _stos_url(self, entry: StosEntry) -> str:
        return f"{self.base}/{entry.path.lstrip('/')}"

    def _fetch(self, z: int, *, force: bool) -> _Stos | None:
        entry = self.entries.get(z)
        if entry is None:
            return None
        url = self._stos_url(entry)
        key = f"registration:{url}"
        with self._lock:
            held = self._stos.get(z)
        if held is None and self.cache is not None:
            stored = self.cache.get(key)
            if stored:
                held = _Stos(stored["text"], stored.get("last_modified"),
                             float(stored.get("checked", 0.0)), stored.get("xml"))
        now = time.time()
        # Asked again when forced, when old, or when the VikingXML has moved
        # since this copy was checked (a re-registration rewrites both).
        expired = self.stos_ttl_s is not None and now - held.checked > self.stos_ttl_s \
            if held is not None else True
        if held is None or force or expired or held.xml != self.xml_last_modified:
            try:
                response = self._get(url, held.last_modified if held else None)
            except RegistrationError:
                if held is None:
                    raise
                with self._lock:
                    self._stos[z] = held
                return held  # the copy kept; checked again next time
            if response.status_code == 304 and held is not None:
                held = _Stos(held.text, held.last_modified, now, self.xml_last_modified)
            elif response.status_code == 200:
                held = _Stos(response.text, response.headers.get("last-modified"), now,
                             self.xml_last_modified)
            elif held is None:
                raise RegistrationError(f"{url} answered HTTP {response.status_code}")
            if self.cache is not None:
                self.cache.set(key, {"text": held.text, "last_modified": held.last_modified,
                                     "checked": held.checked, "xml": held.xml})
        with self._lock:
            self._stos[z] = held
        return held

    def transforms(self, sections, *, force: bool = False) -> dict[int, tuple[SectionTransform, str | None]]:
        """``{z: (map, stos Last-Modified)}`` for the sections that have a stos file.

        The control section maps to itself.  A section the VikingXML does not
        list, or whose stos cannot be read or parsed, is absent.
        """
        self.load(force=force)
        wanted = sorted({int(z) for z in sections})
        out: dict[int, tuple[SectionTransform, str | None]] = {}
        todo = [z for z in wanted if z in self.entries]

        def one(z: int):
            try:
                return z, self._fetch(z, force=force), None
            except RegistrationError as exc:
                return z, None, exc

        with cf.ThreadPoolExecutor(max(1, self.workers)) as pool:
            for z, held, exc in pool.map(one, todo):
                if held is None:
                    log.warning("section %s: %s", z, exc)
                    continue
                cache_key = (z, held.last_modified)
                with self._lock:
                    parsed = self._parsed.get(cache_key)
                if parsed is None:
                    try:
                        parsed = parse_stos(held.text, self.entries[z].pixel_spacing)
                    except RegistrationError as exc2:
                        log.warning("section %s: %s", z, exc2)
                        continue
                    with self._lock:
                        self._parsed[cache_key] = parsed
                out[z] = (parsed, held.last_modified)
        return out


# -- a loaded cell ---------------------------------------------------------------------
#: Viking TypeCodes whose X/Y is the annotation's own center: a circle's center
#: and a closed curve's area centroid.  Validated against VolumeX/VolumeY.
_XY_TYPECODES = (1, 6)

_NUMBER_PAIR = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def _shape(value) -> tuple[str, np.ndarray] | None:
    """``(kind, vertices)`` of a Viking shape column (``POINT``, ``POLYGON``, ``LINESTRING``...)."""
    text = value
    if isinstance(value, dict):
        text = (value.get("Geometry") or {}).get("WellKnownText")
    if not isinstance(text, str) or "(" not in text:
        return None
    kind = text.strip().split("(", 1)[0].strip().upper()
    first_ring = text[text.find("("):]
    if "POLYGON" in kind:  # the outer ring only
        inner = first_ring.lstrip("( ")
        first_ring = inner[:inner.find(")")] if ")" in inner else inner
    pairs = _NUMBER_PAIR.findall(first_ring)
    if not pairs:
        return None
    return kind, np.array(pairs, dtype=float)


def _derived_point(kind: str, vertices: np.ndarray) -> np.ndarray:
    """The point Viking derives from a shape: centroid, then point, then envelope center.

    What ``VolumeX``/``VolumeY`` are computed from ``VolumeShape`` with, so
    the same rule applied to the mapped ``MosaicShape`` gives the live value.
    """
    if kind == "POINT" or len(vertices) == 1:
        return vertices[0]
    if "POLYGON" in kind and len(vertices) >= 3:
        from shapely.geometry import Polygon

        poly = Polygon(vertices)
        if poly.is_valid and poly.area > 0:
            c = poly.centroid
            return np.array([c.x, c.y])
    return (vertices.min(axis=0) + vertices.max(axis=0)) / 2.0


@dataclass
class LiveRegistrationResult:
    """How the current transforms move one structure's annotations."""

    url: str
    group: str
    xml_last_modified: str | None
    #: One row per annotation: ``ID``, ``Z``, ``dx_nm``, ``dy_nm`` (current
    #: transform minus stored position), ``mapped`` (inside a transform) and
    #: ``stos_modified`` (that section's stos Last-Modified, ISO).
    offsets: pd.DataFrame
    extras: dict = field(default_factory=dict)

    @property
    def moved(self) -> pd.DataFrame:
        o = self.offsets
        return o[o["mapped"] & (np.hypot(o["dx_nm"], o["dy_nm"]) > MOVED_NM)]

    @property
    def token(self) -> str | None:
        """Hash of the moves to the nanometer; ``None`` when nothing moved."""
        rows = self.moved.sort_values("ID")
        if not len(rows):
            return None
        text = ";".join(f"{int(i)}:{round(float(a))}:{round(float(b))}"
                        for i, a, b in zip(rows["ID"], rows["dx_nm"], rows["dy_nm"], strict=True))
        return hashlib.sha1(text.encode("ascii")).hexdigest()[:16]

    def summary(self) -> dict:
        o, moved = self.offsets, self.moved
        mags = np.hypot(moved["dx_nm"], moved["dy_nm"]).to_numpy() / 1000.0
        edited = sorted({int(z) for z in moved["Z"]})
        return {
            "vikingxml": self.url,
            "stos_group": self.group,
            "xml_last_modified": self.xml_last_modified,
            "n_annotations": int(len(o)),
            "n_sections": int(o["Z"].nunique()) if len(o) else 0,
            "n_mapped": int(o["mapped"].sum()),
            "n_moved": int(len(moved)),
            "n_sections_moved": len(edited),
            "sections_moved": _ranges(edited),
            "median_um": float(np.median(mags)) if len(mags) else 0.0,
            "p95_um": float(np.percentile(mags, 95)) if len(mags) else 0.0,
            "max_um": float(mags.max()) if len(mags) else 0.0,
            "token": self.token,
            **self.extras,
        }


def _ranges(values: list[int]) -> str:
    """``[937, 938, 939, 1315]`` -> ``"937-939, 1315"``."""
    runs: list[list[int]] = []
    for v in values:
        if runs and v == runs[-1][1] + 1:
            runs[-1][1] = v
        else:
            runs.append([v, v])
    return ", ".join(f"{a}-{b}" if b != a else f"{a}" for a, b in runs)


def live_offsets(neuron, registration: VolumeRegistration, *, force: bool = False,
                 maps: dict | None = None) -> LiveRegistrationResult:
    """Where the current transforms put each annotation, against where it is stored.

    ``maps`` (from :meth:`VolumeRegistration.transforms`) saves fetching them again.
    """
    nodes = neuron.nodes
    if maps is None:
        maps = registration.transforms(nodes["Z"].unique(), force=force)
    n = len(nodes)
    dx, dy = np.zeros(n), np.zeros(n)
    mapped = np.zeros(n, dtype=bool)
    stamp = np.array([None] * n, dtype=object)
    sx, sy = float(neuron.volume_scale[0]), float(neuron.volume_scale[1])
    z_all = nodes["Z"].to_numpy()
    xy = nodes[["X", "Y"]].to_numpy(dtype=float)
    stored = nodes[["VolumeX", "VolumeY"]].to_numpy(dtype=float)
    # A circle's or closed curve's X/Y is its center.  Other shapes (a
    # synapse's line) keep X/Y at 0: their point is derived from the mapped
    # MosaicShape, as VolumeX is from VolumeShape.
    typecode = nodes["TypeCode"].to_numpy() if "TypeCode" in nodes.columns else np.ones(n)
    use_xy = np.isin(typecode, _XY_TYPECODES)
    shapes = nodes["MosaicShape"].to_numpy() if "MosaicShape" in nodes.columns else [None] * n
    for z in np.unique(z_all):
        rows = np.flatnonzero(z_all == z)
        if int(z) == registration.control:
            transform, when = None, None
        elif int(z) in maps:
            transform, when = maps[int(z)]
        else:
            continue
        live = np.full((len(rows), 2), np.nan)
        centers = use_xy[rows]
        if centers.any():
            points = xy[rows[centers]]
            live[centers] = points if transform is None else transform(points)
        for k in np.flatnonzero(~centers):
            row = rows[k]
            shape = _shape(shapes[row])
            if shape is None:
                continue
            kind, vertices = shape
            mapped_v = vertices if transform is None else transform(vertices)
            if np.isfinite(mapped_v).all():
                live[k] = _derived_point(kind, mapped_v)
        ok = np.isfinite(live).all(axis=1) & np.isfinite(stored[rows]).all(axis=1)
        good = rows[ok]
        dx[good] = (live[ok, 0] - stored[good, 0]) * sx
        dy[good] = (live[ok, 1] - stored[good, 1]) * sy
        mapped[good] = True
        modified = _when(when)
        stamp[rows] = modified.isoformat() if modified else None
    offsets = pd.DataFrame({
        "ID": nodes["ID"].to_numpy(dtype=np.int64), "Z": z_all.astype(np.int64),
        "dx_nm": dx, "dy_nm": dy, "mapped": mapped, "stos_modified": stamp,
    })
    result = LiveRegistrationResult(registration.url, registration.group,
                                registration.xml_last_modified, offsets)
    if registration.stale_note:
        result.extras["note"] = registration.stale_note
    return result


def apply_registration(neuron, result: LiveRegistrationResult) -> None:
    """Move the annotations and outlines to where the current transforms put them.

    Sets ``LiveDXnm``/``LiveDYnm`` (zero where nothing moved), recomputes the
    micron columns, shifts each closed-curve outline with its annotation, and
    records the summary on ``neuron.live_registration``.  Run it once, on a
    freshly loaded neuron, before any section correction.
    """
    if getattr(neuron, "live_registration", None):
        raise ValueError(f"c{neuron.id} already carries the live registration")
    if getattr(neuron, "section_correction", None):
        raise ValueError(f"c{neuron.id}: apply the registration before the section correction")
    moved = result.moved.drop_duplicates("ID").set_index("ID")
    nodes = neuron.nodes
    ids = nodes["ID"].astype(np.int64)
    nodes["LiveDXnm"] = ids.map(moved["dx_nm"]).fillna(0.0).astype(float)
    nodes["LiveDYnm"] = ids.map(moved["dy_nm"]).fillna(0.0).astype(float)
    neuron.apply_transform()
    geometries = getattr(neuron, "geometries", None)
    if geometries is not None and len(geometries) and len(moved):
        frame = geometries.copy()
        shifted = []
        for row in frame.itertuples():
            curves = row.Curve or []
            key = int(row.ID)
            if key in moved.index:
                shift = np.array([moved.at[key, "dx_nm"], moved.at[key, "dy_nm"]]) / 1000.0
                curves = [np.asarray(c, dtype=float) + shift for c in curves]
            shifted.append(curves)
        frame["Curve"] = shifted
        neuron.geometries = frame
    neuron.live_registration = result.summary()


class RegistrationProvider:
    """What a long-running server needs around :class:`VolumeRegistration`.

    One registration per VikingXML URL, kept for the life of the process
    (the files are revalidated as described there).  After a failure the
    volume is not asked again for ``retry_after_s``; ``offline`` answers
    from the disk cache only, and says so when it has nothing.
    """

    def __init__(self, *, cache=None, timeout: float = 20.0, retry_after_s: float = 120.0,
                 offline: bool = False, workers: int = 12, budget_s: float = 45.0):
        self.cache = cache
        self.timeout = timeout
        self.retry_after_s = retry_after_s
        self.offline = offline
        self.workers = workers
        #: How long a load waits for a cell's stos files.  Past it the cell
        #: is drawn at its stored positions and the fetch finishes in the
        #: background, so the next load has them (a cold RPC2 cell of 852
        #: sections took 46 s from rogue1.codepharm.net).
        self.budget_s = budget_s
        self._background = cf.ThreadPoolExecutor(2, thread_name_prefix="stos")
        self._volumes: dict[str, VolumeRegistration] = {}
        self._down_until: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            volumes, self._volumes = list(self._volumes.values()), {}
        self._background.shutdown(wait=False, cancel_futures=True)
        for v in volumes:
            v.close()

    def registration(self, url: str) -> VolumeRegistration:
        with self._lock:
            reg = self._volumes.get(url)
            if reg is None:
                client = None
                if self.offline:
                    client = httpx.Client(transport=_OfflineTransport())
                reg = VolumeRegistration(url, cache=self.cache, timeout=self.timeout,
                                         workers=self.workers, client=client)
                self._volumes[url] = reg
            return reg

    def paused(self, url: str) -> bool:
        return time.monotonic() < self._down_until.get(url, 0.0)

    def apply(self, neuron, url: str, *, force: bool = False) -> LiveRegistrationResult:
        """Move ``neuron`` to the current registration; raises :class:`RegistrationError`."""
        if self.paused(url):
            raise RegistrationError(self._errors.get(url) or f"{url} is not answering")
        registration = self.registration(url)
        sections = neuron.nodes["Z"].unique()
        pending = self._background.submit(registration.transforms, sections, force=force)
        try:
            maps = pending.result(timeout=self.budget_s)
        except cf.TimeoutError:
            raise RegistrationError(
                f"the section transforms of {len(sections)} sections are still being read "
                f"(more than {self.budget_s:.0f} s); drawn at the stored positions: "
                "load the cell again in a minute") from None
        except RegistrationError as exc:
            with self._lock:
                self._down_until[url] = time.monotonic() + self.retry_after_s
                self._errors[url] = str(exc)
            log.warning("live registration unavailable: %s", exc)
            raise
        # With ``maps`` in hand nothing here reads a file, so nothing raises
        # RegistrationError: the failures were all above.
        result = live_offsets(neuron, registration, maps=maps)
        apply_registration(neuron, result)
        return result


class _OfflineTransport(httpx.BaseTransport):
    """Every request refused: offline, only what the disk cache holds is used."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"offline: {request.url} not fetched", request=request)
