// The pure core of the in-page dropdown list (static/js/selectpicker.js): row
// stepping and type-ahead, placement, which browsers get the list, which keys
// open it, and the order of events when a row is picked.  The DOM half is
// checked in a real browser by tools/check_dropdowns.py.  Run it with
//
//     node tests/js/test_selectpicker.mjs
//
// It prints one line per case and exits non-zero if any failed.  Dependency
// free; no timers, so it does not depend on how fast the machine is.

import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const jsDir = process.env.SBFSEM_JS_DIR
  || path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..',
    'src', 'sbfsem_web', 'static', 'js');
const modulePath = path.join(jsDir, 'selectpicker.js');

// Importing the module must not read the page or the browser: it is loaded by
// every page, before anything has decided whether it will be used.  Make each
// global that would give it away throw, import, and put them back.
const globalsToPoison = ['document', 'window', 'navigator', 'localStorage', 'HTMLSelectElement'];
const saved = new Map(globalsToPoison.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
for (const name of globalsToPoison) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    get() { throw new Error(`selectpicker.js touched ${name} at import`); },
  });
}
let loadError = null;
let picker = {};
try {
  picker = await import(`${pathToFileURL(modulePath).href}?cold`);
} catch (error) {
  loadError = error;
} finally {
  for (const [name, descriptor] of saved) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
  }
}
const {
  isSelectable, stepIndex, edgeIndex, pageIndex, typeaheadIndex, nextTyped, placement, revealScroll,
  popupWindowPlatform, macPlatform, isChromium, resolveMode, opensList, rowsOf, optionSignature, commitSelection,
  STORAGE_KEY,
} = picker;

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

// -- rows ---------------------------------------------------------------------
const row = (label, extra = {}) => ({ label, value: label.toLowerCase(), ...extra });
const heading = (label) => ({ label, heading: true, disabled: true });
//                       0           1             2            3                4
const mixed = [heading('Group'), row('Alpha'), row('Beta', { disabled: true }), row('Gamma'), row('Delta')];

test('importing the module touches no document, window, navigator or storage', () => {
  assert.equal(loadError, null, loadError && String(loadError.stack));
  assert.equal(typeof stepIndex, 'function');
});

test('the storage key is the one the Display checkbox documents', () => {
  assert.equal(STORAGE_KEY, 'sbfsem:select-lists');
});

test('only a plain, enabled row can be landed on', () => {
  assert.equal(isSelectable(row('A')), true);
  assert.equal(isSelectable(row('A', { disabled: true })), false);
  assert.equal(isSelectable(heading('G')), false);
  assert.equal(isSelectable(undefined), false);
});

// -- stepIndex ------------------------------------------------------------------
test('stepIndex skips disabled rows and headings, in both directions', () => {
  assert.equal(stepIndex(mixed, 1, 1), 3);    // over the disabled Beta
  assert.equal(stepIndex(mixed, 3, -1), 1);
  assert.equal(stepIndex(mixed, 3, 1), 4);
});

test('stepIndex clamps at the ends instead of wrapping', () => {
  assert.equal(stepIndex(mixed, 4, 1), 4);
  assert.equal(stepIndex(mixed, 1, -1), 1);   // the heading above is not a row
});

test('stepIndex stays put rather than landing on a disabled last row', () => {
  const rows = [row('A'), row('B', { disabled: true }), row('C', { disabled: true })];
  assert.equal(stepIndex(rows, 0, 1), 0);
});

test('stepIndex from "nothing active" enters at the first or does nothing going up', () => {
  assert.equal(stepIndex(mixed, -1, 1), 1);
  assert.equal(stepIndex(mixed, -1, -1), -1);
});

test('stepIndex on an empty list returns where it started', () => {
  assert.equal(stepIndex([], -1, 1), -1);
  assert.equal(stepIndex([], 0, -1), 0);
});

