// How an error field becomes colors, and the numbers that describe it: pure
// functions, no DOM and no three.js, so node can test every rule
// (tests/js/test_errorpaint.mjs) and Render compare's panes, legends and the
// error inspector share one implementation.
//
// A *field* is one number per vertex of a mesh the client holds: the distance to
// the nearest annotation (`vertex_um` of a deviation result), the part of it along
// Z (`axial_um`), or a signed distance to another mesh (`distance_um` of a
// difference).  Lengths are micrometers; colors are sRGB components in 0..1, the
// form `Renderer3D.setMesh({ vertexColors })` and `setPoints` take (the same bytes
// compare.js has always passed).
//
// The two ramps here are the ones Render compare has always drawn.  `errorColors`
// and `divergingColors` are COPIES of compare.js's functions, unchanged -- a test
// holds a verbatim copy of the old functions and compares the buffers byte for
// byte -- and compare.js still defines its own until it imports these; the test
// also runs compare.js's, so the two live copies cannot drift apart unnoticed.
// Everything that is new (the lateral/axial split, the threshold, the classes, the
// rim filter, the area-weighted numbers) is built beside them, not into them.
//
// Area weighting.  A region meshed ten times as finely has ten times the vertices
// and the same area; a statistic over *vertices* says how the tessellation is
// spread, not how much of the surface is wrong.  `vertexAreas` gives each vertex a
// third of every face around it (the server's `vertex_areas`), `weightedQuantiles`
// and `areaStats` are the server's `weighted_quantiles` and `AreaStats` in
// JavaScript -- the test pins them against numbers computed by the Python -- so a
// figure computed here for a lateral or axial field reads like the ones the server
// sends for the total.

// -- the ramps -------------------------------------------------------------------

/** Light gray where render and annotations agree, amber half way, red at the scale. */
export const ERROR_STOPS = [[0.85, 0.87, 0.90], [1.0, 0.69, 0.0], [0.85, 0.12, 0.23]];
export const ERROR_GRADIENT = `linear-gradient(to right, ${ERROR_STOPS.map(
  (stop) => `rgb(${stop.map((c) => Math.round(c * 255)).join(',')})`).join(', ')})`;

/** A signed distance: A inside B is blue, A outside B red, white at 0. */
export const DIVERGING_NEGATIVE = [0.23, 0.45, 0.92];
export const DIVERGING_POSITIVE = [0.86, 0.22, 0.22];

/** What a vertex below the threshold is painted: a gray darker than the ramp's zero, so the edge of the highlight reads. */
export const BELOW_THRESHOLD = [0.52, 0.54, 0.57];

/** The stops, as a CSS gradient for a legend bar. */
function cssGradient(stops) {
  return `linear-gradient(to right, ${stops.map(
    (stop) => `rgb(${stop.map((c) => Math.round(c * 255)).join(',')})`).join(', ')})`;
}
export const DIVERGING_GRADIENT = cssGradient([DIVERGING_NEGATIVE, [1, 1, 1], DIVERGING_POSITIVE]);

/** `out` if it can hold `n` colors, else a fresh buffer: a repaint reuses the last one. */
function colorBuffer(n, out) {
  return out && out.length === n * 3 ? out : new Float32Array(n * 3);
}

/** Blue-white-red colors for signed distances at +-`scale` (a copy of the old compare.js function, unchanged). */
export function divergingColors(values, scale, out = null) {
  const colors = colorBuffer(values.length, out);
  const neg = DIVERGING_NEGATIVE;   // A inside B
  const pos = DIVERGING_POSITIVE;   // A outside B
  for (let i = 0; i < values.length; i += 1) {
    let t = values[i] / scale;
    if (t > 1) t = 1; else if (t < -1) t = -1;
    const end = t < 0 ? neg : pos;
    const w = Math.abs(t);
    colors[3 * i] = 1 + (end[0] - 1) * w;
    colors[3 * i + 1] = 1 + (end[1] - 1) * w;
    colors[3 * i + 2] = 1 + (end[2] - 1) * w;
  }
  return colors;
}

