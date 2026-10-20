// The Position shift layer's pure module (static/js/shifts.js): the stages of an
// annotation's position, the pair choice, exaggeration, thresholds, the slice
// window, the arrow and ring buffers, the colors, and the keys.  Run with
//
//     node tests/js/test_shifts.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free, no DOM, no
// three.js.  The numbers marked "c1178" are the ones measured on Inferior Monkey
// c1178 (curve fit and live registration, 2026-10-15): 2,183 of 2,434 annotations
// moved, 95th percentile 0.555 um, bounding-box diagonal 299.7 um.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const S = await import(pathToFileURL(path.join(jsDir, 'shifts.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, tol = 1e-9, what = '') => assert.ok(
  Math.abs(a - b) <= tol, `${what} ${a} is not within ${tol} of ${b}`);
const closeAll = (a, b, tol = 1e-9, what = '') => {
  assert.equal(a.length, b.length, `${what} lengths`);
  for (let i = 0; i < a.length; i += 1) close(a[i], b[i], tol, `${what}[${i}]`);
};

/** A `/nodes` row. */
function node(id, section, x, y, { z = null, shift = null, live = null, radius = 1, type = 1 } = {}) {
  return {
    id, parent_id: 1, z_section: section, radius_um: radius, type_code: type,
    xyz_um: [x, y, z ?? section * 0.07], shift_um: shift, live_shift_um: live,
  };
}

// -- the stages ---------------------------------------------------------------------

test('the stages follow from one copy: stored, registered, corrected', () => {
  const n = node(7, 50, 10, 20, { z: 5, shift: [0.3, -0.2], live: [0.5, 0.1] });
  const p = S.stagePositions(n);
  closeAll(p.corrected, [10, 20], 0);
  closeAll(p.registered, [9.7, 20.2], 1e-12);      // xyz - shift
  closeAll(p.stored, [9.2, 20.1], 1e-12);          // xyz - live - shift
  // A null move counts as zero.
  const plain = S.stagePositions(node(8, 50, 1, 2));
  closeAll(plain.stored, [1, 2], 0);
  closeAll(plain.registered, [1, 2], 0);
  const onlyLive = S.stagePositions(node(9, 50, 1, 2, { live: [0.5, 0] }));
  closeAll(onlyLive.registered, [1, 2], 0);
  closeAll(onlyLive.stored, [0.5, 2], 1e-12);
  // Anything that is not a pair of finite numbers is no move.
  closeAll(S.stagePositions(node(10, 50, 1, 2, { shift: [NaN, 1], live: ['a', 1] })).stored, [1, 2], 0);
});

test('each pair ends at its later stage and its shift reaches back to the earlier one', () => {
  const n = node(7, 50, 10, 20, { z: 5, shift: [0.3, -0.2], live: [0.5, 0.1] });
  const st = S.stagePositions(n);
  const back = (e) => [e.head[0] - e.shift[0], e.head[1] - e.shift[1]];
  const correction = S.pairEnds(n, 'correction');
  closeAll(correction.head, st.corrected, 1e-12);
  closeAll(correction.shift, [0.3, -0.2], 0);       // exactly the move field
  closeAll(back(correction), st.registered, 1e-12);
  const transforms = S.pairEnds(n, 'transforms');
  closeAll(transforms.head, st.registered, 1e-12);
  closeAll(transforms.shift, [0.5, 0.1], 0);
  closeAll(back(transforms), st.stored, 1e-12);
  const both = S.pairEnds(n, 'both');
  closeAll(both.head, st.corrected, 1e-12);
  closeAll(both.shift, [0.8, -0.1], 1e-12);
  closeAll(back(both), st.stored, 1e-12);
  assert.throws(() => S.pairEnds(n, 'sideways'), RangeError);
  assert.deepEqual(S.PAIR_STAGES.correction, ['registered', 'corrected']);
  assert.deepEqual(S.PAIR_STAGES.transforms, ['stored', 'registered']);
  assert.deepEqual(S.PAIR_STAGES.both, ['stored', 'corrected']);
});

test('moveCounts counts each stage on the cell\'s own nodes', () => {
  const nodes = [
    node(1, 1, 0, 0, { shift: [0.1, 0] }), node(2, 1, 0, 0, { shift: [0, 0] }),
    node(3, 1, 0, 0, { live: [0.2, 0.2] }), node(4, 1, 0, 0, { shift: [0.0003, 0] }),
    node(5, 1, 0, 0, { shift: [0.3, 0], live: [0.1, 0] }), node(6, 1, 0, 0),
  ];
  assert.deepEqual(S.moveCounts(nodes), { correction: 2, transforms: 2 });
});

// -- the display keys and which pair the user sees -----------------------------------------

test('the persisted keys are the spec\'s, with its defaults', () => {
  assert.deepEqual([...S.SHIFT_KEYS].sort(), [
    'shiftArrows', 'shiftColor', 'shiftColorMax', 'shiftFocusDim', 'shiftMin', 'shiftMinSlice', 'shiftOpacity',
    'shiftPair', 'shiftRings', 'shiftScale', 'shiftSlice', 'shiftSliceDim', 'shiftThrough', 'shiftWidth',
    'shiftWindow', 'showShift',
  ]);
  assert.deepEqual(S.SHIFT_DEFAULTS, {
    showShift: false, shiftPair: 'correction', shiftArrows: true, shiftRings: false, shiftScale: null,
    shiftMin: 0.25, shiftMinSlice: 0.05, shiftSlice: false, shiftWindow: 5, shiftColor: 'magnitude',
    shiftColorMax: null, shiftThrough: true, shiftOpacity: 0.9, shiftWidth: 2.5, shiftFocusDim: 0.06,
    shiftSliceDim: 0.06,
  });
  assert.ok(Object.isFrozen(S.SHIFT_DEFAULTS));
  assert.deepEqual(S.normalizeShiftDisplay({}), S.SHIFT_DEFAULTS);
  assert.deepEqual(S.normalizeShiftDisplay(undefined), S.SHIFT_DEFAULTS);
  for (const bad of [null, 'on', 7, [], true]) {
    assert.deepEqual(S.normalizeShiftDisplay(bad), S.SHIFT_DEFAULTS, `saved ${JSON.stringify(bad)}`);
  }
  assert.equal(S.thresholdFor(null, 0), S.thresholdFor({}, 0));
});

