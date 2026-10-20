// One color picker for every browser.
//
// `<input type="color">` opens whatever the browser has.  Chrome and Edge
// draw a small popup of their own that closes when you click away; Firefox
// and Safari on a Mac open the system color panel, a separate window that
// stays until its close button is pressed (reported 2026-09-18, with a
// liking for that panel's wheel).  So this replaces all of them: a click on
// any color input is caught before the browser acts on it, and a popup
// anchored to the swatch opens instead -- hue/saturation wheel and
// brightness slider as the system panel has, hex and R G B fields, the
// screen eyedropper where the browser offers one, the cell palette, the
// colors in use, and swatches the user saves.  A click anywhere else, Escape
// or a resize closes it.
//
// The inputs themselves are untouched: the popup writes `input.value` and
// fires `input` while the color is being dragged and `change` when it closes,
// which is what a native picker does, so none of the code that listens to
// them knows the difference.

const SAVED_KEY = 'sbfsem:swatches';
const MAX_SAVED = 20;
const WHEEL_PX = 184;

let open = null;   // { input, pop, original, h, s, v, ... } while a popup is up

// -- color arithmetic -------------------------------------------------------
export function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) return null;
  const h = m[1].length === 3 ? [...m[1]].map((c) => c + c).join('') : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
}

export function rgbToHex([r, g, b]) {
  return `#${[r, g, b].map((v) => Math.round(Math.min(255, Math.max(0, v)))
    .toString(16).padStart(2, '0')).join('')}`;
}

/** r, g, b in 0-255 to hue in turns, saturation and value in 0-1. */
export function rgbToHsv([r, g, b]) {
  const [R, G, B] = [r / 255, g / 255, b / 255];
  const max = Math.max(R, G, B);
  const d = max - Math.min(R, G, B);
  let h = 0;
  if (d > 0) {
    if (max === R) h = ((G - B) / d) % 6;
    else if (max === G) h = (B - R) / d + 2;
    else h = (R - G) / d + 4;
    h = ((h / 6) % 1 + 1) % 1;
  }
  return [h, max === 0 ? 0 : d / max, max];
}

export function hsvToRgb([h, s, v]) {
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  const [r, g, b] = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][i % 6];
  return [r * 255, g * 255, b * 255];
}

// -- saved swatches -----------------------------------------------------------
function savedSwatches() {
  try {
    const list = JSON.parse(localStorage.getItem(SAVED_KEY) || '[]');
    return Array.isArray(list) ? list.filter((c) => hexToRgb(c)).slice(0, MAX_SAVED) : [];
  } catch { return []; }
}

function storeSwatches(list) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(list.slice(0, MAX_SAVED))); } catch { /* private mode */ }
}

// -- the popup ----------------------------------------------------------------
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function paintWheel(canvas, v) {
  const size = canvas.width;
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(size, size);
  const c = (size - 1) / 2;
  const radius = size / 2;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - c;
      const dy = y - c;
      const r = Math.hypot(dx, dy) / radius;
      const at = (y * size + x) * 4;
      if (r > 1) continue;
      const hue = ((Math.atan2(-dy, dx) / (2 * Math.PI)) % 1 + 1) % 1;
      const [R, G, B] = hsvToRgb([hue, r, v]);
      image.data[at] = R; image.data[at + 1] = G; image.data[at + 2] = B;
      // One pixel of fade at the rim instead of a staircase.
      image.data[at + 3] = Math.round(255 * Math.min(1, (1 - r) * radius));
    }
  }
  ctx.putImageData(image, 0, 0);
}

function swatchRow(title, colors, { onPick, onRemove } = {}) {
  const row = el('div', { className: 'cp-swatches' });
  row.append(el('span', { className: 'cp-swatches-title', textContent: title }));
  for (const color of colors) {
    const button = el('button', { type: 'button', className: 'cp-swatch', title: color });
    button.style.background = color;
    button.addEventListener('click', () => onPick(color));
    if (onRemove) {
      button.title = `${color} — right-click to remove`;
      button.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        onRemove(color);
      });
    }
    row.append(button);
  }
  return row;
}

