"""Scripted browser check of the render window's right-click menu.

    .venv/bin/python tools/check_contextmenu.py                         # the dev server on :8765
    .venv/bin/python tools/check_contextmenu.py --url http://127.0.0.1:8812 -v
    .venv/bin/python tools/check_contextmenu.py --only thin,candidates

It drives a headless Chrome for Testing over the DevTools protocol with REAL mouse
input (``Input.dispatchMouseEvent``: a right click is a press and a release of the
right button, in the order this machine's Chrome sends them), in a throwaway
profile (the client and the report are ``tools/check_dropdowns.py``'s).  It is
outside the pytest gate: it needs a running server that can load cells
(``SBFSEM_OFFLINE=1`` and the cells cached under ``~/.cache/sbfsem`` do: c1178 and
c21768 of NeitzInferiorMonkey).  What needs no page -- the order and the names of
what is under the pointer, click against pan, where a menu goes -- is
``tests/js/test_contextmenu.mjs`` and ``tests/js/test_pickgeom.mjs``, which the gate
does run.

The report of 2026-10-16 was "the right click context menu doesn't seem to be
working on all cells".  Every case it came to, and what each check pins:

* ``thin`` -- a cell's thin processes are a pixel or two wide, and a ray went between
  the triangles: of 60 pixels c1178 is drawn at, 16 found no cell (37 for c16137's Closed
  curve volume).
  Every pixel it is drawn at opens its menu (a few pixels' tolerance, one pass over
  the triangles).
* ``layers`` -- with the surface hidden, the rings, the wireframe, the annotation
  wireframe and the skeleton never raised a menu (no ray meets a ring or a line).
  Each one does, for its cell.
* ``hidden`` -- a click on a pixel the crop or the Z range had cut the cell away from
  still opened the cell's menu; one on what is left of it does.
* ``ortho`` -- an orthographic ray started in the camera's plane, so the part of a
  cell behind that plane was drawn and could not be clicked.
* ``gesture`` -- macOS asks for the menu as the right button goes DOWN: it opened under
  every right-drag that began on a cell, and the pan stalled (0.01 µm of pan against 45
  over empty space).  The menu opens when the button comes up, and only for a click.
* ``candidates`` -- more than one thing under the pointer: a list at the top of the same
  menu, a row lights its item up, a click selects it and makes the menu about it, every
  item stays, Esc closes.
* ``cells`` -- cells behind one another; the list names both, the row picks the cell.
* ``lines`` -- a skeleton line of one cell, nearer the camera than another cell's surface,
  took the clicks of every pixel of that surface within a micron of it (the line's pick was a
  micron in the world, which is 14 to 25 pixels at close zoom): 27 of 32 clicks opened the
  wrong cell.  The line's own pixels still open its cell's menu.
* ``compare`` -- Render compare's panes had no menu at all.
* ``leftpick`` -- the left click's pick readout (2.6.5) was still one ray with a micron's line
  threshold: ``thin`` and ``lines`` again with the left button, which now picks the first thing
  the menu would list.
* ``markers`` -- the red, yellow and blue markers (2026-10-16): a triple click on a ring above
  the yellow one makes it red, below it blue; the table under the readout has a row for each in
  its color, with the Location ID and Viking place to copy, the section, and the Z and distance
  from the yellow one; its arrows step a marker and the readout names what was chosen last (it
  went back to the yellow one, which read as the wrong Location ID); Hide above / below go by the
  red and the blue; Esc takes the markers, then the selection; the table fits at 900 to 1920 px.
* ``zslice`` -- "Select all annotations on z-slice" (2026-10-20): with an annotation selected the
  menu offers its section's annotations, of its own cell or of every loaded cell; the rest are drawn
  in its color, the table says how many, and ↑/↓ then step by sections with annotations, not along
  the links: a red z-slice from the yellow's, on to the next, back into the yellow's where it goes;
  Shift moves the yellow's; Esc takes a z-slice before its annotation.
"""

# ruff: noqa: UP031
# The JavaScript snippets below are filled with `%`: their braces would all need
# doubling in an f-string or `.format`, which buries the script they carry.

from __future__ import annotations

import argparse
import colorsys
import io
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_app import STATE, boot, js, load  # noqa: E402
from check_dropdowns import SHIFT, Chrome, Report, find_chrome  # noqa: E402

BACKGROUND = (16, 20, 24)

#: Hides what lies over the render (the readout, the hint, the banners) so a screenshot of it
#: holds the scene and nothing else; `visibility` keeps the layout.
HIDE = ("document.head.append(Object.assign(document.createElement('style'), { textContent: "
        "'.viewport-overlay, #restore-banner, #render-notice, #render-error, #load-progress { visibility: hidden !important; }' }));"
        "window.sbfsem.renderer.setAxesVisible(false); true")


# -- the page --------------------------------------------------------------------------------------

def prepare(c: Chrome) -> None:
    """Put the scene alone in the picture: no overlays, no axes."""
    js(c, HIDE)
    c.settle(3)


def canvas(c: Chrome) -> tuple[float, float, float, float]:
    return tuple(js(c, """(() => { const r = window.sbfsem.renderer.webgl.domElement.getBoundingClientRect();
        return [r.left, r.top, r.width, r.height]; })()"""))


def drawn(c: Chrome, near: tuple[int, int, int] = BACKGROUND, tolerance: int = 40) -> list[tuple[int, int, tuple]]:
    """Every pixel of the render that is not the background: [(x, y, (r, g, b)), ...]."""
    from PIL import Image
    image = Image.open(io.BytesIO(c.shot())).convert('RGB')
    pixels = image.load()
    left, top, width, height = canvas(c)
    out = []
    for y in range(int(top) + 3, int(top + height) - 3):
        for x in range(int(left) + 3, int(left + width) - 3):
            red, green, blue = pixels[x, y]
            if abs(red - near[0]) + abs(green - near[1]) + abs(blue - near[2]) > tolerance:
                out.append((x, y, (red, green, blue)))
    return out


def hue(rgb) -> float:
    return colorsys.rgb_to_hsv(*(v / 255 for v in rgb))[0] * 360


def right_click(c: Chrome, x: float, y: float, wobble: float = 0) -> None:
    """A right click as a hand makes it: the pointer comes, the button goes down and up, `wobble` px apart."""
    c.press(x, y, 'right')
    if wobble:
        c.send('Input.dispatchMouseEvent', type='mouseMoved', x=x + wobble, y=y, buttons=2)
    c.release(x + wobble, y, 'right')
    c.settle(2)


def menu(c: Chrome):
    """The open #ctx-menu as the page shows it, or None."""
    return js(c, """(() => { const m = document.getElementById('ctx-menu'); if (!m) return null;
        const top = [...m.querySelectorAll(':scope > .ctx-item')];
        const rows = [...m.querySelectorAll('.ctx-cand')];
        const box = m.getBoundingClientRect();
        return { title: m.querySelector('.ctx-title').textContent,
                 heading: m.querySelector('.ctx-heading')?.textContent ?? null,
                 rows: rows.map((e) => e.textContent), ticked: rows.map((e) => e.textContent.startsWith('\\u2713')),
                 items: top.map((e) => e.textContent.trim()),
                 parents: [...m.querySelectorAll(':scope > .ctx-parent')].map((e) => e.firstChild.textContent.trim()),
                 box: [box.left, box.top, box.width, box.height] }; })()""")


def close_menu(c: Chrome) -> None:
    js(c, "document.getElementById('ctx-menu')?.remove(); window.sbfsem.renderer.preview(null); true")


def triple_click(c: Chrome, x: float, y: float) -> None:
    """Three left clicks in a row, as the browser counts them (the render's chooser answers the third)."""
    c.move(x, y)
    for count in (1, 2, 3):
        c.send('Input.dispatchMouseEvent', type='mousePressed', x=x, y=y, button='left', buttons=1, clickCount=count)
        c.send('Input.dispatchMouseEvent', type='mouseReleased', x=x, y=y, button='left', buttons=0, clickCount=count)
    c.settle(3)


def row_xy(c: Chrome, index: int):
    return js(c, """(() => { const e = document.querySelectorAll('#ctx-menu .ctx-cand')[%d];
        if (!e) return null; const r = e.getBoundingClientRect(); return [r.left + 40, r.top + r.height / 2]; })()""" % index)


def sample(items, n: int, seed: int = 3):
    return random.Random(seed).sample(items, min(n, len(items)))


def set_show(c: Chrome, label: str, on: bool, seconds: float = 1.0) -> bool:
    """Set a cell panel checkbox (`Show surface`, ...) to `on`; False if the panel has no such box."""
    done = js(c, """(() => { const lab = [...document.querySelectorAll('.cell-panel label.check')]
        .find((l) => l.textContent.trim().startsWith(%s)); if (!lab) return 'none';
        const box = lab.querySelector('input'); if (box.checked !== %s) box.click(); return 'ok'; })()"""
              % (json.dumps(label), 'true' if on else 'false'))
    time.sleep(seconds)
    c.settle(2)
    return done == 'ok'


def open_panel(c: Chrome) -> None:
    """Expand the first cell's panel and open all its sections."""
    js(c, """(() => { if (!document.querySelector('.neuron-item.expanded'))
        document.querySelector('.neuron-item .neuron-head').click(); })()""")
    c.settle(2)
    js(c, """[...document.querySelectorAll('.cell-section .section-head')].forEach((h) => {
        if (h.getAttribute('aria-expanded') !== 'true') h.click(); })""")
    c.settle(2)


def titles(c: Chrome, points, wobble: float = 0) -> list:
    """Right-click each point; the title of the menu it opened (None for none)."""
    got = []
    for x, y in points:
        right_click(c, x, y, wobble)
        m = menu(c)
        got.append(m['title'] if m else None)
        close_menu(c)
    return got


def cell_of(title: str | None) -> str | None:
    return title.split(' ·')[0] if title else None


def menu_cells(c: Chrome, points) -> list:
    """Right-click each point: the cell whose menu opened (`c1178`), None for none."""
    return [cell_of(t) for t in titles(c, points)]


def picked_cells(c: Chrome, points) -> list:
    """Left-click each point: the cell the pick readout took it on (`c1178`), None for none."""
    got = []
    for x, y in points:
        js(c, 'window.sbfsem.renderer.pickedKey = null; true')
        c.click(x, y, 'left')
        c.settle(2)
        key = js(c, 'window.sbfsem.renderer.pickedKey')
        got.append(f"c{key.split('/')[-1]}" if key else None)
    return got


#: How each button is checked: the cells its clicks land on, and what a click is called.
BY_MENU = (menu_cells, 'a right click', 'opens its menu', 'opens nothing')
BY_PICK = (picked_cells, 'a left click', 'picks it', 'picks nothing')


