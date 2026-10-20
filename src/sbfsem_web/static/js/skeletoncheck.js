// The Skeleton check section of a cell's panel.
//
// Rebecca Pfeiffer exported each cell's SWC and mesh to Blender to find and
// hand-fix what misleads a path length.  This brings the finding here: the
// server lists the loops, unlinked pieces, jumps, hairpins and spikes (to fix
// in Viking) and the spokes and stubs (for the export to account for); a click
// flies to one, lights its annotations up and offers Viking at that Location
// ID.  Nothing is edited here: an annotation error is fixed at the source,
// once, and comes back with Update.
//
// The cell list is rebuilt from scratch on every change, so what the section
// knows lives in `checks`, not in its DOM.

import { dataKey } from './state.js';

const KIND_LABELS = {
  loop: 'Loops',
  fragment: 'Unlinked pieces',
  jump: 'Jumps',
  hairpin: 'Hairpins',
  spike: 'Spikes',
  spoke: 'Spokes',
  stub: 'Stubs',
  lamella: 'Lamellae',
};
/** One problem of a kind, as the viewer's label names it ("Lamellae" is not "Lamella" without its "s"). */
const KIND_SINGULAR = {
  loop: 'Loop',
  fragment: 'Unlinked piece',
  jump: 'Jump',
  hairpin: 'Hairpin',
  spike: 'Spike',
  spoke: 'Spoke',
  stub: 'Stub',
  lamella: 'Lamella',
};
export const problemLabel = (kind) => KIND_SINGULAR[kind] || KIND_LABELS[kind] || String(kind);

const KIND_HELP = {
  loop: 'Annotations linked in a ring. A skeleton file is a tree, so every ring loses one link on export and reads as two parallel branches.',
  fragment: 'Annotations of this cell that no link joins to the piece holding the soma. Viking has no link there; the export writes them as separate roots.',
  jump: 'A link whose two annotations stand far apart, edge to edge, compared with the rest of this cell’s links: usually one misplaced or mislinked annotation.',
  hairpin: 'A stretch where the path doubles back on itself: at least twice the cable for the progress made.',
  spike: 'One annotation far from the line of its neighbors along the path.',
  spoke: 'A thin process is linked to the center of the wide profile it leaves, so its first link runs inside that profile. Listed from 1 µm; all of them count toward the rim-corrected length.',
  stub: 'A tip branch no longer than the radius of the annotation it branches from: a bump on the surface rather than a branch.',
  lamella: 'A traced profile at least twice as long as it is wide, or a stack of them on consecutive sections. The skeleton passes through its center; a chain along its long axis would measure its cable differently. The length shown is the change, either way; the detail says which.',
};

/** key -> { stamp, result, current, open:Set<kind>, busy, error } */
const checks = new Map();

const fmt = (v, digits = 1) => Number(v).toLocaleString('en-US', {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
});

/**
 * What a result was found in: the annotations' stamp and the copy of the cell
 * (its Registration and correction choices).  A check is of the positions, so
 * it is not the same check after the section correction is switched on.
 */
function stampOf(record) {
  return dataKey(record);
}

function entryFor(record) {
  let entry = checks.get(record.key);
  if (!entry || entry.stamp !== stampOf(record)) {
    // Updated from Viking, or drawn at other positions, since: whatever was found
    // may have been fixed, or moved.
    entry = { stamp: stampOf(record), result: null, current: -1, open: new Set(), busy: false };
    checks.set(record.key, entry);
  }
  return entry;
}

/** The one-line summary beside the section's name. */
export function skeletonCheckSummary(record) {
  const entry = checks.get(record.key);
  if (!entry || entry.stamp !== stampOf(record) || !entry.result) return 'not run';
  const n = entry.result.problems.filter((p) => p.fix === 'viking').length;
  return n ? `${n} to look at in Viking` : 'nothing to fix in Viking';
}

export function forgetSkeletonCheck(key) {
  checks.delete(key);
}

/**
 * Fill the section body.  `deps`: { api, renderer, nodesFor(record),
 * openLocationInViking(record, id) | null, rerender(), fail(error),
 * volumeName(source) }.
 */
