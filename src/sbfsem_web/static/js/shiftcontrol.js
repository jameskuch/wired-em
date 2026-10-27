// The main window's side of the Position shift layer: which cells draw one, their
// slices (a center section per volume, outside the cell's saved display), the keys
// that step them, the chip over the viewport, and what the cell panel's section
// acts through.  app.js builds one of these after the renderer exists and calls it
// at the places the other annotation layers are refreshed; the layer itself is
// shifts.js and shiftlayer.js, the controls shiftpanel.js.
//
// The slice center is transient on purpose: one number per volume in a SliceState,
// never in `display`, so a key step is no undo entry, no session write and no copy
// to other cells, and the camera never moves with it.

import { SliceState, normalizeShiftDisplay, pairsFor, SHIFT_KEYS } from './shifts.js';
import {
  applySliceKey, buildLayer, chipSummary, drawLayer, focusMarks, setSliceCenters, sliceCenters, sliceRanges,
  syncSlice, thresholdsForPair, ThresholdMemory,
} from './shiftlayer.js';
import { ShiftLegend, legendBlocks } from './shiftlegend.js';
import { dataKey } from './state.js';

/** The correction modes that ran Viking's curve fit, the only ones the Correction log checks: since
 * 2026-10-27 only a cell read from a file saved before then carries one. */
const FIT_MODES = Object.freeze(['curvefit', 'all']);

export class ShiftController {
  /**
   * `renderer`, `state`, `api`; `nodesFor(record)` and `outlinesFor(record)` fetch
   * (and cache) the cell's annotations and closed-curve outlines, `cachedNodes` and
   * `cachedOutlines` answer from the cache or `undefined`; `fail(error)`;
   * `hooks`: `applyDisplay`, `debounced`, `setRegistration`, `renderNeuronList`, `look`;
   * `dom`: the chip's elements (`chip`, `text`, `steps`, `prev`, `next`, `range`, `off`) and the
   * `viewport` the legend goes in; `hasTransforms(record)`: the cell's volume has its current
   * section transforms read here (`/api/volumes`), so all three pairs can exist;
   * `defaultCorrection(record)`: the correction mode its volume's cells get when they name none;
   * `pullRatio()`: the Correction log's ratio in this browser (settings.js `savedRatio`).
   */
  constructor({
    renderer, state, api, nodesFor, outlinesFor, cachedNodes, cachedOutlines, fail, hooks, dom,
    volumeTag = (source) => source, hasTransforms = () => true, defaultCorrection = () => 'neighbor',
    pullRatio = () => 4,
  }) {
    Object.assign(this, {
      renderer, state, api, nodesFor, outlinesFor, cachedNodes, cachedOutlines, fail, hooks, dom,
      volumeTag, hasTransforms, defaultCorrection, pullRatio,
    });
    // What the arrows' colors mean, in a corner of the render while some cell draws them.
    // Above the chip, whatever its height: it wraps in a narrow window and with the slice's stepper.
    this.legend = dom.viewport ? new ShiftLegend(dom.viewport) : null;
    this.legend?.stackAbove(dom.chip);
    // The light viewport has a ramp and hues of its own: the legend follows it.
    renderer.onInverted = () => this.updateChip();
    this.slices = new SliceState();
    this.layers = new Map();      // cell key -> its latest layer, empty ones too
    this.panels = new Map();      // cell key -> the open panel's update(layer, center)
    this.summaries = new WeakMap();   // nodes array -> Map(pair -> promise of /shift-summary)
    this.pulls = new Map();           // cell key -> { stamp, answer } of /correction-pulls
    this.thresholds = new ThresholdMemory();
    this._tokens = new Map();
    this._focus = [];               // the chosen annotations: [{ slot, key, locationId }]
    this._bindChip();
  }

  // -- what is drawn ------------------------------------------------------------------

