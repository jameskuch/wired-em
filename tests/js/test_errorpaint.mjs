// The error paint and the numbers behind it (static/js/errorpaint.js), checked
// without a browser.  Run with
//
//     node tests/js/test_errorpaint.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.
//
// Three things are pinned here:
//   * the error and signed ramps are byte for byte the functions Render compare has
//     always drawn with (a verbatim copy of them from compare.js, 2026-10-15, is kept in
//     this file, and the live ones are run as well while compare.js still has them);
//   * the area-weighted quantiles, statistics, histogram and vertex areas equal the
//     Python's (`PY` below holds numbers computed by sbfsem.geometry.meshdiff
//     weighted_quantiles, _area_stats, _surface_stats and vertex_areas on the same
//     inputs, so a client figure for a lateral or axial field reads like the server's);
//   * the new paints (lateral/axial, threshold, classes, rim filter) do what they say.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const P = await import(pathToFileURL(path.join(jsDir, 'errorpaint.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// Computed by the Python (see above); regenerate by running the same functions on these inputs.
const PY = {"wq":{"values":[0.3,0.0,1.2,0.7,0.7,2.5,0.1,0.9,0.45,1.8],"weights":[1.0,0.5,2.0,0.25,0.75,0.1,3.0,0.0,1.5,0.4],"probs":[0.0,0.1,0.2,0.30000000000000004,0.4,0.5,0.6000000000000001,0.7000000000000001,0.8,0.9,1.0],"expect":[0.0,0.04000000000000001,0.0942857142857143,0.18500000000000005,0.28,0.38999999999999996,0.5785714285714287,0.7090909090909094,1.054545454545455,1.475,2.5],"expect_equal":[0.0,0.05000000000000002,0.2,0.3750000000000001,0.5750000000000001,0.7,0.8000000000000002,1.0500000000000003,1.5000000000000004,2.1500000000000004,2.5],"expect_zero":[0.0,0.05000000000000002,0.2,0.3750000000000001,0.5750000000000001,0.7,0.8000000000000002,1.0500000000000003,1.5000000000000004,2.1500000000000004,2.5]},"area_stats":{"values":[0.3,-0.5,1.2,-0.7,0.0,2.5,-0.1,0.9,-0.45,1.8],"areas":[1.0,0.5,2.0,0.25,0.75,0.1,3.0,0.0,1.5,0.4],"expect":{"mean_abs_um":0.5336842105263158,"rms_um":0.758963975290971,"p95_abs_um":1.7124999999999995,"p98_abs_um":2.107999999999999,"max_abs_um":2.5,"mean_signed_um":0.23894736842105258}},"hist":{"values":[0.3,0.5,1.2,0.7,0.0,2.5,0.1,0.9,0.45,1.8],"areas":[1.0,0.5,2.0,0.25,0.75,0.1,3.0,0.0,1.5,0.4],"scale":1.2,"expect_edges":[0.0,0.045,0.09,0.135,0.18,0.22499999999999998,0.27,0.315,0.36,0.40499999999999997,0.44999999999999996,0.495,0.54,0.585,0.63,0.6749999999999999,0.72,0.765,0.8099999999999999,0.855,0.8999999999999999,0.945,0.99,1.035,1.08,1.125,1.17,1.2149999999999999,1.26,1.305,1.3499999999999999,1.395,1.44,1.4849999999999999,1.53,1.575,1.6199999999999999,1.665,1.71,1.755,1.7999999999999998],"expect_share":[0.07894736842105263,0.0,0.3157894736842105,0.0,0.0,0.0,0.10526315789473684,0.0,0.0,0.0,0.15789473684210525,0.05263157894736842,0.0,0.0,0.0,0.02631578947368421,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.21052631578947367,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0],"expect_tail":0.05263157894736842,"expect_quantiles":[0.0,0.0,0.0,0.0,0.0002666666666666682,0.005333333333333337,0.010400000000000001,0.015466666666666672,0.020533333333333337,0.0256,0.030666666666666672,0.03573333333333334,0.0408,0.045866666666666674,0.050933333333333344,0.056,0.06106666666666667,0.06613333333333335,0.0712,0.07626666666666666,0.08133333333333334,0.08640000000000002,0.09146666666666668,0.09653333333333336,0.10300000000000001,0.11250000000000002,0.12200000000000003,0.13150000000000003,0.14100000000000004,0.1505,0.16,0.16949999999999998,0.179,0.1885,0.198,0.20750000000000002,0.21699999999999997,0.2265,0.236,0.2455,0.255,0.2645,0.27399999999999997,0.2835,0.293,0.303,0.3144,0.32580000000000003,0.33719999999999994,0.34859999999999997,0.36,0.3714,0.38280000000000003,0.3942,0.4056,0.41700000000000004,0.4284,0.4398000000000001,0.45049999999999996,0.45525,0.45999999999999996,0.46475,0.4695,0.47425,0.479,0.48375,0.4885,0.49325,0.498,0.5293333333333335,0.5800000000000002,0.6306666666666663,0.681333333333333,0.7266666666666665,0.7688888888888887,0.811111111111111,0.8533333333333333,0.8955555555555555,0.9377777777777778,0.9800000000000002,1.0222222222222226,1.0644444444444447,1.106666666666667,1.1488888888888893,1.1911111111111112,1.2375,1.285,1.3325,1.38,1.4275,1.475,1.5225,1.5699999999999998,1.6175,1.665,1.7125,1.7599999999999993,1.8419999999999965,2.107999999999999,2.374000000000002,2.5]},"areas":{"vertices":[0.0,0.0,0.0,0.0,0.5,0.0,0.0,1.0,0.0,0.5,0.0,0.0,0.5,0.5,0.025,0.5,1.0,0.05,1.0,0.0,0.0,1.0,0.5,0.05,1.0,1.0,0.1,1.0,0.0,0.0,1.0,0.25,0.025,1.0,0.5,0.05,1.0,0.75,0.07500000000000001,1.0,1.0,0.1,1.25,0.0,0.0,1.25,0.25,0.03125,1.25,0.5,0.0625,1.25,0.75,0.09375,1.25,1.0,0.125,1.5,0.0,0.0,1.5,0.25,0.037500000000000006,1.5,0.5,0.07500000000000001,1.5,0.75,0.11250000000000002,1.5,1.0,0.15000000000000002,1.75,0.0,0.0,1.75,0.25,0.043750000000000004,1.75,0.5,0.08750000000000001,1.75,0.75,0.13125,1.75,1.0,0.17500000000000002,2.0,0.0,0.0,2.0,0.25,0.05,2.0,0.5,0.1,2.0,0.75,0.15000000000000002,2.0,1.0,0.2,9.0,9.0,9.0],"faces":[0,3,4,0,4,1,1,4,5,1,5,2,3,6,7,3,7,4,4,7,8,4,8,5,9,14,15,9,15,10,10,15,16,10,16,11,11,16,17,11,17,12,12,17,18,12,18,13,14,19,20,14,20,15,15,20,21,15,21,16,16,21,22,16,22,17,17,22,23,17,23,18,19,24,25,19,25,20,20,25,26,20,26,21,21,26,27,21,27,22,22,27,28,22,28,23,24,29,30,24,30,25,25,30,31,25,31,26,26,31,32,26,32,27,27,32,33,27,33,28],"expect":[0.08343743497708661,0.1253639026927208,0.04187448175467037,0.1253639026927208,0.250831391032343,0.12557145978229856,0.04187448175467037,0.12557145978229856,0.08385254915624211,0.02096959043418202,0.03145438865314436,0.031489943874403674,0.03154481103066576,0.010520317644127164,0.03153189476070782,0.06301857725817492,0.063076650393281,0.06317332041425769,0.03159636557742759,0.03164459231486851,0.06323125154219111,0.06328912968662345,0.06338547591066392,0.03169600537713745,0.03177610133865,0.06348167602340102,0.06353932623519959,0.06363529394961184,0.03181452097265993,0.01062295731998497,0.031827281160970944,0.031849653630219486,0.03189115222103231,0.021277795364229544,0.0],"surface":2.0170791367128675}};

