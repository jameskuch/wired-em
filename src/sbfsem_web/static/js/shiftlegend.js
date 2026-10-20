// The Position shift layer's legend: what the arrows' colors mean, the pair they
// join and how much they are exaggerated, in a corner of the render while a
// cell draws the layer (asked for 2026-10-16: "direction also could have a color
// and then we need to add a legend").  The main viewport has one, and so has
// every Render compare pane that draws the layer.
//
// `legendModel` is pure (tests/js/test_shiftlegend.mjs): a layer (shiftlayer.js)
// in, the words, ticks and colors out.  `ShiftLegend` puts a model on the page;
// it builds its elements with createElement and textContent, and the colors it
// sets are numbers it computed, never text from a cell or a server.

import {
  DIRECTION_LIGHTNESS, DIRECTION_SATURATION, PAIR_LABELS, RAMPS, badgeText, hslToRgb,
} from './shifts.js';

/** Which two positions a pair joins, as the legend's second line says it. */
export const PAIR_STAGES_TEXT = Object.freeze({
  correction: 'registered → corrected',
  transforms: 'stored → registered',
  both: 'stored → corrected',
});

const hex = (rgb) => `#${rgb.map((c) => Math.round(Math.min(1, Math.max(0, c)) * 255)
  .toString(16).padStart(2, '0')).join('')}`;

/** A length in micrometers with as many decimals as it needs: 12, 1.5, 0.56, 0.004. */
export function formatUm(v) {
  if (!(v > 0)) return '0';
  const decimals = v >= 10 ? 0 : v >= 1 ? 1 : v >= 0.1 ? 2 : 3;
  return v.toFixed(decimals);
}

/** The hues of the direction wheel, `n` of them, from +X counter-clockwise, for a background. */
export function wheelColors(inverted = false, n = 36) {
  const lightness = DIRECTION_LIGHTNESS[inverted ? 'light' : 'dark'];
  return Array.from({ length: n }, (_, i) => hex(hslToRgb(i / n, DIRECTION_SATURATION, lightness)));
}

/**
 * What the legend shows for one layer: `null` for none (no layer, or nothing to
 * draw).  `inverted`: the light viewport, whose ramp and hues are its own.
 *
 * Returns `{ mode, title, sub, badge, ramp?, wheel?, cell?, key }`: `title` is the
 * pair drawn ("Section correction"), `sub` the stages it joins, `badge` the
 * exaggeration ("×14", or '' at ×1).  Magnitude has `ramp`: `{ colors, ticks }`,
 * ticks at 0, half and all of Full color at, `≥` on the last when some move is
 * longer; Direction has `wheel`: `{ colors }` (hue 0 is +X, then toward +Y);
 * Cell color has `cell`: the color.  `key` is the same for two layers that draw
 * the same legend.
 */
export function legendModel(layer, { inverted = false, cellColor = null } = {}) {
  // Only colors that are on screen: arrows that are drawn, at least one of them.
  // Rings are one neutral gray, so they need no key (review of 2026-10-16: with
  // Arrows off, or every move under Only shifts above, a ramp described nothing).
  if (!layer || layer.empty || !layer.arrows || !(layer.sel?.n > 0)) return null;
  const mode = layer.display.shiftColor;
  const model = {
    mode,
    title: PAIR_LABELS[layer.pair] || layer.pair,
    sub: PAIR_STAGES_TEXT[layer.pair] || '',
    badge: badgeText(layer.k),
  };
  if (mode === 'direction') {
    model.wheel = { colors: wheelColors(inverted) };
  } else if (mode === 'cell') {
    model.cell = typeof cellColor === 'string' && /^#[0-9a-f]{6}$/i.test(cellColor) ? cellColor : '#999999';
  } else {
    const max = layer.colorMax;
    const beyond = (layer.field?.stats?.max ?? 0) > max * (1 + 1e-9);
    model.ramp = {
      colors: [...RAMPS[inverted ? 'light' : 'dark']],
      ticks: [
        { at: 0, text: '0' },
        { at: 0.5, text: formatUm(max / 2) },
        { at: 1, text: `${beyond ? '≥' : ''}${formatUm(max)} µm` },
      ],
    };
  }
  model.key = JSON.stringify(model);
  return model;
}

/**
 * The legend blocks for several cells' layers: one per different legend, in the
 * order given, each named by its cells when there is more than one cell.
 * `entries`: `[{ id, layer, cellColor, name? }]`; `name` is how the cell is called
 * (`c1178`, or `c1178 i` beside a cell of another volume: an ID travels with its
 * volume), `c<id>` when not given.  At most `limit` blocks.
 */
export function legendBlocks(entries, { inverted = false, limit = 3 } = {}) {
  const blocks = [];
  const byKey = new Map();
  let drawn = 0;
  for (const entry of entries) {
    const model = legendModel(entry.layer, { inverted, cellColor: entry.cellColor });
    if (!model) continue;
    drawn += 1;
    const name = entry.name || `c${entry.id}`;
    const hit = byKey.get(model.key);
    if (hit) { hit.cells.push(name); continue; }
    const block = { ...model, cells: [name] };
    byKey.set(model.key, block);
    blocks.push(block);
  }
  for (const block of blocks) block.who = drawn > 1 ? block.cells.join(' ') : '';
  return blocks.slice(0, limit);
}

