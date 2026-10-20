// An in-page list for the browser's dropdowns.
//
// On Windows and Linux, Chrome draws a `<select>`'s option list as a separate
// popup window.  Inside some virtual machines (reported 2026-10-15: Chrome in
// a Windows 11 ARM64 guest under Parallels on a Mac) that window does not
// always draw and does not take clicks, so the list cannot be used.  macOS and
// Android use the system menu instead and never show the fault.
//
// This replaces ONLY that popup.  The `<select>` is not wrapped, hidden or
// rebuilt: it stays the control the user sees, the element that has focus and
// the source of truth for `value`, `options`, `disabled` and its events, so
// the ~40 places that write `select.value`, the CSS keyed on `select` and
// every `change` listener are untouched.  What changes is what a mouse press
// or an opening key does: it is caught in the capture phase before the
// browser acts, and a list of rows built from `select.options` at that moment
// opens in the page instead (the same delegated shape as colorpicker.js, so
// it covers selects that are created later).  A pick sets `selectedIndex` and
// fires `input` then `change`, the order the browser's own list uses.
//
// On by default where Chrome uses a popup window (Windows, Linux, ChromeOS,
// Chromium browsers, a fine pointer); off on macOS, touch, Firefox and
// Safari.  The "In-page dropdowns" checkbox in Display overrides it.  Any
// failure while building the list leaves the browser's own list to open.
//
// Everything above `installSelectPicker` is pure: no `document`, `window` or
// `navigator` is read at import, so tests/js/test_selectpicker.mjs runs it
// under node.

export const STORAGE_KEY = 'sbfsem:select-lists';
const TYPE_MS = 700;           // our own type-ahead buffer while the list is open
const CHROME_TYPE_MS = 1000;   // Chrome's type-ahead session, which Space respects
const NBSP = ' ';       // an empty label still takes a row's height

// -- pure helpers -----------------------------------------------------------

/** A row the keyboard and the pointer may land on. */
export const isSelectable = (row) => !!row && !row.heading && !row.disabled;

/** Index of the next selectable row after `from` in direction `step`, or `from`. */
export function stepIndex(rows, from, step) {
  const dir = step < 0 ? -1 : 1;
  for (let i = from + dir; i >= 0 && i < rows.length; i += dir) {
    if (isSelectable(rows[i])) return i;
  }
  return from;
}

/** The first or last selectable row, or -1 when there is none. */
export function edgeIndex(rows, end) {
  const order = rows.map((_, i) => i);
  if (end === 'last') order.reverse();
  return order.find((i) => isSelectable(rows[i])) ?? -1;
}

/**
 * PageUp / PageDown: `count` rows from `from`, landing on a selectable row.
 * Past the end it takes the nearest selectable row on the way back.
 */
export function pageIndex(rows, from, dir, count) {
  if (!rows.length) return from;
  const d = dir < 0 ? -1 : 1;
  const target = Math.min(rows.length - 1, Math.max(0, from + d * Math.max(1, count)));
  for (let i = target; i >= 0 && i < rows.length; i += d) {
    if (isSelectable(rows[i])) return i;
  }
  for (let i = target - d; i !== from && i >= 0 && i < rows.length; i -= d) {
    if (isSelectable(rows[i])) return i;
  }
  return from;
}

/**
 * Type-ahead: the row to move to for the text typed so far.  One letter (or
 * the same letter repeated) steps through the rows that start with it, after
 * `from`; a longer prefix is looked for from `from` itself, so the row you are
 * on stays while it still matches.  Case-insensitive; wraps; -1 for no match.
 */
export function typeaheadIndex(rows, from, typed) {
  const t = String(typed).toLowerCase();
  if (!t) return -1;
  const same = [...t].every((c) => c === t[0]);
  const needle = same ? t[0] : t;
  const n = rows.length;
  for (let k = 1; k <= n; k += 1) {
    const i = (((from + (same ? k : k - 1)) % n) + n) % n;
    const row = rows[i];
    if (isSelectable(row) && String(row.label).trimStart().toLowerCase().startsWith(needle)) return i;
  }
  return -1;
}

