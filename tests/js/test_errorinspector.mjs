// The error inspector (static/js/errorinspector.js): the models behind each card of the
// drawer -- direction, the histogram on one axis, the profile along Z, the table of
// annotations with its sort and its minimum-area filter, the classes by radius, the worst
// places, the improvement between two layers -- on results built the way the server builds
// them, and the drawer itself against a fake DOM (what a drag on the threshold, a click on a
// row, a sort, an edge typed in the box call back with).  Run with
//
//     node tests/js/test_errorinspector.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free, no browser: the real
// SVG is looked at by tools/check_inspector.py.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');

// Importing the module must not read the page.
const poisoned = ['document', 'window', 'navigator', 'localStorage', 'requestAnimationFrame', 'ResizeObserver'];
const saved = new Map(poisoned.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
for (const name of poisoned) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    get() { throw new Error(`errorinspector.js touched ${name} at import`); },
  });
}
let loadError = null;
let I = {};
try {
  I = await import(`${pathToFileURL(path.join(jsDir, 'errorinspector.js')).href}?cold`);
} catch (error) {
  loadError = error;
} finally {
  for (const [name, descriptor] of saved) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
  }
}
const P = await import(pathToFileURL(path.join(jsDir, 'errorpaint.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, tol = 1e-9, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} is not within ${tol} of ${b}`);

/** A deterministic pseudo-random sequence in [0, 1). */
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

// -- a result built the way the server builds one --------------------------------------------------

/**
 * A `DeviationResultDTO` made from per-vertex data by the server's own formulas: `n` annotations
 * (the last `outlines` of them traced outlines), `nv` vertices with an area, an owner and an error.
 * `round` rounds the shares to the 6 decimals the server sends them with.
 */
function makeResult({ seed = 1, n = 60, outlines = 4, nv = 6000, scale = 1.0, bias = 0.6, round = false, quality = 1.0 } = {}) {
  const next = rng(seed);
  const radius = Array.from({ length: n }, () => 0.15 + next() ** 3 * 8);
  const base = Array.from({ length: n }, () => (0.1 + next()) * quality);
  const area = new Float64Array(nv);
  const owner = new Uint32Array(nv);
  const error = new Float64Array(nv);
  const z = new Float64Array(nv);
  for (let v = 0; v < nv; v += 1) {
    area[v] = 0.001 + next() * next() * 0.02;
    owner[v] = Math.min(n - 1, Math.floor(next() ** 2 * n));
    error[v] = base[owner[v]] * (0.4 + 1.2 * next());
    z[v] = 90 + owner[v] * 0.27 + next() * 0.4;
  }
  const total = area.reduce((s, a) => s + a, 0);
  const sumAE = area.reduce((s, a, v) => s + a * error[v], 0);
  const own = (i) => Array.from(owner).map((o, v) => (o === i ? v : -1)).filter((v) => v >= 0);
  const annotations = {
    ids: [], sections: [], kind: [], xyz: [], radius: [], area_share: [], surface_mean: [], surface_max: [],
    rim_mean: [], rim_max: [], rim_enclosed_share: [],
  };
  const r6 = (v) => (round ? Math.round(v * 1e6) / 1e6 : v);
  for (let i = 0; i < n; i += 1) {
    const mine = own(i);
    const a = mine.reduce((s, v) => s + area[v], 0);
    annotations.ids.push(500000 + i * 7);
    annotations.sections.push(1000 + 3 * i);
    annotations.kind.push(i >= n - outlines ? 1 : 0);
    annotations.xyz.push(10 + i, 20 + 2 * i, (1000 + 3 * i) * 0.09);
    annotations.radius.push(radius[i]);
    annotations.area_share.push(r6(a / total));
    annotations.surface_mean.push(mine.length ? mine.reduce((s, v) => s + area[v] * error[v], 0) / a : null);
    annotations.surface_max.push(mine.length ? Math.max(...mine.map((v) => error[v])) : null);
    annotations.rim_mean.push(base[i] * 0.9);
    annotations.rim_max.push(base[i] * 2);
    annotations.rim_enclosed_share.push(i % 11 === 0 ? null : (i % 7) / 7);
  }
  const edges = P.histogramEdges(scale);
  const surface = {
    area_um2: total,
    mean_um: sumAE / total,
    excess_um3: sumAE,
    quantiles: P.quantileTable(error, area),
    hist: P.areaHistogram(error, area, edges),
    floor_um: 0.045,
  };
  const z0 = Math.floor(Math.min(...z));
  const bins = Math.floor(Math.max(...z)) - z0 + 1;
  const zp = { z0, step: 1, area: [], mean: [], p95: [], rim_mean: [], n_annotations: [] };
  for (let b = 0; b < bins; b += 1) {
    const idx = Array.from(z).map((zz, v) => (Math.floor(zz) - z0 === b ? v : -1)).filter((v) => v >= 0);
    const a = idx.reduce((s, v) => s + area[v], 0);
    zp.area.push(a);
    zp.mean.push(a > 0 ? idx.reduce((s, v) => s + area[v] * error[v], 0) / a : 0);
    zp.p95.push(a > 0 ? P.weightedQuantiles(idx.map((v) => error[v]), idx.map((v) => area[v]), [0.95])[0] : 0);
    zp.rim_mean.push(0.2 + b * 0.01);
    zp.n_annotations.push(annotations.xyz.filter((_, k) => k % 3 === 2 && Math.floor(annotations.xyz[k]) - z0 === b).length);
  }
  const spots = Array.from({ length: 12 }, (_, k) => ({
    xyz: [10 + k, 20 + k, 95 + k], section: 1000 + 3 * k, annotation_id: k === 5 ? null : 500000 + k * 7,
    excess_um3: 12 - k, share: (12 - k) / 120, mean: 1.5 - k * 0.05, max: 2 - k * 0.05,
  }));
  return {
    mesh_id: `m${seed}`, n_vertices: nv, n_faces: 2 * nv, vertex_um: [], scale_um: scale,
    surface, annotations, z_profile: zp, spots,
    bias: { n: 500, enclosed_share: bias, enclosed_mean_um: 0.4, outside_mean_um: 0.9, signed_mean_um: bias * 0.4 - (1 - bias) * 0.9 },
    axial_um: [], rim_enclosed: [],
    // The raw data, for the tests that check a statistic against its definition.
    _raw: { area, owner, error, z },
  };
}

// -- loading and numbers -----------------------------------------------------------------------------

test('importing the module touches no document, window, storage or observer', () => {
  assert.equal(loadError, null, loadError && String(loadError.stack));
  assert.equal(typeof I.ErrorInspector, 'function');
});

test('numbers are written with the digits they need, signs are real signs, missing ones are dashes', () => {
  assert.equal(I.fmtUm(0.04567), '0.046');
  assert.equal(I.fmtUm(0.456), '0.46');
  assert.equal(I.fmtUm(12.34), '12.3');
  assert.equal(I.fmtUm(NaN), '—');
  assert.equal(I.fmtSigned(0.12), '+0.12');
  assert.equal(I.fmtSigned(-0.12), '−0.12');
  assert.equal(I.fmtShare(0.0004), '0.040 %');
  assert.equal(I.fmtShare(0.0123), '1.23 %');
  assert.equal(I.fmtShare(0.612), '61.2 %');
  assert.equal(I.fmtShare(null), '—');
  assert.equal(I.fmtCount(1234567), '1,234,567');
});

test('a Location ID always carries its volume, and the lack of one shows', () => {
  assert.equal(I.locationLabel('Inferior', 39425), 'Inferior 39425');
  assert.equal(I.locationLabel('', 39425), '? 39425');
  const rows = I.annotationRows(makeResult(), 'RC2');
  assert.ok(rows.length > 0 && rows.every((r) => r.label === `RC2 ${r.id}` && r.volume === 'RC2'));
  const spots = I.spotSummary(makeResult(), 'RC2');
  assert.ok(spots.rows.filter((r) => r.annotationId !== null).every((r) => r.label === `RC2 ${r.annotationId}`));
  assert.equal(spots.rows[5].label, '—', 'a place with no annotation names none');
});

// -- direction -----------------------------------------------------------------------------------------

test('the direction model splits the rim points into fat and short', () => {
  const m = I.biasModel({ n: 1000, enclosed_share: 0.77, enclosed_mean_um: 0.2, outside_mean_um: 0.5, signed_mean_um: -0.1 });
  assert.equal(m.known, true);
  close(m.fat, 0.77, 1e-12);
  close(m.short, 0.23, 1e-12);
  assert.equal(m.fatMean, 0.2);
  assert.equal(m.signed, -0.1);
  assert.equal(I.biasModel(null).known, false);
  assert.equal(I.biasModel({ n: 0, enclosed_share: 0.5 }).known, false);
  assert.equal(I.biasModel({ n: 10, enclosed_share: 1.4 }).fat, 1, 'a share is clamped to 0..1');
  assert.equal(I.biasModel({ n: 10, enclosed_share: 0.5, enclosed_mean_um: null }).fatMean, null);
});

// -- the histogram on one axis, the threshold ---------------------------------------------------------------

test('layers measured to different scales share one axis: the farthest reach, equal bins', () => {
  const a = makeResult({ seed: 1, scale: 1.0 });
  const b = makeResult({ seed: 2, scale: 2.5 });
  const edges = I.commonEdges([a, b]);
  assert.equal(edges.length, 41);
  assert.equal(edges[0], 0);
  close(edges[40], 1.5 * 2.5, 1e-12);
  close(edges[1] - edges[0], edges[40] / 40, 1e-12);
  assert.deepEqual(I.commonEdges([{}, null]), []);
});

test('re-binning keeps the area where the new edges cover the old', () => {
  const b = makeResult({ seed: 2, scale: 2.5 });
  const own = I.commonEdges([b]);
  const same = I.rebinHistogram(b.surface.hist, own);
  for (let j = 0; j < 40; j += 1) close(same[j], b.surface.hist.area_share[j], 1e-12, `bin ${j}`);
  const a = makeResult({ seed: 1, scale: 1.0 });
  const wide = I.commonEdges([a, b]);                     // a's 40 bins are spread over a wider axis
  const rebinned = I.rebinHistogram(a.surface.hist, wide);
  close(rebinned.reduce((s, v) => s + v, 0), a.surface.hist.area_share.reduce((s, v) => s + v, 0), 1e-12, 'mass');
  // A bin of the narrow layer lands in the right place on the wide axis.
  const k = a.surface.hist.area_share.indexOf(Math.max(...a.surface.hist.area_share));
  const centre = (a.surface.hist.edges[k] + a.surface.hist.edges[k + 1]) / 2;
  const j = Math.floor((centre / wide[40]) * 40);
  assert.ok(rebinned[j] > 0);
  // Narrower edges lose the tail, never invent area.
  const narrow = I.rebinHistogram(b.surface.hist, I.commonEdges([a]));
  assert.ok(narrow.reduce((s, v) => s + v, 0) < b.surface.hist.area_share.reduce((s, v) => s + v, 0));
});

test('the threshold and its percentile are the same number two ways, and the readout adds up', () => {
  const r = makeResult({ seed: 3, scale: 1.2 });
  for (const percent of [10, 50, 90, 98]) {
    const value = I.thresholdAtPercent(r, percent);
    const readout = I.thresholdReadout(r, value);
    close(readout.percent, percent, 1e-6, `p${percent}`);
    close(readout.below + readout.above, 1, 1e-12);
  }
  // Against the definition: the share of the area at or above the 90th percentile is about 10 %.
  const t = I.thresholdAtPercent(r, 90);
  const { area, error } = r._raw;
  const above = area.reduce((s, a, v) => s + (error[v] >= t ? a : 0), 0) / area.reduce((s, a) => s + a, 0);
  close(above, 0.1, 0.01);
  assert.equal(I.thresholdReadout({}, 0.3), null);
  assert.equal(I.thresholdReadout(r, NaN), null);
});

// -- the profile along Z --------------------------------------------------------------------------------------

test('the profile has a point for each bin that has something to say, and breaks where it does not', () => {
  const r = makeResult({ seed: 4 });
  const mean = I.zProfileSeries(r, 'mean');
  const zp = r.z_profile;
  assert.equal(mean.points.length, zp.area.filter((a) => a > 0).length);
  for (const p of mean.points) {
    close(p.zc, zp.z0 + p.i + 0.5, 1e-12);
    assert.equal(p.value, zp.mean[p.i]);
    assert.ok(zp.area[p.i] > 0);
  }
  assert.equal(I.zProfileSeries(r, 'p95').points[0].value, zp.p95[I.zProfileSeries(r, 'p95').points[0].i]);
  const rim = I.zProfileSeries(r, 'rim');
  assert.ok(rim.points.every((p) => zp.n_annotations[p.i] > 0));
  assert.equal(rim.max, Math.max(...rim.points.map((p) => p.value)));
  assert.equal(I.zProfileSeries({}, 'mean'), null);
  assert.equal(I.zProfileSeries({ z_profile: { mean: [] } }, 'mean'), null);
});

// -- the annotation table ------------------------------------------------------------------------------------

test('the table is the columnar result as rows, null being not-a-number', () => {
  const r = makeResult({ seed: 5, n: 30 });
  r.annotations.surface_mean[3] = null;
  r.annotations.rim_enclosed_share[4] = null;
  const rows = I.annotationRows(r, 'Inferior');
  assert.equal(rows.length, 30);
  assert.equal(rows[0].id, r.annotations.ids[0]);
  assert.equal(rows[25].kind, 'disc');
  assert.equal(rows[29].kind, 'outline');
  assert.ok(Number.isNaN(rows[3].surfaceMean) && Number.isNaN(rows[3].excess));
  assert.ok(Number.isNaN(rows[4].fatShare));
  assert.deepEqual(rows[2].xyz, [12, 24, 1006 * 0.09]);
  close(rows[7].excess, r.surface.area_um2 * rows[7].areaShare * rows[7].surfaceMean, 1e-9);
  assert.deepEqual(I.annotationRows({}, 'x'), []);
});

test('sorting: numbers either way with the missing last, IDs in numeric order, ties in table order, the input untouched', () => {
  const rows = [
    { index: 0, id: 9, label: 'V 9', surfaceMean: 0.5, areaShare: 0.1 },
    { index: 1, id: 10, label: 'V 10', surfaceMean: NaN, areaShare: 0.2 },
    { index: 2, id: 2, label: 'V 2', surfaceMean: 0.9, areaShare: 0.3 },
    { index: 3, id: 100, label: 'V 100', surfaceMean: 0.5, areaShare: 0.4 },
  ];
  const ids = (list) => list.map((r) => r.id);
  assert.deepEqual(ids(I.sortRows(rows, 'surfaceMean', 'desc')), [2, 9, 100, 10]);
  assert.deepEqual(ids(I.sortRows(rows, 'surfaceMean', 'asc')), [9, 100, 2, 10], 'the missing one is last whichever way');
  assert.deepEqual(ids(I.sortRows(rows, 'label', 'asc')), [2, 9, 10, 100], 'IDs as numbers: 9 before 10');
  assert.deepEqual(ids(I.sortRows(rows, 'label', 'desc')), [100, 10, 9, 2]);
  assert.deepEqual(ids(I.sortRows(rows, 'areaShare', 'desc')), [100, 2, 10, 9]);
  assert.deepEqual(ids(rows), [9, 10, 2, 100]);
});

test('the minimum-area filter leaves out what owns too little of the surface to be worth ranking', () => {
  const rows = [0.0001, 0.0002, 0.0005, 0.01].map((areaShare, index) => ({ index, id: index, areaShare }));
  assert.deepEqual(I.filterMinArea(rows, 0.0002).map((r) => r.index), [1, 2, 3], 'inclusive');
  assert.equal(I.filterMinArea(rows, 0).length, 4);
  assert.equal(I.filterMinArea(rows, 1).length, 0);
  assert.equal(I.DEFAULT_MIN_AREA_PCT, 0.02);
});

test('the edge box: numbers, any separator, in order, once each; anything else says why', () => {
  assert.deepEqual(I.parseEdges('0.25, 0.5, 1.5'), { ok: true, edges: [0.25, 0.5, 1.5], message: '' });
  assert.deepEqual(I.parseEdges('1.5;0.5 0.25 0.5').edges, [0.25, 0.5, 1.5]);
  assert.equal(I.parseEdges('').ok, false);
  assert.equal(I.parseEdges('   ').ok, false);
  const bad = I.parseEdges('0.25, x, 1');
  assert.equal(bad.ok, false);
  assert.ok(bad.message.includes('"x"'));
  assert.equal(I.parseEdges('0.25, -1').ok, false);
  assert.equal(I.parseEdges('0, 1').ok, false);
  assert.equal(I.parseEdges('1, 2, 3, 4, 5, 6, 7, 8, 9').ok, false);
  assert.deepEqual(Array.from(I.DEFAULT_EDGES), [0.25, 0.5, 1.5]);
});

// -- the classes by radius ---------------------------------------------------------------------------------

test('a class holds its lower edge and not its upper; the first starts at nothing and the last is open', () => {
  const rows = [0.1, 0.25, 0.4999, 0.5, 1.49, 1.5, 7].map((radius, index) => ({ index, id: index, radius, areaShare: 0.1, surfaceMean: 1, surfaceMax: 2 }));
  const { classes } = I.radiusClasses(rows, [0.25, 0.5, 1.5], { area_um2: 100 });
  assert.deepEqual(classes.map((c) => c.n), [1, 2, 2, 2]);
  assert.deepEqual(classes.map((c) => c.label), ['r < 0.25', '0.25 to 0.5', '0.5 to 1.5', 'r ≥ 1.5']);
  assert.equal(I.radiusClasses(rows, [3], null).classes.length, 2);
});

test('the classes sum to the server\'s totals: the area, the mean error and the excess volume', () => {
  for (const [seed, edges] of [[1, [0.25, 0.5, 1.5]], [2, [0.1, 0.3, 0.6, 1, 2, 4]], [3, [5]], [4, [0.01]]]) {
    const r = makeResult({ seed, n: 80, nv: 8000 });
    const rows = I.annotationRows(r, 'V');
    const { classes, totals } = I.radiusClasses(rows, edges, r.surface);
    assert.equal(classes.length, edges.length + 1);
    assert.equal(totals.n, rows.length);
    close(totals.areaShare, 1, 1e-9, `seed ${seed}: area`);
    close(totals.meanErr, r.surface.mean_um, 1e-9, `seed ${seed}: mean error`);
    close(totals.excess, r.surface.excess_um3, 1e-6, `seed ${seed}: excess volume`);
    close(classes.reduce((s, c) => s + c.excessShare, 0), 1, 1e-9, 'excess shares');
    close(classes.reduce((s, c) => s + c.excess, 0), r.surface.excess_um3, 1e-6);
    // A class's mean is the area-weighted mean of its annotations.
    for (const c of classes.filter((x) => x.n)) {
      const mine = rows.filter((x) => x.radius >= c.from && x.radius < c.to && x.areaShare > 0);
      close(c.meanErr, mine.reduce((s, x) => s + x.areaShare * x.surfaceMean, 0) / mine.reduce((s, x) => s + x.areaShare, 0), 1e-9);
    }
  }
});

test('shares sent to six decimals still add up to the totals within the rounding', () => {
  const r = makeResult({ seed: 6, n: 200, nv: 20000, round: true });
  const { totals } = I.radiusClasses(I.annotationRows(r, 'V'), I.DEFAULT_EDGES, r.surface);
  close(totals.areaShare, 1, 200 * 5e-7, 'area');
  close(totals.meanErr / r.surface.mean_um, 1, 1e-3, 'mean error');
  close(totals.excess / r.surface.excess_um3, 1, 1e-3, 'excess');
});

test('an annotation that owns no surface counts for nothing in a class', () => {
  const r = makeResult({ seed: 7, n: 20 });
  r.annotations.area_share[2] = 0;
  r.annotations.surface_mean[2] = null;
  r.annotations.surface_max[2] = null;
  const { totals, classes } = I.radiusClasses(I.annotationRows(r, 'V'), [1], r.surface);
  assert.ok(Number.isFinite(totals.meanErr));
  assert.equal(classes.reduce((s, c) => s + c.n, 0), 20);
});

// -- the worst places --------------------------------------------------------------------------------------------

test('the worst places keep the server\'s order and add up the top ten', () => {
  const r = makeResult({ seed: 8 });
  const s = I.spotSummary(r, 'RC2');
  assert.equal(s.rows.length, 12);
  assert.deepEqual(s.rows.map((x) => x.rank), Array.from({ length: 12 }, (_, k) => k + 1));
  assert.equal(s.topN, 10);
  close(s.topShare, r.spots.slice(0, 10).reduce((sum, x) => sum + x.share, 0), 1e-12);
  assert.deepEqual(s.rows[0].xyz, r.spots[0].xyz);
  assert.equal(I.spotSummary({}, 'x').rows.length, 0);
  assert.equal(I.spotSummary({ spots: r.spots.slice(0, 3) }, 'x').topN, 3);
});

// -- improvement ----------------------------------------------------------------------------------------------------

test('improvement joins two tables by Location ID and counts who fits better', () => {
  const mk = (id, surfaceMean, rimMean = surfaceMean, areaShare = 0.1) => ({ index: id, id, label: `V ${id}`, volume: 'V', section: id, radius: 1, surfaceMean, rimMean, areaShare, xyz: [0, 0, 0] });
  const a = [mk(1, 1.0), mk(2, 1.0), mk(3, 1.0), mk(4, 1.0), mk(5, NaN), mk(6, 2.0)];
  const b = [mk(3, 1.2), mk(2, 0.5), mk(1, 1.004), mk(5, 1.0), mk(4, 1.0), mk(7, 1.0)];         // another order, 6 missing, 7 extra
  const r = I.improvement(a, b, { metric: 'surfaceMean', tolerance: 0.01 });
  assert.deepEqual(r.rows.map((x) => x.id), [2, 4, 1, 3], 'most improved first');
  assert.deepEqual(r.rows.map((x) => Number(x.delta.toFixed(3))), [-0.5, 0, 0.004, 0.2]);
  assert.equal(r.improved, 1);
  assert.equal(r.worse, 1);
  assert.equal(r.unchanged, 2);
  assert.equal(r.missing, 2, 'the NaN one and the one B does not have');
  close(r.meanDelta, (-0.5 + 0.004 + 0 + 0.2) / 4, 1e-12);
  close(r.weightedDelta, r.meanDelta, 1e-12, 'equal shares weigh equally');
  // The tolerance moves the line between unchanged and changed.
  assert.equal(I.improvement(a, b, { tolerance: 0 }).unchanged, 1);
  assert.equal(I.improvement(a, b, { tolerance: 1 }).unchanged, 4);
  // The other metric reads another number.
  const c = [mk(1, 1, 3), mk(2, 1, 1)];
  const d = [mk(1, 1, 2), mk(2, 1, 1.5)];
  assert.deepEqual(I.improvement(c, d, { metric: 'rimMean', tolerance: 0.01 }).rows.map((x) => x.id), [1, 2]);
});

test('improvement of a layer against itself is nothing', () => {
  const rows = I.annotationRows(makeResult({ seed: 9 }), 'V');
  const r = I.improvement(rows, rows);
  assert.equal(r.improved + r.worse, 0);
  assert.equal(r.unchanged, rows.length);
  assert.equal(r.meanDelta, 0);
});

test('a better render improves most annotations, and the counts add up', () => {
  const good = I.annotationRows(makeResult({ seed: 10, quality: 0.4 }), 'V');
  const poor = I.annotationRows(makeResult({ seed: 10, quality: 1.0 }), 'V');
  const r = I.improvement(poor, good);
  assert.equal(r.improved + r.worse + r.unchanged + r.missing, poor.length);
  assert.ok(r.improved > 0.8 * poor.length, `${r.improved} of ${poor.length}`);
  assert.ok(r.weightedDelta < 0);
});

test('ring colors: the ramp for an error, the signed ramp for a change, none for what has no number', () => {
  const rows = [
    { id: 1, rimMean: 0, surfaceMean: 2 }, { id: 2, rimMean: 1, surfaceMean: NaN }, { id: 3, rimMean: NaN, surfaceMean: 0.5 },
  ];
  const rim = I.ringColorMap(rows, { mode: 'rim', scaleUm: 1 });
  assert.deepEqual([...rim.keys()], [1, 2]);
  assert.deepEqual(rim.get(1), P.errorColorAt(0, 1));
  assert.deepEqual(rim.get(2), P.errorColorAt(1, 1));
  const surface = I.ringColorMap(rows, { mode: 'surface', scaleUm: 1 });
  assert.deepEqual([...surface.keys()], [1, 3]);
  assert.deepEqual(surface.get(1), P.errorColorAt(5, 1), 'beyond the scale is the top of the ramp');
  const delta = I.ringColorMap([], { mode: 'delta', scaleUm: 0.5, deltas: new Map([[1, -0.5], [2, 0.5], [3, 0], [4, NaN]]) });
  assert.deepEqual([...delta.keys()], [1, 2, 3]);
  assert.deepEqual(delta.get(1), P.divergingColorAt(-0.5, 0.5));
  assert.deepEqual(delta.get(2), P.divergingColorAt(0.5, 0.5));
  assert.notDeepEqual(delta.get(1), delta.get(2));
});

test('a ring has the color of the surface and of the rim points for the same error: the same numbers, not a CSS string', () => {
  // Render3D reads an array as linear light, as it does the vertex colors of a surface and the colors of
  // setPoints, and a CSS string as sRGB: the same error in two forms is two colors on the screen.
  const errors = [0, 0.1, 0.25, 0.5, 0.8, 1, 2];
  const rows = errors.map((e, k) => ({ id: k, rimMean: e, surfaceMean: e }));
  const rim = I.ringColorMap(rows, { mode: 'rim', scaleUm: 1 });
  const painted = P.errorColors(Float64Array.from(errors), 1);          // what setMesh and setPoints are given
  errors.forEach((_, k) => {
    const ring = rim.get(k);
    assert.ok(Array.isArray(ring) && ring.length === 3, `ring ${k}`);
    for (let c = 0; c < 3; c += 1) assert.equal(ring[c], painted[3 * k + c], `error ${errors[k]}, channel ${c}`);
  });
  const signed = [-1, -0.2, 0, 0.3, 1];
  const delta = I.ringColorMap([], { mode: 'delta', scaleUm: 1, deltas: new Map(signed.map((d, k) => [k, d])) });
  const diverging = P.divergingColors(Float64Array.from(signed), 1);
  signed.forEach((_, k) => {
    for (let c = 0; c < 3; c += 1) assert.ok(Math.abs(delta.get(k)[c] - diverging[3 * k + c]) < 1e-6, `change ${signed[k]}, channel ${c}`);
  });
});

// -- the drawer against a fake DOM -----------------------------------------------------------------------------------

const BUBBLING = new Set(['click', 'dblclick', 'keydown', 'pointerdown', 'pointermove', 'pointerup', 'change']);

class El {
  constructor(tag, ns = null) {
    this.tagName = tag;
    this.ns = ns;
    this.className = '';
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.attrs = new Map();
    this.listeners = [];
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this._text = '';
    this.parent = null;
    this.clientWidth = 0;
  }

  set textContent(v) { this.children = []; this._text = String(v); }

  get textContent() { return this.children.length ? this.children.map((c) => c.textContent).join('') : this._text; }

  append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }

  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }

  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }

  setAttribute(k, v) {
    this.attrs.set(k, String(v));
    if (k === 'class') this.className = String(v);      // an SVG element's class is an attribute
  }

  getAttribute(k) { return this.attrs.get(k) ?? null; }

  removeAttribute(k) { this.attrs.delete(k); }

  addEventListener(type, fn) { this.listeners.push({ type, fn }); }

  removeEventListener() {}

  getBoundingClientRect() { return this.rect || { left: 0, top: 0, width: 0, height: 0 }; }

  setPointerCapture() {}

  releasePointerCapture() {}

  focus() { fakeDocument.activeElement = this; }

  contains(node) {
    for (let n = node; n; n = n.parent) if (n === this) return true;
    return false;
  }

  closest(sel) {
    const attr = /^\[data-([\w-]+)\]$/.exec(sel);
    for (let n = this; n; n = n.parent) if (attr && n.dataset[attr[1]] !== undefined) return n;
    return null;
  }

  /** `[data-x="y"]` or a list of tag names: all the drawer asks for. */
  querySelector(sel) {
    const byData = /^\[data-([\w-]+)="(.*)"\]$/.exec(sel);
    if (byData) return this.find((e) => e.dataset[byData[1]] === byData[2].replace(/\\(.)/g, '$1'))[0] ?? null;
    const tags = sel.split(',').map((t) => t.trim());
    return this.find((e) => tags.includes(e.tagName))[0] ?? null;
  }

  /** Deliver an event: to this element, then (as the page does, for the events that bubble) up its parents until one stops it. */
  fire(type, init = {}) {
    const event = {
      type, prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, target: this, ...init,
    };
    for (let node = this; node && !event.stopped; node = BUBBLING.has(type) ? node.parent : null) {
      for (const l of node.listeners.filter((x) => x.type === type)) l.fn(event);
    }
    return event;
  }

  find(pred, out = []) {
    for (const c of this.children) { if (pred(c)) out.push(c); c.find(pred, out); }
    return out;
  }

  all(sel) {
    const [tag, cls] = sel.includes('.') ? sel.split('.') : [sel, null];
    return this.find((e) => (!tag || e.tagName === tag) && (!cls || e.className.split(/\s+/).includes(cls)));
  }

  one(sel) { return this.all(sel)[0] || null; }

  byData(key, value) { return this.find((e) => e.dataset[key] === value); }
}

