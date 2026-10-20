// The layer model of Render compare, without a page (static/js/comparelayers.js):
// the palette and what a layer starts as, the snapshot rule a request is made from,
// the worst of a pane's layer states, the draw order, the Z range, the browser's
// mesh cache and the scheduler that lets three panes work at once.  Run with
//
//     node tests/js/test_comparelayers.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free, no DOM.
// (compare.js uses all of it against a fake page: test_compare.mjs.)

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const cl = await load('comparelayers.js');
const ro = await load('renderoptions.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const settle = (ms = 5) => new Promise((resolve) => setTimeout(resolve, ms));
/** A promise and the function that settles it. */
function gate() {
  let release;
  let fail;
  const promise = new Promise((resolve, reject) => { release = resolve; fail = reject; });
  return { promise, release, fail };
}

// -- the palette ---------------------------------------------------------------------------

// CIEDE2000 under the four kinds of vision, as the spec measured the palette
// (Machado 2009 at severity 1, in linear light; the dark viewport behind).
const MACHADO = {
  protan: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deutan: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
  tritan: [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]],
};
const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toGamma = (c) => {
  const v = Math.min(1, Math.max(0, c));
  return v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055;
};
const rgbOf = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
function simulate(hex, kind) {
  const lin = rgbOf(hex).map(toLinear);
  const out = kind === 'normal' ? lin : MACHADO[kind].map((row) => row.reduce((s, m, i) => s + m * lin[i], 0));
  return out.map(toGamma);
}
function lab(rgb) {
  const [r, g, b] = rgb.map(toLinear);
  const x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047;
  const y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b;
  const z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883;
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}
function deltaE2000([L1, a1, b1], [L2, a2, b2]) {
  const rad = (d) => d * Math.PI / 180;
  const deg = (r) => ((r * 180 / Math.PI) % 360 + 360) % 360;
  const C1 = Math.hypot(a1, b1);
  const C2 = Math.hypot(a2, b2);
  const Cm = (C1 + C2) / 2;
  const G = 0.5 * (1 - Math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)));
  const a1p = (1 + G) * a1;
  const a2p = (1 + G) * a2;
  const C1p = Math.hypot(a1p, b1);
  const C2p = Math.hypot(a2p, b2);
  const h1p = C1p === 0 ? 0 : deg(Math.atan2(b1, a1p));
  const h2p = C2p === 0 ? 0 : deg(Math.atan2(b2, a2p));
  const dL = L2 - L1;
  const dC = C2p - C1p;
  let dh = 0;
  if (C1p * C2p !== 0) {
    dh = h2p - h1p;
    if (dh > 180) dh -= 360; else if (dh < -180) dh += 360;
  }
  const dH = 2 * Math.sqrt(C1p * C2p) * Math.sin(rad(dh) / 2);
  const Lm = (L1 + L2) / 2;
  const Cmp = (C1p + C2p) / 2;
  let hm = h1p + h2p;
  if (C1p * C2p !== 0) {
    hm = Math.abs(h1p - h2p) <= 180 ? (h1p + h2p) / 2 : (h1p + h2p + (h1p + h2p < 360 ? 360 : -360)) / 2;
  }
  const T = 1 - 0.17 * Math.cos(rad(hm - 30)) + 0.24 * Math.cos(rad(2 * hm))
    + 0.32 * Math.cos(rad(3 * hm + 6)) - 0.20 * Math.cos(rad(4 * hm - 63));
  const dTheta = 30 * Math.exp(-(((hm - 275) / 25) ** 2));
  const Rc = 2 * Math.sqrt(Cmp ** 7 / (Cmp ** 7 + 25 ** 7));
  const Sl = 1 + (0.015 * (Lm - 50) ** 2) / Math.sqrt(20 + (Lm - 50) ** 2);
  const Sc = 1 + 0.045 * Cmp;
  const Sh = 1 + 0.015 * Cmp * T;
  const Rt = -Math.sin(rad(2 * dTheta)) * Rc;
  return Math.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2 + Rt * (dC / Sc) * (dH / Sh));
}
const KINDS = ['normal', 'protan', 'deutan', 'tritan'];
const deltaE = (a, b, kind) => deltaE2000(lab(simulate(a, kind)), lab(simulate(b, kind)));
const worstPair = (colors) => Math.min(...KINDS.flatMap((kind) => colors.flatMap((a, i) =>
  colors.slice(i + 1).map((b) => deltaE(a, b, kind)))));

