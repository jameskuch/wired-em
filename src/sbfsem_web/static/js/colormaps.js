// Colormaps for depth coloring: the MATLAB RenderApp's Colormap menu.
//
// The tables come from the Python registry (colormap-tables.js is generated
// from sbfsem.util.colormaps, which ships what MATLAB R2023b produced); this
// module adds name resolution, sampling, and the per-vertex coloring.
import { COLORMAP_TABLES, DEFAULT_COLORMAP } from './colormap-tables.js';

export { DEFAULT_COLORMAP };

/** Built-in menu names, in MATLAB's menu order. */
export const COLORMAPS = Object.keys(COLORMAP_TABLES);

// Gradients people made: name -> stops [[t, '#rrggbb'], ...], from the shared
// library or the editor's live preview.  Sampled by interpolating the stops.
const custom = new Map();

export function registerGradient(name, stops) {
  const clean = (stops || [])
    .map(([t, c]) => [Math.min(1, Math.max(0, Number(t) || 0)), String(c)])
    .sort((a, b) => a[0] - b[0]);
  if (clean.length < 2) return false;
  custom.set(String(name), clean);
  decoded.delete(String(name));
  return true;
}

export function unregisterGradient(name) {
  custom.delete(String(name));
  decoded.delete(String(name));
}

/** Replace every library gradient (from GET /api/library/gradients). */
export function setGradientLibrary(entries) {
  for (const name of [...custom.keys()]) if (!name.startsWith('__')) unregisterGradient(name);
  for (const entry of entries || []) {
    if (entry?.name && !COLORMAP_TABLES[entry.name]) registerGradient(entry.name, entry.stops);
  }
}

export function customGradientNames() {
  return [...custom.keys()].filter((n) => !n.startsWith('__')).sort((a, b) => a.localeCompare(b));
}

/** Every name the menu offers: the MATLAB maps, then the library's. */
export function colormapNames() {
  return [...COLORMAPS, ...customGradientNames()];
}

export function isCustomGradient(name) {
  return custom.has(String(name));
}

/** A map the gradient editor holds only while previewing (never on a menu, never saved). */
export function isPreviewGradient(name) {
  return String(name).startsWith('__');
}

/** The stops of any map, built-in ones sampled at 9 points, for the editor. */
export function gradientStops(name) {
  const resolved = resolveColormap(name);
  if (custom.has(resolved)) return custom.get(resolved).map(([t, c]) => [t, c]);
  const stops = [];
  for (let i = 0; i <= 8; i += 1) {
    const t = i / 8;
    const [r, g, b] = colormapSample(resolved, t);
    stops.push([t, `#${[r, g, b].map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('')}`]);
  }
  return stops;
}

// Names a saved view may carry from before the menu matched MATLAB.
// `stratify` was the app's own blue-white-red, whose MATLAB original is
// RedBlue (lbmap, as FigureView's 'redblue'); MATLAB's ColorMaps.fromChar
// reads 'jet' as AntiJet. Anything else falls back to the default.
const LEGACY = { stratify: 'RedBlue', jet: 'AntiJet' };
const BY_LOWER = new Map(COLORMAPS.map((name) => [name.toLowerCase(), name]));

/** The menu name for `name`, matched case-insensitively, or the default. */
export function resolveColormap(name) {
  if (name === undefined || name === null) return DEFAULT_COLORMAP;
  const raw = String(name).trim();
  if (custom.has(raw)) return raw;
  const key = raw.toLowerCase();
  return LEGACY[key] || BY_LOWER.get(key) || DEFAULT_COLORMAP;
}

const decoded = new Map();

