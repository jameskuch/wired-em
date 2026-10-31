// Visualizations (static/js/visualizations.js): a cell's whole look under a name --
// the built-in ones, what saving a cell keeps, what applying sets, telling which one a
// cell shows, the browser's store, and the file a look is shared in.  Run with
//
//     node tests/js/test_visualizations.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const V = await load('visualizations.js');
const { DEFAULT_DISPLAY, normalizeDisplay } = await load('state.js');
const { DEFAULT_RENDER, normalizeRender, RENDER_FIELDS } = await load('renderoptions.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** A stand-in for localStorage. */
class Store {
  constructor() { this.items = new Map(); }
  getItem(k) { return this.items.has(k) ? this.items.get(k) : null; }
  setItem(k, v) { this.items.set(k, String(v)); }
}

const cell = (over = {}) => ({
  key: 'NeitzInferiorMonkey/1178', id: 1178, source: 'NeitzInferiorMonkey', color: '#2f6fed',
  render: normalizeRender({}), display: normalizeDisplay({}), opacity: 0.92, ...over,
});

test('the built-in list: the lab\'s and the emphasis looks, ids unique, every one complete when normalized', () => {
  const ids = V.BUILT_IN.map((v) => v.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.ok(ids.includes('crystal-skeleton-rings') && ids.includes('becca-skeleton-check'));
  assert.ok(ids.filter((id) => id.startsWith('jk-')).length >= 3, 'James\'s');
  assert.ok(ids.includes('emphasis-skeleton') && ids.includes('emphasis-annotations') && ids.includes('emphasis-corrections'));
  for (const vis of V.BUILT_IN) {
    const n = V.normalizeVisualization(vis);
    assert.ok(n, vis.id);
    assert.deepEqual(Object.keys(n.render).sort(), Object.keys(DEFAULT_RENDER).sort(), `${vis.id}: every render option`);
    for (const key of Object.keys(DEFAULT_DISPLAY)) {
      if (V.CELL_ONLY_DISPLAY.includes(key)) assert.ok(!(key in n.display), `${vis.id}: no ${key}`);
      else assert.ok(key in n.display, `${vis.id}: ${key}`);
    }
    assert.ok(vis.note.length > 20, `${vis.id} says what it is`);
    // Every key a built-in names is a real one: a typo would be a setting that does nothing.
    const renderKeys = new Set(RENDER_FIELDS.map((f) => f.key));
    for (const key of Object.keys(vis.render)) assert.ok(renderKeys.has(key), `${vis.id}: render key ${key}`);
    for (const key of Object.keys(vis.display)) assert.ok(key in DEFAULT_DISPLAY, `${vis.id}: display key ${key}`);
  }
  // Since 2.9.0 nothing moves annotations by default: no built-in look turns Smooth discs or outlines on.
  for (const vis of V.BUILT_IN) {
    assert.ok(!vis.render.curve_avg_discs && !vis.render.curve_avg_paths, `${vis.id} moves no annotation`);
  }
});

test('a cell\'s look is saved whole, without its Z range, color, registration or moves', () => {
  const record = cell({
    display: normalizeDisplay({ showSkeleton: true, skeletonWidth: 0.2, zMin: 3, zMax: 9, material: 'Clay' }),
    render: normalizeRender({ method: 'closedcurve', pixel_size_um: 0.1 }), opacity: 0.4,
    registration: true, corrections: 'curvefit', manualMoves: { 'curvefit.1': { 1: { dx: 1, dy: 0, dz: 0 } } },
  });
  const vis = V.fromCell(record, '  My look ');
  assert.equal(vis.name, 'My look');
  assert.equal(vis.render.method, 'closedcurve');
  assert.equal(vis.render.pixel_size_um, 0.1);
  assert.equal(vis.display.skeletonWidth, 0.2);
  assert.equal(vis.display.material, 'Clay');
  assert.equal(vis.opacity, 0.4);
  assert.ok(!('zMin' in vis.display) && !('zMax' in vis.display));
  assert.deepEqual(Object.keys(vis).sort(), ['by', 'display', 'id', 'name', 'note', 'opacity', 'render']);
});

test('applying sets the whole look: what it leaves out is the default, the cell keeps its Z range', () => {
  const record = cell({ display: normalizeDisplay({ showWireframe: true, showSynapses: true, zMin: 2, zMax: 5 }) });
  const crystal = V.BUILT_IN.find((v) => v.id === 'crystal-skeleton-rings');
  const look = V.applied(crystal, record);
  assert.equal(look.display.showSurface, false);
  assert.equal(look.display.showSkeleton, true);
  assert.equal(look.display.discColor, '#edec4f');
  assert.equal(look.display.showWireframe, false, 'not what the cell had: the default');
  assert.equal(look.display.showSynapses, false);
  assert.equal(look.display.zMin, 2);
  assert.equal(look.display.zMax, 5);
  assert.equal(look.render.method, 'hybrid');
  assert.equal(look.opacity, 0.92);
  // Coloring by depth needs the volume's boundary markers.
  const glass = V.BUILT_IN.find((v) => v.id === 'jk-hybrid-glass-depth');
  assert.equal(V.applied(glass, record).render.color_by_strata, true);
  assert.equal(V.applied(glass, record, { hasBoundary: () => false }).render.color_by_strata, false);
});

test('which visualization a cell shows: exactly, by name; a changed knob is a custom look', () => {
  const list = [...V.BUILT_IN];
  const fresh = cell();
  assert.equal(V.matching(fresh, list)?.id, 'default', 'a new cell has the app\'s defaults');
  const crystal = V.BUILT_IN.find((v) => v.id === 'crystal-skeleton-rings');
  const look = V.applied(crystal, fresh);
  const shown = cell({ ...look, display: normalizeDisplay(look.display) });
  assert.equal(V.matching(shown, list)?.id, 'crystal-skeleton-rings');
  shown.display = normalizeDisplay({ ...shown.display, discOpacity: 0.5 });
  assert.equal(V.matching(shown, list), null);
  // Its own Z range does not make a look custom.
  const cut = cell({ display: normalizeDisplay({ zMin: 1, zMax: 2 }) });
  assert.equal(V.matching(cut, list)?.id, 'default');
});

test('saved in the browser: replaced by name, deleted, made safe, ids apart from the built-in ones', () => {
  const store = new Store();
  assert.deepEqual(V.loadSaved(store), []);
  let list = V.withSaved([], V.fromCell(cell({ opacity: 0.5 }), 'Mine'));
  list = V.withSaved(list, V.fromCell(cell({ opacity: 0.3 }), 'Other'));
  list = V.withSaved(list, V.fromCell(cell({ opacity: 0.2 }), 'mine'));
  assert.deepEqual(list.map((v) => [v.name, v.opacity]), [['Other', 0.3], ['mine', 0.2]]);
  assert.ok(list.every((v) => v.id.startsWith('mine-')));
  assert.ok(V.storeSaved(list, store));
  const back = V.loadSaved(store);
  assert.deepEqual(back.map((v) => v.name), ['Other', 'mine']);
  assert.deepEqual(V.withoutSaved(back, [back[0].id]).map((v) => v.name), ['mine']);
  // A saved one named like a built-in keeps its own id.
  const clash = V.normalizeSaved([{ name: 'App default', opacity: 1 }]);
  assert.ok(clash[0].id !== 'default' && clash[0].id.startsWith('mine-'));
  // Garbage: dropped, never thrown.
  store.setItem(V.STORE_KEY, '{nope');
  assert.deepEqual(V.loadSaved(store), []);
  assert.deepEqual(V.normalizeSaved([null, 3, { name: '' }, { name: 'ok', opacity: 7 }]).map((v) => [v.name, v.opacity]),
    [['ok', 1]]);
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.deepEqual(V.loadSaved(blocked), []);
  assert.equal(V.storeSaved(list, blocked), false);
});

test('a file to share: only what differs from the defaults, read back the same; other files refused', () => {
  const list = [V.BUILT_IN.find((v) => v.id === 'crystal-skeleton-rings'), V.fromCell(cell({ opacity: 0.4 }), 'Plain 0.4')];
  const text = V.toFile(list, new Date('2026-10-19T12:00:00Z'));
  const doc = JSON.parse(text);
  assert.equal(doc.kind, V.FILE_KIND);
  assert.equal(doc.version, 1);
  assert.equal(doc.saved_at, '2026-10-19T12:00:00.000Z');
  assert.deepEqual(doc.visualizations[0].display, {
    showSurface: false, showSkeleton: true, showDiscs: true, discColor: '#edec4f', discOpacity: 1,
  }, 'only the keys that differ (stroke 0.1, Through and width 0 are the defaults)');
  assert.deepEqual(doc.visualizations[1].render, {});
  const { items, skipped } = V.fromFile(text);
  assert.equal(skipped, 0);
  assert.equal(items.length, 2);
  for (const [i, vis] of items.entries()) {
    const original = V.normalizeVisualization(list[i]);
    assert.equal(V.lookSignature(vis), V.lookSignature(original), `${vis.name} reads back the same look`);
  }
  // One look alone, or a plain list, is read too.
  assert.equal(V.fromFile(JSON.stringify({ name: 'One', display: { showSkeleton: true } })).items.length, 1);
  assert.equal(V.fromFile(JSON.stringify([{ name: 'A', render: {} }, { bad: 1 }])).skipped, 1);
  assert.throws(() => V.fromFile('not json'), /not JSON/);
  assert.throws(() => V.fromFile('{"kind":"other"}'), /names no visualization/);
  assert.throws(() => V.fromFile(JSON.stringify({ kind: V.FILE_KIND, version: 9, visualizations: [] })), /newer/);
  assert.throws(() => V.fromFile(JSON.stringify({ kind: V.FILE_KIND, version: 1, visualizations: [{}] })), /holds no/);
  assert.equal(V.fileName(list), '2-visualizations.sbfsem-vis.json');
  assert.equal(V.fileName([list[1]]), 'plain-0-4.sbfsem-vis.json');
  // A render knob out of range in a file is clamped, never sent to the server as it is.
  const wild = V.fromFile(JSON.stringify({ name: 'Wild', render: { voxel2_faces: -5 } })).items[0];
  assert.ok(wild.render.voxel2_faces >= 0);
});

test('Golgi: MATLAB\'s golgi.m as a look -- the surface alone, solid and unlit, and the view it is seen in', () => {
  const golgi = V.BUILT_IN.find((v) => v.id === 'golgi');
  assert.ok(golgi);
  const n = V.normalizeVisualization(golgi);
  assert.equal(n.display.material, 'Unlit');
  assert.equal(n.display.showSurface, true);
  assert.ok(!n.display.showDiscs && !n.display.showSkeleton && !n.display.showWireframe && !n.display.showSynapses);
  assert.equal(n.opacity, 1);
  assert.deepEqual(n.scene, { invert: true, two_d: true }, 'a light background, straight down Z');
  // The cell keeps its own color: a look never carries one.
  assert.ok(!('color' in n) && !('color' in n.display));
});

test('a look\'s scene: only Display\'s Light background and 2D, as booleans; none is none', () => {
  assert.equal(V.normalizeScene(null), null);
  assert.equal(V.normalizeScene({}), null);
  assert.equal(V.normalizeScene({ flip_z: true, invert: 'yes' }), null, 'nothing else, and no strings');
  assert.deepEqual(V.normalizeScene({ invert: false, two_d: true, extra: 1 }), { invert: false, two_d: true });
  assert.ok(!('scene' in V.normalizeVisualization({ name: 'Plain' })), 'a look without one has none');
  assert.ok(!('scene' in V.fromCell(cell(), 'Mine')), 'saving a cell keeps no scene: the cell has none');
});

test('a scene travels in a file, and is not part of telling which look a cell shows', () => {
  const golgi = V.BUILT_IN.find((v) => v.id === 'golgi');
  const back = V.fromFile(V.toFile([golgi])).items[0];
  assert.deepEqual(back.scene, { invert: true, two_d: true });
  const record = cell();
  const look = V.applied(golgi, record);
  assert.ok(!('scene' in look), 'applying sets the cell; the app sets the page\'s view');
  const shown = { ...record, ...look };
  assert.equal(V.matching(shown, V.BUILT_IN)?.id, 'golgi');
  const { scene, ...cellOnly } = golgi;
  assert.ok(scene);
  assert.equal(V.lookSignature(V.applied(cellOnly, record)), V.lookSignature(look));
});

test('every material a built-in look names is a built-in finish (materials.js)', async () => {
  const { readFile } = await import('node:fs/promises');
  const source = await readFile(path.join(jsDir, 'materials.js'), 'utf8');
  const presets = source.match(/export const PRESETS = \{\n([\s\S]*?)\n\};/)[1];
  const names = new Set([...presets.matchAll(/^ {2}(\w+): \{/gm)].map((m) => m[1]));
  assert.ok(names.has('Standard') && names.has('Unlit'), [...names].join(' '));
  assert.match(presets, /Unlit: \{[\s\S]*?unlit: true,/);
  for (const vis of V.BUILT_IN) {
    if (vis.display.material) assert.ok(names.has(vis.display.material), `${vis.id}: ${vis.display.material}`);
  }
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