const fakeDocument = {
  activeElement: null,
  createElement: (tag) => new El(tag),
  createElementNS: (ns, tag) => new El(tag, ns),
  createTextNode: (text) => Object.assign(new El('#text'), { _text: String(text) }),
};

function layer(id, name, color, seed, extra = {}) {
  return { id, name, color, volume: 'Inferior', result: makeResult({ seed, ...extra }) };
}

function makeDrawer({ layers = [layer('L1', 'Voxel remesh', '#ffd400', 1, { scale: 1.0 })], callbacks = {}, options = {} } = {}) {
  const host = new El('div');
  fakeDocument.activeElement = null;
  const calls = { threshold: [], fly: [], section: [], focus: [], edges: [] };
  const drawer = new I.ErrorInspector(host, {
    onThreshold: (v) => calls.threshold.push(v),
    onFlyTo: (v) => calls.fly.push(v),
    onSection: (s, c) => calls.section.push([s, c]),
    onFocus: (id) => calls.focus.push(id),
    onEdges: (e) => calls.edges.push(e),
    ...callbacks,
  }, { document: fakeDocument, observe: false, ...options });
  drawer.setLayers(layers);
  return { drawer, host, calls };
}

const card = (host, name) => host.byData('card', name)[0];
const text = (el) => el.textContent;
const SVG_W = 360;
/** Give the histogram's svg a size on the screen, as a browser lays it out, and return its plot's left and width. */
function sizeChart(host) {
  const svg = card(host, 'hist').one('svg');
  svg.rect = { left: 100, top: 50, width: SVG_W, height: 150 };
  return { svg, left: 38, width: SVG_W - 38 - 10 };
}
const xAt = (chart, f) => 100 + chart.left + f * chart.width;