# -- thin ------------------------------------------------------------------------------------------

def thin(c: Chrome, r: Report, ctx, by=BY_MENU):
    cells_at, click, lands, nothing = by
    r.start(f'thin: {click} on a thin process {lands} wherever it is drawn')
    boot(c, ctx.url)
    load(c, ctx.cell)
    prepare(c)
    pixels = drawn(c)
    r.check('the cell is drawn', len(pixels) > 500, len(pixels))
    got = cells_at(c, [(x, y) for x, y, _ in sample(pixels, 60)])
    miss = sum(1 for t in got if t != f'c{ctx.cell}')
    r.check(f'{click} on any of 60 pixels c{ctx.cell} is drawn at {lands} (a ray alone: 44 of 60)',
            miss == 0, f'{miss} of 60 found nothing or another cell: {sorted(set(map(str, got)))}')
    # Where the picture is empty by a margin wider than any tolerance, nothing opens.
    left, top, width, height = canvas(c)
    taken = {(x, y) for x, y, _ in pixels}
    margin = 14
    empty = [(x, y) for x in range(int(left) + 30, int(left + width) - 30, 37)
             for y in range(int(top) + 30, int(top + height) - 30, 37)
             if not any((x + dx, y + dy) in taken for dx in range(-margin, margin + 1, 2)
                        for dy in range(-margin, margin + 1, 2))]
    got = cells_at(c, sample(empty, 12))
    r.check(f'empty space (14 px from anything drawn) {nothing}', len(empty) >= 12 and all(t is None for t in got),
            (len(empty), got))


# -- layers ----------------------------------------------------------------------------------------

def layers(c: Chrome, r: Report, ctx):
    r.start('layers: with the surface hidden, what is drawn still opens its cell\'s menu')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    open_panel(c)
    prepare(c)
    cell = f'c{ctx.cell}'

    def only(**on):
        for label, want in (('Show surface', on.get('surface', False)),
                            ('Show wireframe', on.get('wire', False)),
                            ('Show annotations', on.get('rings', False)),
                            ('Show annotation wireframe', on.get('annowire', False)),
                            ('Show skeleton', on.get('skeleton', False))):
            set_show(c, label, want, 1.5 if want else 0.5)
        # A layer the server builds (the skeleton, the annotation wireframe) can take seconds
        # on a busy machine: wait until it is there rather than click on nothing.
        for flag, layer in (('skeleton', 'skeletons'), ('annowire', 'annoWires'), ('rings', 'discs')):
            if on.get(flag):
                c.wait_for(f'window.sbfsem.renderer.{layer}.size > 0', 60)
        c.settle(3)

    def pixels() -> list:
        return [(x, y) for x, y, _ in drawn(c)]

    only(rings=True)
    got = titles(c, sample(pixels(), 24))
    r.check('only the annotation rings drawn: a click on a ring opens the cell\'s menu',
            sum(1 for t in got if cell_of(t) == cell) >= 22, got[:6])
    r.check('and the title says which annotation (the one under the pointer)',
            any('annotation' in (t or '') and '(Inferior Monkey)' in t for t in got), got[:4])
    only(wire=True)
    got = titles(c, sample(pixels(), 24))
    r.check('only the wireframe drawn: a click on an edge opens the cell\'s menu',
            sum(1 for t in got if cell_of(t) == cell) >= 22, got[:6])
    only(skeleton=True)
    got = titles(c, sample(pixels(), 24))
    r.check('only the skeleton drawn: a click on the line opens the cell\'s menu',
            sum(1 for t in got if cell_of(t) == cell) >= 22, got[:6])
    only(annowire=True)
    got = titles(c, sample(pixels(), 24))
    r.check('only the annotation wireframe drawn: a click on a line opens the cell\'s menu',
            sum(1 for t in got if cell_of(t) == cell) >= 22, got[:6])
    r.check('the layers all belong to the cell on screen', js(c, f'{STATE}.neurons.has({json.dumps(key)})'))


# -- hidden ----------------------------------------------------------------------------------------

def hidden(c: Chrome, r: Report, ctx):
    r.start('hidden: what the crop or the Z range has cut away opens no menu')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    prepare(c)
    cell = f'c{ctx.cell}'
    whole = [(x, y) for x, y, _ in drawn(c)]
    k = json.dumps(key)
    # Where the cell is, in its own frame.
    box = js(c, "(() => { const r = window.sbfsem.renderer; return { min: r.bounds.min.toArray(), max: r.bounds.max.toArray() }; })()")
    # Pixels that show the cell now, and the world X of what a ray finds there.
    where = js(c, """(() => { const r = window.sbfsem.renderer; const out = [];
        for (const [x, y] of %s) { const h = r._hitsAt(x, y); if (h.length) out.push([x, y, h[0].point.x, h[0].point.z]); }
        return out; })()""" % json.dumps(sample(whole, 120)))
    xmid = (box['min'][0] + box['max'][0]) / 2
    zmid = (box['min'][2] + box['max'][2]) / 2
    js(c, """window.sbfsem.renderer.setClipBox({ min: [%f, %f, %f], max: [%f, %f, %f] }); true"""
       % (xmid, box['min'][1], box['min'][2], box['max'][0], box['max'][1], box['max'][2]))
    c.settle(3)
    cropped = [(x, y) for x, y, wx, _ in where if wx < xmid - 0.5]
    kept = [(x, y) for x, y, wx, _ in where if wx > xmid + 0.5]
    got = titles(c, sample(cropped, 8))
    r.check('crop: a click where the crop has cut the cell away opens nothing',
            len(cropped) >= 4 and all(t is None for t in got), got)
    got = titles(c, sample(kept, 8))
    r.check('crop: a click on what is left opens the cell\'s menu',
            len(kept) >= 4 and all(cell_of(t) == cell for t in got), got)
    js(c, 'window.sbfsem.renderer.setClipBox(null); true')
    c.settle(2)

    before = {(x, y) for x, y, _ in drawn(c)}
    js(c, "window.sbfsem.renderer.setZHide(%s, { min: %f, max: null }); true" % (k, zmid))
    c.settle(4)
    after = {(x, y) for x, y, _ in drawn(c)}
    # Pixels the Z range emptied, and well clear of anything still drawn (a tolerance reaches
    # a few pixels); and pixels that still show the cell.
    gone = [(x, y) for x, y in before - after
            if not any((x + dx, y + dy) in after for dx in range(-8, 9, 2) for dy in range(-8, 9, 2))]
    got = titles(c, sample(gone, 10))
    r.check('Z range (the lower part hidden): a click where the cell is hidden opens nothing',
            len(gone) >= 10 and all(t is None for t in got), (len(gone), got))
    got = titles(c, sample(sorted(after), 10))
    r.check('Z range: a click on what is shown opens the cell\'s menu',
            all(cell_of(t) == cell for t in got), got)
    js(c, "window.sbfsem.renderer.setZHide(%s, {}); true" % k)


# -- ortho -----------------------------------------------------------------------------------------

def ortho(c: Chrome, r: Report, ctx):
    r.start('ortho: the whole of the picture is clickable, behind the camera\'s plane too')
    boot(c, ctx.url)
    load(c, ctx.cell)
    prepare(c)
    js(c, "window.sbfsem.renderer.setOrthographic(true); window.sbfsem.renderer.resetView(); true")
    c.settle(4)
    cell = f'c{ctx.cell}'
    got = titles(c, [(x, y) for x, y, _ in sample(drawn(c), 30)])
    r.check('orthographic view: every pixel the cell is drawn at opens its menu',
            all(cell_of(t) == cell for t in got), sorted(set(map(str, got))))
    # An orbit or a pan carries cells through the camera's plane; the clipping planes are
    # symmetric about it, so what is behind it is still drawn.
    js(c, """(() => { const r = window.sbfsem.renderer; const cam = r.camera;
        const dir = cam.getWorldDirection(new r.bounds.min.constructor());
        const d = cam.position.distanceTo(r._reachSphere().center);
        cam.position.addScaledVector(dir, d); r.controls.target.addScaledVector(dir, d); r.controls.update(); return true; })()""")
    c.settle(4)
    pixels = drawn(c)
    got = titles(c, [(x, y) for x, y, _ in sample(pixels, 40)])
    r.check('with the camera inside the scene (half of it behind the plane): still every pixel',
            all(cell_of(t) == cell for t in got), sorted(set(map(str, got))))
    # The triple click's chooser starts from the same ray: it still answers here.
    open_panel(c)
    set_show(c, 'Show annotations', True, 3)
    rim = js(c, """(() => { const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
        const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
        const d = r.discs.values().next().value; const big = d.userData.rings.reduce((a, n) => (!a || n.radius_um > a.radius_um ? n : a), null);
        const q = new V(big.xyz_um[0] + big.radius_um, big.xyz_um[1], big.xyz_um[2]).applyMatrix4(d.matrixWorld).project(r.camera);
        return [rect.left + (q.x + 1) / 2 * rect.width, rect.top + (1 - q.y) / 2 * rect.height]; })()""")
    triple_click(c, *rim)
    chooser = menu(c)
    r.check('a triple click on a ring still opens the Select menu, in orthographic view too',
            chooser is not None and chooser['title'].startswith('Select')
            and any(t.startswith('Annotation') for t in chooser['items']), chooser and (chooser['title'], chooser['items'][:2]))
    close_menu(c)


# -- gesture ---------------------------------------------------------------------------------------

