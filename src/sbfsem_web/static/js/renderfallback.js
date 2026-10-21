// What to build when a cell's render fails because of its settings, best first.
//
// Unless expert render mode is on (Settings), the 3D view builds these one after
// another, keeps the first that draws a surface on the cell and says so; with it
// on, a cell is built exactly as set, and the render panel marks the settings and
// offers these as the ways to fix them (renderconflicts.js).  Render compare builds
// its panes as set either way.
//
// Measured 2026-10-21 (tests/fixtures i943 and i90000, through the service):
// every disc technique (Voxel remesh, Voxel remesh orig, Original, Tube,
// Convolution, Wrap) draws nothing from annotations no link joins -- one disc, or
// three unlinked: 0 faces -- while Closed curve draws each one as its own disc in
// its section (120 faces at 0.25 µm) unless it is narrower than about two voxels
// (a disc of radius 0.26 µm: nothing at 0.25 µm).  All 11 empty builds in
// production's job log (2026-10-16 to 10-21) were cells with one annotation.
//
// Pure: no DOM, no fetch.  Loads under node (tests/js/test_renderfallback.mjs).

import {
  DEFAULT_RENDER, RENDER_PRESETS, FIELD_BY_KEY, CURVE_METHODS, normalizeRender,
  effectiveSignature, techniqueDefaults, presetOptions, matchPreset, curvePixelFor,
  curvePixelSeed, loadTechDefaults, techKey,
} from './renderoptions.js';

// -- what failed ---------------------------------------------------------------------

/** A closed-curve refusal names the finest Voxel size that fits (closed_curve.build_curve_volume). */
export const FINEST_PIXEL = /would need ([\d,]+) (voxels|voxel faces).*?the limit is ([\d,]+)\. The finest pixel that fits this structure is ([\d.]+) um/;

/** A build the server could not finish for want of memory: the pool lost its process (jobs.py). */
const OUT_OF_MEMORY = /terminated abruptly|ran out of memory|stopped before this build finished/i;

/**
 * What kind of failure ended a build, when it was the settings':
 * `{ kind: 'empty' | 'too-fine' | 'memory' | 'error', detail }`, and for 'too-fine'
 * `fits` (µm), `need`, `measure` and `limit` from the server's message.  Null for one
 * that other settings would not mend: the network, a restart, a job the server
 * lost, the person's own stop.  `error` is what the build threw (api.js: a failed
 * job's ApiError carries `serverDetail`); `empty` a mesh with no faces.
 */
export function failureOf({ error = null, empty = false } = {}) {
  if (empty) return { kind: 'empty', detail: '' };
  if (!error || error.aborted) return null;
  const detail = error.serverDetail;
  if (typeof detail !== 'string') {
    // Out of range as sent (normalizeRender clamps, so rare): the settings' own.
    if (error.status === 422) return { kind: 'error', detail: String(error.detail ?? error.message ?? '') };
    return null;
  }
  const fine = FINEST_PIXEL.exec(detail);
  if (fine) {
    return {
      kind: 'too-fine', detail, fits: Number(fine[4]), need: fine[1], measure: fine[2], limit: fine[3],
    };
  }
  if (OUT_OF_MEMORY.test(detail)) return { kind: 'memory', detail };
  return { kind: 'error', detail };
}

// -- names --------------------------------------------------------------------------

const PRESET = Object.fromEntries(RENDER_PRESETS.map((p) => [p.id, p]));

/** The technique's name ("Voxel remesh orig"), or method/pathway for a custom one. */
export function techniqueName(options) {
  const match = matchPreset(options);
  return match ? match.label : `${options.method}/${options.pathway}`;
}

const um = (v) => `${Math.round(Number(v) * 10000) / 10000} µm`;
const plural = (n, word) => `${n.toLocaleString('en-US')} ${word}${n === 1 ? '' : 's'}`;

/** "1 annotation, no links", "420 annotations (12 traced outlines), 419 links"; '' when unknown. */
export function makeupText(summary) {
  const n = Number(summary?.n_nodes);
  if (!Number.isFinite(n)) return '';
  const curves = Number(summary.n_curve_annotations) || 0;
  const links = Number(summary.n_edges);
  let text = plural(n, 'annotation');
  if (curves) text += curves === n ? (n === 1 ? ' (a traced outline)' : ' (all traced outlines)')
    : ` (${plural(curves, 'traced outline')})`;
  if (Number.isFinite(links)) text += links ? `, ${plural(links, 'link')}` : ', no links';
  return text;
}

