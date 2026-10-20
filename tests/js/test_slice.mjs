// The Section view (static/js/slice.js): the pure half -- the section-to-Z fit, the
// annotation index, the keys, the 2D view, the contour with an error value at every
// end, the grid that answers nearest-point and inside-or-outside, the gap of an
// annotation's rim from the contour, the shift arrows -- on shapes whose answers are
// known, and `SliceView` against a fake DOM and a recording canvas (listeners only on
// the canvas, keys, pan and zoom, hover and click, what is drawn).  Run with
//
//     node tests/js/test_slice.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free; the real canvas
// is checked in a browser by tools/check_inspector.py.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');

// Importing the module must not read the page: make the globals that would give it
// away throw while it loads.
const poisoned = ['document', 'window', 'navigator', 'localStorage', 'requestAnimationFrame', 'ResizeObserver'];
const saved = new Map(poisoned.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
for (const name of poisoned) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    get() { throw new Error(`slice.js touched ${name} at import`); },
  });
}
let loadError = null;
let S = {};
try {
  S = await import(`${pathToFileURL(path.join(jsDir, 'slice.js')).href}?cold`);
} catch (error) {
  loadError = error;
} finally {
  for (const [name, descriptor] of saved) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
  }
}
const L = await import(pathToFileURL(path.join(jsDir, 'levelset.js')).href);
const SH = await import(pathToFileURL(path.join(jsDir, 'shifts.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, tol = 1e-9, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} is not within ${tol} of ${b}`);

// -- shapes ----------------------------------------------------------------------------------

/** A closed cylinder of radius R from z = 0 to z = h around (cx, cy), `n` sides, caps as fans. */
function cylinder(R, h, n, rows = 1, [cx, cy] = [0, 0]) {
  const vertices = [];
  for (let k = 0; k <= rows; k += 1) {
    for (let i = 0; i < n; i += 1) {
      const th = (2 * Math.PI * i) / n;
      vertices.push(cx + R * Math.cos(th), cy + R * Math.sin(th), (h * k) / rows);
    }
  }
  const bottom = vertices.length / 3;
  vertices.push(cx, cy, 0);
  const top = vertices.length / 3;
  vertices.push(cx, cy, h);
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
  return { vertices: Float64Array.from(vertices), faces: Uint32Array.from(faces) };
}

/** A UV sphere (poles on Z): `nu` meridians, `nv` rings. */
function uvSphere(R, nu, nv) {
  const vertices = [0, 0, R];
  for (let j = 1; j < nv; j += 1) {
    const phi = (Math.PI * j) / nv;
    for (let i = 0; i < nu; i += 1) {
      const th = (2 * Math.PI * i) / nu;
      vertices.push(R * Math.sin(phi) * Math.cos(th), R * Math.sin(phi) * Math.sin(th), R * Math.cos(phi));
    }
  }
  vertices.push(0, 0, -R);
  const south = vertices.length / 3 - 1;
  const ring = (j, i) => 1 + (j - 1) * nu + (i % nu);
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
  return { vertices: Float64Array.from(vertices), faces: Uint32Array.from(faces) };
}

/** A closed polygon of `n` points on a circle, as the flat segment buffer `buildSegmentGrid` takes. */
function circleSegments(R, n, [cx, cy] = [0, 0], reverse = false) {
  const out = [];
  for (let i = 0; i < n; i += 1) {
    const a = (2 * Math.PI * (reverse ? n - i : i)) / n;
    const b = (2 * Math.PI * (reverse ? n - i - 1 : i + 1)) / n;
    out.push(cx + R * Math.cos(a), cy + R * Math.sin(a), 0, cx + R * Math.cos(b), cy + R * Math.sin(b), 0);
  }
  return Float64Array.from(out);
}

const concat = (...arrays) => Float64Array.from(arrays.flatMap((a) => Array.from(a)));

/** Meshes joined into one, as a cell's pieces are: the indices of each are shifted past those before it. */
function joined(...meshes) {
  const vertices = [];
  const faces = [];
  for (const m of meshes) {
    const base = vertices.length / 3;
    vertices.push(...m.vertices);
    for (const f of m.faces) faces.push(f + base);
  }
  return { vertices: Float64Array.from(vertices), faces: Uint32Array.from(faces) };
}

/** The mesh moved `dz` up. */
const lifted = (m, dz) => ({ vertices: Float64Array.from(m.vertices, (v, i) => (i % 3 === 2 ? v + dz : v)), faces: m.faces });

/** The same surface wound the other way: the wall of a cavity (its solid is outside it). */
const insideOut = (m) => ({
  vertices: m.vertices,
  faces: Uint32Array.from(m.faces, (_, k) => m.faces[k % 3 === 0 ? k : k % 3 === 1 ? k + 1 : k - 1]),
});

/** A deterministic pseudo-random sequence in [0, 1). */
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

/** A `/nodes` row. */
const node = (id, section, x, y, { r = 1, type = 1, thickness = 0.07, shift = null, live = null } = {}) => ({
  id, parent_id: 1, z_section: section, radius_um: r, type_code: type,
  xyz_um: [x, y, section * thickness], shift_um: shift, live_shift_um: live,
});

// -- loading ------------------------------------------------------------------------------------

test('importing the module touches no document, window, storage or animation frame', () => {
  assert.equal(loadError, null, loadError && String(loadError.stack));
  assert.equal(typeof S.SliceView, 'function');
});

// -- section number <-> Z -------------------------------------------------------------------------

test('the section thickness is fitted from the annotations, 70 nm not 90', () => {
  const sections = [100, 130, 131, 250, 400];
  for (const thickness of [0.07, 0.09, 0.0703]) {
    const map = S.fitZMap(sections, sections.map((s) => 12.5 + s * thickness));
    close(map.dz, thickness, 1e-12, 'dz');
    assert.ok(map.maxResidualUm < 1e-9);
    assert.equal(map.fitted, true);
    close(S.sectionToZ(map, 300), 12.5 + 300 * thickness, 1e-9, 'z of an unannotated section');
    close(S.zToSection(map, 12.5 + 123 * thickness), 123, 1e-6, 'section of a Z');
  }
});

test('annotations that disagree show it in the residual, and bad pairs are left out', () => {
  const map = S.fitZMap([0, 10, 20, 30, NaN, 40], [0, 0.7, 1.4, 2.2, 5, null]);
  assert.equal(map.n, 4);
  assert.ok(map.maxResidualUm > 0.01);
  assert.equal(S.fitZMap([], []).n, 0);
});

test('one section alone has no slope: every section maps to that Z', () => {
  const map = S.fitZMap([50, 50, 50], [3.5, 3.5, 3.5]);
  assert.equal(map.fitted, false);
  assert.equal(map.dz, 0);
  assert.equal(S.sectionToZ(map, 51), 3.5);
  assert.equal(S.zToSection(map, 9), 50);
});

// -- the annotation index ---------------------------------------------------------------------------

test('annotations are binned by section, ascending, and the index equals a scan', () => {
  const next = rng(7);
  const nodes = [];
  for (let id = 1; id <= 400; id += 1) {
    const s = 10 + Math.floor(next() * 60) * 3;
    nodes.push(node(id, s, next() * 100, next() * 100, { r: 0.2 + next() * 3 }));
  }
  const index = S.buildAnnotationIndex(nodes, []);
  assert.deepEqual(Array.from(index.sections), [...new Set(nodes.map((n) => n.z_section))].sort((a, b) => a - b));
  for (const section of [10, 13, 40, 187, 188, 0, 1000]) {
    const scan = nodes.filter((n) => n.z_section === section).map((n) => n.id);
    assert.deepEqual(S.annotationsAt(index, section).discs.map((d) => d.id), scan, `section ${section}`);
  }
  assert.equal(S.annotationsAt(index, 99999).discs.length, 0);
  assert.equal(index.discs.length, 400);
});

test('a closed-curve node with an outline is the outline; one without is a dashed disc; bad rows are left out', () => {
  const square = [[0, 0], [4, 0], [4, 4], [0, 4]];
  const nodes = [
    node(1, 10, 2, 2, { r: 2.2, type: 6 }),           // has an outline: drawn as the polygon
    node(2, 11, 9, 9, { r: 1, type: 6 }),             // no outline: a disc
    node(3, 12, 1, 1, { r: 0 }),                      // no radius
    node(4, 12, NaN, 1),                              // not a number
    node(5, 12, 5, 5, { r: 1 }),
  ];
  const outlines = [
    { location_id: 1, z_section: 10, z_um: 0.7, outline_um: square, cutouts_um: [[[1, 1], [2, 1], [2, 2]]] },
    { location_id: 99, z_section: 10, z_um: 0.7, outline_um: [[0, 0], [1, 1]] },    // two points: no ring
  ];
  const index = S.buildAnnotationIndex(nodes, outlines);
  assert.deepEqual(index.discs.map((d) => [d.id, d.closedCurve]), [[2, true], [5, false]]);
  assert.equal(index.outlines.length, 1);
  const o = index.outlines[0];
  assert.equal(o.kind, 'outline');
  assert.equal(o.points.length, 8);
  assert.equal(o.cutouts.length, 1);
  close(o.x, 2, 1e-12);
  close(o.r, Math.sqrt(16 / Math.PI), 1e-9, 'equivalent radius from the area');
  assert.deepEqual(Array.from(index.sections), [10, 11, 12]);
  assert.deepEqual(index.bounds, { minX: 0, minY: 0, maxX: 10, maxY: 10 });
  assert.equal(S.buildAnnotationIndex([], []).bounds, null);
});

test('neighbor sections are the nearest annotated ones on each side', () => {
  const sections = Int32Array.from([10, 20, 30, 40, 50]);
  assert.deepEqual(S.neighborSections(sections, 30, 1), { below: [20], above: [40] });
  assert.deepEqual(S.neighborSections(sections, 25, 2), { below: [20, 10], above: [30, 40] });
  assert.deepEqual(S.neighborSections(sections, 5, 1), { below: [], above: [10] });
  assert.deepEqual(S.neighborSections(sections, 55, 1), { below: [50], above: [] });
  assert.deepEqual(Array.from(S.mergeSections([[3, 1], Int32Array.from([2, 3])])), [1, 2, 3]);
});

// -- the keys ---------------------------------------------------------------------------------------------

const key = (k, extra = {}) => ({ key: k, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...extra });
const world = { sections: Int32Array.from([100, 105, 110, 200, 300]), range: { min: 100, max: 300 } };
const act = (event, section) => S.sliceKeyAction(event, { section, ...world });

test('the documented keys move the section by the documented steps', () => {
  assert.deepEqual(act(key('ArrowUp'), 100), { kind: 'section', section: 105 });         // next annotated
  assert.deepEqual(act(key('ArrowUp'), 150), { kind: 'section', section: 200 });         // from an empty section
  assert.deepEqual(act(key('ArrowDown'), 200), { kind: 'section', section: 110 });
  assert.deepEqual(act(key('ArrowDown'), 150), { kind: 'section', section: 110 });
  assert.deepEqual(act(key('ArrowRight'), 150), { kind: 'section', section: 151 });
  assert.deepEqual(act(key('ArrowLeft'), 150), { kind: 'section', section: 149 });
  assert.deepEqual(act(key('ArrowRight', { shiftKey: true }), 150), { kind: 'section', section: 160 });
  assert.deepEqual(act(key('ArrowLeft', { shiftKey: true }), 150), { kind: 'section', section: 140 });
  assert.deepEqual(act(key('PageUp'), 150), { kind: 'section', section: 160 });
  assert.deepEqual(act(key('PageDown'), 150), { kind: 'section', section: 140 });
  assert.deepEqual(act(key('Home'), 150), { kind: 'section', section: 100 });
  assert.deepEqual(act(key('End'), 150), { kind: 'section', section: 300 });
});

test('Shift on Up and Down jumps ten annotated sections, as far as there are', () => {
  const many = Int32Array.from({ length: 30 }, (_, i) => 100 + 2 * i);
  const go = (k, s) => S.sliceKeyAction(key(k, { shiftKey: true }), { section: s, sections: many });
  assert.equal(go('ArrowUp', 100).section, 120);
  assert.equal(go('ArrowUp', 101).section, 120);        // from between: the tenth annotated above
  assert.equal(go('ArrowDown', 158).section, 138);
  assert.equal(go('ArrowUp', 150).section, 158, 'fewer than ten above: the last one');
  assert.equal(go('ArrowDown', 108).section, 100, 'fewer than ten below: the first one');
});

test('at the end of the annotated sections a key changes nothing; the range clamps the plain steps', () => {
  assert.equal(act(key('ArrowUp'), 300).section, 300);
  assert.equal(act(key('ArrowDown'), 100).section, 100);
  const wide = (k, sec) => S.sliceKeyAction(key(k), { section: sec, sections: world.sections, range: { min: 90, max: 400 } });
  assert.equal(wide('ArrowUp', 305).section, 305, 'above the last annotated section, within the range');
  assert.equal(wide('ArrowDown', 95).section, 95, 'below the first');
  assert.equal(wide('ArrowRight', 399).section, 400);
  assert.equal(wide('ArrowRight', 400).section, 400);
  assert.equal(act(key('ArrowRight'), 300).section, 300);
  assert.equal(act(key('PageDown'), 105).section, 100);
  assert.equal(act(key('ArrowLeft', { shiftKey: true }), 103).section, 100);
});

test('zoom and reset keys exist for the keyboard; everything else, and every chord, is not ours', () => {
  assert.deepEqual(act(key('+'), 150), { kind: 'zoom', factor: S.ZOOM_STEP });
  assert.deepEqual(act(key('='), 150), { kind: 'zoom', factor: S.ZOOM_STEP });
  close(act(key('-'), 150).factor, 1 / S.ZOOM_STEP, 1e-12);
  assert.deepEqual(act(key('0'), 150), { kind: 'reset' });
  for (const k of ['a', 'Enter', 'Tab', ' ', 'Escape', 'F5', '[', ']']) assert.equal(act(key(k), 150), null, k);
  for (const chord of [{ ctrlKey: true }, { altKey: true }, { metaKey: true }]) {
    for (const k of ['ArrowUp', 'ArrowRight', 'Home', '0', '+']) assert.equal(act(key(k, chord), 150), null, `${k} ${JSON.stringify(chord)}`);
  }
});

test('without any annotation the keys have nowhere to go', () => {
  const none = (k) => S.sliceKeyAction(key(k), { section: 7, sections: Int32Array.from([]) });
  for (const k of ['ArrowUp', 'ArrowDown', 'Home', 'End']) assert.deepEqual(none(k), { kind: 'section', section: 7 });
});

// -- the view ------------------------------------------------------------------------------------------------

const SIZE = { width: 600, height: 400 };

test('fit centers the box and leaves the margin; a tiny box is not blown up', () => {
  const v = S.fitView({ minX: 10, minY: 20, maxX: 110, maxY: 70 }, SIZE, { margin: 20 });
  assert.deepEqual([v.cx, v.cy], [60, 45]);
  close(v.scale, Math.min(560 / 100, 360 / 50), 1e-12);
  const tiny = S.fitView({ minX: 5, minY: 5, maxX: 5.1, maxY: 5.1 }, SIZE, { margin: 20 });
  close(tiny.scale, Math.min(560, 360) / S.MIN_FIT_UM, 1e-9);
  const none = S.fitView(null, SIZE);
  assert.deepEqual([none.cx, none.cy], [0, 0]);
});

test('world and screen are inverses; Y grows downward on screen', () => {
  const view = { cx: 40, cy: -10, scale: 12 };
  const [px, py] = S.worldToScreen(view, SIZE, 43, -8);
  assert.deepEqual([px, py], [300 + 36, 200 + 24]);
  const [x, y] = S.screenToWorld(view, SIZE, px, py);
  close(x, 43, 1e-12);
  close(y, -8, 1e-12);
});

test('zoom keeps the point under the pointer where it was, and stays within the limits', () => {
  const view = { cx: 12, cy: 7, scale: 20 };
  for (const [px, py, factor] of [[100, 50, 1.25], [450, 300, 0.8], [300, 200, 3]]) {
    const before = S.screenToWorld(view, SIZE, px, py);
    const zoomed = S.zoomAt(view, SIZE, px, py, factor);
    close(zoomed.scale, 20 * factor, 1e-12);
    const after = S.screenToWorld(zoomed, SIZE, px, py);
    close(after[0], before[0], 1e-9);
    close(after[1], before[1], 1e-9);
  }
  assert.equal(S.zoomAt(view, SIZE, 0, 0, 1e9).scale, S.VIEW_LIMITS.maxScale);
  assert.equal(S.zoomAt(view, SIZE, 0, 0, 1e-9).scale, S.VIEW_LIMITS.minScale);
});

test('a drag moves the content with the pointer', () => {
  const view = { cx: 0, cy: 0, scale: 10 };
  const dragged = S.panView(view, 50, -30);
  const [px, py] = S.worldToScreen(dragged, SIZE, 0, 0);
  assert.deepEqual([px, py], [300 + 50, 200 - 30]);
});

test('the scale bar is a round length no wider than asked', () => {
  for (const scale of [0.3, 1, 7.7, 40, 333, 12000]) {
    const bar = S.scaleBarLength(scale, 120);
    assert.ok(bar.px <= 120 + 1e-9 && bar.px > 120 / 5, `${scale}: ${bar.px}`);
    const mantissa = bar.um / 10 ** Math.floor(Math.log10(bar.um));
    assert.ok([1, 2, 5].some((m) => Math.abs(m - mantissa) < 1e-9), `${bar.um}`);
  }
});

// -- the contour with a value at every end ---------------------------------------------------------------------

test('contourSegments is sectionContour, segment for segment, at every kind of level', () => {
  // Two stacks (z 0..2 and 5..7) are in the list: the second one's bottom cap lies on a plane that is not
  // the surface's lowest, which the rule this file once repeated for itself did not cut.
  const stacks = joined(cylinder(2, 2, 24, 2), lifted(cylinder(2, 2, 24, 2), 5));
  for (const mesh of [uvSphere(5, 36, 18), cylinder(4, 10, 24, 5), stacks]) {
    const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
    const heights = Array.from(new Set(Array.from({ length: mesh.vertices.length / 3 }, (_, i) => mesh.vertices[3 * i + 2])));
    const levels = [...heights, -100, 100, 0.123, 3.37, 7.77, ...heights.map((h) => h + 1e-3)];
    const field = Float64Array.from({ length: mesh.vertices.length / 3 }, (_, i) => 2 * mesh.vertices[3 * i] + mesh.vertices[3 * i + 2]);
    for (const level of levels) {
      const mine = S.contourSegments(index, level);
      const ref = L.sectionContour(index, level);
      assert.equal(mine.count, ref.count, `level ${level}`);
      assert.deepEqual(Array.from(mine.positions), Array.from(ref.positions), `level ${level}`);
      assert.equal(mine.values, null);
      // With a field, and the sides every segment carries for the signed count.
      const painted = S.contourSegments(index, level, field);
      const full = L.sectionContour(index, level, { values: field, orient: true });
      assert.deepEqual(Array.from(painted.positions), Array.from(full.positions), `level ${level}`);
      assert.deepEqual(Array.from(painted.values), Array.from(full.values), `level ${level}`);
      assert.deepEqual(Array.from(painted.orient), Array.from(full.orient), `level ${level}`);
      assert.equal(painted.orient.length, painted.count);
    }
  }
});

test('a cell\'s second stack has its outline at its bottom cap too: contourSegments uses sectionContour\'s rule, not a copy of the old one', () => {
  // Two closed prisms, z 0..2 and 5..7, with a cap on the plane of its section at both ends.  The copy of the
  // marching rule this file used to keep cut the bottom cap of the lowest stack only; the second stack's, at
  // z = 5, came out empty, and the test of segment-for-segment equality could not see it (one stack).
  const n = 24;
  const stacks = joined(cylinder(2, 2, n, 2), lifted(cylinder(2, 2, n, 2), 5));
  const index = L.buildSliceIndex(stacks.vertices, stacks.faces);
  for (const z of [0, 2, 5, 7]) assert.equal(S.contourSegments(index, z).count, n, `z ${z}: one segment for each side of the ring`);
  assert.equal(S.contourSegments(index, 3).count, 0, 'between the stacks there is nothing');
  const loops = L.contourLoops(S.contourSegments(index, 5));
  assert.equal(loops.length, 1);
  assert.equal(loops[0].closed, true);
});

test('a field is interpolated along each edge: exact for a linear field', () => {
  const mesh = uvSphere(5, 48, 24);
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  const nv = mesh.vertices.length / 3;
  const values = Float64Array.from({ length: nv }, (_, i) => 2 * mesh.vertices[3 * i] - mesh.vertices[3 * i + 1] + 0.5 * mesh.vertices[3 * i + 2] + 3);
  for (const level of [-3.3, 0.37, 2.2]) {
    const c = S.contourSegments(index, level, values);
    assert.equal(c.values.length, 2 * c.count);
    for (let s = 0; s < c.count; s += 1) {
      for (const end of [0, 1]) {
        const x = c.positions[6 * s + 3 * end];
        const y = c.positions[6 * s + 3 * end + 1];
        const z = c.positions[6 * s + 3 * end + 2];
        close(c.values[2 * s + end], 2 * x - y + 0.5 * z + 3, 1e-9, `level ${level} segment ${s}`);
      }
    }
  }
});

test('a sphere cut between its rings is one closed loop of the right size', () => {
  const R = 5;
  const m = 48;
  const nv = 24;
  const mesh = uvSphere(R, m, nv);
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  for (const z of [0.4, -1.7, 3.9]) {
    const set = S.contourSegments(index, z);
    const loops = L.contourLoops(set);
    assert.equal(loops.length, 1);
    assert.equal(loops[0].closed, true);
    // The mesh is the sphere's polyhedron: its radius at z is within the chord's sagitta of the sphere's.
    const radii = Array.from({ length: loops[0].points.length / 3 }, (_, k) => Math.hypot(loops[0].points[3 * k], loops[0].points[3 * k + 1]));
    const sphereRadius = Math.sqrt(R * R - z * z);
    for (const r of radii) assert.ok(Math.abs(r - sphereRadius) < 0.06 * R, `z ${z}: ${r} vs ${sphereRadius}`);
  }
});

test('the bottom and top caps of a closed stack have their outline at the section on the plane', () => {
  const mesh = cylinder(4, 10, 32, 3);
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  for (const z of [0, 10]) {
    const set = S.contourSegments(index, z);
    assert.ok(set.count >= 32, `z ${z}: ${set.count} segments`);
    const grid = S.buildSegmentGrid(set.positions);
    assert.ok(S.insideContour(grid, 0, 0), `z ${z}: the cap is inside its own rim`);
  }
});

test('bucketing by error puts every segment in exactly one level of the ramp', () => {
  const values = Float64Array.from([0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.2, 1.6, NaN, NaN]);
  const groups = S.bucketByError(values, 5, 1.0, 24);
  assert.equal(groups.reduce((n, g) => n + g.segments.length, 0), 5);
  assert.deepEqual(groups.map((g) => g.level), [...groups.map((g) => g.level)].sort((a, b) => a - b));
  const last = groups[groups.length - 1];
  assert.equal(last.level, 23, 'beyond the scale is the top of the ramp');
  assert.ok(groups.every((g) => /^rgba\(/.test(g.color)));
  // Segments 0..3 sit at 0.05, 0.25, 0.6 and beyond the scale; segment 4 has no value and is painted as no error.
  assert.deepEqual([groups[0].level, groups[0].segments], [0, [4]]);
  const nan = S.bucketByError(Float64Array.from([NaN, NaN]), 1, 1.0);
  assert.equal(nan[0].level, 0, 'a value that is not a number is painted as zero error, not dropped');
});

// -- the grid ---------------------------------------------------------------------------------------------------------

function bruteNearest(positions, x, y) {
  let best = Infinity;
  for (let s = 0; s < positions.length / 6; s += 1) {
    const x0 = positions[6 * s];
    const y0 = positions[6 * s + 1];
    const dx = positions[6 * s + 3] - x0;
    const dy = positions[6 * s + 4] - y0;
    const len2 = dx * dx + dy * dy;
    const t = len2 > 0 ? Math.min(1, Math.max(0, ((x - x0) * dx + (y - y0) * dy) / len2)) : 0;
    best = Math.min(best, Math.hypot(x - (x0 + t * dx), y - (y0 + t * dy)));
  }
  return best;
}

function bruteInside(positions, x, y) {
  let c = 0;
  for (let s = 0; s < positions.length / 6; s += 1) {
    const y0 = positions[6 * s + 1];
    const y1 = positions[6 * s + 4];
    if ((y0 > y) === (y1 > y)) continue;
    const x0 = positions[6 * s];
    if (x0 + ((y - y0) * (positions[6 * s + 3] - x0)) / (y1 - y0) > x) c += 1;
  }
  return c % 2 === 1;
}

test('the nearest point from the grid is the nearest by scan, inside, outside and far away', () => {
  const next = rng(11);
  // A bumpy closed polygon and a small island, as a section of a branching cell would be.
  const bumpy = [];
  const n = 300;
  for (let i = 0; i < n; i += 1) {
    const a = (2 * Math.PI * i) / n;
    const r = 20 + 3 * Math.sin(7 * a) + next();
    bumpy.push([30 + r * Math.cos(a), 25 + r * Math.sin(a)]);
  }
  const flat = [];
  for (let i = 0; i < n; i += 1) {
    const p = bumpy[i];
    const q = bumpy[(i + 1) % n];
    flat.push(p[0], p[1], 0, q[0], q[1], 0);
  }
  const positions = concat(flat, circleSegments(2, 16, [70, 60]));
  const grid = S.buildSegmentGrid(positions);
  for (let k = 0; k < 400; k += 1) {
    const x = -40 + next() * 160;
    const y = -40 + next() * 140;
    const hit = S.nearestOnGrid(grid, x, y);
    close(hit.d, bruteNearest(positions, x, y), 1e-9, `(${x}, ${y})`);
    close(Math.hypot(hit.x - x, hit.y - y), hit.d, 1e-9, 'the reported point is at the reported distance');
  }
  assert.equal(S.nearestOnGrid(S.buildSegmentGrid(new Float64Array(0)), 1, 1), null);
});

test('inside is the even-odd rule: islands, holes, a point on a vertex row, the box outside', () => {
  const next = rng(5);
  const positions = concat(circleSegments(10, 90), circleSegments(4, 48), circleSegments(1.5, 24, [30, 0]));   // disc with a hole, and an island
  const grid = S.buildSegmentGrid(positions);
  assert.equal(S.insideContour(grid, 7, 0), true);          // in the ring
  assert.equal(S.insideContour(grid, 0, 0), false);         // in the hole
  assert.equal(S.insideContour(grid, 30, 0), true);         // on the island
  assert.equal(S.insideContour(grid, 15, 0), false);
  assert.equal(S.insideContour(grid, -50, 0), false);       // left of everything
  assert.equal(S.insideContour(grid, 0, 1000), false);
  for (let k = 0; k < 600; k += 1) {
    const x = -14 + next() * 50;
    const y = -14 + next() * 28;
    assert.equal(S.insideContour(grid, x, y), bruteInside(positions, x, y), `(${x}, ${y})`);
  }
  // A ray along a row of vertices counts each crossing once (the half-open rule).
  const diamond = Float64Array.from([-1, 0, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 0, -1, 0, 0, -1, 0, -1, 0, 0]);
  const dg = S.buildSegmentGrid(diamond);
  assert.equal(S.insideContour(dg, -0.5, 0), true);
  assert.equal(S.insideContour(dg, -3, 0), false);
  assert.equal(S.insideContour(dg, 0, 0), true);
});

function bruteWinding(positions, orient, x, y) {
  let w = 0;
  for (let s = 0; s < positions.length / 6; s += 1) {
    const y0 = positions[6 * s + 1];
    const y1 = positions[6 * s + 4];
    if ((y0 > y) === (y1 > y)) continue;
    const x0 = positions[6 * s];
    if (x0 + ((y - y0) * (positions[6 * s + 3] - x0)) / (y1 - y0) > x) w += orient[s] * (y1 > y0 ? 1 : -1);
  }
  return w;
}

test('with the sides of its segments the grid counts the bodies round a point: overlapping pieces are inside, a hole is not', () => {
  const next = rng(21);
  const same = (n, v) => new Int8Array(n).fill(v);
  // Two counterclockwise circles that overlap, each a body.
  const pieces = concat(circleSegments(4, 64, [0, 0]), circleSegments(4, 64, [3, 0]));
  const signed = S.buildSegmentGrid(pieces, same(128, 1));
  const parity = S.buildSegmentGrid(pieces);
  for (const [x, y] of [[1.5, 0], [1.5, 1.4], [2, -1]]) {
    assert.equal(S.insideContour(signed, x, y), true, `(${x}, ${y}) is in both pieces`);
    assert.equal(S.insideContour(parity, x, y), false, 'what the even-odd rule said of it');
  }
  for (const [x, y] of [[-2.5, 0], [5.5, 0], [0, 3]]) assert.equal(S.insideContour(signed, x, y), true, 'in one');
  for (const [x, y] of [[-9, 0], [0, 9], [8.5, 0]]) assert.equal(S.insideContour(signed, x, y), false, 'in none');
  // A body with a hole: the hole's circle is the same kind of polygon, but its solid is on the other side of it.
  const hollow = concat(circleSegments(10, 90), circleSegments(4, 48));
  const ring = S.buildSegmentGrid(hollow, Int8Array.from([...same(90, 1), ...same(48, -1)]));
  assert.equal(S.insideContour(ring, 7, 0), true);
  assert.equal(S.insideContour(ring, 0, 0), false, 'the hole is outside');
  // An island in the hole is a body again.
  const island = concat(hollow, circleSegments(1.5, 24, [0, 0]));
  assert.equal(S.insideContour(S.buildSegmentGrid(island, Int8Array.from([...same(90, 1), ...same(48, -1), ...same(24, 1)])), 0, 0), true);
  // A body turned inside out (its solid on the right of a counterclockwise circle) is nowhere inside: a negative count is no body.
  const inverted = S.buildSegmentGrid(circleSegments(5, 64), same(64, -1));
  assert.equal(S.insideContour(inverted, 0, 0), false);
  // Without sides, or with fewer than there are segments, it is the parity of old.
  assert.equal(S.insideContour(S.buildSegmentGrid(pieces, same(5, 1)), 1.5, 0), false);
  // The grid reads the same count as a scan of every segment, in a crowd of overlapping pieces and holes.
  const crowd = [];
  const sides = [];
  for (let k = 0; k < 14; k += 1) {
    const hole = k % 4 === 3;
    const part = circleSegments(1 + next() * 5, 24 + Math.floor(next() * 40), [next() * 20, next() * 20]);
    crowd.push(part);
    sides.push(...same(part.length / 6, hole ? -1 : 1));
  }
  const positions = concat(...crowd);
  const orient = Int8Array.from(sides);
  const grid = S.buildSegmentGrid(positions, orient);
  for (let k = 0; k < 800; k += 1) {
    const x = -8 + next() * 36;
    const y = -8 + next() * 36;
    assert.equal(S.insideContour(grid, x, y), bruteWinding(positions, orient, x, y) > 0, `(${x}, ${y})`);
  }
});

test('the grid and the scan agree on the contour of a real cut', () => {
  const mesh = uvSphere(8, 64, 32);
  const index = L.buildSliceIndex(mesh.vertices, mesh.faces);
  const set = S.contourSegments(index, 1.1);
  const grid = S.buildSegmentGrid(set.positions);
  const next = rng(3);
  for (let k = 0; k < 300; k += 1) {
    const x = -12 + next() * 24;
    const y = -12 + next() * 24;
    assert.equal(S.insideContour(grid, x, y), bruteInside(set.positions, x, y));
    close(S.nearestOnGrid(grid, x, y).d, bruteNearest(set.positions, x, y), 1e-9);
  }
});

// -- an annotation against the contour --------------------------------------------------------------------------------

test('a disc\'s rim samples lie on its circle; an outline\'s are spaced evenly along its boundary', () => {
  const disc = { kind: 'disc', x: 3, y: -2, r: 1.5 };
  const s = S.ringSamples(disc, 32);
  assert.equal(s.length, 64);
  for (let k = 0; k < 32; k += 1) close(Math.hypot(s[2 * k] - 3, s[2 * k + 1] + 2), 1.5, 1e-12);
  const square = { kind: 'outline', points: Float64Array.from([0, 0, 4, 0, 4, 4, 0, 4]) };
  const o = S.ringSamples(square, 16);
  assert.equal(o.length, 32);
  // 16 samples around a perimeter of 16 are one unit apart, starting at the first vertex.
  assert.deepEqual([o[0], o[1]], [0, 0]);
  assert.deepEqual([o[2], o[3]], [1, 0]);
  assert.deepEqual([o[8], o[9]], [4, 0]);
  assert.deepEqual([o[10], o[11]], [4, 1]);
  assert.equal(S.ringSamples({ kind: 'outline', points: Float64Array.from([1, 1, 1, 1, 1, 1]) }, 8).length, 0);
});

test('the gap is positive where the contour encloses the rim and negative where it does not', () => {
  const contour = S.buildSegmentGrid(circleSegments(5, 720));
  // Inside, concentric: every rim point 2 um inside the contour.
  const inner = S.annotationGap(contour, S.ringSamples({ kind: 'disc', x: 0, y: 0, r: 3 }));
  close(inner.meanSigned, 2, 0.01);
  assert.equal(inner.enclosedShare, 1);
  close(inner.maxAbs, 2, 0.01);
  // Outside, far: every rim point outside.
  const far = S.annotationGap(contour, S.ringSamples({ kind: 'disc', x: 12, y: 0, r: 1 }));
  assert.equal(far.enclosedShare, 0);
  assert.ok(far.meanSigned < -5 && far.meanSigned > -8, `${far.meanSigned}`);
  assert.ok(far.meanAbs > 5 && far.maxAbs >= far.meanAbs);
  // Straddling: some rim points each side; the share says how many.
  const straddle = S.annotationGap(contour, S.ringSamples({ kind: 'disc', x: 5, y: 0, r: 2 }));
  assert.ok(straddle.enclosedShare > 0.3 && straddle.enclosedShare < 0.7, `${straddle.enclosedShare}`);
  assert.ok(straddle.worst.signed !== 0);
  assert.ok(Math.abs(Math.hypot(straddle.worst.tx, straddle.worst.ty) - 5) < 0.01, 'the worst rim point\'s nearest contour point is on the contour');
  // Coincident: no gap at all, to the polygon's sagitta.
  const same = S.annotationGap(contour, S.ringSamples({ kind: 'disc', x: 0, y: 0, r: 5 }));
  close(same.meanAbs, 0, 0.002);
  // No contour on the section: no gap.
  assert.equal(S.annotationGap(S.buildSegmentGrid(new Float64Array(0)), S.ringSamples({ kind: 'disc', x: 0, y: 0, r: 1 })), null);
});

test('a disc in the hole of a ring-shaped section is outside the render, so its gap is negative', () => {
  const ring = S.buildSegmentGrid(concat(circleSegments(10, 360), circleSegments(4, 360)));
  const inHole = S.annotationGap(ring, S.ringSamples({ kind: 'disc', x: 0, y: 0, r: 2 }));
  assert.equal(inHole.enclosedShare, 0);
  close(inHole.meanSigned, -2, 0.01);
  const inBody = S.annotationGap(ring, S.ringSamples({ kind: 'disc', x: 7, y: 0, r: 1 }));
  assert.equal(inBody.enclosedShare, 1);
  // 7 +- 1 on the axis, the contour 3 to 4 from each rim point inwards and 2 to 4 from the other: positive, and far from 0.
  assert.ok(inBody.meanSigned > 2 && inBody.meanSigned < 3, `${inBody.meanSigned}`);
});

test('what is under a point: the smallest disc, an outline unless the point is in a cutout', () => {
  const square = [[0, 0], [10, 0], [10, 10], [0, 10]];
  const index = S.buildAnnotationIndex([node(1, 5, 20, 20, { r: 3 }), node(2, 5, 20, 20, { r: 1 }), node(3, 6, 20, 20)], [
    { location_id: 7, z_section: 5, z_um: 0.35, outline_um: square, cutouts_um: [[[4, 4], [6, 4], [6, 6], [4, 6]]] },
  ]);
  const sets = [{ index, key: 'a' }];
  assert.equal(S.pickAnnotation(sets, 5, 20.2, 20, 0).annotation.id, 2, 'the smaller of two discs');
  assert.equal(S.pickAnnotation(sets, 5, 22.5, 20, 0).annotation.id, 1);
  assert.equal(S.pickAnnotation(sets, 5, 23.2, 20, 0), null);
  assert.equal(S.pickAnnotation(sets, 5, 23.2, 20, 0.3).annotation.id, 1, 'a little slack at the edge');
  assert.equal(S.pickAnnotation(sets, 5, 2, 2, 0).annotation.id, 7);
  assert.equal(S.pickAnnotation(sets, 5, 5, 5, 0), null, 'the cutout is a hole');
  assert.equal(S.pickAnnotation(sets, 6, 2, 2, 0), null, 'another section');
});

// -- shift arrows and stages ------------------------------------------------------------------------------------------

test('arrows are binned by section, the head on the real position and the tail scaled back', () => {
  const nodes = [
    node(1, 10, 5, 5, { shift: [0.3, -0.4] }),
    node(2, 10, 8, 8, { shift: [0, 0] }),
    node(3, 12, 1, 2, { shift: [0.05, 0], live: [0.1, 0.2] }),
  ];
  const field = SH.prepareField(nodes, 'correction');
  const index = S.buildArrowIndex(field);
  assert.deepEqual(Array.from(index.keys()).sort(), [10, 12]);
  const [a] = S.arrowsAt(index, 10);
  assert.deepEqual([a.x1, a.y1], [5, 5]);
  close(a.x0, 4.7, 1e-12);
  close(a.y0, 5.4, 1e-12);
  assert.equal(a.id, 1);
  close(a.mag, 0.5, 1e-12);
  const big = S.buildArrowIndex(field, { scale: 10 });
  close(S.arrowsAt(big, 10)[0].x0, 5 - 3, 1e-12);
  assert.equal(S.arrowsAt(S.buildArrowIndex(field, { minUm: 0.1 }), 12).length, 0, 'a short move is left out');
  assert.deepEqual(S.arrowsAt(index, 99), []);
  const both = S.buildArrowIndex(SH.prepareField(nodes, 'both'));
  close(S.arrowsAt(both, 12)[0].x0, 1 - 0.15, 1e-12);
});

test('the stages of a copy: stored = xyz - live - shift, registered = xyz - shift, corrected = xyz', () => {
  const nodes = [node(1, 10, 10, 20, { shift: [0.3, -0.2], live: [0.5, 0.1] }), node(2, 11, 1, 2)];
  const stored = S.nodesAtStage(nodes, 'stored');
  const registered = S.nodesAtStage(nodes, 'registered');
  const corrected = S.nodesAtStage(nodes, 'corrected');
  close(stored[0].xyz_um[0], 10 - 0.5 - 0.3, 1e-12);
  close(stored[0].xyz_um[1], 20 - 0.1 + 0.2, 1e-12);
  close(registered[0].xyz_um[0], 9.7, 1e-12);
  assert.deepEqual(corrected[0].xyz_um, nodes[0].xyz_um);
  assert.equal(stored[0].xyz_um[2], nodes[0].xyz_um[2], 'Z does not move');
  assert.deepEqual(stored[1].xyz_um, nodes[1].xyz_um, 'a node that did not move');
  assert.notEqual(stored[0], nodes[0], 'the input is not changed');
  assert.deepEqual(nodes[0].xyz_um, [10, 20, 10 * 0.07]);
  assert.throws(() => S.nodesAtStage(nodes, 'later'), /unknown stage/);
});

test('a tooltip names the annotation with its volume and each surface\'s gap', () => {
  const set = { volume: 'Inferior', key: 'a' };
  const gap = { meanSigned: -0.234, maxAbs: 0.9, enclosedShare: 0.25, worst: { signed: -0.9 } };
  const lines = S.describeAnnotation(set, { id: 74420, section: 311, kind: 'disc', r: 1.25 }, [['Voxel remesh', gap], ['Original', null]]);
  assert.equal(lines[0], 'Inferior 74420');
  assert.ok(lines[1].includes('311'));
  assert.ok(lines.some((l) => l.includes('Voxel remesh') && l.includes('-0.23') && l.includes('25% of the rim inside')));
  assert.ok(lines.some((l) => l.includes('Original') && l.includes('no contour')));
});

// -- the canvas, against a fake DOM ------------------------------------------------------------------------------------------

/**
 * A recording 2D context: every call is kept as `[name, ...args]`, and a call that paints
 * (stroke, fill, fillText, strokeText) also carries `.state`, the style it painted with
 * (strokeStyle, fillStyle, lineWidth, globalAlpha, lineDash, font), because a canvas keeps
 * those as properties and a test that cannot see them cannot tell a red contour from a blue
 * one.  save and restore are real, as the code under test relies on them.
 */
function recordingContext() {
  const calls = [];
  const state = {
    strokeStyle: '#000000', fillStyle: '#000000', lineWidth: 1, globalAlpha: 1, lineDash: [],
    font: '10px sans-serif', textBaseline: 'alphabetic', lineCap: 'butt', lineJoin: 'miter',
  };
  const stack = [];
  const snapshot = () => ({ ...state, lineDash: [...state.lineDash] });
  const painting = new Set(['stroke', 'fill', 'fillText', 'strokeText']);
  const effects = {
    save() { stack.push(snapshot()); },
    restore() { Object.assign(state, stack.pop()); },
    setLineDash(dash) { state.lineDash = [...dash]; },
  };
  return new Proxy({ calls }, {
    get(t, name) {
      if (name === 'calls') return calls;
      if (name === 'state') return state;
      if (name in state) return name === 'lineDash' ? [...state.lineDash] : state[name];
      return (...args) => {
        const call = [name, ...args];
        if (painting.has(name)) call.state = snapshot();
        calls.push(call);
        effects[name]?.(...args);
      };
    },
    set(t, name, value) { state[name] = value; return true; },
  });
}

class El {
  constructor(tag, rect = { left: 0, top: 0, width: 0, height: 0 }) {
    this.tagName = tag;
    this.className = '';
    this.style = {};
    this.children = [];
    this.attrs = new Map();
    this.listeners = [];
    this.hidden = false;
    this.rect = rect;
    this._text = '';
    this.parent = null;
    this.tabIndex = -1;
  }

  set textContent(value) { this._text = String(value); }

  get textContent() { return this._text; }

  append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }

  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }

  setAttribute(k, v) { this.attrs.set(k, v); }

  addEventListener(type, fn, options) { this.listeners.push({ type, fn, options }); }

  removeEventListener(type, fn) { this.listeners = this.listeners.filter((l) => !(l.type === type && l.fn === fn)); }

  getBoundingClientRect() { return this.rect; }

  getContext() { return (this.ctx ||= recordingContext()); }

  focus() { El.focused = this; }

  setPointerCapture() {}

  releasePointerCapture() {}

  /** Deliver an event to this element's listeners; returns it (with `prevented` set by preventDefault). */
  fire(type, init = {}) {
    const event = { type, prevented: false, preventDefault() { this.prevented = true; }, target: this, ...init };
    for (const l of this.listeners.filter((x) => x.type === type)) l.fn(event);
    return event;
  }
}
El.focused = null;

/** A document that fails the test if anything puts a listener on it. */
const fakeDocument = {
  createElement: (tag) => new El(tag),
  addEventListener() { throw new Error('the Section view put a listener on the document'); },
};

/** A scheduler the test flushes by hand. */
function manualFrames() {
  const queue = [];
  return { schedule: (fn) => { queue.push(fn); return queue.length; }, flush() { while (queue.length) queue.shift()(); }, queue };
}

const THICKNESS = 0.07;

/**
 * A view of a cylinder cell (radius 5 around (20, 30), z 0..10) with discs on sections 20, 40, 60, 80.
 * `mesh` is another surface, `ghost` the neighbors drawn, `surface` more fields for `setSurface`.
 */
function makeView({ discs = null, callbacks = {}, size = { width: 600, height: 400 }, mesh = null, ghost = undefined, surface = {} } = {}) {
  const host = new El('div', { left: 0, top: 0, ...size });
  const frames = manualFrames();
  const view = new S.SliceView(host, callbacks, { document: fakeDocument, schedule: frames.schedule, ghost });
  const nodes = discs || [
    node(101, 20, 20, 30, { r: 5, thickness: THICKNESS }),      // the surface's own radius: no gap
    node(102, 40, 20, 30, { r: 3, thickness: THICKNESS }),      // 2 um inside
    node(103, 60, 23.5, 30, { r: 2.5, thickness: THICKNESS }),  // straddles the surface (23.5 + 2.5 > 25)
    node(104, 60, 40, 30, { r: 1, thickness: THICKNESS }),      // far outside
    node(105, 80, 20, 30, { r: 4.6, thickness: THICKNESS }),
  ];
  view.setAnnotations('cell', { nodes, volume: 'Inf', color: '#7aa2ff' });
  const shape = mesh || cylinder(5, 10, 96, 4, [20, 30]);
  view.setSurface('L1', { vertices: shape.vertices, faces: shape.faces, name: 'Voxel remesh', color: '#ffd400', ...surface });
  frames.flush();
  return { view, host, frames, canvas: view.canvas };
}

const strokesOf = (view) => view.canvas.getContext().calls.filter((c) => c[0] === 'stroke').length;

test('the view builds its canvas, fits a z map from the annotations and starts mid-stack', () => {
  const { view, host } = makeView();
  assert.equal(host.children[0], view.canvas);
  assert.equal(view.canvas.tabIndex, 0, 'a canvas that can take focus');
  assert.deepEqual(Array.from(view.sections), [20, 40, 60, 80]);
  assert.equal(view.section, 60);
  close(view.zMap.dz, THICKNESS, 1e-12);
  close(view.z, 60 * THICKNESS, 1e-9);
  assert.equal(view.canvas.width, 600);
  assert.equal(view.canvas.height, 400);
});

test('listeners sit on the canvas alone: none on the document or the window, and wheel is not passive', () => {
  const { view } = makeView();     // the fake document throws if anything is added to it
  assert.equal(typeof window, 'undefined');
  const types = new Set(view.canvas.listeners.map((l) => l.type));
  for (const t of ['keydown', 'wheel', 'pointerdown', 'pointermove', 'pointerup', 'dblclick']) assert.ok(types.has(t), t);
  assert.deepEqual(view.canvas.listeners.find((l) => l.type === 'wheel').options, { passive: false });
});

test('keys on the canvas move the section and tell the host; other elements\' keys do nothing', () => {
  const heard = [];
  const { view, canvas } = makeView({ callbacks: { onSection: (s) => heard.push(s) } });
  assert.equal(canvas.fire('keydown', key('ArrowUp')).prevented, true);
  assert.equal(view.section, 80);
  canvas.fire('keydown', key('ArrowUp'));                                // the end: nothing changes
  canvas.fire('keydown', key('ArrowDown'));
  canvas.fire('keydown', key('ArrowDown'));
  assert.equal(view.section, 40);
  canvas.fire('keydown', key('ArrowRight', { shiftKey: true }));
  assert.equal(view.section, 50);
  canvas.fire('keydown', key('Home'));
  assert.equal(view.section, 20);
  assert.deepEqual(heard, [80, 60, 40, 50, 20], 'one call per change, none for a key that changed nothing');
  // A text box next to the canvas gets the same keys and the view does not hear them.
  const input = new El('input');
  input.fire('keydown', key('End'));
  assert.equal(view.section, 20);
  // A chord and a key that is not ours are left to the page.
  assert.equal(canvas.fire('keydown', key('ArrowUp', { ctrlKey: true })).prevented, false);
  assert.equal(canvas.fire('keydown', key('a')).prevented, false);
  assert.equal(view.section, 20);
});

test('setSection from outside changes the section without calling back, and stays within the annotated range', () => {
  const heard = [];
  const { view } = makeView({ callbacks: { onSection: (s) => heard.push(s) } });
  view.setSection(40);
  assert.equal(view.section, 40);
  view.setSection(5000);
  assert.equal(view.section, 80);
  view.setSection(-4);
  assert.equal(view.section, 20);
  view.setSection(NaN);
  assert.equal(view.section, 20);
  assert.deepEqual(heard, []);
  view.setSection(60, { notify: true });
  assert.deepEqual(heard, [60]);
});

test('the wheel zooms about the pointer and does not scroll the page; a drag pans; double-click fits again', () => {
  const views = [];
  const { view, canvas } = makeView({ callbacks: { onView: (v) => views.push(v) } });
  const start = view.getView();
  const at = [150, 90];
  const before = S.screenToWorld(start, { width: 600, height: 400 }, ...at);
  const wheel = canvas.fire('wheel', { clientX: at[0], clientY: at[1], deltaY: -100, deltaMode: 0 });
  assert.equal(wheel.prevented, true);
  const zoomed = view.getView();
  close(zoomed.scale, start.scale * S.ZOOM_STEP, 1e-9);
  const after = S.screenToWorld(zoomed, { width: 600, height: 400 }, ...at);
  close(after[0], before[0], 1e-9);
  close(after[1], before[1], 1e-9);
  // A drag of (60, -20) pixels moves the content that far.
  canvas.fire('pointerdown', { clientX: 300, clientY: 200, button: 0, pointerId: 1 });
  canvas.fire('pointermove', { clientX: 360, clientY: 180, pointerId: 1 });
  canvas.fire('pointerup', { clientX: 360, clientY: 180, pointerId: 1 });
  const dragged = view.getView();
  close(dragged.cx, zoomed.cx - 60 / zoomed.scale, 1e-9);
  close(dragged.cy, zoomed.cy + 20 / zoomed.scale, 1e-9);
  assert.ok(views.length >= 2, 'the host hears each change of view');
  // Resizing does not undo a view the user chose; a double-click does.
  canvas.fire('dblclick');
  const refit = view.getView();
  const content = S.fitView(view._contentBox(), { width: 600, height: 400 });
  assert.deepEqual(refit, content, 'fitted to what is on the section');
  assert.ok(Math.abs(refit.scale - dragged.scale) > 1e-6);
});

test('a view nobody has touched follows the content from section to section; one that was panned or zoomed stays', () => {
  const { view } = makeView();
  const size = { width: 600, height: 400 };
  assert.equal(view.section, 60);
  const at60 = view.getView();
  assert.deepEqual(at60, S.fitView(view._contentBox(), size));
  view.setSection(20);                                    // one disc and the cut of the cylinder: a smaller box
  const at20 = view.getView();
  assert.notDeepEqual(at20, at60);
  assert.deepEqual(at20, S.fitView(view._contentBox(), size));
  // Once the user has set a view, it is theirs.
  view.setView({ cx: 3, cy: 4, scale: 9 });
  view.setSection(80);
  assert.deepEqual(view.getView(), { cx: 3, cy: 4, scale: 9 });
  // A double-click gives it back to the content.
  view.canvas.fire('dblclick');
  assert.deepEqual(view.getView(), S.fitView(view._contentBox(), size));
  view.setSection(40);
  assert.deepEqual(view.getView(), S.fitView(view._contentBox(), size));
});

test('the keyboard zooms and resets like the wheel and the double-click', () => {
  const { view, canvas } = makeView();
  const start = view.getView();
  canvas.fire('keydown', key('+'));
  close(view.getView().scale, start.scale * S.ZOOM_STEP, 1e-9);
  assert.deepEqual([view.getView().cx, view.getView().cy], [start.cx, start.cy], 'about the center');
  canvas.fire('keydown', key('-'));
  close(view.getView().scale, start.scale, 1e-9);
  canvas.fire('keydown', key('+'));
  canvas.fire('keydown', key('0'));
  close(view.getView().scale, start.scale, 1e-9);
});

test('the view can be set from another canvas under Sync', () => {
  const { view } = makeView();
  view.setView({ cx: 1, cy: 2, scale: 33 });
  assert.deepEqual(view.getView(), { cx: 1, cy: 2, scale: 33 });
  view.setView({ cx: 1, cy: 2, scale: 1e12 });
  assert.equal(view.getView().scale, S.VIEW_LIMITS.maxScale);
});

test('a hover names the annotation with its volume and gap; a click hands it to the host', () => {
  const selected = [];
  const hovered = [];
  const { view, canvas, frames } = makeView({ callbacks: { onSelect: (i) => selected.push(i), onHover: (i) => hovered.push(i) } });
  view.setSection(60);
  frames.flush();
  // The disc 104 (40, 30) is 1 um in radius: find its screen position.
  const [px, py] = S.worldToScreen(view.getView(), { width: 600, height: 400 }, 23.5, 30);
  canvas.fire('pointermove', { clientX: px, clientY: py });
  assert.equal(view.tip.hidden, false);
  const text = view.tip.textContent;
  assert.ok(text.startsWith('Inf 103'), text);
  assert.ok(text.includes('section 60') && text.includes('Voxel remesh') && text.includes('mean gap'), text);
  assert.equal(hovered.at(-1).id, 103);
  canvas.fire('pointerdown', { clientX: px, clientY: py, button: 0, pointerId: 2 });
  canvas.fire('pointerup', { clientX: px, clientY: py, pointerId: 2 });
  assert.equal(selected.length, 1);
  assert.equal(selected[0].volume, 'Inf');
  assert.equal(selected[0].id, 103);
  assert.equal(selected[0].section, 60);
  assert.equal(selected[0].kind, 'disc');
  // Leaving the canvas clears it; a drag is not a click.
  canvas.fire('pointerleave');
  assert.equal(view.tip.hidden, true);
  assert.equal(hovered.at(-1), null);
  canvas.fire('pointerdown', { clientX: px, clientY: py, button: 0, pointerId: 3 });
  canvas.fire('pointermove', { clientX: px + 40, clientY: py, pointerId: 3 });
  canvas.fire('pointerup', { clientX: px + 40, clientY: py, pointerId: 3 });
  assert.equal(selected.length, 1);
});

test('the gap of each disc against the cylinder is what the geometry says', () => {
  const { view } = makeView();
  const set = view.sets.get('cell');
  const surface = view.surfaces.get('L1');
  const gapOf = (section, id) => {
    view.setSection(section);
    const d = S.annotationsAt(set.index, section).discs.find((x) => x.id === id);
    return view._gapOf(surface, set, d);
  };
  close(gapOf(20, 101).meanAbs, 0, 0.02);            // the surface's own radius
  close(gapOf(40, 102).meanSigned, 2, 0.02);         // 2 um inside
  assert.equal(gapOf(40, 102).enclosedShare, 1);
  const straddle = gapOf(60, 103);
  close(straddle.enclosedShare, 0.62, 0.1);          // the rim is inside for the angles whose radius from the axis is under 5
  assert.ok(straddle.meanSigned > -2 && straddle.meanSigned < 2);
  const far = gapOf(60, 104);
  assert.equal(far.enclosedShare, 0);
  close(far.meanSigned, -15, 0.1);                   // centered 20 um from the axis, 5 um of surface in between
});

test('drawing strokes the contour and the annotations, and a surface that is hidden is not drawn', () => {
  const { view, frames } = makeView();
  const drawn = strokesOf(view);
  assert.ok(drawn > 3, `strokes ${drawn}`);
  view.canvas.getContext().calls.length = 0;
  view.setSurface('L1', { vertices: view.surfaces.get('L1').vertices, faces: view.surfaces.get('L1').faces, name: 'Voxel remesh', visible: false });
  frames.flush();
  assert.ok(strokesOf(view) < drawn, 'less to draw without the surface');
  assert.ok(view.canvas.getContext().calls.some((c) => c[0] === 'fillRect'), 'the background is still painted');
});

test('a painted contour is drawn in several colors of the ramp, a solid one in its layer color', () => {
  const { view, frames } = makeView();
  const surface = view.surfaces.get('L1');
  const nv = surface.vertices.length / 3;
  const values = Float64Array.from({ length: nv }, (_, i) => (surface.vertices[3 * i + 2] / 10) * 0.8);      // 0 to 0.8 um up the cylinder
  view.setScale(0.8);
  view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, values, name: 'Voxel remesh', color: '#ffd400' });
  view.canvas.getContext().calls.length = 0;
  frames.flush();
  view.draw();
  const ctx = view.canvas.getContext();
  assert.ok(ctx.calls.filter((c) => c[0] === 'stroke').length >= 2);
  // The strokeStyle assignments are not recorded as calls; the grouping is what picks the colors.
  const groups = S.bucketByError(view._contour(view.surfaces.get('L1')).set.values, view._contour(view.surfaces.get('L1')).set.count, 0.8);
  assert.ok(groups.length >= 1);
});

