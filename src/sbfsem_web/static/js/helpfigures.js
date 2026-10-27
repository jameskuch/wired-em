// Drawings for the hover help (asked for 2026-10-16: "make the helper text much
// more informative and include graphics / drawings").  A control names one with
// `data-help-figure="<name>"`; the help pop draws it above its text.
//
// Every figure is built here from constants: a small tree of SVG element specs
// (`{ tag, attrs, children }`, a child being a spec or a string of text), turned
// into elements with createElementNS and setAttribute, never markup.  Nothing a
// cell, a server or a person typed reaches a figure.  Colors are classes the
// stylesheet maps to the page's tokens (`.help-fig .hf-ink` and the rest in
// app.css), so a figure follows the light and the dark theme; the few fixed
// colors are the viewport's own (a mini viewport is always dark, as the render
// is) and the Position shift layer's ramp and wheel, taken from shifts.js.
//
// Pure apart from `buildFigure`, which needs a document: node tests the trees
// and their markup (tests/js/test_helpfigures.mjs).

import {
  DIRECTION_LIGHTNESS, DIRECTION_SATURATION, RAMPS, RING_COLORS, hslToRgb, rampSample,
} from './shifts.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** The elements a figure may use, and the attributes they may carry. */
export const ALLOWED_TAGS = Object.freeze(new Set([
  'svg', 'g', 'line', 'path', 'circle', 'rect', 'polygon', 'polyline', 'text', 'tspan',
]));
export const ALLOWED_ATTRS = Object.freeze(new Set([
  'viewBox', 'width', 'height', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry',
  'd', 'points', 'class', 'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap',
  'stroke-linejoin', 'stroke-opacity', 'fill-opacity', 'opacity', 'text-anchor', 'transform',
  'role', 'aria-hidden', 'dx', 'dy',
]));

/** The light ink drawn on a mini viewport (always dark, like the render's). */
const VP_INK = '#cfd6de';
const VP_MUTED = '#7f8a96';
/** Where the magnitude ramp sits in the mini viewports: the dark viewport's ramp. */
const RAMP = RAMPS.dark;

const hex = (rgb) => `#${rgb.map((c) => Math.round(Math.min(1, Math.max(0, c)) * 255)
  .toString(16).padStart(2, '0')).join('')}`;
const r1 = (v) => Math.round(v * 10) / 10;

// -- the element specs ------------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  return { tag, attrs, children: children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false) };
}
const g = (attrs, ...children) => el('g', attrs, ...children);
const line = (x1, y1, x2, y2, cls = 'hf-ink', extra = {}) => el('line', { x1, y1, x2, y2, class: cls, ...extra });
const circle = (cx, cy, r, cls, extra = {}) => el('circle', { cx, cy, r, class: cls, ...extra });
const rect = (x, y, width, height, cls, extra = {}) => el('rect', { x, y, width, height, class: cls, ...extra });
const poly = (points, cls, extra = {}) => el('polygon', {
  points: points.map(([x, y]) => `${r1(x)},${r1(y)}`).join(' '), class: cls, ...extra,
});
const path = (d, cls, extra = {}) => el('path', { d, class: cls, ...extra });
const label = (x, y, words, { anchor = 'start', cls = 'hf-label' } = {}) =>
  el('text', { x, y, class: cls, 'text-anchor': anchor }, words);
const small = (x, y, words, anchor = 'start') => label(x, y, words, { anchor, cls: 'hf-small' });

/** A mini viewport: the render's dark ground, rounded. */
const viewport = (x, y, width, height) => rect(x, y, width, height, 'hf-vp', { rx: 5 });

/**
 * An arrow from (x0, y0) to (x1, y1): a shaft that fades toward its tail (as the
 * layer draws it) and a filled head on the end point.  `stroke` is a color (the
 * ramp's, on a mini viewport) or null for the accent class.
 */
