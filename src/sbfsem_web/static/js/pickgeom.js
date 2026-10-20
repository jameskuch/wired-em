// Picking by tolerance: how near, in screen pixels, a surface or a line passes to
// the pointer.  Plain typed-array code with no three.js in it, so node can run it
// (tests/js/test_pickgeom.mjs) and the renderer only has to hand over its buffers
// and one matrix.
//
// A ray goes through one point, and a cell's thin processes are one to four pixels
// wide on screen: the pointer was on them as far as anyone could see, and the ray
// went between the triangles (60 pixels drawn at, with real right clicks, 2026-10-16:
// Inferior Monkey c1178 found 44, its Tube 44, c16137's Original 32 and its Closed
// curve volume 23).  A ray costs as much as a pass over every
// triangle (61 ms on three cells of 1.3 million triangles), so a ring of them
// around the pointer was never an option; one pass over the triangles that
// measures the distance on the screen is a ray's price, once.
//
// `matrix` is projection * view * model, the sixteen numbers of a column-major
// THREE.Matrix4 (`.elements`); `size` is the canvas in CSS pixels; `point` is the
// pointer in the canvas's own pixels, x from the left and y from the top.

/** The smallest clip-space w a vertex may have and still be put on the screen. */
export const BEHIND = 1e-9;

/**
 * Where each vertex lands: x and y in pixels (y down), and its clip-space w,
 * which is its distance along the view axis for a perspective camera and 1 for an
 * orthographic one.  A vertex with w at or under BEHIND is behind the camera and
 * has no place.
 */
export function projectPositions(positions, matrix, width, height) {
  const count = Math.floor(positions.length / 3);
  const sx = new Float32Array(count);
  const sy = new Float32Array(count);
  const sw = new Float32Array(count);
  const halfW = width / 2;
  const halfH = height / 2;
  const m = matrix;
  for (let i = 0, p = 0; i < count; i += 1, p += 3) {
    const x = positions[p];
    const y = positions[p + 1];
    const z = positions[p + 2];
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    sw[i] = w;
    if (!(w > BEHIND)) continue;
    const inv = 1 / w;
    sx[i] = ((m[0] * x + m[4] * y + m[8] * z + m[12]) * inv + 1) * halfW;
    sy[i] = (1 - (m[1] * x + m[5] * y + m[9] * z + m[13]) * inv) * halfH;
  }
  return { sx, sy, sw };
}

const found = new Float64Array(4);

/**
 * The distance from (px, py) to a screen triangle, with the barycentric weights
 * (of a, b, c) of the nearest point on it.  Zero inside.  Leaves its answer in
 * `out`: [distance, wa, wb, wc].
 */
function onTriangle(px, py, ax, ay, bx, by, cx, cy, out) {
  const det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
  if (Math.abs(det) > 1e-12) {
    const u = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det;
    const v = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det;
    const w = 1 - u - v;
    if (u >= 0 && v >= 0 && w >= 0) {
      out[0] = 0; out[1] = u; out[2] = v; out[3] = w;
      return 0;
    }
  }
  // Outside, or too thin to have an inside (seen edge-on): the nearest edge.
  let best = Infinity;
  for (let edge = 0; edge < 3; edge += 1) {
    const ix = edge === 0 ? ax : edge === 1 ? bx : cx;
    const iy = edge === 0 ? ay : edge === 1 ? by : cy;
    const jx = edge === 0 ? bx : edge === 1 ? cx : ax;
    const jy = edge === 0 ? by : edge === 1 ? cy : ay;
    const dx = jx - ix;
    const dy = jy - iy;
    const len2 = dx * dx + dy * dy;
    let t = len2 > 0 ? ((px - ix) * dx + (py - iy) * dy) / len2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const ex = px - (ix + dx * t);
    const ey = py - (iy + dy * t);
    const d = Math.sqrt(ex * ex + ey * ey);
    if (d < best) {
      best = d;
      out[0] = d;
      out[1] = edge === 0 ? 1 - t : edge === 1 ? 0 : t;
      out[2] = edge === 0 ? t : edge === 1 ? 1 - t : 0;
      out[3] = edge === 0 ? 0 : edge === 1 ? t : 1 - t;
    }
  }
  return best;
}

/**
 * The point of a triangle (or segment) the screen shows at barycentric weights
 * `w`, in the mesh's own coordinates and at the depth the camera sees it.  A
 * perspective picture is linear in 1/w, not in the weights, so each weight is
 * divided by its vertex's clip-space w first.
 */
function lift(positions, ids, w, sw, out) {
  let total = 0;
  let x = 0;
  let y = 0;
  let z = 0;
  for (let k = 0; k < ids.length; k += 1) {
    const id = ids[k];
    const share = w[k] / sw[id];
    total += share;
    x += share * positions[3 * id];
    y += share * positions[3 * id + 1];
    z += share * positions[3 * id + 2];
  }
  if (!(total > 0)) {
    // Every weight was zero (cannot happen for a real triangle); the first vertex.
    const id = ids[0];
    out[0] = positions[3 * id]; out[1] = positions[3 * id + 1]; out[2] = positions[3 * id + 2];
    return sw[id];
  }
  out[0] = x / total; out[1] = y / total; out[2] = z / total;
  return 1 / total;
}

