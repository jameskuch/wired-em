// Visualizations: a cell's whole look under one name (asked for 2026-10-19), to set
// with one pick, save, delete, and share as a file.  Pure functions and the
// browser store -- no DOM beyond localStorage, read only when asked -- so node
// tests the rules (tests/js/test_visualizations.mjs); app.js puts the menu in the
// cell's row and the right-click menu.
//
// A visualization is everything about how a cell is drawn: its render options
// (the technique and every knob), its display (surface, wireframe, annotations,
// annotation wireframe, skeleton, synapses and each synapse type, Position shift,
// manual move arrows: what is on, and every color, width, opacity and material),
// and its opacity.  Not in it: the cell's own color (each cell keeps its color,
// so a look applied to many cells still tells them apart), its Z range (a cut of
// that cell), its registration and its manual moves (where its annotations are,
// not how they are drawn).  A look may also set the view it is meant to be seen
// in (`scene`, Display's Light background and 2D boxes): Golgi's light flatmount.
// That is the whole page's, not the cell's, so a look is matched without it.
//
//   { id, name, by, note, render: {...}, display: {...}, opacity, scene?: {...} }
//
// Applied, `render` is completed from the render defaults and `display` from the
// display defaults: a visualization sets the whole look, so what it leaves out is
// the default, never what the cell had.

import { DEFAULT_DISPLAY, normalizeDisplay } from './state.js';
import { effectiveSignature, normalizeRender } from './renderoptions.js';

/** Where this browser keeps the visualizations a person saved. */
export const STORE_KEY = 'sbfsem.visualizations.v1';
/** What an exported file says it is. */
export const FILE_KIND = 'sbfsem-visualizations';
export const FILE_VERSION = 1;
/** The display keys that are the cell's own, never a look's. */
export const CELL_ONLY_DISPLAY = Object.freeze(['zMin', 'zMax']);
/** The page's view settings a look may set too, as a saved session names Display's boxes. */
export const SCENE_KEYS = Object.freeze(['invert', 'two_d']);

const clamp01 = (v, fallback) => (typeof v === 'number' && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : fallback);

/**
 * The visualizations offered to everyone, before any is saved.  The first five
 * are what the lab uses, read from the workspaces saved on sbfsem-tools.com
 * (2026-10-19): James's (`jk`), Crystal's (and the shared ALLofUS folder) and,
 * for Becca, who has saved none, from her skeleton work; the next three put one
 * thing forward over the rest; Golgi is MATLAB's `util/renders/golgi.m`, asked
 * for by Crystal (2026-10-30).
 */
