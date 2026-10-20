// The decisions app.js makes that need no page (static/js/appcore.js).  There is
// no JS runner in this repo: run it with
//
//     node tests/js/test_appcore.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free.  Each case names the defect from the 2026-10-15 audit it pins.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const {
  meshCacheKey, dropWithPrefix, DrawTokens, importKind, isTypingTarget, registrationChoiceDiffers,
  MeshCache, meshWeight, dropOtherCopies, singleFlight, sameOptions, chooseSource,
  helpLayouts, pickHelpLayout, paneCountMessage, StatusHold, FLARE_HOLD_MS,
  extrasWanted, startExtras, adoptExtras, sharedRequest, sameCamera,
  extrasKey, extrasWeight, keptAnswer, keptRequest, KEPT_EXTRAS_LIMIT,
} = await load('appcore.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const cell = (id, extra = {}) => ({
  key: `NeitzInferiorMonkey/${id}`,
  summary: { copy_tag: 'curvefit.1', data_stamp: '2026-09-23T22:13:01.890Z', ...extra },
});

// -- the browser's copy of a surface (W1-F06, W2-A2-01) ----------------------------

test('a surface is keyed on the cell, its copy, its stamp and the options', () => {
  const base = meshCacheKey(cell(1178), 'pathway=voxel2');
  assert.notEqual(base, meshCacheKey(cell(1178), 'pathway=original'), 'options');
  assert.notEqual(base, meshCacheKey(cell(1178, { data_stamp: '2026-10-01T00:00:00Z' }), 'pathway=voxel2'),
    'a cell edited in Viking and loaded again is another surface');
  assert.notEqual(base, meshCacheKey(cell(1178, { copy_tag: 'off.0' }), 'pathway=voxel2'),
    'another copy of the cell (its correction mode / transforms) is another surface');
  assert.notEqual(base, meshCacheKey(cell(1179), 'pathway=voxel2'), 'cell');
  assert.equal(base, meshCacheKey(cell(1178), 'pathway=voxel2'), 'same inputs, same key');
});

test('a copy loaded uncorrected and then corrected is two surfaces (2.6.5)', () => {
  // Inferior c1178 during the Curve fit outage of 2026-10-16: curvefit.1 asked for, the
  // correction not applied.  Unloaded (its surfaces kept for an Undo) and loaded again once
  // the service answered: the same copy and stamp, the annotations now moved.
  const asked = cell(1178, { positions: 'live-registration:9a1e' });
  const done = cell(1178, { positions: 'live-registration:9a1e|section-correction:4f1c0d6a2b9e8c77' });
  assert.notEqual(meshCacheKey(asked, 'pathway=voxel2'), meshCacheKey(done, 'pathway=voxel2'));
  assert.notEqual(meshCacheKey(cell(1178), 'x'), meshCacheKey(asked, 'x'), 'stored positions too');
  const cache = new MeshCache(10000);
  cache.set(meshCacheKey(asked, 'pathway=voxel2'), mesh(3, 3));
  cache.set(meshCacheKey(done, 'pathway=voxel2'), mesh(3, 3));
  dropOtherCopies(cache, done);
  assert.deepEqual([...cache.keys()], [meshCacheKey(done, 'pathway=voxel2')],
    'the uncorrected surface goes with the copy it was drawn from');
  // And the other way: the corrected one goes when the copy comes back uncorrected
  // (the transforms' token alone is not the start of the corrected copy's key).
  cache.set(meshCacheKey(asked, 'pathway=voxel2'), mesh(3, 3));
  dropOtherCopies(cache, asked);
  assert.deepEqual([...cache.keys()], [meshCacheKey(asked, 'pathway=voxel2')]);
});

test('a summary without a copy tag or stamp still keys', () => {
  const key = meshCacheKey({ key: 'RC1/6', summary: {} }, 'x');
  assert.ok(key.startsWith('RC1/6|'));
  assert.doesNotThrow(() => meshCacheKey({ key: 'RC1/6' }, 'x'));
});

test('dropping a cell takes all its surfaces and only its own', () => {
  const cache = new Map();
  for (const [id, sig] of [[1, 'a'], [1, 'b'], [12, 'a'], [2, 'a']]) {
    cache.set(meshCacheKey(cell(id), sig), { id, sig });
  }
  dropWithPrefix(cache, `${cell(1).key}|`);
  // c12 shares a numeric prefix with c1: the delimiter keeps it.
  assert.deepEqual([...cache.values()].map((m) => m.id).sort((a, b) => a - b), [2, 12]);
});

// -- which draw is the live one (W2-A2-03) -----------------------------------------

test('only the newest draw of a cell may land', () => {
  const draws = new DrawTokens();
  const first = draws.begin('a');
  assert.ok(draws.current('a', first));
  const second = draws.begin('a');   // Apply pressed again while the first build runs
  assert.ok(!draws.current('a', first), 'the older draw stands down');
  assert.ok(draws.current('a', second));
});

test('draws of different cells do not retire each other', () => {
  const draws = new DrawTokens();
  const a = draws.begin('a');
  const b = draws.begin('b');
  assert.ok(draws.current('a', a) && draws.current('b', b));
});

test('a forgotten cell retires its draws, and a new draw of it is not one of them', () => {
  const draws = new DrawTokens();
  const old = draws.begin('a');
  draws.forget('a');
  assert.ok(!draws.current('a', old), 'unloaded');
  const fresh = draws.begin('a');    // loaded again
  assert.ok(!draws.current('a', old), 'the old draw must not come back to life');
  assert.ok(draws.current('a', fresh));
});

