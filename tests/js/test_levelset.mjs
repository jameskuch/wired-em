// Level sets on triangle meshes (static/js/levelset.js), checked on shapes whose
// contours are known: a slice of a sphere is a circle, a slice of a torus two
// circles, a linear field's zero line a straight line, a cap lying exactly on the
// level a closed loop.  Run with
//
//     node tests/js/test_levelset.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free, no DOM.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const L = await import(pathToFileURL(path.join(jsDir, 'levelset.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, tol = 1e-9, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} is not within ${tol} of ${b}`);

// -- shapes ---------------------------------------------------------------------------------

/** A UV sphere: `nu` meridians, `nv` rings between the poles.  `seam` repeats the vertices of the first meridian
 * (as a mesh made from a texture-mapped grid does), so the surface is closed but its indices are not. */
function uvSphere(R, nu, nv, { center = [0, 0, 0], seam = false } = {}) {
  const vertices = [];
  const at = (x, y, z) => vertices.push(center[0] + x, center[1] + y, center[2] + z);
  at(0, 0, R);                                   // north pole: 0
  for (let j = 1; j < nv; j += 1) {
    const phi = (Math.PI * j) / nv;
    const columns = seam ? nu + 1 : nu;
    for (let i = 0; i < columns; i += 1) {
      const th = (2 * Math.PI * i) / nu;
      at(R * Math.sin(phi) * Math.cos(th), R * Math.sin(phi) * Math.sin(th), R * Math.cos(phi));
    }
  }
  at(0, 0, -R);                                  // south pole
  const south = vertices.length / 3 - 1;
  const cols = seam ? nu + 1 : nu;
  const ring = (j, i) => 1 + (j - 1) * cols + (seam ? i : i % nu);
  const faces = [];
  for (let i = 0; i < nu; i += 1) {
    faces.push(0, ring(1, i), ring(1, i + 1));
    faces.push(south, ring(nv - 1, i + 1), ring(nv - 1, i));
  }
  for (let j = 1; j < nv - 1; j += 1) {
    for (let i = 0; i < nu; i += 1) {
      faces.push(ring(j, i), ring(j + 1, i), ring(j + 1, i + 1));
      faces.push(ring(j, i), ring(j + 1, i + 1), ring(j, i + 1));
    }
  }
  return { vertices, faces };
}

/** A torus around the Z axis: major radius R, minor r. */
function torus(R, r, nu, nv) {
  const vertices = [];
  for (let i = 0; i < nu; i += 1) {
    const u = (2 * Math.PI * i) / nu;
    for (let j = 0; j < nv; j += 1) {
      const v = (2 * Math.PI * j) / nv;
      vertices.push((R + r * Math.cos(v)) * Math.cos(u), (R + r * Math.cos(v)) * Math.sin(u), r * Math.sin(v));
    }
  }
  const faces = [];
  const id = (i, j) => (i % nu) * nv + (j % nv);
  for (let i = 0; i < nu; i += 1) {
    for (let j = 0; j < nv; j += 1) {
      faces.push(id(i, j), id(i + 1, j), id(i + 1, j + 1));
      faces.push(id(i, j), id(i + 1, j + 1), id(i, j + 1));
    }
  }
  return { vertices, faces };
}

/** A closed cylinder of radius R from z = 0 to z = h, `n` sides, `rows` bands, each cap a fan about its center. */
function cylinder(R, h, n, rows = 1) {
  const vertices = [];
  for (let k = 0; k <= rows; k += 1) {
    for (let i = 0; i < n; i += 1) {
      const th = (2 * Math.PI * i) / n;
      vertices.push(R * Math.cos(th), R * Math.sin(th), (h * k) / rows);
    }
  }
  const bottom = vertices.length / 3;
  vertices.push(0, 0, 0);
  const top = vertices.length / 3;
  vertices.push(0, 0, h);
  const faces = [];
  for (let k = 0; k < rows; k += 1) {
    for (let i = 0; i < n; i += 1) {
      const a = k * n + i;
      const b = k * n + ((i + 1) % n);
      faces.push(a, b, a + n, b, b + n, a + n);
    }
  }
  for (let i = 0; i < n; i += 1) {
    faces.push(bottom, (i + 1) % n, i);
    faces.push(top, rows * n + i, rows * n + ((i + 1) % n));
  }
  return { vertices, faces };
}

/** A flat grid over [0, w] x [0, h] in the plane z = 0. */
function grid(w, h, nx, ny) {
  const vertices = [];
  for (let i = 0; i <= nx; i += 1) for (let j = 0; j <= ny; j += 1) vertices.push((w * i) / nx, (h * j) / ny, 0);
  const faces = [];
  for (let i = 0; i < nx; i += 1) {
    for (let j = 0; j < ny; j += 1) {
      const a = i * (ny + 1) + j;
      faces.push(a, a + ny + 1, a + ny + 2, a, a + ny + 2, a + 1);
    }
  }
  return { vertices, faces };
}

const coordOf = (mesh, axis) => Float64Array.from({ length: mesh.vertices.length / 3 }, (_, i) => mesh.vertices[3 * i + axis]);
const radiiAbout = (points, cx, cy) => Array.from({ length: points.length / 3 }, (_, k) => Math.hypot(points[3 * k] - cx, points[3 * k + 1] - cy));
const bySize = (loops) => loops.slice().sort((a, b) => b.points.length - a.points.length);

// -- the slice contour ---------------------------------------------------------------------------