// -- the old functions, copied verbatim from compare.js (2026-10-15) ------------------------
// These are the golden copy: the ramps must stay byte for byte what Render compare has always
// drawn, whatever happens to compare.js.  While compare.js still defines them the test also
// runs the live ones, so a change there is noticed too.
const OLD_SOURCE = String.raw`
function divergingColors(values, scale) {
  const colors = new Float32Array(values.length * 3);
  const neg = [0.23, 0.45, 0.92];   // A inside B
  const pos = [0.86, 0.22, 0.22];   // A outside B
  for (let i = 0; i < values.length; i += 1) {
    let t = values[i] / scale;
    if (t > 1) t = 1; else if (t < -1) t = -1;
    const end = t < 0 ? neg : pos;
    const w = Math.abs(t);
    colors[3 * i] = 1 + (end[0] - 1) * w;
    colors[3 * i + 1] = 1 + (end[1] - 1) * w;
    colors[3 * i + 2] = 1 + (end[2] - 1) * w;
  }
  return colors;
}
const ERROR_STOPS = [[0.85, 0.87, 0.90], [1.0, 0.69, 0.0], [0.85, 0.12, 0.23]];
function errorColors(values, scale) {
  const colors = new Float32Array(values.length * 3);
  for (let i = 0; i < values.length; i += 1) {
    const t = Math.min(1, Math.max(0, values[i] / scale)) * 2;
    const [from, to] = t < 1 ? [ERROR_STOPS[0], ERROR_STOPS[1]] : [ERROR_STOPS[1], ERROR_STOPS[2]];
    const w = t < 1 ? t : t - 1;
    for (let c = 0; c < 3; c += 1) colors[3 * i + c] = from[c] + (to[c] - from[c]) * w;
  }
  return colors;
}
`;
const OLD_GRADIENT = 'linear-gradient(to right, rgb(217,222,230), rgb(255,176,0), rgb(217,31,59))';
const golden = new Function(`${OLD_SOURCE}; return { divergingColors, errorColors };`)();

