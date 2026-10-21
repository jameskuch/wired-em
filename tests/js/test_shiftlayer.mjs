// The Position shift layer between its data and a renderer (static/js/shiftlayer.js)
// and the main window's controller (shiftcontrol.js), against a stub renderer and a
// fake DOM: what a cell's settings build, how a slice moves across cells and
// volumes, the thresholds a pair needs, the chip's words, and the keys.  Run with
//
//     node tests/js/test_shiftlayer.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const S = await load('shifts.js');
const L = await load('shiftlayer.js');
const { ShiftController, onlyShiftKeys } = await load('shiftcontrol.js');
const { shiftSummary } = await load('shiftpanel.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, tol = 1e-6, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} vs ${b}`);

/** A `/nodes` row. */
function node(id, section, x, y, { shift = null, live = null, radius = 1, type = 1 } = {}) {
  return {
    id, parent_id: 1, z_section: section, radius_um: radius, type_code: type,
    xyz_um: [x, y, section * 0.07], shift_um: shift, live_shift_um: live,
  };
}
/** Sections 10..19, each moved (0.3 + 0.1 i, 0) by the correction; every third also by the transforms. */
const nodes = Array.from({ length: 10 }, (_, i) => node(100 + i, 10 + i, i * 5, 0, {
  shift: [0.3 + 0.1 * i, 0], live: i % 3 === 0 ? [0.01 * (i + 1), 0] : null,
}));
const applied = (n) => ({ requested: true, applied: true, n_moved: n });
const summary = { section_correction: applied(10), live_registration: applied(4) };
const base = { nodes, summary, display: {}, cellColor: '#2f6fed' };

// -- building a layer ------------------------------------------------------------------------

test('a layer: the pair drawn, one arrow per mark, linear colors, and what the renderer takes', () => {
  const layer = L.buildLayer(base);
  assert.equal(layer.empty, false);
  assert.equal(layer.pair, 'correction');
  assert.equal(layer.fellBack, false);
  assert.equal(layer.sel.n, 10, 'all ten are at or above 0.25 um');
  assert.equal(layer.arrows.positions.length, 10 * S.ARROW_VERTS * 3);
  assert.equal(layer.arrows.colors.length, 10 * S.ARROW_VERTS * 3);
  assert.equal(layer.rings, null, 'rings are off by default');
  assert.equal(layer.plane, null, 'no plane outside slice mode');
  assert.deepEqual(Object.keys(layer.forRenderer).sort(), ['arrows', 'meta', 'plane', 'recolor', 'rings']);
  assert.equal(layer.forRenderer.arrows.positions, layer.arrows.positions);
  assert.equal(layer.forRenderer.meta, layer);
  // The ramp is measured in sRGB; the vertex buffer holds linear light.
  const first = S.markColors(layer.field, layer.sel, { max: layer.colorMax }).slice(0, 3);
  const lin = L.linearize(first);
  for (let c = 0; c < 3; c += 1) close(layer.arrows.colors[c], lin[c], 1e-6, 'linear color');
  assert.ok(layer.arrows.colors[0] < first[0] || first[0] === 0, 'linear is darker than sRGB');
  assert.equal(layer.badge, S.badgeText(layer.k));
  assert.match(layer.facts(null), /^10 of 10 moved shown/);
});

test('arrows off and rings on build only the rings; nothing to draw is said by both being null', () => {
  const rings = L.buildLayer({ ...base, display: { shiftArrows: false, shiftRings: true } });
  assert.equal(rings.arrows, null);
  assert.ok(rings.rings.positions.length > 0);
  assert.equal(rings.rings.n, 10);
  const none = L.buildLayer({ ...base, display: { shiftArrows: false, shiftRings: false } });
  assert.equal(none.arrows, null);
  assert.equal(none.rings, null);
  const renderer = new StubRenderer();
  assert.equal(L.drawLayer(renderer, 'v/1', none, null), false);
  assert.deepEqual(renderer.calls.map((c) => c[0]), ['removeShifts']);
});

test('the colors made for the other background have the buffers\' sizes, and the plane follows it', () => {
  const layer = L.buildLayer({ ...base, display: { shiftRings: true, shiftSlice: true } });
  const dark = layer.recolor(false);
  const light = layer.recolor(true);
  assert.equal(dark.arrows.length, layer.arrows.colors.length);
  assert.equal(light.rings.length, layer.rings.colors.length);
  assert.notDeepEqual(Array.from(dark.arrows), Array.from(light.arrows), 'a ramp per background');
  assert.equal(dark.plane, '#ffffff');
  assert.equal(light.plane, '#16191d');
  // The built buffers are for the background the layer was built on.
  const onLight = L.buildLayer({ ...base, inverted: true });
  assert.deepEqual(Array.from(onLight.arrows.colors), Array.from(L.buildLayer(base).recolor(true).arrows));
  assert.equal(onLight.plane, null);
});

test('no pair to show is an empty layer that says why; a fallback pair is flagged', () => {
  const none = L.buildLayer({ ...base, summary: { section_correction: null, live_registration: null } });
  assert.equal(none.empty, true);
  assert.equal(none.pair, null);
  assert.match(none.availability.correction.reason, /is off for this cell/);
  assert.match(none.availability.transforms.reason, /transforms are off for this cell/);
  const fell = L.buildLayer({ ...base, summary: { section_correction: null, live_registration: applied(4) } });
  assert.equal(fell.pair, 'transforms');
  assert.equal(fell.wanted, 'correction');
  assert.equal(fell.fellBack, true);
  assert.match(L.fallbackNote(fell), /^Showing Current transforms: Viking's section correction is off/);
  assert.equal(L.fallbackNote(L.buildLayer(base)), '');
});

test('a pair that stands in for the one asked for gets thresholds that show something, without changing the user\'s', () => {
  // Four annotations moved by the transforms, 0.01 to 0.10 um: all under the default 0.25.
  const display = { shiftPair: 'correction' };
  const layer = L.buildLayer({ ...base, display, summary: { section_correction: null, live_registration: applied(4) } });
  assert.equal(layer.pair, 'transforms');
  assert.ok(layer.sel.n >= 1, `${layer.sel.n} shown`);
  assert.ok(S.thresholdFor(layer.display) < 0.25);
  assert.equal(display.shiftMin, undefined, 'the caller\'s display is untouched');
});

test('the fields are cached per nodes array: another threshold or color builds no new field', () => {
  const a = L.buildLayer(base);
  const b = L.buildLayer({ ...base, display: { shiftColor: 'direction', shiftMin: 0.5 } });
  assert.equal(a.field, b.field);
  const other = L.buildLayer({ ...base, nodes: nodes.slice() });
  assert.notEqual(other.field, a.field, 'a new nodes array is a new field (the cell was loaded again)');
});

test('a slice layer holds every candidate mark; its window is a draw range over them', () => {
  const layer = L.buildLayer({ ...base, display: { shiftSlice: true, shiftWindow: 1, shiftRings: true } });
  assert.equal(layer.slice, true);
  assert.ok(layer.plane.quad.length === 18 && layer.plane.outline.length === 24);
  const w = layer.windowAt(12);
  assert.deepEqual(w.marks, { first: 1, count: 3 }, 'sections 11 to 13');
  assert.deepEqual(w.ranges.arrows, { first: 6, count: 18 });
  const ringRange = S.vertexRange(layer.rings.offsets, w.marks);
  assert.deepEqual(w.ranges.rings, ringRange);
  assert.equal(w.shown, 3);
  assert.equal(layer.shownFor(12), 3);
  assert.match(layer.facts(12), /^3 of 10 moved shown/);
  assert.deepEqual(layer.windowAt(100).marks.count, 0, 'a section with no marks is an empty window');
  close(layer.zOf(12), 12 * 0.07, 1e-9, 'z of a section');
});

// -- thresholds for a pair --------------------------------------------------------------------

test('fitThresholds: unchanged when enough is shown, lowered through the ladder when not', () => {
  const field = S.prepareField(nodes, 'transforms');            // 4 moves of 0.01 .. 0.10
  assert.deepEqual(L.fitThresholds(S.prepareField(nodes, 'correction'), {}), {}, 'plenty at 0.25');
  const lowered = L.fitThresholds(field, {});
  assert.ok(lowered.shiftMin < 0.25 && lowered.shiftMinSlice < 0.05, JSON.stringify(lowered));
  const n = (t) => [...field.mag].filter((m) => m >= t).length;
  assert.ok(n(lowered.shiftMin) >= Math.min(field.n, 10) || lowered.shiftMin === 0);
  // Nothing moved: nothing to fit.
  assert.deepEqual(L.fitThresholds(S.prepareField([node(1, 1, 0, 0)], 'correction'), {}), {});
  // A threshold the user already set low is not touched.
  assert.deepEqual(L.fitThresholds(field, { shiftMin: 0, shiftMinSlice: 0 }), {});
});

test('the memory gives the user\'s thresholds back when the pair returns, and keeps ones they moved', () => {
  const memory = new L.ThresholdMemory();
  const correction = S.prepareField(nodes, 'correction');
  const transforms = S.prepareField(nodes, 'transforms');
  let display = { shiftMin: 0.25, shiftMinSlice: 0.05 };
  const go = (field) => { display = { ...display, ...memory.changes('c', field, display) }; return display; };
  const low = go(transforms);
  assert.ok(low.shiftMin < 0.25);
  const back = go(correction);
  assert.equal(back.shiftMin, 0.25, 'the section correction has its 0.25 again');
  assert.equal(back.shiftMinSlice, 0.05);
  // The user moves the threshold while on the transforms: it is theirs from then on.
  go(transforms);
  display = { ...display, shiftMin: 0.07 };
  const kept = go(correction);
  assert.equal(kept.shiftMin, 0.07);
  // Only the slice threshold moved: the all-sections one still goes back to the user's 0.25.
  display = { shiftMin: 0.25, shiftMinSlice: 0.05 };
  const lowered = go(transforms);
  assert.ok(lowered.shiftMin < 0.25 && lowered.shiftMinSlice < 0.05);
  display = { ...display, shiftMinSlice: 0.015 };
  const mixed = go(correction);
  assert.equal(mixed.shiftMin, 0.25, 'the untouched threshold goes back');
  assert.equal(mixed.shiftMinSlice, 0.015, 'the one they moved stays theirs');
  // Per key.
  assert.deepEqual(new L.ThresholdMemory().changes('other', correction, { shiftMin: 0.25, shiftMinSlice: 0.05 }), {});
  assert.deepEqual(L.thresholdsForPair(new L.ThresholdMemory(), 'k', nodes, 'correction', {}), {});
});

// -- the slice across cells and volumes ------------------------------------------------------------

/** Two volumes in slice mode, `a` over sections 10..19 and `b` over 10..13 (fewer nodes). */
function twoVolumes() {
  const slice = { shiftSlice: true, shiftWindow: 1 };
  const la = L.buildLayer({ ...base, display: slice });
  const lb = L.buildLayer({ ...base, nodes: nodes.slice(0, 4), display: slice, summary });
  return [{ key: 'A/1', id: 1, source: 'A', layer: la }, { key: 'B/2', id: 2, source: 'B', layer: lb }];
}

test('a key steps every volume in slice mode by the same amount, each clamped to its own sections', () => {
  const entries = twoVolumes();
  const slices = new S.SliceState();
  const key = (k, extra = {}) => ({ key: k, ...extra });
  assert.equal(L.applySliceKey(key('x'), entries, slices), null, 'not a slice key');
  const first = L.applySliceKey(key('['), entries, slices);
  // A starts on its strongest shift (section 19), B on its own (13): both then move down one.
  assert.deepEqual([...first], [['A', 18], ['B', 12]]);
  const big = L.applySliceKey(key('{'), entries, slices);
  assert.deepEqual([...big], [['A', 10], ['B', 10]], 'ten down, clamped to 10');
  const end = L.applySliceKey(key('End'), entries, slices);
  assert.deepEqual([...end], [['A', 19], ['B', 13]], 'End is each volume\'s last section with a mark');
  const home = L.applySliceKey(key('Home'), entries, slices);
  assert.deepEqual([...home], [['A', 10], ['B', 10]]);
  // Up and Down are the slice's only with nothing selected; Flip Z swaps their meaning.
  assert.equal(L.applySliceKey(key('ArrowUp'), entries, slices, { selected: true }), null);
  assert.deepEqual([...L.applySliceKey(key('ArrowUp'), entries, slices)], [['A', 11], ['B', 11]]);
  assert.deepEqual([...L.applySliceKey(key('ArrowUp'), entries, slices, { flipZ: true })], [['A', 10], ['B', 10]]);
});

test('no volume in slice mode takes no key; a cell with an empty layer does not count', () => {
  const plain = [{ key: 'A/1', id: 1, source: 'A', layer: L.buildLayer(base) }];
  assert.equal(L.applySliceKey({ key: ']' }, plain, new S.SliceState()), null);
  const empty = L.buildLayer({ ...base, display: { shiftSlice: true }, summary: {} });
  assert.equal(L.sliceRanges([{ key: 'A/1', id: 1, source: 'A', layer: empty }]).size, 0);
});

test('setSliceCenters puts the named volumes on a section, clamped; others stay', () => {
  const entries = twoVolumes();
  const slices = new S.SliceState();
  L.sliceCenters(entries, slices);
  const moved = L.setSliceCenters(entries, slices, ['B'], 99);
  assert.deepEqual([...moved], [['B', 13]]);
  assert.equal(slices.get('A'), 19);
});

test('the chip: one cell in full, several with each one\'s exaggeration', () => {
  const entries = twoVolumes();
  const slices = new S.SliceState();
  const centers = L.sliceCenters(entries, slices);
  const one = L.chipSummary([entries[0]], centers);
  assert.match(one, /^Shifts · (×[\d.]+ · )?section 19 \(1\.33 µm\) ±1 · \d+ shown$/);
  // Two volumes: an ID is numbered per volume, so each cell is named with its own.
  const many = L.chipSummary(entries, centers);
  assert.match(many, /^Shifts · c1 A (×[\d.]+ )?· c2 B (×[\d.]+ )?· section 19 ±1 · section 13 ±1 · /);
  assert.match(L.chipSummary(entries, centers, (source) => source.toLowerCase()), /^Shifts · c1 a .*c2 b /);
  // One volume: the bare IDs.
  const same = entries.map((e) => ({ ...e, source: 'A' }));
  assert.match(L.chipSummary(same, new Map([['A', 19]])), /^Shifts · c1 (×[\d.]+ )?· c2 /);
  assert.equal(L.chipSummary([], centers), '');
  const all = L.buildLayer(base);
  assert.match(L.chipSummary([{ key: 'A/1', id: 1, source: 'A', layer: all }], new Map()), /all sections · 10 shown$/);
});

test('the pick readout: the correction first, the transforms when that is all there is, nothing at rest', () => {
  const moved = node(1, 5, 0, 0, { shift: [0.12, -0.08], live: [0.03, 0] });
  const text = L.pickShiftText(moved);
  assert.equal(text.text, 'shift 0.14 µm (+0.12, -0.08)');
  assert.match(text.title, /section correction moved this annotation/);
  assert.match(L.pickShiftText(moved, 'transforms').text, /^shift 0\.03 µm \(\+0\.03, \+0\.00\)$/);
  assert.equal(L.pickShiftText(moved, 'both').text, 'shift 0.17 µm (+0.15, -0.08)');
  assert.match(L.pickShiftText(node(2, 5, 0, 0, { live: [0.2, 0] })).text, /^shift 0\.20 µm/);
  assert.equal(L.pickShiftText(node(3, 5, 0, 0)), null);
  assert.equal(L.pickShiftText(node(3, 5, 0, 0, { shift: [0.0001, 0] })), null, 'under half a nanometer is no move');
});

// -- drawing on a renderer -------------------------------------------------------------------------

class StubRenderer {
  constructor() {
    this.calls = [];
    this.shifts = new Map();
    this.inverted = false;
    this.flipZ = false;
    this.selection = null;
  }

  setShifts(key, layer, style) {
    this.calls.push(['setShifts', key, style]);
    this.shifts.set(key, { userData: { layer: layer.meta }, visible: true });
  }

  removeShifts(key) { this.calls.push(['removeShifts', key]); this.shifts.delete(key); }
  setShiftWindow(key, range) { this.calls.push(['setShiftWindow', key, range]); }
  setShiftPlane(key, z, rect = null, color = null) {
    this.calls.push(['setShiftPlane', key, z]);
    this.plane = { key, z, rect, color };
  }
  setStyle(key, { visible }) { const g = this.shifts.get(key); if (g && visible !== undefined) g.visible = visible; }
}

/** The same, drawing the chosen annotations' arrows (Renderer3D.setShiftFocus and setShiftDim). */
class FocusRenderer extends StubRenderer {
  constructor() { super(); this.focus = new Map(); this.dim = new Map(); }
  setShiftFocus(key, marks) { this.focus.set(key, marks); }
  setShiftDim(key, factor) { this.dim.set(key, factor); }
}

test('drawLayer hands the renderer the layer and its style, then the slice; syncSlice alone moves only the window', () => {
  const renderer = new StubRenderer();
  const layer = L.buildLayer({ ...base, display: { shiftSlice: true, shiftWindow: 2, shiftOpacity: 0.5, shiftThrough: false } });
  assert.equal(L.drawLayer(renderer, 'v/1', layer, 15), true);
  assert.deepEqual(renderer.calls[0], ['setShifts', 'v/1', { opacity: 0.5, through: false, width: 2.5 }]);
  const [, , range] = renderer.calls.find((c) => c[0] === 'setShiftWindow');
  assert.deepEqual(range.arrows, layer.windowAt(15).ranges.arrows);
  const plane = renderer.calls.find((c) => c[0] === 'setShiftPlane');
  close(plane[2], 15 * 0.07, 1e-9);
  renderer.calls.length = 0;
  L.syncSlice(renderer, 'v/1', layer, 12);
  assert.deepEqual(renderer.calls.map((c) => c[0]), ['setShiftWindow', 'setShiftPlane'], 'nothing is uploaded');
  // Outside slice mode the window is the whole layer and there is no plane.
  renderer.calls.length = 0;
  L.syncSlice(renderer, 'v/1', L.buildLayer(base), 12);
  assert.deepEqual(renderer.calls, [['setShiftWindow', 'v/1', null], ['setShiftPlane', 'v/1', null]]);
});

// -- a volume without current transforms, and the chip's words ----------------------------------------

test('a volume whose transforms are not read here draws the correction, whatever the saved pair', () => {
  for (const shiftPair of ['correction', 'transforms', 'both']) {
    const layer = L.buildLayer({ ...base, display: { shiftPair }, hasTransforms: false });
    assert.equal(layer.pair, 'correction', shiftPair);
    assert.deepEqual([...layer.pairs], ['correction']);
    // A pair the volume cannot have is not a fallback worth a warning: there is nothing else.
    assert.equal(layer.fellBack, false, shiftPair);
    assert.equal(L.fallbackNote(layer), '');
  }
  assert.deepEqual([...L.buildLayer(base).pairs], ['correction', 'transforms', 'both']);
});

test('the chip: loading, nothing moved, a count, the strongest, and the pair drawn when it fell back', () => {
  assert.deepEqual(L.shiftChip(null), { text: 'loading', warn: false, help: 'Loading the cell\'s annotations.' });
  const empty = L.shiftChip(L.buildLayer({ nodes, summary: {}, display: {} }));
  assert.equal(empty.text, 'nothing moved');
  assert.equal(empty.warn, true);
  assert.ok(empty.help.startsWith(S.EMPTY_TEXT));
  assert.match(empty.help, /off for this cell/);
  const layer = L.buildLayer(base);
  const chip = L.shiftChip(layer, null, 'links across sections: median step 0.2 to 0.1 µm');
  assert.equal(chip.text, '10 shown');
  assert.equal(chip.warn, false);
  assert.ok(chip.help.includes(layer.facts(null)), 'the facts the panel used to print');
  assert.match(chip.help, /true move/);
  assert.ok(chip.help.endsWith('Links across sections: median step 0.2 to 0.1 µm.'), 'the verdict, a sentence');
  // The slice's window is what is shown.
  const slice = L.buildLayer({ ...base, display: { shiftSlice: true, shiftWindow: 0 } });
  assert.equal(L.shiftChip(slice, 15).text, '1 shown');
  // Fell back: the pair drawn is on the chip, and why is the first thing its hover says.
  const fell = L.buildLayer({ ...base, summary: { section_correction: { requested: false }, live_registration: summary.live_registration } });
  const warned = L.shiftChip(fell);
  assert.equal(warned.warn, true);
  assert.match(warned.text, / · Transforms$/);
  assert.match(warned.help, /^Showing Current transforms: /);
  // Capped: the strongest.
  const many = Array.from({ length: 2100 }, (_, i) => node(i + 1, 10 + (i % 50), i, 0, { shift: [0.3 + i * 1e-4, 0] }));
  const capped = L.buildLayer({ nodes: many, summary: { section_correction: applied(2100) }, display: {} });
  assert.equal(L.shiftChip(capped).text, 'strongest 2,000');
});

// -- the controller -----------------------------------------------------------------------------------

class Dom {
  constructor() { this.listeners = {}; this.hidden = false; this.textContent = ''; this.value = ''; this.min = ''; this.max = ''; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] || []) fn({}); }
}

