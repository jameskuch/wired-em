// The error inspector: what the drawer under Render compare's panes shows about HOW a
// render's error arises, built from the results of the error-map jobs
// (`DeviationResultDTO`: `surface`, `bias`, `z_profile`, `spots`, `annotations`, ...).
// One module, two halves.  The first is pure (no DOM; node tests it,
// tests/js/test_errorinspector.mjs): the models behind each card -- the direction bar,
// the histogram re-binned onto one axis, the profile along Z, the table of annotations
// with its sort and its minimum-area filter, the classes by radius, the worst places, and
// the improvement between two layers.  The second is `ErrorInspector`, the DOM around them
// (a bias bar, the histogram with a threshold handle you drag, a profile, lists and tables,
// inline SVG in the style of charts.js); a fake DOM tests it (listeners, sorting, what a
// click calls back with), a browser draws it (tools/check_inspector.py).
//
// Every number is a number the SERVER computed, weighted by area where it says so (the
// surface, the histogram, the quantiles: a region meshed ten times as finely counts the
// same): the client only lays them out, sorts them and re-bins them, so the drawer cannot
// disagree with the legend.  The classes by radius are the exception the design asked
// for: they are summed here from the annotation table, so the edges you type take effect at
// once, and the sums are the table's (`radiusClasses` returns them beside the totals the
// server sent, and a test holds them to each other).
//
// Rule 11 of this codebase: an ID always travels with its volume.  Every Location ID the
// drawer shows or hands to a callback comes with the layer's `volume` ("Inferior 39425").
//
// Signs.  Positive means the render claims more than the annotations support: the render is
// FATTER than an annotation where a rim point lies inside the body, and SHORT where it lies
// outside.  The surface itself has no sign: a disc is flat, so only the rims carry a side.
//
// Callbacks (all optional):
//   onThreshold({ value, percent, layerId } | null)   the highlight threshold: `value` in um, its
//                                    percentile in the focused layer; null when it is cleared
//   onFlyTo({ layerId, volume, annotationId, section, xyz, source })
//                                    a worst place or a table row was picked: fly the camera
//                                    to `xyz` (um, the mesh's frame); `source` is 'spot',
//                                    'annotation' or 'improvement'
//   onSection(section, { layerId, volume, annotationId, source, z })
//                                    jump the Section view to this section (a row's section, a
//                                    click on the profile along Z); `source` adds 'profile'
//   onFocus(layerId)                 another layer is the one the single-layer cards show
//   onEdges(edges)                   the class edges (um) changed

import { niceTicks } from './charts.js';
import {
  DIVERGING_NEGATIVE, DIVERGING_POSITIVE, HIST_BINS, divergingColorAt, errorColorAt, histogramModel, percentileOf,
  percentileValue,
} from './errorpaint.js';
import { fitZMap, sectionToZ, zToSection } from './slice.js';

const isNumber = (v) => typeof v === 'number' && Number.isFinite(v);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const finiteOr = (v, fallback = NaN) => (isNumber(v) ? v : fallback);

// -- numbers as text ---------------------------------------------------------------------

const GROUPED = new Intl.NumberFormat('en-US');

/** A length in um: three decimals under a tenth, two above, one from ten up. */
export function fmtUm(v) {
  if (!isNumber(v)) return '—';
  const a = Math.abs(v);
  return v.toFixed(a >= 9.995 ? 1 : a >= 0.0995 ? 2 : 3);
}

/** A signed length: always with its sign (a real minus), for gaps and differences. */
export function fmtSigned(v) {
  if (!isNumber(v)) return '—';
  return `${v < 0 ? '−' : '+'}${fmtUm(Math.abs(v))}`;
}

/** A share 0..1 as a percentage with the digits it needs: 0.003 %, 0.42 %, 12.5 %. */
export function fmtShare(share) {
  if (!isNumber(share)) return '—';
  const p = share * 100;
  return `${p.toFixed(Math.abs(p) >= 9.995 ? 1 : Math.abs(p) >= 0.0995 ? 2 : 3)} %`;
}

/** A whole number with thousands separators. */
export const fmtCount = (n) => (isNumber(n) ? GROUPED.format(n) : '—');

/** A Location ID with its volume: IDs are numbered per volume, so a bare number is the wrong cell in another one. */
export function locationLabel(volume, id) {
  return `${volume || '?'} ${id}`;
}

/** `rgb(r, g, b)` for a color in 0..1. */
const rgbCss = (rgb) => `rgb(${rgb.map((c) => Math.round(clamp(c, 0, 1) * 255)).join(',')})`;
export const FAT_COLOR = rgbCss(DIVERGING_POSITIVE);
export const SHORT_COLOR = rgbCss(DIVERGING_NEGATIVE);

// -- the direction bar -------------------------------------------------------------------

/**
 * What the direction bar says: the share of the tested rim points the render encloses
 * (`fat`: it claims more than that annotation supports) and the share it does not
 * (`short`), the mean distance of each kind, and the signed mean.  `known` is false
 * without a measurement (a result from a server or cache that predates it, or no rim
 * point was tested).
 */
export function biasModel(bias) {
  if (!bias || !(bias.n > 0) || !isNumber(bias.enclosed_share)) return { known: false };
  const fat = clamp(bias.enclosed_share, 0, 1);
  return {
    known: true,
    n: bias.n,
    fat,
    short: 1 - fat,
    fatMean: isNumber(bias.enclosed_mean_um) ? bias.enclosed_mean_um : null,
    shortMean: isNumber(bias.outside_mean_um) ? bias.outside_mean_um : null,
    signed: finiteOr(bias.signed_mean_um, 0),
  };
}

// -- the histogram, the threshold ------------------------------------------------------------

/** The edges of one axis for several layers' histograms: `bins` equal bins from 0 to the farthest reach of any. */
export function commonEdges(results, bins = HIST_BINS) {
  let reach = 0;
  for (const r of results) {
    const e = r?.surface?.hist?.edges;
    if (e && e.length > 1 && isNumber(e[e.length - 1])) reach = Math.max(reach, e[e.length - 1]);
  }
  if (!(reach > 0)) return [];
  return Array.from({ length: bins + 1 }, (_, k) => (k === bins ? reach : (reach * k) / bins));
}

/**
 * A layer's histogram laid on other edges: each bin's share of the area is spread over
 * the new bins it overlaps in proportion to the overlap (uniformly inside a bin).  The
 * total is kept wherever the new edges cover the old, so layers measured to different
 * scales can share one axis; the part beyond the new last edge is the layer's tail: it is
 * not in the bins (the Distribution card says how much of it there is, from the layer's
 * quantile table).
 */
export function rebinHistogram(hist, edges) {
  const out = new Array(Math.max(0, edges.length - 1)).fill(0);
  const src = hist?.edges || [];
  const shares = hist?.area_share || [];
  for (let k = 0; k < shares.length; k += 1) {
    const a = src[k];
    const b = src[k + 1];
    const width = b - a;
    if (!(width > 0) || !isNumber(shares[k])) continue;
    for (let j = 0; j < out.length; j += 1) {
      const lo = Math.max(a, edges[j]);
      const hi = Math.min(b, edges[j + 1]);
      if (hi > lo) out[j] += (shares[k] * (hi - lo)) / width;
    }
  }
  return out;
}

/**
 * Where a threshold (um) sits in one layer: its percentile in the area-weighted quantile
 * table (`surface.quantiles`) and the shares of the surface below it and at or above it.
 * Null when the layer has no table.
 */
export function thresholdReadout(result, value) {
  const table = result?.surface?.quantiles;
  if (!table || table.length < 2 || !isNumber(value)) return null;
  const percent = percentileOf(table, value);
  return { value, percent, below: percent / 100, above: 1 - percent / 100 };
}

/** The threshold (um) that leaves `percent` % of a layer's surface below it. */
export function thresholdAtPercent(result, percent) {
  return percentileValue(result?.surface?.quantiles, clamp(percent, 0, 100));
}

// -- the profile along Z --------------------------------------------------------------------

export const Z_METRICS = Object.freeze(['mean', 'p95', 'rim']);
export const Z_METRIC_LABELS = Object.freeze({ mean: 'mean', p95: '95th percentile', rim: 'rim distance' });

/**
 * The error along the cell for one metric, one point per bin of `z_profile`: `mean` and
 * `p95` of the surface error (a bin with no surface has none), or `rim` (the mean
 * distance of the annotation rims there; a bin with no annotation has none).  Each point
 * is `{ i, z0, z1, zc, value, area, n, mean, p95, rim }` with the bin's Z range and center
 * (um) and everything the tooltip shows; `null` without a profile.
 */
export function zProfileSeries(result, metric = 'mean') {
  const zp = result?.z_profile;
  if (!zp || !zp.mean || !zp.mean.length) return null;
  const points = [];
  let max = 0;
  for (let i = 0; i < zp.mean.length; i += 1) {
    const area = zp.area?.[i] ?? 0;
    const n = zp.n_annotations?.[i] ?? 0;
    let value = null;
    if (metric === 'mean' && area > 0) value = zp.mean[i];
    else if (metric === 'p95' && area > 0) value = zp.p95?.[i];
    else if (metric === 'rim' && n > 0) value = zp.rim_mean?.[i];
    if (!isNumber(value)) continue;
    const z0 = zp.z0 + i * zp.step;
    points.push({
      i, z0, z1: z0 + zp.step, zc: z0 + zp.step / 2, value, area, n,
      mean: zp.mean[i], p95: zp.p95?.[i], rim: zp.rim_mean?.[i],
    });
    max = Math.max(max, value);
  }
  return { metric, step: zp.step, z0: zp.z0, points, max };
}

// -- the annotations -------------------------------------------------------------------------

export const DEFAULT_EDGES = Object.freeze([0.25, 0.5, 1.5]);
/** Annotations that own less of the surface than this (percent) are not ranked by their mean error. */
export const DEFAULT_MIN_AREA_PCT = 0.02;
export const DEFAULT_SORT = Object.freeze({ key: 'surfaceMean', dir: 'desc' });

/**
 * The table of annotations as rows: `annotations` is columnar in the order the cell's
 * annotations are measured (the discs, then the outlines), `null` standing for NaN.  A
 * row has `label` (the Location ID with `volume`), `section`, `kind`, `radius`, the
 * annotation's `areaShare` of the surface and the error over it (`surfaceMean`,
 * `surfaceMax`), the distance of its rim to the surface (`rimMean`, `rimMax`), `fatShare`
 * (the share of its tested rim points the render encloses), `xyz` (um) and `excess` (um^3: the
 * area it owns times the error over it).
 */
