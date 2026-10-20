// Render settings that will not build as set, before Apply: which controls to
// mark (black, red text) and what to change, best first, with the other ways.
//
// Read from the server's estimates (GET .../render-estimate, sbfsem/service/
// estimate.py), one per selected cell:
//
// * Closed curve and Hybrid: a Voxel size past one of the outline raster's
//   limits (its voxels, its voxel faces, the server's memory, the page's text).
//   Apply builds such a cell at the finest size that fits it and says so; this
//   says it first, and offers that size, and the size with Join bodies or the
//   smoothing off when they are what the memory runs out on.
// * Voxel remesh, Convolution and Ultraliser: a typed Fine voxel the build
//   would contour coarser, because the Face cap's raw-triangle budget, the
//   field evaluations one build may make, or the finest typed voxel (0.02 µm)
//   holds it.  The server sizes the grid as the build does, without building;
//   it offers the least Face cap that builds the voxel as typed, Face cap 0, and
//   the Fine voxel the cap holds.
//
// Pure: no DOM, no fetch.  conflictpanel.js puts it on the page.

import { FIELD_BY_KEY, CURVE_METHODS, normalizeRender } from './renderoptions.js';
import { usable, overReasons, countText, mbText } from './renderestimate.js';

/** The pathways that contour one whole-cell field, whose Fine voxel the server sizes. */
export const FIELD_PATHWAYS = new Set(FIELD_BY_KEY.voxel2_um.show.pathway);

/** Whether `options` build a whole-cell field (a Cylinder or Tube build under a field pathway). */
export const buildsField = (options) => (options.method === 'cylinder' || options.method === 'curvemesh')
  && FIELD_PATHWAYS.has(options.pathway);

const label = (key) => FIELD_BY_KEY[key]?.label ?? key;
const um = (v) => `${Math.round(Number(v) * 10000) / 10000} µm`;
/** "1,460,000". */
export const capText = (n) => Math.round(Number(n) || 0).toLocaleString('en-US');
const cellList = (names) => (names.length <= 3 ? names.join(', ') : `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`);
const close = (a, b) => Math.abs(Number(a) - Number(b)) < 1e-9;

// -- Closed curve and Hybrid: the Voxel size --------------------------------------------

/** What turning each one off sets, and how it is said. */
const OFF_WORDS = { joins: 'Join bodies off', averaging: 'smoothing off', both: 'Join bodies and smoothing off' };

function curveConflicts(entries, options) {
  const over = entries.filter((x) => usable(x.estimate) && x.estimate.over?.length);
  if (!over.length) return null;
  const px = options.pixel_size_um;
  const names = over.map((x) => x.name);
  const keys = new Set(['pixel_size_um']);
  const finest = over.map((x) => Number(x.estimate.finest_um)).filter(Number.isFinite);
  const suggestions = [];
  if (finest.length === over.length) {
    // One size every cell fits at: the coarsest of their finest.
    const fits = Math.max(...finest);
    const each = over.length > 1
      ? ` (each one's finest: ${over.map((x) => `${x.name} ${x.estimate.finest_um}`).join(', ')} µm)` : '';
    suggestions.push({
      text: `${label('pixel_size_um')} ${um(fits)}`,
      detail: `the finest that fits ${cellList(names)}${each}; every other setting stays`,
      set: { pixel_size_um: fits },
      best: true,
    });
  }
  // Join bodies and the smoothing only add memory: with them off a finer size fits.
  for (const kind of ['joins', 'averaging', 'both']) {
    const alts = over.map((x) => (x.estimate.alternatives || []).find((a) => a.off === kind));
    if (alts.some((a) => !a)) continue;
    const set = Object.assign({}, ...alts.map((a) => a.set));
    for (const key of Object.keys(set)) keys.add(key);
    if (alts.every((a) => a.fits)) {
      suggestions.push({
        text: OFF_WORDS[kind],
        detail: `then ${um(px)} fits ${cellList(names)} as typed`,
        set,
      });
    } else {
      const fits = Math.max(...alts.map((a) => Number(a.finest_um)));
      suggestions.push({
        text: `${OFF_WORDS[kind]}, ${label('pixel_size_um')} ${um(fits)}`,
        detail: `${kind === 'averaging' || kind === 'both' ? 'the staircase is then not averaged out; ' : ''}`
          + `the finest that fits ${cellList(names)} without ${kind === 'joins' ? 'joining bodies' : 'it'}`,
        set: { ...set, pixel_size_um: fits },
      });
    }
  }
  const reasons = over.map((x) => `${x.name}: ${overReasons(x.estimate).join('; ')}`);
  return {
    keys: [...keys],
    headline: over.length === 1 && entries.length === 1
      ? `${label('pixel_size_um')} ${um(px)} is too fine for ${names[0]}`
      : `${label('pixel_size_um')} ${um(px)} is too fine for ${over.length} of ${entries.length} cells`,
    reasons,
    suggestions,
    note: 'Applied as set, each of them is built at the finest size that fits it, and a notice says which.',
  };
}

