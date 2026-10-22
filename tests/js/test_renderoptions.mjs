// The render options' data layer (static/js/renderoptions.js, built from the
// generated render-schema.js) and the form builder's pure decisions.  No JS
// runner exists in this repo; run it with
//
//     node tests/js/test_renderoptions.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free, no DOM.  tests/unit/test_js.py runs it with the other suites.

import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const ro = await load('renderoptions.js');
const stateModule = await load('state.js');
const { RENDER_FIELDS } = await load('render-schema.js');
const { DEFAULT_RENDER, normalizeRender, renderSignature } = ro;

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// What DEFAULT_RENDER was, key for key and in order, before it was built from
// the schema.  The undo history compares the JSON of the controls, so the
// order is part of the contract.
const BASELINE_KEYS = [
  'method', 'pathway', 'smooth_iterations', 'post_order', 'voxel_detail', 'voxel_smooth_um',
  'tie_um', 'ring_arc_um', 'wrap_spacing_um', 'wrap_blend', 'wrap_reject_sigma', 'wrap_passes',
  'voxel2_um', 'voxel2_faces', 'voxel2_blend_um', 'exact_tube_deg', 'conv_support', 'ultra_soma', 'ultra_smooth',
  'ultra_lambda', 'ultra_inflate', 'meshlab', 'meshlab_edge_um', 'meshlab_fraction',
  'meshlab_depth', 'reduce_fraction', 'pixel_size_um', 'curve_avg_discs', 'curve_avg_paths',
  'surface_smooth_um', 'curve_links_um', 'curve_tie_um', 'curve_bridge_um', 'curve_link_bridge_um',
  'color_by_strata', 'clip_ipl', 'keep_above',
];

// The hash of normalizeRender and renderSignature over 5,000 seeded inputs,
// taken from the tree this schema replaced (state.js with its own literal and
// bounds), twice.  Any change to a default, a bound, the clamp or the order of
// the keys changes it.  Re-pinned (2026-10-16) when the two closed-curve averaging
// strengths joined the keys: it was 603adb32... for the 29 the schema replaced; and
// again the same day when Closed curve's curved links, joined bodies and two bridges
// joined them (96971692... for the 31), when the Voxel size's floor went from 0.01
// to 0.005 um (a typed size: fa5dd918...), and when the viewer's Join bodies default
// became 5 um (2.6.2), when Kernel R/d's box stopped at 2.5 (2.7.1: b3220d8c...;
// a wider kernel loses the soma, convolution.MAX_PANEL_SUPPORT), and when Smooth surface
// joined the keys (2.9.0: d6b841e4..., then 902aab8c...), and Exact tubes after it.
const GOLDEN = 'cf4ebfd026021639db2f9d558e92b69c831d623358029e9f282575d2b98bad0f';

function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function goldenHash(normalize, signature, defaults) {
  const rnd = mulberry32(12345);
  const keys = Object.keys(defaults);
  const junk = [NaN, 'abc', '', null, undefined, -1, 0, 1e9, 0.5, '0.3', Infinity, true];
  const h = createHash('sha256');
  for (let i = 0; i < 5000; i += 1) {
    const o = {};
    for (const k of keys) {
      const r = rnd();
      if (r < 0.35) continue;
      if (r < 0.6) o[k] = junk[Math.floor(rnd() * junk.length)];
      else if (typeof defaults[k] === 'number') o[k] = (rnd() - 0.2) * 20 * (defaults[k] || 1);
      else if (typeof defaults[k] === 'string') {
        o[k] = ['voxel2', 'wrap', 'x', 'discs', 'sphere', 'none'][Math.floor(rnd() * 6)];
      }
    }
    o.stale_key = i;
    h.update(`${JSON.stringify(normalize(o))}|${signature(o)}\n`);
  }
  return h.digest('hex');
}

test('normalizeRender and renderSignature hash as they did before the schema', () => {
  assert.equal(goldenHash(normalizeRender, renderSignature, DEFAULT_RENDER), GOLDEN);
});

test('state.js re-exports the very same functions and defaults', () => {
  assert.equal(stateModule.normalizeRender, ro.normalizeRender);
  assert.equal(stateModule.renderSignature, ro.renderSignature);
  assert.equal(stateModule.DEFAULT_RENDER, ro.DEFAULT_RENDER);
  assert.equal(goldenHash(stateModule.normalizeRender, stateModule.renderSignature,
    stateModule.DEFAULT_RENDER), GOLDEN);
});

test('DEFAULT_RENDER has the old 29 keys in the old order with the six closed-curve ones, frozen', () => {
  assert.deepEqual(Object.keys(DEFAULT_RENDER), BASELINE_KEYS);
  assert.ok(Object.isFrozen(DEFAULT_RENDER));
  // A blank Clip IPL is `undefined`, and the key is there (the signature lists it).
  assert.ok('clip_ipl' in DEFAULT_RENDER && DEFAULT_RENDER.clip_ipl === undefined);
  assert.equal(Object.keys(normalizeRender({})).length, 37);
  // The averaging and the curved links are off until asked for, the viewer joins a Closed
  // curve surface's bodies up to 5 um apart (2.6.2; the API joins none), and the bridges
  // reach as far as they did when they were constants.
  assert.equal(DEFAULT_RENDER.curve_avg_discs, 0);
  assert.equal(DEFAULT_RENDER.curve_avg_paths, 0);
  assert.equal(DEFAULT_RENDER.curve_links_um, 0);
  assert.equal(DEFAULT_RENDER.curve_tie_um, 5);
  assert.equal(DEFAULT_RENDER.curve_bridge_um, 1);
  assert.equal(DEFAULT_RENDER.curve_link_bridge_um, 5);
  assert.deepEqual(Object.keys(normalizeRender({ stale: 1 })), [...BASELINE_KEYS, 'stale']);
});

