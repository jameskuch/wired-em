// Display settings kept per volume (2.47.0).
//
// Flip Z depends on the volume as much as on taste.  The IPL boundary markers
// put RC1's and RC2's GCL on the +Z side of their INL, and the monkey volumes'
// below it, so one Flip Z draws one volume right way up and another upside
// down.  James Anderson asked for exactly this (#ai-requests, 2026-10-30):
// "the RC2 volume should have Invert Z enabled by default unless the user has
// saved that setting differently".  So Flip Z is kept per volume:
//   - a volume starts from the volume table's default (`/api/volumes`
//     `display_defaults`, on for RC2);
//   - once you set it by hand on a volume, this browser keeps your setting
//     for that volume, in every tab.
// The Volume menu decides which volume's setting applies: the scene has one
// Flip Z, whatever volumes its cells come from.
//
// A session or workspace saved by this build marks its Display snapshot
// `per_volume`, and a restore takes its Flip Z as saved.  One saved before
// has no mark.  Its Flip Z off was the old default, not a choice, so the
// volume's own default replaces it.  Its Flip Z on was a choice, so this
// browser's own session keeps it for the volume it was saved on.
//
// Pure: storage is passed in, so node can test it.

/** Where this browser keeps the settings set by hand, by volume: `{ RC2: { flip_z: false } }`. */
export const STORE_KEY = 'sbfsem:volume-display';

/** The Display snapshot keys kept per volume. */
export const PER_VOLUME_KEYS = Object.freeze(['flip_z']);

/** What a volume the server lists no default for starts with (the panel's own default). */
export const FALLBACK = Object.freeze({ flip_z: false });

const plainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/**
 * Keep only what this module wrote: per-volume objects whose values are
 * booleans under a known key.  Anything else is a value someone or something
 * else left under the name, and it is ignored, not trusted.
 */
export function cleanChoices(raw) {
  const out = {};
  if (!plainObject(raw)) return out;
  for (const [source, values] of Object.entries(raw)) {
    if (!plainObject(values)) continue;
    const kept = {};
    for (const key of PER_VOLUME_KEYS) {
      if (typeof values[key] === 'boolean') kept[key] = values[key];
    }
    if (Object.keys(kept).length) out[source] = kept;
  }
  return out;
}

/** The settings set by hand, by volume (`{}` with nothing stored, or no storage). */
export function readChoices(storage) {
  if (!storage) return {};
  try {
    const text = storage.getItem(STORE_KEY);
    return text ? cleanChoices(JSON.parse(text)) : {};
  } catch {
    return {};
  }
}

/** Store them; false when the browser would not (private window, quota). */
export function writeChoices(storage, choices) {
  if (!storage) return false;
  try {
    storage.setItem(STORE_KEY, JSON.stringify(cleanChoices(choices)));
    return true;
  } catch {
    return false;
  }
}

/** A volume's own defaults, as `/api/volumes` lists them, over the panel's. */
export function volumeDefaults(volumes, source) {
  const listed = (volumes || []).find((v) => v.name === source)?.display_defaults;
  const out = { ...FALLBACK };
  if (plainObject(listed)) {
    for (const key of PER_VOLUME_KEYS) {
      if (typeof listed[key] === 'boolean') out[key] = listed[key];
    }
  }
  return out;
}

/** What `source` is shown with: what was set by hand on it, else its default. */
export function effectiveDisplay(volumes, choices, source) {
  return { ...volumeDefaults(volumes, source), ...(cleanChoices(choices)[source] || {}) };
}

/** `choices` with `values` set by hand on `source` (a new object; `choices` is not changed). */
export function remember(choices, source, values) {
  const out = cleanChoices(choices);
  if (!source) return out;
  const kept = { ...(out[source] || {}) };
  for (const key of PER_VOLUME_KEYS) {
    if (typeof values?.[key] === 'boolean') kept[key] = values[key];
  }
  if (Object.keys(kept).length) out[source] = kept;
  return out;
}

/**
 * The per-volume keys a saved Display snapshot was not kept under, so the
 * volume's setting replaces them on a restore: every key when there is no
 * snapshot, none for one this build saved.
 */
export function unsettledKeys(display) {
  if (!plainObject(display)) return [...PER_VOLUME_KEYS];
  const marked = Array.isArray(display.per_volume) ? display.per_volume : [];
  return PER_VOLUME_KEYS.filter((key) => !marked.includes(key));
}

/**
 * `choices` with what a session saved before this build had set by hand: a
 * value other than the panel's default (Flip Z on) becomes the choice for the
 * volume it was saved on, unless that volume already has one.  Only keys in
 * `unsettledKeys`, and only for this browser's own sessions (a workspace
 * someone sent is not a choice made here).
 */
export function seedFromSession(choices, source, display) {
  const out = cleanChoices(choices);
  if (!source || !plainObject(display)) return out;
  const values = {};
  for (const key of unsettledKeys(display)) {
    const v = display[key];
    if (typeof v !== 'boolean' || v === FALLBACK[key]) continue;
    if (out[source] && key in out[source]) continue;
    values[key] = v;
  }
  return Object.keys(values).length ? remember(out, source, values) : out;
}

/**
 * After an undo or redo puts `display` back on `source`: the choice to keep so
 * the volume shows what the undo left, or null when its setting already
 * gives that.
 */
export function choiceAfterUndo(volumes, choices, source, display) {
  if (!source || !plainObject(display)) return null;
  const now = effectiveDisplay(volumes, choices, source);
  const values = {};
  for (const key of PER_VOLUME_KEYS) {
    if (typeof display[key] === 'boolean' && display[key] !== now[key]) values[key] = display[key];
  }
  return Object.keys(values).length ? values : null;
}

/**
 * A saved camera (`Renderer3D.cameraState`, in the scene's frame) moved to a
 * scene whose Flip Z is `flip` when it was saved under `savedFlip`, as a click
 * on Flip Z moves it (`Renderer3D.setFlipZ`): what was at the orbit target's
 * level stays there on the screen, and the camera keeps its direction.  Without
 * this, an older session on RC2 came back looking at where its cells had been
 * before the volume's Flip Z mirrored them.  The same camera when nothing
 * changed.
 */
export function cameraUnderFlip(camera, savedFlip, flip) {
  if (!camera || !!savedFlip === !!flip) return camera;
  const { position, target } = camera;
  if (!Array.isArray(position) || !Array.isArray(target) || position.length < 3 || target.length < 3) return camera;
  const level = target[2];
  return {
    ...camera,
    position: [position[0], position[1], position[2] - 2 * level],
    target: [target[0], target[1], target[2] - 2 * level],
  };
}