  /** The cell's layer as last built, or null (also when its annotations were forgotten since). */
  layerOf(record) {
    const layer = this.layers.get(record.key);
    if (!layer) return null;
    return layer.sourceNodes === this.cachedNodes(record) ? layer : null;
  }

  /** `[{ key, id, source, layer }]` for the visible cells that draw a layer now. */
  entries() {
    const out = [];
    for (const [key, group] of this.renderer.shifts) {
      const record = this.state.neurons.get(key);
      const layer = group.userData.layer;
      if (!record || !layer || group.visible === false) continue;
      out.push({ key, id: record.id, source: record.source, layer });
    }
    return out;
  }

  _build(record, display, nodes, outlines) {
    const layer = buildLayer({
      nodes, outlines, summary: record.summary, display,
      inverted: this.renderer.inverted, cellColor: record.color,
      hasTransforms: this.hasTransforms(record),
    });
    layer.sourceNodes = nodes;
    return layer;
  }

  /** Draw (or remove) the cell's layer from `layer`, start its slice, and update the chip. */
  _show(record, layer) {
    this.layers.set(record.key, layer);
    drawLayer(this.renderer, record.key, layer, null);
    this.renderer.setStyle(record.key, { visible: record.visible });
    if (layer && !layer.empty) {
      // The layer is on the renderer now, so the volume's slice can be started.
      const centers = sliceCenters(this.entries(), this.slices);
      syncSlice(this.renderer, record.key, layer, centers.get(record.source), { hybrid: this._hybrid(record.key) });
    }
    this._afterChange();
    this._drawFocus();
  }

  /**
   * Bring the cell's layer in line with its display: fetch what it needs (the
   * annotations; the outlines only for the rings), build, draw.  `gone()` says the
   * cell was unloaded or loaded again meanwhile, and what came back is dropped.
   */
  async refresh(record, { gone = () => false } = {}) {
    const d = normalizeShiftDisplay(record.display);
    const token = (this._tokens.get(record.key) ?? 0) + 1;
    this._tokens.set(record.key, token);
    if (!d.showShift) {
      this.remove(record);
      return null;
    }
    let nodes;
    let outlines = [];
    try {
      nodes = await this.nodesFor(record);
      if (d.shiftRings) outlines = await this.outlinesFor(record);
    } catch (error) {
      this.fail(error);
      return null;
    }
    if (gone() || this._tokens.get(record.key) !== token) return null;
    const layer = this._build(record, record.display, nodes, outlines);
    this._show(record, layer);
    return layer;
  }

  /** Take the cell's layer away (it was turned off, or the cell went). */
  remove(record) {
    this.layers.delete(record.key);
    this.panels.delete(record.key);
    this.renderer.removeShifts(record.key);
    this.renderer.setSliceFade?.(record.key, null);
    this._afterChange();
    this._drawFocus();
  }

  /**
   * Redraw the cell's layer with `changes` over its display, without saving them: a
   * slider is being dragged.  From the cached annotations only; nothing is fetched.
   */
  live(record, changes) {
    const nodes = this.cachedNodes(record);
    if (!nodes) return;
    const display = { ...record.display, ...changes };
    const layer = this._build(record, display, nodes,
      normalizeShiftDisplay(display).shiftRings ? (this.cachedOutlines(record) ?? []) : []);
    this._show(record, layer);
  }

  /** The cells' colors follow the cell's own (Cell color); everything else is unchanged. */
  restyle(record) {
    const layer = this.layerOf(record);
    if (!layer || layer.empty || layer.display.shiftColor !== 'cell') return;
    const nodes = this.cachedNodes(record);
    if (nodes) this._show(record, this._build(record, record.display, nodes,
      layer.display.shiftRings ? (this.cachedOutlines(record) ?? []) : []));
  }

  // -- the slice ----------------------------------------------------------------------------

  /** The slice's center section for `record`'s volume, or null when it is not in slice mode. */
  centerOf(record) {
    const layer = this.layerOf(record);
    if (!layer || layer.empty || !layer.slice) return null;
    return this.slices.has(record.source) ? this.slices.get(record.source) : null;
  }

