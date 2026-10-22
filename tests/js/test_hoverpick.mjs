// The annotation the pick readout names as the pointer moves
// (static/js/hoverpick.js).  Run with
//
//     node tests/js/test_hoverpick.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.  A ray is
// handed over as the renderer does, in each cell's own frame: an origin and a unit
// direction, microns.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  HOVER_REACH_UM, HOVER_REACH_Z_UM, discEntry, discGap, nearestAnnotation,
} = await import(pathToFileURL(path.join(jsDir, 'hoverpick.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} is not ${b}`);

const unit = (v) => {
  const n = Math.hypot(...v);
  return v.map((x) => x / n);
};
/** One cell of annotations `[x, y, z, r, id]`, seen along a ray. */
const cell = (key, rows, origin, dir) => ({
  key, origin, dir: unit(dir),
  nodes: rows.map(([x, y, z, r, id]) => ({ id, xyz_um: [x, y, z], radius_um: r })),
});
/** Orthographic: `scale` microns a pixel everywhere. */
const ortho = (look = 'render', scale = 0.01) => ({ look, perspective: false, scale, near: -Infinity });
/** Perspective: `scale` microns a pixel per micron along the ray, the camera's near plane 0.1 um on. */
const persp = (look = 'render', scale = 0.001) => ({ look, perspective: true, scale, near: 0.1 });
const named = (found) => found && found.node.id;

/** Sections 0.09 um apart, z 0 to 0.9, every disc 2 um across about (0, 0): a soma's stack. */
const STACK = Array.from({ length: 11 }, (_, i) => [0, 0, Math.round(i * 9) / 100, 2, 100 + i]);

// -- the geometry --------------------------------------------------------------------

test('a ray down the axis enters a disc half its thickness above it', () => {
  near(discEntry(0, 0, -10, 0, 0, -1, 1, 0.1, 0), 9.9);
  near(discEntry(0.9, 0, -10, 0, 0, -1, 1, 0.1, 0), 9.9, 1e-9);
  assert.ok(Number.isNaN(discEntry(1.1, 0, -10, 0, 0, -1, 1, 0.1, 0)), 'outside the radius: missed');
});

test('a ray in the section plane meets a disc only within its half thickness', () => {
  // Along +y, at x 0.5: through a disc of radius 1 at y 5, entering at y 5 - sqrt(0.75).
  near(discEntry(0.5, 5, 0.05, 0, 1, 0, 1, 0.1, 0), 5 - Math.sqrt(0.75), 1e-12);
  assert.ok(Number.isNaN(discEntry(0.5, 5, 0.15, 0, 1, 0, 1, 0.1, 0)), 'above the slab: missed');
});

test('a disc met only behind the near plane is not met; the camera inside one meets it at once', () => {
  assert.ok(Number.isNaN(discEntry(0, 0, 10, 0, 0, -1, 1, 0.1, 0.1)), 'behind the camera');
  near(discEntry(0, 0, 0, 0, 0, -1, 1, 0.5, 0.1), 0.1);
  assert.ok(Number.isNaN(discEntry(0, 0, 0, 0, 0, -1, 1, 0.1, 0.1)), 'left it at the near plane');
});

test('how near a ray passes a disc: edge-on and face-on exactly', () => {
  // Face-on, 1.2 um from the axis of a disc of radius 1: 0.2 um off its rim.
  near(discGap(1.2, 0, -10, 0, 0, -1, 1, 10), 0.2, 1e-12);
  // Edge-on, 0.03 um above the plane and inside the rim: 0.03.
  near(discGap(0.5, 5, -0.03, 0, 1, 0, 1, 5), 0.03, 1e-12);
  // Through it at an angle: 0, though the ray's nearest point to the center is 4 um off it.
  const d = unit([1, 0, -1]);
  const [cx, cy, cz] = [-8, 0, 0];          // the ray crosses the plane 8 um from the center
  const t = cx * d[0] + cy * d[1] + cz * d[2];
  near(discGap(cx, cy, cz, ...d, 10, t), 0, 1e-12);
});

// -- the 3D render ---------------------------------------------------------------------

test('looking down Z at a stack, the top section is named', () => {
  const found = nearestAnnotation([cell('a', STACK, [0.5, 0, 50], [0, 0, -1])], ortho());
  assert.equal(named(found), 110);
  assert.equal(found.hit, true);
  near(found.depth, 50 - 0.9 - HOVER_REACH_Z_UM, 1e-9);
});

test('seen from the side, the section at the pointer is named, and the next one a section up', () => {
  for (const [i, z] of [[5, 0.45], [6, 0.54], [6, 0.51], [5, 0.48], [0, 0.0], [10, 0.9]]) {
    const found = nearestAnnotation([cell('a', STACK, [0.7, -50, z], [0, 1, 0])], ortho());
    assert.equal(named(found), 100 + i, `at z ${z}`);
  }
});

test('the disc in front is named, not the one behind it on the same ray', () => {
  const rows = [[0, 5, 0, 1, 2], [0, -5, 0, 1, 1]];
  assert.equal(named(nearestAnnotation([cell('a', rows, [0, -50, 0], [0, 1, 0])], ortho())), 1);
  assert.equal(named(nearestAnnotation([cell('a', rows, [0, 50, 0], [0, -1, 0])], ortho())), 2);
});

test('a ray between two linked discs of a process names the process, not a cell behind it', () => {
  // Rims 0.6 um apart in one section; the ray passes 0.3 um from each, inside the reach.
  const rows = [[-0.8, -5, 0, 0.5, 1], [0.8, -5, 0, 0.5, 2], [0, 5, 0, 3, 3]];
  assert.ok(0.3 < HOVER_REACH_UM);
  const found = nearestAnnotation([cell('a', rows, [0, -50, 0], [0, 1, 0])], ortho());
  assert.ok([1, 2].includes(named(found)), `named ${named(found)}`);
  // Past the reach (rims 1.4 um apart) the ray goes between them to the cell behind.
  const wide = [[-1.2, -5, 0, 0.5, 1], [1.2, -5, 0, 0.5, 2], [0, 5, 0, 3, 3]];
  assert.equal(named(nearestAnnotation([cell('a', wide, [0, -50, 0], [0, 1, 0])], ortho())), 3);
});

test('a ray that meets no disc names the one nearest the pointer on the screen', () => {
  // 1 um a pixel; the ray at x 10 is 9 um off one rim and 6 off the other.
  const rows = [[0, 0, 0, 1, 1], [20, 0, 0, 4, 2]];
  const found = nearestAnnotation([cell('a', rows, [10, 0, 50], [0, 0, -1])], ortho('render', 1));
  assert.equal(named(found), 2);
  assert.equal(found.hit, false);
  near(found.px, 6, 1e-9);
});

test('an annotation the caller turns down (cropped away) is passed over', () => {
  const rows = [[0, 5, 0, 1, 2], [0, -5, 0, 1, 1]];
  const view = [cell('a', rows, [0, -50, 0], [0, 1, 0])];
  const asked = [];
  const found = nearestAnnotation(view, ortho(), (c, node) => { asked.push(node.id); return node.id !== 1; });
  assert.equal(named(found), 2);
  assert.ok(asked.length <= 2, `asked of ${asked}`);
  assert.equal(nearestAnnotation(view, ortho(), () => false), null);
});

test('an annotation behind a perspective camera is never named', () => {
  const rows = [[0, 0, 60, 2, 1], [30, 0, 0, 1, 2]];
  // Looking down -z from z 50: the disc at z 60 is behind; the other is far to the side but in front.
  const found = nearestAnnotation([cell('a', rows, [0, 0, 50], [0, 0, -1])], persp());
  assert.equal(named(found), 2);
  assert.equal(found.hit, false);
});

test('cells in different frames are compared along the one ray', () => {
  // The same ray, given in each cell's frame: the second cell's volume is moved 100 um in x.
  const a = cell('a', [[0, 10, 0, 1, 1]], [0, -50, 0], [0, 1, 0]);
  const b = cell('b', [[100, 5, 0, 1, 2]], [100, -50, 0], [0, 1, 0]);
  const found = nearestAnnotation([a, b], ortho());
  assert.equal(found.cell.key, 'b');
  assert.equal(named(found), 2);
});

// -- on a surface ----------------------------------------------------------------------

/** A soma's disc (r 6 at z 0), and a thin process in front of it (r 0.2, 25 um nearer the camera). */
const SOMA = [[0, 0, 0, 6, 1], [0.4, 0, 25, 0.2, 2]];

test('on a surface, the annotation nearest the point under the pointer is named', () => {
  // Looking down -z from z 50, 0.05 um a pixel: the ray passes 0.2 um off the
  // process's rim (4 px, in its reach), but the surface drawn there is the soma's.
  const solid = { ...cell('a', SOMA, [0.8, 0, 50], [0, 0, -1]), solid: true };
  const view = { ...ortho('render', 0.05) };
  assert.equal(named(nearestAnnotation([solid], view)), 2, 'by the discs alone the process takes it');
  const found = nearestAnnotation([solid], { ...view, surface: 49 });
  assert.equal(named(found), 1);
  assert.equal(found.hit, true);
});

test("a soma's surface swollen past its discs still names the soma", () => {
  // Seen from the side, the ray 1.5 um above the soma's top disc: no disc is met,
  // and a process 1.1 um above the ray is nearer it on the screen than the soma.
  const rows = [[0, 0, 0, 6, 1], [0.5, 0, 2.6, 0.2, 2]];
  const solid = { ...cell('a', rows, [0, -50, 1.5], [0, 1, 0]), solid: true };
  assert.equal(named(nearestAnnotation([solid], ortho('render', 0.05))), 2, 'on the screen the process is nearer');
  assert.equal(named(nearestAnnotation([solid], { ...ortho('render', 0.05), surface: 44.5 })), 1);
});

test('rings in front of an opaque surface are named; rings behind it are not', () => {
  const surfaceCell = { ...cell('a', [[0, 0, 0, 6, 1]], [0, 0, 50], [0, 0, -1]), solid: true };
  const ringsFront = cell('b', [[0.5, 0, 20, 1, 2]], [0, 0, 50], [0, 0, -1]);
  const ringsBehind = cell('c', [[0.5, 0, -20, 1, 3]], [0, 0, 50], [0, 0, -1]);
  const view = { ...ortho('render', 0.05), surface: 50 - 0.5 };
  assert.equal(named(nearestAnnotation([surfaceCell, ringsFront, ringsBehind], view)), 2);
  assert.equal(named(nearestAnnotation([surfaceCell, ringsBehind], view)), 1);
});

test("on a surface, a solid cell's annotation the caller turns down is passed over", () => {
  const solid = { ...cell('a', [[0, 0, 0, 6, 1], [0, 0, -2, 6, 2]], [0, 0, 50], [0, 0, -1]), solid: true };
  const view = { ...ortho('render', 0.05), surface: 50 };
  assert.equal(named(nearestAnnotation([solid], view)), 1);
  assert.equal(named(nearestAnnotation([solid], view, (c, node) => node.id !== 1)), 2);
});

// -- GraphApp --------------------------------------------------------------------------

test('GraphApp names the dot nearest the pointer, not the disc the pointer is inside', () => {
  // A soma's dot 3 um off the ray, nearer the viewer; a dendrite's 0.5 um off it, behind.
  const rows = [[3, 0, 5, 5, 1], [0.5, 0, -5, 0.3, 2]];
  const found = nearestAnnotation([cell('a', rows, [0, 0, 50], [0, 0, -1])], ortho('graph', 0.1));
  assert.equal(named(found), 2);
  near(found.px, 5, 1e-9);
  // The 3D render names the soma: the ray goes through its disc first.
  assert.equal(named(nearestAnnotation([cell('a', rows, [0, 0, 50], [0, 0, -1])], ortho('render', 0.1))), 1);
});

test('GraphApp: dots within half a pixel of each other go to the one nearer the viewer', () => {
  const rows = [[0.04, 0, -5, 0.3, 1], [0.0, 0, 5, 0.3, 2], [0.2, 0, 10, 0.3, 3]];
  // 0.1 um a pixel: 0.4, 0 and 2 px off; the first two tie, and 2 is nearer the camera.
  const found = nearestAnnotation([cell('a', rows, [0, 0, 50], [0, 0, -1])], ortho('graph', 0.1));
  assert.equal(named(found), 2);
  const farther = [[0.04, 0, 5, 0.3, 1], [0.0, 0, -5, 0.3, 2]];
  assert.equal(named(nearestAnnotation([cell('a', farther, [0, 0, 50], [0, 0, -1])], ortho('graph', 0.1))), 1);
});

test('nothing loaded, or nothing with a place, names nothing', () => {
  assert.equal(nearestAnnotation([], ortho()), null);
  assert.equal(nearestAnnotation([{ origin: [0, 0, 0], dir: [0, 0, -1], nodes: [{ id: 1 }] }], ortho()), null);
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
