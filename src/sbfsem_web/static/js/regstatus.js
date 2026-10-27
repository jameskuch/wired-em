// What a cell's Registration section says: the help of its two switches and of
// the correction's names, and the small status chip beside each switch (a count,
// or "unavailable") whose hover has the facts and, for a failure, the reason in
// plain words with the server's own message at the end.  Asked for 2026-10-16:
// the facts were standing lines under the switches ("helper text ... remove from
// menu"), and the notes were the service's raw errors.
//
// Pure: `stamp` (how a UTC time is printed) is passed in, so node tests every
// rule (tests/js/test_regstatus.mjs).  The figures named here are helpfigures.js's.

const n = (v) => (Number.isFinite(v) ? Math.round(v).toLocaleString('en-US') : '?');
const um = (v) => (Number.isFinite(v) ? v.toFixed(2) : '?');

/**
 * The correction modes by name, as a cell's status, the Correction log and the
 * Registration labels say them.  Viking's corrections service runs one, the
 * residual field (`neighbor`); it ran a curve fit too, alone (`curvefit`) or after
 * the field (`all`), until it stopped on 2026-10-27.  Those two are named here only
 * for a cell read from a file saved before then, which says so.
 */
export const MODE_LABELS = Object.freeze({
  neighbor: 'Residual field', curvefit: 'Curve fit (retired)', all: 'Field + fit (retired)',
});

/** The names a correction setting was saved with that are the residual field now. */
const FIELD_NAMES = new Set(['neighbor', 'residual', 'residualfield', 'field', 'on', 'true', 'yes', '1',
  'curvefit', 'all', 'fieldfit', '2', '3']);

/**
 * A saved correction setting as the server reads it now (`corrections.correction_mode`):
 * 'off', or 'neighbor' -- the residual field, which `true`, and the retired Curve fit
 * (`curvefit`) and Field + fit (`all`), are too.  What it does not know is 'off', so a
 * session, Look or store that holds anything never asks the server for a mode it refuses.
 */
export function correctionSetting(value) {
  if (value === true) return 'neighbor';
  if (typeof value !== 'string' && typeof value !== 'number') return 'off';
  const text = String(value).trim().toLowerCase().replace(/[-_ +]/g, '');
  return FIELD_NAMES.has(text) ? 'neighbor' : 'off';
}

/** Which drawing goes above each help. */
export const REG_FIGURES = Object.freeze({
  transforms: 'transforms', correction: 'correction',
});

/** The switches' help: what each does, before what it did to this cell. */
export const REG_HELP = Object.freeze({
  transforms: 'Viking places every annotation by mapping its position on its section through the volume\'s '
    + 'section-to-volume transforms, the stos files its VikingXML lists, and its database stores the result '
    + 'as it was when the annotation was saved. When a section is re-aligned later, Viking draws its '
    + 'annotations through the new transform at once, but the stored positions lag until someone runs Update '
    + 'Volume Positions (or VikingAU).\n\n'
    + 'On: this viewer reads the same VikingXML and stos files itself (not the corrections service) and draws '
    + 'each annotation, outline and synapse where Viking draws it now. Off: the stored positions. Where nothing '
    + 'was re-aligned, the two agree to a few nanometers.',
  correction: 'What Viking\'s corrections service gives: small sideways moves, section by section, that take '
    + 'out the jitter the alignment leaves between sections. Viking\'s server measures, from every traced '
    + 'process in the volume, how far each annotation sits off the smooth path through its neighbors on the '
    + 'sections above and below, and pools what it finds into one smooth field per section, the residual '
    + 'field, as Viking\'s own exports apply it: a dendrite that zig-zags from section to section is pulled '
    + 'back toward its path. The moves are about a tenth of a micrometer, far below what a surface shows: '
    + 'Position shift draws them.\n\n'
    + 'On: each annotation, outline and synapse moves by the field where it is, on top of the current section '
    + 'transforms; nothing moves where the field has no support. Off: where the transforms put it.\n\n'
    + 'Until 2026-10-27 the service also ran a curve fit along each of the cell\'s own processes (Curve fit, '
    + 'and Field + fit), and this viewer used it by default. Viking stopped running it then: on 11 cells the '
    + 'field alone lined linked outlines up as well (mean overlap 0.792, against 0.791 with the fit) and lost '
    + 'overlap on a quarter as many. A cell, Look, session or link saved with Curve fit or Field + fit is drawn '
    + 'with the residual field.\n\n'
    + 'The service publishes its own measurement for each volume; whether it has one for this cell is the '
    + 'chip beside this switch, and the last line here.',
});

/**
 * A server note in plain words, for the hover of an "unavailable" chip: what
 * happened, then the server's own message at the end.  `what` is 'correction'
 * or 'transforms'; `mode` the correction asked for.
 */
