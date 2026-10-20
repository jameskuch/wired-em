"""Scripted browser check of Renderer3D (static/js/render3d.js) and the Display panel.

    .venv/bin/python tools/check_renderer.py                       # the dev server on :8765
    .venv/bin/python tools/check_renderer.py --url http://127.0.0.1:8777 --cell 943 -v
    .venv/bin/python tools/check_renderer.py --only lifecycle,layers

It drives a headless Chrome for Testing over the DevTools protocol (the client and
the report are ``tools/check_dropdowns.py``'s), in a throwaway profile.  It is
outside the pytest gate: it needs a running server.  The pure half of the code it
exercises is covered by ``tests/js/test_layerbuffers.mjs`` and
``tests/js/test_display.mjs``, which the gate does run; what needs a WebGL context
is checked here, on synthetic meshes, so it needs no cell:

* ``lifecycle`` -- 20 changes of Render compare's pane count (panes come and go at the
  end; the ones that stay keep their renderers) leave the page with its own
  contexts only (when each change rebuilt every pane it created 105, tripped Chrome's
  limit of 16 live ones 89 times and lost the main viewport's for good); a disposed renderer holds no
  geometry or texture but the one sprite quad three.js shares, and no window
  listener; a flight in progress settles; renderers out of sight draw no frames.
* ``layers`` -- ``setVertexColors`` (in place, and orders of magnitude cheaper
  than a rebuild), ``setLayerOrder`` (a fixed draw order while the camera goes
  round), ``setLines``, per-ring colors in ``setDiscs``, framing a scene of
  rings alone, the points layer following a cell, a hidden cell's highlight.
* ``planes`` -- an orbit, a pan, a zoom and a view reset leave no vertex of a long scene cut
  away by the near or far plane, in perspective and in orthographic view (the orthographic
  camera's planes were overwritten with a perspective camera's by every view reset, so after
  an orbit or a pan the cells on the camera's side of the scene vanished, as if past an
  invisible boundary).
* ``undo`` -- needs ``--cell ID``: undo and redo put back a cell's Z range, a
  crop, Hold and a context sheet, and the gradient editor's preview never lands
  in the saved view.
"""

# ruff: noqa: UP031
# The JavaScript snippets below are filled with `%`: their braces would all need
# doubling in an f-string or `.format`, which buries the script they carry.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_dropdowns import Chrome, Report, find_chrome  # noqa: E402

# Added to every document: every WebGL context the page makes, kept so a check can
# ask whether each is still alive.
TRACK = r"""
(() => {
  window.__ctx = [];
  const orig = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
    const gl = orig.call(this, type, ...rest);
    if (gl && /webgl/.test(String(type))) window.__ctx.push(gl);
    return gl;
  };
})();
"""

HELPERS = r"""
window.__make = (w = 600, h = 400) => {
  const div = document.createElement('div');
  div.style.cssText = `position:fixed;left:0;top:0;width:${w}px;height:${h}px;z-index:99999`;
  document.body.append(div);
  const r = new (window.sbfsem.renderer.constructor)(div);
  r.__div = div;
  return r;
};
// A rippled sheet of 2 n^2 triangles over 40 um x 40 um, shifted by (dx, dy).
window.__sheet = (n, dx = 0, dy = 0) => {
  const vertices = new Float32Array((n + 1) * (n + 1) * 3); const faces = [];
  let v = 0;
  for (let i = 0; i <= n; i += 1) for (let j = 0; j <= n; j += 1) {
    const x = i / n * 40; const y = j / n * 40;
    vertices[v] = x + dx; vertices[v + 1] = y + dy; vertices[v + 2] = 3 * Math.sin(x / 5) * Math.cos(y / 5); v += 3;
  }
  for (let i = 0; i < n; i += 1) for (let j = 0; j < n; j += 1) {
    const a = i * (n + 1) + j; faces.push(a, a + 1, a + n + 1, a + 1, a + n + 2, a + n + 1);
  }
  return { vertices, faces, n_faces: n * n * 2, n_vertices: (n + 1) * (n + 1) };
};
// A pane's worth of content: surface, wireframe, rings and a point cloud.
window.__fill = (r, key = 'S/1') => {
  r.setMesh(key, window.__sheet(150), { color: '#ffd400', opacity: 1 });
  r.setWireframe(key, true);
  const nodes = [];
  for (let k = 0; k < 400; k += 1) {
    nodes.push({ id: k, z_section: k, type_code: 1, radius_um: 1 + (k % 5) * 0.2,
                 xyz_um: [5 + (k % 30), 5 + ((k * 7) % 30), k * 0.07] });
  }
  r.setDiscs(key, nodes, { color: '#0019ff', through: true });
  const xyz = []; const col = [];
  for (let k = 0; k < 3000; k += 1) { xyz.push(k % 40, (k * 3) % 40, 0); col.push(1, 0, 0); }
  r.setPoints(key, xyz, col, { size: 3 });
  r.resetView();
};
"""


def js(c: Chrome, expression: str, wait: bool = False):
    return c.ev(expression, wait=wait)


def open_page(chrome: str, url: str, width: int = 1440, height: int = 900) -> Chrome:
    c = Chrome(chrome, width, height)
    c.emulate('mac', width, height)
    c.send('Page.addScriptToEvaluateOnNewDocument', source=TRACK)
    c.goto(url)
    js(c, HELPERS)
    return c


def load_cell(c: Chrome, cell: int, seconds: float = 240) -> str:
    before = js(c, 'window.sbfsem.state.neurons.size')
    js(c, """(() => { const i = document.getElementById('neuron-id'); i.value = '%d';
        document.getElementById('load-form').requestSubmit(); })()""" % cell)
    if not c.wait_for('window.sbfsem.state.neurons.size > %d' % before, 60):
        raise RuntimeError(f'c{cell} did not register')
    key = js(c, '[...window.sbfsem.state.neurons.keys()].at(-1)')
    if not c.wait_for('window.sbfsem.renderer.meshes.has(%s)' % json.dumps(key), seconds):
        raise RuntimeError(f'c{cell} has no mesh')
    time.sleep(0.5)
    return key