/** The 256×3 table for a menu name as floats in [0, 1], decoded once. */
function table(name) {
  let rgb = decoded.get(name);
  if (!rgb) {
    if (custom.has(name)) {
      // A custom gradient is rendered to the same 256-entry form as the
      // MATLAB tables, so sampling and banding treat both alike.
      const stops = custom.get(name);
      rgb = new Float32Array(256 * 3);
      for (let i = 0; i < 256; i += 1) {
        const t = i / 255;
        let k = 0;
        while (k < stops.length - 2 && stops[k + 1][0] < t) k += 1;
        const [t0, c0] = stops[k];
        const [t1, c1] = stops[k + 1];
        const f = t1 > t0 ? Math.min(1, Math.max(0, (t - t0) / (t1 - t0))) : 0;
        const a = hexToRgb(c0);
        const b = hexToRgb(c1);
        rgb[i * 3] = a[0] + (b[0] - a[0]) * f;
        rgb[i * 3 + 1] = a[1] + (b[1] - a[1]) * f;
        rgb[i * 3 + 2] = a[2] + (b[2] - a[2]) * f;
      }
    } else {
      const hex = COLORMAP_TABLES[name];
      rgb = new Float32Array(hex.length / 2);
      for (let i = 0; i < rgb.length; i += 1) {
        rgb[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16) / 255;
      }
    }
    decoded.set(name, rgb);
  }
  return rgb;
}

function hexToRgb(hex) {
  const h = String(hex).replace('#', '');
  if (h.length !== 6) return [0.5, 0.5, 0.5];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
}

/** One colormap sample at `t` in [0, 1], as `[r, g, b]`. */
export function colormapSample(name, t) {
  const rgb = table(resolveColormap(name));
  const n = rgb.length / 3;
  const u = Math.min(1, Math.max(0, t)) * (n - 1);
  const i = Math.min(n - 2, Math.floor(u));
  const f = u - i;
  const a = i * 3;
  const b = a + 3;
  return [
    rgb[a] + (rgb[b] - rgb[a]) * f,
    rgb[a + 1] + (rgb[b + 1] - rgb[a + 1]) * f,
    rgb[a + 2] + (rgb[b + 2] - rgb[a + 2]) * f,
  ];
}

/**
 * Per-vertex RGB for a scalar field, e.g. IPL depth.
 *
 * `levels` quantizes the map into discrete bands (MATLAB's colormap length):
 * banding is what makes a depth gradient readable as strata rather than as a
 * smooth wash. `invert` flips the map, as the Invert checkbox did. Bands are
 * the 256-entry table resampled, which is what every map's own MATLAB code
 * did too, except that MATLAB's `hsv(n)` spaces hues by 1/n so its last band
 * stops short of red; here a few-level Hsv is red at both ends.
 */
export function depthColors(values, { map = DEFAULT_COLORMAP, levels = 256, invert = false } = {}) {
  const out = new Float32Array(values.length * 3);
  const n = Math.max(2, Math.min(256, Math.round(levels) || 256));
  // One color per band, looked up once: the band index is all a vertex needs.
  // Sampling the map per vertex (a name lookup, a table lookup and an array
  // for each) cost 513 ms for 800k vertices against 24 ms for this, on every
  // slider event of the gradient editor.  Same inputs to the same sampler, so
  // the colors are the ones the per-vertex form gave, bit for bit.
  const bands = new Float32Array(n * 3);
  for (let band = 0; band < n; band += 1) {
    const [r, g, b] = colormapSample(map, band / (n - 1));
    bands[band * 3] = r; bands[band * 3 + 1] = g; bands[band * 3 + 2] = b;
  }
  for (let i = 0; i < values.length; i += 1) {
    let t = Math.min(1, Math.max(0, Number(values[i]) || 0));
    if (invert) t = 1 - t;
    // Quantize to the middle of each band, so neither end is half-width.
    const band = Math.min(n - 1, Math.floor(t * n)) * 3;
    out[i * 3] = bands[band]; out[i * 3 + 1] = bands[band + 1]; out[i * 3 + 2] = bands[band + 2];
  }
  return out;
}

/**
 * The colors a colorbar of `levels` bands shows, left to right, as the
 * colors `depthColors` gives a value in each: `[[r, g, b], ...]` in 0-1,
 * one per band, for a bar that reads as the banded surface does.
 */
export function bandColors(map, levels, invert) {
  const n = Math.max(2, Math.min(256, Math.round(levels) || 256));
  const out = [];
  for (let i = 0; i < n; i += 1) {
    // The value at the middle of slot i; inverted, slot i holds band n-1-i.
    const band = invert ? n - 1 - i : i;
    out.push(colormapSample(map, band / (n - 1)));
  }
  return out;
}