test('a view that is not active draws nothing until it is shown again', () => {
  const { view, frames } = makeView();
  view.setActive(false);
  view.canvas.getContext().calls.length = 0;
  view.setSection(20);
  frames.flush();
  assert.equal(view.canvas.getContext().calls.length, 0, 'no drawing while out of sight');
  view.setActive(true);
  frames.flush();
  assert.ok(view.canvas.getContext().calls.some((c) => c[0] === 'fillRect'));
});

test('the arrow hook is asked for the section and drawn', () => {
  const { view, frames } = makeView();
  const asked = [];
  const field = SH.prepareField([node(101, 60, 22, 30, { shift: [0.4, 0.2] })], 'correction');
  const index = S.buildArrowIndex(field);
  view.setArrows((section) => { asked.push(section); return S.arrowsAt(index, section); });
  view.canvas.getContext().calls.length = 0;
  frames.flush();
  assert.ok(asked.includes(60));
  const before = strokesOf(view);
  view.setSection(40);
  frames.flush();
  assert.ok(asked.includes(40));
  assert.ok(before >= 1);
});

test('a section with no annotation says where the nearest ones are, and a surface that does not reach it', () => {
  const { view, frames } = makeView();
  view.setSection(50);
  view.canvas.getContext().calls.length = 0;
  frames.flush();
  const text = view.canvas.getContext().calls.filter((c) => c[0] === 'fillText').map((c) => c[1]).join('\n');
  assert.ok(text.includes('Section 50'), text);
  assert.ok(text.includes('none here') && text.includes('40 below') && text.includes('60 above'), text);
  // Beyond the cylinder (z 0..10) the surface does not reach.
  const sparse = makeView({ discs: [node(1, 20, 20, 30, { r: 1 }), node(2, 200, 20, 30, { r: 1 })] });
  sparse.view.setSection(200);
  sparse.view.canvas.getContext().calls.length = 0;
  sparse.frames.flush();
  const note = sparse.view.canvas.getContext().calls.filter((c) => c[0] === 'fillText').map((c) => c[1]).join('\n');
  assert.ok(note.includes('does not reach this section'), note);
});