/** What went wrong, in a phrase that starts with the technique: "Voxel remesh orig built nothing". */
export function failurePhrase(failure, options) {
  const label = techniqueName(options);
  switch (failure?.kind) {
    case 'empty': return `${label} built nothing`;
    case 'too-fine':
      return `${label} would need ${failure.need} ${failure.measure}, over the server's limit of ${failure.limit}`;
    case 'memory': return `${label} ran out of memory on the server`;
    default: return `${label} failed${failure?.detail ? `: ${failure.detail}` : ''}`;
  }
}

/** A failure as a heading over its fixes: "Voxel remesh orig built nothing for c162392". */
export function failureHeadline(failure, options, name) {
  const label = techniqueName(options);
  switch (failure?.kind) {
    case 'empty': return `${label} built nothing for ${name}`;
    case 'too-fine': return `${label}: Voxel ${um(normalizeRender(options).pixel_size_um)} is too fine for ${name}`;
    case 'memory': return `${label} ran out of memory on the server for ${name}`;
    default: return `${label} failed for ${name}`;
  }
}

/** Why one cell failed, for a list that names it: "c162392 has 1 annotation, no links". */
export function failureReason(name, failure, summary) {
  if (failure?.kind === 'empty') {
    const makeup = makeupText(summary);
    return makeup ? `${name} has ${makeup}` : `${name} has nothing this technique draws`;
  }
  if (failure?.kind === 'too-fine') {
    return `${name} would need ${failure.need} ${failure.measure}; the limit is ${failure.limit}`;
  }
  return `${name}: ${failure?.detail || 'the server gave no reason'}`;
}

/**
 * What to say when nothing built a cell: its make-up, and for a cell with next
 * to nothing traced, that loading it again later is the way.
 */
export function exhaustedNote(name, failure, summary) {
  if (failure?.kind !== 'empty') return `no other settings built ${name} either`;
  const makeup = makeupText(summary);
  const few = Number(summary?.n_nodes) <= 5 || Number(summary?.n_edges) === 0;
  return `no technique could build a surface from ${name}${makeup ? ` (${makeup})` : ''}`
    + (few ? '; load it again (↻) once more of it is traced' : '');
}

// -- what to try instead -----------------------------------------------------------

/** The pathways that contour one whole-cell field (their Fine voxel and Face cap). */
const FIELD_PATHWAYS = new Set(FIELD_BY_KEY.voxel2_um.show.pathway);
const DISC_METHODS = new Set(['cylinder', 'curvemesh']);
/** Options that belong to the cell, not the technique: every candidate keeps the cell's. */
const CELL_KEYS = ['color_by_strata', 'clip_ipl', 'keep_above', 'clip_z']
  .filter((k) => k in DEFAULT_RENDER);
/** The finest Voxel size the ladder tries: the schema's default, for small structures. */
const FINE_PIXEL = DEFAULT_RENDER.pixel_size_um;

/** The outline raster a technique's defaults use on this cell (curvePixelSeed, else theirs). */
function seeded(options, summary) {
  const pixel = curvePixelSeed({ summary }, options);
  return pixel ? { ...options, pixel_size_um: pixel } : options;
}

/**
 * The settings to build instead of `options`, which failed for a cell with this
 * `summary` (its NeuronSummaryDTO) as `failure` says (failureOf), best first:
 * `[{ options, text, detail }]`, `text` naming what changes and `detail` why it
 * may build.  None is the same build as `options` or as one before it.
 *
 * 1. What mends this failure in this technique: the Voxel size the server says
 *    fits; a coarser one, or the defaults' grid, after running out of memory; a
 *    finer Voxel size when an outline method built nothing.
 * 2. The technique's own defaults (the ones saved in this browser, else the
 *    recommended).
 * 3. Voxel remesh, the app's default technique.
 * 4. Hybrid, for a cell with traced outlines.
 * 5. Closed curve, which draws every annotation, linked or not, then finer.
 *
 * Disc techniques draw nothing from annotations no link joins, so 2 and 3 are
 * left out for a disc technique on a cell with no links.
 */
