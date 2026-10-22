// three.js scene for neuron renders.
//
// Replaces RenderApp's PyVista/VTK scene. The custom keyboard navigation from
// views/RenderApp.m is preserved alongside standard mouse orbiting rather than
// instead of it: arrow keys change azimuth/elevation, q/e roll about the view
// axis, z/Z zoom, m resets, f toggles full screen, and c converts the picked
// point back to Viking pixel coordinates and copies it to the clipboard.
// Panning is right-drag only (the old w/a/s/d pan keys were retired).

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { DEFAULT_COLORMAP, depthColors, resolveColormap } from './colormaps.js';
import { highlightLook, pulses, settleProgress } from './highlight.js';
import { nudgedSkeleton } from './manualmoves.js';
import { inSlice, sameAnnotation } from './markers.js';
import {
  RING_SEGMENTS, discColorBuffer, resolveColors, segmentBuffers,
} from './layerbuffers.js';
import {
  applyMaterialSpec, buildEnvironment, createSurfaceMaterial, materialSpec,
} from './materials.js';
import {
  BEHIND, nearestSegment, nearestTriangle, projectPositions,
} from './pickgeom.js';
import { ContextGesture, SURFACE_REACH_PX, listCandidates, pickOf } from './contextmenu.js';
import {
  FAT_FRAGMENT_SHADER, FAT_VERTEX_SHADER, TAIL_ALPHA, WIDTH_DEFAULT, fatArrows, fatColors, fatRange,
  fatSegments, headSize, ringWidth, arrowReach, shaftRange, shaftSegments,
} from './fatlines.js';

const UP = new THREE.Vector3(0, 0, 1);

// The reset/3D viewpoint: MATLAB's default ``view(-37.5, 30)``, exactly, so
// a reset here frames the scene the way every MATLAB figure the lab has ever
// made frames it.  Offset direction for view(az, el) is
// (-sin(az)cos(el), -cos(az)cos(el), sin(el)).
const DEFAULT_AZ = (-37.5 * Math.PI) / 180;
const DEFAULT_EL = (30 * Math.PI) / 180;
const DEFAULT_VIEW_DIR = new THREE.Vector3(
  -Math.sin(DEFAULT_AZ) * Math.cos(DEFAULT_EL),
  -Math.cos(DEFAULT_AZ) * Math.cos(DEFAULT_EL),
  Math.sin(DEFAULT_EL),
).normalize();

/** A floating letter for one axis tip, so the tripod is checkable at a
 * glance rather than three anonymous colored lines. */
function axisLabel(text, color) {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  ctx.font = 'bold 44px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = color;
  ctx.fillText(text, 32, 34);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(canvas), transparent: true, depthTest: false,
  }));
  return sprite;
}

/**
 * Set a material's opacity so the change is actually rendered.
 *
 * `opacity` is a uniform and updates for free, but `transparent` selects the
 * blending path and needs a shader recompile.  Without `needsUpdate` the first
 * transition from opaque to translucent changes the material and nothing on
 * screen -- which looked like the opacity slider being ignored.
 */
function setOpacity(material, opacity) {
  if (opacity === undefined || !material) return;
  const nowTransparent = opacity < 1;
  if (material.transparent !== nowTransparent) {
    material.transparent = nowTransparent;
    material.needsUpdate = true;
  }
  material.opacity = opacity;
}

/** A mesh's faces as a geometry's index.  three.js's `setIndex` takes an
 * Array or an attribute, and a finished render's faces arrive as a
 * Uint32Array (api.js `decodeMeshReply`); an attribute uses it as it is.
 * Nothing writes into a geometry's index, so the cached mesh is never changed. */
function meshIndex(faces) {
  return ArrayBuffer.isView(faces) ? new THREE.BufferAttribute(faces, 1) : faces;
}

/** three.js raycasts hidden objects too; keep only hits whose whole parent
 * chain is visible, so a hidden cell can never swallow a click. */
function visibleHits(hits) {
  return hits.filter(({ object }) => {
    for (let node = object; node; node = node.parent) {
      if (node.visible === false) return false;
    }
    return true;
  });
}

/**
 * The shortest working distance, in microns, that zoom and pan are scaled by.
 * The imagery is 2 to 8 nm per pixel in 50 to 90 nm sections, so a quarter of
 * a micron in front of the camera is already past anything an annotation can
 * show; below it the wheel keeps a constant step and carries on through.
 */
const MIN_WORKING_UM = 0.25;

/**
 * `_viewedDepth`: how much of the picture counts as "what is in view", as a
 * half-width in normalized device coordinates, and how many vertices of each
 * mesh are projected to find it.
 */
const VIEW_DEPTH_WINDOW = 0.7;
const VIEW_DEPTH_VERTICES = 20000;

/**
 * Most indices the annotation layer puts in one draw call; a multiple of 3.
 * Firefox's ceiling is 30 million, and a call over it draws nothing at all.
 */
const MAX_DRAW_INDICES = 12_000_000;

