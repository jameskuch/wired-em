// The buffer builders under Renderer3D's layers (static/js/layerbuffers.js): which
// vertex of the annotation layer's one buffer takes which annotation's color, how
// a color list written for the caller's own array is matched to the rings that
// survive filtering, and which line segments are fit to draw.  Run with
//
//     node tests/js/test_layerbuffers.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const { RING_SEGMENTS, discColorBuffer, resolveColors, segmentBuffers } = await import(
  pathToFileURL(path.join(jsDir, 'layerbuffers.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** Three numbers of vertex `v` of a color buffer. */
const rgbAt = (buffer, v) => Array.from(buffer.slice(v * 3, v * 3 + 3));
const toRgb = (value) => (typeof value === 'string' ? { red: [1, 0, 0], blue: [0, 0, 1] }[value] : value);

// -- resolveColors ---------------------------------------------------------------------------------

test('resolveColors: no spec is no colors, not a list of nulls', () => {
  assert.equal(resolveColors(null, [{}, {}], (x) => x.id, toRgb), null);
  assert.equal(resolveColors(undefined, [{}], (x) => x.id, toRgb), null);
});

test('resolveColors: an array is read by position, a Map by id, a function by both', () => {
  const items = [{ id: 7 }, { id: 8 }, { id: 9 }];
  const idOf = (x) => x.id;
  assert.deepEqual(resolveColors(['red', null, 'blue'], items, idOf, toRgb),
    [[1, 0, 0], null, [0, 0, 1]]);
  assert.deepEqual(resolveColors(new Map([[8, 'blue']]), items, idOf, toRgb),
    [null, [0, 0, 1], null]);
  assert.deepEqual(resolveColors((item, at) => (at === 2 ? [0.1, 0.2, 0.3] : null), items, idOf, toRgb),
    [null, null, [0.1, 0.2, 0.3]]);
});

test('resolveColors: positions keep an array parallel to the caller\'s list when it was filtered', () => {
  // The caller wrote colors for four nodes; only nodes 1 and 3 are rings.
  const caller = ['red', 'blue', 'red', 'blue'];
  const rings = [{ id: 11 }, { id: 13 }];
  assert.deepEqual(resolveColors(caller, rings, (x) => x.id, toRgb, [1, 3]),
    [[0, 0, 1], [0, 0, 1]], 'position 1 and position 3, not 0 and 1');
  const seen = [];
  resolveColors((item, at) => { seen.push(at); return null; }, rings, (x) => x.id, toRgb, [1, 3]);
  assert.deepEqual(seen, [1, 3], 'a function is told the caller\'s position too');
});

test('resolveColors: a bad entry is no color, not a blank layer', () => {
  const items = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }];
  const throwing = (value) => { if (value === 'boom') throw new Error('bad'); return toRgb(value); };
  const out = resolveColors(['boom', [NaN, 0, 0], [1, 2], 'red'], items, (x) => x.id, throwing);
  assert.deepEqual(out, [null, null, null, [1, 0, 0]]);
});

// -- discColorBuffer -------------------------------------------------------------------------------

test('discColorBuffer: every vertex of a ring takes the ring\'s color, in buffer order', () => {
  const perSample = 2;
  const buffer = discColorBuffer({
    ringCount: 2, ringRgb: [[1, 0, 0], [0, 1, 0]], segments: RING_SEGMENTS, perSample,
    fallback: [0.5, 0.5, 0.5],
  });
  const perRing = RING_SEGMENTS * perSample;
  assert.equal(buffer.length, 2 * perRing * 3);
  assert.deepEqual(rgbAt(buffer, 0), [1, 0, 0]);
  assert.deepEqual(rgbAt(buffer, perRing - 1), [1, 0, 0]);
  assert.deepEqual(rgbAt(buffer, perRing), [0, 1, 0]);
  assert.deepEqual(rgbAt(buffer, 2 * perRing - 1), [0, 1, 0]);
});

