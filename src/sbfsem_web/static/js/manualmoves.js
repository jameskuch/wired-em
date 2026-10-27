// Manual moves: a person's own corrections to where an annotation is drawn, after
// Viking's section correction (asked for 2026-10-19: "allow a secondary subsequent
// movement of the annotation").  Pure functions -- no DOM, no three.js -- so node
// tests every rule (tests/js/test_manualmoves.mjs); app.js and movepanel.js act on
// the answers.
//
// A move is X and Y in microns, as Viking's own corrections, and Z in whole
// sections: an annotation is a disc (or an outline) in its section's plane, never
// between two, so a Z move puts it on another section.  Nothing is written to
// Viking.  A cell keeps its moves BY THE REGISTRATION SETTING they were made under
// -- the copy tag the server names it with ("neighbor.1": the correction mode, and
// the current section transforms on), because a move is made from where that
// setting drew the annotation.  The page sends every set with each load of the
// cell, and the server applies the one of the setting it loads
// (sbfsem.core.moves; `SbfsemService.load_neuron`).  Moves made under Curve fit
// ("curvefit.1") or Field + fit ("all.1"), which Viking's service stopped running
// on 2026-10-27, are the residual field's now (`normalizeStore`).
//
//   record.manualMoves = { [tag]: { [locationId]: { dx, dy, dz, t } } }
//
// dx and dy in um, dz in sections, t the last change (ms since 1970).

/** The longest X or Y move, um, and the most sections in Z (sbfsem.core.moves). */
export const MAX_MOVE_UM = 100;
export const MAX_MOVE_SECTIONS = 50;
/** Moves are kept to 0.1 nm, as the server keeps them. */
const ROUND = 1e4;
const round = (v) => Math.round(v * ROUND) / ROUND + 0;

/** The step sizes offered, um; the default is 0.05. */
export const STEPS = Object.freeze([0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1]);
export const STEP_DEFAULT = 0.05;

/**
 * The axes a move can follow, in the order the panel lists them (and the keys 1 to 7
 * choose them): the volume's own X, Y and Z; the viewport's -- Xp to the right of the
 * screen, Yp up it, Zp out of it toward the viewer; and the annotation's own shift
 * (the arrow of the pair the Position shift layer draws).
 */
export const AXES = Object.freeze([
  { key: 'x', label: 'X', group: 'volume', help: 'The volume\'s X, in um: Viking\'s X, scaled.' },
  { key: 'y', label: 'Y', group: 'volume', help: 'The volume\'s Y, in um.' },
  { key: 'z', label: 'Z', group: 'volume', help: 'The volume\'s Z: one section a step, whatever the step size. An annotation sits in its section\'s plane, so a Z move puts it on the next section.' },
  { key: 'xp', label: 'Xp', group: 'view', help: 'The viewport\'s horizontal: right on the screen, laid flat into the annotation\'s section plane. The annotation stays on its section: when the screen\'s axis points along Z, it cannot move it (choose another).' },
  { key: 'yp', label: 'Yp', group: 'view', help: 'The viewport\'s vertical: up the screen, laid flat into the section plane. Seen from the side, up the screen is Z, which an annotation never follows: choose Xp or Zp there.' },
  { key: 'zp', label: 'Zp', group: 'view', help: 'Out of the screen, toward you, laid flat into the section plane: seen from the side, toward you along the section. Seen from above it points along Z and moves nothing.' },
  { key: 'shift', label: 'Shift', group: 'shift', help: 'Along the annotation\'s own shift: the arrow Viking\'s section correction (or the pair the Position shift layer draws) moved it by. + carries on the way the correction went, − goes back toward where it was.' },
]);
export const AXIS_KEYS = Object.freeze(AXES.map((a) => a.key));

/**
 * A screen axis with less than this much of its length in the section plane (it points
 * within about 14° of Z) moves nothing: laid flat it would be a direction the screen
 * barely shows, and turning it into a section step carried the annotation along its own
 * process (2026-10-20: "they need to move on the plane that they are on").
 */
