// Thin fetch wrapper over the SBFSEM-pytools HTTP API.
// Every backend call in the app goes through here, so error handling and the
// busy indicator are in one place.

const listeners = new Set();
let pending = 0;

export function onBusy(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify(message, kind) {
  for (const fn of listeners) fn({ pending, message, kind });
}

/*
 * Which copy of each cell this tab loaded.  The server holds one copy of a
 * cell per setting in use (Viking's correction mode, current section
 * transforms or not), because those settings are per tab: two tabs looking at
 * one cell with different settings must not change each other's cell.  A load
 * answers with its copy's `copy_tag`; every later request for the cell, and
 * every download link, names it back as `?copy=`.
 */
const copies = new Map();
const cellKey = (source, id) => `${source}:${id}`;
const CELL_PATH = /^\/api\/neurons\/([^/?]+)\/(\d+)(?=[/?.]|$)/;

function remember(detail) {
  const summary = detail?.summary;
  if (summary?.copy_tag) copies.set(cellKey(summary.source, summary.id), summary.copy_tag);
  return detail;
}

/*
 * How many loads of each cell this tab has begun, and how many are still under
 * way.  A load can change what the copy it names holds (Update from Viking keeps
 * the copy's name), so an answer asked of a cell while one was under way, or
 * across one, may be of either state: app.js keeps an extra for later loads only
 * when neither happened (`loadsOf`, appcore `keptAnswer`).
 */
const loads = new Map();

/** `{ begun, running }`: this tab's loads of the cell (see `loads`). */
export function loadsOf(source, id) {
  const entry = loads.get(cellKey(source, id));
  return { begun: entry?.begun ?? 0, running: entry?.running ?? 0 };
}

function counted(source, id, send) {
  const key = cellKey(source, id);
  const entry = loads.get(key) ?? { begun: 0, running: 0 };
  entry.begun += 1;
  entry.running += 1;
  loads.set(key, entry);
  const done = () => { entry.running -= 1; };
  // A `send` that throws is a rejected promise like any other failure, and still ends.
  const out = (async () => send())();
  out.then(done, done);
  return out;
}

function withCopy(path) {
  const match = CELL_PATH.exec(path);
  if (!match || /[?&]copy=/.test(path)) return path;
  const copy = copies.get(cellKey(decodeURIComponent(match[1]), match[2]));
  if (!copy) return path;
  return `${path}${path.includes('?') ? '&' : '?'}copy=${encodeURIComponent(copy)}`;
}

/** `?copy=TAG` for a call that names its copy itself (a layer on another copy than the tab's), else nothing. */
function copyQuery(copy) {
  return copy ? `?copy=${encodeURIComponent(copy)}` : '';
}

/**
 * Where a load finds a cell's manual moves (manualmoves.js): `(source, id)` gives
 * the `manual_moves` body, `{ [tag]: [moves] }`, or null.  app.js sets it, so every
 * load of a cell carries the person's moves -- a Registration switch, an update
 * from Viking, an undo -- without each caller knowing.  A body that names its own
 * `manual_moves` (a restore, whose cell is not listed yet) is sent as it is.
 */
let movesFor = null;
export function setMovesProvider(fn) { movesFor = typeof fn === 'function' ? fn : null; }

function withMoves(body) {
  if (!movesFor || !body || body.manual_moves !== undefined) return body;
  const moves = movesFor(body.source, body.id);
  return moves ? { ...body, manual_moves: moves } : body;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed with ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * A FastAPI 422 body is a list of {loc, msg, ...} objects; raw JSON in the
 * render-error banner reads as line noise. "wrap_spacing_um: Input should be
 * greater than or equal to 0.15" names the knob to walk back.
 */
function formatValidationDetail(detail) {
  if (!Array.isArray(detail)) return JSON.stringify(detail);
  const parts = detail.map((d) => {
    const where = Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : '';
    return d?.msg ? (where ? `${where}: ${d.msg}` : d.msg) : JSON.stringify(d);
  });
  return parts.join('; ');
}

async function request(path, options = {}, label = '') {
  pending += 1;
  notify(label || 'working…', 'busy');
  try {
    const response = await fetch(withCopy(path), options);
    if (!response.ok) {
      // Over HTTP/2 statusText is empty, so a proxy error with an HTML body
      // used to surface as a bare number ("— 504 ."). Name the ones nginx
      // actually produces here.
      let detail = {
        502: '502 — the render service is not responding (it may be restarting)',
        504: '504 — the server took too long to build this; the gateway gave up'
          + ' after 5 minutes. A smaller cell, a coarser setting, or the'
          + ' closed-curve technique for vessels may fit within it',
      }[response.status] || `${response.status} ${response.statusText}`.trim();
      try {
        const body = await response.json();
        if (body && body.detail) detail = typeof body.detail === 'string'
          ? body.detail
          : formatValidationDetail(body.detail);
      } catch { /* response was not JSON */ }
      throw new ApiError(response.status, detail);
    }
    return response;
  } finally {
    pending -= 1;
    notify(pending ? 'working…' : 'ready', pending ? 'busy' : '');
  }
}

async function getJSON(path, label) {
  const response = await request(path, {}, label);
  return response.json();
}

/**
 * Hand a finished download to the browser: the blob behind a temporary link, as
 * a click on `<a download>` would.  The browser does this part; it is a
 * parameter of `downloadFile` so the tests can watch it.
 */
function saveBlob(blob, filename) {
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  // After the browser has taken it (Safari reads the link a moment later).
  setTimeout(() => URL.revokeObjectURL(href), 30000);
}

/**
 * Fetch a file the server builds (an export) and save it as `filename`.
 *
 * A bare `<a download>` click navigates to the file, and a failed export (the
 * server answers JSON with the reason: a Closed curve raster over its voxel
 * limit, an empty mesh, a format it does not write) was then saved as the
 * "file" or, in browsers that skip it, did nothing at all.  Fetching it first
 * means the answer is read: on success the bytes are saved, on failure this
 * rejects with the server's own words (an ApiError) for the caller to show.
 * Resolves with `{ filename, bytes }`, and, for a bulk archive, `archive`: how
 * many skeletons it holds (see `archiveReport`).
 */
export async function downloadFile(path, filename, { label = '', save = saveBlob } = {}) {
  const response = await request(path, {}, label || `preparing ${filename}…`);
  const blob = await response.blob();
  save(blob, filename);
  const archive = archiveReport(response.headers);
  return archive ? { filename, bytes: blob.size, archive } : { filename, bytes: blob.size };
}

/**
 * What the bulk SWC route says about the archive it sent, read from its headers
 * (`X-Archive-Asked`, `-Written`, `-Failed`, `-Not-Attempted`): the counts, or
 * `null` when the response carries none (every other download).  An archive cut
 * short by the time budget is a 200 and a zip like a whole one, so the headers
 * are the only place the viewer can learn it is short.  Counts that are not
 * whole non-negative numbers are not believed.
 */
export function archiveReport(headers) {
  const count = (name) => {
    const text = headers?.get?.(name);
    const n = text === null || text === undefined ? NaN : Number(text);
    return Number.isInteger(n) && n >= 0 ? n : null;
  };
  const asked = count('X-Archive-Asked');
  if (asked === null) return null;
  return {
    asked,
    written: count('X-Archive-Written') ?? 0,
    failed: count('X-Archive-Failed') ?? 0,
    notAttempted: count('X-Archive-Not-Attempted') ?? 0,
  };
}

const q = (params) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
};

// -- background jobs ---------------------------------------------------------
// A mesh build, an error-map measurement and a mesh difference all run as
// server jobs, so no request outlives the proxy's 300 s: one call starts (or
// joins) a job, cheap polls follow it.  `pollJob` is the one loop all three
// share.

/** What can decide which side of mesh B a vertex of mesh A is on (`diffJob`). */
const DIFF_SIGNS = ['normal', 'enclosure'];

/**
 * How long a poll asks the server to wait for its job to end (`?wait=`, seconds), for a
 * page that came over `protocol` (the navigation's `nextHopProtocol`): 1 over HTTP/2 or
 * HTTP/3, 0 (no wait) otherwise.  A waiting poll holds its connection for up to that
 * long, and over HTTP/1.1 the browser has six per server: a restore follows six builds
 * at once, and every other request of the page would queue behind them.  Over HTTP/2
 * (production, through nginx) they share one connection.
 */
export function pollWaitFor(protocol) {
  return /^h[23]/.test(String(protocol || '')) ? 1 : 0;
}

function pageProtocol() {
  try {
    return globalThis.performance?.getEntriesByType?.('navigation')?.[0]?.nextHopProtocol || '';
  } catch {
    return '';
  }
}

/** Poll timing (ms).  `wait` (seconds) is how long each poll asks the server to
 * hold it until the job ends (`pollWaitFor`): the server answers the moment the
 * build is done, so the next poll is sent at once, the first one too.  Without it
 * (`wait` 0) the first look comes after `first` (a cached render is done before
 * the browser has finished asking, and waiting a full interval for it was most of
 * a session restore's time) and the rest every `every`, which showed a finished
 * build up to `every` late (0.5 s on average, measured 0 to 0.7 s on production,
 * 2026-10-20).  A poll the server answered at once although it was asked to wait
 * (one that predates `wait`) is followed after the rest of `every`.
 * `requestTimeout` bounds one poll, so a hung connection counts as a failed poll
 * after that long instead of holding the follow (and `alive`/Stop) until the
 * proxy's 300 s.  `now` is the clock the give-up window and a poll's time are
 * measured on.  Overridable for tests. */
export const POLL_TIMING = {
  first: 150, every: 1000, wait: pollWaitFor(pageProtocol()), giveUp: 60000, requestTimeout: 20000,
  now: () => Date.now(),
};

/** The viewer stopped hearing from the server while a job ran.  Not a
 * statement about the job: it may well finish there. */
export class ConnectionLostError extends ApiError {
  constructor(what, seconds) {
    super(0, `lost contact with the server for ${seconds} s while ${what} was running. `
      + 'It may still finish there: ask for it again in a moment.');
    this.lostSeconds = seconds;
  }
}

/** The binary form of a finished mesh (`sbfsem_web/mesh_binary.py`, docs/api.md). */
export const MESH_MEDIA_TYPE = 'application/x-sbfsem-mesh';
/** int32's lowest value: the binary's -0.0, which no coordinate takes. */
const NEGATIVE_ZERO = -(2 ** 31);
/** The binary is little-endian and is read through typed arrays, which take the
 * machine's own order: a big-endian machine asks for JSON. */
const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;

/**
 * A finished mesh job's binary reply as the job `JSON.parse` would give of its
 * JSON: the same keys and numbers, with `mesh.vertices` a Float64Array, `faces`
 * a Uint32Array and `vertex_data` a Float64Array or null.  A vertex travels as
 * int32 steps of 1e-4 µm, the JSON's own fourth decimal, and `k / 1e4` in
 * float64 is exactly the number the JSON text parses to (a division: `k * 1e-4`
 * is not always).  Kept in float64, as the JSON was: the Section view, the
 * level sets and picking work on these numbers, and only the GPU buffer
 * narrows them to float32.
 */
export function decodeMeshReply(buffer) {
  const headerLength = new DataView(buffer).getUint32(0, true);
  const { arrays, ...job } = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength)),
  );
  const start = 4 + headerLength;
  // Copied out of the reply, so the int32 steps can go once the vertices are made.
  const view = (Type, name) => {
    const where = arrays[name];
    return where ? new Type(buffer, start + where[0], where[1]).slice() : null;
  };
  const [at, count] = arrays.vertices;
  const steps = new Int32Array(buffer, start + at, count);
  const vertices = new Float64Array(count);
  for (let i = 0; i < count; i += 1) {
    const k = steps[i];
    vertices[i] = k === NEGATIVE_ZERO ? -0 : k / 1e4;
  }
  // The header's mesh has null where each array goes, so the keys keep the JSON's order.
  job.mesh.vertices = vertices;
  job.mesh.faces = view(Uint32Array, 'faces');
  job.mesh.vertex_data = view(Float64Array, 'vertex_data');
  return job;
}

