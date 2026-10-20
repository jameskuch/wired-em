// The render options' form: every control of the 3D render panel (and, in
// Render compare, of a pane) built from the schema, so the same option has the
// same label, help, range, step and default wherever it is shown.
//
// buildRenderForm() puts the fields into mount points the caller already has
// -- a `{ parent, before }` pair per slot, never a wrapper element, because
// the panel's row and its More menu are styled by their own parents -- and
// returns { read, write, refresh, el, control }.  The decisions (what a control
// is, what a field's state asks of it, how a typed value reads back) are pure
// functions below, tested under node without a DOM; the DOM code only applies
// them, and is checked against the hand-written markup it replaced.
//
// Every render-option <select> is created in this file, so a custom listbox in
// place of the browser's native one would be one change here.

import {
  RENDER_SLOTS, FIELD_BY_KEY, DEFAULT_RENDER, normalizeRender, fieldState, helpFor,
} from './renderoptions.js';

/** The readout beside a percent slider. */
export const percentText = (value) => `${Math.round(value * 100)}%`;

// -- pure: a size typed into a box, with a list of named sizes beside it ---------
// The Voxel size (`numchoice`) is typed, or picked from the schema's named sizes
// in the list beside the box.  The box commits on Enter or when it is left, and
// only then: what is typed meanwhile is read as it stands when it is a size in
// range (so the cost chip can follow it), and otherwise as the last size set.

/** The text a size box shows: at most four decimals, no trailing zeros ("0.025", "1"). */
export function numberText(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  return String(Math.round(n * 1e4) / 1e4);
}

/**
 * The size typed into `field`'s box, or null when the text is not one: a plain
 * decimal number (a trailing "µm" or "um" and a decimal comma are allowed)
 * between the field's min and max, rounded to a ten-thousandth of a micron.
 */
export function parseChoice(field, text) {
  const t = String(text ?? '').trim().replace(/\s*(µm|um|μm)$/i, '').replace(',', '.');
  if (!/^\+?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(t)) return null;
  const value = Number(t);
  if (!Number.isFinite(value) || value < field.min || value > field.max) return null;
  return Math.round(value * 1e4) / 1e4;
}

/**
 * What committing the box's `text` does when `committed` is the size set now:
 * `{ value, text, reverted }`.  Anything that is not a size in range reverts
 * to `committed`.
 */
export function commitChoice(field, text, committed) {
  const value = parseChoice(field, text);
  if (value === null) return { value: committed, text: numberText(committed), reverted: true };
  return { value, text: numberText(value), reverted: false };
}

/** The named size `value` is, as its list option's value; '' for a size the list does not name. */
export function choiceFor(field, value) {
  const hit = field.choices.find(([v]) => Math.abs(Number(v) - Number(value)) < 1e-9);
  return hit ? String(hit[0]) : '';
}

/**
 * The named size one step from `value`: `dir` +1 the next larger one, -1 the
 * next smaller, from a size the list does not name too; `value` itself past
 * either end.  What ArrowUp and ArrowDown do in the box.
 */
export function stepChoice(field, value, dir) {
  const sizes = field.choices.map(([v]) => Number(v)).sort((a, b) => a - b);
  const v = Number(value);
  const next = dir > 0 ? sizes.find((s) => s > v + 1e-9) : [...sizes].reverse().find((s) => s < v - 1e-9);
  return next ?? v;
}

// -- pure: what a field is, what its state asks, how a value reads and writes ---

/**
 * A field's elements, as plain data: its control, and a readout (`out`), a
 * second control (`side`) or a list of named values (`menu`) where it has one.
 */