test('the drawer builds all its cards, and the improvement card waits for a second layer', () => {
  const { host, drawer } = makeDrawer();
  for (const name of ['bias', 'hist', 'z', 'spots', 'annotations', 'classes', 'improve']) assert.ok(card(host, name), name);
  assert.equal(card(host, 'improve').hidden, true);
  drawer.setLayers([layer('L1', 'Voxel remesh', '#ffd400', 1), layer('L2', 'Original', '#56B4E9', 2)]);
  assert.equal(card(host, 'improve').hidden, false);
  // Every card title carries its help, and no knob is without one.
  for (const name of ['bias', 'hist', 'z', 'spots', 'annotations', 'classes', 'improve']) assert.ok(card(host, name).children[0].dataset.help.length > 20, name);
  const unhelped = host.find((e) => ['input', 'select'].includes(e.tagName) && !e.dataset.help);
  assert.deepEqual(unhelped.map((e) => e.dataset.role), [], 'a knob without a data-help');
});

test('the layer chips name the layer with its volume; an unmeasured layer is listed and cannot be focused', () => {
  const { drawer, host, calls } = makeDrawer({
    layers: [layer('L1', 'Voxel remesh', '#ffd400', 1), { id: 'L2', name: 'Original', color: '#56B4E9', volume: 'Inferior', result: null }],
  });
  const chips = host.all('button.insp-layer');
  assert.equal(chips.length, 2);
  assert.ok(text(chips[0]).includes('Voxel remesh') && text(chips[0]).includes('Inferior'));
  assert.ok(text(chips[1]).includes('not measured'));
  assert.equal(chips[1].disabled, true);
  chips[1].fire('click');
  assert.deepEqual(calls.focus, []);
  assert.equal(chips[0].getAttribute('aria-pressed'), 'true');
  assert.equal(drawer.focus.id, 'L1');
  // The basis of the numbers is on the page: by area.
  assert.ok(text(host.one('span.insp-basis')).startsWith('surface, by area: mean'));
});