test('edgeIndex finds the first and last selectable rows', () => {
  assert.equal(edgeIndex(mixed, 'first'), 1);
  assert.equal(edgeIndex(mixed, 'last'), 4);
  assert.equal(edgeIndex([row('A', { disabled: true })], 'first'), -1);
  assert.equal(edgeIndex([], 'last'), -1);
  assert.equal(edgeIndex([row('A'), row('Z', { disabled: true })], 'last'), 0);
});

test('pageIndex moves a page, lands on a selectable row, and clamps', () => {
  const rows = Array.from({ length: 30 }, (_, i) => row(`R${i}`));
  assert.equal(pageIndex(rows, 0, 1, 7), 7);
  assert.equal(pageIndex(rows, 7, -1, 7), 0);
  assert.equal(pageIndex(rows, 28, 1, 7), 29);
  assert.equal(pageIndex(rows, 3, -1, 7), 0);
  const gap = [row('A'), row('B'), row('C', { disabled: true }), row('D', { disabled: true })];
  assert.equal(pageIndex(gap, 0, 1, 10), 1);   // the last rows are disabled: back to the nearest
  assert.equal(pageIndex(mixed, 4, -1, 2), 1);  // target is the disabled Beta: on to Alpha
  assert.equal(pageIndex([], 0, 1, 5), 0);
});

// -- typeaheadIndex -------------------------------------------------------------
const fruit = ['Apple', 'Apricot', 'Banana', 'Blueberry', 'Cherry'].map((l) => row(l));

test('typeaheadIndex: one letter goes to the next row that starts with it', () => {
  assert.equal(typeaheadIndex(fruit, 0, 'b'), 2);
  assert.equal(typeaheadIndex(fruit, 2, 'b'), 3);   // a second press of the same key cycles
  assert.equal(typeaheadIndex(fruit, 3, 'b'), 2);   // and wraps
});

test('typeaheadIndex: a repeated letter cycles, it is not a prefix', () => {
  assert.equal(typeaheadIndex(fruit, 2, 'bb'), 3);
  assert.equal(typeaheadIndex(fruit, 3, 'bbb'), 2);
});

test('typeaheadIndex: a longer prefix is matched from the current row, which can stay', () => {
  assert.equal(typeaheadIndex(fruit, 0, 'ap'), 0);
  assert.equal(typeaheadIndex(fruit, 0, 'apr'), 1);
  assert.equal(typeaheadIndex(fruit, 4, 'blu'), 3);   // wraps round to Blueberry
});

test('typeaheadIndex is case-insensitive and ignores disabled rows and headings', () => {
  assert.equal(typeaheadIndex(fruit, 0, 'CH'), 4);
  assert.equal(typeaheadIndex(mixed, 1, 'b'), -1);    // Beta is disabled
  assert.equal(typeaheadIndex(mixed, 1, 'g'), 3);     // the heading "Group" is not matched
  assert.equal(typeaheadIndex(fruit, 0, 'zzz'), -1);
  assert.equal(typeaheadIndex(fruit, 0, ''), -1);
  assert.equal(typeaheadIndex([], 0, 'a'), -1);
});

test('typeaheadIndex ignores leading space in a label', () => {
  const rows = [row('  indented'), row('other')];
  assert.equal(typeaheadIndex(rows, 1, 'i'), 0);
});

test('nextTyped extends the buffer inside the window and restarts after it', () => {
  let s = { typed: '', at: -Infinity };
  s = nextTyped(s, 'a', 1000);
  assert.deepEqual(s, { typed: 'a', at: 1000 });
  s = nextTyped(s, 'p', 1500);
  assert.equal(s.typed, 'ap');
  s = nextTyped(s, 'r', 2300);   // 800 ms after the last key: a new word
  assert.equal(s.typed, 'r');
});

// -- placement ------------------------------------------------------------------
const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });

