// The Display & context panel: everything RenderApp's "Plot" tab could do.
//
// Camera by typed numbers rather than by dragging (azimuth/elevation in the
// MATLAB `view(az, el)` convention, so a figure angle written in a methods
// section reproduces exactly), axis limits as a real crop, surface finish,
// colormaps with a colorbar, a micron scale bar, and the volume context the
// MATLAB app drew: the fitted INL/GCL boundary sheets and the vasculature.

import {
  DEFAULT_COLORMAP, bandColors, colormapNames, colormapSample, isCustomGradient,
  isPreviewGradient, resolveColormap,
} from './colormaps.js';

const $ = (id) => document.getElementById(id);

/**
 * Camera azimuth/elevation in MATLAB's convention.
 *
 * `view(az, el)`: az is measured from the -Y axis, positive counterclockwise
 * about +Z; el is the angle above the XY plane. The MATLAB default 3-D view
 * is (-37.5, 30), and typing that here reproduces it.
 */
export function cameraAngles(renderer) {
  const offset = renderer.camera.position.clone().sub(renderer.controls.target);
  const r = offset.length();
  if (r < 1e-9) return { azimuth: 0, elevation: 0, distance: 0 };
  const elevation = Math.asin(Math.max(-1, Math.min(1, offset.z / r)));
  const azimuth = Math.atan2(-offset.x, -offset.y);
  return {
    azimuth: (azimuth * 180) / Math.PI,
    elevation: (elevation * 180) / Math.PI,
    distance: r,
  };
}

export function setCameraAngles(renderer, { azimuth, elevation, distance }) {
  const current = cameraAngles(renderer);
  const az = ((azimuth ?? current.azimuth) * Math.PI) / 180;
  const el = ((elevation ?? current.elevation) * Math.PI) / 180;
  const r = Math.max(distance ?? current.distance, 1e-6);
  const target = renderer.controls.target;
  renderer.camera.up.set(0, 0, 1);
  renderer.camera.position.set(
    target.x - r * Math.sin(az) * Math.cos(el),
    target.y - r * Math.cos(az) * Math.cos(el),
    target.z + r * Math.sin(el),
  );
  renderer._syncControlsUp();
  renderer.camera.updateProjectionMatrix();
  renderer.controls.update();
}

/**
 * A number box's value, or null when it is blank or not a number.
 *
 * `Number('')` is 0, a value; a blank box means "no value", which is what the
 * fallbacks (keep the current angle, use the scene's extent) are for.
 */
