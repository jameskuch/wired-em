// Where a field on a triangle mesh crosses a level: pure functions, no DOM and no
// three.js, so node can test them (tests/js/test_levelset.mjs) and Render compare
// can draw what they return through `Renderer3D.setLines`.  Two uses, one routine:
//
//   * the SLICE CONTOUR of a surface at a plane -- the field is a coordinate (Z by
//     default) and the level a section's height: the outline of the cell as that
//     section cuts it, to draw against the annotation circles and outlines;
//   * the ZERO LINE of a signed field -- the field is `distance_um` of a difference
//     (positive where mesh A lies outside mesh B) and the level 0: the curve where
//     the two surfaces cross, to draw over mesh A.
//
// The routine is marching triangles on a per-vertex scalar.  A vertex is on the
// positive side when `field >= level` (`side: 'above'`, the default), with no
// tolerance and no special case: a vertex exactly on the level is positive, so a
// surface touching the level in a vertex or a whole face (a cap of a closed-curve
// cell sits exactly on its section's plane) still has a well defined, closed contour
// -- the boundary between "at or above" and "below" -- and nothing needs nudging off
// the level.
//
// Which cap that holds for depends on the rule.  The TOP cap of a closed-curve cell
// (the wall below it, the cap on the plane) is cut along its rim.  The BOTTOM cap
// (the wall above it) is wholly on the positive side with everything around it, so a
// level exactly at the height of a bottom cap gives an EMPTY contour.  Measured on the
// real closed-curve surface of Inferior c1178 (648,012 vertices, 4,856 vertex heights,
// 2026-10-16): the level of its lowest vertex, 99.855 um, gave 0 segments by the default
// rule and one closed loop of 370 by `side: 'below'`; 10 nm above it, 752 either way; and
// it was the only one of the 4,856 exact vertex heights with an empty contour.  That
// surface has one stack; a cell with a second piece, or a branch that starts at a section
// above the lowest one, has a bottom cap at every such section, and each is empty by the
// default rule too.  `side: 'below'` turns the rule round for the vertices exactly on the
// level (`field > level` is positive): the bottom cap is then cut along its rim and the top
// cap gives nothing.  A section's outline is wanted at both ends of every stack, so
// `sectionContour` takes the union of the two rules (see there).
//
// Each crossing triangle gives one segment between two points on its edges, found
// by linear interpolation of the field along them (exact for a linear field).
// Segments are stored as float64 (`positions`) and narrowed to float32 once for the
// renderer (`segments`).  A segment shorter than `SLIVER_UM` (a vertex on the level to
// within rounding, seen from two of its triangles) is dropped: it has no length to draw.
//
// Two things can ride along with the segments, from the same triangles and by the same
// rule, so a caller that wants them never repeats the marching: another per-vertex field,
// interpolated along the same edges (`values`: the error of a render at both ends of every
// segment), and the SIDE of each segment the solid is on (`axis`: for a slice of a surface
// wound outward, the face's outward normal projected into the plane tells which way the
// segment runs, so the loops of a cut run counterclockwise about a body and clockwise about
// a hole, and a signed count over them is the number of bodies that enclose a point -- see
// slice.js).
//
// A closed surface gives only closed contours; an open one gives open polylines that
// end on its boundary.  `contourLoops` joins the segments end to end *by position*
// (to a picometer), not by vertex index, so a surface whose vertices are repeated
// along a seam (a mesh assembled from pieces) still comes out in one piece where its
// pieces agree.
//
// Lengths are micrometers in whatever frame the mesh is in.  Positions stay float64
// until the one narrowing to the renderer's buffer, per the codebase's precision rule.

/** Two points closer than this (um, 1 pm) are the same point when segments are joined. */
export const JOIN_UM = 1e-6;
/**
 * A segment shorter than this (um, 1 fm) is not drawn or joined: it is what the
 * rounding of a vertex that lies exactly on the level leaves behind (1e-16 to 1e-14).
 * Far below `JOIN_UM`, so a fan of such pieces around one vertex cannot add up to a
 * gap the join would not close.  The vertices of a real mesh are only good to about
 * 1e-6 um (float32), so a vertex *near* the level leaves pieces of that size, and
 * those are kept: the join treats them as the one point they are.
 */
