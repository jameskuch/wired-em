// Display → Mark damaged sections (static/js/damaged.js): the rings it draws and what
// the cell panel says.  There is no JS runner in this repo: run it with
//
//     node tests/js/test_damaged.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const {
  DAMAGED_MIN_RADIUS_UM, DAMAGED_RING_SCALE, DAMAGED_SIDES, damagedRingSegments, damagedText, sectionRanges,
} = await import(pathToFileURL(path.join(jsDir, 'damaged.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, eps = 1e-5) => Math.abs(a - b) <= eps;

test('an annotation on a damaged section is a solid ring in its section plane', () => {
  const seg = damagedRingSegments([{ xyz_um: [10, 20, 64.4], radius_um: 2, over: false }]);
  assert.equal(seg.length, DAMAGED_SIDES * 6);
  const r = 2 * DAMAGED_RING_SCALE;
  for (let s = 0; s < DAMAGED_SIDES; s += 1) {
    const at = s * 6;
    // Both ends on the circle, at the section's Z: a disc, not a sphere.
    assert.ok(close(Math.hypot(seg[at] - 10, seg[at + 1] - 20), r));
    assert.ok(close(Math.hypot(seg[at + 3] - 10, seg[at + 4] - 20), r));
    assert.ok(close(seg[at + 2], 64.4) && close(seg[at + 5], 64.4));
  }
  // Closed: the last side ends where the first began.
  const last = (DAMAGED_SIDES - 1) * 6;
  assert.ok(close(seg[last + 3], seg[0]) && close(seg[last + 4], seg[1]));
});

test('a link over a damaged section is a dashed ring: every other side', () => {
  const seg = damagedRingSegments([{ xyz_um: [0, 0, 1], radius_um: 1, over: true }]);
  assert.equal(seg.length, (DAMAGED_SIDES / 2) * 6);
  // The second side drawn starts two sides on from the first.
  const step = (2 * Math.PI * 2) / DAMAGED_SIDES;
  const r = DAMAGED_RING_SCALE;
  assert.ok(close(seg[6], r * Math.cos(step)) && close(seg[7], r * Math.sin(step)));
});

test('a point annotation still gets a ring, and a mark with no place gets none', () => {
  const seg = damagedRingSegments([
    { xyz_um: [0, 0, 0], radius_um: 0, over: false },
    { xyz_um: [NaN, 0, 0], radius_um: 1, over: false },
    { xyz_um: [0, 0], radius_um: 1, over: false },
    null,
  ]);
  assert.equal(seg.length, DAMAGED_SIDES * 6);
  assert.ok(close(Math.hypot(seg[0], seg[1]), DAMAGED_MIN_RADIUS_UM));
  assert.equal(damagedRingSegments(null).length, 0);
  assert.equal(damagedRingSegments([]).length, 0);
});

test('sections read as runs', () => {
  assert.equal(sectionRanges([930, 915, 916, 917, 917]), '915–917, 930');
  assert.equal(sectionRanges([792]), '792');
  assert.equal(sectionRanges([]), '');
});

test('no list says so, in the server’s words', () => {
  const t = damagedText({ listed: false, volume_name: 'RC1', note: 'No damaged sections listed for RC1' });
  assert.equal(t.text, 'No damaged sections listed for RC1');
  assert.equal(t.crossed, 0);
  assert.equal(damagedText({ listed: false }).text, 'No damaged sections listed for this volume');
  assert.equal(damagedText(null).text, 'Damaged sections: asking…');
});

test('a cell that crosses none says how many the volume lists, and which', () => {
  const t = damagedText({ listed: true, volume_name: 'RC2', damaged_z: [0, 5, 792], sections: [] });
  assert.equal(t.text, "Crosses none of RC2's 3 damaged sections");
  assert.deepEqual(t.lines, ["RC2's damaged sections: 0, 5, 792"]);
});

test('the crossed sections read as runs, with their annotations and links over', () => {
  const sections = [
    { z: 915, n_annotations: 3, n_links_over: 0 },
    ...Array.from({ length: 20 }, (_, i) => ({ z: 916 + i, n_annotations: 0, n_links_over: 2 })),
    { z: 1300, n_annotations: 1, n_links_over: 1 },
  ];
  const t = damagedText({
    listed: true, volume_name: 'InferiorMonkey', damaged_z: [396, ...sections.map((s) => s.z)], sections,
  });
  assert.equal(t.crossed, 22);
  assert.equal(t.text, "Crosses 22 of InferiorMonkey's 23 damaged sections: 915–935, 1300");
  assert.deepEqual(t.lines, [
    '915: 3 annotations', '916–935: 2 links over each', '1300: 1 annotation, 1 link over']);
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
