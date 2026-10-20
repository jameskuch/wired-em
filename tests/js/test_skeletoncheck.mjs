// The Skeleton check section (static/js/skeletoncheck.js) against a minimal fake
// page: what the line under the facts says while the shortest path through every
// annotation is being found, when that request fails, and what a problem is
// called.  There is no JS runner in this repo: run it with
//
//     node tests/js/test_skeletoncheck.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');

// Just enough of a document for the section: elements that remember what they were
// given, and a click that runs the handlers and waits for them.
class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.disabled = false;
    this.listeners = {};
    this.classList = { add: (name) => { this.className = `${this.className} ${name}`.trim(); } };
  }

  append(...kids) { this.children.push(...kids); }

  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }

  async click() { for (const handler of this.listeners.click || []) await handler(); }

  find(predicate, out = []) {
    if (predicate(this)) out.push(this);
    for (const kid of this.children) kid.find(predicate, out);
    return out;
  }
}
globalThis.document = { createElement: (tag) => new Element(tag) };

const { buildSkeletonCheck, problemLabel, forgetSkeletonCheck } = await import(
  pathToFileURL(path.join(jsDir, 'skeletoncheck.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const record = { key: 'NeitzInferiorMonkey/1178', source: 'NeitzInferiorMonkey', id: 1178,
  summary: { data_stamp: '2026-09-23T22:13:01.890Z', copy_tag: 'off.0' } };
const result = {
  summary: {
    n_annotations: 1200, n_components: 1, n_loops: 0, total_length_um: 1072.9, swc_length_um: 1000,
    rim_corrected_length_um: 990, n_spokes: 0, spoke_ratio: 2, spoke_length_um: 0, spoke_length_by_radius_um: 0,
  },
  problems: [],
};

/** A section for `record` with a fake server: `morphometrics` is answered by `answer`. */
function section(answer) {
  const calls = { rerender: 0, fail: [] };
  const body = new Element('div');
  const deps = {
    api: { skeletonCheck: async () => result, morphometrics: answer },
    renderer: {}, nodesFor: async () => [], openLocationInViking: null,
    rerender: () => { calls.rerender += 1; },
    fail: (error) => { calls.fail.push(error); },
    volumeName: (source) => source,
  };
  const draw = () => {
    body.children.length = 0;
    buildSkeletonCheck(body, record, deps);
    return body;
  };
  return { calls, draw, body };
}
const text = (root) => root.find((e) => e.className.includes('skel-facts')).map((e) => e.textContent);
const settle = () => new Promise((resolve) => setTimeout(resolve, 5));

test('while the shortest path is being found the line says so', async () => {
  forgetSkeletonCheck(record.key);
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const s = section(() => slow.then(() => ({ length_floor_um: 880.5, length_floor_by_radius_um: 870 })));
  const run = s.draw().find((e) => e.tagName === 'button')[0];
  await run.click();
  assert.ok(text(s.draw()).includes('finding the shortest path through every annotation…'));
  release();
  await settle();
  assert.ok(text(s.draw()).some((t) => t.startsWith('no shorter than 880.5 µm')), text(s.draw()).join(' | '));
});

test('a failed shortest-path request is said, and the section is drawn again', async () => {
  forgetSkeletonCheck(record.key);
  const s = section(async () => { throw new Error('504 — the server took too long'); });
  const run = s.draw().find((e) => e.tagName === 'button')[0];
  await run.click();
  await settle();
  // One redraw when the check starts, one when it ends, and one for the failed request:
  // without the last, "finding the shortest path..." stays on screen until something else redraws.
  assert.equal(s.calls.rerender, 3);
  const lines = s.draw().find((e) => e.className.includes('skel-facts'));
  const failed = lines.find((e) => e.textContent.includes('could not find'));
  assert.ok(failed, lines.map((e) => e.textContent).join(' | '));
  assert.match(failed.textContent, /504 — the server took too long/);
  assert.ok(failed.className.includes('error'), 'marked as an error');
  assert.ok(!lines.some((e) => e.textContent.includes('finding the shortest path')));
  assert.deepEqual(s.calls.fail, [], 'the list found stands: nothing is reported over it');
});

test('a failure that arrives after the cell was forgotten changes nothing', async () => {
  forgetSkeletonCheck(record.key);
  let fail;
  const late = new Promise((_, reject) => { fail = reject; });
  const s = section(() => late);
  await s.draw().find((e) => e.tagName === 'button')[0].click();
  const before = s.calls.rerender;
  forgetSkeletonCheck(record.key);
  fail(new Error('too late'));
  await settle();
  assert.equal(s.calls.rerender, before, 'no redraw for a check that is gone');
});

test('an older run\'s failure cannot overwrite a newer run\'s answer', async () => {
  forgetSkeletonCheck(record.key);
  const rejects = [];
  let calls = 0;
  const s = section(() => {
    calls += 1;
    if (calls === 1) return new Promise((_, reject) => { rejects.push(reject); });   // run 1 hangs
    return Promise.resolve({ length_floor_um: 880.5, length_floor_by_radius_um: 870 }); // run 2 answers
  });
  await s.draw().find((e) => e.tagName === 'button')[0].click();   // Check the skeleton
  await s.draw().find((e) => e.tagName === 'button')[0].click();   // Check again
  await settle();
  assert.ok(text(s.draw()).some((t) => t.startsWith('no shorter than 880.5 µm')), 'run 2 answered');
  rejects[0](new Error('504 gateway timeout'));                     // run 1 fails, late
  await settle();
  const lines = text(s.draw());
  assert.ok(lines.some((t) => t.startsWith('no shorter than 880.5 µm')), lines.join(' | '));
  assert.ok(!lines.some((t) => t.includes('could not find')), 'the stale failure is not shown');
});

test('a problem is named in the singular: Lamellae is a Lamella, not Lamellae', () => {
  assert.equal(problemLabel('lamella'), 'Lamella');
  assert.equal(problemLabel('loop'), 'Loop');
  assert.equal(problemLabel('fragment'), 'Unlinked piece');
  assert.equal(problemLabel('hairpin'), 'Hairpin');
  assert.equal(problemLabel('unheard-of'), 'unheard-of', 'a kind this page does not know is named as it came');
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
