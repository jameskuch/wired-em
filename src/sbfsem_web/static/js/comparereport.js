// Render compare's report, without a page: which renderings a report can hold,
// the pairs it measures and the quick picks over them, what the request says,
// and about how long it takes and how many pages it makes.  Nothing here reads
// `document` or `window`, so tests/js/test_comparereport.mjs runs it under node;
// reportdialog.js is the dialog, and CompareView.reportSources (compare.js)
// gives it the panes.
//
// A rendering is one technique layer of a pane as it was BUILT: its snapshot
// (`builtOptions`), never what its panel says now (the snapshot rule of
// comparelayers.js).  A layer not built for the compared load of the cell is
// listed but cannot be chosen.  Renderings are named by their layer's key
// (`ref`), so a pick survives the list being drawn again; the request numbers
// them in pane order, which is how the PDF names them (R1, R2, ...).
//
// The server's library writes the PDF (sbfsem.export.compare_pdf) and plans its
// pages (Plan); `estimatePages` follows that plan, and the job's start says the
// exact count.

import { RENDER_FIELDS, normalizeRender, fieldState, matchPreset } from './renderoptions.js';

/** The viewpoints a report can draw from (render3d.js VIEWPOINTS, and the panes' camera). */
export const REPORT_VIEWS = Object.freeze([
  { key: 'view', label: 'This view',
    help: 'The panes’ camera as it is now (its direction; the report’s pictures are orthographic). '
      + 'Under Flip Z, mirrored as on screen.' },
  { key: 'xy1', label: 'XY1', help: 'From +Z: X right, Y up (the viewer’s XY1).' },
  { key: 'xz1', label: 'XZ1', help: 'From −Y: X right, Z up (the viewer’s XZ1, MATLAB’s XZ).' },
  { key: 'yz1', label: 'YZ1', help: 'From +X: Y right, Z up (the viewer’s YZ1, MATLAB’s YZ).' },
]);
export const DEFAULT_VIEWS = Object.freeze(['view', 'xy1', 'xz1']);

/** What one report holds at most: six panes of four layers, and every pair of them. */
export const MAX_RENDERINGS = 24;
/** Pairs the dialog starts with all of: up to this many, every pair; above, a few picks. */
export const ALL_PAIRS_UP_TO = 15;

/** The key of an unordered pair of renderings, by their refs. */
export const pairKey = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);
export const splitPair = (key) => String(key).split('|');

/** The quick picks over the pairs. */
export const PAIR_PICKS = Object.freeze([
  { id: 'all', label: 'All', help: 'Every pair of the renderings chosen above.' },
  { id: 'none', label: 'None', help: 'No pair: the report holds each rendering against the annotations only.' },
  { id: 'same', label: 'Same technique',
    help: 'The pairs whose two renderings are one technique with different settings: what each setting does.' },
  { id: 'across', label: 'Across techniques', help: 'The pairs whose two renderings are different techniques.' },
  { id: 'firsts', label: 'Panes',
    help: 'The first layer of every pane against the first of every other: the panes as they compare at a glance.' },
  { id: 'within', label: 'Within panes', help: 'The layers of each pane against each other.' },
  { id: 'screen', label: 'Differences on screen',
    help: 'The pairs the Difference panes show (each pane’s first layers).' },
]);

/** The technique a set of options is, as the pane pickers name it ("custom" for none). */
export const techniqueOf = (options) => matchPreset(options)?.id ?? 'custom';

/** Every pair of `refs`, in order. */
export function allPairs(refs) {
  const out = [];
  for (let i = 0; i < refs.length; i += 1) {
    for (let j = i + 1; j < refs.length; j += 1) out.push(pairKey(refs[i], refs[j]));
  }
  return out;
}

/**
 * The pairs a quick pick selects among the renderings in `chosen` (refs) of
 * `renderings` (CompareView.reportSources().renderings); `differences` are the
 * Difference panes' pairs as refs `[a, b]`.
 */
