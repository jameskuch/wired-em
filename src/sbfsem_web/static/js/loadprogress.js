/**
 * The load strip: how far a load has got, said over the viewport.
 *
 * Asked for in #ai-requests on 2026-09-26 ("bar one"): someone who follows a
 * link from Viking, or asks for 207 ON bipolars, should see at once that the
 * request arrived, which cells are in hand, and how many are done -- not a
 * 12px counter in the status corner.  Loads completed out of loads
 * requested, with the cells in flight named and staged, for the whole load.
 *
 * Several loads may overlap (a second paste while the first runs, a link
 * arriving during a restore); the strip sums them, and goes away a moment
 * after the last one ends.  The status corner keeps its own final report.
 *
 * Stop (asked for in #ai-requests on 2026-10-14: "how do I cancel the
 * generation of the entire batch?") stops every load the strip shows: each
 * run's `onStop` drops what the load has not drawn, and a stopped run
 * starts no further cells.
 */

import { progressText } from './api.js';

const LINGER_MS = 2500;
const LINGER_FAILED_MS = 6000;

/** What the poll's state means to someone watching, for one cell. */
export function stageText(job) {
  if (!job) return 'building';
  // A finished product on its way down (api.js pollJob): a surface of millions
  // of triangles can take a minute on a slow link, and is no longer building.
  const down = job.downloading;
  if (down) {
    const what = job.kind === 'mesh' ? 'the surface' : 'the result';
    const got = progressText(down.received, down.total);
    if (down.broke && job.reconnecting) return `downloading ${what} broke off at ${got}, asking again`;
    return `downloading ${what}, ${got}`;
  }
  // The follow lost the server (api.js pollJob keeps trying for a minute): say
  // so, rather than leave the last stage and clock frozen on screen.
  if (job.reconnecting) {
    const silent = Math.round(job.reconnectSeconds || 0);
    return silent >= 1
      ? `no answer from the server for ${silent} s, retrying` : 'no answer from the server, retrying';
  }
  if (job.status === 'queued') {
    return job.waiting === 'memory' ? 'waiting for server memory' : 'queued on the server';
  }
  if (job.status === 'running') {
    // The build's own stage when it has reported one (sbfsem.progress):
    // "contouring 412 of 1,830 · 14 s"; until then just the seconds.
    const p = job.progress;
    let text = 'building';
    if (p && p.stage) {
      text = p.stage;
      // A count needs both numbers; the server may send either as null.
      if (p.total && Number.isFinite(p.done)) {
        text += ` ${p.done.toLocaleString()} of ${p.total.toLocaleString()}`;
      }
    }
    return job.seconds >= 1 ? `${text} · ${Math.round(job.seconds)} s` : text;
  }
  return 'building';
}

export class LoadProgress {
  /** @param {HTMLElement} root  the strip's container, hidden while idle */
  constructor(root) {
    this.root = root;
    this.title = root.querySelector('.load-title');
    this.bar = root.querySelector('progress');
    this.list = root.querySelector('.load-cells');
    this.stopButton = root.querySelector('.load-stop');
    this.runs = new Set();
    this.timer = null;
    this.stopButton?.addEventListener('click', () => this.stop());
  }

  /** Stop every load in progress. */
  stop() {
    for (const run of this.runs) run.stop();
  }

