// Registration in Render compare: which copy of the cell a layer is built from.
//
// A copy is the cell loaded with Viking's current section transforms on or off,
// and Viking's section correction off or in one of its modes.  The server holds
// one copy per setting side by side (all eight of them, if asked) and names each
// by its tag, `<correction>.<0|1>` (facade.copy_tag: "all.1" is the current
// transforms with Field + fit, "off.0" the stored positions), and `.k` after it
// when the outlines the correction log lists keep where Viking draws them (a
// curve fit only: "all.1.k", the cell panel's Keep listed outlines).  A layer's
// `registration` is null -- the cell as the page loaded it, the tab's own copy,
// with the person's own moves if it has any -- or `{ live, corrections }`, the
// plain copy of that setting (with `keepOutlines: true` for a tab's copy that
// keeps them).  Asked for 2026-10-20: the same cell with the same render options
// under several registrations, side by side.
//
// Pure: no page, no server; loads under node (tests/js/test_registrations.mjs).

import { MODE_LABELS } from './regstatus.js';

/** Viking's correction, off or one of its modes, in the order the choices list them. */
export const CORRECTIONS = Object.freeze(['off', 'neighbor', 'curvefit', 'all']);

/** The modes with Viking's curve fit: the only ones under which listed outlines can keep their place. */
const FIT = Object.freeze(['curvefit', 'all']);

/** As the cell panel's Registration header says them ("current + field + fit"). */
const SHORT = Object.freeze({ neighbor: 'field', curvefit: 'fit', all: 'field + fit' });

/** A correction's name as the Correction select says it ("Field + fit"), or "no correction". */
export function correctionName(corrections) {
  return MODE_LABELS[corrections] ?? 'no correction';
}

/**
 * A registration as stored: null (the cell as loaded) or `{ live, corrections }`, with
 * `keepOutlines: true` when the listed outlines keep their place (a curve fit only).
 */
export function normalizeRegistration(reg) {
  if (!reg || typeof reg !== 'object') return null;
  const corrections = CORRECTIONS.includes(reg.corrections) ? reg.corrections : 'off';
  const out = { live: !!reg.live, corrections };
  if (reg.keepOutlines === true && FIT.includes(corrections)) out.keepOutlines = true;
  return out;
}

/** The server's name for the copy (`facade.copy_tag`), or null for the cell as loaded. */
export function registrationTag(reg) {
  const r = normalizeRegistration(reg);
  return r ? `${r.corrections}.${r.live ? 1 : 0}${r.keepOutlines ? '.k' : ''}` : null;
}

/** The registration a tag names (`"all.1"`, `"all.1.k"`), or null for one that names no such copy. */
export function registrationOfTag(tag) {
  const m = /^(off|neighbor|curvefit|all)\.([01])(\.k)?$/.exec(String(tag ?? ''));
  if (!m || (m[3] && !FIT.includes(m[1]))) return null;
  return normalizeRegistration({ live: m[2] === '1', corrections: m[1], keepOutlines: !!m[3] });
}

/** The part of a copy's tag that names its registration (`"all.1.k"` of `"all.1.k.m3f9a"`), or null. */
export function registrationPart(tag) {
  const m = /^((?:off|neighbor|curvefit|all)\.[01](?:\.k)?)(?:\.m.+)?$/.exec(String(tag ?? ''));
  return m && registrationOfTag(m[1]) ? m[1] : null;
}

/**
 * A copy's tag in words, as the report names it: a plain copy by its registration, the
 * tab's copy with a person's own moves (`all.1.m<token>`) with that said, an unknown tag as it is.
 */
export function copyLabel(tag, { hasTransforms = true } = {}) {
  const part = registrationPart(tag);
  if (!part) return tag ? String(tag) : 'As loaded';
  const moved = String(tag).length > part.length;
  return registrationLabel(registrationOfTag(part), { hasTransforms }) + (moved ? ', with manual moves' : '');
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
    ...(FIT.includes(r.corrections) ? { keep_outlines: !!r.keepOutlines } : {}),
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
  if (r.corrections === 'off') return `${where}, no correction`;
  return `${where} + ${correctionName(r.corrections)}${r.keepOutlines ? ', listed outlines kept' : ''}`;
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
  const text = r.corrections === 'off' ? where
    : `${where} + ${SHORT[r.corrections]}${r.keepOutlines ? ' + kept' : ''}`;
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
