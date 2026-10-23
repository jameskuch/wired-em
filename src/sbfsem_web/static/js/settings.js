// The Settings tab: debug tools kept out of the way of the work.
//
// Expert render mode (whether a failed render is rebuilt with settings that work,
// or left failed with its settings marked), how IPL depth is measured (along the
// layers' normals, or straight along Z as MATLAB did), three cards for this browser (which build the page is, clearing its cached
// files, resetting what it saved), the Correction log (annotations Viking's
// curve fit pulled toward a much wider linked one, in every loaded cell this
// account may see: /api/correction-log), and one for the server (its state,
// clearing its cached renders, replacing its build processes, restarting it),
// which only an admin is offered: the server decides, through /api/admin/whoami.
//
// The rules are pure and exported, so node can test them (tests/js/test_settings.mjs);
// SettingsPanel is the view, and touches the page only when it is shown.

import { MODE_LABELS } from './regstatus.js';
import { FIELD_BY_KEY } from './renderoptions.js';

/**
 * Everything this site keeps in the browser, by what it is for.  `keys` are
 * exact names, `prefixes` the start of a family of names; `keep` is shown and
 * never offered for a reset (the tab's id names its own saved session).
 * tests/unit/test_settings_storage.py fails when a key the scripts use is in
 * none of these, so a new one gets a name here the day it is written.
 */
export const STORAGE_GROUPS = [
  {
    id: 'session', label: 'Saved sessions',
    help: 'The cells, their renders and the view each tab brings back after a reload',
    keys: ['sbfsem.session.v1', 'sbfsem.restore.auto'], prefixes: ['sbfsem.session.v1.tab.'],
  },
  {
    id: 'expert-render', label: 'Expert render mode',
    help: 'Whether render settings are built exactly as set, even when a cell cannot be built with them',
    keys: ['sbfsem:expert-render'],
  },
  {
    id: 'ipl-method', label: 'IPL depth measured',
    help: 'Whether IPL depth is measured along the layers’ normals (the default) or straight along Z',
    keys: ['sbfsem:ipl-method'],
  },
  {
    id: 'render', label: 'Render defaults',
    help: 'The render settings you saved as defaults, for a new cell and for each technique',
    keys: ['sbfsem:startup-render', 'sbfsem:technique-defaults'],
  },
  {
    id: 'layout', label: 'Layout',
    help: "The cell panel's width and the full-screen sidebar",
    keys: ['sbfsem:sidebar-width', 'sbfsem:fullscreen-sidebar'],
  },
  { id: 'swatches', label: 'Saved colors', help: "The color picker's saved swatches", keys: ['sbfsem:swatches'] },
  {
    id: 'visualizations', label: 'Saved visualizations',
    help: 'The looks you saved from a cell (its Visualizations list); save them to a file first to keep them',
    keys: ['sbfsem.visualizations.v1'],
  },
  {
    id: 'correction-log', label: 'Correction log ratio',
    help: 'How many times wider a linked annotation must be for the Correction log to list a pull toward it',
    keys: ['sbfsem:correction-log-ratio'],
  },
  {
    id: 'graphapp', label: 'GraphApp controls',
    help: 'What GraphApp shows (rings, synapses, the flagged annotations), its marker size and Color segments',
    keys: ['sbfsem:graphapp'],
  },
  {
    id: 'strata', label: 'Stratification chart',
    help: 'The Stratification tab’s gridline step and what its PDF includes, as last chosen',
    keys: ['sbfsem:strata-ticks', 'sbfsem:strata-pdf'],
  },
  { id: 'connectivity', label: 'Connectivity query', help: 'The last query on the Connectivity tab', keys: ['sbfsem.connectivity.query'] },
  {
    id: 'report', label: 'Report options',
    help: 'Render compare’s report: the viewpoints, sign, paper and what to include, as last chosen',
    keys: ['sbfsem:compare-report'],
  },
  {
    id: 'registrations', label: 'Registrations choice',
    help: 'Render compare’s Registrations…: the registrations ticked and where they go, as last set up',
    keys: ['sbfsem:compare-registrations'],
  },
  { id: 'dropdowns', label: 'Dropdown lists', help: 'Whether dropdowns open as the page’s own lists', keys: ['sbfsem:select-lists'] },
  {
    id: 'viking', label: 'Viking programs',
    help: 'Which Viking programs Open in Viking has seen answer, and the one chosen in this tab',
    keys: ['sbfsem.vikingClients', 'sbfsem.vikingChoice'],
  },
  { id: 'tab', label: 'This tab’s id', help: 'Names the tab’s own saved session; kept', keys: ['sbfsem.tab'], keep: true },
  { id: 'note', label: 'Settings note', help: 'What this tab did just before its last reload; kept', keys: ['sbfsem.settings.note'], keep: true },
  {
    id: 'stale-reload', label: 'Old page reload',
    help: 'The server build this tab reloaded itself for, after the browser showed an older page from its cache, so it reloads once and not again; kept',
    keys: ['sbfsem:stale-reload'], keep: true,
  },
];

const OTHER = { id: 'other', label: 'Other', help: 'Saved by this site under names this list does not know' };

/** The group a key belongs to, or the catch-all. */
export function groupOf(key) {
  for (const group of STORAGE_GROUPS) {
    if ((group.keys || []).includes(key)) return group;
    if ((group.prefixes || []).some((p) => key.startsWith(p))) return group;
  }
  return OTHER;
}

/** Every key of a Storage-like object, which may be absent or throw (a private window). */
function entries(store, area) {
  const out = [];
  if (!store) return out;
  try {
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i);
      if (key == null) continue;
      const value = store.getItem(key) ?? '';
      // UTF-16, as the browser counts its quota.
      out.push({ area, key, bytes: 2 * (key.length + value.length) });
    }
  } catch { /* blocked storage: nothing to list */ }
  return out;
}

/**
 * What the browser holds for this site, grouped: `[{ id, label, help, keep,
 * items: [{ area, key, bytes }], bytes }]`, in the order of STORAGE_GROUPS with
 * the unknown names last.  Groups with nothing saved are left out.
 */
export function inventory({ local = null, session = null } = {}) {
  const all = [...entries(local, 'local'), ...entries(session, 'session')];
  const groups = new Map();
  for (const item of all) {
    const group = groupOf(item.key);
    if (!groups.has(group.id)) {
      groups.set(group.id, { id: group.id, label: group.label, help: group.help,
        keep: Boolean(group.keep), items: [], bytes: 0 });
    }
    const row = groups.get(group.id);
    row.items.push(item);
    row.bytes += item.bytes;
  }
  const order = [...STORAGE_GROUPS.map((g) => g.id), OTHER.id];
  return [...groups.values()].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
}

/** Remove the items of the chosen groups (never a kept one); the number removed. */
export function resetGroups(rows, chosen, { local = null, session = null } = {}) {
  const stores = { local, session };
  let removed = 0;
  for (const row of rows) {
    if (row.keep || !chosen.has(row.id)) continue;
    for (const item of row.items) {
      try {
        stores[item.area]?.removeItem(item.key);
        removed += 1;
      } catch { /* blocked: leave it */ }
    }
  }
  return removed;
}

