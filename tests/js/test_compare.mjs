// CompareView (static/js/compare.js) against a fake DOM, a fake Renderer3D and
// a scripted server.  No JS runner exists in this repo; run it with
//
//     node tests/js/test_compare.mjs
//
// It copies compare.js, comparelayers.js, compareoptions.js, api.js, jobcard.js, loadprogress.js, the
// Position shift modules, the render options (renderoptions.js, render-schema.js,
// renderform.js: the real form, built into the fake DOM) and the real state.js into a
// temp directory with render3d.js swapped for a stub (the real one needs three.js and a
// browser), runs the cases and exits non-zero if any failed.  Dependency free; poll
// timing is shrunk so it runs in a second or two, and every wait is for a condition,
// not for a time.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

// Two objects are compared by identity, and a failure says so in a line.  Node's own failure message for
// `assert.equal` diffs the two values, and here they are a pane, a layer or an element with its whole tree:
// cyclic and huge, so a test that failed on such a comparison used to hang the run (and grow it to gigabytes)
// instead of reporting.
const isObject = (value) => (typeof value === 'object' && value !== null) || typeof value === 'function';
const strictEqual = assert.equal;
const strictNotEqual = assert.notEqual;
assert.equal = (actual, expected, message) => {
  if (!isObject(actual) && !isObject(expected)) return strictEqual(actual, expected, message);
  return assert.ok(actual === expected, message ?? `expected the same object (${typeof actual} and ${typeof expected}; not diffed: they are large)`);
};
assert.notEqual = (actual, expected, message) => {
  if (!isObject(actual) && !isObject(expected)) return strictNotEqual(actual, expected, message);
  return assert.ok(actual !== expected, message ?? 'expected two different objects');
};

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');

// -- a fake DOM, just enough for compare.js and jobcard.js --------------------
class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attrs = new Map();
    this.listeners = new Map();
    this.hidden = false;
    this.className = '';
    this.value = '';
    this.checked = false;
    this._text = '';
    this.parentElement = null;
    const owner = this;
    this.classList = {
      _set() { return new Set(owner.className.split(/\s+/).filter(Boolean)); },
      add(c) { const s = this._set(); s.add(c); owner.className = [...s].join(' '); },
      remove(c) { const s = this._set(); s.delete(c); owner.className = [...s].join(' '); },
      toggle(c, on) {
        const s = this._set();
        if (on === undefined ? !s.has(c) : on) s.add(c); else s.delete(c);
        owner.className = [...s].join(' ');
      },
      contains(c) { return this._set().has(c); },
    };
  }

  get textContent() {
    return this.children.length ? this.children.map((c) => c.textContent).join('') : this._text;
  }

  set textContent(value) { this.children = []; this._text = String(value); }

  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === 'string' ? Object.assign(new El('#text'), { _text: node }) : node;
      if (child instanceof El) child.parentElement = this;
      this.children.push(child);
    }
  }

  /** As the page: a node already in the tree is moved, not copied. */
  insertBefore(node, ref) {
    if (node instanceof El) {
      node.remove();
      node.parentElement = this;
    }
    const at = ref ? this.children.indexOf(ref) : -1;
    if (at < 0) this.children.push(node); else this.children.splice(at, 0, node);
  }

  get nextSibling() {
    const siblings = this.parentElement ? this.parentElement.children : [];
    const at = siblings.indexOf(this);
    return at >= 0 ? siblings[at + 1] ?? null : null;
  }

  /** A <select>'s add(): the option goes at `index` (default: the end). */
  add(option, index = null) {
    option.parentElement = this;
    if (index === null || index === undefined) this.children.push(option);
    else this.children.splice(index, 0, option);
  }

  /** Enough of closest() for `[data-key]`, `.class`, `#id` and a tag name. */
  closest(sel) {
    const test = (node) => {
      const attr = /^\[data-([\w-]+)\]$/.exec(sel);
      if (attr) return node.dataset[attr[1]] !== undefined;
      if (sel.startsWith('.')) return node.className.split(/\s+/).includes(sel.slice(1));
      if (sel.startsWith('#')) return node.id === sel.slice(1);
      return node.tagName === sel;
    };
    for (let node = this; node; node = node.parentElement) if (test(node)) return node;
    return null;
  }

  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  prepend(...nodes) {
    const kept = this.children;
    this.children = [];
    this.append(...nodes);
    this.children.push(...kept);
  }
  remove() {
    if (this.parentElement) {
      this.parentElement.children = this.parentElement.children.filter((c) => c !== this);
    }
  }

  setAttribute(k, v) { this.attrs.set(k, v); }
  removeAttribute(k) { this.attrs.delete(k); }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }

  removeEventListener() {}
  fire(type, event = {}) { for (const fn of this.listeners.get(type) || []) fn(event); }
  click() { this.fire('click'); }
  get options() { return this.children.filter((c) => c.tagName === 'option'); }
  querySelector(sel) {
    const want = sel.replace(/^[.#]/, '');
    const tag = /^[a-z]+$/.test(sel);
    const walk = (node) => {
      for (const child of node.children) {
        if (tag ? child.tagName === sel : child.className.split(/\s+/).includes(want) || child.id === want) return child;
        const hit = walk(child);
        if (hit) return hit;
      }
      return null;
    };
    return walk(this);
  }

  contains(node) {
    return node === this || this.children.some((c) => c.contains && c.contains(node));
  }

  focus() { globalThis.document.activeElement = this; }

  find(pred, out = []) {
    for (const child of this.children) { if (pred(child)) out.push(child); child.find(pred, out); }
    return out;
  }
}
globalThis.document = {
  activeElement: null,
  createElement: (tag) => new El(tag),
  createTextNode: (text) => Object.assign(new El('#text'), { _text: text }),
};
globalThis.Option = class extends El {
  constructor(text, value) { super('option'); this._text = text; this.value = String(value); this.disabled = false; }
};
const timers = { setInterval, clearInterval };
void timers;

// -- stubs for the browser-only modules ---------------------------------------
const stubRenderer = `
export class Renderer3D {
  constructor(container) {
    this.container = container; this.calls = []; this.framed = 0;
    this.meshes = new Map(); this.shifts = new Map(); this.flipZ = false; this.onSliceStep = null;
    this.paused = false; this.order = new Map(); this.zhide = new Map(); this.styles = new Map();
    // What the real renderer remembers per key: whether the surface and the wireframe are
    // on, and the points of an error map (new objects, visible until a setStyle hides them).
    this.surfaces = new Map(); this.wires = new Map(); this.points = new Map();
    // The controls tell the view about a camera change, whoever made it (a drag, or a framing); a pane
    // framed while it was out of sight (paused here, a container with no size there) has not been framed
    // against a real size, and frames itself the first time it has one (updateSize).
    this.changeHandlers = []; this.realFramed = false;
    this.bounds = { isEmpty: () => !this.meshes.size };
    const noop = () => {};
    this.controls = {
      addEventListener: (type, fn) => { if (type === 'change') this.changeHandlers.push(fn); },
      update: noop, target: { copy: noop }, dispose: noop,
    };
    this.camera = { position: { copy: noop }, up: { copy: noop }, zoom: 1, near: 1, far: 2, updateProjectionMatrix: noop };
    globalThis.__renderers = (globalThis.__renderers || []).concat(this);
  }
  setMesh(key, mesh, opts) {
    this.calls.push(['setMesh', mesh && mesh.mesh_id, opts && !!opts.vertexColors, key]);
    if (mesh && mesh.n_faces) this.meshes.set(key, mesh); else this.meshes.delete(key);
    // A new object: its surface is on and its wireframe off until the caller says otherwise.
    this.surfaces.set(key, true); this.wires.set(key, false);
  }
  removeAll(key) {
    this.calls.push(['removeAll', key]); this.meshes.delete(key); this.zhide.delete(key); this.points.delete(key);
  }
  removePoints(key) { this.points.delete(key); }
  setPoints(key) { this.calls.push(['setPoints', key]); this.points.set(key, { visible: true }); }
  removeDiscs(key) { this.calls.push(['removeDiscs', key]); }
  setDiscs(key) { this.calls.push(['setDiscs', !!this.disposed, key]); }
  setSurface(key, on) { this.calls.push(['setSurface', key, !!on]); this.surfaces.set(key, !!on); }
  setWireframe(key, on) { this.calls.push(['setWireframe', key, !!on]); this.wires.set(key, !!on); }
  setWireframeStyle() {}
  setStyle(key, opts) {
    this.calls.push(['setStyle', key, opts]);
    this.styles.set(key, { ...(this.styles.get(key) || {}), ...opts });
    // As the real one: visibility reaches every object that holds the key, the points included.
    if (opts.visible !== undefined && this.points.has(key)) this.points.get(key).visible = !!opts.visible;
  }
  setLayerOrder(key, index) { this.calls.push(['setLayerOrder', key, index]); if (index === null) this.order.delete(key); else this.order.set(key, index); return true; }
  setZHide(key, range) { this.calls.push(['setZHide', key, range]); this.zhide.set(key, range); }
  setPaused(on) { this.calls.push(['setPaused', !!on]); this.paused = !!on; }
  setShifts(key, layer, style) {
    this.calls.push(['setShifts', key, layer.arrows ? layer.arrows.positions.length / 18 : 0, style && style.opacity]);
    this.shifts.set(key, layer);
  }
  removeShifts(key) { this.calls.push(['removeShifts', key]); this.shifts.delete(key); }
  setShiftWindow(key, range) {
    this.calls.push(['setShiftWindow', key, range && range.arrows ? [range.arrows.first, range.arrows.count] : null]);
  }
  setShiftPlane(key, z) { this.calls.push(['setShiftPlane', key, z]); }
  cameraState() { return {}; } restoreCamera() {}
  resetView() { this.clearPivot('reset'); this.framed += 1; this.realFramed = !this.paused; }
  // The pivot point, as the real one keeps it: set, let go (saying why) or waiting for a click, the app told
  // through onPivotChange; P sets one where the pointer is (onPivotRequest), and what pivotAt reads.
  pivotPoint() { return this._pivot ? [...this._pivot] : null; }
  get pivotArmed() { return !!this._armed; }
  setPivot(point, opts) {
    this.calls.push(['setPivot', point, opts ?? null]);
    this._pivot = [...point]; this._armed = false;
    this.onPivotChange?.({ on: true, armed: false, reason: null });
    return true;
  }
  clearPivot(reason = 'off') {
    const was = !!this._pivot || !!this._armed;
    this._pivot = null; this._armed = false;
    if (was) this.onPivotChange?.({ on: false, armed: false, reason });
    return was;
  }
  armPivot() {
    if (this._pivot) this.clearPivot('off');
    this._armed = true;
    this.onPivotChange?.({ on: false, armed: true, reason: null });
    return true;
  }
  togglePivot() {
    if (this._pivot || this._armed) return this.clearPivot('off');
    if (this.pointerInView && this.onPivotRequest) { this.onPivotRequest(this.pointerAt[0], this.pointerAt[1]); return true; }
    return this.armPivot();
  }
  get pointerInView() { return !!this.pointerAt; }
  pickAt() { return this.under ? { key: this.under.key, world: { toArray: () => [...this.under.point] } } : null; }
  nearestAnnotation(x, y, cells) {
    this.calls.push(['nearestAnnotation', cells.map((c) => [c.key, c.nodes.length])]);
    const c = this.readout && cells.find((cell) => cell.key === this.readout.key);
    return c ? { key: c.key, node: c.nodes[0], px: 0, hit: true } : null;
  }
  toWorld(p) { return [...p]; }
  umPerPixelAt() { return 0.01; }
  // The view cube's way in: what it reads and the turns it asks for.
  viewFrame() { return { right: [1, 0, 0], up: [0, 1, 0], back: [0, 0, 1] }; }
  turnTo(dir, up) { this.calls.push(['turnTo', dir, up]); return Promise.resolve(!!this.meshes.size); }
  spinBy(yaw, pitch) { this.calls.push(['spinBy', yaw, pitch]); }
  endSpin() { this.calls.push(['endSpin']); }
  updateSize() {
    if (this.paused || this.realFramed || !this.meshes.size) return;
    this.resetView();
    for (const fn of this.changeHandlers) fn();
  }
  dispose() { this.disposed = true; }
}`;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sbfsem-compare-test-'));
for (const name of ['compare.js', 'comparelayers.js', 'api.js', 'jobcard.js', 'loadprogress.js', 'state.js',
  'shifts.js', 'shiftlayer.js', 'shiftpanel.js', 'shiftlegend.js', 'fatlines.js', 'renderoptions.js',
  'render-schema.js', 'compareoptions.js', 'renderform.js', 'renderestimate.js', 'regstatus.js',
  'renderconflicts.js', 'renderfallback.js', 'conflictpanel.js', 'viewcube.js', 'settings.js', 'registrations.js',
  'pivot.js']) {
  let text = fs.readFileSync(path.join(jsDir, name), 'utf8');
  text = text.replace("'./render3d.js'", "'./render3d_stub.js'");
  fs.writeFileSync(path.join(tmp, name), text);
}
fs.writeFileSync(path.join(tmp, 'render3d_stub.js'), stubRenderer);
const mod = (name) => import(pathToFileURL(path.join(tmp, name)).href);
const { CompareView, MESH_OPTION_KEYS } = await mod('compare.js');
const ro = await mod('renderoptions.js');
const co = await mod('compareoptions.js');
const { JobCard } = await mod('jobcard.js');
const { POLL_TIMING } = await mod('api.js');
const { state, DEFAULT_RENDER } = await mod('state.js');
const { registrationTag: registrationTagOf } = await mod('registrations.js');
Object.assign(POLL_TIMING, { first: 1, every: 3, giveUp: 60000, requestTimeout: 0 });
/** Which load of each cell the (real) state module holds; 0 when unloaded. */
const setLoaded = (key, loadGen) => state.neurons.set(key, { key, loadGen });
const unload = (key) => state.neurons.delete(key);

// -- a scripted server ----------------------------------------------------------
let server;
function resetServer(options = {}) {
  server = {
    log: [],               // [method, path, body]
    jobs: new Map(),
    n: 0,
    polls: options.polls ?? 1,          // running polls before done
    failMeasure: options.failMeasure ?? null,
    failBuild: options.failBuild ?? null,
    failDiff: options.failDiff ?? false,
    // An id built from the options that define a surface, whether they arrive
    // as query strings (mesh jobs) or JSON values (measurement bodies).
    meshId: options.meshId ?? ((o) => `m:${['method', 'pathway', 'meshlab', 'smooth_iterations']
      .map((k) => String(o[k] ?? '')).join('|')}`),
    holdBuilds: options.holdBuilds ?? false,
    deviationMeshId: options.deviationMeshId ?? null,
    failNodes: options.failNodes ?? false,
    nodes: options.nodes ?? [],
    nodesGate: options.nodesGate ?? null,   // a promise the /nodes answer waits for
    nodesHits: 0,
    deviationScale: options.deviationScale ?? 0.5,
    deviationIds: options.deviationIds ?? [],  // ids to answer the next measurements with
    emptyMesh: options.emptyMesh ?? null,      // (query) => true: the build comes back with no faces
    open: 0,               // jobs started and not yet answered done, failed or cancelled
    maxOpen: 0,            // the most there ever were at once
    // A copy of the cell under another registration (POST /api/neurons): what was asked, and
    // whether the correction it asks for is applied.
    copyLoads: [],
    failCorrection: options.failCorrection ?? false,
  };
}
const reply = (body, status = 200) => ({
  ok: status < 300, status, statusText: '', json: async () => body,
});
globalThis.fetch = async (url, init = {}) => {
  const method = init.method || 'GET';
  const [pathname, query] = String(url).split('?');
  const body = init.body ? JSON.parse(init.body) : null;
  const search = Object.fromEntries(new URLSearchParams(query || ''));
  server.log.push([method, pathname, body || search, search]);
  // Like the real server: a cell that is not loaded has no jobs to start.
  if ((method === 'GET' && /\/mesh-job$/.test(pathname)
      || method === 'POST' && /-job$/.test(pathname)) && !state.neurons.has('i:1')) {
    return reply({ detail: 'c1 is not loaded' }, 404);
  }
  const newJob = (kind, work) => {
    const id = `j${++server.n}`;
    server.jobs.set(id, { id, kind, work, seen: 0, finished: false });
    server.open += 1;
    server.maxOpen = Math.max(server.maxOpen, server.open);
    return reply({ job_id: id, status: 'queued', kind, weight_mb: 1 });
  };
  if (method === 'GET' && /\/mesh-job$/.test(pathname)) {
    const opts = Object.fromEntries(new URLSearchParams(query || ''));
    return newJob('mesh', () => {
      if (server.failBuild?.(opts)) return { status: 'error', detail: 'out of memory', failed_stage: 'isotropic remesh' };
      if (server.emptyMesh?.(opts)) return { status: 'done', mesh: { name: 'm', n_vertices: 0, n_faces: 0 } };
      return { status: 'done', mesh: { name: 'm', n_vertices: 3, n_faces: 1, vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        faces: [0, 1, 2], vertex_data: null, mesh_id: server.meshId(opts) } };
    });
  }
  if (method === 'POST' && /mesh-deviation-job$/.test(pathname)) {
    return newJob('deviation', () => {
      if (server.failMeasure) return { status: 'error', detail: 'worker died', failed_stage: 'vertex distances' };
      const id = server.deviationIds.length ? server.deviationIds.shift() : null;
      return { status: 'done', result: {
        mesh_id: id ?? server.deviationMeshId ?? server.meshId(body), n_vertices: 3, vertex_um: [0.1, 0.2, 0.3],
        rim_xyz: [0, 0, 0], rim_um: [0.1], rim_stride: 1, n_rims: 1, scale_um: server.deviationScale,
        deviation: { mean_um: 0.1, surface_um: 0.1, annotation_um: 0.1, p95_um: 0.2, max_um: 0.3,
          excess_volume_um3: 1, sample_spacing_um: 0.05, n_discs: 3, n_curves: 0 } } };
    });
  }
  if (method === 'POST' && /mesh-diff-job$/.test(pathname)) {
    return newJob('diff', () => server.failDiff ? { status: 'error', detail: 'diff died', failed_stage: 'distances' } : ({ status: 'done', result: {
      mesh_id: server.meshId(body.a), n_vertices: 3, n_faces: 1, distance_um: [0.1, -0.1, 0],
      mean_abs_um: 0.1, rms_um: 0.1, p95_abs_um: 0.1, max_abs_um: 0.1, mean_signed_um: 0, scale_um: 0.2,
      sample_spacing_um: 0.05, n_samples: 10, deviation_a: null, deviation_b: null } }));
  }
  if (method === 'GET' && /\/api\/mesh-jobs\//.test(pathname)) {
    const job = server.jobs.get(pathname.split('/').pop());
    if (!job) return reply({ detail: 'Not Found' }, 404);
    job.seen += 1;
    if (server.holdBuilds || job.seen <= server.polls) {
      return reply({ job_id: job.id, status: 'running', waiting: '', kind: job.kind, seconds: job.seen,
        progress: { stage: 'contouring', done: job.seen, total: 10 }, mesh: null, result: null });
    }
    if (!job.finished) { job.finished = true; server.open -= 1; }
    return reply({ job_id: job.id, kind: job.kind, seconds: 3, ...job.work() });
  }
  if (method === 'DELETE') {
    const job = server.jobs.get(pathname.split('/').pop());
    if (job && !job.finished) { job.finished = true; server.open -= 1; }
    return reply({ ok: true });
  }
  if (method === 'GET' && /\/nodes/.test(pathname)) {
    server.nodesHits += 1;
    if (server.nodesGate) await server.nodesGate;
    return server.failNodes ? reply({ detail: 'nodes broke' }, 500) : reply(server.nodes);
  }
  if (method === 'GET' && /closed-curves/.test(pathname)) return reply([]);
  if (method === 'POST' && pathname === '/api/neurons') {
    server.copyLoads.push(body);
    const mode = body.section_corrections;
    const missed = server.failCorrection && mode !== 'off';
    return reply({ summary: {
      id: body.id, source: body.source, copy_tag: `${mode}.${body.live_registration ? 1 : 0}`,
      section_correction: { requested: mode !== 'off', applied: mode !== 'off' && !missed, mode,
        note: missed ? 'the corrections service did not answer' : null, n_moved: 0 },
      live_registration: { requested: body.live_registration, applied: body.live_registration, n_moved: 0 },
    } });
  }
  throw new Error(`unscripted request: ${method} ${url}`);
};

// -- helpers ------------------------------------------------------------------
const record = { key: 'i:1', id: 1, source: 'NeitzInferiorMonkey', loadGen: 1 };
function makeView(count = 4, extra = {}) {
  globalThis.__renderers = [];
  setLoaded('i:1', 1);
  globalThis.document.activeElement = null;
  const parent = new El('div');
  const grid = new El('div');
  const summary = new El('span');
  summary.className = 'compare-summary';
  parent.append(grid, summary);
  const statuses = [];
  const running = [];
  const view = new CompareView(grid, {
    onStatus: (message, kind) => statuses.push([message, kind]),
    onRunningChange: (on) => running.push(on),
    ...extra,
  });
  view.setPaneCount(count);
  return { view, grid, summary, statuses, running };
}
const settle = (ms = 20) => new Promise((r) => setTimeout(r, ms));
/** Wait for a condition rather than a fixed time: the machine may be busy. */
async function until(cond, what = 'condition', ms = 5000) {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) assert.fail(`timed out waiting for ${what}`);
    await settle(2);
  }
}
const idle = (view) => until(() => !view.running, 'the view to go idle');
const posts = (kind) => server.log.filter(([m, p]) => m === 'POST' && p.endsWith(kind));
const meshJobs = () => server.log.filter(([m, p]) => m === 'GET' && p.endsWith('/mesh-job'));
const setTech = (view, i, id) => {
  const select = view.panes[i].head.find((c) => c.tagName === 'select')[0];
  select.value = id;
  select.fire('change');
};
const tick = (view, i) => {
  const box = view.panes[i].errorBox;
  box.checked = !box.checked;
  box.fire('change');
};
const banner = (pane) => pane.card.banner;

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('Compare builds the technique panes one after another, then the difference', async () => {
  resetServer();
  const { view, summary, statuses, running } = makeView(4);
  await view.build(record);
  assert.deepEqual(view.panes.map((p) => p.state), ['ready', 'ready', 'ready', 'ready']);
  assert.equal(meshJobs().length, 3);
  assert.equal(posts('mesh-diff-job').length, 1);
  assert.deepEqual(running, [true, false]);
  assert.match(summary.textContent, /^4 of 4 panes ready$/);
  assert.match(statuses.at(-1)[0], /^Comparing c1 · NeitzInferiorMonkey/);
  assert.equal(statuses.at(-1)[1], undefined);
  // The diff pane holds pane 1's mesh and paints the distance map over it.
  assert.equal(view.panes[3].layers[0].mesh, view.panes[0].layers[0].mesh);
  assert.ok(view.panes[3].renderer.calls.some((c) => c[0] === 'setMesh' && c[2] === true));
});

test('a second Compare with nothing changed rebuilds nothing', async () => {
  resetServer();
  const { view, statuses } = makeView(4);
  await view.build(record);
  const before = server.log.length;
  await view.build(record);
  assert.equal(server.log.length, before);
  assert.match(statuses.at(-1)[0], /up to date/);
});

test('error maps are measured from the built snapshot, not from the pane settings', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  const built = view.panes[0].layers[0].builtOptions;
  // Change the technique AFTER the build, tick error without pressing Compare.
  setTech(view, 0, 'voxel2_iso');
  assert.equal(view.panes[0].state, 'stale');
  tick(view, 0);
  await idle(view);
  // The stale pane was rebuilt through a mesh job first (the new settings) ...
  const lastMesh = meshJobs().at(-1)[2];
  assert.equal(lastMesh.meshlab, 'isotropic');
  // ... and the measurement asked for exactly what that build produced.
  const post = posts('mesh-deviation-job').at(-1)[2];
  assert.equal(post.meshlab, 'isotropic');
  assert.equal(JSON.stringify(post), view.panes[0].layers[0].builtKey);
  assert.notEqual(view.panes[0].layers[0].builtKey, JSON.stringify(built));
  assert.equal(view.panes[0].state, 'ready');
  assert.ok(view.panes[0].layers[0].deviation);
});

test('ticking error on a current pane measures what it holds, no rebuild', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  const meshesBefore = meshJobs().length;
  tick(view, 0);
  await idle(view);
  assert.equal(meshJobs().length, meshesBefore);
  const post = posts('mesh-deviation-job').at(-1)[2];
  assert.equal(JSON.stringify(post), view.panes[0].layers[0].builtKey);
  assert.equal(view.panes[0].state, 'ready');
});

test('changing a knob while a measurement runs does not change what is measured', async () => {
  resetServer({ polls: 3 });
  const { view } = makeView(2);
  await view.build(record);
  const key = view.panes[0].layers[0].builtKey;
  tick(view, 0);
  await until(() => view.panes[0].state === 'measuring', 'measuring');
  setTech(view, 0, 'tube');
  await idle(view);
  assert.equal(JSON.stringify(posts('mesh-deviation-job').at(-1)[2]), key);
  assert.equal(view.panes[0].layers[0].builtKey, key);
  assert.ok(view.panes[0].layers[0].deviation);
  // The map belongs to the surface on screen, which no longer matches the knob.
  assert.equal(view.panes[0].state, 'stale');
});

test('annotations: one shared fetch; a failed fetch takes the overlay off and says so', async () => {
  resetServer();
  const { view } = makeView(3);
  await view.build(record);
  for (const i of [0, 1]) {
    view.panes[i].annotations = true;
    view._applyDisplay(view.panes[i]);
  }
  await until(() => server.log.some(([, p]) => p.endsWith('/nodes')), 'the annotation fetch');
  await settle(10);
  assert.equal(server.log.filter(([, p]) => p.endsWith('/nodes')).length, 1);
  // The rings are the pane's overlay under one key of their own, not any layer's.
  await until(() => view.panes[0].renderer.calls.some((c) => c[0] === 'setDiscs'), 'the rings');
  assert.equal(view.panes[0].renderer.calls.find((c) => c[0] === 'setDiscs')[2], 'A');
  resetServer({ failNodes: true });
  view._annotations = null;
  view.panes[2].annotations = true;
  await view._applyDisplay(view.panes[2]);
  assert.equal(view.panes[2].annotations, false);
  assert.match(banner(view.panes[2]).textContent, /annotations could not be loaded/);
  assert.equal(view.panes[2].state, 'ready', 'a failed overlay is not a failed pane');
});

test('Sync cameras turned on lines every pane up with the first that has a surface', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  let copies = 0;
  for (const p of view.panes) p.renderer.camera.position.copy = () => { copies += 1; };
  view.setSync(false);
  assert.equal(copies, 0);
  view.setSync(true);
  assert.equal(copies, 1);
});

test('a measurement that fails keeps the surface and shows the banner', async () => {
  resetServer({ failMeasure: true });
  const { view } = makeView(2);
  await view.build(record);
  tick(view, 0);
  await idle(view);
  const pane = view.panes[0];
  assert.equal(pane.state, 'error-measure');
  assert.ok(pane.layers[0].mesh, 'the surface is kept');
  assert.ok(!pane.renderer.calls.some((c) => c[0] === 'removeAll'), 'nothing was removed');
  assert.equal(pane.layers[0].config.error, false);
  assert.equal(pane.errorBox.checked, false);
  assert.equal(banner(pane).hidden, false);
  assert.match(banner(pane).textContent, /could not be measured/);
  assert.match(banner(pane).textContent, /worker died/);
  assert.match(banner(pane).textContent, /Failed during vertex distances/);
  // Retry measures again (the server still fails): the banner comes back.
  pane.card.retryButton.click();
  await idle(view);
  assert.equal(pane.state, 'error-measure');
});