function arrow(x0, y0, x1, y1, { stroke = null, width = 2.2, head = 8, fade = true } = {}) {
  const len = Math.hypot(x1 - x0, y1 - y0) || 1;
  const ux = (x1 - x0) / len;
  const uy = (y1 - y0) / len;
  const hl = Math.min(head, 0.6 * len);
  const hw = (head * 0.85) * Math.max(hl / head, 0.4);
  const bx = x1 - ux * hl;
  const by = y1 - uy * hl;
  const paint = stroke ? { stroke } : {};
  const fill = stroke ? { fill: stroke } : {};
  const cls = stroke ? '' : 'hf-accent';
  const parts = [];
  // Three pieces of shaft, fainter toward the tail.
  const steps = fade ? [0.35, 0.65, 1] : [1];
  steps.forEach((opacity, i) => {
    const a = i / steps.length;
    const b = (i + 1) / steps.length;
    parts.push(line(r1(x0 + (bx - x0) * a), r1(y0 + (by - y0) * a), r1(x0 + (bx - x0) * b), r1(y0 + (by - y0) * b),
      cls, { ...paint, 'stroke-width': width, 'stroke-opacity': opacity, 'stroke-linecap': 'butt' }));
  });
  parts.push(poly([[x1, y1], [bx - uy * hw / 2, by + ux * hw / 2], [bx + uy * hw / 2, by - ux * hw / 2]],
    stroke ? '' : 'hf-accent-fill', fill));
  return g({}, parts);
}

/** A legend row: a swatch drawn by `icon(x, y)` and its words. */
const legendRow = (x, y, icon, words) => g({}, icon(x, y), label(x + 18, y + 3.5, words));

// -- the figures ---------------------------------------------------------------------------

/** "Viking's current section transforms": a section re-aligned, its annotation carried with it. */
function transforms() {
  // The section as it was aligned when the annotation was saved, and as it is
  // aligned now: turned 6 degrees about its middle and moved (+14, +6).
  const cx = 82; const cy = 62; const a = (7 * Math.PI) / 180;
  const move = ([x, y]) => {
    const dx = x - cx; const dy = y - cy;
    return [cx + dx * Math.cos(a) - dy * Math.sin(a) + 16, cy + dx * Math.sin(a) + dy * Math.cos(a) + 4];
  };
  const corners = [[22, 22], [142, 22], [142, 102], [22, 102]];
  const stored = [68, 52];
  const drawn = move(stored);
  const process = [[40, 90], [56, 70], [68, 52], [92, 40], [120, 34]];
  return el('svg', { viewBox: '0 0 300 132' },
    poly(corners, 'hf-line-dash'),
    poly(corners.map(move), 'hf-ink-thin'),
    el('polyline', { points: process.map(([x, y]) => `${x},${y}`).join(' '), class: 'hf-muted-dash' }),
    el('polyline', { points: process.map(move).map(([x, y]) => `${r1(x)},${r1(y)}`).join(' '), class: 'hf-muted' }),
    circle(stored[0], stored[1], 4.5, 'hf-ring'),
    circle(r1(drawn[0]), r1(drawn[1]), 4.5, 'hf-dot'),
    arrow(stored[0] + 3, stored[1] + 1, r1(drawn[0] - 4.2), r1(drawn[1] - 0.8), { head: 6, width: 1.6, fade: false }),
    legendRow(176, 24, (x, y) => rect(x, y - 4, 13, 9, 'hf-line-dash'), 'aligned when saved'),
    legendRow(176, 42, (x, y) => rect(x, y - 4, 13, 9, 'hf-ink-thin'), 'aligned now'),
    legendRow(176, 64, (x, y) => circle(x + 6, y, 4, 'hf-ring'), 'stored position'),
    legendRow(176, 82, (x, y) => circle(x + 6, y, 4, 'hf-dot'), 'drawn now'),
    small(22, 127, 'one section, seen from above'),
  );
}

