// Render options as data and pure functions: no DOM, no three.js, nothing but
// render-schema.js (generated from src/sbfsem/service/render_schema.py, where
// every option is declared) -- so it loads under node, and both the 3D render
// panel and Render compare can import it without a cycle.
//
// state.js re-exports DEFAULT_RENDER, normalizeRender and renderSignature under
// the names every importer already uses.

import {
  RENDER_FIELDS, RENDER_SLOTS, RENDER_REQUIRES, RENDER_PRESETS,
} from './render-schema.js';

export { RENDER_FIELDS, RENDER_SLOTS, RENDER_REQUIRES, RENDER_PRESETS };

/** Every field by key. */
export const FIELD_BY_KEY = Object.freeze(
  Object.fromEntries(RENDER_FIELDS.map((f) => [f.key, f])));

const keysWhere = (test) => Object.freeze(RENDER_FIELDS.filter(test).map((f) => f.key));

// Mirrors the render endpoint's own defaults, except where the app's product
// default differs on purpose (the schema's one `appDefault`: the pathway; the
// library and bare API keep 'original' so the MATLAB port stays
// parity-first).  Kept complete so a restored session that predates a new
// option still produces a complete request.  The order is the schema's, which
// is the order the undo history's JSON has always had.  A blank Clip IPL is
// `undefined` here, as it always was.
export const DEFAULT_RENDER = Object.freeze(Object.fromEntries(
  RENDER_FIELDS.filter((f) => f.inRender).map((f) => {
    const value = f.hasAppDefault ? f.appDefault : f.default;
    return [f.key, value === null ? undefined : value];
  })));

// The server's accepted range for every numeric knob, mirrored from the mesh
// endpoint's Query bounds. Values can arrive out of range -- typed into a
// number box (the HTML min/max do not stop typing), or restored from an old
// session or saved technique default -- and the server answers 422 for them.
// That 422 then PERSISTS: the failing options are already on the record when
// the build fails, the session auto-save keeps them, and the next morning the
// same cells greet the user with render errors (exactly the 2026-08-31 report:
// a cube size of 0.1 typed during a sweep came back the next day). Clamping
// here, where every path -- controls, session restore, technique defaults,
// attached renders -- already flows through, makes that class of failure
// impossible.
export const RENDER_BOUNDS = Object.freeze(Object.fromEntries(
  RENDER_FIELDS.filter((f) => f.min !== null && f.max !== null).map((f) => [
    f.key,
    Object.freeze(f.kind === 'int' ? { min: f.min, max: f.max, int: true }
      : { min: f.min, max: f.max }),
  ])));

/** Fill in any option the caller left out, so records are always complete,
 *  and clamp every numeric knob into the range the server accepts. */
export function normalizeRender(options) {
  const merged = { ...DEFAULT_RENDER, ...(options || {}) };
  for (const [key, b] of Object.entries(RENDER_BOUNDS)) {
    const value = Number(merged[key]);
    if (!Number.isFinite(value)) {
      merged[key] = DEFAULT_RENDER[key];
      continue;
    }
    const clamped = Math.min(b.max, Math.max(b.min, value));
    merged[key] = b.int ? Math.round(clamped) : clamped;
  }
  return merged;
}

/** Stable key for the mesh cache: same options -> same string. */
export function renderSignature(options) {
  const full = normalizeRender(options);
  return Object.keys(full).sort().map((k) => `${k}=${full[k]}`).join('&');
}

/** `source` restricted to `keys`. */
export const pick = (source, keys) => Object.fromEntries(keys.map((k) => [k, source[k]]));

// The knobs that are a technique's tuning, what a render posts as a mesh, and
// what an export link carries (a mesh's geometry, never its clip or coloring).
export const TECH_KNOBS = keysWhere((f) => f.tune);
export const MESH_OPTION_KEYS = keysWhere((f) => f.scope === 'geometry');
export const EXPORT_KEYS = keysWhere((f) => f.wire.includes('export'));