# -- lifecycle ---------------------------------------------------------------------

def lifecycle(c: Chrome, r: Report, cycles: int = 20):
    r.start(f'lifecycle: {cycles} pane-count changes')
    js(c, "document.querySelector('.tab[data-view=\"compare\"]').click()")
    time.sleep(1.0)
    start = js(c, 'window.__ctx.filter((g) => !g.isContextLost()).length')
    js(c, 'window.__old = [];')
    for cycle in range(cycles):
        n = 6 if cycle % 2 == 0 else 4
        js(c, """(() => { const cmp = window.sbfsem.compare;
            for (const p of cmp.panes) {
              window.__fill(p.renderer);
              // Drawn once, so the GPU counters below are what a pane that was looked at holds.
              p.renderer.webgl.render(p.renderer.scene, p.renderer.camera);
            }
            // Panes are added and taken away at the end: only those a lowered count
            // takes away are disposed; the ones that stay keep their renderers.
            window.__old.push(...cmp.panes.slice(%d).map((p) => p.renderer));
            cmp.setPaneCount(%d); })()""" % (n, n))
        time.sleep(0.1)
    time.sleep(1.0)
    final = js(c, """(() => ({
        created: window.__ctx.length,
        alive: window.__ctx.filter((g) => !g.isContextLost()).length,
        mainLost: window.sbfsem.renderer.webgl.getContext().isContextLost(),
        panes: window.sbfsem.compare.panes.length }))()""")
    r.check('the main viewport keeps its context', not final['mainLost'], final)
    r.check('only the page\'s own contexts are alive afterwards',
            final['alive'] == start - 4 + final['panes'] or final['alive'] == 1 + final['panes'], (start, final))
    old = js(c, """(() => { let geo = 0, tex = 0, prog = 0, lost = 0, held = 0;
        for (const x of window.__old) { const m = x.webgl.info.memory;
          geo += m.geometries; tex += m.textures; prog += x.webgl.info.programs.length;
          if (x.webgl.getContext().isContextLost()) lost += 1;
          held += x.meshes.size + x.discs.size + x.points.size; }
        return { renderers: window.__old.length, geo, tex, prog, lost, held }; })()""")
    r.check('every disposed renderer lost its context', old['lost'] == old['renderers'], old)
    r.check('no disposed renderer holds a mesh, ring or point layer', old['held'] == 0, old)
    r.check('no disposed renderer holds a texture or program', old['tex'] == 0 and old['prog'] == 0, old)
    r.check('a disposed renderer holds at most the one sprite quad three.js shares',
            old['geo'] <= old['renderers'], old)
    warnings = sum('Too many active WebGL' in json.dumps(e) for e in c.events
                   if e.get('method') in ('Log.entryAdded', 'Runtime.consoleAPICalled'))
    r.check('Chrome never warned of too many contexts', warnings == 0, warnings)

    base = c.send('Runtime.evaluate', expression='window')['result']['objectId']

    def window_listeners() -> dict:
        found: dict = {}
        for item in c.send('DOMDebugger.getEventListeners', objectId=base)['listeners']:
            found[item['type']] = found.get(item['type'], 0) + 1
        return found

    before = window_listeners()
    js(c, 'window.__keep = window.__make(); 1')
    alive = window_listeners()
    js(c, 'window.__keep.dispose(); window.__keep.__div.remove(); 1')
    after = window_listeners()
    pointer = ('pointermove', 'pointerup', 'pointercancel')
    r.check('a renderer adds window listeners while it lives', any(alive.get(k, 0) > before.get(k, 0) for k in pointer))
    r.check('dispose takes every one off',
            all(after.get(k, 0) == before.get(k, 0) for k in pointer), (before, alive, after))

    flight = js(c, """(async () => {
        const x = window.__make(); x.setMesh('M', window.__sheet(10), { color: '#ffd400' }); x.resetView();
        const p = x.flyTo([20, 20, 0], { duration: 5000 });
        x.dispose();
        const settled = await Promise.race([p.then(() => 'resolved'),
          new Promise((resolve) => setTimeout(() => resolve('pending'), 500))]);
        x.__div.remove();
        let again = true; try { x.dispose(); } catch (e) { again = String(e); }
        return { settled, again }; })()""", wait=True)
    r.check('a flight in progress settles when the renderer is disposed', flight['settled'] == 'resolved', flight)
    r.check('dispose twice is harmless', flight['again'] is True, flight)

    frames = js(c, """(async () => {
        const x = window.__make(); let n = 0; const render = x.webgl.render.bind(x.webgl);
        x.webgl.render = (...a) => { n += 1; return render(...a); };
        x.setMesh('M', window.__sheet(20), { color: '#ffd400' }); x.resetView();
        const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
        const fps = async () => { n = 0; await wait(600); return n; };
        const out = { running: await fps() };
        x.setPaused(true); await wait(100); out.paused = await fps();
        x.setPaused(false); await wait(100); out.resumed = await fps();
        x.__div.style.display = 'none'; await wait(200); out.hidden = await fps();
        x.__div.style.display = 'block'; await wait(300); out.shown = await fps();
        x.dispose(); x.__div.remove(); return out; })()""", wait=True)
    r.check('a renderer draws frames while it is shown', frames['running'] > 10, frames)
    r.check('a paused renderer draws none, and carries on when told', frames['paused'] == 0 and frames['resumed'] > 10, frames)
    r.check('a renderer whose container has no size draws none, and carries on when it has',
            frames['hidden'] == 0 and frames['shown'] > 10, frames)

    count = js(c, """(async () => {
        const cmp = window.sbfsem.compare; const seen = {};
        const hook = (name, x) => { seen[name] = 0; const render = x.webgl.render.bind(x.webgl);
          x.webgl.render = (...a) => { seen[name] += 1; return render(...a); }; };
        hook('main', window.sbfsem.renderer); cmp.panes.forEach((p, i) => hook('pane' + (i + 1), p.renderer));
        const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
        document.querySelector('.tab[data-view="render"]').click(); await wait(600);
        for (const k of Object.keys(seen)) seen[k] = 0;
        await wait(1000);
        return seen; })()""", wait=True)
    r.check('with the Render tab open only the main viewport draws',
            count['main'] > 20 and all(v == 0 for k, v in count.items() if k != 'main'), count)


