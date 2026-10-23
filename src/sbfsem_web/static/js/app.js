// Wiring: DOM events -> API calls -> views.

import { api, onBusy, ApiError, setMovesProvider, loadsOf } from './api.js';
import {
  state, subscribe, upsertNeuron, removeNeuron, loadGeneration, selectNeuron, selectedRecord,
  selectedRecords, setSelected, selectRange, selectAll, clearSelection,
  isSelected, visibleRecords, setColor, setVisible, setSource, setRenderOptions,
  setDisplay, normalizeRender, normalizeDisplay, effectiveSignature, keyOf,
  DEFAULT_SYNAPSE_SIZE, typeSettings, hasTypeOverride, withTypeOverride,
  DEFAULT_RENDER, PALETTE, dataKey,
} from './state.js';
import { parseStructureSpec } from './ids.js';
import {
  activeSlot, choosable, dotChoices, hideLevels, inSlice, leadOf, markerReadout, markerReadoutText, markerRows,
  placeChoice, sameAnnotation, signed, SLICE_SCOPES, sliceStep, SLOT_NAMES, SLOTS, stepAnchor, stepMarker,
  sideOf,
} from './markers.js';
import {
  CLIENTS, CLIENT_IDS, clientName, planFromStores, planForPick, readMemory, writeChoice,
} from './vikingclient.js';
import {
  meshCacheKey, dropWithPrefix, dropOtherCopies, MeshCache, DrawTokens, importKind, isTypingTarget,
  registrationChoiceDiffers, helpLayouts, pickHelpLayout, singleFlight, sameOptions, paneCountMessage,
  StatusHold, extrasWanted, startExtras, adoptExtras, sharedRequest, sameCamera,
  extrasWeight, keptRequest, KEPT_EXTRAS_LIMIT,
} from './appcore.js';
import { buildRenderForm } from './renderform.js';
import {
  TECH_KNOBS, loadTechDefaults, storeTechDefaults, techKey, techniqueDefaults,
  curvePixelFor, CURVE_METHODS, curvePixelSeed, estimateBuildSeconds,
  RENDER_PRESETS, RENDER_REQUIRES, matchPreset, presetOptions, knobSummary, withIplMethod,
  drawsIplDepth, flatQuery,
} from './renderoptions.js';
import {
  EstimateCache, estimateChip, estimateQuery, fittedPixel, fitStillWanted, interim, mainReason,
  coarsenedNotice,
  fineVoxelNotice, LONG_WAIT_S,
} from './renderestimate.js';
import { renderConflicts, buildsField, fieldNotes } from './renderconflicts.js';
import {
  FINEST_PIXEL, failureOf, fallbacks, failurePhrase, exhaustedNote, makeupText,
} from './renderfallback.js';
import { ConflictPanel } from './conflictpanel.js';

import { Renderer3D } from './render3d.js';
import { CompareView } from './compare.js';
import { ReportDialog } from './reportdialog.js';
import { RegistrationDialog } from './registrationdialog.js';
import { DisplayPanel } from './display.js';
import { ViewCube } from './viewcube.js';
import {
  isPresetMaterial, materialNames, materialSpec, resolveMaterialName, setMaterialLibrary,
} from './materials.js';
import { bindMaterialsPanel } from './materialpanel.js';
import { bindGradientsPanel } from './gradientpanel.js';
import { setGradientLibrary, colormapSample, colormapNames } from './colormaps.js';
import { ViewRecorder, supportedFormats } from './video.js';
import { NetworkView, SYNAPSE_COLORS } from './network.js';
import { ConnectivityView } from './connectivity.js';
import {
  SettingsPanel, pageAssets, savedRatio, expertRender, STALE_RELOAD_KEY, fromBrowserCache, staleStart,
  iplMethod, IPL_METHOD_CHOICES,
} from './settings.js';
import { fillLinkedNeuronsCard, sortableHead, sortRows } from './linkedneurons.js';
import { stratificationChart, barChart, STRATA_TICK_PCT } from './charts.js';
import {
  GRAPH_SETTINGS_KEY, MARKER_SIZES, FLAG_PX, SYNAPSE_PX, FLAG_COLORS, graphColors, unfinishedIds,
  segmentPositions, normalizeGraphSettings, findId, isolate, restoreVisibility,
} from './graphapp.js';
import * as session from './session.js';
import { browserShortcut, toggleFullscreen, watchFullscreen } from './fullscreen.js';
import { installColorPicker } from './colorpicker.js';
import { installSelectPicker } from './selectpicker.js';
import { buildSkeletonCheck, skeletonCheckSummary, forgetSkeletonCheck } from './skeletoncheck.js';
import { showAnnotators, closeAnnotators } from './annotators.js';
import { LoadProgress, stageText } from './loadprogress.js';
import { ShiftController, onlyShiftKeys } from './shiftcontrol.js';
import { buildShiftPanel, shiftSummary } from './shiftpanel.js';
import { pickShiftText } from './shiftlayer.js';
import { normalizeShiftDisplay, stagePositions } from './shifts.js';
import { MoveController } from './movecontrol.js';
import { movesBody } from './manualmoves.js';
import { applied as visualizationLook } from './visualizations.js';
import { dismissApplyAll, visualizationItems, visualizationSelect } from './vismenu.js';
import { buildFigure } from './helpfigures.js';
import { ENDED as PIVOT_ENDED, pivotPlace } from './pivot.js';
import { buildMovie, loadMovies, movieFor, movieManifest } from './helpmovies.js';
import {
  REG_FIGURES, REG_HELP, correctionChip, correctionForCell, transformsChip,
} from './regstatus.js';
import { planBulkSwc, savedNotice, bulkSwcSummary, exportQuery } from './exports.js';
import {
  SELECTABLE_KINDS, contextTitle, describeCandidate as nameCandidate, listCandidates, offersChoice,
  placeInside, sameCandidate,
} from './contextmenu.js';

const $ = (id) => document.getElementById(id);
const statusEl = $('status');
// What a Closed curve or Hybrid build would cost, as the server estimates it
// before building: the cost chip reads it, and every build of an outline method
// is held to it (addToScene).  One cache for the 3D render and Render compare.
const estimates = new EstimateCache(
  (record, options) => api.renderEstimate(record.source, record.id, estimateQuery(options)));
// The flared message's hold on the status line, and the timer that ends it.
const statusHold = new StatusHold();
let statusHoldTimer = 0;
/**
 * Say it so it cannot be missed: red, a swell, then back to the usual gray
 * (`.status.flare` in app.css, about 2.4 s).  For an answer of "no" that
 * leaves nothing else to notice -- no window, no tab, no error.  The busy
 * tracker's "working…" and "ready" wait until it has been read (StatusHold).
 */
function flareStatus(text) {
  statusEl.textContent = text;
  // A class that is already there does not restart the animation, so the same
  // message twice would flare only once: drop it, force layout, put it back.
  statusEl.className = 'status';
  void statusEl.offsetWidth;
  statusEl.className = 'status flare';
  statusHold.hold(performance.now());
  clearTimeout(statusHoldTimer);
  statusHoldTimer = setTimeout(() => {
    // Work still going on takes the line back; anything written over the
    // flare meanwhile (an error, an answer) is left as it is.
    const update = statusHold.release();
    if (update && statusEl.textContent === text) showBusy(update);
  }, statusHold.ms);
}

// The status line never wraps (app.css); whatever it cuts off is its tooltip.
new MutationObserver(() => { statusEl.title = statusEl.textContent; })
  .observe(statusEl, { childList: true, characterData: true, subtree: true });
// So do the two texts under Render compare's grid: one line each, the rest on hover.
for (const id of ['compare-summary', 'compare-status']) {
  const el = $(id);
  new MutationObserver(() => { el.title = el.textContent; })
    .observe(el, { childList: true, characterData: true, subtree: true });
}

/** Read what /open asked for, if this page was reached through a deep link.
 *
 * The server has already translated Viking's volume name into ours and
 * checked that this account may open it, so what arrives here is either
 * instructions or a sentence explaining why there are none.
 */
function openLinkRequest() {
  const el = $('open-link');
  if (!el || !el.dataset.open) return null;
  try {
    return JSON.parse(el.dataset.open);
  } catch (error) {
    console.warn('unreadable open link:', error);
    return null;
  }
}

/** Act on a deep link: switch volume, then load the cells it named.
 *
 * Runs after the saved session is restored, so an explicit link wins over
 * whatever the browser was last looking at -- someone following a link from
 * Viking means to see *that*.
 */
async function applyOpenLink(request) {
  if (!request) return;
  if (request.error) {
    $('render-error-title').textContent = 'Cannot open that link.';
    $('render-error-text').textContent = request.error;
    $('render-error').dataset.key = '';
    $('render-error').hidden = false;
    return;
  }
  if (request.source) switchVolume(request.source);
  if (request.technique) {
    // The technique for a *new* load comes from the render controls, so set
    // them -- not the per-cell overrides, which belong to cells that exist.
    writeControls({ ...readControls(), pathway: request.technique });
    updateMethodControls();
  }
  if (request.note) {
    // The load below writes its own status line over this one, so the note
    // (an unknown technique, ignored cell IDs, the 50-cell limit, a bad
    // location) is also said where nothing overwrites it, for a while.
    statusEl.textContent = request.note;
    statusEl.className = 'status';
    // The headline and the text sit side by side (index.html): the text starts
    // with a space, as every other notice's does.
    showRenderNotice('About the link you followed:', ` ${request.note}`);
  }
  if (request.cells && request.cells.length) {
    await loadFromSpec(request.cells.join(','));
  }
  // The annotation the link was made from -- a Viking context menu on one
  // location -- so the view opens on it rather than on the whole cell.  The
  // volume was switched above, so the ID is looked up in the right one.
  if (request.location) {
    try {
      await gotoLocationId(String(request.location));
    } catch (error) {
      fail(error);
    }
  }
}

let renderer;
let shifts;       // the Position shift layer's controller (shiftcontrol.js)
let moves;        // manual moves of annotations (movecontrol.js)
let network;
let connectivity;
let settings;     // the Settings tab's debug tools (settings.js)
let compare;
let display;
let viewCube;   // the view cube in the viewport's corner (viewcube.js)
let recorder;
// The surfaces this tab has, by meshCacheKey (the cell, its copy and stamp, the
// render options), least recently used first out (MeshCache).  A cell that is
// unloaded keeps its surfaces here for Undo; they go when the cell is loaded as
// other data (forgetDrawn, dropOtherCopies), when a workspace replaces the
// cells, or when newer ones need the room.
const meshCache = new MeshCache();
// The outlines, annotations and skeletons this tab was answered, kept past the load
// they came with by the state of the copy they are of (appcore `extrasKey`), least
// recently used first out: the per-cell caches hold the current load's and are emptied
// with it (forgetDrawn), these are what a later load at the same state is drawn from
// (askKept).  Let go of with the surfaces after a redeploy and when a workspace opens.
const keptExtras = new MeshCache(KEPT_EXTRAS_LIMIT, extrasWeight);
const skeletonCache = new Map();
/** key -> { method, data }: a cell's skeleton on the flattened IPL (the 3D render's; GraphApp's is the other). */
const flatSkeletonCache = new Map();
// The newest draw of each cell: an older one still waiting on a build stands down.
const draws = new DrawTokens();

// Short tags for the loaded list, so differing render styles are visible at a
// glance instead of only after selecting each neuron.
// Two-letter volume tags for the shut rows of the cell list, and the long
// names an open row spells out.
const VOLUME_ABBR = {
  NeitzTemporalMonkey: 'tm', NeitzInferiorMonkey: 'im', NeitzNasalMonkey: 'nm',
  NeitzCped: 'cp', RC1: 'rc1', RC2: 'rc2', RPC1: 'rpc1', RPC2: 'rpc2', DemoVolume: 'demo',
};
function volumeAbbrev(source) {
  return VOLUME_ABBR[source] || String(source).toLowerCase().slice(0, 4);
}
/**
 * A cell as a picker or a chart names it: `c1178`, and its volume's tag when
 * the loaded cells come from more than one volume -- an ID is numbered per
 * volume, so the bare number can name two of them.
 */
function cellLabel(record) {
  const volumes = new Set([...state.neurons.values()].map((r) => r.source));
  return volumes.size > 1 ? `c${record.id} · ${volumeAbbrev(record.source)}` : `c${record.id}`;
}

/** Whether a volume has the IPL boundary markers depth shading needs (true while the list is not loaded). */
function volumeHasBoundary(source) {
  const volume = state.volumes.find((v) => v.name === source);
  return volume ? !!volume.has_boundary : true;
}

// -- the flattened IPL (Display → Flatten IPL) ---------------------------------
// James Anderson's request of 2026-10-22: every cell drawn with its volume's INL and
// GCL flat, its Z the IPL depth times the volume's median IPL thickness along the
// normals (the INL at 0), at 1:1 with X and Y.  One choice for the whole view, made
// in the Display panel and off at every page load: it hides what is drawn in section
// coordinates (rings, outlines, the annotation wireframe, the Position shift layer,
// manual moves, markers), which a flattened Z cannot carry.
let iplFlat = false;

/** Whether `record` is drawn on the flattened IPL: the view is, and its volume has boundary markers. */
function flatFor(record) {
  return iplFlat && !!record && volumeHasBoundary(record.source);
}

/** What a request for a layer of `source` adds in the flattened view (`flatQuery`; {} otherwise). */
function flatParams(source) {
  return flatQuery(currentIplMethod(), iplFlat && volumeHasBoundary(source));
}

/** A cell's Z range as the renderer takes it: none while the cell is flattened, where Z is IPL depth. */
function zHideOf(record, d) {
  return flatFor(record) ? { min: null, max: null } : { min: d.zMin, max: d.zMax };
}
/**
 * Whether a volume's current section transforms are read here (`has_transforms`),
 * so its cells can be drawn where Viking draws them now; true while the list is not
 * loaded, and for a server that predates the flag.
 */
function volumeHasTransforms(source) {
  const volume = state.volumes.find((v) => v.name === source);
  return volume ? volume.has_transforms !== false : true;
}
/**
 * The correction mode a volume's cells get when they name none (`default_correction`:
 * Field + fit for the Neitz lab's volumes, Curve fit for the others), and what the
 * Correction select and the Position shift's Apply offer while a cell's correction is
 * off; Curve fit while the list is not loaded, and for a server that predates the field.
 */
function volumeDefaultCorrection(source) {
  const mode = state.volumes.find((v) => v.name === source)?.default_correction;
  return mode in CORRECTION_SHORT ? mode : 'curvefit';
}
function volumeLongName(source) {
  if (source === 'NeitzCped') return 'Cone pedicles (cped)';
  const v = state.volumes.find((x) => x.name === source);
  return v ? v.display_name : source;
}

/** Global toggles worth restoring with the session. */
function uiFlags() {
  return {
    show_axes: $('show-axes').checked,
    show_viewcube: $('show-viewcube').checked,
    ortho: $('ortho').checked,
    controls: readControls(),
    // Materials, colormap, scale bar, context surfaces, crop: view state, so
    // it belongs in the session and in the undo history like everything else.
    display: display ? display.snapshot() : undefined,
    // GraphApp, and the visibility an isolation would give back (only a session
    // restore reads it: Undo does not change the look).
    graph: graphOn ? { on: true, saved: graphSaved ? [...graphSaved] : null, isolated: graphIsolated } : undefined,
  };
}

let restoring = false;

function persist() {
  // A restore fires dozens of state events; saving during one would only write
  // back what we are in the middle of reading.
  if (restoring) return;
  session.save(state, renderer, uiFlags());
  scheduleHistoryPush();
}

// -- undo / redo -----------------------------------------------------------
// One history of everything: cells, their settings, selection, the active
// tab, and the viewport. Each entry is a full workspace snapshot (a few kB),
// applied back by diffing against what is on screen so undoing a camera move
// never rebuilds a mesh.
const HISTORY_LIMIT = 100;
const HISTORY_MERGE_MS = 350;
const undoStack = []; // [..., current]; undo needs at least two entries
const redoStack = [];
let applyingHistory = false;
let historyTimer = null;

function historySnapshot() {
  const snap = session.snapshot(state, renderer, uiFlags());
  snap.selection = [...state.selection];
  // The timestamp is volatile: with it in the JSON, no two snapshots ever
  // compare equal, the dedupe never fires, and undo's flush re-records the
  // current state instead of stepping back.
  delete snap.saved_at;
  // The camera drifts in its last float bits every frame (OrbitControls
  // round-trips position through spherical coordinates), which also broke
  // the dedupe. 1e-5 um is far beyond anything visible.
  if (snap.camera) {
    const r5 = (a) => a.map((v) => +v.toFixed(5));
    snap.camera = {
      ...snap.camera,
      position: r5(snap.camera.position),
      target: r5(snap.camera.target),
      up: r5(snap.camera.up),
      zoom: +snap.camera.zoom.toFixed(5),
      ...(Number.isFinite(snap.camera.halfHeightUm)
        ? { halfHeightUm: +snap.camera.halfHeightUm.toFixed(5) } : {}),
    };
  }
  return snap;
}

/** Record shortly after a burst of changes settles (a color drag is one step). */
function scheduleHistoryPush() {
  if (applyingHistory || restoring) return;
  clearTimeout(historyTimer);
  historyTimer = setTimeout(pushHistoryNow, HISTORY_MERGE_MS);
}

function pushHistoryNow() {
  clearTimeout(historyTimer);
  historyTimer = null;
  if (applyingHistory || restoring) return;
  const snap = historySnapshot();
  const json = JSON.stringify(snap);
  if (undoStack.length && undoStack[undoStack.length - 1].json === json) return;
  undoStack.push({ snap, json });
  // +1: the top entry is the current state, so 100 steps back stay reachable.
  while (undoStack.length > HISTORY_LIMIT + 1) undoStack.shift();
  redoStack.length = 0;
  updateUndoButtons();
}

function updateUndoButtons() {
  const undoBtn = $('undo-btn');
  const redoBtn = $('redo-btn');
  if (undoBtn) undoBtn.disabled = undoStack.length < 2;
  if (redoBtn) redoBtn.disabled = !redoStack.length;
}

async function undoHistory() {
  if (applyingHistory) return;   // a step is still being applied
  pushHistoryNow(); // a change still sitting in the merge window is a step too
  if (undoStack.length < 2) return;
  redoStack.push(undoStack.pop());
  await applyHistory(undoStack[undoStack.length - 1].snap);
}

async function redoHistory() {
  if (applyingHistory || !redoStack.length) return;
  const entry = redoStack.pop();
  undoStack.push(entry);
  await applyHistory(entry.snap);
}

/** Put the app into `snap`'s state, touching only what actually differs. */
async function applyHistory(snap) {
  applyingHistory = true;
  try {
    // Volume first: applyUiFlags re-fetches the context sheets for
    // state.source, and a sheet fetched for the wrong volume is wrong.
    if (snap.source) switchVolume(snap.source, { quiet: true });
    applyUiFlags(snap.ui);

    const want = new Map(snap.neurons.map((n) => [keyOf(n.source, n.id), n]));
    for (const record of [...state.neurons.values()]) {
      if (want.has(record.key)) continue;
      forgetCell(record);
    }

    for (const [key, entry] of want) {
      let record = state.neurons.get(key);
      if (!record) {
        // An undone unload: the annotations are still in the server cache,
        // so this is quick.
        try {
          const detail = await api.load({
            id: entry.id, source: entry.source, include_synapses: true,
            section_corrections: entry.corrections, live_registration: entry.registration,
            manual_moves: movesBody(entry.manualMoves),
          });
          record = upsertNeuron(detail, {
            render: entry.render, display: entry.display,
          });
          record.corrections = entry.corrections;
          record.correctionMode = entry.correctionMode;
          record.registration = entry.registration;
          record.manualMoves = entry.manualMoves ?? null;
          record.color = entry.color ?? record.color;
          record.visible = entry.visible !== false;
          record.opacity = entry.opacity ?? record.opacity;
          await addToScene(record, { frame: false });
        } catch (error) {
          console.warn(`undo could not reload c${entry.id}:`, error);
        }
        continue;
      }
      // Viking's current transforms or its section correction was switched in
      // this step: the cell has to be loaded again under the step's choices,
      // not just redrawn (it kept drawing at the positions of the other).
      let reloaded = false;
      if (registrationChoiceDiffers(record, entry)) {
        try {
          const detail = await loadInOrder(record.key, {
            id: record.id, source: record.source, include_synapses: true,
            section_corrections: entry.corrections, live_registration: entry.registration,
            manual_moves: movesBody(entry.manualMoves),
          });
          record = upsertNeuron(detail, {});
          record.corrections = entry.corrections;
          record.correctionMode = entry.correctionMode;
          record.registration = entry.registration;
          record.manualMoves = entry.manualMoves ?? null;
          forgetDrawn(record);
          reloaded = true;
        } catch (error) {
          console.warn(`undo could not reload c${entry.id} under its earlier settings:`, error);
        }
      }
      const renderChanged =
        effectiveSignature(record.render) !== effectiveSignature(entry.render);
      const displayChanged = JSON.stringify(normalizeDisplay(record.display))
        !== JSON.stringify(normalizeDisplay(entry.display));
      record.color = entry.color ?? record.color;
      record.visible = entry.visible !== false;
      record.opacity = entry.opacity ?? record.opacity;
      record.render = normalizeRender(entry.render);
      record.display = normalizeDisplay(entry.display);
      renderer.setStyle(record.key, {
        color: record.color, opacity: record.opacity, visible: record.visible,
      });
      if (renderChanged || reloaded) {
        renderer.removeAll(record.key);
        await addToScene(record, { frame: false });
      } else if (displayChanged) {
        const d = normalizeDisplay(record.display);
        renderer.setSurface(record.key, d.showSurface);
        renderer.setWireframe(record.key, d.showWireframe);
        // The cell's Z range is a display setting like the rest: undoing or
        // redoing a Hide above/below must put the range back.
        renderer.setZHide(record.key, zHideOf(record, d));
        renderer.setWireframeStyle(record.key, {
          color: d.wireframeColor || record.color, opacity: d.wireframeOpacity,
        });
        await refreshExtras(record);
        renderer.setStyle(record.key, { visible: record.visible });
      }
    }

    state.selection = new Set(
      (snap.selection || []).filter((k) => state.neurons.has(k)));
    if (snap.selected && state.neurons.has(snap.selected)) {
      state.selected = snap.selected;
    }
    // The viewport, after the meshes exist so nothing re-frames over it.
    if (snap.camera) renderer.restoreCamera(snap.camera);
    display?.syncTwoD();   // the 2D box describes the camera just put back
    if (snap.view && snap.view !== state.view) activateView(snap.view);

    renderNeuronList();
    populateTableSelect();
    updateScopeNote();
    session.save(state, renderer, uiFlags());
  } finally {
    applyingHistory = false;
  }
  updateUndoButtons();
}

// -- status ----------------------------------------------------------------
function showBusy({ message, kind }) {
  statusEl.textContent = message;
  statusEl.className = `status ${kind || ''}`;
}
onBusy((update) => {
  if (statusHold.admit(update, performance.now())) showBusy(update);
});

function fail(error) {
  console.error(error);
  statusEl.textContent = error instanceof ApiError ? error.detail : String(error.message || error);
  statusEl.className = 'status error';
}

/**
 * Announce a failed cell render where it cannot be missed.
 *
 * The 12px status corner is fine for progress, but a cell that silently is
 * not there reads as "the render is broken", so a failure gets a banner over
 * the viewport naming the cell, the technique it failed under, and why --
 * enough to know which parameter to walk back.
 */
function showRenderError(record, options, reason, { failure = null, exhausted = false } = {}) {
  // Every parameter of the technique that was in use (the schema knows which
  // those are), not only the three pathways' first ones.
  const knobs = knobSummary(options);
  if (options.clip_ipl !== undefined && options.clip_ipl !== null) {
    knobs.push(`clip ${options.clip_ipl} ${options.keep_above ? 'above' : 'below'}`);
  }
  // What to do next depends on whose failure it was: the settings' (expert render
  // mode left it failed, and the Render panel marks them), every fallback's too,
  // or nobody's settings at all (the network, a restart).
  let next;
  if (exhausted) {
    next = `${exhaustedNote(`c${record.id}`, failure, record.summary)}.`;
  } else if (failure) {
    next = 'the Render panel marks the settings to change, with ways to fix them'
      + (failure.fits ? ` (Voxel size ${failure.fits} µm or coarser fits).` : '.');
  } else {
    next = 'nothing in its settings caused this: try again in a moment (Apply, or ↻ on its row).';
  }
  $('render-error-title').textContent = 'Render failed.';
  $('render-error-text').textContent =
    `c${record.id} with ${techniqueLabel(options)}`
    + (knobs.length ? ` (${knobs.join(', ')})` : '')
    + ` — ${reason}. The cell is not shown: ${next}`;
  const banner = $('render-error');
  banner.dataset.key = record.key;
  banner.hidden = false;
}

/**
 * Say, for a while, that the app changed a setting away from its default.
 *
 * Top-right over the viewport, clear of the failure banner: the change is
 * the app's, not the user's, so it is shown rather than left for them to
 * find in the Render panel -- but it is not an error, so it goes by itself.
 */
let noticeTimer = 0;
function showRenderNotice(headline, detail, { action = null, ms = 15000 } = {}) {
  const box = $('render-notice');
  $('render-notice-title').textContent = headline;
  $('render-notice-text').textContent = detail;
  // One button at most, for undoing what the notice says the app did.
  const button = $('render-notice-action');
  button.hidden = !action;
  button.textContent = action?.label ?? '';
  button.onclick = action ? () => {
    clearTimeout(noticeTimer);
    box.hidden = true;
    action.run();
  } : null;
  box.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { box.hidden = true; }, ms);
}

/** A cell rendering successfully clears its own failure banner. */
function clearRenderError(record) {
  const banner = $('render-error');
  if (!banner.hidden && banner.dataset.key === record.key) banner.hidden = true;
}

// -- boot ------------------------------------------------------------------
async function boot() {
  // A page the browser showed from its own copy, of a build the server has since
  // replaced, reloads itself before anything is drawn.
  if (await reloadIfStale()) return;
  // The render controls are built first, from the schema; the browser's
  // startup default (or DEFAULT_RENDER) is the one source of truth for what a
  // fresh browser starts with (a restored session overwrites this below).
  buildRenderPanel();
  writeControls(startupRender() || DEFAULT_RENDER);
  renderer = new Renderer3D($('viewport'), {
    onPick: async (pick) => {
      pickedRing(pick);
      // The readout names what the pointer is nearest (hoverReadout); a click still
      // names its cell in the list, and leaves the readout to the pointer.
      // A flattened cell's point has a depth, not an annotation: the click writes it.
      if (hovered && !pick.copied && !flatFor(state.neurons.get(pick.key))) {
        if (pick.key) selectNeuron(pick.key);
      } else {
        await showPick(pick);
      }
      // A synapse's dot in GraphApp: which synapse, and what it links to.
      const record = pick.synapse ? state.neurons.get(pick.key) : null;
      if (record) await showSynapsePick(record, pick.synapse, pick.locationId);
    },
  });
  renderer.onContextMenu = contextMenuFor;
  renderer.onHover = hoverReadout;
  renderer.onPivotRequest = pivotAt;
  renderer.onPivotChange = pivotChanged;
  renderer.onSelectRequest = onSelectRequest;
  renderer.onSelectionChange = onSelectionChange;
  renderer.onMarkerChange = onMarkerChange;
  renderer.onStepZ = (direction, opts) => stepAlongZ(direction, opts).catch(fail);
  shifts = new ShiftController({
    renderer,
    state,
    api,
    nodesFor,
    outlinesFor,
    cachedNodes: (record) => nodesCache.get(record.key),
    cachedOutlines: (record) => outlinesCache.get(record.key),
    fail,
    volumeTag: volumeAbbrev,
    hasTransforms: (record) => volumeHasTransforms(record.source),
    defaultCorrection: (record) => volumeDefaultCorrection(record.source),
    // The cell panel lists the Correction log's pulls at the Settings tab's ratio.
    pullRatio: () => savedRatio(browserStore('localStorage')),
    hooks: {
      applyDisplay, debounced, setRegistration, renderNeuronList, look: lookAt, selectLocation,
    },
    dom: {
      viewport: $('viewport'),
      chip: $('shift-chip'),
      text: $('shift-chip-text'),
      steps: $('shift-chip-steps'),
      prev: $('shift-prev'),
      next: $('shift-next'),
      range: $('shift-range'),
      off: $('shift-off'),
    },
  });
  // Slice mode takes [ ] PageUp PageDown Home End, and Up/Down with nothing selected.
  renderer.onSliceStep = (event) => shifts.onKey(event);
  // Manual moves (2026-10-19): a person's own corrections after Viking's, sent with
  // every load of the cell, by the registration setting they were made under.
  setMovesProvider((source, id) => {
    const record = [...state.neurons.values()].find((r) => r.id === Number(id)
      && (r.source === source || volumeAbbrev(r.source) === source));
    return movesBody(record?.manualMoves);
  });
  moves = new MoveController({
    renderer,
    state,
    nodesFor,
    cachedNodes: (record) => nodesCache.get(record.key),
    shiftPair: (record) => {
      const layer = shifts?.layerOf(record);
      return layer && !layer.empty ? layer.pair : null;
    },
    activeSlot: () => activeSlotNow({ arrows: true }) || 'primary',
    hooks: {
      persist: () => { persist(); renderNeuronListWhenIdle(); },
      reload: reloadWithMoves,
      prepare: (record) => api.gridJob(record.source, record.id, normalizeRender(record.render)),
      renderNeuronList,
      status: (text) => { statusEl.textContent = text; statusEl.className = 'status'; },
      flare: (text) => flareStatus(text),
      fail,
      fly: flyToAnnotation,
      flattened: (record) => flatFor(record),
    },
    dom: { panel: $('move-panel') },
  });
  renderer.onMoveKey = (event) => moves.onKey(event);
  // ⌘/Ctrl-click on a ring in move mode adds it to what a step moves, or takes it out.
  renderer.onAdditiveClick = (ring) => moves.active && moves.toggleMember(ring.key, ring.locationId);
  // Every finished camera move (drag, wheel, roll, single-axis, keys) is an
  // undo step of its own.
  renderer.controls.addEventListener('end', () => pushHistoryNow());
  compare = new CompareView($('compare-grid'), {
    onStatus: (message, kind) => {
      const el = $('compare-status');
      el.textContent = message;
      el.className = kind === 'error' ? 'muted error' : 'muted';
    },
    // What the server can do (MeshLab techniques are offered where it can run
    // them), and the cell the picker names before the first Compare.
    caps: renderCtx,
    cell: () => state.neurons.get($('compare-cell').value) || null,
    // The two bridges between a pane's options and the 3D render panel's controls.
    mainSettings: () => readControls(),
    sendToMain: sendPaneToMain,
    onContextMenu: contextMenuForPane,
    volumeInfo: (source) => state.volumes.find((v) => v.name === source) || null,
    // The server's estimate on the panel's chip, and a layer too fine for its cell
    // built at the finest Voxel size that fits, as in the 3D render.
    estimates,
    fitPixel: fitPixelForPane,
  });
  compare.setPaneCount(4);
  display = new DisplayPanel({
    renderer, api, state,
    flatParams: (source) => flatParams(source),
    onFlatten: (on) => setIplFlat(on),
    hasBoundary: (source) => volumeHasBoundary(source),
    onChange: () => persist(),
    onStatus: (message, kind) => {
      statusEl.textContent = message;
      statusEl.className = `status ${kind || ''}`;
    },
  });
  recorder = new ViewRecorder(renderer, onRecorderState);
  viewCube = new ViewCube($('viewport'), renderer, {
    onHome: resetViewNow,
    onTurned: () => display.readCamera(),
  });
  renderer.onFrame = () => viewCube.update();
  showViewCube($('show-viewcube').checked);
  network = new NetworkView($('cy'));
  network.onSelect = (data) => {
    if (!data.neuronId) return;
    pickStructure(data.source || network.source || state.source, data.neuronId);
  };
  network.onHover = (text) => { $('net-hover').textContent = text; };
  connectivity = new ConnectivityView({
    api,
    records: () => [...state.neurons.values()],
    pick: pickStructure,
    fail,
    defaultSource: () => selectedRecord()?.source || state.source,
    volumeName,
  });
  settings = new SettingsPanel({
    root: $('settings-panel'),
    api,
    assets: pageAssets(import.meta.url),
    // A reload the tab asks for brings the session back without asking, as a redeploy's does.
    beforeReload: () => { session.saveNow(state, renderer, uiFlags()); session.restoreNextLoad(); },
    stopSaving: () => session.stopSaving(),
    // The Correction log's rows: an annotation of a cell this tab has is selected in the
    // 3D view; one of a cell it has not goes to the Structure ID box, with its volume.
    annotations: {
      loaded: (source, id) => state.neurons.has(keyOf(source, id)),
      select: async (source, id, locationId) => {
        const record = state.neurons.get(keyOf(source, id));
        if (!record) return false;
        activateView('render');
        return selectLocation(record, locationId);
      },
      load: (source, id) => { activateView('render'); pickStructure(source, id); },
      viking: (source) => Boolean(state.volumes.find((v) => v.name === source)?.viking_name),
      openInViking: (source, id, locationId) =>
        openLocationInViking(state.neurons.get(keyOf(source, id)) || { source, id }, locationId),
      volumeName: volumeLongName,
    },
    onRatioChange: () => renderNeuronList(),
    // The render panel's note on settings that will not build says what Apply will do.
    onExpertChange: () => updateMethodControls(),
    onIplMethodChange: () => iplMethodChanged(),
  });

  try {
    const volumes = await api.volumes();
    state.volumes = volumes;
    const select = $('volume');
    // Label is the display name; the value stays the wire identifier, which
    // is what every API path and cache key uses.
    select.replaceChildren(...volumes
      .filter((v) => v.is_odata)
      .map((v) => new Option(v.display_name, v.name)));
    // The saved session may name a volume this account can no longer open --
    // a grant withdrawn in Viking, or a link from someone with more access.
    // Falling back to the first volume we do have beats a picker showing a
    // volume that 403s on every request.
    if (!volumes.some((v) => v.name === state.source)) {
      const fallback = volumes.find((v) => v.is_odata) || volumes[0];
      if (fallback) setSource(fallback.name);
    }
    select.value = state.source;
    // Flatten IPL is off in a volume with no boundary markers, now that the list says which.
    display?.syncFlatten(iplFlat);
  } catch (error) {
    fail(error);
  }

  bindEvents();
  subscribe(onStateChange);

  // The shared libraries first: a restored cell may wear a material, and a
  // saved colormap may be a gradient, that only exist there.
  await loadLibraries();
  bindEditors();

  // Cells come back only when asked for: the list first, then Restore or
  // Discard -- except on the reload the redeploy banner offered.
  const auto = session.takeRestoreNextLoad();
  const saved = session.load();
  if (saved && saved.neurons.length) {
    if (auto) await restoreSession(saved);
    else offerSavedSession(saved);
  } else if (saved) {
    // A saved session with no cells is a tab its user cleared: it comes back
    // empty, never filled with the cells other people have open (2026-10-09).
    applyUiFlags(saved.ui);
    display?.syncTwoD();   // no cells, no camera restored: the box follows the default view
  } else {
    // No session at all (a first visit, or storage unavailable): the shared
    // list is offered, never pulled in unasked -- the redeploy reload included.
    await offerServerCells();
  }

  // After the restore: a link someone followed on purpose outranks whatever
  // this browser happened to be looking at last.
  await applyOpenLink(openLinkRequest());

  // A reload the Settings tab asked for comes back to it, saying what it did.
  if (settings?.hasNote()) activateView('settings');

  // Nothing emits a state change on an empty boot, so state the scope once.
  updateScopeNote();
  updateMethodControls();
  // The opening state is the floor of the undo history.
  pushHistoryNow();
  await watchForRedeploy();
}

// -- shared libraries and their editors -------------------------------------
let materialsPanel = null;

async function loadLibraries() {
  const [materials, gradients] = await Promise.allSettled([
    api.library('materials'), api.library('gradients'),
  ]);
  if (materials.status === 'fulfilled') setMaterialLibrary(materials.value.entries || []);
  else console.warn('material library unavailable:', materials.reason);
  if (gradients.status === 'fulfilled') setGradientLibrary(gradients.value.entries || []);
  else console.warn('gradient library unavailable:', gradients.reason);
  display?.refreshColormapMenu();
}

/** The cells a material edit previews on and applies to. */
function materialTargets() {
  const selected = selectedRecords();
  return selected.length ? selected : [...state.neurons.values()];
}

/** Give cells a finish by library name; it persists with the cell. */
function applyMaterialTo(records, name) {
  const resolved = resolveMaterialName(name);
  for (const record of records) {
    setDisplay(record.key, { material: resolved });
    renderer.setCellMaterial(record.key, resolved);
  }
  persist();
  renderNeuronList();
}

function bindEditors() {
  materialsPanel = bindMaterialsPanel({
    api,
    targets: materialTargets,
    allRecords: () => [...state.neurons.values()],
    preview: (records, spec) => {
      for (const record of records) renderer.setCellMaterial(record.key, spec);
    },
    applyTo: applyMaterialTo,
    onLibraryChanged: () => renderNeuronList(),
  });
  bindGradientsPanel({
    api,
    renderer,
    colorbar: display.colorbar,
    currentMap: () => display.currentMap(),
    useMap: (name) => display.useMap(name),
    onLibraryChanged: () => display.refreshColormapMenu(),
  });
}

// -- session ---------------------------------------------------------------
function applyUiFlags(ui) {
  if (!ui) return;
  const set = (id, value) => {
    // A session saved by an older build can name a control that no longer
    // exists; skipping is right, throwing would abort the whole restore.
    const el = $(id);
    if (value === undefined || !el) return;
    el.checked = !!value;
  };
  set('show-axes', ui.show_axes);
  set('show-viewcube', ui.show_viewcube);
  set('ortho', ui.ortho);
  renderer.setAxesVisible($('show-axes').checked);
  showViewCube($('show-viewcube').checked);
  renderer.setOrthographic($('ortho').checked);
  if (ui.controls) writeControls(ui.controls);
  // Fire and forget: the context meshes are a server round trip, and nothing
  // later in a restore depends on them having arrived.
  if (ui.display && display) display.restore(ui.display);
}

/** A saved session's volume and panel flags: everything but its cells. */
function applySessionShell(saved) {
  // Volume first: applyUiFlags fires the context-sheet fetches for
  // state.source without waiting, so with the old order the INL request
  // left for the default volume and only the GCL saw the saved one.
  if (saved.source) switchVolume(saved.source, { quiet: true });
  applyUiFlags(saved.ui);
}

// -- what a page load would bring back ---------------------------------------
// Nothing is loaded behind the user's back (James Anderson, 2026-09-24: "it
// would be nice to see the list first so I can cancel before they appear").
// A page load lists the cells it would bring back -- this browser's saved
// session or, with none, the cells already open on this server -- and waits
// for Restore or Discard.  Until then the saved list stays in the browser.

/** "Inferior Monkey c21768, c1178; RC1 c606", by volume. */
function describeCells(entries) {
  const byVolume = new Map();
  for (const e of entries) {
    if (!byVolume.has(e.source)) byVolume.set(e.source, []);
    byVolume.get(e.source).push(e.id);
  }
  return [...byVolume].map(([source, ids]) => `${volumeName(source)} c${ids.join(', c')}`).join('; ');
}

/** The list as the banner shows it (the first few), and in full for its tooltip. */
function cellList(entries, max = 8) {
  const full = describeCells(entries);
  const shown = entries.length <= max ? full
    : `${describeCells(entries.slice(0, max))} and ${entries.length - max} more`;
  return { shown, full };
}

function offerCells({ text, full, yes, no, onYes, onNo }) {
  const banner = $('restore-banner');
  const yesBtn = $('restore-yes');
  const noBtn = $('restore-no');
  $('restore-text').textContent = text;
  $('restore-text').title = full;
  yesBtn.textContent = yes;
  noBtn.textContent = no;
  const close = () => {
    banner.hidden = true;
    yesBtn.onclick = null;
    noBtn.onclick = null;
  };
  yesBtn.onclick = async () => {
    close();
    try { await onYes(); } catch (error) { fail(error); }
  };
  noBtn.onclick = () => { close(); onNo?.(); };
  banner.hidden = false;
}

/** This browser's saved session: its volume and panels now, its cells if asked. */
function offerSavedSession(saved) {
  applySessionShell(saved);
  // The saved camera comes back only with Restore: until then the 2D box
  // follows the view that is there (Restore sets it again from the saved view).
  display?.syncTwoD();
  session.setPending(saved.neurons);
  const when = saved.saved_at ? new Date(saved.saved_at).toLocaleString() : 'earlier';
  const n = saved.neurons.length;
  const { shown, full } = cellList(saved.neurons);
  offerCells({
    text: `${n} cell${n === 1 ? '' : 's'} from your last visit (${when}): ${shown}.`,
    full,
    yes: 'Restore',
    no: 'Discard',
    onYes: async () => {
      session.setPending(null);
      await restoreSession(saved);
      pushHistoryNow();
    },
    onNo: () => {
      session.setPending(null);
      persist();
      statusEl.textContent = `Discarded ${n} saved cell${n === 1 ? '' : 's'}`;
      statusEl.className = 'status';
    },
  });
}

/** No saved session: the cells open on this server, shared by everyone using it. */
async function offerServerCells() {
  let summaries;
  try {
    summaries = await api.loaded();
  } catch (error) {
    fail(error);
    return;
  }
  if (!summaries.length) return;
  const n = summaries.length;
  const { shown, full } = cellList(summaries);
  offerCells({
    text: `No saved session in this browser. ${n} cell${n === 1 ? ' is' : 's are'} open on this `
      + `server, shared by everyone using it: ${shown}.`,
    full,
    yes: 'Show them',
    no: 'Start empty',
    // Through the same loader as a typed list: each cell takes its attached
    // render or the one its annotations can show, and the load strip tracks
    // them with a Stop.
    onYes: async () => {
      await loadCells(summaries.map(({ source, id }) => ({ source, id })));
      pushHistoryNow();
    },
  });
}

/**
 * Put the researcher back where they were.
 *
 * The server keeps nothing per user and forgets its loaded neurons on restart,
 * so every neuron is re-requested here.  That is close to free after a
 * redeploy: the annotations are still in the on-disk OData cache, so a load
 * that took ~14s from Viking takes ~0.1s now.
 */
async function restoreSession(saved) {
  restoring = true;
  const failures = [];
  try {
    applySessionShell(saved);
    // The saved view goes up before the cells, so they come in where they will
    // be seen and a drag turns the view while they do.  A fresh page's camera
    // sits on its own target at the origin, where a turn has nothing to turn
    // about: in Orthographic view, which draws on both sides of the camera's
    // plane, the cells showed from there and zoomed and panned, but would not
    // rotate until the last one was in (2026-10-20).  With no saved view, the
    // first thing to land frames it, as a first load's does.
    const placed = renderer.restoreCamera(saved.camera) ? renderer.cameraState() : null;
    const framing = placed || display?.holdLimits ? null : { done: false };

    // Side by side, as loadMany does: a restore of thirty cells is thirty
    // Viking round trips (or cache reads) and thirty mesh jobs.  The list is
    // put back in the saved order afterwards, whatever order they finished.
    // Cells this restore added to the list, until each is drawn: what Stop drops.
    const started = new Set();
    const strip = loadStrip().begin(saved.neurons.length, {
      verb: 'Restoring', onStop: () => stopLoad(started),
    });
    await inParallel(saved.neurons, LOAD_CONCURRENCY, async (entry) => {
      const key = keyOf(entry.source, entry.id);
      const cell = strip.cell(entry.id, key);
      const listed = state.neurons.has(key);
      let ok = false;
      let dropped = false;
      try {
        const detail = await api.load({
          id: entry.id,
          source: entry.source,
          include_synapses: true,
          section_corrections: entry.corrections,
          live_registration: entry.registration,
          check_fresh: false,
          manual_moves: movesBody(entry.manualMoves),
        });
        if (strip.stopped) {
          dropped = true;
          if (!listed) api.unload(entry.source, entry.id).catch(() => {});
          return;
        }
        cell.name(detail.summary?.label);
        // A session saved before the annotation-aware seeding can carry a
        // disc-only technique for a cell partly or wholly traced as closed
        // curves -- the stored voxel-on-c1 state that re-broke on every
        // restore. Re-seed exactly those; a stored Hybrid or Closed-curve
        // choice (possibly hand-tuned) is the user's and is kept.
        let render = entry.render;
        const method = (render && render.method) || 'cylinder';
        if (method === 'cylinder' || method === 'curvemesh') {
          const seeded = renderForAnnotations(detail.summary, render);
          if (seeded.note) {
            render = seeded.render;
            statusEl.textContent = `c${entry.id}: ${seeded.note}`;
          }
        }
        // Before materials were per cell the Display panel held one name for
        // everything; a cell restored without its own inherits that one.
        const legacy = display?.legacyMaterial;
        const entryDisplay = entry.display && !entry.display.material && legacy
          ? { ...entry.display, material: resolveMaterialName(legacy) }
          : entry.display;
        const record = upsertNeuron(detail, {
          render, display: entryDisplay,
        });
        record.corrections = entry.corrections;
        record.correctionMode = entry.correctionMode;
        record.registration = entry.registration;
        record.manualMoves = entry.manualMoves ?? null;
        record.color = entry.color ?? record.color;
        record.visible = entry.visible !== false;
        record.opacity = entry.opacity ?? record.opacity;
        if (!listed) started.add(key);
        await addToScene(record, { frame: false, progress: cell, framing });
        if (strip.stopped && !state.neurons.has(key)) {
          dropped = true;
          return;
        }
        ok = true;
      } catch (error) {
        if (strip.stopped) {
          dropped = true;
        } else {
          // One unavailable neuron must not cost the whole session.
          console.warn(`could not restore c${entry.id}:`, error);
          failures.push(entry.id);
        }
      } finally {
        started.delete(key);
        cell.done(ok, { stopped: dropped });
      }
    }, { stopped: () => strip.stopped });
    strip.end();
    checkFreshness(saved.neurons.map((n) => state.neurons.get(keyOf(n.source, n.id))));
    const ordered = saved.neurons
      .map((n) => keyOf(n.source, n.id)).filter((k) => state.neurons.has(k));
    const entries = ordered.map((k) => [k, state.neurons.get(k)]);
    for (const [k] of entries) state.neurons.delete(k);
    for (const [k, r] of entries) state.neurons.set(k, r);

    if (saved.selected && state.neurons.has(saved.selected)) {
      state.selected = saved.selected;
    }
    const record = selectedRecord();
    if (record) {
      writeControls(record.render);
      focusSeen = state.selected;     // the panel has this cell's settings: a tick keeps them
    }

    // Again after the meshes, which can now say whether the saved view sees
    // them; falls back to framing the scene when there is no usable camera.
    // Not over a view the person moved while the cells came in.
    if (viewUntouched(placed ?? framing?.camera)) {
      if (!renderer.restoreCamera(saved.camera)) renderer.resetView();
    }
    // A saved view near the cells can still look at none of them (one saved
    // while its only cell had failed to build): framed, unless Held.
    if (!display?.holdLimits && renderer.seesAnything() === false) renderer.resetView();
    // The 2D box was set from the saved view when the restore began; the
    // camera is decided now, so the box can be judged against it.
    display?.syncTwoD();
    if (saved.view && saved.view !== 'render') activateView(saved.view);
    const graph = saved.ui?.graph;
    if (graph?.on) {
      graphSaved = Array.isArray(graph.saved) ? new Map(graph.saved) : null;
      graphIsolated = graph.saved ? graph.isolated ?? null : null;
      setGraphLook(true);
    }

    renderNeuronList();
    populateTableSelect();
    updateScopeNote();

    const when = saved.saved_at ? new Date(saved.saved_at).toLocaleString() : 'earlier';
    statusEl.textContent = strip.stopped
      ? `Stopped: restored ${ordered.length} of ${saved.neurons.length} neurons from ${when}`
      : failures.length
        ? `Restored ${saved.neurons.length - failures.length} of ${saved.neurons.length}`
          + ` neurons from ${when} (c${failures.join(', c')} unavailable)`
        : `Restored ${saved.neurons.length} neuron(s) from ${when}`;
    statusEl.className = failures.length && !strip.stopped ? 'status error' : 'status';
  } finally {
    restoring = false;
  }
  persist();
}

// -- redeploy detection ----------------------------------------------------
// Polled rather than pushed: an SSE stream would hold the single uvicorn
// worker open per tab, and a mesh build already blocks that worker for
// seconds at a time.
const BUILD_POLL_MS = 20000;
let knownBuild = null;
// The page reloaded itself for the server's build and still came back old (reloadIfStale).
let staleAfterReload = false;

/**
 * A page the browser showed from its own copy (a tab it restored, Back) runs
 * that copy's code, and the watch below compares the server's answers only with
 * each other, so a page that STARTED old never offered a reload: a restored tab
 * had no GraphApp the day after 2.28.0 (2026-10-22).  Such a page asks the
 * server which build it serves and reloads itself once when it is another
 * (settings.staleStart); if the reload comes back old too, the banner says so.
 * A page the server just sent asks nothing.  True while it is reloading.
 */
async function reloadIfStale() {
  if (!fromBrowserCache(performance.getEntriesByType?.('navigation')?.[0])) return false;
  let server;
  try {
    server = await api.build();
  } catch {
    return false;     // the watch takes over once the server answers
  }
  let tried = null;
  try { tried = sessionStorage.getItem(STALE_RELOAD_KEY); } catch { /* blocked */ }
  const what = staleStart({ page: pageAssets(import.meta.url), server, fromCache: true, tried });
  if (what === 'reload') {
    try {
      sessionStorage.setItem(STALE_RELOAD_KEY, server.assets);
    } catch {
      // Nowhere to note the reload, so nothing would stop a second: say it instead.
      staleAfterReload = true;
      return false;
    }
    window.location.reload();
    return true;
  }
  staleAfterReload = what === 'banner';
  return false;
}

async function watchForRedeploy() {
  // A first answer that fails (the server mid-restart, a dropped link) must not
  // leave this tab unwatched for good: the poll starts anyway, and the first
  // answer it gets is the one later ones are compared with (checkBuild).
  try {
    knownBuild = await api.build();
    updateMethodControls();
    const page = pageAssets(import.meta.url);
    if (staleAfterReload && page && page !== knownBuild.assets) {
      $('update-text').textContent = `This page is an older version (${page}) than the server's `
        + `(${knownBuild.assets}), and reloading did not replace it. Reload; if it stays, clear cached `
        + 'files in Settings.';
      $('update-banner').hidden = false;
    }
  } catch (error) {
    console.warn('build check unavailable:', error);
  }
  setInterval(checkBuild, BUILD_POLL_MS);
  // A background tab's timers are throttled to once a minute, and a tab the
  // browser has put to sleep runs none at all; check the moment it is
  // looked at again, so the banner is there when the user is.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') checkBuild();
  });
  window.addEventListener('focus', () => checkBuild());
}

async function checkBuild() {
  let current;
  try {
    current = await api.build();
  } catch {
    // A redeploy restarts the service, so a failed poll during one is normal.
    return;
  }
  if (!knownBuild) {
    // The start-up check failed: this is the first answer, so there is nothing yet to differ from.
    knownBuild = current;
    updateMethodControls();     // whether MeshLab is on this server is part of it
    return;
  }
  const codeChanged = current.version !== knownBuild.version
    || current.assets !== knownBuild.assets;
  const restarted = current.instance !== knownBuild.instance;
  if (!codeChanged && !restarted) return;

  // Save before announcing: the user may hit Reload immediately.  Any reload
  // of this tab from now on restores without asking, as the banner says.
  session.saveNow(state, renderer, uiFlags());
  session.restoreNextLoad();
  knownBuild = current;
  // The surfaces kept for Undo are keyed on the cell, its copy and stamp, not on the server's
  // geometry version: after a redeploy (or a restart) a cell loaded again must be asked for.
  // So are its outlines, annotations and skeleton, which the new code may make otherwise.
  meshCache.clear();
  keptExtras.clear();
  $('update-text').textContent = codeChanged
    ? `A new version (${current.version}) has been deployed. Your session will be restored.`
    : 'The server restarted. Reload to re-sync; your session will be restored.';
  $('update-banner').hidden = false;
}

function bindEvents() {
  $('volume').addEventListener('change', (e) => switchVolume(e.target.value));

  $('load-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    await loadFromSpec($('neuron-id').value);
  });

  $('search-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const label = $('search-label').value.trim();
    if (!label) return;
    // Results belong to the volume they were searched in: the one asked, not
    // whichever is selected when they arrive.
    const source = state.source;
    try {
      const results = await api.search(source, label, false);
      if (state.source === source) renderSearch(results, source);
    } catch (error) { fail(error); }
  });
  $('search-add-all').addEventListener('click', () =>
    addSearchResults(searchResults.map((r) => r.id)));
  $('search-add-selected').addEventListener('click', () =>
    addSearchResults(pickedSearchIds()));
  $('refresh-all').addEventListener('click', refreshAllCells);

  $('import-file').addEventListener('change', async (e) => {
    const [file] = e.target.files;
    if (!file) return;
    try {
      // Three kinds of file arrive here: a neuron export (annotation data),
      // a workspace document, and a plain-text structure list in the Viking
      // ExportPortal's format (IDs, ranges, and labels). A workspace is
      // opened as one -- cells, settings and, when captured, the viewport --
      // instead of erroring at the server.
      const body = await file.text();
      const { kind, doc } = importKind(body);
      if (kind === 'workspace') {
        await openWorkspace(doc, file.name);
      } else if (kind === 'structures') {
        // Not a JSON object: a structure list, in the same grammar the ID box
        // understands, loaded against the currently selected volume.  A list
        // of the one ID, `1178`, is JSON too -- a number -- and is this.
        await loadFromSpec(body);
      } else {
        const detail = await api.importFile(file);
        const record = upsertNeuron(detail, { render: readControls() });
        switchVolume(record.source);
        await addToScene(record);
      }
    } catch (error) { fail(error); }
    e.target.value = '';
  });

  // Editing any render control (all 26 of them, through the form's one delegated
  // listener) re-states what Apply would affect, but never rebuilds on its own.
  fillTechniques();
  $('quick-technique').addEventListener('change', () => applyQuickTechnique());

  $('apply-selected').addEventListener('click',
    () => applyRenderTo(selectedRecords()));
  $('apply-all').addEventListener('click',
    () => applyRenderTo([...state.neurons.values()]));
  $('defaults-selected').addEventListener('click',
    () => applyDefaultsTo(selectedRecords()));
  $('defaults-all').addEventListener('click',
    () => applyDefaultsTo([...state.neurons.values()]));
  $('save-tech-defaults').addEventListener('click', saveTechniqueDefaults);
  $('forget-tech-defaults').addEventListener('click', forgetTechniqueDefaults);
  $('set-startup-render').addEventListener('click', setStartupRender);
  $('clear-startup-render').addEventListener('click', clearStartupRender);
  $('attach-render').addEventListener('click', () => attachRenderTo(selectedRecords()));
  $('detach-render').addEventListener('click', () => detachRenderFrom(selectedRecords()));
  // The Defaults menu: opens on its button, closes after any choice or on a
  // click anywhere else, so it behaves like a native dropdown.
  const defaultsMenu = $('defaults-menu');
  const setMenuOpen = (open) => {
    if (open) {
      // Anchored above the button, right edges aligned. Fixed positioning,
      // because the strip is a scroll container that would clip the popup.
      const anchor = $('defaults-menu-btn').getBoundingClientRect();
      defaultsMenu.style.right = `${window.innerWidth - anchor.right}px`;
      defaultsMenu.style.bottom = `${window.innerHeight - anchor.top + 4}px`;
    }
    defaultsMenu.hidden = !open;
    $('defaults-menu-btn').setAttribute('aria-expanded', String(open));
  };
  $('defaults-menu-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    setMenuOpen(defaultsMenu.hidden);
  });
  // Only a chosen action closes it: the Clip IPL field lives in here too,
  // and typing into it must not slam the menu shut.
  defaultsMenu.addEventListener('click', (e) => {
    if (e.target.closest('button')) setMenuOpen(false);
  });
  document.addEventListener('click', (e) => {
    if (!defaultsMenu.hidden && !defaultsMenu.contains(e.target)) setMenuOpen(false);
  });
  $('render-notice-dismiss').addEventListener('click', () => {
    clearTimeout(noticeTimer);
    $('render-notice').hidden = true;
  });
  $('render-error-dismiss').addEventListener('click', () => {
    $('render-error').hidden = true;
  });

  // Rebuild re-fetches every neuron under its own settings -- it discards the
  // client mesh cache rather than unifying styles.
  $('rebuild').addEventListener('click', async () => {
    meshCache.clear();
    for (const record of state.neurons.values()) {
      renderer.removeAll(record.key);
      await addToScene(record, { frame: false });
    }
  });

  // The button turns into Stop all the moment Compare starts, so the second
  // click of a double click (or a held Enter) would land on it and stop what
  // it just started: clicks this soon after a Compare are ignored.
  let compareStartedAt = 0;
  $('compare-build').addEventListener('click', async () => {
    // While any pane has work queued or running the button reads Stop all.
    if (compare.running) {
      if (Date.now() - compareStartedAt > COMPARE_DOUBLE_CLICK_MS) compare.stopAll();
      return;
    }
    const record = state.neurons.get($('compare-cell').value);
    if (!record) {
      setCompareStatus(COMPARE_NO_CELL);
      return;
    }
    compareStartedAt = Date.now();
    await compare.build(record);
  });
  compare.onRunningChange = (running) => {
    const button = $('compare-build');
    button.textContent = running ? 'Stop all' : 'Compare';
    button.classList.toggle('is-stop', running);
  };
  $('compare-panes').addEventListener('change', (e) => {
    const before = compare.panes.length;
    compare.setPaneCount(Number(e.target.value));
    const message = paneCountMessage(before, compare.panes.length);
    if (message) setCompareStatus(message);
  });
  // Switching cells makes every pane's content — the Difference numbers
  // especially — describe the wrong cell until the next build. Say so.
  $('compare-cell').addEventListener('change', () => {
    compare.markStale($('compare-cell').value);
    setCompareStatus('Cell changed — press Compare to rebuild every pane.');
  });
  $('compare-sync').addEventListener('change', (e) => {
    compare.setSync(e.target.checked);
  });
  // The report: a PDF of what the panes hold (reportdialog.js), made on the server.
  const reportDialog = new ReportDialog($('compare-report-dialog'), {
    compare,
    onStatus: (text, kind) => setCompareStatus(text, kind),
    volumeName,
  });
  $('compare-report').addEventListener('click', () => reportDialog.open());
  // The same rendering under several registrations (registrationdialog.js): set up, then Compare.
  const registrationDialog = new RegistrationDialog($('compare-registrations-dialog'), {
    compare,
    onSetUp: () => { $('compare-panes').value = String(compare.panes.length); },
    onCompare: () => { if (!compare.running) $('compare-build').click(); },
  });
  $('compare-registrations').addEventListener('click', () => registrationDialog.open());

  $('show-axes').addEventListener('change', (e) => {
    renderer.setAxesVisible(e.target.checked);
    persist();
  });
  $('show-viewcube').addEventListener('change', (e) => {
    showViewCube(e.target.checked);
    persist();
  });
  // The page's alone, like the pivot itself: not in the session.  Ticked, a click on
  // the view says where; unticked, turns are about the middle of the picture again.
  $('pivot-toggle').addEventListener('change', (e) => {
    if (e.target.checked) renderer.armPivot(true);
    else renderer.clearPivot('off');
  });
  $('pivot-chip').addEventListener('click', () => {
    hideHelp();
    renderer.clearPivot('off');
  });
  $('ortho').addEventListener('change', (e) => {
    renderer.setOrthographic(e.target.checked);
    persist();
  });
  // The two expandable panels, each toggling on its own (togglePanel): they are
  // read together often enough that closing one to open the other would be a
  // nuisance.
  for (const [button, panel] of [['toggle-render', 'panel-render'],
    ['toggle-display', 'panel-display']]) {
    $(button).addEventListener('click', () => togglePanel(button, panel));
  }

  for (const button of document.querySelectorAll('[data-viewpoint]')) {
    button.addEventListener('click', () => lookAt(button.dataset.viewpoint));
  }

  $('reset-view').addEventListener('click', resetViewNow);
  // A flattened view's picture says what its Z is and the thickness it was scaled by.
  $('snapshot').addEventListener('click', () => renderer.snapshot(`sbfsem-${Date.now()}.png`,
    { caption: flatLines() }));
  bindVideo();

  $('undo-btn').addEventListener('click', () => undoHistory());
  $('redo-btn').addEventListener('click', () => redoHistory());
  window.addEventListener('keydown', (e) => {
    if (!(e.metaKey || e.ctrlKey)) return;
    // Never steal the shortcut from a field the user is typing in -- but a
    // checkbox, a slider or a dropdown that has the focus edits no text, and
    // is where the focus is left after the control just used.
    if (isTypingTarget(document.activeElement)) return;
    // A held key repeats: one step per press, not a pile-up of half-applied ones.
    if (e.repeat) return;
    const key = e.key.toLowerCase();
    if (key === 'z' && !e.shiftKey) { e.preventDefault(); undoHistory(); }
    else if ((key === 'z' && e.shiftKey) || key === 'y') {
      e.preventDefault();
      redoHistory();
    }
  });

  $('select-all').addEventListener('click', () => { selectAll(); renderNeuronList(); });
  $('select-none').addEventListener('click', () => { clearSelection(); renderNeuronList(); });
  $('clear-all').addEventListener('click', clearAllCells);
  initSidebarResizer();
  initPickReadout();
  initFullscreen();
  installColorPicker({ palette: PALETTE });
  installSelectPicker();

  // The camera is not part of the state store, so save it when a gesture ends
  // rather than on every damped frame.  The session only, not persist(): the
  // gesture's undo step was taken as it ended (above), and persist's second one,
  // 350 ms on, caught the next drag halfway when it began that soon after (a
  // view cube turn and then a drag on the cube, 2026-10-20).
  renderer.controls.addEventListener('end', () => {
    if (!restoring) session.save(state, renderer, uiFlags());
  });

  window.addEventListener('beforeunload', () => session.saveNow(state, renderer, uiFlags()));

  for (const tab of document.querySelectorAll('.tab')) {
    tab.addEventListener('click', async (event) => {
      // A tab clicked with the pointer does not keep the focus: the browser drew its own
      // ring round it later, at a key press or a return from another window, and only
      // then (2026-10-22).  Enter or Space (detail 0) keeps it, for the keyboard ring.
      if (event.detail > 0) tab.blur();
      activateView(tab.dataset.view);
      // "3D render" and "GraphApp" are one view in two looks.
      if (tab.dataset.view === 'render') await setGraphLook(tab.dataset.look === 'graph');
    });
  }
  bindGraphTools();

  $('net-refresh').addEventListener('click', buildNetwork);
  $('net-source').addEventListener('change', buildNetwork);
  $('net-layout').addEventListener('change', (e) => network.relayout(e.target.value));
  // Weight and naming are drawn from the kept answer: no second request.
  $('net-minweight').addEventListener('change', drawNetwork);
  $('net-labels').addEventListener('change', drawNetwork);

  $('strata-refresh').addEventListener('click', () => drawStratification());
  bindStrataExports();
  $('table-neuron').addEventListener('change', drawTable);
  $('table-cellonly').addEventListener('change', drawTable);
  $('table-filter').addEventListener('input', filterTable);
  bindUpdateBanner();
  bindAnnotationMenu();
  bindHelp();
  bindDialogForms();
  bindSaveDialogs();
}

/**
 * Enter in a dialog presses its primary button.  The dialogs are
 * `<form method="dialog">`, and the only submit button in each used to be
 * Cancel (the primary action is a plain button so it can check its input
 * first), so Enter in the Save name, New folder or Video number fields submitted
 * the form through its default button and closed the dialog as Cancel.  Cancel
 * is a plain button now, and Enter is handled here: a text box that has a
 * button of its own beside it (`data-enter`: New folder -> Create) presses that.
 */
function bindDialogForms() {
  const primaries = {
    'save-dialog': 'save-confirm', 'open-dialog': 'open-confirm', 'video-dialog': 'video-start',
    'strata-pdf-dialog': 'strata-pdf-create',
  };
  for (const [dialogId, primaryId] of Object.entries(primaries)) {
    const dialog = $(dialogId);
    const form = dialog.querySelector('form');
    // No submit is ever the answer: nothing in a form here is submitted.
    form.addEventListener('submit', (event) => event.preventDefault());
    for (const cancel of dialog.querySelectorAll('[data-dialog-cancel]')) {
      cancel.addEventListener('click', () => dialog.close('cancel'));
    }
    form.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || event.isComposing || event.defaultPrevented) return;
      const target = event.target;
      // Enter on a button presses that button; in a select it opens the list.
      if (!(target instanceof HTMLInputElement) || ['checkbox', 'radio', 'button'].includes(target.type)) return;
      event.preventDefault();
      // A held key repeats: one press of Enter is one press of the button.
      if (event.repeat) return;
      const button = $(target.dataset.enter || primaryId);
      if (button && !button.disabled) button.click();
    });
  }
}

// -- annotations context menu ----------------------------------------------
let contextRow = null;

function bindAnnotationMenu() {
  const menu = $('context-menu');
  const table = $('node-table');

  table.addEventListener('contextmenu', (event) => {
    const tr = event.target.closest('tbody tr');
    if (!tr || !tr.dataset.xyz) return;
    event.preventDefault();
    contextRow?.classList.remove('ctx-target');
    contextRow = tr;
    tr.classList.add('ctx-target');
    // Placed after unhiding so the measured size is real, and nudged back
    // inside the viewport when opened near an edge.
    menu.hidden = false;
    const { offsetWidth: w, offsetHeight: h } = menu;
    menu.style.left = `${Math.min(event.clientX, window.innerWidth - w - 8)}px`;
    menu.style.top = `${Math.min(event.clientY, window.innerHeight - h - 8)}px`;
  });

  const close = () => {
    menu.hidden = true;
    contextRow?.classList.remove('ctx-target');
    contextRow = null;
  };
  document.addEventListener('click', (event) => {
    if (!menu.hidden && !menu.contains(event.target)) close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
  });
  window.addEventListener('resize', close);
  table.addEventListener('scroll', close, true);

  $('ctx-show-location').addEventListener('click', async () => {
    const tr = contextRow;
    close();
    if (tr) await showAnnotationOnRender(tr);
  });
}

/**
 * Jump to the 3D view and fly the camera to one annotation.
 *
 * The cell has to be visible and meshed for the flight to land on anything, so
 * both are ensured first -- silently flying to a hidden neuron would look like
 * the feature had done nothing.
 */
async function showAnnotationOnRender(tr) {
  const point = tr.dataset.xyz.split(',').map(Number);
  const key = tr.dataset.neuronKey;
  const record = key ? state.neurons.get(key) : null;
  if (!point.every(Number.isFinite)) return;

  activateView('render');

  if (record && !record.visible) setVisible(record.key, true);
  if (record && !renderer.meshes.has(record.key)) await addToScene(record, { frame: false });

  const radius = Number(tr.dataset.radius) || 0.5;
  statusEl.textContent = `Flying to annotation ${tr.dataset.annotationId}…`;
  statusEl.className = 'status';
  // Close enough to read the annotation, far enough to keep its neighborhood
  // in frame; the radius sets the scale so a thick soma and a fine dendrite
  // both end up usefully framed.
  await renderer.flyTo(renderer.toWorld(point, record?.source || state.source),
    { duration: 1500, distance: Math.max(radius * 14, 3) });

  showPick({ point: { x: point[0], y: point[1], z: point[2] }, key, copied: false });
  statusEl.textContent = `Annotation ${tr.dataset.annotationId} — press m to reset the view`;
  persist();
}

/**
 * Show a pixel size in the Voxel box without touching the other controls: any
 * size, named in its list or not (a refitted 0.063 um, a saved 0.01).
 */
function setPixelSize(um) {
  renderForm.writeField('pixel_size_um', um);
}

/**
 * The render a cell's annotations can actually show, seeded from `base`.
 *
 * Discs plus traced outlines -- blood vessels, but any mixed cell -- get
 * the Hybrid method, the only one that shows every annotation (RC2 c1:
 * 15,834 curves + 5,922 discs spanning 661 x 731 x 1206 um; disc-only
 * pathways ground past every limit on it). Curve-only structures get the
 * closed-curve volume, which a disc pathway would render as nothing.
 * Vessels rasterize at 1 um (they thread the volume) and sweep their disc
 * half as adaptive tubes -- the voxel pathway's cluster fields over that
 * extent measured 11+ minutes where the tube sweep takes seconds; other
 * structures rasterize at 0.25 um and keep `base`'s pathway.
 *
 * Returns { render, note }; `note` is '' when the base render already fits.
 */
function renderForAnnotations(summary, base) {
  const s = summary || {};
  const curves = s.n_curve_annotations || 0;
  const discs = s.n_disc_annotations || 0;
  if (!curves) return { render: base, note: '' };
  const rasterUm = curvePixelFor(s);
  if (discs > 0) {
    const discPathway = s.type_id === 3 ? { pathway: 'adaptive_taubin' } : {};
    return {
      render: normalizeRender({
        ...base, method: 'hybrid', pixel_size_um: rasterUm, ...discPathway,
      }),
      note: `mixed annotations (${discs.toLocaleString()} discs, `
        + `${curves.toLocaleString()} outlines) — using the Hybrid technique`,
    };
  }
  return {
    render: normalizeRender({
      ...base, method: 'closedcurve', pathway: 'original', pixel_size_um: rasterUm,
    }),
    note: 'traced as closed curves — rendering as a closed-curve volume',
  };
}

// How many cells load side by side. Each is a Viking round trip (seconds,
// uncached) followed by a mesh job the server queues on its own workers, so
// the client only needs enough in flight to keep both busy.
const LOAD_CONCURRENCY = 6;

/**
 * Run `fn` over `items`, at most `limit` at a time, in order of start.  Once
 * `stopped()` is true no further item starts; those running finish.
 */
async function inParallel(items, limit, fn, { stopped = null } = {}) {
  const queue = [...items.entries()];
  const workers = Array.from({ length: Math.min(limit, queue.length) }, async () => {
    while (queue.length && !stopped?.()) {
      const [index, item] = queue.shift();
      await fn(item, index);
    }
  });
  await Promise.all(workers);
}

let loadProgress = null;
/** The load strip over the viewport, made on first use. */
function loadStrip() {
  loadProgress ??= new LoadProgress($('load-progress'));
  return loadProgress;
}

/** Load a set of structure IDs of the current volume (see loadCells). */
async function loadMany(ids) {
  const source = state.source;
  await loadCells(ids.map((id) => ({ source, id })));
}

/**
 * What a cell that is new to the list starts with: the render it has attached
 * on the server, else the panel's `base` seeded for what its annotations can
 * render (renderForAnnotations).  An attached render, and any setting applied
 * afterward, still win.  `note` says why when the start differs from `base`.
 */
function startingRender(detail, base) {
  if (detail.attached_render) {
    return {
      render: normalizeRender(detail.attached_render),
      note: 'loaded with its attached render',
    };
  }
  return renderForAnnotations(detail.summary, base);
}

/**
 * Load cells, `LOAD_CONCURRENCY` at a time, each `{ source, id }` in its own
 * volume: a typed list is all one volume, the cells open on the server (Show
 * them) can be several.  The server queues the meshes on its own workers, so
 * a handful in flight keeps both it and Viking busy without blocking other
 * users.  Failures are collected and reported at the end rather than stopping
 * the run -- one bad id in a pasted range should not cost the other nine.
 *
 * A cell that is already in the list keeps its render and display settings:
 * typing its ID again, or a range that includes it, must not throw away what
 * was tuned for it and rebuild its surface under the panel's settings.
 */
async function loadCells(entries) {
  const render = readControls();
  const cellDisplay = displayTemplate();
  const failures = [];
  const total = entries.length;
  let loaded = 0;
  let done = 0;
  const first = state.neurons.size === 0;

  const progress = (note) => {
    if (total > 1) {
      statusEl.textContent = `Loading ${done} of ${total}${note ? ` — ${note}` : '…'}`;
      statusEl.className = 'status';
    } else if (note) {
      statusEl.textContent = note;
      statusEl.className = 'status';
    }
  };
  progress('');
  // Cells this load added to the list, until each is drawn: what Stop drops.
  const started = new Set();
  const strip = loadStrip().begin(total, { onStop: () => stopLoad(started) });
  // The load's one early framing, on its first cloud of waiting points or its
  // first surface, whichever lands first: only for a load that will frame the
  // scene at its end anyway.
  const framing = first && !display?.holdLimits ? { done: false } : null;

  await inParallel(entries, LOAD_CONCURRENCY, async ({ source, id }) => {
    const key = keyOf(source, id);
    const cell = strip.cell(id, key);
    const listed = state.neurons.has(key);
    let ok = false;
    let dropped = false;
    try {
      // Synapses always come along with the cell; whether they are drawn is
      // the per-cell Show synapses toggle, which starts off on a fresh load.
      const detail = await api.load({ id, source, include_synapses: true, check_fresh: false });
      if (strip.stopped) {
        // Stopped while the annotations were on their way: never listed.
        dropped = true;
        if (!listed) api.unload(source, id).catch(() => {});
        return;
      }
      cell.name(detail.summary?.label);
      let settings = {};
      if (!listed) {
        const start = startingRender(detail, render);
        settings = { render: start.render, display: cellDisplay };
        if (start.note) progress(`c${id}: ${start.note}`);
      }
      const record = upsertNeuron(detail, settings);
      if (!listed) started.add(key);
      await addToScene(record, { frame: false, progress: cell, framing });
      if (strip.stopped && !state.neurons.has(key)) {
        dropped = true;
        return;
      }
      loaded += 1;
      ok = true;
    } catch (error) {
      if (strip.stopped) {
        dropped = true;
      } else {
        failures.push({ source, id });
        console.warn(`could not load c${id}:`, error);
      }
    } finally {
      started.delete(key);
      done += 1;
      cell.done(ok, { stopped: dropped });
      progress('');
    }
  }, { stopped: () => strip.stopped });
  strip.end();
  checkFreshness(entries.map(({ source, id }) => state.neurons.get(keyOf(source, id))));

  // Frame once at the end rather than after each, or a batch load walks the
  // camera around while it runs.  "Hold" in the Display panel means the user
  // has chosen a framing and does not want loading to move it; so has someone
  // who turned, panned or zoomed the view after the load's first framing.
  if (first && loaded && !display?.holdLimits && viewUntouched(framing?.camera)) renderer.resetView();
  // Not under a dropdown, slider or box someone is using in the cell panel
  // while the load ran: the events during the load waited for them, and so does this.
  renderNeuronListWhenIdle();
  persist();

  // Which cells, and in which volume when it is not just one.
  const unavailable = () => (new Set(failures.map((f) => f.source)).size === 1
    ? `c${failures.map((f) => f.id).join(', c')} unavailable in ${volumeName(failures[0].source)}`
    : `${describeCells(failures)} unavailable`);
  if (strip.stopped) {
    statusEl.textContent = `Stopped: loaded ${loaded} of ${total} cells`
      + (failures.length ? `; ${unavailable()}` : '');
    statusEl.className = 'status';
  } else if (!failures.length) {
    statusEl.textContent = loaded === 1
      ? `Loaded c${entries[0].id}` : `Loaded ${loaded} cells`;
    statusEl.className = 'status';
  } else {
    statusEl.textContent = `Loaded ${loaded} of ${total}; ${unavailable()}`;
    statusEl.className = 'status error';
  }
}

/**
 * Load whatever a structure specification names: IDs, ranges, and labels.
 *
 * The grammar is the Viking Data Export portal's, so the same strings work
 * typed here or posted as a .txt file (and the same files work in both
 * tools). Pure-numeric input parses locally; label terms go to the server,
 * which resolves them against the current volume's parent-structure labels
 * case-insensitively. Every term and every unreadable token is reported by
 * name -- input is never silently dropped.
 */
async function loadFromSpec(text) {
  const local = parseStructureSpec(text);
  if (!local.numericIds.length && !local.labels.length && !local.invalid.length) return;

  let ids = local.numericIds;
  let invalid = local.invalid;
  const notes = [];

  if (local.labels.length) {
    let resolved;
    try {
      resolved = await api.resolveStructures(state.source, text);
    } catch (error) { fail(error); return; }
    ids = resolved.ids;
    invalid = resolved.invalid;
    for (const l of resolved.labels) {
      if (l.error) notes.push(`"${l.term}": ${l.error}`);
      else if (!l.ids.length) notes.push(`"${l.term}" matched nothing`);
      else {
        const shown = l.samples.slice(0, 3).join(', ');
        notes.push(`"${l.term}" matched ${l.ids.length}${shown ? ` (${shown}${l.samples.length > 3 ? ', …' : ''})` : ''}`);
      }
    }
  }
  for (const bad of invalid) notes.push(`"${bad.text}": ${bad.reason}`);

  if (!ids.length) {
    statusEl.textContent = notes.length
      ? `Nothing to load — ${notes.join('; ')}` : 'Nothing to load';
    statusEl.className = 'status error';
    return;
  }
  await loadMany(ids);
  // loadMany wrote the outcome; the per-term report rides along after it.
  if (notes.length) statusEl.textContent += ` — ${notes.join('; ')}`;
}

// -- state -----------------------------------------------------------------
let analysesFor = null;   // analysesSignature() of what the Analyses tab shows
let analysesRun = 0;      // the latest renderAnalyses; an older one stands down
let tableFor = null;      // tableSignature() of what the Annotations table shows
let tableRun = 0;

/**
 * What the Analyses tab is built from: the focused cell, and the data of every
 * loaded cell (its "Loaded cells" card measures them all).  It was the focused
 * cell alone, so loading a second cell, unloading one, or changing the
 * Registration of the open one left the cards of before.
 */
function analysesSignature() {
  const cells = [...state.neurons.values()].map((r) => `${r.key}@${dataKey(r)}`).join(',');
  return `${state.selected ?? ''}#${cells}`;
}

/** What the Annotations table is drawn from: the cell it names, its data and the filter on cells. */
function tableSignature() {
  const key = $('table-neuron').value || state.selected || '';
  const record = key ? state.neurons.get(key) : null;
  return `${key}|${record ? dataKey(record) : ''}|${$('table-cellonly').checked}`;
}

/** Redraw what the open tab shows when what it was drawn from has changed. */
function refreshOpenView() {
  if (state.view === 'stats' && analysesSignature() !== analysesFor) renderAnalyses();
  if (state.view === 'table' && tableSignature() !== tableFor) drawTable();
}

// The focused cell as of the last event.  The render panel is an editor (see
// readControls): taking another cell's settings discards its edits, which is
// right when the focus moves to that cell and wrong for a tick of a box, Select
// all or Select none, which leave the focus where it was.
let focusSeen = null;

function onStateChange(event) {
  const focusMoved = state.selected !== focusSeen;
  focusSeen = state.selected;
  if (event === 'neurons' || event === 'selection') {
    renderNeuronListWhenIdle();
    populateTableSelect();
    // The compare tab's cell picker must list every loaded cell even when a
    // cell loads while that tab is already open — it used to refresh only on
    // tab entry, so a fresh load was invisible until you left and came back.
    fillCompareCells();
    // The connectivity query and the network build both take the loaded
    // cells of one volume.
    if (event === 'neurons') {
      shifts?.updateChip();
      connectivity?.loadedChanged();
      fillNetworkSources();
      display?.refreshVolumeOffsets();
    }
    // Focusing a neuron loads its own settings into the panel, so the panel
    // describes the neuron being looked at rather than the last one rendered.
    if (event === 'selection' && focusMoved) {
      const record = selectedRecord();
      if (record) writeControls(record.render);
    }
    updateScopeNote();
    // The cards issue several requests, so only refresh them when the tab is
    // visible and what they were built from has changed.  A batch load fires
    // an event per cell: wait for it to settle.
    if (event === 'neurons') debounced('open-view', refreshOpenView, 400);
    else refreshOpenView();
  }
  if (event === 'style') {
    for (const record of state.neurons.values()) {
      renderer.setStyle(record.key, {
        color: record.color, opacity: record.opacity, visible: record.visible,
      });
      // Features whose color follows the cell's follow it here; explicit
      // overrides keep their own color.
      const d = normalizeDisplay(record.display);
      renderer.setWireframeStyle(record.key, {
        color: d.wireframeColor || record.color, opacity: d.wireframeOpacity,
      });
      renderer.setDiscStyle(record.key, {
        color: d.discColor || record.color, opacity: d.discOpacity,
      });
      renderer.setAnnoWireStyle(record.key, {
        color: d.annoWireColor || record.color, opacity: d.annoWireOpacity,
      });
      renderer.setSkeletonStyle(record.key, {
        color: d.skeletonColor || record.color, opacity: d.skeletonOpacity,
      });
      shifts?.restyle(record);
    }
    shifts?.updateChip();
  }
  if (event === 'display') {
    for (const record of state.neurons.values()) {
      const d = normalizeDisplay(record.display);
      renderer.setSkeletonStyle(record.key, {
        color: d.skeletonColor || record.color,
        opacity: d.skeletonOpacity,
      });
      renderer.setSurface(record.key, d.showSurface);
      renderer.setWireframe(record.key, d.showWireframe);
      renderer.setZHide(record.key, zHideOf(record, d));
      for (const entry of record.synapseCounts) {
        const t = typeSettings(record.display, entry.local_name);
        renderer.setSynapseStyle(record.key, {
          type: entry.local_name,
          color: t.color || SYNAPSE_COLORS[entry.local_name] || rgbCss(entry.color),
          opacity: t.opacity,
          visible: t.show,
        });
      }
    }
  }
  if (event !== 'selection') persist();
  // Selection is not persisted (it is transient), but it IS undoable.
  else scheduleHistoryPush();
}

/** Display settings a newly loaded cell should start from. */
function displayTemplate() {
  const openKey = [...expandedCells][0] || state.selected;
  const source = openKey ? state.neurons.get(openKey) : null;
  // Styling is inherited from the open cell, but synapse markers always start
  // hidden on a fresh load -- only opening a saved workspace shows them.
  return source ? { ...source.display, showSynapses: false } : undefined;
}

// -- deferred help ---------------------------------------------------------
/**
 * Explanations shown only on a deliberate hover.
 *
 * The text used to sit permanently under each control, which is accurate and
 * in the way. Anything carrying `data-help` gets a panel after a pause long
 * enough that skimming across the toolbar never triggers one.
 */
const HELP_DELAY_MS = 900;
let helpTimer = null;
let helpEl = null;

/**
 * An explanation on a control of a cell's panel: the same hover help
 * everywhere in it -- after two seconds, beside the sidebar over the render
 * -- instead of a native tooltip here, a standing note there and two timings.
 */
const PANEL_HELP_DELAY_MS = 2000;
function setHelp(el, text, figure = null) {
  el.dataset.help = text;
  el.dataset.helpDelay = String(PANEL_HELP_DELAY_MS);
  el.dataset.helpSide = 'right';
  // A drawing above the words (helpfigures.js), by name.
  if (figure) el.dataset.helpFigure = figure;
  else delete el.dataset.helpFigure;
  return el;
}

let stopMovie = null;

function hideHelp() {
  clearTimeout(helpTimer);
  helpTimer = null;
  if (stopMovie) { stopMovie(); stopMovie = null; }
  if (helpEl) { helpEl.remove(); helpEl = null; }
}

function showHelp(target) {
  const text = target.dataset.help;
  if (!text) return;
  hideHelp();
  helpEl = document.createElement('div');
  helpEl.className = 'help-pop';
  helpEl.setAttribute('role', 'tooltip');
  // `data-help-figure` names a drawing (helpfigures.js, built from constants) that
  // goes above the words and across all their columns.
  const figure = target.dataset.helpFigure ? buildFigure(target.dataset.helpFigure) : null;
  // A render option's movie (helpmovies.js): real renders of a cell at a sweep of the
  // option's values, above the words like a figure.
  const spec = target.dataset.helpMovie
    ? movieFor(movieManifest(), target.dataset.helpMovie, target.dataset.helpMethod) : null;
  const movie = spec ? buildMovie(spec, {
    current: target.dataset.helpValue,
    reducedMotion: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false,
  }) : null;
  if (movie) stopMovie = movie.stop;
  if (figure || movie) {
    helpEl.classList.add('has-figure');
    helpEl.append(...[movie?.el, figure].filter(Boolean), document.createTextNode(text));
  } else {
    helpEl.textContent = text;
  }
  document.body.append(helpEl);

  // A long help (the Pathway one is nearly 3,000 characters) is taller than the
  // window in the ordinary column, and its last paragraphs were cut off: lay it
  // out in more columns until it fits.  Only a window too small even for that
  // gets a panel that scrolls, which then has to take the pointer.
  const limit = window.innerHeight - 16;
  const layout = pickHelpLayout(helpLayouts(window.innerWidth), limit, ({ columns, width }) => {
    helpEl.style.width = columns > 1 ? `${width}px` : '';
    helpEl.style.maxWidth = `${width}px`;
    helpEl.style.columnCount = columns > 1 ? String(columns) : '';
    return helpEl.getBoundingClientRect().height;
  });
  helpEl.classList.toggle('scroll', !layout.fits);
  helpEl.style.maxHeight = layout.fits ? '' : `${limit}px`;

  // Positioned after insertion so the measured size is real, and clamped so a
  // control at the right edge of a full toolbar still shows its panel.
  const anchor = target.getBoundingClientRect();
  const pop = helpEl.getBoundingClientRect();
  let left = Math.max(8, Math.min(
    anchor.left, window.innerWidth - pop.width - 8));
  // Above the control: these live in the bottom toolbar, so below is off screen.
  let top = anchor.top - pop.height - 8;
  if (top < 8) top = anchor.bottom + 8;
  // And never past the bottom edge, whichever side it landed on.
  top = Math.max(8, Math.min(top, window.innerHeight - pop.height - 8));
  // `data-help-side="right"`: beside the control, over the render, for help
  // on a panel whose own controls sit right above and below it.
  // A control in a cell's panel is beside the panel's edge, not its own: a switch
  // whose words wrap ends mid-panel, and its help would cover the panel.
  const panelRight = target.closest('.cell-panel')?.getBoundingClientRect().right ?? anchor.right;
  const right = Math.max(anchor.right, panelRight);
  if (target.dataset.helpSide === 'right'
      && right + 12 + pop.width <= window.innerWidth - 8) {
    left = right + 12;
    top = Math.max(8, Math.min(anchor.top, window.innerHeight - pop.height - 8));
  }
  helpEl.style.left = `${left}px`;
  helpEl.style.top = `${top}px`;
}

function bindHelp() {
  // The movies' manifest is small; the movies themselves load when a help shows one.
  setTimeout(() => { loadMovies(); }, 2500);
  const arm = (event) => {
    const target = event.target.closest('[data-help]');
    if (!target) return;
    clearTimeout(helpTimer);
    // `data-help-delay`: a control that wants a longer, deliberate hover.
    const delay = Number(target.dataset.helpDelay) || HELP_DELAY_MS;
    helpTimer = setTimeout(() => showHelp(target), delay);
  };
  document.addEventListener('pointerover', arm);
  document.addEventListener('pointerout', (event) => {
    // A panel that scrolls is reached by moving onto it, and left by moving off.
    if (helpEl?.contains(event.relatedTarget)) return;
    if (event.target.closest('[data-help]') || helpEl?.contains(event.target)) hideHelp();
  });
  // A pointer that leaves the window, a click, or a scroll all mean the
  // hover is over -- except on the panel itself, when it scrolls.
  document.addEventListener('pointerdown', (event) => {
    if (!helpEl?.contains(event.target)) hideHelp();
  });
  window.addEventListener('blur', hideHelp);
  window.addEventListener('scroll', (event) => {
    if (!helpEl?.contains(event.target)) hideHelp();
  }, true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideHelp(); });
}

/** `bare` drops the hedging word, for places that already show a ≈ sign. */
function humanSeconds(s, { bare = false } = {}) {
  const about = bare ? '' : 'about ';
  if (s < 1) return bare ? 'under 1 s' : 'under a second';
  if (s < 60) return `${about}${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return r ? `${about}${m} min ${r} s` : `${about}${m} min`;
}

/** What the form needs to know about this server: whether it can run MeshLab. */
const renderCtx = () => ({ meshlabOff: knownBuild !== null && knownBuild.meshlab === false });

/** Gray out controls the chosen method ignores, and estimate the cost. */
function updateMethodControls() {
  const options = readControls();
  // Which controls show, grey out (with the reason in their help) or disable is
  // the schema's, applied by the form: see `show` and `inert` in render_schema.py.
  renderForm.refresh(options, renderCtx());
  gateTechniques();

  // Cost: a compact chip, with the reasoning behind it on hover rather than
  // occupying a paragraph of the toolbar.
  const targets = selectedRecords();
  // Settings that will not build as set for these cells, marked before Apply.
  const notes = updateConflicts(targets, options);
  const chip = $('cost-chip');
  if (!chip) return;
  if (!targets.length) { chip.hidden = true; return; }
  // The outline methods: the server's own estimate, one number for the chip.
  if (CURVE_METHODS.has(options.method) && showEstimateChip(chip, targets, options)) return;
  const total = targets.reduce((sum, r) => sum + estimateBuildSeconds(r, options), 0);
  const slowest = targets.reduce((a, r) =>
    Math.max(a, estimateBuildSeconds(r, options)), 0);
  const heavy = total > LONG_WAIT_S;
  chip.hidden = false;
  chip.classList.toggle('warn', heavy);
  chip.classList.remove('over');
  chip.textContent = `${heavy ? '⚠' : '≈'} ${humanSeconds(total, { bare: true })}`;

  const cells = targets.length === 1 ? `c${targets[0].id}` : `${targets.length} cells`;
  let help = `Rebuilding ${cells} with this method takes ${humanSeconds(total)} on the server.`;
  if (heavy) {
    if (options.pathway === 'voxel') {
      help += ` Voxel remesh orig rebuilds every segment at least 1.5 µm across from `
        + `its distance field, about as much work as Original.`;
    } else if (options.pathway === 'convolution') {
      help += ` The convolution surface sums a kernel along every skeleton segment `
        + `on a whole-cell grid; a soma's kernels reach two radii out, so it is the `
        + `most expensive field. A coarser Fine voxel (0.1 µm) cuts it 4–5×.`;
    } else if (options.pathway === 'ultraliser') {
      help += ` Ultraliser voxelizes the proxies on a whole-cell grid and then `
        + `smooths the contour; a coarser Fine voxel (0.2 µm is its own default) `
        + `cuts the work several-fold.`;
    } else if (options.pathway === 'voxel2') {
      help += ` Voxel remesh contours the whole cell as one field at dendrite `
        + `resolution — one surface, at several times the work of Voxel remesh orig. `
        + `A coarser Fine voxel (0.1 µm) cuts it 4–5×.`;
    } else if (options.method === 'cylinder') {
      const alt = targets.reduce((sum, r) =>
        sum + estimateBuildSeconds(r, { ...options, method: 'curvemesh' }), 0);
      help += ` Cylinder (Line3) fits a tangent frame per annotation, roughly `
        + `25x the work of Tube — the same cells take ${humanSeconds(alt)} as Tube. `
        + `Smoothing is not the expensive part.`;
    } else if (CURVE_METHODS.has(options.method)) {
      help += ` Halving Voxel size multiplies the grid about eightfold; `
        + `0.10 µm is four times lighter than 0.05 µm.`;
    }
    if (slowest > 120) help += ' The browser waits the whole time.';
  }
  if (notes.length) help += `\n\n${notes.join('\n')}`;
  chip.dataset.help = help;
  chip.title = '';
}

// The render panel's row for settings that will not build as set (conflictpanel.js).
let conflictPanel = null;

/**
 * Mark the render settings that will not build as set for the selected cells
 * and list what to change (renderconflicts.js): under any technique, a selected
 * cell whose build with these very settings failed (addToScene's
 * `renderFailure`); and from the server's estimates, the outline methods' Voxel
 * size and the Fine voxel and Face cap of a whole-cell field.  An answer not yet
 * in is asked for once typing stops, and the panel is drawn again when it comes.
 * Returns the field notes for the chip's hover (an automatic Fine voxel the Face
 * cap holds coarser).
 */
function updateConflicts(targets, options) {
  if (!conflictPanel) return [];
  const o = normalizeRender(options);
  const signature = effectiveSignature(o);
  const failures = targets.map((r) => (r.renderFailure?.signature === signature ? r.renderFailure : null));
  const estimated = CURVE_METHODS.has(o.method) || buildsField(o);
  if (!targets.length || !(estimated || failures.some(Boolean))) {
    conflictPanel.show(null);
    return [];
  }
  const held = targets.map((r) => (estimated ? estimates.peek(r, o) : null));
  if (estimated && held.some((e) => e === undefined)) {
    const wanted = estimateFor(targets, o);
    estimates.soon('conflicts', targets, o, () => {
      if (estimateFor(selectedRecords(), readControls()) === wanted) updateMethodControls();
    });
  }
  const entries = targets.map((r, i) => ({
    name: `c${r.id}`, estimate: held[i] || undefined, failure: failures[i], summary: r.summary,
  })).filter((x) => x.estimate || x.failure);
  conflictPanel.show(renderConflicts(entries, o, { expert: isExpert() }));
  return fieldNotes(entries.filter((x) => x.estimate), o);
}

// The chip's next look at an interim answer.
let chipRetry = 0;

/** One string for "these cells with these settings", to tell an answer that is still wanted. */
const estimateFor = (records, options) =>
  records.map((r) => `${r.key}|${r.loadGen ?? ''}`).join(';') + JSON.stringify(estimateQuery(options));

/**
 * The cost chip from the server's estimate, for an outline method: what the
 * selected cells would cost at this Voxel size, amber when it is a long wait or
 * a large share of the memory, red when a size is past a limit (with the
 * finest that fits).  The answers are asked for once typing stops; until they
 * are in the chip says so.  False when the server cannot say (no outlines and no
 * discs, an older server), and the rough estimate stands.
 */
function showEstimateChip(chip, targets, options) {
  const held = targets.map((r) => estimates.peek(r, options));
  const wanted = estimateFor(targets, options);
  const redraw = () => {
    if (estimateFor(selectedRecords(), readControls()) === wanted) updateMethodControls();
  };
  // An interim answer (the server is still making a cell's reference raster) is
  // held a moment and asked again; the chip is drawn again when it has gone.
  if (held.some((e) => interim(e))) {
    clearTimeout(chipRetry);
    chipRetry = setTimeout(redraw, estimates.interimMs + 100);
  }
  if (held.some((e) => e === undefined || e?.pending)) {
    if (held.some((e) => e === undefined)) estimates.soon('chip', targets, options, redraw);
    chip.hidden = false;
    chip.textContent = '≈ …';
    chip.classList.remove('warn', 'over');
    chip.dataset.help = 'Asking the server what building the selected cells at this Voxel size would '
      + 'cost (it answers in about a second, a few the first time for a cell never built with outlines).';
    chip.title = '';
    return true;
  }
  const shown = estimateChip(targets.map((r, i) => ({ name: `c${r.id}`, estimate: held[i] })));
  if (!shown) return false;
  chip.hidden = false;
  chip.textContent = shown.text;
  chip.classList.toggle('warn', shown.level === 'warn');
  chip.classList.toggle('over', shown.level === 'over');
  chip.dataset.help = shown.help;
  chip.title = '';
  return true;
}

// -- the Voxel size that fits --------------------------------------------------
// A Closed curve or Hybrid build too fine for its cell is built at the finest
// Voxel size that fits that cell (the server's estimate, before anything is
// built), so cells of different sizes can end up at different sizes; a notice
// names each cell and its size.  The server's refusal after rasterizing stays
// as the backstop (addToScene).

// The cells given a coarser size lately, for one notice that names them all.
const coarsened = [];
// A note this old belongs to an earlier batch and leaves the notice.
const COARSENED_KEEP_MS = 20000;

/** Say that `record` is built at `to` µm instead of `from`, and why. */
function noteCoarsened(record, from, to, why) {
  const now = Date.now();
  for (let i = coarsened.length - 1; i >= 0; i -= 1) {
    if (now - coarsened[i].at > COARSENED_KEEP_MS || coarsened[i].key === record.key) coarsened.splice(i, 1);
  }
  coarsened.push({ key: record.key, name: `c${record.id}`, from, to, why, at: now });
  const notice = coarsenedNotice(coarsened);
  showRenderNotice(notice.headline, notice.detail);
  syncPixelControl();
}

/**
 * When every selected outline cell is built at one Voxel size, the control
 * shows it; cells at different sizes leave the control as it is (the notice
 * and the chip name each one's).
 */
function syncPixelControl() {
  if (!CURVE_METHODS.has(readControls().method)) return;
  const sizes = new Set(selectedRecords()
    .map((r) => normalizeRender(r.render))
    .filter((o) => CURVE_METHODS.has(o.method))
    .map((o) => o.pixel_size_um));
  if (sizes.size === 1) setPixelSize([...sizes][0]);
  updateScopeNote();
}

/**
 * Hold `record`'s outline build to the server's estimate: when its Voxel size
 * is past a limit, set the finest that fits on the cell and say so.  The size
 * the app seeds a cell with (0.25 µm, 1 µm for vessels) and anything coarser is
 * not asked about: those were chosen to fit, the server's refusal backs them,
 * and a load would otherwise wait on an estimate for every traced cell.
 * Answers the size set, or null when the build goes ahead as asked.
 */
async function fitPixelFor(record, options, current = () => true) {
  if (!CURVE_METHODS.has(options.method)) return null;
  if (options.pixel_size_um >= curvePixelFor(record.summary)) return null;
  const estimate = await estimates.settled(record, options);
  // The estimate took a while: written only if the draw that asked is still the
  // cell's newest and the cell still holds the options it was for (a size applied
  // meanwhile is the person's).
  const now = state.neurons.get(record.key);
  const fits = fitStillWanted(estimate, options, now?.render, current());
  if (!fits) return null;
  setRenderOptions(record.key, { ...now.render, pixel_size_um: fits });
  noteCoarsened(record, options.pixel_size_um, fits, mainReason(estimate));
  return fits;
}

/**
 * The same for a Render compare layer, whose options are its own (the cell's
 * render is not touched): `{ pixel, why }` when it is too fine, else null.
 */
async function fitPixelForPane(record, options) {
  if (!CURVE_METHODS.has(options.method)) return null;
  if (options.pixel_size_um >= curvePixelFor(record.summary)) return null;
  const estimate = await estimates.settled(record, options);
  const fits = fittedPixel(estimate, options);
  return fits ? { pixel: fits, why: mainReason(estimate) } : null;
}

// -- settings that draw the cell ---------------------------------------------------
// A cell its settings cannot build is built with the next settings that may draw
// it (renderfallback.js), unless expert render mode is on (Settings).  The cell
// keeps the first that does until its annotations change (state.upsertNeuron), a
// notice names it with a button to build the cell as set after all, and the
// cell's row is marked.

// The cells drawn with other settings lately, for one notice that names them all.
const fellBack = [];

/**
 * Build `record` with the settings after `asked`, which failed as `failure`
 * says, one after another through `obtain` (addToScene's), and keep the first
 * that draws the cell.  Answers `{ got }` for that one, `{}` when none drew it
 * (the cell has `asked` back), or null once the draw is retired.  A failure that
 * is not the settings' (the network went) gives `asked` back and is thrown.
 */
async function fallBack(record, asked, failure, obtain, current, progress) {
  const what = failurePhrase(failure, asked);
  for (const candidate of fallbacks(asked, record.summary, failure)) {
    if (!current()) return null;
    statusEl.textContent = `c${record.id}: ${what}; trying ${candidate.text}…`;
    statusEl.className = 'status busy';
    progress?.stage(`trying ${candidate.text}`);
    setRenderOptions(record.key, candidate.options);
    let got;
    try {
      got = await obtain(normalizeRender(record.render));
    } catch (error) {
      if (error?.aborted) return null;
      if (failureOf({ error })) continue;
      setRenderOptions(record.key, asked);
      throw error;
    }
    if (!got) return null;
    if (got.mesh.n_faces) {
      record.fallback = {
        asked, failure, used: got.options, text: candidate.text, detail: candidate.detail,
      };
      record.renderFailure = null;
      noteFellBack(record);
      // The panel describes the cell being looked at: what it is drawn with now.
      if (state.selected === record.key) writeControls(got.options);
      renderNeuronListWhenIdle();
      return { got };
    }
  }
  setRenderOptions(record.key, asked);
  return {};
}

/** Why a cell is drawn with other settings, in a sentence. */
function fallbackWhy(record) {
  const { failure, asked } = record.fallback;
  const makeup = failure.kind === 'empty' ? makeupText(record.summary) : '';
  return `${failurePhrase(failure, asked)}${makeup ? ` (${makeup})` : ''}`;
}

/** Say which cells are drawn with other settings than asked, and offer them as set. */
function noteFellBack(record) {
  const now = Date.now();
  for (let i = fellBack.length - 1; i >= 0; i -= 1) {
    if (now - fellBack[i].at > COARSENED_KEEP_MS || fellBack[i].key === record.key) fellBack.splice(i, 1);
  }
  fellBack.push({ key: record.key, at: now });
  const listed = fellBack.map((x) => state.neurons.get(x.key)).filter((r) => r?.fallback);
  if (!listed.length) return;
  const keeps = ' until its annotations change; expert render mode (Settings) builds settings as set.';
  const [one] = listed;
  showRenderNotice(
    listed.length === 1 ? `c${one.id} is drawn with ${one.fallback.text}`
      : `${listed.length} cells are drawn with other settings`,
    listed.length === 1 ? `: ${fallbackWhy(one)}. The cell keeps these settings${keeps}`
      : `: ${listed.map((r) => `c${r.id} with ${r.fallback.text} (${fallbackWhy(r)})`).join('; ')}. `
        + `Each keeps its settings${keeps}`,
    { ms: 30000, action: { label: 'Build as I set it', run: () => buildAsSet(listed) } });
}

/**
 * "Build as I set it": the cells' own settings again, built as set this once,
 * so a cell they cannot build is left failed and the Render panel marks them.
 */
async function buildAsSet(records) {
  for (const record of records) {
    if (!state.neurons.has(record.key) || !record.fallback) continue;
    const { asked } = record.fallback;
    record.fallback = null;
    setRenderOptions(record.key, asked);
    if (state.selected === record.key) writeControls(asked);
    record.verbatimOnce = true;
    renderer.removeMesh(record.key);
    await addToScene(record, { frame: false });
  }
  renderNeuronListWhenIdle();
  updateScopeNote();
  persist();
}

// -- render settings -------------------------------------------------------
// The controls themselves -- 22 in the panel's row, 10 in its More menu -- are
// built from the render schema by renderform.js, with the ids they always had.
let renderForm = null;

function buildRenderPanel() {
  const menu = $('defaults-menu');
  // The chip's reasoning is long (cell by cell for an outline method): it opens on
  // a deliberate two-second hover, not on a pass across the strip.
  $('cost-chip').dataset.helpDelay = '2000';
  renderForm = buildRenderForm({
    mounts: {
      strip: { parent: $('panel-render'), before: $('cost-chip') },
      more: { parent: menu, before: menu.querySelector('hr') },
    },
    ids: 'legacy',
    // Editing any control re-states what Apply would affect, but never
    // rebuilds on its own.
    onInput: () => { updateScopeNote(); syncQuickTechnique(); },
  });
  conflictPanel = new ConflictPanel({
    form: renderForm,
    mount: $('panel-render'),
    // A suggestion goes into the controls as if typed; Apply builds it.
    onPick: (set) => {
      writeControls({ ...readControls(), ...set });
      updateScopeNote();
    },
  });
}

// The sidebar panel is an editor, not the live setting.  It seeds newly loaded
// neurons and is applied to existing ones on demand, which is what lets several
// neurons be on screen at once under different methods, and stops an idle
// fiddle with a slider from silently triggering a multi-second rebuild.
function readControls() {
  return renderForm.read();
}

function writeControls(options) {
  const o = normalizeRender(options);
  renderForm.write(o);
  // Which fields show or grey out, and what a rebuild would cost, follow the
  // options written: Clear startup default and the Defaults buttons left both stale.
  updateMethodControls();
  // Selecting a cell writes its settings here, so the toolbar picker has to
  // follow or it would name a technique the selected cell is not using.
  if ($('quick-technique')?.options.length) syncQuickTechnique();
}

/** Explain what the two Apply buttons will do right now. */
function updateScopeNote() {
  // The cost chip names the selected cells, so it follows the selection, ticked or not.
  updateMethodControls();
  const note = $('render-scope');
  const applySelected = $('apply-selected');
  if (!note || !applySelected) return;
  const chosen = selectedRecords();
  applySelected.disabled = chosen.length === 0;
  // Short, because this lives in a horizontal bar where every pixel competes.
  applySelected.textContent = chosen.length > 1
    ? `Apply to ${chosen.length}` : 'Apply to selected';

  if (!chosen.length) {
    note.textContent = 'nothing selected';
    note.dataset.help = 'These settings seed the next cell you load. '
      + 'Tick cells in the Loaded list to apply them to existing ones.';
    note.classList.remove('pending');
    return;
  }
  const wanted = readControls();
  const differing = chosen.filter(
    (r) => effectiveSignature({ ...r.render, color_by_strata: wanted.color_by_strata })
        !== effectiveSignature(wanted));
  note.textContent = differing.length === 0
    ? (chosen.length === 1 ? `matches c${chosen[0].id}` : `matches all ${chosen.length}`)
    : `${differing.length} of ${chosen.length} differ`;
  note.dataset.help = differing.length === 0
    ? 'The controls match what the selected cells are already rendered with.'
    : `${differing.length} of the ${chosen.length} selected cells are rendered with `
      + 'different settings. Apply rebuilds them.';
  note.classList.toggle('pending', differing.length > 0);
}

// -- per-technique defaults --------------------------------------------------
// Every technique has its own best parameters, so "Defaults" must never
// switch the technique itself (it used to jump everything back to Voxel
// remesh, discarding whichever pathway was being tuned). The recommended
// values ship in DEFAULT_RENDER; "Save technique defaults" overlays the
// user's own tuning for the current technique, kept in this browser.
function techniqueLabel(options) {
  const match = matchPreset(options);
  return match ? match.label : `${options.method}/${options.pathway}`;
}

function saveTechniqueDefaults() {
  const options = readControls();
  const all = loadTechDefaults();
  all[techKey(options.method, options.pathway)] =
    Object.fromEntries(TECH_KNOBS.map((k) => [k, options[k]]));
  if (!storeTechDefaults(all)) {
    statusEl.textContent = 'Could not save: this browser will not keep site data '
      + '(a private window, or storage turned off)';
    statusEl.className = 'status error';
    return;
  }
  statusEl.textContent = `Saved as your defaults for ${techniqueLabel(options)}`;
  statusEl.className = 'status';
}

// The startup default: what this browser's controls open with, and what
// newly loaded cells therefore inherit. Per browser, like technique
// defaults; a render attached to a specific cell on the server still wins.
const STARTUP_RENDER_KEY = 'sbfsem:startup-render';

function startupRender() {
  try { return JSON.parse(localStorage.getItem(STARTUP_RENDER_KEY)); }
  catch { return null; }
}

function setStartupRender() {
  const options = readControls();
  try {
    localStorage.setItem(STARTUP_RENDER_KEY, JSON.stringify(options));
  } catch {
    statusEl.textContent = 'Could not save: this browser will not keep site data '
      + '(a private window, or storage turned off)';
    statusEl.className = 'status error';
    return;
  }
  statusEl.textContent = `${techniqueLabel(options)} is now this browser's startup default`;
  statusEl.className = 'status';
}

function clearStartupRender() {
  try { localStorage.removeItem(STARTUP_RENDER_KEY); } catch { /* nothing was kept there */ }
  writeControls(DEFAULT_RENDER);
  updateScopeNote();
  statusEl.textContent = 'Startup default cleared — back to the app default';
  statusEl.className = 'status';
}

/** Save each record's own current render onto its cell, server-side. */
async function attachRenderTo(records) {
  if (!records.length) {
    statusEl.textContent = 'Select at least one cell to attach a render to';
    statusEl.className = 'status error';
    return;
  }
  let done = 0;
  const failed = [];
  for (const record of records) {
    try {
      await api.attachRender(record.source, record.id, normalizeRender(record.render));
      done += 1;
    } catch (error) {
      failed.push(record);
      fail(error);
    }
  }
  if (failed.length) {
    // Said after fail(): its message is the reason, this one is the count.
    statusEl.textContent = `Attached the render to ${done} of ${records.length} cell(s); `
      + `${describeCells(failed)} failed — ${statusEl.textContent}`;
    statusEl.className = 'status error';
    return;
  }
  statusEl.textContent = `Attached the current render to ${done} cell(s) on the server`;
  statusEl.className = 'status';
}

async function detachRenderFrom(records) {
  if (!records.length) {
    statusEl.textContent = 'Select at least one cell to remove an attached render from';
    statusEl.className = 'status error';
    return;
  }
  let removed = 0;
  const failed = [];
  for (const record of records) {
    try {
      const r = await api.detachRender(record.source, record.id);
      if (r.removed) removed += 1;
    } catch (error) {
      failed.push(record);
      fail(error);
    }
  }
  if (failed.length) {
    statusEl.textContent = `Could not remove the attached render from ${describeCells(failed)}`
      + ` (${removed} of ${records.length} removed) — ${statusEl.textContent}`;
    statusEl.className = 'status error';
    return;
  }
  statusEl.textContent = removed
    ? `Removed the attached render from ${removed} cell(s)`
    : 'No attached render to remove';
  statusEl.className = 'status';
}

function forgetTechniqueDefaults() {
  const options = readControls();
  const all = loadTechDefaults();
  delete all[techKey(options.method, options.pathway)];
  const kept = storeTechDefaults(all);
  writeControls(techniqueDefaults(options.method, options.pathway));
  statusEl.textContent = kept
    ? `${techniqueLabel(options)} is back to the recommended defaults`
    : `${techniqueLabel(options)} shows the recommended defaults, but this browser will `
      + 'not keep site data: your saved tuning comes back on the next visit';
  statusEl.className = kept ? 'status' : 'status error';
}

/**
 * Reset cells to the current technique's default rendering.
 *
 * The technique itself (method + pathway) is kept: each technique has its
 * own best parameters, and "Defaults" resets to those — the user's saved
 * tuning if any, the recommended values otherwise.  The toolbar controls
 * are set first, so what the toolbar shows is exactly what was applied.
 * Depth coloring is preserved the same way Apply preserves it: it lives in
 * each cell's own panel.
 */
async function applyDefaultsTo(records) {
  const { method, pathway } = readControls();
  writeControls(techniqueDefaults(method, pathway));
  await applyRenderTo(records, { fitPixel: true });
}

/**
 * Rebuild `records` under the toolbar's render options.  With `fitPixel` (the
 * technique picker and the Defaults buttons, which ask for a technique "at
 * its best"), each cell's outline raster is fitted to it by curvePixelSeed,
 * and a notice says so.
 *
 * Depth shading is deliberately left alone: it moved into each cell's own
 * panel, so applying the mesh settings must not silently switch it off for
 * every cell that had it on.
 */
async function applyRenderTo(records, { fitPixel = false } = {}) {
  if (!records.length) {
    statusEl.textContent = 'Select one or more cells first';
    statusEl.className = 'status error';
    return;
  }
  const options = readControls();
  // readControls clamps out-of-range typed values to what the server accepts;
  // write the result back so the boxes show what is actually being rendered.
  writeControls(options);
  const fitted = new Map();
  const changed = records.filter((r) => {
    const seeded = fitPixel ? curvePixelSeed(r, options) : null;
    if (seeded) fitted.set(r, seeded);
    const asked = setRenderOptions(r.key, {
      ...options, ...(seeded ? { pixel_size_um: seeded } : {}),
      color_by_strata: r.render.color_by_strata,
    });
    // Settings asked for anew: what the cell fell back from is no longer what it asks.
    if (asked) r.fallback = null;
    return asked;
  });
  if (fitted.size) {
    const sizes = [...new Set(fitted.values())];
    // One size for every fitted cell: the panel shows what was rendered.
    if (sizes.length === 1) setPixelSize(sizes[0]);
    const cells = [...fitted.keys()].map((r) => `c${r.id}`).join(', ');
    showRenderNotice(`Voxel size set to ${sizes.join(' / ')} µm`,
      ` for ${cells}, instead of the ${options.pixel_size_um} µm default:`
      + ` cells traced with outlines are rasterized coarser under`
      + ` ${techniqueLabel(options)}, or they run past the server's voxel limit.`);
  }
  if (!changed.length) {
    statusEl.textContent = 'Those cells already use these settings';
    statusEl.className = 'status';
    updateScopeNote();
    return;
  }
  // The outline builds' estimates, asked for side by side before the builds
  // queue one after another (each build is held to its own; addToScene).
  const outlined = changed.filter((r) => {
    const o = normalizeRender(r.render);
    return CURVE_METHODS.has(o.method) && o.pixel_size_um < curvePixelFor(r.summary);
  });
  if (outlined.length) {
    statusEl.textContent = `Estimating ${outlined.length} outline build(s)…`;
    await Promise.all(outlined.map((r) => estimates.get(r, normalizeRender(r.render))));
  }
  statusEl.textContent = `Rebuilding ${changed.length} cell(s)…`;
  const failed = [];
  for (const record of changed) {
    renderer.removeMesh(record.key);
    if (await addToScene(record, { frame: false }) === 'failed') failed.push(record);
  }
  renderNeuronListWhenIdle();
  updateScopeNote();
  persist();
  if (failed.length) {
    // addToScene said why on the banner over the render; do not say "rebuilt" over it.
    statusEl.textContent = `Rebuilt ${changed.length - failed.length} of ${changed.length} `
      + `cell(s); ${describeCells(failed)} failed — see the message over the render`;
    statusEl.className = 'status error';
    return;
  }
  statusEl.textContent = `Rebuilt ${changed.length} cell(s)`;
}

// -- scene -----------------------------------------------------------------

/**
 * Whether expert render mode is on (Settings): render settings are built
 * exactly as set, and a cell they cannot build is left failed with the
 * settings marked, instead of built with settings that work.
 */
function isExpert() {
  if (settings) return settings.expert;
  try { return expertRender(window.localStorage); } catch { return false; }
}

/**
 * How IPL depth is measured (Settings): `normal`, along the layers' normals (the
 * default), or `column`, straight along Z as MATLAB did.  One choice for the
 * whole page: the Stratification tab, its files, Color by IPL depth, Clip IPL
 * and a clipped export all measure the same way.
 */
function currentIplMethod() {
  if (settings) return settings.iplMethod;
  try { return iplMethod(window.localStorage); } catch { return iplMethod(null); }
}

/** The method's words, as the Settings tab names it. */
function iplMethodWords(method = currentIplMethod()) {
  return IPL_METHOD_CHOICES.find(([value]) => value === method)?.[1] ?? method;
}

/**
 * The IPL depth method was switched: what it measured is measured again, the
 * Stratification chart (now, or when its tab is next shown) and every cell
 * drawn with Color by IPL depth or Clip IPL.
 */
async function iplMethodChanged() {
  strataFor = null;
  if (state.view === 'strata') drawStratification();
  const depthDrawn = [...state.neurons.values()].filter((r) => (
    drawsIplDepth(normalizeRender(r.render)) || flatFor(r)));
  // The flattened sheets lie where the method puts them too.
  if (iplFlat) display?.flattenChanged();
  for (const record of depthDrawn) await rebuildOne(record);
  if (depthDrawn.length) {
    statusEl.textContent = `IPL depth ${iplMethodWords()}: redrew ${describeCells(depthDrawn)}`;
    statusEl.className = 'status';
  }
  if (iplFlat) updateFlatNote();
}

/**
 * Flatten IPL turned on or off (the Display panel, or `why` when the page turned it
 * off itself: GraphApp draws in section coordinates).  Every cell is drawn again,
 * flattened where its volume has IPL boundary markers, the context sheets with
 * them, and the view framed again unless Held: the cells' Z moves by tens of µm.
 * Turning it on lets go of the selection and the markers, drawn in section
 * coordinates; it waits while annotations are being moved.
 */
async function setIplFlat(on, { why = '' } = {}) {
  on = !!on;
  if (on && moves?.active) {
    display?.syncFlatten(iplFlat);
    flareStatus('Leave move mode (Esc) before flattening the IPL');
    return;
  }
  if (on === iplFlat) { display?.syncFlatten(iplFlat); return; }
  iplFlat = on;
  // What the readout named was in the other frame.
  hovered = null;
  lastPick = null;
  showPickShift(null);
  $('pick-text').textContent = on ? 'Click a flattened cell: its depth from the INL shows here'
    : 'Point at a cell: the nearest annotation shows here';
  $('pick-viking').textContent = 'Viking —';
  $('pick-location').textContent = 'Location ID —';
  display?.syncFlatten(iplFlat);
  if (on) renderer.clearSelection();
  renderer.clearPivot('flatten');
  display?.flattenChanged();
  const records = [...state.neurons.values()];
  statusEl.textContent = `${on ? 'Flattening' : 'Unflattening'} the IPL: drawing ${describeCells(records)}…`;
  statusEl.className = 'status busy';
  for (const record of records) await rebuildOne(record);
  if (records.length && !display?.holdLimits) renderer.resetView();
  await updateFlatNote();
  const skipped = records.filter((r) => on && !volumeHasBoundary(r.source));
  // Flared, so the busy line's "ready" does not take it straight away.
  flareStatus((why || (on ? 'IPL flattened: Z is depth from the INL, scaled 1:1 (Display panel)'
    : 'IPL no longer flattened: cells drawn in their volumes\' own Z'))
    + (skipped.length ? `; ${describeCells(skipped)} not flattened (no IPL boundary markers)` : ''));
}

/** A volume's IPL thickness (`/ipl-thickness`), asked once per volume. */
function askIplThickness(source) {
  if (!iplThickness.has(source)) {
    iplThickness.set(source, api.iplThickness(source).catch((error) => {
      iplThickness.delete(source);
      throw error;
    }));
  }
  return iplThickness.get(source);
}

/** source -> the µm its cells are flattened by, as the server reported it. */
const flatThicknessOf = new Map();

/**
 * The flattened view's lines, as the Display panel's note and a snapshot's caption
 * carry them: what Z is, and each flattened volume's thickness.  [] when the view
 * is not flattened.
 */
function flatLines({ short = false } = {}) {
  if (!iplFlat) return [];
  const sources = [...new Set([...state.neurons.values()].map((r) => r.source))];
  if (!sources.length) sources.push(state.source);
  const lines = [short
    ? 'Z = depth from the INL x the median IPL thickness along the normals, 1:1.'
    : `Flattened IPL: Z = depth from the INL (measured ${iplMethodWords()}) `
      + 'x the volume\'s median IPL thickness along the normals; X/Y:Z 1:1.'];
  for (const source of sources) {
    if (!volumeHasBoundary(source)) {
      lines.push(`${volumeLongName(source)}: not flattened (no IPL boundary markers).`);
      continue;
    }
    const t = flatThicknessOf.get(source);
    lines.push(t == null ? `${volumeLongName(source)}: …`
      : `${volumeLongName(source)}: INL at Z = 0, GCL at Z = ${t.toFixed(2)} µm.`);
  }
  return lines;
}

/** What the note under Flatten IPL adds to `flatLines`: what a flattened cell does not draw. */
const FLAT_HIDDEN = 'Rings, markers and arrows are not drawn while flattened (they sit in sections).';

/** The Display panel's note under Flatten IPL, once each shown volume's thickness is known. */
async function updateFlatNote() {
  const note = () => (iplFlat ? [...flatLines({ short: true }), FLAT_HIDDEN] : []);
  display?.setFlattenNote(note());
  if (!iplFlat) return;
  const sources = new Set([...state.neurons.values()].map((r) => r.source));
  if (!sources.size) sources.add(state.source);
  for (const source of sources) {
    if (!volumeHasBoundary(source) || flatThicknessOf.has(source)) continue;
    try {
      const t = await askIplThickness(source);
      if (t?.normal_um?.median != null) flatThicknessOf.set(source, t.normal_um.median);
    } catch { /* the note says … for it */ }
  }
  display?.setFlattenNote(note());
}

/**
 * Build (or fetch from this tab's cache) a cell's surface and put it, its
 * extras and its styling on screen.  Answers `'drawn'`, `'failed'` (the error
 * is on screen) or `'skipped'` (the cell went, was loaded again, or a newer
 * draw of it took over), so a caller that reports "rebuilt N cells" can count.
 * `extrasEarly` asks for the extras' data once the server has taken the build, beside
 * it, rather than once the surface is drawn (`fetchExtrasEarly`); they are drawn with
 * the surface either way.
 *
 * A build that fails because of its settings (renderfallback.failureOf) is built
 * again with the next settings that may draw the cell, and the cell keeps the
 * first that does (`fallBack`), unless expert render mode is on or the cell is
 * to be built as set once ("Build as I set it"): then it is left failed, and the
 * Render panel marks its settings.
 */
async function addToScene(record, {
  frame = true, progress = null, framing = null, extrasEarly = false,
} = {}) {
  // A batch that took its cells before one was unloaded (Apply to all, Copy to)
  // reaches that cell with nothing left to draw it into: its load generation is
  // 0 and so is the one it would compare with, so nothing below would stand down.
  if (!state.neurons.has(record.key)) return 'skipped';
  const verbatim = isExpert() || !!record.verbatimOnce;
  record.verbatimOnce = false;
  let options = normalizeRender(record.render);
  // The load this draw belongs to.  If the cell is loaded again meanwhile
  // (a Registration checkbox, Update from Viking), that load draws it, and
  // this draw stands down rather than land its old surface last.
  const gen = loadGeneration(record.key);
  const sameLoad = () => loadGeneration(record.key) === gen;
  // And the newest draw of it: Apply pressed again while this build is still
  // running starts another, which retires this one -- the older build used to
  // be able to land last.
  const drawn = draws.begin(record.key);
  const current = () => sameLoad() && draws.current(record.key, drawn);
  let early = null;
  let waiting = null;
  // Once the server has taken the build, which it runs in a process of its own:
  // asked beside the estimate or the build's start, the outlines (0.5 s of the
  // service's Python on c1178) held up a Hybrid's estimate from 0.17 to 1.05 s
  // and its start from 0.15 to 1.2 s (production, 2026-10-20).  Not by a draw
  // already retired: its requests would name the copy that replaced it.
  const askExtras = () => {
    if (extrasEarly && !early && current()) early = fetchExtrasEarly(record);
  };

  /**
   * The surface for `asked` (this tab's cache, else the server) as
   * `{ mesh, options, fetched }`, or null once this draw is retired.  Throws
   * what the build threw.  `options` are what was built: an outline raster too
   * fine for the cell is built at the finest size that fits it (the server's
   * estimate), unless the cell is built as set.
   */
  const obtain = async (asked) => {
    let opts = asked;
    // A surface cut or colored by IPL depth is the page's depth method's (never the cell's
    // option), and so is one drawn on the flattened IPL, the view's.
    const depthMethod = currentIplMethod();
    const flat = flatFor(record);
    let cacheKey = meshCacheKey(record, effectiveSignature(withIplMethod(opts, depthMethod, flat)));
    let mesh = meshCache.get(cacheKey);
    if (!mesh && !verbatim && CURVE_METHODS.has(opts.method)) {
      // Held to the server's estimate before anything is built: a Voxel size too
      // fine for this cell is built at the finest that fits it, and a notice says so.
      progress?.stage('estimating the build');
      if (await fitPixelFor(record, opts, current)) {
        if (!current()) return null;
        opts = normalizeRender(state.neurons.get(record.key).render);
        cacheKey = meshCacheKey(record, effectiveSignature(withIplMethod(opts, depthMethod, flat)));
        mesh = meshCache.get(cacheKey);
      }
      if (!current()) return null;
    }
    if (mesh) return { mesh, options: opts, fetched: false };
    waiting ??= showWaitingPoints(record, current, framing);
    progress?.stage('queued on the server');
    mesh = await api.mesh(record.source, record.id, withIplMethod(opts, depthMethod, flat), {
      alive: current,
      onSubmitted: askExtras,
      onStatus: progress ? (job) => progress.stage(stageText(job)) : undefined,
    });
    // The cell may have been unloaded while the server built this mesh --
    // easy to hit when a long build has the single worker queued up.
    // Adding the mesh now would strand a ghost in the scene with no row
    // left to remove it (seen 2026-08-31: a Temporal cell's mesh survived
    // its own ✕ and was mistaken for RC2's c1).  Or loaded again, and this
    // mesh is of the copy before.  Either way it is neither drawn nor kept.
    if (!current()) return null;
    meshCache.set(cacheKey, mesh);
    // The cell was loaded as other data since its earlier surfaces were built.
    dropOtherCopies(meshCache, record);
    return { mesh, options: opts, fetched: true };
  };

  try {
    let got = null;
    let failure = null;
    let thrown = null;
    try {
      got = await obtain(options);
      if (!got) return 'skipped';
      if (!got.mesh.n_faces) failure = failureOf({ empty: true });
    } catch (error) {
      if (error?.aborted) return 'skipped';
      failure = failureOf({ error });
      // Not the settings' (the network, a restart): said below, as it always was.
      if (!failure) throw error;
      thrown = error;
    }
    let exhausted = false;
    if (failure && !verbatim) {
      const failed = got?.options ?? options;
      const fell = await fallBack(record, failed, failure, obtain, current, progress);
      if (!fell) return 'skipped';
      if (fell.got) {
        got = fell.got;
        failure = null;
        thrown = null;
      } else {
        exhausted = true;
        options = failed;
      }
    }
    if (failure) {
      // What the Render panel marks while the controls hold these very settings.
      record.renderFailure = {
        ...failure, signature: effectiveSignature(got?.options ?? options), exhausted,
      };
      updateMethodControls();
    }
    if (thrown) {
      fail(thrown);
      showRenderError(record, options,
        thrown instanceof ApiError ? thrown.detail : String(thrown.message || thrown),
        { failure, exhausted });
      return 'failed';
    }
    options = got.options;
    const { mesh, fetched } = got;
    progress?.stage('drawing');
    if (!mesh.n_faces) {
      // Disc techniques draw nothing from annotations no link joins; Closed curve
      // smooths away anything narrower than about two voxels (renderfallback.js).
      statusEl.textContent =
        `c${record.id} produced no mesh with the "${options.method}" method`;
      statusEl.className = 'status error';
      const makeup = makeupText(record.summary);
      showRenderError(record, options, 'the build produced an empty mesh'
        + (makeup ? ` from ${makeup}` : '')
        + (options.method === 'closedcurve' && !exhausted
          ? ' (everything in it is one section thick or narrower than about two voxels)'
          : ''), { failure, exhausted });
    } else {
      clearRenderError(record);
      record.renderFailure = null;
    }
    // A Fine voxel that was typed and came out coarser (the whole-cell grid's
    // floor or one of its budgets): the mesh says which it used, and why.
    const fine = fetched ? fineVoxelNotice(`c${record.id}`, mesh, options) : null;
    if (fine) showRenderNotice(fine.headline, fine.detail);
    const d0 = normalizeDisplay(record.display);
    renderer.setMesh(record.key, mesh, {
      color: record.color, opacity: record.opacity, material: materialSpec(d0.material),
    });
    // The thickness a flattened surface was scaled by, for the readout, the files and the note.
    record.flatThickness = mesh.ipl_flatten_um ?? null;
    if (record.flatThickness != null) flatThicknessOf.set(record.source, record.flatThickness);
    // Whichever draw puts the surface on screen ends the waiting points, the
    // one that made them or not: a draw that hit this tab's cache has none of
    // its own, and the retired build before it left its points for this draw to
    // clear.
    renderer.removePoints(waitingKey(record));
    renderer.setSurface(record.key, d0.showSurface);
    renderer.setWireframe(record.key, d0.showWireframe);
    // removeAll forgets a cell's Z range; its display settings hold it.
    renderer.setZHide(record.key, zHideOf(record, d0));
    // setMesh builds the wire child at the cell color and full opacity;
    // restore the panel's own wireframe styling on every rebuild.
    renderer.setWireframeStyle(record.key, {
      color: d0.wireframeColor || record.color, opacity: d0.wireframeOpacity,
    });
    // The load's one early framing (showWaitingPoints) when this surface is the
    // first thing of it on screen: one from a cache comes inside the waiting
    // points' delay, so no cloud framed the view before it.  Until something
    // does, a fresh page's camera sits on its own target at the origin, where a
    // drag has nothing to turn about.
    if (framing && !framing.done) {
      framing.done = true;
      renderer.resetView();
      framing.camera = renderer.cameraState();
    }
    askExtras();  // a surface this tab had: asked now, side by side
    await refreshExtras(record, { early });
    // After refreshExtras, so the freshly built synapse and skeleton objects
    // inherit the neuron's visibility rather than defaulting to shown.
    renderer.setStyle(record.key, { visible: record.visible });
    // A surface landing in a view that shows nothing of any cell frames it, as
    // Reset view would: a fresh page's camera at the origin, a load into a list
    // that already held a row (only a first load framed), a cell whose first build
    // came back empty and was then built (Temporal c162392, 2026-10-21: built, in
    // the scene, out of sight until Reset view).  A view that shows anything stays
    // where it is, so does one Held (Display), and a restore decides its own.
    // A build that made no surface is framed the same way on what it left shown
    // (the cell's rings, nodes or skeleton, which Reset view's bounds hold), and
    // nothing moves when it left nothing: built empty, a cell loaded beside another
    // stayed off screen with only those drawn.
    if (record.visible && !restoring && !display?.holdLimits && renderer.seesAnything() === false
        && (mesh.n_faces || renderer.showsCell(record.key))) {
      renderer.resetView();
      if (framing) {
        framing.done = true;
        framing.camera = renderer.cameraState();
      }
    }
    // Directly, not via requestAnimationFrame: rAF never fires in a hidden
    // document, so a neuron loaded in a background tab would never be framed.
    // Suppressed when re-rendering an existing neuron or restoring a session,
    // either of which would otherwise throw away the user's camera.
    if (frame && renderer.meshes.size === 1 && !display?.holdLimits) renderer.resetView();
    persist();
    // GraphApp's drawing of the cell, once the draws settle (it reads this load's data).
    debounced('graph-sync', () => syncGraph().catch(() => {}), 800);
    return mesh.n_faces ? 'drawn' : 'failed';
  } catch (error) {
    // The user unloaded the cell mid-build, or a newer draw took over: their
    // own action, not an error.
    if (error?.aborted) return 'skipped';
    fail(error);
    showRenderError(record, options, error instanceof ApiError ? error.detail : String(error.message || error));
    return 'failed';
  } finally {
    if (draws.current(record.key, drawn)) {
      // The newest draw of the cell ends however it ends, and no later draw is
      // left to clear the points, so it clears them whether it made them or
      // not (a cache hit after a retired build makes none).
      waiting?.stop();
      renderer.removePoints(waitingKey(record));
    } else {
      // A newer draw of this cell is under way (or the cell is gone, and
      // forgetDrawn has cleared them): the points are that draw's cue now.
      waiting?.stop({ keepPoints: true });
    }
  }
}

/**
 * The renderer's key for a cell's waiting points.  One per cell, not per draw:
 * whichever draw of the cell is the newest owns them, so a draw that puts a
 * surface on screen, and `forgetDrawn`, clear them by this key.
 */
const waitingKey = (record) => `${record.key}|waiting`;

// How long a build may take before its cell's annotations stand in for it.
// A surface the server has cached comes back well inside this, so a cached
// cell never flashes points first.
const WAITING_POINTS_DELAY_MS = 600;

/**
 * While a cell's surface is being built, its annotations as points in the
 * cell's color, so the viewport shows where the cell is and roughly its
 * shape instead of nothing (decision 1 of the load-progress plan,
 * 2026-10-07).  Only for a cell with no surface on screen yet -- a rebuild
 * keeps showing the old surface -- and gone the moment the build ends,
 * however it ends.  The node list fetched here is the one picking uses, so
 * the request is not wasted.
 */
function showWaitingPoints(record, current, framing = null) {
  const key = waitingKey(record);
  let stopped = false;
  const timer = setTimeout(async () => {
    if (stopped || !record.visible || renderer.meshes.has(record.key)) return;
    try {
      const nodes = await nodesFor(record);
      if (stopped || !current() || renderer.meshes.has(record.key)) return;
      const rgb = hexToUnitRgb(record.color);
      const xyz = [];
      const colors = [];
      for (const n of nodes) {
        if (!n.xyz_um) continue;
        xyz.push(n.xyz_um[0], n.xyz_um[1], n.xyz_um[2]);
        colors.push(rgb[0], rgb[1], rgb[2]);
      }
      renderer.setPoints(key, xyz, colors, { size: 2 });
      // A first load frames only at its end; its first cloud is framed now,
      // with whatever has landed, so it is on screen.  Once per load, so the
      // camera does not swing per cell.
      if (framing && !framing.done) {
        framing.done = true;
        renderer.framePoints(key);
        framing.camera = renderer.cameraState();
      }
    } catch (error) {
      console.warn(`no waiting points for c${record.id}:`, error);
    }
  }, WAITING_POINTS_DELAY_MS);
  return {
    stop({ keepPoints = false } = {}) {
      stopped = true;
      clearTimeout(timer);
      if (!keepPoints) renderer.removePoints(key);
    },
  };
}

/**
 * Is the camera still where a load put it (`framing.camera`, or the saved view
 * a restore put up)?  Yes too when the load put up nothing, so its end frames
 * the scene as it always has.  A view the person moved while the cells came in
 * is theirs, and the load's end leaves it there.
 */
function viewUntouched(reference) {
  return !reference || sameCamera(renderer.cameraState(), reference);
}

/** '#rrggbb' as three 0-1 numbers; mid gray for anything else. */
function hexToUnitRgb(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || ''));
  return m ? [1, 2, 3].map((i) => parseInt(m[i], 16) / 255) : [0.6, 0.6, 0.6];
}

const nodesCache = new Map();
// key -> the `/nodes` request of the cell's current load, which every asker shares
// (the waiting points, a pick, a draw's extras), answered or not: see askNodes.
const nodesAsked = new Map();
// key -> Location ID -> the IDs it is linked to (the cell's own annotations).
const edgesCache = new Map();
// key -> closed-curve outlines ([] for a cell traced only as discs).
const outlinesCache = new Map();
// key -> { params, wire }: the last annotation wireframe fetched, so a display
// change elsewhere on the cell does not fetch it again.
const annoWireCache = new Map();

/**
 * A cell's `name` extra ('nodes', 'outlines', 'skeleton') for the copy the tab holds now:
 * the one kept from an earlier load at the same state (a Registration switch back to a
 * setting already drawn, the Undo of an unload, an Update that found nothing changed),
 * else `send()`'s, kept when no load of the cell can have changed it (appcore `keptRequest`).
 */
function askKept(record, name, send) {
  return keptRequest(keptExtras, name, {
    live: () => state.neurons.get(record.key) ?? null,
    loads: () => loadsOf(record.source, record.id),
    send,
  });
}

/** A cell's closed-curve outlines, kept or asked for (`askKept`). */
function askOutlines(record) {
  return askKept(record, 'outlines', () => api.closedCurves(record.source, record.id));
}

/** A cell's skeleton, kept or asked for (`askKept`). */
function askSkeleton(record) {
  return askKept(record, 'skeleton', () => api.skeleton(record.source, record.id));
}

/** The annotation wireframe's request, from a cell's display settings. */
function annoWireParams(display) {
  return {
    gain: display.annoWireGain,
    fit: display.annoWireFit,
    size_tolerance: display.annoWireTolerance,
    break_ratio: display.annoWireBreak,
    lines: display.annoWireLines,
    ring_spacing_um: display.annoWireSpacing,
  };
}

/** Whether a cell has closed-curve annotations to draw as outlines. */
function hasClosedCurves(record) {
  const s = record.summary;
  if (!s) return true;  // unknown: ask, the endpoint answers [] cheaply
  return Boolean(s.has_geometry) || (s.n_curve_annotations || 0) > 0;
}

/** Reset view, as its button and the view cube's house do (an undo step of its own). */
function resetViewNow() {
  renderer.resetView();
  display.readCamera();
  pushHistoryNow();
}

/** Show or hide the view cube, in the 3D render and in Render compare; the 3D render's
 * overlay row and notices make room for it. */
function showViewCube(on) {
  viewCube?.setVisible(on);
  compare?.setViewCube(on);
  $('view-render').classList.toggle('with-viewcube', !!on);
}

/** Turn the view to a named viewpoint, as the toolbar's buttons do (an undo step of its own). */
function lookAt(name) {
  renderer.setViewpoint(name);
  display.readCamera();
  pushHistoryNow();
}

/** A cell's closed-curve outlines ([] for one traced only as discs), fetched once. */
async function outlinesFor(record) {
  let outlines = outlinesCache.get(record.key);
  if (!outlines) {
    outlines = hasClosedCurves(record) ? await askOutlines(record) : [];
    outlinesCache.set(record.key, outlines);
  }
  return outlines;
}

/**
 * Send the requests `refreshExtras` will make for `record`, now, while its surface
 * builds: the annotations, the outlines, the skeleton and the annotation wireframe
 * its display draws (`extrasWanted`) and the caches do not hold.  A manual move's
 * Apply used to ask for them one after another once the moved copy's surface was
 * drawn: 1.1 to 1.3 s more per Apply of Inferior c1178 on production (2026-10-20),
 * where a 4.6 to 7.3 s build hides all of them.  A Registration switch and Update
 * from Viking draw a new copy the same way.  Answers the pending requests, for
 * `refreshExtras`'s `early`.  The copy each request names is the one the tab holds
 * now (api.js `withCopy`), the copy this draw is of.
 */
function fetchExtrasEarly(record) {
  const display = normalizeDisplay(record.display);
  const wanted = extrasWanted(display, normalizeShiftDisplay(record.display),
    { hasCurves: hasClosedCurves(record) });
  const params = annoWireParams(display);
  const caches = extraCaches();
  return startExtras(wanted, {
    nodes: () => askNodes(record),
    outlines: () => askOutlines(record),
    skeleton: () => askSkeleton(record),
    wire: async () => ({
      sig: JSON.stringify(params), data: await api.annotationWire(record.source, record.id, params),
    }),
  }, (name) => caches[name].has(record.key));
}

/** The caches a cell's extras are drawn from, by the names `extrasWanted` gives them. */
function extraCaches() {
  return { nodes: nodesCache, outlines: outlinesCache, skeleton: skeletonCache, wire: annoWireCache };
}

/**
 * Draw a cell's extras (rings, Position shift layer, annotation wireframe, skeleton,
 * the manual moves' arrows, synapses) as its display asks, fetching what the caches
 * do not hold.  `early` is what `fetchExtrasEarly` asked for as the draw began: it is
 * waited for and put in the caches first, so everything below draws from them.
 */
async function refreshExtras(record, { early = null } = {}) {
  const display = normalizeDisplay(record.display);
  // Same races as the mesh in addToScene: every await below can resolve
  // after the cell's ✕ (drawing then would leave orphans) or after the cell
  // was loaded again (what came back is of the copy before).  Nothing fetched
  // then is drawn or kept.
  const gen = loadGeneration(record.key);
  const gone = () => loadGeneration(record.key) !== gen;
  if (early && !(await adoptExtras(early, extraCaches(), record.key, gone))) return;
  // On the flattened IPL, what is drawn in section coordinates is not drawn.
  const flat = flatFor(record);

  if (display.showDiscs && !flat) {
    try {
      // Cached: stroke changes rebuild the rings from the same annotations.
      let nodes = nodesCache.get(record.key);
      if (!nodes) {
        nodes = await askNodes(record);
        if (gone()) return;
        nodesCache.set(record.key, nodes);
      }
      // A closed curve is drawn as its traced outline, not as the circle
      // its stored radius would make.
      let outlines = outlinesCache.get(record.key);
      if (!outlines) {
        outlines = hasClosedCurves(record) ? await askOutlines(record) : [];
        if (gone()) return;
        outlinesCache.set(record.key, outlines);
      }
      if (gone()) return;
      renderer.setDiscs(record.key, nodes, {
        color: display.discColor || record.color,
        opacity: display.discOpacity,
        stroke: display.discStroke,
        depth: display.discDepth,
        through: display.discThrough !== false,
        outlines,
      });
    } catch (error) { fail(error); }
  } else {
    renderer.removeDiscs(record.key);
  }

  // The Position shift layer: drawn from the same cached annotations, so it costs
  // a request only the first time they are needed.
  if (flat) shifts?.remove(record);
  else await shifts?.refresh(record, { gone });
  if (gone()) return;

  if (display.showAnnoWire && !flat) {
    try {
      const params = annoWireParams(display);
      const sig = JSON.stringify(params);
      let wire = annoWireCache.get(record.key);
      if (wire?.sig !== sig) {
        const fetched = await api.annotationWire(record.source, record.id, params);
        if (gone()) return;
        wire = { sig, data: fetched };
        annoWireCache.set(record.key, wire);
      }
      if (gone()) return;
      renderer.setAnnoWire(record.key, wire.data, {
        color: display.annoWireColor || record.color,
        opacity: display.annoWireOpacity,
        through: display.annoWireThrough !== false,
      });
    } catch (error) { fail(error); }
  } else {
    renderer.removeAnnoWire(record.key);
  }

  if (display.showSkeleton) {
    try {
      // Cached: changing the skeleton's width rebuilds its geometry, and
      // re-fetching identical centerline data to do that would be waste.
      let skeleton;
      if (flat) {
        // On the flattened IPL: the server's flattening, kept by the depth method.
        const method = currentIplMethod();
        const kept = flatSkeletonCache.get(record.key);
        skeleton = kept?.method === method ? kept.data : null;
        if (!skeleton) {
          skeleton = await api.skeleton(record.source, record.id, flatParams(record.source));
          if (gone()) return;
          flatSkeletonCache.set(record.key, { method, data: skeleton });
        }
      } else {
        skeleton = skeletonCache.get(record.key);
        if (!skeleton) {
          skeleton = await askSkeleton(record);
          if (gone()) return;
          skeletonCache.set(record.key, skeleton);
        }
      }
      if (gone()) return;
      renderer.setSkeleton(record.key, skeleton, {
        color: display.skeletonColor || record.color,
        width: display.skeletonWidth,
        opacity: display.skeletonOpacity,
      });
    } catch (error) { fail(error); }
  } else {
    renderer.removeSkeleton(record.key);
  }
  // The cell's manual moves, an arrow each, the chosen rings ahead of theirs and the
  // skeleton bent to the moves not yet applied (so after the skeleton is drawn).
  moves?.update();

  if (display.showSynapses && record.synapseCounts.length) {
    try {
      // Each type is fetched at its own size and shape, so one type can be a
      // large sphere while another is a fine oval.
      const meshes = await Promise.all(synapseTypeEntries(record).map(async (entry) => {
        const t = typeSettings(display, entry.local_name);
        return {
          type: entry.local_name,
          mesh: t.show
            ? await api.synapseMesh(record.source, record.id, entry.local_name, {
              radius_um: t.size, shape: t.shape, flat: flatParams(record.source),
            })
            : null,
          // The Viking color for the type, unless overridden.
          color: t.color || SYNAPSE_COLORS[entry.local_name] || rgbCss(entry.color),
          opacity: t.opacity,
          visible: t.show,
        };
      }));
      if (gone()) return;
      renderer.setSynapses(record.key, meshes.filter((m) => m.mesh));
    } catch (error) { fail(error); }
  } else {
    renderer.removeSynapses(record.key);
  }
}

const rgbCss = (rgb) => `rgb(${rgb.map((v) => Math.round(v * 255)).join(',')})`;

// -- the pick readout --------------------------------------------------------
// The upper-left readout names the picked point in microns and in Viking's
// pixel/section space -- with the downsample the view corresponds to, so the
// four numbers paste straight into Viking's Commands → Goto Location -- and
// the Location ID of the nearest annotation, for Annotation → Goto Location
// ID.  A left click on either Viking field copies it; a right click opens it
// for typing or pasting, and Enter takes the view there.

/** An annotation farther than this from the picked point is not "the one". */
const NEAR_ANNOTATION_UM = 5;
/** Up to this many loaded cells have their annotations fetched for a typed point. */
const NEAR_FETCH_CELLS = 12;

let lastPick = null;

function pickScale(record) {
  const source = record?.source ?? state.source;
  return record?.volume?.volume_scale
    ?? state.volumes.find((v) => v.name === source)?.volume_scale ?? [1, 1, 1];
}

/** Micron point -> Viking pixel X, Y and section Z (the inverse of Transforms.scale). */
function vikingFromUm(point, scale) {
  return [point.x / (scale[0] / 1000), point.y / (scale[1] / 1000), point.z / (scale[2] / 1000)]
    .map((v) => Math.round(v));
}

function umFromViking(xyz, scale) {
  return {
    x: (xyz[0] * scale[0]) / 1000, y: (xyz[1] * scale[1]) / 1000, z: (xyz[2] * scale[2]) / 1000,
  };
}

/** Viking's downsample: image pixels per screen pixel, at the view's zoom there. */
function downsampleAt(point, scale, source) {
  // `point` is in the volume's frame; the zoom is read where it is drawn.
  return renderer.umPerPixelAt(renderer.toWorld(point, source)) / (scale[0] / 1000);
}

function formatDownsample(d) {
  if (!Number.isFinite(d) || d <= 0) return '1';
  if (d >= 10) return String(Math.round(d));
  return String(Math.round(d * 10) / 10);
}

function vikingText(pick) {
  // Space-separated. Viking's Goto Location paste parser (GoToLocationForm.cs)
  // keeps digit-group separators inside a number, and "," is that separator
  // in a US locale, so "1234, 5678, 90" reached its X box as "1234," and was
  // rejected -- Becca deleted the commas by hand (2026-09-17).
  return `${pick.viking.join(' ')} ${formatDownsample(pick.downsample)}`;
}

/**
 * Where Viking has an annotation that is drawn here at `at` (um): the section
 * correction (`shift_um`) and a manual move (`manual_shift_um`, X, Y and Z) taken
 * back, so the Viking numbers name Viking's own place and section.
 */
function vikingPoint(node, at) {
  const s = node?.shift_um;
  const h = node?.manual_shift_um;
  return {
    x: at.x - (s?.[0] ?? 0) - (h?.[0] ?? 0),
    y: at.y - (s?.[1] ?? 0) - (h?.[1] ?? 0),
    z: at.z - (h?.[2] ?? 0),
  };
}

/** Distance from a point to each annotation's disc; the nearest wins. */
function nearestNode(nodes, point) {
  let best = null;
  for (const n of nodes) {
    const [x, y, z] = n.xyz_um;
    const dxy = Math.max(Math.hypot(x - point.x, y - point.y) - n.radius_um, 0);
    const d = Math.hypot(dxy, z - point.z);
    if (!best || d < best.d) best = { node: n, d };
  }
  return best;
}

/**
 * The cell's annotations from the server, one request for each load of it
 * (`appcore.sharedRequest`): the waiting points asked for them again 0.6 s into a
 * Registration switch, while the answer to the switch's own request waited for the
 * surface to be drawn before it went into the cache.
 */
function askNodes(record) {
  return sharedRequest(nodesAsked, record.key, loadGeneration(record.key),
    () => askKept(record, 'nodes', () => api.nodes(record.source, record.id)));
}

async function nodesFor(record) {
  let nodes = nodesCache.get(record.key);
  if (!nodes) {
    // A fetch for a load of the cell that has since been replaced (a manual move
    // applied, a Registration switch) answers with the old copy's annotations:
    // they are not kept, and the current load's are fetched in their place.
    const gen = loadGeneration(record.key);
    nodes = await askNodes(record);
    if (loadGeneration(record.key) !== gen) {
      const now = state.neurons.get(record.key);
      return now ? nodesFor(now) : nodes;
    }
    nodesCache.set(record.key, nodes);
  }
  return nodes;
}

/** The downsample follows the view: recompute it for the point on show. */
function refreshDownsample() {
  if (!lastPick) return;
  lastPick.downsample = downsampleAt(lastPick.point, lastPick.scale, lastPick.source);
  $('pick-viking').textContent = `Viking ${vikingText(lastPick)}`;
}

/**
 * A readout field written as groups, a space apart: its text is the same, but
 * on a narrow view (the CSS's fold) it wraps between the groups before it
 * breaks one, so "µm 34.78, 186.23, 104.40" stays on one line.
 */
function writeGroups(el, ...groups) {
  el.replaceChildren(...groups.flatMap((text, i) => {
    const span = document.createElement('span');
    span.className = 'pick-group';
    span.textContent = text;
    return i ? [' ', span] : [span];
  }));
}

/** The Location ID field: the ID, then the volume it is numbered in. */
function writeLocationField(field, id, source) {
  writeGroups(field, `Location ID ${id}`, `(${volumeLongName(source)})`);
}

/**
 * How far the section correction or the current transforms moved the annotation
 * under the readout, beside its Location ID: "shift 0.14 µm (+0.12, -0.08)".
 * `node` is the annotation (or null to hide the field).
 */
function showPickShift(node, record) {
  const field = $('pick-shift');
  const sep = $('pick-shift-sep');
  if (!field || !sep) return;
  const layer = node && record ? shifts?.layerOf(record) : null;
  const text = node ? pickShiftText(node, layer && !layer.empty ? layer.pair : null) : null;
  field.hidden = !text;
  sep.hidden = !text;
  if (!text) return;
  field.textContent = text.text;
  field.title = text.title;
}

async function showPick({ point, key, copied }) {
  // The c key copies what the readout shows -- a typed location included --
  // not the renderer's last clicked point.
  if (copied && (lastPick || !point)) { copyPickField('viking'); return; }
  const record = key ? state.neurons.get(key) : null;
  if (record && flatFor(record)) {
    // A point on the flattened IPL has no place in Viking's sections: its Z is depth.
    lastPick = null;
    showPickShift(null);
    const t = record.flatThickness;
    writeGroups($('pick-text'), `${volumeLongName(record.source)} c${record.id}`,
      `µm ${point.x.toFixed(2)}, ${point.y.toFixed(2)}`,
      `depth ${point.z.toFixed(2)} µm from the INL${t != null ? ` (GCL at ${t.toFixed(2)})` : ''}`);
    $('pick-viking').textContent = 'Viking — (the IPL is flattened)';
    $('pick-location').textContent = 'Location ID —';
    if (key) selectNeuron(key);
    return;
  }
  const scale = pickScale(record);
  const source = record?.source || state.source;
  const pick = {
    point: { x: point.x, y: point.y, z: point.z }, key, scale, source,
    viking: vikingFromUm(point, scale), downsample: downsampleAt(point, scale, source),
    locationId: null,
  };
  lastPick = pick;
  showPickShift(null);
  // Structure and Location IDs are numbered per volume -- RC2's c74420 is a
  // ganglion cell, Inferior Monkey's is a child structure of c19698 -- so an
  // ID shown without its volume sent someone to look it up in the wrong one.
  const at = `µm ${point.x.toFixed(2)}, ${point.y.toFixed(2)}, ${point.z.toFixed(2)}`;
  if (record) writeGroups($('pick-text'), `${volumeLongName(record.source)} c${record.id}`, at);
  else $('pick-text').textContent = at;
  $('pick-viking').textContent = `Viking ${vikingText(pick)}`;
  $('pick-location').textContent = 'Location ID …';
  if (copied) copyPickField('viking');   // the c key
  if (key) selectNeuron(key);

  // The nearest annotation: this cell's, or for a typed point whichever
  // loaded cell of this volume comes closest (their annotations are fetched
  // once and cached; past a dozen cells only the cached ones are searched).
  let best = null;
  let owner = null;
  try {
    if (record) {
      best = nearestNode(await nodesFor(record), point);
      owner = record;
    } else {
      const candidates = [...state.neurons.values()].filter((r) => r.source === state.source);
      const fetchAll = candidates.length <= NEAR_FETCH_CELLS;
      const lists = await Promise.all(candidates.map(async (r) => (
        fetchAll ? nodesFor(r) : (nodesCache.get(r.key) ?? null)
      )));
      candidates.forEach((r, i) => {
        const b = lists[i] ? nearestNode(lists[i], point) : null;
        if (b && (!best || b.d < best.d)) { best = b; owner = r; }
      });
    }
  } catch (error) { fail(error); }
  if (lastPick !== pick) return;        // a newer pick took over meanwhile
  const field = $('pick-location');
  if (best && best.d < NEAR_ANNOTATION_UM) {
    pick.locationId = best.node.id;
    pick.locationSource = owner.source;
    showPickShift(best.node, owner);
    // Section corrections moved this annotation from where Viking stores it;
    // the Viking numbers name Viking's place, so they take the move back.
    if (best.node.shift_um || best.node.manual_shift_um) {
      pick.viking = vikingFromUm(vikingPoint(best.node, point), scale);
      $('pick-viking').textContent = `Viking ${vikingText(pick)}`;
    }
    writeLocationField(field, best.node.id, owner.source);
    field.title = `Annotation ${best.node.id} of c${owner.id} in ${volumeLongName(owner.source)}, `
      + `${best.d.toFixed(2)} µm from the picked point. `
      + 'Click: copy the ID for Viking → Annotation → Goto Location ID. '
      + 'Right-click: type or paste a Location ID, Enter takes the view there.';
  } else {
    field.textContent = 'Location ID —';
    field.title = 'No loaded annotation within 5 µm of this point. '
      + 'Right-click: type or paste a Location ID, Enter takes the view there.';
  }
}

/**
 * The readout follows the pointer (asked for 2026-10-22): over the 3D render or
 * GraphApp it names the annotation nearest the pointer (`Renderer3D.nearestAnnotation`,
 * hoverpick.js), its cell, Viking X Y Z and Location ID, and nothing is drawn to
 * show which -- selecting is for that.  It is written again only when that
 * annotation changes, so what a click wrote (a selection, a synapse) stays until
 * the pointer reaches another.  The pointer leaving the view leaves the readout as
 * it was, so its fields can be clicked to copy; `c` copies it with the pointer on
 * the view.  A cell's annotations are fetched the first time the pointer is over
 * the view with the cell shown, once for each load of it.
 */
let hovered = null;                 // { key, node } the readout names from the pointer
const hoverAsked = new Map();       // key -> the load of the cell a fetch was sent for

function hoverReadout(clientX, clientY) {
  const found = renderer.nearestAnnotation(clientX, clientY, hoverCells());
  if (!found || (hovered?.key === found.key && hovered.node === found.node)) return;
  const record = state.neurons.get(found.key);
  if (!record) return;
  hovered = { key: found.key, node: found.node };
  showNearestAnnotation(record, found.node);
}

/**
 * Every shown cell whose annotations the page has, as `nearestAnnotation` takes
 * them; a cell's are fetched the first time they are wanted, once for each load.
 */
function hoverCells() {
  const cells = [];
  for (const record of state.neurons.values()) {
    // A flattened cell is drawn on IPL depth, its annotations in sections: none is nearest.
    if (!record.visible || flatFor(record)) continue;
    const nodes = nodesCache.get(record.key);
    if (nodes) {
      cells.push({ key: record.key, nodes });
      continue;
    }
    const gen = loadGeneration(record.key);
    if (hoverAsked.get(record.key) === gen) continue;
    hoverAsked.set(record.key, gen);
    // A fetch that fails is not sent again for this load: the pointer moves every frame.
    nodesFor(record).then(() => {
      hoverAsked.delete(record.key);
      renderer.hoverAgain?.();
    }).catch(() => {});
  }
  return cells;
}

/**
 * Put the point every turn pivots about where the pointer is (P, an armed click,
 * the right-click menu): the middle of the annotation the readout names there, so
 * a process turns about its own axis, or the point drawn there (pivot.js
 * `pivotPlace`).  Nothing there: the status line says so, and a click is still
 * waited for if one was.
 */
function pivotAt(clientX, clientY) {
  const hit = renderer.pickAt(clientX, clientY);
  const found = renderer.nearestAnnotation(clientX, clientY, hoverCells());
  const record = found ? state.neurons.get(found.key) : null;
  const nearest = record ? {
    point: renderer.toWorld(found.node.xyz_um, record.source), key: found.key, px: found.px, hit: found.hit,
  } : null;
  const pointed = hit ? { point: hit.world.toArray(), key: hit.key } : null;
  const at = pointed?.point ?? nearest?.point;
  const pictureUm = at ? renderer.umPerPixelAt(at) * ($('viewport').clientHeight || 1) : Infinity;
  const place = pivotPlace({ pointed, nearest, pictureUm });
  if (!place) {
    flareStatus(renderer.pivotArmed ? 'Click on a cell: turns will pivot about that point (P or Esc to stop)'
      : 'Point at a cell and press P: turns will pivot about that point');
    return;
  }
  renderer.setPivot(place.point, { key: place.key });
}

/** The Pivot box, the label under the view cube and the status line follow the pivot. */
function pivotChanged({ on, armed, reason }) {
  const box = $('pivot-toggle');
  box.checked = on || armed;
  box.indeterminate = armed;
  $('pivot-chip').hidden = !on;
  if (!on) hideHelp();
  if (armed) flareStatus('Click the point to turn about (P or Esc to stop)');
  else if (on) flareStatus('Rotating about the ringed point — P, Esc or the label turns it off');
  else if (PIVOT_ENDED[reason]) flareStatus(`Rotating about the middle of the view again: ${PIVOT_ENDED[reason]}`);
  else flareStatus('Rotating about the middle of the view again');
}

/** Write an annotation into the readout as the one nearest the pointer. */
function showNearestAnnotation(record, node) {
  const [x, y, z] = node.xyz_um;
  const point = { x, y, z };
  const scale = pickScale(record);
  // The Viking numbers are Viking's place: the annotation's correction and manual
  // move taken back, as for a selected annotation.
  const pick = {
    point, key: record.key, scale, source: record.source,
    viking: vikingFromUm(vikingPoint(node, point), scale), downsample: downsampleAt(point, scale, record.source),
    locationId: node.id, locationSource: record.source,
  };
  lastPick = pick;
  writeGroups($('pick-text'), `${volumeLongName(record.source)} c${record.id}`,
    `µm ${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)}`);
  $('pick-viking').textContent = `Viking ${vikingText(pick)}`;
  const field = $('pick-location');
  writeLocationField(field, node.id, record.source);
  field.title = `The annotation nearest the pointer, ${node.id} of c${record.id} in `
    + `${volumeLongName(record.source)}, section ${node.z_section}. `
    + 'Click: copy the ID for Viking → Annotation → Goto Location ID. '
    + 'Right-click: type or paste a Location ID, Enter takes the view there.';
  showPickShift(node, record);
}

/** Put a Viking field on the clipboard and say so. */
function copyPickField(kind) {
  if (kind === 'viking') refreshDownsample();
  const text = kind === 'viking'
    ? (lastPick ? vikingText(lastPick) : '')
    : (lastPick?.locationId ? String(lastPick.locationId) : '');
  if (!text) {
    statusEl.textContent = kind === 'viking'
      ? 'Point at a cell first' : 'No annotation near the pointer';
    statusEl.className = 'status error';
    return;
  }
  // Clipboard access is denied in some contexts (insecure origin, no user
  // gesture); the text stays on screen either way.
  try { navigator.clipboard?.writeText(text).catch(() => {}); } catch { /* shown anyway */ }
  const button = $(kind === 'viking' ? 'pick-viking' : 'pick-location');
  button.classList.add('copied');
  setTimeout(() => button.classList.remove('copied'), 1200);
  // Name the volume: the number means nothing in any other.
  const where = volumeLongName((kind !== 'viking' && lastPick?.locationSource)
    || state.neurons.get(lastPick?.key)?.source || state.source);
  statusEl.textContent = `Copied “${text}” — in Viking, open ${where} and paste ${kind === 'viking'
    ? 'via Commands → Goto Location' : 'via Annotation → Goto Location ID'}`;
  statusEl.className = 'status';
}

function openPickInput(kind) {
  const input = $('pick-input');
  const button = $(kind === 'viking' ? 'pick-viking' : 'pick-location');
  input.dataset.kind = kind;
  // What is typed is a place in the volume selected now (gotoViking and
  // gotoLocationId both read it that way), so only a pick made in that volume is
  // offered for editing: another volume's numbers would be sent to the wrong place.
  const here = lastPick && (lastPick.locationSource ?? lastPick.source) === state.source
    ? lastPick : null;
  input.value = kind === 'viking'
    ? (here ? vikingText(here) : '')
    : (here?.locationId ? String(here.locationId) : '');
  input.placeholder = kind === 'viking' ? 'X Y Z [downsample]' : 'Location ID';
  button.hidden = true;
  input.hidden = false;
  input.focus();
  input.select();
}

function closePickInput() {
  const input = $('pick-input');
  const kind = input.dataset.kind;
  input.hidden = true;
  if (kind) $(kind === 'viking' ? 'pick-viking' : 'pick-location').hidden = false;
}

/** Take the view to a Viking location typed as "X Y Z" or "X Y Z downsample" (commas are fine too). */
async function gotoViking(text) {
  const parts = text.split(/[\s,;]+/).filter(Boolean).map(Number);
  if (parts.length < 3 || parts.slice(0, 3).some((v) => !Number.isFinite(v))) {
    throw new Error('Enter a Viking location as X Y Z or X Y Z downsample');
  }
  // The numbers are Viking's for the volume selected now, which is also where the
  // view flies and where the readout puts them; the last pick's cell may be in another.
  const scale = pickScale(null);
  const point = umFromViking(parts, scale);
  const down = parts.length > 3 && Number.isFinite(parts[3]) && parts[3] > 0 ? parts[3] : null;
  const umpp = down ? (down * scale[0]) / 1000 : null;
  const distance = umpp
    ? renderer.distanceForUmPerPixel(umpp)
    : renderer.camera.position.distanceTo(renderer.controls.target);
  await renderer.flyTo(renderer.toWorld(point, state.source), { distance });
  if (umpp) renderer.setOrthoUmPerPixel(umpp);
  persist();
  await showPick({ point, key: null });
  statusEl.textContent = `View moved to Viking ${parts.slice(0, 3).map(Math.round).join(' ')}`
    + (down ? ` at downsample ${formatDownsample(down)}` : '');
  statusEl.className = 'status';
}

/**
 * Take the view to a Viking Location ID: a loaded cell's annotation, or Viking's answer.
 * One of a loaded cell is selected as well, drawn yellow as a triple click on its ring
 * draws it (2026-10-20: Viking's "Show in SBFSEM-tools" and a typed Location ID only
 * moved the view, and the annotation was not marked); one of a cell not loaded is
 * selected once its cell is loaded from the status line's button.
 */
async function gotoLocationId(text) {
  const id = Number.parseInt(String(text).trim(), 10);
  if (!Number.isFinite(id) || id <= 0) throw new Error('Enter a Viking Location ID (a positive integer)');
  const source = state.source;
  let hit = null;
  // Only this volume's cells: Location IDs repeat across volumes, and a
  // same-numbered annotation in another one is a different place entirely.
  for (const r of state.neurons.values()) {
    if (r.source !== source) continue;
    const node = nodesCache.get(r.key)?.find((n) => n.id === id);
    if (node) { hit = { record: r, xyz: node.xyz_um, cell: r.id, loaded: true }; break; }
  }
  if (!hit) {
    const found = await api.locate(source, id);
    const record = state.neurons.get(keyOf(source, found.structure_id)) ?? null;
    hit = { record, xyz: found.xyz_um, cell: found.structure_id, loaded: !!record };
  }
  const distance = renderer.camera.position.distanceTo(renderer.controls.target);
  await renderer.flyTo(renderer.toWorld(hit.xyz, source), { distance });
  persist();
  // Selected, the readout names it as it does any selection (showAnnotationReadout).
  if (hit.record && await selectLocation(hit.record, id)) return;
  const point = { x: hit.xyz[0], y: hit.xyz[1], z: hit.xyz[2] };
  await showPick({ point, key: hit.record?.key ?? null });
  if (lastPick) {
    lastPick.locationId = id;
    lastPick.locationSource = source;
    writeLocationField($('pick-location'), id, source);
  }
  statusEl.replaceChildren(document.createTextNode(
    `Location ${id} is on c${hit.cell}${hit.loaded ? '' : ' (not loaded) '}`));
  if (!hit.loaded) {
    const load = document.createElement('button');
    load.type = 'button';
    load.className = 'undo-btn';
    load.textContent = `Load c${hit.cell}`;
    load.addEventListener('click', async () => {
      await loadMany([hit.cell]);
      const record = state.neurons.get(keyOf(source, hit.cell));
      if (record) await selectLocation(record, id);
    });
    statusEl.append(load);
  }
  statusEl.className = 'status';
}

/**
 * Select annotation `locationId` of the loaded cell `record`: drawn yellow, the arrow keys'
 * reference, named by the readout.  False when the cell has no such annotation (or went).
 */
async function selectLocation(record, locationId) {
  const node = (await nodesFor(record)).find((n) => n.id === locationId);
  if (!node || state.neurons.get(record.key) !== record) return false;
  renderer.select(annotationCandidate(record, node));
  activeMarker = renderer.selection;
  return true;
}

// -- Open in Viking ----------------------------------------------------------
// The reverse of Viking's "view in SBFSEM tools".  The browser goes to the
// identity server's launch page (by way of our /open-in-viking, which speaks
// its volume names), which hands viking://open to the operating system.  It
// goes in a tab of its own, reused from one launch to the next: a browser
// with no session there is shown their sign-in page, and that must not
// replace the render the user is looking at.  Once the link has gone to
// Viking the tab closes itself (viking-launch.html), and this one is in front.
//
// Two programs can take the link: the signed Viking (viking://) and the unsigned
// "Viking Test" that installs beside it (viking-test://).  A page cannot ask which
// are installed; the launch tab sees which one takes the focus and keeps that
// (vikingclient.js).  A machine with only one is sent there without a question
// (the signed link is tried first and the test build behind it); with both known,
// the first click of a session asks, and the answer holds for the session.

const VIKING_LAUNCH_TAB = 'sbfsem-open-in-viking';

/** Where the pointer last went down: where the question about the client appears
 * when no menu is open (the skeleton check's rows have none). */
let lastPointerAt = { x: Math.round(window.innerWidth / 2), y: Math.round(window.innerHeight / 3) };
window.addEventListener('pointerdown', (event) => {
  lastPointerAt = { x: event.clientX, y: event.clientY };
}, true);

/** localStorage and sessionStorage, or null where the browser refuses them. */
function browserStore(kind) {
  try { return window[kind] ?? null; } catch { return null; }
}

/**
 * Is this browser running on Windows?  Viking is a Windows program, so on
 * anything else the viking:// hand-off has nothing to hand to and fails in
 * silence -- there is no error to catch, which is why this is asked before the
 * launch rather than after it.  `userAgentData.platform` is exact where it
 * exists (Chromium); elsewhere the user-agent string is all there is, and
 * Android ("Linux; Android") must not read as Windows Phone's "Windows".
 */
function onWindows() {
  const data = navigator.userAgentData;
  if (data && typeof data.platform === 'string' && data.platform) {
    return /^windows$/i.test(data.platform);
  }
  const text = `${navigator.platform || ''} ${navigator.userAgent || ''}`;
  return /Win(dows NT|dows|32|64|CE)/i.test(text) && !/Windows Phone/i.test(text);
}

/** What this browser is running on, for the tooltip on the refusal. */
function platformName() {
  const text = `${navigator.platform || ''} ${navigator.userAgent || ''}`;
  if (/Mac|Darwin/i.test(text)) return 'macOS';
  if (/Android/i.test(text)) return 'Android';
  if (/iPhone|iPad|iPod/i.test(text)) return 'iOS';
  if (/Linux|X11/i.test(text)) return 'Linux';
  return navigator.userAgentData?.platform || navigator.platform || 'this platform';
}

/** The place to open for a cell: the annotation at `worldPoint`, else the point, else the soma. */
async function vikingPlaceFor(record, worldPoint) {
  const nodes = await nodesFor(record);
  if (!worldPoint) {
    // From the cell list there is no point; the largest annotation is the soma.
    const soma = nodes.reduce((a, n) => (!a || n.radius_um > a.radius_um ? n : a), null);
    return soma ? { location: String(soma.id), text: `Location ${soma.id}` } : null;
  }
  const [x, y, z] = renderer.toVolume(worldPoint, record.source);
  const point = { x, y, z };
  const best = nearestNode(nodes, point);
  if (best && best.d < NEAR_ANNOTATION_UM) {
    return { location: String(best.node.id), text: `Location ${best.node.id}` };
  }
  const scale = pickScale(record);
  const xyz = vikingFromUm(point, scale);
  const down = formatDownsample(downsampleAt(point, scale, record.source));
  return { location: [...xyz, down].join(','), text: `${xyz.join(' ')}` };
}

/** Viking at one annotation of the cell's volume (the skeleton check's rows, the right-click menu's list). */
function openLocationInViking(record, locationId, options = {}) {
  return openInViking(record, null,
    { location: String(locationId), text: `Location ${locationId}` }, options);
}

/**
 * Open the place in Viking: in the program the launches say is installed, or
 * the one the person picked this session.  `forceAsk` is the person asking to
 * choose ("Choose Viking client…").
 */
async function openInViking(record, worldPoint, known = null, { forceAsk = false } = {}) {
  // Nothing on a Mac or on Linux: no tab, no launch page, no silent failure.
  // The launch page repeats this check for anyone who opens its URL directly.
  if (!onWindows()) {
    // The status line's tooltip is its own text (a MutationObserver keeps them
    // equal), so the platform this was decided on goes to the console instead.
    console.info(`Open in Viking: ${platformName()} is not Windows; nothing was opened`);
    flareStatus('Viking client can only be run on Windows.');
    return;
  }
  const plan = planFromStores({
    local: browserStore('localStorage'), session: browserStore('sessionStorage'), forceAsk,
  });
  if (plan.ask) {
    // The tab is opened by the click on the answer, which is the gesture that allows it.
    askVikingClient((picked) => launchInViking(record, worldPoint, known, picked));
    return;
  }
  return launchInViking(record, worldPoint, known, plan);
}

/**
 * The question, put once per session when both programs are known and on request:
 * a small menu where the click was.  The answer is kept for the session, so the
 * next click opens there without asking.
 */
function askVikingClient(launch) {
  hideContextMenu();
  const menu = document.createElement('div');
  menu.id = 'ctx-menu';
  menu.className = 'ctx-menu';
  const title = document.createElement('div');
  title.className = 'ctx-title';
  title.textContent = 'Open in which Viking?';
  menu.append(title);
  const memory = readMemory(browserStore('localStorage'));
  for (const id of CLIENT_IDS) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'ctx-item';
    el.textContent = CLIENTS[id].name;
    el.title = id === 'test'
      ? 'The unsigned test build that installs beside Viking (its link is viking-test://)'
      : 'The signed client (its link is viking://)';
    el.addEventListener('click', () => {
      hideContextMenu();
      writeChoice(browserStore('sessionStorage'), id);
      launch(planForPick(memory, id));
    });
    menu.append(el);
  }
  const note = document.createElement('div');
  note.className = 'ctx-note';
  note.textContent = 'Asked once per session.';
  menu.append(note);
  placeContextMenu(menu, lastPointerAt.x, lastPointerAt.y);
}

async function launchInViking(record, worldPoint, known, { first, fallback }) {
  // Opened before anything is awaited: a tab opened after a fetch has lost
  // the click that allows it, and the popup blocker eats it.
  const tab = window.open('', VIKING_LAUNCH_TAB);
  try {
    const place = known || await vikingPlaceFor(record, worldPoint);
    const url = api.openInVikingUrl(record.source, place?.location, { client: first, then: fallback });
    const where = `${volumeLongName(record.source)}${place ? ` at ${place.text}` : ''}`;
    const program = clientName(first);
    if (tab) {
      tab.location.href = url;
      statusEl.textContent = `Opening ${where} in ${program} — the browser may ask before it starts ${program}`;
    } else {
      const link = document.createElement('a');
      link.href = url;
      link.target = VIKING_LAUNCH_TAB;
      link.textContent = `Open ${where} in ${program}`;
      statusEl.replaceChildren(document.createTextNode('The browser blocked the new tab: '), link);
    }
    statusEl.className = 'status';
  } catch (error) {
    tab?.close();
    fail(error);
  }
}

function initPickReadout() {
  const viking = $('pick-viking');
  const location = $('pick-location');
  const input = $('pick-input');
  if (!viking || !location || !input) return;
  viking.addEventListener('click', () => copyPickField('viking'));
  location.addEventListener('click', () => copyPickField('location'));
  viking.addEventListener('contextmenu', (e) => { e.preventDefault(); openPickInput('viking'); });
  location.addEventListener('contextmenu', (e) => { e.preventDefault(); openPickInput('location'); });
  input.addEventListener('keydown', async (e) => {
    e.stopPropagation();                // the viewport's z/Z/m keys stay out
    if (e.key === 'Escape') { closePickInput(); return; }
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const kind = input.dataset.kind;
    const text = input.value;
    closePickInput();
    try {
      if (kind === 'viking') await gotoViking(text);
      else await gotoLocationId(text);
    } catch (error) { fail(error); }
  });
  input.addEventListener('blur', () => setTimeout(closePickInput, 0));
  // Zooming changes the downsample the readout should paste; refresh it once
  // per frame while the camera moves.
  let pending = false;
  renderer.controls.addEventListener('change', () => {
    if (pending || !lastPick) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; refreshDownsample(); });
  });
  initHintToggle();
}

/**
 * The "?" the navigation hint folds into on a narrow viewport.  The CSS
 * decides when it shows (a container query on the overlay); this only opens
 * and closes the hint.
 */
function initHintToggle() {
  const toggle = $('hint-toggle');
  if (!toggle) return;
  toggle.addEventListener('click', (e) => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    // The keys the hint lists are the canvas's: a mouse click on the "?" must
    // not leave them dead until the next click on the render.  A keyboard
    // activation (detail 0) keeps its place in the tab order.
    if (e.detail > 0) renderer.webgl.domElement.focus();
  });
}

// -- sidebar ---------------------------------------------------------------
// -- loaded list -----------------------------------------------------------
// Rows are rebuilt only when the list's *structure* changes -- which cells
// exist, which are expanded, which sections a panel shows. Never on a slider
// move: rebuilding replaces the <input> the pointer is holding, which is what
// made the sliders stop after the first step.

/** Which rows are expanded. Pure UI, so it lives outside `state`. */
const expandedCells = new Set();

/** Last selection box clicked, for shift-click ranges. */
let selectionAnchor = null;

/**
 * Which sections inside a cell are open, keyed `cellKey:section`.
 *
 * Separate from `expandedCells` so opening a cell restores the sections you
 * left open in it rather than resetting to a wall of controls.
 */
const openSections = new Set();

/**
 * Every section starts shut (a closed one still says what is inside it), and
 * one the user opened stays open.  There was a default-open list naming an
 * 'appearance' section that the panel has not had since it was split into
 * Surface and Wireframe, so it opened nothing.
 */
function sectionOpen(key, id) {
  return openSections.has(`${key}:${id}`);
}

function toggleSection(key, id) {
  const tag = `${key}:${id}`;
  if (!openSections.delete(tag)) openSections.add(tag);
}

/**
 * A collapsible section inside a cell panel.
 *
 * `summary` is shown next to the heading so a shut section still says what is
 * inside it -- otherwise collapsing turns the panel into a row of unlabeled
 * triangles.
 */
/** The cell panel's sections GraphApp has a use for; the rest are the 3D render's. */
const GRAPH_SECTIONS = new Set(['skeleton-check', 'registration']);

function section(parent, record, id, title, summary, build) {
  if (graphOn && !GRAPH_SECTIONS.has(id)) return null;
  const open = sectionOpen(record.key, id);
  const wrap = document.createElement('div');
  wrap.className = `cell-section${open ? ' open' : ''}`;

  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'section-head';
  head.setAttribute('aria-expanded', String(open));

  const chev = document.createElement('span');
  chev.className = 'sec-chev';
  chev.textContent = open ? '▼︎' : '▶︎';

  const name = document.createElement('span');
  name.className = 'sec-name';
  name.textContent = title;

  const note = document.createElement('span');
  note.className = 'sec-summary';
  note.textContent = summary;

  head.append(chev, name, note);
  head.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleSection(record.key, id);
    renderNeuronList();
  });
  wrap.append(head);

  if (open) {
    const body = document.createElement('div');
    body.className = 'section-body';
    build(body);
    wrap.append(body);
  }
  parent.append(wrap);
  return wrap;
}

/** Open eye / struck-through eye, at 12px. */
function eyeIcon(open) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '13');
  svg.setAttribute('height', '13');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  const outline = document.createElementNS(ns, 'path');
  outline.setAttribute('d', 'M1.6 12S5.2 5 12 5s10.4 7 10.4 7-3.6 7-10.4 7S1.6 12 1.6 12z');
  svg.append(outline);
  const pupil = document.createElementNS(ns, 'circle');
  pupil.setAttribute('cx', '12'); pupil.setAttribute('cy', '12');
  pupil.setAttribute('r', '3');
  svg.append(pupil);
  if (!open) {
    const slash = document.createElementNS(ns, 'line');
    slash.setAttribute('x1', '3'); slash.setAttribute('y1', '21');
    slash.setAttribute('x2', '21'); slash.setAttribute('y2', '3');
    svg.append(slash);
  }
  return svg;
}

/**
 * Unloading is undoable for a short while.
 *
 * The alternative to making a destructive control safe is spacing it away from
 * the controls next to it, which only lowers the odds. Reloading is cheap --
 * the annotations are cached -- but the cell's color and per-type synapse
 * settings are not recoverable any other way, so they are what this keeps.
 */
const UNDO_MS = 12000;
let undoTimer = null;

function offerUndo(snapshot) {
  clearTimeout(undoTimer);
  statusEl.replaceChildren(
    document.createTextNode(`Unloaded c${snapshot.id} `),
  );
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'undo-btn';
  button.textContent = 'Undo';
  button.addEventListener('click', async () => {
    clearTimeout(undoTimer);
    await restoreUnloaded(snapshot);
  });
  statusEl.append(button);
  statusEl.className = 'status';
  undoTimer = setTimeout(() => {
    if (statusEl.contains(button)) {
      statusEl.textContent = 'ready';
      statusEl.className = 'status';
    }
  }, UNDO_MS);
}

async function restoreUnloaded(snap) {
  statusEl.textContent = `Restoring c${snap.id}…`;
  statusEl.className = 'status';
  try {
    const detail = await api.load({
      id: snap.id, source: snap.source, include_synapses: true,
      section_corrections: snap.corrections, live_registration: snap.registration,
      manual_moves: movesBody(snap.manualMoves),
    });
    const record = upsertNeuron(detail, { render: snap.render, display: snap.display });
    record.corrections = snap.corrections;
    record.correctionMode = snap.correctionMode;
    record.registration = snap.registration;
    record.manualMoves = snap.manualMoves ?? null;
    record.color = snap.color;
    record.opacity = snap.opacity;
    record.visible = snap.visible;
    setSelected(record.key, snap.wasSelected);
    await addToScene(record, { frame: false });
    renderNeuronListWhenIdle();
    persist();
    statusEl.textContent = `Restored c${snap.id}`;
  } catch (error) { fail(error); }
}

function renderNeuronList() {
  const list = $('neuron-list');
  list.replaceChildren();
  $('loaded-count').textContent = state.neurons.size;

  for (const record of state.neurons.values()) {
    list.append(neuronRow(record));
  }
  updateSelectionUi();
}

/**
 * Whether the keyboard focus is on a control in the list that a rebuild would
 * pull out from under the user: a dropdown (open, or just chosen from), a slider
 * or a box being typed in.  A checkbox or a button is not one: pressing it is
 * what asked for the rebuild.
 */
function editingInList() {
  const el = document.activeElement;
  if (!el || !$('neuron-list').contains(el)) return false;
  if (el instanceof HTMLSelectElement) return true;
  return el instanceof HTMLInputElement
    && !['checkbox', 'radio', 'button', 'submit'].includes(el.type);
}

let listRetry = 0;
/**
 * `renderNeuronList` for the changes nobody at the list asked for -- a load
 * finishing, Viking's freshness answer, a cell arriving from another path.
 * Replacing the list replaces the cell panel's dropdowns, sliders and boxes:
 * an open dropdown vanished under the pointer (native or the in-page list), a
 * slider drag was dropped.  So while one of them has the focus the rebuild waits
 * and tries again shortly; a rebuild a user's own change asked for calls
 * `renderNeuronList` directly and is never held back.
 */
function renderNeuronListWhenIdle() {
  clearTimeout(listRetry);
  if (editingInList()) {
    listRetry = setTimeout(renderNeuronListWhenIdle, 400);
    return;
  }
  renderNeuronList();
}

function neuronRow(record) {
  const li = document.createElement('li');
  const expanded = expandedCells.has(record.key);
  li.dataset.key = record.key;
  li.className = 'neuron-item'
    + (isSelected(record.key) ? ' selected' : '')
    + (state.selected === record.key ? ' primary' : '')
    + (expanded ? ' expanded' : '');

  const head = document.createElement('div');
  head.className = 'neuron-head';

  // Two separate boxes, because they answer different questions: which cells
  // an action applies to, and which cells are drawn.
  const select = document.createElement('input');
  select.type = 'checkbox';
  select.className = 'sel';
  select.checked = isSelected(record.key);
  select.title = 'Select for Apply actions';
  select.setAttribute('aria-label', `Select c${record.id}`);
  select.addEventListener('click', (event) => {
    event.stopPropagation();
    if (event.shiftKey && selectionAnchor && selectionAnchor !== record.key) {
      selectRange(selectionAnchor, record.key);
    } else {
      setSelected(record.key, select.checked);
    }
    selectionAnchor = record.key;
  });

  const visible = document.createElement('button');
  visible.type = 'button';
  visible.setAttribute('aria-label', `Show c${record.id} in the render`);
  // A checkbox redrew itself; a button has to be told. `setVisible` emits a
  // style event that repaints the scene but deliberately does not rebuild the
  // list, so the icon is updated in place here instead.
  const paintEye = () => {
    visible.className = `vis${record.visible ? '' : ' off'}`;
    visible.title = record.visible ? 'Hide in the render' : 'Show in the render';
    visible.setAttribute('aria-pressed', String(record.visible));
    visible.replaceChildren(eyeIcon(record.visible));
  };
  paintEye();
  visible.addEventListener('click', (event) => {
    event.stopPropagation();
    setVisible(record.key, !record.visible);
    paintEye();
  });

  const swatch = document.createElement('span');
  swatch.className = 'row-dot';
  swatch.style.background = graphOn ? graphColorsOf(record).annotation : record.color;
  if (graphOn) swatch.title = `GraphApp draws c${record.id} in this color; the 3D render keeps its own`;

  const chevron = document.createElement('span');
  chevron.className = 'chev';
  // U+FE0E keeps the triangles text glyphs; bare they can render as emoji.
  chevron.textContent = expanded ? '▼︎' : '▶︎';

  const name = document.createElement('div');
  name.className = 'neuron-name';
  name.innerHTML = `<b>c${record.id}</b>`;

  // A shut row is one line, the volume a two-letter tag; an open row grows a
  // second line that spells the volume out and names the technique.
  const vol = document.createElement('span');
  vol.className = 'neuron-vol';
  vol.textContent = volumeAbbrev(record.source);
  vol.title = `${volumeLongName(record.source)} — ${techniqueLabel(record.render)}`
    + (record.render.color_by_strata ? ', colored by IPL depth' : '')
    + (record.summary.label ? ` — ${record.summary.label}` : '');

  // Edited in Viking since it was loaded: the row says so, and the circular
  // arrow re-fetches it. The arrow is always there, so any cell can be
  // refreshed on demand; it lights up when the server reports a change.
  const stale = !!record.freshness?.stale;
  if (stale) li.classList.add('stale');
  const refresh = document.createElement('button');
  refresh.className = 'icon-btn refresh';
  refresh.textContent = '↻';
  refresh.title = stale ? freshnessNote(record) : `Refresh c${record.id} from Viking`;
  refresh.setAttribute('aria-label', `Refresh c${record.id} from Viking`);
  refresh.addEventListener('click', async (event) => {
    event.stopPropagation();
    await updateCell(record);
  });

  const remove = document.createElement('button');
  remove.className = 'icon-btn unload';
  remove.textContent = '✕';
  remove.title = `Unload c${record.id}`;
  remove.setAttribute('aria-label', `Unload c${record.id}`);
  remove.addEventListener('click', async (event) => {
    event.stopPropagation();
    await unloadCell(record);
  });

  // Select sits with the unload button on the right, separated from it by a
  // spacer so the two are not neighbors under a hurried pointer.
  const gap = document.createElement('span');
  gap.className = 'row-gap';
  if (stale) {
    const dot = document.createElement('span');
    dot.className = 'stale-dot';
    dot.title = 'Edited in Viking since it was loaded';
    name.querySelector('b').append(dot);
  }
  // Drawn with other settings than asked, because those built nothing or failed.
  if (record.fallback) {
    const mark = document.createElement('span');
    mark.className = 'fallback-mark';
    mark.textContent = 'auto';
    mark.title = `Drawn with ${record.fallback.text}: ${fallbackWhy(record)}. `
      + 'It keeps these settings until its annotations change.';
    name.querySelector('b').append(mark);
  }
  head.append(chevron, visible, swatch, name, vol, refresh, select, gap, remove);
  if (expanded) {
    // An open row's second line is the cell's look (2026-10-19): the visualization it
    // shows, to change with one pick, save, or manage.  What the line said before --
    // the volume, the technique, the label -- is the volume tag's hover.
    const sub = document.createElement('div');
    sub.className = 'neuron-sub vis-row';
    const label = document.createElement('span');
    label.className = 'vis-label';
    label.textContent = 'Look';
    sub.append(label, visualizationSelect(record, visualizationContext()));
    sub.title = `${volumeLongName(record.source)} · ${techniqueLabel(record.render)}`
      + (record.summary.label ? ` · ${record.summary.label}` : '');
    head.append(sub);
  }

  // Clicking the row only opens or closes it -- it never changes the
  // selection, so the cells an Apply will target survive opening the cell the
  // settings are coming from. The checkboxes and the unload button opt out.
  // The RenderApp menu lives on the list rows too -- that is where the
  // original app put it, and where the reflex reaches for it first.
  head.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    event.stopPropagation();
    contextMenuFor({
      key: record.key, clientX: event.clientX, clientY: event.clientY,
    });
  });

  head.addEventListener('click', (event) => {
    if (event.target.closest('.sel, .vis, .icon-btn')) return;
    const wasExpanded = expandedCells.has(record.key);
    // One open cell at a time: the panels are tall, and hunting for the Apply
    // buttons under three expanded cells is its own problem.
    expandedCells.clear();
    if (!wasExpanded) expandedCells.add(record.key);
    selectNeuron(record.key);
    renderNeuronList();
  });

  li.append(head);
  if (expanded) li.append(cellPanel(record));
  return li;
}

/**
 * Selection count and the buttons that need cells.  (The label of Apply to
 * selected is updateScopeNote's alone: two writers gave it different words.)
 */
function updateSelectionUi() {
  const n = state.selection.size;
  const total = state.neurons.size;
  const count = $('selection-count');
  const stale = [...state.neurons.values()].filter((r) => r.freshness?.stale).length;
  if (count) {
    // One centered line under the buttons; a second only when Viking moved on.
    const lines = [total === 0 ? 'No cells loaded'
      : n === 0 ? `${total} loaded, none selected`
      : `${n} of ${total} selected`];
    if (stale) lines.push(`${stale} edited in Viking`);
    count.replaceChildren(...lines.flatMap((line, i) => (
      i ? [document.createElement('br'), document.createTextNode(line)]
        : [document.createTextNode(line)])));
  }
  for (const id of ['select-all', 'select-none', 'refresh-all', 'clear-all']) {
    const b = $(id);
    if (b) b.disabled = total === 0;
  }
  const applySel = $('apply-selected');
  if (applySel) applySel.disabled = n === 0;
}

// -- applying changes ------------------------------------------------------
/** Rebuild one cell's mesh and extras under its current settings. */
async function rebuildOne(record) {
  renderer.removeMesh(record.key);
  await addToScene(record, { frame: false });
}

/**
 * Change one cell's display settings.
 *
 * Color and opacity are a material update and take effect at once.  Anything
 * needing new geometry from the server is fetched as soon as it changes; the
 * sliders debounce (`debounced`), so dragging one does not fire a request per
 * step.
 */
async function applyDisplay(record, changes) {
  const { changed, refetch } = setDisplay(record.key, changes);
  if (!changed) return;
  persist();
  if ('showMoves' in changes) moves?.update();
  if (!refetch) return;
  // The Position shift layer alone: rebuilt from the cached annotations, without
  // fetching the synapses and the rest again.
  if (onlyShiftKeys(changes)) {
    await shifts.refresh(record);
    return;
  }
  await refreshExtrasFor(record);
}

async function refreshExtrasFor(record) {
  await refreshExtras(record);
  renderer.setStyle(record.key, { visible: record.visible });
}

/**
 * Debounce per (cell, reason).
 *
 * A slider emits an `input` event per pixel of travel; each one that reaches
 * the server is a mesh rebuild. Coalescing them means the request goes out
 * once the user stops moving, and the pointer keeps its grip on the control
 * because nothing rebuilds the DOM underneath it.
 */
const SETTLE_MS = 300;
const timers = new Map();
function debounced(key, fn, ms = SETTLE_MS) {
  clearTimeout(timers.get(key));
  timers.set(key, setTimeout(() => { timers.delete(key); fn(); }, ms));
}

/** What the Visualizations menus act through (vismenu.js). */
function visualizationContext() {
  return {
    apply: (record, vis) => applyVisualization(record, vis),
    applyAll: (vis) => applyVisualizationToAll(vis),
    records: () => [...state.neurons.values()],
    status: (text) => { statusEl.textContent = text; statusEl.className = 'status'; },
    flare: (text) => flareStatus(text),
    hasBoundary: (source) => volumeHasBoundary(source),
    changed: () => renderNeuronList(),
    fail,
  };
}

/** Set a cell's look from a visualization (visualizations.js), keeping its color and Z range. */
function setLook(record, vis) {
  const live = state.neurons.get(record.key) ?? record;
  const look = visualizationLook(vis, live, { hasBoundary: volumeHasBoundary });
  setDisplay(live.key, look.display);
  // The render as given, even where the build would read it the same: the cell then shows the visualization by name.
  if (!setRenderOptions(live.key, look.render)) live.render = normalizeRender(look.render);
  live.opacity = look.opacity;
  renderer.setStyle(live.key, { opacity: look.opacity });
  return live;
}

/** Give one cell a visualization's look, and draw it so. */
async function applyVisualization(record, vis) {
  dismissApplyAll();
  const live = setLook(record, vis);
  renderNeuronList();
  persist();
  statusEl.textContent = `Drawing c${live.id} as "${vis.name}"…`;
  statusEl.className = 'status';
  await rebuildOne(live);
  statusEl.textContent = `c${live.id} is drawn as "${vis.name}"`;
}

/** Give every loaded cell a visualization's look (the right-click menu's follow-up). */
async function applyVisualizationToAll(vis) {
  const records = [...state.neurons.values()];
  for (const record of records) setLook(record, vis);
  renderNeuronList();
  persist();
  statusEl.textContent = `Drawing ${records.length} cells as "${vis.name}"…`;
  statusEl.className = 'status';
  for (const record of records) {
    const live = state.neurons.get(record.key);
    if (live) await rebuildOne(live);
  }
  statusEl.textContent = `${records.length} cells are drawn as "${vis.name}"`;
}

/** Copy one cell's display and color settings onto the given cells. */
async function copyCellTo(source, targets) {
  const others = targets.filter((r) => r && r.key !== source.key);
  if (!others.length) {
    statusEl.textContent = 'No other cells to copy to';
    statusEl.className = 'status';
    return;
  }
  for (const target of others) {
    // A Z range belongs to the cell it was cut on, not to a look being copied.
    const own = normalizeDisplay(target.display);
    setDisplay(target.key, { ...source.display, zMin: own.zMin, zMax: own.zMax });
    // Depth shading is a mesh property, so it travels with the display
    // settings rather than being left behind -- except into a volume with no
    // IPL boundary markers, where it cannot be built.
    setRenderOptions(target.key, {
      ...target.render,
      color_by_strata: !!source.render.color_by_strata && volumeHasBoundary(target.source),
    });
    target.opacity = source.opacity;
    renderer.setStyle(target.key, { opacity: source.opacity });
  }
  renderNeuronList();
  persist();
  statusEl.textContent = `Applying c${source.id}'s settings to ${others.length} cell(s)…`;
  for (const target of others) await rebuildOne(target);
  statusEl.textContent = `Applied c${source.id}'s settings to ${others.length} cell(s)`;
}

// -- per-cell display panel -------------------------------------------------
function field(parent, labelText, control) {
  const label = document.createElement('label');
  label.textContent = labelText;
  parent.append(label, control);
  return control;
}

/**
 * A slider paired with a number box, both driving the same value.
 *
 * The slider is for reaching, the box is for precision. `onSettle` runs once
 * the value stops changing; `onLive` runs on every step for changes that are
 * free to apply (a material color or opacity).
 */
function slider(parent, labelText, { min, max, step, value, unit = '',
                                     decimals = 2, onLive, onSettle, key,
                                     boxStep = step }) {
  // Label and number box on one line, the track full width beneath. Laying the
  // track beside the box collapses it to a few pixels in a 300px sidebar,
  // which is unusable however correct the values are.
  const wrap = document.createElement('div');
  wrap.className = 'slider-field';

  const top = document.createElement('div');
  top.className = 'slider-label';
  const caption = document.createElement('span');
  caption.textContent = labelText;

  const box = document.createElement('input');
  box.type = 'number';
  box.className = 'numbox';
  // `boxStep`: a typed value may be finer than the track's notches.
  box.min = String(min); box.max = String(max); box.step = String(boxStep);
  box.value = Number(value).toFixed(decimals);
  box.setAttribute('aria-label', `${labelText} value`);

  top.append(caption, box);
  if (unit) {
    const u = document.createElement('span');
    u.className = 'unit';
    u.textContent = unit;
    top.append(u);
  }

  const range = document.createElement('input');
  range.type = 'range';
  range.min = String(min); range.max = String(max); range.step = String(step);
  range.value = String(value);
  range.setAttribute('aria-label', labelText);

  const clamp = (v) => Math.min(max, Math.max(min, v));
  const push = (v, live) => {
    if (live && onLive) onLive(v);
    if (onSettle) debounced(key, () => onSettle(v));
  };

  range.addEventListener('input', () => {
    const v = Number(range.value);
    box.value = v.toFixed(decimals);
    push(v, true);
  });
  // `change`, not `input`: committing per keystroke would fire on the "0" of
  // "0.05" and snap the value away under the cursor.
  box.addEventListener('change', () => {
    // An emptied box is no number: Number('') is 0, which the clamp would turn
    // into the slider's minimum, so the box is read as text first and put back
    // to the slider's value when it does not hold one.
    const typed = box.value.trim() === '' ? NaN : Number(box.value);
    if (!Number.isFinite(typed)) { box.value = Number(range.value).toFixed(decimals); return; }
    const v = clamp(typed);
    box.value = v.toFixed(decimals);
    range.value = String(v);
    push(v, true);
  });

  wrap.append(top, range);
  parent.append(wrap);
  return wrap;
}

async function unloadCell(record) {
  // Captured before the record goes: color and per-type synapse settings
  // exist nowhere else, so without this an accidental click loses real work.
  const snapshot = {
    id: record.id, source: record.source, color: record.color,
    opacity: record.opacity, visible: record.visible,
    render: { ...record.render },
    display: JSON.parse(JSON.stringify(record.display)),
    corrections: record.corrections,
    correctionMode: record.correctionMode,
    registration: record.registration,
    manualMoves: record.manualMoves ?? null,
    wasSelected: isSelected(record.key),
  };
  try {
    try {
      await api.unload(record.source, record.id);
    } catch (error) {
      // 404: the server no longer holds the cell (a restart empties its
      // table). The user is removing the row either way -- the row must go.
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
    forgetCell(record);
    persist();
    offerUndo(snapshot);
  } catch (error) { fail(error); }
}

/**
 * Unload every loaded cell at once.  Undo restores them: the history step
 * pushed first holds every cell with its color, settings and selection.
 */
async function clearAllCells() {
  const records = [...state.neurons.values()];
  if (!records.length) return;
  pushHistoryNow();
  statusEl.textContent = `Unloading ${records.length} cell${records.length === 1 ? '' : 's'}…`;
  statusEl.className = 'status busy';
  const failed = [];
  for (const record of records) {
    try {
      try {
        await api.unload(record.source, record.id);
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
      forgetCell(record);
    } catch (error) {
      console.error(error);
      failed.push({ record, error });
    }
  }
  persist();
  renderNeuronList();
  const removed = records.length - failed.length;
  statusEl.textContent = failed.length
    ? `Unloaded ${removed} of ${records.length} cells — ${failed.map((f) => `c${f.record.id}`).join(', ')} `
      + `could not be (${failed[0].error instanceof ApiError ? failed[0].error.detail : failed[0].error.message})`
    : `Unloaded ${removed} cell${removed === 1 ? '' : 's'} — Undo brings them back`;
  statusEl.className = failed.length ? 'status error' : 'status';
}

/**
 * Everything this tab holds for a cell: drawn, cached, listed.  The one place
 * a cell is let go of -- the ✕, Unload all, undo, a stopped load and Open... --
 * so a cache added for a cell is added here once, not in hand-copied lists
 * that disagreed (the Skeleton check survived three of them, the client's
 * meshes survived all but one, and a cell loaded again under another
 * Registration setting was handed the surface drawn for the first).
 */
function forgetCell(record) {
  // Its surfaces stay in the mesh cache (bounded; see MeshCache): Undo of the
  // unload draws the cell again from them instead of downloading them again.
  forgetDrawn(record, { keepSurfaces: true });
  draws.forget(record.key);
  expandedCells.delete(record.key);
  forgetSkeletonCheck(record.key);
  metricsCache.delete(record.key);
  removeNeuron(record.key);
}

/**
 * Drop a cell a stopped load never drew, as its ✕ would but with no undo
 * step.  The tab lets go first, so the cell's mesh poll finds it gone and
 * cancels the job on the server (api.mesh's `alive`); the server's copy is
 * unloaded after, in the background.
 */
function discardCell(record) {
  forgetCell(record);
  api.unload(record.source, record.id).catch(() => {});
}

/**
 * What a load's Stop does to the cells it has started: one with a surface on
 * screen stays; one still fetching or building is dropped.  `started` holds
 * the keys of cells the load added that were not in the list before it.
 */
function stopLoad(started) {
  for (const key of started) {
    const record = state.neurons.get(key);
    if (record && !renderer.meshes.has(key)) discardCell(record);
  }
  renderNeuronList();
  persist();
}

// The cell panel's width is a CSS variable; the handle between it and the
// viewport drags it, 300px is the floor, and the choice is kept per browser.
const SIDEBAR_WIDTH_KEY = 'sbfsem:sidebar-width';
const SIDEBAR_MIN_PX = 300;

function applySidebarWidth(px) {
  const w = Math.round(Math.max(SIDEBAR_MIN_PX, Math.min(px, window.innerWidth * 0.6)));
  document.documentElement.style.setProperty('--sidebar-w', `${w}px`);
  return w;
}

function initSidebarResizer() {
  const handle = $('sidebar-resizer');
  if (!handle) return;
  try {
    const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
    if (saved) applySidebarWidth(saved);
  } catch { /* storage blocked: default width */ }
  handle.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    handle.classList.add('dragging');
    handle.setPointerCapture(event.pointerId);
    let width = 0;
    const move = (ev) => { width = applySidebarWidth(ev.clientX); };
    const stop = () => {
      handle.classList.remove('dragging');
      // All three come off together: with `once` on the two end events,
      // whichever did not fire stayed on the handle, one more every drag.
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', stop);
      handle.removeEventListener('pointercancel', stop);
      if (width) {
        try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width)); } catch { /* fine */ }
      }
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  });
  handle.addEventListener('dblclick', () => {
    applySidebarWidth(SIDEBAR_MIN_PX);
    try { localStorage.removeItem(SIDEBAR_WIDTH_KEY); } catch { /* fine */ }
  });
}

// -- full screen -------------------------------------------------------------
// The browser's own full screen (F11, ⌃⌘F, Firefox's ⌘⇧F) is inferred by
// fullscreen.js; the toolbar button and `f` ask for it through the Fullscreen
// API. Either way the page reclaims the title bar and, unless the user keeps
// it, the cell list (asked for 2026-09-17).

const FS_SIDEBAR_KEY = 'sbfsem:fullscreen-sidebar';
let fullscreenNoticeShown = false;

function initFullscreen() {
  const button = $('fullscreen-btn');
  const cells = $('fs-cells');
  button?.addEventListener('click', () => toggleFullscreen());
  cells?.addEventListener('click', () => {
    const on = document.body.classList.toggle('fs-sidebar');
    cells.setAttribute('aria-pressed', String(on));
    // Remembered: someone who wants the list in full screen wants it every time.
    try { localStorage.setItem(FS_SIDEBAR_KEY, on ? '1' : '0'); } catch { /* fine */ }
  });
  renderer.onToggleFullscreen = () => toggleFullscreen();
  // The status line lives in the title bar, which full screen hides; it
  // moves to the end of the tab strip for the duration and back afterwards.
  const statusHome = statusEl.parentElement;
  const statusNext = statusEl.nextSibling;
  const tabs = document.querySelector('.tabs');
  watchFullscreen((on) => {
    document.body.classList.toggle('fullscreen', on);
    if (on && tabs) tabs.append(statusEl);
    else if (!on && statusHome && statusEl.parentElement !== statusHome) {
      statusHome.insertBefore(statusEl, statusNext);
    }
    if (on) {
      let keep = false;
      try { keep = localStorage.getItem(FS_SIDEBAR_KEY) === '1'; } catch { /* fine */ }
      document.body.classList.toggle('fs-sidebar', keep);
      cells?.setAttribute('aria-pressed', String(keep));
    } else {
      document.body.classList.remove('fs-sidebar');
    }
    if (button) {
      button.textContent = on ? 'Exit full screen' : 'Full screen';
      button.title = on
        ? `Back to the browser window (f, or the browser's ${browserShortcut()})`
        : 'Fill the screen: the title bar goes and the cell list folds away behind ≡ Cells (f). '
          + `The browser's own full screen (${browserShortcut()}) does the same`;
    }
    if (on && !fullscreenNoticeShown) {
      fullscreenNoticeShown = true;
      statusEl.textContent = `Full screen — ≡ Cells shows the cell list; f or ${browserShortcut()} leaves`;
      statusEl.className = 'status';
    }
  });
}

/**
 * Drop everything drawn from a cell's old positions, so the next draw asks again.
 * Its surfaces too, unless the cell is merely unloaded (`keepSurfaces`): their
 * keys say which copy of the cell they are of, so they stay for an Undo to find.
 * Its outlines, annotations and skeleton stay in `keptExtras`, by the state they
 * are of, for a later load at that state; the next draw asks for them there first.
 */
function forgetDrawn(record, { keepSurfaces = false, keepDrawn = false } = {}) {
  if (!keepSurfaces) dropWithPrefix(meshCache, `${record.key}|`);
  skeletonCache.delete(record.key);
  flatSkeletonCache.delete(record.key);
  nodesCache.delete(record.key);
  nodesAsked.delete(record.key);
  edgesCache.delete(record.key);
  outlinesCache.delete(record.key);
  annoWireCache.delete(record.key);
  // `keepDrawn`: what is on screen stays until the next draw replaces it, piece by
  // piece (the manual moves' Apply: the cell never leaves the screen while it builds).
  if (keepDrawn) return;
  renderer.removeAll(record.key);
  // The points a build shows while it waits live under a key of their own, which
  // removeAll does not know: a cell let go of mid-build left them on screen.
  renderer.removePoints(waitingKey(record));
}

/**
 * Where one cell's annotations are drawn: Viking's current section transforms
 * (`registration`, on or off) and Viking's section correction (`corrections`:
 * 'off' or a mode, curvefit, neighbor or all).  The server moves the annotations when it loads the cell,
 * so this loads it again (from its cache, not Viking) with both settings and
 * redraws everything; the setting not being changed is passed as it is.
 */
/**
 * One cell's loads, one at a time and in the order they were asked for, so
 * the server ends with the settings of the last one.
 */
const loadQueues = new Map();
function loadInOrder(key, body) {
  const prior = loadQueues.get(key) ?? Promise.resolve();
  const next = prior.catch(() => {}).then(() => api.load(body));
  loadQueues.set(key, next);
  const settle = () => { if (loadQueues.get(key) === next) loadQueues.delete(key); };
  next.then(settle, settle);
  return next;
}

/**
 * Load a cell again under its own settings and with its manual moves (the move
 * panel's Apply, the Manual moves list), and draw it from the copy the server
 * moved; the markers on it go where its annotations now are.  The surfaces
 * already built stay cached (their key holds the moves they were built with), so
 * undoing a move draws the surface from before at once.  Resolves the new record,
 * or null when the cell went or was loaded again meanwhile.
 */
async function reloadWithMoves(record) {
  const live = state.neurons.get(record.key) ?? record;
  const detail = await loadInOrder(record.key, {
    id: live.id, source: live.source, include_synapses: true, check_fresh: false,
    section_corrections: live.corrections, live_registration: live.registration,
  });
  if (!state.neurons.has(record.key)) {
    api.unload(record.source, record.id).catch(() => {});
    return null;
  }
  const fresh = upsertNeuron(detail, { select: false });
  // Drawing the cell again lets go of the selection and the markers on it: they
  // come back, where its moved annotations are now.  The cell stays drawn as it was
  // while the moved copy builds (its surface, rings and skeleton each replaced as
  // the new one comes), rather than leaving the screen for the build.
  const kept = {
    primary: renderer.selection, above: renderer.markers.above, below: renderer.markers.below,
    slices: { ...renderer.slices },
  };
  forgetDrawn(fresh, { keepSurfaces: true, keepDrawn: true });
  // The moved copy's rings, outlines and skeleton are asked for beside its surface's
  // build, and drawn with the surface (the user's choice, 2026-10-20: the screen never
  // shows two copies at once; the moves' preview stands in until then).
  await addToScene(fresh, { frame: false, extrasEarly: true });
  if (loadGeneration(record.key) !== fresh.loadGen) return null;
  await remarkMoved(fresh, kept);
  renderNeuronListWhenIdle();
  persist();
  return fresh;
}

/**
 * Put back the selection and markers `kept` from before `record` was drawn again,
 * those on it where its annotations are drawn now, the arrow keys' marker with them.
 */
async function remarkMoved(record, kept) {
  const nodes = await nodesFor(record);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const moved = (item) => {
    if (!item || item.kind !== 'annotation' || item.key !== record.key) return item;
    const node = byId.get(item.locationId);
    const next = node ? { ...item, point: [...node.xyz_um], zUm: node.xyz_um[2], section: node.z_section } : item;
    if (activeMarker === item) activeMarker = next;
    return next;
  };
  const primary = moved(kept.primary);
  if (primary && renderer.selection !== primary) renderer.select(primary, { keepMarkers: true });
  for (const slot of ['above', 'below']) {
    const next = moved(kept[slot]);
    if (next && renderer.markers[slot] !== next) renderer.setMarker(slot, next, { pulse: false });
  }
  // A z-slice is read again, where the annotations on its section are drawn now.
  for (const slot of SLOTS) {
    const slice = kept.slices?.[slot];
    if (!slice || !renderer.slotItem(slot)) continue;
    if (!renderer.slices[slot] || slice.items.some((c) => c.key === record.key)) retakeSlice(slot, slice).catch(fail);
  }
}

/** Select one of `record`'s annotations by its Location ID and bring it into view (the Manual moves list). */
async function flyToAnnotation(record, locationId) {
  const nodes = await nodesFor(record);
  const node = nodes.find((n) => n.id === locationId);
  if (!node) {
    flareStatus(`Annotation ${locationId} is no longer in c${record.id}`);
    return;
  }
  renderer.select({
    kind: 'annotation', key: record.key, locationId, point: [...node.xyz_um],
    zUm: node.xyz_um[2], section: node.z_section, radius: node.radius_um,
  });
  activeMarker = renderer.selection;
  const distance = renderer.camera.position.distanceTo(renderer.controls.target);
  await renderer.flyTo(renderer.toWorld(node.xyz_um, record.source), { distance });
}

async function setRegistration(record, changes) {
  // The control that asked for this: while it still has the focus the list is its own to rebuild.
  const origin = document.activeElement;
  const live = state.neurons.get(record.key) ?? record;
  const before = { registration: live.registration, corrections: live.corrections };
  const want = { ...before, ...changes };
  // Kept on the cell at once: a second click before this load returns builds
  // on this one's choice, not on the settings from before it.
  live.registration = want.registration;
  live.corrections = want.corrections;
  // A mode, or 'off'; true and false are how a cell saved before the modes says it.
  const correcting = !!changes.corrections && changes.corrections !== 'off';
  const modeName = (CORRECTION_MODES.find(([v]) => v === changes.corrections)?.[1]
    || 'Residual field').toLowerCase();
  const turning = 'registration' in changes
    ? `${changes.registration ? 'Applying' : 'Removing'} Viking's current section transforms`
    : correcting ? `Applying Viking's section correction (${modeName})`
      : "Removing Viking's section correction";
  statusEl.textContent = `${turning} for c${record.id}…`;
  statusEl.className = 'status';
  try {
    const detail = await loadInOrder(record.key, {
      id: record.id, source: record.source, include_synapses: true,
      live_registration: want.registration, section_corrections: want.corrections,
    });
    // Unloaded while this load was under way: the row is gone, and so is the
    // choice.  The server holds a copy nobody will ask for again.
    if (!state.neurons.has(record.key)) {
      api.unload(record.source, record.id).catch(() => {});
      return;
    }
    // The record carries the latest choice over (upsertNeuron), which may be
    // a later click's; that click's load follows this one and draws last.
    // The cell's tick is the user's: this is not a request to select it.
    const fresh = upsertNeuron(detail, { select: false });
    forgetDrawn(fresh);
    // The new copy's rings, outlines and skeleton are asked for while its surface
    // builds, as an Apply's are: one after another once it was drawn, they came
    // 0.5 to 2.0 s after it (production, 2026-10-20).
    await addToScene(fresh, { frame: false, extrasEarly: true });
    // Loaded again meanwhile: the newer load draws and reports.
    if (loadGeneration(record.key) !== fresh.loadGen) return;
    // The box ticked or the mode chosen is already in the record, so a list
    // that waits for the focus to leave a dropdown shows what was chosen.  The dropdown
    // that asked for this load is the one still focused: it is rebuilt now, or its section
    // would keep the numbers of the mode it was changed from; any other one is waited for.
    if (document.activeElement === origin) renderNeuronList();
    else renderNeuronListWhenIdle();
    persist();
    const lr = detail.summary?.live_registration;
    const sc = detail.summary?.section_correction;
    if ('registration' in changes) {
      if (changes.registration && !lr?.applied) {
        flareStatus(`c${record.id}: drawn at the stored positions — ${lr?.note || 'no answer'}`);
      } else {
        statusEl.textContent = changes.registration
          ? `c${record.id}: ${lr.n_moved.toLocaleString('en-US')} annotations where Viking's `
            + `current transforms put them, at most ${lr.max_um.toFixed(2)} µm from their stored position`
          : `c${record.id}: drawn at Viking's stored positions`;
      }
    } else if (correcting && !sc?.applied) {
      flareStatus(`c${record.id}: no section correction applied — ${sc?.note || 'no answer'}`);
    } else {
      statusEl.textContent = correcting
        ? `c${record.id}: ${sc.n_moved.toLocaleString('en-US')} annotations moved by Viking's `
          + `section correction (${modeName}), at most ${sc.max_um.toFixed(2)} µm`
        : `c${record.id}: Viking's section correction removed`;
    }
  } catch (error) {
    // Not loaded: the cell is as it was, so its checkboxes go back too
    // (unless a later click has changed them since).
    const now = state.neurons.get(record.key);
    if (now && now.registration === want.registration && now.corrections === want.corrections) {
      now.registration = before.registration;
      now.corrections = before.corrections;
      renderNeuronList();
    }
    fail(error);
  }
}

/**
 * Re-fetch a cell from Viking, bypassing every cache, and redraw it.  Resolves
 * true when it was redrawn, false when the cell was unloaded meanwhile; a
 * failure throws (updateCell and Refresh all each say it their own way).
 */
async function refetchCell(record) {
  // The choices as the cell holds them now: `record` may be a copy from
  // before a Registration click.
  const now = state.neurons.get(record.key) ?? record;
  const detail = await loadInOrder(record.key, {
    id: record.id, source: record.source,
    include_synapses: true, refresh: true,
    section_corrections: now.corrections, live_registration: now.registration,
  });
  // Unloaded while Viking was asked: do not bring the row back.
  if (!state.neurons.has(record.key)) {
    api.unload(record.source, record.id).catch(() => {});
    return false;
  }
  // The whole point of Update is fresh data: fold the new summary and
  // synapse counts back into the record instead of discarding them, and draw
  // the record that holds them (upsertNeuron makes a new one; `record` is the
  // one from before).  Freshly fetched: nothing newer can exist in Viking at
  // this moment.  Updating is not a request to select the cell.
  const fresh = upsertNeuron(
    { ...detail, freshness: { stale: false, loaded: detail.summary?.data_stamp, live: null } },
    { select: false });
  forgetDrawn(fresh);
  await addToScene(fresh, { frame: false, extrasEarly: true });
  renderNeuronListWhenIdle();
  return true;
}

async function updateCell(record) {
  statusEl.textContent = `Updating c${record.id} from Viking…`;
  statusEl.className = 'status';
  try {
    if (await refetchCell(record)) statusEl.textContent = `c${record.id} updated`;
  } catch (error) { fail(error); }
}

/** The most cells the freshness route takes in one request (routes.FreshnessBatchBody). */
const FRESHNESS_BATCH = 500;

/**
 * Whether Viking has edited these cells since they were fetched, in one
 * request per volume (Viking's StructureFreshnessBatch), after a batch load
 * rather than one live query per cell during it.  Best effort and in the
 * background: the dots and the "edited in Viking" count appear when it
 * answers, and a failure leaves the cells unmarked, as an unreachable Viking
 * always has.
 */
async function checkFreshness(records) {
  const cells = records.filter(Boolean);
  if (!cells.length) return;
  try {
    // The route answers at most FRESHNESS_BATCH cells; a larger session asks in pieces.
    for (let from = 0; from < cells.length; from += FRESHNESS_BATCH) {
      const { cells: answers } = await api.freshnessBatch(
        cells.slice(from, from + FRESHNESS_BATCH).map((r) => ({ source: r.source, id: r.id })));
      for (const answer of answers || []) {
        const record = state.neurons.get(keyOf(answer.source, answer.id));
        if (record && answer.freshness) record.freshness = answer.freshness;
      }
    }
    renderNeuronListWhenIdle();
  } catch (error) {
    console.warn('freshness check skipped:', error);
  }
}

/** What changed in Viking, for the refresh button's tooltip. */
function freshnessNote(record) {
  const f = record.freshness;
  const counts = Number.isFinite(f.live_count) && Number.isFinite(f.loaded_count)
    && f.live_count !== f.loaded_count
    ? `; ${f.loaded_count.toLocaleString()} annotations loaded, ${f.live_count.toLocaleString()} now`
    : '';
  if (f.children_stale) {
    return `c${record.id}'s synapses or other child structures were edited in Viking `
      + 'after this data was fetched; its own annotations, and so its surface, did not change — refresh';
  }
  return `c${record.id} was edited in Viking (${humanStamp(f.live)}) after this data was `
    + `fetched (${humanStamp(f.loaded)})${counts} — refresh`;
}

/** Re-fetch every loaded cell from Viking, stale ones first. */
async function refreshAllCells() {
  const records = [...state.neurons.values()]
    .sort((a, b) => Number(!!b.freshness?.stale) - Number(!!a.freshness?.stale));
  if (!records.length) return;
  const staleCount = records.filter((r) => r.freshness?.stale).length;
  let done = 0;
  const failed = [];
  const button = $('refresh-all');
  button.disabled = true;
  try {
    await inParallel(records, LOAD_CONCURRENCY, async (record) => {
      if (!state.neurons.has(record.key)) return;
      try {
        if (await refetchCell(record)) done += 1;
      } catch (error) {
        console.error(error);
        failed.push({ record, error });
      }
      statusEl.textContent = `Refreshed ${done} of ${records.length} from Viking…`;
      statusEl.className = 'status';
    });
    // The count is of the cells that were refreshed; the ones that were not are
    // named with the first reason, instead of the last progress line saying all
    // went well.
    statusEl.textContent = `Refreshed ${done} cell(s) from Viking`
      + (staleCount ? ` (${staleCount} had been edited)` : '')
      + (failed.length
        ? ` — ${failed.length} failed: ${failed.map((f) => `c${f.record.id}`).join(', ')} `
          + `(${failed[0].error instanceof ApiError ? failed[0].error.detail : failed[0].error.message})`
        : '');
    statusEl.className = failed.length ? 'status error' : 'status';
  } finally {
    button.disabled = false;
    renderNeuronListWhenIdle();
  }
}

function humanStamp(value) {
  if (!value) return 'unknown time';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

// -- context menu on rendered cells ---------------------------------------
// The RenderApp right-click menu, ported: right-click a cell in the 3D view
// (a click, not a pan drag) and act on that cell directly.

function hideContextMenu() {
  const menu = document.getElementById('ctx-menu');
  if (!menu) return;
  menu.remove();
  // A chooser row may have been lighting up a candidate that was not chosen.
  renderer?.preview(null);
}

/**
 * The right-click menu.  Opened on the render it comes with `candidates`, what
 * the right click found under the pointer (render3d.js `contextCandidatesAt`:
 * annotations, a skeleton branch, a synapse type, the cells' surfaces, nearest
 * first); opened from a cell's row in the list it comes with a `key` and nothing
 * else.  The menu is about the first of the candidates until a row of its list
 * says otherwise, and `chosen` is the row that did.
 */
function contextMenuFor({
  key, point = null, clientX, clientY, candidates = [], chosen = null, pane = false, at = null,
}) {
  hideContextMenu();
  // A panel this menu opened last time; not in hideContextMenu, which every
  // click outside the menu calls -- including the clicks inside that panel.
  closeAnnotators();
  const rows = candidates.filter((c) => state.neurons.has(c.key));
  const context = rows.find((c) => sameCandidate(c, chosen)) ?? rows[0] ?? null;
  if (context) {
    key = context.key;
    point = renderer.toWorld(context.point, state.neurons.get(context.key).source);
  }
  const record = key ? state.neurons.get(key) : null;
  // Off every cell there is still one thing to offer: a cell hidden past its
  // last annotation has nothing left to right-click.
  const cut = record ? [] : [...state.neurons.values()].filter(hasZHide);
  if (!record && !cut.length && !renderer.selection) return;

  const menu = document.createElement('div');
  menu.id = 'ctx-menu';
  menu.className = 'ctx-menu';
  const title = document.createElement('div');
  title.className = 'ctx-title';
  title.textContent = record
    ? (context ? contextTitle(context, namingOf(record.key)) : `c${record.id}`)
    : (cut.length ? 'Hidden by Z' : 'Selection');
  menu.append(title);
  // More than one thing under the pointer: the choice, above the menu's own
  // items, which all stay.  A row selects what the render's selection can hold
  // (an annotation, a branch, a synapse type) and makes the menu about it;
  // the surface and the rest only make it about their cell.
  if (record && offersChoice(rows)) {
    // (In a Render compare pane the render's selection is not the pane's, so a row
    // only makes the menu about it.)
    menu.append(...underThePointer(rows, context, (row) => {
      if (!pane && SELECTABLE_KINDS.has(row.kind)) choose(row);
      // The menu again, about the row, where it was (it moves only as far as a taller
      // one must), and the render's keys -- the arrows step the markers -- still work.
      const was = document.getElementById('ctx-menu')?.getBoundingClientRect();
      contextMenuFor({
        key, point, clientX, clientY, candidates, chosen: row, pane,
        at: was ? { x: was.left, y: was.top } : null,
      });
      if (!pane) renderer.webgl.domElement.focus();
    }, { light: !pane }));
  }

  const item = (label, run, { checked = false } = {}) => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'ctx-item';
    el.textContent = (checked ? '✓ ' : '') + label;
    el.addEventListener('click', async () => {
      hideContextMenu();
      await run();
    });
    menu.append(el);
    return el;
  };
  const submenu = (label, entries) => {
    const holder = document.createElement('div');
    holder.className = 'ctx-item ctx-parent';
    holder.textContent = `${label} ▸`;
    const sub = document.createElement('div');
    sub.className = 'ctx-sub';
    for (const [subLabel, run] of entries) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'ctx-item';
      el.textContent = subLabel;
      el.addEventListener('click', async () => {
        hideContextMenu();
        await run();
      });
      sub.append(el);
    }
    // A submenu low in the window rises just enough to stay on screen: the
    // last ones (the exports) otherwise open past its bottom edge.  One that
    // would open past the right edge (the menu is against it: the list of what is
    // under the pointer makes it wide) opens to the left of its item instead.
    holder.addEventListener('mouseenter', () => {
      sub.style.top = '';
      sub.style.left = '';
      sub.style.right = '';
      const box = sub.getBoundingClientRect();
      const over = box.bottom - (window.innerHeight - 8);
      if (over > 0) sub.style.top = `${-4 - over}px`;
      if (box.right > window.innerWidth - 8) {
        sub.style.left = 'auto';
        sub.style.right = 'calc(100% - 4px)';
      }
    });
    holder.append(sub);
    menu.append(holder);
  };

  // Turn about the point right-clicked, or about the middle again (P does both).
  // Not in a Render compare pane, whose camera is not the 3D render's.
  const pivotItem = () => {
    if (pane) return;
    if (renderer.pivotPoint()) {
      item('Rotate about the middle of the view again (P)', () => renderer.clearPivot('off')).title =
        'Turns pivot about the middle of the picture again, as they did before a point was chosen';
    } else if (record) {
      item('Rotate about this point (P)', () => pivotAt(clientX, clientY)).title =
        'Every turn pivots about this point from now on, so it stays where it is on the screen '
        + '(the middle of the annotation here, so a process turns about its own axis)';
    }
  };

  // GraphApp's right click is short: isolate the cell, Viking, deselect.
  if (graphOn && !pane) {
    if (record) graphMenuItems(record, context, item);
    else if (renderer.selection) item('Deselect', () => renderer.clearSelection()).title = 'Also Esc';
    pivotItem();
    placeContextMenu(menu, at?.x ?? clientX, at?.y ?? clientY);
    return;
  }

  // Off every cell -- the surface hidden, only rings under the pointer -- the
  // markers still say which cell they are on.
  const marked = !record && renderer.selection?.kind === 'annotation'
    ? state.neurons.get(renderer.selection.key) : null;
  const deselect = () => {
    if (!renderer.selection) return;
    const withMarkers = renderer.markers.above || renderer.markers.below;
    item('Deselect', () => renderer.clearSelection()).title =
      `${describeCandidate(renderer.selection)}${withMarkers ? ' and its red and blue markers' : ''}`
      + ' — also Esc (the markers first), or a click on empty space';
  };
  // Moving the chosen annotation by hand (2026-10-19): the one the arrow keys act on.
  const moveItem = () => {
    const t = moves?.target();
    if (!t) return;
    if (moves.active) {
      item('Done moving (Esc)', () => moves.stop()).title = 'Leave move mode; a move not yet in the render is applied';
      return;
    }
    item(`Move annotation ${t.item.locationId} by hand… (g)`, () => moves.start()).title =
      `Move the ${SLOT_NAMES[t.slot]} annotation further than Viking's correction did: ←/→ along X, Y, Z, `
      + 'the viewport\'s axes or its own shift; kept with the cell under its Registration setting, '
      + 'never written to Viking';
  };
  if (!record) {
    if (marked) zLevelItems(item, marked, ` c${marked.id}`);
    for (const r of cut) item(`Restore full render of c${r.id}`, () => restoreZ(r));
    if (!pane) zSliceItems(item, context);
    moveItem();
    deselect();
    pivotItem();
    placeContextMenu(menu, at?.x ?? clientX, at?.y ?? clientY);
    return;
  }

  item('Update', () => updateCell(record));
  item('Remove', () => unloadCell(record));
  item('Change Color', () => {
    const input = document.createElement('input');
    input.type = 'color';
    input.value = record.color;
    input.style.position = 'fixed';
    input.style.left = `${clientX}px`;
    input.style.top = `${clientY}px`;
    input.style.opacity = '0';
    document.body.append(input);
    input.addEventListener('input', () => {
      setColor(record.key, input.value);
      renderNeuronList();
    });
    input.addEventListener('change', () => input.remove());
    input.click();
  });
  // The cell's look at one pick (2026-10-19), then a button to give it to every cell.
  submenu('Visualizations', visualizationItems(record, visualizationContext(), { x: clientX, y: clientY }));
  submenu('Change Transparency', [1, 0.75, 0.5, 0.25, 0.1].map((v) => [
    `${Math.round(v * 100)}%`, () => {
      record.opacity = v;
      renderer.setStyle(record.key, { opacity: v });
      persist();
      renderNeuronList();
    },
  ]));
  const strata = item('Color by strata', async () => {
    setRenderOptions(record.key, {
      ...record.render, color_by_strata: !record.render.color_by_strata,
    });
    persist();
    await rebuildOne(record);
    renderNeuronList();
  }, { checked: !!record.render.color_by_strata });
  // Depth shading reads the volume's IPL boundary markers, and a volume that
  // has none answers the build with an error: the cell panel's checkbox is
  // disabled there, and so is this.
  if (!volumeHasBoundary(record.source)) {
    strata.disabled = true;
    strata.title = `${volumeLongName(record.source)} has no IPL boundary markers`;
  }
  // Hide the cell above or below the annotation a triple click selected: two
  // clipping planes, so it is instant and nothing is re-rendered or exported
  // differently.  The level can come from another cell of the same volume.
  zLevelItems(item, record);
  if (hasZHide(record)) item('Restore full render', () => restoreZ(record));
  if (!pane) zSliceItems(item, context);
  moveItem();
  deselect();
  pivotItem();
  item('Show Last Modified', async () => {
    try {
      const row = await api.lastModified(record.source, record.id);
      const when = row.last_modified
        ? new Date(row.last_modified).toLocaleString() : 'unknown time';
      const who = row.username ? ` by ${row.username}` : '';
      statusEl.textContent = `c${record.id} last modified ${when}${who}`;
      statusEl.className = 'status';
      if (row.xyz_um) {
        await renderer.flyTo(renderer.toWorld(row.xyz_um, record.source), { duration: 1500, distance: 6 });
      }
    } catch (error) { fail(error); }
  });
  // Live from Viking, so only for volumes it serves (not the bundled demo).
  if (state.volumes.find((v) => v.name === record.source)?.is_odata) {
    const who = item('Return list of annotators', () => showAnnotators(record, {
      api,
      volumeName: volumeLongName,
      place: (el) => placeContextMenu(el, clientX, clientY),
    }));
    who.title = 'Who annotated this cell and its synapses, with each one’s share — '
      + 'and what Viking’s one-username-per-annotation record cannot say';
  }
  // Only for volumes the identity server knows; the bundled demo is not one.
  if (state.volumes.find((v) => v.name === record.source)?.viking_name) {
    // An annotation that is the menu's own (a row of the list, or the one under
    // the pointer) is where Viking goes, exactly; else the annotation nearest
    // the point.
    const exact = context?.kind === 'annotation' ? context : null;
    const go = (options = {}) => (exact
      ? openLocationInViking(record, exact.locationId, options)
      : openInViking(record, point, null, options));
    const open = item('Open in Viking', () => go());
    open.title = (exact
      ? `Opens this volume in Viking at annotation ${exact.locationId}`
      : point
        ? 'Opens this volume in Viking at the annotation under the pointer (within 5 µm), else at the point itself'
        : 'Opens this volume in Viking at the cell’s largest annotation')
      + '. If the unsigned Viking Test is installed too, the first open of a session asks which.';
    // Windows only, like the launch itself: the way to pick Viking Test while
    // only the signed Viking has answered so far, or to change the pick.
    if (onWindows()) {
      item('Choose Viking client…', () => go({ forceAsk: true })).title =
        'Open in Viking or in Viking Test (the unsigned build that installs beside it); '
        + 'the choice holds for this session';
    }
  }
  // These act on the cell the menu was opened on: it is made the focused cell
  // first (the Analyses and Annotations tabs describe the focused cell, which
  // was whichever one the list last showed), not whatever was focused before.
  item('Open in GraphApp', () => openInGraphApp(record)).title =
    `Show c${record.id} alone as MATLAB's GraphApp drew it: skeleton, a dot at every annotation, rings, `
    + 'and the unfinished, terminal and off-edge annotations and synapses on request. The 3D render button '
    + 'comes back to this view with every cell shown as it was';
  submenu('Analysis', [
    ['Connectivity (Graffinity)', () => showViewFor(record, 'connectivity')],
    ['Stratification', () => showViewFor(record, 'strata')],
    ['Annotations table', () => showViewFor(record, 'table')],
    ['Analyses', () => showViewFor(record, 'stats')],
  ]);
  submenu('Export', [
    ['Wavefront OBJ (.obj)', () => exportCell(record, 'obj')],
    ['STL (.stl)', () => exportCell(record, 'stl')],
    ['PLY (.ply)', () => exportCell(record, 'ply')],
    ['glTF binary (.glb)', () => exportCell(record, 'glb')],
    ['COLLADA (.dae)', () => exportCell(record, 'dae')],
    ['SWC skeleton (.swc)', () => exportCell(record, 'swc')],
    ['JSON (.json)', () => exportCell(record, 'json')],
    [`SWC of every loaded ${volumeLongName(record.source)} cell (.zip)`, () => exportLoadedSwc(record.source)],
  ]);
  // The wireframe as real geometry: OBJ/PLY carry the mesh edges as line
  // elements, STL turns each edge into a thin printable rod, and SWC is the
  // centerline skeleton -- the cell's own wireframe.
  submenu('Export wireframe', [
    ['Wavefront OBJ (.obj)', () => exportCell(record, 'obj', { wireframe: true })],
    ['PLY (.ply)', () => exportCell(record, 'ply', { wireframe: true })],
    ['STL rods (.stl)', () => exportCell(record, 'stl', { wireframe: true })],
    ['SWC skeleton (.swc)', () => exportCell(record, 'swc', { wireframe: true })],
    ['CSV edge table (.csv)', () => exportCell(record, 'csv', { wireframe: true })],
    ['TSV edge table (.tsv)', () => exportCell(record, 'tsv', { wireframe: true })],
    ['NumPy segments (.npy)', () => exportCell(record, 'npy', { wireframe: true })],
    ['NumPy arrays (.npz)', () => exportCell(record, 'npz', { wireframe: true })],
    ['HDF5 (.h5)', () => exportCell(record, 'h5', { wireframe: true })],
  ]);
  // The annotation wireframe as drawn: the panel's settings go with the
  // request, so the file holds the rings and lines on screen.
  if (normalizeDisplay(record.display).showAnnoWire) {
    submenu('Export annotation wireframe', [
      ['Wavefront OBJ (.obj)', () => exportAnnoWire(record, 'obj')],
      ['PLY (.ply)', () => exportAnnoWire(record, 'ply')],
      ['STL rods (.stl)', () => exportAnnoWire(record, 'stl')],
      ['CSV edge table (.csv)', () => exportAnnoWire(record, 'csv')],
      ['TSV edge table (.tsv)', () => exportAnnoWire(record, 'tsv')],
      ['NumPy segments (.npy)', () => exportAnnoWire(record, 'npy')],
      ['NumPy arrays (.npz)', () => exportAnnoWire(record, 'npz')],
      ['HDF5 (.h5)', () => exportAnnoWire(record, 'h5')],
    ]);
  }

  placeContextMenu(menu, at?.x ?? clientX, at?.y ?? clientY);
}

/**
 * A right click in a Render compare pane: the compared cell's menu.  A pane draws
 * the one cell as up to four layers and an annotations overlay, each under a key of
 * its own (`L3`, `A`), so whatever the click found, in any layer, is renamed to the
 * cell's, and the cell is listed once however many layers it was found in; nothing
 * under the pointer, no menu.
 */
function contextMenuForPane({ record, candidates = [], clientX, clientY }) {
  if (!record || !state.neurons.has(record.key)) return;
  const rows = listCandidates(candidates.map((c) => ({ ...c, key: record.key })));
  if (!rows.length) return;
  contextMenuFor({ key: record.key, clientX, clientY, pane: true, candidates: rows });
}

/**
 * The list at the top of the right-click menu: what is under the pointer, one row
 * each, named as the Select menu names them (volume included).  A row lights
 * its item up as the mouse passes over it, if the render can; the one the menu
 * is about is ticked.  Scrolls when there are many, so the menu's own items and
 * their submenus are never pushed off the screen by it.
 */
function underThePointer(rows, context, onPick, { light = true } = {}) {
  const heading = document.createElement('div');
  heading.className = 'ctx-heading';
  heading.textContent = light && renderer.selection?.kind === 'annotation'
    ? `Under the pointer ${MARKING}` : 'Under the pointer';
  const list = document.createElement('div');
  list.className = 'ctx-candidates';
  list.setAttribute('role', 'group');
  list.setAttribute('aria-label', heading.textContent);
  for (const row of rows) {
    const text = describeCandidate(row);
    const current = sameCandidate(row, context);
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'ctx-item ctx-cand';
    el.textContent = (current ? '✓ ' : '') + text;
    el.title = text;
    if (current) el.setAttribute('aria-current', 'true');
    if (light) {
      el.addEventListener('pointerenter', () => renderer.preview(
        SELECTABLE_KINDS.has(row.kind) ? row : null, previewSlot(row)));
    }
    el.addEventListener('click', () => onPick(row));
    list.append(el);
  }
  if (light) list.addEventListener('pointerleave', () => renderer.preview(null));
  return [heading, list];
}

function placeContextMenu(menu, clientX, clientY) {
  document.body.append(menu);
  // The list of what is under the pointer gives way before the menu's own items
  // do: on a short window it shrinks (and scrolls) to what is left, down to two
  // rows (it stopped at four, 96 px, and the menu's own items then ran 6 px past
  // the bottom of a 620 px window once they had Rotate about this point, 2.34.0).
  const list = menu.querySelector('.ctx-candidates');
  if (list) {
    const over = menu.getBoundingClientRect().height - (window.innerHeight - 16);
    if (over > 0) list.style.maxHeight = `${Math.max(48, list.getBoundingClientRect().height - over)}px`;
  }
  // Keep the menu on screen -- a tall one starts at the top, where its first
  // items are, rather than above the window where they cannot be reached.
  const { left, top } = placeInside({ x: clientX, y: clientY }, menu.getBoundingClientRect(),
    { width: window.innerWidth, height: window.innerHeight });
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

// -- selection (left triple click; a click in GraphApp) and the Z range ----

function hasZHide(record) {
  const d = normalizeDisplay(record.display);
  return d.zMin !== null || d.zMax !== null;
}

/** The selected annotation, when it can set a Z level for `record`. */
function selectedLevel(record) {
  const s = renderer.selection;
  if (!s || s.kind !== 'annotation' || !Number.isFinite(s.zUm)) return null;
  return state.neurons.get(s.key)?.source === record.source ? s : null;
}

/**
 * The Hide above / below items for `record`: above the red marker and below the
 * blue one, the yellow annotation standing in for a marker that is not set, and
 * the slab between them when they are two (markers.js `hideLevels`).  `item` is
 * the context menu's item maker; `whose` names the cell when the menu was not
 * opened on it.
 */
function zLevelItems(item, record, whose = '') {
  const level = selectedLevel(record);
  if (!level) return;
  const inVolume = (m) => (m && Number.isFinite(m.zUm)
    && state.neurons.get(m.key)?.source === record.source ? m : null);
  const { upper, lower, both } = hideLevels(level,
    { above: inVolume(renderer.markers.above), below: inVolume(renderer.markers.below) });
  const cell = whose ? `c${record.id}` : 'this cell';
  // With no marker set the yellow annotation is simply "annotation N", as it always was.
  const name = (m) => `${both ? SLOT_NAMES[slotOfItem(m)] : 'annotation'} ${m.locationId} (section ${m.section})`;
  if (both) {
    const dz = Math.abs(upper.zUm - lower.zUm);
    item(`Hide${whose} above ${SLOT_NAMES[slotOfItem(upper)]} and below ${SLOT_NAMES[slotOfItem(lower)]} `
      + `(keep ${dz.toFixed(2)} µm)`, () => hideOutside(record, upper, lower)).title =
      `Shows only the slab of ${cell} between annotation ${lower.locationId} and annotation ${upper.locationId}; `
      + 'nothing is re-rendered, and Restore full render brings it back';
  }
  item(`Hide${whose} above ${name(upper)}`, () => hideFrom(record, upper, 'above')).title =
    `Hides ${cell} above the ${SLOT_NAMES[slotOfItem(upper)]} annotation (the top of the upright view: `
    + 'higher sections unless Flip Z is on); the rest stays as rendered';
  item(`Hide${whose} below ${name(lower)}`, () => hideFrom(record, lower, 'below')).title =
    `Hides ${cell} below the ${SLOT_NAMES[slotOfItem(lower)]} annotation; with Hide above, leaves a slab`;
}

/** Show only the slab between two markers: one Z range, both ends at once. */
async function hideOutside(record, upper, lower) {
  const lo = Math.min(upper.zUm, lower.zUm);
  const hi = Math.max(upper.zUm, lower.zUm);
  await applyDisplay(record, { zMin: lo, zMax: hi });
  statusEl.textContent = `c${record.id} shown between annotations ${lower.locationId} and ${upper.locationId}: `
    + `${lo.toFixed(2)} to ${hi.toFixed(2)} µm in Z (${(hi - lo).toFixed(2)} µm)`;
  statusEl.className = 'status';
  renderNeuronList();
}

/**
 * "Above" is toward the top of the upright view, which is higher Z unless
 * Flip Z is on.  The range is stored in the volume's own microns, so the same
 * annotations stay hidden if Flip Z is switched afterwards.
 */
async function hideFrom(record, level, side) {
  const higher = (side === 'above') !== renderer.flipZ;
  await applyDisplay(record, higher ? { zMax: level.zUm } : { zMin: level.zUm });
  const d = normalizeDisplay(record.display);
  const part = d.zMin !== null && d.zMax !== null
    ? `shown between ${Math.min(d.zMin, d.zMax).toFixed(2)} and ${Math.max(d.zMin, d.zMax).toFixed(2)} µm in Z`
    : `hidden ${side} annotation ${level.locationId} (section ${level.section})`;
  statusEl.textContent = `c${record.id} ${part}`;
  statusEl.className = 'status';
  renderNeuronList();
}

async function restoreZ(record) {
  await applyDisplay(record, { zMin: null, zMax: null });
  statusEl.textContent = `c${record.id} restored to its full render`;
  statusEl.className = 'status';
  renderNeuronList();
}

/** What a candidate is, for the chooser and the status line.  An ID always
 * travels with its volume (ground rule 11). */
function describeCandidate(c) {
  return nameCandidate(c, namingOf(c.key));
}

/** How a cell is named in the words about it: `c1178` and its volume's name. */
function namingOf(key) {
  const record = state.neurons.get(key);
  return {
    cell: record ? `c${record.id}` : key,
    volume: record ? volumeLongName(record.source) : '',
  };
}

/** What the menus say an annotation row does while an annotation is selected. */
const MARKING = '(annotations: red above the yellow, blue below)';

/**
 * Where choosing `c` puts it while an annotation is selected (markers.js
 * `placeChoice`): an annotation of the same volume above the yellow one becomes
 * the red marker, one below the blue marker (2026-10-16; there was one light blue
 * "second" marker since 2026-09-22), one on the yellow's own section becomes the
 * yellow.  Null when `c` simply becomes the selection.
 */
function markerChoice(c) {
  const first = renderer.selection;
  if (!first || first.kind !== 'annotation' || c?.kind !== 'annotation') return null;
  if (state.neurons.get(first.key)?.source !== state.neurons.get(c.key)?.source) return null;
  return placeChoice(first, renderer.markers, c, renderer.flipZ);
}

/** The slot a menu row would be drawn in while the mouse is over it. */
function previewSlot(c) {
  const choice = markerChoice(c);
  return choice?.action === 'set' || choice?.action === 'activate' ? choice.slot : 'primary';
}

/** The slot an item is in: 'primary' (the yellow), 'above' (red), 'below' (blue), or null. */
function slotOfItem(item) {
  if (item && item === renderer.selection) return 'primary';
  if (item && item === renderer.markers.above) return 'above';
  if (item && item === renderer.markers.below) return 'below';
  return null;
}

/** Put a chosen candidate where it goes: the selection, or a marker. */
function choose(candidate) {
  const choice = markerChoice(candidate);
  if (!choice) { renderer.select(candidate); return; }
  const first = renderer.selection;
  if (choice.action === 'same') {
    renderer.pulse('primary');
    yellowActive();
    statusEl.textContent = `Annotation ${first.locationId} is the yellow one — ↑/↓ add a red marker above `
      + 'it or a blue one below';
    statusEl.className = 'status';
    return;
  }
  if (choice.action === 'activate') {
    markerMoved(choice.slot);
    renderer.pulse(choice.slot);
    statusEl.textContent = `Annotation ${candidate.locationId} is already the ${SLOT_NAMES[choice.slot]} marker`
      + ' — ↑/↓ step it';
    statusEl.className = 'status';
    return;
  }
  if (choice.action === 'anchor') {
    // On the yellow's own section: the reference moves within the section, and
    // the red and blue markers are still above and below it.
    renderer.select(candidate, { keepMarkers: true });
    statusEl.textContent = `Annotation ${candidate.locationId} is the yellow one now (it is on section `
      + `${candidate.section}, the yellow one's)`;
    statusEl.className = 'status';
    return;
  }
  renderer.setMarker(choice.slot, candidate);
  markerMoved(choice.slot);
  const record = state.neurons.get(candidate.key);
  if (record) showStepped(choice.slot, first, candidate, record);
}

/** One candidate is selected outright; several are offered in a chooser whose
 * rows light the item up as the mouse passes over them.  `look` 'graph': a
 * GraphApp click, which takes the nearest dot (markers.js `dotChoices`). */
function onSelectRequest({ candidates: under, clientX, clientY, look = 'render' }) {
  hideContextMenu();
  // The rings about as near as the nearest one, not its neighbors a few pixels off.  A
  // flattened cell has no annotation to choose: its Z is depth, not a section.
  const unflat = iplFlat ? under.filter((c) => !flatFor(state.neurons.get(c.key))) : under;
  const candidates = look === 'graph' ? dotChoices(unflat) : choosable(unflat);
  if (!candidates.length) {
    renderer.clearSelection();
    statusEl.textContent = under.length && !unflat.length
      ? 'No annotation can be chosen on the flattened IPL: its Z is depth, not a section (Display → Flatten IPL)'
      : 'Nothing selectable there (the surface never is)';
    statusEl.className = 'status';
    return;
  }
  if (candidates.length === 1) { choose(candidates[0]); return; }

  const menu = document.createElement('div');
  menu.id = 'ctx-menu';
  menu.className = 'ctx-menu';
  const title = document.createElement('div');
  title.className = 'ctx-title';
  title.textContent = renderer.selection?.kind === 'annotation' ? `Select ${MARKING}` : 'Select';
  menu.append(title);
  for (const candidate of candidates) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'ctx-item';
    el.textContent = describeCandidate(candidate);
    el.addEventListener('pointerenter', () => renderer.preview(candidate, previewSlot(candidate)));
    el.addEventListener('click', () => {
      hideContextMenu();
      choose(candidate);
      // The row that had the focus is gone; the render's keys work again.
      renderer.webgl.domElement.focus();
    });
    menu.append(el);
  }
  menu.addEventListener('pointerleave', () => renderer.preview(null));
  placeContextMenu(menu, clientX, clientY);
}

function onSelectionChange(selection) {
  renderMarkerTable();
  focusShifts();
  if (!selection) {
    activeMarker = null;
    statusEl.textContent = 'Selection cleared';
    statusEl.className = 'status';
    return;
  }
  // Short: the status line is one line wide.  What to do next is in the
  // right-click menu (Hide above / Hide below / Deselect) and the guide.
  statusEl.textContent = `Selected: ${describeCandidate(selection)} — `
    + (selection.kind === 'annotation' ? '↑/↓ mark above (red) or below (blue) in Z · ' : '') + 'Esc deselects';
  statusEl.className = 'status';
  // A skeleton-check problem names its anchor annotation the same way.
  if (selection.locationId) showAnnotationReadout(selection).catch(fail);
  // A Position shift slice follows the annotation to its section.
  shifts?.follow(selection);
}

// -- the red and blue markers, and the table of the three ---------------------

/** The marker moved or chosen last: the one the arrow keys move (markers.js `activeSlot`).
 *  The yellow itself when it was chosen or clicked last: the arrows then start a marker from it. */
let activeMarker = null;

/** The marker the arrows (`arrows`) or Esc act on: Esc never takes the yellow before a marker. */
function activeSlotNow({ arrows = false } = {}) {
  const m = renderer.markers;
  if (arrows && activeMarker && activeMarker === renderer.selection) return activeSlot(m, 'primary');
  return activeSlot(m, activeMarker && activeMarker === m.above ? 'above'
    : activeMarker && activeMarker === m.below ? 'below' : null);
}

/**
 * A left click landed on an annotation ring: on the yellow, the next arrow starts a marker
 * from it; on the red or the blue one, the arrows move that one.  Every click of a triple
 * click is such a click, which the choice at the third then confirms.
 */
function pickedRing({ key, locationId }) {
  if (locationId == null) return;
  const hit = (item) => item?.kind === 'annotation' && item.key === key && item.locationId === locationId;
  if (hit(renderer.selection)) { yellowActive(); return; }
  for (const slot of ['above', 'below']) {
    if (hit(renderer.markers[slot])) { activeMarker = renderer.markers[slot]; return; }
  }
  // Another ring of a z-slice: its slot's (2026-10-20).
  const ring = { kind: 'annotation', key, locationId };
  if (inSlice(renderer.slices.primary, ring)) { yellowActive(); return; }
  for (const slot of ['above', 'below']) {
    if (inSlice(renderer.slices[slot], ring)) { activeMarker = renderer.markers[slot]; return; }
  }
}

/** The yellow was chosen or clicked again: the next arrow starts a marker from it. */
function yellowActive() {
  const first = renderer.selection;
  if (first?.kind !== 'annotation') return;
  activeMarker = first;
  shifts?.follow(first);
}

/** The renderer added, moved or removed a marker (or Flip Z swapped the two). */
function onMarkerChange() {
  renderMarkerTable();
  focusShifts();
}

/**
 * The Position shift layer draws the chosen annotations' own arrows in their
 * markers' colors -- the yellow selection, the red marker above it, the blue
 * below -- and dims the rest (2026-10-19: "I don't know which arrow is the
 * movement of this annotation").
 */
function focusShifts() {
  if (!shifts || !renderer) return;
  const items = [];
  for (const slot of ['primary', 'above', 'below']) {
    const item = renderer.slotItem(slot);
    if (item?.kind === 'annotation' && item.locationId != null) {
      items.push({ slot, key: item.key, locationId: item.locationId });
    }
  }
  shifts.focus(items);
  // A chosen annotation's manual move is drawn in its marker's color, and the move
  // panel follows the marker the arrows act on.
  moves?.update();
}

/**
 * A marker was chosen or moved: it is the one the arrows move next, a Position
 * shift slice follows it, and the readout over the render names it -- the one
 * chosen last, whichever color (it used to go back to the yellow one, so a triple
 * click on another ring seemed to answer with the wrong Location ID and section).
 * `null`: a marker went, and the slice goes back to the yellow one.
 */
function markerMoved(slot) {
  const item = slot ? renderer.slotItem(slot) : null;
  if (item) activeMarker = item;
  shifts?.follow(item || renderer.selection);
  const named = item || renderer.selection;
  if (named?.locationId) showAnnotationReadout(named).catch(fail);
}

/** What the table's columns say, for their hover help. */
const MARKER_COLUMNS = [
  ['', 'The marker: yellow is the selected annotation, the reference; red is one above it, blue one '
    + 'below it (the top of the upright view: higher sections unless Flip Z is on). Click the dot to draw '
    + 'it again as when it was chosen; ▲ and ▼ step it to the next annotation up or down along the cell\'s '
    + 'links, as ↑ and ↓ do (Shift with them moves the yellow one).'],
  ['Cell', 'The cell the annotation belongs to.'],
  ['Location ID', 'Viking\'s ID of the annotation, in this volume (an ID means nothing in another). '
    + 'Click: copy it, for Viking → Annotation → Goto Location ID.'],
  ['Viking X Y Z', 'Where Viking has the annotation: X and Y in the volume\'s pixels, Z its section. ↗ shows it in Viking. '
    + '(a section correction is taken back, so this is Viking\'s place). Click: copy it with the downsample '
    + 'the view is at there, for Viking → Commands → Goto Location.'],
  ['Z', 'The section the annotation is on; "×24" when the marker holds every annotation on it (24 of '
    + 'them, of its cell or of every loaded cell: right-click, Select all annotations on z-slice), and the '
    + 'arrows step it a section at a time.'],
  ['ΔZ', 'Sections from the yellow annotation, as Viking counts them: plus above, minus below.'],
  ['ΔZ µm', 'The same in microns: the section thickness times the sections between.'],
  ['Dist µm', 'The straight-line distance from the yellow annotation in the volume, in microns: X, Y and Z '
    + 'together.'],
  ['Shift µm', 'How far Viking\'s section correction (or the pair the Position shift layer draws for the cell) '
    + 'moved the annotation, in X and Y: its true length, never the exaggerated one. With the layer on, the '
    + 'annotation\'s own arrow is drawn in its marker\'s color. Hover a number for X and Y.'],
];

/** Each row's annotation as Viking stores it: the cell, the node, and Viking's numbers for it. */
async function markerFacts(item) {
  const record = state.neurons.get(item.key);
  if (!record) return null;
  const nodes = await nodesFor(record);
  let node = item.locationId != null ? nodes.find((n) => n.id === item.locationId) : null;
  const point = Array.isArray(item.point) ? { x: item.point[0], y: item.point[1], z: item.point[2] } : null;
  // A branch, a wireframe or a synapse type names the annotation nearest its point.
  if (!node && point) {
    const best = nearestNode(nodes, point);
    if (best && best.d < NEAR_ANNOTATION_UM) node = best.node;
  }
  const at = node ? { x: node.xyz_um[0], y: node.xyz_um[1], z: node.xyz_um[2] } : point;
  if (!at) return { record, node: null, viking: null };
  // Viking's place: a section correction and a manual move are taken back, as the readout does.
  const viking = vikingFromUm(vikingPoint(node, at), pickScale(record));
  return { record, node, at, viking };
}

let markerTableToken = 0;

/**
 * The table under the readout: one row for each marker that is set -- red, yellow,
 * blue, top to bottom -- in its marker's color, with its cell, Location ID and
 * Viking place (a click copies either), its section, and the Z and distance from
 * the yellow one.  Shown whenever something is selected (2026-10-16: the Z
 * between the two showed only beside the yellow one's own readout, so mostly
 * while stepping with the arrows).
 */
async function renderMarkerTable() {
  const host = $('marker-table');
  if (!host) return;
  const token = ++markerTableToken;
  const rows = markerRows(renderer.selection, renderer.markers);
  if (!rows.length) {
    host.hidden = true;
    host.replaceChildren();
    return;
  }
  const facts = await Promise.all(rows.map((row) => markerFacts(row.item).catch(() => null)));
  if (token !== markerTableToken) return;      // changed again meanwhile: that draw wins

  const cell = (text, className = '') => {
    const td = document.createElement('td');
    if (className) td.className = className;
    td.textContent = text;
    return td;
  };
  const button = (text, className, title, onClick) => {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = className;
    el.textContent = text;
    el.title = title;
    el.addEventListener('click', (event) => {
      event.stopPropagation();
      onClick(el);
    });
    return el;
  };
  const head = document.createElement('tr');
  for (const [label, help] of MARKER_COLUMNS) {
    const th = document.createElement('th');
    th.textContent = label;
    th.dataset.help = help;
    head.append(th);
  }
  head.append(document.createElement('th'));
  const body = document.createElement('tbody');
  rows.forEach(({ slot, item, delta }, i) => {
    const f = facts[i];
    const name = SLOT_NAMES[slot];
    const color = renderer.markerColor(slot);
    const source = f?.record?.source ?? state.neurons.get(item.key)?.source ?? state.source;
    const where = volumeLongName(source);
    const tr = document.createElement('tr');
    tr.className = 'mk-row';
    tr.dataset.slot = slot;
    tr.style.setProperty('--mk', color);

    const lead = document.createElement('td');
    lead.className = 'mk-lead';
    lead.append(button('●', 'mk-dot', `The ${name} marker${slot === 'primary' ? ' (the selection)' : ''}: `
      + 'click to draw it again as when it was chosen, and to have ↑/↓ act on it'
      + (slot === 'primary' ? ' (from the yellow: a red marker above it or a blue one below)' : ''), () => {
      renderer.pulse(slot);
      if (slot !== 'primary') markerMoved(slot);
      else {
        yellowActive();
        if (item.locationId) showAnnotationReadout(item).catch(fail);
      }
    }));
    const steps = item.kind === 'annotation';
    const slice = renderer.slices[slot];
    for (const [arrow, direction, word] of [['▲', 1, 'up'], ['▼', -1, 'down']]) {
      const step = button(arrow, 'mk-step', `Step the ${name} marker ${word} to the next `
        + (slice ? `z-slice with annotations of ${slice.scope === 'cell' ? `c${f?.record?.id ?? ''}`
          : 'the loaded cells'}` : "annotation along the cell's links")
        + ` (${slot === 'primary' ? 'Shift+' : ''}${direction > 0 ? '↑' : '↓'})`, () => {
        stepAlongZ(direction, slot === 'primary' ? { primary: true } : { slot }).catch(fail);
      });
      step.disabled = !steps;
      lead.append(step);
    }
    const grab = button('✥', 'mk-step mk-move', `Move the ${name} annotation by hand (g): ←/→ along an axis, `
      + '1–7 choose the axis, Enter applies, Esc done', () => {
      if (slot === 'primary') yellowActive(); else markerMoved(slot);
      moves?.start();
    });
    grab.disabled = !steps;
    lead.append(grab);
    tr.append(lead);

    tr.append(cell(f?.record ? `c${f.record.id}` : '—', 'mk-cell'));
    tr.title = `${name[0].toUpperCase()}${name.slice(1)}: ${describeCandidate(item)}`;

    const id = item.locationId ?? f?.node?.id ?? null;
    const idCell = document.createElement('td');
    if (id != null) {
      idCell.append(button(String(id), 'mk-copy', `Copy ${id}, a Location ID of ${where}, for Viking → `
        + 'Annotation → Goto Location ID' + (item.locationId == null ? ' (the annotation nearest the point)' : ''),
      (el) => copyMarkerField(el, String(id), `the ${name} marker's Location ID`, 'location', source)));
    } else {
      idCell.textContent = '—';
    }
    tr.append(idCell);

    const vikingCell = document.createElement('td');
    if (f?.viking) {
      vikingCell.append(button(f.viking.join(' '), 'mk-copy', `Copy “${f.viking.join(' ')} downsample”, `
        + `a place in ${where}, for Viking → Commands → Goto Location`, (el) => {
        const at = f.at;
        const ds = formatDownsample(downsampleAt(at, pickScale(f.record), source));
        copyMarkerField(el, `${f.viking.join(' ')} ${ds}`, `the ${name} marker's place`, 'viking', source);
      }));
    } else {
      vikingCell.textContent = '—';
    }
    // Show in Viking (2026-10-20): Viking at this annotation, as the right-click menu's Open in Viking.
    if (id != null && f?.record && state.volumes.find((v) => v.name === source)?.viking_name) {
      const show = button('↗', 'mk-viking', `Show in Viking: the ${name} annotation, ${id}, in ${where} `
        + '(the first open of a session may ask which Viking)', () => openLocationInViking(f.record, id));
      show.setAttribute('aria-label', `Show the ${name} annotation in Viking`);
      vikingCell.append(show);
    }
    tr.append(vikingCell);

    const section = Number.isFinite(item.section) ? item.section : (f?.node?.z_section ?? f?.viking?.[2]);
    const zCell = cell(Number.isFinite(section) ? String(section) + (slice ? ` ×${slice.items.length}` : '') : '—',
      'mk-num');
    if (slice) zCell.title = `${sliceWords(slot, slice)} (${SLICE_SCOPES[slice.scope]}), drawn ${name}`;
    tr.append(zCell);
    tr.append(cell(delta?.dz != null ? signed(delta.dz) : '—', 'mk-num'));
    tr.append(cell(delta ? signed(delta.dzUm, 2) : '—', 'mk-num'));
    tr.append(cell(delta?.distUm != null ? delta.distUm.toFixed(2) : '—', 'mk-num'));
    const shown = f?.record ? shifts?.layerOf(f.record) : null;
    const moved = f?.node ? pickShiftText(f.node, shown && !shown.empty ? shown.pair : null) : null;
    const shiftCell = cell(moved ? moved.length.toFixed(2) : '—', 'mk-num');
    if (moved) shiftCell.title = `${moved.text.replace(/^shift /, '')}: ${moved.title}`;
    tr.append(shiftCell);

    const close = document.createElement('td');
    close.append(button('✕', 'mk-close', slot === 'primary'
      ? 'Deselect (the markers go with it) — also Esc, after the markers'
      : `Remove the ${name} marker — also Esc`, () => {
      if (slot === 'primary') { renderer.clearSelection(); return; }
      renderer.setMarker(slot, null);
      markerMoved(null);
    }));
    tr.append(close);
    body.append(tr);
  });
  const table = document.createElement('table');
  table.className = 'mk-table';
  const thead = document.createElement('thead');
  thead.append(head);
  table.append(thead, body);
  host.replaceChildren(table);
  host.hidden = false;
}

/** Put a marker's Location ID or Viking place on the clipboard, and say so with its volume. */
function copyMarkerField(button, text, what, kind, source) {
  // Clipboard access is denied in some contexts; the text is on screen either way.
  try { navigator.clipboard?.writeText(text).catch(() => {}); } catch { /* shown anyway */ }
  button.classList.add('copied');
  setTimeout(() => button.classList.remove('copied'), 1200);
  statusEl.textContent = `Copied “${text}”, ${what} — in Viking, open ${volumeLongName(source)} and paste via `
    + (kind === 'viking' ? 'Commands → Goto Location' : 'Annotation → Goto Location ID');
  statusEl.className = 'status';
}

/**
 * The next annotation up or down Z from `fromId`, along the cell's links.
 *
 * Walks Viking's links breadth first, never back past the starting section,
 * so a process that runs level for a while before it climbs is still
 * followed; the first annotation found beyond the start wins, the nearest
 * section first and then the nearest in XY (the straighter way on at a
 * branch).  Null at the end of what is traced.
 */
function nextAlongZ(fromId, sign, byId, adjacency) {
  const start = byId.get(fromId);
  if (!start) return null;
  const z0 = start.z_section * sign;
  const xy = (n) => Math.hypot(n.xyz_um[0] - start.xyz_um[0], n.xyz_um[1] - start.xyz_um[1]);
  const seen = new Set([fromId]);
  let frontier = [fromId];
  while (frontier.length && seen.size < 50000) {
    const found = [];
    const level = [];
    for (const id of frontier) {
      for (const next of adjacency.get(id) || []) {
        if (seen.has(next)) continue;
        seen.add(next);
        const node = byId.get(next);
        if (!node) continue;
        const z = node.z_section * sign;
        if (z > z0) found.push(node);
        else if (z === z0) level.push(next);
      }
    }
    if (found.length) {
      found.sort((a, b) => (a.z_section - b.z_section) * sign || xy(a) - xy(b));
      return found[0];
    }
    frontier = level;
  }
  return null;
}

async function adjacencyFor(record) {
  let adjacency = edgesCache.get(record.key);
  if (!adjacency) {
    const [nodes, edges] = await Promise.all([nodesFor(record), api.edges(record.source, record.id)]);
    const ids = new Set(nodes.map((n) => n.id));
    adjacency = new Map();
    const link = (a, b) => {
      if (!adjacency.has(a)) adjacency.set(a, []);
      adjacency.get(a).push(b);
    };
    for (const { a, b } of edges) {
      if (a === b || !ids.has(a) || !ids.has(b)) continue;
      link(a, b);
      link(b, a);
    }
    edgesCache.set(record.key, adjacency);
  }
  return adjacency;
}

/** An annotation node as a selection candidate, the shape `candidatesAt` gives. */
function annotationCandidate(record, node) {
  return {
    kind: 'annotation', key: record.key, px: 0, depth: 0, through: true,
    locationId: node.id, section: node.z_section, zUm: node.xyz_um[2], point: [...node.xyz_um],
    radiusUm: node.radius_um,
  };
}

let stepping = null;

/**
 * Up/Down with an annotation selected, or a row's ▲ and ▼: step a marker to the
 * next annotation above or below along the cell's links.  `primary` (Shift) moves
 * the yellow one; `slot` a given marker (its row's buttons); otherwise the marker
 * moved last, or with none a new one, red above the yellow or blue below it.  "Above" is
 * the top of the upright view, as for Hide above: higher Z unless Flip Z is on.
 * A marker stepped onto the yellow one goes; one stepped past it changes color
 * when the other side is free, and stops when it is not (markers.js).  The camera
 * stays where it is, and each step draws the marker as just chosen.
 */
async function stepAlongZ(direction, { primary = false, slot = null } = {}) {
  if (stepping) return;                 // a held key queues nothing
  const first = renderer.selection;
  if (first?.kind !== 'annotation') return;
  const moving = primary ? 'primary' : (slot && renderer.markers[slot] ? slot : activeSlotNow({ arrows: true }));
  const from = moving === 'primary' ? first : (moving ? renderer.markers[moving] : first);
  const record = state.neurons.get(from.key);
  if (!record) return;
  // A whole z-slice steps a section at a time, not along the links (a new marker from
  // the yellow's z-slice is a z-slice too).
  const sliced = renderer.slices[moving || 'primary'];
  if (sliced) {
    stepping = stepSlice(direction, moving, sliced);
    try { await stepping; } finally { stepping = null; }
    return;
  }
  stepping = (async () => {
    const [nodes, adjacency] = await Promise.all([nodesFor(record), adjacencyFor(record)]);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const sign = direction * (renderer.flipZ ? -1 : 1);
    const word = direction > 0 ? 'above' : 'below';
    if (renderer.selection !== first) return;       // deselected meanwhile
    const end = (at) => flareStatus(`No annotation of c${record.id} ${word} Location ${at.locationId} `
      + `(section ${at.section}) along its links — the end of what is traced there`);
    let next = nextAlongZ(from.locationId, sign, byId, adjacency);
    if (!next) { end(from); return; }

    if (moving === 'primary') {
      const candidate = annotationCandidate(record, next);
      const r = stepAnchor(first, renderer.markers, candidate, renderer.flipZ);
      if (r.action === 'refuse') { flareStatus(r.reason); return; }
      const keep = activeMarker;
      renderer.select(candidate, { keepMarkers: true });
      // Markers the move put on the yellow's other side change color; one it landed on goes.
      for (const s2 of ['above', 'below']) {
        if (renderer.markers[s2] !== r.markers[s2]) renderer.setMarker(s2, r.markers[s2], { pulse: false });
      }
      activeMarker = keep && slotOfItem(keep) ? keep : null;
      if (r.merged) {
        statusEl.textContent = `The yellow annotation is on the ${SLOT_NAMES[r.merged]} marker's: `
          + `Location ${next.id}, section ${next.z_section}`;
        statusEl.className = 'status';
      }
      return;
    }

    // A red or blue marker, or a new one from the yellow.
    for (let hops = 0; hops < 64; hops += 1) {
      const candidate = annotationCandidate(record, next);
      if (!moving) {
        const side = sideOf(first, candidate, renderer.flipZ);
        if (side) {
          const replaced = renderer.markers[side];
          renderer.setMarker(side, candidate);
          markerMoved(side);
          showStepped(side, first, candidate, record);
          if (replaced && replaced.locationId !== candidate.locationId) {
            statusEl.textContent += ` (in place of Location ${replaced.locationId})`;
          }
          return;
        }
      } else {
        const r = stepMarker(first, renderer.markers, moving, candidate, renderer.flipZ);
        if (r.action === 'refuse') { flareStatus(r.reason); return; }
        if (r.action === 'merge') {
          renderer.setMarker(moving, null);
          markerMoved(null);
          statusEl.textContent = `The ${SLOT_NAMES[moving]} marker met the yellow annotation `
            + `${first.locationId} and went`;
          statusEl.className = 'status';
          return;
        }
        if (r.action === 'move' || r.action === 'switch') {
          if (r.action === 'switch') renderer.setMarker(r.from, null);
          const to = r.action === 'switch' ? r.to : moving;
          renderer.setMarker(to, candidate);
          markerMoved(to);
          showStepped(to, first, candidate, record, r.action === 'switch' ? r.from : null);
          return;
        }
      }
      // On the yellow's own section but not the yellow: step on past it.
      const past = nextAlongZ(next.id, sign, byId, adjacency);
      if (!past) { end(annotationCandidate(record, next)); return; }
      next = past;
    }
  })();
  try { await stepping; } finally { stepping = null; }
}

/** The status line after a step: "Red marker: Location … — Z 1231 → 1234 · ΔZ +0.21 µm · +3 sec". */
function showStepped(slot, first, candidate, record, was = null) {
  const readout = markerReadoutText(markerReadout(first, candidate));
  const name = SLOT_NAMES[slot];
  statusEl.textContent = `${name[0].toUpperCase()}${name.slice(1)} marker: Location ${candidate.locationId} `
    + `of c${record.id} — ${readout || `section ${candidate.section}`}`
    + (was ? ` (it passed the yellow one: it was ${SLOT_NAMES[was]})` : '');
  statusEl.className = 'status';
}

// -- a whole z-slice with the yellow, red or blue one (2026-10-20) -------------------
// "Select all annotations on z-slice", of the one cell or of every loaded cell of the
// volume: the rest drawn in the slot's color, and the arrows step it a section at a time.

/**
 * The slot the right-click menu's z-slice items act on: the one whose annotation, or
 * z-slice, the menu is about; else the one the arrow keys act on; else the yellow.
 */
function menuSlot(context) {
  if (context?.kind === 'annotation') {
    for (const slot of SLOTS) {
      if (sameAnnotation(renderer.slotItem(slot), context) || inSlice(renderer.slices[slot], context)) return slot;
    }
  }
  return activeSlotNow({ arrows: true }) || 'primary';
}

/**
 * The right-click menu's "Select all annotations on z-slice" items, while an annotation is
 * selected: for one slot (`menuSlot`), its own cell's and every loaded cell's.  The one in
 * force is ticked, and choosing it again puts the slot back to its one annotation.
 */
function zSliceItems(item, context) {
  if (renderer.selection?.kind !== 'annotation') return;
  const slot = menuSlot(context);
  const main = renderer.slotItem(slot);
  if (main?.kind !== 'annotation' || !Number.isFinite(main.section)) return;
  const now = renderer.slices[slot];
  const name = SLOT_NAMES[slot];
  // With a marker set, which of the three it is about.
  const which = renderer.markers.above || renderer.markers.below ? name : '';
  const { cell, volume } = namingOf(main.key);
  for (const scope of ['cell', 'volume']) {
    const on = now?.scope === scope;
    const note = [which, scope === 'cell' ? SLICE_SCOPES.cell : ''].filter(Boolean).join(', ');
    const whose = scope === 'cell' ? cell : `every loaded cell of ${volume}`;
    item(`Select all annotations on z-slice ${main.section}${note ? ` (${note})` : ''}`,
      () => (on ? dropSlice(slot) : takeSlice(slot, scope)), { checked: on }).title = on
      ? `Back to the ${name} annotation ${main.locationId} alone — also Esc`
      : `Every annotation of ${whose} on section ${main.section}, drawn ${name} with annotation `
        + `${main.locationId}; ↑/↓ then step it a section at a time, to the next one with annotations `
        + `of ${whose}, not along the links`;
  }
}

/** The cells a z-slice reads: its own, or every loaded cell of its volume that is shown. */
function sliceRecords(slice) {
  if (slice.scope === 'cell') {
    const own = state.neurons.get(slice.key);
    return own ? [own] : [];
  }
  return [...state.neurons.values()].filter((r) => r.source === slice.source
    && (r.visible !== false || r.key === slice.key));
}

/** Every annotation of a z-slice's cells, as candidates (`annotationCandidate`). */
async function sliceNodes(slice) {
  const records = sliceRecords(slice);
  const lists = await Promise.all(records.map((r) => nodesFor(r).catch(() => [])));
  return records.flatMap((r, i) => lists[i]
    .filter((n) => Number.isFinite(n.z_section) && Array.isArray(n.xyz_um))
    .map((n) => annotationCandidate(r, n)));
}

/** "Red z-slice 1234: 24 annotations of c1178" for the status line. */
function sliceWords(slot, slice) {
  const name = SLOT_NAMES[slot];
  const n = slice.items.length;
  const cells = new Set(slice.items.map((c) => c.key)).size;
  const whose = slice.scope === 'cell' ? namingOf(slice.key).cell
    : `${cells} cell${cells === 1 ? '' : 's'} of ${volumeLongName(slice.source)}`;
  return `${name[0].toUpperCase()}${name.slice(1)} z-slice ${slice.section}: `
    + `${n} annotation${n === 1 ? '' : 's'} of ${whose}`;
}

/** Give `slot` every annotation on its section, of its cell (`scope` 'cell') or of the volume's. */
async function takeSlice(slot, scope) {
  const main = renderer.slotItem(slot);
  const record = main ? state.neurons.get(main.key) : null;
  if (main?.kind !== 'annotation' || !record) return;
  const slice = { scope, key: main.key, source: record.source, section: main.section, items: [] };
  const all = await sliceNodes(slice);
  if (renderer.slotItem(slot) !== main) return;          // chosen or stepped again meanwhile
  slice.items = all.filter((c) => c.section === main.section);
  if (!inSlice(slice, main)) slice.items.push(main);
  renderer.setSlice(slot, slice);
  // The arrows act on it: from the yellow's, a new z-slice above (red) or below (blue).
  if (slot === 'primary') yellowActive(); else markerMoved(slot);
  statusEl.textContent = `${sliceWords(slot, slice)} — ↑/↓ `
    + (slot === 'primary' ? 'mark the next z-slice above (red) or below (blue), Shift+↑/↓ move this one'
      : 'step it a section at a time')
    + ', not along the links';
  statusEl.className = 'status';
}

/** Put `slot` back to its one annotation. */
function dropSlice(slot) {
  const main = renderer.slotItem(slot);
  if (!renderer.slices[slot]) return;
  renderer.setSlice(slot, null);
  statusEl.textContent = `The ${SLOT_NAMES[slot]} one is annotation ${main?.locationId} alone again`;
  statusEl.className = 'status';
}

/** Read a z-slice again for its slot's annotation as it is now (a cell drawn again). */
async function retakeSlice(slot, slice) {
  const main = renderer.slotItem(slot);
  if (!main) return;
  const all = await sliceNodes(slice);
  if (renderer.slotItem(slot) !== main) return;
  const items = all.filter((c) => c.section === main.section);
  if (!items.some((c) => sameAnnotation(c, main))) items.push(main);
  renderer.setSlice(slot, { ...slice, section: main.section, items });
}

/**
 * The arrows on a slot that holds a whole z-slice (`moving` as in `stepAlongZ`: 'primary',
 * a marker's slot, or null for a new marker from the yellow's z-slice): the next section up
 * or down with annotations in its scope, all of them, the one nearest the slot's own
 * annotation (markers.js `leadOf`) carrying its readout.  Slices meet and do not pass
 * (markers.js `sliceStep`): a marker that reaches the yellow's section goes, and the yellow
 * that reaches a marker's takes its annotation.
 */
async function stepSlice(direction, moving, slice) {
  const first = renderer.selection;
  const all = await sliceNodes(slice);
  if (renderer.selection !== first || renderer.slices[moving || 'primary'] !== slice) return;
  const from = renderer.slotItem(moving || 'primary');
  const at = {
    primary: first.section,
    above: renderer.markers.above?.section ?? null,
    below: renderer.markers.below?.section ?? null,
  };
  const sign = direction * (renderer.flipZ ? -1 : 1);
  const r = sliceStep(new Set(all.map((c) => c.section)), at, moving, sign, renderer.flipZ);
  const name = SLOT_NAMES[moving || 'primary'];
  if (r.action === 'end') {
    const whose = slice.scope === 'cell' ? namingOf(slice.key).cell : `any loaded ${volumeLongName(slice.source)} cell`;
    flareStatus(`No annotation of ${whose} ${direction > 0 ? 'above' : 'below'} z-slice ${from.section} `
      + `(the ${name} one) — the end of what is traced there`);
    return;
  }
  const next = { ...slice, section: r.section, items: all.filter((c) => c.section === r.section) };
  const say = (slot, lead) => {
    const record = state.neurons.get(lead.key);
    if (record) showStepped(slot, renderer.selection, lead, record);
    statusEl.textContent += ` · ${next.items.length} on z-slice ${r.section}`;
  };

  if (moving === 'primary') {
    // The yellow's z-slice reached a marker's: it takes that marker's annotation, and the marker goes.
    const lead = r.action === 'merge' ? renderer.markers[r.into] : leadOf(next.items, from);
    const keep = activeMarker;
    renderer.select(lead, { keepMarkers: true, slice: next });
    if (r.action === 'merge') renderer.setMarker(r.into, null, { pulse: false });
    activeMarker = keep && slotOfItem(keep) ? keep : null;
    statusEl.textContent = r.action === 'merge'
      ? `The yellow z-slice met the ${SLOT_NAMES[r.into]} one at section ${r.section}: they are one`
      : `${sliceWords('primary', next)} (Location ${lead.locationId} the yellow one)`;
    statusEl.className = 'status';
    return;
  }
  if (r.action === 'merge') {
    renderer.setMarker(moving, null);
    markerMoved(null);
    statusEl.textContent = `The ${name} z-slice met the yellow one at section ${r.section} and went`;
    statusEl.className = 'status';
    return;
  }
  // A marker's z-slice a section on, or a new one from the yellow's, in place of the one there.
  const lead = leadOf(next.items, from);
  const replaced = moving ? null : renderer.markers[r.slot];
  renderer.setMarker(r.slot, lead, { slice: next });
  markerMoved(r.slot);
  say(r.slot, lead);
  if (replaced && !sameAnnotation(replaced, lead)) {
    statusEl.textContent += ` (in place of Location ${replaced.locationId})`;
  }
}

/** Whether `item` is still the selection or one of its markers. */
const isMarked = (item) => !!item && slotOfItem(item) !== null;

/**
 * The pick readout names the annotation chosen last: the selection, or a marker.
 *
 * Every click of a triple click is an ordinary pick too, so the readout was
 * left on the annotation nearest the clicked surface point -- within 5 um,
 * often a neighbor of the one selected, and unchanged when the choice came
 * from the Select menu or the ring had no surface behind it ("does the
 * Location ID ... show that ID?", 2026-09-19: not reliably).  The position is
 * the annotation's own, as Viking stores it, so the Viking X Y Z and the `c`
 * copy go to it -- with its own correction taken back, not that of a wider disc
 * on the same section that also holds the point.
 */
async function showAnnotationReadout(item) {
  const record = state.neurons.get(item.key);
  if (!record) return;
  const node = (await nodesFor(record)).find((n) => n.id === item.locationId);
  const [x, y, z] = node?.xyz_um || item.point;
  if (!isMarked(item)) return;    // deselected or replaced meanwhile
  await showPick({ point: { x, y, z }, key: item.key });
  if (!isMarked(item) || !lastPick
      || lastPick.point.x !== x || lastPick.point.y !== y || lastPick.point.z !== z) return;
  lastPick.locationId = item.locationId;
  lastPick.locationSource = record.source;
  lastPick.viking = vikingFromUm(vikingPoint(node, { x, y, z }), lastPick.scale);
  $('pick-viking').textContent = `Viking ${vikingText(lastPick)}`;
  showPickShift(node, record);
  const name = SLOT_NAMES[slotOfItem(item)] ?? 'selected';
  const field = $('pick-location');
  writeLocationField(field, item.locationId, record.source);
  field.title = `The ${name} annotation, ${item.locationId} of c${record.id} in `
    + `${volumeLongName(record.source)}, section ${item.section}. `
    + 'Click: copy the ID for Viking → Annotation → Goto Location ID. '
    + 'Right-click: type or paste a Location ID, Enter takes the view there.';
}

// The app's SWC types every neurite node dendrite (3). The MATLAB exporter's
// 5 (branch point) and 6 (end point) are read by today's SWC tools as custom
// types, and "what are types 5 and 6?" was the question they raised
// (2026-09-17). The API keeps the MATLAB typing unless told otherwise.
const SWC_APP_OPTIONS = { swc_branch_types: false };
const SWC_HINT = 'Skeleton as SWC: 1 soma, 3 dendrite, in microns; the header lists the codes. '
  + 'Add swc_branch_types=true to the link for the MATLAB typing (5 branch points, 6 end points).';

/**
 * Save a file the server builds, and say how it went.  A bare `<a download>`
 * click showed nothing when the server refused (a Closed curve raster over its
 * voxel limit, an empty mesh, a format it cannot write) and saved the error
 * as the file in some browsers; the file is fetched first, so a refusal is
 * read and shown, in the server's words.  Resolves with the download (its
 * `archive` says how many skeletons a bulk archive holds) when the file was
 * saved, and false when it was not.  A bulk archive the server cut short is
 * saved, and says so in the error color: it is a 200 and a zip like a whole one.
 */
async function saveExport(path, filename) {
  statusEl.textContent = `Exporting ${filename}…`;
  statusEl.className = 'status busy';
  try {
    const done = await api.download(path, filename, { label: `exporting ${filename}…` });
    const notice = savedNotice(filename, done.bytes, done.archive);
    statusEl.textContent = notice.text;
    statusEl.className = notice.partial ? 'status error' : 'status';
    return done;
  } catch (error) {
    console.error(error);
    const why = error instanceof ApiError ? error.detail : String(error.message || error);
    const finest = FINEST_PIXEL.exec(why)?.[4];
    statusEl.textContent = `Export of ${filename} failed — ${why}`
      + (finest ? `. Set Voxel size to ${finest} µm or coarser in the Render panel and press Apply.` : '');
    statusEl.className = 'status error';
    return false;
  }
}

/**
 * One archive per way of drawing, each an SWC per loaded cell of a volume and
 * a manifest of what was written.  An archive draws every cell the same way
 * (Registration and section correction are the route's, not per cell), so
 * cells this tab draws differently go in separate archives; and the route takes
 * at most 200 cells, so a larger volume goes in pieces.
 */
async function exportLoadedSwc(source) {
  const records = [...state.neurons.values()].filter((r) => r.source === source);
  const plan = planBulkSwc(records);
  if (!plan.length) return;
  const name = volumeLongName(source);
  const reports = [];
  for (const [i, piece] of plan.entries()) {
    const filename = plan.length > 1 ? `${source}-swc-${i + 1}.zip` : `${source}-swc.zip`;
    statusEl.textContent = `Exporting ${piece.ids.length} skeleton(s) of ${name} as ${filename}`
      + (plan.length > 1 ? ` (archive ${i + 1} of ${plan.length})` : '') + '…';
    statusEl.className = 'status busy';
    const url = api.bulkSwcUrl(source, piece.ids, {
      pattern: 'c{id}', ...SWC_APP_OPTIONS,
      live_registration: piece.registration, section_corrections: piece.corrections,
    });
    const saved = await saveExport(url, filename);
    if (!saved) return;
    reports.push(saved.archive ?? null);
  }
  // One archive keeps the line its own save wrote; several are summed, so a short
  // one is not buried under the next one's "Exporting...".
  const summary = bulkSwcSummary({ name, cells: records.length, archives: plan.length, reports });
  if (summary) {
    statusEl.textContent = summary.text;
    statusEl.className = summary.partial ? 'status error' : 'status';
  }
}

/** The file one cell's export link names, and its name: from the cell as it is now. */
function exportRequest(record, fmt, extra = {}) {
  const live = state.neurons.get(record.key) ?? record;
  // A surface or its wireframe is written as drawn, flattened too; an SWC or the
  // JSON carries the annotations where Viking has them.
  const flat = flatFor(live) && fmt !== 'swc' && fmt !== 'json';
  const t = flat ? (live.flatThickness ?? flatThicknessOf.get(live.source)) : null;
  const tag = flat ? `-ipl-flat${t != null ? `-${Number(t.toFixed(4))}um` : ''}` : '';
  return {
    url: api.exportUrl(live.source, live.id, fmt,
      { ...exportOptions(live, flat), ...(fmt === 'swc' ? SWC_APP_OPTIONS : {}), ...extra }),
    filename: extra.wireframe ? `c${live.id}-wireframe${tag}.${fmt}` : `c${live.id}${tag}.${fmt}`,
  };
}

function exportCell(record, fmt, extra = {}) {
  const { url, filename } = exportRequest(record, fmt, extra);
  return saveExport(url, filename);
}

function exportAnnoWire(record, fmt) {
  const live = state.neurons.get(record.key) ?? record;
  return saveExport(
    api.annotationWireExportUrl(live.source, live.id, fmt,
      annoWireParams(normalizeDisplay(live.display))),
    `c${live.id}-annotation-wire.${fmt}`);
}

/** The options of the mesh on screen, so exports match it (see exportQuery); `flat`: flattened as drawn. */
function exportOptions(record, flat = false) {
  return exportQuery(record.render, currentIplMethod(), flat);
}

document.addEventListener('pointerdown', (event) => {
  if (!event.target.closest('#ctx-menu')) hideContextMenu();
});
// `m` resets the view wherever the focus is, not only once the canvas has been
// clicked (it said nothing after a load, with the focus on Load: 2026-10-21), and
// `p` sets or lets go of the pivot point (after a click on the Pivot box).  Not
// from a box being typed in, a dropdown (which takes letters to pick an entry), or
// with a modifier; the canvas takes its own keys.
document.addEventListener('keydown', (event) => {
  if (!['m', 'p', 'P'].includes(event.key) || event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;
  if (state.view !== 'render' || !renderer || event.defaultPrevented) return;
  const target = event.target;
  if (target === renderer.webgl?.domElement || isTypingTarget(target)) return;
  if (target?.closest?.('select, [role="listbox"], [role="combobox"], [role="menu"], dialog')) return;
  renderer.handleKey(event);
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  // One Esc per thing: an open menu first, a click waited for by the Pivot box,
  // then the selection, then the pivot point.  Handled here
  // rather than on the render canvas because choosing from the Select menu
  // leaves the keyboard focus on a button that no longer exists, and Esc
  // then never reached the canvas (2026-09-19: "how does one deselect it?").
  if (document.getElementById('ctx-menu')) { hideContextMenu(); return; }
  if (state.view === 'render' && renderer?.pivotArmed) { renderer.clearPivot('off'); return; }
  if (state.view !== 'render' || !renderer) return;
  if (!renderer.selection) {
    if (renderer.pivotPoint() && !event.target.closest?.('input, textarea, select, [contenteditable]')) {
      renderer.clearPivot('off');
    }
    return;
  }
  if (event.target.closest?.('input, textarea, select, [contenteditable]')) return;
  // The markers first, the one moved last first; then the selection.  A z-slice
  // goes before its annotation does.
  const slot = activeSlotNow();
  if (slot) {
    if (renderer.slices[slot]) { dropSlice(slot); return; }
    renderer.setMarker(slot, null);
    markerMoved(null);
    return;
  }
  if (renderer.slices.primary) { dropSlice('primary'); return; }
  renderer.clearSelection();
});

function cellPanel(record) {
  const panel = document.createElement('div');
  panel.className = 'cell-panel';
  // Clicks inside the panel must not bubble to the row and collapse it.
  panel.addEventListener('click', (event) => event.stopPropagation());
  const display = normalizeDisplay(record.display);
  const volume = state.volumes.find((v) => v.name === record.source);
  const hasBoundary = volumeHasBoundary(record.source);

  // A tiny helper: every drawable feature opens with its own checkbox, and
  // the customization controls appear only while it is on.
  const showToggle = (body, label, checked, onChange, title) => {
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = checked;
    const lab = document.createElement('label');
    lab.className = 'check full';
    lab.append(box, document.createTextNode(` ${label}`));
    if (title) setHelp(lab, title);
    body.append(lab);
    box.addEventListener('change', () => onChange(box.checked));
    return box;
  };

  // -- surface ---------------------------------------------------------------
  section(panel, record, 'surface', 'Surface',
    display.showSurface
      ? (record.render.color_by_strata ? 'colored by IPL depth' : 'on') : 'off',
    (body) => {
      showToggle(body, 'Show surface', display.showSurface, (on) => {
        setDisplay(record.key, { showSurface: on });
        renderer.setSurface(record.key, on);
        persist();
        renderNeuronList();
      });
      if (!display.showSurface) return;

      const strata = document.createElement('input');
      strata.type = 'checkbox';
      strata.checked = !!record.render.color_by_strata && hasBoundary;
      strata.disabled = !hasBoundary;
      const strataLabel = document.createElement('label');
      strataLabel.className = `check full${hasBoundary ? '' : ' disabled'}`;
      strataLabel.append(strata, document.createTextNode(' Color by IPL depth'));
      strataLabel.title = hasBoundary
        ? 'Shade each vertex by stratification depth'
        : `${volume ? volume.display_name : record.source} has no IPL boundary markers`;
      body.append(strataLabel);
      if (!hasBoundary) {
        const note = document.createElement('p');
        note.className = 'note';
        note.textContent = 'No IPL boundary markers in this volume.';
        body.append(note);
      }
      strata.addEventListener('change', async () => {
        setRenderOptions(record.key, { ...record.render, color_by_strata: strata.checked });
        persist();
        await rebuildOne(record);
        renderNeuronList();
      });

      if (!strata.checked) {
        const color = document.createElement('input');
        color.type = 'color';
        color.value = record.color;
        color.addEventListener('input', () => {
          setColor(record.key, color.value);
          // This cell's row, which is not always the focused one (the focus
          // moves with a click in the Analyses tab, the open panel does not).
          const dot = $('neuron-list').querySelector(`.neuron-item[data-key="${record.key}"] .row-dot`);
          if (dot) dot.style.background = color.value;
        });
        field(body, 'Color', color);
      }

      slider(body, 'Opacity', {
        key: `${record.key}:opacity`, min: 0.05, max: 1, step: 0.01,
        value: record.opacity, decimals: 2,
        onLive: (v) => {
          record.opacity = v;
          renderer.setStyle(record.key, { opacity: v });
        },
        onSettle: () => persist(),
      });

      // The finish: a name from the shared menu, applied to this cell, with
      // shortcuts to hand it to the selection or to every cell.
      const current = resolveMaterialName(display.material);
      const finish = document.createElement('select');
      finish.replaceChildren(...materialNames().map((n) => {
        const option = new Option(isPresetMaterial(n) ? n : `${n} ★`, n);
        option.title = materialSpec(n).description || '';
        return option;
      }));
      finish.value = current;
      finish.title = materialSpec(current).description || 'Surface finish';
      finish.addEventListener('change', () => {
        applyMaterialTo([record], finish.value);
      });
      const matRow = document.createElement('div');
      matRow.className = 'cell-material';
      const matLabel = document.createElement('span');
      matLabel.textContent = 'Material';
      const matButtons = document.createElement('div');
      matButtons.className = 'cell-material-actions';
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.textContent = 'Edit…';
      edit.title = 'Open this material in the editor (Display & context → Materials)';
      edit.addEventListener('click', () => {
        selectNeuron(record.key);
        if ($('panel-display').hidden) togglePanel('toggle-display', 'panel-display');
        materialsPanel?.edit(finish.value);
      });
      const toSel = document.createElement('button');
      toSel.type = 'button';
      toSel.textContent = '→ selected';
      toSel.title = 'Give the selected cells this finish';
      toSel.addEventListener('click', () => applyMaterialTo(selectedRecords(), finish.value));
      const toAll = document.createElement('button');
      toAll.type = 'button';
      toAll.textContent = '→ all';
      toAll.title = 'Give every loaded cell this finish';
      toAll.addEventListener('click', () => applyMaterialTo([...state.neurons.values()], finish.value));
      matButtons.append(edit, toSel, toAll);
      matRow.append(matLabel, finish, matButtons);
      body.append(matRow);
    });

  // -- wireframe -------------------------------------------------------------
  section(panel, record, 'wireframe', 'Wireframe',
    display.showWireframe ? 'on' : 'off', (body) => {
      showToggle(body, 'Show wireframe', display.showWireframe, (on) => {
        setDisplay(record.key, { showWireframe: on });
        renderer.setWireframe(record.key, on);
        persist();
        renderNeuronList();
      }, 'Draw the triangle edges. Independent of the surface: either, both or neither.');
      if (!display.showWireframe) return;

      const wfColor = document.createElement('input');
      wfColor.type = 'color';
      wfColor.value = display.wireframeColor || record.color;
      wfColor.addEventListener('input', () => {
        setDisplay(record.key, { wireframeColor: wfColor.value });
        renderer.setWireframeStyle(record.key, { color: wfColor.value });
        persist();
        // The button below says whether the color is the cell's: not any more.
        matchCell.textContent = "Match the cell's color";
        matchCell.disabled = false;
      });
      field(body, 'Line color', wfColor);

      const matchCell = document.createElement('button');
      matchCell.type = 'button';
      matchCell.className = 'full';
      matchCell.textContent = display.wireframeColor
        ? "Match the cell's color" : "Matching the cell's color";
      matchCell.disabled = !display.wireframeColor;
      matchCell.addEventListener('click', () => {
        setDisplay(record.key, { wireframeColor: null });
        renderer.setWireframeStyle(record.key, { color: record.color });
        persist();
        renderNeuronList();
      });
      body.append(matchCell);

      slider(body, 'Opacity', {
        key: `${record.key}:wfopacity`, min: 0.05, max: 1, step: 0.01,
        value: display.wireframeOpacity, decimals: 2,
        onLive: (v) => renderer.setWireframeStyle(record.key, { opacity: v }),
        onSettle: (v) => { setDisplay(record.key, { wireframeOpacity: v }); persist(); },
      });
    });

  // -- annotations -----------------------------------------------------------
  section(panel, record, 'discs', 'Annotations',
    display.showDiscs
      ? `${display.discStroke.toFixed(2)} µm stroke`
        + (display.discDepth > 0 ? ` · ${Math.round(display.discDepth * 1000)} nm deep` : '')
      : 'off',
    (body) => {
      showToggle(body, 'Show annotations', display.showDiscs, async (on) => {
        await applyDisplay(record, { showDiscs: on });
        renderNeuronList();
      }, 'The raw Viking annotations, each drawn flat in its section plane: '
        + 'a disc as a ring at exactly the radius the annotator set, a closed '
        + 'curve as its traced outline.');
      if (!display.showDiscs) return;

      // The rings sit on the annotated rims, which the surface encloses by a
      // hair, and a soma's top and bottom rings lie under its caps: with
      // depth testing an opaque soma shows almost none of them (RC1 c606,
      // "the top of the soma is not being shown", 2026-09-17).
      showToggle(body, 'Through the surface', display.discThrough !== false, (on) => {
        setDisplay(record.key, { discThrough: on });
        renderer.setDiscStyle(record.key, { through: on });
        persist();
        renderNeuronList();
      }, 'Draw every annotation over the surface, whatever is in front of it. '
        + 'Off, only the rings that poke out of the surface are visible; on an '
        + 'opaque soma that is nearly none of them.');

      const dColor = document.createElement('input');
      dColor.type = 'color';
      dColor.value = display.discColor || record.color;
      dColor.addEventListener('input', () => {
        setDisplay(record.key, { discColor: dColor.value });
        renderer.setDiscStyle(record.key, { color: dColor.value });
        persist();
      });
      field(body, 'Color', dColor);

      slider(body, 'Opacity', {
        key: `${record.key}:dopacity`, min: 0.05, max: 1, step: 0.01,
        value: display.discOpacity, decimals: 2,
        onLive: (v) => renderer.setDiscStyle(record.key, { opacity: v }),
        onSettle: (v) => { setDisplay(record.key, { discOpacity: v }); persist(); },
      });

      slider(body, 'Stroke', {
        key: `${record.key}:dstroke`, min: 0.02, max: 0.6, step: 0.02,
        value: display.discStroke, unit: 'µm', decimals: 2,
        onSettle: (v) => applyDisplay(record, { discStroke: v }),
      });

      // An annotation is flat, so edge-on -- the XZ and YZ views, where Z
      // levels are read -- it has nothing to show. Depth gives it a height
      // along Z, centered on its section plane; zero is the flat ribbon.
      // Shown in nanometers, the unit sections are counted in (a section is
      // 50 to 90 nm; as 0.07 µm it was read as 0.00007); kept in microns in
      // the state, as every other length is and as saved workspaces have it.
      setHelp(slider(body, 'Depth', {
        key: `${record.key}:ddepth`, min: 0, max: 500, step: 5, boxStep: 1,
        value: Math.round(display.discDepth * 1000), unit: 'nm', decimals: 0,
        onSettle: (v) => applyDisplay(record, { discDepth: Math.round(v) / 1000 })
          .then(renderNeuronList),
      }), 'The total height of each annotation along Z, centered on its '
        + 'section plane: 70 nm is 35 nm above the plane and 35 nm below. At the '
        + 'section thickness, annotations on consecutive sections just touch and a '
        + 'skipped section shows as a gap. 0 is the flat ribbon, which disappears '
        + 'when the view is edge-on.');
      const sectionNm = state.volumes.find((v) => v.name === record.source)?.volume_scale?.[2];
      const depthRow = document.createElement('div');
      depthRow.className = 'row editor-row';
      const presetDepth = (label, value, title) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.title = title;
        button.disabled = Math.abs(display.discDepth - value) < 1e-9;
        button.addEventListener('click', async () => {
          await applyDisplay(record, { discDepth: value });
          renderNeuronList();
        });
        depthRow.append(button);
      };
      presetDepth('flat', 0, 'Depth 0: each annotation is a ribbon in its section plane '
        + '(it disappears when the view is edge-on)');
      if (Number.isFinite(sectionNm) && sectionNm > 0) {
        presetDepth(`one section (${Math.round(sectionNm)} nm)`, sectionNm / 1000,
          'Each annotation as tall as the section it was drawn on, so annotations on '
          + 'consecutive sections just touch and a skipped section shows as a gap');
      }
      body.append(depthRow);
    });

  // -- annotation wireframe --------------------------------------------------
  // Asked for 2026-10-14: a wireframe from the annotations themselves, discs
  // and closed curves, averaged along Z by more in a soma (low-frequency
  // swelling) than in a thin dendrite.  The build is the server's
  // (sbfsem.geometry.annotation_wire); these controls are its knobs.
  const help = setHelp;
  section(panel, record, 'annowire', 'Annotation wireframe',
    display.showAnnoWire
      ? (display.annoWireGain > 0 ? `${display.annoWireGain.toFixed(2)} × r` : 'as annotated')
      : 'off',
    (body) => {
      showToggle(body, 'Show annotation wireframe', display.showAnnoWire, async (on) => {
        await applyDisplay(record, { showAnnoWire: on });
        renderNeuronList();
      }, 'A wireframe made from the annotations alone, with no surface: every disc '
        + 'and closed curve as a ring in its section plane, lines along the linked '
        + 'annotations, and each ring averaged with its neighbors in Z over a window '
        + 'that grows with its size.');
      if (!display.showAnnoWire) return;

      showToggle(body, 'Through the surface', display.annoWireThrough !== false, (on) => {
        setDisplay(record.key, { annoWireThrough: on });
        renderer.setAnnoWireStyle(record.key, { through: on });
        persist();
        renderNeuronList();
      }, 'Draw the wireframe over the surface, whatever is in front of it. Off, the '
        + 'surface hides the lines behind it.');

      const wColor = document.createElement('input');
      wColor.type = 'color';
      wColor.value = display.annoWireColor || record.color;
      wColor.addEventListener('input', () => {
        setDisplay(record.key, { annoWireColor: wColor.value });
        renderer.setAnnoWireStyle(record.key, { color: wColor.value });
        persist();
      });
      field(body, 'Color', wColor);

      slider(body, 'Opacity', {
        key: `${record.key}:awopacity`, min: 0.05, max: 1, step: 0.01,
        value: display.annoWireOpacity, decimals: 2,
        onLive: (v) => renderer.setAnnoWireStyle(record.key, { opacity: v }),
        onSettle: (v) => { setDisplay(record.key, { annoWireOpacity: v }); persist(); },
      });

      help(slider(body, 'Averaging', {
        key: `${record.key}:awgain`, min: 0, max: 2, step: 0.05,
        value: display.annoWireGain, unit: '× r', decimals: 2,
        onSettle: (v) => applyDisplay(record, { annoWireGain: v }).then(renderNeuronList),
      }), 'How far along Z each ring is averaged with its neighbors: the window '
        + "(its sigma) is this multiple of the ring's own radius r. At 0.50 a 9 µm "
        + 'soma ring is fitted over about 4.5 µm of Z and a 0.2 µm dendrite ring '
        + 'over 0.1 µm, a section or two. 0 draws the annotations as traced. The '
        + "first and last rings of a chain (a soma's top and bottom, a dendrite's "
        + 'tip) always stay as annotated; the window narrows toward them.');

      const fit = document.createElement('select');
      fit.append(new Option('Quadratic', 'quadratic'), new Option('Mean', 'mean'));
      fit.value = display.annoWireFit === 'mean' ? 'mean' : 'quadratic';
      fit.addEventListener('change', () => applyDisplay(record, { annoWireFit: fit.value }));
      help(field(body, 'Fit', fit), 'Quadratic fits a curve through the window, so a '
        + "soma's dome or a taper passes through and only faster wobble is removed. "
        + 'Mean is a plain weighted average, which flattens anything curved in the '
        + "window: on Inferior Monkey c1178 it held the soma's equator about a "
        + 'micron inside its annotations.');

      help(slider(body, 'Size tolerance', {
        key: `${record.key}:awtol`, min: 1, max: 4, step: 0.05,
        value: display.annoWireTolerance, unit: '×', decimals: 2,
        onSettle: (v) => applyDisplay(record, { annoWireTolerance: v }),
      }), 'How much a neighbor may differ in radius and still count fully: one this '
        + 'many times larger or smaller weighs 0.61, one twice as far off (1.5 → 2.25) '
        + 'weighs 0.14. 1 turns it off.');

      help(slider(body, 'Break at', {
        key: `${record.key}:awbreak`, min: 1.2, max: 10, step: 0.1,
        value: display.annoWireBreak, unit: '× r', decimals: 1,
        onSettle: (v) => applyDisplay(record, { annoWireBreak: v }),
      }), 'A link between two annotations this many times apart in radius r joins '
        + 'no chain: it is drawn as a tie and nothing is averaged across it, so a soma '
        + 'never averages in the dendrite that leaves it.');

      help(slider(body, 'Lines along', {
        key: `${record.key}:awlines`, min: 0, max: 32, step: 1,
        value: display.annoWireLines, decimals: 0,
        onSettle: (v) => applyDisplay(record, { annoWireLines: Math.round(v) }),
      }), 'Lines drawn along each chain of linked annotations, evenly spaced around '
        + 'its rings (all of a ring\'s points when it has fewer). 0 draws only the rings.');

      help(slider(body, 'Ring spacing', {
        key: `${record.key}:awspacing`, min: 0, max: 5, step: 0.05,
        value: display.annoWireSpacing, unit: 'µm', decimals: 2,
        onSettle: (v) => applyDisplay(record, { annoWireSpacing: v }),
      }), "Draw a chain's rings at least this far apart along it, its first and last "
        + 'always; 0 draws every annotation. The lines along still pass through every '
        + 'ring.');
    });

  // -- skeleton --------------------------------------------------------------
  section(panel, record, 'skeleton', 'Skeleton',
    display.showSkeleton
      ? (display.skeletonWidth > 0 ? `${display.skeletonWidth.toFixed(2)} µm tubes` : 'hairline')
      : 'off',
    (body) => {
      const skeleton = document.createElement('input');
      skeleton.type = 'checkbox';
      skeleton.checked = display.showSkeleton;
      const skeletonLabel = document.createElement('label');
      skeletonLabel.className = 'check full';
      skeletonLabel.append(skeleton, document.createTextNode(' Show skeleton'));
      body.append(skeletonLabel);
      skeleton.addEventListener('change', async () => {
        await applyDisplay(record, { showSkeleton: skeleton.checked });
        renderNeuronList();
      });
      if (!display.showSkeleton) return;

      const skColor = document.createElement('input');
      skColor.type = 'color';
      skColor.value = display.skeletonColor || record.color;
      skColor.addEventListener('input', () => {
        setDisplay(record.key, { skeletonColor: skColor.value });
        persist();
        matchCell.textContent = "Match the cell's color";
        matchCell.disabled = false;
      });
      field(body, 'Line color', skColor);

      const matchCell = document.createElement('button');
      matchCell.type = 'button';
      matchCell.className = 'full';
      matchCell.textContent = display.skeletonColor
        ? "Match the cell's color" : "Matching the cell's color";
      matchCell.disabled = !display.skeletonColor;
      matchCell.addEventListener('click', async () => {
        await applyDisplay(record, { skeletonColor: null });
        renderNeuronList();
      });
      body.append(matchCell);

      setHelp(slider(body, 'Line width', {
        key: `${record.key}:skwidth`, min: 0, max: 1.5, step: 0.05,
        value: display.skeletonWidth, unit: 'µm', decimals: 2,
        onSettle: (v) => applyDisplay(record, { skeletonWidth: v }),
      }), 'At width 0 the line is one pixel wide at any zoom. Above 0 the centerline '
        + 'is drawn as tubes, so the width is real 3D thickness in microns.');
      slider(body, 'Line opacity', {
        key: `${record.key}:skopacity`, min: 0.05, max: 1, step: 0.01,
        value: display.skeletonOpacity, decimals: 2,
        onLive: (v) => renderer.setSkeletonStyle(record.key, { opacity: v }),
        onSettle: (v) => applyDisplay(record, { skeletonOpacity: v }),
      });
    });

  // -- synapses --------------------------------------------------------------
  const typeEntries = synapseTypeEntries(record);
  const shownTypes = typeEntries.filter(
    (e) => typeSettings(display, e.local_name).show).length;
  section(panel, record, 'synapses', 'Synapses',
    !record.synapseCounts.length ? 'none on this cell'
      : display.showSynapses ? `${shownTypes} of ${typeEntries.length} types`
      : 'off',
    (body) => {
      const synapses = document.createElement('input');
      synapses.type = 'checkbox';
      synapses.checked = display.showSynapses;
      synapses.disabled = !record.synapseCounts.length;
      const synLabel = document.createElement('label');
      synLabel.className = `check full${record.synapseCounts.length ? '' : ' disabled'}`;
      synLabel.append(synapses, document.createTextNode(' Show synapses'));
      if (!record.synapseCounts.length) synLabel.title = 'This cell has no synapses';
      body.append(synLabel);
      synapses.addEventListener('change', async () => {
        await applyDisplay(record, { showSynapses: synapses.checked });
        renderNeuronList();
      });

      if (!display.showSynapses || !record.synapseCounts.length) return;
      for (const entry of typeEntries) {
        body.append(synapseTypeRow(record, entry, display));
      }
    });

  section(panel, record, 'skeleton-check', 'Skeleton check', skeletonCheckSummary(record),
    (body) => buildSkeletonCheck(body, record, {
      api, renderer, nodesFor, fail,
      rerender: renderNeuronList,
      volumeName: volumeLongName,
      // Only for volumes the identity server knows; the bundled demo is not one.
      openLocationInViking: state.volumes.find((v) => v.name === record.source)?.viking_name
        ? openLocationInViking : null,
    }));

  section(panel, record, 'registration', 'Registration', registrationSummary(record),
    (body) => buildRegistration(body, record));

  // Asked for 2026-10-15: where each annotation was before the section correction
  // (or the current transforms) and where it is drawn, as arrows, a slice or every
  // section at once.  Draws from the cached annotations; moves no geometry.
  section(panel, record, 'shift', 'Position shift',
    shiftSummary(display, shifts.layerOf(record), shifts.pullsFor(record)),
    (body) => buildShiftPanel(body, record,
      shifts.panelContext(record, { slider, showToggle, field })));

  // A person's own moves of annotations, after Viking's correction (movecontrol.js).
  section(panel, record, 'moves', 'Manual moves', moves.summary(record),
    (body) => moves.buildSection(body, record, {
      showToggle, apply: (changes) => applyDisplay(record, changes),
    }));

  // -- copy this cell's settings elsewhere ----------------------------------
  const actions = document.createElement('div');
  actions.className = 'panel-actions';

  const actionsTitle = document.createElement('div');
  actionsTitle.className = 'panel-actions-title';
  actionsTitle.textContent = "Apply this cell's visualization to";

  const actionsRow = document.createElement('div');
  actionsRow.className = 'panel-actions-row';

  const toSelected = document.createElement('button');
  toSelected.type = 'button';
  const others = state.selection.size - (isSelected(record.key) ? 1 : 0);
  toSelected.textContent = others > 0 ? `${others} selected` : 'selected';
  toSelected.disabled = others <= 0;
  toSelected.title = others > 0
    ? `Copy c${record.id}'s settings to the other selected cells`
    : 'Tick other cells first';
  toSelected.addEventListener('click', () => copyCellTo(record, selectedRecords()));

  const toAll = document.createElement('button');
  toAll.type = 'button';
  toAll.textContent = 'all cells';
  toAll.disabled = state.neurons.size < 2;
  toAll.title = `Copy c${record.id}'s settings to every loaded cell`;
  toAll.addEventListener('click', () => copyCellTo(record, [...state.neurons.values()]));

  actionsRow.append(toSelected, toAll);
  actions.append(actionsTitle, actionsRow);
  // A cell's visualization is the 3D render's look.
  if (!graphOn) panel.append(actions);
  return panel;
}

/** The Registration header: where the cell is drawn, and what moved. */
function registrationSummary(record) {
  const lr = record.summary?.live_registration;
  const sc = record.summary?.section_correction;
  const what = CORRECTION_SHORT[sc?.mode] || 'field';
  // A volume whose transforms are not read here: the correction is all there is to say.
  if (!volumeHasTransforms(record.source)) {
    if (sc?.applied) return `${what}, ${(sc.n_moved ?? 0).toLocaleString('en-US')} moved`;
    if (sc?.requested) return `${what} unavailable`;
    return 'stored positions';
  }
  let text;
  if (!lr?.requested) text = 'stored positions';
  else if (!lr.applied) text = 'stored (unavailable)';
  else if (!lr.n_moved) text = 'current';
  else text = `${lr.n_moved.toLocaleString('en-US')} moved, max ${lr.max_um.toFixed(2)} µm`;
  if (sc?.applied) text += ` + ${what}`;
  else if (sc?.requested) text += ` + ${what} unavailable`;
  return text;
}

/** Viking's section-correction modes, as the Registration panel names them. */
const CORRECTION_MODES = [
  ['curvefit', 'Curve fit'],
  ['neighbor', 'Residual field'],
  ['all', 'Field + fit'],
];
const CORRECTION_SHORT = { curvefit: 'fit', neighbor: 'field', all: 'field + fit' };

/**
 * The correction a cell is drawn with: `off` or a mode.  The server's status
 * says what it applied; before any load the cell's own choice does, and a
 * choice from before the modes (true) was the residual field.
 */
function correctionOf(record) {
  const sc = record.summary?.section_correction;
  if (sc) return sc.requested ? (sc.mode || 'neighbor') : 'off';
  const c = record.corrections;
  if (c === true) return 'neighbor';
  if (c === false) return 'off';
  return typeof c === 'string' ? c : 'off';
}

/**
 * Where one cell is drawn: Viking's current section transforms, and on top of
 * them Viking's section correction, each a switch with a small chip beside it
 * that says what it did ("56 moved", "unavailable").  Nothing stands under them
 * as prose: what each does is the switch's 2 s hover help (with a drawing), and
 * the facts -- the files, the counts, the distances, and for a failure the reason
 * in plain words with the server's own message -- are the chip's.
 *
 * A volume whose transforms are not read here (`has_transforms` false) has no
 * transforms switch; a state saved with it on still loads, at stored positions.
 */
function buildRegistration(body, record) {
  const row = (text, checked, help, figure, chip, onChange) => {
    const wrap = document.createElement('div');
    wrap.className = 'switch-row';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = checked;
    const label = document.createElement('label');
    label.className = 'check';
    label.append(box, document.createTextNode(` ${text}`));
    setHelp(label, help, figure);
    wrap.append(label);
    if (chip.text) {
      const tag = document.createElement('span');
      tag.className = `status-chip${chip.warn ? ' warn' : ''}`;
      tag.tabIndex = 0;
      tag.textContent = chip.text;
      tag.setAttribute('aria-label', `${chip.text}. ${chip.help}`);
      setHelp(tag, chip.help);
      wrap.append(tag);
    }
    body.append(wrap);
    box.addEventListener('change', () => onChange(box.checked));
    return wrap;
  };

  const lr = record.summary?.live_registration;
  if (volumeHasTransforms(record.source)) {
    row("Viking's current section transforms", !!lr?.requested, REG_HELP.transforms, REG_FIGURES.transforms,
      transformsChip(lr, { stamp: humanStamp }),
      (on) => setRegistration(record, { registration: on })).dataset.reg = 'transforms';
  }

  const sc = record.summary?.section_correction;
  const current = correctionOf(record);
  const live = state.neurons.get(record.key) ?? record;
  const pick = document.createElement('select');
  pick.append(...CORRECTION_MODES.map(([value, label]) => new Option(label, value)));
  if (current !== 'off') live.correctionMode = current;
  pick.value = current !== 'off' ? current : (live.correctionMode || volumeDefaultCorrection(record.source));
  row("Viking's section correction", current !== 'off',
    `${REG_HELP.correction}\n\n${correctionForCell(sc)}`, REG_FIGURES.correction,
    correctionChip(sc, { stamp: humanStamp }),
    (on) => setRegistration(record, { corrections: on ? pick.value : 'off' })).dataset.reg = 'correction';
  pick.addEventListener('change', () => {
    live.correctionMode = pick.value;
    setRegistration(record, { corrections: pick.value });
  });
  field(body, 'Correction', pick);
  setHelp(pick, REG_HELP.mode, REG_FIGURES.mode);
  if (pick.previousElementSibling) setHelp(pick.previousElementSibling, REG_HELP.mode, REG_FIGURES.mode);
}

/**
 * One entry per local type. Several Viking types can share a local name
 * (every unmapped type is Unknown; adherens variants are Desmosome), and the
 * markers are fetched by local name, so they are one marker set, one row and
 * one checkbox -- two "Unknown" rows toggling the same markers only confused.
 * The Viking names are kept for the tooltip.
 */
function synapseTypeEntries(record) {
  const merged = new Map();
  for (const entry of record.synapseCounts || []) {
    const hit = merged.get(entry.local_name);
    if (hit) {
      hit.count += entry.count;
      if (!hit.vikingNames.includes(entry.viking_name)) hit.vikingNames.push(entry.viking_name);
    } else {
      merged.set(entry.local_name, { ...entry, vikingNames: [entry.viking_name] });
    }
  }
  return [...merged.values()];
}

/** One synapse type's controls: show, shape, size, color, opacity. */
function synapseTypeRow(record, entry, display) {
  const name = entry.local_name;
  const t = typeSettings(display, name);
  const row = document.createElement('div');
  row.className = 'type-row';

  const head = document.createElement('div');
  head.className = 'type-head';

  const show = document.createElement('input');
  show.type = 'checkbox';
  show.checked = t.show;
  show.title = `Show ${name}`;
  show.addEventListener('change', async () => {
    await applyTypeChange(record, name, { show: show.checked });
    renderNeuronList();
  });

  const swatch = document.createElement('span');
  swatch.className = 'type-dot';
  swatch.style.background = t.color || SYNAPSE_COLORS[name] || rgbCss(entry.color);

  const title = document.createElement('span');
  title.className = 'type-name';
  title.textContent = name;
  const vikingNames = entry.vikingNames || [entry.viking_name];
  if (vikingNames.length && !(vikingNames.length === 1 && vikingNames[0] === name)) {
    title.title = `Viking type${vikingNames.length > 1 ? 's' : ''}: ${vikingNames.join(', ')}`;
  }

  const count = document.createElement('span');
  count.className = 'type-count';
  count.textContent = entry.count;
  count.title = `${entry.count} × ${vikingNames.join(' + ')}`;

  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'icon-btn type-reset';
  reset.textContent = '⟲';
  reset.disabled = !hasTypeOverride(display, name);
  reset.title = reset.disabled
    ? `${name} is using the cell defaults`
    : `Reset ${name} to the cell defaults`;
  reset.addEventListener('click', async () => {
    await applyTypeChange(record, name, {}, { clear: true });
    renderNeuronList();
  });

  head.append(show, swatch, title, count, reset);
  row.append(head);
  if (!t.show) return row;

  const grid = document.createElement('div');
  grid.className = 'type-grid';
  row.append(grid);

  const shape = document.createElement('select');
  shape.append(new Option('Circle', 'circle'), new Option('Oval', 'oval'));
  shape.value = t.shape;
  shape.addEventListener('change', async () => {
    const changes = { shape: shape.value };
    if (t.size === DEFAULT_SYNAPSE_SIZE[t.shape]) {
      changes.size = DEFAULT_SYNAPSE_SIZE[shape.value];
    }
    await applyTypeChange(record, name, changes);
    renderNeuronList();
  });
  field(grid, 'Shape', shape);

  slider(grid, 'Size', {
    key: `${record.key}:${name}:size`, min: 0.02, max: 2, step: 0.01,
    value: t.size, unit: 'µm', decimals: 2,
    onSettle: (v) => applyTypeChange(record, name, { size: v }),
  });

  const color = document.createElement('input');
  color.type = 'color';
  color.value = t.color || SYNAPSE_COLORS[name] || rgbCss(entry.color);
  color.addEventListener('input', () => {
    swatch.style.background = color.value;
    applyTypeChange(record, name, { color: color.value });
  });
  field(grid, 'Color', color);

  slider(grid, 'Opacity', {
    key: `${record.key}:${name}:opacity`, min: 0.05, max: 1, step: 0.01,
    value: t.opacity, decimals: 2,
    onLive: (v) => renderer.setSynapseStyle(record.key, { type: name, opacity: v }),
    onSettle: (v) => applyTypeChange(record, name, { opacity: v }),
  });

  if (t.shape === 'oval') {
    const note = document.createElement('p');
    note.className = 'note';
    note.textContent =
      'Ovals are drawn at each annotation’s true length along its own axis '
      + '(~0.1–0.25 µm). Size sets their thickness.';
    grid.append(note);
  }
  return row;
}

/**
 * Apply one synapse type's overrides.
 *
 * Deliberately does not re-render the list: callers that changed the panel's
 * shape do that themselves. A slider calling this must leave the DOM alone,
 * or the control the pointer is holding is replaced mid-drag.
 */
async function applyTypeChange(record, name, changes, { clear = false } = {}) {
  const next = clear
    ? withTypeOverride(record.display, name, {
      show: undefined, size: undefined, color: undefined,
      opacity: undefined, shape: undefined,
    })
    : withTypeOverride(record.display, name, changes);
  await applyDisplay(record, next);
}


// The last Find: what Add all / Add selected act on, and the volume it was
// searched in (cell IDs are numbered per volume, so results mean nothing in
// another).
let searchResults = [];
let searchSource = null;

/** Forget the Find results: the volume they belong to is no longer the one in use. */
function clearFind() {
  searchResults = [];
  searchSource = null;
  $('search-results').replaceChildren();
  $('search-actions').hidden = true;
}

function renderSearch(results, source) {
  searchResults = results;
  searchSource = source;
  const list = $('search-results');
  const actions = $('search-actions');
  list.replaceChildren();
  if (!results.length) {
    const li = document.createElement('li');
    li.textContent = 'No matches';
    li.className = 'muted';
    list.append(li);
    actions.hidden = true;
    return;
  }
  // Every result, not the first sixty: a label that matches 140 cells is
  // exactly when "Add all" earns its place.
  for (const result of results) {
    const li = document.createElement('li');
    const tick = document.createElement('input');
    tick.type = 'checkbox';
    tick.className = 'pick';
    tick.dataset.id = result.id;
    tick.title = 'Tick for Add selected';
    tick.addEventListener('click', (event) => { event.stopPropagation(); updateSearchActions(); });
    const label = document.createElement('span');
    label.textContent = result.label || '(no label)';
    const id = document.createElement('span');
    id.className = 'id';
    id.textContent = result.id;
    li.append(tick, label, id);
    li.title = `Click to load c${result.id} on its own (${volumeName(source)})`;
    // Clicking the row still loads that one cell, as it always did -- in the
    // volume it was found in, which becomes the working volume.
    li.addEventListener('click', () => {
      switchVolume(source);
      $('neuron-id').value = result.id;
      $('load-form').requestSubmit();
    });
    list.append(li);
  }
  actions.hidden = false;
  updateSearchActions();
}

function pickedSearchIds() {
  return [...document.querySelectorAll('#search-results input.pick:checked')]
    .map((box) => Number(box.dataset.id));
}

function updateSearchActions() {
  const picked = pickedSearchIds().length;
  const loaded = searchResults.filter((r) => state.neurons.has(keyOf(searchSource, r.id))).length;
  $('search-count').textContent = `${searchResults.length} match${searchResults.length === 1 ? '' : 'es'}`
    + (searchResults.length && searchSource ? ` in ${volumeName(searchSource)}` : '')
    + (loaded ? `, ${loaded} loaded` : '');
  $('search-add-all').textContent = `Add all (${searchResults.length})`;
  $('search-add-all').disabled = searchResults.length === 0;
  $('search-add-selected').textContent = picked ? `Add selected (${picked})` : 'Add selected';
  $('search-add-selected').disabled = picked === 0;
}

/** Load a set of Find results, skipping cells already loaded. */
async function addSearchResults(ids) {
  // The volume the search was made in, not the one selected now.
  const source = searchSource ?? state.source;
  const fresh = ids.filter((id) => !state.neurons.has(keyOf(source, id)));
  if (!fresh.length) {
    statusEl.textContent = 'Those cells are already loaded';
    statusEl.className = 'status';
    return;
  }
  await loadCells(fresh.map((id) => ({ source, id })));
  updateSearchActions();
}

// -- views -----------------------------------------------------------------
/** `cell`: the record a right-click asked this tab for, when it was not the tab itself that was clicked. */
function activateView(view, { cell = null } = {}) {
  state.view = view;
  persist();
  markTabs();
  for (const panel of document.querySelectorAll('.view')) {
    panel.classList.toggle('active', panel.id === `view-${view}`);
  }
  if (view === 'stats' && analysesSignature() !== analysesFor) renderAnalyses();
  if (view === 'compare') fillCompareCells();
  if (view === 'network') { fillNetworkSources(); network.fit(); }
  if (view === 'connectivity') connectivity.loadedChanged(cell?.source ?? null);
  if (view === 'strata' && strataSignature(strataRecords(cell)) !== strataFor) {
    drawStratification({ cell });
  }
  if (view === 'table' && tableSignature() !== tableFor) drawTable();
  if (view === 'settings') settings?.show();
}

/**
 * Make `record` the focused cell and show a tab: what the right-click Analysis items do.
 * Each tab is brought to the cell that was clicked, not to the one it showed
 * last: the table's select, the Connectivity volume, and a Stratification chart
 * that has the cells loaded and visible now.
 */
function showViewFor(record, view) {
  selectNeuron(record.key);
  if (view === 'table') {
    // The table names its cell in its own select, which keeps the cell last
    // picked there.
    populateTableSelect();
    $('table-neuron').value = record.key;
  }
  activateView(view, { cell: record });
}

// -- GraphApp ------------------------------------------------------------------
// MATLAB SBFSEM-tools' GraphApp as the 3D view's second look (graphapp.js): the
// tab row's GraphApp button beside 3D render shows the same cells, camera and
// zoom as GraphApp drew a neuron, on black.  The 3D render is left as it was,
// and each cell's GraphApp objects are drawn once per load (in the background
// after a load too), so switching either way is a flip of two layers.
// Right-click "Open in GraphApp" goes there with only that cell shown; the
// visibility it hid is given back on the way out, as GraphApp's own right-click
// "Isolate" gives it back when unticked.

let graphOn = false;            // the 3D view shows GraphApp
let graphSaved = null;          // Map(key -> shown) an isolation keeps to give back
let graphIsolated = null;       // the key isolated, while it is
const graphDrawn = new Map();   // key -> the load its GraphApp objects were drawn from
const graphHue = new Map();     // key -> its place in GraphApp's palette, first come first served
let graphHueNext = 0;
let graphSettings = normalizeGraphSettings(storedJSON(GRAPH_SETTINGS_KEY));

/** The sizes and flag colors GraphApp draws with (graphapp.js). */
const GRAPH_LOOK = { markerPx: MARKER_SIZES, flagPx: FLAG_PX, synapsePx: SYNAPSE_PX, flagColors: FLAG_COLORS };

/** A cell's GraphApp colors, its palette place kept for as long as the tab lives. */
function graphColorsOf(record) {
  if (!graphHue.has(record.key)) graphHue.set(record.key, graphHueNext++);
  return graphColors(graphHue.get(record.key));
}

/** A cell's synapses as GraphApp draws them (`/synapse-points`), kept per load. */
function askSynapsePoints(record) {
  return askKept(record, 'synapsePoints', () => api.synapsePoints(record.source, record.id));
}

/** Draw `record` as GraphApp does, unless it is drawn from this very load already. */
async function drawGraphCell(record) {
  const drawnFrom = `${record.loadGen}|${dataKey(record)}`;
  if (graphDrawn.get(record.key) === drawnFrom && renderer.graphs.has(record.key)) return;
  const curves = (record.summary?.n_curve_annotations || 0) > 0;
  const [nodes, adjacency, skeleton, synapses, outlines] = await Promise.all([
    nodesFor(record), adjacencyFor(record), askSkeleton(record),
    askSynapsePoints(record).catch(() => []), curves ? askOutlines(record).catch(() => []) : [],
  ]);
  // Loaded again or let go while those were on their way: that load draws it.
  if (state.neurons.get(record.key) !== record) return;
  const edges = [];
  for (const [a, ns] of adjacency) for (const b of ns) edges.push({ a, b });
  const synapseColors = {};
  for (const entry of record.synapseCounts || []) {
    synapseColors[entry.local_name] = SYNAPSE_COLORS[entry.local_name] || rgbCss(entry.color);
  }
  renderer.setGraphCell(record.key, {
    nodes, outlines, skeleton, unfinished: unfinishedIds(nodes, edges), synapses, synapseColors,
  }, graphColorsOf(record), GRAPH_LOOK);
  graphDrawn.set(record.key, drawnFrom);
}

/**
 * Every loaded cell drawn as GraphApp does: while GraphApp is shown, and in the
 * background once loads settle, so the switch to it finds them drawn.  Two at a
 * time, the shown cells first.
 */
async function syncGraph() {
  const records = [...state.neurons.values()].sort((a, b) => Number(b.visible) - Number(a.visible));
  const missing = records.filter((r) => graphDrawn.get(r.key) !== `${r.loadGen}|${dataKey(r)}`
    || !renderer.graphs.has(r.key));
  if (!missing.length) return;
  if (graphOn) {
    statusEl.textContent = `Drawing ${missing.length} cell(s) for GraphApp…`;
    statusEl.className = 'status busy';
  }
  let failed = 0;
  await inParallel(missing, 2, async (record) => {
    try { await drawGraphCell(record); } catch (error) {
      failed += 1;
      console.warn(`GraphApp could not draw c${record.id}:`, error);
    }
  });
  if (graphOn) {
    statusEl.textContent = failed ? `GraphApp could not draw ${failed} cell(s)` : 'GraphApp ready';
    statusEl.className = failed ? 'status error' : 'status';
  }
}

/** GraphApp's controls into the renderer (and the boxes that show them). */
function applyGraphSettings() {
  renderer.setGraphSettings(graphSettings, {
    segmentColor: (t) => colormapSample(graphSettings.colormap, t),
    positions: segmentPositions,
  });
}

/**
 * Show GraphApp (`on`) or the 3D render.  `isolateKey` goes to GraphApp with only
 * that cell shown (right-click "Open in GraphApp"); leaving gives back the
 * visibility an isolation hid.  The camera, the cells and the list stay.
 */
async function setGraphLook(on, { isolateKey = null } = {}) {
  if (on) {
    if (iplFlat) await setIplFlat(false, { why: 'IPL no longer flattened: GraphApp draws the annotations where they are' });
    const entering = !graphOn;
    graphOn = true;
    renderer.setLook('graph');
    applyGraphSettings();
    if (isolateKey) isolateCell(isolateKey);
    if (entering) {
      $('view-render').classList.add('graph-on');
      $('graph-tools').hidden = false;
      markTabs();
      renderNeuronList();
      persist();
    }
    await syncGraph();
    return;
  }
  if (!graphOn) return;
  graphOn = false;
  renderer.setLook('render');
  unisolate();
  $('view-render').classList.remove('graph-on');
  $('graph-tools').hidden = true;
  markTabs();
  renderNeuronList();
  persist();
}

/** Show only `key`, keeping what every cell showed to give back. */
function isolateCell(key) {
  const shown = new Map([...state.neurons.values()].map((r) => [r.key, r.visible]));
  const { saved, show } = isolate(shown, key, graphSaved);
  graphSaved = saved;
  graphIsolated = key;
  for (const [k, visible] of show) setVisible(k, visible);
  renderNeuronList();
  persist();
}

/** Give back what an isolation hid (the cells loaded since keep what they show). */
function unisolate() {
  if (!graphSaved) return;
  const shown = new Map([...state.neurons.values()].map((r) => [r.key, r.visible]));
  for (const [k, visible] of restoreVisibility(shown, graphSaved)) setVisible(k, visible);
  graphSaved = null;
  graphIsolated = null;
  renderNeuronList();
  persist();
}

/** Right-click "Open in GraphApp": GraphApp with only this cell shown. */
async function openInGraphApp(record) {
  selectNeuron(record.key);
  if (state.view !== 'render') activateView('render');
  await setGraphLook(true, { isolateKey: record.key });
  // The cell alone in a view that held only the others shows nothing: framed, as Reset
  // view would, unless Held (Temporal c5885 beside c16137, 2026-10-22).
  if (!display?.holdLimits && renderer.seesAnything() === false) renderer.resetView();
}

/** The tab row: "3D render" or "GraphApp" lit by the look, the other tabs by the view. */
function markTabs() {
  for (const tab of document.querySelectorAll('.tab')) {
    const active = tab.dataset.view === state.view
      && (state.view !== 'render' || (tab.dataset.look === 'graph') === graphOn);
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
  }
}

/** The menu GraphApp's right click opens: isolate, Viking, deselect -- no more. */
function graphMenuItems(record, context, item) {
  const isolated = graphIsolated === record.key;
  item(`Isolate c${record.id}`, () => (isolated ? unisolate() : isolateCell(record.key)), { checked: isolated })
    .title = isolated
      ? 'Untick: show the cells again as they were before isolating'
      : 'Hide every other cell; untick to show them again as they were (leaving GraphApp does too)';
  if (state.volumes.find((v) => v.name === record.source)?.viking_name && context?.locationId) {
    item('Open in Viking', () => openLocationInViking(record, context.locationId)).title =
      `Opens this volume in Viking at annotation ${context.locationId}`;
  }
  if (renderer.selection) item('Deselect', () => renderer.clearSelection()).title = 'Also Esc';
}

/** A synapse picked in GraphApp: the readout says which, and what it links to (asked once per cell). */
const linkedPartners = new Map();   // key@dataKey -> Map(synapse ID -> partner cell ID or null)
async function showSynapsePick(record, synapse, locationId) {
  const text = $('pick-text');
  const said = `synapse ${synapse.id} ${synapse.type}`;
  text.textContent = `${volumeLongName(record.source)} c${record.id} · ${said}`;
  writeLocationField($('pick-location'), locationId, record.source);
  if (lastPick) {
    lastPick.locationId = locationId;
    lastPick.locationSource = record.source;
  }
  const cacheKey = `${record.key}@${dataKey(record)}`;
  let partners = linkedPartners.get(cacheKey);
  if (!partners) {
    try {
      const report = await api.linkedNeurons(record.source, record.id);
      partners = new Map(report.rows.map((row) => [row.synapse_id, row.neuron_id ?? null]));
      linkedPartners.set(cacheKey, partners);
    } catch {
      return;
    }
  }
  if (lastPick?.locationId !== locationId) return;
  const partner = partners.get(synapse.id);
  text.textContent = `${volumeLongName(record.source)} c${record.id} · ${said} · `
    + (partner ? `link c${partner}` : 'link none');
}

/** GraphApp's Find ID: a Location ID, else a synapse structure ID, among this volume's loaded cells. */
async function graphFindId(text) {
  const cells = await Promise.all([...state.neurons.values()].filter((r) => r.source === state.source)
    .map(async (r) => ({
      key: r.key, nodes: await nodesFor(r).catch(() => []), synapses: await askSynapsePoints(r).catch(() => []),
    })));
  const found = findId(text, cells);
  if (!found) throw new Error('Enter a Location ID or a synapse ID (a positive integer)');
  if (found.kind === 'unknown' || (found.kind === 'location' && !found.synapse)) {
    await gotoLocationId(String(found.id ?? found.locationId));
    return;
  }
  // A synapse, or a synapse's annotation: flown to and ringed in yellow.
  const record = state.neurons.get(found.key);
  const distance = renderer.camera.position.distanceTo(renderer.controls.target);
  await renderer.flyTo(renderer.toWorld(found.xyz_um, record.source), { distance });
  renderer.select({
    kind: 'synapse', key: record.key, px: 0, depth: 0, through: true, type: found.synapse.type,
    synapseId: found.synapse.synapse_id, locationId: found.locationId, section: found.section,
    point: [...found.xyz_um], radiusUm: 0.35,
  });
  const point = { x: found.xyz_um[0], y: found.xyz_um[1], z: found.xyz_um[2] };
  await showPick({ point, key: record.key });
  await showSynapsePick(record, { id: found.synapse.synapse_id, type: found.synapse.type }, found.locationId);
  statusEl.textContent = `Synapse ${found.synapse.synapse_id} of c${record.id}, at annotation ${found.locationId}`;
  statusEl.className = 'status';
}

/** GraphApp's controls: kept in this browser, drawn at once. */
function bindGraphTools() {
  const boxes = {
    'graph-discs': 'discs', 'graph-synapses': 'synapses', 'graph-unfinished': 'unfinished',
    'graph-terminals': 'terminals', 'graph-offedges': 'offEdges', 'graph-segments': 'colorSegments',
  };
  const maps = $('graph-colormap');
  maps.replaceChildren(...colormapNames().map((n) => new Option(n, n)));
  const show = () => {
    for (const [id, field] of Object.entries(boxes)) $(id).checked = !!graphSettings[field];
    $('graph-marker').value = graphSettings.markerSize;
    maps.value = graphSettings.colormap;
    if (maps.value !== graphSettings.colormap) maps.value = 'Parula';
  };
  const changed = () => {
    graphSettings = normalizeGraphSettings({
      ...Object.fromEntries(Object.entries(boxes).map(([id, field]) => [field, $(id).checked])),
      markerSize: $('graph-marker').value, colormap: maps.value,
    });
    storeValue(GRAPH_SETTINGS_KEY, graphSettings);
    applyGraphSettings();
  };
  show();
  for (const id of [...Object.keys(boxes), 'graph-marker', 'graph-colormap']) $(id).addEventListener('change', changed);
  const find = $('graph-find');
  find.addEventListener('keydown', async (event) => {
    event.stopPropagation();          // the view's keys stay out of the box
    if (event.key !== 'Enter') return;
    event.preventDefault();
    try { await graphFindId(find.value); } catch (error) { fail(error); }
  });
  applyGraphSettings();
}

// -- the quick technique picker ---------------------------------------------
// One list covering the method/pathway pairs anyone actually renders with,
// shared with every Render compare pane and `sbfsem-measure` (RENDER_PRESETS,
// from the schema). The panel's own Method and Pathway dropdowns stay the
// full-control version; this is the shortcut.
const QUICK_TECHNIQUE_TITLE = 'Render technique — applied to the selected cells as soon as you pick one';

function fillTechniques() {
  const select = $('quick-technique');
  select.replaceChildren(...RENDER_PRESETS.map((p) => new Option(p.label, p.id)));
  // A combination the list does not name (hand-tuned in the panel) still
  // needs somewhere to sit, or the box would misreport what is rendered.
  select.append(new Option('Custom…', 'custom'));
  gateTechniques();
  syncQuickTechnique();
}

/** A technique that needs MeshLab is offered only where the server has it. */
function gateTechniques() {
  compare?.refreshCaps();
  const meshlabOff = knownBuild !== null && knownBuild.meshlab === false;
  for (const option of $('quick-technique').options) {
    const preset = RENDER_PRESETS.find((p) => p.id === option.value);
    const off = !!preset?.requires && meshlabOff;
    option.disabled = off;
    option.title = off ? RENDER_REQUIRES[preset.requires] : '';
  }
}

/** Point the quick picker at whatever the panel currently says. */
function syncQuickTechnique() {
  const match = matchPreset({
    method: $('render-method').value,
    pathway: $('pathway').value,
    meshlab: $('meshlab-mode').value,
  });
  const picker = $('quick-technique');
  picker.value = match ? match.id : 'custom';
  // The closed picker is capped in width and clips the end of a long name, which is the part
  // that says which MeshLab mode is on: hover names it in full.
  const shown = picker.selectedOptions?.[0]?.textContent?.trim();
  picker.title = shown ? `${shown}. ${QUICK_TECHNIQUE_TITLE}` : QUICK_TECHNIQUE_TITLE;
}

async function applyQuickTechnique() {
  const technique = RENDER_PRESETS.find((t) => t.id === $('quick-technique').value);
  if (!technique) return;
  // The whole parameter set, not just the two dropdowns: each technique has
  // its own best parameters (the user's saved defaults, or the recommended
  // ones), and picking a technique means asking for it at its best.  A MeshLab
  // preset adds its mode on top.
  const defaults = presetOptions(technique);
  // With the panel open nothing rebuilds yet, so the outline raster is fitted
  // to the selection here, where the panel shows it before Apply.
  // Collapsed, applyRenderTo fits each cell itself.
  const seeded = $('panel-render').hidden ? null
    : curvePixelSeed(selectedRecords()[0], defaults);
  writeControls(seeded ? { ...defaults, pixel_size_um: seeded } : defaults);
  updateMethodControls();
  updateScopeNote();
  if (seeded) {
    showRenderNotice(`Voxel size set to ${seeded} µm`,
      ` instead of the ${defaults.pixel_size_um} µm default: a cell traced`
      + ` with outlines is rasterized at ${seeded} µm under ${technique.label},`
      + ' or it can run past the server\'s voxel limit.');
  }
  // Collapsed, the picker IS the action: nobody wants to open a panel to
  // press Apply for the one setting they came for. Expanded, the panel's own
  // Apply buttons stay in charge, so a technique can be set up alongside its
  // parameters before anything rebuilds.  Say so, or the picker reads as a
  // render that never happened (2026-09-09: 'Original' in the box, the
  // smooth default still on screen).
  if (!$('panel-render').hidden) {
    statusEl.textContent = `${technique.label} set in the panel — press Apply to rebuild`;
    statusEl.className = 'status';
    return;
  }
  const targets = selectedRecords();
  if (!targets.length) {
    statusEl.textContent = 'Select a cell first — the technique applies to the selection';
    statusEl.className = 'status error';
    return;
  }
  await applyRenderTo(targets, { fitPixel: true });
}

// -- expandable tool panels -------------------------------------------------
// Each toggles on its own: the two are read together often enough (set a
// pathway, then judge it against a boundary) that closing one to open the
// other would be a nuisance.
function togglePanel(buttonId, panelId) {
  const panel = $(panelId);
  const open = panel.hidden;
  panel.hidden = !open;
  const button = $(buttonId);
  button.setAttribute('aria-expanded', String(open));
  button.classList.toggle('open', open);
  const caret = button.querySelector('.caret');
  if (caret) caret.textContent = open ? '▾' : '▸';
  // The render toggle is the bare arrow beside the technique picker: it
  // points up when there is a panel to pull up, down when it can be put away.
  if (button.classList.contains('arrow-only')) {
    button.textContent = open ? '▼' : '▲';
    button.title = open ? 'Hide the render parameters' : 'Show every render parameter';
  }
  if (open && panelId === 'panel-display') {
    // Seed the typed fields from what is actually on screen.
    display.readCamera();
    display.readLimits();
  }
}

// -- video recording --------------------------------------------------------
function onRecorderState({ recording, elapsed, total }) {
  const chip = $('rec-indicator');
  chip.hidden = !recording;
  if (!recording) {
    $('video-btn').disabled = false;
    return;
  }
  $('video-btn').disabled = true;
  $('rec-text').textContent = total
    ? `recording ${elapsed.toFixed(1)}s / ${total}s`
    : `recording ${elapsed.toFixed(1)}s`;
}

/** The finished recording, kept so Save can re-offer it on a real click. */
let pendingVideo = null;

function bindVideo() {
  const dialog = $('video-dialog');
  const formats = supportedFormats();
  const formatSelect = $('video-format');
  formatSelect.replaceChildren(...formats.map(
    (f) => new Option(f === 'mp4' ? 'MP4 (H.264)' : 'WebM (VP9)', f)));

  const syncMode = () => {
    $('video-auto-fields').hidden = $('video-mode').value !== 'auto';
  };
  $('video-mode').addEventListener('change', syncMode);
  syncMode();

  $('video-btn').addEventListener('click', () => {
    if (!formats.length) {
      statusEl.textContent = 'This browser cannot record video (no MediaRecorder)';
      statusEl.className = 'status error';
      return;
    }
    $('video-error').hidden = true;
    dialog.showModal();
  });

  $('video-start').addEventListener('click', async () => {
    const options = {
      mode: $('video-mode').value,
      seconds: Number($('video-seconds').value) || 10,
      turns: Number($('video-turns').value) || 1,
      axis: $('video-axis').value,
      fps: Number($('video-fps').value) || 30,
      quality: $('video-quality').value,
      format: formatSelect.value,
    };
    $('video-ready').hidden = true;
    pendingVideo = null;
    let finished;
    try {
      finished = recorder.start(options);
    } catch (error) {
      $('video-error').hidden = false;
      $('video-error').textContent = String(error.message || error);
      return;
    }
    dialog.close();
    const blob = await finished;
    // A recording that produced nothing must say so. Silence here read as
    // "the app did nothing", which is the one thing it must never do.
    if (!blob || !blob.size) {
      statusEl.textContent = 'The recording captured no frames — try WebM, '
        + 'or keep this tab in front while it records';
      statusEl.className = 'status error';
      return;
    }
    pendingVideo = blob;
    const size = blob.size >= 1e6
      ? `${(blob.size / 1e6).toFixed(1)} MB` : `${Math.round(blob.size / 1024)} kB`;
    $('video-ready-text').textContent = `Video ready — ${size}`;
    $('video-ready').hidden = false;
    // Try the automatic download too; when the browser allows it the Save
    // button is simply a second chance nobody has to use.
    ViewRecorder.save(blob, `sbfsem-${Date.now()}`);
    statusEl.textContent = `Recorded ${size} — use Save if nothing downloaded`;
    statusEl.className = 'status';
  });

  $('video-save').addEventListener('click', () => {
    if (pendingVideo) ViewRecorder.save(pendingVideo, `sbfsem-${Date.now()}`);
  });
  $('video-dismiss').addEventListener('click', () => {
    $('video-ready').hidden = true;
    pendingVideo = null;
  });

  $('rec-stop').addEventListener('click', () => recorder.stop());
}

/**
 * A Render compare pane's options into the 3D render panel's controls: the geometry
 * options only (Clip IPL stays as it is), and nothing is applied or rebuilt.
 */
function sendPaneToMain(options, name) {
  writeControls({ ...readControls(), ...options });
  updateMethodControls();
  updateScopeNote();
  setCompareStatus(`${name} is in the 3D render panel — press Apply there to use it.`);
}

const COMPARE_NO_CELL = 'Load a cell first, then press Compare.';
const COMPARE_DOUBLE_CLICK_MS = 700;

/** The compare tab's status line, with the red error look reset by every
 * message that is not one (the class used to stay on). */
function setCompareStatus(message, kind) {
  const el = $('compare-status');
  el.textContent = message;
  el.className = kind === 'error' ? 'muted error' : 'muted';
}

/** The compare tab's cell picker: every loaded cell, keeping the choice. */
function fillCompareCells() {
  const select = $('compare-cell');
  const keep = select.value;
  // Named with the volume as well: IDs are numbered per volume (ground rule 11).
  const entries = [...state.neurons.values()].map(
    (r) => [`c${r.id} · ${volumeAbbrev(r.source)}`, r.key]);
  // Rebuilt only when the list changed: replacing the options under an open
  // dropdown closes it, and this runs on every selection event.
  if (!sameOptions(select.options, entries)) {
    select.replaceChildren(...entries.map(([label, key]) => new Option(label, key)));
    if ([...state.neurons.keys()].includes(keep)) select.value = keep;
    else if (state.selected && state.neurons.has(state.selected)) select.value = state.selected;
  }
  if (!state.neurons.size) setCompareStatus(COMPARE_NO_CELL);
  else if ($('compare-status').textContent === COMPARE_NO_CELL) setCompareStatus('');
  // The compared cell was unloaded or loaded again: what the panes show is
  // of the old one until the next Compare.
  compare?.markStale(select.value);
}

// -- the network tab ---------------------------------------------------------
// One network per volume. Cells in different volumes cannot be connected, and
// an ID names a different cell in each, so the build takes the loaded cells
// of the volume picked here and the request carries that volume (rule 11).

// Link types the user has unticked in the legend; kept across rebuilds.
const networkHidden = new Set();

/** The Volume select on the tab: every volume with a loaded cell. */
function fillNetworkSources() {
  const select = $('net-source');
  if (!select) return;
  const sources = [...new Set([...state.neurons.values()].map((r) => r.source))];
  const preferred = select.value || selectedRecord()?.source || state.source;
  setOptions(select, (sources.length ? sources : [preferred]).map((source) => [volumeName(source), source]));
  select.value = sources.includes(preferred) ? preferred : (sources[0] ?? preferred);
}

async function buildNetwork() {
  fillNetworkSources();
  const source = $('net-source').value;
  const records = [...state.neurons.values()].filter((r) => r.source === source);
  if (!records.length) {
    $('net-summary').textContent = `No cells loaded in ${volumeName(source)}.`;
    return;
  }
  try {
    const data = await api.network(source, records.map((r) => r.id));
    network.setElements(data.elements, source);
    renderNetworkLegend();
    drawNetwork();
  } catch (error) { fail(error); }
}

/** Redraw the kept answer with the current names, weight and hidden types. */
function drawNetwork() {
  if (!network.elements.length) return;
  const present = network.types().map((t) => t.type);
  const enabled = new Set(present.filter((t) => !networkHidden.has(t)));
  const counts = network.render({
    layout: $('net-layout').value,
    minWeight: Number($('net-minweight').value) || 1,
    labels: $('net-labels').value,
    enabledTypes: enabled,
  });
  $('net-summary').textContent =
    `${counts.nodes} neurons, ${counts.edges} connections (of ${counts.totalEdges}) in ${volumeName(network.source)}`;
}

/**
 * The legend: every link type in the answer with its color, its synapse
 * count and a checkbox. Undirected kinds are marked, because Viking's
 * source and target for them are arbitrary and they are drawn without an
 * arrowhead. The legend is what the colors mean; the checkboxes are how to
 * leave adherens junctions out of a synaptic network.
 */
function renderNetworkLegend() {
  const legend = $('net-legend');
  const types = network.types();
  legend.replaceChildren();
  legend.hidden = !types.length;
  if (!types.length) return;
  const head = document.createElement('span');
  head.className = 'muted';
  head.textContent = 'Links:';
  head.dataset.help = 'Types are named from the loaded cell\'s side: ConvPost is a conventional synapse onto it, RibbonPre a ribbon synapse it makes. Arrows point from the presynaptic to the postsynaptic cell. Dashed connections are undirected links — gap junctions, adherens (Desmosome), touches, unknown — for which Viking records no direction. Untick a type to leave it out; weights and colors follow what is left.';
  legend.append(head);
  for (const t of types) {
    const label = document.createElement('label');
    label.className = `check net-type${t.directed ? '' : ' undirected'}`;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !networkHidden.has(t.type);
    box.addEventListener('change', () => {
      if (box.checked) networkHidden.delete(t.type); else networkHidden.add(t.type);
      drawNetwork();
    });
    const swatch = document.createElement('span');
    swatch.className = 'swatch';
    swatch.style.background = t.directed
      ? t.color
      : `repeating-linear-gradient(90deg, ${t.color} 0 4px, transparent 4px 7px)`;
    const name = document.createElement('span');
    name.textContent = t.type;
    const count = document.createElement('span');
    count.className = 'muted';
    count.textContent = ` ${t.synapses}`;
    count.title = `${t.synapses} synapse${t.synapses === 1 ? '' : 's'} of this type on ${t.edges} connection${t.edges === 1 ? '' : 's'}`;
    label.title = t.directed
      ? `${t.type}: directed, drawn with an arrow from pre to post`
      : `${t.type}: undirected, drawn dashed without an arrow`;
    label.append(box, swatch, name, count);
    legend.append(label);
  }
}

let strataFor = null;   // strataSignature() of the cells the Stratification chart shows
let strataRun = 0;      // the latest drawStratification; an older one stands down

/** The cells the chart is for: those shown, and the one a right-click asked for even if it is hidden. */
function strataRecords(asked = null) {
  const records = visibleRecords();
  const extra = asked ? state.neurons.get(asked.key) : null;
  return extra && !records.includes(extra) ? [...records, extra] : records;
}

/** What the Stratification chart is drawn from: those cells and their data. */
function strataSignature(records) {
  return records.map((r) => `${r.key}@${dataKey(r)}`).join(',');
}

/**
 * Chart the stratification of every shown cell (and `cell`, when a right-click
 * named one).  The Stratification tab draws on opening when the cells or their
 * data are not the ones it was drawn for, the way the Analyses and Annotations
 * tabs do: it drew once and kept that chart, so a right-click Analysis >
 * Stratification on a cell loaded after it changed nothing.
 */
async function drawStratification({ cell = null } = {}) {
  const records = strataRecords(cell);
  const run = ++strataRun;
  strataFor = strataSignature(records);
  if (!records.length) return;
  const opts = {
    num_bins: Number($('strata-bins').value),
    include_soma: $('strata-soma').checked,
    omit_outliers: $('strata-outliers').checked,
    ipl_method: currentIplMethod(),
  };
  const series = [];
  const stats = [];
  let failed = false;
  for (const record of records) {
    try {
      const result = await api.stratification(record.source, record.id, opts);
      // Computed again while this one was on its way: that draw is the one to show.
      if (run !== strataRun) return;
      series.push({
        label: cellLabel(record),
        color: record.color,
        centers: result.centers,
        counts: result.counts,
        median: result.stats.median,
      });
      stats.push({ record, result });
    } catch (error) {
      if (run !== strataRun) return;
      failed = true;
      fail(error);
    }
  }
  // Drawn again when this tab is next shown, rather than left looking complete.
  if (failed) strataFor = null;
  strataDrawn = { series, records: stats.map((s) => s.record), opts };
  stratificationChart($('strata-chart'), series, { tickPct: strataTickPct() });
  renderStrataStats(stats);
  showStrataMethod(records, opts.ipl_method, run);
}

/** Each volume's IPL thickness (GET /api/volumes/{source}/ipl-thickness), asked once per volume. */
const iplThickness = new Map();

/**
 * Under the chart: how its depths were measured (the Settings tab's choice), and
 * for each volume of the cells shown, how thick its IPL is along the layers'
 * normals and along Z, and where it is thinnest.
 */
async function showStrataMethod(records, method, run) {
  const note = $('strata-method');
  if (!note) return;
  const lines = [`IPL depth measured ${iplMethodWords(method)} (Settings).`];
  const sources = [...new Set(records.map((r) => r.source))].filter((src) => volumeHasBoundary(src));
  for (const source of sources) askIplThickness(source).catch(() => {});
  note.textContent = lines[0];
  for (const source of sources) {
    let t;
    try { t = await askIplThickness(source); } catch { continue; }
    if (run !== strataRun) return;
    lines.push(iplThicknessText(volumeLongName(source), t));
  }
  note.textContent = lines.join('\n');
}

/** One volume's IPL thickness in a line: the normals' median and range, Z's excess, the thinnest place. */
function iplThicknessText(name, t) {
  const n = t.normal_um;
  if (n?.median == null) return `${name}: no column has a marker of each layer nearby.`;
  const excess = (t.column_over_normal.median - 1) * 100;
  const where = t.thinnest
    ? `; thinnest ${t.thinnest.thickness_um.toFixed(1)} µm at X ${t.thinnest.x_um.toFixed(0)}, `
      + `Y ${t.thinnest.y_um.toFixed(0)} µm` : '';
  return `${name} IPL: ${n.median.toFixed(1)} µm thick along the normals (median; `
    + `${n.p5.toFixed(1)} to ${n.p95.toFixed(1)} µm for 90 % of it${where}); `
    + `along Z it reads ${excess.toFixed(1)} % thicker at the median, up to `
    + `${((t.column_over_normal.max - 1) * 100).toFixed(0)} %.`;
}

// -- the Stratification chart's gridlines and its files ----------------------------------
// The tick step is the page's choice (kept in this browser, 20 % by default) and
// redraws the chart without asking the server again; the PDF and the workbook are
// made on the server from the cells and settings the chart was drawn with
// (sbfsem/export/stratification.py), so they show what is on screen.

const STRATA_TICKS_KEY = 'sbfsem:strata-ticks';
const STRATA_PDF_KEY = 'sbfsem:strata-pdf';
/** Annotation rows on one PDF page (stratification.annotation_rows_per_page, letter). */
const STRATA_ROWS_PER_PAGE = 56;
/** The most annotation rows the PDF writes (stratification.PDF_MAX_ANNOTATION_ROWS). */
const STRATA_PDF_MAX_ROWS = 20000;
/** What the chart was last drawn from: `{ series, records, opts }`. */
let strataDrawn = null;

/** The gridline step typed in the box, 1 to 100 %; the default for anything else. */
function strataTickPct() {
  const value = Number($('strata-ticks').value);
  return Number.isFinite(value) && value >= 1 && value <= 100 ? value : STRATA_TICK_PCT;
}

function storedJSON(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
}

function storeValue(key, value) {
  try { localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value)); }
  catch { /* not kept: this page still uses it */ }
}

/** The cells and settings the chart was drawn with, as the export routes take them. */
function strataExportBody() {
  const records = (strataDrawn?.records || []).filter((r) => state.neurons.has(r.key));
  if (!records.length) return null;
  return {
    cells: records.map((r) => ({
      source: r.source, id: r.id, copy_tag: r.summary?.copy_tag ?? null, label: cellLabel(r),
      color: /^#[0-9a-f]{6}$/i.test(r.color || '') ? r.color : '#4c78a8',
    })),
    ...strataDrawn.opts,
    tick_pct: strataTickPct(),
  };
}

async function exportStrata(format, extra = {}) {
  const body = strataExportBody();
  if (!body) {
    statusEl.textContent = 'Compute the Stratification chart first';
    statusEl.className = 'status error';
    return false;
  }
  try {
    const { filename } = await api.stratificationExport(format, { ...body, ...extra });
    statusEl.textContent = `Saved ${filename}`;
    statusEl.className = 'status';
    return true;
  } catch (error) {
    fail(error);
    return false;
  }
}

function bindStrataExports() {
  const ticks = $('strata-ticks');
  const stored = Number(storedJSON(STRATA_TICKS_KEY));
  ticks.value = String(Number.isFinite(stored) && stored >= 1 && stored <= 100 ? stored : STRATA_TICK_PCT);
  ticks.addEventListener('change', () => {
    ticks.value = String(strataTickPct());
    storeValue(STRATA_TICKS_KEY, ticks.value);
    if (strataDrawn) stratificationChart($('strata-chart'), strataDrawn.series, { tickPct: strataTickPct() });
  });
  $('strata-xlsx').addEventListener('click', () => exportStrata('xlsx'));

  const dialog = $('strata-pdf-dialog');
  const stats = $('strata-pdf-stats');
  const every = $('strata-pdf-annotations');
  $('strata-pdf').addEventListener('click', () => {
    const body = strataExportBody();
    if (!body) {
      exportStrata('pdf');
      return;
    }
    const chosen = storedJSON(STRATA_PDF_KEY) || {};
    const records = strataDrawn.records.filter((r) => state.neurons.has(r.key));
    const rows = records.reduce((sum, r) => sum + (r.summary?.n_nodes || 0), 0);
    const pages = records.reduce(
      (sum, r) => sum + Math.max(1, Math.ceil((r.summary?.n_nodes || 0) / STRATA_ROWS_PER_PAGE)), 0);
    const tooMany = rows > STRATA_PDF_MAX_ROWS;
    stats.checked = chosen.statistics ?? true;
    every.checked = !tooMany && (chosen.annotations ?? false);
    every.disabled = tooMany;
    const blocks = Math.ceil(records.length / 5);
    $('strata-pdf-stats-pages').textContent = blocks > 1 ? `(about ${1 + blocks} pages)` : '(1 or 2 pages)';
    $('strata-pdf-annotation-pages').textContent = tooMany
      ? `(${rows.toLocaleString('en-US')} annotations, more than a PDF takes: Data .xlsx has them all)`
      : `(about ${pages.toLocaleString('en-US')} page${pages === 1 ? '' : 's'})`;
    $('strata-pdf-error').hidden = true;
    dialog.showModal();
  });
  $('strata-pdf-create').addEventListener('click', async () => {
    const choice = { statistics: stats.checked, annotations: every.checked };
    storeValue(STRATA_PDF_KEY, choice);
    const button = $('strata-pdf-create');
    button.disabled = true;
    try {
      if (await exportStrata('pdf', choice)) dialog.close('done');
      else {
        $('strata-pdf-error').textContent = statusEl.textContent;
        $('strata-pdf-error').hidden = false;
      }
    } finally {
      button.disabled = false;
    }
  });
}

/** A count prints as a whole number: `fmt` gives a count the precision of a measurement ("2202.0", "0.000"). */
function fmtCount(value) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString('en-US') : fmt(value);
}

function renderStrataStats(entries) {
  const grid = $('strata-stats');
  grid.replaceChildren();
  for (const { record, result } of entries) {
    for (const [label, value, format] of [
      ['n', result.stats.n, fmtCount],
      ['median', result.stats.median, fmt],
      ['mean', result.stats.mean, fmt],
      ['SEM', result.stats.sem, fmt],
      ['mode', result.mode, fmt],
      ['outliers', result.n_omitted_outlier, fmtCount],
      ['outside surfaces', result.n_omitted_nan, fmtCount],
    ]) {
      const dl = document.createElement('dl');
      dl.className = 'stat';
      const name = document.createElement('dt');
      name.textContent = `${cellLabel(record)} ${label}`;
      const number = document.createElement('dd');
      number.textContent = format(value);
      dl.append(name, number);
      grid.append(dl);
    }
  }
}

/**
 * Give a select these `[label, value]` options, rebuilding only when they are not
 * the ones it has: replacing the options under an open dropdown closes it, and
 * the pickers on the other tabs are refilled on every selection event.
 */
function setOptions(select, entries) {
  if (sameOptions(select.options, entries)) return;
  select.replaceChildren(...entries.map(([label, value]) => new Option(label, value)));
}

function populateTableSelect() {
  const select = $('table-neuron');
  const previous = select.value;
  // Named with the volume as well: IDs are numbered per volume (ground rule 11).
  setOptions(select, [...state.neurons.values()].map(
    (r) => [`c${r.id} · ${volumeAbbrev(r.source)} ${r.summary.label || ''}`.trim(), r.key]));
  if ([...state.neurons.keys()].includes(previous)) select.value = previous;
  else if (state.selected) select.value = state.selected;
}

async function drawTable() {
  const run = ++tableRun;
  const key = $('table-neuron').value || state.selected;
  const record = key ? state.neurons.get(key) : null;
  const table = $('node-table');
  tableFor = tableSignature();
  if (!record) {
    table.tHead.replaceChildren();
    table.tBodies[0].replaceChildren();
    $('table-count').textContent = '';
    return;
  }
  try {
    const nodes = await api.nodes(record.source, record.id, $('table-cellonly').checked);
    // Another cell, or the same one drawn at other positions, was asked for
    // while these were on their way: that draw is the one to show.
    if (run !== tableRun) return;
    // x, y and z are where the cell is drawn (the current transforms and the section
    // correction applied, when the cell has them on).  When either moved an
    // annotation, the position Viking stored for it and the distance between the two
    // are beside it: the table of a corrected cell shows both ends of the move.
    const moved = nodes.some((n) => n.shift_um || n.live_shift_um);
    const columns = ['id', 'parent_id', 'z_section', 'x', 'y', 'z', 'radius_um',
      'off_edge', 'terminal', 'unique', ...(moved ? ['x_stored', 'y_stored', 'moved_um'] : [])];
    table.tHead.replaceChildren(row('th', columns.map(headerLabel)));
    table.tBodies[0].replaceChildren(...nodes.map((n) => {
      const [sx, sy] = stagePositions(n).stored;
      const tr = row('td', [
        n.id, n.parent_id, n.z_section,
        n.xyz_um[0].toFixed(3), n.xyz_um[1].toFixed(3), n.xyz_um[2].toFixed(3),
        n.radius_um.toFixed(4), n.off_edge ? '✓' : '', n.terminal ? '✓' : '', n.unique ? '✓' : '',
        ...(moved ? [sx.toFixed(3), sy.toFixed(3),
          Math.hypot(n.xyz_um[0] - sx, n.xyz_um[1] - sy).toFixed(4)] : []),
      ]);
      // Carried on the row so the context menu needs no second lookup.
      tr.dataset.xyz = n.xyz_um.join(',');
      tr.dataset.annotationId = n.id;
      tr.dataset.neuronKey = record.key;
      tr.dataset.radius = n.radius_um;
      return tr;
    }));
    $('table-count').textContent = `${nodes.length} annotations`;
    filterTable();
  } catch (error) {
    // Drawn again when this tab is next shown, rather than left looking current.
    if (run === tableRun) tableFor = null;
    fail(error);
  }
}

function filterTable() {
  const needle = $('table-filter').value.trim();
  let shown = 0;
  for (const tr of $('node-table').tBodies[0].rows) {
    const match = !needle || tr.cells[0].textContent.includes(needle);
    tr.hidden = !match;
    if (match) shown += 1;
  }
  if (needle) $('table-count').textContent = `${shown} matching annotations`;
}

const headerLabel = (key) => key.replace(/_/g, ' ');

function row(cell, values) {
  const tr = document.createElement('tr');
  for (const value of values) {
    const td = document.createElement(cell);
    td.textContent = value;
    tr.append(td);
  }
  return tr;
}

async function renderAnalyses() {
  // A render for the cell before this one may still be waiting on the server
  // when the next starts; it stands down at its next step instead of
  // appending its cards under the new cell's.
  const run = ++analysesRun;
  const superseded = () => run !== analysesRun;
  const container = $('analysis-cards');
  const record = selectedRecord();
  analysesFor = analysesSignature();
  container.replaceChildren();
  if (!record) {
    container.innerHTML = '<p class="empty">Load a neuron to run analyses.</p>';
    return;
  }

  container.append(summaryCard(record), exportCard(record));

  const morphology = card(`Morphology — c${record.id}`);
  container.append(morphology);
  try {
    const m = await cellMetrics(record);
    if (superseded()) return;
    morphology.append(kvTable(METRIC_ROWS.map(({ label, help, cell }) => [label, cell(m), help])));
    morphology.append(howComputed());
  } catch (error) { if (!superseded()) fail(error); }
  if (superseded()) return;

  // Every loaded cell side by side, whatever volume it came from: the table
  // for "dendritic length per cell, compared across volumes".
  if (state.neurons.size > 1) {
    const all = card(`Loaded cells — ${state.neurons.size}`);
    all.classList.add('wide');
    container.append(all);
    fillLoadedCellsCard(all).catch((error) => { if (!superseded()) fail(error); });
  }

  const diameter = card(`Dendrite diameter — c${record.id}`);
  container.append(diameter);
  try {
    const result = await api.dendriteDiameter(record.source, record.id);
    if (superseded()) return;
    const holder = document.createElement('div');
    diameter.append(holder);
    barChart(holder, result.rows, {
      xKey: 'distance', yKey: 'mean', binWidth: result.bin_width_um,
      xLabel: 'Distance from soma (µm)', yLabel: 'Mean diameter (µm)', color: record.color,
    });
  } catch (error) { if (!superseded()) fail(error); }
  if (superseded()) return;

  // getAllLinkedNeurons: every synapse, its partner, its position — the
  // table the lab exports from MATLAB, with the volume named in the file.
  const links = card(`Linked neurons — c${record.id} (${volumeName(record.source)})`);
  links.classList.add('wide');
  container.append(links);
  await fillLinkedNeuronsCard(links, record, {
    api, pick: pickStructure, volumeName,
    vikingOf: ([x, y, z]) => vikingFromUm({ x, y, z }, pickScale(record)),
  });
}

// -- morphometrics ---------------------------------------------------------
// Every number on the Morphology card, with the definition it is computed by
// (asked for on 2026-09-17: "I get a little nervous not knowing how numbers
// are calculated"). The same list feeds the tooltips, the details block and
// the loaded-cells table, so the three cannot drift apart.

const um = (v) => `${fmt(v)} µm`;
const METRIC_ROWS = [
  { key: 'field_area_um2', label: 'Dendritic field area', csv: 'field_area_um2',
    cell: (m) => `${fmt(m.field_area_um2)} µm²`,
    help: 'Area of the convex hull around every cell-body annotation, projected onto the section (XY) plane. Synapse annotations are not part of it.' },
  { key: 'equivalent_diameter_um', label: 'Equivalent diameter', csv: 'equivalent_diameter_um',
    cell: (m) => um(m.equivalent_diameter_um),
    help: 'Diameter of the circle with the same area as the dendritic field hull: 2·√(area/π).' },
  { key: 'perimeter_um', label: 'Field perimeter', csv: 'field_perimeter_um',
    cell: (m) => um(m.perimeter_um),
    help: 'Perimeter of that convex hull.' },
  { key: 'surface_area_um2', label: 'Surface area', csv: 'surface_area_um2',
    cell: (m) => `${fmt(m.surface_area_um2)} µm²`,
    help: 'Each cell-body annotation contributes the side of a disc of its radius, one section thick (2·π·r·Δz), summed. The MATLAB surfaceArea figure; it is not the area of the rendered mesh.' },
  { key: 'total_length_um', label: 'Total cable length', csv: 'total_length_um',
    cell: (m) => um(m.total_length_um),
    help: 'Length of the traced centerline: the straight-line distance between every pair of linked cell-body annotations, summed in 3-D. This is the skeleton the SWC export writes; nothing is smoothed or resampled.' },
  { key: 'rim_corrected_length_um', label: 'Cable length from the rims', csv: 'rim_corrected_length_um',
    cell: (m) => `${um(m.rim_corrected_length_um)} (−${um(m.spoke_length_um)})`,
    help: 'Total cable length minus the spokes. A thin process is linked to the CENTER of the wide profile it leaves, so its first link runs inside that profile; this counts only the part of each such link outside the profile — clipped exactly against its traced outline, or its radius for a circle. A link is a spoke when one end is at least 3× as wide as the other. Reported beside the uncorrected length, never instead of it; with stored radii as the rims (all an SWC allows) the correction is smaller, and the Skeleton check shows both.' },
  { key: 'dendritic_length_um', label: 'Dendritic length', csv: 'dendritic_length_um',
    cell: (m) => um(m.dendritic_length_um),
    help: 'Total cable length minus the links that run between two soma annotations. A soma annotation is one whose radius is at least 80% of the cell\'s largest, the rule the SWC export and the diameter profile use.' },
  { key: 'dendritic_length_xy_um', label: 'Dendritic length, in plane', csv: 'dendritic_length_xy_um',
    cell: (m) => um(m.dendritic_length_xy_um),
    help: 'Dendritic length measured in the section plane only. Section-to-section registration jitter adds Z steps that are not real cable, so this is the conservative figure for comparing cells across volumes.' },
  { key: 'longest_path_um', label: 'Longest path from soma', csv: 'longest_path_um',
    cell: (m) => um(m.longest_path_um),
    help: 'The longest route along the skeleton from the soma annotation to any annotation, using the 3-D link lengths.' },
  { key: 'n_branch_points', label: 'Branch points / tips', csv: 'n_branch_points',
    cell: (m) => `${m.n_branch_points} / ${m.n_tips}`,
    help: 'Branch points are annotations linked to more than two others; tips are annotations linked to exactly one other, the soma excluded.' },
  { key: 'n_components', label: 'Fragments', csv: 'n_fragments',
    cell: (m) => String(m.n_components),
    help: 'Connected pieces of the annotation graph. More than one means Viking holds parts of this cell that are not linked to the rest: the lengths count every piece, but paths from the soma cannot reach them.' },
  { key: 'ie_ratio', label: 'I:E ratio', csv: 'ie_ratio',
    cell: (m) => fmt(m.ie_ratio),
    help: 'Conventional postsynapses (ConvPost) divided by ribbon postsynapses (RibbonPost), counted as synapse structures on this cell. Gap junctions, adherens, presynaptic sites and every other type play no part. Blank when the cell has no ribbon inputs. A postsynapse is ribbon or conventional by its Viking tags, in any order; one with no such tag takes the type of the structure it is linked to (see Untagged PSDs), and one with neither is left out rather than assumed conventional.' },
  { key: 'inhibitory_fraction', label: 'Inhibitory fraction', csv: 'inhibitory_fraction',
    cell: (m) => fmt(m.inhibitory_fraction),
    help: 'ConvPost / (ConvPost + RibbonPost): what the MATLAB ieRatio actually computed under the name "ratio".' },
  { key: 'n_excitatory', label: 'Excitatory / inhibitory', csv: 'n_ribbon_post',
    cell: (m) => `${m.n_excitatory} / ${m.n_inhibitory}`,
    help: 'The two counts the ratios are made from: RibbonPost synapses and ConvPost synapses on this cell.' },
  { key: 'n_untagged', label: 'Untagged PSDs', csv: 'n_untagged_psd',
    cell: (m) => (m.n_inferred ? `${m.n_untagged} · ${m.n_inferred} inferred` : String(m.n_untagged ?? 0)),
    help: 'Postsynapses whose Viking tags say neither ribbon nor conventional. "Inferred" ones took the type of the structure they are linked to (a ribbon makes RibbonPost, a conventional synapse ConvPost) and are in the counts above; the rest are in neither count. Their tags are still missing in Viking: the Linked neurons card on the Analyses tab lists them with a Location ID each.' },
  { key: 'unfinished_branches', label: 'Unfinished branches', csv: 'n_unfinished_branches',
    cell: (m) => String(m.unfinished_branches),
    help: 'Cell-body annotations with at most one link that the annotator did not tag Terminal in Viking. Off Edge ends count, since the branch continues beyond what was traced; a stranded single annotation counts too. Synapse annotations never do.' },
];

/** Extra columns the loaded-cells table and its .csv carry beyond METRIC_ROWS. */
const METRIC_CSV_EXTRA = [
  ['volume', (m) => m.volume], ['cell', (m) => m.id], ['label', (m) => m.label],
  ['n_tips', (m) => m.n_tips], ['n_conv_post', (m) => m.n_inhibitory],
  ['n_inferred_psd', (m) => m.n_inferred],
  ['n_annotations', (m) => m.n_annotations], ['soma_radius_um', (m) => m.soma_radius_um],
  ['extent_x_um', (m) => m.extent_um[0]], ['extent_y_um', (m) => m.extent_um[1]],
  ['extent_z_um', (m) => m.extent_um[2]],
];

const metricsCache = new Map();  // key -> { stamp, metrics }

/**
 * Every Morphology number for one cell, fetched once per copy of its data: the
 * annotations' stamp and the server's copy for this tab's Registration and
 * correction choices (dataKey).  The stamp alone is the same for every copy, so
 * c1178's cable length stayed 1072.9 µm after the curve fit that makes it
 * 895.6.
 */
async function cellMetrics(record) {
  const stamp = dataKey(record);
  const hit = metricsCache.get(record.key);
  if (hit && hit.stamp === stamp) return hit.metrics;
  const [hull, area, ie, open, morph] = await Promise.all([
    api.hull(record.source, record.id),
    api.surfaceArea(record.source, record.id),
    api.ieRatio(record.source, record.id),
    api.openEnded(record.source, record.id),
    api.morphometrics(record.source, record.id),
  ]);
  const metrics = {
    ...morph,
    id: record.id,
    source: record.source,
    volume: volumeName(record.source),
    label: record.summary?.label || morph.label || '',
    field_area_um2: hull.area_um2,
    equivalent_diameter_um: hull.equivalent_diameter_um,
    perimeter_um: hull.perimeter_um,
    surface_area_um2: area.values[0],
    ie_ratio: ie.ratio,
    inhibitory_fraction: ie.inhibitory_fraction,
    n_excitatory: ie.n_excitatory,
    n_inhibitory: ie.n_inhibitory,
    n_untagged: ie.n_untagged,
    n_inferred: ie.n_inferred,
    unfinished_branches: open.extra.location_ids.length,
  };
  metricsCache.set(record.key, { stamp, metrics });
  return metrics;
}

/** The definitions behind the Morphology card, readable without hovering. */
function howComputed() {
  const details = document.createElement('details');
  details.className = 'how';
  const summary = document.createElement('summary');
  summary.textContent = 'How these are computed';
  details.append(summary);
  for (const { label, help } of METRIC_ROWS) {
    const p = document.createElement('p');
    const b = document.createElement('b');
    b.textContent = `${label}. `;
    p.append(b, document.createTextNode(help));
    details.append(p);
  }
  const note = document.createElement('p');
  note.textContent = 'All lengths follow the annotation centerline as traced: a broad process '
    + 'annotated with large overlapping discs across sections zigzags between their centers, '
    + 'and the in-plane figure is the one least affected.';
  details.append(note);
  const angles = document.createElement('p');
  angles.textContent = 'Directions are not corrected by any of this. A branch is linked to the '
    + 'CENTER of the profile it leaves, so it starts there and departs on a bearing no real '
    + 'process takes; the length from the rims fixes the length only. Do not read branch angles, '
    + 'tortuosity near a junction, or the direction a process leaves a junction off these '
    + 'skeletons without allowing for that. The Skeleton check in a cell’s panel gives the floor '
    + 'under the cable length: the shortest path that still passes through every annotation.';
  details.append(angles);
  return details;
}

const csvCell = (v) => {
  if (v === null || v === undefined || Number.isNaN(v)) return '';
  const text = typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

/** The loaded-cells table: one row per cell, every volume, with a .csv. */
async function fillLoadedCellsCard(node) {
  const records = [...state.neurons.values()];
  const status = document.createElement('p');
  status.className = 'muted';
  status.textContent = `Measuring ${records.length} cells…`;
  node.append(status);
  const rows = [];
  const failures = [];
  await inParallel(records, LOAD_CONCURRENCY, async (record) => {
    try { rows.push(await cellMetrics(record)); } catch (error) { failures.push(`c${record.id}`); }
  });
  status.remove();
  if (!rows.length) {
    const p = document.createElement('p');
    p.className = 'muted error';
    p.textContent = `No cell could be measured (${failures.join(', ')})`;
    node.append(p);
    return;
  }

  const columns = [
    { key: 'volume', label: 'Volume' },
    { key: 'id', label: 'Cell', numeric: true },
    { key: 'label', label: 'Label' },
    ...METRIC_ROWS
      .filter((r) => r.key !== 'n_excitatory')
      .map((r) => ({ key: r.key, label: r.label, numeric: true, help: r.help, cell: r.cell })),
  ];
  const tools = document.createElement('div');
  tools.className = 'card-tools';
  const note = document.createElement('span');
  note.className = 'muted';
  const volumes = new Set(rows.map((r) => r.source)).size;
  note.textContent = `${rows.length} cells from ${volumes} volume${volumes === 1 ? '' : 's'}`
    + (failures.length ? ` · not measured: ${failures.join(', ')}` : '')
    + ' · click a heading to sort · hover one for its definition';
  const csvBtn = document.createElement('button');
  csvBtn.type = 'button';
  csvBtn.textContent = 'Download .csv';
  csvBtn.title = 'Every column of this table plus tips, annotation count, soma radius and extent, one row per cell';
  csvBtn.addEventListener('click', () => {
    const header = [...METRIC_CSV_EXTRA.slice(0, 3).map(([k]) => k),
      ...METRIC_ROWS.map((r) => r.csv), ...METRIC_CSV_EXTRA.slice(3).map(([k]) => k)];
    const line = (m) => [
      ...METRIC_CSV_EXTRA.slice(0, 3).map(([, f]) => csvCell(f(m))),
      ...METRIC_ROWS.map((r) => csvCell(m[r.key])),
      ...METRIC_CSV_EXTRA.slice(3).map(([, f]) => csvCell(f(m))),
    ].join(',');
    const text = [header.join(','), ...rows.map(line)].join('\n') + '\n';
    const blob = new Blob([text], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'loaded-cells-morphometrics.csv';
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
  tools.append(csvBtn, note);
  node.append(tools);

  const scroll = document.createElement('div');
  scroll.className = 'report-scroll';
  const table = document.createElement('table');
  table.className = 'report metrics';
  const sort = { key: null, dir: 1 };
  const tbody = document.createElement('tbody');
  const draw = () => {
    mark();
    const shown = sort.key ? sortRows(rows, columns.find((c) => c.key === sort.key), sort.dir) : rows;
    tbody.replaceChildren();
    for (const m of shown) {
      const tr = document.createElement('tr');
      for (const c of columns) {
        const td = document.createElement('td');
        if (c.key === 'id') {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'cx-id';
          btn.textContent = `c${m.id}`;
          btn.title = `Select c${m.id} (${m.volume})`;
          btn.addEventListener('click', () => {
            // A row of a cell unloaded since (the table is of the cells loaded when it
            // was drawn) names nothing to focus: focusing it left the tabs describing no cell.
            const key = keyOf(m.source, m.id);
            if (state.neurons.has(key)) selectNeuron(key);
          });
          td.append(btn);
        } else if (c.cell) {
          td.textContent = c.cell(m);
        } else {
          td.textContent = m[c.key] ?? '';
          td.className = 'text';
        }
        tr.append(td);
      }
      tbody.append(tr);
    }
  };
  const { head, mark } = sortableHead(columns, sort, draw);
  for (const th of head.children) {
    const column = columns.find((c) => c.key === th.dataset.key);
    if (column?.help) th.dataset.help = `${column.help} Click to sort.`;
  }
  const thead = document.createElement('thead');
  thead.append(head);
  table.append(thead, tbody);
  draw();
  scroll.append(table);
  node.append(scroll);
}

/**
 * The server's display name of a volume ("Cped"), the wire name when the list is
 * not loaded yet: for pickers, tables and the .csv, where a short stable name
 * is wanted.  `volumeLongName` spells a volume out where there is room for it.
 */
function volumeName(source) {
  return state.volumes?.find((v) => v.name === source)?.display_name || source;
}

/**
 * Make `source` the working volume: the state, the Volume select, and what
 * belongs to one volume only -- the Display panel's boundary sheets and
 * vasculature, and the Find results.  The one place a volume is switched (the
 * select, an open link, Undo, a restored session, Import): five copies of it
 * had drifted, and a link or an undo left the old volume's sheets on screen.
 * Answers whether the volume changed.
 */
function switchVolume(source, { quiet = false } = {}) {
  if (!source || source === state.source) return false;
  // `quiet`: a restore or an undo, which save what they end with themselves.
  if (quiet) state.source = source;
  else setSource(source);
  $('volume').value = source;
  // Boundary sheets and vasculature belong to one volume.
  display?.volumeChanged();
  clearFind();
  return true;
}

/**
 * Put a structure in the Structure ID box together with its volume (ground
 * rule 11: an ID means nothing without the volume it was numbered in).
 */
function pickStructure(source, id) {
  switchVolume(source);
  $('neuron-id').value = id;
  $('neuron-id').focus();
  statusEl.textContent = `c${id} (${volumeName(source)}) is in the Structure ID box — Load to add it`;
  statusEl.className = 'status';
}

function summaryCard(record) {
  const node = card(`c${record.id} — ${record.summary.label || 'unlabeled'}`);
  node.append(kvTable([
    ['Volume', volumeLongName(record.source)],
    ['Annotations', record.summary.n_nodes],
    ['Edges', record.summary.n_edges],
    ['Synapse structures', record.summary.n_synapses],
    ['Soma radius', record.summary.soma_radius_um ? `${fmt(record.summary.soma_radius_um)} µm` : '—'],
    ['Closed curves', record.summary.has_geometry ? 'yes' : 'no'],
  ]));
  if (record.synapseCounts.length) {
    // One row per Viking type, named as the volume names it when this tool
    // has no name of its own ("Unknown 2 (PeriGJAdherens)" read as if the
    // type were unknown -- Crystal, 2026-09-17).
    node.append(kvTable(record.synapseCounts.map((s) => {
      const label = s.local_name === 'Unknown' && s.viking_name ? s.viking_name : s.local_name;
      let value = s.viking_name && s.viking_name !== label
        ? `${s.count} (${s.viking_name})` : String(s.count);
      let help = s.local_name === 'Unknown'
        ? `Viking type "${s.viking_name}". This tool has no name of its own for it, so on the render it is grouped under Unknown.`
        : `Viking type "${s.viking_name}"; this tool's name for it is ${s.local_name}.`;
      // Postsynapses whose tags do not say what they are (Crystal, 2026-09-22).
      if (s.local_name === 'UntaggedPost') {
        help = 'Postsynapses whose Viking tags say neither ribbon nor conventional, and whose linked '
          + 'structure does not say either. They are counted as neither; the Linked neurons card '
          + 'lists them with a Location ID each.';
      } else if (s.inferred) {
        value += ` · ${s.inferred} inferred`;
        help += ` ${s.inferred} of them have no tag that says so in Viking; their type was taken from the structure they are linked to.`;
      } else if (s.assumed) {
        value += ` · ${s.assumed} assumed`;
        help += ` ${s.assumed} of them have no tag that says so in Viking and were assumed conventional.`;
      }
      return [label, value, help];
    })));
  }
  return node;
}

// The mesh and skeleton formats of the Analyses export card; the right-click
// Export menu offers the same ones.
const EXPORT_FORMATS = ['swc', 'json', 'dae', 'stl', 'obj', 'ply', 'glb'];

function exportCard(record) {
  const node = card('Export');
  const actions = document.createElement('div');
  actions.className = 'actions';
  for (const fmtName of EXPORT_FORMATS) {
    const link = document.createElement('a');
    // The neuron's own settings, not the panel's: an export should match
    // what is on screen, pathway and all.  Worked out again when the link is
    // used (and kept current for "copy link"): the card is built once per
    // cell, and its Registration or render may change under it.
    const refresh = () => { link.href = exportRequest(record, fmtName).url; };
    refresh();
    link.addEventListener('pointerenter', refresh);
    link.addEventListener('focus', refresh);
    link.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      exportCell(record, fmtName);
    });
    if (fmtName === 'swc') link.title = SWC_HINT;
    link.textContent = `.${fmtName}`;
    link.className = 'muted';
    link.download = exportRequest(record, fmtName).filename;
    actions.append(link);
  }
  node.append(actions);
  return node;
}

function card(title) {
  const node = document.createElement('div');
  node.className = 'card';
  const h = document.createElement('h3');
  h.textContent = title;
  node.append(h);
  return node;
}

/** Key/value rows; a third element is the definition, shown on hover. */
function kvTable(rows) {
  const table = document.createElement('table');
  const body = document.createElement('tbody');
  for (const [key, value, help] of rows) {
    const tr = document.createElement('tr');
    const th = document.createElement('td');
    th.textContent = key;
    const td = document.createElement('td');
    td.textContent = value;
    if (help) {
      th.dataset.help = help;
      td.dataset.help = help;
      th.className = 'help';
    }
    tr.append(th, td);
    body.append(tr);
  }
  table.append(body);
  return table;
}

function fmt(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (typeof value !== 'number') return String(value);
  return Math.abs(value) >= 100 ? value.toFixed(1) : value.toPrecision(4);
}

// -- saving to the server --------------------------------------------------
// What gets written is the workspace, not the neuron data: which cells, in
// what colors, with which per-type synapse settings and where the camera was.
// The annotations themselves are already on the server, so storing them again
// would be megabytes to say something the volume and id already say.

function currentDocument() {
  const snap = session.snapshot(state, renderer, uiFlags());
  // The viewport is opt-in: a document saved without one opens framing the
  // cells instead of jumping to a stored camera.
  if (!$('save-camera').checked) snap.camera = null;
  // Not Hold: it is the page's, and opened elsewhere it left new cells off screen.
  return { kind: 'sbfsem-workspace', version: 1, ...session.withoutPageState(snap) };
}

function describeDocument() {
  const n = state.neurons.size;
  if (!n) return 'Nothing is loaded — there is no workspace to save yet.';
  const names = [...state.neurons.values()].slice(0, 4).map((r) => `c${r.id}`);
  const more = n > names.length ? `, +${n - names.length} more` : '';
  const camera = $('save-camera').checked ? ' and the viewport' : '';
  return `Saves ${n} cell${n === 1 ? '' : 's'} (${names.join(', ')}${more}), every render `
    + `and display setting${camera}. The annotations are not copied — they are `
    + 'already on the server.';
}

function dialogError(id, error) {
  const el = $(id);
  if (!error) { el.hidden = true; el.textContent = ''; return; }
  el.hidden = false;
  el.textContent = error instanceof ApiError ? error.detail : String(error.message || error);
}

// One browser per dialog: which folder is open and which document is picked.
const fileBrowser = {
  save: { folder: null, files: [] },
  open: { folder: null, files: [], picked: null },
};

/** Rebuild one dialog's folder pane, then its document pane. */
async function refreshFolders(mode, keep) {
  const errorId = mode === 'save' ? 'save-error' : 'open-error';
  let folders = [];
  try {
    folders = await api.saveFolders();
  } catch (error) { dialogError(errorId, error); }

  const browser = fileBrowser[mode];
  if (keep) browser.folder = keep;
  if (!folders.some((f) => f.name === browser.folder)) {
    browser.folder = folders.length ? folders[0].name : null;
  }

  const list = $(`${mode}-folder-list`);
  list.replaceChildren();
  if (!folders.length) {
    list.append(emptyRow(mode === 'save' ? 'Create a folder below' : 'Nothing saved yet'));
  }
  for (const f of folders) {
    const li = document.createElement('li');
    li.className = f.name === browser.folder ? 'active' : '';

    const name = document.createElement('span');
    name.className = 'folder-name';
    name.textContent = f.name;

    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = f.files;
    count.title = `${f.files} document${f.files === 1 ? '' : 's'}`;
    li.append(name, count);

    if (!f.files) {
      // Deletable only when empty, which is also the server's rule.
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'icon-btn';
      remove.textContent = '✕';
      remove.title = `Delete the empty folder ${f.name}`;
      remove.addEventListener('click', async (event) => {
        event.stopPropagation();
        try {
          await api.saveDeleteFolder(f.name);
          await refreshFolders(mode);
        } catch (error) { dialogError(errorId, error); }
      });
      li.append(remove);
    }

    li.addEventListener('click', () => {
      browser.folder = f.name;
      refreshFolders(mode);
    });
    list.append(li);
  }
  await refreshFiles(mode);
  return folders;
}

/** Rebuild one dialog's document pane for its open folder. */
async function refreshFiles(mode) {
  const browser = fileBrowser[mode];
  const errorId = mode === 'save' ? 'save-error' : 'open-error';
  const list = $(`${mode}-file-list`);
  const count = $(`${mode}-file-count`);
  list.replaceChildren();
  browser.files = [];
  if (mode === 'open') { browser.picked = null; updateOpenControls(); }

  if (!browser.folder) {
    count.textContent = '';
    if (mode === 'save') updateSaveButton();
    return;
  }
  let docs = [];
  try {
    docs = await api.saveList(browser.folder);
  } catch (error) { dialogError(errorId, error); return; }
  browser.files = docs.map((d) => d.name);
  count.textContent = docs.length || '';
  if (mode === 'save') updateSaveButton();
  if (!docs.length) { list.append(emptyRow('This folder is empty')); return; }

  for (const doc of docs) {
    const li = document.createElement('li');
    li.dataset.name = doc.name;

    const name = document.createElement('span');
    name.className = 'doc-name';
    name.textContent = doc.name;

    const meta = document.createElement('span');
    meta.className = 'doc-meta';
    const when = new Date(doc.modified);
    meta.textContent = `${Math.max(1, Math.round(doc.bytes / 1024))} kB · `
      + `${when.toLocaleDateString()} `
      + when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-btn';
    remove.textContent = '✕';
    remove.title = `Delete ${doc.name}`;
    remove.addEventListener('click', async (event) => {
      event.stopPropagation();
      try {
        await api.saveDelete(browser.folder, doc.name);
        // Folder counts changed with the file, so rebuild both panes.
        await refreshFolders(mode);
      } catch (error) { dialogError(errorId, error); }
    });

    li.append(name, meta, remove);
    if (mode === 'open') {
      li.addEventListener('click', () => pickDocument(li, doc.name));
      li.addEventListener('dblclick', () => openDocument(browser.folder, doc.name));
    } else {
      // Clicking an existing document is "save over this one".
      li.addEventListener('click', () => {
        $('save-name').value = doc.name;
        markSavePick();
      });
    }
    list.append(li);
  }
  if (mode === 'save') markSavePick();
}

/** Highlight the document the current name would overwrite, if any. */
function markSavePick() {
  const name = $('save-name').value.trim();
  for (const li of $('save-file-list').children) {
    li.classList.toggle('active', !!li.dataset.name && li.dataset.name === name);
  }
  updateSaveButton();
}

function updateSaveButton() {
  const name = $('save-name').value.trim();
  const exists = fileBrowser.save.files.includes(name);
  const button = $('save-confirm');
  button.textContent = exists ? 'Overwrite' : 'Save';
  button.title = exists
    ? `Replace the existing ${fileBrowser.save.folder}/${name}` : '';
}

/** Select a document in the Open dialog and peek inside it. */
async function pickDocument(li, name) {
  fileBrowser.open.picked = name;
  for (const row of $('open-file-list').children) {
    row.classList.toggle('active', row === li);
  }
  updateOpenControls();
  const details = $('open-details');
  details.textContent = 'Reading…';
  try {
    const doc = await api.saveRead(fileBrowser.open.folder, name);
    if (fileBrowser.open.picked !== name) return; // the user moved on
    const cells = Array.isArray(doc.neurons) ? doc.neurons : [];
    const names = cells.slice(0, 6).map((n) => `c${n.id}`);
    const more = cells.length > names.length ? `, +${cells.length - names.length} more` : '';
    const volume = state.volumes.find((v) => v.name === doc.source)?.display_name
      || doc.source || 'an unknown volume';
    const when = doc.saved_at ? new Date(doc.saved_at).toLocaleString() : 'an unknown date';
    details.textContent = `${cells.length} cell${cells.length === 1 ? '' : 's'}`
      + `${names.length ? ` (${names.join(', ')}${more})` : ''} in ${volume}, saved ${when}. `
      + (doc.camera
        ? 'Opens at its captured viewport.'
        : 'No viewport captured — opens framing the cells.');
  } catch (error) {
    details.textContent = '';
    dialogError('open-error', error);
  }
}

function updateOpenControls() {
  $('open-confirm').disabled = !fileBrowser.open.picked;
  if (!fileBrowser.open.picked) {
    $('open-details').textContent = 'Select a document to see what is in it.';
  }
}

function bindSaveDialogs() {
  const saveDlg = $('save-dialog');
  const openDlg = $('open-dialog');

  $('save-store').addEventListener('click', async () => {
    dialogError('save-error', null);
    $('save-summary').textContent = describeDocument();
    $('save-name').value = defaultDocumentName();
    await refreshFolders('save');
    saveDlg.showModal();
  });

  $('save-name').addEventListener('input', markSavePick);
  $('save-camera').addEventListener('change', () => {
    $('save-summary').textContent = describeDocument();
  });

  // Once at a time too: the second of two Enters is answered "already exists",
  // which would sit in the dialog beside a folder that was created.
  $('save-create-folder').addEventListener('click', singleFlight(async () => {
    const name = $('save-new-folder').value.trim();
    if (!name) return;
    try {
      await api.saveCreateFolder(name);
      $('save-new-folder').value = '';
      await refreshFolders('save', name);
      dialogError('save-error', null);
    } catch (error) { dialogError('save-error', error); }
  }));

  // One write at a time: the dialog stays open until the server answers, so a
  // double Enter or a double click started a second write of the same document
  // (the server answered one of the two with a 500).
  const confirmSave = $('save-confirm');
  confirmSave.addEventListener('click', singleFlight(async () => {
    const folder = fileBrowser.save.folder;
    const name = $('save-name').value.trim();
    if (!folder) { dialogError('save-error', new Error('Create a folder first')); return; }
    if (!name) { dialogError('save-error', new Error('Give the document a name')); return; }
    confirmSave.disabled = true;
    try {
      const written = await api.saveWrite(folder, name, currentDocument());
      saveDlg.close();
      statusEl.textContent =
        `Saved ${folder}/${written.name} (${Math.max(1, Math.round(written.bytes / 1024))} kB)`;
      statusEl.className = 'status';
    } catch (error) {
      dialogError('save-error', error);
    } finally {
      confirmSave.disabled = false;
    }
  }));

  $('save-open').addEventListener('click', async () => {
    dialogError('open-error', null);
    await refreshFolders('open');
    openDlg.showModal();
  });

  $('open-confirm').addEventListener('click', () => {
    const { folder, picked } = fileBrowser.open;
    if (picked) openDocument(folder, picked);
  });
}

function defaultDocumentName() {
  const now = new Date();
  const stamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    + `-${String(now.getDate()).padStart(2, '0')}`;
  const first = [...state.neurons.values()][0];
  return first ? `c${first.id} ${stamp}` : stamp;
}

function emptyRow(text) {
  const li = document.createElement('li');
  li.className = 'empty';
  li.textContent = text;
  return li;
}

/**
 * Replace everything on screen with a workspace document.
 *
 * Shared by Open… (a document from the server) and Import… (a workspace JSON
 * from disk): the workspace opens as it was saved rather than merging into
 * whatever happened to be loaded.
 */
async function openWorkspace(doc, label) {
  // Looked at before anything is torn down: a file that is not a workspace
  // (or a document damaged on the server) must leave what is on screen alone.
  if (!isWorkspace(doc)) {
    fail(new Error(`${label} is not a workspace document (it has no list of cells)`));
    return;
  }
  const replaced = [...state.neurons.values()];
  for (const record of replaced) forgetCell(record);
  meshCache.clear();
  keptExtras.clear();
  // The cells the document does not bring back are let go of on the server as
  // well, as their ✕ would; the ones it names are loaded again just below.
  const kept = new Set(doc.neurons.map((n) => keyOf(n.source, n.id)));
  for (const record of replaced) {
    if (!kept.has(record.key)) api.unload(record.source, record.id).catch(() => {});
  }
  // A document saved before 2.30.2 can carry Hold; this page's own stays as it is.
  await restoreSession(session.withoutPageState(doc));
  statusEl.textContent = `Opened ${label}`;
  statusEl.className = 'status';
}

/** A workspace document, as far as restoring one needs: a list of cell entries. */
function isWorkspace(doc) {
  return !!doc && typeof doc === 'object' && Array.isArray(doc.neurons)
    && doc.neurons.every((n) => n && typeof n === 'object');
}

// Once at a time: Enter or a double click on Open, while the document is being
// read, used to replace the cells twice over.  Only the read is guarded: the
// workspace's cells then load for as long as they take, and another Open during
// that is a real request, not a repeat of the first.
const readDocument = singleFlight((folder, name) => api.saveRead(folder, name));
async function openDocument(folder, name) {
  try {
    const doc = await readDocument(folder, name);
    if (!doc) return;
    $('open-dialog').close();
    await openWorkspace(doc, `${folder}/${name}`);
  } catch (error) { fail(error); }
}

function bindUpdateBanner() {
  $('update-reload').addEventListener('click', () => {
    session.saveNow(state, renderer, uiFlags());
    session.restoreNextLoad();
    window.location.reload();
  });
  $('update-dismiss').addEventListener('click', () => {
    $('update-banner').hidden = true;
  });
}

// Exposed deliberately: it lets a user inspect or script the scene from the
// browser console, and it is how the render state is debugged.
window.sbfsem = {
  get renderer() { return renderer; },
  get shifts() { return shifts; },
  get moves() { return moves; },
  get compare() { return compare; },
  get network() { return network; },
  state,
  api,
  history() {
    return {
      undo: undoStack.map((e) => e.snap.camera?.position?.map((v) => +v.toFixed(1))),
      redo: redoStack.map((e) => e.snap.camera?.position?.map((v) => +v.toFixed(1))),
    };
  },
  /** How many entries each per-cell cache holds, for finding one that is not emptied. */
  cacheInfo() {
    return {
      meshes: meshCache.size, meshNumbers: meshCache.weight,
      keptExtras: keptExtras.size, keptExtraNumbers: keptExtras.weight,
      skeletons: skeletonCache.size, nodes: nodesCache.size,
      edges: edgesCache.size, outlines: outlinesCache.size, annotationWires: annoWireCache.size,
    };
  },
  sceneInfo() {
    const box = renderer.bounds;
    return {
      visible: [...renderer.meshes.entries()]
        .filter(([, o]) => o.visible).map(([k]) => k),
      hidden: [...renderer.meshes.entries()]
        .filter(([, o]) => !o.visible).map(([k]) => k),
      bounds: box.isEmpty() ? null : { min: box.min.toArray(), max: box.max.toArray() },
      camera: renderer.camera.position.toArray(),
      target: renderer.controls.target.toArray(),
    };
  },
};

// A step of start-up that throws (a link's cell that will not load, a panel that
// fails to build) said nothing: the page just sat there, half wired.
boot().catch((error) => {
  console.error('start-up failed:', error);
  fail(error);
});