export function describeControls(field) {
  const appDefault = field.hasAppDefault ? field.appDefault : field.default;
  const optionsOf = (choices, selected) => choices.map(([value, label]) => ({
    value: String(value), label, selected: String(value) === String(selected),
  }));
  const number = (type = 'number') => {
    const spec = { role: 'control', tag: 'input', type };
    for (const attr of ['min', 'max', 'step']) if (field[attr] !== null) spec[attr] = field[attr];
    if (appDefault !== null) spec.value = appDefault;
    if (field.placeholder) spec.placeholder = field.placeholder;
    return spec;
  };
  switch (field.kind) {
    case 'enum':
      return [{ role: 'control', tag: 'select', options: optionsOf(field.choices, appDefault) }];
    case 'numchoice':
      // A text box, not a number box: a number box's own arrows step by `step`
      // and its validity rules reject "0.025 µm"; parseChoice is the one rule.
      // The list beside it opens in the page on Windows and Linux (selectpicker.js),
      // never as a <datalist>, which is a native popup there.
      return [
        {
          role: 'control', tag: 'input', type: 'text', inputmode: 'decimal',
          value: numberText(appDefault), label: `${field.label} size, µm`,
        },
        {
          role: 'menu', tag: 'select', label: `${field.label} sizes`,
          options: [
            // Selected for a size the list does not name; never shown as a row.
            { value: '', label: 'typed size', selected: false, hidden: true },
            ...optionsOf(field.choices, appDefault),
          ],
        },
      ];
    case 'percent':
      return [number('range'), { role: 'out', tag: 'output', text: percentText(appDefault) }];
    case 'optfloat': {
      const controls = [number()];
      const companion = field.companion ? FIELD_BY_KEY[field.companion] : null;
      if (companion) {
        controls.push({
          role: 'side', tag: 'select',
          options: optionsOf(companion.choices, companion.default ? 'above' : 'below'),
        });
      }
      return controls;
    }
    default:
      return [number()];
  }
}

/** What a field's state asks of its element: hidden, greyed with a reason, disabled. */
export function fieldPlan(field, state) {
  return {
    hidden: state.hidden,
    inert: state.inert,
    help: helpFor(field, state),
    disabled: state.disabled,
    title: state.title,
  };
}

/** A control's text as the value its option holds. */
export function readValue(field, raw) {
  switch (field.kind) {
    case 'enum': return raw;
    case 'optfloat': return raw === '' ? undefined : Number(raw);
    default: return Number(raw);     // Number('') is 0, which normalizeRender clamps up
  }
}

// What a control is given for a value it does not list: only the soma proxy
// was ever coerced (an unknown one reads as discs); every other select keeps
// what it is handed, and reads back blank if it has no such option.
const WRITE_COERCE = { ultra_soma: (value) => (value === 'sphere' ? 'sphere' : 'discs') };

/** The string a control takes for `value`. */
export function writeValue(field, value) {
  const coerce = WRITE_COERCE[field.key];
  return coerce ? coerce(value) : value;
}

// -- the DOM ------------------------------------------------------------------------

function makeElement(spec, field, legacy) {
  const element = document.createElement(spec.tag);
  if (spec.tag === 'input') {
    element.type = spec.type;
    for (const attr of ['min', 'max', 'step', 'placeholder', 'inputmode']) {
      if (spec[attr] !== undefined) element.setAttribute(attr, String(spec[attr]));
    }
    if (spec.value !== undefined) element.setAttribute('value', String(spec.value));
  } else if (spec.tag === 'select') {
    for (const o of spec.options) {
      const option = new Option(o.label, o.value, false, o.selected);
      if (o.hidden) option.hidden = true;
      element.add(option);
    }
  } else if (spec.tag === 'output') {
    element.textContent = spec.text;
  }
  if (spec.label) element.setAttribute('aria-label', spec.label);
  if (legacy) element.id = field.ids[spec.role];
  return element;
}

// A size box that was given something it could not read shows red this long.
const INVALID_MS = 1200;
// The change a size box sends once it has taken a size (any other change event
// on it, a browser's or a script's, is taken as a request to take one).
const SETTLED = Symbol('numchoice settled');

/**
 * Wire a size box and its list: Enter or leaving the box takes what is typed
 * (or puts the size back when it is not one), Escape puts the size back,
 * ArrowUp and ArrowDown step through the named sizes, and a pick from the list
 * is the box's new size.  Each time the size changes the box sends one `change`.
 */