/** compare.js's own functions, extracted from its source, if it still defines them. */
function liveFromCompare() {
  const source = fs.readFileSync(path.join(jsDir, 'compare.js'), 'utf8');
  const start = source.indexOf('function divergingColors(values, scale) {');
  const end = source.indexOf('function errorColors(values, scale) {');
  if (start < 0 || end < 0) return null;
  const close = source.indexOf('\n}\n', end);
  const chunk = source.slice(start, close + 3);
  return new Function(`${chunk}; return { divergingColors, errorColors, ERROR_GRADIENT };`)();
}

const sameBytes = (a, b) => Buffer.from(a.buffer, a.byteOffset, a.byteLength)
  .equals(Buffer.from(b.buffer, b.byteOffset, b.byteLength));

/** A fixed pseudo-random sequence (no Math.random: a failure must reproduce). */
function lcg(seed) {
  let s = seed >>> 0;
  return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
}

/** Distances around a scale: exact stops, zero, negatives, the far tail, and a spread of random ones. */
function probe(scale) {
  const random = lcg(7);
  const values = [0, scale, 2 * scale, -scale, 0.5 * scale, 0.25 * scale, 0.75 * scale, 1e-12, 1e6, -1e-9,
    scale / 3, scale * (1 - 1e-15), scale * (1 + 1e-15), Infinity, -Infinity];
  for (let i = 0; i < 4000; i += 1) values.push((random() * 3 - 1) * scale);
  return values;
}
const SCALES = [0.01, 0.0974, 0.35, 1, 3.3, 120];

// -- the ramps are the old ramps --------------------------------------------------------------

test('error ramp: byte for byte the old compare.js function, at every scale and value', () => {
  for (const scale of SCALES) {
    const values = probe(scale);
    assert.ok(sameBytes(P.errorColors(values, scale), golden.errorColors(values, scale)), `scale ${scale}`);
    // Typed input, as the browser holds it, is the same.
    const typed = Float32Array.from(values.map((v) => (Number.isFinite(v) ? v : 0)));
    assert.ok(sameBytes(P.errorColors(typed, scale), golden.errorColors(typed, scale)));
  }
});

test('diverging ramp: byte for byte the old compare.js function, at every scale and value', () => {
  for (const scale of SCALES) {
    const values = probe(scale);
    assert.ok(sameBytes(P.divergingColors(values, scale), golden.divergingColors(values, scale)), `scale ${scale}`);
  }
});

test('a value that is not a number paints as the old function painted it (not a number)', () => {
  const mine = P.errorColors([NaN, 0.1], 1);
  const old = golden.errorColors([NaN, 0.1], 1);
  assert.ok([0, 1, 2].every((c) => Number.isNaN(mine[c]) && Number.isNaN(old[c])));
  assert.ok(sameBytes(mine.slice(3), old.slice(3)));
});

test('the ramps\' stops and the legend gradient are the old ones', () => {
  assert.deepEqual(P.ERROR_STOPS, [[0.85, 0.87, 0.90], [1.0, 0.69, 0.0], [0.85, 0.12, 0.23]]);
  assert.equal(P.ERROR_GRADIENT, OLD_GRADIENT);
  assert.deepEqual(P.DIVERGING_NEGATIVE, [0.23, 0.45, 0.92]);
  assert.deepEqual(P.DIVERGING_POSITIVE, [0.86, 0.22, 0.22]);
  // A zero distance is the first stop, the scale the last, half way the middle one.
  assert.deepEqual(P.errorColorAt(0, 2), P.ERROR_STOPS[0].map((v) => Math.fround(v)));
  assert.deepEqual(P.errorColorAt(1, 2).map((v) => Math.fround(v)), P.ERROR_STOPS[1].map((v) => Math.fround(v)));
  assert.deepEqual(P.errorColorAt(9, 2).map((v) => Math.fround(v)), P.ERROR_STOPS[2].map((v) => Math.fround(v)));
  assert.deepEqual(P.divergingColorAt(0, 1), [1, 1, 1]);
});

test('compare.js\'s own functions, while it still has them, paint what these paint', () => {
  const live = liveFromCompare();
  if (live === null) return;             // compare.js imports them from here now: the golden copy is the pin
  assert.equal(live.ERROR_GRADIENT, P.ERROR_GRADIENT);
  for (const scale of SCALES) {
    const values = probe(scale);
    assert.ok(sameBytes(live.errorColors(values, scale), P.errorColors(values, scale)), `error ${scale}`);
    assert.ok(sameBytes(live.divergingColors(values, scale), P.divergingColors(values, scale)), `diverging ${scale}`);
  }
});

test('a repaint can reuse its buffer: the same bytes, and a buffer of the wrong size is not written', () => {
  const values = probe(0.5).slice(0, 100);
  const first = P.errorColors(values, 0.5);
  const reused = new Float32Array(300);
  assert.equal(P.errorColors(values, 0.5, reused), reused);
  assert.ok(sameBytes(reused, first));
  const wrong = new Float32Array(10);
  const fresh = P.errorColors(values, 0.5, wrong);
  assert.notEqual(fresh, wrong);
  assert.equal(fresh.length, 300);
  assert.ok(sameBytes(P.divergingColors(values, 0.5, new Float32Array(300)), P.divergingColors(values, 0.5)));
});

