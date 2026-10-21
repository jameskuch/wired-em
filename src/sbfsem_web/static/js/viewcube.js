// The view cube: a small cube in the viewport's top-right corner that stays
// where it is and turns with the view, after the ViewCube in Autodesk Fusion
// 360 (asked for 2026-10-20).  Its 26 parts are the 26 directions a view can be
// set to: the six faces look straight down an axis, the twelve edges are the
// 45-degree views between two faces, the eight corners the views between three.
// Double-clicking one turns the view there; dragging the cube orbits; the house
// is Reset view; looking straight at a face, four triangles turn to the face
// beside it and two curved arrows turn the view a quarter about the screen.
//
// It is drawn with CSS 3D transforms rather than three.js: a WebGL context of
// its own would be one more of the page's sixteen (the Render compare panes use
// them), and drawn into the viewport's own it would land in snapshots and
// videos.  Which part the pointer is over is worked out here, by casting the
// pointer's ray against the cube (`hitRegion`), not by the browser's hit
// testing of 3D-transformed elements.
//
// The cube is built in the scene's frame.  Under Flip Z the scene's Z runs the
// other way (`group.scale.z = -1`), so the face on top is the volume's -Z: the
// labels say so (`volumeLabel`), and the axis tripod at the cube's origin
// corner mirrors with the scene, as the Axes helper does.  The text is never
// mirrored.  The pure part of this module (everything but `ViewCube`) loads
// under node (tests/js/test_viewcube.mjs).

/** Half the cube's edge, CSS pixels. */
export const HALF = 32;
/** The CSS perspective distance of the stage the cube sits on, pixels. */
export const PERSPECTIVE = 400;
/** How wide an edge or corner zone is, as a share of a face's edge. */
export const BORDER = 0.2;
/** How close to a face the view must be for the face controls, degrees. */
export const FACE_ON_DEGREES = 1;
/** Radians the view turns per pixel of a drag on the cube: about a quarter
 * turn for a drag the cube's own width. */
export const DRAG_RADIANS_PER_PX = 0.022;

// The stage: room around the cube for the face controls and the tripod's letters,
// which reach 69 px from its middle (tests/js/test_viewcube.mjs).
export const STAGE = 144;
const MINUS = '−';
// The tripod's colors are the Axes helper's; the faces' letters are darker
// shades of them, to read on the light grey of a face.
const AXIS_COLORS = { X: '#ff6b5e', Y: '#5ecc6b', Z: '#6b9bff' };
const LABEL_COLORS = { X: '#b8322a', Y: '#23803a', Z: '#2a5fc4' };

const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const neg = (a) => [-a[0], -a[1], -a[2]];
const unit = (a) => {
  const n = Math.hypot(a[0], a[1], a[2]);
  return n ? [a[0] / n, a[1] / n, a[2] / n] : [0, 0, 0];
};
const sign = (x) => (x > 0 ? 1 : x < 0 ? -1 : 0);
// -0 reads as "0" everywhere it is shown or compared.
const clean = (a) => a.map((x) => x + 0);

/**
 * The six faces, in the scene's frame: `n` the outward normal, `up` the screen's
 * up when looking straight at the face, `u` and `v` the face's own right and
 * down (its text's).  Looking at a face is a toolbar viewpoint (`button`), with
 * that button's up: Z up for the four sides, Y up for the top and bottom.
 */
export const FACES = [
  { id: 'px', n: [1, 0, 0], up: [0, 0, 1], button: 'yz1' },
  { id: 'nx', n: [-1, 0, 0], up: [0, 0, 1], button: 'yz2' },
  { id: 'py', n: [0, 1, 0], up: [0, 0, 1], button: 'xz2' },
  { id: 'ny', n: [0, -1, 0], up: [0, 0, 1], button: 'xz1' },
  { id: 'pz', n: [0, 0, 1], up: [0, 1, 0], button: 'xy1' },
  { id: 'nz', n: [0, 0, -1], up: [0, 1, 0], button: 'xy2' },
].map((face) => ({ ...face, u: clean(cross(neg(face.n), face.up)), v: clean(neg(face.up)) }));

