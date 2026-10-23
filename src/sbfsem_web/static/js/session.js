// Session persistence.
//
// The server holds no per-user state, and a redeploy restarts it with an empty
// neuron table. Everything needed to put a researcher back where they were --
// which neurons, in what colors, with which render settings, and where the
// camera was pointing -- is therefore kept in the browser, which survives both
// a server restart and a page reload.
//
// Restoring is cheap because the annotations are already on the server's disk
// cache: a reload that took 14s from Viking takes ~0.1s from cache.
//
// Every browser tab keeps its own session (2026-09-17). With one shared slot,
// two tabs on different volumes overwrote each other, and whichever tab
// reloaded came back as the other one -- "I can't have multiple tabs open for
// different volumes" (Crystal). A tab's slot is named by an id in
// sessionStorage, which survives a reload but not closing the tab; the shared
// slot is still written, so a brand-new tab starts from whatever was saved
// last, as before.

const KEY = 'sbfsem.session.v1';
const TAB_PREFIX = `${KEY}.tab.`;
const TAB_ID_KEY = 'sbfsem.tab';
const SCHEMA = 1;
// Long enough to coalesce a color-picker drag, short enough that a crash
// loses nothing anyone would notice.
const WRITE_DELAY_MS = 400;
// A closed tab's slot cannot be told from an open one's, so old slots are
// swept by age instead.
const TAB_SLOT_TTL_MS = 14 * 24 * 3600 * 1000;

let timer = null;
// Set by stopSaving: a page that has reset the saved session must not write it back.
let stopped = false;
let tabIdCache = null;
let channel = null;
// The cells a page load found saved and was not yet told to restore or
// discard (2026-09-24): written back with every save, so a tab closed before
// deciding loses nothing.
let pending = null;
// Set before a reload the app itself offers (after a redeploy): that load
// restores without asking, as the banner promised.
const AUTO_KEY = 'sbfsem.restore.auto';

function storage() {
  // Private windows and blocked site-data make even the accessor throw.
  try {
    const s = window.localStorage;
    s.getItem(KEY);
    return s;
  } catch {
    return null;
  }
}

function newTabId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** This tab's id: kept in sessionStorage so a reload finds its own slot. */
function tabId() {
  if (tabIdCache) return tabIdCache;
  let id = null;
  try {
    id = window.sessionStorage.getItem(TAB_ID_KEY);
    if (!id) {
      id = newTabId();
      window.sessionStorage.setItem(TAB_ID_KEY, id);
    }
  } catch {
    // sessionStorage blocked: the id lives for this page load only.
    id = id || newTabId();
  }
  tabIdCache = id;
  claimTab(id);
  return id;
}

/**
 * "Duplicate tab" copies sessionStorage, so two live tabs can share an id and
 * would then share a slot again. Each tab announces its id; a tab that hears
 * its own id from someone else takes a fresh one. The duplicate started from
 * the same state anyway, so nothing is lost either way.
 */
function claimTab(id) {
  if (channel || typeof BroadcastChannel === 'undefined') return;
  try {
    channel = new BroadcastChannel('sbfsem.tabs');
    channel.addEventListener('message', (event) => {
      const msg = event.data || {};
      if (msg.id !== tabIdCache) return;
      if (msg.type === 'claim') {
        channel.postMessage({ type: 'taken', id: tabIdCache });
      } else if (msg.type === 'taken') {
        tabIdCache = newTabId();
        try { window.sessionStorage.setItem(TAB_ID_KEY, tabIdCache); } catch { /* fine */ }
      }
    });
    channel.postMessage({ type: 'claim', id });
  } catch { /* no cross-tab messaging: slots may collide on a duplicated tab */ }
}

const tabKey = () => `${TAB_PREFIX}${tabId()}`;

// What one page keeps, its undo history included, and never stores: Display's
// Hold (`hold_limits`).  Saved with the session, a Hold ticked once came back on
// every later load and left each new cell off screen, with nothing on the page to
// say why (Temporal c162392, 2026-10-22).  A saved view needs no Hold: a restore
// puts its camera back either way.
const PAGE_ONLY_DISPLAY = ['hold_limits'];

/**
 * `snap` without what only the page that set it keeps: what this browser stores,
 * what a workspace document carries, and what a restore of either reads (one
 * stored before 2.30.2 can still name Hold).  `snap` itself is not changed.
 */
export function withoutPageState(snap) {
  const display = snap?.ui?.display;
  if (!display || typeof display !== 'object' || !PAGE_ONLY_DISPLAY.some((k) => k in display)) return snap;
  const kept = { ...display };
  for (const key of PAGE_ONLY_DISPLAY) delete kept[key];
  return { ...snap, ui: { ...snap.ui, display: kept } };
}

