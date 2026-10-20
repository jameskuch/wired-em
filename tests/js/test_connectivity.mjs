// The Connectivity tab's pure parts (static/js/connectivity.js) and the network
// view's stylesheet (static/js/network.js), without a page: the legend's tick
// labels, the matrix as a .csv, and the lists that were spread into Math.min,
// Math.max and Array.push (a RangeError from about 125,000 items; the tab takes
// up to 200,000 paths).  There is no JS runner in this repo: run it with
//
//     node tests/js/test_connectivity.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free: the modules are copied to a scratch folder with the one bare import
// (cytoscape, which an import map answers in the page) stubbed.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sbfsem-connectivity-test-'));
for (const name of ['connectivity.js', 'network.js', 'appcore.js']) {
  const text = fs.readFileSync(path.join(jsDir, name), 'utf8')
    .replace("import cytoscape from 'cytoscape';", 'const cytoscape = () => { throw new Error("stub"); };');
  fs.writeFileSync(path.join(tmp, name), text);
}
const load = (name) => import(pathToFileURL(path.join(tmp, name)).href);
const C = await load('connectivity.js');
const N = await load('network.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// A list too long to spread into a call.
const BIG = 400_000;
const big = (from = 0) => Array.from({ length: BIG }, (_, i) => from + i);

test('the premise: this node cannot spread 400,000 items into a call', () => {
  assert.throws(() => Math.max(...big()), RangeError);
  assert.throws(() => [].push(...big()), RangeError);
});

// -- the lists (W6-F09) ---------------------------------------------------------------------

test('appendAll takes every item of a list a spread would refuse', () => {
  const out = [-1];
  assert.equal(C.appendAll(out, big()), out, 'it returns the target');
  assert.equal(out.length, BIG + 1);
  assert.equal(out.at(-1), BIG - 1);
  assert.deepEqual(C.appendAll([], []), []);
});

test('mostHops reads 400,000 paths and is at least one', () => {
  // A path alternates node, edge, node ...: n nodes have (length - 1) / 2 hops.
  const paths = Array.from({ length: BIG }, (_, i) => new Array(i === 12345 ? 7 : 3).fill(0));
  assert.equal(C.mostHops(paths), 3);
  assert.equal(C.mostHops([]), 1);
  assert.equal(C.mostHops([[1]]), 1, 'a lone node is no hops, and the answer never drops below 1');
});

test('the shortest path of a connectivity set is found over 400,000 indices', () => {
  const view = Object.create(C.ConnectivityView.prototype);
  view.pathLength = big().map((i) => 2 + (i % 5));
  view.pathLength[321_000] = 1;
  const all = big();
  assert.equal(view.metric(all, 'min_length'), 1);
  assert.equal(view.metric(all.slice(0, 10), 'min_length'), 2);
  assert.equal(view.metric(all, 'paths'), BIG);
  assert.equal(view.metric([], 'min_length'), 0, 'an empty set has no length');
});

test('a matrix cell of several rows and columns collects every path of 400,000', () => {
  const view = Object.create(C.ConnectivityView.prototype);
  view.cellPaths = new Map([['1|9', big()], ['2|9', big(BIG)], ['1|8', [7]]]);
  const rows = { ids: [1, 2] };
  const cols = { ids: [9, 8] };
  const found = view.cellIndices(rows, cols);
  assert.equal(found.length, 2 * BIG + 1);
  assert.equal(found[BIG], 7, 'row by row, column by column');
  assert.equal(found[BIG + 1], BIG, 'the second row after the first');
  assert.deepEqual(view.cellIndices({ ids: [5] }, { ids: [5] }), [], 'a cell with no paths');
  assert.equal(view.cellIndices({ ids: [1] }, { ids: [9] }), view.cellPaths.get('1|9'), 'one cell is the list itself');
});

// -- the legend (W6-F27) --------------------------------------------------------------------

test('a scale up to 3 in six steps labels every boundary differently', () => {
  const scale = C.makeScale(3, { bins: 6 });
  assert.deepEqual(C.tickLabels(scale.edges), ['1', '1.3', '1.7', '2', '2.3', '2.7', '3']);
});

test('a log scale and a finer one stay different too', () => {
  for (const [max, options] of [[8, { bins: 6, log: true }], [3, { bins: 12 }], [5, { bins: 12, log: true }]]) {
    const labels = C.tickLabels(C.makeScale(max, options).edges);
    assert.equal(new Set(labels).size, labels.length, `${max} ${JSON.stringify(options)}: ${labels}`);
  }
});

test('a wide scale keeps whole numbers', () => {
  const labels = C.tickLabels(C.makeScale(200, { bins: 6 }).edges);
  assert.ok(labels.every((text) => /^\d+$/.test(text)), labels.join(' '));
  assert.equal(labels[0], '1');
  assert.equal(labels.at(-1), '200');
  assert.deepEqual(C.tickLabels([1, 2, 3, 4]), ['1', '2', '3', '4']);
});

test('the legend draws steps, boundaries and tooltips from the scale', () => {
  const steps = C.legendSteps(C.makeScale(3, { bins: 6 }));
  assert.equal(steps.n, 6);
  assert.deepEqual(steps.ticks, ['1', '1.3', '1.7', '2', '2.3', '2.7', '3']);
  assert.deepEqual(steps.titles, ['1 to 1.3', '1.3 to 1.7', '1.7 to 2', '2 to 2.3', '2.3 to 2.7', '2.7 to 3']);
  assert.equal(C.legendSteps(C.makeScale(3, { bins: 6 }), true).ticks.at(-1), '3+', 'a capped scale says the data went past it');
});

test('a scale that tops out at 1 has one step and one label, not six steps all labeled 1', () => {
  for (const options of [{ bins: 6 }, { bins: 6, log: true }, { bins: 12 }]) {
    const steps = C.legendSteps(C.makeScale(1, options));
    assert.deepEqual([steps.n, steps.ticks, steps.titles], [1, ['1'], ['1']], JSON.stringify(options));
  }
  // Max set to 1 with cells above it: the lightest step (1) and the darkest (more than 1) are both in use.
  const scale = C.makeScale(1, { bins: 6 });
  const capped = C.legendSteps(scale, true);
  assert.deepEqual([capped.n, capped.ticks, capped.titles], [2, ['1', '', '1+'], ['1', 'more than 1']]);
  assert.equal(scale.stepColor(capped.colorSteps[0]), scale.color(1));
  assert.equal(scale.stepColor(capped.colorSteps[1]), scale.color(3), 'the darkest color the cells use is in the legend');
});

test('boundaries that cannot be told apart in three decimals still return labels', () => {
  const labels = C.tickLabels([1, 1 + 1e-9, 1 + 2e-9]);
  assert.equal(labels.length, 3);
});

// -- the .csv (W6-F10) ----------------------------------------------------------------------

test('the matrix .csv names each row as the screen does: ID and label apart, no toggle glyph', () => {
  const view = Object.create(C.ConnectivityView.prototype);
  const name = (g) => view.groupName(g);
  assert.equal(name({ group: false, id: 5, label: 'GCL' }), 'c5 GCL');
  assert.equal(name({ group: false, id: 5, label: '' }), 'c5');
  assert.equal(name({ group: true, label: 'ON', ids: [1, 2, 3] }), 'ON (3)');
  const csv = C.matrixCsv({
    rows: [name({ group: false, id: 1178, label: 'GC ON' }), name({ group: true, label: 'AII, rod', ids: [4, 5] })],
    cols: [name({ group: false, id: 20, label: '' }), name({ group: false, id: 21, label: 'Bip "a"' })],
    cells: [['3', ''], ['', '12']],
  });
  assert.equal(csv, [
    ',c20,"c21 Bip ""a"""',
    'c1178 GC ON,3,0',
    '"AII, rod (2)",0,12',
    '',
  ].join('\n'));
  assert.ok(!/[▸▾]/.test(csv), 'the toggle glyph is not a name');
});

// -- the network view's colors (W6-F08) -----------------------------------------------------

const find = (sheet, selector) => sheet.find((rule) => rule.selector === selector).style;

test('the graph is drawn in the colors it is given, not white on white', () => {
  const light = { ink: '#16191d', panel: '#ffffff' };
  const sheet = N.graphStyle('both', light);
  assert.equal(find(sheet, 'node').color, light.ink);
  assert.equal(find(sheet, 'node')['text-outline-color'], light.panel, 'the halo is the canvas');
  assert.equal(find(sheet, 'node[?loaded]')['border-color'], light.ink);
  assert.equal(find(sheet, 'node:selected')['border-color'], light.ink);
  assert.equal(find(sheet, 'edge:selected')['line-color'], light.ink);
  for (const rule of sheet) {
    for (const value of Object.values(rule.style)) {
      assert.ok(!['#fff', '#ffffff', '#e8eaed', '#c8ced8'].includes(value) || value === light.panel,
        `${rule.selector} carries a dark-theme color`);
    }
  }
});

test('label modes set the label width', () => {
  assert.equal(find(N.graphStyle('both', N.themeColors()), 'node')['text-max-width'], 170);
  assert.equal(find(N.graphStyle('id', N.themeColors()), 'node')['text-max-width'], 110);
});

test('theme colors come from the page tokens, trimmed, and fall back to the dark theme without a page', () => {
  assert.deepEqual(N.themeColors(), { ink: '#e8eaed', panel: '#1e2227' });
  globalThis.getComputedStyle = () => ({
    getPropertyValue: (name) => ({ '--ink': ' #16191d ', '--panel': '' })[name] ?? '',
  });
  try {
    assert.deepEqual(N.themeColors({}), { ink: '#16191d', panel: '#1e2227' });
  } finally {
    delete globalThis.getComputedStyle;
  }
});

test('the link-type color table has one hex color per type and a fallback', () => {
  for (const [type, color] of Object.entries(N.SYNAPSE_COLORS)) {
    assert.match(color, /^#[0-9a-f]{6}$/, type);
  }
  assert.equal(N.edgeColor('ConvPre'), '#ff474c');
  assert.equal(N.edgeColor('NoSuchType'), '#7f8c99');
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
fs.rmSync(tmp, { recursive: true, force: true });
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