  /**
   * Begin a load of `total` cells.  Returns the run, whose `cell(id, key)` gives
   * a handle per cell: `name(label)`, `stage(text)`, `done(ok, { stopped })`.
   * `key` is what tells two cells apart (the app passes `source/id`): a numeric
   * ID is numbered per volume, so a run holding c1 of two volumes tracked one of
   * them, counted one done of two and never completed.  It defaults to the ID.
   * `run.stopped` turns true when Stop is pressed, after `onStop` has run;
   * a cell the stop dropped reports `done(false, { stopped: true })`.
   */
  begin(total, { verb = 'Loading', onStop = null } = {}) {
    // A load that starts while the last one's result lingers starts afresh.
    if ([...this.runs].every((r) => r.ended)) this.runs.clear();
    const run = {
      total, done: 0, failed: 0, dropped: 0, verb, cells: new Map(), ended: false, stopped: false,
    };
    this.runs.add(run);
    clearTimeout(this.timer);
    this.timer = null;
    const strip = this;
    run.cell = (id, key = id) => {
      const entry = { id, label: '', stage: 'fetching annotations' };
      run.cells.set(key, entry);
      strip.draw();
      return {
        name(label) { entry.label = label || ''; strip.draw(); },
        stage(text) {
          if (entry.stage !== text) { entry.stage = text; strip.draw(); }
        },
        done(ok = true, { stopped = false } = {}) {
          if (!run.cells.delete(key)) return;
          run.done += 1;
          if (stopped) run.dropped += 1;
          else if (!ok) run.failed += 1;
          strip.draw();
        },
      };
    };
    run.stop = () => {
      if (run.ended || run.stopped) return;
      run.stopped = true;
      onStop?.();
      // What is still fetching or building is dropped now, not when its
      // request comes back: an annotation fetch ran on for more than 15 s
      // after Stop in testing.  A cell already drawing its surface is placed,
      // and finishes.
      for (const [key, entry] of run.cells) {
        if (entry.stage === 'drawing') continue;
        run.cells.delete(key);
        run.done += 1;
        run.dropped += 1;
      }
      strip.draw();
    };
    run.end = () => {
      run.ended = true;
      run.cells.clear();
      strip.draw();
    };
    this.draw();
    return run;
  }

  draw() {
    const runs = [...this.runs];
    const total = runs.reduce((n, r) => n + r.total, 0);
    const done = runs.reduce((n, r) => n + r.done, 0);
    const failed = runs.reduce((n, r) => n + r.failed, 0);
    const dropped = runs.reduce((n, r) => n + r.dropped, 0);
    const stopped = runs.some((r) => r.stopped);
    // A stopped run with nothing left drawing is over, whatever its requests
    // still in the air (their answers are dropped when they come).
    const live = runs.filter((r) => !r.ended && !(r.stopped && !r.cells.size));
    const cells = live.flatMap((r) => [...r.cells.values()]);

    if (!live.length) {
      this.finish(total, done - dropped, failed, stopped);
      return;
    }
    this.root.hidden = false;
    this.root.classList.remove('finished', 'has-failures');
    if (this.stopButton) this.stopButton.hidden = live.every((r) => r.stopped);
    const verb = live.length === 1 ? live[0].verb : 'Loading';
    if (live.every((r) => r.stopped)) {
      this.title.textContent = `Stopping · ${done - dropped - failed} of ${total} cells loaded`;
    } else if (total === 1) {
      const only = cells[0];
      this.title.textContent = only
        ? `${verb} c${only.id}${only.label ? ` ${only.label}` : ''}` : `${verb} 1 cell`;
    } else {
      this.title.textContent = `${verb} ${done} of ${total} cells`
        + (failed ? ` · ${failed} failed` : '');
    }
    // A single cell's bar would sit at 0 until it jumped to 1: the stage
    // line says more, so only a batch shows one.
    this.bar.hidden = total === 1;
    this.bar.max = Math.max(1, total);
    this.bar.value = done;
    this.bar.setAttribute('aria-valuetext', `${done} of ${total} cells loaded`);
    this.list.replaceChildren(...cells.map((c) => {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.className = 'load-name';
      name.textContent = total === 1 ? '' : `c${c.id}${c.label ? ` ${c.label}` : ''}`;
      const stage = document.createElement('span');
      stage.className = 'load-stage';
      stage.textContent = c.stage;
      li.append(name, stage);
      return li;
    }));
  }

  /** The final report: `done` counts the cells that finished, failed or not. */
  finish(total, done, failed, stopped = false) {
    if (this.stopButton) this.stopButton.hidden = true;
    if (!total) { this.root.hidden = true; return; }
    this.root.hidden = false;
    this.root.classList.add('finished');
    this.root.classList.toggle('has-failures', failed > 0);
    const ok = done - failed;
    if (stopped) {
      this.title.textContent = `Stopped · ${ok} of ${total} cells loaded`
        + (failed ? ` · ${failed} failed` : '');
    } else {
      this.title.textContent = failed
        ? `Loaded ${ok} of ${total} cells · ${failed} failed`
        : `Loaded ${total === 1 ? '1 cell' : `${total} cells`}`;
    }
    this.bar.hidden = total === 1;
    this.bar.max = Math.max(1, total);
    this.bar.value = done;
    this.list.replaceChildren();
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.runs.clear();
      this.root.hidden = true;
      this.timer = null;
    }, failed ? LINGER_FAILED_MS : LINGER_MS);
  }
}