export function pickPairs(renderings, chosen, pick, differences = []) {
  const held = renderings.filter((r) => r.built && chosen.has(r.ref));
  const refs = held.map((r) => r.ref);
  const byRef = new Map(held.map((r) => [r.ref, r]));
  const every = allPairs(refs);
  const both = (key, test) => {
    const [a, b] = splitPair(key);
    return test(byRef.get(a), byRef.get(b));
  };
  switch (pick) {
    case 'all': return every;
    case 'none': return [];
    case 'same': return every.filter((k) => both(k, (a, b) => techniqueOf(a.options) === techniqueOf(b.options)));
    case 'across': return every.filter((k) => both(k, (a, b) => techniqueOf(a.options) !== techniqueOf(b.options)));
    case 'firsts': {
      const firsts = new Map();
      for (const r of held) if (!firsts.has(r.pane)) firsts.set(r.pane, r.ref);
      return allPairs([...firsts.values()]);
    }
    case 'within': return every.filter((k) => both(k, (a, b) => a.pane === b.pane));
    case 'screen':
      return differences
        .filter(([a, b]) => a !== b && chosen.has(a) && chosen.has(b))
        .map(([a, b]) => pairKey(a, b))
        .filter((k, i, all) => all.indexOf(k) === i);
    default: return [];
  }
}

/**
 * The pairs the dialog starts with: every pair while there are at most
 * ALL_PAIRS_UP_TO of them; beyond, the panes' first layers against each other,
 * each pane's layers against each other and the Differences on screen.
 */
export function defaultPairs(renderings, chosen, differences = []) {
  const every = pickPairs(renderings, chosen, 'all', differences);
  if (every.length <= ALL_PAIRS_UP_TO) return every;
  const picked = new Set([
    ...pickPairs(renderings, chosen, 'firsts', differences),
    ...pickPairs(renderings, chosen, 'within', differences),
    ...pickPairs(renderings, chosen, 'screen', differences),
  ]);
  return every.filter((k) => picked.has(k));
}

/** An option's value as the panel shows it, for a line of text. */
function valueText(field, value) {
  if (field.choices?.length) {
    const found = field.choices.find(([v]) => v === value);
    if (found) return String(found[1]);
  }
  if (field.key === 'voxel2_um' && !value) return 'auto';
  if (field.key === 'voxel2_faces' && value === 0) return 'no cap';
  if (value === null || value === undefined || value === '') return 'none';
  if (field.kind === 'bool') return value ? 'on' : 'off';
  if (field.kind === 'percent') return `${Math.round(Number(value) * 1000) / 10} %`;
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('en-US') : String(value);
  return String(value);
}

/**
 * What sets a rendering apart from the others of its technique in `renderings`:
 * the settings in which they differ, with its values ("Face cap 100,000"); for
 * the only one of its technique, the settings it changed from the technique's
 * defaults.  At most `limit` of them.
 */
export function settingsApart(rendering, renderings, { limit = 3 } = {}) {
  const mine = normalizeRender(rendering.options);
  const state = fieldState(mine);
  const tech = techniqueOf(mine);
  const live = RENDER_FIELDS.filter((f) => f.scope === 'geometry' && !state[f.key].hidden
    && !state[f.key].inert && !['method', 'pathway'].includes(f.key));
  const others = renderings.filter((r) => r.ref !== rendering.ref && techniqueOf(r.options) === tech)
    .map((r) => normalizeRender(r.options));
  const parts = [];
  if (others.length) {
    for (const f of live) {
      if (others.some((o) => o[f.key] !== mine[f.key])) parts.push(`${f.label} ${valueText(f, mine[f.key])}`);
    }
    if (!parts.length) parts.push('same settings as another layer');
  } else {
    const preset = matchPreset(mine);
    const base = normalizeRender({ method: mine.method, pathway: mine.pathway, ...(preset?.set ?? {}) });
    for (const f of live) {
      if (base[f.key] !== mine[f.key]) parts.push(`${f.label} ${valueText(f, mine[f.key])}`);
    }
  }
  if (!parts.length) return ['the technique’s defaults'];
  return parts.length > limit ? [...parts.slice(0, limit), `+${parts.length - limit} more`] : parts;
}

/**
 * How many pages the report will have: the server's plan (compare_pdf.Plan) for
 * `n` renderings, `pairs` pairs, `deviations` renderings held to the annotations
 * and `panes` panes, with or without the addendum.
 */
export function estimatePages({ n, pairs, deviations, panes, addendum = true }) {
  const summaryItems = 1 + (pairs ? 2 : 0) + (deviations ? 1 : 0) + 1;
  const summaryPages = Math.max(1, Math.ceil(n / 24)) + (pairs ? 1 + Math.ceil(pairs / 40) : 0)
    + (deviations ? 1 : 0) + 1;
  const bodyItems = panes + n + deviations + pairs;
  const bodyPages = panes + n + deviations + 3 * pairs;
  const addendumPages = addendum ? 9 : 0;
  const contents = Math.max(1, Math.ceil((summaryItems + bodyItems + addendumPages) / 44));
  return 1 + contents + summaryPages + bodyPages + addendumPages;
}