// -- a pane made before its data, a view shared between panes, a host with a border ---------------------------------------

/** An empty view in a 600 x 400 host, and what it tells. */
function emptyView(callbacks = {}) {
  const host = new El('div', { left: 0, top: 0, width: 600, height: 400 });
  const frames = manualFrames();
  const heard = { section: [], view: [] };
  const view = new S.SliceView(host, {
    onSection: (s) => heard.section.push(s), onView: (v, opts) => heard.view.push([v, opts]), ...callbacks,
  }, { document: fakeDocument, schedule: frames.schedule });
  return { view, host, frames, heard, canvas: view.canvas };
}
/** Discs on every `step`th section from `from` to `to`. */
const discsOn = (from, to, step = 10) => Array.from({ length: (to - from) / step + 1 }, (_, i) => node(100 + i, from + i * step, 20, 30, { r: 2 }));

test('a section asked for before the annotations arrive is applied when they do, within their range', () => {
  // The natural order is the pane first and its data a fetch later.
  let { view } = emptyView();
  view.setSection(700);
  assert.equal(view.section, null, 'nothing to clamp it to yet: it is not pinned to 0');
  view.setAnnotations('cell', { nodes: discsOn(600, 800) });
  assert.equal(view.section, 700);
  assert.deepEqual(Array.from(view.sections).slice(0, 2), [600, 610]);
  // Beyond the range it is the nearest end; the last request before the data is the one that counts.
  ({ view } = emptyView());
  view.setSection(5000);
  view.setAnnotations('cell', { nodes: discsOn(600, 800) });
  assert.equal(view.section, 800);
  ({ view } = emptyView());
  view.setSection(10);
  view.setSection(650);
  view.setAnnotations('cell', { nodes: discsOn(600, 800) });
  assert.equal(view.section, 650);
  // Without a request, the middle of the stack, as ever; a request is used once.
  ({ view } = emptyView());
  view.setAnnotations('cell', { nodes: discsOn(600, 800) });
  assert.equal(view.section, 700);
  view.setSection(610);
  view.setAnnotations('cell', { nodes: discsOn(600, 800) });
  assert.equal(view.section, 610, 'the section the pane is at stays through more data');
  // The data of another cell: the old section means nothing in the new range; one that is still in range stays.
  ({ view } = emptyView());
  view.setAnnotations('cell', { nodes: discsOn(100, 200) });
  view.setSection(150);
  view.setAnnotations('cell', { nodes: discsOn(500, 600) });
  assert.equal(view.section, 550, 'the middle of the new range');
  view.setSection(580);
  view.setAnnotations('cell', { nodes: discsOn(560, 600) });
  assert.equal(view.section, 580);
});

