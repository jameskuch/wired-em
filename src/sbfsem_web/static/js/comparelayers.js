// The layer model of Render compare, without a page: nothing here reads
// `document`, `window` or three.js, so tests/js/test_comparelayers.mjs runs it
// under node.
//
// A pane draws up to four surface layers of one cell in one renderer.  What a
// layer is (a technique's options, a color, an opacity), which colors the
// layers get, what request a layer makes (the snapshot rule), how the work of
// many panes is scheduled and how a downloaded mesh is shared between layers
// are decided here; compare.js draws them.
//
// Three rules the rest depends on:
//   * A layer's request comes from its built snapshot: `pickOptions` of the
//     options the surface was built with, never of the options it shows now
//     (see compare.js, "A pane measures the surface it HOLDS").
//   * Colors are by slot, not by technique, so two layers of one technique
//     (a before and an after) get two hues.  Slot 0 is the yellow every pane
//     has always had and keeps the near-black wireframe, so a one-layer pane is
//     what it was.
//   * One serial queue per pane, and at most MAX_WORKING_PANES panes working at
//     once: the server has four build workers shared with everyone, and its
//     memory gate protects the box.

import { MESH_OPTION_KEYS } from './renderoptions.js';

/** A pane holds one to four surface layers: no fifth color cleared the others. */
export const MAX_LAYERS = 4;
export const MIN_PANES = 1;
export const MAX_PANES = 6;
/** Panes with a job in the air at once (their layers run one after another). */
export const MAX_WORKING_PANES = 3;
/** Meshes kept in the browser, most recently used: the server's own cache size. */
export const MESH_CACHE_LIMIT = 12;

/**
 * Surface layer colors by slot: #ffd400, #56B4E9, #CC79A7 and #009E73, in the lower case
 * a color input reports.  Minimum pairwise CIEDE2000 against the dark viewport under
 * normal, protan, deutan and tritan vision: 43.9 for two layers, 14.0 for three,
 * 12.1 for four (D7 spec, section 2).
 */
export const LAYER_PALETTE = Object.freeze(['#ffd400', '#56b4e9', '#cc79a7', '#009e73']);
export const SURFACE_COLOR = LAYER_PALETTE[0];     // yellow
export const WIREFRAME_COLOR = '#202020';          // near-black (32, 32, 32)
export const DISC_COLOR = '#0019ff';               // blue (0, 25, 255)
/** The opacity of a layer added after the first: translucent, so the one under it shows. */
export const LATER_OPACITY = 0.45;
/** The Ghost preset: the layer is a faint shape to read the others through. */
export const GHOST_OPACITY = 0.15;
export const MIN_OPACITY = 0.05;

const HEX = /^#?([0-9a-f]{6})$/i;

/** `#rrggbb` in lower case, or null for anything else. */
export function normalizeHex(color) {
  const match = HEX.exec(String(color ?? '').trim());
  return match ? `#${match[1].toLowerCase()}` : null;
}

/** `color` with every channel multiplied by `factor` (0 to 1), as `#rrggbb`. */
export function darken(color, factor) {
  const hex = normalizeHex(color) ?? '#808080';
  const channel = (i) => Math.round(parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16) * factor);
  return `#${[0, 1, 2].map((i) => channel(i).toString(16).padStart(2, '0')).join('')}`;
}

/** What a layer starts as in slot `slot`: color, opacity, visibility, surface and wire. */
export function newLayerStyle(slot, color = null) {
  return {
    color: normalizeHex(color) ?? LAYER_PALETTE[slot % LAYER_PALETTE.length],
    opacity: slot === 0 ? 1 : LATER_OPACITY,
    visible: true,
    surface: true,
    wire: false,
  };
}

/** The first palette color none of `used` has: the color of a layer added next. */
export function nextColor(used) {
  const taken = new Set(used.map((c) => normalizeHex(c)).filter(Boolean));
  return LAYER_PALETTE.find((c) => !taken.has(c)) ?? LAYER_PALETTE[used.length % LAYER_PALETTE.length];
}

/** A layer opacity inside the range the renderer takes; blank or nonsense is opaque. */
export function clampOpacity(value) {
  if (value === '' || value === null || value === undefined) return 1;
  const v = Number(value);
  return Number.isFinite(v) ? Math.min(1, Math.max(MIN_OPACITY, v)) : 1;
}

/**
 * The wireframe color of a layer.  The yellow of slot 0 keeps the near-black
 * wire every pane has always had; any other color's wire is its own hue at
 * 45 % brightness, so a wire reads on its surface and tells whose it is.
 */