export const BUILT_IN = Object.freeze([
  {
    id: 'default',
    name: 'App default',
    by: '',
    note: 'The app\'s own defaults: Voxel remesh, an opaque surface and nothing else.',
    render: {},
    display: {},
    opacity: 0.92,
  },
  {
    id: 'jk-closed-curve-clay',
    name: 'James · Closed curve in clay, skeleton and rings',
    by: 'James',
    note: 'From James\'s workspaces of 2026-10-16 (c21768, c16137, c1178): Closed curve at 0.125 µm, links '
      + 'drawn as curves over 0.125 µm, bodies joined within 0.25 µm, a translucent clay surface, the '
      + 'skeleton as 0.1 µm tubes and the annotations as fine dark rings. Smooth discs and Smooth outlines, '
      + 'which those workspaces had at 0.125 and 0.05, are left off: they move annotations, and the app moves '
      + 'none by default since 2.9.0.',
    render: {
      method: 'closedcurve', pixel_size_um: 0.125, curve_bridge_um: 1, curve_link_bridge_um: 5,
      curve_links_um: 0.125, curve_tie_um: 0.25,
    },
    display: {
      material: 'Clay', showSurface: true, showSkeleton: true, skeletonWidth: 0.1,
      showDiscs: true, discColor: '#0a0c03', discOpacity: 0.7, discStroke: 0.02, discDepth: 0.005, discThrough: true,
    },
    opacity: 0.35,
  },
  {
    id: 'jk-hybrid-glass-depth',
    name: 'James · Hybrid in glass, colored by IPL depth',
    by: 'James',
    note: 'From James\'s workspace of 2026-09-18 (c1178, c8232, c21768): Hybrid under Voxel remesh, colored '
      + 'by IPL depth, a glass surface at 0.8, and faint dark-blue rings through it.',
    render: { method: 'hybrid', pathway: 'voxel2', color_by_strata: true },
    display: {
      material: 'Glass', showSurface: true,
      showDiscs: true, discColor: '#10254f', discOpacity: 0.3, discStroke: 0.1, discThrough: true,
    },
    opacity: 0.8,
  },
  {
    id: 'jk-synapses-faint',
    name: 'James · Faint surface with synapses',
    by: 'James',
    note: 'From James\'s bipolar-cell workspaces of 2026-08-29 (Temporal c108 and five more): a faint '
      + 'surface colored by IPL depth with the synapses on, postsynapses as 0.3 µm ovals and ribbon '
      + 'presynapses hidden.',
    render: { color_by_strata: true },
    display: {
      showSurface: true, showSynapses: true,
      synapseTypes: {
        ConvPost: { shape: 'oval', size: 0.3 },
        RibbonPost: { shape: 'oval', size: 0.3 },
        RibbonPre: { shape: 'oval', size: 0.3, show: false },
      },
    },
    opacity: 0.16,
  },
  {
    id: 'crystal-skeleton-rings',
    name: 'Crystal · Skeleton and rings, no surface',
    by: 'Crystal',
    note: 'From Crystal\'s workspaces (RC2 test cells, Grant eval) and the shared ALLofUS ones: Hybrid under '
      + 'Voxel remesh with the surface hidden, the skeleton as lines and the annotations as bright yellow rings.',
    render: { method: 'hybrid', pathway: 'voxel2' },
    display: {
      showSurface: false, showSkeleton: true, skeletonWidth: 0,
      showDiscs: true, discColor: '#edec4f', discOpacity: 1, discStroke: 0.1, discThrough: true,
    },
    opacity: 0.92,
  },
  {
    id: 'becca-skeleton-check',
    name: 'Becca · Skeleton over the annotations',
    by: 'Becca',
    note: 'For Becca\'s skeleton work (SWC exports and the Skeleton check), which she has not saved as a '
      + 'workspace here, so this is put together from it rather than read: the skeleton as 0.08 µm tubes over '
      + 'the annotations, through a barely-there matte surface, to see where the skeleton leaves the rings.',
    render: {},
    display: {
      material: 'Matte', showSurface: true, showSkeleton: true, skeletonWidth: 0.08,
      showDiscs: true, discOpacity: 0.55, discStroke: 0.04, discThrough: true,
    },
    opacity: 0.12,
  },
  {
    id: 'emphasis-skeleton',
    name: 'Emphasis · Skeleton',
    by: '',
    note: 'The skeleton forward: 0.15 µm tubes in the cell\'s color, the surface a faint matte ghost, no rings.',
    render: {},
    display: { material: 'Matte', showSurface: true, showSkeleton: true, skeletonWidth: 0.15, skeletonOpacity: 1 },
    opacity: 0.08,
  },
  {
    id: 'emphasis-annotations',
    name: 'Emphasis · Annotations',
    by: '',
    note: 'The annotations forward: every ring at full strength through a faint surface, the skeleton off.',
    render: {},
    display: {
      material: 'Matte', showSurface: true,
      showDiscs: true, discOpacity: 1, discStroke: 0.06, discDepth: 0, discThrough: true,
    },
    opacity: 0.12,
  },
  {
    id: 'emphasis-corrections',
    name: 'Emphasis · Annotation error service',
    by: '',
    note: 'What Viking\'s section correction (the corrections service) did to the cell: Position shift on, '
      + 'with each annotation\'s ring where it was and the rings where they are, through a translucent surface; '
      + 'a selected annotation\'s own arrow stands out and the others are dimmed. Manual move arrows on.',
    render: {},
    display: {
      material: 'Matte', showSurface: true,
      showDiscs: true, discOpacity: 0.6, discStroke: 0.04, discThrough: true,
      showShift: true, shiftPair: 'correction', shiftArrows: true, shiftRings: true, shiftColor: 'magnitude',
      shiftFocusDim: 0.06, showMoves: true,
    },
    opacity: 0.25,
  },
  {
    id: 'golgi',
    name: 'Golgi · Flat silhouette from above',
    by: '',
    note: 'MATLAB\'s golgi.m: the surface alone, solid, in one flat color with no light or shade (the Unlit '
      + 'material), seen straight down Z in 2D on a light background, as a Golgi-stained flatmount. Each cell '
      + 'keeps its own color: make it black for MATLAB\'s black on white, or untick Display → Light background '
      + 'and make it white.',
    render: {},
    display: { material: 'Unlit', showSurface: true },
    opacity: 1,
    scene: { invert: true, two_d: true },
  },
]);

