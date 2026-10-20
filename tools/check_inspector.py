"""Scripted browser check of the error inspector drawer and the Section view
(static/js/errorinspector.js and static/js/slice.js) on a real result.

    .venv/bin/python tools/check_inspector.py --url http://127.0.0.1:8792
    .venv/bin/python tools/check_inspector.py --url http://127.0.0.1:8792 --shots /tmp/inspector
    .venv/bin/python tools/check_inspector.py --url http://127.0.0.1:8792 --fetch-only

It needs a running server that holds Inferior c1178 (an offline one serves it from the
cache: ``SBFSEM_OFFLINE=1 python -m uvicorn sbfsem_web.app:app --port 8792``).  It loads
the cell through the API, runs the error-map job for two renderings of it (Voxel remesh
and Original; about a minute each the first time, none after: the results are kept in
``--cache``), fetches the meshes, the annotations and the traced outlines, and writes them
under ``--cache`` (default ``~/.cache/sbfsem-check-inspector``).  Then it drives a headless
Chrome for Testing over the DevTools protocol, in a throwaway profile, with real pointer
and key events:

* the drawer is built from the two results on a page of its own (no app boot) and checked:
  every section is there, the threshold handle drags and takes keys, the annotation table
  sorts and filters, the class edges recompute, a spot and a row call back with their
  volume and ID, the improvement table counts, nothing overflows its container;
* the Section view is built from the real mesh and annotations of the cell and checked:
  the contour closes, circles sit on their own section, Up and Down land on annotated
  sections, the wheel zooms about the pointer, a drag pans, the keys are the canvas's
  alone, the gap numbers agree with the rim-point test of the result.

With ``--shots DIR`` it saves a screenshot of each in both color schemes at 900 and
1440 px wide.  It is outside the pytest gate (it needs a server and a browser); what it
exercises is covered without either by ``tests/js/test_errorinspector.mjs`` and
``tests/js/test_slice.mjs``, which the gate does run.
"""

# ruff: noqa: UP031
# The JavaScript snippets below are filled with `%`: their braces would all need
# doubling in an f-string or `.format`, which buries the script they carry.

from __future__ import annotations

import argparse
import functools
import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_dropdowns import SHIFT, Chrome, Report, find_chrome  # noqa: E402

SOURCE, CELL = "i", 1178
#: The two renderings compared: the recommended surface and the piece-assembled one.
RENDERINGS = {
    "voxel2": {"method": "cylinder", "pathway": "voxel2"},
    "original": {"method": "cylinder", "pathway": "original"},
}
DEFAULT_CACHE = Path.home() / ".cache" / "sbfsem-check-inspector"


# -- the data ------------------------------------------------------------------------------

def _poll(client: httpx.Client, started: dict, what: str, seconds: float = 900.0) -> dict:
    t0 = time.time()
    last = ""
    while time.time() - t0 < seconds:
        job = client.get(f"/api/mesh-jobs/{started['job_id']}")
        job.raise_for_status()
        body = job.json()
        stage = (body.get("progress") or {}).get("stage", "")
        if stage and stage != last:
            print(f"   {what}: {stage}", flush=True)
            last = stage
        if body["status"] in ("done", "error", "cancelled"):
            if body["status"] != "done":
                raise RuntimeError(f"{what}: {body['status']}: {body.get('detail')}")
            return body
        time.sleep(0.5)
    raise TimeoutError(what)


def fetch_case(base: str, cache: Path, refresh: bool = False) -> dict:
    """The cell's error maps for each rendering, its meshes, annotations and outlines.

    Kept as JSON files under ``cache``; a file that is there is not asked for again.
    """
    cache.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(base_url=base, timeout=900)

    def kept(name: str, make):
        path = cache / f"{SOURCE}_{CELL}_{name}.json"
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        value = make()
        path.write_text(json.dumps(value), encoding="utf-8")
        return value

    t0 = time.time()
    loaded = client.post("/api/neurons", json={"id": CELL, "source": SOURCE,
                                               "include_synapses": False, "check_fresh": False})
    loaded.raise_for_status()
    summary = loaded.json()["summary"]
    copy = summary["copy_tag"]
    print(f"loaded c{CELL} ({copy}) in {time.time() - t0:.1f} s", flush=True)
    case: dict = {"summary": summary, "results": {}, "meshes": {}}
    for name, options in RENDERINGS.items():
        def measure(options=options, name=name):
            started = client.post(f"/api/neurons/{SOURCE}/{CELL}/mesh-deviation-job",
                                  params={"copy": copy}, json=options)
            started.raise_for_status()
            t = time.time()
            body = _poll(client, started.json(), f"{name} error map")
            print(f"{name}: error map in {time.time() - t:.1f} s", flush=True)
            return body["result"]

        def mesh(options=options, name=name):
            started = client.get(f"/api/neurons/{SOURCE}/{CELL}/mesh-job",
                                 params={**options, "copy": copy})
            started.raise_for_status()
            return _poll(client, started.json(), f"{name} mesh")["mesh"]

        case["results"][name] = kept(f"{name}_result", measure)
        case["meshes"][name] = kept(f"{name}_mesh", mesh)
    case["nodes"] = kept("nodes", lambda: client.get(
        f"/api/neurons/{SOURCE}/{CELL}/nodes", params={"cell_only": True, "copy": copy}).json())
    case["outlines"] = kept("outlines", lambda: client.get(
        f"/api/neurons/{SOURCE}/{CELL}/closed-curves", params={"copy": copy}).json())
    return case


