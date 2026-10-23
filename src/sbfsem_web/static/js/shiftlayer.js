// The Position shift layer between its data (shifts.js) and a renderer: one cell's
// settings in, the buffers a Renderer3D draws out, and the rules for the slice
// (which section is shown, how a key moves it) that the main window and the
// Render compare panes share.  No DOM and no three.js, so node tests it
// (tests/js/test_shiftlayer.mjs) with a stub renderer.
//
// A *layer* is everything the cell shows under one set of settings: the pair that
// is drawn (the one asked for, or the one that can be), the moved annotations as a
// field, the marks that survive the threshold and the cap, the exaggeration, and
// the arrow and ring vertex buffers.  Building one is cheap (a few milliseconds at
// 10,000 annotations; the field itself is cached per `/nodes` array), so a slider
// drag simply builds another.  Stepping a slice builds nothing: the buffers hold
// every candidate mark, and a section window is a draw range over them.

import {
  ARROW_VERTS, EMPTY_TEXT, MOVED_UM, PAIR_LABELS, PAIR_SHORT, SHIFT_PAIRS, badgeText, buildArrows,
  buildRings, capFor, chipText, defaultCenter, drawnLength, edgeSection, effectiveColorMax, effectivePair,
  vikingSection,
  effectiveScale, expandColors, factsLine, markColors, markScales, moveCounts, normalizeShiftDisplay,
  outlineIndex, pairAvailability, pairEnds, pairsFor, prepareField, ringColor, selectMarks, slicePlane,
  sliceKey, thresholdFor, toLinearRgb, uniformColors, vertexRange, windowRange, zOfSection,
} from './shifts.js';

// -- the per-cell caches -----------------------------------------------------------------

/** Per `/nodes` array: the move counts and the field of each pair.  A WeakMap, so
 * a cell's cache goes with its nodes wherever the app forgets them. */
const perNodes = new WeakMap();
const perOutlines = new WeakMap();

function entryFor(nodes) {
  let entry = perNodes.get(nodes);
  if (!entry) {
    entry = { counts: null, pairs: new Map(), extents: null };
    perNodes.set(nodes, entry);
  }
  return entry;
}

/**
 * Each section's annotations in X and Y: Map(section -> [minX, minY, maxX, maxY]),
 * every annotation as drawn (its center out to its radius), by Viking's section
 * (what the slice is centered on).  Cached with the nodes.
 */
export function sectionExtents(nodes) {
  const entry = entryFor(nodes);
  if (entry.extents) return entry.extents;
  const out = new Map();
  for (const node of nodes || []) {
    const p = node.xyz_um;
    const section = vikingSection(node);
    if (!p || !Number.isFinite(p[0]) || !Number.isFinite(p[1]) || !Number.isFinite(section)) continue;
    const r = Number.isFinite(node.radius_um) ? Math.max(0, node.radius_um) : 0;
    const box = out.get(section);
    if (box) {
      box[0] = Math.min(box[0], p[0] - r); box[1] = Math.min(box[1], p[1] - r);
      box[2] = Math.max(box[2], p[0] + r); box[3] = Math.max(box[3], p[1] + r);
    } else {
      out.set(section, [p[0] - r, p[1] - r, p[0] + r, p[1] + r]);
    }
  }
  entry.extents = out;
  return out;
}

/** How many annotations each stage moved (cached with the nodes). */
export function countsFor(nodes) {
  const entry = entryFor(nodes);
  if (!entry.counts) entry.counts = moveCounts(nodes);
  return entry.counts;
}

/** The field of `pair` over `nodes` (cached with the nodes). */
export function fieldFor(nodes, pair) {
  const entry = entryFor(nodes);
  let field = entry.pairs.get(pair);
  if (!field) {
    field = prepareField(nodes, pair);
    entry.pairs.set(pair, field);
  }
  return field;
}

function indexFor(outlines) {
  if (!outlines || !outlines.length) return new Map();
  let index = perOutlines.get(outlines);
  if (!index) {
    index = outlineIndex(outlines);
    perOutlines.set(outlines, index);
  }
  return index;
}

// -- colors for the renderer -----------------------------------------------------------------