export const IN_PLANE_MIN = 0.25;

const isNumber = (v) => typeof v === 'number' && Number.isFinite(v);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// -- the store ----------------------------------------------------------------------------

/** The registration setting a copy tag names: "neighbor.1.m<token>" -> "neighbor.1". */
export function baseTag(copyTag) {
  if (typeof copyTag !== 'string' || !copyTag) return null;
  const at = copyTag.indexOf('.m');
  return at >= 0 ? copyTag.slice(0, at) : copyTag;
}

/** A tag in words: "neighbor.1" -> "Residual field correction, current transforms on". */
export function tagWords(tag, modeNames = {}) {
  if (!tag) return 'an unknown setting';
  const [mode, live] = String(tag).split('.');
  const names = {
    off: 'no section correction', neighbor: 'Residual field correction',
    curvefit: 'Curve fit correction (retired)', all: 'Field + fit correction (retired)', ...modeNames,
  };
  return `${names[mode] ?? `${mode} correction`}, current transforms ${live === '1' ? 'on' : 'off'}`;
}

/** One move made safe, or null when it moves nothing (or is not a move at all). */
function cleanMove(move) {
  if (move === null || typeof move !== 'object') return null;
  const dx = isNumber(move.dx) ? clamp(round(move.dx), -MAX_MOVE_UM, MAX_MOVE_UM) : 0;
  const dy = isNumber(move.dy) ? clamp(round(move.dy), -MAX_MOVE_UM, MAX_MOVE_UM) : 0;
  const dz = isNumber(move.dz) ? clamp(Math.round(move.dz), -MAX_MOVE_SECTIONS, MAX_MOVE_SECTIONS) : 0;
  if (!dx && !dy && !dz) return null;
  return { dx: dx + 0, dy: dy + 0, dz: dz + 0, t: isNumber(move.t) ? move.t : 0 };
}

/** The correction modes Viking's service ran until 2026-10-27, and the one each is read as now. */
const RETIRED_MODES = Object.freeze({ curvefit: 'neighbor', all: 'neighbor' });

/** A setting's tag as the server names it now: "curvefit.1" and "all.1" are "neighbor.1". */
export function currentTag(tag) {
  const [mode, live] = String(tag).split('.');
  return RETIRED_MODES[mode] ? `${RETIRED_MODES[mode]}.${live}` : tag;
}

/**
 * A saved store made safe: a session or localStorage can hold anything (the
 * 2026-08-31 incident).  Unknown shapes are dropped, numbers clamped, and moves of
 * nothing left out; `{}` when there is nothing.  The sets of a retired correction
 * (`currentTag`) join the residual field's, an annotation moved under more than one
 * keeping its latest move (on a tie, the one the residual field's own set holds).
 */
export function normalizeStore(saved) {
  const out = {};
  if (saved === null || typeof saved !== 'object' || Array.isArray(saved)) return out;
  for (const [tag, set] of Object.entries(saved)) {
    if (!/^[a-z]+\.[01]$/.test(tag) || set === null || typeof set !== 'object') continue;
    const into = currentTag(tag);
    const clean = out[into] ?? {};
    for (const [id, move] of Object.entries(set)) {
      if (!/^\d+$/.test(id)) continue;
      const m = cleanMove(move);
      const had = clean[id];
      if (m && (!had || m.t > had.t || (m.t === had.t && into === tag))) clean[id] = m;
    }
    if (Object.keys(clean).length) out[into] = clean;
  }
  return out;
}

/** The moves of `tag`, `{ [id]: move }` (never null). */
export function movesOf(store, tag) {
  return (tag && store?.[tag]) || {};
}

/** How many moves `tag` holds, and how many the other settings hold. */
export function moveCounts(store, tag) {
  let here = 0;
  let elsewhere = 0;
  const other = [];
  for (const [t, set] of Object.entries(store || {})) {
    const n = Object.keys(set || {}).length;
    if (t === tag) here += n;
    else if (n) { elsewhere += n; other.push([t, n]); }
  }
  return { here, elsewhere, other };
}