test('with no annotated section the section keys have nowhere to go and say nothing', () => {
  const { view, canvas, heard } = emptyView();
  for (const k of ['ArrowUp', 'ArrowDown', 'ArrowRight', 'Home', 'End', 'PageUp']) {
    assert.equal(canvas.fire('keydown', key(k)).prevented, true, `${k} is the canvas's`);
  }
  assert.equal(view.section, null, 'no section is made up');
  assert.deepEqual(heard.section, [], 'and no host is told one');
  // A host that shares the section is not pulled to 0 by a key on an empty pane; the zoom keys still work.
  canvas.fire('keydown', key('+'));
  assert.equal(heard.view.length, 1);
});

test('a view that follows the content tells onView when it moves by itself, so a shared view cannot drift', () => {
  const { view, canvas, heard } = emptyView();
  view.setAnnotations('cell', { nodes: [node(1, 10, 20, 30, { r: 2 }), node(2, 20, 120, 80, { r: 5 })] });
  heard.view.length = 0;
  view.setSection(10);
  assert.equal(heard.view.length, 1, 'a new section, a new box');
  assert.deepEqual(heard.view[0][1], { auto: true });
  assert.deepEqual(heard.view[0][0], view.getView());
  view.setSection(10);
  assert.equal(heard.view.length, 1, 'the same section: the view did not move');
  view.setSection(20);
  assert.equal(heard.view.length, 2);
  // A gesture says so without the flag; one reset is one call; the host's own setView is not echoed.
  canvas.fire('wheel', { clientX: 300, clientY: 200, deltaY: -100, deltaMode: 0 });
  assert.equal(heard.view.length, 3);
  assert.equal(heard.view[2][1], undefined);
  canvas.fire('dblclick');
  assert.equal(heard.view.length, 4, 'once, not once for the refit and once for the reset');
  assert.equal(heard.view[3][1], undefined);
  view.setView({ cx: 3, cy: 4, scale: 9 });
  view.setSection(10);
  assert.equal(heard.view.length, 4, 'a view the user set keeps its framing and has nothing to tell');
  // A new size is a new fit.
  canvas.fire('dblclick');
  const before = heard.view.length;
  view.host.rect = { left: 0, top: 0, width: 900, height: 500 };
  view.resize();
  assert.equal(heard.view.length, before + 1);
  assert.deepEqual(heard.view.at(-1)[1], { auto: true });
});

