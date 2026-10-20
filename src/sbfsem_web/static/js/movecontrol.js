// Moving an annotation by hand, after Viking's correction (2026-10-19): the main
// window's side of manual moves.  manualmoves.js holds the rules (the store, a step
// along an axis, the list); this holds what is on the page: move mode and its keys,
// the panel over the render (where the annotation is, the viewport's axes, the axis
// and step), the moves drawn as arrows, the not-yet-applied move drawn ahead on the
// chosen ring, applying the moves to the render, and the cell panel's list.
//
// A move is kept on the cell (`record.manualMoves`) under the registration setting it
// was made under, saved with the page's state and its saved sessions, and sent with
// every load of the cell (api.js `setMovesProvider`); the server applies the set of
// the setting it loads after the correction (sbfsem.core.moves).  Nothing is written
// to Viking.  Applying is a load of the cell from the server's copy: no Viking
// request, then the render, the rings, the skeleton and the shift layer are drawn
// again from the moved copy.

import {
  AXES, AXIS_KEYS, MAX_MOVE_SECTIONS, MAX_MOVE_UM, STEPS, STEP_DEFAULT, axisNote, baseTag, deleteMoves,
  membersOf, moveCounts, moveRows, movesOf, normalizeStore, pendingDeltas, resetMoves, signedMove, stepSet,
  tagWords,
} from './manualmoves.js';
import { correctedXyz, pairEnds } from './shifts.js';

const SLOTS = ['primary', 'above', 'below'];
const SLOT_WORDS = { primary: 'yellow', above: 'red', below: 'blue' };
const NEAR = 1e-6;
const SVG = 'http://www.w3.org/2000/svg';

/** The thickness of one section of `record`'s volume, um. */
export function sectionUm(record) {
  const z = record?.volume?.volume_scale?.[2];
  if (!Number.isFinite(z) || z <= 0) return 0.07;
  return z > 1 ? z / 1000 : z;
}

/** The move the server applied to `node` (from `/nodes`): `{ dx, dy, dz }`, zeros for none. */
export function appliedMove(node) {
  const m = node?.manual_shift_um;
  return {
    dx: Array.isArray(m) ? m[0] : 0,
    dy: Array.isArray(m) ? m[1] : 0,
    dz: Number.isFinite(node?.manual_dz) ? node.manual_dz : 0,
  };
}

/** Whether two moves are the same, to 0.1 nm. */
export function sameMove(a, b) {
  return Math.abs((a?.dx ?? 0) - (b?.dx ?? 0)) < 1e-4 + NEAR
    && Math.abs((a?.dy ?? 0) - (b?.dy ?? 0)) < 1e-4 + NEAR
    && (a?.dz ?? 0) === (b?.dz ?? 0);
}

/**
 * The moves of `tag` not yet in the cell's nodes (what the last load applied):
 * `[{ id, want, applied }]`, one for each ID whose move differs, including a move
 * deleted since (want null).  `nodes` is the cell's `/nodes`.
 */
export function pendingMoves(store, tag, nodes) {
  // An ID the cell no longer has (deleted in Viking) has nothing to draw and nothing to wait for.
  const set = movesOf(store, tag);
  const out = [];
  for (const node of nodes || []) {
    const applied = appliedMove(node);
    const want = set[String(node.id)] ?? null;
    if (!sameMove(want ?? { dx: 0, dy: 0, dz: 0 }, applied)) out.push({ id: node.id, want, applied });
  }
  return out;
}

/**
 * The arrows of the cell's moves: `[{ id, from, to, slot }]` from where the correction
 * left each annotation to where its move (applied or not) puts it, in the volume's um.
 * `marked` maps a Location ID to its marker slot.
 */
export function moveArrows(store, tag, nodes, thicknessUm, marked = new Map()) {
  const set = movesOf(store, tag);
  const out = [];
  for (const node of nodes || []) {
    const m = set[String(node.id)];
    if (!m) continue;
    const from = correctedXyz(node);
    const to = [from[0] + m.dx, from[1] + m.dy, from[2] + m.dz * thicknessUm];
    out.push({ id: node.id, from, to, slot: marked.get(node.id) ?? null });
  }
  return out;
}