/** `store` with `id`'s move under `tag` set to `move` (null or nothing: removed). A new object. */
export function setMove(store, tag, id, move, now = Date.now()) {
  const out = { ...(store || {}) };
  const set = { ...(out[tag] || {}) };
  const clean = cleanMove(move ? { ...move, t: now } : null);
  if (clean) set[String(id)] = clean;
  else delete set[String(id)];
  if (Object.keys(set).length) out[tag] = set;
  else delete out[tag];
  return out;
}

/**
 * `store` with every `[id, move]` of `entries` set under `tag` at once: one step of a
 * move set (a section, a run of sections), in one copy rather than one per annotation.
 */
export function setMoves(store, tag, entries, now = Date.now()) {
  const out = { ...(store || {}) };
  const set = { ...(out[tag] || {}) };
  for (const [id, move] of entries) {
    const clean = cleanMove(move ? { ...move, t: now } : null);
    if (clean) set[String(id)] = clean;
    else delete set[String(id)];
  }
  if (Object.keys(set).length) out[tag] = set;
  else delete out[tag];
  return out;
}

/** `store` without the moves of `ids` under `tag`. */
export function deleteMoves(store, tag, ids) {
  let out = store || {};
  for (const id of ids) out = setMove(out, tag, id, null);
  return out;
}

/** `store` without any move under `tag` (Reset all); the other settings' moves stay. */
export function resetMoves(store, tag) {
  const out = { ...(store || {}) };
  delete out[tag];
  return out;
}

/**
 * What a load sends: every setting's moves as the server reads them,
 * `{ [tag]: [{ id, dx_um, dy_um, dz }] }`, sorted by ID; null when there are none.
 */
export function movesBody(store) {
  const out = {};
  for (const [tag, set] of Object.entries(normalizeStore(store))) {
    out[tag] = Object.keys(set).map(Number).sort((a, b) => a - b)
      .map((id) => ({ id, dx_um: set[id].dx, dy_um: set[id].dy, dz: set[id].dz }));
  }
  return Object.keys(out).length ? out : null;
}

/** One setting's moves as a canonical string: equal strings are the same set. */
export function setSignature(store, tag) {
  const set = movesOf(normalizeStore(store), tag);
  return JSON.stringify(Object.keys(set).map(Number).sort((a, b) => a - b)
    .map((id) => [id, set[id].dx, set[id].dy, set[id].dz]));
}

// -- a step along an axis ---------------------------------------------------------------------

/**
 * The move after one step: `move` ({ dx, dy, dz }, or null for none) moved `sign`
 * (+1 or -1) times `step` um along `axis`.
 *
 * `frame` says what the viewport's axes are in the volume's frame:
 * `{ right, up, out }`, unit 3-vectors (Renderer3D.viewAxes); `shift` is the
 * annotation's own shift vector [dx, dy] (um) for the 'shift' axis.  Returns
 * `{ move, along }` -- `along` says what the step did: 'xy' (in the section plane,
 * `step` um), 'z' (one section: the volume's Z only), or null when it could not (no
 * shift to follow, a view axis that points along Z).
 *
 * A viewport axis is laid flat into the section plane (its X and Y, made a unit
 * vector again), so a step moves `step` um in the plane whatever the view's tilt.  It
 * never leaves the plane: an annotation is a disc or an outline on its own section,
 * and only the volume's Z, chosen as such, puts it on another.  A view axis with
 * less than IN_PLANE_MIN of its length in the plane moves nothing.
 */