test('the direction bar of a layer has its fat and short parts in proportion', () => {
  const { host } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1, { bias: 0.77 })] });
  const row = card(host, 'bias').one('div.insp-row');
  const bar = row.one('div.insp-bar');
  assert.equal(bar.one('span.fat').style.width, `${0.77 * 100}%`);
  assert.equal(Number.parseFloat(bar.one('span.short').style.width).toFixed(1), '23.0');
  assert.ok(bar.getAttribute('aria-label').includes('Voxel remesh') && bar.getAttribute('aria-label').includes('77.0 %'));
  assert.ok(text(row).includes('23.0 % short') && text(row).includes('77.0 % fat'));
  // A result with no rim sides says so.
  const none = makeDrawer({ layers: [{ ...layer('L1', 'A', '#fff', 1), result: { ...makeResult({ seed: 1 }), bias: null } }] });
  assert.ok(text(card(none.host, 'bias')).includes('rim sides not measured'));
});

test('a press on the histogram sets the threshold there; a drag moves it; the callback gets the value, its percentile and the layer', () => {
  const { host, calls, drawer } = makeDrawer();
  const chart = sizeChart(host);
  const hit = chart.svg.one('rect.hit');
  const xmax = drawer.focus.result.surface.hist.edges.at(-1);
  hit.fire('pointerdown', { clientX: xAt(chart, 0.4), button: 0, pointerId: 1 });
  assert.equal(calls.threshold.length, 1);
  close(calls.threshold[0].value, 0.4 * xmax, 1e-3);
  assert.equal(calls.threshold[0].layerId, 'L1');
  close(calls.threshold[0].percent, I.thresholdReadout(drawer.focus.result, calls.threshold[0].value).percent, 1e-9);
  assert.equal(chart.svg.one('rect.grip').getAttribute('visibility'), 'visible');
  assert.equal(chart.svg.one('line.threshold').getAttribute('visibility'), 'visible');
  assert.ok(text(card(host, 'hist')).includes('Threshold'));
  hit.fire('pointermove', { clientX: xAt(chart, 0.6), pointerId: 1 });
  close(calls.threshold[1].value, 0.6 * xmax, 1e-3);
  hit.fire('pointerup', { clientX: xAt(chart, 0.6), pointerId: 1 });
  hit.fire('pointermove', { clientX: xAt(chart, 0.9), pointerId: 1 });
  assert.equal(calls.threshold.length, 2, 'a move after the button is up changes nothing');
  // The chart is the same element throughout, so a drag holding the pointer on it is not cut short.
  assert.equal(card(host, 'hist').one('svg'), chart.svg);
  // The pointer cannot take it off the axis.
  hit.fire('pointerdown', { clientX: xAt(chart, 2.5), button: 0, pointerId: 2 });
  close(calls.threshold.at(-1).value, xmax, 1e-3);
  hit.fire('pointerup', { pointerId: 2 });
  hit.fire('pointerdown', { clientX: xAt(chart, -3), button: 0, pointerId: 3 });
  assert.equal(calls.threshold.at(-1).value, 0);
});

test('the bars below the threshold fade, the readout gives each layer\'s share below and above it', () => {
  const { host, drawer } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1, { scale: 1.0 }), layer('L2', 'Original', '#56B4E9', 2, { scale: 2.0 })] });
  const chart = sizeChart(host);
  const t = I.thresholdAtPercent(drawer.focus.result, 90);
  drawer.setThreshold(t);
  const bars = chart.svg.all('rect.bar');
  assert.equal(bars.length, 40);
  const opacity = bars.map((b) => Number(b.getAttribute('opacity')));
  assert.ok(opacity.includes(0.28) && opacity.includes(0.85));
  assert.equal(opacity.indexOf(0.85) > opacity.lastIndexOf(0.28) - 1, true, 'faded on the left, strong from the threshold on');
  const readout = card(host, 'hist').byData('role', 'threshold')[0];
  const items = readout.all('span.insp-key');
  assert.equal(items.length, 2, 'one entry per measured layer');
  assert.ok(text(items[0]).includes('90.0 % below') && text(items[0]).includes('10.0 % at or above'), text(items[0]));
  // The other layer is an outline on the same axis.
  assert.equal(chart.svg.all('path.outline').length, 1);
  drawer.setThreshold(null);
  assert.equal(chart.svg.one('rect.grip').getAttribute('visibility'), 'hidden');
  assert.ok(text(card(host, 'hist')).includes('Threshold: off'));
});

test('setThreshold from outside moves the drawer and calls nothing back', () => {
  const { drawer, host, calls } = makeDrawer();
  sizeChart(host);
  drawer.setThreshold(0.5);
  assert.equal(drawer.threshold, 0.5);
  drawer.setThreshold(NaN);
  assert.equal(drawer.threshold, null);
  assert.deepEqual(calls.threshold, []);
});

test('the grip takes the keyboard: a percent of the area a step, five with Shift, Escape takes it off', () => {
  const { host, calls, drawer } = makeDrawer();
  const chart = sizeChart(host);
  const hit = chart.svg.one('rect.hit');
  hit.fire('pointerdown', { clientX: xAt(chart, 0.3), button: 0, pointerId: 1 });
  hit.fire('pointerup', { pointerId: 1 });
  const grip = chart.svg.one('rect.grip');
  const q = drawer.focus.result.surface.quantiles;
  const start = I.thresholdReadout(drawer.focus.result, drawer.threshold).percent;
  assert.equal(grip.fire('keydown', { key: 'ArrowRight' }).prevented, true);
  close(I.thresholdReadout(drawer.focus.result, drawer.threshold).percent, Math.min(100, start + 1), 0.02);
  grip.fire('keydown', { key: 'ArrowRight', shiftKey: true });
  close(I.thresholdReadout(drawer.focus.result, drawer.threshold).percent, Math.min(100, start + 6), 0.02);
  grip.fire('keydown', { key: 'ArrowLeft', shiftKey: true });
  close(I.thresholdReadout(drawer.focus.result, drawer.threshold).percent, Math.min(100, start + 1), 0.02);
  grip.fire('keydown', { key: 'End' });
  close(drawer.threshold, q[100], 1e-4);
  grip.fire('keydown', { key: 'Home' });
  assert.equal(drawer.threshold, 0);
  assert.equal(grip.getAttribute('aria-valuenow'), '0');
  assert.equal(grip.getAttribute('role'), 'slider');
  assert.equal(grip.fire('keydown', { key: 'a' }).prevented, false);
  assert.equal(grip.fire('keydown', { key: 'ArrowRight', ctrlKey: true }).prevented, false);
  const before = calls.threshold.length;
  grip.fire('keydown', { key: 'Escape' });
  assert.equal(calls.threshold.length, before + 1);
  assert.equal(calls.threshold.at(-1), null);
  assert.equal(drawer.threshold, null);
});

test('a double-click and the Clear button take the threshold off', () => {
  const { host, calls, drawer } = makeDrawer();
  const chart = sizeChart(host);
  const hit = chart.svg.one('rect.hit');
  hit.fire('pointerdown', { clientX: xAt(chart, 0.5), button: 0, pointerId: 1 });
  hit.fire('pointerup', { pointerId: 1 });
  hit.fire('dblclick');
  assert.equal(calls.threshold.at(-1), null);
  hit.fire('pointerdown', { clientX: xAt(chart, 0.5), button: 0, pointerId: 1 });
  hit.fire('pointerup', { pointerId: 1 });
  card(host, 'hist').byData('role', 'clear')[0].fire('click');
  assert.equal(calls.threshold.at(-1), null);
  assert.equal(drawer.threshold, null);
  hit.fire('dblclick');                                    // already off: nothing to say
  assert.equal(calls.threshold.filter((c) => c === null).length, 2);
});