/** The error ramp at 0..1 written into `colors` at offset `o` (the one place the three stops are interpolated). */
function rampInto(colors, o, x) {
  const t = x * 2;
  const [from, to] = t < 1 ? [ERROR_STOPS[0], ERROR_STOPS[1]] : [ERROR_STOPS[1], ERROR_STOPS[2]];
  const w = t < 1 ? t : t - 1;
  for (let c = 0; c < 3; c += 1) colors[o + c] = from[c] + (to[c] - from[c]) * w;
}

/**
 * Error colors for absolute distances, 0 to `scale`: light gray where the render
 * and the annotations agree, amber half way, red at the scale and beyond.  Gray
 * rather than a dark end, so the part that is right is still a surface you can
 * see on the dark viewport.  (A copy of the old compare.js function, unchanged.)
 */
export function errorColors(values, scale, out = null) {
  const colors = colorBuffer(values.length, out);
  for (let i = 0; i < values.length; i += 1) {
    rampInto(colors, 3 * i, Math.min(1, Math.max(0, values[i] / scale)));
  }
  return colors;
}

/** The error ramp at one distance: [r, g, b]. */
export function errorColorAt(value, scale) {
  const out = new Float32Array(3);
  rampInto(out, 0, Math.min(1, Math.max(0, value / scale)));
  return [out[0], out[1], out[2]];
}

/** The signed ramp at one distance: [r, g, b]. */
export function divergingColorAt(value, scale) {
  return Array.from(divergingColors([value], scale));
}

// -- which part of the error: total, sideways or along Z ------------------------

export const METRICS = Object.freeze(['abs', 'lateral', 'axial']);
export const METRIC_LABELS = Object.freeze({ abs: 'distance', lateral: 'sideways', axial: 'along Z' });

/**
 * The sideways part of each distance: `sqrt(d^2 - axial^2)`, never below 0.  The
 * server sends `axial_um` no larger than `vertex_um`, but both are rounded to
 * 0.1 nm and a field from another source need not obey, so the radicand is
 * clamped.  Float64 until it is painted.
 */
export function lateralValues(abs, axial, out = null) {
  const n = abs.length;
  const result = out && out.length === n ? out : new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const d = abs[i];
    const a = axial[i];
    result[i] = Math.sqrt(Math.max(d * d - a * a, 0));
  }
  return result;
}

/**
 * The field to paint for a metric: `abs` is the distance itself (the very array
 * given, no copy), `axial` the gap along Z, `lateral` what is left.  `null` when
 * the result has no `axial_um` of the right length (a server or a cache that
 * predates it): the caller keeps painting the total and says why the others are
 * not available.
 */
export function errorValues(metric, vertexUm, axialUm) {
  if (metric === 'abs' || metric == null) return vertexUm;
  if (!axialUm || axialUm.length !== vertexUm.length || !vertexUm.length) return null;
  if (metric === 'axial') return axialUm;
  if (metric === 'lateral') return lateralValues(vertexUm, axialUm);
  return null;
}

// -- the threshold ---------------------------------------------------------------

/**
 * Colors that show only the worst part of the surface: a vertex below `threshold`
 * is the neutral gray `below`; from `threshold` up the error ramp is re-zeroed at
 * the threshold and runs to red at `scale`.  So the heavy tail the linear ramp
 * wastes on one shade of amber gets the whole ramp.  A threshold at or above the
 * scale leaves a ramp of no width: every vertex at or above the threshold is red,
 * the one exactly at it too (with the percentile slider at 100 % the threshold is
 * the field's maximum, and the single worst vertex must not be the palest one).  A
 * threshold of 0 is the plain ramp and keeps it whatever the scale: a field of
 * zeros against a scale of 0 (two identical renderings, or a render whose vertices
 * lie on the annotations) is the palest gray everywhere, not the worst red.  A
 * value that is not a number is below.
 */
export function thresholdColors(values, scale, threshold, { below = BELOW_THRESHOLD, out = null } = {}) {
  const colors = colorBuffer(values.length, out);
  const span = Math.max(scale - threshold, 1e-9);
  const noRamp = scale <= threshold && threshold > 0;
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    if (v >= threshold) {
      rampInto(colors, 3 * i, noRamp ? 1 : Math.min(1, (v - threshold) / span));
    } else {
      colors[3 * i] = below[0];
      colors[3 * i + 1] = below[1];
      colors[3 * i + 2] = below[2];
    }
  }
  return colors;
}

