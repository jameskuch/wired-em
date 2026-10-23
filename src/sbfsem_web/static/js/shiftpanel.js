// The Position shift layer's controls: the cell panel's section in the main window
// and the shared strip under the Render compare panes (the layer itself is
// shifts.js and shiftlayer.js).  Every control carries a `data-help` text (2 s,
// beside the control), most with a drawing above it (helpfigures.js).  No result
// stands in the panel as prose: a small chip beside the switch says how many
// arrows are drawn (or "nothing moved", or the pair drawn when it is not the one
// asked for), and its hover has the facts and the verdict on the links.
//
// Nothing here talks to the server or the renderer.  The caller injects what the
// panel acts through (`ctx`), as `buildSkeletonCheck` does, so the same controls
// serve a cell of the main window and the compare panes.

import {
  PAIR_LABELS, SHIFT_FIGURES, SHIFT_HELP, SHIFT_PAIRS, SCALE_MAX, SCALE_MIN, SPAN_MAX, SPAN_SLIDER,
  coherentChipText, dotData, normalizeShiftDisplay, summaryText, thresholdFor, verdictLine,
} from './shifts.js';
import { WIDTH_MAX, WIDTH_MIN } from './fatlines.js';
import { shiftChip } from './shiftlayer.js';
import { MODE_LABELS } from './regstatus.js';
import { PULLS_HELP, cellPullsView } from './settings.js';

/**
 * A hover explanation in the app's own panel (data-help), after a long pause,
 * with the drawing `figure` (helpfigures.js) above it when one is named.
 */
export function help(el, text, side = 'right', figure = null) {
  el.dataset.help = text;
  el.dataset.helpDelay = '2000';
  if (side) el.dataset.helpSide = side;
  if (figure) el.dataset.helpFigure = figure;
  else delete el.dataset.helpFigure;
  return el;
}

/** `help` for one of the layer's controls, by its key in SHIFT_HELP (with its figure). */
const helpFor = (el, key, side = 'right') => help(el, SHIFT_HELP[key], side, SHIFT_FIGURES[key] || null);

/** Help for the controls shifts.js has no text for. */
const PANEL_HELP = Object.freeze({
  chart: 'One dot per moved annotation: its section across, the size of its shift up (the top is '
    + 'a little above the 99th percentile). The shaded stripe is the slice; the dashed line is Only '
    + 'shifts above. Click or drag to put the slice on a section.',
  sliceSection: 'The section the slice is centered on. The arrow keys, [ ] and PageUp/PageDown step '
    + 'it too, with Shift or PageUp/PageDown by ten.',
});

/**
 * A small status chip (`span.status-chip`) in `parent`: a word or a count, with
 * its facts in the 2 s hover.  Returns `{ el, set }`; `set({ text, warn, help })`,
 * and an empty text hides it.
 */
export function statusChip(parent, side = 'right') {
  const chip = document.createElement('span');
  chip.className = 'status-chip';
  chip.tabIndex = 0;
  parent.append(chip);
  const set = ({ text = '', warn = false, help: words = '' } = {}) => {
    chip.hidden = !text;
    chip.textContent = text;
    chip.classList.toggle('warn', !!warn);
    if (words) help(chip, words, side);
    else delete chip.dataset.help;
    chip.setAttribute('aria-label', words ? `${text}. ${words}` : text);
  };
  return { el: chip, set };
}

/**
 * The header summary of the cell panel's section, and after it how many annotations
 * the curve fit pulled toward a much wider one (`pulls`: the cell's /correction-pulls
 * answer, or null), whether the layer is on or not.
 */
export function shiftSummary(display, layer, pulls = null) {
  const view = cellPullsView(pulls);
  const text = layerSummary(display, layer);
  return view ? `${text} · ${view.count}` : text;
}

function layerSummary(display, layer) {
  const d = normalizeShiftDisplay(display);
  if (!d.showShift) return 'off';
  if (!layer) return 'loading';
  if (layer.empty) return 'nothing moved';
  return summaryText(layer.display, layer.k, layer.pair);
}

