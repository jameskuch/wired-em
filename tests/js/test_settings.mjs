// The Settings tab's rules (static/js/settings.js): what the browser holds and
// what a reset removes, which build the page is, the server card's rows, and
// where a restart stands.  Run it with
//
//     node tests/js/test_settings.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const {
  STORAGE_GROUPS, groupOf, inventory, resetGroups, pageAssets, buildCheck,
  formatBytes, formatDuration, restartPhase, serverRows, RESTART_WAIT_S,
  RATIO_KEY, RATIO_LIMITS, parseRatio, formatRatio, correctionLogView,
  savedRatio, pullTitle, cellPullsView, PULLS_HELP,
  EXPERT_RENDER_KEY, EXPERT_RENDER_HELP, expertRender, setExpertRender,
} = await load('settings.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** A Storage the way the browser's behaves, over a Map. */
class FakeStorage {
  constructor(entries = {}) { this.map = new Map(Object.entries(entries)); }
  get length() { return this.map.size; }
  key(i) { return [...this.map.keys()][i] ?? null; }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
}

class BlockedStorage {
  get length() { throw new Error('SecurityError'); }
  key() { throw new Error('SecurityError'); }
  getItem() { throw new Error('SecurityError'); }
  removeItem() { throw new Error('SecurityError'); }
}

const browser = () => ({
  local: new FakeStorage({
    'sbfsem.session.v1': '{"schema":1}',
    'sbfsem.session.v1.tab.k1-abc': '{"schema":1,"neurons":[]}',
    'sbfsem:technique-defaults': '{}',
    'sbfsem:swatches': '["#ff0000"]',
    'someone.else': 'x',
  }),
  session: new FakeStorage({ 'sbfsem.tab': 'k1-abc', 'sbfsem.vikingChoice': 'viking' }),
});

test('every key is put in its group, a tab slot by its prefix, an unknown one under Other', () => {
  assert.equal(groupOf('sbfsem.session.v1.tab.zz-9').id, 'session');
  assert.equal(groupOf('sbfsem.session.v1').id, 'session');
  assert.equal(groupOf('sbfsem:sidebar-width').id, 'layout');
  assert.equal(groupOf('sbfsem.vikingClients').id, 'viking');
  assert.equal(groupOf('mystery').id, 'other');
  const ids = STORAGE_GROUPS.map((g) => g.id);
  assert.equal(new Set(ids).size, ids.length, 'group ids are unique');
});

test('the inventory groups both stores, sizes them as the browser counts, in the list order', () => {
  const rows = inventory(browser());
  assert.deepEqual(rows.map((r) => r.id), ['session', 'render', 'swatches', 'viking', 'tab', 'other']);
  const session = rows.find((r) => r.id === 'session');
  assert.equal(session.items.length, 2);
  const key = 'sbfsem.session.v1';
  assert.equal(session.items.find((i) => i.key === key).bytes, 2 * (key.length + '{"schema":1}'.length));
  assert.equal(rows.find((r) => r.id === 'tab').keep, true);
  assert.deepEqual(rows.find((r) => r.id === 'viking').items.map((i) => i.area), ['session']);
});

test('blocked or absent storage lists nothing and throws nothing', () => {
  assert.deepEqual(inventory({ local: new BlockedStorage(), session: null }), []);
  assert.deepEqual(inventory(), []);
});

test('a reset removes the chosen groups from their own store and never a kept one', () => {
  const stores = browser();
  const rows = inventory(stores);
  const removed = resetGroups(rows, new Set(['session', 'tab', 'other']), stores);
  assert.equal(removed, 3, 'two session items and the unknown one; the tab id is kept');
  assert.equal(stores.local.getItem('sbfsem.session.v1'), null);
  assert.equal(stores.local.getItem('sbfsem.session.v1.tab.k1-abc'), null);
  assert.equal(stores.local.getItem('someone.else'), null);
  assert.equal(stores.session.getItem('sbfsem.tab'), 'k1-abc');
  assert.equal(stores.local.getItem('sbfsem:swatches'), '["#ff0000"]', 'what was not ticked stays');
});

test('the page knows its build from its own module address', () => {
  assert.equal(pageAssets('https://sbfsem-tools.com/static/js/app.js?v=2.7.1-7d6b91d78be2'), '2.7.1-7d6b91d78be2');
  assert.equal(pageAssets('http://127.0.0.1:8765/static/js/app.js'), '');
  assert.equal(pageAssets('not a url'), '');
});

test('the build check says same, differs (what to do) or unknown', () => {
  const server = { version: '2.7.1', assets: '2.7.1-7d6b91d78be2', instance: 'a' };
  assert.equal(buildCheck('2.7.1-7d6b91d78be2', server).state, 'same');
  const stale = buildCheck('2.7.0-0000aaaa', server);
  assert.equal(stale.state, 'differs');
  assert.match(stale.text, /2\.7\.0-0000aaaa.*2\.7\.1-7d6b91d78be2/);
  assert.match(stale.text, /clear cached files/);
  assert.equal(buildCheck('x', null).state, 'unknown');
  assert.equal(buildCheck('', server).state, 'unknown');
});

test('sizes and durations read in the units people use', () => {
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(1536), '1.5 kB');
  assert.equal(formatBytes(4.7 * 1024 ** 3), '4.7 GB');
  assert.equal(formatBytes(65536 * 1024 ** 2), '64.0 GB');
  assert.equal(formatBytes(null), '—');
  assert.equal(formatDuration(42), '42 s');
  assert.equal(formatDuration(3 * 3600 + 120), '3 h 2 min');
  assert.equal(formatDuration(2 * 86400 + 5 * 3600), '2 d 5 h');
  assert.equal(formatDuration(undefined), '—');
});