test('a sphere sliced by a plane is one closed circle of the right radius, at the plane\'s height', () => {
  const R = 5;
  const sphere = uvSphere(R, 96, 48);
  const index = L.buildSliceIndex(sphere.vertices, sphere.faces);
  for (const z of [0, 1.3, -2.2, 4.7]) {
    const set = L.sliceContour(index, z);
    const loops = L.contourLoops(set);
    assert.equal(loops.length, 1, `z ${z}`);
    assert.equal(loops[0].closed, true);
    const expected = Math.sqrt(R * R - z * z);
    const radii = radiiAbout(loops[0].points, 0, 0);
    // The polyhedral sphere lies inside the true one: within 1 % of the radius, never outside it.
    assert.ok(Math.max(...radii) <= expected + 1e-9, `z ${z} outside`);
    assert.ok(Math.min(...radii) >= expected * 0.99, `z ${z} radius ${Math.min(...radii)} of ${expected}`);
    for (let k = 2; k < loops[0].points.length; k += 3) close(loops[0].points[k], z, 1e-9, 'height');
    close(L.loopLength(loops[0]), 2 * Math.PI * expected, 0.01 * 2 * Math.PI * expected, 'circumference');
    assert.equal(loops[0].points.length / 3, set.count, 'a closed loop has as many points as segments');
  }
  // Above the pole and below the south pole nothing is cut.
  assert.equal(L.sliceContour(index, 5.0001).count, 0);
  assert.equal(L.sliceContour(index, -5.5).count, 0);
  assert.deepEqual(L.contourLoops(L.sliceContour(index, 9)), []);
});

test('a torus is cut into two circles, the outer and the inner, and a sphere beside it into a third', () => {
  const R = 4; const r = 1;
  const t = torus(R, r, 120, 48);
  const index = L.buildSliceIndex(t.vertices, t.faces);
  for (const [z, outer, inner] of [[0, R + r, R - r], [0.5, R + Math.sqrt(r * r - 0.25), R - Math.sqrt(r * r - 0.25)]]) {
    const loops = L.contourLoops(L.sliceContour(index, z));
    assert.equal(loops.length, 2, `z ${z}`);
    assert.ok(loops.every((loop) => loop.closed));
    const means = loops.map((loop) => radiiAbout(loop.points, 0, 0).reduce((a, b) => a + b, 0) / (loop.points.length / 3)).sort((a, b) => b - a);
    close(means[0], outer, 0.01 * outer, `outer at ${z}`);
    close(means[1], inner, 0.01 * outer, `inner at ${z}`);
  }
  // The tube sliced through the hole's axis plane: a plane through the torus's axis is not a Z slice, so cut the tube's top.
  assert.equal(L.contourLoops(L.sliceContour(index, r + 0.01)).length, 0);
  // Two bodies in one mesh: both are cut.
  const a = uvSphere(2, 48, 24, { center: [10, 0, 0] });
  const merged = { vertices: [...t.vertices, ...a.vertices], faces: [...t.faces, ...a.faces.map((i) => i + t.vertices.length / 3)] };
  const both = L.contourLoops(L.sliceContour(L.buildSliceIndex(merged.vertices, merged.faces), 0));
  assert.equal(both.length, 3);
  assert.deepEqual(both.map((loop) => loop.closed), [true, true, true]);
});

test('the face index gives exactly the full scan\'s segments, in the same order, at any bin height and any level', () => {
  const t = torus(4, 1, 90, 40);
  const z = coordOf(t, 2);
  const levels = [0, 0.25, -0.7071067811865476, 0.9999, 1, -1, 1.0000001, 0.123456789];
  for (const binUm of [null, 0.01, 0.37, 50]) {
    const index = L.buildSliceIndex(t.vertices, t.faces, { binUm });
    // Levels on the bin boundaries too: the first edge exactly, and one more.
    for (const level of [...levels, index.min, index.min + index.bin, index.min + 3 * index.bin]) {
      const binned = L.sliceContour(index, level);
      const full = L.levelSet(t.vertices, t.faces, z, level);
      assert.equal(binned.count, full.count, `bin ${binUm} level ${level}`);
      assert.deepEqual(binned.positions, full.positions);
      assert.deepEqual(binned.segments, full.segments);
    }
  }
  // Any axis: a cut at x = 0.3 of the same torus.
  const x = coordOf(t, 0);
  const byX = L.buildSliceIndex(t.vertices, t.faces, { axis: 0 });
  assert.deepEqual(L.sliceContour(byX, 0.3).positions, L.levelSet(t.vertices, t.faces, x, 0.3).positions);
  assert.ok(L.sliceContour(byX, 0.3).count > 0);
});

test('a cap lying exactly on the level is cut along its rim: a closed loop, no nudging, no zero-length pieces', () => {
  const n = 40; const R = 3;
  const cyl = cylinder(R, 2, n, 4);                 // rings at z = 0, 0.5, 1, 1.5, 2: every one exactly on a level
  const index = L.buildSliceIndex(cyl.vertices, cyl.faces);
  for (const z of [0.5, 1, 1.5, 2]) {
    const set = L.sliceContour(index, z);
    const loops = L.contourLoops(set);
    assert.equal(loops.length, 1, `z ${z}`);
    assert.equal(loops[0].closed, true);
    assert.equal(loops[0].points.length / 3, n, 'the ring\'s own n vertices');
    for (const r of radiiAbout(loops[0].points, 0, 0)) close(r, R, 1e-9, 'rim radius');
    // Not one segment of no length.
    for (let s = 0; s < set.count; s += 1) {
      const p = set.positions;
      assert.ok(Math.hypot(p[6 * s + 3] - p[6 * s], p[6 * s + 4] - p[6 * s + 1], p[6 * s + 5] - p[6 * s + 2]) > 0);
    }
  }
  // Just above the top cap there is nothing; just below, the same loop (the sides are vertical).
  assert.equal(L.sliceContour(index, 2 + 1e-9).count, 0);
  const below = L.contourLoops(L.sliceContour(index, 2 - 1e-9));
  assert.equal(below.length, 1); assert.ok(below[0].closed);
  // The very bottom: the cap's own plane.  The rule "at or above is positive" puts the bottom cap
  // and the whole wall on one side, so the DEFAULT rule gives no contour at the lowest vertex.
  // That is the rule's documented price (the top cap is cut, the bottom one is not); `side` and
  // `sectionContour` below are the way round it.
  const bottom = L.contourLoops(L.sliceContour(index, 0));
  assert.equal(bottom.length, 0, 'at the bottom cap everything is on or above the level: no boundary');
});

