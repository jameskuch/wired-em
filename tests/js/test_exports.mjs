// The export client: the fetched download (static/js/api.js downloadFile), the
// archives a loaded volume is split into (static/js/exports.js), and the export
// link the Analyses card and the right-click menu build.  No JS runner exists in
// this repo; run it with
//
//     node tests/js/test_exports.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free, no DOM: fetch is a fake and the browser's save is a parameter.
// tests/unit/test_js.py runs it with the other suites.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const { api, ApiError, archiveReport, downloadFile, onBusy } = await load('api.js');
const X = await load('exports.js');
const { normalizeRender } = await load('renderoptions.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** A fake fetch answering `reply` ({status, body, blob}) and remembering the URLs asked for. */
function fakeFetch(reply) {
  const asked = [];
  globalThis.fetch = async (url) => {
    asked.push(String(url));
    const { status = 200, body = {}, blob = new Blob(['x']), headers = {} } = reply;
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: '',
      headers: new Headers(headers),
      json: async () => { if (body instanceof Error) throw body; return body; },
      blob: async () => blob,
    };
  };
  return asked;
}

// -- downloadFile ---------------------------------------------------------------
test('a good export is saved under the name it was asked for', async () => {
  const asked = fakeFetch({ blob: new Blob(['0123456789']) });
  const saved = [];
  const done = await downloadFile('/api/neurons/RC1/410/export/obj?method=cylinder', 'c410.obj',
    { save: (blob, name) => saved.push([blob.size, name]) });
  assert.deepEqual(asked, ['/api/neurons/RC1/410/export/obj?method=cylinder']);
  assert.deepEqual(saved, [[10, 'c410.obj']]);
  assert.deepEqual(done, { filename: 'c410.obj', bytes: 10 });
});

test('a refused export is not saved and says why, in the server\'s words', async () => {
  const detail = 'Closed-curve volume would need 450,725,370 voxels, but the limit is 300,000,000. '
    + 'The finest pixel that fits this structure is 0.12 um';
  fakeFetch({ status: 400, body: { detail } });
  let saved = false;
  await assert.rejects(
    downloadFile('/api/neurons/RC1/410/export/obj', 'c410.obj', { save: () => { saved = true; } }),
    (error) => error instanceof ApiError && error.status === 400 && error.detail === detail);
  assert.ok(!saved, 'the error page was saved as the file');
});

test('an unhandled 500 (a plain-text body) is an error too, not a file', async () => {
  fakeFetch({ status: 500, body: new Error('not json') });
  let saved = false;
  await assert.rejects(
    downloadFile('/api/neurons/RC1/410/export/glb', 'c410.glb', { save: () => { saved = true; } }),
    (error) => error instanceof ApiError && error.status === 500);
  assert.ok(!saved);
});

test('a gateway timeout is named, not a bare number', async () => {
  fakeFetch({ status: 504, body: new Error('<html>') });
  await assert.rejects(
    downloadFile('/api/neurons/RC1/410/export/glb', 'c410.glb', { save: () => {} }),
    (error) => /504/.test(error.detail) && /5 minutes/.test(error.detail));
});

test('the busy line shows the export while it runs and clears after', async () => {
  fakeFetch({});
  const seen = [];
  const off = onBusy((e) => seen.push([e.pending, e.message]));
  await downloadFile('/api/neurons/RC1/410/export/stl', 'c410.stl',
    { label: 'exporting c410.stl…', save: () => {} });
  off();
  assert.deepEqual(seen[0], [1, 'exporting c410.stl…']);
  assert.equal(seen.at(-1)[0], 0);
});

test('api.download is downloadFile', () => {
  assert.equal(api.download, downloadFile);
});

// -- a bulk archive that is not whole ------------------------------------------------
// The route sends a partial archive with a 200 (the time budget ran out; a cell failed), and the viewer
// reads the response and not the zip: the counts are in X-Archive-* headers.
const PARTIAL = { 'X-Archive-Asked': '150', 'X-Archive-Written': '130', 'X-Archive-Failed': '1',
  'X-Archive-Not-Attempted': '19' };

