// The annotation markers and what the readout says about them.  Plain functions,
// so node can run them (tests/js/test_markers.mjs); app.js and render3d.js act on
// the answers.
//
// A triple click (in GraphApp a single click) selects an annotation, drawn yellow:
// the reference.  Up to two
// more annotations of the same volume are marked against it (2026-10-16): one
// ABOVE it, drawn red, and one BELOW it, drawn blue.  "Above" is the top of the
// upright view, as for Hide above and the Up key: higher sections, unless Flip Z
// is on.  So a red marker is always on a section above the yellow and a blue one
// always below, and Hide above / Hide below go by the red and the blue.  A marker
// stepped past the yellow changes color when the other side is free (the one
// moving marker simply carries on), and stops when it is not.

/**
 * How much farther than the nearest ring another ring may be and still be offered by a triple
 * click, in screen pixels.  Rings on neighboring sections lie 2 to 5 px apart at a whole cell's
 * zoom, all inside the 6 px a ring can be picked from, so a click on one listed six
 * (2026-10-17: "the selection is always returning 6 or 7").
 */
export const RING_TIE_PX = 1.5;

/**
 * What a triple click offers from `candidates` (`candidatesAt`'s, nearest first): every
 * annotation ring about as near as the nearest one (within `RING_TIE_PX` of it), and the
 * other kinds (a skeleton branch, a wireframe, a synapse type) as they come.  A ring right
 * under the pointer is then offered alone or with the ring it overlaps, not with its
 * neighbors a few pixels off.
 */
export function choosable(candidates, tiePx = RING_TIE_PX) {
  const rings = candidates.filter((c) => c.kind === 'annotation');
  if (!rings.length) return candidates;
  const nearest = Math.min(...rings.map((c) => c.px));
  return candidates.filter((c) => c.kind !== 'annotation' || c.px <= nearest + tiePx);
}

/**
 * How much farther than the nearest dot another may be and still be offered by a click in
 * GraphApp, in screen pixels: dots this close lie on top of each other on screen, as down a
 * process seen end-on.
 */
export const DOT_TIE_PX = 0.5;

/**
 * What a click offers in GraphApp (2026-10-22): the dot nearest the pointer -- an annotation's
 * (its dot, its ring or a flag on it) or a synapse's -- and any other within `tiePx` of it; a
 * skeleton branch only when no dot is near.  MATLAB's GraphApp named the one node a click
 * landed on (its Data Cursor snapped to the nearest vertex), and every dot lies on its
 * skeleton.  The triple click's rule (`choosable`: the rings within RING_TIE_PX of the
 * nearest, and the branch beside them) offered four annotations and a chooser for a click
 * half a pixel off a dot of c21768 at the whole cell's zoom, where neighbors lie 2 px apart.
 */
export function dotChoices(candidates, tiePx = DOT_TIE_PX) {
  const dots = candidates.filter((c) => c.kind === 'annotation' || c.kind === 'synapse');
  if (!dots.length) return candidates;
  const nearest = Math.min(...dots.map((c) => c.px));
  return dots.filter((c) => c.px <= nearest + tiePx);
}

/** The three slots, in the order the readout lists them: top to bottom, as drawn. */
export const SLOTS = Object.freeze(['above', 'primary', 'below']);
/** What each slot is called in words. */
export const SLOT_NAMES = Object.freeze({ above: 'red', primary: 'yellow', below: 'blue' });

/** The sign Viking's ΔZ is written with: a plus, a true minus, or plus-or-minus for none. */
const sign = (value) => (value > 0 ? '+' : value < 0 ? '−' : '±');
/** A signed number as the readout writes it: "+3", "−0.21", "±0". */
export const signed = (value, digits = 0) => `${sign(value)}${Math.abs(value).toFixed(digits)}`;

/** Two candidates are the same annotation: one cell (key) and one Location ID. */
export function sameAnnotation(a, b) {
  return !!a && !!b && a.locationId != null && a.key === b.key && a.locationId === b.locationId;
}