export function stepMove(move, axis, sign, step, { frame = null, shift = null } = {}) {
  const now = { dx: move?.dx ?? 0, dy: move?.dy ?? 0, dz: move?.dz ?? 0 };
  const s = sign < 0 ? -1 : 1;
  const xy = (ux, uy) => ({ move: { ...now, dx: round(now.dx + s * step * ux), dy: round(now.dy + s * step * uy) }, along: 'xy' });
  const z = (dir) => ({ move: { ...now, dz: now.dz + s * (dir < 0 ? -1 : 1) }, along: 'z' });
  switch (axis) {
    case 'x': return xy(1, 0);
    case 'y': return xy(0, 1);
    case 'z': return z(1);
    case 'shift': {
      const m = shift ? Math.hypot(shift[0], shift[1]) : 0;
      if (!(m > 0)) return { move: now, along: null };
      return xy(shift[0] / m, shift[1] / m);
    }
    case 'xp': case 'yp': case 'zp': {
      const v = frame?.[{ xp: 'right', yp: 'up', zp: 'out' }[axis]];
      if (!v) return { move: now, along: null };
      const flat = Math.hypot(v[0], v[1]);
      if (flat >= IN_PLANE_MIN) return xy(v[0] / flat, v[1] / flat);
      return { move: now, along: null };
    }
    default: return { move: now, along: null };
  }
}

/**
 * What one step along `axis` does in this view, in words for the panel:
 * "0.05 µm a step along (+1.00, +0.00)", "a section a step (+Z)" (the volume's Z),
 * or why the axis moves nothing here.
 */
export function axisNote(axis, step, { frame = null, shift = null } = {}) {
  const probe = stepMove(null, axis, 1, 1, { frame, shift });
  if (!probe.along) {
    if (axis === 'shift') return 'this annotation has no shift to follow';
    return frame ? 'points along Z in this view: an annotation stays on its section, choose another axis' : '—';
  }
  if (probe.along === 'z') return `a section a step (${probe.move.dz > 0 ? '+' : '−'}Z)`;
  const f = (v) => `${v < 0 ? '−' : '+'}${Math.abs(v).toFixed(2)}`;
  return `${step} µm a step along (${f(probe.move.dx)}, ${f(probe.move.dy)})`;
}

// -- what a step moves ------------------------------------------------------------------------

/**
 * What a step moves (2026-10-20: "multi-select, every annotation on a z-plane, multiple
 * z-planes"): 'one' -- the chosen annotation, and the ones added to it (⌘/Ctrl-click);
 * 'section' -- every annotation of the cell on the chosen one's section; 'range' -- every
 * one on the sections `from` to `to`.  A misregistered section is off as a whole, so it
 * is moved as a whole.
 */
export const SCOPES = Object.freeze(['one', 'section', 'range']);

/** A node's section in Viking: its section less a manual Z move (shifts.js `vikingSection`). */
const sectionOf = (node) => (isNumber(node?.manual_dz) ? node.z_section - node.manual_dz : node?.z_section);

/**
 * The Location IDs a step moves, sorted: by `scope` (SCOPES) around the `target`
 * annotation (its ID), with `from` and `to` the sections of a 'range' (either order),
 * plus `extra` IDs (added by hand) that the cell has.  `nodes` is the cell's `/nodes`.
 */
export function membersOf(nodes, { scope = 'one', target = null, from = null, to = null, extra = [] } = {}) {
  const byId = new Map((nodes || []).map((n) => [n.id, n]));
  const out = new Set();
  if (target != null && byId.has(target)) out.add(target);
  if (scope === 'section' || scope === 'range') {
    let lo = null;
    let hi = null;
    if (scope === 'section') {
      const s = sectionOf(byId.get(target));
      if (isNumber(s)) { lo = s; hi = s; }
    } else if (isNumber(from) && isNumber(to)) {
      lo = Math.min(from, to);
      hi = Math.max(from, to);
    }
    if (lo !== null) {
      for (const node of nodes || []) {
        const s = sectionOf(node);
        if (isNumber(s) && s >= lo && s <= hi) out.add(node.id);
      }
    }
  }
  for (const id of extra || []) if (byId.has(id)) out.add(id);
  return [...out].sort((a, b) => a - b);
}

/**
 * One step for every member at once: each `ids`' move under `tag` carried the same
 * step (`stepMove`), so a set keeps its members' own earlier moves apart and moves
 * together.  Returns `{ store, along, over }`: `over` the IDs a step would take past
 * MAX_MOVE_UM or MAX_MOVE_SECTIONS (the store is then unchanged).
 */
