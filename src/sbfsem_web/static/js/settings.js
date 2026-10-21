// The Settings tab: debug tools kept out of the way of the work.
//
// Three cards for this browser (which build the page is, clearing its cached
// files, resetting what it saved) and one for the server (its state, clearing
// its cached renders, replacing its build processes, restarting it), which only
// an admin is offered: the server decides, through /api/admin/whoami.
//
// The rules are pure and exported, so node can test them (tests/js/test_settings.mjs);
// SettingsPanel is the view, and touches the page only when it is shown.

/**
 * Everything this site keeps in the browser, by what it is for.  `keys` are
 * exact names, `prefixes` the start of a family of names; `keep` is shown and
 * never offered for a reset (the tab's id names its own saved session).
 * tests/unit/test_settings_storage.py fails when a key the scripts use is in
 * none of these, so a new one gets a name here the day it is written.
 */
export const STORAGE_GROUPS = [
  {
    id: 'session', label: 'Saved sessions',
    help: 'The cells, their renders and the view each tab brings back after a reload',
    keys: ['sbfsem.session.v1', 'sbfsem.restore.auto'], prefixes: ['sbfsem.session.v1.tab.'],
  },
  {
    id: 'render', label: 'Render defaults',
    help: 'The render settings you saved as defaults, for a new cell and for each technique',
    keys: ['sbfsem:startup-render', 'sbfsem:technique-defaults'],
  },
  {
    id: 'layout', label: 'Layout',
    help: "The cell panel's width and the full-screen sidebar",
    keys: ['sbfsem:sidebar-width', 'sbfsem:fullscreen-sidebar'],
  },
  { id: 'swatches', label: 'Saved colors', help: "The color picker's saved swatches", keys: ['sbfsem:swatches'] },
  {
    id: 'visualizations', label: 'Saved visualizations',
    help: 'The looks you saved from a cell (its Visualizations list); save them to a file first to keep them',
    keys: ['sbfsem.visualizations.v1'],
  },
  { id: 'connectivity', label: 'Connectivity query', help: 'The last query on the Connectivity tab', keys: ['sbfsem.connectivity.query'] },
  {
    id: 'report', label: 'Report options',
    help: 'Render compare’s report: the viewpoints, sign, paper and what to include, as last chosen',
    keys: ['sbfsem:compare-report'],
  },
  { id: 'dropdowns', label: 'Dropdown lists', help: 'Whether dropdowns open as the page’s own lists', keys: ['sbfsem:select-lists'] },
  {
    id: 'viking', label: 'Viking programs',
    help: 'Which Viking programs Open in Viking has seen answer, and the one chosen in this tab',
    keys: ['sbfsem.vikingClients', 'sbfsem.vikingChoice'],
  },
  { id: 'tab', label: 'This tab’s id', help: 'Names the tab’s own saved session; kept', keys: ['sbfsem.tab'], keep: true },
  { id: 'note', label: 'Settings note', help: 'What this tab did just before its last reload; kept', keys: ['sbfsem.settings.note'], keep: true },
];

const OTHER = { id: 'other', label: 'Other', help: 'Saved by this site under names this list does not know' };

/** The group a key belongs to, or the catch-all. */
export function groupOf(key) {
  for (const group of STORAGE_GROUPS) {
    if ((group.keys || []).includes(key)) return group;
    if ((group.prefixes || []).some((p) => key.startsWith(p))) return group;
  }
  return OTHER;
}

/** Every key of a Storage-like object, which may be absent or throw (a private window). */
function entries(store, area) {
  const out = [];
  if (!store) return out;
  try {
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i);
      if (key == null) continue;
      const value = store.getItem(key) ?? '';
      // UTF-16, as the browser counts its quota.
      out.push({ area, key, bytes: 2 * (key.length + value.length) });
    }
  } catch { /* blocked storage: nothing to list */ }
  return out;
}

/**
 * What the browser holds for this site, grouped: `[{ id, label, help, keep,
 * items: [{ area, key, bytes }], bytes }]`, in the order of STORAGE_GROUPS with
 * the unknown names last.  Groups with nothing saved are left out.
 */