test('a value the controls cannot produce falls back instead of reaching the math', () => {
  const d = S.normalizeShiftDisplay({
    showShift: 'yes', shiftPair: 'sideways', shiftColor: 'rainbow', shiftScale: 0, shiftMin: -3,
    shiftMinSlice: NaN, shiftWindow: 7.6, shiftOpacity: 5, shiftColorMax: -1, shiftSlice: 1,
  });
  assert.deepEqual(d, {
    ...S.SHIFT_DEFAULTS, shiftScale: null, shiftMin: 0, shiftWindow: 8, shiftOpacity: 1,
  });
  assert.equal(S.normalizeShiftDisplay({ shiftScale: 500 }).shiftScale, 100);
  assert.equal(S.normalizeShiftDisplay({ shiftScale: 0.2 }).shiftScale, 1);
  assert.equal(S.normalizeShiftDisplay({ shiftWindow: -4 }).shiftWindow, 0);
  // The arrows' width, CSS pixels: 1 to 8, a value from elsewhere is the default.
  assert.equal(S.normalizeShiftDisplay({ shiftWidth: 40 }).shiftWidth, 8);
  assert.equal(S.normalizeShiftDisplay({ shiftWidth: 0.2 }).shiftWidth, 1);
  assert.equal(S.normalizeShiftDisplay({ shiftWidth: '3' }).shiftWidth, 2.5);
  assert.equal(S.normalizeShiftDisplay({ shiftWidth: 4.5 }).shiftWidth, 4.5);
  assert.equal(S.normalizeShiftDisplay({ shiftWindow: 1e9 }).shiftWindow, S.WINDOW_MAX);
  assert.equal(S.normalizeShiftDisplay({ shiftColorMax: 0.8 }).shiftColorMax, 0.8);
  // The others' share while an annotation is selected: 0 to 1.
  assert.equal(S.normalizeShiftDisplay({ shiftFocusDim: 0 }).shiftFocusDim, 0);
  assert.equal(S.normalizeShiftDisplay({ shiftFocusDim: 3 }).shiftFocusDim, 1);
  assert.equal(S.normalizeShiftDisplay({ shiftFocusDim: 'dim' }).shiftFocusDim, 0.06);
  assert.equal(S.normalizeShiftDisplay({ shiftSliceDim: -1 }).shiftSliceDim, 0);
  assert.equal(S.normalizeShiftDisplay({ shiftSliceDim: null }).shiftSliceDim, 0.06);
});

test('the threshold and the cap follow the view: All sections or one slice', () => {
  assert.equal(S.thresholdFor({}), 0.25);
  assert.equal(S.thresholdFor({ shiftSlice: true }), 0.05);
  assert.equal(S.thresholdFor({ shiftSlice: true, shiftMinSlice: 0.5, shiftMin: 2 }), 0.5);
  assert.equal(S.thresholdFor({ shiftMin: 2 }), 2);
  assert.equal(S.capFor({}), 2000);
  assert.equal(S.capFor({ shiftSlice: true }), 15000);
});

const SUMMARY = {
  section_correction: { requested: true, applied: true, n_moved: 587 },
  live_registration: { requested: true, applied: true, n_moved: 0 },
};

test('a pair is offered only when its stage was applied and moved something', () => {
  const av = S.pairAvailability(SUMMARY);
  assert.equal(av.correction.available, true);
  assert.equal(av.transforms.available, false);
  assert.match(av.transforms.reason, /current section transforms moved no annotation/);
  assert.equal(av.both.available, true);
  // The summary also counts child structures: counts from the nodes win.
  const real = S.pairAvailability(SUMMARY, { correction: 0, transforms: 0 });
  assert.equal(real.correction.available, false);
  assert.match(real.correction.reason, /section correction moved no annotation/);
  assert.equal(real.both.available, false);
  assert.match(real.both.reason, /Neither/);
  assert.equal(S.pairAvailability(SUMMARY, { correction: 0, transforms: 4 }).transforms.available, true);
  // Off, and asked for but not applied (the reason is the server's note).
  const off = S.pairAvailability({});
  assert.match(off.correction.reason, /off for this cell/);
  assert.match(off.transforms.reason, /off for this cell/);
  const noted = S.pairAvailability({
    live_registration: { requested: true, applied: false, note: 'RC1\'s section transforms are not read here' },
  });
  assert.equal(noted.transforms.reason, 'RC1\'s section transforms are not read here');
  assert.equal(S.pairAvailability(null).correction.available, false);
});

test('the pair drawn falls back: correction, then transforms, then nothing', () => {
  const av = S.pairAvailability(SUMMARY);
  assert.equal(S.effectivePair('correction', av), 'correction');
  assert.equal(S.effectivePair('both', av), 'both');
  assert.equal(S.effectivePair('transforms', av), 'correction');
  const onlyTransforms = S.pairAvailability({
    section_correction: { requested: true, applied: false, note: 'down' },
    live_registration: { requested: true, applied: true, n_moved: 3 },
  });
  assert.equal(S.effectivePair('correction', onlyTransforms), 'transforms');
  assert.equal(S.effectivePair('both', onlyTransforms), 'both');
  assert.equal(S.effectivePair('correction', S.pairAvailability({})), null);
  assert.equal(S.effectivePair('both', S.pairAvailability({})), null);
});

// -- the field: statistics, auto exaggeration, the length cap -------------------------------

/** 21 moved annotations along a 299.7 um cell whose sorted lengths put the 95th percentile at 0.555. */
function c1178Like() {
  const mags = [];
  for (let i = 0; i < 19; i += 1) mags.push(0.05 + 0.025 * i);
  mags.push(0.555, 3.99);
  const nodes = [node(1, 0, 0, 0, { z: 0 }), node(2, 100, 299.7, 0, { z: 0 })];
  mags.forEach((m, i) => nodes.push(node(10 + i, 5 + i, 10 + 10 * i, 0, { z: 0, shift: [m, 0] })));
  nodes.push(node(40, 30, 50, 0, { z: 0, shift: [0.0004, 0] }));      // below 0.5 nm: not moved
  nodes.push(node(41, 31, 60, 0, { z: 0, shift: [0, 0] }));
  return { nodes, mags };
}

test('c1178: the statistics are numpy\'s and Auto is 13.5', () => {
  const { nodes } = c1178Like();
  const f = S.prepareField(nodes, 'correction');
  assert.equal(f.nNodes, 25);
  assert.equal(f.n, 21);                              // the 0.4 nm and the zero are not moved
  close(f.bbox.diagonal, 299.7, 1e-12);
  close(f.stats.median, 0.3, 1e-12);
  close(f.stats.p95, 0.555, 1e-12);
  close(f.stats.p99, 0.555 + (3.99 - 0.555) * 0.8, 1e-12);
  close(f.stats.max, 3.99, 0);
  assert.equal(f.stats.nMoved, 21);
  close(S.autoScale(f), 13.5, 1e-9);                  // 0.025 * 299.7 / 0.555
  assert.equal(S.effectiveScale({}, f), S.autoScale(f));
  assert.equal(S.effectiveScale({ shiftScale: 4 }, f), 4);
  close(S.effectiveColorMax({}, f), 0.555, 1e-12);
  assert.equal(S.effectiveColorMax({ shiftColorMax: 1.5 }, f), 1.5);
  // The other pairs see nothing here.
  assert.equal(S.prepareField(nodes, 'transforms').n, 0);
  assert.equal(S.autoScale(S.prepareField(nodes, 'transforms')), 1);
});

test('Auto stays between 1 and 100, whatever the cell', () => {
  const tiny = S.prepareField([node(1, 0, 0, 0), node(2, 1, 3, 0, { shift: [30, 0] })], 'correction');
  assert.equal(S.autoScale(tiny), 1);                 // 0.025 * 3 / 30 < 1
  const huge = [node(1, 0, 0, 0, { z: 0 }), node(2, 1, 500, 0, { z: 0 })];
  for (let i = 0; i < 30; i += 1) huge.push(node(3 + i, 2, i, 1, { z: 0, shift: [0.0015, 0] }));
  assert.equal(S.autoScale(S.prepareField(huge, 'correction')), 100);
  assert.equal(S.autoScale(S.prepareField([], 'correction')), 1);
});

