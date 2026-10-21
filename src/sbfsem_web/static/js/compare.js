// The Render compare tab: one cell, several renderings, side by side.
//
// Each pane owns a full Renderer3D over the same cell, with the cameras kept in
// lockstep so orbiting one pane orbits them all -- the eye compares surfaces,
// not viewpoints.  A pane holds up to four surface LAYERS drawn together (a
// layer is one rendering of the cell: its own technique and options, color,
// opacity, visibility, surface and wire), plus one annotations overlay and the
// Position shift layer.  A pane can also be a Difference: mesh A colored by its
// signed distance to mesh B (computed server-side with the measurement's own
// resolution reported), which turns "these look different" into microns.
//
// Everything that takes time is a server job (api.mesh, api.deviationJob,
// api.diffJob), so nothing here can hit the gateway's timeout; each pane shows
// its job on a progress card and its failure on a banner (jobcard.js).  A LAYER
// is always in one state -- idle, queued, building, measuring, ready, stale,
// error-build, error-measure or stopped (CompareView._stateOf) -- the pane's is
// the worst of its layers' (comparelayers.js worstState), and the header chips,
// the card, the legend dimming and the toolbar's summary line are all drawn
// from them.
//
// A technique layer owns a complete, private set of render options
// (`layer.config.options`): the same ones, from the same schema, that the 3D
// render panel edits, shown by the same form in the options panel the pane's
// header arrow opens (compareoptions.js) for the layer being edited.  Its preset
// select only names what the options are, and is re-derived from them after
// every edit.  Editing rebuilds nothing: the layer goes out of date, and Compare
// rebuilds the ones that are.
//
// Work is scheduled per pane: one serial queue each, at most three panes
// working at once (comparelayers.js WorkQueues), and a mesh downloaded for one
// layer is shared by every layer that asks for the same one (MeshCache), so a
// technique in three panes is built and parsed once.
//
// Two rules the measurements depend on:
//   * A layer measures the surface it HOLDS, not the settings it shows now.
//     `layer.builtOptions` / `layer.builtKey` are the options that produced
//     `layer.mesh`, set together with it; the error map and a Difference are
//     requested from that snapshot, never from `_layerOptions(layer)`.  Changing
//     a knob makes the layer stale (the chip says so); it does not change what
//     is measured.
//   * A measurement result carries no geometry.  It is attached to the layer's
//     own mesh only when it names that mesh (`mesh_id`, `n_vertices`);
//     otherwise the mesh is fetched again once and measured again, and a
//     second mismatch is an error rather than a wrong picture.

import { api } from './api.js';
import { Renderer3D } from './render3d.js';
import { ViewCube } from './viewcube.js';
import { loadGeneration } from './state.js';
import { JobCard, formatElapsed } from './jobcard.js';
import { SliceState, SHIFT_DEFAULTS, normalizeShiftDisplay, pairsFor } from './shifts.js';
import {
  applySliceKey, buildLayer, drawLayer, sliceCenters, syncSlice, thresholdsForPair, ThresholdMemory,
} from './shiftlayer.js';
import { buildShiftStrip } from './shiftpanel.js';
import { ShiftLegend, legendBlocks } from './shiftlegend.js';
import {
  RENDER_PRESETS, RENDER_REQUIRES, MESH_OPTION_KEYS, CURVE_METHODS, matchPreset, presetOptions,
  curvePixelSeed, techniqueDefaults, DEFAULT_RENDER,
} from './renderoptions.js';
import {
  OptionsPanel, paneOptions, presetById, geometryOf, techniqueName, copyTuning, resetOptions,
  saveTuning,
} from './compareoptions.js';
import {
  MAX_LAYERS, MAX_PANES, MIN_PANES, GHOST_OPACITY, DISC_COLOR,
  newLayerStyle, nextColor, clampOpacity, wireColorFor, shortName, pickOptions, stableKey, meshKey,
  worstState, moveItem, normalizeZRange, normalizeHex, MeshCache, WorkQueues,
} from './comparelayers.js';

/** The render options the measurement endpoints accept: the whole of a layer's
 * snapshot (`builtOptions`).  They come from the render schema, so a layer, the
 * 3D render panel and the server name the same set. */
export { MESH_OPTION_KEYS };

/** Diverging blue–white–red colors for signed distances at ±scale. */
function divergingColors(values, scale) {
  const colors = new Float32Array(values.length * 3);
  const neg = [0.23, 0.45, 0.92];   // A inside B
  const pos = [0.86, 0.22, 0.22];   // A outside B
  for (let i = 0; i < values.length; i += 1) {
    let t = values[i] / scale;
    if (t > 1) t = 1; else if (t < -1) t = -1;
    const end = t < 0 ? neg : pos;
    const w = Math.abs(t);
    colors[3 * i] = 1 + (end[0] - 1) * w;
    colors[3 * i + 1] = 1 + (end[1] - 1) * w;
    colors[3 * i + 2] = 1 + (end[2] - 1) * w;
  }
  return colors;
}

/**
 * Error colors for absolute distances, 0 to `scale`: light gray where the
 * render and the annotations agree, amber half way, red at the scale and
 * beyond.  Gray rather than a dark end, so the part that is right is still a
 * surface you can see on the dark viewport.
 */
const ERROR_STOPS = [[0.85, 0.87, 0.90], [1.0, 0.69, 0.0], [0.85, 0.12, 0.23]];
const ERROR_GRADIENT = `linear-gradient(to right, ${ERROR_STOPS.map(
  (stop) => `rgb(${stop.map((c) => Math.round(c * 255)).join(',')})`).join(', ')})`;

function errorColors(values, scale) {
  const colors = new Float32Array(values.length * 3);
  for (let i = 0; i < values.length; i += 1) {
    const t = Math.min(1, Math.max(0, values[i] / scale)) * 2;
    const [from, to] = t < 1 ? [ERROR_STOPS[0], ERROR_STOPS[1]] : [ERROR_STOPS[1], ERROR_STOPS[2]];
    const w = t < 1 ? t : t - 1;
    for (let c = 0; c < 3; c += 1) colors[3 * i + c] = from[c] + (to[c] - from[c]) * w;
  }
  return colors;
}

// The Position shift layer of a pane lives under its own key: it does not depend
// on the pane's layers, so a failed build or a cleared layer leaves it alone.
const SHIFT_KEY = 'cmp-shift';
// The annotations overlay of a pane: one per pane, however many layers it has.
// (A key never holds a '/': the renderer reads the text before one as a volume.)
const ANNOTATION_KEY = 'A';
const layerKey = (id) => `L${id}`;

// A pane narrower than this (px) shows its layers as dots: the names are in the
// tooltips and the editor.
const NARROW_PX = 260;

// How many columns the grid has for each pane count (app.css, .compare-grid.panes-N): the view
// cube sits in the last pane of the top row, the grid's top-right corner.
const COLUMNS = [1, 1, 2, 3, 2, 3, 3];

// A one-layer pane whose chips' line has less than this (px) shows its state as a dot, not a word: "out of
// date" is 71 px, the layer's chip at its narrowest 66, the annotations' 66, and the gaps between them.
const CHIP_DOT_BELOW_PX = 215;

// The page-wide popups a control of the layer editor opens (colorpicker.js; the in-page list of selectpicker.js
// stops its own clicks, so none of them reaches here today): a click in one is not a click away from the editor.
const POPUPS_OF_THE_EDITOR = ['.color-pop', '.sel-pop'];

/** Does a measurement result describe exactly this mesh?  The server's copy
 * can differ from the layer's (the cell was reloaded, the cache evicted and the
 * surface rebuilt differently); painting a result over a different mesh would
 * be a wrong picture, so ids and vertex counts must both agree. */
function sameSurface(mesh, result) {
  return !!mesh && !!result
    && (mesh.mesh_id ?? null) === (result.mesh_id ?? null)
    && mesh.n_vertices === result.n_vertices;
}

/** A hover explanation in the app's own panel (data-help), after a long pause. */
function help(node, text) {
  node.dataset.help = text;
  node.dataset.helpDelay = '2000';
  return node;
}

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const textButton = (text, className, helpText) => {
  const node = el('button', className, text);
  node.type = 'button';
  if (helpText) help(node, helpText);
  return node;
};

/** A labeled checkbox: `{ wrap, box }`; `wrap`'s first child is the box. */
function checkbox(label, checked, onChange, helpText) {
  const wrap = el('label', 'compare-check');
  if (helpText) help(wrap, helpText);
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = checked;
  box.addEventListener('change', () => onChange(box.checked));
  wrap.append(box, document.createTextNode(` ${label}`));
  return { wrap, box };
}

/** The header chip for each state that needs one: [text, what it means]. */
const CHIPS = {
  queued: ['waiting', 'Waiting for its turn: at most three panes build at once, and the layers '
    + 'of a pane are built one after another.'],
  stale: ['out of date',
    'Out of date: you changed this pane’s settings, or the cell, or added a layer, after it was '
    + 'built, so what is shown is the earlier build. Press Compare to rebuild it. Ticking error '
    + 'here rebuilds it with the new settings first, then measures.'],
  stopped: ['stopped',
    'Stopped before it finished. Whatever was built earlier is still shown. '
    + 'Press Compare to carry on.'],
  'error-build': ['failed', 'This pane failed; the red banner over it says why and has Retry.'],
  'error-measure': ['failed', 'This pane’s measurement failed; the red banner over it says '
    + 'why and has Retry. The surface is unchanged.'],
  // A Difference has no surface of its own to keep: its map is removed.
  'error-diff': ['failed', 'This Difference could not be measured; the red banner over it says '
    + 'why and has Retry. The pane shows no map until it succeeds.'],
};

/** What a layer's own chip says beside its name (a pane with several layers only). */
const LAYER_STATE_WORDS = {
  idle: 'not built', queued: 'waiting', building: 'building', measuring: 'measuring',
  stale: 'out of date', stopped: 'stopped', 'error-build': 'failed', 'error-measure': 'failed',
};

export class CompareView {
  /**
   * `caps()` says what the server can do ({ meshlabOff }), so a MeshLab
   * technique is offered only where it can run; `cell()` is the compared
   * cell's record before the first Compare has made it `this.record`.
   * `mainSettings()` is what the 3D render panel's controls say now, and
   * `sendToMain(options)` puts a layer's geometry options into them (the two
   * bridges to the 3D render; every other bridge stays inside the view).
   * `panel` is the options panel's element and `errorToggle` the toolbar's
   * Error checkbox (both found by id when not given).
   * `onContextMenu({ record, pane, candidates, clientX, clientY })` is a right click in
   * a pane: the app opens the compared cell's menu (what a pane shows is that cell).
   * `volumeInfo(source)` is a volume's `/api/volumes` entry: where its current section
   * transforms are not read (`has_transforms` false), the Position shift offers the
   * section correction alone.
   * `estimates` (renderestimate.js's EstimateCache) puts the server's estimate of a
   * Closed curve or Hybrid layer on the options panel's chip, and marks the settings
   * of a layer that will not build as set (an outline method's Voxel size, a whole-cell
   * field's Fine voxel and Face cap) with the fixes, as the 3D render panel does;
   * `fitPixel(record, options)` answers `{ pixel, why }` when that layer's Voxel size
   * is too fine for the cell: the layer is then built at `pixel` (both optional).
   */
  constructor(grid, {
    onStatus, summary, onRunningChange, shiftHost, caps, cell, mainSettings, sendToMain, panel,
    errorToggle, onContextMenu, volumeInfo, estimates, fitPixel,
  } = {}) {
    this.grid = grid;
    this.volumeInfo = volumeInfo || (() => null);
    this.onStatus = onStatus || (() => {});
    this.onRunningChange = onRunningChange || null;
    this.caps = caps || (() => ({}));
    this.cell = cell || (() => this.record);
    this.mainSettings = mainSettings || null;
    this.sendToMain = sendToMain || null;
    this.onContextMenu = onContextMenu || null;
    this.fitPixel = fitPixel || null;
    // The pane whose options the panel shows, and whether the panel is up.  The
    // layer it edits is that pane's `editing` layer.
    this.focus = 0;
    this._optionsOpen = false;
    const host = panel || grid.parentElement?.querySelector?.('#panel-compare-options') || null;
    this.optionsPanel = host ? new OptionsPanel(host, {
      caps: () => this.caps() || {},
      estimates: estimates || null,
      onInput: (key, event) => this._optionsEdited(key, event),
      onBridge: (id) => this._bridge(id),
      onPick: (set) => this._fixPicked(set),
      onClose: () => this._closeFromPanel(),
    }) : null;
    // The toolbar's one-line account of every pane (ready, working, failed).
    this.summaryEl = summary || grid.parentElement?.querySelector('.compare-summary') || null;
    // The toolbar's Error toggle: error paint on every solid surface layer, and back.
    this.errorToggle = errorToggle || grid.parentElement?.querySelector?.('#compare-error') || null;
    this.errorToggle?.addEventListener?.('change', () => this.setErrorAll(this.errorToggle.checked));
    this.panes = [];
    this.record = null;      // the compared cell's record, as last built
    this.errorScale = null;  // pinned error scale, null = automatic
    this.syncEnabled = true;
    this._syncing = false;
    this._outdated = false;  // the cell picker now names another cell or load
    this._annotations = null; // { key, promise } the cell's annotations, fetched once
    this.maximized = null;   // the pane that fills the grid, if one does
    this.viewCube = null;    // the view cube in the grid's top-right corner (setViewCube)
    this._pop = null;        // the popover open now: { pane, which, el, anchor }
    this._layerSeq = 0;
    // The Position shift layer, shared by every pane that has its box ticked: one
    // set of settings (Compare-local, started from the compared cell's own), one
    // slice, one layer built once and drawn in each pane through its own renderer.
    this.shiftHost = shiftHost || grid.parentElement?.querySelector?.('.compare-shifts') || null;
    this._shift = {
      display: normalizeShiftDisplay({ ...SHIFT_DEFAULTS, showShift: true }),
      slices: new SliceState(),
      thresholds: new ThresholdMemory(),
      touched: false,        // the user changed a setting here: a new load does not reset it
      sig: null,             // what the layer in `layer` was built from
      promise: null,         // the build under way or done, for that signature
      layer: null,
      strip: null,
    };
    // Downloaded meshes, shared by every layer (a technique in three panes is one download).
    this.meshes = new MeshCache();
    // One serial queue per pane, at most three panes working at once.
    this._queues = new WorkQueues();
    this._tasks = new Set();
    this._inflight = new Set();   // the promise of every task queued or running
    this._notes = [];        // what the work of this run did, for the closing status
    this._queued = null;     // a Compare pressed while work was running
    this._runKind = null;    // 'build' (a Compare) or 'pane' (one layer's own work)
    this._runStartedAt = 0;
    this._ticker = null;
    // A popover closes on a click outside it and on Esc.
    document.addEventListener?.('click', (event) => this._clickAway(event));
    document.addEventListener?.('keydown', (event) => {
      if (event.key === 'Escape' && this._pop) this._closePop({ focus: true });
    });
    // It was placed for this window: another size puts it somewhere else.
    if (typeof window !== 'undefined') window.addEventListener?.('resize', () => this._closePop());
  }

  /** True while any layer has work queued or running. */
  get running() { return this._tasks.size > 0; }

  /** Default technique per pane index: the recommended render, the MATLAB
   * baseline, the tube sweep, the first two measured against each other,
   * and — at five or six panes — the remaining pathways.  Preset ids. */
  static DEFAULTS = ['voxel2', 'original', 'tube', 'diff', 'adaptive', 'wrap'];

  /** A new pane's first layer: its default technique at your saved defaults for it. */
  _defaultConfig(index) {
    const tech = CompareView.DEFAULTS[index] || 'voxel2';
    const preset = presetById(tech) ?? presetById('voxel2');
    return {
      tech,                     // a preset id, 'custom' (options no preset names) or 'diff'
      // The whole option set the layer builds, resolved once: changing the saved
      // technique defaults later never alters a layer that already exists.
      options: paneOptions(presetOptions(preset)),
      seededPixel: null,        // the outline raster this view chose, if it did (see _seedRaster)
      // The outline raster is the user's: typed in the panel, or brought by a bridge.  Then
      // it is kept whatever it is, 0.05 µm included (a technique's untouched raster is
      // not told from a chosen 0.05 by its value).
      pixelChosen: false,
      ...newLayerStyle(0),      // color, opacity (applied live), visible, surface, wire
      error: false,             // color by distance from the annotations
      a: 0,                     // diff sources: pane numbers
      b: 1,
      scale: null,              // diff color scale, null = server suggestion
    };
  }

