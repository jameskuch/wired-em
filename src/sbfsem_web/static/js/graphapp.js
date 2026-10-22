// GraphApp: MATLAB SBFSEM-tools' GraphApp (views/GraphApp.m), as a second look of
// the 3D view.  The tab row's "GraphApp" button beside "3D render" shows the same
// cells, camera and zoom drawn as GraphApp drew a neuron: the skeleton, a dot at
// every annotation, the annotations' rings, and on request the unfinished,
// terminal and off-edge annotations and the synapses, on black, with no surface.
// The 3D render is left as it was, so going back is instant.
//
// The rules here are pure (no DOM, no three.js) and load under node
// (tests/js/test_graphapp.mjs): the colors, which annotations are unfinished, the
// segment colors, the settings, what Find ID finds, and the visibility an
// isolation keeps to give back.  render3d.js draws it (setGraphCell), app.js
// drives it.

/** Where GraphApp's controls are kept in this browser. */
export const GRAPH_SETTINGS_KEY = 'sbfsem:graphapp';

/**
 * GraphApp's controls as MATLAB opened them: no synapses and no flagged
 * annotations until asked for, "Medium" markers, Parula for "Color segments".
 * The rings are this app's own (GraphApp's "surface" lofted them).
 */
export const GRAPH_DEFAULTS = Object.freeze({
  discs: true,
  synapses: false,
  unfinished: false,
  terminals: false,
  offEdges: false,
  markerSize: 'medium',
  colorSegments: false,
  colormap: 'Parula',
});

/**
 * A dot's diameter on screen, CSS px, by "Marker Size": MATLAB's 2, 7 and 10 pt
 * "." markers, which MATLAB draws at a third of the size given (0.7, 2.3 and
 * 3.3 pt), so a chain of annotations a section apart reads as a beaded line.
 */
export const MARKER_SIZES = Object.freeze({ minimal: 1.5, medium: 3, large: 4.5 });
/** The flagged annotations' and synapses' dots, CSS px: MATLAB's 12 and 15 pt "." markers (4 and 5 pt). */
export const FLAG_PX = 6;
export const SYNAPSE_PX = 7;

/**
 * The flagged annotations' colors on black: GraphApp's red, blue and navy, the
 * blue and the navy lightened so they read against black.
 */
export const FLAG_COLORS = Object.freeze({
  unfinished: '#ff5c5c',
  terminals: '#4aa8ff',
  offEdges: '#a99bff',
});

/** GraphApp's background. */
export const GRAPH_BACKGROUND = '#000000';

/**
 * The hues the cells take in turn, degrees: far apart from one cell to the
 * next, so two cells side by side are told apart.  GraphApp keeps its own
 * colors; the 3D render's are left alone.
 */
export const GRAPH_HUES = Object.freeze([205, 25, 140, 275, 350, 55, 175, 315, 95, 240]);

const hex = (r, g, b) => `#${[r, g, b].map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255)
  .toString(16).padStart(2, '0')).join('')}`;

/** An HSL color (hue degrees, saturation and lightness 0-1) as '#rrggbb'. */
export function hslHex(h, s, l) {
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  return hex(f(0), f(8), f(4));
}

/**
 * A cell's GraphApp colors, by the order it was first drawn in: its annotations
 * (rings and dots) in a pastel, and its skeleton in a near-white of the same
 * hue, so each skeleton reads as its annotations' color against black.
 */
export function graphColors(index) {
  const n = GRAPH_HUES.length;
  const i = ((Number(index) % n) + n) % n;
  const hue = GRAPH_HUES[Number.isFinite(i) ? i : 0];
  return { hue, annotation: hslHex(hue, 0.85, 0.74), skeleton: hslHex(hue, 0.9, 0.9) };
}

/**
 * The annotations GraphApp marked unfinished: linked to exactly one other of
 * the cell's annotations, and neither terminal nor off-edge
 * (`sbfsem.core.structure.Structure.unfinished`, MATLAB's
 * `StructureAPI.unfinished`).  `nodes` are the cell's own (`/nodes`), `edges`
 * its links (`/edges`, `{ a, b }`); a link repeated or drawn both ways counts
 * once, and a link to an annotation that is not the cell's is not counted.
 */
