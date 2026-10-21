// The per-call copy of the measurement jobs (static/js/api.js `deviationJob` and
// `diffJob`).  A tab names the copy of a cell it loaded as `?copy=` on every
// request for the cell, automatically; a layer on another copy (a surface before
// and after a setting that moves the annotations) names its own, and a
// difference across two copies names the second one in the body.  Run it with
//
//     node tests/js/test_apicopy.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free, against a fake fetch; the poll timing is shrunk so the file runs in a
// fraction of a second.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const { api, POLL_TIMING } = await import(pathToFileURL(path.join(jsDir, 'api.js')).href);
Object.assign(POLL_TIMING, { first: 1, every: 2, giveUp: 60000, requestTimeout: 0 });

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** A server that starts a job, finishes it at the first poll, and says which copy it loaded. */
function server({ loadedCopy = 'off.1', result = { mesh_id: 'm1', n_vertices: 3 } } = {}) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    const [route] = String(url).split('?');
    calls.push({ method, url: String(url), route, body: options.body ? JSON.parse(options.body) : null });
    let body = {};
    if (method === 'POST' && route === '/api/neurons') {
      body = { summary: { source: 'i', id: 943, copy_tag: loadedCopy } };
    } else if (method === 'POST') {
      body = { job_id: 'j1', status: 'queued', kind: 'diff', weight_mb: 1 };
    } else if (route.startsWith('/api/mesh-jobs/')) {
      body = { job_id: 'j1', status: 'done', kind: 'diff', mesh: null, result };
    }
    return { ok: true, status: 200, statusText: '', json: async () => body };
  };
  return calls;
}

/** The request that started the job (the one POST to a *-job route). */
const starts = (calls) => calls.filter((c) => c.method === 'POST' && /-job$/.test(c.route));

const A = { method: 'cylinder', pathway: 'voxel2' };
const B = { method: 'cylinder', pathway: 'original' };

test('without a copy of its own, a job asks for the copy this tab loaded', async () => {
  const calls = server({ loadedCopy: 'off.1' });
  await api.load({ id: 943, source: 'i' });
  await api.deviationJob('i', 943, A);
  await api.diffJob('i', 943, { a: A, b: B });
  const [dev, diff] = starts(calls);
  assert.equal(dev.url, '/api/neurons/i/943/mesh-deviation-job?copy=off.1');
  assert.equal(diff.url, '/api/neurons/i/943/mesh-diff-job?copy=off.1');
  assert.deepEqual(diff.body, { a: A, b: B });          // no copy_b: both meshes are of this copy
});

test('a deviation job names its own copy once, in place of the tab\'s', async () => {
  const calls = server({ loadedCopy: 'off.1' });
  await api.load({ id: 943, source: 'i' });
  await api.deviationJob('i', 943, A, { copy: 'off.0' });
  const [dev] = starts(calls);
  assert.equal(dev.url, '/api/neurons/i/943/mesh-deviation-job?copy=off.0');
  assert.deepEqual(dev.body, A);
  // The polls follow the job by id: they carry no copy.
  assert.ok(calls.filter((c) => c.route === '/api/mesh-jobs/j1').every((c) => !c.url.includes('copy=')));
});

test('a difference names mesh A\'s copy in the query and mesh B\'s in the body', async () => {
  const calls = server({ loadedCopy: 'off.1' });
  await api.load({ id: 943, source: 'i' });
  await api.diffJob('i', 943, { a: A, b: B, sign: 'enclosure', both: true },
    { copy: 'off.0', copyB: 'off.1' });
  const [diff] = starts(calls);
  assert.equal(diff.url, '/api/neurons/i/943/mesh-diff-job?copy=off.0');
  assert.deepEqual(diff.body, { a: A, b: B, sign: 'enclosure', both: true, copy_b: 'off.1' });
});

test('the caller\'s body is not changed to carry the second copy', async () => {
  server();
  const body = { a: A, b: B };
  await api.diffJob('i', 943, body, { copyB: 'off.0' });
  assert.deepEqual(body, { a: A, b: B });
});

test('a tag is escaped, and an empty one is no copy at all', async () => {
  const calls = server({ loadedCopy: 'off.1' });
  await api.load({ id: 943, source: 'i' });
  await api.deviationJob('i', 943, A, { copy: 'we ird&tag.1' });
  await api.deviationJob('i', 943, A, { copy: '' });
  await api.diffJob('i', 943, { a: A, b: B }, { copyB: '' });
  const [escaped, empty, noSecond] = starts(calls);
  assert.equal(escaped.url, '/api/neurons/i/943/mesh-deviation-job?copy=we%20ird%26tag.1');
  assert.equal(empty.url, '/api/neurons/i/943/mesh-deviation-job?copy=off.1');   // the tab's, as before
  assert.equal('copy_b' in noSecond.body, false);
});