/** Points of a traced process across seven sections, and the smooth path through them. */
const TRACED = [[124, 112], [138, 97], [119, 82], [141, 67], [123, 52], [136, 37], [127, 22]];
const fitX = (y) => 129.5 - (y - 67) * 0.02;

/** "Viking's section correction": a process zig-zagging across sections, pulled onto its path. */
function correction() {
  const sections = TRACED.map(([, y]) => line(62, y, 196, y, 'hf-line'));
  const fit = path(`M ${r1(fitX(116))} 116 L ${r1(fitX(18))} 18`, 'hf-accent-dash');
  const moves = TRACED.map(([x, y]) => {
    const to = fitX(y);
    return Math.abs(to - x) > 3 ? arrow(x, y, to + Math.sign(x - to) * 3.5, y, { head: 5, width: 1.4, fade: false }) : null;
  });
  return el('svg', { viewBox: '0 0 300 132' },
    sections,
    el('polyline', { points: TRACED.map(([x, y]) => `${x},${y}`).join(' '), class: 'hf-muted' }),
    fit,
    TRACED.map(([x, y]) => circle(x, y, 3.6, 'hf-ring')),
    moves,
    TRACED.map(([, y]) => circle(r1(fitX(y)), y, 3.2, 'hf-dot')),
    line(44, 112, 44, 24, 'hf-ink-thin'), poly([[44, 18], [40.5, 26], [47.5, 26]], 'hf-ink-fill'),
    label(38, 72, 'Z', { anchor: 'end' }),
    small(62, 128, 'one process, side on: a line per section'),
    legendRow(206, 40, (x, y) => circle(x + 6, y, 3.6, 'hf-ring'), 'as traced'),
    legendRow(206, 60, (x, y) => circle(x + 6, y, 3.2, 'hf-dot'), 'corrected'),
    legendRow(206, 80, (x, y) => line(x, y, x + 13, y, 'hf-accent-dash'), 'smooth path'),
  );
}

/** "Between": stored, registered, corrected, and which pair each option joins. */
function pair() {
  const y = 34;
  const xs = [36, 150, 264];
  return el('svg', { viewBox: '0 0 300 118' },
    circle(xs[0], y, 9, 'hf-ring'),
    circle(xs[1], y, 9, 'hf-ring'), circle(xs[1], y, 4, 'hf-dot'),
    circle(xs[2], y, 9, 'hf-dot'),
    arrow(xs[0] + 12, y, xs[1] - 12, y, { head: 7, width: 1.8 }),
    arrow(xs[1] + 12, y, xs[2] - 12, y, { head: 7, width: 1.8 }),
    small(xs[0], 12, 'stored', 'middle'),
    small(xs[1], 12, 'registered', 'middle'),
    small(xs[2], 12, 'corrected', 'middle'),
    label(93, 58, 'Current transforms', { anchor: 'middle' }),
    label(207, 58, 'Section correction', { anchor: 'middle' }),
    path(`M ${xs[0]} 70 L ${xs[0]} 76 L ${xs[2]} 76 L ${xs[2]} 70`, 'hf-muted'),
    label(150, 90, 'Both', { anchor: 'middle' }),
    small(xs[0], 108, "Viking's database", 'middle'),
    small(xs[1], 108, 'where Viking draws it', 'middle'),
    small(xs[2], 108, 'drawn here', 'middle'),
  );
}

/** An arrow of the layer in a mini viewport: head on the annotation, tail where it was. */
function arrowFigure() {
  const head = [184, 46];
  const tail = [70, 70];
  return el('svg', { viewBox: '0 0 300 112' },
    viewport(10, 6, 280, 82),
    path('M 30 30 C 90 26, 150 40, 184 46 S 250 64, 280 70', '', { stroke: VP_MUTED, 'stroke-width': 9,
      'stroke-opacity': 0.35, fill: 'none', 'stroke-linecap': 'round' }),
    circle(head[0], head[1], 9, '', { stroke: VP_INK, 'stroke-width': 1.5, fill: 'none' }),
    arrow(tail[0], tail[1], head[0] - 1, head[1], { stroke: hex(rampSample(RAMP, 0.6)), width: 3, head: 11 }),
    circle(tail[0], tail[1], 2, '', { fill: VP_INK, 'fill-opacity': 0.6 }),
    small(14, 104, 'tail: where it was (×k back)'),
    small(290, 104, 'head: the annotation now', 'end'),
    line(tail[0], 74, tail[0], 94, 'hf-muted-thin'),
    line(head[0], 57, head[0], 94, 'hf-muted-thin'),
  );
}

