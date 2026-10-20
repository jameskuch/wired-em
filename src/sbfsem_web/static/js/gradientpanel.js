// The gradient editor under Colormap: start from any map, move and recolor
// its stops with a live preview on the depth-colored cells, save it to the
// shared library under a name, and it is on the Map menu for everyone.
import {
  colormapNames, gradientStops, isCustomGradient, registerGradient,
  setGradientLibrary, unregisterGradient,
} from './colormaps.js';

const $ = (id) => document.getElementById(id);
const PREVIEW = '__preview';

/**
 * @param api        the API client
 * @param renderer   for setColormap while previewing
 * @param colorbar   the legend, redrawn on every edit
 * @param currentMap () => the name the Map menu shows
 * @param useMap     (name) => select a map in the Map menu and apply it
 * @param onLibraryChanged () => the Map menu re-lists its names
 */
export function bindGradientsPanel({ api, renderer, colorbar, currentMap, useMap, onLibraryChanged }) {
  const select = $('grad-select');
  const stopsEl = $('grad-stops');
  const previewEl = $('grad-preview');
  const nameInput = $('grad-name');
  const status = $('grad-status');
  if (!select) return null;

  let stops = [[0, '#000000'], [1, '#ffffff']];
  let basedOn = '';
  let previewing = false;

  const say = (text, kind = '') => {
    status.textContent = text;
    status.className = `panel-note${kind ? ` ${kind}` : ''}`;
  };

  const fillMenu = (keep) => {
    const names = colormapNames();
    select.replaceChildren(...names.map((n) => new Option(isCustomGradient(n) ? `${n} ★` : n, n)));
    select.value = names.includes(keep) ? keep : names[0];
  };

  const css = () => `linear-gradient(to right, ${stops.map(([t, c]) => `${c} ${(t * 100).toFixed(1)}%`).join(', ')})`;

  const preview = () => {
    previewEl.style.background = css();
    registerGradient(PREVIEW, stops);
    previewing = true;
    renderer.setColormap({ map: PREVIEW });
    colorbar.update();
  };

  const drawStops = () => {
    stopsEl.replaceChildren();
    stops.forEach(([t, c], i) => {
      const row = document.createElement('div');
      row.className = 'grad-stop';
      const pos = document.createElement('input');
      pos.type = 'range'; pos.min = 0; pos.max = 1; pos.step = 0.005; pos.value = t;
      pos.dataset.help = 'Position along the map (0 = INL side, 1 = GCL side)';
      const num = document.createElement('input');
      num.type = 'number'; num.min = 0; num.max = 1; num.step = 0.01; num.value = Number(t).toFixed(2);
      const color = document.createElement('input');
      color.type = 'color'; color.value = c;
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'icon-btn'; remove.textContent = '✕';
      remove.title = 'Remove this stop';
      remove.disabled = stops.length <= 2;
      const setPos = (v) => {
        const clamped = Math.min(1, Math.max(0, Number(v) || 0));
        stops[i][0] = clamped;
        pos.value = clamped; num.value = clamped.toFixed(2);
        preview();
      };
      pos.addEventListener('input', () => setPos(pos.value));
      num.addEventListener('change', () => { setPos(num.value); sortStops(); });
      pos.addEventListener('change', sortStops);
      color.addEventListener('input', () => { stops[i][1] = color.value; preview(); });
      remove.addEventListener('click', () => { stops.splice(i, 1); sortStops(); });
      row.append(pos, num, color, remove);
      stopsEl.append(row);
    });
    previewEl.style.background = css();
  };

  function sortStops() {
    stops.sort((a, b) => a[0] - b[0]);
    drawStops();
    preview();
  }

  const load = (name) => {
    stops = gradientStops(name).map(([t, c]) => [t, c]);
    basedOn = name;
    nameInput.value = isCustomGradient(name) ? name : `${name} custom`;
    $('grad-delete').disabled = !isCustomGradient(name);
    $('grad-delete').title = isCustomGradient(name)
      ? `Remove "${name}" from the shared library`
      : 'The MATLAB maps cannot be deleted; save your own under a new name';
    drawStops();
  };

  select.addEventListener('change', () => { load(select.value); preview(); say(`Editing a copy of ${select.value}`); });
  $('grad-add').addEventListener('click', () => {
    const [a, b] = stops.slice(-2);
    stops.push([(a[0] + b[0]) / 2, b[1]]);
    sortStops();
  });
  $('grad-reverse').addEventListener('click', () => {
    stops = stops.map(([t, c]) => [1 - t, c]);
    sortStops();
  });
  $('grad-even').addEventListener('click', () => {
    stops.forEach((s, i) => { s[0] = stops.length > 1 ? i / (stops.length - 1) : 0; });
    sortStops();
  });

  $('grad-save').addEventListener('click', async () => {
    const name = nameInput.value.trim();
    if (!name) { say('Give it a name first', 'error'); return; }
    if (colormapNames().includes(name) && !isCustomGradient(name)) {
      say(`"${name}" is a MATLAB map; choose another name`, 'error');
      return;
    }
    try {
      await api.librarySave('gradients', name, { stops, based_on: basedOn });
      await reload(name);
      load(name);
      useMap(name);
      previewing = false;
      say(`Saved "${name}" to the shared library and selected it`);
    } catch (error) {
      say(error?.detail || String(error?.message || error), 'error');
    }
  });

  $('grad-delete').addEventListener('click', async () => {
    const name = select.value;
    if (!isCustomGradient(name)) return;
    try {
      await api.libraryDelete('gradients', name);
      const wasCurrent = currentMap() === name;
      await reload(colormapNames()[0]);
      load(select.value);
      if (wasCurrent) useMap(colormapNames()[0]);
      say(`Deleted "${name}" from the shared library`);
    } catch (error) {
      say(error?.detail || String(error?.message || error), 'error');
    }
  });

  // Closing the editor ends the preview: the Map menu's choice comes back.
  $('grad-editor').addEventListener('toggle', () => {
    if ($('grad-editor').open) {
      // Opening only loads the stops to edit.  Previewing them here swapped the
      // cells' exact map for the editor's 9-stop sample of it (up to 165/255 off
      // on StepSeq) before anything had been edited; the first edit previews.
      load(currentMap());
    } else if (previewing) {
      previewing = false;
      unregisterGradient(PREVIEW);
      useMap(currentMap());
    }
  });

  async function reload(keep) {
    try {
      const body = await api.library('gradients');
      setGradientLibrary(body.entries || []);
    } catch (error) {
      console.warn('gradient library unavailable:', error);
    }
    fillMenu(keep);
    onLibraryChanged();
  }

  fillMenu(currentMap());
  load(select.value);
}
