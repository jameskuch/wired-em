// Render compare's Registrations… dialog: the same rendering of the compared cell
// under several registrations, side by side (asked for 2026-10-20: "the exact same
// cell with the exact same render parameters while changing multiple combinations
// of Registration").  A grid of boxes -- Viking's correction down the side (none,
// Residual field, Curve fit, Field + fit), the stored positions and the current
// section transforms across the top -- and where they go: one pane each, or the
// layers of the focused pane.  CompareView.sweepRegistrations sets them up;
// Compare builds them.  The choice is remembered in this browser (localStorage, a
// convenience: a private window starts from the defaults).
//
// The rules (which registrations, in what order, how many fit) are
// registrations.js's, tested under node; this is the page around them.

import {
  CORRECTIONS, registrationTag, registrationOfTag, correctionName, defaultSweep, planSweep,
  MAX_SWEEP_LAYERS, MAX_SWEEP_PANES,
} from './registrations.js';

const STORE_KEY = 'sbfsem:compare-registrations';

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function help(node, text) {
  node.dataset.help = text;
  node.dataset.helpDelay = '900';
  return node;
}

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (!saved || typeof saved !== 'object') return null;
    const tags = Array.isArray(saved.tags) ? saved.tags.filter((t) => registrationOfTag(t)) : null;
    return { tags, target: saved.target === 'layers' ? 'layers' : 'panes' };
  } catch {
    return null;
  }
}

function savePrefs(prefs) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(prefs)); } catch { /* private window: not kept */ }
}

export class RegistrationDialog {
  /**
   * `dialog` is the page's `<dialog>`; `compare` the CompareView it sets up;
   * `onSetUp({ panes })` runs after a sweep is set up (the app puts the pane count in
   * its box); `onCompare()` presses Compare.
   */
  constructor(dialog, { compare, onSetUp = () => {}, onCompare = () => {} } = {}) {
    this.dialog = dialog;
    this.compare = compare;
    this.onSetUp = onSetUp;
    this.onCompare = onCompare;
    this.body = dialog.querySelector('.dlg-body') || dialog;
    this.ticked = new Set();
    this.target = 'panes';
    this.context = null;
  }

  /** Open it for the compared cell and the layer a sweep would copy. */
  open() {
    this.context = this.compare.sweepContext();
    const saved = loadPrefs();
    const { hasTransforms, defaultCorrection } = this.context;
    const offered = new Set(this._offered().map(registrationTag));
    const start = saved?.tags?.length ? saved.tags.filter((t) => offered.has(t))
      : defaultSweep({ hasTransforms, defaultCorrection }).map(registrationTag);
    this.ticked = new Set(start);
    this.target = saved?.target ?? 'panes';
    this._draw();
    if (!this.dialog.open) this.dialog.showModal();
  }

  _offered() {
    const lives = this.context.hasTransforms ? [false, true] : [false];
    return CORRECTIONS.flatMap((corrections) => lives.map((live) => ({ live, corrections })));
  }

