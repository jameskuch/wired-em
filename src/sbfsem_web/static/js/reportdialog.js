// Render compare's report dialog: choose the renderings, the pairs and the
// pictures, then make the PDF on the server and save it.
//
// The choices and the request are comparereport.js's (pure, tested under node);
// this is the page around them.  The report is a server job (api.js
// compareReportJob, followed by pollJob like every render and measurement): the
// dialog shows its stage and has Stop, which the server honors between steps.
// Closing the dialog does not stop it: the toolbar's status line follows it, and
// the PDF is saved when it is done.  The viewpoints, sign rule, paper and the two
// "include" boxes are remembered in this browser (localStorage, a convenience:
// a private window starts from the defaults).

import { api, downloadFile } from './api.js';
import { formatElapsed } from './jobcard.js';
import { stageText } from './loadprogress.js';
import {
  REPORT_VIEWS, DEFAULT_VIEWS, PAIR_PICKS, MAX_RENDERINGS, pairKey, pickPairs, defaultPairs,
  settingsApart, estimatePages, estimateSeconds, durationText, createdText, reportBody, copiesOf,
} from './comparereport.js';

const STORE_KEY = 'sbfsem:compare-report';
const DEFAULT_PREFS = Object.freeze({
  views: [...DEFAULT_VIEWS], sign: 'enclosure', addendum: true, pictures: true, page: 'letter',
});

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (!saved || typeof saved !== 'object') return { ...DEFAULT_PREFS };
    const views = Array.isArray(saved.views)
      ? saved.views.filter((v) => REPORT_VIEWS.some((r) => r.key === v)) : DEFAULT_PREFS.views;
    return {
      views: views.length ? views : [...DEFAULT_PREFS.views],
      sign: saved.sign === 'normal' ? 'normal' : 'enclosure',
      addendum: saved.addendum !== false,
      pictures: saved.pictures !== false,
      page: saved.page === 'a4' ? 'a4' : 'letter',
    };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

function savePrefs(prefs) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(prefs)); } catch { /* private window: not kept */ }
}

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

function button(text, className = '', helpText = '') {
  const node = el('button', className, text);
  node.type = 'button';
  if (helpText) help(node, helpText);
  return node;
}

function box(checked, onChange, label = '') {
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = checked;
  if (label) input.setAttribute('aria-label', label);
  input.addEventListener('change', () => onChange(input.checked));
  return input;
}