  /**
   * Show `count` panes (1 to 6).  Panes are added or removed at the end: the ones
   * that stay keep their layers, options, toggles, surfaces and renderers, so
   * changing the count never destroys what you set up.  Work belonging to a
   * removed pane is stopped; the rest carries on.
   */
  setPaneCount(count) {
    const want = Math.min(MAX_PANES, Math.max(MIN_PANES, Math.round(Number(count)) || MIN_PANES));
    if (this.maximized) this._maximize(null);
    while (this.panes.length > want) this._removePane(this.panes[this.panes.length - 1]);
    while (this.panes.length < want) this._addPane(this.panes.length, this._defaultConfig(this.panes.length));
    this.grid.className = `compare-grid panes-${want}`;
    this.focus = Math.min(this.focus, want - 1);
    // A diff pane built mid-loop only saw the panes created before it, so its
    // source dropdowns stopped at that point (at six panes, a diff in pane 4
    // listed panes 1-3 and nothing after). Rebuild them now that the full
    // roster exists.
    this._refreshDiffEditors();
    // A new pane has no surfaces; the caller decides when to build.
    this._refresh();
    // A kept pane keeps its Position shift box ticked: it draws it (it already does, in a kept renderer).
    for (const pane of this.panes) if (pane.shift) this._applyShifts(pane);
    this._shiftStrip();
    this._syncOptions();
    this._placeCube();
  }

  /** Rebuild every Difference pane's editor so its source lists are current. */
  _refreshDiffEditors(except = null) {
    for (const pane of this.panes) {
      if (pane !== except && this._isDiff(pane)) this._fillEditor(pane);
    }
  }

  /** A pane whose one layer is a Difference. */
  _isDiff(pane) { return pane.layers.length === 1 && pane.layers[0].config.tech === 'diff'; }

  _addPane(index, config) {
    const pane = {
      index,
      layers: [],
      editing: 0,             // the layer the editor and the options panel are on
      state: 'idle',          // the worst of the layers' (see _renderState)
      shift: false,           // the Position shift layer (shared settings, the strip)
      shiftLayer: null,       // the Position shift layer this pane draws (shiftlayer.js)
      annotations: false,     // the annotations overlay (discs as rings, outlines)
      annotationsVisible: true,
      annotationsDrawn: null, // the cell load the rings on screen were drawn for
      zRange: null,           // { min, max } µm shown, null = all
      bannerLayer: null,      // the layer whose failure the banner shows
      framedFor: null,        // the cell load the camera was last framed for
      get error() { return this.layers.find((l) => l.error)?.error ?? null; },
      // What the header holds, set by _buildHead and the painters.
      chip: null,             // the pane's state chip
      chipDot: false,         // ... shown as a dot, its word in the tooltip (see _fitChips)
      chipsEl: null,          // the layer chips
      techSelect: null,       // the technique select (in the editor)
      optsButton: null,       // the arrow that opens the layer's options (technique layers)
      errorBox: null,         // the error checkbox (in the editor)
      el: document.createElement('div'),
    };
    pane.el.className = 'compare-pane';

    const head = document.createElement('div');
    head.className = 'compare-head';
    const viewport = document.createElement('div');
    viewport.className = 'compare-viewport';
    const legend = document.createElement('div');
    legend.className = 'compare-legend';
    legend.hidden = true;
    pane.el.append(head, viewport, legend);
    pane.head = head;
    pane.viewport = viewport;
    pane.legend = legend;
    // Clicking a pane's header makes it the focused pane, the one the options
    // panel shows.  The viewport does not: a click there orbits.  Keyboard focus
    // arriving in a header does not either: Tab runs through every header on the
    // way to the panel, and the panel must stay where it is for that (and a
    // mouse press focuses a button before its click, so a focus rule would move
    // the panel first and the arrow's click would then close it).
    head.addEventListener('click', () => this._focusPane(pane));
    this.grid.append(pane.el);

    pane.renderer = new Renderer3D(viewport);
    pane.renderer.onContextMenu = (request) => this.onContextMenu?.({
      ...request, record: this.record || this.cell(), pane: pane.index,
    });
    pane.renderer.controls.addEventListener('change', () => this._syncFrom(pane));
    // Every pane draws every frame it is in sight; the cube reads the one it shows.
    pane.renderer.onFrame = () => this.viewCube?.update();
    // Slice keys on any pane step the shared slice (a pane has no annotation selection).
    pane.renderer.onSliceStep = (event) => this._sliceKey(event, pane);
    // After the renderer, so its layer sits over the canvas.
    pane.card = new JobCard(viewport, {
      onStop: () => this._stopPane(pane),
      // Stop, Retry and Dismiss remove the control that had focus: the
      // pane's first layer chip is the nearest place to carry on from.
      focusFallback: () => pane.head.querySelector?.('.layer-name'),
    });
    // The layers' chips drop their names in a narrow pane.
    if (typeof ResizeObserver !== 'undefined') {
      pane.observer = new ResizeObserver(() => this._sized(pane));
      pane.observer.observe(pane.el);
    }
    this.panes.push(pane);
    this._newLayer(pane, config);
    this._buildHead(pane);
  }

  /** Take a pane away for good: its work, its renderer, its elements. */
  _removePane(pane) {
    for (const layer of pane.layers) this._dropTask(layer);
    if (this._pop?.pane === pane) this._closePop();
    pane.observer?.disconnect();
    pane.renderer.dispose();
    pane.card.destroy();
    pane.el.remove();
    this.panes.splice(this.panes.indexOf(pane), 1);
    if (!this._tasks.size) this._runEnded({ announce: false });
  }

  _sized(pane) {
    const width = pane.el.clientWidth;
    pane.el.classList.toggle('narrow', width > 0 && width < NARROW_PX);
    this._placeChips(pane);
    this._fitChips(pane);
  }

  /**
   * How the chips are drawn in the width they have.  The line they are in takes the width the buttons leave
   * and never more (css: a basis of 0), so it says how much there is whatever the chips hold.  The pane's
   * state is a dot, its word in the tooltip, where the pane has several layers (each chip carries its own
   * word), is narrow, or has under CHIP_DOT_BELOW_PX there.  When the chips at their narrowest (names at their
   * floor, the state words gone) still overflow the line, the pane is `tight`: a dot and a small arrow each,
   * as in a narrow pane.  Both follow from the width and the counts alone, never from a state, so a chip that
   * comes or goes does not change what the others look like.  A pane out of sight has no width: it waits.
   */
  _fitChips(pane) {
    const line = pane.chipsEl;
    const width = line?.clientWidth || 0;
    const narrow = pane.el.classList.contains('narrow');
    pane.chipDot = pane.layers.length > 1 || narrow || (width > 0 && width < CHIP_DOT_BELOW_PX);
    pane.chip.classList.toggle('dot', pane.chipDot);
    if (!(width > 0)) return;
    pane.el.classList.remove('tight');
    if (!narrow && line.scrollWidth > width + 1) pane.el.classList.add('tight');
  }

  // -- layers -----------------------------------------------------------------

  /** A new layer at the end of the pane, with its slot in the legend. */
  _newLayer(pane, config) {
    const id = ++this._layerSeq;
    const legend = el('div', 'legend-section');
    legend.hidden = true;
    pane.legend.append(legend);
    const layer = {
      id,
      key: layerKey(id),      // its key in the pane's renderer
      pane,
      config,
      state: 'idle',
      task: null,             // the work queued or running for this layer
      error: null,            // { kind: 'build' | 'measure' | 'diff', error } until dismissed
      stopped: false,         // the last work was stopped before it finished
      // The mesh a technique layer holds and the options that produced it. A
      // measurement is requested from these, never from the layer's settings.
      mesh: null,
      builtRequest: null,     // the full options the mesh job was asked for
      builtOptions: null,     // their measurement subset (MESH_OPTION_KEYS)
      builtKey: null,         // stableKey(builtOptions)
      builtGen: null,         // the cell load (record.loadGen) it was built for
      deviation: null,        // last deviation result, measured on `mesh`
      // A Difference layer: the result, which panes it measured, and the key
      // (their built keys) it was measured from.
      diff: null,
      diffSources: null,
      diffKey: null,
      colored: false,         // the surface on screen carries vertex colors (an error map or a difference)
      painted: null,          // what the error map on screen was drawn from
      legendRefs: null,       // the error legend's boxes, kept so a repaint edits them in place
      legend,
      chip: null,             // { el, dot, name, state } once the pane's chips are built
    };
    pane.layers.push(layer);
    return layer;
  }

  _editingLayer(pane) {
    return pane.layers[Math.min(pane.editing, pane.layers.length - 1)];
  }

  _layerIndex(layer) { return layer.pane.layers.indexOf(layer); }

  /** "Pane 2" for a pane with one layer, "Pane 2 layer 3" for a layer of several. */
  _where(layer) {
    const n = layer.pane.index + 1;
    return layer.pane.layers.length > 1 ? `Pane ${n} layer ${this._layerIndex(layer) + 1}` : `Pane ${n}`;
  }

  /** The layer's technique in full ("Original (MATLAB)"), or "Difference". */
  _layerTitle(layer) {
    return layer.config.tech === 'diff' ? 'Difference' : techniqueName(layer.config.options);
  }

  /** Is this layer still part of the page (its pane not removed, itself not removed)? */
  _owns(layer) { return this.panes.includes(layer.pane) && layer.pane.layers.includes(layer); }

  /** Add a render layer after the pane's last: a copy of `source`'s options in the next color. */
  addLayer(pane, source = null) {
    if (this._isDiff(pane) || pane.layers.length >= MAX_LAYERS) return null;
    const from = source ?? this._editingLayer(pane);
    const config = {
      ...from.config,
      options: { ...from.config.options },
      ...newLayerStyle(pane.layers.length, nextColor(pane.layers.map((l) => l.config.color))),
      error: false,
    };
    const layer = this._newLayer(pane, config);
    pane.editing = pane.layers.length - 1;
    this._deriveTech(layer);
    this._layersChanged(pane);
    this.onStatus(`${this._where(layer)} added: ${this._layerTitle(layer)}. Press Compare to build it.`);
    return layer;
  }

  /** Take a layer off its pane (the last one stays: a pane always draws something). */
  removeLayer(layer) {
    const { pane } = layer;
    if (pane.layers.length <= 1 || !pane.layers.includes(layer)) return false;
    const index = this._layerIndex(layer);
    this._dropTask(layer);
    if (pane.bannerLayer === layer) { pane.card.clearError(); pane.bannerLayer = null; }
    pane.renderer.removeAll(layer.key);
    pane.renderer.setLayerOrder(layer.key, null);
    layer.legend.remove();
    pane.layers.splice(index, 1);
    this._legendLayout(pane);
    // The layer being edited stays the same one; if it was the one removed, the next takes its place.
    if (pane.editing > index) pane.editing -= 1;
    pane.editing = Math.min(pane.editing, pane.layers.length - 1);
    this._layersChanged(pane);
    this._repaintErrors();
    this._frame(pane);
    return true;
  }

  /** Move a layer `delta` places in the draw order (the first draws first and wins ties). */
  moveLayer(layer, delta) {
    const { pane } = layer;
    const from = this._layerIndex(layer);
    const to = Math.min(pane.layers.length - 1, Math.max(0, from + delta));
    if (from < 0 || to === from) return false;
    const editing = this._editingLayer(pane);
    pane.layers = moveItem(pane.layers, from, to);
    // The legend sections follow the order too.
    pane.legend.replaceChildren(...pane.layers.map((l) => l.legend));
    this._legendLayout(pane);
    pane.editing = pane.layers.indexOf(editing);
    this._layersChanged(pane);
    return true;
  }

  /** Show only this layer, or (when it is already the only one shown) all of them again. */
  soloLayer(layer) {
    const { pane } = layer;
    const others = pane.layers.filter((l) => l !== layer);
    const alone = layer.config.visible && others.every((l) => !l.config.visible);
    layer.config.visible = true;
    for (const other of others) other.config.visible = alone;
    for (const l of pane.layers) this._styleLayer(l, l.colored);
    this._syncHead(pane);
  }

  /**
   * Where the layer chips are in the header's DOM, which is the order Tab walks it in (CSS `order` moves
   * what is seen and not what Tab visits).  One layer's chips sit among the buttons, after the badge; several
   * (or any in a narrow pane) take a line of their own under the buttons.  Tab then goes the way the eye does.
   */
  _placeChips(pane) {
    if (!pane.chipsEl || pane.chipsEl.parentElement !== pane.head) return;
    // On a line of their own (css): several layers, or a pane too narrow to hold them beside the buttons.
    const own = pane.layers.length > 1 || pane.el.classList.contains('narrow');
    const before = own ? pane.addMenu : pane.addButton;
    if (before && pane.chipsEl.nextSibling !== before && before.parentElement === pane.head) {
      pane.head.insertBefore(pane.chipsEl, before);
    }
  }

  /** The layer list of a pane changed: order, count, who is editing. */
  _layersChanged(pane) {
    pane.el.dataset.layers = String(pane.layers.length);
    this._placeChips(pane);
    for (const layer of pane.layers) this._styleLayer(layer, layer.colored);
    this._applyZRange(pane);
    this._buildChips(pane);
    this._fillEditor(pane, { keepOpen: true });
    this._syncHead(pane);
    this._refreshDiffEditors(pane);
    this._refresh();
    this._syncOptions();
  }

  // -- the header ---------------------------------------------------------------

  /**
   * (Re)build one pane's header: the number badge (double-click maximizes), the
   * state chip, the layer chips, `+`, the options arrow, the shift box and the
   * menu, with the popovers they open.  The editor is the layer's controls.
   */
  _buildHead(pane) {
    const head = pane.head;
    const reopen = this._pop?.pane === pane && this._pop.which === 'editor';
    const refocus = this._editorFocus(pane);
    head.replaceChildren();

    // The number a Difference pane's dropdowns and legend refer to; double-click maximizes.
    const num = el('span', 'compare-num', String(pane.index + 1));
    help(num, `Pane ${pane.index + 1} — what "pane ${pane.index + 1}" means in a Difference pane’s `
      + 'dropdowns and legend. Double-click it to make this pane fill the grid (again to put it back).');
    num.addEventListener('dblclick', () => this.toggleMaximize(pane));
    head.append(num);

    // The state chip: only for states that need a word (waiting, out of date, stopped, failed); the
    // progress card says the rest.  It lives in the chips' own element, first in it (_buildChips): that
    // element takes the width left on the header's first line and no more, so a chip that comes or goes
    // cannot push a button to another line, and the header keeps its height.
    pane.chip = el('span', 'compare-chip');
    pane.chip.hidden = true;

    pane.chipsEl = el('div', 'compare-layers');
    head.append(pane.chipsEl);

    pane.addButton = textButton('+', 'compare-add',
      'Add a layer to this pane: another rendering of the cell drawn in the same view (up to four), '
      + 'or the cell’s raw annotations.');
    pane.addButton.setAttribute('aria-haspopup', 'true');
    pane.addButton.setAttribute('aria-expanded', 'false');
    pane.addButton.setAttribute('aria-label', `Add to pane ${pane.index + 1}`);
    // A click with no mouse behind it (Enter or Space) has no click count: focus goes into the popover.
    pane.addButton.addEventListener('click', (event) => this._togglePop(pane, 'add', { keyboard: event?.detail === 0 }));
    head.append(pane.addButton);

    pane.optsButton = null;
    if (!this._isDiff(pane) && this.optionsPanel) {
      // The layer's render options: the same controls as the 3D render panel, in
      // one panel under the focused pane.
      const arrow = el('button', 'compare-opts');
      arrow.type = 'button';
      arrow.setAttribute('aria-controls', 'panel-compare-options');
      arrow.setAttribute('aria-label', `Render options of pane ${pane.index + 1}`);
      help(arrow, 'Show the render options of the layer being edited: the same controls as the 3D '
        + 'render panel, for this layer only. Nothing rebuilds as you edit; press Compare.');
      // A click with no mouse behind it (Enter or Space) has no click count.
      arrow.addEventListener('click', (event) => this._toggleOptions(pane, { keyboard: event?.detail === 0 }));
      pane.optsButton = arrow;
      head.append(arrow);
      this._syncArrow(pane);
    }

    // The Position shift is the pane's, whatever its layers: the layer is independent of them.
    const shift = checkbox('shift', pane.shift, (on) => {
      pane.shift = on;
      this._shiftToggled(pane);
    }, 'Position shift: an arrow from where each annotation was to where '
      + 'it is drawn, with the settings in the strip below the panes (shared by every pane that has '
      + 'this ticked). Nothing about the surface changes.');
    pane.shiftBox = shift.box;
    head.append(shift.wrap);

    pane.moreButton = textButton('⋯', 'compare-more', 'This pane’s tools: reset the view, save a '
      + 'picture, fill the grid with it, and cut it to a range of Z.');
    pane.moreButton.setAttribute('aria-haspopup', 'true');
    pane.moreButton.setAttribute('aria-expanded', 'false');
    pane.moreButton.setAttribute('aria-label', `Pane ${pane.index + 1} tools`);
    pane.moreButton.addEventListener('click', (event) => this._togglePop(pane, 'more', { keyboard: event?.detail === 0 }));
    head.append(pane.moreButton);

    pane.addMenu = this._buildAddMenu(pane);
    pane.paneMenu = this._buildPaneMenu(pane);
    pane.editor = el('div', 'compare-pop compare-editor');
    pane.editor.hidden = true;
    pane.editor.setAttribute('role', 'dialog');
    head.append(pane.addMenu, pane.paneMenu, pane.editor);
    this._fillEditor(pane);
    this._buildChips(pane);
    this._placeChips(pane);
    this._syncHead(pane);
    this._renderState(pane);
    if (reopen) this._openPop(pane, 'editor');
    this._editorFocus(pane, refocus);
  }