  _draw() {
    const ctx = this.context;
    const body = this.body;
    body.replaceChildren();
    body.append(el('h2', '', 'Registrations'));
    if (!ctx.source) {
      body.append(el('p', '', 'There is no technique layer to copy: give a pane a technique first.'));
      body.append(this._buttons({ none: true }));
      return;
    }
    const intro = el('p', 'reg-intro');
    intro.append(`The same rendering of ${ctx.cell} under each registration ticked: `);
    intro.append(el('b', '', ctx.source));
    intro.append(`, ${ctx.where}, with every option it has now. Each registration is its own copy of the cell, `
      + 'loaded on the server the first time a layer asks for it; each layer’s error map is measured against '
      + 'its own copy’s annotations.');
    body.append(intro);

    const lives = ctx.hasTransforms ? [false, true] : [false];
    const table = el('table', 'reg-grid');
    const head = el('tr');
    head.append(el('th', '', ''));
    for (const live of lives) {
      const th = el('th', '', live ? 'Current transforms' : 'Stored positions');
      help(th, live
        ? 'Every annotation where Viking draws it now: its stored position mapped through the volume’s current '
          + 'section transforms (the stos files its VikingXML lists).'
        : 'Every annotation where Viking’s database stores it, as it was when the annotation was saved.');
      head.append(th);
    }
    const thead = el('thead');
    thead.append(head);
    const tbody = el('tbody');
    this.boxes = [];
    for (const corrections of CORRECTIONS) {
      const row = el('tr');
      const name = el('th', '', corrections === 'off' ? 'No correction' : correctionName(corrections));
      help(name, corrections === 'off' ? 'Viking’s section correction off.'
        : `Viking’s section correction in its ${correctionName(corrections)} mode, on top of the positions above.`);
      row.append(name);
      for (const live of lives) {
        const tag = registrationTag({ live, corrections });
        const td = el('td', 'reg-cell');
        const label = el('label', 'check');
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = this.ticked.has(tag);
        box.dataset.tag = tag;
        box.addEventListener('change', () => {
          if (box.checked) this.ticked.add(tag); else this.ticked.delete(tag);
          this._sync();
        });
        label.append(box);
        if (tag === ctx.loadedTag) label.append(el('span', 'reg-loaded', ' as loaded'));
        td.append(label);
        row.append(td);
        this.boxes.push(box);
      }
      tbody.append(row);
    }
    table.append(thead, tbody);
    body.append(table);

    const where = el('div', 'reg-target');
    for (const [value, text, tip] of [
      ['panes', `One pane each (up to ${MAX_SWEEP_PANES})`, 'The grid gets one pane per registration, each '
        + 'with that one layer; the pane count follows.'],
      ['layers', `Layers of pane ${ctx.pane} (up to ${MAX_SWEEP_LAYERS})`, 'The focused pane’s layers become '
        + 'one per registration, drawn over each other in the palette’s colors (the first opaque).'],
    ]) {
      const label = el('label', 'check');
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'reg-target';
      radio.value = value;
      radio.checked = this.target === value;
      radio.addEventListener('change', () => { if (radio.checked) { this.target = value; this._sync(); } });
      label.append(radio, ` ${text}`);
      help(label, tip);
      where.append(label);
    }
    body.append(where);
    this.statusEl = el('p', 'reg-status');
    body.append(this.statusEl);
    body.append(this._buttons());
    this._sync();
  }

  _buttons({ none = false } = {}) {
    const row = el('div', 'dlg-actions');
    const cancel = el('button', '', none ? 'Close' : 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', () => this.dialog.close());
    row.append(cancel);
    if (none) return row;
    this.setUpEl = el('button', '', 'Set up');
    this.setUpEl.type = 'button';
    help(this.setUpEl, 'Put the layers in place; press Compare when you are ready to build them.');
    this.setUpEl.addEventListener('click', () => this._apply(false));
    this.compareEl = el('button', 'primary', 'Set up and compare');
    this.compareEl.type = 'button';
    this.compareEl.addEventListener('click', () => this._apply(true));
    row.append(this.setUpEl, this.compareEl);
    return row;
  }

  _plan() {
    const ticked = [...this.ticked].map(registrationOfTag).filter(Boolean);
    return planSweep(ticked, { target: this.target, hasTransforms: this.context.hasTransforms });
  }

  _sync() {
    if (!this.statusEl) return;
    const { registrations, problem } = this._plan();
    const n = registrations.length;
    this.statusEl.textContent = problem ?? (this.target === 'layers'
      ? `${n} layer${n === 1 ? '' : 's'} in pane ${this.context.pane}.`
      : `${n} pane${n === 1 ? '' : 's'}, one layer each.`);
    this.statusEl.classList.toggle('error', !!problem);
    if (this.setUpEl) this.setUpEl.disabled = !!problem;
    if (this.compareEl) this.compareEl.disabled = !!problem;
  }

  _apply(build) {
    const { registrations, problem } = this._plan();
    if (problem) return;
    const done = this.compare.sweepRegistrations(registrations, { target: this.target });
    if (done.problem) {
      this.statusEl.textContent = done.problem;
      this.statusEl.classList.add('error');
      return;
    }
    savePrefs({ tags: registrations.map(registrationTag), target: this.target });
    this.dialog.close();
    this.onSetUp(done);
    if (build) this.onCompare();
  }
}