test('a build that fails removes the surface: never yesterday\'s mesh', async () => {
  resetServer();
  const { view, statuses } = makeView(2);
  await view.build(record);
  assert.ok(view.panes[1].layers[0].mesh);
  server.failBuild = (opts) => opts.pathway === 'adaptive_taubin';
  setTech(view, 1, 'adaptive');
  await view.build(record);
  const pane = view.panes[1];
  assert.equal(pane.state, 'error-build');
  assert.equal(pane.layers[0].mesh, null);
  assert.ok(pane.renderer.calls.some((c) => c[0] === 'removeAll'));
  assert.match(banner(pane).textContent, /failed during isotropic remesh|Failed during isotropic remesh/i);
  assert.equal(statuses.at(-1)[1], 'error');
  assert.match(statuses.at(-1)[0], /1 of 2 panes failed/);
});

test('Stop all aborts the queue: running jobs cancelled, waiting panes stopped', async () => {
  resetServer({ holdBuilds: true });
  const { view, statuses, running } = makeView(5);
  const run = view.build(record);
  await until(() => view.panes[0].state === 'building', 'the first build');
  assert.equal(view.running, true);
  // Three panes work at once; the Difference waits for its sources and the fifth for a place.
  assert.deepEqual(view.panes.map((p) => p.state), ['building', 'building', 'building', 'queued', 'queued']);
  view.stopAll();
  await run;
  assert.equal(view.running, false);
  assert.ok(view.panes.every((p) => p.state === 'stopped' || p.state === 'idle'));
  assert.ok(server.log.some(([m, p]) => m === 'DELETE' && p.startsWith('/api/mesh-jobs/')));
  assert.equal(meshJobs().length, 3, 'no further job started after the stop');
  assert.equal(server.log.filter(([m]) => m === 'DELETE').length, 3, 'every running job was cancelled');
  assert.deepEqual(running, [true, false]);
  assert.match(statuses.at(-1)[0], /^Stopped/);
});

test('unticking error mid-measurement cancels it and keeps the surface', async () => {
  resetServer({ polls: 1 });
  const { view } = makeView(2);
  await view.build(record);
  server.holdBuilds = true;
  tick(view, 0);
  await until(() => view.panes[0].state === 'measuring', 'measuring');
  tick(view, 0);
  await idle(view);
  assert.equal(view.panes[0].state, 'ready');
  assert.ok(view.panes[0].layers[0].mesh);
  assert.equal(view.panes[0].layers[0].deviation, null);
  assert.ok(server.log.some(([m]) => m === 'DELETE'));
});

test('a result for another mesh is not painted: refetch once, then an error', async () => {
  resetServer({ deviationMeshId: 'someone-elses' });
  const { view } = makeView(2);
  await view.build(record);
  tick(view, 0);
  await idle(view);
  const pane = view.panes[0];
  assert.equal(pane.state, 'error-measure');
  assert.equal(posts('mesh-deviation-job').length, 2, 'measured again once');
  assert.match(banner(pane).textContent, /would not belong to what is on screen/);
  assert.equal(pane.layers[0].deviation, null);
});

test('changing a setting marks the pane out of date; Compare rebuilds only that pane', async () => {
  resetServer();
  const { view } = makeView(3);
  await view.build(record);
  setTech(view, 1, 'wrap');
  assert.equal(view.panes[1].state, 'stale');
  assert.equal(view.panes[1].chip.hidden, false);
  assert.equal(view.panes[1].chip.textContent, 'out of date');
  assert.ok(view.panes[1].chip.dataset.help.length > 20);
  assert.equal(view.panes[0].state, 'ready');
  const before = meshJobs().length;
  await view.build(record);
  assert.equal(meshJobs().length, before + 1);
  assert.equal(view.panes[1].state, 'ready');
});

test('panes added by a pane-count change are framed on the next Compare', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  view.setPaneCount(4);
  await view.build(record);
  assert.ok(view.panes.every((p) => p.renderer.framed >= 1 || p.layers[0].config.tech === 'diff'),
    view.panes.map((p) => p.renderer.framed).join());
});

test('a retry after a failed first build frames the pane', async () => {
  resetServer({ failBuild: () => true });
  const { view } = makeView(2);
  await view.build(record);
  assert.equal(view.panes[1].state, 'error-build');
  server.failBuild = null;
  view.panes[1].card.retryButton.click();
  await idle(view);
  assert.equal(view.panes[1].state, 'ready');
  assert.ok(view.panes[1].renderer.framed >= 1);
});

test('a new cell load makes every pane out of date', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  setLoaded('i:1', 2);
  view.markStale('i:1');
  assert.deepEqual(view.panes.map((p) => p.state), ['stale', 'stale']);
  view.markStale('i:1');
  setLoaded('i:1', 1);
  view.markStale('i:1');
  assert.deepEqual(view.panes.map((p) => p.state), ['ready', 'ready']);
});

test('lowering the pane count stops the work of the panes taken away; the others carry on', async () => {
  resetServer({ holdBuilds: true });
  const { view, running } = makeView(5);
  const run = view.build(record);
  await until(() => meshJobs().length === 3, 'three builds under way');
  const kept = view.panes[0].renderer;
  view.setPaneCount(1);
  assert.equal(view.panes.length, 1);
  assert.equal(view.panes[0].renderer, kept, 'the pane that stays is not rebuilt');
  assert.equal(view.running, true, 'the first pane is still building');
  await until(() => server.log.filter(([m]) => m === 'DELETE').length === 2, 'the two jobs to be cancelled');
  server.holdBuilds = false;
  await run;
  assert.equal(view.running, false);
  assert.deepEqual(running, [true, false]);
  assert.equal(view.panes[0].state, 'ready');
  await settle(20);
  assert.equal(meshJobs().length, 3, 'the panes that never started started nothing');
});

test('a Difference needs two technique panes: with fewer it refuses, says why, sends nothing', async () => {
  resetServer();
  const { view } = makeView(2);
  setTech(view, 1, 'diff');
  await view.build(record);
  assert.equal(view.panes[1].state, 'error-measure');
  assert.match(banner(view.panes[1]).textContent, /needs two different technique panes/);
  assert.equal(posts('mesh-diff-job').length, 0);
});

test('summary line reads like the brief', async () => {
  resetServer({ holdBuilds: true });
  const { view, summary } = makeView(6);
  const run = view.build(record);
  await until(() => view.panes[0].state === 'building', 'the first build');
  // Three panes work at once; the Difference and the fifth and sixth wait.
  assert.match(summary.textContent,
    /^0 of 6 panes ready, pane 1 building, pane 2 building, pane 3 building, 3 waiting, \d+ s$/);
  view.stopAll();
  await run;
});


/** A promise and the function that settles it. */
function gate() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release };
}

test('Dismiss clears a measurement failure: the pane is not failed any more', async () => {
  resetServer({ failMeasure: true });
  const { view, summary, statuses } = makeView(2);
  await view.build(record);
  tick(view, 0);
  await idle(view);
  const pane = view.panes[0];
  assert.equal(pane.state, 'error-measure');
  assert.match(summary.textContent, /1 failed/);
  pane.card.dismissButton.click();
  assert.equal(pane.error, null);
  assert.notEqual(pane.state, 'error-measure');
  assert.doesNotMatch(summary.textContent, /failed/);
  // The status line stops naming a banner that is gone.
  assert.doesNotMatch(statuses.at(-1)[0], /red banner/);
  assert.notEqual(statuses.at(-1)[1], 'error');
});

test('Dismiss clears a build failure too', async () => {
  resetServer({ failBuild: () => true });
  const { view, summary } = makeView(2);
  await view.build(record);
  assert.equal(view.panes[1].state, 'error-build');
  view.panes[1].card.dismissButton.click();
  assert.equal(view.panes[1].error, null);
  assert.equal(view.panes[1].state, 'idle');
  assert.match(summary.textContent, /^0 of 2 panes ready, 1 failed$/);
});

test('Stop during the annotation fetch after a build starts no measurement', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  const g = gate();
  server.nodesGate = g.promise;
  view.panes[0].annotations = true;
  view.panes[0].layers[0].config.error = true;
  setTech(view, 0, 'tube');
  const run = view.build(record);
  await until(() => server.nodesHits > 0, 'the annotation fetch');
  view.stopAll();
  g.release();
  await run;
  assert.equal(posts('mesh-deviation-job').length, 0, 'nothing measured after Stop');
  assert.equal(view.panes[0].state, 'stopped');
  assert.equal(view.panes[0].layers[0].deviation, null);
});

test('error ticked, unticked and ticked again while the first task still waits: it measures', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  server.holdBuilds = true;
  setTech(view, 0, 'tube');
  const run = view.build(record);
  await until(() => view.panes[0].state === 'building', 'the first build');
  tick(view, 1);
  tick(view, 1);
  tick(view, 1);
  assert.equal(view.panes[1].layers[0].config.error, true);
  assert.equal(view.panes[1].state, 'queued');
  server.holdBuilds = false;
  await run;
  await idle(view);
  assert.ok(view.panes[1].layers[0].deviation, 'the map arrived');
  assert.equal(posts('mesh-deviation-job').length, 1);
  assert.equal(view.panes[1].state, 'ready');
});

test('annotations that arrive after the pane became a Difference are not drawn on it', async () => {
  resetServer();
  const { view } = makeView(3);
  await view.build(record);
  const g = gate();
  server.nodesGate = g.promise;
  const pane = view.panes[2];
  pane.annotations = true;
  const drawing = view._applyDisplay(pane);
  await until(() => server.nodesHits > 0, 'the annotation fetch');
  setTech(view, 2, 'diff');
  g.release();
  await drawing;
  assert.ok(!pane.renderer.calls.some((c) => c[0] === 'setDiscs'));
});

test('annotations that arrive after the pane was taken away are not drawn into a disposed renderer', async () => {
  resetServer();
  const { view } = makeView(3);
  await view.build(record);
  const g = gate();
  server.nodesGate = g.promise;
  const pane = view.panes[2];
  pane.annotations = true;
  const drawing = view._applyDisplay(pane);
  await until(() => server.nodesHits > 0, 'the annotation fetch');
  view.setPaneCount(2);
  g.release();
  await drawing;
  assert.equal(pane.renderer.disposed, true);
  assert.ok(!pane.renderer.calls.some((c) => c[0] === 'setDiscs'));
  assert.equal(view.panes[0].renderer.disposed, undefined, 'the panes that stay keep their renderers');
});

test('a finished measurement leaves the other panes\' legends and geometry alone', async () => {
  resetServer();
  const { view } = makeView(3);
  await view.build(record);
  const box = (i) => view.panes[i].legend.find((c) => c.className === 'error-scale')[0];
  tick(view, 0);
  await idle(view);
  const first = box(0);
  const meshWrites = () => view.panes[0].renderer.calls.filter((c) => c[0] === 'setMesh').length;
  const before = meshWrites();
  tick(view, 1);
  await idle(view);
  assert.equal(box(0), first, 'the same scale box');
  assert.equal(meshWrites(), before, 'geometry not rebuilt for an unchanged scale');
  // A new, larger scale does repaint pane 1, but still in place.
  server.deviationScale = 0.9;
  tick(view, 2);
  await idle(view);
  assert.equal(box(0), first);
  assert.equal(first.value, '0.90');
  assert.ok(meshWrites() > before);
  // A box being typed in keeps what was typed.
  globalThis.document.activeElement = first;
  first.value = '1.23';
  view.panes[1].layers[0].deviation = { ...view.panes[1].layers[0].deviation, scale_um: 2 };
  view._repaintErrors();
  assert.equal(first.value, '1.23');
});

test('unloading the cell mid-Compare: panes that never ran stand down quietly', async () => {
  resetServer({ holdBuilds: true });
  const { view, statuses } = makeView(5);
  const run = view.build(record);
  await until(() => meshJobs().length === 3, 'three builds under way');
  unload('i:1');
  server.holdBuilds = false;
  await run;
  assert.deepEqual(view.panes.map((p) => !!p.error), [false, false, false, false, false]);
  assert.equal(meshJobs().length, 3, 'the queued pane started no job for a cell that is gone');
  assert.notEqual(statuses.at(-1)[1], 'error');
});

test('unloading the cell during a measurement ends the follow quietly', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  server.holdBuilds = true;
  tick(view, 0);
  await until(() => view.panes[0].state === 'measuring', 'measuring');
  unload('i:1');
  await idle(view);
  assert.equal(view.panes[0].error, null);
  assert.equal(view.panes[0].layers[0].deviation, null);
  assert.ok(server.log.some(([m]) => m === 'DELETE'));
});

test('a measurement that names another mesh once, then the right one: refetched and drawn', async () => {
  resetServer({ deviationIds: ['some-other-mesh'] });
  const { view } = makeView(2);
  await view.build(record);
  const meshesBefore = meshJobs().length;
  tick(view, 0);
  await idle(view);
  assert.equal(view.panes[0].state, 'ready');
  assert.ok(view.panes[0].layers[0].deviation);
  assert.equal(posts('mesh-deviation-job').length, 2);
  assert.equal(meshJobs().length, meshesBefore + 1, 'the surface was fetched once more');
});

test('a Difference whose two sources resolve to one pane measures against another pane', async () => {
  resetServer();
  const { view } = makeView(3);
  setTech(view, 2, 'diff');
  view.panes[2].layers[0].config.a = 0;
  view.panes[2].layers[0].config.b = 0;
  await view.build(record);
  assert.equal(view.panes[2].state, 'ready');
  const body = posts('mesh-diff-job')[0][2];
  assert.notEqual(JSON.stringify(body.a), JSON.stringify(body.b));
  assert.deepEqual(view.panes[2].layers[0].diffSources, { a: 1, b: 2 });
});

test('a failed Difference says it has no map, not that the surface is unchanged', async () => {
  resetServer();
  const { view } = makeView(2);
  setTech(view, 1, 'diff');
  await view.build(record);
  assert.equal(view.panes[1].state, 'error-measure');
  assert.match(view.panes[1].chip.dataset.help, /Difference/);
  assert.doesNotMatch(view.panes[1].chip.dataset.help, /surface is unchanged/);
});

test('a run that outlived the picker says to press Compare, not "Comparing"', async () => {
  resetServer({ holdBuilds: true });
  const { view, statuses } = makeView(2);
  const run = view.build(record);
  await until(() => view.panes[0].state === 'building', 'the first build');
  view.markStale('i:2');
  server.holdBuilds = false;
  await run;
  assert.match(statuses.at(-1)[0], /^Cell changed — press Compare/);
});

test('the incident path: stale pane + error tick builds first, then measures that build, nothing before', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  const mark = server.log.length;
  setTech(view, 0, 'voxel2_iso');          // settings changed, no Compare pressed
  tick(view, 0);                            // error ticked on the stale pane
  assert.equal(server.log.length, mark, 'no request is sent for a surface that was never built');
  await idle(view);
  const sent = server.log.slice(mark)
    .filter(([m, p]) => (m === 'GET' && p.endsWith('/mesh-job')) || (m === 'POST' && p.endsWith('-job')));
  assert.equal(sent.length, 2);
  assert.match(sent[0][1], /\/mesh-job$/);
  assert.equal(sent[0][2].meshlab, 'isotropic');
  assert.match(sent[1][1], /\/mesh-deviation-job$/);
  assert.equal(sent[1][2].meshlab, 'isotropic');
  assert.equal(view.panes[0].state, 'ready');
});

test('the options a measurement sends are all options the app carries', () => {
  for (const key of MESH_OPTION_KEYS) {
    assert.ok(key in DEFAULT_RENDER || ['smooth_iterations'].includes(key), `${key} is not in DEFAULT_RENDER`);
  }
  assert.equal(new Set(MESH_OPTION_KEYS).size, MESH_OPTION_KEYS.length, 'no key twice');
});

// -- the shared presets and each pane's own options ---------------------------------

// This browser's storage, in memory (node 25 has a global of its own that complains without a
// file): the saved technique defaults a pane is seeded from live in it.
const storage = new Map();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem: (k) => storage.get(k) ?? null,
    setItem: (k, v) => { storage.set(k, String(v)); },
    removeItem: (k) => { storage.delete(k); },
  },
});
/** Run `fn` with these saved technique defaults, then forget them. */
async function withSavedDefaults(saved, fn) {
  storage.set('sbfsem:technique-defaults', JSON.stringify(saved));
  try { return await fn(storage); } finally { storage.clear(); }
}
const paneSelect = (view, i) => view.panes[i].techSelect;
const curveRecord = (type_id = 1) => ({
  ...record, summary: { n_curve_annotations: 40, n_disc_annotations: 0, n_nodes: 400, type_id },
});

test('every pane lists the shared presets, then Custom and Difference; MeshLab follows the server', () => {
  const caps = { meshlabOff: false };
  const { view } = makeView(3, { caps: () => caps });
  const ids = ro.RENDER_PRESETS.map((p) => p.id);
  assert.equal(ids.length, 13);
  for (const i of [0, 1, 2]) {
    assert.deepEqual(paneSelect(view, i).options.map((o) => o.value), [...ids, 'custom', 'diff']);
  }
  assert.ok(paneSelect(view, 0).options.every((o) => !o.disabled));
  caps.meshlabOff = true;
  view.refreshCaps();
  for (const i of [0, 1, 2]) {
    const off = paneSelect(view, i).options.filter((o) => o.disabled).map((o) => o.value);
    assert.deepEqual(off, ['voxel2_iso', 'voxel2_qem', 'voxel2_poisson']);
    assert.match(paneSelect(view, i).options.find((o) => o.value === 'voxel2_qem').title, /pymeshlab/);
  }
});

test('the panes start as the recommended render, the baseline, the tube, a difference, then the rest', () => {
  const { view } = makeView(6);
  assert.deepEqual(view.panes.map((p) => p.layers[0].config.tech),
    ['voxel2', 'original', 'tube', 'diff', 'adaptive', 'wrap']);
  const pathways = view.panes.map((p) => `${p.layers[0].config.options.method}/${p.layers[0].config.options.pathway}`);
  assert.deepEqual(pathways.slice(0, 3), ['cylinder/voxel2', 'cylinder/original', 'curvemesh/original']);
  // The tube shares the main view's cache entry: it carries no smoothing override.
  assert.equal(view.panes[2].layers[0].config.options.smooth_iterations, ro.DEFAULT_RENDER.smooth_iterations);
  // A pane's options are complete, and never carry the 3D render's clip or coloring.
  for (const pane of view.panes) {
    assert.deepEqual(Object.keys(pane.layers[0].config.options), Object.keys(ro.DEFAULT_RENDER));
    assert.equal(pane.layers[0].config.options.clip_ipl, undefined);
    assert.equal(pane.layers[0].config.options.keep_above, true);
    assert.equal(pane.layers[0].config.options.color_by_strata, false);
  }
});

test('an out-of-range option is clamped before the request: 0.5 um fine voxel is sent as 0.3', async () => {
  resetServer();
  const { view } = makeView(2);
  view.panes[0].layers[0].config.options.voxel2_um = 0.5;
  view.panes[0].layers[0].config.options.wrap_blend = Number.NaN;
  await view.build(record);
  const query = meshJobs()[0][2];
  assert.equal(Number(query.voxel2_um), 0.3);
  assert.equal(Number(query.wrap_blend), ro.DEFAULT_RENDER.wrap_blend);
  assert.equal(JSON.parse(view.panes[0].layers[0].builtKey).voxel2_um, 0.3);
});

test('the build request is the pane\'s options: every geometry key, the same values', async () => {
  resetServer();
  const { view } = makeView(2);
  const pane = view.panes[1];
  pane.layers[0].config.options = co.paneOptions({
    ...pane.layers[0].config.options, tie_um: 7, post_order: 'decimate_first', meshlab: 'qem', meshlab_fraction: 0.4,
  });
  await view.build(record);
  const query = meshJobs().map((c) => c[2]).find((q) => q.pathway === 'original');
  const want = view._layerOptions(pane.layers[0]);
  for (const key of MESH_OPTION_KEYS) assert.equal(String(query[key]), String(want[key]), key);
  assert.equal(query.clip_ipl, undefined, 'the clip is never sent');
  assert.deepEqual(Object.keys(JSON.parse(pane.layers[0].builtKey)), MESH_OPTION_KEYS);
});

test('changing the pane count keeps the first panes\' options and toggles', () => {
  const { view } = makeView(4);
  view.panes[0].layers[0].config.options = co.paneOptions({ ...view.panes[0].layers[0].config.options, voxel2_um: 0.12, tie_um: 3 });
  view._deriveTech(view.panes[0].layers[0]);
  view.panes[1].layers[0].config.wire = true;
  view.panes[1].layers[0].config.opacity = 0.4;
  view.panes[2].annotations = true;
  view.panes[2].layers[0].config.error = true;
  const before = view.panes.slice(0, 3).map((p) => JSON.stringify(p.layers[0].config));
  view.setPaneCount(6);
  assert.equal(view.panes.length, 6);
  assert.deepEqual(view.panes.slice(0, 3).map((p) => JSON.stringify(p.layers[0].config)), before);
  assert.equal(view.panes[4].layers[0].config.tech, 'adaptive', 'a new pane starts at its default');
  view.setPaneCount(3);
  assert.deepEqual(view.panes.map((p) => JSON.stringify(p.layers[0].config)), before);
  // The surface is not carried: the new panes show "press Compare".
  assert.deepEqual(view.panes.map((p) => p.state), ['idle', 'idle', 'idle']);
  assert.equal(view.panes[0].techSelect.value, 'voxel2');
});

test('choosing a preset fills the pane with that technique at the saved defaults, nothing else', async () => {
  await withSavedDefaults({ 'cylinder|wrap': { wrap_spacing_um: 0.7, tie_um: 9 } }, () => {
    const { view } = makeView(3);
    setTech(view, 0, 'wrap');
    const o = view.panes[0].layers[0].config.options;
    assert.equal(o.pathway, 'wrap');
    assert.equal(o.wrap_spacing_um, 0.7);
    assert.equal(o.tie_um, 9);
    assert.equal(view.panes[0].layers[0].config.tech, 'wrap');
    // A MeshLab preset adds its mode on top of the technique's defaults.
    setTech(view, 1, 'voxel2_qem');
    assert.equal(view.panes[1].layers[0].config.options.meshlab, 'qem');
    assert.equal(view.panes[1].layers[0].config.options.pathway, 'voxel2');
    assert.equal(view.panes[1].layers[0].config.tech, 'voxel2_qem');
    assert.equal(view.panes[1].layers[0].config.options.clip_ipl, undefined);
  });
  // Saved defaults that include a MeshLab mode: the select names what is built.
  await withSavedDefaults({ 'cylinder|voxel2': { meshlab: 'isotropic' } }, () => {
    const { view } = makeView(2);
    setTech(view, 1, 'voxel2');
    assert.equal(view.panes[1].layers[0].config.options.meshlab, 'isotropic');
    assert.equal(view.panes[1].layers[0].config.tech, 'voxel2_iso');
    assert.equal(paneSelect(view, 1).value, 'voxel2_iso');
  });
});

test('a view field cannot be put in a pane: Clip IPL and the coloring are pinned', () => {
  const o = co.paneOptions({ ...ro.DEFAULT_RENDER, clip_ipl: 0.4, keep_above: false, color_by_strata: true });
  assert.equal(o.clip_ipl, undefined);
  assert.equal(o.keep_above, true);
  assert.equal(o.color_by_strata, false);
  assert.equal(o.method, 'cylinder');
});

test('options no preset names read Custom, and picking Custom changes nothing', () => {
  const { view } = makeView(2);
  const pane = view.panes[0];
  pane.layers[0].config.options = co.paneOptions({ ...pane.layers[0].config.options, method: 'hybrid', pathway: 'wrap' });
  view._deriveTech(pane.layers[0]);
  assert.equal(pane.layers[0].config.tech, 'custom');
  assert.equal(paneSelect(view, 0).value, 'custom');
  const before = JSON.stringify(pane.layers[0].config.options);
  setTech(view, 0, 'custom');
  assert.equal(JSON.stringify(pane.layers[0].config.options), before);
  assert.equal(pane.layers[0].config.tech, 'custom');
  // Original with MeshLab on is still Original, as in the 3D render's picker.
  pane.layers[0].config.options = co.paneOptions({
    ...pane.layers[0].config.options, method: 'cylinder', pathway: 'original', meshlab: 'isotropic',
  });
  view._deriveTech(pane.layers[0]);
  assert.equal(pane.layers[0].config.tech, 'original');
});

test('Closed curve and Hybrid panes build on a curve-traced cell, at the raster the cell wants', async () => {
  resetServer();
  const { view } = makeView(4);
  setTech(view, 0, 'closedcurve');
  setTech(view, 1, 'hybrid');
  assert.equal(view.panes[0].layers[0].config.options.pixel_size_um, ro.DEFAULT_RENDER.pixel_size_um);
  await view.build(curveRecord());
  const queries = meshJobs().map((c) => c[2]);
  const closed = queries.find((q) => q.method === 'closedcurve');
  const hybrid = queries.find((q) => q.method === 'hybrid');
  assert.equal(Number(closed.pixel_size_um), 0.25);
  assert.equal(Number(hybrid.pixel_size_um), 0.25);
  // The form will show the raster that was built, and the pane is current.
  assert.equal(view.panes[0].layers[0].config.options.pixel_size_um, 0.25);
  assert.equal(view.panes[0].state, 'ready');
  assert.equal(JSON.parse(view.panes[0].layers[0].builtKey).pixel_size_um, 0.25);
  // A technique that does not draw outlines is not given one.
  assert.equal(view.panes[2].layers[0].config.options.pixel_size_um, ro.DEFAULT_RENDER.pixel_size_um);
});

test('the raster follows the cell: vessels get 1 um, a typed or saved raster is kept', async () => {
  resetServer();
  const { view } = makeView(2);
  setTech(view, 0, 'closedcurve');
  await view.build(curveRecord(1));
  assert.equal(view.panes[0].layers[0].config.options.pixel_size_um, 0.25);
  // The next cell is a vessel: the raster this view chose is chosen again.
  setLoaded('i:1', 2);
  await view.build({ ...curveRecord(3), loadGen: 2 });
  assert.equal(view.panes[0].layers[0].config.options.pixel_size_um, 1);
  // The user's own raster is theirs.
  view.panes[0].layers[0].config.options = co.paneOptions({ ...view.panes[0].layers[0].config.options, pixel_size_um: 0.1 });
  setLoaded('i:1', 3);
  await view.build({ ...curveRecord(1), loadGen: 3 });
  assert.equal(view.panes[0].layers[0].config.options.pixel_size_um, 0.1);
  // So is a saved technique default.
  await withSavedDefaults({ 'closedcurve|original': { pixel_size_um: 0.025 } }, async () => {
    const other = makeView(2);
    setTech(other.view, 0, 'closedcurve');
    assert.equal(other.view.panes[0].layers[0].config.options.pixel_size_um, 0.025);
    await other.view.build(curveRecord(1));
    assert.equal(other.view.panes[0].layers[0].config.options.pixel_size_um, 0.025);
  });
});

test('an empty mesh says which techniques draw what, not "use the main tab"', async () => {
  resetServer({ emptyMesh: (q) => q.method === 'cylinder' });
  const { view } = makeView(2);
  await view.build(record);
  assert.equal(view.panes[0].state, 'error-build');
  const text = banner(view.panes[0]).textContent;
  assert.match(text, /Voxel remesh has nothing to draw/);
  assert.match(text, /Closed curve or Hybrid/);
  assert.doesNotMatch(text, /main 3D tab/);
});

