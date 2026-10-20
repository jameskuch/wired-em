// Decisions app.js makes that need no page: what the browser's copy of a surface
// is keyed on, which of two draws of one cell is the live one, what a file
// dropped on Import is, whether a key press belongs to a text field, whether
// an undo step changes where a cell is drawn, and how long a flared status
// message keeps the line.  Plain functions and small classes, so node can run
// them (tests/js/test_appcore.mjs) -- app.js itself cannot be loaded without a
// page.

// -- the browser's copy of a surface ------------------------------------------

/**
 * The key of a surface in the browser's mesh cache: the cell, the copy of it
 * this tab holds (Viking's correction mode and current transforms), the
 * annotations' stamp, the moves applied to them (`positions`), and the render
 * options' signature.  These are the things the server keys its own surface on,
 * so a cell reloaded after Viking edited it, or loaded under another setting, is
 * never served the surface of the one before.  The copy says what was asked for
 * and `positions` what was done: a correction asked for while the service was
 * down leaves the cell uncorrected under the same copy, and its surface must not
 * answer the corrected load of the same copy that follows.  Every key of a cell
 * starts `${record.key}|`, which is how a cell's surfaces are dropped together.
 */
export function meshCacheKey(record, signature) {
  return `${copyPrefix(record)}${signature}`;
}

/**
 * The part of a surface's key that names the cell and the copy of it held now.
 * `positions` joins its tokens with `|` itself: as `+` here, so that one copy's
 * prefix is never the start of another's (the transforms' token alone, then the
 * transforms' and the correction's).
 */
function copyPrefix(record) {
  const summary = record.summary || {};
  const positions = String(summary.positions ?? '').replaceAll('|', '+');
  return `${record.key}|${summary.copy_tag ?? ''}|${summary.data_stamp ?? ''}|${positions}|`;
}

/** Remove from `cache` (a Map, or a MeshCache) every key that starts with `prefix`. */
export function dropWithPrefix(cache, prefix) {
  for (const key of [...cache.keys()]) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}

/**
 * Remove a cell's surfaces of every copy but the one `record` holds now: the
 * cell was loaded again as other data (Viking edited it, or another
 * Registration or section correction moved its annotations), so the surfaces of
 * the copy before can only be asked for again by an Undo of that very change,
 * which loads the cell again and builds them anew.
 */
export function dropOtherCopies(cache, record) {
  const own = copyPrefix(record);
  for (const key of [...cache.keys()]) {
    if (key.startsWith(`${record.key}|`) && !key.startsWith(own)) cache.delete(key);
  }
}

/**
 * How much of the browser's memory a surface holds, in numbers: its vertex
 * coordinates, its triangle indices and the per-vertex data that colors it.
 * Measured with the default Voxel remesh: c943 0.8 million, c21768 and c10912
 * 3.6 million, c1178 4.7 million.
 */
export function meshWeight(mesh) {
  if (!mesh) return 0;
  return (mesh.vertices?.length ?? 0) + (mesh.faces?.length ?? 0) + (mesh.vertex_data?.length ?? 0);
}

/**
 * What the tab keeps: 24 million numbers, up to about 200 MB of its memory (a
 * number is 4 to 8 bytes: a face index 4, a coordinate 8, in a JavaScript array
 * or the typed arrays a finished render arrives in).  Five or six large cells,
 * or a few hundred small ones.  The renderer holds its own copies of what is on
 * screen; this is what comes on top of them.
 */
export const MESH_CACHE_LIMIT = 24_000_000;

/**
 * The surfaces this tab has, least recently used first out when their
 * weight passes `limit`.  A Map's interface (`get`, `set`, `has`, `delete`,
 * `keys`, `clear`, `size`) so `dropWithPrefix` works on it; `get` and `set`
 * count as use.
 *
 * Unloading a cell does not empty it: the cell's surfaces stay for Undo, which
 * used to download every one of them again (c1178 came back in 1.9 s instead
 * of 0.3 s; three cells in 3.6 s instead of 0.5 s on localhost).  The key
 * carries the copy of the cell and its annotations' stamp (`meshCacheKey`), so
 * a surface kept this way is the one that cell would be built again, and the
 * bound is what stops them piling up over a long session.  The surface just
 * stored is never the one let go, whatever it weighs.
 */