export class MoveController {
  /**
   * `renderer`, `state`; `nodesFor(record)` (fetches and caches the cell's `/nodes`),
   * `cachedNodes(record)`; `shiftPair(record)`: the pair the shift layer draws for the
   * cell, or null; `activeSlot()`: the marker the arrow keys act on; `hooks`:
   * `persist`, `reload(record)` (load the cell again with its moves and redraw it;
   * resolves the new record), `renderNeuronList`, `status(text)`, `flare(text)`,
   * `fail(error)`, `fly(item)` (bring an annotation into view and select it),
   * `prepare(record)` (have the server keep the cell's grid for Apply: `api.gridJob`);
   * `dom`: `{ panel }`, the panel's host over the render.
   */
  constructor({ renderer, state, nodesFor, cachedNodes, shiftPair = () => null, activeSlot, hooks, dom }) {
    Object.assign(this, { renderer, state, nodesFor, cachedNodes, shiftPair, activeSlot, hooks, dom });
    this.active = false;
    this.axis = 'x';
    this.step = STEP_DEFAULT;
    this.applying = null;
    // What a step moves (manualmoves.js SCOPES): the chosen annotation (and the ones
    // ⌘/Ctrl-clicked in), its whole section, or the sections `range` (inclusive).
    this.scope = 'one';
    this.range = null;
    this.extra = new Map();     // cell key -> Set of Location IDs added by hand
    this._build();
    // The panel's gizmo follows the camera while move mode is on.
    renderer.controls?.addEventListener?.('change', () => { if (this.active) this._drawGizmo(); });
  }

  // -- what is being moved -----------------------------------------------------------------

  /** The annotation the arrows move: `{ slot, item, record }`, or null. */
  target() {
    const slot = this.activeSlot?.() || 'primary';
    for (const s of [slot, 'primary']) {
      const item = this.renderer.slotItem(s);
      if (item?.kind === 'annotation' && item.locationId != null) {
        const record = this.state.neurons.get(item.key);
        if (record) return { slot: s, item, record };
      }
    }
    return null;
  }

  /** The registration setting `record`'s moves are kept under now. */
  tagOf(record) {
    return baseTag(record?.summary?.copy_tag);
  }

  /** The cell's moves, made safe (a saved session can hold anything). */
  storeOf(record) {
    const store = normalizeStore(record?.manualMoves);
    return store;
  }

  // -- move mode ---------------------------------------------------------------------------

  /** Start moving the chosen annotation (the yellow one, or the red or blue marker moved last). */
  async start() {
    const t = this.target();
    if (!t) {
      this.hooks.flare('Select an annotation first (triple click a ring), then Move');
      return false;
    }
    if (!this.tagOf(t.record)) {
      this.hooks.flare(`c${t.record.id} was not loaded from the server with a setting: it cannot be moved`);
      return false;
    }
    this.active = true;
    this.scope = 'one';
    this.range = null;
    this.extra = new Map();
    // The server keeps the cell's whole-cell grid from here, so Apply contours again
    // only the blocks its moves touch (asked once, quietly: nothing waits on it).
    this.hooks.prepare?.(t.record);
    await this.nodesFor(t.record).catch((error) => this.hooks.fail(error));
    this.update();
    this.hooks.status(`Moving annotation ${t.item.locationId}: ←/→ move along ${this._axisLabel()} · 1–7 axis `
      + '· Enter applies · Esc done');
    return true;
  }

  /** Leave move mode; a move not yet in the render is applied on the way out. */
  async stop({ apply = true } = {}) {
    if (!this.active) return;
    this.active = false;
    this.extra = new Map();
    this.update();
    if (apply) await this.applyPending();
  }

  /**
   * The keyboard, while move mode is on (Renderer3D.onMoveKey): Left and Right move the
   * annotation along the axis (Shift ten steps, Alt a tenth), 1 to 7 choose the axis,
   * Enter applies, Esc leaves.  `g` starts it with an annotation selected.  True when
   * the key was this one's.
   */
  onKey(event) {
    if (event.metaKey || event.ctrlKey) return false;
    if (!this.active) {
      if (event.key === 'g' && !event.altKey && this.target()) {
        this.start();
        return true;
      }
      return false;
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const factor = event.shiftKey ? 10 : event.altKey ? 0.1 : 1;
      this.nudge(event.key === 'ArrowRight' ? 1 : -1, factor);
      return true;
    }
    const digit = Number(event.key);
    if (Number.isInteger(digit) && digit >= 1 && digit <= AXIS_KEYS.length) {
      this.setAxis(AXIS_KEYS[digit - 1]);
      return true;
    }
    if (event.key === 'Enter') { this.applyPending(); return true; }
    if (event.key === 'Escape') { this.stop(); return true; }
    return false;
  }

  setAxis(axis) {
    if (!AXIS_KEYS.includes(axis)) return;
    this.axis = axis;
    this.update();
  }

  setStep(step) {
    if (Number.isFinite(step) && step > 0) this.step = step;
    this.update();
  }

  /**
   * What a step moves: 'one', 'section' or 'range'.  A range starts as the sections from
   * the blue marker to the red one when both are on the cell, else the chosen one's.
   */
  setScope(scope) {
    if (!['one', 'section', 'range'].includes(scope)) return;
    this.scope = scope;
    if (scope === 'range' && !this.range) this.range = this.markerRange() ?? this._ownSection();
    this.update();
  }

  /** The sections `from` to `to` (either order) for a 'range'. */
  setRange(from, to) {
    if (!Number.isFinite(from) || !Number.isFinite(to)) return;
    this.range = { from: Math.round(Math.min(from, to)), to: Math.round(Math.max(from, to)) };
    this.scope = 'range';
    this.update();
  }