for (const width of [900, 1140, 1440, 1920]) {
  for (const height of [600, 900]) {
    const vp = { width, height };

    test(`placement ${width}x${height}: opens below a control near the top`, () => {
      const a = rect(40, 100, 160, 26);
      const spot = placement(a, vp, 200, { width: 160 });
      assert.equal(spot.flip, false);
      assert.equal(spot.top, a.bottom + 2);
      assert.equal(spot.height, 200);
      assert.equal(spot.left, 40);
      assert.equal(spot.scroll, false);
    });

    test(`placement ${width}x${height}: a tall list is capped at 360 px and scrolls`, () => {
      const a = rect(40, 60, 160, 26);
      const spot = placement(a, vp, 2000, { width: 160 });
      assert.equal(spot.flip, false);
      assert.equal(spot.height, 360);
      assert.equal(spot.scroll, true);
    });

    test(`placement ${width}x${height}: flips above the bottom toolbar and stays in the viewport`, () => {
      const a = rect(300, height - 44, 180, 28);   // the quick-technique select
      const spot = placement(a, vp, 240, { width: 180 });
      assert.equal(spot.flip, true);
      assert.equal(spot.height, 240);
      assert.equal(spot.top + spot.height, a.top - 2);
      assert.ok(spot.top >= 8);
    });

    test(`placement ${width}x${height}: the right edge is pulled inside by 8 px`, () => {
      const a = rect(width - 60, 100, 60, 26);
      const spot = placement(a, vp, 120, { width: 220 });
      assert.equal(spot.left, width - 220 - 8);
      const far = placement(rect(-30, 100, 60, 26), vp, 120, { width: 220 });
      assert.equal(far.left, 8);
    });
  }
}

test('placement: with little room both ways it takes the larger side and keeps the 96 px floor', () => {
  const vp = { width: 900, height: 300 };
  const spot = placement(rect(40, 140, 160, 26), vp, 400, { width: 160 });   // 124 below, 130 above
  assert.equal(spot.flip, true);
  assert.equal(spot.height, 130);
  assert.equal(spot.top, 8);
  const tiny = placement(rect(40, 60, 160, 26), { width: 900, height: 160 }, 400, { width: 160 });
  assert.equal(tiny.height, 96);
  assert.ok(tiny.top >= 8 && tiny.top + tiny.height <= 160 - 8);
});

test('placement: a short list is never stretched', () => {
  const spot = placement(rect(40, 100, 160, 26), { width: 900, height: 600 }, 50, { width: 160 });
  assert.equal(spot.height, 50);
  assert.equal(placement(rect(40, 100, 160, 26), { width: 900, height: 600 }, 50).left, 40);
});

test('placement: a control scrolled out of view still gives an on-screen list', () => {
  const spot = placement(rect(40, -200, 160, 26), { width: 900, height: 600 }, 120, { width: 160 });
  assert.ok(spot.top >= 8);
});

test('revealScroll: nearest keeps a visible row, scrolls a hidden one just into view, centers on request', () => {
  assert.equal(revealScroll(40, 60, 0, 100), 0);       // already visible
  assert.equal(revealScroll(150, 170, 0, 100), 70);    // below: its bottom edge meets the view's
  assert.equal(revealScroll(10, 30, 50, 100), 10);     // above: its top edge
  assert.equal(revealScroll(500, 520, 0, 100, 'center'), 460);
  assert.equal(revealScroll(10, 30, 0, 100, 'center'), 0);   // never negative
});

// -- which browsers get it ---------------------------------------------------------
test('popupWindowPlatform: Windows, Linux and ChromeOS draw a popup window; macOS and Android do not', () => {
  for (const p of ['Windows', 'Win32', 'Linux', 'Linux x86_64', 'Chrome OS', 'ChromeOS', 'CrOS']) {
    assert.equal(popupWindowPlatform(p), true, p);
  }
  for (const p of ['macOS', 'MacIntel', 'Android', 'Linux Android', 'iPhone', '', undefined, 'Unknown']) {
    assert.equal(popupWindowPlatform(p), false, String(p));
  }
});