// -- Voxel remesh, Convolution, Ultraliser: the Fine voxel and the Face cap ----------------

const FIELD_REASONS = {
  faces: (f) => `Face cap ${capText(f.voxel2_faces)} lets the build draw ${countText(f.raw_budget)} raw triangles`
    + ' (three for each one kept), too few for a finer grid',
  work: (f) => `the build may make ${countText(f.work_budget)} field evaluations, too few for a finer grid`,
  floor: (f) => `${um(f.floor_um)} is the finest Fine voxel a build takes`,
};

/** The field estimate of an entry, when there is one to read. */
const fieldOf = (x) => (x.estimate?.field?.applies && !x.estimate.field.empty ? x.estimate.field : null);

function fieldConflicts(entries, options) {
  const held = entries.filter((x) => {
    const f = fieldOf(x);
    return f && f.typed && f.coarsened;
  });
  if (!held.length) return null;
  const typed = Number(options.voxel2_um);
  const names = held.map((x) => x.name);
  const fields = held.map(fieldOf);
  const keys = new Set(['voxel2_um']);
  const suggestions = [];
  const reasons = held.map((x) => {
    const f = fieldOf(x);
    const why = (FIELD_REASONS[f.reason] || (() => 'a budget of the whole-cell grid holds it'))(f);
    return `${x.name}: builds at ${um(f.voxel_um)}, not ${um(f.typed_um)}: ${why}`;
  });
  const capHelps = fields.every((f) => f.reason !== 'floor')
    && fields.some((f) => f.cap_for_asked || (f.uncapped && f.uncapped.voxel_um < f.voxel_um - 1e-9));
  if (capHelps) keys.add('voxel2_faces');
  if (fields.every((f) => f.cap_for_asked)) {
    const cap = Math.max(...fields.map((f) => f.cap_for_asked.voxel2_faces));
    const peak = Math.max(...fields.map((f) => f.cap_for_asked.peak_mb));
    const raw = Math.max(...fields.map((f) => f.cap_for_asked.raw_triangles));
    suggestions.push({
      text: `${label('voxel2_faces')} ${capText(cap)}`,
      detail: `builds ${um(typed)} as typed: ≈ ${countText(raw)} raw triangles decimated to`
        + ` ${countText(cap)}, the build ≈ ${mbText(peak)} on the server`,
      set: { voxel2_faces: cap },
      best: true,
    });
  }
  const uncapped = fields.map((f) => f.uncapped);
  if (uncapped.every((u) => u) && fields.some((f) => f.uncapped.voxel_um < f.voxel_um - 1e-9)) {
    const v = Math.max(...uncapped.map((u) => u.voxel_um));
    const tri = Math.max(...uncapped.map((u) => u.triangles));
    const peak = Math.max(...uncapped.map((u) => u.peak_mb));
    suggestions.push({
      text: `${label('voxel2_faces')} 0 (keep every triangle)`,
      detail: `${close(v, typed) ? `builds ${um(typed)} as typed` : `builds ${um(v)}, the finest any Face cap allows`}:`
        + ` ≈ ${countText(tri)} triangles to this page, the build ≈ ${mbText(peak)} on the server`,
      set: { voxel2_faces: 0 },
      best: !suggestions.length,
    });
  }
  // With a cap on, Face cap 0 and the finest voxel together; with none, the voxel
  // the cap holds (below) is that already.
  const unreachable = fields.filter((f) => !f.cap_for_asked
    && (f.uncapped ? f.uncapped.voxel_um : f.voxel_um) > f.asked_um + 1e-9);
  if (unreachable.length && options.voxel2_faces > 0 && fields.every((f) => f.reason !== 'floor')) {
    const v = Math.max(...unreachable.map((f) => f.finest_um));
    suggestions.push({
      text: `${label('voxel2_um')} ${um(v)}, ${label('voxel2_faces')} 0`,
      detail: `the finest any budget allows ${cellList(unreachable.map((f) => names[fields.indexOf(f)]))}`,
      set: { voxel2_um: v, voxel2_faces: 0 },
    });
  }
  // Keep the Face cap: the Fine voxel it holds the build to.
  const holds = Math.max(...fields.map((f) => f.voxel_um));
  suggestions.push({
    text: `${label('voxel2_um')} ${um(holds)}`,
    detail: fields.every((f) => f.reason === 'floor')
      ? 'the finest Fine voxel a build takes'
      : options.voxel2_faces > 0
        ? `what Face cap ${capText(options.voxel2_faces)} holds ${cellList(names)} to; nothing else changes`
        : `the finest any budget allows ${cellList(names)}; nothing else changes`,
    set: { voxel2_um: holds },
    best: !suggestions.length,
  });
  return {
    keys: [...keys],
    headline: held.length === 1 && entries.length === 1
      ? `${label('voxel2_um')} ${um(typed)} will be built coarser for ${names[0]}`
      : `${label('voxel2_um')} ${um(typed)} will be built coarser for ${held.length} of ${entries.length} cells`,
    reasons,
    suggestions,
    note: 'Applied as set, each is built at the voxel shown, and a notice says so.',
  };
}