  /** The sections between the red and the blue marker, when both are annotations of the chosen one's cell. */
  markerRange() {
    const t = this.target();
    const above = this.renderer.slotItem('above');
    const below = this.renderer.slotItem('below');
    const ok = (m) => m?.kind === 'annotation' && m.key === t?.item.key && Number.isFinite(m.section);
    if (!t || !ok(above) || !ok(below)) return null;
    return { from: Math.min(above.section, below.section), to: Math.max(above.section, below.section) };
  }

  _ownSection() {
    const t = this.target();
    const node = t ? this.cachedNodes(t.record)?.find((n) => n.id === t.item.locationId) : null;
    if (!node) return null;
    const s = Number.isFinite(node.manual_dz) ? node.z_section - node.manual_dz : node.z_section;
    return { from: s, to: s };
  }

  /** Add an annotation of the chosen one's cell to what a step moves, or take it out (⌘/Ctrl-click). */
  toggleMember(key, locationId) {
    const t = this.target();
    if (!this.active || !t || locationId == null) return false;
    if (key !== t.item.key) {
      this.hooks.flare(`Only annotations of c${t.record.id} move with annotation ${t.item.locationId}`);
      return true;
    }
    if (locationId === t.item.locationId) return true;
    if (!this.extra.has(key)) this.extra.set(key, new Set());
    const set = this.extra.get(key);
    if (set.has(locationId)) set.delete(locationId); else set.add(locationId);
    this.update();
    return true;
  }

  /** The Location IDs a step moves now (manualmoves.js `membersOf`). */
  members(t = this.target()) {
    if (!t) return [];
    const nodes = this.cachedNodes(t.record);
    if (!nodes) return [t.item.locationId];
    return membersOf(nodes, {
      scope: this.scope, target: t.item.locationId, from: this.range?.from, to: this.range?.to,
      extra: [...(this.extra.get(t.item.key) ?? [])],
    });
  }

  _axisLabel() { return AXES.find((a) => a.key === this.axis)?.label ?? this.axis; }

  /** The annotation's own shift, for the Shift axis: the pair the layer draws, else the correction. */
  _shiftOf(record, node) {
    if (!node) return null;
    const pair = this.shiftPair(record) || 'correction';
    try {
      return pairEnds(node, pair).shift;
    } catch {
      return null;
    }
  }

  /**
   * One step of `sign` (+1/-1) times `factor` steps along the axis, for every annotation
   * a step moves (the chosen one, its section, a run of sections): each the same step.
   */
  nudge(sign, factor = 1) {
    const t = this.target();
    if (!t) return;
    const tag = this.tagOf(t.record);
    const nodes = this.cachedNodes(t.record);
    const node = nodes?.find((n) => n.id === t.item.locationId) ?? null;
    const ids = this.members(t);
    const { store, along, over } = stepSet(this.storeOf(t.record), tag, ids, this.axis, sign, this.step * factor, {
      frame: this.renderer.viewAxes?.(), shift: this._shiftOf(t.record, node),
    });
    if (!along) {
      this.hooks.flare(this.axis === 'shift'
        ? `Annotation ${t.item.locationId} has no shift to follow: choose another axis`
        : `${this._axisLabel()} points along Z in this view, and an annotation stays on its section: `
          + 'choose another axis (or Z, to put it on another section)');
      return;
    }
    if (over.length) {
      this.hooks.flare(`A manual move is at most ${MAX_MOVE_UM} µm in X and Y and ${MAX_MOVE_SECTIONS} sections in Z`
        + (ids.length > 1 ? ` (${over.length} of the ${ids.length} would go past it)` : ''));
      return;
    }
    t.record.manualMoves = store;
    this.hooks.persist();
    this.update();
  }

  /** Apply every cell's moves that are not in its render yet (Enter, Apply, leaving). */
  async applyPending() {
    const records = [...this.state.neurons.values()].filter((r) => this.pendingOf(r).length);
    for (const record of records) await this.apply(record);
  }

  /** The moves of `record` not yet in its render (its nodes say what was applied). */
  pendingOf(record) {
    const nodes = this.cachedNodes(record);
    const tag = this.tagOf(record);
    if (!nodes || !tag) return [];
    return pendingMoves(this.storeOf(record), tag, nodes);
  }

  /** Load the cell again with its moves, and draw it from the moved copy. */
  async apply(record) {
    if (this.applying) await this.applying.catch(() => {});
    const n = this.pendingOf(record).length;
    this.hooks.status(`Applying ${n} manual move${n === 1 ? '' : 's'} to c${record.id}…`);
    const run = (async () => {
      const fresh = await this.hooks.reload(record);
      if (!fresh) return;
      await this.nodesFor(fresh).catch(() => {});
      const applied = fresh.summary?.manual_moves;
      this.hooks.status(applied
        ? `c${record.id}: ${applied.n_moved} annotation${applied.n_moved === 1 ? '' : 's'} moved by hand, `
          + `the longest ${applied.max_um.toFixed(2)} µm in X and Y${applied.n_unknown
            ? ` (${applied.n_unknown} no longer in the cell)` : ''}`
        : `c${record.id}: drawn without manual moves`);
    })();
    this.applying = run;
    try {
      await run;
    } catch (error) {
      this.hooks.fail(error);
    } finally {
      if (this.applying === run) this.applying = null;
      this.update();
    }
  }