test('the CIEDE2000 used here agrees with the standard test pairs', () => {
  // Sharma, Wu and Dalal's published pairs (pairs 1, 7 and 17 of their table).
  const near = (got, want) => assert.ok(Math.abs(got - want) < 1e-3, `${got} against ${want}`);
  near(deltaE2000([50, 2.6772, -79.7751], [50, 0, -82.7485]), 2.0425);
  near(deltaE2000([50, 2.5, 0], [50, 0, -2.5]), 4.3065);
  near(deltaE2000([60.2574, -34.0099, 36.2677], [60.4626, -34.1751, 39.4387]), 1.2644);
});

test('the layer colors are the four the spec measured, in slot order, and stay apart for every kind of vision', () => {
  assert.deepEqual([...cl.LAYER_PALETTE], ['#ffd400', '#56b4e9', '#cc79a7', '#009e73']);
  assert.ok(Object.isFrozen(cl.LAYER_PALETTE));
  assert.equal(cl.SURFACE_COLOR, '#ffd400', 'slot 0 is the yellow every pane has always had');
  assert.equal(cl.WIREFRAME_COLOR, '#202020');
  assert.equal(cl.DISC_COLOR, '#0019ff');
  // The spec's measurement: 43.9, 14.0 and 12.1 for two, three and four layers.
  const worst = [2, 3, 4].map((n) => worstPair([...cl.LAYER_PALETTE].slice(0, n)));
  assert.ok(worst[0] >= 43.85, worst[0]);
  assert.ok(worst[1] >= 13.95, worst[1]);
  assert.ok(worst[2] >= 12.05, worst[2]);
  // And each is far enough from the viewport behind it to be seen.
  const background = '#101418';
  for (const color of cl.LAYER_PALETTE) {
    for (const kind of KINDS) assert.ok(deltaE(color, background, kind) > 20, `${color} ${kind}`);
  }
  assert.equal(cl.MAX_LAYERS, 4, 'no fifth color cleared the others');
});

test('a layer starts opaque in the first slot and translucent after, every color in lower case', () => {
  assert.deepEqual(cl.newLayerStyle(0), { color: '#ffd400', opacity: 1, visible: true, surface: true, wire: false });
  for (const slot of [1, 2, 3]) {
    const style = cl.newLayerStyle(slot);
    assert.equal(style.opacity, 0.45);
    assert.equal(style.color, cl.LAYER_PALETTE[slot]);
    assert.equal(style.visible && style.surface && !style.wire, true);
  }
  assert.equal(cl.newLayerStyle(1, '#CC79A7').color, '#cc79a7', 'a color given is normalized');
  assert.equal(cl.newLayerStyle(1, 'nonsense').color, '#56b4e9', 'and a bad one is the slot\'s');
  assert.equal(cl.newLayerStyle(5).color, cl.LAYER_PALETTE[1], 'slots wrap');
});

test('the next color is the first palette color not in use, whatever its case', () => {
  assert.equal(cl.nextColor([]), '#ffd400');
  assert.equal(cl.nextColor(['#ffd400']), '#56b4e9');
  assert.equal(cl.nextColor(['#ffd400', '#56B4E9']), '#cc79a7');
  assert.equal(cl.nextColor(['#ffd400', '#cc79a7']), '#56b4e9', 'a freed slot is taken again');
  assert.equal(cl.nextColor(['#56b4e9', '#ffd400', '#cc79a7', '#009e73']), cl.LAYER_PALETTE[0], 'full: wraps');
  assert.equal(cl.nextColor(['#112233']), '#ffd400', 'a color of the user\'s own takes no slot');
});