function makeController({ displayFor = {}, sources = ['A'], Renderer = StubRenderer, nodeRows = nodes } = {}) {
  const renderer = new Renderer();
  const records = new Map();
  for (const [i, source] of sources.entries()) {
    records.set(`${source}/${i + 1}`, {
      key: `${source}/${i + 1}`, id: i + 1, source, color: '#2f6fed', visible: true, summary,
      display: { showShift: true, shiftSlice: true, shiftWindow: 1, ...displayFor },
    });
  }
  const state = { neurons: records, selected: [...records.keys()][0] };
  const cache = new Map();
  const outlines = new Map();
  const fetched = [];
  const hooks = {
    applyDisplay: async (record, changes) => { record.display = { ...record.display, ...changes }; hooks.applied.push([record.key, changes]); },
    debounced: (key, fn) => fn(),
    setRegistration: async () => {},
    renderNeuronList: () => { hooks.renders += 1; },
    look: () => {},
    applied: [],
    renders: 0,
  };
  const dom = { chip: new Dom(), text: new Dom(), steps: new Dom(), prev: new Dom(), next: new Dom(), range: new Dom(), off: new Dom() };
  const api = { shiftSummary: async (source, id, pair) => { fetched.push([source, id, pair]); return { links: null }; } };
  const controller = new ShiftController({
    renderer, state, api,
    nodesFor: async (record) => { if (!cache.has(record.key)) cache.set(record.key, nodeRows.slice()); return cache.get(record.key); },
    outlinesFor: async (record) => {
      fetched.push(['outlines', record.key]);
      if (!outlines.has(record.key)) outlines.set(record.key, outlineRows);
      return outlines.get(record.key);
    },
    cachedNodes: (record) => cache.get(record.key),
    cachedOutlines: (record) => outlines.get(record.key),
    fail: (error) => { throw error; },
    hooks, dom,
  });
  return { controller, renderer, records, state, hooks, dom, cache, fetched, outlines };
}