/** The build a page was loaded as: the `?v=` its own module was fetched with. */
export function pageAssets(moduleUrl) {
  try {
    return new URL(moduleUrl).searchParams.get('v') || '';
  } catch {
    return '';
  }
}

/**
 * This page's build against the one the server serves (CLAUDE.md ground rule 7:
 * the first question when "the bug is still there").
 */
export function buildCheck(page, server) {
  if (!server) return { state: 'unknown', text: 'The server did not answer, so the builds cannot be compared.' };
  if (!page) return { state: 'unknown', text: `The server serves ${server.assets}; this page could not tell which build it is.` };
  if (page === server.assets) return { state: 'same', text: `This page is the build the server serves (${page}).` };
  return {
    state: 'differs',
    text: `This page was loaded as ${page}, and the server now serves ${server.assets}. `
      + 'Reload; if it stays the old build, clear cached files below.',
  };
}

/** Where this browser keeps which build a stale page reloaded itself for (sessionStorage). */
export const STALE_RELOAD_KEY = 'sbfsem:stale-reload';

/**
 * Whether the page was shown from the browser's own copy rather than asked of the
 * server: a tab the browser restored, or Back.  `nav` is the page's
 * PerformanceNavigationTiming.  A copy is used as it is, even one the server
 * marked to be checked first (`no-cache`), and nothing came over the network
 * for it: no bytes.
 */
export function fromBrowserCache(nav) {
  if (!nav) return false;
  return nav.transferSize === 0 || nav.type === 'back_forward';
}

/**
 * What a page does on starting when it may be a build the server no longer
 * serves (2026-10-22).  `page`: its own build (pageAssets); `server`: the
 * server's /api/build answer; `fromCache`: fromBrowserCache; `tried`: the
 * server build it already reloaded itself for, in this tab.
 *
 * A browser that shows a page from its own copy runs that copy's code, and the
 * redeploy watch only compares the server's answers with each other, so such a
 * page never offered a reload: the GraphApp tab of 2.28.0 was missing from a
 * restored tab the day after.  It reloads itself once ('reload'), and if that
 * still comes back old (a cache between it and the server) says so ('banner').
 * A page the server just sent is left alone (null): its build can differ from
 * /api/build's only on a dev server whose files changed after it started, or
 * while a deploy is installing, which the watch then sees as a restart.
 */
export function staleStart({ page, server, fromCache, tried = null }) {
  if (!page || !server?.assets || page === server.assets || !fromCache) return null;
  return tried === server.assets ? 'banner' : 'reload';
}

