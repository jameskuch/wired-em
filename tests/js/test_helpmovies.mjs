// The render options' hover-help movies (static/js/helpmovies.js): which movie a field
// gets under which method, the order its frames play in, the frame marked as the value
// set now, the measured line under each frame, and the element the help pop shows.
// Run with
//
//     node tests/js/test_helpmovies.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const M = await import(pathToFileURL(path.join(jsDir, 'helpmovies.js')).href);
const manifestPath = path.join(jsDir, '..', 'help', 'movies.json');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

const movie = (over = {}) => ({
  key: 'voxel_smooth_um', variant: null, image: 'voxel_smooth_um.webp', frames: 5,
  width: 316, height: 178, view: 'Temporal c118, the soma from the side', label: 'Envelope σ',
  values: [0, 0.25, 0.5, 1, 2], labels: ['0', '0.25', '0.5', '1', '2'],
  faces: [400000, 400000, 400000, 400000, 400000], seconds: [6.6, 5.6, 5.5, 5.6, 12.4],
  deviation_um: [0.166, 0.102, 0.106, 0.112, 0.135], less: 'terraces', more: 'smooth',
  technique: 'Cylinder, Voxel remesh', ...over,
});

/** Just enough of a document for buildMovie: elements with classes, text, style and children. */
function fakeDoc() {
  const make = () => {
    const el = {
      className: '', textContent: '', title: '', style: {}, children: [],
      append(...kids) { el.children.push(...kids); },
      classList: {
        toggle(name, on) {
          const set = new Set(el.className.split(' ').filter(Boolean));
          if (on) set.add(name); else set.delete(name);
          el.className = [...set].join(' ');
        },
      },
    };
    return el;
  };
  return { createElement: make };
}
function* walk(el) { yield el; for (const k of el.children) yield* walk(k); }
const byClass = (root, cls) => [...walk(root)].filter((e) => e.className.split(' ').includes(cls));

test('a field gets its method\'s own movie, else the general one, else none', () => {
  const doc = {
    curve_avg_discs: movie({ key: 'curve_avg_discs' }),
    'curve_avg_discs@closedcurve': movie({ key: 'curve_avg_discs', variant: 'closedcurve', view: 'cc' }),
    pathway: movie({ key: 'pathway' }),
  };
  assert.equal(M.movieFor(doc, 'curve_avg_discs', 'closedcurve').view, 'cc');
  assert.equal(M.movieFor(doc, 'curve_avg_discs', 'cylinder').variant, null);
  assert.equal(M.movieFor(doc, 'curve_avg_discs', ''), doc.curve_avg_discs);
  assert.equal(M.movieFor(doc, 'tie_um', 'cylinder'), null);
  assert.equal(M.movieFor(null, 'pathway', 'cylinder'), null);
});

test('a movie that is not whole, or names an image outside the help folder, is not shown', () => {
  assert.ok(M.validMovie(movie()));
  assert.ok(!M.validMovie(movie({ frames: 1, values: [0], labels: ['0'] })));
  assert.ok(!M.validMovie(movie({ labels: ['0', '1'] })));
  assert.ok(!M.validMovie(movie({ image: '../app.js' })));
  assert.ok(!M.validMovie(movie({ image: 'https://elsewhere.example/x.webp' })));
  assert.equal(M.movieFor({ voxel_smooth_um: movie({ image: '../x.webp' }) }, 'voxel_smooth_um'), null);
});

test('frames play up the sweep and back down, each end once', () => {
  assert.deepEqual(M.frameOrder(5), [0, 1, 2, 3, 4, 3, 2, 1]);
  assert.deepEqual(M.frameOrder(2), [0, 1]);
  assert.deepEqual(M.frameOrder(1), [0]);
});