  /** Which of the editor's controls has keyboard focus (a name), or put focus back on the one named. */
  _editorFocus(pane, restore = null) {
    const names = ['techSelect', 'moveLeft', 'moveRight', 'duplicateButton', 'removeButton', 'opacityInput'];
    if (restore === null) {
      if (typeof document === 'undefined') return null;
      return names.find((name) => pane[name] && document.activeElement === pane[name]) ?? null;
    }
    // A button that is now disabled (the layer reached an end) hands focus to its neighbor; one with no
    // neighbor (Duplicate at four layers, Remove at one) to the editor's first control, so the keyboard
    // stays in the editor and does not fall to the page.
    const order = restore === 'moveLeft' ? ['moveLeft', 'moveRight'] : restore === 'moveRight' ? ['moveRight', 'moveLeft'] : [restore];
    const target = [...order, 'techSelect'].map((name) => pane[name]).find((node) => node && !node.disabled);
    target?.focus?.();
    return restore;
  }

  /** The `+` menu: another render layer, or the annotations overlay. */
  _buildAddMenu(pane) {
    const menu = el('div', 'compare-pop compare-menu');
    menu.hidden = true;
    menu.setAttribute('role', 'menu');
    pane.addLayerItem = textButton('Render layer', '',
      'Add a surface layer: a copy of the layer being edited, in the next color and translucent, '
      + 'to build another way or tune differently and see against the first. Up to four layers '
      + 'per pane; Compare builds it. A Difference pane takes none.');
    pane.addLayerItem.addEventListener('click', () => {
      this._closePop();
      // The keyboard goes to the layer just added (its chip), which the next Tab would otherwise skip past.
      this.addLayer(pane)?.chip?.name?.focus?.();
    });
    pane.annotationsItem = textButton('Annotations', '',
      'The cell’s raw annotations drawn over every layer of this pane: discs as rings, closed '
      + 'curves as their traced outlines. One overlay per pane, however many layers it has.');
    pane.annotationsItem.addEventListener('click', () => {
      this._closePop({ focus: true });
      this.setAnnotations(pane, !pane.annotations);
    });
    menu.append(pane.addLayerItem, pane.annotationsItem);
    return menu;
  }

  /** The `⋯` menu: view tools and the pane's Z range. */
  _buildPaneMenu(pane) {
    const menu = el('div', 'compare-pop compare-menu');
    menu.hidden = true;
    menu.setAttribute('role', 'menu');
    const reset = textButton('Reset view', '', 'Frame the pane’s layers again (with Sync cameras, '
      + 'every pane follows).');
    reset.addEventListener('click', () => {
      this._closePop({ focus: true });
      pane.renderer.resetView();
      this._syncFrom(pane);
    });
    const snap = textButton('Save picture', '', 'Save this pane’s view as a PNG file.');
    snap.addEventListener('click', () => {
      this._closePop({ focus: true });
      pane.renderer.snapshot?.(`sbfsem-compare-pane-${pane.index + 1}.png`);
    });
    pane.maximizeItem = textButton('Maximize', '', 'Make this pane fill the whole grid; the others '
      + 'keep their layers and wait. Double-clicking its number does the same, and so does '
      + 'this again.');
    pane.maximizeItem.addEventListener('click', () => {
      this._closePop({ focus: true });
      this.toggleMaximize(pane);
    });
    const zRow = el('div', 'compare-zrange');
    help(zRow, 'Show only the part of the pane between these two Z levels, in µm (the volume’s own '
      + 'Z): a cut-away of every layer and the annotations, to read overlapping surfaces apart. '
      + 'Blank = open on that side.');
    const zText = el('span', '', 'Z µm');
    const field = (name) => {
      const input = document.createElement('input');
      input.type = 'number';
      input.step = 'any';
      input.className = 'compare-z';
      input.setAttribute('aria-label', `${name} Z in µm`);
      input.placeholder = name;
      input.addEventListener('change', () => this.setZRange(pane, pane.zMin.value, pane.zMax.value));
      return input;
    };
    pane.zMin = field('from');
    pane.zMax = field('to');
    const clear = textButton('Clear', '', 'Show the whole pane again.');
    clear.addEventListener('click', () => {
      pane.zMin.value = '';
      pane.zMax.value = '';
      this.setZRange(pane, '', '');
    });
    zRow.append(zText, pane.zMin, document.createTextNode('–'), pane.zMax, clear);
    menu.append(reset, snap, pane.maximizeItem, zRow);
    return menu;
  }

  /**
   * The layer editor: the controls of the layer being edited, in a popover under
   * its chip.  Built again whenever the layer being edited or its kind changes;
   * the values of an unchanged editor are kept in line by `_syncHead`.
   */
  _fillEditor(pane, { keepOpen = false } = {}) {
    const layer = this._editingLayer(pane);
    const { config } = layer;
    const diff = config.tech === 'diff';
    const editor = pane.editor;
    const open = keepOpen && this._pop?.pane === pane && this._pop.which === 'editor';
    const refocus = this._editorFocus(pane);
    editor.replaceChildren();
    const k = this._layerIndex(layer) + 1;
    editor.setAttribute('aria-label', pane.layers.length > 1
      ? `Layer ${k} of pane ${pane.index + 1}` : `Pane ${pane.index + 1} layer`);

    const title = el('div', 'pop-title');
    pane.editorTitle = el('span', 'pop-name');
    const close = textButton('✕', 'pop-close', 'Close this.');
    close.setAttribute('aria-label', 'Close the layer editor');
    close.addEventListener('click', () => this._closePop({ focus: true }));
    title.append(pane.editorTitle, close);
    editor.append(title);

    const row = (labelText, ...controls) => {
      const node = el('div', 'pop-row');
      if (labelText) node.append(el('span', 'pop-label', labelText));
      node.append(...controls);
      editor.append(node);
      return node;
    };

    const tech = document.createElement('select');
    help(tech, 'This layer’s technique. Picking one fills the layer’s render options with that '
      + 'technique at your saved defaults for it; the name always follows the options, so '
      + 'a combination no technique names reads Custom…. A Difference is a pane of its own: it '
      + 'needs a pane with one layer.');
    this._fillTechniques(tech, config.tech, layer);
    tech.addEventListener('change', () => this._techniquePicked(layer, tech.value));
    pane.techSelect = tech;
    row('', tech);

    pane.errorBox = null;
    pane.colorInput = null;
    pane.retryButton = null;
    pane.moveLeft = null;
    pane.moveRight = null;
    pane.duplicateButton = null;
    pane.removeButton = null;
    if (diff) {
      this._diffControls(layer, row);
    } else {
      const color = document.createElement('input');
      color.type = 'color';
      color.value = config.color;
      color.className = 'layer-color';
      help(color, 'This layer’s color. Layers get a palette that stays apart for color-blind eyes; '
        + 'the first is the yellow every pane has always had. An error map shows its own colors '
        + 'instead while it is on.');
      color.addEventListener('input', () => this.setLayerColor(layer, color.value));
      pane.colorInput = color;
      row('Color', color);
    }

    const alpha = document.createElement('input');
    alpha.type = 'number';
    alpha.min = 0.05; alpha.max = 1; alpha.step = 0.05;
    alpha.value = config.opacity;
    help(alpha, 'Layer opacity, applied live — turn it down to see the layers and annotations '
      + 'inside it. A translucent layer does not hide what is behind it. Alpha blending of shells '
      + 'that cross is not exact from every angle: reorder the layers, cut the pane with a Z range, '
      + 'or use Ghost to read them.');
    alpha.addEventListener('change', () => this.setLayerOpacity(layer, alpha.value, { echo: alpha }));
    pane.opacityInput = alpha;
    const ghost = textButton('Ghost', '', `Set this layer to ${GHOST_OPACITY} opacity: a faint shape to read the other layers through.`);
    ghost.addEventListener('click', () => this.setLayerOpacity(layer, GHOST_OPACITY));
    row('α', alpha, ghost);

    const show = checkbox('show', config.visible, (on) => this.setLayerVisible(layer, on),
      'Draw this layer. A hidden layer still builds, so showing it again is instant. '
      + 'The dot on its chip does the same (shift-click: only this layer).');
    const surface = checkbox('surface', config.surface, (on) => {
      config.surface = on;
      this._styleLayer(layer, layer.colored);
    }, 'Draw this layer’s filled surface.');
    const wire = checkbox('wire', config.wire, (on) => {
      config.wire = on;
      this._styleLayer(layer, layer.colored);
    }, 'Draw this layer’s triangle edges, in its own hue (the yellow layer’s are near-black).');
    pane.showBox = show.box;
    pane.surfaceBox = surface.box;
    pane.wireBox = wire.box;
    row('', show.wrap, surface.wrap, wire.wrap);

    if (!diff) {
      const error = checkbox('error', config.error, (on) => {
        config.error = on;
        this._applyError(layer);
        this._syncErrorToggle();
      }, 'Show the error this render is scored by: the surface '
        + 'colored by its distance from the nearest annotation, and points along '
        + 'every annotation\'s rim colored by their distance from the surface. '
        + 'Gray agrees, amber to red does not; every layer shares one scale. '
        + 'Measured on the surface the layer shows now; if you changed its settings '
        + 'since, the layer is rebuilt first.');
      pane.errorBox = error.box;
      row('', error.wrap);

      pane.retryButton = textButton('Retry', '', 'This layer failed: try its work again.');
      pane.retryButton.hidden = !layer.error;
      // The popover closes with the button that had the keyboard in it: the keyboard goes back to the chip.
      pane.retryButton.addEventListener('click', () => { this._closePop({ focus: true }); this._retry(layer); });

      pane.moveLeft = textButton('◀', '', 'Draw this layer earlier: layers draw in the order of their '
        + 'chips, the first first, and where two surfaces touch the earlier one wins.');
      pane.moveRight = textButton('▶', '', 'Draw this layer later.');
      pane.moveLeft.setAttribute('aria-label', 'Move this layer earlier');
      pane.moveRight.setAttribute('aria-label', 'Move this layer later');
      pane.moveLeft.addEventListener('click', () => this.moveLayer(layer, -1));
      pane.moveRight.addEventListener('click', () => this.moveLayer(layer, 1));
      pane.duplicateButton = textButton('Duplicate', '', 'Add a copy of this layer after the last: the '
        + 'same options, the next color. Change one thing and compare.');
      pane.duplicateButton.addEventListener('click', () => this.addLayer(pane, layer));
      pane.removeButton = textButton('Remove', '', 'Take this layer off the pane. A pane keeps at '
        + 'least one.');
      pane.removeButton.addEventListener('click', () => this.removeLayer(layer));
      row('', pane.moveLeft, pane.moveRight, pane.duplicateButton, pane.removeButton);
      row('', pane.retryButton);
      if (this.optionsPanel) {
        const all = textButton('All options…', '', 'Open the render options of this layer: every '
          + 'knob the 3D render has, for this layer only.');
        all.addEventListener('click', () => {
          this._closePop();
          this._toggleOptions(pane, { keyboard: false, open: true });
        });
        row('', all);
      }
    }
    this._syncEditor(pane);
    if (open) this._openPop(pane, 'editor');
    this._editorFocus(pane, refocus);
  }

  /** The values in the editor follow the layer being edited. */
  _syncEditor(pane) {
    const layer = this._editingLayer(pane);
    const { config } = layer;
    const n = this._layerIndex(layer);
    if (pane.editorTitle) {
      pane.editorTitle.textContent = pane.layers.length > 1
        ? `Layer ${n + 1} · ${this._layerTitle(layer)}` : this._layerTitle(layer);
    }
    if (pane.techSelect && pane.techSelect.value !== config.tech) pane.techSelect.value = config.tech;
    if (pane.colorInput && pane.colorInput.value !== config.color) pane.colorInput.value = config.color;
    if (pane.opacityInput && document.activeElement !== pane.opacityInput) pane.opacityInput.value = config.opacity;
    if (pane.showBox) pane.showBox.checked = config.visible;
    if (pane.surfaceBox) pane.surfaceBox.checked = config.surface;
    if (pane.wireBox) pane.wireBox.checked = config.wire;
    if (pane.errorBox) pane.errorBox.checked = config.error;
    if (pane.retryButton) pane.retryButton.hidden = !layer.error;
    if (pane.moveLeft) pane.moveLeft.disabled = n <= 0;
    if (pane.moveRight) pane.moveRight.disabled = n >= pane.layers.length - 1;
    if (pane.removeButton) pane.removeButton.disabled = pane.layers.length <= 1;
    if (pane.duplicateButton) pane.duplicateButton.disabled = pane.layers.length >= MAX_LAYERS;
  }

  /** The head's boxes, menus and chips follow the pane's facts (values only: nothing is rebuilt). */
  _syncHead(pane) {
    if (pane.shiftBox) pane.shiftBox.checked = pane.shift;
    const diff = this._isDiff(pane);
    if (pane.addLayerItem) {
      const full = pane.layers.length >= MAX_LAYERS;
      pane.addLayerItem.disabled = diff || full;
      pane.addLayerItem.title = diff ? 'A Difference pane takes no more layers.'
        : full ? `A pane holds up to ${MAX_LAYERS} layers.` : '';
    }
    if (pane.annotationsItem) {
      pane.annotationsItem.disabled = diff;
      pane.annotationsItem.textContent = pane.annotations ? '✓ Annotations' : 'Annotations';
    }
    if (pane.maximizeItem) pane.maximizeItem.textContent = this.maximized === pane ? 'Restore' : 'Maximize';
    if (pane.moreButton) pane.moreButton.classList.toggle('active', !!pane.zRange);
    if (pane.zMin && pane.zMax) {
      pane.zMin.value = pane.zRange?.min ?? '';
      pane.zMax.value = pane.zRange?.max ?? '';
    }
    this._syncEditor(pane);
    for (const layer of pane.layers) this._renderChip(layer);
    this._syncArrow(pane);
  }

  /** One chip per layer (colored dot, short name, state) and the annotations overlay's. */
  _buildChips(pane) {
    const host = pane.chipsEl;
    host.replaceChildren(pane.chip);
    pane.el.dataset.layers = String(pane.layers.length);
    for (const layer of pane.layers) {
      const chip = el('span', 'layer-chip');
      const dot = el('button', 'layer-dot');
      dot.type = 'button';
      dot.addEventListener('click', (event) => {
        if (event?.shiftKey) { this.soloLayer(layer); return; }
        this.setLayerVisible(layer, !layer.config.visible);
      });
      const name = el('button', 'layer-name');
      name.type = 'button';
      name.setAttribute('aria-haspopup', 'dialog');
      name.addEventListener('click', (event) => this._openEditor(layer, { keyboard: event?.detail === 0 }));
      const state = el('span', 'layer-state');
      chip.append(dot, name, state);
      host.append(chip);
      layer.chip = { el: chip, dot, name, state };
    }
    pane.annotationsChip = null;
    if (pane.annotations) {
      const chip = el('span', 'layer-chip overlay');
      const dot = el('button', 'layer-dot');
      dot.type = 'button';
      dot.setAttribute('aria-label', 'Show or hide the annotations');
      help(dot, 'The cell’s raw annotations drawn over the layers. Click to hide or show them; the '
        + '+ menu takes them off the pane.');
      dot.addEventListener('click', () => this.setAnnotationsVisible(pane, !pane.annotationsVisible));
      const name = el('span', 'layer-name static', 'annotations');
      chip.append(dot, name);
      host.append(chip);
      pane.annotationsChip = { el: chip, dot };
      this._renderAnnotationsChip(pane);
    }
    for (const layer of pane.layers) this._renderChip(layer);
    // The editor's anchor was one of the chips just replaced: a popover left pointing at a chip that is no
    // longer in the page would give the keyboard to nothing when it closes.
    if (this._pop?.pane === pane && this._pop.which === 'editor') {
      this._pop.anchor = this._popAnchor(pane, 'editor');
      this._pop.anchor?.setAttribute?.('aria-expanded', 'true');
    }
    this._fitChips(pane);
  }

  /** The annotations chip's dot from the pane's facts, in place: filled while shown, a ring while hidden. */
  _renderAnnotationsChip(pane) {
    const chip = pane.annotationsChip;
    if (!chip) return;
    chip.dot.style.background = pane.annotationsVisible ? DISC_COLOR : 'transparent';
    chip.dot.style.borderColor = DISC_COLOR;
    chip.dot.setAttribute('aria-pressed', String(pane.annotationsVisible));
  }

