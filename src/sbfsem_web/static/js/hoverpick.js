// The annotation the pick readout names while the pointer moves over the view
// (asked for 2026-10-22): its Viking X Y Z and Location ID follow the pointer,
// always the annotation nearest it, and nothing is drawn to show which one -- that
// is what selecting is for.  Plain arithmetic with no three.js in it, so node can
// run it (tests/js/test_hoverpick.mjs); the renderer hands over the pointer's ray
// in each cell's own frame, and how deep along it the surface drawn there is.
//
// The 3D render names what the pointer is on, nearest the viewer:
//
// - On a surface (one at least half opaque: the 3D render's are 0.92 by default),
//   the annotation nearest the point of it under the pointer (its disc's distance,
//   as a click's pick measures it).  The surface is read
//   from what was drawn, not from the discs: on Inferior Monkey c16137 at 0.09 um a
//   pixel, of 398 points on its surfaces, discs alone named an annotation over 5 um
//   from the surface point at 11 to 23 of them -- a soma's surface swells up to 2 um
//   past its discs, so a ray met none of them there, and a thin process passing a
//   few pixels in front took the readout from the soma under the pointer.
// - Otherwise the first disc the ray meets: a cell drawn as rings, skeleton or a
//   surface mostly seen through, in front of any surface.  A Viking annotation is a
//   disc in its section plane, not a sphere; each is widened by a reach so that it
//   stands for the tube around it, HOVER_REACH_UM across and HOVER_REACH_Z_UM up
//   and down, or HOVER_REACH_PX at its depth when that is more.  Linked annotations
//   of c943 and c1178 are one 0.09 um section apart in Z (median and 90th
//   percentile; 0.19 um at the 99th), and in X and Y 0.24 to 0.35 um at the median
//   and 0.9 to 1.0 um at the 90th, radii not taken off: the reaches close the gap
//   between nearly every linked pair, so a ray does not go between two rings of a
//   process and name one behind it.  Discs met within half a pixel of the same
//   depth (a stack seen from the side) are told apart by how near the ray passes
//   each, so the readout steps section by section as the pointer moves.
// - On nothing, the annotation nearest the pointer on the screen.
//
// With annotation rings drawn, a ring the pointer is plainly on comes before all of
// that (`ringUnder`, 2026-10-30): the one ring within ON_RING_PX of the pointer, not
// behind the surface drawn there.  Close in, seen from the side, rings stand a section
// (2 px) apart, and the surface rule named one two sections from the ring the pointer
// was on (Inferior Monkey c943, 31471 for 31428), so a triple click could not choose
// that ring; at a whole cell's zoom, one five sections off (c1178, section 1092 with
// the pointer on 1097's ring).  Where two rings are within a pixel of the pointer, the
// surface rule decides between them as before.
//
// GraphApp names the dot nearest the pointer, as its click does (markers.js
// `dotChoices`): there is no surface, and what is drawn at an annotation is its
// dot.  Dots within half a pixel of the nearest are equally near; the one nearer
// the viewer, drawn over the others, is named.
//
// `cells` is a list of `{ origin, dir, nodes, solid, ... }`: the ray's origin and
// its unit direction in the cell's own frame (microns), the cell's annotations as
// the server's /nodes lists them (`xyz_um`, `radius_um`), and whether the cell is
// drawn with a surface that hides what is behind it.  A ray parameter t is microns along the ray from
// its origin, the same for every cell (a volume's frame is the drawn one moved and
// perhaps mirrored, never scaled).  `view`:
//
//   look         'render' or 'graph'
//   perspective  true for a perspective camera
//   scale        microns per screen pixel: per micron along the ray (perspective),
//                or the same everywhere (orthographic)
//   near         the least t in front of the camera (ignored for orthographic)
//   surface      t of the surface drawn under the pointer, or null
//   reachUm, reachZUm, reachPx   the reaches, when not HOVER_REACH_UM, HOVER_REACH_Z_UM
//                and HOVER_REACH_PX
//
// `accept(cell, node)` says whether an annotation is shown at all (not cut away by
// the crop or its cell's Z range); it is asked only of one that could still win.

/** How near the pointer, in screen pixels, a disc counts as under it. */
export const HOVER_REACH_PX = 3;
/** How far across, in microns, a disc reaches toward its neighbors in the same section. */
export const HOVER_REACH_UM = 0.5;
/** How far up and down, in microns, a disc reaches toward the sections next to it. */
export const HOVER_REACH_Z_UM = 0.1;
/** Two annotations this close on the screen, or in depth, are equally near. */
export const HOVER_TIE_PX = 0.5;