/** All 26 directions the cube offers: components -1, 0 or 1, not all 0. */
export const REGIONS = (() => {
  const out = [];
  for (const x of [-1, 0, 1]) {
    for (const y of [-1, 0, 1]) {
      for (const z of [-1, 0, 1]) if (x || y || z) out.push([x, y, z]);
    }
  }
  return out;
})();

/** 'face', 'edge' or 'corner'. */
export function regionKind(d) {
  return ['', 'face', 'edge', 'corner'][d.filter((c) => c !== 0).length];
}

/**
 * What a direction in the scene's frame is called in the volume's: "+Z",
 * "+X −Y", "+X −Y +Z".  Under Flip Z the scene's +Z is the volume's −Z.
 */
export function volumeLabel(d, flipZ = false) {
  const v = [d[0], d[1], flipZ ? -d[2] : d[2]];
  return ['X', 'Y', 'Z']
    .map((axis, i) => (v[i] > 0 ? `+${axis}` : v[i] < 0 ? `${MINUS}${axis}` : ''))
    .filter(Boolean).join(' ');
}

/** The toolbar viewpoint that looks along a face's direction, or null. */
export function buttonFor(d) {
  const face = FACES.find((f) => f.n.every((c, i) => c === d[i]));
  return face ? face.button : null;
}

/**
 * The view a region turns to: `dir` from what is looked at toward the camera,
 * `up` the screen's up, both unit vectors in the scene's frame.  A face is its
 * toolbar viewpoint exactly; an edge or a corner keeps the scene's Z as upright
 * as it can be (Z with its part along the view taken out).
 */
export function regionView(d) {
  const dir = unit(d);
  if (!d[0] && !d[1]) return { dir: clean(dir), up: [0, 1, 0] };
  const up = unit([-dir[2] * dir[0], -dir[2] * dir[1], 1 - dir[2] * dir[2]]);
  return { dir: clean(dir), up: clean(up) };
}

/**
 * The cube's CSS transform for a view: the camera's `right`, `up` and `back`
 * (toward the viewer) in the scene's frame.  CSS's screen has y down, so a
 * point `w` of the scene lands at (right·w, −up·w, back·w).  Column-major, as
 * `matrix3d()` takes it.
 */
export function cubeMatrix({ right, up, back }) {
  return [
    right[0], -up[0], back[0], 0,
    right[1], -up[1], back[1], 0,
    right[2], -up[2], back[2], 0,
    0, 0, 0, 1,
  ];
}

/**
 * A face's CSS transform inside the cube: its right along `u`, its down along
 * `v`, its front along the outward normal, its center pushed out by `half`.
 * With the cube's matrix (which has y flipped) every face's transform has a
 * positive determinant, so no face's text is ever mirrored.
 */
export function faceMatrix(face, half = HALF) {
  const { u, v, n } = face;
  return [u[0], u[1], u[2], 0, v[0], v[1], v[2], 0, n[0], n[1], n[2], 0,
    n[0] * half, n[1] * half, n[2] * half, 1];
}

/** A matrix as `matrix3d(...)`. */
export function cssMatrix(m) {
  return `matrix3d(${m.map((x) => (Math.abs(x) < 1e-9 ? 0 : +x.toFixed(6))).join(',')})`;
}

/**
 * Where a point of the scene (cube units: the cube is [-1, 1]³) is drawn, in
 * CSS pixels from the stage's center (y down), through the cube's matrix and
 * the stage's perspective.
 */
export function project(frame, w, { half = HALF, perspective = PERSPECTIVE } = {}) {
  const { right, up, back } = frame;
  const x = dot(right, w) * half;
  const y = -dot(up, w) * half;
  const z = dot(back, w) * half;
  const k = Number.isFinite(perspective) ? perspective / (perspective - z) : 1;
  return [x * k, y * k];
}

/**
 * The part of the cube under a pointer `px`, `py` CSS pixels from the stage's
 * center (y down), or null off the cube: its direction, components -1, 0 or 1.
 *
 * The pointer's ray leaves the eye of the stage's perspective and is taken into
 * the cube's frame (the cube's matrix is orthogonal, so its transpose undoes
 * it).  The face it enters first is the face; within the face a band `border`
 * of the edge wide along each side belongs to that edge, and a square in each
 * corner to that corner.
 */