export function wireColorFor(color) {
  const hex = normalizeHex(color);
  return hex === null || hex === SURFACE_COLOR ? WIREFRAME_COLOR : darken(hex, 0.45);
}

/** "Original (MATLAB)" -> "Original", "Voxel remesh + MeshLab isotropic" -> "Voxel remesh + ML isotropic". */
export function shortName(label) {
  return String(label ?? '').replace(/\s*\([^)]*\)\s*/g, ' ').replace(/MeshLab/g, 'ML').trim();
}

// -- the snapshot rule ---------------------------------------------------------

/** The options a measurement is requested from, in a fixed key order. */
export function pickOptions(options) {
  return Object.fromEntries(
    MESH_OPTION_KEYS.filter((k) => options[k] !== undefined).map((k) => [k, options[k]]));
}

/** A stable text for a set of picked options: equal settings, equal key. */
export const stableKey = (picked) => JSON.stringify(picked);

/** What a downloaded mesh is filed under: the cell, which load of it, and the options. */
export const meshKey = (record, picked) => `${record.key}#${record.loadGen}|${stableKey(picked)}`;

// -- states --------------------------------------------------------------------

// The worst of a pane's layer states, first match: work in progress, then
// waiting, then a failure, a stop and a layer that no longer matches.
const STATE_PRIORITY = ['building', 'measuring', 'queued', 'error-build', 'error-measure',
  'stopped', 'stale'];

/**
 * The state of a pane from the states of its layers.  A layer never built next
 * to ones that were (a layer just added) makes the pane out of date: what it
 * shows does not hold every layer yet.
 */
export function worstState(states) {
  for (const state of STATE_PRIORITY) if (states.includes(state)) return state;
  if (states.length && states.every((s) => s === 'ready')) return 'ready';
  return states.includes('ready') ? 'stale' : 'idle';
}

// -- order -----------------------------------------------------------------------
// A layer's place in the draw order is its index in the list: the first draws first and wins ties.

/** `list` with the item at `from` moved to `to` (both clamped); a new array. */
export function moveItem(list, from, to) {
  const next = [...list];
  if (from < 0 || from >= next.length) return next;
  const [item] = next.splice(from, 1);
  next.splice(Math.min(next.length, Math.max(0, to)), 0, item);
  return next;
}

// -- a pane's Z range ---------------------------------------------------------

/**
 * A Z range from two text or number fields: `{ min, max }` in microns with a
 * blank side open, the two put in order, or null when both are blank.
 */
export function normalizeZRange(min, max) {
  const side = (value) => {
    if (value === '' || value === null || value === undefined) return null;
    const v = Number(value);
    return Number.isFinite(v) ? v : null;
  };
  let lo = side(min);
  let hi = side(max);
  if (lo === null && hi === null) return null;
  if (lo !== null && hi !== null && lo > hi) [lo, hi] = [hi, lo];
  return { min: lo, max: hi };
}

// -- sharing downloaded meshes ----------------------------------------------------

/** What a waiter that left (or never joined) rejects with: the shape pollJob's own aborts have. */
export function abortedError() {
  return Object.assign(new Error('aborted'), { aborted: true });
}

/**
 * Meshes already downloaded, most recently used last, and the downloads in the
 * air.  A technique in three panes is downloaded and parsed once: the second
 * layer to ask finds it, or joins the download under way.  A download is
 * abandoned only when every layer waiting for it has left (Stop on one pane
 * does not take the mesh away from another).
 */
export class MeshCache {
  constructor(limit = MESH_CACHE_LIMIT) {
    this.limit = limit;
    this._meshes = new Map();
    this._flights = new Map();
  }

  get size() { return this._meshes.size; }

  /** The filed keys, least recently used first. */
  keys() { return [...this._meshes.keys()]; }

  has(key) { return this._meshes.has(key); }

  /** The filed mesh (it becomes the most recently used), or undefined. */
  get(key) {
    if (!this._meshes.has(key)) return undefined;
    const mesh = this._meshes.get(key);
    this._meshes.delete(key);
    this._meshes.set(key, mesh);
    return mesh;
  }

  set(key, mesh) {
    this._meshes.delete(key);
    this._meshes.set(key, mesh);
    while (this._meshes.size > this.limit) this._meshes.delete(this._meshes.keys().next().value);
  }

  /** Forget every filed mesh (downloads in the air are not touched). */
  clear() { this._meshes.clear(); }

  /** How many layers are waiting for the download of `key` (0 when none is under way). */
  waiting(key) { return this._flights.get(key)?.waiters.size ?? 0; }

