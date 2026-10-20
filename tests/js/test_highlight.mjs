// How a selection ring looks as it is chosen and as it settles (static/js/highlight.js).
// There is no JS runner in this repo: run it with
//
//     node tests/js/test_highlight.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const { HOLD_MS, SETTLE_MS, settleProgress, highlightLook, pulses } = await import(
  pathToFileURL(path.join(jsDir, 'highlight.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('a ring is chosen at the strength it always had: the tube, a glow three times as wide, 30 % opacity', () => {
  const look = highlightLook(0);
  assert.deepEqual([look.core, look.glow, look.alpha], [1, 3, 0.3]);
});

test('it settles to a calmer one: a thinner tube, a narrower and fainter glow', () => {
  const chosen = highlightLook(0);
  const settled = highlightLook(1);
  assert.ok(settled.core < chosen.core && settled.glow < chosen.glow && settled.alpha < chosen.alpha);
  assert.ok(Math.abs(settled.core - 0.8) < 1e-12);
  assert.ok(Math.abs(settled.glow - 2) < 1e-12);
  assert.ok(Math.abs(settled.alpha - 0.2) < 1e-12);
  // "just a bit": not a third of the strength, and still wider than the tube.
  assert.ok(settled.glow > 1.5 && settled.core > 0.7 && settled.alpha > 0.15);
});

test('it is held at full strength for a moment, then settles smoothly and stops', () => {
  assert.equal(settleProgress(0), 0);
  assert.equal(settleProgress(HOLD_MS), 0);
  assert.equal(settleProgress(HOLD_MS + SETTLE_MS), 1);
  assert.equal(settleProgress(HOLD_MS + SETTLE_MS * 5), 1, 'and stays settled');
  let last = 0;
  for (let t = HOLD_MS; t <= HOLD_MS + SETTLE_MS; t += 25) {
    const p = settleProgress(t);
    assert.ok(p >= last, `no step back at ${t} ms`);
    last = p;
  }
  // An ease-out: the first half of the time does more than half of the settling.
  assert.ok(settleProgress(HOLD_MS + SETTLE_MS / 2) > 0.5);
  assert.equal(settleProgress(-5), 0);
  assert.equal(settleProgress(Number.NaN), 0);
});

test('between the two, every part is between them and none grows back', () => {
  let was = highlightLook(0);
  for (let p = 0.05; p <= 1.0001; p += 0.05) {
    const look = highlightLook(p);
    assert.ok(look.core <= was.core + 1e-12 && look.glow <= was.glow + 1e-12 && look.alpha <= was.alpha + 1e-12);
    was = look;
  }
  assert.deepEqual(highlightLook(2), highlightLook(1), 'past the end is the end');
  assert.deepEqual(highlightLook(-1), highlightLook(0), 'before the start is the start');
  assert.deepEqual(highlightLook(Number.NaN), highlightLook(1), 'no number means settled, never the pulse');
});

test('the skeleton check\'s rings, dozens at a time, keep their fainter glow and do not pulse', () => {
  const look = highlightLook(0, 'nodes');
  assert.ok(Math.abs(look.alpha - 0.12) < 1e-12, `the old 0.12, got ${look.alpha}`);
  assert.ok(highlightLook(1, 'nodes').alpha < look.alpha);
  assert.equal(pulses('nodes'), false);
  for (const kind of ['annotation', 'outline', 'skeleton', 'wireframe', 'synapse']) {
    assert.equal(pulses(kind), true, kind);
  }
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
