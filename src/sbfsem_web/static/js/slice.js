// The Section view: one cut through the cell, drawn flat.  A canvas that shows a
// section of the render (the contour of each surface layer where that section's
// plane cuts it), the annotations on that section (circles for discs, polygons for
// traced outlines, their neighbors ghosted), and how far each annotation's rim lies
// from the contour -- the in-plane picture of where the render and the annotations
// part, which the 3D view can only hint at.  It has its own 2D pan and zoom, so no
// camera of the 3D pane moves with it, and keys that act only while the canvas has
// focus.
//
// The file has two halves.  The first is pure (no DOM, no three.js; node tests
// them, tests/js/test_slice.mjs): the section-to-Z fit, the annotation index, the
// contour with an error value at every end, the grid that answers "how far is this
// point from the contour, and is it inside" and the gap of an annotation's rim from
// it, the view transform, the key map, and the arrow data.  The second is
// `SliceView`, the canvas around them.
//
// Frames and units.  Everything is in the mesh's frame, micrometers: the nodes'
// `xyz_um`, the outlines' `outline_um` and the surfaces' vertices are one frame, so
// nothing is converted but the section number, which the nodes' own Z fixes
// (`fitZMap`: Z is linear in the section number, and the thickness is fitted from
// the annotations, never assumed -- RC1 and RC2 are 70 nm, not MATLAB's 90).  X is
// to the right and Y downward, as Viking's section image has them, so a section
// reads as it does in Viking; the 2D view is not the 3D camera and does not follow
// it.  A NumPy-style (row, column) is (y, x); nothing here transposes.
//
// Sign.  The gap of a rim point is positive where the contour ENCLOSES it (the
// render is fatter than the annotation there: it claims more than the annotations
// support) and negative where the point lies outside the contour (the render stops
// short) -- the convention of the error views' `bias`: red where positive, blue
// where negative, on the diverging ramp.  The sign comes from a SIGNED count over the
// section's contour segments, the section's version of the server's `enclosed_points`:
// every segment runs with the solid on its left (its face's outward normal, projected
// into the plane, tells which way), a ray to +X adds one where a segment crosses it
// going up and takes one where it goes down, and a point is inside where the count is
// positive.  A count and not a parity, because the assembled pathways (Original and its
// kin) are unions of closed pieces that interpenetrate: a point inside two of them has
// an even number of crossings to the right of it, and parity calls it outside.  An open
// piece (a tube cut before its end is capped) has open contours; near one the count is as
// good as the server's own upward ray there, which is no better than a ray can be.

import { buildSliceIndex, contourLoops, sectionContour } from './levelset.js';
import { divergingColorAt, errorColorAt } from './errorpaint.js';
import { stagePositions } from './shifts.js';

const isNumber = (v) => typeof v === 'number' && Number.isFinite(v);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// -- section number <-> Z ----------------------------------------------------------

/**
 * The line through (section, Z) of the annotations -- the nodes' `z_section` and
 * `xyz_um[2]`, and the outlines' `z_section` and `z_um` -- by least squares:
 * `{ s0, z0, dz, n, fitted, maxResidualUm }`, with `z0` the mean Z at the mean section
 * `s0` and `dz` the micrometers per section (the section thickness).  Z is linear in
 * the section number, so the residual is the rounding of the stored numbers (a
 * nanometer); `maxResidualUm` says so, and a cell whose annotations disagree shows it.
 * With fewer than two distinct sections there is no slope to fit: `fitted` is false and
 * `dz` is 0 (every section maps to the one Z known).  Pairs that are not numbers are
 * left out.
 */
export function fitZMap(sections, zs) {
  const n0 = Math.min(sections.length, zs.length);
  let n = 0;
  let sumS = 0;
  let sumZ = 0;
  for (let i = 0; i < n0; i += 1) {
    if (isNumber(sections[i]) && isNumber(zs[i])) { n += 1; sumS += sections[i]; sumZ += zs[i]; }
  }
  if (!n) return { s0: 0, z0: 0, dz: 0, n: 0, fitted: false, maxResidualUm: 0 };
  const s0 = sumS / n;
  const z0 = sumZ / n;
  let sxx = 0;
  let sxz = 0;
  for (let i = 0; i < n0; i += 1) {
    if (!(isNumber(sections[i]) && isNumber(zs[i]))) continue;
    const ds = sections[i] - s0;
    sxx += ds * ds;
    sxz += ds * (zs[i] - z0);
  }
  const fitted = sxx > 0;
  const dz = fitted ? sxz / sxx : 0;
  let maxResidualUm = 0;
  for (let i = 0; i < n0; i += 1) {
    if (!(isNumber(sections[i]) && isNumber(zs[i]))) continue;
    maxResidualUm = Math.max(maxResidualUm, Math.abs(z0 + (sections[i] - s0) * dz - zs[i]));
  }
  return { s0, z0, dz, n, fitted, maxResidualUm };
}

/** The Z (um) of a section number under `map` (`fitZMap`). */
export function sectionToZ(map, section) {
  return map.z0 + (section - map.s0) * map.dz;
}

/** The (fractional) section number at Z (um); `s0` when the map has no slope. */
export function zToSection(map, z) {
  return map.dz ? map.s0 + (z - map.z0) / map.dz : map.s0;
}

// -- the annotations of a section --------------------------------------------------

/** TypeCode of a traced closed curve (Viking's `CLOSED_CURVE`). */
export const CLOSED_CURVE = 6;

const flatPoints = (rows) => {
  const out = new Float64Array(rows.length * 2);
  for (let k = 0; k < rows.length; k += 1) { out[2 * k] = rows[k][0]; out[2 * k + 1] = rows[k][1]; }
  return out;
};

/**
 * The annotations of one cell, binned by section number: `nodes` are `/nodes` rows
 * (`id`, `xyz_um`, `radius_um`, `z_section`, `type_code`), `outlines` the closed-curve
 * outlines (`location_id`, `z_section`, `z_um`, `outline_um` [[x, y], ...], optional
 * `cutouts_um`).  A node of the closed-curve type that has an outline is that outline
 * (drawn as a polygon, not as the circle of its equivalent radius); one without is
 * drawn as a disc.  A row that is not finite, or a disc of no radius, is left out.
 *
 * Returns `{ discs, outlines, sections, bySection, bounds }`: every annotation in a
 * flat list (`kind` 'disc' or 'outline', with `id`, `section`, `x`, `y`, `z`, `r`; an
 * outline also has `points` (x, y, ...) and `cutouts`), `sections` the distinct sections
 * with an annotation, ascending (Int32Array), `bySection` a Map to `{ discs, outlines }`,
 * and `bounds` the box of the annotations in X and Y, or null with none.
 */
export function buildAnnotationIndex(nodes = [], outlines = []) {
  const drawn = new Set();
  const outlineList = [];
  for (const o of outlines) {
    if (!o || !Array.isArray(o.outline_um) || o.outline_um.length < 3 || !isNumber(o.z_section)) continue;
    const points = flatPoints(o.outline_um);
    if (!points.every(Number.isFinite)) continue;
    let sx = 0;
    let sy = 0;
    for (let k = 0; k < points.length; k += 2) { sx += points[k]; sy += points[k + 1]; }
    const m = points.length / 2;
    const c = Array.isArray(o.centroid_um) && o.centroid_um.length >= 2 && isNumber(o.centroid_um[0])
      ? o.centroid_um : [sx / m, sy / m];
    outlineList.push({
      kind: 'outline',
      id: o.location_id,
      section: o.z_section,
      x: c[0],
      y: c[1],
      z: isNumber(o.z_um) ? o.z_um : NaN,
      r: isNumber(o.equivalent_diameter_um) ? o.equivalent_diameter_um / 2 : Math.sqrt(Math.abs(polygonArea(points)) / Math.PI),
      points,
      cutouts: (o.cutouts_um || []).map(flatPoints).filter((p) => p.length >= 6),
    });
    drawn.add(o.location_id);
  }
  const discList = [];
  for (const n of nodes) {
    const p = n && n.xyz_um;
    if (!p || !isNumber(p[0]) || !isNumber(p[1]) || !isNumber(p[2]) || !isNumber(n.z_section)) continue;
    if (n.type_code === CLOSED_CURVE && drawn.has(n.id)) continue;
    if (!(n.radius_um > 0)) continue;
    discList.push({
      kind: 'disc', id: n.id, section: n.z_section, x: p[0], y: p[1], z: p[2], r: n.radius_um,
      closedCurve: n.type_code === CLOSED_CURVE,
    });
  }
  const bySection = new Map();
  const slot = (s) => {
    let entry = bySection.get(s);
    if (!entry) { entry = { discs: [], outlines: [] }; bySection.set(s, entry); }
    return entry;
  };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const grow = (x, y, r) => {
    minX = Math.min(minX, x - r); maxX = Math.max(maxX, x + r);
    minY = Math.min(minY, y - r); maxY = Math.max(maxY, y + r);
  };
  for (const d of discList) { slot(d.section).discs.push(d); grow(d.x, d.y, d.r); }
  for (const o of outlineList) {
    slot(o.section).outlines.push(o);
    for (let k = 0; k < o.points.length; k += 2) grow(o.points[k], o.points[k + 1], 0);
  }
  const sections = Int32Array.from([...bySection.keys()].sort((a, b) => a - b));
  return {
    discs: discList,
    outlines: outlineList,
    sections,
    bySection,
    bounds: Number.isFinite(minX) ? { minX, minY, maxX, maxY } : null,
  };
}