export function inventory({ local = null, session = null } = {}) {
  const all = [...entries(local, 'local'), ...entries(session, 'session')];
  const groups = new Map();
  for (const item of all) {
    const group = groupOf(item.key);
    if (!groups.has(group.id)) {
      groups.set(group.id, { id: group.id, label: group.label, help: group.help,
        keep: Boolean(group.keep), items: [], bytes: 0 });
    }
    const row = groups.get(group.id);
    row.items.push(item);
    row.bytes += item.bytes;
  }
  const order = [...STORAGE_GROUPS.map((g) => g.id), OTHER.id];
  return [...groups.values()].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
}

/** Remove the items of the chosen groups (never a kept one); the number removed. */
export function resetGroups(rows, chosen, { local = null, session = null } = {}) {
  const stores = { local, session };
  let removed = 0;
  for (const row of rows) {
    if (row.keep || !chosen.has(row.id)) continue;
    for (const item of row.items) {
      try {
        stores[item.area]?.removeItem(item.key);
        removed += 1;
      } catch { /* blocked: leave it */ }
    }
  }
  return removed;
}

/** The build a page was loaded as: the `?v=` its own module was fetched with. */
export function pageAssets(moduleUrl) {
  try {
    return new URL(moduleUrl).searchParams.get('v') || '';
  } catch {
    return '';
  }
}

/**
 * This page's build against the one the server serves (CLAUDE.md ground rule 7:
 * the first question when "the bug is still there").
 */
export function buildCheck(page, server) {
  if (!server) return { state: 'unknown', text: 'The server did not answer, so the builds cannot be compared.' };
  if (!page) return { state: 'unknown', text: `The server serves ${server.assets}; this page could not tell which build it is.` };
  if (page === server.assets) return { state: 'same', text: `This page is the build the server serves (${page}).` };
  return {
    state: 'differs',
    text: `This page was loaded as ${page}, and the server now serves ${server.assets}. `
      + 'Reload; if it stays the old build, clear cached files below.',
  };
}