function close() {
  if (!open) return;
  const { input, pop } = open;
  open = null;
  pop.remove();
  document.removeEventListener('pointerdown', onAway, true);
  document.removeEventListener('keydown', onKey, true);
  window.removeEventListener('resize', close);
  // Changed or not: a caller that made a throwaway input (the cell's context
  // menu) removes it on `change`, and with a native picker that was
  // dismissed untouched it never got the chance.
  input.dispatchEvent(new Event('change', { bubbles: true }));
  if (input.isConnected && input.offsetParent !== null) input.focus({ preventScroll: true });
}

function onAway(event) {
  if (open && !open.pop.contains(event.target)) close();
}

function onKey(event) {
  if (event.key === 'Escape' && open) {
    event.stopPropagation();
    close();
  }
}

function place(pop, input) {
  const anchor = input.getBoundingClientRect();
  const box = pop.getBoundingClientRect();
  const gap = 8;
  let left = anchor.right + gap;
  if (left + box.width > window.innerWidth - gap) left = anchor.left - box.width - gap;
  if (left < gap) left = Math.min(Math.max(gap, anchor.left), window.innerWidth - box.width - gap);
  let top = Math.min(anchor.top, window.innerHeight - box.height - gap);
  top = Math.max(gap, top);
  pop.style.left = `${Math.round(left)}px`;
  pop.style.top = `${Math.round(top)}px`;
}