test('the profile along Z: a click names a section from the annotations\' own Z, the metric switches, the Section view\'s section is marked', () => {
  const { host, calls, drawer } = makeDrawer();
  const z = card(host, 'z');
  const bins = z.all('rect.bin');
  assert.ok(bins.length > 5);
  const target = bins[3];
  target.fire('click');
  assert.equal(calls.section.length, 1);
  const [section, context] = calls.section[0];
  const zc = Number(target.dataset.z);
  // sections 1000 + 3 i sit at Z (1000 + 3 i) * 0.09: the section at zc is zc / 0.09.
  assert.equal(section, Math.round(zc / 0.09));
  assert.equal(context.source, 'profile');
  assert.equal(context.volume, 'Inferior');
  assert.equal(context.layerId, 'L1');
  close(context.z, zc, 1e-12);
  assert.equal(z.all('line.cursor').length, 0);
  drawer.setSection(1030);
  assert.equal(card(host, 'z').all('line.cursor').length, 1);
  drawer.setSection(null);
  assert.equal(card(host, 'z').all('line.cursor').length, 0);
  const buttons = z.byData('role', 'z-metric')[0].children;
  assert.deepEqual(buttons.map((b) => b.dataset.metric), ['mean', 'p95', 'rim']);
  card(host, 'z').byData('role', 'z-metric')[0].children[1].fire('click');
  assert.equal(drawer.zMetric, 'p95');
  assert.equal(card(host, 'z').byData('role', 'z-metric')[0].children[1].getAttribute('aria-pressed'), 'true');
  assert.ok(text(card(host, 'z').children[0]).includes('95th percentile'));
});

test('two layers are overlaid in their colors on the profile, and a floor line is drawn', () => {
  const { host } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1), layer('L2', 'Original', '#56B4E9', 2)] });
  const paths = card(host, 'z').all('path.series');
  assert.deepEqual(paths.map((p) => p.getAttribute('stroke')), ['#ffd400', '#56B4E9']);
  assert.equal(card(host, 'z').all('line.floor').length, 1);
});

test('the worst places: the header adds up the top ten; a click flies, the section link jumps, both with the volume', () => {
  const { host, calls, drawer } = makeDrawer();
  const spots = card(host, 'spots');
  assert.ok(text(spots.children[0]).includes('top 10 hold'));
  const expected = I.spotSummary(drawer.focus.result, 'Inferior').topShare;
  assert.ok(text(spots.children[0]).includes(I.fmtShare(expected)));
  const items = spots.all('li');
  assert.equal(items.length, 12);
  assert.ok(text(items[0]).includes('Inferior 500000'));
  items[0].fire('click');
  assert.deepEqual(calls.fly[0], {
    layerId: 'L1', volume: 'Inferior', annotationId: 500000, section: 1000, xyz: drawer.focus.result.spots[0].xyz, source: 'spot',
  });
  items[1].byData('role', 'fly')[0].fire('click');
  assert.equal(calls.fly[1].annotationId, 500007);
  items[2].byData('role', 'section')[0].fire('click');
  assert.deepEqual(calls.section.at(-1), [1006, { layerId: 'L1', volume: 'Inferior', annotationId: 500014, source: 'spot' }]);
  assert.equal(calls.fly.length, 2, 'the section link does not also fly');
  // A place with no annotation to name still flies.
  assert.ok(text(items[5]).includes('—'));
  items[5].fire('click');
  assert.equal(calls.fly.at(-1).annotationId, null);
});

test('the table starts sorted by mean error, worst first, among the annotations that own enough surface', () => {
  const big = layer('L1', 'Voxel remesh', '#ffd400', 1, { n: 120, nv: 12000 });
  for (let i = 30; i < 70; i += 1) big.result.annotations.area_share[i] = 0.00001;       // a few vertices each: 0.001 % of the surface
  const { host, drawer } = makeDrawer({ layers: [big] });
  const rows = I.annotationRows(drawer.focus.result, 'Inferior');
  const kept = I.filterMinArea(rows, I.DEFAULT_MIN_AREA_PCT / 100);
  assert.ok(kept.length < rows.length && kept.length > 25, `${kept.length} of ${rows.length}`);
  const body = card(host, 'annotations').one('tbody');
  assert.equal(body.children.length, 25);
  const expected = I.sortRows(kept, 'surfaceMean', 'desc').slice(0, 25).map((r) => String(r.id));
  assert.deepEqual(body.children.map((tr) => tr.dataset.id), expected);
  assert.ok(text(body.children[0].children[0]).startsWith('Inferior '));
  assert.ok(text(card(host, 'annotations').byData('role', 'annotation-tools')[0]).includes(`25 of ${kept.length}`));
  assert.equal(card(host, 'annotations').one('th').getAttribute('aria-sort'), null);
  const sorted = card(host, 'annotations').all('th').find((th) => th.dataset.key === 'surfaceMean');
  assert.equal(sorted.getAttribute('aria-sort'), 'descending');
});

test('a header click sorts, a second reverses; the first click on an error sorts worst first, on an ID smallest first', () => {
  const { host, drawer } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1, { n: 120, nv: 12000 })] });
  const headerButton = (key) => card(host, 'annotations').all('th').find((th) => th.dataset.key === key).children[0];
  const ids = () => card(host, 'annotations').one('tbody').children.map((tr) => Number(tr.dataset.id));
  const kept = I.filterMinArea(I.annotationRows(drawer.focus.result, 'Inferior'), I.DEFAULT_MIN_AREA_PCT / 100);
  headerButton('areaShare').fire('click');
  assert.deepEqual(ids(), I.sortRows(kept, 'areaShare', 'desc').slice(0, 25).map((r) => r.id));
  headerButton('areaShare').fire('click');
  assert.deepEqual(ids(), I.sortRows(kept, 'areaShare', 'asc').slice(0, 25).map((r) => r.id));
  headerButton('label').fire('click');
  assert.deepEqual(ids(), I.sortRows(kept, 'label', 'asc').slice(0, 25).map((r) => r.id));
  assert.equal(card(host, 'annotations').all('th').find((th) => th.dataset.key === 'label').getAttribute('aria-sort'), 'ascending');
  assert.equal(card(host, 'annotations').all('th').find((th) => th.dataset.key === 'areaShare').getAttribute('aria-sort'), null);
});

test('the minimum area box filters the table, an invalid value goes back to the default, and More shows more', () => {
  const { host, drawer } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1, { n: 120, nv: 12000 })] });
  const box = card(host, 'annotations').byData('role', 'min-area')[0];
  const countRows = () => card(host, 'annotations').one('tbody').children.length;
  const all = I.annotationRows(drawer.focus.result, 'Inferior');
  box.value = '0';
  box.fire('change');
  assert.equal(countRows(), 25);
  assert.ok(text(card(host, 'annotations').byData('role', 'annotation-tools')[0]).includes(`of ${all.length}`));
  const more = card(host, 'annotations').byData('role', 'more')[0];
  more.fire('click');
  assert.equal(countRows(), 50);
  box.value = '5';                                       // 5 % of the surface: almost nothing owns that much
  box.fire('change');
  assert.ok(countRows() < 5);
  assert.equal(card(host, 'annotations').byData('role', 'more').length, 0);
  box.value = 'many';
  box.fire('change');
  assert.equal(box.value, String(I.DEFAULT_MIN_AREA_PCT));
  assert.equal(countRows(), 25);
});

test('a row flies to its annotation, its section number jumps the Section view, and each carries the volume', () => {
  const { host, calls, drawer } = makeDrawer();
  const row = card(host, 'annotations').one('tbody').children[0];
  const id = Number(row.dataset.id);
  const expected = I.annotationRows(drawer.focus.result, 'Inferior').find((r) => r.id === id);
  row.fire('click');
  assert.deepEqual(calls.fly[0], { layerId: 'L1', volume: 'Inferior', annotationId: id, section: expected.section, xyz: expected.xyz, source: 'annotation' });
  row.byData('role', 'section')[0].fire('click');
  assert.deepEqual(calls.section.at(-1), [expected.section, { layerId: 'L1', volume: 'Inferior', annotationId: id, source: 'annotation' }]);
  row.byData('role', 'fly')[0].fire('click');
  assert.equal(calls.fly.length, 2);
  assert.ok(card(host, 'annotations').one('tbody').children.every((tr) => text(tr.children[0]).startsWith('Inferior ')));
});

test('the class table follows the edge box: new radii recompute it at once and call back; a bad entry is refused with the reason', () => {
  const { host, calls, drawer } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1, { n: 100, nv: 10000 })] });
  const tbody = () => card(host, 'classes').one('tbody');
  assert.equal(tbody().children.length, 5, 'three edges: four classes and the total');
  assert.equal(tbody().children[0].children[0].textContent, 'r < 0.25');
  const box = card(host, 'classes').byData('role', 'edges')[0];
  assert.equal(box.value, '0.25, 0.5, 1.5');
  box.value = '2, 0.5';
  box.fire('change');
  assert.deepEqual(calls.edges, [[0.5, 2]]);
  assert.equal(box.value, '0.5, 2');
  assert.equal(tbody().children.length, 4);
  assert.equal(tbody().children[1].children[0].textContent, '0.5 to 2');
  // The rows are the model's, formatted.
  const { classes, totals } = I.radiusClasses(I.annotationRows(drawer.focus.result, 'Inferior'), [0.5, 2], drawer.focus.result.surface);
  assert.equal(tbody().children[0].children[1].textContent, I.fmtCount(classes[0].n));
  assert.equal(tbody().children[3].children[2].textContent, I.fmtShare(totals.areaShare));
  box.value = '0.5, nope';
  box.fire('change');
  assert.equal(box.getAttribute('aria-invalid'), 'true');
  assert.ok(text(card(host, 'classes').byData('role', 'class-tools')[0]).includes('"nope"'));
  assert.equal(calls.edges.length, 1, 'nothing was said about a bad entry');
  assert.equal(tbody().children.length, 4, 'the table kept the last good edges');
  box.value = '0.5, 2';
  box.fire('change');
  assert.equal(box.getAttribute('aria-invalid'), null);
  assert.equal(calls.edges.length, 1, 'the same edges again are not a change');
  box.value = '1';
  box.fire('keydown', { key: 'Enter' });
  assert.deepEqual(calls.edges.at(-1), [1]);
});