test('discColorBuffer: rings come first, then each outline polygon with its own sample count', () => {
  const buffer = discColorBuffer({
    ringCount: 1, ringRgb: [[1, 0, 0]], polygonSamples: [5, 3], polygonRgb: [[0, 0, 1], null],
    segments: RING_SEGMENTS, perSample: 4, fallback: [0.25, 0.5, 0.75],
  });
  const ring = RING_SEGMENTS * 4;
  assert.equal(buffer.length, (ring + 5 * 4 + 3 * 4) * 3);
  assert.deepEqual(rgbAt(buffer, ring - 1), [1, 0, 0]);
  assert.deepEqual(rgbAt(buffer, ring), [0, 0, 1], 'first polygon');
  assert.deepEqual(rgbAt(buffer, ring + 5 * 4 - 1), [0, 0, 1]);
  assert.deepEqual(rgbAt(buffer, ring + 5 * 4), [0.25, 0.5, 0.75], 'second polygon has none: the fallback');
});

test('discColorBuffer: rings without colors still fill their share (outline colors alone)', () => {
  const buffer = discColorBuffer({
    ringCount: 3, ringRgb: null, polygonSamples: [2], polygonRgb: [[0, 1, 0]],
    segments: RING_SEGMENTS, perSample: 2, fallback: [0.1, 0.2, 0.3],
  });
  const rings = 3 * RING_SEGMENTS * 2;
  assert.equal(buffer.length, (rings + 2 * 2) * 3, 'the polygon is not shifted onto the rings\' vertices');
  assert.deepEqual(rgbAt(buffer, 0), [0.1, 0.2, 0.3].map(Math.fround));
  assert.deepEqual(rgbAt(buffer, rings), [0, 1, 0]);
});

test('discColorBuffer: matches the vertex count setDiscs lays out, flat or deep', () => {
  // setDiscs: (rings * SEG + band samples) * PER vertices, PER 2 flat and 4 deep.
  for (const perSample of [2, 4]) {
    const buffer = discColorBuffer({
      ringCount: 5, ringRgb: null, polygonSamples: [10, 7], perSample, fallback: [1, 1, 1],
    });
    assert.equal(buffer.length / 3, (5 * RING_SEGMENTS + 17) * perSample);
  }
  assert.equal(discColorBuffer({}).length, 0);
});

// -- segmentBuffers --------------------------------------------------------------------------------

test('segmentBuffers: six numbers a segment, a trailing partial segment is ignored', () => {
  const out = segmentBuffers([0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 9, 9]);
  assert.equal(out.count, 2);
  assert.equal(out.colors, null);
  assert.deepEqual(Array.from(out.positions), [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3]);
  assert.ok(out.positions instanceof Float32Array);
});

test('segmentBuffers: a segment with a non-finite coordinate is dropped whole, with its colors', () => {
  const segments = [0, 0, 0, 1, 0, 0, NaN, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, Infinity];
  const colors = [1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1];
  const out = segmentBuffers(segments, { colors });
  assert.equal(out.count, 1);
  assert.deepEqual(Array.from(out.positions), [0, 0, 0, 1, 0, 0]);
  assert.deepEqual(Array.from(out.colors), [1, 0, 0, 1, 0, 0]);
});

test('segmentBuffers: a color per end is kept, a color per segment is written to both ends', () => {
  const segments = [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0];
  const perEnd = segmentBuffers(segments, { colors: [1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1, 1] });
  assert.deepEqual(Array.from(perEnd.colors), [1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1, 1]);
  const perSegment = segmentBuffers(segments, { colors: [1, 0, 0, 0, 0, 1] });
  assert.deepEqual(Array.from(perSegment.colors), [1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1]);
});

test('segmentBuffers: colors of any other length are ignored, never sheared against the lines', () => {
  const segments = [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0];
  for (const colors of [[1, 0, 0], [1, 0, 0, 0, 1, 0, 0, 0], new Array(13).fill(0.5)]) {
    const out = segmentBuffers(segments, { colors });
    assert.equal(out.count, 2);
    assert.equal(out.colors, null, `length ${colors.length}`);
  }
  // A non-finite color drops its segment rather than painting it black.
  const out = segmentBuffers(segments, { colors: [1, 0, 0, 0, 0, 1].map((v, i) => (i === 4 ? NaN : v)) });
  assert.equal(out.count, 1);
  assert.deepEqual(Array.from(out.positions), [0, 0, 0, 1, 0, 0]);
});

test('segmentBuffers: nothing in, nothing out', () => {
  for (const input of [[], null, undefined, [1, 2, 3, 4, 5]]) {
    const out = segmentBuffers(input);
    assert.equal(out.count, 0);
    assert.equal(out.positions.length, 0);
  }
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