export function annotationRows(result, volume = '') {
  const a = result?.annotations;
  if (!a || !a.ids || !a.ids.length) return [];
  const area = result.surface?.area_um2;
  const rows = [];
  for (let i = 0; i < a.ids.length; i += 1) {
    const areaShare = finiteOr(a.area_share?.[i], 0);
    const surfaceMean = finiteOr(a.surface_mean?.[i]);
    rows.push({
      index: i,
      id: a.ids[i],
      label: locationLabel(volume, a.ids[i]),
      volume,
      section: a.sections?.[i],
      kind: a.kind?.[i] === 1 ? 'outline' : 'disc',
      radius: finiteOr(a.radius?.[i]),
      areaShare,
      surfaceMean,
      surfaceMax: finiteOr(a.surface_max?.[i]),
      rimMean: finiteOr(a.rim_mean?.[i]),
      rimMax: finiteOr(a.rim_max?.[i]),
      fatShare: finiteOr(a.rim_enclosed_share?.[i]),
      xyz: a.xyz && a.xyz.length >= 3 * i + 3 ? [a.xyz[3 * i], a.xyz[3 * i + 1], a.xyz[3 * i + 2]] : null,
      excess: isNumber(area) && isNumber(surfaceMean) ? area * areaShare * surfaceMean : NaN,
    });
  }
  return rows;
}

/** The columns of the annotation table: key, header, and the direction a first click sorts in (worst first for errors). */
export const COLUMNS = Object.freeze([
  { key: 'label', title: 'Location', first: 'asc', help: 'The annotation\'s Location ID with its volume (IDs are numbered per volume).' },
  { key: 'section', title: 'Section', first: 'asc', help: 'Its section number. Click a number to show that section in the Section view.' },
  { key: 'radius', title: 'r µm', first: 'desc', help: 'Its radius (a traced outline: the radius of the disc of equal area).' },
  { key: 'areaShare', title: 'Area', first: 'desc', help: 'The share of the render\'s surface whose nearest annotation this is.' },
  { key: 'surfaceMean', title: 'Mean µm', first: 'desc', help: 'The mean distance from that part of the surface to this annotation.' },
  { key: 'surfaceMax', title: 'Max µm', first: 'desc', help: 'The largest distance from that part of the surface to this annotation.' },
  { key: 'rimMean', title: 'Rim µm', first: 'desc', help: 'The mean distance from the annotation\'s rim to the render\'s surface.' },
  { key: 'fatShare', title: 'Fat', first: 'desc', help: 'The share of its rim points the render encloses: the render is fatter than the annotation there.' },
]);

/** `rows` sorted by `key` (a copy); a value that is not a number comes last whichever way, ties keep the table's order. */
export function sortRows(rows, key = DEFAULT_SORT.key, dir = DEFAULT_SORT.dir) {
  const sign = dir === 'asc' ? 1 : -1;
  const text = key === 'label' || key === 'kind';
  return rows.slice().sort((p, q) => {
    const a = p[key];
    const b = q[key];
    const missingA = text ? a === undefined : !isNumber(a);
    const missingB = text ? b === undefined : !isNumber(b);
    if (missingA || missingB) return missingA === missingB ? p.index - q.index : (missingA ? 1 : -1);
    if (text) {
      // IDs in numeric order, not as text: "9" before "10".
      const byId = key === 'label' ? p.id - q.id : String(a).localeCompare(String(b));
      return byId ? sign * byId : p.index - q.index;
    }
    return a === b ? p.index - q.index : sign * (a - b);
  });
}

/** The rows that own at least `minShare` of the surface (a share, 0..1). */
export function filterMinArea(rows, minShare) {
  return minShare > 0 ? rows.filter((r) => r.areaShare >= minShare) : rows.slice();
}

/** The most class edges the edge box takes. */
export const MAX_EDGES = 8;

/**
 * The edges typed in the edge box: numbers (um) separated by commas, semicolons or spaces,
 * positive, each once, at most `MAX_EDGES`, put in order.  `{ ok, edges, message }`.
 */
export function parseEdges(text) {
  const parts = String(text ?? '').split(/[\s,;]+/).filter(Boolean);
  if (!parts.length) return { ok: false, edges: [], message: 'Give at least one radius, like 0.25, 0.5, 1.5.' };
  const numbers = parts.map(Number);
  const bad = parts.find((_, k) => !isNumber(numbers[k]) || numbers[k] <= 0);
  if (bad !== undefined) return { ok: false, edges: [], message: `"${bad}" is not a positive radius in µm.` };
  const edges = [...new Set(numbers)].sort((a, b) => a - b);
  if (edges.length > MAX_EDGES) return { ok: false, edges: [], message: `At most ${MAX_EDGES} radii.` };
  return { ok: true, edges, message: '' };
}

/** The label of class `k` of `edges` (radii in um, the unit being the table's heading): r < a, a to b, r >= z. */
export function classLabel(edges, k) {
  const f = (v) => String(Number(v.toPrecision(4)));
  if (k === 0) return `r < ${f(edges[0])}`;
  if (k === edges.length) return `r ≥ ${f(edges[k - 1])}`;
  return `${f(edges[k - 1])} to ${f(edges[k])}`;
}

/**
 * The annotations grouped by radius at `edges` (um; class k holds `edges[k-1] <= r <
 * edges[k]`, the first from 0 and the last open): per class the number of annotations, the
 * `areaShare` of the surface they own, the mean error over that surface (`meanErr`, area
 * weighted), its largest (`maxErr`), the class's share of the render's excess volume
 * (`excessShare`) and `excess` itself (um^3, when `surface.area_um2` is known).  `totals`
 * sums them: its `areaShare` is 1 (to the 6 decimals the shares are sent with), its
 * `meanErr` the surface's mean error and its `excess` the surface's `excess_um3`.
 */
export function radiusClasses(rows, edges, surface = null) {
  const n = edges.length + 1;
  const classes = Array.from({ length: n }, (_, k) => ({
    k, label: classLabel(edges, k), from: k ? edges[k - 1] : 0, to: k < edges.length ? edges[k] : Infinity,
    n: 0, areaShare: 0, weighted: 0, maxErr: 0, meanErr: 0, excessShare: 0, excess: NaN,
  }));
  for (const r of rows) {
    if (!isNumber(r.radius)) continue;
    let k = 0;
    while (k < edges.length && r.radius >= edges[k]) k += 1;
    const c = classes[k];
    c.n += 1;
    c.areaShare += r.areaShare;
    if (isNumber(r.surfaceMean)) c.weighted += r.areaShare * r.surfaceMean;
    if (isNumber(r.surfaceMax) && r.areaShare > 0) c.maxErr = Math.max(c.maxErr, r.surfaceMax);
  }
  const weighted = classes.reduce((s, c) => s + c.weighted, 0);
  const area = isNumber(surface?.area_um2) ? surface.area_um2 : NaN;
  for (const c of classes) {
    c.meanErr = c.areaShare > 0 ? c.weighted / c.areaShare : 0;
    c.excessShare = weighted > 0 ? c.weighted / weighted : 0;
    c.excess = area * c.weighted;
  }
  const areaShare = classes.reduce((s, c) => s + c.areaShare, 0);
  return {
    classes,
    totals: {
      n: classes.reduce((s, c) => s + c.n, 0),
      areaShare,
      meanErr: areaShare > 0 ? weighted / areaShare : 0,
      excess: area * weighted,
    },
  };
}

// -- the worst places -----------------------------------------------------------------------

/** How many places the "top N hold X %" header adds up. */
export const SPOTS_TOP = 10;

/**
 * The worst places as rows, largest excess volume first as the server sent them: `rank`,
 * `label` (the annotation that accounts for most of it, with the volume), `section`,
 * `excess` (um^3), `share` of all the excess, `mean` and `max` error (um), `xyz`.  Also
 * `topShare`, the share the first `top` hold together, and `topN`.
 */
export function spotSummary(result, volume = '', top = SPOTS_TOP) {
  const spots = result?.spots || [];
  const rows = spots.map((s, k) => ({
    rank: k + 1,
    annotationId: s.annotation_id ?? null,
    label: s.annotation_id === null || s.annotation_id === undefined ? '—' : locationLabel(volume, s.annotation_id),
    volume,
    section: s.section ?? null,
    excess: s.excess_um3,
    share: s.share,
    mean: s.mean,
    max: s.max,
    xyz: s.xyz,
  }));
  const topN = Math.min(top, rows.length);
  return { rows, topN, topShare: rows.slice(0, topN).reduce((s, r) => s + (isNumber(r.share) ? r.share : 0), 0) };
}

// -- improvement between two layers ----------------------------------------------------------

export const IMPROVE_METRICS = Object.freeze({
  surfaceMean: 'surface error',
  rimMean: 'rim distance',
});
/** Two annotations whose error differs by less than this (um, 10 nm) have not changed: below the sampling of either. */
export const DEFAULT_TOLERANCE_UM = 0.01;

/**
 * Which render is closer to each annotation.  The two row sets are the same cell's
 * annotations (each result is measured against the same list), joined by Location ID; for
 * each, `delta` is the error of `rowsB` minus that of `rowsA` in `metric`
 * (`surfaceMean`: the error over the surface it owns; `rimMean`: the distance of its rim),
 * so a negative `delta` is an annotation that B fits better.  Returns the rows
 * ascending by `delta` (most improved first), with `improved`, `worse` and `unchanged`
 * (counts, by `tolerance`), `missing` (annotations in A with no number in one of them),
 * `meanDelta` and the delta weighted by the area A's annotations own.
 */
export function improvement(rowsA, rowsB, { metric = 'surfaceMean', tolerance = DEFAULT_TOLERANCE_UM } = {}) {
  const byId = new Map(rowsB.map((r) => [r.id, r]));
  const rows = [];
  let missing = 0;
  let sum = 0;
  let weighted = 0;
  let weight = 0;
  for (const a of rowsA) {
    const b = byId.get(a.id);
    if (!b || !isNumber(a[metric]) || !isNumber(b[metric])) { missing += 1; continue; }
    const delta = b[metric] - a[metric];
    rows.push({ id: a.id, label: a.label, volume: a.volume, section: a.section, radius: a.radius, a: a[metric], b: b[metric], delta, xyz: a.xyz || b.xyz, areaShare: a.areaShare });
    sum += delta;
    weighted += a.areaShare * delta;
    weight += a.areaShare;
  }
  rows.sort((p, q) => p.delta - q.delta);
  const improved = rows.filter((r) => r.delta < -tolerance).length;
  const worse = rows.filter((r) => r.delta > tolerance).length;
  return {
    rows, improved, worse, unchanged: rows.length - improved - worse, missing,
    meanDelta: rows.length ? sum / rows.length : 0,
    weightedDelta: weight > 0 ? weighted / weight : 0,
  };
}