  /** A layer's chip from the layer's facts, in place. */
  _renderChip(layer) {
    const { chip, config, pane } = layer;
    if (!chip) return;
    const many = pane.layers.length > 1;
    const diff = config.tech === 'diff';
    const name = diff ? 'Difference' : shortName(techniqueName(config.options));
    const full = this._layerTitle(layer);
    const k = this._layerIndex(layer) + 1;
    const state = layer.state;
    const failed = state === 'error-build' || state === 'error-measure';
    chip.name.textContent = name;
    // The dot: the layer's color, a hollow ring when hidden, a small ramp while error-painted.
    const color = diff ? '#db3838' : config.color;
    chip.dot.style.background = !config.visible ? 'transparent'
      : (config.error && !diff ? ERROR_GRADIENT : color);
    chip.dot.style.borderColor = color;
    chip.dot.setAttribute('aria-pressed', String(config.visible));
    chip.dot.setAttribute('aria-label', `${config.visible ? 'Hide' : 'Show'} ${name}${many ? ` (layer ${k})` : ''}`);
    help(chip.dot, `${config.visible ? 'Hide' : 'Show'} this layer (it still builds while hidden). `
      + 'Shift-click shows only this layer, and everything again.');
    // The state word beside the name (a pane with several layers only); it is also in the tooltip, because a
    // chip that is crowded cuts it short (see .compare-layers).
    const word = many && state !== 'ready' ? LAYER_STATE_WORDS[state] ?? '' : '';
    help(chip.name, `${many ? `Layer ${k}: ` : ''}${full}, opacity ${config.opacity}`
      + `${config.visible ? '' : ', hidden'}${word ? `, ${word}` : ''}. Click to edit its color, opacity and technique.`
      + (failed && layer.error ? ` It failed: ${layer.error.error?.serverDetail || layer.error.error?.detail
        || layer.error.error?.message || layer.error.error}` : ''));
    chip.state.textContent = word;
    chip.state.hidden = !word;
    chip.el.className = 'layer-chip'
      + `${pane.editing === k - 1 ? ' editing' : ''}${config.visible ? '' : ' off'}`
      + `${failed ? ' failed' : ''}${state === 'stale' ? ' stale' : ''}`;
  }

  // -- popovers ---------------------------------------------------------------

  _popEl(pane, which) {
    return which === 'editor' ? pane.editor : which === 'add' ? pane.addMenu : pane.paneMenu;
  }

  _popAnchor(pane, which) {
    if (which === 'editor') return this._editingLayer(pane).chip?.name ?? pane.addButton;
    return which === 'add' ? pane.addButton : pane.moreButton;
  }

  _togglePop(pane, which, { keyboard = false } = {}) {
    if (this._pop?.pane === pane && this._pop.which === which) this._closePop({ focus: true });
    else this._openPop(pane, which, { keyboard });
  }

  _openPop(pane, which, { keyboard = false } = {}) {
    this._closePop();
    const node = this._popEl(pane, which);
    if (!node) return;
    this._focusPane(pane);
    this._syncHead(pane);
    const anchor = this._popAnchor(pane, which);
    node.hidden = false;
    anchor?.setAttribute?.('aria-expanded', 'true');
    this._pop = { pane, which, el: node, anchor };
    if (which === 'more') this._zPlaceholders(pane);
    this._placePop(pane, node, anchor);
    // Opened from the keyboard, the popover is a long way on in the tab order: go to it.
    if (keyboard) (which === 'editor' ? pane.techSelect : node.children?.[0])?.focus?.();
  }

  /** Close the open popover; with `focus`, give focus back to what opened it. */
  _closePop({ focus = false } = {}) {
    const pop = this._pop;
    if (!pop) return;
    this._pop = null;
    pop.el.hidden = true;
    pop.anchor?.setAttribute?.('aria-expanded', 'false');
    if (focus) pop.anchor?.focus?.();
  }

  _clickAway(event) {
    const pop = this._pop;
    if (!pop) return;
    const target = event?.target;
    if (target && (pop.el.contains?.(target) || pop.anchor?.contains?.(target))) return;
    // The editor's own controls open popups of their own, in the page and not in the editor: the color
    // picker of the Color swatch and the in-page list of a select.  A click in one of them is a click
    // in the editor (it was the picker's palette that closed the editor under it, hiding the controls
    // of the layer being colored).
    if (target && POPUPS_OF_THE_EDITOR.some((selector) => target.closest?.(selector))) return;
    // A control that rebuilt itself while it was clicked (a reorder, a duplicate) is no longer in
    // the page: that click was on the popover, not outside it.
    if (target && target.isConnected === false) return;
    this._closePop();
  }

  /** Under its anchor, pulled back inside the window when it would run off the right edge. */
  _placePop(pane, node, anchor) {
    if (typeof window === 'undefined' || !anchor?.getBoundingClientRect || !pane.head.getBoundingClientRect) return;
    const a = anchor.getBoundingClientRect();
    const h = pane.head.getBoundingClientRect();
    const width = node.offsetWidth || 260;
    let left = a.left - h.left;
    const over = a.left + width - (window.innerWidth - 8);
    if (over > 0) left -= over;
    node.style.left = `${Math.max(8 - h.left, left)}px`;
    // Under the header, as the stylesheet has it; where that runs off the bottom of the window (the bottom
    // row of panes on a short window) above the header if it fits there, else cut short and scrollable.
    node.style.top = '';
    node.style.bottom = '';
    node.style.maxHeight = '';
    node.style.overflowY = '';
    const room = window.innerHeight - 8;
    const box = node.getBoundingClientRect?.();
    if (!box || !box.height || box.bottom <= room) return;
    if (h.top - 8 >= box.height) {
      node.style.top = 'auto';
      node.style.bottom = 'calc(100% + 2px)';
    } else {
      node.style.maxHeight = `${Math.max(120, Math.floor(room - box.top))}px`;
      node.style.overflowY = 'auto';
    }
  }

  /** The Z range boxes show the pane's extent as their hint. */
  _zPlaceholders(pane) {
    const b = pane.renderer.bounds;
    if (!b || b.isEmpty?.() || !pane.zMin) return;
    const round = (v) => (Math.round(v * 10) / 10).toString();
    pane.zMin.placeholder = round(b.min.z);
    pane.zMax.placeholder = round(b.max.z);
  }

  /** The chip's name button: open the editor on that layer (or close it when it is already there). */
  _openEditor(layer, { keyboard = false } = {}) {
    const { pane } = layer;
    const index = this._layerIndex(layer);
    const sameOpen = this._pop?.pane === pane && this._pop.which === 'editor' && pane.editing === index;
    if (sameOpen) { this._closePop({ focus: true }); return; }
    pane.editing = index;
    this._focusPane(pane);
    this._fillEditor(pane);
    // In place: the button that was clicked must stay in the page, or the click that opened
    // the editor would read as a click outside it.
    for (const l of pane.layers) this._renderChip(l);
    this._openPop(pane, 'editor', { keyboard });
    this._syncOptions();
  }

  // -- layer settings -------------------------------------------------------

  setLayerColor(layer, color) {
    const hex = normalizeHex(color);
    if (!hex) return;
    layer.config.color = hex;
    this._styleLayer(layer, layer.colored);
    this._syncHead(layer.pane);
  }

  setLayerOpacity(layer, value, { echo = null } = {}) {
    layer.config.opacity = clampOpacity(value);
    // The box shows what is applied (blank or out of range is put back in range).
    if (echo) echo.value = layer.config.opacity;
    this._styleLayer(layer, layer.colored);
    this._syncHead(layer.pane);
  }

  setLayerVisible(layer, visible) {
    layer.config.visible = !!visible;
    this._styleLayer(layer, layer.colored);
    this._syncHead(layer.pane);
  }

  setAnnotations(pane, on) {
    if (this._isDiff(pane) && on) return;
    pane.annotations = !!on;
    if (!on) pane.annotationsDrawn = null;
    this._buildChips(pane);
    this._syncHead(pane);
    this._applyAnnotations(pane);
  }

  setAnnotationsVisible(pane, visible) {
    pane.annotationsVisible = !!visible;
    pane.renderer.setStyle(ANNOTATION_KEY, { visible: pane.annotationsVisible });
    // In place: the dot that was pressed stays in the page (and keeps the keyboard), and so does the
    // anchor of an editor that is open.
    this._renderAnnotationsChip(pane);
  }

  /** Cut a pane to a range of Z (µm; blank = open on that side) for every layer and the annotations. */
  setZRange(pane, min, max) {
    pane.zRange = normalizeZRange(min, max);
    this._applyZRange(pane);
    this._syncHead(pane);
  }

  _applyZRange(pane) {
    const range = pane.zRange ?? {};
    for (const layer of pane.layers) pane.renderer.setZHide(layer.key, range);
    pane.renderer.setZHide(ANNOTATION_KEY, range);
  }

  /** Make `pane` fill the grid (the others keep everything and wait), or put the grid back. */
  toggleMaximize(pane) {
    this._maximize(this.maximized === pane ? null : pane);
  }

  _maximize(pane) {
    const front = this.maximized;
    this.maximized = pane;
    for (const p of this.panes) {
      const away = !!pane && p !== pane;
      p.el.hidden = away;
      p.el.classList.toggle('maximized', p === pane);
      p.renderer.setPaused(away);
    }
    this.grid.classList.toggle('has-maximized', !!pane);
    this._placeCube();
    this._closePop();
    for (const p of this.panes) this._syncHead(p);
    if (pane) {
      this.onStatus(`Pane ${pane.index + 1} fills the grid. Double-click its number to put it back.`);
    } else if (front) {
      this._settleCameras(front);
      // The grid is back: the line that said one pane fills it is not true any more.
      if (!this.running) this._announce();
      else if (this.record) this.onStatus(this._comparingLine(this.record));
    }
  }

  /**
   * The grid is back after `front` filled it.  A pane that was built while it was out of sight was
   * framed against no size at all and frames itself again, by its renderer's own resize, the first
   * time it has one -- and that late framing is a camera change like an orbit, which Sync copies from
   * the pane that made it into every other, the orbit the user had arranged in `front` included.  The
   * panes take their size here, with Sync held, and then the camera of the pane that was in front.
   */
  _settleCameras(front) {
    const was = this._syncing;
    this._syncing = true;
    try {
      for (const p of this.panes) if (p !== front) p.renderer.updateSize?.();
      if (!this.syncEnabled) return;
      // The pane that was in front, or, when it never drew anything (its build failed while it was in front),
      // the first of the others that has now framed itself: the one camera every pane takes.
      const ref = front.renderer.framed ? front : this.panes.find((p) => p !== front && p.renderer.framed);
      if (ref) for (const p of this.panes) if (p !== ref) this._copyCamera(ref, p);
    } finally {
      this._syncing = was;
    }
  }

  // -- a layer's technique and options -----------------------------------------

  /** The technique list every layer's select shows: the shared presets, then
   * Custom… (options no preset names) and Difference.  A MeshLab preset is
   * disabled, with the reason, on a server without pymeshlab. */
  _fillTechniques(select, value, layer = null) {
    for (const p of RENDER_PRESETS) select.append(new Option(p.short || p.label, p.id));
    select.append(new Option('Custom…', 'custom'));
    select.append(new Option('Difference', 'diff'));
    this._gateTechniques(select, layer);
    select.value = value;
  }

  _gateTechniques(select, layer = null) {
    const caps = this.caps() || {};
    // A Difference is a pane of its own: not an option for a pane with several layers.
    const crowded = !!layer && layer.pane.layers.length > 1;
    for (const option of select.options) {
      const preset = presetById(option.value);
      const off = !!preset?.requires && !!caps[`${preset.requires}Off`];
      const noDiff = option.value === 'diff' && crowded;
      option.disabled = off || noDiff;
      option.title = off ? RENDER_REQUIRES[preset.requires]
        : noDiff ? 'A Difference is a pane of its own: remove this pane’s other layers first.' : '';
    }
  }

  /** The server's capabilities became known (or changed). */
  refreshCaps() {
    for (const pane of this.panes) {
      if (pane.techSelect) this._gateTechniques(pane.techSelect, this._editingLayer(pane));
    }
    const shown = this._optionsLayer();
    if (shown && this.optionsPanel?.open) this.optionsPanel.refreshCaps(this._layerOptions(shown));
  }

  /** The cell the next Compare builds: the one the picker names, else the last one compared. */
  _compareRecord() { return this.cell() || this.record || null; }

  /** The technique named after the options: a preset id, or 'custom'. */
  _deriveTech(layer) {
    const match = matchPreset(layer.config.options);
    layer.config.tech = match ? match.id : 'custom';
    const { pane } = layer;
    if (pane.techSelect && this._editingLayer(pane) === layer) pane.techSelect.value = layer.config.tech;
    this._renderChip(layer);
  }

  /**
   * Closed curve and Hybrid on a cell traced with outlines run past the server's
   * voxel limit at the 0.05 µm default, so a layer whose raster is still that
   * default gets the one the 3D render would use for the cell (1 µm for vessels,
   * 0.25 µm otherwise), written into the options so the form shows what is built.
   * A raster the user chose (`pixelChosen`: a typed 0.05 µm included), or saved as
   * a technique default, is left alone.  One this view chose for another cell is
   * chosen again, and given back to the technique's own when the next cell has no
   * outlines.  True when it changed.
   */
  _seedRaster(layer, record) {
    const { config } = layer;
    if (config.pixelChosen) return false;
    const now = config.options.pixel_size_um;
    const own = config.seededPixel !== null && now === config.seededPixel;
    // An untouched raster is what the technique starts at; a seeded one is judged from there.
    const start = own
      ? techniqueDefaults(config.options.method, config.options.pathway).pixel_size_um
      : now;
    const seeded = curvePixelSeed(record, { ...config.options, pixel_size_um: start });
    if (!own && seeded === null) return false;
    const next = seeded ?? start;
    config.seededPixel = seeded;
    if (next === now) return false;
    config.options = { ...config.options, pixel_size_um: next };
    return true;
  }

  /** The preset select was changed: a technique, Custom… or Difference. */
  _techniquePicked(layer, value) {
    const { pane, config } = layer;
    const wasDiff = config.tech === 'diff';
    this._focusPane(pane);
    if (value === 'custom') {
      // Not a technique to switch to (the name follows the options): it takes
      // you to the options, which is where a combination no preset names is made.
      pane.techSelect.value = config.tech;
      if (config.tech !== 'diff') this._setOptionsOpen(true);
      return;
    }
    if (value === 'diff') {
      if (pane.layers.length > 1) { pane.techSelect.value = config.tech; return; }
      config.tech = 'diff';
    } else {
      const preset = presetById(value);
      if (!preset) { pane.techSelect.value = config.tech; return; }
      config.options = paneOptions(presetOptions(preset));
      config.seededPixel = null;
      config.pixelChosen = false;
      const record = this._compareRecord();
      if (record) this._seedRaster(layer, record);
      this._deriveTech(layer);
    }
    // A Difference pane and a technique pane hold different kinds of
    // content (a distance map over A's mesh; a surface with an error map):
    // leaving either under the other's controls would be wrong.
    const nowDiff = config.tech === 'diff';
    if (wasDiff !== nowDiff) {
      this._clearContent(layer);
      // A Difference draws no annotations of its own.
      if (nowDiff && pane.annotations) this.setAnnotations(pane, false);
      this._buildHead(pane);
    } else {
      this._renderChip(layer);
      this._syncEditor(pane);
    }
    // Every diff pane lists "the other technique panes", and this pane
    // just joined or left that set.
    this._refreshDiffEditors(pane);
    this._touch(layer);
    this._syncOptions();
  }

  // -- the options panel -------------------------------------------------------

  /** The pane the panel shows: the focused one, unless the layer being edited is a Difference (it has no options). */
  _optionsPane() {
    return this._optionsLayer()?.pane ?? null;
  }

  /** The layer the panel edits: the focused pane's editing layer, unless it is a Difference. */
  _optionsLayer() {
    const pane = this.panes[this.focus];
    const layer = pane ? this._editingLayer(pane) : null;
    return layer && layer.config.tech !== 'diff' ? layer : null;
  }

  /** The layer's name for the panel's title and the status line. */
  _paneName(layer) {
    const where = layer.pane.layers.length > 1
      ? `Pane ${layer.pane.index + 1} · layer ${this._layerIndex(layer) + 1}`
      : `Pane ${layer.pane.index + 1}`;
    return `${where} — ${techniqueName(layer.config.options)}`;
  }

  /** The pane's arrow button says whether its options are up. */
  _syncArrow(pane) {
    if (!pane.optsButton) return;
    const on = !!this.optionsPanel?.open && this._optionsPane() === pane;
    pane.optsButton.setAttribute('aria-expanded', String(on));
    pane.optsButton.textContent = on ? '▼' : '▲';
  }

  /**
   * Make `pane` the focused pane: its header takes the accent and the panel
   * follows it (hiding when the layer being edited is a Difference, which has
   * no options).
   */
  _focusPane(pane) {
    if (this.focus === pane.index && this.panes[this.focus] === pane) return;
    this.focus = pane.index;
    this._syncOptions();
  }