# -- layers --------------------------------------------------------------------------

def layers(c: Chrome, r: Report):
    r.start('layers')

    out = js(c, """(() => {
      const x = window.__make(); x.setPaused(true);
      const mesh = window.__sheet(450);
      x.setMesh('X/1', mesh, { color: '#ffd400', opacity: 1 }); x.setWireframe('X/1', true); x.resetView();
      const gl = x.webgl.getContext(); const px = new Uint8Array(4);
      const sync = () => { x.webgl.render(x.scene, x.camera); gl.readPixels(300, 200, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); };
      sync();
      const cam = () => JSON.stringify([x.camera.position.toArray(), x.controls.target.toArray(), x.camera.up.toArray()]);
      const cam0 = cam();
      const solid = x._meshPart('X/1', 'surface'); const wire = x._meshPart('X/1', 'wireframe');
      const flat = { vc: solid.material.vertexColors, color: solid.material.color.getHexString(),
                     wvc: wire.material.vertexColors, wcolor: wire.material.color.getHexString() };
      const rgb = new Float32Array(mesh.n_vertices * 3);
      const recolor = []; const rebuild = [];
      for (let k = 0; k < 6; k += 1) {
        for (let i = 0; i < rgb.length; i += 1) rgb[i] = ((i + k) % 11) / 11;
        let t = performance.now(); x.setVertexColors('X/1', rgb); sync(); recolor.push(performance.now() - t);
        t = performance.now(); x.setMesh('X/1', mesh, { vertexColors: rgb, opacity: 1 }); sync(); rebuild.push(performance.now() - t);
      }
      x.setMesh('X/1', mesh, { color: '#ffd400', opacity: 1 }); x.setWireframe('X/1', true);
      const solid2 = x._meshPart('X/1', 'surface'); const wire2 = x._meshPart('X/1', 'wireframe');
      x.setVertexColors('X/1', rgb);
      const on = { vc: solid2.material.vertexColors, color: solid2.material.color.getHexString(), wvc: wire2.material.vertexColors };
      const wrong = x.setVertexColors('X/1', new Float32Array(9));
      x.setVertexColors('X/1', null);
      const off = { vc: solid2.material.vertexColors, color: solid2.material.color.getHexString(),
                    wvc: wire2.material.vertexColors, wcolor: wire2.material.color.getHexString() };
      const med = (a) => [...a].sort((p, q) => p - q)[Math.floor(a.length / 2)];
      const result = { faces: mesh.n_faces, recolorMs: +med(recolor.slice(1)).toFixed(1), rebuildMs: +med(rebuild.slice(1)).toFixed(1),
        flat, on, off, wrong, cameraKept: cam() === cam0 };
      x.dispose(); x.__div.remove(); return result;
    })()""")
    r.check('setVertexColors turns vertex colors on in place and the wireframe follows',
            out['on']['vc'] and out['on']['wvc'] and out['on']['color'] == 'ffffff', out)
    r.check('setVertexColors(null) gives the flat color back, surface and wireframe',
            not out['off']['vc'] and not out['off']['wvc']
            and out['off']['color'] == out['flat']['color'] and out['off']['wcolor'] == out['flat']['wcolor'], out)
    r.check('a color array of the wrong length changes nothing and answers false', out['wrong'] is False, out)
    r.check('a recolor does not move the camera', out['cameraKept'], out)
    r.check(f"a recolor is much cheaper than a rebuild at {out['faces']:,} faces",
            out['recolorMs'] * 5 < out['rebuildMs'], f"{out['recolorMs']} ms against {out['rebuildMs']} ms")

    # A finished render arrives as typed arrays (api.js decodeMeshReply): Float64Array
    # vertices and vertex_data, Uint32Array faces.  three.js's setIndex takes only an
    # Array or an attribute; a bare Uint32Array left the normals at zero and broke the
    # draw and every pick.  The same sheet both ways must draw and pick the same.
    typed = js(c, """(() => {
      const x = window.__make(); x.setPaused(true);
      const sheet = window.__sheet(40);
      const plain = { ...sheet, vertices: Array.from(sheet.vertices),
        vertex_data: Array.from({ length: sheet.n_vertices }, (_, i) => (i % 17) / 17) };
      const typed = { ...plain, vertices: Float64Array.from(plain.vertices), faces: Uint32Array.from(plain.faces),
        vertex_data: Float64Array.from(plain.vertex_data) };
      const out = {};
      for (const [key, mesh] of [['A/1', plain], ['T/1', typed]]) {
        x.setMesh(key, mesh, { color: '#2f6fed', opacity: 1 });
        const g = x._meshPart(key, 'surface').geometry;
        out[key] = { index: Array.from(g.index.array).join(), count: g.index.count,
          normals: Array.from(g.getAttribute('normal').array).join(),
          colors: Array.from(g.getAttribute('color')?.array || []).join() };
      }
      x.resetView(); x.webgl.render(x.scene, x.camera);
      const rect = x.webgl.domElement.getBoundingClientRect();
      for (const key of ['A/1', 'T/1']) {
        x.setStyle(key === 'A/1' ? 'T/1' : 'A/1', { visible: false });
        x.setStyle(key, { visible: true });
        const part = x._meshPart(key, 'surface');
        x.raycaster.setFromCamera({ x: 0.1, y: -0.05 }, x.camera);
        const hit = x.raycaster.intersectObject(part, false)[0];
        const near = x._nearTriangle(part, key, rect.width * 0.55, rect.height * 0.52, rect);
        out[key].ray = hit ? [hit.faceIndex, ...hit.point.toArray().map((v) => +v.toFixed(6))] : null;
        out[key].near = near ? near.world.map((v) => +v.toFixed(6)) : null;
      }
      const a = out['A/1']; const t = out['T/1'];
      const result = {
        faces: plain.n_faces, sameIndex: a.index === t.index && a.count === t.count && t.count === plain.faces.length,
        sameNormals: a.normals === t.normals && /[1-9]/.test(t.normals), sameColors: a.colors === t.colors && t.colors.length > 0,
        ray: [a.ray, t.ray], near: [a.near, t.near],
      };
      x.dispose(); x.__div.remove(); return result;
    })()""")
    r.check('typed-array faces build the same index as an Array', typed['sameIndex'], typed)
    r.check('typed-array mesh: the same normals, not zero', typed['sameNormals'], typed)
    r.check('typed-array vertex_data colors the surface as an Array does', typed['sameColors'], typed)
    r.check('typed-array mesh: a ray and the pixel pick land where they do on the Array mesh',
            typed['ray'][0] is not None and typed['ray'][0] == typed['ray'][1]
            and typed['near'][0] is not None and typed['near'][0] == typed['near'][1], typed)

    order = js(c, """(() => {
      const x = window.__make(); x.setPaused(true);
      const mk = (key, dx, dy) => x.setMesh(key, window.__sheet(30, dx, dy),
        { color: key === 'L1' ? '#ffd400' : '#56B4E9', opacity: 0.45 });
      mk('L1', 0, 0); mk('L2', 8, 0); mk('L3', 0, 8); x.resetView();
      const keys = ['L1', 'L2', 'L3'];
      const around = () => {
        const seen = new Set(); let frame = [];
        for (const k of keys) x._meshPart(k, 'surface').onBeforeRender = () => frame.push(k);
        for (let a = 0; a < 360; a += 15) {
          x.camera.position.set(20 + 60 * Math.cos(a * Math.PI / 180), 20 + 60 * Math.sin(a * Math.PI / 180), 25);
          x.controls.target.set(20, 20, 0); x.camera.lookAt(20, 20, 0); x.camera.updateMatrixWorld();
          frame = []; x.webgl.render(x.scene, x.camera); seen.add(frame.join(','));
        }
        return [...seen];
      };
      const without = around();
      keys.forEach((k, i) => x.setLayerOrder(k, i));
      const withOrder = around();
      const orders = keys.map((k) => x.meshes.get(k).renderOrder);
      const offsets = keys.map((k) => x._meshPart(k, 'surface').material.polygonOffsetFactor);
      x.setMesh('L2', window.__sheet(30), { color: '#56B4E9', opacity: 0.45 });
      const kept = x.meshes.get('L2').renderOrder;
      x.setLayerOrder('L2', null);
      const result = { without, withOrder, orders, kept, forgotten: x.meshes.get('L2').renderOrder,
                       unknown: x.setLayerOrder('nope', 1), offsets };
      x.dispose(); x.__div.remove(); return result;
    })()""")
    r.check('without setLayerOrder three translucent layers swap places as the camera goes round',
            len(order['without']) > 1, order)
    r.check('with it the draw order is the list order from every side', order['withOrder'] == ['L1,L2,L3'], order)
    r.check('layer groups render at index - 100 and later layers are offset in depth',
            order['orders'] == [-100, -99, -98] and order['offsets'] == [0, 1, 2], order)
    r.check('a rebuild keeps the place, null forgets it, an unknown key answers false',
            order['kept'] == -99 and order['forgotten'] == 0 and order['unknown'] is False, order)

    lines = js(c, """(() => {
      const x = window.__make(); x.setPaused(true);
      const seg = [0, 0, 0, 10, 0, 0, 10, 0, 0, 10, 10, 0, NaN, 0, 0, 1, 1, 1, 0, 10, 0, 0, 0, 5];
      const cols = [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 0.5, 0.5, 0.5, 1, 1, 1];
      const o = x.setLines('K/1', seg, { colors: cols, opacity: 0.8 });
      const out = { segments: o.geometry.getAttribute('position').count / 2, painted: o.material.vertexColors,
        through: o.material.depthTest === false, order: o.renderOrder, name: o.name, bounded: !x.bounds.isEmpty() };
      x.setLineStyle('K/1', { through: false }); out.depthAfter = o.material.depthTest;
      x.setZHide('K/1', { min: 1, max: 4 }); out.clip = o.material.clippingPlanes.length;
      x.setStyle('K/1', { visible: false }); out.hidden = !o.visible;
      x.setVolumeOffset('K', [5, 0, 0]); out.offset = o.position.x;
      out.none = x.setLines('K/2', [NaN, 0, 0, 0, 0, 0]);
      x.removeAll('K/1'); out.removed = !x.lines.has('K/1') && x.bounds.isEmpty();
      x.dispose(); x.__div.remove(); return out;
    })()""")
    r.check('setLines drops a non-finite segment and colors the rest per end',
            lines['segments'] == 3 and lines['painted'], lines)
    r.check('lines go through the surface at the point cloud\'s order, and back with through: false',
            lines['through'] and lines['order'] == 4 and lines['depthAfter'] is True, lines)
    r.check('lines follow the cell: Z range, visibility, volume offset, removal',
            lines['clip'] == 2 and lines['hidden'] and lines['offset'] == 5 and lines['removed'], lines)
    r.check('lines with nothing drawable are not a layer', lines['none'] is None, lines)

    rings = js(c, """(() => {
      const x = window.__make(); x.setPaused(true);
      const nodes = [
        { id: 1, z_section: 1, type_code: 1, radius_um: 1, xyz_um: [0, 0, 0] },
        { id: 2, z_section: 2, type_code: 6, radius_um: 2, xyz_um: [5, 0, 0] },
        { id: 3, z_section: 3, type_code: 1, radius_um: 1, xyz_um: [10, 0, 0] },
        { id: 4, z_section: 4, type_code: 1, radius_um: 0, xyz_um: [15, 0, 0] },
        { id: 5, z_section: 5, type_code: 1, radius_um: 1, xyz_um: [20, 0, 0] }];
      const outlines = [{ location_id: 99, z_section: 2, z_um: 0.2, outline_um: [[0, 5], [4, 5], [4, 9], [0, 9]], cutouts_um: [] }];
      x.setDiscs('A', nodes, { outlines, color: '#ffffff', ringColors: [[1, 0, 0], null, [0, 0, 1], [9, 9, 9], 'lime'], outlineColors: [[0, 1, 0]] });
      const o = x.discs.get('A'); const a = o.geometry.getAttribute('color'); const SEG = 24;
      const at = (v) => Array.from(a.array.slice(v * 3, v * 3 + 3)).map((n) => +n.toFixed(3));
      const out = { vertices: a.count, matches: a.count === o.geometry.getAttribute('position').count,
        first: at(0), second: at(2 * SEG), third: at(2 * 2 * SEG), band: at(3 * 2 * SEG), name: o.name };
      x.setDiscs('B', nodes, { color: '#0019ff', ringColors: new Map([[3, [0, 1, 0]]]) });
      const b = x.discs.get('B').geometry.getAttribute('color');
      out.byId = [Array.from(b.array.slice(2 * SEG * 2 * 0 + 2 * SEG * 3, 2 * SEG * 3 + 3)).map((n) => +n.toFixed(3))];
      x.setDiscs('C', nodes, { color: '#0019ff' });
      out.plain = !x.discs.get('C').geometry.getAttribute('color');
      x.setDiscStyle('A', { color: '#ff00ff' }); out.ignored = x.discs.get('A').material.color.getHexString();
      x.setDiscStyle('C', { color: '#ff00ff' }); out.applied = x.discs.get('C').material.color.getHexString();
      out.ringIds = x.discs.get('A').userData.rings.map((n) => n.id);
      x.dispose(); x.__div.remove(); return out;
    })()""")
    r.check('per-ring colors follow the rings, indexed by the caller\'s own list (a closed curve and a '
            'ring with no radius take none of the buffer)',
            rings['first'] == [1, 0, 0] and rings['second'] == [0, 0, 1] and rings['third'] == [0, 1, 0], rings)
    r.check('an outline takes its own color after the rings and the buffer matches the positions',
            rings['band'] == [0, 1, 0] and rings['matches'], rings)
    r.check('a Map of colors by Location ID reaches the ring with that ID', rings['byId'] == [[0, 1, 0]], rings)
    r.check('an annotation with no color takes the layer color, and the layer ignores a later style color',
            rings['ignored'] == 'ffffff' and rings['applied'] == 'ff00ff' and rings['plain'], rings)
    r.check('selection still sees the rings it always did', rings['ringIds'] == [1, 3, 5], rings)

    bare = js(c, """(() => {
      const x = window.__make(); x.setPaused(true);
      const nodes = [{ id: 1, z_section: 1, type_code: 1, radius_um: 2, xyz_um: [10, 20, 3] },
                     { id: 2, z_section: 2, type_code: 1, radius_um: 2, xyz_um: [30, 20, 6] }];
      const out = { empty: x.bounds.isEmpty() };
      const cam0 = x.camera.position.toArray();
      x.setDiscs('A', nodes, { color: '#0019ff' });
      out.rings = { empty: x.bounds.isEmpty(), max: x.bounds.max.toArray().map((n) => +n.toFixed(1)) };
      x.resetView(); out.moved = x.camera.position.toArray().some((v, i) => Math.abs(v - cam0[i]) > 1e-6);
      x.setLines('L', [0, 0, 0, 100, 100, 100]); out.lines = x.bounds.max.toArray();
      x.removeLines('L'); out.afterLines = x.bounds.max.toArray().map((n) => +n.toFixed(1));
      x.setPoints('P', [0, 0, 0, 50, 50, 50], [1, 0, 0, 1, 0, 0]); out.points = x.bounds.max.toArray();
      x.removePoints('P'); x.removeDiscs('A'); out.gone = x.bounds.isEmpty();
      x.setMesh('M', window.__sheet(10), { color: '#ffd400' }); const withMesh = x.bounds.max.toArray();
      x.setDiscs('A2', [{ id: 1, z_section: 1, type_code: 1, radius_um: 500, xyz_um: [0, 0, 0] }]);
      out.meshWins = JSON.stringify(withMesh) === JSON.stringify(x.bounds.max.toArray());
      x.dispose(); x.__div.remove(); return out;
    })()""")
    r.check('a scene of rings alone has bounds and Reset view frames it',
            bare['empty'] and not bare['rings']['empty'] and bare['moved'], bare)
    r.check('lines and points widen those bounds while there is no surface, and shrink them again',
            bare['lines'] == [100, 100, 100] and bare['afterLines'] == bare['rings']['max']
            and bare['points'] == [50, 50, 50] and bare['gone'], bare)
    r.check('with a surface the rings do not move the bounds', bare['meshWins'], bare)

    follow = js(c, """(() => {
      const x = window.__make(); x.setPaused(true);
      x.setMesh('S/1', window.__sheet(10), { color: '#2f6fed' });
      x.setDiscs('S/1', [{ id: 7, z_section: 1, type_code: 1, radius_um: 2, xyz_um: [10, 20, 3] }], { color: '#0019ff' });
      x.setPoints('S/1', [1, 2, 3], [1, 0, 0]);
      x.select({ kind: 'annotation', key: 'S/1', locationId: 7, section: 1, zUm: 3, point: [10, 20, 3], px: 0, depth: 1, through: true });
      const shown = () => x.highlightGroup.children.map((o) => o.visible);
      const out = { shown: shown() };
      x.setStyle('S/1', { visible: false }); out.hiddenCell = shown(); out.pointsHidden = !x.points.get('S/1').visible;
      x.setStyle('S/1', { visible: true }); out.shownAgain = shown();
      x.setVolumeOffset('S', [1, 2, 3]); out.pointsOffset = x.points.get('S/1').position.toArray();
      x.setZHide('S/1', { min: 0, max: 2 }); out.pointsClipped = x.points.get('S/1').material.clippingPlanes.length;
      out.names = [x.discs.get('S/1').name, x.points.get('S/1').name];
      const m = window.__sheet(10); m.vertex_data = new Array(m.n_vertices).fill(0.3);
      x.setMesh('D/1', m, { color: '#2f6fed' }); out.scalars = x._scalars.has('D/1');
      const m2 = window.__sheet(10); x.setMesh('D/1', m2, { vertexColors: new Float32Array(m2.n_vertices * 3).fill(0.9) });
      x.setColormap({ map: 'Jet' });
      out.scalarsAfter = x._scalars.has('D/1');
      out.painted = x._meshPart('D/1', 'surface').geometry.getAttribute('color').array[0];
      x.dispose(); x.__div.remove(); return out;
    })()""")
    r.check('a hidden cell hides its selection highlight and shows it again', follow['shown'] == [True]
            and follow['hiddenCell'] == [False] and follow['shownAgain'] == [True], follow)
    r.check('the points layer follows the cell: visibility, volume offset, Z range',
            follow['pointsHidden'] and follow['pointsOffset'] == [1, 2, 3] and follow['pointsClipped'] == 2, follow)
    r.check('every layer object is named for its cell', follow['names'] == ['S/1', 'S/1'], follow)
    r.check('a rebuild with colors of its own is not repainted by a colormap change',
            follow['scalars'] and not follow['scalarsAfter'] and abs(follow['painted'] - 0.9) < 1e-6, follow)