/** '#rrggbb' as three 0-1 numbers; mid gray for anything else. */
export function hexRgb(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || ''));
  return m ? [1, 2, 3].map((i) => parseInt(m[i], 16) / 255) : [0.6, 0.6, 0.6];
}

/**
 * One sRGB triple per mark -> the same in linear light.  three.js treats a vertex
 * color as linear and encodes it for the screen, so the sRGB values the ramps were
 * measured in (contrast, dE) must be linearized first or they show too light.
 */
export function linearize(colors) {
  const out = new Float32Array(colors.length);
  for (let i = 0; i < colors.length; i += 1) {
    const c = colors[i];
    out[i] = c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }
  return out;
}

/** The slice plane's color: white over the dark viewport, near-black over the light one. */
export const planeColor = (inverted) => (inverted ? '#16191d' : '#ffffff');

// -- the layer -----------------------------------------------------------------------------------

/**
 * Everything one cell shows under `display`.
 *
 * `nodes`: the `/nodes` array (the same array each time, so the field is cached);
 * `outlines`: `/closed-curves` rows (only read for the original rings); `summary`:
 * the cell's summary (which stages applied); `inverted`: the light viewport;
 * `cellColor`: the cell's `#rrggbb`, for Cell color; `hasTransforms`: the
 * volume's current transforms are read here (`/api/volumes`), else the section
 * correction is the only pair, whatever the display asks for.
 *
 * Returns `{ empty: true, ... }` when no pair can be shown, else the layer:
 * `field`, `sel`, `k`, `colorMax`, `pair` (the one drawn) and `wanted`, `arrows`
 * and `rings` (the buffers, linear colors, or null when that part is off),
 * `forRenderer` (what `Renderer3D.setShifts` takes) and the helpers below.
 */
export function buildLayer({
  nodes, outlines = [], summary = null, display = {}, inverted = false, cellColor = null,
  hasTransforms = true,
}) {
  const d = normalizeShiftDisplay(display);
  const availability = pairAvailability(summary, countsFor(nodes), { hasTransforms });
  const pair = effectivePair(d.shiftPair, availability);
  const pairs = pairsFor(hasTransforms);
  const base = {
    display: d, availability, wanted: d.shiftPair, pair, pairs,
    // A pair the volume cannot have at all (a saved state from a volume that can) is not a fallback to say.
    fellBack: !!pair && pair !== d.shiftPair && pairs.includes(d.shiftPair),
  };
  if (!pair) return { ...base, empty: true, slice: d.shiftSlice };

  const field = fieldFor(nodes, pair);
  // A pair drawn only because the one asked for cannot be (the correction is off,
  // so the current transforms stand in) gets thresholds that show something, for
  // as long as that is so: the user's own are not changed.
  const used = pair !== d.shiftPair ? { ...d, ...fitThresholds(field, d) } : d;
  base.display = used;
  const k = effectiveScale(used, field);
  const colorMax = effectiveColorMax(used, field);
  const sel = selectMarks(field, { min: thresholdFor(used), cap: capFor(used) });
  // Exaggerated up to Full color at, true scale past it (an outlier is not blown up k times).
  const scales = markScales(field, sel, k, colorMax);
  const cell = hexRgb(cellColor);
  const colorsFor = (dark) => linearize(markColors(field, sel, {
    mode: d.shiftColor, max: colorMax, inverted: dark, cellColor: cell,
  }));
  const ringLinear = (dark) => toLinearRgb(ringColor(dark));

  const arrows = d.shiftArrows ? buildArrows(field, sel, scales, colorsFor(inverted)) : null;
  const rings = d.shiftRings
    ? buildRings(field, sel, scales, indexFor(outlines), ringLinear(inverted)) : null;
  const slice = d.shiftSlice;
  // The plane is drawn at z = 0 and moved to its section: one geometry for the whole slice.
  const flat = slice ? slicePlane(field, 0) : null;

  const layer = {
    ...base,
    empty: false,
    // Each section's annotations, for sizing the slice's plane (planeRect).
    extents: slice ? sectionExtents(nodes) : null,
    field,
    sel,
    k,
    colorMax,
    scales,
    arrows,
    rings,
    slice,
    // Marks cut from the field by the threshold and the cap: the slice's window reads these.
    sectionRange: { min: field.sections.min, max: field.sections.max },
    plane: flat ? { ...flat, color: planeColor(inverted) } : null,
  };

  /** The colors made for the other background (the viewport was inverted). */
  layer.recolor = (dark) => ({
    arrows: arrows ? expandColors(colorsFor(dark), sel.n, ARROW_VERTS) : null,
    rings: rings ? uniformColors(rings.offsets[rings.n], ringLinear(dark)) : null,
    plane: planeColor(dark),
  });

  /**
   * The slice at `center`: `marks`, those of the sections `center - span .. center +
   * span`, and the draw ranges -- every mark while Outside the slice draws the rest
   * faint, else the slice alone -- with `pick`, the slice's, for the right-click menu.
   */
  layer.windowAt = (center) => {
    const marks = windowRange(sel, center, d.shiftSliceSpan);
    const drawn = d.shiftOutside > 0 ? { first: 0, count: sel.n } : marks;
    const ranges = (m) => ({
      arrows: arrows ? { first: m.first * ARROW_VERTS, count: m.count * ARROW_VERTS } : null,
      rings: rings ? vertexRange(rings.offsets, m) : null,
    });
    return { marks, shown: marks.count, ranges: { ...ranges(drawn), pick: ranges(marks) } };
  };

  layer.zOf = (section) => zOfSection(field, section);
  layer.planeAt = (center) => planeRect(layer, center, layer.extents);
  layer.defaultCenter = () => defaultCenter(field, sel);
  layer.edge = (which) => edgeSection(sel, which);
  layer.shownFor = (center) => (slice ? layer.windowAt(center).shown : sel.n);
  layer.facts = (center) => factsLine(field, sel, layer.shownFor(center));
  layer.badge = badgeText(k);

  layer.forRenderer = {
    arrows: arrows ? { positions: arrows.positions, colors: arrows.colors } : null,
    rings: rings ? { positions: rings.positions, colors: rings.colors } : null,
    plane: layer.plane,
    recolor: layer.recolor,
    meta: layer,
  };
  return layer;
}