export class MeshCache {
  constructor(limit = MESH_CACHE_LIMIT, weigh = meshWeight) {
    this.limit = limit;
    this.weigh = weigh;
    this.entries = new Map();   // key -> { mesh, weight }, oldest first
    this.weight = 0;
  }

  get size() { return this.entries.size; }

  has(key) { return this.entries.has(key); }

  keys() { return this.entries.keys(); }

  get(key) {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    this.entries.delete(key);       // to the newest end
    this.entries.set(key, entry);
    return entry.mesh;
  }

  set(key, mesh) {
    this.delete(key);
    const weight = this.weigh(mesh);
    this.entries.set(key, { mesh, weight });
    this.weight += weight;
    for (const [oldest, entry] of this.entries) {
      if (this.weight <= this.limit || oldest === key) break;
      this.entries.delete(oldest);
      this.weight -= entry.weight;
    }
    return this;
  }

  delete(key) {
    const entry = this.entries.get(key);
    if (!entry) return false;
    this.entries.delete(key);
    this.weight -= entry.weight;
    return true;
  }

  clear() {
    this.entries.clear();
    this.weight = 0;
  }
}

// -- a draw's extras, asked for while its surface builds -------------------------

/**
 * Which of a cell's extras a draw of it will fetch, from its display (`normalizeDisplay`)
 * and its Position shift settings (`normalizeShiftDisplay`): the annotations always (the
 * rings and the shift layer draw them, and after a manual move's Apply the markers and
 * the Manual moves list read them), the closed-curve outlines when the rings or the shift
 * layer's rings are on and the cell has any (`hasCurves`), the skeleton and the
 * annotation wireframe when each is shown.
 */
export function extrasWanted(display, shift, { hasCurves = true } = {}) {
  return {
    nodes: true,
    outlines: Boolean(hasCurves && (display.showDiscs || (shift.showShift && shift.shiftRings))),
    skeleton: Boolean(display.showSkeleton),
    wire: Boolean(display.showAnnoWire),
  };
}

/**
 * Start the requests a draw's extras will make, before its surface is built: the
 * ones `wanted` asks for (`extrasWanted`) and `cached(name)` does not already hold.
 * `fetchers` maps each name to a function that sends its request.  Each is sent at
 * once, and settles to its answer, or to null when it fails (the draw then asks again
 * as it always has, and reports that failure).  Answers `{ name: promise }`.
 */
export function startExtras(wanted, fetchers, cached = () => false) {
  const pending = {};
  for (const [name, fetch] of Object.entries(fetchers)) {
    if (!wanted[name] || cached(name)) continue;
    // An async function runs to its first await at once: the request leaves now.
    pending[name] = (async () => fetch())().catch(() => null);
  }
  return pending;
}

/**
 * Put what `startExtras` fetched for the cell `key` into `caches` (`{ name: Map }`),
 * once the draw that started it reaches its extras, so they draw from the caches as if
 * they had fetched it themselves.  Nothing is kept when `gone()` says the cell went or
 * was loaded again meanwhile (what came back is of the copy before), and nothing
 * replaces what a cache already holds for this load.  Answers false when gone.
 */
export async function adoptExtras(pending, caches, key, gone) {
  const names = Object.keys(pending).filter((name) => caches[name]);
  const values = await Promise.all(names.map((name) => pending[name]));
  if (gone()) return false;
  names.forEach((name, i) => {
    if (values[i] != null && !caches[name].has(key)) caches[name].set(key, values[i]);
  });
  return true;
}