// -- Import (W1-F09) ---------------------------------------------------------------

test('Import: a workspace, a neuron export and structure lists', () => {
  const workspace = JSON.stringify({ kind: 'sbfsem-workspace', neurons: [] });
  assert.equal(importKind(workspace).kind, 'workspace');
  assert.deepEqual(importKind(workspace).doc, { kind: 'sbfsem-workspace', neurons: [] });
  assert.equal(importKind(JSON.stringify({ schema: 2, structure_id: 5 })).kind, 'neuron');
  assert.equal(importKind('1178, 1179, 1180-1190').kind, 'structures');
  assert.equal(importKind('ON bipolar').kind, 'structures');
  assert.equal(importKind('').kind, 'structures');
});

test('Import: a list of one ID is valid JSON (a number) and is still a list', () => {
  for (const text of ['1178', '1178\n', '  5  ', '0.5']) {
    assert.equal(importKind(text).kind, 'structures', JSON.stringify(text));
  }
  // Other JSON that is not an object cannot be a neuron export either.
  for (const text of ['[1, 2, 3]', 'null', 'true', '"c1178"']) {
    assert.equal(importKind(text).kind, 'structures', text);
  }
});

test('Import: a workspace-like object without cells is a neuron export, not a workspace', () => {
  assert.equal(importKind(JSON.stringify({ kind: 'sbfsem-workspace' })).kind, 'neuron');
});

// -- keyboard (W1-F21) ---------------------------------------------------------------

test('Ctrl+Z belongs to a text field, not to a checkbox, slider or button', () => {
  assert.ok(isTypingTarget({ tagName: 'INPUT', type: 'text' }));
  assert.ok(isTypingTarget({ tagName: 'INPUT', type: 'number' }));
  assert.ok(isTypingTarget({ tagName: 'INPUT' }), 'an input with no type is text');
  assert.ok(isTypingTarget({ tagName: 'TEXTAREA' }));
  assert.ok(isTypingTarget({ tagName: 'DIV', isContentEditable: true }));
  for (const type of ['checkbox', 'radio', 'range', 'button', 'color']) {
    assert.ok(!isTypingTarget({ tagName: 'INPUT', type }), type);
  }
  assert.ok(!isTypingTarget({ tagName: 'SELECT' }));
  assert.ok(!isTypingTarget({ tagName: 'BUTTON' }));
  assert.ok(!isTypingTarget({ tagName: 'BODY' }));
  assert.ok(!isTypingTarget(null));
});

// -- undo (W1-F05) -----------------------------------------------------------------

test('a step that switched Registration or the section correction reloads the cell', () => {
  const record = { registration: true, corrections: 'curvefit' };
  assert.ok(!registrationChoiceDiffers(record, { registration: true, corrections: 'curvefit' }));
  assert.ok(registrationChoiceDiffers(record, { registration: false, corrections: 'curvefit' }));
  assert.ok(registrationChoiceDiffers(record, { registration: true, corrections: 'off' }));
  assert.ok(registrationChoiceDiffers(record, { registration: true, corrections: 'neighbor' }));
  // The server's default is a choice too: it is compared as it is.
  assert.ok(registrationChoiceDiffers(record, { registration: undefined, corrections: 'curvefit' }));
  assert.ok(!registrationChoiceDiffers({}, {}));
  // Manual moves (2026-10-19): a step with other moves is another copy of the cell too.
  const moved = { ...record, manualMoves: { 'curvefit.1': { 7: { dx: 0.1, dy: 0, dz: 0, t: 1 } } } };
  assert.ok(registrationChoiceDiffers(moved, { ...record }));
  assert.ok(registrationChoiceDiffers(record, { ...moved }));
  assert.ok(!registrationChoiceDiffers(moved, { ...moved, manualMoves: JSON.parse(JSON.stringify(moved.manualMoves)) }));
  assert.ok(!registrationChoiceDiffers({ ...record, manualMoves: {} }, { ...record, manualMoves: null }),
    'no moves and an empty store are the same');
});

// -- the surfaces kept for Undo (audit wave 2, R3) ----------------------------------

/** A mesh as the server sends one: flat vertex and index arrays. */
const mesh = (vertices, faces) => ({
  vertices: new Array(vertices).fill(0), faces: new Array(faces).fill(0), n_faces: faces / 3,
});

test('a mesh weighs its numbers: vertices, indices and per-vertex data', () => {
  assert.equal(meshWeight(mesh(30, 18)), 48);
  assert.equal(meshWeight({ vertices: [1, 2, 3], faces: [0, 0, 0], vertex_data: [1] }), 7);
  assert.equal(meshWeight({}), 0);
  assert.equal(meshWeight(null), 0);
});

test('a mesh cache gives back what it was given, and forgets nothing while it fits', () => {
  const cache = new MeshCache(100);
  const a = mesh(30, 30);
  cache.set('a', a);
  assert.equal(cache.get('a'), a);
  assert.equal(cache.get('missing'), undefined);
  assert.ok(cache.has('a'));
  assert.equal(cache.size, 1);
  assert.equal(cache.weight, 60);
  cache.delete('a');
  assert.equal(cache.size, 0);
  assert.equal(cache.weight, 0);
});