// -- the options panel ---------------------------------------------------------------

/** A view with the options panel's element in the page, as index.html has it. */
function makeOptionsView(count = 4, extra = {}) {
  const calls = { sent: [], mainReads: 0 };
  globalThis.__renderers = [];
  setLoaded('i:1', 1);
  globalThis.document.activeElement = null;
  const parent = new El('div');
  const grid = new El('div');
  const summary = new El('span');
  summary.className = 'compare-summary';
  const panel = new El('div');
  panel.id = 'panel-compare-options';
  panel.hidden = true;
  parent.append(grid, summary, panel);
  const statuses = [];
  const main = { ...ro.DEFAULT_RENDER, pathway: 'wrap', tie_um: 5, wrap_spacing_um: 0.9 };
  const view = new CompareView(grid, {
    onStatus: (message, kind) => statuses.push([message, kind]),
    mainSettings: () => { calls.mainReads += 1; return main; },
    sendToMain: (options, name) => calls.sent.push([options, name]),
    ...extra,
  });
  view.setPaneCount(count);
  return { view, grid, panel, statuses, calls, main };
}
const form = (view) => view.optionsPanel.form;
const control = (view, key) => form(view).control(key);
/** What a box shows, as text (a real input stringifies what it is given). */
const shown = (view, key) => String(control(view, key).value);
const lastQuery = () => meshJobs().at(-1)[2];
const label = (view, key) => form(view).el(key);
/** Edit a control the way the browser does: set it, then let the delegated listener (on the
 * field's mount, the label's parent) hear it. */
function edit(view, key, value, type = 'input') {
  const c = control(view, key);
  c.value = String(value);
  label(view, key).parentElement.fire(type, { target: c, type });
}
const bridge = (view, id) => view.optionsPanel.bridges.get(id).click();
const headClick = (view, i) => view.panes[i].head.fire('click');

test('the arrow opens the focused pane\'s options; another header moves the panel to that pane', () => {
  const { view, panel } = makeOptionsView(4);
  assert.equal(panel.hidden, true);
  assert.equal(view.panes[3].optsButton, null, 'a Difference pane has no options');
  view.panes[1].optsButton.click();
  assert.equal(panel.hidden, false);
  assert.equal(view.focus, 1);
  assert.equal(view.panes[1].optsButton.attrs.get('aria-expanded'), 'true');
  assert.equal(view.panes[0].optsButton.attrs.get('aria-expanded'), 'false');
  assert.match(view.optionsPanel.title.textContent, /^Pane 2 — Original \(MATLAB\)$/);
  // The form shows pane 2's own options.
  assert.equal(control(view, 'pathway').value, 'original');
  assert.equal(control(view, 'method').value, 'cylinder');
  headClick(view, 2);
  assert.equal(view.focus, 2);
  assert.match(view.optionsPanel.title.textContent, /^Pane 3 — Tube \(curveToMesh\)$/);
  assert.equal(control(view, 'method').value, 'curvemesh');
  assert.ok(view.panes[2].el.classList.contains('focused'));
  assert.ok(!view.panes[1].el.classList.contains('focused'));
  // The panel closes from the pane's own arrow, its close button, and on a Difference pane.
  view.panes[2].optsButton.click();
  assert.equal(panel.hidden, true);
  view.panes[0].optsButton.click();
  view.optionsPanel.close.click();
  assert.equal(panel.hidden, true);
  view.panes[0].optsButton.click();
  headClick(view, 3);
  assert.equal(panel.hidden, true, 'a Difference pane has nothing to edit');
  assert.ok(view.panes[3].el.classList.contains('focused'));
  // Leaving the Difference does not bring it back: opening is the arrow's.
  headClick(view, 0);
  assert.equal(panel.hidden, true);
});

test('the panel has the same controls as the 3D render, minus Clip IPL', () => {
  const { view } = makeOptionsView(2);
  view.panes[0].optsButton.click();
  const keys = ro.RENDER_FIELDS.filter((f) => f.ui && f.scope === 'geometry').map((f) => f.key);
  assert.equal(keys.length, 33);   // Smooth surface µm and Exact tubes ° joined them (2.9.0)
  for (const key of keys) assert.ok(control(view, key), `${key} has a control`);
  assert.equal(control(view, 'clip_ipl'), null);
  // Same label and help as the schema gives the main panel.
  for (const key of keys) {
    assert.equal(label(view, key).dataset.help.endsWith(ro.FIELD_BY_KEY[key].help), true, key);
  }
  // Nothing in it has an id: it lives beside the main panel's controls.
  for (const key of keys) assert.ok(!control(view, key).id, key);
});

test('editing a control marks only that pane out of date; nothing rebuilds', async () => {
  resetServer();
  const { view } = makeOptionsView(3);
  await view.build(record);
  const jobs = meshJobs().length;
  view.panes[1].optsButton.click();
  edit(view, 'smooth_iterations', 4);
  assert.deepEqual(view.panes.map((p) => p.state), ['ready', 'stale', 'ready']);
  assert.equal(view.panes[1].layers[0].config.options.smooth_iterations, 4);
  assert.equal(meshJobs().length, jobs, 'an edit sends nothing');
  // Compare rebuilds exactly that pane, with the edit.
  await view.build(record);
  assert.equal(meshJobs().length, jobs + 1);
  assert.equal(Number(lastQuery().smooth_iterations), 4);
  assert.deepEqual(view.panes.map((p) => p.state), ['ready', 'ready', 'ready']);
});

test('typing is not rewritten under the cursor; a committed value shows what will be built', () => {
  const { view } = makeOptionsView(2);
  view.panes[0].optsButton.click();
  edit(view, 'voxel2_um', 0.9, 'input');
  assert.equal(view.panes[0].layers[0].config.options.voxel2_um, 0.3, 'clamped in the pane');
  assert.equal(shown(view, 'voxel2_um'), '0.9', 'the box keeps what is typed');
  edit(view, 'voxel2_um', 0.9, 'change');
  assert.equal(shown(view, 'voxel2_um'), '0.3', 'a committed value is shown as it is built');
  // A blank box is the minimum, as in the 3D render, not a request the server would refuse.
  edit(view, 'voxel2_faces', '', 'change');
  assert.equal(view.panes[0].layers[0].config.options.voxel2_faces, 0);
});

test('editing method or pathway renames the pane: a preset when one fits, else Custom', () => {
  const { view } = makeOptionsView(2);
  view.panes[0].optsButton.click();
  edit(view, 'pathway', 'wrap', 'change');
  assert.equal(view.panes[0].layers[0].config.tech, 'wrap');
  assert.equal(paneSelect(view, 0).value, 'wrap');
  assert.match(view.optionsPanel.title.textContent, /Wrap \(cube fit\)$/);
  edit(view, 'meshlab', 'isotropic', 'change');
  assert.equal(view.panes[0].layers[0].config.tech, 'wrap', 'MeshLab on Wrap still reads Wrap');
  edit(view, 'pathway', 'voxel2', 'change');
  assert.equal(view.panes[0].layers[0].config.tech, 'voxel2_iso');
  edit(view, 'method', 'hybrid', 'change');
  assert.equal(view.panes[0].layers[0].config.tech, 'custom');
  assert.equal(paneSelect(view, 0).value, 'custom');
  assert.match(view.optionsPanel.title.textContent, /Hybrid \(discs \+ curves\) \/ Voxel remesh$/);
});

test('which controls show follows the pane\'s technique, as in the 3D render', () => {
  const { view } = makeOptionsView(2);
  view.panes[0].optsButton.click();
  const visible = () => ro.RENDER_FIELDS
    .filter((f) => f.ui && control(view, f.key) && !label(view, f.key).hidden).map((f) => f.key);
  assert.ok(visible().includes('voxel2_blend_um') && !visible().includes('wrap_blend'));
  edit(view, 'pathway', 'wrap', 'change');
  assert.ok(visible().includes('wrap_blend') && !visible().includes('voxel2_blend_um'));
  edit(view, 'pathway', 'ultraliser', 'change');
  assert.ok(visible().includes('ultra_smooth') && visible().includes('ultra_soma'));
  assert.ok(!visible().includes('pixel_size_um'));
  edit(view, 'method', 'closedcurve', 'change');
  assert.ok(visible().includes('pixel_size_um'));
  assert.equal(label(view, 'reduce_fraction').classList.contains('inert'), true, 'Decimate is greyed for closed curve');
  // The visible set of every method, pathway and MeshLab mode is the schema's own.
  for (const method of ['cylinder', 'curvemesh', 'closedcurve', 'hybrid']) {
    for (const pathway of ['original', 'wrap', 'voxel2', 'ultraliser']) {
      view.panes[0].layers[0].config.options = co.paneOptions({ ...view.panes[0].layers[0].config.options, method, pathway });
      view._syncOptions();
      assert.deepEqual(visible(), ro.visibleKeys(view.panes[0].layers[0].config.options).filter((k) => control(view, k)),
        `${method}/${pathway}`);
    }
  }
});

test('the MeshLab control and presets are disabled together, with the reason, where the server lacks it', () => {
  const caps = { meshlabOff: false };
  const { view } = makeOptionsView(2, { caps: () => caps });
  view.panes[0].optsButton.click();
  assert.equal(control(view, 'meshlab').disabled, false);
  caps.meshlabOff = true;
  view.refreshCaps();
  assert.equal(control(view, 'meshlab').disabled, true);
  assert.match(control(view, 'meshlab').title, /pymeshlab/);
  assert.ok(paneSelect(view, 0).options.find((o) => o.value === 'voxel2_iso').disabled);
});

test('the cost chip prices the focused pane on the cell the picker names', () => {
  let picked = { ...record, summary: { n_nodes: 2400 } };
  const { view } = makeOptionsView(2, { cell: () => picked });
  view.panes[0].optsButton.click();
  const chip = view.optionsPanel.chip;
  assert.equal(chip.hidden, false);
  // Voxel remesh on 2,400 annotations: 0.00155 s each, times 5 for the server, times 4.
  assert.equal(chip.textContent, '⚠ 1 min 14 s');
  assert.ok(chip.classList.contains('warn'));
  assert.match(chip.dataset.help, /c1 · NeitzInferiorMonkey with this technique takes about 1 min 14 s/);
  edit(view, 'pathway', 'original', 'change');
  assert.equal(chip.textContent, '≈ 19 s');
  assert.ok(!chip.classList.contains('warn'));
  picked = { ...record };
  view.markStale('i:1');
  assert.equal(chip.hidden, true, 'a cell without a summary has nothing to price');
});

test('Use 3D render settings: the pane takes the panel\'s options, never its clip', () => {
  const { view, calls, statuses, main } = makeOptionsView(3);
  main.clip_ipl = 0.3;
  main.keep_above = false;
  main.color_by_strata = true;
  view.panes[0].optsButton.click();
  bridge(view, 'main');
  const o = view.panes[0].layers[0].config.options;
  assert.equal(calls.mainReads, 1);
  assert.equal(o.pathway, 'wrap');
  assert.equal(o.wrap_spacing_um, 0.9);
  assert.equal(o.tie_um, 5);
  assert.equal(o.clip_ipl, undefined);
  assert.equal(o.keep_above, true);
  assert.equal(o.color_by_strata, false);
  assert.equal(view.panes[0].layers[0].config.tech, 'wrap');
  assert.equal(control(view, 'pathway').value, 'wrap', 'the panel shows it');
  assert.equal(view.panes[1].layers[0].config.options.pathway, 'original', 'other panes are untouched');
  assert.match(statuses.at(-1)[0], /3D render panel.*Press Compare/);
});

test('Use this cell\'s settings: the compared cell\'s own render, without its clip', () => {
  const cell = { ...record, render: { ...ro.DEFAULT_RENDER, method: 'closedcurve', pathway: 'original', pixel_size_um: 0.25, clip_ipl: 0.5 } };
  const { view } = makeOptionsView(2, { cell: () => cell });
  view.panes[1].optsButton.click();
  bridge(view, 'cell');
  const o = view.panes[1].layers[0].config.options;
  assert.equal(o.method, 'closedcurve');
  assert.equal(o.pixel_size_um, 0.25);
  assert.equal(o.clip_ipl, undefined);
  assert.equal(view.panes[1].layers[0].config.tech, 'closedcurve');
  assert.equal(view.panes[0].layers[0].config.options.method, 'cylinder');
  // No cell loaded: it says so and changes nothing.
  const none = makeOptionsView(2, { cell: () => null });
  none.view.panes[1].optsButton.click();
  const before = JSON.stringify(none.view.panes[1].layers[0].config.options);
  bridge(none.view, 'cell');
  assert.equal(JSON.stringify(none.view.panes[1].layers[0].config.options), before);
  assert.equal(none.statuses.at(-1)[1], 'error');
});

test('Send to 3D render hands over the geometry options only; the pane and the page keep theirs', () => {
  const { view, calls } = makeOptionsView(2);
  view.panes[0].optsButton.click();
  edit(view, 'tie_um', 11, 'change');
  const before = JSON.stringify(view.panes[0].layers[0].config.options);
  bridge(view, 'send');
  assert.equal(calls.sent.length, 1);
  const [sent, name] = calls.sent[0];
  assert.deepEqual(Object.keys(sent), MESH_OPTION_KEYS);
  assert.equal(sent.tie_um, 11);
  assert.ok(!('clip_ipl' in sent) && !('keep_above' in sent) && !('color_by_strata' in sent));
  assert.match(name, /^Pane 1 — Voxel remesh$/);
  assert.equal(JSON.stringify(view.panes[0].layers[0].config.options), before);
  assert.equal(view.panes[0].state, 'idle');
});

test('Copy tuning goes only to the technique panes that use each knob, and never changes a technique', async () => {
  resetServer();
  const { view } = makeOptionsView(4);
  // Pane 1 (Voxel remesh) is tuned; pane 2 is Original, pane 3 the Tube, pane 4 a Difference.
  view.panes[0].layers[0].config.options = co.paneOptions({
    ...view.panes[0].layers[0].config.options, tie_um: 7, voxel2_um: 0.1, smooth_iterations: 4, post_order: 'decimate_first',
    meshlab: 'qem',
  });
  view._deriveTech(view.panes[0].layers[0]);
  await view.build(record);
  view.panes[0].optsButton.click();
  bridge(view, 'copy');
  const original = view.panes[1].layers[0].config.options;
  assert.equal(original.smooth_iterations, 4, 'Original uses the passes');
  assert.equal(original.post_order, 'decimate_first');
  assert.equal(original.tie_um, ro.DEFAULT_RENDER.tie_um, 'Original has no tie gap: not copied');
  assert.equal(original.voxel2_um, 0, 'nor a fine voxel');
  assert.equal(original.meshlab, 'none', 'the MeshLab mode is the technique\'s, not tuning');
  assert.equal(original.pathway, 'original');
  const tube = view.panes[2].layers[0].config.options;
  assert.equal(tube.smooth_iterations, ro.DEFAULT_RENDER.smooth_iterations, 'greyed out for a Tube: dead');
  assert.equal(tube.post_order, 'decimate_first', 'but the order is shared');
  assert.equal(tube.method, 'curvemesh');
  assert.equal(view.panes[3].layers[0].config.tech, 'diff');
  // The panes it changed are out of date (and the Difference that uses them); the source is not touched.
  assert.deepEqual(view.panes.map((p) => p.state), ['ready', 'stale', 'stale', 'stale']);
  assert.equal(view.panes[0].layers[0].config.options.tie_um, 7);
  // Nothing was rebuilt by it.
  assert.equal(meshJobs().length, 3);
});

test('Reset to technique defaults keeps the technique, MeshLab mode included, and takes your saved tuning', async () => {
  await withSavedDefaults({ 'cylinder|voxel2': { voxel2_um: 0.07 } }, () => {
    const { view } = makeOptionsView(2);
    view.panes[0].layers[0].config.options = co.paneOptions({
      ...view.panes[0].layers[0].config.options, voxel2_um: 0.2, tie_um: 3, meshlab: 'poisson', meshlab_depth: 11,
    });
    view._deriveTech(view.panes[0].layers[0]);
    view.panes[0].optsButton.click();
    bridge(view, 'reset');
    const o = view.panes[0].layers[0].config.options;
    assert.equal(o.voxel2_um, 0.07, 'your saved default');
    assert.equal(o.tie_um, ro.DEFAULT_RENDER.tie_um, 'the recommended value');
    assert.equal(o.meshlab, 'poisson');
    assert.equal(o.meshlab_depth, ro.DEFAULT_RENDER.meshlab_depth);
    assert.equal(view.panes[0].layers[0].config.tech, 'voxel2_poisson');
    assert.equal(shown(view, 'voxel2_um'), '0.07');
  });
});

test('Save as my technique defaults stores the pane\'s tuning where the 3D render reads it', async () => {
  await withSavedDefaults({}, (store) => {
    const { view, statuses } = makeOptionsView(2);
    view.panes[1].optsButton.click();
    edit(view, 'smooth_iterations', 3, 'change');
    bridge(view, 'save');
    const saved = JSON.parse(store.get('sbfsem:technique-defaults'));
    assert.deepEqual(Object.keys(saved['cylinder|original']), ro.TECH_KNOBS);
    assert.equal(saved['cylinder|original'].smooth_iterations, 3);
    assert.match(statuses.at(-1)[0], /^Saved as your defaults for Original \(MATLAB\)/);
    // The 3D render's picker would now give that technique these values.
    assert.equal(ro.techniqueDefaults('cylinder', 'original').smooth_iterations, 3);
  });
});

test('the panes\' options survive adding and removing panes, and the panel follows its pane', () => {
  const { view, panel } = makeOptionsView(4);
  view.panes[1].optsButton.click();
  edit(view, 'smooth_iterations', 6, 'change');
  view.setPaneCount(6);
  assert.equal(view.panes[1].layers[0].config.options.smooth_iterations, 6);
  assert.equal(panel.hidden, false);
  assert.equal(view.focus, 1);
  assert.equal(shown(view, 'smooth_iterations'), '6');
  assert.ok(view.panes[1].el.classList.contains('focused'));
  assert.equal(view.panes[1].optsButton.attrs.get('aria-expanded'), 'true');
  // The focused pane is removed: the panel moves to the last pane there is.
  headClick(view, 5);
  view.setPaneCount(3);
  assert.equal(view.focus, 2);
  assert.equal(view.optionsPanel.title.textContent.startsWith('Pane 3'), true);
  view.setPaneCount(2);
  assert.equal(view.focus, 1);
  assert.equal(view.panes[1].layers[0].config.options.smooth_iterations, 6);
});

test('Compare again snapshots the options: an edit after a build is measured only after a rebuild', async () => {
  resetServer();
  const { view } = makeOptionsView(2);
  await view.build(record);
  const key = view.panes[0].layers[0].builtKey;
  view.panes[0].optsButton.click();
  edit(view, 'tie_um', 9, 'change');
  tick(view, 0);
  await idle(view);
  // The map was measured on the rebuilt surface, and the body names exactly what was built.
  const post = posts('mesh-deviation-job').at(-1)[2];
  assert.equal(post.tie_um, 9);
  assert.equal(JSON.stringify(post), view.panes[0].layers[0].builtKey);
  assert.notEqual(view.panes[0].layers[0].builtKey, key);
  assert.equal(view.panes[0].state, 'ready');
  // And the Difference's two sides carry the full options of their panes.
  const diff = makeOptionsView(4);
  await diff.view.build(record);
  const body = posts('mesh-diff-job').at(-1)[2];
  assert.deepEqual(Object.keys(body.a), MESH_OPTION_KEYS);
  assert.deepEqual(Object.keys(body.b), MESH_OPTION_KEYS);
});

test('Custom… in the preset list opens the panel on that pane and changes nothing', () => {
  const { view, panel } = makeOptionsView(2);
  const before = JSON.stringify(view.panes[1].layers[0].config.options);
  setTech(view, 1, 'custom');
  assert.equal(panel.hidden, false);
  assert.equal(view.focus, 1);
  assert.equal(JSON.stringify(view.panes[1].layers[0].config.options), before);
  assert.equal(paneSelect(view, 1).value, 'original');
});

// -- the options panel under real input -----------------------------------------------

/**
 * What a browser does when the mouse presses a header button: Chrome focuses the button first,
 * so the header hears focusin (it bubbles), then the button's click, which then bubbles to the
 * header's own click listener.  A bare `click()` skips the first and the last.
 */
function press(view, i, which = 'optsButton') {
  const pane = view.panes[i];
  pane.head.fire('focusin', { target: pane[which] });
  pane[which].click();
  pane.head.fire('click', { target: pane[which] });
}
/** Keyboard focus arriving in a control of pane `i`'s header (focusin bubbles to the header). */
const tabInto = (view, i, which = 'techSelect') => {
  view.panes[i].head.fire('focusin', { target: view.panes[i][which] });
};

test('a mouse press on another pane\'s arrow moves the open panel there; on its own arrow it closes it', () => {
  const { view, panel } = makeOptionsView(4);
  press(view, 0);
  assert.deepEqual([view.focus, panel.hidden], [0, false]);
  // Chrome focuses the button before the click: that must not turn the click into a close.
  press(view, 1);
  assert.deepEqual([view.focus, panel.hidden], [1, false]);
  assert.match(view.optionsPanel.title.textContent, /^Pane 2 — /);
  assert.equal(view.panes[1].optsButton.attrs.get('aria-expanded'), 'true');
  assert.equal(view.panes[0].optsButton.attrs.get('aria-expanded'), 'false');
  press(view, 0);
  assert.deepEqual([view.focus, panel.hidden], [0, false]);
  assert.match(view.optionsPanel.title.textContent, /^Pane 1 — /);
  press(view, 0);
  assert.equal(panel.hidden, true, 'its own arrow closes it');
  press(view, 2);
  assert.deepEqual([view.focus, panel.hidden], [2, false], 'and opens it again, on that pane');
});

test('keyboard focus moving through the headers leaves the panel where it is, a Difference included', () => {
  const { view, panel } = makeOptionsView(4);
  press(view, 0);
  // Tab runs through every header on its way to the panel (which follows the grid).
  for (const i of [1, 2, 3]) {
    tabInto(view, i);
    assert.deepEqual([view.focus, panel.hidden], [0, false], `focus entering pane ${i + 1}`);
  }
  tabInto(view, 0, 'optsButton');
  assert.match(view.optionsPanel.title.textContent, /^Pane 1 — /);
  assert.equal(view.panes[0].optsButton.attrs.get('aria-expanded'), 'true');
  // Operating a control does follow the pane: picking its technique focuses it.
  setTech(view, 1, 'tube');
  assert.deepEqual([view.focus, panel.hidden], [1, false]);
  // The pane's arrow, pressed from the keyboard (a click with no mouse), toggles as before.
  view.panes[1].optsButton.click();
  assert.equal(panel.hidden, true);
  view.panes[1].optsButton.click();
  assert.equal(panel.hidden, false);
});

test('a panel closed because its pane became a Difference or went away stays closed until an arrow opens it', () => {
  const { view, panel } = makeOptionsView(6);
  press(view, 4);
  assert.equal(panel.hidden, false);
  // Fewer panes than the focus: it lands on the Difference (pane 4), which hides the panel ...
  view.setPaneCount(4);
  assert.equal(view.focus, 3);
  assert.equal(panel.hidden, true);
  // ... and touching a technique pane's header later is not an arrow.
  headClick(view, 0);
  assert.equal(panel.hidden, true, 'nobody pressed an arrow');
  assert.equal(view.panes[0].optsButton.attrs.get('aria-expanded'), 'false');
  press(view, 0);
  assert.equal(panel.hidden, false);
  // Making the focused pane a Difference through its own select does the same.
  setTech(view, 0, 'diff');
  assert.equal(panel.hidden, true);
  headClick(view, 1);
  assert.equal(panel.hidden, true, 'nobody pressed an arrow');
  // Back to a technique: the panel stays closed, opening is the arrow's.
  setTech(view, 0, 'original');
  assert.equal(panel.hidden, true);
  press(view, 0);
  assert.equal(panel.hidden, false);
});

test('opened from the keyboard, the arrow moves focus into the panel; Esc and the close button give it back', () => {
  const { view, panel } = makeOptionsView(3);
  const arrow = view.panes[1].optsButton;
  const active = () => globalThis.document.activeElement;
  // A mouse click (a click count of 1) leaves focus where the press put it.
  arrow.focus();
  arrow.fire('click', { detail: 1 });
  assert.equal(panel.hidden, false);
  assert.equal(active(), arrow);
  arrow.fire('click', { detail: 1 });
  assert.equal(panel.hidden, true);
  // Enter or Space (a click with no click count): the panel is far on in the tab order, so go there.
  arrow.fire('click', { detail: 0 });
  assert.equal(panel.hidden, false);
  assert.equal(active(), control(view, 'method'), 'the first control of the panel');
  // Esc inside the panel closes it and puts focus back on the arrow it came from.
  panel.fire('keydown', { key: 'Escape' });
  assert.equal(panel.hidden, true);
  assert.equal(active(), arrow);
  // Another key does nothing.
  arrow.fire('click', { detail: 0 });
  panel.fire('keydown', { key: 'a' });
  assert.equal(panel.hidden, false);
  // With the More menu open, Esc is the menu's first.
  view.optionsPanel.menuButton.click();
  panel.fire('keydown', { key: 'Escape' });
  assert.equal(panel.hidden, false, 'the menu has the first Esc');
  // The close button does the same as Esc.
  view.optionsPanel.close.click();
  assert.equal(panel.hidden, true);
  assert.equal(active(), arrow);
  // The arrow pressed again from the keyboard closes it, and focus is still on the arrow.
  arrow.fire('click', { detail: 0 });
  assert.equal(panel.hidden, false);
  arrow.focus();
  arrow.fire('click', { detail: 0 });
  assert.equal(panel.hidden, true);
  assert.equal(active(), arrow);
  // A Difference has no arrow to press and no panel to go to.
  setTech(view, 1, 'diff');
  assert.equal(view.panes[1].optsButton, null);
});

// -- the raster a Closed curve or Hybrid pane builds at ----------------------------------

const rasterOf = (view, i) => view.panes[i].layers[0].config.options.pixel_size_um;
const builtRaster = (method) => Number(meshJobs().filter((c) => c[2].method === method).at(-1)[2].pixel_size_um);

test('a raster picked in the panel is kept, 0.05 um included: Compare does not seed over it', async () => {
  resetServer();
  const { view } = makeOptionsView(4, { cell: () => curveRecord() });
  setTech(view, 0, 'closedcurve');
  setTech(view, 1, 'hybrid');
  // The cell has outlines and the pane is still at the technique's raster: seeded when it is built.
  assert.equal(rasterOf(view, 0), 0.25, 'seeded as soon as the technique is picked for that cell');
  // Pick 0.05 on purpose, in the panel.
  press(view, 0);
  edit(view, 'pixel_size_um', 0.05, 'change');
  assert.equal(rasterOf(view, 0), 0.05);
  await view.build(curveRecord());
  assert.equal(rasterOf(view, 0), 0.05, 'an explicit 0.05 is the user\'s');
  assert.equal(shown(view, 'pixel_size_um'), '0.05');
  assert.equal(builtRaster('closedcurve'), 0.05);
  assert.equal(JSON.parse(view.panes[0].layers[0].builtKey).pixel_size_um, 0.05);
  // The other pane, never touched, was seeded.
  assert.equal(builtRaster('hybrid'), 0.25);
  // A later cell does not take it back either.
  setLoaded('i:1', 2);
  await view.build({ ...curveRecord(3), loadGen: 2 });
  assert.equal(rasterOf(view, 0), 0.05);
  assert.equal(rasterOf(view, 1), 1, 'the seeded one follows the cell');
});