  // -- the list's actions ------------------------------------------------------------------

  /** Delete `ids`' moves of `record` under its setting, and apply. */
  async remove(record, ids) {
    const tag = this.tagOf(record);
    if (!tag || !ids.length) return;
    record.manualMoves = deleteMoves(this.storeOf(record), tag, ids);
    this.hooks.persist();
    this.update();
    await this.apply(record);
  }

  /** Every move of `record` under its setting goes (the other settings' stay), and apply. */
  async reset(record) {
    const tag = this.tagOf(record);
    if (!tag) return;
    record.manualMoves = resetMoves(this.storeOf(record), tag);
    this.hooks.persist();
    this.update();
    await this.apply(record);
  }

  // -- what is drawn -----------------------------------------------------------------------

  /** Draw again: every cell's move arrows, the chosen rings ahead of their moves, the panel. */
  update() {
    const marked = new Map();       // key -> Map(id -> slot)
    for (const slot of SLOTS) {
      const item = this.renderer.slotItem(slot);
      if (item?.kind !== 'annotation' || item.locationId == null) continue;
      if (!marked.has(item.key)) marked.set(item.key, new Map());
      marked.get(item.key).set(item.locationId, slot);
    }
    for (const record of this.state.neurons.values()) this.refresh(record, marked.get(record.key));
    this._drawSet();
    // The chosen rings drawn where their moves put them, until the moves are applied.
    for (const slot of SLOTS) {
      const item = this.renderer.slotItem(slot);
      let nudge = null;
      if (item?.kind === 'annotation' && item.locationId != null) {
        const record = this.state.neurons.get(item.key);
        const node = record ? this.cachedNodes(record)?.find((x) => x.id === item.locationId) : null;
        const want = record ? movesOf(this.storeOf(record), this.tagOf(record))[String(item.locationId)] : null;
        if (node) {
          const had = appliedMove(node);
          const now = want ?? { dx: 0, dy: 0, dz: 0 };
          nudge = [now.dx - had.dx, now.dy - had.dy, (now.dz - had.dz) * sectionUm(record)];
        }
      }
      this.renderer.nudgeHighlight?.(slot, nudge);
    }
    this._renderPanel();
  }

  /**
   * The rings of what a step moves, where their moves put them, while move mode is on and it
   * is more than the chosen annotation (`Renderer3D.setMoveSet`); none otherwise.
   */
  _drawSet() {
    const t = this.active ? this.target() : null;
    for (const key of this.renderer.moveSets?.keys?.() ?? []) {
      if (!t || key !== t.item.key) this.renderer.setMoveSet?.(key, []);
    }
    if (!t) return;
    const ids = this.members(t);
    const nodes = this.cachedNodes(t.record);
    if (!nodes || ids.length < 2) {
      this.renderer.setMoveSet?.(t.item.key, []);
      return;
    }
    const want = new Set(ids);
    const set = movesOf(this.storeOf(t.record), this.tagOf(t.record));
    const thickness = sectionUm(t.record);
    const rings = [];
    for (const node of nodes) {
      if (!want.has(node.id)) continue;
      const from = correctedXyz(node);
      const m = set[String(node.id)] ?? { dx: 0, dy: 0, dz: 0 };
      rings.push({ center: [from[0] + m.dx, from[1] + m.dy, from[2] + m.dz * thickness], radius: node.radius_um });
    }
    this.renderer.setMoveSet?.(t.item.key, rings);
  }

  /**
   * One cell's move arrows (shown while it has moves, Show moves is on, or move mode is),
   * and its skeleton bent to the moves not yet in the render.
   */
  refresh(record, marked = null) {
    const nodes = this.cachedNodes(record);
    const tag = this.tagOf(record);
    const store = this.storeOf(record);
    this.renderer.nudgeSkeleton?.(record.key, nodes && tag
      ? pendingDeltas(pendingMoves(store, tag, nodes), sectionUm(record)) : null);
    const show = record.display?.showMoves !== false || this.active;
    if (!nodes || !tag || !show || !Object.keys(movesOf(store, tag)).length) {
      this.renderer.removeMoveArrows?.(record.key);
      if (!nodes && tag && Object.keys(movesOf(store, tag)).length && show) {
        // Moves to draw and no annotations yet: fetch them once, then draw.
        this.nodesFor(record).then(() => this.update(), () => {});
      }
      return;
    }
    const marks = moveArrows(store, tag, nodes, sectionUm(record), marked ?? new Map());
    this.renderer.setMoveArrows?.(record.key, marks, {
      width: record.display?.shiftWidth ?? 2.5,
    });
  }