test('a full mesh cache lets go of the one used longest ago, and a read counts as use', () => {
  const cache = new MeshCache(100);
  cache.set('old', mesh(20, 20));      // 40
  cache.set('mid', mesh(20, 20));      // 80
  cache.get('old');                    // old is now the newest
  cache.set('new', mesh(20, 20));      // 120 > 100: mid goes, not old
  assert.deepEqual([...cache.keys()].sort(), ['new', 'old']);
  assert.equal(cache.weight, 80);
});

test('the surface just stored is never the one evicted, even when it alone is over the limit', () => {
  const cache = new MeshCache(100);
  cache.set('small', mesh(6, 6));
  cache.set('huge', mesh(300, 300));
  assert.deepEqual([...cache.keys()], ['huge'], 'the small one made room; the huge one stays');
  assert.equal(cache.get('huge').n_faces, 100);
});

test('storing a key again replaces its weight instead of adding to it', () => {
  const cache = new MeshCache(1000);
  cache.set('a', mesh(30, 30));
  cache.set('a', mesh(10, 10));
  assert.equal(cache.size, 1);
  assert.equal(cache.weight, 20);
});

test('dropWithPrefix empties a mesh cache the way it empties a Map, and keeps its weight true', () => {
  const cache = new MeshCache(10000);
  for (const [id, sig] of [[1, 'a'], [1, 'b'], [12, 'a']]) cache.set(meshCacheKey(cell(id), sig), mesh(30, 30));
  dropWithPrefix(cache, `${cell(1).key}|`);
  assert.deepEqual([...cache.keys()], [meshCacheKey(cell(12), 'a')]);
  assert.equal(cache.weight, 60);
  cache.clear();
  assert.equal(cache.size, 0);
  assert.equal(cache.weight, 0);
});

test('a cell loaded as another copy drops the surfaces of the copies before it, and only its own', () => {
  const cache = new MeshCache(10000);
  const before = cell(1178);
  const edited = cell(1178, { data_stamp: '2026-10-01T00:00:00Z' });
  cache.set(meshCacheKey(before, 'x'), mesh(3, 3));
  cache.set(meshCacheKey(before, 'y'), mesh(3, 3));
  cache.set(meshCacheKey(cell(11780), 'x'), mesh(3, 3));
  cache.set(meshCacheKey(edited, 'x'), mesh(3, 3));
  dropOtherCopies(cache, edited);
  assert.deepEqual([...cache.keys()].sort(),
    [meshCacheKey(cell(11780), 'x'), meshCacheKey(edited, 'x')].sort(),
    'the new copy keeps all its surfaces; another cell is left alone');
  // Undo of an unload brings back the same copy: nothing of it was dropped.
  dropOtherCopies(cache, cell(11780));
  assert.equal(cache.size, 2);
});

// -- Enter and clicks that arrive twice (audit wave 2, CR-2) ---------------------------

test('a single-flight action runs once however many times it is asked while it runs', async () => {
  let runs = 0;
  let finish;
  const save = singleFlight(() => { runs += 1; return new Promise((resolve) => { finish = resolve; }); });
  const first = save('a');
  assert.equal(await save('b'), undefined, 'the second Enter does nothing');
  save('c');
  assert.equal(runs, 1);
  finish('written');
  assert.equal(await first, 'written');
  save('d');                                    // free again
  assert.equal(runs, 2);
  finish();
});

test('a single-flight action that throws is free again', async () => {
  let runs = 0;
  const flaky = singleFlight(async () => { runs += 1; throw new Error('500'); });
  await assert.rejects(flaky(), /500/);
  await assert.rejects(flaky(), /500/);
  assert.equal(runs, 2, 'a failed save can be pressed again');
});

// -- a dropdown rebuilt only when it differs (audit wave 2, R4) ----------------------

/** What a browser's <option> gives back: `text` strips and collapses whitespace, `textContent` does not. */
const option = (label, value) => ({
  value, textContent: label, get text() { return label.replace(/\s+/g, ' ').trim(); },
});

test('options that are the entries are the same, repeated and non-breaking spaces included', () => {
  const entries = [['c9 · im ON  parasol x', 'k1'], ['c10 · im', 'k2'], ['plain', 'k3']];
  const options = entries.map(([label, value]) => option(label, value));
  assert.ok(sameOptions(options, entries));
  // Comparing option.text would have said "different" here, for the double space.
  assert.notEqual(options[0].text, entries[0][0]);
});

test('options differ by label, by value, by count and by order', () => {
  const entries = [['a', '1'], ['b', '2']];
  const options = entries.map(([label, value]) => option(label, value));
  assert.ok(!sameOptions(options, [['a', '1'], ['B', '2']]));
  assert.ok(!sameOptions(options, [['a', '1'], ['b', '3']]));
  assert.ok(!sameOptions(options, [['a', '1']]));
  assert.ok(!sameOptions(options, [['b', '2'], ['a', '1']]));
  assert.ok(sameOptions([], []));
});

// -- the volume the Connectivity tab opens on (audit wave 2, right-click Analysis) ---