test('the frame marked is the value set now, or the nearest one', () => {
  const m = movie();
  assert.equal(M.frameOf(m, '0.5'), 2);
  assert.equal(M.frameOf(m, 0.6), 2);
  assert.equal(M.frameOf(m, '1.7'), 4);
  assert.equal(M.frameOf(m, ''), -1);
  const named = movie({ values: ['original', 'voxel2'], labels: ['Original', 'Voxel remesh'], frames: 2 });
  assert.equal(M.frameOf(named, 'voxel2'), 1);
  assert.equal(M.frameOf(named, 'wrap'), -1);
});

test('each frame says what its build measured', () => {
  const m = movie();
  assert.equal(M.frameNumbers(m, 0), '400,000 triangles · 6.6 s to build · 0.166 µm from the annotations');
  assert.equal(M.frameNumbers(m, 4), '400,000 triangles · 12 s to build · 0.135 µm from the annotations');
  assert.equal(M.frameNumbers(movie({ deviation_um: [null, null, null, null, null] }), 1),
    '400,000 triangles · 5.6 s to build');
});

test('a frame\'s place in the strip', () => {
  assert.equal(M.framePosition(0, 5), '0% 0');
  assert.equal(M.framePosition(4, 5), '100% 0');
  assert.equal(M.framePosition(2, 5), '50% 0');
});

test('the two ends are said in words, and left out when a movie has none', () => {
  assert.deepEqual(M.endWords(movie()), { less: '← 0: terraces', more: '2: smooth →' });
  assert.deepEqual(M.endWords(movie({ less: '', more: '' })), { less: '', more: '' });
});

test('the movie element: a stage, a scale with the value set now marked, and its numbers', () => {
  const { el, stop } = M.buildMovie(movie(), { current: '0.5', doc: fakeDoc() });
  try {
    const stage = byClass(el, 'hm-stage')[0];
    assert.equal(stage.style.aspectRatio, '316 / 178');
    const layers = byClass(stage, 'hm-frame');
    assert.equal(layers.length, 2, 'two layers cross-fade');
    assert.equal(layers[0].style.backgroundImage, 'url("/static/help/voxel_smooth_um.webp")');
    assert.equal(layers[0].style.backgroundSize, '500% 100%');
    const ticks = byClass(el, 'hm-tick');
    assert.deepEqual(ticks.map((t) => t.textContent), ['0', '0.25', '0.5', '1', '2']);
    assert.ok(ticks[2].className.includes('now'));
    assert.ok(ticks[0].className.includes('on'), 'the first frame shows first');
    assert.equal(byClass(el, 'hm-value')[0].textContent, 'Envelope σ 0');
    assert.match(byClass(el, 'hm-numbers')[0].textContent, /^400,000 triangles/);
    assert.equal(byClass(el, 'hm-view')[0].textContent,
      'Temporal c118, the soma from the side · Cylinder, Voxel remesh');
  } finally {
    stop();
  }
});

test('with reduced motion the frames are a still strip', () => {
  const { el, stop } = M.buildMovie(movie(), { current: '1', doc: fakeDoc(), reducedMotion: true });
  stop();
  assert.equal(byClass(el, 'hm-stage').length, 0);
  const thumbs = byClass(el, 'hm-thumb');
  assert.equal(thumbs.length, 5);
  assert.equal(byClass(el, 'hm-frame')[4].style.backgroundPosition, '100% 0');
  assert.ok(byClass(el, 'hm-thumb-label')[3].className.includes('now'));
});

test('the manifest shipped with the page is whole', () => {
  if (!fs.existsSync(manifestPath)) return;
  const doc = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
  const ids = Object.keys(doc);
  assert.ok(ids.length > 0);
  for (const id of ids) {
    assert.ok(M.validMovie(doc[id]), id);
    assert.equal(id, doc[id].variant ? `${doc[id].key}@${doc[id].variant}` : doc[id].key, id);
    assert.ok(fs.existsSync(path.join(path.dirname(manifestPath), doc[id].image)), doc[id].image);
  }
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.stack || error}`);
  }
}
console.log(failed ? `${failed} of ${cases.length} failed` : `${cases.length} passed`);
process.exit(failed ? 1 : 0);
