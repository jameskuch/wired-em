// "Return list of annotators": who worked on a cell, from the render window's
// right-click menu.
//
// The numbers come live from Viking and are shares of the *record*, not of the
// work: Viking keeps one username per annotation -- the last person to write
// it -- and no history, so an annotation one person placed and another later
// moved counts for the second.  The panel says that in the footer rather than
// leaving a reader to assume otherwise (see sbfsem/analysis/annotators.py).
//
// It stays open until the focus leaves it: text can be selected inside it, the
// table can be copied, and another browser window can be brought forward to
// paste into without the panel vanishing.  A click anywhere else in the app,
// Tab out of it, or Esc closes it.

const ID = 'annotators-pop';

const n = (v) => Number(v || 0).toLocaleString('en-US');
const pct = (v) => `${(Number(v || 0) * 100).toFixed(1)}%`;
const day = (iso) => (iso ? String(iso).slice(0, 10) : '—');
const when = (iso) => {
  if (!iso) return 'never';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
};

export function closeAnnotators() {
  document.getElementById(ID)?.remove();
}

/**
 * Open the panel for one cell and fill it from the API.
 *
 * `deps`: { api, volumeName(source), place(element) }.  `place` puts it on
 * screen (the same clamping the context menu uses).  An error is shown inside
 * the panel, which is where the reader is looking.
 */
export function showAnnotators(record, deps) {
  closeAnnotators();
  const pop = document.createElement('div');
  pop.id = ID;
  pop.className = 'annot-pop';
  pop.tabIndex = -1;
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', `Annotators of c${record.id}`);

  const head = document.createElement('div');
  head.className = 'annot-head';
  const title = document.createElement('span');
  title.className = 'annot-title';
  title.textContent = `Annotators of c${record.id} — ${deps.volumeName(record.source)}`;
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'annot-copy';
  copy.textContent = 'Copy';
  copy.disabled = true;
  copy.title = 'Copy the table as tab-separated text: it pastes into a spreadsheet as columns';
  const shut = document.createElement('button');
  shut.type = 'button';
  shut.className = 'annot-shut';
  shut.textContent = '✕';
  shut.title = 'Close (Esc, or a click anywhere else)';
  shut.addEventListener('click', closeAnnotators);
  head.append(title, copy, shut);

  const body = document.createElement('div');
  body.className = 'annot-body';
  const note = document.createElement('p');
  note.className = 'note';
  note.textContent = 'Asking Viking who annotated this cell…';
  body.append(note);
  pop.append(head, body);
  deps.place(pop);
  pop.focus({ preventScroll: true });
  bindDismissal(pop);

  deps.api.annotators(record.source, record.id).then((report) => {
    if (!document.getElementById(ID)) return;      // closed while we waited
    fill(body, report, record, deps);
    // Placed twice: the first time it held one line, and a table of eight
    // annotators that is clamped to the screen as a one-liner hangs off it.
    deps.place(pop);
    copy.disabled = false;
    copy.addEventListener('click', async () => {
      const text = asText(report, record, deps);
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = 'Copied';
      } catch {
        // No clipboard permission (or no HTTPS): select the table instead, so
        // the keyboard can still copy it.
        selectTable(body);
        copy.textContent = 'Selected';
      }
      setTimeout(() => { copy.textContent = 'Copy'; }, 1500);
    });
  }).catch((error) => {
    if (!document.getElementById(ID)) return;
    note.textContent = `Viking could not be asked: ${error.detail || error.message}`;
    note.className = 'note error';
    deps.place(pop);
  });
}

/** Close on a click outside, on focus moving outside, and on Esc. */
function bindDismissal(pop) {
  const gone = () => !document.getElementById(ID);
  const outside = (event) => {
    if (gone()) { detach(); return; }
    if (!event.target.closest?.(`#${ID}`)) { closeAnnotators(); detach(); }
  };
  // focusin rather than the panel's own focusout: a click on the panel's own
  // text moves the focus to the body without leaving the panel, and closing
  // on that would make the table impossible to select.
  const moved = (event) => {
    if (gone()) { detach(); return; }
    if (!pop.contains(event.target)) { closeAnnotators(); detach(); }
  };
  const key = (event) => {
    if (gone()) { detach(); return; }
    if (event.key !== 'Escape') return;
    event.stopPropagation();
    closeAnnotators();
    detach();
  };
  function detach() {
    document.removeEventListener('pointerdown', outside, true);
    document.removeEventListener('focusin', moved, true);
    document.removeEventListener('keydown', key, true);
  }
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('focusin', moved, true);
  document.addEventListener('keydown', key, true);
}