test('a raster typed equal to the seeded one is the user\'s too, and the next cell leaves it', async () => {
  resetServer();
  const { view } = makeOptionsView(2);
  setTech(view, 0, 'closedcurve');
  await view.build(curveRecord(1));
  assert.equal(rasterOf(view, 0), 0.25, 'seeded');
  press(view, 0);
  edit(view, 'pixel_size_um', 0.25, 'change');
  setLoaded('i:1', 2);
  await view.build({ ...curveRecord(3), loadGen: 2 });
  assert.equal(rasterOf(view, 0), 0.25, 'not the vessel\'s 1 um: the user took it over');
});

test('a Voxel size being typed into the pane form is not written over (review 2026-10-16)', async () => {
  resetServer();
  const { view } = makeOptionsView(2);
  setTech(view, 0, 'closedcurve');
  await view.build(curveRecord(1));
  press(view, 0);
  const box = control(view, 'pixel_size_um');
  assert.equal(shown(view, 'pixel_size_um'), '0.25');
  // Someone has the box and has typed into it; the panel is bound again (a build, a refocus).
  box.focus();
  box.value = '0.03';
  view._syncOptions();
  assert.equal(shown(view, 'pixel_size_um'), '0.03', 'what is being typed stays');
  // Left alone again (not focused, or the text is the size): the box shows the layer's size.
  globalThis.document.activeElement = null;
  view._syncOptions();
  assert.equal(shown(view, 'pixel_size_um'), '0.25');
  box.focus();
  view._syncOptions();
  assert.equal(shown(view, 'pixel_size_um'), '0.25', 'focused but not edited: written as before');
  globalThis.document.activeElement = null;
});

test('Use this cell\'s settings and Use 3D render settings keep the raster they bring, 0.05 um included', async () => {
  resetServer();
  const cellRender = { ...ro.DEFAULT_RENDER, method: 'closedcurve', pathway: 'original', pixel_size_um: 0.05 };
  const cell = { ...curveRecord(), render: cellRender };
  const { view, main } = makeOptionsView(3, { cell: () => cell });
  press(view, 1);
  bridge(view, 'cell');
  assert.equal(view.panes[1].layers[0].config.tech, 'closedcurve');
  assert.equal(rasterOf(view, 1), 0.05, 'the cell was built at 0.05: the pane does the same');
  Object.assign(main, { method: 'hybrid', pathway: 'voxel', pixel_size_um: 0.05 });
  press(view, 0);
  bridge(view, 'main');
  assert.equal(view.panes[0].layers[0].config.options.method, 'hybrid');
  assert.equal(rasterOf(view, 0), 0.05, 'the 3D render panel says 0.05: so does the pane');
  await view.build(cell);
  assert.equal(builtRaster('closedcurve'), 0.05);
  assert.equal(builtRaster('hybrid'), 0.05);
  // A raster that is not the default travels the same way.
  main.pixel_size_um = 0.1;
  bridge(view, 'main');
  assert.equal(rasterOf(view, 0), 0.1);
});

test('Reset to technique defaults and picking the technique again hand the raster back to the cell', async () => {
  resetServer();
  const { view } = makeOptionsView(2, { cell: () => curveRecord() });
  setTech(view, 0, 'closedcurve');
  press(view, 0);
  edit(view, 'pixel_size_um', 0.05, 'change');
  bridge(view, 'reset');
  assert.equal(rasterOf(view, 0), 0.25, 'the technique\'s untouched raster is seeded for this cell');
  edit(view, 'pixel_size_um', 0.05, 'change');
  setTech(view, 0, 'hybrid');
  assert.equal(rasterOf(view, 0), 0.25, 'a freshly picked technique is seeded');
  await view.build(curveRecord());
  assert.equal(builtRaster('hybrid'), 0.25);
});

test('a bridge does not make a raster the user never saw their choice: Method to Closed curve is still seeded', async () => {
  resetServer();
  const { view, main } = makeOptionsView(2, { cell: () => curveRecord() });
  Object.assign(main, { method: 'cylinder', pathway: 'voxel2', pixel_size_um: 0.05 });
  press(view, 0);
  bridge(view, 'main');
  assert.equal(view.panes[0].layers[0].config.pixelChosen, false, 'Voxel remesh shows no raster: nobody chose 0.05');
  edit(view, 'method', 'closedcurve', 'change');
  assert.equal(rasterOf(view, 0), 0.25, 'the box shows what Compare will build, before Compare');
  await view.build(curveRecord());
  assert.equal(builtRaster('closedcurve'), 0.25);
  // Back to a technique with no raster: the seeded one is given back, not carried along.
  edit(view, 'method', 'cylinder', 'change');
  assert.equal(rasterOf(view, 0), ro.DEFAULT_RENDER.pixel_size_um);
});

test('editing any other control does not take the seeded raster over', async () => {
  resetServer();
  const { view } = makeOptionsView(2, { cell: () => curveRecord() });
  setTech(view, 0, 'closedcurve');
  await view.build(curveRecord(1));
  assert.equal(rasterOf(view, 0), 0.25);
  press(view, 0);
  edit(view, 'meshlab', 'none', 'change');
  assert.equal(view.panes[0].layers[0].config.pixelChosen, false, 'only a typed Voxel size is the user\'s');
  setLoaded('i:1', 2);
  await view.build({ ...curveRecord(3), loadGen: 2 });
  assert.equal(rasterOf(view, 0), 1, 'a vessel is seeded at 1 um again');
});

test('a raster seeded for one cell goes back to the technique\'s own when the next cell has no outlines', async () => {
  resetServer();
  const { view } = makeOptionsView(2);
  setTech(view, 0, 'closedcurve');
  await view.build(curveRecord(1));
  assert.equal(rasterOf(view, 0), 0.25);
  const plain = { ...record, loadGen: 2, summary: { n_curve_annotations: 0, n_disc_annotations: 30, n_nodes: 300, type_id: 1 } };
  setLoaded('i:1', 2);
  await view.build(plain);
  assert.equal(rasterOf(view, 0), ro.DEFAULT_RENDER.pixel_size_um, 'a cell with discs only is built at the default');
  assert.equal(builtRaster('closedcurve'), ro.DEFAULT_RENDER.pixel_size_um);
  assert.equal(view.panes[0].layers[0].config.seededPixel, null);
  // The next cell with outlines is seeded again.
  setLoaded('i:1', 3);
  await view.build({ ...curveRecord(1), loadGen: 3 });
  assert.equal(rasterOf(view, 0), 0.25);
  // A technique default saved since is what it goes back to, not the recommended 0.05.
  await withSavedDefaults({ 'closedcurve|original': { pixel_size_um: 0.1 } }, async () => {
    setLoaded('i:1', 4);
    await view.build({ ...plain, loadGen: 4 });
    assert.equal(rasterOf(view, 0), 0.1);
  });
});

test('Copy tuning that carries a raster makes it the target\'s own; one that does not leaves the target seeded', async () => {
  resetServer();
  const { view } = makeOptionsView(4);
  setTech(view, 0, 'closedcurve');
  setTech(view, 1, 'hybrid');
  await view.build(curveRecord(1));
  assert.deepEqual([rasterOf(view, 0), rasterOf(view, 1)], [0.25, 0.25]);
  // The Voxel remesh pane's Passes go to the Hybrid pane: no raster is copied.
  view.panes[2].layers[0].config.options = co.paneOptions({
    ...view.panes[2].layers[0].config.options, method: 'cylinder', pathway: 'voxel2', smooth_iterations: 3,
  });
  view._deriveTech(view.panes[2].layers[0]);
  press(view, 2);
  bridge(view, 'copy');
  assert.equal(view.panes[1].layers[0].config.options.smooth_iterations, 3);
  setLoaded('i:1', 2);
  await view.build({ ...curveRecord(3), loadGen: 2 });
  assert.equal(rasterOf(view, 1), 1, 'still the cell\'s to choose');
  // A raster that is copied is a choice.
  press(view, 0);
  edit(view, 'pixel_size_um', 0.1, 'change');
  bridge(view, 'copy');
  assert.equal(rasterOf(view, 1), 0.1);
  setLoaded('i:1', 3);
  await view.build({ ...curveRecord(1), loadGen: 3 });
  assert.equal(rasterOf(view, 1), 0.1);
  // Including a 0.05 um that is also the technique's default, which only the choice keeps.
  setTech(view, 1, 'hybrid');
  assert.equal(rasterOf(view, 1), 0.25, 'a freshly picked technique is seeded for the cell');
  press(view, 0);
  edit(view, 'pixel_size_um', 0.05, 'change');
  bridge(view, 'copy');
  assert.equal(rasterOf(view, 1), 0.05);
  setLoaded('i:1', 4);
  await view.build({ ...curveRecord(1), loadGen: 4 });
  assert.equal(rasterOf(view, 1), 0.05, 'copied from a pane whose 0.05 um was chosen');
});

test('the open panel shows the raster Compare chose for the cell, and what it costs', async () => {
  resetServer();
  const traced = { ...curveRecord(1), summary: { ...curveRecord(1).summary, n_nodes: 8300 } };
  const { view } = makeOptionsView(2);
  setTech(view, 0, 'closedcurve');
  assert.equal(rasterOf(view, 0), 0.05, 'no cell has been named yet: the technique\'s own');
  press(view, 0);
  assert.equal(shown(view, 'pixel_size_um'), '0.05');
  await view.build(traced);
  assert.equal(shown(view, 'pixel_size_um'), '0.25', 'the open box shows what was built');
  // 0.0675 s per section at 0.05 um scaled to 0.25 um, over the ~415 sections of the cell, times the
  // server's 5, and a third more for the 5 um Join bodies the viewer starts at (2.6.2): 5.6 s x 1.3.
  const chip = view.optionsPanel.chip;
  assert.equal(chip.textContent, '≈ 7 s');
  assert.ok(!chip.classList.contains('warn'), 'a seeded Closed curve pane is not a warning');
});

test('an empty mesh from Closed curve or Hybrid does not tell the pane to pick Closed curve or Hybrid', async () => {
  resetServer({ emptyMesh: (q) => q.method === 'closedcurve' || q.method === 'hybrid' || q.method === 'curvemesh' });
  const { view } = makeView(3);
  setTech(view, 0, 'closedcurve');
  setTech(view, 1, 'hybrid');
  await view.build(record);
  for (const i of [0, 1]) {
    assert.equal(view.panes[i].state, 'error-build');
    const text = banner(view.panes[i]).textContent;
    assert.match(text, /has nothing to draw for this cell/);
    assert.match(text, /another cell|another technique/);
    assert.doesNotMatch(text, /pick Closed curve or Hybrid/, `pane ${i + 1}`);
    assert.doesNotMatch(text, /Cylinder and Tube cannot/, `pane ${i + 1}`);
  }
  // The Tube still gets the advice that applies to it.
  assert.match(banner(view.panes[2]).textContent, /pick Closed curve or Hybrid/);
});

test('Copy tuning copies only the knobs both panes use: a destination\'s own tuning is not overwritten with hidden defaults', () => {
  const { view } = makeOptionsView(4);
  // Pane 1: Voxel remesh with a tie gap.  Pane 3 becomes a Wrap tuned to its own Cube size, Ring detail and Blend.
  view.panes[0].layers[0].config.options = co.paneOptions({ ...view.panes[0].layers[0].config.options, tie_um: 7 });
  setTech(view, 2, 'wrap');
  view.panes[2].layers[0].config.options = co.paneOptions({
    ...view.panes[2].layers[0].config.options, wrap_spacing_um: 0.7, ring_arc_um: 0.6, wrap_blend: 3, tie_um: 12,
  });
  press(view, 0);
  bridge(view, 'copy');
  const wrap = view.panes[2].layers[0].config.options;
  assert.equal(wrap.tie_um, 7, 'Tie gaps are shown by both: copied');
  assert.deepEqual([wrap.wrap_spacing_um, wrap.ring_arc_um, wrap.wrap_blend], [0.7, 0.6, 3],
    'the source does not use these: it only held their hidden defaults');
  assert.equal(wrap.pathway, 'wrap');
  // The same through the pure function: only knobs both use are named.
  const { options, changed } = co.copyTuning(
    co.paneOptions({ ...ro.DEFAULT_RENDER, pathway: 'voxel2', tie_um: 7 }),
    co.paneOptions({ ...ro.DEFAULT_RENDER, pathway: 'wrap', wrap_spacing_um: 0.7, ring_arc_um: 0.6, wrap_blend: 3 }));
  assert.deepEqual(changed, ['tie_um']);
  assert.equal(options.wrap_spacing_um, 0.7);
  // A knob the destination uses and the source grays out is not copied either: Passes of an Ultraliser pane.
  const ultra = co.paneOptions({ ...ro.DEFAULT_RENDER, pathway: 'ultraliser', smooth_iterations: 4 });
  const orig = co.paneOptions({ ...ro.DEFAULT_RENDER, pathway: 'original', smooth_iterations: 2 });
  assert.equal(co.copyTuning(ultra, orig).options.smooth_iterations, 2);
});

test('Voxel remesh tuning copied onto a Closed curve pane leaves the pane\'s own Voxel size', () => {
  const { view, statuses } = makeOptionsView(3);
  setTech(view, 1, 'closedcurve');
  view.panes[1].layers[0].config.options = co.paneOptions({ ...view.panes[1].layers[0].config.options, pixel_size_um: 0.1 });
  view.panes[1].layers[0].config.pixelChosen = true;
  press(view, 0);
  bridge(view, 'copy');
  assert.equal(rasterOf(view, 1), 0.1, 'Voxel remesh has no Voxel size to give');
  assert.equal(view.panes[1].layers[0].config.pixelChosen, true);
  assert.match(statuses.at(-1)[0], /copied to 1 other pane|No other pane shares/);
});

test('the More menu closes with the panel', () => {
  const { view } = makeOptionsView(2);
  press(view, 0);
  view.optionsPanel.menuButton.click();
  assert.equal(view.optionsPanel.menu.hidden, false);
  assert.equal(view.optionsPanel.menuButton.attrs.get('aria-expanded'), 'true');
  view.optionsPanel.close.click();
  assert.equal(view.optionsPanel.host.hidden, true);
  assert.equal(view.optionsPanel.menu.hidden, true, 'it does not wait, open, for the next time');
  assert.equal(view.optionsPanel.menuButton.attrs.get('aria-expanded'), 'false');
});

test('a view without the panel in the page still compares: no arrow, the presets work', async () => {
  resetServer();
  const { view } = makeView(2);
  assert.equal(view.optionsPanel, null);
  assert.equal(view.panes[0].optsButton, null);
  await view.build(record);
  assert.equal(view.panes[0].state, 'ready');
});

test('the job card speaks when the work changes phase, not at every poll, and names its bar', () => {
  const host = new El('div');
  let stops = 0;
  const card = new JobCard(host, { onStop: () => { stops += 1; } });
  const spoken = [];
  Object.defineProperty(card.live, 'textContent', {
    set(v) { spoken.push(v); }, get() { return spoken.at(-1) ?? ''; }, configurable: true,
  });
  assert.equal(card.live.attrs.get('role'), 'status');
  assert.equal(card.live.attrs.get('aria-live'), 'polite');
  card.show({ title: 'Building X' });
  for (let i = 1; i <= 8; i += 1) {
    card.update({ status: 'running', progress: { stage: 'contouring', done: i, total: 10 }, seconds: i });
  }
  assert.deepEqual(spoken, ['Building X: starting', 'Building X: contouring']);
  assert.equal(card.bar.attrs.get('aria-label'), 'Building X');
  card.update({ status: 'running', progress: { stage: 'smoothing', done: 1, total: 4 }, seconds: 9 });
  assert.equal(spoken.at(-1), 'Building X: smoothing');
  card.update({ status: 'running', reconnecting: true, reconnectSeconds: 5,
    progress: { stage: 'smoothing', done: 1, total: 4 } });
  assert.match(spoken.at(-1), /No answer from the server/);
  const n = spoken.length;
  card.update({ status: 'running', reconnecting: true, reconnectSeconds: 6,
    progress: { stage: 'smoothing', done: 1, total: 4 } });
  assert.equal(spoken.length, n, 'a longer silence is not announced again');
  card.setTitle('Measuring');
  assert.equal(card.bar.attrs.get('aria-label'), 'Measuring');
  // Stop: once, and the button stays a button (a disabled one drops focus).
  card.stopButton.click();
  card.stopButton.click();
  assert.equal(stops, 1);
  assert.equal(card.stopButton.attrs.get('aria-disabled'), 'true');
  assert.ok(!card.stopButton.disabled);
});

test('the card says the server stopped answering once, not twice', () => {
  const host = new El('div');
  const card = new JobCard(host, {});
  card.show({ title: 'Building X' });
  card.update({ status: 'running', reconnecting: true, reconnectSeconds: 14,
    progress: { stage: 'contouring', done: 3, total: 10 }, seconds: 12 });
  assert.equal(card.stage.textContent, 'contouring 3 of 10');
  assert.match(card.retrying.textContent, /no answer for \d+ s, retrying/);
  assert.doesNotMatch(card.stage.textContent, /no answer/);
});

test('the card shows a finished surface on its way down, with a bar when its size is known', () => {
  const host = new El('div');
  const card = new JobCard(host, {});
  const spoken = [];
  Object.defineProperty(card.live, 'textContent', {
    set(v) { spoken.push(v); }, get() { return spoken.at(-1) ?? ''; }, configurable: true,
  });
  card.show({ title: 'Building X' });
  card.update({ status: 'running', progress: null, kind: 'mesh',
    downloading: { received: 25e6, total: 153707436, faces: 8539810 } });
  assert.equal(card.stage.textContent, 'downloading the surface, 25 of 154 MB');
  assert.equal(card.bar.max, 153707436);
  assert.equal(card.bar.value, 25e6);
  card.update({ status: 'running', progress: null, kind: 'mesh',
    downloading: { received: 50e6, total: 153707436, faces: 8539810 } });
  assert.equal(spoken.at(-1), 'Building X: downloading the surface', 'spoken once, not at every count');
  assert.equal(spoken.filter((v) => /downloading/.test(v)).length, 1);
  // Broken off: the stage keeps what had come, the line below says it is retrying.
  card.update({ status: 'running', progress: null, kind: 'mesh', reconnecting: true, reconnectSeconds: 20,
    downloading: { received: 50e6, total: 153707436, broke: true } });
  assert.equal(card.stage.textContent, 'downloading the surface, 50 of 154 MB');
  assert.match(card.retrying.textContent, /no answer for 20 s, retrying/);
});

test('Stop, Retry and Dismiss hand keyboard focus back to the pane', () => {
  const host = new El('div');
  const target = new El('select');
  const card = new JobCard(host, { focusFallback: () => target });
  // Dismiss
  card.fail({ title: 't', message: 'm', onRetry: () => card.clearError() });
  globalThis.document.activeElement = card.dismissButton;
  card.dismissButton.click();
  assert.equal(globalThis.document.activeElement, target);
  // Retry (the host's callback takes the banner down)
  card.fail({ title: 't', message: 'm', onRetry: () => card.clearError() });
  globalThis.document.activeElement = card.retryButton;
  card.retryButton.click();
  assert.equal(globalThis.document.activeElement, target);
  // Stop: the card goes away when the work ends
  card.show({ title: 'x' });
  globalThis.document.activeElement = card.stopButton;
  card.hide();
  assert.equal(globalThis.document.activeElement, target);
  // Focus elsewhere on the page is left alone.
  const elsewhere = new El('input');
  globalThis.document.activeElement = elsewhere;
  card.show({ title: 'x' });
  card.hide();
  assert.equal(globalThis.document.activeElement, elsewhere);
});


// -- the Position shift layer ------------------------------------------------------

/** Six annotations on sections 10 to 15, each moved 0.3 to 0.8 um by the correction. */
const shiftNodes = [0, 1, 2, 3, 4, 5].map((i) => ({
  id: 100 + i, parent_id: 1, z_section: 10 + i, radius_um: 1, type_code: 1,
  xyz_um: [i, 0, (10 + i) * 0.07], shift_um: [0.3 + 0.1 * i, 0], live_shift_um: null,
}));
const shiftRecord = {
  ...record, color: '#2f6fed', display: {},
  summary: {
    section_correction: { requested: true, applied: true, n_moved: 6 },
    live_registration: { requested: true, applied: false, note: 'none' },
  },
};
function makeShiftView(count = 4) {
  globalThis.__renderers = [];
  setLoaded('i:1', 1);
  globalThis.document.activeElement = null;
  const parent = new El('div');
  const grid = new El('div');
  const summary = new El('span');
  summary.className = 'compare-summary';
  const strip = new El('div');
  strip.className = 'compare-shifts';
  strip.hidden = true;
  parent.append(grid, strip, summary);
  const statuses = [];
  const view = new CompareView(grid, { onStatus: (message, kind) => statuses.push([message, kind]) });
  view.setPaneCount(count);
  return { view, strip, statuses };
}
const shiftBox = (view, i) => view.panes[i].head
  .find((c) => c.className === 'compare-check' && c.textContent.includes('shift'))[0].children[0];
const tickShift = (view, i, on = true) => {
  const box = shiftBox(view, i);
  box.checked = on;
  box.fire('change');
};
const shiftCalls = (view, i, name = 'setShifts') => view.panes[i].renderer.calls.filter((c) => c[0] === name);

test('shift: two ticked panes draw one layer from one fetch, and a strip change reaches both', async () => {
  resetServer({ nodes: shiftNodes });
  const { view, strip } = makeShiftView(4);
  await view.build(shiftRecord);
  assert.equal(strip.hidden, true, 'no strip until a pane has the box ticked');
  tickShift(view, 0);
  tickShift(view, 2);
  await until(() => shiftCalls(view, 0).length && shiftCalls(view, 2).length, 'the layers');
  assert.equal(strip.hidden, false);
  assert.equal(shiftCalls(view, 1).length, 0, 'an unticked pane draws nothing');
  assert.equal(shiftCalls(view, 0)[0][1], 'cmp-shift', 'under its own key, not the mesh\'s');
  assert.equal(shiftCalls(view, 0)[0][2], 6, 'six arrows');
  assert.equal(server.nodesHits, 1, 'one annotation fetch for both panes');
  // Compare-local settings: initialized from the cell's display (shift on), not written back.
  assert.equal(view._shift.display.shiftColor, 'magnitude');
  view._shiftModel().set({ shiftColor: 'direction' });
  await until(() => shiftCalls(view, 0).length === 2 && shiftCalls(view, 2).length === 2, 'the redraw');
  assert.equal(shiftCalls(view, 1).length, 0);
  assert.equal(server.nodesHits, 1, 'a color change refetches nothing');
  assert.deepEqual(shiftCalls(view, 0).at(-1), shiftCalls(view, 2).at(-1), 'both panes got the same layer');
  assert.equal(shiftRecord.display.shiftColor, undefined, 'the cell\'s own display is untouched');
  // Unticking takes the layer away in that pane only.
  const removals = shiftCalls(view, 2, 'removeShifts').length;
  tickShift(view, 0, false);
  await until(() => !view.panes[0].renderer.shifts.size, 'the removal');
  assert.equal(shiftCalls(view, 2, 'removeShifts').length, removals);
  assert.equal(view.panes[2].renderer.shifts.size, 1, 'the other pane keeps its layer');
  tickShift(view, 2, false);
  await until(() => strip.hidden, 'the strip to hide');
});

test('shift: a new load of the cell fetches its annotations again and redraws every ticked pane', async () => {
  resetServer({ nodes: shiftNodes });
  const { view } = makeShiftView(3);
  await view.build(shiftRecord);
  tickShift(view, 0);
  tickShift(view, 1);
  await until(() => shiftCalls(view, 0).length && shiftCalls(view, 1).length, 'the layers');
  assert.equal(server.nodesHits, 1);
  setLoaded('i:1', 2);
  await view.build({ ...shiftRecord, loadGen: 2 });
  await until(() => shiftCalls(view, 0).length === 2 && shiftCalls(view, 1).length === 2, 'the redraw');
  assert.equal(server.nodesHits, 2, 'the annotations of the new copy');
});

test('shift: a pane kept through a pane-count change keeps its layer; panes added draw none', async () => {
  resetServer({ nodes: shiftNodes });
  const { view } = makeShiftView(4);
  await view.build(shiftRecord);
  tickShift(view, 0);
  tickShift(view, 1);
  await until(() => shiftCalls(view, 0).length && shiftCalls(view, 1).length, 'the layers');
  const renderers = view.panes.map((p) => p.renderer);
  const before = [shiftCalls(view, 0).length, shiftCalls(view, 1).length];
  // The panes that stay are not rebuilt: their renderers still hold the layer, and their boxes stay ticked.
  view.setPaneCount(3);
  assert.equal(shiftBox(view, 0).checked, true);
  assert.deepEqual(view.panes.map((p) => p.renderer), renderers.slice(0, 3));
  assert.equal(view.panes[0].renderer.shifts.size, 1);
  assert.equal(view.panes[1].renderer.shifts.size, 1);
  await settle(20);
  assert.equal(shiftCalls(view, 2).length, 0, 'a pane that never ticked it draws nothing');
  assert.deepEqual([shiftCalls(view, 0).length, shiftCalls(view, 1).length].map((n, i) => n >= before[i]), [true, true]);
  view.setPaneCount(6);
  await settle(20);
  assert.ok(view.panes.slice(3).every((p) => p.renderer.shifts.size === 0 && p.shift === false));
  assert.equal(view._shiftPanes().length, 2);
});

test('shift: a slice key on any pane steps the shared slice in every ticked pane', async () => {
  resetServer({ nodes: shiftNodes });
  const { view } = makeShiftView(4);
  await view.build(shiftRecord);
  tickShift(view, 0);
  tickShift(view, 2);
  await until(() => shiftCalls(view, 0).length && shiftCalls(view, 2).length, 'the layers');
  // Not in slice mode: the keys are the camera's.
  assert.equal(view.panes[1].renderer.onSliceStep({ key: ']' }), false);
  // Outside the slice at 0: only the slice is drawn, so the draw range is the window's.
  view._shiftModel().set({ shiftSlice: true, shiftSliceSpan: 0, shiftOutside: 0 });
  await until(() => shiftCalls(view, 0).length === 2 && shiftCalls(view, 2).length === 2, 'the slice');
  // It starts on the strongest shift: section 15 (index 5), a window of that section alone.
  assert.equal(view._shiftCenter(), 15);
  assert.deepEqual(shiftCalls(view, 0, 'setShiftWindow').at(-1)[2], [5 * 6, 6]);
  assert.equal(view.panes[1].renderer.onSliceStep({ key: '[' }), true, 'a pane with the box clear takes it too');
  assert.equal(view._shiftCenter(), 14);
  for (const i of [0, 2]) assert.deepEqual(shiftCalls(view, i, 'setShiftWindow').at(-1)[2], [4 * 6, 6]);
  assert.equal(shiftCalls(view, 1, 'setShiftWindow').length, 0);
  // Shift steps ten, clamped to the cell's sections; Home and End are the ends.
  assert.equal(view.panes[3].renderer.onSliceStep({ key: '{' }), true);
  assert.equal(view._shiftCenter(), 10);
  assert.equal(view.panes[0].renderer.onSliceStep({ key: 'End' }), true);
  assert.equal(view._shiftCenter(), 15);
  assert.equal(view.panes[0].renderer.onSliceStep({ key: 'Home' }), true);
  assert.equal(view._shiftCenter(), 10);
  // Up and Down are the slice's here: a pane has no annotation selection.
  assert.equal(view.panes[0].renderer.onSliceStep({ key: 'ArrowUp' }), true);
  assert.equal(view._shiftCenter(), 11);
  assert.equal(view.panes[0].renderer.onSliceStep({ key: 'ArrowUp', ctrlKey: true }), false, 'a modifier is not ours');
  assert.equal(view.panes[0].renderer.onSliceStep({ key: 'x' }), false);
});