  /**
   * The pane's arrow: open the panel on it, or close it when it is already there.
   * Opened from the keyboard, the panel is a long way on in the tab order (after
   * every pane's header), so focus goes to it: Tab then starts at its first control.
   */
  _toggleOptions(pane, { keyboard = false, open = false } = {}) {
    if (this._isDiff(pane) || this._editingLayer(pane).config.tech === 'diff') return;
    if (this._optionsOpen && this.focus === pane.index && !open) {
      this._optionsOpen = false;
    } else {
      this.focus = pane.index;
      this._optionsOpen = true;
    }
    this._syncOptions();
    if ((keyboard || open) && this._optionsOpen) this.optionsPanel?.focusFirst();
  }

  /** The panel's close button or Esc: close it and give focus back to the arrow it came from. */
  _closeFromPanel() {
    const arrow = this.panes[this.focus]?.optsButton;
    this._setOptionsOpen(false);
    arrow?.focus?.();
  }

  /** Open or close the panel on the focused pane. */
  _setOptionsOpen(open) {
    this._optionsOpen = open;
    this._syncOptions();
  }

  /**
   * Bring the panel, the arrows and the focus accent in line with `this.focus`
   * and `this._optionsOpen`; point the panel at the focused pane's editing layer.
   */
  _syncOptions() {
    const panel = this.optionsPanel;
    const layer = this._optionsLayer();
    // The panel belongs to a layer with options: a Difference (or no pane at all,
    // once the pane count shrinks past the focus) closes it, and it stays closed
    // until an arrow opens it again, not when some header is touched later.
    if (!layer) this._optionsOpen = false;
    const open = !!panel && this._optionsOpen;
    if (panel) panel.show(open);
    if (open) {
      panel.bind({
        title: this._paneName(layer), options: this._layerOptions(layer), record: this._compareRecord(),
      });
    }
    for (const p of this.panes) {
      p.el.classList.toggle('focused', p.index === this.focus);
      this._syncArrow(p);
    }
  }

  /** A control of the options panel was edited (`input`: while typing, `change`: committed). */
  _optionsEdited(key, event) {
    const layer = this._optionsLayer();
    if (!layer || !this.optionsPanel?.open) return;
    layer.config.options = paneOptions(this.optionsPanel.read());
    // The pixel size is now the user's, whatever it says: this view no longer owns it.
    if (key === 'pixel_size_um') {
      layer.config.seededPixel = null;
      layer.config.pixelChosen = true;
    } else if (key === 'method') {
      // A new method is seeded (or given back its own raster) as picking the technique would,
      // so the box and the cost chip show what Compare will build.
      const record = this._compareRecord();
      if (record && this._seedRaster(layer, record)) {
        layer.config.options = paneOptions(layer.config.options);
        this.optionsPanel.write(layer.config.options);
      }
    }
    this._deriveTech(layer);
    this.optionsPanel.update({
      options: layer.config.options, record: this._compareRecord(), title: this._paneName(layer),
    });
    // Typing is never rewritten under the cursor; a committed value is shown as it will be built.
    if (event?.type === 'change') this.optionsPanel.write(layer.config.options);
    this._syncEditor(layer.pane);
    this._touch(layer);
  }

  /**
   * A fix from the panel's row for settings that will not build as set: its
   * settings go into the layer the panel edits, and only that one, as if typed
   * (a Voxel size becomes the user's); Compare builds it.
   */
  _fixPicked(set) {
    const layer = this._optionsLayer();
    if (!layer || !this.optionsPanel?.open) return;
    layer.config.options = paneOptions({ ...this._layerOptions(layer), ...set });
    if ('pixel_size_um' in set) {
      layer.config.seededPixel = null;
      layer.config.pixelChosen = true;
    }
    this._deriveTech(layer);
    this.optionsPanel.write(layer.config.options);
    this.optionsPanel.update({
      options: layer.config.options, record: this._compareRecord(), title: this._paneName(layer),
    });
    this._syncEditor(layer.pane);
    this._touch(layer);
  }

  /**
   * Replace a layer's options wholesale (a bridge), and say so.  They come from
   * somewhere that already decided their raster, so it is kept as it is
   * (`pixelChosen`) -- except for Reset, whose options are the technique's
   * untouched ones and are seeded for the cell like a freshly picked technique.
   */
  _setLayerOptions(layer, options, message, { pixelChosen = true } = {}) {
    layer.config.options = paneOptions(options);
    layer.config.seededPixel = null;
    // A raster nobody saw is not a choice: only one the bridged technique shows (a curve
    // method), or one that is not the untouched default, is kept as the user's.
    layer.config.pixelChosen = pixelChosen && (CURVE_METHODS.has(layer.config.options.method)
      || layer.config.options.pixel_size_um !== DEFAULT_RENDER.pixel_size_um);
    const record = this._compareRecord();
    if (record) this._seedRaster(layer, record);
    this._deriveTech(layer);
    this._touch(layer);
    this._syncOptions();
    this.onStatus(`${message} Press Compare to rebuild.`);
  }

  /** A bridge in the panel's More menu, for the layer the panel edits. */
  _bridge(id) {
    const layer = this._optionsLayer();
    if (!layer) return;
    const who = this._where(layer);
    const options = this._layerOptions(layer);
    if (id === 'main') {
      const settings = this.mainSettings?.();
      if (!settings) return;
      this._setLayerOptions(layer, settings, `${who} now uses the 3D render panel’s settings.`);
    } else if (id === 'cell') {
      const render = this._compareRecord()?.render;
      if (!render) {
        this.onStatus('Pick a loaded cell first: its settings are what this would use.', 'error');
        return;
      }
      const cell = this._compareRecord();
      this._setLayerOptions(layer, render, `${who} now uses the settings of c${cell.id} · ${cell.source}.`);
    } else if (id === 'send') {
      if (!this.sendToMain) return;
      this.sendToMain(geometryOf(options), `${who} — ${techniqueName(options)}`);
    } else if (id === 'copy') {
      const targets = this.panes.flatMap((p) => p.layers)
        .filter((l) => l !== layer && l.config.tech !== 'diff');
      let touched = 0;
      for (const target of targets) {
        const { options: next, changed } = copyTuning(options, this._layerOptions(target));
        if (!changed.length) continue;
        target.config.options = next;
        // Only a raster that was copied becomes the user's; one left alone stays as it was.
        if (changed.includes('pixel_size_um')) {
          target.config.seededPixel = null;
          target.config.pixelChosen = true;
        }
        this._deriveTech(target);
        this._touch(target);
        touched += 1;
      }
      const noun = targets.every((l) => l.pane.layers.length === 1) ? 'pane' : 'layer';
      this.onStatus(touched
        ? `${who}’s tuning copied to ${touched} other ${noun}${touched === 1 ? '' : 's'}. `
          + 'Press Compare to rebuild them.'
        : `No other ${noun} shares a knob that differs from ${who.toLowerCase()}’s.`);
    } else if (id === 'reset') {
      this._setLayerOptions(layer, resetOptions(options),
        `${who} is back to the defaults of ${techniqueName(options)}.`, { pixelChosen: false });
    } else if (id === 'save') {
      saveTuning(options);
      this.onStatus(`Saved as your defaults for ${techniqueName(options)}. The 3D render’s picker `
        + 'and the panes use them from now on.');
    }
  }

  /** Full render options for one technique layer: complete, clamped, view fields pinned. */
  _layerOptions(layer) {
    return paneOptions(layer.config.options);
  }

  /** The key of the options this layer's settings would build now. */
  _configKey(layer) {
    return stableKey(pickOptions(this._layerOptions(layer)));
  }

  // -- what each layer is, as facts ------------------------------------------

  /** The layer holds a surface built with the settings it shows now, for the
   * load of the cell that was compared: nothing to rebuild. */
  _meshCurrent(layer) {
    return !!layer.mesh && !!this.record && layer.config.tech !== 'diff'
      && layer.builtGen === this.record.loadGen
      && layer.builtKey === this._configKey(layer);
  }

  /** What the layer shows no longer matches: other settings, or the picker
   * now names another cell or another load of it. */
  _meshStale(layer) {
    if (!layer.mesh || layer.config.tech === 'diff') return false;
    return this._outdated || !this._meshCurrent(layer);
  }

  /** Which two technique layers (the first of two panes) a Difference measures, or why none. */
  _diffSources(layer) {
    const tech = this.panes.filter((p) => !this._isDiff(p)).map((p) => p.layers[0]);
    const src = (i) => {
      const pane = this.panes[i];
      return pane && !this._isDiff(pane) ? pane.layers[0] : tech[0];
    };
    const a = src(layer.config.a);
    const picked = src(layer.config.b);
    const b = picked === a ? tech.find((l) => l !== a) : picked;
    if (!a || !b) {
      return { problem: 'A Difference pane needs two different technique panes, and this '
        + 'layout has fewer. Add panes or change another pane’s technique.' };
    }
    return { a, b };
  }

  _diffKey(a, b) {
    return JSON.stringify([a.pane.index, b.pane.index, a.builtKey, b.builtKey, a.builtGen, b.builtGen]);
  }

  _diffStale(layer) {
    if (!layer.diff) return false;
    if (this._outdated) return true;
    const { a, b, problem } = this._diffSources(layer);
    if (problem) return true;
    return this._diffKey(a, b) !== layer.diffKey || this._meshStale(a) || this._meshStale(b);
  }

  /** The layer's state, from the facts. Work in progress wins, then a failure
   * nobody has dismissed, then a stop; what is left is whether the content
   * on screen still matches the settings. */
  _stateOf(layer) {
    if (layer.task) return layer.task.phase;
    if (layer.error) return layer.error.kind === 'build' ? 'error-build' : 'error-measure';
    if (layer.stopped) return 'stopped';
    if (layer.config.tech === 'diff') {
      if (!layer.diff) return 'idle';
      return this._diffStale(layer) ? 'stale' : 'ready';
    }
    if (!layer.mesh) return 'idle';
    return this._meshStale(layer) ? 'stale' : 'ready';
  }

  _renderState(pane) {
    for (const layer of pane.layers) layer.state = this._stateOf(layer);
    const state = worstState(pane.layers.map((l) => l.state));
    pane.state = state;
    pane.el.dataset.state = state;
    const failedDiff = state === 'error-measure' && pane.layers.some((l) => l.error?.kind === 'diff');
    const chip = CHIPS[failedDiff ? 'error-diff' : state];
    if (pane.chip) {
      if (chip) {
        pane.chip.hidden = false;
        pane.chip.textContent = chip[0];
        pane.chip.className = `compare-chip ${state}${pane.chipDot ? ' dot' : ''}`;
        help(pane.chip, chip[1]);
      } else {
        pane.chip.hidden = true;
      }
    }
    for (const layer of pane.layers) {
      this._renderChip(layer);
      // A legend whose numbers predate the current settings.
      layer.legend.classList.toggle('stale', layer.state === 'stale' && !layer.legend.hidden);
    }
    this._syncEditor(pane);
  }

  _refresh() {
    for (const pane of this.panes) {
      this._renderState(pane);
      // Whatever took a banner down (another layer's card, a layer removed, work stopped), a pane with
      // nothing left to do and a failed layer shows that failure.
      this._reshowFailure(pane);
    }
    this._syncErrorToggle();
    this._renderSummary();
  }

  /** "3 of 6 panes ready, pane 4 measuring, 41 s". */
  _renderSummary() {
    const host = this.summaryEl;
    if (!host) return;
    if (!this.record) { host.textContent = ''; host.classList.remove('has-failures'); return; }
    const by = (...states) => this.panes.filter((p) => states.includes(p.state));
    const parts = [`${by('ready').length} of ${this.panes.length} panes ready`];
    const active = by('building', 'measuring');
    if (active.length) {
      parts.push(active.map((p) => {
        const layer = p.layers.find((l) => l.state === 'building' || l.state === 'measuring');
        return `${this._where(layer).toLowerCase()} ${layer.state}`;
      }).join(', '));
    }
    const waiting = by('queued').length;
    if (waiting) parts.push(`${waiting} waiting`);
    const failed = by('error-build', 'error-measure').length;
    if (failed) parts.push(`${failed} failed`);
    const stale = by('stale').length;
    if (stale) parts.push(`${stale} out of date`);
    const stopped = by('stopped').length;
    if (stopped) parts.push(`${stopped} stopped`);
    if (this.running) parts.push(formatElapsed(Date.now() - this._runStartedAt));
    host.textContent = parts.join(', ');
    host.classList.toggle('has-failures', failed > 0);
  }

  /** A setting of this layer changed: what is shown no longer matches it. */
  _touch(layer) {
    if (layer) layer.stopped = false;
    if (this.record) this.onStatus('Settings changed — press Compare to rebuild.');
    this._refresh();
  }

  // -- the work: a queue per pane ---------------------------------------------

  _newTask(layer, kind, record) {
    this._abortLayer(layer);
    const task = {
      layer, pane: layer.pane, kind, record, phase: 'queued',
      controller: new AbortController(), phaseController: null,
      onlyForMeasure: false,   // started by ticking error: unticking cancels it
      quiet: false,            // canceled on purpose: not a "stopped" layer
    };
    if (!this._tasks.size) this._runStarted();
    layer.task = task;
    layer.stopped = false;
    // A new attempt answers the failure it follows (its banner goes with it).
    this._clearFailure(layer);
    this._tasks.add(task);
    this._refresh();
    return task;
  }

  /** The layer's failure is over: the error, and the banner when the banner is about this layer. */
  _clearFailure(layer) {
    layer.error = null;
    if (layer.pane.bannerLayer === layer) {
      layer.pane.card.clearError();
      layer.pane.bannerLayer = null;
    }
  }

  /**
   * Queue `work(task)` on the layer's pane, behind the work already queued there;
   * at most three panes work at once.  `after` (promises) holds the task until
   * they have settled without taking a place meanwhile.  Never rejects.
   */
  _enqueue(task, work, { after = [] } = {}) {
    const { layer, pane } = task;
    const run = async () => {
      try {
        if (task.controller.signal.aborted || layer.task !== task) return;
        // The cell was unloaded while this waited its turn: the user's own
        // doing, so the work stands down quietly (no request, no banner).
        if (task.record && loadGeneration(task.record.key) === 0) {
          task.quiet = true;
          return;
        }
        await work(task);
      } catch (error) {
        // work() answers for its own failures; anything reaching here is a
        // bug, and it is shown rather than swallowed.
        if (this._owns(layer)) {
          this._failLayer(layer, task.kind === 'diff' ? 'diff' : 'build', error, { what: 'unexpected error' });
        }
      } finally {
        if (layer.task === task) {
          if (task.controller.signal.aborted && !task.quiet) layer.stopped = true;
          layer.task = null;
          pane.card.hide();
        }
        this._tasks.delete(task);
        this._refresh();
        if (!this._tasks.size) this._runEnded();
      }
    };
    const promise = this._queues.enqueue(pane, run, { after });
    this._inflight.add(promise);
    promise.then(() => this._inflight.delete(promise));
    return promise;
  }

  /** Cancel the layer's work on purpose and let go of it at once.  The task
   * may still sit in the queue (or be unwinding); it is skipped when its turn
   * comes, but the layer no longer waits on it: a new task can start now. */
  _abortLayer(layer) {
    const task = layer.task;
    if (!task) return;
    task.quiet = true;
    task.controller.abort();
    layer.task = null;
    // The run that held the card ends only when its poll notices the abort, and by then the
    // layer may be gone, or its task replaced: its end hides the card only for a task the layer
    // still holds.  The card describes work, and this work is over.
    this._settleCard(layer.pane);
  }

  /** As `_abortLayer`, and the run no longer counts the task (the layer or its pane is going away). */
  _dropTask(layer) {
    const task = layer.task;
    if (!task) return;
    this._abortLayer(layer);
    this._tasks.delete(task);
  }

  /** Take the pane's progress card down when no layer of the pane has work left. */
  _settleCard(pane) {
    if (pane.card.running && !pane.layers.some((l) => l.task)) pane.card.hide();
  }

  /** True while the compared cell is still loaded: the polls stand down
   * quietly when the user unloads it. */
  _alive(record) {
    return () => loadGeneration(record.key) !== 0;
  }

  /** The pane's own Stop button: everything this pane has queued or running. */
  _stopPane(pane) {
    for (const layer of pane.layers) layer.task?.controller.abort();
  }

  /** Stop everything: every poll is abandoned and every still-queued job
   * canceled on the server (a running one finishes there, into the cache). */
  stopAll() {
    this._queued = null;
    for (const task of this._tasks) task.controller.abort();
    this.onStatus('Stopping…');
  }

  /** The card's title: a pane with several layers names the one that is working. */
  _taskTitle(task, text) {
    const { layer } = task;
    return layer.pane.layers.length > 1
      ? `Layer ${this._layerIndex(layer) + 1} · ${this._layerTitle(layer)}: ${text}` : text;
  }

  _phase(task, phase, title, working = '') {
    task.phase = phase;
    task.pane.card.show({ title: this._taskTitle(task, title), working });
    // Showing the card took any banner down: no layer's failure is on show now.
    task.pane.bannerLayer = null;
    this._refresh();
  }

  _onJob(pane, job) {
    pane.card.update(job);
    this._renderSummary();
  }

