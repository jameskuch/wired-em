// The Position shift layer's data: where each annotation was and where it is
// drawn, as numbers and vertex buffers.  Pure functions, no DOM and no three.js,
// so node can test every rule (tests/js/test_shifts.mjs) and the main window and
// the Render compare panes share one implementation.
//
// An annotation passes through up to three stages (Structure.apply_transform adds
// the moves, in volume nm, to Viking's stored VolumeX/VolumeY in this order):
//
//   stored      Viking's own stored position
//   registered  + the volume's current section transforms (`live_shift_um`):
//               where Viking draws it now, and what Goto Location takes
//   corrected   + Viking's section correction (`shift_um`): the node's `xyz_um`
//
// so, from the fields of one node of ONE server copy of the cell:
//
//   stored     = xyz_um - live_shift_um - shift_um
//   registered = xyz_um - shift_um
//   corrected  = xyz_um              (a null move counts as zero)
//
// Every move lies in its section plane: there is no Z move.  A person's own move
// (manualmoves.js, `manual_shift_um`: X, Y and Z, after the correction) is taken
// off `xyz_um` first, so the stages and the arrows are Viking's; the manual moves
// are drawn by the app, an arrow each.  A *pair* is the move
// between two stages -- 'correction' (registered to corrected), 'transforms'
// (stored to registered) or 'both' (stored to corrected).  The arrow's head is the
// real position of the pair's later stage, so it sits on the ring the user already
// sees; only its tail (and the original ring) is exaggerated by k.  Lengths in this
// file are micrometers, sections are integers, and positions stay float64 until a
// vertex buffer is built (Float32Array), per the codebase's precision rule.
//
// Colors are sRGB components in 0..1, like colormaps.js; the renderer converts if
// its working space is linear.

import { WIDTH_DEFAULT, WIDTH_MAX, WIDTH_MIN } from './fatlines.js';

/** Display keys this layer persists (merged into state.js's DEFAULT_DISPLAY). */
export const SHIFT_DEFAULTS = Object.freeze({
  showShift: false,
  shiftPair: 'correction',
  shiftArrows: true,
  shiftRings: false,
  // null = Auto (autoScale); a number is the user's exaggeration.
  shiftScale: null,
  // "Only shifts above", true um: one threshold for All sections, one for a slice.
  shiftMin: 0.25,
  shiftMinSlice: 0.05,
  shiftSlice: false,
  // One slice at a time (2026-10-22): the slice is the plane's section and this many
  // sections either side of it, drawn at full strength; everything else of the cell
  // (its other arrows and rings, its surface, rings, skeleton and synapses) at
  // `shiftOutside` of its opacity, red above the slice and blue below.  Replaces
  // shiftWindow (the sections drawn at all) and shiftSliceDim (the strength of those
  // off the plane), whose two meanings overlapped.
  shiftSliceSpan: 0,
  shiftColor: 'magnitude',
  // "Full color at", um; null = Auto (the 95th percentile of the moved).
  shiftColorMax: null,
  shiftThrough: true,
  shiftOpacity: 0.9,
  // How wide the arrows are on the screen, CSS pixels (fatlines.js draws them).
  shiftWidth: WIDTH_DEFAULT,
  // While an annotation is selected (the yellow, red or blue marker) and its own
  // arrow is drawn in its marker's color, every other arrow and ring is drawn at
  // this share of its opacity: 0 hides them, 1 leaves them as they are.  Almost
  // nothing by default (2026-10-20: "deemphasis ... to almost nothing").
  shiftFocusDim: 0.06,
  // One slice at a time: everything of the cell outside the slice at this share of its
  // opacity; 0 hides it ("make opacity 0.05 what they currently are", 2026-10-22).
  shiftOutside: 0.05,
});

export const SHIFT_KEYS = Object.freeze(Object.keys(SHIFT_DEFAULTS));
export const SHIFT_PAIRS = Object.freeze(['correction', 'transforms', 'both']);
export const SHIFT_COLORS = Object.freeze(['magnitude', 'direction', 'cell']);

/** The stage each pair starts from and ends at. */
export const PAIR_STAGES = Object.freeze({
  correction: Object.freeze(['registered', 'corrected']),
  transforms: Object.freeze(['stored', 'registered']),
  both: Object.freeze(['stored', 'corrected']),
});

/** Names for the Between select. */
export const PAIR_LABELS = Object.freeze({
  correction: 'Section correction',
  transforms: 'Current transforms',
  both: 'Both',
});

/** Short names for the header summary. */
export const PAIR_SHORT = Object.freeze({ correction: 'Correction', transforms: 'Transforms', both: 'Both' });

/** A move shorter than this (0.5 nm) is not a move: the server counts it the same way. */
export const MOVED_UM = 0.0005;
/** The strongest marks kept in the All sections view, and in the slice view's buffer. */
export const ALL_CAP = 2000;
export const SLICE_CAP = 15000;
/** Auto exaggeration: this fraction of the cell's bounding-box diagonal over the 95th percentile. */
export const AUTO_FRACTION = 0.025;
export const SCALE_MIN = 1;
export const SCALE_MAX = 100;
/** An exaggerated arrow is drawn no longer than this fraction of the diagonal ... */
export const LENGTH_CAP_FRACTION = 0.10;
/** ... unless its true shift is already longer: that is drawn at true length. */
export const BARB_FRACTION = 0.30;
export const BARB_ANGLE = (25 * Math.PI) / 180;
export const RING_SEGMENTS = 16;
/** Vertices per arrow (LineSegments: shaft, two barbs). */
export const ARROW_VERTS = 6;
export const CLOSED_CURVE = 6;
/** The most sections either side a slice takes in (the panel's slider stops at SPAN_SLIDER). */
export const SPAN_MAX = 100;
export const SPAN_SLIDER = 20;

/** The ring polygon's unit circle, RING_SEGMENTS + 1 points (the last closes it). */
const UNIT_COS = Float64Array.from({ length: RING_SEGMENTS + 1 }, (_, k) => Math.cos((2 * Math.PI * k) / RING_SEGMENTS));
const UNIT_SIN = Float64Array.from({ length: RING_SEGMENTS + 1 }, (_, k) => Math.sin((2 * Math.PI * k) / RING_SEGMENTS));

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const isNumber = (v) => typeof v === 'number' && Number.isFinite(v);