/**
 * One request for each `key` and `gen` (a cell and its load): `send()` is called by the
 * first asker, and everyone who asks for the same load after it gets the same promise,
 * answered or not, so what one load's request brought back is never asked for twice.  A
 * request of an earlier load is never handed to a later one; a failed one is dropped, so
 * the next asker tries again.  `pending` is the caller's Map, and the caller deletes a
 * key when it lets the load go.  A Registration switch asked for the cell's annotations
 * twice, 0.6 s apart: once with its extras while the surface built, and again for the
 * points that show while it builds, before the first answer had reached the caches.
 */
export function sharedRequest(pending, key, gen, send) {
  const out = pending.get(key);
  if (out && out.gen === gen) return out.promise;
  // An async function runs to its first await at once: the request leaves now, and a
  // `send` that throws is a rejected promise like any other failure.
  const entry = { gen, promise: (async () => send())() };
  pending.set(key, entry);
  entry.promise.catch(() => { if (pending.get(key) === entry) pending.delete(key); });
  return entry.promise;
}

// -- an action that arrives twice ------------------------------------------------

/**
 * `task` that runs once at a time: asked again while a call is still pending,
 * it does nothing and answers `undefined`.  A held Enter (key repeat) or a
 * double click on Save used to start a second write while the first was in the
 * air; the server answered one of them with a 500.
 */
export function singleFlight(task) {
  let running = false;
  return async function singleFlighted(...args) {
    if (running) return undefined;
    running = true;
    try {
      return await task.apply(this, args);
    } finally {
      running = false;
    }
  };
}

// -- dropdowns -----------------------------------------------------------------

/**
 * Whether `options` (a select's `<option>` elements, in order) already are
 * `entries` (`[label, value]`).  The label is compared as it was written
 * (`textContent`): `option.text` strips and collapses whitespace, so a label
 * with two spaces in a row never matched itself and the picker was replaced on
 * every event, closing an open dropdown.
 */
export function sameOptions(options, entries) {
  return entries.length === options.length
    && entries.every(([label, value], i) => options[i].value === value
      && options[i].textContent === label);
}

/**
 * The volume a volume dropdown ends on: the one asked for when it has loaded
 * cells (the cell a right-click was made on), else the one it showed, else the
 * first with cells.  `sources` are the volumes that have loaded cells; with
 * none, the dropdown still shows `current`.
 */
export function chooseSource({ sources, current, preferred = null }) {
  if (preferred && sources.includes(preferred)) return preferred;
  return sources.includes(current) ? current : (sources[0] ?? current);
}

// -- which draw of a cell is the live one --------------------------------------

/**
 * Numbers the draws of each cell so only the newest may land.
 *
 * A draw is a mesh build awaited over the network.  Press Apply twice, or
 * Apply while a restore is still drawing, and two builds of one cell are in the
 * air; whichever finished last used to win, which was not always the newest.
 * `begin(key)` names a new draw and retires the one before it; a draw asks
 * `current(key, n)` after each wait and stands down when it is not.  The
 * numbers never repeat (one counter for every cell), so `forget` cannot let an
 * old draw come back to life.
 */
export class DrawTokens {
  constructor() {
    this.latest = new Map();
    this.counter = 0;
  }

  /** A draw of `key` begins: its number.  Any earlier draw of the cell is retired. */
  begin(key) {
    this.counter += 1;
    this.latest.set(key, this.counter);
    return this.counter;
  }

  /** Is draw `n` still the newest draw of `key`? */
  current(key, n) {
    return this.latest.get(key) === n;
  }

  /** The cell is gone: every draw of it is retired. */
  forget(key) {
    this.latest.delete(key);
  }
}

// -- long help ----------------------------------------------------------------

// The ordinary help panel is 340 px wide, padding and border included.
const HELP_COLUMN_PX = 314;
const HELP_GAP_PX = 18;
const HELP_FRAME_PX = 26;

/**
 * The layouts to try for a help panel, narrowest first: one column (the
 * ordinary panel), then two, three and four side by side, none wider than the
 * window.  The first is always offered, however narrow the window is.
 */
