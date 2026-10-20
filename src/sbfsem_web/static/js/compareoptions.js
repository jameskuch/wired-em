// The render options of a Render compare layer, and the panel that edits them.
//
// A layer (one rendering of the cell in a pane) owns one complete, private set of
// the same options the 3D render panel edits: it is built from the render schema
// by the same form (renderform.js), so a control has the same label, help, range,
// step and default in both places.  Nothing here touches a cell's own render or
// the 3D render panel's controls except through a bridge the user presses.  (The
// functions keep the names they had when a pane held the options: `paneOptions`
// is the options of a layer.)
//
// The first half is pure -- option sets, the bridges' arithmetic, the cost
// chip -- and loads under node; the second half is the panel's DOM.

import { buildRenderForm } from './renderform.js';
import {
  FIELD_BY_KEY, TECH_KNOBS, RENDER_PRESETS, normalizeRender, matchPreset,
  fieldState, estimateBuildSeconds, techniqueDefaults, techKey, loadTechDefaults,
  storeTechDefaults, pick, MESH_OPTION_KEYS, CURVE_METHODS,
} from './renderoptions.js';
import { estimateChip, interim, LONG_WAIT_S } from './renderestimate.js';
import { renderConflicts, buildsField, fieldNotes } from './renderconflicts.js';
import { ConflictPanel } from './conflictpanel.js';

// -- a pane's options --------------------------------------------------------

/**
 * Clip IPL, its side and Color by strata are applied to a mesh after it is
 * built, and a clipped mesh loses the id the measurements name it by, so a pane
 * never carries them: whatever a bridge or a preset brings in, they are reset
 * to their defaults.
 */
export const VIEW_FIELDS = Object.freeze({ clip_ipl: undefined, keep_above: true, color_by_strata: false });

/** The options a layer builds: complete, clamped to what the server accepts, view fields pinned. */
export const paneOptions = (options) => ({ ...normalizeRender(options), ...VIEW_FIELDS });

/** A named technique by its id, or null (`custom` and `diff` are not techniques). */
export const presetById = (id) => RENDER_PRESETS.find((p) => p.id === id) ?? null;

/** The geometry options of `options`, as a mesh request and a measurement carry them. */
export const geometryOf = (options) => pick(options, MESH_OPTION_KEYS);

const choiceLabel = (key, value) => (
  FIELD_BY_KEY[key].choices.find(([v]) => v === value)?.[1] ?? String(value));

/** What a layer is building, in words: the preset it is, or its method and pathway. */
export function techniqueName(options) {
  const match = matchPreset(options);
  if (match) return match.label;
  return `${choiceLabel('method', options.method)} / ${choiceLabel('pathway', options.pathway)}`;
}

// -- the bridges -------------------------------------------------------------

// The technique, not its tuning: a copy of tuning to another layer never changes
// which technique that layer is.
const NOT_TUNING = new Set(['meshlab']);

/**
 * The tuning knobs a layer building with `options` actually uses: the ones its
 * technique shows and does not gray out.  A knob it hides is only holding a
 * default nobody sees, and one it grays out is dead.
 */
export function tuningKeys(options) {
  const state = fieldState(options);
  return TECH_KNOBS.filter((k) => !NOT_TUNING.has(k) && !state[k].hidden && !state[k].inert);
}

/**
 * `destination` with `source`'s value for every knob BOTH use; `changed` names those
 * that moved.  A knob the source does not use holds only its hidden default, and
 * copying that would overwrite what the destination was tuned to.
 */
export function copyTuning(source, destination) {
  const next = { ...destination };
  const changed = [];
  const shared = new Set(tuningKeys(source));
  for (const key of tuningKeys(destination)) {
    if (shared.has(key) && next[key] !== source[key]) { next[key] = source[key]; changed.push(key); }
  }
  return { options: paneOptions(next), changed };
}

/**
 * The technique's own defaults -- the user's saved tuning, else the recommended
 * values -- with the pane's technique kept, MeshLab mode included.
 */
export function resetOptions(options) {
  return paneOptions({ ...techniqueDefaults(options.method, options.pathway), meshlab: options.meshlab });
}