  /** The volume the chip's stepper drives: the focused cell's, else the first in slice mode. */
  _primarySource(entries) {
    const slicing = entries.filter((e) => e.layer.slice);
    const focused = slicing.find((e) => e.key === this.state.selected);
    return (focused ?? slicing[0])?.source ?? null;
  }

  /** After a center moved: every window and plane, the open panels, and the chip. */
  _afterSlice() {
    const entries = this.entries();
    const centers = sliceCenters(entries, this.slices);
    for (const e of entries) {
      syncSlice(this.renderer, e.key, e.layer, centers.get(e.source), { hybrid: this._hybrid(e.key) });
    }
    for (const [key, update] of this.panels) {
      const e = entries.find((x) => x.key === key);
      if (e) update(e.layer, centers.get(e.source));
    }
    this._renderChip(entries, centers);
  }

  /** Something about a layer changed: the chip and the open panels. */
  _afterChange() {
    this._afterSlice();
  }

  /** Whether an annotation of the cell `key` is the selection (a slice then pales its own section). */
  _hybrid(key) {
    const s = this.renderer.selection;
    return s?.kind === 'annotation' && s.key === key;
  }

  /** Move every slice volume by `delta` sections (the stepper and the range input). */
  moveBy(delta) {
    const entries = this.entries();
    const ranges = sliceRanges(entries);
    if (!ranges.size) return;
    sliceCenters(entries, this.slices);
    this.slices.stepAll(delta, ranges);
    this._afterSlice();
  }

  /** Put `record`'s volume's slice on `section` (the chart, the section slider, a chip). */
  setCenter(record, section) {
    const entries = this.entries();
    setSliceCenters(entries, this.slices, [record.source], section);
    this._afterSlice();
  }

  /**
   * The keyboard (Renderer3D.onSliceStep): `[ ]`, Up and Down, PageUp and PageDown
   * step the slices, Home and End jump to the first and last section with a shown
   * shift.  Up and Down are the slice's only while nothing is selected; with an
   * annotation selected they walk along its links as before.
   */
  onKey(event) {
    const entries = this.entries();
    if (!entries.length) return false;
    const moved = applySliceKey(event, entries, this.slices, {
      selected: this.renderer.selection?.kind === 'annotation',
      flipZ: this.renderer.flipZ,
    });
    if (!moved) return false;
    this._afterSlice();
    return true;
  }

  /** A selected annotation draws the slice to its section, in the volume's slices that have one. */
  follow(candidate) {
    if (!candidate || candidate.kind !== 'annotation' || !Number.isFinite(candidate.section)) return;
    const record = this.state.neurons.get(candidate.key);
    if (!record) return;
    const entries = this.entries();
    if (!sliceRanges(entries).has(record.source)) return;
    setSliceCenters(entries, this.slices, [record.source], candidate.section);
    this._afterSlice();
  }

  // -- the chip over the viewport -----------------------------------------------------------------

  _bindChip() {
    const { prev, next, range, off } = this.dom;
    // The slice keys are the render canvas's: after a click here the focus is on the button,
    // and the keys would silently stop, so it goes back to the canvas.
    const refocus = () => { try { this.renderer.webgl?.domElement?.focus?.({ preventScroll: true }); } catch { /* none */ } };
    prev.addEventListener('click', () => { this.moveBy(-1); refocus(); });
    next.addEventListener('click', () => { this.moveBy(1); refocus(); });
    range.addEventListener('change', refocus);
    range.addEventListener('input', () => {
      const entries = this.entries();
      const source = this._primarySource(entries);
      if (source === null) return;
      const from = this.slices.get(source);
      this.moveBy(Number(range.value) - (Number.isFinite(from) ? from : Number(range.value)));
    });
    off.addEventListener('click', async () => {
      refocus();
      for (const { key } of this.entries()) {
        const record = this.state.neurons.get(key);
        if (record) await this.hooks.applyDisplay(record, { showShift: false });
      }
      this.hooks.renderNeuronList();
    });
  }