test('side "below" cuts the bottom cap along its rim and leaves the top cap empty: the two ends of a stack, pinned', () => {
  const n = 40; const R = 3;
  const cyl = cylinder(R, 2, n, 4);                 // z = 0 (bottom cap), 0.5, 1, 1.5, 2 (top cap)
  const index = L.buildSliceIndex(cyl.vertices, cyl.faces);
  const count = (z, side) => L.sliceContour(index, z, { side }).count;
  const loopsOf = (z, side) => L.contourLoops(L.sliceContour(index, z, { side }));
  // Top end: the default cuts the cap along its rim, the strict rule has the cap and the wall below it on one side.
  assert.equal(loopsOf(2, 'above').length, 1);
  assert.equal(count(2, 'below'), 0);
  // Bottom end: the other way round.
  assert.equal(count(0, 'above'), 0);
  const bottom = loopsOf(0, 'below');
  assert.equal(bottom.length, 1); assert.equal(bottom[0].closed, true);
  assert.equal(bottom[0].points.length / 3, n, 'the ring\'s own n vertices');
  for (const r of radiiAbout(bottom[0].points, 0, 0)) close(r, R, 1e-9, 'rim radius');
  for (let k = 0; k < bottom[0].points.length / 3; k += 1) close(bottom[0].points[3 * k + 2], 0, 1e-12, 'in the cap\'s plane');
  // Between the ends a ring of vertices on the level is cut the same way by either rule (the surface continues
  // on both sides, so the boundary between "at or above" and "below" is the ring either way).
  for (const z of [0.5, 1, 1.5]) {
    const a = L.sliceContour(index, z, { side: 'above' }); const b = L.sliceContour(index, z, { side: 'below' });
    assert.equal(a.count, b.count, `z ${z}`);
    const lengths = [a, b].map((set) => L.loopLength(L.contourLoops(set)[0]));
    close(lengths[0], lengths[1], 1e-9, `loop length at ${z}`);
  }
  // Off every vertex the rule is irrelevant: the same segments, in the same order.
  for (const z of [0.1, 0.77, 1.9, -1, 3]) {
    assert.deepEqual(L.sliceContour(index, z, { side: 'below' }).positions, L.sliceContour(index, z).positions, `z ${z}`);
  }
  // The full scan takes the option too, and an unknown rule is refused rather than guessed at.
  const z = Float64Array.from({ length: cyl.vertices.length / 3 }, (_, i) => cyl.vertices[3 * i + 2]);
  assert.equal(L.levelSet(cyl.vertices, cyl.faces, z, 0, { side: 'below' }).count, n);
  assert.equal(L.levelSet(cyl.vertices, cyl.faces, z, 0).count, 0);
  assert.throws(() => L.levelSet(cyl.vertices, cyl.faces, z, 0, { side: 'under' }), RangeError);
});

test('a section\'s outline at either end of a stack: sectionContour takes the rule that keeps the cap', () => {
  const n = 40; const R = 3;
  const cyl = cylinder(R, 2, n, 4);
  const index = L.buildSliceIndex(cyl.vertices, cyl.faces);
  assert.equal(index.min, 0);
  for (const z of [0, 0.5, 1, 1.5, 2]) {
    const loops = L.contourLoops(L.sectionContour(index, z));
    assert.equal(loops.length, 1, `z ${z}: one outline`);
    assert.equal(loops[0].closed, true);
    assert.equal(loops[0].points.length / 3, n, `z ${z}: the ring`);
    close(L.loopLength(loops[0]), 2 * n * R * Math.sin(Math.PI / n), 1e-9, `z ${z}: the ring's perimeter`);
  }
  // Everywhere else it is sliceContour, segment for segment; outside the surface there is nothing.
  for (const z of [0.1, 0.77, 1.5, 1.9999, 2]) {
    assert.deepEqual(L.sectionContour(index, z).positions, L.sliceContour(index, z).positions, `z ${z}`);
  }
  for (const z of [-1e-9, 2 + 1e-9, 5, NaN]) assert.equal(L.sectionContour(index, z).count, 0, `z ${z}`);
  // An empty index has no outline.
  assert.equal(L.sectionContour(L.buildSliceIndex([], []), 0).count, 0);
  // The lowest vertex need not be at 0: the rule follows the mesh.
  const lifted = { vertices: cyl.vertices.map((v, i) => (i % 3 === 2 ? v + 100.855 : v)), faces: cyl.faces };
  const high = L.buildSliceIndex(lifted.vertices, lifted.faces);
  close(high.min, 100.855, 1e-12);
  for (const z of [100.855, 102.855]) assert.equal(L.contourLoops(L.sectionContour(high, z)).length, 1, `z ${z}`);
});

/** Meshes joined into one, the way a cell's pieces are: the indices of each are shifted past the ones before it. */
function joined(...meshes) {
  const vertices = []; const faces = [];
  for (const m of meshes) {
    const base = vertices.length / 3;
    for (const v of m.vertices) vertices.push(v);
    for (const f of m.faces) faces.push(f + base);
  }
  return { vertices, faces };
}

const lifted = (mesh, dz) => ({ vertices: mesh.vertices.map((v, i) => (i % 3 === 2 ? v + dz : v)), faces: mesh.faces });

