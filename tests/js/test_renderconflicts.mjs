// Render settings that will not build as set (static/js/renderconflicts.js): which
// controls the panel marks and what it offers to change, best first, from the
// server's estimates.  Run it with
//
//     node tests/js/test_renderconflicts.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const rc = await import(pathToFileURL(path.join(jsDir, 'renderconflicts.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** A Closed curve estimate as the server answers it (Inferior c16137 at 0.008 um, a 3 GB server). */
const curve = (over = {}) => ({
  applies: true, method: 'closedcurve', pixel_size_um: 0.008,
  voxels: 40_000_000, faces: 14_000_000, triangles: 27_000_000,
  peak_mb: 12000, serve_mb: 8000, server_mb: 21000, json_mb: 977, tab_mb: 1600, seconds: 200,
  over: ['faces', 'memory', 'page'], finest_um: 0.027, binding: 'memory',
  limits: { voxels: 300_000_000, faces: 10_000_000, memory_mb: 3000, json_mb: 500 },
  basis: { kind: 'built', pixel_um: 0.1 }, alternatives: [],
  ...over,
});

/** A field estimate (RPC1 c430 under Voxel remesh, as the server answered on 2026-10-20). */
const field = (over = {}) => ({
  applies: false, peak_mb: 1288,
  field: {
    applies: true, pathway: 'voxel2', typed: true, typed_um: 0.03, asked_um: 0.03, floor_um: 0.02,
    voxel_um: 0.0763, coarsened: true, reason: 'faces', area_um2: 1997, voxel2_faces: 400000,
    raw_budget: 1_200_000, raw_triangles: 1_200_000, triangles: 400000, work: 21_000_000,
    work_budget: 60_000_000, peak_mb: 1288, gate_mb: 1288,
    cap_for_asked: { voxel2_faces: 2_960_000, voxel2_um: 0.03, voxel_um: 0.03, reason: '',
      raw_triangles: 7_764_204, triangles: 2_960_000, peak_mb: 9493, gate_mb: 7547 },
    uncapped: { voxel2_faces: 0, voxel2_um: 0.03, voxel_um: 0.03, reason: '',
      raw_triangles: 7_764_204, triangles: 7_764_204, peak_mb: 3765, gate_mb: 4760 },
    finest_um: 0.0297, finest_at_cap_um: 0.0763,
    ...over,
  },
});

const cc = { method: 'closedcurve', pathway: 'voxel2', pixel_size_um: 0.008, curve_tie_um: 5 };
const vr = { method: 'cylinder', pathway: 'voxel2', voxel2_um: 0.03, voxel2_faces: 400000 };

// -- Closed curve ----------------------------------------------------------------------

test('a size past a limit marks Voxel and offers the finest that fits first', () => {
  const got = rc.renderConflicts([{ name: 'c16137', estimate: curve() }], cc);
  assert.deepEqual(got.keys, ['pixel_size_um']);
  assert.equal(got.headline, 'Voxel 0.008 µm is too fine for c16137');
  assert.match(got.reasons[0], /^c16137: ≈ 14 M voxel faces against the limit of 10 M/);
  assert.deepEqual(got.suggestions.map((s) => s.set), [{ pixel_size_um: 0.027 }]);
  assert.equal(got.suggestions[0].best, true);
  assert.match(got.note, /finest size that fits/);
});

test('with Join bodies on, its alternative is offered and Join bodies is marked too', () => {
  const alternatives = [{ off: 'joins', set: { curve_tie_um: 0 }, finest_um: 0.022, fits: false }];
  const got = rc.renderConflicts([{ name: 'c16137', estimate: curve({ alternatives }) }], cc);
  assert.deepEqual(got.keys.sort(), ['curve_tie_um', 'pixel_size_um']);
  assert.equal(got.suggestions[1].text, 'Join bodies off, Voxel 0.022 µm');
  assert.deepEqual(got.suggestions[1].set, { curve_tie_um: 0, pixel_size_um: 0.022 });
  assert.equal(got.suggestions[1].best, undefined);
});