// -- area-weighted numbers -------------------------------------------------------

/**
 * The area each vertex stands for: a third of every face around it, float64.  The
 * areas add up to the surface area, so a statistic weighted by them is a statistic
 * over the surface and not its tessellation.  `vertices` and `faces` are the flat
 * arrays of a MeshDTO (x, y, z per vertex; three indices per face); a vertex no
 * face uses weighs nothing.
 */
export function vertexAreas(vertices, faces) {
  const nv = Math.floor(vertices.length / 3);
  const area = new Float64Array(nv);
  for (let f = 0; f + 2 < faces.length; f += 3) {
    const a = faces[f] * 3;
    const b = faces[f + 1] * 3;
    const c = faces[f + 2] * 3;
    const ux = vertices[b] - vertices[a];
    const uy = vertices[b + 1] - vertices[a + 1];
    const uz = vertices[b + 2] - vertices[a + 2];
    const vx = vertices[c] - vertices[a];
    const vy = vertices[c + 1] - vertices[a + 1];
    const vz = vertices[c + 2] - vertices[a + 2];
    const cx = uy * vz - uz * vy;
    const cy = uz * vx - ux * vz;
    const cz = ux * vy - uy * vx;
    const third = (0.5 * Math.sqrt(cx * cx + cy * cy + cz * cz)) / 3;
    area[faces[f]] += third;
    area[faces[f + 1]] += third;
    area[faces[f + 2]] += third;
  }
  return area;
}

/** The weights to use: `weights` if any is positive, else null (every value counts once). */
function usableWeights(weights, n) {
  if (weights) {
    for (let i = 0; i < n; i += 1) if (weights[i] > 0) return weights;
  }
  return null;
}

/**
 * Quantiles of `values` in which each value counts for its weight (the server's
 * `weighted_quantiles`): every value sits at the middle of its own share of the
 * cumulative weight and a quantile is read between neighbors, so with equal
 * weights it is the plotting-position quantile and not the nearest rank.  A
 * probability outside the first and last position gets the first or last value.
 * Zero weights and values that are not finite numbers are left out; without any
 * weight every value counts once; without values every quantile is 0.  `probs` are
 * 0..1; the result is a plain array.
 */
export function weightedQuantiles(values, weights, probs) {
  const n = values.length;
  if (!n) return Array.from(probs, () => 0);
  const w = usableWeights(weights, n);
  const held = [];
  for (let i = 0; i < n; i += 1) if ((w === null || w[i] > 0) && Number.isFinite(values[i])) held.push(i);
  if (!held.length) return Array.from(probs, () => 0);
  const order = Uint32Array.from(held).sort((a, b) => values[a] - values[b]);
  const m = order.length;
  const v = new Float64Array(m);
  const x = new Float64Array(m);
  let total = 0;
  for (let k = 0; k < m; k += 1) total += w === null ? 1 : w[order[k]];
  let cum = 0;
  for (let k = 0; k < m; k += 1) {
    const wk = w === null ? 1 : w[order[k]];
    cum += wk;
    v[k] = values[order[k]];
    x[k] = (cum - 0.5 * wk) / total;
  }
  return Array.from(probs, (p) => interpolate(x, v, p));
}

/** numpy.interp over strictly increasing `x`: the end values outside it. */
function interpolate(x, y, p) {
  const m = x.length;
  if (!(p > x[0])) return y[0];
  if (!(p < x[m - 1])) return y[m - 1];
  let lo = 0;
  let hi = m - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (x[mid] <= p) lo = mid; else hi = mid;
  }
  return y[lo] + ((y[hi] - y[lo]) * (p - x[lo])) / (x[hi] - x[lo]);
}

/** The 0th to 100th percentile (101 values) of `values` weighted by `weights`: the table `percentileValue` reads. */
export function quantileTable(values, weights, steps = 101) {
  const probs = Array.from({ length: steps }, (_, k) => k / (steps - 1));
  return weightedQuantiles(values, weights, probs);
}

/**
 * The value at a percentile of a quantile table (`surface.quantiles` of a result,
 * or `quantileTable`): `percent` 0..100, read between the table's entries.
 */
