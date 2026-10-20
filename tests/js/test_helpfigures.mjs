// The drawings of the hover help (static/js/helpfigures.js): every figure is
// well-formed SVG built from constants, uses only the elements, attributes and
// classes the page allows and styles, and is built with createElementNS and
// setAttribute, never markup.  Run with
//
//     node tests/js/test_helpfigures.mjs
//
// One line per case; exits non-zero if any failed.  Dependency free.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const jsDir = process.env.SBFSEM_JS_DIR || path.join(here, '..', '..', 'src', 'sbfsem_web', 'static', 'js');
const cssPath = path.join(jsDir, '..', 'css', 'app.css');
const load = (name) => import(pathToFileURL(path.join(jsDir, name)).href);
const H = await load('helpfigures.js');
const S = await load('shifts.js');
const R = await load('regstatus.js');

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

/** Every node of a tree, depth first. */
function* walk(node) {
  yield node;
  if (typeof node !== 'string') for (const child of node.children) yield* walk(child);
}

/** A tiny XML check: tags balance and nest, attributes are quoted, and no raw `<` or `&` is in text. */
function wellFormed(markup) {
  const stack = [];
  const token = /<(\/?)([a-zA-Z][\w-]*)((?:\s+[\w:-]+="[^"<]*")*)\s*(\/?)>|([^<]+)/g;
  let at = 0;
  for (let m = token.exec(markup); m; m = token.exec(markup)) {
    if (m.index !== at) return `unparsed at ${at}: ${markup.slice(at, at + 40)}`;
    at = token.lastIndex;
    if (m[5] !== undefined) {
      if (/&(?!amp;|lt;|gt;|quot;)/.test(m[5])) return `a raw & in text: ${m[5]}`;
      continue;
    }
    const [, close, tag, , selfClose] = m;
    if (close) {
      if (stack.pop() !== tag) return `</${tag}> closes nothing open`;
    } else if (!selfClose) {
      stack.push(tag);
    }
  }
  if (at !== markup.length) return `trailing text at ${at}`;
  return stack.length ? `unclosed ${stack.join(', ')}` : '';
}

test('every figure a help names exists, and each is a figure of its own', () => {
  const names = H.figureNames();
  assert.ok(names.length >= 10, names);
  const wanted = new Set([...Object.values(S.SHIFT_FIGURES), ...Object.values(R.REG_FIGURES)]);
  for (const name of wanted) assert.ok(names.includes(name), `${name} is named by a help and not drawn`);
  for (const name of names) assert.ok(H.figureTree(name), name);
});

test('a name that is not a figure draws nothing, the object\'s own keys included', () => {
  for (const name of ['nope', '', 'constructor', '__proto__', 'toString', 'hasOwnProperty', null, undefined]) {
    assert.equal(H.figureTree(name), null, String(name));
  }
});

test('each figure is an SVG with a view box, of allowed elements and attributes only', () => {
  for (const name of H.figureNames()) {
    const tree = H.figureTree(name);
    assert.equal(tree.tag, 'svg', name);
    assert.match(String(tree.attrs.viewBox), /^0 0 \d+ \d+$/, `${name} viewBox`);
    assert.equal(tree.attrs['aria-hidden'], 'true', `${name}: the words say it, the drawing is decoration`);
    for (const node of walk(tree)) {
      if (typeof node === 'string') {
        assert.ok(node.length < 80, `${name}: a label, not prose: ${node}`);
        continue;
      }
      assert.ok(H.ALLOWED_TAGS.has(node.tag), `${name}: <${node.tag}>`);
      for (const [key, value] of Object.entries(node.attrs)) {
        assert.ok(H.ALLOWED_ATTRS.has(key), `${name}: ${key}`);
        assert.ok(!/^on/i.test(key), `${name}: an event handler ${key}`);
        assert.ok(['string', 'number'].includes(typeof value), `${name}: ${key} is ${typeof value}`);
        assert.ok(!/[<>"&]|javascript:|url\(|expression\(/i.test(String(value)), `${name}: ${key}="${value}"`);
        if (typeof value === 'number') assert.ok(Number.isFinite(value), `${name}: ${key} is ${value}`);
      }
    }
  }
});

test('each figure\'s markup is well-formed, and the same every time it is built', () => {
  for (const name of H.figureNames()) {
    const markup = H.toMarkup(H.figureTree(name));
    assert.equal(wellFormed(markup), '', name);
    assert.ok(markup.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'), name);
    assert.equal(H.toMarkup(H.figureTree(name)), markup, `${name} is deterministic`);
    assert.ok(markup.length < 20000, `${name}: ${markup.length} characters`);
  }
});

test('text is escaped in markup, whatever it holds', () => {
  const markup = H.toMarkup({ tag: 'text', attrs: { x: 1, class: 'a"b' }, children: ['1 < 2 & "3"'] });
  assert.equal(markup, '<text x="1" class="a&quot;b">1 &lt; 2 &amp; &quot;3&quot;</text>');
  assert.equal(wellFormed(markup), '');
});

test('every class a figure uses is styled by the stylesheet, from its tokens', () => {
  const css = fs.readFileSync(cssPath, 'utf8');
  const used = new Set();
  for (const name of H.figureNames()) {
    for (const node of walk(H.figureTree(name))) {
      if (typeof node !== 'string' && node.attrs.class) for (const c of String(node.attrs.class).split(/\s+/)) used.add(c);
    }
  }
  assert.ok(used.size >= 8, [...used]);
  for (const cls of used) {
    const rule = new RegExp(`\\.help-fig \\.${cls}\\s*\\{([^}]*)\\}`).exec(css);
    assert.ok(rule, `.help-fig .${cls} has no rule in app.css`);
  }
  // The ink of the drawings follows the theme: the classes are colored by tokens.
  for (const cls of ['hf-ink', 'hf-label', 'hf-muted', 'hf-accent', 'hf-vp']) {
    const rule = new RegExp(`\\.help-fig \\.${cls}\\s*\\{([^}]*)\\}`).exec(css);
    assert.match(rule[1], /var\(--/, `.${cls} takes a token`);
  }
});

/** A fake document that builds elements and refuses markup. */
function fakeDocument() {
  const made = [];
  class Node {
    constructor(tag, ns = null) { this.tag = tag; this.ns = ns; this.attrs = {}; this.children = []; this.className = ''; made.push(this); }
    setAttribute(k, v) { this.attrs[k] = v; }
    append(...kids) { this.children.push(...kids); }
    set innerHTML(_) { throw new Error('innerHTML is never set'); }
    set outerHTML(_) { throw new Error('outerHTML is never set'); }
    insertAdjacentHTML() { throw new Error('insertAdjacentHTML is never used'); }
  }
  return {
    made,
    createElement: (tag) => new Node(tag),
    createElementNS: (ns, tag) => new Node(tag, ns),
    createTextNode: (text) => ({ text }),
  };
}

test('buildFigure makes SVG elements in the SVG namespace, inside a div.help-fig, with no markup', () => {
  const doc = fakeDocument();
  const wrap = H.buildFigure('pair', doc);
  assert.equal(wrap.tag, 'div');
  assert.equal(wrap.className, 'help-fig');
  const svg = wrap.children[0];
  assert.equal(svg.tag, 'svg');
  assert.equal(svg.ns, 'http://www.w3.org/2000/svg');
  assert.ok(doc.made.filter((n) => n.ns).length > 10);
  assert.ok(doc.made.every((n) => n.tag === 'div' || n.ns === 'http://www.w3.org/2000/svg'));
  assert.equal(H.buildFigure('nope', doc), null);
  assert.equal(H.buildFigure('pair', null), null, 'no document, no figure');
});

test('the ramp and the wheel in the figures are the layer\'s own colors', () => {
  const markup = H.toMarkup(H.figureTree('color'));
  // The ramp's first and last stops, as the dark viewport draws them.
  const hex = (h) => h.toLowerCase();
  const first = S.rampSample(S.RAMPS.dark, 0.5 / 24).map((c) => Math.round(c * 255).toString(16).padStart(2, '0')).join('');
  assert.ok(markup.includes(`#${first}`), `the ramp's first cell #${first}`);
  // Hue 0 (+X) is red-ish on the wheel.
  const wedge = S.hslToRgb(0.5 / 36, S.DIRECTION_SATURATION, S.DIRECTION_LIGHTNESS.dark)
    .map((c) => Math.round(c * 255).toString(16).padStart(2, '0')).join('');
  assert.ok(hex(markup).includes(`#${wedge}`), `the wheel's first wedge #${wedge}`);
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