export function buildSkeletonCheck(body, record, deps) {
  const entry = entryFor(record);
  const wrap = document.createElement('div');
  wrap.className = 'skel-check';
  body.append(wrap);

  const run = document.createElement('button');
  run.type = 'button';
  run.textContent = entry.busy ? 'Checking…' : entry.result ? 'Check again' : 'Check the skeleton';
  run.disabled = entry.busy;
  run.title = 'Look through this cell’s annotations and links for what would mislead a path '
    + 'length. Nothing is changed, here or in Viking.';
  run.addEventListener('click', async () => {
    entry.busy = true;
    deps.rerender();
    try {
      entry.result = await deps.api.skeletonCheck(record.source, record.id);
      entry.current = -1;
      // The floor under the cable length takes seconds on a large cell, so it
      // follows the list rather than holding it up.
      entry.bracket = null;
      // A later run (Check again) reuses this entry: only the newest run's answer, or failure, is shown.
      const runId = (entry.run = (entry.run || 0) + 1);
      deps.api.morphometrics(record.source, record.id, { bracket: true }).then((m) => {
        if (checks.get(record.key) !== entry || entry.run !== runId) return;
        entry.bracket = { floor: m.length_floor_um, byRadius: m.length_floor_by_radius_um };
        deps.rerender();
      }).catch((error) => {
        // The list above stands without it; the line says why it is missing
        // instead of promising it for ever.
        if (checks.get(record.key) !== entry || entry.run !== runId) return;
        entry.bracket = { failed: error?.message || String(error) };
        deps.rerender();
      });
      entry.open = new Set(Object.keys(KIND_LABELS)
        .filter((kind) => entry.result.problems.some((p) => p.kind === kind && p.fix === 'viking')));
    } catch (error) {
      deps.fail(error);
    } finally {
      entry.busy = false;
      deps.rerender();
    }
  });
  wrap.append(run);
  if (!entry.result) {
    const note = document.createElement('p');
    note.className = 'note';
    note.textContent = 'Finds loops, unlinked pieces, jumps, hairpins and spikes to fix in Viking, '
      + 'and the spokes and stubs an export should account for.';
    wrap.append(note);
    return;
  }

  const { summary, problems } = entry.result;
  const facts = document.createElement('p');
  facts.className = 'note skel-facts';
  facts.textContent = `${summary.n_annotations.toLocaleString('en-US')} annotations · `
    + `${summary.n_components} piece${summary.n_components === 1 ? '' : 's'} · `
    + `${summary.n_loops} loop${summary.n_loops === 1 ? '' : 's'} · `
    + `${fmt(summary.total_length_um)} µm, ${fmt(summary.rim_corrected_length_um)} µm from the rims`;
  facts.title = `Total cable ${fmt(summary.total_length_um, 2)} µm over every link in Viking; `
    + `${fmt(summary.swc_length_um, 2)} µm in the tree an SWC keeps. `
    + `${summary.n_spokes} links start inside a profile at least ${summary.spoke_ratio}× as wide: `
    + `${fmt(summary.spoke_length_um, 2)} µm by the traced outlines `
    + `(${fmt(summary.spoke_length_by_radius_um, 2)} µm by the stored radii).`;
  wrap.append(facts);

  const bracket = document.createElement('p');
  bracket.className = 'note skel-facts';
  if (entry.bracket?.failed) {
    bracket.textContent = `could not find the shortest path through every annotation: ${entry.bracket.failed}`;
    bracket.classList.add('error');
  } else if (entry.bracket) {
    bracket.textContent = `no shorter than ${fmt(entry.bracket.floor)} µm through every annotation`;
    bracket.title = 'The cable length lies between two numbers. The path through the annotation '
      + `centers, ${fmt(summary.total_length_um, 2)} µm, over-measures it: every sideways wander of a `
      + 'center from one section to the next is added in. The shortest path that still passes '
      + `inside every annotation, ${fmt(entry.bracket.floor, 2)} µm, cannot exceed it. `
      + `(Held inside stored radii instead of traced outlines: ${fmt(entry.bracket.byRadius, 2)} µm, `
      + 'but a circle of a closed curve’s radius reaches outside an elongated outline.) '
      + 'Branch angles and tortuosity are not corrected by either number.';
  } else if (entry.bracket === null) {
    bracket.textContent = 'finding the shortest path through every annotation…';
  }
  if (bracket.textContent) wrap.append(bracket);

  if (!problems.length) {
    const none = document.createElement('p');
    none.className = 'note';
    none.textContent = 'Nothing found.';
    wrap.append(none);
    return;
  }

  // Step through everything in list order (what is fixed in Viking first).
  const stepper = document.createElement('div');
  stepper.className = 'skel-step';
  const step = (label, delta, title) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.title = title;
    b.addEventListener('click', () => {
      const n = problems.length;
      const next = entry.current < 0 ? (delta > 0 ? 0 : n - 1) : (entry.current + delta + n) % n;
      go(record, entry, next, deps);
    });
    return b;
  };
  const where = document.createElement('span');
  where.className = 'skel-where';
  where.textContent = entry.current >= 0 ? `${entry.current + 1} of ${problems.length}`
    : `${problems.length} found`;
  stepper.append(step('◀', -1, 'Previous'), where, step('▶', 1, 'Next'));
  wrap.append(stepper);

  for (const kind of Object.keys(KIND_LABELS)) {
    const rows = problems.map((p, index) => ({ p, index })).filter(({ p }) => p.kind === kind);
    if (!rows.length) continue;
    const open = entry.open.has(kind);
    const head = document.createElement('button');
    head.type = 'button';
    head.className = `skel-kind${open ? ' open' : ''}`;
    head.title = KIND_HELP[kind];
    const total = rows.reduce((sum, { p }) => sum + p.length_um, 0);
    head.textContent = `${open ? '▼︎' : '▶︎'} ${KIND_LABELS[kind]} — ${rows.length} · ${fmt(total)} µm · `
      + (rows[0].p.fix === 'viking' ? 'fix in Viking' : 'for the export');
    head.addEventListener('click', () => {
      if (open) entry.open.delete(kind); else entry.open.add(kind);
      deps.rerender();
    });
    wrap.append(head);
    if (!open) continue;

    for (const { p, index } of rows) {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = `skel-row${index === entry.current ? ' current' : ''}`;
      row.title = p.detail;
      row.textContent = `section ${p.z_section} · ${p.location_ids.length} annotation`
        + `${p.location_ids.length === 1 ? '' : 's'} · ${fmt(p.length_um, 2)} µm`;
      row.addEventListener('click', () => go(record, entry, index, deps));
      wrap.append(row);
      if (index !== entry.current) continue;

      const detail = document.createElement('div');
      detail.className = 'skel-detail';
      const text = document.createElement('p');
      text.textContent = `Location ${p.anchor_id} (${deps.volumeName(record.source)}): ${p.detail}.`;
      detail.append(text);
      if (deps.openLocationInViking) {
        const viking = document.createElement('button');
        viking.type = 'button';
        viking.textContent = 'Open in Viking';
        viking.title = `Opens ${deps.volumeName(record.source)} in Viking at Location ${p.anchor_id}`;
        viking.addEventListener('click', () => deps.openLocationInViking(record, p.anchor_id));
        detail.append(viking);
      }
      wrap.append(detail);
    }
  }
}