  _renderChip(entries, centers) {
    const { chip, text, steps, range } = this.dom;
    const shown = entries.filter((e) => !e.layer.empty);
    // Cells of more than one volume are named with their volume, as on the chip.
    const mixed = new Set(shown.map((e) => e.source)).size > 1;
    this.legend?.update(legendBlocks(shown.map((e) => ({
      id: e.id, layer: e.layer, cellColor: this.state.neurons.get(e.key)?.color,
      name: mixed ? `c${e.id} ${this.volumeTag(e.source)}` : `c${e.id}`,
    })), { inverted: this.renderer.inverted }));
    chip.hidden = shown.length === 0;
    if (chip.hidden) return;
    text.textContent = chipSummary(shown, centers, this.volumeTag);
    const source = this._primarySource(shown);
    steps.hidden = source === null;
    if (source === null) return;
    const cells = shown.filter((e) => e.source === source && e.layer.slice);
    range.min = String(Math.min(...cells.map((e) => e.layer.sectionRange.min)));
    range.max = String(Math.max(...cells.map((e) => e.layer.sectionRange.max)));
    range.value = String(centers.get(source));
  }

  /** Re-render the chip from what is drawn (visibility changed, a cell went). */
  updateChip() {
    // A cell that was unloaded takes its layer, panel and token with it: each layer holds
    // the cell's whole /nodes array, megabytes on a large one.
    for (const map of [this.layers, this.panels, this._tokens]) {
      for (const key of [...map.keys()]) if (!this.state.neurons.has(key)) map.delete(key);
    }
    // Visibility or the background changed: the slices' colors (a recolor gave the marks their
    // own back), the chip, and the chosen arrows follow.
    this._afterSlice();
    this._drawFocus();
  }

  // -- the chosen annotations ----------------------------------------------------------------------

  /**
   * The annotations chosen in the render, `[{ slot, key, locationId }]` (the yellow
   * selection, the red marker above it, the blue below): each one's own arrow is drawn
   * over its cell's layer in its marker's color, wider and over everything, and while
   * any is, every cell's other arrows are drawn at its `shiftFocusDim` share of their
   * opacity.  `[]` (nothing chosen) puts them back.
   */
  focus(items) {
    this._focus = (items || []).filter((item) => item?.key && item.locationId != null);
    // A slice's section turns pale yellow while an annotation of its cell is selected.
    if (this.entries().some((e) => e.layer.slice)) this._afterSlice();
    this._drawFocus();
  }

  /** What `focus` shows for the annotations chosen now: `[{ slot, key, mark }]`. */
  focused() {
    const out = [];
    for (const item of this._focus) {
      const record = this.state.neurons.get(item.key);
      const layer = record ? this.layerOf(record) : null;
      const group = this.renderer.shifts.get(item.key);
      if (!layer || layer.empty || !group || group.visible === false) continue;
      // Arrows and Original rings are the gates for a chosen annotation's own arrow and
      // ring too (2026-10-22: a ring was drawn for the selection with the rings off).
      // With the rings on, the outlines were fetched with the layer.
      const { shiftArrows, shiftRings } = layer.display;
      const outlines = shiftRings ? (this.cachedOutlines(record) ?? []) : [];
      const [mark] = focusMarks(layer, [item.locationId], outlines);
      if (!mark) continue;
      const shown = { ...mark, arrow: shiftArrows ? mark.arrow : null, ring: shiftRings ? mark.ring : null };
      if (!shown.arrow && !shown.ring) continue;
      out.push({ slot: item.slot, key: item.key, mark: { ...shown, width: layer.display.shiftWidth } });
    }
    return out;
  }

