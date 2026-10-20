"""Build the render options' hover-help movies: what a knob does to the final surface.

The user, 2026-10-17: "Can this be taken even further to include a graphical movie
representation of what the parameter is related to in the final wireframe/surface?
like, if this value gets bigger this happens to the final render and if this value gets
smaller this happens (shown graphically)?"

Every movie is real: one piece of a real cell, built by this package's own geometry
code at each of a sweep of the knob's values (every other option at the viewer's
default for that technique), framed by one fixed camera and drawn as the viewer draws
it, a surface with its wireframe over it.  Each frame carries the build's measured
numbers (triangles, build seconds, mean distance from the annotations).  Frames are
rendered by three.js in headless Chrome for Testing, packed side by side into one WebP
per movie under ``src/sbfsem_web/static/help/``, and described in ``movies.json``
there, which the help pop reads (``static/js/helpmovies.js``).

Run it on the Mac after a change to how a knob builds (it takes about half an hour,
one build at a time, a couple of GB at most)::

    .venv/bin/python tools/help_movies.py                 # every movie
    .venv/bin/python tools/help_movies.py voxel_smooth_um # just these
    .venv/bin/python tools/help_movies.py --list
    .venv/bin/python tools/help_movies.py --texts         # words and labels only, no builds

Cells come from the OData disk cache (or Viking) at their stored positions, with no
section correction, so a rebuild draws the same frames while the annotations stay as
they are.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from check_dropdowns import Chrome, find_chrome  # noqa: E402

from sbfsem.geometry.meshdiff import annotation_deviation  # noqa: E402
from sbfsem.service.facade import SbfsemService, annotation_ground_truth  # noqa: E402
from sbfsem.service.render_schema import app_defaults, field_by_key  # noqa: E402

OUT = ROOT / "src" / "sbfsem_web" / "static" / "help"
#: What a frame is drawn at (CSS pixels) and the device-pixel ratio it is stored at.
FRAME_W, FRAME_H, DPR = 316, 178, 2
WEBP_QUALITY = 74
#: The viewer's own colors: the dark viewport and a cell's surface.
BACKGROUND = "#151a21"
SURFACE = "#c9b13c"
WIRE = "#14181d"
#: The most annotation rings a frame draws over the surface.
RINGS = 36

log = logging.getLogger("help_movies")


# -- the pieces of cells the movies look at -------------------------------------------------
@dataclass(frozen=True)
class View:
    """A camera on one piece of a cell: the point it looks at, the direction it looks
    along (from the camera toward the point), the width of the view in microns, and the
    box of the mesh kept for drawing (a margin round what is in view)."""

    cell: int
    volume: str
    center: tuple[float, float, float]
    looking: tuple[float, float, float]
    width_um: float
    name: str
    #: The disc annotations' rings over the surface (the truth the knob is held to).
    rings: bool = False

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        c = np.asarray(self.center, dtype=float)
        half = 0.75 * self.width_um
        return c - half, c + half


INFERIOR = "NeitzInferiorMonkey"
TEMPORAL = "NeitzTemporalMonkey"
#: Inferior c1178 (dM1 ipRGC): its soma is 130 traced outlines with a disc-drawn top,
#: its arbor discs.  Temporal c118: a soma and arbor drawn with discs alone.
VIEWS = {
    "c1178-soma": View(1178, INFERIOR, (44.0, 186.0, 106.0), (0.0, 1.0, -0.12), 56.0,
                       "Inferior c1178, the soma from the side"),
    "c1178-left": View(1178, INFERIOR, (21.0, 187.0, 107.0), (0.35, 1.0, -0.25), 15.0,
                       "Inferior c1178, the process leaving the soma's left end", rings=True),
    "c1178-top": View(1178, INFERIOR, (48.0, 185.0, 110.5), (0.0, 1.0, -0.35), 22.0,
                      "Inferior c1178, the soma's top: traced outlines below, discs above"),
    # Where Join bodies lays two ties at Voxel 0.25 um, and where two of its links
    # skip 22 untraced sections (5.4 and 5.8 um long).
    "c1178-ties": View(1178, INFERIOR, (66.0, 212.0, 89.5), (0.0, 1.0, -0.15), 18.0,
                       "Inferior c1178, two dendrites the Voxel size breaks"),
    "c1178-links": View(1178, INFERIOR, (138.4, 104.0, 83.1), (0.0, 0.35, -1.0), 12.0,
                        "Inferior c1178, two links across 22 untraced sections, from above",
                        rings=True),
    # Where Smooth surface moves c1178's Closed curve surface most (Voxel 0.1 um): the
    # soma's traced wall, close enough that its pixel-wide grain shows (a whole soma
    # puts a 0.05 um move under a pixel).
    "c1178-wall": View(1178, INFERIOR, (46.0, 178.2, 105.9), (-0.99, -0.16, -0.3), 3.0,
                       "Inferior c1178, 3 µm of the soma's traced wall, close up"),
    # The one gap Tie gaps closes on c1178 under Hybrid (0.7 um).
    "c1178-tie": View(1178, INFERIOR, (28.5, 206.9, 99.4), (0.0, 1.0, -0.2), 7.0,
                      "Inferior c1178, the gap between two pieces Viking never linked"),
}


def _discover_views(service: SbfsemService) -> None:
    """Views of Temporal c118 found from its annotations: its soma, the trunk where
    a dendrite leaves it, and a thin dendrite."""
    service.load_neuron(118, TEMPORAL, live_registration=False, section_corrections=False,
                        include_synapses=False)
    n = service.neuron(118, TEMPORAL)
    nodes = n.get_cell_nodes()
    discs = nodes[nodes["TypeCode"] == 1]
    xyz = discs[["Xum", "Yum", "Zum"]].to_numpy(float)
    r = discs["Rum"].to_numpy(float)
    fat = r >= max(2.0, np.percentile(r, 97))
    soma = np.average(xyz[fat], axis=0, weights=r[fat] ** 2)
    soma_r = float(np.percentile(r[fat], 90))
    # Framed on the soma's discs: its height (Z) and width (X), with a margin.
    lo = (xyz[fat] - r[fat, None] * np.array([1, 1, 0])).min(axis=0)
    hi = (xyz[fat] + r[fat, None] * np.array([1, 1, 0])).max(axis=0)
    mid = (lo + hi) / 2
    width = 1.6 * max(hi[0] - lo[0], (hi[2] - lo[2]) * FRAME_W / FRAME_H)
    VIEWS["c118-soma"] = View(118, TEMPORAL, tuple(mid), (0.0, 1.0, -0.15), float(width),
                              "Temporal c118, the soma from the side", rings=True)
    # A thin dendrite: the median of the discs of radius at most 0.3 um that lie 25 to
    # 45 um from the soma, and the junction nearest the soma among the forks.
    d = np.linalg.norm(xyz - soma, axis=1)
    thin = (r <= 0.3) & (d > 25) & (d < 45)
    if thin.any():
        pick = xyz[thin][np.argsort(d[thin])[len(np.flatnonzero(thin)) // 2]]
        VIEWS["c118-thin"] = View(118, TEMPORAL, tuple(pick), (0.2, 1.0, -0.4), 6.0,
                                  "Temporal c118, a thin dendrite", rings=True)
    import networkx as nx

    g = nx.Graph()
    g.add_edges_from(zip(n.edges["A"].astype(int), n.edges["B"].astype(int), strict=True))
    by_id = discs.set_index("ID")
    forks = [i for i in by_id.index if i in g and g.degree(i) >= 3 and by_id.loc[i, "Rum"] < 1.0]
    if forks:
        fx = by_id.loc[forks, ["Xum", "Yum", "Zum"]].to_numpy(float)
        near = fx[np.argmin(np.abs(np.linalg.norm(fx - soma, axis=1) - 2.5 * soma_r))]
        VIEWS["c118-fork"] = View(118, TEMPORAL, tuple(near), (0.25, 1.0, -0.5), 7.0,
                                  "Temporal c118, a branch point", rings=True)


# -- the movies ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Movie:
    """A knob swept over ``values`` on one view.  ``base`` sets the technique (method,
    pathway and any other option the knob needs to act); every option not named is
    the viewer's default.  ``less``/``more`` say in words what the frames at each end
    show.  ``variant`` is the method the movie is for when a knob has one per method."""

    key: str
    view: str
    values: tuple
    base: dict = field(default_factory=dict)
    less: str = ""
    more: str = ""
    labels: tuple | None = None
    variant: str | None = None


V2 = {"method": "cylinder", "pathway": "voxel2"}
CC = {"method": "closedcurve"}
#: c1178 is traced with outlines and discs: the viewer loads it on Hybrid.
HY = {"method": "hybrid", "pathway": "voxel2", "pixel_size_um": 0.1}
MOVIES: tuple[Movie, ...] = (
    Movie("method", "c1178-top", ("cylinder", "curvemesh", "closedcurve", "hybrid"),
          {"pathway": "voxel2", "pixel_size_um": 0.1},
          labels=("Cylinder", "Tube", "Closed curve", "Hybrid"),
          less="the discs alone (outlines read as discs of their stored radius)",
          more="the traced outlines and the discs, one surface"),
    Movie("pathway", "c118-soma",
          ("original", "adaptive_taubin", "voxel", "voxel2", "convolution", "ultraliser", "wrap"),
          {"method": "cylinder"},
          labels=("Original", "Adaptive", "Voxel orig", "Voxel remesh", "Convolution",
                  "Ultraliser", "Wrap"),
          less="MATLAB's fixed rings: a faceted soma", more="its cube fit over the tubes"),
    Movie("smooth_iterations", "c118-thin", (0, 1, 2, 5, 10), V2,
          less="the contour as marched", more="smoother facets; Taubin keeps the volume"),
    Movie("reduce_fraction", "c118-fork", (1.0, 0.3, 0.1, 0.03), V2,
          labels=("100%", "30%", "10%", "3%"),
          less="every triangle", more="fewer, larger triangles; the shape holds until ~10%"),
    Movie("post_order", "c118-fork", ("smooth_first", "decimate_first"),
          {**V2, "reduce_fraction": 0.3}, labels=("smooth → decimate", "decimate → smooth"),
          less="smoothing on the full mesh", more="cheaper, coarser facets"),
    Movie("voxel_smooth_um", "c118-soma", (0.0, 0.25, 0.5, 1.0, 2.0), V2,
          less="the literal disc stack: terraces", more="a smooth envelope; past 1 µm features soften"),
    Movie("tie_um", "c1178-tie", (0.0, 0.5, 20.0), HY,
          less="pieces Viking never linked stay apart", more="gaps up to this wide are tied"),
    Movie("voxel2_um", "c118-thin", (0.3, 0.2, 0.12, 0.08, 0.0), V2,
          labels=("0.3", "0.2", "0.12", "0.08", "auto"),
          less="a coarse grid: thin processes go lumpy", more="a fine grid follows the thinnest discs"),
    Movie("voxel2_faces", "c118-fork", (25_000, 60_000, 150_000, 400_000, 0), V2,
          labels=("25 k", "60 k", "150 k", "400 k", "no cap"),
          less="few triangles, kept where the shape bends", more="every triangle the grid made"),
    Movie("voxel2_blend_um", "c118-fork", (0.0, 0.2, 0.4, 0.8), V2,
          less="an exact crease where branches meet", more="a rounded fillet, the way tissue joins"),
    Movie("exact_tube_deg", "c118-fork", (0.0, 35.0, 50.0), V2, labels=("0 (round cones)", "35°", "50°"),
          less="round cones: the outer edge of every wobble",
          more="steep stretches exact to each disc, with a ring at each section"),
    Movie("conv_support", "c118-soma", (1.2, 1.6, 2.0, 2.5), {"method": "cylinder", "pathway": "convolution"},
          less="crisp, close to the annotations", more="blended junctions; the soma shrinks"),
    Movie("ultra_soma", "c118-soma", ("discs", "sphere"), {"method": "cylinder", "pathway": "ultraliser"},
          labels=("discs", "sphere"), less="our disc-loft soma", more="a ball of the soma's mean radius"),
    Movie("ultra_smooth", "c118-thin", (0, 5, 15, 40), {"method": "cylinder", "pathway": "ultraliser"},
          less="the binary grid's staircase", more="smooth; thin parts shrink unless Inflate keeps up"),
    Movie("ultra_lambda", "c118-thin", (0.3, 0.5, 0.9), {"method": "cylinder", "pathway": "ultraliser"},
          less="under Inflate the re-inflation wins: rough", more="large steps: smooth, thin parts shrink"),
    Movie("ultra_inflate", "c118-thin", (0.0, 0.52, 1.0), {"method": "cylinder", "pathway": "ultraliser"},
          less="plain Laplacian: thin parts shrink", more="past Smooth λ the re-inflation roughens it"),
    Movie("ring_arc_um", "c118-thin", (0.1, 0.35, 0.8, 1.5), {"method": "cylinder", "pathway": "adaptive_taubin"},
          less="many points per ring: fine facets", more="few points per ring: coarse facets"),
    Movie("wrap_spacing_um", "c118-soma", (0.15, 0.4, 1.0, 2.0), {"method": "cylinder", "pathway": "wrap"},
          less="small cubes follow fine shape", more="large cubes: a coarse wrap"),
    Movie("wrap_blend", "c118-soma", (1.2, 2.0, 4.0, 6.0), {"method": "cylinder", "pathway": "wrap"},
          less="each cube looks close by", more="each cube looks far: smoother"),
    Movie("wrap_reject_sigma", "c118-soma", (0.5, 2.0, 6.0), {"method": "cylinder", "pathway": "wrap"},
          less="outliers cut hard", more="outliers kept"),
    Movie("wrap_passes", "c118-soma", (0, 2, 6), {"method": "cylinder", "pathway": "wrap"},
          less="no outlier rejection", more="many reject-and-refit rounds"),
    Movie("pixel_size_um", "c1178-soma", (1.0, 0.5, 0.25, 0.1, 0.05), CC,
          less="a coarse raster: blocky, fast", more="a fine raster: section-by-section detail"),
    Movie("curve_avg_discs", "c1178-left", (0.0, 0.5, 1.0, 2.0), HY,
          less="the union of every annotated wobble", more="tubes through the discs, moved toward their neighbors"),
    Movie("curve_avg_discs", "c118-soma", (0.0, 0.5, 1.0, 2.0), {**CC, "pixel_size_um": 0.1},
          variant="closedcurve",
          less="the stack of plates, as drawn", more="the discs moved: the staircase averaged out"),
    Movie("curve_avg_paths", "c1178-soma", (0.0, 0.1, 0.25, 0.5, 1.0), {**CC, "pixel_size_um": 0.1},
          less="the traced outlines' staircase", more="the outlines moved along their chain: smoothed"),
    Movie("surface_smooth_um", "c1178-wall", (0.0, 0.1, 0.25, 0.5, 1.0), {**CC, "pixel_size_um": 0.1},
          less="the surface as the method drew it: a pixel-wide grain",
          more="the grain smoothed, the traced steps kept; from ~0.65 µm at this Voxel, no further"),
    Movie("curve_links_um", "c1178-links", (0.0, 1.0, 3.0), {**CC, "pixel_size_um": 0.1},
          labels=("0 (straight)", "1", "3"),
          less="a straight band from disc to disc", more="long links curve through the chain"),
    Movie("curve_tie_um", "c1178-ties", (0.0, 1.0, 5.0), {**CC, "pixel_size_um": 0.25},
          less="separate bodies stay apart", more="bodies this close are joined"),
    Movie("meshlab", "c118-fork", ("none", "isotropic", "qem", "poisson"), V2,
          labels=("none", "isotropic", "quadric", "Poisson"),
          less="the technique's own triangles", more="a new surface fitted to the vertices"),
    Movie("meshlab_edge_um", "c118-fork", (0.1, 0.25, 0.5), {**V2, "meshlab": "isotropic"},
          less="short edges: many triangles", more="long edges: few triangles"),
    Movie("meshlab_fraction", "c118-fork", (0.05, 0.25, 0.6), {**V2, "meshlab": "qem"},
          labels=("5%", "25%", "60%"), less="few triangles kept", more="most triangles kept"),
)


def _movie_id(movie: Movie) -> str:
    return movie.key if movie.variant is None else f"{movie.key}@{movie.variant}"


# -- building ---------------------------------------------------------------------------------
def _options(movie: Movie, value) -> tuple[str, int, float, float, dict]:
    """``_build_geometry``'s arguments for one frame: the viewer's defaults, the
    technique's base, the knob at ``value``."""
    opts: dict[str, Any] = {k: v for k, v in app_defaults().items()
                            if field_by_key(k).scope == "geometry"}
    opts.update(movie.base)
    opts[movie.key] = value
    method = str(opts.pop("method"))
    passes = int(opts.pop("smooth_iterations"))
    reduce = float(opts.pop("reduce_fraction"))
    pixel = float(opts.pop("pixel_size_um"))
    # A closed-curve raster the viewer seeds per cell (0.25 um for a whole cell) unless
    # the movie names one.
    if "pixel_size_um" not in movie.base and movie.key != "pixel_size_um":
        pixel = 0.25
    return method, passes, reduce, pixel, opts