test('a wire takes its layer\'s hue at 45 percent; the yellow keeps the near-black', () => {
  assert.equal(cl.wireColorFor('#ffd400'), '#202020');
  assert.equal(cl.wireColorFor('#FFD400'), '#202020');
  assert.equal(cl.wireColorFor('#56b4e9'), '#275169');
  assert.equal(cl.wireColorFor('#ffffff'), '#737373');
  assert.equal(cl.darken('#ff8000', 0.5), '#804000');
  assert.equal(cl.darken('garbage', 1), '#808080');
  assert.equal(cl.wireColorFor('garbage'), '#202020', 'no color is the default layer\'s');
});

test('opacity is kept between 0.05 and 1; blank or nonsense is opaque', () => {
  assert.equal(cl.clampOpacity(0.45), 0.45);
  assert.equal(cl.clampOpacity('0.3'), 0.3);
  assert.equal(cl.clampOpacity(7), 1);
  assert.equal(cl.clampOpacity(0), 0.05);
  assert.equal(cl.clampOpacity(-1), 0.05);
  for (const blank of ['', null, undefined, 'x', NaN]) assert.equal(cl.clampOpacity(blank), 1);
  assert.equal(cl.GHOST_OPACITY, 0.15);
  assert.ok(cl.GHOST_OPACITY >= cl.MIN_OPACITY && cl.GHOST_OPACITY < cl.LATER_OPACITY);
});

test('a technique\'s name is short on a chip', () => {
  assert.equal(cl.shortName('Original (MATLAB)'), 'Original');
  assert.equal(cl.shortName('Tube (curveToMesh)'), 'Tube');
  assert.equal(cl.shortName('Voxel remesh + MeshLab isotropic'), 'Voxel remesh + ML isotropic');
  assert.equal(cl.shortName('Voxel remesh'), 'Voxel remesh');
  assert.equal(cl.shortName(undefined), '');
  for (const preset of ro.RENDER_PRESETS) assert.ok(cl.shortName(preset.label).length <= 28, preset.label);
});

// -- the snapshot rule ---------------------------------------------------------------------

test('a request is made from the snapshot: the geometry options, in the schema\'s order, nothing else', () => {
  const options = { ...ro.DEFAULT_RENDER, clip_ipl: 0.4, keep_above: false, color_by_strata: true, extra: 1 };
  const picked = cl.pickOptions(options);
  assert.deepEqual(Object.keys(picked), ro.MESH_OPTION_KEYS);
  assert.equal('extra' in picked, false);
  assert.equal('clip_ipl' in picked, false);
  assert.equal('keep_above' in picked, false);
  // A key the options lack is left out, not sent as undefined.
  const partial = cl.pickOptions({ method: 'cylinder', pathway: 'wrap' });
  assert.deepEqual(Object.keys(partial), ['method', 'pathway']);
  // Equal settings, equal key; one knob moved, another key.
  const a = cl.stableKey(cl.pickOptions({ ...ro.DEFAULT_RENDER }));
  const b = cl.stableKey(cl.pickOptions({ ...ro.DEFAULT_RENDER, smooth_iterations: 4 }));
  assert.equal(a, cl.stableKey(cl.pickOptions({ ...ro.DEFAULT_RENDER, clip_ipl: 0.9 })), 'the clip is no part of it');
  assert.notEqual(a, b);
  assert.equal(a, JSON.stringify(cl.pickOptions(ro.DEFAULT_RENDER)));
});

test('a mesh is filed under the cell, the load of it and the snapshot', () => {
  const record = { key: 'NeitzInferiorMonkey/1178', loadGen: 3 };
  const picked = cl.pickOptions(ro.DEFAULT_RENDER);
  const key = cl.meshKey(record, picked);
  assert.equal(key, `NeitzInferiorMonkey/1178#3|${JSON.stringify(picked)}`);
  assert.notEqual(key, cl.meshKey({ ...record, loadGen: 4 }, picked), 'another load of the cell');
  assert.notEqual(key, cl.meshKey({ ...record, key: 'NeitzInferiorMonkey/606' }, picked), 'another cell');
  assert.notEqual(key, cl.meshKey(record, cl.pickOptions({ ...ro.DEFAULT_RENDER, tie_um: 9 })), 'another technique tuning');
});

