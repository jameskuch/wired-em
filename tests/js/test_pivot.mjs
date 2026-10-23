// The point a turn of the 3D view pivots about (static/js/pivot.js).  Run with
//
//     node tests/js/test_pivot.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  ENDED, PIVOT_ZOOM_OUT, SNAP_PICTURE, SNAP_REACH_PX, STALE_DEPTH, pivotAtPointer, pivotEnds, pivotPlace, staleDepth,
} = await import(pathToFileURL(path.join(jsDir, 'pivot.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// -- pivotEnds -------------------------------------------------------------------------
const held = { drawn: true, ndc: [0.2, -0.3], inFront: true, umPerPixel: 0.01, umPerPixelAtSet: 0.01 };

test('a pivot in the picture, at the scale it was set at, holds', () => {
  assert.equal(pivotEnds(held), null);
});

test('zooming in never lets go', () => {
  assert.equal(pivotEnds({ ...held, umPerPixel: 0.0001 }), null);
});

test('zoomed out to 4x holds, past it lets go', () => {
  assert.equal(PIVOT_ZOOM_OUT, 4);
  assert.equal(pivotEnds({ ...held, umPerPixel: 0.04 }), null);
  assert.equal(pivotEnds({ ...held, umPerPixel: 0.0401 }), 'zoomed');
});

test('at the edge of the picture holds; past any edge, or behind the camera, lets go', () => {
  assert.equal(pivotEnds({ ...held, ndc: [1, -1] }), null);
  for (const ndc of [[1.001, 0], [-1.2, 0], [0, 1.01], [0, -3]]) assert.equal(pivotEnds({ ...held, ndc }), 'left');
  assert.equal(pivotEnds({ ...held, inFront: false }), 'left');
  assert.equal(pivotEnds({ ...held, ndc: [NaN, 0] }), 'left');
});

test('its cell no longer drawn lets go first', () => {
  assert.equal(pivotEnds({ ...held, drawn: false, ndc: [5, 5] }), 'hidden');
});

test('every reason the pivot lets go by itself has words for the status line', () => {
  for (const reason of ['hidden', 'left', 'zoomed', 'reset', 'flight', 'flatten']) {
    assert.ok(ENDED[reason], reason);
  }
  assert.ok(ENDED.zoomed.includes('4×'));
});

// -- pivotPlace ------------------------------------------------------------------------
const neurite = { point: [10, 20, 30], key: 'NeitzInferiorMonkey/943' };

test('on a process, the middle of the annotation there (its own axis)', () => {
  const pointed = { point: [10.2, 20, 30], key: neurite.key };
  const nearest = { ...neurite, px: 0, hit: true };
  assert.deepEqual(pivotPlace({ pointed, nearest, pictureUm: 3 }), { ...neurite, snapped: true });
});

test('close up on a soma, the point pointed at, not the middle of its annotation', () => {
  const pointed = { point: [14, 20, 30], key: neurite.key };       // 4 um from the middle
  const nearest = { ...neurite, px: 0, hit: true };
  // A quarter of a 12 um picture is 3 um: the middle is too far.
  assert.deepEqual(pivotPlace({ pointed, nearest, pictureUm: 12 }), { ...pointed, snapped: false });
  // A quarter of a 20 um picture is 5 um: near enough.
  assert.deepEqual(pivotPlace({ pointed, nearest, pictureUm: 20 }), { ...neurite, snapped: true });
  assert.equal(SNAP_PICTURE, 0.25);
});

test('an annotation of another cell than the one pointed at does not take the pivot', () => {
  const pointed = { point: [10.1, 20, 30], key: 'NeitzInferiorMonkey/21768' };
  const nearest = { ...neurite, px: 0, hit: true };
  assert.deepEqual(pivotPlace({ pointed, nearest, pictureUm: 3 }), { ...pointed, snapped: false });
});

test('a cell with no annotations known yet: the point pointed at', () => {
  const pointed = { point: [1, 2, 3], key: neurite.key };
  assert.deepEqual(pivotPlace({ pointed, nearest: null, pictureUm: 3 }), { ...pointed, snapped: false });
});

test('on empty space beside a thin process, the nearest annotation within reach', () => {
  assert.deepEqual(pivotPlace({ pointed: null, nearest: { ...neurite, px: SNAP_REACH_PX, hit: false } }),
    { ...neurite, snapped: true });
  assert.equal(pivotPlace({ pointed: null, nearest: { ...neurite, px: SNAP_REACH_PX + 1, hit: false } }), null);
});

test('nothing near: nowhere to pivot', () => {
  assert.equal(pivotPlace({}), null);
});

// -- pivotAtPointer --------------------------------------------------------------------
/** A view that answers what a Renderer3D would: `hit` under the pointer, `found` the readout's annotation. */
const fakeView = ({ hit = null, found = null, umPerPixel = 0.01, offset = [0, 0, 0] } = {}) => ({
  asked: [],
  pickAt(x, y) { this.asked.push(['pickAt', x, y]); return hit && { key: hit.key, world: { toArray: () => hit.point } }; },
  nearestAnnotation(x, y, cells) { this.asked.push(['nearest', x, y, cells.map((c) => c.key)]); return found; },
  toWorld(p, source) { this.asked.push(['toWorld', source]); return p.map((v, i) => v + offset[i]); },
  umPerPixelAt() { return umPerPixel; },
});

test('the readout\'s annotation on the surface pointed at: its middle, in the drawn frame', () => {
  const view = fakeView({
    hit: { key: 'L3', point: [10.2, 20, 30] },
    found: { key: 'L3', node: { xyz_um: [10, 20, 30] }, px: 0, hit: true },
    offset: [0, 0, 0],
  });
  const cells = [{ key: 'L3', nodes: [{}], source: null }, { key: 'A', nodes: [{}], source: null }];
  assert.deepEqual(pivotAtPointer(view, 5, 6, cells, 300), { point: [10, 20, 30], key: 'L3', snapped: true });
  assert.deepEqual(view.asked.find((a) => a[0] === 'nearest'), ['nearest', 5, 6, ['L3', 'A']]);
  assert.deepEqual(view.asked.find((a) => a[0] === 'toWorld'), ['toWorld', null]);
});

test('a cell of a volume: its annotation goes through that volume\'s frame', () => {
  const key = 'NeitzInferiorMonkey/943';
  const view = fakeView({
    hit: { key, point: [11, 20, 30] }, found: { key, node: { xyz_um: [1, 20, 30] }, px: 0, hit: true }, offset: [10, 0, 0],
  });
  const place = pivotAtPointer(view, 0, 0, [{ key, nodes: [{}], source: 'NeitzInferiorMonkey' }], 300);
  assert.deepEqual(place, { point: [11, 20, 30], key, snapped: true });
  assert.deepEqual(view.asked.find((a) => a[0] === 'toWorld'), ['toWorld', 'NeitzInferiorMonkey']);
});

test('the picture\'s height sets how far the middle may be (a quarter of it)', () => {
  // 4 um from the middle; 0.01 um a pixel: 300 px is 3 um of picture (too far), 2,000 px 20 um.
  const view = fakeView({
    hit: { key: 'L1', point: [14, 20, 30] }, found: { key: 'L1', node: { xyz_um: [10, 20, 30] }, px: 0, hit: true },
  });
  const cells = [{ key: 'L1', nodes: [{}] }];
  assert.equal(pivotAtPointer(view, 0, 0, cells, 300).snapped, false);
  assert.equal(pivotAtPointer(view, 0, 0, cells, 2000).snapped, true);
});

test('no annotations known: what is drawn there, and the readout is not asked', () => {
  const view = fakeView({ hit: { key: 'L1', point: [1, 2, 3] } });
  assert.deepEqual(pivotAtPointer(view, 0, 0, [], 300), { point: [1, 2, 3], key: 'L1', snapped: false });
  assert.equal(view.asked.some((a) => a[0] === 'nearest'), false);
  assert.equal(pivotAtPointer(fakeView(), 0, 0, [], 300), null);
});

// -- staleDepth ------------------------------------------------------------------------
test('the measured case: the pivot 23 um behind a process 5 um away is stale', () => {
  // c943, 2026-10-22: half the picture's height at 4.6 um is 4.6 * tan(22.5 deg) = 1.9 um.
  assert.equal(staleDepth({ pivotDepth: 28.2, contentDepth: 4.6, halfHeightUm: 1.9 }), true);
});

test('a whole cell framed by Reset view is not (its turns stay as they were)', () => {
  // c1178: the target at 472.8 um, the content's median 523.2 um, half the picture there 216.7 um.
  assert.equal(staleDepth({ pivotDepth: 472.8, contentDepth: 523.2, halfHeightUm: 216.7 }), false);
  // c943: 29.64 against 29.15, half the picture 12.1 um.
  assert.equal(staleDepth({ pivotDepth: 29.64, contentDepth: 29.15, halfHeightUm: 12.1 }), false);
});

test('the line is half the picture\'s half height, either side', () => {
  assert.equal(STALE_DEPTH, 0.5);
  assert.equal(staleDepth({ pivotDepth: 10.5, contentDepth: 10, halfHeightUm: 1 }), false);
  assert.equal(staleDepth({ pivotDepth: 10.51, contentDepth: 10, halfHeightUm: 1 }), true);
  assert.equal(staleDepth({ pivotDepth: 9.49, contentDepth: 10, halfHeightUm: 1 }), true);
});

test('Orthographic depths may be negative (behind the camera\'s plane)', () => {
  assert.equal(staleDepth({ pivotDepth: 5, contentDepth: -5, halfHeightUm: 2 }), true);
  assert.equal(staleDepth({ pivotDepth: -4.5, contentDepth: -5, halfHeightUm: 2 }), false);
});

test('nothing measured, or a view with no size, is never stale', () => {
  assert.equal(staleDepth({ pivotDepth: 5, contentDepth: NaN, halfHeightUm: 1 }), false);
  assert.equal(staleDepth({ pivotDepth: 5, contentDepth: 50, halfHeightUm: 0 }), false);
});

// -- run ----------------------------------------------------------------------------
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