  /**
   * The mesh for `key`: filed, or the download under way, or `start(...)`.
   * `start({ signal, onStatus })` does the download and resolves with the mesh;
   * `signal` ends it when the last waiter has left and `onStatus` reaches every
   * waiter's own.  `fresh` skips what is filed and what is under way (the
   * server's copy changed: ask again) and files the new answer.  `signal` is
   * this caller's own Stop; leaving rejects with an `aborted` error.
   */
  fetch(key, start, { signal = null, onStatus = null, fresh = false, cacheable = (m) => !!m?.n_faces } = {}) {
    if (!fresh) {
      if (this._meshes.has(key)) return Promise.resolve(this.get(key));
      const flight = this._flights.get(key);
      if (flight) return this._join(key, flight, { signal, onStatus });
    }
    // A caller that has already left starts nothing: there is no one for the download to serve.
    if (signal?.aborted) return Promise.reject(abortedError());
    const flight = { controller: new AbortController(), waiters: new Set(), promise: null };
    flight.promise = Promise.resolve().then(() => start({
      signal: flight.controller.signal,
      onStatus: (job) => { for (const waiter of [...flight.waiters]) waiter.onStatus?.(job); },
    })).then((mesh) => {
      if (this._flights.get(key) === flight) this._flights.delete(key);
      if (cacheable(mesh) && !flight.controller.signal.aborted) this.set(key, mesh);
      return mesh;
    }, (error) => {
      if (this._flights.get(key) === flight) this._flights.delete(key);
      throw error;
    });
    // Nobody may be left to hear this one (every waiter stopped): not an unhandled rejection.
    flight.promise.catch(() => {});
    this._flights.set(key, flight);
    return this._join(key, flight, { signal, onStatus });
  }

  _join(key, flight, { signal, onStatus }) {
    return new Promise((resolve, reject) => {
      const waiter = { onStatus };
      flight.waiters.add(waiter);
      let done = false;
      const leave = () => {
        if (done) return;
        done = true;
        flight.waiters.delete(waiter);
        signal?.removeEventListener?.('abort', leave);
        if (!flight.waiters.size) {
          // Nobody wants it: a new caller must not join a download that is being abandoned.
          if (this._flights.get(key) === flight) this._flights.delete(key);
          flight.controller.abort();
        }
        reject(abortedError());
      };
      if (signal) {
        if (signal.aborted) { leave(); return; }
        signal.addEventListener('abort', leave, { once: true });
      }
      flight.promise.then((mesh) => {
        if (done) return;
        done = true;
        signal?.removeEventListener?.('abort', leave);
        flight.waiters.delete(waiter);
        resolve(mesh);
      }, (error) => {
        if (done) return;
        done = true;
        signal?.removeEventListener?.('abort', leave);
        flight.waiters.delete(waiter);
        reject(error);
      });
    });
  }
}

// -- scheduling ----------------------------------------------------------------------

/**
 * One serial queue per key (a pane) with at most `limit` keys running at once.
 * The jobs of one key run in the order they were queued; keys start in the
 * order they first had work.  A job may wait for promises (`after`) before it
 * takes a place: a Difference waits for the surfaces it measures, and meanwhile
 * the places go to other panes.  `enqueue` resolves when its job has run (or
 * has thrown: the error is the job's to report, not the queue's), and never
 * rejects.
 */
export class WorkQueues {
  constructor({ limit = MAX_WORKING_PANES } = {}) {
    this.limit = limit;
    this._queues = new Map();
    this._active = new Set();
  }

  /** The keys with a job running. */
  get working() { return [...this._active]; }

  /** The jobs waiting (not running), over all keys. */
  get waiting() {
    let n = 0;
    for (const queue of this._queues.values()) n += queue.length;
    return n;
  }

  enqueue(key, job, { after = [] } = {}) {
    return new Promise((resolve) => {
      const item = { job, resolve, ready: !after.length };
      let queue = this._queues.get(key);
      if (!queue) { queue = []; this._queues.set(key, queue); }
      queue.push(item);
      if (after.length) {
        Promise.allSettled(after).then(() => { item.ready = true; this._pump(); });
      }
      this._pump();
    });
  }

  _pump() {
    for (const [key, queue] of this._queues) {
      if (this._active.size >= this.limit) return;
      if (this._active.has(key)) continue;
      const item = queue[0];
      if (!item || !item.ready) continue;
      queue.shift();
      this._active.add(key);
      Promise.resolve().then(item.job).catch(() => {}).then(() => {
        this._active.delete(key);
        if (!queue.length && this._queues.get(key) === queue) this._queues.delete(key);
        item.resolve();
        this._pump();
      });
    }
  }
}
