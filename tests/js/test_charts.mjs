// The dendrite-diameter bar chart's layout (static/js/charts.js barLayout):
// where each bar goes, and the x axis.  No JS runner exists in this repo; run
// it with
//
//     node tests/js/test_charts.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free, no DOM (the layout is pure; the SVG drawing is checked in a browser).
// tests/unit/test_js.py runs it with the other suites.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const { barLayout, niceTicks } = await import(pathToFileURL(path.join(jsDir, 'charts.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** A dendrite-diameter answer: bin centers 2.5, 7.5, ... and a mean, null where the bin is empty. */
const rowsOf = (means, width = 5) => means.map((mean, i) => ({
  bin: i, distance: width * (i + 0.5), n: mean === null ? 0 : 3, mean,
}));
const layout = (rows, extra = {}) => barLayout(rows, {
  xKey: 'distance', yKey: 'mean', binWidth: 5, width: 420, height: 220, ...extra,
});

test('bars sit at their distance: an empty bin leaves a gap, not a shift', () => {
  // bins 0-5, 5-10, (10-15 empty), 15-20: the API sends no row for the empty bin
  const rows = rowsOf([0.4, 0.5, null, 0.3]).filter((r) => r.mean !== null);
  const { bars, plot } = layout(rows);
  assert.equal(bars.length, 3);
  const slot = plot.width / 4;                       // four bins of 5 um across 0-20
  // each bar starts at its bin's edge (+1 px of gap), the last in bin 3 and not bin 2
  assert.ok(Math.abs(bars[0].x - (plot.left + 0 * slot + 1)) < 1e-9);
  assert.ok(Math.abs(bars[1].x - (plot.left + 1 * slot + 1)) < 1e-9);
  assert.ok(Math.abs(bars[2].x - (plot.left + 3 * slot + 1)) < 1e-9, 'the bar after the gap moved left');
  assert.ok(Math.abs(bars[2].x - bars[1].x - 2 * slot) < 1e-9);
});

test('each bar is as wide as its bin', () => {
  const { bars, plot } = layout(rowsOf([0.4, 0.5, 0.3]));
  for (const bar of bars) assert.ok(Math.abs(bar.width - (plot.width / 3 - 2)) < 1e-9);
});

test('heights follow the values, the tallest filling the plot', () => {
  const { bars, plot, y1 } = layout(rowsOf([0.2, 0.4, 0.8]));
  assert.equal(y1, 0.8);
  assert.ok(Math.abs(bars[2].height - plot.height) < 1e-9);
  assert.ok(Math.abs(bars[0].height - plot.height / 4) < 1e-9);
  assert.ok(Math.abs(bars[2].y - plot.top) < 1e-9);
});

test('the axis starts at 0 even when the first bins are empty', () => {
  const rows = rowsOf([null, null, 0.5, 0.6]).filter((r) => r.mean !== null);
  const { bars, plot, ticks } = layout(rows);
  assert.equal(ticks[0].value, 0);
  assert.ok(Math.abs(ticks[0].x - plot.left) < 1e-9);
  // the first drawn bar is the third bin, a half of the 20 um across
  assert.ok(Math.abs(bars[0].x - (plot.left + plot.width / 2 + 1)) < 1e-9);
});

test('the x axis has ticks in microns', () => {
  const { ticks, plot } = layout(rowsOf([0.4, 0.5, 0.3, 0.2]));
  assert.deepEqual(ticks.map((t) => t.value), [0, 5, 10, 15, 20]);
  assert.ok(Math.abs(ticks.at(-1).x - (plot.left + plot.width)) < 1e-9);
});

test('rows with no mean are left out, and a bin width is found when not given', () => {
  const rows = rowsOf([0.4, null, 0.3, 0.2]);          // the null row is still in the list
  const { bars } = layout(rows, { binWidth: undefined });
  assert.equal(bars.length, 3);
  const { plot } = layout(rows, { binWidth: undefined });
  // closest two rows are 5 um apart, so the width is the same as when it was given
  assert.ok(Math.abs(bars[0].width - (plot.width / 4 - 2)) < 1e-9);
});

test('without xKey the bars go by position, as before', () => {
  const { bars } = barLayout([{ v: 1 }, { v: 2 }, { v: 3 }], { yKey: 'v' });
  assert.equal(bars.length, 3);
  assert.ok(bars[0].x < bars[1].x && bars[1].x < bars[2].x);
});

test('nothing to draw is an empty layout, not an error', () => {
  assert.deepEqual(layout([]).bars, []);
  assert.deepEqual(layout([{ distance: 2.5, mean: null }]).bars, []);
  assert.deepEqual(layout([{ distance: 2.5, mean: NaN }]).bars, []);
});

test('niceTicks', () => {
  assert.deepEqual(niceTicks(0, 20), [0, 5, 10, 15, 20]);
  assert.deepEqual(niceTicks(0, 100), [0, 20, 40, 60, 80, 100]);
  assert.deepEqual(niceTicks(0, 1), [0, 0.2, 0.4, 0.6, 0.8, 1]);
  assert.deepEqual(niceTicks(3, 3), [3]);
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