test('a right-click on a cell opens Connectivity on that cell\'s volume, else keeps the choice', () => {
  const sources = ['NeitzInferiorMonkey', 'RC1'];
  assert.equal(chooseSource({ sources, current: 'NeitzInferiorMonkey', preferred: 'RC1' }), 'RC1');
  assert.equal(chooseSource({ sources, current: 'RC1' }), 'RC1', 'the tab opened by hand keeps what was chosen');
  assert.equal(chooseSource({ sources, current: 'RC1', preferred: null }), 'RC1');
  assert.equal(chooseSource({ sources, current: 'RC2' }), 'NeitzInferiorMonkey',
    'a volume with nothing loaded falls to the first that has cells');
  assert.equal(chooseSource({ sources, current: 'RC1', preferred: 'RC2' }), 'RC1',
    'a preferred volume with no loaded cells is not offered');
  assert.equal(chooseSource({ sources: [], current: 'RC2', preferred: 'RC1' }), 'RC2',
    'no cells at all: the current volume stands');
});

// -- long help panels (audit wave 2, F-help) -----------------------------------------------

test('help layouts: one column always, more only while they fit the window', () => {
  assert.deepEqual(helpLayouts(300).map((l) => l.columns), [1], 'a narrow window still gets the one column');
  assert.deepEqual(helpLayouts(800).map((l) => l.columns), [1, 2]);
  assert.deepEqual(helpLayouts(1024).map((l) => l.columns), [1, 2, 3]);
  assert.deepEqual(helpLayouts(1280).map((l) => l.columns), [1, 2, 3]);
  assert.deepEqual(helpLayouts(1920).map((l) => l.columns), [1, 2, 3, 4]);
  assert.deepEqual(helpLayouts(1920, 2).map((l) => l.columns), [1, 2], 'a cap on the columns');
  const widths = helpLayouts(1920).map((l) => l.width);
  assert.deepEqual(widths, [340, 672, 1004, 1336], 'the 340 px panel, then 314 px columns 18 px apart');
});

test('help layout: the narrowest panel that is short enough, else the narrowest, to scroll', () => {
  const layouts = helpLayouts(1920);
  const heights = { 1: 1154, 2: 614, 3: 416, 4: 330 };
  const heightAt = (layout) => heights[layout.columns];
  assert.deepEqual(pickHelpLayout(layouts, 700, heightAt), { columns: 2, width: 672, height: 614, fits: true });
  assert.equal(pickHelpLayout(layouts, 400, heightAt).columns, 4);
  assert.equal(pickHelpLayout(layouts, 5000, heightAt).columns, 1, 'one column when it already fits');
  const none = pickHelpLayout(layouts, 100, heightAt);
  assert.deepEqual([none.columns, none.fits, none.height], [1, false, 1154]);
  const measured = [];
  pickHelpLayout(layouts, 700, (l) => { measured.push(l.columns); return heights[l.columns]; });
  assert.deepEqual(measured, [1, 2], 'it stops at the first that fits');
});

test('the status line after the pane count changes: new panes wait for Compare, fewer panes leave the rest as it was', () => {
  assert.equal(paneCountMessage(2, 4), 'Press Compare to fill the new panes.');
  assert.equal(paneCountMessage(3, 4), 'Press Compare to fill the new pane.');
  const fewer = paneCountMessage(4, 2);
  assert.ok(!/fill the new/.test(fewer), `no new pane to fill: ${fewer}`);
  assert.equal(fewer, 'Showing 2 panes; the ones that stay keep what they show.');
  assert.equal(paneCountMessage(2, 1), 'Showing 1 pane; the ones that stay keep what they show.');
  assert.equal(paneCountMessage(4, 4), null, 'nothing changed, nothing said');
});

// -- a flared status message against the busy tracker -----------------------------

// What api.js's busy tracker sends: a request starting, the last one ending.
const working = { pending: 1, message: 'working…', kind: 'busy' };
const ready = { pending: 0, message: 'ready', kind: '' };

test('a request that began before the flare and ends after it cannot wipe it (the freshness check, 2.12.0)', () => {
  // check_app --only viking: POST /api/neurons/freshness started 70 ms before the
  // click on Open in Viking and ended 8 ms after it; its "ready" replaced the flare.
  const hold = new StatusHold();
  assert.equal(hold.admit(working, -70), true, 'no hold yet: the tracker has the line');
  hold.hold(0);
  assert.equal(hold.admit(ready, 8), false, 'the "ready" waits');
  assert.equal(hold.admit(working, 500), false, 'and so does a request started under the flare');
  assert.equal(hold.admit(ready, FLARE_HOLD_MS - 1), false, 'up to the end of the hold');
  assert.equal(hold.admit(working, FLARE_HOLD_MS), true, 'then the tracker has the line again');
  assert.ok(FLARE_HOLD_MS > 2400, 'held at least as long as the flare runs (app.css: 2.4 s)');
});

test('at the end of the hold, work still going on takes the line back, and a "ready" is not said late', () => {
  const hold = new StatusHold(1000);
  hold.hold(0);
  hold.admit(ready, 10);
  const build = { pending: 0, message: 'meshing c943… 3s', kind: 'busy' };
  hold.admit(build, 900);
  assert.deepEqual(hold.release(), build, 'a poll loop between polls is busy with nothing pending');
  assert.equal(hold.admit(ready, 950), true, 'released early by its timer: no hold left');

  hold.hold(2000);
  hold.admit(working, 2100);
  hold.admit(ready, 2200);
  assert.equal(hold.release(), null, 'everything ended: the flared message stays until the next update');
  hold.hold(4000);
  assert.equal(hold.release(), null, 'nothing missed, nothing given back');
});

test('a second flare starts the hold again and forgets what the first one turned away', () => {
  const hold = new StatusHold(1000);
  hold.hold(0);
  hold.admit(working, 100);
  hold.hold(800);
  assert.equal(hold.admit(ready, 1500), false, 'held from the second flare');
  assert.equal(hold.admit(working, 1800), true);
  hold.hold(2000);
  assert.equal(hold.release(), null, 'what the first hold missed is not given back by the second');
});