// -- lateral and axial ------------------------------------------------------------------------

test('lateral is what is left of the distance once the part along Z is taken out', () => {
  const abs = [5, 0, 0.3, 1.0, 2.0];
  const axial = [3, 0, 0.3, 0, 2.0];
  const lateral = P.lateralValues(abs, axial);
  assert.deepEqual(Array.from(lateral).map((v) => Math.round(v * 1e9) / 1e9), [4, 0, 0, 1, 0]);
  // Rounded fields that overshoot by a hair do not make a negative radicand.
  assert.equal(P.lateralValues([0.1], [0.1000001])[0], 0);
  assert.ok(lateral instanceof Float64Array);
  const reuse = new Float64Array(5);
  assert.equal(P.lateralValues(abs, axial, reuse), reuse);
});

test('the metric picks the field: the total as given, the axial gap, the sideways part; none without an axial field', () => {
  const abs = [5, 1, 2];
  const axial = [3, 0, 2];
  assert.equal(P.errorValues('abs', abs, axial), abs, 'the total is the very array, no copy');
  assert.equal(P.errorValues(undefined, abs, null), abs);
  assert.equal(P.errorValues('axial', abs, axial), axial);
  assert.deepEqual(Array.from(P.errorValues('lateral', abs, axial)).map((v) => Math.round(v * 1e9) / 1e9), [4, 1, 0]);
  assert.equal(P.errorValues('lateral', abs, []), null, 'a result from before the axial field');
  assert.equal(P.errorValues('axial', abs, null), null);
  assert.equal(P.errorValues('axial', abs, [1, 2]), null, 'the wrong length is not this mesh\'s');
  assert.equal(P.errorValues('sideways', abs, axial), null);
  assert.deepEqual(P.METRICS, ['abs', 'lateral', 'axial']);
  assert.ok(P.METRICS.every((m) => P.METRIC_LABELS[m]));
});

// -- the threshold ------------------------------------------------------------------------------

test('threshold: below it the neutral gray, from it the ramp starts over and reaches red at the scale', () => {
  const colors = P.thresholdColors([0, 0.4, 0.5, 0.75, 1.0, 2.0], 1.0, 0.5);
  const at = (i) => Array.from(colors.slice(3 * i, 3 * i + 3));
  const near = (a, b) => a.every((v, c) => Math.abs(v - b[c]) < 1e-6);
  assert.ok(near(at(0), P.BELOW_THRESHOLD) && near(at(1), P.BELOW_THRESHOLD));
  assert.ok(near(at(2), P.ERROR_STOPS[0]), 'the threshold is the ramp\'s zero');
  assert.ok(near(at(3), P.ERROR_STOPS[1]), 'half way up is amber');
  assert.ok(near(at(4), P.ERROR_STOPS[2]) && near(at(5), P.ERROR_STOPS[2]), 'the scale and beyond are red');
  // The part above the threshold is the plain ramp over the shifted values.
  const values = [0.5, 0.6, 0.8, 0.95, 1.4];
  assert.ok(sameBytes(P.thresholdColors(values, 1.0, 0.5),
    P.errorColors(values.map((v) => v - 0.5), 0.5)), 'above the threshold: errorColors of (v - threshold) at (scale - threshold)');
  // A custom gray, and a buffer to reuse.
  const out = new Float32Array(3);
  assert.equal(P.thresholdColors([0.1], 1, 0.5, { below: [0, 0, 0], out }), out);
  assert.deepEqual(Array.from(out), [0, 0, 0]);
});

