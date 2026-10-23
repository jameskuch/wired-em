// The session the browser keeps (static/js/session.js): what it stores, what a
// restore reads back, and what stays with the page.  Run it with
//
//     node tests/js/test_session.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free; localStorage and sessionStorage are plain maps.  tests/unit/test_js.py
// runs it with the other suites.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

/** A Storage with what session.js uses of one. */
class MemoryStorage {
  constructor() { this.items = new Map(); }
  get length() { return this.items.size; }
  key(i) { return [...this.items.keys()][i] ?? null; }
  getItem(k) { return this.items.has(k) ? this.items.get(k) : null; }
  setItem(k, v) { this.items.set(k, String(v)); }
  removeItem(k) { this.items.delete(k); }
  clear() { this.items.clear(); }
}

const local = new MemoryStorage();
const tab = new MemoryStorage();
globalThis.window = { localStorage: local, sessionStorage: tab };

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const session = await import(pathToFileURL(path.join(jsDir, 'session.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const KEY = 'sbfsem.session.v1';
const state = { source: 'NeitzTemporalMonkey', view: 'render', selected: null, neurons: new Map() };
const renderer = { cameraState: () => ({ position: [0, 0, 10], target: [0, 0, 0], up: [0, 0, 1], zoom: 1 }) };

/** The Display panel's snapshot with Hold ticked and a crop, as a page has it. */
const ui = () => ({
  ortho: false,
  display: { invert: false, clip_box: { min: [0, 0, 0], max: [5, 5, 5] }, hold_limits: true, volume_offsets: {} },
});

const stored = () => JSON.parse(local.getItem(KEY));

test('the session written to the browser leaves Hold out and keeps the rest of the Display panel', () => {
  local.clear();
  session.saveNow(state, renderer, ui());
  const saved = stored();
  assert.ok(saved, 'a session was written');
  assert.equal('hold_limits' in saved.ui.display, false, 'no Hold in the stored session');
  assert.deepEqual(saved.ui.display.clip_box, { min: [0, 0, 0], max: [5, 5, 5] }, 'the crop is kept');
  assert.equal(saved.ui.ortho, false);
  // The tab's own slot is the same text.
  const slot = [...local.items.keys()].find((k) => k.startsWith(`${KEY}.tab.`));
  assert.equal(local.getItem(slot), local.getItem(KEY));
});

test('a session stored with Hold ticked (before 2.30.2) is read back without it', () => {
  local.clear();
  tab.clear();
  const old = { schema: 1, saved_at: new Date().toISOString(), source: 'NeitzTemporalMonkey',
    neurons: [{ id: 162392, source: 'NeitzTemporalMonkey' }], camera: null, ui: ui() };
  local.setItem(KEY, JSON.stringify(old));
  const saved = session.load();
  assert.equal(saved.neurons[0].id, 162392);
  assert.equal('hold_limits' in saved.ui.display, false, 'Hold is not restored');
  assert.deepEqual(saved.ui.display.clip_box, { min: [0, 0, 0], max: [5, 5, 5] }, 'the crop still is');
});

test('a cell\'s Keep listed outlines is saved with it, and nothing when it never chose', () => {
  const neurons = new Map([
    ['a', { id: 1, source: 'i', corrections: 'all', keepOutlines: true, render: {}, display: {} }],
    ['b', { id: 2, source: 'i', corrections: 'all', render: {}, display: {} }],
  ]);
  const snap = session.snapshot({ ...state, neurons }, renderer, ui());
  assert.equal(snap.neurons[0].keepOutlines, true);
  assert.equal('keepOutlines' in snap.neurons[1], false);
});

test('the undo history keeps Hold: snapshot() is the page\'s and carries it', () => {
  const snap = session.snapshot(state, renderer, ui());
  assert.equal(snap.ui.display.hold_limits, true);
});

test('withoutPageState: copies what it changes, and passes anything without Hold through as it is', () => {
  const snap = session.snapshot(state, renderer, ui());
  const doc = { kind: 'sbfsem-workspace', version: 1, ...snap };
  const out = session.withoutPageState(doc);
  assert.equal(out.kind, 'sbfsem-workspace');
  assert.equal('hold_limits' in out.ui.display, false);
  assert.equal(doc.ui.display.hold_limits, true, 'the document given is not changed');
  assert.equal(out.neurons, doc.neurons, 'nothing else is copied');
  for (const plain of [null, undefined, {}, { ui: {} }, { ui: { display: null } }, { ui: { display: { invert: true } } }]) {
    assert.equal(session.withoutPageState(plain), plain);
  }
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n     ${error.stack.split('\n').slice(0, 3).join('\n     ')}`);
  }
}
console.log(`${cases.length - failed} of ${cases.length} passed`);
process.exit(failed ? 1 : 0);