// -- the display keys ------------------------------------------------------------

/**
 * The layer's display keys made safe.  A saved workspace or localStorage can carry
 * a value the controls would never produce (the 2026-08-31 incident was exactly
 * that), so each key falls back to its default rather than reaching the math.
 */
export function normalizeShiftDisplay(saved) {
  // null, a string or an array in a restored session are all "nothing saved".
  const display = saved !== null && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
  const d = SHIFT_DEFAULTS;
  const out = {};
  const bool = (key) => { out[key] = typeof display[key] === 'boolean' ? display[key] : d[key]; };
  // A nullable key is Auto when null, and also when it is not a positive number.
  const num = (key, lo, hi, nullable = false) => {
    const v = display[key];
    if (nullable) { out[key] = isNumber(v) && v > 0 ? clamp(v, lo, hi) : null; return; }
    out[key] = isNumber(v) ? clamp(v, lo, hi) : d[key];
  };
  bool('showShift'); bool('shiftArrows'); bool('shiftRings'); bool('shiftSlice'); bool('shiftThrough');
  out.shiftPair = SHIFT_PAIRS.includes(display.shiftPair) ? display.shiftPair : d.shiftPair;
  out.shiftColor = SHIFT_COLORS.includes(display.shiftColor) ? display.shiftColor : d.shiftColor;
  num('shiftScale', SCALE_MIN, SCALE_MAX, true);
  num('shiftMin', 0, 1e6);
  num('shiftMinSlice', 0, 1e6);
  num('shiftColorMax', 1e-6, 1e6, true);
  num('shiftOpacity', 0.05, 1);
  num('shiftWidth', WIDTH_MIN, WIDTH_MAX);
  num('shiftFocusDim', 0, 1);
  num('shiftOutside', 0, 1);
  out.shiftSliceSpan = isNumber(display.shiftSliceSpan)
    ? clamp(Math.round(display.shiftSliceSpan), 0, SPAN_MAX) : d.shiftSliceSpan;
  return out;
}

/** The threshold in force: slice view and All sections each remember their own. */
export function thresholdFor(display) {
  const d = normalizeShiftDisplay(display);
  return d.shiftSlice ? d.shiftMinSlice : d.shiftMin;
}

/** How many marks the buffer may hold: the slab is cut from it, so the slice's is larger. */
export function capFor(display) {
  return normalizeShiftDisplay(display).shiftSlice ? SLICE_CAP : ALL_CAP;
}

// -- the stages -------------------------------------------------------------------

const ZERO = Object.freeze([0, 0]);
const ZERO3 = Object.freeze([0, 0, 0]);
const move = (v) => (Array.isArray(v) && isNumber(v[0]) && isNumber(v[1]) ? v : ZERO);
const move3 = (v) => (Array.isArray(v) && isNumber(v[0]) && isNumber(v[1]) && isNumber(v[2]) ? v : ZERO3);

/**
 * Where the correction left a node, before a person's own move: `xyz_um` less
 * `manual_shift_um`, and its section less `manual_dz` (Viking's section).
 */
export function correctedXyz(node) {
  const p = node.xyz_um;
  const h = move3(node.manual_shift_um);
  return [p[0] - h[0], p[1] - h[1], p[2] - h[2]];
}

/** A node's own section in Viking: `z_section` less a manual Z move. */
export function vikingSection(node) {
  return isNumber(node.manual_dz) ? node.z_section - node.manual_dz : node.z_section;
}

/** `[x, y]` positions of one node at each stage, um. */
export function stagePositions(node) {
  const [x, y] = correctedXyz(node);
  const c = move(node.shift_um);
  const l = move(node.live_shift_um);
  const registered = [x - c[0], y - c[1]];
  return {
    stored: [registered[0] - l[0], registered[1] - l[1]],
    registered,
    corrected: [x, y],
  };
}

/**
 * One node's move across `pair`, as `{ head, shift }`: `head` the real position of
 * the pair's later stage, `shift` the vector from the earlier stage to it.  The
 * vector is read from the move fields, not taken from two positions, so it is
 * exactly `shift_um` (correction), `live_shift_um` (transforms) or their sum.
 */
export function pairEnds(node, pair) {
  const [x, y] = correctedXyz(node);
  const c = move(node.shift_um);
  const l = move(node.live_shift_um);
  switch (pair) {
    case 'transforms': return { head: [x - c[0], y - c[1]], shift: [l[0], l[1]] };
    case 'both': return { head: [x, y], shift: [c[0] + l[0], c[1] + l[1]] };
    case 'correction': return { head: [x, y], shift: [c[0], c[1]] };
    default: throw new RangeError(`unknown pair ${pair}`);
  }
}

// -- which pair the user sees -----------------------------------------------------------

/**
 * The pairs a volume can show at all: all three where its current section
 * transforms are read here (`/api/volumes` `has_transforms`), else the section
 * correction alone -- without the transforms the stored and the registered
 * positions are one, and Both is the correction.
 */
export function pairsFor(hasTransforms = true) {
  return hasTransforms ? SHIFT_PAIRS : Object.freeze(['correction']);
}

/**
 * Which pairs this copy of the cell can show, and why not when it can't.
 *
 * `summary` is the cell's summary (`section_correction`, `live_registration`); a
 * stage must be `applied` and have moved something.  `counts` ({ correction,
 * transforms }, from `moveCounts`) overrides the summary's `n_moved`, which also
 * counts the cell's child structures.  `hasTransforms: false` (the volume's
 * transforms are not read here) leaves the correction as the only pair, whatever
 * a saved state asks for.
 */
export function pairAvailability(summary, counts = null, { hasTransforms = true } = {}) {
  const stage = (info, what, count, plural = false) => {
    const [is, was] = plural ? ['are', 'were'] : ['is', 'was'];
    if (!info?.requested) return { available: false, reason: `${what} ${is} off for this cell (Registration)` };
    if (!info.applied) return { available: false, reason: info.note || `${what} ${was} asked for and not applied` };
    const moved = count ?? info.n_moved ?? 0;
    if (!moved) return { available: false, reason: `${what} moved no annotation of this cell` };
    return { available: true, reason: '' };
  };
  const correction = stage(summary?.section_correction, "Viking's section correction", counts?.correction);
  if (!hasTransforms) {
    const none = "This volume's current section transforms are not read here: its annotations are drawn where Viking stores them";
    return {
      correction,
      transforms: { available: false, reason: none },
      both: { available: false, reason: `${none}, so Both is the section correction` },
    };
  }
  const transforms = stage(summary?.live_registration, "The volume's current section transforms",
    counts?.transforms, true);
  const both = correction.available || transforms.available
    ? { available: true, reason: '' }
    : { available: false, reason: 'Neither the section correction nor the current transforms moved this cell' };
  return { correction, transforms, both };
}

