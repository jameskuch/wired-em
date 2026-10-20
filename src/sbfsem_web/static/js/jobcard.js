/**
 * A progress card and an error banner for one piece of server work, laid over
 * whatever element hosts it (a compare pane's viewport today; any render layer
 * tomorrow -- nothing here knows about panes).
 *
 * The card says what is happening (a title), how far it has got (the stage the
 * server reports, with "N of M" when it counts, a bar that is determinate only
 * when it can be), how long it has been (a client clock, so it moves between
 * polls), and, while polls fail, that the server has not answered and the
 * client is retrying.  It has a Stop button.
 *
 * The banner is what a failure looks like: a solid dark-red band with white
 * bold text, the server's message and the stage that was running, Retry and
 * Dismiss.  It stays until dismissed -- the old failure line was 10.5 px red
 * text in a click-through legend, easy to miss and gone with the next redraw.
 *
 * Pure DOM; colors come from the app's CSS variables (app.css, "Job card").
 */

import { stageText } from './loadprogress.js';

/** "41 s", "2 min 05 s". */
export function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

/** A hover explanation in the app's own panel (data-help), after a long pause. */
function help(el, text) {
  el.dataset.help = text;
  el.dataset.helpDelay = '2000';
  return el;
}

export class JobCard {
  /**
   * @param {HTMLElement} host  positioned element to lay the card over
   * @param {object} [options]
   * @param {Function} [options.onStop]  Stop was pressed
   * @param {number} [options.tickMs]  how often the clock redraws (500)
   * @param {Function} [options.focusFallback]  the element to focus when the
   *   card or banner that held keyboard focus goes away (Stop, Retry and
   *   Dismiss all remove the control that was focused); else focus is left
   *   to fall to the page
   */
  constructor(host, { onStop = null, tickMs = 500, focusFallback = null } = {}) {
    this.onStop = onStop;
    this.tickMs = tickMs;
    this.focusFallback = focusFallback;
    this._announced = '';
    this._stopping = false;
    this.job = null;
    this.working = '';
    this.startedAt = 0;
    this.reconnectSince = 0;
    this.timer = null;
    this._retry = null;
    this._dismiss = null;

    // The layer lets every click through to the viewport behind it; only the
    // card and the banner take them.
    this.root = node('div', 'job-layer');

    // A screen reader hears the card through its own live region, which
    // changes only when the work changes phase (never with each count or
    // tick); the card itself is a plain group, so it is not reread whole at
    // every poll.
    this.live = node('div', 'job-announce');
    this.live.setAttribute('role', 'status');
    this.live.setAttribute('aria-live', 'polite');
    this.card = node('div', 'job-card');
    this.card.setAttribute('role', 'group');
    this.card.hidden = true;
    const head = node('div', 'job-head');
    this.title = node('span', 'job-title');
    this.stopButton = node('button', 'job-stop', 'Stop');
    this.stopButton.type = 'button';
    help(this.stopButton, 'Stop this. A job already running on the server cannot be '
      + 'interrupted there: it finishes and its result is kept, so pressing '
      + 'Compare again is then immediate.');
    this.stopButton.addEventListener('click', () => {
      // Not disabled: a disabled button drops keyboard focus.  The label says
      // it is stopping, and a second press does nothing.
      if (this._stopping) return;
      this._stopping = true;
      this.stopButton.textContent = 'Stopping…';
      this.stopButton.setAttribute('aria-disabled', 'true');
      this.onStop?.();
    });
    head.append(this.title, this.stopButton);
    this.stage = node('div', 'job-stage');
    this.bar = node('progress', 'job-bar');
    const meta = node('div', 'job-meta');
    this.elapsed = node('span', 'job-elapsed');
    // The clock redraws twice a second; a screen reader announcing it that
    // often would be noise.
    this.elapsed.setAttribute('aria-hidden', 'true');
    this.retrying = node('span', 'job-retrying');
    meta.append(this.elapsed, this.retrying);
    this.card.append(head, this.stage, this.bar, meta);

    this.banner = node('div', 'job-banner');
    this.banner.setAttribute('role', 'alert');
    this.banner.hidden = true;
    const body = node('div', 'job-banner-body');
    this.bannerTitle = node('div', 'job-banner-title');
    this.bannerMessage = node('div', 'job-banner-message');
    this.bannerStage = node('div', 'job-banner-stage');
    body.append(this.bannerTitle, this.bannerMessage, this.bannerStage);
    const actions = node('div', 'job-banner-actions');
    this.retryButton = node('button', 'job-retry', 'Retry');
    this.retryButton.type = 'button';
    this.retryButton.addEventListener('click', () => this._retry?.());
    this.dismissButton = node('button', 'job-dismiss', 'Dismiss');
    this.dismissButton.type = 'button';
    this.dismissButton.addEventListener('click', () => {
      // The callback is taken before clearError() forgets it.
      const dismissed = this._dismiss;
      this.clearError();
      dismissed?.();
    });
    actions.append(this.retryButton, this.dismissButton);
    this.banner.append(body, actions);

    this.root.append(this.live, this.banner, this.card);
    host.append(this.root);
  }