/**
 * The triangle of a mesh nearest the pointer on the screen, or null when none
 * comes within `reach` pixels.  `index` is the triangle list (three vertex numbers
 * each), or null when the positions are already in threes.
 *
 * Returns `{ distance, triangle, weights, depth, local }`: how far from the
 * pointer (0 when it is inside the triangle), the three vertex numbers, the
 * weights of the nearest point on it, its depth along the view axis, and where it
 * is in the mesh's own coordinates.  When several triangles cover the pointer the
 * nearest to the camera wins.  `accept(local)` may turn a triangle down (a point
 * the Z range or the crop has cut away), and the search goes on.  `depthOf(local)`
 * is how far a point is along the view axis; without it the clip-space w is, which
 * is that for a perspective camera and a constant for an orthographic one.
 */
export function nearestTriangle(positions, index, matrix, size, point, reach,
  { accept = null, depthOf = null } = {}) {
  const { sx, sy, sw } = projectPositions(positions, matrix, size.width, size.height);
  const count = Math.floor((index ? index.length : sx.length) / 3);
  const px = point[0];
  const py = point[1];
  const here = [0, 0, 0];
  let limit = reach;
  let best = null;
  for (let t = 0; t < count; t += 1) {
    const a = index ? index[3 * t] : 3 * t;
    const b = index ? index[3 * t + 1] : 3 * t + 1;
    const c = index ? index[3 * t + 2] : 3 * t + 2;
    if (!(sw[a] > BEHIND && sw[b] > BEHIND && sw[c] > BEHIND)) continue;
    const ax = sx[a]; const bx = sx[b]; const cx = sx[c];
    const lo = px - limit;
    const hi = px + limit;
    if ((ax < lo && bx < lo && cx < lo) || (ax > hi && bx > hi && cx > hi)) continue;
    const ay = sy[a]; const by = sy[b]; const cy = sy[c];
    const top = py - limit;
    const bottom = py + limit;
    if ((ay < top && by < top && cy < top) || (ay > bottom && by > bottom && cy > bottom)) continue;
    const d = onTriangle(px, py, ax, ay, bx, by, cx, cy, found);
    if (d > limit) continue;
    const weights = [found[1], found[2], found[3]];
    let depth = lift(positions, [a, b, c], weights, sw, here);
    if (depthOf) depth = depthOf(here);
    if (best && !(d < best.distance || (d === best.distance && depth < best.depth))) continue;
    if (accept && !accept(here.slice())) continue;
    best = { distance: d, triangle: [a, b, c], weights, depth, local: here.slice() };
    limit = d;
  }
  return best;
}

/**
 * The line segment nearest the pointer on the screen, or null when none comes
 * within `reach` pixels.  `positions` holds the two ends of each segment, as a
 * THREE.LineSegments does; `from` and `to` bound the segments looked at (the
 * layer's draw range: what is not drawn is not there to be pointed at).
 *
 * Returns `{ distance, segment, t, depth, local }`, `t` being how far along the
 * segment (0 to 1) the nearest point lies.  `accept` and `depthOf` are as for
 * `nearestTriangle`.
 */
export function nearestSegment(positions, matrix, size, point, reach,
  { from = 0, to = Infinity, accept = null, depthOf = null } = {}) {
  const { sx, sy, sw } = projectPositions(positions, matrix, size.width, size.height);
  const total = Math.floor(sx.length / 2);
  const last = Math.min(total, to);
  const px = point[0];
  const py = point[1];
  const here = [0, 0, 0];
  let limit = reach;
  let best = null;
  for (let s = Math.max(0, from); s < last; s += 1) {
    const a = 2 * s;
    const b = 2 * s + 1;
    if (!(sw[a] > BEHIND && sw[b] > BEHIND)) continue;
    const ax = sx[a]; const bx = sx[b];
    const lo = px - limit;
    const hi = px + limit;
    if ((ax < lo && bx < lo) || (ax > hi && bx > hi)) continue;
    const ay = sy[a]; const by = sy[b];
    const top = py - limit;
    const bottom = py + limit;
    if ((ay < top && by < top) || (ay > bottom && by > bottom)) continue;
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy;
    let t = len2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const ex = px - (ax + dx * t);
    const ey = py - (ay + dy * t);
    const d = Math.sqrt(ex * ex + ey * ey);
    if (d > limit) continue;
    let depth = lift(positions, [a, b], [1 - t, t], sw, here);
    if (depthOf) depth = depthOf(here);
    if (best && !(d < best.distance || (d === best.distance && depth < best.depth))) continue;
    if (accept && !accept(here.slice())) continue;
    best = { distance: d, segment: s, t, depth, local: here.slice() };
    limit = d;
  }
  return best;
}

/**
 * The matrix `nearestTriangle` and `nearestSegment` take, from the three that make
 * it, each the sixteen numbers of a column-major 4x4: projection * view * model.
 * (three.js has Matrix4.multiplyMatrices; this is for callers that have no
 * three.js, the tests.)
 */
export function multiply4(a, b) {
  const out = new Array(16).fill(0);
  for (let col = 0; col < 4; col += 1) {
    for (let row = 0; row < 4; row += 1) {
      let sum = 0;
      for (let k = 0; k < 4; k += 1) sum += a[k * 4 + row] * b[col * 4 + k];
      out[col * 4 + row] = sum;
    }
  }
  return out;
}