/**
 * The pair actually drawn: the one asked for if it can be shown, else the
 * correction, then the transforms; `null` when nothing moved.
 */
export function effectivePair(wanted, availability) {
  const order = [wanted, 'correction', 'transforms'];
  for (const pair of order) if (availability[pair]?.available) return pair;
  return null;
}

/** How many of `nodes` each stage moved (the cell's own annotations, as `/nodes` answers). */
export function moveCounts(nodes) {
  const out = { correction: 0, transforms: 0 };
  for (const n of nodes) {
    const c = move(n.shift_um);
    const l = move(n.live_shift_um);
    if (Math.hypot(c[0], c[1]) > MOVED_UM) out.correction += 1;
    if (Math.hypot(l[0], l[1]) > MOVED_UM) out.transforms += 1;
  }
  return out;
}

// -- the field --------------------------------------------------------------------------

/** numpy's default percentile (linear interpolation) of an ascending array. */
export function percentileSorted(sorted, p) {
  const n = sorted.length;
  if (!n) return 0;
  const pos = (p / 100) * (n - 1);
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** Median, 95th and 99th percentile and maximum of the moves' lengths. */
export function shiftStats(mags) {
  const sorted = Float64Array.from(mags).sort();
  return {
    nMoved: sorted.length,
    median: percentileSorted(sorted, 50),
    p95: percentileSorted(sorted, 95),
    p99: percentileSorted(sorted, 99),
    max: sorted.length ? sorted[sorted.length - 1] : 0,
  };
}

/**
 * Everything a pair says about a cell, independent of threshold, cap, scale and
 * window: the moved annotations as typed arrays (one row each, in node order), the
 * cell's bounding box and section range, and the size statistics.
 *
 * `nodes` are `/nodes` rows (the cell's own annotations).  A row is *moved* when
 * its pair shift is longer than MOVED_UM.
 */
export function prepareField(nodes, pair = 'correction') {
  if (!SHIFT_PAIRS.includes(pair)) throw new RangeError(`unknown pair ${pair}`);
  const cap = nodes.length;
  const f = {
    index: new Int32Array(cap),
    id: new Float64Array(cap),
    section: new Int32Array(cap),
    z: new Float64Array(cap),
    cx: new Float64Array(cap),
    cy: new Float64Array(cap),
    hx: new Float64Array(cap),
    hy: new Float64Array(cap),
    dx: new Float64Array(cap),
    dy: new Float64Array(cap),
    mag: new Float64Array(cap),
    radius: new Float64Array(cap),
    curve: new Uint8Array(cap),
  };
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  let sMin = Infinity;
  let sMax = -Infinity;
  let zAtMin = 0;
  let zAtMax = 0;
  let r = 0;
  for (let i = 0; i < cap; i += 1) {
    const node = nodes[i];
    if (!node.xyz_um) continue;
    // Where the correction left it: a person's own move is drawn by the app, apart.
    const p = node.manual_shift_um ? correctedXyz(node) : node.xyz_um;
    if (!isNumber(p[0]) || !isNumber(p[1]) || !isNumber(p[2])) continue;
    for (let a = 0; a < 3; a += 1) {
      if (p[a] < min[a]) min[a] = p[a];
      if (p[a] > max[a]) max[a] = p[a];
    }
    const s = vikingSection(node);
    if (isNumber(s)) {
      if (s < sMin) { sMin = s; zAtMin = p[2]; }
      if (s > sMax) { sMax = s; zAtMax = p[2]; }
    }
    // The pair's head and shift (see pairEnds), without allocating per node.
    const c = move(node.shift_um);
    const l = move(node.live_shift_um);
    let sx = c[0];
    let sy = c[1];
    let hx = p[0];
    let hy = p[1];
    if (pair === 'transforms') { sx = l[0]; sy = l[1]; hx = p[0] - c[0]; hy = p[1] - c[1]; }
    else if (pair === 'both') { sx = c[0] + l[0]; sy = c[1] + l[1]; }
    const m = Math.hypot(sx, sy);
    if (!(m > MOVED_UM)) continue;
    f.index[r] = i;
    f.id[r] = node.id;
    f.section[r] = s;
    f.z[r] = p[2];
    f.cx[r] = p[0];
    f.cy[r] = p[1];
    f.hx[r] = hx;
    f.hy[r] = hy;
    f.dx[r] = sx;
    f.dy[r] = sy;
    f.mag[r] = m;
    f.radius[r] = isNumber(node.radius_um) ? node.radius_um : 0;
    f.curve[r] = node.type_code === CLOSED_CURVE ? 1 : 0;
    r += 1;
  }
  const have = Number.isFinite(min[0]);
  const field = {
    pair,
    nNodes: cap,
    n: r,
    bbox: have
      ? { min, max, diagonal: Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) }
      : { min: [0, 0, 0], max: [0, 0, 0], diagonal: 0 },
    sections: Number.isFinite(sMin) ? { min: sMin, max: sMax } : { min: 0, max: 0 },
    // z of a section number: linear (Zum is Z times the section thickness).
    zMap: Number.isFinite(sMin)
      ? { s0: sMin, z0: zAtMin, dz: sMax > sMin ? (zAtMax - zAtMin) / (sMax - sMin) : 0 }
      : { s0: 0, z0: 0, dz: 0 },
    stats: null,
  };
  for (const key of Object.keys(f)) field[key] = f[key].slice(0, r);
  field.stats = shiftStats(field.mag);
  return field;
}

/** The z (um) of a section number in this cell, for the slice plane and the chip. */
export function zOfSection(field, section) {
  const m = field.zMap;
  return m.z0 + (section - m.s0) * m.dz;
}

/**
 * Auto exaggeration: `AUTO_FRACTION` of the bounding-box diagonal over the 95th
 * percentile of the moved annotations, in [1, 100].  It does not depend on the
 * camera, the threshold or the slice, so a number quoted for a cell stays true.
 */