  // -- the panel over the render -----------------------------------------------------------

  _build() {
    const host = this.dom?.panel;
    if (!host) return;
    host.replaceChildren();
    host.classList.add('move-panel');
    const el = (tag, cls, text) => {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    };
    const head = el('div', 'mv-head');
    this._title = el('span', 'mv-title');
    const close = el('button', 'mv-close', '✕');
    close.type = 'button';
    close.title = 'Done moving (Esc): a move not yet in the render is applied';
    close.addEventListener('click', () => { this.stop(); this._refocus(); });
    head.append(this._title, close);

    this._where = el('div', 'mv-where');
    this._moved = el('div', 'mv-moved');

    const body = el('div', 'mv-body');
    this._gizmo = document.createElementNS(SVG, 'svg');
    this._gizmo.setAttribute('class', 'mv-gizmo');
    this._gizmo.setAttribute('viewBox', '0 0 76 76');
    this._gizmo.setAttribute('aria-label', 'The volume\'s X, Y and Z as the viewport sees them');
    const controls = el('div', 'mv-controls');
    const axes = el('div', 'mv-axes');
    this._axisButtons = new Map();
    AXES.forEach((a, i) => {
      const b = el('button', `mv-axis mv-${a.group}`, a.label);
      b.type = 'button';
      b.title = `${a.help} (key ${i + 1})`;
      b.addEventListener('click', () => { this.setAxis(a.key); this._refocus(); });
      this._axisButtons.set(a.key, b);
      axes.append(b);
    });
    this._note = el('div', 'mv-note');
    const steps = el('div', 'mv-steps');
    const minus = el('button', 'mv-step', '◀ −');
    minus.type = 'button';
    minus.title = 'Move one step back along the axis (←; Shift ten steps, Alt a tenth)';
    minus.addEventListener('click', () => { this.nudge(-1); this._refocus(); });
    const plus = el('button', 'mv-step', '+ ▶');
    plus.type = 'button';
    plus.title = 'Move one step along the axis (→; Shift ten steps, Alt a tenth)';
    plus.addEventListener('click', () => { this.nudge(1); this._refocus(); });
    this._stepSelect = el('select', 'mv-size');
    for (const s of STEPS) this._stepSelect.append(new Option(`${s} µm`, String(s)));
    this._stepSelect.title = 'How far a step moves in X and Y. A step in Z is always one section.';
    this._stepSelect.addEventListener('change', () => { this.setStep(Number(this._stepSelect.value)); this._refocus(); });
    steps.append(minus, this._stepSelect, plus);
    controls.append(axes, this._note, steps);
    body.append(this._gizmo, controls);

    // What a step moves: the chosen annotation (⌘/Ctrl-click adds others), its section, or a run of sections.
    const scope = el('div', 'mv-scope');
    const scopeLabel = el('span', 'mv-scope-label', 'Move');
    scope.append(scopeLabel);
    this._scopeButtons = new Map();
    for (const [key, text, help] of [
      ['one', 'This one', 'The chosen annotation, and any you add or take out with ⌘-click (Ctrl-click on Windows) on a ring'],
      ['section', 'Its section', 'Every annotation of the cell on the chosen one\'s section: a section that is off as a whole'],
      ['range', 'Sections', 'Every annotation of the cell on the sections from and to (both included)'],
    ]) {
      const b = el('button', 'mv-scope-btn', text);
      b.type = 'button';
      b.title = help;
      b.addEventListener('click', () => { this.setScope(key); this._refocus(); });
      this._scopeButtons.set(key, b);
      scope.append(b);
    }
    this._from = el('input', 'mv-sec');
    this._to = el('input', 'mv-sec');
    for (const [box, label] of [[this._from, 'From section'], [this._to, 'To section']]) {
      box.type = 'number';
      box.step = '1';
      box.title = label;
      box.setAttribute('aria-label', label);
      box.addEventListener('change', () => {
        this.setRange(Number(this._from.value), Number(this._to.value));
        this._refocus();
      });
    }
    this._redBlue = el('button', 'mv-scope-btn', 'Red to blue');
    this._redBlue.type = 'button';
    this._redBlue.title = 'The sections from the blue marker to the red one (↑/↓ place them first)';
    this._redBlue.addEventListener('click', () => {
      const r = this.markerRange();
      if (r) this.setRange(r.from, r.to);
      this._refocus();
    });
    this._rangeBox = el('span', 'mv-range');
    this._rangeBox.append(this._from, el('span', '', '–'), this._to, this._redBlue);
    scope.append(this._rangeBox);
    this._count = el('div', 'mv-count');

    const foot = el('div', 'mv-foot');
    this._apply = el('button', 'mv-apply primary', 'Apply to render ⏎');
    this._apply.type = 'button';
    this._apply.title = 'Draw the cell again with its moves (Enter): the server moves its copy of the cell; Viking is not changed';
    this._apply.addEventListener('click', () => { this.applyPending(); this._refocus(); });
    this._undo = el('button', 'mv-undo', 'Undo this move');
    this._undo.type = 'button';
    this._undo.title = 'Put this annotation back where the correction drew it';
    this._undo.addEventListener('click', () => {
      const t = this.target();
      if (t) this.remove(t.record, this.members(t));
      this._refocus();
    });
    this._kept = el('div', 'mv-kept');
    foot.append(this._apply, this._undo);
    host.append(head, this._where, this._moved, scope, this._count, body, foot, this._kept);
    host.hidden = true;
  }