export function hitRegion(frame, px, py, {
  half = HALF, perspective = PERSPECTIVE, border = BORDER,
} = {}) {
  const toCube = cubeFrom(frame);
  const ortho = !Number.isFinite(perspective);
  const eye = ortho ? [px, py, 1e6] : [0, 0, perspective];
  const ray = ortho ? [0, 0, -1] : [px, py, -perspective];
  const o = toCube(eye).map((c) => c / half);
  const d = toCube(ray);
  const hit = slabs(o, d);
  if (!hit || hit.far < 0) return null;
  const p = o.map((c, i) => c + d[i] * hit.near);
  const edge = 1 - 2 * border;
  return p.map((c, i) => (i === hit.axis ? sign(c) : c > edge ? 1 : c < -edge ? -1 : 0));
}

/**
 * Whether the cube hides a point outside it (cube units): the sight line from
 * the stage's eye to the point passes through the cube on the way.  The axis
 * letters are drawn flat over the cube, so the one whose tip is behind it is
 * left out, as the line itself is hidden by the faces.
 */
export function behindCube(frame, w, { half = HALF, perspective = PERSPECTIVE } = {}) {
  const toCube = cubeFrom(frame);
  const ortho = !Number.isFinite(perspective);
  // Orthographic: from far out along the view, toward the viewer, to the point.
  const eye = ortho ? w.map((c, i) => c + frame.back[i] * 1e6) : toCube([0, 0, perspective]).map((c) => c / half);
  const d = w.map((c, i) => c - eye[i]);
  const hit = slabs(eye, d);
  return !!hit && hit.near > 0 && hit.near < 1;
}

/** The cube's matrix undone: CSS screen coordinates to the cube's frame (its transpose). */
function cubeFrom({ right, up, back }) {
  return (c) => [
    right[0] * c[0] - up[0] * c[1] + back[0] * c[2],
    right[1] * c[0] - up[1] * c[1] + back[1] * c[2],
    right[2] * c[0] - up[2] * c[1] + back[2] * c[2],
  ];
}

/** Where the ray `o + t d` enters and leaves the cube [-1, 1]³ (`near`, `far`) and
 * through which axis's faces it enters, or null when it misses. */
function slabs(o, d) {
  let near = -Infinity;
  let far = Infinity;
  let axis = -1;
  for (let i = 0; i < 3; i += 1) {
    if (Math.abs(d[i]) < 1e-12) {
      if (Math.abs(o[i]) > 1) return null;
      continue;
    }
    let t0 = (-1 - o[i]) / d[i];
    let t1 = (1 - o[i]) / d[i];
    if (t0 > t1) [t0, t1] = [t1, t0];
    if (t0 > near) { near = t0; axis = i; }
    far = Math.min(far, t1);
  }
  return near > far || axis < 0 ? null : { near, far, axis };
}

/**
 * The cells of a face that a region lights, as indices 0 to 8 in the face's
 * 3 × 3 grid (row by row, in the face's own right and down), or [] when the
 * region does not touch the face.
 */
export function faceCells(face, d) {
  if (dot(face.n, d) !== 1) return [];
  const col = dot(face.u, d) + 1;
  const row = dot(face.v, d) + 1;
  return [row * 3 + col];
}

/**
 * Whether the view looks straight at a face (within `degrees`), and if so the
 * face's direction and the screen's up and right snapped to the cube's axes --
 * the frame the face controls turn from.  null otherwise.
 */
export function faceOn({ up, back }, degrees = FACE_ON_DEGREES) {
  const limit = Math.cos((degrees * Math.PI) / 180);
  const axis = [0, 1, 2].reduce((a, i) => (Math.abs(back[i]) > Math.abs(back[a]) ? i : a), 0);
  if (Math.abs(back[axis]) < limit) return null;
  const dir = [0, 0, 0];
  dir[axis] = sign(back[axis]);
  // The screen's up, snapped: whichever of the four axes across the view it is nearest.
  let best = null;
  for (let i = 0; i < 3; i += 1) {
    if (i === axis) continue;
    for (const s of [1, -1]) {
      const cand = [0, 0, 0];
      cand[i] = s;
      if (!best || dot(cand, up) > dot(best, up)) best = cand;
    }
  }
  return { dir, up: best, right: clean(cross(best, dir)) };
}