/** Why the drawn pair is not the one asked for, or '' when it is. */
export function fallbackNote(layer) {
  if (!layer || layer.empty || !layer.fellBack) return '';
  const why = layer.availability[layer.wanted]?.reason || 'it moved nothing';
  return `Showing ${PAIR_LABELS[layer.pair]}: ${why}`;
}

/**
 * The small chip beside the layer's switch (and in Render compare's strip):
 * `{ text, warn, help }`.  `text` is a word or a count ("loading", "nothing
 * moved", "429 shown", "strongest 2,000", with the pair drawn when it is not the
 * one asked for); `help` is everything the panel used to print under its
 * controls: the facts of the arrows shown, and `verdict` (the server's line on
 * the links across sections) when it has come.  `layer` null is loading.
 */
export function shiftChip(layer, center = null, verdict = '') {
  if (!layer) return { text: 'loading', warn: false, help: 'Loading the cell\'s annotations.' };
  if (layer.empty) {
    const reasons = SHIFT_PAIRS.filter((p) => layer.pairs?.includes(p) ?? true)
      .map((p) => layer.availability?.[p]?.reason).filter(Boolean);
    return {
      text: 'nothing moved', warn: true, help: [EMPTY_TEXT, ...new Set(reasons)].join('\n\n'),
    };
  }
  // A slice not placed yet starts where the layer starts it.
  const at = layer.slice && !Number.isFinite(center) ? layer.defaultCenter() : center;
  const shown = layer.shownFor(at);
  const count = Math.round(shown).toLocaleString('en-US');
  let text = layer.sel.truncated && !layer.slice ? `strongest ${count}` : `${count} shown`;
  const lines = [];
  if (layer.fellBack) {
    text += ` · ${PAIR_SHORT[layer.pair]}`;
    lines.push(`${fallbackNote(layer)}.`);
  }
  lines.push(layer.facts(at));
  lines.push('Every number is the true move, never the exaggerated one, counted on this cell\'s own annotations.');
  if (verdict) lines.push(`${verdict[0].toUpperCase()}${verdict.slice(1)}.`);
  return { text, warn: layer.fellBack, help: lines.join('\n\n') };
}

// -- thresholds that fit a pair ---------------------------------------------------------------------------