// -- a manual move's Apply: the extras asked for while the surface builds (2026-10-20) -----

const OFF = { showDiscs: false, showSkeleton: false, showAnnoWire: false };
const NO_SHIFT = { showShift: false, shiftRings: false };

test('the extras fetched early follow the display: annotations always, the rest only when drawn', () => {
  assert.deepEqual(extrasWanted(OFF, NO_SHIFT),
    { nodes: true, outlines: false, skeleton: false, wire: false }, 'nothing shown: the annotations alone');
  assert.equal(extrasWanted({ ...OFF, showDiscs: true }, NO_SHIFT).outlines, true, 'the rings draw outlines');
  assert.equal(extrasWanted({ ...OFF, showDiscs: true }, NO_SHIFT, { hasCurves: false }).outlines, false,
    'a cell traced only as discs has none to fetch');
  assert.equal(extrasWanted(OFF, { showShift: true, shiftRings: true }).outlines, true,
    "the Position shift layer's rings draw them too");
  assert.equal(extrasWanted(OFF, { showShift: true, shiftRings: false }).outlines, false);
  assert.equal(extrasWanted(OFF, { showShift: false, shiftRings: true }).outlines, false, 'rings of a layer that is off');
  assert.equal(extrasWanted({ ...OFF, showSkeleton: true }, NO_SHIFT).skeleton, true);
  assert.equal(extrasWanted({ ...OFF, showAnnoWire: true }, NO_SHIFT).wire, true);
});

test('the early requests leave at once, only for what is wanted and not cached, and a failure settles to null', async () => {
  const sent = [];
  const fetcher = (name, answer) => () => {
    sent.push(name);
    return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
  };
  const pending = startExtras({ nodes: true, outlines: true, skeleton: true, wire: false }, {
    nodes: fetcher('nodes', ['n']),
    outlines: fetcher('outlines', new Error('502')),
    skeleton: fetcher('skeleton', { s: 1 }),
    wire: fetcher('wire', { sig: '', data: [] }),
  }, (name) => name === 'skeleton');
  assert.deepEqual(sent, ['nodes', 'outlines'], 'sent before startExtras returned; the cached skeleton and the unwanted wire are not');
  assert.deepEqual(Object.keys(pending), ['nodes', 'outlines']);
  assert.deepEqual(await pending.nodes, ['n']);
  assert.equal(await pending.outlines, null, 'a failed early request is asked again by the draw, which reports it');
});

test('what came early reaches the caches only when the draw takes it, after its surface', async () => {
  // The order addToScene keeps: the requests leave, the surface builds and lands, then the
  // extras are taken.  Answers in hand before the surface stay out of the caches until then.
  const order = [];
  const caches = { nodes: new Map(), outlines: new Map(), skeleton: new Map() };
  const key = 'NeitzInferiorMonkey/1178';
  const answer = (name, value, ms) => () => new Promise((resolve) => {
    order.push(`ask ${name}`);
    setTimeout(() => { order.push(`got ${name}`); resolve(value); }, ms);
  });
  const pending = startExtras({ nodes: true, outlines: true, skeleton: true }, {
    nodes: answer('nodes', ['moved'], 1), outlines: answer('outlines', ['curve'], 4),
    skeleton: answer('skeleton', { moved: true }, 2),
  });
  const surface = new Promise((resolve) => setTimeout(() => { order.push('surface'); resolve(); }, 15));
  await surface;
  assert.equal(caches.nodes.size + caches.outlines.size + caches.skeleton.size, 0, 'nothing kept before the draw takes it');
  assert.equal(await adoptExtras(pending, caches, key, () => false), true);
  order.push('drawn');
  assert.deepEqual(order.slice(0, 3), ['ask nodes', 'ask outlines', 'ask skeleton'], 'all three asked at the start');
  assert.ok(order.indexOf('got outlines') < order.indexOf('surface'), 'in hand while the surface built');
  assert.equal(order.at(-1), 'drawn');
  assert.deepEqual(caches.nodes.get(key), ['moved']);
  assert.deepEqual(caches.outlines.get(key), ['curve']);
  assert.deepEqual(caches.skeleton.get(key), { moved: true });
});

test('what came early for a copy since replaced is never kept, and never replaces this load\'s own', async () => {
  const key = 'NeitzInferiorMonkey/1178';
  // The cell was loaded again while the moved copy built (a Registration switch, another Apply).
  const stale = { nodes: new Map(), skeleton: new Map() };
  const gone = await adoptExtras({ nodes: Promise.resolve(['old copy']), skeleton: Promise.resolve({}) },
    stale, key, () => true);
  assert.equal(gone, false);
  assert.equal(stale.nodes.size + stale.skeleton.size, 0, 'nothing of the copy before is kept, so nothing of it is drawn');
  // The same load fetched the annotations meanwhile (a pick): those stay; a failed request adds nothing.
  const caches = { nodes: new Map([[key, ['picked']]]), outlines: new Map(), skeleton: new Map() };
  assert.equal(await adoptExtras({ nodes: Promise.resolve(['early']), outlines: Promise.resolve(null) },
    caches, key, () => false), true);
  assert.deepEqual(caches.nodes.get(key), ['picked']);
  assert.equal(caches.outlines.has(key), false, 'a failed early request leaves the draw to ask again');
});

