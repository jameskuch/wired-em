// Application state: which neurons are loaded, their colors, visibility and
// per-neuron render settings. Views subscribe to changes rather than reaching
// into each other.
//
// Render settings live on the record, not on the sidebar controls, so two
// neurons can be shown at once with different methods -- a closed-curve volume
// beside a Line3 cylinder -- and so changing the controls does not silently
// invalidate what is already on screen.

import { SHIFT_DEFAULTS, SHIFT_KEYS } from './shifts.js';
import {
  DEFAULT_RENDER, normalizeRender, renderSignature, effectiveSignature,
} from './renderoptions.js';

export const PALETTE = [
  '#2f6fed', '#e0643a', '#22a06b', '#a855f7', '#e0a800',
  '#0ea5b7', '#d6336c', '#7c8b1f', '#5b6dcd', '#b3541e',
];

// Per-cell display settings. Unlike DEFAULT_RENDER these mostly cost nothing to
// change -- color and opacity are a material update -- but synapse size and
// shape need a fresh mesh from the server.
export const DEFAULT_DISPLAY = Object.freeze({
  // Synapses are always imported with the cell, but start hidden: a fresh
  // load shows the surface, and the markers are a checkbox away. A saved
  // workspace carries its own explicit value and is unaffected.
  showSynapses: false,
  // Cell-wide defaults. Each synapse type inherits these until it is given its
  // own override in `synapseTypes`, so a cell with one setting for everything
  // stays simple and per-type tuning is opt-in.  No control sets these four:
  // the panel edits the per-type overrides (and "Apply this cell to ..." copies
  // them), so a cell's defaults are these values unless a saved workspace
  // carries others.
  synapseSize: 0.5,
  // null means "use the per-type Viking colors"; a hex string overrides them.
  synapseColor: null,
  synapseOpacity: 1,
  synapseShape: 'circle',
  //: { [localName]: { show?, size?, color?, opacity?, shape? } } -- sparse:
  //: only keys the user actually changed are present.
  synapseTypes: {},
  // Surface finish, by name from the material menu (presets plus the shared
  // library); resolved when applied, so a deleted custom name falls back.
  material: 'Standard',
  // The three ways a cell can be drawn, all independent of each other.
  showSurface: true,
  showWireframe: false,
  showSkeleton: false,
  // null means "use the cell's own color".
  wireframeColor: null,
  wireframeOpacity: 1,
  // The raw Viking annotations, drawn as rings in their section planes.
  showDiscs: false,
  discColor: null,
  discOpacity: 0.85,
  // Drawn over the surface: an opaque soma otherwise hides its own rings.
  discThrough: true,
  // Ring stroke width in microns.
  discStroke: 0.1,
  // Ring depth along Z in microns.  Never less than one pixel on the screen, so
  // edge-on an annotation stays a hairline at any zoom: zero is that pixel alone
  // (since 2026-10-27; before, zero was the flat ribbon, which vanishes edge-on,
  // and a saved zero now takes the pixel).  The section thickness makes each
  // annotation the slab of tissue it was drawn on.
  discDepth: 0,
  // The plain ribbon in the section plane, with no least height (Depth's "flat").
  discFlat: false,
  // The annotations as a wireframe: each one a ring in its section plane,
  // strung along Viking's links, every ring averaged over a Z window of
  // `annoWireGain` times its own radius -- microns in a soma, a section or two
  // in a thin dendrite.  The build settings are fetched; color, opacity and
  // Through are a material update.
  showAnnoWire: false,
  annoWireGain: 0.5,
  annoWireFit: 'quadratic',
  annoWireTolerance: 1.5,
  annoWireBreak: 2.5,
  annoWireLines: 12,
  // Rings at least this far apart along a chain, microns: at every section
  // (70 or 90 nm, by volume) a soma's rings read as a solid sheet, not a
  // wireframe.  0 draws every annotation.
  annoWireSpacing: 0.5,
  annoWireColor: null,
  annoWireOpacity: 0.9,
  annoWireThrough: true,
  // null means "use the cell's own color".
  skeletonColor: null,
  // Microns. Zero draws a one-pixel line; anything larger becomes a tube,
  // because WebGL ignores line width.
  skeletonWidth: 0,
  skeletonOpacity: 1,
  // The Z range drawn, in the volume's microns; null leaves that side open.
  // Set by "Hide above" / "Hide below" on a selected annotation: a hide, not
  // a re-render, and exports are not affected.
  zMin: null,
  zMax: null,
  // The Position shift layer: an arrow from where each annotation was to where
  // it is drawn (shifts.js documents each key).  Drawn from the cell's cached
  // annotations, so none of these costs a request except turning it on.
  ...SHIFT_DEFAULTS,
  // The cell's manual moves (movecontrol.js), an arrow each, while it has any.
  showMoves: true,
});