/** The steps "Only shifts above" is lowered through, in true um. */
const THRESHOLD_LADDER = [0.25, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001, 0];

/**
 * The thresholds to set when the pair changes.  The section correction moves most
 * annotations by tenths of a micrometer, but the current transforms by hundredths
 * (c1178: at most 0.21 um), so the default 0.25 um would hide every one of them.
 * When the threshold in force (the All sections one and the slice's) would leave
 * fewer than 5 marks of `field` (or fewer than all of them, if there are fewer),
 * it is lowered to the largest step on the ladder that shows 10 (or all): only the
 * keys that change are returned, `{ shiftMin, shiftMinSlice }`.
 */
export function fitThresholds(field, display) {
  const d = normalizeShiftDisplay(display);
  const count = (t) => {
    let n = 0;
    for (let i = 0; i < field.n; i += 1) if (field.mag[i] >= t) n += 1;
    return n;
  };
  const need = Math.min(field.n, 5);
  const want = Math.min(field.n, 10);
  const fit = (current) => {
    if (!field.n || count(current) >= need) return current;
    for (const step of THRESHOLD_LADDER) if (step < current && count(step) >= want) return step;
    return 0;
  };
  const out = {};
  const all = fit(d.shiftMin);
  const slice = fit(d.shiftMinSlice);
  if (all !== d.shiftMin) out.shiftMin = all;
  if (slice !== d.shiftMinSlice) out.shiftMinSlice = slice;
  return out;
}

/**
 * Fits the thresholds to each pair and gives the user's back when the pair
 * returns: lowering 0.25 to 0.02 for the current transforms must not leave the
 * section correction (2,000 marks, capped) at 0.02.  Per key (a cell, or Compare's
 * strip); transient, like everything about the slice.  A threshold the user has
 * moved since is theirs, and is the new starting point; the other one still goes back.
 */
export class ThresholdMemory {
  constructor() { this._memory = new Map(); }

  /** The display changes for showing `field` (the pair about to be drawn) at `key`. */
  changes(key, field, display) {
    const d = normalizeShiftDisplay(display);
    const mem = this._memory.get(key);
    // Each threshold on its own: the one the person moved since is theirs and the new start,
    // the other still goes back to what it was.
    const back = (name) => (mem && d[name] === mem.to[name] ? mem.from[name] : d[name]);
    const base = { shiftMin: back('shiftMin'), shiftMinSlice: back('shiftMinSlice') };
    const fit = fitThresholds(field, { ...d, ...base });
    const target = {
      shiftMin: fit.shiftMin ?? base.shiftMin,
      shiftMinSlice: fit.shiftMinSlice ?? base.shiftMinSlice,
    };
    if (target.shiftMin === base.shiftMin && target.shiftMinSlice === base.shiftMinSlice) {
      this._memory.delete(key);
    } else {
      this._memory.set(key, { from: base, to: target });
    }
    const out = {};
    if (target.shiftMin !== d.shiftMin) out.shiftMin = target.shiftMin;
    if (target.shiftMinSlice !== d.shiftMinSlice) out.shiftMinSlice = target.shiftMinSlice;
    return out;
  }
}

/** The same for a cell's nodes: the thresholds `memory` gives for showing `pair` there. */
export function thresholdsForPair(memory, key, nodes, pair, display) {
  return memory.changes(key, fieldFor(nodes, pair), display);
}

// -- the chosen annotations' own arrows ------------------------------------------------------------

/** Per field: Location ID -> row, built the first time an annotation of it is chosen. */
const rowsById = new WeakMap();

/** The row of `field` that holds Location `id`, or -1 when the pair did not move it. */
export function rowOfId(field, id) {
  let index = rowsById.get(field);
  if (!index) {
    index = new Map();
    for (let r = 0; r < field.n; r += 1) index.set(field.id[r], r);
    rowsById.set(field, index);
  }
  return index.get(Number(id)) ?? -1;
}

