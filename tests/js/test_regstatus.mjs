// What a cell's Registration section says (static/js/regstatus.js): the chip
// beside each switch, its facts, and a failure of the corrections service in
// plain words with the server's own message at the end.  Run with
//
//     node tests/js/test_regstatus.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const R = await load('regstatus.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);
const stamp = (t) => `<${t}>`;

test('the Keep listed outlines chip counts the outlines kept, and says nothing when off', () => {
  const sc = { requested: true, applied: true, mode: 'all', keep_outlines: true, n_kept: 2, kept_outlines: [1337465, 39957] };
  const chip = R.keepChip(sc);
  assert.equal(chip.text, '2 kept');
  assert.match(chip.help, /^2 traced outlines the Correction log lists are drawn where Viking draws them, without the correction: 1337465, 39957\./);
  assert.equal(R.keepChip({ ...sc, n_kept: 1, kept_outlines: [5] }).text, '1 kept');
  assert.match(R.keepChip({ ...sc, n_kept: 1, kept_outlines: [5] }).help, /^1 traced outline .* is drawn where Viking draws it/);
  const many = Array.from({ length: 15 }, (_, i) => i + 1);
  assert.match(R.keepChip({ ...sc, n_kept: 15, kept_outlines: many }).help, /: 1, 2, .*, 12, …\./);
  assert.equal(R.keepChip({ ...sc, n_kept: 0, kept_outlines: [] }).text, 'none kept');
  for (const off of [null, { ...sc, keep_outlines: false }, { ...sc, applied: false }, { requested: false }]) {
    assert.equal(R.keepChip(off).text, '', JSON.stringify(off));
  }
  assert.equal(R.keepForCell(sc), 'This cell: 2 traced outlines left where Viking draws them.');
  assert.equal(R.keepForCell({ ...sc, n_kept: 0 }), 'This cell: the Correction log lists none of its outlines.');
  assert.equal(R.keepForCell({ requested: true, applied: true, mode: 'all' }), 'This cell: off.');
  assert.match(R.keepForCell(sc, { fit: false }), /has no curve fit/);
  assert.match(R.REG_HELP.keep, /Only with Curve fit or Field \+ fit\.$/);
});

// What the server answered on 2026-10-16 (facade._correct, CorrectionsClient.call).
const NOT_FOUND = "Not applied: CorrectStructures at https://corrections.codepharm.net:443: NOT_FOUND no VikingXML endpoint for 'InferiorMonkey'";
const TIMEOUT = 'Not applied: CorrectStructures at https://corrections.codepharm.net:443: DEADLINE_EXCEEDED Deadline Exceeded';
const DOWN = 'Not applied: ListCorrectionSets at https://corrections.codepharm.net:443: UNAVAILABLE failed to connect';
const HELD = `${NOT_FOUND} (not asked again for InferiorMonkey until 14:05 UTC)`;
// What the service said for Cped at 17:31 UTC on 2026-10-20, held as the provider holds it.
const VOLUME_DOWN = "Not applied: CorrectStructures at https://corrections.codepharm.net:443: UNAVAILABLE volume mapping temporarily unavailable for 'cped': Error connecting to volume server after 4 attempts: http://v2223.host.s.uw.edu/Neitz/CPED/cped.xml Connection refused (not asked again for cped until 17:33 UTC)";