/** Effective settings for one synapse type: its overrides over the cell's. */
export function typeSettings(display, localName) {
  const d = normalizeDisplay(display);
  const over = d.synapseTypes?.[localName] || {};
  return {
    show: over.show !== undefined ? over.show : true,
    size: over.size !== undefined ? over.size : d.synapseSize,
    // `null` is meaningful here (use the Viking color), so only `undefined`
    // falls through to the cell default.
    color: over.color !== undefined ? over.color : d.synapseColor,
    opacity: over.opacity !== undefined ? over.opacity : d.synapseOpacity,
    shape: over.shape !== undefined ? over.shape : d.synapseShape,
  };
}

/** True when this type has been given any setting of its own. */
export function hasTypeOverride(display, localName) {
  const over = normalizeDisplay(display).synapseTypes?.[localName];
  return !!over && Object.keys(over).length > 0;
}

// A round marker is a display convention and is drawn far larger than a real
// synapse (~0.1 um half-length) so it can be seen at all.  An oval is drawn at
// the annotation's true length, so the same thickness would make it wider than
// it is long and it would read as a disc.  Each shape therefore has its own
// sensible thickness, applied when the user switches between them.
export const DEFAULT_SYNAPSE_SIZE = Object.freeze({ circle: 0.5, oval: 0.1 });

// The render options -- their defaults, the ranges every numeric knob is
// clamped into, and the two functions that complete and compare them -- are
// declared once in the schema (render-schema.js, generated from
// src/sbfsem/service/render_schema.py) and built in renderoptions.js.  They are
// re-exported under the names every importer already uses.
export { DEFAULT_RENDER, normalizeRender, renderSignature, effectiveSignature };

export const state = {
  source: 'NeitzInferiorMonkey',
  volumes: [],
  neurons: new Map(), // key `${source}/${id}` -> record
  // The focused cell: what the Analyses and Annotations tabs describe. Always
  // a member of `selection` when anything is selected.
  selected: null,
  // Multi-selection, which is what the Apply actions operate on. Kept separate
  // from the per-row visibility checkbox: hiding a cell and choosing which
  // cells an action targets are different questions, and conflating them made
  // "Apply to selected" impossible to aim.
  selection: new Set(),
  view: 'render',
};

const listeners = new Set();
let colorIndex = 0;

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emit(event = 'change') {
  for (const fn of listeners) fn(event, state);
}

export const keyOf = (source, id) => `${source}/${id}`;

export function normalizeDisplay(display) {
  const merged = { ...DEFAULT_DISPLAY, ...(display || {}) };
  // Never share the frozen default object between records.
  merged.synapseTypes = { ...(display?.synapseTypes || {}) };
  return merged;
}

/** Which display fields require a new mesh from the server. */
const SERVER_DISPLAY_KEYS = ['synapseSize', 'synapseShape'];

/** Skeleton geometry has to be rebuilt for these, but nothing is refetched. */
const SKELETON_GEOMETRY_KEYS = ['skeletonWidth'];

/** The annotation wireframe is fetched again when any of these change. */
export const ANNO_WIRE_KEYS = ['annoWireGain', 'annoWireFit', 'annoWireTolerance',
  'annoWireBreak', 'annoWireLines', 'annoWireSpacing'];

/** Per-type geometry keys, compared across every type present. */
function typeGeometrySignature(display) {
  const d = normalizeDisplay(display);
  return Object.keys(d.synapseTypes).sort().map((name) => {
    const t = d.synapseTypes[name];
    return `${name}:${t.size ?? ''}:${t.shape ?? ''}:${t.show ?? ''}`;
  }).join('|');
}

/** Whether any Position shift key differs: the layer is rebuilt from the cached annotations. */
export function shiftChanged(a, b) {
  return SHIFT_KEYS.some((k) => a[k] !== b[k]);
}

export function displayNeedsRefetch(before, after) {
  const a = normalizeDisplay(before);
  const b = normalizeDisplay(after);
  // Either direction: turning a feature on fetches it, turning it off removes
  // it.  Testing only for the "on" direction left unchecking a box changing
  // the state and nothing on screen.
  if (a.showSynapses !== b.showSynapses) return true;
  if (a.showSkeleton !== b.showSkeleton) return true;
  if (a.showDiscs !== b.showDiscs) return true;
  if (a.showAnnoWire !== b.showAnnoWire) return true;
  if (shiftChanged(a, b)) return true;
  if (b.showAnnoWire && ANNO_WIRE_KEYS.some((k) => a[k] !== b[k])) return true;
  if (a.discStroke !== b.discStroke) return true;
  if (a.discDepth !== b.discDepth) return true;
  if (a.discFlat !== b.discFlat) return true;
  if (SKELETON_GEOMETRY_KEYS.some((k) => a[k] !== b[k])) return true;
  if (typeGeometrySignature(a) !== typeGeometrySignature(b)) return true;
  return SERVER_DISPLAY_KEYS.some((k) => a[k] !== b[k]);
}