test('shift: a Difference pane can draw it, and a failed annotation fetch un-ticks the box', async () => {
  resetServer({ nodes: shiftNodes });
  const { view, statuses } = makeShiftView(4);
  await view.build(shiftRecord);
  assert.equal(view.panes[3].layers[0].config.tech, 'diff');
  tickShift(view, 3);
  await until(() => shiftCalls(view, 3).length, 'the layer in the Difference pane');
  assert.equal(view.panes[3].state, 'ready', 'the overlay is not part of the pane\'s state');
  // The layer is the pane's own, not the mesh's: a cleared pane keeps it.
  view._clearContent(view.panes[3].layers[0]);
  assert.equal(view.panes[3].renderer.shifts.size, 1);
  resetServer({ failNodes: true });
  view._annotations.clear();
  view._shift.copies.clear();
  tickShift(view, 0);
  await until(() => !view.panes[0].shift, 'the box to be cleared');
  assert.match(statuses.at(-1)[0], /Pane 1: the annotations could not be loaded/);
  assert.equal(statuses.at(-1)[1], 'error');
  assert.equal(shiftBox(view, 0).checked, false, 'the rebuilt header matches');
});

// -- layers: several renderings in one pane ----------------------------------------------

const L = (view, i, k = 0) => view.panes[i].layers[k];
/** Set a layer's technique through the select the editor shows for it. */
function pickTech(view, i, k, id) {
  view.panes[i].editing = k;
  view._fillEditor(view.panes[i]);
  const select = view.panes[i].techSelect;
  select.value = id;
  select.fire('change');
}
const calls = (renderer, name) => renderer.calls.filter((c) => c[0] === name);
// The layers' chips: the chips' element holds the pane's state chip first, then one chip per layer, then the
// annotations overlay's.
const layerChips = (pane) => pane.chipsEl.children.filter((chip) => chip !== pane.chip && !chip.className.includes('overlay'));
const chipsOf = (pane) => layerChips(pane).map((chip) => chip.children[1].textContent);

test('a default Compare is what it always was: one layer a pane, the same requests, pane 4 a Difference', async () => {
  resetServer();
  const { view } = makeView(4);
  assert.deepEqual(view.panes.map((p) => p.layers.length), [1, 1, 1, 1]);
  assert.deepEqual(view.panes.map((p) => p.layers[0].config.tech), ['voxel2', 'original', 'tube', 'diff']);
  for (const pane of view.panes) {
    assert.equal(pane.layers[0].config.color, '#ffd400');
    assert.equal(pane.layers[0].config.opacity, 1);
    assert.equal(pane.layers[0].config.visible, true);
  }
  await view.build(record);
  assert.deepEqual(meshJobs().map((c) => `${c[2].method}/${c[2].pathway}`).sort(),
    ['curvemesh/original', 'cylinder/original', 'cylinder/voxel2']);
  // The Difference measures what panes 1 and 2 hold, the snapshots they were built from.
  assert.deepEqual(posts('mesh-diff-job')[0][2], { a: L(view, 0).builtOptions, b: L(view, 1).builtOptions });
  // Each pane's one layer is drawn under its own key, first in the draw order, plain.
  for (const pane of view.panes.slice(0, 3)) {
    const key = pane.layers[0].key;
    assert.ok(calls(pane.renderer, 'setMesh').every((c) => c[3] === key && c[2] === false));
    assert.equal(pane.renderer.order.get(key), 0);
    assert.deepEqual(pane.renderer.styles.get(key), { color: '#ffd400', opacity: 1, visible: true });
    assert.deepEqual(pane.renderer.zhide.get(key), {});
  }
});

test('adding a layer copies the layer being edited in the next palette color, translucent; four at most', () => {
  resetServer();
  const { view } = makeView(2);
  const pane = view.panes[0];
  L(view, 0).config.options = co.paneOptions({ ...L(view, 0).config.options, smooth_iterations: 4 });
  pane.addLayerItem.click();
  assert.equal(pane.layers.length, 2);
  const second = pane.layers[1];
  assert.equal(second.config.options.smooth_iterations, 4, 'a copy of the layer, tuning included');
  assert.equal(second.config.color, '#56b4e9');
  assert.equal(second.config.opacity, 0.45);
  assert.equal(pane.editing, 1, 'the new layer is the one being edited');
  assert.notEqual(second.config.options, L(view, 0).config.options, 'its options are its own');
  pane.addLayerItem.click();
  pane.addLayerItem.click();
  assert.deepEqual(pane.layers.map((l) => l.config.color), ['#ffd400', '#56b4e9', '#cc79a7', '#009e73']);
  assert.deepEqual(pane.layers.map((l) => l.config.opacity), [1, 0.45, 0.45, 0.45]);
  assert.equal(pane.addLayerItem.disabled, true, 'the menu says four is the most');
  assert.equal(view.addLayer(pane), null);
  assert.equal(pane.layers.length, 4);
  // Every layer has a key of its own, drawn in list order.
  assert.equal(new Set(pane.layers.map((l) => l.key)).size, 4);
  assert.deepEqual(pane.layers.map((l) => l.state), ['idle', 'idle', 'idle', 'idle']);
  assert.equal(pane.state, 'idle');
  assert.equal(layerChips(pane).length, 4);
});

test('a freed palette color is the next one a layer takes', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane); view.addLayer(pane); view.addLayer(pane);
  view.removeLayer(pane.layers[1]);
  assert.deepEqual(pane.layers.map((l) => l.config.color), ['#ffd400', '#cc79a7', '#009e73']);
  assert.equal(view.addLayer(pane).config.color, '#56b4e9');
});

test('Compare builds the layers of a pane one after another, each once, and then nothing', async () => {
  resetServer({ polls: 2 });
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  view.addLayer(pane);
  pickTech(view, 0, 2, 'wrap');
  await view.build(record);
  assert.equal(meshJobs().length, 3);
  assert.equal(server.maxOpen, 1, 'the layers of one pane never build at the same time');
  assert.deepEqual(meshJobs().map((c) => c[2].pathway), ['voxel2', 'original', 'wrap']);
  assert.deepEqual(pane.layers.map((l) => l.state), ['ready', 'ready', 'ready']);
  assert.equal(pane.state, 'ready');
  // Each layer draws under its own key, in its place in the order.
  assert.deepEqual(calls(pane.renderer, 'setMesh').map((c) => c[3]), pane.layers.map((l) => l.key));
  assert.deepEqual(pane.layers.map((l) => pane.renderer.order.get(l.key)), [0, 1, 2]);
  const before = server.log.length;
  await view.build(record);
  assert.equal(server.log.length, before, 'a second Compare asks for nothing');
});

test('a layer requests the surface it holds, from its snapshot, not the options it shows now', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  await view.build(record);
  const second = pane.layers[1];
  const built = second.builtKey;
  // Change the second layer's knob after the build, then ask for its error map.
  second.config.options = co.paneOptions({ ...second.config.options, smooth_iterations: 5 });
  view._deriveTech(second);
  view._refresh();
  assert.equal(second.state, 'stale');
  assert.equal(pane.layers[0].state, 'ready', 'the other layer is not touched');
  assert.equal(pane.state, 'stale', 'the pane is as stale as its worst layer');
  second.config.error = true;
  view._applyError(second);
  await idle(view);
  // It was rebuilt with the new knob first, then measured exactly as built.
  assert.equal(Number(meshJobs().at(-1)[2].smooth_iterations), 5);
  assert.equal(JSON.stringify(posts('mesh-deviation-job').at(-1)[2]), second.builtKey);
  assert.notEqual(second.builtKey, built);
  assert.equal(second.state, 'ready');
});

test('at most three panes work at once; the layers of a pane are in one queue', async () => {
  resetServer({ holdBuilds: true });
  const { view } = makeView(6);
  const run = view.build(record);
  await until(() => meshJobs().length === 3, 'three jobs');
  await settle(30);
  assert.equal(server.maxOpen, 3);
  assert.equal(meshJobs().length, 3, 'a fourth pane does not start until a place is free');
  assert.deepEqual(view.panes.map((p) => p.state),
    ['building', 'building', 'building', 'queued', 'queued', 'queued']);
  server.holdBuilds = false;
  await run;
  assert.equal(server.maxOpen, 3);
  assert.deepEqual(view.panes.map((p) => p.state), Array(6).fill('ready'));
  assert.equal(meshJobs().length, 5);
});

test('the same technique in two panes, or twice in a pane, is built and downloaded once', async () => {
  resetServer({ polls: 2 });
  const { view } = makeView(3);
  setTech(view, 1, 'voxel2');          // pane 2 is Voxel remesh like pane 1
  view.addLayer(view.panes[2]);        // and pane 3 gets a copy of its Tube
  await view.build(record);
  const queries = meshJobs().map((c) => `${c[2].method}/${c[2].pathway}`);
  assert.equal(queries.length, 2, queries.join());
  assert.deepEqual(queries.sort(), ['curvemesh/original', 'cylinder/voxel2']);
  assert.equal(L(view, 0).mesh, L(view, 1).mesh, 'one downloaded mesh, shared');
  assert.equal(L(view, 2).mesh, L(view, 2, 1).mesh);
  assert.deepEqual(view.panes.map((p) => p.state), ['ready', 'ready', 'ready']);
});

test('two panes asking for one mesh at once join one download; Stop on one leaves it to the other', async () => {
  resetServer({ holdBuilds: true });
  const { view } = makeView(2);
  setTech(view, 1, 'voxel2');
  const run = view.build(record);
  await until(() => meshJobs().length === 1, 'the one job');
  await settle(20);
  assert.equal(meshJobs().length, 1, 'the second pane joined the first');
  view._stopPane(view.panes[1]);
  await until(() => view.panes[1].state === 'stopped', 'pane 2 stopped');
  assert.equal(view.panes[0].state, 'building', 'pane 1 still waits for its mesh');
  assert.equal(server.log.filter(([m]) => m === 'DELETE').length, 0, 'the job was not cancelled for the other pane');
  server.holdBuilds = false;
  await run;
  assert.equal(view.panes[0].state, 'ready');
  // Stop on the last one waiting does cancel it.
  resetServer({ holdBuilds: true });
  const alone = makeView(2);
  setTech(alone.view, 1, 'voxel2');
  const again = alone.view.build(record);
  await until(() => meshJobs().length === 1, 'the job');
  alone.view.stopAll();
  await again;
  assert.equal(server.log.filter(([m]) => m === 'DELETE').length, 1);
});

test('a technique built before comes from the browser, not the server: back to it is instant', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  assert.equal(meshJobs().length, 2);
  setTech(view, 0, 'wrap');
  await view.build(record);
  assert.equal(meshJobs().length, 3);
  setTech(view, 0, 'voxel2');
  await view.build(record);
  assert.equal(meshJobs().length, 3, 'Voxel remesh was still filed from the first build');
  assert.equal(view.panes[0].state, 'ready');
  // Another load of the cell is another surface: nothing filed for the old one is used.
  setLoaded('i:1', 2);
  await view.build({ ...record, loadGen: 2 });
  assert.equal(meshJobs().length, 5);
  assert.equal(view.meshes.size, 2, 'and the old load\'s meshes were let go');
});

test('a measurement that names another mesh refetches past the filed one', async () => {
  resetServer({ deviationIds: ['some-other-mesh'] });
  const { view } = makeView(2);
  await view.build(record);
  const jobs = meshJobs().length;
  tick(view, 0);
  await idle(view);
  assert.equal(view.panes[0].state, 'ready');
  assert.equal(meshJobs().length, jobs + 1, 'the filed copy is not the one the server meant');
});

test('layers keep their place in the draw order as they are added, moved and removed', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  const [a] = pane.layers;
  const b = view.addLayer(pane);
  const c = view.addLayer(pane);
  const order = () => pane.layers.map((l) => pane.renderer.order.get(l.key));
  assert.deepEqual(order(), [0, 1, 2]);
  // Move the first layer later: it draws later, and the others close up.
  assert.equal(view.moveLayer(a, 1), true);
  assert.deepEqual(pane.layers.map((l) => l.id), [b.id, a.id, c.id]);
  assert.deepEqual(order(), [0, 1, 2]);
  assert.equal(pane.renderer.order.get(a.key), 1);
  assert.equal(view.moveLayer(c, 5), false, 'already last');
  assert.equal(view.moveLayer(b, -1), false, 'already first');
  assert.equal(layerChips(pane).length, 3);
  // The layer being edited stays the same layer through a move.
  pane.editing = 1;
  view.moveLayer(a, -1);
  assert.equal(view._editingLayer(pane), a);
  assert.equal(pane.editing, 0);
  // A removed layer leaves the renderer, and its place is closed up.
  view.removeLayer(pane.layers[1]);
  assert.equal(pane.layers.length, 2);
  assert.ok(calls(pane.renderer, 'removeAll').some((c2) => c2[1] === b.key));
  assert.equal(pane.renderer.order.has(b.key), false);
  assert.deepEqual(order(), [0, 1]);
  assert.equal(view.removeLayer(pane.layers[0]), true);
  assert.equal(view.removeLayer(pane.layers[0]), false, 'the last layer stays');
  assert.equal(pane.layers.length, 1);
});

test('a layer\'s color, opacity and visibility are live: nothing rebuilds and nothing goes out of date', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  await view.build(record);
  const jobs = meshJobs().length;
  const second = pane.layers[1];
  view.setLayerColor(second, '#CC79A7');
  view.setLayerOpacity(second, 0.8);
  view.setLayerVisible(second, false);
  const style = pane.renderer.styles.get(second.key);
  assert.deepEqual(style, { color: '#cc79a7', opacity: 0.8, visible: false });
  assert.equal(second.config.color, '#cc79a7');
  assert.equal(pane.state, 'ready');
  assert.equal(meshJobs().length, jobs);
  // Out-of-range or blank opacity is put back in range.
  view.setLayerOpacity(second, 7);
  assert.equal(second.config.opacity, 1);
  view.setLayerOpacity(second, '');
  assert.equal(second.config.opacity, 1);
  view.setLayerOpacity(second, 0.001);
  assert.equal(second.config.opacity, 0.05);
  // Ghost is one click.
  second.config.opacity = 0.45;
  view.setLayerOpacity(second, 0.15);
  assert.equal(pane.renderer.styles.get(second.key).opacity, 0.15);
  // Not a color: nothing changes.
  view.setLayerColor(second, 'blue');
  assert.equal(second.config.color, '#cc79a7');
});

test('a hidden layer still builds; shift on its dot shows only that layer, and again shows all', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  view.addLayer(pane);
  view.setLayerVisible(pane.layers[1], false);
  await view.build(record);
  assert.equal(meshJobs().length, 1, 'the three layers are one technique: one download');
  assert.equal(pane.layers[1].state, 'ready');
  assert.equal(pane.renderer.styles.get(pane.layers[1].key).visible, false);
  const dot = (k) => pane.layers[k].chip.dot;
  dot(2).fire('click', { shiftKey: true });
  assert.deepEqual(pane.layers.map((l) => l.config.visible), [false, false, true]);
  dot(2).fire('click', { shiftKey: true });
  assert.deepEqual(pane.layers.map((l) => l.config.visible), [true, true, true]);
  dot(0).fire('click', {});
  assert.equal(pane.layers[0].config.visible, false);
});

test('the editor has a show box that is the dot\'s switch; the chip\'s name opens the editor on its layer', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  assert.equal(pane.showBox.checked, true);
  pane.showBox.checked = false;
  pane.showBox.fire('change');
  assert.equal(pane.layers[1].config.visible, false, 'the layer being edited is the new one');
  assert.equal(pane.layers[1].chip.dot.attrs.get('aria-pressed'), 'false');
  view.setLayerVisible(pane.layers[1], true);
  assert.equal(pane.showBox.checked, true);
  // The name opens the editor on its layer, and again closes it.
  pane.layers[0].chip.name.fire('click', { detail: 1 });
  assert.equal(pane.editing, 0);
  assert.equal(pane.editor.hidden, false);
  assert.equal(pane.editorTitle.textContent, 'Layer 1 · Voxel remesh');
  assert.equal(pane.layers[0].chip.name.attrs.get('aria-expanded'), 'true');
  pane.layers[0].chip.name.fire('click', { detail: 1 });
  assert.equal(pane.editor.hidden, true);
  assert.equal(pane.layers[0].chip.name.attrs.get('aria-expanded'), 'false');
  // From the keyboard (a click with no click count) focus goes into the editor, and Esc brings it back.
  pane.layers[1].chip.name.fire('click', { detail: 0 });
  assert.equal(globalThis.document.activeElement, pane.techSelect);
  view._closePop({ focus: true });
  assert.equal(globalThis.document.activeElement, pane.layers[1].chip.name);
  // A reorder keeps focus on the same control (the button was rebuilt under the keyboard).
  view._openEditor(pane.layers[0]);
  pane.moveRight.focus();
  pane.moveRight.fire('click');
  assert.equal(pane.layers[1].id < pane.layers[0].id, true, 'the first layer moved later');
  assert.equal(pane.editor.hidden, false);
  assert.equal(globalThis.document.activeElement, pane.moveLeft, 'at the end its neighbor takes the focus');
});

test('a layer that failed keeps its failed chip when another layer takes the banner\'s place; the banner comes back when the work ends; Dismiss clears it', async () => {
  resetServer({ failBuild: (o) => o.pathway === 'original' && o.method === 'cylinder' });
  const { view, summary, statuses } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  view.addLayer(pane);
  pickTech(view, 0, 2, 'wrap');
  await view.build(record);
  assert.deepEqual(pane.layers.map((l) => l.state), ['ready', 'error-build', 'ready']);
  assert.equal(pane.state, 'error-build');
  // The third layer's progress card took the banner down while it worked; the second layer is still failed,
  // and when the pane's work was over its banner came back: the status line and the chip send people to it.
  assert.equal(banner(pane).hidden, false);
  assert.equal(pane.bannerLayer, pane.layers[1]);
  assert.match(banner(pane).textContent, /Pane 1 layer 2: .* failed to build/);
  assert.match(banner(pane).textContent, /out of memory/);
  assert.match(statuses.at(-1)[0], /^1 of 1 panes failed \(pane 1\) — the red banner on it says why\.$/);
  assert.equal(pane.card.running, false);
  assert.ok(pane.layers[1].chip.el.className.split(' ').includes('failed'));
  assert.match(pane.layers[1].chip.name.dataset.help, /It failed: out of memory/);
  assert.match(summary.textContent, /1 failed/);
  // The editor says so and retries only that layer.
  view._openEditor(pane.layers[1]);
  assert.equal(pane.retryButton.hidden, false);
  server.failBuild = null;
  pane.retryButton.click();
  await idle(view);
  assert.equal(pane.layers[1].state, 'ready');
  assert.equal(pane.state, 'ready');
  // A failure with its banner up: Dismiss clears the layer's failure.
  server.failBuild = () => true;
  pane.layers[2].config.options = co.paneOptions({ ...pane.layers[2].config.options, wrap_spacing_um: 0.9 });
  view._deriveTech(pane.layers[2]);
  await view.build(record);
  assert.equal(pane.layers[2].state, 'error-build');
  assert.equal(banner(pane).hidden, false);
  assert.match(banner(pane).textContent, /Pane 1 layer 3/);
  pane.card.dismissButton.click();
  assert.equal(pane.layers[2].error, null);
  assert.notEqual(pane.state, 'error-build');
});

test('two layers failed in one pane: the banner is the last one\'s, Dismiss shows the other, and the status line never names a banner that is not there', async () => {
  resetServer({ failBuild: (o) => o.pathway === 'original' || o.pathway === 'wrap' });
  const { view, statuses } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  view.addLayer(pane);
  pickTech(view, 0, 2, 'wrap');
  await view.build(record);
  assert.deepEqual(pane.layers.map((l) => l.state), ['ready', 'error-build', 'error-build']);
  const named = () => (banner(pane).hidden ? null : /Pane 1 layer (\d)/.exec(banner(pane).textContent)?.[1]);
  const says = () => statuses.at(-1)[0];
  // One banner at a time: the last layer to fail has it (the card of the work after it never took it back).
  assert.equal(pane.bannerLayer, pane.layers[2]);
  assert.equal(named(), '3');
  assert.match(says(), /the red banner on it says why/);
  // Dismissing it is not the end of the failure the status line named: layer 2's banner takes its place.
  pane.card.dismissButton.click();
  assert.equal(pane.layers[2].error, null);
  assert.equal(pane.bannerLayer, pane.layers[1], 'the other failed layer has its turn');
  assert.equal(named(), '2');
  assert.match(says(), /^1 of 1 panes failed \(pane 1\) — the red banner on it says why\.$/);
  assert.equal(pane.card.running, false);
  // Its Dismiss leaves nothing failed, no banner, and a line that no longer points at one.
  pane.card.dismissButton.click();
  assert.equal(pane.layers[1].error, null);
  assert.equal(pane.bannerLayer, null);
  assert.equal(named(), null);
  assert.doesNotMatch(says(), /red banner/);
  assert.notEqual(pane.state, 'error-build');
  // Work still going on in the pane keeps its card: a dismissal then re-shows nothing over it.
  resetServer({ holdBuilds: true, failBuild: (o) => o.pathway === 'wrap' });
  const held = makeView(1);
  const hp = held.view.panes[0];
  held.view.addLayer(hp);
  pickTech(held.view, 0, 1, 'wrap');
  hp.layers[0].error = { kind: 'build', error: new Error('earlier'), shown: { title: 'earlier', note: '', retry: null } };
  const run = held.view.build(record);
  await until(() => hp.layers[0].state === 'building', 'the first layer');
  held.view._reshowFailure(hp);
  assert.equal(hp.card.failed, false, 'no banner over a card that is working');
  server.holdBuilds = false;
  await run;
});

test('the card and the summary name the layer that is working', async () => {
  resetServer({ holdBuilds: true });
  const { view, statuses, summary } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  const run = view.build(record);
  await until(() => pane.layers[0].state === 'building', 'the first layer');
  assert.equal(pane.layers[1].state, 'queued');
  assert.match(pane.card.title.textContent, /^Layer 1 · Voxel remesh: Building Voxel remesh$/);
  assert.match(summary.textContent, /^0 of 1 panes ready, pane 1 layer 1 building, \d+ s$/);
  server.holdBuilds = false;
  await run;
  assert.match(statuses.at(-1)[0], /^Comparing c1/);
});

test('Stop on a pane stops all of its layers; the other panes carry on', async () => {
  resetServer({ holdBuilds: true });
  const { view } = makeView(2);
  view.addLayer(view.panes[0]);
  pickTech(view, 0, 1, 'wrap');
  const run = view.build(record);
  await until(() => meshJobs().length === 2, 'both panes building');
  view.panes[0].card.stopButton.click();
  await until(() => view.panes[0].layers.every((l) => l.state === 'stopped'), 'pane 1 stopped');
  assert.equal(view.panes[1].state, 'building');
  assert.equal(meshJobs().length, 2, 'the stopped pane\'s second layer never started');
  server.holdBuilds = false;
  await run;
  assert.equal(view.panes[1].state, 'ready');
  assert.deepEqual(view.panes[0].layers.map((l) => l.state), ['stopped', 'stopped']);
});

test('a Difference is a pane of its own: no layers added to it, and not chosen on a pane with several', () => {
  const { view } = makeView(4);
  assert.equal(view.addLayer(view.panes[3]), null);
  assert.equal(view.panes[3].addLayerItem.disabled, true);
  view.addLayer(view.panes[0]);
  // The select leaves Difference out for a pane with two layers, and refuses it if asked.
  view._fillEditor(view.panes[0]);
  assert.equal(view.panes[0].techSelect.options.find((o) => o.value === 'diff').disabled, true);
  view.panes[0].techSelect.value = 'diff';
  view.panes[0].techSelect.fire('change');
  assert.equal(L(view, 0).config.tech, 'voxel2');
  assert.equal(view.panes[0].techSelect.value, 'voxel2');
  // And a Difference keeps Position shift but takes no annotations overlay.
  view.setAnnotations(view.panes[3], true);
  assert.equal(view.panes[3].annotations, false);
});

test('a pane that becomes a Difference loses its annotations overlay', async () => {
  resetServer({ nodes: [] });
  const { view } = makeView(2);
  await view.build(record);
  view.setAnnotations(view.panes[0], true);
  await until(() => calls(view.panes[0].renderer, 'setDiscs').length, 'the rings');
  assert.equal(view.panes[0].annotations, true);
  setTech(view, 0, 'diff');
  assert.equal(view.panes[0].annotations, false);
  assert.equal(calls(view.panes[0].renderer, 'removeDiscs').at(-1)[1], 'A');
});

test('the annotations overlay is one per pane under its own key, however many layers; hiding keeps it', async () => {
  resetServer({ nodes: [] });
  const { view } = makeView(2);
  const pane = view.panes[0];
  view.addLayer(pane);
  await view.build(record);
  view.setAnnotations(pane, true);
  await until(() => calls(pane.renderer, 'setDiscs').length === 1, 'the rings');
  assert.equal(calls(pane.renderer, 'setDiscs')[0][2], 'A');
  assert.ok(pane.annotationsChip, 'the overlay has its own chip');
  // A second layer built later does not draw the rings again.
  pane.layers[1].config.options = co.paneOptions({ ...pane.layers[1].config.options, smooth_iterations: 3 });
  view._deriveTech(pane.layers[1]);
  await view.build(record);
  assert.equal(calls(pane.renderer, 'setDiscs').length, 1, 'once per cell load');
  view.setAnnotationsVisible(pane, false);
  assert.deepEqual(calls(pane.renderer, 'setStyle').filter((c) => c[1] === 'A').at(-1)[2], { visible: false });
  assert.equal(pane.annotations, true);
  view.setAnnotations(pane, false);
  assert.equal(calls(pane.renderer, 'removeDiscs').at(-1)[1], 'A');
  assert.equal(pane.annotationsChip, null);
  // On again: the rings are drawn again.
  view.setAnnotations(pane, true);
  await until(() => calls(pane.renderer, 'setDiscs').length === 2, 'the rings again');
});

test('the toolbar Error toggle paints every solid layer of every pane and half-ticks when only some are', async () => {
  resetServer();
  const parent = new El('div');
  const grid = new El('div');
  const toggle = new El('input');
  toggle.id = 'compare-error';
  parent.append(grid, toggle);
  globalThis.__renderers = [];
  setLoaded('i:1', 1);
  const view = new CompareView(grid, { onStatus: () => {} });
  view.setPaneCount(4);
  view.addLayer(view.panes[0]);
  await view.build(record);
  assert.equal(toggle.checked, false);
  toggle.checked = true;
  toggle.fire('change');
  await idle(view);
  const solid = view._allLayers().filter((l) => l.config.tech !== 'diff');
  assert.equal(solid.length, 4);
  assert.ok(solid.every((l) => l.config.error && l.deviation), 'every solid layer is measured');
  assert.equal(posts('mesh-deviation-job').length, 4);
  assert.equal(view.panes[3].layers[0].config.error, false, 'a Difference has no error paint');
  assert.equal(toggle.checked, true);
  assert.equal(toggle.indeterminate, false);
  // One layer's own box clears it: the toggle shows a mixture.
  view.panes[1].layers[0].config.error = false;
  view._applyError(view.panes[1].layers[0]);
  view._refresh();
  assert.equal(toggle.checked, false);
  assert.equal(toggle.indeterminate, true);
  // The toggle again: all on.
  toggle.checked = true;
  toggle.fire('change');
  await idle(view);
  assert.ok(solid.every((l) => l.config.error));
  // And off: every layer has its plain surface back.
  toggle.checked = false;
  toggle.fire('change');
  assert.ok(solid.every((l) => !l.config.error));
  assert.equal(toggle.indeterminate, false);
});