export function autoScale(field) {
  const p95 = field.stats?.p95 ?? 0;
  if (!(p95 > 0) || !(field.bbox.diagonal > 0)) return SCALE_MIN;
  return clamp((AUTO_FRACTION * field.bbox.diagonal) / p95, SCALE_MIN, SCALE_MAX);
}

/** The exaggeration in force: the user's, else Auto. */
export function effectiveScale(display, field) {
  const d = normalizeShiftDisplay(display);
  return d.shiftScale ?? autoScale(field);
}

/** "Full color at" in force: the user's, else the 95th percentile of the moved. */
export function effectiveColorMax(display, field) {
  const d = normalizeShiftDisplay(display);
  if (d.shiftColorMax) return d.shiftColorMax;
  const p95 = field.stats?.p95 ?? 0;
  return p95 > 0 ? p95 : 1;
}

/**
 * How long an arrow of true length `trueLen` is drawn at exaggeration `k` in a
 * cell whose bounding-box diagonal is `diagonal`: k times as long up to `fullAt`
 * (the legend's Full color at), and at true scale past it, so a move longer than
 * the ramp's last color is drawn as the arrow of Full color at plus its excess,
 * never k times its outlier length (2026-10-16: on c1178 a few 1.4 to 4 um moves
 * at Auto's x13 ran to the cap and across the view; they now end within 11 um).
 * Longer moves still draw longer.  Never past LENGTH_CAP_FRACTION of the diagonal
 * either -- except that a shift already longer than that is drawn at its true
 * length (a 159 um outlier stays one arrow, not 2,000 um).
 */
export function drawnLength(trueLen, k, diagonal, fullAt = Infinity) {
  const cap = LENGTH_CAP_FRACTION * diagonal;
  const wanted = fullAt > 0 && trueLen > fullAt ? k * fullAt + (trueLen - fullAt) : k * trueLen;
  return wanted <= cap ? wanted : Math.max(cap, trueLen);
}

// -- the marks that are shown ------------------------------------------------------------

/**
 * The marks to draw: moved annotations of `field` at or above `min` (true um),
 * the strongest `cap` of them if there are more, ordered by section.
 *
 * Returns `{ rows, section, sections, starts, n, total, truncated }`: `rows` index
 * the field's arrays in draw order; `section[i]` is mark i's section (ascending);
 * `sections` the distinct ones, with `starts[j]` the first mark of `sections[j]`
 * (and `starts[sections.length] = n`), so a window of sections is a range of marks.
 * `total` is how many were at or above `min` before the cap.
 */
export function selectMarks(field, { min = 0, cap = ALL_CAP } = {}) {
  const candidates = [];
  for (let r = 0; r < field.n; r += 1) if (field.mag[r] >= min) candidates.push(r);
  const total = candidates.length;
  let kept = candidates;
  if (total > cap) {
    // The strongest `cap`; equal lengths keep the earlier row, so the cut is deterministic.
    kept = candidates.slice().sort((a, b) => field.mag[b] - field.mag[a] || a - b).slice(0, cap);
  }
  kept.sort((a, b) => field.section[a] - field.section[b] || a - b);
  const n = kept.length;
  const rows = Int32Array.from(kept);
  const section = new Int32Array(n);
  const sections = [];
  const starts = [];
  for (let i = 0; i < n; i += 1) {
    section[i] = field.section[rows[i]];
    if (i === 0 || section[i] !== section[i - 1]) {
      sections.push(section[i]);
      starts.push(i);
    }
  }
  starts.push(n);
  return {
    rows, section, n, total, truncated: total > cap,
    sections: Int32Array.from(sections), starts: Int32Array.from(starts),
  };
}

const lowerBound = (a, v) => {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] < v) lo = mid + 1; else hi = mid;
  }
  return lo;
};

/**
 * The marks of the sections `center - half .. center + half` (inclusive), as
 * `{ first, count }` in draw order: a binary search over the sorted sections, so
 * stepping the slice is a draw range and no upload.  An empty window is `count 0`.
 */
export function windowRange(sel, center, half) {
  const lo = lowerBound(sel.section, center - half);
  const hi = lowerBound(sel.section, center + half + 1);
  return { first: lo, count: Math.max(0, hi - lo) };
}

// -- the buffers ----------------------------------------------------------------------------

/**
 * Per mark, the exaggeration its arrow and ring are drawn with: `k` up to `fullAt`
 * (Full color at) and true scale past it, within the length cap (`drawnLength`).
 */
export function markScales(field, sel, k, fullAt = Infinity) {
  const out = new Float64Array(sel.n);
  const diag = field.bbox.diagonal;
  for (let i = 0; i < sel.n; i += 1) {
    const r = sel.rows[i];
    out[i] = drawnLength(field.mag[r], k, diag, fullAt) / field.mag[r];
  }
  return out;
}

/**
 * Arrow vertices for `sel`, as LineSegments positions: per mark the shaft from the
 * tail (`head - scale * shift`) to the head, then two barbs from the head back
 * along the shaft at +/-BARB_ANGLE, BARB_FRACTION of the drawn length, in the
 * section plane.  ARROW_VERTS vertices per mark.  `colors` (per mark, 3 floats)
 * expand to every vertex.
 */
export function buildArrows(field, sel, scales, colors = null) {
  const n = sel.n;
  const positions = new Float32Array(n * ARROW_VERTS * 3);
  const cos = Math.cos(BARB_ANGLE);
  const sin = Math.sin(BARB_ANGLE);
  for (let i = 0; i < n; i += 1) {
    const r = sel.rows[i];
    const s = scales[i];
    const hx = field.hx[r];
    const hy = field.hy[r];
    const z = field.z[r];
    const tx = hx - s * field.dx[r];
    const ty = hy - s * field.dy[r];
    // Unit vector from the head back toward the tail, and the barb length.
    const m = field.mag[r];
    const bx = -field.dx[r] / m;
    const by = -field.dy[r] / m;
    const barb = BARB_FRACTION * s * m;
    const o = i * ARROW_VERTS * 3;
    positions.set([
      tx, ty, z, hx, hy, z,
      hx, hy, z, hx + barb * (bx * cos - by * sin), hy + barb * (bx * sin + by * cos), z,
      hx, hy, z, hx + barb * (bx * cos + by * sin), hy + barb * (-bx * sin + by * cos), z,
    ], o);
  }
  return { positions, colors: colors ? expandColors(colors, n, ARROW_VERTS) : null, n, vertsPerMark: ARROW_VERTS };
}

