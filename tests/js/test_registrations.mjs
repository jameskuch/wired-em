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
  assert.equal(rg.registrationTag({ live: true, corrections: 'all' }), 'all.1');
  assert.equal(rg.registrationTag({ live: 0, corrections: 'curvefit' }), 'curvefit.0');
  assert.equal(rg.registrationTag({ live: true, corrections: 'none' }), 'off.1', 'an unknown correction is off');
  assert.deepEqual(rg.registrationOfTag('neighbor.1'), { live: true, corrections: 'neighbor' });
  assert.equal(rg.registrationOfTag('all.1.m3f9a'), null, 'a copy with moves is not a plain registration');
  assert.equal(rg.registrationOfTag('none.0'), null, 'the server never names a copy none');
  assert.ok(rg.sameRegistration({ live: true, corrections: 'off' }, { live: 1, corrections: 'off' }));
});

test('in words, as the cell panel says them; a volume without transforms names none', () => {
  const all = { live: true, corrections: 'all' };
  assert.equal(rg.registrationLabel(all), 'Current transforms + Field + fit');
  assert.equal(rg.registrationShort(all), 'current + field + fit');
  assert.equal(rg.registrationLabel({ live: false, corrections: 'off' }), 'Stored positions, no correction');
  assert.equal(rg.registrationShort({ live: false, corrections: 'off' }), 'stored');
  assert.equal(rg.registrationLabel(all, { hasTransforms: false }), 'Stored positions + Field + fit');
  assert.equal(rg.registrationLabel(null), 'As loaded');
  assert.equal(rg.copyLabel('curvefit.1'), 'Current transforms + Curve fit');
  assert.equal(rg.copyLabel('all.1.m3f9a'), 'Current transforms + Field + fit, with manual moves');
  assert.equal(rg.correctionName('neighbor'), 'Residual field');
  assert.equal(rg.correctionName('off'), 'no correction');
});

test('the load of a copy asks for the registration and the synapses, never the person\'s moves', () => {
  const body = rg.copyLoadBody({ id: 1178, source: 'NeitzInferiorMonkey' }, { live: false, corrections: 'curvefit' });
  assert.deepEqual(body, { id: 1178, source: 'NeitzInferiorMonkey', include_synapses: true,
    live_registration: false, section_corrections: 'curvefit' });
});

test('every registration a layer can take: eight with the transforms read, four without, stored first', () => {
  const with8 = rg.registrationChoices().map(rg.registrationTag);
  assert.deepEqual(with8, ['off.0', 'off.1', 'neighbor.0', 'neighbor.1', 'curvefit.0', 'curvefit.1', 'all.0', 'all.1']);
  assert.deepEqual(rg.registrationChoices({ hasTransforms: false }).map(rg.registrationTag),
    ['off.0', 'neighbor.0', 'curvefit.0', 'all.0']);
});

test('a sweep starts with none, each step alone and all of them, and keeps to what a grid or a pane holds', () => {
  const start = rg.defaultSweep({ hasTransforms: true, defaultCorrection: 'curvefit' }).map(rg.registrationTag);
  assert.deepEqual(start, ['off.0', 'off.1', 'neighbor.1', 'curvefit.1', 'all.1', 'curvefit.0']);
  assert.equal(start.length, rg.MAX_SWEEP_PANES, 'it fills the grid');
  assert.deepEqual(rg.defaultSweep({ hasTransforms: false }).map(rg.registrationTag),
    ['off.0', 'neighbor.0', 'curvefit.0', 'all.0']);
  const plan = rg.planSweep(rg.defaultSweep({ defaultCorrection: 'all' }));
  assert.equal(plan.problem, null);
  assert.deepEqual(plan.registrations.map(rg.registrationTag),
    ['off.0', 'off.1', 'neighbor.1', 'curvefit.1', 'all.0', 'all.1'], 'in the order they are offered');
  assert.match(rg.planSweep(rg.registrationChoices()).problem, /holds 6 panes: untick 2/);
  assert.match(rg.planSweep(plan.registrations, { target: 'layers' }).problem, /holds 4 layers: untick 2/);
  assert.match(rg.planSweep([]).problem, /at least one/);
  assert.deepEqual(rg.planSweep([{ live: true, corrections: 'all' }], { hasTransforms: false }).registrations, [],
    'a volume without transforms offers none with them');
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