test('a download carries the archive report the server sent, and only then', async () => {
  fakeFetch({ headers: PARTIAL, blob: new Blob(['0123456789']) });
  const done = await downloadFile('/api/neurons/RC1/export-bulk/swc?ids=1', 'RC1-swc.zip', { save: () => {} });
  assert.deepEqual(done.archive, { asked: 150, written: 130, failed: 1, notAttempted: 19 });
  assert.equal(done.bytes, 10);
  fakeFetch({});
  const plain = await downloadFile('/api/neurons/RC1/410/export/obj', 'c410.obj', { save: () => {} });
  assert.ok(!('archive' in plain), 'a mesh export has no archive report');
});

test('archiveReport trusts only whole non-negative counts', () => {
  const of = (headers) => archiveReport(new Headers(headers));
  assert.equal(of({}), null);
  assert.equal(archiveReport(undefined), null);
  assert.equal(archiveReport({}), null, 'not a Headers');
  assert.equal(of({ 'X-Archive-Asked': 'many' }), null);
  assert.equal(of({ 'X-Archive-Asked': '-1' }), null);
  assert.equal(of({ 'X-Archive-Asked': '2.5' }), null);
  assert.deepEqual(of({ 'X-Archive-Asked': '3' }), { asked: 3, written: 0, failed: 0, notAttempted: 0 });
  assert.deepEqual(of({ ...PARTIAL, 'X-Archive-Failed': 'x' }),
    { asked: 150, written: 130, failed: 0, notAttempted: 19 });
});

test('a truncated archive is not reported as an export that went well', () => {
  const notice = X.savedNotice('RC1-swc.zip', 3_200_000,
    { asked: 150, written: 130, failed: 1, notAttempted: 19 });
  assert.equal(notice.partial, true);
  assert.match(notice.text, /^Exported RC1-swc\.zip \(3\.2 MB\)/);
  assert.match(notice.text, /only 130 of 150 skeletons/);
  assert.match(notice.text, /19 not attempted/);
  assert.match(notice.text, /1 failed/);
  assert.match(notice.text, /manifest\.csv/);
  // only the failures, or only the time limit, name only what happened
  assert.doesNotMatch(X.savedNotice('a.zip', 5, { asked: 2, written: 1, failed: 1, notAttempted: 0 }).text,
    /not attempted/);
  assert.doesNotMatch(X.savedNotice('a.zip', 5, { asked: 2, written: 1, failed: 0, notAttempted: 1 }).text,
    /failed/);
});

test('a whole archive, and a file that is not an archive, read as they always did', () => {
  const whole = { asked: 150, written: 150, failed: 0, notAttempted: 0 };
  assert.deepEqual(X.savedNotice('RC1-swc.zip', 3_200_000, whole),
    { text: 'Exported RC1-swc.zip (3.2 MB)', partial: false });
  assert.deepEqual(X.savedNotice('c410.obj', 840 * 1024),
    { text: 'Exported c410.obj (840 kB)', partial: false });
  assert.deepEqual(X.savedNotice('c410.obj', 840 * 1024, null).partial, false);
});

test('several archives are summed, and a short one is not buried by the next', () => {
  const r = (asked, written, failed = 0, notAttempted = 0) => ({ asked, written, failed, notAttempted });
  // One archive: the line the save already showed stands.
  assert.equal(X.bulkSwcSummary({ name: 'Inferior Monkey', cells: 150, archives: 1, reports: [r(150, 130, 0, 20)] }), null);
  // Several and whole: as before.
  const whole = X.bulkSwcSummary({ name: 'Inferior Monkey', cells: 300, archives: 2, reports: [r(200, 200), r(100, 100)] });
  assert.equal(whole.partial, false);
  assert.match(whole.text, /^Exported 300 skeletons of Inferior Monkey in 2 archives: cells drawn with different/);
  // Several and one short: the sum says so.
  const short = X.bulkSwcSummary({ name: 'Inferior Monkey', cells: 300, archives: 2, reports: [r(200, 160, 0, 40), r(100, 100)] });
  assert.equal(short.partial, true);
  assert.match(short.text, /Exported 260 of 300 skeletons of Inferior Monkey in 2 archives/);
  assert.match(short.text, /40 not attempted/);
  assert.match(short.text, /manifest\.csv/);
  // An archive the server said nothing about is not counted as a short one.
  const unknown = X.bulkSwcSummary({ name: 'Inferior Monkey', cells: 300, archives: 2, reports: [null, null] });
  assert.equal(unknown.partial, false);
  assert.match(unknown.text, /^Exported 300 skeletons/);
});