/** The type-ahead buffer after `key` at time `now` (ms): it restarts after `timeout` of silence. */
export function nextTyped(state, key, now, timeout = TYPE_MS) {
  const fresh = now - state.at > timeout;
  return { typed: (fresh ? '' : state.typed) + key, at: now };
}

/**
 * Where the list goes.  `anchor` and `vp` are {top, left, right, bottom, width,
 * height} rectangles in CSS px; `want` is the list's natural height.  Below the
 * control when it fits, else above when there is more room there; capped at
 * `cap` px and never shorter than `floor` (or `want`, if smaller); the left
 * edge sits on the control's and is pulled `margin` px inside the viewport.
 * `width` is the list's own width.
 */
export function placement(anchor, vp, want, {
  gap = 2, margin = 8, cap = 360, floor = 96, width = anchor.width,
} = {}) {
  const below = vp.height - anchor.bottom - gap - margin;
  const above = anchor.top - gap - margin;
  const fits = Math.min(want, cap);
  const flip = below < fits && above > below;
  const room = flip ? above : below;
  const height = Math.max(Math.min(fits, room), Math.min(want, floor));
  const raw = flip ? anchor.top - gap - height : anchor.bottom + gap;
  const top = Math.max(margin, Math.min(raw, vp.height - margin - height));
  const left = Math.max(margin, Math.min(anchor.left, vp.width - width - margin));
  return { flip, top, left, height, scroll: want > height };
}

/**
 * The scrollTop that brings a row (offsets within the list's content) into a
 * view `viewHeight` tall.  `align: 'center'` is for the first reveal.
 */
export function revealScroll(rowTop, rowBottom, scrollTop, viewHeight, align = 'nearest') {
  if (align === 'center') return Math.max(0, rowTop - (viewHeight - (rowBottom - rowTop)) / 2);
  if (rowTop < scrollTop) return rowTop;
  if (rowBottom > scrollTop + viewHeight) return rowBottom - viewHeight;
  return scrollTop;
}

// -- where it is on, and which keys open it ---------------------------------

/** Platforms where Chrome draws a select's list as a separate popup window. */
export const popupWindowPlatform = (platform) => {
  const p = String(platform || '');
  return !/android/i.test(p) && /^(win|linux|chrome ?os|cros)/i.test(p);
};

/**
 * Is this a Chromium browser?  ``navigator.userAgentData`` exists only in a
 * secure context, so a page opened over plain http (a dev server reached by
 * IP from a virtual machine, which is where the native list fails) has none:
 * the user-agent string answers then.  Safari and Firefox do not carry one of
 * these tokens; Edge and Opera say Edg/ and OPR/.
 */
export function isChromium(data, userAgent) {
  if (data && Array.isArray(data.brands)) return data.brands.some((b) => /chromium/i.test(b.brand));
  return /\b(Chrome|Chromium|Edg|OPR)\//.test(String(userAgent || ''));
}

export const macPlatform = (platform) => /^(mac|iphone|ipad)/i.test(String(platform || ''));

/**
 * 'page' or 'native'.  Not a Chromium browser: native (Firefox and Safari keep
 * their own lists).  Otherwise the person's choice, if there is one; else the
 * in-page list wherever Chrome would use a popup window and a mouse or pen
 * is attached.
 */
export function resolveMode({ stored, chromium, platform, anyFine }) {
  if (!chromium) return 'native';
  if (stored === 'page' || stored === 'native') return stored;
  return anyFine && popupWindowPlatform(platform) ? 'page' : 'native';
}

/**
 * Does this key press, on a focused and closed `<select>`, open Chrome's own
 * list?  Exactly those are cancelled and open ours instead, so no second,
 * native list appears and no other key changes meaning.  From Chromium's
 * HTMLSelectElement (ShouldOpenPopupForKeyDownEvent / ...KeyPressEvent) and
 * LayoutTheme: Alt+ArrowUp/Down and F4 (no Alt, no Ctrl) everywhere; Space
 * unless a type-ahead session is running (`typing`); Enter only where the
 * theme pops by the return key (not on macOS); ArrowUp/Down alone only on
 * macOS, where they open the menu -- on Windows and Linux they step the value
 * in place, which must stay as it is.
 */