export function unfinishedIds(nodes, edges) {
  const ids = new Set((nodes || []).map((n) => n.id));
  const neighbors = new Map();
  for (const { a, b } of edges || []) {
    if (a === b || !ids.has(a) || !ids.has(b)) continue;
    if (!neighbors.has(a)) neighbors.set(a, new Set());
    if (!neighbors.has(b)) neighbors.set(b, new Set());
    neighbors.get(a).add(b);
    neighbors.get(b).add(a);
  }
  const out = new Set();
  for (const n of nodes || []) {
    if (n.terminal || n.off_edge) continue;
    if (neighbors.get(n.id)?.size === 1) out.add(n.id);
  }
  return out;
}

/**
 * Where on the colormap each of `count` segments is drawn, 0 to 1: the golden
 * ratio's steps, so neighboring segments (which come one after another) land
 * far apart, as GraphApp's shuffled colormap did -- but the same each time,
 * where GraphApp drew new random ones on every tick of the box.
 */
export function segmentPositions(count) {
  const out = [];
  for (let i = 0; i < count; i += 1) out.push((0.5 + i * 0.6180339887498949) % 1);
  return out;
}

/** GraphApp's controls from what was kept, every field checked; the defaults for the rest. */
export function normalizeGraphSettings(value) {
  const v = value && typeof value === 'object' ? value : {};
  const out = { ...GRAPH_DEFAULTS };
  for (const key of ['discs', 'synapses', 'unfinished', 'terminals', 'offEdges', 'colorSegments']) {
    if (typeof v[key] === 'boolean') out[key] = v[key];
  }
  if (Object.hasOwn(MARKER_SIZES, v.markerSize)) out.markerSize = v.markerSize;
  if (typeof v.colormap === 'string' && v.colormap) out.colormap = v.colormap;
  return out;
}

/**
 * What a typed ID names among the loaded cells of one volume, as GraphApp's
 * Find ID read it: a Location ID first (an annotation of a cell, or a synapse's),
 * else a synapse's structure ID, which goes to its annotation.  `cells` are
 * `[{ key, nodes, synapses }]` (the cell's `/nodes` and `/synapse-points`).
 * Answers `{ kind: 'location' | 'synapse', key, locationId, xyz_um, section,
 * synapse }` (`synapse` for an annotation of one), `{ kind: 'unknown', id }`
 * for a number nothing loaded has, or null for text that is not an ID.
 */
export function findId(text, cells) {
  const id = Number.parseInt(String(text ?? '').trim(), 10);
  if (!Number.isFinite(id) || id <= 0 || String(id) !== String(text ?? '').trim()) return null;
  for (const cell of cells || []) {
    const node = (cell.nodes || []).find((n) => n.id === id);
    if (node) {
      return { kind: 'location', key: cell.key, locationId: id, xyz_um: node.xyz_um, section: node.z_section };
    }
    const at = (cell.synapses || []).find((s) => s.location_id === id);
    if (at) {
      return { kind: 'location', key: cell.key, locationId: id, xyz_um: at.xyz_um, section: at.z_section, synapse: at };
    }
  }
  for (const cell of cells || []) {
    const syn = (cell.synapses || []).find((s) => s.synapse_id === id);
    if (syn) {
      return {
        kind: 'synapse', key: cell.key, locationId: syn.location_id, xyz_um: syn.xyz_um,
        section: syn.z_section, synapse: syn,
      };
    }
  }
  return { kind: 'unknown', id };
}

/**
 * Isolating one cell: every other cell hidden, and what each showed kept to give
 * back.  `visible` is `Map(key -> shown)` now; `saved` what an isolation already
 * under way kept (null for none: this is the first, and it keeps what is shown
 * now).  Answers `{ saved, show: Map(key -> shown) }`: what to set now, and what
 * to give back on leaving GraphApp or un-isolating.
 */
export function isolate(visible, key, saved = null) {
  const keep = saved ?? new Map(visible);
  const show = new Map();
  for (const k of visible.keys()) show.set(k, k === key);
  return { saved: keep, show };
}

/**
 * The visibility to give back after an isolation: what was kept, for the cells
 * still loaded; a cell loaded since keeps what it shows now.
 */
export function restoreVisibility(visible, saved) {
  const out = new Map();
  for (const [k, shown] of visible) out.set(k, saved?.has(k) ? saved.get(k) : shown);
  return out;
}