export function percentileValue(table, percent) {
  if (!table || !table.length) return 0;
  const at = Math.min(1, Math.max(0, percent / 100)) * (table.length - 1);
  const lo = Math.floor(at);
  const hi = Math.min(table.length - 1, lo + 1);
  return table[lo] + (table[hi] - table[lo]) * (at - lo);
}

/**
 * The percentile a value sits at in a quantile table, 0..100: the largest one at
 * which the table does not exceed it, so a long stretch of exact zeros reads as
 * the end of the stretch.  The inverse of `percentileValue` wherever the table is
 * strictly increasing.
 */
export function percentileOf(table, value) {
  const m = table ? table.length : 0;
  if (m < 2) return 0;
  if (!(value > table[0])) return value === table[0] ? flatEnd(table, 0) : 0;
  if (value >= table[m - 1]) return 100;
  let lo = 0;
  let hi = m - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (table[mid] <= value) lo = mid; else hi = mid;
  }
  const frac = (value - table[lo]) / (table[hi] - table[lo]);
  return ((lo + frac) / (m - 1)) * 100;
}

/** The last index of the run of equal entries that starts at `i`, as a percentile. */
function flatEnd(table, i) {
  let j = i;
  while (j + 1 < table.length && table[j + 1] === table[i]) j += 1;
  return (j / (table.length - 1)) * 100;
}

/** The share (0..1) of the surface area at or above `threshold`. */
export function areaShareAtLeast(values, areas, threshold) {
  const weights = usableWeights(areas, values.length);
  let total = 0;
  let above = 0;
  for (let i = 0; i < values.length; i += 1) {
    const w = weights ? weights[i] : 1;
    total += w;
    if (values[i] >= threshold) above += w;
  }
  return total > 0 ? above / total : 0;
}

/**
 * The statistics of a (signed) field over the surface, as the server's
 * `AreaStats`: mean |d|, RMS, the 95th and 98th percentile of |d|, the largest
 * |d| over the vertices some face uses, and the mean of d.  Microns.
 */
export function areaStats(values, areas) {
  const n = values.length;
  // Missing areas (none yet, or none of them positive) count every vertex once, as in
  // every other helper here.
  let weights = usableWeights(areas, n);
  let total = 0;
  if (weights) for (let i = 0; i < n; i += 1) total += weights[i];
  if (!weights || !(total > 0)) { weights = new Float64Array(n).fill(1); total = n; }
  const magnitude = new Float64Array(n);
  let sumAbs = 0;
  let sumSq = 0;
  let sumSigned = 0;
  let max = 0;
  for (let i = 0; i < n; i += 1) {
    const d = values[i];
    magnitude[i] = Math.abs(d);
    sumAbs += weights[i] * magnitude[i];
    sumSq += weights[i] * d * d;
    sumSigned += weights[i] * d;
    if (weights[i] > 0 && magnitude[i] > max) max = magnitude[i];
  }
  const [p95, p98] = weightedQuantiles(magnitude, weights, [0.95, 0.98]);
  return {
    mean_abs_um: sumAbs / total,
    rms_um: Math.sqrt(sumSq / total),
    p95_abs_um: p95,
    p98_abs_um: p98,
    max_abs_um: max,
    mean_signed_um: sumSigned / total,
  };
}

// -- histograms ------------------------------------------------------------------

export const HIST_BINS = 40;
export const HIST_REACH = 1.5;

/** The edges of the surface histogram the server sends: `bins` equal bins from 0 to `reach` x `scale`. */
export function histogramEdges(scale, { bins = HIST_BINS, reach = HIST_REACH } = {}) {
  // numpy.linspace(0, stop, bins + 1), to the last bit: the server's edges.
  const stop = reach * scale;
  const step = stop / bins;
  return Array.from({ length: bins + 1 }, (_, k) => (k === bins ? stop : k * step));
}

/**
 * The share of the surface area in each bin between `edges` (a bin holds its left
 * edge; the last also holds its right), and the share beyond the last edge:
 * `{ edges, area_share, tail_share }`, the shape of a result's `surface.hist`.  A
 * value below the first edge is counted nowhere.
 */
