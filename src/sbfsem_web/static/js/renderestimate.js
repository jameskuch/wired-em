// What a Closed curve or Hybrid build will cost, as the server estimates it
// before building (GET /api/neurons/{source}/{id}/render-estimate,
// sbfsem/service/estimate.py), in the words of the cost chip, its hover help
// and the notice that says a Voxel size was made coarser.
//
// The first half is pure (no DOM, no fetch) and loads under node; the second is
// a small cache that asks the server, at most once per cell and settings, and
// never while a size is still being typed.

import { normalizeRender, effectiveSignature, MESH_OPTION_KEYS, pick } from './renderoptions.js';

// -- thresholds ----------------------------------------------------------------
// Amber past a long wait: a minute is past where a render feels like a click
// (production builds RPC1 c2628 at 0.05 um in about 38 s, at 0.035 in about 64 s,
// at 0.025 in 109 s), and a minute of nothing in particular is what "drastically
// long" means for a view that is otherwise interactive.
export const LONG_WAIT_S = 60;
// Amber past half the memory the server gives builds: such a build cannot run
// beside another one like it, so it waits for the others and runs alone.
export const SERVER_SHARE = 0.5;
// Amber past this much of the tab's heap (c2628 at 0.025 um: 620 MB, drawn in
// a second; more is a slow parse and a heavy tab, and several such cells add up).
export const TAB_HEAP_MB = 512;

/** What each limit is called in a sentence. */
const LIMIT_WORDS = {
  voxels: 'raster voxels', faces: 'voxel faces', memory: 'server memory', page: 'page text',
};

// -- numbers in words ---------------------------------------------------------------

/** "940 k", "2.1 M", "13 M". */
export function countText(n) {
  const v = Number(n) || 0;
  if (v >= 1e7) return `${Math.round(v / 1e6)} M`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)} M`;
  if (v >= 1e4) return `${Math.round(v / 1e3)} k`;
  return String(Math.round(v));
}

/** "640 MB", "1.4 GB", "12 GB". */
export function mbText(mb) {
  const v = Number(mb) || 0;
  if (v >= 10_000) return `${Math.round(v / 1000)} GB`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)} GB`;
  return `${Math.round(v)} MB`;
}

/** "under 1 s", "25 s", "1 min 50 s". */
export function secondsText(s) {
  const v = Number(s) || 0;
  if (v < 1) return 'under 1 s';
  if (v < 60) return `${Math.round(v)} s`;
  const m = Math.floor(v / 60);
  const r = Math.round(v - m * 60);
  return r ? `${m} min ${r} s` : `${m} min`;
}

// -- one estimate -------------------------------------------------------------------

/** Whether the estimate speaks for the build (a curve method on a cell with something to draw). */
export const usable = (e) => !!(e && e.applies && !e.empty && !e.pending);

/** An answer the server means to improve on: pending (no count of the cell yet) or approximate
 * (scaled further than it should be while the cell's reference raster waits). */
export const interim = (e) => !!(e && (e.pending || e.approximate));

/** The reasons an estimate is amber, in words (empty when it is not). */
export function warnings(e) {
  if (!usable(e)) return [];
  const out = [];
  if (e.seconds > LONG_WAIT_S) out.push(`about ${secondsText(e.seconds)} on the server`);
  const limit = e.limits?.memory_mb;
  if (limit && e.server_mb > SERVER_SHARE * limit) {
    out.push(`${mbText(e.server_mb)} of the ${mbText(limit)} the server gives a build, so it builds alone`);
  }
  if (e.tab_mb > TAB_HEAP_MB) out.push(`about ${mbText(e.tab_mb)} more in this tab`);
  return out;
}

/** 'ok', 'warn' (amber) or 'over' (red: past a limit). */
export function estimateLevel(e) {
  if (!usable(e)) return 'ok';
  if (e.over?.length) return 'over';
  return warnings(e).length ? 'warn' : 'ok';
}