/**
 * From a face-on view (`faceOn`), the view a face control turns to: 'up',
 * 'down', 'left' or 'right' bring the face on that side of the screen round to
 * the front, a quarter turn about the screen's other axis; 'cw' and 'ccw' turn
 * the picture a quarter clockwise or counterclockwise about the view.
 */
export function quarterTurn(state, which) {
  const { dir, up, right } = state;
  switch (which) {
    case 'up': return { dir: up, up: clean(neg(dir)) };
    case 'down': return { dir: clean(neg(up)), up: dir };
    case 'left': return { dir: clean(neg(right)), up };
    case 'right': return { dir: right, up };
    // Clockwise: what was on the right goes to the bottom, so the old right is the new down.
    case 'cw': return { dir, up: clean(neg(right)) };
    case 'ccw': return { dir, up: right };
    default: return null;
  }
}

/**
 * The axis tripod at the cube's origin corner, the volume's (-1, -1, -1), in the
 * scene's frame: for each axis where it starts, which way it runs, and the two
 * directions across it (each line is two crossed strips, so it never vanishes
 * edge on).  `length` in cube units; it reaches a little past the cube.
 */
export function tripod(flipZ = false, { length = 2.3, lift = 0.05 } = {}) {
  const toScene = (v) => [v[0], v[1], flipZ ? -v[2] : v[2]];
  const basis = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  return ['X', 'Y', 'Z'].map((name, i) => {
    const across = basis.filter((_, j) => j !== i);
    // Just outside the two faces the line runs along, so the faces never cut it.
    const start = [-1, -1, -1].map((c, j) => (j === i ? c : c - lift));
    return {
      name,
      color: AXIS_COLORS[name],
      start: toScene(start),
      dir: toScene(basis[i]),
      across: across.map(toScene),
      length,
      tip: toScene(start.map((c, j) => (j === i ? c + length + 0.2 : c))),
    };
  });
}

/** A strip's CSS transform: its length along `dir`, its width along `across`,
 * centered half its length from `start` (cube units scaled by `half`). */
export function stripMatrix(start, dir, across, length, half = HALF) {
  const z = cross(dir, across);
  const c = start.map((s, i) => (s + dir[i] * length / 2) * half);
  return [dir[0], dir[1], dir[2], 0, across[0], across[1], across[2], 0, z[0], z[1], z[2], 0,
    c[0], c[1], c[2], 1];
}