test('a bad sign is still refused before anything is sent', async () => {
  const calls = server();
  await assert.rejects(() => api.diffJob('i', 943, { a: A, b: B, sign: 'inward' }, { copyB: 'off.0' }),
    /sign of a difference/);
  assert.equal(starts(calls).length, 0);
});

test('a mesh build names its own copy by putting copy in its options', async () => {
  // Nothing was added to api.mesh for a layer on another copy: its options already travel as the
  // query, and withCopy leaves a path that names a copy alone.  This pins that, so a change to
  // either does not silently send a layer's build to the tab's copy.
  const calls = server({ loadedCopy: 'off.1', result: null });
  const mesh = api.mesh('i', 943, { pathway: 'voxel2', copy: 'off.0' });
  await mesh.catch(() => {});                           // the fake job has no mesh: only the URL matters
  const [start] = calls.filter((c) => c.route === '/api/neurons/i/943/mesh-job');
  const query = new URL(start.url, 'http://x').searchParams;
  assert.deepEqual(query.getAll('copy'), ['off.0']);
});

test('a build estimate asks for the tab\'s copy, quietly, and says why when it cannot', async () => {
  const { onBusy } = await import(pathToFileURL(path.join(jsDir, 'api.js')).href);
  const calls = server({ loadedCopy: 'curvefit.1' });
  await api.load({ id: 943, source: 'i' });
  const heard = [];
  const stop = onBusy((note) => heard.push(note));
  await api.renderEstimate('i', 943, { method: 'closedcurve', pixel_size_um: 0.03 });
  stop();
  const [asked] = calls.filter((c) => c.route === '/api/neurons/i/943/render-estimate');
  const query = new URL(asked.url, 'http://x').searchParams;
  assert.equal(query.get('copy'), 'curvefit.1');
  assert.equal(query.get('pixel_size_um'), '0.03');
  assert.deepEqual(heard, [], 'asked while a size is typed: never on the busy line');
  globalThis.fetch = async () => ({ ok: false, status: 404, statusText: 'Not Found',
    json: async () => ({ detail: 'c943 is not loaded' }) });
  await assert.rejects(() => api.renderEstimate('i', 943, {}), /c943 is not loaded/);
});

test('another registration\'s copy is loaded without becoming the tab\'s, and its annotations are asked of it', async () => {
  const calls = server({ loadedCopy: 'all.1' });
  await api.load({ id: 943, source: 'i' });
  // Render compare loads a layer's copy (registrations.js copyLoadBody): the answer names it ...
  globalThis.fetch = (wrap => async (url, options = {}) => {
    const answer = await wrap(url, options);
    if ((options.method || 'GET') === 'POST' && String(url) === '/api/neurons') {
      return { ...answer, json: async () => ({ summary: { source: 'i', id: 943, copy_tag: 'off.0' } }) };
    }
    return answer;
  })(globalThis.fetch);
  const detail = await api.loadCopy({ id: 943, source: 'i', live_registration: false, section_corrections: 'off' });
  assert.equal(detail.summary.copy_tag, 'off.0');
  const [, second] = calls.filter((c) => c.method === 'POST' && c.route === '/api/neurons');
  assert.equal('manual_moves' in second.body, false, 'the copy is the setting\'s plain one');
  // ... and the tab's own requests still name the copy it loaded.
  await api.deviationJob('i', 943, A);
  assert.equal(starts(calls).at(-1).url, '/api/neurons/i/943/mesh-deviation-job?copy=all.1');
  await api.nodes('i', 943, true, { copy: 'off.0' });
  await api.closedCurves('i', 943, { copy: 'off.0' });
  await api.nodes('i', 943);
  const asked = calls.filter((c) => /\/(nodes|closed-curves)$/.test(c.route))
    .map((c) => new URL(c.url, 'http://x').searchParams.getAll('copy'));
  assert.deepEqual(asked, [['off.0'], ['off.0'], ['all.1']]);
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack}`);
  }
}
console.log(`${cases.length - failed}/${cases.length} passed`);
process.exit(failed ? 1 : 0);
