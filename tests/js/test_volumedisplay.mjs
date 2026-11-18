// Display settings kept per volume (static/js/volumedisplay.js): Flip Z from the
// volume's default until it is set by hand on the volume, what a session saved
// before 2.47.0 keeps, what an undo leaves, and the saved camera moved with a
// Flip Z the volume's default turned.  Run it with
//
//     node tests/js/test_volumedisplay.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.
// tests/unit/test_js.py runs it with the other suites.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const V = await import(pathToFileURL(path.join(jsDir, 'volumedisplay.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

class MemoryStorage {
  constructor() { this.items = new Map(); }
  getItem(k) { return this.items.has(k) ? this.items.get(k) : null; }
  setItem(k, v) { this.items.set(k, String(v)); }
  removeItem(k) { this.items.delete(k); }
}

/** /api/volumes as the server lists them (2.47.0): RC2 starts flipped, the rest do not. */
const VOLUMES = [
  { name: 'NeitzInferiorMonkey', display_defaults: { flip_z: false } },
  { name: 'RC1', display_defaults: { flip_z: false } },
  { name: 'RC2', display_defaults: { flip_z: true } },
  // A server older than 2.47.0 lists no defaults.
  { name: 'RPC1' },
];

test('a volume starts from its own default, and one the server lists none for from the panel’s', () => {
  assert.deepEqual(V.effectiveDisplay(VOLUMES, {}, 'RC2'), { flip_z: true });
  assert.deepEqual(V.effectiveDisplay(VOLUMES, {}, 'NeitzInferiorMonkey'), { flip_z: false });
  assert.deepEqual(V.effectiveDisplay(VOLUMES, {}, 'RPC1'), { flip_z: false });
  assert.deepEqual(V.effectiveDisplay(VOLUMES, {}, 'NotAVolume'), { flip_z: false });
  assert.deepEqual(V.effectiveDisplay(null, null, 'RC2'), { flip_z: false }, 'no list yet: the panel’s default');
});

test('what is set by hand on a volume wins over its default, and only on that volume', () => {
  let choices = V.remember({}, 'RC2', { flip_z: false });
  choices = V.remember(choices, 'NeitzInferiorMonkey', { flip_z: true });
  assert.deepEqual(V.effectiveDisplay(VOLUMES, choices, 'RC2'), { flip_z: false });
  assert.deepEqual(V.effectiveDisplay(VOLUMES, choices, 'NeitzInferiorMonkey'), { flip_z: true });
  assert.deepEqual(V.effectiveDisplay(VOLUMES, choices, 'RC1'), { flip_z: false }, 'another volume is untouched');
  const before = { RC2: { flip_z: true } };
  V.remember(before, 'RC2', { flip_z: false });
  assert.deepEqual(before, { RC2: { flip_z: true } }, 'remember returns a new object');
  assert.deepEqual(V.remember({}, '', { flip_z: true }), {}, 'no volume, nothing kept');
  assert.deepEqual(V.remember({}, 'RC2', { flip_z: 'yes' }), {}, 'only booleans are kept');
});

test('the choices go through the browser’s storage, and anything else under the name is ignored', () => {
  const store = new MemoryStorage();
  assert.deepEqual(V.readChoices(store), {}, 'nothing stored');
  assert.equal(V.writeChoices(store, { RC2: { flip_z: false } }), true);
  assert.equal(store.getItem(V.STORE_KEY), '{"RC2":{"flip_z":false}}');
  assert.deepEqual(V.readChoices(store), { RC2: { flip_z: false } });
  store.setItem(V.STORE_KEY, '{not json');
  assert.deepEqual(V.readChoices(store), {}, 'unreadable: nothing');
  store.setItem(V.STORE_KEY, JSON.stringify({ RC2: { flip_z: 1, other: true }, RC1: 'on', RPC1: { flip_z: true } }));
  assert.deepEqual(V.readChoices(store), { RPC1: { flip_z: true } });
  assert.deepEqual(V.readChoices(null), {}, 'no storage (a private window)');
  assert.equal(V.writeChoices(null, {}), false);
  const full = { getItem: () => null, setItem: () => { throw new Error('QuotaExceededError'); } };
  assert.equal(V.writeChoices(full, { RC2: { flip_z: true } }), false, 'a full store says so, never throws');
});

