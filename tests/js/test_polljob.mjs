// pollJob and the job calls built on it (static/js/api.js), against a fake
// fetch.  There is no JS runner in this repo: run it with
//
//     node tests/js/test_polljob.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free; the poll timing is shrunk so the whole file runs in well under a
// second.  Nothing here depends on how fast the machine is: the give-up
// window is measured on a fake clock the scripted server advances, never on
// the wall clock.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const {
  api, ApiError, ConnectionLostError, DownloadFailedError, pollJob, POLL_TIMING, pollWaitFor,
  MESH_MEDIA_TYPE, sizeText, progressText, onBusy,
} = await load('api.js');
// What the module chose for a page with no navigation entry (node): no wait.
const LOADED_WAIT = POLL_TIMING.wait;
const { stageText } = await load('loadprogress.js');

// A generous window and no per-poll timeout unless a case asks for them.
const BASE_TIMING = {
  first: 1, every: 2, wait: 0, giveUp: 60000, requestTimeout: 0, stallTimeout: 0, downloadTries: 3,
  now: () => Date.now(),
};
Object.assign(POLL_TIMING, BASE_TIMING);

/** A clock the scripted server moves forward `step` ms at every request. */
function fakeClock(step) {
  const clock = { t: 1_000_000, step };
  POLL_TIMING.now = () => clock.t;
  return clock;
}

/** A scripted server.  `routes` maps "METHOD path-without-query" to a list of
 * replies, consumed in order (the last repeats): `{ status, body }` or an
 * Error to throw (a network failure). */
function server(routes, { clock } = {}) {
  const calls = [];
  const counters = new Map();
  globalThis.fetch = async (url, options = {}) => {
    if (clock) clock.t += clock.step;
    const method = options.method || 'GET';
    const path = String(url).split('?')[0];
    calls.push({ method, url: String(url), body: options.body });
    const key = `${method} ${path}`;
    const replies = routes[key];
    if (!replies) throw new Error(`unscripted request: ${key}`);
    const n = counters.get(key) || 0;
    counters.set(key, n + 1);
    const reply = replies[Math.min(n, replies.length - 1)];
    if (reply instanceof Error) throw reply;
    // A function answers for itself (a request that hangs until its signal).
    if (typeof reply === 'function') return reply(options);
    const { status = 200, body = {} } = reply;
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: '',
      json: async () => body,
    };
  };
  return calls;
}

const running = (extra = {}) => ({
  body: {
    job_id: 'j1', status: 'running', waiting: '', kind: 'mesh', seconds: 1.2,
    progress: { stage: 'contouring', done: 3, total: 10 }, ...extra,
  },
});
const done = (extra = {}) => ({
  body: { job_id: 'j1', status: 'done', kind: 'mesh', mesh: { n_faces: 7, mesh_id: 'm1' }, ...extra },
});
const started = { body: { job_id: 'j1', status: 'queued', kind: 'mesh', weight_mb: 10 } };

// AbortSignal.timeout() does not keep node's event loop alive, so a case that
// waits on one would let the process exit mid-await: hold the loop open.
const keepAlive = setInterval(() => {}, 1000);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const nextTick = (ms = 5) => new Promise((resolve) => setTimeout(resolve, ms));

/** Fail (rather than hang the file) if `promise` has not settled in `ms`. */
function within(ms, promise) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new assert.AssertionError({ message: `still waiting after ${ms} ms` })), ms);
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

/** A request that never answers unless its signal is aborted (a hung connection). */
const hang = (options) => (options.signal
  ? new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason)))
  : new Promise(() => {}));

async function rejects(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  return assert.fail('expected a rejection');
}

test('mesh: queued, running, done returns the mesh and reports every poll', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [
      { body: { job_id: 'j1', status: 'queued', waiting: 'worker', kind: 'mesh' } },
      running(), done(),
    ],
  });
  const seen = [];
  const mesh = await api.mesh('i', 1, { pathway: 'voxel2' }, { onStatus: (j) => seen.push(j.status) });
  assert.deepEqual(mesh, { n_faces: 7, mesh_id: 'm1' });
  assert.deepEqual(seen, ['queued', 'running', 'done']);
  assert.equal(calls.filter((c) => c.method === 'GET' && c.url.includes('mesh-job?')).length, 1);
  assert.ok(calls[0].url.includes('pathway=voxel2'));
});