test('sectionContour keeps every cap on its plane, not only the lowest one: two stacks, a bottom cap in the middle', () => {
  // Two closed prisms, z 0..2 and z 5..7, each with a flat cap on its section's plane at both ends: a cell with a
  // second piece that begins at section 5.  The default rule loses the second piece's bottom cap, `side: 'below'`
  // loses the tops, and the surface's lowest vertex (0) is the only height the old rule looked after.
  const n = 24; const R = 2;
  const mesh = joined(cylinder(R, 2, n, 2), lifted(cylinder(R, 2, n, 2), 5));
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  assert.equal(index.min, 0);
  assert.equal(L.sliceContour(index, 5).count, 0, 'the default rule: the second piece\'s bottom cap is empty');
  assert.equal(L.sliceContour(index, 5, { side: 'below' }).count, n, 'the other rule has it');
  assert.equal(L.sliceContour(index, 7, { side: 'below' }).count, 0, '... and not the top');
  for (const z of [0, 1, 2, 5, 6, 7]) {
    const set = L.sectionContour(index, z);
    const loops = L.contourLoops(set);
    assert.equal(set.count, n, `z ${z}: one segment for each side of the ring`);
    assert.equal(loops.length, 1, `z ${z}: one outline`);
    assert.equal(loops[0].closed, true);
    assert.equal(loops[0].points.length / 3, n, `z ${z}: the ring`);
    close(L.loopLength(loops[0]), 2 * n * R * Math.sin(Math.PI / n), 1e-9, `z ${z}: the ring's perimeter`);
    for (const r of radiiAbout(loops[0].points, 0, 0)) close(r, R, 1e-9, `z ${z}: rim radius`);
    for (let k = 2; k < loops[0].points.length; k += 3) close(loops[0].points[k], z, 1e-12, `z ${z}: in the plane`);
  }
  // Between the stacks and off the surface: nothing; between vertex heights it is sliceContour, segment for segment.
  for (const z of [3, 4.5, -1e-9, 7 + 1e-9]) assert.equal(L.sectionContour(index, z).count, 0, `z ${z}`);
  for (const z of [0.3, 1.7, 5.4, 6.9]) {
    assert.deepEqual(L.sectionContour(index, z).positions, L.sliceContour(index, z).positions, `z ${z}`);
    assert.equal(L.contourLoops(L.sectionContour(index, z)).length, 1, `z ${z}`);
  }
  // A ring in the middle of a stack is found by both rules and kept once.
  assert.deepEqual(L.sectionContour(index, 1).positions, L.sliceContour(index, 1).positions);
  assert.deepEqual(L.sectionContour(index, 6).positions, L.sliceContour(index, 6).positions);
  // The buffer the renderer takes is the narrowed positions.
  const set = L.sectionContour(index, 5);
  assert.deepEqual(set.segments, Float32Array.from(set.positions));
  assert.equal(set.level, 5);
});

test('sectionContour: a top cap and a bottom cap on the same ring are one outline, not two on top of each other', () => {
  // A prism ends at z = 2 and the next begins on the same ring (the indices are not shared, as in a cell assembled
  // from pieces): the top cap is cut by the default rule, the bottom cap by the other, and both are the same circle.
  const n = 24; const R = 2;
  const mesh = joined(cylinder(R, 2, n, 1), lifted(cylinder(R, 2, n, 1), 2));
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  assert.equal(L.sliceContour(index, 2).count, n, 'the default rule: the first prism\'s top');
  assert.equal(L.sliceContour(index, 2, { side: 'below' }).count, n, 'the other rule: the second prism\'s bottom');
  const set = L.sectionContour(index, 2);
  assert.equal(set.count, n, 'the same n segments, kept once');
  const loops = L.contourLoops(set);
  assert.equal(loops.length, 1); assert.equal(loops[0].closed, true); assert.equal(loops[0].points.length / 3, n);
  // A narrower prism that also begins on that plane has a ring of its own: a different circle stays a second loop.
  const offset = joined(cylinder(R, 2, n, 1), lifted(cylinder(R, 2, n, 1), 2), lifted(cylinder(0.5 * R, 1, n, 1), 2));
  const inner = L.contourLoops(L.sectionContour(L.buildSliceIndex(offset.vertices, offset.faces), 2));
  assert.deepEqual(inner.map((loop) => loop.points.length / 3), [n, n], 'the narrower prism on the same plane has its own ring');
  assert.ok(inner.every((loop) => loop.closed));
});

test('the face index keeps within its bin budget on a thin, tall surface, and still gives the full scan\'s segments', () => {
  // 3000 small triangles stacked over 3,000,000 um: the mean extent of a face (1 um) would ask for three million
  // bins; the index may have no more than MAX_BINS (and one for the closed end).
  const vertices = []; const faces = [];
  for (let k = 0; k < 3000; k += 1) {
    const z = k * 1000;
    vertices.push(0, 0, z, 1, 0, z + 0.5, 0, 1, z + 1);
    faces.push(3 * k, 3 * k + 1, 3 * k + 2);
  }
  const index = L.buildSliceIndex(vertices, faces);
  assert.ok(index.nBins <= L.MAX_BINS + 1, `${index.nBins} bins`);
  assert.ok(index.bin >= 3_000_000 / L.MAX_BINS * 0.99, `bin ${index.bin}`);
  const z = Float64Array.from({ length: vertices.length / 3 }, (_, i) => vertices[3 * i + 2]);
  for (const level of [0.25, 0.5, 1000.25, 1499000.75, 2_999_000.6, 7]) {
    const binned = L.sliceContour(index, level);
    const full = L.levelSet(vertices, faces, z, level);
    assert.deepEqual(binned.positions, full.positions, `level ${level}`);
  }
  assert.ok(L.sliceContour(index, 1000.25).count === 1);
  // An explicit bin finer than the budget is raised to it.
  assert.ok(L.buildSliceIndex(vertices, faces, { binUm: 0.001 }).nBins <= L.MAX_BINS + 1);
});

test('a surface repeated along a seam (indices not shared) still comes out as one closed loop', () => {
  const welded = uvSphere(5, 64, 32);
  const seamed = uvSphere(5, 64, 32, { seam: true });
  assert.ok(seamed.vertices.length > welded.vertices.length, 'the seam repeats vertices');
  for (const z of [0, 2.2]) {
    const a = L.contourLoops(L.sliceContour(L.buildSliceIndex(welded.vertices, welded.faces), z));
    const b = L.contourLoops(L.sliceContour(L.buildSliceIndex(seamed.vertices, seamed.faces), z));
    assert.equal(b.length, 1, `z ${z}`);
    assert.equal(b[0].closed, true);
    assert.equal(b[0].points.length, a[0].points.length);
    close(L.loopLength(b[0]), L.loopLength(a[0]), 1e-9, 'same loop');
  }
});