/** "Exaggerate": the same move at x1 and xk; the head stays, the tail goes back. */
function scale() {
  const hx = 250;
  const rows = [
    { y: 30, len: 12, text: '×1: the true move, 0.1 µm' },
    { y: 70, len: 168, text: '×14: the tail goes back, the head stays' },
  ];
  return el('svg', { viewBox: '0 0 300 104' },
    viewport(10, 8, 280, 80),
    line(hx, 12, hx, 84, '', { stroke: VP_MUTED, 'stroke-dasharray': '3 3', 'stroke-width': 1 }),
    rows.map(({ y, len }) => [
      circle(hx, y, 7, '', { stroke: VP_INK, 'stroke-width': 1.4, fill: 'none' }),
      arrow(hx - len, y, hx - 1, y, { stroke: hex(rampSample(RAMP, 0.55)), width: 3, head: 10 }),
    ]),
    rows.map(({ y, text: words }) => el('text', { x: 20, y: y - 10, class: 'hf-vp-label' }, words)),
    small(hx, 100, 'the annotation', 'middle'),
  );
}

/** "Original rings": the ring where the annotation was, at the arrow's tail. */
function rings() {
  const head = [204, 44];
  const tail = [96, 56];
  return el('svg', { viewBox: '0 0 300 104' },
    viewport(10, 6, 280, 74),
    circle(tail[0], tail[1], 14, '', { stroke: RING_COLORS.dark, 'stroke-width': 1.6, fill: 'none', 'stroke-dasharray': '4 3' }),
    circle(head[0], head[1], 14, '', { stroke: VP_INK, 'stroke-width': 1.6, fill: 'none' }),
    arrow(tail[0], tail[1], head[0] - 1, head[1], { stroke: hex(rampSample(RAMP, 0.6)), width: 3, head: 10 }),
    small(14, 96, 'original ring: where it was'),
    small(290, 96, 'the annotation now', 'end'),
  );
}

/** "Only shifts above": the short moves are hidden, the long ones drawn. */
function threshold() {
  const lengths = [5, 8, 12, 18, 26, 36, 48];
  const cut = 15;
  let x = 22;
  const parts = [];
  for (const len of lengths) {
    const shown = len >= cut;
    parts.push(arrow(x, 52, x + len, 52 - len * 0.35, {
      stroke: shown ? hex(rampSample(RAMP, Math.min(1, len / 40))) : VP_MUTED, width: 2.4, head: 7, fade: shown,
    }));
    if (!shown) parts.push(line(x - 2, 58, x + len + 2, 40, '', { stroke: '#ff8a8a', 'stroke-width': 1, 'stroke-opacity': 0.7 }));
    x += len + 14;
  }
  return el('svg', { viewBox: '0 0 300 100' },
    viewport(10, 8, 280, 62),
    parts,
    line(80, 12, 80, 66, '', { stroke: VP_INK, 'stroke-dasharray': '3 3', 'stroke-width': 1 }),
    small(46, 84, 'shorter: hidden', 'middle'),
    small(190, 84, 'at least the threshold: drawn', 'middle'),
    small(80, 96, 'Only shifts above', 'middle'),
  );
}

/** The marker colors a slice is drawn in (render3d.js HIGHLIGHT, ABOVE_ and BELOW_HIGHLIGHT). */
const SLICE_ON = '#ffe14d';
const SLICE_ABOVE = '#ff5c5c';
const SLICE_BELOW = '#8fd0ff';