test('error: the message names the failed stage and the server text', async () => {
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), {
      body: { job_id: 'j1', status: 'error', detail: 'out of memory', failed_stage: 'isotropic remesh' },
    }],
  });
  const error = await rejects(api.mesh('i', 1));
  assert.ok(error instanceof ApiError);
  assert.equal(error.detail, 'failed during isotropic remesh: out of memory');
  assert.equal(error.failedStage, 'isotropic remesh');
  assert.equal(error.serverDetail, 'out of memory');
  assert.ok(!error.aborted);
});

test('error without a stage: the server text alone', async () => {
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [{ body: { job_id: 'j1', status: 'error', detail: 'bad', failed_stage: '' } }],
  });
  const error = await rejects(api.mesh('i', 1));
  assert.equal(error.detail, 'bad');
});

test('cancelled: rejects as aborted, quietly', async () => {
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [{ body: { job_id: 'j1', status: 'cancelled' } }],
  });
  const error = await rejects(api.mesh('i', 1));
  assert.equal(error.aborted, true);
  assert.equal(error.message, 'c1 build cancelled');
});

test('404 on a poll: resubmits once and follows the new job', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [
      started, { body: { job_id: 'j2', status: 'queued', kind: 'mesh' } }],
    'GET /api/mesh-jobs/j1': [{ status: 404, body: { detail: 'Not Found' } }],
    'GET /api/mesh-jobs/j2': [done({ job_id: 'j2' })],
  });
  const mesh = await api.mesh('i', 1);
  assert.equal(mesh.n_faces, 7);
  assert.equal(calls.filter((c) => c.url.includes('/mesh-job?') || c.url.endsWith('/mesh-job')).length, 2);
});

test('404 twice: gives up with a message that says so', async () => {
  server({
    'GET /api/neurons/i/1/mesh-job': [started, { body: { job_id: 'j2', status: 'queued' } }],
    'GET /api/mesh-jobs/j1': [{ status: 404, body: {} }],
    'GET /api/mesh-jobs/j2': [{ status: 404, body: {} }],
  });
  const error = await rejects(api.mesh('i', 1));
  assert.equal(error.status, 404);
  assert.match(error.detail, /no longer has this job \(it lost it twice\)/);
  // A restored or batch-loaded cell has no button to press.
  assert.doesNotMatch(error.detail, /button/);
});

test('a start promise cannot be resubmitted: a 404 is an error', async () => {
  server({ 'GET /api/mesh-jobs/j1': [{ status: 404, body: {} }] });
  const error = await rejects(pollJob(Promise.resolve({ job_id: 'j1' }), { what: 'thing' }));
  assert.equal(error.status, 404);
  assert.match(error.detail, /thing: the server no longer has this job\./);
  assert.doesNotMatch(error.detail, /button/);
});

test('transient failures: network errors and 502/503/504 are retried, with reconnecting reports', async () => {
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [
      running(), new TypeError('network down'), { status: 502, body: {} },
      { status: 503, body: {} }, { status: 504, body: {} }, running({ seconds: 9 }), done(),
    ],
  });
  const seen = [];
  const mesh = await api.mesh('i', 1, {}, { onStatus: (j) => seen.push(j) });
  assert.equal(mesh.n_faces, 7);
  const reconnecting = seen.filter((j) => j.reconnecting);
  assert.equal(reconnecting.length, 4);
  // The last known job is carried so a progress display keeps its numbers.
  assert.equal(reconnecting[0].status, 'running');
  assert.equal(reconnecting[0].progress.stage, 'contouring');
  assert.equal(typeof reconnecting[0].reconnectSeconds, 'number');
  assert.match(reconnecting[0].error, /network down/);
});

test('failures that last past the limit end the follow with a lost-contact error and release the job', async () => {
  const clock = fakeClock(25000);
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [new TypeError('network down')],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  }, { clock });
  const error = await rejects(api.mesh('i', 1));
  assert.ok(error instanceof ConnectionLostError);
  assert.ok(error instanceof ApiError);
  assert.equal(error.status, 0);
  assert.match(error.detail, /lost contact with the server for \d+ s while c1 build was running/);
  assert.match(error.detail, /may still finish/);
  await nextTick();
  assert.equal(calls.filter((c) => c.method === 'DELETE' && c.url === '/api/mesh-jobs/j1').length, 1);
});

test('a poll that keeps answering 504 does not blame a build timeout', async () => {
  const clock = fakeClock(25000);
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [{ status: 504, body: {} }],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  }, { clock });
  const error = await rejects(api.mesh('i', 1));
  assert.ok(error instanceof ConnectionLostError);
  assert.doesNotMatch(error.detail, /504|gateway|too long to build/);
});