test('the percentile is numpy\'s default (linear)', () => {
  const a = Float64Array.of(1, 2, 3, 4);
  assert.equal(S.percentileSorted(a, 50), 2.5);
  assert.equal(S.percentileSorted(a, 0), 1);
  assert.equal(S.percentileSorted(a, 100), 4);
  close(S.percentileSorted(a, 95), 3.85, 1e-12);
  assert.equal(S.percentileSorted(new Float64Array(0), 50), 0);
  assert.equal(S.percentileSorted(Float64Array.of(7), 99), 7);
});

test('an exaggerated arrow stops at 10% of the diagonal; a longer true shift is drawn true', () => {
  const diag = 299.7;
  const cap = 29.97;
  close(S.drawnLength(0.5, 13.5, diag), 6.75, 1e-12);           // under the cap: k times
  close(S.drawnLength(2.2, 13.5, diag), 29.7, 1e-12);           // just under it
  close(S.drawnLength(3.99, 13.5, diag), cap, 1e-12);           // 53.9 would pass it: capped
  close(S.drawnLength(159.137, 13.5, diag), 159.137, 0);        // already longer: true length
  close(S.drawnLength(159.137, 1, diag), 159.137, 0);
  close(S.drawnLength(0.5, 1, diag), 0.5, 0);                    // at x1 nothing is exaggerated
  close(S.drawnLength(40, 1, diag), 40, 0);
});

test('only a move up to Full color at is exaggerated; past it the arrow grows at true scale', () => {
  // c1178 (2026-10-16): Auto x13, Full color at 0.56 um, a 300 um diagonal (cap 30 um).
  const diag = 299.7;
  const full = 0.56;
  close(S.drawnLength(0.5, 13, diag, full), 6.5, 1e-12);                 // under Full color at: k times
  close(S.drawnLength(0.56, 13, diag, full), 7.28, 1e-12);               // at it: k times
  close(S.drawnLength(3.99, 13, diag, full), 7.28 + 3.43, 1e-12);        // 3.99 um: 10.71, was the 30 um cap
  close(S.drawnLength(1.4, 13, diag, full), 7.28 + 0.84, 1e-12);
  // Longer moves still draw longer, and never shorter than they are.
  const lens = [0.1, 0.3, 0.56, 0.7, 1.4, 4, 20, 40, 159].map((t) => S.drawnLength(t, 13, diag, full));
  for (let i = 1; i < lens.length; i += 1) assert.ok(lens[i] > lens[i - 1], `monotone at ${i}`);
  [0.1, 0.56, 4, 159].forEach((t) => assert.ok(S.drawnLength(t, 13, diag, full) >= t));
  // The cap still bounds it, and a true move past the cap is drawn true.
  close(S.drawnLength(25, 13, diag, full), 29.97, 1e-12);                 // 7.28 + 24.44 = 31.7: capped
  close(S.drawnLength(159.137, 13, diag, full), 159.137, 0);
  // Without a Full color at (the old call) it is k times, as before.
  close(S.drawnLength(3.99, 13.5, diag), 29.97, 1e-12);
  // markScales passes it through: the scale is the drawn length over the true one.
  const rows = [node(1, 1, 0, 0, { shift: [0.5, 0] }), node(2, 2, 0, 0, { shift: [3.99, 0] })];
  const f = S.prepareField(rows, 'correction');
  f.bbox.diagonal = diag;
  const sel = S.selectMarks(f, { min: 0 });
  const scales = S.markScales(f, sel, 13, full);
  close(scales[0], 13, 1e-12);
  close(scales[1], (7.28 + 3.43) / 3.99, 1e-12);
});

test('zOfSection is the cell\'s own section-to-z map', () => {
  const nodes = [node(1, 100, 0, 0), node(2, 200, 1, 1), node(3, 150, 2, 2)];
  const f = S.prepareField(nodes, 'correction');
  close(S.zOfSection(f, 150), 150 * 0.07, 1e-12);
  close(S.zOfSection(f, 939), 939 * 0.07, 1e-9);
  assert.deepEqual(f.sections, { min: 100, max: 200 });
});

// -- the marks: threshold, cap, order, window -------------------------------------------------

/** 3,000 annotations, 10 per section on sections 0-299, lengths 0.00-0.99 in a fixed shuffle. */
function wideCell() {
  const nodes = [];
  for (let i = 0; i < 3000; i += 1) {
    const m = ((i * 37) % 100) / 100;
    nodes.push(node(1000 + i, Math.floor(i / 10), i % 97, Math.floor(i / 97), { shift: [m, 0] }));
  }
  return nodes;
}

test('the threshold keeps shifts at or above it, in true micrometers', () => {
  const nodes = wideCell();
  const f = S.prepareField(nodes, 'correction');
  const lengths = nodes.map((n) => n.shift_um[0]);
  assert.equal(f.n, lengths.filter((m) => m > 0.0005).length);          // the zeros are not moved
  for (const min of [0, 0.05, 0.25, 0.5, 0.97, 5]) {
    const sel = S.selectMarks(f, { min, cap: 1e9 });
    const want = lengths.filter((m) => m >= min && m > 0.0005).length;
    assert.equal(sel.n, want, `min ${min}`);
    assert.equal(sel.total, want);
    assert.equal(sel.truncated, false);
    for (let i = 0; i < sel.n; i += 1) assert.ok(f.mag[sel.rows[i]] >= min);
  }
});

test('over the cap only the strongest are kept, deterministically, ordered by section', () => {
  const nodes = wideCell();
  const f = S.prepareField(nodes, 'correction');
  const sel = S.selectMarks(f, { min: 0.05, cap: 100 });
  assert.equal(sel.n, 100);
  assert.equal(sel.truncated, true);
  assert.equal(sel.total, nodes.filter((n) => n.shift_um[0] >= 0.05).length);
  // The strongest 100 by an independent sort: longest first, the earlier annotation on a tie.
  const ranked = nodes.map((n, i) => ({ m: n.shift_um[0], i })).filter((r) => r.m >= 0.05)
    .sort((a, b) => b.m - a.m || a.i - b.i).slice(0, 100).map((r) => nodes[r.i].id).sort((a, b) => a - b);
  const kept = [...sel.rows].map((r) => f.id[r]).sort((a, b) => a - b);
  assert.deepEqual(kept, ranked);
  assert.deepEqual(S.selectMarks(f, { min: 0.05, cap: 100 }).rows, sel.rows);       // stable
  // Drawn in section order, with a prefix-offset array over the distinct sections.
  for (let i = 1; i < sel.n; i += 1) assert.ok(sel.section[i] >= sel.section[i - 1]);
  assert.equal(sel.starts.length, sel.sections.length + 1);
  assert.equal(sel.starts[sel.sections.length], sel.n);
  for (let j = 0; j < sel.sections.length; j += 1) {
    assert.equal(sel.section[sel.starts[j]], sel.sections[j]);
    assert.equal(sel.starts[j + 1] - sel.starts[j], [...sel.section].filter((s) => s === sel.sections[j]).length);
  }
});