// -- states -------------------------------------------------------------------------------------

test('a pane is as far from ready as its worst layer', () => {
  const w = cl.worstState;
  assert.equal(w(['ready']), 'ready');
  assert.equal(w(['ready', 'ready', 'ready']), 'ready');
  assert.equal(w(['idle']), 'idle');
  assert.equal(w([]), 'idle');
  assert.equal(w(['ready', 'stale']), 'stale');
  assert.equal(w(['ready', 'idle']), 'stale', 'a layer never built beside built ones: the pane is out of date');
  assert.equal(w(['idle', 'idle']), 'idle');
  assert.equal(w(['ready', 'stopped']), 'stopped');
  assert.equal(w(['stale', 'stopped']), 'stopped');
  assert.equal(w(['stopped', 'error-measure']), 'error-measure');
  assert.equal(w(['error-measure', 'error-build']), 'error-build');
  assert.equal(w(['error-build', 'queued']), 'queued');
  assert.equal(w(['queued', 'measuring']), 'measuring');
  assert.equal(w(['building', 'measuring']), 'building');
  assert.equal(w(['ready', 'building', 'error-build']), 'building');
});

// -- order ---------------------------------------------------------------------------------------

test('the draw order is the list order, and moving a layer moves the others to close the gap', () => {
  const list = ['a', 'b', 'c', 'd'];
  assert.deepEqual(cl.moveItem(list, 0, 2), ['b', 'c', 'a', 'd']);
  assert.deepEqual(cl.moveItem(list, 3, 0), ['d', 'a', 'b', 'c']);
  assert.deepEqual(cl.moveItem(list, 1, 1), list);
  assert.deepEqual(cl.moveItem(list, 1, 99), ['a', 'c', 'd', 'b'], 'clamped');
  assert.deepEqual(cl.moveItem(list, 1, -5), ['b', 'a', 'c', 'd'], 'clamped');
  assert.deepEqual(cl.moveItem(list, 9, 0), list, 'an index that is not there moves nothing');
  assert.deepEqual(list, ['a', 'b', 'c', 'd'], 'the list itself is not changed');
  // The place in the draw order is the index: moving c to the front gives c, a, b their places 0, 1, 2.
  assert.deepEqual(cl.moveItem(['a', 'b', 'c'], 2, 0).map((item, index) => `${item}${index}`), ['c0', 'a1', 'b2']);
});

test('a Z range is two numbers in order, open on a blank side, none when both are', () => {
  assert.deepEqual(cl.normalizeZRange('1', '2.5'), { min: 1, max: 2.5 });
  assert.deepEqual(cl.normalizeZRange(2.5, 1), { min: 1, max: 2.5 });
  assert.deepEqual(cl.normalizeZRange('', '4'), { min: null, max: 4 });
  assert.deepEqual(cl.normalizeZRange('3', ''), { min: 3, max: null });
  assert.deepEqual(cl.normalizeZRange('-2', '-5'), { min: -5, max: -2 });
  assert.deepEqual(cl.normalizeZRange('0', '0'), { min: 0, max: 0 }, 'a plane is a range');
  assert.equal(cl.normalizeZRange('', ''), null);
  assert.equal(cl.normalizeZRange(null, undefined), null);
  assert.equal(cl.normalizeZRange('abc', NaN), null);
  assert.deepEqual(cl.normalizeZRange('abc', '2'), { min: null, max: 2 }, 'a side that is not a number is open');
});

// -- the mesh cache ------------------------------------------------------------------------------

const mesh = (id) => ({ mesh_id: id, n_vertices: 3, n_faces: 1 });