  _runStarted() {
    this._runStartedAt = Date.now();
    clearInterval(this._ticker);
    this._ticker = setInterval(() => this._renderSummary(), 500);
    this.onRunningChange?.(true);
  }

  _runEnded({ announce = true } = {}) {
    const wasRunning = !!this._ticker;
    clearInterval(this._ticker);
    this._ticker = null;
    if (wasRunning) this.onRunningChange?.(false);
    this._renderSummary();
    if (announce && wasRunning) this._announce();
    this._runKind = null;
    this._notes = [];
  }

  /** What the status line says of a Compare that is under way or has just ended. */
  _comparingLine(record) {
    return `Comparing c${record.id} · ${record.source} — drag any pane to orbit all of them.`;
  }

  /** The status line after work ends, from the panes' facts: never a success
   * line over a failure. */
  _announce() {
    const n = this.panes.length;
    const ready = this.panes.filter((p) => p.state === 'ready').length;
    const failed = this.panes.filter((p) => p.error);
    const stopped = this.panes.filter((p) => p.layers.some((l) => l.stopped));
    const rec = this.record;
    if (failed.length) {
      this.onStatus(`${failed.length} of ${n} panes failed (`
        + `${failed.map((p) => `pane ${p.index + 1}`).join(', ')}) — the red banner on `
        + `${failed.length === 1 ? 'it says' : 'each says'} why.`, 'error');
    } else if (stopped.length) {
      this.onStatus(`Stopped — ${ready} of ${n} panes ready.`);
    } else if (this._outdated) {
      // The picker moved on (or the cell was loaded again) while this ran:
      // every pane reads "out of date", whatever it was building.
      this.onStatus('Cell changed — press Compare to rebuild every pane.');
    } else if (this._runKind === 'build' && rec) {
      this.onStatus(this._comparingLine(rec));
    } else {
      this.onStatus(this._notes.at(-1) || `${ready} of ${n} panes ready.`);
    }
  }

  // -- drawing -------------------------------------------------------------

  /** Frame a pane the first time it holds this cell load, and line its
   * camera up with the panes framed before it.  Per pane, not per view: a
   * pane added by a pane-count change, or one whose first build failed and
   * was retried, was never framed by the view-wide flag this replaces. */
  _frame(pane) {
    const rec = this.record;
    if (!rec) return;
    const token = `${rec.key}#${rec.loadGen}`;
    if (pane.framedFor === token || pane.renderer.bounds.isEmpty()) return;
    const ref = this.panes.find((p) => p !== pane && p.framedFor === token
      && !p.renderer.bounds.isEmpty());
    pane.renderer.resetView();
    pane.framedFor = token;
    if (ref) this._copyCamera(ref, pane);
  }

  _copyCamera(from, to) {
    const src = from.renderer;
    const dst = to.renderer;
    const was = this._syncing;
    this._syncing = true;
    try {
      dst.camera.position.copy(src.camera.position);
      dst.controls.target.copy(src.controls.target);
      dst.camera.up.copy(src.camera.up);
      // The orbit frame follows the up, as every change of it in the renderer does: with the
      // old pole, the update below pushed a view down that pole (XY1 after a turn from the 3D
      // view) 1e-6 rad off it, OrbitControls' clamp short of the pole.
      dst._syncControlsUp?.();
      dst.camera.zoom = src.camera.zoom;
      dst.camera.near = src.camera.near;
      dst.camera.far = src.camera.far;
      dst.camera.updateProjectionMatrix();
      dst.controls.update();
    } finally {
      this._syncing = was;
    }
  }

  /** Orbiting one pane orbits the rest (when sync is on). */
  _syncFrom(source, { force = false } = {}) {
    if ((!this.syncEnabled && !force) || this._syncing) return;
    for (const pane of this.panes) {
      if (pane !== source) this._copyCamera(source, pane);
    }
  }

  // -- the view cube ---------------------------------------------------------------

  /**
   * Show or hide the view cube (viewcube.js) in the grid's top-right corner, the same cube
   * as the 3D render's.  It sits in the viewport of whichever pane is there (the last of
   * the top row, or the pane that fills the grid), shows the focused pane's camera, and
   * turns every pane.  With Sync cameras the focused pane turns and Sync carries the others
   * along, frame by frame; without it each pane turns to the same direction about its own
   * pivot, keeping its own zoom, and a drag on the cube orbits each by the same amount.
   * The house is Reset view: the focused pane's, followed by the rest with Sync, every
   * pane's own without it.
   */
  setViewCube(on) {
    if (!on && !this.viewCube) return;
    if (!this.viewCube) {
      const lead = () => this._cubeLead().renderer;
      const view = {
        get flipZ() { return lead().flipZ; },
        viewFrame: () => lead().viewFrame(),
        turnTo: (dir, up) => this._cubeTurn(dir, up),
        spinBy: (yaw, pitch) => { for (const r of this._cubeTargets()) r.spinBy(yaw, pitch); },
        endSpin: () => { for (const r of this._cubeTargets()) r.endSpin(); },
      };
      this.viewCube = new ViewCube(this.grid, view, { onHome: () => this._cubeHome() });
    }
    this.viewCube.setVisible(on);
    this._placeCube();
  }

  /** The pane whose camera the cube shows: the one filling the grid, else the focused
   * pane, else the first with something drawn. */
  _cubeLead() {
    if (this.maximized) return this.maximized;
    const drawn = (p) => p && !p.renderer.bounds.isEmpty();
    const focused = this.panes[this.focus];
    return drawn(focused) ? focused : (this.panes.find(drawn) ?? focused ?? this.panes[0]);
  }

  /** The renderers a turn of the cube moves itself: with Sync cameras the shown pane alone
   * (Sync copies it into the rest), without it every pane with something drawn. */
  _cubeTargets() {
    if (this.syncEnabled) return [this._cubeLead().renderer];
    return this.panes.filter((p) => !p.renderer.bounds.isEmpty()).map((p) => p.renderer);
  }

  async _cubeTurn(dir, up) {
    const lead = this._cubeLead();
    const arrived = await Promise.all(this._cubeTargets().map((r) => r.turnTo(dir, up)));
    // The glide's last step is too small for the controls to call it a change, so Sync never
    // copied the arrival itself (the other pane stopped 1.1e-6 rad short of an edge view,
    // measured): the other panes take it here.
    if (this.syncEnabled && this.panes.includes(lead)) this._syncFrom(lead);
    return arrived.some(Boolean);
  }

  _cubeHome() {
    if (!this.syncEnabled) {
      for (const p of this.panes) p.renderer.resetView();
      return;
    }
    const lead = this._cubeLead();
    lead.renderer.resetView();
    this._syncFrom(lead);
  }

  /** Put the cube in the viewport of the pane at the grid's top right; that pane's Position
   * shift legend moves to its bottom right (app.css). */
  _placeCube() {
    const cube = this.viewCube;
    if (!cube) return;
    const host = this.maximized
      ?? this.panes[Math.min(COLUMNS[this.panes.length] ?? 1, this.panes.length) - 1];
    for (const p of this.panes) p.viewport.classList.toggle('has-viewcube', cube.visible && p === host);
    if (host) cube.attach(host.viewport);
  }

  /** The Sync cameras checkbox.  Turning it on lines everything up with the
   * first pane that has a surface, rather than waiting for the next drag. */
  setSync(on) {
    this.syncEnabled = !!on;
    if (!on) return;
    const ref = this.panes.find((p) => !p.renderer.bounds.isEmpty());
    if (ref) this._syncFrom(ref, { force: true });
  }

  /**
   * What a layer looks like, in its renderer: surface and wire on or off, the
   * wire's hue, color (a plain surface only: a map has its own), opacity,
   * visibility, its place in the draw order and the pane's Z range.  The order
   * is the layer's index in the pane, so the first layer draws first and wins a
   * tie; nothing here touches the camera or rebuilds a mesh.  A layer carrying
   * vertex colors keeps its wireframe in those colors.
   */
  _styleLayer(layer, colored = false) {
    const { pane, config, key } = layer;
    const r = pane.renderer;
    if (!colored) r.setWireframeStyle(key, { color: wireColorFor(config.color), opacity: 1 });
    r.setSurface(key, config.surface);
    r.setWireframe(key, config.wire);
    r.setStyle(key, { ...(colored ? {} : { color: config.color }), opacity: config.opacity, visible: config.visible });
    r.setLayerOrder(key, this._layerIndex(layer));
    r.setZHide(key, pane.zRange ?? {});
  }

  /** Draw the layer's mesh (flat, or with `vertexColors`) and style it. */
  _drawSurface(layer, vertexColors = null, { keepCamera = false } = {}) {
    const { pane, config } = layer;
    const saved = keepCamera ? pane.renderer.cameraState() : null;
    pane.renderer.setMesh(layer.key, layer.mesh, vertexColors
      ? { vertexColors, opacity: config.opacity }
      : { color: config.color, opacity: config.opacity });
    layer.colored = !!vertexColors;
    this._styleLayer(layer, layer.colored);
    if (saved) pane.renderer.restoreCamera(saved);
  }

  /** The surface this layer holds, plain: no error colors, no map. */
  _showPlain(layer) {
    const { pane } = layer;
    pane.renderer.removePoints(layer.key);
    this._drawSurface(layer, null, { keepCamera: true });
    this._legendHide(layer);
    layer.painted = null;
    this._repaintErrors();
  }

  /** Remember a freshly built mesh with the options that produced it. */
  _setMesh(layer, mesh, request, record) {
    layer.mesh = mesh;
    layer.builtRequest = request;
    layer.builtOptions = pickOptions(request);
    layer.builtKey = stableKey(layer.builtOptions);
    layer.builtGen = record.loadGen;
    layer.deviation = null;   // measured on the mesh that was just replaced
  }

  /** Everything this layer shows, and what it knew about it. */
  _clearContent(layer) {
    this._abortLayer(layer);
    layer.pane.renderer.removeAll(layer.key);
    layer.mesh = null;
    layer.builtRequest = null;
    layer.builtOptions = null;
    layer.builtKey = null;
    layer.builtGen = null;
    layer.deviation = null;
    layer.diff = null;
    layer.diffSources = null;
    layer.diffKey = null;
    layer.colored = false;
    layer.error = null;
    layer.stopped = false;
    layer.legendRefs = null;
    layer.painted = null;
    layer.legend.replaceChildren();
    layer.legend.classList.remove('stale');
    this._legendHide(layer);
    if (layer.pane.bannerLayer === layer) { layer.pane.card.clearError(); layer.pane.bannerLayer = null; }
    this._settleCard(layer.pane);
  }

  // -- the legend -----------------------------------------------------------

  _legendHide(layer) {
    layer.legend.hidden = true;
    this._legendLayout(layer.pane);
  }

  _legendShow(layer) {
    layer.legend.hidden = false;
    this._legendLayout(layer.pane);
  }

  /** The pane's legend shows while some layer has a map; the ramp once, on the first section. */
  _legendLayout(pane) {
    pane.legend.hidden = pane.layers.every((l) => l.legend.hidden);
    let first = true;
    for (const layer of pane.layers) {
      const lead = !layer.legend.hidden && first;
      layer.legend.classList.toggle('lead', lead);
      if (!layer.legend.hidden) first = false;
    }
  }

  // -- error maps ----------------------------------------------------------

  /** Every layer in every pane. */
  _allLayers() { return this.panes.flatMap((p) => p.layers); }

  /** The color scale every error layer shares: pinned, else the largest the
   * measured layers suggest, so the same color means the same microns in all. */
  _errorScale() {
    if (this.errorScale) return this.errorScale;
    const scales = this._allLayers().filter((l) => l.config.error && l.deviation)
      .map((l) => l.deviation.scale_um);
    return scales.length ? Math.max(...scales) : 1;
  }

  /** The toolbar's Error toggle follows the layers: on when every solid layer has error, mixed when some do. */
  _syncErrorToggle() {
    const toggle = this.errorToggle;
    if (!toggle) return;
    const solid = this._allLayers().filter((l) => l.config.tech !== 'diff');
    const on = solid.filter((l) => l.config.error).length;
    toggle.checked = solid.length > 0 && on === solid.length;
    toggle.indeterminate = on > 0 && on < solid.length;
  }

  /** The toolbar's Error toggle: error paint on every solid layer in every pane, or off again. */
  setErrorAll(on) {
    for (const pane of this.panes) {
      for (const layer of pane.layers) {
        if (layer.config.tech === 'diff' || layer.config.error === !!on) continue;
        layer.config.error = !!on;
        this._applyError(layer);
      }
      this._syncEditor(pane);
    }
    this._syncErrorToggle();
  }

  /** The error checkbox was ticked or cleared. */
  _applyError(layer) {
    if (layer.config.tech === 'diff') return;
    const task = layer.task;
    if (!layer.config.error) {
      // Off: stop a measurement under way (and a rebuild that existed only
      // to measure), and bring the plain surface back.
      if (task?.phase === 'measuring') task.phaseController?.abort();
      else if (task?.onlyForMeasure) this._abortLayer(layer);
      if (layer.mesh) this._showPlain(layer);
      this._refresh();
      return;
    }
    const record = this.record;
    // Nothing compared yet: Compare measures every layer that has it ticked.
    if (!record) return;
    // The layer has work of its own queued or building; it measures when it
    // gets there, because the box is ticked by then.
    if (task && !task.controller.signal.aborted && !task.phaseController?.signal.aborted) return;
    if (layer.error?.kind === 'measure') this._clearFailure(layer);
    if (this._meshCurrent(layer) && layer.deviation) {
      this._repaintErrors();
      this._refresh();
      return;
    }
    this._runKind = this._runKind || 'pane';
    this._scheduleLayer(layer, record, { reason: layer.mesh ? 'stale-tick' : 'tick' });
  }

  /** Paint every layer that has a map and whose picture is not already that
   * map at the current scale.  A layer whose map and scale have not changed is
   * left alone: repainting rebuilds its geometry, and its legend holds a scale
   * box that may be under the user's fingers. */
  _repaintErrors(except = null) {
    const scale = this._errorScale();
    for (const layer of this._allLayers()) {
      if (layer === except || !layer.config.error || !layer.deviation || !layer.mesh) continue;
      if (layer.painted?.deviation === layer.deviation && layer.painted.scale === scale) continue;
      this._paintError(layer);
    }
  }

  _paintError(layer) {
    const { pane } = layer;
    const dev = layer.deviation;
    const scale = this._errorScale();
    // The layer's own mesh, colored by the measured per-vertex distances.  The
    // distances are not attached to `layer.mesh` itself: setMesh would color
    // any mesh that has `vertex_data` by depth, and the plain surface must
    // stay in its layer color whenever the map is off.
    this._drawSurface(layer, errorColors(dev.vertex_um, scale), { keepCamera: true });
    pane.renderer.setPoints(layer.key, dev.rim_xyz, errorColors(dev.rim_um, scale));
    // The points are a new object, visible until told otherwise: the style just applied to the
    // surface did not reach them, so a hidden layer would show its rim points on nothing.
    pane.renderer.setStyle(layer.key, { visible: layer.config.visible });
    layer.painted = { deviation: dev, scale };
    this._legendShow(layer);
    this._errorLegend(layer, dev, scale);
    this._renderState(pane);
  }

  /** The error map's legend: built once per layer and updated in place, so a
   * repaint (another layer finishing) never replaces a scale box being edited. */
  _errorLegend(layer, dev, scale) {
    const d = dev.deviation;
    const mid = this.errorScale ? 'render ↔ annotations, µm (pinned)' : 'render ↔ annotations, µm';
    const statsText = d
      ? `surface→annotation ${d.surface_um.toFixed(3)}`
        + ` · annotation→surface ${d.annotation_um.toFixed(3)} · mean ${d.mean_um.toFixed(3)}`
        + ` · p95 ${d.p95_um.toFixed(2)} · max ${d.max_um.toFixed(2)} µm`
      : '';
    const statsHelp = d
      ? 'The surface is colored by each vertex\'s distance to the nearest annotation '
        + '(a disc in its section plane, or a traced outline); its area average is surface→annotation. '
        + 'The points are samples along every annotation\'s rim, colored by their distance to the '
        + 'surface; their average is annotation→surface. '
        + (dev.rim_stride > 1
          ? `Every ${dev.rim_stride}th of ${dev.n_rims.toLocaleString()} rim points is drawn; the numbers use all of them. `
          : `${dev.n_rims.toLocaleString()} rim points. `)
        + `Against ${d.n_discs} discs` + (d.n_curves ? ` + ${d.n_curves} outlines.` : '.')
        + ' All distances are absolute.'
      : '';
    let refs = layer.legendRefs;
    if (!refs || refs.kind !== 'error' || !!refs.stats !== !!d) {
      layer.legend.replaceChildren();
      const bar = document.createElement('div');
      bar.className = 'diff-bar';
      bar.style.background = ERROR_GRADIENT;
      const labels = document.createElement('div');
      labels.className = 'diff-labels';
      const pin = document.createElement('input');
      pin.type = 'number';
      pin.min = 0.01; pin.step = 0.05;
      pin.className = 'error-scale';
      help(pin, 'The distance drawn full red, in every error layer. Clear it to go back to automatic '
        + '(the largest 98th percentile among the measured layers).');
      pin.addEventListener('change', () => {
        const v = Number(pin.value);
        this.errorScale = Number.isFinite(v) && v > 0 ? v : null;
        this._repaintErrors();
      });
      const right = document.createElement('span');
      right.append(pin);
      const left = document.createElement('span');
      left.textContent = '0';
      const middle = document.createElement('span');
      labels.append(left, middle, right);
      layer.legend.append(bar, labels);
      let stats = null;
      if (d) {
        stats = document.createElement('div');
        stats.className = 'diff-stats';
        layer.legend.append(stats);
      }
      refs = { kind: 'error', pin, middle, stats };
      layer.legendRefs = refs;
    }
    // The box being typed in keeps what the user typed.
    if (typeof document === 'undefined' || document.activeElement !== refs.pin) {
      refs.pin.value = scale.toFixed(2);
    }
    refs.middle.textContent = mid;
    if (refs.stats) {
      refs.stats.textContent = statsText;
      help(refs.stats, statsHelp);
      // Which layer a line is about, when the pane has several.
      const many = layer.pane.layers.length > 1;
      refs.stats.style.borderLeft = many ? `3px solid ${layer.config.color}` : '';
      refs.stats.style.paddingLeft = many ? '5px' : '';
    }
  }

