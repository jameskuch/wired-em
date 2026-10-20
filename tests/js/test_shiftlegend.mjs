// The Position shift legend (static/js/shiftlegend.js): what it says for a layer
// -- the pair, the exaggeration, the ramp's ticks in micrometers, the direction
// wheel, the cell color -- and the element it keeps in a viewport.  Run with
//
//     node tests/js/test_shiftlegend.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const G = await load('shiftlegend.js');
const S = await load('shifts.js');
const L = await load('shiftlayer.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const node = (id, section, x, { shift = null, live = null } = {}) => ({
  id, parent_id: 1, z_section: section, radius_um: 1, type_code: 1,
  xyz_um: [x, 0, section * 0.07], shift_um: shift, live_shift_um: live,
});
const nodes = Array.from({ length: 20 }, (_, i) => node(i + 1, 10 + i, i * 5, {
  shift: [0.1 + 0.05 * i, 0], live: i % 2 ? [0.01, 0.02] : null,
}));
const applied = (n) => ({ requested: true, applied: true, n_moved: n });
const summary = { section_correction: applied(20), live_registration: applied(10) };
const layerWith = (display = {}, extra = {}) => L.buildLayer({ nodes, summary, display, cellColor: '#2f6fed', ...extra });

test('no layer, an empty one or one with nothing to draw has no legend', () => {
  assert.equal(G.legendModel(null), null);
  assert.equal(G.legendModel(L.buildLayer({ nodes, summary: {}, display: {} })), null);
  assert.equal(G.legendModel(layerWith({ shiftArrows: false, shiftRings: false })), null);
  // Only colors that are on screen: rings alone are gray, and a threshold over every
  // move draws no arrow (review of 2026-10-16).
  assert.equal(G.legendModel(layerWith({ shiftArrows: false, shiftRings: true })), null);
  assert.equal(G.legendModel(layerWith({ shiftMin: 1e5 })), null);
});

test('Magnitude: the pair, its stages, the badge, and ticks at 0, half and Full color at in µm', () => {
  const layer = layerWith();
  const model = G.legendModel(layer);
  assert.equal(model.mode, 'magnitude');
  assert.equal(model.title, 'Section correction');
  assert.equal(model.sub, 'registered → corrected');
  assert.equal(model.badge, S.badgeText(layer.k));
  assert.deepEqual(model.ramp.colors, [...S.RAMPS.dark]);
  assert.deepEqual(model.ramp.ticks.map((t) => t.at), [0, 0.5, 1]);
  assert.equal(model.ramp.ticks[0].text, '0');
  assert.equal(model.ramp.ticks[1].text, G.formatUm(layer.colorMax / 2));
  // Auto is the 95th percentile, so the longest moves are past it: the end says so.
  assert.equal(model.ramp.ticks[2].text, `≥${G.formatUm(layer.colorMax)} µm`);
  // A Full color at past every move has no "≥".
  const all = G.legendModel(layerWith({ shiftColorMax: 10 }));
  assert.equal(all.ramp.ticks[2].text, '10 µm');
  assert.equal(all.ramp.ticks[1].text, '5.0');
});

test('the light (inverted) viewport has the light ramp, as the arrows do there', () => {
  const model = G.legendModel(layerWith(), { inverted: true });
  assert.deepEqual(model.ramp.colors, [...S.RAMPS.light]);
});

test('each pair names its two positions', () => {
  assert.equal(G.legendModel(layerWith({ shiftPair: 'transforms', shiftMin: 0 })).sub, 'stored → registered');
  assert.equal(G.legendModel(layerWith({ shiftPair: 'both' })).sub, 'stored → corrected');
  assert.equal(G.legendModel(layerWith({ shiftPair: 'transforms', shiftMin: 0 })).title, 'Current transforms');
});