test('the app default differs from the API default only in the pathway, Join bodies and IPL depth', () => {
  assert.equal(DEFAULT_RENDER.pathway, 'voxel2');
  assert.equal(DEFAULT_RENDER.curve_tie_um, 5);
  // The IPL depth method is the page's (Settings), never a cell's render: in no record's defaults.
  assert.deepEqual(RENDER_FIELDS.filter((f) => f.hasAppDefault).map((f) => f.key),
    ['pathway', 'curve_tie_um', 'ipl_method']);
  assert.ok(!('ipl_method' in DEFAULT_RENDER));
  for (const f of RENDER_FIELDS) {
    if (!f.inRender || f.key === 'pathway' || f.key === 'curve_tie_um') continue;
    assert.deepEqual(DEFAULT_RENDER[f.key], f.default === null ? undefined : f.default, f.key);
  }
});

test('normalizeRender keeps its semantics: clamp, round ints, non-finite to default', () => {
  const n = normalizeRender({
    voxel2_um: 0.5, tie_um: -5, smooth_iterations: 3.6, wrap_passes: 'abc', ring_arc_um: '0.3',
    pixel_size_um: 0, reduce_fraction: 0, voxel2_faces: 12345.6, meshlab_depth: 99,
  });
  assert.equal(n.voxel2_um, 0.3);
  assert.equal(n.tie_um, 0);
  assert.equal(n.smooth_iterations, 4);
  assert.equal(n.wrap_passes, DEFAULT_RENDER.wrap_passes);
  assert.equal(n.ring_arc_um, 0.3);
  assert.equal(n.pixel_size_um, 0.005);   // the Voxel box's floor (0.01 until 2026-10-16)
  assert.equal(n.reduce_fraction, 0.1);
  assert.equal(n.voxel2_faces, 12346);
  assert.equal(n.meshlab_depth, 11);
  // No enum strictness: a value the select does not list passes through.
  assert.equal(normalizeRender({ pathway: 'x' }).pathway, 'x');
  // Number('') is 0 and is then clamped to the minimum, not reset to the default.
  assert.equal(normalizeRender({ ring_arc_um: Number('') }).ring_arc_um, 0.1);
});

test('the bounds are the old twenty-one and the six closed-curve ones, with the same integer flags', () => {
  const old = {
    smooth_iterations: [0, 10, true], reduce_fraction: [0.1, 1], voxel_detail: [0.25, 3],
    voxel_smooth_um: [0, 2], tie_um: [0, 100], ring_arc_um: [0.1, 1.5],
    wrap_spacing_um: [0.15, 2], wrap_blend: [1.2, 6], wrap_reject_sigma: [0.5, 6],
    wrap_passes: [0, 6, true], voxel2_um: [0, 0.3], voxel2_faces: [0, 5000000, true],
    voxel2_blend_um: [0, 1], exact_tube_deg: [0, 60], conv_support: [1.2, 2.5], ultra_smooth: [0, 100, true],
    ultra_lambda: [0, 1], ultra_inflate: [0, 1], meshlab_edge_um: [0.05, 2],
    meshlab_fraction: [0.02, 1], meshlab_depth: [5, 11, true], pixel_size_um: [0.005, 1],
    curve_avg_discs: [0, 3], curve_avg_paths: [0, 2], curve_links_um: [0, 20],
    curve_tie_um: [0, 20], curve_bridge_um: [0, 10], curve_link_bridge_um: [0, 50],
    surface_smooth_um: [0, 2],
  };
  const expected = Object.fromEntries(Object.entries(old).map(
    ([k, [min, max, int]]) => [k, int ? { min, max, int } : { min, max }]));
  assert.deepEqual({ ...ro.RENDER_BOUNDS }, expected);
});

test('the key lists the app uses are derived from the schema', () => {
  assert.equal(ro.TECH_KNOBS.length, 31);
  assert.equal(ro.TECH_KNOBS[0], 'smooth_iterations');
  assert.equal(ro.TECH_KNOBS.at(-1), 'curve_link_bridge_um');
  assert.equal(ro.MESH_OPTION_KEYS.length, 34);
  assert.ok(ro.MESH_OPTION_KEYS.includes('method') && !ro.MESH_OPTION_KEYS.includes('clip_ipl'));
  assert.deepEqual(ro.EXPORT_KEYS, ro.MESH_OPTION_KEYS);
  assert.ok(ro.EXPORT_KEYS.includes('pixel_size_um'));
  assert.deepEqual(ro.pick({ a: 1, b: 2, c: 3 }, ['c', 'a']), { c: 3, a: 1 });
});

// -- the visibility rules, against the fixture the Python evaluator wrote -------
const FIXTURE = JSON.parse(fs.readFileSync(
  path.join(here, '..', 'fixtures', 'render_visibility.json'), 'utf8'));

test('fieldState equals the Python evaluator for all 224 states', () => {
  assert.equal(FIXTURE.length, 224);
  for (const c of FIXTURE) {
    const got = ro.fieldState(c.options, c.ctx);
    const where = JSON.stringify(c.options) + JSON.stringify(c.ctx);
    assert.deepEqual(RENDER_FIELDS.map((f) => f.key).filter((k) => got[k].hidden), c.hidden, where);
    const inert = Object.fromEntries(
      Object.entries(got).filter(([, s]) => s.inert).map(([k, s]) => [k, s.why]));
    assert.deepEqual(inert, c.inert, where);
    const disabled = Object.fromEntries(
      Object.entries(got).filter(([, s]) => s.disabled).map(([k, s]) => [k, s.title]));
    assert.deepEqual(disabled, c.disabled, where);
  }
});