test('a restart is back only on a new instance, and gives up after its wait', () => {
  assert.equal(restartPhase('old', { instance: 'old' }, 1), 'stopping');
  assert.equal(restartPhase('old', null, 4), 'down');
  assert.equal(restartPhase('old', { instance: 'new' }, 9), 'back');
  assert.equal(restartPhase('old', null, RESTART_WAIT_S), 'timeout');
  assert.equal(restartPhase('old', { instance: 'new' }, RESTART_WAIT_S + 5), 'back', 'back wins over late');
});

test('the server card reads the status the server sends', () => {
  const rows = new Map(serverRows({
    version: '2.7.2', assets: '2.7.2-abc', instance: 'i1', uptime_s: 7260, pid: 42, supervised: true,
    service_memory_bytes: 1553 * 1024 ** 2,
    memory: { neurons_loaded: 342, copies_held: 350, annotations_held: 1234567, meshes_cached: 12,
      mesh_cache_limit: 12, mesh_bytes: 300 * 1024 ** 2 },
    disk_cache: { files: 4012, bytes: 21 * 1024 ** 3, budget_bytes: 64 * 1024 ** 3 },
    pool: { build_processes: 4, pool_running: true, pool_recycles: 1 },
    builds: { running: 1, queued: 2, gate_waits: 3, gate_wait_max_s: 30.2 },
  }));
  assert.match(rows.get('Instance'), /up 2 h 1 min · pid 42 · run by systemd/);
  assert.match(rows.get('Memory'), /1\.5 GB for the service and its build processes/);
  assert.match(rows.get('Cells'), /342 loaded · 350 copies · 1,234,567 annotations/);
  assert.match(rows.get('Surfaces on disk'), /4,012 files · 21\.0 GB of 64\.0 GB/);
  assert.match(rows.get('Build processes'), /4 · running · replaced 1 time for memory/);
  assert.match(rows.get('Builds'), /1 running · 2 queued · 3 waits for memory \(longest 30\.2 s\)/);
  const local = new Map(serverRows({ version: 'v', assets: 'a', instance: 'i', uptime_s: 5, pid: 1,
    supervised: false, service_memory_bytes: null, memory: { process_rss_bytes: 1024 ** 3 },
    disk_cache: null, pool: { build_processes: 0 }, builds: null }));
  assert.match(local.get('Memory'), /1\.0 GB for this process/);
  assert.equal(local.get('Surfaces on disk'), 'no disk cache');
  assert.match(local.get('Build processes'), /none: builds run in the server process/);
  assert.match(local.get('Instance'), /not run by systemd/);
});