/**
 * About how long the server takes, in seconds: drawing a page (about half a
 * second), a picture (by its surface's triangles), an error map (by vertices)
 * and a pair both ways (by both surfaces' vertices; the exact sign about four
 * times the normal's).  Measured on Inferior c943 and c1178 (2026-10-20); a
 * measurement a pane already made is free, which this does not know.
 */
export function estimateSeconds({ renderings, deviations, pairs, views, sign = 'enclosure', pages }) {
  const byRef = new Map(renderings.map((r) => [r.ref, r]));
  const k = Math.max(1, views);
  const picture = (r) => 0.1 + 0.25 * ((r?.faces ?? 0) / 1e5);
  let s = 0.5 * pages + 4;
  for (const r of renderings) s += k * picture(r) + 0.3 + 0.4 * ((r.faces ?? 0) / 1e5);
  for (const ref of deviations) {
    const r = byRef.get(ref);
    s += 1.5 + 6 * ((r?.vertices ?? 0) / 1e5) + k * picture(r);
  }
  for (const key of pairs) {
    const [a, b] = splitPair(key).map((ref) => byRef.get(ref));
    const v = ((a?.vertices ?? 0) + (b?.vertices ?? 0)) / 1e5;
    s += (sign === 'enclosure' ? 0.6 + 1.2 * v : 0.25 + 0.3 * v) + 2 * k * (picture(a) + picture(b)) / 2
      + 2 * (picture(a) + picture(b));
  }
  return Math.round(s);
}

/** "about 40 s", "about 3 min". */
export function durationText(seconds) {
  if (seconds < 60) return `about ${Math.max(5, Math.round(seconds / 5) * 5)} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `about ${minutes} min` : `about ${Math.round(minutes / 6) / 10} h`;
}

/** The browser's date, time and zone for the cover: "2026-10-20 15:04 PDT". */
export function createdText(date = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  let zone = '';
  try {
    zone = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' }).formatToParts(date)
      .find((p) => p.type === 'timeZoneName')?.value ?? '';
  } catch { /* no Intl time zones: the time alone */ }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}${zone ? ` ${zone}` : ''}`;
}

/**
 * The request (POST .../compare-report-job) for the chosen renderings, pairs
 * and options.  `sources` is CompareView.reportSources(); `chosen` and
 * `deviations` are Sets of refs, `pairs` a Set of pairKeys, `snapshots` a Map
 * of pane number to a data URL.  Renderings are numbered in pane and layer
 * order; a pair or a Difference that names a rendering left out is dropped.
 */
export function reportBody(sources, {
  chosen, deviations, pairs, views = DEFAULT_VIEWS, sign = 'enclosure', addendum = true,
  page = 'letter', snapshots = new Map(), created = '',
}) {
  const held = sources.renderings.filter((r) => r.built && chosen.has(r.ref));
  const index = new Map(held.map((r, i) => [r.ref, i]));
  const pairList = [];
  for (const key of pairs) {
    const [a, b] = splitPair(key);
    if (index.has(a) && index.has(b) && a !== b) {
      const i = index.get(a);
      const j = index.get(b);
      pairList.push(i < j ? [i, j] : [j, i]);
    }
  }
  pairList.sort((p, q) => p[0] - q[0] || p[1] - q[1]);
  const camera = sources.camera ?? null;
  const viewList = REPORT_VIEWS.map((v) => v.key).filter((k) => views.includes(k) && (k !== 'view' || camera));
  return {
    renderings: held.map((r) => ({
      options: r.options, label: r.label, color: r.color, pane: r.pane, layer: r.layer,
      deviation: deviations.has(r.ref), stale: !!r.stale,
    })),
    pairs: pairList,
    panes: sources.panes.map((p) => {
      const difference = p.kind === 'difference' && p.a && p.b && index.has(p.a) && index.has(p.b)
        ? [index.get(p.a), index.get(p.b)] : null;
      const out = { number: p.number, kind: p.kind, layers: p.layers, difference };
      const shot = snapshots.get(p.number);
      if (shot) out.snapshot = shot;
      return out;
    }),
    views: viewList.length ? viewList : ['xy1'],
    camera: viewList.includes('view') ? camera : null,
    sign,
    addendum: !!addendum,
    page,
    created,
  };
}