export const SLIVER_UM = 1e-9;
/** The most Z bins the face index may have (a thin cell on a fine bin would need more). */
export const MAX_BINS = 8192;

/** A growable float64 buffer. */
class Growable {
  constructor(capacity = 1024) {
    this.data = new Float64Array(capacity);
    this.length = 0;
  }

  push6(a, b, c, d, e, f) {
    if (this.length + 6 > this.data.length) {
      const bigger = new Float64Array(this.data.length * 2);
      bigger.set(this.data);
      this.data = bigger;
    }
    const x = this.data;
    const n = this.length;
    x[n] = a; x[n + 1] = b; x[n + 2] = c; x[n + 3] = d; x[n + 4] = e; x[n + 5] = f;
    this.length = n + 6;
  }

  finish() { return this.data.slice(0, this.length); }
}

/**
 * The level set of a per-vertex scalar on a triangle mesh.
 *
 * @param vertices  flat x, y, z per vertex (a MeshDTO's `vertices`, number[] or typed)
 * @param faces     flat vertex indices, three per face
 * @param field     one number per vertex
 * @param level     the value to cross (default 0)
 * @param faceIds   only these faces (a Uint32Array, ascending), or all of them
 * @param side      which side a vertex exactly on the level belongs to: `'above'`
 *   (the default; `field >= level` is the positive side) or `'below'` (`field >
 *   level`).  It changes nothing for a vertex off the level, and nothing at all for
 *   a surface that touches the level nowhere in a vertex.
 * @param values    another per-vertex number (the error of a render), interpolated along the
 *   same edge at both ends of every segment, or null
 * @param axis      0, 1 or 2 when `field` is that coordinate (the cut is a plane across the
 *   axis) and the segments' sides are wanted, else null.  With the plane's own two axes taken
 *   as (u, v) = ((axis + 1) % 3, (axis + 2) % 3), right-handed with the axis, a segment gets +1
 *   where the face's outward normal, projected into the plane, lies to the RIGHT of the way the
 *   segment runs (first end to second), -1 where it lies to the left, and 0 where the face
 *   has no horizontal normal.  The faces must be wound outward (every mesh here is): the solid
 *   is then on the left of every +1 segment, and a closed cut of a body, followed in the
 *   direction of its +1 segments, runs counterclockwise (seen from the positive end of the axis).
 * @returns `{ level, count, positions, segments, values, orient }`: `count` segments;
 *   `positions` float64, six numbers per segment (x0, y0, z0, x1, y1, z1); `segments` the same
 *   as float32, the buffer `Renderer3D.setLines` takes; `values` float64, two per segment, or
 *   null; `orient` an Int8Array, one per segment, or null.  A face with a field value
 *   that is not a finite number contributes nothing.
 */