test('two panes that share a view through onView stay together while one of them follows its content', () => {
  const other = makeView({ discs: [node(1, 20, 20, 30, { r: 1 }), node(2, 40, 21, 31, { r: 2 })] });
  const own = makeView({ callbacks: { onView: (v) => other.view.setView(v) } });
  assert.deepEqual(other.view.getView(), own.view.getView(), 'the sharing began with the first view');
  for (const section of [80, 20, 40, 60]) {
    own.view.setSection(section);
    assert.deepEqual(other.view.getView(), own.view.getView(), `after section ${section}`);
  }
  own.canvas.fire('keydown', key('ArrowDown'));
  own.canvas.fire('keydown', key('ArrowDown'));
  assert.deepEqual(other.view.getView(), own.view.getView(), 'after two keys');
});

test('the canvas takes the host\'s content box, not its border box: a host with a border and padding does not overflow', () => {
  const had = globalThis.getComputedStyle;
  globalThis.getComputedStyle = () => ({ paddingLeft: '6px', paddingRight: '6px', paddingTop: '6px', paddingBottom: '6px' });
  try {
    const host = new El('div', { left: 0, top: 0, width: 400, height: 300 });     // the border box, with 3 px borders
    host.clientWidth = 394;                                                         // inside the borders: padding and content
    host.clientHeight = 294;
    const view = new S.SliceView(host, {}, { document: fakeDocument, schedule: manualFrames().schedule });
    assert.equal(view.canvas.style.width, '382px');
    assert.equal(view.canvas.style.height, '282px');
    assert.equal(view.canvas.width, 382);
    assert.equal(view.canvas.height, 282);
    host.rect = { left: 0, top: 0, width: 800, height: 600 };
    host.clientWidth = 794;
    host.clientHeight = 594;
    view.resize();
    assert.equal(view.canvas.style.width, '782px');
    // A host without a client size (not laid out, or not an element) gives the box it has.
    host.clientWidth = 0;
    host.clientHeight = 0;
    view.resize();
    assert.equal(view.canvas.style.width, '800px');
    // A page with no computed style gives the box it has as well.
    host.clientWidth = 794;
    host.clientHeight = 594;
    delete globalThis.getComputedStyle;
    host.rect = { left: 0, top: 0, width: 640, height: 480 };
    view.resize();
    assert.equal(view.canvas.style.width, '640px');
  } finally {
    if (had) globalThis.getComputedStyle = had; else delete globalThis.getComputedStyle;
  }
});

