// Registration in Render compare: which copy of the cell a layer is built from.
//
// A copy is the cell loaded with Viking's current section transforms on or off,
// and Viking's section correction off or in one of its modes.  The server holds
// one copy per setting side by side (all eight of them, if asked) and names each
// by its tag, `<correction>.<0|1>` (facade.copy_tag: "all.1" is the current
// transforms with Field + fit, "off.0" the stored positions).  A layer's
// `registration` is null -- the cell as the page loaded it, the tab's own copy,
// with the person's own moves if it has any -- or `{ live, corrections }`, the
// plain copy of that setting.  Asked for 2026-10-20: the same cell with the same
// render options under several registrations, side by side.
//
// Pure: no page, no server; loads under node (tests/js/test_registrations.mjs).

import { MODE_LABELS } from './regstatus.js';

/** Viking's correction, off or one of its modes, in the order the choices list them. */
export const CORRECTIONS = Object.freeze(['off', 'neighbor', 'curvefit', 'all']);

/** As the cell panel's Registration header says them ("current + field + fit"). */
const SHORT = Object.freeze({ neighbor: 'field', curvefit: 'fit', all: 'field + fit' });

/** A correction's name as the Correction select says it ("Field + fit"), or "no correction". */
export function correctionName(corrections) {
  return MODE_LABELS[corrections] ?? 'no correction';
}

/** A registration as stored: null (the cell as loaded) or `{ live, corrections }`. */
export function normalizeRegistration(reg) {
  if (!reg || typeof reg !== 'object') return null;
  const corrections = CORRECTIONS.includes(reg.corrections) ? reg.corrections : 'off';
  return { live: !!reg.live, corrections };
}

/** The server's name for the copy (`facade.copy_tag`), or null for the cell as loaded. */
export function registrationTag(reg) {
  const r = normalizeRegistration(reg);
  return r ? `${r.corrections}.${r.live ? 1 : 0}` : null;
}

/** The registration a tag names (`"all.1"`), or null for one that names no plain copy. */
export function registrationOfTag(tag) {
  const m = /^(off|neighbor|curvefit|all)\.([01])$/.exec(String(tag ?? ''));
  return m ? { live: m[2] === '1', corrections: m[1] } : null;
}

/**
 * A copy's tag in words, as the report names it: a plain copy by its registration, the
 * tab's copy with a person's own moves (`all.1.m<token>`) with that said, an unknown tag as it is.
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
 * In words: "Current transforms + Field + fit", "Stored positions, no
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
 * Short: "current + field + fit", "stored".  `compact` joins without spaces
 * ("current+field+fit"), for a chip: cut short anywhere after "current+field", it
 * still shows whether more follows, so the eight stay told apart.
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
 * stored positions first, then each correction; with the transforms read, each
 * of those with and without them (8), else without (4).
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
 * What a sweep starts with ticked: no registration at all, each step alone, and
 * all of them -- the stored positions, the current transforms, the transforms
 * with each correction, and the volume's own default correction on the stored
 * positions (6 with the transforms; 4 without).
 */
export function defaultSweep({ hasTransforms = true, defaultCorrection = 'all' } = {}) {
  if (!hasTransforms) return CORRECTIONS.map((corrections) => ({ live: false, corrections }));
  const fallback = CORRECTIONS.includes(defaultCorrection) && defaultCorrection !== 'off'
    ? defaultCorrection : 'all';
  return [
    { live: false, corrections: 'off' },
    { live: true, corrections: 'off' },
    { live: true, corrections: 'neighbor' },
    { live: true, corrections: 'curvefit' },
    { live: true, corrections: 'all' },
    { live: false, corrections: fallback },
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