/**
 * Which side of the yellow `anchor` a candidate `c` is on in the upright view:
 * 'above', 'below', or null when it is on the yellow's own section.  Sections
 * decide when both have one (two annotations on one section are level whatever
 * their microns say); otherwise their Z in microns does.
 */
export function sideOf(anchor, c, flipZ = false) {
  let d = NaN;
  if (Number.isFinite(anchor?.section) && Number.isFinite(c?.section)) d = c.section - anchor.section;
  else if (Number.isFinite(anchor?.zUm) && Number.isFinite(c?.zUm)) {
    d = Math.abs(c.zUm - anchor.zUm) < 1e-6 ? 0 : c.zUm - anchor.zUm;
  }
  if (!Number.isFinite(d) || d === 0) return null;
  return (d > 0) !== !!flipZ ? 'above' : 'below';
}

const other = (slot) => (slot === 'above' ? 'below' : 'above');

/**
 * Where a chosen annotation `c` goes while the yellow `anchor` is selected and
 * `markers` is `{ above, below }` (either may be null).  The caller has already
 * checked that `c` and the yellow are annotations of one volume.
 *
 *   { action: 'same' }               it is the yellow
 *   { action: 'activate', slot }     it is already the red or the blue one
 *   { action: 'set', slot }          it becomes the red (above) or the blue (below),
 *                                    in place of the one there
 *   { action: 'anchor' }             it is on the yellow's own section: it becomes
 *                                    the yellow, and the markers stay (still above
 *                                    and below it)
 */
export function placeChoice(anchor, markers, c, flipZ = false) {
  if (sameAnnotation(anchor, c)) return { action: 'same' };
  for (const slot of ['above', 'below']) {
    if (sameAnnotation(markers?.[slot], c)) return { action: 'activate', slot };
  }
  const side = sideOf(anchor, c, flipZ);
  return side ? { action: 'set', slot: side } : { action: 'anchor' };
}

/**
 * Stepping the marker in `slot` to the annotation `next` (the next one along the
 * cell's links, up or down).
 *
 *   { action: 'merge' }                  `next` is the yellow: the marker goes
 *   { action: 'move', slot }             it stays on its side and keeps its color
 *   { action: 'switch', from, to }       it crossed the yellow with the other side
 *                                        free: it carries on in the other color
 *   { action: 'skip' }                   `next` is on the yellow's section but is not
 *                                        the yellow: step on past it
 *   { action: 'refuse', reason }         it would cross the yellow onto the other
 *                                        marker's side
 */
export function stepMarker(anchor, markers, slot, next, flipZ = false) {
  if (sameAnnotation(anchor, next)) return { action: 'merge' };
  const side = sideOf(anchor, next, flipZ);
  if (!side) return { action: 'skip' };
  if (side === slot) return { action: 'move', slot };
  if (markers?.[side] && !sameAnnotation(markers[side], next)) {
    return {
      action: 'refuse',
      reason: `The ${SLOT_NAMES[slot]} marker stays ${slot} the yellow while there is a `
        + `${SLOT_NAMES[side]} one: step the ${SLOT_NAMES[side]} one, or remove it`,
    };
  }
  return { action: 'switch', from: slot, to: side };
}

/**
 * Moving the yellow itself to `next` (Shift with the arrows, or its row's
 * buttons): the markers as they are then, or a refusal.
 *
 *   { action: 'move', markers: { above, below }, merged }   `merged` names the slot
 *       whose marker `next` was (it is the yellow now); a marker the move put on
 *       the yellow's other side has changed color
 *   { action: 'refuse', reason }   a marker would be level with the yellow, or
 *       both would be on one side of it
 */