test('a vertex a hair off the level (a real mesh\'s rounding) leaves pieces that still join into one curve', () => {
  // A fan of 40 triangles about a center a ten-millionth of a micrometer above the level, on a plane
  // tilted so that half of the ring is above the level and half below.  The
  // contour runs through the center's neighborhood in a fan of pieces, each far shorter than the join
  // tolerance but together longer than it.  Dropping the short pieces cut the curve (c1178, 2026-10-15).
  const n = 40;
  const vertices = [0, 0, 1e-7];
  for (let i = 0; i < n; i += 1) {
    const th = (2 * Math.PI * (i + 0.5)) / n;
    vertices.push(Math.cos(th), Math.sin(th), Math.cos(th));            // z = x on the ring
  }
  const faces = [];
  for (let i = 0; i < n; i += 1) faces.push(0, 1 + i, 1 + ((i + 1) % n));
  const field = Float64Array.from({ length: n + 1 }, (_, i) => vertices[3 * i + 2]);
  const set = L.levelSet(vertices, faces, field, 0);
  const lengths = Array.from({ length: set.count }, (_, s) => Math.hypot(
    set.positions[6 * s + 3] - set.positions[6 * s], set.positions[6 * s + 4] - set.positions[6 * s + 1],
    set.positions[6 * s + 5] - set.positions[6 * s + 2]));
  assert.ok(lengths.some((len) => len < L.JOIN_UM), 'the case has pieces shorter than the join tolerance');
  const loops = L.contourLoops(set);
  assert.equal(loops.length, 1, 'one curve, not pieces');
  assert.equal(loops[0].closed, false);
  close(L.loopLength(loops[0]), 2, 0.02, 'across the disc');
  // The fan of slivers around the vertex is a few points, and nothing is a step of no length.
  const p = loops[0].points;
  for (let k = 0; k + 1 < p.length / 3; k += 1) {
    assert.ok(Math.hypot(p[3 * k + 3] - p[3 * k], p[3 * k + 4] - p[3 * k + 1], p[3 * k + 5] - p[3 * k + 2]) > L.JOIN_UM);
  }
  // Pieces no longer than the rounding of a float64 are dropped outright.
  assert.ok(lengths.every((len) => len >= L.SLIVER_UM));
});

test('segment ends a hair apart are joined wherever they fall, even on either side of a bucket edge', () => {
  // A square whose corners lie on multiples of the join bucket (1.024e-3 um), each end of each side
  // nudged by +-3e-10 um so the two ends of every corner fall on opposite sides of the edge.
  const cell = L.JOIN_UM * 1024;
  const a = 100 * cell; const b = 200 * cell;
  const corners = [[a, a], [b, a], [b, b], [a, b]];
  const nudge = 3e-10;
  const positions = [];
  for (let k = 0; k < 4; k += 1) {
    const [x0, y0] = corners[k]; const [x1, y1] = corners[(k + 1) % 4];
    positions.push(x0 + nudge, y0 + nudge, 7, x1 - nudge, y1 - nudge, 7);
  }
  const set = { level: 7, count: 4, positions: Float64Array.from(positions), segments: Float32Array.from(positions) };
  const loops = L.contourLoops(set);
  assert.equal(loops.length, 1);
  assert.equal(loops[0].closed, true);
  assert.equal(loops[0].points.length / 3, 4);
  // Pulled apart by more than the tolerance they are not joined: four open pieces.
  const apart = positions.slice();
  for (let k = 0; k < 4; k += 1) { apart[6 * k] += 1e-4; apart[6 * k + 1] += 1e-4; }
  const open = L.contourLoops({ ...set, positions: Float64Array.from(apart) });
  assert.equal(open.length, 4);
  assert.ok(open.every((loop) => !loop.closed));
  // A wider tolerance joins them again.
  assert.equal(L.contourLoops({ ...set, positions: Float64Array.from(apart) }, { joinUm: 1e-3 }).length, 1);
});

test('an open surface gives an open curve that ends on its boundary', () => {
  const sphere = uvSphere(5, 64, 32);
  // The northern half: drop the faces that use a vertex below the equator.
  const z = coordOf(sphere, 2);
  const kept = [];
  for (let f = 0; f < sphere.faces.length; f += 3) {
    if ([0, 1, 2].every((c) => z[sphere.faces[f + c]] >= -1e-12)) kept.push(...sphere.faces.slice(f, f + 3));
  }
  // A cut through the side of the cap at x = 1: an arc from rim to rim.
  const x = coordOf(sphere, 0);
  const set = L.levelSet(sphere.vertices, kept, x, 1);
  const loops = L.contourLoops(set);
  assert.equal(loops.length, 1);
  assert.equal(loops[0].closed, false);
  assert.equal(loops[0].points.length / 3, set.count + 1, 'an open curve has one more point than segments');
  const p = loops[0].points;
  const m = p.length / 3;
  for (const end of [0, m - 1]) close(p[3 * end + 2], 0, 0.15, 'ends on the equator');
  for (let k = 0; k < m; k += 1) close(p[3 * k], 1, 1e-9, 'on the plane');
});

// -- what rides along with the segments: another field, and the side the solid is on ---------------------

/** The signed volume of a closed mesh: positive when it is wound outward. */
function volumeOf(mesh) {
  let volume = 0;
  const at = (i, q) => mesh.vertices[3 * i + q];
  for (let f = 0; f < mesh.faces.length; f += 3) {
    const [a, b, c] = [mesh.faces[f], mesh.faces[f + 1], mesh.faces[f + 2]];
    volume += (at(a, 0) * (at(b, 1) * at(c, 2) - at(b, 2) * at(c, 1))
      - at(a, 1) * (at(b, 0) * at(c, 2) - at(b, 2) * at(c, 0))
      + at(a, 2) * (at(b, 0) * at(c, 1) - at(b, 1) * at(c, 0))) / 6;
  }
  return volume;
}

