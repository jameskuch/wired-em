// The words and the cache of the build estimate (static/js/renderestimate.js): the
// cost chip of an outline method, the notice that names each cell's Voxel size, the
// Fine voxel notice, and the cache that asks the server once per cell and settings,
// never while a size is being typed.  Run it with
//
//     node tests/js/test_renderestimate.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const est = await import(pathToFileURL(path.join(jsDir, 'renderestimate.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** An estimate as the server answers it (RPC1 c2628 at 0.025 um, as measured). */
const estimate = (over = {}) => ({
  applies: true, method: 'closedcurve', pixel_size_um: 0.025,
  voxels: 115_759_104, faces: 6_780_000, triangles: 9_580_000,
  peak_mb: 3227, serve_mb: 2874, server_mb: 6101, json_mb: 344.8, tab_mb: 575, seconds: 104.2,
  over: [], finest_um: 0.021, binding: 'faces',
  limits: { voxels: 300_000_000, faces: 10_000_000, memory_mb: 9000, json_mb: 500 },
  basis: { kind: 'reference', pixel_um: 0.1 },
  ...over,
});
const small = (over = {}) => estimate({
  pixel_size_um: 0.05, voxels: 587_000, faces: 49_000, triangles: 91_000, peak_mb: 477,
  serve_mb: 27, server_mb: 504, json_mb: 3, tab_mb: 5, seconds: 3, finest_um: 0.005, binding: null,
  ...over,
});

test('numbers in words: counts, megabytes, seconds', () => {
  assert.equal(est.countText(940), '940');
  assert.equal(est.countText(91_000), '91 k');
  assert.equal(est.countText(2_140_000), '2.1 M');
  assert.equal(est.countText(24_689_100), '25 M');
  assert.equal(est.mbText(504), '504 MB');
  assert.equal(est.mbText(6101), '6.1 GB');
  assert.equal(est.mbText(21_000), '21 GB');
  assert.equal(est.secondsText(0.4), 'under 1 s');
  assert.equal(est.secondsText(37), '37 s');
  assert.equal(est.secondsText(104.2), '1 min 44 s');
  assert.equal(est.secondsText(120), '2 min');
});

test('the levels: plain, amber for a long wait or a large share of the memory, red past a limit', () => {
  assert.equal(est.estimateLevel(small()), 'ok');
  assert.equal(est.estimateLevel(small({ seconds: est.LONG_WAIT_S + 1 })), 'warn');
  assert.equal(est.estimateLevel(small({ server_mb: 4600 })), 'warn', 'over half of 9,000 MB');
  assert.equal(est.estimateLevel(small({ tab_mb: est.TAB_HEAP_MB + 1 })), 'warn');
  assert.equal(est.estimateLevel(estimate({ over: ['faces'] })), 'over');
  assert.equal(est.estimateLevel({ applies: false }), 'ok');
  assert.equal(est.estimateLevel(null), 'ok');
  assert.deepEqual(est.warnings(estimate()), [
    'about 1 min 44 s on the server',
    '6.1 GB of the 9.0 GB the server gives a build, so it builds alone',
    'about 575 MB more in this tab']);
});

test('the size a cell is built at: the finest that fits, only ever coarser than asked', () => {
  const over = estimate({ over: ['voxels', 'faces', 'memory', 'page'], pixel_size_um: 0.012 });
  assert.equal(est.fittedPixel(over, { method: 'closedcurve', pixel_size_um: 0.012 }), 0.021);
  assert.equal(est.fittedPixel(estimate(), { method: 'closedcurve', pixel_size_um: 0.025 }), null, 'not over: as asked');
  assert.equal(est.fittedPixel(estimate({ over: ['faces'], finest_um: 0.02 }), { method: 'closedcurve', pixel_size_um: 0.025 }), null);
  assert.equal(est.fittedPixel(estimate({ over: ['memory'], finest_um: null }), { method: 'closedcurve', pixel_size_um: 0.025 }), null,
    'nothing fits: the build is left to the server, which says so');
  assert.equal(est.fittedPixel({ applies: false }, { method: 'closedcurve', pixel_size_um: 0.025 }), null);
  assert.equal(est.fittedPixel(null, { method: 'closedcurve', pixel_size_um: 0.025 }), null);
  assert.equal(est.fittedPixel(estimate({ empty: true, over: ['faces'] }), { method: 'closedcurve', pixel_size_um: 0.025 }), null);
});