// -- the Correction log ------------------------------------------------------

test('the ratio box takes a number within the limits, and refuses what is not one', () => {
  assert.equal(parseRatio('4'), 4);
  assert.equal(parseRatio(' 2.5 '), 2.5);
  assert.equal(parseRatio('6×'), 6);
  assert.equal(parseRatio('0.2'), RATIO_LIMITS.min);
  assert.equal(parseRatio('500'), RATIO_LIMITS.max);
  assert.equal(parseRatio('3', { min: 1, max: 2 }), 2);
  for (const bad of ['', '  ', 'four', null, undefined, 'NaN']) assert.equal(parseRatio(bad), null, String(bad));
  assert.equal(RATIO_LIMITS.fallback, 4);
  assert.equal(groupOf(RATIO_KEY).id, 'correction-log', 'the saved ratio has a group');
});

test('expert render mode is off unless this browser keeps it on, and is listed with what is kept', () => {
  const store = new FakeStorage();
  assert.equal(expertRender(store), false);
  assert.equal(expertRender(null), false);
  assert.equal(expertRender(new BlockedStorage()), false);
  assert.equal(setExpertRender(store, true), true);
  assert.equal(store.getItem(EXPERT_RENDER_KEY), '1');
  assert.equal(expertRender(store), true);
  assert.equal(setExpertRender(store, false), true);
  assert.equal(store.getItem(EXPERT_RENDER_KEY), null, 'off is no entry at all');
  assert.equal(setExpertRender(new BlockedStorage(), true), false);
  assert.equal(groupOf(EXPERT_RENDER_KEY).id, 'expert-render');
  // The hover says both settings.
  assert.match(EXPERT_RENDER_HELP, /^On: render settings are applied verbatim/);
  assert.match(EXPERT_RENDER_HELP, /Off \(the default\)/);
});

test('a ratio is written as the log shows it', () => {
  assert.equal(formatRatio(13.42), '13×');
  assert.equal(formatRatio(4.35), '4.3×');
  assert.equal(formatRatio(4), '4×');
  assert.equal(formatRatio(null), '∞');
});

const LOG = {
  ratio: 4, default_ratio: 4, min_ratio: 1, max_ratio: 50, ratio_sent_to_viking: false,
  cells: [
    { source: 'NeitzInferiorMonkey', neuron_id: 1178, copy_tag: 'curvefit.1', mode: 'curvefit', checked: true,
      note: '', service_version: '1.2.0', stos_group: 'Grid16', n_annotations: 2436, n_moved: 2179,
      n_next_to_wider: 12, pulls: [
        { location_id: 1328721, z: 1160, r_um: 0.737, dx_um: -10.6, dy_um: 0.5, move_um: 10.645,
          neighbor_id: 1557441, neighbor_r_um: 9.887, ratio: 13.42, distance_um: 21.5, frac: 0.495, cos: 1.0,
          moved_with: [1328723] },
      ] },
    { source: 'RC1', neuron_id: 476, copy_tag: 'all.1', mode: 'all', checked: true, note: '',
      service_version: null, n_annotations: 10, n_moved: 9, n_next_to_wider: 1, pulls: [] },
    { source: 'RC1', neuron_id: 943, copy_tag: 'neighbor.1', mode: 'neighbor', checked: false,
      note: 'Not checked: this mode has no curve fit', n_annotations: 0, n_moved: 0, n_next_to_wider: 0, pulls: [] },
  ],
};