test('a window of sections is a range of marks: counts at the extremes and when empty', () => {
  const f = S.prepareField(wideCell(), 'correction');
  const sel = S.selectMarks(f, { min: 0.9, cap: 15000 });
  const brute = (center, half) => [...sel.section].filter((s) => s >= center - half && s <= center + half).length;
  for (const [center, half] of [[150, 5], [0, 5], [299, 5], [-20, 5], [400, 5], [150, 0], [0, 0], [299, 0],
    [150, 1000], [3, 2], [296, 10], [150.4, 3]]) {
    const w = S.windowRange(sel, Math.round(center), half);
    assert.equal(w.count, brute(Math.round(center), half), `center ${center} +/- ${half}`);
    // The range is contiguous marks, all inside the window.
    for (let i = w.first; i < w.first + w.count; i += 1) {
      assert.ok(sel.section[i] >= Math.round(center) - half && sel.section[i] <= Math.round(center) + half);
    }
  }
  // A window between two marked sections, and far outside the cell: empty, never negative.
  const empty = S.windowRange(sel, 1e6, 3);
  assert.deepEqual(empty, { first: sel.n, count: 0 });
  assert.equal(S.windowRange(sel, -1e6, 3).count, 0);
  const none = S.selectMarks(f, { min: 99 });
  assert.equal(none.n, 0);
  assert.deepEqual(S.windowRange(none, 10, 5), { first: 0, count: 0 });
});

test('the slice keeps its own threshold and a larger cap, so the slab loses no member', () => {
  const nodes = wideCell();
  const f = S.prepareField(nodes, 'correction');
  const all = S.selectMarks(f, { min: S.thresholdFor({}), cap: S.capFor({}) });
  const slice = S.selectMarks(f, { min: S.thresholdFor({ shiftSlice: true }), cap: S.capFor({ shiftSlice: true }) });
  assert.equal(all.n, 2000);
  assert.equal(all.truncated, true);
  assert.equal(slice.truncated, false);
  assert.ok(slice.n > all.n);
  // Every mark of the slab at or above 0.05 is in the slice buffer.
  const w = S.windowRange(slice, 150, 5);
  const want = nodes.filter((n) => n.z_section >= 145 && n.z_section <= 155 && n.shift_um[0] >= 0.05).length;
  assert.equal(w.count, want);
});

// -- the buffers ----------------------------------------------------------------------------------

const TOL32 = 2e-5;

/** One moved annotation at (10, 20, 5) among others that give a 300 um diagonal. */
function oneArrowCell(extra = {}) {
  return [
    node(1, 0, 0, 0, { z: 0 }), node(2, 100, 300, 0, { z: 0 }),
    node(7, 50, 10, 20, { z: 5, shift: [0.3, 0.4], radius: 2, ...extra }),
  ];
}

test('an arrow is a shaft from the tail to the head and two barbs, in the section plane', () => {
  const f = S.prepareField(oneArrowCell(), 'correction');
  const sel = S.selectMarks(f, { min: 0 });
  assert.equal(sel.n, 1);
  const scales = S.markScales(f, sel, 10);
  close(scales[0], 10, 1e-12);                                  // 0.5 um * 10 = 5 um < 30 um cap
  const a = S.buildArrows(f, sel, scales);
  assert.equal(a.positions.length, 6 * 3);
  assert.equal(a.vertsPerMark, S.ARROW_VERTS);
  const v = (i) => [a.positions[i * 3], a.positions[i * 3 + 1], a.positions[i * 3 + 2]];
  closeAll(v(0), [7, 16, 5], TOL32);                            // tail = head - 10 * shift
  closeAll(v(1), [10, 20, 5], TOL32);                           // head = the annotation
  closeAll(v(2), v(1), 0);                                      // both barbs leave the head
  closeAll(v(4), v(1), 0);
  // Barbs: 30% of the 5 um drawn length, back along the shaft at +/-25 degrees.
  const back = [-0.6, -0.8];
  for (const [i, sign] of [[3, 1], [5, -1]]) {
    const dx = v(i)[0] - v(1)[0];
    const dy = v(i)[1] - v(1)[1];
    close(Math.hypot(dx, dy), 1.5, TOL32, 'barb length');
    close((dx * back[0] + dy * back[1]) / 1.5, Math.cos(S.BARB_ANGLE), TOL32, 'barb angle');
    const cross = back[0] * dy - back[1] * dx;                    // sign says which side
    assert.ok(Math.sign(cross) === sign, `barb ${i} on the wrong side`);
    assert.equal(v(i)[2], 5);
  }
});

test('the head is the pair\'s later stage; the original sits at head - k * shift', () => {
  const rows = oneArrowCell({ shift: [0.3, 0.4], live: [0.1, 0.2] });
  const target = rows[2];
  const st = S.stagePositions(target);
  for (const [pair, tailAtK1] of [['correction', st.registered], ['transforms', st.stored], ['both', st.stored]]) {
    const f = S.prepareField(rows, pair);
    const sel = S.selectMarks(f, { min: 0 });
    const a = S.buildArrows(f, sel, S.markScales(f, sel, 1));
    const head = pair === 'transforms' ? st.registered : st.corrected;
    closeAll(a.positions.slice(0, 2), tailAtK1, TOL32, `${pair} tail at x1`);
    closeAll(a.positions.slice(3, 5), head, TOL32, `${pair} head`);
    // The disc ring of the original sits at the same tail.
    const rings = S.buildRings(f, sel, S.markScales(f, sel, 1), []);
    const ringCenter = [0, 0];
    for (let i = 0; i < 16; i += 1) { ringCenter[0] += rings.positions[i * 6] / 16; ringCenter[1] += rings.positions[i * 6 + 1] / 16; }
    closeAll(ringCenter, tailAtK1, TOL32, `${pair} ring center at x1`);
  }
  // At k = 10 the tail moves ten shifts back from the head, the head stays.
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0 });
  const a = S.buildArrows(f, sel, S.markScales(f, sel, 10));
  closeAll(a.positions.slice(0, 2), [10 - 3, 20 - 4], TOL32);
  closeAll(a.positions.slice(3, 5), [10, 20], TOL32);
});

test('a 159 um outlier is one arrow at true length, not a line across the volume', () => {
  const rows = [
    node(1, 0, 0, 0, { z: 0 }), node(2, 100, 299.7, 0, { z: 0 }),
    node(431668, 383, 100, 50, { z: 26.8, shift: [108.534, 115.586] }),     // RC2 c5597: 158.6 um
    node(5, 40, 50, 50, { z: 2.8, shift: [0.5, 0] }),
    node(6, 41, 60, 50, { z: 2.9, shift: [3.99, 0] }),
  ];
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0 });
  const k = 13.5;
  const scales = S.markScales(f, sel, k);
  const a = S.buildArrows(f, sel, scales);
  const length = (i) => Math.hypot(a.positions[i * 18 + 3] - a.positions[i * 18],
    a.positions[i * 18 + 4] - a.positions[i * 18 + 1]);
  const byId = (id) => [...sel.rows].findIndex((r) => f.id[r] === id);
  // diagonal 299.7 -> cap 29.97 um (the diagonal is not the xy extent: z is 26.8 um tall).
  const cap = 0.1 * f.bbox.diagonal;
  close(length(byId(431668)), Math.hypot(108.534, 115.586), 5e-3, 'outlier drawn at true length');
  close(length(byId(5)), 6.75, 5e-5, 'a small shift, k times');
  close(length(byId(6)), cap, 5e-3, 'a shift whose k times passes the cap');
});

