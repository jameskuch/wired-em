// Recording the 3D view to a video file.
//
// The canvas is captured directly (`captureStream`), so what lands in the
// file is exactly what was on screen -- no re-render at a different size, and
// no dependency on an encoder the browser does not have. Two modes: FREE
// records while the user orbits by hand and stops when they say so; AUTO
// spins the scene about a chosen viewport axis for a fixed duration, which is
// what a figure-quality turntable needs.

/**
 * Container/codec candidates, best first within each family.
 *
 * MP4 leads because a MediaRecorder WebM carries no duration in its header
 * (a long-standing Chromium quirk): it plays, but seeking and "how long is
 * this?" are broken in most editors, which is exactly what a figure video
 * gets dropped into.
 */
const FORMATS = {
  mp4: [
    'video/mp4;codecs=avc1.640028',
    'video/mp4;codecs=avc1',
    'video/mp4',
  ],
  webm: [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm',
  ],
};

/** Bits per second by quality name, at 1080p-ish canvas sizes. */
export const QUALITY_BITRATES = {
  low: 2_500_000,
  medium: 8_000_000,
  high: 16_000_000,
  max: 40_000_000,
};

/** The first supported mime type for a family, or null when none is. */
export function pickMimeType(family) {
  const candidates = FORMATS[family] || FORMATS.webm;
  for (const type of candidates) {
    if (window.MediaRecorder?.isTypeSupported?.(type)) return type;
  }
  return null;
}

/** Which families this browser can actually write. */
export function supportedFormats() {
  return Object.keys(FORMATS).filter((f) => pickMimeType(f));
}

export class ViewRecorder {
  /**
   * @param {Renderer3D} renderer the view to record
   * @param {(state) => void} onState progress callback: {recording, elapsed, total}
   */
  constructor(renderer, onState = () => {}) {
    this.renderer = renderer;
    this.onState = onState;
    this.recorder = null;
    this.chunks = [];
    this._spin = null;
    this._tick = null;
    this._startedAt = 0;
    this._total = 0;
  }

  get recording() {
    return !!this.recorder && this.recorder.state === 'recording';
  }

  /**
   * Start recording.
   *
   * `mode` is 'free' (record until stop) or 'auto' (spin, then stop by
   * itself). `axis` picks the spin axis in VIEWPORT terms: 'y' turntable
   * (the usual), 'x' tumble, 'roll' about the view axis. `turns` is how many
   * full revolutions the auto spin makes over `seconds`.
   */
  start({
    mode = 'free', seconds = 10, fps = 30, quality = 'high', format = 'webm',
    axis = 'y', turns = 1,
  } = {}) {
    if (this.recording) return null;
    const mime = pickMimeType(format);
    if (!mime) throw new Error(`This browser cannot record ${format}`);

    const canvas = this.renderer.webgl.domElement;
    // The capture is fixed at the canvas's size the moment the stream opens,
    // so make sure that size is the one on screen: recording a canvas that
    // has not been laid out yet yields a 300x150 postage stamp of a file.
    this.renderer._resize();
    if (!canvas.width || !canvas.height) {
      throw new Error('The 3D view has no size yet — open the render tab first');
    }
    // captureStream(0) is frame-on-demand: we hand the encoder a frame right
    // after each draw, rather than trusting the compositor to notice that a
    // WebGL canvas changed (which it does not do reliably -- an automatic
    // stream can record a whole take of empty frames). Falls back to the
    // timed stream where requestFrame is unavailable.
    const stream = canvas.captureStream(0);
    const track = stream.getVideoTracks()[0];
    this._frameTrack = typeof track?.requestFrame === 'function' ? track : null;
    if (!this._frameTrack) {
      // Safari has captureStream but no requestFrame. The compositor then
      // decides when a frame is taken, and it only takes one from a WebGL
      // canvas that is actively being drawn -- so the pump still runs, it
      // just redraws instead of pushing.
      stream.getTracks().forEach((t) => t.stop());
      return this._startWith(canvas.captureStream(fps),
        { mode, seconds, fps, quality, format, axis, turns });
    }
    return this._startWith(stream, { mode, seconds, fps, quality, format, axis, turns });
  }