/** A poll's answer: the binary of a finished mesh, or JSON (a job still
 * running, a failure, a measurement, a mesh the binary cannot carry, a server
 * from before the binary). */
async function readJob(response) {
  const type = response.headers?.get?.('content-type') || '';
  if (type.split(';')[0].trim() === MESH_MEDIA_TYPE) {
    return decodeMeshReply(await response.arrayBuffer());
  }
  return response.json();
}

/** One poll of a job, bounded in time.  Asks for a finished mesh as binary:
 * 7.2 MB instead of 12.7 MB of JSON for c1178, which production's nginx
 * compressed for 0.6 s of every finished poll, and no JSON.parse.  `wait`
 * (seconds) asks the server to hold the poll until the job ends. */
function pollOnce(jobId, label, timeout, wait = 0) {
  const options = {};
  if (timeout > 0 && typeof AbortSignal !== 'undefined' && AbortSignal.timeout) {
    options.signal = AbortSignal.timeout(timeout);
  }
  if (LITTLE_ENDIAN) options.headers = { Accept: `${MESH_MEDIA_TYPE}, application/json` };
  const query = wait > 0 ? `?wait=${wait}` : '';
  return request(`/api/mesh-jobs/${jobId}${query}`, options, label).then(readJob);
}

/** Statuses a proxy answers while the service restarts or is busy: the job
 * may well be alive behind them, so the poll tries again. */