test('fieldState: the measured rules, by hand', () => {
  const s = (options, ctx) => ro.fieldState(options, ctx);
  assert.equal(s({ method: 'cylinder' }).pixel_size_um.hidden, true);
  assert.equal(s({ method: 'closedcurve' }).pixel_size_um.hidden, false);
  assert.equal(s({ method: 'hybrid' }).pixel_size_um.hidden, false);
  // Passes: the tube method skips the smoother only under the Original pathway
  // (c1178 moves up to 5.2 um between 0 and 2 passes under Adaptive + Taubin, and
  // 1.2 to 1.6 um under Voxel remesh orig and Wrap); Ultraliser smooths itself.
  const passes = (options) => s(options).smooth_iterations;
  assert.equal(passes({ method: 'curvemesh', pathway: 'original' }).inert, true);
  assert.match(passes({ method: 'curvemesh', pathway: 'original' }).why, /Original pathway/);
  for (const pathway of ['adaptive_taubin', 'voxel', 'wrap', 'voxel2', 'convolution']) {
    assert.equal(passes({ method: 'curvemesh', pathway }).inert, false, `tube + ${pathway}`);
  }
  assert.equal(passes({ method: 'cylinder', pathway: 'original' }).inert, false, 'MATLAB smoothed a cylinder');
  for (const method of ['cylinder', 'curvemesh', 'hybrid']) {
    assert.equal(passes({ method, pathway: 'ultraliser' }).inert, true, `${method} + ultraliser`);
  }
  assert.match(passes({ method: 'closedcurve' }).why, /voxel grid/);
  assert.equal(passes({ method: 'hybrid', pathway: 'voxel' }).inert, false);
  assert.equal(s({ method: 'curvemesh' }).reduce_fraction.inert, false);
  assert.equal(s({ method: 'closedcurve' }).reduce_fraction.inert, true);
  // The closed-curve method reads the voxel size and nothing of the pathway: the pathway,
  // its knobs and Order gray out whichever pathway happens to be selected.
  for (const key of ['pathway', 'post_order', 'voxel_smooth_um', 'tie_um', 'voxel2_um',
    'voxel2_faces', 'voxel2_blend_um']) {
    assert.equal(s({ method: 'closedcurve', pathway: 'voxel2' })[key].inert, true, key);
    assert.equal(s({ method: 'cylinder', pathway: 'voxel2' })[key].inert, false, key);
    assert.equal(s({ method: 'hybrid', pathway: 'voxel2' })[key].inert, false, key);
  }
  assert.equal(s({ method: 'closedcurve', pathway: 'voxel2' }).pixel_size_um.inert, false);
  assert.equal(s({ method: 'closedcurve', pathway: 'voxel2' }).meshlab.inert, false);
  // Hidden stays hidden, not grayed.
  assert.equal(s({ method: 'closedcurve', pathway: 'original' }).wrap_spacing_um.hidden, true);
  assert.equal(s({ method: 'closedcurve', pathway: 'original' }).wrap_spacing_um.inert, false);
  // The default state is Voxel remesh under Cylinder.
  const d = s({});
  assert.equal(d.voxel_smooth_um.hidden, false);
  assert.equal(d.voxel2_blend_um.hidden, false);
  assert.equal(d.conv_support.hidden, true);
  assert.equal(d.wrap_spacing_um.hidden, true);
  assert.equal(d.meshlab_edge_um.hidden, true);
  assert.equal(s({ meshlab: 'qem' }).meshlab_fraction.hidden, false);
  assert.equal(s({ meshlab: 'poisson' }).meshlab_depth.hidden, false);
  // MeshLab is disabled, with the reason, only where the server lacks it.
  assert.equal(s({}, {}).meshlab.disabled, false);
  assert.equal(s({}, { meshlabOff: true }).meshlab.disabled, true);
  assert.match(s({}, { meshlabOff: true }).meshlab.title, /pymeshlab/);
  assert.equal(s({}, { meshlabOff: true }).pathway.disabled, false);
});

test('a hidden field is never inert, and its help is its own', () => {
  const field = RENDER_FIELDS.find((f) => f.key === 'smooth_iterations');
  const state = ro.fieldState({ method: 'curvemesh', pathway: 'original' }).smooth_iterations;
  assert.equal(state.inert, true);
  assert.equal(ro.helpFor(field, state), `${state.why}\n\n${field.help}`);
  assert.equal(ro.helpFor(field, ro.fieldState({}).smooth_iterations), field.help);
  for (const c of FIXTURE) {
    for (const k of c.hidden) assert.ok(!(k in c.inert), k);
  }
});