// -- one visualization ------------------------------------------------------------------------

/** A display without the keys that are the cell's own. */
function lookDisplay(display) {
  const d = normalizeDisplay(display);
  for (const key of CELL_ONLY_DISPLAY) delete d[key];
  return d;
}

/**
 * A visualization made safe and complete: a name, render options completed and
 * clamped (`normalizeRender`), a display completed from the defaults
 * (`normalizeDisplay`, the cell's own keys left out), an opacity in 0..1.  Null for
 * something that is not one.
 */
export function normalizeVisualization(raw, { id = null } = {}) {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const name = typeof raw.name === 'string' ? raw.name.trim().slice(0, 120) : '';
  if (!name) return null;
  const render = raw.render && typeof raw.render === 'object' && !Array.isArray(raw.render) ? raw.render : {};
  const display = raw.display && typeof raw.display === 'object' && !Array.isArray(raw.display) ? raw.display : {};
  const scene = normalizeScene(raw.scene);
  return {
    id: typeof raw.id === 'string' && raw.id ? raw.id.slice(0, 80) : (id ?? slug(name)),
    name,
    by: typeof raw.by === 'string' ? raw.by.slice(0, 60) : '',
    note: typeof raw.note === 'string' ? raw.note.slice(0, 2000) : '',
    render: normalizeRender(render),
    display: lookDisplay(display),
    opacity: clamp01(raw.opacity, 0.92),
    ...(scene ? { scene } : {}),
  };
}

/** A look's view settings: the SCENE_KEYS it sets, each true or false, or null when it sets none. */
export function normalizeScene(raw) {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const out = {};
  for (const key of SCENE_KEYS) if (typeof raw[key] === 'boolean') out[key] = raw[key];
  return Object.keys(out).length ? out : null;
}

/** A name as an id: "Crystal · Skeleton" -> "crystal-skeleton". */
export function slug(name) {
  return String(name).toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
    .slice(0, 60) || 'visualization';
}

/** The look of a cell as a visualization named `name` (what Save keeps). */
export function fromCell(record, name, { by = '', note = '' } = {}) {
  return normalizeVisualization({
    name, by, note, render: record.render, display: record.display, opacity: record.opacity,
  });
}

/**
 * What applying `vis` to `record` sets: `{ render, display, opacity }`, the display
 * keeping the cell's own Z range.  `hasBoundary(source)`: the cell's volume has the
 * IPL boundary markers coloring by depth needs (it is left off where it has none).
 */
export function applied(vis, record, { hasBoundary = () => true } = {}) {
  const v = normalizeVisualization(vis) || normalizeVisualization(BUILT_IN[0]);
  const own = normalizeDisplay(record?.display);
  const display = { ...v.display };
  for (const key of CELL_ONLY_DISPLAY) display[key] = own[key];
  const render = { ...v.render };
  if (render.color_by_strata && !hasBoundary(record?.source)) render.color_by_strata = false;
  return { render, display, opacity: v.opacity };
}

/** A canonical string of a look, to tell whether a cell shows a visualization now. */
export function lookSignature({ render, display, opacity }) {
  // The render as the build reads it: a knob the technique ignores is not a difference.
  const r = effectiveSignature(render);
  const d = lookDisplay(display);
  const sort = (o) => Object.keys(o).sort().map((k) => [k, k === 'synapseTypes' ? sortTypes(o[k]) : o[k]]);
  return JSON.stringify([r, sort(d), Math.round((opacity ?? 0.92) * 1000) / 1000]);
}

function sortTypes(types) {
  return Object.keys(types || {}).sort().map((name) => [name, Object.keys(types[name]).sort()
    .map((k) => [k, types[name][k]])]);
}

/** The visualization of `list` that `record` shows exactly now, or null ("Custom"). */
export function matching(record, list, { hasBoundary = () => true } = {}) {
  const now = lookSignature({ render: record.render, display: record.display, opacity: record.opacity });
  for (const vis of list) {
    if (lookSignature(applied(vis, record, { hasBoundary })) === now) return vis;
  }
  return null;
}

