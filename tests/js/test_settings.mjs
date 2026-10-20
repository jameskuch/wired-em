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