/** Merge one type's overrides into a display object, dropping empty ones. */
export function withTypeOverride(display, localName, changes) {
  const d = normalizeDisplay(display);
  const next = { ...(d.synapseTypes[localName] || {}), ...changes };
  for (const key of Object.keys(next)) {
    if (next[key] === undefined) delete next[key];
  }
  const types = { ...d.synapseTypes };
  if (Object.keys(next).length) types[localName] = next;
  else delete types[localName];
  return { ...d, synapseTypes: types };
}

let loadCounter = 0;

/**
 * What a cell's positions were drawn from: the annotations' stamp, the copy
 * the server holds for this tab's Registration/correction choices, and the moves
 * that copy was given (`positions`).  The stamp alone is the same for every copy
 * of one cell (a cell's `off.0`, `off.1` and `neighbor.1` copies share it), and a
 * copy is what was asked for, not what was done (a correction the service could
 * not make leaves `neighbor.1` uncorrected), so anything computed from the cell's
 * positions -- the Morphology numbers, the Skeleton check -- is cached on this
 * and goes stale when the choice or the moves change.
 */
export function dataKey(record) {
  const summary = record?.summary;
  const stamp = summary?.data_stamp || summary?.last_modified || '';
  return `${stamp}|${summary?.copy_tag ?? ''}|${summary?.positions ?? ''}`;
}

/**
 * Add a loaded cell to the list, or fold a fresh load of a listed one back in.
 *
 * A new cell joins the selection (loading is an explicit act).  `select: false`
 * leaves the selection as it is, for the loads that are not a request to look at
 * the cell: Update, Refresh all and a Registration click re-fetch a cell that is
 * already listed, and used to tick the box the user had unticked.
 */
export function upsertNeuron(detail, { render, display, select = true } = {}) {
  const { summary } = detail;
  const key = keyOf(summary.source, summary.id);
  const existing = state.neurons.get(key);
  // Drawn with other settings because the ones asked for failed (renderfallback.js),
  // a cell keeps them until its annotations change; new data is built as asked again.
  const sameData = existing?.summary?.data_stamp === summary.data_stamp
    && existing?.summary?.n_nodes === summary.n_nodes;
  const fallback = render === undefined && sameData ? existing?.fallback ?? null : null;
  const prior = existing?.fallback && !sameData ? existing.fallback.asked : existing?.render;
  const record = {
    key,
    id: summary.id,
    source: summary.source,
    summary,
    synapseCounts: detail.synapse_counts || [],
    volume: detail.volume,
    color: existing?.color ?? PALETTE[colorIndex++ % PALETTE.length],
    visible: existing?.visible ?? true,
    opacity: existing?.opacity ?? 0.92,
    // An explicit `render` wins (a restored session), then whatever the record
    // already had, then the defaults.
    render: normalizeRender(render ?? prior),
    // `{ asked, failure, used, text, detail }` while it is drawn with other settings.
    fallback,
    display: normalizeDisplay(display ?? existing?.display),
    // Whether Viking has edited the cell since its data was fetched; filled
    // by the load response, cleared by a refresh.
    freshness: detail.freshness ?? existing?.freshness ?? null,
    // Viking's section correction, as this tab asked for it: 'off' or 'neighbor'
    // (the residual field; app.js `savedCorrection` reads a saved true, curvefit or
    // all as that), or undefined for the server's default (the volume's
    // `default_correction`, the residual field).  What was actually applied is
    // summary.section_correction.
    corrections: existing?.corrections,
    // The mode last chosen in the Correction dropdown, gone since 2026-10-27 (one
    // mode is left); kept so a session saved with it says what it said.
    correctionMode: existing?.correctionMode,
    // Viking's current section transforms, likewise (undefined: the server's
    // default, on); what was applied is summary.live_registration.
    registration: existing?.registration,
    // A person's own moves of the cell's annotations, by the registration setting
    // they were made under (manualmoves.js); sent with every load of the cell, and
    // what the server applied is summary.manual_moves.
    manualMoves: existing?.manualMoves ?? null,
    // Which load this is, numbered across all cells so an unloaded and
    // reloaded cell never repeats one.  Every upsert is followed by a redraw,
    // and a draw still under way for an earlier load stands down when it sees
    // a newer one (loadGeneration), rather than land its old surface last.
    loadGen: ++loadCounter,
  };
  state.neurons.set(key, record);
  // A freshly loaded cell joins the selection: loading is an explicit act, and
  // it means the Apply buttons target the batch you just asked for without a
  // second round of clicking.
  if (select) state.selection.add(key);
  if (!state.selected || !state.neurons.has(state.selected)) state.selected = key;
  emit('neurons');
  return record;
}