/** The closed-curve outlines `outlinesFor` answers with (none, unless a test sets them). */
let outlineRows = [];

test('refresh draws the layer, starts the slice on the strongest shift and writes the chip', async () => {
  const { controller, renderer, records, dom } = makeController();
  const record = records.get('A/1');
  const layer = await controller.refresh(record);
  assert.equal(layer.pair, 'correction');
  assert.ok(renderer.shifts.has('A/1'));
  assert.equal(controller.layerOf(record), layer);
  assert.equal(controller.centerOf(record), 19);
  assert.equal(dom.chip.hidden, false);
  assert.match(dom.text.textContent, /section 19/);
  assert.equal(dom.steps.hidden, false);
  assert.equal(dom.range.min, '10');
  assert.equal(dom.range.max, '19');
  assert.equal(dom.range.value, '19');
  const window = renderer.calls.filter((c) => c[0] === 'setShiftWindow').at(-1);
  assert.deepEqual(window[2].arrows, layer.windowAt(19).ranges.arrows);
});

test('turning the layer off removes it, hides the chip and forgets the panel', async () => {
  const { controller, renderer, records, dom } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record);
  record.display = { ...record.display, showShift: false };
  assert.equal(await controller.refresh(record), null);
  assert.equal(renderer.shifts.size, 0);
  assert.equal(dom.chip.hidden, true);
  assert.equal(controller.layerOf(record), null);
});