/** The same surface wound the other way: a hole's wall, or a body turned inside out. */
const inside_out = (mesh) => ({
  vertices: mesh.vertices,
  faces: Array.from({ length: mesh.faces.length }, (_, k) => {
    const f = Math.floor(k / 3) * 3;
    return mesh.faces[k % 3 === 0 ? f : k % 3 === 1 ? f + 2 : f + 1];
  }),
});

/** Twice the area (shoelace) of the (u, v) polygon of the points, `stride` numbers each, u and v at 0 and 1 by default. */
const shoelace = (points, stride = 3, u = 0, v = 1) => {
  let sum = 0;
  const m = points.length / stride;
  for (let k = 0; k < m; k += 1) {
    const j = (k + 1) % m;
    sum += points[stride * k + u] * points[stride * j + v] - points[stride * j + u] * points[stride * k + v];
  }
  return sum;
};

/** The loops, the one that encloses the most first (the points are equal in number for the circles of a torus). */
const byArea = (loops) => loops.slice().sort((p, q) => Math.abs(shoelace(q.points)) - Math.abs(shoelace(p.points)));

/** The segments of a set run in the direction their sides say (swapped where the side is -1): returns the pairs of ends. */
function directed(set, u = 0, v = 1) {
  let sum = 0;
  for (let s = 0; s < set.count; s += 1) {
    const p = set.positions;
    const [a, b] = set.orient[s] < 0 ? [3, 0] : [0, 3];
    sum += p[6 * s + a + u] * p[6 * s + b + v] - p[6 * s + b + u] * p[6 * s + a + v];
  }
  return sum;
}

test('a field rides along the segments, exactly for a linear one, and the geometry does not change', () => {
  const sphere = uvSphere(5, 48, 24);
  const z = coordOf(sphere, 2);
  const nv = sphere.vertices.length / 3;
  const values = Float64Array.from({ length: nv }, (_, i) => 2 * sphere.vertices[3 * i] - sphere.vertices[3 * i + 1] + 0.5 * sphere.vertices[3 * i + 2] + 3);
  for (const level of [-3.3, 0.37, 2.2]) {
    const plain = L.levelSet(sphere.vertices, sphere.faces, z, level);
    const set = L.levelSet(sphere.vertices, sphere.faces, z, level, { values });
    assert.deepEqual(set.positions, plain.positions, 'the same segments with or without a field');
    assert.equal(plain.values, null);
    assert.equal(plain.orient, null);
    assert.equal(set.values.length, 2 * set.count);
    for (let s = 0; s < set.count; s += 1) {
      for (const end of [0, 1]) {
        const o = 6 * s + 3 * end;
        close(set.values[2 * s + end], 2 * set.positions[o] - set.positions[o + 1] + 0.5 * set.positions[o + 2] + 3, 1e-9, `level ${level} segment ${s}`);
      }
    }
  }
});

test('a segment knows which side the solid is on: the cut of a body runs counterclockwise, the cut of a hole clockwise', () => {
  const n = 32; const R = 3;
  const cyl = cylinder(R, 2, n, 4);
  assert.ok(volumeOf(cyl) > 0, 'the fixture is wound outward');
  const index = L.buildSliceIndex(cyl.vertices, cyl.faces);
  const area = (n / 2) * R * R * Math.sin((2 * Math.PI) / n);
  for (const z of [0.3, 0.77, 1.25, 1.9]) {
    const set = L.sectionContour(index, z, { orient: true });
    assert.equal(set.orient.length, set.count);
    assert.ok(Array.from(set.orient).every((s) => s === 1 || s === -1), 'every wall face has a side');
    close(directed(set) / 2, area, 1e-9, `z ${z}: run the way the sides say, the ring is counterclockwise`);
  }
  // Turned inside out it is a hole: the same ring, clockwise.
  const hole = inside_out(cyl);
  assert.ok(volumeOf(hole) < 0);
  const holeIndex = L.buildSliceIndex(hole.vertices, hole.faces);
  close(directed(L.sectionContour(holeIndex, 1.25, { orient: true })) / 2, -area, 1e-9, 'a hole runs clockwise');
  // A torus cut through its middle: the outer circle counterclockwise, the inner one clockwise.
  let ring = torus(5, 1.5, 48, 16);
  if (volumeOf(ring) < 0) ring = inside_out(ring);
  const set = L.sliceContour(L.buildSliceIndex(ring.vertices, ring.faces), 0.123, { orient: true });
  const loops = byArea(L.contourLoops(set));
  assert.equal(loops.length, 2);
  assert.ok(loops.every((loop) => loop.closed));
  const areas = loops.map((loop) => shoelace(loop.points) / 2);
  assert.ok(areas[0] > 0 && areas[1] < 0, `outer ${areas[0].toFixed(2)}, inner ${areas[1].toFixed(2)}`);
  close(areas[0] + areas[1], Math.PI * (6.5 ** 2 - 3.5 ** 2) * (1 - 0.01), 3, 'together they enclose the ring of the torus');
});

test('the sides are measured across any axis: right-handed in the plane\'s own two', () => {
  const sphere = uvSphere(5, 48, 24);
  const index = (axis) => L.buildSliceIndex(sphere.vertices, sphere.faces, { axis });
  const circle = Math.PI * 4 ** 2;                       // the cut at 3 from the center has radius 4
  // Across x: the plane's axes are (y, z); across y: (z, x); across z: (x, y).
  for (const [axis, u, v] of [[0, 1, 2], [1, 2, 0], [2, 0, 1]]) {
    const set = L.sectionContour(index(axis), 3, { orient: true });
    const sum = directed(set, u, v) / 2;
    assert.ok(sum > 0.98 * circle && sum < circle, `axis ${axis}: ${sum.toFixed(2)} against ${circle.toFixed(2)}`);
  }
  assert.throws(() => L.levelSet(sphere.vertices, sphere.faces, coordOf(sphere, 2), 0, { axis: 3 }), RangeError);
});