/**
 * The line of annotations Viking's curve fit pulled toward a much wider linked one,
 * each ID a button that selects it (`select(locationId)`); nothing when none was.
 */
export function pullsRow(parent, pulls, select) {
  const view = cellPullsView(pulls);
  if (!view) return null;
  const row = document.createElement('div');
  row.className = 'shift-pulls';
  const words = document.createElement('span');
  words.className = 'shift-pulls-text';
  words.textContent = view.line;
  help(words, PULLS_HELP);
  row.append(words);
  for (const { locationId, title } of view.ids) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'shift-pull-id';
    button.textContent = String(locationId);
    button.dataset.location = String(locationId);
    help(button, `${title}. Click to select it in the 3D view.`);
    button.addEventListener('click', () => select?.(locationId));
    row.append(button);
  }
  parent.append(row);
  return row;
}

// -- a logarithmic slider -----------------------------------------------------------------------------

/**
 * The exaggeration, 1 to 100 on a log scale (a slider over the exponent), with its
 * number box and a Fit button that goes back to Auto.  `auto`: the number shown is
 * Auto's; the caption says so until the user moves it.
 */
export function logSlider(parent, { value, auto, onLive, onSettle, onFit, title, figure = null }) {
  const wrap = document.createElement('div');
  wrap.className = 'slider-field shift-scale';
  const top = document.createElement('div');
  top.className = 'slider-label';
  const caption = document.createElement('span');
  const box = document.createElement('input');
  box.type = 'number';
  box.className = 'numbox';
  box.min = String(SCALE_MIN);
  box.max = String(SCALE_MAX);
  box.step = '0.1';
  box.setAttribute('aria-label', 'Exaggeration value');
  const unit = document.createElement('span');
  unit.className = 'unit';
  unit.textContent = '×';
  const fit = document.createElement('button');
  fit.type = 'button';
  fit.className = 'shift-fit';
  fit.textContent = 'Fit';
  help(fit, SHIFT_HELP.scale, 'right', figure);
  top.append(caption, box, unit, fit);

  const range = document.createElement('input');
  range.type = 'range';
  range.min = '0';
  range.max = String(Math.log10(SCALE_MAX));
  range.step = '0.005';
  range.setAttribute('aria-label', 'Exaggeration');

  const show = (k, isAuto) => {
    const v = Math.min(SCALE_MAX, Math.max(SCALE_MIN, k));
    box.value = v.toFixed(v >= 10 ? 0 : 1);
    range.value = String(Math.log10(v));
    caption.textContent = 'Exaggerate';
    fit.textContent = isAuto ? 'Auto' : 'Fit';
    fit.disabled = isAuto;
  };
  show(value, auto);
  range.addEventListener('input', () => {
    const k = 10 ** Number(range.value);
    show(k, false);
    onLive?.(Number(k.toFixed(2)));
    onSettle?.(Number(k.toFixed(2)));
  });
  box.addEventListener('change', () => {
    const k = Number(box.value);
    if (!Number.isFinite(k) || k <= 0) { show(value, auto); return; }
    show(k, false);
    const clamped = Math.min(SCALE_MAX, Math.max(SCALE_MIN, k));
    onLive?.(clamped);
    onSettle?.(clamped);
  });
  fit.addEventListener('click', () => onFit?.());
  wrap.append(top, range);
  if (title) help(wrap, title, 'right', figure);
  parent.append(wrap);
  return { wrap, show, box, range };
}

// -- the per-section chart --------------------------------------------------------------------------------

/**
 * One dot per moved annotation: its section across, the true length of its shift
 * up (the top is the 99th percentile, so one 159 µm outlier does not flatten the
 * rest; a dot above it sits on the top edge).  The slice's window is shaded, the
 * "Only shifts above" threshold is a faint line, and a click or a drag across the
 * chart sets the slice.  Colors are read from the page's tokens at draw time, so
 * it follows the theme.
 */
