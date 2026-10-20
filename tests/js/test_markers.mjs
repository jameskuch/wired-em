// What the readout says about the two annotation markers (static/js/markers.js): the
// section each one is on, and the Z between them.  There is no JS runner in this
// repo: run it with
//
//     node tests/js/test_markers.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  markerReadout, markerReadoutText, sideOf, placeChoice, stepMarker, stepAnchor, activeSlot,
  hideLevels, markerDelta, markerRows, signed,
} = await import(pathToFileURL(path.join(jsDir, 'markers.js')).href);
const M = await import(pathToFileURL(path.join(jsDir, 'markers.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const at = (section, zUm) => ({ section, zUm });

test('the readout names the section of each marker and the Z between them', () => {
  const r = markerReadout(at(1231, 86.17), at(1234, 86.38));
  assert.equal(r.firstZ, 'Z 1231');
  assert.equal(r.secondZ, '1234');
  assert.equal(r.sections, 3);
  assert.ok(Math.abs(r.um - 0.21) < 1e-9);
  assert.equal(r.delta, 'ΔZ +0.21 µm · +3 sec');
  assert.equal(markerReadoutText(r), 'Z 1231 → 1234 · ΔZ +0.21 µm · +3 sec');
});

test('a marker below the yellow one reads with a true minus sign', () => {
  const r = markerReadout(at(1231, 86.17), at(1228, 85.96));
  assert.equal(r.secondZ, '1228');
  assert.equal(r.sections, -3);
  assert.equal(r.delta, 'ΔZ −0.21 µm · −3 sec');
});

test('two markers in one section read as plus-or-minus zero', () => {
  const r = markerReadout(at(900, 63), at(900, 63));
  assert.equal(r.delta, 'ΔZ ±0.00 µm · ±0 sec');
  assert.equal(r.firstZ, 'Z 900');
  assert.equal(r.secondZ, '900');
});

test('the section number steps with the marker: +1, +2, +3 and the section it is on', () => {
  const first = at(1231, 86.17);
  const seen = [1232, 1233, 1234].map((s) => markerReadout(first, at(s, 86.17 + (s - 1231) * 0.07)));
  assert.deepEqual(seen.map((r) => r.sections), [1, 2, 3]);
  assert.deepEqual(seen.map((r) => r.secondZ), ['1232', '1233', '1234']);
  assert.ok(seen.every((r) => r.firstZ === 'Z 1231'), 'the yellow one stays where it is');
});

test('without a section number only the Z between is written', () => {
  const r = markerReadout({ zUm: 10 }, { section: 5, zUm: 10.14 });
  assert.equal(r.firstZ, null);
  assert.equal(r.secondZ, null);
  assert.equal(r.sections, null);
  assert.equal(r.delta, 'ΔZ +0.14 µm');
  assert.equal(markerReadoutText(r), 'ΔZ +0.14 µm');
});

test('no second marker, or a marker without a position, has no readout', () => {
  assert.equal(markerReadout(at(1, 1), null), null);
  assert.equal(markerReadout(null, at(1, 1)), null);
  assert.equal(markerReadout(at(1, NaN), at(2, 1)), null);
  assert.equal(markerReadout(at(1, 1), { section: 2 }), null);
  assert.equal(markerReadoutText(null), '');
});

// -- the red, yellow and blue markers (2026-10-16) ------------------------------------

/** An annotation of cell `key` on `section`, 0.07 µm a section, at x microns. */
const ann = (id, section, { key = 'A', x = 0 } = {}) => ({
  kind: 'annotation', key, locationId: id, section, zUm: section * 0.07, point: [x, 0, section * 0.07],
});
const Y = ann(100, 1231);

test('a chosen annotation above the yellow is red, below it blue, and Flip Z swaps the two', () => {
  assert.equal(sideOf(Y, ann(101, 1234)), 'above');
  assert.equal(sideOf(Y, ann(102, 1228)), 'below');
  assert.equal(sideOf(Y, ann(103, 1231)), null, 'its own section is level');
  assert.equal(sideOf(Y, ann(101, 1234), true), 'below', 'under Flip Z higher sections are drawn below');
  assert.deepEqual(placeChoice(Y, {}, ann(101, 1234)), { action: 'set', slot: 'above' });
  assert.deepEqual(placeChoice(Y, {}, ann(102, 1228)), { action: 'set', slot: 'below' });
});

test('without section numbers the Z in microns decides the side', () => {
  const a = { key: 'A', locationId: 1, zUm: 10 };
  assert.equal(sideOf(a, { key: 'A', locationId: 2, zUm: 10.14 }), 'above');
  assert.equal(sideOf(a, { key: 'A', locationId: 2, zUm: 9.8 }), 'below');
  assert.equal(sideOf(a, { key: 'A', locationId: 2, zUm: 10 + 1e-9 }), null);
  assert.equal(sideOf(a, { key: 'A', locationId: 2 }), null);
});

test('choosing the yellow, a marker, or an annotation on the yellow section', () => {
  const red = ann(101, 1234);
  assert.deepEqual(placeChoice(Y, { above: red }, ann(100, 1231)), { action: 'same' });
  assert.deepEqual(placeChoice(Y, { above: red }, ann(101, 1234)), { action: 'activate', slot: 'above' });
  assert.deepEqual(placeChoice(Y, { above: red }, ann(105, 1240)), { action: 'set', slot: 'above' },
    'a second annotation above replaces the red one');
  assert.deepEqual(placeChoice(Y, { above: red }, ann(103, 1231)), { action: 'anchor' },
    'one on the yellow section becomes the yellow');
  // Another cell's annotation with the same Location ID number is another annotation.
  assert.deepEqual(placeChoice(Y, {}, ann(100, 1231, { key: 'B' })), { action: 'anchor' });
  assert.deepEqual(placeChoice(Y, {}, ann(100, 1240, { key: 'B' })), { action: 'set', slot: 'above' });
});

test('a marker stepped onto the yellow goes; one stepped on keeps its color', () => {
  const red = ann(101, 1232);
  assert.deepEqual(stepMarker(Y, { above: red }, 'above', ann(100, 1231)), { action: 'merge' });
  assert.deepEqual(stepMarker(Y, { above: red }, 'above', ann(104, 1233)), { action: 'move', slot: 'above' });
  assert.deepEqual(stepMarker(Y, { below: ann(102, 1230) }, 'below', ann(106, 1229)), { action: 'move', slot: 'below' });
});

test('one marker crossing the yellow changes color; with both set it is refused', () => {
  const red = ann(101, 1232);
  const past = ann(107, 1230, { x: 3 });    // another branch, below the yellow
  assert.deepEqual(stepMarker(Y, { above: red }, 'above', past), { action: 'switch', from: 'above', to: 'below' });
  const refused = stepMarker(Y, { above: red, below: ann(102, 1228) }, 'above', past);
  assert.equal(refused.action, 'refuse');
  assert.match(refused.reason, /red marker stays above the yellow while there is a blue one/);
  assert.deepEqual(stepMarker(Y, { above: red }, 'above', ann(108, 1231, { x: 2 })), { action: 'skip' },
    'level with the yellow but not the yellow: step on');
});

test('Flip Z: red is the one drawn above, and stepping follows the view', () => {
  // Under Flip Z a lower section is drawn higher: a marker on 1229 is red.
  assert.deepEqual(placeChoice(Y, {}, ann(101, 1229), true), { action: 'set', slot: 'above' });
  assert.deepEqual(stepMarker(Y, { above: ann(101, 1229) }, 'above', ann(109, 1228), true),
    { action: 'move', slot: 'above' });
});

test('moving the yellow keeps the markers on their sides, merges one it lands on, refuses a crossing', () => {
  const red = ann(101, 1234);
  const blue = ann(102, 1228);
  const up = stepAnchor(Y, { above: red, below: blue }, ann(110, 1232));
  assert.equal(up.action, 'move');
  assert.equal(up.markers.above, red);
  assert.equal(up.markers.below, blue);
  assert.equal(up.merged, null);
  const onto = stepAnchor(Y, { above: red, below: blue }, ann(101, 1234));
  assert.deepEqual([onto.action, onto.merged, onto.markers.above, onto.markers.below], ['move', 'above', null, blue]);
  const level = stepAnchor(Y, { above: red }, ann(111, 1234, { x: 5 }));
  assert.equal(level.action, 'refuse');
  assert.match(level.reason, /level with the red marker/);
  // Past the red one with no blue: the red one is below the yellow now, so blue.
  const past = stepAnchor(Y, { above: red }, ann(112, 1236));
  assert.equal(past.action, 'move');
  assert.equal(past.markers.below, red);
  assert.equal(past.markers.above, null);
  // Past the red one with a blue one: both would be below.
  const both = stepAnchor(Y, { above: red, below: blue }, ann(112, 1236));
  assert.equal(both.action, 'refuse');
  assert.match(both.reason, /cannot pass the red marker while there is a blue one/);
});

test('the arrows move the marker moved last, else whichever is set', () => {
  const red = ann(101, 1234);
  const blue = ann(102, 1228);
  assert.equal(activeSlot({ above: red, below: blue }, 'below'), 'below');
  assert.equal(activeSlot({ above: red, below: null }, 'below'), 'above');
  assert.equal(activeSlot({ above: null, below: blue }, null), 'below');
  assert.equal(activeSlot({}, 'above'), null);
  // The yellow chosen or clicked last: the arrows start a marker from it, whatever is set
  // (2026-10-17: Down moved a stepped red marker back instead of adding the blue one).
  assert.equal(activeSlot({ above: red, below: null }, 'primary'), null);
  assert.equal(activeSlot({ above: red, below: blue }, 'primary'), null);
});

test('Hide above goes by the red marker and Hide below by the blue, the yellow standing in', () => {
  const red = ann(101, 1234);
  const blue = ann(102, 1228);
  assert.equal(hideLevels(null, {}), null);
  assert.deepEqual(hideLevels(Y, {}), { upper: Y, lower: Y, both: false });
  assert.deepEqual(hideLevels(Y, { above: red }), { upper: red, lower: Y, both: true });
  assert.deepEqual(hideLevels(Y, { below: blue }), { upper: Y, lower: blue, both: true });
  assert.deepEqual(hideLevels(Y, { above: red, below: blue }), { upper: red, lower: blue, both: true });
});

test('each row: sections and microns in Z from the yellow, and the distance in the volume', () => {
  const red = { ...ann(101, 1234), point: [3, 4, 1234 * 0.07] };
  const yellow = { ...Y, point: [0, 0, 1231 * 0.07] };
  const d = markerDelta(yellow, red);
  assert.equal(d.dz, 3);
  assert.ok(Math.abs(d.dzUm - 0.21) < 1e-9);
  assert.ok(Math.abs(d.distUm - Math.hypot(3, 4, 0.21)) < 1e-9);
  assert.equal(markerDelta(yellow, yellow), null);
  assert.equal(markerDelta(yellow, { ...red, point: null }).distUm, null);
  assert.equal(markerDelta(yellow, { ...red, section: undefined }).dz, null);
  const rows = markerRows(yellow, { above: red, below: null });
  assert.deepEqual(rows.map((r) => r.slot), ['above', 'primary']);
  assert.equal(rows[1].delta, null);
  assert.deepEqual(markerRows(null, { above: red }), []);
  assert.equal(signed(3), '+3');
  assert.equal(signed(-0.214, 2), '−0.21');
  assert.equal(signed(0), '±0');
});

test('a triple click offers the rings about as near as the nearest, not its neighbors', () => {
  const ring = (px, id) => ({ kind: 'annotation', px, locationId: id });
  // c1178 at a whole cell's zoom (tools/check_contextmenu.py --only markerthird): the ring
  // clicked at 0 px, its section's neighbors 1.8 to 2.7 px off.
  const cell = [ring(0, 1), ring(2, 2), ring(2.4, 3), ring(1.8, 4), ring(2.6, 5), ring(2.7, 6)];
  assert.deepEqual(M.choosable(cell).map((c) => c.locationId), [1]);
  // Two rings under the pointer are both offered, and a pointer between two rings offers both.
  assert.deepEqual(M.choosable([ring(0, 1), ring(0, 2), ring(4.7, 3)]).map((c) => c.locationId), [1, 2]);
  assert.deepEqual(M.choosable([ring(2.4, 1), ring(2.9, 2), ring(5.5, 3)]).map((c) => c.locationId), [1, 2]);
  // Other kinds keep their own reach: a wireframe at 0 px does not crowd out a ring at 3.
  const mixed = [{ kind: 'wireframe', px: 0 }, ring(3, 1), { kind: 'skeleton', px: 5 }, ring(5.5, 2)];
  assert.deepEqual(M.choosable(mixed).map((c) => c.kind), ['wireframe', 'annotation', 'skeleton']);
  assert.deepEqual(M.choosable([]), []);
  assert.equal(M.RING_TIE_PX, 1.5);
});

// -- a whole z-slice (2026-10-20) ------------------------------------------------------

test('the next section with annotations, up or down, never the one it is on', () => {
  const sections = [100, 101, 104, 104, 110, 97];
  assert.equal(M.nextSection(sections, 101, 1), 104);
  assert.equal(M.nextSection(sections, 101, -1), 100);
  assert.equal(M.nextSection(sections, 102, 1), 104);     // between two: the nearest that way
  assert.equal(M.nextSection(sections, 110, 1), null);    // the end
  assert.equal(M.nextSection(sections, 97, -1), null);
  assert.equal(M.nextSection(new Set([5, NaN, 7]), 5, 1), 7);
  assert.equal(M.nextSection([], 5, 1), null);
});

test('the annotation that carries the readout after a step: its own cell first, then the nearest in XY', () => {
  const a = (key, id, x, y) => ({ kind: 'annotation', key, locationId: id, point: [x, y, 0] });
  const from = a('V/1', 1, 0, 0);
  const items = [a('V/2', 9, 0.1, 0), a('V/1', 7, 5, 0), a('V/1', 8, 2, 1)];
  assert.equal(M.leadOf(items, from).locationId, 8);      // c1's nearest, though c2's is nearer
  assert.equal(M.leadOf([a('V/2', 9, 3, 0), a('V/3', 4, 1, 1)], from).locationId, 4);   // none of c1: the nearest
  assert.equal(M.leadOf([], from), null);
});

test('whether a candidate is one of a z-slice: one cell and one Location ID', () => {
  const slice = { items: [{ key: 'V/1', locationId: 5 }, { key: 'V/2', locationId: 5 }] };
  assert.ok(M.inSlice(slice, { key: 'V/2', locationId: 5 }));
  assert.ok(!M.inSlice(slice, { key: 'W/2', locationId: 5 }));   // the same number in another volume
  assert.ok(!M.inSlice(null, { key: 'V/1', locationId: 5 }));
});

test('a marker z-slice steps a section at a time and goes when it reaches the yellow', () => {
  const sections = [96, 98, 100, 103, 107];
  const at = { primary: 100, above: 103, below: null };
  assert.deepEqual(M.sliceStep(sections, at, 'above', 1), { action: 'move', section: 107, slot: 'above' });
  assert.deepEqual(M.sliceStep(sections, at, 'above', -1), { action: 'merge', section: 100, into: 'primary' });
  assert.deepEqual(M.sliceStep(sections, { ...at, above: 107 }, 'above', 1), { action: 'end' });
  // The yellow's section stops it even when no annotation of its scope is there (the
  // yellow on another cell): it never passes to the blue side.
  assert.deepEqual(M.sliceStep([96, 103], { primary: 100, above: 103, below: null }, 'above', -1),
    { action: 'merge', section: 100, into: 'primary' });
});

test('from the yellow z-slice the arrows start a red one above or a blue one below; Flip Z swaps them', () => {
  const sections = [96, 98, 100, 103];
  const at = { primary: 100, above: null, below: null };
  assert.deepEqual(M.sliceStep(sections, at, null, 1), { action: 'move', section: 103, slot: 'above' });
  assert.deepEqual(M.sliceStep(sections, at, null, -1), { action: 'move', section: 98, slot: 'below' });
  // Under Flip Z the app steps toward lower sections for Up, and those are the top of the view.
  assert.deepEqual(M.sliceStep(sections, at, null, -1, true), { action: 'move', section: 98, slot: 'above' });
  assert.deepEqual(M.sliceStep([100], at, null, 1), { action: 'end' });
});

test('the yellow z-slice stops at a marker and takes its place; it never passes one', () => {
  const sections = [90, 95, 100, 102, 104, 110];
  const at = { primary: 100, above: 104, below: 95 };
  assert.deepEqual(M.sliceStep(sections, at, 'primary', 1), { action: 'move', section: 102, slot: 'primary' });
  assert.deepEqual(M.sliceStep(sections, { ...at, primary: 102 }, 'primary', 1),
    { action: 'merge', section: 104, into: 'above' });
  assert.deepEqual(M.sliceStep(sections, at, 'primary', -1), { action: 'merge', section: 95, into: 'below' });
  // A marker's section is a stop even with nothing of the yellow's scope on it.
  assert.deepEqual(M.sliceStep([100, 110], { primary: 100, above: 104, below: null }, 'primary', 1),
    { action: 'merge', section: 104, into: 'above' });
  assert.deepEqual(M.sliceStep(sections, { primary: null }, 'primary', 1), { action: 'end' });
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