test('the cache keeps twelve meshes, the most recently used', () => {
  assert.equal(cl.MESH_CACHE_LIMIT, 12);
  const cache = new cl.MeshCache();
  for (let i = 0; i < 12; i += 1) cache.set(`k${i}`, mesh(i));
  assert.equal(cache.size, 12);
  assert.equal(cache.get('k0').mesh_id, 0, 'reading k0 makes it the most recently used');
  cache.set('k12', mesh(12));
  assert.equal(cache.size, 12);
  assert.equal(cache.has('k1'), false, 'the least recently used went');
  assert.equal(cache.has('k0'), true);
  assert.deepEqual(cache.keys().slice(-2), ['k0', 'k12']);
  cache.set('k5', mesh('again'));
  assert.equal(cache.get('k5').mesh_id, 'again', 'setting a filed key replaces it');
  assert.equal(cache.size, 12);
  assert.equal(cache.get('nope'), undefined);
  cache.clear();
  assert.equal(cache.size, 0);
  const small = new cl.MeshCache(2);
  small.set('a', 1); small.set('b', 2); small.set('c', 3);
  assert.deepEqual(small.keys(), ['b', 'c']);
});

test('fetching a filed mesh starts nothing; fetching a new one files it', async () => {
  const cache = new cl.MeshCache();
  let started = 0;
  const start = async () => { started += 1; return mesh('m'); };
  assert.equal((await cache.fetch('k', start)).mesh_id, 'm');
  assert.equal(started, 1);
  assert.equal((await cache.fetch('k', start)).mesh_id, 'm');
  assert.equal(started, 1, 'the second time came from the cache');
  assert.equal(cache.has('k'), true);
  // A mesh with no faces is not worth keeping (the pane says why it is empty).
  const empty = await cache.fetch('empty', async () => ({ n_vertices: 0, n_faces: 0 }));
  assert.equal(empty.n_faces, 0);
  assert.equal(cache.has('empty'), false);
});

test('two layers asking for one mesh at once make one download and both get it', async () => {
  const cache = new cl.MeshCache();
  const g = gate();
  let started = 0;
  const seen = { a: [], b: [] };
  const start = ({ onStatus }) => { started += 1; onStatus({ n: 1 }); return g.promise; };
  const a = cache.fetch('k', start, { onStatus: (job) => seen.a.push(job) });
  const b = cache.fetch('k', start, { onStatus: (job) => seen.b.push(job) });
  await settle();
  assert.equal(started, 1);
  assert.equal(cache.waiting('k'), 2);
  g.release(mesh('shared'));
  const [ma, mb] = await Promise.all([a, b]);
  assert.equal(ma, mb);
  assert.equal(cache.waiting('k'), 0);
  assert.equal(cache.get('k'), ma);
  // The first waiter heard the poll that began before the second joined; later polls reach both.
  assert.equal(seen.a.length, 1);
});

test('every waiter hears every poll that follows its joining', async () => {
  const cache = new cl.MeshCache();
  const g = gate();
  let report;
  const seen = { a: [], b: [] };
  const a = cache.fetch('k', ({ onStatus }) => { report = onStatus; return g.promise; }, { onStatus: (j) => seen.a.push(j) });
  await settle();
  const b = cache.fetch('k', () => assert.fail('joined, not started'), { onStatus: (j) => seen.b.push(j) });
  report({ poll: 1 });
  report({ poll: 2 });
  g.release(mesh('m'));
  await Promise.all([a, b]);
  assert.deepEqual(seen.a, [{ poll: 1 }, { poll: 2 }]);
  assert.deepEqual(seen.b, [{ poll: 1 }, { poll: 2 }]);
});

test('a waiter that stops leaves the download to the others; the last one to leave ends it', async () => {
  const cache = new cl.MeshCache();
  const g = gate();
  let download;
  const start = ({ signal }) => { download = signal; return g.promise; };
  const stopA = new AbortController();
  const stopB = new AbortController();
  const a = cache.fetch('k', start, { signal: stopA.signal });
  const b = cache.fetch('k', start, { signal: stopB.signal });
  await settle();
  stopA.abort();
  await assert.rejects(a, (error) => error.aborted === true);
  assert.equal(download.aborted, false, 'the other layer still wants it');
  assert.equal(cache.waiting('k'), 1);
  stopB.abort();
  await assert.rejects(b, (error) => error.aborted === true);
  assert.equal(download.aborted, true, 'nobody is left: the download is abandoned');
  assert.equal(cache.waiting('k'), 0);
  // A new request does not join the abandoned download; it starts its own.
  const again = cache.fetch('k', () => Promise.resolve(mesh('fresh')));
  assert.equal((await again).mesh_id, 'fresh');
  g.release(mesh('late'));
  await settle();
  assert.equal(cache.get('k').mesh_id, 'fresh', 'the abandoned one never overwrites it');
});