/**
 * The colors a ring of each annotation can take, as `Map(Location ID -> [r, g, b])`, for
 * `Renderer3D.setDiscs(key, nodes, { ringColors, outlineColors })` (the same Map for both: a
 * traced outline is colored through `outlineColors`, by its `location_id`): `mode` 'rim' paints
 * the distance of the annotation's rim to the surface on the error ramp, 'surface' the mean error
 * over the surface it owns, 'delta' the entries of `deltas` (a Map from ID to the difference of
 * two layers, `improvement`'s) on the signed ramp (blue: B fits it better, red: worse).  An
 * annotation with no number is left out, so it keeps the layer's own color.  `scaleUm` is the
 * full-scale value.  The colors are the ramp's own numbers, in linear light as the renderer
 * takes an array (`errorColors` and `divergingColors` give `setMesh` and `setPoints` the same
 * ones): a CSS string would be read as sRGB, and a ring would not match the surface or the rim
 * points it stands for.
 */
export function ringColorMap(rows, { mode = 'rim', scaleUm = 1, deltas = null } = {}) {
  const out = new Map();
  if (mode === 'delta') {
    for (const [id, d] of deltas || []) if (isNumber(d)) out.set(id, divergingColorAt(d, scaleUm));
    return out;
  }
  const key = mode === 'surface' ? 'surfaceMean' : 'rimMean';
  for (const r of rows) {
    if (isNumber(r[key])) out.set(r.id, errorColorAt(r[key], scaleUm));
  }
  return out;
}

// -- the DOM ------------------------------------------------------------------------------------

const SVG_NS = 'http://www.w3.org/2000/svg';

/** CSS for the drawer; the host page adds it once (`installInspectorStyles`) or moves it to its stylesheet. */
export const INSPECTOR_CSS = `
.insp { container-type: inline-size; display: grid; gap: 10px; padding: 10px 12px 14px; color: var(--ink, #16191d); font-size: 12px; line-height: 1.4; min-width: 0; }
.insp * { box-sizing: border-box; }
.insp-layers { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.insp-layer { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 2px 8px; border: 1px solid var(--line, #dfe3e8); border-radius: 999px; background: var(--panel, #fff); color: inherit; font: inherit; cursor: pointer; }
.insp-layer[aria-pressed="true"] { border-color: var(--accent, #2f6fed); box-shadow: inset 0 0 0 1px var(--accent, #2f6fed); }
.insp-layer .dot, .insp-key .dot { flex: none; display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: var(--c, #888); }
.insp-key { display: inline-flex; align-items: center; gap: 4px; }
.insp-layer .vol { color: var(--muted, #6b7280); }
.insp-basis { color: var(--muted, #6b7280); margin-left: auto; font-family: var(--mono, monospace); font-size: 11px; }
.insp-grid { display: grid; gap: 10px; grid-template-columns: minmax(0, 1fr); align-items: start; }
@container (min-width: 1076px) {   /* the drawer's content: a drawer 1100 px wide, less its padding */
  .insp-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .insp-card.wide { grid-column: span 2; }
}
.insp-card { min-width: 0; border: 1px solid var(--line, #dfe3e8); border-radius: var(--radius, 8px); background: var(--panel, #fff); padding: 8px 10px 10px; }
.insp-card > h3 { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin: 0 0 6px; font-size: 12px; font-weight: 600; cursor: help; }
.insp-card > h3 .aside { color: var(--muted, #6b7280); font-weight: 400; font-family: var(--mono, monospace); font-size: 11px; text-align: right; }
.insp-empty { color: var(--muted, #6b7280); font-style: italic; }
.insp-row { display: grid; grid-template-columns: 12px minmax(0, 1fr) auto; align-items: center; gap: 6px 8px; margin: 3px 0; }
.insp-row .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--c, #888); }
.insp-bar { display: flex; height: 14px; border-radius: 3px; overflow: hidden; background: var(--line, #dfe3e8); }
.insp-bar .short { background: ${SHORT_COLOR}; }
.insp-bar .fat { background: ${FAT_COLOR}; }
.insp-num { font-family: var(--mono, monospace); font-variant-numeric: tabular-nums; white-space: nowrap; }
.insp-note { font-family: var(--mono, monospace); font-variant-numeric: tabular-nums; margin: 4px 0 6px; overflow-wrap: anywhere; }
.insp-sub { grid-column: 2 / 4; color: var(--muted, #6b7280); font-size: 11px; }
.insp-chart { display: block; width: 100%; height: auto; touch-action: none; overflow: visible; }
.insp-chart text { fill: var(--muted, #6b7280); font-size: 10px; }
.insp-chart text.tail { paint-order: stroke; stroke: var(--panel, #fff); stroke-width: 3px; stroke-linejoin: round; }
.insp-chart .grip { cursor: ew-resize; }
.insp-chart .grip:focus-visible { outline: 2px solid var(--accent, #2f6fed); outline-offset: 2px; }
.insp-seg { display: inline-flex; border: 1px solid var(--line, #dfe3e8); border-radius: 6px; overflow: hidden; }
.insp-seg button { border: 0; background: transparent; color: inherit; font: inherit; padding: 1px 7px; cursor: pointer; }
.insp-seg button[aria-pressed="true"] { background: var(--accent, #2f6fed); color: var(--accent-ink, #fff); }
.insp-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin-bottom: 6px; }
.insp-tools label { display: inline-flex; align-items: center; gap: 4px; }
.insp-tools input[type="number"], .insp-tools input[type="text"], .insp-tools select { width: 70px; font: inherit; padding: 1px 4px; }
.insp-tools select { width: auto; max-width: 150px; }
.insp-tools input[type="text"] { width: 120px; }
.insp-tools input[aria-invalid="true"] { border-color: var(--danger, #c0392b); outline-color: var(--danger, #c0392b); }
.insp-tools .msg { color: var(--danger, #c0392b); }
.insp-scroll { overflow-x: auto; max-width: 100%; }
.insp-table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.insp-table th, .insp-table td { padding: 2px 6px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--line, #dfe3e8); }
.insp-table th:first-child, .insp-table td:first-child { text-align: left; }
.insp-table th button { border: 0; background: none; color: var(--muted, #6b7280); font: inherit; font-weight: 600; cursor: pointer; padding: 0; }
.insp-table th[aria-sort] button { color: var(--ink, #16191d); }
.insp-table tbody tr[data-fly] { cursor: pointer; }
.insp-table tbody tr[data-fly]:hover { background: color-mix(in srgb, var(--accent, #2f6fed) 10%, transparent); }
.insp-table td button.link { border: 0; background: none; color: var(--accent, #2f6fed); font: inherit; cursor: pointer; padding: 0; text-decoration: underline; }
.insp-more { margin-top: 6px; }
.insp-more button, .insp-tools button.clear, .insp-tools button.set { font: inherit; padding: 1px 8px; cursor: pointer; }
.insp-spots li { display: grid; grid-template-columns: 1.6em minmax(0, 1fr) auto; gap: 2px 8px; align-items: baseline; padding: 3px 4px; border-bottom: 1px solid var(--line, #dfe3e8); cursor: pointer; }
.insp-spots { list-style: none; margin: 0; padding: 0; }
.insp-spots li:hover { background: color-mix(in srgb, var(--accent, #2f6fed) 10%, transparent); }
.insp-spots li .rank { color: var(--muted, #6b7280); text-align: right; }
.insp-spots li .more { grid-column: 2 / 4; color: var(--muted, #6b7280); font-size: 11px; }
.insp-spots li button.link { border: 0; background: none; color: var(--accent, #2f6fed); font: inherit; cursor: pointer; padding: 0; text-decoration: underline; text-align: left; justify-self: start; }
`;

/** Add `INSPECTOR_CSS` to the document once. */
export function installInspectorStyles(doc = globalThis.document) {
  if (!doc || doc.getElementById?.('inspector-styles')) return;
  const style = doc.createElement('style');
  style.id = 'inspector-styles';
  style.textContent = INSPECTOR_CSS;
  (doc.head || doc.documentElement).append(style);
}

/**
 * The drawer's content, built into `host` from the error-map results of the layers.
 *
 *     const inspector = new ErrorInspector(host, { onThreshold, onFlyTo, onSection });
 *     inspector.setLayers([{ id: 'L1', name: 'Voxel remesh', color: '#ffd400',
 *                            volume: 'Inferior', result }]);
 *
 * A layer is `{ id, name, color, volume, result }`: `result` is its `DeviationResultDTO`
 * (a layer not measured yet has none and is listed as such), `volume` goes next to every
 * Location ID.  The cards that show one layer -- the histogram's bars, the worst places, the
 * annotation table, the classes -- follow the focused one (click its chip); the direction
 * bars, the profile along Z and the histogram's outlines show every measured layer
 * overlaid in its color.  `setThreshold`, `setSection` and `setFocus` change the drawer from
 * outside without calling back.
 */