test('a Display snapshot this build saved is taken as saved; an older one or none is not', () => {
  assert.deepEqual(V.unsettledKeys({ flip_z: false, per_volume: ['flip_z'] }), []);
  assert.deepEqual(V.unsettledKeys({ flip_z: false }), ['flip_z'], 'saved before 2.47.0');
  assert.deepEqual(V.unsettledKeys(undefined), ['flip_z'], 'no snapshot (a first visit)');
  assert.deepEqual(V.unsettledKeys({ flip_z: true, per_volume: 'flip_z' }), ['flip_z'], 'a mark that is not a list is no mark');
});

test('an older session’s Flip Z on is kept for its volume; off was the old default and is not', () => {
  // Turned on by hand on Inferior before 2.47.0: kept.
  assert.deepEqual(V.seedFromSession({}, 'NeitzInferiorMonkey', { flip_z: true }), { NeitzInferiorMonkey: { flip_z: true } });
  // Off on RC2: the old default, so RC2's own (on) applies.
  const seeded = V.seedFromSession({}, 'RC2', { flip_z: false });
  assert.deepEqual(seeded, {});
  assert.deepEqual(V.effectiveDisplay(VOLUMES, seeded, 'RC2'), { flip_z: true });
  // A choice already made on the volume is not replaced by an old session.
  assert.deepEqual(V.seedFromSession({ RC1: { flip_z: false } }, 'RC1', { flip_z: true }), { RC1: { flip_z: false } });
  // A session this build saved holds nothing to seed.
  assert.deepEqual(V.seedFromSession({}, 'RC1', { flip_z: true, per_volume: ['flip_z'] }), {});
  // Older still: a session from before Flip Z existed.
  assert.deepEqual(V.seedFromSession({}, 'RC1', { invert: true }), {});
  assert.deepEqual(V.seedFromSession({}, '', { flip_z: true }), {}, 'no volume named');
});

test('an undo that puts Flip Z back is kept for the volume; one that matches it keeps nothing', () => {
  // Off by hand on RC2, then undone: RC2 is on again, and stays on when come back to.
  const off = V.remember({}, 'RC2', { flip_z: false });
  const undone = V.choiceAfterUndo(VOLUMES, off, 'RC2', { flip_z: true });
  assert.deepEqual(undone, { flip_z: true });
  assert.deepEqual(V.effectiveDisplay(VOLUMES, V.remember(off, 'RC2', undone), 'RC2'), { flip_z: true });
  // Undoing a volume switch lands on what the volume shows already: nothing to keep.
  assert.equal(V.choiceAfterUndo(VOLUMES, {}, 'NeitzInferiorMonkey', { flip_z: false }), null);
  assert.equal(V.choiceAfterUndo(VOLUMES, {}, 'RC2', { flip_z: true }), null);
  assert.equal(V.choiceAfterUndo(VOLUMES, {}, 'RC2', { invert: true }), null, 'a step with no Flip Z');
  assert.equal(V.choiceAfterUndo(VOLUMES, {}, 'RC2', undefined), null);
});

test('a saved camera moves with a Flip Z the volume turned, as a click on the box moves it', () => {
  const camera = { position: [10, 20, 300], target: [10, 20, 110], up: [0, 1, 0], zoom: 2, orthographic: false };
  const moved = V.cameraUnderFlip(camera, false, true);
  // The target's level, z 110 in the unflipped scene, is drawn at -110 flipped.
  assert.deepEqual(moved.target, [10, 20, -110]);
  assert.deepEqual(moved.position, [10, 20, 80], 'the camera keeps its offset from the target');
  assert.deepEqual([moved.up, moved.zoom, moved.orthographic], [[0, 1, 0], 2, false], 'the rest is kept');
  assert.deepEqual(camera.target, [10, 20, 110], 'the saved camera is not changed');
  // And back again: the same camera.
  assert.deepEqual(V.cameraUnderFlip(moved, true, false), camera);
  // Nothing turned, or no camera: as it was.
  assert.equal(V.cameraUnderFlip(camera, true, true), camera);
  assert.equal(V.cameraUnderFlip(camera, undefined, false), camera, 'saved before Flip Z: unflipped');
  assert.equal(V.cameraUnderFlip(null, false, true), null);
  const odd = { position: [1, 2], target: [0, 0, 0] };
  assert.equal(V.cameraUnderFlip(odd, false, true), odd, 'a camera it cannot read is left for restoreCamera to refuse');
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
console.log(`${cases.length - failed}/${cases.length} passed`);
process.exit(failed ? 1 : 0);
