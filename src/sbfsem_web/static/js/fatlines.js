// Lines a set number of screen pixels wide, for the Position shift layer.
//
// WebGL draws a line one pixel wide whatever `linewidth` says, so the shift
// arrows were hairlines (asked for 2026-10-16: "can they be thicker").  Each
// segment is drawn here as two triangles instead, spread across its own
// direction on the screen by a small vertex shader (the classic wide-line
// technique; three.js's own `Line2` does the same, but its addons are not
// vendored).  An arrow is its shaft so drawn plus a filled head: a triangle whose
// tip sits on the head point and whose size is in pixels too, so it reads at
// every zoom, and which shrinks with an arrow that is short on the screen so an
// arrow is never all head.
//
// The buffers are built from the layer's ordinary line buffers (shifts.js's
// `buildArrows` and `buildRings`, LineSegments layout), which are kept as they
// are: the right-click menu picks on those segments, and a slice's window is a
// range of them.  `fatRange` maps that range onto the wide buffers.
//
// Pure: no DOM, no three.js.  The shader sources are plain strings for the
// renderer's ShaderMaterial; `cornerOffset` is the same arithmetic in
// JavaScript, so node tests what the shader does (tests/js/test_fatlines.mjs).

/** Source vertices per arrow (shifts.js ARROW_VERTS: shaft, two barbs). */
export const ARROW_SOURCE_VERTS = 6;
/** Wide vertices per arrow: the shaft's two triangles, then the head's one. */
export const ARROW_FAT_VERTS = 9;
/** Wide vertices per plain segment (a ring's side): two triangles. */
export const SEGMENT_FAT_VERTS = 6;

/** What a corner belongs to: a plain segment, an arrow's shaft or its head. */
export const KIND = Object.freeze({ segment: 0, shaft: 1, head: 2 });

// Corners as [along, side, kind]: `along` 0 is the segment's start (an arrow's
// tail), 1 its end (the head point, or where the shaft meets the head), 2 the
// head's tip; `side` is -1 or +1 across the line, 0 on it.
const SEGMENT_CORNERS = Object.freeze([
  [0, -1, 0], [1, -1, 0], [1, 1, 0],
  [0, -1, 0], [1, 1, 0], [0, 1, 0],
]);
const ARROW_CORNERS = Object.freeze([
  [0, -1, 1], [1, -1, 1], [1, 1, 1],
  [0, -1, 1], [1, 1, 1], [0, 1, 1],
  [1, -1, 2], [2, 0, 2], [1, 1, 2],
]);

/** Arrow width in CSS pixels, by default, and the range the setting allows. */
export const WIDTH_DEFAULT = 2.5;
export const WIDTH_MIN = 1;
export const WIDTH_MAX = 8;

/**
 * The head of an arrow drawn `width` pixels wide: `{ length, width }` in the
 * same pixels.  About 11 by 9 at the default 2.5, so the head stays a clear
 * wedge beside a shaft that is only a few pixels across.
 */
export function headSize(width) {
  const w = Number.isFinite(width) && width > 0 ? width : WIDTH_DEFAULT;
  return { length: 3.2 * w + 3, width: 2.8 * w + 2 };
}

/** The original rings are drawn thinner than the arrows: they are context. */
export function ringWidth(width) {
  const w = Number.isFinite(width) && width > 0 ? width : WIDTH_DEFAULT;
  return Math.min(3, Math.max(1, 0.6 * w));
}

/** The head's share of an arrow's length on the screen, at most. */
export const HEAD_SHARE = 0.6;
/** The head's width never shrinks below this share of its full width. */
export const HEAD_MIN_WIDTH_SHARE = 0.4;
/** Alpha at an arrow's tail, as a share of its head's: fainter tail, full head. */
export const TAIL_ALPHA = 0.35;

const corner = (out, o, c) => { out[o] = c[0]; out[o + 1] = c[1]; out[o + 2] = c[2]; };

