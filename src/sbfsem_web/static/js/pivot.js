// The point a turn of the 3D view pivots about (render3d.js turns; app.js asks).
// Plain functions, so node can run them (tests/js/test_pivot.mjs).
//
// Every turn of the view -- a drag with or without Shift, Ctrl or Cmd/Alt, its
// glide, the arrow keys, q and e, the view cube -- pivots about one point.  It
// used to be the orbit target alone: a point on the line through the middle of the
// picture, slid to the depth of the surface under the pointer at each wheel step or
// right-press, and nowhere else.  Two things followed (measured 2026-10-22 on
// Inferior Monkey c943, perspective, a 1,134 x 679 px view):
//
// - Zoomed in with the pointer held over empty space beside a thin process, the
//   wheel kept the depth of the last surface it had been over: 17 steps in, the
//   process was 5.1 um from the camera and the pivot 23.1 um behind it, and a 60 px
//   drag (about 32 degrees) threw the process 1,561 px, off the picture.  So a turn
//   now measures the depth again as it starts when the pivot is plainly off what is
//   in the middle (`staleDepth`); the same drag then moved it 144 px.
// - A process away from the middle of a large view swings about the middle: what
//   was left of the 144 px.  So a person can put the pivot on it (P, or the
//   right-click menu, or the toolbar's Pivot box and a click), and every turn then
//   keeps that point where it is on the screen.  It lets go by itself when the
//   point leaves the picture, when the view is zoomed out past PIVOT_ZOOM_OUT times
//   the scale it was set at, or when its cell is no longer drawn (`pivotEnds`), and
//   with Reset view, a jump to an annotation, Flatten IPL, P, Esc or a click on its
//   label.  It is the page's alone: never saved with the session or a workspace.
//
// Render compare's panes have the same pivot, each its own renderer's (compare.js):
// with Sync cameras one point serves every pane, set from any of them and let go
// in all of them together; without it each pane keeps its own.

/** How far a set pivot's view may be zoomed out, as a multiple of its scale when set, before it lets go. */
export const PIVOT_ZOOM_OUT = 4;

/**
 * How far along the view a turn's pivot may lie from what is in the middle of the
 * picture before a turn measures its depth again, as a fraction of the picture's
 * half height at that depth: beyond it, a quarter turn carries what is in the middle
 * more than a quarter of the picture away.  A whole cell framed by Reset view is
 * well inside (c943 0.01, c1178 0.23 of its half height), so its turns are as before.
 */
export const STALE_DEPTH = 0.5;

/**
 * A P press on a cell puts the pivot on the middle of the annotation the readout
 * names there (the process's own axis, so it turns about itself) unless that lies
 * further from the point pointed at than this fraction of the picture's height
 * (close up on a soma: the patch looked at, not the soma's middle, stays put).
 */
export const SNAP_PICTURE = 0.25;

/** With nothing drawn under the pointer, the nearest annotation within this many pixels takes the pivot. */
export const SNAP_REACH_PX = 24;

/**
 * Whether a set pivot still holds this frame: null, or why it lets go ('hidden',
 * 'left', 'zoomed').  `ndc` is the point's place in normalized device coordinates
 * (-1..1 across the picture), `inFront` whether it is in front of the camera, and
 * the scales microns per pixel at the point now and when it was set.
 */
export function pivotEnds({ drawn = true, ndc = [0, 0], inFront = true, umPerPixel, umPerPixelAtSet }) {
  if (!drawn) return 'hidden';
  const [x, y] = ndc;
  if (!inFront || !Number.isFinite(x) || !Number.isFinite(y) || Math.abs(x) > 1 || Math.abs(y) > 1) {
    return 'left';
  }
  if (umPerPixel > umPerPixelAtSet * PIVOT_ZOOM_OUT) return 'zoomed';
  return null;
}

/** What the status line says when the pivot lets go by itself, by `pivotEnds`' reason. */
export const ENDED = Object.freeze({
  hidden: 'its cell is no longer drawn',
  left: 'the point left the picture',
  zoomed: `the view was zoomed out past ${PIVOT_ZOOM_OUT}×`,
  reset: 'Reset view',
  flight: 'the view moved to another place',
  flatten: 'Flatten IPL moved the cells',
});

const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/**
 * Where a pivot asked for at the pointer goes: `{ point, key }`, or null for nothing
 * to turn about.  `pointed` is what is drawn under the pointer (`{ point, key }`, the
 * point in the scene's frame) or null; `nearest` the annotation the pick readout
 * names there (`{ point, key, px, hit }`: its middle, how many pixels from the
 * pointer, and whether the pointer is on its surface or disc) or null; `pictureUm`
 * the picture's height, microns, at the depth of the point pointed at (or of the
 * annotation, with nothing pointed at).
 */
export function pivotPlace({ pointed = null, nearest = null, pictureUm = Infinity }) {
  if (nearest?.hit) {
    if (!pointed || (pointed.key === nearest.key
        && distance(pointed.point, nearest.point) <= SNAP_PICTURE * pictureUm)) {
      return { point: nearest.point, key: nearest.key, snapped: true };
    }
    return { point: pointed.point, key: pointed.key, snapped: false };
  }
  if (pointed) return { point: pointed.point, key: pointed.key, snapped: false };
  if (nearest && nearest.px <= SNAP_REACH_PX) return { point: nearest.point, key: nearest.key, snapped: true };
  return null;
}

/**
 * Where a pivot asked for at a screen point goes in `view`, a Renderer3D (anything
 * with its `pickAt`, `nearestAnnotation`, `toWorld` and `umPerPixelAt`): `pivotPlace`
 * of what is drawn there and of the annotation the readout names there, or null.
 * `cells` are the `{ key, nodes, source }` `nearestAnnotation` reads, the nodes in
 * `source`'s frame (`source` null for keys of no volume, a Render compare layer's);
 * `height` the picture's height, pixels.  The 3D render and every Render compare pane
 * ask through it.
 */
export function pivotAtPointer(view, clientX, clientY, cells, height) {
  const hit = view.pickAt(clientX, clientY);
  const found = cells?.length ? view.nearestAnnotation(clientX, clientY, cells) : null;
  const cell = found ? cells.find((c) => c.key === found.key) : null;
  const nearest = cell ? {
    point: view.toWorld(found.node.xyz_um, cell.source ?? null), key: found.key, px: found.px, hit: found.hit,
  } : null;
  const pointed = hit ? { point: hit.world.toArray(), key: hit.key } : null;
  const at = pointed?.point ?? nearest?.point;
  const pictureUm = at ? view.umPerPixelAt(at) * (height || 1) : Infinity;
  return pivotPlace({ pointed, nearest, pictureUm });
}

/**
 * Whether a turn about the orbit target, `pivotDepth` along the view, would carry
 * off what is in the middle of the picture, `contentDepth` along it, where the
 * picture's half height is `halfHeightUm`: then the turn measures its depth again.
 */
export function staleDepth({ pivotDepth, contentDepth, halfHeightUm }) {
  if (![pivotDepth, contentDepth, halfHeightUm].every(Number.isFinite) || !(halfHeightUm > 0)) return false;
  return Math.abs(pivotDepth - contentDepth) > STALE_DEPTH * halfHeightUm;
}