export function stepSet(store, tag, ids, axis, sign, step, options = {}) {
  const set = movesOf(store, tag);
  const entries = [];
  const over = [];
  let along = null;
  for (const id of ids) {
    const r = stepMove(set[String(id)] ?? null, axis, sign, step, options);
    along = r.along;
    if (!r.along) return { store, along: null, over: [] };
    if (Math.abs(r.move.dx) > MAX_MOVE_UM || Math.abs(r.move.dy) > MAX_MOVE_UM
        || Math.abs(r.move.dz) > MAX_MOVE_SECTIONS) over.push(id);
    entries.push([id, r.move]);
  }
  if (over.length) return { store, along, over };
  return { store: setMoves(store, tag, entries), along, over };
}

// -- the skeleton, as the moves not yet applied bend it ----------------------------------------

/**
 * How far each annotation's move not yet in the render takes it from where the render
 * drew it: `Map(id -> [dx, dy, dz])` in um, from movecontrol's `pendingMoves` rows
 * (`{ id, want, applied }`; `want` null for a move taken back) and the section
 * thickness in um.  Empty when every move is in the render.
 */
export function pendingDeltas(pending, thicknessUm) {
  const out = new Map();
  for (const { id, want, applied } of pending || []) {
    const w = want ?? { dx: 0, dy: 0, dz: 0 };
    const a = applied ?? { dx: 0, dy: 0, dz: 0 };
    const d = [w.dx - a.dx, w.dy - a.dy, (w.dz - a.dz) * thicknessUm];
    if (d.some((v) => Math.abs(v) > 1e-9)) out.set(id, d);
  }
  return out;
}

/**
 * The cell's skeleton (`/skeleton`: segments of `location_ids` and `points_um`) with
 * each point of a moved annotation carried by its delta, so the skeleton follows a
 * move as it is made, before Apply builds the surface again (2026-10-20: "when they
 * move, the skeleton changes").  The same object when nothing moves; a new one, the
 * original left as it was, otherwise.
 */
export function nudgedSkeleton(skeleton, deltas) {
  if (!skeleton?.segments || !deltas?.size) return skeleton;
  let touched = false;
  const segments = skeleton.segments.map((segment) => {
    const ids = segment.location_ids || [];
    if (!ids.some((id) => deltas.has(id))) return segment;
    touched = true;
    return {
      ...segment,
      points_um: segment.points_um.map((p, i) => {
        const d = deltas.get(ids[i]);
        return d ? [p[0] + d[0], p[1] + d[1], p[2] + d[2]] : p;
      }),
    };
  });
  return touched ? { ...skeleton, segments } : skeleton;
}

// -- the list ---------------------------------------------------------------------------------

/**
 * The rows of the Manual moves list for one setting: `[{ id, dx, dy, dz, um, t,
 * section, viking }]`, newest first, where `section` is the annotation's section
 * as moved (from `nodes`, the cell's `/nodes`) when it is there.
 */
export function moveRows(store, tag, nodes = []) {
  const set = movesOf(store, tag);
  const byId = new Map((nodes || []).map((n) => [n.id, n]));
  return Object.entries(set).map(([id, m]) => {
    const node = byId.get(Number(id));
    const section = Number.isFinite(node?.z_section) ? node.z_section : null;
    return {
      id: Number(id), dx: m.dx, dy: m.dy, dz: m.dz, t: m.t,
      um: Math.hypot(m.dx, m.dy),
      section,
      // Where Viking has it: the section before any Z move this page sent.
      vikingSection: section !== null ? section - (node?.manual_dz ?? 0) : null,
      found: !!node,
    };
  }).sort((a, b) => b.t - a.t || a.id - b.id);
}

/** A signed number as the list writes it: "+0.25", "−0.10", "0". */
export function signedMove(value, digits = 2) {
  if (!value) return '0';
  return `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(digits)}`;
}