test('error paint on two layers of one pane shares one ramp and one scale', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'wrap');
  await view.build(record);
  view.setErrorAll(true);
  await idle(view);
  assert.ok(pane.layers.every((l) => l.deviation && !l.legend.hidden));
  const boxes = pane.legend.find((c) => c.className === 'error-scale');
  assert.equal(boxes.length, 2);
  assert.ok(pane.layers[0].legend.classList.contains('lead'));
  assert.ok(!pane.layers[1].legend.classList.contains('lead'), 'the ramp shows once');
  // Each stats line says whose it is.
  for (const layer of pane.layers) {
    assert.equal(layer.legendRefs.stats.style.borderLeft, `3px solid ${layer.config.color}`);
  }
  view.setLayerVisible(pane.layers[0], false);
  assert.equal(pane.legend.hidden, false);
});

test('the pane count is 1 to 6, panes are added and taken away at the end, and the rest are kept', async () => {
  resetServer();
  const { view } = makeView(2);
  assert.equal(view.grid.className, 'compare-grid panes-2');
  view.addLayer(view.panes[1]);
  view.panes[0].layers[0].config.wire = true;
  await view.build(record);
  const kept = view.panes.map((p) => p.renderer);
  const meshes = view.panes.map((p) => p.layers[0].mesh);
  view.setPaneCount(6);
  assert.equal(view.panes.length, 6);
  assert.deepEqual(view.panes.slice(0, 2).map((p) => p.renderer), kept, 'the same renderers');
  assert.deepEqual(view.panes.slice(0, 2).map((p) => p.layers[0].mesh), meshes, 'the surfaces are kept too');
  assert.equal(view.panes[1].layers.length, 2);
  assert.equal(view.panes[0].layers[0].config.wire, true);
  assert.deepEqual(view.panes.map((p) => p.layers[0].config.tech),
    ['voxel2', 'original', 'tube', 'diff', 'adaptive', 'wrap'], 'new panes start at their defaults');
  assert.equal(view.grid.className, 'compare-grid panes-6');
  assert.deepEqual(view.panes.slice(0, 2).map((p) => p.state), ['ready', 'ready']);
  assert.deepEqual(view.panes.slice(2).map((p) => p.state), ['idle', 'idle', 'idle', 'idle']);
  view.setPaneCount(1);
  assert.equal(view.panes.length, 1);
  assert.equal(view.grid.className, 'compare-grid panes-1');
  assert.equal(view.panes[0].renderer, kept[0]);
  view.setPaneCount(0);
  assert.equal(view.panes.length, 1, 'never fewer than one');
  view.setPaneCount(9);
  assert.equal(view.panes.length, 6, 'never more than six');
  // Each pane's elements are in the grid, and only those.
  assert.equal(view.grid.children.length, 6);
  view.setPaneCount(3);
  assert.equal(view.grid.children.length, 3);
});

test('a pane taken away is disposed with its renderer and card; the ones kept are not', () => {
  const { view } = makeView(4);
  const gone = view.panes.slice(2);
  const stay = view.panes.slice(0, 2);
  view.setPaneCount(2);
  assert.ok(gone.every((p) => p.renderer.disposed === true));
  assert.ok(stay.every((p) => p.renderer.disposed === undefined));
  assert.ok(gone.every((p) => p.layers.every((l) => !view._owns(l))), 'their layers are not the page\'s any more');
  assert.ok(stay.every((p) => p.layers.every((l) => view._owns(l))));
  assert.ok(gone.every((p) => !view.grid.children.includes(p.el)));
});

test('the options panel edits the layer being edited, and says which', () => {
  resetServer();
  const { view, panel } = makeOptionsView(2);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'wrap');
  view.panes[0].optsButton.click();
  assert.equal(panel.hidden, false);
  assert.match(view.optionsPanel.title.textContent, /^Pane 1 · layer 2 — Wrap \(cube fit\)$/);
  assert.equal(control(view, 'pathway').value, 'wrap');
  edit(view, 'wrap_spacing_um', 0.6, 'change');
  assert.equal(pane.layers[1].config.options.wrap_spacing_um, 0.6);
  assert.notEqual(pane.layers[0].config.options.wrap_spacing_um, 0.6, 'the other layer is untouched');
  assert.equal(pane.layers[0].state, 'idle');
  // Opening the editor on the first layer moves the panel to it.
  view._openEditor(pane.layers[0]);
  assert.match(view.optionsPanel.title.textContent, /^Pane 1 · layer 1 — Voxel remesh$/);
  assert.equal(control(view, 'pathway').value, 'voxel2');
  // A single-layer pane is named as it always was.
  headClick(view, 1);
  view.panes[1].optsButton.click();
  assert.match(view.optionsPanel.title.textContent, /^Pane 2 — Original \(MATLAB\)$/);
  // The panel's bridges act on the layer too.
  view._openEditor(pane.layers[1]);
  view.panes[0].optsButton.click();
  bridge(view, 'reset');
  assert.equal(pane.layers[1].config.options.wrap_spacing_um, ro.DEFAULT_RENDER.wrap_spacing_um);
});

test('Copy tuning reaches the other layers of the same pane too', () => {
  const { view } = makeOptionsView(2);
  const pane = view.panes[0];
  view.addLayer(pane);
  pane.layers[0].config.options = co.paneOptions({ ...pane.layers[0].config.options, tie_um: 7 });
  view._openEditor(pane.layers[0]);
  view.panes[0].optsButton.click();
  bridge(view, 'copy');
  assert.equal(pane.layers[1].config.options.tie_um, 7);
});

test('maximizing a pane hides the others without touching them; again puts the grid back', () => {
  const { view } = makeView(4);
  view.panes[2].layers[0].config.wire = true;
  view.toggleMaximize(view.panes[1]);
  assert.equal(view.maximized, view.panes[1]);
  assert.deepEqual(view.panes.map((p) => p.el.hidden), [true, false, true, true]);
  assert.deepEqual(view.panes.map((p) => p.renderer.paused), [true, false, true, true]);
  assert.ok(view.grid.classList.contains('has-maximized'));
  assert.ok(view.panes[1].el.classList.contains('maximized'));
  assert.ok(view.panes.every((p) => p.renderer.disposed === undefined), 'nothing was thrown away');
  assert.equal(view.panes[2].layers[0].config.wire, true, 'nor was anything reset');
  assert.equal(view.panes[1].maximizeItem.textContent, 'Restore');
  // Double-clicking the badge is the way in and out.
  view.panes[1].head.children[0].fire('dblclick');
  assert.equal(view.maximized, null);
  assert.deepEqual(view.panes.map((p) => p.el.hidden), [false, false, false, false]);
  assert.deepEqual(view.panes.map((p) => p.renderer.paused), [false, false, false, false]);
  view.panes[3].head.children[0].fire('dblclick');
  assert.equal(view.maximized, view.panes[3]);
  // Changing the pane count puts the grid back first.
  view.setPaneCount(2);
  assert.equal(view.maximized, null);
  assert.deepEqual(view.panes.map((p) => p.el.hidden), [false, false]);
  assert.equal(view.grid.className, 'compare-grid panes-2');
});

test('a Z range is applied to every layer and the annotations of its pane, and kept through rebuilds', async () => {
  resetServer();
  const { view } = makeView(2);
  const pane = view.panes[0];
  view.addLayer(pane);
  view.setZRange(pane, '2.5', '1');
  assert.deepEqual(pane.zRange, { min: 1, max: 2.5 }, 'put in order');
  for (const layer of pane.layers) assert.deepEqual(pane.renderer.zhide.get(layer.key), { min: 1, max: 2.5 });
  assert.deepEqual(pane.renderer.zhide.get('A'), { min: 1, max: 2.5 });
  assert.equal(view.panes[1].renderer.zhide.get('A') ?? null, null, 'the other panes are not cut');
  // A layer added later, and one rebuilt later, are cut the same way.
  const third = view.addLayer(pane);
  assert.deepEqual(pane.renderer.zhide.get(third.key), { min: 1, max: 2.5 });
  await view.build(record);
  assert.deepEqual(calls(pane.renderer, 'setZHide').filter((c) => c[1] === pane.layers[0].key).at(-1)[2], { min: 1, max: 2.5 });
  // One open side; blank both is no range.
  view.setZRange(pane, '', '4');
  assert.deepEqual(pane.zRange, { min: null, max: 4 });
  view.setZRange(pane, '', '');
  assert.equal(pane.zRange, null);
  for (const layer of pane.layers) assert.deepEqual(pane.renderer.zhide.get(layer.key), {});
  assert.deepEqual(pane.renderer.zhide.get('A'), {});
});

test('layer wireframes take the layer\'s hue; the yellow keeps the near-black wire', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  const wires = [];
  const original = pane.renderer.setWireframeStyle;
  pane.renderer.setWireframeStyle = function (key, style) { wires.push([key, style]); return original.call(this, key, style); };
  const second = view.addLayer(pane);
  view.setLayerColor(pane.layers[0], '#ffd400');
  assert.deepEqual(wires.filter(([k]) => k === pane.layers[0].key).at(-1)[1], { color: '#202020', opacity: 1 });
  const wire = wires.filter(([k]) => k === second.key).at(-1)[1].color;
  assert.notEqual(wire, '#202020');
  assert.match(wire, /^#[0-9a-f]{6}$/);
  // 45 % of #56b4e9 per channel.
  assert.equal(wire, '#275169');
});

test('every control of the layer editor and the menus carries a hover explanation', () => {
  const { view } = makeOptionsView(4);
  const pane = view.panes[0];
  view.addLayer(pane);
  const missing = [];
  const walk = (node, where) => {
    for (const child of node.children) {
      if (['button', 'select', 'input'].includes(child.tagName) && !child.dataset.help) {
        // A select, checkbox or number box inside a labelled wrapper is explained by the wrapper.
        let wrapped = false;
        for (let up = child.parentElement; up; up = up.parentElement) if (up.dataset.help) wrapped = true;
        if (!wrapped) missing.push(`${where}: ${child.tagName} ${child.className}`);
      }
      walk(child, where);
    }
  };
  for (const which of ['editor', 'addMenu', 'paneMenu']) walk(pane[which], which);
  // The close button is named by its aria-label and its glyph.
  assert.deepEqual(missing, []);
  assert.ok(layerChips(pane)[0].children[0].dataset.help, 'the chip\'s dot');
  assert.ok(layerChips(pane)[0].children[1].dataset.help, 'the chip\'s name');
  assert.ok(pane.addButton.dataset.help && pane.moreButton.dataset.help);
});

test('the layer editor follows the layer being edited, and a Difference has its own controls', () => {
  const { view } = makeView(4);
  const pane = view.panes[0];
  view.addLayer(pane);
  view._openEditor(pane.layers[1]);
  assert.equal(pane.editorTitle.textContent, 'Layer 2 · Voxel remesh');
  assert.equal(pane.colorInput.value, '#56b4e9');
  assert.equal(String(pane.opacityInput.value), '0.45');
  assert.equal(pane.moveRight.disabled, true);
  assert.equal(pane.moveLeft.disabled, false);
  assert.equal(pane.removeButton.disabled, false);
  view._openEditor(pane.layers[0]);
  assert.equal(pane.colorInput.value, '#ffd400');
  assert.equal(pane.moveLeft.disabled, true);
  // A Difference pane's editor has the sources and the scale, and no color or error box.
  const diff = view.panes[3];
  assert.equal(diff.colorInput, null);
  assert.equal(diff.errorBox, null);
  assert.equal(diff.editor.find((c) => c.tagName === 'select').length, 3);
  assert.equal(diff.editorTitle.textContent, 'Difference');
  assert.equal(diff.removeButton, null);
  // The editor is a popover: one at a time, closed by a click away.
  view._openEditor(pane.layers[1]);
  assert.equal(pane.editor.hidden, false);
  view._openPop(view.panes[1], 'add');
  assert.equal(pane.editor.hidden, true, 'one popover at a time');
  assert.equal(view.panes[1].addMenu.hidden, false);
  view._clickAway({ target: new El('div') });
  assert.equal(view.panes[1].addMenu.hidden, true);
});

test('a popover stays open when the click that used it rebuilt the control', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  view._openEditor(pane.layers[0]);
  const old = new El('button');          // a control the click removed from the page
  old.isConnected = false;
  view._clickAway({ target: old });
  assert.equal(pane.editor.hidden, false);
  view.moveLayer(pane.layers[0], 1);
  assert.equal(pane.editor.hidden, false, 'a reorder keeps the editor up');
});

test('a popover closes on Esc (focus back on what opened it), on a click away and when the window is resized', () => {
  const listeners = [];
  const had = { document: globalThis.document.addEventListener, window: globalThis.window };
  globalThis.document.addEventListener = (type, fn) => listeners.push([type, fn]);
  globalThis.window = { addEventListener: (type, fn) => listeners.push([`window:${type}`, fn]) };
  try {
    const { view } = makeView(2);
    const fire = (type, event) => listeners.filter(([t]) => t === type).forEach(([, fn]) => fn(event));
    const pane = view.panes[0];
    // Esc: the editor closes and focus returns to the chip that opened it.
    view._openEditor(pane.layers[0], { keyboard: true });
    assert.equal(pane.editor.hidden, false);
    fire('keydown', { key: 'a' });
    assert.equal(pane.editor.hidden, false, 'another key does nothing');
    fire('keydown', { key: 'Escape' });
    assert.equal(pane.editor.hidden, true);
    assert.equal(globalThis.document.activeElement, pane.layers[0].chip.name);
    // A click on the page closes it; one inside it does not; one on its own chip is the chip's to handle.
    view._openEditor(pane.layers[0]);
    fire('click', { target: pane.techSelect });
    assert.equal(pane.editor.hidden, false, 'a click inside the editor');
    fire('click', { target: pane.layers[0].chip.name });
    assert.equal(pane.editor.hidden, false, 'a click on the chip that opened it');
    fire('click', { target: new El('div') });
    assert.equal(pane.editor.hidden, true, 'a click on the page');
    // The + menu, then a resize.
    view._togglePop(pane, 'add');
    assert.equal(pane.addMenu.hidden, false);
    assert.equal(pane.addButton.attrs.get('aria-expanded'), 'true');
    fire('window:resize', {});
    assert.equal(pane.addMenu.hidden, true);
    assert.equal(pane.addButton.attrs.get('aria-expanded'), 'false');
    // Nothing is open: none of it does anything.
    fire('keydown', { key: 'Escape' });
    fire('click', { target: new El('div') });
  } finally {
    if (had.document) globalThis.document.addEventListener = had.document; else delete globalThis.document.addEventListener;
    if (had.window) globalThis.window = had.window; else delete globalThis.window;
  }
});

test('Closed curve and Hybrid seed their raster on every layer, not only the first', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 0, 'closedcurve');
  pickTech(view, 0, 1, 'hybrid');
  await view.build(curveRecord());
  assert.deepEqual(pane.layers.map((l) => l.config.options.pixel_size_um), [0.25, 0.25]);
  assert.deepEqual(meshJobs().map((c) => Number(c[2].pixel_size_um)), [0.25, 0.25]);
});

test('a layer\'s chip says what it is: its short name, its dot color, a hollow dot when hidden', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  assert.deepEqual(chipsOf(pane), ['Voxel remesh', 'Original']);
  const dot = pane.layers[1].chip.dot;
  assert.equal(dot.style.background, '#56b4e9');
  view.setLayerVisible(pane.layers[1], false);
  assert.equal(dot.style.background, 'transparent');
  assert.equal(dot.style.borderColor, '#56b4e9');
  assert.equal(dot.attrs.get('aria-pressed'), 'false');
  assert.ok(pane.layers[1].chip.el.className.includes('off'));
  // A pane with several layers says each layer's state; a single layer's lives on the pane's chip.
  assert.equal(pane.layers[1].chip.state.textContent, 'not built');
  assert.match(pane.layers[1].chip.name.dataset.help, /, hidden, not built\. Click to edit/, 'a crowded chip cuts the word short: the tooltip has it');
  const solo = makeView(1).view.panes[0];
  assert.equal(solo.layers[0].chip.state.textContent, '');
});

// -- the snapshot rule (2026-10-15): a layer is measured with the options that built its mesh ------

/** Edit a knob of a layer the way the options panel does, and let its chip and state follow. */
function editKnob(view, layer, knob, value) {
  layer.config.options = co.paneOptions({ ...layer.config.options, [knob]: value });
  view._deriveTech(layer);
  view._refresh();
}

test('the measurement sends the options that built the mesh, though they were edited after the build and before it', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  resetServer({ nodesGate: gate });
  const { view } = makeView(1);
  const pane = view.panes[0];
  const layer = pane.layers[0];
  tick(view, 0);                                     // error on, before anything is built
  view.setAnnotations(pane, true);                   // the build then waits for the rings' annotations
  const run = view.build(record);
  await until(() => server.nodesHits === 1, 'the annotation fetch, after the mesh came in');
  const built = layer.builtKey;
  assert.ok(built && layer.mesh, 'the mesh is in and its snapshot taken');
  const was = layer.builtOptions.smooth_iterations;
  // The user edits a knob in the gap between the build and the measurement.
  editKnob(view, layer, 'smooth_iterations', was + 6);
  release();
  await run;
  const body = posts('mesh-deviation-job').at(-1)[2];
  assert.equal(body.smooth_iterations, was, 'the body is what the mesh was built from');
  assert.equal(JSON.stringify(body), built);
  assert.equal(layer.builtKey, built);
  assert.ok(layer.deviation, 'it was measured');
  assert.equal(layer.state, 'stale', 'and the pane says the settings moved on');
});

test('a measurement that names another mesh refetches the surface it holds, not the one the settings now describe', async () => {
  resetServer({ polls: 3, deviationIds: ['some-other-mesh'] });
  const { view } = makeView(1);
  const layer = view.panes[0].layers[0];
  await view.build(record);
  const built = layer.builtKey;
  const was = layer.builtOptions.smooth_iterations;
  tick(view, 0);
  await until(() => layer.state === 'measuring', 'the measurement');
  editKnob(view, layer, 'smooth_iterations', was + 6);        // while the first answer is awaited
  await idle(view);
  assert.equal(Number(meshJobs().at(-1)[2].smooth_iterations), was, 'the refetch is of the snapshot');
  assert.equal(posts('mesh-deviation-job').length, 2);
  assert.equal(JSON.stringify(posts('mesh-deviation-job').at(-1)[2]), built);
  assert.equal(layer.builtKey, built);
  assert.equal(layer.state, 'stale');
});

test('a Difference retried after its panes\' settings moved on still measures the surfaces they hold', async () => {
  resetServer({ failDiff: true });
  const { view } = makeView(4);
  await view.build(record);
  const pane = view.panes[3];
  assert.equal(pane.state, 'error-measure');
  const [a, b] = [L(view, 0), L(view, 1)];
  const held = { a: a.builtOptions, b: b.builtOptions };
  editKnob(view, a, 'smooth_iterations', a.builtOptions.smooth_iterations + 6);
  server.failDiff = false;
  pane.card.retryButton.click();
  await idle(view);
  assert.deepEqual(posts('mesh-diff-job').at(-1)[2], held, 'the snapshots, not the settings shown now');
  assert.ok(pane.layers[0].diff, 'it was measured');
  assert.equal(pane.layers[0].state, 'stale', 'and it says its source moved on');
});

// -- the per-layer switches, and a layer taken away -----------------------------------------------

test('a layer\'s surface and wire boxes reach the renderer for that layer alone, and survive a rebuild', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  const first = pane.layers[0];
  const second = view.addLayer(pane);                // the editor is on the new layer
  assert.equal(pane.renderer.surfaces.get(second.key), true);
  assert.equal(pane.renderer.wires.get(second.key), false);
  pane.surfaceBox.checked = false;
  pane.surfaceBox.fire('change');
  assert.equal(second.config.surface, false);
  assert.equal(pane.renderer.surfaces.get(second.key), false);
  assert.equal(pane.renderer.surfaces.get(first.key), true, 'the other layer keeps its surface');
  pane.wireBox.checked = true;
  pane.wireBox.fire('change');
  assert.equal(pane.renderer.wires.get(second.key), true);
  assert.equal(pane.renderer.wires.get(first.key), false);
  // A build draws new objects; the switches are put on them again.
  await view.build(record);
  assert.equal(pane.renderer.surfaces.get(second.key), false);
  assert.equal(pane.renderer.wires.get(second.key), true);
  assert.equal(pane.renderer.surfaces.get(first.key), true);
  pane.surfaceBox.checked = true;
  pane.surfaceBox.fire('change');
  assert.equal(pane.renderer.surfaces.get(second.key), true);
});

test('removing a layer takes its failure banner with it, and an earlier removal keeps the editor on the same layer', async () => {
  resetServer({ failBuild: (o) => o.pathway === 'original' && o.method === 'cylinder' });
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  await view.build(record);
  assert.equal(pane.layers[1].state, 'error-build');
  assert.equal(banner(pane).hidden, false);
  assert.equal(pane.bannerLayer, pane.layers[1]);
  view.removeLayer(pane.layers[1]);
  assert.equal(banner(pane).hidden, true, 'its Retry would retry a layer that is gone');
  assert.equal(pane.bannerLayer, null);
  assert.equal(pane.state, 'ready');
  // The layer being edited is still the same one when a layer before it is removed (a layer in the
  // middle: at the end the clamp alone would hide a missing shift).
  view.addLayer(pane);
  view.addLayer(pane);
  view.addLayer(pane);
  const middle = pane.layers[2];
  view._openEditor(middle);
  assert.equal(pane.editing, 2);
  view.removeLayer(pane.layers[0]);
  assert.equal(pane.editing, 1);
  assert.equal(view._editingLayer(pane), middle);
  assert.equal(pane.editorTitle.textContent, 'Layer 2 · Voxel remesh');
});

test('soloing a hidden layer shows it and hides the others; again shows them all', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  view.addLayer(pane);
  view.setLayerVisible(pane.layers[1], false);
  view.soloLayer(pane.layers[1]);
  assert.deepEqual(pane.layers.map((l) => l.config.visible), [false, true, false]);
  assert.deepEqual(pane.layers.map((l) => pane.renderer.styles.get(l.key).visible), [false, true, false]);
  view.soloLayer(pane.layers[1]);
  assert.deepEqual(pane.layers.map((l) => l.config.visible), [true, true, true]);
});

test('a layer removed while its build is in the air is neither drawn nor reported when the server answers', async () => {
  for (const fail of [false, true]) {
    resetServer();
    const { view } = makeView(1);
    const pane = view.panes[0];
    await view.build(record);
    const gone = view.addLayer(pane);
    pickTech(view, 0, 1, 'original');
    server.holdBuilds = true;
    const run = view.build(record);
    await until(() => gone.state === 'building', 'layer 2 building');
    const key = gone.key;
    assert.equal(view._owns(gone), true);
    view.removeLayer(gone);
    assert.equal(view._owns(gone), false, 'a removed layer is not the page\'s any more');
    if (fail) server.failBuild = () => true;
    server.holdBuilds = false;
    await run;
    await idle(view);
    assert.equal(calls(pane.renderer, 'setMesh').filter((c) => c[3] === key).length, 0, `nothing drawn for it (failing: ${fail})`);
    assert.equal(banner(pane).hidden, true, `and nothing reported (failing: ${fail})`);
    assert.equal(pane.bannerLayer, null);
    assert.deepEqual(pane.layers.map((l) => l.state), ['ready']);
    assert.equal(pane.renderer.meshes.has(key), false);
  }
});

// -- a layer taken away or hidden while it works ---------------------------------------------

test('removing a layer while it builds takes its progress card down at once; its Stop does not linger', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  await view.build(record);                          // layer 1 is built
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');                  // layer 2 is not
  server.holdBuilds = true;
  const run = view.build(record);
  await until(() => pane.layers[1].state === 'building', 'layer 2 building');
  assert.equal(pane.card.running, true);
  assert.equal(view.removeLayer(pane.layers[1]), true);
  assert.equal(pane.card.running, false, 'the card describes work that no longer exists');
  server.holdBuilds = false;
  await run;
  await idle(view);
  assert.equal(pane.card.running, false, 'and the unwinding of that work does not bring it back');
  assert.equal(view.running, false);
  assert.equal(pane.layers.length, 1);
  assert.equal(pane.state, 'ready');
});

test('removing a layer while it is measured clears the card too', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  await view.build(record);
  server.holdBuilds = true;
  tick(view, 0);                                     // pane.errorBox is the editing layer's: the second
  await until(() => pane.layers[1].state === 'measuring', 'layer 2 measuring');
  assert.equal(pane.card.running, true);
  view.removeLayer(pane.layers[1]);
  assert.equal(pane.card.running, false);
  server.holdBuilds = false;
  await idle(view);
  assert.equal(pane.card.running, false);
});

test('removing a layer that only waits leaves the card of the layer that is working', async () => {
  resetServer({ holdBuilds: true });
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  const run = view.build(record);
  await until(() => pane.layers[0].state === 'building', 'layer 1 building');
  assert.equal(pane.layers[1].state, 'queued');
  view.removeLayer(pane.layers[1]);
  assert.equal(pane.card.running, true, 'layer 1 is still working under that card');
  assert.equal(pane.layers[0].state, 'building');
  server.holdBuilds = false;
  await run;
  assert.equal(pane.card.running, false);
  assert.equal(pane.state, 'ready');
});

test('unticking error while a stale layer rebuilds for it leaves no card behind either', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  await view.build(record);
  setTech(view, 0, 'wrap');                          // stale: ticking error rebuilds it first
  server.holdBuilds = true;
  tick(view, 0);
  await until(() => pane.layers[0].state === 'building', 'the rebuild');
  assert.equal(pane.card.running, true);
  tick(view, 0);                                     // unticked: the rebuild existed only to measure
  assert.equal(pane.card.running, false);
  server.holdBuilds = false;
  await idle(view);
  assert.equal(pane.card.running, false);
});

test('a hidden layer\'s error map draws its rim points hidden too, and showing the layer shows them', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  const layer = pane.layers[0];
  await view.build(record);
  view.setLayerVisible(layer, false);
  tick(view, 0);
  await idle(view);
  assert.ok(layer.deviation, 'the map was measured');
  assert.ok(calls(pane.renderer, 'setPoints').some((c) => c[1] === layer.key), 'with its rim points');
  assert.equal(pane.renderer.points.get(layer.key).visible, false, 'a hidden layer\'s points are not drawn');
  view.setLayerVisible(layer, true);
  assert.equal(pane.renderer.points.get(layer.key).visible, true);
  // Rebuilt hidden with the map on, the new points are hidden as well.
  view.setLayerVisible(layer, false);
  setTech(view, 0, 'wrap');
  await view.build(record);
  assert.equal(layer.painted?.deviation, layer.deviation);
  assert.equal(pane.renderer.points.get(layer.key).visible, false);
});

// -- the layers' header and popovers: focus, anchors, order, room ----------------------------------------------------