/** How near the pointer, in screen pixels, an item must pass to be selectable. */
const SELECT_PX = 6;
/** The most rings of a z-slice drawn as just chosen, settling; a bigger one is drawn settled. */
const SLICE_PULSE_MAX = 60;
/** The least a highlighted annotation's GraphApp dot is across, CSS px, before its glow. */
const DOT_HIGHLIGHT_MIN_PX = 6;
/** The slots a selection has: the yellow, the red above it, the blue below. */
const SLOTS = ['primary', 'above', 'below'];
/** Slack, in microns, so a point on the very edge of the crop box is not taken for outside it. */
const CROP_SLACK_UM = 1e-3;
/** Selection highlight: yellow, or cyan on a cell that is itself yellow. */
const HIGHLIGHT = '#ffe14d';
const HIGHLIGHT_ALT = '#38e1ff';
/** The marker below the yellow one: light blue, or pink when the yellow one is cyan. */
const BELOW_HIGHLIGHT = '#8fd0ff';
const BELOW_HIGHLIGHT_ALT = '#ff8ad8';
/** The marker above it: red, or orange on a cell that is itself red (2026-10-16). */
const ABOVE_HIGHLIGHT = '#ff5c5c';
const ABOVE_HIGHLIGHT_ALT = '#ff7a00';
/** Does the person's system ask for less motion?  A chosen ring is then drawn settled at once. */
function prefersReducedMotion() {
  try {
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/**
 * Where each vertex of a tube sits on its path and how far out from it, so the tube's
 * radius can be changed by scaling the offsets and the geometry never built again.
 * `TubeGeometry` places ring `i` at `getPointAt(i / segments)`, and the last ring of a
 * closed tube on the first.
 */
function tubeAnchors(geometry, curve, segments, radialSegments, closed) {
  const array = geometry.getAttribute('position').array;
  const center = new Float32Array(array.length);
  const row = radialSegments + 1;
  for (let i = 0; i <= segments; i += 1) {
    const point = curve.getPointAt((closed && i === segments ? 0 : i) / segments);
    for (let j = 0; j < row; j += 1) {
      const at = (i * row + j) * 3;
      center[at] = point.x;
      center[at + 1] = point.y;
      center[at + 2] = point.z;
    }
  }
  const offset = new Float32Array(array.length);
  for (let n = 0; n < array.length; n += 1) offset[n] = array[n] - center[n];
  return { center, offset };
}

/** Slack, in microns, so the annotation a Z hide is set from stays drawn. */
const Z_HIDE_SLACK_UM = 0.02;

/**
 * A surface layer's group renders at `index - LAYER_ORDER_BASE`: far enough
 * below zero that every layer draws before the rings and markers, whose own
 * render orders (3, 4) are positive.
 */
const LAYER_ORDER_BASE = 100;

/** Viking TypeCode of a closed-curve annotation (a disc is 1). */
const CLOSED_CURVE = 6;

/**
 * A polygon's distinct vertices: the closing point Viking repeats (and any
 * non-finite sample) dropped, so a band around it has no zero-length edge.
 */
function closedPolygon(points) {
  const out = [];
  for (const p of points || []) {
    if (!p || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue;
    out.push([p[0], p[1]]);
  }
  if (out.length > 1) {
    const [a, b] = [out[0], out[out.length - 1]];
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-9) out.pop();
  }
  return out;
}

export class Renderer3D {
  constructor(container, { onPick } = {}) {
    this.container = container;
    this.onPick = onPick;
    this.meshes = new Map();   // key -> THREE.Object3D
    this.synapses = new Map(); // key -> THREE.Object3D
    this.skeletons = new Map();
    this.discs = new Map();    // key -> annotation rings
    this.annoWires = new Map(); // key -> annotation wireframe (averaged rings)
    this.points = new Map();   // key -> colored point cloud (Render compare's error map)
    this.lines = new Map();    // key -> line segments, optionally colored per end (setLines)
    this.shifts = new Map();   // key -> Position shift layer (arrows, original rings, slice plane)
    this.moveArrows = new Map(); // key -> a cell's manual moves, an arrow each (setMoveArrows)
    this.moveSets = new Map();   // key -> the rings of what a manual step moves (setMoveSet)
    // key -> the cell as GraphApp draws it (setGraphCell): one group of the skeleton,
    // rings, a dot per annotation, the flagged annotations and the synapses.
    this.graphs = new Map();
    // Which look the view shows: 'render' (the 3D render) or 'graph' (GraphApp).
    this.look = 'render';
    this.graphSettings = null;
    this._nudges = new Map();  // marker slot -> [dx, dy, dz] um: a move not yet applied, drawn ahead
    this.volumeOffsets = new Map(); // source -> THREE.Vector3, microns
    this.zHide = new Map();    // key -> { min, max }: the Z range shown, volume microns
    this._order = new Map();   // key -> draw-order index of a surface layer (setLayerOrder)
    this._hiddenCells = new Set(); // keys setStyle({ visible: false }) hid
    this._paused = false;      // setPaused: draw nothing until told to carry on
    this._hidden = false;      // the container has no size (a tab or pane out of sight)
    this._raf = null;
    this.selection = null;     // what a triple click picked (see candidatesAt)
    // The markers either side of a selected annotation (2026-10-16; one light
    // blue "second" marker since 2026-09-22): `above`, drawn red, on a section
    // above it in the upright view, and `below`, drawn blue, under it.  The app
    // decides which is which (markers.js), the arrow keys step them along Z, and
    // the readout lists all three with the Z between them.
    this.markers = { above: null, below: null };
    // Each slot's z-slice, or null (setSlice): the rest of the annotations on its section.
    this.slices = { primary: null, above: null, below: null };
    this.onSelectRequest = null;
    this.onSelectionChange = null;
    this.onMarkerChange = null;
    // Up/Down with an annotation selected: `(direction, { primary })`, with
    // direction +1 for "above" (the app knows what that means under Flip Z).
    this.onStepZ = null;
    // Slice mode of the Position shift layer: `(keyboardEvent) => boolean`, true
    // when it took the key (the app knows which volumes are in slice mode and
    // whether an annotation is selected).  Offered after the onStepZ walk.
    this.onSliceStep = null;
    // The viewport changed background (setInverted): `(inverted) => void`, for
    // what is drawn over it in its colors (the Position shift legend).
    this.onInverted = null;
    // After every frame drawn: `() => void`, for what follows the camera over
    // the canvas (the view cube).
    this.onFrame = null;
    this.bounds = new THREE.Box3();
    // Everything drawn, hidden or not, context included: what the far plane
    // has to reach.  Rebuilt lazily when something is added or removed.
    this._reach = new THREE.Sphere();
    this._reachDirty = true;
    this.pickedPoint = null;
    this.pickedKey = null;
    this.framed = false;
    this._lastViewpoint = null;
    this._flight = null;
    this._endFlight = null;
    this._anchor = { x: -1, y: -1, at: 0, every: 200, hit: false, hold: null };

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x101418);

    this.perspective = new THREE.PerspectiveCamera(45, 1, 0.05, 20000);
    this.orthographic = new THREE.OrthographicCamera(-1, 1, 1, -1, -20000, 20000);
    this.camera = this.perspective;
    this.camera.up.copy(UP);

    this.webgl = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.webgl.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    // Axis limits are global clipping planes; a cell's own Z range (setZHide)
    // is a pair of planes on that cell's materials, which needs this on.
    this.webgl.localClippingEnabled = true;
    this.inverted = false;
    this.flipZ = false;
    this.clipBox = null;
    this.colormap = { map: DEFAULT_COLORMAP, levels: 256, invert: false };
    this._scalars = new Map();
    container.appendChild(this.webgl.domElement);

    this.controls = new OrbitControls(this.camera, this.webgl.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    // Wheel zoom dollies along the ray under the mouse pointer (the Fusion
    // 360 behavior): off-center, zooming in pulls the scene toward the
    // pointer, the more so the further the pointer sits from screen center;
    // dead center it is a plain orthogonal zoom. Purely viewport-driven --
    // the content's position never enters into it. Works for the
    // orthographic camera too; the orbit target is re-placed on the new view
    // axis so subsequent orbits stay centered on what is being looked at.
    this.controls.zoomToCursor = true;
    // OrbitControls' own rotation is off.  It measures elevation against a
    // fixed pole and clamps it short of straight up and straight down, which
    // was the hard stop a plain left-drag ran into.  Plain drags now turn the
    // view in _bindEvents, through the same frame-rotating path the arrow
    // keys and single-axis drags already used, so rotation carries on for as
    // long as the hand does.  Pan and zoom stay with OrbitControls.
    this.controls.enableRotate = false;
    this._spin = { yaw: 0, pitch: 0, endPending: false };

    // Two key lights plus ambient, matching the MATLAB lightangle(45,30) /
    // lightangle(225,30) pair.
    // Intensities retuned for physically based materials, which take the
    // lights literally: Phong's 0.55 ambient read as a mid grey, Physical
    // wants about twice that from the lamps plus an environment to reflect.
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    this.keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
    this.fillLight = new THREE.DirectionalLight(0xffffff, 1.1);
    this.scene.add(this.keyLight, this.fillLight);
    // Metals and clearcoats reflect this; matte finishes barely notice it.
    try {
      this.scene.environment = buildEnvironment(this.webgl);
    } catch (error) {
      console.warn('no environment map:', error);
    }

    this.axes = new THREE.AxesHelper(1);
    this.axes.visible = true;
    // AxesHelper colors: +X red, +Y green, +Z blue -- matched here.
    for (const [text, color, position] of [
      ['X', '#ff6b5e', [1.14, 0, 0]],
      ['Y', '#5ecc6b', [0, 1.14, 0]],
      ['Z', '#6b9bff', [0, 0, 1.14]],
    ]) {
      const label = axisLabel(text, color);
      label.position.fromArray(position);
      label.scale.setScalar(0.15);
      this.axes.add(label);
    }
    this.scene.add(this.axes);

    this.group = new THREE.Group();
    this.scene.add(this.group);
    // The two looks of the view (setLook): every cell's 3D render objects in one
    // group, its GraphApp objects in the other, so switching shows one and hides
    // the other and builds nothing.  Both inside the group: Flip Z and the volume
    // offsets are the same for both.
    this.renderLayer = new THREE.Group();
    this.graphLayer = new THREE.Group();
    this.graphLayer.visible = false;
    this.group.add(this.renderLayer, this.graphLayer);
    // Selection highlights: inside the group so Flip Z mirrors them with the
    // scene, but in no content map, so bounds, picks and styles ignore them.
    this.highlightGroup = new THREE.Group();
    this.group.add(this.highlightGroup);

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();

    this._bindEvents();
    this._resize();
    this._animate();
  }

  _bindEvents() {
    this._observer = new ResizeObserver(() => this._resize());
    this._observer.observe(this.container);
    // One signal for every listener below: dispose() aborts it, which takes the
    // handlers off `window` too.  Those would otherwise outlive the renderer,
    // one more set each time a Render compare grid was rebuilt, each holding
    // the renderer and so its scene.
    const { signal } = (this._abort = new AbortController());
    this.webgl.domElement.addEventListener('pointerdown', (e) => this._pick(e), { signal });
    // Zoom and pan are scaled by the distance to what the pointer is over
    // (_anchorDepth).  Capture phase, so the depth is set before
    // OrbitControls handles the same event.
    this.webgl.domElement.addEventListener('wheel', (e) => this._anchorDepth(e),
      { capture: true, passive: true, signal });
    this.webgl.domElement.addEventListener('pointerdown', (e) => {
      if (e.button === 2) this._anchorDepth(e, { force: true });
    }, { capture: true, signal });
    this.webgl.domElement.tabIndex = 0;
    this.webgl.domElement.addEventListener('keydown', (e) => this._key(e), { signal });

    // A left TRIPLE click selects what is under the pointer -- an annotation,
    // a skeleton branch, a wireframe, a synapse type; never the surface.  The
    // app decides what to do with several candidates (it asks).  In GraphApp a
    // single click does (`gcX`, `gcY`: where its last click was).
    let tcX = 0;
    let tcY = 0;
    let gcX = NaN;
    let gcY = NaN;
    this.webgl.domElement.addEventListener('pointerdown', (e) => {
      if (e.button === 0) { tcX = e.clientX; tcY = e.clientY; }
    }, { signal });
    this.webgl.domElement.addEventListener('click', (e) => {
      if (e.button !== 0) return;
      // ⌘-click (Ctrl-click on Windows) on a ring, without a drag: the app adds it to a set
      // or takes it out (moving annotations by hand).  A drag with the key still turns the view.
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && this.onAdditiveClick
          && Math.hypot(e.clientX - tcX, e.clientY - tcY) <= 5) {
        const ring = this.candidatesAt(e.clientX, e.clientY).find((c) => c.kind === 'annotation');
        if (ring && this.onAdditiveClick(ring)) return;
      }
      if (e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
      if (Math.hypot(e.clientX - tcX, e.clientY - tcY) > 5) return;
      // In GraphApp ONE click selects, as MATLAB's GraphApp named the node a click
      // landed on (2026-10-22); the clicks after it in a double or triple click on the
      // same spot do nothing more.  A click on nothing lets go, as below.
      if (this.look === 'graph') {
        const fresh = e.detail <= 1 || Math.hypot(e.clientX - gcX, e.clientY - gcY) > 5;
        gcX = e.clientX;
        gcY = e.clientY;
        if (!fresh) return;
        const candidates = this.candidatesAt(e.clientX, e.clientY);
        if (!candidates.length) this.clearSelection();
        else this.onSelectRequest?.({ candidates, clientX: e.clientX, clientY: e.clientY, look: 'graph' });
        return;
      }
      // A plain click on empty space lets go of the selection, as it does in
      // every other 3D program; a drag from there still only orbits.  A ring
      // is not empty space although no ray stops on it: that click is the
      // first of a triple click choosing a second annotation.
      if (e.detail === 1 && this.selection && this._pickMissed
          && !this.candidatesAt(e.clientX, e.clientY).length) this.clearSelection();
      if (e.detail !== 3) return;
      this.onSelectRequest?.({
        candidates: this.candidatesAt(e.clientX, e.clientY),
        clientX: e.clientX,
        clientY: e.clientY,
      });
    }, { signal });

    // A right CLICK (as opposed to the right-drag pan) opens the context menu.
    // What counts as a click, and when the menu opens, is ContextGesture's: on
    // macOS the browser asks for the menu as the button goes DOWN, which used to
    // open it under every right-drag that began on a cell and stall the pan; it
    // opens as the button comes up, on every platform.  The pointer's press is
    // noted first, ahead of the roll and axis drags below, which swallow theirs.
    this.onContextMenu = null;
    const gesture = new ContextGesture();
    const openMenu = ({ x, y }) => {
      const candidates = this.contextCandidatesAt(x, y);
      const first = candidates[0] ?? null;
      // `key` and `point` (in the scene's own frame, as a pick's point is) are
      // the first thing under the pointer, as they always were; `candidates` is
      // all of it.
      this.onContextMenu?.({
        key: first ? first.key : null,
        point: first ? this.toWorld(first.point, this._sourceOf(first.key)) : null,
        candidates,
        clientX: x,
        clientY: y,
      });
    };
    this.webgl.domElement.addEventListener('pointerdown', (e) => {
      gesture.press({ x: e.clientX, y: e.clientY, button: e.button, ctrl: e.ctrlKey });
    }, { capture: true, signal });
    this.webgl.domElement.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const at = gesture.menu({ x: e.clientX, y: e.clientY });
      if (at) openMenu(at);
    }, { signal });
    window.addEventListener('pointerup', (e) => {
      const at = gesture.release({ x: e.clientX, y: e.clientY });
      if (at) openMenu(at);
    }, { signal });
    window.addEventListener('pointercancel', () => gesture.cancel(), { signal });

    // Shift + left-drag rolls about the view axis. The controls are paused
    // for the duration so the drag does not orbit at the same time.
    let rolling = false;
    let rollX = 0;
    this.webgl.domElement.addEventListener('pointerdown', (e) => {
      if (e.button === 0 && e.shiftKey) {
        rolling = true;
        rollX = e.clientX;
        this.controls.enabled = false;
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    }, { capture: true, signal });
    window.addEventListener('pointermove', (e) => {
      if (!rolling) return;
      this.roll((e.clientX - rollX) * 0.01);
      rollX = e.clientX;
    }, { signal });
    window.addEventListener('pointerup', () => {
      if (!rolling) return;
      rolling = false;
      this.controls.enabled = true;
      // A synthetic end so listeners (undo history) see the drag finish, the
      // same signal OrbitControls emits for its own drags.
      this.controls.dispatchEvent({ type: 'end' });
    }, { signal });

    // Single-axis orbits in VIEWPORT axes, complementing the shift-roll:
    // ctrl + left-drag rotates only about the screen-horizontal axis, and
    // cmd (alt on Windows/Linux) + left-drag only about the screen-vertical
    // axis. The axes follow the current view, not the volume.
    let axisDrag = null; // 'pitch' | 'yaw'
    let axisX = 0;
    let axisY = 0;
    this.webgl.domElement.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || e.shiftKey) return;
      const mode = e.ctrlKey ? 'pitch' : (e.metaKey || e.altKey) ? 'yaw' : null;
      if (!mode) return;
      axisDrag = mode;
      axisX = e.clientX;
      axisY = e.clientY;
      this.controls.enabled = false;
      e.preventDefault();
      e.stopImmediatePropagation();
    }, { capture: true, signal });
    window.addEventListener('pointermove', (e) => {
      if (!axisDrag) return;
      // Same feel as a plain orbit: the object follows the mouse, so the
      // camera swings the other way.
      if (axisDrag === 'pitch') {
        this.orbitViewAxis('x', -(e.clientY - axisY) * 0.008);
      } else {
        this.orbitViewAxis('y', -(e.clientX - axisX) * 0.008);
      }
      axisX = e.clientX;
      axisY = e.clientY;
    }, { signal });
    window.addEventListener('pointerup', () => {
      if (!axisDrag) return;
      axisDrag = null;
      this.controls.enabled = true;
      this.controls.dispatchEvent({ type: 'end' });
    }, { signal });

    // Plain left-drag (one finger on a touch screen) orbits freely, with no
    // pole to stop at.  It only queues the turn; _applySpin plays it out each
    // frame with the same easing OrbitControls' damping gave, so the feel of
    // a drag and its glide are unchanged -- only the wall is gone.  Modified
    // drags never get here: the roll and single-axis handlers take them in
    // the capture phase.  A second finger hands over to OrbitControls'
    // pinch-zoom and two-finger pan.
    const touching = new Set();
    let freeDrag = null;
    this.webgl.domElement.addEventListener('pointerdown', (e) => {
      touching.add(e.pointerId);
      if (touching.size > 1) { freeDrag = null; return; }
      if (e.button !== 0 || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
      if (!this.controls.enabled) return;
      freeDrag = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: false };
    }, { signal });
    window.addEventListener('pointermove', (e) => {
      if (!freeDrag || e.pointerId !== freeDrag.id) return;
      const dx = e.clientX - freeDrag.x;
      const dy = e.clientY - freeDrag.y;
      if (!dx && !dy) return;
      freeDrag.x = e.clientX;
      freeDrag.y = e.clientY;
      freeDrag.moved = true;
      // OrbitControls' own rate: a full turn per viewport height.  The object
      // follows the pointer, so the camera swings the other way.
      const perPixel = (2 * Math.PI * this.controls.rotateSpeed)
        / Math.max(this.webgl.domElement.clientHeight, 1);
      this._spin.yaw -= dx * perPixel;
      this._spin.pitch -= dy * perPixel;
    }, { signal });
    const endFreeDrag = (e) => {
      touching.delete(e.pointerId);
      if (!freeDrag || e.pointerId !== freeDrag.id) return;
      // The undo step and the saved camera are taken when the glide settles,
      // not at release -- OrbitControls recorded them mid-glide.
      if (freeDrag.moved) this._spin.endPending = true;
      freeDrag = null;
    };
    window.addEventListener('pointerup', endFreeDrag, { signal });
    window.addEventListener('pointercancel', endFreeDrag, { signal });
  }

  /**
   * Measure the container now.  The ResizeObserver does it a frame after the container gets a
   * size; a caller that has just taken the pane out of hiding and wants it framed against its real
   * size at once (so that the framing is its own to place, not an event that arrives later) asks.
   */
  updateSize() { this._resize(); }

  _resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    // A container with no size is out of sight (another tab, a hidden pane):
    // nothing is drawn for it until it has one again.
    this._hidden = !w || !h;
    if (!w || !h) return;
    this.webgl.setSize(w, h);
    this.perspective.aspect = w / h;
    this.perspective.updateProjectionMatrix();
    this._updateOrtho();
    // A scene built while the container had no size — a background tab, a
    // hidden pane — was framed against a meaningless aspect ratio, so frame it
    // again the first time real dimensions arrive.
    if (!this.framed && !this.bounds.isEmpty()) this.resetView();
  }

  /**
   * The orthographic frustum, sized to the shown cells' bounds.  `keepScale`
   * (a cell shown, hidden, loaded or let go) keeps the microns per pixel of an
   * orthographic view on screen by moving its zoom with the size: the view used
   * to follow the bounds, so hiding the large one of two cells zoomed in 8x about
   * the pair's center and threw the other off screen, and showing it again zoomed
   * back out (c943 beside c21768: 0.318 -> 0.040 um a pixel, 2026-10-22).  A
   * perspective view keeps following them, so turning Orthographic on frames.
   */
  _updateOrtho({ keepScale = false } = {}) {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    const size = this.bounds.isEmpty()
      ? 10
      : this.bounds.getSize(new THREE.Vector3()).length() * 0.6;
    const aspect = w / h;
    if (keepScale && this.camera === this.orthographic && this.orthographic.top > 0) {
      this.orthographic.zoom *= size / this.orthographic.top;
    }
    this.orthographic.left = -size * aspect;
    this.orthographic.right = size * aspect;
    this.orthographic.top = size;
    this.orthographic.bottom = -size;
    this.orthographic.updateProjectionMatrix();
  }

  /**
   * Stop rendering and give back everything this renderer holds.
   *
   * The Render compare panes are made and thrown away as the grid is resized,
   * and contexts are a scarce per-page resource: a page may hold 16 live ones,
   * and the 17th makes the browser drop the OLDEST -- the main viewport's.
   * Measured before this was complete (headless Chrome, 20 pane rebuilds):
   * 105 contexts created, 89 "too many contexts" warnings, the main viewport
   * lost for good, and every disposed renderer still holding its geometries,
   * a texture, its scene and its seven window listeners.  So this takes the
   * listeners off, frees every layer's buffers, materials and textures (the
   * renderer's own `dispose()` frees none of the app's objects), and then
   * ends the context itself, which `webgl.dispose()` does not.
   */
  dispose() {
    if (this._disposed) return;
    this._disposed = true;
    // A flight in progress settles (its promise resolves) while the controls
    // still exist.
    this._cancelFlight();
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this._observer?.disconnect();
    this._abort?.abort();
    this.controls.dispose();
    // Whatever the app wired in stops hearing from a renderer that is gone.
    this.onPick = null;
    this.onSelectRequest = null;
    this.onSelectionChange = null;
    this.onMarkerChange = null;
    this.onStepZ = null;
    this.onSliceStep = null;
    this.onInverted = null;
    this.onFrame = null;
    this.onContextMenu = null;
    this.selection = null;
    this.markers = { above: null, below: null };
    this.slices = { primary: null, above: null, below: null };
    this._previewing = null;
    this.scene.traverse(releaseNode);
    this.scene.environment?.dispose();
    this.scene.environment = null;
    for (const map of [...this._cellLayers(), this.context]) map?.clear();
    this.highlightGroup.clear();
    this.zHide.clear();
    this._scalars.clear();
    this._order.clear();
    this._hiddenCells.clear();
    this.webgl.dispose();
    // Ends the context now rather than at some later garbage collection.
    this.webgl.forceContextLoss();
    this.webgl.domElement.remove();
  }

  /**
   * Draw nothing until told to carry on (`false`).  The scene, camera and
   * controls stay as they are, so un-pausing shows the view as it was left;
   * Render compare pauses a pane whose 3D view is covered by another view.
   */
  setPaused(paused) {
    this._paused = !!paused;
  }

  get paused() { return this._paused; }

  _animate = () => {
    if (this._disposed) return;
    this._raf = requestAnimationFrame(this._animate);
    // A renderer out of sight (a hidden tab or pane, or one told to wait) used
    // to draw every frame all the same: five at boot, four of them in the
    // Compare tab that is not showing.
    if (this._paused || this._hidden) return;
    this._applySpin();
    this.controls.update();
    this._syncPlanes();
    this._refreshHighlight();
    const dir = new THREE.Vector3();
    this.camera.getWorldDirection(dir);
    this.keyLight.position.copy(this.camera.position);
    this.fillLight.position.copy(this.camera.position).addScaledVector(dir, 2).negate();
    this.webgl.render(this.scene, this.camera);
    this.onFrame?.();
  };

  // -- content ------------------------------------------------------------
  setMesh(key, mesh, {
    color = '#2f6fed', opacity = 0.92, vertexColors = null,
    material: finish = null,
  } = {}) {
    this.removeMesh(key);
    if (!mesh || !mesh.n_faces) return null;

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.vertices, 3));
    geometry.setIndex(meshIndex(mesh.faces));
    geometry.computeVertexNormals();

    // Either precomputed RGB triplets (the compare view's difference map) or
    // per-vertex scalars run through the depth colormap.
    let colors = null;
    if (vertexColors && vertexColors.length === mesh.n_vertices * 3) {
      colors = vertexColors;
    } else {
      const values = mesh.vertex_data;
      if (values && values.length === mesh.n_vertices) {
        colors = depthColors(values, this.colormap);
        // Kept so the Colormap controls can recolor without a rebuild.
        this._scalars = this._scalars || new Map();
        this._scalars.set(key, values);
      }
    }
    if (colors) {
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    }
    // The finish is the cell's own (a name or a spec); it survives every
    // rebuild because the caller passes it in each time.
    const spec = finish && typeof finish === 'object' ? finish : materialSpec(finish);
    const material = createSurfaceMaterial(spec, { color, opacity, vertexColors: !!colors });

    // Surface and wireframe are separate children over one shared geometry, so
    // either can be shown without the other. Toggling `material.wireframe`
    // instead would make them mutually exclusive -- turning the wireframe on
    // would take the surface away.
    const object = new THREE.Group();
    object.name = key;

    const solid = new THREE.Mesh(geometry, material);
    solid.userData.part = 'surface';
    // The flat color setVertexColors(key, null) puts back.
    solid.userData.baseColor = color;
    object.add(solid);

    // MeshBasicMaterial: edges should read at a constant brightness rather
    // than being shaded away where the surface turns from the light.
    // A colored mesh colors its wireframe too, so a difference map reads the
    // same whether the pane shows the surface, the wireframe, or both.
    const wireMaterial = new THREE.MeshBasicMaterial({
      color: new THREE.Color(color), wireframe: true,
    });
    if (colors) {
      wireMaterial.vertexColors = true;
      wireMaterial.color.set('#ffffff');
    }
    const wire = new THREE.Mesh(geometry, wireMaterial);
    wire.userData.part = 'wireframe';
    wire.userData.baseColor = color;
    wire.visible = false;
    // Not raycast: clicking a point should hit the surface it looks like it
    // hit, and the two are coincident.
    wire.raycast = () => {};
    object.add(wire);

    this._place(object, this._sourceOf(key));
    this.renderLayer.add(object);
    this.meshes.set(key, object);
    // A layer keeps its place in the draw order through a rebuild.
    if (this._order.has(key)) this.setLayerOrder(key, this._order.get(key));
    this._applyZHide(key);
    this._recomputeBounds();
    return object;
  }

  /** Show or hide the filled surface, independently of the wireframe. */
  setSurface(key, on) {
    const part = this._meshPart(key, 'surface');
    if (part) part.visible = !!on;
  }

  /** Show or hide the triangle edges, independently of the surface. */
  setWireframe(key, on) {
    const part = this._meshPart(key, 'wireframe');
    if (part) part.visible = !!on;
  }

  _meshPart(key, part) {
    const object = this.meshes.get(key);
    if (!object) return null;
    let found = null;
    object.traverse((child) => {
      if (child.userData.part === part) found = child;
    });
    return found;
  }

  /**
   * Traced centerline for one cell.
   *
   * `width` is in microns. At zero the skeleton is drawn as `LineSegments`,
   * which is cheap but always one pixel wide -- WebGL ignores
   * `LineBasicMaterial.linewidth` on essentially every platform, so a width
   * control backed by it would do nothing. Any width above zero therefore
   * builds real tube geometry instead, which is affordable here: the largest
   * skeleton in the collection is ~130k triangles against its mesh's ~830k.
   */
  setSkeleton(key, skeleton, { color = '#ffffff', width = 0, opacity = 1 } = {}) {
    this.removeSkeleton(key);
    if (!skeleton || !skeleton.segments.length) return;

    const object = width > 0
      ? this._skeletonTubes(skeleton, color, width, opacity)
      : this._skeletonLines(skeleton, color, opacity);
    if (!object) return;
    object.userData.skeletonWidth = width;
    object.userData.skeletonStyle = { color, width, opacity };
    // Kept for selection: a triple click names the branch under the pointer.
    object.userData.skeleton = skeleton;
    object.name = key;
    this._place(object, this._sourceOf(key));
    this.renderLayer.add(object);
    this.skeletons.set(key, object);
    this._applyZHide(key);
    this._recomputeBounds();
  }

  /**
   * Bend a cell's skeleton to manual moves not yet applied: `deltas`, `Map(id -> [dx, dy,
   * dz])` um (manualmoves.js `pendingDeltas`), carries each moved annotation's points;
   * null or empty puts the skeleton the server sent back.  Drawn again only when what it
   * draws changes, in its own style, as visible as it was.
   */
  nudgeSkeleton(key, deltas) {
    const object = this.skeletons.get(key);
    if (!object) return;
    const sig = deltas?.size ? JSON.stringify([...deltas].sort((a, b) => a[0] - b[0])) : '';
    if ((object.userData.nudgeSig ?? '') === sig) return;
    const base = object.userData.baseSkeleton ?? object.userData.skeleton;
    const visible = object.visible;
    this.setSkeleton(key, nudgedSkeleton(base, deltas), object.userData.skeletonStyle);
    const drawn = this.skeletons.get(key);
    if (!drawn) return;
    drawn.userData.baseSkeleton = base;
    drawn.userData.nudgeSig = sig;
    drawn.visible = visible;
  }

  _skeletonLines(skeleton, color, opacity) {
    const positions = [];
    for (const segment of skeleton.segments) {
      for (let i = 0; i < segment.points_um.length - 1; i += 1) {
        positions.push(...segment.points_um[i], ...segment.points_um[i + 1]);
      }
    }
    if (!positions.length) return null;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const material = new THREE.LineBasicMaterial({ color: new THREE.Color(color) });
    setOpacity(material, opacity);
    return new THREE.LineSegments(geometry, material);
  }

  _skeletonTubes(skeleton, color, width, opacity) {
    const group = new THREE.Group();
    const material = new THREE.MeshPhongMaterial({
      color: new THREE.Color(color), shininess: 30, side: THREE.DoubleSide,
    });
    setOpacity(material, opacity);
    for (const segment of skeleton.segments) {
      const points = segment.points_um;
      if (!points || points.length < 2) continue;
      const curve = new THREE.CatmullRomCurve3(
        points.map((p) => new THREE.Vector3(p[0], p[1], p[2])),
      );
      // One tubular division per traced point: the centerline is already
      // sampled at the annotation spacing, so subdividing further only adds
      // triangles without adding shape.
      const geometry = new THREE.TubeGeometry(
        curve, Math.max(points.length - 1, 1), width / 2, 6, false,
      );
      group.add(new THREE.Mesh(geometry, material));
    }
    return group.children.length ? group : null;
  }

  /** Wireframe color and opacity, independent of the surface. */
  setWireframeStyle(key, { color, opacity } = {}) {
    const part = this._meshPart(key, 'wireframe');
    if (!part) return;
    if (color) {
      part.material.color.set(color);
      part.userData.baseColor = color;
    }
    setOpacity(part.material, opacity);
  }

  /**
   * The raw Viking annotations, each flat in its section plane: a disc as a
   * ring at exactly the radius the annotator set, a closed curve as a band
   * along its traced outline (`outlines`, from the closed-curves endpoint).
   * Closed-curve rows in `nodes` carry a radius too -- Viking stores one --
   * but it is not what was traced, so they are skipped as rings. Everything
   * shares one geometry + material so thousands of annotations stay one
   * draw call.
   *
   * `through` draws the rings over the surface regardless of depth. A ring
   * sits exactly on the annotated rim, which the smoothed surface encloses by
   * a few tens of nanometres, and the top and bottom rings of a soma stack lie
   * under its caps -- with depth testing on, an opaque soma shows almost none
   * of its annotations ("the top of the soma is not being shown", RC1 c606,
   * 2026-09-17).
   *
   * `ringColors` and `outlineColors` give single annotations a color of their
   * own (Render compare's error per annotation), still in the one draw call:
   * the buffer carries a color per vertex.  Each is an array parallel to
   * `nodes` / `outlines`, a Map from Location ID (`node.id`, `location_id`), or
   * a function `(item, index) => color`; a color is a CSS string or `[r, g, b]`
   * in linear light, as `setPoints` takes them.  An annotation with none takes
   * `color`.  The layer then ignores a later `setDiscStyle` color (there is no
   * single color to change); opacity and depth behavior still apply.
   */
  setDiscs(key, nodes, options = {}) {
    this.removeDiscs(key);
    const object = this._ringMesh(key, nodes, options);
    if (!object) return;
    this._place(object, this._sourceOf(key));
    this.renderLayer.add(object);
    this.discs.set(key, object);
    this._reachDirty = true;
    this._applyZHide(key);
    this._refreshBoundsIfBare();
  }

  /**
   * The rings and outline bands of a cell's annotations as one mesh (setDiscs'
   * and GraphApp's), with what selection needs kept on it (`rings`, `bands`,
   * `stroke`); null when there is nothing to draw.  Not placed or added.
   *
   * `lines` draws each ring and outline as a line in place of a band (GraphApp's,
   * 2026-10-22): no width in X and Y and no depth in Z, yet one pixel on the
   * screen at any zoom and from any side, where a band thinner than a pixel
   * breaks up zoomed out and a flat one vanishes edge-on.  `stroke` and `depth`
   * are then not used, and `stroke` is kept as 0.
   */
  _ringMesh(key, nodes, {
    color = '#ffffff', opacity = 0.85, stroke = 0.1, outlines = [], through = true,
    depth = 0, ringColors = null, outlineColors = null, lines = false,
  } = {}) {
    const SEG = RING_SEGMENTS;
    // Kept with their place in `nodes`, which a color array is indexed by.
    const ringAt = [];
    const rings = nodes.filter((n, i) => {
      const ok = Number.isFinite(n.radius_um) && n.radius_um > 0 && n.type_code !== CLOSED_CURVE;
      if (ok) ringAt.push(i);
      return ok;
    });
    // Every polygon to band: the outline and any cut-outs, closing point dropped.
    const polygons = [];
    for (const [oi, o] of (outlines || []).entries()) {
      for (const pts of [o.outline_um, ...(o.cutouts_um || [])]) {
        const poly = closedPolygon(pts);
        if (poly.length < 3) continue;
        // Center and reach let a triple click skip outlines it is nowhere near.
        const cx = poly.reduce((sum, q) => sum + q[0], 0) / poly.length;
        const cy = poly.reduce((sum, q) => sum + q[1], 0) / poly.length;
        const reach = poly.reduce((r, q) => Math.max(r, Math.hypot(q[0] - cx, q[1] - cy)), 0);
        polygons.push({
          pts: poly, z: o.z_um, id: o.location_id, section: o.z_section, cx, cy, reach, oi,
        });
      }
    }
    if (!rings.length && !polygons.length) return null;
    const nBand = polygons.reduce((n, p) => n + p.pts.length, 0);
    if (lines) {
      return this._ringLines(key, rings, ringAt, polygons, nBand, {
        color, opacity, outlines, through, ringColors, outlineColors,
      });
    }
    // `depth` gives the flat ribbon a height along Z, centered on the section
    // plane: a band with a rectangular cross-section, stroke wide and depth
    // tall (asked for 2026-09-19 -- a ribbon in the section plane has no
    // thickness, so from the side, where Z levels are read, the annotations
    // vanished).  Zero keeps the ribbon exactly as it was: two vertices a
    // sample and one quad; a band is four vertices and four quads.
    const deep = depth > 0;
    const half = deep ? depth / 2 : 0;
    const PER = deep ? 4 : 2;
    const verts = new Float32Array((rings.length * SEG + nBand) * PER * 3);
    const index = new Uint32Array((rings.length * SEG + nBand) * (deep ? 24 : 6));
    let vi = 0;
    let fi = 0;
    const tri = (a, b, c, d) => {
      index[fi] = a; index[fi + 1] = b; index[fi + 2] = c;
      index[fi + 3] = c; index[fi + 4] = b; index[fi + 5] = d;
      fi += 6;
    };
    // Per sample: outer, inner (at +half when deep), then outer, inner at
    // -half.  Quads between consecutive samples: the top ribbon, and for a
    // band the bottom ribbon and the outer and inner walls.
    const quad = (base, k, n) => {
      const o = base + k * PER;
      const nx = base + ((k + 1) % n) * PER;
      tri(o, nx, o + 1, nx + 1);
      if (!deep) return;
      tri(o + 2, nx + 2, o + 3, nx + 3);
      tri(o, nx, o + 2, nx + 2);
      tri(o + 1, nx + 1, o + 3, nx + 3);
    };
    const put = (xo, yo, xi, yi, z) => {
      verts[vi] = xo; verts[vi + 1] = yo; verts[vi + 2] = z + half;
      verts[vi + 3] = xi; verts[vi + 4] = yi; verts[vi + 5] = z + half;
      vi += 6;
      if (!deep) return;
      verts[vi] = xo; verts[vi + 1] = yo; verts[vi + 2] = z - half;
      verts[vi + 3] = xi; verts[vi + 4] = yi; verts[vi + 5] = z - half;
      vi += 6;
    };
    for (const n of rings) {
      const [cx, cy, cz] = n.xyz_um;
      const rOut = n.radius_um + stroke / 2;
      const rIn = Math.max(n.radius_um - stroke / 2, n.radius_um * 0.02);
      const base = vi / 3;
      for (let k = 0; k < SEG; k += 1) {
        const a = (k / SEG) * Math.PI * 2;
        const ca = Math.cos(a);
        const sa = Math.sin(a);
        put(cx + rOut * ca, cy + rOut * sa, cx + rIn * ca, cy + rIn * sa, cz);
        quad(base, k, SEG);
      }
    }
    for (const { pts, z } of polygons) {
      const n = pts.length;
      const base = vi / 3;
      for (let k = 0; k < n; k += 1) {
        const [x, y] = pts[k];
        const [px, py] = pts[(k + n - 1) % n];
        const [qx, qy] = pts[(k + 1) % n];
        // The band's normal bisects the two edges at this sample, so it thins
        // slightly at a sharp corner instead of spiking there.
        let tx = qx - px;
        let ty = qy - py;
        let len = Math.hypot(tx, ty);
        if (len < 1e-9) { tx = qx - x; ty = qy - y; len = Math.hypot(tx, ty); }
        if (len < 1e-9) { tx = 1; ty = 0; len = 1; }
        const nx = (-ty / len) * (stroke / 2);
        const ny = (tx / len) * (stroke / 2);
        put(x + nx, y + ny, x - nx, y - ny, z);
        quad(base, k, n);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(verts, 3));
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
    // Colors of their own for single annotations: one more attribute, so the
    // layer is still one draw call.  The material color is then white (it
    // multiplies the vertex colors) and an annotation with no color takes
    // the layer color through the buffer.
    const layerColor = new THREE.Color(color);
    const painted = ringColors !== null || outlineColors !== null;
    if (painted) {
      const toRgb = (value) => {
        if (Array.isArray(value) || ArrayBuffer.isView(value)) return [value[0], value[1], value[2]];
        const c = new THREE.Color(value);
        return [c.r, c.g, c.b];
      };
      geometry.setAttribute('color', new THREE.BufferAttribute(discColorBuffer({
        ringCount: rings.length,
        ringRgb: resolveColors(ringColors, rings, (n) => n.id, toRgb, ringAt),
        polygonRgb: resolveColors(outlineColors, polygons.map((p) => outlines[p.oi]),
          (o) => o.location_id, toRgb, polygons.map((p) => p.oi)),
        polygonSamples: polygons.map((p) => p.pts.length),
        segments: SEG,
        perSample: PER,
        fallback: [layerColor.r, layerColor.g, layerColor.b],
      }), 3));
    }
    const material = new THREE.MeshBasicMaterial({
      color: painted ? new THREE.Color(1, 1, 1) : layerColor, side: THREE.DoubleSide,
      vertexColors: painted,
    });
    setOpacity(material, opacity);
    const object = new THREE.Mesh(geometry, material);
    object.raycast = () => {};
    object.name = key;
    object.userData.painted = painted;
    // One draw call is all or nothing, and Firefox refuses one of more than
    // 30 million indices (`webgl.max-vert-ids-per-draw`): RPC1 c2628 with a
    // depth is 38,146,920, so there every annotation vanished at any depth
    // but zero while Chromium drew them (2026-09-19).  Past the budget the
    // rest is drawn by children over the same buffers and the same material,
    // so the style setters keep working on `object.material` alone.
    if (index.length > MAX_DRAW_INDICES) {
      geometry.setDrawRange(0, MAX_DRAW_INDICES);
      for (let start = MAX_DRAW_INDICES; start < index.length; start += MAX_DRAW_INDICES) {
        const part = new THREE.BufferGeometry();
        part.setAttribute('position', geometry.getAttribute('position'));
        if (painted) part.setAttribute('color', geometry.getAttribute('color'));
        part.setIndex(geometry.getIndex());
        part.setDrawRange(start, Math.min(MAX_DRAW_INDICES, index.length - start));
        const child = new THREE.Mesh(part, material);
        child.raycast = () => {};
        object.add(child);
      }
    }
    // Kept for selection, which works in screen space rather than by ray, so
    // a ring seen edge-on can still be triple-clicked.
    object.userData.rings = rings;
    object.userData.bands = polygons;
    object.userData.stroke = stroke;
    applyDiscDepth(object, through);
    return object;
  }

  /**
   * `_ringMesh`'s rings and outlines as lines (its `lines`): one vertex a sample,
   * a segment to the next, every ring and outline closed.  `rings`, `ringAt`,
   * `polygons` and `nBand` are what `_ringMesh` chose to draw.
   */
  _ringLines(key, rings, ringAt, polygons, nBand, {
    color, opacity, outlines, through, ringColors, outlineColors,
  }) {
    const SEG = RING_SEGMENTS;
    const verts = new Float32Array((rings.length * SEG + nBand) * 3);
    const index = new Uint32Array((rings.length * SEG + nBand) * 2);
    let vi = 0;
    let ii = 0;
    const loop = (base, n) => {
      for (let k = 0; k < n; k += 1) {
        index[ii] = base + k;
        index[ii + 1] = base + ((k + 1) % n);
        ii += 2;
      }
    };
    for (const n of rings) {
      const [cx, cy, cz] = n.xyz_um;
      const base = vi / 3;
      for (let k = 0; k < SEG; k += 1) {
        const a = (k / SEG) * Math.PI * 2;
        verts[vi] = cx + n.radius_um * Math.cos(a);
        verts[vi + 1] = cy + n.radius_um * Math.sin(a);
        verts[vi + 2] = cz;
        vi += 3;
      }
      loop(base, SEG);
    }
    for (const { pts, z } of polygons) {
      const base = vi / 3;
      for (const [x, y] of pts) {
        verts[vi] = x; verts[vi + 1] = y; verts[vi + 2] = z;
        vi += 3;
      }
      loop(base, pts.length);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(verts, 3));
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
    const layerColor = new THREE.Color(color);
    const painted = ringColors !== null || outlineColors !== null;
    if (painted) {
      const toRgb = (value) => {
        if (Array.isArray(value) || ArrayBuffer.isView(value)) return [value[0], value[1], value[2]];
        const c = new THREE.Color(value);
        return [c.r, c.g, c.b];
      };
      geometry.setAttribute('color', new THREE.BufferAttribute(discColorBuffer({
        ringCount: rings.length,
        ringRgb: resolveColors(ringColors, rings, (n) => n.id, toRgb, ringAt),
        polygonRgb: resolveColors(outlineColors, polygons.map((p) => outlines[p.oi]),
          (o) => o.location_id, toRgb, polygons.map((p) => p.oi)),
        polygonSamples: polygons.map((p) => p.pts.length),
        segments: SEG,
        perSample: 1,
        fallback: [layerColor.r, layerColor.g, layerColor.b],
      }), 3));
    }
    const material = new THREE.LineBasicMaterial({
      color: painted ? new THREE.Color(1, 1, 1) : layerColor, vertexColors: painted,
    });
    setOpacity(material, opacity);
    const object = new THREE.LineSegments(geometry, material);
    object.raycast = () => {};
    object.name = key;
    object.userData.painted = painted;
    // Past the one-draw budget (see _ringMesh) the rest is drawn by children.
    if (index.length > MAX_DRAW_INDICES) {
      geometry.setDrawRange(0, MAX_DRAW_INDICES);
      for (let start = MAX_DRAW_INDICES; start < index.length; start += MAX_DRAW_INDICES) {
        const part = new THREE.BufferGeometry();
        part.setAttribute('position', geometry.getAttribute('position'));
        if (painted) part.setAttribute('color', geometry.getAttribute('color'));
        part.setIndex(geometry.getIndex());
        part.setDrawRange(start, Math.min(MAX_DRAW_INDICES, index.length - start));
        const child = new THREE.LineSegments(part, material);
        child.raycast = () => {};
        object.add(child);
      }
    }
    object.userData.rings = rings;
    object.userData.bands = polygons;
    object.userData.stroke = 0;
    applyDiscDepth(object, through);
    return object;
  }

  /** Disc color, opacity and depth behavior without rebuilding the rings. */
  setDiscStyle(key, { color, opacity, through } = {}) {
    const object = this.discs.get(key);
    if (!object) return;
    // A layer with colors of its own has no single color to change: its
    // material color is the white that multiplies them.
    if (color && !object.userData.painted) object.material.color.set(color);
    setOpacity(object.material, opacity);
    // Always: the pass the rings belong in depends on both settings.
    applyDiscDepth(object, through ?? object.material.depthTest === false);
  }

  removeDiscs(key) { this._dispose(this.discs, key); }

  /**
   * The annotations as an averaged wireframe (`/annotation-wire`): each
   * annotation's ring in its section plane, lines along the chains of linked
   * annotations, and ties where a branch leaves another annotation.  One
   * `LineSegments`, one pixel wide (WebGL ignores line width), never picked:
   * selection works on the raw rings `setDiscs` draws.  `through` draws it
   * over the surface, as those rings are.
   */
  setAnnoWire(key, wire, { color = '#ffffff', opacity = 0.9, through = true } = {}) {
    this.removeAnnoWire(key);
    if (!wire) return;
    const rings = wire.rings || [];   // [z, x0, y0, x1, y1, ...], closed
    const lines = wire.lines || [];   // [x0, y0, z0, x1, y1, z1, ...]
    const ties = wire.ties || [];     // segment pairs, flat
    let count = Math.floor(ties.length / 6);
    for (const r of rings) count += Math.floor((r.length - 1) / 2);
    for (const l of lines) count += Math.max(Math.floor(l.length / 3) - 1, 0);
    if (!count) return;
    const pos = new Float32Array(count * 6);
    let o = 0;
    for (const r of rings) {
      const z = r[0];
      const n = Math.floor((r.length - 1) / 2);
      for (let k = 0; k < n; k += 1) {
        const a = 1 + 2 * k;
        const b = 1 + 2 * ((k + 1) % n);
        pos[o] = r[a]; pos[o + 1] = r[a + 1]; pos[o + 2] = z;
        pos[o + 3] = r[b]; pos[o + 4] = r[b + 1]; pos[o + 5] = z;
        o += 6;
      }
    }
    for (const l of lines) {
      for (let k = 0; k + 5 < l.length; k += 3) {
        for (let j = 0; j < 6; j += 1) pos[o + j] = l[k + j];
        o += 6;
      }
    }
    pos.set(ties.slice(0, count * 6 - o), o);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const material = new THREE.LineBasicMaterial({ color: new THREE.Color(color) });
    setOpacity(material, opacity);
    const object = new THREE.LineSegments(geometry, material);
    object.raycast = () => {};
    object.name = key;
    applyDiscDepth(object, through);
    this._place(object, this._sourceOf(key));
    this.renderLayer.add(object);
    this.annoWires.set(key, object);
    this._reachDirty = true;
    this._applyZHide(key);
    this._refreshBoundsIfBare();
  }

  /** Annotation wireframe color, opacity and depth behavior, without a rebuild. */
  setAnnoWireStyle(key, { color, opacity, through } = {}) {
    const object = this.annoWires.get(key);
    if (!object) return;
    if (color) object.material.color.set(color);
    setOpacity(object.material, opacity);
    applyDiscDepth(object, through ?? object.material.depthTest === false);
  }

  removeAnnoWire(key) { this._dispose(this.annoWires, key); }

  /**
   * The Position shift layer of one cell: an arrow from where each annotation
   * was to where it is drawn, optionally a ring where it was, and in slice mode
   * a plane at the slice's section (shifts.js and shiftlayer.js build the
   * buffers; this only draws them).
   *
   * `layer`: `{ arrows, rings, plane, recolor, meta }`.  `arrows` and `rings`
   * are `{ positions, colors }` flat Float32Arrays (colors in linear light, three
   * numbers a vertex) drawn as one LineSegments each; `plane` is
   * `{ quad, outline }` at z = 0, moved to its section by `setShiftPlane`;
   * `recolor(inverted)` answers the same buffers' colors for the other
   * background.  `meta` is kept on the group's `userData.layer` for the app.
   *
   * Arrows and rings are drawn `width` screen pixels wide (fatlines.js: two
   * triangles a segment, spread across it in the vertex shader, and a filled
   * head a set number of pixels long; WebGL draws a line one pixel wide whatever
   * it is told).  The line buffers they are built from are kept on each part
   * (`userData.pick`) for the right-click menu, which measures to the segments.
   * Never ray-picked: the layer adds nothing to the scene's bounds, picks or
   * framing, so only Reset view re-frames.  It follows the cell's visibility,
   * volume offset and Z range like the annotation layers do (the material
   * clips), and Flip Z and the orthographic camera need nothing (it is a child
   * of `group`, and the width is worked out on the screen).
   */
  setShifts(key, layer, { opacity = 0.9, through = true, width = WIDTH_DEFAULT } = {}) {
    this.removeShifts(key);
    if (!layer || (!layer.arrows && !layer.rings)) return null;
    const group = new THREE.Group();
    group.name = key;
    group.userData.layer = layer.meta ?? null;
    group.userData.recolor = layer.recolor ?? null;
    const lines = (spec, role) => {
      if (!spec?.positions?.length) return null;
      const object = wideLines(role === 'arrows'
        ? fatArrows(spec.positions, spec.colors) : fatSegments(spec.positions, spec.colors), role);
      object.raycast = () => {};
      object.userData.role = role;
      // The colors as built, which a slice's tint writes over and gives back (setShiftSliceTint).
      object.userData.baseColor = object.geometry.getAttribute('lineColor').array.slice();
      // The segments themselves, never drawn: what the right-click menu measures to
      // (an arrow's shaft alone: the wide arrow draws no barbs).
      const pick = new THREE.BufferGeometry();
      pick.setAttribute('position', new THREE.BufferAttribute(
        role === 'arrows' ? shaftSegments(spec.positions) : spec.positions, 3));
      object.userData.pick = pick;
      group.add(object);
      return object;
    };
    lines(layer.rings, 'rings');
    lines(layer.arrows, 'arrows');
    if (layer.plane) {
      const flat = new THREE.BufferGeometry();
      flat.setAttribute('position', new THREE.BufferAttribute(layer.plane.quad, 3));
      const sheet = new THREE.Mesh(flat, new THREE.MeshBasicMaterial({
        color: new THREE.Color(layer.plane.color || '#ffffff'),
        transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false,
      }));
      sheet.raycast = () => {};
      sheet.userData.role = 'plane';
      const edge = new THREE.BufferGeometry();
      edge.setAttribute('position', new THREE.BufferAttribute(layer.plane.outline, 3));
      const frame = new THREE.LineSegments(edge, new THREE.LineBasicMaterial({
        color: new THREE.Color(layer.plane.color || '#ffffff'), transparent: true, opacity: 0.35,
      }));
      frame.raycast = () => {};
      frame.userData.role = 'plane';
      group.add(sheet, frame);
      group.userData.plane = [sheet, frame];
      sheet.visible = false;
      frame.visible = false;
    }
    this._place(group, this._sourceOf(key));
    this.renderLayer.add(group);
    this.shifts.set(key, group);
    this.setShiftStyle(key, { opacity, through, width });
    this._reachDirty = true;
    this._applyZHide(key);
    return group;
  }

  /**
   * Arrow and ring opacity, depth behavior and width (CSS pixels), without
   * rebuilding anything.
   */
  setShiftStyle(key, { opacity, through, width } = {}) {
    const group = this.shifts.get(key);
    if (!group) return;
    if (through === undefined) through = group.userData.through !== false;
    group.userData.through = through;
    if (opacity !== undefined) group.userData.opacity = opacity;
    const dim = group.userData.dim ?? 1;
    group.traverse((child) => {
      if (child === group || child.userData.role === 'focus') return;
      if (child.userData.role === 'plane') {
        // The plane sits under the arrows and rings, and follows their depth rule.
        child.renderOrder = 2;
        if (child.material.depthTest === through) child.material.needsUpdate = true;
        child.material.depthTest = !through;
        return;
      }
      if (opacity !== undefined) setOpacity(child.material, opacity * dim);
      applyDiscDepth(child, through);
      // Always blended: the tail fades toward it, at any opacity and either depth rule.
      if (!child.material.transparent) {
        child.material.transparent = true;
        child.material.needsUpdate = true;
      }
      if (Number.isFinite(width) && child.userData.setWidth) child.userData.setWidth(width);
      // Above the annotation rings (3) and the cell's own layers.
      child.renderOrder += 1;
    });
  }

  /**
   * Show only a range of the layer (slice mode): `arrows` and `rings` are
   * `{ first, count }` in the vertices of their line buffers (shifts.js's
   * layout), mapped here onto the wide ones.  A draw range, so stepping a slice
   * uploads nothing.  `null` shows everything.
   */
  setShiftWindow(key, range) {
    const group = this.shifts.get(key);
    if (!group) return;
    for (const child of group.children) {
      const role = child.userData.role;
      if (role === 'plane') continue;
      const spec = range?.[role];
      const wide = fatRange(spec, role);
      if (wide) child.geometry.setDrawRange(wide.first, wide.count);
      else child.geometry.setDrawRange(0, Infinity);
      const pick = child.userData.pick;
      if (pick) {
        const own = role === 'arrows' ? shaftRange(spec) : spec;
        if (own) pick.setDrawRange(own.first, own.count);
        else pick.setDrawRange(0, Infinity);
      }
    }
  }

  /** Put the slice plane at `zUm` (the cell's own frame), or hide it with `null`. */
  setShiftPlane(key, zUm, rect = null, color = null) {
    const group = this.shifts.get(key);
    const plane = group?.userData.plane;
    if (!plane) return;
    const on = Number.isFinite(zUm);
    // The plane sized to its section's annotations (shiftlayer.js planeRect), in the
    // plane's yellow; without a rect it keeps the size it was built with.
    if (on && rect) {
      const { x0, y0, x1, y1 } = rect;
      const [sheet, frame] = plane;
      sheet.geometry.getAttribute('position').array.set([
        x0, y0, 0, x1, y0, 0, x1, y1, 0, x0, y0, 0, x1, y1, 0, x0, y1, 0]);
      frame.geometry.getAttribute('position').array.set([
        x0, y0, 0, x1, y0, 0, x1, y0, 0, x1, y1, 0, x1, y1, 0, x0, y1, 0, x0, y1, 0, x0, y0, 0]);
      for (const child of plane) {
        child.geometry.getAttribute('position').needsUpdate = true;
        child.geometry.computeBoundingSphere();
      }
    }
    if (on && color) {
      for (const child of plane) child.material.color.setRGB(color[0], color[1], color[2]);
      plane[0].material.opacity = 0.07;
      plane[1].material.opacity = 0.55;
    }
    for (const child of plane) {
      child.visible = on;
      if (on) child.position.z = zUm;
    }
  }

  /**
   * The slice's colors for one cell, linear RGB: `above` the red marker's, `on` the
   * yellow selection's, `below` the blue marker's (with their stand-ins on a cell
   * whose own color is too near), so a slice reads in the markers' terms.
   */
  sliceColors(key) {
    const rgb = (slot) => { const c = this._tint(slot, key); return [c.r, c.g, c.b]; };
    return { above: rgb('above'), on: rgb('primary'), below: rgb('below') };
  }

  /**
   * Color a slice's window (shiftlayer.js `sliceTint`): for each part, its marks' arrows
   * (and rings) in one color at one alpha, written into the wide lines' color and
   * alpha; `null` gives every mark back its own color at full strength.  Only the
   * window is written, and only it is drawn.
   */
  setShiftSliceTint(key, tint) {
    const group = this.shifts.get(key);
    if (!group) return;
    for (const child of group.children) {
      const role = child.userData.role;
      if (role !== 'arrows' && role !== 'rings') continue;
      const color = child.geometry.getAttribute('lineColor');
      const alpha = child.geometry.getAttribute('lineAlpha');
      if (!color || !alpha) continue;
      if (!tint) {
        if (!child.userData.tinted) continue;
        if (child.userData.baseColor?.length === color.array.length) color.array.set(child.userData.baseColor);
        alpha.array.fill(1);
        child.userData.tinted = false;
      } else {
        const parts = role === 'arrows'
          ? tint.parts.map((p) => ({ ...p, ...fatRange({ first: p.first * 6, count: p.count * 6 }, 'arrows') }))
          : (tint.rings || []).map((p) => ({ ...p, ...fatRange({ first: p.first, count: p.count }, 'rings') }));
        for (const part of parts) {
          const end = Math.min(part.first + part.count, alpha.count);
          for (let v = part.first; v < end; v += 1) {
            color.array[v * 3] = part.rgb[0];
            color.array[v * 3 + 1] = part.rgb[1];
            color.array[v * 3 + 2] = part.rgb[2];
            alpha.array[v] = part.alpha;
          }
        }
        child.userData.tinted = true;
      }
      color.needsUpdate = true;
      alpha.needsUpdate = true;
    }
  }

  removeShifts(key) { this._dispose(this.shifts, key); }

  /**
   * The chosen annotations' own arrows over a cell's layer (shiftlayer.js
   * `focusMarks`): `marks` is `[{ slot, arrow, ring, width }]`, `arrow` and `ring`
   * line positions in the layer's layout.  Each arrow is drawn in its marker's
   * color (the yellow selection, the red marker above it, the blue below), wider
   * than the layer's, over a dark edge and over everything, with a fuller tail;
   * the ring where the annotation was is drawn in the same color.  Children of the
   * cell's layer group, so they follow its visibility, volume offset and Z range,
   * and go with it when the layer is drawn again.  `[]` takes them away.
   */
  setShiftFocus(key, marks = []) {
    const group = this.shifts.get(key);
    if (!group) return;
    for (const child of [...group.children]) {
      if (child.userData.role !== 'focus') continue;
      group.remove(child);
      releaseNode(child);
    }
    const dark = new THREE.Color(0.02, 0.02, 0.03);
    marks.forEach((mark, i) => {
      const tint = this._tint(mark.slot, key);
      const css = Number.isFinite(mark.width) ? mark.width : WIDTH_DEFAULT;
      const wide = Math.max(css * 1.6, css + 2);
      const draw = (buffers, kind, color, width, alpha, order, tail = 1) => {
        const rgb = new Float32Array(buffers.position.length);
        for (let v = 0; v < rgb.length; v += 3) { rgb[v] = color.r; rgb[v + 1] = color.g; rgb[v + 2] = color.b; }
        const object = wideLines({ ...buffers, color: rgb }, kind);
        object.raycast = () => {};
        object.userData.role = 'focus';
        object.userData.slot = mark.slot;
        object.userData.setWidth(width);
        object.material.uniforms.tailAlpha.value = tail;
        object.material.opacity = alpha;
        object.material.depthTest = false;
        object.material.depthWrite = false;
        // Over the selection's own ring (20, 21), and the edge under the color.
        object.renderOrder = order + i * 2;
        group.add(object);
      };
      if (mark.ring?.length) {
        const ring = fatSegments(mark.ring);
        draw(ring, 'rings', dark, wide + 2, 0.55, 30);
        draw(ring, 'rings', tint, wide, 1, 31);
      }
      if (mark.arrow?.length) {
        const arrow = fatArrows(mark.arrow);
        draw(arrow, 'arrows', dark, wide + 3, 0.6, 32, 0.8);
        draw(arrow, 'arrows', tint, wide, 1, 33, 0.75);
      }
    });
    this._applyZHide(key);
  }

  /**
   * Draw a cell's arrows and rings at `factor` of their opacity (0 hides them):
   * the others while an annotation's own arrow is shown (`setShiftFocus`).  The
   * opacity setting is kept apart, so either can change without the other.
   */
  setShiftDim(key, factor = 1) {
    const group = this.shifts.get(key);
    if (!group) return;
    const f = Number.isFinite(factor) ? Math.min(1, Math.max(0, factor)) : 1;
    group.userData.dim = f;
    const base = group.userData.opacity ?? 0.9;
    for (const child of group.children) {
      if (!['arrows', 'rings'].includes(child.userData.role)) continue;
      child.visible = f > 0;
      setOpacity(child.material, base * f);
      // Always blended (the tail fades): setOpacity drops `transparent` at 1.
      if (!child.material.transparent) {
        child.material.transparent = true;
        child.material.needsUpdate = true;
      }
    }
  }

  // -- manual moves ---------------------------------------------------------------------

  /**
   * A cell's manual moves, an arrow each from where its correction drew the
   * annotation to where it was moved (manualmoves.js): `marks` is
   * `[{ from, to, slot }]` in the volume's microns, `slot` the marker the
   * annotation is ('primary', 'above', 'below') or null.  A marked one is drawn in
   * its marker's color and wider; the rest in the light gray of a note.  At true
   * scale, over a dark edge and over everything; never picked.  `[]` removes them.
   */
  setMoveArrows(key, marks = [], { width = WIDTH_DEFAULT } = {}) {
    this.removeMoveArrows(key);
    if (!marks.length) return;
    const group = new THREE.Group();
    group.name = key;
    const dark = new THREE.Color(0.02, 0.02, 0.03);
    const plain = new THREE.Color(this.inverted ? 0.25 : 0.92, this.inverted ? 0.27 : 0.93, this.inverted ? 0.3 : 0.95);
    const draw = (list, color, css, alpha, order) => {
      if (!list.length) return;
      const positions = new Float32Array(list.length * 18);
      list.forEach((m, i) => positions.set([...m.from, ...m.to], i * 18));
      const buffers = fatArrows(positions);
      const rgb = new Float32Array(buffers.position.length);
      for (let v = 0; v < rgb.length; v += 3) { rgb[v] = color.r; rgb[v + 1] = color.g; rgb[v + 2] = color.b; }
      const object = wideLines({ ...buffers, color: rgb }, 'arrows');
      object.raycast = () => {};
      object.userData.role = 'moves';
      object.userData.setWidth(css);
      object.material.uniforms.tailAlpha.value = 0.8;
      object.material.opacity = alpha;
      object.material.depthTest = false;
      object.material.depthWrite = false;
      object.renderOrder = order;
      group.add(object);
    };
    const wide = Math.max(width * 1.6, width + 2);
    const rest = marks.filter((m) => !m.slot);
    draw(rest, dark, width + 2, 0.5, 34);
    draw(rest, plain, width, 0.95, 35);
    for (const slot of ['below', 'above', 'primary']) {
      const mine = marks.filter((m) => m.slot === slot);
      draw(mine, dark, wide + 3, 0.6, 36);
      draw(mine, this._tint(slot, key), wide, 1, 37);
    }
    this._place(group, this._sourceOf(key));
    group.visible = !this._hiddenCells.has(key);
    this.renderLayer.add(group);
    this.moveArrows.set(key, group);
    this._applyZHide(key);
  }

  removeMoveArrows(key) { this._dispose(this.moveArrows, key); }

  /**
   * The rings of what a manual step moves (a section, a run of sections, the annotations
   * ⌘/Ctrl-clicked in): `rings` is `[{ center, radius }]` in the volume's microns, each
   * drawn as a circle in its section plane, in the selection's yellow toward white, thin,
   * over everything.  `[]` takes them away.
   */
  setMoveSet(key, rings = []) {
    this._dispose(this.moveSets, key);
    if (!rings.length) return;
    const sides = 24;
    const positions = new Float32Array(rings.length * sides * 6);
    let o = 0;
    for (const { center: [cx, cy, cz], radius } of rings) {
      const r = Number.isFinite(radius) && radius > 0 ? radius : 0.2;
      for (let k = 0; k < sides; k += 1) {
        const a0 = (k / sides) * Math.PI * 2;
        const a1 = ((k + 1) / sides) * Math.PI * 2;
        positions.set([cx + r * Math.cos(a0), cy + r * Math.sin(a0), cz,
          cx + r * Math.cos(a1), cy + r * Math.sin(a1), cz], o);
        o += 6;
      }
    }
    const buffers = fatSegments(positions);
    const tint = this._tint('primary', key);
    const rgb = new Float32Array(buffers.position.length);
    for (let v = 0; v < rgb.length; v += 3) {
      rgb[v] = tint.r + (1 - tint.r) * 0.4; rgb[v + 1] = tint.g + (1 - tint.g) * 0.4; rgb[v + 2] = tint.b + (1 - tint.b) * 0.4;
    }
    const object = wideLines({ ...buffers, color: rgb }, 'rings');
    object.raycast = () => {};
    object.userData.role = 'moveset';
    object.userData.setWidth(2);
    object.material.opacity = 0.85;
    object.material.depthTest = false;
    object.material.depthWrite = false;
    object.renderOrder = 33;
    const group = new THREE.Group();
    group.name = key;
    group.add(object);
    this._place(group, this._sourceOf(key));
    group.visible = !this._hiddenCells.has(key);
    this.renderLayer.add(group);
    this.moveSets.set(key, group);
    this._applyZHide(key);
  }

  /**
   * Draw a marker's ring `delta` ([dx, dy, dz] um, volume frame) from where it is: a
   * manual move not yet applied to the cell (null puts it back).  Nothing is rebuilt.
   */
  nudgeHighlight(slot, delta) {
    if (delta && delta.some((v) => v)) this._nudges.set(slot, [...delta]);
    else this._nudges.delete(slot);
    for (const object of this.highlightGroup.children) {
      if (object.userData.slot === slot) this._placeHighlight(object);
    }
  }

  /** A highlight at its cell's place, plus its slot's nudge. */
  _placeHighlight(object) {
    this._place(object, this._sourceOf(object.userData.key));
    // The move is the slot's own annotation's, not the rest of its z-slice's.
    const nudge = object.userData.extra ? null : this._nudges.get(object.userData.slot);
    if (nudge) object.position.add(new THREE.Vector3(nudge[0], nudge[1], nudge[2]));
  }

  /**
   * The viewport's axes in the volume's frame: `{ right, up, out }`, unit vectors --
   * right and up along the screen, out of it toward the viewer -- as the volume's
   * X, Y and Z see them (Flip Z taken back).  What a manual move along Xp, Yp or Zp
   * follows.
   */
  viewAxes() {
    this.camera.updateMatrixWorld(true);
    const e = this.camera.matrixWorld.elements;
    const axis = (c) => {
      const v = new THREE.Vector3(e[c * 4], e[c * 4 + 1], e[c * 4 + 2]).normalize();
      return [v.x, v.y, this.flipZ ? -v.z : v.z];
    };
    return { right: axis(0), up: axis(1), out: axis(2) };
  }

  /** The viewport changed background: give each layer the colors made for it. */
  _recolorShifts() {
    for (const group of this.shifts.values()) {
      const colors = group.userData.recolor?.(this.inverted);
      if (!colors) continue;
      for (const child of group.children) {
        const role = child.userData.role;
        const next = colors[role] ? fatColors(colors[role], role) : null;
        const attr = child.geometry.getAttribute('lineColor');
        if (next && attr && attr.array.length === next.length) {
          attr.array.set(next);
          attr.needsUpdate = true;
          // A slice's tint is put back over these by the next syncSlice.
          child.userData.baseColor = next.slice();
        }
      }
      if (colors.plane && group.userData.plane) {
        for (const child of group.userData.plane) child.material.color.set(colors.plane);
      }
    }
  }

  /**
   * A cloud of colored points, a fixed number of screen pixels across: the
   * Render compare tab's annotation-rim samples, each colored by its distance
   * to the rendered surface.  Drawn through the surface, as the rings are --
   * a rim the render swallowed is exactly what the colors are there to show.
   * `xyz` and `colors` are flat, three numbers a point.
   */
  setPoints(key, xyz, colors, { size = 3 } = {}) {
    this.removePoints(key);
    if (!xyz?.length) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(Float32Array.from(xyz), 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(Float32Array.from(colors), 3));
    const object = new THREE.Points(geometry, new THREE.PointsMaterial({
      size, sizeAttenuation: false, vertexColors: true,
      transparent: true, depthTest: false, depthWrite: false,
    }));
    object.raycast = () => {};
    object.renderOrder = 4;
    object.name = key;
    this._place(object, this._sourceOf(key));
    this.renderLayer.add(object);
    this.points.set(key, object);
    this._reachDirty = true;
    this._applyZHide(key);
    this._refreshBoundsIfBare();
  }

  removePoints(key) { this._dispose(this.points, key); }

  /**
   * Line segments, one pixel wide (WebGL ignores line width) and never picked:
   * an intersection line, an iso-line, an arrow per correction.  `segments` is
   * flat, six numbers a segment (x0 y0 z0 x1 y1 z1) in the key's volume
   * frame; a segment with a non-finite coordinate is dropped.  `colors` gives a
   * color per end (six numbers a segment) or per segment (three), in linear
   * light as `setPoints` takes them; without it the lines are `color`, a CSS
   * color.  `through` draws them over the surfaces at the order of the point
   * cloud, which a line needs because lines take no polygon offset and would
   * otherwise z-fight the surface they lie on.  Follows the cell's visibility,
   * volume offset and Z range like every other layer.  Returns the object, or
   * null when nothing was drawable.
   */
  setLines(key, segments, {
    color = '#ffffff', colors = null, through = true, opacity = 1,
  } = {}) {
    this.removeLines(key);
    const built = segmentBuffers(segments, { colors });
    if (!built.count) return null;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(built.positions, 3));
    if (built.colors) geometry.setAttribute('color', new THREE.BufferAttribute(built.colors, 3));
    const material = new THREE.LineBasicMaterial({
      color: built.colors ? new THREE.Color(1, 1, 1) : new THREE.Color(color),
      vertexColors: !!built.colors,
    });
    setOpacity(material, opacity);
    const object = new THREE.LineSegments(geometry, material);
    object.raycast = () => {};
    object.name = key;
    object.userData.painted = !!built.colors;
    applyDiscDepth(object, through);
    if (through) object.renderOrder = 4;
    this._place(object, this._sourceOf(key));
    this.renderLayer.add(object);
    this.lines.set(key, object);
    this._reachDirty = true;
    this._applyZHide(key);
    this._refreshBoundsIfBare();
    return object;
  }

  /** A line layer's color (when it has a single one), opacity and depth behavior, without a rebuild. */
  setLineStyle(key, { color, opacity, through } = {}) {
    const object = this.lines.get(key);
    if (!object) return;
    if (color && !object.userData.painted) object.material.color.set(color);
    setOpacity(object.material, opacity);
    applyDiscDepth(object, through ?? object.material.depthTest === false);
    if (object.material.depthTest === false) object.renderOrder = 4;
  }

  removeLines(key) { this._dispose(this.lines, key); }

  /**
   * Color a surface per vertex, in place, or take the colors off again.
   *
   * `rgb` is flat, three numbers a vertex in linear light, as many vertices as
   * the mesh has (the wrong length changes nothing and answers false); null
   * gives the surface back its flat color.  A recolor costs a buffer write --
   * milliseconds on a 400k-face mesh against the rebuild `setMesh` is -- so a
   * threshold slider can repaint at interactive speed.  The wireframe follows
   * the surface, and the camera is not touched.
   */
  setVertexColors(key, rgb) {
    const solid = this._meshPart(key, 'surface');
    const wire = this._meshPart(key, 'wireframe');
    if (!solid) return false;
    const geometry = solid.geometry;
    const count = geometry.getAttribute('position').count;
    if (rgb && rgb.length !== count * 3) return false;
    // Explicit colors win over the depth colormap, and so does having none.
    this._scalars.delete(key);
    const material = solid.material;
    const spec = material.userData?.materialSpec;
    if (rgb) {
      const attr = geometry.getAttribute('color');
      if (attr && attr.count === count) {
        attr.array.set(rgb);
        attr.needsUpdate = true;
      } else {
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(rgb, 3));
      }
    }
    const on = !!rgb;
    if (material.vertexColors !== on) {
      const base = solid.userData.baseColor || '#2f6fed';
      material.vertexColors = on;
      material.color.set(on ? '#ffffff' : base);
      if (spec) applyMaterialSpec(material, spec, { color: base, vertexColors: on });
      material.needsUpdate = true;
    }
    if (wire && wire.material.vertexColors !== on) {
      wire.material.vertexColors = on;
      wire.material.color.set(on ? '#ffffff' : (wire.userData.baseColor || '#202020'));
      wire.material.needsUpdate = true;
    }
    return true;
  }

  /**
   * Where a surface layer sits in the draw order, so two surfaces of one cell
   * keep their order as the camera turns.  three.js sorts the translucent
   * objects by the depth of each geometry's bounding-sphere center, so two
   * meshes of one cell swap places as you orbit; a group's `renderOrder` is
   * the first thing it sorts by, so the layer group takes `index - 100`
   * and layers draw in index order, before the through-rings (order 3) and the
   * points and lines (4), which stay on top.  The surface also gets a positive
   * polygon offset of `index`, so on a tie in depth the earlier layer wins and
   * a later one shows only where it protrudes.  Index 0 is today's look
   * exactly (no offset); null forgets the order.  The order is kept across a
   * rebuild of the layer (`setMesh`).
   */
  setLayerOrder(key, index) {
    const given = Number.isFinite(index);
    if (given) this._order.set(key, index);
    else this._order.delete(key);
    const object = this.meshes.get(key);
    if (!object) return false;
    const offset = given && index > 0 ? index : 0;
    object.renderOrder = given ? index - LAYER_ORDER_BASE : 0;
    object.traverse((child) => {
      if (child.userData.part !== 'surface') return;
      child.material.polygonOffset = offset > 0;
      child.material.polygonOffsetFactor = offset;
      child.material.polygonOffsetUnits = offset;
    });
    return true;
  }

  /**
   * Point the camera at a point cloud and whatever surfaces are already in
   * the scene.  The first cell of a load into an empty viewer shows its
   * waiting annotations (app.js showWaitingPoints) before the load frames
   * anything -- it frames once, at its end -- and would otherwise sit
   * wherever the camera last looked.  Next to a surface or skeleton the cloud
   * does not stay in the scene's bounds (a scene of rings, lines and points
   * alone is framed on them: `_recomputeBounds`).
   */
  framePoints(key) {
    const object = this.points.get(key);
    if (!object) return false;
    object.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(object);
    if (box.isEmpty()) return false;
    const kept = this.bounds.clone();
    this.bounds.union(box);
    this.resetView();
    this.bounds.copy(kept);
    return true;
  }

  /**
   * Point the camera at a cell's shown layers (those `seesAnything` judges) and
   * whatever surfaces are already in the scene, as `framePoints` does for a cloud.
   * A cell whose build made no surface is drawn as its rings, nodes or skeleton,
   * and next to another cell's surface its rings are not in the scene's bounds: Reset
   * view framed the other cell and left this one out of sight.  False, moving
   * nothing, when the cell shows nothing.
   */
  frameCell(key) {
    const box = new THREE.Box3();
    for (const map of [this.meshes, this.skeletons, this.discs, this.annoWires, this.lines, this.points,
      this.graphs]) {
      const object = map.get(key);
      if (!object || !object.visible || object.parent?.visible === false) continue;
      object.updateMatrixWorld(true);
      box.union(new THREE.Box3().setFromObject(object));
    }
    if (box.isEmpty()) return false;
    const kept = this.bounds.clone();
    this.bounds.union(box);
    this.resetView();
    this.bounds.copy(kept);
    return true;
  }

  /** Skeleton color and opacity, independent of the cell's mesh. */
  setSkeletonStyle(key, { color, opacity } = {}) {
    const object = this.skeletons.get(key);
    if (!object) return;
    object.traverse((child) => {
      if (!child.material) return;
      if (color) child.material.color.set(color);
      setOpacity(child.material, opacity);
    });
  }

  /**
   * Synapse markers for one cell, one child per synapse type.
   *
   * Each child is tagged with its type so color, opacity and visibility can be
   * addressed per type without rebuilding the others.
   */
  setSynapses(key, meshesByType) {
    this.removeSynapses(key);
    const group = new THREE.Group();
    for (const { mesh, color, type, opacity, visible } of meshesByType) {
      if (!mesh || !mesh.n_faces) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.vertices, 3));
      geometry.setIndex(meshIndex(mesh.faces));
      geometry.computeVertexNormals();
      const child = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({
        color: new THREE.Color(color), shininess: 60,
      }));
      // Remembered so a per-cell override can be cleared back to the Viking
      // per-type color without rebuilding the mesh.
      child.userData.typeColor = color;
      child.userData.typeName = type;
      if (visible !== undefined) child.visible = visible;
      setOpacity(child.material, opacity);
      group.add(child);
    }
    if (!group.children.length) return;
    group.name = key;
    this._place(group, this._sourceOf(key));
    this.renderLayer.add(group);
    this.synapses.set(key, group);
    this._applyZHide(key);
  }

  removeMesh(key) {
    // The depth scalars belong to the mesh they colored: a rebuilt mesh with
    // colors of its own must not be repainted from the old ones by setColormap.
    this._scalars.delete(key);
    this._dispose(this.meshes, key);
  }
  removeSynapses(key) { this._dispose(this.synapses, key); }
  removeSkeleton(key) { this._dispose(this.skeletons, key); }

  /**
   * Every per-cell layer, in one place.  A walk over "the cell's objects"
   * (offsets, visibility, Z range, removal) takes this list instead of its own,
   * so a layer added here is in all of them: the points layer was in three of
   * six hand-written lists (2026-10-15 audit).
   */
  _cellLayers() {
    return [this.meshes, this.skeletons, this.synapses, this.discs, this.annoWires,
      this.shifts, this.points, this.lines, this.moveArrows, this.moveSets, this.graphs];
  }

  removeAll(key) {
    this.removeMesh(key);
    this.removeSynapses(key);
    this.removeSkeleton(key);
    this.removeDiscs(key);
    this.removeAnnoWire(key);
    this.removeShifts(key);
    this.removeMoveArrows(key);
    this._dispose(this.moveSets, key);
    this.removePoints(key);
    this.removeLines(key);
    this.removeGraphCell(key);
    this.zHide.delete(key);
    this._hiddenCells.delete(key);
    if (this.selection?.key === key) this.clearSelection();
    else this._dropMarkersOf(key);
    this._dropSlicesOf(key);
    this._recomputeBounds();
  }

  _dispose(map, key) {
    const object = map.get(key);
    if (!object) return;
    // A highlight never outlives the layer it was drawn from.
    if (this.selection?.key === key && (this._layerOf(this.selection.kind) === map
        || (map === this.graphs && this.look === 'graph'))) {
      this.clearSelection();
    } else if (map === this.discs) {
      this._dropMarkersOf(key);
    }
    // The surface and wireframe share one geometry, so track what has already
    // been released rather than disposing it once per child.
    const freed = new Set();
    object.traverse((child) => {
      if (child.geometry && !freed.has(child.geometry)) {
        freed.add(child.geometry);
        child.geometry.dispose();
      }
      if (Array.isArray(child.material)) child.material.forEach((m) => m.dispose());
      else child.material?.dispose();
    });
    object.parent?.remove(object);
    map.delete(key);
    this._reachDirty = true;
    this._refreshBoundsIfBare();
  }

  /**
   * Re-measure the scene when no surface or skeleton is in it: then its bounds
   * are those of the rings, lines and points (`_recomputeBounds`), and they
   * change as those come and go.  With a surface present nothing here runs,
   * so a cell's rings changing never moves the bounds the surface set.
   */
  _refreshBoundsIfBare() {
    if (!this.meshes.size && !this.skeletons.size) this._recomputeBounds();
  }

  // -- volume offsets -------------------------------------------------------
  // Every object is built in its own volume's micron frame. A per-volume
  // translation lets cells from several volumes share the scene side by side
  // or on top of one another (asked for 2026-09-17). Objects carry their
  // volume in `userData.source`; picks and fly-to targets are converted, so
  // what the readout shows and what Viking is told stay in each volume's own
  // frame (ground rule 11: an ID, and a coordinate, travels with its volume).
  // World = S · (local + offset), S the Flip Z mirror; both directions honor
  // it, which also puts a flipped scene's pick readout right.

  /** The volume a scene key belongs to (`source/id`), or null. */
  _sourceOf(key) {
    if (!key) return null;
    const text = String(key);
    const cut = text.indexOf('/');
    return cut > 0 ? text.slice(0, cut) : null;
  }

  _place(object, source) {
    object.userData.source = source || null;
    const offset = source ? this.volumeOffsets.get(source) : null;
    if (offset) object.position.copy(offset); else object.position.set(0, 0, 0);
  }

  /** Shift every object of one volume by `xyz` microns; zeros or null clear it. */
  setVolumeOffset(source, xyz) {
    const v = new THREE.Vector3();
    if (Array.isArray(xyz)) v.set(Number(xyz[0]) || 0, Number(xyz[1]) || 0, Number(xyz[2]) || 0);
    if (v.lengthSq() === 0) this.volumeOffsets.delete(source);
    else this.volumeOffsets.set(source, v);
    const maps = this._cellLayers();
    if (this.context) maps.push(this.context);
    for (const map of maps) {
      for (const object of map.values()) {
        if (object.userData.source === source) object.position.copy(v);
      }
    }
    for (const object of this.highlightGroup.children) {
      if (object.userData.source === source) this._placeHighlight(object);
    }
    // The Z-range planes are in world space and move with the volume.
    for (const key of this.zHide.keys()) this._applyZHide(key);
    this._recomputeBounds();
  }

  /** One volume's shift as [x, y, z] microns (zeros when none). */
  volumeOffset(source) {
    const v = this.volumeOffsets.get(source);
    return v ? v.toArray() : [0, 0, 0];
  }

  /** Every non-zero shift, {source: [x, y, z]} -- what the session stores. */
  volumeOffsetMap() {
    const out = {};
    for (const [source, v] of this.volumeOffsets) out[source] = v.toArray();
    return out;
  }

  /** A point in `source`'s frame -> where it is drawn (array of 3). */
  toWorld(point, source) {
    const p = Array.isArray(point) ? [point[0], point[1], point[2]] : [point.x, point.y, point.z];
    const off = this.volumeOffset(source);
    const out = [p[0] + off[0], p[1] + off[1], p[2] + off[2]];
    if (this.flipZ) out[2] = -out[2];
    return out;
  }

  /** A drawn point -> `source`'s own frame (array of 3). */
  toVolume(point, source) {
    const p = Array.isArray(point) ? [point[0], point[1], point[2]] : [point.x, point.y, point.z];
    if (this.flipZ) p[2] = -p[2];
    const off = this.volumeOffset(source);
    return [p[0] - off[0], p[1] - off[1], p[2] - off[2]];
  }

  _pickInVolume() {
    if (!this.pickedPoint) return null;
    const [x, y, z] = this.toVolume(this.pickedPoint, this._sourceOf(this.pickedKey));
    return new THREE.Vector3(x, y, z);
  }

  /** Extent of one volume's cells in that volume's own frame, or null. */
  sourceBounds(source) {
    const box = new THREE.Box3();
    for (const map of [this.meshes, this.skeletons, this.discs, this.annoWires]) {
      for (const object of map.values()) {
        if (object.userData.source !== source) continue;
        object.traverse((child) => {
          if (!child.geometry) return;
          if (!child.geometry.boundingBox) child.geometry.computeBoundingBox();
          if (child.geometry.boundingBox) box.union(child.geometry.boundingBox);
        });
      }
    }
    return box.isEmpty() ? null : { min: box.min.toArray(), max: box.max.toArray() };
  }

  /**
   * Neuron color, opacity and visibility.
   *
   * Visibility covers the synapses and skeleton too, but color and opacity do
   * not: synapses carry per-type Viking colors, and repainting them with the
   * cell color -- which this did -- silently destroyed that distinction the
   * first time anyone touched the color swatch.  Use `setSynapseStyle` and
   * `setSkeletonStyle` for those.
   */
  setStyle(key, { color, opacity, visible } = {}) {
    let recompute = false;
    for (const map of this._cellLayers()) {
      const object = map.get(key);
      if (!object) continue;
      if (visible !== undefined) {
        object.visible = visible;
        recompute = true;
      }
      // Synapses carry per-type colors, and the skeleton has its own color and
      // opacity. Applying the mesh's opacity to the skeleton made a one-pixel
      // line vanish the moment the cell was made translucent -- which is
      // exactly when the centerline is most wanted.
      // Discs and the annotation wireframe likewise keep their own
      // color/opacity from the panel.
      // The shift layer's colors come from its ramp and its opacity from its
      // panel, and the points and lines layers from whoever draws them.
      // Only the surface takes the cell's color and opacity.
      if (map !== this.meshes) continue;
      object.traverse((child) => {
        if (!child.material) return;
        // The wireframe keeps its own color and opacity (a custom line color
        // must survive a cell recolor); app-level code repaints it when it is
        // set to follow the cell.
        if (child.userData.part === 'wireframe') return;
        // Remembered for setVertexColors(key, null), which gives the surface
        // back its flat color.
        if (color && child.userData.part === 'surface') child.userData.baseColor = color;
        if (color && !child.material.vertexColors) {
          child.material.color.set(color);
          // The glow follows the cell color; re-apply so it does.
          const spec = child.material.userData?.materialSpec;
          if (spec && child.userData.part === 'surface') {
            applyMaterialSpec(child.material, spec, { color });
          }
        }
        setOpacity(child.material, opacity);
        // A translucent cell must not write depth either, or its own far
        // side is hidden; glass never does.
        if (child.userData.part === 'surface' && child.material.userData?.materialSpec) {
          child.material.depthWrite = child.material.opacity >= 1
            && child.material.userData.materialSpec.transmission < 0.5;
        }
      });
    }
    if (recompute) {
      if (visible) this._hiddenCells.delete(key);
      else this._hiddenCells.add(key);
      // The selection highlight is drawn from the cell's layers, so it goes
      // and comes with them.
      this._syncHighlightVisibility(key);
      this._recomputeBounds();
    }
  }

  /** A highlight is shown while the cell it was drawn from is. */
  _syncHighlightVisibility(key) {
    const shown = !this._hiddenCells.has(key);
    for (const object of this.highlightGroup.children) {
      if (object.userData.key === key) object.visible = shown;
    }
  }

  /**
   * Synapse marker color, opacity and visibility.
   *
   * `type` restricts the change to one synapse type; omit it for every type on
   * the cell.  `color: null` restores that type's Viking color.
   */
  setSynapseStyle(key, { color, opacity, visible, type } = {}) {
    const group = this.synapses.get(key);
    if (!group) return;
    for (const child of group.children) {
      if (type !== undefined && child.userData.typeName !== type) continue;
      if (visible !== undefined) child.visible = visible;
      if (!child.material) continue;
      if (color !== undefined) {
        child.material.color.set(color || child.userData.typeColor || '#888888');
      }
      setOpacity(child.material, opacity);
    }
  }

  setAxesVisible(visible) { this.axes.visible = visible; }

  /**
   * Named viewpoints: each looks down one volume axis with a fixed up
   * vector, so two cells photographed from "XZ1" are comparable between
   * sessions -- which is the point of a named view over a hand-orbited one.
   *
   * XY1 / XY2 / XZ / YZ / 3D are RenderApp's buttons (`view(2)`,
   * `view(0, -90)`, `view(0, 0)`, `view(90, 0)`, `view(3)`): 1 is the axis
   * seen from its positive end, 2 the same face from behind, mirrored.
   * MATLAB had one XZ and one YZ; the far sides (XZ2, YZ2) were asked for on
   * 2026-09-18.  `dir` points from what is looked at to the camera.
   */
  static VIEWPOINTS = {
    xy1: { dir: [0, 0, 1], up: [0, 1, 0] },    // from +Z: X right, Y up
    xy2: { dir: [0, 0, -1], up: [0, 1, 0] },   // from -Z: X left, Y up
    // From -Z with Y down: X right, Y down, the way an image is indexed (the
    // Position shift panel's "Y down"; no toolbar button).
    xyv: { dir: [0, 0, -1], up: [0, -1, 0] },
    xz1: { dir: [0, -1, 0], up: [0, 0, 1] },   // from -Y: X right, Z up (MATLAB's XZ)
    xz2: { dir: [0, 1, 0], up: [0, 0, 1] },    // from +Y: X left, Z up
    yz1: { dir: [1, 0, 0], up: [0, 0, 1] },    // from +X: Y right, Z up (MATLAB's YZ)
    yz2: { dir: [-1, 0, 0], up: [0, 0, 1] },   // from -X: Y left, Z up
    // MATLAB's default view(-37.5, 30), the direction Reset view uses.
    '3d': { dir: null, up: [0, 0, 1] },
  };

  /** Names the buttons had before there were two of each. */
  static VIEWPOINT_ALIASES = { xz: 'xz1', yz: 'yz1' };

  /**
   * Turn the view to a named viewpoint.
   *
   * What is in view stays in view, at the zoom it had (asked for
   * 2026-09-18: "the only thing that should pull the viewport back is the
   * Reset view button"): the camera swings about the point on the view axis
   * at the depth of what the viewport shows (`_viewedDepth`), keeping its
   * distance to it, so whatever sat at the middle of the picture still does
   * and is the same size.  The buttons used to re-frame the whole scene,
   * which threw away a close-up every time the axis was changed.  For the
   * whole scene along an axis: Reset view, then the button.  A scene never
   * framed yet is fitted.
   */
  setViewpoint(name) {
    const key = String(name).toLowerCase();
    const spec = Renderer3D.VIEWPOINTS[Renderer3D.VIEWPOINT_ALIASES[key] ?? key];
    if (!spec || this.bounds.isEmpty()) return false;
    // A view cube turn under way would carry on over this one.
    this._settleFlight();
    const direction = spec.dir
      ? new THREE.Vector3().fromArray(spec.dir).normalize()
      : DEFAULT_VIEW_DIR.clone();
    this._placeView(direction, new THREE.Vector3().fromArray(spec.up), this._viewPivot());
    return true;
  }

  /**
   * Where a turn of the view pivots and how far the camera stays from it, for
   * setViewpoint and the view cube's turns (`turnTo`): `{ pivot, distance,
   * center, radius }`, the last two the scene's.
   */
  _viewPivot() {
    const center = this.bounds.getCenter(new THREE.Vector3());
    const radius = Math.max(this.bounds.getSize(new THREE.Vector3()).length() * 0.5, 1e-3);
    const cam = this.camera;
    const pivot = center.clone();
    let distance;
    const last = this._lastViewpoint;
    // "Nothing moved" to within a billionth of the scene: OrbitControls' update
    // moves the camera and its target by a float bit or two in the frames after
    // a view is placed (2e-14 um, measured on c943 after XY1, and a bit a frame
    // after XZ1), which an exact comparison took for a move.
    const still = (a, b) => a.distanceTo(b) <= 1e-9 * Math.max(1, radius);
    if (this.framed && last && last.camera === cam && last.zoom === cam.zoom
        && still(last.position, cam.position) && still(last.target, this.controls.target)) {
      // One viewpoint straight after another, nothing moved in between: turn
      // about the same point at the same distance.  Measuring again would
      // find a different column of content in each direction and creep
      // outward -- 33.7 to 43.4 um over the seven buttons on RPC1 c2628.
      pivot.copy(this.controls.target);
      distance = cam.position.distanceTo(pivot);
    } else if (this.framed) {
      distance = this._viewedDepth();
      pivot.copy(cam.position)
        .addScaledVector(cam.getWorldDirection(new THREE.Vector3()), distance);
    } else if (cam.isPerspectiveCamera) {
      const vFov = THREE.MathUtils.degToRad(cam.fov);
      const hFov = 2 * Math.atan(Math.tan(vFov / 2) * cam.aspect);
      distance = (1.3 * radius) / Math.tan(Math.min(vFov, hFov) / 2);
    } else {
      distance = radius * 3;
    }
    return { pivot, distance, center, radius };
  }

  /** Put the camera `distance` from `pivot` along `direction`, `up` up (see setViewpoint). */
  _placeView(direction, up, { pivot, distance, center, radius }) {
    const cam = this.camera;
    // A turn in progress would carry on about the new axis.
    this._spin.yaw = 0;
    this._spin.pitch = 0;
    cam.up.copy(up).normalize();
    cam.position.copy(pivot).addScaledVector(direction, distance);
    this.controls.target.copy(pivot);
    // Far enough for the whole scene from wherever the pivot is in it.
    this._setPlanes(cam, Math.max(distance / 2000, 1e-4),
      (distance + pivot.distanceTo(center)) * 8 + radius * 20);
    cam.updateProjectionMatrix();
    this._syncControlsUp();
    this._updateOrtho();
    this.controls.update();
    this.framed = true;
    this._lastViewpoint = {
      camera: cam, zoom: cam.zoom,
      position: cam.position.clone(), target: this.controls.target.clone(),
    };
  }

  /**
   * Turn the view to look along `dir` (from what is looked at toward the
   * camera) with `up` up, both in the scene's frame, gliding there as Fusion
   * 360's view cube does.  The view cube's way in (viewcube.js).
   *
   * It pivots as a viewpoint button does (setViewpoint): what is in view stays
   * in view, at its zoom.  The camera's orientation is slerped, so the pivot
   * stays in the middle of the picture throughout, and the glide is longer the
   * further it turns (0.22 s, up to 0.6 s for a half turn; none with reduced
   * motion asked for).  Resolves true once the view is there -- an undo step of
   * its own ('end') -- or false with nothing to look at.
   */
  turnTo(dir, up, { duration = null } = {}) {
    if (this.bounds.isEmpty()) return Promise.resolve(false);
    this._settleFlight();
    const cam = this.camera;
    const direction = new THREE.Vector3().fromArray(dir).normalize();
    const upward = new THREE.Vector3().fromArray(up).normalize();
    const view = this._viewPivot();
    const origin = new THREE.Vector3();
    const facing = (eye, top) => new THREE.Quaternion()
      .setFromRotationMatrix(new THREE.Matrix4().lookAt(eye, origin, top));
    // From where the camera is now about the pivot, which lies on its view
    // axis (or, for a scene never framed, is its center).
    const from = facing(cam.position.clone().sub(view.pivot), cam.up);
    const to = facing(direction, upward);
    const angle = 2 * Math.acos(Math.min(1, Math.abs(from.dot(to))));
    const still = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const ms = duration ?? (still ? 0 : 220 + (380 * angle) / Math.PI);

    this._spin.yaw = 0;
    this._spin.pitch = 0;
    this._spin.endPending = false;
    // As in flyTo: damping would go on easing the target while this writes it.
    const damping = this.controls.enableDamping;
    this.controls.enableDamping = false;
    this.controls.enabled = false;
    this._setPlanes(cam, Math.max(view.distance / 2000, 1e-4),
      (view.distance + view.pivot.distanceTo(view.center)) * 8 + view.radius * 20);
    cam.updateProjectionMatrix();
    const q = new THREE.Quaternion();
    const place = (t) => {
      q.slerpQuaternions(from, to, t);
      cam.position.set(0, 0, view.distance).applyQuaternion(q).add(view.pivot);
      cam.up.set(0, 1, 0).applyQuaternion(q);
      this.controls.target.copy(view.pivot);
      this._syncControlsUp();
      this.controls.update();
    };

    return new Promise((resolve) => {
      let late = null;
      const finish = () => {
        if (this._flight === null && this._endFlight === null) return;
        if (this._flight) cancelAnimationFrame(this._flight);
        this._flight = null;
        this._endFlight = null;
        clearTimeout(late);
        document.removeEventListener('visibilitychange', onHide);
        // Damping back on first: the arrival is placed by the update every later
        // frame makes, so the camera stays on it to the bit and a viewpoint button
        // pressed next turns about the same pivot (setViewpoint).
        this.controls.enableDamping = damping;
        this.controls.enabled = true;
        this._placeView(direction, upward, view);
        this.controls.dispatchEvent({ type: 'end' });
        resolve(true);
      };
      // No frames come in a hidden document (see flyTo): arrive at once.
      const onHide = () => { if (document.hidden) finish(); };
      this._endFlight = finish;
      if (document.hidden || ms <= 0) { finish(); return; }
      document.addEventListener('visibilitychange', onHide);
      // Nor in a page that is not hidden but not being drawn either (a pane out of
      // sight in an embedding app): the turn lands when it should have ended, and
      // the controls it disabled come back.
      late = setTimeout(finish, ms + 250);
      const start = performance.now();
      const step = (now) => {
        const raw = Math.min(1, (now - start) / ms);
        if (raw >= 1) { finish(); return; }
        place(raw * raw * (3 - 2 * raw));
        this._flight = requestAnimationFrame(step);
      };
      this._flight = requestAnimationFrame(step);
    });
  }

  /**
   * The camera's own axes in the scene's frame (Flip Z not taken back, unlike
   * viewAxes): unit vectors `right` and `up` along the screen and `back` out of
   * it toward the viewer, as arrays.  What the view cube turns with.
   */
  viewFrame() {
    const q = this.camera.quaternion;
    const axis = (x, y, z) => new THREE.Vector3(x, y, z).applyQuaternion(q).toArray();
    return { right: axis(1, 0, 0), up: axis(0, 1, 0), back: axis(0, 0, 1) };
  }

  /**
   * Turn the view by a drag that is not on the canvas (the view cube's), eased
   * as a plain drag is: `yaw` about the screen's vertical, `pitch` about its
   * horizontal, radians.  `endSpin` when the drag lets go, so the glide's end
   * is an undo step as a canvas drag's is.
   */
  spinBy(yaw, pitch) {
    if (!this.controls.enabled) return;
    this._spin.yaw += yaw;
    this._spin.pitch += pitch;
  }

  endSpin() { this._spin.endPending = true; }

  /**
   * How far in front of the camera the content of the viewport lies: the
   * median depth, along the view axis, of the surface vertices that project
   * into the middle 70 % of the picture.
   *
   * The orbit target is no guide -- it is an invisible point that need not
   * be on anything (see `_anchorDepth`).  Rays were tried first and dropped:
   * 15 to 35 ms each on RPC1 c2628 with its annotations drawn, so a time
   * budget decided how many were cast and two presses in a row disagreed by
   * a micron; and a ray reports the front face, where a quarter turn wants
   * the middle of the column of content being looked through, or the back
   * of it swings out of the picture.  Projecting a few thousand vertices of
   * each mesh is exact to repeat, takes about a millisecond, and sees the
   * whole column.  Surfaces are used when any is drawn, otherwise whatever
   * is (skeletons, annotation rings); hidden cells and Z-hidden stretches
   * are not content.  With nothing in view, the distance to the orbit
   * target stands.
   */
  _viewedDepth() {
    const cam = this.camera;
    cam.updateMatrixWorld();
    this.group.updateMatrixWorld(true);
    const ortho = cam.isOrthographicCamera;
    const fallback = Math.max(cam.position.distanceTo(this.controls.target), MIN_WORKING_UM);
    const world = new THREE.Vector3();
    const seen = new THREE.Vector3();
    const depthsOf = (surfacesOnly) => {
      const depths = [];
      this.group.traverse((object) => {
        const positions = object.geometry?.getAttribute?.('position');
        if (!positions || (surfacesOnly && object.userData.part !== 'surface')) return;
        for (let node = object; node; node = node.parent) {
          if (node.visible === false || node === this.highlightGroup) return;
        }
        const key = ownerKey(object);
        const hidden = key && this.zHide.get(key);
        const step = Math.max(1, Math.floor(positions.count / VIEW_DEPTH_VERTICES));
        for (let i = 0; i < positions.count; i += step) {
          world.fromBufferAttribute(positions, i).applyMatrix4(object.matrixWorld);
          if (hidden && !this._inZRange(key, world)) continue;
          seen.copy(world).applyMatrix4(cam.matrixWorldInverse);
          const depth = -seen.z;
          if (depth <= 0 && !ortho) continue;
          seen.applyMatrix4(cam.projectionMatrix);
          if (Math.abs(seen.x) <= VIEW_DEPTH_WINDOW && Math.abs(seen.y) <= VIEW_DEPTH_WINDOW) {
            depths.push(depth);
          }
        }
      });
      return depths;
    };
    let depths = depthsOf(true);
    if (!depths.length) depths = depthsOf(false);
    if (!depths.length) return fallback;
    depths.sort((a, b) => a - b);
    const median = depths[Math.floor(depths.length / 2)];
    return ortho ? median : Math.max(median, MIN_WORKING_UM);
  }

  /**
   * Change one cell's surface finish in place: a name from the material
   * menu, or a full spec while the editor previews an unsaved one.
   */
  setCellMaterial(key, finish) {
    const object = this.meshes.get(key);
    if (!object) return false;
    const spec = finish && typeof finish === 'object' ? finish : materialSpec(finish);
    object.traverse((child) => {
      if (!child.material || child.userData.part !== 'surface') return;
      applyMaterialSpec(child.material, spec, {
        color: `#${child.material.color.getHexString()}`,
      });
    });
    return true;
  }

  /**
   * Change the colormap of every depth-colored mesh already on screen.
   *
   * Recolors in place from the stored scalars, so switching maps or levels
   * costs a buffer write rather than a server round trip.
   */
  setColormap({ map, levels, invert } = {}) {
    if (map !== undefined) this.colormap.map = resolveColormap(map);
    if (levels !== undefined) this.colormap.levels = levels;
    if (invert !== undefined) this.colormap.invert = invert;
    for (const [key, values] of this._scalars) {
      const object = this.meshes.get(key);
      if (!object) { this._scalars.delete(key); continue; }
      const colors = depthColors(values, this.colormap);
      object.traverse((child) => {
        const attr = child.geometry?.getAttribute('color');
        if (!attr || attr.count * 3 !== colors.length) return;
        attr.copyArray(colors);
        attr.needsUpdate = true;
      });
    }
  }

  /** Light viewport (for figures) or the dark default. */
  setInverted(on) {
    this.inverted = !!on;
    this._applyBackground();
    this._recolorShifts();
    this.onInverted?.(this.inverted);
  }

  /** Mirror the scene through the XY plane, as ``set(gca, 'ZDir', 'reverse')``. */
  setFlipZ(on) {
    const flipped = this.flipZ !== !!on;
    this.flipZ = !!on;
    this.group.scale.z = on ? -1 : 1;
    // The context sheets and vasculature mirror with the cells they sit around.
    if (this.contextGroup) this.contextGroup.scale.z = this.group.scale.z;
    // A mirrored scale inverts every triangle's winding; two-sided materials
    // already draw both faces, but the lighting normals have to follow.
    this.group.updateMatrixWorld(true);
    for (const key of this.zHide.keys()) this._applyZHide(key);
    this._recomputeBounds();
    // Red is the marker drawn above the yellow one and blue the one below: a
    // mirrored Z puts each on the other side, so they trade colors.
    if (flipped && (this.markers.above || this.markers.below)) {
      const { above, below } = this.markers;
      this.markers = { above: below, below: above };
      this.slices = { primary: this.slices.primary, above: this.slices.below, below: this.slices.above };
      for (const slot of ['above', 'below']) {
        this._drawHighlight(this.markers[slot], slot);
        this._drawSlice(slot);
        this.onMarkerChange?.(slot, this.markers[slot]);
      }
    }
  }

  /**
   * Crop the scene to a box, as MATLAB's xlim/ylim/zlim.
   *
   * Six global clipping planes rather than a geometry cut: it is instant,
   * reversible, and applies to every object including the context sheets.
   * `null` clears the crop.
   */
  setClipBox(box) {
    if (!box) {
      this.clipBox = null;
      this.webgl.clippingPlanes = [];
      return;
    }
    const { min, max } = box;
    this.clipBox = box;
    this.webgl.clippingPlanes = [
      new THREE.Plane(new THREE.Vector3(1, 0, 0), -min[0]),
      new THREE.Plane(new THREE.Vector3(-1, 0, 0), max[0]),
      new THREE.Plane(new THREE.Vector3(0, 1, 0), -min[1]),
      new THREE.Plane(new THREE.Vector3(0, -1, 0), max[1]),
      new THREE.Plane(new THREE.Vector3(0, 0, 1), -min[2]),
      new THREE.Plane(new THREE.Vector3(0, 0, -1), max[2]),
    ];
  }

  /** The scene's own extent, for seeding the axis-limit boxes. */
  sceneBounds() {
    if (this.bounds.isEmpty()) return null;
    return { min: this.bounds.min.toArray(), max: this.bounds.max.toArray() };
  }

  /**
   * Context geometry: the fitted IPL boundary sheets and the vasculature.
   *
   * Kept in their own group so `_recomputeBounds` ignores them -- a boundary
   * sheet spans the whole volume, and framing the scene to it would shrink
   * every neuron to a speck (which is exactly what the MATLAB app did).
   */
  setContextMesh(key, mesh, { color = '#8f8fd0', opacity = 0.45, source = null } = {}) {
    this.removeContextMesh(key);
    if (!mesh || !mesh.n_faces) return null;
    if (!this.context) {
      this.context = new Map();
      this.contextGroup = new THREE.Group();
      this.contextGroup.scale.z = this.group.scale.z;
      this.scene.add(this.contextGroup);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.vertices, 3));
    geometry.setIndex(meshIndex(mesh.faces));
    geometry.computeVertexNormals();
    const object = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({
      color: new THREE.Color(color),
      transparent: opacity < 1,
      opacity,
      side: THREE.DoubleSide,
      shininess: 8,
      depthWrite: opacity >= 1,
    }));
    object.name = key;
    // Never steals a pick from the cell it is context for.
    object.raycast = () => {};
    this._place(object, source);
    this.contextGroup.add(object);
    this.context.set(key, object);
    this._reachDirty = true;
    return object;
  }

  removeContextMesh(key) {
    if (!this.context) return;
    const object = this.context.get(key);
    if (!object) return;
    this.contextGroup.remove(object);
    object.geometry?.dispose();
    object.material?.dispose();
    this.context.delete(key);
    this._reachDirty = true;
  }

  setContextStyle(key, { color, opacity, visible } = {}) {
    const object = this.context?.get(key);
    if (!object) return;
    if (visible !== undefined) object.visible = visible;
    if (color) object.material.color.set(color);
    setOpacity(object.material, opacity);
    if (opacity !== undefined) object.material.depthWrite = opacity >= 1;
  }

  /**
   * Microns per screen pixel at the orbit target, for the scale bar.
   *
   * Perspective scale depends on depth, so this is the scale in the plane
   * through the target -- the plane the user is looking at.
   */
  micronsPerPixel() {
    if (!this.container.clientHeight) return null;
    return this.umPerPixelAt(this.controls.target);
  }

  setOrthographic(useOrtho) {
    const target = this.controls.target.clone();
    const position = this.camera.position.clone();
    // Carry the horizon across the swap rather than slamming it back to Z:
    // a roll is a deliberate view state, and resetting it here silently
    // undid one. The orbit frame is re-synced either way -- setting `up`
    // without telling the controls leaves them orbiting the old pole.
    const up = this.camera.up.clone();
    this.camera = useOrtho ? this.orthographic : this.perspective;
    this.camera.up.copy(up);
    this.camera.position.copy(position);
    this.controls.object = this.camera;
    this.controls.target.copy(target);
    this._syncControlsUp();
    this._updateOrtho();
    this.controls.update();
  }

  _recomputeBounds() {
    this._reachDirty = true;
    // Only what is on screen: hiding every neuron but one should let "Reset
    // view" frame that one.
    this.bounds.makeEmpty();
    for (const map of [this.meshes, this.skeletons]) {
      for (const object of map.values()) {
        if (object.visible) this.bounds.expandByObject(object);
      }
    }
    if (this.bounds.isEmpty()) {
      for (const map of [this.meshes, this.skeletons]) {
        for (const object of map.values()) this.bounds.expandByObject(object);
      }
    }
    // No surface or skeleton at all: a scene of annotation rings, lines or
    // points alone (a Render compare pane showing only its annotations, a
    // load whose mesh has not arrived) frames on those, shown ones first.
    // Without this, Reset view and the viewpoint buttons did nothing for it.
    for (const shownOnly of [true, false]) {
      if (!this.bounds.isEmpty()) break;
      for (const map of [this.discs, this.annoWires, this.lines, this.points, this.graphs]) {
        for (const object of map.values()) {
          if (!shownOnly || object.visible) this.bounds.expandByObject(object);
        }
      }
    }
    if (this.bounds.isEmpty()) return;
    const size = this.bounds.getSize(new THREE.Vector3()).length();
    const scale = Math.max(size * 0.12, 1);
    // The indicator has to mirror with the scene: Flip Z reverses the
    // geometry through `group.scale.z`, and an unmirrored helper would go on
    // pointing +Z the old way -- an axis marker lying about which way the
    // volume's Z now runs.
    this.axes.scale.set(scale, scale, this.flipZ ? -scale : scale);
    const min = this.bounds.min;
    this.axes.position.set(min.x, min.y, min.z);
    this._updateOrtho({ keepScale: true });
  }

  /**
   * Whether the camera shows any part of a shown cell -- its surface, skeleton,
   * annotation rings, lines or points -- judged by their boxes against the view
   * frustum.  Null when the view has no size to judge by (a hidden tab).  A fresh
   * page's camera sits at the origin, nowhere near a Viking volume's cells; a load
   * into a list that already held a row did not frame them, and the cell was in the
   * scene, built and out of sight, until Reset view (Temporal c162392, 2026-10-21).
   */
  seesAnything() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return null;
    const cam = this.camera;
    cam.updateMatrixWorld();
    const frustum = new THREE.Frustum().setFromProjectionMatrix(
      new THREE.Matrix4().multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse));
    const box = new THREE.Box3();
    for (const map of [this.meshes, this.skeletons, this.discs, this.annoWires, this.lines, this.points,
      this.graphs]) {
      for (const object of map.values()) {
        // A layer the look does not show (GraphApp's, or the 3D render's) is not seen either.
        if (!object.visible || object.parent?.visible === false) continue;
        box.setFromObject(object);
        if (!box.isEmpty() && frustum.intersectsBox(box)) return true;
      }
    }
    return false;
  }

  /** A key the canvas would take, pressed with the focus elsewhere on the page (`m`). */
  handleKey(event) {
    this._key(event);
  }

  resetView() {
    // A view cube turn under way would carry on over the reset.
    this._settleFlight();
    if (this.bounds.isEmpty()) {
      // Nothing to frame, but the horizon can still be put back: pressing
      // Reset view with every cell hidden used to do nothing at all, which
      // left a rolled view rolled with no way out short of a reload.
      this.camera.up.copy(UP);
      this._syncControlsUp();
      this.controls.update();
      return;
    }
    const { clientWidth: w, clientHeight: h } = this.container;
    this.framed = Boolean(w && h);
    const center = this.bounds.getCenter(new THREE.Vector3());
    const radius = Math.max(this.bounds.getSize(new THREE.Vector3()).length() * 0.5, 1e-3);
    const cam = this.camera;
    let distance;
    if (cam.isPerspectiveCamera) {
      // Frame the bounding sphere on whichever field of view is tighter, so a
      // wide neuron is not cropped in a tall viewport (or the reverse).
      const vFov = THREE.MathUtils.degToRad(cam.fov);
      const hFov = 2 * Math.atan(Math.tan(vFov / 2) * cam.aspect);
      distance = (1.3 * radius) / Math.tan(Math.min(vFov, hFov) / 2);
    } else {
      distance = radius * 3;
    }
    const direction = DEFAULT_VIEW_DIR.clone();
    cam.position.copy(center).addScaledVector(direction, distance);
    this.controls.target.copy(center);
    // Put the horizon back too.  A roll or a single-axis pitch leaves `up`
    // tilted away from the volume's Z, and moving only the camera POSITION
    // left the scene lying at whatever angle it had been rolled to -- with
    // OrbitControls then orbiting about that tilted pole, so plain left-drag
    // no longer went the way the hand expected.  Reset means reset.
    this._spin.yaw = 0;
    this._spin.pitch = 0;
    cam.up.copy(UP);
    this._syncControlsUp();
    // And the orthographic zoom: a wheel zoom outlived Reset view there (10 steps in,
    // 0.034 um a pixel before and after it, where framing is 0.318; 2026-10-22).
    this.orthographic.zoom = 1;
    this._setPlanes(cam, Math.max(distance / 2000, 1e-4), distance * 8 + radius * 20);
    cam.updateProjectionMatrix();
    this._updateOrtho();
    this.controls.update();
  }

  snapshot(filename = 'sbfsem.png') {
    const link = document.createElement('a');
    link.download = filename;
    link.href = this.snapshotURL();
    link.click();
  }

  /**
   * The picture on screen as a data URL, drawn first so it is this frame.  With
   * `maxWidth`, a wider canvas is scaled down to it (a report wants a pane's look,
   * not its device pixels); `type` and `quality` are toDataURL's.
   */
  snapshotURL({ type = 'image/png', quality = undefined, maxWidth = 0 } = {}) {
    this.webgl.render(this.scene, this.camera);
    const canvas = this.webgl.domElement;
    if (!(maxWidth > 0) || canvas.width <= maxWidth || typeof document === 'undefined') {
      return canvas.toDataURL(type, quality);
    }
    const out = document.createElement('canvas');
    out.width = maxWidth;
    out.height = Math.max(1, Math.round(canvas.height * maxWidth / canvas.width));
    out.getContext('2d').drawImage(canvas, 0, 0, out.width, out.height);
    return out.toDataURL(type, quality);
  }

  // -- per-cell Z range -----------------------------------------------------
  // "Hide above" and "Hide below" an annotation (asked for 2026-09-18): show a
  // cell only between two Z levels.  Two clipping planes on the cell's own
  // materials -- nothing is rebuilt, no other cell is touched, and clearing
  // the range brings the whole render back.  The range is kept in the
  // volume's own microns, so the same annotations stay hidden through Flip Z
  // and a volume offset; the planes, which are in world space, are re-derived.

  /** Show `key` only between `min` and `max` (volume microns; null = open). */
  setZHide(key, { min = null, max = null } = {}) {
    const lo = Number.isFinite(min) ? min : null;
    const hi = Number.isFinite(max) ? max : null;
    if (lo === null && hi === null) this.zHide.delete(key);
    else this.zHide.set(key, { min: lo, max: hi });
    this._applyZHide(key);
  }

  _zPlanes(key) {
    const range = this.zHide.get(key);
    if (!range) return null;
    const source = this._sourceOf(key);
    const shift = (source && this.volumeOffsets.get(source)?.z) || 0;
    const f = this.flipZ ? -1 : 1;  // world z = f * (z + shift)
    const planes = [];
    if (range.max !== null) {
      planes.push(new THREE.Plane(new THREE.Vector3(0, 0, -f), range.max + Z_HIDE_SLACK_UM + shift));
    }
    if (range.min !== null) {
      planes.push(new THREE.Plane(new THREE.Vector3(0, 0, f), -(range.min - Z_HIDE_SLACK_UM + shift)));
    }
    return planes;
  }

  _applyZHide(key) {
    const planes = this._zPlanes(key);
    const clip = (object) => object?.traverse((child) => {
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) if (material) material.clippingPlanes = planes;
    });
    for (const map of this._cellLayers()) {
      clip(map.get(key));
    }
    // A highlighted wireframe or synapse set is the cell's geometry again and
    // is cut with it; a highlighted annotation or branch is the thing a range
    // is set from, and stays whole.
    for (const object of this.highlightGroup.children) {
      if (object.userData.key === key && object.userData.clipped) clip(object);
    }
  }

  /** Whether a world point on `key` lies inside the Z range shown for it. */
  _inZRange(key, worldPoint) {
    const range = key ? this.zHide.get(key) : null;
    if (!range) return true;
    const z = this.toVolume(worldPoint, this._sourceOf(key))[2];
    return (range.min === null || z >= range.min - Z_HIDE_SLACK_UM)
      && (range.max === null || z <= range.max + Z_HIDE_SLACK_UM);
  }

  /**
   * Whether a world point (a Vector3 or three numbers) is inside the crop box.
   * The crop is six global clipping planes, which the GPU honors and a ray does
   * not: a click on a pixel that showed nothing, because the crop had cut the cell
   * away there, still hit the cell and opened its menu (measured 2026-10-16: 8
   * clicks in 8).
   */
  _inCrop(worldPoint) {
    const planes = this.webgl.clippingPlanes;
    if (!planes?.length) return true;
    const p = Array.isArray(worldPoint)
      ? (this._cropPoint = this._cropPoint || new THREE.Vector3()).fromArray(worldPoint) : worldPoint;
    return planes.every((plane) => plane.distanceToPoint(p) >= -CROP_SLACK_UM);
  }

  /** Whether what is drawn at a world point of `key` is shown there: not cut away by its Z range or the crop. */
  _shown(key, worldPoint) {
    return this._inZRange(key, worldPoint) && this._inCrop(worldPoint);
  }

  /**
   * Point the picking ray through a screen position, and say how far behind the
   * camera it starts.  A perspective ray starts at the camera and the answer is 0.
   * An orthographic ray starts in the camera's PLANE, and an orbit or a pan carries
   * cells through that plane -- the clipping planes are symmetric about it, so what
   * is behind it is still drawn -- but whatever lies behind the start of a ray is
   * unreachable by it: 16 of 54 points on a cell could not be clicked once half of
   * it was behind the plane (2026-10-16).  So it starts clear of everything drawn,
   * and a hit's `distance - back` is its depth along the view, as it always was.
   */
  _aim(clientX, clientY) {
    const rect = this.webgl.domElement.getBoundingClientRect();
    this.pointer.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    if (!this.camera.isOrthographicCamera) return 0;
    const reach = this._reachSphere();
    const back = reach ? this.camera.position.distanceTo(reach.center) + reach.radius : 0;
    this.raycaster.ray.origin.addScaledVector(this.raycaster.ray.direction, -back);
    return back;
  }

  // -- GraphApp --------------------------------------------------------------
  // MATLAB SBFSEM-tools' GraphApp as a second look of the view (graphapp.js):
  // each cell's skeleton, a dot at every annotation, its rings, the unfinished,
  // terminal and off-edge annotations and its synapses, on black.  Each cell's
  // GraphApp objects are one group in `graphs`, under `graphLayer`; setLook
  // shows that layer or the 3D render's, so neither is rebuilt to switch.

  /** Show the 3D render ('render') or GraphApp ('graph'); the camera stays as it is. */
  setLook(look) {
    const graph = look === 'graph';
    if ((this.look === 'graph') === graph) return;
    this.look = graph ? 'graph' : 'render';
    this.renderLayer.visible = !graph;
    this.graphLayer.visible = graph;
    // The context sheets and vasculature are the 3D render's, a highlighted annotation's dot GraphApp's.
    if (this.contextGroup) this.contextGroup.visible = !graph;
    for (const object of this.highlightGroup.children) {
      for (const child of object.children) if (child.userData.graphOnly) child.visible = graph;
    }
    this._applyBackground();
    // A selection only one look draws (a synapse type's markers, a wireframe edge) goes.
    if (this.selection && ['synapse', 'wireframe'].includes(this.selection.kind)) this.clearSelection();
    this._reachDirty = true;
    this._recomputeBounds();
  }

  /** The background the look and the inversion ask for: GraphApp's is black. */
  _applyBackground() {
    const graph = this.look === 'graph';
    this.scene.background = new THREE.Color(graph ? 0x000000 : (this.inverted ? 0xf6f7f9 : 0x101418));
    // What is drawn over the view takes the dark style on GraphApp's black.
    this.container.classList.toggle('inverted', !!this.inverted && !graph);
    this.container.classList.toggle('graph-look', graph);
  }

  /** A round dot, for GraphApp's points (made once). */
  _dotTexture() {
    if (this._dot) return this._dot;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 64;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(32, 32, 30, 0, Math.PI * 2);
    ctx.fill();
    this._dot = new THREE.CanvasTexture(canvas);
    return this._dot;
  }

  /** Points at `xyz` (micron triples) in `colors` (one THREE.Color each), `px` CSS pixels across. */
  _dots(xyz, colors, px, { over = false } = {}) {
    const positions = new Float32Array(xyz.length * 3);
    const rgb = new Float32Array(xyz.length * 3);
    xyz.forEach((p, i) => {
      positions.set(p, i * 3);
      rgb.set([colors[i].r, colors[i].g, colors[i].b], i * 3);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(rgb, 3));
    const object = new THREE.Points(geometry, new THREE.PointsMaterial({
      size: px * this.webgl.getPixelRatio(), sizeAttenuation: false, vertexColors: true,
      map: this._dotTexture(), alphaTest: 0.5, depthTest: !over, depthWrite: !over,
    }));
    object.userData.px = px;
    object.raycast = () => {};
    // The flagged annotations over everything, as GraphApp's markers were.
    object.renderOrder = over ? 6 : 3;
    return object;
  }

  /** The skeleton as lines with a color per vertex, and which segment each vertex is of. */
  _graphSkeleton(skeleton, color) {
    const positions = [];
    const segOf = [];
    skeleton.segments.forEach((segment, index) => {
      const pts = segment.points_um || [];
      for (let i = 0; i < pts.length - 1; i += 1) {
        positions.push(...pts[i], ...pts[i + 1]);
        segOf.push(index, index);
      }
    });
    if (!positions.length) return null;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const rgb = new Float32Array(segOf.length * 3);
    for (let i = 0; i < segOf.length; i += 1) rgb.set([color.r, color.g, color.b], i * 3);
    geometry.setAttribute('color', new THREE.BufferAttribute(rgb, 3));
    const object = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ vertexColors: true }));
    object.userData.segOf = Uint32Array.from(segOf);
    object.raycast = () => {};
    return object;
  }

  /**
   * Draw a cell as GraphApp does.  `data`: `{ nodes, outlines, skeleton, unfinished,
   * synapses, synapseColors }` -- the cell's own annotations (`/nodes`), its traced
   * outlines, its skeleton (`/skeleton`), the Set of unfinished Location IDs
   * (graphapp.unfinishedIds), its synapses (`/synapse-points`) and a CSS color per
   * synapse type; `colors`: `{ annotation, skeleton }` (graphapp.graphColors).  The
   * flag colors and dot sizes are `look`'s (graphapp.js).  Drawn by the current
   * GraphApp settings (setGraphSettings), shown when GraphApp is and the cell is.
   */
  setGraphCell(key, data, colors, look) {
    this.removeGraphCell(key);
    const nodes = data.nodes || [];
    const root = new THREE.Group();
    root.name = key;
    root.userData.colors = colors;
    root.userData.look = look;
    const annotation = new THREE.Color(colors.annotation);
    if (data.skeleton?.segments?.length) {
      const skeleton = this._graphSkeleton(data.skeleton, new THREE.Color(colors.skeleton));
      if (skeleton) {
        skeleton.userData.part = 'skeleton';
        skeleton.userData.skeleton = data.skeleton;
        root.add(skeleton);
      }
    }
    // The rings as lines: as thin as anything drawn, and still a pixel on the screen (2026-10-22).
    const rings = this._ringMesh(key, nodes, {
      color: colors.annotation, opacity: 0.9, lines: true, outlines: data.outlines || [], through: false,
    });
    if (rings) {
      rings.userData.part = 'discs';
      root.add(rings);
    }
    if (nodes.length) {
      const dots = this._dots(nodes.map((n) => n.xyz_um), nodes.map(() => annotation), look.markerPx.medium);
      dots.userData.part = 'nodes';
      dots.userData.nodes = nodes;
      // Each annotation's segment, for "Color segments": the first that lists it.
      const segmentOf = new Map();
      (data.skeleton?.segments || []).forEach((segment, index) => {
        for (const id of segment.location_ids || []) if (!segmentOf.has(id)) segmentOf.set(id, index);
      });
      dots.userData.segmentOf = nodes.map((n) => segmentOf.get(n.id) ?? -1);
      root.add(dots);
    }
    const flags = [
      ['unfinished', (n) => data.unfinished?.has(n.id)],
      ['terminals', (n) => n.terminal],
      ['offEdges', (n) => n.off_edge],
    ];
    for (const [part, test] of flags) {
      const chosen = nodes.filter(test);
      if (!chosen.length) continue;
      const color = new THREE.Color(look.flagColors[part]);
      const dots = this._dots(chosen.map((n) => n.xyz_um), chosen.map(() => color), look.flagPx, { over: true });
      dots.userData.part = part;
      dots.userData.nodes = chosen;
      root.add(dots);
    }
    const synapses = data.synapses || [];
    if (synapses.length) {
      const dots = this._dots(synapses.map((x) => x.xyz_um),
        synapses.map((x) => new THREE.Color(data.synapseColors?.[x.type] || '#bbbbbb')), look.synapsePx);
      dots.userData.part = 'synapses';
      dots.userData.synapses = synapses;
      root.add(dots);
    }
    if (!root.children.length) return;
    this._place(root, this._sourceOf(key));
    root.visible = !this._hiddenCells.has(key);
    this.graphLayer.add(root);
    this.graphs.set(key, root);
    this._styleGraph(root);
    this._dotHighlights(key);
    this._reachDirty = true;
    this._applyZHide(key);
    this._refreshBoundsIfBare();
  }

  removeGraphCell(key) { this._dispose(this.graphs, key); }

  /**
   * GraphApp's controls (graphapp.normalizeGraphSettings) for every cell:
   * which parts show, the dot size, and "Color segments" with `segmentColor(t)`,
   * the colormap's sRGB triple at t (0 to 1), for each segment's place
   * (graphapp.segmentPositions, by `positions(count)`).
   */
  setGraphSettings(settings, { segmentColor = null, positions = null } = {}) {
    this.graphSettings = { settings, segmentColor, positions };
    for (const root of this.graphs.values()) this._styleGraph(root);
    // A highlighted annotation's dot is sized by the dots it stands out from.
    for (const object of this.highlightGroup.children) {
      const dot = object.children.find((c) => c.userData.graphOnly);
      if (!dot) continue;
      dot.userData.base = this._dotBase(object.userData.key, dot.userData.corePx);
      if (!object.userData.settle) this._sizeHighlightDot(dot, highlightLook(1, 'annotation'));
    }
  }

  _styleGraph(root) {
    const held = this.graphSettings;
    if (!held) return;
    const { settings, segmentColor, positions } = held;
    const look = root.userData.look;
    const dpr = this.webgl.getPixelRatio();
    const shows = { discs: settings.discs, synapses: settings.synapses, unfinished: settings.unfinished,
      terminals: settings.terminals, offEdges: settings.offEdges };
    const skeleton = root.children.find((c) => c.userData.part === 'skeleton');
    const segments = skeleton?.userData.skeleton.segments.length || 0;
    const colored = settings.colorSegments && segmentColor && positions && segments
      ? positions(segments).map((t) => new THREE.Color().setRGB(...segmentColor(t), THREE.SRGBColorSpace))
      : null;
    for (const part of root.children) {
      const name = part.userData.part;
      if (name in shows) part.visible = !!shows[name];
      if (name === 'nodes') {
        part.material.size = (look.markerPx[settings.markerSize] ?? look.markerPx.medium) * dpr;
        part.userData.px = part.material.size / dpr;
        const rgb = part.geometry.getAttribute('color');
        const base = new THREE.Color(root.userData.colors.annotation);
        part.userData.segmentOf.forEach((seg, i) => {
          const c = colored && seg >= 0 ? colored[seg] : base;
          rgb.setXYZ(i, c.r, c.g, c.b);
        });
        rgb.needsUpdate = true;
      }
      if (name === 'skeleton') {
        const rgb = part.geometry.getAttribute('color');
        const base = new THREE.Color(root.userData.colors.skeleton);
        part.userData.segOf.forEach((seg, i) => {
          const c = colored ? colored[seg] : base;
          rgb.setXYZ(i, c.r, c.g, c.b);
        });
        rgb.needsUpdate = true;
      }
    }
  }

  /** The rings a highlight is drawn on in the look shown. */
  _ringsOf(key) {
    if (this.look === 'graph') return this.graphs.get(key)?.children.find((c) => c.userData.part === 'discs');
    return this.discs.get(key);
  }

  /** The skeleton a highlight is drawn along in the look shown. */
  _skeletonOf(key) {
    if (this.look === 'graph') return this.graphs.get(key)?.children.find((c) => c.userData.part === 'skeleton');
    return this.skeletons.get(key);
  }

  /** `[key, part]` for every cell's GraphApp part named `name`. */
  * _graphParts(name) {
    for (const [key, root] of this.graphs) {
      const part = root.children.find((c) => c.userData.part === name);
      if (part) yield [key, part];
    }
  }

  /** The annotation GraphApp draws a dot at for Location `locationId` of `key`, or null. */
  _graphNode(key, locationId) {
    const dots = this.graphs.get(key)?.children.find((c) => c.userData.part === 'nodes');
    if (!dots || locationId == null) return null;
    if (!dots.userData.byId) dots.userData.byId = new Map(dots.userData.nodes.map((n) => [n.id, n]));
    return dots.userData.byId.get(locationId) ?? null;
  }

  /** A highlight dot's size at a look's core 1, CSS px: as wide as the ring's tube, twice a dot. */
  _dotBase(key, corePx) {
    const dots = this.graphs.get(key)?.children.find((c) => c.userData.part === 'nodes');
    return Math.max(2 * (corePx || 0), 2 * (dots?.userData.px || 0), DOT_HIGHLIGHT_MIN_PX);
  }

  /**
   * GraphApp's dot of a highlighted annotation, in the highlight's color `tint`: a
   * solid dot in a wider additive glow, as the ring is a tube in one, at
   * `look` (highlight.js `highlightLook`), which the settling then carries on.
   * `corePx`: the ring's tube radius on screen.  Only GraphApp shows it (`setLook`);
   * null when the cell has no GraphApp dot for the annotation.
   */
  _highlightDot(item, tint, corePx, look) {
    const node = this._graphNode(item.key, item.locationId);
    if (!node) return null;
    const group = new THREE.Group();
    group.userData.graphOnly = true;
    group.userData.corePx = corePx;
    group.userData.base = this._dotBase(item.key, corePx);
    group.visible = this.look === 'graph';
    for (const [role, order, blending] of [['core', 22, THREE.NormalBlending], ['glow', 20, THREE.AdditiveBlending]]) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(node.xyz_um, 3));
      const points = new THREE.Points(geometry, new THREE.PointsMaterial({
        color: tint, map: this._dotTexture(), size: 1, sizeAttenuation: false,
        transparent: true, depthTest: false, depthWrite: false, blending,
      }));
      points.userData.role = role;
      points.renderOrder = order;
      points.raycast = () => {};
      group.add(points);
    }
    this._sizeHighlightDot(group, look);
    return group;
  }

  /** A highlight dot at `look`: its core and glow as wide as the ring's, the glow as faint. */
  _sizeHighlightDot(group, look) {
    const dpr = this.webgl.getPixelRatio();
    for (const points of group.children) {
      const glow = points.userData.role === 'glow';
      points.material.size = group.userData.base * (glow ? look.glow : look.core) * dpr;
      if (glow) points.material.opacity = look.alpha;
    }
  }

  /** The highlights of `key` drawn before its GraphApp dots were take theirs, settled. */
  _dotHighlights(key) {
    for (const object of this.highlightGroup.children) {
      const item = object.userData.item;
      if (object.userData.key !== key || item?.kind !== 'annotation' || !object.userData.tint) continue;
      if (object.children.some((c) => c.userData.graphOnly)) continue;
      const dot = this._highlightDot(item, object.userData.tint, object.userData.corePx,
        highlightLook(1, 'annotation'));
      if (dot) object.add(dot);
    }
  }

  // -- selection ------------------------------------------------------------
  // A left triple click picks the item under the pointer (asked for
  // 2026-09-18).  The surface is never an item: it is what everything else is
  // drawn on, and a click on a cell would otherwise always mean the surface.
  // Annotations and skeletons are matched in SCREEN space -- within SELECT_PX
  // of the pointer -- because a ray does not meet a one-pixel line or a ring
  // seen edge-on, and those are exactly the views a Z level is chosen in.

  _layerOf(kind) {
    return {
      annotation: this.discs, skeleton: this.skeletons,
      wireframe: this.meshes, synapse: this.synapses,
    }[kind] || null;
  }

  /**
   * Everything selectable within SELECT_PX of a screen point, most obvious
   * first: nearest the pointer, then nearest the camera.  Each candidate is
   * `{ kind, key, px, depth, point, ... }` with `point` as [x, y, z] in the
   * volume's own frame; annotations add `locationId`, `section` and `zUm`, skeletons
   * `segment`, synapses `type`.
   *
   * With `context` (the right-click menu) it also answers for the cells themselves:
   * each cell's surface under the pointer (`kind: 'surface'`), or a few pixels from
   * it when no ray found one, and its annotation wireframe and position-shift
   * arrows (`'annowire'`, `'shifts'`).  None of those is something to select, so
   * the triple click never asks for them.
   */
  candidatesAt(clientX, clientY, { context = false } = {}) {
    const rect = this.webgl.domElement.getBoundingClientRect();
    const back = this._aim(clientX, clientY);
    this.group.updateMatrixWorld(true);
    const camera = this.camera;
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const eye = camera.position;
    const viewDir = camera.getWorldDirection(new THREE.Vector3());
    const v = new THREE.Vector3();
    // World point -> [x px, y px, depth along the view]; null behind the camera.
    const project = (world) => {
      const depth = v.copy(world).sub(eye).dot(viewDir);
      if (camera.isPerspectiveCamera && depth <= camera.near) return null;
      v.copy(world).project(camera);
      return [(v.x + 1) * 0.5 * rect.width, (1 - v.y) * 0.5 * rect.height, depth];
    };
    const shown = (object) => {
      for (let node = object; node; node = node.parent) if (node.visible === false) return false;
      return true;
    };
    const out = [];

    // Opaque surfaces hide what is behind them; the first face each visible
    // mesh shows the ray also tells whether its wireframe is under the pointer.
    let blockAt = Infinity;
    const missed = [];      // context: surfaces no ray went through, for the pass a few pixels around the pointer
    for (const [key, object] of this.meshes) {
      if (!shown(object)) continue;
      const solid = this._meshPart(key, 'surface');
      const wire = this._meshPart(key, 'wireframe');
      if (!solid || (!solid.visible && !wire?.visible)) continue;
      // Mesh.raycast does not look at `visible`, so a hidden surface still
      // answers for the wireframe that shares its geometry.
      const hit = this.raycaster.intersectObject(solid, false)
        .find((h) => this._shown(key, h.point));
      if (!hit) {
        if (context) missed.push({ key, solid });
        continue;
      }
      if (solid.visible && !solid.material.transparent && solid.material.opacity >= 0.999) {
        blockAt = Math.min(blockAt, hit.distance - back);
      }
      if (context && solid.visible) {
        out.push({
          kind: 'surface', key, px: 0, depth: hit.distance - back,
          point: this.toVolume(hit.point, this._sourceOf(key)),
        });
      }
      if (!wire?.visible || !hit.face) continue;
      const position = solid.geometry.getAttribute('position');
      const corners = [hit.face.a, hit.face.b, hit.face.c].map((i) => project(
        new THREE.Vector3().fromBufferAttribute(position, i).applyMatrix4(solid.matrixWorld)));
      if (corners.some((c) => !c)) continue;
      let best = Infinity;
      for (let i = 0; i < 3; i += 1) {
        best = Math.min(best, segmentDistance(px, py, corners[i], corners[(i + 1) % 3]).d);
      }
      if (best <= SELECT_PX) {
        out.push({
          kind: 'wireframe', key, px: best, depth: hit.distance - back,
          point: this.toVolume(hit.point, this._sourceOf(key)),
        });
      }
    }

    for (const [key, object] of this.synapses) {
      if (!shown(object)) continue;
      const hit = visibleHits(this.raycaster.intersectObject(object, true))
        .find((h) => this._shown(key, h.point));
      if (!hit) {
        // A marker is a few pixels across at the zoom of a whole cell.
        if (context) {
          for (const child of object.children) {
            if (!child.visible) continue;
            const near = this._nearTriangle(child, key, px, py, rect);
            if (!near) continue;
            out.push({
              kind: 'synapse', key, px: near.distance, depth: near.depth,
              type: child.userData.typeName, point: this.toVolume(near.world, this._sourceOf(key)),
            });
          }
        }
        continue;
      }
      out.push({
        kind: 'synapse', key, px: 0, depth: hit.distance - back, type: hit.object.userData.typeName,
        point: this.toVolume(hit.point, this._sourceOf(key)),
      });
    }

    const world = new THREE.Vector3();
    for (const [key, object] of [...this.skeletons, ...this._graphParts('skeleton')]) {
      const segments = object.userData.skeleton?.segments;
      if (!shown(object) || !segments) continue;
      const half = (object.userData.skeletonWidth || 0) / 2;
      let best = null;
      segments.forEach((segment, index) => {
        let prev = null;
        for (const p of segment.points_um || []) {
          const here = project(world.set(p[0], p[1], p[2]).applyMatrix4(object.matrixWorld));
          if (prev && here) {
            const { d, t } = segmentDistance(px, py, prev.s, here);
            // A tube is as wide on screen as its radius makes it there.
            const reach = SELECT_PX + (half ? half / this._umPerPixelAtDepth(here[2]) : 0);
            if (d <= reach && (!best || d < best.px)) {
              best = {
                px: d, segment: index, depth: prev.s[2] + (here[2] - prev.s[2]) * t,
                local: [0, 1, 2].map((k) => prev.p[k] + (p[k] - prev.p[k]) * t),
              };
            }
          }
          prev = here ? { s: here, p } : null;
        }
      });
      if (!best || !this._shown(key, this.toWorld(best.local, this._sourceOf(key)))) continue;
      out.push({
        kind: 'skeleton', key, px: best.px, depth: best.depth, segment: best.segment,
        point: best.local,
      });
    }

    for (const [key, object] of [...this.discs, ...this._graphParts('discs')]) {
      if (!shown(object)) continue;
      const through = object.material.depthTest === false;
      const range = this.zHide.get(key);
      const inRange = (z) => !range
        || ((range.min === null || z >= range.min - Z_HIDE_SLACK_UM)
          && (range.max === null || z <= range.max + Z_HIDE_SLACK_UM));
      const found = [];
      const outline = (points, z, info) => {
        let best = null;
        let prev = null;
        const visit = (p) => {
          const here = project(world.set(p[0], p[1], z).applyMatrix4(object.matrixWorld));
          if (prev && here) {
            const { d, t } = segmentDistance(px, py, prev, here);
            if (!best || d < best.px) best = { px: d, depth: prev[2] + (here[2] - prev[2]) * t };
          }
          prev = here;
        };
        points.forEach(visit);
        if (points.length) visit(points[0]);
        if (best && best.px <= SELECT_PX) found.push({ ...best, ...info, through });
      };
      // Skip what the pointer is nowhere near before walking its rim: a
      // traced Muller cell carries 1.6 million outline points.
      const near = (cx, cy, cz, reachUm) => {
        const center = project(world.set(cx, cy, cz).applyMatrix4(object.matrixWorld));
        if (!center) return false;
        const reach = reachUm / this._umPerPixelAtDepth(center[2]) + SELECT_PX;
        return Math.hypot(center[0] - px, center[1] - py) <= reach;
      };
      for (const node of object.userData.rings || []) {
        const [cx, cy, cz] = node.xyz_um;
        if (!inRange(cz) || !near(cx, cy, cz, node.radius_um)) continue;
        const rim = [];
        for (let k = 0; k < 32; k += 1) {
          const a = (k / 32) * Math.PI * 2;
          rim.push([cx + node.radius_um * Math.cos(a), cy + node.radius_um * Math.sin(a)]);
        }
        outline(rim, cz, { locationId: node.id, section: node.z_section, zUm: cz, center: [cx, cy, cz] });
      }
      for (const band of object.userData.bands || []) {
        if (!inRange(band.z) || !near(band.cx, band.cy, band.z, band.reach)) continue;
        outline(band.pts, band.z, {
          locationId: band.id, section: band.section, zUm: band.z,
          center: [band.cx, band.cy, band.z],
        });
      }
      // A traced outline and its cut-outs are one annotation.
      const byId = new Map();
      for (const f of found) {
        const held = byId.get(f.locationId);
        if (!held || f.px < held.px) byId.set(f.locationId, f);
      }
      for (const f of byId.values()) {
        // An annotation the crop has cut away is not there to be chosen.
        if (!this._inCrop(this.toWorld(f.center, this._sourceOf(key)))) continue;
        out.push({
          kind: 'annotation', key, px: f.px, depth: f.depth, through: f.through,
          locationId: f.locationId, section: f.section, zUm: f.zUm,
          point: f.center,
        });
      }
    }

    // GraphApp's dots: an annotation's (and a flagged one's) names the annotation, a
    // synapse's the synapse and the annotation it is drawn at.
    for (const [key, root] of this.graphs) {
      if (!shown(root)) continue;
      const source = this._sourceOf(key);
      for (const part of root.children) {
        const name = part.userData.part;
        const items = part.userData.synapses || part.userData.nodes;
        if (!items || !part.visible) continue;
        const reach = SELECT_PX + (part.userData.px || 0) / 2;
        for (const it of items) {
          const here = project(world.set(...it.xyz_um).applyMatrix4(part.matrixWorld));
          if (!here) continue;
          const d = Math.hypot(here[0] - px, here[1] - py);
          if (d > reach || !this._shown(key, this.toWorld(it.xyz_um, source))) continue;
          if (name === 'synapses') {
            out.push({
              kind: 'synapse', key, px: d, depth: here[2], through: true, type: it.type,
              synapseId: it.synapse_id, locationId: it.location_id, section: it.z_section,
              point: [...it.xyz_um], radiusUm: 0.35,
            });
          } else {
            out.push({
              kind: 'annotation', key, px: d, depth: here[2], through: part.material.depthTest === false,
              locationId: it.id, section: it.z_section, zUm: it.xyz_um[2], point: [...it.xyz_um],
              radiusUm: it.radius_um,
            });
          }
        }
      }
    }
    if (this.graphs.size) {
      // A ring, its dot and a flag on it are one annotation: the nearest stands for it.
      const best = new Map();
      for (const c of out) {
        if (c.kind !== 'annotation' && !(c.kind === 'synapse' && c.synapseId)) continue;
        const id = `${c.kind}|${c.key}|${c.locationId}`;
        const held = best.get(id);
        if (!held || c.px < held.px) best.set(id, c);
      }
      for (let i = out.length - 1; i >= 0; i -= 1) {
        const c = out[i];
        if (c.kind !== 'annotation' && !(c.kind === 'synapse' && c.synapseId)) continue;
        if (best.get(`${c.kind}|${c.key}|${c.locationId}`) !== c) out.splice(i, 1);
      }
    }

    if (context) this._cellCandidates(out, missed, { px, py, rect, shown });

    const slack = (d) => Math.max(0.05, d * 0.002);
    const seen = out.filter((c) => c.through === true || c.depth <= blockAt + slack(blockAt));
    seen.sort((a, b) => (Math.round(a.px) - Math.round(b.px)) || (a.depth - b.depth));
    // A stack of rings seen down Z can put dozens under one pixel.
    let annotations = 0;
    return seen.filter((c) => c.kind !== 'annotation' || (annotations += 1) <= 6);
  }

  _umPerPixelAtDepth(depth) {
    const h = this.container.clientHeight || 1;
    if (this.camera.isOrthographicCamera) {
      return (this.camera.top - this.camera.bottom) / this.camera.zoom / h;
    }
    return (2 * Math.max(depth, 1e-6)
      * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)) / h;
  }

  /**
   * What a right click at a screen point can be about, in the order the menu lists
   * it (contextmenu.js): the things `candidatesAt` finds, and each cell's surface,
   * annotation wireframe and position-shift arrows, the cell once.  Empty when
   * nothing drawn is under the pointer.
   */
  contextCandidatesAt(clientX, clientY) {
    return listCandidates(this.candidatesAt(clientX, clientY, { context: true }));
  }

  /**
   * The cell-level candidates of a right click (see `candidatesAt`): a surface a
   * few pixels from the pointer when no ray found one, and the annotation
   * wireframe and the position-shift arrows, which no ray ever meets (lines).
   * Appended to `out`.
   *
   * A ray goes through one point of the screen, and a thin process is one to
   * four pixels wide there: of 60 pixels c16137's Closed curve volume is drawn at, 37
   * found no cell.  So where no ray found a surface, the nearest triangle within
   * SURFACE_REACH_PX is looked for by projecting the mesh's own vertices
   * (pickgeom.js), one pass for the price of about one ray.  Where a ray did, the
   * neighbors are not listed: the pointer is on a cell, and a thin process that
   * passes close by is not what was pointed at.
   */
  _cellCandidates(out, missed, { px, py, rect, shown }) {
    this.camera.updateMatrixWorld();
    const exact = out.some((c) => c.kind === 'surface' || c.kind === 'wireframe');
    if (!exact) {
      for (const { key, solid } of missed) {
        const near = this._nearTriangle(solid, key, px, py, rect);
        if (!near) continue;
        out.push({
          kind: solid.visible ? 'surface' : 'wireframe', key, px: near.distance, depth: near.depth,
          point: this.toVolume(near.world, this._sourceOf(key)),
        });
      }
    }
    for (const [key, object] of this.annoWires) {
      if (!shown(object)) continue;
      const near = this._nearLines(object, key, px, py, rect, SELECT_PX);
      if (!near) continue;
      out.push({
        kind: 'annowire', key, px: near.distance, depth: near.depth,
        through: object.material.depthTest === false,
        point: this.toVolume(near.world, this._sourceOf(key)),
      });
    }
    for (const [key, group] of this.shifts) {
      if (!shown(group)) continue;
      let best = null;
      for (const child of group.children) {
        if (!child.visible || !['rings', 'arrows'].includes(child.userData.role)) continue;
        // Measured to the segments the wide lines are drawn from, over their window.
        const lines = child.userData.pick
          ? { geometry: child.userData.pick, matrixWorld: child.matrixWorld } : child;
        const reach = child.userData.role === 'arrows'
          ? arrowReach(child.userData.cssWidth?.(), SELECT_PX) : SELECT_PX;
        const near = this._nearLines(lines, key, px, py, rect, reach);
        if (near && (!best || near.distance < best.distance)) best = near;
      }
      if (!best) continue;
      out.push({
        kind: 'shifts', key, px: best.distance, depth: best.depth,
        through: group.userData.through !== false,
        point: this.toVolume(best.world, this._sourceOf(key)),
      });
    }
  }

  /** projection * view * model of an object, for pickgeom.js. */
  _pickMatrix(object) {
    return new THREE.Matrix4()
      .multiplyMatrices(this.camera.projectionMatrix, this.camera.matrixWorldInverse)
      .multiply(object.matrixWorld);
  }

  /**
   * Whether a geometry's bounding box, on the screen, comes within `reach` pixels
   * of a point: a cheap way out for a cell nowhere near it, before its vertices
   * are projected.  True when it cannot tell (a corner behind the camera).
   */
  _maybeNear(geometry, matrix, rect, px, py, reach) {
    if (!geometry.boundingBox) geometry.computeBoundingBox();
    const box = geometry.boundingBox;
    if (!box || box.isEmpty()) return false;
    const corners = [];
    for (const x of [box.min.x, box.max.x]) {
      for (const y of [box.min.y, box.max.y]) {
        for (const z of [box.min.z, box.max.z]) corners.push(x, y, z);
      }
    }
    const { sx, sy, sw } = projectPositions(corners, matrix.elements, rect.width, rect.height);
    if (Array.from(sw).some((w) => !(w > BEHIND))) return true;
    return px >= Math.min(...sx) - reach && px <= Math.max(...sx) + reach
      && py >= Math.min(...sy) - reach && py <= Math.max(...sy) + reach;
  }

  /**
   * The nearest triangle of a mesh to a pixel of the canvas, within
   * SURFACE_REACH_PX, as `{ distance, depth, world }` (`world` three numbers), or
   * null.  What the Z range or the crop has cut away is not there.
   */
  _nearTriangle(mesh, key, px, py, rect) {
    const geometry = mesh.geometry;
    const position = geometry.getAttribute('position');
    if (!position?.array || position.isInterleavedBufferAttribute || position.itemSize !== 3) return null;
    const matrix = this._pickMatrix(mesh);
    if (!this._maybeNear(geometry, matrix, rect, px, py, SURFACE_REACH_PX)) return null;
    const eye = this.camera.position;
    const dir = this.camera.getWorldDirection(new THREE.Vector3());
    const v = new THREE.Vector3();
    const toWorld = (local) => v.fromArray(local).applyMatrix4(mesh.matrixWorld);
    const hit = nearestTriangle(position.array, geometry.index ? geometry.index.array : null,
      matrix.elements, { width: rect.width, height: rect.height }, [px, py], SURFACE_REACH_PX, {
        accept: (local) => this._shown(key, toWorld(local)),
        depthOf: (local) => toWorld(local).sub(eye).dot(dir),
      });
    if (!hit) return null;
    return { distance: hit.distance, depth: hit.depth, world: toWorld(hit.local).toArray() };
  }

  /**
   * The same for a layer of line segments (a THREE.LineSegments): the nearest within
   * `reach` pixels, over the part of it that is drawn.
   */
  _nearLines(object, key, px, py, rect, reach) {
    const geometry = object.geometry;
    const position = geometry?.getAttribute('position');
    if (!position?.array || position.isInterleavedBufferAttribute || position.itemSize !== 3) return null;
    const matrix = this._pickMatrix(object);
    if (!this._maybeNear(geometry, matrix, rect, px, py, reach)) return null;
    const start = geometry.drawRange.start || 0;
    const count = geometry.drawRange.count;
    const eye = this.camera.position;
    const dir = this.camera.getWorldDirection(new THREE.Vector3());
    const v = new THREE.Vector3();
    const toWorld = (local) => v.fromArray(local).applyMatrix4(object.matrixWorld);
    const hit = nearestSegment(position.array, matrix.elements,
      { width: rect.width, height: rect.height }, [px, py], reach, {
        from: Math.floor(start / 2),
        to: Number.isFinite(count) ? Math.ceil((start + count) / 2) : Infinity,
        accept: (local) => this._shown(key, toWorld(local)),
        depthOf: (local) => toWorld(local).sub(eye).dot(dir),
      });
    if (!hit) return null;
    return { distance: hit.distance, depth: hit.depth, world: toWorld(hit.local).toArray() };
  }

  /** Make a candidate from `candidatesAt` the selection and highlight it.
   * A new selection lets go of the markers, unless told to keep them.  `slice`:
   * the yellow's z-slice (`setSlice`); without it the one it had stays while the
   * new selection is one of its annotations. */
  select(candidate, { keepMarkers = false, slice } = {}) {
    this._previewing = null;
    this.selection = candidate || null;
    // A nudge belongs to the annotation it was drawn for.
    this._nudges.delete('primary');
    if (!(keepMarkers && candidate)) this._nudges.clear();
    if (!(keepMarkers && candidate)) this.clearMarkers();
    this._takeSlice('primary', slice);
    this._drawHighlight(this.selection, 'primary', { pulse: true });
    this._drawSlice('primary', { pulse: true });
    this.onSelectionChange?.(this.selection);
  }

  clearSelection() {
    if (!this.selection && !this.markers.above && !this.markers.below
      && !this.highlightGroup.children.length) return;
    this.select(null);
  }

  /**
   * A marker beside the selection: `slot` is 'above' (red) or 'below' (blue),
   * `candidate` an annotation candidate (see `candidatesAt`) or null.  `pulse`:
   * drawn as just chosen, at full strength and then settling.  `slice` as for
   * `select`.
   */
  setMarker(slot, candidate, { pulse = true, slice } = {}) {
    if (slot !== 'above' && slot !== 'below') return;
    this._previewing = null;
    this.markers[slot] = candidate || null;
    this._takeSlice(slot, slice);
    this._nudges.delete(slot);
    this._drawHighlight(this.markers[slot], slot, { pulse });
    this._drawSlice(slot, { pulse });
    // A preview drawn over the yellow one is gone with it.
    if (this.selection && !this.highlightGroup.children.some((o) => o.userData.slot === 'primary'
      && !o.userData.extra)) {
      this._drawHighlight(this.selection);
    }
    this.onMarkerChange?.(slot, this.markers[slot]);
  }

  /** Both markers off; the selection stays. */
  clearMarkers() {
    for (const slot of ['above', 'below']) if (this.markers[slot]) this.setMarker(slot, null);
  }

  /**
   * The z-slice of a slot (2026-10-20): every annotation on the section of the
   * yellow, red or blue one, of its cell or of every loaded cell of its volume,
   * drawn in its color.  `slice` is `{ scope, key, source, section, items }`,
   * `items` annotation candidates (the slot's own among them, drawn as before);
   * null puts the slot back to its one annotation.  The app reads the
   * annotations and steps the slices (markers.js `sliceStep`).
   */
  setSlice(slot, slice) {
    if (!this.slotItem(slot)) return;
    this.slices[slot] = slice || null;
    this._drawSlice(slot, { pulse: true });
    this.onMarkerChange?.(slot, this.slotItem(slot));
  }

  /** The slot's z-slice after its annotation changed: `slice` when given, else the
   * one it had while the new annotation is one of it, else none. */
  _takeSlice(slot, slice) {
    const item = this.slotItem(slot);
    if (slice !== undefined) this.slices[slot] = item ? slice || null : null;
    else if (!item || !inSlice(this.slices[slot], item)) this.slices[slot] = null;
  }

  /** Draw the rest of a slot's z-slice, in the slot's color (its own annotation's). */
  _drawSlice(slot, { pulse = false } = {}) {
    this._clearHighlight(slot, { extra: true });
    const slice = this.slices[slot];
    const main = this.slotItem(slot);
    if (!slice || !main) return;
    const rest = slice.items.filter((c) => !sameAnnotation(c, main));
    // Hundreds of rings settling at once is a lot of vertices a frame: a big slice is drawn settled.
    const settle = pulse && rest.length <= SLICE_PULSE_MAX;
    for (const c of rest) this._drawHighlight(c, slot, { pulse: settle, extra: true, tintKey: main.key });
  }

  /** A cell went: its annotations leave every z-slice. */
  _dropSlicesOf(key) {
    for (const slot of SLOTS) {
      const slice = this.slices[slot];
      if (!slice?.items.some((c) => c.key === key)) continue;
      this.slices[slot] = { ...slice, items: slice.items.filter((c) => c.key !== key) };
      this._drawSlice(slot);
    }
  }

  /** Whatever is in `slot`: the selection for 'primary', else that marker. */
  slotItem(slot) {
    return slot === 'primary' ? this.selection : (this.markers[slot] ?? null);
  }

  /** Draw a chosen ring again as when it was chosen: full strength, then settling. */
  pulse(slot) {
    const item = this.slotItem(slot);
    if (item) this._drawHighlight(item, slot, { pulse: true });
    this._drawSlice(slot, { pulse: true });
  }

  /** The CSS color a slot is drawn in: yellow (or cyan), red (or orange), blue (or pink). */
  markerColor(slot) {
    return this._tint(slot, this.slotItem(slot)?.key ?? this.selection?.key).getStyle();
  }

  _dropMarkersOf(key) {
    for (const slot of ['above', 'below']) if (this.markers[slot]?.key === key) this.setMarker(slot, null);
  }

  /** Highlight a candidate without selecting it (a menu row under the mouse);
   * `null` puts the real highlights back.  `slot` is where the choice would
   * go: 'above' or 'below' draw it red or blue in place of that marker. */
  preview(candidate, slot = 'primary') {
    if (!candidate && !this._previewing) return;
    const was = this._previewing;
    this._previewing = candidate ? { candidate, slot } : null;
    if (was && was.slot !== slot) this._drawHighlight(this.slotItem(was.slot), was.slot);
    if (candidate) this._drawHighlight(candidate, slot);
    else if (was) this._drawHighlight(this.slotItem(was.slot), was.slot);
  }

  /** Take down the highlights of `slot` (every slot without one): its own ring and
   * the rest of its z-slice, or with `extra` true only the rest, false only its own. */
  _clearHighlight(slot = null, { extra = null } = {}) {
    for (const object of [...this.highlightGroup.children]) {
      if (slot && object.userData.slot !== slot) continue;
      if (extra !== null && !!object.userData.extra !== extra) continue;
      this._removeHighlight(object);
    }
  }

  _removeHighlight(object) {
    object.traverse((child) => {
      // A wireframe or synapse highlight borrows the cell's geometry.
      if (child.geometry && !object.userData.borrowed) child.geometry.dispose();
      child.material?.dispose();
    });
    this.highlightGroup.remove(object);
  }

  /**
   * `pulse`: the ring was just chosen, so it is drawn at full strength and then
   * settles to a calmer one (highlight.js); any other draw is the settled ring.
   */
  _drawHighlight(item, slot = 'primary', { pulse = false, extra = false, tintKey = null } = {}) {
    // The rest of a z-slice is drawn beside the slot's own ring, which leaves it standing.
    if (!extra) this._clearHighlight(slot, { extra: false });
    if (!item) return;
    const source = this._sourceOf(item.key);
    const tint = this._tint(slot, tintKey ?? item.key);
    const object = new THREE.Group();
    object.userData.key = item.key;
    object.userData.slot = slot;
    object.userData.item = item;
    object.userData.extra = extra;
    object.userData.tintKey = tintKey;
    const flat = (opacity, extra = {}) => new THREE.MeshBasicMaterial({
      color: tint, transparent: true, opacity, depthTest: false, depthWrite: false,
      side: THREE.DoubleSide, ...extra,
    });

    if (item.kind === 'wireframe') {
      const solid = this._meshPart(item.key, 'surface');
      if (!solid) return;
      // Same geometry, drawn after the cell's own edges: equal depth passes.
      const lines = new THREE.Mesh(solid.geometry,
        new THREE.MeshBasicMaterial({ color: tint, wireframe: true }));
      lines.renderOrder = 20;
      lines.raycast = () => {};
      object.add(lines);
      object.userData.borrowed = true;
      object.userData.clipped = true;
    } else if (item.kind === 'synapse' && !item.synapseId) {
      const group = this.synapses.get(item.key);
      const child = group?.children.find((c) => c.userData.typeName === item.type);
      if (!child) return;
      const glow = new THREE.Mesh(child.geometry, flat(0.6, { depthTest: true }));
      glow.material.polygonOffset = true;
      glow.material.polygonOffsetFactor = -2;
      glow.material.polygonOffsetUnits = -2;
      glow.renderOrder = 20;
      glow.raycast = () => {};
      object.add(glow);
      object.userData.borrowed = true;
      object.userData.clipped = true;
    } else {
      // A closed tube along the ring or outline, or an open one along the
      // branch: round, so it reads from every angle (a flat band vanishes
      // edge-on), a few pixels wide at the zoom it is drawn for, with a wider
      // translucent sleeve as the glow.
      const paths = this._highlightPaths(item);
      if (!paths.length) return;
      const umpp = this.umPerPixelAt(this.toWorld(item.point, source));
      const core = Math.max(2 * umpp, item.kind === 'skeleton'
        ? ((this._skeletonOf(item.key)?.userData.skeletonWidth || 0) / 2) * 1.3
        : (this._ringsOf(item.key)?.userData.stroke ?? 0.1) * 0.75);
      // A ring just chosen starts at full strength and settles; the rest are drawn settled.
      const settling = pulse && pulses(item.kind) && !prefersReducedMotion();
      const start = highlightLook(settling ? 0 : 1, item.kind);
      const parts = [];
      for (const { points, closed } of paths) {
        if (points.length < 2) continue;
        const curve = new THREE.CatmullRomCurve3(points, closed, 'centripetal');
        const steps = Math.min(Math.max(points.length * 2, 16), 2000);
        for (const [role, radius, material, order] of [
          ['core', core * start.core, flat(1), 21],
          // Dozens of rings side by side add up: a fainter sleeve each.
          ['glow', core * start.glow, flat(start.alpha, { blending: THREE.AdditiveBlending }), 20],
        ]) {
          const geometry = new THREE.TubeGeometry(curve, steps, radius, 8, closed);
          const tube = new THREE.Mesh(geometry, material);
          tube.renderOrder = order;
          tube.raycast = () => {};
          object.add(tube);
          if (settling) parts.push({ role, mesh: tube, ...tubeAnchors(geometry, curve, steps, 8, closed) });
        }
      }
      // GraphApp's dot of the annotation, in the same color and chosen and settling
      // with its ring (2026-10-22); shown while GraphApp is.
      const dot = item.kind === 'annotation' ? this._highlightDot(item, tint, core / umpp, start) : null;
      if (dot) object.add(dot);
      if (parts.length) {
        object.userData.settle = { t0: performance.now(), kind: item.kind, from: start, parts, dot };
      }
      object.userData.tint = tint;
      object.userData.corePx = core / umpp;
      object.userData.umpp = umpp;
    }
    this._place(object, source);
    // A manual move not yet applied draws the chosen ring where it is going.
    const nudge = extra ? null : this._nudges.get(slot);
    if (nudge) object.position.add(new THREE.Vector3(nudge[0], nudge[1], nudge[2]));
    object.visible = !this._hiddenCells.has(item.key);
    this.highlightGroup.add(object);
    if (object.userData.clipped) this._applyZHide(item.key);
  }

  /** The 3D paths a highlight follows, in the volume's frame. */
  _highlightPaths(item) {
    if (item.kind === 'nodes') {
      // A set of annotations and the links between them, handed over whole
      // (the skeleton check): a ring per annotation at its radius, a line per
      // link.  It needs no layer to be shown, so it outlives none.
      const ring = ({ xyz_um: [cx, cy, cz], radius_um: r }) => {
        const points = [];
        for (let k = 0; k < 32; k += 1) {
          const a = (k / 32) * Math.PI * 2;
          points.push(new THREE.Vector3(cx + r * Math.cos(a), cy + r * Math.sin(a), cz));
        }
        return { points, closed: true };
      };
      return [
        ...(item.nodes || []).filter((n) => n.radius_um > 0).map(ring),
        ...(item.links || []).map(([a, b]) => ({
          points: [new THREE.Vector3(...a), new THREE.Vector3(...b)], closed: false,
        })),
      ];
    }
    if (item.kind === 'skeleton') {
      const segment = this._skeletonOf(item.key)?.userData.skeleton?.segments?.[item.segment];
      return segment ? [{
        points: segment.points_um.map((p) => new THREE.Vector3(p[0], p[1], p[2])), closed: false,
      }] : [];
    }
    const circle = ([cx, cy, cz], r) => {
      const points = [];
      for (let k = 0; k < 48; k += 1) {
        const a = (k / 48) * Math.PI * 2;
        points.push(new THREE.Vector3(cx + r * Math.cos(a), cy + r * Math.sin(a), cz));
      }
      return [{ points, closed: true }];
    };
    const discs = item.kind === 'annotation' ? this._ringsOf(item.key) : null;
    const ring = (discs?.userData.rings || []).find((n) => n.id === item.locationId);
    if (ring) return circle(ring.xyz_um, ring.radius_um);
    const bands = (discs?.userData.bands || []).filter((b) => b.id === item.locationId).map((b) => ({
      points: b.pts.map((q) => new THREE.Vector3(q[0], q[1], b.z)), closed: true,
    }));
    if (bands.length) return bands;
    // An annotation of a z-slice on a cell whose rings are not drawn: a circle at its own radius.
    const r = item.radiusUm;
    return Array.isArray(item.point) && Number.isFinite(r) && r > 0 ? circle(item.point, r) : [];
  }

  /** Yellow, unless the cell is itself a yellow: then cyan. */
  _highlightColor(key) {
    return new THREE.Color(this._yellowish(key) ? HIGHLIGHT_ALT : HIGHLIGHT);
  }

  /**
   * A slot's color: the selection yellow (cyan on a yellow cell); the marker
   * above it red (orange on a red cell); the one below light blue (pink when the
   * yellow one had to be cyan, which light blue is too close to).
   */
  _tint(slot, key) {
    if (slot === 'above') return new THREE.Color(this._reddish(key) ? ABOVE_HIGHLIGHT_ALT : ABOVE_HIGHLIGHT);
    if (slot === 'below') {
      return new THREE.Color(this._yellowish(this.selection?.key ?? key) ? BELOW_HIGHLIGHT_ALT : BELOW_HIGHLIGHT);
    }
    return this._highlightColor(key);
  }

  _hueOf(key) {
    const solid = key ? this._meshPart(key, 'surface') : null;
    const hsl = { h: 0, s: 0, l: 0 };
    solid?.material?.color?.getHSL(hsl);
    return hsl;
  }

  _yellowish(key) {
    const hsl = this._hueOf(key);
    return hsl.s > 0.35 && hsl.h > 0.09 && hsl.h < 0.2;
  }

  _reddish(key) {
    const hsl = this._hueOf(key);
    return hsl.s > 0.35 && (hsl.h < 0.05 || hsl.h > 0.93);
  }

  /**
   * Settle the rings just chosen (highlight.js): move each tube's vertices in toward
   * its path, and fade its glow, without building the tube again.
   */
  _settleHighlights() {
    const now = performance.now();
    for (const object of this.highlightGroup.children) {
      const settle = object.userData.settle;
      if (!settle) continue;
      const progress = settleProgress(now - settle.t0);
      const look = highlightLook(progress, settle.kind);
      for (const part of settle.parts) {
        const scale = (part.role === 'core' ? look.core / settle.from.core : look.glow / settle.from.glow);
        const position = part.mesh.geometry.getAttribute('position');
        const out = position.array;
        for (let n = 0; n < out.length; n += 1) out[n] = part.center[n] + part.offset[n] * scale;
        position.needsUpdate = true;
        if (part.role === 'glow') part.mesh.material.opacity = look.alpha;
      }
      if (settle.dot) this._sizeHighlightDot(settle.dot, look);
      if (progress >= 1) delete object.userData.settle;
    }
  }

  /** Redraw a pixel-sized highlight once the zoom has changed its size. */
  _refreshHighlight() {
    this._settleHighlights();
    for (const object of [...this.highlightGroup.children]) {
      const item = object.userData.item;
      if (!object.userData.umpp || !item) continue;
      const now = this.umPerPixelAt(this.toWorld(item.point, this._sourceOf(item.key)));
      const ratio = now / object.userData.umpp;
      if (ratio > 1.4 || ratio < 0.7) {
        if (!object.userData.extra) this._drawHighlight(item, object.userData.slot);
        else {
          this._removeHighlight(object);
          this._drawHighlight(item, object.userData.slot, { extra: true, tintKey: object.userData.tintKey });
        }
      }
    }
  }

  // -- interaction --------------------------------------------------------
  /** What a ray through a screen point hits, nearest first, visible only. */
  _hitsAt(clientX, clientY) {
    this._aim(clientX, clientY);
    // Geometry a Z range or the crop hides is not there to be clicked, any more
    // than a hidden cell is.
    return visibleHits(this.raycaster.intersectObjects(this.group.children, true))
      .filter((hit) => this._shown(ownerKey(hit.object), hit.point));
  }

  /**
   * Scale zoom and pan by the distance to what the pointer is over.
   *
   * OrbitControls zooms by shrinking the distance to its orbit target 5 % a
   * notch and pans in proportion to the same distance, and the target is an
   * invisible point that need not be on anything.  Approaching a surface that
   * lies beyond it, the distance collapses -- measured on RPC1 c2628: 25 um
   * to 0.0000007 um over 360 notches, the step per notch falling from 1.3 um
   * to 0.00000004 um at a steady 60 frames a second -- and zoom, pan and
   * rotation all crawl until the user zooms back out ("the speed of the zoom
   * decreases more and more", 2026-09-18).  Nothing was slow; the ruler was
   * wrong.
   *
   * So the target is slid along the view axis to the depth of the surface
   * under the pointer before a wheel step or a pan starts.  The camera does
   * not move and the picture does not change; a notch is then 5 % of the way
   * to what is being pointed at, a pan keeps that surface under the pointer,
   * and the next rotation turns about it.  With nothing under the pointer the
   * distance is held, and it is never below MIN_WORKING_UM, so the wheel goes
   * on through a surface or empty space at a steady step instead of stalling.
   *
   * A ray test costs about 20 ms on a 600k-triangle cell, so wheel events
   * reuse the last depth until the pointer moves or a fifth of a second
   * passes (longer when the test itself is slow).  Orthographic zoom is a
   * magnification, which has no such failure, and is left alone.
   */
  _anchorDepth(event, { force = false } = {}) {
    if (!this.camera.isPerspectiveCamera || this._endFlight || !this.controls.enabled) return;
    const forward = this.camera.getWorldDirection(new THREE.Vector3());
    let depth = this.camera.position.distanceTo(this.controls.target);
    const a = this._anchor;
    const now = performance.now();
    const moved = Math.hypot(event.clientX - a.x, event.clientY - a.y) > 4;
    if (force || moved || now - a.at >= a.every) {
      const hits = this._hitsAt(event.clientX, event.clientY);
      a.every = Math.max(200, (performance.now() - now) * 6);
      a.at = now;
      a.x = event.clientX;
      a.y = event.clientY;
      const along = hits.length
        ? hits[0].point.clone().sub(this.camera.position).dot(forward) : 0;
      a.hit = along > 0;
      if (a.hit) depth = along;
    }
    // Zooming in over empty space: hold the distance the pointer left the
    // geometry at, so the wheel flies on at that step rather than shrinking
    // the ruler against nothing.  Zooming out grows it as it always did.
    if (a.hit || event.type !== 'wheel' || event.deltaY >= 0) a.hold = null;
    else depth = a.hold = a.hold ?? depth;
    depth = Math.max(depth, MIN_WORKING_UM);
    this.controls.target.copy(this.camera.position).addScaledVector(forward, depth);
  }

  /**
   * The near plane follows the working distance, so a surface can be
   * approached as closely as the wheel allows without being cut open.  It
   * used to be set once, when the scene was framed (0.05 to 0.3 um for a
   * whole cell), and everything nearer than that was clipped.
   *
   * The far plane follows the scene: it reaches past everything drawn, from
   * wherever the camera is.  It too was set only when the scene was framed,
   * so cells that arrived after the first one of a load were cut away as the
   * view turned, until the load ended and framed again (2026-10-09).  It
   * grows at once and shrinks only when far too deep, which keeps depth
   * precision without re-projecting every frame.  The camera never moves.
   */
  _syncPlanes() {
    const cam = this.camera;
    if (this._endFlight) return;
    if (cam.isOrthographicCamera) {
      // No perspective, so no near plane to speak of: the planes only have to hold the whole
      // scene, in front of the camera's own plane and behind it (an orbit or a pan carries
      // cells through that plane, and whatever is behind it is cut away -- the cells vanish
      // as if past an invisible boundary).  They stay symmetric about it.
      const reach = this._reachSphere();
      const span = (reach ? cam.position.distanceTo(reach.center) + reach.radius
        : cam.position.distanceTo(this.controls.target)) * 1.25;
      const far = Math.max(span, 1);
      if (far > cam.far || far < cam.far * 0.25 || cam.near !== -cam.far) {
        this._setPlanes(cam, 0, far);
        cam.updateProjectionMatrix();
      }
      return;
    }
    let changed = false;
    const near = Math.max(cam.position.distanceTo(this.controls.target) / 500, 1e-4);
    if (Math.abs(near - cam.near) > cam.near * 0.02) {
      cam.near = near;
      changed = true;
    }
    const reach = this._reachSphere();
    if (reach) {
      const far = Math.max((cam.position.distanceTo(reach.center) + reach.radius) * 1.25,
        near * 1000);
      if (far > cam.far || far < cam.far * 0.25) {
        cam.far = far;
        changed = true;
      }
    }
    if (changed) cam.updateProjectionMatrix();
  }

  /**
   * A camera's clipping planes.  A perspective camera has a near plane in front of it; an
   * orthographic one has no "behind", so its planes are the same distance either way and
   * a positive near plane never cuts the scene on the camera's side (the orthographic camera
   * is made with +-20000, and every view reset, restore and flight used to overwrite it with
   * a perspective camera's near plane).
   */
  _setPlanes(cam, near, far) {
    if (cam.isOrthographicCamera) {
      cam.near = -far;
      cam.far = far;
    } else {
      cam.near = near;
      cam.far = far;
    }
  }

  /** A sphere around everything drawn (cells, rings, markers, context), or null. */
  _reachSphere() {
    if (this._reachDirty) {
      this._reachDirty = false;
      const box = new THREE.Box3().expandByObject(this.group);
      if (this.contextGroup) box.expandByObject(this.contextGroup);
      if (box.isEmpty()) this._reach.makeEmpty();
      else box.getBoundingSphere(this._reach);
    }
    return this._reach.isEmpty() ? null : this._reach;
  }

  _pick(event) {
    this.webgl.domElement.focus();
    // With an annotation selected the readout names it: a right-click (the
    // menu that acts on it) or a right-drag pan must not move it elsewhere.
    if (event.button === 2 && this.selection?.kind === 'annotation') return;
    const hit = this.pickAt(event.clientX, event.clientY);
    this._pickMissed = !hit;
    if (!hit) return;
    this.pickedPoint = hit.world;
    this.pickedKey = hit.key;
    // An annotation ring under the pointer is named, so the app can tell a click on the
    // yellow or on a marker (what the arrow keys then act on).
    this.onPick?.({ point: this._pickInVolume(), key: this.pickedKey,
      locationId: hit.kind === 'annotation' || hit.synapse ? hit.locationId : null, synapse: hit.synapse });
  }

  /**
   * What a click at a screen point lands on, for the pick readout: the first
   * thing the right-click menu would list there (`pickOf`), as `{ key, kind,
   * world }` with `world` a THREE.Vector3, or null.
   *
   * It used to be the first thing one ray met, which missed a thin process the
   * pointer was plainly on (the ray went between its triangles: 44 of 60 drawn
   * pixels of Inferior c1178 found it) and let a line take a click from a micron
   * away in the world (three's line threshold), which is 14 to 25 pixels at close
   * zoom.  The menu's candidates measure on the screen instead: a surface within
   * SURFACE_REACH_PX (pickgeom.js), a line within SELECT_PX, and only what the
   * nearest opaque surface does not hide.  An annotation is picked at its center.
   */
  pickAt(clientX, clientY) {
    const top = pickOf(this.candidatesAt(clientX, clientY, { context: true }));
    if (!top?.point) return null;
    const world = new THREE.Vector3(...this.toWorld(top.point, this._sourceOf(top.key)));
    return {
      key: top.key, kind: top.kind, world, locationId: top.locationId ?? null,
      synapse: top.synapseId ? { id: top.synapseId, type: top.type, section: top.section } : null,
    };
  }

  _key(event) {
    const step = Math.PI / 36; // 5 degrees, as RenderApp's arrow keys used
    // Moving an annotation by hand takes Left and Right (and the axis keys) first.
    if (this.onMoveKey?.(event)) {
      event.preventDefault();
      return;
    }
    // With an annotation selected, Up and Down walk along Z instead of
    // orbiting: plain keys move a red or blue marker, shift moves the yellow.
    if ((event.key === 'ArrowUp' || event.key === 'ArrowDown')
        && this.selection?.kind === 'annotation' && this.onStepZ
        && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      this.onStepZ(event.key === 'ArrowUp' ? 1 : -1, { primary: event.shiftKey });
      return;
    }
    // Slice mode of the Position shift layer takes [ ] { } PageUp PageDown Home
    // End, and Up/Down while nothing is selected (the walk above won).  It moves
    // no camera and records no undo step: a held key must not flood the history.
    if (this.onSliceStep?.(event)) {
      event.preventDefault();
      return;
    }
    let handled = true;
    switch (event.key) {
      case 'ArrowLeft': this._orbit(-step, 0); break;
      case 'ArrowRight': this._orbit(step, 0); break;
      case 'ArrowUp': this._orbit(0, step); break;
      case 'ArrowDown': this._orbit(0, -step); break;
      case 'z': this._zoom(0.9); break;
      case 'Z': this._zoom(1.1); break;
      // Roll: rotation about the axis coming out of the screen.  Panning is
      // right-drag only; the old w/a/s/d/q/e pan keys were redundant with it.
      case 'q': this.roll(event.shiftKey ? Math.PI / 2 : step * 3); break;
      case 'e': this.roll(event.shiftKey ? -Math.PI / 2 : -step * 3); break;
      case 'Q': this.roll(Math.PI / 2); break;
      case 'E': this.roll(-Math.PI / 2); break;
      case 'm': this.resetView(); break;
      case 'c': this._copyPicked(); break;
      case 'f': this.onToggleFullscreen?.(); break;
      default: handled = false;
    }
    if (handled) {
      event.preventDefault();
      // Key-driven camera moves emit the same end signal as a drag, so the
      // undo history records them.  Copying and full screen move nothing.
      if (event.key !== 'c' && event.key !== 'f') this.controls.dispatchEvent({ type: 'end' });
    }
  }

  /**
   * Rotate the view about the axis pointing out of the screen.
   *
   * OrbitControls has no roll of its own: rotating the camera's up vector
   * about the current view direction is what tilts the horizon, and the
   * controls keep working relative to the new up afterwards.
   */
  roll(angle) {
    const dir = this.controls.target.clone().sub(this.camera.position);
    // A degenerate orbit (camera on its target) still has a facing direction.
    if (dir.lengthSq() < 1e-12) this.camera.getWorldDirection(dir);
    dir.normalize();
    this.camera.up.applyAxisAngle(dir, angle).normalize();
    this._syncControlsUp();
    this.controls.update();
  }

  /**
   * Tell OrbitControls the up vector changed.
   *
   * The controls capture their orbit frame from ``camera.up`` once, at
   * construction; after a roll or a single-axis orbit the stale frame makes
   * ``update()`` snap the camera back toward the old pole -- measured as a
   * ~2% drift per drag before this sync existed.
   */
  _syncControlsUp() {
    this.controls._quat.setFromUnitVectors(this.camera.up, new THREE.Vector3(0, 1, 0));
    this.controls._quatInverse.copy(this.controls._quat).invert();
  }

  /**
   * Rotate the camera about ONE viewport axis through the target.
   *
   * `'x'` is the screen-horizontal axis (a pure pitch: the object tips
   * toward or away from you), `'y'` the screen-vertical (a pure yaw).  Both
   * are defined by the current view -- after a roll they follow the rolled
   * screen, not the volume's own axes.  The up vector rotates with the
   * camera so consecutive drags stay consistent.
   */
  orbitViewAxis(which, angle) {
    this._turnAxis(which, angle);
    this._syncControlsUp();
    this.controls.update();
  }

  /**
   * One turn about a screen axis through the target, `up` turning with it.
   *
   * The axes are the screen's own -- right = forward x up, screen-up =
   * right x forward -- rather than raw `camera.up`, which is screen-up only
   * while the two are square.  `up` is re-squared on the way out: visually a
   * no-op (only its component across the view counts), but it keeps both axes
   * exact after a zoom-to-cursor nudges the view direction, and it is what
   * keeps OrbitControls from ever meeting a pole.  Measured against an `up`
   * that turns with the camera, the elevation it clamps never changes.
   */
  _turnAxis(which, angle) {
    const target = this.controls.target;
    const offset = this.camera.position.clone().sub(target);
    if (offset.lengthSq() < 1e-12) return;
    const forward = offset.clone().negate().normalize();
    const right = new THREE.Vector3().crossVectors(forward, this.camera.up);
    // Looking straight along `up` leaves no cross product; the rendered
    // camera still knows which way its screen's right is.
    if (right.lengthSq() < 1e-12) right.setFromMatrixColumn(this.camera.matrixWorld, 0);
    right.normalize();
    const screenUp = new THREE.Vector3().crossVectors(right, forward).normalize();
    const q = new THREE.Quaternion().setFromAxisAngle(which === 'x' ? right : screenUp, angle);
    offset.applyQuaternion(q);
    this.camera.up.copy(screenUp).applyQuaternion(q).normalize();
    this.camera.position.copy(target).add(offset);
  }

  /**
   * Play out a plain drag's queued turn, eased the way OrbitControls damps.
   *
   * Each frame applies a `dampingFactor` share of what is left, so the total
   * is exactly what the hand asked for and the tail is the familiar glide.
   * A flight or a modal drag disabling the controls cancels whatever is left.
   */
  _applySpin() {
    const s = this._spin;
    if (!s.yaw && !s.pitch) {
      if (s.endPending) {
        s.endPending = false;
        this.controls.dispatchEvent({ type: 'end' });
      }
      return;
    }
    if (!this.controls.enabled) {
      s.yaw = 0;
      s.pitch = 0;
      s.endPending = false;
      return;
    }
    const share = this.controls.enableDamping ? this.controls.dampingFactor : 1;
    let yaw = s.yaw * share;
    let pitch = s.pitch * share;
    s.yaw -= yaw;
    s.pitch -= pitch;
    if (Math.abs(s.yaw) < 1e-5) { yaw += s.yaw; s.yaw = 0; }
    if (Math.abs(s.pitch) < 1e-5) { pitch += s.pitch; s.pitch = 0; }
    if (yaw) this._turnAxis('y', yaw);
    if (pitch) this._turnAxis('x', pitch);
    this._syncControlsUp();
  }

  /**
   * Arrow-key azimuth and elevation, about the CAMERA's own frame.
   *
   * This used to build a spherical in a hardcoded world-Z-up frame, so once
   * a roll or a single-axis pitch had tilted `camera.up` the arrow keys and
   * a mouse drag orbited about two different poles -- the keys swinging the
   * scene one way and the mouse another, which reads as the view coming
   * loose from the volume. Both now go through `orbitViewAxis`.
   */
  _orbit(dTheta, dPhi) {
    if (dTheta) this.orbitViewAxis('y', -dTheta);
    if (dPhi) this.orbitViewAxis('x', -dPhi);
  }

  _zoom(factor) {
    // The same floor as the wheel (_anchorDepth): the z key never stalls.
    if (this.camera.isPerspectiveCamera
        && this.camera.position.distanceTo(this.controls.target) < MIN_WORKING_UM) {
      this.controls.target.copy(this.camera.position).addScaledVector(
        this.camera.getWorldDirection(new THREE.Vector3()), MIN_WORKING_UM);
    }
    const offset =this.camera.position.clone().sub(this.controls.target).multiplyScalar(factor);
    this.camera.position.copy(this.controls.target).add(offset);
    if (this.camera.isOrthographicCamera) {
      this.camera.zoom /= factor;
      this.camera.updateProjectionMatrix();
    }
    this.controls.update();
  }

  /**
   * Everything needed to put the camera back exactly where it was.
   *
   * Returns null before the camera has been aimed at anything.  A camera
   * sitting at the origin looking at the origin is the three.js default, not a
   * viewpoint, and saving it produces a session that restores to an empty
   * viewport with the meshes loaded just off screen.
   */
  cameraState() {
    if (this.camera.position.distanceTo(this.controls.target) < 1e-6) return null;
    return {
      position: this.camera.position.toArray(),
      target: this.controls.target.toArray(),
      up: this.camera.up.toArray(),
      zoom: this.camera.zoom,
      orthographic: this.camera.isOrthographicCamera === true,
    };
  }

  /**
   * Restore a saved viewpoint, refusing one that cannot see the scene.
   *
   * Returning false tells the caller to frame the scene instead.  Trusting a
   * bad camera is the worse failure: the neuron is loaded and visible in the
   * scene graph, so nothing reports an error -- the viewport is just empty.
   */
  restoreCamera(saved) {
    if (!saved || !Array.isArray(saved.position) || !Array.isArray(saved.target)) return false;
    if (!saved.position.every(Number.isFinite) || !saved.target.every(Number.isFinite)) return false;

    const position = new THREE.Vector3().fromArray(saved.position);
    const target = new THREE.Vector3().fromArray(saved.target);
    if (position.distanceTo(target) < 1e-6) return false;

    // A viewpoint from a session whose neurons are no longer loaded can be
    // arbitrarily far from what is on screen now.
    if (!this.bounds.isEmpty()) {
      const center = this.bounds.getCenter(new THREE.Vector3());
      const radius = Math.max(this.bounds.getSize(new THREE.Vector3()).length() * 0.5, 1e-3);
      if (target.distanceTo(center) > radius * 20) return false;
    }

    if (saved.orthographic !== undefined) this.setOrthographic(saved.orthographic);
    this.camera.position.copy(position);
    this.controls.target.copy(target);
    if (Array.isArray(saved.up) && saved.up.every(Number.isFinite)) {
      this.camera.up.fromArray(saved.up);
      this._syncControlsUp();
    }
    if (Number.isFinite(saved.zoom) && saved.zoom > 0) this.camera.zoom = saved.zoom;
    // Near/far were sized for whatever was framed last; re-derive them or a
    // restored close-up clips through the neuron.
    const distance = position.distanceTo(target);
    this._setPlanes(this.camera, Math.max(distance / 2000, 1e-4), distance * 8 + (this.bounds.isEmpty()
      ? distance : this.bounds.getSize(new THREE.Vector3()).length() * 20));
    this.camera.updateProjectionMatrix();
    this.controls.update();
    // Stops the next _resize from re-framing and throwing the restore away.
    this.framed = true;
    return true;
  }

  /**
   * Animate the camera to look closely at one point.
   *
   * Interpolates position and target together with a smoothstep ease, so the
   * move reads as a camera flight rather than a cut.  The approach direction is
   * chosen by casting against the loaded meshes and picking the least obstructed
   * of a spread of candidates, which is what stops the flight from ending inside
   * the soma or behind a dendrite.
   */
  flyTo(point, { duration = 1400, distance = null } = {}) {
    const target = new THREE.Vector3().fromArray(
      Array.isArray(point) ? point : [point.x, point.y, point.z],
    );
    const span = this.bounds.isEmpty()
      ? 10
      : this.bounds.getSize(new THREE.Vector3()).length();
    const range = distance ?? Math.max(span * 0.06, 2.5);

    const fromPos = this.camera.position.clone();
    const fromTarget = this.controls.target.clone();
    const toPos = target.clone().add(this._clearApproach(target, range));

    this._cancelFlight();

    // Damping fights an animated camera: it keeps applying its own easing to
    // controls.target while we are writing to it.
    const damping = this.controls.enableDamping;
    this.controls.enableDamping = false;
    this.controls.enabled = false;

    return new Promise((resolve) => {
      const place = (t) => {
        this.camera.position.lerpVectors(fromPos, toPos, t);
        this.controls.target.lerpVectors(fromTarget, target, t);
        this._setPlanes(this.camera, Math.max(range / 500, 1e-4), range * 200 + span * 4);
        this.camera.updateProjectionMatrix();
        this.controls.update();
      };

      const finish = () => {
        if (this._flight === null && this._endFlight === null) return;
        if (this._flight) cancelAnimationFrame(this._flight);
        this._flight = null;
        this._endFlight = null;
        document.removeEventListener('visibilitychange', onHide);
        place(1);
        this.controls.enableDamping = damping;
        this.controls.enabled = true;
        this.framed = true;
        resolve();
      };
      // requestAnimationFrame does not fire in a hidden document, so an
      // animation started (or interrupted) while the tab is in the background
      // would never finish -- leaving the promise pending and, worse, the
      // controls disabled for good. Jump straight to the destination instead.
      const onHide = () => { if (document.hidden) finish(); };

      this._endFlight = finish;
      if (document.hidden) { finish(); return; }
      document.addEventListener('visibilitychange', onHide);

      const start = performance.now();
      const step = (now) => {
        const raw = Math.min(1, (now - start) / duration);
        // smoothstep: zero velocity at both ends.
        place(raw * raw * (3 - 2 * raw));
        if (raw < 1) {
          this._flight = requestAnimationFrame(step);
          return;
        }
        finish();
      };
      this._flight = requestAnimationFrame(step);
    });
  }

  /** Settle a flight if one is under way; with none, touch nothing (not even the controls). */
  _settleFlight() {
    if (this._endFlight || this._flight) this._cancelFlight();
  }

  /** Settle any flight in progress, so a new one starts from a clean state. */
  _cancelFlight() {
    if (this._endFlight) this._endFlight();
    if (this._flight) {
      cancelAnimationFrame(this._flight);
      this._flight = null;
    }
    this.controls.enabled = true;
  }

  /**
   * Offset from `target` to a viewpoint with the clearest line of sight.
   *
   * Keeps the current viewing direction when nothing is in the way, so a
   * short hop does not spin the scene for no reason.
   */
  _clearApproach(target, range) {
    const current = this.camera.position.clone().sub(this.controls.target);
    if (current.lengthSq() < 1e-9) current.copy(DEFAULT_VIEW_DIR);
    current.normalize();

    const meshes = [...this.meshes.values()].filter((m) => m.visible);
    if (!meshes.length) return current.multiplyScalar(range);

    const candidates = [current];
    // A spread of alternatives: rotations of the current direction about Z,
    // each tilted, plus the two poles.
    for (const turn of [0.6, -0.6, 1.2, -1.2, 2.0, -2.0, Math.PI]) {
      for (const tilt of [0, 0.5, -0.5]) {
        const v = current.clone().applyAxisAngle(UP, turn);
        const axis = new THREE.Vector3().crossVectors(v, UP).normalize();
        if (axis.lengthSq() > 0.5) v.applyAxisAngle(axis, tilt);
        candidates.push(v.normalize());
      }
    }
    candidates.push(UP.clone(), UP.clone().negate());

    let best = current;
    let bestHits = Infinity;
    for (const direction of candidates) {
      const from = target.clone().addScaledVector(direction, range);
      this.raycaster.set(from, direction.clone().negate());
      this.raycaster.far = range * 0.98;
      const hits = this.raycaster.intersectObjects(meshes, true).length;
      if (hits < bestHits) {
        bestHits = hits;
        best = direction;
        if (hits === 0) break;
      }
    }
    // The raycaster is shared with picking; leaving a few-micron far plane
    // behind would silently kill every later click.
    this.raycaster.far = Infinity;
    return best.clone().multiplyScalar(range);
  }

  _copyPicked() {
    // The app owns the clipboard text: it knows the downsample and the
    // nearest annotation.  `copied` asks it to copy what its readout shows.
    this.onPick?.({ point: this._pickInVolume(), key: this.pickedKey, copied: true });
  }

  /** Microns per screen pixel at a point -- the view's zoom there. */
  umPerPixelAt(point) {
    const h = this.container.clientHeight || 1;
    if (this.camera.isOrthographicCamera) {
      return (this.camera.top - this.camera.bottom) / this.camera.zoom / h;
    }
    const p = new THREE.Vector3().fromArray(
      Array.isArray(point) ? point : [point.x, point.y, point.z]);
    const d = this.camera.position.distanceTo(p);
    return (2 * d * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)) / h;
  }

  /** The perspective camera distance that shows `umpp` microns per screen pixel. */
  distanceForUmPerPixel(umpp) {
    const h = this.container.clientHeight || 1;
    const cam = this.camera.isOrthographicCamera ? this.perspective : this.camera;
    return (umpp * h) / (2 * Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2));
  }

  /** In orthographic view, the zoom that shows `umpp` microns per screen pixel. */
  setOrthoUmPerPixel(umpp) {
    if (!this.camera.isOrthographicCamera || !(umpp > 0)) return;
    const h = this.container.clientHeight || 1;
    this.camera.zoom = (this.camera.top - this.camera.bottom) / (umpp * h);
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }
}