// -- the bulk SWC archives --------------------------------------------------------
const cell = (id, { registration, corrections, lr, sc } = {}) => ({
  id, source: 'RC1', registration, corrections,
  summary: { live_registration: lr, section_correction: sc },
});

test('cells drawn the same way go in one archive, in the order given', () => {
  const plan = X.planBulkSwc([cell(3), cell(1), cell(2)]);
  assert.equal(plan.length, 1);
  assert.deepEqual(plan[0].ids, [3, 1, 2]);
  assert.equal(plan[0].registration, undefined);   // the server's default
  assert.equal(plan[0].corrections, undefined);
});

test('what the server applied is what the archive is drawn with', () => {
  const way = X.drawnWith(cell(1, {
    registration: undefined, corrections: true,
    lr: { requested: true, applied: true }, sc: { requested: true, mode: 'curvefit', applied: true },
  }));
  assert.deepEqual(way, { registration: true, corrections: 'curvefit' });
  const off = X.drawnWith(cell(1, {
    lr: { requested: false }, sc: { requested: false, applied: false },
  }));
  assert.deepEqual(off, { registration: false, corrections: 'off' });
});

test('a legacy on/off correction is the residual field / off', () => {
  assert.equal(X.drawnWith(cell(1, { corrections: true })).corrections, 'neighbor');
  assert.equal(X.drawnWith(cell(1, { corrections: false })).corrections, 'off');
  assert.equal(X.drawnWith(cell(1, { corrections: 'all' })).corrections, 'all');
});

test('cells drawn differently are not mixed in one archive', () => {
  const plan = X.planBulkSwc([
    cell(1, { corrections: 'curvefit' }), cell(2, { corrections: 'off' }),
    cell(3, { corrections: 'curvefit' }), cell(4, { corrections: 'off', registration: false }),
  ]);
  assert.equal(plan.length, 3);
  assert.deepEqual(plan.map((p) => p.ids), [[1, 3], [2], [4]]);
  assert.deepEqual(plan.map((p) => p.corrections), ['curvefit', 'off', 'off']);
  assert.deepEqual(plan.map((p) => p.registration), [undefined, undefined, false]);
});

test('more than the route\'s limit is split into archives of at most that many', () => {
  const many = Array.from({ length: 450 }, (_, i) => cell(i + 1));
  const plan = X.planBulkSwc(many);
  assert.deepEqual(plan.map((p) => p.ids.length), [200, 200, 50]);
  assert.equal(X.BULK_SWC_LIMIT, 200);
  assert.deepEqual(plan.flatMap((p) => p.ids), many.map((c) => c.id));
  assert.deepEqual(X.planBulkSwc([]), []);
});

test('the bulk link carries the way the archive is drawn, and leaves a default out', () => {
  const link = (opts) => api.bulkSwcUrl('RC1', [1, 2], { pattern: 'c{id}', ...opts });
  const url = new URL(link({ live_registration: false, section_corrections: 'off' }), 'http://x');
  assert.equal(url.searchParams.get('live_registration'), 'false');   // false is not "absent"
  assert.equal(url.searchParams.get('section_corrections'), 'off');
  assert.equal(url.searchParams.get('ids'), '1,2');
  const plain = new URL(link({ live_registration: undefined, section_corrections: undefined }), 'http://x');
  assert.ok(!plain.searchParams.has('live_registration'));
  assert.ok(!plain.searchParams.has('section_corrections'));
});

// -- the export link ------------------------------------------------------------------
const asked = (render, fmt = 'obj') => new URL(
  api.exportUrl('RC1', 410, fmt, X.exportQuery(render)), 'http://x').searchParams;

test('an export link carries the on-screen closed-curve raster', () => {
  const params = asked({ method: 'closedcurve', pixel_size_um: 0.25 });
  assert.equal(params.get('pixel_size_um'), '0.25');
  assert.equal(params.get('method'), 'closedcurve');
});