/**
 * Wide arrows from `buildArrows` output: `positions` (and `colors`, one RGB a
 * vertex, or null) in its layout, ARROW_SOURCE_VERTS vertices an arrow with the
 * shaft first (tail, then head).  Returns the attributes the shader reads, nine
 * vertices an arrow: `position` (the corner's own end of the shaft: the tail, or
 * the head point), `other` (the opposite end), `corner` and `color` (the
 * shader's `lineColor`: the tail's color at the tail, the head's elsewhere), and
 * `n` arrows.
 */
export function fatArrows(positions, colors = null) {
  const n = Math.floor(positions.length / (ARROW_SOURCE_VERTS * 3));
  const V = ARROW_FAT_VERTS;
  const position = new Float32Array(n * V * 3);
  const other = new Float32Array(n * V * 3);
  const corners = new Int8Array(n * V * 3);
  const color = colors ? new Float32Array(n * V * 3) : null;
  for (let i = 0; i < n; i += 1) {
    const s = i * ARROW_SOURCE_VERTS * 3;   // the tail; the head is the next vertex
    for (let v = 0; v < V; v += 1) {
      const c = ARROW_CORNERS[v];
      const atTail = c[0] < 0.5;
      const own = atTail ? s : s + 3;
      const far = atTail ? s + 3 : s;
      const o = (i * V + v) * 3;
      position[o] = positions[own]; position[o + 1] = positions[own + 1]; position[o + 2] = positions[own + 2];
      other[o] = positions[far]; other[o + 1] = positions[far + 1]; other[o + 2] = positions[far + 2];
      corner(corners, o, c);
      if (color) { color[o] = colors[own]; color[o + 1] = colors[own + 1]; color[o + 2] = colors[own + 2]; }
    }
  }
  return { position, other, corner: corners, color, n, vertsPerMark: V };
}

/**
 * Wide segments from LineSegments `positions` (two vertices a segment) and their
 * `colors`: six vertices a segment, in the same attributes as `fatArrows`.
 */
export function fatSegments(positions, colors = null) {
  const n = Math.floor(positions.length / 6);
  const V = SEGMENT_FAT_VERTS;
  const position = new Float32Array(n * V * 3);
  const other = new Float32Array(n * V * 3);
  const corners = new Int8Array(n * V * 3);
  const color = colors ? new Float32Array(n * V * 3) : null;
  for (let i = 0; i < n; i += 1) {
    const s = i * 6;
    for (let v = 0; v < V; v += 1) {
      const c = SEGMENT_CORNERS[v];
      const own = c[0] < 0.5 ? s : s + 3;
      const far = c[0] < 0.5 ? s + 3 : s;
      const o = (i * V + v) * 3;
      position[o] = positions[own]; position[o + 1] = positions[own + 1]; position[o + 2] = positions[own + 2];
      other[o] = positions[far]; other[o + 1] = positions[far + 1]; other[o + 2] = positions[far + 2];
      corner(corners, o, c);
      if (color) { color[o] = colors[own]; color[o + 1] = colors[own + 1]; color[o + 2] = colors[own + 2]; }
    }
  }
  return { position, other, corner: corners, color, n, vertsPerMark: V };
}

/** The wide buffers' colors for a role, from line colors in the source layout (a recolor). */
export function fatColors(colors, role) {
  if (role === 'arrows') {
    const n = Math.floor(colors.length / (ARROW_SOURCE_VERTS * 3));
    const out = new Float32Array(n * ARROW_FAT_VERTS * 3);
    for (let i = 0; i < n; i += 1) {
      const s = i * ARROW_SOURCE_VERTS * 3;
      for (let v = 0; v < ARROW_FAT_VERTS; v += 1) {
        const own = ARROW_CORNERS[v][0] < 0.5 ? s : s + 3;
        const o = (i * ARROW_FAT_VERTS + v) * 3;
        out[o] = colors[own]; out[o + 1] = colors[own + 1]; out[o + 2] = colors[own + 2];
      }
    }
    return out;
  }
  const n = Math.floor(colors.length / 6);
  const out = new Float32Array(n * SEGMENT_FAT_VERTS * 3);
  for (let i = 0; i < n; i += 1) {
    for (let v = 0; v < SEGMENT_FAT_VERTS; v += 1) {
      const own = i * 6 + (SEGMENT_CORNERS[v][0] < 0.5 ? 0 : 3);
      const o = (i * SEGMENT_FAT_VERTS + v) * 3;
      out[o] = colors[own]; out[o + 1] = colors[own + 1]; out[o + 2] = colors[own + 2];
    }
  }
  return out;
}