test('macPlatform recognizes the Apple platforms', () => {
  for (const p of ['macOS', 'MacIntel', 'iPhone', 'iPad']) assert.equal(macPlatform(p), true, p);
  for (const p of ['Windows', 'Linux', '', undefined]) assert.equal(macPlatform(p), false, String(p));
});

test('resolveMode: the in-page list is the default on Windows, Linux and ChromeOS with a fine pointer', () => {
  for (const platform of ['Windows', 'Linux', 'Chrome OS']) {
    assert.equal(resolveMode({ stored: null, chromium: true, platform, anyFine: true }), 'page', platform);
  }
});

test('resolveMode: native on macOS, with no fine pointer, and outside Chromium', () => {
  assert.equal(resolveMode({ stored: null, chromium: true, platform: 'macOS', anyFine: true }), 'native');
  assert.equal(resolveMode({ stored: null, chromium: true, platform: 'Windows', anyFine: false }), 'native');
  assert.equal(resolveMode({ stored: null, chromium: false, platform: 'Windows', anyFine: true }), 'native');
  assert.equal(resolveMode({ stored: null, chromium: true, platform: 'Android', anyFine: true }), 'native');
});

test('resolveMode: the person\'s choice wins in a Chromium browser, anywhere', () => {
  assert.equal(resolveMode({ stored: 'page', chromium: true, platform: 'macOS', anyFine: false }), 'page');
  assert.equal(resolveMode({ stored: 'native', chromium: true, platform: 'Windows', anyFine: true }), 'native');
});

test('resolveMode: a stored choice cannot turn the list on in a browser that is not Chromium', () => {
  assert.equal(resolveMode({ stored: 'page', chromium: false, platform: 'Windows', anyFine: true }), 'native');
});

test('resolveMode: a stored value that is neither page nor native is ignored', () => {
  assert.equal(resolveMode({ stored: 'on', chromium: true, platform: 'Windows', anyFine: true }), 'page');
  assert.equal(resolveMode({ stored: '', chromium: true, platform: 'macOS', anyFine: true }), 'native');
});

// -- which keys open the list --------------------------------------------------------
const key = (k, mods = {}) => ({ key: k, altKey: false, ctrlKey: false, metaKey: false, ...mods });

for (const [name, mac] of [['Windows/Linux', false], ['macOS', true]]) {
  test(`opensList on ${name}: Space opens unless a type-ahead session is running`, () => {
    assert.equal(opensList(key(' '), { mac }), true);
    assert.equal(opensList(key(' '), { mac, typing: true }), false);
    assert.equal(opensList(key(' ', { ctrlKey: true }), { mac }), false);
    assert.equal(opensList(key(' ', { altKey: true }), { mac }), false);
  });

  test(`opensList on ${name}: F4 opens, Ctrl+F4 and Alt+F4 do not`, () => {
    assert.equal(opensList(key('F4'), { mac }), true);
    assert.equal(opensList(key('F4', { ctrlKey: true }), { mac }), false);
    assert.equal(opensList(key('F4', { altKey: true }), { mac }), false);
  });

  test(`opensList on ${name}: Alt+ArrowDown and Alt+ArrowUp open`, () => {
    assert.equal(opensList(key('ArrowDown', { altKey: true }), { mac }), true);
    assert.equal(opensList(key('ArrowUp', { altKey: true }), { mac }), true);
  });

  test(`opensList on ${name}: Home, End, PageUp, PageDown, letters and Tab never do`, () => {
    for (const k of ['Home', 'End', 'PageUp', 'PageDown', 'a', 'Z', '1', 'Tab', 'Escape', 'ArrowLeft', 'ArrowRight']) {
      assert.equal(opensList(key(k), { mac }), false, k);
    }
  });
}

test('opensList: Enter opens the list on Windows and Linux, and not on macOS', () => {
  assert.equal(opensList(key('Enter'), { mac: false }), true);
  assert.equal(opensList(key('Enter'), { mac: true }), false);
  assert.equal(opensList(key('Enter', { ctrlKey: true }), { mac: false }), false);
});

