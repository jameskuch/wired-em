// The Visualizations menu on the page (asked for 2026-10-19): a dropdown in an open
// cell's row, the same list in the right-click menu with a follow-up "apply to all
// cells", and a small window to delete, share and import them.  The rules are
// visualizations.js; app.js gives this what it acts through:
//
//   ctx = { apply(record, vis), applyAll(vis), records(), status(text), flare(text),
//           hasBoundary(source), fail(error) }

import {
  BUILT_IN, fileName, fromCell, fromFile, loadSaved, matching, storeSaved, toFile, withSaved, withoutSaved,
} from './visualizations.js';

/** Every visualization offered: the built-in ones, then this browser's. */
export function allVisualizations() {
  return [...BUILT_IN, ...loadSaved()];
}

const ACTIONS = Object.freeze({ save: '@save', manage: '@manage' });

/** Save `text` as a file named `name`, as a download link would. */
function download(text, name) {
  const href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 30000);
}

/** Ask for a name and keep `record`'s look under it in this browser; the saved visualization, or null. */
export function saveLook(record, ctx) {
  const name = window.prompt(`Save c${record.id}'s look as a visualization named:`, '');
  if (!name || !name.trim()) return null;
  const list = loadSaved();
  const replacing = list.some((v) => v.name.toLowerCase() === name.trim().toLowerCase());
  if (replacing && !window.confirm(`Replace the saved visualization "${name.trim()}"?`)) return null;
  const vis = fromCell(record, name.trim());
  if (!storeSaved(withSaved(list, vis))) {
    ctx.flare('This browser would not keep it (private window, or storage blocked): use Manage → Save to a file');
    return null;
  }
  ctx.status(`Saved "${vis.name}" in this browser: it is in every cell's Visualizations list`);
  return vis;
}

/**
 * The dropdown for an open cell's row: the visualization the cell shows now (or
 * "Custom look"), every visualization to choose, Save this cell's look, and Manage.
 */
export function visualizationSelect(record, ctx) {
  const select = document.createElement('select');
  select.className = 'vis-select';
  select.setAttribute('aria-label', `c${record.id}'s visualization`);
  const list = allVisualizations();
  const now = matching(record, list, { hasBoundary: ctx.hasBoundary });
  const custom = new Option('Custom look', '');
  custom.disabled = true;
  select.append(custom);
  const group = (label, items) => {
    if (!items.length) return;
    const g = document.createElement('optgroup');
    g.label = label;
    for (const vis of items) {
      const o = new Option(vis.name, vis.id);
      o.title = vis.note || '';
      g.append(o);
    }
    select.append(g);
  };
  group('The lab\'s, and emphasis', BUILT_IN);
  group('Saved in this browser', list.filter((v) => !BUILT_IN.includes(v)));
  const more = document.createElement('optgroup');
  more.label = 'More';
  more.append(new Option('Save this cell\'s look as…', ACTIONS.save), new Option('Manage, share and import…', ACTIONS.manage));
  select.append(more);
  select.value = now ? now.id : '';
  select.title = now
    ? `${now.name}${now.note ? `\n\n${now.note}` : ''}`
    : 'This cell\'s look matches no visualization: Save this cell\'s look keeps it';
  select.addEventListener('click', (event) => event.stopPropagation());
  select.addEventListener('change', async () => {
    const value = select.value;
    select.value = now ? now.id : '';
    if (value === ACTIONS.save) { saveLook(record, ctx); ctx.changed?.(); return; }
    if (value === ACTIONS.manage) { openManager(select, record, ctx); return; }
    const vis = list.find((v) => v.id === value);
    if (vis) await ctx.apply(record, vis);
  });
  return select;
}

/**
 * The right-click menu's Visualizations submenu: `[[label, action]]`, each visualization
 * applied to `record`, then a button to apply it to every cell too, near (`x`, `y`).
 */