  _refocus() {
    try { this.renderer.webgl?.domElement?.focus?.({ preventScroll: true }); } catch { /* none */ }
  }

  _renderPanel() {
    const host = this.dom?.panel;
    if (!host) return;
    const t = this.active ? this.target() : null;
    host.hidden = !t;
    if (!t) return;
    const { record, item, slot } = t;
    const tag = this.tagOf(record);
    const nodes = this.cachedNodes(record);
    const node = nodes?.find((n) => n.id === item.locationId) ?? null;
    const store = this.storeOf(record);
    const move = movesOf(store, tag)[String(item.locationId)] ?? { dx: 0, dy: 0, dz: 0 };
    const thickness = sectionUm(record);
    host.style.setProperty('--mv', this.renderer.markerColor?.(slot) ?? '#ffc107');
    this._title.textContent = `Move annotation ${item.locationId} · c${record.id} (${SLOT_WORDS[slot]})`;
    if (node) {
      const from = correctedXyz(node);
      const at = [from[0] + move.dx, from[1] + move.dy, from[2] + move.dz * thickness];
      const viking = Number.isFinite(node.manual_dz) ? node.z_section - node.manual_dz : node.z_section;
      const section = viking + move.dz;
      this._where.textContent = `X ${at[0].toFixed(2)}  Y ${at[1].toFixed(2)}  Z ${at[2].toFixed(2)} µm`
        + ` · section ${section}${move.dz ? ` (Viking ${viking})` : ''}`;
    } else {
      this._where.textContent = 'Loading the annotation…';
    }
    const pending = node && !sameMove(move, appliedMove(node));
    this._moved.textContent = `Moved ΔX ${signedMove(move.dx)}  ΔY ${signedMove(move.dy)} µm · ΔZ ${signedMove(move.dz, 0)}`
      + ` section${Math.abs(move.dz) === 1 ? '' : 's'}${pending ? ' · not yet in the render' : ''}`;
    this._moved.classList.toggle('pending', !!pending);
    for (const [key, b] of this._axisButtons) b.classList.toggle('on', key === this.axis);
    for (const [key, b] of this._scopeButtons) b.classList.toggle('on', key === this.scope);
    this._rangeBox.hidden = this.scope !== 'range';
    if (this.range) {
      if (document.activeElement !== this._from) this._from.value = String(this.range.from);
      if (document.activeElement !== this._to) this._to.value = String(this.range.to);
    }
    this._redBlue.disabled = !this.markerRange();
    const members = this.members(t);
    const extra = this.extra.get(item.key)?.size ?? 0;
    const where = this.scope === 'section' ? ` on section ${this._ownSection()?.from ?? '?'}`
      : this.scope === 'range' && this.range ? ` on sections ${this.range.from}–${this.range.to}` : '';
    this._count.textContent = `Moving ${members.length} annotation${members.length === 1 ? '' : 's'}${where}`
      + `${extra ? ` (${extra} added by ⌘/Ctrl-click)` : ''} · ⌘/Ctrl-click a ring to add or take one out`;
    if (document.activeElement !== this._stepSelect) this._stepSelect.value = String(this.step);
    this._note.textContent = `${this._axisLabel()}: ${axisNote(this.axis, this.step, {
      frame: this.renderer.viewAxes?.(), shift: this._shiftOf(record, node),
    })}`;
    const anyPending = [...this.state.neurons.values()].some((r) => this.pendingOf(r).length);
    this._apply.disabled = !anyPending || !!this.applying;
    const set = movesOf(store, tag);
    this._undo.disabled = !members.some((id) => set[String(id)]);
    this._undo.textContent = members.length > 1 ? `Undo these ${members.length} moves` : 'Undo this move';
    const counts = moveCounts(store, tag);
    this._kept.textContent = `${counts.here} move${counts.here === 1 ? '' : 's'} kept for c${record.id} under `
      + `${tagWords(tag)}. Kept in this page and its saved sessions; never written to Viking.`;
    this._drawGizmo();
  }