# -- the page -------------------------------------------------------------------------------

# The page is a stand-in for the app's Render compare tab, not the app: a sidebar the width
# of the real one (the panes and the drawer get only what is left of the window), the drawer,
# and two Section views side by side, all from the app's own stylesheet and modules.
HARNESS_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark">
<title>inspector check</title>
<link rel="stylesheet" href="/static/css/app.css">
<style>
  body { display: block; height: auto; min-height: 100%; }
  #side { position: absolute; left: 0; top: 0; bottom: 0; width: 300px; background: var(--panel);
          border-right: 1px solid var(--line); padding: 10px; color: var(--muted); }
  main { margin-left: 306px; padding: 8px; }
  h2 { font-size: 13px; margin: 6px 0; }
  #drawer { border: 1px solid var(--line); background: var(--bg); }
  #slices { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
  .pane { position: relative; height: 340px; overflow: hidden; border: 1px solid var(--line); }
  #field { margin-top: 6px; }
</style></head>
<body><div id="side">sidebar (300 px)</div>
<main>
  <h2>Error inspector (the drawer)</h2>
  <div id="drawer"></div>
  <h2>Section view, two panes sharing the section</h2>
  <div id="slices"><div class="pane" id="pane1"></div><div class="pane" id="pane2"></div></div>
  <input id="field" type="text" value="a text box">
</main></body></html>
"""

SETUP_JS = r"""
window.__setup = async (dataUrl) => {
  const I = await import('/static/js/errorinspector.js');
  const S = await import('/static/js/slice.js');
  const L = await import('/static/js/levelset.js');
  const P = await import('/static/js/errorpaint.js');
  I.installInspectorStyles();
  S.installSliceStyles();
  // The files are tens of MB: a busy machine can drop one of six at once, so each is asked for up to three times.
  const get = async (n) => {
    for (let k = 0; ; k += 1) {
      try { return await (await fetch(dataUrl + '/i_1178_' + n + '.json')).json(); } catch (error) { if (k >= 2) throw error; }
    }
  };
  const [rv, ro, mv, mo, nodes, outlines] = await Promise.all(
    ['voxel2_result', 'original_result', 'voxel2_mesh', 'original_mesh', 'nodes', 'outlines'].map(get));
  Object.assign(window, { I, S, L, P });
  window.__case = { rv, ro, mv, mo, nodes, outlines };
  const calls = window.__calls = { threshold: [], fly: [], section: [], focus: [], edges: [], views: [] };
  const slices = window.__slices = [];
  const setAll = (section) => { for (const s of slices) s.setSection(section); window.__drawer.setSection(section); };
  window.__drawer = new I.ErrorInspector(document.getElementById('drawer'), {
    onThreshold: (v) => calls.threshold.push(v),
    onFlyTo: (v) => calls.fly.push(v),
    onSection: (s, c) => { calls.section.push([s, c]); if (s !== null) setAll(s); },
    onFocus: (id) => calls.focus.push(id),
    onEdges: (e) => calls.edges.push(e),
  });
  window.__drawer.setLayers([
    { id: 'L1', name: 'Voxel remesh', color: '#ffd400', volume: 'Inferior', result: rv },
    { id: 'L2', name: 'Original', color: '#56B4E9', volume: 'Inferior', result: ro },
  ]);
  const t0 = performance.now();
  const make = (host, mesh, result, color, painted) => {
    const view = new S.SliceView(host, {
      onSection: (s) => { calls.views.push(s); setAll(s); },
    });
    view.setAnnotations('cell', { nodes, outlines, volume: 'Inferior', label: 'c1178', color: '#7aa2ff' });
    view.setScale(result.scale_um);
    view.setSurface('L', {
      vertices: mesh.vertices, faces: mesh.faces, name: painted ? 'Voxel remesh' : 'Original', color,
      values: painted ? Float64Array.from(result.vertex_um) : null,
    });
    slices.push(view);
    return view;
  };
  make(document.getElementById('pane1'), mv, rv, '#ffd400', true);
  make(document.getElementById('pane2'), mo, ro, '#56B4E9', false);
  const first = slices[0];
  // Start on a section with something to see: the most annotations, a traced outline counting for three.
  const index = first.sets.get('cell').index;
  let best = first.sections[0]; let bestScore = -1;
  for (const s of first.sections) {
    const h = S.annotationsAt(index, s); const score = h.outlines.length * 3 + h.discs.length;
    if (score > bestScore) { bestScore = score; best = s; }
  }
  setAll(best);
  return { sections: first.sections.length, section: first.section, dz: first.zMap.dz, residual: first.zMap.maxResidualUm,
           buildMs: performance.now() - t0 };
};