def _crop(mesh, view: View) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = view.box()
    v = np.asarray(mesh.vertices, dtype=float)
    f = np.asarray(mesh.faces, dtype=np.int64)
    inside = ((v >= lo) & (v <= hi)).all(axis=1)
    keep = inside[f].any(axis=1)
    f = f[keep]
    used = np.unique(f)
    remap = np.full(len(v), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return v[used].astype(np.float32), remap[f].astype(np.uint32)


def _rings(neuron, view: View) -> np.ndarray:
    """Each disc in view as a 48-point ring in its section plane, ``(n, 48, 3)``."""
    lo, hi = view.box()
    nodes = neuron.get_cell_nodes()
    discs = nodes[nodes["TypeCode"] == 1]
    c = discs[["Xum", "Yum", "Zum"]].to_numpy(float)
    r = discs["Rum"].to_numpy(float)
    sel = np.flatnonzero(((c >= lo) & (c <= hi)).all(axis=1))
    # At most RINGS of them, evenly through the section order: a soma drawn every
    # section is a solid wall of rings.
    sel = sel[np.argsort(c[sel, 2], kind="stable")]
    if len(sel) > RINGS:
        sel = sel[np.linspace(0, len(sel) - 1, RINGS).round().astype(int)]
    t = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    ring = np.stack([np.cos(t), np.sin(t), np.zeros_like(t)], axis=1)
    return (c[sel, None, :] + r[sel, None, None] * ring[None]).astype(np.float32)


def build_frames(service: SbfsemService, movie: Movie, work: Path) -> list[dict]:
    view = VIEWS[movie.view]
    service.load_neuron(view.cell, view.volume, live_registration=False,
                        section_corrections=False, include_synapses=False)
    neuron = service.neuron(view.cell, view.volume)
    truth = annotation_ground_truth(neuron)
    frames = []
    for i, value in enumerate(movie.values):
        method, passes, reduce, pixel, opts = _options(movie, value)
        t0 = time.time()
        mesh = service._build_mesh(neuron, method, passes, reduce, pixel, opts)
        seconds = time.time() - t0
        dev = annotation_deviation(mesh, *truth, samples=20_000) if not mesh.is_empty else None
        v, f = _crop(mesh, view)
        stem = f"{_movie_id(movie)}-{i}".replace("@", "_")
        (work / f"{stem}.v").write_bytes(v.tobytes())
        (work / f"{stem}.f").write_bytes(f.tobytes())
        frame = {"value": value, "stem": stem, "faces": int(len(mesh.faces)),
                 "seconds": round(seconds, 1),
                 "deviation_um": round(float(dev.mean_um), 3) if dev is not None else None}
        log.info("%s = %s: %s faces, %.1f s, %s um", _movie_id(movie), value, f"{len(mesh.faces):,}",
                 seconds, frame["deviation_um"])
        frames.append(frame)
    if view.rings:
        (work / f"{_movie_id(movie).replace('@', '_')}-rings.v").write_bytes(_rings(neuron, view).tobytes())
    return frames


# -- drawing ----------------------------------------------------------------------------------
PAGE = """<!doctype html><meta charset="utf-8"><body style="margin:0;background:%(bg)s">
<canvas id="c"></canvas>
<script type="module">
import * as THREE from '/static/vendor/three/three.module.min.js';
const W = %(w)d, H = %(h)d, DPR = %(dpr)d;
const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('c'), antialias: true,
                                           preserveDrawingBuffer: true });
renderer.setPixelRatio(DPR);
renderer.setSize(W, H);
renderer.setClearColor('%(bg)s');
async function buffer(url, Type) {
  const res = await fetch(url);
  if (!res.ok) return null;
  return new Type(await res.arrayBuffer());
}
window.draw = async (spec) => {
  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 0.9));
  const camDir = new THREE.Vector3(...spec.looking).normalize();
  const up = new THREE.Vector3(0, 0, 1);
  const half = spec.width / 2;
  const cam = new THREE.OrthographicCamera(-half, half, half * H / W, -half * H / W, -1000, 1000);
  const center = new THREE.Vector3(...spec.center);
  cam.position.copy(center).addScaledVector(camDir, -200);
  cam.up.copy(up);
  cam.lookAt(center);
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.copy(cam.position).add(new THREE.Vector3(0, 0, 120)).add(new THREE.Vector3().crossVectors(camDir, up).multiplyScalar(80));
  const fill = new THREE.DirectionalLight(0xffffff, 1.1);
  fill.position.copy(cam.position).add(new THREE.Vector3(0, 0, -60));
  scene.add(key, fill);
  const v = await buffer('/movie/' + spec.stem + '.v', Float32Array);
  const f = await buffer('/movie/' + spec.stem + '.f', Uint32Array);
  if (v && f && f.length) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(v, 3));
    g.setIndex(new THREE.BufferAttribute(f, 1));
    g.computeVertexNormals();
    const surface = new THREE.Mesh(g, new THREE.MeshPhysicalMaterial({
      color: '%(surface)s', roughness: 0.55, metalness: 0.0, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
    // The wireframe as the viewer draws it, faded where its triangles are only a few
    // pixels across in this frame (a whole soma's): there it would hide the surface.
    let edge = 0, n = Math.min(f.length / 3, 20000);
    for (let i = 0; i < n; i += 1) {
      const a = f[3 * i] * 3, b = f[3 * i + 1] * 3;
      edge += Math.hypot(v[a] - v[b], v[a + 1] - v[b + 1], v[a + 2] - v[b + 2]);
    }
    const px = (edge / Math.max(n, 1)) * W / spec.width;
    const wire = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
      color: '%(wire)s', wireframe: true, transparent: true,
      opacity: Math.min(0.5, Math.max(0.06, (px - 1.5) / 8)) }));
    scene.add(surface, wire);
  }
  if (spec.rings) {
    const r = await buffer('/movie/' + spec.rings + '-rings.v', Float32Array);
    if (r && r.length) {
      const pts = [];
      for (let i = 0; i < r.length / 3; i += 48) {
        for (let k = 0; k < 48; k += 1) {
          const a = (i + k) * 3, b = (i + (k + 1) %% 48) * 3;
          pts.push(r[a], r[a + 1], r[a + 2], r[b], r[b + 1], r[b + 2]);
        }
      }
      const lg = new THREE.BufferGeometry();
      lg.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
      scene.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({
        color: 0x6fd3ff, transparent: true, opacity: 0.55, depthTest: false })));
    }
  }
  renderer.render(scene, cam);
  return renderer.domElement.toDataURL('image/png');
};
window.ready = true;
</script>"""


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, work: Path, **kwargs):
        self.work = work
        super().__init__(*args, directory=str(ROOT / "src" / "sbfsem_web"), **kwargs)

    def translate_path(self, path):
        if path.startswith("/movie/"):
            return str(self.work / path[len("/movie/"):].split("?")[0])
        return super().translate_path(path)

    def log_message(self, *args):  # quiet
        pass