export function helpLayouts(viewportWidth, maxColumns = 4) {
  const layouts = [];
  for (let columns = 1; columns <= maxColumns; columns += 1) {
    const width = columns * HELP_COLUMN_PX + (columns - 1) * HELP_GAP_PX + HELP_FRAME_PX;
    if (columns === 1 || width <= viewportWidth - 16) layouts.push({ columns, width });
  }
  return layouts;
}

/**
 * The first of `layouts` whose panel is no taller than `limit` (`heightAt`
 * lays the text out in one and measures it), with that height.  When none is
 * short enough the narrowest is answered, `fits: false`: the caller scrolls it.
 * The Pathway help is nearly 3,000 characters: 1,154 px tall in one column,
 * most of it below the bottom of a 1080 px window.
 */
export function pickHelpLayout(layouts, limit, heightAt) {
  for (const layout of layouts) {
    const height = heightAt(layout);
    if (height <= limit) return { ...layout, height, fits: true };
  }
  const narrowest = layouts[0];
  return { ...narrowest, height: heightAt(narrowest), fits: false };
}

// -- Import -------------------------------------------------------------------

/**
 * What a file given to Import is.  A neuron export or a workspace is a JSON
 * object; anything else is a structure list in the ID box's grammar (IDs,
 * ranges and labels), loaded into the selected volume.  A list of one ID --
 * `1178` -- is also valid JSON (a number), which is why "parses as JSON" is not
 * the test: it was sent to the neuron importer and failed.
 * Returns `{ kind: 'workspace' | 'neuron' | 'structures', doc }`.
 */
export function importKind(text) {
  let doc = null;
  try {
    doc = JSON.parse(text);
  } catch {
    return { kind: 'structures', doc: null };
  }
  if (doc && doc.kind === 'sbfsem-workspace' && Array.isArray(doc.neurons)) {
    return { kind: 'workspace', doc };
  }
  if (doc && typeof doc === 'object' && !Array.isArray(doc)) return { kind: 'neuron', doc };
  return { kind: 'structures', doc: null };
}

// -- keyboard -----------------------------------------------------------------

// Inputs whose own keys Ctrl+Z is not: a checkbox, a slider or a button edits
// no text, so the shortcut belongs to the app while one has the focus.
const NON_TEXT_INPUTS = new Set([
  'checkbox', 'radio', 'range', 'button', 'submit', 'reset', 'color', 'file', 'image',
]);

/** Is `element` a place the user is typing text, whose undo is its own? */
export function isTypingTarget(element) {
  if (!element) return false;
  if (element.isContentEditable) return true;
  const tag = String(element.tagName || '').toUpperCase();
  if (tag === 'TEXTAREA') return true;
  if (tag === 'INPUT') return !NON_TEXT_INPUTS.has(String(element.type || 'text').toLowerCase());
  return false;
}

// -- undo ----------------------------------------------------------------------

/**
 * Does applying a history step change which copy of the cell is loaded?  The
 * Registration panel's two choices -- Viking's current section transforms and
 * its section correction -- are kept on the cell and saved in every step; a step
 * that differs in either needs the cell loaded again under the step's choices,
 * not just redrawn.  `undefined` is a choice (the server's default), so it is
 * compared as it is.
 */
export function registrationChoiceDiffers(record, entry) {
  return record.registration !== entry.registration || record.corrections !== entry.corrections
    || movesDiffer(record.manualMoves, entry.manualMoves);
}

/**
 * Do two cells' manual moves (manualmoves.js) differ?  A history step with other
 * moves is another copy of the cell on the server, loaded again like a
 * Registration change.  No moves and an empty store are the same.
 */
export function movesDiffer(a, b) {
  const text = (store) => (store && Object.keys(store).length ? JSON.stringify(store) : '');
  return text(a) !== text(b);
}

