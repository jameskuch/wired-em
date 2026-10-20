// The "Linked neurons" card on the Analyses tab: the getAllLinkedNeurons
// table — every synapse of the cell, which neuron it links to, and where it
// is — with the two orderings the lab uses, a sortable view, and the .csv and
// .xlsx downloads the MATLAB-era script produced.

const COLUMNS = [
  { key: 'neuron_id', label: 'Partner', numeric: true },
  { key: 'neuron_label', label: 'Label' },
  { key: 'synapse_type', label: 'Synapse type' },
  { key: 'tagged', label: 'Tagged?' },
  { key: 'synapse_id', label: 'Synapse ID', numeric: true },
  { key: 'direction', label: 'Direction' },
  { key: 'x', label: 'X (µm)', numeric: true },
  { key: 'y', label: 'Y (µm)', numeric: true },
  { key: 'z', label: 'Z (µm)', numeric: true },
];

const PARTNER_COLUMNS = [
  { key: 'neuron_id', label: 'Partner', numeric: true },
  { key: 'neuron_label', label: 'Label' },
  { key: 'synapse_type', label: 'Synapse type' },
  { key: 'count', label: 'Count', numeric: true },
];

// Whether Viking's tags say what a postsynapse is (Crystal Sigulinsky,
// 2026-09-22: RC1's pre-tag PSDs were all counted conventional).  Blank for a
// type Viking's type alone decides, so a sort puts those last.
const TAGGED = { tagged: 'Y', inferred: 'inferred', untagged: 'N', assumed: 'N' };

/** What the Tagged? cell says on hover. */
function tagHelp(r) {
  const tags = r.tags ? `Viking tags: ${r.tags}.` : 'No Viking tags.';
  switch (r.tag_status) {
    case 'tagged': return `${tags} They say what it is.`;
    case 'inferred': return `${tags} They do not say what it is, so its type was taken from the `
      + `structure it is linked to. Tagging it "${r.suggested_tag}" in Viking would say so.`;
    case 'untagged': return `${tags} They do not say what it is, and no linked structure does: `
      + 'it is counted as neither ribbon nor conventional. Its type has to be read from the images in Viking.';
    case 'assumed': return `${tags} They do not say what it is; it was assumed conventional, as MATLAB did.`;
    default: return 'Its Viking type says what it is; no tag needed.';
  }
}

/** The postsynapses to put right in Viking, one row each, as CSV text. */
export function worklistCsv(rows, volume, vikingOf) {
  const seen = new Set();
  const out = [['Volume', 'SynapseID', 'LocationID', 'VikingX', 'VikingY', 'VikingZ', 'TypeHere',
    'TagStatus', 'VikingTags', 'SuggestedTag', 'PartnerID', 'PartnerLabel']];
  for (const r of rows) {
    if (!['inferred', 'untagged', 'assumed'].includes(r.tag_status) || seen.has(r.synapse_id)) continue;
    seen.add(r.synapse_id);
    // Where Viking draws it now: a section correction's move taken back.
    const s = r.synapse_shift_um;
    const xyz = r.synapse_xyz_um && s
      ? [r.synapse_xyz_um[0] - s[0], r.synapse_xyz_um[1] - s[1], r.synapse_xyz_um[2]]
      : r.synapse_xyz_um;
    const v = xyz && vikingOf ? vikingOf(xyz) : ['', '', ''];
    out.push([volume, r.synapse_id, r.location_id ?? '', ...v, r.synapse_type, r.tag_status,
      r.tags, r.suggested_tag, r.neuron_id ?? '', r.neuron_label]);
  }
  const cell = (v) => {
    const text = String(v ?? '');
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return { text: `${out.map((line) => line.map(cell).join(',')).join('\n')}\n`, n: out.length - 1 };
}

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/** Rows in `dir` order of `column`; blanks sink to the bottom either way. */
export function sortRows(rows, column, dir) {
  const key = column.key;
  return [...rows].sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    const blankA = va === null || va === undefined || va === '';
    const blankB = vb === null || vb === undefined || vb === '';
    if (blankA && blankB) return 0;
    if (blankA) return 1;
    if (blankB) return -1;
    const cmp = column.numeric ? va - vb : String(va).localeCompare(String(vb));
    return cmp * dir;
  });
}

/**
 * A heading row whose every cell sorts the table (a click sorts ascending, a
 * second click reverses). `sort` is shared state; `redraw` repaints the body.
 */