// -- the saved ones -----------------------------------------------------------------------------

/** The saved list made safe: each one normalized, ids unique, at most 200. */
export function normalizeSaved(raw) {
  const list = Array.isArray(raw) ? raw : [];
  const out = [];
  const ids = new Set(BUILT_IN.map((v) => v.id));
  for (const item of list.slice(0, 200)) {
    const vis = normalizeVisualization(item);
    if (!vis) continue;
    let id = vis.id.startsWith('mine-') ? vis.id : `mine-${vis.id}`;
    for (let n = 2; ids.has(id); n += 1) id = `mine-${slug(vis.name)}-${n}`;
    ids.add(id);
    out.push({ ...vis, id });
  }
  return out;
}

/** The person's saved visualizations, from this browser ([] when none or blocked). */
export function loadSaved(storage = globalThis.localStorage) {
  try {
    return normalizeSaved(JSON.parse(storage?.getItem(STORE_KEY) || '[]'));
  } catch {
    return [];
  }
}

/** Keep `list` in this browser; false when the browser would not. */
export function storeSaved(list, storage = globalThis.localStorage) {
  try {
    storage.setItem(STORE_KEY, JSON.stringify(normalizeSaved(list)));
    return true;
  } catch {
    return false;
  }
}

/** `list` with `vis` in it: a saved one of the same name is replaced, else it is added. */
export function withSaved(list, vis) {
  const clean = normalizeVisualization(vis);
  if (!clean) return list;
  const others = list.filter((v) => v.name.toLowerCase() !== clean.name.toLowerCase());
  return normalizeSaved([...others, { ...clean, id: `mine-${slug(clean.name)}` }]);
}

/** `list` without the ones whose ids are in `ids`. */
export function withoutSaved(list, ids) {
  const gone = new Set(ids);
  return list.filter((v) => !gone.has(v.id));
}

// -- files --------------------------------------------------------------------------------------

/** A file to share: `{ kind, version, saved_at, visualizations }`, as JSON text. */
export function toFile(list, now = new Date()) {
  const items = list.map((v) => {
    const clean = normalizeVisualization(v);
    // Only what differs from the defaults travels, so a file reads as what the look is.
    return {
      name: clean.name,
      ...(clean.by ? { by: clean.by } : {}),
      ...(clean.note ? { note: clean.note } : {}),
      render: diff(clean.render, normalizeRender({})),
      display: diff(clean.display, lookDisplay(DEFAULT_DISPLAY)),
      opacity: clean.opacity,
      ...(clean.scene ? { scene: clean.scene } : {}),
    };
  });
  return `${JSON.stringify({
    kind: FILE_KIND, version: FILE_VERSION, saved_at: now.toISOString(), visualizations: items,
  }, null, 2)}\n`;
}

function diff(values, defaults) {
  const out = {};
  for (const [k, v] of Object.entries(values)) {
    if (JSON.stringify(v) !== JSON.stringify(defaults[k])) out[k] = v;
  }
  return out;
}

/**
 * The visualizations in a file's text: `{ items, skipped }`.  Takes this app's file,
 * one visualization alone, or a list of them; throws an Error with a reason a
 * person can act on for anything else.
 */
export function fromFile(text) {
  let doc;
  try {
    doc = JSON.parse(text);
  } catch {
    throw new Error('not a visualization file: it is not JSON');
  }
  let raw;
  if (Array.isArray(doc)) raw = doc;
  else if (doc && doc.kind === FILE_KIND) {
    if (doc.version > FILE_VERSION) throw new Error(`a newer kind of visualization file (version ${doc.version}): update the page`);
    raw = Array.isArray(doc.visualizations) ? doc.visualizations : [];
  } else if (doc && typeof doc === 'object' && doc.name && (doc.render || doc.display)) raw = [doc];
  else throw new Error('not a visualization file: it names no visualization');
  const items = [];
  let skipped = 0;
  for (const item of raw) {
    const vis = normalizeVisualization(item);
    if (vis) items.push(vis); else skipped += 1;
  }
  if (!items.length) throw new Error('the file holds no visualization this page can read');
  return { items, skipped };
}

/** A file name for `list`: one look's name, or how many. */
export function fileName(list) {
  const base = list.length === 1 ? slug(list[0].name) : `${list.length}-visualizations`;
  return `${base}.sbfsem-vis.json`;
}