/**
 * What the status line says after the Panes box went from `before` panes to `after`, or null when
 * nothing changed.  New panes are empty and wait for Compare; fewer panes leave the ones that stay
 * as they were (a change of the count used to rebuild every pane, and "press Compare to fill the new
 * panes" dates from then: said after lowering the count it points at panes that are not new).
 */
export function paneCountMessage(before, after) {
  if (after > before) return `Press Compare to fill the new pane${after - before === 1 ? '' : 's'}.`;
  if (after < before) return `Showing ${after} pane${after === 1 ? '' : 's'}; the ones that stay keep what they show.`;
  return null;
}

// -- the view a load put up ------------------------------------------------------

/**
 * Are two `Renderer3D.cameraState()`s the same view?  A load that framed the
 * view, or put a saved one back, as its first cells came in compares the camera
 * at its end with what it put up: the same, and the load frames the whole scene
 * (or puts the saved view back) over it; moved, and the person turned, panned
 * or zoomed it while the cells came in, so it stays where they put it.  Null is
 * a camera aimed at nothing (`cameraState()`), the same only as another null.
 * The controls recompute the camera every frame, so positions are compared to a
 * millionth of the camera's distance from its target (half a nanometer at
 * 500 µm), far under the smallest move a hand makes, and never with `==`.
 */
export function sameCamera(a, b) {
  if (!a || !b) return !a && !b;
  if (Boolean(a.orthographic) !== Boolean(b.orthographic)) return false;
  const near = (p, q, tol) => Array.isArray(p) && Array.isArray(q) && p.length === q.length
    && p.every((v, i) => Math.abs(v - q[i]) <= tol);
  const span = Math.hypot(...a.position.map((v, i) => v - a.target[i]));
  const tol = Math.max(span, 1) * 1e-6;
  const zoomA = a.zoom ?? 1;
  const zoomB = b.zoom ?? 1;
  return near(a.position, b.position, tol) && near(a.target, b.target, tol)
    && near(a.up ?? [0, 0, 1], b.up ?? [0, 0, 1], 1e-6)
    && Math.abs(zoomA - zoomB) <= Math.max(Math.abs(zoomA), 1) * 1e-6;
}

// -- a flared status message against the busy tracker --------------------------

/** How long a flared message keeps the status line: the flare's 2.4 s (`.status.flare`, app.css) and a moment to read it. */
export const FLARE_HOLD_MS = 4000;

/**
 * Keeps a flared status message (app.js `flareStatus`) on the line until it has
 * been read.
 *
 * The busy tracker (api.js `onBusy`) says "ready" when the last request in
 * flight ends, whoever started it.  A request begun before the flare and ending
 * after it -- the freshness check a load starts as its surface lands -- wiped
 * "Viking client can only be run on Windows." a few milliseconds after it was
 * put up.  `hold(at)` starts the hold; until it ends `admit(update, at)` turns
 * the tracker's updates away and keeps the last.  `release()` ends it and gives
 * that update back while work is still going on (a `busy` one), so a long
 * build's progress is not lost; after a "ready" it gives nothing, and the
 * message stays until the next update, as a flare with nothing in flight
 * always has.  Times are milliseconds on one clock (`performance.now()`).
 */
export class StatusHold {
  constructor(ms = FLARE_HOLD_MS) {
    this.ms = ms;
    this.until = -Infinity;
    this.missed = null;
  }

  /** A message was flared at `at`: hold the line for `ms` from now (a second flare starts again). */
  hold(at) {
    this.until = at + this.ms;
    this.missed = null;
  }

  /** May the tracker's `update` ({ pending, message, kind }) be shown at `at`? */
  admit(update, at) {
    if (at < this.until) {
      this.missed = update;
      return false;
    }
    this.missed = null;
    return true;
  }

  /** The hold is over: the update to show now, or null. */
  release() {
    const missed = this.missed;
    this.until = -Infinity;
    this.missed = null;
    return missed?.kind === 'busy' ? missed : null;
  }
}