/** One RGB per mark -> one per vertex (`perMark` vertices each). */
export function expandColors(colors, n, perMark) {
  const out = new Float32Array(n * perMark * 3);
  for (let i = 0; i < n; i += 1) {
    const r = colors[i * 3];
    const g = colors[i * 3 + 1];
    const b = colors[i * 3 + 2];
    for (let v = 0; v < perMark; v += 1) {
      const o = (i * perMark + v) * 3;
      out[o] = r; out[o + 1] = g; out[o + 2] = b;
    }
  }
  return out;
}

/** The outline points without the repeated closing point (as render3d's closedPolygon). */
function closedPolygon(points) {
  const pts = (points || []).filter((p) => Array.isArray(p) && isNumber(p[0]) && isNumber(p[1]));
  if (pts.length > 1) {
    const a = pts[0];
    const b = pts[pts.length - 1];
    if (a[0] === b[0] && a[1] === b[1]) pts.pop();
  }
  return pts;
}

/** Closed-curve outlines by the annotation they belong to (`ClosedCurveDTO.location_id`). */
export function outlineIndex(outlines) {
  const byId = new Map();
  for (const o of outlines || []) byId.set(o.location_id, o);
  return byId;
}

/**
 * The original rings: for each mark the annotation as it was at the tail -- a
 * RING_SEGMENTS-gon of its `radius_um` at the tail position, or, for a closed curve
 * with a traced outline, that outline moved by the same amount (`apply_offsets` and
 * `apply_registration` both move an outline rigidly with its annotation).  A mark
 * with neither has no ring.
 *
 * `outlines` is the `/closed-curves` array or an `outlineIndex` Map.  Returns
 * `{ positions, colors, offsets, n }`; `offsets[i]` is mark i's first vertex and
 * `offsets[n]` the total, so a window of marks is a vertex range even though rings
 * differ in size.  `color` is one RGB for all rings.
 */
export function buildRings(field, sel, scales, outlines, color = [0.6, 0.65, 0.72]) {
  const byId = outlines instanceof Map ? outlines : outlineIndex(outlines);
  const n = sel.n;
  const polygons = new Array(n);
  const offsets = new Uint32Array(n + 1);
  let total = 0;
  for (let i = 0; i < n; i += 1) {
    const r = sel.rows[i];
    let poly = null;
    if (field.curve[r]) {
      const o = byId.get(field.id[r]);
      const pts = o ? closedPolygon(o.outline_um) : [];
      if (pts.length >= 3) poly = pts;
    }
    polygons[i] = poly;
    const sides = poly ? poly.length : (field.radius[r] > 0 && !field.curve[r] ? RING_SEGMENTS : 0);
    offsets[i] = total;
    total += sides * 2;
  }
  offsets[n] = total;
  const positions = new Float32Array(total * 3);
  for (let i = 0; i < n; i += 1) {
    const r = sel.rows[i];
    if (offsets[i + 1] === offsets[i]) continue;
    const s = scales[i];
    const z = field.z[r];
    // The tail: the head pulled back along the shift.
    const tailX = field.hx[r] - s * field.dx[r];
    const tailY = field.hy[r] - s * field.dy[r];
    let o = offsets[i] * 3;
    const poly = polygons[i];
    if (poly) {
      // A traced outline sits at the corrected stage, with the annotation; it
      // moves by whatever takes the annotation from there to the tail.
      const ox = tailX - field.cx[r];
      const oy = tailY - field.cy[r];
      for (let k = 0; k < poly.length; k += 1) {
        const a = poly[k];
        const b = poly[(k + 1) % poly.length];
        positions[o] = a[0] + ox; positions[o + 1] = a[1] + oy; positions[o + 2] = z;
        positions[o + 3] = b[0] + ox; positions[o + 4] = b[1] + oy; positions[o + 5] = z;
        o += 6;
      }
    } else {
      const rad = field.radius[r];
      for (let k = 0; k < RING_SEGMENTS; k += 1) {
        positions[o] = tailX + rad * UNIT_COS[k]; positions[o + 1] = tailY + rad * UNIT_SIN[k];
        positions[o + 2] = z;
        positions[o + 3] = tailX + rad * UNIT_COS[k + 1]; positions[o + 4] = tailY + rad * UNIT_SIN[k + 1];
        positions[o + 5] = z;
        o += 6;
      }
    }
  }
  return { positions, colors: uniformColors(total, color), offsets, n };
}

/** `count` vertices of one RGB (the rings' color; rebuilt on its own when the viewport inverts). */
export function uniformColors(count, rgb) {
  const out = new Float32Array(count * 3);
  for (let v = 0; v < count; v += 1) {
    out[v * 3] = rgb[0]; out[v * 3 + 1] = rgb[1]; out[v * 3 + 2] = rgb[2];
  }
  return out;
}

/** Vertex range `{ first, count }` of marks `[first, first + count)` in a buffer with `offsets`. */
export function vertexRange(offsets, markRange) {
  const a = offsets[markRange.first];
  const b = offsets[markRange.first + markRange.count];
  return { first: a, count: b - a };
}

/** The slice plane: a quad over the cell's XY box (+5 %) at `z`, and its outline. */
export function slicePlane(field, z, margin = 0.05) {
  const { min, max } = field.bbox;
  const px = (max[0] - min[0]) * margin;
  const py = (max[1] - min[1]) * margin;
  const x0 = min[0] - px; const x1 = max[0] + px;
  const y0 = min[1] - py; const y1 = max[1] + py;
  return {
    quad: Float32Array.of(x0, y0, z, x1, y0, z, x1, y1, z, x0, y0, z, x1, y1, z, x0, y1, z),
    outline: Float32Array.of(x0, y0, z, x1, y0, z, x1, y0, z, x1, y1, z,
      x1, y1, z, x0, y1, z, x0, y1, z, x0, y0, z),
  };
}

// -- color --------------------------------------------------------------------------------------

const hexRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);

/** The viewport behind the layer: the dark default and the inverted (figure) background. */
export const BACKGROUNDS = Object.freeze({ dark: '#101418', light: '#f6f7f9' });