export function visualizationItems(record, ctx, { x, y }) {
  const list = allVisualizations();
  const now = matching(record, list, { hasBoundary: ctx.hasBoundary });
  const items = list.map((vis) => [`${now?.id === vis.id ? '✓ ' : ''}${vis.name}`, async () => {
    // Offered at once, while the cell is drawn: applying sets the look before it waits on the build.
    const drawing = ctx.apply(record, vis);
    if (ctx.records().length > 1) offerApplyAll(vis, ctx, { x, y });
    await drawing;
  }]);
  items.push(['Save this cell\'s look as…', () => { saveLook(record, ctx); ctx.changed?.(); }]);
  return items;
}

let offer = null;

/** Take the "apply to all cells" button away. */
export function dismissApplyAll() {
  if (!offer) return;
  offer.remove();
  offer = null;
}

/**
 * A button near (`x`, `y`) that applies `vis` to every loaded cell; it goes with any
 * click elsewhere, or Esc (asked for: "this disappears if a user clicks anywhere
 * else -- assume they only wanted to apply to the one cell").
 */
export function offerApplyAll(vis, ctx, { x, y }) {
  dismissApplyAll();
  const n = ctx.records().length;
  const box = document.createElement('div');
  box.className = 'vis-offer';
  box.setAttribute('role', 'dialog');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'primary';
  button.textContent = `Apply "${vis.name}" to all ${n} cells`;
  button.addEventListener('click', async (event) => {
    event.stopPropagation();
    dismissApplyAll();
    await ctx.applyAll(vis);
  });
  box.append(button);
  document.body.append(box);
  offer = box;
  const rect = box.getBoundingClientRect();
  box.style.left = `${Math.max(8, Math.min(x, window.innerWidth - rect.width - 8))}px`;
  box.style.top = `${Math.max(8, Math.min(y, window.innerHeight - rect.height - 8))}px`;
  const away = (event) => {
    if (box.contains(event.target)) return;
    gone();
  };
  const key = (event) => { if (event.key === 'Escape') gone(); };
  const gone = () => {
    document.removeEventListener('pointerdown', away, true);
    document.removeEventListener('keydown', key, true);
    if (offer === box) dismissApplyAll();
  };
  // After the click that chose it has finished.
  setTimeout(() => {
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', key, true);
  }, 0);
}

let manager = null;

/**
 * A small window under `anchor`: every visualization with a box to tick; Delete (the
 * saved ones), Save to a file (the ticked ones, or all saved when none is ticked),
 * Import from a file, Close.
 */
