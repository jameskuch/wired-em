// The load strip's bookkeeping (static/js/loadprogress.js), against a stand-in for
// the page.  There is no JS runner in this repo: run it with
//
//     node tests/js/test_loadprogress.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);

// Just enough page for LoadProgress to draw into.
const element = () => ({
  hidden: false,
  textContent: '',
  value: 0,
  max: 1,
  className: '',
  children: [],
  classList: { remove() {}, add() {}, toggle() {} },
  setAttribute() {},
  addEventListener() {},
  append(...nodes) { this.children.push(...nodes); },
  replaceChildren(...nodes) { this.children = nodes; },
});
globalThis.document = { createElement: () => element() };
const { LoadProgress } = await load('loadprogress.js');

function strip() {
  const parts = {
    '.load-title': element(), progress: element(), '.load-cells': element(), '.load-stop': element(),
  };
  const root = { ...element(), querySelector: (selector) => parts[selector] };
  return { strip: new LoadProgress(root), parts, root };
}

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('the same numeric ID in two volumes is two cells, and the bar completes (W1-F10)', () => {
  const { strip: bar, parts } = strip();
  const run = bar.begin(3);
  // A restored session holding c1 of two volumes, and c2 of one.
  const a = run.cell(1, 'NeitzInferiorMonkey/1');
  const b = run.cell(1, 'RC1/1');
  const c = run.cell(2, 'NeitzInferiorMonkey/2');
  assert.equal(run.cells.size, 3, 'all three are tracked');
  a.done(true);
  b.done(true);
  c.done(true);
  assert.equal(run.done, 3, 'three done of three');
  assert.equal(run.cells.size, 0);
  run.end();
  assert.equal(parts['.load-title'].textContent, 'Loaded 3 cells');
  assert.equal(parts.progress.value, 3);
  assert.equal(parts.progress.max, 3);
});

test('without a key the ID still tells cells apart, as it did', () => {
  const { strip: bar } = strip();
  const run = bar.begin(2);
  const a = run.cell(7);
  const b = run.cell(8);
  assert.equal(run.cells.size, 2);
  a.done(true);
  b.done(false);
  assert.equal(run.done, 2);
  assert.equal(run.failed, 1);
});

test('a cell finishing twice is counted once', () => {
  const { strip: bar } = strip();
  const run = bar.begin(2);
  const a = run.cell(1, 'A/1');
  run.cell(1, 'B/1');
  a.done(true);
  a.done(true);
  assert.equal(run.done, 1);
});

test('Stop drops the cells still fetching, in each volume, and keeps the one drawing', () => {
  const { strip: bar } = strip();
  let stopped = 0;
  const run = bar.begin(3, { onStop: () => { stopped += 1; } });
  run.cell(1, 'A/1');
  run.cell(1, 'B/1');
  const drawing = run.cell(2, 'A/2');
  drawing.stage('drawing');
  bar.stop();
  assert.equal(stopped, 1);
  assert.equal(run.dropped, 2, 'both c1 are dropped, whichever volume');
  assert.equal(run.cells.size, 1, 'the cell already drawing finishes');
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack || error}`);
  }
}
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