// Rectangles on the screen: [x, y, w, h] of an element, or of a point of the histogram's plot.
window.__rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
window.__histAt = (f) => {
  const svg = document.querySelector('[data-card="hist"] svg');
  svg.scrollIntoView({ block: 'center' });
  const r = svg.getBoundingClientRect();
  const [, , W] = svg.getAttribute('viewBox').split(' ').map(Number);
  const k = r.width / W;
  return [r.left + (38 + f * (W - 48)) * k, r.top + 75 * (r.height / 150)];
};
window.__overflow = () => {
  const bad = [];
  for (const el of document.querySelectorAll('#drawer .insp, #drawer .insp-card, #drawer svg')) {
    if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible' && el.tagName !== 'svg') {
      bad.push((el.dataset.card || el.className) + ' ' + el.scrollWidth + '>' + el.clientWidth);
    }
  }
  const drawer = document.getElementById('drawer');
  const outside = [...document.querySelectorAll('#drawer .insp-card')]
    .filter((c) => c.getBoundingClientRect().right > drawer.getBoundingClientRect().right + 1).map((c) => c.dataset.card);
  return { bad, outside, page: document.documentElement.scrollWidth - document.documentElement.clientWidth };
};

// What the drawer and the Section view cost on the real result, in milliseconds.
window.__timings = () => {
  const out = {};
  const d = window.__drawer;
  let t = performance.now();
  d.setLayers(d.layers.map((l) => ({ ...l })));
  out.drawerBuildMs = performance.now() - t;
  const xmax = d.focus.result.surface.hist.edges.at(-1);
  t = performance.now();
  for (let k = 0; k < 100; k += 1) d.setThreshold((k / 100) * xmax);
  out.thresholdMoveMs = (performance.now() - t) / 100;
  d.setThreshold(null);
  const v = window.__slices[0];
  const sections = Array.from(v.sections).filter((_, i) => i % 5 === 0).slice(10, 60);
  t = performance.now();
  for (const s of sections) { v.setSection(s); v.draw(); }
  out.sectionStepMs = (performance.now() - t) / sections.length;
  t = performance.now();
  window.L.buildSliceIndex(window.__case.mv.vertices, window.__case.mv.faces);
  out.sliceIndexMs = performance.now() - t;
  out.faces = window.__case.mv.faces.length / 3;
  return out;
};