// -- the signature the build reads --------------------------------------------------
test('effectiveSignature: an option the technique ignores is not a difference', () => {
  const eff = ro.effectiveSignature;
  // Closed curve reads the voxel size alone (c1178: the mesh is identical bit for bit).
  const a = { method: 'closedcurve', pathway: 'original', pixel_size_um: 0.25, smooth_iterations: 1 };
  const b = {
    ...a, pathway: 'voxel2', smooth_iterations: 4, post_order: 'decimate_first',
    voxel2_um: 0.1, tie_um: 5, reduce_fraction: 0.5,
  };
  assert.equal(eff(a), eff(b));
  assert.notEqual(ro.renderSignature(a), ro.renderSignature(b), 'the plain signature still tells them apart');
  assert.notEqual(eff(a), eff({ ...a, pixel_size_um: 0.1 }), 'what it reads is a difference');
  assert.notEqual(eff(a), eff({ ...a, method: 'hybrid' }));
  // A tube is smoothed under Taubin pathways but not under Original.
  const tube = { method: 'curvemesh', pathway: 'original' };
  assert.equal(eff({ ...tube, smooth_iterations: 0 }), eff({ ...tube, smooth_iterations: 3 }));
  const taubin = { method: 'curvemesh', pathway: 'adaptive_taubin' };
  assert.notEqual(eff({ ...taubin, smooth_iterations: 0 }), eff({ ...taubin, smooth_iterations: 3 }));
  // Ultraliser smooths itself; a hidden pathway knob is not read.
  const ultra = { method: 'cylinder', pathway: 'ultraliser' };
  assert.equal(eff({ ...ultra, smooth_iterations: 0 }), eff({ ...ultra, smooth_iterations: 3 }));
  assert.equal(eff({ pathway: 'original', conv_support: 3 }), eff({ pathway: 'original', conv_support: 2 }));
  assert.notEqual(eff({ pathway: 'convolution', conv_support: 3 }), eff({ pathway: 'convolution', conv_support: 2 }));
  // The view options are never ignored.
  assert.notEqual(eff({ ...a, clip_ipl: 0.5 }), eff(a));
  assert.notEqual(eff({ ...a, color_by_strata: true }), eff(a));
});

test('knobSummary names the parameters of the technique in use, and only those', () => {
  const { knobSummary } = ro;
  assert.deepEqual(knobSummary({ pathway: 'voxel' }), ['Envelope σ 0.5', 'Tie gaps ≤ 20']);
  assert.deepEqual(knobSummary({ pathway: 'voxel2' }), [
    'Envelope σ 0.5', 'Tie gaps ≤ 20', 'Fine voxel auto', 'Face cap 400000', 'Junction fillet 0']);
  assert.deepEqual(knobSummary({ pathway: 'voxel2', exact_tube_deg: 35 }).at(-1), 'Exact tubes ° 35');
  assert.deepEqual(knobSummary({ pathway: 'wrap', wrap_spacing_um: 0.6 }), [
    'Envelope σ 0.5', 'Tie gaps ≤ 20', 'Ring detail 0.35', 'Cube size 0.6', 'Blend 2', 'Reject σ 2', 'Refits 2']);
  assert.deepEqual(knobSummary({ pathway: 'ultraliser', voxel2_um: 0.2 }).slice(-4),
    ['Soma proxy discs', 'Smoothing 15', 'Smooth λ 0.5', 'Inflate 0.52']);
  assert.deepEqual(knobSummary({ pathway: 'original' }), []);
  // Disc avg reaches the tubes (2.7.0), named there only once it is on.
  assert.deepEqual(knobSummary({ pathway: 'voxel', curve_avg_discs: 0.5 }),
    ['Envelope σ 0.5', 'Tie gaps ≤ 20', 'Smooth discs ×r 0.5']);
  // The closed-curve methods report their voxel size, their averaging, the curved links,
  // the joined bodies and the bridges (Hybrid's outlines only: its discs are the pathway's);
  // the closed-curve method reads no pathway.
  const reaches = ['Curve links µm 0', 'Join bodies µm 5', 'Bridge skips µm 1', 'Follow links µm 5'];
  assert.deepEqual(knobSummary({ method: 'closedcurve', pathway: 'voxel2', pixel_size_um: 0.25 }),
    ['Voxel 0.25', 'Smooth discs ×r 0', 'Smooth outlines µm 0', ...reaches]);
  assert.deepEqual(knobSummary({ method: 'closedcurve', pixel_size_um: 0.1, curve_avg_discs: 0.5, curve_avg_paths: 0.25 }),
    ['Voxel 0.1', 'Smooth discs ×r 0.5', 'Smooth outlines µm 0.25', ...reaches]);
  assert.deepEqual(knobSummary({ method: 'closedcurve', pixel_size_um: 0.1, curve_links_um: 1, curve_tie_um: 2 }),
    ['Voxel 0.1', 'Smooth discs ×r 0', 'Smooth outlines µm 0', 'Curve links µm 1', 'Join bodies µm 2',
      'Bridge skips µm 1', 'Follow links µm 5']);
  assert.deepEqual(knobSummary({ method: 'hybrid', pathway: 'voxel', pixel_size_um: 0.1 }),
    ['Envelope σ 0.5', 'Tie gaps ≤ 20', 'Voxel 0.1', 'Smooth outlines µm 0', 'Bridge skips µm 1',
      'Follow links µm 5']);
  assert.ok(knobSummary({ pathway: 'original', meshlab: 'qem' }).includes('MeshLab qem'));
});

test('CURVE_METHODS is the schema rule that shows the voxel size', () => {
  assert.deepEqual([...ro.CURVE_METHODS].sort(), ['closedcurve', 'hybrid']);
  assert.deepEqual([...ro.CURVE_METHODS].sort(),
    [...RENDER_FIELDS.find((f) => f.key === 'pixel_size_um').show.method].sort());
});

test('effectiveSignature equals renderSignature when nothing is ignored', () => {
  assert.equal(ro.effectiveSignature(DEFAULT_RENDER), ro.renderSignature(DEFAULT_RENDER));
  const tuned = { ...DEFAULT_RENDER, voxel_smooth_um: 0.8, voxel2_blend_um: 0.3, smooth_iterations: 2 };
  assert.equal(ro.effectiveSignature(tuned), ro.renderSignature(tuned));
});

test('visibleKeys lists the controls a state shows, in schema order', () => {
  const keys = ro.visibleKeys({});
  assert.deepEqual(keys.slice(0, 5),
    ['method', 'pathway', 'smooth_iterations', 'post_order', 'voxel_smooth_um']);
  assert.ok(!keys.includes('pixel_size_um') && !keys.includes('clip_z') && !keys.includes('keep_above'));
  const wrap = ro.visibleKeys({ pathway: 'wrap' });
  for (const k of ['ring_arc_um', 'wrap_spacing_um', 'wrap_blend', 'wrap_reject_sigma', 'wrap_passes']) {
    assert.ok(wrap.includes(k), k);
  }
});