/** Each limit an estimate is past, as "≈ 13 M voxel faces against the limit of 10 M". */
export function overReasons(e) {
  if (!usable(e)) return [];
  const l = e.limits || {};
  const words = {
    voxels: `≈ ${countText(e.voxels)} raster voxels against the limit of ${countText(l.voxels)}`,
    faces: `≈ ${countText(e.faces)} voxel faces against the limit of ${countText(l.faces)}`,
    memory: `≈ ${mbText(e.server_mb)} of server memory against the ${mbText(l.memory_mb)} it gives a build`,
    page: `≈ ${mbText(e.json_mb)} of text for the page, more than a tab can read (${mbText(l.json_mb)})`,
  };
  return (e.over || []).map((k) => words[k] || k);
}

/**
 * Why a cell is built coarser, in one phrase: the limit that sets the size that
 * fits when the options are past it (the voxel faces, as a rule), else the
 * first one they are past, and how many more there are.
 */
export function mainReason(e) {
  const over = usable(e) ? (e.over || []) : [];
  if (!over.length) return '';
  const reasons = overReasons(e);
  const main = Math.max(0, over.indexOf(e.binding));
  const more = over.length - 1;
  return reasons[main] + (more ? `, and past ${more} more limit${more > 1 ? 's' : ''}` : '');
}

/**
 * The Voxel size a cell must be built at to fit, or null when `options` fit as
 * they are (or the estimate cannot say).  Only ever coarser than asked.
 */
export function fittedPixel(e, options) {
  if (!usable(e) || !e.over?.length) return null;
  const fits = Number(e.finest_um);
  const asked = Number(normalizeRender(options).pixel_size_um);
  return Number.isFinite(fits) && fits > asked + 1e-9 && fits <= 1 ? fits : null;
}

/**
 * The size to set on a cell once its estimate has come back, or null: only while
 * the draw that asked is still the cell's newest (`current`) and the cell still holds
 * the options the estimate was for (`now`, its render as it is now).  A size applied
 * while the estimate was out is the person's, and is not overwritten.
 */
export function fitStillWanted(estimate, asked, now, current = true) {
  if (!current || !now) return null;
  if (effectiveSignature(now) !== effectiveSignature(asked)) return null;
  return fittedPixel(estimate, asked);
}

/** One cell's line in the hover help. */
export function cellLine(name, e) {
  const l = e.limits || {};
  const voxels = e.voxels === null || e.voxels === undefined ? 'raster voxels not counted'
    : `${countText(e.voxels)} raster voxels (limit ${countText(l.voxels)})`;
  const parts = [
    `${name} at ${e.pixel_size_um} µm: ≈ ${countText(e.faces)} voxel faces (limit ${countText(l.faces)})`
      + ` from ${voxels}`,
    `≈ ${countText(e.triangles)} triangles`,
    `the build peaks at ≈ ${mbText(e.peak_mb)} and sending it adds ≈ ${mbText(e.serve_mb)}:`
      + ` ${mbText(e.server_mb)} of the ${mbText(l.memory_mb)} this server gives a build`,
    `≈ ${secondsText(e.seconds)} on the server`,
    `the page receives ≈ ${mbText(e.json_mb)} of text and its tab grows by ≈ ${mbText(e.tab_mb)}`,
  ];
  let line = `${parts.join('; ')}.`;
  if (e.over?.length) {
    line += ` Past the limit: ${overReasons(e).join('; ')}.`;
  }
  if (e.finest_um) {
    const why = e.binding ? ` (${LIMIT_WORDS[e.binding] || e.binding})` : '';
    line += ` Finest size that fits: ${e.finest_um} µm${why}.`;
  }
  return line;
}

/**
 * The chip for several cells' estimates (`[{ name, estimate }]`, every one
 * usable): `{ text, level, help }`.  Triangles and seconds add up (the builds
 * queue), memory is the largest one build's.
 */