// -- when a field applies ----------------------------------------------------
/**
 * Which fields hide, gray out or disable for these options: the browser's
 * copy of `field_state` in render_schema.py, checked against the same
 * fixture (tests/fixtures/render_visibility.json) so they cannot drift.
 *
 * A field is hidden unless every `show` rule holds; a visible field is inert
 * when every option of one of its `inert` rules holds one of the rule's values
 * (the first such rule says why); a field that `requires` a server feature is
 * disabled when `ctx` says it is `<feature>Off`.  A hidden field is never also
 * inert: nobody can see it.
 */
export function fieldState(options, ctx = {}) {
  const full = { ...DEFAULT_RENDER, ...(options || {}) };
  const out = {};
  for (const f of RENDER_FIELDS) {
    const hidden = Object.entries(f.show).some(([key, allowed]) => !allowed.includes(full[key]));
    let inert = false;
    let why = '';
    if (!hidden) {
      const rule = f.inert.find(
        (r) => Object.entries(r.when).every(([key, values]) => values.includes(full[key])));
      if (rule) {
        inert = true;
        why = rule.why;
      }
    }
    const disabled = !!(f.requires && ctx[`${f.requires}Off`]);
    out[f.key] = {
      hidden, inert, why, disabled, title: disabled ? RENDER_REQUIRES[f.requires] : '',
    };
  }
  return out;
}

/**
 * Like `renderSignature`, but an option the technique ignores counts as its
 * default: a field these options hide, or gray out as ignored (the rules
 * above, which are what the build was measured to read).  Two renders that
 * differ only in such an option are one surface, so they share a cached mesh
 * and Apply has nothing to rebuild; picking the technique a cell already had
 * again, or Defaults, used to rebuild it for a knob the build never reads.
 */
export function effectiveSignature(options) {
  const full = normalizeRender(options);
  const state = fieldState(full);
  return Object.keys(full).sort().map((key) => {
    const ignored = state[key] && (state[key].hidden || state[key].inert);
    return `${key}=${ignored ? DEFAULT_RENDER[key] : full[key]}`;
  }).join('&');
}

/**
 * The tuning knobs that define a technique, as "Label value" strings: the
 * pathway's own parameters, the voxel size of the outline methods and the
 * MeshLab mode's, each only while the options show it and the build reads it.
 * What a failed build is reported with ("Envelope σ 0.5, Fine voxel auto"):
 * the parameters to walk back.
 */
export function knobSummary(options) {
  const full = normalizeRender(options);
  const state = fieldState(full);
  const parts = [];
  for (const f of RENDER_FIELDS) {
    if (!f.tune || state[f.key].hidden || state[f.key].inert) continue;
    const own = Object.keys(f.show).length > 0 || (f.key === 'meshlab' && full.meshlab !== 'none');
    if (!own) continue;
    // Smooth discs reached the tubes in 2.7.0; off, it says nothing about a tube's
    // surface, so a Cylinder, Tube or Hybrid summary names it only when it is on.
    // So with Exact tubes (2.9.0), off unless asked for.
    if (f.key === 'curve_avg_discs' && full.method !== 'closedcurve' && !full[f.key]) continue;
    if (f.key === 'exact_tube_deg' && !full[f.key]) continue;
    const value = f.key === 'voxel2_um' && !full[f.key] ? 'auto' : full[f.key];
    parts.push(`${f.label} ${value}`);
  }
  return parts;
}

/** The controls a state shows: every UI field the options do not hide. */
export function visibleKeys(options, ctx = {}) {
  const state = fieldState(options, ctx);
  return RENDER_FIELDS.filter((f) => f.ui && !state[f.key].hidden).map((f) => f.key);
}

/** A field's help, led by why it is grayed out when it is. */
export const helpFor = (field, state) => (
  state?.inert ? `${state.why}\n\n${field.help}` : field.help);