/**
 * "One slice at a time" (2026-10-22): the plane's section in yellow and Slice ± sections
 * either side, red above and blue below, at full strength; the rest of the cell faint
 * in the same red and blue (Outside the slice).
 */
function slice() {
  const ys = Array.from({ length: 11 }, (_, i) => 18 + i * 8.5);
  const center = 5;
  const half = 1;
  const parts = [];
  ys.forEach((y, i) => {
    const inBand = Math.abs(i - center) <= half;
    // The view's up is the higher section: the lines are drawn top down.
    const color = i === center ? SLICE_ON : (i < center ? SLICE_ABOVE : SLICE_BELOW);
    parts.push(line(26, y, 196, y, '', { stroke: inBand ? VP_INK : VP_MUTED, 'stroke-width': i === center ? 1.6 : 0.8,
      'stroke-opacity': inBand ? 0.9 : 0.4 }));
    [60, 112, 160].forEach((x, k) => {
      const len = 10 + ((i * 7 + k * 5) % 9);
      parts.push(g({ opacity: inBand ? 1 : 0.22 },
        arrow(x, y, x + len, y - 0.1, { stroke: color, width: 2, head: 6 })));
    });
  });
  const top = ys[center - half] - 3.5;
  const bottom = ys[center + half] + 3.5;
  return el('svg', { viewBox: '0 0 300 124' },
    viewport(16, 6, 190, 104),
    rect(18, top, 186, bottom - top, '', { fill: VP_INK, 'fill-opacity': 0.08 }),
    parts,
    path(`M 210 ${r1(top)} L 214 ${r1(top)} L 214 ${r1(bottom)} L 210 ${r1(bottom)}`, 'hf-muted'),
    label(220, ys[center] - 2, 'the slice'),
    small(220, ys[center] + 10, 'Slice ± sections'),
    small(220, ys[1] + 2, 'above: faint red'),
    small(220, ys[9] + 2, 'below: faint blue'),
    small(16, 120, 'side on: a line per section'),
    small(290, 120, '[ ] ↑ ↓ step it', 'end'),
  );
}

/** The magnitude ramp on the viewport, `width` wide at (x, y), with its ends named. */
function rampBar(x, y, width, { low = '0', high = 'Full color at' } = {}) {
  const steps = 24;
  const w = width / steps;
  const cells = Array.from({ length: steps }, (_, i) => rect(r1(x + i * w), y, r1(w + 0.6), 10, '',
    { fill: hex(rampSample(RAMP, (i + 0.5) / steps)) }));
  return g({},
    cells,
    label(x, y + 22, low, { cls: 'hf-vp-label' }),
    label(x + width, y + 22, high, { anchor: 'end', cls: 'hf-vp-label' }),
  );
}

/** The direction wheel: one hue per angle, +X to the right and +Y up. */
function wheel(cx, cy, radius) {
  const wedges = 36;
  const parts = [];
  for (let i = 0; i < wedges; i += 1) {
    const a0 = (i / wedges) * 2 * Math.PI;
    const a1 = ((i + 1) / wedges) * 2 * Math.PI;
    const mid = (i + 0.5) / wedges;
    const color = hex(hslToRgb(mid, DIRECTION_SATURATION, DIRECTION_LIGHTNESS.dark));
    parts.push(poly([[cx, cy], [cx + radius * Math.cos(a0), cy - radius * Math.sin(a0)],
      [cx + radius * Math.cos(a1), cy - radius * Math.sin(a1)]], '', { fill: color, stroke: color, 'stroke-width': 0.6 }));
  }
  return g({}, parts, circle(cx, cy, radius * 0.38, 'hf-vp'));
}