export function openManager(anchor, record, ctx) {
  manager?.remove();
  const box = document.createElement('div');
  box.className = 'vis-manager';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', 'Manage visualizations');
  const close = () => {
    box.remove();
    if (manager === box) manager = null;
    document.removeEventListener('pointerdown', away, true);
    document.removeEventListener('keydown', key, true);
  };
  const away = (event) => { if (!box.contains(event.target) && event.target !== anchor) close(); };
  const key = (event) => { if (event.key === 'Escape') close(); };
  const draw = () => {
    box.replaceChildren();
    const title = document.createElement('div');
    title.className = 'vis-manager-title';
    title.textContent = 'Visualizations';
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'icon-btn';
    x.textContent = '✕';
    x.title = 'Close';
    x.addEventListener('click', close);
    title.append(x);
    box.append(title);
    const saved = loadSaved();
    const ticked = new Set();
    const list = document.createElement('div');
    list.className = 'vis-manager-list';
    for (const vis of [...BUILT_IN, ...saved]) {
      const row = document.createElement('label');
      row.className = 'vis-manager-row';
      const tick = document.createElement('input');
      tick.type = 'checkbox';
      tick.addEventListener('change', () => {
        if (tick.checked) ticked.add(vis.id); else ticked.delete(vis.id);
        sync();
      });
      const name = document.createElement('span');
      name.textContent = vis.name;
      const kind = document.createElement('span');
      kind.className = 'vis-manager-kind';
      kind.textContent = BUILT_IN.includes(vis) ? 'built in' : 'saved here';
      row.title = vis.note || '';
      row.append(tick, name, kind);
      list.append(row);
    }
    box.append(list);
    const actions = document.createElement('div');
    actions.className = 'vis-manager-actions';
    const button = (text, title, fn) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = text;
      b.title = title;
      b.addEventListener('click', fn);
      actions.append(b);
      return b;
    };
    const del = button('Delete', 'Delete the ticked visualizations saved in this browser (built-in ones stay)', () => {
      const ids = [...ticked].filter((id) => saved.some((v) => v.id === id));
      if (!ids.length) return;
      const names = saved.filter((v) => ids.includes(v.id)).map((v) => `"${v.name}"`);
      if (!window.confirm(`Delete ${names.join(', ')} from this browser?`)) return;
      storeSaved(withoutSaved(saved, ids));
      ctx.status(`Deleted ${names.length} saved visualization${names.length === 1 ? '' : 's'}`);
      ctx.changed?.();
      draw();
    });
    const save = button('Save to a file', 'Save the ticked visualizations (or, with none ticked, every one saved here) '
      + 'to a file to send to someone, who brings them in with Import', () => {
      const all = [...BUILT_IN, ...saved];
      const chosen = ticked.size ? all.filter((v) => ticked.has(v.id)) : saved;
      if (!chosen.length) {
        ctx.flare('Tick the visualizations to save to a file (none is saved in this browser yet)');
        return;
      }
      download(toFile(chosen), fileName(chosen));
      ctx.status(`Saved ${chosen.length} visualization${chosen.length === 1 ? '' : 's'} to ${fileName(chosen)}`);
    });
    button('Import from a file…', 'Bring in the visualizations of a file someone saved with Save to a file', () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json,application/json';
      input.addEventListener('change', async () => {
        const file = input.files?.[0];
        if (!file) return;
        try {
          const { items, skipped } = fromFile(await file.text());
          let next = loadSaved();
          for (const vis of items) next = withSaved(next, vis);
          if (!storeSaved(next)) throw new Error('this browser would not keep them (private window, or storage blocked)');
          ctx.status(`Imported ${items.length} visualization${items.length === 1 ? '' : 's'} from ${file.name}`
            + `${skipped ? ` (${skipped} unreadable, left out)` : ''}; a saved one of the same name was replaced`);
          ctx.changed?.();
          draw();
        } catch (error) {
          ctx.flare(`Not imported: ${error.message}`);
        }
      });
      input.click();
    });
    const apply = button('Apply to this cell', `Apply the ticked visualization to c${record.id}`, async () => {
      const all = [...BUILT_IN, ...saved];
      const vis = all.find((v) => ticked.has(v.id));
      if (!vis) return;
      close();
      await ctx.apply(record, vis);
    });
    const sync = () => {
      const savedTicked = [...ticked].filter((id) => saved.some((v) => v.id === id)).length;
      del.disabled = !savedTicked;
      del.textContent = savedTicked ? `Delete ${savedTicked}` : 'Delete';
      save.textContent = ticked.size ? `Save ${ticked.size} to a file` : 'Save to a file';
      apply.disabled = ticked.size !== 1;
    };
    sync();
    box.append(actions);
    const hint = document.createElement('div');
    hint.className = 'vis-manager-hint';
    hint.textContent = 'Saved visualizations are kept in this browser. To share one, save it to a file and '
      + 'send the file; the other person imports it.';
    box.append(hint);
  };
  draw();
  document.body.append(box);
  manager = box;
  const at = anchor.getBoundingClientRect();
  const rect = box.getBoundingClientRect();
  box.style.left = `${Math.max(8, Math.min(at.left, window.innerWidth - rect.width - 8))}px`;
  box.style.top = `${Math.max(8, Math.min(at.bottom + 4, window.innerHeight - rect.height - 8))}px`;
  setTimeout(() => {
    document.addEventListener('pointerdown', away, true);
    document.addEventListener('keydown', key, true);
  }, 0);
  return box;
}