  _startWith(stream, { mode, seconds, fps, quality, format, axis, turns }) {
    const mime = pickMimeType(format);
    this.chunks = [];
    this.recorder = new MediaRecorder(stream, {
      mimeType: mime,
      videoBitsPerSecond: QUALITY_BITRATES[quality] ?? QUALITY_BITRATES.high,
    });
    this.recorder.ondataavailable = (e) => {
      if (e.data && e.data.size) this.chunks.push(e.data);
    };
    this.mime = mime;
    this.extension = mime.startsWith('video/mp4') ? 'mp4' : 'webm';

    this._done = new Promise((resolve) => {
      this.recorder.onstop = () => {
        const blob = new Blob(this.chunks, { type: this.mime });
        this._stopSpin();
        this.recorder = null;
        this.onState({ recording: false, elapsed: 0, total: 0 });
        resolve(blob);
      };
    });

    this.recorder.start(200);
    this._startedAt = performance.now();
    this._total = mode === 'auto' ? seconds : 0;
    this._tick = setInterval(() => {
      this.onState({
        recording: true,
        elapsed: (performance.now() - this._startedAt) / 1000,
        total: this._total,
      });
    }, 200);

    this._startFramePump(fps);
    if (mode === 'auto') this._startSpin({ seconds, axis, turns });
    return this._done;
  }

  /**
   * Hand the encoder one frame per output frame interval.
   *
   * Rendering explicitly here rather than waiting on the scene's own loop:
   * `requestFrame` captures whatever is in the drawing buffer *now*, so the
   * draw has to have happened on this tick or the take stutters.
   *
   * On a timer, not requestAnimationFrame: rAF follows the display refresh
   * (and stops entirely in a background tab or an unfocused embedded view),
   * where a recording needs exactly `fps` frames a second regardless of who
   * is watching. Driving capture from rAF produced files with no frames.
   */
  _startFramePump(fps) {
    const interval = 1000 / Math.max(1, Math.min(120, fps));
    this._pump = setInterval(() => {
      if (!this.recording) return;
      // The redraw matters in both modes: with requestFrame it is the frame
      // being pushed, and without it, it is what gives the compositor
      // something new to sample.
      this.renderer.webgl.render(this.renderer.scene, this.renderer.camera);
      this._frameTrack?.requestFrame();
    }, interval);
  }

  /**
   * Spin the view for the recording.
   *
   * Driven by wall clock rather than a fixed per-frame step: a frame the GPU
   * misses would otherwise shorten the turn, and a turntable that does not
   * come back to its start is obvious in a loop.
   */
  _startSpin({ seconds, axis, turns }) {
    const total = Math.max(0.5, seconds) * 1000;
    const sweep = 2 * Math.PI * (Number(turns) || 1);
    const started = performance.now();
    let last = 0;
    // Timer-driven for the same reason as the frame pump, and stepped from
    // the wall clock so a slow frame shortens no part of the turn: a
    // turntable that does not come back to its start is obvious in a loop.
    this._spin = setInterval(() => {
      const t = Math.min(1, (performance.now() - started) / total);
      const angle = sweep * t - last;
      last = sweep * t;
      if (axis === 'roll') this.renderer.roll(angle);
      else this.renderer.orbitViewAxis(axis === 'x' ? 'x' : 'y', angle);
      if (t >= 1) this.stop();
    }, 1000 / 60);
    // Belt and braces: a throttled timer still ends the take, so the user
    // always gets their file back.
    this._deadline = setTimeout(() => this.stop(), total + 1500);
  }

  _stopSpin() {
    if (this._spin) clearInterval(this._spin);
    this._spin = null;
    if (this._pump) clearInterval(this._pump);
    this._pump = null;
    if (this._tick) clearInterval(this._tick);
    this._tick = null;
    if (this._deadline) clearTimeout(this._deadline);
    this._deadline = null;
  }

  /** Stop and resolve the promise returned by `start` with the video blob. */
  stop() {
    this._stopSpin();
    if (this.recorder && this.recorder.state !== 'inactive') this.recorder.stop();
    return this._done;
  }

  /** Hand the finished recording to the user as a download. */
  static save(blob, basename = 'sbfsem') {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${basename}.${blob.type.startsWith('video/mp4') ? 'mp4' : 'webm'}`;
    link.click();
    // Revoked late: Safari reads the blob after the click returns.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}