def render(movies: list[tuple[Movie, list[dict]]], work: Path, chrome_path: str | None) -> None:
    from PIL import Image

    (work / "page.html").write_text(PAGE % {"bg": BACKGROUND, "w": FRAME_W, "h": FRAME_H, "dpr": DPR,
                                            "surface": SURFACE, "wire": WIRE}, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, work=work))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page = Chrome(find_chrome(chrome_path), width=FRAME_W + 40, height=FRAME_H + 40)
    try:
        page.send("Page.navigate", url=f"http://127.0.0.1:{server.server_port}/movie/page.html")
        if not page.wait_for("window.ready === true", 60):
            raise RuntimeError("the drawing page did not start")
        manifest = json.loads((OUT / "movies.json").read_text(encoding="utf-8")) \
            if (OUT / "movies.json").exists() else {}
        for movie, frames in movies:
            view = VIEWS[movie.view]
            movie_id = _movie_id(movie)
            images = []
            for frame in frames:
                spec = {"stem": frame["stem"], "center": view.center, "looking": view.looking,
                        "width": view.width_um,
                        "rings": movie_id.replace("@", "_") if view.rings else None}
                url = page.ev(f"window.draw({json.dumps(spec)})", wait=True)
                images.append(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB"))
            w, h = images[0].size
            sheet = Image.new("RGB", (w * len(images), h))
            for i, im in enumerate(images):
                sheet.paste(im, (i * w, 0))
            name = f"{movie_id.replace('@', '_')}.webp"
            sheet.save(OUT / name, "WEBP", quality=WEBP_QUALITY, method=6)
            f = field_by_key(movie.key)
            labels = list(movie.labels) if movie.labels else [_label(v) for v in movie.values]
            manifest[movie_id] = {
                "key": movie.key, "variant": movie.variant, "image": name, "frames": len(frames),
                "width": FRAME_W, "height": FRAME_H, "view": view.name, "label": f.label,
                "values": list(movie.values), "labels": labels,
                "faces": [fr["faces"] for fr in frames], "seconds": [fr["seconds"] for fr in frames],
                "deviation_um": [fr["deviation_um"] for fr in frames],
                "less": movie.less, "more": movie.more,
                "technique": _technique(movie),
            }
            log.info("wrote %s (%d frames, %d KB)", name, len(frames), (OUT / name).stat().st_size // 1024)
        (OUT / "movies.json").write_text(json.dumps(dict(sorted(manifest.items())), indent=1,
                                                    ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        page.close()
        server.shutdown()


def _label(value) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _technique(movie: Movie) -> str:
    """What the frames were built under, in the viewer's words."""
    base = {**movie.base}
    method = base.get("method", "cylinder")
    names = {"cylinder": "Cylinder", "curvemesh": "Tube", "closedcurve": "Closed curve",
             "hybrid": "Hybrid"}
    pathways = {"original": "Original", "adaptive_taubin": "Adaptive ring + Taubin",
                "voxel": "Voxel remesh orig", "voxel2": "Voxel remesh",
                "convolution": "Convolution surface", "ultraliser": "Ultraliser", "wrap": "Wrap"}
    words = names.get(method, method)
    if method != "closedcurve" and movie.key not in ("pathway",):
        words += f", {pathways.get(base.get('pathway', 'voxel2'), base.get('pathway'))}"
    return words


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("keys", nargs="*", help="movies to build (an option key, or key@method)")
    parser.add_argument("--list", action="store_true", help="list the movies and exit")
    parser.add_argument("--chrome", help="Chrome for Testing's binary")
    parser.add_argument("--texts", action="store_true",
                        help="rewrite the manifest's words and labels from MOVIES, building nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("sbfsem").setLevel(logging.ERROR)
    if args.list:
        for movie in MOVIES:
            print(f"{_movie_id(movie):24s} {movie.view:12s} {movie.values}")
        return 0
    if args.texts:
        path = OUT / "movies.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for movie in MOVIES:
            entry = manifest.get(_movie_id(movie))
            if entry is None:
                continue
            entry.update({"less": movie.less, "more": movie.more, "technique": _technique(movie),
                          "label": field_by_key(movie.key).label,
                          "labels": list(movie.labels) if movie.labels else [_label(v) for v in movie.values]})
        path.write_text(json.dumps(dict(sorted(manifest.items())), indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return 0
    wanted = [m for m in MOVIES if not args.keys or _movie_id(m) in args.keys or m.key in args.keys]
    if not wanted:
        parser.error(f"no movie named {args.keys}")
    OUT.mkdir(parents=True, exist_ok=True)
    service = SbfsemService(offline=False)
    try:
        _discover_views(service)
        with tempfile.TemporaryDirectory(prefix="help-movies-") as tmp:
            work = Path(tmp)
            built = []
            for movie in wanted:
                if movie.view not in VIEWS:
                    log.warning("no view %s: %s skipped", movie.view, _movie_id(movie))
                    continue
                built.append((movie, build_frames(service, movie, work)))
            render(built, work, args.chrome)
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