  // -- annotations (the overlay) -----------------------------------------------

  /** The cell's annotations and closed-curve outlines, fetched once per load
   * and shared by every pane that asks while the fetch is in the air. */
  _loadAnnotations(record) {
    const key = `${record.key}#${record.loadGen}`;
    if (this._annotations?.key !== key) {
      // Closed curves are drawn as their traced outlines; [] for a disc-only cell.
      const promise = Promise.all([
        api.nodes(record.source, record.id),
        api.closedCurves(record.source, record.id).catch(() => []),
      ]).then(([nodes, outlines]) => ({ nodes, outlines }));
      this._annotations = { key, promise };
      // A failed fetch is not remembered: the next tick asks again.
      promise.catch(() => { if (this._annotations?.key === key) this._annotations = null; });
    }
    return this._annotations.promise;
  }

  /** Which load of which cell the annotations on screen are of (or would be). */
  _annotationsToken(record) {
    return record ? `${record.key}#${record.loadGen}` : null;
  }

  /** What a pane's layers and overlays look like, without refetching anything. */
  async _applyDisplay(pane) {
    for (const layer of pane.layers) this._styleLayer(layer, layer.colored);
    // Never throws, and is not waited for, so it cannot hold up the annotations.
    this._applyShifts(pane);
    await this._applyAnnotations(pane);
  }

  /** The annotations overlay: drawn once per cell load, taken away when off. */
  async _applyAnnotations(pane) {
    const record = this.record;
    if (!pane.annotations || !record || this._isDiff(pane)) {
      pane.renderer.removeDiscs(ANNOTATION_KEY);
      pane.annotationsDrawn = null;
      return;
    }
    // Still wanted when the fetch comes back?  The pane may have been
    // dropped (pane count), turned into a Difference, un-ticked or moved
    // to another cell meanwhile.
    const wanted = () => this.panes.includes(pane) && !this._isDiff(pane)
      && pane.annotations && this.record === record;
    let annotations;
    try {
      annotations = await this._loadAnnotations(record);
    } catch (error) {
      if (!wanted()) return;
      // Said on the pane, and the overlay taken off so it matches the picture.
      // While a layer has a job on the card, the banner would be wiped by
      // the job's next phase, so then the status line says it.
      pane.annotations = false;
      pane.annotationsDrawn = null;
      this._buildChips(pane);
      this._syncHead(pane);
      const title = `Pane ${pane.index + 1}: the annotations could not be loaded`;
      if (pane.layers.some((l) => l.task)) {
        this.onStatus(`${title}: ${error?.detail || error?.message || error}`, 'error');
      } else {
        pane.bannerLayer = null;
        pane.card.fail({
          title,
          error,
          onRetry: () => this.setAnnotations(pane, true),
          onDismiss: () => this._refresh(),
        });
      }
      return;
    }
    if (!wanted()) return;
    const token = this._annotationsToken(record);
    if (pane.annotationsDrawn !== token) {
      pane.renderer.setDiscs(ANNOTATION_KEY, annotations.nodes, {
        color: DISC_COLOR, opacity: 0.85, stroke: 0.06, outlines: annotations.outlines,
      });
      pane.annotationsDrawn = token;
    }
    pane.renderer.setStyle(ANNOTATION_KEY, { visible: pane.annotationsVisible });
    pane.renderer.setZHide(ANNOTATION_KEY, pane.zRange ?? {});
  }

  // -- Difference ---------------------------------------------------------------

  /** A Difference layer's sources (two panes), scale and number boxes, in the editor. */
  _diffControls(layer, row) {
    const { config, pane } = layer;
    const others = this.panes.filter((p) => p !== pane && !this._isDiff(p));
    const selects = {};
    const pick = (key) => {
      const select = document.createElement('select');
      help(select, key === 'a'
        ? 'Pane whose mesh is measured and colored'
        : 'Pane measured against');
      for (const p of others) {
        select.append(new Option(`pane ${p.index + 1}`, p.index));
      }
      if (others.some((p) => p.index === config[key])) select.value = config[key];
      else if (others.length) config[key] = Number(select.value);
      select.addEventListener('change', () => {
        config[key] = Number(select.value);
        spread();
        this._touch(layer);
      });
      selects[key] = select;
      return select;
    };
    // A pane measured against itself is 0 everywhere: each side leaves the
    // other's choice out of its list while there is another to pick.
    const spread = () => {
      if (others.length < 2) return;
      for (const [key, other] of [['a', 'b'], ['b', 'a']]) {
        for (const option of selects[key].options) {
          option.disabled = Number(option.value) === config[other];
        }
      }
    };
    const a = pick('a');
    const b = pick('b');
    if (others.length > 1 && config.a === config.b) {
      config.b = others.find((p) => p.index !== config.a).index;
      selects.b.value = config.b;
    }
    spread();
    const sources = el('span', 'compare-param');
    sources.append(a, document.createTextNode(' − '), b);
    row('', sources);

    const scaleLabel = el('label', 'compare-param');
    help(scaleLabel, 'Color scale: full red/blue at ± this many µm. Blank = automatic (98th percentile).');
    const scale = document.createElement('input');
    scale.type = 'number';
    scale.min = 0.01; scale.step = 0.05;
    scale.placeholder = 'auto';
    if (config.scale != null) scale.value = config.scale;
    scale.addEventListener('change', () => {
      // Zero or less would divide by zero in the colors; blank is automatic.
      const v = Number(scale.value);
      config.scale = scale.value !== '' && Number.isFinite(v) && v > 0 ? v : null;
      scale.value = config.scale ?? '';
      if (layer.diff) this._paintDiff(layer, { keepCamera: true });
    });
    scaleLabel.append(document.createTextNode('±µm '), scale);
    row('', scaleLabel);
  }

  _paintDiff(layer, { keepCamera = false } = {}) {
    const { pane, config } = layer;
    const diff = layer.diff;
    if (!diff || !layer.mesh) return;
    const scale = config.scale ?? diff.scale_um;
    const colors = divergingColors(diff.distance_um, scale);
    // A Difference pane shows no rings or error points of its own; any left
    // from when it was a technique pane would be drawn over the map.
    pane.renderer.removeDiscs(ANNOTATION_KEY);
    pane.renderer.removePoints(layer.key);
    this._drawSurface(layer, colors, { keepCamera });

    const spacing = diff.sample_spacing_um;
    this._legendShow(layer);
    layer.legend.replaceChildren();
    layer.legendRefs = null;
    const bar = document.createElement('div');
    bar.className = 'diff-bar';
    const labels = document.createElement('div');
    labels.className = 'diff-labels';
    for (const text of [`−${scale.toFixed(2)}`, 'inside ← µm → outside', `+${scale.toFixed(2)}`]) {
      const span = document.createElement('span');
      span.textContent = text;
      labels.append(span);
    }
    const stats = document.createElement('div');
    stats.className = 'diff-stats';
    stats.textContent = `mean |d| ${diff.mean_abs_um.toFixed(3)} · RMS ${diff.rms_um.toFixed(3)}`
      + ` · p95 ${diff.p95_abs_um.toFixed(3)} · max ${diff.max_abs_um.toFixed(2)} µm`
      + ` (resolution ~${spacing.toFixed(3)} µm)`;
    layer.legend.append(bar, labels, stats);

    // Each source rendering held to the cell's own annotations — the discs
    // (and any traced outlines) are the closest thing to ground truth.
    if (diff.deviation_a || diff.deviation_b) {
      const s = layer.diffSources;
      const fmt = (d) => (d ? d.mean_um.toFixed(2) : 'n/a');
      const dev = document.createElement('div');
      dev.className = 'diff-stats';
      dev.textContent = `vs annotations: pane ${s.a} ${fmt(diff.deviation_a)}`
        + ` · pane ${s.b} ${fmt(diff.deviation_b)} µm mean |d|`;
      const detail = (name, d) => (d
        ? `Pane ${name}: mean ${d.mean_um.toFixed(3)} µm `
          + `(surface→annotation ${d.surface_um.toFixed(3)}, `
          + `annotation→surface ${d.annotation_um.toFixed(3)}), `
          + `p95 ${d.p95_um.toFixed(2)}, max ${d.max_um.toFixed(2)} µm; `
          + `unsupported volume ≈ ${Math.round(d.excess_volume_um3).toLocaleString()} µm³ `
          + `against ${d.n_discs} discs`
          + (d.n_curves ? ` + ${d.n_curves} outlines` : '') + '.'
        : '');
      help(dev, [
        detail(s.a, diff.deviation_a),
        detail(s.b, diff.deviation_b),
        'Annotations are treated as ground truth. All distances are absolute'
        + ' — an annotation poking outside the surface counts the same as'
        + ' surface ballooning past an annotation; errors never cancel.',
      ].filter(Boolean).join('\n'));
      layer.legend.append(dev);
    }
    this._renderState(pane);
  }

  // -- Position shift --------------------------------------------------------

  /** The panes whose box is ticked. */
  _shiftPanes() { return this.panes.filter((p) => p.shift); }

  /**
   * The compared cell is another cell, or another load of it: the shared settings
   * start from its own display (unless the user has changed them here), a new cell
   * starts a new slice, and every ticked pane draws the new copy's annotations.
   */
  _shiftRecordChanged(record) {
    const sh = this._shift;
    if (!sh.touched) sh.display = normalizeShiftDisplay({ ...record.display, showShift: true });
    if (sh.recordKey !== record.key) sh.slices = new SliceState();
    sh.recordKey = record.key;
    sh.sig = null;
    sh.promise = null;
    sh.layer = null;
    for (const pane of this._shiftPanes()) this._applyShifts(pane);
    this._shiftStrip();
  }

  /** The layer for the compared cell under the shared settings: built once per (cell, load, settings). */
  _shiftLayerFor(record) {
    const sh = this._shift;
    const sig = `${record.key}#${record.loadGen}|${JSON.stringify(sh.display)}`;
    if (sh.sig !== sig) {
      sh.sig = sig;
      sh.promise = this._loadAnnotations(record).then(({ nodes, outlines }) => {
        const layer = buildLayer({
          nodes, outlines, summary: record.summary, display: sh.display,
          inverted: false, cellColor: record.color, hasTransforms: this._hasTransforms(record),
        });
        if (sh.sig === sig) { sh.layer = layer; sh.nodes = nodes; }
        return layer;
      });
      // A failed fetch is not remembered: the next ask goes again.
      sh.promise.catch(() => { if (sh.sig === sig) { sh.sig = null; sh.promise = null; } });
    }
    return sh.promise;
  }

  /** Whether the compared cell's volume has its current section transforms read here. */
  _hasTransforms(record) {
    return !record || this.volumeInfo(record.source)?.has_transforms !== false;
  }

  /** The pane's legend for the layer it draws (or none). */
  _shiftLegend(pane, layer) {
    if (!layer) { pane.shiftLegend?.update([]); return; }
    pane.shiftLegend ??= new ShiftLegend(pane.viewport);
    pane.shiftLegend.update(legendBlocks([{ id: this.record?.id, layer, cellColor: this.record?.color }]));
  }

  /** The slice's center in the compared cell's volume (null until it has one). */
  _shiftCenter() {
    const { record } = this;
    return record && this._shift.slices.has(record.source)
      ? this._shift.slices.get(record.source) : null;
  }

  /**
   * Draw (or take away) the shared layer in one pane.  Never throws and is not
   * waited for by the pane's own work; the annotations come from the fetch the
   * annotations overlay shares.
   */
  async _applyShifts(pane) {
    if (!this.panes.includes(pane)) return;
    const { record } = this;
    if (!pane.shift || !record) {
      if (pane.shiftLayer !== null) pane.renderer.removeShifts(SHIFT_KEY);
      pane.shiftLayer = null;
      this._shiftLegend(pane, null);
      this._shiftStrip();
      return;
    }
    let layer;
    try {
      layer = await this._shiftLayerFor(record);
    } catch (error) {
      if (!this.panes.includes(pane) || !pane.shift || this.record !== record) return;
      // Said on the status line, and the box un-ticked so it matches the picture.
      pane.shift = false;
      this._syncHead(pane);
      this.onStatus(`Pane ${pane.index + 1}: the annotations could not be loaded: `
        + `${error?.detail || error?.message || error}`, 'error');
      this._shiftStrip();
      return;
    }
    // Other settings, another cell or another pane count came meanwhile: that call draws.
    if (!this.panes.includes(pane) || !pane.shift || this.record !== record
        || this._shift.layer !== layer) return;
    const center = sliceCenters([{ source: record.source, layer }], this._shift.slices)
      .get(record.source);
    // A layer object is one cell, load and set of settings: the pane that already
    // draws it only follows the slice (its buffers are not built again).
    if (pane.shiftLayer === layer) syncSlice(pane.renderer, SHIFT_KEY, layer, center);
    else drawLayer(pane.renderer, SHIFT_KEY, layer, center);
    pane.shiftLayer = layer;
    this._shiftLegend(pane, layer);
    this._shiftStrip();
  }

  /** A pane's shift box was ticked or cleared. */
  _shiftToggled(pane) {
    this._applyShifts(pane);
    this._shiftStrip();
  }

  /** Move every ticked pane's window and plane to the shared slice. */
  _shiftSync() {
    const { record } = this;
    const layer = this._shift.layer;
    if (record && layer && !layer.empty) {
      const center = this._shiftCenter();
      for (const pane of this._shiftPanes()) syncSlice(pane.renderer, SHIFT_KEY, layer, center);
    }
    this._shiftStrip();
  }

  /** The strip is shown while some pane draws the layer; its controls follow the shared settings. */
  _shiftStrip() {
    const host = this.shiftHost;
    if (!host) return;
    const on = this._shiftPanes().length > 0;
    host.hidden = !on;
    if (!on) return;
    if (!this._shift.strip) this._shift.strip = buildShiftStrip(host, this._shiftModel());
    else this._shift.strip.update();
  }

  /** What the strip acts through. */
  _shiftModel() {
    const sh = this._shift;
    const range = () => {
      const layer = sh.layer;
      return layer && !layer.empty
        ? { min: layer.sectionRange.min, max: layer.sectionRange.max, fallback: layer.defaultCenter() }
        : null;
    };
    return {
      display: () => sh.display,
      // A pair the thresholds in force would hide gets thresholds that show it.
      thresholdsFor: (pair) => (sh.nodes
        ? thresholdsForPair(sh.thresholds, 'strip', sh.nodes, pair, sh.display) : {}),
      layer: () => sh.layer,
      center: () => this._shiftCenter(),
      pairs: () => pairsFor(this._hasTransforms(this.record)),
      set: (changes) => {
        sh.display = normalizeShiftDisplay({ ...sh.display, ...changes, showShift: true });
        sh.touched = true;
        for (const pane of this._shiftPanes()) this._applyShifts(pane);
        this._shiftStrip();
      },
      setCenter: (section) => {
        const r = range();
        if (!r || !this.record) return;
        sh.slices.set(this.record.source, section, r);
        this._shiftSync();
      },
      step: (delta) => {
        const r = range();
        if (!r || !this.record) return;
        sh.slices.step(this.record.source, delta, r, r.fallback);
        this._shiftSync();
      },
    };
  }

  /** A slice key on any pane steps the shared slice, so every ticked pane follows. */
  _sliceKey(event, pane) {
    const { record } = this;
    const layer = this._shift.layer;
    if (!record || !layer || layer.empty || !this._shiftPanes().length) return false;
    const moved = applySliceKey(event, [{ source: record.source, layer }], this._shift.slices,
      { selected: false, flipZ: pane.renderer.flipZ });
    if (!moved) return false;
    this._shiftSync();
    return true;
  }