test('why, in one phrase: the limit that sets the size, and how many more', () => {
  const over = estimate({ over: ['voxels', 'faces', 'memory'], binding: 'faces', faces: 24_689_100 });
  assert.equal(est.mainReason(over),
    '≈ 25 M voxel faces against the limit of 10 M, and past 2 more limits');
  assert.equal(est.mainReason(estimate({ over: ['memory'], binding: 'faces', server_mb: 9500 })),
    '≈ 9.5 GB of server memory against the 9.0 GB it gives a build');
  assert.equal(est.mainReason(estimate()), '');
  assert.equal(est.overReasons(estimate({ over: ['page'], json_mb: 577 }))[0],
    '≈ 577 MB of text for the page, more than a tab can read (500 MB)');
});

test('the chip: one cell, several, past a limit', () => {
  const one = est.estimateChip([{ name: 'c943', estimate: small() }]);
  assert.equal(one.text, '≈ 91 k tri · 504 MB · 3 s');
  assert.equal(one.level, 'ok');
  assert.match(one.help, /^Building c943 at this Voxel size, as the server estimates it before building:/);
  assert.match(one.help, /c943 at 0\.05 µm: ≈ 49 k voxel faces \(limit 10 M\) from 587 k raster voxels/);
  assert.match(one.help, /Finest size that fits: 0\.005 µm\./);
  assert.match(one.help, /a raster at 0\.1 µm/);
  const heavy = est.estimateChip([{ name: 'c2628', estimate: estimate() }]);
  assert.equal(heavy.text, '⚠ 9.6 M tri · 6.1 GB · 1 min 44 s');
  assert.equal(heavy.level, 'warn');
  assert.match(heavy.help, /Long or heavy: c2628: about 1 min 44 s on the server/);
  assert.match(heavy.help, /Finest size that fits: 0\.021 µm \(voxel faces\)\./);
  const red = est.estimateChip([{ name: 'c2628', estimate: estimate({ over: ['faces'] }) }]);
  assert.equal(red.text, '✕ too fine · fits at 0.021 µm');
  assert.equal(red.level, 'over');
  assert.match(red.help, /Past the limit: ≈ 6\.8 M voxel faces against the limit of 10 M/);
  assert.match(red.help, /Apply builds each cell that is too fine at the finest size that fits it/);
  const two = est.estimateChip([
    { name: 'c943', estimate: small() }, { name: 'c2628', estimate: estimate({ over: ['faces'] }) }]);
  assert.equal(two.text, '✕ 1 of 2 too fine');
  assert.match(two.help, /^Building 2 cells at this Voxel size/);
  // Amber for the sum of several cells' waits, each short.
  const many = est.estimateChip(Array.from({ length: 4 }, (_, i) => (
    { name: `c${i}`, estimate: small({ seconds: 20 }) })));
  assert.equal(many.level, 'warn');
  assert.match(many.help, /about 1 min 20 s for all of them/);
  // A cell the estimate cannot speak for is left out; none at all is no chip.
  assert.equal(est.estimateChip([{ name: 'c1', estimate: { applies: false } }]), null);
  assert.equal(est.estimateChip([{ name: 'c1', estimate: null }]), null);
});

test('the notice names each cell and its size', () => {
  const one = est.coarsenedNotice([{ name: 'c2628', from: 0.012, to: 0.021, why: 'voxel faces' }]);
  assert.equal(one.headline, 'Voxel size set to 0.021 µm');
  assert.equal(one.detail, ' c2628: 0.012 → 0.021 µm (voxel faces). Each is the finest size that fits that '
    + 'cell; a finer one is past what the server or this page can hold.');
  const two = est.coarsenedNotice([
    { name: 'c2628', from: 0.012, to: 0.021, why: '' }, { name: 'c16137', from: 0.012, to: 0.013, why: '' }]);
  assert.equal(two.headline, 'Voxel size set per cell (2 cells)');
  assert.match(two.detail, /^ c2628: 0\.012 → 0\.021 µm; c16137: 0\.012 → 0\.013 µm\./);
  const same = est.coarsenedNotice([
    { name: 'c1', from: 0.01, to: 0.03, why: '' }, { name: 'c2', from: 0.01, to: 0.03, why: '' }]);
  assert.equal(same.headline, 'Voxel size set to 0.03 µm');
  assert.equal(est.coarsenedNotice([]), null);
});