const COLUMNS = [
  ['Annotator', (a) => a.username || '(no username)', 'left'],
  ['Annotations', (a) => n(a.annotations)],
  ['Share', (a) => pct(a.fraction)],
  ['As placed', (a) => n(a.as_placed)],
  ['Edits', (a) => n(a.revisions)],
  ['Synapses', (a) => n(a.structures)],
  ['First', (a) => day(a.first)],
  ['Last', (a) => day(a.last)],
];
const HELP = {
  Annotations: 'Annotations Viking attributes to this username now — the last person to write each one',
  Share: 'This annotator’s annotations as a percentage of the cell’s',
  'As placed': 'Never written again after it was created: certainly this person’s own annotation',
  Edits: 'Annotations this person wrote well after they were created — an edit of an annotation '
    + 'that already existed. Whose it was is not recorded.',
  Synapses: 'Child structures (synapses, cell boundaries) created under this username',
  First: 'Earliest annotation of theirs on this cell, by when it was created',
  Last: 'Latest write of theirs on this cell',
};

function fill(body, report, record, deps) {
  body.replaceChildren();
  const table = document.createElement('table');
  table.className = 'annot-table';
  const thead = document.createElement('thead');
  const hrow = document.createElement('tr');
  for (const [label] of COLUMNS) {
    const th = document.createElement('th');
    th.textContent = label;
    if (HELP[label]) th.title = HELP[label];
    hrow.append(th);
  }
  thead.append(hrow);
  const tbody = document.createElement('tbody');
  for (const a of report.annotators) {
    const tr = document.createElement('tr');
    COLUMNS.forEach(([label, value, align], i) => {
      const td = document.createElement('td');
      td.textContent = value(a);
      if (align === 'left') td.className = 'annot-name';
      if (i === 0 && a.username && a.username === report.owner) {
        td.title = 'Created the cell’s own Viking structure';
        td.append(document.createTextNode(' ★'));
      }
      if (label === 'Last' && a.last) td.title = when(a.last);
      tr.append(td);
    });
    tbody.append(tr);
  }
  table.append(thead, tbody);
  body.append(table);

  for (const line of footer(report, record, deps)) {
    const p = document.createElement('p');
    p.className = 'note';
    p.textContent = line;
    body.append(p);
  }
}

/** The lines under the table: the totals first, then what the record cannot say. */
function footer(report, record, deps) {
  const lines = [];
  const people = report.annotators.length;
  lines.push(`${n(report.annotations)} annotations · ${people} annotator${people === 1 ? '' : 's'}`
    + ` · ${n(report.on_children)} on ${n(report.n_children)} child structure`
    + `${report.n_children === 1 ? '' : 's'}`
    + (report.owner ? ` · ★ ${report.owner} created c${record.id}` : ''));
  const tol = report.tolerance_s >= 3600
    ? `${Math.round(report.tolerance_s / 3600)} h`
    : `${Math.round(report.tolerance_s / 60)} min`;
  lines.push(`${n(report.revisions)} annotations (${pct(report.annotations
    ? report.revisions / report.annotations : 0)}) were last written more than ${tol} after they`
    + ' were created. Viking records only that last writer, so who placed them is not in the'
    + ' record: a mentor who fixes an annotation takes it over entirely.');
  if (report.restamped) {
    lines.push(`${n(report.restamped)} more were re-stamped in ${report.bulk_edits} bulk edit`
      + `${report.bulk_edits === 1 ? '' : 's'} — tens of annotations a minute under several`
      + ' usernames at once, or written while the rest of the volume was being rewritten by the'
      + ' hundred (a re-registration, Update Volume Positions): volume-wide operations rather'
      + ' than anyone’s work. They are left out of the edit counts.');
  }
  lines.push('Someone whose every annotation was later edited by somebody else does not appear'
    + ' here at all. The record keeps one name per annotation.');
  return lines;
}

/** The table as tab-separated text, with the cell it belongs to and the caveats. */
function asText(report, record, deps) {
  const lines = [
    `Annotators of c${record.id} (${deps.volumeName(record.source)}), read from Viking `
      + `${new Date().toISOString().slice(0, 19)}Z`,
    '',
    COLUMNS.map(([label]) => label).join('\t'),
  ];
  for (const a of report.annotators) {
    lines.push(COLUMNS.map(([, value]) => String(value(a)).replace(/ /g, ' ')).join('\t'));
  }
  lines.push('', ...footer(report, record, deps));
  return `${lines.join('\n')}\n`;
}

function selectTable(body) {
  const table = body.querySelector('.annot-table');
  if (!table) return;
  const range = document.createRange();
  range.selectNodeContents(table);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}