test('toggling the annotations dot updates it in place: the chips and the keyboard stay, and an open editor keeps a live anchor', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  view.setAnnotations(pane, true);
  const dot = pane.annotationsChip.dot;
  const names = pane.layers.map((l) => l.chip.name);
  dot.focus();
  dot.fire('click');
  assert.equal(pane.annotationsChip.dot, dot, 'the same button');
  assert.deepEqual(pane.layers.map((l) => l.chip.name), names, 'and the same layer chips');
  assert.equal(globalThis.document.activeElement, dot, 'the second Space has something to land on');
  assert.equal(dot.attrs.get('aria-pressed'), 'false');
  assert.equal(dot.style.background, 'transparent');
  dot.fire('click');
  assert.equal(dot.attrs.get('aria-pressed'), 'true');
  assert.notEqual(dot.style.background, 'transparent');
  assert.equal(globalThis.document.activeElement, dot);
  // With an editor open, whatever rebuilds the chips leaves its anchor pointing at a chip that is in the page.
  view._openEditor(pane.layers[0]);
  view.moveLayer(pane.layers[0], 1);                       // the chips are rebuilt
  const now = view._editingLayer(pane).chip.name;
  assert.ok(pane.chipsEl.contains(now));
  assert.equal(view._pop.anchor, now);
  assert.equal(now.attrs.get('aria-expanded'), 'true');
  view._closePop({ focus: true });
  assert.equal(globalThis.document.activeElement, now, 'Esc gives the keyboard to the chip that is there');
  // Chips rebuilt by themselves (the header after an annotation fetch failed, say), with the editor open and
  // nothing else rebuilt: the anchor is the chip that is there, and not the one that was.
  view._openEditor(pane.layers[0]);
  const before = pane.layers[0].chip.name;
  view._buildChips(pane);
  assert.notEqual(pane.layers[0].chip.name, before, 'a new chip');
  assert.equal(view._pop.anchor, pane.layers[0].chip.name);
  assert.equal(pane.layers[0].chip.name.attrs.get('aria-expanded'), 'true');
});

test('a click in the color picker or the in-page list the editor opened is not a click away from the editor', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  view._openEditor(pane.layers[0]);
  const inPopup = (cls) => {
    const popup = new El('div');
    popup.className = cls;
    const swatch = new El('button');
    popup.append(swatch);
    return swatch;
  };
  for (const cls of ['color-pop', 'sel-pop']) {
    view._clickAway({ target: inPopup(cls) });
    assert.equal(pane.editor.hidden, false, `a click in a .${cls} keeps the editor`);
  }
  // Anywhere else on the page, it closes, as ever.
  const other = new El('div');
  other.className = 'something-else';
  view._clickAway({ target: other });
  assert.equal(pane.editor.hidden, true);
});

test('a control that disables itself hands the keyboard to the editor\'s first control, and a menu action leaves it on a visible control', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  // Duplicate three times: the fourth layer disables the button the keyboard is on.
  view._openEditor(pane.layers[0]);
  for (let k = 0; k < 3; k += 1) {
    pane.duplicateButton.focus();
    pane.duplicateButton.fire('click');
  }
  assert.equal(pane.layers.length, 4);
  assert.equal(pane.duplicateButton.disabled, true);
  assert.equal(globalThis.document.activeElement, pane.techSelect, 'not the page');
  assert.ok(pane.editor.contains(globalThis.document.activeElement));
  // Remove down to the last layer: the Remove button disables itself too.
  for (let k = 0; k < 3; k += 1) {
    pane.removeButton.focus();
    pane.removeButton.fire('click');
  }
  assert.equal(pane.layers.length, 1);
  assert.equal(pane.removeButton.disabled, true);
  assert.ok(pane.editor.contains(globalThis.document.activeElement), 'the keyboard stayed in the editor');
  // Retry closes the editor and the keyboard goes back to the chip, not the page.
  pane.layers[0].error = { kind: 'build', error: new Error('x') };
  view._fillEditor(pane, { keepOpen: true });
  pane.retryButton.focus();
  view.record = null;                                       // nothing to retry against: only the focus is looked at
  pane.retryButton.fire('click');
  assert.equal(pane.editor.hidden, true);
  assert.equal(globalThis.document.activeElement, pane.layers[0].chip.name);
  // The + menu: Render layer puts the keyboard on the new layer's chip; Annotations on the + button.
  view._togglePop(pane, 'add', { keyboard: true });
  pane.addLayerItem.focus();
  pane.addLayerItem.fire('click');
  assert.equal(pane.layers.length, 2);
  assert.equal(globalThis.document.activeElement, pane.layers[1].chip.name);
  view._togglePop(pane, 'add', { keyboard: true });
  pane.annotationsItem.focus();
  pane.annotationsItem.fire('click');
  assert.equal(globalThis.document.activeElement, pane.addButton);
  // The tools menu goes back to its button.
  view._togglePop(pane, 'more', { keyboard: true });
  pane.maximizeItem.focus();
  pane.maximizeItem.fire('click');
  assert.equal(globalThis.document.activeElement, pane.moreButton);
});

test('Tab goes the way the eye does: one layer\'s chip among the buttons, several on the line under them', () => {
  const { view } = makeView(2);
  const pane = view.panes[0];
  const order = () => pane.head.children.filter((c) => [pane.chipsEl, pane.addButton, pane.moreButton, pane.addMenu, pane.paneMenu, pane.editor].includes(c));
  const names = () => order().map((c) => ({ [pane.chipsEl === c ? 'chips' : pane.addButton === c ? 'add' : pane.moreButton === c ? 'more' : pane.addMenu === c ? 'addMenu' : pane.paneMenu === c ? 'paneMenu' : 'editor']: 1 }))
    .map((o) => Object.keys(o)[0]);
  assert.deepEqual(names(), ['chips', 'add', 'more', 'addMenu', 'paneMenu', 'editor'], 'one layer: the chip before the buttons');
  view.addLayer(pane);
  assert.deepEqual(names(), ['add', 'more', 'chips', 'addMenu', 'paneMenu', 'editor'], 'several: the chips after the buttons, before the popovers');
  view.addLayer(pane);
  assert.deepEqual(names(), ['add', 'more', 'chips', 'addMenu', 'paneMenu', 'editor']);
  view.removeLayer(pane.layers[2]);
  view.removeLayer(pane.layers[1]);
  assert.deepEqual(names(), ['chips', 'add', 'more', 'addMenu', 'paneMenu', 'editor'], 'one again: back among the buttons');
  // A pane built with several layers from the start (a header rebuilt) is in the same order.
  view.addLayer(pane);
  view._buildHead(pane);
  assert.deepEqual(names(), ['add', 'more', 'chips', 'addMenu', 'paneMenu', 'editor']);
});

test('the pane\'s state chip is the first thing in the chips\' element, whatever rebuilds them: nothing on the header\'s first line changes with the state', () => {
  const { view } = makeView(2);
  const pane = view.panes[0];
  const firstLine = () => pane.head.children.filter((c) => c !== pane.chipsEl && !pane.head.children.slice(-3).includes(c))
    .map((c) => c.className);
  const before = firstLine();
  // A chip that is there or not, and says one word or another, is inside the element that takes the width left
  // on the line (css: a basis of 0): the buttons beside it stay where they are.
  assert.ok(pane.chipsEl.children[0] === pane.chip, 'first, before the layers\' chips');
  assert.ok(!pane.head.children.includes(pane.chip), 'and not an item of the header');
  for (const word of ['stopped', 'failed', 'stale', 'queued']) {
    const layer = pane.layers[0];
    layer.stopped = word === 'stopped';
    layer.error = word === 'failed' ? { kind: 'build', error: new Error('x') } : null;
    layer.mesh = word === 'stale' ? { n_faces: 1 } : null;
    layer.task = word === 'queued' ? { phase: 'queued' } : null;
    view._renderState(pane);
    assert.equal(pane.chip.hidden, false, word);
    assert.deepEqual(firstLine(), before, `the header's own items do not change when the pane is ${word}`);
  }
  // Rebuilt by anything (a layer added, moved, the annotations on, the header built again): still first.
  pane.layers[0].task = null; pane.layers[0].mesh = null; pane.layers[0].error = null; pane.layers[0].stopped = false;
  view.addLayer(pane);
  view.setAnnotations(pane, true);
  view.moveLayer(pane.layers[0], 1);
  assert.ok(pane.chipsEl.children[0] === pane.chip);
  view._buildHead(pane);
  assert.ok(pane.chipsEl.children[0] === pane.chip);
  assert.equal(pane.chipsEl.children.filter((c) => c === pane.chip).length, 1, 'once');
  assert.ok(pane.chip.hidden, 'idle: no word');
});

test('a failed build leaves no rings: the pane shows nothing of a load it could not build, and the next build draws them', async () => {
  resetServer({ nodes: [] });
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.setAnnotations(pane, true);
  await view.build(record);
  await until(() => calls(pane.renderer, 'setDiscs').length === 1, 'the rings');
  assert.equal(pane.annotationsDrawn, 'i:1#1');
  const before = calls(pane.renderer, 'removeDiscs').length;
  // The cell is loaded again; every build now fails.
  setLoaded('i:1', 2);
  server.failBuild = () => true;
  await view.build({ ...record, loadGen: 2 });
  assert.equal(pane.layers[0].state, 'error-build');
  assert.equal(calls(pane.renderer, 'removeDiscs').length, before + 1, 'the rings of the earlier load were taken away');
  assert.equal(pane.annotationsDrawn, null);
  // A build that works draws them again.
  server.failBuild = null;
  pane.card.retryButton.click();
  await idle(view);
  await until(() => calls(pane.renderer, 'setDiscs').length === 2, 'the rings of the new load');
  assert.equal(pane.annotationsDrawn, 'i:1#2');
  // Another layer that holds a surface keeps them: only a pane with none is emptied.
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  server.failBuild = (o) => o.pathway === 'original';
  const discs = calls(pane.renderer, 'removeDiscs').length;
  await view.build({ ...record, loadGen: 2 });
  assert.equal(pane.layers[1].state, 'error-build');
  assert.equal(calls(pane.renderer, 'removeDiscs').length, discs, 'the first layer\'s surface is still there');
  assert.equal(pane.annotationsDrawn, 'i:1#2');
});

test('putting the grid back after a Compare that ran with a pane maximized keeps the camera the user arranged', async () => {
  resetServer();
  const { view, statuses } = makeView(3);
  view.toggleMaximize(view.panes[0]);                       // panes 2 and 3 are out of sight
  assert.match(statuses.at(-1)[0], /fills the grid/);
  await view.build(record);
  const [front, second, third] = view.panes.map((p) => p.renderer);
  assert.ok(front.realFramed, 'the pane in front was framed against its real size');
  assert.ok(!second.realFramed && !third.realFramed, 'the others were framed against no size at all');
  // Who copied whose camera: [into, from].  The camera in front is the user's; the others follow it.
  const log = [];
  [['front', front], ['second', second], ['third', third]].forEach(([name, r]) => {
    r.camera.position.id = name;
    r.camera.position.copy = (from) => { log.push([name, from.id]); };
  });
  view.toggleMaximize(null);
  // The browser's own resize observers tell the panes that now have a size, a frame later.
  second.updateSize();
  third.updateSize();
  assert.ok(!log.some(([into]) => into === 'front'), 'no late framing was broadcast over the camera in front');
  assert.deepEqual([...new Set(log.map(([into]) => into))].sort(), ['second', 'third'], 'the others took a camera');
  assert.ok(log.every(([, from]) => from === 'front'), `and every one of them the one in front: ${JSON.stringify(log)}`);
  assert.ok(second.realFramed && third.realFramed, 'and they are framed against their real size');
  // With Sync off the panes keep their own cameras: nothing is copied anywhere.
  view.toggleMaximize(view.panes[1]);
  view.setSync(false);
  let copied = 0;
  for (const p of view.panes) p.renderer.camera.position.copy = () => { copied += 1; };
  view.toggleMaximize(null);
  assert.equal(copied, 0);
  // A pane in front that never drew anything has no camera worth copying: the others frame themselves, and the
  // first of them to have done so gives its camera to the rest (the pane in front included), so that with
  // Sync on they still look the same way, as the broadcast of the first framing used to make them.
  resetServer({ failBuild: (o) => o.pathway === 'voxel2' });
  const empty = makeView(3);
  empty.view.toggleMaximize(empty.view.panes[0]);
  await empty.view.build(record);
  const [none, one, two] = empty.view.panes.map((p) => p.renderer);
  assert.equal(empty.view.panes[0].state, 'error-build');
  assert.ok(!one.realFramed && !two.realFramed);
  const taken = [];
  [none, one, two].forEach((r) => { r.camera.position.copy = () => { taken.push(r); }; });
  empty.view.toggleMaximize(null);
  assert.ok(one.realFramed && two.realFramed, 'they framed themselves against their real size');
  assert.deepEqual(taken.filter((r) => r === one), [], 'the first framed pane is the one that gives');
  assert.deepEqual(taken, [none, two], 'and the others take its camera');
  // With Sync off none of this happens.
  const apart = makeView(3);
  apart.view.setSync(false);
  apart.view.toggleMaximize(apart.view.panes[0]);
  await apart.view.build(record);
  let moved = 0;
  for (const p of apart.view.panes) p.renderer.camera.position.copy = () => { moved += 1; };
  apart.view.toggleMaximize(null);
  assert.equal(moved, 0, 'nothing is copied with Sync off');
});

test('putting the grid back says how the panes stand, not that one of them fills it', async () => {
  resetServer();
  const { view, statuses } = makeView(2);
  await view.build(record);
  view.toggleMaximize(view.panes[1]);
  assert.match(statuses.at(-1)[0], /^Pane 2 fills the grid/);
  view.toggleMaximize(null);
  assert.doesNotMatch(statuses.at(-1)[0], /fills the grid/);
  assert.equal(statuses.at(-1)[0], '2 of 2 panes ready.');
});

test('a popover that would run off the bottom of the window goes above its header if it fits there, else is cut short', () => {
  const had = globalThis.window;
  globalThis.window = { innerWidth: 1400, innerHeight: 600, addEventListener() {} };
  try {
    const { view } = makeView(1);
    const pane = view.panes[0];
    const node = pane.editor;
    const anchor = pane.layers[0].chip.name;
    const rect = (top, height, left = 100, width = 270) => ({ left, top, width, height, bottom: top + height, right: left + width });
    let box;
    node.getBoundingClientRect = () => box;
    anchor.getBoundingClientRect = () => rect(0, 20);
    // It fits under the header: the stylesheet's own place, nothing set.
    pane.head.getBoundingClientRect = () => rect(0, 30, 0, 700);
    box = rect(32, 268);
    view._placePop(pane, node, anchor);
    assert.deepEqual([node.style.top, node.style.bottom, node.style.maxHeight], ['', '', '']);
    // It does not, and there is room above the header for it.
    pane.head.getBoundingClientRect = () => rect(330, 30, 0, 700);
    box = rect(362, 268);
    view._placePop(pane, node, anchor);
    assert.equal(node.style.top, 'auto');
    assert.equal(node.style.bottom, 'calc(100% + 2px)');
    // Not above either: cut short and scrollable, to the bottom of the window.
    pane.head.getBoundingClientRect = () => rect(100, 30, 0, 700);
    box = rect(132, 700);
    view._placePop(pane, node, anchor);
    assert.equal(node.style.maxHeight, `${600 - 8 - 132}px`);
    assert.equal(node.style.overflowY, 'auto');
    // Hardly any room under the header and none above it: it keeps a floor of 120 px, whatever is left.
    pane.head.getBoundingClientRect = () => rect(100, 30, 0, 700);
    box = rect(560, 700);
    view._placePop(pane, node, anchor);
    assert.equal(node.style.maxHeight, '120px');
    // And it opens afresh the next time: the last placement is not kept.
    pane.head.getBoundingClientRect = () => rect(0, 30, 0, 700);
    box = rect(32, 268);
    view._placePop(pane, node, anchor);
    assert.deepEqual([node.style.top, node.style.bottom, node.style.maxHeight, node.style.overflowY], ['', '', '', '']);
  } finally {
    if (had) globalThis.window = had; else delete globalThis.window;
  }
});

// -- the chips' fit, banners that come back, and the last gaps found by the reviewers ------------------------------

test('the chips\' fit: the pane\'s state is a dot where its word has no room, a pane is "tight" where the chips at their narrowest overflow, and a state changes neither', () => {
  const { view } = makeView(2);
  const pane = view.panes[0];
  const size = (clientWidth, scrollWidth) => { pane.chipsEl.clientWidth = clientWidth; pane.chipsEl.scrollWidth = scrollWidth; view._fitChips(pane); };
  const dot = () => pane.chip.classList.contains('dot');
  const tight = () => pane.el.classList.contains('tight');
  size(0, 0);                                    // no layout (out of sight): nothing is decided
  assert.ok(!dot() && !tight());
  size(300, 300);                                // room for the word, nothing overflows
  assert.ok(!dot() && !tight());
  size(180, 180);                                // under 215 px the word is a dot
  assert.ok(dot() && !tight());
  size(180, 260);                                // the chips, at their narrowest, overflow: dots and arrows
  assert.ok(dot() && tight());
  size(400, 400);                                // room again: both go
  assert.ok(!dot() && !tight());
  // Several layers: each layer's chip says its own word, and the pane's is always a dot.
  view.addLayer(pane);
  size(400, 400);
  assert.ok(dot());
  // A narrow pane is not measured for `tight` (its chips are compact already); its chip is a dot.
  pane.el.classList.add('narrow');
  size(100, 400);
  assert.ok(dot() && !tight());
  // A state only rewrites the chip's class: the dot stays.
  pane.layers[0].stopped = true;
  view._renderState(pane);
  assert.ok(dot() && pane.chip.classList.contains('stopped'));
});

test('a narrow pane\'s chips take a line of their own under the buttons, in the page\'s order too', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  const at = (node) => pane.head.children.indexOf(node);
  assert.ok(at(pane.chipsEl) < at(pane.addButton), 'one layer in a wide pane: beside the buttons');
  pane.el.clientWidth = 200;                     // what the size observer reports
  view._sized(pane);
  assert.ok(pane.el.classList.contains('narrow'));
  assert.ok(at(pane.chipsEl) > at(pane.moreButton), 'in a narrow one: after them');
  pane.el.clientWidth = 400;
  view._sized(pane);
  assert.ok(!pane.el.classList.contains('narrow'));
  assert.ok(at(pane.chipsEl) < at(pane.addButton), 'and back beside them');
  // The size observer's callback also fits the chips to the line they have now.
  pane.chipsEl.clientWidth = 100;
  pane.chipsEl.scrollWidth = 300;
  view._sized(pane);
  assert.ok(pane.el.classList.contains('tight') && pane.chip.classList.contains('dot'));
  // So does any rebuild of the chips (a layer added, the annotations on): the line is measured again.
  pane.chipsEl.clientWidth = 400;
  pane.chipsEl.scrollWidth = 400;
  view.setAnnotations(pane, true);
  assert.ok(!pane.el.classList.contains('tight') && !pane.chip.classList.contains('dot'), 'room again: both are gone');
});

test('a state chip\'s tooltip says its word: where the chip is a dot that is all there is of it', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  const layer = pane.layers[0];
  const cases = [
    ['waiting', () => { layer.task = { phase: 'queued' }; }],
    ['out of date', () => { layer.mesh = { n_faces: 1 }; }],
    ['stopped', () => { layer.stopped = true; }],
    ['failed', () => { layer.error = { kind: 'build', error: new Error('x') }; }],
  ];
  for (const [word, set] of cases) {
    layer.task = null; layer.mesh = null; layer.stopped = false; layer.error = null;
    set();
    view._renderState(pane);
    assert.equal(pane.chip.textContent, word);
    assert.ok(pane.chip.dataset.help.toLowerCase().includes(word), `${word}: ${pane.chip.dataset.help}`);
  }
});

const failedAt = (what) => ({ kind: 'measure', error: new Error(what), shown: { what, note: '', retry: null } });

test('a failed layer\'s banner comes back whenever what covered it goes: work dropped, a layer removed, the annotations banner dismissed', async () => {
  // (a) The error box unticked while a layer rebuilds for it: the rebuild is dropped and its card with it.
  resetServer({ failBuild: (o) => o.pathway === 'original' && o.method === 'cylinder' });
  let made = makeView(1);
  let { view, statuses } = made;
  let pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  await view.build(record);
  assert.ok(pane.bannerLayer === pane.layers[1]);
  server.holdBuilds = true;
  pickTech(view, 0, 0, 'tube');                  // layer 1 is out of date
  tick(view, 0);                                 // error on: it rebuilds, then measures
  await until(() => pane.layers[0].state === 'building', 'layer 1 building');
  assert.equal(banner(pane).hidden, true, 'the card has the place while it works');
  tick(view, 0);                                 // error off: the rebuild that was only for measuring is dropped
  assert.equal(banner(pane).hidden, false, 'at once, not when the dropped job notices');
  assert.ok(pane.bannerLayer === pane.layers[1]);
  await idle(view);
  server.holdBuilds = false;
  await settle(30);
  assert.equal(banner(pane).hidden, false);
  assert.match(statuses.at(-1)[0], /red banner/);

  // (b) A layer that is building is removed while another is failed.
  resetServer({ failMeasure: true });
  made = makeView(1);
  ({ view, statuses } = made);
  pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  pane.editing = 1; view._fillEditor(pane);
  tick(view, 0);                                 // layer 2: error ticked, and its measurement will fail
  await view.build(record);
  assert.ok(pane.bannerLayer === pane.layers[1]);
  view.addLayer(pane);
  pickTech(view, 0, 2, 'tube');
  server.holdBuilds = true;
  const run = view.build(record);
  await until(() => pane.layers[2].state === 'building', 'layer 3 building');
  assert.equal(banner(pane).hidden, true);
  view.removeLayer(pane.layers[2]);
  assert.equal(banner(pane).hidden, false, 'layer 2\'s banner is back as the card goes');
  assert.ok(pane.bannerLayer === pane.layers[1]);
  await idle(view);
  server.holdBuilds = false;
  await run;

  // (c) The layer whose banner is up is removed while another is failed.
  resetServer({ failBuild: (o) => o.pathway === 'original' || o.pathway === 'wrap' });
  made = makeView(1);
  view = made.view;
  pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  view.addLayer(pane);
  pickTech(view, 0, 2, 'wrap');
  await view.build(record);
  assert.ok(pane.bannerLayer === pane.layers[2]);
  const second = pane.layers[1];
  view.removeLayer(pane.layers[2]);
  assert.ok(pane.bannerLayer === second, 'the other failed layer has the banner now');
  assert.equal(banner(pane).hidden, false);

  // (d) The annotations banner is dismissed over a failed layer.
  resetServer({ failBuild: (o) => o.pathway === 'original' && o.method === 'cylinder', failNodes: true });
  made = makeView(1);
  ({ view, statuses } = made);
  pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  await view.build(record);
  assert.ok(pane.bannerLayer === pane.layers[1]);
  view.setAnnotations(pane, true);
  await until(() => /annotations could not be loaded/.test(banner(pane).textContent), 'the annotations banner');
  pane.card.dismissButton.click();
  assert.equal(banner(pane).hidden, false, 'the failed layer\'s banner is back');
  assert.match(banner(pane).textContent, /Pane 1 layer 2/);
  assert.ok(pane.bannerLayer === pane.layers[1]);
});

test('a failure is not shown over work or over a banner that is up, and the first failed layer has the place', async () => {
  resetServer();
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  view.addLayer(pane);
  pickTech(view, 0, 2, 'wrap');
  await view.build(record);                      // all three are built, and current
  // Layers 2 and 3 failed (measurements: a Compare does not clear those, and these are not rebuilt by it).
  pane.layers[1].error = failedAt('second');
  pane.layers[2].error = failedAt('third');
  view._refresh();
  assert.ok(pane.bannerLayer === pane.layers[1], 'nothing at work: the first failed layer has the banner');
  assert.match(banner(pane).textContent, /Pane 1 layer 2: second/);
  // Layer 1 is changed and rebuilt: the card takes the place, and nothing is put over it.
  server.holdBuilds = true;
  pickTech(view, 0, 0, 'tube');
  const run = view.build(record);
  await until(() => pane.layers[0].state === 'building', 'the first layer');
  assert.equal(pane.card.running, true);
  assert.equal(banner(pane).hidden, true, 'the card has the place');
  view._reshowFailure(pane);
  assert.equal(pane.card.running, true, 'a failure is not shown over a card');
  assert.equal(banner(pane).hidden, true);
  pane.card.hide();                              // the card is down for a moment, but the layer still has its work
  view._reshowFailure(pane);
  assert.equal(banner(pane).hidden, true, 'nor over work that has no card at the moment');
  server.holdBuilds = false;
  await run;
  assert.ok(pane.bannerLayer === pane.layers[1], 'the work is over: the first failed layer again');
  // A banner that is up is not replaced by another, or by the same again.
  const text = banner(pane).textContent;
  view._reshowFailure(pane);
  assert.equal(banner(pane).textContent, text);
  pane.bannerLayer = null;                       // a banner of another kind: the annotations'
  pane.card.fail({ title: 'Pane 1: the annotations could not be loaded', error: new Error('x') });
  view._reshowFailure(pane);
  assert.match(banner(pane).textContent, /annotations could not be loaded/);
  // A card that is up with no layer at work (one has just been dropped) is not taken down either.
  pane.card.show({ title: 'working' });
  view._reshowFailure(pane);
  assert.equal(pane.card.running, true);
});

test('a re-shown banner names the layer by the place it has now', async () => {
  resetServer({ failMeasure: true });
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.addLayer(pane);
  pickTech(view, 0, 1, 'original');
  pane.editing = 1; view._fillEditor(pane);
  tick(view, 0);
  await view.build(record);
  const failed = pane.layers[1];
  assert.match(banner(pane).textContent, /Pane 1 layer 2: the error map could not be measured/);
  view.moveLayer(failed, -1);                    // it is the first layer now
  pane.card.clearError();
  pane.bannerLayer = null;
  view._reshowFailure(pane);
  assert.match(banner(pane).textContent, /Pane 1 layer 1: the error map could not be measured/);
  assert.match(banner(pane).textContent, /The surface is unchanged\./);
});

test('a failed build on the same load keeps the rings that are still true; those of another load go', async () => {
  resetServer({ nodes: [] });
  const { view } = makeView(1);
  const pane = view.panes[0];
  view.setAnnotations(pane, true);
  await view.build(record);
  await until(() => calls(pane.renderer, 'setDiscs').length === 1, 'the rings');
  const before = calls(pane.renderer, 'removeDiscs').length;
  server.failBuild = (o) => o.pathway === 'original';
  pickTech(view, 0, 0, 'original');
  await view.build(record);                      // the same load
  assert.equal(pane.layers[0].state, 'error-build');
  assert.equal(calls(pane.renderer, 'removeDiscs').length, before, 'the rings of this load stay');
  assert.equal(pane.annotationsDrawn, 'i:1#1');
  // The cell is loaded again: its rings of the load before are yesterday's, and go with the failure.
  setLoaded('i:1', 2);
  await view.build({ ...record, loadGen: 2 });
  assert.equal(calls(pane.renderer, 'removeDiscs').length, before + 1);
  assert.equal(pane.annotationsDrawn, null);
});