test('closed loops come out the way their segments say, whichever segment the walk begins on', () => {
  const ring = (() => { const m = torus(5, 1.5, 48, 16); return volumeOf(m) < 0 ? inside_out(m) : m; })();
  const set = L.sliceContour(L.buildSliceIndex(ring.vertices, ring.faces), 0.123, { orient: true });
  const next = (() => { let s = 7; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; }; })();
  for (let round = 0; round < 12; round += 1) {
    // The same cut with its segments shuffled and about half of them written the other way round.
    const order = Array.from({ length: set.count }, (_, k) => k).sort(() => next() - 0.5);
    const positions = new Float64Array(6 * set.count);
    const orient = new Int8Array(set.count);
    order.forEach((from, to) => {
      const flip = next() < 0.5;
      for (let k = 0; k < 3; k += 1) {
        positions[6 * to + k] = set.positions[6 * from + (flip ? 3 : 0) + k];
        positions[6 * to + 3 + k] = set.positions[6 * from + (flip ? 0 : 3) + k];
      }
      orient[to] = flip ? -set.orient[from] : set.orient[from];
    });
    const loops = byArea(L.contourLoops({ count: set.count, positions, orient }));
    assert.equal(loops.length, 2);
    assert.ok(loops[0].closed && loops[1].closed);
    assert.ok(shoelace(loops[0].points) > 0, `round ${round}: the outer circle runs counterclockwise`);
    assert.ok(shoelace(loops[1].points) < 0, `round ${round}: the inner one clockwise`);
  }
  // Without the sides the walk's own direction stands: nothing is promised, nothing is reversed.
  const plain = L.contourLoops({ count: set.count, positions: set.positions });
  assert.equal(plain.length, 2);
});

test('sectionContour carries the field and the sides through the union of its two rules', () => {
  // Two closed prisms, z 0..2 and 5..7, with caps on their section planes: the second one's bottom cap is found
  // by the other rule only, and what rides along with its segments must come from that rule's faces.
  const n = 24; const R = 2;
  const mesh = joined(cylinder(R, 2, n, 2), lifted(cylinder(R, 2, n, 2), 5));
  assert.ok(volumeOf(mesh) > 0);
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  const field = Float64Array.from({ length: mesh.vertices.length / 3 }, (_, i) => 3 * mesh.vertices[3 * i + 2] + mesh.vertices[3 * i]);
  const area = (n / 2) * R * R * Math.sin((2 * Math.PI) / n);
  for (const z of [0, 1, 2, 5, 6, 7]) {
    const set = L.sectionContour(index, z, { values: field, orient: true });
    assert.equal(set.count, n, `z ${z}`);
    assert.equal(set.values.length, 2 * n);
    assert.equal(set.orient.length, n);
    for (let s = 0; s < n; s += 1) {
      for (const end of [0, 1]) {
        const o = 6 * s + 3 * end;
        close(set.values[2 * s + end], 3 * set.positions[o + 2] + set.positions[o], 1e-9, `z ${z} segment ${s}`);
      }
    }
    close(directed(set) / 2, area, 1e-9, `z ${z}: counterclockwise, the bottom cap's ring too`);
    assert.deepEqual(L.sectionContour(index, z).positions, set.positions, 'the field and the sides change no segment');
  }
  // Where the rules agree nothing is added twice, and the three arrays stay the same length.
  const middle = L.sectionContour(index, 1, { values: field, orient: true });
  assert.equal(middle.count, middle.orient.length);
});

// -- the zero line of a signed field --------------------------------------------------------------

test('the zero crossing of a linear field is a straight line, found exactly', () => {
  const g = grid(2, 1, 8, 5);
  // f = x - 0.3 - 0.25 y: zero where x = 0.3 + 0.25 y.
  const field = Float64Array.from({ length: g.vertices.length / 3 }, (_, i) => g.vertices[3 * i] - 0.3 - 0.25 * g.vertices[3 * i + 1]);
  const set = L.zeroLine(g.vertices, g.faces, field);
  assert.ok(set.count > 0);
  for (let s = 0; s < set.count; s += 1) {
    for (const o of [0, 3]) {
      const x = set.positions[6 * s + o]; const y = set.positions[6 * s + o + 1];
      close(x, 0.3 + 0.25 * y, 1e-12, 'on the line');
    }
  }
  const loops = L.contourLoops(set);
  assert.equal(loops.length, 1); assert.equal(loops[0].closed, false);
  const p = loops[0].points;
  const m = p.length / 3;
  const ys = [p[1], p[3 * m - 2]].sort((a, b) => a - b);
  close(ys[0], 0, 1e-12); close(ys[1], 1, 1e-12);
  // The line runs straight: every point is collinear with the two ends.
  for (let k = 1; k < m - 1; k += 1) {
    const cross = (p[3 * k] - p[0]) * (p[3 * m - 2] - p[1]) - (p[3 * k + 1] - p[1]) * (p[3 * m - 3] - p[0]);
    close(cross, 0, 1e-12, 'collinear');
  }
  // The level moves it: f = 0.2 is the line x = 0.5 + 0.25 y.
  const shifted = L.zeroLine(g.vertices, g.faces, field, { level: 0.2 });
  close(shifted.positions[0], 0.5 + 0.25 * shifted.positions[1], 1e-12);
});