export function opensList(event, { mac = false, typing = false } = {}) {
  const { key, altKey = false, ctrlKey = false, metaKey = false } = event;
  if (key === 'ArrowDown' || key === 'ArrowUp') return altKey || mac;
  if (key === 'F4') return !altKey && !ctrlKey;
  if (altKey || ctrlKey || metaKey) return false;
  if (key === ' ') return !typing;
  if (key === 'Enter') return !mac;
  return false;
}

// -- rows -------------------------------------------------------------------

/**
 * The rows of a select, read from its options at this moment: {label, value,
 * index (in select.options), title, disabled}, with optgroups as heading rows
 * ({heading: true, label}).  Hidden options and groups are skipped.
 */
export function rowsOf(select) {
  const rows = [];
  // Counted rather than read from `option.index`, which walks the select's whole
  // list on every call: 25 ms for 2,000 options against 2 ms.  `select.options`
  // is these same elements in this same order, hidden ones included.
  let at = 0;
  const add = (option, group) => {
    const index = at++;
    if (option.hidden) return;
    rows.push({
      label: option.label || option.textContent || NBSP,
      value: option.value,
      index,
      title: option.title || '',
      disabled: !!option.disabled || !!group?.disabled,
    });
  };
  for (const child of select.children) {
    if (child.tagName === 'OPTGROUP') {
      if (child.hidden) { at += child.children.length; continue; }
      rows.push({ label: child.label || NBSP, heading: true, disabled: true });
      for (const option of child.children) add(option, child);
    } else if (child.tagName === 'OPTION') {
      add(child, null);
    }
  }
  return rows;
}

/** One string that changes when the options, their text, their state or the selection do. */
export function optionSignature(select) {
  return `${select.selectedIndex}|${Array.from(select.options, (o) => [
    o.value, o.label || o.textContent, o.disabled ? 1 : 0, o.hidden ? 1 : 0, o.title,
  ].join('\u0001')).join('\u0002')}`;
}

/**
 * Pick a row: set `selectedIndex`, then fire `input` and `change`, once each,
 * in the order and with the bubbling the browser's own list uses (18 render
 * controls listen to `input`, the rest to `change`).  Nothing happens, and
 * false comes back, when the row is already the selection or the options were
 * replaced while the list was open (the row's index no longer holds its value).
 */
export function commitSelection(select, row) {
  if (!row || row.heading || row.disabled) return false;
  if (select.options[row.index]?.value !== row.value) return false;
  if (select.selectedIndex === row.index) return false;
  select.selectedIndex = row.index;
  select.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  select.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

// -- the DOM half -----------------------------------------------------------
// Nothing below runs at import; `installSelectPicker()` adds the listeners.

// A press on one of these keeps its normal meaning when it dismisses a list;
// on anything else the press is swallowed, so dismissing never picks or
// deselects a cell in the 3D view behind it.
const INTERACTIVE = 'a, button, input, select, textarea, summary, label, '
  + '[role="button"], [contenteditable]';
const PAD_PX = 3;                // the list's padding (app.css), kept in view around a row

let installed = false;
let env = { chromium: false, platform: '', anyFine: false };
let mode = null;                 // 'page' | 'native', cached
let pointerType = 'mouse';       // of the last press: touch keeps the system picker
let lastPrintable = { el: null, at: -Infinity }; // for Space: Chrome's type-ahead session, one per select
let seq = 0;
let open = null;                 // the list on screen, if any

function environment() {
  const data = navigator.userAgentData;
  let anyFine = false;
  try { anyFine = window.matchMedia('(any-pointer: fine)').matches; } catch { /* no media queries */ }
  return {
    chromium: isChromium(data, navigator.userAgent),
    platform: data?.platform || navigator.platform || '',
    anyFine,
  };
}

function storedMode() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'page' || value === 'native' ? value : null;
  } catch { return null; }
}

function currentMode() {
  if (mode === null) {
    env = environment();
    mode = resolveMode({ stored: storedMode(), ...env });
  }
  return mode;
}

/** Re-read the browser and the stored choice (a media or storage change). */
function refreshMode() {
  mode = null;
  currentMode();
  syncBox();
  if (mode !== 'page') close();
}