def gesture(c: Chrome, r: Report, ctx):
    r.start('gesture: a click opens the menu as the button comes up, a drag pans')
    boot(c, ctx.url)
    load(c, ctx.cell)
    prepare(c)
    cell = f'c{ctx.cell}'

    def camera():
        return js(c, "window.sbfsem.renderer.camera.position.toArray()")

    def soma():
        """Something thick enough that 14 px either side of a press is still the cell: the soma."""
        taken = {(px, py) for px, py, _ in drawn(c)}
        thick = [(px, py) for px, py in sorted(taken)
                 if all((px + dx, py + dy) in taken for dx in range(-14, 15, 2) for dy in range(-6, 7, 2))]
        return thick[len(thick) // 2] if thick else None

    spot = soma()
    r.check('the cell has a thick part to press on', spot is not None)
    if spot is None:
        return
    x, y = spot

    # The order this Chrome sends them in: the menu is not up while the button is down.
    c.press(x, y, 'right')
    c.settle(2)
    r.check('the menu is not open while the button is down (macOS asks for it then)', menu(c) is None, menu(c))
    c.release(x, y, 'right')
    c.settle(2)
    m = menu(c)
    r.check('it opens when the button comes up', m is not None and cell_of(m['title']) == cell, m and m['title'])
    close_menu(c)

    # Windows sends the contextmenu event after the button is up: the same click, in that order.
    c.press(x, y, 'right')
    c.release(x, y, 'right')
    c.settle(2)
    close_menu(c)
    js(c, """document.querySelector('#viewport canvas').dispatchEvent(new MouseEvent('contextmenu',
        { bubbles: true, cancelable: true, clientX: %f, clientY: %f, button: 2 })); true""" % (x, y))
    m = menu(c)
    r.check('a contextmenu that arrives after the release (the Windows order) opens the menu at once',
            m is not None and cell_of(m['title']) == cell, m and m['title'])
    close_menu(c)
    js(c, """document.querySelector('#viewport canvas').dispatchEvent(new MouseEvent('contextmenu',
        { bubbles: true, cancelable: true, clientX: %f, clientY: %f, button: 2 })); true""" % (x + 300, y))
    r.check('and one that arrives far from the press (a menu key) opens nothing', menu(c) is None, menu(c))

    before = camera()
    c.press(x, y, 'right')
    for step in range(1, 11):
        c.send('Input.dispatchMouseEvent', type='mouseMoved', x=x + 12 * step, y=y, buttons=2)
        time.sleep(0.04)
    c.release(x + 120, y, 'right')
    c.settle(3)
    moved = max(abs(a - b) for a, b in zip(before, camera(), strict=True))
    r.check('a right-drag from a cell pans (it stalled under the menu: 0.01 µm against 45)', moved > 5, moved)
    r.check('and opens no menu', menu(c) is None, menu(c))

    spot = soma()   # the drag moved the picture
    if spot is not None:
        wx, wy = spot
        r.check('a click that wobbles 6 px is still a click', cell_of(titles(c, [(wx - 3, wy)], wobble=6)[0]) == cell)
        r.check('one that travels 12 px is a pan', titles(c, [(wx - 6, wy)], wobble=12)[0] is None)
        c.click(wx, wy)
        r.check('the left button opens no menu', menu(c) is None, menu(c))
    # The cell's row in the list opens the same menu as it always did.
    js(c, """(() => { const head = document.querySelector('.neuron-item .neuron-head'); const b = head.getBoundingClientRect();
        head.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
          clientX: b.left + 40, clientY: b.top + 8 })); })()""")
    m = menu(c)
    r.check('a cell\'s row in the list opens its menu, with no list of what is under the pointer',
            m is not None and m['title'] == cell and m['heading'] is None, m and (m['title'], m['heading']))
    close_menu(c)


# -- candidates ------------------------------------------------------------------------------------

def candidates(c: Chrome, r: Report, ctx):
    r.start('candidates: what is under the pointer is the top of the same menu')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    open_panel(c)
    cell = f'c{ctx.cell}'
    # Viking's launch is Windows only: say this is one, and catch where it is sent.
    js(c, """(() => {
        Object.defineProperty(navigator, 'userAgentData', { get: () => ({ platform: 'Windows' }) });
        window.__opened = [];
        window.open = () => ({ location: { set href(v) { window.__opened.push(v); } }, close() {} });
        return true; })()""")
    prepare(c)
    # One thing under the pointer: the menu as it was, with the title of what it is about.
    pixels = drawn(c)
    x, y, _ = sample(pixels, 1)[0]
    right_click(c, x, y)
    m = menu(c)
    plain = m
    r.check('one thing under the pointer (the surface): no list, the title is the cell, the menu as it was',
            m is not None and m['heading'] is None and m['title'] == cell and not m['rows']
            and 'Open in Viking' in m['items'], m and (m['title'], m['heading'], m['rows']))
    close_menu(c)

    # Rings on, seen down Z: the soma's rim has a stack of them under one pixel.
    set_show(c, 'Show annotations', True, 3)
    set_show(c, 'Show skeleton', True, 2)
    js(c, "window.sbfsem.renderer.setViewpoint('xy1'); true")
    c.settle(4)
    soma = js(c, """(() => { const r = window.sbfsem.renderer; const d = r.discs.values().next().value;
        const big = d.userData.rings.reduce((a, n) => (!a || n.radius_um > a.radius_um ? n : a), null);
        return { xyz: big.xyz_um, radius: big.radius_um, id: big.id }; })()""")
    js(c, """(() => { const r = window.sbfsem.renderer; const s = %s;
        r.flyTo(r.toWorld(s.xyz, r._sourceOf(%s)), { duration: 10, distance: s.radius * 5 }); return true; })()"""
       % (json.dumps(soma), json.dumps(key)))
    time.sleep(2)
    c.settle(4)
    js(c, """(() => { const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
        const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
        const d = r.discs.values().next().value; const n = d.userData.rings.find((q) => q.id === %d);
        const v = new V(n.xyz_um[0] + n.radius_um, n.xyz_um[1], n.xyz_um[2]).applyMatrix4(d.matrixWorld).project(r.camera);
        window.__rim = [rect.left + (v.x + 1) / 2 * rect.width, rect.top + (1 - v.y) / 2 * rect.height]; return true; })()""" % soma['id'])
    x, y = js(c, 'window.__rim')
    right_click(c, x, y)
    m = menu(c)
    r.check('several things under the pointer: a list heads the menu',
            m is not None and m['heading'] is not None and len(m['rows']) >= 3, m and (m['heading'], len(m['rows'])))
    if not m:
        return
    r.check('its rows name the annotation, the cell, the section and the volume (ground rule 11)',
            all('(Inferior Monkey)' in t for t in m['rows']) and any('· section' in t for t in m['rows']), m['rows'][:3])
    r.check('the first row, the nearest, is the one the menu is about, and is ticked',
            m['ticked'][0] and sum(m['ticked']) == 1 and m['title'].startswith(f'{cell} · annotation '), (m['title'], m['ticked']))
    r.check('and the cell is listed once among the rows (the surface)',
            sum(1 for t in m['rows'] if t.lstrip('✓ ').startswith('Surface of')) == 1, m['rows'])
    r.check('every item of the menu without a list is still there, Open in Viking included',
            all(item in m['items'] or item in m['parents'] for item in plain['items'] + plain['parents']),
            [i for i in plain['items'] + plain['parents'] if i not in m['items'] + m['parents']])
    r.check('the list is on top, above the first item',
            js(c, """(() => { const m = document.getElementById('ctx-menu');
                return m.children[1].className === 'ctx-heading' && m.children[2].className === 'ctx-candidates'
                    && m.children[3].textContent.trim() === 'Update'; })()"""))

    # Hover: the row lights its annotation up; the surface row lights nothing.
    annotation_rows = [i for i, t in enumerate(m['rows']) if 'Annotation' in t.split('(')[0]]
    xy = row_xy(c, annotation_rows[1])
    c.move(*xy)
    c.settle(3)
    preview = js(c, "(() => { const p = window.sbfsem.renderer._previewing; return p && [p.candidate.kind, p.slot]; })()")
    r.check('the mouse over an annotation row lights that annotation up', preview == ['annotation', 'primary'], preview)
    r.check('and nothing is selected by it', js(c, 'window.sbfsem.renderer.selection === null'))
    surface_row = next(i for i, t in enumerate(m['rows']) if t.lstrip('✓ ').startswith('Surface of'))
    c.move(*row_xy(c, surface_row))
    c.settle(3)
    r.check('over the surface row nothing is lit', js(c, 'window.sbfsem.renderer._previewing === null'))

    # Click a row: it is selected, and the menu is about it.
    want = int(m['rows'][annotation_rows[1]].split()[1 if not m['rows'][annotation_rows[1]].startswith('✓') else 2])
    c.click(*row_xy(c, annotation_rows[1]))
    c.settle(3)
    m2 = menu(c)
    selected = js(c, "(() => { const s = window.sbfsem.renderer.selection; return s && [s.kind, s.locationId]; })()")
    r.check('a click on a row selects it (the render\'s own selection)', selected == ['annotation', want], (selected, want))
    r.check('and the menu stays, about it: the title and the tick moved',
            m2 is not None and f'annotation {want} ' in m2['title'] and m2['ticked'][annotation_rows[1]]
            and sum(m2['ticked']) == 1, m2 and (m2['title'], m2['ticked']))
    # (A menu against the window's right edge keeps that edge: its left follows its width.)
    moved_left = abs((m2['box'][0] + m2['box'][2]) - (m['box'][0] + m['box'][2])) if m2 else None
    rests = m2 is not None and m2['box'][1] + m2['box'][3] >= c.height - 9
    r.check('the menu stays where it was, moving up only as far as its new height needs',
            m2 is not None and moved_left < 1 and (abs(m2['box'][1] - m['box'][1]) < 1 or rests),
            m2 and (m['box'], m2['box']))
    r.check('and the render has the keyboard again (the arrows step the markers)',
            js(c, "document.activeElement === window.sbfsem.renderer.webgl.domElement"))
    r.check('its items now act on that annotation: Hide above and below it, Deselect',
            m2 is not None and any(i.startswith(f'Hide above annotation {want}') for i in m2['items'])
            and 'Deselect' in m2['items'], m2 and [i for i in m2['items'] if 'Hide' in i])
    # The submenus open beside their item and stay on the screen, however long the menu now is.
    fits = []
    for name in ('Analysis', 'Export', 'Export wireframe'):
        xy = js(c, """(() => { const p = [...document.querySelectorAll('#ctx-menu > .ctx-parent')]
            .find((e) => e.firstChild.textContent.trim().replace(/ \\u25b8$/, '') === %s);
            if (!p) return null; const b = p.getBoundingClientRect(); return [b.left + 30, b.top + b.height / 2]; })()""" % json.dumps(name))
        if not xy:
            fits.append((name, 'no such item'))
            continue
        c.move(*xy)
        c.settle(2)
        sub = js(c, """(() => { const p = [...document.querySelectorAll('#ctx-menu > .ctx-parent')]
            .find((e) => e.firstChild.textContent.trim().replace(/ \\u25b8$/, '') === %s);
            const s = p.querySelector('.ctx-sub'); const b = s.getBoundingClientRect();
            return { shown: getComputedStyle(s).display !== 'none', top: b.top, bottom: b.bottom, left: b.left, right: b.right }; })()""" % json.dumps(name))
        if not sub['shown'] or sub['top'] < 0 or sub['bottom'] > c.height or sub['right'] > c.width + 1:
            fits.append((name, sub))
    r.check('the submenus (Analysis, Export, Export wireframe) open on the screen', not fits, fits)
    c.move(40, 40)
    c.settle(2)
    # Open in Viking goes to the annotation chosen, exactly.
    js(c, "window.__opened.length = 0; true")
    js(c, """(() => { const e = [...document.querySelectorAll('#ctx-menu > .ctx-item')].find((b) => b.textContent.trim() === 'Open in Viking');
        if (e) e.click(); return !!e; })()""")
    time.sleep(1.5)
    opened = js(c, 'window.__opened')
    r.check('Open in Viking opens at the annotation chosen', bool(opened) and f'location={want}' in opened[0], opened)

    # A second annotation row becomes a marker, red above the yellow one or blue below it, as the
    # triple-click chooser does.
    right_click(c, x, y)
    m3 = menu(c)
    other = next((i for i, t in enumerate(m3['rows']) if 'Annotation' in t.split('(')[0] and f'Annotation {want} ' not in t), None)
    r.check('the menu opened again lists the rows, with the selection among them', other is not None and m3['heading'] is not None
            and 'red above the yellow' in m3['heading'].lower(), m3 and m3['heading'])
    second = int(m3['rows'][other].lstrip('✓ ').split()[1])
    c.click(*row_xy(c, other))
    c.settle(3)
    pair = js(c, "(() => { const r = window.sbfsem.renderer; const m = r.markers.above || r.markers.below; return [r.selection && r.selection.locationId, m && m.locationId]; })()")
    r.check('a second annotation row sets a marker and keeps the yellow one', pair == [want, second], pair)
    m4 = menu(c)
    r.check('and the menu offers the slab between the two', m4 is not None
            and any(i.startswith('Hide above ') and '(keep ' in i for i in m4['items']), m4 and m4['items'][:8])
    # Esc closes the menu and leaves the selection; the next removes the marker; the next the selection.
    c.key('Escape')
    r.check('Esc closes the menu', menu(c) is None)
    marked = "(window.sbfsem.renderer.markers.above || window.sbfsem.renderer.markers.below)"
    r.check('and leaves the selection and the marker', js(c, f"window.sbfsem.renderer.selection !== null && !!{marked}"))
    c.key('Escape')
    r.check('the next Esc takes the marker', js(c, f"!{marked} && window.sbfsem.renderer.selection !== null"))
    c.key('Escape')
    r.check('and the next the selection', js(c, "window.sbfsem.renderer.selection === null"))
    # The triple click's own chooser is what it was: items to select, never the surface.
    triple_click(c, x, y)
    chooser = menu(c)
    r.check('a triple click still opens the Select menu, with the items and not the surface',
            chooser is not None and chooser['title'].startswith('Select') and chooser['items']
            and all('Surface' not in t for t in chooser['items']) and any(t.startswith('Annotation') for t in chooser['items']),
            chooser and (chooser['title'], chooser['items'][:3]))
    close_menu(c)
    # A row of the list that is not an annotation makes the menu about the cell only.
    right_click(c, x, y)
    m5 = menu(c)
    surface_row = next(i for i, t in enumerate(m5['rows']) if t.lstrip('✓ ').startswith('Surface of'))
    c.click(*row_xy(c, surface_row))
    c.settle(3)
    m6 = menu(c)
    r.check('the surface row makes the menu about the cell, and selects nothing',
            m6 is not None and m6['title'] == cell and js(c, 'window.sbfsem.renderer.selection === null'), m6 and m6['title'])
    # The mouse leaving the menu leaves nothing lit.
    c.move(40, 40)
    c.settle(2)
    r.check('the mouse leaving the list leaves nothing lit', js(c, 'window.sbfsem.renderer._previewing === null'))
    close_menu(c)
    # A short window: the list gives way, the menu's own items do not.
    c.send('Emulation.setDeviceMetricsOverride', width=c.width, height=620, deviceScaleFactor=1, mobile=False,
           screenWidth=2560, screenHeight=1600)
    c.settle(3)
    right_click(c, x, min(y, 330))
    m7 = menu(c)
    inside = m7 is not None and m7['box'][1] >= 7 and m7['box'][1] + m7['box'][3] <= 620 - 7
    r.check('on a 620 px window the whole menu is on screen, its list scrolling to make room',
            inside, m7 and m7['box'])
    close_menu(c)
    c.send('Emulation.setDeviceMetricsOverride', width=c.width, height=c.height, deviceScaleFactor=1, mobile=False,
           screenWidth=2560, screenHeight=1600)


# -- cells -----------------------------------------------------------------------------------------

def cells(c: Chrome, r: Report, ctx):
    r.start('cells: two cells under the pointer, the list names both')
    boot(c, ctx.url)
    a = load(c, ctx.cell)
    b = load(c, ctx.second)
    prepare(c)
    both = [f'c{ctx.cell}', f'c{ctx.second}']
    # A cell is 0.92 opaque by default, and translucent surfaces are drawn in the order of their
    # centers, not pixel by pixel: the one on top of the picture need not be the nearer.  Opaque
    # first, where the picture and the depth agree.
    for key in (a, b):
        js(c, "window.sbfsem.renderer.setStyle(%s, { opacity: 1 }); true" % json.dumps(key))
    c.settle(3)
    colors = js(c, f"[{json.dumps(a)}, {json.dumps(b)}].map((k) => {STATE}.neurons.get(k).color)")
    hues = [colorsys.rgb_to_hsv(*(int(h.lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4)))[0] * 360 for h in colors]
    pixels = drawn(c)

    def owner(rgb) -> str:
        d = [min(abs(hue(rgb) - h), 360 - abs(hue(rgb) - h)) for h in hues]
        return both[d.index(min(d))]

    # Pixels that are one cell's through and through: a blend of two colors, where they meet, is no one's.
    own = {(x, y): owner(rgb) for x, y, rgb in pixels}
    inside = [(x, y, own[x, y]) for x, y in own
              if all(own.get((x + dx, y + dy), own[x, y]) == own[x, y] for dx in (-3, 0, 3) for dy in (-3, 0, 3))]
    r.check('both cells are drawn', {o for _, _, o in inside} == set(both), sorted({o for _, _, o in inside}))
    got = []
    for x, y, who in sample(inside, 60, seed=5):
        right_click(c, x, y)
        m = menu(c)
        got.append((who, cell_of(m['title']) if m else None))
        close_menu(c)
    wrong = [g for g in got if g[0] != g[1]]
    r.check('opaque cells: the menu is of the cell drawn at the pixel (the one in front)', not wrong, wrong[:4])
    # Where a ray goes through both cells (the farther one is behind the nearer): the screen cells both
    # meshes' vertices fall in, then the rays that confirm it -- a scan of every pixel with a ray
    # each (a tenth of a second on cells this size) is minutes.
    crossing = js(c, """(() => { const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
        const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
        const cells = (key) => { const obj = r.meshes.get(key); const solid = obj.children.find((q) => q.userData.part === 'surface');
          const pos = solid.geometry.getAttribute('position'); const seen = new Map(); const v = new V();
          for (let i = 0; i < pos.count; i += 7) {
            v.fromBufferAttribute(pos, i).applyMatrix4(solid.matrixWorld).project(r.camera);
            const x = rect.left + (v.x + 1) / 2 * rect.width, y = rect.top + (1 - v.y) / 2 * rect.height;
            if (v.z > -1 && v.z < 1 && x > rect.left && x < rect.right && y > rect.top && y < rect.bottom)
              seen.set(Math.floor(x / 6) + ',' + Math.floor(y / 6), [x, y]); }
          return seen; };
        const first = cells(%s); const second = cells(%s); const out = [];
        for (const [k, p] of first) {
          if (!second.has(k)) continue;
          const keys = new Set(r._hitsAt(p[0], p[1]).map((h) => { for (let n = h.object; n; n = n.parent) if (n.name) return n.name; }));
          if (keys.size === 2) { out.push(p); if (out.length >= 3) break; } }
        return out; })()""" % (json.dumps(a), json.dumps(b)))
    r.check('the two cells cross on screen', len(crossing) >= 1, crossing)
    if not crossing:
        return
    x, y = crossing[0]
    right_click(c, x, y)
    m = menu(c)
    r.check('opaque: only the cell in front is under the pointer, and no list is offered',
            m is not None and m['heading'] is None and m['title'] in both, m and (m['title'], m['heading']))
    close_menu(c)
    # Translucent: both are seen, so both are listed.
    for key in (a, b):
        js(c, "window.sbfsem.renderer.setStyle(%s, { opacity: 0.35 }); true" % json.dumps(key))
    c.settle(3)
    right_click(c, x, y)
    m = menu(c)
    r.check('both cells are listed, by name and volume',
            m is not None and m['heading'] is not None
            and all(any(t.lstrip('✓ ').startswith('Surface of ' + n) for t in m['rows']) for n in both),
            m and m['rows'])
    first = m['title']
    other = next(i for i, t in enumerate(m['rows']) if not t.startswith('✓'))
    chosen = m['rows'][other].split('Surface of ')[1].split(' ')[0]
    c.click(*row_xy(c, other))
    c.settle(3)
    m2 = menu(c)
    r.check('the row for the other cell makes the menu about that cell', m2 is not None and m2['title'] == chosen and m2['title'] != first,
            m2 and (first, m2['title']))
    r.check('and selects nothing, a surface is never an item', js(c, 'window.sbfsem.renderer.selection === null'))
    # Its Analysis items act on the cell chosen: Annotations table names it.
    js(c, """(() => { const parent = [...document.querySelectorAll('#ctx-menu > .ctx-parent')].find((p) => p.firstChild.textContent.startsWith('Analysis'));
        const item = [...parent.querySelectorAll('.ctx-item')].find((e) => e.textContent.trim() === 'Annotations table'); item.click(); return true; })()""")
    time.sleep(2)
    c.settle(3)
    shown_cell = js(c, "document.querySelector('.tab.active')?.dataset.view")
    r.check('Analysis > Annotations table opens the table', shown_cell == 'table', shown_cell)


# -- lines -----------------------------------------------------------------------------------------

def lines(c: Chrome, r: Report, ctx, by=BY_MENU):
    cells_at, click, lands, _ = by
    r.start(f'lines: a skeleton line does not take {click} on the cell behind it')
    boot(c, ctx.url)
    a = load(c, ctx.cell)
    b = load(c, ctx.second)
    for key in (a, b):
        js(c, "window.sbfsem.renderer.setStyle(%s, { opacity: 1 }); true" % json.dumps(key))
    js(c, """(() => { document.querySelector('.neuron-item .neuron-head').click(); return true; })()""")
    c.settle(2)
    open_panel(c)
    js(c, """[...document.querySelectorAll('.neuron-item.expanded')].forEach((li) => {
        if (li.dataset.key !== %s) li.querySelector('.neuron-head').click(); }); true""" % json.dumps(a))
    c.settle(2)
    set_show(c, 'Show skeleton', True, 4)
    r.check('the first cell draws its skeleton (a line a pixel wide)',
            js(c, "window.sbfsem.renderer.skeletons.has(%s)" % json.dumps(a)))
    prepare(c)
    # Where the second cell's surface passes nearest the first one's skeleton, in 3D.
    near = js(c, """(() => { const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
        const skel = r.skeletons.get(%s); const solid = r.meshes.get(%s).children.find((q) => q.userData.part === 'surface');
        const pos = solid.geometry.getAttribute('position'); const pts = [];
        for (const s of skel.userData.skeleton.segments) for (let i = 0; i < s.points_um.length; i += 10) pts.push(s.points_um[i]);
        let best = null; const p = new V();
        for (const q of pts) { for (let i = 0; i < pos.count; i += 40) {
          const d = Math.hypot(pos.getX(i) - q[0], pos.getY(i) - q[1], pos.getZ(i) - q[2]);
          if (!best || d < best.d) best = { d, at: q }; } }
        return best; })()""" % (json.dumps(a), json.dumps(b)))
    r.check('the two cells pass within a few microns of each other', near is not None and near['d'] < 4, near)
    if not near:
        return
    wrong, total, theirs = [], 0, 0
    for view in ('xz1', 'xz2', 'xy1', 'xy2', 'yz1', 'yz2'):
        js(c, """(async () => { const r = window.sbfsem.renderer; await r.flyTo(r.toWorld(%s, null), { duration: 10, distance: 25 });
            r.setViewpoint(%s); return true; })()""" % (json.dumps(near['at']), json.dumps(view)), wait=True)
        c.settle(4)
        colors = js(c, f"[{json.dumps(a)}, {json.dumps(b)}].map((k) => {STATE}.neurons.get(k).color)")
        hues = [colorsys.rgb_to_hsv(*(int(h.lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4)))[0] * 360 for h in colors]
        names = [f'c{ctx.cell}', f'c{ctx.second}']

        def owner(rgb, hues=hues, names=names):
            d = [min(abs(hue(rgb) - h), 360 - abs(hue(rgb) - h)) for h in hues]
            return names[d.index(min(d))]

        own = {(x, y): owner(rgb) for x, y, rgb in drawn(c)}
        inside = {(x, y): who for (x, y), who in own.items()
                  if all(own.get((x + dx, y + dy), who) == who for dx in (-3, 0, 3) for dy in (-3, 0, 3))}
        skeleton = js(c, """(() => { const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
            const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
            const o = r.skeletons.get(%s); const out = [];
            for (const s of o.userData.skeleton.segments) for (const p of s.points_um) {
              const q = new V(p[0], p[1], p[2]).applyMatrix4(o.matrixWorld).project(r.camera);
              const x = rect.left + (q.x + 1) / 2 * rect.width, y = rect.top + (1 - q.y) / 2 * rect.height;
              if (q.z > -1 && q.z < 1 && x > rect.left && x < rect.right && y > rect.top && y < rect.bottom) out.push([x, y]); }
            return out; })()""" % json.dumps(a))
        beside = sorted({(int(sx + dx), int(sy + dy)) for sx, sy in skeleton
                         for dx, dy in ((14, 0), (-14, 0), (0, 14), (0, -14), (25, 0), (-25, 0), (0, 25), (0, -25))
                         if inside.get((int(sx + dx), int(sy + dy))) == names[1]})
        for got in cells_at(c, sample(beside, 12, seed=7)):
            total += 1
            theirs += got == names[1]
            if got != names[1]:
                wrong.append((view, got))
    r.check('pixels of the second cell 14 to 25 px from the first one\'s skeleton line, in six views, are the second cell\'s',
            total >= 20 and not wrong, f'{total} clicks, {theirs} the second cell\'s, wrong: {wrong[:6]}')
    # ... while a click on the line itself is the first cell's.
    js(c, "window.sbfsem.renderer.setViewpoint('xz1'); true")
    c.settle(3)
    on_line = js(c, """(() => { const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
        const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
        const o = r.skeletons.get(%s); const out = [];
        for (const s of o.userData.skeleton.segments) for (const p of s.points_um) {
          const q = new V(p[0], p[1], p[2]).applyMatrix4(o.matrixWorld).project(r.camera);
          const x = rect.left + (q.x + 1) / 2 * rect.width, y = rect.top + (1 - q.y) / 2 * rect.height;
          if (q.z > -1 && q.z < 1 && x > rect.left + 4 && x < rect.right - 4 && y > rect.top + 4 && y < rect.bottom - 4) out.push([x, y]); }
        return out; })()""" % json.dumps(a))
    got = cells_at(c, sample(on_line, 8, seed=2))
    r.check(f'{click} on the line itself is its cell\'s (the line is the thing under the pointer)',
            all(t == f'c{ctx.cell}' for t in got), got)


# -- compare ---------------------------------------------------------------------------------------

def compare(c: Chrome, r: Report, ctx):
    r.start('compare: a Render compare pane opens the compared cell\'s menu, for every layer it draws')
    boot(c, ctx.url)
    load(c, ctx.cell)
    js(c, "document.querySelector('.tab[data-view=\"compare\"]').click(); true")
    c.settle(3)
    pane = js(c, "!!window.sbfsem.compare && window.sbfsem.compare.panes.length")
    r.check('the compare tab has panes', bool(pane), pane)
    js(c, "document.getElementById('compare-build').click(); true")
    ok = c.wait_for("window.sbfsem.compare.panes[0].renderer.meshes.size > 0 "
                    "&& !window.sbfsem.compare.running", 400)
    r.check('the first pane has built its surface', ok)
    if not ok:
        return
    js(c, "document.head.append(Object.assign(document.createElement('style'), { textContent: "
          "'.compare-legend, .job-card, .compare-head { visibility: hidden !important; }' })); true")

    def points_of(layer_index: int, n: int = 10, seed: int = 4):
        """Screen points over the vertices of one layer's mesh, in pane 0."""
        return js(c, """(() => { const v = window.sbfsem.compare; const pane = v.panes[0]; const r = pane.renderer;
            const layer = pane.layers[%d]; const solid = r.meshes.get(layer.key).children.find((q) => q.userData.part === 'surface');
            const V = r.bounds.min.constructor; const rect = r.webgl.domElement.getBoundingClientRect();
            r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
            const pos = solid.geometry.getAttribute('position'); const out = [];
            let seed = %d;
            const next = () => { seed = (seed * 1664525 + 1013904223) %% 4294967296; return seed / 4294967296; };
            for (let tries = 0; tries < 4000 && out.length < %d; tries += 1) {
              const i = Math.floor(next() * pos.count);
              const q = new V(pos.getX(i), pos.getY(i), pos.getZ(i)).applyMatrix4(solid.matrixWorld).project(r.camera);
              const x = rect.left + (q.x + 1) / 2 * rect.width; const y = rect.top + (1 - q.y) / 2 * rect.height;
              if (q.z > -1 && q.z < 1 && x > rect.left + 6 && x < rect.right - 6 && y > rect.top + 6 && y < rect.bottom - 6) out.push([x, y]);
            }
            return out; })()""" % (layer_index, seed, n))

    cell = f'c{ctx.cell}'
    got = titles(c, points_of(0))
    r.check('a right click in a pane opens the compared cell\'s menu', all(cell_of(t) == cell for t in got),
            sorted(set(map(str, got))))
    right_click(c, *points_of(0, 1)[0])
    m = menu(c)
    r.check('with Open in Viking, and the items that act on the cell', m is not None and 'Open in Viking' in m['items']
            and 'Open GraphApp' in m['items'], m and m['items'][:8])
    close_menu(c)

    # A second layer in the same pane, another shape (the tube sweep); each layer on its own.
    js(c, """(() => { const v = window.sbfsem.compare; const pane = v.panes[0]; const layer = v.addLayer(pane);
        v._techniquePicked(layer, 'tube'); return true; })()""")
    js(c, "document.getElementById('compare-build').click(); true")
    ok = c.wait_for("window.sbfsem.compare.panes[0].renderer.meshes.size >= 2 && !window.sbfsem.compare.running", 400)
    r.check('the pane draws two layers', ok)
    if not ok:
        return
    c.settle(3)
    for index, other in ((1, 0), (0, 1)):
        js(c, """(() => { const v = window.sbfsem.compare; const pane = v.panes[0];
            v.setLayerVisible(pane.layers[%d], false); v.setLayerVisible(pane.layers[%d], true); return true; })()"""
           % (other, index))
        c.settle(3)
        got = titles(c, points_of(index, 12))
        r.check(f'layer {index + 1} alone on screen: a right click on its mesh opens the cell\'s menu',
                all(cell_of(t) == cell for t in got), sorted(set(map(str, got))))
    js(c, """(() => { const v = window.sbfsem.compare; const pane = v.panes[0];
        for (const layer of pane.layers) v.setLayerVisible(layer, true); return true; })()""")
    c.settle(3)
    # Both on screen: the cell is listed once, not once a layer.
    both = js(c, """(() => { const pane = window.sbfsem.compare.panes[0]; const r = pane.renderer; const V = r.bounds.min.constructor;
        const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
        const cells = (key) => { const solid = r.meshes.get(key).children.find((q) => q.userData.part === 'surface');
          const pos = solid.geometry.getAttribute('position'); const seen = new Map(); const v = new V();
          for (let i = 0; i < pos.count; i += 7) {
            v.fromBufferAttribute(pos, i).applyMatrix4(solid.matrixWorld).project(r.camera);
            const x = rect.left + (v.x + 1) / 2 * rect.width, y = rect.top + (1 - v.y) / 2 * rect.height;
            if (v.z > -1 && v.z < 1 && x > rect.left && x < rect.right && y > rect.top && y < rect.bottom)
              seen.set(Math.floor(x / 4) + ',' + Math.floor(y / 4), [x, y]); }
          return seen; };
        const first = cells(pane.layers[0].key); const second = cells(pane.layers[1].key);
        for (const [k, p] of first) {
          if (!second.has(k)) continue;
          const keys = new Set(r.contextCandidatesAt(p[0], p[1]).filter((q) => q.kind === 'surface').map((q) => q.key));
          if (keys.size === 2) return p; }
        return null; })()""")
    r.check('two layers cross under a pixel', both is not None, both)
    if both:
        right_click(c, *both)
        m = menu(c)
        r.check('and the cell is one thing there, not two: no list', m is not None and m['title'] == cell and m['heading'] is None,
                m and (m['title'], m['heading'], m['rows']))
        close_menu(c)
    # The pane's annotations overlay (one per pane, under a key of its own): a ring names its annotation,
    # and a row of the list only says what the menu is about, since a pane has no selection.
    js(c, "window.sbfsem.compare.setAnnotations(window.sbfsem.compare.panes[0], true); true")
    ok = c.wait_for("window.sbfsem.compare.panes[0].renderer.discs.size > 0", 120)
    r.check('the pane draws the annotations', ok)
    if ok:
        c.settle(3)
        rim = js(c, """(() => { const pane = window.sbfsem.compare.panes[0]; const r = pane.renderer; const V = r.bounds.min.constructor;
            const d = r.discs.values().next().value; const rect = r.webgl.domElement.getBoundingClientRect();
            r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
            const big = d.userData.rings.reduce((a, n) => (!a || n.radius_um > a.radius_um ? n : a), null);
            const q = new V(big.xyz_um[0] + big.radius_um, big.xyz_um[1], big.xyz_um[2]).applyMatrix4(d.matrixWorld).project(r.camera);
            return [rect.left + (q.x + 1) / 2 * rect.width, rect.top + (1 - q.y) / 2 * rect.height, big.id]; })()""")
        right_click(c, rim[0], rim[1])
        m = menu(c)
        # (A stack of rings: the one nearest the pointer is whichever rim is a hair closer.)
        r.check('a click on a ring of the overlay names an annotation of the cell',
                m is not None and m['title'].startswith(f'{cell} · annotation ')
                and m['title'].split('annotation ')[1].split(' ')[0] in ''.join(m['rows'][:1]), m and (m['title'], m['rows'][:1]))
        if m and m['rows']:
            r.check('its list names the cell once', sum(1 for t in m['rows'] if t.lstrip('\u2713 ').startswith('Surface of')) <= 1,
                    m['rows'])
            second = next((i for i, t in enumerate(m['rows']) if not t.startswith('\u2713')), None)
            if second is not None:
                c.click(*row_xy(c, second))
                c.settle(3)
                r.check('a row there only makes the menu about it: the main render selects nothing',
                        js(c, 'window.sbfsem.renderer.selection === null && !window.sbfsem.renderer.markers.above'
                           ' && !window.sbfsem.renderer.markers.below'))
        close_menu(c)
        js(c, "window.sbfsem.compare.setAnnotations(window.sbfsem.compare.panes[0], false); true")

    # A pane with nothing under the pointer opens nothing.
    left, top, width, height = js(c, """(() => { const r = window.sbfsem.compare.panes[0].renderer.webgl.domElement.getBoundingClientRect();
        return [r.left, r.top, r.width, r.height]; })()""")
    r.check('empty space in a pane opens nothing', titles(c, [(left + 8, top + height - 8)])[0] is None)


# -- markers ------------------------------------------------------------------------------------

#: Where on a ring's rim a triple click picks that ring alone: tries the rim at 24 angles.
RIM_OF = """(id) => { const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
    const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld(); r.group.updateMatrixWorld(true);
    const d = r.discs.values().next().value; const n = d.userData.rings.find((q) => q.id === id);
    for (let k = 0; k < 24; k += 1) {
      const a = (k / 24) * Math.PI * 2;
      const v = new V(n.xyz_um[0] + n.radius_um * Math.cos(a), n.xyz_um[1] + n.radius_um * Math.sin(a), n.xyz_um[2])
        .applyMatrix4(d.matrixWorld).project(r.camera);
      const x = rect.left + (v.x + 1) / 2 * rect.width; const y = rect.top + (1 - v.y) / 2 * rect.height;
      if (x < rect.left + 20 || x > rect.right - 20 || y < rect.top + 140 || y > rect.bottom - 60) continue;
      const found = r.candidatesAt(x, y);
      if (found.length === 1 && found[0].locationId === id) return [x, y];
    }
    return null; }"""

TABLE = """(() => { const t = document.getElementById('marker-table'); if (!t || t.hidden) return null;
    const rows = [...t.querySelectorAll('tbody tr')];
    return rows.map((tr) => ({ slot: tr.dataset.slot, color: tr.style.getPropertyValue('--mk').trim(),
      cells: [...tr.children].map((td) => td.textContent.trim()) })); })()"""


def markers(c: Chrome, r: Report, ctx):
    r.start('markers: red above the yellow one, blue below, a row each')
    boot(c, ctx.url)
    load(c, ctx.cell)
    open_panel(c)
    set_show(c, 'Show annotations', True, 3)
    c.wait_for('window.sbfsem.renderer.discs.size > 0', 60)
    # A process of the cell: a ring with rings on sections above and below it, near it in XY,
    # seen from the side so each is a separate line.
    plan = js(c, """(() => { const d = window.sbfsem.renderer.discs.values().next().value;
        const rings = d.userData.rings.filter((n) => n.radius_um > 0.15 && n.radius_um < 1.5);
        const near = (a, dz) => rings.filter((n) => n.z_section === a.z_section + dz
          && Math.hypot(n.xyz_um[0] - a.xyz_um[0], n.xyz_um[1] - a.xyz_um[1]) < 0.8);
        for (const a of rings) {
          const up = near(a, 4); const down = near(a, -4);
          if (up.length === 1 && down.length === 1 && near(a, 0).length === 1)
            return { y: a.id, up: up[0].id, down: down[0].id, xyz: a.xyz_um, sec: a.z_section,
                     upSec: up[0].z_section, downSec: down[0].z_section };
        }
        return null; })()""")
    if not plan:
        r.check('a process with rings four sections above and below one ring was found', False)
        return
    js(c, "window.sbfsem.renderer.setViewpoint('xz1'); true")
    c.settle(3)
    js(c, """(() => { const r = window.sbfsem.renderer;
        r.flyTo(r.toWorld(%s, 'NeitzInferiorMonkey'), { duration: 10, distance: 4 }); return true; })()"""
       % json.dumps(plan['xyz']))
    time.sleep(1.5)
    c.settle(4)
    rim = {k: js(c, f"({RIM_OF})({plan[k]})") for k in ('y', 'up', 'down')}
    r.check('each of the three rings can be clicked alone', all(rim.values()), rim)
    if not all(rim.values()):
        return

    triple_click(c, *rim['y'])
    t = js(c, TABLE)
    r.check('a triple click selects the ring, and the table has its yellow row',
            js(c, 'window.sbfsem.renderer.selection && window.sbfsem.renderer.selection.locationId') == plan['y']
            and t is not None and [row['slot'] for row in t] == ['primary'] and str(plan['y']) in t[0]['cells'], t)
    triple_click(c, *rim['up'])
    triple_click(c, *rim['down'])
    t = js(c, TABLE) or []
    slots = [row['slot'] for row in t]
    r.check('a ring above it becomes red and one below it blue; the rows run red, yellow, blue',
            slots == ['above', 'primary', 'below']
            and js(c, 'window.sbfsem.renderer.markers.above.locationId') == plan['up']
            and js(c, 'window.sbfsem.renderer.markers.below.locationId') == plan['down'], t)
    colors = js(c, "['above', 'primary', 'below'].map((s) => window.sbfsem.renderer.markerColor(s))")
    r.check('each row is in its marker\'s color', [row['color'] for row in t] == colors, (colors, [row['color'] for row in t]))
    if slots == ['above', 'primary', 'below']:
        red, yellow, blue = t
        r.check('each row names its own annotation, section, and the Z from the yellow one',
                red['cells'][2] == str(plan['up']) and red['cells'][4] == str(plan['upSec'])
                and red['cells'][5] == f"+{plan['upSec'] - plan['sec']}"
                and blue['cells'][2] == str(plan['down']) and blue['cells'][5] == f"\u2212{plan['sec'] - plan['downSec']}"
                and yellow['cells'][5] == '\u2014' and float(red['cells'][7]) > 0, t)
    r.check('the readout over the render names the one chosen last (the blue one), not the yellow one',
            f"Location ID {plan['down']} " in js(c, "document.getElementById('pick-location').textContent"),
            js(c, "document.getElementById('pick-location').textContent"))

    # A click on the red row's Location ID copies it, with the volume named.
    js(c, """document.querySelector('#marker-table tr[data-slot="above"] .mk-copy').click()""")
    status = js(c, "document.querySelector('.status').textContent")
    r.check('a click on a row\'s Location ID copies it, naming the volume',
            status.startswith(f"Copied \u201c{plan['up']}\u201d, the red marker") and 'Inferior Monkey' in status, status)
    # The blue row's up arrow steps it toward the yellow one.
    before = js(c, 'window.sbfsem.renderer.markers.below.section')
    js(c, """document.querySelector('#marker-table tr[data-slot="below"] .mk-step').click()""")
    c.wait_for('window.sbfsem.renderer.markers.below === null || window.sbfsem.renderer.markers.below.section !== %d' % before, 5)
    after = js(c, 'window.sbfsem.renderer.markers.below && window.sbfsem.renderer.markers.below.section')
    r.check('a row\'s \u25b2 steps its marker up along the links, and draws it as just chosen',
            after is not None and after > before
            and js(c, "window.sbfsem.renderer.highlightGroup.children.some((o) => o.userData.slot === 'below' && o.userData.settle)"),
            (before, after))
    # The arrow keys move the marker moved last (the blue one), down again.
    js(c, 'window.sbfsem.renderer.webgl.domElement.focus(); true')
    c.key('ArrowDown')
    c.wait_for('window.sbfsem.renderer.markers.below && window.sbfsem.renderer.markers.below.section < %d' % after, 5)
    r.check('\u2193 steps the marker moved last', js(c, 'window.sbfsem.renderer.markers.below.section') < after,
            js(c, 'window.sbfsem.renderer.markers.below.section'))

    # Hide above / below go by the red and the blue.
    rx, ry = rim['y']
    right_click(c, rx, ry)
    m = menu(c)
    items = m['items'] if m else []
    r.check('the menu hides above the red one and below the blue one, and keeps the slab between',
            any(i.startswith(f"Hide above red {plan['up']} ") for i in items)
            and any(i.startswith('Hide below blue ') for i in items)
            and any(i.startswith('Hide above red and below blue (keep ') for i in items), items[:10])
    close_menu(c)

    # Show in Viking (2026-10-20): a button in each row's Viking cell, for that row's annotation.
    shows = js(c, """(() => [...document.querySelectorAll('#marker-table tbody tr')].map((tr) => {
        const b = tr.querySelector('.mk-viking'); return b ? [tr.dataset.slot, b.textContent, b.title] : [tr.dataset.slot, null]; }))()""")
    r.check('each row has a Show in Viking button for its own annotation',
            bool(shows) and len(shows) == 3 and all(x[1] == '↗' and x[2].startswith('Show in Viking') for x in shows)
            and str(plan['up']) in shows[0][2] and str(plan['y']) in shows[1][2], shows)
    # Off Windows a launch says so instead; the button reaches it (macOS here).
    js(c, """document.querySelector('#marker-table tr[data-slot="primary"] .mk-viking').click()""")
    c.settle(1)
    said = js(c, "document.getElementById('status').textContent")
    r.check('it goes to Open in Viking (off Windows: says Viking runs on Windows only)',
            'Windows' in (said or '') or 'Viking' in (said or ''), said)

    # Wide and narrow windows: the table stays inside the render, clear of the hint.
    fits = []
    for width in (900, 1140, 1440, 1920):
        c.emulate('mac', width=width, height=900)
        c.settle(4)
        fits.append(js(c, """(() => { const t = document.getElementById('marker-table').getBoundingClientRect();
            const v = document.getElementById('viewport').getBoundingClientRect();
            const h = document.getElementById('nav-hint'); const hb = h.getBoundingClientRect();
            const hintShown = getComputedStyle(h).display !== 'none' && hb.width > 0;
            const clear = !hintShown || t.right <= hb.left || t.top >= hb.bottom || t.bottom <= hb.top;
            return [%d, t.left >= v.left && t.right <= v.right, clear, Math.round(t.width)]; })()""" % width))
    c.emulate('mac', width=1440, height=900)
    c.settle(3)
    r.check('the table fits inside the render and clear of the hint at 900, 1140, 1440 and 1920 px',
            all(f[1] and f[2] for f in fits), fits)

    # Esc: the marker moved last, the other, then the selection.
    js(c, 'window.sbfsem.renderer.webgl.domElement.focus(); true')
    c.key('Escape')
    one = js(c, '[!!window.sbfsem.renderer.markers.above, !!window.sbfsem.renderer.markers.below]')
    c.key('Escape')
    c.key('Escape')
    r.check('Esc takes the marker moved last, then the other, then the selection, and the table goes',
            one == [True, False] and js(c, 'window.sbfsem.renderer.selection === null')
            and js(c, "document.getElementById('marker-table').hidden"), one)


#: The z-slice state, for the checks: each slot's annotation, section, and its z-slice's scope,
#: section and Location IDs; and each slot's extra rings and their color.
ZSLICE = """(() => { const r = window.sbfsem.renderer; const out = {};
    for (const s of ['primary', 'above', 'below']) { const m = r.slotItem(s); const z = r.slices[s];
      const extra = r.highlightGroup.children.filter((o) => o.userData.slot === s && o.userData.extra);
      out[s] = m ? { id: m.locationId, key: m.key, section: m.section,
        slice: z ? { scope: z.scope, section: z.section, ids: z.items.map((c) => c.key + '#' + c.locationId),
                     sections: [...new Set(z.items.map((c) => c.section))] } : null,
        extra: extra.length,
        colors: [...new Set(extra.map((o) => o.children[0]?.material.color.getStyle()))],
        own: r.markerColor(s) } : null; }
    return out; })()"""


def zslice(c: Chrome, r: Report, ctx):
    r.start('zslice: every annotation on the section with the yellow, red or blue one, stepped by section')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    open_panel(c)
    set_show(c, 'Show annotations', True, 3)
    c.wait_for('window.sbfsem.renderer.discs.size > 0', 60)
    # The cell's sections and the annotations on each, as the app reads them; the one chosen is a ring.
    plan = js(c, """(async () => { const d = window.sbfsem.renderer.discs.get(%s);
        const nodes = await fetch('/api/neurons/' + %s + '/nodes?cell_only=true').then((x) => x.json());
        const ringed = new Set(d.userData.rings.map((n) => n.id));
        const by = new Map();
        for (const n of nodes) by.set(n.z_section, (by.get(n.z_section) || []).concat(n.id));
        const sections = [...by.keys()].sort((a, b) => a - b);
        // A section with several annotations, one of them a ring, and at least two more sections above it.
        for (const [i, s] of sections.entries()) {
          const ids = by.get(s); const ring = ids.find((id) => ringed.has(id));
          if (ids.length >= 3 && ring && i + 2 < sections.length && i > 1)
            return { section: s, ids, ring, up: sections[i + 1], up2: sections[i + 2], down: sections[i - 1],
                     upCount: by.get(sections[i + 1]).length, n: sections.length };
        }
        return null; })()""" % (json.dumps(key), json.dumps(key)), wait=True)
    r.check('c%d has a section with three or more annotations and sections above and below it' % ctx.cell,
            plan is not None, plan)
    if not plan:
        return
    # Select one of them, as a triple click does, and bring it into view from above.
    first = plan['ring']
    js(c, """(() => { const r = window.sbfsem.renderer; const d = r.discs.get(%s);
        const n = d.userData.rings.find((q) => q.id === %d);
        r.select({ kind: 'annotation', key: %s, locationId: n.id, section: n.z_section, zUm: n.xyz_um[2],
                   point: [...n.xyz_um], px: 0, depth: 0, through: true });
        r.setViewpoint('xy1'); r.flyTo(r.toWorld(n.xyz_um, 'NeitzInferiorMonkey'), { duration: 10, distance: 30 });
        return true; })()""" % (json.dumps(key), first, json.dumps(key)))
    time.sleep(1.5)
    c.settle(4)
    rim = js(c, f"({RIM_OF})({first})")
    r.check('the selected ring can be right-clicked alone', bool(rim), rim)
    if not rim:
        return

    def menu_at(xy):
        right_click(c, *xy)
        m = menu(c)
        return m['items'] if m else []

    items = menu_at(rim)
    want_cell = f"Select all annotations on z-slice {plan['section']} (same cell only)"
    want_all = f"Select all annotations on z-slice {plan['section']}"
    r.check('the menu offers both, named by the section', want_cell in items and want_all in items,
            [i for i in items if i.startswith('Select all')])
    js(c, """[...document.querySelectorAll('#ctx-menu > .ctx-item')].find((e) => e.textContent.trim() === %s).click()"""
       % json.dumps(want_cell))
    c.wait_for('window.sbfsem.renderer.slices.primary !== null', 10)
    z = js(c, ZSLICE)
    p = z['primary']
    r.check('(same cell only): every annotation of the cell on that section, the rest drawn in yellow',
            p['slice'] and p['slice']['scope'] == 'cell' and p['slice']['sections'] == [plan['section']]
            and sorted(int(x.split('#')[1]) for x in p['slice']['ids']) == sorted(plan['ids'])
            and p['extra'] == len(plan['ids']) - 1 and p['colors'] == [p['own']], p)
    t = js(c, TABLE) or []
    r.check('the table\'s Z says how many', bool(t) and t[0]['cells'][4] == f"{plan['section']} ×{len(plan['ids'])}",
            t[0]['cells'] if t else t)
    items = menu_at(rim)
    r.check('the menu ticks the one in force', ('✓ ' + want_cell) in items,
            [i for i in items if 'z-slice' in i])
    close_menu(c)

    # Up from the yellow's z-slice: a red one on the next section up with annotations of the cell.
    js(c, 'window.sbfsem.renderer.webgl.domElement.focus(); true')
    c.key('ArrowUp')
    c.wait_for('window.sbfsem.renderer.markers.above !== null', 10)
    z = js(c, ZSLICE)
    a = z['above']
    r.check('↑ marks the next section up with annotations as a red z-slice of the same scope',
            a is not None and a['section'] == plan['up'] and a['slice']['scope'] == 'cell'
            and a['slice']['sections'] == [plan['up']] and len(a['slice']['ids']) == plan['upCount']
            and a['extra'] == plan['upCount'] - 1 and (a['colors'] == [a['own']] or a['extra'] == 0), a)
    c.key('ArrowUp')
    c.wait_for('window.sbfsem.renderer.markers.above && window.sbfsem.renderer.markers.above.section === %d'
               % plan['up2'], 10)
    a = js(c, ZSLICE)['above']
    r.check('↑ again steps it to the section after, by section and not along the links',
            a is not None and a['section'] == plan['up2'] and a['slice']['sections'] == [plan['up2']], a)
    status = js(c, "document.getElementById('status').textContent")
    r.check('the status line says how many are on the z-slice', f"on z-slice {plan['up2']}" in status, status)
    c.key('ArrowDown')
    c.key('ArrowDown')
    c.wait_for('window.sbfsem.renderer.markers.above === null', 10)
    z = js(c, ZSLICE)
    r.check('↓ twice brings it back to the yellow\'s section, where it goes',
            z['above'] is None and z['primary']['slice'] is not None, js(c, "document.getElementById('status').textContent"))

    # Shift moves the yellow's z-slice.
    c.key('ArrowUp', SHIFT)
    c.wait_for('window.sbfsem.renderer.selection.section === %d' % plan['up'], 10)
    p = js(c, ZSLICE)['primary']
    r.check('Shift+↑ moves the yellow z-slice to the next section up',
            p['section'] == plan['up'] and p['slice']['sections'] == [plan['up']], p)
    c.key('ArrowDown', SHIFT)
    c.wait_for('window.sbfsem.renderer.selection.section === %d' % plan['section'], 10)

    # Every loaded cell: a second cell of the volume, on the sections it shares with the first.
    second = load(c, ctx.second)
    set_show(c, 'Show annotations', True, 3)
    shared = js(c, """(async () => {
        const get = (k) => fetch('/api/neurons/' + k + '/nodes?cell_only=true').then((x) => x.json());
        const [a, b] = await Promise.all([get(%s), get(%s)]);
        const rings = window.sbfsem.renderer.discs.get(%s).userData.rings;
        const bs = new Set(b.map((n) => n.z_section));
        const both = rings.filter((n) => bs.has(n.z_section)).sort((x, y) => x.z_section - y.z_section);
        if (!both.length) return null;
        const ring = both[Math.floor(both.length / 2)]; const s = ring.z_section;
        return { section: s, ring: ring.id, a: a.filter((n) => n.z_section === s).map((n) => n.id),
                 b: b.filter((n) => n.z_section === s).map((n) => n.id) }; })()"""
                % (json.dumps(key), json.dumps(second), json.dumps(key)), wait=True)
    if not shared:
        r.check(f'c{ctx.cell} and c{ctx.second} share a section (else every loaded cell is not tested)', False)
        return
    js(c, """(() => { const r = window.sbfsem.renderer; const n = r.discs.get(%s).userData.rings.find((q) => q.id === %d);
        r.select({ kind: 'annotation', key: %s, locationId: n.id, section: n.z_section, zUm: n.xyz_um[2],
                   point: [...n.xyz_um], px: 0, depth: 0, through: true }); return true; })()"""
       % (json.dumps(key), shared['ring'], json.dumps(key)))
    c.settle(3)
    # Off every cell: a corner of the render with nothing under it.
    corner = js(c, """(() => { const r = window.sbfsem.renderer; const b = r.webgl.domElement.getBoundingClientRect();
        for (const [fx, fy] of [[0.05, 0.9], [0.95, 0.9], [0.05, 0.3], [0.95, 0.3], [0.5, 0.95]]) {
          const x = b.left + fx * b.width; const y = b.top + fy * b.height;
          if (!r.candidatesAt(x, y, { context: true }).length) return [x, y]; }
        return null; })()""")
    items = menu_at(corner) if corner else []
    want_all = f"Select all annotations on z-slice {shared['section']}"
    r.check('right-clicked off every cell, the menu offers the selection\'s z-slice', want_all in items,
            (corner, [i for i in items if 'z-slice' in i]))
    if want_all in items:
        js(c, """[...document.querySelectorAll('#ctx-menu > .ctx-item')].find((e) => e.textContent.trim() === %s).click()"""
           % json.dumps(want_all))
        c.wait_for('window.sbfsem.renderer.slices.primary && window.sbfsem.renderer.slices.primary.scope === "volume"', 15)
        p = js(c, ZSLICE)['primary']
        ids = set(p['slice']['ids']) if p and p['slice'] else set()
        expect = {f'{key}#{i}' for i in shared['a']} | {f'{second}#{i}' for i in shared['b']}
        r.check('(every loaded cell): both cells\' annotations on the section, all drawn yellow',
                ids == expect and p['extra'] == len(expect) - 1 and p['colors'] == [p['own']],
                (len(ids), len(expect), p and p['colors']))

    # Esc: the z-slice first, then the selection.
    js(c, 'window.sbfsem.renderer.webgl.domElement.focus(); true')
    c.key('Escape')
    one = js(c, ZSLICE)['primary']
    c.key('Escape')
    r.check('Esc takes the z-slice, then the selection',
            one is not None and one['slice'] is None and one['extra'] == 0
            and js(c, 'window.sbfsem.renderer.selection === null'), one)


def markerthird(c: Chrome, r: Report, ctx):
    """The third marker from the yellow (reported 2026-10-17): with a red marker stepped up,
    choosing the yellow ring again and pressing Down adds a blue one below the yellow (it moved
    the red one); the same with a blue marker and Up.  And what a triple click on a ring lists."""
    r.start('markerthird: after choosing the yellow again, the arrows add the other marker')
    boot(c, ctx.url)
    load(c, ctx.cell)
    open_panel(c)
    set_show(c, 'Show annotations', True, 3)
    c.wait_for('window.sbfsem.renderer.discs.size > 0', 60)
    plan = js(c, """(() => { const d = window.sbfsem.renderer.discs.values().next().value;
        const rings = d.userData.rings.filter((n) => n.radius_um > 0.15 && n.radius_um < 1.5);
        const near = (a, dz) => rings.filter((n) => n.z_section === a.z_section + dz
          && Math.hypot(n.xyz_um[0] - a.xyz_um[0], n.xyz_um[1] - a.xyz_um[1]) < 0.8);
        for (const a of rings) {
          if (near(a, 4).length === 1 && near(a, -4).length === 1 && near(a, 0).length === 1)
            return { y: a.id, xyz: a.xyz_um, sec: a.z_section };
        }
        return null; })()""")
    if not plan:
        r.check('a process with rings above and below one ring was found', False)
        return
    js(c, "window.sbfsem.renderer.setViewpoint('xz1'); true")
    c.settle(3)
    js(c, """(() => { const r = window.sbfsem.renderer;
        r.flyTo(r.toWorld(%s, 'NeitzInferiorMonkey'), { duration: 10, distance: 4 }); return true; })()"""
       % json.dumps(plan['xyz']))
    time.sleep(1.5)
    c.settle(4)
    rim = js(c, f"({RIM_OF})({plan['y']})")
    r.check('the yellow ring can be clicked alone', bool(rim), rim)
    if not rim:
        return
    sel = 'window.sbfsem.renderer.selection && window.sbfsem.renderer.selection.locationId'
    mk = "(s) => { const m = window.sbfsem.renderer.markers[s]; return m ? m.section : null; }"

    def step(key: str, settle: str) -> None:
        js(c, 'window.sbfsem.renderer.webgl.domElement.focus(); true')
        c.key(key)
        c.wait_for(settle, 5)
        c.settle(2)

    def single_click(x: float, y: float) -> None:
        c.move(x, y)
        c.send('Input.dispatchMouseEvent', type='mousePressed', x=x, y=y, button='left', buttons=1, clickCount=1)
        c.send('Input.dispatchMouseEvent', type='mouseReleased', x=x, y=y, button='left', buttons=0, clickCount=1)
        c.settle(3)

    for first, second, word, again in (('ArrowUp', 'ArrowDown', 'red', 'triple'),
                                       ('ArrowDown', 'ArrowUp', 'blue', 'triple'),
                                       ('ArrowUp', 'ArrowDown', 'red', 'single')):
        js(c, 'window.sbfsem.renderer.clearSelection(); true')
        triple_click(c, *rim)
        ok = js(c, sel) == plan['y']
        own = 'above' if first == 'ArrowUp' else 'below'
        far = 'below' if own == 'above' else 'above'
        step(first, f'({mk})("{own}") !== null')
        once = js(c, f'({mk})("{own}")')
        step(first, f'({mk})("{own}") !== {once}')
        twice = js(c, f'({mk})("{own}")')
        r.check(f'{first} twice: a {word} marker, stepped away from the yellow',
                ok and once is not None and twice is not None and twice != once, (ok, once, twice))
        if again == 'triple':
            triple_click(c, *rim)
        else:
            single_click(*rim)
        r.check(f'a {again} click on the yellow ring keeps it the yellow', js(c, sel) == plan['y'], js(c, sel))
        step(second, f'({mk})("{far}") !== null')
        third = js(c, f'({mk})("{far}")')
        kept = js(c, f'({mk})("{own}")')
        r.check(f'after a {again} click on the yellow, {second} adds the other marker beside it, and the '
                f'{word} one stays where it was',
                third is not None and kept == twice and (third < plan['sec'] if far == 'below' else third > plan['sec']),
                {'third': third, word: kept, 'was': twice, 'yellow section': plan['sec']})
    js(c, 'window.sbfsem.renderer.clearSelection(); true')

    # What a triple click on one ring lists, at this zoom (4 um away) and at a whole cell's.
    counts = {}
    for label, distance in (('close', 4), ('near', 15), ('mid', 30), ('cell', 60)):
        js(c, """(() => { const r = window.sbfsem.renderer;
            r.flyTo(r.toWorld(%s, 'NeitzInferiorMonkey'), { duration: 10, distance: %d }); return true; })()"""
           % (json.dumps(plan['xyz']), distance))
        time.sleep(1.2)
        c.settle(4)
        counts[label] = js(c, """(async () => { const M = await import('/static/js/markers.js');
            const r = window.sbfsem.renderer; const V = r.bounds.min.constructor;
            const rect = r.webgl.domElement.getBoundingClientRect(); r.camera.updateMatrixWorld();
            const d = r.discs.values().next().value; const n = d.userData.rings.find((q) => q.id === %d);
            const v = new V(n.xyz_um[0] + n.radius_um, n.xyz_um[1], n.xyz_um[2]).applyMatrix4(d.matrixWorld).project(r.camera);
            const x = rect.left + (v.x + 1) / 2 * rect.width; const y = rect.top + (1 - v.y) / 2 * rect.height;
            const all = r.candidatesAt(x, y);
            return { n: all.length, annotations: all.filter((q) => q.kind === 'annotation').length,
                     kinds: [...new Set(all.map((q) => q.kind))],
                     px: all.map((q) => Math.round(q.px * 10) / 10),
                     sections: all.map((q) => q.section ?? null),
                     offered: M.choosable(all).length }; })()""" % plan['y'], wait=True)
    r.check('a triple click on a ring offers it, and a ring it overlaps, not its neighbors a few pixels off '
            '(it listed six at a whole cell\'s zoom)',
            all(v and 1 <= v['offered'] <= 2 for v in counts.values()), counts)


def leftpick(c: Chrome, r: Report, ctx):
    """The left click's pick (2.6.5): the same pixels as `thin` and `lines`, by the left button.

    It was one ray with three's line threshold of a micron: 44 of c1178's 60 drawn
    pixels picked it, and a skeleton line took the clicks of another cell's surface
    from 14 to 25 px away at close zoom."""
    thin(c, r, ctx, BY_PICK)
    lines(c, r, ctx, BY_PICK)


SECTIONS = {
    'thin': thin, 'layers': layers, 'hidden': hidden, 'ortho': ortho, 'gesture': gesture,
    'candidates': candidates, 'cells': cells, 'lines': lines, 'compare': compare, 'markers': markers,
    'leftpick': leftpick, 'markerthird': markerthird, 'zslice': zslice,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8765', help='the dev server')
    parser.add_argument('--chrome', help="Chrome for Testing, if not in Playwright's cache")
    parser.add_argument('--cell', type=int, default=1178, help='a cell with thin processes, annotations and a skeleton')
    parser.add_argument('--second', type=int, default=21768, help='another cell of the same volume')
    parser.add_argument('--only', help='comma list of: ' + ', '.join(SECTIONS))
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    wanted = [s for s in SECTIONS if s in set((args.only or ','.join(SECTIONS)).split(','))]
    report = Report(args.verbose)
    page = Chrome(find_chrome(args.chrome), 1440, 900)
    try:
        for name in wanted:
            try:
                SECTIONS[name](page, report, args)
            except Exception as error:  # noqa: BLE001 - a script error is a failed check, the rest still run
                report.check(f'the {name} section ran to its end', False,
                             f'{type(error).__name__}: {str(error)[:400]}')
        report.start('the page')
        thrown = [e['params']['exceptionDetails'].get('text', '') for e in page.events
                  if e.get('method') == 'Runtime.exceptionThrown']
        failed = [e['params']['entry']['text'] for e in page.events
                  if e.get('method') == 'Log.entryAdded' and e['params']['entry']['level'] == 'error'
                  and 'favicon' not in e['params']['entry'].get('url', '')]
        report.check('nothing was thrown and nothing logged an error', not thrown and not failed,
                     (thrown + failed)[:3])
    finally:
        page.close()
    return report.summary()


if __name__ == '__main__':
    sys.exit(main())