/** A ring this near the pointer, in screen pixels, is under it (`ringUnder`): its drawn line is a pixel wide. */
export const ON_RING_PX = 1;
/**
 * How far behind the surface drawn under the pointer, in microns, an annotation's disc
 * may begin and still be under the pointer.  A surface swells up to 2 um past the discs
 * it is drawn around (above), so a disc that begins nearer is one of those; one that
 * begins farther is behind the surface, out of sight, though a ring drawn through the
 * surface shows it.
 */
export const HIDDEN_BEHIND_UM = 2;

const EPS = 1e-12;

/**
 * Where a ray from the origin along (dx, dy, dz) enters a flat cylinder about
 * (cx, cy, cz) (relative to the origin) of radius `radius` and half height `half`,
 * or NaN when it misses it or meets it only at or behind `near`.  The cylinder's
 * axis is Z: an annotation's disc lies in its section.
 */
export function discEntry(cx, cy, cz, dx, dy, dz, radius, half, near) {
  let lo = -Infinity;
  let hi = Infinity;
  if (Math.abs(dz) < EPS) {
    if (Math.abs(cz) > half) return NaN;
  } else {
    const a = (cz - half) / dz;
    const b = (cz + half) / dz;
    lo = Math.min(a, b);
    hi = Math.max(a, b);
  }
  const a = dx * dx + dy * dy;
  const c = cx * cx + cy * cy - radius * radius;
  if (a < EPS) {
    if (c > 0) return NaN;
  } else {
    const h = dx * cx + dy * cy;
    const disc = h * h - a * c;
    if (disc < 0) return NaN;
    const s = Math.sqrt(disc);
    lo = Math.max(lo, (h - s) / a);
    hi = Math.min(hi, (h + s) / a);
  }
  if (lo > hi || hi <= near) return NaN;
  return Math.max(lo, near);
}

/** Squared distance from the ray's point at t to the disc about (cx, cy, cz) of `radius`. */
function discGap2(t, cx, cy, cz, dx, dy, dz, radius) {
  const rim = Math.max(Math.hypot(t * dx - cx, t * dy - cy) - radius, 0);
  const z = t * dz - cz;
  return rim * rim + z * z;
}

/**
 * How near the ray passes to a disc, microns: the least of the distances from three
 * points of the ray, nearest the disc's center (`t`), on its plane and nearest its
 * axis.  Exact seen edge-on and seen face-on, a little over between.
 */
export function discGap(cx, cy, cz, dx, dy, dz, radius, t) {
  let best = discGap2(t, cx, cy, cz, dx, dy, dz, radius);
  if (Math.abs(dz) >= EPS) best = Math.min(best, discGap2(cz / dz, cx, cy, cz, dx, dy, dz, radius));
  const a = dx * dx + dy * dy;
  if (a >= EPS) best = Math.min(best, discGap2((dx * cx + dy * cy) / a, cx, cy, cz, dx, dy, dz, radius));
  return Math.sqrt(best);
}

/**
 * The least of several by one measure with a tie: those within their `tie` of the
 * least `first` are held, and the least `second` among them wins (then the least
 * `first`).  Fed one at a time; `accept` is asked only of a candidate that could
 * still win.
 */
class Least {
  constructor(accept) {
    this.accept = accept;
    this.first = Infinity;
    this.held = [];
  }

  offer(first, tie, second, cell, node) {
    if (first > this.first + tie || !this.accept(cell, node)) return;
    if (first < this.first) {
      this.first = first;
      this.held = this.held.filter((h) => h.first <= first + h.tie);
    }
    this.held.push({ first, tie, second, cell, node });
  }

  best() {
    let out = null;
    for (const h of this.held) {
      if (h.first > this.first + h.tie) continue;
      if (!out || h.second < out.second || (h.second === out.second && h.first < out.first)) out = h;
    }
    return out;
  }
}

/** Whether a ring (`{ front }`, its disc's least depth along the view) lies behind a surface `surface` deep. */
export function behindSurface(ring, surface) {
  return surface !== null && surface !== undefined && ring.front > surface + HIDDEN_BEHIND_UM;
}

/**
 * The drawn ring the pointer is plainly on, or null: of `rings` (`{ px, front, locationId,
 * key }`, the renderer's candidates, any order), the one within ON_RING_PX of the pointer
 * when no other is.  Rings behind the surface drawn under the pointer (`surface`, its depth
 * along the view, or null) are not there to be on.
 */