  /** The volume's X, Y and Z as the screen sees them, and the axis chosen. */
  _drawGizmo() {
    const svg = this._gizmo;
    if (!svg || this.dom?.panel?.hidden) return;
    const frame = this.renderer.viewAxes?.();
    svg.replaceChildren();
    if (!frame) return;
    const add = (name, attrs, text) => {
      const e = document.createElementNS(SVG, name);
      for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
      if (text !== undefined) e.textContent = text;
      svg.append(e);
      return e;
    };
    const c = 38;
    const len = 26;
    add('rect', { x: 1, y: 1, width: 74, height: 74, rx: 6, class: 'mv-gz-frame' });
    // The screen's own axes: Xp right, Yp up; Zp out of the screen, a dot.
    add('text', { x: 72, y: 73, 'text-anchor': 'end', class: 'mv-gz-screen' }, 'Xp→');
    add('text', { x: 4, y: 11, class: 'mv-gz-screen' }, '↑Yp');
    const dot = (v) => [v[0] * frame.right[0] + v[1] * frame.right[1] + v[2] * frame.right[2],
      v[0] * frame.up[0] + v[1] * frame.up[1] + v[2] * frame.up[2]];
    const chosen = this.axis;
    for (const [axis, v, cls] of [['x', [1, 0, 0], 'x'], ['y', [0, 1, 0], 'y'], ['z', [0, 0, 1], 'z']]) {
      const [sx, sy] = dot(v);
      const x = c + sx * len;
      const y = c - sy * len;
      add('line', { x1: c, y1: c, x2: x, y2: y, class: `mv-gz-axis mv-gz-${cls}${chosen === axis ? ' on' : ''}` });
      add('text', { x: c + sx * (len + 7), y: c - sy * (len + 7) + 3, 'text-anchor': 'middle',
        class: `mv-gz-label mv-gz-${cls}` }, axis.toUpperCase());
    }
    add('circle', { cx: c, cy: c, r: 2.2, class: 'mv-gz-origin' });
  }

  // -- the cell panel's section ----------------------------------------------------------

  /** The summary beside the section's name: "3 · 1 not applied", or "none". */
  summary(record) {
    const tag = this.tagOf(record);
    const n = Object.keys(movesOf(this.storeOf(record), tag)).length;
    if (!n) return 'none';
    const pending = this.pendingOf(record).length;
    return `${n}${pending ? ` · ${pending} not applied` : ''}`;
  }

  /**
   * The Manual moves section: Show moves, the setting the moves are kept under (and how
   * many the other settings hold), a row a move -- tick to choose, click the Location ID
   * to select it -- and Move, Delete chosen, Reset all, Apply.  `helpers.showToggle` is
   * app.js's checkbox maker; `apply(changes)` sets the cell's display.
   */
  buildSection(body, record, { showToggle, apply }) {
    const tag = this.tagOf(record);
    const store = this.storeOf(record);
    const nodes = this.cachedNodes(record);
    if (!nodes) this.nodesFor(record).then(() => this.hooks.renderNeuronList(), () => {});
    showToggle(body, 'Show moves', record.display?.showMoves !== false,
      (on) => Promise.resolve(apply({ showMoves: on })).then(() => this.update()),
      'An arrow for each annotation moved by hand, from where Viking\'s correction drew it to where '
      + 'it was moved, at true scale. The chosen ones are drawn in their marker\'s color.');
    const counts = moveCounts(store, tag);
    const under = document.createElement('div');
    under.className = 'mv-under';
    under.textContent = tag ? `Under ${tagWords(tag)}` : 'Not loaded with a setting';
    if (counts.elsewhere) {
      under.textContent += `; ${counts.elsewhere} more under ${counts.other.map(([t, n]) => `${tagWords(t)} (${n})`)
        .join(', ')}, applied when the cell is drawn with that setting`;
    }
    body.append(under);

    const rows = moveRows(store, tag, nodes || []);
    const chosen = new Set();
    const actions = document.createElement('div');
    actions.className = 'mv-actions';
    const button = (text, title, onClick, cls = '') => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = text;
      b.title = title;
      if (cls) b.className = cls;
      b.addEventListener('click', onClick);
      actions.append(b);
      return b;
    };
    button('Move…', 'Move the selected annotation by hand (also g, with an annotation selected): '
      + '←/→ along an axis, 1–7 choose the axis', () => this.start());
    const del = button('Delete chosen', 'Delete the ticked moves: those annotations go back where the correction drew them',
      () => this.remove(record, [...chosen]));
    const reset = button('Reset all', `Delete every move of c${record.id} under this setting (moves under other settings stay)`,
      () => {
        if (window.confirm(`Delete all ${rows.length} manual move(s) of c${record.id} under ${tagWords(tag)}?`)) {
          this.reset(record);
        }
      });
    const pending = this.pendingOf(record).length;
    const applyButton = button(pending ? `Apply ${pending}` : 'Apply', 'Draw the cell again with its moves',
      () => this.apply(record), pending ? 'primary' : '');
    applyButton.disabled = !pending;
    del.disabled = true;
    reset.disabled = !rows.length;