test('the original rings: a polygon of the radius, or the traced outline moved with its annotation', () => {
  const rows = [
    node(1, 0, 0, 0, { z: 0 }), node(2, 100, 300, 0, { z: 0 }),
    node(7, 50, 10, 20, { z: 5, shift: [0.3, 0.4], radius: 2 }),
    node(8, 51, 1, 1, { z: 5.07, shift: [0.3, 0.4], radius: 0, type: 6 }),
    node(9, 52, 5, 5, { z: 5.14, shift: [0.3, 0.4], radius: 0, type: 6 }),    // a curve with no outline
  ];
  const outline = { location_id: 8, z_section: 51, z_um: 5.07,
    outline_um: [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]], cutouts_um: [] };
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0 });
  assert.equal(sel.n, 3);
  const scales = S.markScales(f, sel, 1);
  const rings = S.buildRings(f, sel, scales, [outline]);
  assert.equal(rings.n, 3);
  // Marks come in section order: the disc (50), the closed curve with an outline (51), the one without (52).
  assert.deepEqual([...rings.offsets], [0, 32, 40, 40]);
  assert.equal(rings.positions.length, 40 * 3);
  assert.equal(rings.colors.length, 40 * 3);
  // The disc: radius 2 about the tail (10 - 0.3, 20 - 0.4), in the disc's plane.
  for (let i = 0; i < 32; i += 1) {
    const x = rings.positions[i * 3] - 9.7;
    const y = rings.positions[i * 3 + 1] - 19.6;
    close(Math.hypot(x, y), 2, TOL32, 'ring radius');
    assert.ok(Math.abs(rings.positions[i * 3 + 2] - 5) < TOL32);
  }
  // The outline square, moved by -shift: four segments, each corner shifted.
  const o = rings.offsets[1] * 3;
  closeAll(rings.positions.slice(o, o + 6), [-0.3, -0.4, 5.07, 1.7, -0.4, 5.07], TOL32);
  closeAll(rings.positions.slice(o + 6, o + 12), [1.7, -0.4, 5.07, 1.7, 1.6, 5.07], TOL32);
  // Ranges: the rings of a window of marks are one vertex range, whatever their sizes.
  assert.deepEqual(S.vertexRange(rings.offsets, { first: 1, count: 2 }), { first: 32, count: 8 });
  assert.deepEqual(S.vertexRange(rings.offsets, { first: 0, count: 0 }), { first: 0, count: 0 });
  assert.deepEqual(S.vertexRange(rings.offsets, { first: 0, count: 3 }), { first: 0, count: 40 });
  // The outline index can be given prebuilt.
  assert.deepEqual([...S.buildRings(f, sel, scales, S.outlineIndex([outline])).offsets], [...rings.offsets]);
});

test('an outline ghost follows the pair: transforms moves it back past the correction too', () => {
  const rows = [
    node(1, 0, 0, 0, { z: 0 }),
    node(8, 51, 1, 1, { z: 5.07, shift: [0.3, 0.4], live: [0.1, 0.2], radius: 0, type: 6 }),
  ];
  const outline = { location_id: 8, outline_um: [[0, 0], [2, 0], [2, 2], [0, 2]], z_um: 5.07 };
  for (const [pair, move] of [['correction', [-0.3, -0.4]], ['transforms', [-0.4, -0.6]], ['both', [-0.4, -0.6]]]) {
    const f = S.prepareField(rows, pair);
    const sel = S.selectMarks(f, { min: 0 });
    const rings = S.buildRings(f, sel, S.markScales(f, sel, 1), [outline]);
    assert.equal(rings.offsets[sel.n], 8, pair);
    closeAll(rings.positions.slice(0, 2), move, TOL32, `${pair} first corner`);
  }
});

test('ring count equals the shown set and a window draws exactly its rings', () => {
  const f = S.prepareField(wideCell(), 'correction');
  const sel = S.selectMarks(f, { min: 0.5, cap: 15000 });
  const scales = S.markScales(f, sel, 5);
  const rings = S.buildRings(f, sel, scales, []);
  assert.equal(rings.offsets.length, sel.n + 1);
  assert.equal(rings.offsets[sel.n], sel.n * 32);                // every wide-cell node has radius 1
  const w = S.windowRange(sel, 100, 2);
  const range = S.vertexRange(rings.offsets, w);
  assert.equal(range.count, w.count * 32);
  assert.equal(range.first, w.first * 32);
});

test('the slice plane is the cell\'s xy box and a margin, at one z', () => {
  const f = S.prepareField([node(1, 0, 0, 0, { z: 0 }), node(2, 100, 100, 50, { z: 7 })], 'correction');
  const p = S.slicePlane(f, 3.5);
  assert.equal(p.quad.length, 6 * 3);
  assert.equal(p.outline.length, 8 * 3);
  const xs = [...p.quad].filter((_, i) => i % 3 === 0);
  const ys = [...p.quad].filter((_, i) => i % 3 === 1);
  close(Math.min(...xs), -5, TOL32); close(Math.max(...xs), 105, TOL32);
  close(Math.min(...ys), -2.5, TOL32); close(Math.max(...ys), 52.5, TOL32);
  assert.ok([...p.quad].filter((_, i) => i % 3 === 2).every((z) => z === 3.5));
  assert.ok([...p.outline].filter((_, i) => i % 3 === 2).every((z) => z === 3.5));
});

test('one dot per moved annotation for the chart', () => {
  const { nodes } = c1178Like();
  const f = S.prepareField(nodes, 'correction');
  const d = S.dotData(f);
  assert.equal(d.n, 21);
  assert.equal(d.section.length, 21);
  assert.equal(d.maxMag, 3.99);
  assert.deepEqual([d.sectionMin, d.sectionMax], [0, 100]);
});

// -- color ------------------------------------------------------------------------------------------

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lin = (c) => { const v = c / 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const luminance = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
};
function lab(rgb) {
  const [r, g, b] = rgb.map(lin);
  const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
  const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}
const dE = (a, b) => { const p = lab(a); const q = lab(b); return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]); };
const bytes = (rgb) => rgb.map((v) => Math.round(v * 255));

/** Colors the layer must stay apart from, as in render3d.js, compare.js and app.css. */
const RESERVED = {
  highlight: '#ffe14d', highlightAlt: '#38e1ff', second: '#8fd0ff', secondAlt: '#ff8ad8',
  above: '#ff5c5c', aboveAlt: '#ff7a00',
  errorGray: '#d9dee6', errorAmber: '#ffb000', errorRed: '#d91f3b',
  diffBlue: '#3b73eb', diffRed: '#db3838', compareYellow: '#ffd400',
};