test('a refresh for a cell that was loaded again meanwhile draws nothing', async () => {
  const { controller, renderer, records } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record, { gone: () => true });
  assert.equal(renderer.shifts.size, 0);
  assert.equal(controller.layerOf(record), null);
});

test('layerOf drops a layer whose annotations were forgotten (the cell was loaded again)', async () => {
  const { controller, records, cache } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record);
  assert.ok(controller.layerOf(record));
  cache.delete(record.key);
  assert.equal(controller.layerOf(record), null);
});

test('keys: [ and ] step the slice; Up and Down only while nothing is selected', async () => {
  const { controller, renderer, records } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record);
  assert.equal(controller.onKey({ key: '[' }), true);
  assert.equal(controller.centerOf(record), 18);
  assert.equal(controller.onKey({ key: 'ArrowDown' }), true);
  assert.equal(controller.centerOf(record), 17);
  renderer.selection = { kind: 'annotation' };
  assert.equal(controller.onKey({ key: 'ArrowUp' }), false, 'the walk along the links is the selection\'s');
  assert.equal(controller.centerOf(record), 17);
  assert.equal(controller.onKey({ key: ']' }), true, 'but [ and ] are the slice\'s whatever is selected');
  assert.equal(controller.centerOf(record), 18);
  renderer.selection = null;
  assert.equal(controller.onKey({ key: 'ArrowLeft' }), false, 'left and right orbit');
  assert.equal(controller.onKey({ key: 'PageUp', ctrlKey: true }), false);
  // A stepped slice is a draw range, not a rebuild.
  assert.equal(renderer.calls.filter((c) => c[0] === 'setShifts').length, 1);
});