test('a hung poll times out as a failed poll and is retried', async () => {
  Object.assign(POLL_TIMING, { requestTimeout: 20 });
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [hang, done()],
  });
  const seen = [];
  const mesh = await within(3000, api.mesh('i', 1, {}, { onStatus: (j) => seen.push(j) }));
  assert.equal(mesh.n_faces, 7);
  assert.equal(seen.filter((j) => j.reconnecting).length, 1);
});

test('a hung poll does not hold alive(): the follow stands down after the timeout', async () => {
  Object.assign(POLL_TIMING, { requestTimeout: 20 });
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [hang],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  });
  let wanted = true;
  const promise = api.mesh('i', 1, {}, { alive: () => wanted });
  await nextTick(5);
  wanted = false;
  const error = await within(3000, rejects(promise));
  assert.equal(error.aborted, true);
  assert.equal(error.message, 'c1 build abandoned');
  await nextTick();
  assert.equal(calls.filter((c) => c.method === 'DELETE').length, 1);
});

test('a real error status from a poll (500) is not retried', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [{ status: 500, body: { detail: 'boom' } }],
  });
  const error = await rejects(api.mesh('i', 1));
  assert.equal(error.status, 500);
  assert.equal(error.detail, 'boom');
  assert.equal(calls.filter((c) => c.method === 'GET' && c.url.endsWith('/api/mesh-jobs/j1')).length, 1);
  // The job is released, not left queued for nobody.
  await nextTick();
  assert.equal(calls.filter((c) => c.method === 'DELETE' && c.url === '/api/mesh-jobs/j1').length, 1);
});

test('404 while the cell is no longer wanted: no second job is started', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started, { body: { job_id: 'j2', status: 'queued' } }],
    'GET /api/mesh-jobs/j1': [{ status: 404, body: { detail: 'Not Found' } }],
    'DELETE /api/mesh-jobs/j2': [{ body: {} }],
  });
  let checks = 0;
  // Wanted at the poll's turn, gone by the time the 404 comes back.
  const alive = () => { checks += 1; return checks < 3; };
  const error = await rejects(api.mesh('i', 1, {}, { alive }));
  assert.equal(error.aborted, true);
  assert.equal(error.message, 'c1 build abandoned');
  assert.equal(calls.filter((c) => c.url.includes('mesh-job') && c.method === 'GET'
    && !c.url.includes('mesh-jobs')).length, 1, 'the start request was not repeated');
});

test('an already-aborted signal sends nothing at all', async () => {
  const calls = server({});
  const controller = new AbortController();
  controller.abort();
  let started = false;
  const error = await rejects(pollJob(() => { started = true; return Promise.resolve({ job_id: 'x' }); },
    { signal: controller.signal }));
  assert.equal(error.aborted, true);
  assert.equal(started, false);
  assert.equal(calls.length, 0);
});

test('a cell that is already gone starts no job', async () => {
  const calls = server({});
  const error = await rejects(api.mesh('i', 1, {}, { alive: () => false }));
  assert.equal(error.aborted, true);
  assert.equal(calls.length, 0);
});

test('abort: a signal sends DELETE and rejects as aborted', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running()],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  });
  const controller = new AbortController();
  const promise = api.mesh('i', 1, {}, { signal: controller.signal });
  await nextTick(12);
  controller.abort();
  const error = await rejects(promise);
  assert.equal(error.aborted, true);
  await nextTick();
  assert.equal(calls.filter((c) => c.method === 'DELETE' && c.url === '/api/mesh-jobs/j1').length, 1);
});

test('abort while the start request is in the air cancels the job it starts', async () => {
  const calls = server({ 'DELETE /api/mesh-jobs/late': [{ body: {} }] });
  const controller = new AbortController();
  let release;
  const start = () => new Promise((resolve) => { release = () => resolve({ job_id: 'late' }); });
  const promise = pollJob(start, { signal: controller.signal });
  await nextTick();
  controller.abort();
  const error = await rejects(promise);
  assert.equal(error.aborted, true);
  release();
  await nextTick();
  assert.ok(calls.some((c) => c.method === 'DELETE' && c.url === '/api/mesh-jobs/late'));
});

test('alive() false (the cell was unloaded): DELETE and the old abandoned rejection', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running()],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  });
  let polls = 0;
  const alive = () => { polls += 1; return polls < 3; };
  const error = await rejects(api.mesh('i', 1, {}, { alive }));
  assert.equal(error.aborted, true);
  assert.equal(error.message, 'c1 build abandoned');
  await nextTick();
  assert.equal(calls.filter((c) => c.method === 'DELETE').length, 1);
});