test('the Fine voxel notice: a typed size that came out coarser, and why', () => {
  const mesh = { voxel2_um: 0.02, voxel2_asked_um: 0.01, voxel2_coarsened: true, voxel2_reason: 'floor' };
  const note = est.fineVoxelNotice('c943', mesh, { voxel2_um: 0.01 });
  assert.equal(note.headline, 'Fine voxel built at 0.02 µm');
  assert.equal(note.detail, ' for c943, not the 0.01 µm typed: the whole-cell grid\'s finest typed voxel is 0.02 µm.');
  assert.match(est.fineVoxelNotice('c1', { ...mesh, voxel2_um: 0.0712, voxel2_reason: 'faces' },
    { voxel2_um: 0.05 }).detail, /more raw triangles than this Face cap allows/);
  assert.match(est.fineVoxelNotice('c1', { ...mesh, voxel2_um: 0.09, voxel2_reason: 'work' },
    { voxel2_um: 0.05 }).headline, /0\.09 µm/);
  assert.equal(est.fineVoxelNotice('c1', mesh, { voxel2_um: 0 }), null, 'automatic: the server\'s to size');
  assert.equal(est.fineVoxelNotice('c1', { ...mesh, voxel2_coarsened: false }, { voxel2_um: 0.02 }), null);
  assert.equal(est.fineVoxelNotice('c1', {}, { voxel2_um: 0.02 }), null, 'another technique says nothing');
});

test('what the server is asked: the build\'s options, under the cell, its copy and its load', () => {
  const q = est.estimateQuery({ method: 'closedcurve', pixel_size_um: 0.03, clip_ipl: 0.4, keep_above: false });
  assert.equal(q.method, 'closedcurve');
  assert.equal(q.pixel_size_um, 0.03);
  assert.ok(!('clip_ipl' in q) && !('keep_above' in q) && !('color_by_strata' in q));
  const record = { key: 'i:943', summary: { copy_tag: 'curvefit.1' }, loadGen: 2 };
  const key = est.estimateKey(record, { method: 'closedcurve', pixel_size_um: 0.03 });
  assert.ok(key.startsWith('i:943|curvefit.1|2|'));
  assert.notEqual(key, est.estimateKey({ ...record, loadGen: 3 }, { method: 'closedcurve', pixel_size_um: 0.03 }));
  assert.notEqual(key, est.estimateKey(record, { method: 'closedcurve', pixel_size_um: 0.031 }));
  // A knob the method ignores is the same estimate.
  assert.equal(key, est.estimateKey(record, { method: 'closedcurve', pixel_size_um: 0.03, tie_um: 3 }));
});

const record = (id) => ({ key: `i:${id}`, id, source: 'i', summary: { copy_tag: 'off.0' }, loadGen: 1 });

test('the cache asks once per cell and settings, and shares a request in the air', async () => {
  const asked = [];
  const cache = new est.EstimateCache(async (r, o) => { asked.push([r.id, o.pixel_size_um]); await pause(5); return { id: r.id, px: o.pixel_size_um }; },
    { delay: 10 });
  const [a, b] = await Promise.all([cache.get(record(1), { method: 'closedcurve', pixel_size_um: 0.03 }), cache.get(record(1), { method: 'closedcurve', pixel_size_um: 0.03 })]);
  assert.deepEqual(a, b);
  assert.deepEqual(asked, [[1, 0.03]]);
  assert.deepEqual(cache.peek(record(1), { method: 'closedcurve', pixel_size_um: 0.03 }), { id: 1, px: 0.03 });
  assert.equal(cache.peek(record(1), { method: 'closedcurve', pixel_size_um: 0.04 }), undefined);
  await cache.get(record(1), { method: 'closedcurve', pixel_size_um: 0.03 });
  assert.equal(asked.length, 1, 'held');
});

test('a server that cannot answer is null, kept a while, then asked again', async () => {
  let fail = true;
  let calls = 0;
  const cache = new est.EstimateCache(async () => { calls += 1; if (fail) throw new Error('503'); return { ok: 1 }; },
    { delay: 5, retryMs: 30 });
  assert.equal(await cache.get(record(2), { method: 'closedcurve', pixel_size_um: 0.03 }), null);
  assert.equal(cache.peek(record(2), { method: 'closedcurve', pixel_size_um: 0.03 }), null, 'held as "cannot say"');
  await cache.get(record(2), { method: 'closedcurve', pixel_size_um: 0.03 });
  assert.equal(calls, 1);
  fail = false;
  await pause(50);
  assert.deepEqual(await cache.get(record(2), { method: 'closedcurve', pixel_size_um: 0.03 }), { ok: 1 });
  assert.equal(calls, 2);
});