/**
 * Rings drawn through the surface ignore the depth buffer and render last.
 *
 * "Last" is only true inside three.js's transparent pass: `renderOrder` sorts
 * within a pass, and every opaque material is drawn before every translucent
 * one.  At opacity 1.00 `setOpacity` made the rings opaque, so they were drawn
 * first and each translucent surface in front was then blended over them:
 * black rings on RPC1 c2628's 0.20 crimson surface read rgb(5, 2, 3) at 0.95
 * and rgb(89, 25, 48) at 1.00 (reported 2026-09-18; rgb(3, 1, 2) now).
 * Through-rings stay in the transparent pass at any opacity.  Nothing else
 * has the jump: the skeleton, the synapses, the wireframe and rings with
 * "Through the surface" off all test depth, so a surface in front tints
 * them the same either side of 1.00 (skeleton: 90, 113, 52 and 90, 118, 51).
 */
function applyDiscDepth(object, through) {
  const material = object.material;
  const transparent = through || material.opacity < 1;
  if (material.depthTest === through || material.transparent !== transparent) {
    material.needsUpdate = true;
  }
  material.depthTest = !through;
  material.depthWrite = !through;
  material.transparent = transparent;
  // The children are the rest of a layer too large for one draw call.
  object.traverse((child) => { child.renderOrder = through ? 3 : 0; });
}

