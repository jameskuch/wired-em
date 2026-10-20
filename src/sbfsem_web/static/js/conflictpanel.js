// The render panel's warning for settings that will not build as set
// (renderconflicts.js decides; this draws): the controls to change turn black
// with red text, and a row at the top of the panel says why and lists what to
// change, best first.  A suggestion's button puts its settings into the
// controls; nothing is built until Apply.

/** The class a control's field takes while its setting is one to change. */
export const CONFLICT_CLASS = 'conflict';

export class ConflictPanel {
  /**
   * `form` is the render form (renderform.js: `el(key)`, `control(key)`), `mount`
   * the panel the row goes at the top of, `onPick(set)` what a suggestion's
   * button does with the options it changes.
   */
  constructor({ form, mount, onPick }) {
    this.form = form;
    this.onPick = onPick;
    this.marked = new Set();
    this.drawn = '';
    this.box = document.createElement('div');
    this.box.id = 'render-conflict';
    this.box.className = 'render-conflict';
    this.box.setAttribute('role', 'alert');
    this.box.hidden = true;
    mount.prepend(this.box);
  }

  /** Mark and explain `conflict` (renderConflicts' answer), or clear it all for null. */
  show(conflict) {
    const keys = new Set(conflict?.keys || []);
    for (const key of this.marked) {
      if (keys.has(key)) continue;
      this.form.el(key)?.classList.remove(CONFLICT_CLASS);
      this.form.control(key)?.removeAttribute('aria-invalid');
    }
    this.marked = new Set();
    for (const key of keys) {
      const field = this.form.el(key);
      if (!field) continue;
      field.classList.add(CONFLICT_CLASS);
      this.form.control(key)?.setAttribute('aria-invalid', 'true');
      this.marked.add(key);
    }
    // Drawn again only when it says something else: the panel refreshes on every
    // keystroke, and a row rebuilt under the pointer would lose its hover and focus.
    const drawn = conflict ? JSON.stringify(conflict) : '';
    if (drawn === this.drawn) return;
    this.drawn = drawn;
    this.box.replaceChildren();
    this.box.hidden = !conflict;
    if (!conflict) return;

    const head = document.createElement('div');
    head.className = 'render-conflict-head';
    const title = document.createElement('strong');
    title.textContent = `⚠ ${conflict.headline}.`;
    head.append(title);
    for (const reason of conflict.reasons) {
      const why = document.createElement('span');
      why.className = 'render-conflict-why';
      why.textContent = ` ${reason}.`;
      head.append(why);
    }
    this.box.append(head);

    if (conflict.suggestions.length) {
      const list = document.createElement('ol');
      list.className = 'render-conflict-fixes';
      list.setAttribute('aria-label', 'Ways to fix it, best first');
      for (const s of conflict.suggestions) {
        const item = document.createElement('li');
        const use = document.createElement('button');
        use.type = 'button';
        use.className = s.best ? 'render-conflict-use best' : 'render-conflict-use';
        use.textContent = s.text;
        use.title = 'Put this into the controls (Apply builds it)';
        use.addEventListener('click', () => this.onPick(s.set));
        const detail = document.createElement('span');
        detail.className = 'render-conflict-detail';
        detail.textContent = ` ${s.best ? '(best) ' : ''}${s.detail}`;
        item.append(use, detail);
        list.append(item);
      }
      this.box.append(list);
    }
    if (conflict.note) {
      const note = document.createElement('div');
      note.className = 'render-conflict-note';
      note.textContent = conflict.note;
      this.box.append(note);
    }
  }
}