// -- the form builder's pure decisions (renderform.js) ----------------------------
const form = await load('renderform.js');

test('describeControls: what each of the 34 fields is made of', () => {
  const ui = RENDER_FIELDS.filter((f) => f.ui);
  assert.equal(ui.length, 34);
  const spec = (key) => form.describeControls(ui.find((f) => f.key === key));
  const tags = ui.map((f) => form.describeControls(f).map((s) => s.tag).join('+'));
  assert.equal(tags.filter((t) => t === 'select').length, 5);          // the enums
  assert.equal(tags.filter((t) => t === 'input+output').length, 1);    // Decimate
  assert.equal(tags.filter((t) => t === 'input+select').length, 2);    // Clip IPL, the Voxel size
  assert.equal(tags.filter((t) => t === 'input').length, 26);
  for (const f of ui) {
    assert.equal(form.describeControls(f)[0].role, 'control', f.key);
  }
  // Passes has never had a step; Decimate is a slider with a readout; a number box's
  // bounds are the clamp's.
  assert.deepEqual(spec('smooth_iterations'),
    [{ role: 'control', tag: 'input', type: 'number', min: 0, max: 10, value: 1 }]);
  assert.deepEqual(spec('reduce_fraction'), [
    { role: 'control', tag: 'input', type: 'range', min: 0.1, max: 1, step: 0.05, value: 1 },
    { role: 'out', tag: 'output', text: '100%' }]);
  assert.deepEqual(spec('voxel2_faces')[0],
    { role: 'control', tag: 'input', type: 'number', min: 0, max: 5000000, step: 10000, value: 400000 });
  // Clip IPL: blank by default, "off" as its hint, with the side select beside it.
  const clip = spec('clip_ipl');
  assert.deepEqual(clip[0], { role: 'control', tag: 'input', type: 'number', step: 0.05, placeholder: 'off' });
  assert.deepEqual(clip[1].options.map((o) => [o.value, o.selected]), [['above', true], ['below', false]]);
  // The selects: the app's default is the one chosen, in the panel's order.
  assert.deepEqual(spec('pathway')[0].options.map((o) => o.value),
    ['original', 'adaptive_taubin', 'voxel2', 'voxel', 'convolution', 'ultraliser', 'wrap']);
  assert.deepEqual(spec('pathway')[0].options.filter((o) => o.selected).map((o) => o.value), ['voxel2']);
  // The Voxel size: a box a size is typed into (text, so "0.025 µm" reads), and its list of
  // named sizes, the first row of which stands for a typed size and is never shown.
  const [box, menu] = spec('pixel_size_um');
  assert.deepEqual(box, { role: 'control', tag: 'input', type: 'text', inputmode: 'decimal',
    value: '0.05', label: 'Voxel size, µm' });
  assert.equal(menu.role, 'menu');
  assert.equal(menu.label, 'Voxel sizes');
  assert.deepEqual(menu.options.map((o) => [o.value, o.selected, !!o.hidden]),
    [['', false, true], ['1', false, false], ['0.25', false, false], ['0.1', false, false],
      ['0.05', true, false], ['0.025', false, false]]);
  assert.deepEqual(spec('ultra_soma')[0].options.map((o) => [o.value, o.label, o.selected]),
    [['discs', 'discs (ours)', true], ['sphere', 'sphere (Ultraliser)', false]]);
});

test('fieldPlan: hidden, grayed with the reason first, disabled with the title', () => {
  const field = (key) => RENDER_FIELDS.find((f) => f.key === key);
  const plan = (key, options, ctx) => form.fieldPlan(field(key), ro.fieldState(options, ctx)[key]);
  const tube = plan('smooth_iterations', { method: 'curvemesh', pathway: 'original' });
  assert.equal(tube.inert, true);
  assert.ok(tube.help.startsWith('The tube method under the Original pathway is not smoothed'));
  assert.ok(tube.help.includes('and ignores this.\n\n'));
  assert.ok(tube.help.endsWith(field('smooth_iterations').help));
  assert.equal(plan('smooth_iterations', {}).help, field('smooth_iterations').help);
  assert.equal(plan('pixel_size_um', { method: 'cylinder' }).hidden, true);
  assert.equal(plan('pixel_size_um', { method: 'hybrid' }).hidden, false);
  const off = plan('meshlab', {}, { meshlabOff: true });
  assert.equal(off.disabled, true);
  assert.match(off.title, /pymeshlab/);
  assert.deepEqual(plan('meshlab', {}, {}), {
    hidden: false, inert: false, help: field('meshlab').help, disabled: false, title: '' });
});

test('readValue and writeValue keep the panel\'s coercions', () => {
  const field = (key) => RENDER_FIELDS.find((f) => f.key === key);
  assert.equal(form.readValue(field('pathway'), 'wrap'), 'wrap');
  assert.equal(form.readValue(field('tie_um'), '7.5'), 7.5);
  assert.equal(form.readValue(field('tie_um'), ''), 0);           // then clamped to the minimum
  assert.ok(Number.isNaN(form.readValue(field('tie_um'), 'abc'))); // then reset to the default
  assert.equal(form.readValue(field('clip_ipl'), ''), undefined);
  assert.equal(form.readValue(field('clip_ipl'), '0.4'), 0.4);
  assert.equal(form.readValue(field('pixel_size_um'), '0.063'), 0.063);
  assert.equal(form.writeValue(field('ultra_soma'), 'sphere'), 'sphere');
  assert.equal(form.writeValue(field('ultra_soma'), 'bogus'), 'discs');
  assert.equal(form.writeValue(field('pathway'), 'bogus'), 'bogus');   // no other select coerces
  assert.equal(form.percentText(0.355), '36%');
  assert.equal(form.percentText(1), '100%');
});

