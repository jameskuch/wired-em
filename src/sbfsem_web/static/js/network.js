// Cytoscape.js connectivity view.
//
// This is the interactive graph interface the MATLAB toolbox never had: its
// util/network plots were static graph/plot output. The backend serves the
// elements JSON for one volume (cells in different volumes cannot be
// connected); this module keeps that answer and draws it as many times as the
// controls change -- names, hidden link types, minimum weight -- without a
// second request.

import cytoscape from 'cytoscape';

// One color per link type; the legend on the tab shows whichever are present.
const SYNAPSE_COLORS = {
  ConvPre: '#ff474c', ConvPost: '#fdaa48', RibbonPre: '#15b01a',
  RibbonPost: '#40a368', BipConvPre: '#0652ff', BipConvPost: '#04d8b2',
  GABAPre: '#c79fef', GABAPost: '#c79fef', GapJunction: '#94d2fc',
  Desmosome: '#069af3', Touch: '#13eac9', Unknown: '#808080',
  CisternPre: '#e17055', CisternPost: '#fab1a0',
  BasalTA: '#b8860b', BasalNTA: '#daa520', BasalMNTA: '#eec96a',
  UntaggedPost: '#dbdbdb',
  // Viking 257-270, the Marc lab volumes only.
  DistalJunction: '#556b2f', FlatContact: '#bc8f8f', BubblesSwirls: '#b0c4de',
  PeriGJAdherens: '#4682b4', CaveolaString: '#9acd32', CoatedPits: '#d2b48c',
  GJEndo: '#66cdaa', DenseCoreVesicle: '#8b4513', StartVaricosity: '#da70d6',
  EndVaricosity: '#9932cc', GlialAdjacency: '#009999',
  HorizontalCell: '#cc9900', ExtracellularVesicle: '#f08080', BasalBody: '#696969',
};
const FALLBACK_COLOR = '#7f8c99';
const edgeColor = (type) => SYNAPSE_COLORS[type] || FALLBACK_COLOR;

/**
 * The page's own colors for the parts of the graph that are not data: label
 * text and its halo, the ring on a loaded cell, a selected edge.  They were
 * fixed light-on-dark values, white on a white canvas in the light theme.
 * Read from the stylesheet's tokens when the graph is drawn (and again when
 * the theme changes), with the dark theme's values where there is no page.
 */
export function themeColors(element = null) {
  const fallback = { ink: '#e8eaed', panel: '#1e2227' };
  if (!element || typeof getComputedStyle !== 'function') return fallback;
  const css = getComputedStyle(element);
  const token = (name, otherwise) => css.getPropertyValue(name).trim() || otherwise;
  return { ink: token('--ink', fallback.ink), panel: token('--panel', fallback.panel) };
}

/** The graph's stylesheet, for the label mode and the theme's colors. */
export function graphStyle(labels, colors) {
  return [
    {
      selector: 'node',
      style: {
        'background-color': '#4d6b9a',
        label: 'data(label)',
        color: colors.ink,
        'font-size': 10,
        'text-valign': 'center',
        'text-halign': 'right',
        'text-margin-x': 6,
        'text-outline-width': 2,
        'text-outline-color': colors.panel,
        'text-wrap': 'ellipsis',
        'text-max-width': labels === 'both' ? 170 : 110,
        width: 'mapData(degree, 0, 20, 14, 44)',
        height: 'mapData(degree, 0, 20, 14, 44)',
      },
    },
    {
      // The cells you loaded, as against the partners the links reached.
      selector: 'node[?loaded]',
      style: { 'border-width': 2, 'border-color': colors.ink },
    },
    {
      selector: 'node:selected',
      style: { 'background-color': '#f0a04b', 'border-width': 2, 'border-color': colors.ink },
    },
    {
      selector: 'edge',
      style: {
        width: 'mapData(weight, 1, 20, 1, 8)',
        'line-color': (edge) => edgeColor(edge.data('primaryType')),
        'target-arrow-color': (edge) => edgeColor(edge.data('primaryType')),
        // Pre -> post gets an arrow; a gap junction, adherens, touch or
        // unknown link has no such sides and is drawn dashed instead.
        'target-arrow-shape': (edge) => (edge.data('directed') ? 'triangle' : 'none'),
        'line-style': (edge) => (edge.data('directed') ? 'solid' : 'dashed'),
        'curve-style': 'bezier',
        opacity: 0.8,
      },
    },
    { selector: 'edge:selected', style: { 'line-color': colors.ink, opacity: 1 } },
  ];
}

/** What a node is called on screen: its ID, its Viking label, or both. */
function displayLabel(data, mode) {
  const id = `c${data.neuronId}`;
  const label = data.vikingLabel || '';
  if (mode === 'id') return id;
  if (mode === 'label') return label || id;
  return label ? `${id} ${label}` : id;
}