export function formatBytes(n) {
  if (n == null || !Number.isFinite(n)) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['kB', 'MB', 'GB', 'TB'];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${s % 60} s`;
  return `${s} s`;
}

/** How long the page waits for a restarted server before saying it has not come back. */
export const RESTART_WAIT_S = 120;

/**
 * Where a restart stands, from the instance it was asked of and the server's
 * latest answer to /api/build (null while it is down).
 */
export function restartPhase(beforeInstance, answer, elapsedS, limitS = RESTART_WAIT_S) {
  if (answer && answer.instance && answer.instance !== beforeInstance) return 'back';
  if (elapsedS >= limitS) return 'timeout';
  return answer ? 'stopping' : 'down';
}

/** The rows of the server card, from /api/admin/status. */
export function serverRows(status) {
  const mem = status.memory || {};
  const disk = status.disk_cache;
  const pool = status.pool || {};
  const builds = status.builds || {};
  const serviceMemory = status.service_memory_bytes ?? mem.process_rss_bytes;
  return [
    ['Version', `${status.version} · assets ${status.assets}`],
    ['Instance', `${status.instance} · up ${formatDuration(status.uptime_s)} · pid ${status.pid}`
      + (status.supervised ? ' · run by systemd' : ' · not run by systemd')],
    ['Memory', status.service_memory_bytes != null
      ? `${formatBytes(serviceMemory)} for the service and its build processes`
      : `${formatBytes(serviceMemory)} for this process`],
    ['Cells', `${mem.neurons_loaded ?? '—'} loaded · ${mem.copies_held ?? '—'} copies · `
      + `${(mem.annotations_held ?? 0).toLocaleString('en-US')} annotations`],
    ['Surfaces in memory', `${mem.meshes_cached ?? '—'} of ${mem.mesh_cache_limit ?? '—'} · ${formatBytes(mem.mesh_bytes)}`],
    ['Surfaces on disk', disk ? `${disk.files.toLocaleString('en-US')} files · ${formatBytes(disk.bytes)} `
      + `of ${formatBytes(disk.budget_bytes)}` : 'no disk cache'],
    ['Build processes', pool.build_processes
      ? `${pool.build_processes} · ${pool.pool_running ? 'running' : 'not started'} · `
        + `replaced ${pool.pool_recycles} time${pool.pool_recycles === 1 ? '' : 's'} for memory`
      : 'none: builds run in the server process'],
    ['Builds', `${builds.running ?? 0} running · ${builds.queued ?? 0} queued · `
      + `${builds.gate_waits ?? 0} waits for memory (longest ${builds.gate_wait_max_s ?? 0} s)`],
  ];
}

// -- expert render mode -----------------------------------------------------------

/** Where expert render mode is kept in this browser ('1' on; anything else, or nothing, off). */
export const EXPERT_RENDER_KEY = 'sbfsem:expert-render';

/** The checkbox's hover help: what each setting does with a render that fails. */
export const EXPERT_RENDER_HELP = 'On: render settings are applied verbatim, even when a cell cannot '
  + 'be rendered with them. The cell is left without a surface, and the Render panel marks the '
  + 'settings to change (black, red text) with ways to fix them, best first.\n\n'
  + 'Off (the default): a render that fails is built again with settings that work (the '
  + "technique's defaults, Voxel remesh, Hybrid, then Closed curve), the cell keeps the first that "
  + 'draws it until its annotations change, and a notice says what changed.';

// -- how IPL depth is measured ------------------------------------------------------

/** Where the IPL depth method is kept in this browser (nothing: the default). */
export const IPL_METHOD_KEY = 'sbfsem:ipl-method';

/** The methods, as the schema names them (`[value, label]`), and the viewer's default. */
export const IPL_METHOD_CHOICES = FIELD_BY_KEY.ipl_method.choices;
export const IPL_METHOD_DEFAULT = FIELD_BY_KEY.ipl_method.appDefault;

/** The control's hover help: what each method measures, and what it changes. */
export const IPL_METHOD_HELP = 'How a depth in the IPL is measured, for the Stratification tab and its '
  + 'files, Color by IPL depth and Clip IPL.\n\n'
  + 'Along the layers’ normals (the default): from the nearest point of the fitted INL surface and of '
  + 'the GCL surface, so the depth is a distance from the INL and the IPL’s thickness is measured '
  + 'square to the layers, wherever they tilt.\n\n'
  + 'Straight along Z (MATLAB’s column): between the two surfaces’ heights above and below the point, '
  + 'as SBFSEM-tools did before 2.31.0. A tilted IPL reads thicker this way by 1/cos(tilt); the '
  + 'depth as a percent of the IPL moves by about 1 % or less for 95 % of points.';

/** The IPL depth method kept in this browser, or the default (`store`: localStorage, or null). */
export function iplMethod(store) {
  let kept = null;
  try { kept = store?.getItem(IPL_METHOD_KEY) ?? null; } catch { kept = null; }
  return IPL_METHOD_CHOICES.some(([value]) => value === kept) ? kept : IPL_METHOD_DEFAULT;
}

/** Keep the IPL depth method (the default is kept as nothing); false when this browser will not keep it. */
export function setIplMethod(store, method) {
  try {
    if (method === IPL_METHOD_DEFAULT) store.removeItem(IPL_METHOD_KEY);
    else store.setItem(IPL_METHOD_KEY, method);
    return true;
  } catch {
    return false;
  }
}

/** Whether expert render mode is on in this browser (`store`: localStorage, or null: off). */
export function expertRender(store) {
  try { return store?.getItem(EXPERT_RENDER_KEY) === '1'; } catch { return false; }
}

/** Keep expert render mode on or off; false when this browser will not keep it. */
export function setExpertRender(store, on) {
  try {
    if (on) store.setItem(EXPERT_RENDER_KEY, '1');
    else store.removeItem(EXPERT_RENDER_KEY);
    return true;
  } catch {
    return false;
  }
}

// -- the correction log ----------------------------------------------------------

/** Where the Correction log's ratio is kept in this browser. */
export const RATIO_KEY = 'sbfsem:correction-log-ratio';
/** The server's limits, until it has said its own (/api/correction-log answers them). */
export const RATIO_LIMITS = Object.freeze({ min: 1, max: 50, fallback: 4 });

/**
 * The ratio typed in the box, as a number within the limits; `null` when the
 * text is not a number at all (the box then says so and the last good value holds).
 */
export function parseRatio(text, { min = RATIO_LIMITS.min, max = RATIO_LIMITS.max } = {}) {
  const value = Number(String(text ?? '').trim().replace(/[×x]$/i, ''));
  if (String(text ?? '').trim() === '' || !Number.isFinite(value)) return null;
  return Math.min(max, Math.max(min, value));
}

/** The ratio this browser keeps for the log (`store`: localStorage, or null), else the default. */
export function savedRatio(store) {
  let text = '';
  try { text = store?.getItem(RATIO_KEY) ?? ''; } catch { /* refused: the default */ }
  return parseRatio(text) ?? RATIO_LIMITS.fallback;
}

/** A ratio as the log writes it: 13.4×, 4×, ∞ for a zero-width annotation. */
export function formatRatio(ratio) {
  if (ratio == null || !Number.isFinite(ratio)) return '∞';
  return `${ratio >= 10 ? ratio.toFixed(0) : String(Number(ratio.toFixed(1)))}×`;
}

const um = (value, digits = 2) => (Number.isFinite(value) ? value.toFixed(digits) : '—');

/** One pull in words: a log row's hover, and an ID's in the cell panel. */
export function pullTitle(p) {
  const moved = p.moved_with?.length ? `; ${p.moved_with.join(', ')} moved with it` : '';
  return `Location ${p.location_id} (r ${um(p.r_um)} µm, section ${p.z}) moved ${um(p.move_um)} µm, `
    + `cos ${um(p.cos)} toward ${p.neighbor_id} (r ${um(p.neighbor_r_um)} µm, ${um(p.distance_um)} µm away): `
    + `${um(p.frac)} of the way there${moved}`;
}

/** When the server lists an outline, until it has said its own (/api/correction-log answers it). */
export const OUTLINE_RULE = Object.freeze({ min_move_um: 0.25, min_overlap: 0.8, min_off_share: 0.2 });

/** Why an outline moved off its linked outlines, as the log's Why column says it and its hover explains it. */
export const OUTLINE_CAUSES = Object.freeze({
  shape: { word: 'shape', help: 'It moved toward a linked outline whose centroid steps away from its own because '
    + 'that outline’s shape changes (it takes in a lobe or the base of a branch): slid by that step, the two would '
    + 'overlap less, not more. Viking’s curve fit takes the centroid as a point of the process.' },
  slide: { word: 'slide', help: 'It moved toward a linked outline that is this one displaced (slid by the step '
    + 'between their centroids they would overlap more), yet the move lowered its overlap with all its linked outlines.' },
  other: { word: 'other', help: 'No linked outline’s centroid lies the way it moved and as far: the fit carried it '
    + 'along with other annotations of the process (often one it pulled toward a much wider annotation).' },
});

const share = (value, digits = 2) => (Number.isFinite(value) ? value.toFixed(digits) : '—');
/** An overlap: three places, as a soma's outline loses little of it when it moves. */
const overlap = (value) => share(value, 3);

/** One outline moved off its linked outlines in words: a log row's hover, and an ID's in the cell panel. */
export function outlineTitle(m) {
  const why = m.cause === 'shape'
    ? `; toward ${m.neighbor_id} (section ${m.neighbor_z}), whose centroid sits ${um(m.step_um)} µm away though `
      + `the two overlap ${overlap(m.overlap_neighbor)} (${overlap(m.overlap_slid)} slid by that step): a change of shape, not of place`
    : m.cause === 'slide'
      ? `; toward ${m.neighbor_id} (section ${m.neighbor_z}, ${um(m.step_um)} µm away), which it would overlap `
        + `${overlap(m.overlap_slid)} slid by that step (${overlap(m.overlap_neighbor)} as drawn)`
      : `; toward no linked outline’s centroid (the nearest in direction, ${m.neighbor_id}, is ${um(m.step_um)} µm `
        + `away at cos ${um(m.cos)}): carried along with the process`;
  return `Location ${m.location_id} (r ${um(m.r_um)} µm, section ${m.z}) moved ${um(m.move_um)} µm off the `
    + `${m.n_linked === 1 ? 'outline' : `${m.n_linked} outlines`} linked to it: overlap ${overlap(m.overlap_traced)} where `
    + `Viking draws it, ${overlap(m.overlap_moved)} moved, ${share(m.off_share)} of the way off${why}`;
}

/**
 * The log as the card draws it: one entry per checked cell, in the server's
 * order, with its heading and its rows' texts, and the cells that were not
 * checked, apart.  `volumeName(source)` names a volume as the page does.
 */
export function correctionLogView(log, { volumeName = (s) => s } = {}) {
  const ratio = log?.ratio ?? RATIO_LIMITS.fallback;
  const checked = [];
  const unchecked = [];
  for (const cell of log?.cells || []) {
    // A copy loaded with Keep listed outlines: its rows are what Viking's correction did, as the
    // plain copy's are, and the outlines listed are drawn where Viking draws them.
    const mode = (MODE_LABELS[cell.mode] || cell.mode) + (cell.keep_outlines ? ' · kept' : '');
    const base = { source: cell.source, cellId: cell.neuron_id, copy: cell.copy_tag, mode,
      volume: volumeName(cell.source), service: cell.service_version || '' };
    if (!cell.checked) {
      unchecked.push({ ...base, note: cell.note || 'Not checked' });
      continue;
    }
    const n = cell.pulls.length;
    const near = cell.n_next_to_wider;
    checked.push({
      ...base,
      summary: n
        ? `${n} of the ${near} moved annotation${near === 1 ? '' : 's'} next to one ${formatRatio(ratio)} as wide or wider `
          + `${n === 1 ? 'was' : 'were'} pulled toward it`
        : `None pulled: ${near} moved annotation${near === 1 ? '' : 's'} next to one ${formatRatio(ratio)} as wide or wider, `
          + `none moved toward it by more than its diameter`,
      rows: cell.pulls.map((p) => ({
        locationId: p.location_id,
        z: String(p.z),
        r: um(p.r_um),
        move: um(p.move_um),
        neighborId: p.neighbor_id,
        neighborR: um(p.neighbor_r_um),
        ratio: formatRatio(p.ratio),
        frac: um(p.frac),
        distance: um(p.distance_um),
        movedWith: [...(p.moved_with || [])],
        title: pullTitle(p),
      })),
      outlines: outlinesOf(cell),
    });
  }
  return { ratio, checked, unchecked };
}

/** What a log row or a cell panel ID adds when the copy keeps the outline where Viking draws it. */
export const KEPT_NOTE = 'This copy keeps it where Viking draws it (Keep listed outlines).';

/** A checked cell's outlines moved off their linked outlines: its summary and rows. */
function outlinesOf(cell) {
  const moves = cell.outline_moves || [];
  const n = moves.length;
  const tested = cell.n_outlines_moved ?? 0;
  const linked = cell.n_outlines ?? 0;
  const kept = cell.n_kept ?? 0;
  const keeping = !cell.keep_outlines ? ''
    : kept ? `; this copy keeps ${kept === n ? (n === 1 ? 'it' : `all ${n}`) : kept} where Viking draws `
      + `${kept === 1 ? 'it' : 'them'}` : '';
  const summary = !linked ? ''
    : n ? `${n} of the ${tested} traced outline${tested === 1 ? '' : 's'} moved by the rule’s distance or more `
      + `${n === 1 ? 'was' : 'were'} moved off the outlines linked to ${n === 1 ? 'it' : 'them'} (${linked} linked in all)`
      + keeping
      : `None moved off: ${tested} of the ${linked} linked traced outline${linked === 1 ? '' : 's'} moved by the rule’s `
        + 'distance or more, none away from where it overlaps its linked ones';
  return {
    summary,
    rows: moves.map((m) => ({
      locationId: m.location_id,
      z: String(m.z),
      r: um(m.r_um),
      move: um(m.move_um),
      overlap: `${overlap(m.overlap_traced)} → ${overlap(m.overlap_moved)}`,
      off: share(m.off_share),
      neighborId: m.neighbor_id,
      step: um(m.step_um),
      cause: (OUTLINE_CAUSES[m.cause]?.word || m.cause) + (m.kept ? ', kept' : ''),
      causeHelp: (OUTLINE_CAUSES[m.cause]?.help || '') + (m.kept ? ` ${KEPT_NOTE}` : ''),
      kept: !!m.kept,
      title: outlineTitle(m) + (m.kept ? `. ${KEPT_NOTE}` : ''),
    })),
  };
}

/** The rule in words: the log's card and the cell panel's hover say it. */
export function outlineRuleText(rule = OUTLINE_RULE) {
  return `Listed when the correction moved a traced outline ${rule.min_move_um} µm or more, where Viking draws it `
    + `at least ${Math.round(rule.min_overlap * 100)} % of it (of the smaller of two) overlaps the outlines linked to it, `
    + `and the move took it at least ${rule.min_off_share} of the way off them: the overlap it lost, as a share of `
    + 'what the same move takes from a circle of its area lying on a copy of itself.';
}

/** What the pull line's hover says about the bug (the log's card says it at more length). */
export const PULLS_HELP = 'Viking’s section correction (Curve fit, or Field + fit) takes a soma’s or a large '
  + 'outline’s center as a point of the process, so a thin process leaving it is pulled toward that center: '
  + 'halfway when its neighbors are a section either side, a third, two thirds or three quarters of the way when '
  + 'they are not. These annotations moved toward a linked one at least this many times as wide, by more than '
  + 'their diameter and not past it. The ratio is the Settings tab’s Correction log’s, kept in this browser; it '
  + 'changes what is listed, not Viking’s correction. Click an ID to select it in the 3D view.';

/** What the outline line's hover says (the log's card says it at more length). */
export function outlinesHelp(rule = OUTLINE_RULE) {
  return 'Viking’s section correction (Curve fit, or Field + fit) takes a traced outline’s centroid as a point of '
    + 'the process, so where an outline takes in a lobe or the base of a branch its centroid steps sideways though '
    + 'its membrane does not, and the fit moves the next outline toward that step; others it carries along with the '
    + 'annotations around them. These outlines lined up with the outlines linked to them where Viking draws them, '
    + `and the correction moved them off. ${outlineRuleText(rule)} Click an ID to select it in the 3D view; `
    + 'the Settings tab’s Correction log lists them all, with their numbers.';
}

/** How many of a cell's outline IDs its panel shows; the Settings tab's log lists the rest. */
export const OUTLINE_IDS_SHOWN = 12;
/** How many of a cell's outline rows the log shows before "Show all". */
export const OUTLINE_ROWS_FOLDED = 10;

/**
 * A cell's own pulls and outlines moved off (`GET /neurons/{source}/{id}/correction-pulls`)
 * as its Position shift section shows them: `count` for the section's summary, `line`
 * before the pulls' IDs, one `{ locationId, title }` per pull, and `outlines`: its
 * `line`, the first {@link OUTLINE_IDS_SHOWN} IDs and how many `more`.  Null with
 * nothing to show: not checked (no curve fit), or neither found.
 */
export function cellPullsView(answer) {
  const n = answer?.checked ? (answer.pulls?.length ?? 0) : 0;
  const moves = answer?.checked ? (answer.outline_moves || []) : [];
  const m = moves.length;
  if (!n && !m) return null;
  const ratio = formatRatio(answer.ratio ?? RATIO_LIMITS.fallback);
  const shown = moves.slice(0, OUTLINE_IDS_SHOWN);
  return {
    count: [n ? `${n} pulled` : '', m ? `${m} off` : ''].filter(Boolean).join(', '),
    line: n ? `Curve fit pulled ${n} annotation${n === 1 ? '' : 's'} toward one ${ratio} as wide or wider:` : '',
    ids: (answer.pulls || []).map((p) => ({ locationId: p.location_id, title: pullTitle(p) })),
    outlines: m ? {
      line: `Curve fit moved ${m} traced outline${m === 1 ? '' : 's'} off the outlines linked to `
        + `${m === 1 ? 'it' : 'them'}`
        + (answer.keep_outlines && answer.n_kept
          ? `; ${answer.n_kept === m ? (m === 1 ? 'it is' : 'all are') : `${answer.n_kept} are`} kept where Viking draws `
            + `${answer.n_kept === 1 ? 'it' : 'them'}:`
          : ':'),
      ids: shown.map((o) => ({ locationId: o.location_id, title: outlineTitle(o) + (o.kept ? `. ${KEPT_NOTE}` : '') })),
      more: m - shown.length,
      help: outlinesHelp(answer.outline_rule || OUTLINE_RULE),
    } : null,
  };
}

// -- the view ----------------------------------------------------------------

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (name === 'text') node.textContent = value;
    else if (name === 'class') node.className = value;
    else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? '' : String(value));
  }
  for (const child of [].concat(children)) {
    if (child != null) node.append(child);
  }
  return node;
}

const NOTE_KEY = 'sbfsem.settings.note';

function browserStore(name) {
  try {
    const store = window[name];
    store.getItem(NOTE_KEY);
    return store;
  } catch {
    return null;
  }
}

export class SettingsPanel {
  /**
   * `api`: the page's api; `assets`: the build this page was loaded as;
   * `beforeReload()`: saves the session so the reload brings it back;
   * `stopSaving()`: no more session writes from this page (a reset that
   * reloads must not be written back by the page on its way out).
   */
  /**
   * `onExpertChange(on)`: expert render mode was switched (the render panel says it).
   * `annotations`: what the Correction log's rows do in the page:
   * `{ loaded(source, cellId), select(source, cellId, locationId), load(source, cellId),
   * viking(source), openInViking(source, cellId, locationId), volumeName(source) }`.
   * `onRatioChange(ratio)`: the log's ratio was changed (the cell panels list by it too).
   * `onIplMethodChange(method)`: the IPL depth method was switched (the page measures again).
   */
  constructor({
    root, api, assets, beforeReload = () => {}, stopSaving = () => {}, annotations = null, onRatioChange = null,
    onExpertChange = null, onIplMethodChange = null,
  }) {
    this.root = root;
    this.api = api;
    this.assets = assets;
    this.beforeReload = beforeReload;
    this.stopSaving = stopSaving;
    this.annotations = annotations;
    this.onRatioChange = onRatioChange;
    this.onExpertChange = onExpertChange;
    this.onIplMethodChange = onIplMethodChange;
    this.built = false;
  }

  /** Called when the tab is shown: every card is read again. */
  async show() {
    if (!this.built) this._build();
    await Promise.all([this._refreshBuild(), this._refreshStorage(), this._refreshLog(),
      this._refreshServer()]);
  }

  _build() {
    this.built = true;
    this.root.replaceChildren();
    const note = this._takeNote();
    if (note) this.root.append(el('div', { class: 'card settings-note', role: 'status', text: note }));

    this._buildRendering();

    this.buildText = el('p', { class: 'settings-text' });
    this.root.append(el('section', { class: 'card' }, [
      el('h3', { text: 'This page' }),
      this.buildText,
      el('div', { class: 'row settings-actions' }, [
        el('button', { type: 'button', text: 'Check again', onclick: () => this._refreshBuild() }),
        el('button', { type: 'button', text: 'Copy details', title: 'Copy this page’s build, the server’s and the browser, for a bug report',
          onclick: () => this._copyDetails() }),
      ]),
    ]));

    this.root.append(el('section', { class: 'card' }, [
      el('h3', { text: 'Cached files' }),
      el('p', { class: 'settings-text', text: 'Drops what this browser has cached of the site (the page, its scripts and styles) '
        + 'and reloads, so the page is the build the server serves. Your sessions, saved settings and sign-in stay.' }),
      el('div', { class: 'row settings-actions' }, [
        el('button', { type: 'button', text: 'Clear cached files and reload', onclick: () => this._clearBrowserCache() }),
      ]),
    ]));

    this.storageBody = el('tbody');
    this.storageTotal = el('p', { class: 'settings-text muted' });
    this.resetButton = el('button', { type: 'button', text: 'Reset the ticked items and reload', disabled: true,
      onclick: () => this._resetStorage() });
    this.root.append(el('section', { class: 'card' }, [
      el('h3', { text: 'Saved in this browser' }),
      el('table', { class: 'settings-table' }, [this.storageBody]),
      this.storageTotal,
      el('div', { class: 'row settings-actions' }, [this.resetButton]),
    ]));

    this._buildLog();

    this.serverCard = el('section', { class: 'card settings-server' });
    this.root.append(this.serverCard);
  }

  _buildRendering() {
    const store = browserStore('localStorage');
    this.expertBox = el('input', {
      id: 'expert-render', type: 'checkbox', checked: expertRender(store),
      onchange: () => this._expertChanged(),
    });
    this.expertStatus = el('p', { class: 'settings-text muted', role: 'status' });
    this.iplSelect = el('select', { id: 'ipl-method', onchange: () => this._iplMethodChanged() },
      IPL_METHOD_CHOICES.map(([value, label]) => el('option', {
        value, text: value === IPL_METHOD_DEFAULT ? `${label} (default)` : label,
      })));
    this.iplSelect.value = iplMethod(store);
    this.iplStatus = el('p', { class: 'settings-text muted', role: 'status' });
    this.root.append(el('section', { class: 'card' }, [
      el('h3', { text: 'Rendering' }),
      el('label', { class: 'settings-check', 'data-help': EXPERT_RENDER_HELP }, [
        this.expertBox, 'Enable expert render mode',
      ]),
      this.expertStatus,
      el('label', { class: 'settings-field', 'data-help': IPL_METHOD_HELP }, [
        'IPL depth measured ', this.iplSelect,
      ]),
      this.iplStatus,
    ]));
  }

  _iplMethodChanged() {
    const method = this.iplSelect.value;
    const kept = setIplMethod(browserStore('localStorage'), method);
    this.iplStatus.textContent = kept ? ''
      : 'This browser will not keep site data (a private window, or storage turned off): '
        + 'the choice holds until the page is reloaded.';
    this.onIplMethodChange?.(method);
  }

  /** The IPL depth method chosen: what this page uses even when the browser would not keep it. */
  get iplMethod() {
    return this.iplSelect ? this.iplSelect.value : iplMethod(browserStore('localStorage'));
  }

  _expertChanged() {
    const on = this.expertBox.checked;
    const kept = setExpertRender(browserStore('localStorage'), on);
    this.expertStatus.textContent = kept ? ''
      : 'This browser will not keep site data (a private window, or storage turned off): '
        + 'the choice holds until the page is reloaded.';
    this.onExpertChange?.(on);
  }

  /** Whether the checkbox is ticked: what this page uses even when the browser would not keep it. */
  get expert() {
    return this.expertBox ? this.expertBox.checked : expertRender(browserStore('localStorage'));
  }

  _buildLog() {
    this.ratio = savedRatio(browserStore('localStorage'));
    this.ratioInput = el('input', {
      id: 'correction-log-ratio', type: 'number', inputmode: 'decimal', step: '0.5',
      min: RATIO_LIMITS.min, max: RATIO_LIMITS.max, value: this.ratio, class: 'settings-ratio',
      'aria-describedby': 'correction-log-ratio-help',
      onchange: () => this._ratioChanged(),
      onkeydown: (event) => { if (event.key === 'Enter') this._ratioChanged(); },
    });
    this.logStatus = el('p', { class: 'settings-text muted', role: 'status' });
    this.logBody = el('div', { class: 'settings-log' });
    this.root.append(el('section', { class: 'card settings-wide', id: 'correction-log' }, [
      el('h3', { text: 'Correction log' }),
      el('p', { class: 'settings-text', text: 'Annotations Viking’s section correction (Curve fit, or Field + fit) '
        + 'moved toward a much wider annotation linked to them, in the cells loaded on this server that you may see. '
        + 'Viking’s curve fit takes a soma’s or a large outline’s center as a point of the process, so a thin process '
        + 'leaving it is pulled toward that center: halfway when its neighbors are a section either side, '
        + 'a third, two thirds or three quarters of the way when they are not. An annotation that moved past '
        + 'the wider one (more than 1 of the way) is not this bug and is not listed. '
        + 'A second table lists the traced outlines the correction moved off the outlines linked to them: '
        + 'the fit takes an outline’s centroid as a point of the process, so where an outline takes in a lobe or '
        + 'the base of a branch, the next one is moved toward that lobe though the membranes line up. '
        + 'A row selects its annotation in the 3D view; ↗ shows it in Viking.' }),
      el('div', { class: 'row settings-actions' }, [
        el('label', { for: 'correction-log-ratio', text: 'Wider by at least' }),
        this.ratioInput,
        el('span', { class: 'settings-unit', text: '×' }),
        el('button', { type: 'button', text: 'Refresh', onclick: () => this._refreshLog() }),
      ]),
      el('p', { class: 'settings-text muted', id: 'correction-log-ratio-help', text: 'This ratio sets only what this '
        + 'log lists. It does not change Viking’s correction: the corrections service does not take one yet. '
        + `Default ${RATIO_LIMITS.fallback}×; kept in this browser.` }),
      this.logStatus,
      this.logBody,
    ]));
  }

  /** The outlines table: one block of rows per cell, a long one folded to {@link OUTLINE_ROWS_FOLDED} rows. */
  _outlineTable(checked, rule) {
    const cells = checked.filter((c) => c.outlines.summary);
    if (!cells.length) return null;
    const head = el('tr', {}, ['Volume', 'Cell', 'Mode', 'Service', 'Location', 'Z', 'r µm', 'Moved µm',
      'Overlap', 'Off', 'Toward', 'Step µm', 'Why', ''].map((t) => el('th', { text: t })));
    head.children[8].title = 'Their shared area over the smaller outline’s, summed over the linked outlines: '
      + 'where Viking draws it → moved by the correction';
    head.children[9].title = 'How far off them the move took it: the overlap it lost, as a share of what the same '
      + 'move takes from a circle of its area lying on a copy of itself (1: straight off)';
    head.children[11].title = 'From its centroid to that of the linked outline the move points at most';
    const body = el('tbody');
    for (const cell of cells) {
      const where = () => [el('td', { text: cell.volume }), el('td', { text: `c${cell.cellId}` }),
        el('td', { text: cell.mode, title: `Copy ${cell.copy}` }),
        el('td', { class: 'num', text: cell.service || '—', title: 'The corrections service’s version' })];
      const rows = cell.outlines.rows;
      if (!rows.length) {
        body.append(el('tr', { class: 'settings-log-none' }, [...where(),
          el('td', { colspan: 10, class: 'muted', text: cell.outlines.summary })]));
        continue;
      }
      const folded = [];
      rows.forEach((row, i) => {
        const tr = el('tr', { title: row.title, 'data-location': row.locationId, 'data-outline': '' },
          [...(i === 0 ? where() : where().map(() => el('td'))),
            el('td', {}, [this._idButton(cell, row.locationId)]),
            el('td', { class: 'num', text: row.z }),
            el('td', { class: 'num', text: row.r }),
            el('td', { class: 'num', text: row.move }),
            el('td', { class: 'num', text: row.overlap }),
            el('td', { class: 'num', text: row.off }),
            el('td', {}, [this._idButton(cell, row.neighborId)]),
            el('td', { class: 'num', text: row.step }),
            el('td', { class: 'help', text: row.cause, title: row.causeHelp }),
            el('td', {}, [this._vikingButton(cell, row.locationId)])]);
        if (i >= OUTLINE_ROWS_FOLDED) {
          tr.hidden = true;
          folded.push(tr);
        }
        body.append(tr);
      });
      if (folded.length) {
        const more = el('button', { type: 'button', class: 'settings-more',
          text: `Show all ${rows.length} (${folded.length} more)` });
        const fold = el('tr', { class: 'settings-log-more' }, [el('td', { colspan: 14 }, [more])]);
        more.addEventListener('click', () => {
          const open = folded[0].hidden;
          for (const tr of folded) tr.hidden = !open;
          more.textContent = open ? `Show the first ${OUTLINE_ROWS_FOLDED}` : `Show all ${rows.length} (${folded.length} more)`;
        });
        body.append(fold);
      }
      body.append(el('tr', { class: 'settings-log-sum' }, [el('td', { colspan: 14, class: 'muted', text: cell.outlines.summary })]));
    }
    return [
      el('h4', { class: 'settings-subhead', text: 'Traced outlines moved off the outlines linked to them' }),
      el('p', { class: 'settings-text muted', text: outlineRuleText(rule) }),
      el('div', { class: 'settings-scroll' }, [
        el('table', { class: 'settings-table settings-log-table settings-outline-table' }, [el('thead', {}, [head]), body])]),
    ];
  }

  _ratioChanged() {
    const limits = this.limits || RATIO_LIMITS;
    const value = parseRatio(this.ratioInput.value, limits);
    if (value == null) {
      // A number box empties itself of text it cannot read, so there is nothing to quote.
      this.logStatus.textContent = `Not a number: type a ratio from ${limits.min}× to ${limits.max}×; `
        + `the log stays at ${formatRatio(this.ratio)}.`;
      this.ratioInput.value = this.ratio;
      return;
    }
    this.ratio = value;
    this.ratioInput.value = value;
    try { browserStore('localStorage')?.setItem(RATIO_KEY, String(value)); } catch { /* not kept: fine */ }
    this.onRatioChange?.(value);
    this._refreshLog();
  }

  async _refreshLog() {
    if (!this.logBody) return;
    let log;
    try {
      log = await this.api.correctionLog(this.ratio);
    } catch (error) {
      this.logStatus.textContent = `No answer: ${error.message}`;
      return;
    }
    this.limits = { min: log.min_ratio, max: log.max_ratio };
    const page = this.annotations || {};
    const view = correctionLogView(log, { volumeName: page.volumeName || ((s) => s) });
    const pulled = view.checked.reduce((sum, c) => sum + c.rows.length, 0);
    const off = view.checked.reduce((sum, c) => sum + c.outlines.rows.length, 0);
    this.logStatus.textContent = view.checked.length
      ? `${pulled} pull${pulled === 1 ? '' : 's'} toward an annotation ${formatRatio(view.ratio)} as wide or wider, `
        + `and ${off} traced outline${off === 1 ? '' : 's'} moved off the outlines linked to them, `
        + `in ${view.checked.length} loaded cell${view.checked.length === 1 ? '' : 's'} with a curve fit.`
      : 'No cell loaded on this server has a curve-fit correction (Curve fit, or Field + fit).';
    const blocks = [];
    if (view.checked.length) {
      const head = el('tr', {}, ['Volume', 'Cell', 'Mode', 'Service', 'Location', 'Z', 'r µm', 'Moved µm',
        'Toward', 'Its r µm', 'Ratio', 'Of the way', 'Moved with', ''].map((t) => el('th', { text: t })));
      const body = el('tbody');
      for (const cell of view.checked) {
        const where = [el('td', { text: cell.volume }), el('td', { text: `c${cell.cellId}` }),
          el('td', { text: cell.mode, title: `Copy ${cell.copy}` }),
          el('td', { class: 'num', text: cell.service || '—', title: 'The corrections service’s version' })];
        if (!cell.rows.length) {
          body.append(el('tr', { class: 'settings-log-none' }, [...where,
            el('td', { colspan: 10, class: 'muted', text: cell.summary })]));
          continue;
        }
        cell.rows.forEach((row, i) => {
          body.append(el('tr', { title: row.title, 'data-location': row.locationId },
            [...(i === 0 ? where : where.map(() => el('td'))),
              el('td', {}, [this._idButton(cell, row.locationId)]),
              el('td', { class: 'num', text: row.z }),
              el('td', { class: 'num', text: row.r }),
              el('td', { class: 'num', text: row.move }),
              el('td', {}, [this._idButton(cell, row.neighborId)]),
              el('td', { class: 'num', text: row.neighborR }),
              el('td', { class: 'num', text: row.ratio }),
              el('td', { class: 'num', text: row.frac }),
              el('td', {}, row.movedWith.length ? row.movedWith.map((id) => this._idButton(cell, id)) : [el('span', { class: 'muted', text: '—' })]),
              el('td', {}, [this._vikingButton(cell, row.locationId)])]));
        });
        body.append(el('tr', { class: 'settings-log-sum' }, [el('td', { colspan: 14, class: 'muted', text: cell.summary })]));
      }
      blocks.push(el('div', { class: 'settings-scroll' }, [
        el('table', { class: 'settings-table settings-log-table' }, [el('thead', {}, [head]), body])]));
      const outlines = this._outlineTable(view.checked, log.outline_rule || OUTLINE_RULE);
      if (outlines) blocks.push(...outlines);
    }
    if (view.unchecked.length) {
      blocks.push(el('p', { class: 'settings-text muted', text: 'Not checked: ' + view.unchecked.map((c) =>
        `c${c.cellId} (${c.volume}, ${c.mode}): ${c.note}`).join('; ') + '.' }));
    }
    this.logBody.replaceChildren(...blocks);
  }

  /** A Location ID that selects its annotation, or offers to load its cell when this tab has not. */
  _idButton(cell, locationId) {
    const page = this.annotations;
    const loaded = page?.loaded?.(cell.source, cell.cellId);
    const button = el('button', {
      type: 'button', class: 'settings-id', text: String(locationId),
      title: loaded
        ? `Select ${locationId}, an annotation of c${cell.cellId} in ${cell.volume}, in the 3D view`
        : `c${cell.cellId} (${cell.volume}) is not loaded in this tab: put it in the Structure ID box to load it`,
      onclick: async () => {
        if (!page) return;
        if (!page.loaded(cell.source, cell.cellId)) {
          page.load(cell.source, cell.cellId);
          return;
        }
        const ok = await page.select(cell.source, cell.cellId, locationId);
        if (!ok) this.logStatus.textContent = `c${cell.cellId} (${cell.volume}) has no annotation ${locationId} in this tab’s copy.`;
      },
    });
    button.dataset.cell = String(cell.cellId);
    button.dataset.source = cell.source;
    return button;
  }

  /** Show in Viking, for a volume Viking serves. */
  _vikingButton(cell, locationId) {
    const page = this.annotations;
    if (!page?.viking?.(cell.source)) return el('span');
    const button = el('button', {
      type: 'button', class: 'settings-viking', text: '↗',
      title: `Show in Viking: annotation ${locationId} of c${cell.cellId}, in ${cell.volume} `
        + '(the first open of a session may ask which Viking)',
      'aria-label': `Show annotation ${locationId} in Viking`,
      onclick: () => page.openInViking(cell.source, cell.cellId, locationId),
    });
    return button;
  }

  /** Whether the last reload was one this tab asked for, with a note to show. */
  hasNote() {
    try { return Boolean(browserStore('sessionStorage')?.getItem(NOTE_KEY)); } catch { return false; }
  }

  _note(text) {
    try { browserStore('sessionStorage')?.setItem(NOTE_KEY, `${text} (${new Date().toLocaleTimeString('en-US')})`); } catch { /* fine */ }
  }

  _takeNote() {
    const store = browserStore('sessionStorage');
    try {
      const note = store?.getItem(NOTE_KEY);
      store?.removeItem(NOTE_KEY);
      return note || '';
    } catch {
      return '';
    }
  }

  async _refreshBuild() {
    let server = null;
    try { server = await this.api.build(); } catch { /* down, or restarting */ }
    this.server = server;
    const check = buildCheck(this.assets, server);
    this.buildText.textContent = check.text;
    this.buildText.dataset.state = check.state;
  }

  async _copyDetails() {
    const lines = [
      `page build: ${this.assets || 'unknown'}`,
      `server build: ${this.server ? `${this.server.version} · ${this.server.assets} · instance ${this.server.instance}` : 'no answer'}`,
      `browser: ${navigator.userAgent}`,
      `window: ${window.innerWidth}×${window.innerHeight} @${window.devicePixelRatio}`,
      `time: ${new Date().toISOString()}`,
    ];
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      this.buildText.textContent = `${buildCheck(this.assets, this.server).text} Details copied.`;
    } catch {
      window.prompt('Copy these details:', lines.join(' | '));
    }
  }

  async _clearBrowserCache() {
    this.beforeReload();
    try {
      await this.api.clearBrowserCache();
      this._note('Cached files were cleared.');
    } catch (error) {
      this._note(`Cached files could not be cleared (${error.message}); the page was reloaded.`);
    }
    window.location.reload();
  }

  _stores() {
    return { local: browserStore('localStorage'), session: browserStore('sessionStorage') };
  }

  _refreshStorage() {
    this.rows = inventory(this._stores());
    this.chosen = new Set();
    this.storageBody.replaceChildren(...this.rows.map((row) => {
      const box = row.keep
        ? el('span', { class: 'muted', title: 'Kept: not offered for a reset', text: '—' })
        : el('input', { type: 'checkbox', 'aria-label': `Reset ${row.label}`,
          onchange: (e) => {
            if (e.target.checked) this.chosen.add(row.id); else this.chosen.delete(row.id);
            this.resetButton.disabled = this.chosen.size === 0;
          } });
      const where = [...new Set(row.items.map((i) => (i.area === 'local' ? 'all tabs' : 'this tab')))].join(', ');
      return el('tr', {}, [
        el('td', {}, [box]),
        el('td', {}, [el('div', { text: row.label }), el('div', { class: 'muted', text: row.help })]),
        el('td', { class: 'num', text: `${row.items.length} · ${formatBytes(row.bytes)}`,
          title: row.items.map((i) => i.key).join('\n') }),
        el('td', { class: 'muted settings-where', text: where }),
      ]);
    }));
    if (!this.rows.length) this.storageBody.append(el('tr', {}, [el('td', { colspan: 4, class: 'muted', text: 'Nothing is saved.' })]));
    const total = this.rows.reduce((sum, row) => sum + row.bytes, 0);
    this.storageTotal.textContent = `${formatBytes(total)} in all. A reset removes the ticked items from every tab of this site, then reloads.`;
    this.resetButton.disabled = true;
  }

  _resetStorage() {
    const names = this.rows.filter((r) => this.chosen.has(r.id)).map((r) => r.label);
    if (!names.length) return;
    if (!window.confirm(`Reset ${names.join(', ')}? This removes them from every tab of this site, and reloads.`)) return;
    // The page saves its session on the way out; a reset session must stay reset.
    if (this.chosen.has('session')) this.stopSaving();
    else this.beforeReload();
    // Read again now: the page saves its session moments after the tab opens
    // (and on every change), after the list on screen was made.
    const stores = this._stores();
    const removed = resetGroups(inventory(stores), this.chosen, stores);
    this._note(`Reset ${names.join(', ')}: ${removed} item${removed === 1 ? '' : 's'} removed.`);
    window.location.reload();
  }

  async _refreshServer() {
    const card = this.serverCard;
    let me;
    try {
      me = await this.api.adminWhoami();
    } catch (error) {
      card.replaceChildren(el('h3', { text: 'This server' }), el('p', { class: 'settings-text', text: `No answer: ${error.message}` }));
      return;
    }
    if (!me.admin) {
      card.replaceChildren(el('h3', { text: 'This server' }), el('p', { class: 'settings-text muted', text:
        `The server's tools (clear its cached renders, replace its build processes, restart it) need an admin account. `
        + `You are signed in as ${me.user || 'nobody'}; the server names its admins in ${me.admin_setting}.` }));
      return;
    }
    let status;
    try {
      status = await this.api.adminStatus();
    } catch (error) {
      card.replaceChildren(el('h3', { text: 'This server' }), el('p', { class: 'settings-text', text: `No answer: ${error.message}` }));
      return;
    }
    this.status = status;
    this.serverText = el('p', { class: 'settings-text', role: 'status' });
    const restartHelp = status.supervised
      ? 'Down for about 10 s for everyone; every open page is asked to reload and gets its session back. Builds running now are lost.'
      : 'This server was not started by systemd, so it would not come back: restart it where it was started.';
    card.replaceChildren(
      el('h3', { text: 'This server' }),
      el('table', { class: 'settings-table' }, [el('tbody', {}, serverRows(status).map(([name, value]) =>
        el('tr', {}, [el('th', { text: name }), el('td', { text: value })])))]),
      el('div', { class: 'row settings-actions' }, [
        el('button', { type: 'button', text: 'Refresh', onclick: () => this._refreshServer() }),
        el('button', { type: 'button', text: 'Clear surfaces in memory',
          title: 'Every surface held in memory is built again on its next request, from the disk cache where it is there',
          onclick: () => this._clearRenders(false) }),
        el('button', { type: 'button', class: 'danger', text: 'Clear surfaces in memory and on disk',
          title: 'Every surface is built from scratch on its next request, for everyone using this server',
          onclick: () => this._clearRenders(true) }),
        el('button', { type: 'button', text: 'Replace build processes', disabled: !status.pool?.build_processes,
          title: 'Builds running now finish in the old processes; the next one starts in new ones, which hold nothing',
          onclick: () => this._recycle() }),
        el('button', { type: 'button', class: 'danger', text: 'Restart the server', disabled: !status.supervised,
          title: restartHelp, onclick: () => this._restart() }),
      ]),
      el('p', { class: 'settings-text muted', text: restartHelp }),
      this.serverText,
    );
  }

  async _clearRenders(disk) {
    const files = this.status?.disk_cache?.files ?? 0;
    const ask = disk
      ? `Delete every cached surface, in memory and the ${files.toLocaleString('en-US')} files on disk? `
        + 'Every cell is built from scratch on its next request, for everyone using this server.'
      : 'Forget the surfaces held in memory? Each is read again from the disk cache, or built, on its next request.';
    if (!window.confirm(ask)) return;
    try {
      const done = await this.api.adminClearRenders(disk);
      await this._refreshServer();
      this.serverText.textContent = `Cleared ${done.memory_meshes} surface${done.memory_meshes === 1 ? '' : 's'} in memory`
        + (disk ? ` and ${done.disk_files.toLocaleString('en-US')} files (${formatBytes(done.disk_bytes)}) on disk.` : '.');
    } catch (error) {
      this.serverText.textContent = `Not cleared: ${error.message}`;
    }
  }

  async _recycle() {
    if (!window.confirm('Replace the build processes? Builds running now finish first in the old ones.')) return;
    try {
      const done = await this.api.adminRecycleWorkers();
      await this._refreshServer();
      this.serverText.textContent = done.recycled
        ? 'The build processes were replaced; the old ones exit when their builds are done.'
        : 'There were no build processes to replace (none has been started yet).';
    } catch (error) {
      this.serverText.textContent = `Not replaced: ${error.message}`;
    }
  }

  async _restart() {
    if (!window.confirm('Restart the server for everyone? It is down for about 10 s, every open page is asked '
      + 'to reload, and builds running now are lost.')) return;
    const before = this.status?.instance;
    try {
      await this.api.adminRestart();
    } catch (error) {
      this.serverText.textContent = `Not restarted: ${error.message}`;
      return;
    }
    this.beforeReload();
    const started = Date.now();
    for (;;) {
      const elapsed = (Date.now() - started) / 1000;
      let answer = null;
      try { answer = await this.api.build(); } catch { /* down */ }
      const phase = restartPhase(before, answer, elapsed);
      if (phase === 'back') {
        this._note(`The server restarted in ${Math.round(elapsed)} s.`);
        window.location.reload();
        return;
      }
      if (phase === 'timeout') {
        this.serverText.textContent = `The server has not come back after ${RESTART_WAIT_S} s: deploy/status.sh on the server says why.`;
        return;
      }
      this.serverText.textContent = phase === 'down'
        ? `Restarting: the server is down (${Math.round(elapsed)} s)…`
        : `Restarting: the server is finishing its requests (${Math.round(elapsed)} s)…`;
      await new Promise((resolve) => { setTimeout(resolve, 1000); });
    }
  }
}