export function levelSet(vertices, faces, field, level = 0, {
  faceIds = null, side = 'above', values = null, axis = null,
} = {}) {
  if (side !== 'above' && side !== 'below') throw new RangeError(`side is 'above' or 'below', not ${side}`);
  if (axis !== null && axis !== 0 && axis !== 1 && axis !== 2) throw new RangeError(`axis is 0, 1 or 2, not ${axis}`);
  const strict = side === 'below';
  const out = new Growable();
  const carried = values ? [] : null;
  const sides = axis !== null ? [] : null;
  const u = axis === null ? 0 : (axis + 1) % 3;
  const v = axis === null ? 0 : (axis + 2) % 3;
  const n = faceIds ? faceIds.length : Math.floor(faces.length / 3);
  for (let k = 0; k < n; k += 1) {
    const f = faceIds ? faceIds[k] : k;
    const a = faces[3 * f];
    const b = faces[3 * f + 1];
    const c = faces[3 * f + 2];
    const sa = field[a] - level;
    const sb = field[b] - level;
    const sc = field[c] - level;
    const pa = strict ? sa > 0 : sa >= 0;
    const pb = strict ? sb > 0 : sb >= 0;
    const pc = strict ? sc > 0 : sc >= 0;
    if (pa === pb && pb === pc) continue;
    if (!(Number.isFinite(sa) && Number.isFinite(sb) && Number.isFinite(sc))) continue;
    // Two of the three edges cross; their crossing points, in the order ab, bc, ca.
    let have = 0;
    if (pa !== pb) { crossing(vertices, a, b, sa, sb, SCRATCH, 3 * have, values, SCRATCH_VALUES, have); have += 1; }
    if (pb !== pc) { crossing(vertices, b, c, sb, sc, SCRATCH, 3 * have, values, SCRATCH_VALUES, have); have += 1; }
    if (pc !== pa) { crossing(vertices, c, a, sc, sa, SCRATCH, 3 * have, values, SCRATCH_VALUES, have); have += 1; }
    const dx = SCRATCH[3] - SCRATCH[0];
    const dy = SCRATCH[4] - SCRATCH[1];
    const dz = SCRATCH[5] - SCRATCH[2];
    if (dx * dx + dy * dy + dz * dz >= SLIVER_UM * SLIVER_UM) {
      out.push6(SCRATCH[0], SCRATCH[1], SCRATCH[2], SCRATCH[3], SCRATCH[4], SCRATCH[5]);
      if (carried) carried.push(SCRATCH_VALUES[0], SCRATCH_VALUES[1]);
      if (sides) sides.push(sideOf(vertices, a, b, c, SCRATCH, u, v));
    }
  }
  const positions = out.finish();
  return {
    level,
    count: positions.length / 6,
    positions,
    segments: Float32Array.from(positions),
    values: carried ? Float64Array.from(carried) : null,
    orient: sides ? Int8Array.from(sides) : null,
  };
}

/** The two crossing points of the face being looked at (x, y, z, x, y, z), and the field there. */
const SCRATCH = new Float64Array(6);
const SCRATCH_VALUES = new Float64Array(2);

/**
 * Where the edge i-j crosses the level (its ends are `si` and `sj` from it, one at or above, one
 * below), into `into` at `o`; with `values`, the interpolated field there into `valuesInto[slot]`.
 */
function crossing(vertices, i, j, si, sj, into, o, values = null, valuesInto = null, slot = 0) {
  const t = si / (si - sj);
  const x = vertices[3 * i];
  const y = vertices[3 * i + 1];
  const z = vertices[3 * i + 2];
  into[o] = x + t * (vertices[3 * j] - x);
  into[o + 1] = y + t * (vertices[3 * j + 1] - y);
  into[o + 2] = z + t * (vertices[3 * j + 2] - z);
  if (values) valuesInto[slot] = values[i] + t * (values[j] - values[i]);
}

/**
 * Which side of the segment `points` (x, y, z, x, y, z) the face a-b-c's outward normal lies on,
 * in the plane across the axis whose other two are (u, v): +1 to the right of the way the segment
 * runs, -1 to the left, 0 for none.  The normal is (b - a) x (c - a), the winding every mesh
 * here has; its part in the plane against the segment's direction is the z of their cross product.
 */
function sideOf(vertices, a, b, c, points, u, v) {
  const e1u = vertices[3 * b + u] - vertices[3 * a + u];
  const e1v = vertices[3 * b + v] - vertices[3 * a + v];
  const e1w = vertices[3 * b + 3 - u - v] - vertices[3 * a + 3 - u - v];
  const e2u = vertices[3 * c + u] - vertices[3 * a + u];
  const e2v = vertices[3 * c + v] - vertices[3 * a + v];
  const e2w = vertices[3 * c + 3 - u - v] - vertices[3 * a + 3 - u - v];
  // n = e1 x e2 in (u, v, axis) coordinates, which are right-handed.
  const nu = e1v * e2w - e1w * e2v;
  const nv = e1w * e2u - e1u * e2w;
  const s = nu * (points[3 + v] - points[v]) - nv * (points[3 + u] - points[u]);
  return s > 0 ? 1 : s < 0 ? -1 : 0;
}

