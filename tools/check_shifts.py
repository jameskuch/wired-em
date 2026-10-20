"""Scripted browser check of the Position shift layer and the Registration section.

    .venv/bin/python tools/check_shifts.py                          # the dev server on :8765
    .venv/bin/python tools/check_shifts.py --url http://127.0.0.1:8772 -v
    .venv/bin/python tools/check_shifts.py --only arrows,legend --shots /tmp/shifts

It drives a headless Chrome for Testing over the DevTools protocol (the client and the
report are ``tools/check_dropdowns.py``'s), in a throwaway profile.  It is outside the
pytest gate: it needs a running server that can load Inferior Monkey c1178 and RC1 c410
(cached under ``~/.cache/sbfsem``, or online).  The pure half -- the wide-line buffers and
their draw ranges, the legend's model, the figures, the Registration chips -- is covered by
``tests/js/test_fatlines.mjs``, ``test_shiftlegend.mjs``, ``test_helpfigures.mjs`` and
``test_regstatus.mjs``, which the gate runs; what needs a page and a WebGL context is here
(asked for 2026-10-16: the help in tooltips with drawings, thicker arrows and a legend, and no
transforms switch where a volume has none):

* ``panel`` -- the Registration and Position shift sections hold controls and small status
  chips, no standing lines of facts; each chip carries its facts as hover help.
* ``help`` -- a help pop shows its drawing above its words after a 2 s hover, and not before.
* ``arrows`` -- the arrows are drawn as wide lines (a ShaderMaterial mesh, with the segments kept
  for the right-click menu): measured across a shaft on the screen they are about the width
  asked for (2.5 px by default, 6 px when set), where the old lines were one pixel; the
  right-click menu still finds them.
* ``slice`` -- One slice at a time still limits what is drawn: the wide mesh's draw range is the
  window's, fewer pixels are lit, and stepping changes the range and uploads nothing.
* ``legend`` -- the legend shows while a cell draws the layer and matches its color mode (the
  ramp with ticks, the direction wheel, the cell color), follows the inverted viewport, and
  covers neither the pick readout, the chip nor the toolbar; it goes with the layer.
* ``rc1`` -- an RC1 cell, whose transforms are not read here, has no transforms switch and no
  Between select; c1178's has both.
* ``compare`` -- a Render compare pane that draws the layer draws it wide and has a legend; the
  strip's facts are a chip.
* ``layout`` -- with ``--shots DIR``: screenshots of the panel, the legend and the arrows at
  900, 1140, 1440 and 1920 px wide in the dark and the light theme, to look at.
* ``focus`` -- a selected annotation's own arrow is drawn in its marker's color (yellow, then the
  red marker above it), over a dark edge and on top, even when the threshold leaves it out of the
  layer; the other arrows are dimmed to Others when selected and come back when it is deselected
  (asked for 2026-10-19: "I don't know which arrow is the movement of this annotation").
* ``moves`` -- an annotation moved by hand (2026-10-19): ``g`` opens the move panel with the
  annotation's X, Y, Z and the viewport's axes, the arrow keys move it along the chosen axis (the
  ring is drawn ahead, the move is kept on the cell), Enter applies it (the server's copy is named
  by the moves, the node is moved, the selection follows), the Manual moves list shows it, a page
  reload keeps it, and Reset all puts the cell back on its plain copy.  The Apply asks for the moved
  copy's annotations and outlines while its surface builds, once each, and draws the rings with the
  surface, not before it (2026-10-20); its polls ask the server to wait (``?wait=1``, which the page
  sends only over HTTP/2, so here it is turned on by hand) and are no more than one a second.
* ``slicecolors`` -- One slice at a time (2026-10-20): the plane is the size of its section's
  annotations and their arrows, not the cell; the window's arrows on the plane's section are drawn in the selection's
  yellow at full strength, those above in red and below in blue at Off the plane (0.06); a selected
  annotation of the cell pales the section's yellow and keeps the selection fade off; a step recolors.
* ``moveset`` -- moving a set by hand (2026-10-20): Its section moves every annotation of the
  section by the same step, Sections from the blue marker to the red one moves the run, a
  ⌘/Ctrl-click adds one, Enter applies them all, and a click on a section in the list ticks
  every move on it.
* ``regswitch`` -- a Registration switch and Update from Viking draw a new copy of the cell the way
  an Apply does (2026-10-20): its annotations and outlines are asked for once each, while its
  surface builds, and its rings are drawn with the surface.  A second switch made while the first
  one's outlines are still out (held here) draws the second copy's rings, never the first's, and
  nothing of the first is left in the caches for a later draw.
* ``console`` -- nothing logged an error or a warning on the way.
"""

# ruff: noqa: UP031
# The JavaScript snippets below are filled with `%`: their braces would all need
# doubling in an f-string or `.format`, which buries the script they carry.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_app import boot, load  # noqa: E402
from check_dropdowns import Chrome, Report, find_chrome, s_console  # noqa: E402

CELL = 1178
SOURCE = 'NeitzInferiorMonkey'
RC1_CELL = 410

# Installed after boot: helpers the sections share.
HELPERS = r"""
(() => {
  const r = () => window.sbfsem.renderer;
  window.__section = (name) => [...document.querySelectorAll('.cell-section')]
    .find((s) => s.querySelector('.sec-name').textContent === name) || null;
  window.__openSection = (name) => {
    const s = window.__section(name);
    if (s && !s.classList.contains('open')) s.querySelector('.section-head').click();
    return !!window.__section(name);
  };
  // The shift layer's group of a cell, and its wide arrows.
  window.__group = (key) => r().shifts.get(key) || null;
  window.__arrows = (key) => (window.__group(key)?.children || []).find((c) => c.userData.role === 'arrows') || null;
  // Look straight down Z at the layer, close enough that its arrows are long on the screen.
  window.__frameLayer = (key, fill = 0.7) => {
    const R = r();
    const group = window.__group(key);
    if (!group) return false;
    group.updateMatrixWorld(true);
    const Box = R.bounds.constructor;
    const V = R.bounds.min.constructor;
    const box = new Box().setFromObject(group);
    if (box.isEmpty()) return false;
    const center = box.getCenter(new V());
    const size = box.getSize(new V());
    const h = R.container.clientHeight;
    const w = R.container.clientWidth;
    const span = Math.max(size.x / (w / h), size.y, 1e-3) / fill;
    const dist = span / (2 * Math.tan((R.perspective.fov * Math.PI) / 360));
    R.camera.position.set(center.x, center.y, center.z + dist);
    R.camera.up.set(0, 1, 0);
    R.controls.target.copy(center);
    R.camera.near = dist / 100; R.camera.far = dist * 100;
    R.camera.updateProjectionMatrix();
    R.controls.update();
    return true;
  };
  // The canvas's pixels after a fresh frame, and the background color.
  window.__pixels = () => {
    const R = r();
    R.webgl.render(R.scene, R.camera);
    const c = R.webgl.domElement;
    const tmp = document.createElement('canvas');
    tmp.width = c.width; tmp.height = c.height;
    const ctx = tmp.getContext('2d');
    ctx.drawImage(c, 0, 0);
    const bg = R.scene.background;
    // The background as the canvas shows it, read off a corner.
    const corner = ctx.getImageData(1, 1, 1, 1).data;
    return { data: ctx.getImageData(0, 0, c.width, c.height).data, w: c.width, h: c.height,
             bg: [corner[0], corner[1], corner[2]], ratio: c.width / R.container.clientWidth, bgObj: bg };
  };
  window.__lit = (px, x, y) => {
    const i = (Math.round(y) * px.w + Math.round(x)) * 4;
    const d = px.data;
    return Math.abs(d[i] - px.bg[0]) + Math.abs(d[i + 1] - px.bg[1]) + Math.abs(d[i + 2] - px.bg[2]) > 36;
  };
  window.__litCount = () => {
    const px = window.__pixels();
    let n = 0;
    for (let y = 0; y < px.h; y += 1) for (let x = 0; x < px.w; x += 1) if (window.__lit(px, x, y)) n += 1;
    return n;
  };
  // Each arrow's tail and head on the screen (drawing-buffer pixels), from the shafts kept for picking
  // (two vertices an arrow: the wide arrows draw no barbs, so the menu does not measure to them).
  window.__arrowEnds = (key) => {
    const R = r();
    const arrows = window.__arrows(key);
    if (!arrows) return [];
    arrows.updateMatrixWorld(true);
    const V = R.bounds.min.constructor;
    const pos = arrows.userData.pick.getAttribute('position').array;
    const range = arrows.userData.pick.drawRange;
    const first = range.start / 2;
    const last = Number.isFinite(range.count) ? (range.start + range.count) / 2 : pos.length / 6;
    const c = R.webgl.domElement;
    const out = [];
    const screen = (o) => {
      const v = new V(pos[o], pos[o + 1], pos[o + 2]).applyMatrix4(arrows.matrixWorld).project(R.camera);
      return [(v.x + 1) / 2 * c.width, (1 - v.y) / 2 * c.height];
    };
    for (let i = first; i < Math.min(last, pos.length / 6); i += 1) {
      out.push({ i, tail: screen(i * 6), head: screen(i * 6 + 3) });
    }
    return out;
  };
  // How wide the longest shown arrow's shaft is on the screen: lit pixels across it, sampled
  // every half pixel at a third of the way from the tail, in CSS pixels.  Only that arrow is
  // drawn while it is measured (a window of one), so no other arrow crossing it counts.
  window.__shaftWidth = (key) => {
    const ends = window.__arrowEnds(key);
    if (!ends.length) return null;
    const len = (e) => Math.hypot(e.head[0] - e.tail[0], e.head[1] - e.tail[1]);
    const best = ends.reduce((a, b) => (len(b) > len(a) ? b : a));
    r().setShiftWindow(key, { arrows: { first: best.i * 6, count: 6 } });
    const px = window.__pixels();
    r().setShiftWindow(key, null);
    const L = len(best);
    const dir = [(best.head[0] - best.tail[0]) / L, (best.head[1] - best.tail[1]) / L];
    const perp = [-dir[1], dir[0]];
    const widths = [];
    for (const t of [0.3, 0.4, 0.5]) {
      const m = [best.tail[0] + dir[0] * L * t, best.tail[1] + dir[1] * L * t];
      let lit = 0;
      for (let s = -20; s <= 20; s += 0.5) {
        const x = m[0] + perp[0] * s; const y = m[1] + perp[1] * s;
        if (x < 0 || y < 0 || x >= px.w || y >= px.h) continue;
        if (window.__lit(px, x, y)) lit += 0.5;
      }
      widths.push(lit / px.ratio);
    }
    widths.sort((a, b) => a - b);
    return { width: widths[1], length: L / px.ratio, mid: [best.tail[0] + dir[0] * L * 0.4, best.tail[1] + dir[1] * L * 0.4] };
  };
})();
"""

STATE = 'window.sbfsem.state'


def js(c: Chrome, expression: str, wait: bool = False):
    return c.ev(expression, wait=wait)


def rect(c: Chrome, selector: str):
    return js(c, """(() => { const e = document.querySelector(%s);
      if (!e || e.hidden) return null; const r = e.getBoundingClientRect();
      return r.width && r.height ? [r.left, r.top, r.right, r.bottom] : null; })()""" % json.dumps(selector))