function wireChoice(b) {
  const { field, control: box, menu } = b;
  let flash = 0;
  const show = (value) => {
    box.value = numberText(value);
    menu.value = choiceFor(field, value);
  };
  const announce = () => {
    const event = new Event('change', { bubbles: true });
    event[SETTLED] = true;
    box.dispatchEvent(event);
  };
  const settle = () => {
    const { value, text, reverted } = commitChoice(field, box.value, b.committed);
    const changed = value !== b.committed;
    const shown = box.value;
    b.committed = value;
    show(value);
    if (reverted && shown !== text) {
      box.classList.add('invalid');
      clearTimeout(flash);
      flash = setTimeout(() => box.classList.remove('invalid'), INVALID_MS);
      // What was typed is gone: whatever followed the typing hears the size back.
      box.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (changed) announce();
  };
  box.addEventListener('change', (event) => {
    if (event[SETTLED]) return;
    // The browser's own change (on leaving an edited box), or a script's:
    // settle it, and let only the settled one through.
    event.stopImmediatePropagation();
    settle();
  });
  box.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      settle();
    } else if (event.key === 'Escape' && box.value !== numberText(b.committed)) {
      // Escape first puts the size back; a second one closes whatever holds the box.
      event.preventDefault();
      event.stopPropagation();
      show(b.committed);
      box.dispatchEvent(new Event('input', { bubbles: true }));
    } else if ((event.key === 'ArrowUp' || event.key === 'ArrowDown')
        && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      const from = parseChoice(field, box.value) ?? b.committed;
      box.value = numberText(stepChoice(field, from, event.key === 'ArrowUp' ? 1 : -1));
      settle();
    }
  });
  const pick = () => {
    if (menu.value === '') return;
    b.committed = Number(menu.value);
    box.value = numberText(b.committed);
  };
  // The list says `input` then `change`, like a select; the box takes the size on the first.
  menu.addEventListener('input', pick);
  menu.addEventListener('change', pick);
}

function buildField(field, legacy) {
  const label = document.createElement('label');
  label.className = 'field';
  if (legacy) label.id = field.ids.wrapper;
  label.dataset.key = field.key;
  label.dataset.help = field.help;
  label.append(document.createTextNode(field.label));
  const built = { field, label };
  // A size box and its list sit together, with their unit after them.
  const holder = field.kind === 'numchoice' ? document.createElement('span') : label;
  for (const spec of describeControls(field)) {
    const element = makeElement(spec, field, legacy);
    built[spec.role] = element;
    holder.append(element);
  }
  if (holder !== label) {
    holder.className = 'numchoice';
    built.menu.className = 'numchoice-menu';
    const unit = document.createElement('span');
    unit.className = 'numchoice-unit';
    unit.textContent = 'µm';
    holder.append(unit);
    label.append(holder);
    built.committed = field.hasAppDefault ? field.appDefault : field.default;
    wireChoice(built);
  }
  return built;
}

/** Whether a size box is being typed into: it has the focus and its text is not its size's. */
export function editing(box, committed) {
  const focused = typeof document !== 'undefined' && document.activeElement === box;
  return focused && String(box.value) !== numberText(committed);
}

/** What a field's control says: a size box reads what is typed when it is a size, else the size set. */
function readOne(b) {
  if (b.field.kind === 'numchoice') return parseChoice(b.field, b.control.value) ?? b.committed;
  return readValue(b.field, b.control.value);
}

/** Put field `b`'s value from the (completed) options `o` into its control(s). */
function writeOne(b, o) {
  const { field, control } = b;
  const value = o[field.key];
  if (field.kind === 'numchoice') {
    // Any size shows, named in the list or not (a refitted 0.063 um, a saved 0.01) --
    // except over what someone is typing: a box that has the focus and holds text
    // other than its size is left as it is, and takes the new size as the one an
    // Escape goes back to.
    const typing = editing(control, b.committed);
    b.committed = Number(value);
    if (!typing) control.value = numberText(value);
    b.menu.value = choiceFor(field, value);
  } else if (field.kind === 'optfloat') {
    control.value = value === undefined || value === null ? '' : value;
    if (b.side) b.side.value = o.keep_above ? 'above' : 'below';
  } else {
    control.value = writeValue(field, value);
    if (b.out) b.out.textContent = percentText(value);
  }
}