test('a waiter that is already stopped never starts a download of its own', async () => {
  const cache = new cl.MeshCache();
  const stop = new AbortController();
  stop.abort();
  let started = 0;
  await assert.rejects(cache.fetch('k', async () => { started += 1; return mesh('x'); }, { signal: stop.signal }),
    (error) => error.aborted === true);
  await settle();
  assert.equal(started, 0, 'nothing was started for a waiter that had already left');
  assert.equal(cache.has('k'), false);
  assert.equal(cache.waiting('k'), 0);
  // The same for a download that is asked for fresh.
  await assert.rejects(cache.fetch('k', async () => { started += 1; return mesh('y'); }, { signal: stop.signal, fresh: true }),
    (error) => error.aborted === true);
  await settle();
  assert.equal(started, 0);
});

test('a download that fails reaches every waiter, is not filed, and is tried again next time', async () => {
  const cache = new cl.MeshCache();
  const g = gate();
  const a = cache.fetch('k', () => g.promise);
  const b = cache.fetch('k', () => g.promise);
  g.fail(new Error('out of memory'));
  await assert.rejects(a, /out of memory/);
  await assert.rejects(b, /out of memory/);
  assert.equal(cache.has('k'), false);
  assert.equal((await cache.fetch('k', async () => mesh('second'))).mesh_id, 'second');
});

test('fresh skips what is filed and what is under way, and replaces what is filed', async () => {
  const cache = new cl.MeshCache();
  cache.set('k', mesh('old'));
  let started = 0;
  const got = await cache.fetch('k', async () => { started += 1; return mesh('new'); }, { fresh: true });
  assert.equal(got.mesh_id, 'new');
  assert.equal(started, 1);
  assert.equal(cache.get('k').mesh_id, 'new');
});

// -- scheduling ----------------------------------------------------------------------------------

/** A job that records when it started and ends when told. */
function job(log, name) {
  const g = gate();
  return { run: () => { log.push(`start ${name}`); return g.promise.then(() => { log.push(`end ${name}`); }); }, g };
}

test('the jobs of one pane run one after another, in the order they were queued', async () => {
  const queues = new cl.WorkQueues();
  const log = [];
  const a = job(log, 'a'); const b = job(log, 'b'); const c = job(log, 'c');
  const pa = queues.enqueue('pane1', a.run);
  const pb = queues.enqueue('pane1', b.run);
  const pc = queues.enqueue('pane1', c.run);
  await settle();
  assert.deepEqual(log, ['start a']);
  assert.equal(queues.waiting, 2);
  a.g.release(); await pa;
  await settle();
  assert.deepEqual(log, ['start a', 'end a', 'start b']);
  b.g.release(); await pb;
  c.g.release(); await pc;
  assert.deepEqual(log, ['start a', 'end a', 'start b', 'end b', 'start c', 'end c']);
  assert.deepEqual(queues.working, []);
  assert.equal(queues.waiting, 0);
});

