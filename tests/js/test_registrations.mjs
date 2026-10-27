// Registration in Render compare, without a page (static/js/registrations.js): the
// server's names for the copies of a cell, the words for them, what a layer can be
// put on, and what the Registrations… dialog sets up.  Run with
//
//     node tests/js/test_registrations.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free, no DOM.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const rg = await import(pathToFileURL(path.join(jsDir, 'registrations.js')).href);

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('a registration is the server\'s copy tag (facade.copy_tag), and a tag reads back', () => {
  assert.equal(rg.registrationTag(null), null, 'the cell as loaded names no copy');
  assert.equal(rg.registrationTag({ live: true, corrections: 'neighbor' }), 'neighbor.1');
  assert.equal(rg.registrationTag({ live: true, corrections: 'none' }), 'off.1', 'an unknown correction is off');
  assert.deepEqual(rg.registrationOfTag('neighbor.1'), { live: true, corrections: 'neighbor' });
  assert.equal(rg.registrationOfTag('neighbor.1.m3f9a'), null, 'a copy with moves is not a plain registration');
  assert.equal(rg.registrationOfTag('none.0'), null, 'the server never names a copy none');
  assert.ok(rg.sameRegistration({ live: true, corrections: 'off' }, { live: 1, corrections: 'off' }));
});

test('a registration saved with Curve fit or Field + fit, which Viking stopped running, is the residual field', () => {
  assert.equal(rg.registrationTag({ live: true, corrections: 'all' }), 'neighbor.1');
  assert.equal(rg.registrationTag({ live: 0, corrections: 'curvefit' }), 'neighbor.0');
  assert.equal(rg.registrationTag({ live: true, corrections: true }), 'neighbor.1', 'a choice from before the modes');
  assert.deepEqual(rg.registrationOfTag('all.1'), { live: true, corrections: 'neighbor' });
  assert.deepEqual(rg.registrationOfTag('curvefit.0'), { live: false, corrections: 'neighbor' });
  assert.ok(rg.sameRegistration({ live: true, corrections: 'all' }, { live: true, corrections: 'neighbor' }));
  assert.equal(rg.copyLabel('curvefit.1'), 'Current transforms + Residual field');
  assert.equal(rg.correctionName('all'), 'Residual field');
});

test('in words, as the cell panel says them; a volume without transforms names none', () => {
  const field = { live: true, corrections: 'neighbor' };
  assert.equal(rg.registrationLabel(field), 'Current transforms + Residual field');
  assert.equal(rg.registrationShort(field), 'current + field');
  assert.equal(rg.registrationShort(field, { compact: true }), 'current+field');
  assert.equal(rg.registrationLabel({ live: false, corrections: 'off' }), 'Stored positions, no correction');
  assert.equal(rg.registrationShort({ live: false, corrections: 'off' }), 'stored');
  assert.equal(rg.registrationLabel(field, { hasTransforms: false }), 'Stored positions + Residual field');
  assert.equal(rg.registrationLabel(null), 'As loaded');
  assert.equal(rg.copyLabel('neighbor.1.m3f9a'), 'Current transforms + Residual field, with manual moves');
  assert.equal(rg.correctionName('neighbor'), 'Residual field');
  assert.equal(rg.correctionName('off'), 'no correction');
});

test('the load of a copy asks for the registration and the synapses, never the person\'s moves', () => {
  const body = rg.copyLoadBody({ id: 1178, source: 'NeitzInferiorMonkey' }, { live: false, corrections: 'neighbor' });
  assert.deepEqual(body, { id: 1178, source: 'NeitzInferiorMonkey', include_synapses: true,
    live_registration: false, section_corrections: 'neighbor' });
  const old = rg.copyLoadBody({ id: 1178, source: 'NeitzInferiorMonkey' }, { live: true, corrections: 'curvefit' });
  assert.equal(old.section_corrections, 'neighbor', 'never a mode the service no longer runs');
});

test('every registration a layer can take: four with the transforms read, two without, stored first', () => {
  assert.deepEqual(rg.CORRECTIONS, ['off', 'neighbor']);
  assert.deepEqual(rg.registrationChoices().map(rg.registrationTag), ['off.0', 'off.1', 'neighbor.0', 'neighbor.1']);
  assert.deepEqual(rg.registrationChoices({ hasTransforms: false }).map(rg.registrationTag), ['off.0', 'neighbor.0']);
});

test('a sweep starts with every registration, and keeps to what a grid or a pane holds', () => {
  for (const defaultCorrection of ['neighbor', 'curvefit', 'all', undefined]) {
    assert.deepEqual(rg.defaultSweep({ hasTransforms: true, defaultCorrection }).map(rg.registrationTag),
      ['off.0', 'off.1', 'neighbor.1', 'neighbor.0'], String(defaultCorrection));
  }
  assert.deepEqual(rg.defaultSweep({ hasTransforms: false }).map(rg.registrationTag), ['off.0', 'neighbor.0']);
  const plan = rg.planSweep(rg.defaultSweep());
  assert.equal(plan.problem, null);
  assert.deepEqual(plan.registrations.map(rg.registrationTag),
    ['off.0', 'off.1', 'neighbor.0', 'neighbor.1'], 'in the order they are offered');
  assert.equal(rg.planSweep(rg.registrationChoices()).problem, null, 'all four fit the grid');
  assert.equal(rg.planSweep(plan.registrations, { target: 'layers' }).problem, null, 'and a pane');
  assert.match(rg.planSweep([]).problem, /at least one/);
  assert.deepEqual(rg.planSweep([{ live: true, corrections: 'neighbor' }], { hasTransforms: false }).registrations, [],
    'a volume without transforms offers none with them');
  assert.deepEqual(rg.planSweep([{ live: true, corrections: 'all' }, { live: true, corrections: 'neighbor' }])
    .registrations.map(rg.registrationTag), ['neighbor.1'], 'a retired one and the field are one registration');
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