/**
 * Lines a set number of screen pixels wide, from fatlines.js's buffers: one Mesh
 * of two triangles a segment (and a filled head an arrow), spread on the screen
 * by the vertex shader.  `role` 'arrows' gets heads and a fainter tail; 'rings'
 * are plain segments, thinner.
 *
 * The width is in CSS pixels (`userData.setWidth`); the drawing buffer's size,
 * its pixel ratio and the material's opacity reach the shader each time it is
 * drawn, so a resize, a move to a screen of another density and the opacity
 * setters need nothing else.  `position` holds each corner's own end of its
 * segment, so the bounds three.js culls by are the segments'.  `clipping` takes
 * the crop box and the cell's Z range, as a LineBasicMaterial would.
 */
function wideLines(buffers, role) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(buffers.position, 3));
  geometry.setAttribute('other', new THREE.BufferAttribute(buffers.other, 3));
  geometry.setAttribute('corner', new THREE.BufferAttribute(buffers.corner, 3));
  geometry.setAttribute('lineColor', new THREE.BufferAttribute(
    buffers.color ?? new Float32Array(buffers.position.length).fill(1), 3));
  // Each corner's own alpha: 1, but for the marks One slice at a time fades (setShiftSliceTint).
  geometry.setAttribute('lineAlpha', new THREE.BufferAttribute(
    new Float32Array(buffers.position.length / 3).fill(1), 1));
  const arrows = role === 'arrows';
  const uniforms = {
    resolution: { value: new THREE.Vector2(1, 1) },
    lineWidth: { value: 1 },
    headLength: { value: 0 },
    headWidth: { value: 0 },
    opacity: { value: 1 },
    tailAlpha: { value: arrows ? TAIL_ALPHA : 1 },
  };
  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: FAT_VERTEX_SHADER,
    fragmentShader: FAT_FRAGMENT_SHADER,
    clipping: true,
    transparent: true,
    // The triangles' winding follows the line's direction on the screen.
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geometry, material);
  let css = WIDTH_DEFAULT;
  mesh.userData.setWidth = (width) => { css = width; };
  mesh.userData.cssWidth = () => css;
  const viewport = new THREE.Vector4();
  mesh.onBeforeRender = (renderer) => {
    renderer.getCurrentViewport(viewport);
    uniforms.resolution.value.set(Math.max(1, viewport.z), Math.max(1, viewport.w));
    const ratio = renderer.getPixelRatio();
    uniforms.lineWidth.value = (arrows ? css : ringWidth(css)) * ratio;
    if (arrows) {
      const head = headSize(css);
      uniforms.headLength.value = head.length * ratio;
      uniforms.headWidth.value = head.width * ratio;
    }
    uniforms.opacity.value = material.opacity;
    material.uniformsNeedUpdate = true;
  };
  return mesh;
}

/** Distance in pixels from (px, py) to the screen segment a-b, and where
 * along it (0 at a, 1 at b) the nearest point lies. */
function segmentDistance(px, py, a, b) {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  const t = len2 > 0 ? Math.min(1, Math.max(0, ((px - a[0]) * dx + (py - a[1]) * dy) / len2)) : 0;
  return { d: Math.hypot(px - (a[0] + dx * t), py - (a[1] + dy * t)), t };
}

function ownerKey(object) {
  let node = object;
  while (node) {
    if (node.name) return node.name;
    node = node.parent;
  }
  return null;
}

/**
 * Free a scene node's GPU objects: its geometry, its materials, and the
 * textures those hold (`Material.dispose()` frees the programs, not the
 * textures).  A Sprite's quad is one geometry three.js shares among every
 * sprite on the page, so it is left to three.
 */
function releaseNode(node) {
  if (node.geometry && !node.isSprite) node.geometry.dispose();
  for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
    if (!material) continue;
    for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
    material.dispose();
  }
}