/** Remember a layer's tuning as the user's defaults for its technique (the 3D render's store). */
export function saveTuning(options) {
  const all = loadTechDefaults();
  all[techKey(options.method, options.pathway)] = Object.fromEntries(
    TECH_KNOBS.map((k) => [k, options[k]]));
  storeTechDefaults(all);
}

// -- what a build costs --------------------------------------------------------

/** "about 40 s", or "40 s" when `bare` (a chip that already says ≈). */
export function humanSeconds(s, { bare = false } = {}) {
  const about = bare ? '' : 'about ';
  if (s < 1) return bare ? 'under 1 s' : 'under a second';
  if (s < 60) return `${about}${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return r ? `${about}${m} min ${r} s` : `${about}${m} min`;
}

// What makes each pathway slow, said when a build is long (the 3D render's chip says the same).
const COST_HINTS = {
  voxel: 'Voxel remesh orig rebuilds every segment at least 1.5 µm across from its distance '
    + 'field, roughly 40% more work than Original.',
  convolution: 'The convolution surface sums a kernel along every skeleton segment on a '
    + 'whole-cell grid, the most expensive field. A coarser Fine voxel (0.1 µm) cuts it 4–5×.',
  ultraliser: 'Ultraliser voxelizes the proxies on a whole-cell grid and then smooths the '
    + 'contour; a coarser Fine voxel (0.2 µm is its own default) cuts the work several-fold.',
  voxel2: 'Voxel remesh contours the whole cell as one field at dendrite resolution, at several '
    + 'times the work of Voxel remesh orig. A coarser Fine voxel (0.1 µm) cuts it 4–5×.',
};

/**
 * The cost chip for building `options` on `record`: `{ text, warn, help }`, or
 * null when there is nothing to estimate (no cell, or one without a summary).
 */
export function costChip(record, options) {
  if (!record?.summary) return null;
  const seconds = estimateBuildSeconds(record, options);
  const heavy = seconds > LONG_WAIT_S;
  let help = `Building c${record.id} · ${record.source} with this technique takes `
    + `${humanSeconds(seconds)} on the server.`;
  if (heavy) {
    if (COST_HINTS[options.pathway] && options.method === 'cylinder') {
      help += ` ${COST_HINTS[options.pathway]}`;
    } else if (options.method === 'closedcurve') {
      help += ' Halving Voxel size multiplies the grid about eightfold; 0.10 µm is four times '
        + 'lighter than 0.05 µm.';
    }
    if (seconds > 120) help += ' The pane waits the whole time.';
  }
  return { text: `${heavy ? '⚠' : '≈'} ${humanSeconds(seconds, { bare: true })}`, warn: heavy, help };
}

// -- the panel -----------------------------------------------------------------

/** The bridges, in the order the menu lists them: [id, label, what it does]. */
export const BRIDGES = [
  ['main', 'Use 3D render settings',
    'Replace this layer\'s settings with what the 3D render panel shows now. The panel itself is '
    + 'not touched. Clip IPL is left out: only the 3D render applies it.'],
  ['cell', 'Use this cell\'s settings',
    'Replace this layer\'s settings with the render the compared cell was last built with in '
    + 'the 3D render tab (Clip IPL left out).'],
  ['send', 'Send to 3D render',
    'Put this layer\'s settings into the 3D render panel. Nothing is applied or rebuilt there: '
    + 'press Apply to selected in that panel to use them.'],
  ['copy', 'Copy tuning to other layers',
    'Copy this layer\'s tuning (every knob except the technique, the MeshLab mode and Clip IPL) '
    + 'to each other technique layer, in this pane and the others, only the knobs both layers\' '
    + 'techniques use: a knob either one hides or grays out is left as it is. The layers go out '
    + 'of date; press Compare to rebuild them.'],
  ['reset', 'Reset to technique defaults',
    'Put this technique\'s knobs back to your saved defaults for it, or the recommended values. '
    + 'The technique itself, MeshLab mode included, is kept.'],
  ['save', 'Save as my technique defaults',
    'Remember this layer\'s tuning as YOUR defaults for its technique, in this browser. The 3D '
    + 'render\'s technique picker and Defaults buttons use the same ones.'],
];

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/**
 * The options panel a pane's header arrow opens: the render schema's strip
 * and More controls for the layer being edited in the focused pane, the cost
 * chip, a More menu with the bridges, and the 3D render panel's row for
 * settings that will not build as set (conflictpanel.js).  One panel serves
 * every layer; `bind()` points it at one.
 *
 * `onInput(key, event)` hears every edit of a control, `onBridge(id)` a press of
 * a bridge, `onPick(set)` a press of one of the row's fixes (the options it
 * changes), `onClose()` the close button.
 */
export class OptionsPanel {
  constructor(host, { caps = () => ({}), onInput, onBridge, onPick, onClose, estimates = null }) {
    this.host = host;
    this.caps = caps;
    // The server's estimates (renderestimate.js), for the chip of an outline method
    // and the settings that will not build as set.
    this.estimates = estimates;
    this._shown = null;
    host.setAttribute('role', 'group');

    this.title = el('span', 'compare-opts-title');
    this.title.dataset.help = 'The render options of this layer only (a pane\'s first layer, unless '
      + 'you opened another layer\'s editor): the same controls as the 3D render panel. Nothing is '
      + 'rebuilt as you edit; the layer is marked out of date, and Compare rebuilds the layers '
      + 'that are.';
    this.chip = el('span', 'cost-chip');
    this.chip.hidden = true;
    // Its reasoning opens on a deliberate hover, as the 3D render's chip's does.
    this.chip.dataset.helpDelay = '2000';
    const divider = el('span', 'tool-divider');
    divider.setAttribute('aria-hidden', 'true');
    this.menuButton = el('button', '', 'More ▾');
    this.menuButton.type = 'button';
    this.menuButton.setAttribute('aria-haspopup', 'true');
    this.menuButton.setAttribute('aria-expanded', 'false');
    this.menu = el('div', 'menu-pop');
    this.menu.hidden = true;
    const rule = () => el('hr');
    this.menu.append(rule());
    const buttons = new Map();
    BRIDGES.forEach(([id, label, text], i) => {
      if (i === 3) this.menu.append(rule());
      const button = el('button', '', label);
      button.type = 'button';
      button.dataset.help = text;
      button.dataset.bridge = id;
      button.addEventListener('click', () => onBridge(id));
      buttons.set(id, button);
      this.menu.append(button);
    });
    this.bridges = buttons;
    const menuWrap = el('span', 'menu');
    menuWrap.append(this.menuButton, this.menu);
    this.close = el('button', 'compare-opts-close', '✕');
    this.close.type = 'button';
    this.close.setAttribute('aria-label', 'Close the pane options');
    this.close.dataset.help = 'Close this panel. The settings stay with the layer.';
    this.close.addEventListener('click', () => onClose?.());
    host.append(this.title, this.chip, divider, menuWrap, this.close);

    // Fields go in front of the chip (the strip) and the menu's first rule (More).
    this.form = buildRenderForm({
      mounts: {
        strip: { parent: host, before: this.chip },
        more: { parent: this.menu, before: this.menu.querySelector('hr') },
      },
      ids: 'none',
      skip: ['clip_ipl'],
      onInput: (key, event) => onInput(key, event),
    });
    // Settings that will not build as set for the cell: their controls black with red
    // text, and a row at the top of the panel with the fixes, best first.
    this.conflicts = new ConflictPanel({ form: this.form, mount: host, id: null, onPick: (set) => onPick?.(set) });

    // Esc closes the panel, as it closes the More menu (which has the first Esc when open).
    host.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.menu.hidden) onClose?.();
    });

    this.menuButton.addEventListener('click', (event) => {
      event.stopPropagation?.();
      this._menuOpen(this.menu.hidden);
    });
    // Only a chosen action closes it, as in the 3D render panel: typing into a
    // field in the menu must not slam it shut.
    this.menu.addEventListener('click', (event) => {
      if (event.target.closest?.('button')) this._menuOpen(false);
    });
    document.addEventListener?.('click', (event) => {
      if (!this.menu.hidden && !this.menu.contains(event.target)) this._menuOpen(false);
    });
    document.addEventListener?.('keydown', (event) => {
      if (event.key === 'Escape' && !this.menu.hidden) {
        this._menuOpen(false);
        this.menuButton.focus?.();
      }
    });
  }

  /** Open or close the More menu, above its button (fixed: the panel scrolls). */
  _menuOpen(open) {
    if (open) {
      const anchor = this.menuButton.getBoundingClientRect?.();
      if (anchor && typeof window !== 'undefined') {
        this.menu.style.right = `${window.innerWidth - anchor.right}px`;
        this.menu.style.bottom = `${window.innerHeight - anchor.top + 4}px`;
      }
    }
    this.menu.hidden = !open;
    this.menuButton.setAttribute('aria-expanded', String(open));
  }

  get open() { return !this.host.hidden; }

  /** Move keyboard focus to the panel's first control (the Method box). */
  focusFirst() { this.form.control('method')?.focus?.(); }

  show(open) {
    this.host.hidden = !open;
    if (!open) this._menuOpen(false);
  }

  /** The options the controls say (completed, clamped). */
  read() { return this.form.read(); }

  /** Point the panel at a layer: `{ title, options, record }`. */
  bind({ title, options, record }) {
    this.title.textContent = title;
    this.host.setAttribute('aria-label', `Render options, ${title}`);
    this.form.write(options);
    this.update({ options, record });
  }

  /**
   * What follows from the options without touching what is typed: which controls
   * show, the cost, and the settings that will not build as set.
   */
  update({ options, record, title }) {
    if (title !== undefined) this.title.textContent = title;
    this.form.refresh(options, this.caps());
    this._shown = { options, record };
    const held = this._estimate(record, options);
    const notes = this._conflicts(record, options, held);
    const cost = this._cost(record, options, held);
    this.chip.hidden = !cost;
    if (cost) {
      this.chip.textContent = cost.text;
      this.chip.classList.toggle('warn', cost.warn);
      this.chip.classList.toggle('over', !!cost.over);
      // An automatic Fine voxel the Face cap holds coarser is said on hover, as in the 3D render.
      this.chip.dataset.help = notes.length ? `${cost.help}\n\n${notes.join('\n')}` : cost.help;
    }
  }

  /**
   * The server's estimate of building this layer on the cell, for a method it
   * speaks for (Closed curve and Hybrid; a whole-cell field): asked for once
   * typing stops, and drawn when it comes, if the panel still shows the same
   * options.  Undefined while it is out, null when there is none to ask for or
   * the server could not say.
   */
  _estimate(record, options) {
    if (!this.estimates || !record?.summary) return null;
    if (!CURVE_METHODS.has(options.method) && !buildsField(options)) return null;
    const held = this.estimates.peek(record, options);
    const redraw = () => {
      const now = this._shown;
      if (now && now.record === record && JSON.stringify(now.options) === JSON.stringify(options)) {
        this.update(now);
      }
    };
    // An interim answer is held a moment and asked again: look again once it has gone.
    if (interim(held)) {
      clearTimeout(this._retry);
      this._retry = setTimeout(redraw, this.estimates.interimMs + 100);
    }
    if (held === undefined) this.estimates.soon('compare-pane', [record], options, redraw);
    return held;
  }

  /**
   * Mark the settings that will not build as set for the cell and list the fixes
   * (renderconflicts.js), or clear them; the field notes for the chip's hover.
   */
  _conflicts(record, options, held) {
    const entries = held ? [{ name: `c${record.id} · ${record.source}`, estimate: held }] : [];
    this.conflicts.show(entries.length ? renderConflicts(entries, options) : null);
    return entries.length ? fieldNotes(entries, options) : [];
  }

  /**
   * The chip: for Closed curve and Hybrid the server's estimate of the cell
   * (`held`, from `_estimate`), otherwise the rough one.
   */
  _cost(record, options, held) {
    if (this.estimates && record?.summary && CURVE_METHODS.has(options.method)) {
      if (held === undefined || held?.pending) {
        return { text: '≈ …', warn: false, over: false,
          help: 'Asking the server what building this layer at this Voxel size would cost.' };
      }
      const shown = estimateChip([{ name: `c${record.id} · ${record.source}`, estimate: held }]);
      if (shown) {
        return { text: shown.text, warn: shown.level === 'warn', over: shown.level === 'over',
          help: shown.help };
      }
    }
    return costChip(record, options);
  }

  /** The server's capabilities changed (the build info arrived): re-gate the controls. */
  refreshCaps(options) { this.form.refresh(options, this.caps()); }

  /** Write the normalized values back into the boxes (after a change). */
  write(options) { this.form.write(options); }
}
