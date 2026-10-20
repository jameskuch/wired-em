// The render options' hover-help movies (asked for 2026-10-17: "a graphical movie
// representation of what the parameter is related to in the final wireframe/surface
// ... if this value gets bigger this happens to the final render and if this value
// gets smaller this happens").
//
// Each movie is real renders: one piece of a real cell built by the server's own
// geometry code at a sweep of the option's values, drawn as the viewer draws a cell
// (surface and wireframe), by tools/help_movies.py.  Its frames sit side by side in
// one WebP under /static/help/, and /static/help/movies.json says what each frame is:
// the value, the label, the whole build's triangles, seconds and mean distance from
// the annotations, and in words what the two ends of the sweep show.
//
// A render panel field carries `data-help-movie` (its option's key), `data-help-method`
// (the method its form shows) and `data-help-value` (what the option is set to); the
// help pop puts the movie above the words.  Frames cross-fade in a back-and-forth
// loop; with reduced motion the frames are laid out as a still strip instead.
//
// Pure apart from `buildMovie` and `loadMovies`: node tests the rest
// (tests/js/test_helpmovies.mjs).  Nothing a cell or a person typed reaches a movie:
// the manifest is a file of this site, and its text is set as text.

export const MANIFEST_URL = '/static/help/movies.json';
export const IMAGE_BASE = '/static/help/';
/** How long a frame shows, and the cross-fade into the next, in ms. */
export const FRAME_MS = 1500;
export const FADE_MS = 380;

let manifest = null;
let loading = null;

/** The manifest, fetched once; null until it has arrived (or when it cannot). */
export function movieManifest() { return manifest; }

/** Fetch the manifest once.  Resolves to it, or to null when there is none. */
export function loadMovies(fetchFn = globalThis.fetch) {
  if (manifest || !fetchFn) return Promise.resolve(manifest);
  if (!loading) {
    loading = fetchFn(MANIFEST_URL)
      .then((res) => (res.ok ? res.json() : null))
      .then((doc) => { manifest = isManifest(doc) ? doc : null; return manifest; })
      .catch(() => null);
  }
  return loading;
}

/** For the tests: forget what was fetched. */
export function resetMovies(doc = null) { manifest = doc; loading = null; }

function isManifest(doc) {
  return doc !== null && typeof doc === 'object' && !Array.isArray(doc);
}

/**
 * The movie for option `key` under `method`: the method's own (`key@method`) when there
 * is one, else the option's general movie; null when the option has none.
 */
export function movieFor(doc, key, method) {
  if (!doc || !key) return null;
  const own = method ? doc[`${key}@${method}`] : null;
  if (own && validMovie(own)) return own;
  const general = doc[key];
  return general && validMovie(general) ? general : null;
}

/** A movie the page can show: frames, an image of this site, one value and label a frame. */
export function validMovie(m) {
  return Boolean(m) && Number.isInteger(m.frames) && m.frames >= 2
    && typeof m.image === 'string' && /^[\w.@-]+\.webp$/.test(m.image)
    && Array.isArray(m.values) && m.values.length === m.frames
    && Array.isArray(m.labels) && m.labels.length === m.frames
    && Number(m.width) > 0 && Number(m.height) > 0;
}

/** The frames in the order they play: up the sweep and back down, each end once. */
export function frameOrder(n) {
  if (n < 2) return [0];
  const up = Array.from({ length: n }, (_, i) => i);
  return [...up, ...up.slice(1, -1).reverse()];
}

/** The frame whose value is the option's `current` value (the nearest, for a number). */
export function frameOf(movie, current) {
  if (current === undefined || current === null || current === '') return -1;
  const exact = movie.values.findIndex((v) => String(v) === String(current));
  if (exact >= 0) return exact;
  const x = Number(current);
  if (!Number.isFinite(x)) return -1;
  let best = -1;
  let gap = Infinity;
  movie.values.forEach((v, i) => {
    const d = Math.abs(Number(v) - x);
    if (Number.isFinite(d) && d < gap) { gap = d; best = i; }
  });
  return best;
}

const thousands = (n) => Math.round(n).toLocaleString('en-US');

/** The measured line under frame `i`: its triangles, seconds and distance from the annotations. */
export function frameNumbers(movie, i) {
  const parts = [];
  const faces = movie.faces?.[i];
  if (Number.isFinite(faces)) parts.push(`${thousands(faces)} triangles`);
  const seconds = movie.seconds?.[i];
  if (Number.isFinite(seconds)) parts.push(`${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s to build`);
  const dev = movie.deviation_um?.[i];
  if (Number.isFinite(dev)) parts.push(`${dev.toFixed(3)} µm from the annotations`);
  return parts.join(' · ');
}