    if (rows.length) {
      const table = document.createElement('table');
      table.className = 'mv-table';
      const head = document.createElement('tr');
      const all = document.createElement('input');
      all.type = 'checkbox';
      all.title = 'Tick every move';
      const boxes = [];
      let lastTicked = -1;
      const tick = (index, on) => {
        const [box, id] = boxes[index];
        box.checked = on;
        if (on) chosen.add(id); else chosen.delete(id);
      };
      const sync = () => {
        del.disabled = !chosen.size;
        del.textContent = chosen.size ? `Delete ${chosen.size}` : 'Delete chosen';
        all.checked = chosen.size === rows.length;
        all.indeterminate = chosen.size > 0 && chosen.size < rows.length;
      };
      all.addEventListener('change', () => {
        for (const [box, id] of boxes) {
          box.checked = all.checked;
          if (all.checked) chosen.add(id); else chosen.delete(id);
        }
        sync();
      });
      const th0 = document.createElement('th');
      th0.append(all);
      head.append(th0);
      for (const [text, help] of [['ID', 'Viking\'s Location ID. Click: select it and look at it.'],
        ['Sec', 'The section it is drawn on (Viking\'s, when moved in Z, in the tooltip). Click a section: tick every move on it.'],
        ['ΔX', 'X move, µm'], ['ΔY', 'Y move, µm'], ['ΔZ', 'Z move, sections']]) {
        const th = document.createElement('th');
        th.textContent = text;
        th.title = help;
        head.append(th);
      }
      head.append(document.createElement('th'));
      const thead = document.createElement('thead');
      thead.append(head);
      const tbody = document.createElement('tbody');
      for (const row of rows) {
        const tr = document.createElement('tr');
        if (!row.found) tr.className = 'mv-gone';
        const cell = (text, cls = 'mv-num') => {
          const td = document.createElement('td');
          td.className = cls;
          td.textContent = text;
          tr.append(td);
          return td;
        };
        const td0 = document.createElement('td');
        const box = document.createElement('input');
        box.type = 'checkbox';
        const index = boxes.length;
        box.title = 'Tick to choose; Shift-click ticks every row from the last one ticked';
        box.addEventListener('click', (event) => {
          // Shift: the rows between this one and the last one ticked take this one's state.
          if (event.shiftKey && lastTicked >= 0 && lastTicked !== index) {
            const [a, b] = lastTicked < index ? [lastTicked, index] : [index, lastTicked];
            for (let i = a; i <= b; i += 1) tick(i, box.checked);
          } else {
            tick(index, box.checked);
          }
          lastTicked = index;
          sync();
        });
        boxes.push([box, row.id, row.section]);
        td0.append(box);
        tr.append(td0);
        const idCell = document.createElement('td');
        const link = document.createElement('button');
        link.type = 'button';
        link.className = 'mv-id';
        link.textContent = String(row.id);
        link.title = row.found ? `Select annotation ${row.id} and look at it` : 'No longer in the cell (deleted in Viking?)';
        link.disabled = !row.found;
        link.addEventListener('click', () => this.hooks.fly(record, row.id));
        idCell.append(link);
        tr.append(idCell);
        const sec = cell(row.section ?? '—');
        if (row.section !== null) {
          // Every move on this section at once (a section moved as a whole); again: none of them.
          sec.classList.add('mv-secpick');
          sec.title = `Tick every move on section ${row.section}${row.dz && row.vikingSection !== null
            ? ` (Viking's section ${row.vikingSection})` : ''}`;
          sec.addEventListener('click', () => {
            const on = boxes.filter(([, , s]) => s === row.section);
            const all = on.every(([b]) => b.checked);
            boxes.forEach(([, , s], i) => { if (s === row.section) tick(i, !all); });
            sync();
          });
        }
        cell(signedMove(row.dx));
        cell(signedMove(row.dy));
        cell(signedMove(row.dz, 0));
        tr.title = `Annotation ${row.id}: ${row.um.toFixed(3)} µm in X and Y`
          + `${row.dz ? `, ${Math.abs(row.dz)} section${Math.abs(row.dz) === 1 ? '' : 's'} ${row.dz > 0 ? 'up' : 'down'} in Z` : ''}`;
        const x = document.createElement('td');
        const kill = document.createElement('button');
        kill.type = 'button';
        kill.className = 'mv-del';
        kill.textContent = '✕';
        kill.title = `Delete the move of ${row.id}`;
        kill.addEventListener('click', () => this.remove(record, [row.id]));
        x.append(kill);
        tr.append(x);
        tbody.append(tr);
      }
      table.append(thead, tbody);
      const wrap = document.createElement('div');
      wrap.className = 'mv-table-wrap';
      wrap.append(table);
      body.append(wrap);
    } else {
      const none = document.createElement('div');
      none.className = 'mv-none';
      none.textContent = 'No annotation of this cell is moved by hand under this setting.';
      body.append(none);
    }
    body.append(actions);
  }
}