/**
 * Build the render form into `mounts` ({ strip: { parent, before }, more: {...} };
 * a slot with no mount is simply not built) and return its handles.
 *
 * `ids: 'legacy'` gives every element the id the hand-written panel had, which
 * the main panel's code still looks up; `'none'` gives none, for a form that
 * lives beside another (Render compare).  Either way each field carries its
 * `data-key`, and one delegated `input` and `change` listener per mount parent
 * calls `onInput(key, event)`: a field added to the schema is wired by being
 * built, and none can be forgotten.
 *
 * `skip` names options this form does not offer: Render compare leaves out
 * the view-scope Clip IPL, which only the 3D render applies.  A skipped option
 * is not read, so it comes back as its default.
 */
export function buildRenderForm({ mounts, ids = 'legacy', onInput = () => {}, skip = [] }) {
  const legacy = ids === 'legacy';
  const fields = new Map();
  const skipped = new Set(skip);
  for (const slot of Object.keys(RENDER_SLOTS)) {
    const mount = mounts[slot];
    if (!mount) continue;
    for (const key of RENDER_SLOTS[slot]) {
      if (skipped.has(key)) continue;
      const built = buildField(FIELD_BY_KEY[key], legacy);
      mount.parent.insertBefore(built.label, mount.before ?? null);
      fields.set(key, built);
    }
    // One handler per mount, and one mount's parent can contain another's (the strip's
    // panel holds the More menu), so each answers only for the keys built in its own slot.
    const own = new Set(RENDER_SLOTS[slot].filter((key) => !skipped.has(key)));
    const handler = (event) => {
      const label = event.target.closest?.('[data-key]');
      const built = label && own.has(label.dataset.key) && mount.parent.contains(label)
        ? fields.get(label.dataset.key) : null;
      if (!built) return;
      // The slider's readout follows it as it moves, before anything else hears.
      if (built.out && event.target === built.control) {
        built.out.textContent = percentText(Number(built.control.value));
      }
      onInput(built.field.key, event);
    };
    mount.parent.addEventListener('input', handler);
    mount.parent.addEventListener('change', handler);
  }

  const form = {
    /** The options the controls say, completed and clamped like every other path. */
    read() {
      const raw = {};
      for (const [key, b] of fields) raw[key] = readOne(b);
      const clip = fields.get('clip_ipl');
      if (clip?.side) raw.keep_above = clip.side.value === 'above';
      return normalizeRender(raw);
    },

    /** Put `options` (completed and clamped) into the controls; visibility is `refresh`'s. */
    write(options) {
      const o = normalizeRender(options);
      for (const b of fields.values()) writeOne(b, o);
    },

    /** Put one option's value into its control, as it is (not clamped), leaving the rest alone. */
    writeField(key, value) {
      const b = fields.get(key);
      if (b) writeOne(b, { [key]: value, keep_above: b.side ? b.side.value === 'above' : true });
    },

    /** Hide, grey out and disable fields for `options` (default: what is shown now). */
    refresh(options = form.read(), ctx = {}) {
      const state = fieldState(options, ctx);
      for (const [key, b] of fields) {
        const plan = fieldPlan(b.field, state[key]);
        b.label.hidden = plan.hidden;
        b.label.classList.toggle('inert', plan.inert);
        b.label.dataset.help = plan.help;
        // The hover help's movie (helpmovies.js): which option, under which method,
        // set to what (its frame is marked).
        b.label.dataset.helpMovie = key;
        b.label.dataset.helpMethod = String(options.method ?? '');
        b.label.dataset.helpValue = String(options[key] ?? '');
        if (b.field.requires) {
          b.control.disabled = plan.disabled;
          b.control.title = plan.title;
        }
      }
    },

    /** A field's wrapper (the label). */
    el(key) { return fields.get(key)?.label ?? null; },

    /** A field's own control. */
    control(key) { return fields.get(key)?.control ?? null; },
  };
  form.refresh(DEFAULT_RENDER, {});
  return form;
}