/** Signed area of a flat x, y polygon (positive counterclockwise in a y-up frame). */
export function polygonArea(points) {
  let sum = 0;
  const m = points.length / 2;
  for (let k = 0; k < m; k += 1) {
    const j = (k + 1) % m;
    sum += points[2 * k] * points[2 * j + 1] - points[2 * j] * points[2 * k + 1];
  }
  return sum / 2;
}

const EMPTY = Object.freeze({ discs: Object.freeze([]), outlines: Object.freeze([]) });

/** The annotations on `section`: `{ discs, outlines }`, both empty when there are none. */
export function annotationsAt(index, section) {
  return index.bySection.get(section) || EMPTY;
}

/** Index of the first element of the ascending `a` that is not below `v`. */
function lowerBound(a, v) {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] < v) lo = mid + 1; else hi = mid;
  }
  return lo;
}

/**
 * The annotated sections next to `section` in the ascending `sections`: up to `count`
 * below it and `count` above it, nearest first (the section itself is neither).
 */
export function neighborSections(sections, section, count = 1) {
  const at = lowerBound(sections, section);
  const below = [];
  const above = [];
  for (let k = at - 1; k >= 0 && below.length < count; k -= 1) below.push(sections[k]);
  for (let k = at; k < sections.length && above.length < count; k += 1) {
    if (sections[k] !== section) above.push(sections[k]);
  }
  return { below, above };
}

/** The union of several ascending section lists, ascending and distinct. */
export function mergeSections(lists) {
  const all = new Set();
  for (const list of lists) for (const s of list) all.add(s);
  return Int32Array.from([...all].sort((a, b) => a - b));
}

// -- the keys ------------------------------------------------------------------

/** The factor one zoom key (or wheel notch) scales the view by. */
export const ZOOM_STEP = 1.25;

/**
 * What a key press asks of the Section view, whether or not the canvas has focus (the
 * canvas listens, and only a focused element gets the key): `{ kind: 'section', section }`
 * for the new section, `{ kind: 'zoom', factor }`, `{ kind: 'reset' }`, or `null` when it
 * is not ours (the page's own shortcuts carry on, and Ctrl, Alt and Meta chords always do).
 *
 *   Up / Down          the next / previous annotated section (Shift: ten of them)
 *   Right / Left       one section up / down (Shift: ten)
 *   PageUp / PageDown  ten sections up / down
 *   Home / End         the first / last annotated section
 *   + / -  and  0      zoom, and fit the view to the section: the keyboard's
 *                      wheel and double-click
 *
 * `sections` are the annotated ones, ascending; `range` ({ min, max }) bounds Left, Right,
 * PageUp and PageDown (default: the annotated range).  Up from a section that has no
 * annotation goes to the nearest annotated one above it, and Down to the one below;
 * at the end of the list a key changes nothing (the section comes back as it was).
 */
export function sliceKeyAction(event, { section, sections, range = null }) {
  if (event.altKey || event.ctrlKey || event.metaKey) return null;
  const list = sections || [];
  const bounds = range || (list.length ? { min: list[0], max: list[list.length - 1] } : { min: section, max: section });
  const to = (s) => ({ kind: 'section', section: clamp(Math.round(s), bounds.min, bounds.max) });
  const walk = (steps) => {
    // The annotated section `steps` places above (positive) or below (negative) `section`;
    // with fewer than that in the way, the last one there is; with none, the section stays.
    let first;                                    // index of the nearest annotated section in that direction
    if (steps > 0) {
      first = lowerBound(list, section);
      if (list[first] === section) first += 1;
      if (first >= list.length) return to(section);
      return to(list[Math.min(list.length - 1, first + steps - 1)]);
    }
    first = lowerBound(list, section) - 1;
    if (first < 0) return to(section);
    return to(list[Math.max(0, first + steps + 1)]);
  };
  const tenfold = event.shiftKey ? 10 : 1;
  switch (event.key) {
    case 'ArrowUp': return walk(tenfold);
    case 'ArrowDown': return walk(-tenfold);
    case 'ArrowRight': return to(section + tenfold);
    case 'ArrowLeft': return to(section - tenfold);
    case 'PageUp': return to(section + 10);
    case 'PageDown': return to(section - 10);
    case 'Home': return list.length ? to(list[0]) : to(section);
    case 'End': return list.length ? to(list[list.length - 1]) : to(section);
    case '+': case '=': return { kind: 'zoom', factor: ZOOM_STEP };
    case '-': case '_': return { kind: 'zoom', factor: 1 / ZOOM_STEP };
    case '0': return { kind: 'reset' };
    default: return null;
  }
}

// -- the 2D view ----------------------------------------------------------------

/** Pixels per micrometer the view may take. */
export const VIEW_LIMITS = Object.freeze({ minScale: 0.25, maxScale: 40000 });
/** The least extent (um) a fit gives the view, so a single small disc is not blown up. */
export const MIN_FIT_UM = 8;

/**
 * A view of the box `{ minX, minY, maxX, maxY }` in a `size` = { width, height } of
 * pixels: `{ cx, cy, scale }`, the box's center and the pixels per micrometer that fit
 * it with `margin` pixels to spare.  A box smaller than `MIN_FIT_UM` is padded to it.
 */
export function fitView(box, size, { margin = 28 } = {}) {
  const b = box && Number.isFinite(box.minX) ? box : { minX: -MIN_FIT_UM / 2, maxX: MIN_FIT_UM / 2, minY: -MIN_FIT_UM / 2, maxY: MIN_FIT_UM / 2 };
  const w = Math.max(b.maxX - b.minX, MIN_FIT_UM);
  const h = Math.max(b.maxY - b.minY, MIN_FIT_UM);
  const room = { width: Math.max(size.width - 2 * margin, 16), height: Math.max(size.height - 2 * margin, 16) };
  return {
    cx: (b.minX + b.maxX) / 2,
    cy: (b.minY + b.maxY) / 2,
    scale: clamp(Math.min(room.width / w, room.height / h), VIEW_LIMITS.minScale, VIEW_LIMITS.maxScale),
  };
}

/** Screen pixel of a point (um); Y grows downward on screen as in Viking's section image. */
export function worldToScreen(view, size, x, y) {
  return [size.width / 2 + (x - view.cx) * view.scale, size.height / 2 + (y - view.cy) * view.scale];
}

/** The point (um) under a screen pixel. */
export function screenToWorld(view, size, px, py) {
  return [view.cx + (px - size.width / 2) / view.scale, view.cy + (py - size.height / 2) / view.scale];
}

/** The view zoomed by `factor` about a screen pixel, which keeps the point under it. */
export function zoomAt(view, size, px, py, factor) {
  const scale = clamp(view.scale * factor, VIEW_LIMITS.minScale, VIEW_LIMITS.maxScale);
  const [wx, wy] = screenToWorld(view, size, px, py);
  return {
    scale,
    cx: wx - (px - size.width / 2) / scale,
    cy: wy - (py - size.height / 2) / scale,
  };
}

/** The view dragged by a screen offset: the content follows the pointer. */
export function panView(view, dxPx, dyPx) {
  return { ...view, cx: view.cx - dxPx / view.scale, cy: view.cy - dyPx / view.scale };
}