const TRANSIENT_STATUSES = new Set([502, 503, 504]);
const ABORT = Symbol('aborted');

function abortedError(message) {
  const error = new Error(message);
  error.aborted = true;
  return error;
}

/** Settle with `promise`, or with the ABORT sentinel the moment `signal`
 * fires, so Stop does not wait for a request that is in the air. */
function abortable(signal, promise) {
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    const stop = () => reject(ABORT);
    if (signal.aborted) { stop(); return; }
    signal.addEventListener('abort', stop, { once: true });
    promise.then(resolve, reject)
      .finally(() => signal.removeEventListener('abort', stop));
  });
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** The server's words for a failed job, naming the stage it was in. */
function jobFailure(job, what) {
  const detail = job.detail || `${what} failed`;
  const stage = job.failed_stage || '';
  const error = new ApiError(400, stage ? `failed during ${stage}: ${detail}` : detail);
  error.failedStage = stage;
  error.serverDetail = detail;
  return error;
}

/**
 * Follow one server job to its end.
 *
 * `start` is a function returning a promise of the start answer
 * (`{ job_id }`), or such a promise.  A function can be called again: when a
 * poll answers 404 (the job expired after ten minutes, or the server
 * restarted and lost it) the job is submitted once more and followed; a
 * promise cannot, so there a 404 is an error.
 *
 * `label` is the busy line's text; `what` names the work in messages
 * ("c1178 build").  `onStatus(job)` gets every poll's answer for progress
 * displays, and while polls fail `onStatus({ ...lastJob, reconnecting: true,
 * reconnectSeconds, error })`.  `onSubmitted(jobId)` is called once, when the
 * server has taken the job (its start answered), before the first poll.  A
 * network failure, a poll that outlasts
 * `POLL_TIMING.requestTimeout`, or a 502/503/504 is tolerated for
 * `POLL_TIMING.giveUp` ms; after that a ConnectionLostError says so (and the
 * job is released, as it is before any other error ends the follow).
 * `signal` (an AbortSignal) and `alive` (a function, consulted between
 * polls and before a resubmit) both stop the follow: a DELETE cancels a
 * still-queued job (a running one cannot be interrupted; its result lands in
 * the server cache so nothing is wasted) and the rejection carries
 * `aborted: true`, so callers go quiet rather than announce an error the
 * user caused on purpose.  A signal that is already aborted sends nothing.
 *
 * Resolves with the final `done` job (`job.mesh` or `job.result` holds the
 * product).  Rejects with an ApiError for a failed job; `error.failedStage`
 * and `error.serverDetail` carry the parts of its message.
 */