/**
 * The arrows of chosen annotations (the yellow, red and blue markers), to draw over
 * the layer in their markers' colors (2026-10-19: "I don't know which arrow is the
 * movement of this annotation").  For each Location ID of `ids` that the layer's
 * pair moved: `{ locationId, trueUm, shift, scale, arrow, ring, curve }` -- `arrow`
 * its arrow's line positions (ARROW_VERTS vertices, as `buildArrows`) and `ring`
 * the annotation where it was (as `buildRings`; null for a closed curve whose
 * outline is not in `outlines`), both at the exaggeration the layer draws that
 * arrow with.  Whatever the threshold, the cap and the slice's window: a chosen
 * annotation's arrow is drawn even where the layer leaves it out.  An ID the pair
 * did not move is left out.
 */
export function focusMarks(layer, ids, outlines = []) {
  if (!layer || layer.empty || !layer.field) return [];
  const { field } = layer;
  const byId = outlines instanceof Map ? outlines : outlineIndex(outlines);
  const out = [];
  for (const id of ids) {
    const r = rowOfId(field, id);
    if (r < 0) continue;
    const scale = drawnLength(field.mag[r], layer.k, field.bbox.diagonal, layer.colorMax) / field.mag[r];
    const one = { rows: Int32Array.of(r), n: 1 };
    const scales = Float64Array.of(scale);
    const ring = buildRings(field, one, scales, byId);
    out.push({
      locationId: Number(id),
      trueUm: field.mag[r],
      shift: [field.dx[r], field.dy[r]],
      scale,
      arrow: buildArrows(field, one, scales).positions,
      ring: ring.positions.length ? ring.positions : null,
      curve: !!field.curve[r],
    });
  }
  return out;
}

// -- One slice at a time: the plane and the colors ----------------------------------------------------

