// The Connectivity tab: Graffinity's connectivity matrix and intermediate
// node table (Kerzner et al., "Graffinity: Visualizing Connectivity in Large
// Graphs", EuroVis 2017) over Viking's synapse links.
//
// The backend answers a query with the paths and both groupings of them
// (POST /api/connectivity/query). This module draws them: a matrix of start
// cells against end cells with a metric per cell, a table of the neurons the
// paths pass through, aggregation of either by label, a binned color scale
// with its legend, linked highlighting between the two views (pinned by a
// click, so it survives a screenshot), and the list of paths behind whatever
// is selected, grouped by cell sequence as Graffinity listed them. Nothing
// here is a port of the Graffinity source (AngularJS 1.5 and D3 3); the
// definitions come from the paper.

import { SYNAPSE_COLORS } from './network.js';
import { chooseSource } from './appcore.js';

const EDGE_TYPE_SUGGESTIONS = [
  'ConvPre', 'RibbonPre', 'BipConvPre', 'GABAPre', 'GapJunction', 'Desmosome',
  'Touch', 'Unknown', 'CisternPre', 'ConvPre|RibbonPre', 'Ribbon.*|BipConv.*',
];
const METRIC_TITLES = {
  paths: 'Number of paths', min_length: 'Shortest path (hops)', synapses: 'Number of synapses',
};
const PATH_LIST_LIMIT = 500;
const MAX_DRAWN_CELLS = 40000;
const STORAGE_KEY = 'sbfsem.connectivity.query';