export class ErrorInspector {
  constructor(host, callbacks = {}, options = {}) {
    this.host = host;
    this.callbacks = callbacks;
    this.doc = options.document || globalThis.document;
    this.layers = [];
    this.focusId = null;
    this.threshold = null;
    this.section = null;
    this.zMetric = 'mean';
    this.sort = { ...DEFAULT_SORT };
    this.minAreaPct = options.minAreaPct ?? DEFAULT_MIN_AREA_PCT;
    this.edges = [...(options.edges || DEFAULT_EDGES)];
    this.shown = options.rows ?? 25;
    this.visibleRows = this.shown;
    this.improve = { from: null, to: null, metric: 'surfaceMean', tolerance: DEFAULT_TOLERANCE_UM, worst: false };
    this.width = options.width || 360;
    this._chartW = 0;
    this._drag = null;
    this._hist = null;
    this._disposed = false;
    this._measureFrame = 0;
    // The charts are redrawn from a frame of their own, never inside the observer's callback (see _queueMeasure).
    this._schedule = options.schedule
      || (typeof globalThis.requestAnimationFrame === 'function'
        ? (fn) => globalThis.requestAnimationFrame(fn) : (fn) => setTimeout(fn, 0));

    this.root = this._h('div', 'insp');
    this.layersBar = this._h('div', 'insp-layers');
    this.grid = this._h('div', 'insp-grid');
    this.cards = {};
    const card = (name, title, help, extra = '') => {
      const section = this._h('section', `insp-card ${extra}`.trim());
      section.dataset.card = name;
      const head = this._h('h3');
      this._help(head, help);
      const text = this._h('span', '', title);
      const aside = this._h('span', 'aside');
      head.append(text, aside);
      const body = this._h('div', 'insp-body');
      section.append(head, body);
      this.cards[name] = { section, head, aside, body };
      this.grid.append(section);
    };
    card('bias', 'Direction', 'Which way the render is wrong, from the points along the annotations\' rims: where the render encloses a rim point it is fatter than the annotation there, where it does not it stops short. A disc is flat, so the surface itself has no side.');
    card('hist', 'Distribution', 'The error of the surface, by area (a region meshed ten times as finely counts the same). Drag the line, or click, to set a threshold that highlights only the worse part of the surface.');
    card('z', 'Along Z', 'The error along the cell, in 1 µm steps of Z. A step at a section is often a registration jump. Click to show that section in the Section view.');
    card('spots', 'Worst places', 'The places that carry the most excess volume (area times error), no two within 2 µm. Click one to fly there.');
    card('annotations', 'Annotations', 'Every annotation with the part of the surface nearest to it. Sorted by the mean error over that part; annotations that own very little of the surface are left out until you lower the minimum area.', 'wide');
    card('classes', 'By size', 'The annotations grouped by radius: where the error is, in somas, branches or fine neurites. Type other radii (µm) in the box.');
    card('improve', 'Improvement', 'Which of two layers fits each annotation better, by the error over the surface it owns or the distance of its rim.', 'wide');
    this.root.append(this.layersBar, this.grid);
    host.append(this.root);

    if (typeof globalThis.ResizeObserver === 'function' && options.observe !== false) {
      this._resizer = new globalThis.ResizeObserver(() => this._queueMeasure());
      this._resizer.observe(host);
    }
    this._buildControls();
    this.render();
  }

  // -- tiny DOM helpers ------------------------------------------------------------------