test('a Voxel size typed into its box: what reads, what reverts, what shows', () => {
  const voxel = RENDER_FIELDS.find((f) => f.key === 'pixel_size_um');
  assert.equal(voxel.min, 0.005);
  assert.equal(voxel.max, 1);
  // Reads: plain decimals, a leading dot, a unit, a decimal comma, an exponent; rounded to 0.1 nm.
  assert.equal(form.parseChoice(voxel, '0.025'), 0.025);
  assert.equal(form.parseChoice(voxel, ' .0125 '), 0.0125);
  assert.equal(form.parseChoice(voxel, '0.03 µm'), 0.03);
  assert.equal(form.parseChoice(voxel, '0.03um'), 0.03);
  assert.equal(form.parseChoice(voxel, '0,04'), 0.04);
  assert.equal(form.parseChoice(voxel, '5e-3'), 0.005);
  assert.equal(form.parseChoice(voxel, '0.123456'), 0.1235);
  assert.equal(form.parseChoice(voxel, '1'), 1);
  // Does not: out of range, blank, negative, text, two numbers, half-typed.
  for (const text of ['0.004', '1.5', '', '   ', '-0.05', 'abc', '0.05 0.1', '0.', '0.0', '.', 'NaN',
    'Infinity', '0x10']) {
    assert.equal(form.parseChoice(voxel, text), null, JSON.stringify(text));
  }
  // Committing: a size takes, anything else reverts to the size set, which the box shows again.
  assert.deepEqual(form.commitChoice(voxel, '0.032', 0.05), { value: 0.032, text: '0.032', reverted: false });
  assert.deepEqual(form.commitChoice(voxel, '0.0320', 0.05), { value: 0.032, text: '0.032', reverted: false });
  assert.deepEqual(form.commitChoice(voxel, 'oops', 0.05), { value: 0.05, text: '0.05', reverted: true });
  assert.deepEqual(form.commitChoice(voxel, '0.001', 0.25), { value: 0.25, text: '0.25', reverted: true });
  // The list names a size or not; a saved size it does not name still shows in the box.
  assert.equal(form.choiceFor(voxel, 0.25), '0.25');
  assert.equal(form.choiceFor(voxel, 0.063), '');
  assert.equal(form.numberText(0.063), '0.063');
  assert.equal(form.numberText(0.0125), '0.0125');
  assert.equal(form.numberText(1), '1');
  assert.equal(form.numberText(0.1 + 0.2), '0.3');
  // ArrowUp / ArrowDown step through the named sizes, from a typed one too, and stop at the ends.
  assert.equal(form.stepChoice(voxel, 0.05, 1), 0.1);
  assert.equal(form.stepChoice(voxel, 0.05, -1), 0.025);
  assert.equal(form.stepChoice(voxel, 0.063, 1), 0.1);
  assert.equal(form.stepChoice(voxel, 0.063, -1), 0.05);
  assert.equal(form.stepChoice(voxel, 1, 1), 1);
  assert.equal(form.stepChoice(voxel, 0.01, -1), 0.01);
});

// -- the named techniques ----------------------------------------------------------
test('there are thirteen presets: the old ten, then the three MeshLab ones', () => {
  assert.deepEqual(ro.RENDER_PRESETS.map((p) => p.id), [
    'voxel2', 'voxel', 'convolution', 'ultraliser', 'original', 'adaptive', 'wrap', 'tube',
    'closedcurve', 'hybrid', 'voxel2_iso', 'voxel2_qem', 'voxel2_poisson']);
  // What the bottom-bar picker listed before it read this list.
  const before = [
    ['voxel2', 'Voxel remesh', 'cylinder', 'voxel2'],
    ['voxel', 'Voxel remesh orig', 'cylinder', 'voxel'],
    ['convolution', 'Convolution surface', 'cylinder', 'convolution'],
    ['ultraliser', 'Ultraliser (voxelized proxies)', 'cylinder', 'ultraliser'],
    ['original', 'Original (MATLAB)', 'cylinder', 'original'],
    ['adaptive', 'Adaptive + Taubin', 'cylinder', 'adaptive_taubin'],
    ['wrap', 'Wrap (cube fit)', 'cylinder', 'wrap'],
    ['tube', 'Tube (curveToMesh)', 'curvemesh', 'original'],
    ['closedcurve', 'Closed curve volume', 'closedcurve', 'original'],
    ['hybrid', 'Hybrid (discs + curves)', 'hybrid', 'voxel'],
  ];
  assert.deepEqual(
    ro.RENDER_PRESETS.slice(0, 10).map((p) => [p.id, p.label, p.method, p.pathway]), before);
  for (const p of ro.RENDER_PRESETS.slice(0, 10)) assert.deepEqual(p.set, {}, p.id);
  assert.deepEqual(ro.RENDER_PRESETS.filter((p) => p.requires).map((p) => p.set.meshlab),
    ['isotropic', 'qem', 'poisson']);
});