export function areaHistogram(values, areas, edges) {
  const bins = edges.length - 1;
  const weights = usableWeights(areas, values.length);
  const share = new Float64Array(bins);
  let total = 0;
  let tail = 0;
  const last = edges[bins];
  for (let i = 0; i < values.length; i += 1) {
    const w = weights ? weights[i] : 1;
    total += w;
    const v = values[i];
    if (v > last) { tail += w; continue; }
    if (!(v >= edges[0])) continue;
    let lo = 0;
    let hi = bins;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (edges[mid] <= v) lo = mid; else hi = mid;
    }
    share[lo] += w;
  }
  if (total > 0) {
    for (let k = 0; k < bins; k += 1) share[k] /= total;
    tail /= total;
  }
  return { edges: Array.from(edges), area_share: Array.from(share), tail_share: tail };
}

/**
 * What a histogram plot needs, as numbers in 0..1 so any drawing can scale them:
 * a bar per bin (`x0`..`x1` along the axis, `height` relative to the tallest bar,
 * `state` against the threshold: 'below', 'above', 'straddle' or 'on' when there
 * is none), the tail beyond the last edge, the position of the threshold and of the
 * sampling floor, percentile ticks read from `quantiles`, and the share of the area
 * at or above the threshold (read from the bars: the bar the threshold falls in is
 * split in proportion).  A mark beyond the axis is flagged `beyond`.
 */
export function histogramModel(hist, { threshold = null, floor = 0, quantiles = null, marks = [50, 90, 95, 98] } = {}) {
  const edges = hist.edges || [];
  const shares = hist.area_share || [];
  const n = shares.length;
  if (n < 1 || edges.length !== n + 1) {
    return { bars: [], tail: null, threshold: null, floor: null, ticks: [], shareAbove: null, axis: [0, 0] };
  }
  const lo = edges[0];
  const hi = edges[n];
  const span = hi - lo > 0 ? hi - lo : 1;
  const tailShare = hist.tail_share || 0;
  const peak = Math.max(1e-12, ...shares, tailShare);
  const at = (v) => (v - lo) / span;
  const bars = shares.map((share, i) => {
    let state = 'on';
    if (threshold !== null) {
      if (edges[i + 1] <= threshold) state = 'below';
      else if (edges[i] >= threshold) state = 'above';
      else state = 'straddle';
    }
    return { x0: at(edges[i]), x1: at(edges[i + 1]), from: edges[i], to: edges[i + 1], share, height: share / peak, state };
  });
  let shareAbove = null;
  if (threshold !== null) {
    shareAbove = tailShare;
    for (const bar of bars) {
      if (bar.state === 'above') shareAbove += bar.share;
      else if (bar.state === 'straddle') shareAbove += (bar.share * (bar.to - threshold)) / (bar.to - bar.from);
    }
    // Past the last edge the bars say nothing: the tail is the most that can lie above.
    if (threshold > hi) shareAbove = tailShare;
  }
  const mark = (v) => (v === null || v === undefined ? null : { value: v, at: Math.min(1, Math.max(0, at(v))), beyond: v > hi });
  const ticks = quantiles ? marks.map((percent) => {
    const value = percentileValue(quantiles, percent);
    return { percent, value, at: Math.min(1, Math.max(0, at(value))), beyond: value > hi };
  }) : [];
  return {
    bars,
    tail: { share: tailShare, height: tailShare / peak, from: hi },
    threshold: mark(threshold),
    floor: floor > 0 ? mark(floor) : null,
    ticks,
    shareAbove,
    shareAboveIsBound: threshold !== null && threshold > hi,
    axis: [lo, hi],
  };
}

// -- the legend --------------------------------------------------------------------

/**
 * The ticks and bar of an error legend: the ramp's gradient and the values at its
 * left, middle and right.  With a threshold the ramp starts there and the left
 * label says so; without, the ramp starts at 0.
 */
export function legendModel({ scale, threshold = null }) {
  const from = threshold === null ? 0 : threshold;
  const to = Math.max(scale, from);
  return {
    gradient: ERROR_GRADIENT,
    thresholded: threshold !== null,
    ticks: [from, (from + to) / 2, to],
    below: threshold === null ? null : BELOW_THRESHOLD,
  };
}

// -- two surfaces: where one is inside, shared with, or outside the other -------------