test('the reserved colors are still the ones in the source (or the source moved)', () => {
  const read = (name) => { try { return fs.readFileSync(path.join(jsDir, name), 'utf8'); } catch { return ''; } };
  const render = read('render3d.js');
  const compare = read('compare.js');
  const css = (() => { try { return fs.readFileSync(path.join(jsDir, '..', 'css', 'app.css'), 'utf8'); } catch { return ''; } })();
  const grab = (src, re) => (re.exec(src) || [])[1];
  const checks = [
    ['highlight', grab(render, /const HIGHLIGHT = '(#[0-9a-f]{6})'/i)],
    ['highlightAlt', grab(render, /const HIGHLIGHT_ALT = '(#[0-9a-f]{6})'/i)],
    ['second', grab(render, /const BELOW_HIGHLIGHT = '(#[0-9a-f]{6})'/i)],
    ['secondAlt', grab(render, /const BELOW_HIGHLIGHT_ALT = '(#[0-9a-f]{6})'/i)],
    ['above', grab(render, /const ABOVE_HIGHLIGHT = '(#[0-9a-f]{6})'/i)],
    ['aboveAlt', grab(render, /const ABOVE_HIGHLIGHT_ALT = '(#[0-9a-f]{6})'/i)],
    ['compareYellow', grab(compare, /const SURFACE_COLOR = '(#[0-9a-f]{6})'/i)],
  ];
  for (const [key, found] of checks) {
    if (found === undefined) { console.log(`     (${key}: constant not found in the source; using the list here)`); continue; }
    assert.equal(found.toLowerCase(), RESERVED[key], `${key} changed in the source: update the ramp and RESERVED`);
  }
  const stops = /const ERROR_STOPS = \[\[([\d., ]+)\], \[([\d., ]+)\], \[([\d., ]+)\]\]/.exec(compare);
  if (stops) {
    const got = [1, 2, 3].map((i) => stops[i].split(',').map((v) => Math.round(parseFloat(v) * 255)));
    const want = [RESERVED.errorGray, RESERVED.errorAmber, RESERVED.errorRed].map(hex);
    assert.deepEqual(got, want, 'the error scale changed in compare.js: update the ramp and RESERVED');
  }
  const gradient = /linear-gradient\(to right, (#[0-9a-f]{6}), #ffffff, (#[0-9a-f]{6})\)/i.exec(css);
  if (gradient) {
    assert.equal(gradient[1].toLowerCase(), RESERVED.diffBlue);
    assert.equal(gradient[2].toLowerCase(), RESERVED.diffRed);
  }
});

for (const theme of ['dark', 'light']) {
  test(`the ${theme} magnitude ramp keeps 4:1 on its background and 14 dE from every reserved color`, () => {
    const bg = hex(S.BACKGROUNDS[theme]);
    const stops = S.RAMPS[theme];
    let minContrast = Infinity;
    let minDE = Infinity;
    for (let t = 0; t <= 1.0001; t += 0.01) {
      const c = bytes(S.rampSample(stops, Math.min(1, t)));
      minContrast = Math.min(minContrast, contrast(c, bg));
      for (const h of Object.values(RESERVED)) minDE = Math.min(minDE, dE(c, hex(h)));
    }
    console.log(`     ${theme}: min contrast ${minContrast.toFixed(2)}, min dE76 ${minDE.toFixed(1)}`);
    assert.ok(minContrast >= 4.0, `contrast ${minContrast}`);
    assert.ok(minDE >= 14, `dE ${minDE}`);
  });
}

test('the direction hues and the ring colors stay legible on their background', () => {
  for (const theme of ['dark', 'light']) {
    const bg = hex(S.BACKGROUNDS[theme]);
    let worst = Infinity;
    for (let deg = 0; deg < 360; deg += 1) {
      const c = bytes(S.hslToRgb(deg / 360, S.DIRECTION_SATURATION, S.DIRECTION_LIGHTNESS[theme]));
      worst = Math.min(worst, contrast(c, bg));
    }
    console.log(`     ${theme}: direction hues min contrast ${worst.toFixed(2)}`);
    assert.ok(worst >= 4.0, `${theme} direction contrast ${worst}`);
    assert.ok(contrast(hex(S.RING_COLORS[theme]), bg) >= 4.0, `${theme} ring color`);
  }
});

test('magnitude color runs along the ramp to "Full color at" and holds past it', () => {
  const rows = [node(1, 1, 0, 0, { shift: [0.1, 0] }), node(2, 2, 0, 0, { shift: [0.5, 0] }),
    node(3, 3, 0, 0, { shift: [1.0, 0] }), node(4, 4, 0, 0, { shift: [3.0, 0] })];
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0 });
  const colors = S.markColors(f, sel, { mode: 'magnitude', max: 1.0, inverted: false });
  const at = (i) => [...colors.slice(i * 3, i * 3 + 3)];
  closeAll(at(2), S.rampSample(S.RAMPS.dark, 1), 1e-6);           // 1.0 of 1.0: the last stop
  closeAll(at(3), at(2), 0);                                        // past the max: clamped
  closeAll(at(1), S.rampSample(S.RAMPS.dark, 0.5), 1e-6);
  closeAll(at(1), [0x2f / 255, 0xd6 / 255, 0xa0 / 255], 1e-6);      // the middle stop
  closeAll(at(0), S.rampSample(S.RAMPS.dark, 0.1), 1e-6);
  const light = S.markColors(f, sel, { mode: 'magnitude', max: 1.0, inverted: true });
  closeAll(light.slice(3, 6), [0x70 / 255, 0x38 / 255, 0xc8 / 255], 1e-6);
  assert.notDeepEqual([...light], [...colors]);
});

test('direction color: one hue per angle, whatever the length; cell color is the cell\'s', () => {
  const rows = [node(1, 1, 0, 0, { shift: [0.1, 0.1] }), node(2, 2, 0, 0, { shift: [2.0, 2.0] }),
    node(3, 3, 0, 0, { shift: [-0.5, -0.5] }), node(4, 4, 0, 0, { shift: [0.3, -0.3] })];
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0 });
  const c = S.markColors(f, sel, { mode: 'direction' });
  const at = (i) => [...c.slice(i * 3, i * 3 + 3)];
  closeAll(at(0), at(1), 1e-6);                     // same angle, different length
  assert.ok(at(0).some((v, i) => Math.abs(v - at(2)[i]) > 0.05));        // opposite angle
  assert.ok(at(0).some((v, i) => Math.abs(v - at(3)[i]) > 0.05));
  closeAll([S.directionHue(1, 0)], [0], 0);
  close(S.directionHue(0, 1), 0.25, 1e-12);
  close(S.directionHue(-1, 0), 0.5, 1e-12);
  close(S.directionHue(0, -1), 0.75, 1e-12);
  const own = S.markColors(f, sel, { mode: 'cell', cellColor: [0.2, 0.4, 0.6] });
  closeAll([...own.slice(0, 3)], [0.2, 0.4, 0.6], 1e-6);
  closeAll([...own.slice(9, 12)], [0.2, 0.4, 0.6], 1e-6);
});