  _h(tag, className = '', text = undefined) {
    const el = this.doc.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  _s(name, attrs = {}, text = undefined) {
    const el = this.doc.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    if (text !== undefined) el.textContent = text;
    return el;
  }

  _help(el, text) {
    el.dataset.help = text;
    return el;
  }

  /** The layers that have a result. */
  get measured() { return this.layers.filter((l) => l.result); }

  /** The layer the single-layer cards follow. */
  get focus() {
    return this.measured.find((l) => l.id === this.focusId) || this.measured[0] || null;
  }

  // -- feeding it -----------------------------------------------------------------------------

  /** Set the layers (in order); a layer with no `result` is listed as not measured. */
  setLayers(layers) {
    this.layers = (layers || []).map((l) => ({
      id: l.id, name: l.name ?? String(l.id), color: l.color || '#ffd400', volume: l.volume || '', result: l.result || null,
    }));
    this.focusId = this.focus ? this.focus.id : null;
    const ids = this.measured.map((l) => l.id);
    if (!ids.includes(this.improve.from)) this.improve.from = ids[0] ?? null;
    if (!ids.includes(this.improve.to) || this.improve.to === this.improve.from) {
      this.improve.to = ids.find((i) => i !== this.improve.from) ?? null;
    }
    this.visibleRows = this.shown;
    this.render();
  }

  /** Make `id` the focused layer, without calling back. */
  setFocus(id) {
    if (!this.layers.some((l) => l.id === id && l.result)) return;
    this.focusId = id;
    this.visibleRows = this.shown;
    this.render();
  }

  /** Put the threshold (um) at `value`, or take it off with null, without calling back. */
  setThreshold(value) {
    this.threshold = isNumber(value) ? value : null;
    this._applyThreshold();
  }

  /** Mark the section the Section view shows on the profile along Z, or take the mark off with null. */
  setSection(section) {
    this.section = isNumber(section) ? section : null;
    this._renderZ();
  }

  // -- the whole drawer ---------------------------------------------------------------------

  render() {
    this._keepFocus(() => {
      this._renderLayers();
      this._renderBias();
      this._renderHist();
      this._renderZ();
      this._renderSpots();
      this._renderAnnotations();
      this._renderClasses();
      this._renderImprove();
    });
  }

  /** The width charts are drawn at: the card they sit in, clamped to what stays legible. */
  _chartWidth() {
    const w = Math.round(this.cards.hist.body.clientWidth || this.cards.z.body.clientWidth || 0);
    return clamp(w > 0 ? w : this.width, 240, 760);
  }

  /**
   * A size change is answered in the next frame, not in the observer's callback: the charts are
   * redrawn at the width of their card, which changes the height of the host the observer is
   * watching, and a change made inside the callback to what it observes is delivered in the same
   * frame -- the browser stops that and reports "ResizeObserver loop completed with undelivered
   * notifications" as a window error.  Several changes before the frame are one redraw.
   */
  _queueMeasure() {
    if (this._measureFrame || this._disposed) return;
    this._measureFrame = this._schedule(() => {
      this._measureFrame = 0;
      if (!this._disposed) this._measure();
    });
  }

  _measure() {
    const w = this._chartWidth();
    if (this._chartW && Math.abs(w - this._chartW) > 8) {
      this._keepFocus(() => {
        this._renderHist();
        this._renderZ();
      });
    }
  }

  /**
   * Run `rebuild`, which replaces controls, and give the keyboard back to the control of the same
   * `data-focus` in what it built: a control that has the keyboard and is replaced takes it with it
   * (focus falls to the page and the next Tab starts again at the top).  When there is no such
   * control any more (the last "Show more"), the first control of the card it was in takes it.
   */
  _keepFocus(rebuild) {
    const active = this.doc.activeElement;
    const id = active && this.root.contains?.(active) ? active.dataset?.focus : undefined;
    const home = id ? active.closest?.('[data-card]') : null;
    rebuild();
    if (!id) return;
    let again = this.root.querySelector?.(`[data-focus="${String(id).replace(/(["\\])/g, '\\$1')}"]`);
    if (!again || again.disabled) again = home?.querySelector?.('button, input, select');
    again?.focus?.();
  }

  _renderLayers() {
    const bar = this.layersBar;
    bar.replaceChildren();
    if (!this.layers.length) { bar.append(this._h('span', 'insp-empty', 'No layers to inspect.')); return; }
    for (const l of this.layers) {
      const chip = this._h('button', 'insp-layer');
      chip.type = 'button';
      chip.dataset.layer = String(l.id);
      chip.dataset.focus = `layer:${l.id}`;
      chip.setAttribute('aria-pressed', String(this.focus ? this.focus.id === l.id : false));
      chip.disabled = !l.result;
      const dot = this._h('span', 'dot');
      dot.style.background = l.color;
      const name = this._h('span', 'name', l.name);
      const state = l.result ? l.volume : (l.volume ? `${l.volume} · not measured` : 'not measured');
      chip.append(dot, name, this._h('span', 'vol', state));
      this._help(chip, l.result
        ? 'Show this layer in the cards that show one layer: the histogram\'s bars, the worst places, the annotations and the classes.'
        : 'This layer has not been measured against the annotations yet: press Compare.');
      chip.addEventListener('click', () => {
        if (!l.result || (this.focus && this.focus.id === l.id)) return;
        this.focusId = l.id;
        this.visibleRows = this.shown;
        this.render();
        this.callbacks.onFocus?.(l.id);
      });
      bar.append(chip);
    }
    const s = this.focus?.result?.surface;
    if (s) {
      const basis = this._h('span', 'insp-basis',
        `surface, by area: mean ${fmtUm(s.mean_um)} µm · ${fmtCount(Math.round(s.area_um2))} µm² · excess ${fmtCount(Math.round(s.excess_um3))} µm³`);
      this._help(basis, 'The focused layer\'s surface error, weighted by the area each vertex stands for (the legend\'s own figures count every vertex once and read lower or higher on an unevenly meshed surface). Excess volume is area times error summed over the surface.');
      bar.append(basis);
    }
  }

  _empty(card, text) {
    card.body.replaceChildren(this._h('div', 'insp-empty', text));
  }

  // -- the direction bars -----------------------------------------------------------------------

  _renderBias() {
    const card = this.cards.bias;
    const layers = this.measured;
    if (!layers.length) { this._empty(card, 'Nothing measured yet.'); return; }
    const rows = [];
    for (const l of layers) {
      const m = biasModel(l.result.bias);
      const row = this._h('div', 'insp-row');
      row.dataset.layer = String(l.id);
      const dot = this._h('span', 'dot');
      dot.style.background = l.color;
      if (!m.known) {
        row.append(dot, this._h('span', 'insp-empty', `${l.name}: rim sides not measured`), this._h('span'));
        rows.push(row);
        continue;
      }
      const bar = this._h('div', 'insp-bar');
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', `${l.name}: render fatter on ${fmtShare(m.fat)} of ${fmtCount(m.n)} rim points, short on ${fmtShare(m.short)}`);
      const shortPart = this._h('span', 'short');
      shortPart.style.width = `${m.short * 100}%`;
      const fatPart = this._h('span', 'fat');
      fatPart.style.width = `${m.fat * 100}%`;
      bar.append(shortPart, fatPart);
      this._help(bar, `${l.name}: of ${fmtCount(m.n)} points along the annotations' rims, the render encloses ${fmtShare(m.fat)} (it is fatter than the annotation there`
        + `${m.fatMean === null ? '' : `, by ${fmtUm(m.fatMean)} µm on average`}) and misses ${fmtShare(m.short)} (it stops short`
        + `${m.shortMean === null ? '' : `, by ${fmtUm(m.shortMean)} µm on average`}).`);
      const num = this._h('span', 'insp-num', `${fmtShare(m.short)} short · ${fmtShare(m.fat)} fat`);
      const sub = this._h('div', 'insp-sub', `${l.name}: mean ${fmtSigned(m.signed)} µm (positive: claims more than the annotations support)`);
      row.append(dot, bar, num, sub);
      rows.push(row);
    }
    card.body.replaceChildren(...rows);
  }

  // -- the histogram and its threshold ---------------------------------------------------------------

  _renderHist() {
    const card = this.cards.hist;
    const layers = this.measured.filter((l) => l.result.surface?.hist?.edges?.length > 1);
    const focus = this.focus;
    this._hist = null;
    // The chart is built again: the element that held the pointer is gone with the old one, and no
    // pointerup will reach the new ones, so a drag still on would follow the pointer's hovering.
    this._drag = null;
    if (!layers.length || !focus || !(focus.result.surface?.hist?.edges?.length > 1)) {
      this._empty(card, this.measured.length ? 'No histogram in this result.' : 'Nothing measured yet.');
      card.aside.textContent = '';
      return;
    }
    const W = this._chartWidth();
    this._chartW = W;
    const H = 160;
    const m = { top: 20, right: 10, bottom: 36, left: 38 };
    const pw = W - m.left - m.right;
    const ph = H - m.top - m.bottom;
    const edges = commonEdges(layers.map((l) => l.result));
    const xmax = edges[edges.length - 1];
    const shares = layers.map((l) => rebinHistogram(l.result.surface.hist, edges));
    const ymax = Math.max(1e-9, ...shares.flat());
    const yTicks = niceTicks(0, ymax * 100, 3).filter((t) => t > 0);
    const yTop = Math.max(ymax, (yTicks[yTicks.length - 1] || 0) / 100);
    const sx = (v) => m.left + (v / xmax) * pw;
    const sy = (v) => m.top + ph - (v / yTop) * ph;
    const svg = this._s('svg', { class: 'insp-chart', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'group', 'aria-label': 'Distribution of the error over the surface' });
    svg.style.touchAction = 'none';

    // The focused layer's bars; those below the threshold fade (applyThreshold).  Its bars, the marks on its
    // axis and its tail come from errorpaint's `histogramModel`, the one model of a plotted histogram (bars
    // against the tallest, the tail past the last edge, the floor, the percentile ticks and whether each lies
    // beyond the axis), laid on the axis the layers share.
    const fi = layers.findIndex((l) => l.id === focus.id);
    const own = focus.result.surface;
    // What the focused layer has beyond the shared axis: its quantile table says it; without one, whatever its
    // bins do not hold (its own last edge may stop short of the axis, and its histogram calls the rest "tail").
    const beyond = own.quantiles?.length > 1
      ? clamp(1 - percentileOf(own.quantiles, xmax) / 100, 0, 1)
      : clamp(1 - shares[fi].reduce((sum, v) => sum + v, 0), 0, 1);
    const model = histogramModel({ edges, area_share: shares[fi], tail_share: beyond },
      { floor: own.floor_um, quantiles: own.quantiles });
    const bars = [];
    model.bars.forEach((b) => {
      const x0 = m.left + b.x0 * pw;
      const x1 = m.left + b.x1 * pw;
      const bar = this._s('rect', {
        class: 'bar', x: x0 + 0.5, y: sy(b.share), width: Math.max(x1 - x0 - 1, 0.5), height: Math.max(m.top + ph - sy(b.share), 0),
        fill: focus.color, opacity: 0.85,
      });
      bar.append(this._s('title', {}, `${fmtUm(b.from)} to ${fmtUm(b.to)} µm: ${fmtShare(b.share)} of the surface`));
      svg.append(bar);
      bars.push({ el: bar, hi: b.to });
    });
    // The other layers as outlines on the same axis.
    layers.forEach((l, k) => {
      if (k === fi) return;
      let d = '';
      shares[k].forEach((share, j) => {
        d += `${j ? 'L' : 'M'}${sx(edges[j]).toFixed(1)},${sy(share).toFixed(1)}L${sx(edges[j + 1]).toFixed(1)},${sy(share).toFixed(1)}`;
      });
      svg.append(this._s('path', { class: 'outline', d, fill: 'none', stroke: l.color, 'stroke-width': 1.6, 'stroke-linejoin': 'round' }));
    });
    // Axes.
    svg.append(this._s('line', { x1: m.left, y1: m.top + ph, x2: m.left + pw, y2: m.top + ph, stroke: 'currentColor', opacity: 0.35 }));
    for (const v of niceTicks(0, xmax, 5)) {
      svg.append(this._s('line', { x1: sx(v), y1: m.top + ph, x2: sx(v), y2: m.top + ph + 4, stroke: 'currentColor', opacity: 0.35 }));
      svg.append(this._s('text', { x: sx(v), y: m.top + ph + 15, 'text-anchor': 'middle' }, String(Number(v.toPrecision(4)))));
    }
    svg.append(this._s('text', { x: m.left + pw, y: H - 2, 'text-anchor': 'end' }, 'distance to the annotations (µm)'));
    for (const v of yTicks) {
      svg.append(this._s('text', { x: m.left - 5, y: sy(v / 100) + 3, 'text-anchor': 'end' }, `${Number(v.toPrecision(3))}%`));
      svg.append(this._s('line', { x1: m.left, y1: sy(v / 100), x2: m.left + pw, y2: sy(v / 100), stroke: 'currentColor', opacity: 0.1 }));
    }
    // The sampling floor, the share past the end of the axis and the percentile marks of the focused layer.
    if (model.floor && !model.floor.beyond) {
      const x = m.left + model.floor.at * pw;
      const f = this._s('line', { class: 'floor', x1: x, y1: m.top, x2: x, y2: m.top + ph, stroke: 'currentColor', opacity: 0.5, 'stroke-dasharray': '3 3' });
      f.append(this._s('title', {}, `Sampling floor ${fmtUm(model.floor.value)} µm: even a perfect render shows about this much error between sections (half the median gap between annotated sections).`));
      svg.append(f);
    }
    if (model.tail && model.tail.share >= 0.0005) {
      const tail = this._s('text', { class: 'tail', x: m.left + pw - 2, y: m.top + 9, 'text-anchor': 'end' }, `${fmtShare(model.tail.share)} beyond ${fmtUm(xmax)} ›`);
      tail.append(this._s('title', {}, `${fmtShare(model.tail.share)} of ${focus.name}'s surface is farther than ${fmtUm(xmax)} µm from an annotation, past the end of this axis (which reaches as far as the farthest layer's does).`));
      svg.append(tail);
    }
    const ticks = model.ticks.filter((t) => t.value > 0);
    for (const t of ticks.filter((x) => !x.beyond)) {
      const x = m.left + t.at * pw;
      svg.append(this._s('path', { class: 'tick', d: `M${x - 3},${m.top - 12}L${x + 3},${m.top - 12}L${x},${m.top - 6}Z`, fill: focus.color }));
      const label = this._s('text', { x, y: m.top - 14, 'text-anchor': 'middle' }, `p${t.percent}`);
      label.append(this._s('title', {}, `${t.percent}% of the surface lies within ${fmtUm(t.value)} µm of an annotation`));
      svg.append(label);
    }
    // Marks past the axis are one mark at its end, naming each, and not dropped: p98 is the legend's own scale.
    const off = ticks.filter((t) => t.beyond);
    if (off.length) {
      const x = m.left + pw;
      svg.append(this._s('path', { class: 'tick beyond', d: `M${x - 6},${m.top - 12}L${x},${m.top - 9}L${x - 6},${m.top - 6}Z`, fill: focus.color }));
      const label = this._s('text', { class: 'beyond', x: x - 8, y: m.top - 14, 'text-anchor': 'end' }, `${off.map((t) => `p${t.percent}`).join(' ')} ›`);
      label.append(this._s('title', {}, `${off.map((t) => `${t.percent}% of the surface lies within ${fmtUm(t.value)} µm`).join('; ')} of an annotation: past the end of this axis (${fmtUm(xmax)} µm).`));
      svg.append(label);
    }
    // The threshold: a shade over what it keeps, a line, and (above the pointer area) its grip.
    const shade = this._s('rect', { class: 'shade', x: m.left, y: m.top, width: 0, height: ph, fill: 'currentColor', opacity: 0.06, visibility: 'hidden' });
    const line = this._s('line', { class: 'threshold', x1: 0, y1: m.top - 4, x2: 0, y2: m.top + ph, stroke: 'currentColor', 'stroke-width': 1.5, visibility: 'hidden' });
    svg.append(shade, line);
    const hit = this._s('rect', { class: 'hit', x: m.left, y: m.top - 12, width: pw, height: ph + 12, fill: 'transparent' });
    hit.style.cursor = 'ew-resize';
    this._help(hit, 'Click or drag to set a threshold: only the surface at or above it is painted in the ramp, the rest in gray. Double-click, or Escape, takes it off.');
    svg.append(hit);
    const grip = this._s('rect', {
      class: 'grip', x: 0, y: m.top - 10, width: 10, height: 16, rx: 3, fill: 'currentColor', visibility: 'hidden',
      tabindex: 0, role: 'slider', 'aria-label': 'Threshold', 'aria-valuemin': 0, 'aria-valuemax': Number(xmax.toFixed(4)),
    });
    this._help(grip, 'Drag to move the threshold, or use the arrow keys (one percent of the area a step, Shift: five). Escape or double-click takes it off.');
    svg.append(grip);

    const valueAt = (event) => {
      const rect = svg.getBoundingClientRect();
      const scale = rect.width > 0 ? W / rect.width : 1;
      return clamp(((((event.clientX - rect.left) * scale) - m.left) / pw) * xmax, 0, xmax);
    };
    const bindDrag = (el) => {
      el.addEventListener('pointerdown', (e) => {
        if (e.button !== undefined && e.button !== 0) return;
        el.setPointerCapture?.(e.pointerId);
        this._drag = { id: e.pointerId };
        this._moveThreshold(valueAt(e));
      });
      el.addEventListener('pointermove', (e) => {
        if (!this._drag) return;
        // No button is down: the release went where nobody heard it.  Hovering is not dragging.
        if (e.buttons === 0) { this._drag = null; return; }
        this._moveThreshold(valueAt(e));
      });
      const end = (e) => {
        if (!this._drag) return false;
        el.releasePointerCapture?.(e.pointerId);
        this._drag = null;
        return true;
      };
      // A click on the chart leaves the keyboard on the grip it has just made visible (a press on a rect
      // that cannot take focus leaves it on the page, and Escape, which the hit area's help promises,
      // is the grip's).  After the release, when the browser's own focus handling for the press is over.
      el.addEventListener('pointerup', (e) => { if (end(e) && el === hit && this.threshold !== null) grip.focus?.(); });
      el.addEventListener('pointercancel', end);
      el.addEventListener('lostpointercapture', () => { this._drag = null; });
      el.addEventListener('dblclick', () => this.clearThreshold());
    };
    bindDrag(hit);
    bindDrag(grip);
    grip.addEventListener('keydown', (e) => this._gripKey(e));

    const readout = this._h('div', 'insp-tools');
    readout.dataset.role = 'threshold';
    card.aside.textContent = `${focus.name} · ${fmtCount(Math.round(focus.result.surface.area_um2))} µm²`;
    card.body.replaceChildren(svg, readout);
    this._hist = { svg, sx, xmax, bars, shade, line, grip, readout, layers, pw, left: m.left };
    this._applyThreshold();
  }

  /** Bring the threshold's line, grip, shade, faded bars and readout to `this.threshold`, without redrawing the chart (a drag in progress holds the pointer on it). */
  _applyThreshold() {
    const h = this._hist;
    if (!h) return;
    const t = this.threshold;
    // A threshold beyond the end of the axis (the keyboard's End goes to the farthest vertex, and the
    // axis may stop short of it) keeps its grip, pinned at the axis's end: a hidden grip would drop the keyboard.
    const on = t !== null;
    const show = on ? 'visible' : 'hidden';
    for (const el of [h.shade, h.line, h.grip]) el.setAttribute('visibility', show);
    if (on) {
      const x = h.sx(Math.min(t, h.xmax));
      h.shade.setAttribute('x', x);
      h.shade.setAttribute('width', Math.max(h.left + h.pw - x, 0));
      h.line.setAttribute('x1', x);
      h.line.setAttribute('x2', x);
      h.grip.setAttribute('x', x - 5);
      h.grip.setAttribute('aria-valuenow', Number(t.toFixed(4)));
      h.grip.setAttribute('aria-valuetext', this._thresholdText(t));
    }
    for (const b of h.bars) b.el.setAttribute('opacity', t === null || b.hi > t ? 0.85 : 0.28);

    const nodes = [this._h('span', 'insp-num', t === null ? 'Threshold: off' : `Threshold ${fmtUm(t)} µm`)];
    if (t === null && this.focus?.result?.surface?.quantiles?.length > 1) {
      // The way in for a keyboard: the grip is not there to be reached while there is no threshold.
      const set = this._h('button', 'set', 'Set at 90 %');
      set.type = 'button';
      set.dataset.role = 'set-threshold';
      set.dataset.focus = 'threshold-toggle';
      this._help(set, 'Put the threshold at the 90th percentile of this layer\'s surface, so that only the worst tenth is painted '
        + 'in the ramp, and move to its grip: the arrow keys take it from there. (With the mouse, a press on the chart sets it.)');
      set.addEventListener('click', () => {
        this._moveThreshold(thresholdAtPercent(this.focus.result, 90));
        this._hist?.grip.focus?.();
      });
      nodes.push(set);
    }
    if (t !== null) {
      const clear = this._h('button', 'clear', 'Clear');
      clear.type = 'button';
      clear.dataset.role = 'clear';
      clear.dataset.focus = 'threshold-toggle';
      this._help(clear, 'Take the threshold off: the surface is painted on the whole ramp again.');
      clear.addEventListener('click', () => this.clearThreshold());
      nodes.push(clear);
      for (const l of h.layers) {
        const r = thresholdReadout(l.result, t);
        if (!r) continue;
        const item = this._h('span', 'insp-key insp-num');
        item.dataset.layer = String(l.id);
        const dot = this._h('span', 'dot');
        dot.style.background = l.color;
        item.append(dot, this.doc.createTextNode(` ${fmtShare(r.below)} below · ${fmtShare(r.above)} at or above`));
        nodes.push(item);
      }
    }
    this._keepFocus(() => h.readout.replaceChildren(...nodes));
  }

  _thresholdText(value) {
    const r = thresholdReadout(this.focus?.result, value);
    return r ? `${fmtUm(value)} micrometers, the ${r.percent.toFixed(1)}th percentile` : `${fmtUm(value)} micrometers`;
  }

  _moveThreshold(value) {
    const v = Number(clamp(value, 0, Infinity).toFixed(4));
    if (v === this.threshold) return;
    this.threshold = v;
    this._applyThreshold();
    this._emitThreshold();
  }

  _emitThreshold() {
    if (this.threshold === null) { this.callbacks.onThreshold?.(null); return; }
    const r = thresholdReadout(this.focus?.result, this.threshold);
    this.callbacks.onThreshold?.({ value: this.threshold, percent: r ? r.percent : null, layerId: this.focus ? this.focus.id : null });
  }

  /** Take the threshold off, calling back with null. */
  clearThreshold() {
    if (this.threshold === null) return;
    this.threshold = null;
    this._applyThreshold();
    this._emitThreshold();
  }

  _gripKey(event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const focus = this.focus;
    if (!focus || this.threshold === null) return;
    const q = focus.result.surface.quantiles;
    const step = event.shiftKey ? 5 : 1;
    const percent = q && q.length > 1 ? percentileOf(q, this.threshold) : null;
    let next = null;
    switch (event.key) {
      case 'ArrowRight': case 'ArrowUp': next = percent === null ? this.threshold * 1.05 : percentileValue(q, Math.min(100, percent + step)); break;
      case 'ArrowLeft': case 'ArrowDown': next = percent === null ? this.threshold / 1.05 : percentileValue(q, Math.max(0, percent - step)); break;
      case 'Home': next = 0; break;
      case 'End': next = percent === null ? this.threshold : percentileValue(q, 100); break;
      case 'Escape': case 'Delete': case 'Backspace': event.preventDefault(); this.clearThreshold(); return;
      default: return;
    }
    event.preventDefault();
    this._moveThreshold(next);
  }

  // -- the profile along Z --------------------------------------------------------------------------------

  _renderZ() {
    const card = this.cards.z;
    const layers = this.measured.map((l) => ({ l, series: zProfileSeries(l.result, this.zMetric) })).filter((x) => x.series);
    if (!layers.length) { this._empty(card, this.measured.length ? 'No profile in this result.' : 'Nothing measured yet.'); card.aside.textContent = ''; return; }
    const W = this._chartWidth();
    this._chartW = W;
    const H = 150;
    const m = { top: 8, right: 10, bottom: 26, left: 38 };
    const pw = W - m.left - m.right;
    const ph = H - m.top - m.bottom;
    const zLo = Math.min(...layers.map((x) => x.series.z0));
    const zHi = Math.max(...layers.map((x) => {
      const last = x.series.points.length ? x.series.points[x.series.points.length - 1].i + 1 : 0;
      return x.series.z0 + x.series.step * last;
    }));
    const vmax = Math.max(1e-9, ...layers.map((x) => x.series.max));
    const floor = this.focus?.result?.surface?.floor_um || 0;
    const yTicks = niceTicks(0, Math.max(vmax, floor), 4);
    const yTop = Math.max(vmax, yTicks[yTicks.length - 1] || 0);
    const sx = (z) => m.left + ((z - zLo) / Math.max(zHi - zLo, 1e-9)) * pw;
    const sy = (v) => m.top + ph - (v / yTop) * ph;
    const svg = this._s('svg', { class: 'insp-chart', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'group', 'aria-label': `Error along Z, ${Z_METRIC_LABELS[this.zMetric]}` });
    svg.append(this._s('line', { x1: m.left, y1: m.top + ph, x2: m.left + pw, y2: m.top + ph, stroke: 'currentColor', opacity: 0.35 }));
    for (const z of niceTicks(zLo, zHi, 5)) {
      svg.append(this._s('line', { x1: sx(z), y1: m.top + ph, x2: sx(z), y2: m.top + ph + 4, stroke: 'currentColor', opacity: 0.35 }));
      svg.append(this._s('text', { x: sx(z), y: m.top + ph + 15, 'text-anchor': 'middle' }, String(Number(z.toPrecision(5)))));
    }
    svg.append(this._s('text', { x: m.left + pw, y: H - 2, 'text-anchor': 'end' }, 'Z (µm)'));
    for (const v of yTicks) {
      svg.append(this._s('text', { x: m.left - 5, y: sy(v) + 3, 'text-anchor': 'end' }, String(Number(v.toPrecision(3)))));
      svg.append(this._s('line', { x1: m.left, y1: sy(v), x2: m.left + pw, y2: sy(v), stroke: 'currentColor', opacity: 0.1 }));
    }
    if (floor > 0 && floor <= yTop) {
      const line = this._s('line', { class: 'floor', x1: m.left, y1: sy(floor), x2: m.left + pw, y2: sy(floor), stroke: 'currentColor', opacity: 0.5, 'stroke-dasharray': '3 3' });
      line.append(this._s('title', {}, `Sampling floor ${fmtUm(floor)} µm: half the median gap between annotated sections`));
      svg.append(line);
    }
    // The section the Section view shows.
    const map = this._sectionMap();
    if (this.section !== null && map) {
      const z = sectionToZ(map, this.section);
      if (z >= zLo && z <= zHi) {
        svg.append(this._s('line', { class: 'cursor', x1: sx(z), y1: m.top, x2: sx(z), y2: m.top + ph, stroke: 'currentColor', 'stroke-width': 1, opacity: 0.6 }));
      }
    }
    for (const { l, series } of layers) {
      let d = '';
      let prev = -2;
      for (const p of series.points) {
        d += `${p.i === prev + 1 ? 'L' : 'M'}${sx(p.zc).toFixed(1)},${sy(p.value).toFixed(1)}`;
        prev = p.i;
      }
      svg.append(this._s('path', { class: 'series', d, fill: 'none', stroke: l.color, 'stroke-width': 1.6, 'stroke-linejoin': 'round' }));
      if (series.points.length < 60) {
        for (const p of series.points) svg.append(this._s('circle', { cx: sx(p.zc), cy: sy(p.value), r: 2, fill: l.color }));
      }
    }
    // One hit area per bin of the focused layer: a tooltip, and a click to show that section.
    const focusSeries = layers.find((x) => x.l.id === this.focus?.id) || layers[0];
    const binW = Math.max(sx(zLo + focusSeries.series.step) - sx(zLo), 2);
    for (const p of focusSeries.series.points) {
      const rect = this._s('rect', { class: 'bin', x: sx(p.z0), y: m.top, width: binW, height: ph, fill: 'transparent' });
      rect.dataset.z = String(p.zc);
      rect.style.cursor = 'pointer';
      rect.append(this._s('title', {}, `Z ${Number(p.z0.toPrecision(5))} to ${Number(p.z1.toPrecision(5))} µm: mean ${fmtUm(p.mean)}, 95th ${fmtUm(p.p95)}, rim ${fmtUm(p.rim)} µm; ${fmtCount(Math.round(p.area))} µm² of surface, ${fmtCount(p.n)} annotations`));
      rect.addEventListener('click', () => this._pickZ(p.zc, focusSeries.l));
      svg.append(rect);
    }
    const tools = this._h('div', 'insp-tools');
    const seg = this._h('div', 'insp-seg');
    seg.dataset.role = 'z-metric';
    const helps = {
      mean: 'The mean error of the surface in each step of Z.',
      p95: 'The 95th percentile of the error of the surface in each step of Z: the worst part of it.',
      rim: 'The mean distance of the annotations\' rims to the surface in each step of Z.',
    };
    for (const metric of Z_METRICS) {
      const b = this._h('button', '', { mean: 'mean', p95: '95th', rim: 'rim' }[metric]);
      b.type = 'button';
      b.dataset.metric = metric;
      b.dataset.focus = `z-metric:${metric}`;
      b.setAttribute('aria-pressed', String(metric === this.zMetric));
      this._help(b, helps[metric]);
      b.addEventListener('click', () => { this.zMetric = metric; this._renderZ(); });
      seg.append(b);
    }
    tools.append(seg);
    card.aside.textContent = `${Z_METRIC_LABELS[this.zMetric]} µm`;
    this._keepFocus(() => card.body.replaceChildren(tools, svg));
  }

  /** The focused layer's section-to-Z line, fitted from its annotations, or null. */
  _sectionMap() {
    const a = this.focus?.result?.annotations;
    if (!a || !a.sections || a.sections.length < 2 || !a.xyz || a.xyz.length < 3 * a.sections.length) return null;
    const map = fitZMap(a.sections, a.sections.map((_, i) => a.xyz[3 * i + 2]));
    return map.fitted ? map : null;
  }

  _pickZ(z, layer) {
    const map = this._sectionMap();
    if (!map) return;
    const section = Math.round(zToSection(map, z));
    this.callbacks.onSection?.(section, { layerId: layer.id, volume: layer.volume, annotationId: null, source: 'profile', z });
  }

  // -- the worst places --------------------------------------------------------------------------------------

  _renderSpots() {
    const card = this.cards.spots;
    const f = this.focus;
    card.aside.textContent = '';
    if (!f) { this._empty(card, 'Nothing measured yet.'); return; }
    const s = spotSummary(f.result, f.volume);
    if (!s.rows.length) { this._empty(card, 'No place stands out.'); return; }
    card.aside.textContent = `top ${s.topN} hold ${fmtShare(s.topShare)} of the excess`;
    const list = this._h('ol', 'insp-spots');
    for (const r of s.rows) {
      const item = this._h('li');
      item.dataset.spot = String(r.rank);
      this._help(item, `Fly to this place: ${fmtUm(r.excess)} µm³ of the render's excess volume within 2 µm of it, ${fmtUm(r.mean)} µm of error on average and ${fmtUm(r.max)} at worst.`);
      const fly = () => this.callbacks.onFlyTo?.({ layerId: f.id, volume: f.volume, annotationId: r.annotationId, section: r.section, xyz: r.xyz, source: 'spot' });
      const where = this._h('button', 'link', r.label);
      where.type = 'button';
      where.dataset.role = 'fly';
      where.addEventListener('click', (e) => { e.stopPropagation?.(); fly(); });
      const num = this._h('span', 'insp-num', `${fmtUm(r.excess)} µm³ · ${fmtShare(r.share)}`);
      const more = this._h('span', 'more');
      more.append(this.doc.createTextNode(`mean ${fmtUm(r.mean)}, max ${fmtUm(r.max)} µm`));
      if (r.section !== null) {
        const go = this._h('button', 'link', `section ${r.section}`);
        go.type = 'button';
        go.dataset.role = 'section';
        this._help(go, 'Show this section in the Section view.');
        go.addEventListener('click', (e) => {
          e.stopPropagation?.();
          this.callbacks.onSection?.(r.section, { layerId: f.id, volume: f.volume, annotationId: r.annotationId, source: 'spot' });
        });
        more.append(this.doc.createTextNode(' · '), go);
      }
      item.addEventListener('click', fly);
      item.append(this._h('span', 'rank', String(r.rank)), where, num, more);
      list.append(item);
    }
    card.body.replaceChildren(list);
  }

  // -- the controls that keep what is typed in them ---------------------------------------------------------------

  _buildControls() {
    const ann = this.cards.annotations;
    const tools = this._h('div', 'insp-tools');
    tools.dataset.role = 'annotation-tools';
    const minLabel = this._h('label');
    const min = this._h('input');
    min.type = 'number';
    min.min = '0';
    min.step = '0.01';
    min.value = String(this.minAreaPct);
    min.dataset.role = 'min-area';
    this._help(min, 'Leave out annotations that own less than this share of the surface, in percent: a handful of vertices can have a large mean error that is nothing to fix.');
    min.addEventListener('change', () => {
      const v = Number(min.value);
      this.minAreaPct = isNumber(v) && v >= 0 ? v : DEFAULT_MIN_AREA_PCT;
      min.value = String(this.minAreaPct);
      this.visibleRows = this.shown;
      this._renderAnnotations();
    });
    minLabel.append(this.doc.createTextNode('min area '), min, this.doc.createTextNode(' %'));
    this.annInfo = this._h('span', 'insp-num');
    tools.append(minLabel, this.annInfo);
    this.annTableHost = this._h('div', 'insp-scroll');
    this.annMore = this._h('div', 'insp-more');
    ann.body.append(tools, this.annTableHost, this.annMore);

    const cls = this.cards.classes;
    const ctools = this._h('div', 'insp-tools');
    ctools.dataset.role = 'class-tools';
    const edgeLabel = this._h('label');
    const box = this._h('input');
    box.type = 'text';
    box.value = this.edges.join(', ');
    box.dataset.role = 'edges';
    box.setAttribute('aria-label', 'Class edges in micrometers');
    this._help(box, 'Radii (µm) that divide the annotations into classes, separated by commas: 0.25, 0.5, 1.5 gives fine neurites, branches, thick branches and somas.');
    this.edgeMessage = this._h('span', 'msg');
    const commit = () => {
      const parsed = parseEdges(box.value);
      if (!parsed.ok) {
        box.setAttribute('aria-invalid', 'true');
        this.edgeMessage.textContent = parsed.message;
        return;
      }
      box.removeAttribute('aria-invalid');
      this.edgeMessage.textContent = '';
      box.value = parsed.edges.join(', ');
      if (parsed.edges.join() === this.edges.join()) return;
      this.edges = parsed.edges;
      this._renderClasses();
      this.callbacks.onEdges?.([...this.edges]);
    };
    box.addEventListener('change', commit);
    box.addEventListener('keydown', (e) => { if (e.key === 'Enter') commit(); });
    edgeLabel.append(this.doc.createTextNode('radii µm '), box);
    ctools.append(edgeLabel, this.edgeMessage);
    this.classTableHost = this._h('div', 'insp-scroll');
    cls.body.append(ctools, this.classTableHost);

    const imp = this.cards.improve;
    this.improveTools = this._h('div', 'insp-tools');
    this.improveTools.dataset.role = 'improve-tools';
    this.improveBody = this._h('div');
    imp.body.append(this.improveTools, this.improveBody);
  }

  // -- the table of annotations --------------------------------------------------------------------------------

  _renderAnnotations() {
    const f = this.focus;
    const host = this.annTableHost;
    this.cards.annotations.aside.textContent = f ? f.name : '';
    if (!f) { host.replaceChildren(this._h('div', 'insp-empty', 'Nothing measured yet.')); this.annMore.replaceChildren(); this.annInfo.textContent = ''; return; }
    const all = annotationRows(f.result, f.volume);
    if (!all.length) {
      host.replaceChildren(this._h('div', 'insp-empty', 'No annotation table in this result.'));
      this.annMore.replaceChildren();
      this.annInfo.textContent = '';
      return;
    }
    const kept = filterMinArea(all, this.minAreaPct / 100);
    const sorted = sortRows(kept, this.sort.key, this.sort.dir);
    const shown = sorted.slice(0, this.visibleRows);
    this.annInfo.textContent = `${fmtCount(shown.length)} of ${fmtCount(kept.length)} (${fmtCount(all.length)} annotations)`;
    const table = this._h('table', 'insp-table');
    const head = this._h('thead');
    const hr = this._h('tr');
    for (const col of COLUMNS) {
      const th = this._h('th');
      th.dataset.key = col.key;
      if (this.sort.key === col.key) th.setAttribute('aria-sort', this.sort.dir === 'asc' ? 'ascending' : 'descending');
      const b = this._h('button', '', col.title);
      b.type = 'button';
      b.dataset.focus = `sort:${col.key}`;
      this._help(b, `${col.help} Click to sort.`);
      b.addEventListener('click', () => {
        this.sort = this.sort.key === col.key
          ? { key: col.key, dir: this.sort.dir === 'asc' ? 'desc' : 'asc' }
          : { key: col.key, dir: col.first };
        this.visibleRows = this.shown;
        this._renderAnnotations();
      });
      th.append(b);
      hr.append(th);
    }
    head.append(hr);
    const body = this._h('tbody');
    for (const r of shown) {
      const fly = () => this.callbacks.onFlyTo?.({ layerId: f.id, volume: f.volume, annotationId: r.id, section: r.section, xyz: r.xyz, source: 'annotation' });
      const tr = this._h('tr');
      tr.dataset.id = String(r.id);
      tr.dataset.fly = '1';
      const where = this._h('td');
      const flyButton = this._h('button', 'link', r.label);
      flyButton.type = 'button';
      flyButton.dataset.role = 'fly';
      this._help(flyButton, 'Fly to this annotation.');
      flyButton.addEventListener('click', (e) => { e.stopPropagation?.(); fly(); });
      where.append(flyButton);
      tr.append(where, this._sectionCell(r.section, f, r.id, 'annotation'), this._h('td', '', fmtUm(r.radius)),
        this._h('td', '', fmtShare(r.areaShare)), this._h('td', '', fmtUm(r.surfaceMean)), this._h('td', '', fmtUm(r.surfaceMax)),
        this._h('td', '', fmtUm(r.rimMean)), this._h('td', '', isNumber(r.fatShare) ? `${Math.round(r.fatShare * 100)} %` : '—'));
      tr.addEventListener('click', fly);
      body.append(tr);
    }
    table.append(head, body);
    let more = null;
    if (sorted.length > shown.length) {
      more = this._h('button', '', `Show ${Math.min(this.shown, sorted.length - shown.length)} more`);
      more.type = 'button';
      more.dataset.role = 'more';
      more.dataset.focus = 'more';
      more.addEventListener('click', () => { this.visibleRows += this.shown; this._renderAnnotations(); });
    }
    this._keepFocus(() => {
      host.replaceChildren(table);
      this.annMore.replaceChildren();
      if (more) this.annMore.append(more);
    });
  }

  /** A table cell with a section number that shows that section in the Section view. */
  _sectionCell(section, layer, annotationId, source) {
    const td = this._h('td');
    if (section === undefined || section === null) return td;
    const go = this._h('button', 'link', String(section));
    go.type = 'button';
    go.dataset.role = 'section';
    this._help(go, 'Show this section in the Section view.');
    go.addEventListener('click', (e) => {
      e.stopPropagation?.();
      this.callbacks.onSection?.(section, { layerId: layer.id, volume: layer.volume, annotationId, source });
    });
    td.append(go);
    return td;
  }

  // -- the classes by radius ---------------------------------------------------------------------------------------

  _renderClasses() {
    const f = this.focus;
    const host = this.classTableHost;
    this.cards.classes.aside.textContent = f ? f.name : '';
    if (!f) { host.replaceChildren(this._h('div', 'insp-empty', 'Nothing measured yet.')); return; }
    const rows = annotationRows(f.result, f.volume);
    if (!rows.length) { host.replaceChildren(this._h('div', 'insp-empty', 'No annotation table in this result.')); return; }
    const { classes, totals } = radiusClasses(rows, this.edges, f.result.surface);
    const table = this._h('table', 'insp-table');
    const head = this._h('thead');
    const hr = this._h('tr');
    for (const [title, help] of [
      ['Radius µm', 'Annotations of this radius.'], ['N', 'How many annotations.'],
      ['Area', 'The share of the surface whose nearest annotation is in this class.'],
      ['Mean', 'The mean error (µm) over that part of the surface.'],
      ['Excess', 'Its share of the render\'s excess volume (area times error).']]) {
      const th = this._h('th', '', title);
      this._help(th, help);
      hr.append(th);
    }
    head.append(hr);
    const body = this._h('tbody');
    for (const c of classes) {
      const tr = this._h('tr');
      tr.dataset.class = String(c.k);
      for (const text of [c.label, fmtCount(c.n), fmtShare(c.areaShare), c.n ? fmtUm(c.meanErr) : '—', fmtShare(c.excessShare)]) {
        tr.append(this._h('td', '', text));
      }
      body.append(tr);
    }
    const total = this._h('tr');
    total.dataset.class = 'total';
    for (const text of ['all', fmtCount(totals.n), fmtShare(totals.areaShare), fmtUm(totals.meanErr), totals.areaShare > 0 ? '100 %' : '—']) {
      total.append(this._h('td', '', text));
    }
    this._help(total, `${fmtCount(Math.round(totals.excess))} µm³ of excess volume in all.`);
    body.append(total);
    table.append(head, body);
    host.replaceChildren(table);
  }

  // -- improvement between two layers -----------------------------------------------------------------------------------

  _renderImprove() {
    this._keepFocus(() => this._buildImprove());
  }

  _buildImprove() {
    const tools = this.improveTools;
    const body = this.improveBody;
    const layers = this.measured;
    tools.replaceChildren();
    if (layers.length < 2) {
      this.cards.improve.section.hidden = true;
      body.replaceChildren();
      return;
    }
    this.cards.improve.section.hidden = false;
    const imp = this.improve;
    const select = (role, value, onChange, help) => {
      const s = this._h('select');
      s.dataset.role = role;
      s.dataset.focus = `improve:${role}`;
      this._help(s, help);
      for (const l of layers) {
        const o = this._h('option', '', l.name);
        o.value = String(l.id);
        s.append(o);
      }
      s.value = String(value);
      s.addEventListener('change', () => onChange(layers.find((l) => String(l.id) === s.value)?.id));
      return s;
    };
    const from = select('from', imp.from, (id) => { imp.from = id; this._renderImprove(); }, 'The layer the comparison starts from.');
    const to = select('to', imp.to, (id) => { imp.to = id; this._renderImprove(); }, 'The layer it is compared with: an annotation counts as improved where this one fits it better.');
    const metric = this._h('select');
    metric.dataset.role = 'metric';
    metric.dataset.focus = 'improve:metric';
    this._help(metric, 'What is compared for each annotation: the mean error over the surface it owns, or the distance of its rim to the surface.');
    for (const [value, text] of Object.entries(IMPROVE_METRICS)) {
      const o = this._h('option', '', text);
      o.value = value;
      metric.append(o);
    }
    metric.value = imp.metric;
    metric.addEventListener('change', () => { imp.metric = metric.value; this._renderImprove(); });
    const tol = this._h('input');
    tol.type = 'number';
    tol.min = '0';
    tol.step = '0.005';
    tol.value = String(imp.tolerance);
    tol.dataset.role = 'tolerance';
    tol.dataset.focus = 'improve:tolerance';
    this._help(tol, 'Annotations whose error differs by less than this (µm) count as unchanged. The default, 10 nm, is below the sampling of either render.');
    tol.addEventListener('change', () => {
      const v = Number(tol.value);
      imp.tolerance = isNumber(v) && v >= 0 ? v : DEFAULT_TOLERANCE_UM;
      this._renderImprove();
    });
    const order = this._h('div', 'insp-seg');
    for (const [worst, text] of [[false, 'most improved'], [true, 'most worse']]) {
      const b = this._h('button', '', text);
      b.type = 'button';
      b.dataset.order = worst ? 'worst' : 'best';
      b.dataset.focus = `improve:order-${worst ? 'worst' : 'best'}`;
      b.setAttribute('aria-pressed', String(imp.worst === worst));
      b.addEventListener('click', () => { imp.worst = worst; this._renderImprove(); });
      order.append(b);
    }
    const l1 = this._h('label');
    l1.append(this.doc.createTextNode('from '), from);
    const l2 = this._h('label');
    l2.append(this.doc.createTextNode('to '), to);
    const l3 = this._h('label');
    l3.append(this.doc.createTextNode('unchanged within '), tol, this.doc.createTextNode(' µm'));
    tools.append(l1, l2, metric, l3, order);

    const A = layers.find((l) => l.id === imp.from);
    const B = layers.find((l) => l.id === imp.to);
    if (!A || !B || A.id === B.id) { body.replaceChildren(this._h('div', 'insp-empty', 'Choose two different layers.')); return; }
    const result = improvement(annotationRows(A.result, A.volume), annotationRows(B.result, B.volume), { metric: imp.metric, tolerance: imp.tolerance });
    const summary = this._h('div', 'insp-note');
    summary.dataset.role = 'improve-summary';
    // The change is given both ways, because they can disagree: the mean per annotation counts a
    // soma and a spine the same, while everything else in the drawer is weighted by the surface.
    summary.textContent = `${B.name} fits ${fmtCount(result.improved)} annotations better, ${A.name} ${fmtCount(result.worse)}; `
      + `${fmtCount(result.unchanged)} within ${fmtUm(imp.tolerance)} µm (of ${fmtCount(result.rows.length)}`
      + `${result.missing ? `, ${fmtCount(result.missing)} without a number` : ''}) · change `
      + `${fmtSigned(result.meanDelta)} µm per annotation, ${fmtSigned(result.weightedDelta)} µm by area`;
    this._help(summary, `The change is ${B.name} minus ${A.name}: negative where ${B.name} fits better. Per annotation `
      + `counts each annotation once; by area weights each by the share of ${A.name}'s surface it owns, as the rest of the `
      + 'drawer is weighted. They can read the other way: many small annotations better and the large ones no better.');
    const list = imp.worst ? result.rows.slice().reverse() : result.rows;
    const table = this._h('table', 'insp-table');
    const head = this._h('thead');
    const hr = this._h('tr');
    for (const [title, help] of [
      ['Location', 'The annotation, with its volume.'], ['Section', 'Its section number.'], ['r \u00b5m', 'Its radius.'],
      ['from', `${A.name}: the error (\u00b5m) of this annotation in the layer the comparison starts from.`],
      ['to', `${B.name}: the error (\u00b5m) of this annotation in the layer it is compared with.`],
      ['Change', 'To minus from, in \u00b5m: negative where the second layer fits this annotation better.']]) {
      const th = this._h('th', '', title);
      this._help(th, help);
      hr.append(th);
    }
    head.append(hr);
    const rows = this._h('tbody');
    for (const r of list.slice(0, 12)) {
      const fly = () => this.callbacks.onFlyTo?.({ layerId: B.id, volume: B.volume, annotationId: r.id, section: r.section, xyz: r.xyz, source: 'improvement' });
      const tr = this._h('tr');
      tr.dataset.id = String(r.id);
      tr.dataset.fly = '1';
      const where = this._h('td');
      const flyButton = this._h('button', 'link', r.label);
      flyButton.type = 'button';
      flyButton.dataset.role = 'fly';
      flyButton.addEventListener('click', (e) => { e.stopPropagation?.(); fly(); });
      where.append(flyButton);
      tr.append(where, this._sectionCell(r.section, B, r.id, 'improvement'), this._h('td', '', fmtUm(r.radius)),
        this._h('td', '', fmtUm(r.a)), this._h('td', '', fmtUm(r.b)), this._h('td', '', fmtSigned(r.delta)));
      tr.addEventListener('click', fly);
      rows.append(tr);
    }
    table.append(head, rows);
    const scroll = this._h('div', 'insp-scroll');
    scroll.append(table);
    body.replaceChildren(summary, scroll);
    this.cards.improve.aside.textContent = `${A.name} → ${B.name}`;
  }

  /** Take the drawer down and let go of what it holds. */
  dispose() {
    this._disposed = true;
    this._resizer?.disconnect();
    this.root.remove?.();
    this.layers = [];
    this._hist = null;
  }
}