/** The zero line of a signed field: where mesh A crosses mesh B (see `levelSet`). */
export function zeroLine(vertices, faces, field, { level = 0 } = {}) {
  return levelSet(vertices, faces, field, level);
}

// -- the slice contour: a face index along one axis ------------------------------

/**
 * A mesh's faces binned along one axis (Z by default), so the contour at a plane
 * looks at the few faces that reach it and not at all of them: 0.2 to 0.4 ms
 * against 10 ms by full scan on a 400,000-face mesh.  Build it once per mesh.
 *
 * A face is listed in every bin its extent along the axis touches, so the bin a
 * level falls in holds every face that crosses it (and some that do not, which
 * the level set then passes over): the result is the full scan's, segment for
 * segment, in the same order.
 *
 * @param binUm  the height of a bin; by default the mean extent of a face along
 *   the axis (so a face is in about two bins), but no finer than the range over
 *   `MAX_BINS`.
 */
export function buildSliceIndex(vertices, faces, { axis = 2, binUm = null } = {}) {
  const nv = Math.floor(vertices.length / 3);
  const coord = new Float64Array(nv);
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < nv; i += 1) {
    const v = vertices[3 * i + axis];
    coord[i] = v;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const nf = Math.floor(faces.length / 3);
  if (!(hi >= lo) || nf === 0) {
    return { vertices, faces, axis, coord, min: 0, bin: 1, nBins: 0, start: new Uint32Array(1), ids: new Uint32Array(0) };
  }
  const range = hi - lo;
  let bin = binUm;
  if (!(bin > 0)) {
    let sum = 0;
    for (let f = 0; f < nf; f += 1) {
      const p = coord[faces[3 * f]];
      const q = coord[faces[3 * f + 1]];
      const r = coord[faces[3 * f + 2]];
      sum += Math.max(p, q, r) - Math.min(p, q, r);
    }
    bin = sum / nf;
  }
  bin = Math.max(bin, range / MAX_BINS, 1e-9);
  const nBins = Math.floor(range / bin) + 1;
  const first = new Int32Array(nf);
  const last = new Int32Array(nf);
  const start = new Uint32Array(nBins + 1);
  for (let f = 0; f < nf; f += 1) {
    const p = coord[faces[3 * f]];
    const q = coord[faces[3 * f + 1]];
    const r = coord[faces[3 * f + 2]];
    const b0 = Math.floor((Math.min(p, q, r) - lo) / bin);
    const b1 = Math.floor((Math.max(p, q, r) - lo) / bin);
    first[f] = b0;
    last[f] = b1;
    for (let b = b0; b <= b1; b += 1) start[b + 1] += 1;
  }
  for (let b = 0; b < nBins; b += 1) start[b + 1] += start[b];
  const filled = new Uint32Array(nBins);
  const ids = new Uint32Array(start[nBins]);
  for (let f = 0; f < nf; f += 1) {
    for (let b = first[f]; b <= last[f]; b += 1) ids[start[b] + filled[b]++] = f;
  }
  return { vertices, faces, axis, coord, min: lo, bin, nBins, start, ids };
}

/** The faces that can cross the plane at `level`: the bin it falls in (empty outside the mesh). */
export function sliceFaces(index, level) {
  if (!index.nBins || !Number.isFinite(level)) return new Uint32Array(0);
  const b = Math.floor((level - index.min) / index.bin);
  if (b < 0 || b >= index.nBins) return new Uint32Array(0);
  return index.ids.subarray(index.start[b], index.start[b + 1]);
}

/**
 * The contour of the surface at the plane `axis = level`: a `levelSet` over the index's
 * faces.  `side` is the `levelSet` rule for vertices exactly on the level; with the
 * default a cap lying on the plane is cut along its rim when it is the top of the
 * surface and not at all when it is the bottom.  `values` (one number per vertex) is
 * carried along the segments, and `orient` also gives each segment its side (see `levelSet`:
 * the index's own axis).
 */
export function sliceContour(index, level, { side = 'above', values = null, orient = false } = {}) {
  return levelSet(index.vertices, index.faces, index.coord, level, {
    faceIds: sliceFaces(index, level), side, values, axis: orient ? index.axis : null,
  });
}

