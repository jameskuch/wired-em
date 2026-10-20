// The Display panel's logic (static/js/display.js) and the colormap sampler it and
// the renderer share (static/js/colormaps.js), against a stub renderer and a fake
// DOM: the banded colorbar, depth colors against a per-vertex reference, blank
// number boxes, the 2D box, and a restore that reverts a crop, Hold, a volume
// shift and a context sheet as well as applying them.  Run with
//
//     node tests/js/test_display.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');

// -- a fake DOM: every id is an element that remembers what was set and listened --------------------
const elements = new Map();
function makeElement(id) {
  const listeners = new Map();
  const el = {
    id, checked: false, value: '', hidden: false, disabled: false, textContent: '', className: '',
    style: {}, dataset: {}, options: [], children: [], listeners,
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
    removeEventListener() {},
    fire(type, extra = {}) { for (const fn of listeners.get(type) || []) fn({ target: el, ...extra }); },
    replaceChildren(...kids) { el.options = kids; el.children = kids; },
    append(...kids) { el.children.push(...kids); },
    querySelector() { return null; },
  };
  return el;
}
const byId = (id) => {
  if (!elements.has(id)) elements.set(id, makeElement(id));
  return elements.get(id);
};
globalThis.document = {
  getElementById: byId,
  createElement: () => makeElement(''),
  createTextNode: (text) => ({ text }),
};
globalThis.Option = class { constructor(text, value) { this.text = text; this.value = value; } };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};

