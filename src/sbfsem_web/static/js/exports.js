// What an export asks the server for, as pure functions (no DOM), so it can be
// tested under node: the options of the mesh on screen, how a cell is drawn, and
// how the cells of a volume are split into the archives the bulk SWC route will
// take.

import { EXPORT_KEYS, pick, normalizeRender } from './renderoptions.js';

/**
 * The query an export of a cell's mesh carries, so the file is the surface on
 * screen: every geometry option (EXPORT_KEYS: the closed-curve raster
 * included, without which a Closed curve or Hybrid cell shown at 0.25 um was
 * exported at the 0.05 um default), and the IPL clip when the render has one
 * (a cell clipped to isolate its ON or OFF arbors was exported whole).  Depth
 * coloring is a look, not geometry, and stays out.
 */
export function exportQuery(render) {
  const full = normalizeRender(render);
  const clipped = full.clip_ipl !== undefined && full.clip_ipl !== null;
  return {
    ...pick(full, EXPORT_KEYS),
    ...(clipped ? { clip_ipl: full.clip_ipl, keep_above: full.keep_above } : {}),
  };
}

/** The bulk SWC route takes at most this many structures (routes.BULK_EXPORT_LIMIT). */
export const BULK_SWC_LIMIT = 200;

/**
 * How a cell is drawn, as the export routes take it: `registration` (Viking's
 * current section transforms: true or false) and `corrections` (`off` or a
 * mode), each `undefined` when the cell never chose and the server's default
 * applies.  What the server reported applying wins over the cell's own
 * choice: the choice may be a legacy `true`, or a click still on its way.
 */
export function drawnWith(record) {
  const lr = record?.summary?.live_registration;
  const sc = record?.summary?.section_correction;
  let registration;
  if (lr && typeof lr.requested === 'boolean') registration = lr.requested;
  else if (typeof record?.registration === 'boolean') registration = record.registration;

  let corrections;
  if (sc && typeof sc.requested === 'boolean') {
    corrections = sc.requested ? (sc.mode || 'neighbor') : 'off';
  } else if (record?.corrections === true) corrections = 'neighbor';
  else if (record?.corrections === false) corrections = 'off';
  else if (typeof record?.corrections === 'string') corrections = record.corrections;
  return { registration, corrections };
}

/**
 * The archives to ask for: the cells split by how they are drawn (one archive
 * draws every cell the same way, so cells drawn differently cannot share one),
 * and each group into pieces of at most `limit`.  Groups and ids keep the
 * order the cells were given in.  Each is `{ ids, registration, corrections }`.
 */
export function planBulkSwc(records, limit = BULK_SWC_LIMIT) {
  const groups = new Map();
  for (const record of records) {
    const way = drawnWith(record);
    const key = `${way.registration}|${way.corrections}`;
    if (!groups.has(key)) groups.set(key, { ...way, ids: [] });
    groups.get(key).ids.push(record.id);
  }
  const plan = [];
  for (const group of groups.values()) {
    for (let i = 0; i < group.ids.length; i += limit) {
      plan.push({
        registration: group.registration, corrections: group.corrections,
        ids: group.ids.slice(i, i + limit),
      });
    }
  }
  return plan;
}

/** "3.2 MB", "840 kB": the size of a finished download. */
export function fileSize(bytes) {
  if (!Number.isFinite(bytes)) return '';
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

/** What an archive lacks, in words: "19 not attempted (the server's time ran out), 1 failed". */
function whatIsMissing({ failed = 0, notAttempted = 0 }) {
  const parts = [];
  if (notAttempted) parts.push(`${notAttempted} not attempted (the server's time ran out)`);
  if (failed) parts.push(`${failed} failed`);
  return parts.join(', ');
}

/**
 * The status line for a saved file.  A plain download reads "Exported c.zip
 * (3.2 MB)".  A bulk archive that holds fewer skeletons than were asked for
 * (the server's time budget ran out, or a cell could not be written) says so
 * instead, because it is saved as a 200 and a zip like a whole one and the
 * only other place that says is the manifest inside it.  `archive` is the
 * report `api.js` reads from the response's headers, if it sent one; the
 * caller shows `partial` in the error color.
 */
export function savedNotice(filename, bytes, archive = null) {
  const plain = `Exported ${filename} (${fileSize(bytes)})`;
  if (!archive || (!archive.notAttempted && !archive.failed)) return { text: plain, partial: false };
  return {
    text: `${plain} holds only ${archive.written} of ${archive.asked} skeletons: ${whatIsMissing(archive)}. `
      + 'manifest.csv in the archive names the rest; export those cells again.',
    partial: true,
  };
}

/**
 * The line after the last of several archives (cells drawn with different
 * Registration or correction choices cannot share one, and the route takes at
 * most 200 cells), or `null` when there was one archive and its own line
 * stands.  `reports` is each archive's `archiveReport` (`null` when the server
 * sent none, which counts as whole).  The sum is what the person needs: the
 * next archive's "Exporting..." must not bury a short one.
 */
export function bulkSwcSummary({ name, cells, archives, reports }) {
  if (archives < 2) return null;
  const known = reports.filter(Boolean);
  const sum = (key) => known.reduce((total, report) => total + (report[key] || 0), 0);
  const missing = { failed: sum('failed'), notAttempted: sum('notAttempted') };
  if (!missing.failed && !missing.notAttempted) {
    return {
      text: `Exported ${cells} skeletons of ${name} in ${archives} archives: `
        + 'cells drawn with different Registration or correction choices cannot share one',
      partial: false,
    };
  }
  return {
    text: `Exported ${sum('written')} of ${sum('asked')} skeletons of ${name} in ${archives} archives: `
      + `${whatIsMissing(missing)}. Each archive's manifest.csv names what it lacks; export those cells again.`,
    partial: true,
  };
}