export function explainNote(note, { what = 'correction', mode = null } = {}) {
  const text = String(note || '');
  const modeName = MODE_LABELS[mode] || 'correction';
  let plain;
  if (what === 'transforms') {
    plain = /not read here/i.test(text)
      ? 'This volume\'s section transforms are not read here, so its cells are drawn at their stored positions.'
      : 'Viking\'s transform files for this volume could not be read, so the cell is drawn at its stored positions. '
        + 'Reload the cell (↻) to try again.';
  } else if (/not a Viking volume/i.test(text)) {
    plain = 'This volume is not a Viking volume: there is no correction for it.';
  } else if (/No section correction is published/i.test(text)) {
    plain = 'Viking\'s corrections service publishes no correction for this volume, so the cell is drawn uncorrected.';
  } else if (/INVALID_ARGUMENT/.test(text)) {
    // A request the service refused: since 2026-10-27 the modes it takes have changed twice.
    plain = 'Viking\'s corrections service refused the correction this viewer asked for, so the cell is drawn '
      + 'uncorrected. The service may have changed which corrections it runs; reload the page (a newer viewer '
      + 'may be out) or the cell (↻) later.';
  } else if (/NOT_FOUND/.test(text)) {
    plain = `Viking's corrections service answered that it has no ${modeName} for this volume, so the cell is `
      + 'drawn uncorrected.';
  } else if (/DEADLINE_EXCEEDED|timed? ?out/i.test(text)) {
    plain = 'Viking\'s corrections service did not answer in time, so the cell is drawn uncorrected.';
  } else if (/UNAVAILABLE .*'[^']+'/.test(text)) {
    // The service answered, naming the volume whose own server it could not reach.
    plain = 'Viking\'s corrections service could not reach this volume\'s own server, so the cell is drawn '
      + 'uncorrected. Cells of other volumes are still corrected.';
  } else if (/UNAVAILABLE|not answering|offline|could not be reached/i.test(text)) {
    plain = 'Viking\'s corrections service could not be reached, so the cell is drawn uncorrected.';
  } else {
    plain = 'Viking\'s corrections service could not correct this cell, so it is drawn uncorrected.';
  }
  const again = /until (\d{1,2}:\d{2}) UTC/.exec(text);
  if (again) plain += ` It is not asked again for this volume until ${again[1]} UTC; reload the cell (↻) after that.`;
  return text ? `${plain}\n\nThe server said: ${text}` : plain;
}

/**
 * The transforms switch's chip: `{ text, warn, help }`, `text` '' when there is
 * nothing to say (the switch is off).  `lr` is the cell's `live_registration`.
 */
export function transformsChip(lr, { stamp = (t) => String(t) } = {}) {
  if (!lr?.requested) return { text: '', warn: false, help: '' };
  if (!lr.applied) {
    return { text: 'unavailable', warn: true, help: explainNote(lr.note, { what: 'transforms' }) };
  }
  const file = (lr.vikingxml || '').split('/').pop() || 'VikingXML';
  const lines = [`${file} · ${lr.stos_group || 'stos'}${lr.xml_last_modified ? ` · ${stamp(lr.xml_last_modified)}` : ''}`];
  if (lr.n_moved) {
    lines.push(`${n(lr.n_moved)} of ${n(lr.n_annotations)} annotations are drawn away from where Viking stores them `
      + '(more than 10 nm)' + (lr.sections_moved ? `, on sections ${lr.sections_moved}` : '') + '.');
    lines.push(`How far: median ${um(lr.median_um)} · 95% ${um(lr.p95_um)} · max ${um(lr.max_um)} µm, in X and Y.`);
  } else {
    lines.push(`All ${n(lr.n_annotations)} annotations are where Viking stores them: no section this cell is on `
      + 'was re-aligned since they were saved.');
  }
  if (Number.isFinite(lr.n_sections)) {
    lines.push(`${n(lr.n_annotations)} annotations on ${n(lr.n_sections)} sections, `
      + `${n(lr.n_mapped)} inside a transform.`);
  }
  if (lr.vikingxml) lines.push(`Read from ${lr.vikingxml}`);
  if (lr.note) lines.push(lr.note);
  return {
    text: lr.n_moved ? `${n(lr.n_moved)} moved` : 'none moved',
    warn: false,
    help: lines.join('\n'),
  };
}

/**
 * The section correction switch's chip, as `transformsChip`.  `sc` is the cell's
 * `section_correction`.
 */
export function correctionChip(sc, { stamp = (t) => String(t) } = {}) {
  if (!sc?.requested) return { text: '', warn: false, help: '' };
  const mode = sc.mode || 'neighbor';
  if (!sc.applied) {
    return { text: 'unavailable', warn: true, help: explainNote(sc.note, { what: 'correction', mode }) };
  }
  const lines = [
    `${MODE_LABELS[mode] || mode} · ${sc.volume_name || ''} · ${sc.stos_group || ''}`
      + `${sc.built_utc ? ` · built ${stamp(sc.built_utc)}` : ''}`,
    `${n(sc.n_moved)} of ${n(sc.n_annotations)} annotations moved: median ${um(sc.median_um)} · `
      + `95% ${um(sc.p95_um)} · max ${um(sc.max_um)} µm, in X and Y.`,
    `${n(sc.n_annotations)} annotations on ${n(sc.n_sections)} sections: ${n(sc.n_found)} corrected by Viking, `
      + `${n(sc.n_trusted)} of them applied. One that Viking places more than 20 µm from where it is drawn here `
      + 'keeps its place: its correction was worked out somewhere else.',
    `Published by ${sc.service || 'the corrections service'}`
      + (sc.annotation_watermark ? `, from the annotations as of ${stamp(sc.annotation_watermark)}` : '') + '.',
  ];
  if (sc.note) lines.push(sc.note);
  return { text: `${n(sc.n_moved)} moved`, warn: !!sc.note, help: lines.join('\n') };
}

/**
 * The last line of the correction switch's help: what the service did for this
 * cell, read from its own status, so it stays true when the service changes.
 */
export function correctionForCell(sc) {
  if (!sc?.requested) return 'This cell: off.';
  const mode = MODE_LABELS[sc.mode || 'neighbor'] || sc.mode;
  if (sc.applied) {
    return `This cell: ${mode} applied by the service; ${n(sc.n_moved)} of ${n(sc.n_annotations)} annotations `
      + `moved, by a median ${um(sc.median_um)} µm.`;
  }
  const first = explainNote(sc.note, { what: 'correction', mode: sc.mode }).split('\n')[0];
  return `This cell: not corrected. ${first}`;
}