test('a hidden cell takes no slice key and no chip; an unloaded one is forgotten', async () => {
  const { controller, renderer, records, state, dom } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record);
  renderer.shifts.get('A/1').visible = false;
  assert.equal(controller.onKey({ key: '[' }), false, 'nothing visible draws a slice: the key is the camera\'s');
  controller.updateChip();
  assert.equal(dom.chip.hidden, true);
  renderer.shifts.get('A/1').visible = true;
  controller.updateChip();
  assert.equal(dom.chip.hidden, false);
  assert.ok(controller.layers.has('A/1'));
  state.neurons.delete('A/1');          // unloaded: the layer holds the cell's whole /nodes array
  controller.updateChip();
  assert.equal(controller.layers.has('A/1'), false);
  assert.equal(controller._tokens.has('A/1'), false);
});

test('a selected annotation draws the slice to its section, in its own volume only', async () => {
  const { controller, records } = makeController({ sources: ['A', 'B'] });
  await controller.refresh(records.get('A/1'));
  await controller.refresh(records.get('B/2'));
  controller.follow({ kind: 'annotation', key: 'A/1', section: 12 });
  assert.equal(controller.centerOf(records.get('A/1')), 12);
  assert.equal(controller.centerOf(records.get('B/2')), 19);
  controller.follow({ kind: 'skeleton', key: 'A/1', section: 15 });
  controller.follow(null);
  assert.equal(controller.centerOf(records.get('A/1')), 12, 'only annotations are followed');
});

test('the chip\'s stepper and range move the slice; Off turns the layer off and rebuilds the list', async () => {
  const { controller, renderer, records, dom, hooks } = makeController();
  const record = records.get('A/1');
  // The keys are the canvas's: each click on the chip hands the focus back to it.
  let focused = 0;
  renderer.webgl = { domElement: { focus: () => { focused += 1; } } };
  await controller.refresh(record);
  dom.prev.fire('click');
  assert.equal(controller.centerOf(record), 18);
  assert.equal(focused, 1);
  dom.next.fire('click');
  assert.equal(focused, 2);
  dom.prev.fire('click');
  dom.range.value = '14';
  dom.range.fire('input');
  assert.equal(controller.centerOf(record), 14);
  assert.equal(dom.range.value, '14');
  dom.range.fire('change');
  assert.equal(focused, 4);
  dom.off.fire('click');
  assert.equal(focused, 5);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(hooks.applied.at(-1), ['A/1', { showShift: false }]);
  assert.ok(hooks.renders >= 1);
});

test('live redraws from the cached annotations with the changes over the display, saving nothing', async () => {
  const { controller, records, hooks, fetched } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record);
  const before = controller.layerOf(record);
  controller.live(record, { shiftScale: 50 });
  const live = controller.layerOf(record);
  assert.notEqual(live, before);
  assert.equal(live.k, 50);
  assert.equal(record.display.shiftScale, undefined, 'not saved');
  assert.deepEqual(hooks.applied, []);
  assert.equal(fetched.length, 0);
  assert.equal(live.field, before.field, 'the same field: only the marks and buffers were rebuilt');
});

test('the panel is told after a redraw and after a slice step, with the layer and the center', async () => {
  const { controller, records } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record);
  const seen = [];
  const ctx = controller.panelContext(record, {});
  ctx.register((layer, center) => seen.push([layer.pair, center]));
  controller.onKey({ key: '[' });
  assert.deepEqual(seen.at(-1), ['correction', 18]);
  ctx.setCenter(11);
  assert.deepEqual(seen.at(-1), ['correction', 11]);
  controller.live(record, { shiftOpacity: 0.4 });
  assert.deepEqual(seen.at(-1), ['correction', 11]);
});

test('the server\'s summary is fetched once per cell copy and pair, and not again after it failed', async () => {
  const { controller, records, fetched } = makeController();
  const record = records.get('A/1');
  await controller.refresh(record);
  const ctx = controller.panelContext(record, {});
  await ctx.summary();
  await ctx.summary();
  assert.deepEqual(fetched, [['A', 1, 'correction']]);
  assert.equal(ctx.thresholdsFor('correction') && Object.keys(ctx.thresholdsFor('correction')).length, 0);
  const lowered = ctx.thresholdsFor('transforms');
  assert.ok(lowered.shiftMin < 0.25);
});