/** A round length (1, 2 or 5 times a power of ten, um) at most `maxPx` pixels wide at `scale` px/um. */
export function scaleBarLength(scale, maxPx = 120) {
  const target = maxPx / scale;
  const power = 10 ** Math.floor(Math.log10(target));
  const step = [5, 2, 1].map((m) => m * power).find((v) => v <= target) ?? power;
  return { um: step, px: step * scale };
}

// -- the contour, with a value at every end ---------------------------------------

/**
 * The contour of a surface at the plane Z = `level`: `levelset.js` `sectionContour` itself
 * (a cap lying on the plane is cut along its rim at the bottom of a stack as at the top,
 * wherever in the surface it is), carrying along what the Section view needs from the same
 * faces -- the field interpolated at both ends of every segment when `values` (one number
 * per vertex: the error) is given, and the side of every segment the solid is on.  There is
 * one marching rule and it is levelset's; this file used to repeat it, and the repetition
 * fell behind when that rule changed.
 *
 * @param index   `buildSliceIndex(vertices, faces)` of the surface (binned along Z)
 * @returns `{ count, positions, values, orient }`: `positions` float64, six numbers a
 *   segment; `values` float64, two a segment (or null without `values`); `orient` an Int8Array,
 *   +1 where the solid is on the left of the segment as it runs (`levelSet`'s `axis`).
 */
export function contourSegments(index, level, values = null) {
  return sectionContour(index, level, { values, orient: true });
}

// -- a grid over the contour: nearest point and inside / outside ---------------------

/**
 * A uniform grid over the XY extent of contour segments (`positions`: x, y, z, x, y, z
 * a segment; Z is ignored), each cell listing the segments whose box reaches it, so a
 * point's nearest segment and the inside test read a few cells and not every segment.
 * `orient` (one number per segment, +1 / -1 / 0: `contourSegments`') says which way each
 * runs round the solid; with it the inside test is a signed count, without it (segments
 * from nowhere in particular) a parity.  Build it once per (surface, section).
 */
