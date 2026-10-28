// Display → Mark damaged sections: where a cell crosses a damaged section of its volume.
//
// A damaged section is one the volume's slice-to-volume registration was built without
// (Viking's NonStosSectionNumbers).  Viking's section-correction service lists a set's
// as `damaged_z` (service 1.6.0): it corrects no other section from them and gives each
// a shift of its own.  The server (`GET .../damaged-sections`, sbfsem.analysis.damaged)
// finds where a cell crosses one -- an annotation on it, or a link that skips over it,
// as InferiorMonkey's 916-935 are crossed, which hold no annotation -- and answers a
// disc for each (`marks`).  This module turns that answer into ring segments and words.
//
// Pure: no three.js and no DOM (tests/js/test_damaged.mjs).

/** The rings' color: apart from the markers' red, yellow and blue and the selection. */
export const DAMAGED_COLOR = '#ff3df5';
/** Sides of a ring; a dashed ring draws every other one. */
export const DAMAGED_SIDES = 24;
/** A ring is drawn a little outside its disc, so it rings the annotation's own. */
export const DAMAGED_RING_SCALE = 1.15;
/** The smallest ring drawn, µm: a point annotation still gets a ring. */
export const DAMAGED_MIN_RADIUS_UM = 0.25;

/**
 * Line segments for the marks, six numbers each (x0 y0 z0 x1 y1 z1), in the cell's
 * frame: each a ring in its section plane (a disc, never a sphere), solid for an
 * annotation on the damaged section, dashed (every other side) for a link passing
 * over it.  A mark with a non-finite center is left out.
 *
 * @param marks  `[{ xyz_um: [x, y, z], radius_um, over }]`
 * @returns {Float32Array}
 */
export function damagedRingSegments(marks, {
  sides = DAMAGED_SIDES, scale = DAMAGED_RING_SCALE, minRadius = DAMAGED_MIN_RADIUS_UM,
} = {}) {
  const usable = (marks || []).filter((m) => Array.isArray(m?.xyz_um)
    && m.xyz_um.length === 3 && m.xyz_um.every(Number.isFinite));
  let count = 0;
  for (const m of usable) count += m.over ? Math.ceil(sides / 2) : sides;
  const out = new Float32Array(count * 6);
  let o = 0;
  for (const m of usable) {
    const [cx, cy, cz] = m.xyz_um;
    const r0 = Number(m.radius_um);
    const r = Math.max(Number.isFinite(r0) && r0 > 0 ? r0 * scale : 0, minRadius);
    for (let k = 0; k < sides; k += m.over ? 2 : 1) {
      const a0 = (k / sides) * Math.PI * 2;
      const a1 = ((k + 1) / sides) * Math.PI * 2;
      out[o] = cx + r * Math.cos(a0); out[o + 1] = cy + r * Math.sin(a0); out[o + 2] = cz;
      out[o + 3] = cx + r * Math.cos(a1); out[o + 4] = cy + r * Math.sin(a1); out[o + 5] = cz;
      o += 6;
    }
  }
  return out;
}

/** Section numbers as runs: [915, 916, 917, 930] -> '915–917, 930' (sorted, each once). */
export function sectionRanges(zs) {
  const sorted = [...new Set((zs || []).filter(Number.isFinite))].sort((a, b) => a - b);
  const runs = [];
  for (const z of sorted) {
    const last = runs[runs.length - 1];
    if (last && z === last[1] + 1) last[1] = z;
    else runs.push([z, z]);
  }
  return runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(', ');
}

const plural = (n, one, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;

/**
 * What a cell's Registration section says about its damaged sections, from the
 * server's answer (`null` while it is asked for): `{ text, lines, crossed }`.
 * `text` is the one line shown, `lines` the detail (runs of consecutive sections
 * with the same counts), `crossed` how many damaged sections the cell crosses.
 * No list says why, in the server's words ("No damaged sections listed for RC1").
 */
export function damagedText(answer) {
  if (!answer) return { text: 'Damaged sections: asking…', lines: [], crossed: 0 };
  const volume = answer.volume_name || 'this volume';
  if (!answer.listed) {
    return { text: answer.note || `No damaged sections listed for ${volume}`, lines: [], crossed: 0 };
  }
  const listed = (answer.damaged_z || []).length;
  const sections = answer.sections || [];
  if (!sections.length) {
    return {
      text: `Crosses none of ${volume}'s ${plural(listed, 'damaged section')}`,
      lines: [`${volume}'s damaged sections: ${sectionRanges(answer.damaged_z)}`],
      crossed: 0,
    };
  }
  const runs = [];
  for (const s of sections) {
    const last = runs[runs.length - 1];
    if (last && s.z === last.to + 1 && s.n_annotations === last.on && s.n_links_over === last.over) {
      last.to = s.z;
    } else {
      runs.push({ from: s.z, to: s.z, on: s.n_annotations, over: s.n_links_over });
    }
  }
  const lines = runs.map(({ from, to, on, over }) => {
    const where = from === to ? `${from}` : `${from}–${to}`;
    const each = from === to ? '' : ' each';
    const parts = [];
    if (on) parts.push(plural(on, 'annotation'));
    if (over) parts.push(`${plural(over, 'link')} over`);
    return `${where}: ${parts.join(', ')}${each}`;
  });
  return {
    text: `Crosses ${sections.length.toLocaleString('en-US')} of ${volume}'s `
      + `${plural(listed, 'damaged section')}: ${sectionRanges(sections.map((s) => s.z))}`,
    lines,
    crossed: sections.length,
  };
}