/** "Color": the magnitude ramp, and the direction wheel. */
function color() {
  return el('svg', { viewBox: '0 0 300 116' },
    viewport(10, 8, 168, 62),
    label(18, 24, 'Magnitude', { cls: 'hf-vp-label' }),
    rampBar(20, 34, 148),
    small(20, 84, 'the move\'s length, in µm'),
    viewport(190, 8, 100, 100),
    wheel(236, 60, 30),
    el('text', { x: 270, y: 63, class: 'hf-vp-label' }, '+X'),
    el('text', { x: 236, y: 23, class: 'hf-vp-label', 'text-anchor': 'middle' }, '+Y'),
    small(240, 116, 'Direction', 'middle'),
  );
}

/** "Full color at": the ramp, and what is past its end. */
function ramp() {
  return el('svg', { viewBox: '0 0 300 66' },
    viewport(10, 6, 280, 44),
    rampBar(26, 16, 200, { low: '0 µm', high: 'Full color at' }),
    rect(236, 16, 40, 10, '', { fill: RAMP[RAMP.length - 1] }),
    small(256, 62, 'longer: the same', 'middle'),
  );
}

/** "Through the surface": on, the arrow shows across the cell; off, the cell hides it. */
function through() {
  const blob = 'M 40 58 C 40 36, 70 28, 90 34 C 110 40, 118 56, 108 70 C 98 84, 52 84, 40 58 Z';
  const panel = (ox, on) => g({ transform: `translate(${ox} 0)` },
    viewport(0, 6, 140, 88),
    on ? null : arrow(18, 64, 128, 42, { stroke: hex(rampSample(RAMP, 0.7)), width: 3, head: 10 }),
    path(blob, '', { fill: '#4f7fd6', 'fill-opacity': 0.92 }),
    on ? arrow(18, 64, 128, 42, { stroke: hex(rampSample(RAMP, 0.7)), width: 3, head: 10 }) : null,
    small(70, 108, on ? 'on: over the cell' : 'off: hidden behind it', 'middle'));
  return el('svg', { viewBox: '0 0 300 112' }, panel(6, true), panel(154, false));
}

const FIGURES = Object.freeze({
  transforms, correction, pair, arrow: arrowFigure, scale, rings, threshold, slice,
  color, ramp, through,
});

/** Every figure's name. */
export function figureNames() { return Object.keys(FIGURES); }

/** The spec tree of a figure, or null for a name that is not one. */
export function figureTree(name) {
  const make = Object.hasOwn(FIGURES, name) ? FIGURES[name] : null;
  if (!make) return null;
  const tree = make();
  tree.attrs = { role: 'img', 'aria-hidden': 'true', ...tree.attrs };
  return tree;
}

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
const escape = (text) => String(text).replace(/[&<>"]/g, (c) => ESCAPES[c]);

/** A spec tree as SVG markup (for the tests; the page builds elements instead). */
export function toMarkup(node) {
  if (typeof node === 'string') return escape(node);
  const attrs = Object.entries(node.attrs)
    .filter(([, v]) => v !== '' && v !== null && v !== undefined)
    .map(([k, v]) => ` ${k}="${escape(v)}"`).join('');
  const ns = node.tag === 'svg' ? ` xmlns="${SVG_NS}"` : '';
  return `<${node.tag}${ns}${attrs}>${node.children.map(toMarkup).join('')}</${node.tag}>`;
}

function toElement(node, doc) {
  if (typeof node === 'string') return doc.createTextNode(node);
  const out = doc.createElementNS(SVG_NS, node.tag);
  for (const [key, value] of Object.entries(node.attrs)) {
    if (value === '' || value === null || value === undefined) continue;
    out.setAttribute(key, String(value));
  }
  for (const child of node.children) out.append(toElement(child, doc));
  return out;
}

/**
 * The figure `name` as an SVG element of `doc`, inside a `div.help-fig`, or null
 * when there is no such figure.
 */
export function buildFigure(name, doc = globalThis.document) {
  const tree = figureTree(name);
  if (!tree || !doc) return null;
  const wrap = doc.createElement('div');
  wrap.className = 'help-fig';
  wrap.append(toElement(tree, doc));
  return wrap;
}