// -- what the canvas is told to draw ----------------------------------------------------------------------------------------
//
// The context keeps the style of every stroke, fill and text it is given, so these say what is on the canvas: the
// color of a contour, the number beside a circle, the dashes of a neighbor, and not only that something was stroked.

/** Draw now and return the calls of that one frame. */
function paint(view) {
  const ctx = view.canvas.getContext();
  ctx.calls.length = 0;
  view.draw();
  return ctx.calls.slice();
}

/** The strokes of a frame whose style satisfies `test`. */
const strokes = (calls, test2) => calls.filter((c) => c[0] === 'stroke' && test2(c.state));
/** The contour's strokes: 2 px, round caps (the arrows are 1.5 px, a circle 1.6 and 4, a ghost 1). */
const contourStrokes = (calls) => strokes(calls, (st) => st.lineWidth === 2 && st.lineCap === 'round');
/** The circles' own strokes (the second of the two each gets; the first is its halo, 4 px). */
const circleStrokes = (calls) => strokes(calls, (st) => st.lineWidth === 1.6);
/** The numbers drawn beside circles: a sign and two decimals. */
const gapLabels = (calls) => calls.filter((c) => c[0] === 'fillText' && /^[+−]\d+\.\d\d$/.test(c[1])).map((c) => c[1]);
const labelOf = (gap) => `${gap.meanSigned >= 0 ? '+' : '−'}${Math.abs(gap.meanSigned).toFixed(2)}`;