export async function pollJob(start, {
  label = 'working…', what = 'job', signal, alive, onStatus, onSubmitted, timing,
} = {}) {
  const {
    first, every, wait, giveUp, requestTimeout, now: clock,
  } = { ...POLL_TIMING, ...timing };
  // A poll that waits on the server is sent at once; the server answers when there is news.
  const firstDelay = wait > 0 ? 0 : first;
  const begin = typeof start === 'function' ? start : () => start;
  const cancel = (jobId) => {
    fetch(`/api/mesh-jobs/${jobId}`, { method: 'DELETE' }).catch(() => {});
  };
  const stopped = (jobId, message) => {
    if (jobId) cancel(jobId);
    notify('ready', '');
    return abortedError(message);
  };
  // Submit, and if Stop wins while the request is in the air, cancel the job
  // it eventually starts rather than leave it queued for nobody.
  const submit = async () => {
    if (signal?.aborted) throw ABORT;
    const started = begin();
    try {
      return (await abortable(signal, started)).job_id;
    } catch (error) {
      if (error === ABORT) started.then((answer) => cancel(answer?.job_id), () => {});
      throw error;
    }
  };
  // A progress display that throws must not end the follow: the job is still
  // running on the server.  Said in the console, then carried on.
  const report = (job) => {
    try {
      onStatus?.(job);
    } catch (error) {
      console.error('job status callback failed', error);
    }
  };
  const t0 = clock();
  let resubmitted = false;
  let jobId = null;
  let lastJob = null;
  let failingSince = 0;
  try {
    if (alive && !alive()) throw stopped(null, `${what} abandoned`);
    jobId = await submit();
    if (signal?.aborted) throw ABORT;
    try {
      onSubmitted?.(jobId);
    } catch (error) {
      console.error('job submitted callback failed', error);
    }
    let delay = firstDelay;
    for (;;) {
      notify(label, 'busy');
      await abortable(signal, pause(delay));
      delay = every;
      if (alive && !alive()) throw stopped(jobId, `${what} abandoned`);
      const asked = clock();
      const seconds = Math.round((asked - t0) / 1000);
      let job;
      try {
        job = await abortable(signal, pollOnce(jobId, `${label} ${seconds}s`, requestTimeout, wait));
      } catch (error) {
        if (error === ABORT) throw error;
        if (error instanceof ApiError && error.status === 404) {
          // Nobody wants the job any more: do not start another for them.
          if (alive && !alive()) throw stopped(null, `${what} abandoned`);
          if (signal?.aborted) throw ABORT;
          if (resubmitted || typeof start !== 'function') {
            throw new ApiError(404, `${what}: the server no longer has this job`
              + (resubmitted ? ' (it lost it twice)' : '')
              + '. It expired, or the server restarted. Ask for it again.');
          }
          // Expired or lost in a restart: ask again, once.  The server's
          // cache answers at once if the work had finished.
          resubmitted = true;
          jobId = await submit();
          delay = firstDelay;
          continue;
        }
        const transient = !(error instanceof ApiError) || TRANSIENT_STATUSES.has(error.status);
        if (!transient) {
          cancel(jobId);
          throw error;
        }
        const now = clock();
        if (!failingSince) failingSince = now;
        if (now - failingSince > giveUp) {
          // The poll's own error ("504 ... the gateway gave up") describes a
          // build request, not this follow: say what happened to it instead.
          cancel(jobId);
          throw new ConnectionLostError(what, Math.round((now - failingSince) / 1000));
        }
        report({
          ...(lastJob || { status: 'unknown' }), reconnecting: true,
          reconnectSeconds: Math.round((now - failingSince) / 1000),
          error: String(error?.detail || error?.message || error),
        });
        continue;
      }
      failingSince = 0;
      lastJob = job;
      report(job);
      if (job.status === 'done') return job;
      if (job.status === 'error') throw jobFailure(job, what);
      if (job.status === 'cancelled') throw abortedError(`${what} cancelled`);
      // Held by the server until it had news: ask again at once.  Answered at once:
      // after the rest of the interval, as without `wait`.
      if (wait > 0) delay = Math.max(0, every - (clock() - asked));
    }
  } catch (error) {
    if (error === ABORT) throw stopped(jobId, `${what} stopped`);
    throw error;
  }
}

const postJSON = (path, body, label) => request(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
}, label).then((r) => r.json());

/**
 * POST `body` as JSON and save the file the server answers, named as its
 * Content-Disposition says (else `fallback`); rejects with the server's reason.
 */