/**
 * The magnitude ramp, for the dark viewport and the light one.  Not Viridis (its
 * low end is 1.2:1 on the dark background) and not a pale cyan to magenta (within
 * dE 3.5 of the second-marker blue); a green-teal-violet that keeps at least 4:1
 * against its own background and 14 dE76 from the highlights, the error scale, the
 * difference scale and Compare's yellow (pinned by tests/js/test_shifts.mjs, on a
 * 0.01 grid of the ramp: dark 4.82:1 and dE 15.2, light 4.06:1 and dE 21.1).  The
 * light violet is the designers' #5b4ad8 turned toward purple: that one was 14.0
 * from the difference scale's blue.
 */
export const RAMPS = Object.freeze({
  dark: Object.freeze(['#b6f59a', '#2fd6a0', '#a05cff']),
  light: Object.freeze(['#0f8a5f', '#7038c8', '#b3158f']),
});

/** Ring color on each background: neutral, so the arrows' ramp is the only hue. */
export const RING_COLORS = Object.freeze({ dark: '#9aa7b8', light: '#586274' });

/**
 * Saturation and lightness of the direction hues on each background.  The worst
 * hue (blue on the dark viewport, yellow on the light) keeps 4:1 against it.
 */
export const DIRECTION_SATURATION = 0.8;
export const DIRECTION_LIGHTNESS = Object.freeze({ dark: 0.68, light: 0.26 });

/** A ramp's stops parsed once: sRGB 0..1 triples. */
export function parseStops(stops) {
  return stops.map(hexRgb);
}

const RAMP_RGB = Object.freeze({ dark: parseStops(RAMPS.dark), light: parseStops(RAMPS.light) });

/**
 * A sample of an evenly spaced stop list at `t` in [0, 1], interpolated in sRGB
 * (as the contrast numbers were measured).  `stops` are hex strings or the
 * triples `parseStops` gives.
 */
export function rampSample(stops, t) {
  const colors = typeof stops[0] === 'string' ? parseStops(stops) : stops;
  const u = clamp(Number.isFinite(t) ? t : 0, 0, 1) * (colors.length - 1);
  const i = Math.min(colors.length - 2, Math.floor(u));
  const f = u - i;
  return colors[i].map((v, c) => v + (colors[i + 1][c] - v) * f);
}