// -- technique defaults -------------------------------------------------------
// Every technique has its own best parameters, so "Defaults" must never
// switch the technique itself (it used to jump everything back to Voxel
// remesh, discarding whichever pathway was being tuned). The recommended
// values ship in DEFAULT_RENDER; "Save technique defaults" overlays the
// user's own tuning for the current technique, kept in this browser.
export const TECH_DEFAULTS_KEY = 'sbfsem:technique-defaults';

export function loadTechDefaults() {
  try { return JSON.parse(localStorage.getItem(TECH_DEFAULTS_KEY)) || {}; }
  catch { return {}; }
}

/** Keep the saved tunings in this browser; false when it will not (a private window). */
export function storeTechDefaults(all) {
  try {
    localStorage.setItem(TECH_DEFAULTS_KEY, JSON.stringify(all));
    return true;
  } catch {
    return false;
  }
}

export const techKey = (method, pathway) => `${method}|${pathway}`;

/** A technique's default options: the recommended values, then the user's saved tuning. */
export function techniqueDefaults(method, pathway) {
  const saved = loadTechDefaults()[techKey(method, pathway)] || {};
  const base = { ...DEFAULT_RENDER, method, pathway };
  for (const k of TECH_KNOBS) if (saved[k] !== undefined) base[k] = saved[k];
  return base;
}

// -- the named techniques -------------------------------------------------------
/**
 * The preset these options are, or null (Custom).  A preset is a method, a
 * pathway and -- for the three MeshLab entries -- a MeshLab mode: it is tried
 * on all three first, then on method and pathway alone, so Original with
 * MeshLab isotropic on still reads Original instead of Custom (as it always
 * did), while Voxel remesh with qem reads Voxel remesh + MeshLab quadric.
 */
export function matchPreset(options) {
  const { method, pathway } = options;
  const meshlab = options.meshlab ?? 'none';
  const same = RENDER_PRESETS.filter((p) => p.method === method && p.pathway === pathway);
  return same.find((p) => (p.set.meshlab ?? 'none') === meshlab) ?? same[0] ?? null;
}

/**
 * The options a preset asks for: the technique's own defaults (the user's
 * saved tuning, else the recommended values) with the preset's own settings on
 * top.  Only those: a plain preset does not force MeshLab off, so a user who
 * saved a MeshLab default for the technique still gets it.
 */
export function presetOptions(preset) {
  return { ...techniqueDefaults(preset.method, preset.pathway), ...preset.set };
}

// -- the closed-curve raster ---------------------------------------------------
/**
 * The closed-curve raster a cell's outlines are seeded at: 1 um for vessels,
 * 0.25 um otherwise.  The 0.05 um default is for a small structure's detail;
 * a whole traced cell at 0.05 runs past the server's voxel limit (RC1 c410:
 * 476M voxels against 300M).
 */
export const curvePixelFor = (summary) => ((summary || {}).type_id === 3 ? 1 : 0.25);

// The methods that rasterize outlines: the ones the Voxel size control shows for,
// so the schema's rule is the one place the list is written.
export const CURVE_METHODS = new Set(FIELD_BY_KEY.pixel_size_um.show.method);

/**
 * The outline raster a technique's untouched defaults should use on this
 * cell, or null to leave `options` alone.
 *
 * The default 0.05 um pixel suits a small structure; picking Hybrid or
 * Closed curve for a whole traced cell at 0.05 failed on the voxel limit and
 * stayed failed, the choice being saved with the cell (RC1 c410,
 * 2026-10-01).  Only the default is replaced: a pixel the user typed, or
 * saved as their technique default, is theirs.
 */
export function curvePixelSeed(record, options) {
  if (!record || !CURVE_METHODS.has(options.method)) return null;
  if (!(record.summary?.n_curve_annotations > 0)) return null;
  if (options.pixel_size_um !== DEFAULT_RENDER.pixel_size_um) return null;
  const saved = loadTechDefaults()[techKey(options.method, options.pathway)];
  if (saved && saved.pixel_size_um !== undefined) return null;
  const seeded = curvePixelFor(record.summary);
  return seeded > options.pixel_size_um ? seeded : null;
}