/** The closed polygons of the last fill: [[x, y, x, y, ...], ...] in canvas pixels. */
function fillPolygons(calls) {
  const at = calls.findLastIndex((c) => c[0] === 'fill');
  assert.ok(at >= 0, 'something was filled');
  let begin = at;
  while (begin > 0 && calls[begin][0] !== 'beginPath') begin -= 1;
  const polygons = [];
  for (let i = begin; i < at; i += 1) {
    const [name, x, y] = calls[i];
    if (name === 'moveTo') polygons.push([x, y]);
    else if (name === 'lineTo') polygons.at(-1).push(x, y);
  }
  return polygons;
}
const polygonSide = (p) => Math.sign(p.reduce((sum, _, k) => (k % 2 ? sum : sum + p[k] * p[(k + 3) % p.length] - p[(k + 2) % p.length] * p[k + 1]), 0));

test('a disc in the overlap of two closed pieces is inside the render, and the gap there is fat: parity called it outside', () => {
  const pieces = joined(cylinder(4, 10, 64, 4, [20, 30]), cylinder(4, 10, 64, 4, [23, 30]));
  const { view, canvas } = makeView({
    mesh: pieces,
    discs: [
      node(1, 60, 21.5, 30, { r: 1 }),       // in both pieces: the render claims it twice over
      node(2, 60, 17, 30, { r: 0.5 }),       // in the first piece only
      node(3, 60, 40, 30, { r: 1 }),         // in neither
    ],
  });
  const set = view.sets.get('cell');
  const surface = view.surfaces.get('L1');
  const here = S.annotationsAt(set.index, 60).discs;
  const gap = (id) => view._gapOf(surface, set, here.find((d) => d.id === id));
  assert.equal(gap(1).enclosedShare, 1, 'every rim point of it is inside the render');
  assert.ok(gap(1).meanSigned > 1, `fat: ${gap(1).meanSigned}`);
  assert.equal(gap(2).enclosedShare, 1);
  assert.equal(gap(3).enclosedShare, 0);
  assert.ok(gap(3).meanSigned < -5);
  // The same contour without its sides is the parity grid the section view used to read.
  const cut = view._contour(surface).set;
  const old = S.annotationGap(S.buildSegmentGrid(cut.positions), S.ringSamples(here.find((d) => d.id === 1)));
  assert.equal(old.enclosedShare, 0, 'parity put the overlap outside');
  // The hover says so, and the circle is stroked in the fat color.
  const [px, py] = S.worldToScreen(view.getView(), { width: 600, height: 400 }, 21.5, 30);
  canvas.fire('pointermove', { clientX: px, clientY: py });
  assert.ok(view.tip.textContent.includes('100% of the rim inside'), view.tip.textContent);
  const calls = paint(view);
  const strokesInOrder = circleStrokes(calls).map((c) => c.state.strokeStyle);
  assert.equal(strokesInOrder[0], S.gapColor(gap(1).meanSigned, view.scaleUm));
});

test('a disc in a cavity is outside the render and the wall around it inside: the cavity is a hole in the count', () => {
  const wall = joined(cylinder(5, 10, 64, 4, [20, 30]), insideOut(lifted(cylinder(2, 6, 64, 3, [20, 30]), 2)));
  const { view } = makeView({
    mesh: wall,
    discs: [node(1, 60, 20, 30, { r: 1 }), node(2, 60, 23.5, 30, { r: 0.5 }), node(3, 20, 20, 30, { r: 1 })],
  });
  const set = view.sets.get('cell');
  const surface = view.surfaces.get('L1');
  const gap = (section, id) => {
    view.setSection(section);
    return view._gapOf(surface, set, S.annotationsAt(set.index, section).discs.find((d) => d.id === id));
  };
  assert.equal(gap(60, 1).enclosedShare, 0, 'in the cavity');
  assert.ok(gap(60, 1).meanSigned < 0);
  assert.equal(gap(60, 2).enclosedShare, 1, 'in the wall around it');
  // Below the cavity (z 1.4 against its 2..8) the same disc is inside the solid.
  assert.equal(gap(20, 3).enclosedShare, 1);
});

test('the surface is tinted by one nonzero fill over the closed loops, which run the way their sides say', () => {
  const { view } = makeView();
  let calls = paint(view);
  const fills = calls.filter((c) => c[0] === 'fill');
  assert.equal(fills.length, 1, 'one fill, not one for each loop');
  assert.deepEqual(fills[0].slice(1), ['nonzero']);
  assert.equal(fills[0].state.fillStyle, '#ffd400', 'in the layer color');
  assert.equal(fills[0].state.globalAlpha, 0.1, 'at a tenth of its opacity');
  assert.equal(fillPolygons(calls).length, 1);
  // The layer's opacity scales it; fill: false takes it away.
  const surface = view.surfaces.get('L1');
  view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, name: 'Voxel remesh', color: '#ffd400', alpha: 0.5 });
  assert.equal(paint(view).find((c) => c[0] === 'fill').state.globalAlpha, 0.05);
  view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, name: 'Voxel remesh', color: '#ffd400', fill: false });
  calls = paint(view);
  assert.equal(calls.filter((c) => c[0] === 'fill').length, 0);
  // Two pieces that overlap are two loops run the same way, so their overlap is filled and not a hole ...
  const pieces = makeView({ mesh: joined(cylinder(4, 10, 64, 4, [20, 30]), cylinder(4, 10, 64, 4, [23, 30])) });
  const both = fillPolygons(paint(pieces.view));
  assert.equal(both.length, 2);
  assert.equal(polygonSide(both[0]), polygonSide(both[1]), 'the same way round');
  assert.notEqual(polygonSide(both[0]), 0);
  // ... and a cavity is a loop run the other way, which takes its place out of the wall's winding.
  const hollow = makeView({ mesh: joined(cylinder(5, 10, 64, 4, [20, 30]), insideOut(lifted(cylinder(2, 6, 64, 3, [20, 30]), 2))) });
  const rings = fillPolygons(paint(hollow.view));
  assert.equal(rings.length, 2);
  assert.equal(polygonSide(rings[0]) * polygonSide(rings[1]), -1, 'opposite ways round');
});

test('a painted contour is stroked in the colors of the error ramp, a solid one in its layer color', () => {
  const { view } = makeView();
  const surface = view.surfaces.get('L1');
  const solid = contourStrokes(paint(view));
  assert.equal(solid.length, 1, 'one path for a contour of one color');
  assert.equal(solid[0].state.strokeStyle, '#ffd400');
  assert.equal(solid[0].state.globalAlpha, 1);
  // Painted by error: 0 at the cylinder's west side to 0.8 um at its east side, on a 0.8 um scale.
  const nv = surface.vertices.length / 3;
  const rising = Float64Array.from({ length: nv }, (_, i) => ((surface.vertices[3 * i] - 15) / 10) * 0.8);
  view.setScale(0.8);
  view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, values: rising, name: 'Voxel remesh', color: '#ffd400' });
  const painted = contourStrokes(paint(view));
  const cut = view._contour(view.surfaces.get('L1')).set;
  const groups = S.bucketByError(cut.values, cut.count, 0.8);
  assert.ok(painted.length >= 2, 'several colors');
  assert.deepEqual(painted.map((c) => c.state.strokeStyle), groups.map((g) => g.color));
  assert.ok(painted.every((c) => c.state.strokeStyle !== '#ffd400'), 'none in the layer\'s color');
  // The colors are the ramp's own: no error is its light gray, the scale is its red.
  const constant = (v) => {
    view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, values: new Float64Array(nv).fill(v), name: 'Voxel remesh' });
    return contourStrokes(paint(view)).map((c) => c.state.strokeStyle);
  };
  assert.deepEqual(constant(0), [S.errorCss(0, 0.8)]);
  assert.deepEqual(constant(0.8), [S.errorCss(0.8, 0.8)]);
  assert.deepEqual(constant(5), [S.errorCss(0.8, 0.8)], 'beyond the scale it stays red');
  assert.notEqual(S.errorCss(0, 0.8), S.errorCss(0.8, 0.8));
  // The layer's opacity is the contour's.
  view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, name: 'Voxel remesh', color: '#ffd400', alpha: 0.4 });
  assert.equal(contourStrokes(paint(view))[0].state.globalAlpha, 0.4);
});

