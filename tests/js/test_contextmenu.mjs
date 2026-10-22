// What the render window's right-click menu decides without a page
// (static/js/contextmenu.js): a click against a pan, the order and the names of
// what is under the pointer, and where the menu goes.  Run with
//
//     node tests/js/test_contextmenu.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  CLICK_SLOP_PX, SURFACE_REACH_PX, MAX_ROWS, CELL_KINDS, SELECTABLE_KINDS, isClick,
  ContextGesture, identity, sameCandidate, listCandidates, offersChoice, describeCandidate,
  contextTitle, placeInside, pickOf,
} = await import(pathToFileURL(path.join(jsDir, 'contextmenu.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const ring = (id, px, depth = 10, key = 'NeitzInferiorMonkey/1178') => ({
  kind: 'annotation', key, px, depth, locationId: id, section: 100 + (id % 7), zUm: 1, point: [0, 0, 0],
});
const surface = (key, px = 0, depth = 10) => ({ kind: 'surface', key, px, depth, point: [0, 0, 0] });
const branch = (segment, px, key = 'NeitzInferiorMonkey/1178') => ({
  kind: 'skeleton', key, px, depth: 10, segment, point: [0, 0, 0],
});

// -- click or pan -------------------------------------------------------------------

test('a click is a press and a release within the slop, and the slop is a little looser than 5 px', () => {
  assert.ok(CLICK_SLOP_PX > 5, 'a right click that wobbled 6 pixels is still a click');
  assert.ok(isClick({ x: 10, y: 10 }, { x: 10 + CLICK_SLOP_PX, y: 10 }));
  assert.ok(!isClick({ x: 10, y: 10 }, { x: 10 + CLICK_SLOP_PX + 1, y: 10 }));
  assert.ok(isClick({ x: 0, y: 0 }, { x: 5, y: 5 }), 'diagonally 7.07');
});

test('Windows: the button goes up, then contextmenu arrives -- the menu opens where it came up', () => {
  const g = new ContextGesture();
  g.press({ x: 100, y: 100, button: 2 });
  assert.equal(g.release({ x: 102, y: 101 }), null, 'a release alone opens nothing');
  assert.deepEqual(g.menu({ x: 102, y: 101 }), { x: 102, y: 101 });
});

test('Windows: a drag is a pan, whichever way far it went', () => {
  const g = new ContextGesture();
  g.press({ x: 100, y: 100, button: 2 });
  g.release({ x: 160, y: 100 });
  assert.equal(g.menu({ x: 160, y: 100 }), null);
});

test('macOS: contextmenu arrives as the button goes down -- nothing opens until it comes up', () => {
  const g = new ContextGesture();
  g.press({ x: 100, y: 100, button: 2 });
  assert.equal(g.menu({ x: 100, y: 100 }), null, 'a menu opened here would stall the pan under it');
  assert.deepEqual(g.release({ x: 101, y: 100 }), { x: 101, y: 100 }, 'a click: opens where it came up');
});

test('macOS: a right-drag opens no menu (the bug: it opened at the press and the pan stalled)', () => {
  const g = new ContextGesture();
  g.press({ x: 100, y: 100, button: 2 });
  assert.equal(g.menu({ x: 100, y: 100 }), null);
  assert.equal(g.release({ x: 100 + 3 * CLICK_SLOP_PX, y: 100 }), null);
});

test('a left button is no right click, a Control-click is (the Mac way), a touch is not', () => {
  const left = new ContextGesture();
  left.press({ x: 5, y: 5, button: 0 });
  assert.equal(left.menu({ x: 5, y: 5 }), null);
  assert.equal(left.release({ x: 5, y: 5 }), null);
  const control = new ContextGesture();
  control.press({ x: 5, y: 5, button: 0, ctrl: true });
  assert.equal(control.menu({ x: 5, y: 5 }), null);
  assert.deepEqual(control.release({ x: 5, y: 5 }), { x: 5, y: 5 });
});

test('a menu key, or a contextmenu with no press behind it, opens nothing', () => {
  const g = new ContextGesture();
  assert.equal(g.menu({ x: 300, y: 300 }), null, 'never pressed');
  g.press({ x: 10, y: 10, button: 2 });
  g.release({ x: 10, y: 10 });
  assert.equal(g.menu({ x: 300, y: 300 }), null, 'far from where the last press was');
});

test('a press that is canceled is forgotten, and the next one starts clean', () => {
  const g = new ContextGesture();
  g.press({ x: 10, y: 10, button: 2 });
  g.menu({ x: 10, y: 10 });
  g.cancel();
  assert.equal(g.release({ x: 10, y: 10 }), null);
  g.press({ x: 50, y: 50, button: 2 });
  g.release({ x: 50, y: 50 });
  assert.deepEqual(g.menu({ x: 50, y: 50 }), { x: 50, y: 50 });
});

test('each press asks again: a contextmenu of the last click does not open the next one\'s', () => {
  const g = new ContextGesture();
  g.press({ x: 10, y: 10, button: 2 });
  g.menu({ x: 10, y: 10 });
  g.release({ x: 10, y: 10 });
  g.press({ x: 40, y: 40, button: 2 });         // a pan begins; no contextmenu this time
  assert.equal(g.release({ x: 40, y: 40 }), null);
});

// -- what is under the pointer ------------------------------------------------------

test('nearest the pointer first, to the pixel; then the specific before the general; then the camera', () => {
  const rows = listCandidates([
    surface('NeitzInferiorMonkey/1178', 0, 30),
    ring(3, 4.2), ring(2, 1.2), branch(7, 1.4), ring(1, 0.3, 50), ring(4, 0.4, 20),
  ]);
  // px 0: ring 4 (depth 20) and ring 1 (depth 50) tie with the surface, and a ring comes before the
  // surface it is on; 1.2 and 1.4 both round to 1: the annotation before the branch; then 4.2.
  assert.deepEqual(rows.map((c) => `${c.kind}:${c.locationId ?? c.segment ?? ''}`),
    ['annotation:4', 'annotation:1', 'surface:', 'annotation:2', 'skeleton:7', 'annotation:3']);
});

test('a thing is listed once, at its nearest', () => {
  const rows = listCandidates([ring(5, 3), ring(5, 1), ring(6, 2), ring(5, 2)]);
  assert.deepEqual(rows.map((c) => [c.locationId, c.px]), [[5, 1], [6, 2]]);
  assert.equal(identity(ring(5, 1)), identity(ring(5, 9)));
  assert.notEqual(identity(ring(5, 1)), identity(ring(5, 1, 10, 'RC1/1178')), 'the volume is part of it');
});

test('a cell is listed once, whichever of its surface, wireframe and wires is nearest', () => {
  const key = 'NeitzInferiorMonkey/1178';
  const rows = listCandidates([
    { kind: 'wireframe', key, px: 2, depth: 10 },
    { kind: 'annowire', key, px: 1, depth: 10 },
    surface(key, 0),
    surface('RC1/410', 3),
  ]);
  assert.deepEqual(rows.map((c) => [c.kind, c.key]), [['surface', key], ['surface', 'RC1/410']]);
  // With the surface hidden the wireframe is the cell's entry; on a tie the surface is.
  const bare = listCandidates([{ kind: 'wireframe', key, px: 1, depth: 10 }, surface(key, 1)]);
  assert.deepEqual(bare.map((c) => c.kind), ['surface']);
  const wired = listCandidates([{ kind: 'wireframe', key, px: 1, depth: 10 }, { kind: 'annowire', key, px: 0, depth: 10 }]);
  assert.deepEqual(wired.map((c) => c.kind), ['annowire']);
  for (const kind of CELL_KINDS) assert.ok(!SELECTABLE_KINDS.has(kind), `${kind} is the cell, not an item`);
});

test('the same annotation id of two volumes is two things', () => {
  const rows = listCandidates([ring(9, 1), ring(9, 1, 10, 'RC1/410')]);
  assert.equal(rows.length, 2);
});

test('at most MAX_ROWS rows, the nearest', () => {
  const many = Array.from({ length: 40 }, (_, i) => ring(1000 + i, i % 6));
  const rows = listCandidates(many);
  assert.equal(rows.length, MAX_ROWS);
  assert.ok(rows.every((c, i) => i === 0 || Math.round(rows[i - 1].px) <= Math.round(c.px)));
  assert.equal(listCandidates(many, { limit: 3 }).length, 3);
});

test('what is not a candidate is not listed, and nothing is nothing', () => {
  assert.deepEqual(listCandidates([null, undefined, {}, { kind: 'surface' }]), []);
  assert.deepEqual(listCandidates([]), []);
  assert.deepEqual(listCandidates(null), []);
});

test('listing leaves the renderer\'s array alone', () => {
  const items = [ring(2, 3), ring(1, 1)];
  listCandidates(items);
  assert.deepEqual(items.map((c) => c.locationId), [2, 1]);
});

test('the menu offers a choice only when there is one', () => {
  assert.ok(!offersChoice([]));
  assert.ok(!offersChoice(listCandidates([surface('a/1')])));
  assert.ok(!offersChoice(listCandidates([surface('a/1'), { kind: 'wireframe', key: 'a/1', px: 1, depth: 1 }])),
    'one cell, shown two ways, is one thing');
  assert.ok(offersChoice(listCandidates([surface('a/1'), ring(1, 2, 10, 'a/1')])));
  assert.ok(offersChoice(listCandidates([surface('a/1'), surface('a/2')])));
});

test('the row that is the menu\'s is found among the rows', () => {
  const a = ring(1, 0);
  assert.ok(sameCandidate(a, { ...a, px: 5 }));
  assert.ok(!sameCandidate(a, ring(2, 0)));
  assert.ok(!sameCandidate(a, null));
  assert.ok(sameCandidate(surface('a/1'), { kind: 'wireframe', key: 'a/1' }), 'the cell is the cell');
});

// -- what a click picks (2.6.5) -----------------------------------------------------

test('a click picks what the menu would list first: a thin process no ray found', () => {
  // A ray went between the triangles of 16 of 60 drawn pixels of c1178; the tolerance found them.
  const thinProcess = surface('NeitzInferiorMonkey/1178', 2.4, 40);
  assert.equal(pickOf([thinProcess]), thinProcess);
  assert.equal(pickOf([]), null, 'nothing under the pointer picks nothing');
  assert.equal(pickOf(undefined), null);
});

test('a click on a surface is the surface, not a line a few pixels off it', () => {
  // A skeleton nearer the camera, 5 px away (a micron at close zoom is 14 to 25 px:
  // three's line threshold gave the click to the line).
  const behind = surface('NeitzInferiorMonkey/21768', 0, 30);
  const line = { ...branch(4, 5, 'NeitzInferiorMonkey/1178'), depth: 20 };
  assert.equal(pickOf([line, behind]), behind);
  // On the line itself, the line is what was pointed at.
  const onIt = { ...line, px: 0.4 };
  assert.equal(pickOf([behind, onIt]), onIt);
});

test('on its own pixel an annotation is picked before the surface it is drawn on', () => {
  const on = surface('NeitzInferiorMonkey/1178', 0, 12);
  const r = ring(1001, 0.4, 12);
  assert.equal(pickOf([on, r]), r);
  // A pixel off it, the surface the pointer is on.
  assert.equal(pickOf([on, ring(1001, 1.6, 12)]), on);
});

// -- what they are called -----------------------------------------------------------

const naming = { cell: 'c1178', volume: 'Inferior Monkey' };

test('a GraphApp synapse is that one synapse, not its type', () => {
  const naming = { cell: 'c21768', volume: 'Inferior Monkey' };
  const one = { kind: 'synapse', key: 'k', type: 'RibbonPre', synapseId: 9329, section: 734 };
  const other = { ...one, synapseId: 9330 };
  assert.equal(describeCandidate(one, naming), 'Synapse 9329 (RibbonPre) of c21768 (Inferior Monkey) · section 734');
  assert.equal(contextTitle(one, naming), 'c21768 · synapse 9329 (RibbonPre)');
  assert.ok(!sameCandidate(one, other), 'two synapses of one type are two things');
  assert.ok(sameCandidate({ kind: 'synapse', key: 'k', type: 'RibbonPre' }, { kind: 'synapse', key: 'k', type: 'RibbonPre' }));
});

test('the names the Select menu has always used are unchanged, byte for byte', () => {
  assert.equal(describeCandidate({ kind: 'annotation', locationId: 1335638, section: 983 }, naming),
    'Annotation 1335638 (Inferior Monkey) · c1178 · section 983');
  assert.equal(describeCandidate({ kind: 'skeleton' }, naming), 'Skeleton branch of c1178 (Inferior Monkey)');
  assert.equal(describeCandidate({ kind: 'wireframe' }, naming), 'Wireframe of c1178 (Inferior Monkey)');
  assert.equal(describeCandidate({ kind: 'synapse', type: 'Ribbon' }, naming),
    'Ribbon synapses of c1178 (Inferior Monkey)');
  assert.equal(describeCandidate({ kind: 'nodes', label: 'Loop 2' }, naming), 'Loop 2 (Inferior Monkey) · c1178');
  assert.equal(describeCandidate({ kind: 'annotation', locationId: 5, section: 3 }, { cell: 'NeitzInferiorMonkey/9' }),
    'Annotation 5 () · NeitzInferiorMonkey/9 · section 3');
});

test('the cell kinds are named, and every name carries the volume', () => {
  assert.equal(describeCandidate({ kind: 'surface' }, naming), 'Surface of c1178 (Inferior Monkey)');
  assert.equal(describeCandidate({ kind: 'annowire' }, naming), 'Annotation wireframe of c1178 (Inferior Monkey)');
  assert.equal(describeCandidate({ kind: 'shifts' }, naming), 'Position shift of c1178 (Inferior Monkey)');
  for (const c of [ring(1, 0), surface('a/1'), branch(0, 0), { kind: 'synapse', type: 'Conventional' }]) {
    assert.ok(describeCandidate({ ...c, section: 1 }, naming).includes('(Inferior Monkey)'), c.kind);
  }
});

test('the title is the cell, and says which thing on it when it is one', () => {
  assert.equal(contextTitle(null, naming), 'c1178');
  assert.equal(contextTitle(surface('a/1'), naming), 'c1178', 'a surface keeps the title the menu always had');
  assert.equal(contextTitle({ kind: 'wireframe' }, naming), 'c1178');
  assert.equal(contextTitle(ring(77, 0), naming), 'c1178 · annotation 77 (Inferior Monkey)');
  assert.equal(contextTitle(branch(2, 0), naming), 'c1178 · skeleton branch');
  assert.equal(contextTitle({ kind: 'synapse', type: 'Ribbon' }, naming), 'c1178 · Ribbon synapses');
});

// -- where it goes ------------------------------------------------------------------

test('a menu opens where it was asked for, and moves in only as far as it must', () => {
  const view = { width: 1000, height: 800 };
  assert.deepEqual(placeInside({ x: 100, y: 100 }, { width: 200, height: 300 }, view), { left: 100, top: 100 });
  assert.deepEqual(placeInside({ x: 950, y: 700 }, { width: 200, height: 300 }, view), { left: 792, top: 492 });
});

test('a menu taller than the window starts at the top, where its first items are', () => {
  const view = { width: 1000, height: 600 };
  assert.deepEqual(placeInside({ x: 100, y: 300 }, { width: 200, height: 900 }, view), { left: 100, top: 8 },
    'the old clamp put it at -308, above the window, with the title and the rows out of reach');
  assert.deepEqual(placeInside({ x: 5, y: 5 }, { width: 2000, height: 10 }, view), { left: 8, top: 8 });
});

test('the constants are what the docs say', () => {
  assert.equal(SURFACE_REACH_PX, 4);
  assert.equal(MAX_ROWS, 12);
});

// -- run ----------------------------------------------------------------------------
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
