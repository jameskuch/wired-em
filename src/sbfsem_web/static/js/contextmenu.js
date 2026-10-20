// What the render window's right-click menu decides without a page: when a press
// and a release are a click and not a pan, which of the things under the pointer
// the menu lists and in what order, what each is called, and where a menu goes
// so that all of it stays on screen.  Plain functions and one small class, so
// node can run them (tests/js/test_contextmenu.mjs); app.js builds the menu from
// them and render3d.js gathers the candidates.

/** A right button that travels farther than this between going down and coming up is a pan. */
export const CLICK_SLOP_PX = 8;

/**
 * How near the pointer, in pixels, a cell's surface has to pass when it is not
 * under it exactly: a thin process is a pixel or two wide on screen, and a person
 * aims at what they can see.
 */
export const SURFACE_REACH_PX = 4;

/** Most rows the list offers. */
export const MAX_ROWS = 12;

/** Whether `a` and `b` ({ x, y }) are within `slop` pixels of each other. */
export function isClick(a, b, slop = CLICK_SLOP_PX) {
  return Math.hypot(a.x - b.x, a.y - b.y) <= slop;
}

/**
 * Press, release and the `contextmenu` event of one right click, in whichever
 * order the platform sends them.
 *
 * Windows sends `contextmenu` after the button is up; macOS (and some Linux
 * browsers) send it as the button goes DOWN.  A menu opened at that moment sits
 * under the pointer of a pan that has only begun, and the pan stalls (measured
 * with real input in Chrome on macOS: 0.01 µm of pan against 45 µm over empty
 * space, 2026-10-16); and the old test, that the pointer had barely moved since
 * the press, was a test of 0 pixels there, so a right-drag over a cell always
 * opened the menu.  So the menu opens when the button comes up, if the pointer is
 * still where it went down, on either platform.
 *
 * A press counts when it is the right button, or the left with Control held (a
 * Mac's way of a right click).  A touch screen's long press is neither, and
 * opens nothing, as before.
 */
export class ContextGesture {
  constructor(slop = CLICK_SLOP_PX) {
    this.slop = slop;
    this.down = null;      // the press still held
    this.last = null;      // the last press, for a `contextmenu` that arrives after the release
    this.asked = false;    // `contextmenu` came while the button was down
  }

  /** `pointerdown`: `{ x, y, button, ctrl }`. */
  press({ x, y, button, ctrl = false }) {
    this.down = { x, y, button, ctrl: !!ctrl };
    this.last = this.down;
    this.asked = false;
  }

  /** The `contextmenu` event at `{ x, y }`: where to open the menu now, or null. */
  menu({ x, y }) {
    const from = this.down || this.last;
    if (!from || !(from.button === 2 || (from.button === 0 && from.ctrl))) return null;
    // As the button goes down: wait for it to come up.
    if (this.down) { this.asked = true; return null; }
    return isClick(from, { x, y }, this.slop) ? { x, y } : null;
  }

  /** `pointerup` of the press: where to open the menu that was asked for, or null. */
  release({ x, y }) {
    const from = this.down;
    const asked = this.asked;
    this.down = null;
    this.asked = false;
    if (!from || !asked) return null;
    return isClick(from, { x, y }, this.slop) ? { x, y } : null;
  }

  /** `pointercancel`: the press is not going to be released. */
  cancel() {
    this.down = null;
    this.asked = false;
  }
}

// -- what is under the pointer ---------------------------------------------------

/**
 * The kinds that stand for a whole cell: its surface, its wireframe, its
 * annotation wireframe, its position-shift arrows.  A cell is listed once, as
 * whichever of them is nearest the pointer (the surface, on a tie), because
 * choosing between them changes nothing the menu does.
 */
export const CELL_KINDS = new Set(['surface', 'wireframe', 'annowire', 'shifts']);

/** The kinds a row can select (and light up): the ones the render's selection knows. */
export const SELECTABLE_KINDS = new Set(['annotation', 'skeleton', 'synapse']);

/** Which first when two are as near: the specific thing before the cell it is on. */
const RANK = {
  annotation: 0, skeleton: 1, synapse: 2, surface: 3, wireframe: 4, annowire: 5, shifts: 6,
};

const rankOf = (c) => RANK[c.kind] ?? 9;