export function estimateChip(entries, { cells = '' } = {}) {
  const list = entries.filter((x) => usable(x.estimate));
  if (!list.length) return null;
  const tri = list.reduce((s, x) => s + x.estimate.triangles, 0);
  const secs = list.reduce((s, x) => s + x.estimate.seconds, 0);
  const mb = Math.max(...list.map((x) => x.estimate.server_mb));
  const over = list.filter((x) => x.estimate.over?.length);
  const warn = list.filter((x) => estimateLevel(x.estimate) === 'warn');
  const level = over.length ? 'over' : (warn.length || secs > LONG_WAIT_S ? 'warn' : 'ok');
  let text;
  if (over.length === 1 && list.length === 1) {
    text = `✕ too fine · fits at ${over[0].estimate.finest_um ?? '–'} µm`;
  } else if (over.length) {
    text = `✕ ${over.length} of ${list.length} too fine`;
  } else {
    text = `${level === 'warn' ? '⚠' : '≈'} ${countText(tri)} tri · ${mbText(mb)} · ${secondsText(secs)}`;
  }
  const lines = list.map((x) => cellLine(x.name, x.estimate));
  const who = cells || (list.length === 1 ? list[0].name : `${list.length} cells`);
  let help = `Building ${who} at this Voxel size, as the server estimates it before building:\n\n`
    + lines.join('\n\n');
  const amber = list.flatMap((x) => warnings(x.estimate).map((w) => `${x.name}: ${w}`));
  if (secs > LONG_WAIT_S && list.length > 1) amber.unshift(`about ${secondsText(secs)} for all of them`);
  if (amber.length) help += `\n\nLong or heavy: ${amber.join('; ')}.`;
  if (over.length) {
    help += '\n\nApply builds each cell that is too fine at the finest size that fits it,'
      + ' and a notice says which cell got which size.';
  }
  const basis = list[0].estimate.basis;
  help += '\n\nFrom the outlines without building: the raster\'s voxels are counted exactly; its'
    + ' voxel faces are scaled from '
    + (basis?.kind === 'built' ? `the build at ${basis.pixel_um} µm` : `a raster at ${basis?.pixel_um} µm`)
    + ' (faces in the sections\' planes go as 1 / size, faces between sections as 1 / size², within'
    + ' 2 % on the cells measured); a face is 1.4 to 2 triangles, this cell\'s own where a contoured'
    + ' raster of it says. Seconds are the production'
    + ` server's. Amber past ${LONG_WAIT_S} s, past half the server's memory for builds, or past`
    + ` ${mbText(TAB_HEAP_MB)} in this tab; red past a limit.`;
  if (list.some((x) => x.estimate.approximate)) {
    help += '\n\nApproximate for now: scaled further than it should be while the server makes a'
      + ' raster of the cell at 0.1 µm (one at a time); asked again in a moment.';
  }
  return { text, level, help };
}

/** The notice for cells built coarser than asked: `{ headline, detail }`; `fits` is `[{ name, from, to, why }]`. */
export function coarsenedNotice(fits) {
  if (!fits.length) return null;
  const sizes = [...new Set(fits.map((f) => f.to))];
  const headline = fits.length === 1 || sizes.length === 1
    ? `Voxel size set to ${sizes.join(' / ')} µm`
    : `Voxel size set per cell (${fits.length} cells)`;
  const lines = fits.map((f) => `${f.name}: ${f.from} → ${f.to} µm${f.why ? ` (${f.why})` : ''}`);
  const detail = ` ${lines.join('; ')}. Each is the finest size that fits that cell; a finer one`
    + ' is past what the server or this page can hold.';
  return { headline, detail };
}

/**
 * The notice for a Fine voxel that was typed and came out coarser (Voxel remesh,
 * Convolution, Ultraliser; the disc half of a Hybrid): `{ headline, detail }`,
 * or null.  `mesh` is the server's answer, which says the voxel it used
 * (`voxel2_um`) and why (`voxel2_reason`); an automatic Fine voxel (0) is the
 * server's to size, and says nothing.
 */
export function fineVoxelNotice(name, mesh, options) {
  const asked = Number(normalizeRender(options).voxel2_um);
  const used = Number(mesh?.voxel2_um);
  if (!(asked > 0) || !mesh?.voxel2_coarsened || !(used > asked + 1e-9)) return null;
  const rounded = Math.round(used * 1000) / 1000;
  const why = {
    floor: `the whole-cell grid's finest typed voxel is ${rounded} µm`,
    faces: 'a finer grid would make more raw triangles than this Face cap allows (three for each'
      + ' triangle kept, and never more than 10 million)',
    work: 'a finer grid would take more field evaluations than one build may (60 million, or 20'
      + ' for each raw triangle of a budget over 3 million)',
  }[mesh.voxel2_reason] || 'it is past one of the whole-cell grid\'s budgets';
  return {
    headline: `Fine voxel built at ${rounded} µm`,
    detail: ` for ${name}, not the ${asked} µm typed: ${why}.`,
  };
}