export function stepAnchor(anchor, markers, next, flipZ = false) {
  const out = { above: null, below: null };
  let merged = null;
  for (const slot of ['above', 'below']) {
    const m = markers?.[slot];
    if (!m) continue;
    if (sameAnnotation(m, next)) { merged = slot; continue; }
    const side = sideOf(next, m, flipZ);
    if (!side) {
      return {
        action: 'refuse',
        reason: `The yellow would be level with the ${SLOT_NAMES[slot]} marker: step that one first`,
      };
    }
    if (out[side]) {
      return {
        action: 'refuse',
        reason: `The yellow cannot pass the ${SLOT_NAMES[other(side)]} marker while there is a `
          + `${SLOT_NAMES[side]} one: step one of them first`,
      };
    }
    out[side] = m;
  }
  return { action: 'move', markers: out, merged };
}

/**
 * The slot the arrow keys move next: the one moved or chosen last if it is still set, else
 * whichever is.  `preferred` 'primary' (the yellow was chosen or clicked last) is null: the
 * arrows then start a marker from the yellow, red with Up and blue with Down, in place of the
 * one on that side (2026-10-17: with a red marker stepped up, choosing the yellow again and
 * pressing Down moved the red one back instead of adding the blue).
 */
export function activeSlot(markers, preferred) {
  if (preferred === 'primary') return null;
  if (preferred && markers?.[preferred]) return preferred;
  if (markers?.above) return 'above';
  if (markers?.below) return 'below';
  return null;
}

/**
 * The levels Hide above and Hide below take: the red marker, or the yellow
 * without one, for "above"; the blue, or the yellow, for "below".  Null without
 * a yellow annotation.  `both` is true when the two are different annotations
 * (a slab between them can be kept).
 */
export function hideLevels(anchor, markers) {
  if (!anchor) return null;
  const upper = markers?.above || anchor;
  const lower = markers?.below || anchor;
  return { upper, lower, both: upper !== lower };
}

/**
 * The numbers of a marker's row against the yellow: `dz` sections and `dzUm` in
 * Z, and `distUm`, the straight-line distance between the two annotations in the
 * volume's microns.  Null for the yellow itself or without positions.
 * `point` is each candidate's [x, y, z] in microns, in the volume's frame.
 */
export function markerDelta(anchor, m) {
  if (!anchor || !m || anchor === m) return null;
  if (!Number.isFinite(anchor.zUm) || !Number.isFinite(m.zUm)) return null;
  const dzUm = m.zUm - anchor.zUm;
  const dz = Number.isFinite(anchor.section) && Number.isFinite(m.section) ? m.section - anchor.section : null;
  const p = anchor.point;
  const q = m.point;
  const distUm = Array.isArray(p) && Array.isArray(q) && p.length === 3 && q.length === 3
    ? Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) : null;
  return { dz, dzUm, distUm: Number.isFinite(distUm) ? distUm : null };
}

/**
 * A marker's readout against the yellow, or null without one.
 *
 * `first` is the yellow annotation and `second` a marker (both are the
 * candidates `candidatesAt` makes: `{ section, zUm }`).  The Z between them is
 * the second minus the first, as Viking counts it.  `firstZ` and `secondZ` are
 * the sections the two rings sit in ("Z 1231 → 1234"), null when a marker has no
 * section number, and then only the Z between is written.
 */
export function markerReadout(first, second) {
  if (!first || !second || !Number.isFinite(first.zUm) || !Number.isFinite(second.zUm)) return null;
  const um = second.zUm - first.zUm;
  const known = Number.isFinite(first.section) && Number.isFinite(second.section);
  const sections = known ? second.section - first.section : null;
  const delta = `ΔZ ${sign(um)}${Math.abs(um).toFixed(2)} µm`
    + (known ? ` · ${sign(sections)}${Math.abs(sections)} sec` : '');
  return {
    um,
    sections,
    firstZ: known ? `Z ${first.section}` : null,
    secondZ: known ? String(second.section) : null,
    delta,
  };
}

/** The readout as one line of plain text (the status line quotes it). */
export function markerReadoutText(readout) {
  if (!readout) return '';
  return readout.firstZ ? `${readout.firstZ} → ${readout.secondZ} · ${readout.delta}` : readout.delta;
}

/**
 * The rows of the marker table, top to bottom (red, yellow, blue), for the slots
 * that hold something: `{ slot, item, delta }`, `delta` from `markerDelta`
 * (null on the yellow's row).
 */