/**
 * A draw range in the line buffer's vertices (what shiftlayer.js's windows are)
 * as one in the wide buffer's: an arrow is 6 vertices there and 9 here, a ring
 * segment 2 and 6.  `null` stays null (everything is drawn).
 */
export function fatRange(range, role) {
  if (!range) return null;
  if (role === 'arrows') {
    return {
      first: (range.first / ARROW_SOURCE_VERTS) * ARROW_FAT_VERTS,
      count: (range.count / ARROW_SOURCE_VERTS) * ARROW_FAT_VERTS,
    };
  }
  return { first: (range.first / 2) * SEGMENT_FAT_VERTS, count: (range.count / 2) * SEGMENT_FAT_VERTS };
}

/**
 * What the right-click menu measures an arrow by: its shaft alone, two vertices
 * an arrow, from the six of the line buffer (shaft, then two barbs).  The wide
 * arrow draws no barbs, so measuring to them answered clicks on empty background
 * beside a long arrow (review of 2026-10-16: 12 px off a 100 px shaft, on
 * nothing, picked it at 0.31 px); its head is reached by widening the reach.
 */
export function shaftSegments(positions) {
  const n = Math.floor(positions.length / (ARROW_SOURCE_VERTS * 3));
  const out = new Float32Array(n * 6);
  for (let i = 0; i < n; i += 1) {
    out.set(positions.subarray(i * ARROW_SOURCE_VERTS * 3, i * ARROW_SOURCE_VERTS * 3 + 6), i * 6);
  }
  return out;
}

/** A window in the line buffer's arrow vertices (6 an arrow) as one in `shaftSegments`' (2). */
export function shaftRange(range) {
  if (!range) return null;
  return { first: (range.first / ARROW_SOURCE_VERTS) * 2, count: (range.count / ARROW_SOURCE_VERTS) * 2 };
}

/** How far from a shaft, in pixels, a click still finds the arrow: its head is wider than the shaft. */
export function arrowReach(width, base) {
  return Math.max(base, headSize(width).width / 2 + 1);
}

/**
 * Where one corner goes, in pixels from its anchor (the tail for `along` 0, the
 * head point otherwise), given the arrow's two ends on the screen in pixels.
 * The vertex shader below does exactly this; it is written out here so node can
 * check it.  `lineWidth`, `headLength` and `headWidth` are pixels.
 *
 * The shaft is `lineWidth` across.  An arrow's shaft stops where the head
 * begins, so a translucent arrow is not darker where they would overlap.  The
 * head is `headLength` long and `headWidth` across at its base, but no longer
 * than HEAD_SHARE of the arrow on the screen; its width shrinks with it, down to
 * HEAD_MIN_WIDTH_SHARE.  Returns `{ offset: [dx, dy], fade }`, `fade` what the
 * fragment fades by (0 at the tail, 1 at the shaft's end and on the head).
 */
export function cornerOffset(tail, head, [along, side, kind], { lineWidth, headLength = 0, headWidth = 0 }) {
  const dx = head[0] - tail[0];
  const dy = head[1] - tail[1];
  const len = Math.hypot(dx, dy);
  const dir = len > 1e-4 ? [dx / len, dy / len] : [1, 0];
  const perp = [-dir[1], dir[0]];
  const hl = kind > 0.5 && headLength > 0 ? Math.min(headLength, HEAD_SHARE * len) : 0;
  const hw = headLength > 0 ? headWidth * Math.max(hl / headLength, HEAD_MIN_WIDTH_SHARE) : 0;
  if (kind > 1.5) {
    if (along > 1.5) return { offset: [0, 0], fade: 1 };
    return { offset: [-dir[0] * hl + perp[0] * side * 0.5 * hw, -dir[1] * hl + perp[1] * side * 0.5 * hw], fade: 1 };
  }
  const half = 0.5 * lineWidth * side;
  const back = along < 0.5 ? 0 : hl;
  return { offset: [perp[0] * half - dir[0] * back, perp[1] * half - dir[1] * back], fade: along < 0.5 ? 0 : 1 };
}