// How well the section's inside test agrees with the server's 3-D rim test, for one rendering ('voxel2' or
// 'original').  Two ways: for every annotation the share of its rim the contour of the right section encloses,
// against the share of its displayed rim points the render encloses (the two sample a rim differently, so they
// differ a little though every point agrees); and for every rim point the server displays, the side the section
// puts it on against the server's own flag (`rim_enclosed`), by the signed count the view uses and by the
// even-odd rule it replaced.  Both are "the render is fatter than the annotation here".
window.__agreement = (which) => {
  const { nodes, outlines } = window.__case;
  const result = which === 'original' ? window.__case.ro : window.__case.rv;
  const mesh = which === 'original' ? window.__case.mo : window.__case.mv;
  const { S, L } = window;
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  const ann = S.buildAnnotationIndex(nodes, outlines);
  const zmap = window.__slices[0].zMap;
  const server = new Map(result.annotations.ids.map((id, i) => [id, result.annotations.rim_enclosed_share[i]]));
  const rimBySection = new Map();
  for (let i = 0; i < result.rim_enclosed.length; i += 1) {
    const section = Math.round(S.zToSection(zmap, result.rim_xyz[3 * i + 2]));
    if (!rimBySection.has(section)) rimBySection.set(section, []);
    rimBySection.get(section).push(i);
  }
  const diffs = [];
  let closed = 0; let open = 0; let degenerate = 0; let empty = 0;
  let points = 0; let agree = 0; let agreeParity = 0;
  const t0 = performance.now();
  for (const section of new Set([...ann.sections, ...rimBySection.keys()])) {
    const seg = S.contourSegments(index, S.sectionToZ(zmap, section));
    if (!seg.count) { if (ann.bySection.has(section)) empty += 1; continue; }
    for (let k = 0; k < seg.count; k += 1) {
      if (Math.hypot(seg.positions[6 * k + 3] - seg.positions[6 * k], seg.positions[6 * k + 4] - seg.positions[6 * k + 1]) < 1e-9) degenerate += 1;
    }
    if (ann.bySection.has(section)) {
      for (const loop of L.contourLoops(seg)) { if (loop.closed) closed += 1; else open += 1; }
    }
    const grid = S.buildSegmentGrid(seg.positions, seg.orient);
    const here = S.annotationsAt(ann, section);
    for (const a of [...here.discs, ...here.outlines]) {
      const s = server.get(a.id);
      if (s === null || s === undefined) continue;
      const gap = S.annotationGap(grid, S.ringSamples(a));
      if (gap) diffs.push(Math.abs(gap.enclosedShare - s));
    }
    const parity = S.buildSegmentGrid(seg.positions);
    for (const i of rimBySection.get(section) || []) {
      const x = result.rim_xyz[3 * i]; const y = result.rim_xyz[3 * i + 1];
      const flag = result.rim_enclosed[i] === 1;
      points += 1;
      if (S.insideContour(grid, x, y) === flag) agree += 1;
      if (S.insideContour(parity, x, y) === flag) agreeParity += 1;
    }
  }
  diffs.sort((a, b) => a - b);
  const q = (p) => diffs[Math.min(diffs.length - 1, Math.floor(p * diffs.length))];
  return { which, n: diffs.length, median: q(0.5), p90: q(0.9), p99: q(0.99), within15: diffs.filter((d) => d <= 0.15).length / diffs.length,
           sections: ann.sections.length, emptySections: empty, closedLoops: closed, openLoops: open, degenerate,
           points, pointAgreement: agree / points, pointAgreementParity: agreeParity / points, ms: performance.now() - t0 };
};
"""


def serve_cache(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    """The kept data over HTTP with CORS, for the page to fetch: the files are too large to push through the DevTools socket."""
    class Handler(SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def open_page(chrome: str, base: str, data_url: str, width: int, height: int, scheme: str) -> tuple[Chrome, dict]:
    c = Chrome(chrome, width, height)
    try:
        c.emulate("mac", width, height, scheme)
        c.send("Page.navigate", url=f"{base}/api/build")       # a page of the app's origin to write the harness into
        c.wait_for("document.readyState === 'complete'", 30)
        c.ev("document.open(); document.write(%s); document.close();" % json.dumps(HARNESS_HTML))
        c.wait_for("document.readyState === 'complete' && !!document.getElementById('drawer')", 30)
        c.ev(SETUP_JS)
        info = c.ev("window.__setup(%s)" % json.dumps(data_url), wait=True)
        c.settle(3)
    except BaseException:
        c.close()                                              # a page that did not start must not leave its browser running
        raise
    return c, info


def fit_viewport(c: Chrome, width: int) -> int:
    """Make the viewport as tall as the page, so a screenshot shows all of it."""
    height = int(c.ev("Math.ceil(document.documentElement.scrollHeight)"))
    c.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False,
           screenWidth=2560, screenHeight=1600)
    c.settle(2)
    return height


def centre(c: Chrome, expression: str):
    """Where to click an element: scrolled to the middle of the window first, so the pointer can reach it."""
    x, y, w, h = c.ev(f"(() => {{ const e = {expression}; e.scrollIntoView({{ block: 'center' }}); return window.__rect(e); }})()")
    return x + w / 2, y + h / 2


def wheel(c: Chrome, x: float, y: float, delta: float):
    c.send("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=delta)
    c.settle()


# -- the checks ----------------------------------------------------------------------------------

def check_drawer(c: Chrome, r: Report, width: int):
    r.start(f"drawer at {width} px")
    c.ev("window.__drawer.setThreshold(null)")
    cards = c.ev("[...document.querySelectorAll('#drawer .insp-card')].map((e) => [e.dataset.card, !e.hidden, "
                 "e.getBoundingClientRect().width | 0])")
    r.check("all seven cards, the improvement one included, with two layers",
            [name for name, shown, _ in cards if shown] == ["bias", "hist", "z", "spots", "annotations", "classes", "improve"], cards)
    over = c.ev("window.__overflow()")
    r.check("nothing spills past its card or the drawer, and the page does not scroll sideways",
            not over["bad"] and not over["outside"] and over["page"] <= 0, over)
    svg = c.ev("[...document.querySelectorAll('#drawer svg')].map((s) => [s.getBoundingClientRect().width | 0, s.parentElement.getBoundingClientRect().width | 0])")
    r.check("each chart is drawn at the width of its card", all(abs(a - b) <= 2 for a, b in svg), svg)

    # The threshold, with the real mouse.
    c.ev("window.__calls.threshold.length = 0")
    x0, y0 = c.ev("window.__histAt(0.4)")
    c.press(x0, y0)
    c.move(*c.ev("window.__histAt(0.5)"), buttons=1)
    c.move(*c.ev("window.__histAt(0.62)"), buttons=1)
    c.release(*c.ev("window.__histAt(0.62)"))
    c.settle()
    calls = c.ev("window.__calls.threshold")
    xmax = c.ev("window.__case.rv.surface.hist.edges.at(-1) > window.__case.ro.surface.hist.edges.at(-1) ? "
                "window.__case.rv.surface.hist.edges.at(-1) : window.__case.ro.surface.hist.edges.at(-1)")
    r.check("a press and a drag on the histogram move the threshold along the axis",
            len(calls) >= 3 and abs(calls[0]["value"] - 0.4 * xmax) < 0.03 * xmax
            and abs(calls[-1]["value"] - 0.62 * xmax) < 0.03 * xmax and calls[-1]["layerId"] == "L1", calls[:1] + calls[-1:])
    r.check("the callback says where the threshold falls in the focused layer's area",
            0 < calls[-1]["percent"] < 100, calls[-1])
    grip = c.ev("(() => { const g = document.querySelector('[data-card=\"hist\"] .grip'); return [g.getAttribute('visibility'), g.getAttribute('aria-valuetext')]; })()")
    r.check("the grip is shown and says its value", grip[0] == "visible" and "percentile" in grip[1], grip)
    readout = c.ev("document.querySelector('[data-card=\"hist\"] [data-role=\"threshold\"]').textContent")
    r.check("the readout gives each layer's share below and above", readout.count("at or above") == 2, readout)

    # The keyboard on the grip, from a threshold in the body of the distribution.
    c.press(*c.ev("window.__histAt(0.2)"))
    c.release(*c.ev("window.__histAt(0.2)"))
    c.settle()
    before = c.ev("window.__drawer.threshold")
    c.ev("document.querySelector('[data-card=\"hist\"] .grip').focus()")
    c.key("ArrowRight")
    after = c.ev("window.__drawer.threshold")
    c.key("ArrowRight", SHIFT)
    after5 = c.ev("window.__drawer.threshold")
    c.key("Escape")
    r.check("the arrow keys move the threshold by one percent of the area, Shift by five, Escape takes it off",
            after > before and after5 > after and c.ev("window.__drawer.threshold") is None
            and c.ev("window.__calls.threshold.at(-1)") is None, (before, after, after5))

    # The table.
    def first_ids():
        return c.ev("[...document.querySelectorAll('[data-card=\"annotations\"] tbody tr')].slice(0, 3).map((t) => t.dataset.id)")

    default = c.ev("(() => { const rows = window.I.annotationRows(window.__case.rv, 'Inferior'); "
                   "const kept = window.I.filterMinArea(rows, window.I.DEFAULT_MIN_AREA_PCT / 100); "
                   "return [window.I.sortRows(kept, 'surfaceMean', 'desc').slice(0, 3).map((x) => String(x.id)), rows.length, kept.length]; })()")
    r.check("the table opens on the worst mean error among annotations that own enough surface", first_ids() == default[0], default)
    c.click(*centre(c, "document.querySelector('[data-card=\"annotations\"] th[data-key=\"areaShare\"] button')"))
    areas = c.ev("[...document.querySelectorAll('[data-card=\"annotations\"] tbody tr')].slice(0, 6).map((t) => parseFloat(t.children[3].textContent))")
    r.check("a click on a header sorts by it, largest first", areas == sorted(areas, reverse=True), areas)
    c.click(*centre(c, "document.querySelector('[data-card=\"annotations\"] th[data-key=\"areaShare\"] button')"))
    areas = c.ev("[...document.querySelectorAll('[data-card=\"annotations\"] tbody tr')].slice(0, 6).map((t) => parseFloat(t.children[3].textContent))")
    r.check("a second click reverses it", areas == sorted(areas), areas)
    c.ev("window.__calls.fly.length = 0; window.__calls.section.length = 0")
    c.click(*centre(c, "document.querySelector('[data-card=\"annotations\"] tbody tr td:nth-child(3)')"))
    fly = c.ev("window.__calls.fly")
    r.check("a click on a row flies to its annotation, with the volume and the position",
            len(fly) == 1 and fly[0]["volume"] == "Inferior" and len(fly[0]["xyz"]) == 3 and fly[0]["source"] == "annotation", fly)
    c.click(*centre(c, "document.querySelector('[data-card=\"annotations\"] tbody tr [data-role=\"section\"]')"))
    sec = c.ev("window.__calls.section")
    shown = c.ev("[window.__slices[0].section, window.__slices[1].section]")
    r.check("a section number jumps the Section views, and nothing flies",
            len(sec) == 1 and sec[0][1]["volume"] == "Inferior" and shown[0] == shown[1] == sec[0][0] and len(c.ev("window.__calls.fly")) == 1, (sec, shown))
    labels = c.ev("[...document.querySelectorAll('[data-card=\"annotations\"] tbody tr td:first-child')].map((t) => t.textContent.startsWith('Inferior '))")
    r.check("every row names its volume beside its Location ID", all(labels) and len(labels) > 0)

    # The classes.
    classes = c.ev("(() => { const rv = window.__case.rv; const rows = window.I.annotationRows(rv, 'Inferior'); "
                   "const { totals, classes } = window.I.radiusClasses(rows, window.I.DEFAULT_EDGES, rv.surface); "
                   "return { n: classes.map((c) => c.n), total: totals.n, area: totals.areaShare, mean: totals.meanErr / rv.surface.mean_um, "
                   "excess: totals.excess / rv.surface.excess_um3, names: classes.map((c) => c.label) }; })()")
    r.check("the classes by radius add up to the server's totals (area, mean error, excess volume)",
            abs(classes["area"] - 1) < 1e-3 and abs(classes["mean"] - 1) < 1e-3 and abs(classes["excess"] - 1) < 1e-3
            and classes["total"] == 2434, classes)
    r.check("the default edges are 0.25, 0.5 and 1.5", classes["names"][0] == "r < 0.25" and classes["names"][-1] == "r ≥ 1.5", classes["names"])
    box = centre(c, "document.querySelector('[data-card=\"classes\"] [data-role=\"edges\"]')")
    c.click(*box)
    c.ev("document.querySelector('[data-card=\"classes\"] [data-role=\"edges\"]').select()")
    for ch in "0.3, 1":
        c.send("Input.dispatchKeyEvent", type="char", text=ch)
    c.key("Enter")
    rows_now = c.ev("document.querySelectorAll('[data-card=\"classes\"] tbody tr').length")
    r.check("typing other radii and Enter recomputes the table and calls back",
            rows_now == 4 and c.ev("window.__calls.edges.at(-1)") == [0.3, 1], (rows_now, c.ev("window.__calls.edges")))

    # The profile along Z and the spots.
    c.ev("window.__calls.section.length = 0")
    c.click(*centre(c, "document.querySelectorAll('[data-card=\"z\"] rect.bin')[10]"))
    sec = c.ev("window.__calls.section")
    expected = c.ev("(() => { const z = Number(document.querySelectorAll('[data-card=\"z\"] rect.bin')[10].dataset.z); "
                    "const a = window.__case.rv.annotations; const m = window.S.fitZMap(a.sections, a.sections.map((_, i) => a.xyz[3 * i + 2])); "
                    "return Math.round(window.S.zToSection(m, z)); })()")
    r.check("a click on the profile along Z names the section at that Z, from the annotations' own Z",
            len(sec) == 1 and sec[0][0] == expected and sec[0][1]["source"] == "profile", (sec, expected))
    r.check("the Section view's section is marked on the profile",
            c.ev("document.querySelectorAll('[data-card=\"z\"] line.cursor').length") == 1)
    c.ev("window.__calls.fly.length = 0")
    c.click(*centre(c, "document.querySelector('[data-card=\"spots\"] li [data-role=\"fly\"]')"))
    fly = c.ev("window.__calls.fly")
    spot = c.ev("window.__case.rv.spots[0].xyz")
    r.check("a worst place flies to its position", len(fly) == 1 and fly[0]["xyz"] == spot and fly[0]["source"] == "spot", (fly, spot))
    header = c.ev("document.querySelector('[data-card=\"spots\"] h3').textContent")
    top = c.ev("window.I.spotSummary(window.__case.rv, 'Inferior').topShare")
    r.check("the header of the worst places adds up the top ten", f"{top * 100:.1f}".split(".")[0] in header and "top 10 hold" in header, header)

    # The improvement.
    summary = c.ev("document.querySelector('[data-card=\"improve\"] [data-role=\"improve-summary\"]').textContent")
    counts = c.ev("(() => { const i = window.I.improvement(window.I.annotationRows(window.__case.rv, 'Inferior'), window.I.annotationRows(window.__case.ro, 'Inferior')); "
                  "return [i.improved, i.worse, i.unchanged, i.missing]; })()")
    r.check("the improvement between the two renders counts every annotation once: better, worse, unchanged, or without a number",
            sum(counts) == 2434 and all(n >= 0 for n in counts), (summary, counts))
    r.check("and the summary line says the counts the module computed",
            all(f"{n:,}" in summary for n in counts[:3]), (summary, counts))
    return counts


def check_slice(c: Chrome, r: Report):
    r.start("Section view")
    agree = c.ev("window.__agreement('voxel2')")
    r.check("every section's contour closes (no open curve, no degenerate segment)",
            agree["openLoops"] == 0 and agree["degenerate"] == 0 and agree["closedLoops"] > 0, agree)
    r.check("the contours exist on the annotated sections", agree["emptySections"] <= 0.02 * agree["sections"], agree)
    r.check("a disc's rim against the contour agrees with the server's 3-D rim test (median difference in the share enclosed)",
            agree["n"] > 2000 and agree["median"] <= 0.1 and agree["within15"] >= 0.9, agree)
    r.check("the Voxel remesh: the section puts the server's rim points on the server's side (all but a few of 45,732)",
            agree["points"] > 40000 and agree["pointAgreement"] >= 0.999, agree)
    # The Original is a union of interpenetrating closed pieces with open ends: a parity would call the overlaps outside.
    original = c.ev("window.__agreement('original')")
    # How near the server's rim test the count gets depends on the copy of the cell the server holds: on stored
    # positions (no annotation carries a shift_um: a server that could not apply the correction) 99.15 % of the
    # 45,732 points agree; on the curve-fit corrected copy the web app serves, 96.5 % (2026-10-16, both against
    # 85 % for the parity).  The floor is set for the copy the page was given.
    corrected = c.ev("window.__case.nodes.some((n) => n.shift_um)")
    floor = 0.95 if corrected else 0.985
    r.check("the Original: its pieces overlap and its ends are open, so the sign is a count and the agreement is a little lower"
            f" (at least {floor:.1%} on {'the corrected copy' if corrected else 'stored positions'})",
            original["n"] > 2000 and original["points"] > 40000 and original["pointAgreement"] >= floor
            and original["median"] <= 0.08 and original["within15"] >= 0.85, original)
    r.check("and a signed count agrees with the server where the even-odd rule it replaced does not (by more than ten points)",
            original["pointAgreement"] - original["pointAgreementParity"] > 0.1, original)

    zmap = c.ev("(() => { const z = window.__slices[0].zMap; return [z.dz, z.maxResidualUm, z.fitted]; })()")
    r.check("the section thickness comes from the annotations (90 nm for Inferior), with a residual of a nanometer",
            abs(zmap[0] - 0.09) < 1e-6 and zmap[1] < 1e-3 and zmap[2], zmap)

    # The circles are drawn on their own section (the view first fitted to it: the screenshots zoomed it).
    c.ev("window.__slices.forEach((s) => s.resetView())")
    c.settle(3)
    drawn = c.ev("""(() => {
      const view = window.__slices[0]; const { S } = window;
      const here = S.annotationsAt(view.sets.get('cell').index, view.section);
      const size = view._size; const ctx = view.canvas.getContext('2d'); const out = [];
      const lit = (px, py, half) => {
        const data = ctx.getImageData(Math.round((px - half) * size.dpr), Math.round((py - half) * size.dpr), Math.ceil(2 * half * size.dpr), Math.ceil(2 * half * size.dpr)).data;
        let n = 0; for (let i = 0; i < data.length; i += 4) if (data[i] + data[i + 1] + data[i + 2] > 150) n += 1;
        return n;
      };
      for (const d of here.discs) {
        const [px, py] = S.worldToScreen(view.view, size, d.x, d.y);
        if (px < 6 || py < 6 || px > size.width - 6 || py > size.height - 6) continue;
        // On the rim, or (a circle of a pixel or two) on the spot where it is.
        const r = view.view.scale * d.r;
        out.push(r >= 6 ? lit(px + r, py, 3) > 0 : lit(px, py, 4) > 0);
      }
      return { checked: out.length, drawn: out.filter(Boolean).length, n: here.discs.length + here.outlines.length, section: view.section, scale: view.view.scale };
    })()""")
    r.check("the discs of the section are on the canvas where they are", drawn["checked"] > 0 and drawn["drawn"] == drawn["checked"], drawn)

    # Keys, with the real keyboard, only on a focused canvas.
    sections = c.ev("Array.from(window.__slices[0].sections)")
    c.click(*centre(c, "window.__slices[0].canvas"))
    start = c.ev("window.__slices[0].section")
    focused = c.ev("document.activeElement === window.__slices[0].canvas")
    c.key("ArrowUp")
    up = c.ev("window.__slices[0].section")
    c.key("ArrowDown")
    down = c.ev("window.__slices[0].section")
    nxt = next((s for s in sections if s > start), start)
    r.check("a click gives the canvas the keyboard", focused)
    r.check("Up lands on the next annotated section and Down comes back",
            up == nxt and up in sections and down == start, (start, up, down))
    c.key("ArrowRight")
    c.key("ArrowRight", SHIFT)
    r.check("Right is one section and Shift+Right ten", c.ev("window.__slices[0].section") == start + 11, c.ev("window.__slices[0].section"))
    c.key("Home")
    r.check("Home goes to the first annotated section", c.ev("window.__slices[0].section") == sections[0])
    c.key("End")
    r.check("End goes to the last", c.ev("window.__slices[0].section") == sections[-1])
    r.check("the other pane follows the shared section", c.ev("window.__slices[1].section") == sections[-1])
    # Keys elsewhere do nothing.
    c.click(*centre(c, "document.getElementById('field')"))
    before = c.ev("window.__slices[0].section")
    for name in ("ArrowUp", "Home", "ArrowLeft"):
        c.key(name)
    r.check("with the keyboard in a text box next to it, the canvas hears nothing", c.ev("window.__slices[0].section") == before, before)
    c.ev("window.__slices[0].canvas.blur(); document.body.focus();")
    c.key("End")
    r.check("with the canvas not focused, End does nothing", c.ev("window.__slices[0].section") == before)
    c.ev("window.__slices.forEach((s) => s.setSection(%d))" % start)

    # Wheel zoom about the pointer, a drag to pan, a double-click to fit.
    view0 = c.ev("window.__slices[0].getView()")
    other0 = c.ev("window.__slices[1].getView()")
    x, y = centre(c, "window.__slices[0].canvas")
    px, py = round(x + 60), round(y - 40)          # the pointer is at whole pixels, as the browser reports it
    world0 = c.ev("(() => { const v = window.__slices[0]; const rect = v.canvas.getBoundingClientRect(); return window.S.screenToWorld(v.view, v._size, %f - rect.left, %f - rect.top); })()" % (px, py))
    wheel(c, px, py, -100)
    view1 = c.ev("window.__slices[0].getView()")
    world1 = c.ev("(() => { const v = window.__slices[0]; const rect = v.canvas.getBoundingClientRect(); return window.S.screenToWorld(v.view, v._size, %f - rect.left, %f - rect.top); })()" % (px, py))
    r.check("the wheel zooms by one step about the pointer, which keeps the point under it",
            abs(view1["scale"] / view0["scale"] - 1.25) < 1e-6 and abs(world1[0] - world0[0]) < 1e-6 and abs(world1[1] - world0[1]) < 1e-6,
            (view0, view1, world0, world1))
    c.press(x, y)
    c.move(x + 50, y + 20, buttons=1)
    c.move(x + 80, y + 30, buttons=1)
    c.release(x + 80, y + 30)
    c.settle()
    view2 = c.ev("window.__slices[0].getView()")
    r.check("a drag pans the content with the pointer",
            abs((view1["cx"] - view2["cx"]) * view1["scale"] - 80) < 1.5 and abs((view1["cy"] - view2["cy"]) * view1["scale"] - 30) < 1.5, (view1, view2))
    other1 = c.ev("window.__slices[1].getView()")
    r.check("the other pane's view did not move (a view is a pane's own)", other1 == other0, (other0, other1))
    c.click(x, y)
    c.click(x, y)
    c.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=2)
    c.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=2)
    c.settle()
    view3 = c.ev("window.__slices[0].getView()")
    fitted = c.ev("(() => { const v = window.__slices[0]; return window.S.fitView(v._contentBox(), v._size); })()")
    r.check("a double-click fits the view to the section again", all(abs(view3[k] - fitted[k]) < 1e-6 for k in fitted), (view3, fitted))
    c.key("ArrowUp")
    r.check("and the view follows the content again from section to section",
            c.ev("(() => { const v = window.__slices[0]; const f = window.S.fitView(v._contentBox(), v._size); const g = v.getView(); "
                 "return Math.abs(f.scale - g.scale) < 1e-9 && Math.abs(f.cx - g.cx) < 1e-9; })()"))

    # The tooltip names the annotation with its volume.
    target = c.ev("""(() => { const v = window.__slices[0]; const here = window.S.annotationsAt(v.sets.get('cell').index, v.section);
      const d = [...here.discs].sort((a, b) => b.r - a.r)[0]; if (!d) return null;
      const rect = v.canvas.getBoundingClientRect(); const [px, py] = window.S.worldToScreen(v.view, v._size, d.x, d.y);
      return [rect.left + px, rect.top + py, d.id]; })()""")
    if target:
        c.move(target[0], target[1])
        c.settle()
        tip = c.ev("(() => { const t = window.__slices[0].tip; return [t.hidden, t.textContent]; })()")
        r.check("hovering a disc shows its volume and ID, its section and the gap to the contour",
                not tip[0] and tip[1].startswith(f"Inferior {target[2]}") and "mean gap" in tip[1], tip)
    else:
        r.skip("hover", "no disc on the section")

    # Drawing stops while the pane is out of sight.
    drew = c.ev("""(() => { const v = window.__slices[1]; let n = 0; const real = v.draw.bind(v); v.draw = () => { n += 1; real(); };
      v.setActive(false); v.setSection(v.sections[3]); return new Promise((resolve) => setTimeout(() => { const quiet = n; v.setActive(true);
      setTimeout(() => { v.draw = real; resolve([quiet, n]); }, 200); }, 200)); })()""", wait=True)
    r.check("a pane that is not active draws nothing, and draws once when it is shown again", drew[0] == 0 and drew[1] == 1, drew)
    c.ev("window.__slices.forEach((s) => s.setSection(%d)); window.__slices.forEach((s) => s.resetView()); window.__drawer.setSection(%d);" % (start, start))
    c.settle(3)
    return agree


def screenshots(c: Chrome, folder: Path, width: int, scheme: str):
    folder.mkdir(parents=True, exist_ok=True)
    # The threshold at the 90th percentile of the focused layer, as a person would have left it.
    c.ev("window.__drawer.setThreshold(window.I.thresholdAtPercent(window.__case.rv, 90))")
    # Both Section views on the annotation of the section that lies farthest from the render, zoomed to it.
    c.ev("""(() => {
      const { S } = window; const [a, b] = window.__slices;
      const set = a.sets.get('cell'); const surface = a.surfaces.get('L');
      const here = S.annotationsAt(set.index, a.section);
      let worst = null;
      for (const d of [...here.discs, ...here.outlines]) {
        const g = a._gapOf(surface, set, d);
        if (g && (!worst || g.maxAbs > worst.g.maxAbs)) worst = { d, g };
      }
      if (!worst) return;
      const half = Math.max(8, 4 * worst.d.r);
      const view = S.fitView({ minX: worst.d.x - half, maxX: worst.d.x + half, minY: worst.d.y - half, maxY: worst.d.y + half }, a._size);
      a.setView(view); b.setView(view);
    })()""")
    height = fit_viewport(c, width)
    path = folder / f"inspector_{width}_{scheme}.png"
    path.write_bytes(c.shot())
    print(f"   saved {path} ({width} x {height})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8792", help="the server (default %(default)s)")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="where the fetched data is kept")
    parser.add_argument("--refresh", action="store_true", help="ask the server again for everything")
    parser.add_argument("--fetch-only", action="store_true", help="fetch and keep the data, then stop")
    parser.add_argument("--shots", type=Path, help="save screenshots here (900 and 1440 px, dark and light)")
    parser.add_argument("--widths", default="900,1440", help="window widths to check (default %(default)s)")
    parser.add_argument("--chrome", help="path to Chrome for Testing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    case = fetch_case(args.url, args.cache, args.refresh)
    print("results (MB):", {k: round(len(json.dumps(v)) / 1e6, 2) for k, v in case["results"].items()})
    if args.fetch_only:
        return 0
    chrome = find_chrome(args.chrome)
    server, data_url = serve_cache(args.cache)
    report = Report(args.verbose)
    try:
        for width in [int(w) for w in args.widths.split(",")]:
            for scheme in ("dark", "light"):
                c, info = open_page(chrome, args.url, data_url, width, 1000, scheme)
                try:
                    report.start(f"{width} px, {scheme}: setup")
                    report.check("the page, the drawer and two Section views built from the real result of c1178",
                                 info["sections"] > 100, info)
                    if args.shots:
                        screenshots(c, args.shots, width, scheme)
                    if scheme == "dark":
                        check_drawer(c, report, width)
                        check_slice(c, report)
                        timings = c.ev("window.__timings()")
                        print("   cost (ms):", {k: round(v, 1) for k, v in timings.items()})
                        report.check("a step to another section (contour, gaps, drawing) takes well under a quarter of a second",
                                     timings["sectionStepMs"] < 250, timings)
                    errors = [e for e in c.events if e.get("method") == "Runtime.exceptionThrown"]
                    report.check(f"{width} px, {scheme}: no exception thrown in the page", not errors, errors[:1])
                finally:
                    c.close()
    finally:
        server.shutdown()
    return report.summary()


if __name__ == "__main__":
    sys.exit(main())
