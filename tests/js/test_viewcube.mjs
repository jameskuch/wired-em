// The view cube's geometry (static/js/viewcube.js): which of its 26 parts the
// pointer is over, the view each turns to, the face controls' quarter turns, and
// that no face's text is ever mirrored.  Run with
//
//     node tests/js/test_viewcube.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.  A view is
// built here from its direction and up the way the renderer's camera is
// (right = up x back), with no three.js.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  FACES, REGIONS, HALF, PERSPECTIVE, STAGE, regionKind, volumeLabel, buttonFor, regionView, cubeMatrix,
  faceMatrix, cssMatrix, project, hitRegion, behindCube, faceCells, faceOn, quarterTurn, tripod,
} = await import(pathToFileURL(path.join(jsDir, 'viewcube.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (a) => { const n = Math.hypot(...a); return a.map((c) => c / n); };
const near = (a, b, eps = 1e-9) => {
  if (Array.isArray(a)) { a.forEach((x, i) => near(x, b[i], eps)); return; }
  assert.ok(Math.abs(a - b) <= eps, `${a} is not ${b}`);
};
/** The camera frame looking along `dir` (toward the camera) with `up` up. */
const frameOf = (dir, up) => {
  const back = unit(dir);
  const right = unit(cross(up, back));
  return { right, up: cross(back, right), back };
};
/** Turn a frame by `deg` about the axis `axis` (Rodrigues). */
const turned = (frame, axis, deg) => {
  const k = unit(axis);
  const t = (deg * Math.PI) / 180;
  const rot = (v) => {
    const c = cross(k, v);
    const d = dot(k, v);
    return v.map((x, i) => x * Math.cos(t) + c[i] * Math.sin(t) + k[i] * d * (1 - Math.cos(t)));
  };
  return { right: rot(frame.right), up: rot(frame.up), back: rot(frame.back) };
};
/** The 3 x 3 part of a column-major 4 x 4, as rows. */
const rows3 = (m) => [[m[0], m[4], m[8]], [m[1], m[5], m[9]], [m[2], m[6], m[10]]];
const mul3 = (a, b) => a.map((row) => [0, 1, 2].map((j) => row[0] * b[0][j] + row[1] * b[1][j] + row[2] * b[2][j]));
const det3 = (m) => dot(m[0], cross(m[1], m[2]));
const apply3 = (m, v) => m.map((row) => dot(row, v));

// The toolbar's viewpoints, read from the renderer's own table so the two cannot drift.
const VIEWPOINTS = Object.fromEntries([...readFileSync(path.join(jsDir, 'render3d.js'), 'utf8')
  .matchAll(/^\s+(xy1|xy2|xz1|xz2|yz1|yz2): \{ dir: \[([^\]]+)\], up: \[([^\]]+)\] \}/gm)]
  .map((m) => [m[1], { dir: m[2].split(',').map(Number), up: m[3].split(',').map(Number) }]));

// -- the parts and what they are called ------------------------------------------

test('26 parts: six faces, twelve edges, eight corners', () => {
  assert.equal(REGIONS.length, 26);
  const count = (kind) => REGIONS.filter((d) => regionKind(d) === kind).length;
  assert.deepEqual([count('face'), count('edge'), count('corner')], [6, 12, 8]);
});

test('labels name the volume\'s axes, and Flip Z turns the Z sign round', () => {
  assert.equal(volumeLabel([0, 0, 1]), '+Z');
  assert.equal(volumeLabel([1, -1, 1]), '+X −Y +Z');
  assert.equal(volumeLabel([0, 0, 1], true), '−Z');
  assert.equal(volumeLabel([-1, 0, -1], true), '−X +Z');
  assert.equal(volumeLabel([1, 1, 0], true), '+X +Y');
});

// -- the views they turn to --------------------------------------------------------

test('the renderer\'s six axis viewpoints were found', () => {
  assert.deepEqual(Object.keys(VIEWPOINTS).sort(), ['xy1', 'xy2', 'xz1', 'xz2', 'yz1', 'yz2']);
});

test('a face turns to its toolbar viewpoint exactly, direction and up', () => {
  for (const face of FACES) {
    const view = regionView(face.n);
    const button = VIEWPOINTS[face.button];
    assert.deepEqual(view.dir, button.dir, face.id);
    assert.deepEqual(view.up, button.up, face.id);
    assert.equal(buttonFor(face.n), face.button);
  }
  assert.equal(buttonFor([1, 1, 0]), null);
});

test('an edge is the 45 degree view between its two faces, upright', () => {
  const v = regionView([1, 0, 1]);
  near(v.dir, [Math.SQRT1_2, 0, Math.SQRT1_2]);
  near(v.up, [-Math.SQRT1_2, 0, Math.SQRT1_2]);
  const side = regionView([1, -1, 0]);
  near(side.dir, [Math.SQRT1_2, -Math.SQRT1_2, 0]);
  assert.deepEqual(side.up, [0, 0, 1]);
});

test('every edge and corner view keeps Z up as far as it can', () => {
  for (const d of REGIONS.filter((r) => regionKind(r) !== 'face')) {
    const { dir, up } = regionView(d);
    near(dot(dir, up), 0, 1e-12);
    near(Math.hypot(...up), 1, 1e-12);
    // Z's part across the view, normalized, is the up.
    const across = unit([0, 0, 1].map((c, i) => c - dir[2] * dir[i]));
    near(up, across, 1e-12);
  }
  near(regionView([1, 1, 1]).dir[2], 1 / Math.sqrt(3));
});

// -- drawing -----------------------------------------------------------------------

test('the cube\'s matrix puts the screen\'s right at +x and its up at -y (CSS y runs down)', () => {
  const frame = frameOf([1, 2, 3], [0, 0, 1]);
  const m = rows3(cubeMatrix(frame));
  near(apply3(m, frame.right), [1, 0, 0], 1e-12);
  near(apply3(m, frame.up), [0, -1, 0], 1e-12);
  near(apply3(m, frame.back), [0, 0, 1], 1e-12);
});

test('no face\'s text is mirrored, from any view, with or without Flip Z', () => {
  const frames = REGIONS.map((d) => regionView(d)).map((v) => frameOf(v.dir, v.up));
  frames.push(turned(frameOf([0.3, -0.8, 0.5], [0, 0, 1]), [0.2, 0.9, 0.1], 73));
  for (const frame of frames) {
    for (const face of FACES) {
      const composite = mul3(rows3(cubeMatrix(frame)), rows3(faceMatrix(face)));
      near(det3(composite), 1, 1e-9);
    }
  }
});

test('looking at a face from its viewpoint, its text runs left to right and top to bottom', () => {
  for (const face of FACES) {
    const { dir, up } = regionView(face.n);
    const m = rows3(cubeMatrix(frameOf(dir, up)));
    near(apply3(m, face.u), [1, 0, 0], 1e-12);
    near(apply3(m, face.v), [0, 1, 0], 1e-12);
    near(apply3(m, face.n), [0, 0, 1], 1e-12);
  }
});

test('a face sits half the cube out along its normal', () => {
  const m = faceMatrix(FACES.find((f) => f.id === 'pz'));
  assert.deepEqual(m.slice(12), [0, 0, HALF, 1]);
  assert.match(cssMatrix(m), /^matrix3d\(1,0,0,0,0,-1,0,0,0,0,1,0,0,0,32,1\)$/);
});

// -- what the pointer is over ------------------------------------------------------

test('every part is under the pointer at the middle of its own view', () => {
  for (const d of REGIONS) {
    const v = regionView(d);
    assert.deepEqual(hitRegion(frameOf(v.dir, v.up), 0, 0), d, volumeLabel(d));
  }
});

test('from XY1, the edges and corners of the +Z face lie where the screen says', () => {
  const frame = frameOf([0, 0, 1], [0, 1, 0]);
  const face = HALF * PERSPECTIVE / (PERSPECTIVE - HALF); // the front face, drawn
  assert.deepEqual(hitRegion(frame, 0, 0), [0, 0, 1]);
  assert.deepEqual(hitRegion(frame, 0.9 * face, 0), [1, 0, 1], 'right edge: +X');
  assert.deepEqual(hitRegion(frame, 0, -0.9 * face), [0, 1, 1], 'top edge: +Y (CSS y is down)');
  assert.deepEqual(hitRegion(frame, -0.9 * face, 0.9 * face), [-1, -1, 1], 'bottom-left corner');
  assert.deepEqual(hitRegion(frame, 0.5 * face, 0), [0, 0, 1], 'inside the middle 60 %');
  assert.equal(hitRegion(frame, 1.2 * face, 0), null, 'beside the cube');
});

test('the zone boundary is a fifth of the face in from each edge', () => {
  const frame = frameOf([0, 0, 1], [0, 1, 0]);
  const scale = PERSPECTIVE / (PERSPECTIVE - HALF);
  assert.deepEqual(hitRegion(frame, 0.59 * HALF * scale, 0), [0, 0, 1]);
  assert.deepEqual(hitRegion(frame, 0.61 * HALF * scale, 0), [1, 0, 1]);
});

test('a drawn point and the pointer agree: projecting a face\'s middle hits that face', () => {
  const frame = turned(frameOf([0.5, -0.7, 0.6], [0, 0, 1]), [0, 0, 1], 20);
  for (const face of FACES) {
    if (dot(face.n, frame.back) <= 0.2) continue; // facing away or nearly edge on
    const [x, y] = project(frame, face.n);
    assert.deepEqual(hitRegion(frame, x, y), face.n, face.id);
  }
});

test('without perspective the pointer is cast straight in', () => {
  const frame = frameOf([0, 0, 1], [0, 1, 0]);
  const flat = { perspective: Infinity };
  assert.deepEqual(hitRegion(frame, 0.9 * HALF, 0, flat), [1, 0, 1]);
  assert.equal(hitRegion(frame, 1.01 * HALF, 0, flat), null);
  near(project(frame, [1, 0, 1], flat), [HALF, 0]);
});

test('a part lights its cell on each face it touches, in the face\'s own right and down', () => {
  const byId = Object.fromEntries(FACES.map((f) => [f.id, f]));
  assert.deepEqual(faceCells(byId.pz, [0, 0, 1]), [4]);
  assert.deepEqual(faceCells(byId.pz, [1, 0, 1]), [5], '+X is right on the +Z face');
  assert.deepEqual(faceCells(byId.px, [1, 0, 1]), [1], '+Z is up on the +X face');
  assert.deepEqual(faceCells(byId.nz, [1, 0, 1]), []);
  for (const d of REGIONS) {
    const lit = FACES.filter((f) => faceCells(f, d).length).length;
    assert.equal(lit, { face: 1, edge: 2, corner: 3 }[regionKind(d)], volumeLabel(d));
  }
});

// -- the face controls -------------------------------------------------------------

test('face on: within a degree, snapped to the cube\'s axes; not at 2 degrees or a corner', () => {
  const xy1 = frameOf([0, 0, 1], [0, 1, 0]);
  assert.deepEqual(faceOn(xy1), { dir: [0, 0, 1], up: [0, 1, 0], right: [1, 0, 0] });
  const tipped = turned(xy1, [1, 0, 0], 0.6);
  assert.deepEqual(faceOn(tipped).dir, [0, 0, 1]);
  assert.equal(faceOn(turned(xy1, [1, 0, 0], 2)), null);
  const corner = regionView([1, 1, 1]);
  assert.equal(faceOn(frameOf(corner.dir, corner.up)), null);
  // Rolled 30 degrees about the view, the up snaps back to the nearest axis.
  assert.deepEqual(faceOn(turned(xy1, [0, 0, 1], 30)).up, [0, 1, 0]);
  assert.deepEqual(faceOn(turned(xy1, [0, 0, 1], 60)).up, [-1, 0, 0]);
});

test('from XY1 the triangles bring each neighbor round, and below is XZ1', () => {
  const state = faceOn(frameOf([0, 0, 1], [0, 1, 0]));
  assert.deepEqual(quarterTurn(state, 'down'), VIEWPOINTS.xz1);
  assert.deepEqual(quarterTurn(state, 'up'), { dir: [0, 1, 0], up: [0, 0, -1] });
  assert.deepEqual(quarterTurn(state, 'left'), { dir: [-1, 0, 0], up: [0, 1, 0] });
  assert.deepEqual(quarterTurn(state, 'right'), { dir: [1, 0, 0], up: [0, 1, 0] });
});

test('a triangle\'s turn brings the face that was on that side of the screen to the front', () => {
  const start = frameOf([0, -1, 0], [0, 0, 1]); // XZ1
  const state = faceOn(start);
  const sides = { up: start.up, down: start.up.map((c) => -c), right: start.right, left: start.right.map((c) => -c) };
  for (const [which, side] of Object.entries(sides)) {
    const to = quarterTurn(state, which);
    near(to.dir, side);
    near(dot(to.dir, to.up), 0);
    // The face that was in front is now on the opposite side of the screen.
    const after = frameOf(to.dir, to.up);
    const opposite = { up: after.up.map((c) => -c), down: after.up, right: after.right.map((c) => -c), left: after.right }[which];
    near(opposite, state.dir, 1e-12);
  }
});

test('the curved arrows turn the picture a quarter; four of one are a full turn', () => {
  const start = faceOn(frameOf([0, 0, 1], [0, 1, 0]));
  const cw = quarterTurn(start, 'cw');
  // Clockwise: +X, on the right, goes to the bottom, so up is -X.
  assert.deepEqual(cw, { dir: [0, 0, 1], up: [-1, 0, 0] });
  assert.deepEqual(quarterTurn(start, 'ccw'), { dir: [0, 0, 1], up: [1, 0, 0] });
  let state = start;
  for (let i = 0; i < 4; i += 1) state = faceOn(frameOf(...Object.values(quarterTurn(state, 'cw'))));
  assert.deepEqual(state, start);
  const back = faceOn(frameOf(...Object.values(quarterTurn(start, 'left'))));
  assert.deepEqual(quarterTurn(back, 'right'), { dir: start.dir, up: start.up });
});

// -- the axis tripod -----------------------------------------------------------------

test('the tripod starts at the volume\'s origin corner and mirrors with Flip Z', () => {
  const [x, , z] = tripod(false);
  assert.deepEqual(x.dir, [1, 0, 0]);
  assert.deepEqual(z.dir, [0, 0, 1]);
  assert.ok(z.start[2] === -1 && x.start[2] < -1, 'below the cube, just outside the faces');
  const flipped = tripod(true);
  assert.deepEqual(flipped[2].dir, [0, 0, -1], 'the volume\'s +Z runs down');
  assert.ok(flipped[2].start[2] === 1 && flipped[0].start[2] > 1, 'from the top corner');
  for (const axis of tripod(true)) {
    for (const across of axis.across) near(dot(across, axis.dir), 0);
    near(axis.tip, axis.start.map((c, i) => c + axis.dir[i] * (axis.length + 0.2)), 1e-12);
  }
});

test('an axis letter behind the cube is hidden, one beside or in front of it is not', () => {
  const v = regionView([1, -1, 1]); // from +X, -Y, +Z: the volume's Y runs away behind the cube
  const frame = frameOf(v.dir, v.up);
  const [x, y, z] = tripod(false);
  assert.equal(behindCube(frame, y.tip), true, 'Y');
  assert.equal(behindCube(frame, x.tip), false, 'X');
  assert.equal(behindCube(frame, z.tip), false, 'Z');
  // From the far corner it is the other way round for Y.
  const far = regionView([-1, 1, -1]);
  assert.equal(behindCube(frameOf(far.dir, far.up), y.tip), false);
  // A point straight behind the middle is hidden; one in front of the cube is not.
  const xy1 = frameOf([0, 0, 1], [0, 1, 0]);
  assert.equal(behindCube(xy1, [0, 0, -1.5]), true);
  assert.equal(behindCube(xy1, [0, 0, 1.5]), false);
  assert.equal(behindCube(xy1, [0, 0, -1.5], { perspective: Infinity }), true);
  assert.equal(behindCube(xy1, [1.5, 0, -1.5], { perspective: Infinity }), false);
});

test('the axis letters and the cube stay on the stage from every direction and roll', () => {
  // A fixed sweep: 24 x 12 directions, 8 rolls each, both Flip Z states.
  let reach = 0;
  let cube = 0;
  for (let a = 0; a < 24; a += 1) {
    for (let e = 0; e < 12; e += 1) {
      const az = (a / 24) * 2 * Math.PI;
      const el = -Math.PI / 2 + ((e + 0.5) / 12) * Math.PI;
      const dir = [Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el)];
      for (let r = 0; r < 8; r += 1) {
        const frame = turned(frameOf(dir, Math.abs(dir[2]) > 0.99 ? [0, 1, 0] : [0, 0, 1]), dir, r * 45);
        for (const flip of [false, true]) {
          for (const axis of tripod(flip)) {
            if (behindCube(frame, axis.tip)) continue;
            reach = Math.max(reach, Math.hypot(...project(frame, axis.tip)));
          }
        }
        for (const d of REGIONS.filter((x) => regionKind(x) === 'corner')) {
          cube = Math.max(cube, ...project(frame, d).map(Math.abs));
        }
      }
    }
  }
  // A letter is 11 px tall and about 8 wide: its middle at most 69 px out leaves
  // it within the 72 px half-stage and the box's 2 px margin.
  assert.ok(reach <= 69, `a letter's middle reaches ${reach.toFixed(1)} px`);
  assert.ok(reach + 4 <= STAGE / 2 + 2, 'and the letter itself stays on the stage');
  assert.ok(cube < STAGE / 2 - 10, `the cube reaches ${cube.toFixed(1)} px`);
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