/**
 * The settings that will not build as set for these cells (`[{ name, estimate }]`,
 * the server's answers, `undefined` while one is out), or null when they will.
 * `{ keys, headline, reasons, suggestions: [{ text, detail, set, best }], note }`:
 * `keys` are the controls to mark; a suggestion's `set` is the options it changes.
 */
export function renderConflicts(entries, options) {
  const o = normalizeRender(options);
  const found = CURVE_METHODS.has(o.method) ? curveConflicts(entries, o)
    : buildsField(o) ? fieldConflicts(entries, o) : null;
  if (!found) return null;
  // A way that changes nothing is none, and two that end in the same settings are
  // one, said the first time.
  const seen = new Set();
  found.suggestions = found.suggestions.filter((s) => {
    const changed = Object.entries(s.set).filter(([k, v]) => !(o[k] === v || close(o[k], v))).sort();
    const key = JSON.stringify(changed);
    if (!changed.length || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!found.suggestions.some((s) => s.best) && found.suggestions.length) found.suggestions[0].best = true;
  return found;
}

/**
 * For the cost chip's hover, under a field pathway: each cell whose automatic
 * Fine voxel the Face cap holds coarser than its annotations ask, and the Face
 * cap that would build it at theirs.  Not a conflict (the cap is what keeps a
 * surface light), so nothing is marked; it is said for whoever wants finer.
 */
export function fieldNotes(entries, options) {
  const o = normalizeRender(options);
  if (!buildsField(o)) return [];
  return entries.flatMap((x) => {
    const f = fieldOf(x);
    if (!f || f.typed || !f.coarsened) return [];
    const ways = [];
    if (f.cap_for_asked) {
      ways.push(`${capText(f.cap_for_asked.voxel2_faces)} (≈ ${mbText(f.cap_for_asked.peak_mb)})`);
    }
    if (f.uncapped && f.uncapped.voxel_um <= f.asked_um + 1e-9) {
      ways.push(`0 (≈ ${countText(f.uncapped.triangles)} triangles, ≈ ${mbText(f.uncapped.peak_mb)})`);
    }
    const reach = ways.length ? `; Face cap ${ways.join(' or ')} builds it there` : '';
    return [`${x.name}: Fine voxel auto is ${um(f.voxel_um)}; its annotations ask for ${um(f.asked_um)}${reach}.`];
  });
}

/** Options with a suggestion's settings put in. */
export const withSuggestion = (options, suggestion) => normalizeRender({ ...options, ...suggestion.set });
