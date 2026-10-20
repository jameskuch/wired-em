// Buffer builders for Renderer3D's layers: plain numbers in, typed arrays out.
//
// No three.js and no DOM, so node tests (tests/js/test_layerbuffers.mjs) can pin
// them.  The renderer owns the GPU objects; these own the arithmetic that has to
// be right before one is made -- which vertex of the annotation layer's single
// buffer belongs to which annotation, and which line segments are fit to draw.

/** Samples around one annotation ring (render3d.js `setDiscs` uses the same). */
export const RING_SEGMENTS = 24;

/**
 * The color of each item of a list, whichever way the caller named them.
 *
 * `spec` is an array (by position), a Map (by the item's id, as `idOf` reads
 * it), a function `(item, position) => color`, or null for no per-item colors.
 * `positions[i]` is where `items[i]` sat in the caller's own list when that list
 * was filtered on the way here (default: `i`), so a color array stays parallel
 * to the list the caller wrote.  Each answer goes through `toRgb`, which turns
 * a CSS color or a ready `[r, g, b]` into three numbers (or null for "no color
 * of its own"); a throwing or non-finite answer counts as null, so one bad
 * entry never blanks the layer.
 *
 * @returns {Array<number[]|null>|null} one entry per item, or null for no spec
 */
export function resolveColors(spec, items, idOf, toRgb, positions = null) {
  if (spec === null || spec === undefined) return null;
  const out = new Array(items.length);
  for (let i = 0; i < items.length; i += 1) {
    const at = positions ? positions[i] : i;
    let raw = null;
    if (typeof spec === 'function') raw = spec(items[i], at);
    else if (spec instanceof Map) raw = spec.get(idOf(items[i]));
    else if (Array.isArray(spec)) raw = spec[at];
    let rgb = null;
    try {
      rgb = raw === null || raw === undefined ? null : toRgb(raw);
    } catch {
      rgb = null;
    }
    out[i] = Array.isArray(rgb) && rgb.length === 3 && rgb.every(Number.isFinite) ? rgb : null;
  }
  return out;
}

/**
 * Per-vertex colors for the annotation layer's one buffer.
 *
 * The buffer holds every ring first (`segments` samples each), then every
 * traced outline's polygons (their own sample counts), and each sample owns
 * `perSample` vertices (2 for a flat ribbon, 4 for one with a depth).  Every
 * vertex of an item takes that item's color; an item without one takes
 * `fallback`, so a layer where only some annotations are colored draws the
 * rest as it always did.
 *
 * @param ringCount       rings in the buffer
 * @param ringRgb         one `[r, g, b]` or null per ring, or null for none
 * @param polygonRgb      one `[r, g, b]` or null per polygon, or null for none
 * @param polygonSamples  samples in each polygon
 * @param fallback        `[r, g, b]` for an item with no color
 * @returns {Float32Array} three numbers a vertex, in buffer order
 */
export function discColorBuffer({
  ringCount = 0, ringRgb = null, polygonRgb = null, polygonSamples = [],
  segments = RING_SEGMENTS, perSample = 2, fallback = [1, 1, 1],
}) {
  const rings = ringCount;
  const polygons = polygonSamples.length;
  let vertices = rings * segments * perSample;
  for (const n of polygonSamples) vertices += n * perSample;
  const out = new Float32Array(vertices * 3);
  let at = 0;
  const fill = (rgb, count) => {
    const [r, g, b] = rgb || fallback;
    for (let k = 0; k < count; k += 1) {
      out[at] = r; out[at + 1] = g; out[at + 2] = b;
      at += 3;
    }
  };
  for (let i = 0; i < rings; i += 1) fill(ringRgb ? ringRgb[i] : null, segments * perSample);
  for (let i = 0; i < polygons; i += 1) {
    fill(polygonRgb ? polygonRgb[i] : null, polygonSamples[i] * perSample);
  }
  return out;
}

/**
 * Positions (and colors) for a LineSegments layer.
 *
 * `segments` is flat, six numbers a segment (x0 y0 z0 x1 y1 z1); a trailing
 * partial segment is ignored.  A segment with any non-finite coordinate is
 * dropped whole -- a NaN position draws nothing in one browser and a streak to
 * the origin in another -- together with its colors.
 *
 * `colors` is either six numbers a segment (a color per end) or three (one color
 * per segment, written to both ends); any other length is ignored, so a stale
 * color array can never shear the lines from their colors.
 *
 * @returns {{positions: Float32Array, colors: (Float32Array|null), count: number}}
 */
export function segmentBuffers(segments, { colors = null } = {}) {
  const total = Math.floor((segments?.length || 0) / 6);
  let stride = 0;
  if (colors && total) {
    if (colors.length === total * 6) stride = 6;
    else if (colors.length === total * 3) stride = 3;
  }
  const positions = new Float32Array(total * 6);
  const rgb = stride ? new Float32Array(total * 6) : null;
  let kept = 0;
  for (let s = 0; s < total; s += 1) {
    const at = s * 6;
    let ok = true;
    for (let k = 0; k < 6; k += 1) {
      if (!Number.isFinite(segments[at + k])) { ok = false; break; }
    }
    if (ok && stride) {
      const from = s * stride;
      for (let k = 0; k < stride; k += 1) {
        if (!Number.isFinite(colors[from + k])) { ok = false; break; }
      }
    }
    if (!ok) continue;
    const to = kept * 6;
    for (let k = 0; k < 6; k += 1) positions[to + k] = segments[at + k];
    if (rgb) {
      const from = s * stride;
      if (stride === 6) {
        for (let k = 0; k < 6; k += 1) rgb[to + k] = colors[from + k];
      } else {
        for (let k = 0; k < 3; k += 1) {
          rgb[to + k] = colors[from + k];
          rgb[to + 3 + k] = colors[from + k];
        }
      }
    }
    kept += 1;
  }
  return {
    positions: positions.slice(0, kept * 6),
    colors: rgb ? rgb.slice(0, kept * 6) : null,
    count: kept,
  };
}