test('opensList: the plain arrows step the value in place on Windows and Linux, and open the menu on macOS', () => {
  assert.equal(opensList(key('ArrowDown'), { mac: false }), false);
  assert.equal(opensList(key('ArrowUp'), { mac: false }), false);
  assert.equal(opensList(key('ArrowDown'), { mac: true }), true);
  assert.equal(opensList(key('ArrowUp'), { mac: true }), true);
});

// -- rows of a select ------------------------------------------------------------------
const option = (label, value, extra = {}) => ({
  tagName: 'OPTION', label, textContent: label, value, index: -1, title: '', disabled: false, hidden: false, ...extra,
});

/** A fake <select>: the shape rowsOf and commitSelection read, with real events. */
function fakeSelect(children, selectedIndex = 0) {
  const select = new EventTarget();
  select.children = children;
  const flat = children.flatMap((c) => (c.tagName === 'OPTGROUP' ? c.children : [c]));
  flat.forEach((o, i) => { o.index = i; });
  select.options = flat;
  select.selectedIndex = selectedIndex;
  select.log = [];
  for (const type of ['input', 'change']) {
    select.addEventListener(type, (event) => {
      select.log.push({ type, at: select.selectedIndex, bubbles: event.bubbles, composed: event.composed });
    });
  }
  return select;
}

test('rowsOf reads options, optgroup headings, hidden and disabled state, titles and indexes', () => {
  const select = fakeSelect([
    option('Plain', 'p'),
    option('Secret', 's', { hidden: true }),
    { tagName: 'OPTGROUP', label: 'Fits', disabled: false, hidden: false,
      children: [option('Quadratic', 'q', { title: 'Fits a curve' }), option('Mean', 'm', { disabled: true })] },
    { tagName: 'OPTGROUP', label: 'Off', disabled: true, hidden: false, children: [option('Never', 'n')] },
    { tagName: 'OPTGROUP', label: 'Hidden group', hidden: true, children: [option('Lost', 'l')] },
  ]);
  const rows = rowsOf(select);
  assert.deepEqual(rows.map((r) => [r.heading ? 'H' : 'O', r.label]),
    [['O', 'Plain'], ['H', 'Fits'], ['O', 'Quadratic'], ['O', 'Mean'], ['H', 'Off'], ['O', 'Never']]);
  assert.equal(rows[2].title, 'Fits a curve');
  assert.equal(rows[2].index, 2);          // its place in select.options, not in the list
  assert.equal(rows[3].disabled, true);
  assert.equal(rows[5].disabled, true);    // a disabled group disables its options
  assert.equal(rows[0].disabled, false);
  assert.equal(rows[1].disabled, true);    // a heading can never be picked
});

test('rowsOf counts the options of a hidden group, so a row after it keeps its place in select.options', () => {
  const hidden = { tagName: 'OPTGROUP', label: 'Away', hidden: true, children: [option('H1', 'h1'), option('H2', 'h2')] };
  const select = fakeSelect([option('A', 'a'), hidden, option('Z', 'z')]);
  const rows = rowsOf(select);
  assert.deepEqual(rows.map((r) => r.label), ['A', 'Z']);
  assert.equal(rows[1].index, 3);          // A, H1, H2, Z in select.options
});