// -- what a build costs ---------------------------------------------------------
// Seconds per annotation as timed locally, on c1178 / c10912 / c103 (2.4k / 11k /
// 34k annotations: linear at ~1.55 ms for Cylinder and ~0.06 ms for Tube), and
// SERVER_FACTOR, the ~5x the 2-vCPU server (retired 2026-09-17; the box now is
// 4 vCPU) measured slower on the one cell timed on both.  The estimate is the
// product.
const SERVER_FACTOR = 5;
const SECONDS_PER_ANNOTATION = { cylinder: 0.00155, curvemesh: 0.00006 };
// Voxel remesh rebuilds the thick segments from a distance field.  With the
// disc-loft field this is cheap -- c1178 measured 5.3s against 4.2s, c4010
// 18.1s against 17.5s -- because each z-slice of a loft is scalar arithmetic.
// Voxel remesh 2 contours the whole cell at dendrite resolution: c106 built
// in 2.0 s against 0.34 s on voxel, c1178 in 6.0 s against 4.3 s (auto
// voxel) -- a fixed cost per soma plus one that scales with the arbor.
const PATHWAY_COST = {
  original: 1, adaptive_taubin: 1.1, voxel: 1.0, wrap: 1.4, voxel2: 4.0,
  convolution: 6.0, ultraliser: 4.5,
};
// Closed curve is a voxel grid, so it scales with resolution, not annotations.
// From c2542: 0.7s / 2.7s / 10.4s at 0.1 / 0.05 / 0.025 um for 40 sections.  Each
// halving of the pixel is about 4x the work (0.0175 / 0.0675 / 0.26 s), so a raster
// that was not timed -- the 0.25 um and 1 um a traced cell is seeded to, a typed
// one -- is the 0.05 um rate scaled by (0.05 / pixel)^2.
const CLOSED_CURVE_PER_SECTION = { 0.1: 0.0175, 0.05: 0.0675, 0.025: 0.26 };
const CLOSED_CURVE_REFERENCE_UM = 0.05;
const closedCurveRate = (pixelUm) => CLOSED_CURVE_PER_SECTION[pixelUm]
  ?? CLOSED_CURVE_PER_SECTION[CLOSED_CURVE_REFERENCE_UM] * (CLOSED_CURVE_REFERENCE_UM / pixelUm) ** 2;

/** Rough seconds to build one cell's mesh on the server. */
export function estimateBuildSeconds(record, options) {
  const o = normalizeRender(options);
  const n = record.summary.n_nodes || 0;
  if (o.method === 'closedcurve') {
    const per = closedCurveRate(o.pixel_size_um);
    // Averaging out the staircase adds the chain fits, the labels and the surface passes:
    // about a fifth more for discs (c16137 at 0.1 um: 2.5 s, 3.0 s) and four fifths more
    // for traced outlines (c2628: 3.0 s, 5.5 s).
    // Curving long disc links adds the curves' outlines (c1178 at 0.1 um: about a second on
    // nine), joining bodies the search for gaps and the blocks contoured again (RPC1 c2628 at
    // 0.1 um, 397 ties: about 2.5 s of the build; c1178 at 0.25 um 1.4 s; a third more, as
    // the server's estimate counts it, JOIN_TIME): the viewer joins them by default.
    const averaging = 1 + (o.curve_avg_discs > 0 ? 0.2 : 0) + (o.curve_avg_paths > 0 ? 0.8 : 0)
      + (o.curve_links_um > 0 ? 0.1 : 0) + (o.curve_tie_um > 0 ? 0.3 : 0);
    // Sections carrying a closed curve, which is far fewer than annotations.
    return per * Math.max(n / 20, 1) * SERVER_FACTOR * averaging;
  }
  const rate = SECONDS_PER_ANNOTATION[o.method] ?? SECONDS_PER_ANNOTATION.cylinder;
  return rate * n * SERVER_FACTOR * (PATHWAY_COST[o.pathway] ?? 1);
}
