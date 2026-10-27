// A finished mesh poll's binary reply (static/js/api.js `decodeMeshReply`), read
// with the page's own code.  Run it with
//
//     node tests/js/test_meshbinary.mjs
//
// tests/fixtures/mesh_binary.json holds replies the server's encoder made
// (`sbfsem_web/mesh_binary.py`), each beside the JSON reply of the same job;
// tests/unit/test_mesh_binary.py checks the fixture is what the encoder makes
// today.  The decoded job must hold exactly the numbers JSON.parse gives, bit
// for bit: the sign of a zero, and `k / 1e4` where `k * 1e-4` would differ.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const {
  api, decodeMeshReply, meshReplySize, MESH_MEDIA_TYPE, POLL_TIMING,
} = await load('api.js');
const { meshWeight } = await load('appcore.js');

const fixture = JSON.parse(fs.readFileSync(path.join(here, '..', 'fixtures', 'mesh_binary.json'), 'utf8'));
Object.assign(POLL_TIMING, { first: 1, every: 2, giveUp: 60000, requestTimeout: 0 });

/** A reply's bytes as an ArrayBuffer of their own, as `response.arrayBuffer()` gives. */
function bufferOf(base64) {
  const bytes = Buffer.from(base64, 'base64');
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

/** Every number the same bits (Object.is tells -0 from 0), the same count. */
function sameNumbers(got, want, what) {
  assert.equal(got.length, want.length, `${what}: length`);
  for (let i = 0; i < want.length; i += 1) {
    if (!Object.is(got[i], want[i])) {
      assert.fail(`${what}[${i}]: ${got[i]} where the JSON has ${want[i]}`);
    }
  }
}

function sameAsJson(decoded, parsed) {
  const { mesh, ...status } = decoded;
  const { mesh: want, ...wantStatus } = parsed;
  assert.deepEqual(status, wantStatus);
  assert.deepEqual(Object.keys(mesh), Object.keys(want), 'the mesh keys, in the JSON order');
  for (const key of Object.keys(want)) {
    if (['vertices', 'faces', 'vertex_data'].includes(key)) continue;
    assert.deepEqual(mesh[key], want[key], key);
  }
  assert.ok(mesh.vertices instanceof Float64Array, 'vertices stay float64');
  assert.ok(mesh.faces instanceof Uint32Array);
  sameNumbers(mesh.vertices, want.vertices, 'vertices');
  sameNumbers(mesh.faces, want.faces, 'faces');
  if (want.vertex_data === null) {
    assert.equal(mesh.vertex_data, null);
  } else {
    assert.ok(mesh.vertex_data instanceof Float64Array);
    sameNumbers(mesh.vertex_data, want.vertex_data, 'vertex_data');
  }
}

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

for (const [name, reply] of Object.entries(fixture)) {
  test(`${name}: the binary decodes to exactly what JSON.parse gives`, () => {
    sameAsJson(decodeMeshReply(bufferOf(reply.binary)), JSON.parse(reply.json));
  });
}

for (const [name, reply] of Object.entries(fixture)) {
  test(`${name}: the header says how long the whole reply is and how many triangles it carries`, () => {
    const bytes = new Uint8Array(bufferOf(reply.binary));
    const parsed = JSON.parse(reply.json);
    assert.deepEqual(meshReplySize(bytes), { total: bytes.byteLength, faces: parsed.mesh.n_faces });
    // Known from the header alone, before any of the arrays has arrived.
    const headerEnd = 4 + new DataView(bytes.buffer).getUint32(0, true);
    assert.deepEqual(meshReplySize(bytes.subarray(0, headerEnd)), meshReplySize(bytes));
    assert.equal(meshReplySize(bytes.subarray(0, headerEnd - 1)), null, 'not until the header is whole');
    assert.equal(meshReplySize(bytes.subarray(0, 3)), null);
  });
}

test('the tricky coordinates: both zeros, int32\'s ends, steps where k * 1e-4 differs', () => {
  const { mesh } = decodeMeshReply(bufferOf(fixture.tricky.binary));
  assert.ok(Object.is(mesh.vertices[0], 0) && Object.is(mesh.vertices[1], -0));
  assert.equal(mesh.vertices[4], 0.0003);
  assert.notEqual(3 * 1e-4, 0.0003, 'the reason the page divides');
  assert.equal(mesh.vertices[10], 214748.3647);
  assert.equal(mesh.vertices[11], -214748.3647);
});

test('the decoded arrays are their own: the reply\'s buffer can go', () => {
  const buffer = bufferOf(fixture.vertex_data.binary);
  const { mesh } = decodeMeshReply(buffer);
  for (const key of ['vertices', 'faces', 'vertex_data']) {
    assert.notEqual(mesh[key].buffer, buffer, key);
    assert.equal(mesh[key].byteOffset, 0, key);
  }
  assert.equal(meshWeight(mesh), mesh.vertices.length + mesh.faces.length + mesh.vertex_data.length);
});

/** A fake server: the mesh-job start, then one poll answered by `poll(options)`. */
function serve(poll) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    if (String(url).includes('/mesh-job?') || String(url).endsWith('/mesh-job')) {
      return { ok: true, status: 200, json: async () => ({ job_id: 'j1', status: 'queued' }) };
    }
    return poll(options);
  };
  return calls;
}

test('a poll asks for the binary, and a binary answer gives the decoded mesh', async () => {
  const calls = serve(() => ({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': MESH_MEDIA_TYPE }),
    arrayBuffer: async () => bufferOf(fixture.vertex_data.binary),
    json: async () => assert.fail('a binary answer is not JSON'),
  }));
  const mesh = await api.mesh('i', 943, { pathway: 'voxel2' });
  sameAsJson({ ...JSON.parse(fixture.vertex_data.json), mesh }, JSON.parse(fixture.vertex_data.json));
  const poll = calls.find((c) => c.url.startsWith('/api/mesh-jobs/'));
  assert.match(poll.options.headers.Accept, /^application\/x-sbfsem-mesh\b/);
  assert.match(poll.options.headers.Accept, /application\/json/);
});

test('a JSON answer (an older server, a mesh the binary cannot carry) still works', async () => {
  serve(() => ({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    arrayBuffer: async () => assert.fail('a JSON answer is not binary'),
    json: async () => JSON.parse(fixture.tricky.json),
  }));
  const mesh = await api.mesh('i', 943, {});
  assert.ok(Array.isArray(mesh.vertices));
  sameNumbers(mesh.vertices, JSON.parse(fixture.tricky.json).mesh.vertices, 'vertices');
});

test('an answer with no headers at all is read as JSON', async () => {
  serve(() => ({ ok: true, status: 200, json: async () => ({ job_id: 'j1', status: 'done', mesh: { n_faces: 0 } }) }));
  assert.deepEqual(await api.mesh('i', 943, {}), { n_faces: 0 });
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
