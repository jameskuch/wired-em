// What a cell's Registration section says: the help of its two switches and of
// the Correction select, and the small status chip beside each switch (a count,
// or "unavailable") whose hover has the facts and, for a failure, the reason in
// plain words with the server's own message at the end.  Asked for 2026-10-16:
// the facts were standing lines under the switches ("helper text ... remove from
// menu"), and the notes were the service's raw errors.
//
// Pure: `stamp` (how a UTC time is printed) is passed in, so node tests every
// rule (tests/js/test_regstatus.mjs).  The figures named here are helpfigures.js's.

const n = (v) => (Number.isFinite(v) ? Math.round(v).toLocaleString('en-US') : '?');
const um = (v) => (Number.isFinite(v) ? v.toFixed(2) : '?');

/** The correction modes, as the Correction select names them. */
export const MODE_LABELS = Object.freeze({
  curvefit: 'Curve fit', neighbor: 'Residual field', all: 'Field + fit',
});

/** Which drawing goes above each help. */
export const REG_FIGURES = Object.freeze({
  transforms: 'transforms', correction: 'correction', mode: 'correctionModes',
});

/** The switches' and the select's help: what each does, before what it did to this cell. */
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
    + 'process, how far each annotation sits off the smooth path through its neighbors on the sections above '
    + 'and below; a dendrite that zig-zags from section to section is pulled back onto its path. The moves are '
    + 'about a tenth of a micrometer (Inferior c1178 with Curve fit: median 0.11 µm, 95 % under 0.56 µm), far '
    + 'below what a surface shows: Position shift draws them.\n\n'
    + 'On: each annotation moves by the correction chosen below, on top of the current section transforms. '
    + 'Off: where the transforms put it. The service publishes its own measurement for each volume; whether it '
    + 'has one for this cell, in the mode chosen, is the chip beside this switch, and the last line here.',
  mode: 'How the correction is worked out.\n\n'
    + 'Curve fit (the default for the Marc lab\'s rabbit volumes): each traced process of the cell is fitted '
    + 'on its own, leaving each annotation out of its own fit, and the annotation moves onto the fit. Synapses '
    + 'keep their place.\n\n'
    + 'Residual field: the same measurement from every process in the volume, pooled into one smooth field '
    + 'per section on a 2 µm grid, as Viking\'s own exports apply it. Each annotation, outline and synapse '
    + 'moves by the field where it is; nothing moves where the field has no support. It measured worse than '
    + 'the fit near Inferior c21768 (2026-09-23).\n\n'
    + 'Field + fit (the default for the Neitz lab\'s volumes): the field, then the fit; what Viking\'s service '
    + 'does when it is not told which.',
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
  } else if (/NOT_FOUND/.test(text)) {
    plain = `Viking's corrections service answered that it has no ${modeName} for this volume, so the cell is `
      + 'drawn uncorrected. Another Correction may be available.';
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
    mode === 'neighbor'
      ? `${n(sc.n_annotations)} annotations on ${n(sc.n_sections)} sections: ${n(sc.n_found)} on a section with a `
        + `published map, ${n(sc.n_trusted)} where the field has support. Only those move.`
      : `${n(sc.n_annotations)} annotations on ${n(sc.n_sections)} sections: ${n(sc.n_found)} corrected by Viking, `
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