/**
 * The vertex shader.  three.js prefixes it with the matrices, `position` and the
 * clipping defines.  Both ends go to clip space; an end behind the camera of a
 * perspective view is first pulled to the near plane, or the line would fold
 * through infinity.  The corner is then moved in pixels (`cornerOffset`) and
 * put back at its end's depth, so the depth test and the clipping planes (the
 * crop box and the cell's Z range, read at the end's own place) work as for a line.
 */
export const FAT_VERTEX_SHADER = /* glsl */ `
#include <common>
#include <clipping_planes_pars_vertex>
uniform vec2 resolution;
uniform float lineWidth;
uniform float headLength;
uniform float headWidth;
attribute vec3 other;
attribute vec3 corner;
attribute vec3 lineColor;
attribute float lineAlpha;
varying vec3 vColor;
varying float vAlpha;
varying float vFade;

void toNear(in vec4 keep, inout vec4 move) {
  // The view-space z of the near plane, from the projection matrix.
  float a = projectionMatrix[2][2];
  float b = projectionMatrix[3][2];
  float nearZ = -0.5 * b / a;
  float t = (nearZ - keep.z) / (move.z - keep.z);
  move.xyz = mix(keep.xyz, move.xyz, t);
}

void main() {
  vColor = lineColor;
  vAlpha = lineAlpha;
  float along = corner.x;
  float side = corner.y;
  float kind = corner.z;
  bool atTail = along < 0.5;
  vec4 tail = modelViewMatrix * vec4(atTail ? position : other, 1.0);
  vec4 head = modelViewMatrix * vec4(atTail ? other : position, 1.0);
  if (projectionMatrix[2][3] == -1.0) {
    if (tail.z < 0.0 && head.z >= 0.0) toNear(tail, head);
    else if (head.z < 0.0 && tail.z >= 0.0) toNear(head, tail);
  }
  vec4 ct = projectionMatrix * tail;
  vec4 ch = projectionMatrix * head;
  vec2 st = (ct.xy / ct.w * 0.5 + 0.5) * resolution;
  vec2 sh = (ch.xy / ch.w * 0.5 + 0.5) * resolution;
  vec2 d = sh - st;
  float len = length(d);
  vec2 dir = len > 1e-4 ? d / len : vec2(1.0, 0.0);
  vec2 perp = vec2(-dir.y, dir.x);
  float hl = (kind > 0.5 && headLength > 0.0) ? min(headLength, ${HEAD_SHARE.toFixed(3)} * len) : 0.0;
  float hw = headLength > 0.0 ? headWidth * max(hl / headLength, ${HEAD_MIN_WIDTH_SHARE.toFixed(3)}) : 0.0;
  vec2 offset;
  if (kind > 1.5) {
    offset = along > 1.5 ? vec2(0.0) : (-dir * hl + perp * side * 0.5 * hw);
    vFade = 1.0;
  } else {
    offset = perp * side * 0.5 * lineWidth - dir * (atTail ? 0.0 : hl);
    vFade = atTail ? 0.0 : 1.0;
  }
  vec4 clip = atTail ? ct : ch;
  clip.xy += offset * 2.0 / resolution * clip.w;
  gl_Position = clip;
  vec4 mvPosition = atTail ? tail : head;
  #include <clipping_planes_vertex>
}
`;

/**
 * The fragment shader: the corner's color at `opacity` times its own `lineAlpha`
 * (1, except where One slice at a time fades the marks off the plane), the tail fainter
 * (`tailAlpha` of it) so which way an arrow points reads at a glance, then the
 * renderer's tone mapping and output color space, as a LineBasicMaterial's.
 */
export const FAT_FRAGMENT_SHADER = /* glsl */ `
#include <common>
#include <clipping_planes_pars_fragment>
uniform float opacity;
uniform float tailAlpha;
varying vec3 vColor;
varying float vAlpha;
varying float vFade;

void main() {
  #include <clipping_planes_fragment>
  gl_FragColor = vec4(vColor, opacity * vAlpha * mix(tailAlpha, 1.0, vFade));
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;