// -- one request for a load's annotations ---------------------------------------------

/** Requests the test answers when it likes: `send` sends one, `sent[i]` settles the i-th. */
function requests() {
  const sent = [];
  return {
    sent,
    send: () => new Promise((resolve, reject) => { sent.push({ resolve, reject }); }),
  };
}

test('askers of one load share its request, answered or not, and a later load sends its own', async () => {
  const pending = new Map();
  const key = 'NeitzInferiorMonkey/1178';
  const r = requests();
  // A Registration switch's extras, then the waiting points 0.6 s later: one request.
  const early = sharedRequest(pending, key, 7, r.send);
  const points = sharedRequest(pending, key, 7, r.send);
  assert.equal(r.sent.length, 1, 'the second asker waits for the first request');
  assert.equal(points, early);
  r.sent[0].resolve(['nodes of load 7']);
  assert.deepEqual(await points, ['nodes of load 7']);
  // Answered, and not yet in the caches (they take it once the surface is drawn): a pick asks.
  const pick = sharedRequest(pending, key, 7, r.send);
  assert.equal(r.sent.length, 1, 'the same load is never asked twice');
  assert.deepEqual(await pick, ['nodes of load 7']);
  // The cell loaded again (Update from Viking keeps the copy's name, not its load).
  const update = sharedRequest(pending, key, 8, r.send);
  assert.equal(r.sent.length, 2, 'a later load sends its own request');
  assert.notEqual(update, early);
  // Let go of (forgetDrawn): asked again, it is sent again.
  pending.delete(key);
  sharedRequest(pending, key, 8, r.send);
  assert.equal(r.sent.length, 3);
});

test('a request of an earlier load is never shared with a later one, nor with another cell', async () => {
  const pending = new Map();
  const r = requests();
  const old = sharedRequest(pending, 'RC1/410', 3, r.send);
  const fresh = sharedRequest(pending, 'RC1/410', 4, r.send);
  const other = sharedRequest(pending, 'RC1/514', 4, r.send);
  assert.equal(r.sent.length, 3);
  assert.notEqual(old, fresh);
  assert.notEqual(fresh, other);
  // The earlier load's answer coming last does not take the later load's place.
  r.sent[1].resolve(['load 4']);
  await fresh;
  r.sent[0].resolve(['load 3']);
  await old;
  await Promise.resolve();
  assert.equal(sharedRequest(pending, 'RC1/410', 4, r.send), fresh, 'the later load keeps its own');
  assert.deepEqual(await sharedRequest(pending, 'RC1/410', 4, r.send), ['load 4']);
  assert.equal(r.sent.length, 3);
});

test('a failed request fails for every asker and is not held, and a send that throws is a rejection', async () => {
  const pending = new Map();
  const r = requests();
  const a = sharedRequest(pending, 'k', 1, r.send);
  const b = sharedRequest(pending, 'k', 1, r.send);
  r.sent[0].reject(new Error('502'));
  await assert.rejects(a, /502/);
  await assert.rejects(b, /502/);
  assert.equal(pending.size, 0, 'a failure is not kept');
  sharedRequest(pending, 'k', 1, r.send);
  assert.equal(r.sent.length, 2, 'the next asker tries again');
  r.sent[1].resolve([]);
  const thrown = sharedRequest(pending, 'other', 1, () => { throw new Error('offline'); });
  await assert.rejects(thrown, /offline/);
  await Promise.resolve();
  assert.equal(pending.has('other'), false);
});

// -- a copy's extras, kept for the next load at the same positions (2.17.7) ----------

/** A record of RPC1 c430 as a load's summary gives it. */
const loaded = (extra = {}) => ({
  key: 'RPC1/430',
  summary: {
    copy_tag: 'curvefit.1', data_stamp: '2026-10-20T17:00:00.123000Z', last_modified: '2026-10-19T08:00:00Z',
    n_nodes: 2487, positions: 'live-registration:9f1c|section-correction:4be0', ...extra,
  },
});

test('a kept extra is keyed on the cell, its annotations and their positions, not on the copy\'s name', () => {
  const base = extrasKey(loaded(), 'outlines');
  assert.equal(base, extrasKey(loaded({ copy_tag: 'all.1' }), 'outlines'),
    'two settings that move nothing differently are one state (the server keys its outlines so too)');
  assert.notEqual(base, extrasKey(loaded(), 'nodes'), 'each extra its own');
  assert.notEqual(base, extrasKey({ ...loaded(), key: 'RPC2/430' }, 'outlines'), 'an ID is numbered per volume');
  assert.notEqual(base, extrasKey(loaded({ positions: 'live-registration:9f1c' }), 'outlines'),
    'the correction off, or asked for and not applied');
  assert.notEqual(base, extrasKey(loaded({ positions: 'section-correction:4be0' }), 'outlines'), 'the transforms off');
  assert.notEqual(base, extrasKey(loaded({ positions: `${loaded().summary.positions}|manual:0c41aa` }), 'outlines'),
    'a manual move');
  assert.notEqual(base, extrasKey(loaded({ positions: null }), 'outlines'), 'the stored positions');
  // An Update from Viking that found the cell edited.
  assert.notEqual(base, extrasKey(loaded({ data_stamp: '2026-10-20T18:00:00Z' }), 'outlines'), 'an annotation edited');
  assert.notEqual(base, extrasKey(loaded({ n_nodes: 2486 }), 'outlines'),
    'an annotation deleted: the newest stamp can stay as it was, the count cannot');
  assert.notEqual(base, extrasKey(loaded({ last_modified: '2026-10-20T18:00:00Z' }), 'outlines'), 'the structure row edited');
  assert.equal(extrasKey(loaded({ data_stamp: null }), 'outlines'), null, 'a file import is never kept');
  assert.equal(extrasKey(null, 'outlines'), null);
});