test('onlyShiftKeys: a change that is the layer\'s alone is redrawn without fetching the rest', () => {
  assert.equal(onlyShiftKeys({ shiftScale: 3, showShift: true }), true);
  assert.equal(onlyShiftKeys({ shiftScale: 3, showDiscs: true }), false);
  assert.equal(onlyShiftKeys({}), false);
});

// -- run -----------------------------------------------------------------------------------------------
// -- the chosen annotations' own arrows ---------------------------------------------------------------

test('focusMarks: a chosen annotation\'s arrow and ring at the layer\'s own scale, whatever the threshold or window', () => {
  // Shown only from 0.9 um and in a one-section window far away: the chosen one is still drawn.
  const layer = L.buildLayer({ ...base, display: { shiftMinSlice: 0.9, shiftSlice: true, shiftWindow: 0 } });
  const id = 102;                       // section 12, shift (0.5, 0): under the threshold
  assert.ok(![...layer.sel.rows].some((r) => layer.field.id[r] === id), 'the layer itself leaves it out');
  const [mark] = L.focusMarks(layer, [id]);
  assert.equal(mark.locationId, id);
  close(mark.trueUm, 0.5, 1e-12, 'true length');
  assert.deepEqual(mark.shift, [0.5, 0]);
  assert.equal(mark.arrow.length, S.ARROW_VERTS * 3);
  // The head is the annotation's real place; the tail is pulled back by scale x shift.
  const head = [mark.arrow[3], mark.arrow[4]];
  close(head[0], 10, 1e-6, 'head x');
  close(mark.arrow[0], 10 - mark.scale * 0.5, 1e-5, 'tail x');
  const expected = S.drawnLength(0.5, layer.k, layer.field.bbox.diagonal, layer.colorMax) / 0.5;
  close(mark.scale, expected, 1e-12, 'the scale markScales gives');
  // A disc's ring where it was: RING_SEGMENTS sides, two vertices each.
  assert.equal(mark.ring.length, S.RING_SEGMENTS * 2 * 3);
  assert.equal(mark.curve, false);
  // One the pair did not move, and one not in the cell, have no arrow.
  const still = L.buildLayer({ ...base, nodes: [...nodes, node(999, 30, 0, 0)] });
  assert.deepEqual(L.focusMarks(still, [999, 123456]), []);
  assert.deepEqual(L.focusMarks(null, [id]), []);
  assert.equal(L.rowOfId(layer.field, id), [...layer.field.id].indexOf(id));
  assert.equal(L.rowOfId(layer.field, 5), -1);
});

test('focusMarks: a closed curve\'s ring is its outline moved back, or none without it', () => {
  const curve = node(500, 12, 3, 4, { shift: [0.4, 0.3], type: S.CLOSED_CURVE });
  const layer = L.buildLayer({ ...base, nodes: [...nodes, curve] });
  const [bare] = L.focusMarks(layer, [500]);
  assert.equal(bare.curve, true);
  assert.equal(bare.ring, null, 'no outline fetched: no ring');
  const outline = { location_id: 500, outline_um: [[2, 3], [4, 3], [4, 5], [2, 5]] };
  const [ringed] = L.focusMarks(layer, [500], [outline]);
  assert.equal(ringed.ring.length, 4 * 2 * 3);
  // The outline moves by whatever takes the annotation from its real place to the tail.
  close(ringed.ring[0], 2 - ringed.scale * 0.4, 1e-5, 'outline x');
  close(ringed.ring[1], 3 - ringed.scale * 0.3, 1e-5, 'outline y');
});

test('the controller draws each chosen annotation\'s arrow on its cell and dims the rest; none, nothing dimmed', async () => {
  const { controller, renderer, records } = makeController({
    Renderer: FocusRenderer, displayFor: { shiftFocusDim: 0.3, shiftSlice: false },
  });
  const record = records.get('A/1');
  await controller.refresh(record);
  assert.deepEqual(renderer.focus.get('A/1'), [], 'nothing chosen');
  assert.equal(renderer.dim.get('A/1'), 1);
  controller.focus([
    { slot: 'primary', key: 'A/1', locationId: 103 },
    { slot: 'above', key: 'A/1', locationId: 104 },
    { slot: 'below', key: 'A/1', locationId: 99999 },     // not moved: no arrow
    { slot: 'below', key: 'B/9', locationId: 103 },       // no such cell
  ]);
  const marks = renderer.focus.get('A/1');
  assert.deepEqual(marks.map((m) => [m.slot, m.locationId]), [['primary', 103], ['above', 104]]);
  assert.equal(marks[0].width, 2.5, 'drawn from the layer\'s own width');
  assert.equal(renderer.dim.get('A/1'), 0.3);
  // The layer drawn again (a slider) keeps them: setShifts made a new group.
  controller.live(record, { shiftScale: 20 });
  assert.equal(renderer.focus.get('A/1').length, 2);
  assert.ok(renderer.focus.get('A/1')[0].scale > 1);
  // Only an annotation the pair did not move: nothing to look at, so nothing is dimmed.
  controller.focus([{ slot: 'primary', key: 'A/1', locationId: 99999 }]);
  assert.deepEqual(renderer.focus.get('A/1'), []);
  assert.equal(renderer.dim.get('A/1'), 1);
  controller.focus([]);
  assert.equal(renderer.dim.get('A/1'), 1);
  // A hidden cell's arrow is not drawn.
  controller.focus([{ slot: 'primary', key: 'A/1', locationId: 103 }]);
  renderer.shifts.get('A/1').visible = false;
  controller.updateChip();
  assert.deepEqual(renderer.focus.get('A/1'), []);
});