/** Fly to a problem, light its annotations up, and show it in the list. */
async function go(record, entry, index, deps) {
  const problem = entry.result.problems[index];
  entry.current = index;
  entry.open.add(problem.kind);
  deps.rerender();
  try {
    const byId = new Map((await deps.nodesFor(record)).map((n) => [n.id, n]));
    const nodes = problem.location_ids.map((id) => byId.get(id)).filter(Boolean);
    const links = problem.links
      .map(([a, b]) => [byId.get(a)?.xyz_um, byId.get(b)?.xyz_um])
      .filter(([a, b]) => a && b && a.some((v, k) => v !== b[k]));
    // A lamella brings the chain that would replace its links: drawn with them.
    const chain = problem.extra?.chain_um;
    if (Array.isArray(chain)) {
      for (let k = 1; k < chain.length; k += 1) links.push([chain[k - 1], chain[k]]);
    }
    const { renderer } = deps;
    renderer.select({
      kind: 'nodes', key: record.key, point: problem.xyz_um, nodes, links,
      locationId: problem.anchor_id, section: problem.z_section,
      label: `${problemLabel(problem.kind)} at Location ${problem.anchor_id}`,
    });
    // Close enough to read the problem, far enough to see all of it.
    let reach = 0;
    for (const n of nodes) {
      reach = Math.max(reach, n.radius_um + Math.hypot(
        n.xyz_um[0] - problem.xyz_um[0], n.xyz_um[1] - problem.xyz_um[1],
        n.xyz_um[2] - problem.xyz_um[2]));
    }
    await renderer.flyTo(renderer.toWorld(problem.xyz_um, record.source),
      { distance: Math.max(reach * 6, 5) });
  } catch (error) {
    deps.fail(error);
  }
}