# -- glow ------------------------------------------------------------------------------

def glow(c: Chrome, r: Report):
    r.start('glow')
    probe = """(() => {
      const x = window.__make(); x.setPaused(true);
      x.setMesh('S/1', window.__sheet(10), { color: '#2f6fed' });
      x.setDiscs('S/1', [{ id: 7, z_section: 1, type_code: 1, radius_um: 2, xyz_um: [10, 20, 3] },
                         { id: 8, z_section: 2, type_code: 1, radius_um: 2, xyz_um: [10, 20, 3.2] },
                         { id: 9, z_section: 3, type_code: 1, radius_um: 2, xyz_um: [10, 20, 3.4] }], { color: '#0019ff' });
      const ring = (id, z) => ({ kind: 'annotation', key: 'S/1', locationId: id, section: id - 6, zUm: z,
                                 point: [10, 20, z], px: 0, depth: 1, through: true });
      // A ring lies in a plane of constant Z: half its tube's extent in Z is the tube's radius.
      const tubes = (slot = 'primary') => {
        const group = x.highlightGroup.children.find((o) => o.userData.slot === slot);
        return group ? group.children.map((m) => {
          m.geometry.computeBoundingBox();
          const b = m.geometry.boundingBox;
          return { radius: (b.max.z - b.min.z) / 2, opacity: m.material.opacity };
        }) : null;
      };
      const settling = (slot = 'primary') => {
        const group = x.highlightGroup.children.find((o) => o.userData.slot === slot);
        return !!(group && group.userData.settle);
      };
      const later = (ms, slot = 'primary') => {
        const group = x.highlightGroup.children.find((o) => o.userData.slot === slot);
        if (group && group.userData.settle) group.userData.settle.t0 -= ms;
        x._settleHighlights();
      };
      const out = {};
      x.select(ring(7, 3));
      out.chosen = tubes(); out.chosenSettling = settling();
      later(200); out.held = tubes();
      later(450); out.midway = tubes(); out.midSettling = settling();
      later(5000); out.settled = tubes(); out.settledSettling = settling();
      x.preview(ring(8, 3.2)); out.previewed = tubes(); out.previewSettling = settling();
      x.preview(null);
      x._drawHighlight(x.selection); out.redrawn = tubes(); out.redrawnSettling = settling();
      x.setMarker('below', ring(8, 3.2)); out.second = tubes('below'); out.secondSettling = settling('below');
      later(5000, 'below'); out.secondSettled = tubes('below');
      x.setMarker('above', ring(9, 3.4)); out.above = tubes('above'); out.aboveSettling = settling('above');
      later(5000, 'above'); out.aboveSettled = !settling('above');
      x.pulse('above'); out.replayed = settling('above');
      out.colors = ['primary', 'above', 'below'].map((s) => x.markerColor(s));
      x.select(null);
      x.select({ kind: 'nodes', key: 'S/1', label: 'check', point: [10, 20, 3],
                 nodes: [{ xyz_um: [10, 20, 3], radius_um: 2 }], links: [] });
      out.nodes = tubes(); out.nodesSettling = settling();
      x.dispose(); x.__div.remove();
      return out;
    })()"""
    out = js(c, probe)
    chosen, settled = out['chosen'], out['settled']
    core0, glow0 = chosen[0]['radius'], chosen[1]['radius']
    r.check('a ring just chosen is drawn as it always was: a glow three times the tube, at 30 %',
            abs(glow0 / core0 - 3) < 0.01 and abs(chosen[1]['opacity'] - 0.3) < 1e-6 and out['chosenSettling'], chosen)
    r.check('and held there for a moment', out['held'][0]['radius'] == core0 and out['held'][1]['radius'] == glow0
            and out['held'][1]['opacity'] == chosen[1]['opacity'], out['held'])
    mid = out['midway']
    r.check('then it settles: between the two on the way',
            settled[0]['radius'] < mid[0]['radius'] < core0 and settled[1]['radius'] < mid[1]['radius'] < glow0
            and settled[1]['opacity'] < mid[1]['opacity'] < chosen[1]['opacity'] and out['midSettling'], out['midway'])
    r.check('the tube ends four fifths as thick, the glow two thirds as wide and as bright, and it stops',
            abs(settled[0]['radius'] / core0 - 0.8) < 0.01 and abs(settled[1]['radius'] / glow0 - 2 / 3) < 0.01
            and abs(settled[1]['opacity'] - 0.2) < 1e-6 and not out['settledSettling'], settled)
    shown, again = out['previewed'], out['redrawn']
    r.check('a ring that is only redrawn, or previewed from a menu row, is drawn settled at once',
            all(abs(a['radius'] - b['radius']) < 1e-6 and abs(a['opacity'] - b['opacity']) < 1e-6
                for a, b in zip(again, settled, strict=True))
            and abs(shown[1]['radius'] / shown[0]['radius'] - 2.5) < 0.01 and abs(shown[1]['opacity'] - 0.2) < 1e-6
            and not out['previewSettling'] and not out['redrawnSettling'], (shown, again))
    second, second_done = out['second'], out['secondSettled']
    r.check('the blue marker is chosen strong and settles the same way',
            out['secondSettling'] and abs(second[1]['radius'] / second[0]['radius'] - 3) < 0.01
            and abs(second_done[1]['radius'] / second[1]['radius'] - 2 / 3) < 0.01, (second, second_done))
    r.check('so is the red one, and a click on its row replays the emphasis',
            out['aboveSettling'] and out['aboveSettled'] and out['replayed']
            and abs(out['above'][1]['radius'] / out['above'][0]['radius'] - 3) < 0.01, out['above'])
    r.check('yellow, red and blue are three colors', len(set(out['colors'])) == 3, out['colors'])
    r.check('the skeleton check\'s rings, dozens at a time, are not pulsed and keep their faint glow',
            not out['nodesSettling'] and abs(out['nodes'][1]['opacity'] - 0.08) < 1e-6, out['nodes'])

    # A system that asks for less motion: every ring is settled at once.
    c.send('Emulation.setEmulatedMedia', features=[{'name': 'prefers-reduced-motion', 'value': 'reduce'}])
    still = js(c, probe)
    c.send('Emulation.setEmulatedMedia', features=[{'name': 'prefers-reduced-motion', 'value': 'no-preference'}])
    r.check('with reduced motion asked for, a chosen ring is settled at once',
            not still['chosenSettling'] and abs(still['chosen'][1]['opacity'] - 0.2) < 1e-6
            and abs(still['chosen'][0]['radius'] / core0 - 0.8) < 0.01, still['chosen'])