const C = await import(pathToFileURL(path.join(jsDir, 'colormaps.js')).href);
const D = await import(pathToFileURL(path.join(jsDir, 'display.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// -- colormaps: depth colors, bit for bit --------------------------------------------------------------

/** The sampler as it was before the band table: one colormapSample per vertex. */
function referenceDepthColors(values, { map, levels = 256, invert = false }) {
  const out = new Float32Array(values.length * 3);
  const n = Math.max(2, Math.min(256, Math.round(levels) || 256));
  for (let i = 0; i < values.length; i += 1) {
    let t = Math.min(1, Math.max(0, Number(values[i]) || 0));
    if (invert) t = 1 - t;
    const band = Math.min(n - 1, Math.floor(t * n));
    const [r, g, b] = C.colormapSample(map, n === 1 ? 0.5 : band / (n - 1));
    out[i * 3] = r; out[i * 3 + 1] = g; out[i * 3 + 2] = b;
  }
  return out;
}

/** Values that hit every band, the edges, and what a depth array can hold (NaN, out of range). */
function values() {
  const out = [0, 1, -0.5, 1.5, NaN, undefined, 0.5, 0.999999, 1e-9];
  let seed = 12345;
  const next = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  for (let i = 0; i < 5000; i += 1) out.push(next());
  for (let k = 0; k <= 256; k += 1) out.push(k / 256, (k + 0.5) / 256);
  return out;
}

test('depthColors: the band table gives the per-vertex sampler\'s colors exactly, for every map and level count', () => {
  C.registerGradient('__test', [[0, '#000000'], [0.4, '#ff0000'], [1, '#ffffff']]);
  const vals = values();
  for (const map of [C.DEFAULT_COLORMAP, 'StepSeq', 'Jet', 'AntiJet', 'RedBlue', '__test', 'no such map']) {
    for (const levels of [2, 3, 4, 8, 64, 255, 256, 300, 0, NaN, undefined]) {
      for (const invert of [false, true]) {
        const got = C.depthColors(vals, { map, levels, invert });
        const want = referenceDepthColors(vals, { map, levels, invert });
        assert.equal(got.length, want.length);
        for (let i = 0; i < got.length; i += 1) {
          assert.ok(Object.is(got[i], want[i]), `${map} levels=${levels} invert=${invert} at ${i}: ${got[i]} vs ${want[i]}`);
        }
      }
    }
  }
  C.unregisterGradient('__test');
});

test('bandColors: slot i is the color depthColors gives a value in that slot, inverted or not', () => {
  for (const [map, levels] of [['StepSeq', 4], [C.DEFAULT_COLORMAP, 8], ['Jet', 7]]) {
    for (const invert of [false, true]) {
      const slots = C.bandColors(map, levels, invert);
      assert.equal(slots.length, levels);
      slots.forEach((rgb, i) => {
        const v = (i + 0.5) / levels;   // the middle of the slot, as a value along the bar
        const painted = C.depthColors([v], { map, levels, invert });
        assert.deepEqual(rgb.map(Math.fround), Array.from(painted), `${map} slot ${i} invert=${invert}`);
      });
    }
  }
});

test('isPreviewGradient: only the editor\'s private names', () => {
  assert.equal(C.isPreviewGradient('__preview'), true);
  assert.equal(C.isPreviewGradient('Spectral'), false);
  assert.equal(C.isPreviewGradient('My ★ map'), false);
});

// -- the colorbar -----------------------------------------------------------------------------------------

/** Every `rgb(r,g,b) p%` stop of a gradient string. */
function parseStops(gradient) {
  return [...gradient.matchAll(/rgb\((\d+),(\d+),(\d+)\) ([\d.]+)%/g)].map((m) => ({
    rgb: [Number(m[1]), Number(m[2]), Number(m[3])], at: Number(m[4]),
  }));
}
const to255 = (rgb) => rgb.map((v) => Math.round(v * 255));

test('colorbarGradient: 4 levels is 4 flat blocks of the colors the surface is painted in', () => {
  for (const invert of [false, true]) {
    const stops = parseStops(D.colorbarGradient('StepSeq', 4, invert));
    assert.equal(stops.length, 8, 'two stops a block: a hard edge');
    for (let band = 0; band < 4; band += 1) {
      const [a, b] = [stops[band * 2], stops[band * 2 + 1]];
      assert.deepEqual(a.rgb, b.rgb, 'a block is one color');
      assert.ok(Math.abs(a.at - band * 25) < 1e-6 && Math.abs(b.at - (band + 1) * 25) < 1e-6);
      const v = (band + 0.5) / 4;
      const painted = C.depthColors([v], { map: 'StepSeq', levels: 4, invert });
      assert.deepEqual(a.rgb, to255(Array.from(painted)), `block ${band} invert=${invert}`);
    }
  }
});

test('colorbarGradient: above 64 levels the bar is smooth, as it always was', () => {
  const stops = parseStops(D.colorbarGradient(C.DEFAULT_COLORMAP, 256, false));
  assert.equal(stops.length, 64);
  assert.deepEqual(stops[0].rgb, to255(C.colormapSample(C.DEFAULT_COLORMAP, 0)));
  assert.deepEqual(stops[63].rgb, to255(C.colormapSample(C.DEFAULT_COLORMAP, 1)));
  const flipped = parseStops(D.colorbarGradient(C.DEFAULT_COLORMAP, 256, true));
  assert.deepEqual(flipped[0].rgb, stops[63].rgb, 'inverted: the ends swap');
});

test('colorbarGradient: levels that are not a count fall back to the default 256 (smooth)', () => {
  assert.equal(parseStops(D.colorbarGradient(C.DEFAULT_COLORMAP, 0, false)).length, 64);
  assert.equal(parseStops(D.colorbarGradient(C.DEFAULT_COLORMAP, NaN, false)).length, 64);
  assert.equal(parseStops(D.colorbarGradient(C.DEFAULT_COLORMAP, 1, false)).length, 4, '1 level is 2 blocks, the minimum');
});

// -- helpers ------------------------------------------------------------------------------------------------

test('readNumber: blank and non-numbers are no value, zero is a value', () => {
  assert.equal(D.readNumber(''), null);
  assert.equal(D.readNumber('   '), null);
  assert.equal(D.readNumber(undefined), null);
  assert.equal(D.readNumber(null), null);
  assert.equal(D.readNumber('abc'), null);
  assert.equal(D.readNumber('Infinity'), null);
  assert.equal(D.readNumber('0'), 0);
  assert.equal(D.readNumber(' -37.5 '), -37.5);
  assert.equal(D.readNumber('1e3'), 1000);
});

test('isTwoDView: orthographic and along Z from either end, within half a degree', () => {
  assert.equal(D.isTwoDView([0, 0, 10], true), true);
  assert.equal(D.isTwoDView([0, 0, -10], true), true, 'XY2 is face on too');
  assert.equal(D.isTwoDView([0, 0, 10], false), false, 'perspective is not 2D');
  assert.equal(D.isTwoDView([0, 0, 0], true), false);
  assert.equal(D.isTwoDView([0.05, 0, 10], true), true, 'about 0.29 degrees');
  assert.equal(D.isTwoDView([0.2, 0, 10], true), false, 'about 1.1 degrees');
  assert.equal(D.isTwoDView([10, 0, 0], true), false);
});

// -- the panel against a stub renderer ---------------------------------------------------------------------

class Vec {
  constructor(x = 0, y = 0, z = 0) { this.x = x; this.y = y; this.z = z; }
  clone() { return new Vec(this.x, this.y, this.z); }
  sub(o) { this.x -= o.x; this.y -= o.y; this.z -= o.z; return this; }
  length() { return Math.hypot(this.x, this.y, this.z); }
  set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
}

function stubRenderer() {
  const calls = [];
  const controlsListeners = [];
  const r = {
    calls,
    colormap: { map: C.DEFAULT_COLORMAP, levels: 256, invert: false },
    camera: { position: new Vec(0, 0, 10), isOrthographicCamera: false, up: new Vec(0, 0, 1), updateProjectionMatrix() {} },
    controls: {
      target: new Vec(),
      update() {},
      addEventListener(type, fn) { if (type === 'change') controlsListeners.push(fn); },
    },
    container: { clientWidth: 800 },
    clipBox: null,
    volumeOffsets: new Map(),
    moveCamera(x, y, z) { r.camera.position.set(x, y, z); for (const fn of controlsListeners) fn(); },
    setInverted(on) { calls.push(['invert', on]); },
    setFlipZ(on) { calls.push(['flipz', on]); },
    setColormap(spec) { r.colormap = { ...r.colormap, ...spec }; },
    setOrthographic(on) { r.camera.isOrthographicCamera = on; for (const fn of controlsListeners) fn(); },
    setViewpoint() { r.moveCamera(0, 0, 10); },
    setClipBox(box) { r.clipBox = box; calls.push(['clip', box]); },
    sceneBounds() { return { min: [0, 0, 0], max: [10, 20, 30] }; },
    volumeOffset(source) { return r.volumeOffsets.get(source) || [0, 0, 0]; },
    setVolumeOffset(source, xyz) {
      if (xyz.every((v) => v === 0)) r.volumeOffsets.delete(source); else r.volumeOffsets.set(source, xyz);
    },
    volumeOffsetMap() { return Object.fromEntries(r.volumeOffsets); },
    setContextMesh(key, mesh, opts) { calls.push(['sheet', key, opts.opacity]); },
    setContextStyle(key, style) { calls.push(['sheetStyle', key, style]); },
    removeContextMesh(key) { calls.push(['sheetRemove', key]); },
    micronsPerPixel: () => 0.1,
  };
  return r;
}

function panel() {
  elements.clear();
  byId('scale-bar'); byId('colorbar');
  byId('opt-levels').value = '256';
  byId('ctx-opacity').value = '0.45';
  const renderer = stubRenderer();
  const api = {
    boundaryMesh: async () => ({ n_faces: 1 }),
    vesselMesh: async () => ({ n_faces: 1 }),
  };
  const state = { neurons: new Map(), selection: new Set(), volumes: [], source: 'S' };
  const statuses = [];
  const display = new D.DisplayPanel({
    renderer, api, state, onChange() {}, onStatus: (m, k) => statuses.push([m, k]),
  });
  return { display, renderer, state, statuses };
}

test('snapshot: a map the gradient editor is previewing is stored as the map the menu names', () => {
  const { display, renderer } = panel();
  byId('opt-colormap').value = 'StepSeq';
  renderer.colormap.map = '__preview';
  assert.equal(display.snapshot().colormap.map, 'StepSeq');
  renderer.colormap.map = 'Jet';
  assert.equal(display.snapshot().colormap.map, 'Jet', 'a real map is stored as it is');
});

test('restore: a snapshot with the crop, Hold, volume shifts and sheets off reverts all four', async () => {
  const { display, renderer } = panel();
  const clear = display.snapshot();
  assert.equal(clear.clip_box, null);
  assert.equal(clear.hold_limits, false);
  assert.deepEqual(clear.volume_offsets, {});
  assert.equal(clear.context.inl, false);

  // The user then crops, holds, shifts a volume and shows the INL sheet.
  renderer.setClipBox({ min: [1, 1, 1], max: [5, 5, 5] });
  display.holdLimits = true;
  byId('lim-hold').checked = true;
  renderer.setVolumeOffset('A', [3, 0, 0]);
  byId('ctx-inl').checked = true;
  await display._toggleBoundary('INL', byId('ctx-inl'));
  assert.equal(display.contextLoaded.get('boundary:INL'), 'S');

  renderer.calls.length = 0;
  await display.restore(clear);
  assert.equal(renderer.clipBox, null, 'the crop is off');
  assert.equal(display.holdLimits, false, 'Hold is off');
  assert.equal(byId('lim-hold').checked, false);
  assert.equal(renderer.volumeOffsets.size, 0, 'the volume is back in its frame');
  assert.equal(byId('ctx-inl').checked, false);
  assert.deepEqual(renderer.calls.filter((c) => c[0] === 'sheetStyle' && c[1] === 'boundary:INL').at(-1),
    ['sheetStyle', 'boundary:INL', { visible: false }], 'the sheet is hidden');
});

test('restore: a snapshot with them on applies them, and a volume it does not list is reset', async () => {
  const { display, renderer } = panel();
  renderer.setVolumeOffset('stale', [9, 9, 9]);
  await display.restore({
    clip_box: { min: [0, 0, 0], max: [2, 2, 2] }, hold_limits: true,
    volume_offsets: { A: [1, 2, 3] }, context: { inl: true, gcl: false, vessels: false, opacity: 0.3 },
  });
  assert.deepEqual(renderer.clipBox, { min: [0, 0, 0], max: [2, 2, 2] });
  assert.equal(display.holdLimits, true);
  assert.equal(byId('lim-hold').checked, true);
  assert.deepEqual([...renderer.volumeOffsets.keys()], ['A']);
  assert.equal(byId('ctx-inl').checked, true);
  assert.ok(renderer.calls.some((c) => c[0] === 'sheet' && c[1] === 'boundary:INL' && c[2] === 0.3));
});

test('restore: a field the snapshot does not carry is left alone (a session saved by an older build)', async () => {
  const { display, renderer } = panel();
  renderer.setClipBox({ min: [1, 1, 1], max: [5, 5, 5] });
  display.holdLimits = true;
  renderer.setVolumeOffset('A', [3, 0, 0]);
  await display.restore({ invert: true });
  assert.deepEqual(renderer.clipBox, { min: [1, 1, 1], max: [5, 5, 5] });
  assert.equal(display.holdLimits, true);
  assert.equal(renderer.volumeOffsets.size, 1);
  assert.equal(byId('opt-invert').checked, true);
});

test('restore: opacity reaches the sheets already loaded', async () => {
  const { display, renderer } = panel();
  byId('ctx-gcl').checked = true;
  await display._toggleBoundary('GCL', byId('ctx-gcl'));
  renderer.calls.length = 0;
  await display.restore({ context: { inl: false, gcl: true, vessels: false, opacity: 0.8 } });
  assert.ok(renderer.calls.some((c) => c[0] === 'sheetStyle' && c[1] === 'boundary:GCL' && c[2].opacity === 0.8));
});

test('a sheet unticked (or undone) while it loads is loaded but not shown when it arrives', async () => {
  const { display, renderer } = panel();
  let arrive;
  display.api.boundaryMesh = () => new Promise((resolve) => { arrive = () => resolve({ n_faces: 1 }); });
  byId('ctx-inl').checked = true;
  const loading = display._toggleBoundary('INL', byId('ctx-inl'));
  byId('ctx-inl').checked = false;
  arrive();
  await loading;
  assert.equal(display.contextLoaded.get('boundary:INL'), 'S', 'kept, so ticking again is instant');
  assert.deepEqual(renderer.calls.filter((c) => c[0] === 'sheetStyle').at(-1),
    ['sheetStyle', 'boundary:INL', { visible: false }]);
  // Still ticked on arrival: nothing hides it.
  renderer.calls.length = 0;
  display.contextLoaded.clear();
  byId('ctx-inl').checked = true;
  const again = display._toggleBoundary('INL', byId('ctx-inl'));
  arrive();
  await again;
  assert.equal(renderer.calls.filter((c) => c[0] === 'sheetStyle').length, 0);
});

test('the blood vessels unticked (or undone) while they load are loaded but not shown when they arrive', async () => {
  const { display, renderer } = panel();
  let arrive;
  display.api.vesselMesh = () => new Promise((resolve) => { arrive = () => resolve({ n_faces: 1 }); });
  byId('ctx-vessels').checked = true;
  const loading = display._toggleVessels(byId('ctx-vessels'));
  byId('ctx-vessels').checked = false;
  arrive();
  await loading;
  assert.equal(display.contextLoaded.get('vessels'), 'S', 'kept, so ticking again is instant');
  assert.deepEqual(renderer.calls.filter((c) => c[0] === 'sheetStyle').at(-1),
    ['sheetStyle', 'vessels', { visible: false }]);
});

test('restore: a snapshot with the GCL sheet and the vessels off hides both, not only the INL sheet', async () => {
  const { display, renderer } = panel();
  for (const [id, toggle] of [['ctx-gcl', () => display._toggleBoundary('GCL', byId('ctx-gcl'))],
    ['ctx-vessels', () => display._toggleVessels(byId('ctx-vessels'))]]) {
    byId(id).checked = true;
    await toggle();
  }
  renderer.calls.length = 0;
  await display.restore({ context: { inl: false, gcl: false, vessels: false, opacity: 0.45 } });
  assert.equal(byId('ctx-gcl').checked, false);
  assert.equal(byId('ctx-vessels').checked, false);
  const hidden = (key) => renderer.calls.filter((c) => c[0] === 'sheetStyle' && c[1] === key).at(-1);
  assert.deepEqual(hidden('boundary:GCL'), ['sheetStyle', 'boundary:GCL', { visible: false }]);
  assert.deepEqual(hidden('vessels'), ['sheetStyle', 'vessels', { visible: false }]);
});

test('restore: every sheet box is set before the first request, so the view saved right after is the restored one', async () => {
  const { display } = panel();
  byId('ctx-gcl').checked = true;
  await display._toggleBoundary('GCL', byId('ctx-gcl'));
  // Undo of "tick GCL": the session is saved the moment applyHistory returns from
  // restore()'s first await, before the sheets' requests (or their microtasks) are done.
  let arrive;
  display.api.boundaryMesh = () => new Promise((resolve) => { arrive = () => resolve({ n_faces: 1 }); });
  const restoring = display.restore({ context: { inl: true, gcl: false, vessels: false, opacity: 0.45 } });
  const saved = display.snapshot().context;
  assert.deepEqual([saved.inl, saved.gcl, saved.vessels], [true, false, false],
    'the GCL box was still ticked while the INL sheet loaded');
  arrive();
  await restoring;
});

test('restore: the sheets to hide are hidden at once, not after a slow sheet has loaded', async () => {
  const { display, renderer } = panel();
  byId('ctx-gcl').checked = true;
  await display._toggleBoundary('GCL', byId('ctx-gcl'));
  let arrive;
  display.api.boundaryMesh = () => new Promise((resolve) => { arrive = () => resolve({ n_faces: 1 }); });
  renderer.calls.length = 0;
  const restoring = display.restore({ context: { inl: true, gcl: false, vessels: false, opacity: 0.45 } });
  assert.deepEqual(renderer.calls.filter((c) => c[0] === 'sheetStyle' && c[1] === 'boundary:GCL').at(-1),
    ['sheetStyle', 'boundary:GCL', { visible: false }], 'hidden before the INL request is answered');
  arrive();
  await restoring;
});

/** A promise that fails instead of hanging, so a regression is a failed case, not a stuck run. */
function settles(promise, what, ms = 1500) {
  let timer;
  const late = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${what} did not settle`)), ms); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

test('restore: a second restore started while the first waits for a sheet is not overridden by it', async () => {
  const { display } = panel();
  const requested = [];
  const resolvers = [];
  display.api.boundaryMesh = (source, layer) => new Promise((resolve) => {
    requested.push(layer);
    resolvers.push(() => resolve({ n_faces: 1 }));
  });
  // Rapid Ctrl+Z: the first restore wants INL and GCL and is waiting on a slow INL fetch ...
  const first = display.restore({ context: { inl: true, gcl: true, vessels: false, opacity: 0.45 } });
  assert.deepEqual(requested, ['INL']);
  // ... when the second, which wants everything off, comes and completes.
  await display.restore({ context: { inl: false, gcl: false, vessels: false, opacity: 0.45 } });
  resolvers[0]();                             // the INL sheet finally arrives
  await settles(first, 'the first restore');
  assert.deepEqual(requested, ['INL'], 'the first restore never went on to ask for GCL');
  const ctx = display.snapshot().context;
  assert.deepEqual([ctx.inl, ctx.gcl, ctx.vessels], [false, false, false]);
  assert.equal(byId('ctx-gcl').checked, false);
});

test('restore: a later restore that carries no sheets does not cut the earlier one short', async () => {
  const { display } = panel();
  const requested = [];
  const resolvers = [];
  display.api.boundaryMesh = (source, layer) => new Promise((resolve) => {
    requested.push(layer);
    resolvers.push(() => resolve({ n_faces: 1 }));
  });
  const first = display.restore({ context: { inl: true, gcl: true, vessels: false, opacity: 0.45 } });
  await display.restore({ invert: true });    // an older session: no context at all
  resolvers[0]();
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(requested, ['INL', 'GCL'], 'the ticked GCL box still gets its sheet');
  resolvers[1]();
  await first;
});

test('restore: a crop equal to the current one leaves the limit boxes someone is typing in alone', async () => {
  const { display, renderer } = panel();
  const crop = { min: [1, 1, 1], max: [5, 5, 5] };
  renderer.setClipBox(crop);
  display.readLimits();
  byId('lim-x0').value = '3.7';              // typed, not yet applied
  await display.restore({ clip_box: { min: [1, 1, 1], max: [5, 5, 5] } });
  assert.equal(byId('lim-x0').value, '3.7', 'an undo step about something else does not overwrite it');
  await display.restore({ clip_box: { min: [2, 2, 2], max: [6, 6, 6] } });
  assert.equal(byId('lim-x0').value, '2.0', 'a different crop does');
});

test('the 2D box: ticking it turns the view and stays ticked; leaving 2D unticks it', () => {
  const { renderer } = panel();
  renderer.moveCamera(5, 5, 5);
  byId('opt-2d').checked = true;
  byId('opt-2d').fire('change');
  assert.equal(byId('opt-2d').checked, true, 'the turn to XY1 passes through states that are not 2D');
  assert.equal(byId('ortho').checked, true);
  renderer.moveCamera(0.02, 0, 10);
  assert.equal(byId('opt-2d').checked, true, 'a hair off axis is still 2D');
  renderer.moveCamera(4, 0, 10);
  assert.equal(byId('opt-2d').checked, false, 'orbited away');
  // Perspective is not 2D either.
  byId('opt-2d').checked = true;
  renderer.moveCamera(0, 0, 10);
  renderer.setOrthographic(false);
  assert.equal(byId('opt-2d').checked, false);
});

test('the 2D box is part of the snapshot', () => {
  const { display, renderer } = panel();
  assert.equal(display.snapshot().two_d, false);
  renderer.camera.isOrthographicCamera = true;
  renderer.moveCamera(0, 0, 10);
  byId('opt-2d').checked = true;
  assert.equal(display.snapshot().two_d, true);
});

test('restoring a 2D view over a 2D camera keeps the box ticked', async () => {
  const { display, renderer } = panel();
  renderer.camera.isOrthographicCamera = true;
  renderer.moveCamera(0, 0, 10);
  byId('opt-2d').checked = false;
  await display.restore({ two_d: true });
  assert.equal(byId('opt-2d').checked, true);
});

test('a restored 2D box waits for the camera it describes: a session restore puts the camera back seconds later', async () => {
  const { display, renderer } = panel();
  // After a reload the camera is the default perspective one, orbited away from Z.
  renderer.moveCamera(7, 0, 3);
  await display.restore({ two_d: true });
  assert.equal(byId('opt-2d').checked, true,
    'restore() does not judge the box against the camera of before the restore');
  // The cells have loaded; the saved camera arrives: orthographic, straight down Z.
  renderer.camera.isOrthographicCamera = true;
  renderer.moveCamera(0, 0, 10);
  assert.equal(byId('opt-2d').checked, true, 'and the view it was saved with keeps it ticked');
});

test('syncTwoD: whoever has decided the camera after a restore can judge the box (Discard, no cells)', async () => {
  const { display, renderer } = panel();
  renderer.moveCamera(7, 0, 3);              // the view that is there stays: its saved camera is not coming back
  await display.restore({ two_d: true });
  assert.equal(byId('opt-2d').checked, true, 'restore() leaves the judging to its caller');
  display.syncTwoD();
  assert.equal(byId('opt-2d').checked, false);
  renderer.camera.isOrthographicCamera = true;
  renderer.moveCamera(0, 0, 10);
  byId('opt-2d').checked = true;
  display.syncTwoD();
  assert.equal(byId('opt-2d').checked, true, 'and a 2D view keeps its tick');
});

test('a restored 2D box is unticked by the camera that arrives, if that view is not 2D', async () => {
  const { display, renderer } = panel();
  renderer.camera.isOrthographicCamera = true;
  renderer.moveCamera(0, 0, 10);
  await display.restore({ two_d: true });
  assert.equal(byId('opt-2d').checked, true);
  renderer.moveCamera(7, 0, 3);              // a stale snapshot, a camera that is not 2D
  assert.equal(byId('opt-2d').checked, false);
});

test('Set view: a blank angle keeps the current one instead of becoming 0', () => {
  const { display, renderer } = panel();
  renderer.camera.up = { set() {} };
  renderer._syncControlsUp = () => {};
  renderer.camera.position.set(-10, -10, 10);   // az 45 (from -Y, counterclockwise), el about 35.3
  const before = D.cameraAngles(renderer);
  byId('cam-az').value = '';
  byId('cam-el').value = '20';
  byId('cam-dist').value = '';
  byId('cam-apply').fire('click');
  const after = D.cameraAngles(renderer);
  assert.ok(Math.abs(after.azimuth - before.azimuth) < 1e-6, `azimuth ${before.azimuth} -> ${after.azimuth}`);
  assert.ok(Math.abs(after.elevation - 20) < 1e-6);
  assert.ok(Math.abs(after.distance - before.distance) < 1e-6);
  void display;
});

test('Crop: a blank limit is the scene\'s extent on that side, not 0', () => {
  const { renderer } = panel();
  for (const id of ['lim-x0', 'lim-y0', 'lim-z0', 'lim-x1', 'lim-y1', 'lim-z1']) byId(id).value = '';
  byId('lim-x0').value = '2';
  byId('lim-z1').value = '12';
  byId('lim-apply').fire('click');
  // sceneBounds: min [0,0,0], max [10,20,30]
  assert.deepEqual(renderer.clipBox, { min: [2, 0, 0], max: [10, 20, 12] });
});

// -- run -----------------------------------------------------------------------------------------------
let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack || error}`);
  }
}
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