test('deviationJob: POST body, then the result; requests carry ?copy=', async () => {
  const calls = server({
    'GET /api/neurons/i/5': [{ body: { summary: { source: 'i', id: 5, copy_tag: 'abc' } } }],
    'POST /api/neurons/i/5/mesh-deviation-job': [
      { body: { job_id: 'j1', status: 'queued', kind: 'deviation', weight_mb: 1 } }],
    'GET /api/mesh-jobs/j1': [
      running({ kind: 'deviation' }),
      { body: { job_id: 'j1', status: 'done', kind: 'deviation', result: { mesh_id: 'm9', n_vertices: 3 } } },
    ],
  });
  await api.detail('i', 5);
  const result = await api.deviationJob('i', 5, { pathway: 'voxel2', meshlab: 'isotropic' });
  assert.deepEqual(result, { mesh_id: 'm9', n_vertices: 3 });
  const post = calls.find((c) => c.method === 'POST');
  assert.ok(post.url.endsWith('/mesh-deviation-job?copy=abc'), post.url);
  assert.deepEqual(JSON.parse(post.body), { pathway: 'voxel2', meshlab: 'isotropic' });
});

test('diffJob: POST {a, b}, then the result', async () => {
  const calls = server({
    'POST /api/neurons/i/6/mesh-diff-job': [{ body: { job_id: 'j1', status: 'queued', kind: 'diff' } }],
    'GET /api/mesh-jobs/j1': [
      { body: { job_id: 'j1', status: 'done', kind: 'diff', result: { mesh_id: 'm1', distance_um: [0.1] } } }],
  });
  const result = await api.diffJob('i', 6, { a: { pathway: 'voxel' }, b: { pathway: 'original' } });
  assert.deepEqual(result.distance_um, [0.1]);
  const post = calls.find((c) => c.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), { a: { pathway: 'voxel' }, b: { pathway: 'original' } });
});

test('diffJob: the sign rule and both ways go to the server as given, and only as given', async () => {
  const result = { mesh_id: 'm1', distance_um: [-0.1], sign_by: 'enclosure', distance_b_um: [0.2] };
  const calls = server({
    'POST /api/neurons/i/6/mesh-diff-job': [{ body: { job_id: 'j1', status: 'queued', kind: 'diff' } }],
    'GET /api/mesh-jobs/j1': [{ body: { job_id: 'j1', status: 'done', kind: 'diff', result } }],
  });
  const body = { a: { pathway: 'voxel2' }, b: { pathway: 'original' }, sign: 'enclosure', both: true };
  const found = await api.diffJob('i', 6, body);
  assert.deepEqual(found.distance_b_um, [0.2]);
  const post = calls.find((c) => c.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), body);
  // A sign the server does not know is refused here, before a request is made.
  const before = calls.length;
  const error = await rejects(api.diffJob('i', 6, { ...body, sign: 'inward' }));
  assert.match(error.detail, /one of normal or enclosure/);
  assert.equal(calls.length, before, 'nothing was sent');
});

test('a job that finished without a result is an error, not undefined', async () => {
  server({
    'POST /api/neurons/i/7/mesh-deviation-job': [{ body: { job_id: 'j1' } }],
    'GET /api/mesh-jobs/j1': [{ body: { job_id: 'j1', status: 'done', kind: 'deviation', result: null } }],
  });
  const error = await rejects(api.deviationJob('i', 7, {}));
  assert.match(error.detail, /without a result/);
});

test('a status callback that throws does not end the follow', async () => {
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), done()],
  });
  const logged = [];
  const original = console.error;
  console.error = (...args) => logged.push(args);
  try {
    const mesh = await api.mesh('i', 1, {}, { onStatus: () => { throw new Error('ui bug'); } });
    assert.equal(mesh.n_faces, 7);
  } finally {
    console.error = original;
  }
  assert.ok(logged.length >= 1);
});

test('stageText: says so while the server does not answer', () => {
  const lost = { status: 'running', progress: { stage: 'contouring', done: 3, total: 10 }, seconds: 12,
    reconnecting: true, reconnectSeconds: 14 };
  assert.equal(stageText(lost), 'no answer from the server for 14 s, retrying');
  assert.equal(stageText({ ...lost, reconnectSeconds: 0 }), 'no answer from the server, retrying');
  // Not the frozen last stage and clock.
  assert.doesNotMatch(stageText(lost), /contouring|12 s/);
});