# -- undo ------------------------------------------------------------------------------

def planes(c: Chrome, r: Report):
    r.start('planes')
    out = js(c, """(() => {
      const result = {};
      for (const ortho of [false, true]) {
        const x = window.__make(); x.setPaused(true);
        // Two sheets 500 um apart: a scene several times longer than the camera's distance.
        x.setMesh('A/1', window.__sheet(40), { color: '#ffd400', opacity: 1 });
        x.setMesh('B/2', window.__sheet(40, 420, 280), { color: '#00d4ff', opacity: 1 });
        x.setOrthographic(ortho);
        x.resetView();
        const clipped = () => {
          x._syncPlanes(); x.camera.updateMatrixWorld(true); x.camera.updateProjectionMatrix();
          let bad = 0; let seen = 0;
          const v = x.camera.position.clone();
          for (const key of ['A/1', 'B/2']) {
            const obj = x._meshPart(key, 'surface'); obj.updateMatrixWorld(true);
            const pos = obj.geometry.attributes.position;
            for (let i = 0; i < pos.count; i += 7) {
              v.set(pos.getX(i), pos.getY(i), pos.getZ(i)).applyMatrix4(obj.matrixWorld);
              const p = v.clone().project(x.camera);
              // In front of the camera, and on the screen: the plane is what would cut it.
              const front = x.camera.isOrthographicCamera || v.clone().sub(x.camera.position).dot(x.camera.getWorldDirection(v.clone())) > 0;
              if (front && Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1) { seen += 1; if (p.z < -1 || p.z > 1) bad += 1; }
            }
          }
          return [bad, seen];
        };
        const steps = [];
        const t = x.controls.target; const cam = x.camera; const d0 = cam.position.distanceTo(t);
        const orbit = (az, el) => {
          cam.position.set(t.x + d0 * Math.cos(el) * Math.cos(az), t.y + d0 * Math.cos(el) * Math.sin(az), t.z + d0 * Math.sin(el));
          cam.lookAt(t); x.controls.update();
        };
        steps.push(['reset', ...clipped()]);
        for (const [az, el] of [[0.3, 0.2], [1.6, 0.5], [3.0, 1.0], [4.4, -0.3], [5.5, -0.9]]) {
          orbit(az, el); steps.push([`orbit ${az}/${el}`, ...clipped()]);
        }
        // A pan, then an orbit about the moved target: the camera sweeps a sphere that goes
        // through the scene, so cells end up on either side of the camera's own plane.
        const box = x.sceneBounds(); const mid = box.min.map((lo, i) => (lo + box.max[i]) / 2);
        t.set(mid[0] - 300, mid[1] + 250, mid[2] - 100);
        const d1 = 420;
        for (let az = 0; az < 6.3; az += 0.6) {
          for (const el of [-0.8, 0.1, 0.9]) {
            cam.position.set(t.x + d1 * Math.cos(el) * Math.cos(az), t.y + d1 * Math.cos(el) * Math.sin(az), t.z + d1 * Math.sin(el));
            cam.lookAt(t); x.controls.update();
            steps.push([`pan and orbit ${az.toFixed(1)}/${el}`, ...clipped()]);
          }
        }
        x.resetView(); steps.push(['reset again', ...clipped()]);
        result[ortho ? 'orthographic' : 'perspective'] = { near: cam.near, far: cam.far, steps };
        x.dispose(); x.__div.remove();
      }
      return result;
    })()""")
    for kind, data in out.items():
        bad = [(name, bad, seen) for name, bad, seen in data['steps'] if bad]
        r.check(f'{kind}: no vertex in front of the camera and on screen is cut by a near or far plane',
                not bad, f"{bad[:3]} (near {data['near']:.3g}, far {data['far']:.4g})")
        r.check(f'{kind}: the checks looked at vertices', sum(seen for _n, _b, seen in data['steps']) > 1000, data['steps'][:4])