test('where one sphere pokes through another the zero line is the circle they cross in', () => {
  // A: radius 5 about the origin.  B stand-in: the signed field x - 2 on A (outside the plane x = 2 is positive).
  const a = uvSphere(5, 128, 64);
  const field = Float64Array.from({ length: a.vertices.length / 3 }, (_, i) => a.vertices[3 * i] - 2);
  const loops = L.contourLoops(L.zeroLine(a.vertices, a.faces, field));
  assert.equal(loops.length, 1); assert.equal(loops[0].closed, true);
  const expected = Math.sqrt(25 - 4);
  const p = loops[0].points;
  for (let k = 0; k < p.length; k += 3) {
    close(p[k], 2, 1e-9, 'x');
    close(Math.hypot(p[k + 1], p[k + 2]), expected, 0.004 * expected, 'radius');
  }
});

test('a field that is not a number leaves its faces out and nothing else; a field of one sign has no line', () => {
  const sphere = uvSphere(5, 32, 16);
  const z = coordOf(sphere, 2);
  const clean = L.levelSet(sphere.vertices, sphere.faces, z, 0.5);
  // Spoil two vertices of faces the level crosses.
  const broken = Float64Array.from(z);
  const crossing = [];
  for (let f = 0; f < sphere.faces.length && crossing.length < 2; f += 3) {
    const zs = [0, 1, 2].map((c) => z[sphere.faces[f + c]]);
    if (Math.min(...zs) < 0.5 && Math.max(...zs) >= 0.5) crossing.push(sphere.faces[f]);
  }
  broken[crossing[0]] = NaN; broken[crossing[1]] = Infinity;
  const holed = L.levelSet(sphere.vertices, sphere.faces, broken, 0.5);
  assert.ok(holed.count < clean.count && holed.count > 0);
  assert.ok(Array.from(holed.positions).every(Number.isFinite));
  assert.equal(L.levelSet(sphere.vertices, sphere.faces, z, 99).count, 0);
  assert.equal(L.levelSet(sphere.vertices, sphere.faces, z, -99).count, 0);
  assert.equal(L.levelSet([], [], [], 0).count, 0);
  const empty = L.buildSliceIndex([], []);
  assert.equal(L.sliceContour(empty, 0).count, 0);
  assert.equal(L.sliceContour(L.buildSliceIndex(sphere.vertices, sphere.faces), NaN).count, 0);
});

// -- the buffers --------------------------------------------------------------------------------------

test('the segments are the renderer\'s buffer: float32, six numbers each, the narrowed positions', () => {
  const sphere = uvSphere(5, 48, 24);
  const set = L.levelSet(sphere.vertices, sphere.faces, coordOf(sphere, 2), 1.5);
  assert.ok(set.segments instanceof Float32Array && set.positions instanceof Float64Array);
  assert.equal(set.segments.length, set.count * 6);
  assert.deepEqual(set.segments, Float32Array.from(set.positions));
  const loops = L.contourLoops(set);
  const back = L.loopsToSegments(loops);
  assert.equal(back.length, set.count * 6, 'a closed loop gives back every segment');
  // An open curve has one segment fewer than points.
  const open = L.loopsToSegments([{ points: Float64Array.from([0, 0, 0, 1, 0, 0, 1, 1, 0]), closed: false }]);
  assert.equal(open.length, 12);
  assert.deepEqual(Array.from(open.slice(6)), [1, 0, 0, 1, 1, 0]);
  const shut = L.loopsToSegments([{ points: Float64Array.from([0, 0, 0, 1, 0, 0, 1, 1, 0]), closed: true }]);
  assert.equal(shut.length, 18);
  assert.deepEqual(Array.from(shut.slice(12)), [1, 1, 0, 0, 0, 0]);
  close(L.loopLength({ points: Float64Array.from([0, 0, 0, 1, 0, 0, 1, 1, 0]), closed: true }), 2 + Math.SQRT2);
  close(L.loopLength({ points: Float64Array.from([0, 0, 0, 1, 0, 0, 1, 1, 0]), closed: false }), 2);
});

test('typed arrays, as a renderer holds them, give the same contour', () => {
  const sphere = uvSphere(5, 64, 32);
  const plain = L.contourLoops(L.sliceContour(L.buildSliceIndex(sphere.vertices, sphere.faces), 1));
  const typed = L.contourLoops(L.sliceContour(
    L.buildSliceIndex(Float32Array.from(sphere.vertices), Uint32Array.from(sphere.faces)), 1));
  assert.equal(typed.length, 1);
  assert.equal(typed[0].points.length, plain[0].points.length);
  close(L.loopLength(typed[0]), L.loopLength(plain[0]), 1e-4);
});

// -- speed ----------------------------------------------------------------------------------------------

test('a 400,000-face mesh is indexed once and cut in milliseconds', () => {
  const sphere = uvSphere(50, 400, 500);
  const faces = sphere.faces.length / 3;
  assert.ok(faces >= 399000, `${faces} faces`);
  let t = performance.now();
  const index = L.buildSliceIndex(sphere.vertices, sphere.faces);
  const build = performance.now() - t;
  t = performance.now();
  let segments = 0;
  const reps = 50;
  for (let k = 0; k < reps; k += 1) segments += L.sliceContour(index, -40 + (80 * k) / reps).count;
  const slice = (performance.now() - t) / reps;
  t = performance.now();
  const full = L.levelSet(sphere.vertices, sphere.faces, index.coord, 3.3);
  const scan = performance.now() - t;
  t = performance.now();
  const loops = L.contourLoops(L.sliceContour(index, 3.3));
  const join = performance.now() - t;
  assert.equal(loops.length, 1); assert.ok(loops[0].closed);
  assert.equal(full.count, L.sliceContour(index, 3.3).count);
  // Generous bounds for a loaded machine; the point is that a slice does not scan the mesh.
  assert.ok(slice < 100, `slice ${slice.toFixed(2)} ms`);
  assert.ok(build < 3000, `index ${build.toFixed(0)} ms`);
  console.log(`     (${faces.toLocaleString()} faces: index ${build.toFixed(0)} ms, slice ${slice.toFixed(2)} ms `
    + `(${(segments / reps).toFixed(0)} segments), full scan ${scan.toFixed(1)} ms, join ${join.toFixed(1)} ms)`);
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