test('stageText: a count needs both numbers (done or total may be null)', () => {
  const at = (progress) => stageText({ status: 'running', progress, seconds: 0 });
  assert.equal(at({ stage: 'contouring', done: 3, total: 1830 }), 'contouring 3 of 1,830');
  assert.equal(at({ stage: 'MeshLab remesh', done: null, total: null }), 'MeshLab remesh');
  assert.equal(at({ stage: 'contouring', done: null, total: 5 }), 'contouring');
  assert.equal(at({ stage: 'contouring', done: undefined, total: 5 }), 'contouring');
  assert.equal(at({ stage: 'contouring', done: 5, total: null }), 'contouring');
  assert.equal(stageText({ status: 'queued', waiting: 'memory' }), 'waiting for server memory');
});

// -- polls that wait on the server (2026-10-20) ----------------------------------------------

test('pollWaitFor: polls wait over HTTP/2 and HTTP/3 only (six HTTP/1.1 connections would be held)', () => {
  assert.equal(pollWaitFor('h2'), 1);
  assert.equal(pollWaitFor('h3'), 1);
  assert.equal(pollWaitFor('http/1.1'), 0);
  assert.equal(pollWaitFor(''), 0);
  assert.equal(pollWaitFor(undefined), 0);
  assert.equal(LOADED_WAIT, 0, 'a page that cannot say how it came does not wait');
});

test('without wait: polls carry no wait and follow the interval, as before', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), done()],
  });
  await api.mesh('i', 1);
  const polls = calls.filter((c) => c.url.startsWith('/api/mesh-jobs/'));
  assert.deepEqual(polls.map((c) => c.url), ['/api/mesh-jobs/j1', '/api/mesh-jobs/j1']);
});

test('wait: every poll asks the server to hold it, the first goes at once, and a held poll is followed at once', async () => {
  // Each request moves the clock a full second: the server held every poll until it had news.
  const clock = fakeClock(1000);
  Object.assign(POLL_TIMING, { first: 400, every: 400, wait: 1 });
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), running(), running(), done()],
  }, { clock });
  const t0 = Date.now();
  const mesh = await within(2000, api.mesh('i', 1));
  const wall = Date.now() - t0;
  assert.equal(mesh.n_faces, 7);
  const polls = calls.filter((c) => c.url.startsWith('/api/mesh-jobs/'));
  assert.equal(polls.length, 4);
  assert.ok(polls.every((c) => c.url === '/api/mesh-jobs/j1?wait=1'), polls.map((c) => c.url).join(' '));
  // No `first` and no interval between them: one 400 ms pause would show.
  assert.ok(wall < 300, `four held polls took ${wall} ms of pauses`);
});

test('wait: a poll answered at once (a server that does not wait) is followed after the interval', async () => {
  const clock = fakeClock(0);
  Object.assign(POLL_TIMING, { every: 150, wait: 1 });
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), running(), done()],
  }, { clock });
  const t0 = Date.now();
  await within(3000, api.mesh('i', 1));
  const wall = Date.now() - t0;
  assert.ok(wall >= 280, `two answered-at-once polls were followed after ${wall} ms, not two intervals`);
});

test('wait: a failed poll is still retried after the interval, not at once', async () => {
  const clock = fakeClock(1000);
  Object.assign(POLL_TIMING, { every: 200, wait: 1 });
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), new TypeError('network down'), running(), done()],
  }, { clock });
  const t0 = Date.now();
  await within(3000, api.mesh('i', 1));
  const wall = Date.now() - t0;
  assert.ok(wall >= 180 && wall < 600, `one interval after the failure: ${wall} ms`);
});

test('onSubmitted: called once with the job id when the server has taken it, before any poll; a throw does not end the follow', async () => {
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), done()],
  });
  const seen = [];
  const mesh = await api.mesh('i', 1, {}, {
    onSubmitted: (jobId) => {
      seen.push([jobId, calls.filter((c) => c.url.startsWith('/api/mesh-jobs/')).length]);
      throw new Error('a careless callback');
    },
  });
  assert.equal(mesh.n_faces, 7);
  assert.deepEqual(seen, [['j1', 0]], 'once, with no poll sent yet');
});

test('the old synchronous POSTs are gone from the API object', () => {
  assert.equal(api.meshDiff, undefined);
  assert.equal(api.meshDeviation, undefined);
});

// -- a finished surface that takes long to download (2026-10-27) ------------------------------
// RC1 c9025 at Voxel 0.02 um: 8,539,810 triangles, a 154 MB reply, 50 MB on the wire.  At
// 0.7 MB/s a 20 s limit on each poll cut it off at 12 to 15 MB four times, and the page
// said the build "may still finish" although it had finished.