def undo(c: Chrome, r: Report, cell: int):
    r.start(f'undo and the gradient editor (c{cell})')
    key = load_cell(c, cell)
    js(c, "window.__S = null; import('/static/js/state.js').then((m) => { window.__S = m; })")
    if not c.wait_for('window.__S', 20):
        raise RuntimeError('state.js did not load')
    time.sleep(1.0)

    def zhide():
        return js(c, 'JSON.stringify(window.sbfsem.renderer.zHide.get(%s) || null)' % json.dumps(key))

    def step(button: str):
        js(c, f"document.getElementById('{button}').click()")
        time.sleep(1.2)

    box = js(c, 'window.sbfsem.renderer.sceneBounds()')
    mid = (box['min'][2] + box['max'][2]) / 2
    js(c, 'window.__S.setDisplay(%s, { zMax: %r })' % (json.dumps(key), mid))
    time.sleep(1.0)
    r.check('a Z range hides part of the cell', zhide() != 'null')
    step('undo-btn')
    r.check('undo puts the Z range back', zhide() == 'null', zhide())
    step('redo-btn')
    r.check('redo applies it again', zhide() != 'null', zhide())

    js(c, "document.getElementById('toggle-display').click()")
    time.sleep(0.5)
    js(c, """(() => { const b = document.getElementById('lim-hold'); b.checked = true;
        b.dispatchEvent(new Event('change')); document.getElementById('lim-apply').click(); })()""")
    time.sleep(1.0)
    r.check('Crop and Hold took', js(c, "!!window.sbfsem.renderer.clipBox && document.getElementById('lim-hold').checked"))
    step('undo-btn')
    r.check('undo takes the crop and Hold off',
            js(c, "!window.sbfsem.renderer.clipBox && !document.getElementById('lim-hold').checked"))
    step('redo-btn')
    r.check('redo puts them back',
            js(c, "!!window.sbfsem.renderer.clipBox && document.getElementById('lim-hold').checked"))

    js(c, """(() => { const b = document.getElementById('ctx-inl'); b.checked = true;
        b.dispatchEvent(new Event('change')); })()""")
    if c.wait_for('window.sbfsem.renderer.context && window.sbfsem.renderer.context.size > 0', 40):
        step('undo-btn')
        r.check('undo unticks a boundary sheet and hides it',
                js(c, "!document.getElementById('ctx-inl').checked && "
                      "[...window.sbfsem.renderer.context.values()].every((o) => !o.visible)"))
    else:
        r.skip('boundary sheet', 'this volume has no INL boundary to load')

    stored = ("(() => { let found = null; for (const k of Object.keys(localStorage)) { if (!k.startsWith('sbfsem.session')) "
              "continue; try { const m = JSON.parse(localStorage.getItem(k))?.ui?.display?.colormap?.map; if (m) found = m; } "
              "catch (e) {} } return found; })()")
    js(c, """(() => { const m = document.getElementById('opt-colormap'); m.value = 'StepSeq';
        m.dispatchEvent(new Event('change')); })()""")
    time.sleep(0.4)
    js(c, "(() => { const d = document.getElementById('grad-editor'); d.open = true; d.dispatchEvent(new Event('toggle')); })()")
    time.sleep(0.5)
    r.check('opening the gradient editor leaves the cells\' map alone',
            js(c, 'window.sbfsem.renderer.colormap.map') == 'StepSeq', js(c, 'window.sbfsem.renderer.colormap.map'))
    js(c, "document.getElementById('grad-reverse').click()")
    time.sleep(0.8)
    r.check('the first edit previews', js(c, 'window.sbfsem.renderer.colormap.map') == '__preview')
    r.check('a view saved mid-preview names the menu\'s map, not the preview', js(c, stored) == 'StepSeq', js(c, stored))
    js(c, "(() => { const d = document.getElementById('grad-editor'); d.open = false; d.dispatchEvent(new Event('toggle')); })()")
    time.sleep(0.5)
    r.check('closing the editor ends the preview', js(c, 'window.sbfsem.renderer.colormap.map') == 'StepSeq')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8765', help='the dev server')
    parser.add_argument('--chrome', help='Chrome for Testing, if not in Playwright\'s cache')
    parser.add_argument('--cell', type=int, help='load this cell for the undo checks')
    parser.add_argument('--only', help='comma list of: lifecycle, layers, planes, glow, undo')
    parser.add_argument('--cycles', type=int, default=20, help='Render compare pane rebuilds')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    wanted = set((args.only or 'lifecycle,layers,planes,glow,undo').split(','))
    report = Report(args.verbose)
    chrome = find_chrome(args.chrome)
    page = open_page(chrome, args.url)

    def run(name: str, section, *section_args):
        """One section, whose script error is a failed check and not the end of the run.

        A page script that throws (a renamed method, a layer a change dropped) used
        to end the whole run with a traceback, no FAIL line and the sections after
        it never run.
        """
        try:
            section(page, report, *section_args)
        except Exception as error:  # noqa: BLE001 - any error here is the finding
            report.check(f'the {name} section ran to its end', False,
                         f'{type(error).__name__}: {str(error)[:400]}')

    try:
        if 'layers' in wanted:
            run('layers', layers)
        if 'planes' in wanted:
            run('planes', planes)
        if 'glow' in wanted:
            run('glow', glow)
        if 'lifecycle' in wanted:
            run('lifecycle', lifecycle, args.cycles)
        if 'undo' in wanted:
            if args.cell:
                run('undo', undo, args.cell)
            else:
                report.start('undo')
                report.skip('undo, crop, Hold and the gradient editor', 'needs --cell ID')
        report.start('the page')
        thrown = [e['params']['exceptionDetails'].get('text', '') for e in page.events
                  if e.get('method') == 'Runtime.exceptionThrown']
        failed = [e['params']['entry']['text'] for e in page.events
                  if e.get('method') == 'Log.entryAdded' and e['params']['entry']['level'] == 'error'
                  and 'favicon' not in e['params']['entry'].get('url', '')]
        report.check('nothing was thrown and nothing logged an error', not thrown and not failed,
                     (thrown + failed)[:3])
    finally:
        page.close()
    return report.summary()


if __name__ == '__main__':
    sys.exit(main())