// -- on the page ---------------------------------------------------------------------------

function div(className, text = '') {
  const out = document.createElement('div');
  out.className = className;
  if (text) out.textContent = text;
  return out;
}

/**
 * The CSS of the direction wheel: a conic gradient through `colors` (hue 0 at
 * +X, then counter-clockwise toward +Y, as the arrows are colored).  CSS runs a
 * conic gradient clockwise from where `from` puts it, so the hues go in reverse.
 */
export function wheelGradient(colors) {
  const n = colors.length;
  const stops = [];
  for (let i = 0; i <= n; i += 1) stops.push(`${colors[(n - i) % n]} ${((i / n) * 360).toFixed(1)}deg`);
  return `conic-gradient(from 90deg, ${stops.join(', ')})`;
}

/**
 * The legend element in `host` (a viewport, positioned): `update(blocks)` shows
 * `legendBlocks` output, or hides it when there are none.  Built on demand, so a
 * viewport that never draws the layer never gets one.
 */
export class ShiftLegend {
  constructor(host) {
    this.host = host;
    this.el = null;
    this._key = null;
    this._below = null;
    this._observer = null;
  }

  /**
   * Keep the legend above `other`, an element along the bottom of the same host
   * (the main viewport's chip, which wraps onto a second row in a narrow window
   * or with the slice's stepper): its bottom is set from where `other` begins,
   * each time `other` changes size.  Without one, the stylesheet places it.
   */
  stackAbove(other) {
    this._below = other || null;
    this._observer?.disconnect();
    this._observer = null;
    if (other && typeof ResizeObserver !== 'undefined') {
      this._observer = new ResizeObserver(() => this._place());
      this._observer.observe(other);
    }
    this._place();
  }

  _place() {
    const other = this._below;
    if (!this.el || !other) return;
    if (other.hidden || !other.getBoundingClientRect || !other.offsetHeight) {
      this.el.style.bottom = '';
      return;
    }
    const gap = this.host.getBoundingClientRect().bottom - other.getBoundingClientRect().top;
    this.el.style.bottom = `${Math.max(0, Math.round(gap)) + 8}px`;
  }

  update(blocks) {
    if (!blocks?.length) {
      if (this.el) this.el.hidden = true;
      this._key = null;
      return;
    }
    if (!this.el) {
      this.el = div('shift-legend');
      this.el.setAttribute('role', 'note');
      this.el.setAttribute('aria-label', 'Position shift legend');
      this.host.append(this.el);
    }
    this.el.hidden = false;
    this._place();
    const key = JSON.stringify(blocks.map((b) => [b.key, b.who]));
    if (key === this._key) return;
    this._key = key;
    this.el.replaceChildren(...blocks.map((block) => this._block(block)));
  }

  _block(block) {
    const out = div('shift-legend-block');
    const head = div('shift-legend-head');
    const title = div('shift-legend-title', block.who ? `${block.who} · ${block.title}` : block.title);
    head.append(title);
    if (block.badge) head.append(div('shift-legend-badge', block.badge));
    out.append(head);
    if (block.sub) out.append(div('shift-legend-sub', block.sub));
    if (block.ramp) {
      const bar = div('shift-legend-ramp');
      bar.style.background = `linear-gradient(to right, ${block.ramp.colors.join(', ')})`;
      const ticks = div('shift-legend-ticks');
      for (const tick of block.ramp.ticks) {
        const span = document.createElement('span');
        span.textContent = tick.text;
        // The ends sit inside the bar's ends, the middle on it.
        if (tick.at <= 0) span.style.left = '0';
        else if (tick.at >= 1) span.style.right = '0';
        else { span.style.left = `${tick.at * 100}%`; span.style.transform = 'translateX(-50%)'; }
        ticks.append(span);
      }
      out.append(bar, ticks);
    } else if (block.wheel) {
      const row = div('shift-legend-wheel-row');
      const wheel = div('shift-legend-wheel');
      wheel.style.background = wheelGradient(block.wheel.colors);
      const axes = div('shift-legend-axes');
      axes.textContent = 'hue: direction\n+X →   +Y ↑\nthe volume\'s X, Y';
      row.append(wheel, axes);
      out.append(row);
    } else if (block.cell) {
      const row = div('shift-legend-wheel-row');
      const swatch = div('shift-legend-swatch');
      swatch.style.background = block.cell;
      row.append(swatch, div('shift-legend-axes', "the cell's color"));
      out.append(row);
    }
    return out;
  }

  /** Take the element away (its viewport is going). */
  destroy() {
    this._observer?.disconnect();
    this._observer = null;
    this.el?.remove();
    this.el = null;
    this._key = null;
  }
}