def overlaps(a, b) -> bool:
    return bool(a and b and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def start(c: Chrome, ctx, cell: int = CELL, source: str | None = None) -> str:
    """A clean page with the cell loaded, its panel open at Registration and Position shift."""
    boot(c, ctx.url)
    js(c, HELPERS)
    key = load(c, cell, source=source)
    js(c, "document.querySelector(%s).click(); true" % json.dumps(f'.neuron-item[data-key="{key}"] .chev'))
    c.wait_for("!!document.querySelector('.cell-panel')", 10)
    for name in ('Registration', 'Position shift'):
        js(c, "window.__openSection(%s)" % json.dumps(name))
        time.sleep(0.3)
    return key


def switch_on(c: Chrome, key: str, seconds: float = 60) -> bool:
    """Tick Show position shift in the panel; true once the renderer draws the layer."""
    js(c, """(() => { const s = window.__section('Position shift');
      const b = s && s.querySelector('.switch-row input[type=checkbox]'); if (b && !b.checked) b.click(); })()""")
    ok = c.wait_for('window.sbfsem.renderer.shifts.has(%s)' % json.dumps(key), seconds)
    js(c, "window.__openSection('Position shift'); window.__openSection('Registration')")
    c.settle(3)
    return ok


def apply(c: Chrome, key: str, changes: dict) -> None:
    """Change the cell's layer settings the way the panel does (saved, redrawn)."""
    js(c, "window.sbfsem.shifts.hooks.applyDisplay(%s.neurons.get(%s), %s)"
       % (STATE, json.dumps(key), json.dumps(changes)), wait=True)
    c.settle(3)


# -- panel ---------------------------------------------------------------------------------------

def s_panel(c: Chrome, r: Report, ctx):
    r.start('panel: controls and status chips, no standing lines of facts')
    key = start(c, ctx)
    ctx.key = key
    on = switch_on(c, key)
    r.check('ticking Show position shift draws the layer', on)
    got = js(c, """(() => {
      const out = {};
      for (const name of ['Registration', 'Position shift']) {
        const s = window.__section(name);
        const body = s && s.querySelector('.section-body');
        out[name] = body ? {
          notes: [...body.querySelectorAll('p.note, .corr-facts')].map((p) => p.textContent),
          chips: [...body.querySelectorAll('.status-chip')].filter((e) => !e.hidden)
            .map((e) => ({ text: e.textContent, help: (e.dataset.help || '').length, delay: e.dataset.helpDelay })),
          switches: [...body.querySelectorAll('.switch-row')].map((e) => e.dataset.reg || 'shift'),
        } : null;
      }
      return out; })()""")
    reg = got.get('Registration') or {}
    shift = got.get('Position shift') or {}
    r.check('the Registration section prints no line of facts or notes', reg and not reg['notes'], reg)
    r.check('the Position shift section prints no line of facts or notes', shift and not shift['notes'], shift)
    r.check('Registration has its two switches, each in a row with room for a chip',
            reg.get('switches') == ['transforms', 'correction'], reg.get('switches'))
    r.check('every chip carries its facts as 2 s hover help',
            bool(reg.get('chips')) and bool(shift.get('chips'))
            and all(ch['help'] > 20 and ch['delay'] == '2000' for ch in reg['chips'] + shift['chips']),
            {'reg': reg.get('chips'), 'shift': shift.get('chips')})
    facts = js(c, """(() => { const chip = window.__section('Position shift').querySelector('.status-chip');
      return chip ? chip.dataset.help : ''; })()""")
    r.check('the shift chip\'s hover has the facts the panel used to print (counts, median, 95%, max)',
            all(w in (facts or '') for w in ('moved shown', 'median', '95%', 'max')), facts)


# -- help ------------------------------------------------------------------------------------------

def hover(c: Chrome, selector: str):
    """Rest the pointer on an element's first line of text (a label that wraps is two boxes).

    The scroll that brings it into view is let finish first: a scroll puts any help away.
    """
    c.ev("document.querySelector(%s)?.scrollIntoView({ block: 'center' })" % json.dumps(selector))
    time.sleep(0.8)
    spot = c.ev("""(() => { const e = document.querySelector(%s); if (!e) return null;
      const r = e.getClientRects()[0];
      return r ? [r.left + Math.min(r.width / 2, 40), r.top + r.height / 2] : null; })()""" % json.dumps(selector))
    if not spot:
        return None
    c.move(spot[0] - 3, spot[1])
    c.move(spot[0], spot[1])
    return spot


def s_help(c: Chrome, r: Report, ctx):
    r.start('help: a drawing above the words, after a 2 s hover')
    key = getattr(ctx, 'key', None) or start(c, ctx)
    ctx.key = key
    js(c, "window.__section('Registration').querySelector('[data-reg=correction] label').dataset.probe = 'corr'")
    spot = hover(c, '[data-probe="corr"]')
    under = js(c, """(() => { const e = document.elementFromPoint(%f, %f);
      const h = e && e.closest('[data-help]'); return [e && e.outerHTML.slice(0, 80), h && h.dataset.probe]; })()"""
               % tuple(spot or (0, 0)))
    r.check('the pointer rests on the switch\'s words', bool(under and under[1] == 'corr'), [spot, under])
    time.sleep(1.2)
    early = js(c, "!!document.querySelector('.help-pop')")
    r.check('nothing shows before 2 s', not early)
    time.sleep(1.3)
    pop = js(c, """(() => { const p = document.querySelector('.help-pop'); if (!p) return null;
      const svg = p.querySelector('.help-fig svg'); const b = p.getBoundingClientRect();
      return { svg: !!svg, kids: svg ? svg.querySelectorAll('*').length : 0, text: p.textContent,
               fig: svg ? svg.getBoundingClientRect().width : 0, w: b.width, h: b.height,
               inside: b.left >= 0 && b.top >= 0 && b.right <= innerWidth && b.bottom <= innerHeight }; })()""")
    r.check('after 2 s the section correction switch shows its help with a drawing',
            bool(pop and pop['svg'] and pop['kids'] > 20), pop)
    text = (pop or {}).get('text', '')
    r.check('the help says what the corrections service gives, and what it did for this cell',
            'corrections service gives' in text and 'This cell:' in text, text[-300:])
    r.check('the pop is inside the window and its drawing is drawn at its size',
            bool(pop and pop['inside'] and pop['fig'] > 200), pop)
    if ctx.shots:
        (ctx.shots / 'help-correction.png').write_bytes(c.shot())
    c.move(5, 5)
    time.sleep(0.3)
    # A Position shift control.
    if not js(c, 'window.sbfsem.renderer.shifts.has(%s)' % json.dumps(key)):
        switch_on(c, key)
    js(c, """[...window.__section('Position shift').querySelectorAll('label.check')]
      .find((l) => l.textContent.trim() === 'Arrows').dataset.probe = 'arrows'""")
    hover(c, '[data-probe="arrows"]')
    time.sleep(2.4)
    fig = js(c, "(() => { const p = document.querySelector('.help-pop'); return p ? !!p.querySelector('.help-fig svg') : null; })()")
    r.check('the Arrows switch\'s help has its drawing too', fig is True, fig)
    if ctx.shots:
        (ctx.shots / 'help-arrows.png').write_bytes(c.shot())
    c.move(5, 5)
    time.sleep(0.3)


# -- arrows ----------------------------------------------------------------------------------------

def s_arrows(c: Chrome, r: Report, ctx):
    r.start('arrows: wide lines, measured on the screen')
    key = getattr(ctx, 'key', None) or start(c, ctx)
    ctx.key = key
    if not js(c, 'window.sbfsem.renderer.shifts.has(%s)' % json.dumps(key)):
        switch_on(c, key)
    info = js(c, """(() => { const a = window.__arrows(%s); if (!a) return null;
      return { type: a.type, material: a.material.type, clipping: a.material.clipping,
               fat: a.geometry.getAttribute('position').count, pick: a.userData.pick.getAttribute('position').count }; })()"""
              % json.dumps(key))
    r.check('the arrows are a mesh drawn by a ShaderMaterial that clips', bool(
        info and info['type'] == 'Mesh' and info['material'] == 'ShaderMaterial' and info['clipping']), info)
    r.check('nine vertices an arrow on the screen, its shaft (two) kept for the menu',
            bool(info and info['fat'] * 2 == info['pick'] * 9 and info['pick'] > 0), info)
    # A shift is exaggerated only up to Full color at (2.6.3), so with Auto c1178's longest arrow is
    # about 11 um, 26 px across the framed cell: too short to measure a shaft across. Full color at 5 um
    # exaggerates every move again for this section (the longest at the 30 um cap).
    apply(c, key, {'shiftColorMax': 5})
    # Only the layer on a plain ground: the surface and the axes out of the way.
    js(c, """(() => { const R = window.sbfsem.renderer; R.meshes.get(%s).visible = false; R.setAxesVisible(false);
      window.__frameLayer(%s); })()""" % (json.dumps(key), json.dumps(key)))
    c.settle(4)
    widths = {}
    for w in (1, 2.5, 6):
        js(c, "window.sbfsem.renderer.setShiftStyle(%s, { width: %s })" % (json.dumps(key), w))
        c.settle(2)
        widths[w] = js(c, "window.__shaftWidth(%s)" % json.dumps(key))
    r.check('the longest arrow is long enough on the screen to measure', bool(widths[2.5] and widths[2.5]['length'] > 40),
            widths[2.5])
    got = {w: (v or {}).get('width') for w, v in widths.items()}
    r.check('at the default 2.5 px a shaft is 2 to 4 px across (the old lines were one)',
            got[2.5] is not None and 2 <= got[2.5] <= 4.5, got)
    r.check('at 6 px it is 5 to 7.5 px across, and at 1 px about one', got[6] is not None and 5 <= got[6] <= 7.5
            and got[1] is not None and got[1] <= 2, got)
    lit = {}
    for w in (1, 6):
        js(c, "window.sbfsem.renderer.setShiftStyle(%s, { width: %s })" % (json.dumps(key), w))
        # The longest arrow alone, as for the width.
        lit[w] = js(c, """(() => { const ends = window.__arrowEnds(%s);
          const len = (e) => Math.hypot(e.head[0] - e.tail[0], e.head[1] - e.tail[1]);
          const best = ends.reduce((a, b) => (len(b) > len(a) ? b : a));
          window.sbfsem.renderer.setShiftWindow(%s, { arrows: { first: best.i * 6, count: 6 } });
          const n = window.__litCount(); window.sbfsem.renderer.setShiftWindow(%s, null); return n; })()"""
                    % (json.dumps(key), json.dumps(key), json.dumps(key)))
    r.check('six times the width lights at least three times the pixels', lit[1] and lit[6] >= 3 * lit[1], lit)
    js(c, "window.sbfsem.renderer.setShiftStyle(%s, { width: 2.5 })" % json.dumps(key))
    c.settle(2)
    if ctx.shots:
        (ctx.shots / 'arrows-close.png').write_bytes(c.shot())
    # What the 1 px lines did, the wide ones do: Flip Z, the orthographic camera, a volume
    # offset (each measured where the arrow's own matrix puts it), and the Z range.
    q = json.dumps(key)
    source = js(c, "window.sbfsem.state.neurons.get(%s).source" % q)
    views = {}
    for name, setup, undo in (
        ('flip Z', 'R.setFlipZ(true)', 'R.setFlipZ(false)'),
        ('orthographic', 'R.setOrthographic(true)', 'R.setOrthographic(false)'),
        ('volume offset', 'R.setVolumeOffset(%s, [40, -25, 3])' % json.dumps(source),
         'R.setVolumeOffset(%s, null)' % json.dumps(source)),
    ):
        js(c, "(() => { const R = window.sbfsem.renderer; %s; window.__frameLayer(%s); })()" % (setup, q))
        c.settle(3)
        views[name] = (js(c, "window.__shaftWidth(%s)" % q) or {}).get('width')
        js(c, "(() => { const R = window.sbfsem.renderer; %s; window.__frameLayer(%s); })()" % (undo, q))
        c.settle(2)
    r.check('under Flip Z, the orthographic camera and a volume offset the arrows are as wide, where they belong',
            all(v is not None and 2 <= v <= 4.5 for v in views.values()), views)
    zs = js(c, """(() => { const a = window.__arrows(%s).userData.pick.getAttribute('position').array;
      let lo = Infinity, hi = -Infinity; for (let i = 2; i < a.length; i += 3) { lo = Math.min(lo, a[i]); hi = Math.max(hi, a[i]); }
      return [lo, hi]; })()""" % q)
    before = js(c, 'window.__litCount()')
    js(c, "window.sbfsem.renderer.setZHide(%s, { min: %f, max: %f })" % (q, zs[1] + 5, zs[1] + 10))
    c.settle(2)
    cut = js(c, 'window.__litCount()')
    js(c, "window.sbfsem.renderer.setZHide(%s, {})" % q)
    c.settle(2)
    r.check('a Z range above every arrow cuts them all away (the material clips)', before > 200 and cut < 20,
            [before, cut, zs])
    # The right-click menu still finds an arrow, on its shaft.
    mid = (widths[2.5] or {}).get('mid')
    if mid:
        found = js(c, """(() => { const R = window.sbfsem.renderer; const b = R.webgl.domElement.getBoundingClientRect();
          const ratio = R.webgl.domElement.width / b.width;
          return R.contextCandidatesAt(b.left + %f / ratio, b.top + %f / ratio).map((x) => x.kind); })()""" % (mid[0], mid[1]))
        r.check('a right click on a shaft lists the cell\'s position shift', 'shifts' in (found or []), found)
    # The inverted (light) viewport recolors the arrows in place.
    before = js(c, "Array.from(window.__arrows(%s).geometry.getAttribute('lineColor').array.slice(0, 3))" % json.dumps(key))
    js(c, "window.sbfsem.renderer.setInverted(true)")
    after = js(c, "Array.from(window.__arrows(%s).geometry.getAttribute('lineColor').array.slice(0, 3))" % json.dumps(key))
    js(c, "window.sbfsem.renderer.setInverted(false)")
    r.check('inverting the viewport recolors the wide arrows', before != after, [before, after])
    js(c, "(() => { const R = window.sbfsem.renderer; R.meshes.get(%s).visible = true; R.setAxesVisible(true); })()"
       % json.dumps(key))
    apply(c, key, {'shiftColorMax': None})


# -- slice -----------------------------------------------------------------------------------------

def s_slice(c: Chrome, r: Report, ctx):
    r.start('slice: one slice at a time still limits what is drawn')
    key = getattr(ctx, 'key', None) or start(c, ctx)
    ctx.key = key
    if not js(c, 'window.sbfsem.renderer.shifts.has(%s)' % json.dumps(key)):
        switch_on(c, key)
    js(c, """(() => { const R = window.sbfsem.renderer; R.meshes.get(%s).visible = false; R.setAxesVisible(false);
      window.__frameLayer(%s); })()""" % (json.dumps(key), json.dumps(key)))
    c.settle(3)
    every = js(c, 'window.__litCount()')
    # The slice plane is a faint sheet over everything: the count is of the arrows alone.
    hide_plane = "(window.__group(%s).userData.plane || []).forEach((p) => { p.visible = false; })" % json.dumps(key)
    total = js(c, "window.__arrows(%s).geometry.getAttribute('position').count" % json.dumps(key))
    apply(c, key, {'shiftSlice': True, 'shiftWindow': 0, 'shiftMinSlice': 0})
    layer = js(c, """(() => { const g = window.__group(%s); const l = g.userData.layer; const a = window.__arrows(%s);
      return { n: l.sel.n, range: [a.geometry.drawRange.start, a.geometry.drawRange.count],
               pick: [a.userData.pick.drawRange.start, a.userData.pick.drawRange.count],
               plane: (g.userData.plane || []).some((p) => p.visible) }; })()""" % (json.dumps(key), json.dumps(key)))
    # A redraw of the layer gives every part of the cell its visibility back: the surface goes again.
    js(c, "window.sbfsem.renderer.meshes.get(%s).visible = false; %s" % (json.dumps(key), hide_plane))
    one = js(c, 'window.__litCount()')
    r.check('the wide mesh draws a window of whole arrows, the pick segments the same window',
            bool(layer and layer['range'][1] is not None and layer['range'][1] % 9 == 0
                 and layer['range'][1] * 2 == layer['pick'][1] * 9 and layer['range'][0] * 2 == layer['pick'][0] * 9),
            layer)
    r.check('a single section draws fewer arrows than every section', bool(layer and layer['range'][1] < total),
            [layer, total])
    r.check('and lights fewer pixels', one < every, [one, every])
    r.check('the slice plane is shown', bool(layer and layer['plane']))
    # A step moves the window: a draw range, nothing uploaded.
    versions = js(c, "window.__arrows(%s).geometry.getAttribute('position').version" % json.dumps(key))
    c.ev("window.sbfsem.renderer.webgl.domElement.focus()")
    c.key(']')
    c.key(']')
    after = js(c, """(() => { const a = window.__arrows(%s);
      return [a.geometry.drawRange.start, a.geometry.drawRange.count, a.geometry.getAttribute('position').version]; })()"""
               % json.dumps(key))
    r.check('stepping the slice moves the draw range and uploads nothing',
            after[2] == versions and (after[0], after[1]) != tuple(layer['range']), [layer['range'], after])
    apply(c, key, {'shiftSlice': False})
    js(c, "(() => { const R = window.sbfsem.renderer; R.meshes.get(%s).visible = true; R.setAxesVisible(true); })()"
       % json.dumps(key))


# -- legend ----------------------------------------------------------------------------------------

def s_legend(c: Chrome, r: Report, ctx):
    r.start('legend: shown with the layer, matching its colors')
    key = getattr(ctx, 'key', None) or start(c, ctx)
    ctx.key = key
    if not js(c, 'window.sbfsem.renderer.shifts.has(%s)' % json.dumps(key)):
        switch_on(c, key)
    c.settle(2)
    shown = js(c, """(() => { const l = document.querySelector('#viewport .shift-legend');
      if (!l || l.hidden) return null;
      return { title: l.querySelector('.shift-legend-title')?.textContent,
               ramp: !!l.querySelector('.shift-legend-ramp'), ticks: [...l.querySelectorAll('.shift-legend-ticks span')].map((s) => s.textContent),
               bg: l.querySelector('.shift-legend-ramp')?.style.background || '' }; })()""")
    pair = js(c, "window.__group(%s).userData.layer.pair" % json.dumps(key))
    names = {'correction': 'Section correction', 'transforms': 'Current transforms', 'both': 'Both'}
    r.check('the legend is in the viewport while the layer is drawn', bool(shown), shown)
    r.check('it names the pair drawn', bool(shown and shown['title'] == names.get(pair)), [shown, pair])
    r.check('Magnitude: the ramp with three ticks, the last in µm', bool(
        shown and shown['ramp'] and len(shown['ticks']) == 3 and shown['ticks'][2].endswith('µm')), shown)
    boxes = {name: rect(c, sel) for name, sel in (
        ('legend', '#viewport .shift-legend'), ('pick', '.picked'), ('chip', '#shift-chip'),
        ('footer', '#view-render .view-footer .viewport-tools'), ('hint', '#nav-hint'))}
    clash = [name for name in ('pick', 'chip', 'footer', 'hint') if overlaps(boxes['legend'], boxes[name])]
    r.check('it covers neither the pick readout, the chip, the hint nor the toolbar', not clash, boxes)
    apply(c, key, {'shiftColor': 'direction'})
    wheel = js(c, """(() => { const l = document.querySelector('#viewport .shift-legend');
      const w = l && l.querySelector('.shift-legend-wheel'); return w ? getComputedStyle(w).backgroundImage.slice(0, 40) : null; })()""")
    r.check('Direction: the wheel, a conic gradient', bool(wheel and 'conic-gradient' in wheel), wheel)
    if ctx.shots:
        (ctx.shots / 'legend-direction.png').write_bytes(c.shot())
    apply(c, key, {'shiftColor': 'cell'})
    swatch = js(c, "!!document.querySelector('#viewport .shift-legend .shift-legend-swatch')")
    r.check('Cell color: a swatch of the cell\'s color', swatch is True)
    apply(c, key, {'shiftColor': 'magnitude'})
    dark = js(c, "document.querySelector('#viewport .shift-legend-ramp').style.background")
    js(c, 'window.sbfsem.renderer.setInverted(true)')
    c.settle(2)
    light = js(c, "document.querySelector('#viewport .shift-legend-ramp').style.background")
    js(c, 'window.sbfsem.renderer.setInverted(false)')
    r.check('the inverted viewport\'s legend shows the light ramp the arrows take there', dark != light, [dark, light])
    js(c, """(() => { const b = window.__section('Position shift').querySelector('.switch-row input'); if (b.checked) b.click(); })()""")
    c.wait_for('!window.sbfsem.renderer.shifts.has(%s)' % json.dumps(key), 20)
    c.settle(2)
    gone = js(c, "(() => { const l = document.querySelector('#viewport .shift-legend'); return !l || l.hidden; })()")
    r.check('it goes when the layer is turned off', gone is True)
    switch_on(c, key)


# -- rc1 ---------------------------------------------------------------------------------------------

def s_rc1(c: Chrome, r: Report, ctx):
    r.start('rc1: no transforms switch where the volume has none')
    key = getattr(ctx, 'key', None) or start(c, ctx)
    ctx.key = key
    has = js(c, "!!window.__section('Registration').querySelector('[data-reg=transforms]')")
    r.check('c1178 (Inferior Monkey) has the transforms switch', has is True)
    if not js(c, 'window.sbfsem.renderer.shifts.has(%s)' % json.dumps(key)):
        switch_on(c, key)
    between = js(c, """(() => { const b = window.__section('Position shift').querySelector('.section-body');
      return [...b.querySelectorAll(':scope > label')].map((l) => l.textContent); })()""")
    r.check('and its Position shift offers Between', 'Between' in (between or []), between)
    rc1 = load(c, RC1_CELL, source='RC1')
    js(c, "document.querySelectorAll('.neuron-item.expanded .chev').forEach((e) => e.click())")
    js(c, "document.querySelector('.neuron-item[data-key=\"%s\"] .chev').click()" % rc1)
    c.wait_for("!!document.querySelector('.neuron-item.expanded[data-key=\"%s\"] .cell-panel')" % rc1, 10)
    for name in ('Registration', 'Position shift'):
        js(c, "window.__openSection(%s)" % json.dumps(name))
        time.sleep(0.3)
    reg = js(c, """(() => { const s = window.__section('Registration'); const b = s && s.querySelector('.section-body');
      return b ? { rows: [...b.querySelectorAll('.switch-row')].map((e) => e.dataset.reg), text: b.textContent } : null; })()""")
    r.check('RC1 c410 has no transforms switch, only the section correction',
            bool(reg and reg['rows'] == ['correction'] and 'current section transforms' not in reg['text']), reg)
    on = switch_on(c, rc1, 90)
    between = js(c, """(() => { const b = window.__section('Position shift').querySelector('.section-body');
      return { labels: [...b.querySelectorAll(':scope > label')].map((l) => l.textContent),
               chip: b.querySelector('.status-chip')?.textContent }; })()""")
    r.check('its Position shift has no Between select (the correction is the only pair)',
            'Between' not in (between or {}).get('labels', []), [on, between])
    if ctx.shots:
        (ctx.shots / 'rc1-panel.png').write_bytes(c.shot())
    summary = js(c, "document.querySelector('.neuron-item[data-key=\"%s\"] .cell-section .sec-summary') && "
                    "window.__section('Registration').querySelector('.sec-summary').textContent" % rc1)
    r.check('its Registration header says nothing about transforms', bool(summary) and 'stored (' not in summary
            and 'current' not in summary, summary)
    js(c, "document.querySelector('.neuron-item[data-key=\"%s\"] .chev').click()" % rc1)


# -- compare ---------------------------------------------------------------------------------------

def s_compare(c: Chrome, r: Report, ctx):
    r.start('compare: a pane draws the layer wide, with its legend')
    key = getattr(ctx, 'key', None) or start(c, ctx)
    ctx.key = key
    js(c, "document.querySelector('.tab[data-view=\"compare\"]').click(); true")
    c.settle(3)
    js(c, "window.sbfsem.compare.setPaneCount(1); true")
    c.settle(2)
    js(c, "document.getElementById('compare-build').click(); true")
    ok = c.wait_for("window.sbfsem.compare.panes[0].renderer.meshes.size > 0 && !window.sbfsem.compare.running", 400)
    r.check('the pane has built its surface', ok)
    js(c, "(() => { const p = window.sbfsem.compare.panes[0]; if (!p.shift) p.shiftBox.click(); })()")
    drawn = c.wait_for("window.sbfsem.compare.panes[0].renderer.shifts.size > 0", 60)
    c.settle(3)
    got = js(c, """(() => { const p = window.sbfsem.compare.panes[0]; const g = [...p.renderer.shifts.values()][0];
      const a = g && g.children.find((x) => x.userData.role === 'arrows');
      const l = p.viewport.querySelector('.shift-legend');
      const chip = document.querySelector('#compare-shifts .status-chip');
      return { material: a && a.material.type, legend: !!l && !l.hidden, title: l && l.querySelector('.shift-legend-title')?.textContent,
               chip: chip && !chip.hidden ? chip.textContent : null, chipHelp: chip ? (chip.dataset.help || '').length : 0,
               facts: !!document.querySelector('#compare-shifts .shift-facts') }; })()""")
    r.check('the pane draws the arrows wide', bool(drawn and got['material'] == 'ShaderMaterial'), got)
    r.check('the pane has a legend', bool(got['legend']), got)
    r.check('the strip\'s facts are a chip with its hover, not a line', bool(got['chip'] and got['chipHelp'] > 20
            and not got['facts']), got)
    if ctx.shots:
        (ctx.shots / 'compare-pane.png').write_bytes(c.shot())
    js(c, "(() => { const p = window.sbfsem.compare.panes[0]; if (p.shift) p.shiftBox.click(); })()")
    c.settle(2)
    gone = js(c, "(() => { const l = window.sbfsem.compare.panes[0].viewport.querySelector('.shift-legend'); return !l || l.hidden; })()")
    r.check('unticking the pane takes its legend away', gone is True)
    js(c, "document.querySelector('.tab[data-view=\"render\"]').click(); true")
    c.settle(2)


# -- layout ----------------------------------------------------------------------------------------

def s_layout(c: Chrome, r: Report, ctx):
    r.start('layout: screenshots at four widths in both themes')
    if not ctx.shots:
        r.skip('screenshots', 'no --shots folder')
        return
    for scheme in ('dark', 'light'):
        for width in (900, 1140, 1440, 1920):
            c.emulate('mac', width=width, height=900, scheme=scheme)
            key = start(c, ctx)
            c.scheme(scheme)        # boot() emulates the dark theme
            switch_on(c, key)
            js(c, "window.__section('Position shift').scrollIntoView({ block: 'start' })")
            c.settle(4)
            (ctx.shots / f'layout-{width}-{scheme}.png').write_bytes(c.shot())
            boxes = {name: rect(c, sel) for name, sel in (
                ('legend', '#viewport .shift-legend'), ('pick', '.picked'), ('chip', '#shift-chip'),
                ('footer', '#view-render .view-footer .viewport-tools'), ('hint', '#nav-hint'))}
            clash = [n for n in ('pick', 'chip', 'footer', 'hint') if overlaps(boxes['legend'], boxes[n])]
            r.check(f'{width} px, {scheme}: the legend covers nothing', bool(boxes['legend']) and not clash, boxes)
            rows = js(c, """[...document.querySelectorAll('.switch-row')].map((row) => {
              const r = row.getBoundingClientRect(); return Math.round(r.height); })""")
            r.check(f'{width} px, {scheme}: each switch and its chip take at most three lines', all(h <= 64 for h in rows), rows)
            if width == 900:
                # The slice's stepper widens the chip onto a second row here: the legend moves up with it.
                apply(c, key, {'shiftSlice': True})
                time.sleep(0.5)
                c.settle(3)
                boxes = {name: rect(c, sel) for name, sel in (
                    ('legend', '#viewport .shift-legend'), ('chip', '#shift-chip'))}
                (ctx.shots / f'layout-{width}-{scheme}-slice.png').write_bytes(c.shot())
                r.check(f'{width} px, {scheme}, one slice: the legend sits above the taller chip',
                        bool(boxes['legend'] and boxes['chip']) and not overlaps(boxes['legend'], boxes['chip']), boxes)
                apply(c, key, {'shiftSlice': False})
            if width == 1440:
                # A help pop with its drawing, and a chip's facts, in this theme.
                for name, probe in (('between', "[...window.__section('Position shift').querySelectorAll('label')]"
                                                ".find((l) => l.textContent === 'Between')"),
                                    ('chip', "window.__section('Registration').querySelector('.status-chip')")):
                    js(c, "(() => { const e = %s; if (e) e.dataset.probe = %s; })()" % (probe, json.dumps(name)))
                    hover(c, '[data-probe="%s"]' % name)
                    time.sleep(2.4)
                    shown = js(c, "!!document.querySelector('.help-pop')")
                    r.check(f'{scheme}: the {name} help shows', shown is True)
                    (ctx.shots / f'help-{name}-{scheme}.png').write_bytes(c.shot())
                    c.move(5, 5)
                    time.sleep(0.4)
    c.emulate('mac', width=1440, height=900, scheme='dark')


# -- focus ----------------------------------------------------------------------------------------

FOCUS_PICK = r"""(() => {
  const R = window.sbfsem.renderer; const rec = window.sbfsem.state.neurons.get(%s);
  const layer = window.sbfsem.shifts.layerOf(rec); const f = layer.field;
  const rings = R.discs.get(rec.key)?.userData.rings || [];
  const byId = new Map(rings.map((n) => [n.id, n]));
  const cand = (i) => ({ kind: 'annotation', key: rec.key, locationId: f.id[i], point: [f.cx[i], f.cy[i], f.z[i]],
    section: f.section[i], zUm: f.z[i] });
  // A disc moved 0.1 to 0.24 um: under the default 0.25 um, so the layer leaves its arrow out.
  let low = -1;
  for (let i = 0; i < f.n; i += 1) if (!f.curve[i] && f.mag[i] > 0.1 && f.mag[i] < 0.24 && byId.has(f.id[i])) { low = i; break; }
  if (low < 0) return null;
  const shown = new Set([...layer.sel.rows].map((r) => f.id[r]));
  let up = -1;
  for (let i = 0; i < f.n; i += 1) if (!f.curve[i] && f.section[i] > f.section[low] + 2 && f.mag[i] > 0.3 && byId.has(f.id[i])) { up = i; break; }
  window.__focusCands = { low: cand(low), up: up >= 0 ? cand(up) : null };
  return { id: f.id[low], mag: f.mag[low], inLayer: shown.has(f.id[low]), up: up >= 0 ? f.id[up] : null };
})()"""

FOCUS_STATE = r"""(() => {
  const g = window.__group(%s); if (!g) return null;
  const focus = g.children.filter((c) => c.userData.role === 'focus');
  const arrows = g.children.find((c) => c.userData.role === 'arrows');
  const color = (c) => { const a = c.geometry.getAttribute('lineColor').array; return [a[0], a[1], a[2]].map((v) => +v.toFixed(3)); };
  return { slots: focus.map((c) => c.userData.slot), dim: g.userData.dim ?? 1,
           opacity: arrows ? +arrows.material.opacity.toFixed(3) : null, visible: arrows ? arrows.visible : null,
           onTop: focus.every((c) => c.material.depthTest === false),
           colors: focus.map(color), tint: (() => { const t = window.sbfsem.renderer._tint('primary', %s); return [t.r, t.g, t.b].map((v) => +v.toFixed(3)); })() };
})()"""


def s_focus(c: Chrome, r: Report, ctx):
    r.start('focus: the selected annotation\'s arrow in its marker\'s color, the others dimmed')
    # A clean page: the sections before it leave other tabs, cells and thresholds behind.
    key = start(c, ctx)
    ctx.key = key
    q = json.dumps(key)
    if not js(c, 'window.sbfsem.renderer.shifts.has(%s)' % q):
        switch_on(c, key)
    # The rings are what a triple click selects: shown, so the selection draws its yellow ring.
    js(c, "window.__openSection('Annotations')")
    time.sleep(0.3)
    js(c, """(() => { const lab = [...document.querySelectorAll('.cell-panel label.check')]
        .find((l) => l.textContent.trim().startsWith('Show annotations'));
        const b = lab && lab.querySelector('input'); if (b && !b.checked) b.click(); })()""")
    c.wait_for('window.sbfsem.renderer.discs.has(%s)' % q, 60)
    c.settle(3)
    plan = js(c, FOCUS_PICK % q)
    r.check('c1178 has a disc moved 0.1 to 0.24 um, which the default threshold leaves out of the layer',
            bool(plan) and not plan['inLayer'], plan)
    if not plan:
        return
    before = js(c, FOCUS_STATE % (q, q))
    js(c, 'window.sbfsem.renderer.select(window.__focusCands.low); true')
    c.settle(2)
    on = js(c, FOCUS_STATE % (q, q))
    r.check('before a selection nothing is focused and nothing dimmed', before and not before['slots']
            and before['dim'] == 1 and abs(before['opacity'] - 0.9) < 1e-6, before)
    r.check('selecting it draws its arrow and its ring where it was, an edge under each, all on top',
            on and on['slots'] == ['primary'] * 4 and on['onTop'], on)
    r.check('in the selection\'s own color', on and on['colors'] and on['colors'][1] == on['tint']
            and on['colors'][3] == on['tint'], on)
    r.check('and the other arrows at Others when selected (0.06, almost nothing) of their opacity',
            on and on['dim'] == 0.06 and abs(on['opacity'] - 0.054) < 1e-6, on)
    # On the screen: the focused arrow lights pixels of the selection's color that are not there without it.
    # Close in on it, then let the page draw: the selection's tube is a few pixels wide at the zoom it
    # was drawn for, and the frame loop draws it again for the new one.  Counted in the frame before
    # that, it is the cell view's tube seen from 25 um, several times as wide, and the arrow lies inside it.
    js(c, """(() => { const R = window.sbfsem.renderer; R.meshes.get(%s).visible = false;
      const p = window.__focusCands.low.point; const w = R.toWorld(p, R._sourceOf(%s));
      R.camera.position.set(w[0], w[1], w[2] + 25); R.camera.up.set(0, 1, 0); R.controls.target.set(w[0], w[1], w[2]);
      R.camera.near = 0.05; R.camera.far = 500; R.camera.updateProjectionMatrix(); R.controls.update(); })()""" % (q, q))
    c.settle(1)
    lit = js(c, """(() => { const R = window.sbfsem.renderer;
      const t = R._tint('primary', %s).clone().convertLinearToSRGB(); const want = [t.r * 255, t.g * 255, t.b * 255];
      const count = () => { const px = window.__pixels(); let n = 0;
        for (let i = 0; i < px.data.length; i += 4) {
          if (Math.abs(px.data[i] - want[0]) + Math.abs(px.data[i + 1] - want[1]) + Math.abs(px.data[i + 2] - want[2]) < 60) n += 1; }
        return n; };
      const withIt = count(); const marks = R.shifts.get(%s).children.filter((c) => c.userData.role === 'focus');
      marks.forEach((m) => { m.visible = false; }); const without = count(); marks.forEach((m) => { m.visible = true; });
      R.meshes.get(%s).visible = true; return [withIt, without]; })()""" % (q, q, q))
    r.check('on the screen the arrow lights pixels of that color that are not lit without it',
            bool(lit) and lit[0] > lit[1] + 30, lit)
    if plan.get('up'):
        js(c, "window.sbfsem.renderer.setMarker('above', window.__focusCands.up); true")
        c.settle(2)
        two = js(c, FOCUS_STATE % (q, q))
        red = js(c, """(() => { const t = window.sbfsem.renderer._tint('above', %s); return [t.r, t.g, t.b].map((v) => +v.toFixed(3)); })()""" % q)
        r.check('a red marker above it gets its own arrow, in red', two and two['slots'].count('above') == 4
                and red in two['colors'], [two and two['slots'], red])
    # The slider: 0 hides the others; deselecting puts everything back.
    apply(c, key, {'shiftFocusDim': 0})
    hidden = js(c, FOCUS_STATE % (q, q))
    r.check('Others when selected at 0 hides the other arrows, the focused ones stay',
            hidden and hidden['visible'] is False and 'primary' in hidden['slots'], hidden)
    apply(c, key, {'shiftFocusDim': 0.06})
    js(c, 'window.sbfsem.renderer.clearSelection(); true')
    c.settle(2)
    off = js(c, FOCUS_STATE % (q, q))
    r.check('deselecting takes the focused arrows away and the others back to their opacity',
            off and not off['slots'] and off['dim'] == 1 and off['visible'] and abs(off['opacity'] - 0.9) < 1e-6, off)
    row = js(c, """(() => { const ths = [...document.querySelectorAll('#marker-table th')].map((t) => t.textContent);
      return ths; })()""")
    js(c, 'window.sbfsem.renderer.select(window.__focusCands.low); true')
    c.settle(2)
    table = js(c, """(() => { const t = document.getElementById('marker-table'); if (!t || t.hidden) return null;
      const th = [...t.querySelectorAll('th')].map((x) => x.textContent); const i = th.indexOf('Shift µm');
      const td = t.querySelector('tbody tr[data-slot="primary"]').children[i]; return [i, td && td.textContent]; })()""")
    r.check('the marker table gives the selected annotation\'s true shift', bool(table) and table[0] > 0
            and abs(float(table[1]) - plan['mag']) < 0.006, [table, plan['mag'], row])
    js(c, 'window.sbfsem.renderer.clearSelection(); true')


# -- manual moves ----------------------------------------------------------------------------------

def s_moves(c: Chrome, r: Report, ctx):
    r.start('moves: an annotation moved by hand, applied, listed, kept and reset')
    key = start(c, ctx)
    ctx.key = key
    q = json.dumps(key)
    js(c, "window.__openSection('Annotations')")
    time.sleep(0.3)
    js(c, """(() => { const lab = [...document.querySelectorAll('.cell-panel label.check')]
        .find((l) => l.textContent.trim().startsWith('Show annotations'));
        const b = lab && lab.querySelector('input'); if (b && !b.checked) b.click(); })()""")
    c.wait_for('window.sbfsem.renderer.discs.has(%s)' % q, 60)
    c.settle(3)
    picked = js(c, """(async () => { const R = window.sbfsem.renderer; const rec = window.sbfsem.state.neurons.get(%s);
      const nodes = await window.sbfsem.api.nodes(rec.source, rec.id);
      const n = nodes.find((x) => x.radius_um > 0.5 && x.type_code === 1 && !x.manual_shift_um);
      R.select({ kind: 'annotation', key: rec.key, locationId: n.id, point: n.xyz_um, section: n.z_section, zUm: n.xyz_um[2] });
      R.webgl.domElement.focus(); return { id: n.id, xyz: n.xyz_um, section: n.z_section, tag: rec.summary.copy_tag }; })()""" % q, wait=True)
    # The plain copy is the volume's default setting's (Field + fit, `all.1`, for the Neitz volumes
    # since 2.17.0): its moves are kept under it, and a moved copy is named after it.
    r.check('a disc of c1178 is selected on the plain copy',
            bool(picked) and bool(picked['tag']) and '.m' not in picked['tag'], picked)
    if not picked:
        return
    base = picked['tag']
    c.key('g')
    c.settle(2)
    panel = js(c, """(() => { const p = document.getElementById('move-panel'); if (!p || p.hidden) return null;
      return { title: p.querySelector('.mv-title').textContent, where: p.querySelector('.mv-where').textContent,
               gizmo: p.querySelectorAll('.mv-gizmo line').length, axes: [...p.querySelectorAll('.mv-axis')].map((b) => b.textContent) }; })()""")
    r.check('g opens the move panel: the annotation, its X, Y, Z and section, the three axes on the gizmo, seven axis buttons',
            bool(panel) and str(picked['id']) in panel['title'] and 'section' in panel['where'] and panel['gizmo'] == 3
            and panel['axes'] == ['X', 'Y', 'Z', 'Xp', 'Yp', 'Zp', 'Shift'], panel)
    c.key('1')
    for _ in range(4):
        c.key('ArrowRight')
    c.key('3')
    c.key('ArrowRight')
    c.settle(1)
    kept = js(c, """(() => { const rec = window.sbfsem.state.neurons.get(%s); const R = window.sbfsem.renderer;
      return { store: rec.manualMoves, nudge: R._nudges.get('primary') || null,
               moved: document.querySelector('#move-panel .mv-moved').textContent }; })()""" % q)
    move = ((kept or {}).get('store') or {}).get(base, {}).get(str(picked['id']))
    r.check('four steps of 0.05 um along X and one section in Z are kept on the cell, under its setting',
            bool(move) and abs(move['dx'] - 0.2) < 1e-9 and move['dy'] == 0 and move['dz'] == 1, kept)
    r.check('the ring is drawn ahead of the render by the move, and the panel says it is not applied yet',
            bool(kept and kept['nudge']) and abs(kept['nudge'][0] - 0.2) < 1e-9 and kept['nudge'][2] > 0
            and 'not yet in the render' in kept['moved'], kept)
    # What the Apply asks for and draws, in order: the renderer's draws are timed, the requests
    # are in the resource timings, and its polls wait on the server as they do over HTTP/2.
    js(c, """(async () => { const R = window.sbfsem.renderer; window.__drawn = [];
      for (const name of ['setMesh', 'setDiscs']) {
        const f = R[name]; R[name] = function (...a) { window.__drawn.push([name, a[0], performance.now()]); return f.apply(this, a); };
      }
      (await import('/static/js/api.js')).POLL_TIMING.wait = 1;
      performance.setResourceTimingBufferSize(2000); performance.clearResourceTimings(); })()""", wait=True)
    c.key('Enter')
    applied = c.wait_for("(window.sbfsem.state.neurons.get(%s).summary.copy_tag || '').startsWith(%s)"
                         " && !window.sbfsem.moves.applying && window.sbfsem.renderer.meshes.has(%s)"
                         % (q, json.dumps(base + '.m'), q), 90)
    c.settle(3)
    order = js(c, """(async () => { const key = %s; const tag = window.sbfsem.state.neurons.get(key).summary.copy_tag;
      const res = performance.getEntriesByType('resource').map((e) => [e.name, e.startTime, e.responseEnd]);
      const of = (part) => res.filter(([u]) => u.includes(part) && u.includes('copy=' + tag));
      const polls = res.filter(([u]) => u.includes('/api/mesh-jobs/'));
      const job = res.find(([u]) => u.includes('/mesh-job?') && u.includes('copy=' + tag));
      const at = (name) => (window.__drawn.filter(([n, k]) => n === name && k === key).at(-1) || [])[2] ?? null;
      (await import('/static/js/api.js')).POLL_TIMING.wait = 0;
      return { tag, nodes: of('/nodes?').map((e) => e[1]), curves: of('/closed-curves').map((e) => e[1]),
               job: job ? job[1] : null, polls: polls.length, waited: polls.every(([u]) => u.includes('wait=1')),
               lastPoll: polls.length ? polls.at(-1)[1] : null, built: polls.length ? polls.at(-1)[2] - job[1] : null,
               setMesh: at('setMesh'), setDiscs: at('setDiscs') }; })()""" % q, wait=True)
    r.check("the moved copy's annotations and outlines are asked for once each, while its surface builds",
            bool(order) and len(order['nodes']) == 1 and len(order['curves']) == 1 and order['job'] is not None
            and order['lastPoll'] is not None
            # No later than the last poll: a surface the server holds is answered by the first
            # poll, sent in the same tick, and the two can share a 0.1 ms timestamp.
            and max(order['nodes'][0], order['curves'][0]) <= order['lastPoll'], order)
    r.check('the rings are drawn with the surface, not before it',
            bool(order) and order['setMesh'] is not None and order['setDiscs'] is not None
            and 0 <= order['setDiscs'] - order['setMesh'] < 500, order)
    r.check('the polls ask the server to wait, and are no more than one a second of the build',
            bool(order) and order['waited'] and order['polls'] <= order['built'] / 1000 + 2, order)
    after = js(c, """(async () => { const rec = window.sbfsem.state.neurons.get(%s); const R = window.sbfsem.renderer;
      const n = (await window.sbfsem.api.nodes(rec.source, rec.id)).find((x) => x.id === %d);
      return { tag: rec.summary.copy_tag, positions: rec.summary.positions, mm: rec.summary.manual_moves,
               xyz: n.xyz_um, section: n.z_section, dz: n.manual_dz, sel: R.selection && R.selection.section,
               mesh: R.meshes.has(rec.key), arrows: R.moveArrows.has(rec.key),
               moved: document.querySelector('#move-panel .mv-moved').textContent }; })()""" % (q, picked['id']), wait=True)
    r.check('Enter applies: a copy named by the moves, the surface keyed on them, the cell drawn again',
            applied and bool(after) and after['mm'] and after['mm']['n_moved'] == 1
            and after['positions'].endswith('manual:' + after['mm']['token']) and after['mesh'], after)
    r.check('the annotation is 0.2 um along X and one section up, and the selection followed it',
            bool(after) and abs(after['xyz'][0] - picked['xyz'][0] - 0.2) < 1e-6
            and after['section'] == picked['section'] + 1 and after['dz'] == 1 and after['sel'] == after['section']
            and 'not yet' not in after['moved'] and after['arrows'], after)
    js(c, "window.__openSection('Manual moves')")
    c.settle(2)
    listed = js(c, """(() => { const s = window.__section('Manual moves'); if (!s) return null;
      return { summary: s.querySelector('.sec-summary').textContent,
               rows: [...s.querySelectorAll('.mv-table tbody tr')].map((tr) => [...tr.children].map((td) => td.textContent.trim())) }; })()""")
    r.check('the Manual moves list shows the move: ID, section, +0.20 in X, +1 in Z', bool(listed) and listed['summary'] == '1'
            and len(listed['rows']) == 1 and listed['rows'][0][1] == str(picked['id'])
            and listed['rows'][0][3] == '+0.20' and listed['rows'][0][5] == '+1', listed)
    # A reload of the page brings the cell back moved (the page's saved state).
    # The check's pages forget what they stored on every load unless told to keep it (check_app CLEAR).
    js(c, "sessionStorage.setItem('__keep', '1')")
    c.ev('location.reload()')
    c.wait_for('!!window.sbfsem', 30)
    if c.wait_for("!document.getElementById('restore-banner').hidden", 15):
        js(c, "document.getElementById('restore-yes').click()")
    js(c, HELPERS)
    back = c.wait_for("!!window.sbfsem && (window.sbfsem.state.neurons.get(%s)?.summary?.copy_tag || '')"
                      ".startsWith(%s)" % (q, json.dumps(base + '.m')), 90)
    r.check('a reload of the page restores the cell with its move applied', back,
            js(c, "window.sbfsem && window.sbfsem.state.neurons.get(%s)?.summary?.copy_tag" % q))
    c.wait_for('window.sbfsem.renderer.meshes.has(%s)' % q, 90)
    reset = js(c, """(async () => { const m = window.sbfsem.moves; const rec = window.sbfsem.state.neurons.get(%s);
      await m.reset(rec); const now = window.sbfsem.state.neurons.get(%s);
      return { tag: now.summary.copy_tag, mm: now.summary.manual_moves, store: now.manualMoves,
               arrows: window.sbfsem.renderer.moveArrows.has(now.key) }; })()""" % (q, q), wait=True)
    r.check('Reset all puts the cell back on its plain copy, with no move and no arrow',
            bool(reset) and reset['tag'] == base and reset['mm'] is None and not reset['store']
            and not reset['arrows'], reset)


SLICE_STATE = r"""(() => {
  const R = window.sbfsem.renderer; const key = %s; const g = window.__group(key); const l = g.userData.layer;
  const a = window.__arrows(key); const col = a.geometry.getAttribute('lineColor').array;
  const alp = a.geometry.getAttribute('lineAlpha').array; const start = a.geometry.drawRange.start;
  const count = a.geometry.drawRange.count; const sc = R.sliceColors(key); const r3 = (v) => v.map((x) => +x.toFixed(3));
  const groups = {};
  for (let v = start; v < start + count; v += 9) {
    const k = `${alp[v].toFixed(2)}|${r3([col[v * 3], col[v * 3 + 1], col[v * 3 + 2]]).join(',')}`;
    groups[k] = (groups[k] || 0) + 1;
  }
  const plane = g.userData.plane[0].geometry.getAttribute('position').array;
  const xs = [plane[0], plane[3], plane[6]]; const ys = [plane[1], plane[4], plane[7]];
  const box = l.field.bbox;
  const center = window.sbfsem.shifts.centerOf(window.sbfsem.state.neurons.get(key));
  const ext = l.extents.get(center);
  // What the section holds: its annotations and its arrows, head to exaggerated tail.
  let frame = ext ? [...ext] : null;
  const { sel, field, scales } = l;
  for (let i = 0; i < sel.n; i += 1) {
    if (sel.section[i] !== center) continue;
    const r = sel.rows[i];
    for (const [x, y] of [[field.hx[r], field.hy[r]], [field.hx[r] - scales[i] * field.dx[r], field.hy[r] - scales[i] * field.dy[r]]]) {
      frame = frame ? [Math.min(frame[0], x), Math.min(frame[1], y), Math.max(frame[2], x), Math.max(frame[3], y)] : [x, y, x, y];
    }
  }
  return { center, groups, colors: { above: r3(sc.above), on: r3(sc.on), below: r3(sc.below) },
           plane: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
           cell: [box.max[0] - box.min[0], box.max[1] - box.min[1]], ext: ext ? [...ext] : null, frame,
           dim: g.userData.dim ?? 1, visible: g.userData.plane[0].visible };
})()"""


def s_slicecolors(c: Chrome, r: Report, ctx):
    r.start('slicecolors: the plane fits its section, the slice in the markers\' colors')
    key = start(c, ctx)
    ctx.key = key
    q = json.dumps(key)
    switch_on(c, key)
    apply(c, key, {'shiftSlice': True, 'shiftWindow': 3, 'shiftMinSlice': 0})
    st = js(c, SLICE_STATE % q)
    w = st['plane'][2] - st['plane'][0]
    h = st['plane'][3] - st['plane'][1]
    # A ganglion cell's dendrites spread through a section, so the plane is what that section holds:
    # its annotations and the arrows drawn on it, tails included (planeRect), a fraction of the cell's
    # box.  The tails count: under Field + fit a closed curve on c1178's section 1159 moves 10.9 um,
    # and its arrow's tail reaches 17 um past the annotations' 21 um.
    ew = st['ext'][2] - st['ext'][0] if st['ext'] else 0
    eh = st['ext'][3] - st['ext'][1] if st['ext'] else 0
    fr = st['frame'] or [0, 0, 0, 0]
    fw = fr[2] - fr[0]
    fh = fr[3] - fr[1]
    r.check('the plane is the size of its section\'s annotations and their arrows, not the whole cell',
            st['visible'] and bool(st['ext']) and st['plane'][0] <= fr[0] and st['plane'][2] >= fr[2]
            and st['plane'][1] <= fr[1] and st['plane'][3] >= fr[3]
            and w * h < 0.6 * st['cell'][0] * st['cell'][1] and w < fw * 1.5 + 4 and h < fh * 1.5 + 4,
            [round(w, 1), round(h, 1), round(ew, 1), round(eh, 1), round(fw, 1), round(fh, 1), st['cell']])
    on = '1.00|' + ','.join(str(v) for v in st['colors']['on'])
    above = '0.06|' + ','.join(str(v) for v in st['colors']['above'])
    below = '0.06|' + ','.join(str(v) for v in st['colors']['below'])
    r.check('the plane\'s section is drawn yellow at full strength, above it red and below it blue at 0.06',
            st['groups'].get(on, 0) > 0 and st['groups'].get(above, 0) > 0 and st['groups'].get(below, 0) > 0
            and set(st['groups']) == {on, above, below}, st['groups'])
    pick = js(c, """(() => { const R = window.sbfsem.renderer; const l = window.__group(%s).userData.layer;
      const center = window.sbfsem.shifts.centerOf(window.sbfsem.state.neurons.get(%s));
      const i = [...l.sel.section].indexOf(center); const r = l.sel.rows[i]; const f = l.field;
      R.select({ kind: 'annotation', key: %s, locationId: f.id[r], point: [f.cx[r], f.cy[r], f.z[r]],
                 section: f.section[r], zUm: f.z[r] }); return f.id[r]; })()""" % (q, q, q))
    c.settle(1)
    sel = js(c, SLICE_STATE % q)
    pale = [round(v + (1 - v) * 0.55, 3) for v in st['colors']['on']]
    r.check('with an annotation of the cell selected, its section turns pale yellow and nothing else fades',
            any(k.startswith('1.00|') and all(abs(float(x) - y) < 0.002 for x, y in zip(k.split('|')[1].split(','), pale, strict=True))
                for k in sel['groups']) and sel['dim'] == 1, [pick, sel['groups'], pale, sel['dim']])
    js(c, 'window.sbfsem.renderer.clearSelection(); window.sbfsem.renderer.webgl.domElement.focus(); true')
    c.key(']')
    c.settle(1)
    step = js(c, SLICE_STATE % q)
    r.check('a step moves the plane and recolors the new window', step['center'] == st['center'] + 1
            and step['groups'].get(on, 0) > 0 and step['plane'] != st['plane'], [st['center'], step['center'], step['groups']])
    apply(c, key, {'shiftSlice': False})


def s_moveset(c: Chrome, r: Report, ctx):
    r.start('moveset: a section, a run of sections and a hand-picked set move together')
    key = start(c, ctx)
    ctx.key = key
    q = json.dumps(key)
    plan = js(c, """(async () => { const R = window.sbfsem.renderer; const rec = window.sbfsem.state.neurons.get(%s);
      const nodes = await window.sbfsem.api.nodes(rec.source, rec.id);
      const by = new Map(); for (const n of nodes) { const s = n.z_section; if (!by.has(s)) by.set(s, []); by.get(s).push(n); }
      const sec = [...by.keys()].sort((a, b) => a - b).find((s) => by.get(s).length >= 3 && by.has(s + 1) && by.has(s - 1));
      const y = by.get(sec)[0]; const up = by.get(sec + 1)[0]; const down = by.get(sec - 1)[0];
      const far = nodes.find((n) => Math.abs(n.z_section - sec) > 5);
      const cand = (n) => ({ kind: 'annotation', key: rec.key, locationId: n.id, point: n.xyz_um, section: n.z_section, zUm: n.xyz_um[2] });
      R.select(cand(y)); R.setMarker('above', cand(up)); R.setMarker('below', cand(down));
      window.__mv = { cand, far: cand(far) };
      return { sec, n: by.get(sec).length, n3: by.get(sec).length + by.get(sec + 1).length + by.get(sec - 1).length,
               y: y.id, up: up.id, down: down.id, far: far.id, tag: rec.summary.copy_tag }; })()""" % q, wait=True)
    r.check('c1178 has a section with three or more annotations and annotations on the sections beside it', bool(plan), plan)
    if not plan:
        return
    base = json.dumps(plan['tag'])
    js(c, "(async () => { const m = window.sbfsem.moves; await m.start(); m.setScope('section'); m.setAxis('x'); })()", wait=True)
    c.settle(1)
    # The panel moves the marker the arrow keys act on (the one moved last): count its section.
    sec = js(c, """(async () => { const m = window.sbfsem.moves; const R = window.sbfsem.renderer;
      const t = m.target(); const rec = window.sbfsem.state.neurons.get(%s);
      const nodes = await window.sbfsem.api.nodes(rec.source, rec.id);
      return { members: m.members().length, section: t.item.section,
               expected: nodes.filter((n) => n.z_section === t.item.section).length,
               count: document.querySelector('#move-panel .mv-count').textContent, rings: R.moveSets.has(%s) }; })()"""
             % (q, q), wait=True)
    r.check('Its section moves every annotation of the moved annotation\'s section, drawn as rings',
            sec['members'] == sec['expected'] and sec['members'] >= 2 and sec['rings']
            and f"on section {sec['section']}" in sec['count'], sec)
    js(c, "(() => { const m = window.sbfsem.moves; m.nudge(1); m.nudge(1); })()")
    moved = js(c, """(() => { const rec = window.sbfsem.state.neurons.get(%s); const set = rec.manualMoves[%s] || {};
      return Object.values(set).map((m) => m.dx); })()""" % (q, base))
    r.check('a step moves all of them by the same 0.1 um', len(moved) == sec['expected']
            and all(abs(x - 0.1) < 1e-9 for x in moved), moved)
    # The red and blue markers bound a run of sections.
    rng = js(c, """(() => { const m = window.sbfsem.moves; m.setScope('range');
      return { range: m.range, members: m.members().length,
               shown: !document.querySelector('#move-panel .mv-range').hidden }; })()""")
    r.check('Sections starts at the blue marker\'s section and ends at the red one\'s',
            rng['range'] == {'from': plan['sec'] - 1, 'to': plan['sec'] + 1} and rng['members'] == plan['n3'] and rng['shown'],
            [rng, plan['n3']])
    # A hand-picked annotation, as a ⌘/Ctrl-click hands it over.
    added = js(c, """(() => { const R = window.sbfsem.renderer; const m = window.sbfsem.moves;
      const before = m.members().length; R.onAdditiveClick(window.__mv.far); const after = m.members().length;
      R.onAdditiveClick(window.__mv.far); const again = m.members().length; R.onAdditiveClick(window.__mv.far);
      return [before, after, again, m.members().length]; })()""")
    r.check('a ⌘/Ctrl-click adds an annotation of another section, and a second takes it out',
            added == [plan['n3'], plan['n3'] + 1, plan['n3'], plan['n3'] + 1], added)
    js(c, "window.sbfsem.moves.setAxis('y'); window.sbfsem.moves.nudge(-1)")
    js(c, 'window.sbfsem.renderer.webgl.domElement.focus(); true')
    c.key('Enter')
    ok = c.wait_for("(window.sbfsem.state.neurons.get(%s).summary.copy_tag || '').startsWith(%s)"
                    " && !window.sbfsem.moves.applying" % (q, json.dumps(plan['tag'] + '.m')), 120)
    c.settle(3)
    got = js(c, """(() => { const rec = window.sbfsem.state.neurons.get(%s); return { mm: rec.summary.manual_moves,
      stored: Object.keys(rec.manualMoves[%s] || {}).length }; })()""" % (q, base))
    r.check('Enter applies the whole set on the server', ok and got['mm'] and got['mm']['n_moved'] == plan['n3'] + 1
            and got['stored'] == plan['n3'] + 1, got)
    js(c, "window.sbfsem.moves.stop({ apply: false }); window.__openSection('Manual moves')")
    c.settle(2)
    ticked = js(c, """(() => { const s = window.__section('Manual moves'); const cell = [...s.querySelectorAll('td.mv-secpick')]
        .find((td) => td.textContent === String(%d)); cell.click();
      const rows = [...s.querySelectorAll('.mv-table tbody tr')];
      const on = rows.filter((tr) => tr.querySelector('input').checked).map((tr) => tr.children[2].textContent);
      return { on: on.length, all: on.every((x) => x === String(%d)),
               del: [...s.querySelectorAll('.mv-actions button')].find((b) => b.textContent.startsWith('Delete')).textContent }; })()"""
                 % (plan['sec'], plan['sec']))
    r.check('in the list, a click on a section ticks every move on it', ticked['on'] == plan['n'] and ticked['all']
            and ticked['del'] == f"Delete {plan['n']}", ticked)
    js(c, "(async () => { await window.sbfsem.moves.reset(window.sbfsem.state.neurons.get(%s)); })()" % q, wait=True)


def s_moveplane(c: Chrome, r: Report, ctx):
    r.start('moveplane: a move stays on its section, the skeleton follows it, the cell stays drawn through Apply')
    key = start(c, ctx)
    ctx.key = key
    q = json.dumps(key)
    js(c, "window.__openSection('Skeleton')")
    time.sleep(0.3)
    js(c, """(() => { const lab = [...document.querySelectorAll('.cell-panel label.check')]
        .find((l) => l.textContent.trim().startsWith('Show skeleton'));
        const b = lab && lab.querySelector('input'); if (b && !b.checked) b.click(); })()""")
    r.check('the skeleton is drawn', c.wait_for('window.sbfsem.renderer.skeletons.has(%s)' % q, 60))
    c.settle(2)
    # An annotation in the middle of a skeleton segment: its point is the one that must follow.
    picked = js(c, """(async () => { const R = window.sbfsem.renderer; const rec = window.sbfsem.state.neurons.get(%s);
      const nodes = await window.sbfsem.api.nodes(rec.source, rec.id);
      const sk = R.skeletons.get(rec.key).userData.skeleton;
      const seg = sk.segments.find((s) => s.location_ids.length >= 5);
      const id = seg.location_ids[Math.floor(seg.location_ids.length / 2)];
      const n = nodes.find((x) => x.id === id);
      R.select({ kind: 'annotation', key: rec.key, locationId: n.id, point: n.xyz_um, section: n.z_section, zUm: n.xyz_um[2] });
      R.webgl.domElement.focus();
      return { id, xyz: n.xyz_um, section: n.z_section, tag: rec.summary.copy_tag,
               point: seg.points_um[seg.location_ids.indexOf(id)] }; })()""" % q, wait=True)
    r.check('an annotation in the middle of a skeleton segment is selected', bool(picked), picked)
    if not picked:
        return
    point_of = """((key, id) => { const o = window.sbfsem.renderer.skeletons.get(key); if (!o) return null;
      const find = (sk) => { for (const s of sk.segments) { const i = s.location_ids.indexOf(id);
        if (i >= 0) return s.points_um[i]; } return null; };
      return { now: find(o.userData.skeleton), base: o.userData.baseSkeleton ? find(o.userData.baseSkeleton) : null,
               sig: o.userData.nudgeSig || '' }; })(%s, %d)""" % (q, picked['id'])
    c.key('g')
    c.settle(2)
    c.key('1')
    for _ in range(4):
        c.key('ArrowRight')
    c.settle(1)
    bent = js(c, point_of)
    r.check('four steps along X carry the skeleton\'s point 0.2 um with the annotation, before Apply',
            bool(bent and bent['now']) and abs(bent['now'][0] - picked['point'][0] - 0.2) < 1e-6
            and abs(bent['now'][1] - picked['point'][1]) < 1e-9 and abs(bent['now'][2] - picked['point'][2]) < 1e-9
            and bent['base'] == picked['point'], bent)
    # Seen from the side, up the screen is Z: a step along Yp must not carry the annotation to another
    # section (it ran along the cell's own process), so it moves nothing and says why.
    js(c, "window.sbfsem.renderer.setViewpoint('xz1')")
    c.settle(1)
    before = js(c, "JSON.stringify(window.sbfsem.state.neurons.get(%s).manualMoves)" % q)
    c.key('5')
    c.key('ArrowRight')
    c.settle(1)
    side = js(c, """(() => ({ store: JSON.stringify(window.sbfsem.state.neurons.get(%s).manualMoves),
      status: document.getElementById('status').textContent,
      note: document.querySelector('#move-panel .mv-note')?.textContent || '' }))()""" % q)
    r.check('from the side, Yp moves nothing (no section step) and says it points along Z',
            bool(side) and side['store'] == before and 'points along Z' in (side['status'] + side['note']), side)
    c.key('6')
    c.key('ArrowRight')
    c.settle(1)
    zp = js(c, "window.sbfsem.state.neurons.get(%s).manualMoves[%s]?.[%s]"
            % (q, json.dumps(picked['tag']), json.dumps(str(picked['id']))))
    r.check('from the side, Zp moves in the section plane (toward the viewer, -Y), never in Z',
            bool(zp) and zp['dz'] == 0 and abs(zp['dy'] + 0.05) < 1e-9 and abs(zp['dx'] - 0.2) < 1e-9, zp)
    # Apply: the old surface and skeleton stay on screen until the moved copy's replace them.
    js(c, """(() => { const R = window.sbfsem.renderer; const key = %s; window.__gaps = { mesh: 0, skeleton: 0, samples: 0 };
      window.__watch = setInterval(() => { window.__gaps.samples += 1;
        if (!R.meshes.has(key)) window.__gaps.mesh += 1; if (!R.skeletons.has(key)) window.__gaps.skeleton += 1; }, 15);
      })()""" % q)
    c.key('Enter')
    applied = c.wait_for("(window.sbfsem.state.neurons.get(%s).summary.copy_tag || '').startsWith(%s)"
                         " && !window.sbfsem.moves.applying && window.sbfsem.renderer.meshes.has(%s)"
                         % (q, json.dumps(picked['tag'] + '.m'), q), 120)
    c.settle(2)
    gaps = js(c, "(() => { clearInterval(window.__watch); return window.__gaps; })()")
    r.check('Apply never takes the surface or the skeleton off the screen while the moved copy builds',
            applied and bool(gaps) and gaps['samples'] > 3 and gaps['mesh'] == 0 and gaps['skeleton'] == 0, gaps)
    after = js(c, point_of)
    r.check('after Apply the skeleton is the server\'s for the moved copy: the point where the move put it, nothing pending',
            bool(after and after['now']) and abs(after['now'][0] - picked['point'][0] - 0.2) < 1e-6
            and abs(after['now'][1] - picked['point'][1] + 0.05) < 1e-6 and after['sig'] == '', after)
    js(c, "(async () => { await window.sbfsem.moves.stop({ apply: false });"
          " await window.sbfsem.moves.reset(window.sbfsem.state.neurons.get(%s)); })()" % q, wait=True)


# -- a new copy's extras -------------------------------------------------------------------------

# Spies on the cell's draws and on the requests for its annotations and outlines.  Each answer is
# marked with the copy the cell held when it was asked and a serial number; each surface and ring
# draw records the copy the cell holds as it is drawn and the marks of what it draws.  The next
# outline request can be held (`window.__holdNext`) until `window.__release()`.
SWITCH_SPY = r"""
(async () => {
  const key = %s;
  const R = window.sbfsem.renderer;
  const api = window.sbfsem.api;
  const tagNow = () => window.sbfsem.state.neurons.get(key)?.summary?.copy_tag ?? null;
  const mark = (o) => (o && typeof o === 'object' ? { copy: o.__copy ?? null, seq: o.__seq ?? null } : null);
  window.__drawn = []; window.__asked = []; window.__seq = window.__seq || 0;
  window.__holdNext = false; window.__held = 0;
  if (!window.__switchSpied) {
    window.__switchSpied = true;
    for (const name of ['setMesh', 'setDiscs']) {
      const f = R[name];
      R[name] = function (k, data, opts, ...rest) {
        if (k === key) {
          window.__drawn.push({ name, at: performance.now(), tag: tagNow(),
            nodes: name === 'setDiscs' ? mark(data) : null,
            outlines: name === 'setDiscs' ? mark(opts?.outlines) : null });
        }
        return f.call(this, k, data, opts, ...rest);
      };
    }
    for (const name of ['nodes', 'closedCurves']) {
      const f = api[name];
      api[name] = async function (...a) {
        const tag = tagNow();
        window.__seq += 1;
        const seq = window.__seq;
        window.__asked.push({ name, at: performance.now(), tag, seq });
        if (name === 'closedCurves' && window.__holdNext) {
          window.__holdNext = false;
          window.__held += 1;
          await new Promise((resolve) => { window.__release = resolve; });
        }
        const out = await f.apply(this, a);
        if (out && typeof out === 'object') {
          Object.defineProperty(out, '__copy', { value: tag, configurable: true });
          Object.defineProperty(out, '__seq', { value: seq, configurable: true });
        }
        return out;
      };
    }
  }
  (await import('/static/js/api.js')).POLL_TIMING.wait = 1;
  performance.setResourceTimingBufferSize(4000); performance.clearResourceTimings();
  return true;
})()
"""

# What a draw since `t0` (performance.now()) asked for and drew, for the cell `key`.
SWITCH_REPORT = r"""
((key, t0, seq0) => {
  const tag = window.sbfsem.state.neurons.get(key)?.summary?.copy_tag ?? null;
  const asked = window.__asked.filter((a) => a.at >= t0);
  const drawn = window.__drawn.filter((d) => d.at >= t0);
  const polls = performance.getEntriesByType('resource').filter((e) => e.name.includes('/api/mesh-jobs/') && e.startTime >= t0);
  const meshes = drawn.filter((d) => d.name === 'setMesh');
  const rings = drawn.filter((d) => d.name === 'setDiscs');
  const last = rings.at(-1) || null;
  return { tag, seq0,
    nodes: asked.filter((a) => a.name === 'nodes'), outlines: asked.filter((a) => a.name === 'closedCurves'),
    lastPoll: polls.length ? polls.at(-1).startTime : null, polls: polls.length,
    setMesh: meshes.length ? meshes.at(-1).at : null, setDiscs: last ? last.at : null, last,
    stale: rings.filter((d) => d.nodes?.copy !== d.tag || d.outlines?.copy !== d.tag
      || (d.nodes?.seq ?? 0) <= seq0 || (d.outlines?.seq ?? 0) <= seq0) };
})(%s, %s, %s)
"""


def _reg_box(c: Chrome, which: str, on: bool) -> str | None:
    """Set the Registration section's `which` switch ('transforms' or 'correction') to `on`:
    'clicked', 'already' when it was so, None when the section has no such switch."""
    return js(c, """(() => { window.__openSection('Registration');
      const b = window.__section('Registration')?.querySelector('[data-reg=%s] input[type=checkbox]');
      if (!b) return null; if (b.checked === %s) return 'already'; b.click(); return 'clicked'; })()"""
              % (which, json.dumps(on)))


def _show_rings(c: Chrome, on: bool) -> None:
    js(c, """(() => { window.__openSection('Annotations');
        const lab = [...document.querySelectorAll('.cell-panel label.check')]
          .find((l) => l.textContent.trim().startsWith('Show annotations'));
        const b = lab && lab.querySelector('input'); if (b && b.checked !== %s) b.click(); })()""" % json.dumps(on))


def _mark(c: Chrome) -> tuple[float, int]:
    return js(c, '[performance.now(), window.__seq]')


def _drawn_after(c: Chrome, key: str, t0: float, old_tag: str | None, seconds: float = 120) -> bool:
    """True once the cell holds a copy other than `old_tag` (any copy when None) and its rings are drawn from it."""
    return c.wait_for("""(() => { const tag = window.sbfsem.state.neurons.get(%s)?.summary?.copy_tag;
      return (%s === null || tag !== %s) && window.__drawn.some((d) => d.name === 'setDiscs' && d.at >= %s && d.tag === tag); })()"""
                      % (json.dumps(key), json.dumps(old_tag), json.dumps(old_tag), t0), seconds)


def _check_early(r: Report, what: str, got) -> None:
    ok = bool(got)
    r.check(f"{what}: the new copy's annotations and outlines are asked for once each, from that copy, while its surface builds",
            ok and len(got['nodes']) == 1 and len(got['outlines']) == 1
            and got['nodes'][0]['tag'] == got['tag'] and got['outlines'][0]['tag'] == got['tag']
            and got['lastPoll'] is not None
            # No later than the last poll: a surface the server holds is answered by the first
            # poll, sent in the same tick, and the two can share a 0.1 ms timestamp.
            and max(got['nodes'][0]['at'], got['outlines'][0]['at']) <= got['lastPoll'], got)
    r.check(f'{what}: the rings are drawn with the surface, not before it, from what was asked for the new copy',
            ok and got['setMesh'] is not None and got['setDiscs'] is not None
            and 0 <= got['setDiscs'] - got['setMesh'] < 1000 and not got['stale'], got)


def s_regswitch(c: Chrome, r: Report, ctx):
    r.start("regswitch: a Registration switch and Update from Viking ask for the new copy's extras while it builds")
    key = start(c, ctx)
    ctx.key = key
    q = json.dumps(key)
    _show_rings(c, True)
    r.check('the rings are drawn on the plain copy', c.wait_for('window.sbfsem.renderer.discs.has(%s)' % q, 60))
    c.settle(3)
    js(c, SWITCH_SPY % q, wait=True)
    tag0 = js(c, "window.sbfsem.state.neurons.get(%s).summary.copy_tag" % q)

    # One switch: Viking's current transforms off.
    t0, seq0 = _mark(c)
    r.check('the transforms switch is there', _reg_box(c, 'transforms', False) == 'clicked')
    _drawn_after(c, key, t0, tag0)
    c.settle(3)
    got = js(c, SWITCH_REPORT % (q, t0, seq0))
    _check_early(r, 'transforms off', got)
    tag1 = (got or {}).get('tag')

    # A second switch while the first one's outlines are still out: the transforms on again, its
    # outlines held after its surface is drawn, then the correction off.  The first copy's rings
    # are never drawn, nor kept for a later draw.
    js(c, 'window.__holdNext = true')
    t0, seq0 = _mark(c)
    _reg_box(c, 'transforms', True)
    held = c.wait_for("window.__held === 1 && window.__drawn.some((d) => d.name === 'setMesh' && d.at >= %s"
                      " && d.tag !== %s)" % (t0, json.dumps(tag1)), 120)
    mid = js(c, "window.sbfsem.state.neurons.get(%s).summary.copy_tag" % q)
    r.check("the first switch's surface is drawn and its outlines are held", held and mid != tag1,
            {'held': held, 'tag': mid})
    _reg_box(c, 'correction', False)
    _drawn_after(c, key, t0, mid)
    js(c, 'window.__release && window.__release()')
    time.sleep(1.5)
    c.settle(3)
    got = js(c, SWITCH_REPORT % (q, t0, seq0))
    r.check('the rings drawn after both are the second copy\'s, and none from the first',
            bool(got) and got['tag'] not in (mid, tag1) and got['last'] and got['last']['nodes']['copy'] == got['tag']
            and got['last']['outlines']['copy'] == got['tag'] and not got['stale'], got)
    # Drawn again from the caches: nothing of the first copy was kept in them.
    t0b = js(c, 'performance.now()')
    _show_rings(c, False)
    c.wait_for('!window.sbfsem.renderer.discs.has(%s)' % q, 10)
    _show_rings(c, True)
    c.wait_for("window.__drawn.some((d) => d.name === 'setDiscs' && d.at >= %s)" % t0b, 30)
    c.settle(2)
    again = js(c, "window.__drawn.filter((d) => d.name === 'setDiscs' && d.at >= %s).at(-1) || null" % t0b)
    r.check('rings drawn again come from the caches, and are the second copy\'s',
            bool(again) and again['nodes']['copy'] == again['tag'] and again['outlines']['copy'] == again['tag']
            and again['tag'] == (got or {}).get('tag'), again)

    # Update from Viking: the row's refresh button.  The copy keeps its name; what is drawn is what
    # was asked after the click.
    t0, seq0 = _mark(c)
    clicked = js(c, """(() => { const b = document.querySelector(%s); if (!b) return false; b.click(); return true; })()"""
                 % json.dumps(f'.neuron-item[data-key="{key}"] .icon-btn.refresh'))
    r.check('the refresh button is there', clicked)
    c.wait_for("window.__drawn.some((d) => d.name === 'setDiscs' && d.at >= %s && (d.nodes?.seq ?? 0) > %s)"
               % (t0, seq0), 180)
    c.settle(3)
    got = js(c, SWITCH_REPORT % (q, t0, seq0))
    _check_early(r, 'Update from Viking', got)

    # As it was: the correction and the transforms back on.
    for which in ('correction', 'transforms'):
        t0 = js(c, 'performance.now()')
        if _reg_box(c, which, True) == 'clicked':
            _drawn_after(c, key, t0, None)
            c.settle(2)
    js(c, "(async () => { (await import('/static/js/api.js')).POLL_TIMING.wait = 0; })()", wait=True)


SECTIONS = {
    'panel': s_panel, 'help': s_help, 'arrows': s_arrows, 'slice': s_slice, 'legend': s_legend,
    'rc1': s_rc1, 'compare': s_compare, 'layout': s_layout, 'focus': s_focus, 'moves': s_moves,
    'slicecolors': s_slicecolors, 'moveset': s_moveset, 'moveplane': s_moveplane, 'regswitch': s_regswitch,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--chrome', default=None)
    parser.add_argument('--only', default='', help='comma-separated sections: ' + ', '.join(SECTIONS))
    parser.add_argument('--shots', default=None, help='a folder for the screenshots to look at')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    wanted = [s for s in args.only.split(',') if s] or list(SECTIONS)
    unknown = [s for s in wanted if s not in SECTIONS]
    if unknown:
        parser.error(f'unknown sections: {unknown}')
    ctx = argparse.Namespace(url=args.url, shots=Path(args.shots) if args.shots else None, key=None)
    if ctx.shots:
        ctx.shots.mkdir(parents=True, exist_ok=True)
    report = Report(args.verbose)
    c = Chrome(find_chrome(args.chrome), width=1440, height=900)
    try:
        c.emulate('mac', width=1440, height=900, scheme='dark')
        for name in wanted:
            try:
                SECTIONS[name](c, report, ctx)
            except Exception as error:  # a section that breaks is a failure, the next one still runs
                report.check(f'{name} ran to the end', False, repr(error)[:400])
                ctx.key = None
        s_console(c, report, ctx)
    finally:
        c.close()
    return report.summary()


if __name__ == '__main__':
    sys.exit(main())