/** Where frame `i` of `n` sits in the strip, as a background-position percentage. */
export function framePosition(i, n) {
  return n > 1 ? `${(100 * i) / (n - 1)}% 0` : '0 0';
}

// -- the page ---------------------------------------------------------------------------

function div(doc, cls, text) {
  const el = doc.createElement('div');
  el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

/**
 * The movie as an element of `doc`: `{ el, stop }`.  `current` is the option's value
 * now (its tick is marked); `reducedMotion` lays the frames out still.
 */
export function buildMovie(movie, { current = null, doc = globalThis.document, reducedMotion = false } = {}) {
  const n = movie.frames;
  const url = `${IMAGE_BASE}${movie.image}`;
  const root = div(doc, 'help-movie');
  const head = div(doc, 'hm-head');
  head.append(div(doc, 'hm-title', 'What it does to the surface'),
    div(doc, 'hm-view', `${movie.view} · ${movie.technique}`));
  root.append(head);
  const marked = frameOf(movie, current);

  if (reducedMotion) {
    const strip = div(doc, 'hm-strip');
    for (let i = 0; i < n; i += 1) {
      const cell = div(doc, 'hm-thumb');
      const pic = div(doc, 'hm-frame');
      pic.style.backgroundImage = `url("${url}")`;
      pic.style.backgroundSize = `${n * 100}% 100%`;
      pic.style.backgroundPosition = framePosition(i, n);
      pic.style.aspectRatio = `${movie.width} / ${movie.height}`;
      cell.append(pic, div(doc, `hm-thumb-label${i === marked ? ' now' : ''}`, movie.labels[i]));
      strip.append(cell);
    }
    root.append(strip);
    if (movie.less || movie.more) root.append(ends(doc, movie));
    return { el: root, stop() {} };
  }

  const stage = div(doc, 'hm-stage');
  stage.style.aspectRatio = `${movie.width} / ${movie.height}`;
  const layers = [div(doc, 'hm-frame'), div(doc, 'hm-frame')];
  for (const layer of layers) {
    layer.style.backgroundImage = `url("${url}")`;
    layer.style.backgroundSize = `${n * 100}% 100%`;
    layer.style.transition = `opacity ${FADE_MS}ms ease`;
    stage.append(layer);
  }
  const badge = div(doc, 'hm-value');
  stage.append(badge);
  const scale = div(doc, 'hm-scale');
  const ticks = movie.labels.map((label, i) => {
    const tick = div(doc, `hm-tick${i === marked ? ' now' : ''}`, label);
    if (i === marked) tick.title = 'what it is set to now';
    scale.append(tick);
    return tick;
  });
  const numbers = div(doc, 'hm-numbers');
  root.append(stage, scale);
  if (movie.less || movie.more) root.append(ends(doc, movie));
  root.append(numbers);

  const order = frameOrder(n);
  let step = 0;
  let front = 0;
  const show = (i, fade) => {
    const next = layers[1 - front];
    next.style.backgroundPosition = framePosition(i, n);
    if (fade) {
      next.style.opacity = '1';
      layers[front].style.opacity = '0';
      front = 1 - front;
    } else {
      layers[front].style.backgroundPosition = framePosition(i, n);
      layers[front].style.opacity = '1';
      next.style.opacity = '0';
    }
    badge.textContent = `${movie.label} ${movie.labels[i]}`;
    ticks.forEach((t, k) => t.classList.toggle('on', k === i));
    numbers.textContent = frameNumbers(movie, i);
  };
  show(order[0], false);
  const timer = setInterval(() => {
    step = (step + 1) % order.length;
    show(order[step], true);
  }, FRAME_MS);
  return { el: root, stop() { clearInterval(timer); } };
}

/** What the two ends of the sweep show, in words: "← 0: terraces" and "2: smooth →". */
export function endWords(movie) {
  const last = movie.frames - 1;
  return {
    less: movie.less ? `← ${movie.labels[0]}: ${movie.less}` : '',
    more: movie.more ? `${movie.labels[last]}: ${movie.more} →` : '',
  };
}

function ends(doc, movie) {
  const words = endWords(movie);
  const row = div(doc, 'hm-ends');
  row.append(div(doc, 'hm-less', words.less), div(doc, 'hm-more', words.more));
  return row;
}