/** The plane's margin around what it frames: this share of its larger side, and at least PLANE_PAD_UM. */
export const PLANE_MARGIN = 0.05;
export const PLANE_PAD_UM = 0.5;
/** The plane is at least this wide, so one small annotation still has a plane around it. */
export const PLANE_MIN_UM = 3;
/** A plane on a section with nothing on it frames the sections this far either side (or the slice's). */
export const PLANE_REACH = 5;

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
 * The slice plane at section `center`, sized to what is on it (2026-10-20: the
 * plane over the whole cell did not fit the view): `{ x0, y0, x1, y1 }` around the
 * annotations of that section (`extents`, `sectionExtents`) and the arrows drawn on
 * it, tails included, with a margin; around those of the sections PLANE_REACH either
 * side (or the slice's, if thicker) when that section has none; null when they have
 * nothing either.
 */
export function planeRect(layer, center, extents = null) {
  if (!layer || layer.empty || !Number.isFinite(center)) return null;
  let box = null;
  const grow = (x0, y0, x1, y1) => {
    if (!box) box = [x0, y0, x1, y1];
    else { box[0] = Math.min(box[0], x0); box[1] = Math.min(box[1], y0); box[2] = Math.max(box[2], x1); box[3] = Math.max(box[3], y1); }
  };
  const take = (from, to) => {
    for (let s = from; s <= to; s += 1) {
      const e = extents?.get(s);
      if (e) grow(e[0], e[1], e[2], e[3]);
    }
    // The arrows of those sections, their exaggerated tails too.
    const { sel, field, scales } = layer;
    const a = lowerBound(sel.section, from);
    const b = lowerBound(sel.section, to + 1);
    for (let i = a; i < b; i += 1) {
      const r = sel.rows[i];
      const tx = field.hx[r] - scales[i] * field.dx[r];
      const ty = field.hy[r] - scales[i] * field.dy[r];
      grow(Math.min(tx, field.hx[r]), Math.min(ty, field.hy[r]), Math.max(tx, field.hx[r]), Math.max(ty, field.hy[r]));
    }
  };
  take(center, center);
  if (!box) {
    const half = Math.max(layer.display.shiftSliceSpan, PLANE_REACH);
    take(center - half, center + half);
  }
  if (!box) return null;
  const w = box[2] - box[0];
  const h = box[3] - box[1];
  const pad = Math.max(PLANE_MARGIN * Math.max(w, h), PLANE_PAD_UM);
  const cx = (box[0] + box[2]) / 2;
  const cy = (box[1] + box[3]) / 2;
  const hw = Math.max(w / 2 + pad, PLANE_MIN_UM / 2);
  const hh = Math.max(h / 2 + pad, PLANE_MIN_UM / 2);
  return { x0: cx - hw, y0: cy - hh, x1: cx + hw, y1: cy + hh };
}

/** `rgb` part of the way to white: the selection's yellow made the plane's when both are on screen. */
export function towardWhite(rgb, share) {
  return rgb.map((c) => c + (1 - c) * share);
}

/** How far toward white the plane's yellow goes while an annotation is selected (the selection keeps the full yellow). */
export const HYBRID_WHITE = 0.55;

/**
 * The colors of a slice (2026-10-20, one rule since 2026-10-22): the marks of the
 * plane's section in the selection's yellow, those of the sections above it in the
 * red marker's color and those below in the blue one's; at full strength inside the
 * slice (the plane's section and Slice ± sections either side, `shiftSliceSpan`)
 * and at `shiftOutside` of their opacity past it.  "Above" is the upright view's:
 * higher sections, unless `flipZ`.  With an annotation of the cell selected
 * (`hybrid`), the plane's marks are the yellow taken toward white, so the selected
 * one's own arrow (full yellow, wider, edged) stays apart from the rest of its section.
 *
 * `colors`: `{ above, on, below }` linear RGB (the renderer's marker colors).  Returns
 * `{ parts: [{ side, inside, first, count, rgb, alpha }], rings }`: every mark of the
 * layer in five ranges, in draw order (lower sections past the slice, lower ones in
 * it, the plane's, higher ones in it, higher ones past it), and the same as vertex
 * ranges of the rings' buffer when the layer draws rings.  Null outside slice mode.
 */
export function sliceTint(layer, center, { colors, flipZ = false, hybrid = false } = {}) {
  if (!layer || layer.empty || !layer.slice || !Number.isFinite(center) || !colors) return null;
  const { sel } = layer;
  const d = normalizeShiftDisplay(layer.display);
  const span = d.shiftSliceSpan;
  const at = (section) => lowerBound(sel.section, section);
  const [s0, p0, p1, s1] = [at(center - span), at(center), at(center + 1), at(center + span + 1)];
  const lower = flipZ ? 'above' : 'below';
  const higher = flipZ ? 'below' : 'above';
  const on = hybrid ? towardWhite(colors.on, HYBRID_WHITE) : colors.on;
  const part = (side, inside, first, end, rgb) => ({
    side, inside, first, count: end - first, rgb, alpha: inside ? 1 : d.shiftOutside,
  });
  const parts = [
    part(lower, false, 0, s0, colors[lower]),
    part(lower, true, s0, p0, colors[lower]),
    part('on', true, p0, p1, on),
    part(higher, true, p1, s1, colors[higher]),
    part(higher, false, s1, sel.n, colors[higher]),
  ];
  const rings = layer.rings
    ? parts.map((p) => ({ ...p, ...vertexRange(layer.rings.offsets, { first: p.first, count: p.count }) }))
    : null;
  return { parts, rings };
}

/**
 * The rest of the cell under a slice (Renderer3D.setSliceFade): the slice's Z span,
 * from half a section below its lowest section to half a section above its highest
 * (the cell's own microns), what is outside it drawn at `shiftOutside` in the red and
 * blue markers' colors.  `colors` as `sliceTint` takes them.  Null outside slice mode.
 */
export function sliceFade(layer, center, colors) {
  if (!layer || layer.empty || !layer.slice || !Number.isFinite(center) || !colors) return null;
  const d = normalizeShiftDisplay(layer.display);
  const a = layer.zOf(center - d.shiftSliceSpan);
  const b = layer.zOf(center + d.shiftSliceSpan);
  // One section's thickness; a cell on one section has no step to read, so 70 nm.
  const step = Math.abs(layer.field.zMap.dz);
  const half = (step > 0 ? step : 0.07) / 2;
  return {
    lo: Math.min(a, b) - half, hi: Math.max(a, b) + half, outside: d.shiftOutside,
    above: colors.above, below: colors.below,
  };
}

// -- drawing a layer ----------------------------------------------------------------------------------

/**
 * Put a layer on `renderer` under `key`, replacing what was there, and set its
 * slice window to `center`.  An empty layer, or one with nothing to draw, removes
 * the cell's layer.  Returns true when something is drawn.
 */
export function drawLayer(renderer, key, layer, center = null) {
  if (!layer || layer.empty || !(layer.arrows || layer.rings)) {
    renderer.removeShifts(key);
    renderer.setSliceFade?.(key, null);
    return false;
  }
  renderer.setShifts(key, layer.forRenderer, {
    opacity: layer.display.shiftOpacity, through: layer.display.shiftThrough, width: layer.display.shiftWidth,
  });
  syncSlice(renderer, key, layer, center);
  return true;
}

/**
 * Move a drawn layer's slice to `center`: the draw ranges, the plane's place and
 * size, the marks' colors (red above the plane, yellow on it, blue below, faint past
 * the slice; `sliceTint`) and the rest of the cell faded past the slice
 * (`sliceFade`).  `hybrid`: an annotation of the cell is selected.  The colors come
 * from the renderer (`sliceColors`), so its marker colors and the light viewport hold.
 * A layer in slice mode whose center is not known yet keeps the cell's fade: it is
 * placed a moment later (`drawLayer` before the controller's centers).
 */
export function syncSlice(renderer, key, layer, center, { hybrid = false } = {}) {
  if (!layer || layer.empty) return;
  if (layer.slice && Number.isFinite(center)) {
    renderer.setShiftWindow(key, layer.windowAt(center).ranges);
    const colors = renderer.sliceColors?.(key) ?? null;
    renderer.setShiftPlane(key, layer.zOf(center), layer.planeAt?.(center) ?? null, colors?.on ?? null);
    renderer.setShiftSliceTint?.(key, sliceTint(layer, center, { colors, flipZ: !!renderer.flipZ, hybrid }));
    renderer.setSliceFade?.(key, sliceFade(layer, center, colors));
  } else {
    renderer.setShiftWindow(key, null);
    renderer.setShiftPlane(key, null);
    renderer.setShiftSliceTint?.(key, null);
    if (!layer.slice) renderer.setSliceFade?.(key, null);
  }
}

// -- the slice, across cells and volumes ----------------------------------------------------------------------

/**
 * The slice centers' ranges, per volume, from `entries` = `[{ source, layer }]`:
 * the union of the cells' section ranges for the volumes whose cells are in slice
 * mode, and the section a slice starts at (the strongest shift of the first cell).
 */
export function sliceRanges(entries) {
  const out = new Map();
  for (const { source, layer } of entries) {
    if (!layer || layer.empty || !layer.slice) continue;
    const { min, max } = layer.sectionRange;
    const hit = out.get(source);
    if (hit) {
      hit.min = Math.min(hit.min, min);
      hit.max = Math.max(hit.max, max);
    } else {
      out.set(source, { min, max, fallback: layer.defaultCenter() });
    }
  }
  return out;
}

/**
 * The center of each volume's slice, started where it has none: `slices` is a
 * SliceState.  Returns volume -> section for the volumes in slice mode.
 */
export function sliceCenters(entries, slices) {
  const out = new Map();
  for (const [source, range] of sliceRanges(entries)) {
    out.set(source, slices.has(source)
      ? slices.set(source, slices.get(source), range)   // clamp to what the cells now span
      : slices.set(source, range.fallback, range));
  }
  return out;
}

/**
 * What a key press does to the slices of `entries`: the new center of every
 * volume in slice mode, or null when the key is not the slice's (or no volume is
 * in slice mode).  `selected`: an annotation is selected (Up and Down are then the
 * walk's); `flipZ`: the view is mirrored.
 */
export function applySliceKey(event, entries, slices, { selected = false, flipZ = false } = {}) {
  const ranges = sliceRanges(entries);
  const command = sliceKey(event, { sliceMode: ranges.size > 0, selected, flipZ });
  if (!command) return null;
  sliceCenters(entries, slices);   // every volume has a center before it moves
  if (command.kind === 'step') return slices.stepAll(command.delta, ranges);
  const moved = new Map();
  for (const [source, range] of ranges) {
    const sections = entries
      .filter((e) => e.source === source && e.layer && !e.layer.empty && e.layer.slice)
      .map((e) => e.layer.edge(command.which))
      .filter((s) => s !== null);
    if (!sections.length) continue;
    const pick = command.which === 'first' ? Math.min(...sections) : Math.max(...sections);
    moved.set(source, slices.set(source, pick, range));
  }
  return moved;
}

/**
 * The same section on every slice volume that follows (an annotation was
 * selected, a chip or the chart was clicked): `slices.set` for each volume of
 * `entries` named in `sources`, clamped.  Returns volume -> section.
 */
export function setSliceCenters(entries, slices, sources, section) {
  const out = new Map();
  for (const [source, range] of sliceRanges(entries)) {
    if (sources.includes(source)) out.set(source, slices.set(source, section, range));
  }
  return out;
}

/**
 * One cell's layer to the chip's words.  `entries`: `[{ id, source, layer }]`
 * for the cells with a layer on; `centers`: volume -> section.  A single cell is
 * the full phrase ("Shifts · ×14 · section 939 (84.51 µm) ±5 · 31 shown"); several
 * list each with its own exaggeration ("Shifts · c1178 ×14 · c4555 ×5 · all sections · 131 shown").
 */
export function chipSummary(entries, centers, volumeTag = (source) => source) {
  const live = entries.filter((e) => e.layer && !e.layer.empty);
  if (!live.length) return '';
  const shown = (e) => e.layer.shownFor(centers.get(e.source));
  if (live.length === 1) {
    const [e] = live;
    const center = centers.get(e.source);
    return chipText({
      k: e.layer.k,
      slice: e.layer.slice,
      section: center,
      z: e.layer.slice && Number.isFinite(center) ? e.layer.zOf(center) : 0,
      half: e.layer.display.shiftSliceSpan,
      shown: shown(e),
      truncated: e.layer.sel.truncated && !e.layer.slice,
    });
  }
  // An ID is numbered per volume: with cells of more than one volume on screen each carries its own.
  const mixed = new Set(live.map((e) => e.source)).size > 1;
  const parts = ['Shifts'];
  for (const e of live) {
    const badge = badgeText(e.layer.k);
    const name = mixed ? `c${e.id} ${volumeTag(e.source)}` : `c${e.id}`;
    parts.push(badge ? `${name} ${badge}` : name);
  }
  const slicing = live.filter((e) => e.layer.slice);
  if (slicing.length) {
    const seen = new Set();
    for (const e of slicing) {
      if (seen.has(e.source)) continue;
      seen.add(e.source);
      parts.push(`section ${centers.get(e.source)} ±${e.layer.display.shiftSliceSpan}`);
    }
  }
  if (slicing.length < live.length) parts.push('all sections');
  parts.push(`${live.reduce((n, e) => n + shown(e), 0).toLocaleString('en-US')} shown`);
  return parts.join(' · ');
}

// -- the pick readout --------------------------------------------------------------------------------------

/**
 * How far one annotation moved, for the readout beside its Location ID:
 * `{ text: "shift 0.14 µm (+0.12, -0.08)", title, length, shift, pair }`, or null
 * when it did not move.
 * `pair`: the pair the cell's layer draws; without one, the section correction if
 * it moved this annotation, else the current transforms.
 */
export function pickShiftText(node, pair = null) {
  const moved = (p) => {
    const { shift } = pairEnds(node, p);
    return Math.hypot(shift[0], shift[1]) > MOVED_UM ? shift : null;
  };
  const chosen = pair ?? (moved('correction') ? 'correction' : 'transforms');
  const shift = moved(chosen);
  if (!shift) return null;
  const sign = (v) => `${v < 0 ? '-' : '+'}${Math.abs(v).toFixed(2)}`;
  const length = Math.hypot(shift[0], shift[1]);
  const what = {
    correction: "Viking's section correction moved this annotation",
    transforms: "The volume's current section transforms moved this annotation from its stored position",
    both: 'The current transforms and then the section correction moved this annotation from its stored position',
  }[chosen];
  return {
    length,
    shift: [shift[0], shift[1]],
    pair: chosen,
    text: `shift ${length.toFixed(2)} µm (${sign(shift[0])}, ${sign(shift[1])})`,
    title: `${what}, in X and Y, by ${length.toFixed(3)} µm. The µm numbers of the readout are where it `
      + 'is drawn here; the Viking numbers take the section correction back, so they name where '
      + 'Viking draws it now.',
  };
}