export class NetworkView {
  constructor(container) {
    this.container = container;
    this.cy = null;
    this.onSelect = null;
    this.onHover = null;
    this.elements = [];
    this.source = null;
    this.labels = 'both';
    // The canvas follows the page's theme: recolor in place when the system's changes.
    if (typeof matchMedia === 'function') {
      matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => this.restyle());
    }
  }

  /** Draw the graph again in the page's colors, keeping its layout. */
  restyle() {
    this.cy?.style(graphStyle(this.labels, themeColors(this.container)));
  }

  /** Keep the server's answer for one volume; drawing is a separate step. */
  setElements(elements, source) {
    this.elements = elements;
    this.source = source;
  }

  /** The link types present, with synapse totals, largest first. */
  types() {
    const out = new Map();
    for (const el of this.elements) {
      if (!el.data.source) continue;
      const directed = new Set(el.data.directedTypes || []);
      for (const [type, n] of Object.entries(el.data.typeCounts || {})) {
        const row = out.get(type) || { type, synapses: 0, edges: 0, directed: directed.has(type), color: edgeColor(type) };
        row.synapses += n;
        row.edges += 1;
        out.set(type, row);
      }
    }
    return [...out.values()].sort((a, b) => b.synapses - a.synapses || a.type.localeCompare(b.type));
  }

  /**
   * Draw the kept answer. `enabledTypes` (a Set, or null for all) drops link
   * types; an edge's weight, color and arrow are recomputed from what is
   * left, and a partner left without edges disappears. Loaded cells stay.
   */
  render({ layout = 'cose', minWeight = 1, labels = 'both', enabledTypes = null } = {}) {
    const edges = [];
    for (const el of this.elements) {
      if (!el.data.source) continue;
      const counts = Object.entries(el.data.typeCounts || {})
        .filter(([type]) => !enabledTypes || enabledTypes.has(type))
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      const weight = counts.reduce((sum, [, n]) => sum + n, 0);
      if (!weight || weight < minWeight) continue;
      const directedTypes = new Set(el.data.directedTypes || []);
      edges.push({
        data: {
          ...el.data,
          weight,
          types: counts.map(([type]) => type),
          primaryType: counts[0][0],
          directed: counts.some(([type]) => directedTypes.has(type)),
          summary: counts.map(([type, n]) => `${n} ${type}`).join(', '),
        },
      });
    }
    const connected = new Set(edges.flatMap((e) => [e.data.source, e.data.target]));
    const nodes = this.elements
      .filter((el) => !el.data.source && (el.data.loaded || connected.has(el.data.id)))
      .map((el) => ({ data: { ...el.data, label: displayLabel(el.data, labels) } }));
    const nodeIds = new Set(nodes.map((n) => n.data.id));
    const kept = edges.filter((e) => nodeIds.has(e.data.source) && nodeIds.has(e.data.target));

    this.labels = labels;
    this.cy?.destroy();
    this.cy = cytoscape({
      container: this.container,
      elements: [...nodes, ...kept],
      style: graphStyle(labels, themeColors(this.container)),
      layout: layoutOptions(layout),
      wheelSensitivity: 0.2,
    });

    this.cy.on('tap', 'node', (event) => {
      this.onSelect?.({ ...event.target.data(), source: this.source });
    });
    this.cy.on('mouseover', 'edge', (event) => {
      const data = event.target.data();
      const arrow = data.directed ? '→' : '—';
      this.onHover?.(`c${data.source} ${arrow} c${data.target}: ${data.summary}`);
    });
    this.cy.on('mouseover', 'node', (event) => {
      const data = event.target.data();
      const who = data.vikingLabel ? `c${data.neuronId} ${data.vikingLabel}` : `c${data.neuronId}`;
      this.onHover?.(`${who} · ${data.degree} connection${data.degree === 1 ? '' : 's'}${data.loaded ? ' · loaded' : ''}`);
    });
    this.cy.on('mouseout', 'node, edge', () => this.onHover?.(''));
    return {
      nodes: this.cy.nodes().length,
      edges: this.cy.edges().length,
      totalEdges: this.elements.filter((el) => el.data.source).length,
    };
  }

  relayout(name) {
    this.cy?.layout(layoutOptions(name)).run();
  }

  fit() { this.cy?.fit(undefined, 30); }

  destroy() { this.cy?.destroy(); this.cy = null; }
}

function layoutOptions(name) {
  const base = { name, animate: false, padding: 30 };
  if (name === 'cose') {
    return { ...base, idealEdgeLength: 140, nodeRepulsion: 30000, nestingFactor: 1.1 };
  }
  if (name === 'concentric') {
    return { ...base, concentric: (n) => n.degree(), levelWidth: () => 2, minNodeSpacing: 24 };
  }
  if (name === 'breadthfirst') return { ...base, directed: true, spacingFactor: 1.2 };
  return base;
}

export { SYNAPSE_COLORS, edgeColor };