test('a circle big enough on screen carries its signed gap as a number beside it, a small one does not', () => {
  const { view } = makeView();
  const set = view.sets.get('cell');
  const surface = view.surfaces.get('L1');
  const discs = S.annotationsAt(set.index, 60).discs;
  const expected = discs.map((d) => labelOf(view._gapOf(surface, set, d)));
  assert.equal(expected.length, 2);
  assert.ok(expected.every((text) => /^[+−]\d+\.\d\d$/.test(text)));
  assert.deepEqual(gapLabels(paint(view)).sort(), expected.sort());
  // A fat gap reads with a plus, a short one with a real minus.
  const far = view._gapOf(surface, set, discs.find((d) => d.id === 104));
  assert.ok(labelOf(far).startsWith('−'), labelOf(far));
  // Zoomed out so that no circle is 10 px across its radius, there are no numbers.
  view.setView({ cx: 20, cy: 30, scale: 2 });
  assert.deepEqual(gapLabels(paint(view)), []);
  // Zoomed in the numbers sit at the circle's right edge.
  view.setView({ cx: 20, cy: 30, scale: 30 });
  const calls = paint(view);
  assert.equal(gapLabels(calls).length, 2);
  const label = calls.find((c) => c[0] === 'fillText' && c[1] === labelOf(view._gapOf(surface, set, discs.find((d) => d.id === 103))));
  close(label[2], 300 + (23.5 - 20) * 30 + 2.5 * 30 + 5, 1e-9, 'x: the circle\'s right edge and 5 px');
  close(label[3], 200, 1e-9, 'y: the circle\'s center');
});

test('a circle is stroked in the color of its gap, red where the render is fat and blue where it stops short', () => {
  const { view } = makeView();
  const set = view.sets.get('cell');
  const surface = view.surfaces.get('L1');
  const rgb = (css) => css.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number);
  // Section 40: disc 102 is 2 um inside the cylinder (fat).  Section 60: disc 104 is far outside (short).
  const colorAt = (section, id) => {
    view.setSection(section);
    const gap = view._gapOf(surface, set, S.annotationsAt(set.index, section).discs.find((d) => d.id === id));
    const shapes = S.annotationsAt(set.index, section).discs;
    const strokesNow = circleStrokes(paint(view));
    return { gap, color: strokesNow[shapes.findIndex((d) => d.id === id)].state.strokeStyle };
  };
  const fat = colorAt(40, 102);
  const short = colorAt(60, 104);
  assert.ok(fat.gap.meanSigned > 1 && short.gap.meanSigned < -5);
  assert.equal(fat.color, S.gapColor(fat.gap.meanSigned, view.scaleUm));
  assert.equal(short.color, S.gapColor(short.gap.meanSigned, view.scaleUm));
  const [fr, , fb] = rgb(fat.color);
  const [sr, , sb] = rgb(short.color);
  assert.ok(fr > fb && sb > sr, `red for fat ${fat.color}, blue for short ${short.color}`);
  assert.notEqual(fat.color, short.color);
  // The set's own color is the halo under every circle, 4 px wide and half transparent.
  const halos = strokes(paint(view), (st) => st.lineWidth === 4);
  assert.ok(halos.length >= 1 && halos.every((c) => c.state.strokeStyle === '#7aa2ff' && c.state.globalAlpha === 0.45));
  // The error scale moves the colors; a scale that is not a positive number is ignored.
  const before = colorAt(60, 104).color;
  view.setScale(60);
  assert.notEqual(colorAt(60, 104).color, before, 'the same gap is paler on a longer scale');
  for (const bad of [0, -1, NaN, 'x']) { view.setScale(bad); assert.equal(view.scaleUm, 60); }
});

test('the neighbor sections are ghosted: dashed, faint and fainter the farther, in the set\'s color; none with ghost 0', () => {
  const { view } = makeView();                        // section 60: 40 and 80 are 20 sections away
  const dashed = (calls) => strokes(calls, (st) => st.lineDash.length === 2 && st.lineDash[0] === 3 && st.lineWidth === 1);
  let ghosts = dashed(paint(view));
  assert.equal(ghosts.length, 2, 'one for each side');
  for (const g of ghosts) {
    close(g.state.globalAlpha, 0.28 / Math.sqrt(20), 1e-12, 'alpha');
    assert.equal(g.state.strokeStyle, '#7aa2ff');
  }
  // Two on a side reach farther, and fainter.
  const wide = makeView({ ghost: 2 });
  wide.view.setSection(40);
  ghosts = dashed(paint(wide.view));
  assert.equal(ghosts.length, 3, '20 below, 60 and 80 above');
  assert.deepEqual(ghosts.map((g) => Number(g.state.globalAlpha.toFixed(6))).sort(),
    [0.28 / Math.sqrt(40), 0.28 / Math.sqrt(20), 0.28 / Math.sqrt(20)].map((v) => Number(v.toFixed(6))).sort());
  assert.deepEqual(dashed(paint(makeView({ ghost: 0 }).view)), []);
});

test('each shift arrow is one stroke with its barbs; one shorter than half a pixel is not drawn', () => {
  const { view } = makeView();
  view.setArrows((section) => (section === 60 ? [
    { x0: 18, y0: 30, x1: 22, y1: 30 },                                // a default-colored arrow
    { x0: 10, y0: 10, x1: 10.0001, y1: 10, color: '#00ff00' },         // shorter than half a pixel at this scale
    { x0: 5, y0: 5, x1: 8, y1: 9, color: '#ff00ff' },
  ] : null));
  const arrows = strokes(paint(view), (st) => st.lineWidth === 1.5 && st.lineCap === 'round');
  assert.deepEqual(arrows.map((c) => c.state.strokeStyle), ['#f0b44c', '#ff00ff']);
  // Another section asks the hook again and gets none.
  view.setSection(40);
  assert.equal(strokes(paint(view), (st) => st.lineWidth === 1.5).length, 0);
  // The shaft and two barbs make one path: three moveTo for the arrow of the first section.
  view.setSection(60);
  const calls = paint(view);
  const at = calls.findIndex((c) => c[0] === 'stroke' && c.state.lineWidth === 1.5);
  let moves = 0;
  for (let i = at - 1; i >= 0 && calls[i][0] !== 'beginPath'; i -= 1) if (calls[i][0] === 'moveTo') moves += 1;
  assert.equal(moves, 3);
});

test('two annotation sets with the same IDs keep a gap each, and a surface and a set can be taken away', () => {
  const { view } = makeView({ discs: [node(7, 60, 20, 30, { r: 3 })] });       // 'cell': 2 um inside the cylinder
  // The same Location ID in a second set, at another position: 25 um off the axis.
  view.setAnnotations('moved', { nodes: [node(7, 60, 45, 30, { r: 1 })], volume: 'Inf', color: '#ff6b6b' });
  const surface = view.surfaces.get('L1');
  const a = view._gapOf(surface, view.sets.get('cell'), S.annotationsAt(view.sets.get('cell').index, 60).discs[0]);
  const b = view._gapOf(surface, view.sets.get('moved'), S.annotationsAt(view.sets.get('moved').index, 60).discs[0]);
  assert.ok(a.meanSigned > 1 && b.meanSigned < -10, `${a.meanSigned} and ${b.meanSigned}`);
  const colors = circleStrokes(paint(view)).map((c) => c.state.strokeStyle);
  assert.deepEqual(colors, [S.gapColor(a.meanSigned, view.scaleUm), S.gapColor(b.meanSigned, view.scaleUm)]);
  // A second surface; the circles are measured against the one chosen, the first visible by default, none with null.
  const smaller = cylinder(3, 10, 64, 4, [20, 30]);
  view.setSurface('L2', { vertices: smaller.vertices, faces: smaller.faces, name: 'Tube', color: '#56b4e9' });
  assert.equal(contourStrokes(paint(view)).length, 2, 'two contours');
  const tube = view._gapOf(view.surfaces.get('L2'), view.sets.get('cell'), S.annotationsAt(view.sets.get('cell').index, 60).discs[0]);
  assert.ok(tube.meanSigned < a.meanSigned, 'a smaller render leaves less of the disc inside');
  assert.equal(circleStrokes(paint(view))[0].state.strokeStyle, S.gapColor(a.meanSigned, view.scaleUm),
    'by default the first visible surface is the reference');
  view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, name: 'Voxel remesh', color: '#ffd400', visible: false });
  assert.equal(circleStrokes(paint(view))[0].state.strokeStyle, S.gapColor(tube.meanSigned, view.scaleUm),
    'hide it and the next one is');
  view.setSurface('L1', { vertices: surface.vertices, faces: surface.faces, name: 'Voxel remesh', color: '#ffd400' });
  view.setGapSurface('L2');
  assert.equal(circleStrokes(paint(view))[0].state.strokeStyle, S.gapColor(tube.meanSigned, view.scaleUm));
  view.setGapSurface(null);
  const plain = circleStrokes(paint(view));
  assert.deepEqual(plain.map((c) => c.state.strokeStyle), ['#7aa2ff', '#ff6b6b'], 'the sets\' own colors');
  assert.deepEqual(gapLabels(paint(view)), [], 'and no numbers');
  // A surface taken away is not drawn or measured; so is a set.
  view.removeSurface('L2');
  assert.equal(contourStrokes(paint(view)).length, 1);
  assert.equal(view.surfaces.size, 1);
  view.removeAnnotations('moved');
  assert.deepEqual(Array.from(view.sections), [60]);
  assert.equal(view.sets.size, 1);
  view.removeAnnotations('cell');
  assert.deepEqual(Array.from(view.sections), []);
  assert.equal(circleStrokes(paint(view)).length, 0);
});

test('a z map given from outside replaces the fitted one, and null gives the fit back', () => {
  const { view } = makeView();
  close(view.z, 60 * THICKNESS, 1e-9);
  const surface = view.surfaces.get('L1');
  view.setZMap({ s0: 0, z0: 7, dz: 0, n: 1, fitted: false, maxResidualUm: 0 });
  assert.equal(view.z, 7);
  const cut = view._contour(surface).set;
  for (let s = 0; s < cut.count; s += 1) close(cut.positions[6 * s + 2], 7, 1e-9, 'the cut is at the given Z');
  view.setAnnotations('cell', { nodes: [node(1, 60, 20, 30, { r: 1 }), node(2, 90, 20, 30, { r: 1 })] });
  assert.equal(view.z, 7, 'adding annotations does not refit a map that was given');
  view.setZMap(null);
  close(view.z, 60 * THICKNESS, 1e-9);
});

test('disposing lets go of every listener and the element', () => {
  const { view, host } = makeView();
  view.dispose();
  assert.equal(view.canvas.listeners.length, 0);
  assert.equal(host.children.includes(view.canvas), false);
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