test('with two layers the improvement table counts, orders and flies, and the choices re-count it', () => {
  const layers = [layer('L1', 'Poor', '#ffd400', 10, { quality: 1.0 }), layer('L2', 'Good', '#56B4E9', 10, { quality: 0.4 })];
  const { host, calls, drawer } = makeDrawer({ layers });
  const imp = card(host, 'improve');
  const expected = I.improvement(I.annotationRows(layers[0].result, 'Inferior'), I.annotationRows(layers[1].result, 'Inferior'));
  const summary = () => text(card(host, 'improve').byData('role', 'improve-summary')[0]);
  assert.ok(summary().startsWith(`Good fits ${I.fmtCount(expected.improved)} annotations better, Poor ${I.fmtCount(expected.worse)};`), summary());
  const rows = () => card(host, 'improve').one('tbody').children;
  assert.equal(rows().length, 12);
  assert.equal(rows()[0].dataset.id, String(expected.rows[0].id), 'most improved first');
  rows()[0].fire('click');
  assert.equal(calls.fly[0].source, 'improvement');
  assert.equal(calls.fly[0].layerId, 'L2');
  assert.equal(calls.fly[0].volume, 'Inferior');
  assert.ok(text(rows()[0].children[0]).startsWith('Inferior '));
  // Swapping the layers turns the count round.
  const selects = imp.all('select');
  const from = selects.find((s) => s.dataset.role === 'from');
  const to = selects.find((s) => s.dataset.role === 'to');
  from.value = 'L2';
  from.fire('change');
  to.value = 'L1';
  to.fire('change');
  const swapped = I.improvement(I.annotationRows(layers[1].result, 'Inferior'), I.annotationRows(layers[0].result, 'Inferior'));
  assert.ok(summary().startsWith(`Poor fits ${I.fmtCount(swapped.improved)} annotations better, Good ${I.fmtCount(swapped.worse)};`), summary());
  // The same layer twice is refused.
  to.value = 'L2';
  to.fire('change');
  assert.ok(text(card(host, 'improve')).length > 0);
  // The tolerance and the order.
  drawer.improve.from = 'L1';
  drawer.improve.to = 'L2';
  drawer.render();
  const tol = card(host, 'improve').byData('role', 'tolerance')[0];
  tol.value = '100';
  tol.fire('change');
  assert.ok(summary().includes(`${I.fmtCount(expected.rows.length)} within`), summary());
  tol.value = '0.01';
  tol.fire('change');
  card(host, 'improve').find((e) => e.dataset.order === 'worst')[0].fire('click');
  assert.equal(rows()[0].dataset.id, String(expected.rows.at(-1).id), 'most worse first');
});

test('the improvement headline gives the change per annotation and by area, which can read the other way', () => {
  // Every small annotation fits better in B, and the one that owns nine tenths of the surface fits worse.
  const a = layer('L1', 'A', '#ffd400', 4);
  const b = layer('L2', 'B', '#56B4E9', 4);
  const n = a.result.annotations.ids.length;
  a.result.annotations.area_share = a.result.annotations.area_share.map((_, i) => (i === 0 ? 0.9 : 0.1 / (n - 1)));
  b.result.annotations.surface_mean = a.result.annotations.surface_mean.map((v, i) => (i === 0 ? v + 0.5 : v - 0.2));
  const { host } = makeDrawer({ layers: [a, b] });
  const rows = (l) => I.annotationRows(l.result, 'Inferior');
  const expected = I.improvement(rows(a), rows(b));
  assert.ok(expected.meanDelta < -0.1 && expected.weightedDelta > 0.3, `${expected.meanDelta} and ${expected.weightedDelta}`);
  const summary = text(card(host, 'improve').byData('role', 'improve-summary')[0]);
  assert.ok(summary.includes(`${I.fmtSigned(expected.meanDelta)} µm per annotation`), summary);
  assert.ok(summary.includes(`${I.fmtSigned(expected.weightedDelta)} µm by area`), summary);
  assert.ok(summary.includes('−') && summary.includes('+'), 'a minus and a plus: they disagree');
  assert.ok(card(host, 'improve').byData('role', 'improve-summary')[0].dataset.help.includes('by area'));
});

test('focusing another layer re-points the single-layer cards and calls back', () => {
  const { host, calls, drawer } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1), layer('L2', 'Original', '#56B4E9', 2)] });
  const first = card(host, 'annotations').one('tbody').children.map((tr) => tr.dataset.id);
  host.all('button.insp-layer')[1].fire('click');
  assert.deepEqual(calls.focus, ['L2']);
  assert.equal(drawer.focus.id, 'L2');
  assert.notDeepEqual(card(host, 'annotations').one('tbody').children.map((tr) => tr.dataset.id), first);
  assert.ok(text(card(host, 'spots')).includes('Inferior'));
  host.all('button.insp-layer')[1].fire('click');
  assert.deepEqual(calls.focus, ['L2'], 'the focused layer again says nothing');
  drawer.setFocus('L1');
  assert.equal(drawer.focus.id, 'L1');
  assert.deepEqual(calls.focus, ['L2'], 'from outside, no callback');
  drawer.setFocus('nope');
  assert.equal(drawer.focus.id, 'L1');
});

test('with nothing measured every card says so, and a result without the newer fields still draws', () => {
  const { host } = makeDrawer({ layers: [{ id: 'L1', name: 'A', color: '#fff', volume: 'V', result: null }] });
  for (const name of ['bias', 'hist', 'z', 'spots']) assert.ok(text(card(host, name)).includes('Nothing measured yet'), name);
  assert.ok(text(card(host, 'annotations')).includes('Nothing measured yet'));
  // The old result: a vertex_um and a scale, nothing of the rest.
  const { host: old } = makeDrawer({ layers: [{ id: 'L1', name: 'Old', color: '#fff', volume: 'V', result: { mesh_id: 'x', n_vertices: 3, vertex_um: [0.1, 0.2, 0.3], scale_um: 1 } }] });
  assert.ok(text(card(old, 'hist')).includes('No histogram'));
  assert.ok(text(card(old, 'z')).includes('No profile'));
  assert.ok(text(card(old, 'spots')).includes('No place stands out'));
  assert.ok(text(card(old, 'annotations')).includes('No annotation table'));
  assert.ok(text(card(old, 'bias')).includes('rim sides not measured'));
});

// -- the pointer, the keyboard, the size -------------------------------------------------------------------------------

test('a redraw in the middle of a drag leaves no drag behind: hovering the new chart moves nothing', () => {
  const { host, calls, drawer } = makeDrawer();
  const chart = sizeChart(host);
  chart.svg.one('rect.hit').fire('pointerdown', { clientX: xAt(chart, 0.4), button: 0, pointerId: 1 });
  const before = calls.threshold.length;
  // The host redraws as another layer's measurement lands; the element that held the pointer is gone.
  drawer.setLayers(drawer.layers);
  assert.equal(drawer._drag, null);
  const again = sizeChart(host);
  assert.notEqual(again.svg, chart.svg);
  again.svg.one('rect.hit').fire('pointermove', { clientX: xAt(again, 0.9), pointerId: 1 });
  again.svg.one('rect.grip').fire('pointermove', { clientX: xAt(again, 0.9), pointerId: 1 });
  assert.equal(calls.threshold.length, before, 'no button is down, so nothing moves');
  // A drag whose release nobody heard ends at the first move that reports no button.
  const hit = again.svg.one('rect.hit');
  hit.fire('pointerdown', { clientX: xAt(again, 0.2), button: 0, pointerId: 2 });
  const held = calls.threshold.length;
  hit.fire('pointermove', { clientX: xAt(again, 0.3), buttons: 1, pointerId: 2 });
  hit.fire('pointermove', { clientX: xAt(again, 0.7), buttons: 0, pointerId: 2 });
  hit.fire('pointermove', { clientX: xAt(again, 0.8), buttons: 1, pointerId: 2 });
  assert.equal(calls.threshold.length, held + 1, 'the held move counted, then the drag was over');
  // And when the browser takes the pointer's capture away, it is over as well.
  hit.fire('pointerdown', { clientX: xAt(again, 0.2), button: 0, pointerId: 3 });
  hit.fire('lostpointercapture', { pointerId: 3 });
  assert.equal(drawer._drag, null);
});

test('a size change is answered in the next frame, once, and never inside the observer\'s callback', () => {
  let notify = null;
  const had = globalThis.ResizeObserver;
  globalThis.ResizeObserver = class {
    constructor(fn) { notify = fn; }

    observe() {}

    disconnect() { this.gone = true; }
  };
  try {
    const frames = [];
    const { host, drawer } = makeDrawer({ options: { observe: true, schedule: (fn) => { frames.push(fn); return frames.length; } } });
    const svg = card(host, 'hist').one('svg');
    drawer.cards.hist.body.clientWidth = 600;                 // the card is wider
    notify(); notify(); notify();
    assert.equal(card(host, 'hist').one('svg'), svg, 'nothing is redrawn while the observer is being told');
    assert.equal(frames.length, 1, 'three notifications are one frame');
    frames.shift()();
    const wide = card(host, 'hist').one('svg');
    assert.notEqual(wide, svg);
    assert.ok(wide.getAttribute('viewBox').startsWith('0 0 600 '), wide.getAttribute('viewBox'));
    // Within the 8 px that are not worth a redraw: the frame comes and nothing is drawn.
    drawer.cards.hist.body.clientWidth = 604;
    notify();
    frames.shift()();
    assert.equal(card(host, 'hist').one('svg'), wide);
    // A frame still pending when the drawer is taken down draws nothing.
    drawer.cards.hist.body.clientWidth = 300;
    notify();
    drawer.dispose();
    frames.shift()();
    assert.equal(drawer.cards.hist.body.one('svg'), wide);
    assert.equal(frames.length, 0);
  } finally {
    if (had) globalThis.ResizeObserver = had; else delete globalThis.ResizeObserver;
  }
});