/**
 * The outline of the surface as the section at `level` cuts it: every cap lying on the
 * plane is cut along its rim, the bottom ones as well as the top ones, wherever in the
 * surface they are.  It is `sliceContour` (the top caps, the walls) plus the segments the
 * other rule, `side: 'below'`, finds (the bottom caps), each kept once: where a rim edge
 * has the surface on both sides of the plane the two rules find the same segment, and the
 * second is dropped.  The segments are compared by position (both ends within `JOIN_UM`,
 * in either order), not by vertex, so a rim repeated along a seam is not drawn twice.
 *
 * At a level that is not the height of a vertex of the faces it looks at, nothing lies
 * on the plane and the result is `sliceContour`'s, segment for segment (and the second rule
 * is not run).  The level is compared exactly: a section height that misses a cap's plane
 * by a rounding has no cap to cut, whichever rule.
 *
 * `values` and `orient` are as for `sliceContour`; the segments added from the second rule
 * bring theirs along, so the section view's contour (slice.js) is this routine's own, not a
 * copy of its rule.
 */
export function sectionContour(index, level, { values = null, orient = false } = {}) {
  const faceIds = sliceFaces(index, level);
  const axis = orient ? index.axis : null;
  const above = levelSet(index.vertices, index.faces, index.coord, level, { faceIds, side: 'above', values, axis });
  if (!faceIds.length || !touchesLevel(index, faceIds, level)) return above;
  const below = levelSet(index.vertices, index.faces, index.coord, level, { faceIds, side: 'below', values, axis });
  if (!below.count) return above;
  const added = segmentsNotIn(below, above);
  if (!added.length) return above;
  const count = above.count + added.length;
  const positions = new Float64Array(6 * count);
  positions.set(above.positions);
  added.forEach((s, k) => positions.set(below.positions.subarray(6 * s, 6 * s + 6), above.positions.length + 6 * k));
  let carried = null;
  if (values) {
    carried = new Float64Array(2 * count);
    carried.set(above.values);
    added.forEach((s, k) => carried.set(below.values.subarray(2 * s, 2 * s + 2), above.values.length + 2 * k));
  }
  let sides = null;
  if (axis !== null) {
    sides = new Int8Array(count);
    sides.set(above.orient);
    added.forEach((s, k) => { sides[above.count + k] = below.orient[s]; });
  }
  return { level, count, positions, segments: Float32Array.from(positions), values: carried, orient: sides };
}

/** True when some vertex of the given faces lies exactly on the level. */
function touchesLevel(index, faceIds, level) {
  const { faces, coord } = index;
  for (let k = 0; k < faceIds.length; k += 1) {
    const f = faceIds[k];
    if (coord[faces[3 * f]] === level || coord[faces[3 * f + 1]] === level || coord[faces[3 * f + 2]] === level) return true;
  }
  return false;
}

/**
 * The segments of `set` that `other` does not already have: both ends within `JOIN_UM` of a
 * segment of `other`'s, in either order, is the same segment.  `other`'s segments are
 * bucketed by their midpoints in cells a thousand tolerances wide and a segment is looked
 * for in the 27 cells around its own midpoint, so a pair a hair apart is found whichever side
 * of a bucket's edge it falls on.  Returns indices into `set`.
 */