/** The three classes of a signed distance to another surface. */
export const OVERLAP = Object.freeze({ INSIDE: 0, SHARED: 1, OUTSIDE: 2 });
export const OVERLAP_NAMES = Object.freeze(['inside', 'shared', 'outside']);

/** The tolerance a comparison defaults to: twice its sample spacing (smaller differences are noise). */
export function defaultTolerance(sampleSpacingUm) {
  return 2 * sampleSpacingUm;
}

/**
 * Each vertex of a layer against the other layer: `OUTSIDE` where it lies more
 * than `tolerance` outside it (the render claims what the other does not),
 * `INSIDE` where it lies more than `tolerance` inside it, `SHARED` within the
 * tolerance.  A distance that is not a number is `SHARED`: nothing was measured.
 */
export function classifyOverlap(signed, tolerance, out = null) {
  const n = signed.length;
  const classes = out && out.length === n ? out : new Uint8Array(n);
  for (let i = 0; i < n; i += 1) {
    const d = signed[i];
    classes[i] = d > tolerance ? OVERLAP.OUTSIDE : d < -tolerance ? OVERLAP.INSIDE : OVERLAP.SHARED;
  }
  return classes;
}

/** The colors of the classes by default: the diverging ramp's two ends and the ramp's gray. */
export const OVERLAP_COLORS = Object.freeze([
  DIVERGING_NEGATIVE, ERROR_STOPS[0], DIVERGING_POSITIVE,
]);

/** A color per vertex from its class. */
export function overlapColors(classes, palette = OVERLAP_COLORS, out = null) {
  const colors = colorBuffer(classes.length, out);
  for (let i = 0; i < classes.length; i += 1) {
    const c = palette[classes[i]] || palette[OVERLAP.SHARED];
    colors[3 * i] = c[0];
    colors[3 * i + 1] = c[1];
    colors[3 * i + 2] = c[2];
  }
  return colors;
}

/** The share of the surface area (and the count of vertices) in each class: `{ inside, shared, outside, counts }`. */
export function overlapShares(classes, areas) {
  const weights = usableWeights(areas, classes.length);
  const sums = [0, 0, 0];
  const counts = [0, 0, 0];
  let total = 0;
  for (let i = 0; i < classes.length; i += 1) {
    const w = weights ? weights[i] : 1;
    sums[classes[i]] += w;
    counts[classes[i]] += 1;
    total += w;
  }
  const share = (k) => (total > 0 ? sums[k] / total : 0);
  return { inside: share(0), shared: share(1), outside: share(2), counts };
}

// -- the rim points ------------------------------------------------------------------

export const RIM_FILTERS = Object.freeze(['all', 'outside', 'enclosed', 'none']);

/**
 * The rim points to draw: `all`, `none`, those the render encloses (it is fatter
 * than the annotation there) or those it does not (it stops short, `outside`).
 * `rimXyz` is flat x, y, z; `rimUm` the distance of each to the surface;
 * `rimEnclosed` 1 or 0 for each, empty when the sides were not measured -- then
 * the side filters keep every point and `known` is false.  Returns the points'
 * `xyz` and `values` ready for the renderer, how many (`n`) and `known`.
 */
export function filterRims(rimXyz, rimUm, rimEnclosed, mode = 'all') {
  const total = rimUm.length;
  const sides = rimEnclosed && rimEnclosed.length === total && total > 0;
  if (mode === 'none') return { xyz: new Float32Array(0), values: new Float32Array(0), n: 0, known: true };
  const side = mode === 'outside' ? 0 : mode === 'enclosed' ? 1 : null;
  const all = side === null || !sides;
  const keep = [];
  for (let i = 0; i < total; i += 1) if (all || rimEnclosed[i] === side) keep.push(i);
  const xyz = new Float32Array(keep.length * 3);
  const values = new Float32Array(keep.length);
  keep.forEach((i, k) => {
    xyz[3 * k] = rimXyz[3 * i];
    xyz[3 * k + 1] = rimXyz[3 * i + 1];
    xyz[3 * k + 2] = rimXyz[3 * i + 2];
    values[k] = rimUm[i];
  });
  return { xyz, values, n: keep.length, known: side === null || sides };
}