export function readNumber(text) {
  const raw = String(text ?? '').trim();
  if (raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

/** How far from the Z axis the camera may sit and still be "looking down Z" (degrees). */
const TWO_D_TOLERANCE_DEG = 0.5;

/**
 * Whether a view is the 2D view the 2D checkbox makes: orthographic, looking
 * straight along the section axis (from either end).  `offset` is the camera's
 * position relative to its orbit target.
 */
export function isTwoDView(offset, orthographic) {
  if (!orthographic) return false;
  const r = Math.hypot(offset[0], offset[1], offset[2]);
  if (!(r > 0)) return false;
  return Math.abs(offset[2]) / r >= Math.cos((TWO_D_TOLERANCE_DEG * Math.PI) / 180);
}

/** 1, 2 or 5 times a power of ten -- the lengths a scale bar is allowed. */
function niceLength(raw) {
  const power = 10 ** Math.floor(Math.log10(raw));
  for (const step of [1, 2, 5]) {
    if (raw <= step * power) return step * power;
  }
  return 10 * power;
}

/**
 * A micron scale bar over the viewport.
 *
 * Recomputed every frame from the camera, because a perspective view has no
 * single scale: this is the scale in the plane through the orbit target,
 * which is the plane the user is looking at and measuring against.
 */
export class ScaleBar {
  constructor(renderer, element) {
    this.renderer = renderer;
    this.el = element;
    this.visible = false;
    this.fixedLength = null;
    this._raf = null;
  }

  setVisible(on) {
    this.visible = !!on;
    this.el.hidden = !on;
    if (on) this._loop();
    else if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
  }

  /** null follows the zoom; a number pins the bar to that many microns. */
  setLength(um) {
    this.fixedLength = um && um > 0 ? Number(um) : null;
  }

  _loop = () => {
    if (!this.visible) return;
    this.update();
    this._raf = requestAnimationFrame(this._loop);
  };

  update() {
    const perPixel = this.renderer.micronsPerPixel();
    if (!perPixel || !Number.isFinite(perPixel)) return;
    // Aim for a quarter of the viewport, then round to a legible length.
    const target = perPixel * this.renderer.container.clientWidth * 0.25;
    const microns = this.fixedLength ?? niceLength(target);
    const pixels = microns / perPixel;
    if (!Number.isFinite(pixels) || pixels <= 0) return;
    this.el.style.width = `${Math.round(pixels)}px`;
    const label = microns >= 1 ? `${+microns.toFixed(2)} µm` : `${Math.round(microns * 1000)} nm`;
    if (this.el.dataset.label !== label) {
      this.el.dataset.label = label;
      this.el.textContent = label;
    }
  }
}

/** Past this many levels the bands are too narrow to see and the bar is drawn smooth. */
const COLORBAR_MAX_BANDS = 64;

/**
 * The CSS background of the colorbar for a map as the meshes are colored by it.
 *
 * With Levels at 8 the meshes are painted in 8 flat bands, so the bar is 8 flat
 * blocks: a smooth gradient there misread banded data by up to half the range
 * (129/255 at 4 levels).  Above `COLORBAR_MAX_BANDS` the bands are narrower than
 * a pixel or two and the bar is the smooth sampling it always was.
 */
export function colorbarGradient(map, levels, invert) {
  const rgb = (c) => `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)})`;
  const n = Math.max(2, Math.min(256, Math.round(levels) || 256));
  const stops = [];
  if (n <= COLORBAR_MAX_BANDS) {
    bandColors(map, n, invert).forEach((c, i) => {
      // A hard edge: this block ends where the next begins.
      stops.push(`${rgb(c)} ${((i / n) * 100).toFixed(3)}%`, `${rgb(c)} ${(((i + 1) / n) * 100).toFixed(3)}%`);
    });
  } else {
    for (let i = 0; i < COLORBAR_MAX_BANDS; i += 1) {
      const t = i / (COLORBAR_MAX_BANDS - 1);
      stops.push(`${rgb(colormapSample(map, invert ? 1 - t : t))} ${(t * 100).toFixed(1)}%`);
    }
  }
  return `linear-gradient(to right, ${stops.join(',')})`;
}

/** The colormap legend, drawn from the same table the meshes are colored by. */
export class Colorbar {
  constructor(renderer, element) {
    this.renderer = renderer;
    this.el = element;
  }

  setVisible(on) {
    this.el.hidden = !on;
    if (on) this.update();
  }

  update() {
    if (this.el.hidden) return;
    const { map, invert, levels } = this.renderer.colormap;
    const bar = this.el.querySelector('.cbar-gradient');
    if (bar) bar.style.background = colorbarGradient(map, levels, invert);
  }
}

/**
 * Binds the Display & context panel.
 *
 * Everything here is a view concern: nothing re-fetches a neuron, so a
 * material or a colormap change is instant and never invalidates a mesh.
 */
export class DisplayPanel {
  constructor({ renderer, api, state, onChange = () => {}, onStatus = () => {} }) {
    this.renderer = renderer;
    this.api = api;
    this.state = state;
    this.onChange = onChange;
    this.onStatus = onStatus;
    this.scaleBar = new ScaleBar(renderer, $('scale-bar'));
    this.colorbar = new Colorbar(renderer, $('colorbar'));
    this.contextLoaded = new Map(); // key -> source it was loaded for
    this._contextRun = 0;           // the latest restore() that carried the context sheets
    this.holdLimits = false;
    this._bind();
  }

  _bind() {
    // -- display options --------------------------------------------------
    $('opt-invert').addEventListener('change', (e) => {
      this.renderer.setInverted(e.target.checked);
      this.onChange();
    });
    $('opt-flipz').addEventListener('change', (e) => {
      this.renderer.setFlipZ(e.target.checked);
      this.onChange();
    });
    $('opt-2d').addEventListener('change', (e) => {
      // MATLAB's 2D: look straight down the section axis, orthographic, so
      // what you measure on screen is what the section plane holds.
      if (e.target.checked) {
        // The camera passes through states that are not 2D on the way; the
        // box must not unlatch itself from them.
        this._enteringTwoD = true;
        try {
          $('ortho').checked = true;
          this.renderer.setOrthographic(true);
          this.renderer.setViewpoint('xy1');
        } finally {
          this._enteringTwoD = false;
        }
      }
      this.onChange();
    });
    // The box says what the view is: it lets go when the view leaves 2D
    // (an orbit, another viewpoint, perspective), instead of staying ticked
    // over a view that no longer is.
    this.renderer.controls.addEventListener('change', () => this.syncTwoD());

    // -- colormap ---------------------------------------------------------
    const colormapSelect = $('opt-colormap');
    // The MATLAB RenderApp menu, same names and order, Spectral first as it
    // opened with, then the shared library's gradients (starred).
    this.refreshColormapMenu();
    colormapSelect.value = DEFAULT_COLORMAP;
    const applyColormap = () => {
      this.renderer.setColormap({
        map: colormapSelect.value,
        levels: Number($('opt-levels').value) || 256,
        invert: $('opt-cmap-invert').checked,
      });
      this.colorbar.update();
      this.onChange();
    };
    this._applyColormap = applyColormap;
    colormapSelect.addEventListener('change', applyColormap);
    $('opt-levels').addEventListener('change', applyColormap);
    $('opt-cmap-invert').addEventListener('change', applyColormap);
    $('opt-colorbar').addEventListener('change', (e) => {
      this.colorbar.setVisible(e.target.checked);
      this.onChange();
    });

    // -- scale bar --------------------------------------------------------
    $('opt-scalebar').addEventListener('change', (e) => {
      this.scaleBar.setVisible(e.target.checked);
      this.onChange();
    });
    $('opt-scalebar-um').addEventListener('change', (e) => {
      this.scaleBar.setLength(Number(e.target.value));
      this.scaleBar.update();
      this.onChange();
    });

    // -- context surfaces -------------------------------------------------
    for (const [id, key] of [['ctx-inl', 'INL'], ['ctx-gcl', 'GCL']]) {
      $(id).addEventListener('change', () => this._toggleBoundary(key, $(id)));
    }
    $('ctx-vessels').addEventListener('change', () => this._toggleVessels($('ctx-vessels')));
    $('ctx-opacity').addEventListener('input', (e) => {
      const opacity = Number(e.target.value);
      for (const key of this.contextLoaded.keys()) {
        this.renderer.setContextStyle(key, { opacity });
      }
      this.onChange();
    });

    // -- viewpoint --------------------------------------------------------
    $('cam-apply').addEventListener('click', () => {
      // A blank box keeps that part of the view as it is.
      setCameraAngles(this.renderer, {
        azimuth: readNumber($('cam-az').value) ?? undefined,
        elevation: readNumber($('cam-el').value) ?? undefined,
        distance: readNumber($('cam-dist').value) || undefined,
      });
      this.onChange();
    });
    $('cam-read').addEventListener('click', () => this.readCamera());

    // -- axis limits ------------------------------------------------------
    $('lim-apply').addEventListener('click', () => this._applyLimits());
    $('lim-reset').addEventListener('click', () => {
      this.renderer.setClipBox(null);
      this.holdLimits = false;
      $('lim-hold').checked = false;
      this.readLimits();
      this.onChange();
    });
    $('lim-read').addEventListener('click', () => this.readLimits());
    $('lim-hold').addEventListener('change', (e) => { this.holdLimits = e.target.checked; });

    // -- volume offsets ---------------------------------------------------
    $('vol-arrange')?.addEventListener('click', () => this.arrangeSideBySide());
    $('vol-align')?.addEventListener('click', () => this.alignSelectedSomas());
    $('vol-reset')?.addEventListener('click', () => this.resetVolumeOffsets());
  }

  /**
   * Unticks 2D once the camera is no longer looking down Z in orthographic.
   * The camera's change events call it; so does whoever has just decided the
   * camera after a `restore()` (it cannot judge the box itself: the camera it
   * was saved with is put back later).
   */
  syncTwoD() {
    const box = $('opt-2d');
    if (!box?.checked || this._enteringTwoD) return;
    const offset = this.renderer.camera.position.clone().sub(this.renderer.controls.target);
    if (!isTwoDView([offset.x, offset.y, offset.z], this.renderer.camera.isOrthographicCamera)) {
      box.checked = false;
    }
  }

  // -- volume offsets -----------------------------------------------------
  // Cells from several volumes share one scene, each in its own frame; these
  // move whole volumes so they can be compared side by side or overlaid.

  _volumeName(source) {
    return this.state.volumes?.find((v) => v.name === source)?.display_name || source;
  }

  /** Volumes with cells in the scene, in load order, then any still shifted. */
  _sourcesInScene() {
    const sources = [];
    for (const r of this.state.neurons.values()) {
      if (!sources.includes(r.source)) sources.push(r.source);
    }
    for (const s of this.renderer.volumeOffsets.keys()) {
      if (!sources.includes(s)) sources.push(s);
    }
    return sources;
  }

  /** One row of X/Y/Z fields per volume; the group hides with one volume and no shift. */
  refreshVolumeOffsets() {
    const host = $('vol-offsets');
    if (!host) return;
    const sources = this._sourcesInScene();
    const group = $('vol-group');
    if (group) group.hidden = sources.length < 2 && !this.renderer.volumeOffsets.size;
    host.replaceChildren();
    for (const source of sources) {
      const row = document.createElement('div');
      row.className = 'vol-row';
      const name = document.createElement('span');
      name.className = 'vol-name';
      name.textContent = this._volumeName(source);
      name.title = source;
      row.append(name);
      const offset = this.renderer.volumeOffset(source);
      ['X', 'Y', 'Z'].forEach((axis, i) => {
        const label = document.createElement('label');
        label.className = 'field';
        label.append(document.createTextNode(`${axis} `));
        const input = document.createElement('input');
        input.type = 'number';
        input.step = '1';
        input.value = String(Math.round(offset[i] * 100) / 100);
        input.dataset.help = `${axis} shift of everything from ${this._volumeName(source)}, in microns`;
        input.addEventListener('change', () => {
          const current = this.renderer.volumeOffset(source);
          current[i] = Number(input.value) || 0;
          this.renderer.setVolumeOffset(source, current);
          this.onChange();
        });
        label.append(input);
        row.append(label);
      });
      host.append(row);
    }
  }

  /** Line the volumes up along X, centered on the first in Y and Z. */
  arrangeSideBySide() {
    const boxes = this._sourcesInScene()
      .map((source) => ({ source, box: this.renderer.sourceBounds(source) }))
      .filter((b) => b.box);
    if (boxes.length < 2) {
      this.onStatus('Load cells from two or more volumes first', 'error');
      return;
    }
    const width = Math.max(...boxes.map(({ box }) => box.max[0] - box.min[0]));
    const gap = Math.max(width * 0.15, 5);
    const center = (box, i) => (box.min[i] + box.max[i]) / 2;
    const [first, ...rest] = boxes;
    this.renderer.setVolumeOffset(first.source, [0, 0, 0]);
    let cursor = first.box.max[0];
    for (const { source, box } of rest) {
      const dx = cursor + gap - box.min[0];
      this.renderer.setVolumeOffset(source, [
        dx, center(first.box, 1) - center(box, 1), center(first.box, 2) - center(box, 2),
      ]);
      cursor = box.max[0] + dx;
    }
    this.refreshVolumeOffsets();
    this.renderer.resetView();
    this.onChange();
    this.onStatus(`${boxes.length} volumes side by side along X, ${Math.round(gap)} µm apart; `
      + `${this._volumeName(first.source)} stays in its own frame`);
  }

  /** Put the soma of each selected cell on the soma of the first selected one. */
  alignSelectedSomas() {
    const selected = [...this.state.neurons.values()]
      .filter((r) => this.state.selection.has(r.key) && r.summary?.soma_xyz_um);
    const bySource = new Map();
    for (const r of selected) if (!bySource.has(r.source)) bySource.set(r.source, r);
    const anchor = selected[0];
    if (!anchor || bySource.size < 2) {
      this.onStatus('Select one cell in each of two or more volumes first', 'error');
      return;
    }
    const target = this.renderer.toWorld(anchor.summary.soma_xyz_um, anchor.source);
    if (this.renderer.flipZ) target[2] = -target[2];  // back to the unmirrored frame
    const moved = [];
    for (const [source, r] of bySource) {
      if (source === anchor.source) continue;
      const soma = r.summary.soma_xyz_um;
      this.renderer.setVolumeOffset(source, [
        target[0] - soma[0], target[1] - soma[1], target[2] - soma[2],
      ]);
      moved.push(`c${r.id} (${this._volumeName(source)})`);
    }
    this.refreshVolumeOffsets();
    this.onChange();
    this.onStatus(`Soma of ${moved.join(', ')} moved onto the soma of c${anchor.id} `
      + `(${this._volumeName(anchor.source)})`);
  }

  resetVolumeOffsets() {
    for (const source of [...this.renderer.volumeOffsets.keys()]) {
      this.renderer.setVolumeOffset(source, [0, 0, 0]);
    }
    this.refreshVolumeOffsets();
    this.onChange();
    this.onStatus('Every volume is back in its own frame');
  }

  /** Copy the live camera into the typed fields. */
  readCamera() {
    const { azimuth, elevation, distance } = cameraAngles(this.renderer);
    $('cam-az').value = azimuth.toFixed(1);
    $('cam-el').value = elevation.toFixed(1);
    $('cam-dist').value = distance.toFixed(1);
  }

  /** Copy the scene's extent (or the active crop) into the typed fields. */
  readLimits() {
    const box = this.renderer.clipBox || this.renderer.sceneBounds();
    if (!box) return;
    const ids = [['lim-x0', 'lim-x1'], ['lim-y0', 'lim-y1'], ['lim-z0', 'lim-z1']];
    ids.forEach(([lo, hi], axis) => {
      $(lo).value = box.min[axis].toFixed(1);
      $(hi).value = box.max[axis].toFixed(1);
    });
  }

  _applyLimits() {
    // A blank limit is the scene's own extent on that side.
    const num = (id, fallback) => readNumber($(id).value) ?? fallback;
    const scene = this.renderer.sceneBounds();
    if (!scene) return;
    const min = [num('lim-x0', scene.min[0]), num('lim-y0', scene.min[1]),
      num('lim-z0', scene.min[2])];
    const max = [num('lim-x1', scene.max[0]), num('lim-y1', scene.max[1]),
      num('lim-z1', scene.max[2])];
    for (let i = 0; i < 3; i += 1) {
      if (max[i] <= min[i]) {
        this.onStatus('Axis limits need max above min', 'error');
        return;
      }
    }
    this.renderer.setClipBox({ min, max });
    this.onChange();
  }

  /** Re-list the Map menu: the MATLAB maps, then the library's gradients. */
  refreshColormapMenu() {
    const select = $('opt-colormap');
    if (!select) return;
    const keep = select.value;
    const names = colormapNames();
    select.replaceChildren(...names.map((name) => new Option(
      name === DEFAULT_COLORMAP ? `${name} (default)`
        : isCustomGradient(name) ? `${name} ★` : name,
      name,
    )));
    select.value = names.includes(keep) ? keep : DEFAULT_COLORMAP;
  }

  /** The name the Map menu shows. */
  currentMap() {
    return $('opt-colormap').value || DEFAULT_COLORMAP;
  }

  /** Select a map in the menu and apply it, as the user choosing it would. */
  useMap(name) {
    const select = $('opt-colormap');
    if (![...select.options].some((o) => o.value === name)) this.refreshColormapMenu();
    select.value = resolveColormap(name);
    this._applyColormap?.();
  }

  async _toggleBoundary(layer, checkbox) {
    const key = `boundary:${layer}`;
    if (!checkbox.checked) {
      this.renderer.setContextStyle(key, { visible: false });
      return;
    }
    if (this.contextLoaded.get(key) === this.state.source) {
      this.renderer.setContextStyle(key, { visible: true });
      return;
    }
    // Pinned before the request. A session restore can switch volume while
    // the sheet is in flight, and a sheet fetched for one volume must never be
    // recorded as another's -- that put Inferior Monkey's INL over RC1 cells.
    const source = this.state.source;
    checkbox.disabled = true;
    let mesh;
    try {
      mesh = await this.api.boundaryMesh(source, layer);
    } catch (error) {
      checkbox.checked = false;
      this.onStatus(error?.detail || `No ${layer} boundary for this volume`, 'error');
      return;
    } finally {
      checkbox.disabled = false;
    }
    if (this.state.source !== source) return this._toggleBoundary(layer, checkbox);
    this.renderer.setContextMesh(key, mesh, {
      color: layer === 'INL' ? '#7f8fd6' : '#9b7fd6',
      opacity: Number($('ctx-opacity').value),
      source,
    });
    this.contextLoaded.set(key, source);
    // Unticked while it was in flight (or undone): loaded, but not shown.
    if (!checkbox.checked) this.renderer.setContextStyle(key, { visible: false });
  }

  async _toggleVessels(checkbox) {
    const key = 'vessels';
    if (!checkbox.checked) {
      this.renderer.setContextStyle(key, { visible: false });
      return;
    }
    if (this.contextLoaded.get(key) === this.state.source) {
      this.renderer.setContextStyle(key, { visible: true });
      return;
    }
    const source = this.state.source; // pinned, as in _toggleBoundary
    checkbox.disabled = true;
    this.onStatus('Loading blood vessels…');
    let mesh;
    try {
      mesh = await this.api.vesselMesh(source);
    } catch (error) {
      checkbox.checked = false;
      this.onStatus(error?.detail || String(error?.message || error), 'error');
      return;
    } finally {
      checkbox.disabled = false;
    }
    if (this.state.source !== source) return this._toggleVessels(checkbox);
    if (!mesh.n_faces) {
      checkbox.checked = false;
      this.onStatus('This volume has no traced blood vessels', 'error');
      return;
    }
    this.renderer.setContextMesh(key, mesh, {
      color: '#c0392b', opacity: Number($('ctx-opacity').value), source,
    });
    this.contextLoaded.set(key, source);
    // Unticked while it was in flight (or undone): loaded, but not shown.
    if (!checkbox.checked) this.renderer.setContextStyle(key, { visible: false });
    this.onStatus('Blood vessels loaded');
  }

  /** Drop context loaded for a volume the user has left. */
  volumeChanged() {
    for (const [key, source] of [...this.contextLoaded]) {
      if (source === this.state.source) continue;
      this.renderer.removeContextMesh(key);
      this.contextLoaded.delete(key);
      const box = { 'boundary:INL': 'ctx-inl', 'boundary:GCL': 'ctx-gcl', vessels: 'ctx-vessels' }[key];
      if (box && $(box)) $(box).checked = false;
    }
  }

  /** Everything the session should remember about the view. */
  snapshot() {
    // While the gradient editor previews, the renderer wears a private
    // '__preview' map that exists only in this tab; what a session or an undo
    // step should keep is the map the menu names.
    const colormap = { ...this.renderer.colormap };
    if (isPreviewGradient(colormap.map)) colormap.map = this.currentMap();
    return {
      invert: $('opt-invert').checked,
      flip_z: $('opt-flipz').checked,
      two_d: $('opt-2d').checked,
      colormap,
      colorbar: $('opt-colorbar').checked,
      scalebar: $('opt-scalebar').checked,
      scalebar_um: $('opt-scalebar-um').value,
      context: {
        inl: $('ctx-inl').checked,
        gcl: $('ctx-gcl').checked,
        vessels: $('ctx-vessels').checked,
        opacity: Number($('ctx-opacity').value),
      },
      clip_box: this.renderer.clipBox,
      hold_limits: this.holdLimits,
      volume_offsets: this.renderer.volumeOffsetMap(),
    };
  }

  /**
   * Restore a snapshot; context meshes are re-fetched, not stored.
   *
   * Every field a snapshot carries is APPLIED, off as well as on: undo and
   * redo restore through here, and a restore that only switched things on could
   * not revert a crop, Hold, a volume shift or a context sheet (all four stayed
   * as they were when the earlier state had them off).  A field a snapshot does
   * not carry at all (a session saved before it existed) is left as it is.
   */
  async restore(saved) {
    if (!saved) return;
    const set = (id, value) => { if (value !== undefined && $(id)) $(id).checked = !!value; };
    set('opt-invert', saved.invert);
    set('opt-flipz', saved.flip_z);
    set('opt-2d', saved.two_d);
    this.renderer.setInverted(!!saved.invert);
    this.renderer.setFlipZ(!!saved.flip_z);
    // Sessions from before materials were per cell carry one global name;
    // the app applies it to cells that have none of their own.
    if (saved.material) this.legacyMaterial = saved.material;
    if (saved.colormap) {
      // Older views may name a map the menu no longer has (`stratify`, `jet`).
      const map = resolveColormap(saved.colormap.map);
      this.refreshColormapMenu();
      $('opt-colormap').value = map;
      $('opt-levels').value = saved.colormap.levels ?? 256;
      $('opt-cmap-invert').checked = !!saved.colormap.invert;
      this.renderer.setColormap({ ...saved.colormap, map });
    }
    set('opt-colorbar', saved.colorbar);
    this.colorbar.setVisible(!!saved.colorbar);
    set('opt-scalebar', saved.scalebar);
    if (saved.scalebar_um !== undefined) $('opt-scalebar-um').value = saved.scalebar_um;
    this.scaleBar.setLength(Number(saved.scalebar_um));
    this.scaleBar.setVisible(!!saved.scalebar);
    if (saved.clip_box !== undefined) {
      // No box is no crop: the planes come off and the boxes show the scene
      // again.  Only when the crop differs, so an undo step that has nothing to
      // do with it does not overwrite limits someone is typing.
      const want = saved.clip_box || null;
      if (JSON.stringify(this.renderer.clipBox) !== JSON.stringify(want)) {
        this.renderer.setClipBox(want);
        this.readLimits();
      }
    }
    if (saved.hold_limits !== undefined) {
      this.holdLimits = !!saved.hold_limits;
      $('lim-hold').checked = this.holdLimits;
    }
    // Before the cells arrive: an object placed later reads its volume's
    // offset as it is added.  A volume the snapshot does not list is back in
    // its own frame.
    if (saved.volume_offsets && typeof saved.volume_offsets === 'object') {
      for (const source of [...this.renderer.volumeOffsets.keys()]) {
        if (!(source in saved.volume_offsets)) this.renderer.setVolumeOffset(source, [0, 0, 0]);
      }
      for (const [source, xyz] of Object.entries(saved.volume_offsets)) {
        if (Array.isArray(xyz) && xyz.length === 3) this.renderer.setVolumeOffset(source, xyz);
      }
      this.refreshVolumeOffsets();
    }
    const ctx = saved.context;
    if (ctx) {
      if (ctx.opacity !== undefined) {
        $('ctx-opacity').value = ctx.opacity;
        for (const key of this.contextLoaded.keys()) {
          this.renderer.setContextStyle(key, { opacity: Number(ctx.opacity) });
        }
      }
      // A sheet the snapshot has off is hidden and unticked, loaded or not.
      // All three boxes first, before any request: `snapshot()` (what the
      // session and the undo history save right after a restore starts) reads
      // them, and with a box set only after the sheet before it had loaded the
      // stored session still had the undone sheet on.
      const sheets = [
        ['ctx-inl', (box) => this._toggleBoundary('INL', box), ctx.inl],
        ['ctx-gcl', (box) => this._toggleBoundary('GCL', box), ctx.gcl],
        ['ctx-vessels', (box) => this._toggleVessels(box), ctx.vessels],
      ];
      for (const [id, , on] of sheets) $(id).checked = !!on;
      // A restore that comes after this one has its own boxes: this one stops
      // at its next step rather than set a sheet the later one switched off.
      const mine = ++this._contextRun;
      // The ones to hide go first: that is immediate, where a sheet to show is a request.
      for (const [id, toggle, on] of sheets) {
        if (!on) toggle($(id));
      }
      // Sequential: each is a server request, and the vessels one is the slow.
      for (const [id, toggle, on] of sheets) {
        if (!on) continue;
        if (mine !== this._contextRun) return;
        await toggle($(id));
      }
    }
    // No `syncTwoD()` here.  The 2D box was set from the snapshot above, and
    // the camera it describes is restored by the caller later (a session
    // restore does it seconds on, once the cells are loaded): the camera's own
    // change event unticks the box if the view it restores is not 2D.  Checking
    // now compared the box with the camera of before the restore and unticked
    // a 2D view that was about to be put back.  A caller that decides the camera
    // another way, or decides to leave it (Discard, a session with no cells),
    // calls `syncTwoD()` itself once it has.
  }
}