test('a chosen closed curve fetches its outline once, then draws its ring', async () => {
  const curve = node(500, 12, 3, 4, { shift: [0.4, 0.3], type: S.CLOSED_CURVE });
  outlineRows = [{ location_id: 500, outline_um: [[2, 3], [4, 3], [4, 5], [2, 5]] }];
  try {
    const { controller, renderer, records, fetched } = makeController({ Renderer: FocusRenderer, nodeRows: [...nodes, curve] });
    await controller.refresh(records.get('A/1'));
    controller.focus([{ slot: 'primary', key: 'A/1', locationId: 500 }]);
    assert.equal(renderer.focus.get('A/1')[0].ring, null, 'drawn at once without its ring');
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(renderer.focus.get('A/1')[0].ring.length, 4 * 2 * 3, 'then with it');
    controller.focus([{ slot: 'primary', key: 'A/1', locationId: 500 }]);
    assert.equal(fetched.filter((f) => f[0] === 'outlines').length, 1, 'fetched once');
  } finally {
    outlineRows = [];
  }
});


// -- One slice at a time: the plane's size and the colors (2026-10-20) ---------------------------------

/** A renderer that colors slices, as Renderer3D does. */
class SliceRenderer extends FocusRenderer {
  constructor() { super(); this.tints = new Map(); }
  sliceColors() { return { above: [1, 0, 0], on: [1, 1, 0], below: [0, 0, 1] }; }
  setShiftSliceTint(key, tint) { this.tints.set(key, tint); }
}

test('the slice plane frames its section\'s annotations and arrows, not the whole cell', () => {
  // Sections 10..19, one annotation each at x = 5 i, radius 1, all moved along +x.
  const layer = L.buildLayer({ ...base, display: { shiftSlice: true, shiftWindow: 2, shiftMinSlice: 0 } });
  assert.ok(layer.extents instanceof Map && layer.extents.size === 10);
  const rect = layer.planeAt(14);                      // the node at x = 20, y = 0
  const tail = 20 - layer.scales[[...layer.sel.section].indexOf(14)] * 0.7;
  const pad = Math.max(L.PLANE_MARGIN * (21 - Math.min(tail, 19)), L.PLANE_PAD_UM);
  close(rect.x1, 21 + pad, 1e-9, 'right: the ring\'s edge plus the margin');
  close(rect.x0, Math.min(tail, 19) - pad, 1e-9, 'left: the arrow\'s tail, or the ring');
  assert.ok(rect.y1 - rect.y0 >= L.PLANE_MIN_UM - 1e-9, 'at least the minimum size');
  const cell = layer.field.bbox;
  assert.ok(rect.x1 - rect.x0 < (cell.max[0] - cell.min[0]) / 2, 'far smaller than the cell');
  // A section with nothing on it frames the window's; outside every window, none.
  const sparse = L.buildLayer({ ...base, nodes: nodes.filter((n) => n.z_section !== 14),
    display: { shiftSlice: true, shiftWindow: 1, shiftMinSlice: 0 } });
  const around = sparse.planeAt(14);
  assert.ok(around && around.x0 < 15 && around.x1 > 25, 'sections 13 and 15 frame it');
  assert.equal(sparse.planeAt(500), null);
  assert.equal(L.buildLayer(base).extents, null, 'not built outside slice mode');
});

test('a slice\'s colors: yellow on the plane at full strength, red above and blue below almost nothing', () => {
  const layer = L.buildLayer({ ...base, display: { shiftSlice: true, shiftWindow: 2, shiftMinSlice: 0 } });
  const colors = { above: [1, 0, 0], on: [1, 1, 0], below: [0, 0, 1] };
  const t = L.sliceTint(layer, 14, { colors });
  const { marks } = layer.windowAt(14);
  assert.deepEqual(t.parts.map((p) => [p.side, p.first, p.count]), [
    ['below', marks.first, 2], ['on', marks.first + 2, 1], ['above', marks.first + 3, 2]]);
  assert.deepEqual(t.parts.map((p) => p.alpha), [0.06, 1, 0.06]);
  assert.deepEqual(t.parts[0].rgb, [0, 0, 1]);
  assert.deepEqual(t.parts[2].rgb, [1, 0, 0]);
  // Flip Z: higher sections are below.
  const flipped = L.sliceTint(layer, 14, { colors, flipZ: true });
  assert.deepEqual(flipped.parts.map((p) => p.side), ['above', 'on', 'below']);
  assert.deepEqual(flipped.parts[0].rgb, [1, 0, 0]);
  // A selection on the cell: the plane's section turns pale; the selected arrow keeps the full yellow.
  const hybrid = L.sliceTint(layer, 14, { colors, hybrid: true });
  assert.deepEqual(hybrid.parts[1].rgb, [1, 1, L.HYBRID_WHITE]);
  // Off the plane at the setting; outside slice mode, nothing.
  const faint = L.buildLayer({ ...base, display: { shiftSlice: true, shiftWindow: 2, shiftMinSlice: 0, shiftSliceDim: 0 } });
  assert.deepEqual(L.sliceTint(faint, 14, { colors }).parts.map((p) => p.alpha), [0, 1, 0]);
  assert.equal(L.sliceTint(L.buildLayer(base), 14, { colors }), null);
  // Rings: the same parts as vertex ranges of the rings' buffer.
  const ringed = L.buildLayer({ ...base, display: { shiftSlice: true, shiftWindow: 2, shiftMinSlice: 0, shiftRings: true } });
  const r = L.sliceTint(ringed, 14, { colors });
  assert.equal(r.rings[1].count, S.RING_SEGMENTS * 2);
});