test('matchPreset: method, pathway and MeshLab mode, then method and pathway, then Custom', () => {
  const id = (o) => ro.matchPreset({ ...DEFAULT_RENDER, ...o })?.id ?? 'custom';
  assert.equal(id({}), 'voxel2');
  assert.equal(id({ pathway: 'original' }), 'original');
  // The panel's MeshLab mode alone used to leave the picker saying "Voxel remesh".
  assert.equal(id({ meshlab: 'isotropic' }), 'voxel2_iso');
  assert.equal(id({ meshlab: 'qem' }), 'voxel2_qem');
  assert.equal(id({ meshlab: 'poisson' }), 'voxel2_poisson');
  // A pair with no MeshLab preset keeps reading as its plain technique, as it always did.
  assert.equal(id({ pathway: 'original', meshlab: 'isotropic' }), 'original');
  assert.equal(id({ pathway: 'wrap', meshlab: 'qem' }), 'wrap');
  assert.equal(id({ method: 'curvemesh', pathway: 'original' }), 'tube');
  assert.equal(id({ method: 'closedcurve', pathway: 'original' }), 'closedcurve');
  assert.equal(id({ method: 'hybrid', pathway: 'voxel' }), 'hybrid');
  assert.equal(id({ method: 'hybrid', pathway: 'voxel2' }), 'custom');
  assert.equal(id({ pathway: 'bogus' }), 'custom');
  // Options without a MeshLab key are plain.
  assert.equal(ro.matchPreset({ method: 'cylinder', pathway: 'voxel2' }).id, 'voxel2');
});

test('presetOptions adds only the preset\'s own settings to the technique\'s defaults', () => {
  for (const p of ro.RENDER_PRESETS) {
    const options = ro.presetOptions(p);
    assert.equal(options.method, p.method, p.id);
    assert.equal(options.pathway, p.pathway, p.id);
    assert.equal(ro.matchPreset(options).id, p.id, p.id);        // each reads back as itself
    assert.deepEqual(options, { ...ro.techniqueDefaults(p.method, p.pathway), ...p.set }, p.id);
  }
  // A plain preset is the main picker's old pick, key for key.
  assert.deepEqual(ro.presetOptions(ro.RENDER_PRESETS[0]),
    { ...DEFAULT_RENDER, method: 'cylinder', pathway: 'voxel2' });
  withStorage(() => {
    // MeshLab saved as the default of Voxel remesh: the plain preset keeps it,
    // a MeshLab preset overrides it, and both share the one saved record.
    ro.storeTechDefaults({ 'cylinder|voxel2': { meshlab: 'qem', meshlab_fraction: 0.5 } });
    const plain = ro.presetOptions(ro.RENDER_PRESETS.find((p) => p.id === 'voxel2'));
    assert.equal(plain.meshlab, 'qem');
    assert.equal(plain.meshlab_fraction, 0.5);
    const iso = ro.presetOptions(ro.RENDER_PRESETS.find((p) => p.id === 'voxel2_iso'));
    assert.equal(iso.meshlab, 'isotropic');
    assert.equal(iso.meshlab_fraction, 0.5);
  });
});

// -- technique defaults, the curve raster and the cost estimate ------------------
function withStorage(fn) {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
  };
  try { return fn(store); } finally { delete globalThis.localStorage; }
}

test('techniqueDefaults: recommended values, then the saved tuning of that technique only', () => {
  // Nothing in storage (and no localStorage at all, as under node): the defaults.
  assert.deepEqual(ro.techniqueDefaults('cylinder', 'wrap'),
    { ...DEFAULT_RENDER, method: 'cylinder', pathway: 'wrap' });
  withStorage((store) => {
    ro.storeTechDefaults({ 'cylinder|wrap': { wrap_spacing_um: 0.9, method: 'tube', stale: 1 } });
    assert.equal(store.has(ro.TECH_DEFAULTS_KEY), true);
    const wrap = ro.techniqueDefaults('cylinder', 'wrap');
    assert.equal(wrap.wrap_spacing_um, 0.9);
    assert.equal(wrap.method, 'cylinder');      // only tuning knobs are read back
    assert.ok(!('stale' in wrap));
    assert.equal(ro.techniqueDefaults('cylinder', 'voxel2').wrap_spacing_um, 0.4);
    store.set(ro.TECH_DEFAULTS_KEY, '{not json');
    assert.deepEqual(ro.loadTechDefaults(), {});
  });
  assert.equal(ro.techKey('cylinder', 'voxel2'), 'cylinder|voxel2');
});

test('curvePixelSeed: only an untouched default, only a cell with outlines', () => {
  const cell = (extra) => ({ summary: { n_curve_annotations: 40, type_id: 1, ...extra } });
  const hybrid = { ...DEFAULT_RENDER, method: 'hybrid', pathway: 'voxel' };
  assert.equal(ro.curvePixelSeed(cell(), hybrid), 0.25);
  assert.equal(ro.curvePixelSeed(cell({ type_id: 3 }), hybrid), 1);
  assert.equal(ro.curvePixelSeed(cell(), { ...hybrid, method: 'closedcurve' }), 0.25);
  assert.equal(ro.curvePixelSeed(cell(), { ...hybrid, method: 'cylinder' }), null);
  assert.equal(ro.curvePixelSeed(cell(), { ...hybrid, pixel_size_um: 0.1 }), null);
  assert.equal(ro.curvePixelSeed(cell({ n_curve_annotations: 0 }), hybrid), null);
  assert.equal(ro.curvePixelSeed(null, hybrid), null);
  withStorage(() => {
    ro.storeTechDefaults({ 'hybrid|voxel': { pixel_size_um: 0.05 } });
    assert.equal(ro.curvePixelSeed(cell(), hybrid), null);   // the user's own default is theirs
  });
  assert.equal(ro.curvePixelFor({ type_id: 3 }), 1);
  assert.equal(ro.curvePixelFor(undefined), 0.25);
});