/** What makes two candidates the same thing. */
export function identity(c) {
  if (CELL_KINDS.has(c.kind)) return `cell|${c.key}`;
  if (c.kind === 'annotation') return `annotation|${c.key}|${c.locationId}`;
  if (c.kind === 'skeleton') return `skeleton|${c.key}|${c.segment}`;
  if (c.kind === 'synapse') return `synapse|${c.key}|${c.type}`;
  return `${c.kind}|${c.key}`;
}

/** Whether two candidates are the same thing (the row that is the menu's, among the rows). */
export function sameCandidate(a, b) {
  return !!a && !!b && identity(a) === identity(b);
}

/**
 * Everything under the pointer, in the order the menu lists it: nearest the
 * pointer first, to the pixel; then the specific before the general (an
 * annotation, a skeleton branch, a synapse type, then the cell); then nearest
 * the camera.  Each thing once, and a cell once.
 *
 * `items` are the renderer's candidates: `{ kind, key, px, depth, ... }`.
 */
export function listCandidates(items, { limit = MAX_ROWS } = {}) {
  const ordered = (items || [])
    .filter((c) => c && c.kind && c.key != null)
    .sort((a, b) => (Math.round(a.px || 0) - Math.round(b.px || 0))
      || (rankOf(a) - rankOf(b))
      || ((a.depth || 0) - (b.depth || 0)));
  const seen = new Set();
  const out = [];
  for (const c of ordered) {
    const id = identity(c);
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(c);
    if (out.length >= limit) break;
  }
  return out;
}

/**
 * What a click picks (the pick readout's point and cell): the first thing the menu
 * would list under the pointer, or null.  Both buttons read one list, so a thin
 * process a ray goes between, and a line passing a micron from the pointer, are
 * the same thing to a left click as to a right one.
 */
export function pickOf(items) {
  return listCandidates(items, { limit: 1 })[0] ?? null;
}

/** Whether the menu lists what is under the pointer: only when there is a choice. */
export function offersChoice(rows) {
  return rows.length >= 2;
}

/**
 * What a candidate is called.  An ID always travels with its volume (CLAUDE.md
 * ground rule 11).  `cell` is how the cell is named (`c1178`) and `volume` its
 * volume's name.
 */
export function describeCandidate(c, { cell, volume = '' }) {
  if (c.kind === 'annotation') {
    return `Annotation ${c.locationId} (${volume}) · ${cell} · section ${c.section}`;
  }
  if (c.kind === 'nodes') return `${c.label} (${volume}) · ${cell}`;
  if (c.kind === 'skeleton') return `Skeleton branch of ${cell} (${volume})`;
  if (c.kind === 'wireframe') return `Wireframe of ${cell} (${volume})`;
  if (c.kind === 'surface') return `Surface of ${cell} (${volume})`;
  if (c.kind === 'annowire') return `Annotation wireframe of ${cell} (${volume})`;
  if (c.kind === 'shifts') return `Position shift of ${cell} (${volume})`;
  return `${c.type} synapses of ${cell} (${volume})`;
}

/**
 * The menu's title: the cell, and when what it is about is one thing on the
 * cell, which.  A cell alone (its surface, or a click that found no more) keeps
 * the title the menu always had.
 */
export function contextTitle(c, { cell, volume = '' }) {
  if (!c || CELL_KINDS.has(c.kind)) return cell;
  if (c.kind === 'annotation') return `${cell} · annotation ${c.locationId} (${volume})`;
  if (c.kind === 'skeleton') return `${cell} · skeleton branch`;
  if (c.kind === 'synapse') return `${cell} · ${c.type} synapses`;
  return cell;
}

// -- where it goes ---------------------------------------------------------------

/**
 * Where a box of `width` x `height` goes so that it opens at (x, y) and stays on a
 * screen of `view` ({ width, height }) with `margin` to spare.  A box too big for
 * the screen is put at the top left corner, where its first items are, rather than
 * above it where they cannot be reached.
 */
export function placeInside({ x, y }, { width, height }, view, margin = 8) {
  const left = Math.max(margin, Math.min(x, view.width - width - margin));
  const top = Math.max(margin, Math.min(y, view.height - height - margin));
  return { left, top };
}