test('the log view names each cell with its volume and mode, and keeps the unchecked apart', () => {
  const view = correctionLogView(LOG, { volumeName: (s) => ({ NeitzInferiorMonkey: 'Inferior Monkey' })[s] || s });
  assert.equal(view.ratio, 4);
  assert.equal(view.checked.length, 2);
  const [c1178, c476] = view.checked;
  assert.deepEqual([c1178.volume, c1178.cellId, c1178.mode, c1178.service, c1178.copy],
    ['Inferior Monkey', 1178, 'Curve fit', '1.2.0', 'curvefit.1']);
  assert.match(c1178.summary, /^1 of the 12 moved annotations next to one 4× as wide or wider was pulled toward it$/);
  const [row] = c1178.rows;
  assert.deepEqual([row.locationId, row.z, row.r, row.move, row.neighborId, row.neighborR, row.ratio, row.frac],
    [1328721, '1160', '0.74', '10.64', 1557441, '9.89', '13×', '0.49']);
  assert.deepEqual(row.movedWith, [1328723]);
  assert.match(row.title, /0\.49 of the way there/);
  assert.equal(c476.mode, 'Field + fit');
  assert.equal(c476.service, '');
  assert.equal(c476.rows.length, 0);
  assert.match(c476.summary, /^None pulled: 1 moved annotation next to one 4× as wide or wider/);
  assert.deepEqual(view.unchecked.map((c) => [c.cellId, c.mode, c.note]),
    [[943, 'Residual field', 'Not checked: this mode has no curve fit']]);
});

test('an empty or missing log is drawn as nothing to show', () => {
  assert.deepEqual(correctionLogView({ ratio: 6, cells: [] }), { ratio: 6, checked: [], unchecked: [] });
  assert.deepEqual(correctionLogView(null), { ratio: 4, checked: [], unchecked: [] });
});

test('the saved ratio is the box value, or the default when there is none, it is not a number or the store refuses', () => {
  assert.equal(savedRatio(new FakeStorage({ [RATIO_KEY]: '6' })), 6);
  assert.equal(savedRatio(new FakeStorage({ [RATIO_KEY]: '500' })), RATIO_LIMITS.max);
  assert.equal(savedRatio(new FakeStorage({ [RATIO_KEY]: 'four' })), 4);
  assert.equal(savedRatio(new FakeStorage()), 4);
  assert.equal(savedRatio(null), 4);
  assert.equal(savedRatio({ getItem() { throw new Error('SecurityError'); } }), 4);
});

test('a pull in words names what moved with it', () => {
  const [pull] = LOG.cells[0].pulls;
  assert.equal(pullTitle(pull), 'Location 1328721 (r 0.74 µm, section 1160) moved 10.64 µm, cos 1.00 toward '
    + '1557441 (r 9.89 µm, 21.50 µm away): 0.49 of the way there; 1328723 moved with it');
  assert.equal(pullTitle({ ...pull, moved_with: [] }).endsWith('0.49 of the way there'), true);
});

test('a cell panel shows its pulls with a count, and nothing without a checked pull', () => {
  const answer = { ...LOG.cells[0], ratio: 4, ratio_sent_to_viking: false };
  const view = cellPullsView(answer);
  assert.equal(view.count, '1 pulled');
  assert.equal(view.line, 'Curve fit pulled 1 annotation toward one 4× as wide or wider:');
  assert.deepEqual(view.ids.map((i) => i.locationId), [1328721]);
  assert.equal(view.ids[0].title, pullTitle(answer.pulls[0]));
  const two = cellPullsView({ ...answer, ratio: 13.42, pulls: [answer.pulls[0], { ...answer.pulls[0], location_id: 7 }] });
  assert.equal(two.line, 'Curve fit pulled 2 annotations toward one 13× as wide or wider:');
  assert.equal(two.count, '2 pulled');
  assert.equal(cellPullsView({ ...LOG.cells[1], ratio: 4 }), null, 'none pulled');
  assert.equal(cellPullsView({ ...LOG.cells[2], ratio: 4 }), null, 'not checked');
  assert.equal(cellPullsView({ ...answer, checked: false }), null, 'pulls of an unchecked copy are not shown');
  assert.equal(cellPullsView(null), null);
  assert.match(PULLS_HELP, /halfway when its neighbors are a section either side/);
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