/** A finished mesh job's binary reply (mesh_binary.py's layout): 4 vertices and
 * `faces` triangles, no vertex_data. */
function binaryReply(faces = 2000) {
  const head = {
    job_id: 'j1', status: 'done', kind: 'mesh', seconds: 40.9,
    mesh: { mesh_id: 'm1', n_faces: faces, n_vertices: 4, vertices: null, faces: null, vertex_data: null },
    arrays: { vertices: [0, 12], faces: [48, faces * 3], vertex_data: null },
  };
  let text = JSON.stringify(head);
  text += ' '.repeat((8 - ((4 + text.length) % 8)) % 8);
  const bytes = new Uint8Array(4 + text.length + 48 + faces * 12);
  new DataView(bytes.buffer).setUint32(0, text.length, true);
  bytes.set(new TextEncoder().encode(text), 4);
  new Int32Array(bytes.buffer, 4 + text.length, 12).set(Array.from({ length: 12 }, (_, k) => k * 10000));
  return bytes;
}

/**
 * A reply whose body arrives in `pieces` pieces `gap` ms apart, as a download does,
 * and errors when the request's signal fires.  After `stopAfter` pieces it stops
 * arriving and never ends (a stalled download); `breakAfter` errors it there instead
 * (a connection reset).  Each piece moves `clock` on by `tick` ms.  `asked` collects
 * each request's options.
 */
function streamed(bytes, {
  pieces = 10, gap = 2, stopAfter = Infinity, breakAfter = Infinity, clock = null, tick = 0, asked = null,
  type = MESH_MEDIA_TYPE,
} = {}) {
  return (options) => {
    asked?.push(options);
    const size = Math.ceil(bytes.byteLength / pieces);
    let timer = null;
    let ended = false;
    let at = 0;
    let sent = 0;
    const body = new ReadableStream({
      start(controller) {
        const fail = (reason) => {
          clearTimeout(timer);
          if (!ended) { ended = true; controller.error(reason); }
        };
        options.signal?.addEventListener('abort', () => fail(options.signal.reason));
        const next = () => {
          if (ended) return;
          if (sent >= breakAfter) { fail(new TypeError('network error')); return; }
          if (sent >= stopAfter) return;
          if (at >= bytes.byteLength) { ended = true; controller.close(); return; }
          if (clock) clock.t += tick;
          controller.enqueue(bytes.slice(at, at + size));
          at += size;
          sent += 1;
          timer = setTimeout(next, gap);
        };
        timer = setTimeout(next, gap);
      },
    });
    return new Response(body, { status: 200, headers: { 'content-type': type } });
  };
}

const polls = (calls) => calls.filter((c) => c.method === 'GET' && c.url.startsWith('/api/mesh-jobs/'));

test('sizes read as nginx counts them, and a download as part of its whole', () => {
  assert.equal(sizeText(153707436), '154 MB');
  assert.equal(sizeText(4300000), '4.3 MB');
  assert.equal(sizeText(24480), '24 kB');
  assert.equal(sizeText(12), '1 kB');
  assert.equal(progressText(15260874, 153707436), '15 of 154 MB');
  assert.equal(progressText(2500000, 7200000), '2.5 of 7.2 MB');
  assert.equal(progressText(9800, 24480), '9 of 24 kB');
  assert.equal(progressText(12000000, null), '12 MB');
  // Rounded down: one byte short of the end is not the whole.
  assert.equal(progressText(1591440, 1591593), '1.5 of 1.6 MB');
  assert.equal(progressText(153707435, 153707436), '153 of 154 MB');
  assert.equal(progressText(1591593, 1591593), '1.6 of 1.6 MB', 'and the whole reads as its size');
});

test('the message for c9025: the surface was built, how big it is, and that the download failed', () => {
  const error = new DownloadFailedError('c9025 build', {
    mesh: true, faces: 8539810, total: 153707436, received: 15260874, tries: 3,
  });
  assert.ok(error instanceof ApiError);
  assert.equal(error.status, 0);
  assert.equal(error.detail, 'c9025 build finished on the server (8,539,810 triangles, 154 MB), but '
    + 'downloading the surface failed: it broke off 3 times, at most 15 of 154 MB in. The server keeps '
    + 'it, so asking for it again downloads it without building it again; a coarser setting makes a '
    + 'smaller surface.');
  assert.doesNotMatch(error.detail, /lost contact|may still finish|running/);
  // Not a failure of the settings: the render fallback leaves it alone (it reads serverDetail).
  assert.equal(error.serverDetail, undefined);
  const quiet = new DownloadFailedError('c9025 build', { mesh: true, faces: 8539810, total: 153707436,
    received: 15260874, lostSeconds: 63 });
  assert.match(quiet.detail, /failed: the server stopped answering for 63 s after 15 of 154 MB had arrived\./);
});

