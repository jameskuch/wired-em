// Registration in Render compare: which copy of the cell a layer is built from.
//
// A copy is the cell loaded with Viking's current section transforms on or off,
// and Viking's section correction, its residual field, off or on.  The server holds
// one copy per setting side by side (all four of them, if asked) and names each
// by its tag, `<correction>.<0|1>` (facade.copy_tag: "neighbor.1" is the current
// transforms with the residual field, "off.0" the stored positions).  Viking's
// service ran a curve fit too until 2026-10-27, "curvefit" and "all" (Field + fit);
// a layer, tag or report saved with either is read as the residual field
// (`correctionSetting`), as the server reads it.  A layer's
// `registration` is null -- the cell as the page loaded it, the tab's own copy,
// with the person's own moves if it has any -- or `{ live, corrections }`, the
// plain copy of that setting.  Asked for 2026-10-20: the same cell with the same
// render options under several registrations, side by side.
//
// Pure: no page, no server; loads under node (tests/js/test_registrations.mjs).

import { MODE_LABELS, correctionSetting } from './regstatus.js';

/** Viking's correction, off or its residual field, in the order the choices list them. */
export const CORRECTIONS = Object.freeze(['off', 'neighbor']);

/** As the cell panel's Registration header says them ("current + field"). */
const SHORT = Object.freeze({ neighbor: 'field' });

/** A correction's name as the Registration section says it ("Residual field"), or "no correction". */
export function correctionName(corrections) {
  const setting = correctionSetting(corrections);
  return setting === 'off' ? 'no correction' : MODE_LABELS[setting];
}

/**
 * A registration as stored: null (the cell as loaded) or `{ live, corrections }`, a
 * retired correction (Curve fit, Field + fit) read as the residual field.
 */
export function normalizeRegistration(reg) {
  if (!reg || typeof reg !== 'object') return null;
  return { live: !!reg.live, corrections: correctionSetting(reg.corrections) };
}

/** The server's name for the copy (`facade.copy_tag`), or null for the cell as loaded. */
export function registrationTag(reg) {
  const r = normalizeRegistration(reg);
  return r ? `${r.corrections}.${r.live ? 1 : 0}` : null;
}

/**
 * The registration a tag names (`"neighbor.1"`; a retired mode's, `"all.1"`, names the
 * same), or null for one that names no plain copy.
 */
export function registrationOfTag(tag) {
  const m = /^(off|neighbor|curvefit|all)\.([01])$/.exec(String(tag ?? ''));
  return m ? { live: m[2] === '1', corrections: correctionSetting(m[1]) } : null;
}

/**
 * A copy's tag in words, as the report names it: a plain copy by its registration, the
 * tab's copy with a person's own moves (`neighbor.1.m<token>`) with that said, an unknown tag as it is.
 */
export function copyLabel(tag, { hasTransforms = true } = {}) {
  const m = /^((?:off|neighbor|curvefit|all)\.[01])(\.m.+)?$/.exec(String(tag ?? ''));
  if (!m) return tag ? String(tag) : 'As loaded';
  return registrationLabel(registrationOfTag(m[1]), { hasTransforms }) + (m[2] ? ', with manual moves' : '');
}

export function sameRegistration(a, b) {
  return registrationTag(a) === registrationTag(b);
}

/** The body of the load that makes the copy (`POST /api/neurons`), without the person's moves. */
export function copyLoadBody(record, reg) {
  const r = normalizeRegistration(reg);
  return {
    id: record.id, source: record.source, include_synapses: true,
    live_registration: r.live, section_corrections: r.corrections,
  };
}

/**
 * In words: "Current transforms + Residual field", "Stored positions, no
 * correction".  `hasTransforms` false (a volume whose transforms are not read
 * here): the transforms are not named, since on and off draw the same.
 */
export function registrationLabel(reg, { hasTransforms = true } = {}) {
  const r = normalizeRegistration(reg);
  if (!r) return 'As loaded';
  const where = hasTransforms && r.live ? 'Current transforms' : 'Stored positions';
  return r.corrections === 'off' ? `${where}, no correction` : `${where} + ${correctionName(r.corrections)}`;
}

/**
 * Short: "current + field", "stored".  `compact` joins without spaces
 * ("current+field"), for a chip.
 */
export function registrationShort(reg, { hasTransforms = true, compact = false } = {}) {
  const r = normalizeRegistration(reg);
  if (!r) return '';
  const where = hasTransforms && r.live ? 'current' : 'stored';
  const text = r.corrections === 'off' ? where : `${where} + ${SHORT[r.corrections]}`;
  return compact ? text.replaceAll(' + ', '+') : text;
}

/**
 * Every registration a layer can be put on, in the order they are offered: the
 * stored positions first, then the correction; with the transforms read, each
 * of those with and without them (4), else without (2).
 */
export function registrationChoices({ hasTransforms = true } = {}) {
  const lives = hasTransforms ? [false, true] : [false];
  const out = [];
  for (const corrections of CORRECTIONS) {
    for (const live of lives) out.push({ live, corrections });
  }
  return out;
}

/** The most panes a grid has, and the most layers a pane holds (compare.js MAX_LAYERS). */
export const MAX_SWEEP_PANES = 6;
export const MAX_SWEEP_LAYERS = 4;

/**
 * What a sweep starts with ticked: every registration -- the stored positions, the
 * current transforms, the transforms with the correction, and the correction on the
 * stored positions (4 with the transforms; 2 without).  `defaultCorrection`, the
 * volume's own, is the residual field whatever it names (a server from before
 * 2026-10-27 says Curve fit or Field + fit), so it no longer changes the sweep.
 */
// eslint-disable-next-line no-unused-vars
export function defaultSweep({ hasTransforms = true, defaultCorrection = 'neighbor' } = {}) {
  if (!hasTransforms) return CORRECTIONS.map((corrections) => ({ live: false, corrections }));
  return [
    { live: false, corrections: 'off' },
    { live: true, corrections: 'off' },
    { live: true, corrections: 'neighbor' },
    { live: false, corrections: 'neighbor' },
  ];
}

/**
 * The sweep the dialog would set up: the ticked registrations in the order they
 * are offered, and why it cannot be set up as asked (too many for the target, or
 * none).  `target` is 'panes' (one pane each, the grid's count set to match) or
 * 'layers' (layers of one pane).
 */
export function planSweep(ticked, { target = 'panes', hasTransforms = true } = {}) {
  const tags = new Set(ticked.map(registrationTag));
  const regs = registrationChoices({ hasTransforms }).filter((r) => tags.has(registrationTag(r)));
  const most = target === 'layers' ? MAX_SWEEP_LAYERS : MAX_SWEEP_PANES;
  let problem = null;
  if (!regs.length) problem = 'Tick at least one registration.';
  else if (regs.length > most) {
    problem = target === 'layers'
      ? `A pane holds ${MAX_SWEEP_LAYERS} layers: untick ${regs.length - most}, or put one in each pane.`
      : `The grid holds ${MAX_SWEEP_PANES} panes: untick ${regs.length - most}.`;
  }
  return { registrations: regs, problem };
}