test('an alternative that fits the size typed changes only the setting it turns off', () => {
  const alternatives = [{ off: 'averaging', set: { curve_avg_paths: 0 }, finest_um: 0.006, fits: true }];
  const got = rc.renderConflicts([{ name: 'c2628', estimate: curve({ alternatives }) }],
    { ...cc, curve_avg_paths: 0.25 });
  const way = got.suggestions.find((s) => s.text === 'smoothing off');
  assert.deepEqual(way.set, { curve_avg_paths: 0 });
  assert.match(way.detail, /0\.008 µm fits c2628 as typed/);
});

test('several cells: one size every one fits at, each one named', () => {
  const got = rc.renderConflicts([
    { name: 'c1178', estimate: curve({ finest_um: 0.022 }) },
    { name: 'c16137', estimate: curve({ finest_um: 0.027 }) },
    { name: 'c943', estimate: curve({ over: [] }) },
  ], cc);
  assert.equal(got.headline, 'Voxel 0.008 µm is too fine for 2 of 3 cells');
  assert.deepEqual(got.suggestions[0].set, { pixel_size_um: 0.027 });
  assert.match(got.suggestions[0].detail, /c1178 0\.022, c16137 0\.027 µm/);
  // An alternative only some of them have is not offered.
  const some = rc.renderConflicts([
    { name: 'c1178', estimate: curve({ alternatives: [{ off: 'joins', set: { curve_tie_um: 0 }, finest_um: 0.02, fits: false }] }) },
    { name: 'c16137', estimate: curve() },
  ], cc);
  assert.equal(some.suggestions.length, 1);
});

test('nothing past a limit, an interim answer or none yet: no conflict', () => {
  assert.equal(rc.renderConflicts([{ name: 'c1', estimate: curve({ over: [] }) }], cc), null);
  assert.equal(rc.renderConflicts([{ name: 'c1', estimate: curve({ pending: true }) }], cc), null);
  assert.equal(rc.renderConflicts([], cc), null);
});

// -- Voxel remesh: the Fine voxel and the Face cap ------------------------------------------

test('a typed Fine voxel the Face cap holds coarser marks both, the least cap first', () => {
  const got = rc.renderConflicts([{ name: 'c430', estimate: field() }], vr);
  assert.deepEqual(got.keys.sort(), ['voxel2_faces', 'voxel2_um']);
  assert.equal(got.headline, 'Fine voxel 0.03 µm will be built coarser for c430');
  assert.match(got.reasons[0], /^c430: builds at 0\.0763 µm, not 0\.03 µm: Face cap 400,000 lets/);
  assert.deepEqual(got.suggestions.map((s) => s.text), [
    'Face cap 2,960,000', 'Face cap 0 (keep every triangle)', 'Fine voxel 0.0763 µm']);
  assert.deepEqual(got.suggestions.map((s) => s.set), [
    { voxel2_faces: 2960000 }, { voxel2_faces: 0 }, { voxel2_um: 0.0763 }]);
  assert.deepEqual(got.suggestions.map((s) => !!s.best), [true, false, false]);
  assert.match(got.suggestions[0].detail, /builds 0\.03 µm as typed: ≈ 7\.8 M raw triangles decimated to 3\.0 M, the build ≈ 9\.5 GB/);
  assert.match(got.suggestions[1].detail, /≈ 7\.8 M triangles to this page, the build ≈ 3\.8 GB/);
});