function setMode(next) {
  try { localStorage.setItem(STORAGE_KEY, next); } catch { /* private mode: lasts until reload */ }
  mode = env.chromium ? next : 'native';
  if (mode !== 'page') close();
}

function syncBox() {
  const box = document.getElementById('opt-page-selects');
  if (box) box.checked = mode === 'page';
}

function bindBox() {
  const box = document.getElementById('opt-page-selects');
  if (!box) return;
  // Firefox and Safari keep their own lists: nothing to switch.
  const label = box.closest('label');
  if (label) label.hidden = !env.chromium;
  syncBox();
  box.addEventListener('change', () => setMode(box.checked ? 'page' : 'native'));
}

/** Would we open a list for this select, right now? */
function eligible(select) {
  return currentMode() === 'page'
    && select instanceof HTMLSelectElement
    && !select.matches(':disabled')
    && !select.multiple
    && select.size <= 1;
}

const el = (tag, className) => Object.assign(document.createElement(tag), { className });

/** The list's accessible name: the select's own, else its label's own text (not its options'). */
function nameOf(select) {
  const own = select.getAttribute('aria-label');
  if (own) return own;
  const label = select.labels?.[0];
  const text = label
    ? [...label.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent)
      .join(' ').replace(/\s+/g, ' ').trim()
    : '';
  return text || select.title || 'Options';
}

/** Fill the list with one node per row; returns them in row order. */
function fillRows(pop, rows, select, id) {
  const nodes = rows.map((r, i) => {
    const node = el('div', r.heading ? 'sel-head' : 'sel-opt');
    node.id = `${id}-${i}`;
    node.textContent = r.label;      // text only: cell names appear in some lists
    if (r.heading) {
      node.setAttribute('role', 'presentation');
    } else {
      node.setAttribute('role', 'option');
      node.setAttribute('aria-selected', String(r.index === select.selectedIndex));
      if (r.disabled) node.setAttribute('aria-disabled', 'true');
      // Not `title`: that opens a native tooltip window, the thing being avoided.
      if (r.title) node.dataset.help = r.title;
      node.dataset.i = String(i);
    }
    return node;
  });
  pop.replaceChildren(...nodes);
  return nodes;
}

/**
 * The list's nodes for a select, not yet in the page.  Null when there is
 * nothing to show.  Built before the browser's own list is cancelled, so a
 * throw here leaves that list to open.
 */
function build(select) {
  const rows = rowsOf(select);
  if (!rows.some((r) => !r.heading)) return null;
  const id = `sel-pop-${++seq}`;
  const pop = el('div', 'sel-pop');
  pop.id = id;
  pop.setAttribute('role', 'listbox');
  pop.setAttribute('aria-label', nameOf(select));
  // A sibling, not a child: a listbox may only contain options.
  const live = el('div', 'sel-live');
  live.setAttribute('role', 'status');
  const list = { select, rows, pop, live, id, nodes: fillRows(pop, rows, select, id) };

  // The select keeps focus: pressing in the list must not move it.
  pop.addEventListener('mousedown', (event) => event.preventDefault());
  // Never reaches the More menu's click-outside closer, whatever was pressed.
  pop.addEventListener('click', (event) => event.stopPropagation());
  pop.addEventListener('contextmenu', (event) => event.preventDefault());
  // `pointermove`, not `pointerover`: a list scrolling under a still pointer
  // must not take the highlight from the keyboard.
  pop.addEventListener('pointermove', (event) => {
    const node = event.target instanceof Element ? event.target.closest('.sel-opt') : null;
    if (!node || node.hasAttribute('aria-disabled') || !open || open.pop !== pop) return;
    open.hovered = node;
    setActive(Number(node.dataset.i), false);
  });
  // Press on the select, drag to a row, release: as the browser's list does.
  // A press that began on the scrollbar or the padding and drifted onto a row picks nothing.
  pop.addEventListener('pointerup', (event) => {
    const node = event.target instanceof Element ? event.target.closest('.sel-opt') : null;
    if (event.button !== 0 || !node || node.hasAttribute('aria-disabled')) return;
    if (!open || open.pop !== pop || !open.armed) return;
    choose(Number(node.dataset.i), true);
  });
  return list;
}

