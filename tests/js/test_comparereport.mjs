// Render compare's report, without a page (static/js/comparereport.js): the quick
// picks over the pairs, the pairs a dialog starts with, what sets a rendering
// apart, the page count (the server's plan), the time, and the request.  Run with
//
//     node tests/js/test_comparereport.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free, no DOM.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const cr = await load('comparereport.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const V2 = { method: 'cylinder', pathway: 'voxel2' };
const rendering = (ref, pane, layer, options, more = {}) => ({
  ref, pane, layer, options, built: true, faces: 100000, vertices: 50000, label: 'x', color: '#ffd400', ...more,
});
const R = [
  rendering('L1', 1, 1, V2),
  rendering('L2', 1, 2, { ...V2, voxel2_faces: 100000 }, { color: '#56b4e9' }),
  rendering('L3', 2, 1, { method: 'cylinder', pathway: 'original' }),
  rendering('L4', 3, 1, { method: 'curvemesh', pathway: 'original', smooth_iterations: 0 }),
  { ref: 'L5', pane: 3, layer: 2, options: null, built: false, faces: 0, vertices: 0, label: 'Tube', color: '#56b4e9' },
];
const chosen = new Set(['L1', 'L2', 'L3', 'L4']);

test('a pair is unordered and named by both refs', () => {
  assert.equal(cr.pairKey('L3', 'L1'), 'L1|L3');
  assert.equal(cr.pairKey('L1', 'L3'), 'L1|L3');
  assert.deepEqual(cr.splitPair('L1|L3'), ['L1', 'L3']);
  assert.deepEqual(cr.allPairs(['a', 'b', 'c']), ['a|b', 'a|c', 'b|c']);
});

test('the quick picks select what they say', () => {
  assert.equal(cr.pickPairs(R, chosen, 'all').length, 6);
  assert.deepEqual(cr.pickPairs(R, chosen, 'none'), []);
  assert.deepEqual(cr.pickPairs(R, chosen, 'same'), ['L1|L2']);            // two Voxel remesh layers
  assert.equal(cr.pickPairs(R, chosen, 'across').length, 5);
  assert.deepEqual(cr.pickPairs(R, chosen, 'firsts'), ['L1|L3', 'L1|L4', 'L3|L4']);
  assert.deepEqual(cr.pickPairs(R, chosen, 'within'), ['L1|L2']);
  assert.deepEqual(cr.pickPairs(R, chosen, 'screen', [['L3', 'L1'], ['L1', 'L3']]), ['L1|L3']);
});

test('a rendering left out takes its pairs with it', () => {
  const some = new Set(['L1', 'L3']);
  assert.deepEqual(cr.pickPairs(R, some, 'all'), ['L1|L3']);
  assert.deepEqual(cr.pickPairs(R, some, 'screen', [['L1', 'L4']]), []);
  assert.deepEqual(cr.pickPairs(R, new Set(['L1', 'L5']), 'all'), [], 'an unbuilt layer is never in a pair');
});

test('a dialog starts with every pair while there are few, a selection when there are many', () => {
  assert.equal(cr.defaultPairs(R, chosen).length, 6);
  const many = Array.from({ length: 8 }, (_, k) => rendering(`M${k}`, 1 + Math.floor(k / 4), 1 + (k % 4), V2));
  const all = new Set(many.map((r) => r.ref));
  const picked = cr.defaultPairs(many, all, [['M0', 'M5']]);
  assert.ok(picked.length < 28, `${picked.length} of 28`);
  assert.ok(picked.includes('M0|M4'), 'the panes’ first layers');
  assert.ok(picked.includes('M0|M1') && picked.includes('M4|M7'), 'within each pane');
  assert.ok(picked.includes('M0|M5'), 'the Difference on screen');
});

test('what sets a rendering apart is what differs from its technique’s others', () => {
  const built = R.filter((r) => r.built);
  assert.deepEqual(cr.settingsApart(R[0], built), ['Face cap 400,000']);
  assert.deepEqual(cr.settingsApart(R[1], built), ['Face cap 100,000']);
  assert.deepEqual(cr.settingsApart(R[2], built), ['the technique’s defaults']);
  const fine = rendering('L9', 4, 1, { ...V2, voxel2_um: 0.05 });
  assert.deepEqual(cr.settingsApart(fine, [fine]), ['Fine voxel 0.05']);
  const many = rendering('L8', 4, 2, { ...V2, voxel2_um: 0.05, voxel2_faces: 0, voxel_smooth_um: 1, tie_um: 5 });
  const apart = cr.settingsApart(many, [many]);
  assert.equal(apart.length, 4);
  assert.match(apart.at(-1), /^\+1 more$/);
  const uncapped = rendering('L7', 4, 3, { ...V2, voxel2_faces: 0 });
  assert.deepEqual(cr.settingsApart(uncapped, [uncapped]), ['Face cap no cap']);
});