function segmentsNotIn(set, other) {
  const cell = JOIN_UM * 1024;
  const reach2 = JOIN_UM * JOIN_UM;
  const cellOf = (v) => Math.floor(v / cell);
  const midpoint = (p, s, axis) => 0.5 * (p[6 * s + axis] + p[6 * s + 3 + axis]);
  const buckets = new Map();
  const op = other.positions;
  for (let t = 0; t < other.count; t += 1) {
    const key = `${cellOf(midpoint(op, t, 0))},${cellOf(midpoint(op, t, 1))},${cellOf(midpoint(op, t, 2))}`;
    const list = buckets.get(key);
    if (list) list.push(t); else buckets.set(key, [t]);
  }
  const near = (p, i, q, j) => {
    const dx = p[i] - q[j]; const dy = p[i + 1] - q[j + 1]; const dz = p[i + 2] - q[j + 2];
    return dx * dx + dy * dy + dz * dz <= reach2;
  };
  const sp = set.positions;
  const fresh = [];
  for (let s = 0; s < set.count; s += 1) {
    const cx = cellOf(midpoint(sp, s, 0)); const cy = cellOf(midpoint(sp, s, 1)); const cz = cellOf(midpoint(sp, s, 2));
    let found = false;
    for (let dx = -1; dx <= 1 && !found; dx += 1) {
      for (let dy = -1; dy <= 1 && !found; dy += 1) {
        for (let dz = -1; dz <= 1 && !found; dz += 1) {
          const list = buckets.get(`${cx + dx},${cy + dy},${cz + dz}`);
          if (!list) continue;
          found = list.some((t) => (near(sp, 6 * s, op, 6 * t) && near(sp, 6 * s + 3, op, 6 * t + 3))
            || (near(sp, 6 * s, op, 6 * t + 3) && near(sp, 6 * s + 3, op, 6 * t)));
        }
      }
    }
    if (!found) fresh.push(s);
  }
  return fresh;
}

// -- joining segments into loops -----------------------------------------------------

/**
 * The segments of a level set joined end to end: `[{ points, closed }]`, each
 * `points` a float64 array of x, y, z in order along the curve.  A closed loop
 * does not repeat its first point.  Two segment ends are the same point when they
 * agree to `joinUm`; where three or more meet (a pinch) the walk takes them in
 * the order found.  Longest first.
 *
 * With `set.orient` (the sides of the segments, see `levelSet`) a CLOSED loop comes out
 * in the direction most of its segments say: the solid on its left, so the cut of a body
 * runs counterclockwise (seen from the positive end of the axis) and the cut of a hole
 * clockwise, whichever segment the walk began on.  An open curve keeps the walk's direction.
 */
