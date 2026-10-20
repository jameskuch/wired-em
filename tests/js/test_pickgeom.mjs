// Picking by tolerance (static/js/pickgeom.js): how near, on the screen, a surface
// or a line passes to the pointer.  Run with
//
//     node tests/js/test_pickgeom.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.  The cases
// build their own cameras: an orthographic one whose pixels are the model's
// x and y, and a pinhole perspective one looking down -z.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  BEHIND, projectPositions, nearestTriangle, nearestSegment, multiply4,
} = await import(pathToFileURL(path.join(jsDir, 'pickgeom.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const SIZE = { width: 400, height: 200 };
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} is not ${b}`);

/** Orthographic: a model point (x, y, z) lands on pixel (x, y). */
function pixelMatrix({ width, height } = SIZE) {
  return [2 / width, 0, 0, 0, 0, -2 / height, 0, 0, 0, 0, 1, 0, -1, 1, 0, 1];
}

/** A pinhole camera at the origin looking down -z, 90 degrees across the height. */
function pinhole({ width, height } = SIZE, near_ = 0.1, far = 1000) {
  const f = 1;
  const aspect = width / height;
  return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near_) / (near_ - far), -1,
    0, 0, (2 * far * near_) / (near_ - far), 0];
}

// -- projection ---------------------------------------------------------------------

test('an orthographic matrix puts a vertex on its own x and y', () => {
  const { sx, sy, sw } = projectPositions([10, 20, 5, 390, 190, -7], pixelMatrix(), 400, 200);
  assert.deepEqual([...sx], [10, 390]);
  assert.deepEqual([...sy], [20, 190]);
  assert.deepEqual([...sw], [1, 1], 'w is 1 for an orthographic camera');
});

test('a perspective matrix foreshortens, and a vertex behind the camera has no place', () => {
  // x = 1 at distance 2 is at ndc 1 / 2 / aspect from the center, in a 400 x 200 view.
  const { sx, sy, sw } = projectPositions([1, 0, -2, 1, 0, 2], pinhole(), 400, 200);
  near(sx[0], 200 + (0.5 / 2) * 200);
  near(sy[0], 100);
  near(sw[0], 2);
  assert.ok(sw[1] <= BEHIND, 'behind the camera: w <= 0');
});

test('multiply4 composes the way a column-major matrix product does', () => {
  const translate = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, -5, 1];
  const both = multiply4(pinhole(), translate);
  const a = projectPositions([0, 0, 3], both, 400, 200);
  const b = projectPositions([0, 0, -2], pinhole(), 400, 200);
  near(a.sw[0], b.sw[0]);
});

// -- triangles ----------------------------------------------------------------------

const TRI = [100, 100, 0, 140, 100, 0, 100, 140, 0];

test('a pointer inside a triangle is at distance 0, with the weights of where it is', () => {
  const hit = nearestTriangle(TRI, null, pixelMatrix(), SIZE, [110, 110], 4);
  assert.equal(hit.distance, 0);
  assert.deepEqual(hit.triangle, [0, 1, 2]);
  near(hit.weights.reduce((s, w) => s + w, 0), 1);
  near(hit.local[0], 110);
  near(hit.local[1], 110);
});

test('beyond the reach there is nothing, within it the distance is the distance on the screen', () => {
  // 3 pixels left of the vertical edge x = 100.
  const hit = nearestTriangle(TRI, null, pixelMatrix(), SIZE, [97, 120], 4);
  near(hit.distance, 3);
  near(hit.local[0], 100);
  near(hit.local[1], 120);
  assert.equal(nearestTriangle(TRI, null, pixelMatrix(), SIZE, [97, 120], 2), null);
  // Past a corner the distance is to the corner, not to the line its edges lie on.
  const corner = nearestTriangle(TRI, null, pixelMatrix(), SIZE, [96, 96], 8);
  near(corner.distance, Math.hypot(4, 4));
  near(corner.local[0], 100);
  near(corner.local[1], 100);
});

test('a thin triangle seen edge-on is as near as its edge is', () => {
  // Three collinear points: no inside at all.
  const sliver = [50, 50, 0, 90, 50, 0, 70, 50, 0];
  const hit = nearestTriangle(sliver, null, pixelMatrix(), SIZE, [70, 53], 4);
  near(hit.distance, 3);
});

test('an indexed mesh is read through its index, and shares its vertices', () => {
  const positions = [0, 0, 0, 40, 0, 0, 40, 40, 0, 0, 40, 0, 200, 100, 0, 240, 100, 0, 220, 140, 0];
  const index = [0, 1, 2, 0, 2, 3, 4, 5, 6];
  const second = nearestTriangle(positions, index, pixelMatrix(), SIZE, [220, 110], 4);
  assert.deepEqual(second.triangle, [4, 5, 6]);
  const quad = nearestTriangle(positions, index, pixelMatrix(), SIZE, [10, 30], 4);
  assert.deepEqual(quad.triangle, [0, 2, 3]);
  assert.equal(quad.distance, 0);
});

test('of two triangles over the pointer, the one nearer the camera is found', () => {
  // Two squares in perspective, at distance 5 and 8, both covering the screen center.
  const quad = (z) => [-1, -1, z, 1, -1, z, 1, 1, z, -1, -1, z, 1, 1, z, -1, 1, z];
  const positions = [...quad(-8), ...quad(-5)];
  const hit = nearestTriangle(positions, null, pinhole(), SIZE, [200, 100], 4);
  assert.equal(hit.distance, 0);
  near(hit.depth, 5);
  near(hit.local[2], -5);
  // And the other way round in the buffer: the order is not what decides it.
  const flipped = [...quad(-5), ...quad(-8)];
  near(nearestTriangle(flipped, null, pinhole(), SIZE, [200, 100], 4).depth, 5);
});

test('a perspective point is lifted by 1/w, not by the screen weights', () => {
  // A plane tilted away from the camera: its far edge is twice as far as its near edge.
  // The pixel halfway between the two edges on screen is not halfway between them in depth.
  const positions = [-1, -1, -2, 1, -1, -2, -2, 1, -4, 2, 1, -4, -2, 1, -4, 1, -1, -2];
  const hit = nearestTriangle(positions, null, pinhole(), SIZE, [200, 100], 4);
  assert.equal(hit.distance, 0);
  // The ray through the middle of the screen is the z axis: it meets this plane at z = -3.
  near(hit.local[0], 0, 1e-5);
  near(hit.local[2], -3, 1e-5);
  near(hit.depth, 3, 1e-5);
});

test('a triangle behind the camera, or half behind it, is left alone', () => {
  const behind = [-1, -1, 2, 1, -1, 2, 0, 1, 2];
  assert.equal(nearestTriangle(behind, null, pinhole(), SIZE, [200, 100], 40), null);
  const across = [-1, -1, -2, 1, -1, -2, 0, 1, 2];
  assert.equal(nearestTriangle(across, null, pinhole(), SIZE, [200, 100], 40), null);
});

test('a triangle the caller turns down is passed over for the next one', () => {
  const quad = (z) => [-1, -1, z, 1, -1, z, 1, 1, z, -1, -1, z, 1, 1, z, -1, 1, z];
  const positions = [...quad(-8), ...quad(-5)];
  const hit = nearestTriangle(positions, null, pinhole(), SIZE, [200, 100], 4,
    { accept: (local) => local[2] < -6 });   // the near one is cropped away
  near(hit.depth, 8);
  assert.equal(nearestTriangle(positions, null, pinhole(), SIZE, [200, 100], 4,
    { accept: () => false }), null);
});

test('depthOf replaces the clip-space w, which an orthographic camera does not have', () => {
  // Two overlapping triangles, the second nearer by the caller's own measure (smaller z).
  const positions = [100, 100, 5, 140, 100, 5, 100, 140, 5, 100, 100, 1, 140, 100, 1, 100, 140, 1];
  const plain = nearestTriangle(positions, null, pixelMatrix(), SIZE, [110, 110], 4);
  assert.equal(plain.depth, 1, 'w is 1 whichever is nearer');
  const hit = nearestTriangle(positions, null, pixelMatrix(), SIZE, [110, 110], 4,
    { depthOf: (local) => local[2] });
  assert.deepEqual(hit.triangle, [3, 4, 5]);
  assert.equal(hit.depth, 1);
});

test('nothing in, nothing out', () => {
  assert.equal(nearestTriangle([], null, pixelMatrix(), SIZE, [10, 10], 4), null);
  assert.equal(nearestTriangle([], [], pixelMatrix(), SIZE, [10, 10], 4), null);
  assert.equal(nearestSegment([], pixelMatrix(), SIZE, [10, 10], 4), null);
});

// -- segments -----------------------------------------------------------------------

const LINES = [10, 10, 0, 110, 10, 0, 10, 50, 0, 10, 150, 0];

test('the nearest segment, how far along it, and where', () => {
  const hit = nearestSegment(LINES, pixelMatrix(), SIZE, [60, 14], 6);
  assert.equal(hit.segment, 0);
  near(hit.distance, 4);
  near(hit.t, 0.5);
  near(hit.local[0], 60);
  near(hit.local[1], 10);
  const other = nearestSegment(LINES, pixelMatrix(), SIZE, [12, 100], 6);
  assert.equal(other.segment, 1);
  near(other.t, 0.5);
});

test('past the end of a segment the distance is to the end', () => {
  const hit = nearestSegment(LINES, pixelMatrix(), SIZE, [114, 13], 8);
  near(hit.distance, 5);
  near(hit.t, 1);
});

test('only the segments in the draw range are there to be pointed at', () => {
  assert.equal(nearestSegment(LINES, pixelMatrix(), SIZE, [60, 10], 6, { from: 1 }), null);
  assert.equal(nearestSegment(LINES, pixelMatrix(), SIZE, [60, 10], 6, { from: 0, to: 1 }).segment, 0);
});

test('a segment the caller turns down is passed over', () => {
  const crossing = [10, 10, 0, 110, 10, 0, 10, 10, 0, 10, 80, 0];
  const hit = nearestSegment(crossing, pixelMatrix(), SIZE, [10, 10], 4,
    { accept: (local) => local[0] < 11 && local[1] > 11 });
  assert.equal(hit, null, 'both pass within reach, neither is accepted at its nearest point');
  const first = nearestSegment(crossing, pixelMatrix(), SIZE, [12, 10], 4,
    { accept: (local) => local[0] > 11 });
  assert.equal(first.segment, 0);
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
