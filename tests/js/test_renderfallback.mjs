// What to build when a render fails because of its settings (static/js/renderfallback.js):
// which failures are the settings', and the settings to try instead, best first.
// Run it with
//
//     node tests/js/test_renderfallback.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const fb = await import(pathToFileURL(path.join(jsDir, 'renderfallback.js')).href);
const ro = await import(pathToFileURL(path.join(jsDir, 'renderoptions.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// This browser's saved technique defaults (renderoptions.loadTechDefaults reads them).
const saved = new Map();
globalThis.localStorage = {
  getItem: (k) => (saved.has(k) ? saved.get(k) : null),
  setItem: (k, v) => { saved.set(k, String(v)); },
  removeItem: (k) => { saved.delete(k); },
};

/** A failed job's error as api.js jobFailure makes it. */
const jobError = (detail) => Object.assign(new Error(detail), { status: 400, detail, serverDetail: detail });
/** Options for a preset by id, at its defaults. */
const preset = (id) => ro.normalizeRender(ro.presetOptions(ro.RENDER_PRESETS.find((p) => p.id === id)));
/** Temporal c162392 on 2026-10-21, as the server held it: one disc, nothing linked. */
const ONE_DISC = { n_nodes: 1, n_edges: 0, n_disc_annotations: 1, n_curve_annotations: 0, type_id: 1 };
/** A traced cell: discs and outlines, linked. */
const MIXED = { n_nodes: 420, n_edges: 419, n_disc_annotations: 400, n_curve_annotations: 20, type_id: 1 };
const DISCS = { n_nodes: 199, n_edges: 194, n_disc_annotations: 199, n_curve_annotations: 0, type_id: 1 };
const names = (list) => list.map((c) => fb.techniqueName(c.options));

test('an empty build, a server refusal and running out of memory are the settings\'', () => {
  assert.deepEqual(fb.failureOf({ empty: true }), { kind: 'empty', detail: '' });
  const refusal = 'Closed-curve volume would need 759,817,520 voxels (1340 x 1052 x 539) at 0.05 um/pixel; '
    + 'the limit is 300,000,000. The finest pixel that fits this structure is 0.069 um';
  const fine = fb.failureOf({ error: jobError(refusal) });
  assert.equal(fine.kind, 'too-fine');
  assert.equal(fine.fits, 0.069);
  assert.equal(fine.measure, 'voxels');
  assert.equal(fb.failureOf({ error: jobError('A process in the process pool was terminated abruptly while '
    + 'the future was running or pending.') }).kind, 'memory');
  assert.equal(fb.failureOf({ error: jobError('the build process stopped before this build finished (the '
    + 'server most likely ran out of memory). It has been restarted') }).kind, 'memory');
  assert.equal(fb.failureOf({ error: jobError('MeshLab poisson returned an empty mesh') }).kind, 'error');
  assert.equal(fb.failureOf({ error: Object.assign(new Error('x'), { status: 422, detail: 'tie_um: too big' }) }).kind,
    'error');
});

test('the network, a restart, a lost job and a stop are not', () => {
  assert.equal(fb.failureOf({ error: new TypeError('Failed to fetch') }), null);
  assert.equal(fb.failureOf({ error: Object.assign(new Error('gateway'), { status: 502, detail: 'Bad Gateway' }) }),
    null);
  assert.equal(fb.failureOf({ error: Object.assign(new Error('gone'), { status: 404, detail: 'not loaded' }) }), null);
  assert.equal(fb.failureOf({ error: Object.assign(new Error('stop'), { aborted: true }) }), null);
  assert.equal(fb.failureOf({}), null);
});

test('one unlinked disc: no disc technique is tried, Closed curve is, then finer', () => {
  const ladder = fb.fallbacks(preset('voxel'), ONE_DISC, { kind: 'empty' });
  assert.deepEqual(names(ladder), ['Closed curve volume', 'Closed curve volume']);
  assert.deepEqual(ladder.map((c) => c.options.pixel_size_um), [0.25, ro.DEFAULT_RENDER.pixel_size_um]);
  assert.match(ladder[0].detail, /linked or not/);
});

test('a linked disc cell that built nothing: the defaults, Voxel remesh, then Closed curve', () => {
  const asked = { ...preset('convolution'), conv_support: 2.5 };
  const ladder = fb.fallbacks(asked, DISCS, { kind: 'empty' });
  assert.deepEqual(names(ladder),
    ['Convolution surface', 'Voxel remesh', 'Closed curve volume', 'Closed curve volume']);
  assert.equal(ladder[0].text, 'Convolution surface defaults');
  assert.equal(ladder[0].options.conv_support, ro.DEFAULT_RENDER.conv_support);
});

test('the defaults saved in this browser are the ones tried, and said to be', () => {
  ro.storeTechDefaults({ 'cylinder|convolution': { conv_support: 1.5 } });
  try {
    const [first] = fb.fallbacks({ ...preset('convolution'), conv_support: 2.5 }, DISCS, { kind: 'empty' });
    assert.equal(first.options.conv_support, 1.5);
    assert.equal(first.detail, 'the defaults you saved for this technique');
  } finally {
    saved.clear();
  }
});

test('a cell with traced outlines is offered Hybrid', () => {
  const ladder = fb.fallbacks(preset('original'), MIXED, { kind: 'empty' });
  assert.deepEqual(names(ladder),
    ['Voxel remesh', 'Hybrid (discs + curves)', 'Closed curve volume', 'Closed curve volume']);
  // Hybrid's outline raster is seeded for a traced cell, as the technique picker seeds it.
  assert.equal(ladder[1].options.pixel_size_um, 0.25);
});

test('the build that failed is never offered again, nor one build twice', () => {
  const ladder = fb.fallbacks(preset('voxel2'), DISCS, { kind: 'error', detail: 'boom' });
  assert.ok(!names(ladder).includes('Voxel remesh'), 'Voxel remesh at its defaults is what failed');
  const signatures = ladder.map((c) => ro.effectiveSignature(c.options));
  assert.equal(new Set(signatures).size, signatures.length);
  assert.ok(!signatures.includes(ro.effectiveSignature(preset('voxel2'))));
});

test('a Voxel size too fine: the size the server says fits comes first', () => {
  const asked = { ...preset('closedcurve'), pixel_size_um: 0.05 };
  const ladder = fb.fallbacks(asked, MIXED, { kind: 'too-fine', fits: 0.069, measure: 'voxels' });
  assert.equal(ladder[0].options.method, 'closedcurve');
  assert.equal(ladder[0].options.pixel_size_um, 0.069);
  assert.equal(ladder[0].text, 'Voxel 0.069 µm');
});

test('out of memory: coarser outlines, or the field defaults\' grid', () => {
  const curve = fb.fallbacks({ ...preset('hybrid'), pixel_size_um: 0.1 }, MIXED, { kind: 'memory' });
  assert.equal(curve[0].options.method, 'hybrid');
  assert.equal(curve[0].options.pixel_size_um, 0.2);
  const field = fb.fallbacks({ ...preset('voxel2'), voxel2_um: 0.03, voxel2_faces: 0 }, DISCS, { kind: 'memory' });
  assert.equal(field[0].options.voxel2_um, ro.DEFAULT_RENDER.voxel2_um);
  assert.equal(field[0].options.voxel2_faces, ro.DEFAULT_RENDER.voxel2_faces);
  assert.equal(field[0].options.pathway, 'voxel2');
});

test('an outline method that built nothing tries a finer Voxel size first', () => {
  const ladder = fb.fallbacks({ ...preset('closedcurve'), pixel_size_um: 0.25 }, ONE_DISC, { kind: 'empty' });
  assert.equal(ladder[0].options.method, 'closedcurve');
  assert.equal(ladder[0].options.pixel_size_um, ro.DEFAULT_RENDER.pixel_size_um);
  assert.equal(ladder.length, 1, 'the coarse Closed curve is what failed; the finer is the one way left');
});

test('what belongs to the cell stays with it', () => {
  const asked = { ...preset('voxel'), color_by_strata: true, clip_ipl: 0.4, keep_above: false };
  for (const c of fb.fallbacks(asked, MIXED, { kind: 'empty' })) {
    assert.equal(c.options.color_by_strata, true);
    assert.equal(c.options.clip_ipl, 0.4);
    assert.equal(c.options.keep_above, false);
  }
});

test('the words: make-up, what failed, and what to say when nothing built it', () => {
  assert.equal(fb.makeupText(ONE_DISC), '1 annotation, no links');
  assert.equal(fb.makeupText(MIXED), '420 annotations (20 traced outlines), 419 links');
  assert.equal(fb.makeupText({ n_nodes: 3, n_curve_annotations: 3, n_edges: 2 }),
    '3 annotations (all traced outlines), 2 links');
  assert.equal(fb.makeupText({}), '');
  assert.equal(fb.failurePhrase({ kind: 'empty' }, preset('voxel')), 'Voxel remesh orig built nothing');
  assert.equal(fb.failureReason('c162392', { kind: 'empty' }, ONE_DISC), 'c162392 has 1 annotation, no links');
  assert.match(fb.exhaustedNote('c162392', { kind: 'empty' }, ONE_DISC),
    /^no technique could build a surface from c162392 \(1 annotation, no links\); load it again \(↻\)/);
  assert.equal(fb.exhaustedNote('c1', { kind: 'memory' }, DISCS), 'no other settings built c1 either');
});

test('a fix\'s button changes only what differs; the controls a failure points at', () => {
  const asked = preset('voxel');
  const set = fb.changedOptions(asked, preset('closedcurve'));
  assert.equal(set.method, 'closedcurve');
  assert.ok(!('color_by_strata' in set));
  assert.deepEqual(fb.failureKeys({ kind: 'empty' }, asked), ['method', 'pathway']);
  assert.deepEqual(fb.failureKeys({ kind: 'too-fine' }, preset('closedcurve')), ['pixel_size_um']);
  assert.deepEqual(fb.failureKeys({ kind: 'memory' }, preset('voxel2')), ['voxel2_um', 'voxel2_faces']);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`not ok - ${name}\n${error.stack}`);
  }
}
if (failed) process.exit(1);