/** HSL (h in turns, s and l in 0..1) to sRGB 0..1. */
export function hslToRgb(h, s, l) {
  const f = (n) => {
    const k = (n + h * 12) % 12;
    return l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

/** The hue of a direction: the angle of (dx, dy) as a turn, 0 along +X. */
export function directionHue(dx, dy) {
  const turn = Math.atan2(dy, dx) / (2 * Math.PI);
  return turn < 0 ? turn + 1 : turn;
}

/** sRGB 0..1 to linear light, for a renderer whose working space is linear. */
export function toLinearRgb(rgb) {
  return rgb.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
}

/**
 * One RGB per mark.  `style`: `mode` (magnitude, direction or cell), `max` (the
 * length that gets the full color, um), `inverted` (light viewport) and, for
 * `cell`, `cellColor` (RGB 0..1).  Magnitude is linear from 0 to `max`; direction
 * is one hue per angle, so a section shifted as a whole reads as one color.
 */
export function markColors(field, sel, style = {}) {
  const { mode = 'magnitude', max = 1, inverted = false, cellColor = [0.6, 0.6, 0.6] } = style;
  const theme = inverted ? 'light' : 'dark';
  const out = new Float32Array(sel.n * 3);
  const stops = RAMP_RGB[theme];
  for (let i = 0; i < sel.n; i += 1) {
    const r = sel.rows[i];
    let rgb;
    if (mode === 'direction') {
      rgb = hslToRgb(directionHue(field.dx[r], field.dy[r]), DIRECTION_SATURATION, DIRECTION_LIGHTNESS[theme]);
    } else if (mode === 'cell') {
      rgb = cellColor;
    } else {
      rgb = rampSample(stops, max > 0 ? field.mag[r] / max : 1);
    }
    out[i * 3] = rgb[0]; out[i * 3 + 1] = rgb[1]; out[i * 3 + 2] = rgb[2];
  }
  return out;
}

/** The ring color for the viewport behind it, RGB 0..1. */
export function ringColor(inverted) {
  return hexRgb(inverted ? RING_COLORS.light : RING_COLORS.dark);
}

// -- the chart and the facts --------------------------------------------------------------------

/**
 * One dot per moved annotation for the per-section chart: its section and its true
 * length, plus the extents the axes need.  No threshold: the chart shows where the
 * moves are, and the window is shaded over it.
 */
export function dotData(field) {
  return {
    n: field.n,
    section: field.section,
    mag: field.mag,
    maxMag: field.stats.max,
    sectionMin: field.sections.min,
    sectionMax: field.sections.max,
  };
}

const n0 = (v) => Math.round(v).toLocaleString('en-US');
const um2 = (v) => v.toFixed(2);

/**
 * "429 of 2,183 moved shown · median 0.11 · 95% 0.56 · max 3.99 µm", in the
 * Registration lines' style.  `shown` is the number drawn now (the slice's
 * window, if there is one); when the cap cut the marks it says "strongest 2,000
 * of 5,321" (those at or above the threshold).  The statistics are of every
 * moved annotation, whatever is shown.
 */
export function factsLine(field, sel, shown = sel.n) {
  const s = field.stats;
  if (!field.n) return 'no annotation moved';
  const head = sel.truncated && shown === sel.n
    ? `strongest ${n0(shown)} of ${n0(sel.total)} moved shown`
    : `${n0(shown)} of ${n0(field.n)} moved shown`;
  return `${head} · median ${um2(s.median)} · 95% ${um2(s.p95)} · max ${um2(s.max)} µm`;
}

/** The exaggeration as the badge writes it: "×14", "×2.5"; "" at ×1. */
export function badgeText(k) {
  if (!(k > 1.0005)) return '';
  return `×${k >= 10 ? Math.round(k) : (Math.round(k * 10) / 10)}`;
}

/** The viewport chip's text: "Shifts · ×14 · section 939 (84.51 µm) ±5 · 31 shown". */
export function chipText({ k, slice, section, z, half, shown, truncated = false }) {
  const parts = ['Shifts'];
  const badge = badgeText(k);
  if (badge) parts.push(badge);
  if (slice) parts.push(`section ${section} (${um2(z)} µm) ±${half}`);
  else parts.push('all sections');
  parts.push(truncated ? `strongest ${n0(shown)}` : `${n0(shown)} shown`);
  return parts.join(' · ');
}

/** The cell panel's header summary: "off", or "Correction · all sections · ≥0.25 µm · ×14". */
export function summaryText(display, k = 1, pair = null) {
  const d = normalizeShiftDisplay(display);
  if (!d.showShift) return 'off';
  const parts = [PAIR_SHORT[pair || d.shiftPair]];
  parts.push(d.shiftSlice ? `section ±${d.shiftSliceSpan}` : 'all sections');
  parts.push(`≥${thresholdFor(d)} µm`);
  const badge = badgeText(k);
  if (badge) parts.push(badge);
  return parts.join(' · ');
}

/** "links across sections: median step 0.237 to 0.182 µm · 65% shrank, 29% grew · over 1.5 µm 116 to 71". */
export function verdictLine(summary) {
  const links = summary?.links;
  const verdict = links?.verdict;
  if (!links || !verdict || !verdict.n_links) return '';
  const before = links.stages?.[verdict.before];
  const after = links.stages?.[verdict.after];
  if (!before || !after) return '';
  const pct = (v) => `${Math.round(v * 100)}%`;
  return `links across sections: median step ${before.median_um.toFixed(3)} to ${after.median_um.toFixed(3)} µm`
    + ` · ${pct(verdict.share_shrank)} shrank, ${pct(verdict.share_grew)} grew`
    + ` · over 1.5 µm ${before.n_over_1p5_um} to ${after.n_over_1p5_um}`;
}

/** A coherent-section chip: "939 · 6 · (-0.62, +0.65) µm". */
export function coherentChipText(c) {
  const sign = (v) => `${v < 0 ? '-' : '+'}${Math.abs(v).toFixed(2)}`;
  return `${c.section} · ${c.n} · (${sign(c.mean_dx_um)}, ${sign(c.mean_dy_um)}) µm`;
}

/**
 * Help texts, one per control (data-help, 2 s, beside the panel).  Nothing about
 * the layer stands in the panel as prose: the explanations are here, the
 * results are in the hover of a small chip (shiftpanel.js), and a figure
 * (`SHIFT_FIGURES`, drawn by helpfigures.js) sits above the words where a
 * picture says it faster.
 */
export const SHIFT_HELP = Object.freeze({
  master: 'Draws how far each annotation of this cell was moved, as an arrow from where it was to where it is '
    + 'drawn now, in its own section plane. Nothing else changes: the surface, the rings and the cached '
    + 'meshes stay as they are, and once the annotations are loaded no request is made.\n\n'
    + 'The legend in the corner of the render says what the colors and the exaggeration are; the chip beside '
    + 'this switch says how many arrows are drawn, and its hover has the numbers.',
  pair: 'Which two positions each arrow joins. An annotation passes through three on its way to the screen:\n'
    + '  stored: where Viking\'s database keeps it;\n'
    + '  registered: where Viking draws it now, through the volume\'s current section transforms;\n'
    + '  corrected: where the section correction puts it, which is where it is drawn here.\n\n'
    + 'Section correction joins registered to corrected: the before and after of Viking\'s corrections '
    + 'service. Current transforms joins stored to registered: the section re-alignments made since the '
    + 'annotation was saved. Both joins stored to corrected. A grayed option is a move this cell did not '
    + 'make; its hover says why. A volume whose transforms are not read here offers the correction alone.',
  arrows: 'One arrow per annotation that moved. The head is the annotation where the later of the two '
    + 'positions puts it: on the ring you see, for Section correction and Both; for Current transforms it '
    + 'is where Viking draws the annotation, which differs from the ring by the section correction when '
    + 'that is on. The tail is where it was, pulled back by Exaggerate. The shaft fades toward the tail, so '
    + 'which way an arrow points reads at a glance.',
  rings: 'The annotation where it was: a ring of its own radius at the arrow\'s tail (a closed curve\'s '
    + 'traced outline, moved the same way), beside the real ring and joined to it by the arrow. At ×1 it '
    + 'is the true earlier position; exaggerated, it goes back with the tail. Off, no ring is drawn, the '
    + 'selected annotation\'s included (Arrows does the same for the arrows).',
  scale: 'Draws each tail (and each original ring) this many times farther from the head than the true '
    + 'move, so a tenth of a micrometer shows beside a 10 µm soma. The head never moves.\n\n'
    + 'Auto is 2.5 % of the cell\'s bounding box over the 95th percentile of its moves, between ×1 and '
    + '×100. Only a move up to Full color at is exaggerated: past it the arrow grows at true scale, so an '
    + 'outlier is the arrow of Full color at plus its true excess, not this many times its length. An '
    + 'arrow is never drawn longer than a tenth of the box, unless its true move is. '
    + 'Every number shown (the legend, the chip, the counts) is the true move. Fit goes back to Auto.',
  min: 'Hides moves shorter than this, in true micrometers, so the few that matter are not lost among '
    + 'thousands of tiny ones. Every section and One slice keep a value each (0.25 and 0.05 µm to start). '
    + 'With every section shown, at most the 2,000 longest are drawn.',
  slice: 'One rule for the whole cell: the slice (a section, with a faint plane at it, and Slice ± sections '
    + 'either side) is drawn at full strength, and everything else of the cell -- the other '
    + 'arrows and rings, the surface, the annotation rings, the skeleton -- at Outside the slice of its '
    + 'opacity, in red above the slice and blue below (the red and blue markers\' colors; Flip Z trades '
    + 'them). The arrows of the plane\'s own section are yellow. Looked at down Z (Look down Z), it is the '
    + 'two-dimensional field of moves on that section.\n\n'
    + 'Step it with ↑ and ↓ (while no annotation is selected), [ and ], or PageUp and PageDown; Shift, '
    + 'PageUp and PageDown step ten, Home and End go to the first and last section with a shown arrow. '
    + 'The camera never moves.',
  span: 'How thick the slice is: the plane\'s section and this many sections either side of it are drawn '
    + 'at full strength, the plane\'s own arrows in yellow and the others in red above it and blue below. '
    + '0 is the plane\'s section alone. A section holds only two or three annotations of a typical cell, '
    + 'so a few sections each side make a fuller field.',
  outside: 'How strong everything of this cell outside the slice is drawn, as a share of its own opacity: '
    + 'the arrows and rings of the other sections, and the surface, annotation rings, annotation '
    + 'wireframe, skeleton and synapses above and below it -- in red above the slice and blue below. 0 '
    + 'hides them; 0.05 leaves a trace of the cell around the slice; 1 draws them as strong as the slice.',
  lookDownZ: 'Turn the view to look straight down the section axis (XY1), where the arrows are the '
    + 'two-dimensional vector field of the sections shown.',
  yDown: 'Look down the section axis with X to the right and Y down, the way an image is indexed.',
  color: 'Magnitude: the length of the move, from the ramp\'s first color at 0 to its last at Full color '
    + 'at (and beyond). Direction: the angle of the move in the volume\'s X and Y, one hue per direction, '
    + 'so a section that moved as a whole is one color. Cell color: the cell\'s own. The legend in the '
    + 'corner of the render shows the one in use, with its numbers.',
  colorMax: 'The move that gets the ramp\'s last color; longer ones get it too. Auto is the 95th '
    + 'percentile of the moves, so one outlier does not wash out the rest.',
  through: 'On: the arrows and rings are drawn over the surface, so the ones inside or behind the cell '
    + 'show. Off: the surface hides them where it is in front, like any other object.',
  opacity: 'How opaque the arrows and rings are. An arrow\'s tail is always fainter than its head.',
  focusDim: 'While an annotation is selected, its own arrow is drawn in its marker\'s color -- yellow for the '
    + 'selection, red for the marker above it, blue for the one below -- wider and over everything, with the '
    + 'ring where it was in the same color when Original rings is on. Every other arrow and ring is drawn at '
    + 'this share of its opacity: 0 hides them, 1 leaves them as they are. An annotation this pair did not '
    + 'move has no arrow, and nothing is dimmed for it. One slice at a time has its own emphasis (Outside '
    + 'the slice), so this is not used there.',
  width: 'How wide the arrows are on the screen, in pixels, whatever the zoom. The head grows with it; '
    + 'the original rings are drawn a little thinner.',
  apply: 'Turn on Viking\'s section correction for this cell, its residual field, so there is a shift to show.',
});

/** The drawing above each help text (helpfigures.js), where one helps. */
export const SHIFT_FIGURES = Object.freeze({
  master: 'arrow',
  pair: 'pair',
  arrows: 'arrow',
  rings: 'rings',
  scale: 'scale',
  min: 'threshold',
  slice: 'slice',
  span: 'slice',
  color: 'color',
  colorMax: 'ramp',
  through: 'through',
});

/** The empty-state line when nothing moved. */
export const EMPTY_TEXT = 'No annotation of this cell moved: turn on the section correction (or, where the volume has them, the current transforms) in Registration.';

// -- the slice and its keys ---------------------------------------------------------------------------

/**
 * The slice's center section, one per volume, held outside the cell's display (so
 * it is not in undo, saved sessions or copyCellTo).  Every center is clamped to
 * the section range given with it.
 */
export class SliceState {
  constructor() { this._centers = new Map(); }

  /** The stored center for `volume`, or `undefined`. */
  get(volume) { return this._centers.get(volume); }

  has(volume) { return this._centers.has(volume); }

  clear(volume) { this._centers.delete(volume); }

  /** Put the center on `section` (clamped to `range` = { min, max }); returns it. */
  set(volume, section, range) {
    const v = clamp(Math.round(section), range.min, range.max);
    this._centers.set(volume, v);
    return v;
  }

  /** Move the center by `delta` sections from where it is (or from `fallback`). */
  step(volume, delta, range, fallback = range.min) {
    const from = this._centers.has(volume) ? this._centers.get(volume) : fallback;
    return this.set(volume, from + delta, range);
  }

  /**
   * One key step for every volume in slice mode, by the same number of sections:
   * `ranges` maps volume -> { min, max, fallback? }.  Returns volume -> new center.
   */
  stepAll(delta, ranges) {
    const out = new Map();
    for (const [volume, range] of ranges) out.set(volume, this.step(volume, delta, range, range.fallback ?? range.min));
    return out;
  }
}

/**
 * Where a slice starts: the section of the strongest shown shift (the first place
 * worth looking), else the middle of the cell's sections.
 */
export function defaultCenter(field, sel) {
  let best = -1;
  let at = null;
  for (let i = 0; i < sel.n; i += 1) {
    const m = field.mag[sel.rows[i]];
    if (m > best) { best = m; at = sel.section[i]; }
  }
  return at ?? Math.round((field.sections.min + field.sections.max) / 2);
}

/** First or last section with a shown mark, for Home and End; `null` with none. */
export function edgeSection(sel, which) {
  if (!sel.n) return null;
  return which === 'first' ? sel.sections[0] : sel.sections[sel.sections.length - 1];
}

/**
 * What a key press asks of the slice: `{ kind: 'step', delta }`, `{ kind: 'edge',
 * which }` or `null` (not ours; the renderer's other key handling carries on).
 *
 * [ ] (and { } with Shift), Up and Down, PageUp and PageDown step by 1 or 10 and
 * mean up and down in the view -- so, like stepAlongZ, Up is the higher section and
 * the lower one under Flip Z.  Home and End are by section number.  Up and Down
 * are the slice's only while slice mode is on and no annotation is selected (with
 * one selected they walk along its links); the other keys need only slice mode.
 */
export function sliceKey(event, { sliceMode, selected = false, flipZ = false }) {
  if (!sliceMode || event.altKey || event.ctrlKey || event.metaKey) return null;
  const view = flipZ ? -1 : 1;
  switch (event.key) {
    case '[': return { kind: 'step', delta: -1 * view };
    case '{': return { kind: 'step', delta: -10 * view };
    case ']': return { kind: 'step', delta: 1 * view };
    case '}': return { kind: 'step', delta: 10 * view };
    case 'PageUp': return { kind: 'step', delta: 10 * view };
    case 'PageDown': return { kind: 'step', delta: -10 * view };
    case 'ArrowUp': return selected ? null : { kind: 'step', delta: (event.shiftKey ? 10 : 1) * view };
    case 'ArrowDown': return selected ? null : { kind: 'step', delta: (event.shiftKey ? -10 : -1) * view };
    case 'Home': return { kind: 'edge', which: 'first' };
    case 'End': return { kind: 'edge', which: 'last' };
    default: return null;
  }
}