/** Size and place the list from the select's rectangle now. */
function position(list) {
  const { select, pop } = list;
  const anchor = select.getBoundingClientRect();
  pop.style.minWidth = `${anchor.width}px`;
  pop.style.left = '0px';
  pop.style.top = '0px';
  pop.style.height = '';
  const want = pop.scrollHeight + 2;           // + the two borders
  const width = pop.getBoundingClientRect().width;
  const root = document.documentElement;
  const spot = placement(anchor, { width: root.clientWidth, height: root.clientHeight }, want, { width });
  pop.style.top = `${spot.top}px`;
  pop.style.left = `${spot.left}px`;
  pop.style.height = `${spot.height}px`;
  list.rect = anchor;
}

function reveal(node, align = 'nearest') {
  const { pop } = open;
  const top = node.offsetTop;
  const bottom = top + node.offsetHeight;
  pop.scrollTop = align === 'center'
    ? revealScroll(top, bottom, pop.scrollTop, pop.clientHeight, 'center')
    : revealScroll(top - PAD_PX, bottom + PAD_PX, pop.scrollTop, pop.clientHeight);
}

function setActive(i, scroll) {
  const list = open;
  if (!list || !isSelectable(list.rows[i])) return;
  list.nodes[list.active]?.classList.remove('active');
  const node = list.nodes[i];
  node.classList.add('active');
  list.active = i;
  list.select.setAttribute('aria-activedescendant', node.id);
  const options = list.rows.filter((r) => !r.heading);
  const place = list.rows.slice(0, i + 1).filter((r) => !r.heading).length;
  list.live.textContent = `${list.rows[i].label}, ${place} of ${options.length}`;
  if (scroll) reveal(node, scroll === 'center' ? 'center' : 'nearest');
}

/** Row to highlight when the list opens: the selection, else the first row that can be picked. */
function startRow(list) {
  const at = list.rows.findIndex((r) => r.index === list.select.selectedIndex && isSelectable(r));
  return at >= 0 ? at : edgeIndex(list.rows, 'first');
}

function present(list, armed = false) {
  close();
  // `armed`: a release over a row picks it.  True when the press that opened the list is
  // still down (press, drag to a row, release); else only a press that began on a row arms it.
  open = Object.assign(list, {
    armed, active: -1, typed: '', typedAt: -Infinity, hovered: null, raf: 0, rect: null,
    sig: optionSignature(list.select),
  });
  const { select, pop, live } = list;
  // Inside the select's own modal <dialog> when it has one: a modal makes the
  // rest of the page inert.  No Popover API: its top layer would paint over
  // the help panel, which shows an option's description beside the list.
  (select.closest('dialog') || document.body).append(pop, live);
  const style = getComputedStyle(select);
  pop.style.fontSize = style.fontSize;
  pop.style.fontFamily = style.fontFamily;
  position(list);
  select.setAttribute('aria-expanded', 'true');
  select.setAttribute('aria-controls', list.id);
  list.focused = document.activeElement === select;
  setActive(startRow(list), 'center');
  list.raf = requestAnimationFrame(watch);
}

/** Runs each frame while a list is open: close or rebuild it when the page changes under it. */
function watch() {
  const list = open;
  if (!list) return;
  const { select } = list;
  const r = select.getBoundingClientRect();
  const moved = Math.abs(r.top - list.rect.top) + Math.abs(r.left - list.rect.left) > 1;
  if (!select.isConnected || select.matches(':disabled') || !r.width || !r.height || moved
      || (list.focused && document.activeElement !== select)) {
    close();
    return;
  }
  const sig = optionSignature(select);
  if (sig !== list.sig) rebuild(sig);
  if (open === list) list.raf = requestAnimationFrame(watch);
}

/** The options changed while the list was open: redraw it, keeping the highlight by value. */
function rebuild(sig) {
  const list = open;
  const { select, pop } = list;
  const rows = rowsOf(select);
  if (!rows.some((r) => !r.heading)) { close(); return; }
  const kept = list.rows[list.active]?.value;
  const scrollTop = pop.scrollTop;
  list.rows = rows;
  list.sig = sig;
  list.hovered = null;
  list.nodes = fillRows(pop, rows, select, list.id);
  position(list);
  pop.scrollTop = scrollTop;
  list.active = -1;
  let at = rows.findIndex((r) => isSelectable(r) && r.value === kept);
  if (at < 0) at = startRow(list);
  setActive(at, 'nearest');
}