test('a kept extra weighs its numbers: outline points two each, annotations ten, skeleton points five', () => {
  const outline = { outline_um: [[0, 0], [1, 0], [1, 1]], cutouts_um: [[[0.2, 0.2], [0.3, 0.2]]] };
  assert.equal(extrasWeight([outline, { ...outline, cutouts_um: [] }]), (6 + 7 + 4) + (6 + 7));
  assert.equal(extrasWeight([{ id: 1, xyz_um: [0, 0, 0] }, { id: 2, xyz_um: [1, 1, 1] }]), 20);
  assert.equal(extrasWeight({ segments: [{ points_um: [[0, 0, 0], [1, 1, 1]] }, { points_um: [[2, 2, 2]] }] }), 4 + 15);
  assert.equal(extrasWeight([]), 0, 'a cell traced only as discs');
  // RPC1 c430's three as production answered them (2026-10-20: 897 outlines of 185,771 points
  // and 77 cutouts, 2,487 annotations, 2,697 skeleton points) weigh 425,916.
  assert.ok(425_916 * 7 < KEPT_EXTRAS_LIMIT, 'seven of its copies fit');
});

test('a kept extra answers without a request, and a missing one is asked for and kept', async () => {
  const kept = new MeshCache(1000, extrasWeight);
  const r = requests();
  const first = keptAnswer(kept, 'k', r.send);
  assert.equal(r.sent.length, 1);
  r.sent[0].resolve(['outlines']);
  assert.deepEqual(await first, ['outlines']);
  assert.deepEqual(await keptAnswer(kept, 'k', r.send), ['outlines']);
  assert.equal(r.sent.length, 1, 'answered from what is kept');
  assert.equal(await keptAnswer(kept, 'k', r.send), await keptAnswer(kept, 'k', r.send), 'the same answer, not a copy');
  // No key (a file import): asked each time, kept never.
  const a = keptAnswer(kept, null, r.send);
  r.sent[1].resolve(['import']);
  await a;
  keptAnswer(kept, null, r.send);
  assert.equal(r.sent.length, 3);
  assert.equal(kept.size, 1);
});

test('an answer the caller cannot vouch for, a failure or nothing is not kept', async () => {
  const kept = new MeshCache(1000, extrasWeight);
  const r = requests();
  const a = keptAnswer(kept, 'k', r.send, () => false);
  r.sent[0].resolve(['of either copy']);
  assert.deepEqual(await a, ['of either copy'], 'still answered');
  assert.equal(kept.has('k'), false);
  const b = keptAnswer(kept, 'k', r.send);
  r.sent[1].reject(new Error('502'));
  await assert.rejects(b, /502/);
  assert.equal(kept.has('k'), false);
  const c = keptAnswer(kept, 'k', r.send);
  r.sent[2].resolve(null);
  assert.equal(await c, null);
  assert.equal(kept.has('k'), false);
  keptAnswer(kept, 'k', r.send);
  assert.equal(r.sent.length, 4, 'asked again each time');
});

test('kept extras are bounded, least recently used first out', () => {
  const kept = new MeshCache(25, extrasWeight);
  const nodes = (n) => Array.from({ length: n }, (_, id) => ({ id }));
  kept.set('a', nodes(1)).set('b', nodes(1));
  kept.get('a');
  kept.set('c', nodes(1));
  assert.deepEqual([...kept.keys()], ['a', 'c'], 'b, used longest ago, made room');
  kept.set('d', nodes(5));
  assert.deepEqual([...kept.keys()], ['d'], 'one over the limit alone is kept until the next');
});

/**
 * A cell as the page holds it: its record (replaced on each load) and the tab's loads of it,
 * driven by the test the way api.js and state.js move them.
 */
function tabCell(record) {
  const cell = { record, loads: { begun: 1, running: 0 } };
  cell.opts = (send) => ({ live: () => cell.record, loads: () => ({ ...cell.loads }), send });
  return cell;
}

test('a switch back to a setting already drawn sends no request; a new setting or a manual move does', async () => {
  const kept = new MeshCache(KEPT_EXTRAS_LIMIT, extrasWeight);
  const r = requests();
  const corrected = loaded();
  const uncorrected = loaded({ copy_tag: 'off.1', positions: 'live-registration:9f1c' });
  const cell = tabCell(corrected);
  const ask = () => keptRequest(kept, 'outlines', cell.opts(r.send));
  const drawn = ask();
  r.sent[0].resolve(['corrected outlines']);
  await drawn;
  // The correction off: a new copy and a new state, so asked.
  cell.record = uncorrected;
  cell.loads.begun += 1;
  const off = ask();
  assert.equal(r.sent.length, 2);
  r.sent[1].resolve(['uncorrected outlines']);
  assert.deepEqual(await off, ['uncorrected outlines']);
  // And back on: drawn from what this tab holds.
  cell.record = loaded();
  cell.loads.begun += 1;
  assert.deepEqual(await ask(), ['corrected outlines']);
  assert.equal(r.sent.length, 2, 'no request');
  // A manual move on top: another state, asked.
  cell.record = loaded({ copy_tag: 'curvefit.1.m0c41aa', positions: `${corrected.summary.positions}|manual:0c41aa` });
  cell.loads.begun += 1;
  ask();
  assert.equal(r.sent.length, 3);
});