test('syncSlice hands the renderer the plane\'s rect and color and the tint; the selection pales the plane', async () => {
  const { controller, renderer, records } = makeController({ Renderer: SliceRenderer, displayFor: { shiftWindow: 2 } });
  const record = records.get('A/1');
  await controller.refresh(record);
  const center = controller.centerOf(record);
  assert.ok(renderer.plane.rect && renderer.plane.color, 'sized and colored');
  assert.deepEqual(renderer.plane.color, [1, 1, 0]);
  const tint = renderer.tints.get('A/1');
  assert.ok(tint.parts.some((p) => p.side === 'on' && p.count === 1));
  assert.deepEqual(tint.parts.find((p) => p.side === 'on').rgb, [1, 1, 0]);
  // Selected on this cell: pale; the selection fade is not applied to a slice.
  renderer.selection = { kind: 'annotation', key: 'A/1', locationId: 100 + center - 10 };
  controller.focus([{ slot: 'primary', key: 'A/1', locationId: 100 + center - 10 }]);
  assert.deepEqual(renderer.tints.get('A/1').parts.find((p) => p.side === 'on').rgb, [1, 1, L.HYBRID_WHITE]);
  assert.equal(renderer.dim.get('A/1'), 1);
  // Stepping recolors the new window.
  controller.onKey({ key: '[' });
  assert.equal(renderer.tints.get('A/1').parts.find((p) => p.side === 'on').first,
    [...controller.layerOf(record).sel.section].indexOf(center - 1));
  // Out of slice mode the tint is taken away.
  record.display = { ...record.display, shiftSlice: false };
  await controller.refresh(record);
  assert.equal(renderer.tints.get('A/1'), null);
});

// -- the Correction log's pulls in the cell panel ------------------------------------------------------

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const PULL = { location_id: 103, z: 13, r_um: 0.5, dx_um: -3, dy_um: 0, move_um: 3, neighbor_id: 100,
  neighbor_r_um: 10, ratio: 20, distance_um: 6, frac: 0.5, cos: 1, moved_with: [104] };

test('a cell panel asks for its curve-fit pulls once per copy, annotations and ratio, and draws again when they come', async () => {
  const { controller, records, hooks } = makeController();
  const record = records.get('A/1');
  const asked = [];
  let ratio = 4;
  controller.pullRatio = () => ratio;
  controller.api = {
    correctionPulls: async (source, id, r) => { asked.push([source, id, r]); return { checked: true, ratio: r, pulls: [PULL] }; },
  };
  const fitted = (extra = {}) => ({ ...summary, section_correction: { ...applied(10), mode: 'curvefit' },
    copy_tag: 'curvefit.1', data_stamp: '2026-10-20T00:00:00Z', ...extra });

  // Not corrected by a curve fit: nothing to ask.
  for (const sc of [{ ...applied(10), mode: 'neighbor' }, { requested: true, applied: false, mode: 'curvefit' }, null]) {
    record.summary = { ...summary, section_correction: sc };
    assert.equal(controller.pullsFor(record), null);
  }
  assert.deepEqual(asked, []);

  record.summary = fitted();
  assert.equal(controller.pullsFor(record), null, 'asked, not here yet');
  assert.equal(controller.pullsFor(record), null, 'and not asked twice');
  const renders = hooks.renders;
  await tick();
  assert.deepEqual(asked, [['A', 1, 4]]);
  assert.equal(hooks.renders, renders + 1, 'the cell list is drawn again with the answer');
  assert.deepEqual(controller.pullsFor(record).pulls.map((p) => p.location_id), [103]);
  assert.equal(asked.length, 1);

  // The Settings ratio, the copy and the annotations each make it another question.
  ratio = 6;
  controller.pullsFor(record);
  record.summary = fitted({ copy_tag: 'all.1', section_correction: { ...applied(10), mode: 'all' } });
  controller.pullsFor(record);
  record.summary = fitted({ copy_tag: 'all.1', section_correction: { ...applied(10), mode: 'all' }, data_stamp: '2026-10-21T00:00:00Z' });
  controller.pullsFor(record);
  await tick();
  assert.deepEqual(asked.slice(1), [['A', 1, 6], ['A', 1, 6], ['A', 1, 6]]);
  assert.equal(controller.pullsFor(record).ratio, 6);

  // A failed question shows nothing and is not repeated for the same stamp.
  controller.api.correctionPulls = async () => { asked.push('failed'); throw new Error('404'); };
  ratio = 8;
  assert.equal(controller.pullsFor(record), null);
  await tick();
  assert.equal(controller.pullsFor(record), null);
  assert.equal(asked.filter((a) => a === 'failed').length, 1);
});

test('an answer that comes after the cell changed copy is dropped', async () => {
  const { controller, records } = makeController();
  const record = records.get('A/1');
  let release;
  controller.api = {
    correctionPulls: (source, id, r) => (r === 4
      ? new Promise((resolve) => { release = () => resolve({ checked: true, ratio: 4, pulls: [PULL] }); })
      : Promise.resolve({ checked: true, ratio: r, pulls: [] })),
  };
  let ratio = 4;
  controller.pullRatio = () => ratio;
  record.summary = { ...summary, section_correction: { ...applied(10), mode: 'curvefit' }, copy_tag: 'curvefit.1' };
  controller.pullsFor(record);
  ratio = 5;
  controller.pullsFor(record);
  await tick();
  release();
  await tick();
  assert.deepEqual(controller.pullsFor(record).pulls, [], 'the answer for 5x, not the late one for 4x');
});

test('the panel acts through the controller: its pulls, and selecting one of them', () => {
  const { controller, records, hooks } = makeController();
  const record = records.get('A/1');
  const picked = [];
  hooks.selectLocation = (r, id) => { picked.push([r.key, id]); return true; };
  controller.pulls.set(record.key, { stamp: 'held', answer: { checked: true, ratio: 4, pulls: [PULL] } });
  const ctx = controller.panelContext(record, {});
  assert.equal(typeof ctx.pulls, 'function');
  ctx.selectLocation(103);
  assert.deepEqual(picked, [['A/1', 103]]);
});

test('the section summary carries the pull count, whether the layer is on or not', async () => {
  const answer = { checked: true, ratio: 4, pulls: [PULL, { ...PULL, location_id: 105 }] };
  assert.equal(shiftSummary({ showShift: false }, null, answer), 'off · 2 pulled');
  assert.equal(shiftSummary({ showShift: true }, null, answer), 'loading · 2 pulled');
  assert.equal(shiftSummary({ showShift: false }, null, { ...answer, pulls: [] }), 'off');
  assert.equal(shiftSummary({ showShift: false }, null, null), 'off');
  assert.equal(shiftSummary({ showShift: false }, null), 'off');
  const layer = L.buildLayer(base);
  assert.equal(shiftSummary({ showShift: true }, layer, answer), `${shiftSummary({ showShift: true }, layer)} · 2 pulled`);
});

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