function close() {
  const list = open;
  if (!list) return;
  open = null;
  cancelAnimationFrame(list.raf);
  const { select, pop, live, hovered } = list;
  // The row under the pointer is about to vanish without a `pointerout`; tell
  // the help panel (app.js bindHelp) the pointer left, or its text stays.
  if (hovered?.dataset.help && typeof PointerEvent === 'function') {
    hovered.dispatchEvent(new PointerEvent('pointerout', { bubbles: true }));
  }
  select.removeAttribute('aria-expanded');
  select.removeAttribute('aria-controls');
  select.removeAttribute('aria-activedescendant');
  pop.remove();
  live.remove();
}

/**
 * Pick row `i`: close the list, give the select its focus back, then commit.
 * In that order because a handler of `change` may rebuild the panel, even
 * remove the select (Render compare does).
 */
function choose(i, fromPointer = false) {
  const list = open;
  if (!list || !isSelectable(list.rows[i])) return;
  const { select } = list;
  const row = list.rows[i];
  close();
  if (fromPointer) swallowClick({ released: true });
  if (select.isConnected) select.focus({ preventScroll: true });
  commitSelection(select, row);
}

/**
 * Eat the one `click` that the press we are handling is about to produce.
 * A press on the select released on a row lands its click on their common
 * ancestor, which the More menu reads as a click outside it; a press that only
 * dismissed a list must not also pick a cell.  It never outlives the gesture:
 * removed one task after the release (Chrome dispatches the click in the same
 * task as the release), or at once by `pointercancel` or a window blur.
 */
function swallowClick({ released = false } = {}) {
  const eat = (event) => { event.preventDefault(); event.stopPropagation(); off(); };
  const later = () => setTimeout(off, 0);
  function off() {
    window.removeEventListener('click', eat, true);
    window.removeEventListener('pointerup', later, true);
    window.removeEventListener('pointercancel', off, true);
    window.removeEventListener('blur', off);
  }
  window.addEventListener('click', eat, true);
  if (released) { later(); return; }
  window.addEventListener('pointerup', later, true);
  window.addEventListener('pointercancel', off, true);
  window.addEventListener('blur', off);
}

/** Open the list for `select` instead of the browser's, from a press or an opening key. */
function openFor(select, event) {
  let list;
  try {
    list = build(select);
  } catch (error) {
    console.warn("select list: could not build it, using the browser's", error);
    return;
  }
  if (!list) return;
  event.preventDefault();               // from here the browser's own list is cancelled
  select.focus({ preventScroll: true });
  try {
    present(list, event.type === 'mousedown');
  } catch (error) {
    console.warn("select list: could not show it, using the browser's", error);
    close();
    list.pop.remove();
    list.live.remove();
    try { select.showPicker?.(); } catch { /* nothing more to try */ }
  }
}

// -- events -------------------------------------------------------------------

function onPointerDown(event) {
  pointerType = event.pointerType || 'mouse';
  if (!open) return;
  const target = event.target instanceof Element ? event.target : null;
  if (target && open.pop.contains(target)) { open.armed = !!target.closest('.sel-opt'); return; }
  if (target && open.select.contains(target)) { open.armed = true; return; }
  if (!target) return;
  close();
  // A press on a control keeps its meaning (a second select's own mousedown
  // opens that select's list, so one click is enough).  Anywhere else the press
  // only dismisses: it must not start an orbit or pick the cell under it.
  if (event.button !== 0 || target.closest(INTERACTIVE)) return;
  event.preventDefault();
  event.stopPropagation();
  swallowClick();
}

function onMouseDown(event) {
  if (event.button !== 0 || pointerType === 'touch') return;   // touch keeps the system picker
  const target = event.target instanceof Element ? event.target : null;
  const select = target?.closest('select');
  if (!select || !eligible(select)) return;
  if (open?.select === select) {       // a press on the open select closes it
    event.preventDefault();
    close();
    return;
  }
  openFor(select, event);
}