const SVG = {
  home: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 7.6 8 2.5l6 5.1" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/><path d="M4 7v6.2h3v-3.6h2v3.6h3V7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  tri: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M1.5 3h9L6 9.5z" fill="currentColor"/></svg>',
  cw: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 10.5a5.2 5.2 0 1 1 8.8 1.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M13.8 9.4 12.3 13l-3.4-1.9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  ccw: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M12.8 10.5a5.2 5.2 0 1 0-8.8 1.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M2.2 9.4 3.7 13l3.4-1.9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const TURN_HELP = {
  up: 'Turn to the face above', down: 'Turn to the face below',
  left: 'Turn to the face on the left', right: 'Turn to the face on the right',
  cw: 'Turn the view a quarter clockwise', ccw: 'Turn the view a quarter counterclockwise',
};

/**
 * The cube itself, over a view.  The `view` is what it reads and turns: a
 * Renderer3D (the 3D render's), or anything with the same `viewFrame()`,
 * `flipZ`, `turnTo(dir, up)` (a promise of whether it arrived), `spinBy(yaw,
 * pitch)` and `endSpin()` (Render compare's, over all its panes).  Whoever
 * draws the view calls `update()` after every frame (a renderer's `onFrame`).
 * `onHome` is the house; `onTurned` follows every turn the cube made, once the
 * view has arrived.  `attach(container)` moves the cube to another host.
 */
export class ViewCube {
  constructor(container, view, { onHome = null, onTurned = null } = {}) {
    this.view = view;
    this.onHome = onHome;
    this.onTurned = onTurned;
    this.visible = false;
    this._matrix = '';
    this._flip = null;
    this._hover = null;      // the region under the pointer
    this._pointer = null;    // [px, py] from the stage's center while the pointer is over a face
    this._press = null;      // a press on the cube: where, and whether it became a drag
    this._faceOn = null;
    this._build(container);
  }

  _build(container) {
    const root = document.createElement('div');
    root.className = 'viewcube';
    root.hidden = true;
    const stage = document.createElement('div');
    stage.className = 'vc-stage';
    stage.style.perspective = `${PERSPECTIVE}px`;
    const cube = document.createElement('div');
    cube.className = 'vc-cube';
    this.faces = FACES.map((face) => {
      const el = document.createElement('div');
      el.className = `vc-face vc-face-${face.id}`;
      el.style.transform = cssMatrix(faceMatrix(face));
      const cells = [];
      for (let i = 0; i < 9; i += 1) {
        const cell = document.createElement('div');
        cell.className = 'vc-cell';
        el.append(cell);
        cells.push(cell);
      }
      const label = document.createElement('span');
      label.className = 'vc-label';
      el.append(label);
      cube.append(el);
      return { face, el, cells, label };
    });
    this.strips = [];
    for (let i = 0; i < 3; i += 1) {
      for (let j = 0; j < 2; j += 1) {
        const strip = document.createElement('div');
        strip.className = 'vc-axis';
        cube.append(strip);
        this.strips.push(strip);
      }
    }
    stage.append(cube);
    root.append(stage);
    this.tips = ['X', 'Y', 'Z'].map((name) => {
      const tip = document.createElement('span');
      tip.className = 'vc-tip';
      tip.textContent = name;
      tip.style.color = AXIS_COLORS[name];
      root.append(tip);
      return tip;
    });
    const button = (cls, svg, title) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `vc-btn ${cls}`;
      b.innerHTML = svg;
      b.title = title;
      b.setAttribute('aria-label', title);
      root.append(b);
      return b;
    };
    this.home = button('vc-home', SVG.home, 'Home: Reset view (m)');
    this.turnButtons = {};
    for (const which of ['up', 'down', 'left', 'right']) {
      this.turnButtons[which] = button(`vc-turn vc-nudge vc-nudge-${which}`, SVG.tri, TURN_HELP[which]);
    }
    for (const which of ['ccw', 'cw']) {
      this.turnButtons[which] = button(`vc-turn vc-roll vc-roll-${which}`, SVG[which], TURN_HELP[which]);
    }
    const caption = document.createElement('div');
    caption.className = 'vc-caption';
    root.append(caption);

    this.root = root;
    this.stage = stage;
    this.cube = cube;
    this.caption = caption;
    container.append(root);
    this._bind();
  }

  _bind() {
    const { stage } = this;
    this._abort = new AbortController();
    const { signal } = this._abort;
    const at = (e) => {
      const r = stage.getBoundingClientRect();
      return [e.clientX - (r.left + r.width / 2), e.clientY - (r.top + r.height / 2)];
    };
    stage.addEventListener('pointermove', (e) => {
      this._pointer = at(e);
      const press = this._press;
      if (press && e.pointerId === press.id) {
        const dx = e.clientX - press.x;
        const dy = e.clientY - press.y;
        press.x = e.clientX;
        press.y = e.clientY;
        press.travel += Math.hypot(dx, dy);
        if (press.travel > 3) {
          press.dragged = true;
          this.root.classList.add('dragging');
          this.view.spinBy(-dx * DRAG_RADIANS_PER_PX, -dy * DRAG_RADIANS_PER_PX);
        }
      }
      this._refreshHover();
    }, { signal });
    stage.addEventListener('pointerleave', () => {
      if (this._press) return;
      this._pointer = null;
      this._refreshHover();
    }, { signal });
    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      // No text selection from a double click, and the drag stays the cube's.
      e.preventDefault();
      e.target.setPointerCapture?.(e.pointerId);
      this._press = { id: e.pointerId, x: e.clientX, y: e.clientY, travel: 0, dragged: false };
    }, { signal });
    const release = (e) => {
      const press = this._press;
      if (!press || e.pointerId !== press.id) return;
      this._press = null;
      this._lastDragged = press.dragged;
      this.root.classList.remove('dragging');
      if (press.dragged) this.view.endSpin();
      if (e.type === 'pointercancel' || !stage.contains(document.elementFromPoint(e.clientX, e.clientY))) {
        this._pointer = null;
      }
      this._refreshHover();
    };
    stage.addEventListener('pointerup', release, { signal });
    stage.addEventListener('pointercancel', release, { signal });
    stage.addEventListener('dblclick', (e) => {
      e.preventDefault();
      if (this._lastDragged) return;
      const region = hitRegion(this._frame(), ...at(e));
      if (region) this._turn(regionView(region));
    }, { signal });
    // The browser's own menu has nothing for the cube.
    stage.addEventListener('contextmenu', (e) => e.preventDefault(), { signal });
    this.home.addEventListener('click', () => this.onHome?.(), { signal });
    for (const [which, b] of Object.entries(this.turnButtons)) {
      b.addEventListener('click', () => {
        if (!this._faceOn) return;
        this._turn(quarterTurn(this._faceOn, which));
      }, { signal });
    }
  }

  _frame() {
    return this.view.viewFrame();
  }

  async _turn(to) {
    if (!to) return;
    const arrived = await this.view.turnTo(to.dir, to.up);
    if (arrived) this.onTurned?.();
  }

  /** Move the cube into another host element (a no-op when it is there already). */
  attach(container) {
    if (!container || this.root.parentElement === container) return;
    container.append(this.root);
    this._pointer = null;
    this._matrix = '';
    this.update();
  }

  setVisible(on) {
    this.visible = !!on;
    this.root.hidden = !this.visible;
    if (this.visible) {
      this._matrix = '';
      this.update();
    }
  }

  /** Follow the camera: called after each frame the view draws. */
  update() {
    if (!this.visible) return;
    const flip = !!this.view.flipZ;
    if (flip !== this._flip) this._relabel(flip);
    const frame = this._frame();
    const matrix = cssMatrix(cubeMatrix(frame));
    if (matrix === this._matrix) return;
    this._matrix = matrix;
    this.cube.style.transform = matrix;
    this._placeTips(frame);
    this._faceOn = faceOn(frame);
    this.root.classList.toggle('face-on', !!this._faceOn);
    this._refreshHover(frame);
  }

  _relabel(flip) {
    this._flip = flip;
    for (const { face, label } of this.faces) {
      const text = volumeLabel(face.n, flip);
      label.textContent = text;
      label.style.color = LABEL_COLORS[text.slice(-1)];
    }
    const axes = tripod(flip);
    axes.forEach((axis, i) => {
      axis.across.forEach((across, j) => {
        const strip = this.strips[i * 2 + j];
        const length = axis.length * HALF;
        strip.style.width = `${length}px`;
        strip.style.left = `${HALF - length / 2}px`;
        strip.style.top = `${HALF - 1}px`;
        strip.style.background = axis.color;
        strip.style.transform = cssMatrix(stripMatrix(axis.start, axis.dir, across, axis.length));
      });
    });
    this._tripod = axes;
    this._matrix = '';
  }

  _placeTips(frame) {
    this._tripod.forEach((axis, i) => {
      const [x, y] = project(frame, axis.tip);
      this.tips[i].style.left = `${STAGE / 2 + x}px`;
      this.tips[i].style.top = `${STAGE / 2 + y}px`;
      this.tips[i].hidden = behindCube(frame, axis.tip);
    });
  }

  _refreshHover(frame = null) {
    const region = this._pointer && !this._press?.dragged
      ? hitRegion(frame || this._frame(), ...this._pointer) : null;
    const key = region ? region.join(',') : '';
    if (key === (this._hover ? this._hover.join(',') : '')) return;
    this._hover = region;
    for (const { face, cells } of this.faces) {
      const lit = region ? faceCells(face, region) : [];
      cells.forEach((cell, i) => cell.classList.toggle('hot', lit.includes(i)));
    }
    if (!region) {
      this.caption.textContent = '';
      return;
    }
    const button = buttonFor(region);
    const name = volumeLabel(region, !!this.view.flipZ);
    this.caption.innerHTML = '';
    const title = document.createElement('strong');
    title.textContent = button ? `${name} · ${button.toUpperCase()}` : name;
    const hint = document.createElement('span');
    hint.textContent = 'double-click';
    this.caption.append(title, hint);
  }

  dispose() {
    this._abort?.abort();
    this.root.remove();
  }
}