export function markerRows(anchor, markers) {
  if (!anchor) return [];
  const rows = [];
  for (const slot of SLOTS) {
    const item = slot === 'primary' ? anchor : markers?.[slot];
    if (!item) continue;
    rows.push({ slot, item, delta: slot === 'primary' ? null : markerDelta(anchor, item) });
  }
  return rows;
}

// -- a whole z-slice (2026-10-20) ------------------------------------------------------
//
// From the right-click menu the yellow, red or blue annotation takes every annotation on
// its section with it: of its own cell, or of every loaded cell of its volume.  The others
// are drawn in its color, and it stays the one the readout, the table and Hide above /
// below go by.  The arrow keys then step such a slot a section at a time, to the next
// section with annotations in its scope, and not along the links.

/** What each scope of a z-slice is called. */
export const SLICE_SCOPES = Object.freeze({ cell: 'same cell only', volume: 'every loaded cell' });

/** Whether the candidate `c` is one of the z-slice `slice`'s annotations. */
export function inSlice(slice, c) {
  return !!slice && (slice.items || []).some((i) => sameAnnotation(i, c));
}

/**
 * The section nearest `from` strictly beyond it, toward higher section numbers for `sign` +1
 * and lower for -1, among `sections` (any iterable of numbers); null when there is none.
 */
export function nextSection(sections, from, sign) {
  let best = null;
  for (const s of sections) {
    if (!Number.isFinite(s) || (s - from) * sign <= 0) continue;
    if (best === null || Math.abs(s - from) < Math.abs(best - from)) best = s;
  }
  return best;
}

/**
 * Which of the annotations `items` on a z-slice carries the slot's readout after a step from
 * `from`: one of `from`'s own cell when the slice has any, and of those the nearest in XY,
 * so the Z and the distance in the table still read along the process.
 */
export function leadOf(items, from) {
  if (!items?.length) return null;
  const own = items.filter((c) => c.key === from?.key);
  const pool = own.length ? own : items;
  const p = from?.point;
  const xy = (c) => (Array.isArray(p) && Array.isArray(c.point)
    ? Math.hypot(c.point[0] - p[0], c.point[1] - p[1]) : 0);
  return pool.reduce((best, c) => (xy(c) < xy(best) ? c : best));
}

/**
 * Stepping a slot that holds a whole z-slice: `slot` 'primary' (the yellow, Shift with the
 * arrows), 'above' or 'below' (a marker), or null (a new marker from the yellow), and `sign`
 * +1 toward higher sections.  `sections` are the sections with annotations in the slice's
 * scope, and `at` the sections the three are on now (`{ primary, above, below }`, null where
 * unset).  Slices do not pass through one another: a marker stops at the yellow's section,
 * and the yellow at a marker's, and there the two are one.
 *
 *   { action: 'end' }                    no section with annotations that way
 *   { action: 'move', section, slot }    the slice goes to `section`; for a new marker `slot`
 *                                        is the side it starts on (red above, blue below)
 *   { action: 'merge', section, into }   it reached the section of `into`: a marker that
 *                                        reaches the yellow goes, and the yellow that reaches
 *                                        a marker takes that marker's annotation (it goes)
 */
export function sliceStep(sections, at, slot, sign, flipZ = false) {
  const from = at?.[slot || 'primary'];
  if (!Number.isFinite(from)) return { action: 'end' };
  const stops = new Set(sections);
  const meet = new Map();
  const others = slot === 'primary' ? ['above', 'below'] : slot ? ['primary'] : [];
  for (const s of others) {
    if (!Number.isFinite(at[s])) continue;
    stops.add(at[s]);
    meet.set(at[s], s);
  }
  const section = nextSection(stops, from, sign);
  if (section === null) return { action: 'end' };
  if (meet.has(section)) return { action: 'merge', section, into: meet.get(section) };
  return { action: 'move', section, slot: slot || sideOf({ section: from }, { section }, flipZ) };
}