  _drawFocus() {
    if (!this.renderer.setShiftFocus) return;
    const shown = this.focused();
    const byKey = new Map();
    for (const { slot, key, mark } of shown) {
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push({ ...mark, slot });
    }
    for (const [key, group] of this.renderer.shifts) {
      this.renderer.setShiftFocus(key, byKey.get(key) ?? []);
      const layer = group.userData.layer;
      // A slice has its own emphasis (its section yellow, the rest almost nothing): the
      // selection's fade would put out the plane's section too.
      this.renderer.setShiftDim(key, shown.length && layer && !layer.empty && !layer.slice
        ? normalizeShiftDisplay(layer.display).shiftFocusDim : 1);
    }
  }

  // -- the cell panel ------------------------------------------------------------------------------

  /** The server's verdict on this cell's links across sections, once per (annotations, pair). */
  summaryFor(record) {
    const nodes = this.cachedNodes(record);
    const layer = this.layerOf(record);
    if (!nodes || !layer || layer.empty) return null;
    let byPair = this.summaries.get(nodes);
    if (!byPair) {
      byPair = new Map();
      this.summaries.set(nodes, byPair);
    }
    if (!byPair.has(layer.pair)) {
      const promise = this.api.shiftSummary(record.source, record.id, layer.pair)
        .catch(() => { byPair.delete(layer.pair); return null; });
      byPair.set(layer.pair, promise);
    }
    return byPair.get(layer.pair);
  }

  /**
   * The annotations Viking's curve fit pulled toward a much wider linked one in this
   * cell (its rows of the Settings tab's Correction log), at that log's ratio: the
   * server's answer once it is here, else null.  Asked once per copy, annotations and
   * ratio, and only of a cell its curve fit moved; the cell list is drawn again when
   * the answer comes.  A failed ask shows nothing, and is not repeated for that stamp.
   */
  pullsFor(record) {
    const sc = record.summary?.section_correction;
    if (!sc?.applied || !FIT_MODES.includes(sc.mode)) return null;
    const ratio = this.pullRatio();
    const stamp = `${dataKey(record)}|${ratio}`;
    const held = this.pulls.get(record.key);
    if (held?.stamp === stamp) return held.answer;
    const entry = { stamp, answer: null };
    this.pulls.set(record.key, entry);
    this.api.correctionPulls(record.source, record.id, ratio).then((answer) => {
      if (this.pulls.get(record.key) !== entry) return;
      entry.answer = answer;
      this.hooks.renderNeuronList();
    }, () => {});
    return null;
  }

  /** What shiftpanel.buildShiftPanel acts through for one cell. */
  panelContext(record, helpers) {
    return {
      helpers,
      layer: () => this.layerOf(record),
      center: () => this.centerOf(record),
      apply: (changes) => this.hooks.applyDisplay(record, changes),
      // The thresholds a pair needs, so a pair that moves things by hundredths is not hidden by 0.25.
      thresholdsFor: (pair) => {
        const nodes = this.cachedNodes(record);
        return nodes ? thresholdsForPair(this.thresholds, record.key, nodes, pair, record.display) : {};
      },
      live: (changes) => this.live(record, changes),
      settle: (key, fn) => this.hooks.debounced(key, fn),
      setCenter: (section) => this.setCenter(record, section),
      look: (name) => this.hooks.look(name),
      correction: () => this.defaultCorrection(record),
      applyCorrection: () => this.hooks.setRegistration(record, { corrections: this.defaultCorrection(record) }),
      summary: () => this.summaryFor(record),
      pulls: () => this.pullsFor(record),
      selectLocation: (locationId) => this.hooks.selectLocation?.(record, locationId),
      // The pairs the cell's volume can have at all: the correction alone where its transforms are not read.
      pairs: () => pairsFor(this.hasTransforms(record)),
      rerender: () => this.hooks.renderNeuronList(),
      register: (update) => this.panels.set(record.key, update),
    };
  }
}

/** True when every key of `changes` belongs to the Position shift layer. */
export function onlyShiftKeys(changes) {
  const keys = Object.keys(changes);
  return keys.length > 0 && keys.every((k) => SHIFT_KEYS.includes(k));
}