test('an Update from Viking that found the cell edited asks again; one that found nothing changed does not', async () => {
  const kept = new MeshCache(KEPT_EXTRAS_LIMIT, extrasWeight);
  const r = requests();
  const cell = tabCell(loaded());
  const ask = () => keptRequest(kept, 'nodes', cell.opts(r.send));
  const first = ask();
  r.sent[0].resolve(['as loaded']);
  await first;
  // The same copy's name, loaded again: Viking had nothing newer.
  cell.record = loaded();
  cell.loads.begun += 1;
  assert.deepEqual(await ask(), ['as loaded']);
  assert.equal(r.sent.length, 1);
  // Viking had an annotation edited.
  cell.record = loaded({ data_stamp: '2026-10-20T19:00:00Z' });
  cell.loads.begun += 1;
  const edited = ask();
  assert.equal(r.sent.length, 2);
  r.sent[1].resolve(['edited']);
  assert.deepEqual(await edited, ['edited']);
});

test('while a load of the cell is under way nothing is taken from what is kept, nor kept from it', async () => {
  const kept = new MeshCache(KEPT_EXTRAS_LIMIT, extrasWeight);
  kept.set(extrasKey(loaded(), 'outlines'), ['kept']);
  const r = requests();
  const cell = tabCell(loaded());
  // Update from Viking was clicked: the server may answer from the copy before or after it.
  cell.loads = { begun: 2, running: 1 };
  const during = keptRequest(kept, 'outlines', cell.opts(r.send));
  assert.equal(r.sent.length, 1, 'asked of the server, not taken from the kept');
  r.sent[0].resolve(['edited, already']);
  cell.loads.running = 0;
  assert.deepEqual(await during, ['edited, already']);
  assert.deepEqual(kept.get(extrasKey(loaded(), 'outlines')), ['kept'], 'nor kept under the state from before');
});

test('an answer that came back across a load is not kept: begun meanwhile, or answered and not yet the record', async () => {
  const kept = new MeshCache(KEPT_EXTRAS_LIMIT, extrasWeight);
  const r = requests();
  const before = loaded({ positions: 'live-registration:9f1c' });
  const cell = tabCell(before);
  // Asked; then the same copy is loaded again with the correction applied this time.
  const across = keptRequest(kept, 'outlines', cell.opts(r.send));
  cell.loads.begun += 1;
  r.sent[0].resolve(['corrected, under the uncorrected name']);
  await across;
  assert.equal(kept.size, 0, 'a load began while it was out');
  // Asked in the moment between a load's answer and its record (api.js names the new copy).
  const gap = keptRequest(kept, 'outlines', cell.opts(r.send));
  cell.record = loaded();
  r.sent[1].resolve(['of the new copy']);
  await gap;
  assert.equal(kept.size, 0, 'the record it was asked for was replaced');
  // Nothing loaded meanwhile: what came back is of the copy it names, and is kept.
  const quiet = keptRequest(kept, 'outlines', cell.opts(r.send));
  r.sent[2].resolve(['corrected']);
  await quiet;
  assert.deepEqual(kept.get(extrasKey(loaded(), 'outlines')), ['corrected']);
});

// -- the view a load put up ------------------------------------------------------------

test('a view moved while the cells came in is told from the one the load put up', () => {
  // The user's saved workspace of 2026-10-20 (c1178 and c16137, orthographic).
  const put = {
    position: [600.7653910611868, 11.662770846297022, 243.21372644697553],
    target: [147.31534332279375, 95.9447824616182, 46.134553350380884],
    up: [-0.4164512264176127, -0.14002482965030866, 0.8983103155907032],
    zoom: 12.336356474530863,
    orthographic: true,
  };
  const moved = (change) => ({ ...put, ...change });
  assert.equal(sameCamera(put, { ...put }), true);
  // What the controls' every-frame recompute leaves: the last bits of each coordinate.
  assert.equal(sameCamera(put, moved({ position: put.position.map((v) => v * (1 + 1e-14)) })), true);
  // The smallest moves a hand makes: a pixel of pan at this zoom, a tenth of a degree of turn, a wheel notch.
  assert.equal(sameCamera(put, moved({ target: [put.target[0] + 0.05, put.target[1], put.target[2]] })), false);
  const turn = 0.1 * Math.PI / 180;
  const [x, y, z] = put.position.map((v, i) => v - put.target[i]);
  const turned = [x * Math.cos(turn) - y * Math.sin(turn), x * Math.sin(turn) + y * Math.cos(turn), z];
  assert.equal(sameCamera(put, moved({ position: turned.map((v, i) => v + put.target[i]) })), false);
  assert.equal(sameCamera(put, moved({ zoom: put.zoom * 1.05 })), false);
  assert.equal(sameCamera(put, moved({ up: [0, 0, 1] })), false, 'a roll');
  assert.equal(sameCamera(put, moved({ orthographic: false })), false, 'Orthographic switched off');
  // Aimed at nothing (a fresh page's camera on its own target) is the same only as nothing.
  assert.equal(sameCamera(null, null), true);
  assert.equal(sameCamera(null, put), false);
  assert.equal(sameCamera(put, undefined), false);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack || error}`);
  }
}
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