export function sortableHead(columns, sort, redraw) {
  const head = el('tr');
  for (const column of columns) {
    const th = el('th', column.numeric ? '' : 'text', column.label);
    th.dataset.key = column.key;
    th.title = `Sort by ${column.label}`;
    th.addEventListener('click', () => {
      if (sort.key === column.key) sort.dir = -sort.dir; else { sort.key = column.key; sort.dir = 1; }
      redraw();
    });
    head.append(th);
  }
  const mark = () => {
    for (const th of head.children) {
      th.setAttribute('aria-sort', th.dataset.key === sort.key ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none');
    }
  };
  mark();
  return { head, mark };
}

/**
 * Fill `card` (an element whose first child is its heading) with the report
 * for `record`. `pick(source, id)` puts a partner in the Structure ID box.
 */
export async function fillLinkedNeuronsCard(card, record, {
  api, pick, volumeName, vikingOf = null, order = 'neuron', refresh = false,
}) {
  const heading = card.firstElementChild;
  card.replaceChildren(heading);
  const note = el('p', 'muted', refresh ? 'Asking Viking again…' : 'Resolving synaptic partners in Viking…');
  card.append(note);
  let report;
  try {
    report = await api.linkedNeurons(record.source, record.id, { order, refresh });
  } catch (error) {
    note.textContent = error?.detail || error?.message || String(error);
    note.classList.add('error');
    return;
  }
  note.remove();

  const rows = report.rows.map((r) => ({
    ...r,
    tagged: TAGGED[r.tag_status] || '',
    x: r.synapse_xyz_um ? r.synapse_xyz_um[0] : null,
    y: r.synapse_xyz_um ? r.synapse_xyz_um[1] : null,
    z: r.synapse_xyz_um ? r.synapse_xyz_um[2] : null,
  }));

  // -- toolbar ---------------------------------------------------------------
  const tools = el('div', 'report-tools');
  const summary = el('span', 'muted');
  const partners = `${report.n_partners} partner neuron${report.n_partners === 1 ? '' : 's'}`;
  summary.textContent = `${report.n_synapses} synapses · ${report.n_linked} linked to ${partners}`
    + (report.n_unlinked ? ` · ${report.n_unlinked} not linked to anything in Viking` : '');
  tools.append(summary);

  // Postsynapses whose Viking tags do not say what they are, counted once each.
  const unsure = new Map();
  for (const r of rows) {
    if (['inferred', 'untagged', 'assumed'].includes(r.tag_status)) unsure.set(r.synapse_id, r.tag_status);
  }
  if (unsure.size) {
    const n = (status) => [...unsure.values()].filter((v) => v === status).length;
    const parts = [];
    if (n('inferred')) parts.push(`${n('inferred')} typed from the linked structure`);
    if (n('untagged')) parts.push(`${n('untagged')} of unknown kind`);
    if (n('assumed')) parts.push(`${n('assumed')} assumed conventional`);
    const flag = el('span', 'tag-flag', `${unsure.size} PSD${unsure.size === 1 ? '' : 's'} untagged in Viking: ${parts.join(', ')}`);
    flag.title = 'Their Viking tags do not say whether they are ribbon or conventional. '
      + 'Sort or filter by Tagged? to see them; "Untagged PSDs" downloads them with a Location ID each, '
      + 'to put right in Viking.';
    tools.append(flag);
  }

  const orderField = el('label', 'field', 'Order ');
  const orderSelect = el('select');
  for (const [value, label] of [['neuron', 'by partner (getAllLinkedNeurons)'], ['type', 'by synapse type (getAllLinkedNeurons_sorted)']]) {
    const option = el('option', '', label);
    option.value = value;
    orderSelect.append(option);
  }
  orderSelect.value = report.order;
  orderSelect.addEventListener('change', () => {
    fillLinkedNeuronsCard(card, record, { api, pick, volumeName, vikingOf, order: orderSelect.value });
  });
  orderField.append(orderSelect);
  tools.append(orderField);

  const filter = el('input');
  filter.type = 'search';
  filter.placeholder = 'Filter…';
  filter.title = 'Keep rows whose partner, label, synapse type, synapse ID, Viking tags or Tagged? '
    + '(Y, N, inferred) contains this';
  tools.append(filter);

  const refreshBtn = el('button', '', '↻ Viking');
  refreshBtn.type = 'button';
  refreshBtn.title = 'Resolve the partners again from Viking (links are cached with the cell)';
  refreshBtn.addEventListener('click', () => {
    fillLinkedNeuronsCard(card, record, {
      api, pick, volumeName, vikingOf, order: orderSelect.value, refresh: true,
    });
  });
  tools.append(refreshBtn);

  const downloads = el('span', 'actions');
  for (const fmt of ['csv', 'xlsx']) {
    const link = el('a', 'muted', `.${fmt}`);
    link.href = api.linkedNeuronsUrl(record.source, record.id, fmt, { order: report.order });
    link.download = `c${record.id}-${record.source}-linked-neurons.${fmt}`;
    link.title = `Download the table as .${fmt} (${volumeName(record.source)} c${record.id})`;
    downloads.append(link);
  }
  if (unsure.size) {
    const work = el('a', 'muted', 'Untagged PSDs (.csv)');
    const name = `c${record.id}-${record.source}-untagged-psds.csv`;
    work.href = '#';
    work.title = `The ${unsure.size} postsynapses of ${volumeName(record.source)} c${record.id} whose tags `
      + 'do not say what they are: a Location ID and Viking X Y Z each (Goto Location ID / Goto Location), '
      + 'the tags they have, and for an inferred one the tag that would say so';
    work.addEventListener('click', (event) => {
      event.preventDefault();
      const { text } = worklistCsv(rows, record.source, vikingOf);
      const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    });
    downloads.append(work);
  }
  tools.append(downloads);
  card.append(tools);

  // -- the table -------------------------------------------------------------
  const scroll = el('div', 'report-scroll');
  const table = el('table', 'report');
  const thead = el('thead');
  const sort = { key: null, dir: 1 };
  const { head, mark } = sortableHead(COLUMNS, sort, () => draw());
  thead.append(head);
  const tbody = el('tbody');
  table.append(thead, tbody);
  scroll.append(table);
  card.append(scroll);

  const fmt = (v) => (v === null || v === undefined || Number.isNaN(v) ? '' : v.toFixed(2));

  function draw() {
    const needle = filter.value.trim().toLowerCase();
    let shown = rows;
    if (needle) {
      shown = rows.filter((r) => [r.neuron_id, r.neuron_label, r.synapse_type, r.synapse_id, r.tags]
        .some((v) => v !== null && v !== undefined && String(v).toLowerCase().includes(needle))
        || (r.tagged && r.tagged.toLowerCase() === needle));
    }
    if (sort.key) shown = sortRows(shown, COLUMNS.find((c) => c.key === sort.key), sort.dir);
    mark();
    tbody.replaceChildren();
    for (const r of shown) {
      const tr = el('tr');
      if (r.tag_status === 'untagged' || r.tag_status === 'assumed') tr.className = 'untagged';
      else if (r.tag_status === 'inferred') tr.className = 'inferred';
      const partner = el('td', 'text');
      if (r.neuron_id === null || r.neuron_id === undefined) {
        partner.append(el('span', 'muted', '— unlinked'));
      } else {
        const btn = el('button', 'cx-id', `c${r.neuron_id}`);
        btn.type = 'button';
        btn.title = `Put c${r.neuron_id} (${volumeName(record.source)}) in the Structure ID box`;
        btn.addEventListener('click', () => pick(record.source, r.neuron_id));
        partner.append(btn);
      }
      const tagged = el('td', 'text', r.tagged || '—');
      tagged.title = tagHelp(r);
      tr.append(partner,
        el('td', 'text', r.neuron_label),
        el('td', 'text', r.synapse_type),
        tagged,
        el('td', '', String(r.synapse_id)),
        el('td', 'text', r.direction),
        el('td', '', fmt(r.x)), el('td', '', fmt(r.y)), el('td', '', fmt(r.z)));
      tbody.append(tr);
    }
    count.textContent = needle ? `${shown.length} of ${rows.length} rows` : `${rows.length} rows`;
  }
  const count = el('span', 'muted');
  tools.append(count);
  filter.addEventListener('input', draw);
  draw();

  // -- synapses per partner ----------------------------------------------------
  // Sortable by every column too (asked for 2026-09-17): by count to see the
  // strongest partners first, by type to group them.
  if (report.partners.length) {
    const details = el('details', 'report-details');
    details.append(el('summary', 'muted', `Synapses per partner (${report.partners.length})`));
    const per = el('table', 'report');
    const pSort = { key: null, dir: 1 };
    const pBody = el('tbody');
    const drawPartners = () => {
      pMark();
      const shown = pSort.key
        ? sortRows(report.partners, PARTNER_COLUMNS.find((c) => c.key === pSort.key), pSort.dir)
        : report.partners;
      pBody.replaceChildren();
      for (const p of shown) {
        const tr = el('tr');
        const td = el('td', 'text');
        const btn = el('button', 'cx-id', `c${p.neuron_id}`);
        btn.type = 'button';
        btn.title = `Put c${p.neuron_id} (${volumeName(record.source)}) in the Structure ID box`;
        btn.addEventListener('click', () => pick(record.source, p.neuron_id));
        td.append(btn);
        tr.append(td, el('td', 'text', p.neuron_label), el('td', 'text', p.synapse_type), el('td', '', String(p.count)));
        pBody.append(tr);
      }
    };
    const { head: pHeadRow, mark: pMark } = sortableHead(PARTNER_COLUMNS, pSort, drawPartners);
    const pHead = el('thead');
    pHead.append(pHeadRow);
    per.append(pHead, pBody);
    drawPartners();
    const wrap = el('div', 'report-scroll');
    wrap.append(per);
    details.append(wrap);
    card.append(details);
  }
}
