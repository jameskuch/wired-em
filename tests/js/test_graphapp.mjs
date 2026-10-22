// GraphApp's rules (static/js/graphapp.js): its colors, which annotations are
// unfinished, the segment colors, the kept settings, what Find ID finds, and the
// visibility an isolation gives back.  Run it with
//
//     node tests/js/test_graphapp.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const g = await import(pathToFileURL(path.join(jsDir, 'graphapp.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const hueOf = (h) => {
  const [r, gg, b] = rgb(h).map((v) => v / 255);
  const max = Math.max(r, gg, b);
  const min = Math.min(r, gg, b);
  const d = max - min;
  if (d === 0) return null;
  let hue;
  if (max === r) hue = ((gg - b) / d) % 6;
  else if (max === gg) hue = (b - r) / d + 2;
  else hue = (r - gg) / d + 4;
  return (hue * 60 + 360) % 360;
};

test('each cell a pastel for its annotations and a near-white of the same hue for its skeleton', () => {
  for (let i = 0; i < g.GRAPH_HUES.length; i += 1) {
    const c = g.graphColors(i);
    assert.match(c.annotation, /^#[0-9a-f]{6}$/);
    assert.match(c.skeleton, /^#[0-9a-f]{6}$/);
    assert.ok(Math.abs(hueOf(c.annotation) - c.hue) < 3 || Math.abs(hueOf(c.annotation) - c.hue) > 357, c);
    assert.ok(Math.abs(hueOf(c.skeleton) - c.hue) < 4 || Math.abs(hueOf(c.skeleton) - c.hue) > 356, c);
    // Near white: every channel high; the annotations' pastel darker than the skeleton.
    assert.ok(Math.min(...rgb(c.skeleton)) >= 200, c.skeleton);
    const light = (h) => rgb(h).reduce((s, v) => s + v, 0);
    assert.ok(light(c.annotation) < light(c.skeleton), c);
  }
  assert.deepEqual(g.graphColors(g.GRAPH_HUES.length), g.graphColors(0), 'the palette repeats');
  assert.notEqual(g.graphColors(0).annotation, g.graphColors(1).annotation);
  assert.equal(g.hslHex(0, 1, 0.5), '#ff0000');
  assert.equal(g.hslHex(120, 1, 0.5), '#00ff00');
});

test('the color a lone cell takes is told apart from every selection marker', () => {
  // render3d.js: the yellow and its cyan, the blue below and its pink, the red above and its orange.
  // The first color was the blue, hue 205, the blue marker's own (2026-10-22).
  const markers = ['#ffe14d', '#38e1ff', '#8fd0ff', '#ff8ad8', '#ff5c5c', '#ff7a00'];
  const apart = (a, b) => Math.min(Math.abs(a - b), 360 - Math.abs(a - b));
  for (const part of ['annotation', 'skeleton']) {
    const hue = hueOf(g.graphColors(0)[part]);
    for (const m of markers) assert.ok(apart(hue, hueOf(m)) >= 45, `${part} ${hue} vs ${m} ${hueOf(m)}`);
  }
});

// OKLab (Ottosson 2020): a distance there is how different two colors look.
const linear = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const oklab = (h) => {
  const [r, gg, b] = rgb(h).map((v) => linear(v / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * gg + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * gg + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * gg + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
};
const okDistance = (a, b) => Math.hypot(...oklab(a).map((v, i) => v - oklab(b)[i]));

test('no cell takes a selection marker\'s color: the blue below, the yellow, the red above', () => {
  // The blue, hue 205, stayed in the palette (third) after it stopped being the first, and the
  // user still saw the blue marker's color on a cell (2026-10-22): 0.031 from it in OKLab, the
  // yellow 55 0.045 from the yellow, the red 350 0.088 from the red.  The green the first cell
  // takes is 0.148 from the nearest, the yellow.
  const markers = { yellow: '#ffe14d', blue: '#8fd0ff', red: '#ff5c5c' };
  for (let i = 0; i < g.GRAPH_HUES.length; i += 1) {
    const c = g.graphColors(i);
    for (const [name, m] of Object.entries(markers)) {
      assert.ok(okDistance(c.annotation, m) >= 0.12, `hue ${c.hue} ${c.annotation} vs the ${name} marker`);
    }
    assert.ok(!(c.hue >= 180 && c.hue <= 250), `hue ${c.hue} is a blue`);
  }
  // render3d.js `_yellowish` and `_reddish`, which turn the markers cyan, pink or orange on a cell
  // of their own color, read three.js's HSL of linear RGB: none of the palette is either.
  for (let i = 0; i < g.GRAPH_HUES.length; i += 1) {
    const [r, gg, b] = rgb(g.graphColors(i).annotation).map((v) => linear(v / 255));
    const max = Math.max(r, gg, b);
    const min = Math.min(r, gg, b);
    const s = (max - min) / (1 - Math.abs(max + min - 1));
    let h = max === r ? ((gg - b) / (max - min)) % 6 : max === gg ? (b - r) / (max - min) + 2 : (r - gg) / (max - min) + 4;
    h = ((h * 60 + 360) % 360) / 360;
    assert.ok(!(s > 0.35 && h > 0.09 && h < 0.2), `hue ${g.GRAPH_HUES[i]} reads as yellow`);
    assert.ok(!(s > 0.35 && (h < 0.05 || h > 0.93)), `hue ${g.GRAPH_HUES[i]} reads as red`);
  }
  // The four cells most often shown together are told apart from each other.
  for (let i = 0; i < 4; i += 1) {
    for (let j = 0; j < i; j += 1) {
      const d = okDistance(g.graphColors(i).annotation, g.graphColors(j).annotation);
      assert.ok(d >= 0.13, `cells ${j} and ${i}: ${d.toFixed(3)}`);
    }
  }
});

test('unfinished: one link, not terminal, not off-edge; a repeated link counts once', () => {
  const nodes = [
    { id: 1 }, { id: 2 }, { id: 3 }, { id: 4, terminal: true }, { id: 5, off_edge: true }, { id: 6 },
  ];
  // 1-2-3 with 3-4 and 2-5; 6 linked to nothing; 1-2 drawn twice and both ways.
  const edges = [{ a: 1, b: 2 }, { a: 2, b: 1 }, { a: 1, b: 2 }, { a: 2, b: 3 }, { a: 3, b: 4 },
    { a: 2, b: 5 }, { a: 6, b: 6 }, { a: 3, b: 999 }];
  assert.deepEqual([...g.unfinishedIds(nodes, edges)].sort(), [1]);
  // The other end of a chain is unfinished too.
  assert.deepEqual([...g.unfinishedIds([{ id: 1 }, { id: 2 }], [{ a: 1, b: 2 }])].sort(), [1, 2]);
  assert.equal(g.unfinishedIds([], []).size, 0);
});

test('segment colors step by the golden ratio: the same each time, neighbors far apart', () => {
  const t = g.segmentPositions(50);
  assert.equal(t.length, 50);
  assert.deepEqual(g.segmentPositions(50), t);
  assert.ok(t.every((v) => v >= 0 && v < 1));
  for (let i = 1; i < t.length; i += 1) {
    const d = Math.abs(t[i] - t[i - 1]);
    assert.ok(Math.min(d, 1 - d) > 0.3, `${i}: ${t[i - 1]} -> ${t[i]}`);
  }
});

test('the kept settings are read field by field; anything else is the default', () => {
  assert.deepEqual(g.normalizeGraphSettings(null), g.GRAPH_DEFAULTS);
  assert.equal(g.GRAPH_DEFAULTS.markerSize, 'medium');
  assert.equal(g.GRAPH_DEFAULTS.synapses, false);
  const s = g.normalizeGraphSettings({ synapses: true, markerSize: 'huge', colormap: 'Viridis', discs: 'no' });
  assert.equal(s.synapses, true);
  assert.equal(s.markerSize, 'medium');
  assert.equal(s.colormap, 'Viridis');
  assert.equal(s.discs, true);
});

test('Find ID: a Location ID first, a synapse annotation\'s too, then a synapse structure ID', () => {
  const syn = { synapse_id: 500, type: 'RibbonPre', location_id: 9001, xyz_um: [4, 5, 6], z_section: 12 };
  const cells = [
    { key: 'i/1', nodes: [{ id: 7, xyz_um: [1, 2, 3], z_section: 10 }], synapses: [syn] },
    { key: 'i/2', nodes: [{ id: 500, xyz_um: [9, 9, 9], z_section: 11 }], synapses: [] },
  ];
  assert.deepEqual(g.findId('7', cells), { kind: 'location', key: 'i/1', locationId: 7, xyz_um: [1, 2, 3], section: 10 });
  assert.equal(g.findId('9001', cells).synapse, syn);
  // 500 is c2's Location ID before it is c1's synapse: a Location ID wins, as in GraphApp.
  assert.equal(g.findId('500', cells).key, 'i/2');
  assert.equal(g.findId('500', [cells[0]]).kind, 'synapse');
  assert.equal(g.findId('500', [cells[0]]).locationId, 9001);
  assert.deepEqual(g.findId('123', cells), { kind: 'unknown', id: 123 });
  assert.equal(g.findId('12x', cells), null);
  assert.equal(g.findId('', cells), null);
  assert.equal(g.findId('-3', cells), null);
});

test('isolating keeps what each cell showed, and gives it back to the cells still loaded', () => {
  const visible = new Map([['a', true], ['b', false], ['c', true]]);
  const first = g.isolate(visible, 'a');
  assert.deepEqual([...first.show], [['a', true], ['b', false], ['c', false]]);
  assert.deepEqual([...first.saved], [...visible]);
  // A second isolation keeps the first one's record.
  const now = new Map([['a', false], ['b', true], ['c', true]]);
  const second = g.isolate(now, 'c', first.saved);
  assert.equal(second.saved, first.saved);
  assert.deepEqual([...second.show], [['a', false], ['b', false], ['c', true]]);
  // Back: what was kept; a cell loaded meanwhile keeps its own.
  const later = new Map([['a', false], ['c', true], ['d', true]]);
  assert.deepEqual([...g.restoreVisibility(later, first.saved)], [['a', true], ['c', true], ['d', true]]);
  assert.deepEqual([...g.restoreVisibility(later, null)], [...later]);
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