  /** True while the progress card is up. */
  get running() { return !this.card.hidden; }

  /** True while the failure banner is up. */
  get failed() { return !this.banner.hidden; }

  /**
   * Begin showing progress.  Clears any banner: a new attempt replaces the
   * failure it follows.  `working` is the word for a running job that has not
   * reported a stage ("measuring"); the default is the poll's own "building".
   */
  show({ title, working = '' }) {
    this.clearError();
    this.job = null;
    this.working = working;
    this.reconnectSince = 0;
    this.startedAt = Date.now();
    this.title.textContent = title;
    this.bar.setAttribute('aria-label', title);
    this._stopping = false;
    this.stopButton.removeAttribute('aria-disabled');
    this.stopButton.textContent = 'Stop';
    this._announced = '';
    this.card.hidden = false;
    this._render();
    clearInterval(this.timer);
    this.timer = setInterval(() => this._render(), this.tickMs);
  }

  setTitle(text) {
    if (this.title.textContent !== text) {
      this.title.textContent = text;
      this.bar.setAttribute('aria-label', text);
      this._render();
    }
  }

  /** One poll's answer (a server job, or a "reconnecting" report from pollJob). */
  update(job) {
    if (!this.running) return;
    this.job = job;
    this.reconnectSince = job?.reconnecting
      ? Date.now() - (job.reconnectSeconds || 0) * 1000 : 0;
    this._render();
  }

  /** Take the card down (the work ended, however it ended). */
  hide() {
    clearInterval(this.timer);
    this.timer = null;
    this._releaseFocus();
    this.card.hidden = true;
    this.job = null;
  }

  /**
   * Show a failure.  `error` is what the job call rejected with: its
   * `serverDetail` (else `detail`, else `message`) is the message and its
   * `failedStage` the stage line.  `onRetry` gives the Retry button its work
   * (hidden when absent); `onDismiss` runs after Dismiss.
   */
  fail({ title, error = null, message = '', stage = '', note = '', onRetry = null, onDismiss = null }) {
    this.hide();
    this.bannerTitle.textContent = title;
    this.bannerMessage.textContent = message
      || error?.serverDetail || error?.detail || error?.message || String(error || 'failed');
    const failedStage = stage || error?.failedStage || '';
    this.bannerStage.textContent = [failedStage ? `Failed during ${failedStage}.` : '', note]
      .filter(Boolean).join(' ');
    this.bannerStage.hidden = !this.bannerStage.textContent;
    this._retry = onRetry;
    this._dismiss = onDismiss;
    this.retryButton.hidden = !onRetry;
    this.banner.hidden = false;
  }

  clearError() {
    this._releaseFocus();
    this.banner.hidden = true;
    this._retry = null;
    this._dismiss = null;
  }

  /** The control that had focus is about to be hidden: hand focus to the
   * host's fallback instead of letting it fall to the page. */
  _releaseFocus() {
    const active = typeof document !== 'undefined' ? document.activeElement : null;
    if (active && this.root.contains?.(active)) this.focusFallback?.()?.focus?.();
  }

  /** Remove the card from the page and stop its clock. */
  destroy() {
    this.hide();
    this.root.remove();
  }

  _render() {
    const now = Date.now();
    const job = this.job;
    // The server's seconds are dropped from the stage line: the clock beside
    // it is the client's own and keeps moving between polls.
    // The card has its own "no answer" line below, so the stage line keeps
    // the last stage it knew (and not stageText's reconnect wording).
    let text = job ? stageText({ ...job, seconds: 0, reconnecting: false }) : 'starting';
    if (text === 'building' && this.working && job?.status !== 'queued') text = this.working;
    if (this.stage.textContent !== text) this.stage.textContent = text;

    const p = job?.progress;
    const counted = !!(p && p.total > 0 && Number.isFinite(p.done));
    if (counted) {
      this.bar.max = p.total;
      this.bar.value = Math.min(p.done, p.total);
      this.bar.setAttribute('aria-valuetext', text);
    } else {
      // No value attribute: the browser's own indeterminate bar.  Never a
      // percentage the server did not report.
      this.bar.removeAttribute('value');
      this.bar.removeAttribute('max');
      this.bar.removeAttribute('aria-valuetext');
    }

    this.elapsed.textContent = formatElapsed(now - this.startedAt);
    if (this.reconnectSince) {
      const silent = Math.max(1, Math.round((now - this.reconnectSince) / 1000));
      this.retrying.textContent = `no answer for ${silent} s, retrying`;
    } else {
      this.retrying.textContent = '';
    }
    this.card.classList.toggle('reconnecting', !!this.reconnectSince);

    // Spoken only when the work changes phase: the title, the stage's name
    // (not its counts) and whether the server is answering.
    const stageName = job?.status === 'queued' ? text : (p?.stage || text.replace(/ \d[\d,]* of [\d,]+$/, ''));
    const spoken = `${this.title.textContent}: ${stageName}`
      + (this.reconnectSince ? '. No answer from the server, retrying' : '');
    if (spoken !== this._announced) {
      this._announced = spoken;
      this.live.textContent = spoken;
    }
  }
}