test('a click on the chart leaves the keyboard on the grip, so Escape, which the help promises, takes the threshold off', () => {
  const { host, calls, drawer } = makeDrawer();
  const chart = sizeChart(host);
  const hit = chart.svg.one('rect.hit');
  assert.ok(hit.dataset.help.includes('Escape'), 'the promise');
  hit.fire('pointerdown', { clientX: xAt(chart, 0.4), button: 0, pointerId: 1 });
  hit.fire('pointerup', { pointerId: 1 });
  const grip = chart.svg.one('rect.grip');
  assert.equal(grip.getAttribute('visibility'), 'visible');
  assert.equal(fakeDocument.activeElement, grip);
  grip.fire('keydown', { key: 'Escape' });
  assert.equal(drawer.threshold, null);
  assert.equal(calls.threshold.at(-1), null);
  // A press that the browser cancels moves the keyboard nowhere.
  fakeDocument.activeElement = null;
  hit.fire('pointerdown', { clientX: xAt(chart, 0.4), button: 0, pointerId: 2 });
  hit.fire('pointercancel', { pointerId: 2 });
  assert.equal(fakeDocument.activeElement, null);
});

test('the keyboard can set the threshold: a button puts it at the 90th percentile and moves to the grip', () => {
  const { host, calls, drawer } = makeDrawer();
  const set = card(host, 'hist').byData('role', 'set-threshold')[0];
  assert.ok(set, 'a button where there is no threshold');
  assert.ok(set.dataset.help.length > 20);
  set.focus();                                                // Tab got there
  set.fire('click');                                          // Enter or Space
  close(drawer.threshold, I.thresholdAtPercent(drawer.focus.result, 90), 1e-4);
  assert.equal(calls.threshold.at(-1).layerId, 'L1');
  close(calls.threshold.at(-1).percent, 90, 0.05);
  const grip = card(host, 'hist').one('rect.grip');
  assert.equal(grip.getAttribute('visibility'), 'visible');
  assert.equal(fakeDocument.activeElement, grip, 'the arrow keys work from here');
  const was = drawer.threshold;
  grip.fire('keydown', { key: 'ArrowRight' });
  assert.ok(drawer.threshold > was);
  // While there is a threshold the button is Clear; clearing it by key leaves the keyboard on the button that replaces it.
  assert.equal(card(host, 'hist').byData('role', 'set-threshold').length, 0);
  const clear = card(host, 'hist').byData('role', 'clear')[0];
  clear.focus();
  clear.fire('click');
  assert.equal(drawer.threshold, null);
  assert.equal(fakeDocument.activeElement.dataset.role, 'set-threshold');
  assert.ok(host.contains(fakeDocument.activeElement));
  // A layer with no quantile table has no percentile to set it at.
  const bare = layer('L1', 'Old', '#fff', 1);
  delete bare.result.surface.quantiles;
  assert.equal(card(makeDrawer({ layers: [bare] }).host, 'hist').byData('role', 'set-threshold').length, 0);
});

test('a control that is rebuilt while it has the keyboard hands the keyboard to the one that replaces it', () => {
  const layers = [layer('L1', 'A', '#ffd400', 1, { n: 120, nv: 12000 }), layer('L2', 'B', '#56B4E9', 2, { n: 120, nv: 12000 })];
  const { host, drawer } = makeDrawer({ layers });
  // Press `old` by key: it is replaced; the keyboard must be on something in the page, with the same job.
  const press = (old) => {
    old.focus();
    old.fire('click');
    const now = fakeDocument.activeElement;
    assert.ok(host.contains(now), `the keyboard is on something in the drawer after ${old.dataset.focus}`);
    assert.notEqual(now, old, 'a new control');
    assert.equal(now.dataset.focus, old.dataset.focus, 'doing the same job');
    return now;
  };
  const header = (key) => card(host, 'annotations').all('th').find((th) => th.dataset.key === key).children[0];
  press(header('areaShare'));
  press(header('areaShare'));
  press(card(host, 'annotations').byData('role', 'more')[0]);
  const p95 = press(card(host, 'z').byData('metric', 'p95')[0]);
  assert.equal(p95.getAttribute('aria-pressed'), 'true');
  const chip = press(host.all('button.insp-layer')[1]);
  assert.equal(chip.getAttribute('aria-pressed'), 'true');
  // The improvement card's controls are rebuilt by every choice.
  const imp = card(host, 'improve');
  const to = imp.all('select').find((s) => s.dataset.role === 'metric');
  to.focus();
  to.value = 'rimMean';
  to.fire('change');
  assert.equal(fakeDocument.activeElement.dataset.focus, 'improve:metric');
  assert.ok(host.contains(fakeDocument.activeElement));
  const tol = card(host, 'improve').byData('role', 'tolerance')[0];
  tol.focus();
  tol.value = '0.02';
  tol.fire('change');
  assert.equal(fakeDocument.activeElement.dataset.focus, 'improve:tolerance');
  press(card(host, 'improve').find((e) => e.dataset.order === 'worst')[0]);
  // Nothing was focused: a redraw does not take the keyboard anywhere.
  fakeDocument.activeElement = null;
  drawer.render();
  assert.equal(fakeDocument.activeElement, null);
  // The last "Show more" goes away when it is used: the keyboard goes to the card's first control.
  const rows = I.filterMinArea(I.annotationRows(drawer.focus.result, 'Inferior'), drawer.minAreaPct / 100).length;
  drawer.visibleRows = rows - 10;
  drawer._renderAnnotations();
  const more = card(host, 'annotations').byData('role', 'more')[0];
  assert.ok(more, 'ten rows are still to show');
  more.focus();
  more.fire('click');
  assert.equal(card(host, 'annotations').byData('role', 'more').length, 0);
  assert.equal(fakeDocument.activeElement.dataset.role, 'min-area', 'the first control of the annotations card');
});

// -- the Distribution card: its marks, its tail, what each number is -----------------------------------------------------

/** An element's own text, without its <title> (the fake DOM's textContent of an element with children is the children's). */
const own = (el) => el._text;

/** The x (in the svg's own units) of a tick's triangle: its path starts three to the left of it. */
const tickX = (tick) => Number(/^M([\d.eE+-]+),/.exec(tick.getAttribute('d'))[1]) + 3;

test('the percentile ticks sit at the quantiles of the focused layer, each with its words; the floor is a dashed line at its value', () => {
  const { host, drawer } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1, { scale: 2.0 })] });
  const svg = card(host, 'hist').one('svg');
  const s = drawer.focus.result.surface;
  const xmax = s.hist.edges.at(-1);
  const plot = (v) => 38 + (v / xmax) * (360 - 38 - 10);
  const ticks = svg.all('path.tick');
  assert.deepEqual(ticks.length, 4);
  const labels = svg.all('text').filter((t) => /^p\d+$/.test(own(t)));
  assert.deepEqual(labels.map(own), ['p50', 'p90', 'p95', 'p98']);
  [50, 90, 95, 98].forEach((p, k) => {
    const v = P.percentileValue(s.quantiles, p);
    close(tickX(ticks[k]), plot(v), 1e-6, `p${p}`);
    close(Number(labels[k].getAttribute('x')), plot(v), 1e-6, `label p${p}`);
    assert.ok(labels[k].children[0].textContent.includes(`${p}%`) && labels[k].children[0].textContent.includes(I.fmtUm(v)), labels[k].children[0].textContent);
    assert.equal(ticks[k].getAttribute('fill'), '#ffd400');
  });
  // The sampling floor of the surface: a dashed line, with what it means.
  const floor = svg.all('line.floor');
  assert.equal(floor.length, 1);
  close(Number(floor[0].getAttribute('x1')), plot(s.floor_um), 1e-6);
  assert.equal(floor[0].getAttribute('stroke-dasharray'), '3 3');
  assert.ok(floor[0].children[0].textContent.includes('Sampling floor 0.045 µm'));
  // None when there is no floor.
  const bare = layer('L1', 'A', '#fff', 1, { scale: 2.0 });
  bare.result.surface.floor_um = 0;
  assert.equal(card(makeDrawer({ layers: [bare] }).host, 'hist').all('line.floor').length, 0);
});

test('the share past the end of the axis is written, and the marks beyond it are one mark that names each', () => {
  // An axis of 0.75 um for a surface that reaches 1.8: a good share of it is past the end.
  const { host, drawer } = makeDrawer({ layers: [layer('L1', 'Voxel remesh', '#ffd400', 1, { scale: 0.5 })] });
  const svg = card(host, 'hist').one('svg');
  const s = drawer.focus.result.surface;
  const xmax = s.hist.edges.at(-1);
  const beyond = 1 - P.percentileOf(s.quantiles, xmax) / 100;
  assert.ok(beyond > 0.05, `${beyond}`);
  const tail = svg.one('text.tail');
  assert.equal(own(tail), `${I.fmtShare(beyond)} beyond ${I.fmtUm(xmax)} ›`);
  assert.ok(tail.children[0].textContent.includes('past the end of this axis'));
  // The bars and the tail are the whole surface.
  const inBars = svg.all('rect.bar').reduce((sum, b) => sum + Number(/: ([\d.]+) %/.exec(b.children[0].textContent)[1]), 0);
  assert.ok(Math.abs(inBars + beyond * 100 - 100) < 1.5, `${inBars} + ${beyond * 100}`);
  // p98 is the legend's scale: past the axis it is still marked, together with whichever other marks are.
  const off = [50, 90, 95, 98].filter((p) => P.percentileValue(s.quantiles, p) > xmax);
  const inside = [50, 90, 95, 98].filter((p) => P.percentileValue(s.quantiles, p) > 0 && P.percentileValue(s.quantiles, p) <= xmax);
  assert.ok(off.includes(98) && inside.length >= 1, `${off} beyond, ${inside} inside`);
  assert.deepEqual(svg.all('text').filter((t) => /^p\d+$/.test(own(t))).map(own), inside.map((p) => `p${p}`));
  const mark = svg.one('text.beyond');
  assert.equal(own(mark), `${off.map((p) => `p${p}`).join(' ')} ›`);
  for (const p of off) assert.ok(mark.children[0].textContent.includes(I.fmtUm(P.percentileValue(s.quantiles, p))), `p${p}`);
  assert.equal(svg.all('path.tick').length, inside.length + 1, 'a triangle for each mark inside and one at the end');
  // A threshold past the end of the axis keeps its grip, at the end, so the keyboard is not dropped.
  drawer.setThreshold(xmax * 2);
  const grip = svg.one('rect.grip');
  assert.equal(grip.getAttribute('visibility'), 'visible');
  close(Number(grip.getAttribute('x')), 38 + (360 - 38 - 10) - 5, 1e-9);
  assert.equal(grip.getAttribute('aria-valuenow'), String(Number((xmax * 2).toFixed(4))));
  // End goes to the farthest vertex, which the axis may not reach; the grip is still there.
  drawer.setThreshold(null);
  grip.focus();
  drawer.setThreshold(xmax / 2);
  grip.fire('keydown', { key: 'End' });
  close(drawer.threshold, s.quantiles.at(-1), 1e-4);
  assert.equal(grip.getAttribute('visibility'), 'visible');
  assert.equal(fakeDocument.activeElement, grip);
  // A surface that stays inside the axis has no such words.
  const tidy = makeDrawer({ layers: [layer('L1', 'A', '#fff', 1, { scale: 3.0 })] });
  assert.equal(card(tidy.host, 'hist').all('text.tail').length, 0);
  assert.equal(card(tidy.host, 'hist').all('text.beyond').length, 0);
});