test('an export link carries every geometry option of the render, and not its look', () => {
  const render = normalizeRender({ pathway: 'voxel2', voxel2_um: 0.07, meshlab: 'qem', color_by_strata: true });
  const params = asked(render);
  assert.equal(params.get('pathway'), 'voxel2');
  assert.equal(params.get('voxel2_um'), '0.07');
  assert.equal(params.get('meshlab'), 'qem');
  assert.ok(!params.has('color_by_strata'));
  // 34 geometry options (Smooth surface µm and Exact tubes ° joined them, 2.9.0), none
  // dropped by the link
  assert.equal([...params.keys()].length, 34);
});

test('an export of a clipped render carries the clip, so the file is the surface on screen', () => {
  const params = asked({ clip_ipl: 0.5, keep_above: false });
  assert.equal(params.get('clip_ipl'), '0.5');
  assert.equal(params.get('keep_above'), 'false');   // false is a side, not "absent"
  const zero = asked({ clip_ipl: 0 });                // 0 is the INL border, not "no clip"
  assert.equal(zero.get('clip_ipl'), '0');
});

test('an unclipped render adds no clip to the link', () => {
  const params = asked({});
  assert.ok(!params.has('clip_ipl'));
  assert.ok(!params.has('keep_above'));
  assert.ok(!params.has('clip_z'));
});

test('the clip an export link carries is named as the schema names its view fields', async () => {
  // The export routes declare clip_ipl, keep_above and clip_z (routes._export_clip; checked against the
  // mesh route and this schema in tests/unit/test_web.py).  The link carries the first two, by those names.
  const { RENDER_FIELDS } = await load('render-schema.js');
  const view = new Set(RENDER_FIELDS.filter((f) => f.scope === 'view').map((f) => f.key));
  const query = X.exportQuery({ clip_ipl: 0.5, keep_above: false, color_by_strata: true });
  assert.deepEqual(Object.keys(query).filter((k) => view.has(k)).sort(), ['clip_ipl', 'keep_above']);
  assert.ok(view.has('clip_z'), 'the third clip field is still the schema\'s');
  // and nothing else of the schema's view fields: depth coloring is a look
  assert.ok(!('color_by_strata' in query));
});

test('a clipped export measures its depth as the page does, and an unclipped one says nothing of it', () => {
  const clipped = asked({ clip_ipl: 0.5, keep_above: true }, 'obj');
  assert.ok(!clipped.has('ipl_method'), 'without the page\'s method the server\'s default stands');
  const query = X.exportQuery({ clip_ipl: 0.5, keep_above: true, color_by_strata: true }, 'normal');
  assert.deepEqual(Object.keys(query).filter((k) => ['clip_ipl', 'keep_above', 'ipl_method'].includes(k)).sort(),
    ['clip_ipl', 'ipl_method', 'keep_above']);
  assert.equal(query.ipl_method, 'normal');
  assert.ok(!('ipl_method' in X.exportQuery({ color_by_strata: true }, 'normal')), 'coloring is not exported');
  assert.equal(X.exportQuery({ clip_ipl: 0 }, 'column').ipl_method, 'column', '0 is the INL border, a clip');
});

test('a flattened view\'s export is flattened as drawn, by the page\'s method', () => {
  const flat = X.exportQuery({}, 'normal', true);
  assert.equal(flat.ipl_flatten, true);
  assert.equal(flat.ipl_method, 'normal');
  const plain = X.exportQuery({}, 'normal');
  assert.ok(!('ipl_flatten' in plain) && !('ipl_method' in plain), 'unflattened: the link it always was');
  const clipped = X.exportQuery({ clip_ipl: 0.3 }, 'column', true);
  assert.equal(clipped.clip_ipl, 0.3);
  assert.equal(clipped.ipl_method, 'column');
});

test('fileSize', () => {
  assert.equal(X.fileSize(3_200_000), '3.2 MB');
  assert.equal(X.fileSize(840 * 1024), '840 kB');
  assert.equal(X.fileSize(10), '1 kB');
  assert.equal(X.fileSize(undefined), '');
});

// -- run -------------------------------------------------------------------------
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