export function contourLoops(set, { joinUm = JOIN_UM } = {}) {
  const n = set.count;
  if (!n) return [];
  const pos = set.positions;
  const orient = set.orient || null;
  // Nodes: the distinct segment ends, two being one when they lie within `joinUm`.  Ends
  // are bucketed in cells a thousand tolerances wide, and an end near a cell's edge also
  // looks in the cell across it, so a pair a hair apart is found whichever side of a
  // boundary it falls on (rounding to a grid would split some pairs, at random).
  const cell = joinUm * 1024;
  const reach2 = joinUm * joinUm;
  const buckets = new Map();                 // hashed cell -> node ids
  const nodes = [];                          // x, y, z per node
  const hash = (a, b, c) => (Math.imul(a, 73856093) ^ Math.imul(b, 19349663) ^ Math.imul(c, 83492791));
  const cellsAround = (v) => {
    const c = Math.floor(v / cell);
    const f = v - c * cell;
    if (f < joinUm) return [c, c - 1];
    if (cell - f < joinUm) return [c, c + 1];
    return [c];
  };
  const nodeAt = (x, y, z) => {
    for (const a of cellsAround(x)) {
      for (const b of cellsAround(y)) {
        for (const c of cellsAround(z)) {
          const list = buckets.get(hash(a, b, c));
          if (!list) continue;
          for (const id of list) {
            const dx = nodes[3 * id] - x;
            const dy = nodes[3 * id + 1] - y;
            const dz = nodes[3 * id + 2] - z;
            if (dx * dx + dy * dy + dz * dz <= reach2) return id;
          }
        }
      }
    }
    const id = nodes.length / 3;
    nodes.push(x, y, z);
    const home = hash(Math.floor(x / cell), Math.floor(y / cell), Math.floor(z / cell));
    const list = buckets.get(home);
    if (list) list.push(id); else buckets.set(home, [id]);
    return id;
  };
  const ends = new Int32Array(2 * n);
  const adjacent = [];                       // node -> [segment * 2 + end, ...]
  let kept = 0;
  for (let s = 0; s < n; s += 1) {
    const a = nodeAt(pos[6 * s], pos[6 * s + 1], pos[6 * s + 2]);
    const b = nodeAt(pos[6 * s + 3], pos[6 * s + 4], pos[6 * s + 5]);
    if (a === b) { ends[2 * s] = -1; ends[2 * s + 1] = -1; continue; }      // within a picometer: no length
    ends[2 * s] = a; ends[2 * s + 1] = b;
    (adjacent[a] ||= []).push(2 * s);
    (adjacent[b] ||= []).push(2 * s + 1);
    kept += 1;
  }
  if (!kept) return [];
  const used = new Uint8Array(n);
  let turn = 0;                              // the last walk: segments walked the way they say, less those against

  /** Walk from segment `s` out of its end `exit`, marking what it visits; the nodes passed, in order. */
  const walk = (s, exit) => {
    const path = [];
    let seg = s;
    let out = exit;
    used[seg] = 1;
    turn = 0;
    for (;;) {
      if (orient) turn += (out === 1 ? 1 : -1) * orient[seg];
      const node = ends[2 * seg + out];
      path.push(node);
      const next = (adjacent[node] || []).find((slot) => !used[slot >> 1]);
      if (next === undefined) return path;
      seg = next >> 1;
      out = 1 - (next & 1);
      used[seg] = 1;
    }
  };

  const loops = [];
  for (let s = 0; s < n; s += 1) {
    if (used[s] || ends[2 * s] < 0) continue;
    const forward = walk(s, 1);
    const forwardTurn = turn;
    const startNode = ends[2 * s];
    let nodeIds;
    let closed = false;
    if (forward[forward.length - 1] === startNode) {
      closed = true;                          // came back to where it began: the last node is the first
      const around = forward.slice(0, -1);
      // Against the way the segments say: the same loop the other way round, from the same node.
      nodeIds = [startNode, ...(forwardTurn < 0 ? around.reverse() : around)];
    } else {
      // An open curve: walk the other way from the start and put the two halves together.
      const backward = walk(s, 0).slice(1);   // walk(s, 0) re-lists the start's own far end first
      nodeIds = [...backward.reverse(), startNode, ...forward];
    }
    const points = new Float64Array(nodeIds.length * 3);
    nodeIds.forEach((id, k) => {
      points[3 * k] = nodes[3 * id]; points[3 * k + 1] = nodes[3 * id + 1]; points[3 * k + 2] = nodes[3 * id + 2];
    });
    loops.push({ points, closed });
  }
  return loops.sort((p, q) => q.points.length - p.points.length);
}

/** Segment pairs (the buffer `setLines` takes) from loops: every edge, a closed loop's last one back to its first. */
export function loopsToSegments(loops) {
  let total = 0;
  for (const loop of loops) {
    const m = loop.points.length / 3;
    total += loop.closed ? m : m - 1;
  }
  const out = new Float32Array(total * 6);
  let o = 0;
  for (const loop of loops) {
    const p = loop.points;
    const m = p.length / 3;
    const edges = loop.closed ? m : m - 1;
    for (let k = 0; k < edges; k += 1) {
      const a = 3 * k;
      const b = 3 * ((k + 1) % m);
      out[o++] = p[a]; out[o++] = p[a + 1]; out[o++] = p[a + 2];
      out[o++] = p[b]; out[o++] = p[b + 1]; out[o++] = p[b + 2];
    }
  }
  return out;
}

/** The length of a loop (a closed one includes the edge back to its start), in the mesh's units. */
export function loopLength(loop) {
  const p = loop.points;
  const m = p.length / 3;
  let length = 0;
  for (let k = 0; k + 1 < m; k += 1) {
    length += Math.hypot(p[3 * k + 3] - p[3 * k], p[3 * k + 4] - p[3 * k + 1], p[3 * k + 5] - p[3 * k + 2]);
  }
  if (loop.closed && m > 1) {
    length += Math.hypot(p[0] - p[3 * m - 3], p[1] - p[3 * m - 2], p[2] - p[3 * m - 1]);
  }
  return length;
}