export function fallbacks(options, summary, failure) {
  const asked = normalizeRender(options);
  const s = summary || {};
  const own = Object.fromEntries(CELL_KEYS.map((k) => [k, asked[k]]));
  const links = Number(s.n_edges);
  const tubes = !Number.isFinite(links) || links > 0;
  const outlines = Number(s.n_curve_annotations) > 0;
  const out = [];
  const add = (next, text, detail) => out.push({ options: normalizeRender({ ...next, ...own }), text, detail });

  // 1. This failure, in this technique.
  const curve = CURVE_METHODS.has(asked.method);
  if (failure?.kind === 'too-fine' && curve && failure.fits > asked.pixel_size_um && failure.fits <= 1) {
    add({ ...asked, pixel_size_um: failure.fits }, `Voxel ${um(failure.fits)}`,
      `the finest that fits this cell under the server's limit on ${failure.measure}`);
  }
  if (failure?.kind === 'memory') {
    if (curve && asked.pixel_size_um < 1) {
      const coarser = Math.min(1, Math.round(asked.pixel_size_um * 2 * 1000) / 1000);
      add({ ...asked, pixel_size_um: coarser }, `Voxel ${um(coarser)}`,
        'twice as coarse: the build ran out of memory on the server');
    } else if (DISC_METHODS.has(asked.method) && FIELD_PATHWAYS.has(asked.pathway)) {
      add({ ...asked, voxel2_um: DEFAULT_RENDER.voxel2_um, voxel2_faces: DEFAULT_RENDER.voxel2_faces },
        `Fine voxel auto, Face cap ${DEFAULT_RENDER.voxel2_faces.toLocaleString('en-US')}`,
        'the grid the defaults build: the build ran out of memory on the server');
    }
  }
  if (failure?.kind === 'empty' && curve && asked.pixel_size_um > FINE_PIXEL) {
    add({ ...asked, pixel_size_um: FINE_PIXEL }, `Voxel ${um(FINE_PIXEL)}`,
      'finer: what is there may be narrower than about two voxels');
  }

  // 2. The technique's own defaults.
  if (tubes || !DISC_METHODS.has(asked.method)) {
    const saved = !!loadTechDefaults()[techKey(asked.method, asked.pathway)];
    add(seeded(techniqueDefaults(asked.method, asked.pathway), s), `${techniqueName(asked)} defaults`,
      saved ? 'the defaults you saved for this technique' : "this technique's recommended settings");
  }
  // 3. The app's default technique.
  if (tubes) {
    add(presetOptions(PRESET.voxel2), PRESET.voxel2.label, "the app's default technique");
  }
  // 4. Discs and outlines in one surface.
  if (outlines) {
    add(seeded(presetOptions(PRESET.hybrid), s), PRESET.hybrid.label,
      'draws the traced outlines and the discs in one surface');
  }
  // 5. Every annotation as it is, linked or not.
  const closed = presetOptions(PRESET.closedcurve);
  const coarse = curvePixelFor(s);
  add({ ...closed, pixel_size_um: coarse }, `${PRESET.closedcurve.label}, Voxel ${um(coarse)}`,
    'draws every annotation, linked or not, as its own disc or outline in its section');
  add({ ...closed, pixel_size_um: FINE_PIXEL }, `${PRESET.closedcurve.label}, Voxel ${um(FINE_PIXEL)}`,
    'the same, finer: for annotations narrower than about two voxels');

  const seen = new Set([effectiveSignature(asked)]);
  return out.filter((c) => {
    const signature = effectiveSignature(c.options);
    if (seen.has(signature)) return false;
    seen.add(signature);
    return true;
  });
}

/** The options a candidate changes from `options`, for a fix's button. */
export function changedOptions(options, next) {
  const from = normalizeRender(options);
  const to = normalizeRender(next);
  return Object.fromEntries(Object.entries(to).filter(([k, v]) => from[k] !== v));
}

/**
 * The controls a failure points at: the technique for one that built nothing or
 * broke, the Voxel size for an outline raster too fine, the grid's size for a
 * field that ran out of memory.
 */
export function failureKeys(failure, options) {
  const o = normalizeRender(options);
  const curve = CURVE_METHODS.has(o.method);
  switch (failure?.kind) {
    case 'too-fine': return curve ? ['pixel_size_um'] : ['method', 'pathway'];
    case 'memory':
      if (curve) return ['pixel_size_um'];
      if (DISC_METHODS.has(o.method) && FIELD_PATHWAYS.has(o.pathway)) return ['voxel2_um', 'voxel2_faces'];
      return ['method', 'pathway'];
    case 'empty': return curve ? ['method', 'pathway', 'pixel_size_um'] : ['method', 'pathway'];
    default: return ['method', 'pathway'];
  }
}