export function dotChart(parent, { onPick, height = 84 }) {
  const wrap = document.createElement('div');
  wrap.className = 'shift-chart';
  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Size of each annotation\'s shift by section');
  canvas.style.height = `${height}px`;
  help(canvas, PANEL_HELP.chart);
  const axis = document.createElement('div');
  axis.className = 'shift-chart-axis';
  const left = document.createElement('span');
  const right = document.createElement('span');
  axis.append(left, right);
  wrap.append(canvas, axis);
  parent.append(wrap);

  let state = null;
  const MARGIN = { l: 4, r: 4, t: 6, b: 4 };

  const sectionAt = (clientX) => {
    if (!state) return null;
    const rect = canvas.getBoundingClientRect();
    const t = (clientX - rect.left - MARGIN.l) / Math.max(1, rect.width - MARGIN.l - MARGIN.r);
    const { sectionMin, sectionMax } = state.data;
    return Math.round(sectionMin + Math.min(1, Math.max(0, t)) * (sectionMax - sectionMin));
  };
  let dragging = false;
  canvas.addEventListener('pointerdown', (e) => {
    if (!state || e.button !== 0) return;
    dragging = true;
    canvas.setPointerCapture?.(e.pointerId);
    onPick?.(sectionAt(e.clientX));
  });
  canvas.addEventListener('pointermove', (e) => { if (dragging) onPick?.(sectionAt(e.clientX)); });
  const stop = () => { dragging = false; };
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);

  const draw = () => {
    if (!state) return;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const cssW = Math.max(60, canvas.clientWidth || wrap.clientWidth || 240);
    const cssH = height;
    if (canvas.width !== Math.round(cssW * ratio) || canvas.height !== Math.round(cssH * ratio)) {
      canvas.width = Math.round(cssW * ratio);
      canvas.height = Math.round(cssH * ratio);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    const style = getComputedStyle(canvas);
    const ink = style.getPropertyValue('--muted').trim() || '#6b7280';
    const accent = style.getPropertyValue('--accent').trim() || '#2f6fed';
    const line = style.getPropertyValue('--line').trim() || '#dfe3e8';
    const { data, window: win, threshold, center } = state;
    const w = cssW - MARGIN.l - MARGIN.r;
    const h = cssH - MARGIN.t - MARGIN.b;
    const span = Math.max(1, data.sectionMax - data.sectionMin);
    const x = (s) => MARGIN.l + ((s - data.sectionMin) / span) * w;
    const top = state.top;
    const y = (v) => MARGIN.t + h - Math.min(1, v / top) * h;
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    ctx.strokeRect(MARGIN.l + 0.5, MARGIN.t + 0.5, w - 1, h - 1);
    if (win) {
      const x0 = x(win.from - 0.5);
      const x1 = x(win.to + 0.5);
      ctx.fillStyle = accent;
      ctx.globalAlpha = 0.2;
      ctx.fillRect(Math.max(MARGIN.l, x0), MARGIN.t, Math.max(2, Math.min(MARGIN.l + w, x1) - Math.max(MARGIN.l, x0)), h);
      ctx.globalAlpha = 1;
    }
    if (threshold > 0 && threshold < top) {
      ctx.strokeStyle = ink;
      ctx.globalAlpha = 0.4;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(MARGIN.l, y(threshold) + 0.5);
      ctx.lineTo(MARGIN.l + w, y(threshold) + 0.5);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = ink;
    ctx.globalAlpha = 0.55;
    for (let i = 0; i < data.n; i += 1) {
      ctx.fillRect(x(data.section[i]) - 0.75, y(data.mag[i]) - 0.75, 1.5, 1.5);
    }
    ctx.globalAlpha = 1;
    if (Number.isFinite(center)) {
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x(center) + 0.5, MARGIN.t);
      ctx.lineTo(x(center) + 0.5, MARGIN.t + h);
      ctx.stroke();
    }
  };

  return {
    wrap,
    /** `field` (shifts.js), the window in sections (`{from, to}` or null), the slice's center. */
    update(field, { window: win = null, center = null, threshold = 0 } = {}) {
      const data = dotData(field);
      const top = Math.max(1e-6, (field.stats?.p99 || data.maxMag) * 1.25);
      state = { data, window: win, center, threshold, top };
      left.textContent = `section ${data.sectionMin}`;
      right.textContent = `${data.sectionMax}`;
      axis.title = `0 to ${top.toFixed(2)} µm${data.maxMag > top ? ` (the longest, ${data.maxMag.toFixed(2)} µm, sits on the top edge)` : ''}`;
      draw();
    },
    redraw: draw,
  };
}