export function buildSegmentGrid(positions, orient = null) {
  const n = Math.floor(positions.length / 6);
  if (!n) return { n: 0, positions, orient: null, nx: 0, ny: 0, cell: 1, minX: 0, minY: 0, maxX: 0, maxY: 0, start: new Uint32Array(1), ids: new Uint32Array(0), stamp: new Uint32Array(0), query: 0 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let length = 0;
  for (let s = 0; s < n; s += 1) {
    const x0 = positions[6 * s];
    const y0 = positions[6 * s + 1];
    const x1 = positions[6 * s + 3];
    const y1 = positions[6 * s + 4];
    minX = Math.min(minX, x0, x1); maxX = Math.max(maxX, x0, x1);
    minY = Math.min(minY, y0, y1); maxY = Math.max(maxY, y0, y1);
    length += Math.hypot(x1 - x0, y1 - y0);
  }
  const w = maxX - minX;
  const h = maxY - minY;
  // Cells a few segments wide (a segment then touches a handful of them), but never so
  // fine that the grid outgrows the segments, nor so many that it is a table of its own.
  const cell = Math.max(3 * (length / n), Math.sqrt((w * h) / Math.max(n, 1)), Math.max(w, h) / 1024, 1e-9);
  const nx = Math.floor(w / cell) + 1;
  const ny = Math.floor(h / cell) + 1;
  const start = new Uint32Array(nx * ny + 1);
  const cx = (x) => Math.min(nx - 1, Math.floor((x - minX) / cell));
  const cy = (y) => Math.min(ny - 1, Math.floor((y - minY) / cell));
  for (let s = 0; s < n; s += 1) {
    const i0 = cx(Math.min(positions[6 * s], positions[6 * s + 3]));
    const i1 = cx(Math.max(positions[6 * s], positions[6 * s + 3]));
    const j0 = cy(Math.min(positions[6 * s + 1], positions[6 * s + 4]));
    const j1 = cy(Math.max(positions[6 * s + 1], positions[6 * s + 4]));
    for (let j = j0; j <= j1; j += 1) for (let i = i0; i <= i1; i += 1) start[j * nx + i + 1] += 1;
  }
  for (let c = 0; c < nx * ny; c += 1) start[c + 1] += start[c];
  const filled = new Uint32Array(nx * ny);
  const ids = new Uint32Array(start[nx * ny]);
  for (let s = 0; s < n; s += 1) {
    const i0 = cx(Math.min(positions[6 * s], positions[6 * s + 3]));
    const i1 = cx(Math.max(positions[6 * s], positions[6 * s + 3]));
    const j0 = cy(Math.min(positions[6 * s + 1], positions[6 * s + 4]));
    const j1 = cy(Math.max(positions[6 * s + 1], positions[6 * s + 4]));
    for (let j = j0; j <= j1; j += 1) {
      for (let i = i0; i <= i1; i += 1) {
        const c = j * nx + i;
        ids[start[c] + filled[c]++] = s;
      }
    }
  }
  return { n, positions, orient: orient && orient.length >= n ? orient : null, nx, ny, cell, minX, minY, maxX, maxY, start, ids, stamp: new Uint32Array(n), query: 0 };
}

/** The closest point of segment `s` of `positions` to (x, y): [squared distance, px, py]. */
function closestOnSegment(positions, s, x, y, out) {
  const x0 = positions[6 * s];
  const y0 = positions[6 * s + 1];
  const dx = positions[6 * s + 3] - x0;
  const dy = positions[6 * s + 4] - y0;
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? clamp(((x - x0) * dx + (y - y0) * dy) / len2, 0, 1) : 0;
  const px = x0 + t * dx;
  const py = y0 + t * dy;
  out[0] = (x - px) * (x - px) + (y - py) * (y - py);
  out[1] = px;
  out[2] = py;
}

const HIT = new Float64Array(3);

/**
 * The point of the contour closest to (x, y): `{ d, x, y, segment }` (micrometers), or
 * null when the grid has no segment.  Rings of cells are read outward from the point's
 * own and the search ends when no cell not yet read can hold anything closer, so the
 * answer is the exact nearest segment, as a scan of all of them would give.
 */
export function nearestOnGrid(grid, x, y) {
  if (!grid.n) return null;
  const { nx, ny, cell, start, ids, positions } = grid;
  const ci = clamp(Math.floor((x - grid.minX) / cell), 0, nx - 1);
  const cj = clamp(Math.floor((y - grid.minY) / cell), 0, ny - 1);
  let best2 = Infinity;
  let bx = 0;
  let by = 0;
  let bs = -1;
  const scan = (i, j) => {
    const c = j * nx + i;
    for (let k = start[c]; k < start[c + 1]; k += 1) {
      closestOnSegment(positions, ids[k], x, y, HIT);
      if (HIT[0] < best2) { best2 = HIT[0]; bx = HIT[1]; by = HIT[2]; bs = ids[k]; }
    }
  };
  const last = Math.max(nx, ny);
  for (let r = 0; r <= last; r += 1) {
    if (r === 0) scan(ci, cj);
    else {
      for (let i = Math.max(0, ci - r); i <= Math.min(nx - 1, ci + r); i += 1) {
        if (cj - r >= 0) scan(i, cj - r);
        if (cj + r < ny) scan(i, cj + r);
      }
      for (let j = Math.max(0, cj - r + 1); j <= Math.min(ny - 1, cj + r - 1); j += 1) {
        if (ci - r >= 0) scan(ci - r, j);
        if (ci + r < nx) scan(ci + r, j);
      }
    }
    // Anything not read yet is in a ring beyond r, at least r cells from the point.
    if (best2 <= (r * cell) * (r * cell)) break;
  }
  return { d: Math.sqrt(best2), x: bx, y: by, segment: bs };
}

/**
 * Is (x, y) inside the contour?  By the segments a ray to +X crosses (a segment counts when
 * its ends are on opposite sides of the ray's Y, half open, so a vertex on the ray is not
 * counted twice).  With the segments' sides (`buildSegmentGrid`'s `orient`) a crossing adds
 * one where the segment, run with the solid on its left, goes up and takes one where it goes
 * down, and the point is inside where the sum is positive: the number of bodies that enclose
 * it, as the server's `enclosed_points` counts, so a point inside two interpenetrating pieces
 * is inside (a negative sum, which a body wound inward or an open end can give, is not).
 * Without them the rule is even-odd: right for the closed loops of a single body, with its
 * holes and islands, and wrong wherever pieces overlap.
 */
export function insideContour(grid, x, y) {
  if (!grid.n) return false;
  if (y < grid.minY || y > grid.maxY || x < grid.minX || x > grid.maxX) return false;
  const { nx, ny, cell, start, ids, positions, stamp, orient } = grid;
  const j = Math.min(ny - 1, Math.floor((y - grid.minY) / cell));
  const i0 = Math.min(nx - 1, Math.floor((x - grid.minX) / cell));
  grid.query = (grid.query + 1) >>> 0;
  if (grid.query === 0) { stamp.fill(0); grid.query = 1; }
  let crossings = 0;
  let count = 0;
  for (let i = i0; i < nx; i += 1) {
    const c = j * nx + i;
    for (let k = start[c]; k < start[c + 1]; k += 1) {
      const s = ids[k];
      if (stamp[s] === grid.query) continue;
      stamp[s] = grid.query;
      const y0 = positions[6 * s + 1];
      const y1 = positions[6 * s + 4];
      if ((y0 > y) === (y1 > y)) continue;
      const x0 = positions[6 * s];
      const xi = x0 + ((y - y0) * (positions[6 * s + 3] - x0)) / (y1 - y0);
      if (xi > x) {
        crossings += 1;
        if (orient) count += orient[s] * (y1 > y0 ? 1 : -1);
      }
    }
  }
  return orient ? count > 0 : (crossings & 1) === 1;
}

// -- an annotation against the contour -------------------------------------------------

/** How many points of an annotation's rim are tested against the contour. */
export const RIM_SAMPLES = 32;

/**
 * `n` points evenly around an annotation's rim, flat x, y: a disc's circle from angle 0,
 * a traced outline's boundary at equal arc lengths from its first vertex (cutouts are not
 * sampled).  Empty for an outline with no length.
 */
export function ringSamples(shape, n = RIM_SAMPLES) {
  const out = new Float64Array(2 * n);
  if (shape.kind !== 'outline') {
    for (let k = 0; k < n; k += 1) {
      const a = (2 * Math.PI * k) / n;
      out[2 * k] = shape.x + shape.r * Math.cos(a);
      out[2 * k + 1] = shape.y + shape.r * Math.sin(a);
    }
    return out;
  }
  const p = shape.points;
  const m = p.length / 2;
  const cum = new Float64Array(m + 1);
  for (let k = 0; k < m; k += 1) {
    const j = (k + 1) % m;
    cum[k + 1] = cum[k] + Math.hypot(p[2 * j] - p[2 * k], p[2 * j + 1] - p[2 * k + 1]);
  }
  const total = cum[m];
  if (!(total > 0)) return new Float64Array(0);
  let edge = 0;
  for (let k = 0; k < n; k += 1) {
    const at = (total * k) / n;
    while (edge < m - 1 && cum[edge + 1] <= at) edge += 1;
    const j = (edge + 1) % m;
    const span = cum[edge + 1] - cum[edge];
    const t = span > 0 ? (at - cum[edge]) / span : 0;
    out[2 * k] = p[2 * edge] + t * (p[2 * j] - p[2 * edge]);
    out[2 * k + 1] = p[2 * edge + 1] + t * (p[2 * j + 1] - p[2 * edge + 1]);
  }
  return out;
}

/**
 * How far an annotation's rim lies from the contour, in the section's plane: each of
 * the `samples` (flat x, y) gets its distance to the nearest contour point, positive
 * where the contour encloses it (the render is fatter than the annotation there) and
 * negative where it does not (the render stops short).  Returns null when there is no
 * contour, else `{ n, meanSigned, meanAbs, maxAbs, enclosedShare, worst }`, with `worst`
 * the rim point farthest from the contour: `{ x, y, tx, ty, signed }`, `t` being the
 * contour point nearest it.
 */
export function annotationGap(grid, samples) {
  const n = samples.length / 2;
  if (!grid.n || !n) return null;
  let sum = 0;
  let sumAbs = 0;
  let enclosed = 0;
  let worst = null;
  for (let k = 0; k < n; k += 1) {
    const x = samples[2 * k];
    const y = samples[2 * k + 1];
    const hit = nearestOnGrid(grid, x, y);
    const inside = insideContour(grid, x, y);
    const signed = inside ? hit.d : -hit.d;
    sum += signed;
    sumAbs += hit.d;
    if (inside) enclosed += 1;
    if (!worst || hit.d > Math.abs(worst.signed)) worst = { x, y, tx: hit.x, ty: hit.y, signed };
  }
  return {
    n,
    meanSigned: sum / n,
    meanAbs: sumAbs / n,
    maxAbs: Math.abs(worst.signed),
    enclosedShare: enclosed / n,
    worst,
  };
}

/** Is (x, y) inside the polygon `points` (flat x, y), the even-odd rule? */
export function pointInPolygon(points, x, y) {
  const m = points.length / 2;
  let inside = false;
  for (let k = 0, j = m - 1; k < m; j = k, k += 1) {
    const yk = points[2 * k + 1];
    const yj = points[2 * j + 1];
    if ((yk > y) !== (yj > y)) {
      const xi = points[2 * k] + ((y - yk) * (points[2 * j] - points[2 * k])) / (yj - yk);
      if (xi > x) inside = !inside;
    }
  }
  return inside;
}

/**
 * The annotation under (x, y) on `section`, or null: the smallest disc whose circle
 * contains the point (within `tolUm` of its edge), or an outline that contains it (a
 * cutout is a hole).  `sets` are `{ index, ... }` records, nearer the front winning ties.
 */
export function pickAnnotation(sets, section, x, y, tolUm = 0) {
  let best = null;
  let bestSize = Infinity;
  for (const set of sets) {
    const here = annotationsAt(set.index, section);
    for (const d of here.discs) {
      const dist = Math.hypot(x - d.x, y - d.y);
      if (dist <= d.r + tolUm && d.r < bestSize) { best = { set, annotation: d }; bestSize = d.r; }
    }
    for (const o of here.outlines) {
      const holed = o.cutouts.some((c) => pointInPolygon(c, x, y));
      if (pointInPolygon(o.points, x, y) && !holed && o.r < bestSize) { best = { set, annotation: o }; bestSize = o.r; }
    }
  }
  return best;
}

// -- the shift vectors ------------------------------------------------------------------

/**
 * The arrows of a Position-shift field (`shifts.js` `prepareField`), binned by section:
 * a Map from a section to `[{ x0, y0, x1, y1, id, mag }]`, the head (x1, y1) at the
 * annotation's real position, its tail `scale` times the shift away from it (the arrow
 * of the 3D layer, which exaggerates the tail only).  `minUm` leaves out the shorter
 * moves.
 */
export function buildArrowIndex(field, { scale = 1, minUm = 0 } = {}) {
  const bySection = new Map();
  for (let i = 0; i < field.n; i += 1) {
    if (field.mag[i] < minUm) continue;
    const arrow = {
      x0: field.hx[i] - scale * field.dx[i],
      y0: field.hy[i] - scale * field.dy[i],
      x1: field.hx[i],
      y1: field.hy[i],
      id: field.id[i],
      mag: field.mag[i],
    };
    const list = bySection.get(field.section[i]);
    if (list) list.push(arrow); else bySection.set(field.section[i], [arrow]);
  }
  return bySection;
}

/** The arrows on `section` of a `buildArrowIndex` Map, as the `SliceView` arrow hook takes them. */
export function arrowsAt(arrowIndex, section) {
  return arrowIndex.get(section) || [];
}

/**
 * `nodes` with their positions at one stage of the correction chain -- 'stored',
 * 'registered' or 'corrected' (`shifts.js` `stagePositions`) -- so two annotation sets of
 * the same cell, before and after, can be drawn side by side.  Z does not move.
 */
export function nodesAtStage(nodes, stage) {
  return nodes.map((node) => {
    const at = stagePositions(node)[stage];
    if (!at) throw new RangeError(`unknown stage ${stage}`);
    return { ...node, xyz_um: [at[0], at[1], node.xyz_um[2]] };
  });
}

// -- colors ------------------------------------------------------------------------------

const css = (rgb, alpha = 1) => `rgba(${Math.round(rgb[0] * 255)},${Math.round(rgb[1] * 255)},${Math.round(rgb[2] * 255)},${alpha})`;

/** The color of a signed gap on the diverging ramp, at +-`scaleUm`: red where the render is fat, blue where it is short. */
export function gapColor(signedUm, scaleUm, alpha = 1) {
  return css(divergingColorAt(signedUm, scaleUm), alpha);
}

/** The error ramp's color at a distance, 0 to `scaleUm`. */
export function errorCss(valueUm, scaleUm, alpha = 1) {
  return css(errorColorAt(valueUm, scaleUm), alpha);
}

/** Levels the error ramp is cut into when a contour is colored: one path and one color per level. */
export const CONTOUR_LEVELS = 24;

/**
 * The segments of a contour grouped by the level of the error ramp their mean value
 * falls in: `[{ level, color, segments: [index, ...] }]`, so a colored contour is a
 * handful of paths and not one per segment.
 */
export function bucketByError(values, count, scaleUm, levels = CONTOUR_LEVELS) {
  const groups = new Map();
  for (let s = 0; s < count; s += 1) {
    const v = 0.5 * (values[2 * s] + values[2 * s + 1]);
    const t = Number.isFinite(v) ? clamp(v / scaleUm, 0, 1) : 0;
    const level = Math.round(t * (levels - 1));
    const list = groups.get(level);
    if (list) list.push(s); else groups.set(level, [s]);
  }
  return [...groups.keys()].sort((a, b) => a - b).map((level) => ({
    level,
    color: errorCss((level / (levels - 1)) * scaleUm, scaleUm),
    segments: groups.get(level),
  }));
}

// -- the canvas ------------------------------------------------------------------------------

const BACKGROUND = '#101418';
const INK = '#cfd6dd';
const MUTED = 'rgba(207,214,221,0.62)';

/** Text with a dark rim, so it reads over the contour lines behind it (the fill color is the caller's). */
function haloText(ctx, text, x, y) {
  ctx.save();
  ctx.lineJoin = 'round';
  ctx.lineWidth = 3.5;
  ctx.strokeStyle = 'rgba(16, 20, 24, 0.85)';
  ctx.strokeText(text, x, y);
  ctx.restore();
  ctx.fillText(text, x, y);
}

/** A tooltip's lines for an annotation and its gaps, as plain text. */
export function describeAnnotation(set, annotation, gaps) {
  const volume = set.volume ? `${set.volume} ` : '';
  const lines = [`${volume}${annotation.id}`, `section ${annotation.section}`];
  lines.push(annotation.kind === 'outline'
    ? `traced outline, equivalent radius ${annotation.r.toFixed(2)} µm`
    : `${annotation.closedCurve ? 'closed-curve node' : 'disc'}, radius ${annotation.r.toFixed(2)} µm`);
  for (const [name, gap] of gaps) {
    if (!gap) { lines.push(`${name}: no contour on this section`); continue; }
    const sign = gap.meanSigned >= 0 ? '+' : '-';
    lines.push(`${name}: ${sign}${Math.abs(gap.meanSigned).toFixed(2)} µm mean gap, `
      + `${Math.round(gap.enclosedShare * 100)}% of the rim inside, worst ${gap.worst.signed >= 0 ? '+' : '-'}${gap.maxAbs.toFixed(2)} µm`);
  }
  return lines;
}

/**
 * The Section view: a canvas in `host` that draws one section.
 *
 * Feed it with `setAnnotations` (nodes and outlines of a copy of the cell, as many sets
 * as there are, before and after for instance) and `setSurface` (a layer's mesh, with
 * its error values if it is painted by error); choose the section with `setSection`;
 * `setScale` is the error scale (um) the contour and gap colors reach red at.  The view
 * keeps its own pan and zoom (`getView` / `setView`, `onView` to follow another
 * canvas under Sync).  Keys act only while the canvas has focus (see `sliceKeyAction`).
 *
 * @param host      an element to put the canvas in (it should have a size)
 * @param callbacks `onSection(section)` a key changed the section; `onView(view, { auto })` the
 *   view changed: the user panned or zoomed it (no second argument), or it followed the content
 *   of a new section, a new surface or a new size (`auto: true`); `onSelect(info)` an annotation was clicked (`{ key, volume, id, section,
 *   x, y, z, r, kind }`); `onHover(info|null)`
 * @param options   `ghost` annotated sections each side drawn faintly (default 1); `document`,
 *   `schedule` (a `requestAnimationFrame`) for tests
 */
export class SliceView {
  constructor(host, callbacks = {}, options = {}) {
    this.host = host;
    this.callbacks = callbacks;
    this.ghost = options.ghost ?? 1;
    this._doc = options.document || globalThis.document;
    this._schedule = options.schedule
      || (typeof globalThis.requestAnimationFrame === 'function'
        ? (fn) => globalThis.requestAnimationFrame(fn) : (fn) => setTimeout(fn, 0));
    this.sets = new Map();           // key -> { key, index, color, label, volume, dash }
    this.surfaces = new Map();       // key -> { key, name, index, color, values, visible, ... }
    this.arrows = null;              // (section) => [{ x0, y0, x1, y1 }]
    this.scaleUm = 0.5;
    this.section = null;
    this.zMap = null;
    this._zMapGiven = false;
    this.view = { cx: 0, cy: 0, scale: 40 };
    this._moved = false;
    this._active = true;
    this._dirty = true;
    this._frame = 0;
    this._hover = null;
    this._contours = new Map();
    this._gaps = new Map();
    this._sections = new Int32Array(0);
    this._pendingSection = null;     // a section asked for before any annotation could bound it
    this._gapSurface = undefined;
    this._drag = null;
    this._size = { width: 0, height: 0, dpr: 1 };

    const doc = this._doc;
    this.canvas = doc.createElement('canvas');
    this.canvas.className = 'slice-canvas';
    this.canvas.tabIndex = 0;
    this.canvas.setAttribute('role', 'application');
    this.canvas.setAttribute('aria-label', 'Section view. Up and Down move to the next annotated section, '
      + 'Left and Right by one, Page Up and Page Down by ten, Home and End to the first and last.');
    this.tip = doc.createElement('div');
    this.tip.className = 'slice-tip';
    this.tip.hidden = true;
    host.append(this.canvas, this.tip);
    this.ctx = this.canvas.getContext('2d');

    this._on = {
      keydown: (e) => this._key(e),
      wheel: (e) => this._wheel(e),
      pointerdown: (e) => this._down(e),
      pointermove: (e) => this._move(e),
      pointerup: (e) => this._up(e),
      pointercancel: (e) => this._up(e),
      pointerleave: () => this._leave(),
      dblclick: () => this.resetView(),
      focus: () => this._request(),
      blur: () => this._request(),
    };
    // The wheel zooms the view and not the page, so its listener must not be passive.
    for (const [type, fn] of Object.entries(this._on)) {
      this.canvas.addEventListener(type, fn, type === 'wheel' ? { passive: false } : undefined);
    }
    if (typeof globalThis.ResizeObserver === 'function') {
      this._resizer = new globalThis.ResizeObserver(() => this.resize());
      this._resizer.observe(host);
    }
    this.resize();
  }

  // -- data -------------------------------------------------------------------------

  /**
   * An annotation set: `{ nodes, outlines, color, label, volume, dash }`.  `volume` goes
   * next to every Location ID the view shows (IDs are numbered per volume).
   */
  setAnnotations(key, { nodes = [], outlines = [], color = '#7aa2ff', label = key, volume = '', dash = null } = {}) {
    this.sets.set(key, { key, index: buildAnnotationIndex(nodes, outlines), color, label, volume, dash });
    this._afterAnnotations();
  }

  removeAnnotations(key) {
    if (this.sets.delete(key)) this._afterAnnotations();
  }

  _afterAnnotations() {
    this._sections = mergeSections([...this.sets.values()].map((s) => s.index.sections));
    if (!this._zMapGiven) this.zMap = this._fitZ();
    this._gaps.clear();
    if (this._sections.length) {
      const { min, max } = this.range;
      if (this._pendingSection !== null) {
        // The host asked for a section before there were any to choose from (the natural order: the
        // pane first, its data a fetch later): it is applied now, within the range there is.
        this.section = clamp(this._pendingSection, min, max);
        this._pendingSection = null;
        this._moved = false;
      } else if (this.section === null || this.section < min || this.section > max) {
        // The first data, or another cell's: the old section means nothing in this range.
        this.section = this._sections[this._sections.length >> 1];
        this._moved = false;
      }
    }
    this._refit();
    this._request();
  }

  /**
   * Fit the view to the section's content again, unless the user has panned or zoomed it.  A view
   * that changes by itself is told to `onView` (with `{ auto: true }`), so a host that shares one
   * view between panes (`onView: (v) => others.setView(v)`) does not drift apart from a pane that
   * follows its content.  True when the view changed.
   */
  _refit({ notify = true } = {}) {
    if (this._moved || this.section === null) return false;
    const was = this.view;
    this.view = fitView(this._contentBox(), this._size);
    const changed = this.view.cx !== was.cx || this.view.cy !== was.cy || this.view.scale !== was.scale;
    if (changed && notify) this.callbacks.onView?.(this.getView(), { auto: true });
    return changed;
  }

  _fitZ() {
    const sections = [];
    const zs = [];
    for (const set of this.sets.values()) {
      for (const d of set.index.discs) { sections.push(d.section); zs.push(d.z); }
      for (const o of set.index.outlines) { sections.push(o.section); zs.push(o.z); }
    }
    return sections.length ? fitZMap(sections, zs) : null;
  }

  /** Use this section-to-Z map (`fitZMap`) and not the one fitted from the annotations. */
  setZMap(map) {
    this.zMap = map;
    this._zMapGiven = map !== null;
    if (!map) this.zMap = this._fitZ();
    this._contours.clear();
    this._gaps.clear();
    this._request();
  }

  /**
   * A surface layer: `{ vertices, faces, values, color, name, visible, fill, alpha }`.
   * `vertices` and `faces` are a MeshDTO's flat arrays; `values` one error number per
   * vertex paints the contour by the error ramp (to `setScale`'s scale), else it is
   * `color`.  The face index is built here, once, and kept while the same arrays are given.
   */
  setSurface(key, spec) {
    const old = this.surfaces.get(key);
    const same = old && old.vertices === spec.vertices && old.faces === spec.faces;
    this.surfaces.set(key, {
      key,
      name: spec.name ?? key,
      vertices: spec.vertices,
      faces: spec.faces,
      index: same ? old.index : buildSliceIndex(spec.vertices, spec.faces),
      values: spec.values || null,
      color: spec.color || '#ffd400',
      visible: spec.visible !== false,
      fill: spec.fill !== false,
      alpha: spec.alpha ?? 1,
    });
    // The contour and the gaps are the same while the mesh and its values are; a color or
    // visibility change only repaints.
    if (!(same && old.values === (spec.values || null))) {
      this._contours.delete(key);
      this._gaps.delete(key);
    }
    this._refit();
    this._request();
  }

  removeSurface(key) {
    this.surfaces.delete(key);
    this._contours.delete(key);
    this._gaps.delete(key);
    this._refit();
    this._request();
  }

  /** The arrow hook: `(section) => [{ x0, y0, x1, y1, color? }]` in um, or null for none. */
  setArrows(source) {
    this.arrows = source;
    this._request();
  }

  /** The error scale (um) the contour and gap colors reach red at. */
  setScale(scaleUm) {
    if (isNumber(scaleUm) && scaleUm > 0) { this.scaleUm = scaleUm; this._request(); }
  }

  /** Which surface the circles' stroke colors show the gap to: a key, or null for none (the first visible by default). */
  setGapSurface(key) {
    this._gapSurface = key;
    this._request();
  }

  // -- the section --------------------------------------------------------------------

  /** The annotated sections of every set, ascending. */
  get sections() { return this._sections; }

  /** The sections the keys may reach: the annotated range. */
  get range() {
    const s = this._sections;
    return s.length ? { min: s[0], max: s[s.length - 1] } : { min: this.section ?? 0, max: this.section ?? 0 };
  }

  /** The Z (um) of the current section, or null before there is a map. */
  get z() {
    return this.zMap && this.section !== null ? sectionToZ(this.zMap, this.section) : null;
  }

  /** Show `section` (clamped to the range).  `notify` also calls `onSection`, as a key does. */
  setSection(section, { notify = false } = {}) {
    if (!isNumber(section)) return;
    if (!this._sections.length) {
      // Nothing to clamp it to: clamping to the empty range would pin the section to 0 for good.  It waits
      // for the annotations, and is applied (within their range) when they arrive.
      this._pendingSection = Math.round(section);
      return;
    }
    this._pendingSection = null;
    const range = this.range;
    const next = clamp(Math.round(section), range.min, range.max);
    const changed = next !== this.section;
    this.section = next;
    if (changed) {
      this._hover = null;
      this._hideTip();
      // A view nobody has panned or zoomed follows the content from section to section; one that
      // has been keeps its framing (and comes back with a double-click or `0`).
      this._refit();
    }
    this._request();
    if (notify && changed) this.callbacks.onSection?.(next);
  }

  // -- the view -----------------------------------------------------------------------

  getView() { return { ...this.view }; }

  /** Set the view (another canvas's, under Sync); `notify` also calls `onView`. */
  setView(view, { notify = false } = {}) {
    this.view = { cx: view.cx, cy: view.cy, scale: clamp(view.scale, VIEW_LIMITS.minScale, VIEW_LIMITS.maxScale) };
    this._moved = true;
    this._request();
    if (notify) this.callbacks.onView?.(this.getView());
  }

  /** Fit the view to what is on the current section (its annotations and contours), else to the whole cell; it then follows the content again from section to section. */
  resetView() {
    this._moved = false;
    this._refit({ notify: false });
    this._request();
    this.callbacks.onView?.(this.getView());
  }

  _contentBox() {
    let box = null;
    const add = (minX, minY, maxX, maxY) => {
      box = box
        ? { minX: Math.min(box.minX, minX), minY: Math.min(box.minY, minY), maxX: Math.max(box.maxX, maxX), maxY: Math.max(box.maxY, maxY) }
        : { minX, minY, maxX, maxY };
    };
    for (const set of this.sets.values()) {
      const here = annotationsAt(set.index, this.section);
      for (const d of here.discs) add(d.x - d.r, d.y - d.r, d.x + d.r, d.y + d.r);
      for (const o of here.outlines) {
        for (let k = 0; k < o.points.length; k += 2) add(o.points[k], o.points[k + 1], o.points[k], o.points[k + 1]);
      }
    }
    for (const surface of this.surfaces.values()) {
      if (!surface.visible) continue;
      const c = this._contour(surface);
      for (let s = 0; s < c.set.count; s += 1) {
        const p = c.set.positions;
        add(Math.min(p[6 * s], p[6 * s + 3]), Math.min(p[6 * s + 1], p[6 * s + 4]),
          Math.max(p[6 * s], p[6 * s + 3]), Math.max(p[6 * s + 1], p[6 * s + 4]));
      }
    }
    if (box) return box;
    for (const set of this.sets.values()) {
      const b = set.index.bounds;
      if (b) add(b.minX, b.minY, b.maxX, b.maxY);
    }
    return box;
  }

  /** Stop drawing while the pane is out of sight (and draw once when it returns). */
  setActive(active) {
    this._active = !!active;
    if (this._active) this._request();
  }

  /**
   * The room the canvas has: the host's content box.  Its border and padding are the host's own
   * (a canvas the size of the border box overflows a host that adds either: scrollbars on an
   * `overflow: auto` one, the last pixels and the scale bar cut off on an `overflow: hidden` one).
   * Where the page cannot say (no computed style, or a host with no client size) the box of the
   * element, as before.
   */
  _hostSize() {
    const host = this.host;
    const style = typeof globalThis.getComputedStyle === 'function' && host.clientWidth && host.clientHeight
      ? globalThis.getComputedStyle(host) : null;
    if (style) {
      const px = (v) => Number.parseFloat(v) || 0;
      return {
        width: Math.max(0, Math.round(host.clientWidth - px(style.paddingLeft) - px(style.paddingRight))),
        height: Math.max(0, Math.round(host.clientHeight - px(style.paddingTop) - px(style.paddingBottom))),
      };
    }
    const rect = host.getBoundingClientRect ? host.getBoundingClientRect() : { width: 0, height: 0 };
    return {
      width: Math.max(0, Math.round(rect.width || host.clientWidth || 0)),
      height: Math.max(0, Math.round(rect.height || host.clientHeight || 0)),
    };
  }

  resize() {
    const dpr = globalThis.devicePixelRatio || 1;
    const { width, height } = this._hostSize();
    if (width === this._size.width && height === this._size.height && dpr === this._size.dpr) return;
    this._size = { width, height, dpr };
    this.canvas.width = Math.max(1, Math.round(width * dpr));
    this.canvas.height = Math.max(1, Math.round(height * dpr));
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this._refit();
    this._request();
  }

  // -- input -----------------------------------------------------------------------

  _key(event) {
    const action = sliceKeyAction(event, { section: this.section ?? 0, sections: this._sections, range: this.range });
    if (!action) return;
    event.preventDefault();
    if (action.kind === 'section') {
      // With no annotated section there is none to go to: the key is the canvas's, and changes nothing.
      if (this.section !== null) this.setSection(action.section, { notify: true });
    } else if (action.kind === 'zoom') {
      const { width, height } = this._size;
      this.setView(zoomAt(this.view, this._size, width / 2, height / 2, action.factor), { notify: true });
    } else if (action.kind === 'reset') this.resetView();
  }

  _local(event) {
    const rect = this.canvas.getBoundingClientRect ? this.canvas.getBoundingClientRect() : { left: 0, top: 0 };
    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  _wheel(event) {
    event.preventDefault();
    const [px, py] = this._local(event);
    // One notch is ZOOM_STEP; a trackpad's small deltas scale it down so the zoom is smooth.
    const notches = clamp(-event.deltaY / (event.deltaMode === 1 ? 3 : 100), -4, 4);
    this.setView(zoomAt(this.view, this._size, px, py, ZOOM_STEP ** notches), { notify: true });
  }

  _down(event) {
    if (event.button !== undefined && event.button !== 0) return;
    this.canvas.focus?.();
    this.canvas.setPointerCapture?.(event.pointerId);
    this._drag = { x: event.clientX, y: event.clientY, view: this.getView(), travelled: 0 };
  }

  _move(event) {
    if (this._drag) {
      const dx = event.clientX - this._drag.x;
      const dy = event.clientY - this._drag.y;
      this._drag.travelled = Math.max(this._drag.travelled, Math.hypot(dx, dy));
      if (this._drag.travelled > 3) {
        this.setView(panView(this._drag.view, dx, dy), { notify: true });
        this._hideTip();
      }
      return;
    }
    this._hoverAt(...this._local(event));
  }

  _up(event) {
    const drag = this._drag;
    this._drag = null;
    this.canvas.releasePointerCapture?.(event.pointerId);
    if (drag && drag.travelled <= 3 && event.type === 'pointerup') {
      const hit = this._pick(...this._local(event));
      if (hit) this.callbacks.onSelect?.(this._info(hit));
    }
  }

  _leave() {
    if (this._drag) return;
    this._hover = null;
    this._hideTip();
    this.callbacks.onHover?.(null);
    this._request();
  }

  _pick(px, py) {
    if (this.section === null) return null;
    const [x, y] = screenToWorld(this.view, this._size, px, py);
    // A few pixels of slack, so a small circle can be hit.
    return pickAnnotation([...this.sets.values()].reverse(), this.section, x, y, 3 / this.view.scale);
  }

  _info(hit) {
    const { set, annotation: a } = hit;
    return { key: set.key, volume: set.volume, id: a.id, section: a.section, x: a.x, y: a.y, z: a.z, r: a.r, kind: a.kind };
  }

  _hoverAt(px, py) {
    const hit = this._pick(px, py);
    const same = hit && this._hover && hit.annotation === this._hover.annotation;
    if (same) { this._placeTip(px, py); return; }
    this._hover = hit;
    if (!hit) { this._hideTip(); this.callbacks.onHover?.(null); this._request(); return; }
    const gaps = [];
    for (const surface of this.surfaces.values()) {
      if (surface.visible) gaps.push([surface.name, this._gapOf(surface, hit.set, hit.annotation)]);
    }
    this.tip.textContent = describeAnnotation(hit.set, hit.annotation, gaps).join('\n');
    this.tip.hidden = false;
    this._placeTip(px, py);
    this.callbacks.onHover?.(this._info(hit));
    this._request();
  }

  _placeTip(px, py) {
    const { width, height } = this._size;
    const left = px > width - 260 ? px - 250 : px + 14;
    const top = py > height - 120 ? py - 90 : py + 14;
    this.tip.style.left = `${Math.max(4, left)}px`;
    this.tip.style.top = `${Math.max(4, top)}px`;
  }

  _hideTip() {
    this.tip.hidden = true;
  }

  // -- the per-section work, cached ----------------------------------------------------

  /** The contour of a surface at the current section: segments, loops and the grid, cached. */
  _contour(surface) {
    const key = `${surface.key}@${this.section}`;
    let hit = this._contours.get(surface.key);
    if (hit && hit.key === key && hit.z === this.z) return hit;
    const z = this.z;
    const set = z === null ? { count: 0, positions: new Float64Array(0), values: null }
      : contourSegments(surface.index, z, surface.values);
    const grid = buildSegmentGrid(set.positions, set.orient);
    hit = { key, z, set, grid, loops: null };
    this._contours.set(surface.key, hit);
    return hit;
  }

  /** The gap of an annotation's rim from a surface's contour (cached for the section). */
  _gapOf(surface, set, annotation) {
    const contour = this._contour(surface);
    let cache = this._gaps.get(surface.key);
    if (!cache || cache.key !== contour.key) { cache = { key: contour.key, byAnnotation: new Map() }; this._gaps.set(surface.key, cache); }
    const id = `${set.key}:${annotation.kind}:${annotation.id}`;
    if (!cache.byAnnotation.has(id)) cache.byAnnotation.set(id, annotationGap(contour.grid, ringSamples(annotation)));
    return cache.byAnnotation.get(id);
  }

  // -- drawing ---------------------------------------------------------------------------

  _request() {
    this._dirty = true;
    if (this._frame || !this._active) return;
    this._frame = this._schedule(() => { this._frame = 0; if (this._active && this._dirty) this.draw(); });
  }

  /** Draw the section now. */
  draw() {
    this._dirty = false;
    const { ctx } = this;
    const { width, height, dpr } = this._size;
    if (!ctx || !width || !height) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = BACKGROUND;
    ctx.fillRect(0, 0, width, height);
    if (this.section === null) { this._hud(ctx, 'No annotations to show a section of.'); return; }
    const tx = (x) => width / 2 + (x - this.view.cx) * this.view.scale;
    const ty = (y) => height / 2 + (y - this.view.cy) * this.view.scale;
    const surfaces = [...this.surfaces.values()].filter((s) => s.visible);
    for (const surface of surfaces) this._drawFill(ctx, surface, tx, ty);
    for (const set of this.sets.values()) this._drawGhosts(ctx, set, tx, ty);
    const reference = this._gapSurface === null ? null
      : (this._gapSurface !== undefined ? this.surfaces.get(this._gapSurface) : surfaces[0]) || null;
    for (const set of this.sets.values()) this._drawAnnotations(ctx, set, reference, tx, ty);
    for (const surface of surfaces) this._drawContour(ctx, surface, tx, ty);
    this._drawArrows(ctx, tx, ty);
    this._drawHover(ctx, reference, tx, ty);
    this._hud(ctx, null);
  }

  _drawFill(ctx, surface, tx, ty) {
    if (!surface.fill) return;
    const c = this._contour(surface);
    if (!c.loops) c.loops = contourLoops(c.set);
    ctx.save();
    ctx.globalAlpha = 0.1 * surface.alpha;
    ctx.fillStyle = surface.color;
    ctx.beginPath();
    for (const loop of c.loops) {
      if (!loop.closed) continue;
      const p = loop.points;
      ctx.moveTo(tx(p[0]), ty(p[1]));
      for (let k = 3; k < p.length; k += 3) ctx.lineTo(tx(p[k]), ty(p[k + 1]));
      ctx.closePath();
    }
    // The loops run the way their segments say (the solid on their left), so the winding of a
    // point is the number of bodies enclosing it, a hole cancels the body round it, and
    // where closed pieces overlap the tint is one fill and not holes.
    ctx.fill('nonzero');
    ctx.restore();
  }

  _drawContour(ctx, surface, tx, ty) {
    const c = this._contour(surface);
    const p = c.set.positions;
    ctx.save();
    ctx.globalAlpha = surface.alpha;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (surface.values && c.set.values) {
      for (const group of bucketByError(c.set.values, c.set.count, this.scaleUm)) {
        ctx.strokeStyle = group.color;
        ctx.beginPath();
        for (const s of group.segments) {
          ctx.moveTo(tx(p[6 * s]), ty(p[6 * s + 1]));
          ctx.lineTo(tx(p[6 * s + 3]), ty(p[6 * s + 4]));
        }
        ctx.stroke();
      }
    } else {
      ctx.strokeStyle = surface.color;
      ctx.beginPath();
      for (let s = 0; s < c.set.count; s += 1) {
        ctx.moveTo(tx(p[6 * s]), ty(p[6 * s + 1]));
        ctx.lineTo(tx(p[6 * s + 3]), ty(p[6 * s + 4]));
      }
      ctx.stroke();
    }
    ctx.restore();
  }

  _shapePath(ctx, a, tx, ty) {
    if (a.kind === 'outline') {
      for (const poly of [a.points, ...a.cutouts]) {
        ctx.moveTo(tx(poly[0]), ty(poly[1]));
        for (let k = 2; k < poly.length; k += 2) ctx.lineTo(tx(poly[k]), ty(poly[k + 1]));
        ctx.closePath();
      }
    } else {
      ctx.moveTo(tx(a.x) + a.r * this.view.scale, ty(a.y));
      ctx.arc(tx(a.x), ty(a.y), a.r * this.view.scale, 0, 2 * Math.PI);
    }
  }

  _drawGhosts(ctx, set, tx, ty) {
    if (!this.ghost) return;
    const { below, above } = neighborSections(set.index.sections, this.section, this.ghost);
    ctx.save();
    ctx.strokeStyle = set.color;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    [...below, ...above].forEach((s) => {
      const distance = Math.abs(s - this.section);
      ctx.globalAlpha = 0.28 / Math.sqrt(distance);
      const here = annotationsAt(set.index, s);
      ctx.beginPath();
      for (const d of here.discs) this._shapePath(ctx, d, tx, ty);
      for (const o of here.outlines) this._shapePath(ctx, o, tx, ty);
      ctx.stroke();
    });
    ctx.restore();
  }

  _drawAnnotations(ctx, set, reference, tx, ty) {
    const here = annotationsAt(set.index, this.section);
    const shapes = [...here.outlines, ...here.discs];
    ctx.save();
    for (const a of shapes) {
      // The set's own color as a halo, so which set this is stays visible under a gap color.
      ctx.setLineDash(set.dash || []);
      ctx.strokeStyle = set.color;
      ctx.globalAlpha = 0.45;
      ctx.lineWidth = 4;
      ctx.beginPath();
      this._shapePath(ctx, a, tx, ty);
      ctx.stroke();
      const gap = reference ? this._gapOf(reference, set, a) : null;
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = gap ? gapColor(gap.meanSigned, this.scaleUm) : set.color;
      if (a.closedCurve) ctx.setLineDash([4, 3]);
      ctx.beginPath();
      this._shapePath(ctx, a, tx, ty);
      ctx.stroke();
      // The gap as a number beside the circle, when the circle is big enough to carry it.
      if (gap && a.r * this.view.scale >= 10) {
        ctx.setLineDash([]);
        ctx.fillStyle = INK;
        ctx.font = '11px system-ui, sans-serif';
        ctx.textBaseline = 'middle';
        const sign = gap.meanSigned >= 0 ? '+' : '−';
        haloText(ctx, `${sign}${Math.abs(gap.meanSigned).toFixed(2)}`, tx(a.x) + a.r * this.view.scale + 5, ty(a.y));
      }
    }
    ctx.restore();
  }

  _drawHover(ctx, reference, tx, ty) {
    if (!this._hover) return;
    const { set, annotation: a } = this._hover;
    ctx.save();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    this._shapePath(ctx, a, tx, ty);
    ctx.stroke();
    const gap = reference ? this._gapOf(reference, set, a) : null;
    if (gap) {
      // From the rim point farthest from the contour to the contour point nearest it.
      const w = gap.worst;
      ctx.strokeStyle = gapColor(gap.worst.signed, this.scaleUm);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(tx(w.x), ty(w.y));
      ctx.lineTo(tx(w.tx), ty(w.ty));
      ctx.stroke();
    }
    ctx.restore();
  }

  _drawArrows(ctx, tx, ty) {
    if (!this.arrows) return;
    const list = this.arrows(this.section) || [];
    ctx.save();
    ctx.lineWidth = 1.5;
    ctx.lineCap = 'round';
    for (const a of list) {
      const x0 = tx(a.x0);
      const y0 = ty(a.y0);
      const x1 = tx(a.x1);
      const y1 = ty(a.y1);
      const len = Math.hypot(x1 - x0, y1 - y0);
      if (len < 0.5) continue;
      ctx.strokeStyle = a.color || '#f0b44c';
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      const barb = Math.min(8, len * 0.4);
      const angle = Math.atan2(y1 - y0, x1 - x0);
      for (const turn of [-0.45, 0.45]) {
        ctx.moveTo(x1, y1);
        ctx.lineTo(x1 - barb * Math.cos(angle + turn), y1 - barb * Math.sin(angle + turn));
      }
      ctx.stroke();
    }
    ctx.restore();
  }

  _hud(ctx, message) {
    const { width, height } = this._size;
    ctx.save();
    ctx.font = '12px system-ui, sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillStyle = INK;
    if (message) { haloText(ctx, message, 10, 8); ctx.restore(); return; }
    const z = this.z;
    haloText(ctx, `Section ${this.section}${z === null ? '' : ` · z ${z.toFixed(2)} µm`}`, 10, 8);
    ctx.fillStyle = MUTED;
    const lines = [];
    for (const set of this.sets.values()) {
      const here = annotationsAt(set.index, this.section);
      const n = here.discs.length + here.outlines.length;
      if (n) lines.push(`${set.label}: ${n} annotation${n === 1 ? '' : 's'}`);
      else {
        const near = neighborSections(set.index.sections, this.section, 1);
        const parts = [near.below.length ? `${near.below[0]} below` : '', near.above.length ? `${near.above[0]} above` : ''].filter(Boolean);
        lines.push(`${set.label}: none here${parts.length ? ` (nearest ${parts.join(', ')})` : ''}`);
      }
    }
    for (const surface of this.surfaces.values()) {
      if (surface.visible && !this._contour(surface).set.count) lines.push(`${surface.name}: the surface does not reach this section`);
    }
    lines.forEach((line, k) => haloText(ctx, line, 10, 26 + 15 * k));
    // The scale bar.
    const bar = scaleBarLength(this.view.scale);
    ctx.strokeStyle = INK;
    ctx.fillStyle = INK;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(10, height - 14);
    ctx.lineTo(10 + bar.px, height - 14);
    ctx.stroke();
    ctx.textBaseline = 'bottom';
    haloText(ctx, `${bar.um >= 1 ? bar.um : bar.um.toPrecision(1)} µm`, 10, height - 18);
    ctx.restore();
  }

  /** Take the canvas down and let go of every listener. */
  dispose() {
    for (const [type, fn] of Object.entries(this._on)) this.canvas.removeEventListener(type, fn);
    this._resizer?.disconnect();
    this._active = false;
    this.canvas.remove?.();
    this.tip.remove?.();
    this.sets.clear();
    this.surfaces.clear();
    this._contours.clear();
    this._gaps.clear();
  }
}

/** CSS for the canvas and its tooltip, which the host page adds once (`installSliceStyles`). */
export const SLICE_CSS = `
.slice-canvas { display: block; width: 100%; height: 100%; background: ${BACKGROUND}; cursor: grab; touch-action: none; outline: none; }
.slice-canvas:active { cursor: grabbing; }
.slice-canvas:focus-visible { box-shadow: inset 0 0 0 2px var(--accent, #6b9bff); }
.slice-tip { position: absolute; z-index: 2; pointer-events: none; white-space: pre; max-width: 260px; padding: 6px 8px;
  font: 12px/1.35 system-ui, sans-serif; color: #e8eaed; background: rgba(16, 20, 24, 0.92); border: 1px solid #2e343b; border-radius: 6px; }
`;

/** Add `SLICE_CSS` to the document once. */
export function installSliceStyles(doc = globalThis.document) {
  if (!doc || doc.getElementById?.('slice-styles')) return;
  const style = doc.createElement('style');
  style.id = 'slice-styles';
  style.textContent = SLICE_CSS;
  (doc.head || doc.documentElement).append(style);
}