function show(input, { palette }) {
  close();
  const original = hexToRgb(input.value) ? input.value.toLowerCase() : '#000000';
  let [h, s, v] = rgbToHsv(hexToRgb(original));

  const wheel = el('canvas', { className: 'cp-wheel', width: WHEEL_PX, height: WHEEL_PX });
  wheel.tabIndex = 0;
  wheel.setAttribute('role', 'slider');
  wheel.setAttribute('aria-label', 'Hue and saturation: arrow keys move the marker');
  const marker = el('span', { className: 'cp-marker' });
  const wheelBox = el('div', { className: 'cp-wheel-box' }, wheel, marker);

  const value = el('input', { type: 'range', className: 'cp-value', min: 0, max: 1, step: 0.005 });
  value.setAttribute('aria-label', 'Brightness');

  const before = el('button', {
    type: 'button', className: 'cp-chip cp-before', title: 'The color it had: click to put it back',
  });
  before.style.background = original;
  const after = el('span', { className: 'cp-chip cp-after', title: 'The new color' });
  const hex = el('input', {
    type: 'text', className: 'cp-hex', spellcheck: false, maxLength: 7,
  });
  hex.setAttribute('aria-label', 'Hex color');
  const channels = ['R', 'G', 'B'].map((name) => {
    const field = el('input', { type: 'number', min: 0, max: 255, step: 1, className: 'cp-channel' });
    field.setAttribute('aria-label', name);
    return field;
  });
  const dropper = typeof window.EyeDropper === 'function'
    ? el('button', {
      type: 'button', className: 'cp-tool', textContent: '⌖',
      title: 'Pick a color from anywhere on the screen',
    }) : null;
  const save = el('button', {
    type: 'button', className: 'cp-tool', textContent: '+', title: 'Save this color as a swatch',
  });

  const swatches = el('div', { className: 'cp-swatch-groups' });
  const pop = el('div', { className: 'color-pop' },
    wheelBox, value,
    el('div', { className: 'cp-row' }, before, after, hex, ...(dropper ? [dropper] : []), save),
    el('div', { className: 'cp-row cp-rgb' },
      ...channels.flatMap((field, i) => [el('label', { textContent: 'RGB'[i] }, field)])),
    swatches);
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Choose a color');

  const state = { input, pop };

  const drawSwatches = () => {
    const inUse = [...new Set([...document.querySelectorAll('input[type="color"]')]
      .map((node) => node.value.toLowerCase()))].slice(0, 10);
    const saved = savedSwatches();
    swatches.replaceChildren(
      swatchRow('Palette', palette, { onPick: setHex }),
      ...(inUse.length ? [swatchRow('In use', inUse, { onPick: setHex })] : []),
      ...(saved.length ? [swatchRow('Saved', saved, {
        onPick: setHex,
        onRemove: (color) => { storeSwatches(savedSwatches().filter((c) => c !== color)); drawSwatches(); },
      })] : []),
    );
  };

  let paintedAt = null;
  const sync = ({ from = null, emit = true } = {}) => {
    const rgb = hsvToRgb([h, s, v]);
    const now = rgbToHex(rgb);
    if (paintedAt !== v) { paintWheel(wheel, v); paintedAt = v; }
    const angle = h * 2 * Math.PI;
    const radius = (WHEEL_PX / 2) * s;
    marker.style.left = `${WHEEL_PX / 2 + Math.cos(angle) * radius}px`;
    marker.style.top = `${WHEEL_PX / 2 - Math.sin(angle) * radius}px`;
    marker.style.background = now;
    if (from !== value) value.value = v;
    value.style.setProperty('--cp-full', rgbToHex(hsvToRgb([h, s, 1])));
    after.style.background = now;
    if (from !== hex) hex.value = now;
    channels.forEach((field, i) => { if (from !== field) field.value = Math.round(rgb[i]); });
    if (emit && input.value.toLowerCase() !== now) {
      input.value = now;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };

  function setHex(color, from = null) {
    const rgb = hexToRgb(color);
    if (!rgb) return;
    const [nh, ns, nv] = rgbToHsv(rgb);
    // Gray has no hue and black no saturation: keep the marker where it was
    // rather than snapping it to red at the rim's centre.
    if (ns > 0 && nv > 0) h = nh;
    if (nv > 0) s = ns;
    v = nv;
    sync({ from });
  }

  const fromWheel = (event) => {
    const box = wheel.getBoundingClientRect();
    const dx = event.clientX - (box.left + box.width / 2);
    const dy = event.clientY - (box.top + box.height / 2);
    h = ((Math.atan2(-dy, dx) / (2 * Math.PI)) % 1 + 1) % 1;
    s = Math.min(1, Math.hypot(dx, dy) / (box.width / 2));
    // The wheel at zero brightness is a black disc: picking on it means a color.
    if (v === 0) v = 1;
    sync();
  };
  wheel.addEventListener('pointerdown', (event) => {
    wheel.setPointerCapture(event.pointerId);
    fromWheel(event);
  });
  wheel.addEventListener('pointermove', (event) => {
    if (wheel.hasPointerCapture(event.pointerId)) fromWheel(event);
  });
  wheel.addEventListener('keydown', (event) => {
    const step = event.shiftKey ? 0.1 : 0.02;
    const move = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[event.key];
    if (!move) return;
    event.preventDefault();
    const x = Math.cos(h * 2 * Math.PI) * s + move[0];
    const y = Math.sin(h * 2 * Math.PI) * s + move[1];
    h = ((Math.atan2(y, x) / (2 * Math.PI)) % 1 + 1) % 1;
    s = Math.min(1, Math.hypot(x, y));
    sync();
  });
  value.addEventListener('input', () => { v = Number(value.value); sync({ from: value }); });
  hex.addEventListener('input', () => {
    const text = hex.value.startsWith('#') ? hex.value : `#${hex.value}`;
    if (hexToRgb(text) && text.length === 7) setHex(text, hex);
  });
  hex.addEventListener('keydown', (event) => { if (event.key === 'Enter') close(); });
  channels.forEach((field) => field.addEventListener('input', () => {
    const rgb = channels.map((f) => Math.min(255, Math.max(0, Number(f.value) || 0)));
    setHex(rgbToHex(rgb), field);
  }));
  before.addEventListener('click', () => setHex(original));
  save.addEventListener('click', () => {
    const now = rgbToHex(hsvToRgb([h, s, v]));
    storeSwatches([now, ...savedSwatches().filter((c) => c !== now)]);
    drawSwatches();
  });
  dropper?.addEventListener('click', async () => {
    try {
      const { sRGBHex } = await new window.EyeDropper().open();
      setHex(sRGBHex);
    } catch { /* Escape: nothing picked */ }
  });

  document.body.append(pop);
  open = state;
  drawSwatches();
  sync({ emit: false });
  place(pop, input);
  // After this click has finished bubbling, or it would count as a click away.
  setTimeout(() => {
    if (open !== state) return;
    document.addEventListener('pointerdown', onAway, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', close);
  }, 0);
  wheel.focus({ preventScroll: true });
}

/**
 * Route every `<input type="color">` on the page, present and future, to the
 * popup.  `palette` is the row of stock colors it offers.
 */
export function installColorPicker({ palette = [] } = {}) {
  document.addEventListener('click', (event) => {
    const input = event.target instanceof Element
      ? event.target.closest('input[type="color"]') : null;
    if (!input || input.disabled) return;
    // Before the browser opens its own.
    event.preventDefault();
    if (open?.input === input) { close(); return; }
    show(input, { palette });
  }, true);
}
