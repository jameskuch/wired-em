// The material editor in Display & context: pick a finish, drag its
// properties with a live preview on the selected cells, save it to the shared
// library under a name, apply it to cells.
import {
  DEFAULT_MATERIAL, MATERIAL_COLORS, MATERIAL_FIELDS, completeSpec, isPresetMaterial,
  materialNames, materialSpec, setMaterialLibrary, specForLibrary,
} from './materials.js';

const $ = (id) => document.getElementById(id);

/**
 * @param api      the API client (library, librarySave, libraryDelete)
 * @param targets  () => records the preview and Apply act on: the selected
 *                 cells, or every cell when none is selected
 * @param allRecords () => every loaded record
 * @param preview  (records, spec) => paint an unsaved spec on those cells
 * @param applyTo  (records, name) => assign a library name to those cells
 * @param onLibraryChanged () => menus elsewhere re-list the names
 * @returns {{edit: (name: string) => void}|null} `edit` opens the editor on a material
 */
export function bindMaterialsPanel({
  api, targets, allRecords, preview, applyTo, onLibraryChanged,
}) {
  const select = $('mat-select');
  const fields = $('mat-fields');
  const nameInput = $('mat-name');
  const descInput = $('mat-desc-input');
  const status = $('mat-status');
  if (!select) return null;

  let spec = materialSpec(DEFAULT_MATERIAL);
  let dirty = false;
  const controls = new Map();

  const say = (text, kind = '') => {
    status.textContent = text;
    status.className = `panel-note${kind ? ` ${kind}` : ''}`;
  };

  const fillMenu = (keep) => {
    const names = materialNames();
    select.replaceChildren(...names.map((n) => new Option(isPresetMaterial(n) ? n : `${n} ★`, n)));
    select.value = names.includes(keep) ? keep : DEFAULT_MATERIAL;
  };

  const showSpec = () => {
    $('mat-desc').textContent = spec.description || '';
    for (const [key, control] of controls) {
      if (control.type === 'checkbox') control.checked = !!spec[key];
      else control.value = spec[key];
      const out = control.parentElement.querySelector('output');
      if (out) out.value = Number(spec[key]).toFixed(2);
    }
    const preset = isPresetMaterial(spec.name);
    nameInput.value = preset ? `${spec.name} copy` : spec.name || '';
    descInput.value = preset ? '' : (spec.description || '');
    $('mat-delete').disabled = preset;
    $('mat-delete').title = preset
      ? 'Built-in materials cannot be deleted; save a copy under your own name'
      : `Remove "${spec.name}" from the shared library`;
  };

  // -- the property controls ------------------------------------------------
  const paint = () => {
    dirty = true;
    preview(targets(), completeSpec({ ...spec, name: '__preview' }));
    const n = targets().length;
    say(n ? `Previewing on ${n} cell${n === 1 ? '' : 's'} — Save to keep it` : 'Load a cell to preview');
  };
  for (const [key, [lo, hi, , label, help]] of Object.entries(MATERIAL_FIELDS)) {
    const row = document.createElement('label');
    row.className = 'mat-field';
    row.dataset.help = help;
    const text = document.createElement('span');
    text.textContent = label;
    const input = document.createElement('input');
    input.type = 'range';
    input.min = lo; input.max = hi; input.step = 0.01;
    const out = document.createElement('output');
    input.addEventListener('input', () => {
      spec[key] = Number(input.value);
      out.value = Number(input.value).toFixed(2);
      paint();
    });
    row.append(text, input, out);
    fields.append(row);
    controls.set(key, input);
  }
  for (const [key, [label]] of Object.entries(MATERIAL_COLORS)) {
    const row = document.createElement('label');
    row.className = 'mat-field color';
    const text = document.createElement('span');
    text.textContent = label;
    const input = document.createElement('input');
    input.type = 'color';
    input.addEventListener('input', () => { spec[key] = input.value; paint(); });
    row.append(text, input);
    fields.append(row);
    controls.set(key, input);
  }
  {
    const row = document.createElement('label');
    row.className = 'check';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.addEventListener('change', () => { spec.flatShading = input.checked; paint(); });
    row.append(input, document.createTextNode(' Flat shading (show every triangle)'));
    fields.append(row);
    controls.set('flatShading', input);
  }
  {
    const row = document.createElement('label');
    row.className = 'check';
    row.title = 'One flat color with no light or shade: every other field is then left out';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.addEventListener('change', () => { spec.unlit = input.checked; paint(); });
    row.append(input, document.createTextNode(' Unlit (one flat color)'));
    fields.append(row);
    controls.set('unlit', input);
  }

  // -- picking, saving, deleting, applying -------------------------------------
  // Like the Map menu: choosing applies at once, to the selected cells (or
  // all when none are selected), and the editor below shows that material.
  select.addEventListener('change', () => {
    spec = materialSpec(select.value);
    dirty = false;
    showSpec();
    const records = targets();
    if (records.length) {
      applyTo(records, spec.name);
      say(`${spec.name} on ${records.length} cell${records.length === 1 ? '' : 's'} — ${spec.description || ''}`);
    } else {
      say(`${spec.name}: ${spec.description || 'no notes'}`);
    }
  });

  $('mat-save').addEventListener('click', async () => {
    const name = nameInput.value.trim();
    if (!name) { say('Give it a name first', 'error'); return; }
    if (isPresetMaterial(name)) { say(`"${name}" is a built-in; choose another name`, 'error'); return; }
    const doc = specForLibrary({ ...spec, description: descInput.value.trim() });
    doc.based_on = isPresetMaterial(spec.name) ? spec.name : (spec.based_on || spec.name);
    try {
      await api.librarySave('materials', name, doc);
      await reload(name);
      spec = materialSpec(name);
      dirty = false;
      showSpec();
      // What was previewed is now a library name: assign it, so the cells
      // survive a reload wearing it.
      applyTo(targets(), name);
      say(`Saved "${name}" to the shared library and applied it`);
    } catch (error) {
      say(error?.detail || String(error?.message || error), 'error');
    }
  });

  $('mat-delete').addEventListener('click', async () => {
    const name = spec.name;
    if (isPresetMaterial(name)) return;
    try {
      await api.libraryDelete('materials', name);
      await reload(DEFAULT_MATERIAL);
      // Cells wearing it fall back to the default when they resolve.
      for (const record of allRecords()) {
        if (record.display?.material === name) applyTo([record], DEFAULT_MATERIAL);
      }
      spec = materialSpec(DEFAULT_MATERIAL);
      showSpec();
      say(`Deleted "${name}" from the shared library`);
    } catch (error) {
      say(error?.detail || String(error?.message || error), 'error');
    }
  });

  const applyCurrent = (records) => {
    if (!records.length) { say('No cells to apply to', 'error'); return; }
    if (dirty) {
      // An unsaved edit is only a preview; naming it is what makes it
      // something a session can restore.
      preview(records, completeSpec({ ...spec, name: '__preview' }));
      say(`Previewing on ${records.length} cell(s) — Save to library to keep it`);
      return;
    }
    applyTo(records, spec.name);
    say(`Applied ${spec.name} to ${records.length} cell(s)`);
  };
  $('mat-apply-selected').addEventListener('click', () => applyCurrent(targets()));
  $('mat-apply-all').addEventListener('click', () => applyCurrent(allRecords()));

  async function reload(keep) {
    try {
      const body = await api.library('materials');
      setMaterialLibrary(body.entries || []);
    } catch (error) {
      console.warn('material library unavailable:', error);
    }
    fillMenu(keep);
    onLibraryChanged();
  }

  fillMenu(DEFAULT_MATERIAL);
  showSpec();
  return {
    /** Open the editor on a named material (from a cell's Edit… button). */
    edit(name) {
      $('mat-editor').open = true;
      select.value = materialNames().includes(name) ? name : DEFAULT_MATERIAL;
      select.dispatchEvent(new Event('change'));
      $('mat-editor').scrollIntoView({ block: 'nearest' });
    },
  };
}