test('vertex colors expand per mark to every vertex of the arrow', () => {
  const rows = oneArrowCell();
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0 });
  const colors = S.markColors(f, sel, { mode: 'cell', cellColor: [0.1, 0.2, 0.3] });
  const a = S.buildArrows(f, sel, S.markScales(f, sel, 1), colors);
  assert.equal(a.colors.length, a.positions.length);
  for (let v = 0; v < 6; v += 1) closeAll([...a.colors.slice(v * 3, v * 3 + 3)], [0.1, 0.2, 0.3], 1e-6);
  assert.equal(S.buildArrows(f, sel, S.markScales(f, sel, 1)).colors, null);
  const u = S.uniformColors(4, S.ringColor(true));
  assert.equal(u.length, 12);
  closeAll([...u.slice(9, 12)], S.ringColor(true), 1e-6);
  assert.notDeepEqual(S.ringColor(true), S.ringColor(false));
  closeAll(S.toLinearRgb([0, 1, 0.04045]), [0, 1, 0.04045 / 12.92], 1e-12);
});

// -- the slice and its keys --------------------------------------------------------------------------

test('the slice center is one number per volume, clamped to the cell\'s sections', () => {
  const s = new S.SliceState();
  const range = { min: 10, max: 50 };
  assert.equal(s.has('i'), false);
  assert.equal(s.get('i'), undefined);
  assert.equal(s.set('i', 30, range), 30);
  assert.equal(s.step('i', 1, range), 31);
  assert.equal(s.step('i', 10, range), 41);
  assert.equal(s.step('i', 10, range), 50);                        // clamped at the last section
  assert.equal(s.step('i', 10, range), 50);
  assert.equal(s.step('i', -100, range), 10);
  assert.equal(s.set('i', 12.6, range), 13);
  // Nothing stored yet: the step starts from the fallback (the first section by default).
  assert.equal(s.step('RC1', 1, { min: 5, max: 9 }), 6);
  assert.equal(s.step('RC2', 1, { min: 5, max: 9 }, 7), 8);
  s.clear('RC1');
  assert.equal(s.has('RC1'), false);
});

test('one key step moves every volume in slice mode by the same amount', () => {
  const s = new S.SliceState();
  s.set('i', 100, { min: 0, max: 200 });
  s.set('RC1', 3, { min: 0, max: 200 });
  const ranges = new Map([['i', { min: 0, max: 200 }], ['RC1', { min: 0, max: 10 }], ['RPC1', { min: 1, max: 9, fallback: 5 }]]);
  const moved = s.stepAll(10, ranges);
  assert.deepEqual([...moved], [['i', 110], ['RC1', 10], ['RPC1', 9]]);   // each clamped to its own cell
  assert.equal(s.get('i'), 110);
  assert.deepEqual([...s.stepAll(-1, ranges)], [['i', 109], ['RC1', 9], ['RPC1', 8]]);
});

test('a slice starts at the strongest shift; Home and End are the first and last shown section', () => {
  const rows = [node(1, 40, 0, 0, { shift: [0.4, 0] }), node(2, 90, 0, 0, { shift: [2.4, 0] }),
    node(3, 10, 0, 0, { shift: [1.4, 0] }), node(4, 200, 0, 0, { shift: [0.01, 0] })];
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0.3 });
  assert.equal(S.defaultCenter(f, sel), 90);
  assert.equal(S.edgeSection(sel, 'first'), 10);
  assert.equal(S.edgeSection(sel, 'last'), 90);                    // 200 is under the threshold: not shown
  const none = S.selectMarks(f, { min: 50 });
  assert.equal(S.edgeSection(none, 'first'), null);
  assert.equal(S.defaultCenter(f, none), Math.round((10 + 200) / 2));
});

const key = (k, extra = {}) => ({ key: k, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...extra });

test('keys: [ ] and { } step 1 and 10, Up and Down only with nothing selected', () => {
  const on = { sliceMode: true };
  assert.deepEqual(S.sliceKey(key(']'), on), { kind: 'step', delta: 1 });
  assert.deepEqual(S.sliceKey(key('['), on), { kind: 'step', delta: -1 });
  // Shift changes the character: Shift+] arrives as }.
  assert.deepEqual(S.sliceKey(key('}', { shiftKey: true }), on), { kind: 'step', delta: 10 });
  assert.deepEqual(S.sliceKey(key('{', { shiftKey: true }), on), { kind: 'step', delta: -10 });
  assert.deepEqual(S.sliceKey(key('ArrowUp'), on), { kind: 'step', delta: 1 });
  assert.deepEqual(S.sliceKey(key('ArrowDown'), on), { kind: 'step', delta: -1 });
  assert.deepEqual(S.sliceKey(key('ArrowUp', { shiftKey: true }), on), { kind: 'step', delta: 10 });
  assert.deepEqual(S.sliceKey(key('ArrowDown', { shiftKey: true }), on), { kind: 'step', delta: -10 });
  assert.deepEqual(S.sliceKey(key('PageUp'), on), { kind: 'step', delta: 10 });
  assert.deepEqual(S.sliceKey(key('PageDown'), on), { kind: 'step', delta: -10 });
  assert.deepEqual(S.sliceKey(key('Home'), on), { kind: 'edge', which: 'first' });
  assert.deepEqual(S.sliceKey(key('End'), on), { kind: 'edge', which: 'last' });
  // With an annotation selected, Up and Down walk along its links (not ours); the rest still step.
  const selected = { sliceMode: true, selected: true };
  assert.equal(S.sliceKey(key('ArrowUp'), selected), null);
  assert.equal(S.sliceKey(key('ArrowDown', { shiftKey: true }), selected), null);
  assert.deepEqual(S.sliceKey(key(']'), selected), { kind: 'step', delta: 1 });
  assert.deepEqual(S.sliceKey(key('PageUp'), selected), { kind: 'step', delta: 10 });
  // Left and Right, the camera keys, and everything else stay with the renderer.
  for (const k of ['ArrowLeft', 'ArrowRight', 'z', 'Z', 'q', 'm', 'c', 'f', 'a', 'Escape']) {
    assert.equal(S.sliceKey(key(k), on), null, k);
  }
});

test('keys: not in slice mode, or with a modifier, nothing is taken', () => {
  for (const k of [']', '[', 'ArrowUp', 'PageUp', 'Home', 'End']) {
    assert.equal(S.sliceKey(key(k), { sliceMode: false }), null, k);
    assert.equal(S.sliceKey(key(k), {}), null, k);
    for (const mod of ['altKey', 'ctrlKey', 'metaKey']) {
      assert.equal(S.sliceKey(key(k, { [mod]: true }), { sliceMode: true }), null, `${k} ${mod}`);
    }
  }
});