test('Direction: a wheel of hues from +X counter-clockwise, in the viewport\'s lightness', () => {
  const model = G.legendModel(layerWith({ shiftColor: 'direction' }));
  assert.equal(model.mode, 'direction');
  assert.equal(model.ramp, undefined);
  assert.equal(model.wheel.colors.length, 36);
  const hue0 = S.hslToRgb(0, S.DIRECTION_SATURATION, S.DIRECTION_LIGHTNESS.dark)
    .map((c) => Math.round(c * 255).toString(16).padStart(2, '0')).join('');
  assert.equal(model.wheel.colors[0], `#${hue0}`, '+X is hue 0');
  assert.notDeepEqual(G.legendModel(layerWith({ shiftColor: 'direction' }), { inverted: true }).wheel.colors,
    model.wheel.colors, 'the light viewport\'s hues are darker');
  // CSS runs a conic gradient clockwise: the hues go in reverse, starting at +X.
  const css = G.wheelGradient(model.wheel.colors);
  assert.match(css, /^conic-gradient\(from 90deg, /);
  const stops = css.slice('conic-gradient(from 90deg, '.length, -1).split(', ');
  assert.equal(stops.length, 37);
  assert.equal(stops[0], `${model.wheel.colors[0]} 0.0deg`);
  assert.equal(stops[1], `${model.wheel.colors[35]} 10.0deg`, 'clockwise is toward -Y: the last hue');
  assert.equal(stops[9], `${model.wheel.colors[27]} 90.0deg`, '-Y, straight down, is hue 0.75');
  assert.equal(stops[36], `${model.wheel.colors[0]} 360.0deg`);
});

test('Cell color: the cell\'s own color, and a safe gray for anything that is not one', () => {
  assert.equal(G.legendModel(layerWith({ shiftColor: 'cell' }), { cellColor: '#ff8800' }).cell, '#ff8800');
  for (const bad of ['red', 'url(x)', '#12345', null, 7]) {
    assert.equal(G.legendModel(layerWith({ shiftColor: 'cell' }), { cellColor: bad }).cell, '#999999', String(bad));
  }
});

test('lengths print with the decimals they need', () => {
  assert.deepEqual([0, 0.0042, 0.037, 0.18, 0.56, 1.25, 12.4, 159.1].map(G.formatUm),
    ['0', '0.004', '0.037', '0.18', '0.56', '1.3', '12', '159']);
});

test('several cells: one block per different legend, named by its cells', () => {
  const a = layerWith();
  const b = layerWith();
  const c = layerWith({ shiftColor: 'direction' });
  const blocks = G.legendBlocks([{ id: 1, layer: a }, { id: 2, layer: b }, { id: 3, layer: c }]);
  assert.equal(blocks.length, 2);
  assert.deepEqual(blocks[0].cells, ['c1', 'c2']);
  assert.equal(blocks[0].who, 'c1 c2');
  assert.equal(blocks[1].who, 'c3');
  // One cell: no name, it is the cell on screen.
  assert.equal(G.legendBlocks([{ id: 1, layer: a }])[0].who, '');
  assert.deepEqual(G.legendBlocks([]), []);
  assert.deepEqual(G.legendBlocks([{ id: 9, layer: null }]), []);
  const many = Array.from({ length: 6 }, (_, i) => ({ id: i, layer: layerWith({ shiftColorMax: i + 1 }) }));
  assert.equal(G.legendBlocks(many).length, 3, 'at most three blocks');
  // An ID travels with its volume: the caller names cells of several volumes so.
  const named = G.legendBlocks([{ id: 1178, layer: a, name: 'c1178 i' }, { id: 1178, layer: c, name: 'c1178 rc1' }]);
  assert.deepEqual(named.map((x) => x.who), ['c1178 i', 'c1178 rc1']);
});

// -- on a page ------------------------------------------------------------------------------------

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.attrs = {}; this.hidden = false; this.className = ''; this.textContent = ''; }
  append(...kids) { this.children.push(...kids); kids.forEach((k) => { k.parent = this; }); }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  setAttribute(k, v) { this.attrs[k] = v; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  set innerHTML(_) { throw new Error('innerHTML is never set'); }
  find(cls) {
    if (this.className.split(' ').includes(cls)) return this;
    for (const k of this.children) { const hit = k.find?.(cls); if (hit) return hit; }
    return null;
  }
}
globalThis.document = { createElement: (tag) => new El(tag) };

test('ShiftLegend: made on the first legend, hidden with none, rebuilt only when it changes', () => {
  const host = new El('div');
  const legend = new G.ShiftLegend(host);
  legend.update([]);
  assert.equal(host.children.length, 0, 'a viewport that never draws the layer gets no legend');
  const blocks = G.legendBlocks([{ id: 1, layer: layerWith() }]);
  legend.update(blocks);
  assert.equal(host.children.length, 1);
  const el = host.children[0];
  assert.equal(el.className, 'shift-legend');
  assert.equal(el.hidden, false);
  assert.equal(el.find('shift-legend-title').textContent, 'Section correction');
  assert.match(el.find('shift-legend-ramp').style.background, /^linear-gradient\(to right, #/);
  assert.equal(el.find('shift-legend-ticks').children.length, 3);
  const first = el.children[0];
  legend.update(G.legendBlocks([{ id: 1, layer: layerWith() }]));
  assert.equal(el.children[0], first, 'the same legend is not rebuilt');
  legend.update(G.legendBlocks([{ id: 1, layer: layerWith({ shiftColor: 'direction' }) }]));
  assert.match(el.find('shift-legend-wheel').style.background, /^conic-gradient/);
  legend.update([]);
  assert.equal(el.hidden, true);
  legend.destroy();
  assert.equal(host.children.length, 0);
});

test('stacked above the chip: its bottom is where the chip begins, and the stylesheet\'s when the chip is gone', () => {
  const host = new El('div');
  host.getBoundingClientRect = () => ({ top: 100, bottom: 700 });
  const chip = new El('div');
  chip.offsetHeight = 56;          // two rows: a narrow window, or the slice's stepper
  chip.getBoundingClientRect = () => ({ top: 604, bottom: 660 });
  const legend = new G.ShiftLegend(host);
  legend.stackAbove(chip);
  legend.update(G.legendBlocks([{ id: 1, layer: layerWith() }]));
  assert.equal(legend.el.style.bottom, '104px', '96 px of chip and margin below it, and 8 more');
  chip.hidden = true;
  legend._place();
  assert.equal(legend.el.style.bottom, '', 'no chip: back to the stylesheet');
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