test('at most three panes work at once, and the next pane in line takes a place as one frees', async () => {
  assert.equal(cl.MAX_WORKING_PANES, 3);
  const queues = new cl.WorkQueues();
  const log = [];
  const jobs = [1, 2, 3, 4, 5, 6].map((n) => job(log, `p${n}`));
  const done = jobs.map((j, i) => queues.enqueue(`pane${i + 1}`, j.run));
  await settle();
  assert.deepEqual(log, ['start p1', 'start p2', 'start p3']);
  assert.deepEqual(queues.working, ['pane1', 'pane2', 'pane3']);
  jobs[1].g.release(); await done[1];
  await settle();
  assert.deepEqual(log.slice(3), ['end p2', 'start p4'], 'the fourth pane took the place pane 2 freed');
  jobs[0].g.release(); jobs[2].g.release();
  await settle();
  assert.deepEqual(queues.working.sort(), ['pane4', 'pane5', 'pane6']);
  for (const j of jobs) j.g.release();
  await Promise.all(done);
  assert.deepEqual(queues.working, []);
  // Never more than three at any moment of the whole run.
  let live = 0;
  let peak = 0;
  for (const line of log) {
    live += line.startsWith('start') ? 1 : -1;
    peak = Math.max(peak, live);
  }
  assert.equal(peak, 3);
});

test('a pane with two jobs holds one place, not two', async () => {
  const queues = new cl.WorkQueues({ limit: 2 });
  const log = [];
  const a1 = job(log, 'a1'); const a2 = job(log, 'a2'); const b1 = job(log, 'b1'); const c1 = job(log, 'c1');
  queues.enqueue('A', a1.run); queues.enqueue('A', a2.run);
  queues.enqueue('B', b1.run); queues.enqueue('C', c1.run);
  await settle();
  assert.deepEqual(log, ['start a1', 'start b1']);
  a1.g.release();
  await settle();
  assert.deepEqual(log.slice(2), ['end a1', 'start a2'], 'its second job follows its first, ahead of pane C');
  a2.g.release(); b1.g.release(); c1.g.release();
});

test('a job waits for what it is told to without taking a place, and the places go to others meanwhile', async () => {
  const queues = new cl.WorkQueues({ limit: 1 });
  const log = [];
  const surfaces = gate();
  const diff = job(log, 'diff');
  const other = job(log, 'other');
  const pd = queues.enqueue('D', diff.run, { after: [surfaces.promise] });
  const po = queues.enqueue('O', other.run);
  await settle();
  assert.deepEqual(log, ['start other'], 'the pane with nothing to wait for goes first');
  other.g.release(); await po;
  await settle();
  assert.deepEqual(log, ['start other', 'end other'], 'its one place stays free while the Difference waits');
  surfaces.release();
  await settle();
  assert.deepEqual(log.slice(2), ['start diff']);
  diff.g.release(); await pd;
  // A dependency that failed still lets the job run (it answers for what is missing).
  const failed = Promise.reject(new Error('build failed'));
  failed.catch(() => {});
  const second = job(log, 'second');
  const ps = queues.enqueue('D', second.run, { after: [failed] });
  await settle();
  assert.equal(log.at(-1), 'start second');
  second.g.release(); await ps;
});

test('a job that throws does not stop the queue; enqueue never rejects', async () => {
  const queues = new cl.WorkQueues();
  const log = [];
  const bad = queues.enqueue('P', async () => { log.push('bad'); throw new Error('bug'); });
  const good = queues.enqueue('P', async () => { log.push('good'); });
  await bad;
  await good;
  assert.deepEqual(log, ['bad', 'good']);
  assert.deepEqual(queues.working, []);
  // A job that throws synchronously is the same.
  await queues.enqueue('Q', () => { throw new Error('sync'); });
  await queues.enqueue('Q', async () => { log.push('after'); });
  assert.equal(log.at(-1), 'after');
});

test('a pane keeps its place while it has jobs, and gives it up to the next pane when it has none', async () => {
  const queues = new cl.WorkQueues({ limit: 1 });
  const log = [];
  const a = job(log, 'a1');
  const pa = queues.enqueue('A', a.run);
  const b = job(log, 'b1');
  queues.enqueue('B', b.run);
  await settle();
  const a2 = job(log, 'a2');
  queues.enqueue('A', a2.run);          // A is still working: its second job queues behind its first
  a.g.release(); await pa;
  await settle();
  assert.equal(log.at(-1), 'start a2', 'a pane that kept its place keeps working until it is out of jobs');
  a2.g.release();
  await settle();
  assert.equal(log.at(-1), 'start b1');
  b.g.release();
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