test('typing asks once, for the size typed last; the same request again keeps its place', async () => {
  const asked = [];
  const cache = new est.EstimateCache(async (r, o) => { asked.push(o.pixel_size_um); return { px: o.pixel_size_um }; },
    { delay: 40 });
  let answered = null;
  for (const px of [0.0, 0.03, 0.032]) {
    cache.soon('chip', [record(3)], { method: 'closedcurve', pixel_size_um: px }, (answers) => { answered = answers; });
    await pause(10);
  }
  await pause(80);
  assert.deepEqual(asked, [0.032]);
  assert.deepEqual(answered, [{ px: 0.032 }]);
  // A panel that redraws every 10 ms with the same settings is answered after the delay, not never.
  asked.length = 0;
  answered = null;
  for (let i = 0; i < 10; i += 1) {
    cache.soon('chip', [record(4)], { method: 'closedcurve', pixel_size_um: 0.05 }, (answers) => { answered = answers; });
    await pause(10);
  }
  await pause(60);
  assert.deepEqual(asked, [0.05]);
  assert.deepEqual(answered, [{ px: 0.05 }]);
  // Two purposes do not cancel each other.
  asked.length = 0;
  cache.soon('chip', [record(5)], { method: 'closedcurve', pixel_size_um: 0.06 }, () => {});
  cache.soon('compare-chip', [record(6)], { method: 'closedcurve', pixel_size_um: 0.07 }, () => {});
  await pause(80);
  assert.deepEqual(asked.sort(), [0.06, 0.07]);
});

test('a size is set after its estimate only if nothing changed meanwhile (review 2026-10-16)', () => {
  const asked = { method: 'closedcurve', pixel_size_um: 0.01 };
  const over = estimate({ over: ['faces'], finest_um: 0.022, pixel_size_um: 0.01 });
  assert.equal(est.fitStillWanted(over, asked, { ...asked }, true), 0.022);
  // 0.1 applied while the estimate for 0.01 was out: the person's size stays.
  assert.equal(est.fitStillWanted(over, asked, { ...asked, pixel_size_um: 0.1 }, true), null);
  // A newer draw of the cell, or the cell gone: nothing is written.
  assert.equal(est.fitStillWanted(over, asked, { ...asked }, false), null);
  assert.equal(est.fitStillWanted(over, asked, undefined, true), null);
  // A knob the method ignores changed meanwhile: still the same build.
  assert.equal(est.fitStillWanted(over, asked, { ...asked, tie_um: 3 }, true), 0.022);
  // Pending says nothing to fit.
  assert.equal(est.fitStillWanted({ ...over, pending: true }, asked, { ...asked }, true), null);
});

test('an interim answer is held a moment, then asked again; settled waits out a pending one', async () => {
  let n = 0;
  const answers = [{ applies: true, pending: true, approximate: true },
    { ...small(), approximate: true }, small()];
  const cache = new est.EstimateCache(async () => answers[Math.min(n++, 2)], { delay: 5, interimMs: 20 });
  const opts = { method: 'closedcurve', pixel_size_um: 0.03 };
  const first = await cache.get(record(7), opts);
  assert.equal(first.pending, true);
  assert.equal(est.interim(first), true);
  assert.equal(est.usable(first), false, 'a pending answer is not drawn as an estimate');
  assert.equal(cache.peek(record(7), opts), first, 'held for the moment');
  await pause(40);
  assert.equal(cache.peek(record(7), opts), undefined, 'and then let go, to be asked again');
  const settled = await cache.settled(record(7), opts);
  assert.ok(!settled.pending, 'settled asks again while pending');
  assert.equal(n, 2);
  // A finished answer is kept.
  await pause(40);
  const last = await cache.get(record(7), opts);
  assert.equal(est.interim(last), false);
  await pause(40);
  assert.deepEqual(cache.peek(record(7), opts), last);
});

test('more cells selected than the cache keeps are asked once each, not for ever (review 2026-10-16)', async () => {
  let asked = 0;
  const cache = new est.EstimateCache(async (r) => { asked += 1; return { id: r.id }; }, { delay: 5, keep: 400 });
  const many = Array.from({ length: 500 }, (_, i) => record(1000 + i));
  const opts = { method: 'closedcurve', pixel_size_um: 0.05 };
  let answers = null;
  cache.soon('chip', many, opts, (got) => { answers = got; });
  await pause(60);
  assert.equal(answers.length, 500);
  assert.equal(asked, 500);
  assert.ok(many.every((r) => cache.peek(r, opts) !== undefined), 'every answer still held');
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}`);
    console.log(error);
  }
}
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