// -- the cell panel's section ------------------------------------------------------------------------------

/**
 * Build the "Position shift" section body.
 *
 * `ctx`:
 *   helpers      `{ slider, showToggle, field }` from the app (the same ones the other sections use)
 *   layer()      the cell's layer (shiftlayer.js) as drawn now, or null
 *   center()     the slice's center section for the cell's volume, or null
 *   pairs()      the pairs the cell's volume can have (shifts.js pairsFor); all three if absent
 *   apply(changes)       save display changes and redraw (a promise)
 *   thresholdsFor(pair)  the thresholds the pair needs to show something ({} when the ones in force do)
 *   live(changes)        redraw with the changes, nothing saved (a slider drag)
 *   settle(key, fn)      run fn once the value stops changing
 *   setCenter(section)   put the slice on a section
 *   look(viewpoint)      turn the view ('xy1', 'xyv')
 *   correction()         the correction mode Apply turns on: the volume's default
 *   applyCorrection()    turn on the section correction, in that mode, for the cell
 *   summary()            the server's shift summary for the pair asked for, a promise or null
 *   rerender()           rebuild the cell list
 *   register(update)     the app calls `update(layer, center)` after a redraw or a slice step
 */
export function buildShiftPanel(body, record, ctx) {
  const { slider, showToggle, field } = ctx.helpers;
  const display = normalizeShiftDisplay(record.display);
  const layer = ctx.layer();

  // The switch, and beside it what it draws: the facts are the chip's hover.
  const row = document.createElement('div');
  row.className = 'switch-row';
  body.append(row);
  const master = showToggle(row, 'Show position shift', display.showShift, async (on) => {
    await ctx.apply({ showShift: on });
    ctx.rerender();
  });
  helpFor(master.parentElement, 'master');
  // Whether or not the layer is on: what the correction did wrong is not a drawing setting.
  pullsRow(body, ctx.pulls?.(), ctx.selectLocation);
  if (!display.showShift) return;
  const chip = statusChip(row);
  let verdictText = '';
  chip.set(shiftChip(layer, ctx.center()));

  if (!layer) return;
  if (layer.empty) {
    const sc = record.summary?.section_correction;
    if (!sc?.requested || !sc.applied) {
      const apply = document.createElement('button');
      apply.type = 'button';
      apply.className = 'full';
      apply.textContent = `Apply ${(MODE_LABELS[ctx.correction?.()] || MODE_LABELS.curvefit).toLowerCase()}`;
      helpFor(apply, 'apply');
      apply.addEventListener('click', () => ctx.applyCorrection());
      body.append(apply);
    }
    return;
  }

  // -- which two positions: the pairs the volume can have, and no select for one ---------------
  const pairs = ctx.pairs?.() ?? SHIFT_PAIRS;
  if (pairs.length > 1) {
    const pair = document.createElement('select');
    for (const p of pairs) {
      const option = new Option(PAIR_LABELS[p], p);
      const availability = layer.availability[p];
      if (!availability.available) {
        option.disabled = true;
        option.title = availability.reason;
        option.dataset.help = availability.reason;
      }
      pair.append(option);
    }
    pair.value = pairs.includes(display.shiftPair) ? display.shiftPair : layer.pair;
    pair.addEventListener('change', () => ctx
      .apply({ shiftPair: pair.value, ...ctx.thresholdsFor(pair.value) }).then(ctx.rerender));
    labelled(field(body, 'Between', pair), 'pair');
  }

  // -- what is drawn ------------------------------------------------------------------------
  const arrows = showToggle(body, 'Arrows', display.shiftArrows,
    (on) => ctx.apply({ shiftArrows: on }).then(ctx.rerender));
  helpFor(arrows.parentElement, 'arrows');
  const rings = showToggle(body, 'Original rings', display.shiftRings,
    (on) => ctx.apply({ shiftRings: on }).then(ctx.rerender));
  helpFor(rings.parentElement, 'rings');

  const scale = logSlider(body, {
    value: layer.k,
    auto: display.shiftScale === null,
    onLive: (k) => ctx.live({ shiftScale: k }),
    onSettle: (k) => ctx.settle(`${record.key}:shiftscale`, () => ctx.apply({ shiftScale: k })),
    onFit: () => ctx.apply({ shiftScale: null }).then(ctx.rerender),
    title: SHIFT_HELP.scale,
    figure: SHIFT_FIGURES.scale,
  });

  helpFor(slider(body, 'Arrow width', {
    key: `${record.key}:shiftwidth`, min: WIDTH_MIN, max: WIDTH_MAX, step: 0.5,
    value: display.shiftWidth, unit: 'px', decimals: 1,
    onLive: (v) => ctx.live({ shiftWidth: v }),
    onSettle: (v) => ctx.apply({ shiftWidth: v }),
  }), 'width');

  const minKey = display.shiftSlice ? 'shiftMinSlice' : 'shiftMin';
  helpFor(slider(body, 'Only shifts above', {
    key: `${record.key}:${minKey}`, min: 0, max: 3, step: 0.01,
    // The layer's own: a pair that stands in for the one asked for has its own threshold.
    value: Math.min(3, thresholdFor(layer.display)), unit: 'µm', decimals: 2,
    onLive: (v) => ctx.live({ [minKey]: v }),
    onSettle: (v) => ctx.apply({ [minKey]: v }),
  }), 'min');

  // -- one slice, or every section --------------------------------------------------------------
  const slice = showToggle(body, 'One slice at a time', display.shiftSlice,
    (on) => ctx.apply({ shiftSlice: on }).then(ctx.rerender));
  helpFor(slice.parentElement, 'slice');

  let sectionBox = null;
  let sectionRange = null;
  let chart = null;
  let chips = null;
  if (display.shiftSlice) {
    // One rule (2026-10-22): the slice at full strength, everything else of the cell at Outside the slice.
    helpFor(slider(body, 'Slice ± sections', {
      key: `${record.key}:shiftslicespan`, min: 0, max: SPAN_SLIDER, step: 1,
      value: Math.min(SPAN_SLIDER, display.shiftSliceSpan), decimals: 0,
      onLive: (v) => ctx.live({ shiftSliceSpan: Math.round(v) }),
      onSettle: (v) => ctx.apply({ shiftSliceSpan: Math.round(v) }),
    }), 'span');
    helpFor(slider(body, 'Outside the slice', {
      key: `${record.key}:shiftoutside`, min: 0, max: 1, step: 0.01,
      value: display.shiftOutside, decimals: 2,
      onLive: (v) => ctx.live({ shiftOutside: v }),
      onSettle: (v) => ctx.apply({ shiftOutside: v }),
    }), 'outside');

    const center = ctx.center();
    const section = slider(body, 'Section', {
      key: `${record.key}:shiftsection`,
      min: layer.sectionRange.min, max: layer.sectionRange.max, step: 1,
      value: Number.isFinite(center) ? center : layer.sectionRange.min, decimals: 0,
      onLive: (v) => ctx.setCenter(Math.round(v)),
    });
    help(section, PANEL_HELP.sliceSection, 'right', 'slice');
    sectionBox = section.querySelector('.numbox');
    sectionRange = section.querySelector('input[type=range]');
    chart = dotChart(body, { onPick: (s) => ctx.setCenter(s) });
    chips = document.createElement('div');
    chips.className = 'shift-chips';
    body.append(chips);

    const looks = document.createElement('div');
    looks.className = 'row editor-row shift-looks';
    const lookDown = document.createElement('button');
    lookDown.type = 'button';
    lookDown.textContent = 'Look down Z';
    helpFor(lookDown, 'lookDownZ');
    lookDown.addEventListener('click', () => ctx.look('xy1'));
    const yDown = document.createElement('button');
    yDown.type = 'button';
    yDown.textContent = 'Y down';
    helpFor(yDown, 'yDown');
    yDown.addEventListener('click', () => ctx.look('xyv'));
    looks.append(lookDown, yDown);
    body.append(looks);
  }

  // -- color --------------------------------------------------------------------------------
  const color = document.createElement('select');
  color.append(new Option('Magnitude', 'magnitude'), new Option('Direction', 'direction'),
    new Option('Cell color', 'cell'));
  color.value = display.shiftColor;
  color.addEventListener('change', () => ctx.apply({ shiftColor: color.value }).then(ctx.rerender));
  labelled(field(body, 'Color', color), 'color');
  if (display.shiftColor === 'magnitude') {
    const max = document.createElement('input');
    max.type = 'number';
    max.min = '0.01';
    max.step = '0.05';
    max.placeholder = `auto ${layer.colorMax.toFixed(2)} µm`;
    if (display.shiftColorMax) max.value = String(display.shiftColorMax);
    max.addEventListener('change', () => {
      const v = Number(max.value);
      ctx.apply({ shiftColorMax: max.value !== '' && Number.isFinite(v) && v > 0 ? v : null })
        .then(ctx.rerender);
    });
    labelled(field(body, 'Full color at', max), 'colorMax');
  }

  const through = showToggle(body, 'Through the surface', display.shiftThrough,
    (on) => ctx.apply({ shiftThrough: on }));
  helpFor(through.parentElement, 'through');
  helpFor(slider(body, 'Opacity', {
    key: `${record.key}:shiftopacity`, min: 0.05, max: 1, step: 0.01,
    value: display.shiftOpacity, decimals: 2,
    onLive: (v) => ctx.live({ shiftOpacity: v }),
    onSettle: (v) => ctx.apply({ shiftOpacity: v }),
  }), 'opacity');
  // A slice has its own emphasis (Outside the slice): the selection's fade is not used there.
  if (!display.shiftSlice) {
    helpFor(slider(body, 'Others when selected', {
      key: `${record.key}:shiftfocusdim`, min: 0, max: 1, step: 0.05,
      value: display.shiftFocusDim, decimals: 2,
      onLive: (v) => ctx.live({ shiftFocusDim: v }),
      onSettle: (v) => ctx.apply({ shiftFocusDim: v }),
    }), 'focusDim');
  }

  const update = (next, center) => {
    const l = next || ctx.layer();
    if (!l || l.empty) return;
    const c = Number.isFinite(center) ? center : ctx.center();
    chip.set(shiftChip(l, c, verdictText));
    scale.show(l.k, l.display.shiftScale === null);
    if (sectionBox && Number.isFinite(c)) {
      sectionBox.value = String(c);
      sectionRange.value = String(c);
    }
    if (chart) {
      const half = l.display.shiftSliceSpan;
      chart.update(l.field, {
        window: Number.isFinite(c) ? { from: c - half, to: c + half } : null,
        center: c,
        threshold: thresholdFor(l.display),
      });
    }
  };
  ctx.register(update);
  update(layer, ctx.center());

  const summary = ctx.summary?.();
  if (summary) {
    summary.then((s) => {
      verdictText = verdictLine(s);
      update(null, null);
      if (chips && s?.coherent_sections?.length) {
        chips.replaceChildren(...s.coherent_sections.slice(0, 8).map((c) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'shift-chip-btn';
          button.textContent = coherentChipText(c);
          help(button, `Section ${c.section}: ${c.n} annotations moved together by ${c.mean_um.toFixed(2)} µm (resultant ${c.resultant.toFixed(2)}). Click to put the slice there.`);
          button.addEventListener('click', () => ctx.setCenter(c.section));
          return button;
        }));
      }
    }).catch(() => {});
  }
}