test('a download that outlasts the poll limit but keeps arriving is read whole, in one poll', async () => {
  // 25 pieces 10 ms apart: 250 ms in all, against 40 ms for the headers and 150 ms between pieces.
  Object.assign(POLL_TIMING, { requestTimeout: 40, stallTimeout: 150 });
  const bytes = binaryReply(2000);
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [streamed(bytes, { pieces: 25, gap: 10 })],
  });
  const seen = [];
  const t0 = Date.now();
  const mesh = await within(5000, api.mesh('i', 1, {}, { onStatus: (j) => seen.push(j) }));
  assert.ok(Date.now() - t0 >= 200, 'the download took longer than the poll limit');
  assert.equal(mesh.n_faces, 2000);
  assert.ok(mesh.faces instanceof Uint32Array);
  assert.equal(mesh.faces.length, 6000);
  assert.equal(mesh.vertices[3], 3);
  assert.equal(polls(calls).length, 1, 'never cut off and asked again');
  assert.equal(seen.filter((j) => j.reconnecting).length, 0);
});

test('the give-up window does not count time spent downloading', async () => {
  const clock = fakeClock(0);
  Object.assign(POLL_TIMING, { requestTimeout: 1000, stallTimeout: 1000, giveUp: 60000 });
  const bytes = binaryReply(2000);
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    // Twice broken off after 80 s of steady bytes, then whole: 160 s between the
    // first failure and the second, every second of it the server talking.
    'GET /api/mesh-jobs/j1': [
      streamed(bytes, { pieces: 12, breakAfter: 8, clock, tick: 10000 }),
      streamed(bytes, { pieces: 12, breakAfter: 8, clock, tick: 10000 }),
      streamed(bytes, { pieces: 12, clock, tick: 10000 }),
    ],
  }, { clock });
  const mesh = await within(3000, api.mesh('i', 1));
  assert.equal(mesh.n_faces, 2000);
});

test('a download that stops arriving is dropped after the stall limit and asked for again', async () => {
  Object.assign(POLL_TIMING, { requestTimeout: 1000, stallTimeout: 40 });
  const bytes = binaryReply(2000);
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [streamed(bytes, { stopAfter: 3 }), streamed(bytes)],
  });
  const seen = [];
  const mesh = await within(3000, api.mesh('i', 1, {}, { onStatus: (j) => seen.push(j) }));
  assert.equal(mesh.n_faces, 2000);
  assert.equal(polls(calls).length, 2);
  const retry = seen.find((j) => j.reconnecting);
  assert.ok(retry, 'the retry was reported');
  assert.equal(retry.downloading.broke, true);
  assert.equal(retry.downloading.total, bytes.byteLength, 'the size, from the reply\'s header');
  assert.equal(retry.downloading.faces, 2000);
  assert.match(retry.error, /the download broke off at \d+ of \d+ kB: the download stopped/);
  assert.equal(stageText(retry), `downloading the surface broke off at ${progressText(retry.downloading.received,
    bytes.byteLength)}, asking again`);
});

test('a download that keeps breaking off ends saying the surface was built, with its size', async () => {
  Object.assign(POLL_TIMING, { requestTimeout: 1000, stallTimeout: 30, downloadTries: 3 });
  const bytes = binaryReply(2000);
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [streamed(bytes, { stopAfter: 2 }), streamed(bytes, { breakAfter: 4 }),
      streamed(bytes, { stopAfter: 3 })],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  });
  const error = await within(3000, rejects(api.mesh('i', 1)));
  assert.ok(error instanceof DownloadFailedError, String(error));
  assert.ok(error instanceof ApiError);
  assert.ok(!(error instanceof ConnectionLostError));
  assert.ok(!error.aborted);
  assert.equal(polls(calls).length, 3);
  assert.equal(error.faces, 2000);
  assert.equal(error.total, bytes.byteLength);
  assert.equal(error.received, Math.ceil(bytes.byteLength / 10) * 4);
  assert.match(error.detail, /^c1 build finished on the server \(2,000 triangles, \d+ kB\), but downloading the surface failed: it broke off 3 times, at most \d+ of \d+ kB in\./);
  assert.doesNotMatch(error.detail, /lost contact|may still finish/);
  await nextTick();
  assert.equal(calls.filter((c) => c.method === 'DELETE').length, 1);
});