test('estimateBuildSeconds is the old estimate', () => {
  const record = { summary: { n_nodes: 2400 } };
  const est = (o) => ro.estimateBuildSeconds(record, { ...DEFAULT_RENDER, ...o });
  assert.ok(Math.abs(est({ pathway: 'original' }) - 0.00155 * 2400 * 5) < 1e-9);
  assert.ok(Math.abs(est({ pathway: 'voxel2' }) - 0.00155 * 2400 * 5 * 4) < 1e-9);
  assert.ok(Math.abs(est({ method: 'curvemesh' }) - 0.00006 * 2400 * 5 * 4) < 1e-9);
  // (Join bodies off: the rates themselves; the viewer's default 5 um adds a third.)
  const curve = (pixel) => est({ method: 'closedcurve', pixel_size_um: pixel, curve_tie_um: 0 });
  assert.ok(Math.abs(curve(0.1) - 0.0175 * 120 * 5) < 1e-9);
  assert.ok(Math.abs(curve(0.05) - 0.0675 * 120 * 5) < 1e-9);
  assert.ok(Math.abs(curve(0.025) - 0.26 * 120 * 5) < 1e-9);
  assert.ok(Math.abs(est({ method: 'closedcurve', pixel_size_um: 0.1 }) - 0.0175 * 120 * 5 * 1.3) < 1e-9);
  assert.equal(ro.estimateBuildSeconds({ summary: {} }, DEFAULT_RENDER), 0);
});

test('estimateBuildSeconds: a closed-curve raster that was not timed costs by its area, not the 0.05 rate', () => {
  const record = { summary: { n_nodes: 2400 } };
  const est = (pixel) => ro.estimateBuildSeconds(
    record, { ...DEFAULT_RENDER, method: 'closedcurve', pixel_size_um: pixel, curve_tie_um: 0 });
  // The rasters a traced cell is seeded to: the 0.05 rate times (0.05 / pixel)^2.
  assert.ok(Math.abs(est(0.25) - 0.0675 * (0.05 / 0.25) ** 2 * 120 * 5) < 1e-9);
  assert.ok(Math.abs(est(1) - 0.0675 * (0.05 / 1) ** 2 * 120 * 5) < 1e-9);
  // 0.25 um was priced at the 0.05 rate, 25 times too high.
  assert.ok(Math.abs(est(0.05) / est(0.25) - 25) < 1e-9);
  // Coarser is cheaper at every raster, timed or not, and the timed ones are untouched.
  const rasters = [1, 0.25, 0.15, 0.1, 0.07, 0.05, 0.04, 0.025, 0.02];
  for (let i = 1; i < rasters.length; i += 1) assert.ok(est(rasters[i - 1]) < est(rasters[i]), String(rasters[i]));
  // A pixel the 3D render's box clamps (0.01 to 1) still gives a finite, positive estimate.
  for (const pixel of [0.01, 0.37, 1]) assert.ok(Number.isFinite(est(pixel)) && est(pixel) > 0);
});

test('a render cut or colored by IPL depth carries the page\'s depth method, and no other does', () => {
  const plain = ro.normalizeRender({});
  assert.equal(ro.withIplMethod(plain, 'normal'), plain, 'the same object: the same request and cache entry');
  assert.equal(ro.drawsIplDepth(plain), false);
  const colored = ro.normalizeRender({ color_by_strata: true });
  assert.equal(ro.withIplMethod(colored, 'normal').ipl_method, 'normal');
  assert.ok(!('ipl_method' in colored), 'the cell\'s own options are left alone');
  for (const clip of [0, 0.5]) {
    assert.equal(ro.withIplMethod(ro.normalizeRender({ clip_ipl: clip }), 'column').ipl_method, 'column');
  }
  // The mesh cache tells the two methods apart.
  assert.notEqual(ro.effectiveSignature(ro.withIplMethod(colored, 'normal')),
    ro.effectiveSignature(ro.withIplMethod(colored, 'column')));
  // Not a cell's render option: it is in no record's defaults.
  assert.ok(!('ipl_method' in ro.DEFAULT_RENDER));
  assert.equal(ro.FIELD_BY_KEY.ipl_method.inRender, false);
});

test('a flattened view asks for every surface flattened, by the page\'s method, and an unflattened one never does', () => {
  const plain = ro.normalizeRender({});
  const flat = ro.withIplMethod(plain, 'normal', true);
  assert.equal(flat.ipl_flatten, true);
  assert.equal(flat.ipl_method, 'normal', 'the depth the flattening reads is the page\'s');
  assert.ok(!('ipl_flatten' in plain), 'the cell\'s own options are left alone');
  assert.equal(ro.withIplMethod(plain, 'normal', false), plain, 'unflattened: the request it always was');
  // The mesh cache tells flattened from not, and the two methods apart.
  assert.notEqual(ro.effectiveSignature(flat), ro.effectiveSignature(plain));
  assert.notEqual(ro.effectiveSignature(flat), ro.effectiveSignature(ro.withIplMethod(plain, 'column', true)));
  // A colored, clipped render keeps its own view options when flattened.
  const both = ro.withIplMethod(ro.normalizeRender({ clip_ipl: 0.4, color_by_strata: true }), 'column', true);
  assert.equal(both.clip_ipl, 0.4);
  assert.equal(both.ipl_method, 'column');
  // The context layers' query.
  assert.deepEqual(ro.flatQuery('normal', true), { ipl_flatten: true, ipl_method: 'normal' });
  assert.deepEqual(ro.flatQuery('normal', false), {});
  // Off by default, and not a cell's render option.
  assert.ok(!('ipl_flatten' in ro.DEFAULT_RENDER));
  assert.equal(ro.FIELD_BY_KEY.ipl_flatten.inRender, false);
  assert.equal(ro.FIELD_BY_KEY.ipl_flatten.default, false);
  assert.equal(ro.FIELD_BY_KEY.ipl_flatten.scope, 'view');
});

// -- run -------------------------------------------------------------------------
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
