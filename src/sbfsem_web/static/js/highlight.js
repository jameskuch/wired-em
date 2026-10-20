// How a selection ring looks as it is chosen and as it settles (render3d.js draws it).
// Plain functions, so node can run them (tests/js/test_highlight.mjs).
//
// A selected ring is a solid tube along the annotation inside a wider, additive glow.
// It was drawn at one strength, and that strength was a lot: three times the tube's
// radius at 30 % opacity, which dozens of rings side by side only added up (the report
// of 2026-10-16: "overly large / emphasized").  It is now drawn at that strength when
// it is chosen, which is what says "this is the one", and settles within a second and
// a half to a calmer one: the tube four fifths as thick, the glow two thirds as wide
// and as bright.  A ring that is only redrawn (the zoom changed, a menu row was
// previewed) is drawn settled at once, and so is every ring for someone who asks
// their system for less motion.

/** How long a chosen ring is held at full strength, and how long it then takes to settle. */
export const HOLD_MS = 350;
export const SETTLE_MS = 900;

/** The tube's radius, the glow's radius (both in units of the tube's base radius) and the glow's opacity. */
const CHOSEN = Object.freeze({ core: 1, glow: 3, alpha: 0.3 });
const SETTLED = Object.freeze({ core: 0.8, glow: 2, alpha: 0.2 });
/** Dozens of rings at a time (the skeleton check's) each get a fainter glow: 0.12 where a ring has 0.3. */
const NODES_ALPHA = 0.4;

/** 0 while a ring is held at full strength, then an ease-out up to 1 (settled). */
export function settleProgress(elapsedMs, hold = HOLD_MS, settle = SETTLE_MS) {
  if (!(elapsedMs > hold)) return 0;
  const t = Math.min(1, (elapsedMs - hold) / settle);
  return 1 - (1 - t) ** 3;
}

/**
 * The look at `progress` (0 chosen .. 1 settled): `core` is the tube's radius and `glow` the
 * glow's, both as multiples of the base radius the caller works out from the zoom and the
 * ring's stroke; `alpha` is the glow's opacity.
 */
export function highlightLook(progress, kind = 'annotation') {
  const p = Math.min(1, Math.max(0, Number.isFinite(progress) ? progress : 1));
  const mix = (from, to) => from + (to - from) * p;
  return {
    core: mix(CHOSEN.core, SETTLED.core),
    glow: mix(CHOSEN.glow, SETTLED.glow),
    alpha: mix(CHOSEN.alpha, SETTLED.alpha) * (kind === 'nodes' ? NODES_ALPHA : 1),
  };
}

/** Does this kind of highlight pulse when chosen?  The skeleton check's rings come dozens at a time. */
export function pulses(kind) {
  return kind !== 'nodes';
}