export function formatBytes(n) {
  if (n == null || !Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['kB', 'MB', 'GB', 'TB'];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${s % 60} s`;
  return `${s} s`;
}

/** How long the page waits for a restarted server before saying it has not come back. */
export const RESTART_WAIT_S = 120;

/**
 * Where a restart stands, from the instance it was asked of and the server's
 * latest answer to /api/build (null while it is down).
 */
export function restartPhase(beforeInstance, answer, elapsedS, limitS = RESTART_WAIT_S) {
  if (answer && answer.instance && answer.instance !== beforeInstance) return 'back';
  if (elapsedS >= limitS) return 'timeout';
  return answer ? 'stopping' : 'down';
}

/** The rows of the server card, from /api/admin/status. */
export function serverRows(status) {
  const mem = status.memory || {};
  const disk = status.disk_cache;
  const pool = status.pool || {};
  const builds = status.builds || {};
  const serviceMemory = status.service_memory_bytes ?? mem.process_rss_bytes;
  return [
    ['Version', `${status.version} · assets ${status.assets}`],
    ['Instance', `${status.instance} · up ${formatDuration(status.uptime_s)} · pid ${status.pid}`
      + (status.supervised ? ' · run by systemd' : ' · not run by systemd')],
    ['Memory', status.service_memory_bytes != null
      ? `${formatBytes(serviceMemory)} for the service and its build processes`
      : `${formatBytes(serviceMemory)} for this process`],
    ['Cells', `${mem.neurons_loaded ?? '—'} loaded · ${mem.copies_held ?? '—'} copies · `
      + `${(mem.annotations_held ?? 0).toLocaleString('en-US')} annotations`],
    ['Surfaces in memory', `${mem.meshes_cached ?? '—'} of ${mem.mesh_cache_limit ?? '—'} · ${formatBytes(mem.mesh_bytes)}`],
    ['Surfaces on disk', disk ? `${disk.files.toLocaleString('en-US')} files · ${formatBytes(disk.bytes)} `
      + `of ${formatBytes(disk.budget_bytes)}` : 'no disk cache'],
    ['Build processes', pool.build_processes
      ? `${pool.build_processes} · ${pool.pool_running ? 'running' : 'not started'} · `
        + `replaced ${pool.pool_recycles} time${pool.pool_recycles === 1 ? '' : 's'} for memory`
      : 'none: builds run in the server process'],
    ['Builds', `${builds.running ?? 0} running · ${builds.queued ?? 0} queued · `
      + `${builds.gate_waits ?? 0} waits for memory (longest ${builds.gate_wait_max_s ?? 0} s)`],
  ];
}

// -- the view ----------------------------------------------------------------

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (name === 'text') node.textContent = value;
    else if (name === 'class') node.className = value;
    else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? '' : String(value));
  }
  for (const child of [].concat(children)) {
    if (child != null) node.append(child);
  }
  return node;
}

const NOTE_KEY = 'sbfsem.settings.note';

function browserStore(name) {
  try {
    const store = window[name];
    store.getItem(NOTE_KEY);
    return store;
  } catch {
    return null;
  }
}

export class SettingsPanel {
  /**
   * `api`: the page's api; `assets`: the build this page was loaded as;
   * `beforeReload()`: saves the session so the reload brings it back;
   * `stopSaving()`: no more session writes from this page (a reset that
   * reloads must not be written back by the page on its way out).
   */
  constructor({ root, api, assets, beforeReload = () => {}, stopSaving = () => {} }) {
    this.root = root;
    this.api = api;
    this.assets = assets;
    this.beforeReload = beforeReload;
    this.stopSaving = stopSaving;
    this.built = false;
  }

  /** Called when the tab is shown: every card is read again. */
  async show() {
    if (!this.built) this._build();
    await Promise.all([this._refreshBuild(), this._refreshStorage(), this._refreshServer()]);
  }

  _build() {
    this.built = true;
    this.root.replaceChildren();
    const note = this._takeNote();
    if (note) this.root.append(el('div', { class: 'card settings-note', role: 'status', text: note }));

    this.buildText = el('p', { class: 'settings-text' });
    this.root.append(el('section', { class: 'card' }, [
      el('h3', { text: 'This page' }),
      this.buildText,
      el('div', { class: 'row settings-actions' }, [
        el('button', { type: 'button', text: 'Check again', onclick: () => this._refreshBuild() }),
        el('button', { type: 'button', text: 'Copy details', title: 'Copy this page’s build, the server’s and the browser, for a bug report',
          onclick: () => this._copyDetails() }),
      ]),
    ]));

    this.root.append(el('section', { class: 'card' }, [
      el('h3', { text: 'Cached files' }),
      el('p', { class: 'settings-text', text: 'Drops what this browser has cached of the site (the page, its scripts and styles) '
        + 'and reloads, so the page is the build the server serves. Your sessions, saved settings and sign-in stay.' }),
      el('div', { class: 'row settings-actions' }, [
        el('button', { type: 'button', text: 'Clear cached files and reload', onclick: () => this._clearBrowserCache() }),
      ]),
    ]));

    this.storageBody = el('tbody');
    this.storageTotal = el('p', { class: 'settings-text muted' });
    this.resetButton = el('button', { type: 'button', text: 'Reset the ticked items and reload', disabled: true,
      onclick: () => this._resetStorage() });
    this.root.append(el('section', { class: 'card' }, [
      el('h3', { text: 'Saved in this browser' }),
      el('table', { class: 'settings-table' }, [this.storageBody]),
      this.storageTotal,
      el('div', { class: 'row settings-actions' }, [this.resetButton]),
    ]));

    this.serverCard = el('section', { class: 'card settings-server' });
    this.root.append(this.serverCard);
  }

  /** Whether the last reload was one this tab asked for, with a note to show. */
  hasNote() {
    try { return Boolean(browserStore('sessionStorage')?.getItem(NOTE_KEY)); } catch { return false; }
  }

  _note(text) {
    try { browserStore('sessionStorage')?.setItem(NOTE_KEY, `${text} (${new Date().toLocaleTimeString('en-US')})`); } catch { /* fine */ }
  }

  _takeNote() {
    const store = browserStore('sessionStorage');
    try {
      const note = store?.getItem(NOTE_KEY);
      store?.removeItem(NOTE_KEY);
      return note || '';
    } catch {
      return '';
    }
  }

  async _refreshBuild() {
    let server = null;
    try { server = await this.api.build(); } catch { /* down, or restarting */ }
    this.server = server;
    const check = buildCheck(this.assets, server);
    this.buildText.textContent = check.text;
    this.buildText.dataset.state = check.state;
  }

  async _copyDetails() {
    const lines = [
      `page build: ${this.assets || 'unknown'}`,
      `server build: ${this.server ? `${this.server.version} · ${this.server.assets} · instance ${this.server.instance}` : 'no answer'}`,
      `browser: ${navigator.userAgent}`,
      `window: ${window.innerWidth}×${window.innerHeight} @${window.devicePixelRatio}`,
      `time: ${new Date().toISOString()}`,
    ];
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      this.buildText.textContent = `${buildCheck(this.assets, this.server).text} Details copied.`;
    } catch {
      window.prompt('Copy these details:', lines.join(' | '));
    }
  }

  async _clearBrowserCache() {
    this.beforeReload();
    try {
      await this.api.clearBrowserCache();
      this._note('Cached files were cleared.');
    } catch (error) {
      this._note(`Cached files could not be cleared (${error.message}); the page was reloaded.`);
    }
    window.location.reload();
  }

  _stores() {
    return { local: browserStore('localStorage'), session: browserStore('sessionStorage') };
  }

  _refreshStorage() {
    this.rows = inventory(this._stores());
    this.chosen = new Set();
    this.storageBody.replaceChildren(...this.rows.map((row) => {
      const box = row.keep
        ? el('span', { class: 'muted', title: 'Kept: not offered for a reset', text: '—' })
        : el('input', { type: 'checkbox', 'aria-label': `Reset ${row.label}`,
          onchange: (e) => {
            if (e.target.checked) this.chosen.add(row.id); else this.chosen.delete(row.id);
            this.resetButton.disabled = this.chosen.size === 0;
          } });
      const where = [...new Set(row.items.map((i) => (i.area === 'local' ? 'all tabs' : 'this tab')))].join(', ');
      return el('tr', {}, [
        el('td', {}, [box]),
        el('td', {}, [el('div', { text: row.label }), el('div', { class: 'muted', text: row.help })]),
        el('td', { class: 'num', text: `${row.items.length} · ${formatBytes(row.bytes)}`,
          title: row.items.map((i) => i.key).join('\n') }),
        el('td', { class: 'muted settings-where', text: where }),
      ]);
    }));
    if (!this.rows.length) this.storageBody.append(el('tr', {}, [el('td', { colspan: 4, class: 'muted', text: 'Nothing is saved.' })]));
    const total = this.rows.reduce((sum, row) => sum + row.bytes, 0);
    this.storageTotal.textContent = `${formatBytes(total)} in all. A reset removes the ticked items from every tab of this site, then reloads.`;
    this.resetButton.disabled = true;
  }

  _resetStorage() {
    const names = this.rows.filter((r) => this.chosen.has(r.id)).map((r) => r.label);
    if (!names.length) return;
    if (!window.confirm(`Reset ${names.join(', ')}? This removes them from every tab of this site, and reloads.`)) return;
    // The page saves its session on the way out; a reset session must stay reset.
    if (this.chosen.has('session')) this.stopSaving();
    else this.beforeReload();
    // Read again now: the page saves its session moments after the tab opens
    // (and on every change), after the list on screen was made.
    const stores = this._stores();
    const removed = resetGroups(inventory(stores), this.chosen, stores);
    this._note(`Reset ${names.join(', ')}: ${removed} item${removed === 1 ? '' : 's'} removed.`);
    window.location.reload();
  }

  async _refreshServer() {
    const card = this.serverCard;
    let me;
    try {
      me = await this.api.adminWhoami();
    } catch (error) {
      card.replaceChildren(el('h3', { text: 'This server' }), el('p', { class: 'settings-text', text: `No answer: ${error.message}` }));
      return;
    }
    if (!me.admin) {
      card.replaceChildren(el('h3', { text: 'This server' }), el('p', { class: 'settings-text muted', text:
        `The server's tools (clear its cached renders, replace its build processes, restart it) need an admin account. `
        + `You are signed in as ${me.user || 'nobody'}; the server names its admins in ${me.admin_setting}.` }));
      return;
    }
    let status;
    try {
      status = await this.api.adminStatus();
    } catch (error) {
      card.replaceChildren(el('h3', { text: 'This server' }), el('p', { class: 'settings-text', text: `No answer: ${error.message}` }));
      return;
    }
    this.status = status;
    this.serverText = el('p', { class: 'settings-text', role: 'status' });
    const restartHelp = status.supervised
      ? 'Down for about 10 s for everyone; every open page is asked to reload and gets its session back. Builds running now are lost.'
      : 'This server was not started by systemd, so it would not come back: restart it where it was started.';
    card.replaceChildren(
      el('h3', { text: 'This server' }),
      el('table', { class: 'settings-table' }, [el('tbody', {}, serverRows(status).map(([name, value]) =>
        el('tr', {}, [el('th', { text: name }), el('td', { text: value })])))]),
      el('div', { class: 'row settings-actions' }, [
        el('button', { type: 'button', text: 'Refresh', onclick: () => this._refreshServer() }),
        el('button', { type: 'button', text: 'Clear surfaces in memory',
          title: 'Every surface held in memory is built again on its next request, from the disk cache where it is there',
          onclick: () => this._clearRenders(false) }),
        el('button', { type: 'button', class: 'danger', text: 'Clear surfaces in memory and on disk',
          title: 'Every surface is built from scratch on its next request, for everyone using this server',
          onclick: () => this._clearRenders(true) }),
        el('button', { type: 'button', text: 'Replace build processes', disabled: !status.pool?.build_processes,
          title: 'Builds running now finish in the old processes; the next one starts in new ones, which hold nothing',
          onclick: () => this._recycle() }),
        el('button', { type: 'button', class: 'danger', text: 'Restart the server', disabled: !status.supervised,
          title: restartHelp, onclick: () => this._restart() }),
      ]),
      el('p', { class: 'settings-text muted', text: restartHelp }),
      this.serverText,
    );
  }

  async _clearRenders(disk) {
    const files = this.status?.disk_cache?.files ?? 0;
    const ask = disk
      ? `Delete every cached surface, in memory and the ${files.toLocaleString('en-US')} files on disk? `
        + 'Every cell is built from scratch on its next request, for everyone using this server.'
      : 'Forget the surfaces held in memory? Each is read again from the disk cache, or built, on its next request.';
    if (!window.confirm(ask)) return;
    try {
      const done = await this.api.adminClearRenders(disk);
      await this._refreshServer();
      this.serverText.textContent = `Cleared ${done.memory_meshes} surface${done.memory_meshes === 1 ? '' : 's'} in memory`
        + (disk ? ` and ${done.disk_files.toLocaleString('en-US')} files (${formatBytes(done.disk_bytes)}) on disk.` : '.');
    } catch (error) {
      this.serverText.textContent = `Not cleared: ${error.message}`;
    }
  }

  async _recycle() {
    if (!window.confirm('Replace the build processes? Builds running now finish first in the old ones.')) return;
    try {
      const done = await this.api.adminRecycleWorkers();
      await this._refreshServer();
      this.serverText.textContent = done.recycled
        ? 'The build processes were replaced; the old ones exit when their builds are done.'
        : 'There were no build processes to replace (none has been started yet).';
    } catch (error) {
      this.serverText.textContent = `Not replaced: ${error.message}`;
    }
  }

  async _restart() {
    if (!window.confirm('Restart the server for everyone? It is down for about 10 s, every open page is asked '
      + 'to reload, and builds running now are lost.')) return;
    const before = this.status?.instance;
    try {
      await this.api.adminRestart();
    } catch (error) {
      this.serverText.textContent = `Not restarted: ${error.message}`;
      return;
    }
    this.beforeReload();
    const started = Date.now();
    for (;;) {
      const elapsed = (Date.now() - started) / 1000;
      let answer = null;
      try { answer = await this.api.build(); } catch { /* down */ }
      const phase = restartPhase(before, answer, elapsed);
      if (phase === 'back') {
        this._note(`The server restarted in ${Math.round(elapsed)} s.`);
        window.location.reload();
        return;
      }
      if (phase === 'timeout') {
        this.serverText.textContent = `The server has not come back after ${RESTART_WAIT_S} s: deploy/status.sh on the server says why.`;
        return;
      }
      this.serverText.textContent = phase === 'down'
        ? `Restarting: the server is down (${Math.round(elapsed)} s)…`
        : `Restarting: the server is finishing its requests (${Math.round(elapsed)} s)…`;
      await new Promise((resolve) => { setTimeout(resolve, 1000); });
    }
  }
}