test('a server that goes silent after part of the surface arrived: the download failed, not "may still finish"', async () => {
  const clock = fakeClock(25000);
  Object.assign(POLL_TIMING, { requestTimeout: 1000, stallTimeout: 30, downloadTries: 5 });
  const bytes = binaryReply(2000);
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [streamed(bytes, { stopAfter: 3 }), new TypeError('network down')],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  }, { clock });
  const error = await within(3000, rejects(api.mesh('i', 1)));
  assert.ok(error instanceof DownloadFailedError, String(error));
  assert.ok(error.lostSeconds > 60);
  assert.match(error.detail, /^c1 build finished on the server \(2,000 triangles, \d+ kB\), but downloading the surface failed: the server stopped answering for \d+ s after \d+ of \d+ kB had arrived\./);
});

test('Stop during a download aborts the request itself, and releases the job', async () => {
  const bytes = binaryReply(2000);
  const asked = [];
  const calls = server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [streamed(bytes, { pieces: 100, gap: 10, asked })],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  });
  const controller = new AbortController();
  const promise = api.mesh('i', 1, {}, { signal: controller.signal });
  await nextTick(60);
  controller.abort();
  const error = await within(500, rejects(promise));
  assert.equal(error.aborted, true);
  assert.equal(error.message, 'c1 build stopped');
  assert.equal(asked.length, 1);
  assert.equal(asked[0].signal.aborted, true, 'the download was ended, not left running');
  await nextTick();
  assert.equal(calls.filter((c) => c.method === 'DELETE').length, 1);
});

test('a cell unloaded during a download stands down at once, ending the download', async () => {
  const bytes = binaryReply(2000);
  const asked = [];
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [streamed(bytes, { pieces: 100, gap: 10, asked })],
    'DELETE /api/mesh-jobs/j1': [{ body: {} }],
  });
  let wanted = true;
  const promise = api.mesh('i', 1, {}, { alive: () => wanted });
  await nextTick(60);
  wanted = false;
  const error = await within(500, rejects(promise));
  assert.equal(error.aborted, true);
  assert.equal(error.message, 'c1 build abandoned');
  assert.equal(asked[0].signal.aborted, true);
});

test('a download says how far it has got, from half a second in, with the size from its header', async () => {
  const clock = fakeClock(0);
  const bytes = binaryReply(2000);
  server({
    'GET /api/neurons/i/1/mesh-job': [started],
    'GET /api/mesh-jobs/j1': [running(), streamed(bytes, { pieces: 10, clock, tick: 300 })],
  }, { clock });
  const seen = [];
  const busy = [];
  const off = onBusy(({ message }) => busy.push(message));
  try {
    await within(3000, api.mesh('i', 1, {}, { onStatus: (j) => seen.push(j) }));
  } finally {
    off();
  }
  const down = seen.filter((j) => j.downloading);
  // Pieces at 300 ms steps: none in the first half second, then each 300 ms (over 250).
  assert.equal(down.length, 8);
  assert.ok(down.every((j) => j.downloading.total === bytes.byteLength && j.downloading.faces === 2000));
  assert.ok(down.every((j, i) => i === 0 || j.downloading.received > down[i - 1].downloading.received));
  // The last stage the build reported is not shown as still going.
  assert.ok(down.every((j) => j.progress === null && j.kind === 'mesh'));
  assert.match(stageText(down[0]), /^downloading the surface, \d+ of \d+ kB$/);
  assert.ok(busy.some((m) => /^c1 build: downloading \d+ of \d+ kB$/.test(m)), busy.join(' | '));
  assert.equal(busy.at(-1), 'ready', 'the busy line does not keep saying the download');
  assert.equal(seen.at(-1).status, 'done');
});

test('stageText: a result that is not a surface, and a download whose size is not known', () => {
  assert.equal(stageText({ status: 'running', kind: 'deviation', downloading: { received: 12e6, total: null } }),
    'downloading the result, 12 MB');
  assert.equal(stageText({ status: 'done', kind: 'mesh', downloading: { received: 25e6, total: 153707436 } }),
    'downloading the surface, 25 of 154 MB');
});

let failed = 0;
for (const [name, fn] of cases) {
  Object.assign(POLL_TIMING, BASE_TIMING);
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack || error}`);
  }
}
clearInterval(keepAlive);
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