test('isChromium reads userAgentData where there is one, else the user-agent string', () => {
  assert.equal(isChromium({ brands: [{ brand: 'Chromium' }, { brand: 'Google Chrome' }] }, 'x'), true);
  assert.equal(isChromium({ brands: [{ brand: 'Not.A/Brand' }] }, 'Chrome/148'), false);
  // A plain-http page has no userAgentData (not a secure context).
  const chrome = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36';
  assert.equal(isChromium(undefined, chrome), true);
  assert.equal(isChromium(undefined, `${chrome} Edg/148.0.0.0`), true);
  assert.equal(isChromium(undefined, 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'), false);
  assert.equal(isChromium(undefined, 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0'), false);
  assert.equal(isChromium(undefined, undefined), false);
});

test('rowsOf gives an empty label a non-breaking space so the row keeps its height', () => {
  const rows = rowsOf(fakeSelect([option('', ''), option('A', 'a')]));
  assert.equal(rows[0].label, ' ');
  assert.equal(rows[0].value, '');
});

test('rowsOf of a select with no options is empty', () => {
  assert.deepEqual(rowsOf(fakeSelect([])), []);
});

test('optionSignature changes with the options, their text, state and the selection', () => {
  const base = optionSignature(fakeSelect([option('A', 'a'), option('B', 'b')], 0));
  assert.equal(base, optionSignature(fakeSelect([option('A', 'a'), option('B', 'b')], 0)));
  assert.notEqual(base, optionSignature(fakeSelect([option('A', 'a'), option('B', 'b')], 1)));
  assert.notEqual(base, optionSignature(fakeSelect([option('A', 'a'), option('B2', 'b')], 0)));
  assert.notEqual(base, optionSignature(fakeSelect([option('A', 'a'), option('B', 'x')], 0)));
  assert.notEqual(base, optionSignature(fakeSelect([option('A', 'a'), option('B', 'b', { disabled: true })], 0)));
  assert.notEqual(base, optionSignature(fakeSelect([option('A', 'a')], 0)));
});

// -- committing a pick -------------------------------------------------------------------
test('commitSelection sets the index, then fires input and change, once each, in that order', () => {
  const select = fakeSelect([option('A', 'a'), option('B', 'b'), option('C', 'c')], 0);
  const [, b] = rowsOf(select);
  assert.equal(commitSelection(select, b), true);
  assert.equal(select.selectedIndex, 1);
  assert.deepEqual(select.log.map((e) => e.type), ['input', 'change']);
  // The index was already set when the first listener ran.
  assert.deepEqual(select.log.map((e) => e.at), [1, 1]);
});

test('commitSelection: both events bubble, and input is composed, like the browser\'s own', () => {
  const select = fakeSelect([option('A', 'a'), option('B', 'b')], 0);
  commitSelection(select, rowsOf(select)[1]);
  assert.deepEqual(select.log.map((e) => [e.bubbles, e.composed]), [[true, true], [true, false]]);
});

test('commitSelection does nothing for the row that is already selected', () => {
  const select = fakeSelect([option('A', 'a'), option('B', 'b')], 1);
  assert.equal(commitSelection(select, rowsOf(select)[1]), false);
  assert.equal(select.selectedIndex, 1);
  assert.deepEqual(select.log, []);
});

test('commitSelection refuses a stale row: the options were replaced while the list was open', () => {
  const select = fakeSelect([option('A', 'a'), option('B', 'b')], 0);
  const stale = rowsOf(select)[1];
  const replaced = fakeSelect([option('X', 'x'), option('Y', 'y')], 0);
  assert.equal(commitSelection(replaced, stale), false);
  assert.equal(replaced.selectedIndex, 0);
  assert.deepEqual(replaced.log, []);
  const shorter = fakeSelect([option('A', 'a')], 0);
  assert.equal(commitSelection(shorter, stale), false);
});

test('commitSelection refuses headings and disabled rows', () => {
  const select = fakeSelect([option('A', 'a'), option('B', 'b', { disabled: true })], 0);
  assert.equal(commitSelection(select, rowsOf(select)[1]), false);
  assert.equal(commitSelection(select, { heading: true, label: 'G', disabled: true }), false);
  assert.equal(commitSelection(select, undefined), false);
  assert.deepEqual(select.log, []);
});

test('commitSelection picks by index, so a duplicate value still lands on the row that was clicked', () => {
  const select = fakeSelect([option('One', 'x'), option('Two', 'x')], 0);
  assert.equal(commitSelection(select, rowsOf(select)[1]), true);
  assert.equal(select.selectedIndex, 1);
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