function megabytes(bytes) {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

export class ReportDialog {
  /**
   * `dialog` is the page's `<dialog>`; `compare` the CompareView whose panes the
   * report is made of; `onStatus(text, kind)` the toolbar's status line, which
   * follows a report while the dialog is closed; `volumeName(source)` a volume's
   * name as the page shows it.
   */
  constructor(dialog, { compare, onStatus = () => {}, volumeName = (source) => source } = {}) {
    this.dialog = dialog;
    this.compare = compare;
    this.onStatus = onStatus;
    this.volumeName = volumeName;
    this.prefs = loadPrefs();
    this.sources = null;
    this.chosen = new Set();
    this.deviations = new Set();
    this.pairs = new Set();
    this.run = null;          // { controller, startedAt, timer, job, record } while a report is made
    this.last = null;         // { path, filename, meta } of the last finished report
    this.body = dialog.querySelector('.dlg-body') || dialog;
    dialog.addEventListener('close', () => this._closed());
  }

  get running() { return !!this.run; }

  /** Open the dialog over the panes as they are now (a report under way is shown as it goes). */
  open() {
    if (!this.running) this._read();
    this._draw();
    if (!this.dialog.open) this.dialog.showModal();
  }

  _read() {
    this.sources = this.compare.reportSources();
    const built = (this.sources?.renderings ?? []).filter((r) => r.built).slice(0, MAX_RENDERINGS);
    this.chosen = new Set(built.map((r) => r.ref));
    this.deviations = new Set(this.chosen);
    this.pairs = new Set(defaultPairs(this.sources?.renderings ?? [], this.chosen, this._differences()));
    if (!this.sources?.camera) this.prefs.views = this.prefs.views.filter((v) => v !== 'view');
    if (!this.prefs.views.length) this.prefs.views = ['xy1'];
  }

  _differences() {
    return (this.sources?.panes ?? []).filter((p) => p.kind === 'difference' && p.a && p.b).map((p) => [p.a, p.b]);
  }

  _held() {
    return (this.sources?.renderings ?? []).filter((r) => r.built && this.chosen.has(r.ref));
  }

  /** "R3" for a chosen rendering (its number in the request), "—" for one left out. */
  _number(ref) {
    const k = this._held().findIndex((r) => r.ref === ref);
    return k < 0 ? '—' : `R${k + 1}`;
  }

  // -- drawing ----------------------------------------------------------------------
  _draw() {
    const root = this.body;
    root.replaceChildren();
    root.append(el('h2', '', 'Comparison report (PDF)'));
    const record = this.sources?.record;
    if (!record) {
      root.append(el('p', 'dlg-note', 'Press Compare first: the report is made of the layers the panes have built.'));
      root.append(this._actions(false));
      return;
    }
    root.append(el('p', 'dlg-note', `c${record.id} · ${this.volumeName(record.source)}: a color PDF of the `
      + 'renderings chosen below, each held to the cell’s annotations, and every chosen pair measured against '
      + 'each other both ways, whether or not a Difference pane shows it: a summary with a table of the error '
      + 'between every pair, pages for each pane, rendering and pair, and an addendum that defines every '
      + 'number with its formula and a figure.'));
    root.append(this._renderingsSection(), this._pairsSection(), this._optionsSection());
    this.estimateEl = el('p', 'rep-estimate');
    root.append(this.estimateEl);
    this.progressEl = el('div', 'rep-progress');
    this.stageEl = el('div', 'rep-stage');
    this.barEl = el('progress', 'rep-bar');
    this.stopEl = button('Stop', 'rep-stop', 'Stop the report. The server stops at its next step; nothing is saved.');
    this.stopEl.addEventListener('click', () => this.stop());
    const line = el('div', 'rep-progress-line');
    line.append(this.barEl, this.stopEl);
    this.progressEl.append(this.stageEl, line);
    this.errorEl = el('p', 'dlg-error');
    this.errorEl.setAttribute('role', 'alert');
    this.doneEl = el('p', 'rep-done');
    this.doneEl.setAttribute('role', 'status');
    root.append(this.progressEl, this.errorEl, this.doneEl, this._actions(true));
    this._sync();
  }

  _actions(canCreate) {
    const actions = el('div', 'dlg-actions');
    const close = button('Close', '', 'Close this dialog. A report being made carries on, and is saved when it is done.');
    close.addEventListener('click', () => this.dialog.close('close'));
    actions.append(close);
    if (canCreate) {
      this.createEl = button('Create PDF', 'primary');
      this.createEl.addEventListener('click', () => this.create());
      actions.append(this.createEl);
    }
    return actions;
  }

  _renderingsSection() {
    const section = el('section', 'rep-section');
    const head = el('div', 'rep-head');
    head.append(el('b', '', 'Renderings'));
    this.renderCountEl = el('span', 'rep-count');
    head.append(this.renderCountEl);
    const all = button('All', 'rep-mini', 'Choose every built layer.');
    all.addEventListener('click', () => {
      for (const r of this.sources.renderings) if (r.built && this.chosen.size < MAX_RENDERINGS) this.chosen.add(r.ref);
      this._afterRenderings();
    });
    const none = button('None', 'rep-mini', 'Leave every layer out.');
    none.addEventListener('click', () => { this.chosen.clear(); this._afterRenderings(); });
    const devAll = button('All against the annotations', 'rep-mini',
      'Give every chosen rendering its page against the annotations (an error map each).');
    devAll.addEventListener('click', () => { for (const ref of this.chosen) this.deviations.add(ref); this._drawRenderings(); this._sync(); });
    const tools = el('span', 'rep-tools');
    tools.append(all, none, devAll);
    head.append(tools);
    section.append(head);
    this.renderTable = el('table', 'rep-table');
    const wrap = el('div', 'rep-table-wrap');
    wrap.append(this.renderTable);
    section.append(wrap);
    this._drawRenderings();
    return section;
  }

  _drawRenderings() {
    const table = this.renderTable;
    table.replaceChildren();
    const headRow = el('tr');
    for (const [text, cls, tip] of [['', ''], ['', ''], ['Pane · layer', ''], ['Technique', ''],
      ['Sets it apart', '', 'The settings in which a layer differs from the others of its technique.'],
      ['Faces', 'num'], ['vs annotations', 'center', 'A page of the rendering held to the cell’s annotations: '
        + 'pictures colored by the distance to the nearest annotation, its distribution, its profile '
        + 'along Z and the worst places.']]) {
      const th = el('th', cls, text);
      if (tip) help(th, tip);
      headRow.append(th);
    }
    const thead = el('thead');
    thead.append(headRow);
    const tbody = el('tbody');
    const all = this.sources.renderings;
    const builtAll = all.filter((r) => r.built);
    // Built from several registrations of the cell: each row says which.
    const registrations = copiesOf(builtAll) > 1;
    for (const r of all) {
      const row = el('tr', r.built ? '' : 'rep-off');
      const pick = box(this.chosen.has(r.ref), (on) => {
        if (on && this.chosen.size >= MAX_RENDERINGS) { pick.checked = false; return; }
        if (on) { this.chosen.add(r.ref); this.deviations.add(r.ref); } else this.chosen.delete(r.ref);
        this._afterRenderings();
      }, `Put pane ${r.pane} layer ${r.layer} in the report`);
      pick.disabled = !r.built || this.running;
      const swatch = el('span', 'rep-swatch');
      swatch.style.background = r.color;
      const name = el('td', 'rep-name');
      name.append(swatch, document.createTextNode(` ${this._number(r.ref)}`));
      const apart = r.built ? settingsApart(r, builtAll).join(', ') : r.why;
      const dev = box(this.deviations.has(r.ref), (on) => {
        if (on) this.deviations.add(r.ref); else this.deviations.delete(r.ref);
        this._sync();
      }, `Hold pane ${r.pane} layer ${r.layer} to the annotations`);
      dev.disabled = !r.built || !this.chosen.has(r.ref) || this.running;
      const cells = [pick, name, `${r.pane} · ${r.layer}`, r.label, apart,
        r.built ? r.faces.toLocaleString('en-US') : '—', dev];
      cells.forEach((cell, k) => {
        const td = cell instanceof HTMLElement && cell.tagName === 'TD' ? cell
          : el('td', k === 5 ? 'num' : k === 6 ? 'center' : '');
        if (cell instanceof HTMLElement && cell.tagName !== 'TD') td.append(cell);
        else if (!(cell instanceof HTMLElement)) td.textContent = String(cell);
        if (k === 3 && registrations && r.registration) td.append(el('span', 'rep-reg', r.registration));
        if (k === 4 && r.stale) {
          td.append(el('span', 'rep-stale', ' · changed since built: measured as built'));
        }
        row.append(td);
      });
      tbody.append(row);
    }
    table.append(thead, tbody);
  }

  _afterRenderings() {
    // Pairs that lost a rendering go; the Differences on screen of renderings put back return.
    for (const key of [...this.pairs]) {
      const [a, b] = key.split('|');
      if (!this.chosen.has(a) || !this.chosen.has(b)) this.pairs.delete(key);
    }
    for (const ref of [...this.deviations]) if (!this.chosen.has(ref)) this.deviations.delete(ref);
    this._drawRenderings();
    this._drawMatrix();
    this._sync();
  }

  _pairsSection() {
    const section = el('section', 'rep-section');
    const head = el('div', 'rep-head');
    head.append(el('b', '', 'Comparisons'));
    this.pairCountEl = el('span', 'rep-count');
    head.append(this.pairCountEl);
    section.append(head);
    const picks = el('div', 'rep-picks');
    for (const pick of PAIR_PICKS) {
      const b = button(pick.label, 'rep-mini', pick.help);
      b.addEventListener('click', () => {
        const chosen = pickPairs(this.sources.renderings, this.chosen, pick.id, this._differences());
        if (pick.id === 'none') this.pairs.clear();
        else if (pick.id === 'all') this.pairs = new Set(chosen);
        else for (const key of chosen) this.pairs.add(key);
        this._drawMatrix();
        this._sync();
      });
      picks.append(b);
    }
    section.append(picks);
    section.append(el('p', 'rep-hint', 'Each box is one pair, measured both ways on pages of its own. '
      + 'The picks other than All and None add to what is ticked.'));
    this.matrixWrap = el('div', 'rep-matrix-wrap');
    section.append(this.matrixWrap);
    this._drawMatrix();
    return section;
  }

  _drawMatrix() {
    const held = this._held();
    const wrap = this.matrixWrap;
    wrap.replaceChildren();
    if (held.length < 2) {
      wrap.append(el('p', 'rep-hint', 'Choose two renderings or more to compare them.'));
      return;
    }
    const table = el('table', 'rep-matrix');
    const top = el('tr');
    top.append(el('th'));
    held.forEach((r, j) => {
      const th = el('th', 'rep-col', `R${j + 1}`);
      th.style.setProperty('--swatch', r.color);
      help(th, `R${j + 1}: pane ${r.pane} layer ${r.layer}, ${r.label}`);
      top.append(th);
    });
    table.append(top);
    held.forEach((a, i) => {
      const row = el('tr');
      const th = el('th', 'rep-rowhead', `R${i + 1}`);
      th.style.setProperty('--swatch', a.color);
      help(th, `R${i + 1}: pane ${a.pane} layer ${a.layer}, ${a.label}`);
      row.append(th);
      held.forEach((b, j) => {
        const td = el('td', j <= i ? 'rep-blank' : '');
        if (j > i) {
          const key = pairKey(a.ref, b.ref);
          const input = box(this.pairs.has(key), (on) => {
            if (on) this.pairs.add(key); else this.pairs.delete(key);
            this._sync();
          }, `R${i + 1} ${a.label} against R${j + 1} ${b.label}`);
          input.disabled = this.running;
          td.append(input);
          help(td, `R${i + 1} (pane ${a.pane} layer ${a.layer}, ${a.label}) against `
            + `R${j + 1} (pane ${b.pane} layer ${b.layer}, ${b.label}): both ways`);
        }
        row.append(td);
      });
      table.append(row);
    });
    wrap.append(table);
  }

  _optionsSection() {
    const section = el('section', 'rep-section rep-options');
    const views = el('div', 'rep-row');
    views.append(el('span', 'rep-label', 'Viewpoints'));
    for (const view of REPORT_VIEWS) {
      const label = el('label', 'rep-check');
      help(label, view.help);
      const input = box(this.prefs.views.includes(view.key), (on) => {
        const next = new Set(this.prefs.views);
        if (on) next.add(view.key); else next.delete(view.key);
        if (!next.size) { input.checked = true; return; }
        this.prefs.views = REPORT_VIEWS.map((v) => v.key).filter((k) => next.has(k));
        this._prefsChanged();
      });
      if (view.key === 'view' && !this.sources?.camera) {
        input.dataset.noCamera = '1';
        help(label, 'The panes have no camera yet.');
      }
      label.append(input, document.createTextNode(` ${view.label}`));
      views.append(label);
    }
    const sign = el('div', 'rep-row');
    sign.append(el('span', 'rep-label', 'Sign'));
    const signs = [
      ['enclosure', 'Exact (enclosure)', 'A point is inside the other surface when that surface’s solid encloses it: '
        + 'right for surfaces assembled from pieces, and what Difference panes use. Slower.'],
      ['normal', 'Faster (normal)', 'The other surface’s outward normal at the nearest sample: about four times '
        + 'faster, and wrong where that sample is on a piece buried inside it.'],
    ];
    for (const [value, text, tip] of signs) {
      const label = el('label', 'rep-check');
      help(label, tip);
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'rep-sign';
      input.checked = this.prefs.sign === value;
      input.addEventListener('change', () => { this.prefs.sign = value; this._prefsChanged(); });
      label.append(input, document.createTextNode(` ${text}`));
      sign.append(label);
    }
    const include = el('div', 'rep-row');
    include.append(el('span', 'rep-label', 'Include'));
    for (const [key, text, tip] of [
      ['pictures', 'Pictures of the panes as they are now', 'Each pane as it is on screen, on its section’s first page and on the cover.'],
      ['addendum', 'Addendum: the mathematics', 'Nine pages that define every number with its formula and a figure.'],
    ]) {
      const label = el('label', 'rep-check');
      help(label, tip);
      label.append(box(this.prefs[key], (on) => { this.prefs[key] = on; this._prefsChanged(); }),
        document.createTextNode(` ${text}`));
      include.append(label);
    }
    const paper = el('div', 'rep-row');
    paper.append(el('span', 'rep-label', 'Paper'));
    for (const [value, text] of [['letter', 'Letter'], ['a4', 'A4']]) {
      const label = el('label', 'rep-check');
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'rep-page';
      input.checked = this.prefs.page === value;
      input.addEventListener('change', () => { this.prefs.page = value; this._prefsChanged(); });
      label.append(input, document.createTextNode(` ${text}`));
      paper.append(label);
    }
    section.append(views, sign, include, paper);
    this.optionInputs = [...section.querySelectorAll('input')];
    return section;
  }

  _prefsChanged() {
    savePrefs(this.prefs);
    this._sync();
  }

  /** The counts, the estimate, and what can be pressed. */
  _sync() {
    if (!this.estimateEl) return;
    const held = this._held();
    const built = this.sources.renderings.filter((r) => r.built).length;
    this.renderCountEl.textContent = `${held.length} of ${built} built`;
    const every = held.length * (held.length - 1) / 2;
    const pairs = [...this.pairs];
    this.pairCountEl.textContent = `${pairs.length} of ${every} pair${every === 1 ? '' : 's'}`;
    const deviations = held.filter((r) => this.deviations.has(r.ref)).map((r) => r.ref);
    const pages = estimatePages({
      n: held.length, pairs: pairs.length, deviations: deviations.length,
      panes: this.sources.panes.length, copies: copiesOf(held), addendum: this.prefs.addendum,
    });
    const seconds = estimateSeconds({
      renderings: held, deviations, pairs, views: this.prefs.views.length, sign: this.prefs.sign, pages,
    });
    this.estimateEl.textContent = held.length
      ? `About ${pages} pages; ${durationText(seconds)} on the server (less for what the panes already measured).`
      : 'Choose at least one rendering.';
    const running = this.running;
    if (this.createEl) {
      this.createEl.disabled = running || !held.length;
      this.createEl.textContent = running ? 'Working…' : 'Create PDF';
    }
    for (const input of this.optionInputs || []) input.disabled = running || input.dataset.noCamera === '1';
    this.progressEl.hidden = !running;
    this.dialog.classList.toggle('rep-running', running);
    if (this.last && !running) {
      this.doneEl.hidden = false;
    } else if (running) {
      this.doneEl.hidden = true;
    }
  }

  // -- making it ----------------------------------------------------------------------
  async create() {
    if (this.running || !this.sources?.record) return;
    const record = this.sources.record;
    const held = this._held();
    if (!held.length) return;
    this.errorEl.hidden = true;
    this.errorEl.textContent = '';
    this.last = null;
    this.doneEl.hidden = true;
    const snapshots = this.prefs.pictures ? this.compare.paneSnapshots() : new Map();
    const body = reportBody(this.sources, {
      chosen: this.chosen, deviations: this.deviations, pairs: this.pairs, views: this.prefs.views,
      sign: this.prefs.sign, addendum: this.prefs.addendum, page: this.prefs.page, snapshots,
      created: createdText(),
    });
    const controller = new AbortController();
    this.stopEl.textContent = 'Stop';
    this.run = { controller, startedAt: Date.now(), timer: null, job: null, record };
    this.run.timer = setInterval(() => this._showJob(), 500);
    this._drawRenderings();
    this._drawMatrix();
    this._sync();
    this._showJob();
    let result = null;
    try {
      result = await api.compareReportJob(record.source, record.id, body, {
        signal: controller.signal,
        onStatus: (job) => { if (this.run) this.run.job = job; this._showJob(); },
      });
    } catch (error) {
      this._ended();
      if (error?.aborted) {
        this.onStatus(`Report for c${record.id} stopped.`);
        return;
      }
      const message = error?.serverDetail || error?.detail || error?.message || String(error);
      this.errorEl.textContent = `The report could not be made: ${message}`;
      this.errorEl.hidden = false;
      this.onStatus(`Report for c${record.id} failed: ${message}`, 'error');
      if (!this.dialog.open) this.dialog.showModal();
      return;
    }
    this._ended();
    const path = api.compareReportPath(result.jobId);
    this.last = { path, filename: result.filename, meta: result };
    await this._save();
  }

  async _save() {
    const last = this.last;
    if (!last) return;
    this.doneEl.replaceChildren();
    try {
      await downloadFile(last.path, last.filename, { label: 'saving the report…' });
    } catch (error) {
      const message = error?.detail || error?.message || String(error);
      this.errorEl.textContent = `The report was made but could not be saved: ${message}`;
      this.errorEl.hidden = false;
      return;
    }
    const meta = last.meta;
    const again = button('Save again', 'rep-mini', 'Save the same PDF again (the server keeps it for ten minutes).');
    again.addEventListener('click', () => this._save());
    this.doneEl.append(document.createTextNode(`Saved ${last.filename}: ${meta.pages} pages, `
      + `${megabytes(meta.bytes)}, made in ${formatElapsed(meta.seconds * 1000)}. `), again);
    if (meta.notes?.length) this.doneEl.append(el('div', 'rep-notes', meta.notes.join(' ')));
    this.doneEl.hidden = false;
    this.onStatus(`Report saved: ${last.filename} (${meta.pages} pages).`);
  }

  _showJob() {
    const run = this.run;
    if (!run || !this.stageEl) return;
    const job = run.job;
    // The report's own step is the count (compare_pdf `_within`); the stage says the rest.
    const p = job?.progress;
    const stage = job?.status === 'running' && p?.stage ? p.stage
      : job ? stageText({ ...job, seconds: 0 }) : 'starting';
    const step = p && p.total && Number.isFinite(p.done) ? ` · step ${Math.min(p.done + 1, p.total)} of ${p.total}` : '';
    this.stageEl.textContent = `${stage}${step} · ${formatElapsed(Date.now() - run.startedAt)}`;
    if (p && p.total && Number.isFinite(p.done)) {
      this.barEl.max = p.total;
      this.barEl.value = p.done;
    } else {
      this.barEl.removeAttribute('value');
    }
    if (!this.dialog.open) this.onStatus(`Report for c${run.record.id}: ${stage}`);
  }

  _ended() {
    if (this.run) clearInterval(this.run.timer);
    this.run = null;
    if (this.estimateEl) {
      this._drawRenderings();
      this._drawMatrix();
      this._sync();
    }
  }

  /** Stop the report being made (the server stops at its next step). */
  stop() {
    if (!this.run) return;
    this.stopEl.textContent = 'Stopping…';
    this.run.controller.abort();
  }

  _closed() {
    if (this.running) this.onStatus(`Report for c${this.run.record.id} carries on; it is saved when it is done.`);
  }
}