test('keys: Up is the higher section, and the lower one under Flip Z, as stepAlongZ', () => {
  const flipped = { sliceMode: true, flipZ: true };
  assert.deepEqual(S.sliceKey(key('ArrowUp'), flipped), { kind: 'step', delta: -1 });
  assert.deepEqual(S.sliceKey(key('ArrowDown'), flipped), { kind: 'step', delta: 1 });
  assert.deepEqual(S.sliceKey(key('ArrowUp', { shiftKey: true }), flipped), { kind: 'step', delta: -10 });
  assert.deepEqual(S.sliceKey(key(']'), flipped), { kind: 'step', delta: -1 });
  assert.deepEqual(S.sliceKey(key('{', { shiftKey: true }), flipped), { kind: 'step', delta: 10 });
  assert.deepEqual(S.sliceKey(key('PageUp'), flipped), { kind: 'step', delta: -10 });
  // Home and End are by section number: Flip Z does not touch them.
  assert.deepEqual(S.sliceKey(key('Home'), flipped), { kind: 'edge', which: 'first' });
  assert.deepEqual(S.sliceKey(key('End'), flipped), { kind: 'edge', which: 'last' });
});

// -- the words ------------------------------------------------------------------------------------------

test('the facts line is the Registration lines\' style, with the cap said when it bites', () => {
  const rows = [node(1, 1, 0, 0), node(2, 2, 100, 0)];
  for (let i = 0; i < 2183; i += 1) rows.push(node(10 + i, 3 + (i % 40), i % 100, 1, { shift: [0.05 + (i % 200) * 0.001, 0] }));
  const f = S.prepareField(rows, 'correction');
  const sel = S.selectMarks(f, { min: 0.25, cap: 2000 });
  const line = S.factsLine(f, sel);
  assert.match(line, /^\d{1,3}(,\d{3})? of 2,183 moved shown · median 0\.\d\d · 95% 0\.\d\d · max 0\.\d\d µm$/);
  const capped = S.selectMarks(f, { min: 0, cap: 100 });
  assert.match(S.factsLine(f, capped), /^strongest 100 of 2,183 moved shown · median/);
  assert.match(S.factsLine(f, capped, 31), /^31 of 2,183 moved shown · median/);        // a slice shows a window
  assert.equal(S.factsLine(S.prepareField([node(1, 1, 0, 0)], 'correction'), sel), 'no annotation moved');
  // Exactly the numbers of the c1178 measurement, formatted.
  const c = c1178Like();
  const cf = S.prepareField(c.nodes, 'correction');
  const csel = S.selectMarks(cf, { min: 0.25 });
  assert.equal(S.factsLine(cf, csel), `${csel.n} of 21 moved shown · median 0.30 · 95% 0.56 · max 3.99 µm`);
});

test('the badge, the chip, and the header summary', () => {
  assert.equal(S.badgeText(1), '');
  assert.equal(S.badgeText(13.5), '×14');
  assert.equal(S.badgeText(2.46), '×2.5');
  assert.equal(S.badgeText(100), '×100');
  assert.equal(S.chipText({ k: 13.5, slice: true, section: 939, z: 84.51, half: 5, shown: 31 }),
    'Shifts · ×14 · section 939 (84.51 µm) ±5 · 31 shown');
  assert.equal(S.chipText({ k: 1, slice: false, shown: 429 }), 'Shifts · all sections · 429 shown');
  assert.equal(S.chipText({ k: 1, slice: false, shown: 2000, truncated: true }), 'Shifts · all sections · strongest 2,000');
  assert.equal(S.summaryText({}), 'off');
  assert.equal(S.summaryText({ showShift: true }, 13.5), 'Correction · all sections · ≥0.25 µm · ×14');
  assert.equal(S.summaryText({ showShift: true, shiftSlice: true, shiftPair: 'transforms' }, 1),
    'Transforms · section ±5 · ≥0.05 µm');
  assert.equal(S.summaryText({ showShift: true, shiftPair: 'both' }, 1, 'correction'), 'Correction · all sections · ≥0.25 µm');
});

test('the server\'s verdict and a coherent section, in words', () => {
  const summary = {
    links: {
      n_links: 2428,
      stages: {
        stored: { median_um: 0.237, n_over_1p5_um: 116 },
        registered: { median_um: 0.237, n_over_1p5_um: 116 },
        corrected: { median_um: 0.1819, n_over_1p5_um: 71 },
      },
      verdict: { before: 'registered', after: 'corrected', n_links: 2428, share_shrank: 0.648, share_grew: 0.288 },
    },
  };
  assert.equal(S.verdictLine(summary),
    'links across sections: median step 0.237 to 0.182 µm · 65% shrank, 29% grew · over 1.5 µm 116 to 71');
  assert.equal(S.verdictLine(null), '');
  assert.equal(S.verdictLine({ links: { verdict: { n_links: 0 }, stages: {} } }), '');
  assert.equal(S.verdictLine({ links: { verdict: { n_links: 3, before: 'stored', after: 'registered' }, stages: { stored: null } } }), '');
  assert.equal(S.coherentChipText({ section: 939, n: 6, mean_dx_um: -0.62, mean_dy_um: 0.65 }), '939 · 6 · (-0.62, +0.65) µm');
});

test('a volume without current transforms offers the correction alone, whatever was saved', () => {
  assert.deepEqual([...S.pairsFor(true)], ['correction', 'transforms', 'both']);
  assert.deepEqual([...S.pairsFor(false)], ['correction']);
  const summary = {
    section_correction: { requested: true, applied: true, n_moved: 5 },
    // A state saved with the transforms on, for a volume whose transforms are not read here.
    live_registration: { requested: true, note: "RC1's section transforms are not read here; stored positions" },
  };
  const none = S.pairAvailability(summary, { correction: 5, transforms: 0 }, { hasTransforms: false });
  assert.equal(none.correction.available, true);
  assert.equal(none.transforms.available, false);
  assert.equal(none.both.available, false);
  assert.match(none.both.reason, /Both is the section correction/);
  for (const wanted of ['correction', 'transforms', 'both']) assert.equal(S.effectivePair(wanted, none), 'correction');
  // With them, as before: Both is there when either stage moved something.
  const some = S.pairAvailability(summary, { correction: 5, transforms: 0 });
  assert.equal(some.both.available, true);
});

test('every control has a help text and the empty state a line', () => {
  for (const key of ['master', 'pair', 'arrows', 'rings', 'scale', 'min', 'slice', 'window', 'lookDownZ',
    'yDown', 'color', 'colorMax', 'through', 'opacity', 'width', 'apply']) {
    assert.ok(S.SHIFT_HELP[key] && S.SHIFT_HELP[key].length > 20, key);
  }
  // A figure is named only for a control that has help, and the explanations say what they draw.
  for (const key of Object.keys(S.SHIFT_FIGURES)) assert.ok(S.SHIFT_HELP[key], key);
  assert.match(S.SHIFT_HELP.pair, /stored/);
  assert.match(S.SHIFT_HELP.pair, /registered/);
  assert.match(S.SHIFT_HELP.pair, /corrected/);
  assert.match(S.SHIFT_HELP.scale, /head never moves/);
  // The Y-down view is not Viking's orientation until it has been checked there.
  assert.doesNotMatch(S.SHIFT_HELP.yDown, /Viking/);
  assert.match(S.EMPTY_TEXT, /No annotation of this cell moved/);
  assert.deepEqual(Object.keys(S.PAIR_LABELS), ['correction', 'transforms', 'both']);
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