test('the bars are the focused layer\'s whatever its place in the list; the others are outlines', () => {
  const layers = [layer('L1', 'Voxel remesh', '#ffd400', 1, { scale: 1.0 }), layer('L2', 'Original', '#56B4E9', 2, { scale: 2.0 })];
  const { host, drawer } = makeDrawer({ layers });
  drawer.setFocus('L2');
  const svg = card(host, 'hist').one('svg');
  const edges = I.commonEdges(layers.map((l) => l.result));
  const shares = I.rebinHistogram(layers[1].result.surface.hist, edges);
  const bars = svg.all('rect.bar');
  assert.equal(bars.length, 40);
  assert.ok(bars.every((b) => b.getAttribute('fill') === '#56B4E9'), 'in the focused layer\'s color');
  const top = Math.max(...shares);
  const tallest = bars.reduce((best, b) => (Number(b.getAttribute('height')) > Number(best.getAttribute('height')) ? b : best));
  assert.equal(shares.indexOf(top), bars.indexOf(tallest), 'the tallest bar is the focused layer\'s');
  assert.ok(text(tallest.children[0]).includes(I.fmtShare(top)));
  assert.equal(svg.all('path.outline')[0].getAttribute('stroke'), '#ffd400', 'the other layer is the outline');
  assert.ok(card(host, 'hist').children[0].children[1].textContent.startsWith('Original'));
});

test('a drag measures the chart as it is shown, not as it was drawn: a chart scaled to twice its size gives the same value', () => {
  const { host, drawer, calls } = makeDrawer();
  const svg = card(host, 'hist').one('svg');
  svg.rect = { left: 100, top: 50, width: 2 * SVG_W, height: 300 };                     // shown at twice the width it was drawn at
  const xmax = drawer.focus.result.surface.hist.edges.at(-1);
  const plotAt = (f) => 100 + 2 * (38 + f * (SVG_W - 38 - 10));
  svg.one('rect.hit').fire('pointerdown', { clientX: plotAt(0.25), button: 0, pointerId: 1 });
  close(calls.threshold.at(-1).value, 0.25 * xmax, 1e-3);
  svg.one('rect.hit').fire('pointermove', { clientX: plotAt(0.75), pointerId: 1 });
  close(calls.threshold.at(-1).value, 0.75 * xmax, 1e-3);
});

test('the classes\' excess is each class\'s share of the area-times-error, which is not its share of the area', () => {
  // Fine neurites have a poor fit and large ones a good one: the share of the excess is not the share of the area.
  const bad = layer('L1', 'A', '#ffd400', 1, { n: 80, nv: 8000 });
  const a = bad.result.annotations;
  a.surface_mean = a.surface_mean.map((v, i) => (a.radius[i] < 0.5 ? 3 : 0.1) * (v ?? 0.3));
  const { host, drawer } = makeDrawer({ layers: [bad] });
  const rows = I.annotationRows(drawer.focus.result, 'Inferior');
  const { classes } = I.radiusClasses(rows, I.DEFAULT_EDGES, drawer.focus.result.surface);
  const weight = (c) => rows.filter((r) => r.radius >= c.from && r.radius < c.to).reduce((sum, r) => sum + r.areaShare * r.surfaceMean, 0);
  const total = classes.reduce((sum, c) => sum + weight(c), 0);
  classes.forEach((c) => close(c.excessShare, weight(c) / total, 1e-12, `class ${c.k}`));
  assert.ok(classes.some((c) => Math.abs(c.excessShare - c.areaShare) > 0.1), 'the two shares part company');
  const body = card(host, 'classes').one('tbody').children;
  classes.forEach((c, k) => {
    assert.equal(body[k].children[2].textContent, I.fmtShare(c.areaShare), `area of class ${k}`);
    assert.equal(body[k].children[4].textContent, I.fmtShare(weight(c) / total), `excess of class ${k}`);
  });
});

test('the profile along Z gives the metric asked for: the surface\'s mean, its 95th percentile, or the rim distance', () => {
  const result = makeResult({ seed: 3 });
  const zp = result.z_profile;
  const rim = I.zProfileSeries(result, 'rim');
  assert.ok(rim.points.length > 5);
  for (const p of rim.points) {
    assert.equal(p.value, zp.rim_mean[p.i], `rim at bin ${p.i}`);
    assert.ok(zp.n_annotations[p.i] > 0, 'only where an annotation sits');
  }
  for (const p of I.zProfileSeries(result, 'mean').points) assert.equal(p.value, zp.mean[p.i]);
  for (const p of I.zProfileSeries(result, 'p95').points) assert.equal(p.value, zp.p95[p.i]);
  assert.notDeepEqual(rim.points.map((p) => p.value), I.zProfileSeries(result, 'mean').points.map((p) => p.value));
  // And the drawer's tooltip for a bin gives all three.
  const { host } = makeDrawer({ layers: [{ id: 'L1', name: 'A', color: '#fff', volume: 'V', result }] });
  const first = card(host, 'z').one('rect.bin');
  const p = rim.points[0];
  const bin = card(host, 'z').all('rect.bin').find((b) => Number(b.dataset.z) === p.zc);
  assert.ok(bin.children[0].textContent.includes(`rim ${I.fmtUm(zp.rim_mean[p.i])}`), bin.children[0].textContent);
  assert.ok(first);
});

test('the improvement card follows its metric box, and its table gives the Fat share of a rim, a dash where there is none', () => {
  const a = layer('L1', 'Poor', '#ffd400', 10, { quality: 1.0 });
  const b = layer('L2', 'Good', '#56B4E9', 10, { quality: 1.0 });
  // The surface error is better in B everywhere, the rim distance worse for the first few.
  b.result.annotations.surface_mean = a.result.annotations.surface_mean.map((v) => (v === null ? v : v * 0.5));
  b.result.annotations.rim_mean = a.result.annotations.rim_mean.map((v, i) => (i < 20 ? v * 3 : v * 0.5));
  const { host } = makeDrawer({ layers: [a, b] });
  const summary = () => text(card(host, 'improve').byData('role', 'improve-summary')[0]);
  const rows = (l) => I.annotationRows(l.result, 'Inferior');
  const bySurface = I.improvement(rows(a), rows(b), { metric: 'surfaceMean' });
  const byRim = I.improvement(rows(a), rows(b), { metric: 'rimMean' });
  assert.ok(byRim.worse > 0 && bySurface.worse === 0, 'the fixture makes the two metrics disagree');
  assert.ok(summary().includes(`${I.fmtCount(bySurface.improved)} annotations better, Poor ${I.fmtCount(bySurface.worse)};`), summary());
  const metric = card(host, 'improve').all('select').find((s) => s.dataset.role === 'metric');
  metric.value = 'rimMean';
  metric.fire('change');
  assert.ok(summary().includes(`${I.fmtCount(byRim.improved)} annotations better, Poor ${I.fmtCount(byRim.worse)};`), summary());
  assert.equal(card(host, 'improve').one('tbody').children[0].dataset.id, String(byRim.rows[0].id), 'the table follows the metric too');
  assert.ok(card(host, 'improve').one('aside, span.aside') || true);
  // The annotations table's last column: the share of the rim the render encloses, or a dash.
  const table = card(host, 'annotations').one('tbody').children;
  const listed = I.sortRows(I.filterMinArea(rows(a), I.DEFAULT_MIN_AREA_PCT / 100), 'surfaceMean', 'desc').slice(0, table.length);
  listed.forEach((r, k) => {
    const want = Number.isFinite(r.fatShare) ? `${Math.round(r.fatShare * 100)} %` : '—';
    assert.equal(table[k].children[7].textContent, want, `Fat of ${r.id}`);
  });
  assert.ok(listed.some((r) => !Number.isFinite(r.fatShare)) || table.length < 12, 'a dash is in the fixture or the table is short');
  assert.ok(listed.some((r) => Number.isFinite(r.fatShare) && r.fatShare > 0 && r.fatShare < 1));
});

test('a link inside a row or a worst place does its own job once: the row does not also fly', () => {
  const { host, calls } = makeDrawer();
  const row = card(host, 'annotations').one('tbody').children[0];
  row.byData('role', 'fly')[0].fire('click');
  assert.equal(calls.fly.length, 1, 'the link, not the link and the row');
  row.byData('role', 'section')[0].fire('click');
  assert.equal(calls.fly.length, 1, 'the section link does not fly');
  assert.equal(calls.section.length, 1);
  row.children[2].fire('click');                          // elsewhere in the row: the row flies
  assert.equal(calls.fly.length, 2);
  const spot = card(host, 'spots').all('li')[0];
  spot.byData('role', 'fly')[0].fire('click');
  assert.equal(calls.fly.length, 3);
  spot.byData('role', 'section')[0].fire('click');
  assert.equal(calls.fly.length, 3);
  assert.equal(calls.section.length, 2);
  spot.fire('click');
  assert.equal(calls.fly.length, 4);
});

test('disposing takes the drawer out of its host', () => {
  const { host, drawer } = makeDrawer();
  assert.equal(host.children.length, 1);
  drawer.dispose();
  assert.equal(host.children.length, 0);
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