function keyWhileOpen(event) {
  const list = open;
  const { key } = event;
  const stop = () => { event.preventDefault(); event.stopPropagation(); };
  if (key === 'Escape') { stop(); close(); return; }
  if (key === 'F4' || (event.altKey && (key === 'ArrowDown' || key === 'ArrowUp'))) {
    stop();
    if (!event.repeat) choose(list.active);
    return;
  }
  // Browser and app shortcuts pass through.
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const rows = list.rows;
  const page = () => {
    const first = list.nodes.find((n) => n.classList.contains('sel-opt'));
    return Math.max(1, Math.floor(list.pop.clientHeight / (first?.offsetHeight || 24)) - 1);
  };
  if (key === 'ArrowDown') { stop(); setActive(stepIndex(rows, list.active, 1), true); return; }
  if (key === 'ArrowUp') { stop(); setActive(stepIndex(rows, list.active, -1), true); return; }
  // The select would step its value in place under the open list.
  if (key === 'ArrowLeft' || key === 'ArrowRight') { stop(); return; }
  if (key === 'Home') { stop(); setActive(edgeIndex(rows, 'first'), true); return; }
  if (key === 'End') { stop(); setActive(edgeIndex(rows, 'last'), true); return; }
  if (key === 'PageDown') { stop(); setActive(pageIndex(rows, list.active, 1, page()), true); return; }
  if (key === 'PageUp') { stop(); setActive(pageIndex(rows, list.active, -1, page()), true); return; }
  if (key === 'Enter') { stop(); if (!event.repeat) choose(list.active); return; }
  if (key === 'Tab') {                   // commit, and let the focus move on
    choose(list.active);
    close();
    return;
  }
  const buffering = list.typed && event.timeStamp - list.typedAt <= TYPE_MS;
  if (key === ' ' && !buffering) { stop(); if (!event.repeat) choose(list.active); return; }
  if (key.length === 1) {                // type-ahead (a space inside a word included)
    stop();
    const next = nextTyped({ typed: list.typed, at: list.typedAt }, key, event.timeStamp);
    list.typed = next.typed;
    list.typedAt = next.at;
    const hit = typeaheadIndex(rows, list.active, list.typed);
    if (hit >= 0) setActive(hit, true);
  }
}

function onKeyDown(event) {
  if (event.isComposing || event.keyCode === 229) return;
  if (open) {
    // The list owns the keys only while its select has the focus; if the
    // focus went elsewhere (a script moved it) the list is stale.
    if (document.activeElement === open.select) { keyWhileOpen(event); return; }
    close();
  }
  const select = event.target instanceof HTMLSelectElement ? event.target : null;
  if (!select || !eligible(select)) return;
  const typing = lastPrintable.el === select && event.timeStamp - lastPrintable.at < CHROME_TYPE_MS;
  if (opensList(event, { mac: macPlatform(env.platform), typing })) {
    // Cancelling keydown also cancels the keypress Chrome opens its list on.
    if (event.repeat) event.preventDefault();
    else openFor(select, event);
    return;
  }
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    lastPrintable = { el: select, at: event.timeStamp };
  }
}

/** For tests and the console: which mode is in force and whether a list is open. */
export function selectPickerState() {
  return { mode: currentMode(), open: open ? open.select : null, platform: env.platform, chromium: env.chromium };
}

/**
 * Route every `<select>` on the page, present and future, to the in-page list
 * (where the mode is 'page'), and wire the Display checkbox.  Idempotent.
 */
export function installSelectPicker() {
  if (installed) return;
  installed = true;
  currentMode();
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('mousedown', onMouseDown, true);
  document.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('blur', close);
  window.addEventListener('resize', close);
  // Scrolling or zooming the page behind the list ends it, as it does the browser's own.
  document.addEventListener('wheel', (event) => {
    if (open && !open.pop.contains(event.target)) close();
  }, { capture: true, passive: true });
  window.addEventListener('storage', (event) => {
    if (event.key === null || event.key === STORAGE_KEY) refreshMode();
  });
  try { window.matchMedia('(any-pointer: fine)').addEventListener('change', refreshMode); } catch { /* old browser */ }
  bindBox();
}