/** What the server is asked for: the options a mesh is built from, nothing that cuts or colors it. */
export const estimateQuery = (options) => pick(normalizeRender(options), MESH_OPTION_KEYS);

/** One cell, one copy, one set of settings. */
export function estimateKey(record, options) {
  return `${record.key}|${record.summary?.copy_tag ?? ''}|${record.loadGen ?? ''}|${effectiveSignature(options)}`;
}

// -- asking the server ---------------------------------------------------------------

/**
 * The estimates this tab has, by cell and settings.  `fetcher(record, options,
 * { signal })` asks the server.  `peek` answers from what is held; `get` asks
 * (once: a second caller shares the first's request); `soon` asks after `delay`
 * ms of quiet, and a newer `soon` for the same purpose replaces it, so typing a
 * size asks once, for the size typed last.
 */
export class EstimateCache {
  constructor(fetcher, { delay = 350, keep = 400, retryMs = 30000, interimMs = 1500 } = {}) {
    this.fetcher = fetcher;
    this.delay = delay;
    this.keep = keep;
    this.retryMs = retryMs;
    // An approximate or pending answer (the server is making the cell's reference
    // raster, or another one) is held this long, then asked again.
    this.interimMs = interimMs;
    this.held = new Map();
    this.flying = new Map();
    this.timers = new Map();
  }

  peek(record, options) {
    return this.held.get(estimateKey(record, options));
  }

  /** The estimate, from what is held or asked once; null when the server could not say
   * (kept a while too, so a chip waiting on it does not ask again and again). */
  get(record, options) {
    const key = estimateKey(record, options);
    if (this.held.has(key)) return Promise.resolve(this.held.get(key));
    if (this.flying.has(key)) return this.flying.get(key);
    const keep = (answer, life = Infinity) => {
      this.held.set(key, answer);
      while (this.held.size > this.keep) this.held.delete(this.held.keys().next().value);
      if (Number.isFinite(life)) {
        setTimeout(() => { if (this.held.get(key) === answer) this.held.delete(key); }, life);
      }
      return answer;
    };
    const asked = Promise.resolve()
      .then(() => this.fetcher(record, options))
      .then((answer) => keep(answer, interim(answer) ? this.interimMs : Infinity),
        () => keep(null, this.retryMs))
      .finally(() => this.flying.delete(key));
    this.flying.set(key, asked);
    return asked;
  }

  /**
   * The estimate once the server has more than an interim answer for it: asked
   * again while it is pending (another cell's reference raster is being made),
   * at most `tries` times `interimMs` apart.  What a build is held to.
   */
  async settled(record, options, { tries = 20 } = {}) {
    let answer = await this.get(record, options);
    for (let i = 0; i < tries && answer?.pending; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, this.interimMs));
      this.held.delete(estimateKey(record, options));
      answer = await this.get(record, options);
    }
    return answer;
  }

  /**
   * Ask for every record after a quiet `delay`; `done(answers)` once all are in (an
   * answer is null where the server could not say).  `purpose` names who is waiting:
   * a request for other settings replaces the one waiting, while the same request
   * again keeps its place (a panel that redraws often must not put it off for ever).
   */
  soon(purpose, records, options, done) {
    // Room for every cell asked about at once: with more selected than are kept,
    // the first answers were dropped before the last came in, and the chip asked
    // again for ever.
    this.keep = Math.max(this.keep, 2 * records.length);
    const want = records.map((r) => estimateKey(r, options)).join(';');
    const waiting = this.timers.get(purpose);
    if (waiting && waiting.want === want) {
      waiting.done = done;
      return;
    }
    if (waiting) clearTimeout(waiting.timer);
    const entry = { want, done, timer: 0 };
    entry.timer = setTimeout(() => {
      if (this.timers.get(purpose) === entry) this.timers.delete(purpose);
      Promise.all(records.map((r) => this.get(r, options))).then((answers) => entry.done(answers));
    }, this.delay);
    this.timers.set(purpose, entry);
  }

  /** Forget a cell's answers (it was loaded again). */
  forget(record) {
    for (const key of [...this.held.keys()]) if (key.startsWith(`${record.key}|`)) this.held.delete(key);
  }
}