test('putting the grid back while work runs says a Compare is under way, not that one pane fills the grid', async () => {
  resetServer({ holdBuilds: true });
  const { view, statuses } = makeView(2);
  const run = view.build(record);
  await until(() => view.panes[0].layers[0].state === 'building', 'building');
  view.toggleMaximize(view.panes[1]);
  assert.match(statuses.at(-1)[0], /^Pane 2 fills the grid/);
  view.toggleMaximize(null);
  assert.match(statuses.at(-1)[0], /^Comparing c1 · NeitzInferiorMonkey — drag any pane to orbit all of them\.$/);
  server.holdBuilds = false;
  await run;
});

test('Reset view and Save picture give the keyboard back to the tools button, as the other menu items do', () => {
  const { view } = makeView(1);
  const pane = view.panes[0];
  const [reset, snap] = pane.paneMenu.children;
  for (const item of [reset, snap]) {
    view._togglePop(pane, 'more', { keyboard: true });
    item.focus();
    item.fire('click');
    assert.ok(globalThis.document.activeElement === pane.moreButton);
    assert.equal(pane.paneMenu.hidden, true);
  }
});

// -- settings that will not build as set -------------------------------------------------

/** A whole-cell field estimate: a typed Fine voxel of 0.03 um that a Face cap of 400,000 holds to 0.0763
 * (RPC1 c430 under Voxel remesh, as the server answered on 2026-10-20). */
const heldField = () => ({
  applies: false, peak_mb: 1288,
  field: {
    applies: true, pathway: 'voxel2', typed: true, typed_um: 0.03, asked_um: 0.03, floor_um: 0.02,
    voxel_um: 0.0763, coarsened: true, reason: 'faces', voxel2_faces: 400000, raw_budget: 1_200_000,
    work_budget: 60_000_000,
    cap_for_asked: { voxel2_faces: 2_960_000, raw_triangles: 7_764_204, triangles: 2_960_000, peak_mb: 9493 },
    uncapped: { voxel_um: 0.03, triangles: 7_764_204, peak_mb: 3765 },
    finest_um: 0.0297,
  },
});
/** A field estimate that builds as typed. */
const fitsField = () => ({ applies: false, field: { applies: true, typed: true, coarsened: false } });
/** A Closed curve estimate past the server's memory at 0.006 um, Join bodies included. */
const overCurve = () => ({
  applies: true, method: 'closedcurve', pixel_size_um: 0.006, voxels: 1e7, faces: 8e6, triangles: 1.5e7,
  peak_mb: 9000, serve_mb: 4500, server_mb: 13500, json_mb: 540, tab_mb: 900, seconds: 240,
  over: ['memory', 'page'], finest_um: 0.011, binding: 'memory',
  alternatives: [{ off: 'joins', set: { curve_tie_um: 0 }, finest_um: 0.009, fits: false }],
  limits: { voxels: 3e8, faces: 1e7, memory_mb: 9000, json_mb: 500 },
  basis: { kind: 'built', pixel_um: 0.1 }, approximate: false,
});
const fitsCurve = (options) => ({
  applies: true, method: 'closedcurve', pixel_size_um: options.pixel_size_um, voxels: 1e5, faces: 1e5,
  triangles: 2e5, peak_mb: 200, serve_mb: 100, server_mb: 300, json_mb: 10, tab_mb: 20, seconds: 4, over: [],
  limits: { voxels: 3e8, faces: 1e7, memory_mb: 9000, json_mb: 500 }, basis: { kind: 'built', pixel_um: 0.1 },
  approximate: false,
});
/** The pane options panel's row and the fields it marks. */
const conflictRow = (view) => view.optionsPanel.conflicts.box;
const conflictMarked = (view) => [...view.optionsPanel.conflicts.marked].sort();
const fixButtons = (view) => conflictRow(view).find((c) => c.tagName === 'button');

async function conflictView(answer) {
  const { EstimateCache } = await mod('renderestimate.js');
  const asked = [];
  const estimates = new EstimateCache(async (rec, options) => {
    asked.push(options);
    return answer(options);
  }, { delay: 0 });
  const cell = { ...record, summary: { n_curve_annotations: 0, n_disc_annotations: 400, n_nodes: 400, type_id: 1 } };
  const made = makeOptionsView(2, { estimates, cell: () => cell });
  return { ...made, asked, cell };
}

test('a pane\'s options mark a typed Fine voxel the Face cap holds coarser, and a fix changes that layer only', async () => {
  resetServer();
  const { view, asked } = await conflictView((o) => (o.voxel2_um === 0.03 && o.voxel2_faces === 400000
    ? heldField() : fitsField()));
  setTech(view, 0, 'voxel2');
  setTech(view, 1, 'voxel2');
  press(view, 0);
  await until(() => asked.length, 'the first estimate');
  await settle();
  assert.equal(conflictRow(view).hidden, true, 'the automatic Fine voxel builds as set');
  assert.ok(!conflictRow(view).id, 'the 3D render panel has the row\'s id');
  edit(view, 'voxel2_um', 0.03, 'change');
  await until(() => !conflictRow(view).hidden, 'the row');
  assert.deepEqual(conflictMarked(view), ['voxel2_faces', 'voxel2_um']);
  assert.ok(label(view, 'voxel2_um').classList.contains('conflict'));
  assert.equal(control(view, 'voxel2_um').attrs.get('aria-invalid'), 'true');
  assert.match(conflictRow(view).textContent, /Fine voxel 0\.03 µm will be built coarser for c1 · NeitzInferiorMonkey/);
  const fixes = fixButtons(view);
  assert.deepEqual(fixes.map((b) => b.textContent),
    ['Face cap 2,960,000', 'Face cap 0 (keep every triangle)', 'Fine voxel 0.0763 µm']);
  assert.ok(fixes[0].className.includes('best'));
  const other = { ...view.panes[1].layers[0].config.options };
  fixes[0].click();
  const layer = view.panes[0].layers[0].config.options;
  assert.deepEqual([layer.voxel2_um, layer.voxel2_faces], [0.03, 2_960_000], 'the fix is in the layer');
  assert.equal(shown(view, 'voxel2_faces'), '2960000', 'and in its box');
  assert.deepEqual(view.panes[1].layers[0].config.options, other, 'the other pane is not touched');
  assert.equal(meshJobs().length, 0, 'nothing is built until Compare');
  await until(() => conflictRow(view).hidden, 'the row to go');
  assert.deepEqual(conflictMarked(view), []);
  assert.ok(!label(view, 'voxel2_um').classList.contains('conflict'));
  assert.equal(control(view, 'voxel2_um').attrs.has('aria-invalid'), false);
  // The other pane's panel has its own settings, which build as set.
  press(view, 1);
  await settle();
  assert.equal(conflictRow(view).hidden, true);
});

test('a pane\'s Closed curve size past the server\'s memory marks Voxel and Join bodies; a fix sets both', async () => {
  resetServer();
  const { view } = await conflictView((o) => (o.method === 'closedcurve' && o.pixel_size_um === 0.006
    ? overCurve() : fitsCurve(o)));
  setTech(view, 0, 'closedcurve');
  press(view, 0);
  edit(view, 'pixel_size_um', 0.006, 'change');
  await until(() => !conflictRow(view).hidden, 'the row');
  assert.deepEqual(conflictMarked(view), ['curve_tie_um', 'pixel_size_um']);
  assert.deepEqual(fixButtons(view).map((b) => b.textContent), ['Voxel 0.011 µm', 'Join bodies off, Voxel 0.009 µm']);
  assert.match(view.optionsPanel.chip.textContent, /too fine/, 'the chip says it too');
  fixButtons(view)[1].click();
  const { config } = view.panes[0].layers[0];
  assert.deepEqual([config.options.pixel_size_um, config.options.curve_tie_um], [0.009, 0]);
  assert.equal(config.pixelChosen, true, 'the size picked is the user\'s: Compare builds it as set');
  await until(() => conflictRow(view).hidden, 'the row to go');
  // A method the estimates do not speak for marks nothing.
  setTech(view, 0, 'original');
  await settle();
  assert.equal(conflictRow(view).hidden, true);
  assert.deepEqual(conflictMarked(view), []);
});

test('a pane with no estimates has no row', () => {
  const { view } = makeOptionsView(2);
  setTech(view, 0, 'voxel2');
  press(view, 0);
  assert.equal(conflictRow(view).hidden, true);
});

// -- the view cube ------------------------------------------------------------

test('the view cube sits in the pane at the grid\'s top right, whatever the count, and in a pane that fills it', () => {
  const { view } = makeView(4);
  view.setViewCube(true);
  const cube = view.viewCube;
  const host = () => view.panes.findIndex((p) => cube.root.parentElement === p.viewport);
  const marked = () => view.panes.filter((p) => p.viewport.classList.contains('has-viewcube')).map((p) => p.index);
  assert.equal(host(), 1, 'two by two: the second pane');
  assert.deepEqual(marked(), [1], 'its legend moves aside; no other pane is marked');
  assert.equal(cube.root.hidden, false);
  for (const [count, want] of [[3, 2], [6, 2], [5, 2], [2, 1], [1, 0], [4, 1]]) {
    view.setPaneCount(count);
    assert.equal(host(), want, `${count} panes`);
    assert.deepEqual(marked(), [want], `${count} panes: one pane marked`);
  }
  view.toggleMaximize(view.panes[3]);
  assert.equal(host(), 3, 'the pane that fills the grid holds it');
  view.toggleMaximize(view.panes[3]);
  assert.equal(host(), 1, 'and the grid back puts it back');
  view.setViewCube(false);
  assert.equal(cube.root.hidden, true);
  assert.deepEqual(marked(), [], 'hidden, no pane makes room for it');
});

test('with Sync cameras the cube turns the pane it shows and Sync carries the rest; without, every drawn pane turns', async () => {
  const { view } = makeView(3);
  view.setViewCube(true);
  const r = view.panes.map((p) => p.renderer);
  r[0].meshes.set('L', { n_faces: 1 });
  r[2].meshes.set('L', { n_faces: 1 });
  const asked = (renderer, kind) => renderer.calls.filter((c) => c[0] === kind);
  view.focus = 2;
  const turned = await view.viewCube.view.turnTo([0, 0, 1], [0, 1, 0]);
  assert.equal(turned, true);
  assert.deepEqual(r.map((x) => asked(x, 'turnTo').length), [0, 0, 1], 'synced: the focused pane alone turns');
  // ... and its arrival is copied into the others at the end, whatever the controls called a change.
  const copied = [];
  const copy = view._copyCamera.bind(view);
  view._copyCamera = (from, to) => { copied.push([from.index, to.index]); copy(from, to); };
  await view.viewCube.view.turnTo([0, 0, -1], [0, 1, 0]);
  assert.deepEqual(copied, [[2, 0], [2, 1]], 'the arrival is copied from the shown pane into the rest');
  view._copyCamera = copy;
  view.focus = 1;   // nothing drawn there: the first pane that has a surface is shown
  assert.equal(view._cubeLead(), view.panes[0]);
  view.viewCube.view.spinBy(0.1, 0);
  assert.deepEqual(r.map((x) => asked(x, 'spinBy').length), [1, 0, 0]);
  view.setSync(false);
  await view.viewCube.view.turnTo([1, 0, 0], [0, 0, 1]);
  assert.deepEqual(r.map((x) => asked(x, 'turnTo').length), [1, 0, 3], 'unsynced: every pane with a surface turns');
  assert.deepEqual(asked(r[0], 'turnTo')[0].slice(1), [[1, 0, 0], [0, 0, 1]], 'to the same direction and up');
  view.viewCube.view.spinBy(0.1, -0.2);
  view.viewCube.view.endSpin();
  assert.deepEqual(r.map((x) => asked(x, 'spinBy').length), [2, 0, 1]);
  assert.deepEqual(r.map((x) => asked(x, 'endSpin').length), [1, 0, 1]);
});

test('the cube\'s house resets the shown pane with Sync, every pane without; the cube reads the shown pane\'s Flip Z', () => {
  const { view } = makeView(2);
  view.setViewCube(true);
  const r = view.panes.map((p) => p.renderer);
  r[0].meshes.set('L', { n_faces: 1 });
  r[1].meshes.set('L', { n_faces: 1 });
  const before = r.map((x) => x.framed);
  view.focus = 1;
  view.viewCube.home.fire('click');
  assert.deepEqual(r.map((x, i) => x.framed - before[i]), [0, 1], 'synced: the focused pane is framed, Sync copies it');
  view.setSync(false);
  view.viewCube.home.fire('click');
  assert.deepEqual(r.map((x, i) => x.framed - before[i]), [1, 2], 'unsynced: each pane frames itself');
  r[1].flipZ = true;
  assert.equal(view.viewCube.view.flipZ, true);
  view.focus = 0;
  assert.equal(view.viewCube.view.flipZ, false);
});


// -- the pivot point ------------------------------------------------------------------------

test('pivot: with Sync cameras one point serves every pane, set in any and let go in all; the label says so', () => {
  const { view, statuses } = makeView(3);
  view.setViewCube(true);
  const r = view.panes.map((p) => p.renderer);
  for (const x of r) {
    x.meshes.set('L', { n_faces: 1 });
    x.resetView();          // framed, as a built pane is
  }
  assert.equal(view.pivotChip, null, 'no label until a pivot is set');
  r[0].setPivot([1, 2, 3]);
  assert.deepEqual(r.map((x) => x.pivotPoint()), [[1, 2, 3], [1, 2, 3], [1, 2, 3]], 'every pane turns about it');
  const chip = view.pivotChip;
  assert.equal(chip.hidden, false);
  assert.equal(chip.parentElement, view.panes[2].viewport, 'the label is with the cube, in the top-right pane');
  assert.equal(chip.label.textContent, 'Rotating about a point');
  assert.match(statuses.at(-1)[0], /^Every pane rotates about the ringed point/);
  r[2].clearPivot('left');
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, null, null], 'let go in one pane, let go in all');
  assert.equal(chip.hidden, true);
  assert.match(statuses.at(-1)[0], /the point left the picture$/);
  r[1].setPivot([4, 5, 6]);
  view.toggleMaximize(view.panes[0]);
  assert.equal(chip.parentElement, view.panes[0].viewport, 'a pane that fills the grid takes the label');
  view.toggleMaximize(view.panes[0]);
  assert.deepEqual(r.map((x) => x.pivotPoint()), [[4, 5, 6], [4, 5, 6], [4, 5, 6]], 'and putting the grid back keeps it');
  chip.fire('click');
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, null, null], 'a click on the label lets go of it');
});

test('pivot: Reset view in one synced pane, or the cube\'s house, lets go of every pane\'s', () => {
  const { view } = makeView(2);
  view.setViewCube(true);
  const r = view.panes.map((p) => p.renderer);
  for (const x of r) x.meshes.set('L', { n_faces: 1 });
  r[1].setPivot([1, 2, 3]);
  r[0].resetView();
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, null]);
  r[1].setPivot([1, 2, 3]);
  view.viewCube.home.fire('click');
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, null]);
});

test('pivot: without Sync each pane keeps its own; Sync on gives every pane the first drawn pane\'s, or none', () => {
  const { view } = makeView(3);
  const r = view.panes.map((p) => p.renderer);
  for (const x of r) x.meshes.set('L', { n_faces: 1 });
  view.setSync(false);
  r[1].setPivot([1, 1, 1]);
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, [1, 1, 1], null], 'set in one pane, only that pane');
  assert.equal(view.pivotChip.label.textContent, 'Pane 2 rotates about a point');
  r[2].setPivot([2, 2, 2]);
  assert.equal(view.pivotChip.label.textContent, 'Panes 2 and 3 rotate about points');
  r[2].clearPivot('off');
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, [1, 1, 1], null], 'let go in one pane, kept in the other');
  for (const x of r) x.setPivot([3, 3, 3]);
  assert.equal(view.pivotChip.label.textContent, 'Rotating about a point', 'every drawn pane: said plainly');
  r[0].setPivot([7, 8, 9]);
  view.setSync(true);
  assert.deepEqual(r.map((x) => x.pivotPoint()), [[7, 8, 9], [7, 8, 9], [7, 8, 9]], 'one camera, one pivot: pane 1\'s');
  view.setSync(false);
  r[0].clearPivot('off');
  view.setSync(true);
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, null, null], 'pane 1 has none: no pane keeps one');
});

test('pivot: P over a pane snaps to the annotation of the copy each layer was built from, through the shared helper', async () => {
  resetServer({ nodes: [{ id: 7, xyz_um: [1, 2, 3], radius_um: 1 }] });
  const { view } = makeView(1);
  view.setAnnotations(view.panes[0], true);
  await view.build(record);
  await idle(view);
  const pane = view.panes[0];
  const r = pane.renderer;
  const key = pane.layers[0].key;
  pane.viewport.clientHeight = 600;     // 6 um of picture at the stub's 0.01 um a pixel: 0.1 um is near the middle
  r.under = { key, point: [1.1, 2, 3] };
  r.readout = { key };
  r.pointerAt = [10, 20];
  r.togglePivot();
  await until(() => r.pivotPoint(), 'the pivot');
  assert.deepEqual(r.pivotPoint(), [1, 2, 3], 'the middle of the annotation there');
  assert.deepEqual(r.calls.filter((c) => c[0] === 'nearestAnnotation').at(-1)[1], [[key, 1], ['A', 1]],
    'the layer and the overlay, each with the copy\'s annotations');
  assert.deepEqual(r.calls.filter((c) => c[0] === 'setPivot').at(-1)[2], null, 'on no layer in particular');
  r.togglePivot();
  assert.equal(r.pivotPoint(), null, 'P again lets go');
  r.under = null;
  r.readout = null;
  assert.equal(await view.pivotAt(0, 10, 20), false, 'nothing there: no pivot');
  assert.equal(r.pivotPoint(), null);
});

test('pivot: a pane framed after the pivot was set takes Sync\'s pivot; P with the keyboard elsewhere goes to the pane under the pointer', async () => {
  resetServer();
  const { view } = makeView(2);
  await view.build(record);
  await idle(view);
  const r = view.panes.map((p) => p.renderer);
  r[0].setPivot([5, 5, 5]);
  // Pane 2 frames itself again for a new load of the cell, after pane 1.
  view.panes[1].framedFor = null;
  view._frame(view.panes[1]);
  assert.deepEqual(r.map((x) => x.pivotPoint()), [[5, 5, 5], [5, 5, 5]], 'its framing lets go of no pivot');
  view.pivotKey();
  assert.deepEqual(r.map((x) => x.pivotPoint()), [null, null], 'the pointer on no pane: P lets go of every pivot');
  r[1].pointerAt = [3, 4];
  r[1].under = { key: view.panes[1].layers[0].key, point: [9, 9, 9] };
  view.pivotKey();
  await until(() => r[1].pivotPoint(), 'the pivot');
  assert.deepEqual(r.map((x) => x.pivotPoint()), [[9, 9, 9], [9, 9, 9]]);
  assert.equal(view.pivotOn, true);
  assert.equal(view.clearPivots(), true);
  assert.equal(view.pivotOn, false);
});

// -- registrations: a layer built from another copy of the cell ----------------------------

// The compared cell as the tab loaded it: the current transforms with the residual field.
const regRecord = { ...record, summary: { id: 1, source: 'NeitzInferiorMonkey', copy_tag: 'neighbor.1' } };
const meshQueries = () => meshJobs().map(([, , q]) => q);
/** Put a layer on a registration through the select its editor shows. */
function pickRegistration(view, i, k, tag) {
  view.panes[i].editing = k;
  view._fillEditor(view.panes[i]);
  const select = view.panes[i].registrationSelect;
  select.value = tag;
  select.fire('change');
}

test('registration: a layer on another registration loads that copy once and builds from it, named by its tag', async () => {
  resetServer();
  const { view } = makeView(2);
  setTech(view, 1, 'voxel2');
  pickRegistration(view, 1, 0, 'neighbor.0');
  assert.deepEqual(L(view, 1).config.registration, { live: false, corrections: 'neighbor' });
  assert.equal(L(view, 1).chip.name.textContent, 'stored+field · Voxel remesh', 'the chip names the registration first');
  await view.build(regRecord);
  await idle(view);
  assert.deepEqual(server.copyLoads.map((b) => [b.live_registration, b.section_corrections, b.manual_moves]),
    [[false, 'neighbor', undefined]], 'one load of the copy, without the person\'s moves');
  const queries = meshQueries();
  assert.equal(queries.length, 2, 'the same options on two copies are two builds');
  assert.deepEqual(queries.map((q) => q.copy ?? null), [null, 'neighbor.0']);
  assert.equal(L(view, 1).builtCopy, 'neighbor.0');
  assert.equal(L(view, 0).builtCopy, null, 'the cell as loaded is the tab\'s own copy');
  assert.equal(view.panes[1].state, 'ready');
  // Built again with nothing changed: nothing is loaded or built.
  await view.build(regRecord);
  await idle(view);
  assert.equal(server.copyLoads.length, 1);
  assert.equal(meshJobs().length, 2);
});

test('registration: changing it makes the layer out of date; As loaded goes back to the tab\'s copy', async () => {
  resetServer();
  const { view } = makeView(1);
  await view.build(regRecord);
  await idle(view);
  assert.equal(view.panes[0].state, 'ready');
  pickRegistration(view, 0, 0, 'off.0');
  assert.equal(view.panes[0].state, 'stale', 'another registration is another surface');
  pickRegistration(view, 0, 0, '');
  assert.equal(L(view, 0).config.registration, null);
  assert.equal(view.panes[0].state, 'ready', 'back to what it was built from');
  const options = view.panes[0].registrationSelect.options.map((o) => [o.value, o.textContent]);
  assert.deepEqual(options[0], ['', 'As loaded (current + field)']);
  assert.equal(options.length, 5, 'as loaded, and every registration of a volume whose transforms are read');
  assert.ok(!options.some(([v]) => /curvefit|all/.test(v)), 'no mode Viking no longer runs');
  assert.ok(options.some(([v, t]) => v === 'neighbor.1' && t === 'Current transforms + Residual field'));
});

test('registration: the error map is measured against the layer\'s own copy, and a pane\'s rings are its first layer\'s', async () => {
  resetServer();
  const { view } = makeView(1);
  pickRegistration(view, 0, 0, 'off.1');
  view.setAnnotations(view.panes[0], true);
  tick(view, 0);
  await view.build(regRecord);
  await idle(view);
  const measured = posts('mesh-deviation-job');
  assert.equal(measured.length, 1);
  assert.equal(measured[0][3].copy, 'off.1', 'measured on the copy it was built from');
  const nodes = server.log.filter(([m, p]) => m === 'GET' && /\/nodes$/.test(p)).map(([, , , q]) => q.copy);
  assert.ok(nodes.length && nodes.every((c) => c === 'off.1'), `the rings are the copy's: ${nodes}`);
  assert.equal(view.panes[0].annotationsDrawn, 'i:1#1@off.1');
});

test('registration: a Difference across two copies holds each surface to its own (copy and copy_b)', async () => {
  resetServer();
  const { view } = makeView(3);
  setTech(view, 0, 'voxel2');
  setTech(view, 1, 'voxel2');
  pickRegistration(view, 1, 0, 'off.0');
  setTech(view, 2, 'diff');
  await view.build(regRecord);
  await idle(view);
  const diffs = posts('mesh-diff-job');
  assert.equal(diffs.length, 1);
  assert.equal(diffs[0][3].copy, 'neighbor.1', 'A on the tab\'s copy, named');
  assert.equal(diffs[0][2].copy_b, 'off.0', 'B on its own');
  assert.equal(view.panes[2].state, 'ready');
});

test('registration: a correction the copy did not get is said, not hidden', async () => {
  resetServer({ failCorrection: true });
  const { view, statuses } = makeView(1);
  pickRegistration(view, 0, 0, 'neighbor.1');
  await view.build(regRecord);
  await idle(view);
  const [last, kind] = statuses.at(-1);
  assert.match(last, /Pane 1: Residual field was not applied \(the corrections service did not answer\), so this layer is drawn without it\.$/,
    'the closing line keeps it');
  assert.equal(kind, 'error');
  assert.match(L(view, 0).chip.name.dataset.help, /Built residual field was not applied/);
});

test('registration: Registrations… copies the layer being edited once per registration, into panes or layers', async () => {
  resetServer();
  const { view } = makeView(4);
  setTech(view, 0, 'tube');
  view.focus = 0;
  const regs = [{ live: false, corrections: 'off' }, { live: true, corrections: 'off' },
    { live: true, corrections: 'neighbor' }, { live: false, corrections: 'neighbor' }];
  const done = view.sweepRegistrations(regs, { target: 'panes' });
  assert.deepEqual(done, { panes: 4, layers: 1 });
  assert.equal(view.panes.length, 4);
  assert.deepEqual(view.panes.map((p) => p.layers.length), [1, 1, 1, 1]);
  assert.deepEqual(view.panes.map((p) => p.layers[0].config.tech), ['tube', 'tube', 'tube', 'tube']);
  assert.deepEqual(view.panes.map((p) => registrationTagOf(p.layers[0].config.registration)),
    ['off.0', 'off.1', 'neighbor.1', 'neighbor.0']);
  assert.ok(view.panes.every((p) => p.layers[0].config.color === view.panes[0].layers[0].config.color),
    'one layer each, in the first color');
  // A sweep saved with the retired corrections: each is the residual field, once.
  const retired = view.sweepRegistrations([{ live: true, corrections: 'curvefit' }, { live: true, corrections: 'all' },
    { live: true, corrections: 'neighbor' }], { target: 'panes' });
  assert.deepEqual(retired, { panes: 1, layers: 1 });
  assert.deepEqual(registrationTagOf(view.panes[0].layers[0].config.registration), 'neighbor.1');
  // As layers of one pane: four at most, in the palette's colors.
  view.sweepRegistrations(regs, { target: 'panes' });
  const layered = view.sweepRegistrations(regs.slice(0, 3), { target: 'layers' });
  assert.deepEqual(layered, { panes: 1, layers: 3 });
  assert.deepEqual(view.panes[0].layers.map((l) => registrationTagOf(l.config.registration)), ['off.0', 'off.1', 'neighbor.1']);
  assert.equal(new Set(view.panes[0].layers.map((l) => l.config.color)).size, 3);
  assert.deepEqual(view.panes[0].layers.map((l) => l.config.opacity), [1, 0.45, 0.45]);
  assert.ok(view.sweepRegistrations([...regs, { live: false, corrections: 'off' }, ...regs], { target: 'layers' }).problem === undefined,
    'a duplicate is one registration, so four fit a pane');
  view.sweepRegistrations(regs, { target: 'panes' });
  await view.build(regRecord);
  await idle(view);
  assert.equal(server.copyLoads.length, 4, 'one load per registration, shared by the panes and layers that ask');
});

test('registration: the report names each rendering\'s copy and its registration in words', async () => {
  resetServer();
  const { view } = makeView(2);
  setTech(view, 1, 'voxel2');
  pickRegistration(view, 1, 0, 'off.0');
  await view.build(regRecord);
  await idle(view);
  view.reportCamera = () => null;   // the stub renderer has no camera to read
  const { renderings } = view.reportSources();
  assert.deepEqual(renderings.map((r) => [r.copy, r.registration]),
    [['neighbor.1', 'Current transforms + Residual field'], ['off.0', 'Stored positions, no correction']]);
});

// -- run -------------------------------------------------------------------
let failed = 0;
const only = process.env.ONLY || '';
for (const [name, fn] of cases) {
  if (only && !name.includes(only)) continue;
  try {
    // A case that never settles (a queue that deadlocked) fails instead of hanging the run.
    await Promise.race([fn(), new Promise((_, reject) => {
      setTimeout(() => reject(new Error('the case did not finish in 20 s')), 20000).unref();
    })]);
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack || error}`);
  }
}
fs.rmSync(tmp, { recursive: true, force: true });
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