test('a voxel no Face cap reaches: Face cap 0 builds the finest there is, and is said so', () => {
  const got = rc.renderConflicts([{ name: 'c1178', estimate: field({
    voxel_um: 0.1202, cap_for_asked: null, finest_um: 0.0417,
    uncapped: { voxel2_faces: 0, voxel2_um: 0.03, voxel_um: 0.0417, reason: 'faces',
      raw_triangles: 10_000_000, triangles: 10_000_000, peak_mb: 4760, gate_mb: 4760 },
  }) }], vr);
  assert.equal(got.suggestions[0].text, 'Face cap 0 (keep every triangle)');
  assert.equal(got.suggestions[0].best, true);
  assert.match(got.suggestions[0].detail, /builds 0\.0417 µm, the finest any Face cap allows/);
  assert.deepEqual(got.suggestions[1].set, { voxel2_um: 0.0417, voxel2_faces: 0 });
  assert.deepEqual(got.suggestions.at(-1).set, { voxel2_um: 0.1202 });
});

test('under the finest typed voxel: only the Fine voxel, and 0.02 µm offered', () => {
  const got = rc.renderConflicts([{ name: 'c943', estimate: field({
    typed_um: 0.01, asked_um: 0.02, voxel_um: 0.02, reason: 'floor', voxel2_faces: 0,
    cap_for_asked: null, uncapped: null, finest_um: 0.02,
  }) }], { ...vr, voxel2_um: 0.01, voxel2_faces: 0 });
  assert.deepEqual(got.keys, ['voxel2_um']);
  assert.match(got.reasons[0], /0\.02 µm is the finest Fine voxel a build takes/);
  assert.deepEqual(got.suggestions.map((s) => s.set), [{ voxel2_um: 0.02 }]);
});

test('an automatic Fine voxel the cap holds is a note for the chip, not a conflict', () => {
  const auto = field({ typed: false, typed_um: 0.04, asked_um: 0.04,
    cap_for_asked: { voxel2_faces: 1_460_000, voxel_um: 0.04, raw_triangles: 4_367_364, triangles: 1_460_000, peak_mb: 3449 },
    uncapped: { voxel2_faces: 0, voxel_um: 0.04, raw_triangles: 4_367_364, triangles: 4_367_364, peak_mb: 2254 } });
  const options = { ...vr, voxel2_um: 0 };
  assert.equal(rc.renderConflicts([{ name: 'c430', estimate: auto }], options), null);
  assert.deepEqual(rc.fieldNotes([{ name: 'c430', estimate: auto }], options), [
    'c430: Fine voxel auto is 0.0763 µm; its annotations ask for 0.04 µm; Face cap 1,460,000 (≈ 3.4 GB)'
      + ' or 0 (≈ 4.4 M triangles, ≈ 2.3 GB) builds it there.']);
});

test('a suggestion that changes nothing, or repeats another, is left out', () => {
  // Face cap already 0: "Face cap 0" would change nothing; the finest is offered once.
  const got = rc.renderConflicts([{ name: 'c1178', estimate: field({
    voxel_um: 0.0417, voxel2_faces: 0, raw_budget: 10_000_000, cap_for_asked: null, uncapped: null,
    finest_um: 0.0417, finest_at_cap_um: 0.0417,
  }) }], { ...vr, voxel2_faces: 0 });
  assert.deepEqual(got.suggestions.map((s) => s.set), [{ voxel2_um: 0.0417 }]);
  assert.equal(got.suggestions[0].best, true);
  assert.match(got.suggestions[0].detail, /^the finest any budget allows c1178/);

});

test('other methods and pathways are never marked', () => {
  const e = [{ name: 'c430', estimate: field() }];
  assert.equal(rc.renderConflicts(e, { ...vr, pathway: 'voxel' }), null);
  assert.equal(rc.renderConflicts(e, { ...vr, pathway: 'original' }), null);
  assert.equal(rc.renderConflicts(e, { ...vr, method: 'hybrid' }), null);
  assert.equal(rc.buildsField({ method: 'curvemesh', pathway: 'ultraliser' }), true);
  assert.equal(rc.buildsField({ method: 'closedcurve', pathway: 'voxel2' }), false);
  assert.deepEqual(rc.withSuggestion(vr, { set: { voxel2_faces: 0 } }).voxel2_faces, 0);
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