test('the page count is the server’s plan', () => {
  // compare_pdf.Plan for the c943 report of 2026-10-20: 4 renderings in 4 panes (one a
  // Difference), 4 pairs, 4 against the annotations, the addendum: 40 pages.
  assert.equal(cr.estimatePages({ n: 4, pairs: 4, deviations: 4, panes: 4, addendum: true }), 40);
  // 3 renderings, 3 pairs, 3 against the annotations, 4 panes: the browser's report, 35 pages.
  assert.equal(cr.estimatePages({ n: 3, pairs: 3, deviations: 3, panes: 4, addendum: true }), 35);
  // The route's test: 2 renderings, 1 pair, 1 against the annotations, 3 panes, no addendum.
  assert.equal(cr.estimatePages({ n: 2, pairs: 1, deviations: 1, panes: 3, addendum: false }), 16);
});

test('the time grows with the pairs and the exact sign costs more', () => {
  const pairs = cr.pickPairs(R, chosen, 'all');
  const base = { renderings: R.slice(0, 4), deviations: ['L1'], views: 3, pages: 40 };
  const few = cr.estimateSeconds({ ...base, pairs: pairs.slice(0, 1) });
  const all = cr.estimateSeconds({ ...base, pairs });
  assert.ok(all > few);
  assert.ok(cr.estimateSeconds({ ...base, pairs, sign: 'normal' }) < all);
  assert.equal(cr.durationText(30), 'about 30 s');
  assert.equal(cr.durationText(150), 'about 3 min');
  assert.equal(cr.durationText(7200), 'about 2 h');
});

test('the cover’s time is the browser’s, with its zone', () => {
  const text = cr.createdText(new Date(2026, 9, 20, 15, 4));
  assert.match(text, /^2026-10-20 15:04( \S+)?$/);
});

test('the request numbers the chosen renderings in order and drops what was left out', () => {
  const sources = {
    renderings: R,
    panes: [{ number: 1, kind: 'layers', layers: 2 }, { number: 2, kind: 'layers', layers: 1 },
      { number: 3, kind: 'layers', layers: 2 }, { number: 4, kind: 'difference', layers: 1, a: 'L1', b: 'L3' }],
    camera: { direction: [0, 0, 1], up: [0, 1, 0], flip_z: true },
  };
  const body = cr.reportBody(sources, {
    chosen: new Set(['L1', 'L3', 'L4', 'L5']), deviations: new Set(['L3']),
    pairs: new Set(['L3|L4', 'L1|L3', 'L1|L2']), views: ['xz1', 'view'], sign: 'normal',
    addendum: false, page: 'a4', snapshots: new Map([[2, 'data:image/jpeg;base64,AA==']]), created: 'now',
  });
  assert.deepEqual(body.renderings.map((r) => r.pane * 10 + r.layer), [11, 21, 31], 'L2 not chosen, L5 not built');
  assert.deepEqual(body.renderings.map((r) => r.deviation), [false, true, false]);
  assert.deepEqual(body.pairs, [[0, 1], [1, 2]], 'L1|L2 names a rendering left out');
  assert.deepEqual(body.panes[3].difference, [0, 1]);
  assert.equal(body.panes[1].snapshot, 'data:image/jpeg;base64,AA==');
  assert.equal(body.panes[0].snapshot, undefined);
  assert.deepEqual(body.views, ['view', 'xz1'], 'in the viewer’s order');
  assert.deepEqual(body.camera, sources.camera);
  assert.equal(body.sign, 'normal');
  assert.equal(body.page, 'a4');
  assert.equal(body.addendum, false);
  // Without a camera "This view" cannot be drawn: it is left out, and a request is never viewless.
  const blind = cr.reportBody({ ...sources, camera: null }, {
    chosen: new Set(['L1']), deviations: new Set(), pairs: new Set(), views: ['view'],
  });
  assert.deepEqual(blind.views, ['xy1']);
  assert.equal(blind.camera, null);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n     ${error.message}`);
  }
}
console.log(`${cases.length - failed} of ${cases.length} passed`);
process.exit(failed ? 1 : 0);