// ColorBrewer Blues, light to dark. A cell paints its own background, so the
// ramp reads the same in the light and the dark theme.
const RAMP = ['#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#08519c', '#08306b'];

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rampColor(t) {
  const x = Math.min(1, Math.max(0, t)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const a = hexToRgb(RAMP[i]);
  const b = hexToRgb(RAMP[i + 1]);
  const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/**
 * A stepped color scale over [1, max] in `bins` equal (or, with `log`,
 * equal-ratio) steps, the way Graffinity's legend read: the first step is
 * the lightest color, the last the darkest; values above `max` fall in the
 * last step. `invert` darkens the small values instead (shortest path).
 */
export function makeScale(max, { bins = 6, log = false, invert = false } = {}) {
  const n = Math.max(1, Math.round(bins));
  const lo = 1;
  const hi = Math.max(max, lo + 1e-9);
  const edges = [];
  for (let i = 0; i <= n; i += 1) {
    const t = i / n;
    edges.push(log ? Math.exp(Math.log(lo) + t * (Math.log(hi) - Math.log(lo))) : lo + t * (hi - lo));
  }
  const bin = (value) => {
    if (!(value > 0)) return -1;
    for (let k = 1; k < n; k += 1) if (value < edges[k]) return k - 1;
    return n - 1;
  };
  const position = (i) => {
    const t = n > 1 ? i / (n - 1) : 1;
    return invert ? 1 - t : t;
  };
  return {
    n, edges, max: hi, bin,
    color: (value) => { const i = bin(value); return i < 0 ? null : rampColor(position(i)); },
    dark: (value) => { const i = bin(value); return i >= 0 && position(i) > 0.55; },
    stepColor: (i) => rampColor(position(i)),
  };
}

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const csvCell = (value) => {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

function downloadText(filename, text, type = 'text/csv') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * The legend's tick labels: each boundary with the fewest decimals that keeps
 * every label different from its neighbors.  Whole numbers were all it ever
 * printed, so a scale from 1 to 3 in six steps read 1 1 2 2 3 3.
 */
export function tickLabels(edges) {
  const text = (digits) => edges.map((value) => String(Number(value.toFixed(digits))));
  for (let digits = 0; digits <= 3; digits += 1) {
    const labels = text(digits);
    if (new Set(labels).size === labels.length) return labels;
  }
  return text(3);
}

/**
 * What the legend draws for a scale: how many steps, the label at each
 * boundary and each step's tooltip.  A scale whose top is its bottom (every
 * count is 1) has one step and one label, not `bins` steps all labeled 1; with
 * `capped` (the data went past the scale's top, so the last boundary is marked
 * with a +) such a scale keeps the two steps its cells use.
 */
export function legendSteps(scale, capped = false) {
  const flat = scale.edges[scale.n] - scale.edges[0] < 1e-6;
  if (flat && capped) {
    // Every count is 1 or past it (Max is 1): the data uses the lightest step and the darkest,
    // and the legend shows those two, the second labeled "1+".
    return { n: 2, ticks: ['1', '', '1+'], titles: ['1', 'more than 1'], colorSteps: [0, scale.n - 1] };
  }
  const labels = tickLabels(flat ? [scale.edges[0]] : scale.edges);
  const n = flat ? 1 : scale.n;
  return {
    n,
    ticks: labels.map((text, i) => text + (capped && i === labels.length - 1 ? '+' : '')),
    titles: flat ? [labels[0]] : Array.from({ length: n }, (_, i) => `${labels[i]} to ${labels[i + 1]}`),
    colorSteps: Array.from({ length: n }, (_, i) => i),
  };
}

/** Every item of `items` onto `target`.  `target.push(...items)` throws a RangeError from about 125,000 items. */
export function appendAll(target, items) {
  for (const item of items) target.push(item);
  return target;
}

/** The most hops of any path (at least 1).  `Math.max(...paths.map(...))` throws from about 125,000 paths. */
export function mostHops(paths) {
  let hops = 1;
  for (const path of paths) hops = Math.max(hops, (path.length - 1) / 2);
  return hops;
}

/**
 * The matrix as the screen shows it, as CSV text: the first line names the
 * columns, then one line per row, each with the name the row has on screen.
 * `grid` is `{ rows: [name], cols: [name], cells: [[text]] }`.
 */
export function matrixCsv(grid) {
  const lines = [['', ...grid.cols].map(csvCell).join(',')];
  grid.rows.forEach((name, i) => {
    lines.push([name, ...grid.cells[i].map((text) => text || '0')].map(csvCell).join(','));
  });
  return lines.join('\n') + '\n';
}

export class ConnectivityView {
  /**
   * @param {object} deps
   * @param {object} deps.api            the API client
   * @param {() => object[]} deps.records   loaded cell records
   * @param {(source, id) => void} deps.pick  put a cell in the Structure ID box (with its volume)
   * @param {(error) => void} deps.fail   report an error in the status line
   * @param {() => string} deps.defaultSource  the volume to offer first
   * @param {(source) => string} deps.volumeName  display name of a volume
   */
  constructor({ api, records, pick, fail, defaultSource, volumeName }) {
    Object.assign(this, { api, records, pick, fail, defaultSource, volumeName });
    this.$ = (id) => document.getElementById(id);
    this.result = null;
    this.expanded = { rows: new Set(), cols: new Set(), nodes: new Set() };
    this.selection = null;
    this.matrixCells = [];
    this.nodeCells = [];
    this.openGroups = new Set();
    this.restore();
    this.bind();
    this.buildPatternRow();
  }

  // -- the query panel -----------------------------------------------------
  bind() {
    const $ = this.$;
    $('cx-form').addEventListener('submit', (event) => {
      event.preventDefault();
      this.run();
    });
    $('cx-hops').addEventListener('change', () => this.buildPatternRow());
    $('cx-direction').addEventListener('change', () => this.buildPatternRow());
    $('cx-anchor-mode').addEventListener('change', () => this.buildPatternRow());
    $('cx-source').addEventListener('change', () => this.updateAnchorNote());
    for (const id of ['cx-metric', 'cx-scale', 'cx-agg-rows', 'cx-agg-cols', 'cx-order']) {
      $(id).addEventListener('change', () => this.renderMatrix());
    }
    // The scale's steps and ceiling serve both tables.
    for (const id of ['cx-bins', 'cx-max']) {
      $(id).addEventListener('change', () => { this.persistView(); this.renderMatrix(); this.renderNodes(); });
    }
    $('cx-agg-nodes').addEventListener('change', () => this.renderNodes());
    $('cx-list').addEventListener('change', () => { this.persistView(); this.renderPaths(); });
    $('cx-clear').addEventListener('click', () => this.clearSelection());
    $('cx-matrix-csv').addEventListener('click', () => this.downloadMatrix());
    $('cx-paths-csv').addEventListener('click', () => this.downloadPaths());
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.selection && this.$('view-connectivity').classList.contains('active')) {
        this.clearSelection();
      }
    });
    const types = $('cx-edge-types');
    for (const name of EDGE_TYPE_SUGGESTIONS) {
      const option = document.createElement('option');
      option.value = name;
      types.append(option);
    }
  }

  /**
   * Volumes with loaded cells; called when the tab opens or cells change.
   * `preferred` is the volume a right-click on a cell asked for: it wins over
   * the one the tab last showed (which `select.value` keeps, and which is why
   * the right-click used to open it on the wrong volume's cells).
   */
  loadedChanged(preferred = null) {
    const $ = this.$;
    const select = $('cx-source');
    const sources = [...new Set(this.records().map((r) => r.source))];
    const current = select.value || this.defaultSource();
    select.replaceChildren(...sources.map((source) => {
      const option = document.createElement('option');
      option.value = source;
      option.textContent = this.volumeName(source);
      return option;
    }));
    if (!sources.length) {
      const option = document.createElement('option');
      option.value = current;
      option.textContent = this.volumeName(current);
      select.append(option);
    }
    select.value = chooseSource({ sources, current, preferred });
    this.updateAnchorNote();
  }

  anchorRecords() {
    const source = this.$('cx-source').value;
    return this.records().filter((r) => r.source === source);
  }

  updateAnchorNote() {
    const note = this.$('cx-anchor-note');
    const mode = this.$('cx-anchor-mode').value;
    if (mode !== 'loaded') {
      note.textContent = 'every cell in the volume whose label matches the pattern';
      return;
    }
    const cells = this.anchorRecords();
    if (!cells.length) {
      note.textContent = 'no cells loaded in this volume — load one, or anchor on a label pattern';
      return;
    }
    const names = cells.slice(0, 6).map((r) => `c${r.id}`).join(', ');
    const more = cells.length > 6 ? `, +${cells.length - 6} more` : '';
    note.textContent = `${cells.length} loaded cell${cells.length === 1 ? '' : 's'}: ${names}${more}`;
  }

  hops() { return Number(this.$('cx-hops').value) || 2; }

  anchorPosition() {
    return this.$('cx-direction').value === 'upstream' ? this.hops() : 0;
  }

  /** The row of label and synapse-type patterns, one input per position. */
  buildPatternRow() {
    const row = this.$('cx-patterns');
    const keep = this.readPatterns();
    row.replaceChildren();
    const hops = this.hops();
    const anchor = this.anchorPosition();
    const loaded = this.$('cx-anchor-mode').value === 'loaded';
    for (let position = 0; position <= hops; position += 1) {
      if (position > 0) {
        const edge = el('input', 'cx-edge-input');
        edge.type = 'text';
        edge.dataset.hop = String(position - 1);
        edge.placeholder = 'any synapse';
        edge.title = `Synapse type of hop ${position}: a whole-string regular expression over this tool's names (ConvPre, RibbonPre, GapJunction…)`;
        edge.setAttribute('list', 'cx-edge-types');
        edge.value = keep.edges[position - 1] ?? '';
        edge.autocomplete = 'off';
        row.append(el('span', 'cx-arrow', '—'), edge, el('span', 'cx-arrow', '→'));
      }
      if (position === anchor && loaded) {
        const chip = el('span', 'cx-chip cx-anchor-chip', 'loaded cells');
        chip.title = 'The anchors of the query: the cells loaded in the chosen volume';
        row.append(chip);
        continue;
      }
      const node = el('input', 'cx-node-input');
      node.type = 'text';
      node.dataset.position = String(position);
      node.placeholder = position === anchor ? 'label pattern, e.g. CBb.*' : 'any label';
      node.title = position === 0
        ? 'Label of the first neuron of every path (whole-string regular expression)'
        : position === hops
          ? 'Label of the last neuron of every path'
          : `Label of the neuron at position ${position}`;
      node.value = keep.nodes[position] ?? '';
      node.autocomplete = 'off';
      if (position === anchor) node.classList.add('cx-anchor-input');
      row.append(node);
    }
    this.updateAnchorNote();
  }

  readPatterns() {
    const nodes = [];
    const edges = [];
    for (const input of this.$('cx-patterns').querySelectorAll('.cx-node-input')) {
      nodes[Number(input.dataset.position)] = input.value.trim();
    }
    for (const input of this.$('cx-patterns').querySelectorAll('.cx-edge-input')) {
      edges[Number(input.dataset.hop)] = input.value.trim();
    }
    return { nodes, edges };
  }

  queryBody() {
    const $ = this.$;
    const hops = this.hops();
    const { nodes, edges } = this.readPatterns();
    const nodePatterns = Array.from({ length: hops + 1 }, (_, i) => nodes[i] ?? '');
    const edgePatterns = Array.from({ length: hops }, (_, i) => edges[i] ?? '');
    const body = {
      source: $('cx-source').value,
      hops,
      min_hops: $('cx-shorter').checked ? 1 : hops,
      direction: $('cx-direction').value,
      node_patterns: nodePatterns,
      edge_patterns: edgePatterns,
      max_paths: Number($('cx-max-paths').value) || 20000,
      max_frontier: Number($('cx-max-frontier').value) || 300,
      allow_repeats: $('cx-repeats').checked,
      refresh: $('cx-refresh').checked,
    };
    if ($('cx-anchor-mode').value === 'loaded') {
      body.start_ids = this.anchorRecords().map((r) => r.id);
    } else {
      body.start_pattern = nodePatterns[this.anchorPosition()] || '.*';
    }
    return body;
  }

  persist() {
    try {
      const { nodes, edges } = this.readPatterns();
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        ...saved,
        hops: this.hops(), direction: this.$('cx-direction').value,
        mode: this.$('cx-anchor-mode').value, shorter: this.$('cx-shorter').checked,
        repeats: this.$('cx-repeats').checked, nodes, edges,
      }));
    } catch { /* storage unavailable */ }
  }

  persistView() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        ...saved,
        bins: this.$('cx-bins').value, max: this.$('cx-max').value, list: this.$('cx-list').value,
      }));
    } catch { /* storage unavailable */ }
  }

  restore() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch { saved = null; }
    if (!saved) return;
    const $ = this.$;
    if (saved.hops) $('cx-hops').value = String(saved.hops);
    if (saved.direction) $('cx-direction').value = saved.direction;
    if (saved.mode) $('cx-anchor-mode').value = saved.mode;
    $('cx-shorter').checked = Boolean(saved.shorter);
    $('cx-repeats').checked = Boolean(saved.repeats);
    if (saved.bins) $('cx-bins').value = saved.bins;
    if (saved.max) $('cx-max').value = saved.max;
    if (saved.list) $('cx-list').value = saved.list;
    this.savedPatterns = { nodes: saved.nodes || [], edges: saved.edges || [] };
    // buildPatternRow reads the inputs, which do not exist yet; seed it once.
    const original = this.readPatterns.bind(this);
    this.readPatterns = () => {
      this.readPatterns = original;
      return this.savedPatterns;
    };
  }

  // -- running ---------------------------------------------------------------
  async run() {
    const $ = this.$;
    const status = $('cx-status');
    let body;
    try {
      body = this.queryBody();
    } catch (error) { this.fail(error); return; }
    if (body.start_ids && !body.start_ids.length) {
      status.textContent = 'Load a cell in this volume first, or anchor on a label pattern.';
      status.className = 'muted error';
      return;
    }
    this.persist();
    $('cx-run').disabled = true;
    status.textContent = 'querying Viking…';
    status.className = 'muted';
    try {
      const result = await this.api.connectivity(body);
      this.setResult(result);
      $('cx-refresh').checked = false;
    } catch (error) {
      status.textContent = error?.detail || error?.message || String(error);
      status.className = 'muted error';
      this.fail(error);
    } finally {
      $('cx-run').disabled = false;
    }
  }

  setResult(result) {
    this.result = result;
    this.expanded = { rows: new Set(), cols: new Set(), nodes: new Set() };
    this.selection = null;
    this.openGroups = new Set();
    this.index();
    this.renderMatrix();
    this.renderNodes();
    this.renderPaths();
    const status = this.$('cx-status');
    const { matrix } = result;
    const parts = [
      `${result.n_paths} path${result.n_paths === 1 ? '' : 's'}`,
      `${matrix.rows.length} × ${matrix.cols.length}`,
      `${result.anchors.length} anchor${result.anchors.length === 1 ? '' : 's'}`,
      `${result.expanded} neuron${result.expanded === 1 ? '' : 's'} fetched`,
      `${result.seconds.toFixed(1)} s`,
    ];
    const warnings = [];
    if (result.truncated) warnings.push(`cut at ${result.paths.length} paths — raise the limit or narrow the patterns`);
    if (result.frontier_capped) warnings.push(`${result.unexpanded} neurons left unexpanded at a hop — raise "neurons per hop" or narrow the patterns`);
    status.textContent = parts.join(' · ') + (warnings.length ? ` — ${warnings.join('; ')}` : '');
    status.className = warnings.length ? 'muted error' : 'muted';
  }

  /** Lookups from the answer: paths per pair, per row, per column, per node. */
  index() {
    const r = this.result;
    this.nodes = r.nodes;
    this.pathLength = r.paths.map((p) => (p.length - 1) / 2);
    this.cellPaths = new Map();
    this.rowPaths = new Map();
    this.colPaths = new Map();
    r.matrix.rows.forEach((row, i) => {
      r.matrix.cols.forEach((col, j) => {
        const indices = r.matrix.cells[i][j];
        if (!indices.length) return;
        this.cellPaths.set(`${row}|${col}`, indices);
        this.rowPaths.set(row, (this.rowPaths.get(row) || []).concat(indices));
        this.colPaths.set(col, (this.colPaths.get(col) || []).concat(indices));
      });
    });
    this.nodeRows = new Map(r.node_table.rows.map((row) => [row.id, row.cells]));
  }

  labelOf(id) {
    const node = this.nodes[String(id)];
    return node?.label || '';
  }

  /** The metric of one connectivity set. */
  metric(indices, name) {
    if (!indices || !indices.length) return 0;
    if (name === 'paths') return indices.length;
    if (name === 'min_length') {
      let shortest = Infinity;
      for (const i of indices) shortest = Math.min(shortest, this.pathLength[i]);
      return shortest;
    }
    const used = new Set();
    for (const i of indices) {
      const path = this.result.paths[i];
      for (let k = 1; k < path.length; k += 2) used.add(path[k]);
    }
    let total = 0;
    for (const e of used) total += this.result.edges[e].n_synapses;
    return total;
  }

  /** The Bins and Max controls, read once per draw. */
  scaleOptions() {
    const bins = Math.min(12, Math.max(2, Number(this.$('cx-bins').value) || 6));
    const max = Number(this.$('cx-max').value) || 0;
    return { bins, max: max > 0 ? max : null, log: this.$('cx-scale').value === 'log' };
  }

  /**
   * Rows or columns as drawn: one per neuron, or one per label with the
   * neurons beneath it when that label is expanded (Graffinity's
   * expand/collapse of aggregated rows).
   */
  groups(ids, aggregate, expandedSet, totalOf, order) {
    let groups;
    if (!aggregate) {
      groups = ids.map((id) => ({ key: String(id), label: this.labelOf(id), ids: [id], id }));
    } else {
      const byLabel = new Map();
      for (const id of ids) {
        const label = this.labelOf(id) || '(unlabeled)';
        if (!byLabel.has(label)) byLabel.set(label, []);
        byLabel.get(label).push(id);
      }
      groups = [];
      for (const [label, members] of byLabel) {
        const open = expandedSet.has(label);
        groups.push({ key: `g:${label}`, label, ids: members, group: true, open });
        if (open) {
          for (const id of members) {
            groups.push({ key: `${label}/${id}`, label: this.labelOf(id), ids: [id], id, child: true });
          }
        }
      }
    }
    for (const g of groups) g.total = totalOf(g);
    if (order === 'label') {
      groups.sort((a, b) => (a.label || '').localeCompare(b.label || '') || (a.id ?? 0) - (b.id ?? 0));
    } else if (order === 'total') {
      groups.sort((a, b) => b.total - a.total);
    }
    if (aggregate && order !== 'query') {
      // Keep children right under their group whatever the order.
      const top = groups.filter((g) => !g.child);
      const children = groups.filter((g) => g.child);
      groups = [];
      for (const g of top) {
        groups.push(g);
        if (g.group && g.open) {
          appendAll(groups, children.filter((c) => c.key.startsWith(`${g.label}/`)));
        }
      }
    }
    return groups;
  }

  cellIndices(rowGroup, colGroup) {
    if (rowGroup.ids.length === 1 && colGroup.ids.length === 1) {
      return this.cellPaths.get(`${rowGroup.ids[0]}|${colGroup.ids[0]}`) || [];
    }
    const out = [];
    for (const r of rowGroup.ids) {
      for (const c of colGroup.ids) {
        const found = this.cellPaths.get(`${r}|${c}`);
        if (found) appendAll(out, found);
      }
    }
    return out;
  }

  idChip(id, source) {
    const chip = el('button', 'cx-id', `c${id}`);
    chip.type = 'button';
    chip.title = `Put c${id} (${this.volumeName(source)}) in the Structure ID box`;
    chip.addEventListener('click', (event) => {
      event.stopPropagation();
      this.pick(source, id);
    });
    return chip;
  }

  /** Graffinity's legend: one swatch per step, the boundaries beneath. */
  legend(scale, title, { capped = false } = {}) {
    const { n, ticks: tickText, titles, colorSteps } = legendSteps(scale, capped);
    const box = el('div', 'cx-legend');
    // How wide a step is belongs to the stylesheet (--cx-step); this says how many.
    box.style.setProperty('--cx-steps', String(n));
    box.append(el('div', 'cx-legend-title', title));
    const swatches = el('div', 'cx-legend-swatches');
    for (let i = 0; i < n; i += 1) {
      const swatch = el('span', 'cx-legend-swatch');
      swatch.style.background = scale.stepColor(colorSteps[i]);
      swatch.title = titles[i];
      swatches.append(swatch);
    }
    box.append(swatches);
    const ticks = el('div', 'cx-legend-ticks');
    tickText.forEach((text, i) => {
      if (!text) return;
      const tick = el('span', 'cx-legend-tick', text);
      tick.style.left = `${(100 * i) / n}%`;
      if (i === 0) tick.classList.add('first');
      else if (i === n) tick.classList.add('last');
      ticks.append(tick);
    });
    box.append(ticks);
    return box;
  }

  // -- the connectivity matrix -----------------------------------------------
  renderMatrix() {
    const $ = this.$;
    const holder = $('cx-matrix');
    this.matrixCells = [];
    this.matrixGrid = null;
    if (!this.result) return;
    const r = this.result;
    const metricName = $('cx-metric').value;
    const order = $('cx-order').value;
    const source = r.source;
    holder.replaceChildren();
    if (!r.paths.length) {
      holder.append(el('p', 'empty', 'No paths match. Loosen a pattern, add a hop, or switch the direction — a ganglion cell has inputs, not outputs.'));
      return;
    }

    const rows = this.groups(r.matrix.rows, $('cx-agg-rows').checked, this.expanded.rows,
      (g) => this.metric(g.ids.flatMap((id) => this.rowPaths.get(id) || []), metricName), order);
    const cols = this.groups(r.matrix.cols, $('cx-agg-cols').checked, this.expanded.cols,
      (g) => this.metric(g.ids.flatMap((id) => this.colPaths.get(id) || []), metricName), order);

    if (rows.length * cols.length > MAX_DRAWN_CELLS) {
      holder.append(el('p', 'empty',
        `${rows.length} × ${cols.length} cells is more than this view draws (${MAX_DRAWN_CELLS}). Group rows or columns by label, or narrow the query.`));
      return;
    }

    // Values first, for the color scale.
    const values = rows.map((row) => cols.map((col) => {
      const indices = this.cellIndices(row, col);
      return { indices, value: this.metric(indices, metricName) };
    }));
    let dataMax = 0;
    for (const line of values) for (const cell of line) dataMax = Math.max(dataMax, cell.value);
    const opts = this.scaleOptions();
    const max = opts.max ?? dataMax;
    const scale = makeScale(max, { bins: opts.bins, log: opts.log, invert: metricName === 'min_length' });

    const table = el('table', 'cx-matrix');
    const thead = el('thead');
    const head = el('tr');
    const corner = el('th', 'cx-corner');
    corner.append(el('span', 'cx-axis', r.direction === 'upstream' ? 'inputs ↓ · loaded cells →' : 'loaded cells ↓ · partners →'));
    head.append(corner);
    for (const col of cols) {
      const th = el('th', 'cx-col');
      th.append(this.headerContent(col, source, 'cols'));
      th.title = col.group ? `${col.label}: ${col.ids.length} cells` : `c${col.id} ${col.label}`;
      if (col.child) th.classList.add('cx-child');
      if (col.group) th.classList.add('cx-group');
      head.append(th);
    }
    thead.append(head);
    table.append(thead);

    // What the .csv holds: the names and numbers on screen, not text read back from the table.
    const grid = { rows: rows.map((g) => this.groupName(g)), cols: cols.map((g) => this.groupName(g)), cells: [] };
    const tbody = el('tbody');
    rows.forEach((row, i) => {
      const tr = el('tr');
      if (row.child) tr.classList.add('cx-child');
      if (row.group) tr.classList.add('cx-group');
      const th = el('th', 'cx-row');
      th.append(this.headerContent(row, source, 'rows'));
      tr.append(th);
      grid.cells.push([]);
      cols.forEach((col, j) => {
        const { indices, value } = values[i][j];
        const td = el('td', 'cx-cell');
        if (value > 0) {
          td.style.background = scale.color(value);
          td.style.color = scale.dark(value) ? '#fff' : '#16191d';
          td.textContent = Number.isInteger(value) ? String(value) : value.toFixed(1);
          td.title = this.cellTitle(row, col, indices);
          td._paths = indices;
          td._view = 'matrix';
          td.addEventListener('mouseenter', () => this.applyLinks(td));
          td.addEventListener('mouseleave', () => this.applyLinks(null));
          td.addEventListener('click', () => this.select(td, indices, `${this.groupName(row)} → ${this.groupName(col)}`));
          this.matrixCells.push(td);
        }
        grid.cells[i].push(td.textContent);
        tr.append(td);
      });
      tbody.append(tr);
    });
    this.matrixGrid = grid;
    table.append(tbody);
    holder.append(table);
    holder.append(this.legend(scale, METRIC_TITLES[metricName] + (opts.log ? ', log steps' : ''),
      { capped: opts.max !== null && dataMax > opts.max }));
    this.restoreSelection();
  }

  groupName(g) {
    return g.group ? `${g.label} (${g.ids.length})` : `c${g.id}${g.label ? ` ${g.label}` : ''}`;
  }

  headerContent(g, source, which) {
    const wrap = el('span', 'cx-head');
    if (g.group) {
      const toggle = el('button', 'cx-toggle', g.open ? '▾' : '▸');
      toggle.type = 'button';
      toggle.title = g.open ? 'Collapse this label' : `Expand the ${g.ids.length} cells with this label`;
      toggle.addEventListener('click', (event) => {
        event.stopPropagation();
        const set = this.expanded[which];
        if (set.has(g.label)) set.delete(g.label); else set.add(g.label);
        if (which === 'nodes') this.renderNodes(); else this.renderMatrix();
      });
      wrap.append(toggle, el('span', 'cx-label', `${g.label} (${g.ids.length})`));
    } else {
      wrap.append(this.idChip(g.id, source));
      if (g.label) wrap.append(el('span', 'cx-label', g.label));
    }
    return wrap;
  }

  cellTitle(row, col, indices) {
    const paths = this.metric(indices, 'paths');
    const synapses = this.metric(indices, 'synapses');
    const shortest = this.metric(indices, 'min_length');
    return `${this.groupName(row)} → ${this.groupName(col)}: ${paths} path${paths === 1 ? '' : 's'}, `
      + `${synapses} synapse${synapses === 1 ? '' : 's'}, shortest ${shortest} hop${shortest === 1 ? '' : 's'}`;
  }

  // -- the intermediate node table -------------------------------------------
  renderNodes() {
    const $ = this.$;
    const holder = $('cx-nodes');
    this.nodeCells = [];
    if (!this.result) return;
    const r = this.result;
    holder.replaceChildren();
    const columns = r.node_table.columns;
    if (!columns.length || !r.node_table.rows.length) {
      holder.append(el('p', 'empty', r.paths.length
        ? 'One-hop paths have no intermediate nodes.'
        : 'No paths, so no intermediate nodes.'));
      return;
    }
    const ids = r.node_table.rows.map((row) => row.id);
    const totalOf = (g) => g.ids.reduce((sum, id) => {
      const cells = this.nodeRows.get(id) || {};
      return sum + Object.values(cells).reduce((s, v) => s + v.length, 0);
    }, 0);
    const rows = this.groups(ids, $('cx-agg-nodes').checked, this.expanded.nodes, totalOf, 'total');

    const values = rows.map((row) => columns.map((column) => {
      const indices = [];
      for (const id of row.ids) {
        const found = (this.nodeRows.get(id) || {})[column];
        if (found) appendAll(indices, found);
      }
      return indices;
    }));
    let dataMax = 0;
    for (const line of values) for (const cell of line) dataMax = Math.max(dataMax, cell.length);
    const opts = this.scaleOptions();
    const max = opts.max ?? dataMax;
    const scale = makeScale(max, { bins: opts.bins, log: opts.log });

    const table = el('table', 'cx-matrix cx-nodetable');
    const thead = el('thead');
    const head = el('tr');
    head.append(el('th', 'cx-corner', 'via'));
    for (const column of columns) {
      const [length, position] = column.split(':');
      const th = el('th', 'cx-col');
      th.append(el('span', 'cx-head', `len ${length} · pos ${position}`));
      th.title = `Paths of length ${length} whose node at position ${position} is this neuron`;
      head.append(th);
    }
    head.append(el('th', 'cx-col cx-total', 'total'));
    thead.append(head);
    table.append(thead);

    const tbody = el('tbody');
    rows.forEach((row, i) => {
      const tr = el('tr');
      if (row.child) tr.classList.add('cx-child');
      if (row.group) tr.classList.add('cx-group');
      const th = el('th', 'cx-row');
      th.append(this.headerContent(row, r.source, 'nodes'));
      tr.append(th);
      let total = 0;
      columns.forEach((column, j) => {
        const indices = values[i][j];
        const td = el('td', 'cx-cell');
        if (indices.length) {
          td.style.background = scale.color(indices.length);
          td.style.color = scale.dark(indices.length) ? '#fff' : '#16191d';
          td.textContent = String(indices.length);
          td.title = `${this.groupName(row)} at position ${column.split(':')[1]} of ${indices.length} path${indices.length === 1 ? '' : 's'} of length ${column.split(':')[0]}`;
          td._paths = indices;
          td._view = 'nodes';
          td.addEventListener('mouseenter', () => this.applyLinks(td));
          td.addEventListener('mouseleave', () => this.applyLinks(null));
          td.addEventListener('click', () => this.select(td, indices, `via ${this.groupName(row)}`));
          this.nodeCells.push(td);
          total += indices.length;
        }
        tr.append(td);
      });
      tr.append(el('td', 'cx-cell cx-total', String(total)));
      tbody.append(tr);
    });
    table.append(tbody);
    holder.append(table);
    holder.append(this.legend(scale, 'Number of paths' + (opts.log ? ', log steps' : ''),
      { capped: opts.max !== null && dataMax > opts.max }));
    this.restoreSelection();
  }

  // -- linking the views -------------------------------------------------------
  /**
   * Outline, in both tables, every cell that shares a path with `hovered`,
   * or with the pinned selection when nothing is hovered. A click pins; a
   * second click on the same cell, Escape, or Clear unpins.
   */
  applyLinks(hovered) {
    const origin = hovered || this.selection?.cell || null;
    const indices = hovered ? hovered._paths : this.selection?.indices;
    const cells = [...this.matrixCells, ...this.nodeCells];
    if (!indices || !origin) {
      for (const cell of cells) cell.classList.remove('linked');
      return;
    }
    const wanted = new Set(indices);
    if (wanted.size * cells.length > 4_000_000) return;  // not worth the stall
    for (const cell of cells) {
      cell.classList.toggle('linked', cell !== origin && cell._paths.some((i) => wanted.has(i)));
    }
  }

  select(cell, indices, title) {
    if (this.selection && this.selection.key === cell.title) {
      this.clearSelection();
      return;
    }
    for (const c of [...this.matrixCells, ...this.nodeCells]) c.classList.remove('selected');
    cell.classList.add('selected');
    this.selection = { indices, title, key: cell.title, cell };
    this.$('cx-clear').hidden = false;
    this.applyLinks(null);
    this.renderPaths();
  }

  clearSelection() {
    this.selection = null;
    this.$('cx-clear').hidden = true;
    for (const c of [...this.matrixCells, ...this.nodeCells]) c.classList.remove('selected', 'linked');
    this.renderPaths();
  }

  /** After a redraw, find the pinned cell again by its tooltip and re-link. */
  restoreSelection() {
    if (!this.selection) return;
    const match = [...this.matrixCells, ...this.nodeCells].find((c) => c.title === this.selection.key);
    if (match) {
      match.classList.add('selected');
      this.selection.cell = match;
    }
    this.applyLinks(null);
  }

  // -- the path list -------------------------------------------------------------
  renderPaths() {
    const $ = this.$;
    const holder = $('cx-paths');
    holder.replaceChildren();
    const title = this.selection?.title;
    $('cx-paths-title').textContent = title ? `Paths: ${title}` : 'Paths';
    const note = $('cx-paths-note');
    if (!this.result) { note.textContent = ''; return; }
    const r = this.result;
    const indices = this.selection?.indices;
    const chosen = indices ?? r.paths.map((_, i) => i);
    if ($('cx-list').value === 'flat') this.renderFlatPaths(holder, chosen, Boolean(indices));
    else this.renderGroupedPaths(holder, chosen, Boolean(indices));
  }

  /** One row per cell sequence, as Graffinity's path list: Hops and Freq,
   *  opening to the synapse-type combinations, each opening to the
   *  structure IDs behind every hop. */
  renderGroupedPaths(holder, chosen, narrowed) {
    const r = this.result;
    const note = this.$('cx-paths-note');
    const groups = this.groupPaths(chosen);
    const shown = groups.slice(0, PATH_LIST_LIMIT);
    const what = `${groups.length} cell sequence${groups.length === 1 ? '' : 's'}, ${chosen.length} path${chosen.length === 1 ? '' : 's'}`;
    note.textContent = shown.length < groups.length
      ? `showing ${shown.length} of ${what}`
      : narrowed ? what : `all ${what} — click a matrix cell or an intermediate node to narrow`;
    if (!chosen.length) return;

    const table = el('table', 'cx-pathlist');
    const thead = el('thead');
    const head = el('tr');
    head.append(el('th', ''), el('th', 'text', 'Paths'), el('th', '', 'Hops'), el('th', '', 'Freq'));
    thead.append(head);
    table.append(thead);
    const tbody = el('tbody');
    for (const group of shown) {
      const open = this.openGroups.has(group.key);
      const tr = el('tr', 'cx-group-row');
      const toggleCell = el('td');
      const toggle = el('button', 'cx-toggle', open ? '▾' : '▸');
      toggle.type = 'button';
      toggle.title = open ? 'Hide the synapse types' : `Show the ${group.paths.length} synapse-type combination${group.paths.length === 1 ? '' : 's'}`;
      toggleCell.append(toggle);
      const cells = el('td', 'text cx-sequence');
      group.nodes.forEach((id, k) => {
        if (k > 0) cells.append(el('span', 'cx-link-line'));
        const box = el('span', 'cx-box');
        box.append(this.idChip(id, r.source));
        box.append(el('span', 'cx-box-label', this.labelOf(id) || '—'));
        cells.append(box);
      });
      tr.append(toggleCell, cells, el('td', '', String(group.hops)), el('td', 'cx-freq', String(group.paths.length)));
      tbody.append(tr);

      const detail = el('tr', 'cx-group-detail');
      detail.hidden = !open;
      const cell = el('td');
      cell.colSpan = 4;
      cell.append(this.edgeTable(group));
      detail.append(cell);
      tbody.append(detail);

      toggle.addEventListener('click', () => {
        const now = detail.hidden;
        detail.hidden = !now;
        toggle.textContent = now ? '▾' : '▸';
        if (now) this.openGroups.add(group.key); else this.openGroups.delete(group.key);
      });
    }
    table.append(tbody);
    holder.append(table);
  }

  /** Graffinity's "Edge 1 … Edge n" table for one cell sequence. */
  edgeTable(group) {
    const r = this.result;
    const table = el('table', 'cx-edgetable');
    const thead = el('thead');
    const head = el('tr');
    head.append(el('th', ''));
    for (let k = 1; k <= group.hops; k += 1) head.append(el('th', 'text', `Edge ${k}`));
    thead.append(head);
    table.append(thead);
    const tbody = el('tbody');
    for (const index of group.paths) {
      const path = r.paths[index];
      const edges = path.filter((_, k) => k % 2 === 1).map((e) => r.edges[e]);
      const tr = el('tr');
      const toggle = el('button', 'cx-toggle', '▸');
      toggle.type = 'button';
      toggle.title = 'Show the synapse structures behind each hop';
      const toggleCell = el('td');
      toggleCell.append(toggle);
      tr.append(toggleCell);
      for (const edge of edges) {
        const td = el('td', 'text');
        const name = el('span', 'cx-edge-name', edge.type + (edge.n_synapses > 1 ? ` ×${edge.n_synapses}` : ''));
        name.style.color = SYNAPSE_COLORS[edge.type] || '#7f8c99';
        td.append(name);
        tr.append(td);
      }
      tbody.append(tr);
      const links = el('tr', 'cx-links-row');
      links.hidden = true;
      links.append(el('td'));
      for (const edge of edges) {
        const td = el('td', 'text cx-links');
        td.title = `Source and target structure IDs in ${this.volumeName(r.source)}`;
        for (const [a, b] of edge.links) td.append(el('div', '', `${a} → ${b}`));
        links.append(td);
      }
      tbody.append(links);
      toggle.addEventListener('click', () => {
        links.hidden = !links.hidden;
        toggle.textContent = links.hidden ? '▸' : '▾';
      });
    }
    table.append(tbody);
    return table;
  }

  groupPaths(indices) {
    const groups = new Map();
    for (const index of indices) {
      const path = this.result.paths[index];
      const nodes = path.filter((_, k) => k % 2 === 0);
      const key = nodes.join('>');
      let group = groups.get(key);
      if (!group) {
        group = { key, nodes, hops: nodes.length - 1, paths: [] };
        groups.set(key, group);
      }
      group.paths.push(index);
    }
    return [...groups.values()].sort((a, b) => b.paths.length - a.paths.length
      || a.hops - b.hops || a.key.localeCompare(b.key));
  }

  /** Every path on its own line: cells and synapse types in order. */
  renderFlatPaths(holder, chosen, narrowed) {
    const r = this.result;
    const note = this.$('cx-paths-note');
    const shown = chosen.slice(0, PATH_LIST_LIMIT);
    note.textContent = shown.length < chosen.length
      ? `showing ${shown.length} of ${chosen.length}`
      : narrowed
        ? `${chosen.length} path${chosen.length === 1 ? '' : 's'}`
        : (chosen.length ? `all ${chosen.length} — click a matrix cell or an intermediate node to narrow` : '');
    for (const index of shown) {
      const path = r.paths[index];
      const line = el('div', 'cx-path');
      for (let k = 0; k < path.length; k += 1) {
        if (k % 2 === 0) {
          const id = path[k];
          const cell = el('span', 'cx-path-node');
          cell.append(this.idChip(id, r.source));
          const label = this.labelOf(id);
          if (label) cell.append(el('span', 'cx-label', label));
          line.append(cell);
        } else {
          const edge = r.edges[path[k]];
          const color = SYNAPSE_COLORS[edge.type] || '#7f8c99';
          const arrow = el('button', 'cx-path-edge', `—${edge.type}${edge.n_synapses > 1 ? ` ×${edge.n_synapses}` : ''}→`);
          arrow.type = 'button';
          arrow.style.color = color;
          arrow.title = 'Show the synapse structures behind this connection';
          arrow.addEventListener('click', () => {
            const existing = line.querySelector(`.cx-links[data-edge="${path[k]}"]`);
            if (existing) { existing.remove(); return; }
            const list = el('div', 'cx-links');
            list.dataset.edge = String(path[k]);
            list.append(el('span', 'muted',
              `${edge.n_synapses} synapse structure${edge.n_synapses === 1 ? '' : 's'} in ${this.volumeName(r.source)}: `));
            list.append(el('span', '', edge.links.map(([a, b]) => `${a} → ${b}`).join(', ')));
            line.append(list);
          });
          line.append(arrow);
        }
      }
      holder.append(line);
    }
  }

  // -- exports ----------------------------------------------------------------
  downloadMatrix() {
    if (!this.result || !this.matrixGrid) return;
    const metricName = this.$('cx-metric').value;
    downloadText(`connectivity-${this.result.source}-${metricName}.csv`, matrixCsv(this.matrixGrid));
  }

  downloadPaths() {
    if (!this.result) return;
    const r = this.result;
    const chosen = this.selection ? this.selection.indices : r.paths.map((_, i) => i);
    const hops = mostHops(r.paths);
    const header = ['Volume'];
    for (let p = 0; p <= hops; p += 1) {
      header.push(`Node${p}`, `Label${p}`);
      if (p < hops) header.push(`Synapse${p + 1}`, `Count${p + 1}`, `Structures${p + 1}`);
    }
    const lines = [header.map(csvCell).join(',')];
    for (const index of chosen) {
      const path = r.paths[index];
      const row = [r.source];
      for (let k = 0; k < path.length; k += 1) {
        if (k % 2 === 0) {
          row.push(path[k], this.labelOf(path[k]));
        } else {
          const edge = r.edges[path[k]];
          row.push(edge.type, edge.n_synapses, edge.links.map(([a, b]) => `${a}->${b}`).join(';'));
        }
      }
      while (row.length < header.length) row.push('');
      lines.push(row.map(csvCell).join(','));
    }
    downloadText(`paths-${r.source}.csv`, lines.join('\n') + '\n');
  }
}