async function postDownload(path, body, fallback, label = '') {
  const response = await request(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }, label || `preparing ${fallback}…`);
  const blob = await response.blob();
  const named = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '');
  const filename = named ? named[1] : fallback;
  saveBlob(blob, filename);
  return { filename, bytes: blob.size };
}

export const api = {
  health: () => getJSON('/api/health'),

  // Documents saved on the server, under user-named folders.
  saveFolders: () => getJSON('/api/saves'),
  saveCreateFolder: (name) =>
    request('/api/saves', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }, 'creating folder…').then((r) => r.json()),
  saveList: (folder) => getJSON(`/api/saves/${encodeURIComponent(folder)}`),
  saveDeleteFolder: (folder) =>
    request(`/api/saves/${encodeURIComponent(folder)}`, { method: 'DELETE' }, 'deleting folder…'),
  saveWrite: (folder, name, document) =>
    request(`/api/saves/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(document),
    }, 'saving…').then((r) => r.json()),
  saveRead: (folder, name) =>
    getJSON(`/api/saves/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`, 'opening…'),
  saveDelete: (folder, name) =>
    request(`/api/saves/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`,
      { method: 'DELETE' }, 'deleting…'),
  // Polled on a timer, so it must not announce itself in the status line.
  build: () => fetch('/api/build', { cache: 'no-store' }).then((r) => {
    if (!r.ok) throw new Error(`build check failed: ${r.status}`);
    return r.json();
  }),
  // The Settings tab: this browser's cached files, and the server's tools (an admin's).
  clearBrowserCache: () =>
    request('/api/browser/clear-cache', { method: 'POST' }, 'clearing cached files…').then((r) => r.json()),
  // The annotations Viking's curve fit pulled toward a much wider linked one, in every
  // loaded cell this account may see (the Settings tab's Correction log).
  correctionLog: (ratio) => getJSON(`/api/correction-log${q({ ratio })}`),
  adminWhoami: () => getJSON('/api/admin/whoami'),
  adminStatus: () => getJSON('/api/admin/status', 'server status…'),
  adminClearRenders: (disk) =>
    postJSON('/api/admin/clear-renders', { confirm: 'clear', disk: Boolean(disk) }, 'clearing cached renders…'),
  adminRecycleWorkers: () =>
    postJSON('/api/admin/recycle-workers', { confirm: 'recycle' }, 'replacing the build processes…'),
  adminRestart: () => postJSON('/api/admin/restart', { confirm: 'restart' }, 'restarting the server…'),
  volumes: () => getJSON('/api/volumes', 'volumes…'),
  // Volume context: the fitted IPL boundary sheets and the vasculature; `flat`
  // is renderoptions.flatQuery's, {} for the volume's own Z.
  boundaryMesh: (source, layer, decimate = 2, flat = {}) =>
    getJSON(`/api/volumes/${source}/boundary/${layer}/mesh${q({ decimate, ...flat })}`,
      `${layer} boundary…`),
  vesselMesh: (source, flat = {}) =>
    getJSON(`/api/volumes/${source}/vessels/mesh${q(flat)}`, 'blood vessels…'),
  search: (source, label, exact = false) =>
    getJSON(`/api/volumes/${source}/search${q({ label, exact })}`, 'searching…'),
  // The Viking ExportPortal grammar: IDs, ranges, and label terms. The server
  // parses and resolves labels against this volume, so the dialog and a
  // posted .txt file understand exactly the same strings.
  resolveStructures: (source, text) =>
    request(`/api/volumes/${source}/resolve-structures`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }, 'resolving structures…').then((r) => r.json()),

  loaded: () => getJSON('/api/neurons'),
  load: (body) => counted(body.source, body.id, () =>
    request('/api/neurons', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(withMoves(body)),
    }, `loading c${body.id}…`).then((r) => r.json()).then(remember)),
  /**
   * Load another copy of a cell -- the cell under another registration setting
   * (`live_registration`, `section_corrections`) -- without making it this tab's:
   * Render compare builds a layer from it by naming its `copy_tag` (registrations.js).
   * Not counted as a load of the tab's cell, and the person's own moves are not
   * sent, so the copy is the setting's plain one.
   */
  loadCopy: (body) => request('/api/neurons', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, `loading c${body.id} under another registration…`).then((r) => r.json()),
  importFile: (file) => {
    const form = new FormData();
    form.append('file', file);
    return request('/api/neurons/import', { method: 'POST', body: form }, 'importing…')
      .then((r) => r.json()).then(remember);
  },
  renderPrefs: (source, id) =>
    getJSON(`/api/neurons/${source}/${id}/render-prefs`),
  attachRender: (source, id, render) =>
    request(`/api/neurons/${source}/${id}/render-prefs`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(render),
    }, `attaching render to c${id}…`).then((r) => r.json()),
  detachRender: (source, id) =>
    request(`/api/neurons/${source}/${id}/render-prefs`, { method: 'DELETE' },
      `detaching render from c${id}…`).then((r) => r.json()),
  unload: (source, id) =>
    request(`/api/neurons/${source}/${id}`, { method: 'DELETE' }).then((r) => r.json())
      .finally(() => copies.delete(cellKey(source, id))),

  detail: (source, id) => getJSON(`/api/neurons/${source}/${id}`).then(remember),
  /** The cell's annotations; `copy` names another copy of it than the tab's (a Render compare layer's). */
  nodes: (source, id, cellOnly = true, { copy } = {}) =>
    getJSON(`/api/neurons/${source}/${id}/nodes${q({ cell_only: cellOnly, copy })}`, 'annotations…'),
  /** The links between annotations: `{ structure_id, a, b }` Location ID pairs. */
  edges: (source, id) => getJSON(`/api/neurons/${source}/${id}/edges`, 'annotation links…'),
  /** Splined closed-curve outlines (TypeCode 6), in microns, in the cell's frame. */
  closedCurves: (source, id, { copy } = {}) =>
    getJSON(`/api/neurons/${source}/${id}/closed-curves${copyQuery(copy)}`, 'outlines…'),
  /** The cell's segments; `flat` as boundaryMesh's (the flattened view's skeleton). */
  skeleton: (source, id, flat = {}) =>
    getJSON(`/api/neurons/${source}/${id}/skeleton${q(flat)}`, 'skeleton…'),
  /** Every synapse of a cell as one point (GraphApp's synapse dots). */
  synapsePoints: (source, id) => getJSON(`/api/neurons/${source}/${id}/synapse-points`, 'synapses…'),
  /** The annotations as an averaged wireframe: rings, lines along, ties. */
  annotationWire: (source, id, params = {}) =>
    getJSON(`/api/neurons/${source}/${id}/annotation-wire${q(params)}`, 'annotation wireframe…'),
  /** Where one Viking Location is: a loaded cell answers, else Viking (404 if unknown). */
  locate: (source, locationId) =>
    getJSON(`/api/volumes/${source}/locations/${locationId}`, `locating ${locationId}…`),
  freshness: (source, id) => getJSON(`/api/neurons/${source}/${id}/freshness`),
  /** Many cells' freshness in one request per volume: `[{ source, id }]`, each
   * sent with the copy of the cell this tab holds. */
  freshnessBatch: (cells) =>
    request('/api/neurons/freshness', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        cells: cells.map(({ source, id }) => ({
          source, id, copy: copies.get(cellKey(source, id)) || undefined,
        })),
      }),
    }, 'checking Viking for updates…').then((r) => r.json()),
  // Shared libraries (materials, gradients): what one user saves, all see.
  library: (kind) => getJSON(`/api/library/${kind}`),
  librarySave: (kind, name, doc) =>
    request(`/api/library/${kind}/${encodeURIComponent(name)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(doc),
    }, `saving ${name}…`).then((r) => r.json()),
  libraryDelete: (kind, name) =>
    request(`/api/library/${kind}/${encodeURIComponent(name)}`, { method: 'DELETE' },
      `deleting ${name}…`).then((r) => r.json()),
  lastModified: (source, id) =>
    getJSON(`/api/neurons/${source}/${id}/last-modified`, 'checking history…'),
  /** Who Viking attributes each of the cell's annotations to, live. */
  annotators: (source, id, opts = {}) =>
    getJSON(`/api/neurons/${source}/${id}/annotators${q(opts)}`, `annotators of c${id}…`),
  /**
   * Build a mesh through a background job, so no build can outlive an HTTP
   * request. One call starts (or joins) the job; cheap polls follow it, with
   * the elapsed time in the busy line so a long build reads as working, not
   * hung. `alive` (optional) is consulted between polls — when it returns
   * false the poll stops, a cancel is sent for a still-queued job, and the
   * rejection carries `aborted: true` so callers can go quiet instead of
   * announcing an error the user caused on purpose (by unloading the cell).
   * `signal` (optional AbortSignal) does the same on demand (a Stop button).
   * `onStatus` (optional) gets each poll's answer, for the load strip, and
   * `onSubmitted` (optional) is called once the server has taken the build.
   */
  /**
   * What a render with `opts` would cost on this tab's copy of the cell,
   * before it is built (`GET .../render-estimate`): for Closed curve and Hybrid
   * the raster's voxels and faces, triangles, the server's memory, seconds,
   * the page's JSON, the limits and the finest Voxel size that fits.  Quiet:
   * it is asked while a size is typed, so it never touches the busy line.
   * Rejects with an ApiError, or an AbortError when `signal` fires.
   */
  renderEstimate: async (source, id, opts = {}, { signal } = {}) => {
    const response = await fetch(withCopy(`/api/neurons/${source}/${id}/render-estimate${q(opts)}`),
      signal ? { signal } : {});
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`.trim();
      try {
        const body = await response.json();
        if (body?.detail) detail = typeof body.detail === 'string'
          ? body.detail : formatValidationDetail(body.detail);
      } catch { /* not JSON */ }
      throw new ApiError(response.status, detail);
    }
    return response.json();
  },
  mesh: async (source, id, opts = {}, { alive, signal, onStatus, onSubmitted } = {}) => {
    const job = await pollJob(
      () => getJSON(`/api/neurons/${source}/${id}/mesh-job${q(opts)}`, `meshing c${id}…`),
      { label: `meshing c${id}…`, what: `c${id} build`, alive, signal, onStatus, onSubmitted });
    return job.mesh;
  },
  /**
   * Have the server keep the whole-cell grid of the render `opts` describes, built
   * in the background when it has none (`GET .../grid-job`): asked when move mode
   * starts, so the first Apply contours only the blocks its moves touched.  Quiet
   * and not followed: nothing waits on it, and a failure costs only that speed.
   */
  gridJob: (source, id, opts = {}) => fetch(withCopy(`/api/neurons/${source}/${id}/grid-job${q(opts)}`))
    .then((r) => (r.ok ? r.json() : null)).catch(() => null),
  /**
   * Hold one rendering to the cell's own annotations, point by point, as a
   * server job (the work can take minutes on a big cell).  Resolves with the
   * result, which carries no geometry: `{ mesh_id, n_vertices, vertex_um,
   * rim_xyz, rim_um, rim_stride, n_rims, scale_um, deviation }`.  The caller
   * attaches it to the mesh it holds after checking `mesh_id`.  `copy` names the
   * copy of the cell (a summary's `copy_tag`) to build and measure when a layer
   * is not on the copy this tab loaded last; it goes as `?copy=`, in place of the
   * automatic one.  Left out, it is this tab's copy, as before.
   */
  deviationJob: async (source, id, options, { signal, alive, onStatus, copy } = {}) => {
    const job = await pollJob(
      () => postJSON(`/api/neurons/${source}/${id}/mesh-deviation-job${copyQuery(copy)}`, options,
        `measuring c${id} against its annotations…`),
      { label: `measuring c${id}…`, what: `c${id} measurement`, signal, alive, onStatus });
    if (!job.result) throw new ApiError(500, `c${id} measurement finished without a result`);
    return job.result;
  },
  /** Mesh A's signed distance to mesh B, as a server job: `{ a, b, samples?,
   * sign?, both? }` in, `{ mesh_id, n_vertices, distance_um, ... }` out.
   * `sign` is what decides which side of B a vertex is on: 'normal' (B's
   * outward normal at the nearest surface sample: the server's default, and
   * wrong where that sample is on a piece buried inside B) or 'enclosure'
   * (whether B's solid encloses the vertex: a ray count over B with its round open
   * ends closed, and B's winding number where a closing fan would be a sheet that is
   * not there; the same from every ray direction except for a vertex on B's surface,
   * where B is open (the Original pathway); tens of seconds on a large cell, and
   * only ever a job: the blocking `mesh-diff` route answers a 400).  It is sent
   * as given and left out when not given, so a caller that never heard of it
   * measures what it always did.  `both` also returns B's field against A
   * (`distance_b_um`, with `mesh_id_b` and `n_vertices_b`); the server keeps one
   * cached measurement per sign, so asking for it after the one-way answer costs
   * the reverse pass alone, and dropping it costs nothing.
   *
   * `copy` and `copyB` name the copy of the cell (a summary's `copy_tag`) that
   * mesh A and mesh B are built from and held to, when a layer is not on the
   * copy this tab loaded last (a surface before and after a setting that moves
   * the annotations).  `copy` goes as `?copy=`, in place of the automatic one,
   * as for every request for the cell; `copyB` as the body's `copy_b`.  Left
   * out, both meshes are of this tab's copy, as before. */
  diffJob: async (source, id, body, { signal, alive, onStatus, copy, copyB } = {}) => {
    if (body && body.sign !== undefined && !DIFF_SIGNS.includes(body.sign)) {
      throw new ApiError(400, `the sign of a difference is one of ${DIFF_SIGNS.join(' or ')}, not ${body.sign}`);
    }
    const sent = copyB ? { ...body, copy_b: copyB } : body;
    const job = await pollJob(
      () => postJSON(`/api/neurons/${source}/${id}/mesh-diff-job${copyQuery(copy)}`, sent,
        `measuring c${id} difference…`),
      { label: `measuring c${id} difference…`, what: `c${id} difference`, signal, alive, onStatus });
    if (!job.result) throw new ApiError(500, `c${id} difference finished without a result`);
    return job.result;
  },
  /**
   * Render compare's report (comparereport.js `reportBody` in), as a server job:
   * resolves with what the PDF is, `{ filename, bytes, pages, seconds, notes, jobId }`;
   * `compareReportPath(jobId)` is where it is fetched from (for the ten minutes a
   * finished job is kept).  Stop (`signal`) cancels the job, and the server stops
   * it at its next step.
   */
  compareReportJob: async (source, id, body, { signal, onStatus } = {}) => {
    const job = await pollJob(
      () => postJSON(`/api/neurons/${source}/${id}/compare-report-job`, body, `starting the c${id} report…`),
      { label: `c${id} report…`, what: `c${id} report`, signal, onStatus });
    if (!job.result) throw new ApiError(500, `c${id} report finished without a file`);
    return { ...job.result, jobId: job.job_id };
  },
  compareReportPath: (jobId) => `/api/compare-reports/${encodeURIComponent(jobId)}`,
  synapseMesh: (source, id, type, opts = {}) =>
    getJSON(`/api/neurons/${source}/${id}/synapses/${encodeURIComponent(type)}/mesh`
      + q({ radius_um: opts.radius_um ?? 0.5, shape: opts.shape ?? 'circle', ...(opts.flat || {}) })),

  /** How thick a volume's IPL is, along the layers' normals and along Z, and where it thins. */
  iplThickness: (source) => getJSON(`/api/volumes/${source}/ipl-thickness`, 'IPL thickness…'),
  stratification: (source, id, opts = {}) =>
    getJSON(`/api/neurons/${source}/${id}/stratification${q(opts)}`, 'stratification…'),
  synapseStratification: (source, id, type, opts = {}) =>
    getJSON(`/api/neurons/${source}/${id}/synapse-stratification/${encodeURIComponent(type)}${q(opts)}`),
  hull: (source, id) => getJSON(`/api/neurons/${source}/${id}/hull`),
  ieRatio: (source, id) => getJSON(`/api/neurons/${source}/${id}/ie-ratio`),
  /** Cable length, longest path, branch points and tips of the traced skeleton. */
  morphometrics: (source, id, opts = {}) =>
    getJSON(`/api/neurons/${source}/${id}/morphometrics${q(opts)}`),
  /** Loops, unlinked pieces, jumps, hairpins, spikes, spokes and stubs, by Location ID. */
  skeletonCheck: (source, id) =>
    getJSON(`/api/neurons/${source}/${id}/skeleton/check`, 'checking the skeleton…'),
  /**
   * How far the section correction and the current transforms moved the cell,
   * and whether the links across sections agree better after (`pair`:
   * correction, transforms or both).  Milliseconds, so a plain GET; it is
   * answered from the copy the page loaded, like every request for the cell.
   */
  shiftSummary: (source, id, pair = 'correction') =>
    getJSON(`/api/neurons/${source}/${id}/shift-summary${q({ pair })}`),
  /** The cell's own rows of the Correction log at `ratio`, from the copy the page loaded. */
  correctionPulls: (source, id, ratio) =>
    getJSON(`/api/neurons/${source}/${id}/correction-pulls${q({ ratio })}`),
  surfaceArea: (source, id) => getJSON(`/api/neurons/${source}/${id}/surface-area`),
  dendriteDiameter: (source, id, binWidth = 5) =>
    getJSON(`/api/neurons/${source}/${id}/dendrite-diameter${q({ bin_width: binWidth })}`),
  openEnded: (source, id) => getJSON(`/api/neurons/${source}/${id}/open-ended`),
  tortuosity: (source, id, a, b, dim = 2) =>
    getJSON(`/api/neurons/${source}/${id}/tortuosity${q({ location_a: a, location_b: b, dim })}`),

  network: (source, ids) =>
    getJSON(`/api/network${q({ source, ids: ids.join(',') })}`, 'network…'),
  /** The getAllLinkedNeurons table: every synapse, its partner and position. */
  linkedNeurons: (source, id, opts = {}) =>
    getJSON(`/api/neurons/${source}/${id}/linked-neurons${q(opts)}`, `linked neurons of c${id}…`),
  linkedNeuronsUrl: (source, id, fmt, opts = {}) =>
    withCopy(`/api/neurons/${source}/${id}/linked-neurons.${fmt}${q(opts)}`),
  /** A Graffinity path query: the paths plus both overviews of them. */
  connectivity: (body) =>
    request('/api/connectivity/query', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }, 'connectivity…').then((r) => r.json()),
  connectivityGraph: (source) => getJSON(`/api/connectivity/${source}/graph`),

  exportUrl: (source, id, fmt, opts = {}) =>
    withCopy(`/api/neurons/${source}/${id}/export/${fmt}${q(opts)}`),
  /** Fetch a file by its URL and save it (see downloadFile); rejects with the server's reason. */
  download: downloadFile,
  /** The Stratification chart as `pdf` or its data as `xlsx` (routes.StrataExportBody), saved. */
  stratificationExport: (format, body) => postDownload(`/api/stratification/export.${format}`, body,
    `stratification.${format}`, format === 'pdf' ? 'drawing the PDF…' : 'writing the workbook…'),
  /** The annotation wireframe as a file; `params` are the ones it is drawn with. */
  annotationWireExportUrl: (source, id, fmt, params = {}) =>
    withCopy(`/api/neurons/${source}/${id}/annotation-wire/export/${fmt}${q(params)}`),

  /** A zip of one SWC per structure plus manifest.csv; `pattern` names the files ({id}, {volume}, {label}). */
  bulkSwcUrl: (source, ids, opts = {}) =>
    `/api/neurons/${source}/export-bulk/swc${q({ ids: ids.join(','), ...opts })}`,

  /**
   * Our bounce to the identity server's launch page; `location` is a Location
   * ID or "x,y,z,downsample".  `page=1` asks for the launch page rather than
   * the bare redirect: it is the only place that can say "Viking runs on
   * Windows only" and notice a hand-off nothing answered (viking-launch.html).
   * `client` is `viking` (the signed program, the default) or `test` (the
   * unsigned "Viking Test"); `then` is the other one, for the page to try when
   * nothing answers the first.
   */
  openInVikingUrl: (source, location, { client, then } = {}) =>
    `/open-in-viking${q({
      volume: source, location: location ?? undefined, page: 1, client, then,
    })}`,
};