  // -- failures ------------------------------------------------------------

  /** Show a failure on the layer's pane, as a banner that stays until dismissed.
   * The layer keeps its failed chip until it is dismissed, retried or rebuilt,
   * even after another layer's progress card has taken the banner's place. */
  _failLayer(layer, kind, error, { what = 'failed', note = '', retry = null } = {}) {
    const { pane } = layer;
    // What the banner says is kept, to put it back (see _reshowFailure); its "Pane 2 layer 3" is made again
    // each time, since the layer may have moved.
    layer.error = { kind, error, shown: { what, note, retry } };
    pane.bannerLayer = layer;
    pane.card.fail({
      title: `${this._where(layer)}: ${what}`,
      error,
      note,
      onRetry: retry || (() => this._retry(layer)),
      onDismiss: () => {
        layer.error = null;
        if (pane.bannerLayer === layer) pane.bannerLayer = null;
        // The refresh gives the banner to the pane's next failed layer, if it has one.
        this._refresh();
        // The status line named this failure: say how things stand now.
        if (!this.running) this._announce();
      },
    });
    this._refresh();
  }

  /**
   * A pane has nothing left to do and a layer of it is still failed with no banner on show: the progress
   * card of a layer that worked after it took the banner's place (showing a card clears the banner), or
   * the layer whose banner it was is gone or was dismissed.  The status line and the pane's chip both send
   * people to "the red banner", so the first failed layer's is put back, with its Retry and Dismiss; the
   * other failed layers say why in their chips, and each has its turn as the banner is dismissed.
   */
  _reshowFailure(pane) {
    if (pane.card.running || pane.card.failed) return;        // a card or a banner is up
    if (pane.layers.some((l) => l.task)) return;              // work is coming or going: the card has the place
    const layer = pane.layers.find((l) => l.error);
    if (layer) this._failLayer(layer, layer.error.kind, layer.error.error, layer.error.shown);
  }

  _retry(layer) {
    const record = this.record;
    if (!record) return;
    this._runKind = this._runKind || 'pane';
    const kind = layer.error?.kind;
    this._clearFailure(layer);
    if (layer.config.tech === 'diff') {
      this._scheduleDiff(layer, record);
    } else {
      if (kind === 'measure') {
        layer.config.error = true;
        this._syncEditor(layer.pane);
      }
      this._scheduleLayer(layer, record, { reason: 'retry' });
    }
  }

  /** A build failed: the layer shows the error and no surface, never the
   * previous build's mesh dressed as this one. */
  _buildFailed(layer, error) {
    layer.pane.renderer.removeAll(layer.key);
    layer.mesh = null;
    layer.builtRequest = null;
    layer.builtOptions = null;
    layer.builtKey = null;
    layer.builtGen = null;
    layer.deviation = null;
    layer.diff = null;
    layer.colored = false;
    layer.painted = null;
    this._legendHide(layer);
    // The rings are drawn once per cell load, over whatever layers hold a surface.  With none left, the rings
    // of an earlier load (or of another cell) are yesterday's picture as much as a surface would be, and go;
    // the rings of this load are still true, and stay (the overlay's dot still switches them).
    const { pane } = layer;
    if (!pane.layers.some((l) => l.mesh) && pane.annotationsDrawn !== null
        && pane.annotationsDrawn !== this._annotationsToken(this.record)) {
      pane.renderer.removeDiscs(ANNOTATION_KEY);
      pane.annotationsDrawn = null;
    }
    this._failLayer(layer, 'build', error, { what: `${techniqueName(layer.config.options)} failed to build` });
  }

  // -- technique layers -------------------------------------------------------

  /** Build (if the surface is not current) and measure (if error is ticked)
   * one technique layer.  `reason`: 'compare', 'tick' / 'stale-tick' (the
   * error box was ticked), or 'retry'. */
  _scheduleLayer(layer, record, { reason = 'compare' } = {}) {
    const task = this._newTask(layer, 'pane', record);
    task.onlyForMeasure = reason === 'tick' || reason === 'stale-tick';
    return this._enqueue(task, async () => {
      if (!this._meshCurrent(layer)) {
        if (!(await this._buildStep(task, record, reason === 'stale-tick'))) return;
      }
      if (layer.config.error && layer.mesh && !layer.deviation) await this._measureStep(task, record);
    });
  }

  /** The mesh for these options: filed, or the download under way, or a new job (see MeshCache). */
  _fetchMesh(record, options, task, { fresh = false } = {}) {
    const key = meshKey(record, pickOptions(options));
    return this.meshes.fetch(key, ({ signal, onStatus }) => api.mesh(record.source, record.id, options, {
      signal, alive: this._alive(record), onStatus,
    }), {
      signal: task.phaseController?.signal ?? task.controller.signal,
      onStatus: (job) => this._onJob(task.pane, job),
      fresh,
    });
  }

  async _buildStep(task, record, rebuild) {
    const { layer } = task;
    const { pane } = layer;
    let options = this._layerOptions(layer);
    // A Voxel size too fine for this cell is built at the finest that fits it, as
    // the 3D render does, and the status line says so.
    const fit = this.fitPixel && CURVE_METHODS.has(options.method)
      ? await this.fitPixel(record, options) : null;
    let fitted = '';
    if (fit) {
      if (!this._owns(layer) || task.controller.signal.aborted) return false;
      fitted = ` at ${fit.pixel} µm, not ${options.pixel_size_um}: the finest Voxel size that fits `
        + `c${record.id}${fit.why ? ` (${fit.why})` : ''}`;
      layer.config.options = paneOptions({ ...layer.config.options, pixel_size_um: fit.pixel });
      this._deriveTech(layer);
      options = this._layerOptions(layer);
      if (this._optionsLayer() === layer) this._syncOptions();
    }
    const label = techniqueName(options);
    this.onStatus(`${this._where(layer)}: building ${label}${fitted}…`);
    this._phase(task, 'building',
      rebuild ? `Settings changed, rebuilding this ${pane.layers.length > 1 ? 'layer' : 'pane'}`
        : `Building ${label}`);
    this._legendHide(layer);
    let mesh;
    try {
      mesh = await this._fetchMesh(record, options, task);
    } catch (error) {
      if (error?.aborted) {
        // Stopped: the layer keeps what it had (it is marked stopped, and
        // stale if its settings moved on).
        if (layer.mesh && layer.config.error && layer.deviation) this._repaintErrors();
        return false;
      }
      if (!this._owns(layer)) return false;
      this._buildFailed(layer, error);
      return false;
    }
    if (!this._owns(layer)) return false;
    if (!mesh?.n_faces) {
      // Cylinder and Tube draw discs: a cell traced only with closed curves has none, and
      // Closed curve or Hybrid is the way out.  Those two have no such way out to name.
      const outlines = CURVE_METHODS.has(options.method);
      this._buildFailed(layer, new Error(`The build produced an empty mesh: ${label} has nothing `
        + (outlines
          ? 'to draw for this cell. It has no annotations this technique can turn into a surface '
            + '(no outlines and no discs it can use): compare another cell, or pick another '
            + 'technique for this pane.'
          : 'to draw for this cell. A cell traced only with closed curves has no discs, so '
            + 'Cylinder and Tube cannot draw it: pick Closed curve or Hybrid for this pane.')));
      return false;
    }
    this._setMesh(layer, mesh, options, record);
    pane.renderer.removePoints(layer.key);
    this._drawSurface(layer);
    layer.painted = null;
    await this._applyDisplay(pane);
    this._frame(pane);
    // Stop may have landed while the annotations were fetched (an await
    // nothing else stops): the measurement that follows must not start.
    return !task.controller.signal.aborted;
  }

  /** The error map of the surface the layer HOLDS: requested from its
   * snapshot (`builtOptions`), attached only when the result names its mesh. */
  async _measureStep(task, record) {
    const { layer } = task;
    const { pane } = layer;
    if (task.controller.signal.aborted) return false;
    const phase = new AbortController();
    const link = () => phase.abort();
    task.controller.signal.addEventListener('abort', link, { once: true });
    task.phaseController = phase;
    this.onStatus(`${this._where(layer)}: measuring against the annotations…`);
    this._phase(task, 'measuring', 'Measuring against the annotations', 'measuring');
    const call = () => api.deviationJob(record.source, record.id, layer.builtOptions, {
      signal: phase.signal, alive: this._alive(record), onStatus: (job) => this._onJob(pane, job),
    });
    try {
      let result = await call();
      if (!sameSurface(layer.mesh, result)) {
        // The server measured another copy of this surface than the one the
        // layer holds. Fetch the surface again, once, and measure that.
        pane.card.setTitle(this._taskTitle(task, 'The server’s surface changed, fetching it again'));
        const mesh = await this._fetchMesh(record, layer.builtRequest, task, { fresh: true });
        if (!this._owns(layer)) return false;
        this._setMesh(layer, mesh, layer.builtRequest, record);
        this._drawSurface(layer);
        layer.painted = null;
        await this._applyDisplay(pane);
        pane.card.setTitle(this._taskTitle(task, 'Measuring against the annotations'));
        result = await call();
        if (!sameSurface(layer.mesh, result)) {
          throw new Error('The server’s copy of this surface changed again while it was '
            + 'measured, so the error map would not belong to what is on screen. '
            + 'Press Compare to rebuild the pane.');
        }
      }
      if (!Array.isArray(result.vertex_um) || result.vertex_um.length !== layer.mesh.n_vertices) {
        throw new Error('The measurement came back without one distance per vertex.');
      }
      layer.deviation = result;
    } catch (error) {
      if (error?.aborted || !this._owns(layer)) return false;
      // The surface is fine and stays; only the map failed.  The box is
      // un-ticked so it matches what is drawn, and Retry on the banner puts
      // it back.
      layer.config.error = false;
      this._syncEditor(pane);
      this._syncErrorToggle();
      this._showPlain(layer);
      this._failLayer(layer, 'measure', error, {
        what: 'the error map could not be measured',
        note: 'The surface is unchanged.',
      });
      return false;
    } finally {
      task.controller.signal.removeEventListener('abort', link);
    }
    this._notes.push(`${this._where(layer)}: error map drawn.`);
    this.onStatus(this._notes.at(-1));
    // A new layer can raise the shared scale, so every error layer repaints.
    this._repaintErrors();
    return true;
  }

  // -- Difference panes ------------------------------------------------------

  _scheduleDiff(layer, record, { after = [] } = {}) {
    const task = this._newTask(layer, 'diff', record);
    return this._enqueue(task, () => this._diffStep(task, record), { after });
  }

  async _diffStep(task, record) {
    const { layer } = task;
    const { pane } = layer;
    // Already measured from the surfaces the two panes hold now.
    if (layer.diff && !layer.error && !this._diffStale(layer)) return true;
    const { a, b, problem } = this._diffSources(layer);
    const missing = problem ? null : [a, b].find((l) => !l.mesh);
    if (problem || missing) {
      this._diffFailed(layer, new Error(problem
        || `Pane ${missing.pane.index + 1} has no surface (it failed or has not been built), `
          + 'so there is nothing to compare.'));
      return false;
    }
    const names = `pane ${a.pane.index + 1} − pane ${b.pane.index + 1}`;
    this.onStatus(`Pane ${pane.index + 1}: measuring ${names}…`);
    this._phase(task, 'measuring', `Measuring ${names}`, 'measuring');
    const { signal } = task.controller;
    const body = () => ({ a: a.builtOptions, b: b.builtOptions });
    const call = () => api.diffJob(record.source, record.id, body(), {
      signal, alive: this._alive(record), onStatus: (job) => this._onJob(pane, job),
    });
    try {
      let mesh = a.mesh;
      let result = await call();
      if (!sameSurface(mesh, result)) {
        // The server's pane-A surface is not the one pane A holds: fetch it
        // again for this pane (pane A is left as it is), once.
        pane.card.setTitle('The server’s surface changed, fetching it again');
        mesh = await this._fetchMesh(record, a.builtRequest, task, { fresh: true });
        pane.card.setTitle(`Measuring ${names}`);
        result = await call();
        if (!sameSurface(mesh, result)) {
          throw new Error('The server’s copy of pane '
            + `${a.pane.index + 1}’s surface changed again while it was measured, so the `
            + 'difference would not belong to what is on screen. Press Compare to rebuild.');
        }
      }
      if (!Array.isArray(result.distance_um) || result.distance_um.length !== mesh.n_vertices) {
        throw new Error('The measurement came back without one distance per vertex.');
      }
      if (!this._owns(layer)) return false;
      layer.mesh = mesh;
      layer.diff = result;
      layer.diffSources = { a: a.pane.index + 1, b: b.pane.index + 1 };
      layer.diffKey = this._diffKey(a, b);
    } catch (error) {
      if (error?.aborted || !this._owns(layer)) return false;
      this._diffFailed(layer, error);
      return false;
    }
    this._paintDiff(layer);
    this._frame(pane);
    return true;
  }

  /** A Difference that could not be measured: no map (yesterday's numbers
   * for other settings would mislead), and the banner says why. */
  _diffFailed(layer, error) {
    layer.pane.renderer.removeAll(layer.key);
    layer.mesh = null;
    layer.diff = null;
    layer.diffSources = null;
    layer.diffKey = null;
    layer.colored = false;
    this._legendHide(layer);
    this._failLayer(layer, 'diff', error, { what: 'the difference could not be measured' });
  }

  // -- Compare ---------------------------------------------------------------

  /** Build every layer that is not up to date for one cell, then measure the
   * layers that ask for it.  Each pane works through its layers in order, and up
   * to three panes at a time (the server's worker slots are shared with
   * everyone else); a Difference waits for the surfaces it measures.  Layers
   * already current (same settings, same load of the cell) are left as they are,
   * and a technique another layer or pane already downloaded is not fetched again. */
  async build(record) {
    // Compare pressed mid-build must never be silently ignored: remember the
    // latest request and run it when the current work finishes.
    if (this.running) {
      this._queued = record;
      this.onStatus('Still working — this Compare will run as soon as it finishes.');
      return;
    }
    const changed = !this.record || this.record.key !== record.key
      || this.record.loadGen !== record.loadGen;
    this.record = record;
    this._outdated = false;
    this._runKind = 'build';
    if (changed) {
      this._shiftRecordChanged(record);
      // A mesh filed for another cell or load can never be asked for again.
      this.meshes.clear();
    }
    const layers = this._allLayers();
    for (const layer of layers) {
      // The raster a curve technique will use on this cell, shown before it is built.
      if (layer.config.tech !== 'diff') this._seedRaster(layer, record);
      // A different cell or load makes a Difference's map wrong the moment
      // the build starts; it must never keep the OLD cell's map if its own
      // measurement later fails.
      if (changed && layer.config.tech === 'diff') this._clearContent(layer);
      // A new attempt answers the build and Difference failures of the last.
      if (layer.error && layer.error.kind !== 'measure') this._clearFailure(layer);
      layer.stopped = false;
    }
    const work = [];
    for (const layer of layers) {
      if (layer.config.tech === 'diff') continue;
      const wantsMap = layer.config.error && layer.mesh && !layer.deviation;
      if (!this._meshCurrent(layer) || wantsMap) work.push(this._scheduleLayer(layer, record));
    }
    // A Difference waits for every surface being built (this Compare's, and any still under way).
    const surfaces = [...this._inflight];
    for (const layer of layers) {
      if (layer.config.tech !== 'diff') continue;
      // A Difference whose two sources stay as they are, and which is already
      // measured from them, has nothing to do (and must not flash the Stop
      // all button for it).
      const measured = layer.diff && !layer.error && !this._diffStale(layer);
      if (work.length || !measured) work.push(this._scheduleDiff(layer, record, { after: surfaces }));
    }
    this._refresh();
    // The panel shows the raster just chosen for this cell, and what it costs.
    this._syncOptions();
    if (work.length) {
      await Promise.all(work);
    } else {
      // Every layer is current: nothing to rebuild, and the status says so
      // rather than leaving the last message up.
      this.onStatus(this.panes.some((p) => p.error)
        ? 'Nothing to rebuild; the panes that failed are marked.'
        : `Every pane is up to date for c${record.id} · ${record.source}. `
          + 'Change a setting, then press Compare to rebuild.',
      this.panes.some((p) => p.error) ? 'error' : undefined);
      this._runKind = null;
    }
    const next = this._queued;
    this._queued = null;
    if (next) await this.build(next);
  }

  /** Public stale marker, for settings that live outside this view (the
   * cell picker): with the picker's current key, what is shown is out of
   * date when that is another cell or another load of it. */
  markStale(pickedKey) {
    // The options panel's cost is for the cell the picker names.
    const shown = this._optionsLayer();
    if (shown && this.optionsPanel?.open) {
      this.optionsPanel.update({ options: this._layerOptions(shown), record: this._compareRecord() });
    }
    if (!this.record) return;
    if (pickedKey === undefined) {
      this._outdated = true;
    } else {
      this._outdated = pickedKey !== this.record.key
        || loadGeneration(pickedKey) !== this.record.loadGen;
    }
    this._refresh();
  }

}