test('threshold: no threshold width left, a value that is not a number, and a threshold of zero', () => {
  // At or past the scale the ramp has no width: every vertex at or above the threshold is red,
  // the one exactly at it too (it used to be the ramp's pale zero: the worst vertex, the palest).
  const c = P.thresholdColors([0.9, 1.0, 1.0000001, 5], 0.8, 1.0);
  const near = (i, rgb) => Array.from(c.slice(3 * i, 3 * i + 3)).every((v, k) => Math.abs(v - rgb[k]) < 1e-6);
  assert.ok(near(0, P.BELOW_THRESHOLD) && near(1, P.ERROR_STOPS[2]) && near(2, P.ERROR_STOPS[2]) && near(3, P.ERROR_STOPS[2]));
  // The percentile slider at 100 %: the threshold is the field's maximum, above a scale (the 98th
  // percentile) that lies below it.  The single worst vertex is red, and so is the next worst.
  const top = P.thresholdColors([0.1, 0.4, 2.0], 0.5, 2.0);
  const f32 = (rgb) => Array.from(rgb, (v) => Math.fround(v));     // the buffer is float32
  const topAt = (i) => Array.from(top.slice(3 * i, 3 * i + 3));
  assert.deepEqual(topAt(0), f32(P.BELOW_THRESHOLD)); assert.deepEqual(topAt(1), f32(P.BELOW_THRESHOLD));
  assert.deepEqual(topAt(2), f32(P.ERROR_STOPS[2]));
  // Threshold equal to the scale, at it and past it, the same.
  const equal = P.thresholdColors([1.5, 1.5 + 1e-9, 1.6], 1.5, 1.5);
  for (let i = 0; i < 3; i += 1) assert.deepEqual(Array.from(equal.slice(3 * i, 3 * i + 3)), f32(P.ERROR_STOPS[2]));
  // With a ramp of any width the threshold is still its zero.
  assert.ok(Array.from(P.thresholdColors([1.0], 1.5, 1.0)).every((v, k) => Math.abs(v - P.ERROR_STOPS[0][k]) < 1e-6));
  const nan = P.thresholdColors([NaN, undefined], 1, 0.5);
  assert.ok(Array.from(nan).every((v, k) => Math.abs(v - P.BELOW_THRESHOLD[k % 3]) < 1e-6));
  // A threshold of 0 is the plain ramp (nothing is below it).
  const values = probe(1).filter((v) => Number.isFinite(v) && v >= 0);
  assert.ok(sameBytes(P.thresholdColors(values, 1, 0), P.errorColors(values, 1)));
  // ... also when the scale is 0 as well (two identical renderings; a render whose vertices lie on
  // the annotations has no lateral error): a field of zeros is the palest gray, not the worst red,
  // and the first vertex that is off at all is red, as it was before the no-width rule existed.
  const zeros = P.thresholdColors([0, 0, 0], 0, 0);
  for (let i = 0; i < 3; i += 1) assert.deepEqual(Array.from(zeros.slice(3 * i, 3 * i + 3)), f32(P.ERROR_STOPS[0]));
  const barely = P.thresholdColors([0, 1e-6], 0, 0);
  assert.deepEqual(Array.from(barely.slice(0, 3)), f32(P.ERROR_STOPS[0]));
  assert.deepEqual(Array.from(barely.slice(3, 6)), f32(P.ERROR_STOPS[2]));
});

// -- area-weighted numbers: pinned against the Python ----------------------------------------

