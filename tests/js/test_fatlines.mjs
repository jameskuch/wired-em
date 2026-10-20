// The Position shift layer's wide lines (static/js/fatlines.js): the buffers the
// vertex shader spreads across the screen, the draw ranges that map a slice's
// window onto them, and the corner arithmetic the shader does, written out in
// JavaScript.  Run with
//
//     node tests/js/test_fatlines.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const F = await load('fatlines.js');
const S = await load('shifts.js');
const L = await load('shiftlayer.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, tol = 1e-9, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} vs ${b}`);
const triple = (a, i) => [a[i * 3], a[i * 3 + 1], a[i * 3 + 2]];

/** Two arrows in buildArrows' layout: tail, head, then two barbs; one color each. */
const positions = Float32Array.from([
  0, 0, 1, 10, 0, 1, 10, 0, 1, 8, 1, 1, 10, 0, 1, 8, -1, 1,
  5, 5, 2, 5, 9, 2, 5, 9, 2, 4, 8, 2, 5, 9, 2, 6, 8, 2,
]);
const colors = Float32Array.from([
  ...Array(6).fill([0.1, 0.2, 0.3]).flat(),
  ...Array(6).fill([0.7, 0.8, 0.9]).flat(),
]);

// -- the buffers ---------------------------------------------------------------------------------

test('an arrow is nine vertices: its own end of the shaft, the other end, a corner and a color each', () => {
  const fat = F.fatArrows(positions, colors);
  assert.equal(fat.n, 2);
  assert.equal(fat.vertsPerMark, F.ARROW_FAT_VERTS);
  assert.equal(fat.position.length, 2 * 9 * 3);
  assert.equal(fat.other.length, fat.position.length);
  assert.equal(fat.corner.length, fat.position.length);
  assert.ok(fat.corner instanceof Int8Array, 'the corners are small integers');
  for (let v = 0; v < 9; v += 1) {
    const [along, side, kind] = triple(fat.corner, v);
    const atTail = along < 0.5;
    assert.deepEqual(triple(fat.position, v), atTail ? [0, 0, 1] : [10, 0, 1], `corner ${v}: its own end`);
    assert.deepEqual(triple(fat.other, v), atTail ? [10, 0, 1] : [0, 0, 1], `corner ${v}: the other end`);
    assert.ok([-1, 0, 1].includes(side));
    assert.equal(kind, v < 6 ? F.KIND.shaft : F.KIND.head, `corner ${v} is ${v < 6 ? 'shaft' : 'head'}`);
  }
  // The head: two base corners and the tip on the head point.
  assert.deepEqual([6, 7, 8].map((v) => triple(fat.corner, v)), [[1, -1, 2], [2, 0, 2], [1, 1, 2]]);
  // The second arrow keeps its own ends and color.
  assert.deepEqual(triple(fat.position, 9), [5, 5, 2]);
  assert.deepEqual(triple(fat.other, 9), [5, 9, 2]);
  for (let v = 0; v < 9; v += 1) close(fat.color[(9 + v) * 3], 0.7, 1e-6);
  // Two triangles of the shaft and the head's one cover the six shaft corners and three head corners.
  const shaftSides = [0, 1, 2, 3, 4, 5].map((v) => fat.corner[v * 3 + 1]);
  assert.deepEqual(shaftSides.sort(), [-1, -1, -1, 1, 1, 1]);
});

test('a segment is six vertices, as a ring\'s sides are drawn', () => {
  const ring = Float32Array.from([0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
  const fat = F.fatSegments(ring, Float32Array.from([1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0]));
  assert.equal(fat.n, 2);
  assert.equal(fat.position.length, 2 * 6 * 3);
  for (let v = 0; v < 6; v += 1) {
    const [along, , kind] = triple(fat.corner, v);
    assert.equal(kind, F.KIND.segment);
    assert.deepEqual(triple(fat.position, v), along < 0.5 ? [0, 0, 0] : [1, 0, 0]);
  }
  assert.deepEqual(Array.from(triple(fat.color, 6)), [0, 1, 0], 'each segment its own colors');
  assert.equal(F.fatSegments(ring).color, null, 'no colors in, none out');
});

test('recolor: fatColors gives what the builders gave, for both roles', () => {
  const fat = F.fatArrows(positions, colors);
  assert.deepEqual(Array.from(F.fatColors(colors, 'arrows')), Array.from(fat.color));
  const ring = Float32Array.from([0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
  const ringColors = Float32Array.from([1, 0, 0, 0.5, 0, 0, 0, 1, 0, 0, 0.5, 0]);
  assert.deepEqual(Array.from(F.fatColors(ringColors, 'rings')), Array.from(F.fatSegments(ring, ringColors).color));
});

// -- the draw ranges --------------------------------------------------------------------------------

test('a slice\'s window in line vertices is whole wide arrows and whole wide ring sides', () => {
  assert.equal(F.fatRange(null, 'arrows'), null);
  assert.deepEqual(F.fatRange({ first: 12, count: 18 }, 'arrows'), { first: 18, count: 27 });
  assert.deepEqual(F.fatRange({ first: 32, count: 64 }, 'rings'), { first: 96, count: 192 });
  assert.deepEqual(F.fatRange({ first: 0, count: 0 }, 'arrows'), { first: 0, count: 0 });
});

test('with a real layer: every window maps onto the wide buffers exactly', () => {
  const node = (id, section, x, shift) => ({
    id, parent_id: 1, z_section: section, radius_um: 1, type_code: 1, xyz_um: [x, 0, section * 0.07],
    shift_um: shift, live_shift_um: null,
  });
  const nodes = Array.from({ length: 30 }, (_, i) => node(i + 1, 100 + (i % 10), i * 3, [0.3 + 0.01 * i, 0.1]));
  const summary = { section_correction: { requested: true, applied: true, n_moved: 30 } };
  const layer = L.buildLayer({
    nodes, summary, display: { shiftSlice: true, shiftWindow: 1, shiftRings: true, shiftMinSlice: 0 },
  });
  const fatArrows = F.fatArrows(layer.arrows.positions, layer.arrows.colors);
  const fatRings = F.fatSegments(layer.rings.positions, layer.rings.colors);
  for (const center of [99, 100, 104, 109, 110]) {
    const { ranges, shown } = layer.windowAt(center);
    const a = F.fatRange(ranges.arrows, 'arrows');
    assert.equal(a.count, shown * F.ARROW_FAT_VERTS, `center ${center}: ${shown} arrows`);
    assert.equal(a.first % F.ARROW_FAT_VERTS, 0);
    assert.ok(a.first + a.count <= fatArrows.position.length / 3);
    const r = F.fatRange(ranges.rings, 'rings');
    assert.equal(r.count % F.SEGMENT_FAT_VERTS, 0);
    assert.ok(r.first + r.count <= fatRings.position.length / 3);
    // The window's first arrow is the one the line buffer's window starts at.
    if (shown) {
      assert.deepEqual(triple(fatArrows.position, a.first), triple(layer.arrows.positions, ranges.arrows.first));
    }
  }
});

// -- what the shader does ----------------------------------------------------------------------------

const opts = { lineWidth: 4, headLength: 11, headWidth: 9 };

test('a shaft corner is half the width across the line; the shaft stops where the head begins', () => {
  const tail = [0, 0];
  const head = [100, 0];
  for (const [corner, want] of [
    [[0, -1, 1], [0, -2]], [[0, 1, 1], [0, 2]], [[1, -1, 1], [-11, -2]], [[1, 1, 1], [-11, 2]],
  ]) {
    const { offset } = F.cornerOffset(tail, head, corner, opts);
    close(offset[0], want[0], 1e-9, `${corner} x`);
    close(offset[1], want[1], 1e-9, `${corner} y`);
  }
  // Fainter at the tail, full from the shaft's end on.
  assert.equal(F.cornerOffset(tail, head, [0, 1, 1], opts).fade, 0);
  assert.equal(F.cornerOffset(tail, head, [1, 1, 1], opts).fade, 1);
});

test('the head\'s tip is on the head point and its base is the head\'s width across, behind it', () => {
  const tail = [0, 0];
  const head = [0, 100];
  assert.deepEqual(F.cornerOffset(tail, head, [2, 0, 2], opts).offset, [0, 0]);
  const left = F.cornerOffset(tail, head, [1, -1, 2], opts).offset;
  const right = F.cornerOffset(tail, head, [1, 1, 2], opts).offset;
  close(left[1], -11, 1e-9); close(right[1], -11, 1e-9);
  close(Math.abs(left[0] - right[0]), 9, 1e-9, 'base width');
  assert.equal(F.cornerOffset(tail, head, [1, 1, 2], opts).fade, 1);
});

test('a plain segment has no head: its end corners are not pulled back', () => {
  const { offset } = F.cornerOffset([0, 0], [10, 0], [1, 1, 0], opts);
  close(offset[0], 0, 1e-9, 'not pulled back');
  close(offset[1], 2, 1e-9, 'half the width across');
});

test('a short arrow is never all head: the head shrinks with it, its width not below 40 %', () => {
  const short = F.cornerOffset([0, 0], [10, 0], [1, 1, 2], opts).offset;
  close(short[0], -6, 1e-9, 'at most 60 % of a 10 px arrow');
  close(short[1], 0.5 * 9 * (6 / 11), 1e-9, 'the width follows');
  const tiny = F.cornerOffset([0, 0], [1, 0], [1, 1, 2], opts).offset;
  close(tiny[1], 0.5 * 9 * F.HEAD_MIN_WIDTH_SHARE, 1e-9, 'never thinner than 40 %');
  // A zero-length arrow still has a direction and a size: nothing divides by zero.
  const none = F.cornerOffset([5, 5], [5, 5], [0, 1, 1], opts);
  assert.ok(none.offset.every(Number.isFinite));
});

test('the head grows with the width; the rings are drawn thinner, within 1 and 3 px', () => {
  const a = F.headSize(1); const b = F.headSize(2.5); const c = F.headSize(8);
  assert.ok(a.length < b.length && b.length < c.length);
  assert.ok(b.length > 2 * 2.5 && b.width > 2.5, 'a head is a clear wedge beside its shaft');
  assert.deepEqual(F.headSize(NaN), F.headSize(F.WIDTH_DEFAULT));
  assert.equal(F.ringWidth(1), 1);
  assert.equal(F.ringWidth(2.5), 1.5);
  assert.equal(F.ringWidth(8), 3);
});

test('the shaders read what the builders write, clip, and share the head rules', () => {
  for (const name of ['other', 'corner', 'lineColor']) assert.match(F.FAT_VERTEX_SHADER, new RegExp(`attribute vec3 ${name};`));
  assert.match(F.FAT_VERTEX_SHADER, /#include <clipping_planes_pars_vertex>/);
  assert.match(F.FAT_VERTEX_SHADER, /#include <clipping_planes_vertex>/);
  assert.match(F.FAT_VERTEX_SHADER, /vec4 mvPosition = /, 'the clipping chunk reads mvPosition');
  assert.match(F.FAT_FRAGMENT_SHADER, /#include <clipping_planes_fragment>/);
  assert.match(F.FAT_FRAGMENT_SHADER, /#include <colorspace_fragment>/);
  assert.ok(F.FAT_VERTEX_SHADER.includes(`${F.HEAD_SHARE.toFixed(3)} * len`), 'the head share is the one tested above');
  assert.ok(F.FAT_VERTEX_SHADER.includes(`${F.HEAD_MIN_WIDTH_SHARE.toFixed(3)})`));
  assert.doesNotMatch(F.FAT_VERTEX_SHADER, /\$\{/, 'no template left unfilled');
  // A perspective end behind the camera is pulled to the near plane, by the near plane three.js projects to.
  assert.match(F.FAT_VERTEX_SHADER, /float a = projectionMatrix\[2\]\[2\];\s*float b = projectionMatrix\[3\]\[2\];/);
});

test('the default width is the layer\'s, and building 15,000 arrows takes milliseconds', () => {
  assert.equal(S.SHIFT_DEFAULTS.shiftWidth, F.WIDTH_DEFAULT);
  const n = 15000;
  const big = new Float32Array(n * 18).map((_, i) => (i % 7) * 0.5);
  const t0 = performance.now();
  const fat = F.fatArrows(big, new Float32Array(n * 18).fill(0.5));
  const ms = performance.now() - t0;
  assert.equal(fat.n, n);
  console.log(`     15,000 arrows: ${ms.toFixed(1)} ms, ${((fat.position.byteLength * 3 + fat.corner.byteLength) / 1e6).toFixed(1)} MB`);
  assert.ok(ms < 500, `${ms} ms`);
});

test('the menu measures an arrow by its shaft: no barbs, which are not drawn, and its head\'s reach', () => {
  const field = S.prepareField([
    { id: 1, z_section: 1, xyz_um: [0, 0, 0.07], radius_um: 0.2, shift_um: [1, 0] },
    { id: 2, z_section: 2, xyz_um: [5, 0, 0.14], radius_um: 0.2, shift_um: [0, 1] },
  ], 'correction');
  const sel = S.selectMarks(field, { min: 0 });
  const arrows = S.buildArrows(field, sel, S.markScales(field, sel, 1), [[1, 1, 1]]);
  const shafts = F.shaftSegments(arrows.positions);
  assert.equal(shafts.length, 2 * 2 * 3, 'two vertices an arrow');
  for (let i = 0; i < 2; i += 1) {
    assert.deepEqual([...shafts.subarray(i * 6, i * 6 + 6)], [...arrows.positions.subarray(i * 18, i * 18 + 6)]);
  }
  assert.deepEqual(F.shaftRange({ first: 6, count: 6 }), { first: 2, count: 2 });
  assert.equal(F.shaftRange(null), null);
  assert.ok(F.arrowReach(8, 6) > 6, 'a wide head reaches past the shaft\'s reach');
  assert.equal(F.arrowReach(1, 6), 6);
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
