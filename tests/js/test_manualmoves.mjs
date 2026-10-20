// Manual moves (static/js/manualmoves.js): the store kept per cell and registration
// setting, what a load sends, a step along each axis, and the list's rows.  Run with
//
//     node tests/js/test_manualmoves.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const M = await load('manualmoves.js');
const S = await load('shifts.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const close = (a, b, tol = 1e-9, what = '') => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} vs ${b}`);

// -- the store -----------------------------------------------------------------------------

test('a copy tag names its setting; the words for it', () => {
  assert.equal(M.baseTag('curvefit.1'), 'curvefit.1');
  assert.equal(M.baseTag('curvefit.1.m0123456789ab'), 'curvefit.1');
  assert.equal(M.baseTag(''), null);
  assert.equal(M.baseTag(undefined), null);
  assert.equal(M.tagWords('curvefit.1'), 'Curve fit correction, current transforms on');
  assert.equal(M.tagWords('off.0'), 'no section correction, current transforms off');
});

test('moves are set, replaced, deleted and reset per setting; each change is a new object', () => {
  let store = {};
  store = M.setMove(store, 'curvefit.1', 7, { dx: 0.1, dy: -0.2, dz: 0 }, 100);
  store = M.setMove(store, 'curvefit.1', 9, { dx: 0, dy: 0, dz: 2 }, 200);
  store = M.setMove(store, 'off.0', 7, { dx: 1 }, 300);
  const before = store;
  store = M.setMove(store, 'curvefit.1', 7, { dx: 0.3, dy: -0.2, dz: 0 }, 400);
  assert.notEqual(store, before);
  assert.deepEqual(before['curvefit.1']['7'], { dx: 0.1, dy: -0.2, dz: 0, t: 100 }, 'the old store is untouched');
  assert.deepEqual(store['curvefit.1']['7'], { dx: 0.3, dy: -0.2, dz: 0, t: 400 });
  assert.deepEqual(M.moveCounts(store, 'curvefit.1'), { here: 2, elsewhere: 1, other: [['off.0', 1]] });
  // A move back to nothing is no move.
  store = M.setMove(store, 'curvefit.1', 9, { dx: 0, dy: 0, dz: 0 });
  assert.equal(store['curvefit.1']['9'], undefined);
  store = M.deleteMoves(store, 'curvefit.1', [7]);
  assert.equal(store['curvefit.1'], undefined, 'an empty setting goes');
  assert.deepEqual(Object.keys(store), ['off.0']);
  store = M.setMove(store, 'curvefit.1', 1, { dx: 1 });
  store = M.resetMoves(store, 'curvefit.1');
  assert.deepEqual(Object.keys(store), ['off.0'], 'Reset all leaves the other settings\' moves');
});

test('a saved store is made safe: shapes dropped, numbers clamped and rounded', () => {
  assert.deepEqual(M.normalizeStore(null), {});
  assert.deepEqual(M.normalizeStore('moves'), {});
  assert.deepEqual(M.normalizeStore([1, 2]), {});
  const out = M.normalizeStore({
    'curvefit.1': {
      5: { dx: 0.123456789, dy: 'x', dz: 2.6 },
      six: { dx: 1 },
      7: { dx: 500, dy: -500, dz: -99 },
      8: { dx: 0, dy: 0, dz: 0 },
      9: null,
    },
    'bad tag': { 1: { dx: 1 } },
    'off.0': {},
  });
  assert.deepEqual(out, {
    'curvefit.1': {
      5: { dx: 0.1235, dy: 0, dz: 3, t: 0 },
      7: { dx: M.MAX_MOVE_UM, dy: -M.MAX_MOVE_UM, dz: -M.MAX_MOVE_SECTIONS, t: 0 },
    },
  });
});

test('a load sends every setting\'s moves, sorted, in the server\'s words; none is null', () => {
  let store = M.setMove({}, 'curvefit.1', 30, { dx: 0.25 });
  store = M.setMove(store, 'curvefit.1', 4, { dz: -1 });
  store = M.setMove(store, 'off.0', 2, { dy: 0.5 });
  assert.deepEqual(M.movesBody(store), {
    'curvefit.1': [{ id: 4, dx_um: 0, dy_um: 0, dz: -1 }, { id: 30, dx_um: 0.25, dy_um: 0, dz: 0 }],
    'off.0': [{ id: 2, dx_um: 0, dy_um: 0.5, dz: 0 }],
  });
  assert.equal(M.movesBody({}), null);
  assert.equal(M.movesBody(undefined), null);
  // The signature ignores when a move was made.
  const later = M.setMove(store, 'curvefit.1', 30, { dx: 0.25 }, 99999);
  assert.equal(M.setSignature(store, 'curvefit.1'), M.setSignature(later, 'curvefit.1'));
  assert.notEqual(M.setSignature(store, 'curvefit.1'), M.setSignature(store, 'off.0'));
});

// -- a step along an axis ---------------------------------------------------------------------

const top = { right: [1, 0, 0], up: [0, 1, 0], out: [0, 0, 1] };          // XY1: looking down Z
const side = { right: [1, 0, 0], up: [0, 0, 1], out: [0, -1, 0] };        // XZ: Z up the screen
const tilted = { right: [Math.SQRT1_2, Math.SQRT1_2, 0], up: [-0.5, 0.5, Math.SQRT1_2], out: [0.5, -0.5, Math.SQRT1_2] };

test('the volume\'s axes: X and Y by the step, Z by one section whatever the step', () => {
  let r = M.stepMove(null, 'x', 1, 0.05);
  assert.deepEqual([r.move, r.along], [{ dx: 0.05, dy: 0, dz: 0 }, 'xy']);
  r = M.stepMove(r.move, 'y', -1, 0.25);
  assert.deepEqual(r.move, { dx: 0.05, dy: -0.25, dz: 0 });
  r = M.stepMove(r.move, 'z', 1, 0.25);
  assert.deepEqual([r.move, r.along], [{ dx: 0.05, dy: -0.25, dz: 1 }, 'z']);
  r = M.stepMove(r.move, 'z', -1, 1);
  assert.equal(r.move.dz, 0);
  // Steps add up without float drift (0.1 nm kept).
  let m = null;
  for (let i = 0; i < 30; i += 1) m = M.stepMove(m, 'x', 1, 0.1).move;
  assert.equal(m.dx, 3);
});

test('the viewport\'s axes stay in the section plane, and move nothing where they point along Z', () => {
  // From above, Xp and Yp are X and Y; Zp points along Z and moves nothing.
  assert.deepEqual(M.stepMove(null, 'xp', 1, 0.1, { frame: top }).move, { dx: 0.1, dy: 0, dz: 0 });
  assert.deepEqual(M.stepMove(null, 'yp', -1, 0.1, { frame: top }).move, { dx: 0, dy: -0.1, dz: 0 });
  const zp = M.stepMove(null, 'zp', 1, 0.1, { frame: top });
  assert.deepEqual([zp.move, zp.along], [{ dx: 0, dy: 0, dz: 0 }, null]);
  // From the side, Yp is Z: it never carries the annotation to another section (2026-10-20:
  // it ran along the cell's own process); Zp is in the plane (toward the viewer: -Y).
  const yp = M.stepMove(null, 'yp', 1, 0.1, { frame: side });
  assert.deepEqual([yp.move, yp.along], [{ dx: 0, dy: 0, dz: 0 }, null]);
  assert.deepEqual(M.stepMove(null, 'zp', 1, 0.1, { frame: side }).move, { dx: 0, dy: -0.1, dz: 0 });
  // Tilted 45°: Yp moves in the plane, a full step.
  const t = M.stepMove(null, 'yp', 1, 0.1, { frame: tilted });
  assert.equal(t.along, 'xy');
  close(Math.hypot(t.move.dx, t.move.dy), 0.1, 1e-4, 'a full step in the plane');
  close(t.move.dx, -0.0707, 1e-4); close(t.move.dy, 0.0707, 1e-4);
  // Steep, 80° from the plane: under IN_PLANE_MIN of it lies in the plane -- nothing, never a section.
  const a = (80 * Math.PI) / 180;
  const steep = { right: [1, 0, 0], up: [0, Math.cos(a), Math.sin(a)], out: [0, -Math.sin(a), Math.cos(a)] };
  assert.ok(Math.cos(a) < M.IN_PLANE_MIN);
  assert.equal(M.stepMove(null, 'yp', 1, 0.1, { frame: steep }).along, null);
  // 70° from the plane: enough of it lies there, a full step in it.
  const b = (70 * Math.PI) / 180;
  const sloped = { right: [1, 0, 0], up: [0, Math.cos(b), Math.sin(b)], out: [0, -Math.sin(b), Math.cos(b)] };
  assert.deepEqual(M.stepMove(null, 'yp', 1, 0.1, { frame: sloped }).move, { dx: 0, dy: 0.1, dz: 0 });
  // No view axis ever answers 'z': only the volume's Z, chosen as such, changes the section.
  for (const frame of [top, side, tilted, steep, sloped]) {
    for (const axis of ['xp', 'yp', 'zp']) {
      for (const sign of [1, -1]) assert.equal(M.stepMove(null, axis, sign, 0.1, { frame }).move.dz, 0);
    }
  }
  // Without a frame a view axis does nothing.
  assert.equal(M.stepMove(null, 'xp', 1, 0.1).along, null);
});

test('the shift axis follows the annotation\'s own shift, and back', () => {
  const r = M.stepMove(null, 'shift', 1, 0.5, { shift: [0.3, -0.4] });
  close(r.move.dx, 0.3, 1e-9); close(r.move.dy, -0.4, 1e-9);
  const back = M.stepMove(r.move, 'shift', -1, 0.5, { shift: [0.3, -0.4] });
  assert.deepEqual(back.move, { dx: 0, dy: 0, dz: 0 });
  assert.equal(M.stepMove(null, 'shift', 1, 0.5, { shift: [0, 0] }).along, null);
  assert.equal(M.stepMove(null, 'shift', 1, 0.5).along, null);
  assert.match(M.axisNote('shift', 0.5), /no shift/);
  assert.equal(M.axisNote('z', 0.5), 'a section a step (+Z)');
  assert.equal(M.axisNote('x', 0.05), '0.05 µm a step along (+1.00, +0.00)');
  assert.match(M.axisNote('yp', 0.1, { frame: side }), /points along Z in this view/);
});

test('every axis is listed once, with help; the steps include the default', () => {
  assert.deepEqual([...M.AXIS_KEYS], ['x', 'y', 'z', 'xp', 'yp', 'zp', 'shift']);
  for (const a of M.AXES) assert.ok(a.help.length > 20, a.key);
  assert.ok(M.STEPS.includes(M.STEP_DEFAULT));
});

// -- the skeleton, bent to the moves not yet applied --------------------------------------------

test('pending deltas: what each move not yet applied adds, Z in um; a taken-back move goes home', () => {
  const d = M.pendingDeltas([
    { id: 5, want: { dx: 0.3, dy: 0, dz: 0 }, applied: { dx: 0.1, dy: 0, dz: 0 } },
    { id: 6, want: { dx: 0, dy: 0, dz: 2 }, applied: { dx: 0, dy: 0, dz: 0 } },
    { id: 7, want: null, applied: { dx: 0.5, dy: -0.25, dz: 0 } },
    { id: 8, want: { dx: 0.1, dy: 0, dz: 0 }, applied: { dx: 0.1, dy: 0, dz: 0 } },
  ], 0.07);
  close(d.get(5)[0], 0.2, 1e-12);
  assert.deepEqual(d.get(6), [0, 0, 0.14]);
  assert.deepEqual(d.get(7), [-0.5, 0.25, 0]);
  assert.ok(!d.has(8), 'nothing pending');
  assert.equal(M.pendingDeltas([], 0.07).size, 0);
});

test('the skeleton follows the moved annotations, point by point, and is not changed in place', () => {
  const skeleton = {
    neuron_id: 1,
    segments: [
      { location_ids: [1, 2, 3], points_um: [[0, 0, 0], [1, 0, 0], [2, 0, 0]], radii_um: [1, 1, 1] },
      { location_ids: [3, 4], points_um: [[2, 0, 0], [3, 0, 0]], radii_um: [1, 1] },
      { location_ids: [9, 10], points_um: [[5, 5, 5], [6, 5, 5]], radii_um: [1, 1] },
    ],
  };
  const frozen = JSON.stringify(skeleton);
  const bent = M.nudgedSkeleton(skeleton, new Map([[3, [0, 0.5, 0]], [4, [0, 0, 0.07]]]));
  assert.deepEqual(bent.segments[0].points_um, [[0, 0, 0], [1, 0, 0], [2, 0.5, 0]]);
  // A branch point shared by two segments moves in both.
  assert.deepEqual(bent.segments[1].points_um, [[2, 0.5, 0], [3, 0, 0.07]]);
  assert.equal(bent.segments[2], skeleton.segments[2], 'an untouched segment is the same object');
  assert.equal(JSON.stringify(skeleton), frozen, 'the server\'s skeleton is kept as it was');
  assert.equal(M.nudgedSkeleton(skeleton, new Map()), skeleton);
  assert.equal(M.nudgedSkeleton(skeleton, null), skeleton);
  assert.equal(M.nudgedSkeleton(skeleton, new Map([[99, [1, 1, 1]]])), skeleton, 'no point of it moved');
});

// -- the list ---------------------------------------------------------------------------------

test('the list: newest first, with the section as moved and Viking\'s', () => {
  let store = M.setMove({}, 'curvefit.1', 10, { dx: 0.3, dy: 0.4 }, 100);
  store = M.setMove(store, 'curvefit.1', 11, { dz: 2 }, 200);
  store = M.setMove(store, 'curvefit.1', 12, { dx: 1 }, 50);
  const nodes = [
    { id: 10, z_section: 500 },
    { id: 11, z_section: 503, manual_dz: 2 },
  ];
  const rows = M.moveRows(store, 'curvefit.1', nodes);
  assert.deepEqual(rows.map((r) => r.id), [11, 10, 12]);
  close(rows[1].um, 0.5);
  assert.equal(rows[0].section, 503);
  assert.equal(rows[0].vikingSection, 501);
  assert.equal(rows[2].found, false);
  assert.equal(rows[2].section, null);
  assert.deepEqual(M.moveRows(store, 'off.0', nodes), []);
  assert.equal(M.signedMove(0.25), '+0.25');
  assert.equal(M.signedMove(-0.1), '−0.10');
  assert.equal(M.signedMove(0), '0');
});

// -- the shift layer under a manual move --------------------------------------------------------

test('the shift layer measures from where the correction left an annotation, not the manual move', () => {
  const node = {
    id: 1, z_section: 12, radius_um: 1, type_code: 1, xyz_um: [10.5, 2, 12 * 0.07 + 0.14],
    shift_um: [0.3, 0], live_shift_um: null, manual_shift_um: [0.5, 0, 0.14], manual_dz: 2,
  };
  assert.deepEqual(S.correctedXyz(node).map((v) => +v.toFixed(9)), [10, 2, +(12 * 0.07).toFixed(9)]);
  assert.equal(S.vikingSection(node), 10);
  assert.deepEqual(S.stagePositions(node).corrected, [10, 2]);
  assert.deepEqual(S.pairEnds(node, 'correction').head, [10, 2]);
  const field = S.prepareField([node], 'correction');
  assert.equal(field.n, 1);
  close(field.hx[0], 10); close(field.z[0], 12 * 0.07);
  assert.equal(field.section[0], 10, 'Viking\'s section');
  // A node never moved by hand is as it was.
  const plain = { ...node, manual_shift_um: null, manual_dz: null };
  assert.deepEqual(S.correctedXyz(plain), plain.xyz_um);
  assert.equal(S.vikingSection(plain), 12);
});

// -- a set: several, a section, a run of sections (2026-10-20) ------------------------------------

const cellNodes = [
  { id: 1, z_section: 100 }, { id: 2, z_section: 100 }, { id: 3, z_section: 101 },
  { id: 4, z_section: 102 }, { id: 5, z_section: 103 },
  { id: 6, z_section: 104, manual_dz: 2 },           // Viking's 102, moved up two
];

test('what a step moves: the one, its section, a run of sections, plus the ones added', () => {
  assert.deepEqual(M.membersOf(cellNodes, { target: 3 }), [3]);
  assert.deepEqual(M.membersOf(cellNodes, { target: 3, extra: [5, 99] }), [3, 5], 'an ID the cell lacks is left out');
  assert.deepEqual(M.membersOf(cellNodes, { scope: 'section', target: 1 }), [1, 2]);
  assert.deepEqual(M.membersOf(cellNodes, { scope: 'section', target: 4 }), [4, 6], 'by Viking\'s section');
  assert.deepEqual(M.membersOf(cellNodes, { scope: 'range', target: 1, from: 102, to: 101 }), [1, 3, 4, 6]);
  assert.deepEqual(M.membersOf(cellNodes, { scope: 'range', target: 1 }), [1], 'no range yet: the one');
  assert.deepEqual(M.membersOf([], { target: 1 }), []);
  assert.deepEqual([...M.SCOPES], ['one', 'section', 'range']);
});

test('a set steps together: each member the same step from its own move, or none past the limit', () => {
  let store = M.setMove({}, 'curvefit.1', 2, { dx: 0.5 }, 1);
  const r = M.stepSet(store, 'curvefit.1', [1, 2], 'x', 1, 0.1);
  assert.equal(r.along, 'xy');
  assert.deepEqual(r.over, []);
  assert.equal(r.store['curvefit.1']['1'].dx, 0.1);
  assert.equal(r.store['curvefit.1']['2'].dx, 0.6, 'its earlier move kept, the same step added');
  const z = M.stepSet(r.store, 'curvefit.1', [1, 2], 'z', -1, 0.1);
  assert.deepEqual([z.store['curvefit.1']['1'].dz, z.store['curvefit.1']['2'].dz], [-1, -1]);
  // One member past the limit: nothing moves.
  store = M.setMove({}, 'curvefit.1', 2, { dx: M.MAX_MOVE_UM }, 1);
  const stopped = M.stepSet(store, 'curvefit.1', [1, 2], 'x', 1, 1);
  assert.deepEqual(stopped.over, [2]);
  assert.equal(stopped.store, store, 'unchanged');
  // No shift to follow: nothing.
  assert.equal(M.stepSet({}, 'curvefit.1', [1], 'shift', 1, 0.1).along, null);
  // setMoves: one copy for many, a move of nothing removed.
  const many = M.setMoves({}, 'off.0', [[1, { dx: 1 }], [2, { dy: 1 }], [3, { dx: 0 }]], 5);
  assert.deepEqual(Object.keys(many['off.0']), ['1', '2']);
  assert.equal(many['off.0']['1'].t, 5);
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