test('a failure is said in plain words first, and the server\'s own message comes last', () => {
  const said = R.explainNote(NOT_FOUND, { mode: 'curvefit' });
  assert.match(said, /^Viking's corrections service answered that it has no Curve fit for this volume/);
  assert.ok(said.endsWith(`The server said: ${NOT_FOUND}`));
  assert.match(R.explainNote(TIMEOUT), /did not answer in time/);
  assert.match(R.explainNote(DOWN), /could not be reached/);
  const volume = R.explainNote(VOLUME_DOWN, { mode: 'all' });
  assert.match(volume, /^Viking's corrections service could not reach this volume's own server/);
  assert.match(volume, /Cells of other volumes are still corrected\. It is not asked again for this volume until 17:33 UTC/);
  assert.match(R.explainNote('Not applied: something new'), /could not correct this cell/);
  assert.match(R.explainNote('No section correction is published for RC9'), /publishes no correction for this volume/);
  assert.match(R.explainNote('Demo is not a Viking volume'), /not a Viking volume/);
  assert.match(R.explainNote(HELD, { mode: 'curvefit' }), /not asked again for this volume until 14:05 UTC/);
  assert.equal(R.explainNote('', { mode: 'all' }).includes('The server said'), false, 'nothing said, nothing quoted');
});

test('the transforms say why too', () => {
  assert.match(R.explainNote("RC1's section transforms are not read here; stored positions", { what: 'transforms' }),
    /^This volume's section transforms are not read here/);
  const failed = R.explainNote('Stored positions: http://x/y.xml answered HTTP 404', { what: 'transforms' });
  assert.match(failed, /could not be read/);
  assert.ok(failed.endsWith('The server said: Stored positions: http://x/y.xml answered HTTP 404'));
});

const lr = {
  requested: true, applied: true, vikingxml: 'http://v2486.host.s.uw.edu/Neitz/InferiorMonkey/InferiorMonkey.xml',
  stos_group: 'SliceToVolume1', xml_last_modified: '2026-09-22T21:50:44Z', n_moved: 56, n_annotations: 2819,
  n_sections: 627, n_mapped: 2800, sections_moved: '937-940', median_um: 0.02, p95_um: 0.04, max_um: 0.21,
};

test('the transforms chip: a count, its facts in the hover; nothing when the switch is off', () => {
  assert.deepEqual(R.transformsChip(null), { text: '', warn: false, help: '' });
  assert.deepEqual(R.transformsChip({ requested: false }), { text: '', warn: false, help: '' });
  const chip = R.transformsChip(lr, { stamp });
  assert.equal(chip.text, '56 moved');
  assert.equal(chip.warn, false);
  for (const fact of ['InferiorMonkey.xml · SliceToVolume1 · <2026-09-22T21:50:44Z>', '56 of 2,819',
    'on sections 937-940', 'median 0.02 · 95% 0.04 · max 0.21 µm', '627 sections', '2,800 inside a transform']) {
    assert.ok(chip.help.includes(fact), fact);
  }
  const none = R.transformsChip({ ...lr, n_moved: 0 }, { stamp });
  assert.equal(none.text, 'none moved');
  assert.match(none.help, /All 2,819 annotations are where Viking stores them/);
  const off = R.transformsChip({ requested: true, note: 'Stored positions: boom' });
  assert.equal(off.text, 'unavailable');
  assert.equal(off.warn, true);
  assert.match(off.help, /could not be read/);
});

const sc = {
  requested: true, applied: true, mode: 'curvefit', volume_name: 'InferiorMonkey', stos_group: 'SliceToVolume1',
  built_utc: '2026-10-01T00:00:00Z', n_moved: 2183, n_annotations: 2434, n_sections: 627, n_found: 2300,
  n_trusted: 2290, median_um: 0.109, p95_um: 0.555, max_um: 3.989, service: 'corrections.codepharm.net',
};

test('the correction chip: a count and its facts; "unavailable" with the reason; a warning when cached', () => {
  assert.equal(R.correctionChip(null).text, '');
  assert.equal(R.correctionChip({ requested: false, mode: 'off' }).text, '');
  const chip = R.correctionChip(sc, { stamp });
  assert.equal(chip.text, '2,183 moved');
  assert.equal(chip.warn, false);
  for (const fact of ['Curve fit · InferiorMonkey · SliceToVolume1 · built <2026-10-01T00:00:00Z>',
    '2,183 of 2,434 annotations moved', 'median 0.11 · 95% 0.56 · max 3.99 µm', '2,300 corrected by Viking',
    'Published by corrections.codepharm.net']) {
    assert.ok(chip.help.includes(fact), fact);
  }
  assert.match(R.correctionChip({ ...sc, mode: 'neighbor' }).help, /with a published map/);
  const cached = R.correctionChip({ ...sc, note: 'The service is not answering; offsets kept from before (x)' });
  assert.equal(cached.warn, true);
  assert.match(cached.help, /offsets kept from before/);
  const failed = R.correctionChip({ requested: true, mode: 'curvefit', note: NOT_FOUND });
  assert.deepEqual([failed.text, failed.warn], ['unavailable', true]);
  assert.match(failed.help, /has no Curve fit for this volume/);
});

test('the switch\'s last line is what the service did for this cell, from its own status', () => {
  assert.equal(R.correctionForCell(null), 'This cell: off.');
  assert.match(R.correctionForCell(sc), /^This cell: Curve fit applied by the service; 2,183 of 2,434 annotations moved, by a median 0\.11 µm\.$/);
  const failed = R.correctionForCell({ requested: true, mode: 'curvefit', note: NOT_FOUND });
  assert.match(failed, /^This cell: not corrected\. Viking's corrections service answered that it has no Curve fit/);
  assert.ok(!failed.includes('The server said'), 'the raw message is the chip\'s');
});

test('the help says what each switch does and names no volume as having or lacking a correction', () => {
  assert.match(R.REG_HELP.correction, /What Viking's corrections service gives/);
  assert.match(R.REG_HELP.transforms, /not the corrections service/);
  for (const key of ['transforms', 'correction', 'mode']) assert.ok(R.REG_HELP[key].length > 200, key);
  // Which volumes have a correction changes with the service: the cell's own status says it.
  for (const name of ['RC1', 'RC2', 'RPC1', 'RPC2', 'Temporal', 'Nasal', 'cped']) {
    assert.ok(!R.REG_HELP.correction.includes(name), name);
  }
  assert.deepEqual(Object.keys(R.MODE_LABELS), ['curvefit', 'neighbor', 'all']);
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