/** A field's control and its label (the element `field` put before it) get the same help. */
function labelled(control, key) {
  helpFor(control, key);
  const label = control.previousElementSibling;
  if (label && label.tagName === 'LABEL') helpFor(label, key);
  return control;
}

// -- the shared strip under the Render compare panes ------------------------------------------------------------

/**
 * Fill `host` (the strip's row) with the settings every pane shares.
 *
 * `model`: `display()` the strip's normalized settings, `thresholdsFor(pair)`, `layer()` the layer as
 * drawn (or null), `center()` the slice's center, `pairs()` the pairs the compared cell's volume can
 * have (all three if absent), `set(changes)` change settings, `setCenter(section)`, `step(delta)`.
 * Returns `{ update }`, called after each redraw or slice step.
 */
export function buildShiftStrip(host, model) {
  host.replaceChildren();
  const labelledField = (text, control, key) => {
    const label = document.createElement('label');
    label.className = 'field';
    label.append(document.createTextNode(`${text} `), control);
    if (key) help(label, SHIFT_HELP[key], null, SHIFT_FIGURES[key] || null);
    host.append(label);
    return label;
  };
  const number = (props) => Object.assign(document.createElement('input'), { type: 'number', ...props });

  const title = document.createElement('strong');
  title.className = 'shift-strip-title';
  title.textContent = 'Position shift';
  host.append(title);
  const chip = statusChip(host, null);

  const pair = document.createElement('select');
  pair.addEventListener('change', () => model.set({ shiftPair: pair.value, ...model.thresholdsFor(pair.value) }));
  const pairLabel = labelledField('Between', pair, 'pair');
  let pairsShown = '';

  const min = number({ min: 0, step: 0.05, className: 'shift-num' });
  min.addEventListener('change', () => {
    const v = Number(min.value);
    if (!Number.isFinite(v) || v < 0) { update(); return; }
    model.set({ [model.display().shiftSlice ? 'shiftMinSlice' : 'shiftMin']: v });
  });
  labelledField('Only above (µm)', min, 'min');

  const k = number({ min: SCALE_MIN, max: SCALE_MAX, step: 0.5, className: 'shift-num' });
  k.addEventListener('change', () => {
    const v = Number(k.value);
    if (!Number.isFinite(v) || v <= 0) { update(); return; }
    model.set({ shiftScale: Math.min(SCALE_MAX, Math.max(SCALE_MIN, v)) });
  });
  const fit = document.createElement('button');
  fit.type = 'button';
  fit.textContent = 'Fit';
  help(fit, SHIFT_HELP.scale, null, SHIFT_FIGURES.scale);
  fit.addEventListener('click', () => model.set({ shiftScale: null }));
  const kLabel = labelledField('×', k, 'scale');
  kLabel.append(fit);

  const width = number({ min: WIDTH_MIN, max: WIDTH_MAX, step: 0.5, className: 'shift-num narrow' });
  width.addEventListener('change', () => {
    const v = Number(width.value);
    if (!Number.isFinite(v) || v <= 0) { update(); return; }
    model.set({ shiftWidth: Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, v)) });
  });
  labelledField('Width (px)', width, 'width');

  const sliceBox = document.createElement('input');
  sliceBox.type = 'checkbox';
  sliceBox.addEventListener('change', () => model.set({ shiftSlice: sliceBox.checked }));
  const sliceLabel = document.createElement('label');
  sliceLabel.className = 'check';
  sliceLabel.append(sliceBox, document.createTextNode(' One slice ±'));
  const half = number({ min: 0, max: SPAN_MAX, step: 1, className: 'shift-num narrow' });
  half.addEventListener('change', () => {
    const v = Math.round(Number(half.value));
    if (Number.isFinite(v) && v >= 0) model.set({ shiftSliceSpan: Math.min(SPAN_MAX, v) });
    else update();
  });
  sliceLabel.append(half);
  help(sliceLabel, SHIFT_HELP.slice, null, SHIFT_FIGURES.slice);
  host.append(sliceLabel);

  const stepper = document.createElement('span');
  stepper.className = 'shift-stepper';
  const prev = document.createElement('button');
  prev.type = 'button';
  prev.textContent = '◀';
  prev.setAttribute('aria-label', 'Previous section');
  const range = document.createElement('input');
  range.type = 'range';
  range.step = '1';
  range.setAttribute('aria-label', 'Slice section');
  const next = document.createElement('button');
  next.type = 'button';
  next.textContent = '▶';
  next.setAttribute('aria-label', 'Next section');
  const where = document.createElement('span');
  where.className = 'muted shift-where';
  stepper.append(prev, range, next, where);
  prev.addEventListener('click', () => model.step(-1));
  next.addEventListener('click', () => model.step(1));
  range.addEventListener('input', () => model.setCenter(Number(range.value)));
  host.append(stepper);

  const color = document.createElement('select');
  color.append(new Option('Magnitude', 'magnitude'), new Option('Direction', 'direction'),
    new Option('Cell color', 'cell'));
  color.addEventListener('change', () => model.set({ shiftColor: color.value }));
  labelledField('Color', color, 'color');

  const max = number({ min: 0.01, step: 0.05, className: 'shift-num wide', placeholder: 'auto' });
  max.addEventListener('change', () => {
    const v = Number(max.value);
    model.set({ shiftColorMax: max.value !== '' && Number.isFinite(v) && v > 0 ? v : null });
  });
  const maxLabel = labelledField('Full color at (µm)', max, 'colorMax');

  function update() {
    const d = model.display();
    const layer = model.layer();
    // Only the pairs the compared cell's volume can have; no select at all for one.
    const pairs = model.pairs?.() ?? SHIFT_PAIRS;
    if (pairs.join() !== pairsShown) {
      pairsShown = pairs.join();
      pair.replaceChildren(...pairs.map((p) => new Option(PAIR_LABELS[p], p)));
    }
    pairLabel.hidden = pairs.length < 2;
    pair.value = pairs.includes(d.shiftPair) ? d.shiftPair : pairs[0];
    for (const option of pair.options) {
      const availability = layer?.availability?.[option.value];
      option.disabled = !!availability && !availability.available;
      option.title = availability && !availability.available ? availability.reason : '';
    }
    if (document.activeElement !== min) min.value = String(thresholdFor(layer && !layer.empty ? layer.display : d));
    if (document.activeElement !== k) k.value = layer && !layer.empty ? layer.k.toFixed(layer.k >= 10 ? 0 : 1) : '';
    k.placeholder = 'auto';
    fit.disabled = d.shiftScale === null;
    if (document.activeElement !== width) width.value = String(d.shiftWidth);
    sliceBox.checked = d.shiftSlice;
    if (document.activeElement !== half) half.value = String(d.shiftSliceSpan);
    color.value = d.shiftColor;
    maxLabel.hidden = d.shiftColor !== 'magnitude';
    if (document.activeElement !== max) max.value = d.shiftColorMax ? String(d.shiftColorMax) : '';
    max.placeholder = layer && !layer.empty ? `auto (${layer.colorMax.toFixed(2)})` : 'auto';
    stepper.hidden = !(d.shiftSlice && layer && !layer.empty);
    if (!stepper.hidden) {
      const c = model.center();
      range.min = String(layer.sectionRange.min);
      range.max = String(layer.sectionRange.max);
      if (Number.isFinite(c)) {
        range.value = String(c);
        where.textContent = `section ${c} (${layer.zOf(c).toFixed(2)} µm)`;
      }
    }
    chip.set(shiftChip(layer, model.center()));
  }
  update();
  return { update };
}