/** Serialize live state into a plain object (Hold included: the undo history is the page's). */
export function snapshot(state, renderer, ui = {}) {
  return {
    schema: SCHEMA,
    saved_at: new Date().toISOString(),
    source: state.source,
    view: state.view,
    selected: state.selected,
    neurons: [...state.neurons.values()].map((r) => ({
      id: r.id,
      source: r.source,
      color: r.color,
      visible: r.visible,
      opacity: r.opacity,
      render: r.render,
      display: r.display,
      corrections: r.corrections,
      correctionMode: r.correctionMode,
      registration: r.registration,
      ...(r.manualMoves && Object.keys(r.manualMoves).length ? { manualMoves: r.manualMoves } : {}),
    })),
    camera: renderer ? renderer.cameraState() : null,
    ui,
  };
}

/** Keep these saved cells (or none) in every save until the user decides. */
export function setPending(neurons) {
  pending = neurons && neurons.length ? neurons : null;
}

/** The next page load in this tab restores its session without asking. */
export function restoreNextLoad() {
  try { window.sessionStorage.setItem(AUTO_KEY, '1'); } catch { /* it asks instead */ }
}

/** Was this page load one the app offered?  Asked once: the answer is spent. */
export function takeRestoreNextLoad() {
  try {
    const auto = window.sessionStorage.getItem(AUTO_KEY) === '1';
    window.sessionStorage.removeItem(AUTO_KEY);
    return auto;
  } catch {
    return false;
  }
}

function write(store, state, renderer, ui) {
  const snap = withoutPageState(snapshot(state, renderer, ui));
  if (pending) {
    const have = new Set(snap.neurons.map((n) => `${n.source}:${n.id}`));
    snap.neurons.push(...pending.filter((n) => !have.has(`${n.source}:${n.id}`)));
  }
  const text = JSON.stringify(snap);
  // This tab's own slot, and the shared "last session" a new tab starts from.
  store.setItem(tabKey(), text);
  store.setItem(KEY, text);
}

export function save(state, renderer, ui) {
  const store = storage();
  if (!store || stopped) return;
  clearTimeout(timer);
  timer = setTimeout(() => {
    try {
      write(store, state, renderer, ui);
    } catch (error) {
      // Quota exceeded, or storage disabled mid-session. A lost session is an
      // inconvenience; a crashed UI is not acceptable.
      console.warn('session not saved:', error);
    }
  }, WRITE_DELAY_MS);
}

/** No more writes from this page: the Settings tab reset the saved session and reloads. */
export function stopSaving() {
  stopped = true;
  clearTimeout(timer);
}

/** Write immediately, for beforeunload and for the pre-redeploy hook. */
export function saveNow(state, renderer, ui) {
  const store = storage();
  if (!store || stopped) return;
  clearTimeout(timer);
  try {
    write(store, state, renderer, ui);
  } catch (error) {
    console.warn('session not saved:', error);
  }
}

function parse(raw) {
  if (!raw) return null;
  try {
    const data = JSON.parse(raw);
    // A schema bump means the shape changed; a stale session is worse than
    // none, because it half-restores and looks like a bug.
    if (!data || data.schema !== SCHEMA) return null;
    if (!Array.isArray(data.neurons)) return null;
    // A session stored before Hold stayed with its page still names it.
    return withoutPageState(data);
  } catch (error) {
    console.warn('discarding unreadable session:', error);
    return null;
  }
}

/** Drop tab slots nobody has written to in a fortnight. */
function sweep(store) {
  const now = Date.now();
  const mine = tabKey();
  const stale = [];
  try {
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i);
      if (!key || !key.startsWith(TAB_PREFIX) || key === mine) continue;
      let savedAt = 0;
      try { savedAt = Date.parse(JSON.parse(store.getItem(key))?.saved_at) || 0; } catch { /* unreadable: stale */ }
      if (now - savedAt > TAB_SLOT_TTL_MS) stale.push(key);
    }
    for (const key of stale) store.removeItem(key);
  } catch { /* a sweep that fails costs a few kilobytes */ }
}

/**
 * The session to restore: this tab's own after a reload, else the last one
 * any tab saved (a new tab, or the first visit after the per-tab slots).
 */
export function load() {
  const store = storage();
  if (!store) return null;
  sweep(store);
  let raw;
  try {
    raw = store.getItem(tabKey());
    if (!raw) raw = store.getItem(KEY);
  } catch {
    return null;
  }
  return parse(raw);
}
