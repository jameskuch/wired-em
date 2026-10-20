// The cell list's state (static/js/state.js): what a re-fetch of a listed cell
// leaves alone, and what a cell's cached numbers are keyed on.  No JS runner
// exists in this repo; run it with
//
//     node tests/js/test_state.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free, no DOM.  tests/unit/test_js.py runs it with the other suites.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const S = await load('state.js');
const { state } = S;

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** A load answer for one cell. */
const detail = (id, summary = {}) => ({
  summary: {
    id, source: 'NeitzInferiorMonkey', label: `cell ${id}`, data_stamp: '2026-09-23T22:22:17.537Z',
    copy_tag: 'curvefit.1', ...summary,
  },
  synapse_counts: [],
});

function reset() {
  for (const key of [...state.neurons.keys()]) S.removeNeuron(key);
  state.selection = new Set();
  state.selected = null;
}

// -- upsertNeuron and the selection -------------------------------------------------
test('a new cell joins the selection and takes the focus', () => {
  reset();
  const record = S.upsertNeuron(detail(1));
  assert.ok(S.isSelected(record.key));
  assert.equal(state.selected, record.key);
});

test('a re-fetch with select: false leaves an unticked cell unticked (Update, Refresh all, Registration)', () => {
  reset();
  const a = S.upsertNeuron(detail(1));
  const b = S.upsertNeuron(detail(2));
  S.setSelected(a.key, false);
  assert.ok(!S.isSelected(a.key));
  const again = S.upsertNeuron(detail(1, { data_stamp: 'newer' }), { select: false });
  assert.ok(!S.isSelected(a.key), 'the cell the user unticked was ticked again');
  assert.ok(S.isSelected(b.key), 'another cell lost its tick');
  assert.equal(again.summary.data_stamp, 'newer', 'the new load is what the record holds');
  assert.equal(state.neurons.size, 2);
});

test('a re-fetch with select: false keeps a ticked cell ticked', () => {
  reset();
  const a = S.upsertNeuron(detail(1));
  S.upsertNeuron(detail(1), { select: false });
  assert.ok(S.isSelected(a.key));
});

test('loading a listed cell on purpose still selects it (the default)', () => {
  reset();
  const a = S.upsertNeuron(detail(1));
  S.setSelected(a.key, false);
  S.upsertNeuron(detail(1));
  assert.ok(S.isSelected(a.key));
});

test('a re-fetch keeps the cell\'s color, visibility, opacity, render and display', () => {
  reset();
  const a = S.upsertNeuron(detail(1), { render: { smooth_iterations: 4 } });
  a.color = '#123456';
  a.visible = false;
  a.opacity = 0.3;
  a.corrections = 'neighbor';
  a.registration = false;
  const again = S.upsertNeuron(detail(1), { select: false });
  assert.equal(again.color, '#123456');
  assert.equal(again.visible, false);
  assert.equal(again.opacity, 0.3);
  assert.equal(again.render.smooth_iterations, 4);
  assert.equal(again.corrections, 'neighbor');
  assert.equal(again.registration, false);
  assert.ok(again.loadGen > a.loadGen, 'every upsert is a newer load');
});

// -- dataKey ------------------------------------------------------------------------
test('copies of one cell share a stamp but not a dataKey', () => {
  const stamp = '2026-09-23T22:22:17.537Z';
  const keys = ['off.0', 'off.1', 'curvefit.1'].map(
    (copy_tag) => S.dataKey({ summary: { data_stamp: stamp, copy_tag } }));
  assert.equal(new Set(keys).size, 3, keys.join(' / '));
});

test('one copy loaded uncorrected, then corrected, is two dataKeys (2.6.5)', () => {
  // The service down: curvefit.1 asked for, nothing applied.  Loaded again once it answers.
  const stamp = '2026-09-23T22:22:17.537Z';
  const asked = S.dataKey({ summary: { data_stamp: stamp, copy_tag: 'curvefit.1', positions: null } });
  const done = S.dataKey({
    summary: { data_stamp: stamp, copy_tag: 'curvefit.1', positions: 'section-correction:4f1c0d6a2b9e8c77' },
  });
  assert.notEqual(asked, done);
});

test('dataKey follows the stamp, and falls back to last_modified', () => {
  const k = (summary) => S.dataKey({ summary });
  assert.notEqual(k({ data_stamp: 'a', copy_tag: 'x' }), k({ data_stamp: 'b', copy_tag: 'x' }));
  assert.equal(k({ last_modified: 'a', copy_tag: 'x' }), k({ data_stamp: 'a', copy_tag: 'x' }));
});

test('dataKey is the same for the same load and tolerates a missing summary', () => {
  const summary = { data_stamp: 's', copy_tag: 'curvefit.1' };
  assert.equal(S.dataKey({ summary }), S.dataKey({ summary: { ...summary } }));
  assert.equal(typeof S.dataKey({}), 'string');
  assert.equal(typeof S.dataKey(undefined), 'string');
  // a record with no copy tag (an older server) is not confused with one that has
  assert.notEqual(S.dataKey({ summary: { data_stamp: 's' } }), S.dataKey({ summary }));
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