/** The current load of a cell (0 once it is unloaded); see `loadGen`. */
export function loadGeneration(key) {
  return state.neurons.get(key)?.loadGen ?? 0;
}

export function removeNeuron(key) {
  state.neurons.delete(key);
  state.selection.delete(key);
  if (state.selected === key) {
    state.selected = state.neurons.keys().next().value ?? null;
  }
  emit('neurons');
}

/**
 * Focus a cell without touching the selection.
 *
 * Focus is "the cell being looked at" -- it drives the Analyses and Annotations
 * tabs and seeds the Render panel. Selection is "the cells an action applies
 * to", and is only ever changed by its own checkbox. Conflating the two made
 * it impossible to open cell B while A and C stayed selected, which is exactly
 * the case the Apply buttons exist for.
 */
export function selectNeuron(key) {
  if (state.selected === key) return;
  state.selected = key;
  emit('selection');
}

/** Check or uncheck one cell's selection box. */
export function setSelected(key, on) {
  if (!state.neurons.has(key)) return;
  const had = state.selection.has(key);
  if (on === had) return;
  if (on) state.selection.add(key);
  else state.selection.delete(key);
  emit('selection');
}

/**
 * Select every cell between `anchorKey` and `key` inclusive.
 *
 * Shift-clicking a selection box extends from the last box that was clicked,
 * which is what every list in every other program does.
 */
export function selectRange(anchorKey, key) {
  const keys = [...state.neurons.keys()];
  const to = keys.indexOf(key);
  const from = anchorKey ? keys.indexOf(anchorKey) : to;
  if (to < 0) return;
  const [a, b] = from <= to && from >= 0 ? [from, to] : [to, Math.max(from, to)];
  for (const k of keys.slice(a, b + 1)) state.selection.add(k);
  emit('selection');
}

export function selectAll() {
  state.selection = new Set(state.neurons.keys());
  if (!state.selected) state.selected = state.selection.values().next().value ?? null;
  emit('selection');
}

export function clearSelection() {
  state.selection = new Set();
  emit('selection');
}

export function isSelected(key) {
  return state.selection.has(key);
}

/** The focused cell, for views that describe exactly one. */
export function selectedRecord() {
  return state.selected ? state.neurons.get(state.selected) : null;
}

/** Every selected cell, for actions that operate on many. */
export function selectedRecords() {
  return [...state.selection]
    .map((k) => state.neurons.get(k))
    .filter(Boolean);
}

export function visibleRecords() {
  return [...state.neurons.values()].filter((r) => r.visible);
}

export function setColor(key, color) {
  const record = state.neurons.get(key);
  if (!record) return;
  record.color = color;
  emit('style');
}

export function setVisible(key, visible) {
  const record = state.neurons.get(key);
  if (!record) return;
  record.visible = visible;
  emit('style');
}

/**
 * Set one neuron's render settings.
 *
 * Returns true when something actually changed, so the caller can skip a
 * rebuild -- a mesh build is seconds of server CPU, not a free repaint.
 */
export function setRenderOptions(key, options) {
  const record = state.neurons.get(key);
  if (!record) return false;
  const next = normalizeRender(options);
  // Compared as the build reads them: an option the technique ignores is not a change.
  if (effectiveSignature(record.render) === effectiveSignature(next)) return false;
  record.render = next;
  emit('render-options');
  return true;
}

/**
 * Set one neuron's display settings.
 *
 * Returns ``{changed, refetch}``.  ``refetch`` means the scene objects have to
 * be rebuilt (some of them from the server).
 */
export function setDisplay(key, display) {
  const record = state.neurons.get(key);
  if (!record) return { changed: false, refetch: false };
  const next = normalizeDisplay({ ...record.display, ...display });
  const changed = JSON.stringify(next) !== JSON.stringify(record.display);
  const refetch = changed && displayNeedsRefetch(record.display, next);
  record.display = next;
  if (changed) emit('display');
  return { changed, refetch };
}

export function setSource(source) {
  state.source = source;
  emit('source');
}