const close = (a, b, tol = 1e-12, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} is not within ${tol} of ${b}`);
const closeAll = (a, b, tol = 1e-12, what = '') => {
  assert.equal(a.length, b.length, `${what} lengths`);
  for (let i = 0; i < a.length; i += 1) close(a[i], b[i], tol, `${what}[${i}]`);
};

test('weighted quantiles equal the Python\'s (the server\'s weighted_quantiles)', () => {
  const { values, weights, probs, expect, expect_equal: equal, expect_zero: zero } = PY.wq;
  closeAll(P.weightedQuantiles(values, weights, probs), expect, 1e-12, 'weighted');
  closeAll(P.weightedQuantiles(values, new Float64Array(values.length).fill(1), probs), equal, 1e-12, 'equal weights');
  closeAll(P.weightedQuantiles(values, null, probs), equal, 1e-12, 'no weights');
  closeAll(P.weightedQuantiles(values, new Float64Array(values.length), probs), zero, 1e-12, 'zero weights count every value once');
  assert.deepEqual(P.weightedQuantiles([], [], [0.1, 0.9]), [0, 0]);
  // A value that is not a number is left out, not sorted into the middle of the others.
  closeAll(P.weightedQuantiles([4, NaN, 1, Infinity, 3, 2], null, [0, 0.125, 0.5, 1]), [1, 1, 2.5, 4]);
  assert.deepEqual(P.weightedQuantiles([NaN, NaN], null, [0.5]), [0]);
  // By hand, as the Python's own test: 1, 2, 3, 4 at the plotting positions 1/8, 3/8, 5/8, 7/8.
  closeAll(P.weightedQuantiles([4, 1, 3, 2], [1, 1, 1, 1], [0, 0.125, 0.5, 1]), [1, 1, 2.5, 4]);
  closeAll(P.weightedQuantiles([4, 1, 3, 2], [1, 3, 1, 1], [0.1, 0.25, 0.5, 0.75, 0.95]), [1, 1, 1.75, 3, 4]);
});

test('area statistics equal the Python\'s AreaStats, field for field', () => {
  const { values, areas, expect } = PY.area_stats;
  const stats = P.areaStats(values, areas);
  assert.deepEqual(Object.keys(stats).sort(), Object.keys(expect).sort());
  for (const key of Object.keys(expect)) close(stats[key], expect[key], 1e-12, key);
  // Without any area every vertex counts once.
  const flat = P.areaStats([1, -3], [0, 0]);
  close(flat.mean_abs_um, 2); close(flat.mean_signed_um, -1); close(flat.max_abs_um, 3);
});

test('area statistics without areas yet count every vertex once, as every other helper does', () => {
  // A caller that computes a lateral or axial figure before the mesh's areas exist (null or
  // undefined, or an array of no positive weight) gets the unit-weight answer, not an exception.
  const values = [1, -3, 2.5, -0.5];
  const ones = P.areaStats(values, new Float64Array(4).fill(1));
  for (const none of [null, undefined, [], new Float64Array(4), [0, 0, 0, 0]]) {
    const stats = P.areaStats(values, none);
    for (const key of Object.keys(ones)) close(stats[key], ones[key], 1e-12, `${key} for ${JSON.stringify(none)}`);
  }
  close(ones.mean_abs_um, 1.75); close(ones.mean_signed_um, 0); close(ones.max_abs_um, 3);
  // The same for the other helpers that take areas (they already did).
  assert.equal(P.areaShareAtLeast(values, null, 2), 0.25);
  assert.equal(P.overlapShares(new Uint8Array([0, 1, 2, 2]), null).outside, 0.5);
});

test('the largest error is over the vertices some face uses, and a vertex that stands for no area does not set it', () => {
  // The Python's AreaStats.max_abs_um is over `weight > 0`: a vertex no face uses (area 0) can
  // carry a wild value from an old mesh or a failed measurement and must not become the maximum.
  const values = [0.2, -0.4, 50, 0.6, -9];
  const areas = [1, 2, 0, 1, 0];
  const stats = P.areaStats(values, areas);
  close(stats.max_abs_um, 0.6, 1e-12, 'max over the vertices with area');
  close(stats.mean_abs_um, (0.2 + 0.8 + 0.6) / 4, 1e-12, 'mean');
  // They are out of the quantiles too (zero weights are left out).
  close(stats.p98_abs_um, P.weightedQuantiles(values.map(Math.abs), areas, [0.98])[0], 1e-12, 'p98');
  assert.ok(stats.p98_abs_um <= 0.6);
  // Without any area at all every vertex counts, the unreferenced ones too: nothing says otherwise.
  close(P.areaStats(values, null).max_abs_um, 50);
});

test('vertex areas equal the Python\'s, add up to the surface, and an unused vertex weighs nothing', () => {
  const { vertices, faces, expect, surface } = PY.areas;
  const area = P.vertexAreas(vertices, faces);
  closeAll(area, expect, 1e-12, 'area');
  close(area.reduce((a, b) => a + b, 0), surface, 1e-12, 'sum');
  assert.equal(area[area.length - 1], 0);
  assert.equal(P.vertexAreas([], []).length, 0);
  // Typed arrays (what a renderer holds) give the same.
  closeAll(P.vertexAreas(Float32Array.from(vertices), Uint32Array.from(faces)), expect, 1e-5, 'typed');
});

test('the histogram equals the Python\'s: the same edges, the same area in each bin, the same tail', () => {
  const { values, areas, scale, expect_edges: edges, expect_share: share, expect_tail: tail, expect_quantiles: q } = PY.hist;
  const mine = P.histogramEdges(scale);
  assert.deepEqual(mine, edges, 'numpy.linspace to the last bit');
  const hist = P.areaHistogram(values, areas, edges);
  closeAll(hist.area_share, share, 1e-12, 'share');
  close(hist.tail_share, tail, 1e-12, 'tail');
  assert.equal(hist.area_share.length, 40);
  close(hist.area_share.reduce((a, b) => a + b, 0) + hist.tail_share, 1 - 0, 1e-12, 'all of the area');
  closeAll(P.quantileTable(values, areas), q, 1e-12, 'quantile table');
  // A bin holds its left edge, the last also its right; the value beyond is the tail.
  const small = P.areaHistogram([0, 0.5, 1, 1.0000001], null, [0, 0.5, 1]);
  assert.deepEqual(small.area_share, [0.25, 0.5]);
  close(small.tail_share, 0.25);
});

test('a quantile table is read both ways: the value at a percentile and the percentile of a value', () => {
  const table = Array.from({ length: 101 }, (_, k) => k * 0.01);          // 0 .. 1, evenly
  close(P.percentileValue(table, 0), 0); close(P.percentileValue(table, 100), 1);
  close(P.percentileValue(table, 90), 0.9); close(P.percentileValue(table, 90.5), 0.905);
  close(P.percentileValue(table, 250), 1); close(P.percentileValue(table, -5), 0);
  close(P.percentileOf(table, 0.9), 90, 1e-9); close(P.percentileOf(table, 0.905), 90.5, 1e-9);
  assert.equal(P.percentileOf(table, 5), 100); assert.equal(P.percentileOf(table, -1), 0);
  // A run of zeros ends where the run ends: a share cannot be put in the middle of it.
  const flat = [0, 0, 0, 0.1, 0.2];
  assert.equal(P.percentileOf(flat, 0), 50);
  close(P.percentileOf(flat, 0.15), 87.5, 1e-9);
  assert.equal(P.percentileValue([], 50), 0);
  assert.equal(P.percentileOf([], 1), 0);
  // The round trip on a real table.
  const real = P.quantileTable(PY.hist.values, PY.hist.areas);
  for (const p of [10, 33, 50, 80, 97]) {
    const v = P.percentileValue(real, p);
    assert.ok(Math.abs(P.percentileOf(real, v) - p) < 1e-6, `percentile ${p}`);
  }
});

test('the share of the surface at or above a threshold is by area, and the vertices\' count says otherwise', () => {
  const values = [1, 1, 1, 0];
  const areas = [1, 1, 1, 9];
  close(P.areaShareAtLeast(values, areas, 1), 0.25);
  close(P.areaShareAtLeast(values, null, 1), 0.75);
  close(P.areaShareAtLeast(values, [0, 0, 0, 0], 1), 0.75, 1e-12, 'no area: every vertex counts once');
  assert.equal(P.areaShareAtLeast([], [], 0), 0);
});

// -- the histogram and legend, as data --------------------------------------------------------

test('histogram model: bars along 0..1 with their state against the threshold, the tail, the marks and the share above', () => {
  const hist = { edges: [0, 1, 2, 3, 4], area_share: [0.4, 0.3, 0.2, 0.05], tail_share: 0.05 };
  const quantiles = Array.from({ length: 101 }, (_, k) => k * 0.04);       // 0 .. 4
  const model = P.histogramModel(hist, { threshold: 2.5, floor: 0.5, quantiles });
  assert.deepEqual(model.bars.map((b) => b.state), ['below', 'below', 'straddle', 'above']);
  assert.deepEqual(model.bars.map((b) => [b.x0, b.x1]), [[0, 0.25], [0.25, 0.5], [0.5, 0.75], [0.75, 1]]);
  closeAll(model.bars.map((b) => b.height), [1, 0.75, 0.5, 0.125], 1e-12, 'height');
  assert.deepEqual(model.axis, [0, 4]);
  close(model.tail.share, 0.05); close(model.tail.height, 0.125); assert.equal(model.tail.from, 4);
  close(model.threshold.at, 0.625); assert.equal(model.threshold.beyond, false);
  close(model.floor.at, 0.125);
  // Half of the straddled bin (2..3) is above 2.5.
  close(model.shareAbove, 0.05 + 0.05 + 0.1, 1e-12);
  assert.equal(model.shareAboveIsBound, false);
  assert.deepEqual(model.ticks.map((t) => t.percent), [50, 90, 95, 98]);
  close(model.ticks[1].value, 3.6, 1e-9); close(model.ticks[1].at, 0.9, 1e-9);
  // No threshold: every bar is simply on; a threshold beyond the axis only bounds the share.
  assert.ok(P.histogramModel(hist).bars.every((b) => b.state === 'on'));
  assert.equal(P.histogramModel(hist).shareAbove, null);
  const far = P.histogramModel(hist, { threshold: 9 });
  assert.equal(far.threshold.beyond, true); assert.equal(far.threshold.at, 1);
  assert.equal(far.shareAbove, 0.05); assert.equal(far.shareAboveIsBound, true);
  assert.equal(P.histogramModel({}).bars.length, 0);
  assert.equal(P.histogramModel(hist, { floor: 0 }).floor, null);
});

test('histogram model: a tail taller than every bar sets the height every bar is drawn at', () => {
  const hist = { edges: [0, 1, 2], area_share: [0.1, 0.2], tail_share: 0.7 };
  const model = P.histogramModel(hist);
  close(model.tail.height, 1, 1e-12, 'the tail is the tallest thing drawn');
  closeAll(model.bars.map((b) => b.height), [0.1 / 0.7, 0.2 / 0.7], 1e-12, 'bars relative to the tail');
  // With no tail the tallest bar is.
  const none = P.histogramModel({ ...hist, tail_share: 0 });
  closeAll(none.bars.map((b) => b.height), [0.5, 1], 1e-12, 'bars relative to the tallest bar');
  close(none.tail.height, 0);
  // A histogram of nothing is not divided by zero.
  const empty = P.histogramModel({ edges: [0, 1, 2], area_share: [0, 0], tail_share: 0 });
  assert.ok(empty.bars.every((b) => b.height === 0) && empty.tail.height === 0);
});

test('legend model: the ramp, its three numbers, and where it starts when there is a threshold', () => {
  const plain = P.legendModel({ scale: 0.8 });
  assert.equal(plain.gradient, P.ERROR_GRADIENT);
  assert.deepEqual(plain.ticks, [0, 0.4, 0.8]);
  assert.equal(plain.thresholded, false); assert.equal(plain.below, null);
  const cut = P.legendModel({ scale: 0.8, threshold: 0.4 });
  assert.deepEqual(cut.ticks.map((v) => Math.round(v * 1e9) / 1e9), [0.4, 0.6, 0.8]);
  assert.equal(cut.thresholded, true); assert.deepEqual(cut.below, P.BELOW_THRESHOLD);
  assert.deepEqual(P.legendModel({ scale: 0.3, threshold: 0.5 }).ticks, [0.5, 0.5, 0.5]);
  assert.match(P.DIVERGING_GRADIENT, /^linear-gradient\(to right, rgb\(59,115,235\), rgb\(255,255,255\), rgb\(219,56,56\)\)$/);
});

// -- two surfaces ---------------------------------------------------------------------------------

test('overlap classes: outside, shared and inside by a tolerance, twice the sample spacing by default', () => {
  assert.equal(P.defaultTolerance(0.06), 0.12);
  const signed = [-0.5, -0.12, -0.1, 0, 0.1, 0.12, 0.1200001, 0.5, NaN];
  const classes = P.classifyOverlap(signed, 0.12);
  assert.deepEqual(Array.from(classes), [0, 1, 1, 1, 1, 1, 2, 2, 1]);
  assert.deepEqual(P.OVERLAP_NAMES, ['inside', 'shared', 'outside']);
  assert.equal(P.OVERLAP.INSIDE, 0); assert.equal(P.OVERLAP.SHARED, 1); assert.equal(P.OVERLAP.OUTSIDE, 2);
  // A wider tolerance only moves vertices toward shared.
  const wide = P.classifyOverlap(signed, 1);
  assert.ok(Array.from(wide).every((c) => c === 1));
  const zero = P.classifyOverlap([0, 1e-9, -1e-9], 0);
  assert.deepEqual(Array.from(zero), [1, 2, 0]);
  const reuse = new Uint8Array(signed.length);
  assert.equal(P.classifyOverlap(signed, 0.12, reuse), reuse);
});

test('overlap colors follow the class, and the shares are by area', () => {
  const classes = Uint8Array.from([0, 1, 2, 2]);
  const colors = P.overlapColors(classes);
  assert.deepEqual(Array.from(colors.slice(0, 3)).map((v) => Math.fround(v)), P.DIVERGING_NEGATIVE.map((v) => Math.fround(v)));
  assert.deepEqual(Array.from(colors.slice(3, 6)).map((v) => Math.fround(v)), P.ERROR_STOPS[0].map((v) => Math.fround(v)));
  assert.deepEqual(Array.from(colors.slice(9, 12)).map((v) => Math.fround(v)), P.DIVERGING_POSITIVE.map((v) => Math.fround(v)));
  const shares = P.overlapShares(classes, [1, 1, 2, 4]);
  close(shares.inside, 0.125); close(shares.shared, 0.125); close(shares.outside, 0.75);
  assert.deepEqual(shares.counts, [1, 1, 2]);
  close(P.overlapShares(classes, null).outside, 0.5);
  close(P.overlapShares(new Uint8Array(0), []).outside, 0);
});

// -- the rim points ---------------------------------------------------------------------------------

test('rim filter: all, none, the ones inside the render (it is fatter) or outside it (it stops short)', () => {
  const xyz = [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3];
  const um = [0.1, 0.2, 0.3, 0.4];
  const sides = [1, 0, 0, 1];
  const all = P.filterRims(xyz, um, sides, 'all');
  assert.equal(all.n, 4); assert.deepEqual(Array.from(all.xyz), xyz); assert.ok(all.known);
  assert.deepEqual(Array.from(all.values).map((v) => Math.fround(v)), um.map((v) => Math.fround(v)));
  const out = P.filterRims(xyz, um, sides, 'outside');
  assert.equal(out.n, 2); assert.deepEqual(Array.from(out.xyz), [1, 1, 1, 2, 2, 2]);
  assert.deepEqual(Array.from(out.values).map((v) => Math.fround(v)), [0.2, 0.3].map((v) => Math.fround(v)));
  const fat = P.filterRims(xyz, um, sides, 'enclosed');
  assert.deepEqual(Array.from(fat.xyz), [0, 0, 0, 3, 3, 3]); assert.equal(fat.n, 2);
  const none = P.filterRims(xyz, um, sides, 'none');
  assert.equal(none.n, 0); assert.equal(none.xyz.length, 0);
  // Sides not measured (an older result): the filters keep every point and say they could not filter.
  const unknown = P.filterRims(xyz, um, [], 'outside');
  assert.equal(unknown.n, 4); assert.equal(unknown.known, false);
  assert.equal(P.filterRims(xyz, um, [1], 'enclosed').known, false, 'the wrong length is not these points\'');
  assert.equal(P.filterRims([], [], [], 'outside').n, 0);
  assert.deepEqual(P.RIM_FILTERS, ['all', 'outside', 'enclosed', 'none']);
});

// -- speed ------------------------------------------------------------------------------------------

test('a 400,000-face mesh repaints, thresholds and totals fast enough to follow a slider', () => {
  const n = 200000;
  const random = lcg(11);
  const values = Float64Array.from({ length: n }, () => random() * 1.5);
  const t0 = performance.now();
  const colors = new Float32Array(n * 3);
  for (let k = 0; k < 5; k += 1) P.thresholdColors(values, 1.0, 0.1 + 0.1 * k, { out: colors });
  const repaint = (performance.now() - t0) / 5;
  const t1 = performance.now();
  const table = P.quantileTable(values, values);
  const table_ms = performance.now() - t1;
  assert.equal(table.length, 101);
  // Generous bounds (a busy machine): the interactive path is milliseconds, the one-time sort well under a second or two.
  assert.ok(repaint < 400, `repaint ${repaint.toFixed(1)} ms`);
  assert.ok(table_ms < 4000, `quantile table ${table_ms.toFixed(0)} ms`);
  console.log(`     (${n.toLocaleString()} vertices: threshold repaint ${repaint.toFixed(1)} ms, quantile table ${table_ms.toFixed(0)} ms)`);
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