export function ringUnder(rings, surface = null) {
  let first = null;
  let second = null;
  for (const ring of rings || []) {
    if (behindSurface(ring, surface)) continue;
    if (!first || ring.px < first.px) {
      second = first;
      first = ring;
    } else if (!second || ring.px < second.px) second = ring;
  }
  if (!first || first.px > ON_RING_PX) return null;
  return second && second.px <= ON_RING_PX ? null : first;
}

/**
 * The annotation the readout names for this ray: `{ cell, node, px, depth, hit }`,
 * `hit` when the 3D render's pointer is on a surface or a disc (and `px` then 0),
 * or null when no cell has an annotation in front of the camera.
 */
export function nearestAnnotation(cells, view, accept = () => true) {
  const { perspective, scale } = view;
  const near = perspective ? view.near : -Infinity;
  const graph = view.look === 'graph';
  const surface = !graph && Number.isFinite(view.surface) ? view.surface : null;
  const reachUm = view.reachUm ?? HOVER_REACH_UM;
  const reachZUm = view.reachZUm ?? HOVER_REACH_Z_UM;
  const reachPx = view.reachPx ?? HOVER_REACH_PX;
  const front = new Least(accept);      // discs the ray meets: by depth, then by how near it passes
  const onto = new Least(accept);       // the opaque surface's point: by distance to the disc
  const screen = new Least(accept);     // otherwise: by pixels from the pointer, then by depth
  const pxAt = (t) => (perspective ? Math.max(t, near) * scale : scale);
  for (const cell of cells) {
    const [ox, oy, oz] = cell.origin;
    const [dx, dy, dz] = cell.dir;
    // Where the surface is, in this cell's frame, when the cell is what drew it.
    const on = surface !== null && cell.solid;
    for (const node of cell.nodes || []) {
      const p = node.xyz_um;
      if (!p) continue;
      const r = node.radius_um > 0 ? node.radius_um : 0;
      const cx = p[0] - ox;
      const cy = p[1] - oy;
      const cz = p[2] - oz;
      if (on) {
        const rim = Math.max(Math.hypot(surface * dx - cx, surface * dy - cy) - r, 0);
        const d = Math.hypot(rim, surface * dz - cz);
        if (d <= onto.first) onto.offer(d, 0, 0, cell, node);
        continue;
      }
      const t = cx * dx + cy * dy + cz * dz;       // nearest the center along the ray
      if (t + (graph ? 0 : r) <= near) continue;   // behind the camera
      const umpp = pxAt(t);
      const ex = cx - t * dx;
      const ey = cy - t * dy;
      const ez = cz - t * dz;
      const off = Math.sqrt(ex * ex + ey * ey + ez * ez);   // the ray's distance from the center
      if (graph) {
        if (t > near) screen.offer(off / umpp, HOVER_TIE_PX, t, cell, node);
        continue;
      }
      const reach = Math.max(reachUm, reachPx * umpp);
      const reachZ = Math.max(reachZUm, reachPx * umpp);
      // The widened disc is inside the ball about its center that reaches its rim's
      // corners: a ray farther from the center than that cannot meet it.
      if (off <= Math.hypot(r + reach, reachZ)) {
        const depth = discEntry(cx, cy, cz, dx, dy, dz, r + reach, reachZ, near);
        // Behind the opaque surface it is out of sight.
        if (depth <= front.first + HOVER_TIE_PX * umpp && !(surface !== null && depth >= surface)) {
          front.offer(depth, HOVER_TIE_PX * pxAt(depth), discGap(cx, cy, cz, dx, dy, dz, r, t), cell, node);
        }
      }
      if (surface !== null || front.held.length || t <= near) continue;
      // Nothing under the pointer yet: the nearest on the screen, should nothing be.
      if ((off - r) / umpp > screen.first + HOVER_TIE_PX) continue;
      screen.offer(discGap(cx, cy, cz, dx, dy, dz, r, t) / umpp, HOVER_TIE_PX, t, cell, node);
    }
  }
  const met = front.best();
  if (met) return { cell: met.cell, node: met.node, px: 0, depth: met.first, hit: true };
  const under = onto.best();
  if (under) return { cell: under.cell, node: under.node, px: 0, depth: surface, hit: true };
  const best = screen.best();
  return best ? { cell: best.cell, node: best.node, px: best.first, depth: best.second, hit: false } : null;
}
