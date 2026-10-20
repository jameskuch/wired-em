"""Scripted browser check of the glue in static/js/app.js.

    .venv/bin/python tools/check_app.py                         # the dev server on :8765
    .venv/bin/python tools/check_app.py --url http://127.0.0.1:8783 --cell 943 -v
    .venv/bin/python tools/check_app.py --only waiting,undo

It drives a headless Chrome for Testing over the DevTools protocol (the client and the
report are ``tools/check_dropdowns.py``'s), in a throwaway profile.  It is outside the
pytest gate: it needs a running server that can load cells (``SBFSEM_OFFLINE=1`` and the
cells cached under ``~/.cache/sbfsem`` do: c943, c1178 and c21768 of NeitzInferiorMonkey,
c410 of RC1).  The decisions ``app.js`` makes that need no page are covered by
``tests/js/test_appcore.mjs``, which the gate does run; what needs the page itself --
the order things happen in around a slow build, what a rebuild does to a dropdown that
has the focus, what a key does -- is checked here.  Every section builds on a page of its
own, and a slow build is held back by a gate on ``api.mesh`` rather than waited for.

* ``notice`` -- the /open link's notice reads as a sentence (headline, then note).
* ``waiting`` -- the waiting points of a build that was superseded by a draw from the
  browser's cache are gone when that draw lands, and when the cell is unloaded.
* ``draws`` -- of two builds of one cell in flight, the newest is the one drawn.
* ``undo`` -- Undo of an unload draws the cell from the surfaces the tab kept: no new
  mesh request, and the time it takes.
* ``idle`` -- a load that finishes does not replace the dropdown someone has open.
* ``picker`` -- the Annotations table's cell picker is not replaced by an event that
  changes nothing, labels with repeated spaces included.
* ``save`` -- Enter in the Save dialog saves once, however many times it arrives.
* ``analysis`` -- right-click Analysis > Connectivity acts on the cell clicked.
* ``strata`` -- right-click Analysis > Stratification does too (a cell loaded after the
  chart, a hidden cell), and the tiles print counts as whole numbers.
* ``twod`` -- the Display panel's 2D box survives a reload and Restore, and Discard leaves it off.
* ``helpmovie`` -- a render option's hover help plays its movie (helpmovies.js): the shipped
  frames, the value set now marked, the measured numbers, the method's own movie, and a still
  strip with reduced motion.
* ``sheets`` -- Undo of a context sheet reaches the session stored in the browser.
* ``volume`` -- switching volume takes the first volume's boundary sheets off the scene.
* ``hold`` -- Hold in the Display panel keeps a load from reframing the scene.
* ``orbit`` -- a plain drag turns the view while cells come in: during a Restore of a session saved in
  Orthographic view (a fresh page's camera sat on its own target at the origin until the last cell was in,
  so the cells zoomed and panned but would not turn, 2026-10-20) and during a first load; the end of
  either leaves a view turned meanwhile, and an untouched Restore still ends on the saved view.
* ``keys`` -- Ctrl+Z belongs to a text box, and a held key is one step.
* ``viking`` -- Open in Viking and Viking Test: the launch tab tries the signed Viking and, when
  nothing answers, says so and offers Viking Test on a button (a click is the only way Chrome lets
  the second launch through); what each launch saw is kept; the menu asks which program once per
  session when both have answered, and "Choose Viking client…" asks on request.
* ``voxel`` -- the Voxel size typed into its box (Enter, Escape, a size it cannot read, the
  arrows), the server's estimate on the chip (asked once typing stops), a size past a big cell's
  limits built at the finest that fits it (a small cell beside it keeps its own), the notice, and
  Render compare's pane form and builds (needs --big, RPC1 c2628 by default).
* ``race`` -- a size applied while an estimate was out is not overwritten when it comes back.
* ``fine`` -- a Fine voxel the server built coarser than typed is named, with why.
* ``conflicts`` -- before Apply, a typed Fine voxel the Face cap holds coarser marks both controls
  black with red text and lists the fixes, the least Face cap first; picking one puts it in the
  controls and clears the marks, and Apply then builds it as typed; a Closed curve size past the
  server's memory marks Voxel and Join bodies, and the other way sets both; a method that reads
  neither marks nothing.
* ``compareconflicts`` -- the same marks and fixes in a Render compare pane's options panel: a pane's typed
  Fine voxel the Face cap holds coarser marks both of its controls black with red text and lists the fixes;
  picking the best (a real click) changes only that layer (the other pane, the 3D render panel and its row are
  untouched, nothing is built), and Compare then builds it as typed; a pane's Closed curve size past the
  server's memory marks Voxel and Join bodies, and the other way sets both in that layer; a method that reads
  neither marks nothing.
* ``compare`` -- Render compare's pane header: it keeps its height in every state (idle, waiting,
  building, measuring, out of date, stopped, failed) at 900, 1140, 1440 and 1920 px, 3 and 6 panes, 1 and
  4 layers, annotations on, and through a real build; Tab walks the buttons and then the chips under
  them (several layers), or the chip and then the buttons (one); the popovers of the bottom row end inside a
  window 600 px tall, and are cut short and scroll in one 360 px tall.
* ``comparekeys`` -- the keyboard in that header, with real keys: Space on the annotations dot keeps the
  keyboard on it; the layer editor stays open under its color picker (a click in it; Escape closes the
  picker only); a button that disables itself (Duplicate at four layers, Remove at one) leaves the keyboard
  in the editor, and the + and tools menus' items leave it on a visible control.
* ``comparestate`` -- failed layers, the status line and the cameras: a layer whose failure another layer's
  progress card covered has its banner back when the work ends, and Dismiss gives it to the next failed
  layer; the status line names a banner only when there is one, says what the Panes box did (new panes wait
  for Compare; fewer panes keep what they show) and does not go on saying one pane fills the grid; the orbit
  the user gave the pane in front survives putting the grid back after a Compare that ran while the other
  panes were out of sight.
* ``comparetoolbar`` -- the text under the grid (the panes-ready summary with its running clock, and the
  status line) never resizes the grid or the renders in it: the grid is one height at 480 to 1920 px
  whether the toolbar is idle, running (the clock at 3 s, 41 s, 1 min 05 s and 12 min 05 s), done, noting
  something, or failing with a long message, in both themes; the text has a row of its own under the controls
  on a window narrower than about 1650 px and sits beside them on a wider one; the controls do not move when
  the text changes; and a text that is cut short has the whole of it on hover.
* ``settings`` -- the Settings tab (settings.js) sits at the far end of the tab row; on a freshly started
  server the page is the build the server serves; an open local server offers its tools and refuses Restart
  (systemd did not start it); clearing surfaces in memory says what it cleared; "Clear cached files and
  reload" sends Clear-Site-Data and comes back to the tab with its note; a reset removes the ticked items,
  the session the page saved after the list was made included, and leaves the rest.
* ``viewcube`` -- the view cube in the corner (viewcube.js), with a real mouse: a double-click on a
  face turns the view to that face's toolbar viewpoint exactly, on an edge to the 45 degree view and on a
  corner to the view between three faces; a single click turns nothing; looking at a face, the triangles
  and curved arrows make quarter turns about one pivot, which nine of them do not move; a drag on the
  cube orbits, and one begun straight after a turn is one undo step, not two; the house is
  Reset view; the overlay row makes room for it; Flip Z relabels it; hidden, it stays hidden after a
  reload and Restore.
* ``locate`` -- Viking's "Show in SBFSEM-tools" (``/open?...&location=N``) and a Location ID typed into the
  readout took the view to the annotation and left it unmarked (2026-10-20): both select it now, drawn
  yellow, the readout naming it, and it stays selected once the cell has finished drawing; with the cell's
  annotations hidden it is drawn as a ring at its radius.
"""

# ruff: noqa: UP031
# The JavaScript snippets below are filled with `%`: their braces would all need
# doubling in an f-string or `.format`, which buries the script they carry.

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_dropdowns import Chrome, Report, cell_selects, find_chrome, open_cell_panel  # noqa: E402

# Every document starts with nothing stored, unless the section asked to keep it across a reload.
CLEAR = ("try { if (!sessionStorage.getItem('__keep')) { localStorage.clear(); sessionStorage.clear(); } }"
         " catch (e) {}")

# Installed in every page, after the app has started.  `__hold()` makes every mesh
# request wait at the gate until `__release()`; `__calls` counts the requests made.
HELPERS = r"""
(() => {
  const api = window.sbfsem.api;
  if (api.__orig) return;
  api.__orig = api.mesh;
  api.__calls = [];
  window.__hold = () => {
    let release; const promise = new Promise((resolve) => { release = resolve; });
    window.__gate = { promise, release };
  };
  window.__release = () => { const g = window.__gate; window.__gate = null; if (g) g.release(); };
  api.mesh = async (source, id, opts, extra) => {
    api.__calls.push({ id, tie: opts && opts.tie_um });
    const gate = window.__gate;
    if (gate) await gate.promise;
    const mesh = await api.__orig(source, id, opts, extra);
    return { ...mesh, __tag: opts && opts.tie_um };
  };
  // Which surface each setMesh put on screen, in order.
  window.__landed = [];
  const r = window.sbfsem.renderer;
  const setMesh = r.setMesh.bind(r);
  r.setMesh = (key, mesh, o) => { window.__landed.push([key, mesh.__tag ?? null]); return setMesh(key, mesh, o); };
})();
"""

STATE = "window.sbfsem.state"


def js(c: Chrome, expression: str, wait: bool = False):
    return c.ev(expression, wait=wait)


def row(key: str, inside: str = '') -> str:
    """A JavaScript expression for a cell's row of the list, or something inside it."""
    selector = f'.neuron-item[data-key="{key}"]' + (f' {inside}' if inside else '')
    return f'document.querySelector({json.dumps(selector)})'


def boot(c: Chrome, url: str, path: str = '/', keep: bool = False) -> None:
    """Open the app on a clean page (`keep`: with what the page stored before, as a reload has it)."""
    c.emulate('mac')           # native selects: the in-page list is for Windows and Linux
    with suppress(Exception):
        c.ev("try { sessionStorage.%s('__keep'%s); } catch (e) {}"
             % (('setItem', ", '1'") if keep else ('removeItem', '')))
    c.goto(url.rstrip('/') + path, extra_script=CLEAR)
    js(c, HELPERS)


def load(c: Chrome, cell: int, source: str | None = None, mesh: bool = True, seconds: float = 240) -> str:
    """Load a cell through the page's own form; its key, once it is listed (and drawn, if `mesh`)."""
    if source:
        js(c, """(() => { const v = document.getElementById('volume'); v.value = %s;
            v.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(source))
    before = js(c, f'{STATE}.neurons.size')
    js(c, """(() => { document.getElementById('neuron-id').value = '%d';
        document.getElementById('load-form').requestSubmit(); })()""" % cell)
    if not c.wait_for(f'{STATE}.neurons.size > %d' % before, 60):
        raise RuntimeError(f'c{cell} was not listed')
    key = js(c, f'[...{STATE}.neurons.keys()].at(-1)')
    if mesh and not c.wait_for('window.sbfsem.renderer.meshes.has(%s)' % json.dumps(key), seconds):
        raise RuntimeError(f'c{cell} has no mesh')
    c.settle(3)
    return key


def waiting_points(c: Chrome) -> list[str]:
    return js(c, "[...window.sbfsem.renderer.points.keys()].filter((k) => k.endsWith('|waiting'))")


def apply_tie(c: Chrome, tie: float, select: str) -> None:
    """Put `tie` in the panel's Tie gaps box and press Apply to selected, for the cell `select`."""
    js(c, """(() => { const s = window.sbfsem.state;
        if (!s.selection.has(%s)) %s.click();
        const box = document.getElementById('tie-um'); box.value = '%s';
        box.dispatchEvent(new Event('input', { bubbles: true }));
        box.dispatchEvent(new Event('change', { bubbles: true }));
        document.getElementById('apply-selected').click(); })()"""
       % (json.dumps(select), row(select, '.sel'), tie))


def menu_item(c: Chrome, key: str, label: str) -> bool:
    """Right-click a cell's row and press an item of its menu."""
    js(c, """(() => { const head = %s;
        const box = head.getBoundingClientRect();
        head.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
          clientX: box.left + 40, clientY: box.top + 8 })); })()""" % row(key, '.neuron-head'))
    return bool(js(c, """(() => { const item = [...document.querySelectorAll('#ctx-menu .ctx-item')]
        .find((e) => e.textContent.trim() === %s); if (!item) return false; item.click(); return true; })()"""
                   % json.dumps(label)))


# -- notice ------------------------------------------------------------------------

def notice(c: Chrome, r: Report, ctx):
    r.start('notice: the /open link\'s note')
    boot(c, ctx.url, '/open?volume=NeitzInferiorMonkey&cells=abc&technique=nonsense&location=xyz')
    shown = c.wait_for("!document.getElementById('render-notice').hidden", 10)
    r.check('the notice is shown', shown)
    text = js(c, """(() => { const t = document.getElementById('render-notice-title').textContent;
        const n = document.getElementById('render-notice-text').textContent;
        return { title: t, note: n, together: t + n }; })()""")
    r.check('the headline and the note are not run together',
            text['title'].endswith(':') and text['note'].startswith(' '), text)
    r.check('the note is the link\'s', 'nonsense' in text['together'], text)


# -- waiting points ------------------------------------------------------------------

def waiting(c: Chrome, r: Report, ctx):
    r.start('waiting: the points a slow build leaves')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    original = js(c, f'{STATE}.neurons.get({json.dumps(key)}).render.tie_um')
    other = original + 3

    def points_while_building() -> bool:
        js(c, '__hold()')
        apply_tie(c, other, key)
        return c.wait_for("window.sbfsem.renderer.points.has(%s)" % json.dumps(key + '|waiting'), 20)

    r.check('a slow build shows the cell\'s annotations as points', points_while_building())
    # The panel back to what is drawn: the cell's surface is in the browser's cache, so this
    # draw lands at once, while the build above is still held at the gate.
    apply_tie(c, original, key)
    landed = c.wait_for('window.sbfsem.renderer.meshes.has(%s)' % json.dumps(key), 10)
    r.check('the cached surface is drawn again', landed)
    time.sleep(0.5)
    left = waiting_points(c)
    r.check('and the points are gone with it', left == [], left)
    js(c, '__release()')
    time.sleep(3.0)
    left = waiting_points(c)
    r.check('and stay gone when the build they were for is given up', left == [], left)

    # A cell unloaded in the middle of such a draw leaves nothing behind either.
    r.check('a second slow build shows the points again', points_while_building())
    apply_tie(c, original, key)
    c.wait_for('window.sbfsem.renderer.meshes.has(%s)' % json.dumps(key), 10)
    js(c, f"{row(key, '.unload')}.click()")
    c.wait_for(f'{STATE}.neurons.size === 0', 10)
    c.settle(2)
    # At once, with the build the points were for still held: nothing is left to take them off later.
    left = waiting_points(c)
    r.check('an unloaded cell has no points on screen', left == [], left)
    js(c, '__release()')
    time.sleep(2.0)
    held = js(c, '[...window.sbfsem.renderer.points.keys()]')
    r.check('nor any other point layer of it', not any(k.startswith(key) for k in held), held)

    # And a cell unloaded while its first build is still waiting.
    boot(c, ctx.url)
    js(c, '__hold()')
    js(c, """(() => { document.getElementById('neuron-id').value = '%d';
        document.getElementById('load-form').requestSubmit(); })()""" % ctx.cell)
    c.wait_for(f'{STATE}.neurons.size === 1', 60)
    key = js(c, f'[...{STATE}.neurons.keys()][0]')
    c.wait_for("window.sbfsem.renderer.points.has(%s)" % json.dumps(key + '|waiting'), 20)
    js(c, f"{row(key, '.unload')}.click()")
    c.wait_for(f'{STATE}.neurons.size === 0', 10)
    c.settle(2)
    left = waiting_points(c)
    r.check('a cell unloaded during its first build leaves no points', left == [], left)
    js(c, '__release()')
    time.sleep(2.0)
    left = waiting_points(c)
    r.check('and none appear when its build is given up', left == [], left)


# -- draws -----------------------------------------------------------------------------

def draws(c: Chrome, r: Report, ctx):
    r.start('draws: the newest build is the one drawn')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    base = js(c, f'{STATE}.neurons.get({json.dumps(key)}).render.tie_um')
    first, second = base + 2, base + 4
    js(c, '__hold(); window.__landed.length = 0')
    apply_tie(c, first, key)
    time.sleep(0.5)
    apply_tie(c, second, key)
    time.sleep(0.5)
    js(c, '__release()')
    c.wait_for('window.__landed.length > 0', 240)
    time.sleep(2.0)
    tags = [tag for _, tag in js(c, 'window.__landed')]
    r.check('the surface drawn is the second one', tags and tags[-1] == second, tags)
    r.check('the first build never lands', first not in tags, tags)

    # A cell unloaded while its build runs is not drawn when the build ends.
    js(c, '__hold(); window.__landed.length = 0')
    apply_tie(c, first, key)
    time.sleep(0.5)
    js(c, f"{row(key, '.unload')}.click()")
    c.wait_for(f'{STATE}.neurons.size === 0', 10)
    js(c, '__release()')
    time.sleep(3.0)
    r.check('an unloaded cell is not drawn by the build it left running',
            not js(c, 'window.sbfsem.renderer.meshes.size') and not js(c, 'window.__landed.length'))


# -- undo ---------------------------------------------------------------------------------

def undo(c: Chrome, r: Report, ctx):
    r.start('undo: an unload comes back from the surfaces the tab kept')
    boot(c, ctx.url)
    first = load(c, ctx.cell)
    load(c, ctx.second)
    calls = js(c, 'window.sbfsem.api.__calls.length')
    weight = js(c, "window.sbfsem.cacheInfo().meshes")
    r.check('the tab holds the surfaces it drew', weight >= 2, weight)

    def back(open_undo: str, wanted: list[str]) -> float | None:
        js(c, open_undo)
        c.settle(2)
        return js(c, """(async () => { const t0 = performance.now();
            const button = document.querySelector('.undo-btn') || document.getElementById('undo-btn');
            button.click();
            const want = %s;
            for (let i = 0; i < 6000; i += 1) {
              if (want.every((k) => window.sbfsem.renderer.meshes.has(k))) return performance.now() - t0;
              await new Promise((resolve) => setTimeout(resolve, 5));
            }
            return null; })()""" % json.dumps(wanted), wait=True)

    one = back(f"{row(first, '.unload')}.click()",
               [first])
    r.check('Undo of a ✕ draws the cell again', one is not None)
    made = js(c, 'window.sbfsem.api.__calls.length') - calls
    r.check('without asking the server for its surface again', made == 0, f'{made} mesh requests, {one} ms')
    print(f'     Undo of one cell: {one:.0f} ms' if one is not None else '     Undo of one cell: not back')

    # Unload all, then the Undo button (the history step).
    js(c, "document.getElementById('clear-all').click()")
    c.wait_for(f'{STATE}.neurons.size === 0', 20)
    c.settle(2)
    t0 = time.time()
    js(c, "document.getElementById('undo-btn').click()")
    both = c.wait_for(f'{STATE}.neurons.size === 2 && window.sbfsem.renderer.meshes.size === 2', 120)
    elapsed = time.time() - t0
    r.check('Unload all, then Undo, brings both cells back', both)
    made = js(c, 'window.sbfsem.api.__calls.length') - calls
    r.check('with no surface asked for again', made == 0, f'{made} mesh requests, {elapsed:.2f} s')
    print(f'     Undo of Unload all (2 cells): {elapsed * 1000:.0f} ms')

    # Rebuild all renders still empties the tab's surfaces.
    js(c, "document.getElementById('rebuild').click()")
    c.wait_for('window.sbfsem.api.__calls.length > %d' % calls, 60)
    r.check('Rebuild all renders asks for every surface again',
            js(c, 'window.sbfsem.api.__calls.length') - calls >= 1)


# -- idle ---------------------------------------------------------------------------------

def idle(c: Chrome, r: Report, ctx):
    r.start('idle: a load that finishes leaves a dropdown alone')
    boot(c, ctx.url)
    load(c, ctx.cell)
    if not open_cell_panel(c) or not cell_selects(c):
        r.skip('the cell panel has no dropdown', 'this cell offers none')
        return
    js(c, """(() => { const s = document.querySelector('.cell-panel select');
        window.__select = s; s.focus(); })()""")
    stands = "document.contains(window.__select) && document.activeElement === window.__select"
    r.check('a dropdown of the cell panel has the focus', js(c, stands))
    js(c, '__hold()')
    js(c, """(() => { document.getElementById('neuron-id').value = '%d';
        document.getElementById('load-form').requestSubmit(); })()""" % ctx.second)
    c.wait_for(f'{STATE}.neurons.size === 2', 60)
    time.sleep(1.5)
    r.check('while the second cell builds, the list has not been rebuilt under it', js(c, stands))
    js(c, '__release()')
    ok = c.wait_for(f'{STATE}.neurons.size === 2 && window.sbfsem.renderer.meshes.size === 2', 240)
    r.check('the second cell is drawn', ok)
    time.sleep(1.0)
    r.check('when the load finishes, the dropdown is still there, and still has the focus', js(c, stands))
    js(c, 'window.__select.blur()')
    shown = c.wait_for("document.querySelectorAll('.neuron-item').length === 2", 10)
    r.check('and the list catches up once the focus leaves it', shown)


# -- picker ---------------------------------------------------------------------------------

def picker(c: Chrome, r: Report, ctx):
    r.start('picker: the Annotations table\'s cell picker')
    boot(c, ctx.url)
    key = load(c, ctx.cell, mesh=False)
    js(c, f"{STATE}.neurons.get({json.dumps(key)}).summary.label = 'ON  parasol'")
    js(c, "document.getElementById('select-none').click(); document.getElementById('select-all').click()")
    label = js(c, "document.querySelector('#table-neuron option').textContent")
    r.check('the label has its two spaces', label.endswith('ON  parasol'), label)
    js(c, "window.__option = document.querySelector('#table-neuron option')")
    for _ in range(3):
        js(c, "document.getElementById('select-none').click(); document.getElementById('select-all').click()")
    r.check('events that change nothing leave the picker\'s options in place',
            js(c, "document.querySelector('#table-neuron option') === window.__option"))
    # The Render compare tab's cell picker is kept the same way.
    js(c, "window.__option = document.querySelector('#compare-cell option')")
    for _ in range(3):
        js(c, "document.getElementById('select-none').click(); document.getElementById('select-all').click()")
    r.check('and so is the Render compare tab\'s',
            js(c, "!!window.__option && document.querySelector('#compare-cell option') === window.__option"))


# -- save ----------------------------------------------------------------------------------

def save(c: Chrome, r: Report, ctx):
    r.start('save: Enter saves once')
    boot(c, ctx.url)
    load(c, ctx.cell, mesh=False)
    folder = f'check-app-{int(time.time())}'
    js(c, """(() => { window.__puts = 0; const real = window.fetch;
        window.fetch = async (url, init) => {
          if (init && init.method === 'PUT' && String(url).includes('/api/saves/')) {
            window.__puts += 1; await new Promise((resolve) => setTimeout(resolve, 500));
          }
          return real(url, init); }; })()""")
    js(c, "document.getElementById('save-store').click()")
    if not c.wait_for("document.getElementById('save-dialog').open", 10):
        r.check('the Save dialog opens', False)
        return
    try:
        js(c, """(() => { document.getElementById('save-new-folder').value = %s;
            document.getElementById('save-create-folder').click(); })()""" % json.dumps(folder))
        c.wait_for("!!document.querySelector('#save-folder-list li.active, #save-folder-list li.selected')", 10)
        time.sleep(0.5)
        js(c, "document.getElementById('save-name').value = 'one'")
        # A press, its key repeat, and another press while the first is still being written.
        js(c, """(() => { const box = document.getElementById('save-name');
            for (const repeat of [false, true, false]) {
              box.dispatchEvent(new KeyboardEvent('keydown',
                { key: 'Enter', bubbles: true, cancelable: true, repeat }));
            } })()""")
        closed = c.wait_for("!document.getElementById('save-dialog').open", 15)
        r.check('the dialog closes when the document is written', closed)
        puts = js(c, 'window.__puts')
        r.check('and the document was written once', puts == 1, f'{puts} writes')
        error = js(c, "document.getElementById('save-error').hidden")
        r.check('with no error left in the dialog', error is True)
        # The same through two clicks on the button.
        js(c, "document.getElementById('save-store').click()")
        c.wait_for("document.getElementById('save-dialog').open", 10)
        time.sleep(0.8)
        js(c, "document.getElementById('save-name').value = 'two'; window.__puts = 0")
        js(c, "const b = document.getElementById('save-confirm'); b.click(); b.click(); b.click();")
        c.wait_for("!document.getElementById('save-dialog').open", 15)
        puts = js(c, 'window.__puts')
        r.check('three clicks on Save in a row write it once', puts == 1, f'{puts} writes')
    finally:
        base = ctx.url.rstrip('/') + '/api/saves/' + folder
        for name in ('one', 'two'):
            _delete(f'{base}/{name}')
        _delete(base)


def _delete(url: str) -> None:
    # Tidying up: the check has said what it found.
    with suppress(Exception):
        urllib.request.urlopen(urllib.request.Request(url, method='DELETE'), timeout=10).read()


# -- analysis ---------------------------------------------------------------------------------

def analysis(c: Chrome, r: Report, ctx):
    r.start('analysis: right-click Analysis acts on the cell clicked')
    boot(c, ctx.url)
    js(c, '__hold()')
    home = load(c, ctx.cell, mesh=False)
    other = load(c, ctx.other, source=ctx.other_source, mesh=False)
    r.check('cells of two volumes are listed', js(c, f'{STATE}.neurons.size') == 2)

    def view() -> str:
        return js(c, "document.querySelector('.tab.active').dataset.view")

    def go(tab: str):
        js(c, "document.querySelector('.tab[data-view=\"%s\"]').click()" % tab)
        c.settle(2)

    go('connectivity')
    js(c, """(() => { const s = document.getElementById('cx-source'); s.value = %s;
        s.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(ctx.source))
    go('render')
    found = menu_item(c, other, 'Connectivity (Graffinity)')
    r.check('the menu has Analysis > Connectivity', found)
    c.settle(2)
    r.check('the Connectivity tab opens', view() == 'connectivity', view())
    got = js(c, "document.getElementById('cx-source').value")
    r.check('on the volume of the cell clicked, not the one it showed', got == ctx.other_source, got)
    note = js(c, "document.getElementById('cx-anchor-note').textContent")
    r.check('and its anchor note names that cell', f'c{ctx.other}' in note, note)
    go('render')
    menu_item(c, home, 'Connectivity (Graffinity)')
    c.settle(2)
    got = js(c, "document.getElementById('cx-source').value")
    r.check('a right-click on a cell of the first volume goes back to it', got == ctx.source, got)
    js(c, '__release()')


def strata(c: Chrome, r: Report, ctx):
    r.start('strata: the Stratification tab and its tiles')
    boot(c, ctx.url)
    js(c, '__hold()')
    home = load(c, ctx.cell, mesh=False)
    js(c, "document.querySelector('.tab[data-view=\"strata\"]').click()")
    c.wait_for("document.querySelectorAll('#strata-stats dd').length > 0", 60)
    tiles = js(c, """[...document.querySelectorAll('#strata-stats dl')].map((d) =>
        [d.querySelector('dt').textContent, d.querySelector('dd').textContent])""")
    counts = [t for t in tiles if t[0].split(' ', 1)[1] in ('n', 'outliers', 'outside surfaces')]
    r.check('the tab drew a tile each for n, outliers and outside surfaces', len(counts) == 3, tiles)
    whole = all(v.replace(',', '').isdigit() for _, v in counts)
    r.check('and they are whole numbers', whole, counts)
    del home
    other = load(c, ctx.second, mesh=False)
    js(c, "document.querySelector('.tab[data-view=\"render\"]').click()")
    c.settle(2)
    menu_item(c, other, 'Stratification')
    c.wait_for("document.getElementById('strata-stats').textContent.includes('c%d')" % ctx.second, 60)
    names = js(c, "document.getElementById('strata-stats').textContent")
    r.check('right-click Stratification on a cell loaded after the chart shows that cell',
            f'c{ctx.second}' in names, names[:200])
    # A hidden cell is charted when it is the one asked for.
    js(c, "document.querySelector('.tab[data-view=\"render\"]').click()")
    c.settle(2)
    third = load(c, ctx.third, mesh=False)
    js(c, f"{row(third, '.vis')}.click()")
    r.check('a cell can be hidden', js(c, f'{STATE}.neurons.get({json.dumps(third)}).visible') is False)
    menu_item(c, third, 'Stratification')
    c.wait_for("document.getElementById('strata-stats').textContent.includes('c%d')" % ctx.third, 120)
    r.check('even when it is hidden',
            f'c{ctx.third}' in js(c, "document.getElementById('strata-stats').textContent"))
    # Opening the tab by hand charts the shown cells again, as the tab always did.
    js(c, "document.querySelector('.tab[data-view=\"render\"]').click()")
    c.settle(2)
    js(c, "document.querySelector('.tab[data-view=\"strata\"]').click()")
    c.wait_for("!document.getElementById('strata-stats').textContent.includes('c%d')" % ctx.third, 60)
    r.check('and a plain visit leaves the hidden cell out again',
            f'c{ctx.third}' not in js(c, "document.getElementById('strata-stats').textContent"))
    js(c, '__release()')


# -- twod -------------------------------------------------------------------------------------

def twod(c: Chrome, r: Report, ctx):
    r.start('twod: the 2D box across a reload')

    def state() -> dict:
        return js(c, """(() => { const cam = window.sbfsem.renderer.camera;
            return { box: document.getElementById('opt-2d').checked, ortho: !!cam.isOrthographicCamera,
                     z: +Math.abs(cam.position.z - window.sbfsem.renderer.controls.target.z).toFixed(2) }; })()""")

    boot(c, ctx.url)
    load(c, ctx.cell)
    js(c, "document.getElementById('toggle-display').click(); document.getElementById('opt-2d').click()")
    c.settle(3)
    saved = state()
    r.check('ticking 2D turns the view: orthographic, along Z', saved['box'] and saved['ortho'], saved)
    time.sleep(1.0)

    # A reload offers the saved cells; the box follows the view that is there until Restore.
    boot(c, ctx.url, keep=True)
    c.wait_for("!document.getElementById('restore-banner').hidden", 30)
    shown = state()
    r.check('before Restore the box describes the view that is there, not the saved one',
            not shown['box'], shown)
    js(c, "document.getElementById('restore-yes').click()")
    c.wait_for('window.sbfsem.renderer.meshes.size === 1', 240)
    c.settle(3)
    after = state()
    r.check('after Restore the view is the saved 2D one', after['ortho'] and after['z'] > 0, after)
    r.check('and the box is ticked, as it was saved', after['box'], after)

    # Discard leaves the default view and an unticked box.
    boot(c, ctx.url, keep=True)
    c.wait_for("!document.getElementById('restore-banner').hidden", 30)
    js(c, "document.getElementById('restore-no').click()")
    c.settle(2)
    discarded = state()
    r.check('Discard leaves the box unticked: the saved camera is not coming back',
            not discarded['box'], discarded)


# -- sheets -----------------------------------------------------------------------------------

def sheets(c: Chrome, r: Report, ctx):
    r.start('sheets: Undo of a context sheet reaches the stored session')
    boot(c, ctx.url)
    load(c, ctx.cell)
    js(c, "document.getElementById('toggle-display').click()")
    js(c, """(() => { const b = document.getElementById('ctx-gcl'); b.checked = true;
        b.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    if not c.wait_for('window.sbfsem.renderer.context && window.sbfsem.renderer.context.size > 0', 60):
        r.skip('the GCL sheet', 'this volume has no GCL boundary to load')
        return
    time.sleep(1.0)            # past the history's merge window: the tick is a step of its own
    stored = ("(() => { try { return JSON.parse(localStorage.getItem('sbfsem.session.v1'))"
              ".ui.display.context.gcl; } catch (e) { return String(e); } })()")
    # (Ticking a sheet does not save the session by itself; Undo does, as its last step.)
    js(c, "document.getElementById('undo-btn').click()")
    time.sleep(0.6)
    r.check('Undo unticks the box', js(c, "document.getElementById('ctx-gcl').checked") is False)
    r.check('and hides the sheet', js(c, "[...window.sbfsem.renderer.context.values()].every((o) => !o.visible)"))
    r.check('and the stored session no longer has it', js(c, stored) is False, js(c, stored))


# -- volume -----------------------------------------------------------------------------------

def volume(c: Chrome, r: Report, ctx):
    r.start('volume: leaving a volume leaves its sheets behind')
    boot(c, ctx.url)
    js(c, "document.getElementById('toggle-display').click()")
    js(c, """(() => { const b = document.getElementById('ctx-inl'); b.checked = true;
        b.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    if not c.wait_for('window.sbfsem.renderer.context && window.sbfsem.renderer.context.size > 0', 60):
        r.skip('the INL sheet', 'this volume has no INL boundary to load')
        return
    r.check('the INL sheet of the first volume is on screen', True)
    js(c, """(() => { const v = document.getElementById('volume'); v.value = %s;
        v.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(ctx.other_source))
    c.settle(3)
    r.check('switching volume takes the sheet off the scene',
            js(c, 'window.sbfsem.renderer.context.size') == 0, js(c, 'window.sbfsem.renderer.context.size'))
    r.check('and unticks its box', js(c, "document.getElementById('ctx-inl').checked") is False)


# -- hold -------------------------------------------------------------------------------------

def hold(c: Chrome, r: Report, ctx):
    r.start('hold: a load leaves a framing the user chose')

    def camera() -> list[float]:
        return js(c, 'window.sbfsem.renderer.camera.position.toArray()')

    def moved(a, b) -> bool:
        return max(abs(x - y) for x, y in zip(a, b, strict=True)) > 1e-3

    # Without Hold, the first cell loaded frames the scene.
    boot(c, ctx.url)
    before = camera()
    load(c, ctx.cell)
    r.check('a first load frames the scene', moved(before, camera()), (before, camera()))
    # With Hold ticked first, it does not: neither at the end of the load nor when the
    # annotation points of a slow build are first framed.  (loadCells once read the
    # settings template where it meant the Display panel, so Hold was never seen.)
    boot(c, ctx.url)
    js(c, """(() => { document.getElementById('toggle-display').click();
        const box = document.getElementById('lim-hold'); box.checked = true;
        box.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    c.settle(2)
    before = camera()
    js(c, '__hold()')
    js(c, """(() => { document.getElementById('neuron-id').value = '%d';
        document.getElementById('load-form').requestSubmit(); })()""" % ctx.cell)
    c.wait_for(f'{STATE}.neurons.size === 1', 60)
    c.wait_for("[...window.sbfsem.renderer.points.keys()].some((k) => k.endsWith('|waiting'))", 20)
    r.check('the points of a slow build do not move a held camera', not moved(before, camera()),
            (before, camera()))
    js(c, '__release()')
    c.wait_for('window.sbfsem.renderer.meshes.size === 1', 240)
    c.settle(3)
    r.check('and neither does the cell\'s arrival', not moved(before, camera()), (before, camera()))


# -- orbit ------------------------------------------------------------------------------------

def orbit(c: Chrome, r: Report, ctx):
    r.start('orbit: a drag turns the view while cells come in')
    view = 'window.sbfsem.renderer.cameraState()'
    gate_shut = "[...window.sbfsem.renderer.points.keys()].some((k) => k.endsWith('|waiting'))"

    def direction() -> list[float]:
        return js(c, 'window.sbfsem.renderer.camera.getWorldDirection('
                     'window.sbfsem.renderer.camera.position.clone()).toArray()')

    def drag() -> tuple[float, float]:
        """A plain left-drag of 120 px across the viewport, with a real mouse: (degrees turned, expected)."""
        box = js(c, "(() => { const b = window.sbfsem.renderer.webgl.domElement.getBoundingClientRect();"
                    " return [b.left, b.top, b.width, b.height]; })()")
        x, y = box[0] + box[2] * 0.35, box[1] + box[3] * 0.55
        before = direction()
        c.press(x, y)
        for i in range(1, 61):
            c.move(x + i * 2, y, buttons=1)
            time.sleep(0.02)
        c.release(x + 120, y)
        time.sleep(1.2)                       # the glide
        after = direction()
        dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(before, after, strict=True))))
        return math.degrees(math.acos(dot)), 120 / box[3] * 360

    def apart(a, b) -> float:
        """How far apart two camera states are, in µm of position or target (inf for one aimed at nothing)."""
        if not a or not b:
            return 0.0 if not a and not b else float('inf')
        return max(abs(x - y) for x, y in zip(a['position'] + a['target'], b['position'] + b['target'], strict=True))

    # A session in Orthographic view, as the user's workspace of 2026-10-20 was.  A fresh page's
    # camera sits on its own target at the origin; the restore put the saved view back only after
    # the last cell, and until then the cells showed, zoomed and panned, but a drag turned nothing.
    boot(c, ctx.url)
    load(c, ctx.cell)
    js(c, "document.getElementById('ortho').click()")
    c.settle(2)
    drag()                                     # a view of the user's own, saved with the session
    time.sleep(1.0)
    saved = js(c, view)
    r.check('the session is saved in Orthographic view', saved and saved['orthographic'], saved)
    stored = "(() => { try { return JSON.parse(localStorage.getItem('sbfsem.session.v1')).camera; } catch (e) { return null; } })()"
    for touch in (True, False):
        boot(c, ctx.url, keep=True)
        c.wait_for("!document.getElementById('restore-banner').hidden", 30)
        saved = js(c, stored)             # a view turned during the first restore is saved with it
        js(c, '__hold()')
        js(c, "document.getElementById('restore-yes').click()")
        c.wait_for(gate_shut, 30)
        c.settle(3)
        during = js(c, view)
        r.check(f"{'' if touch else 'again: '}while the restore waits on its build, the saved view is up",
                apart(during, saved) < 1e-3, (during, saved))
        if touch:
            turned, expected = drag()
            r.check('a plain drag turns it', abs(turned - expected) < 3,
                    f'{turned:.1f} degrees of {expected:.0f}')
            mine = js(c, view)
        js(c, '__release()')
        c.wait_for('window.sbfsem.renderer.meshes.size === 1', 240)
        c.settle(3)
        end = js(c, view)
        if touch:
            r.check('and the restore\'s end leaves the turned view',
                    apart(end, mine) < 0.5 and apart(end, saved) > 1, f'{apart(end, mine):.3f} µm from it')
        else:
            r.check('untouched, the restore ends on the saved view', apart(end, saved) < 1e-3, (end, saved))

    # A first load: the waiting points frame the view early, a drag turns it, and the load's
    # framing at its end does not throw the turn away.
    boot(c, ctx.url)
    js(c, '__hold()')
    js(c, """(() => { document.getElementById('neuron-id').value = '%d';
        document.getElementById('load-form').requestSubmit(); })()""" % ctx.cell)
    c.wait_for(gate_shut, 30)
    c.settle(3)
    turned, expected = drag()
    r.check('a first load: a drag turns the view while the build waits', abs(turned - expected) < 3,
            f'{turned:.1f} degrees of {expected:.0f}')
    mine = js(c, view)
    js(c, '__release()')
    c.wait_for('window.sbfsem.renderer.meshes.size === 1', 240)
    c.settle(3)
    end = js(c, view)
    r.check('and the load\'s end leaves the turned view', apart(end, mine) < 0.5, f'{apart(end, mine):.3f} µm from it')


# -- keys -------------------------------------------------------------------------------------

def keys(c: Chrome, r: Report, ctx):
    r.start('keys: Ctrl+Z')
    boot(c, ctx.url)
    load(c, ctx.cell)
    time.sleep(1.0)
    steps = js(c, 'window.sbfsem.history().undo.length')
    r.check('the history has steps to undo', steps >= 2, steps)

    def press(focus: str | None, repeat: bool) -> None:
        # The app asks what has the focus, not where the event was sent.
        js(c, "(%s)" % (f"document.querySelector({json.dumps(focus)}).focus()" if focus
                        else "document.activeElement && document.activeElement.blur()"))
        js(c, """(document.activeElement || document.body).dispatchEvent(new KeyboardEvent('keydown',
            { key: 'z', ctrlKey: true, bubbles: true, cancelable: true, repeat: %s }))"""
           % ('true' if repeat else 'false'))
        time.sleep(0.6)

    press('#neuron-id', False)
    r.check('in a text box Ctrl+Z is the box\'s', js(c, 'window.sbfsem.history().undo.length') == steps)
    press(None, True)
    r.check('a held key is not a step', js(c, 'window.sbfsem.history().undo.length') == steps)
    press(None, False)
    c.wait_for('window.sbfsem.history().undo.length < %d' % steps, 30)
    r.check('and a press is one', js(c, 'window.sbfsem.history().undo.length') == steps - 1,
            js(c, 'window.sbfsem.history().undo.length'))


# -- viking -----------------------------------------------------------------------------

# The launch tab waits 6 s for the focus to leave before it says nothing answered; the check
# shortens every timer of 5 s or more so a launch page can be walked through in a second.
FAST_WAIT = ("(() => { const st = window.setTimeout; window.setTimeout = (f, ms, ...a) =>"
             " st(f, ms >= 5000 ? ms / 20 : ms, ...a); })();")

# The app's window.open, replaced by a recorder: the launch tab is the page under test in the first
# half and a popup the check cannot see in the second.
SPY_OPEN = r"""
(() => {
  window.__opened = [];
  window.open = (url, name) => {
    const entry = { url, name, href: null };
    window.__opened.push(entry);
    return { location: { set href(v) { entry.href = v; } }, close() { entry.closed = true; } };
  };
})();
"""

# Holds each answer of /api/health until the check opens its gate (`window.__gates[i].open()`), so a
# request can be kept in flight across a click: the busy tracker's "ready" at its end is what wiped
# a flared status message (the freshness check a load starts, 2.12.0 and before).
GATE_HEALTH = r"""
(() => {
  if (window.__gates) return;
  window.__gates = [];
  const plain = window.fetch;
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!url.startsWith('/api/health')) return plain(input, init);
    let open;
    const gate = new Promise((resolve) => { open = resolve; });
    const entry = { open, done: false };
    window.__gates.push(entry);
    return plain(input, init).then(async (response) => { await gate; entry.done = true; return response; });
  };
})();
"""
HEALTH = "void window.sbfsem.api.health().catch(() => {})"
STATUS = "document.getElementById('status').textContent"

MEMORY = 'sbfsem.vikingClients'
CHOICE = 'sbfsem.vikingChoice'


def quiet(c: Chrome, expression: str):
    """Evaluate without the gesture `Chrome.ev` claims: a page that has been given one launches
    what Chrome would otherwise refuse, and the launch tab's first launch would be a question
    headless Chrome on macOS cannot show, ending all input to the page."""
    done = c.send('Runtime.evaluate', expression=expression, returnByValue=True, userGesture=False)
    if 'exceptionDetails' in done:
        raise RuntimeError(json.dumps(done['exceptionDetails'])[:600])
    return done['result'].get('value')


def quiet_wait(c: Chrome, expression: str, seconds: float = 10.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if quiet(c, expression):
            return True
        time.sleep(0.1)
    return False


def launches(c: Chrome, prefix: str = 'viking') -> list[str]:
    """The external-protocol links the page asked the browser to open, in order."""
    quiet(c, '0')           # events are read while a command waits for its answer
    return [e['params']['url'] for e in c.events
            if e.get('method') == 'Page.frameRequestedNavigation' and e['params']['url'].startswith(prefix)]


def refusals(c: Chrome) -> list[str]:
    quiet(c, '0')
    return [e['params']['entry'].get('text', '') for e in c.events
            if e.get('method') == 'Log.entryAdded' and 'Not allowed to launch' in e['params']['entry'].get('text', '')]


def memory_of(c: Chrome) -> dict:
    return json.loads(quiet(c, "localStorage.getItem(%s) || '{}'" % json.dumps(MEMORY)))


def head_of(c: Chrome) -> str:
    return quiet(c, "document.getElementById('head') ? document.getElementById('head').textContent : ''")


def visible(c: Chrome, ident: str) -> bool:
    return bool(quiet(c, "(() => { const e = document.getElementById(%s); return !!e && !e.classList.contains('hide')"
                         " && e.getBoundingClientRect().height > 0; })()" % json.dumps(ident)))


def launch_page(c: Chrome, ctx, query: str) -> None:
    """Open the launch tab's page in this tab, as Windows, with its waits shortened.

    The page is reached by a script with no gesture, which is how Chrome then refuses the
    launch the page makes as it loads ("a user gesture is required"): a launch it allowed
    would leave headless Chrome on macOS with a question nobody can answer, and a page that
    takes no input.  The page cannot tell: no program takes the focus either way.
    """
    c.emulate('windows')
    c.send('Emulation.setFocusEmulationEnabled', enabled=True)     # a headless page has no focus to lose
    base = ctx.url.rstrip('/')
    c.send('Page.navigate', url=f'{base}/api/build')
    quiet_wait(c, "document.readyState === 'complete'", 20)
    ident = c.send('Page.addScriptToEvaluateOnNewDocument', source=CLEAR + FAST_WAIT)['identifier']
    c.events.clear()
    target = f'{base}/open-in-viking?volume={ctx.source}&location=769111&page=1&{query}'
    quiet(c, f'location.href = {json.dumps(target)}')
    if not quiet_wait(c, "document.readyState === 'complete' && !!document.getElementById('head')", 20):
        raise RuntimeError('the launch page did not load')
    c.send('Page.removeScriptToEvaluateOnNewDocument', identifier=ident)


def viking(c: Chrome, r: Report, ctx):
    r.start('viking: the launch tab, and Viking Test')
    # -- the launch tab ----------------------------------------------------------------------
    launch_page(c, ctx, 'client=viking&then=test')
    r.check('the signed link is launched as the page loads', bool(launches(c)), launches(c))
    quiet_wait(c, "document.getElementById('head').textContent.includes('not installed')", 15)
    r.check('nothing answered it: the same message as ever', head_of(c) == 'Viking client is not installed.', head_of(c))
    r.check('Viking Test is offered, not started: only viking:// was launched',
            [u.split('://')[0] for u in launches(c)] == ['viking'], launches(c))
    r.check('the offer is a button that names it', visible(c, 'other') and
            js(c, "document.getElementById('tryOther').textContent") == 'Open in Viking Test')
    r.check('the installer link is still the signed Viking\'s', visible(c, 'install') and
            'Viking-win-Setup.exe' in js(c, "document.getElementById('installLink').href"))
    memory = memory_of(c)
    r.check('the signed Viking is remembered as not answering',
            memory.get('absent', {}).get('viking', 0) > 0 and memory.get('seen', {}).get('viking') == 0, memory)

    box = quiet(c, "(() => { const b = document.getElementById('tryOther').getBoundingClientRect();"
                   " return [b.left + b.width / 2, b.top + b.height / 2]; })()")
    # The rule this design stands on, in this Chrome: a script that starts a launch seconds after
    # its page loaded is refused, and a click is not.
    quiet(c, "location.replace('viking-test://by-a-timer')")
    time.sleep(0.2)
    r.check('a launch a script starts with no gesture is refused ("a user gesture is required")',
            len(refusals(c)) == 1 and 'by-a-timer' in refusals(c)[0], refusals(c))
    c.press(box[0], box[1])                                         # a real click: a gesture
    c.release(box[0], box[1])
    time.sleep(0.4)
    r.check('a click on the button launches viking-test:// and Chrome lets it through',
            any(u.startswith('viking-test://open?volumeName=') for u in launches(c))
            and len(refusals(c)) == 1, (launches(c), refusals(c)))
    quiet(c, "window.dispatchEvent(new Event('blur'))")             # the program takes the focus
    r.check('and when it answers, the page says it was handed to Viking Test',
            head_of(c) == 'Handed to Viking Test.', head_of(c))
    memory = memory_of(c)
    r.check('Viking Test is remembered as the one that answers',
            memory.get('seen', {}).get('test', 0) > 0 and memory.get('last') == 'test'
            and memory.get('absent', {}).get('test') == 0 and memory.get('absent', {}).get('viking', 0) > 0, memory)

    launch_page(c, ctx, 'client=test')
    r.check('a link for Viking Test is viking-test://, launched as the page loads',
            [u.split('://')[0] for u in launches(c)] == ['viking-test'], launches(c))
    quiet(c, "window.dispatchEvent(new Event('blur'))")
    r.check('handed over: no offer of another program', head_of(c) == 'Handed to Viking Test.'
            and not visible(c, 'other'), head_of(c))
    launch_page(c, ctx, 'client=viking')
    quiet_wait(c, "document.getElementById('head').textContent.includes('not installed')", 15)
    r.check('with no other program to offer, the message stands alone',
            head_of(c) == 'Viking client is not installed.' and not visible(c, 'other'), head_of(c))
    c.send('Page.navigate', url=f"{ctx.url.rstrip('/')}/open-in-viking?volume={ctx.source}&page=1&client=beta")
    r.check('a program it does not know is refused in words',
            quiet_wait(c, "document.body && document.body.innerText.includes('Unknown Viking client')", 10))

    # -- the menu ----------------------------------------------------------------------------
    c.emulate('windows')
    c.goto(ctx.url, extra_script=CLEAR + SPY_OPEN)
    key = load(c, ctx.cell)
    js(c, HELPERS)

    def menu_labels() -> list[str]:
        js(c, """(() => { const head = %s; const box = head.getBoundingClientRect();
            head.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
              clientX: box.left + 40, clientY: box.top + 8 })); })()""" % row(key, '.neuron-head'))
        return js(c, "[...document.querySelectorAll('#ctx-menu .ctx-item')].map((e) => e.textContent.trim())")

    def close_menu() -> None:
        js(c, "document.getElementById('ctx-menu') && document.getElementById('ctx-menu').remove()")

    def hrefs() -> list[str]:
        return js(c, 'window.__opened.map((o) => o.href)')

    def store(local: dict | None = None, session: str | None = None) -> None:
        js(c, "(() => { localStorage.removeItem(%s); sessionStorage.removeItem(%s); %s %s window.__opened.length = 0; })()"
           % (json.dumps(MEMORY), json.dumps(CHOICE),
              "localStorage.setItem(%s, %s);" % (json.dumps(MEMORY), json.dumps(json.dumps(local))) if local else '',
              "sessionStorage.setItem(%s, %s);" % (json.dumps(CHOICE), json.dumps(session)) if session else ''))

    def seen(*who: str) -> dict:
        now = int(time.time() * 1000)
        return {'seen': {'viking': now if 'viking' in who else 0, 'test': now if 'test' in who else 0},
                'absent': {'viking': 0, 'test': 0}, 'last': who[-1] if who else None}

    def clicked_open_in_viking() -> list[str]:
        menu_item(c, key, 'Open in Viking')
        c.wait_for('window.__opened.length > 0 && window.__opened.at(-1).href', 15) or time.sleep(0.5)
        return hrefs()

    labels = menu_labels()
    r.check('the menu has Open in Viking and, on Windows, Choose Viking client…',
            'Open in Viking' in labels and 'Choose Viking client…' in labels, labels)
    close_menu()

    store()
    got = clicked_open_in_viking()
    r.check('nothing known: the signed Viking first, Viking Test behind it',
            len(got) == 1 and 'client=viking' in got[0] and 'then=test' in got[0], got)

    store(seen('test'))
    got = clicked_open_in_viking()
    r.check('only Viking Test has answered: it is used without asking',
            len(got) == 1 and 'client=test' in got[0] and 'then=viking' in got[0]
            and js(c, 'document.getElementById("ctx-menu") === null'), got)

    store(seen('viking', 'test'))
    menu_item(c, key, 'Open in Viking')
    time.sleep(0.4)
    title = js(c, "(() => { const t = document.querySelector('#ctx-menu .ctx-title'); return t ? t.textContent : null; })()")
    rows = js(c, "[...document.querySelectorAll('#ctx-menu .ctx-item')].map((e) => e.textContent.trim())")
    r.check('both have answered: the first open of the session asks which',
            title == 'Open in which Viking?' and rows == ['Viking', 'Viking Test'] and hrefs() == [], (title, rows, hrefs()))
    js(c, "[...document.querySelectorAll('#ctx-menu .ctx-item')].find((e) => e.textContent.trim() === 'Viking Test').click()")
    c.wait_for('window.__opened.length > 0 && window.__opened[0].href', 15)
    got = hrefs()
    r.check('the answer is used and kept for the session',
            len(got) == 1 and 'client=test' in got[0] and js(c, "sessionStorage.getItem(%s)" % json.dumps(CHOICE)) == 'test', got)
    got = clicked_open_in_viking()
    r.check('the next open of the session does not ask again',
            len(got) == 2 and 'client=test' in got[1] and js(c, 'document.getElementById("ctx-menu") === null'), got)

    store(seen('viking'))
    menu_item(c, key, 'Choose Viking client…')
    time.sleep(0.4)
    r.check('Choose Viking client… asks even when only one has answered',
            js(c, "(() => { const t = document.querySelector('#ctx-menu .ctx-title'); return t && t.textContent; })()")
            == 'Open in which Viking?')
    js(c, "[...document.querySelectorAll('#ctx-menu .ctx-item')].find((e) => e.textContent.trim() === 'Viking Test').click()")
    c.wait_for('window.__opened.length > 0 && window.__opened[0].href', 15)
    got = hrefs()
    r.check('and the pick opens there, with the other behind it',
            len(got) == 1 and 'client=test' in got[0] and 'then=viking' in got[0], got)
    got = clicked_open_in_viking()
    r.check('then the plain item follows the pick', len(got) == 2 and 'client=test' in got[1], got)

    # -- not Windows: nothing changes ------------------------------------------------------------
    c.emulate('mac')
    c.goto(ctx.url, extra_script=CLEAR + SPY_OPEN)
    key = load(c, ctx.cell)
    labels = menu_labels()
    r.check('off Windows there is no client to choose', 'Open in Viking' in labels and 'Choose Viking client…' not in labels, labels)
    close_menu()
    # A request in flight across the click, ending under the flare: its "ready" took the line.
    js(c, GATE_HEALTH)
    js(c, HEALTH)
    c.wait_for('window.__gates.length === 1', 5)
    menu_item(c, key, 'Open in Viking')
    time.sleep(0.3)
    js(c, 'window.__gates[0].open()')
    c.wait_for('window.__gates[0].done', 5)
    time.sleep(0.2)
    r.check('and Open in Viking says it can only be run on Windows, opening nothing',
            js(c, 'window.__opened.length') == 0 and 'can only be run on Windows' in js(c, STATUS),
            js(c, STATUS))
    js(c, HEALTH)
    c.wait_for('window.__gates.length === 2', 5)
    time.sleep(0.2)
    r.check('a request started under the flare does not take the line either',
            'can only be run on Windows' in js(c, STATUS), js(c, STATUS))
    r.check('once the flare has been read (4 s), work still going on takes the line back',
            c.wait_for(f"{STATUS} === 'working…'", 6), js(c, STATUS))
    js(c, 'window.__gates[1].open()')
    r.check('and its end says "ready" as ever', c.wait_for(f"{STATUS} === 'ready'", 5), js(c, STATUS))


# -- the Voxel size: typed, estimated, fitted per cell ------------------------------------

# Records every estimate asked for and every mesh requested, and holds back the mesh
# requests of the cells in `__holdIds` (a big cell's build is checked by what was asked
# for, not waited for).
VOXEL_SPY = r"""
(() => {
  const api = window.sbfsem.api;
  if (api.__estimates) return;
  api.__estimates = [];
  api.__meshes = [];
  window.__holdIds = new Set();
  const estimate = api.renderEstimate;
  api.renderEstimate = (source, id, opts, extra) => {
    api.__estimates.push({ id, pixel: opts.pixel_size_um, method: opts.method });
    return estimate(source, id, opts, extra);
  };
  const mesh = api.mesh;
  api.mesh = async (source, id, opts, extra) => {
    api.__meshes.push({ id, pixel: opts.pixel_size_um, method: opts.method, voxel2: opts.voxel2_um,
                        pathway: opts.pathway });
    if (window.__holdIds.has(id)) await new Promise(() => {});
    return mesh(source, id, opts, extra);
  };
})();
"""


def type_into(c: Chrome, target: str, text: str, gap: float = 0.08) -> None:
    """Type `text` into the box `target` (a JavaScript expression), one character at a time, as a
    person does: the box is focused and emptied first, and each character is an input event."""
    js(c, "(() => { const b = %s; b.focus(); b.select(); })()" % target)
    for ch in text:
        c.send('Input.insertText', text=ch)
        time.sleep(gap)


def press_key(c: Chrome, target: str, key: str) -> None:
    """A key on the box `target`, focused first (Enter, Escape, ArrowUp ...)."""
    js(c, "%s.focus()" % target)
    c.key(key)


def select_only(c: Chrome, keys: list[str]) -> None:
    js(c, """(() => { const s = window.sbfsem.state; const want = new Set(%s);
        for (const k of [...s.neurons.keys()]) {
          if (s.selection.has(k) !== want.has(k)) document.querySelector(`.neuron-item[data-key="${k}"] .sel`).click();
        } })()""" % json.dumps(keys))
    c.settle(2)


def set_control(c: Chrome, ident: str, value: str) -> None:
    js(c, """(() => { const s = document.getElementById(%s); s.value = %s;
        s.dispatchEvent(new Event('input', { bubbles: true }));
        s.dispatchEvent(new Event('change', { bubbles: true })); })()""" % (json.dumps(ident), json.dumps(value)))
    c.settle(2)


BOX = "document.getElementById('pixel-size')"
CHIP = "document.getElementById('cost-chip')"


def chip_settled(c: Chrome, seconds: float = 90) -> dict:
    c.wait_for(f"{CHIP}.textContent !== '≈ …'", seconds)
    c.settle(2)
    return js(c, f"({{ text: {CHIP}.textContent, cls: {CHIP}.className, help: {CHIP}.dataset.help }})")


def voxel(c: Chrome, r: Report, ctx):
    r.start('voxel: a size typed into the Voxel box, the estimate beside it, a size that fits each cell')
    boot(c, ctx.url)
    js(c, VOXEL_SPY)
    small = load(c, ctx.cell)
    select_only(c, [small])
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    set_control(c, 'render-method', 'closedcurve')
    chip = chip_settled(c)
    r.check('the chip shows the server\'s estimate for an outline method',
            ' tri · ' in chip['text'] and 'as the server estimates it' in chip['help'], chip)
    r.check('the list beside the box is a select of the named sizes, the box a text box',
            js(c, f"[{BOX}.tagName, {BOX}.type, document.getElementById('pixel-size-menu').tagName]")
            == ['INPUT', 'text', 'SELECT'])

    # Typing: one estimate for the size typed last, not one a keystroke.
    asked = js(c, 'window.sbfsem.api.__estimates.length')
    type_into(c, BOX, '0.032')
    time.sleep(1.5)
    typed = js(c, 'window.sbfsem.api.__estimates.slice(%d)' % asked)
    r.check('typing a size asks the server once, for the size typed, before Enter',
            [e['pixel'] for e in typed] == [0.032], typed)
    press_key(c, BOX, 'Enter')
    shown = js(c, f"[{BOX}.value, document.getElementById('pixel-size-menu').value]")
    r.check('Enter sets it: the box shows it, the list names none', shown == ['0.032', ''], shown)
    chip = chip_settled(c)
    r.check('the chip follows', '0.032 µm' in chip['help'], chip['help'][:200])

    type_into(c, BOX, 'abc')
    press_key(c, BOX, 'Enter')
    back = js(c, f"[{BOX}.value, {BOX}.classList.contains('invalid')]")
    r.check('a size it cannot read goes back to the one set, marked', back == ['0.032', True], back)
    type_into(c, BOX, '0.07')
    press_key(c, BOX, 'Escape')
    r.check('Escape puts the size back', js(c, f'{BOX}.value') == '0.032', js(c, f'{BOX}.value'))
    type_into(c, BOX, '0.002')
    js(c, f'{BOX}.blur()')
    c.settle(2)
    r.check('leaving the box with a size under the floor goes back too',
            js(c, f'{BOX}.value') == '0.032', js(c, f'{BOX}.value'))
    press_key(c, BOX, 'ArrowUp')
    stepped = js(c, f"[{BOX}.value, document.getElementById('pixel-size-menu').value]")
    r.check('ArrowUp steps to the next named size', stepped == ['0.05', '0.05'], stepped)

    # A big traced cell at a size past its limits: built at the finest that fits it, said so;
    # the small cell beside it keeps the size it was given.
    big = load(c, ctx.big, ctx.big_source)
    js(c, 'window.__holdIds.add(%d)' % ctx.big)
    select_only(c, [small, big])
    set_control(c, 'render-method', 'closedcurve')
    type_into(c, BOX, '0.012')
    press_key(c, BOX, 'Enter')
    chip = chip_settled(c, 180)
    r.check('the chip is red for a size past a cell\'s limit, and says which', 'over' in chip['cls']
            and 'too fine' in chip['text'] and 'Finest size that fits' in chip['help'], chip)
    # Asked with the options the app sends (Join bodies at the viewer's 5 um, which costs memory).
    estimate = js(c, """window.sbfsem.api.renderEstimate('%s', %d, { method: 'closedcurve', pixel_size_um: 0.012,
                  curve_tie_um: 5 })""" % (ctx.big_source, ctx.big), wait=True)
    r.check('the server names the finest size that fits the big cell', estimate['over'] and estimate['finest_um']
            and estimate['finest_um'] > 0.012, {k: estimate.get(k) for k in ('over', 'finest_um', 'binding', 'faces')})
    first = js(c, 'window.sbfsem.api.__meshes.length')
    x, y, *_ = c.center('#apply-selected')
    c.click(x, y)
    c.wait_for('window.sbfsem.api.__meshes.slice(%d).some((m) => m.id === %d)' % (first, ctx.big), 240)
    asked = js(c, 'window.sbfsem.api.__meshes.slice(%d)' % first)
    big_asked = [m['pixel'] for m in asked if m['id'] == ctx.big]
    small_asked = [m['pixel'] for m in asked if m['id'] != ctx.big]
    r.check('the big cell is built at the finest size that fits it', big_asked == [estimate['finest_um']],
            asked)
    r.check('the small cell keeps the size asked for', small_asked == [0.012], asked)
    c.wait_for("!document.getElementById('render-notice').hidden", 10)
    note = js(c, """[document.getElementById('render-notice-title').textContent,
        document.getElementById('render-notice-text').textContent]""")
    r.check('a notice names the cell and its size', ('%g' % estimate['finest_um']) in note[0]
            and f'c{ctx.big}' in note[1] and '0.012 →' in note[1], note)
    r.check('the box keeps the size asked for, the cells being at two sizes',
            js(c, f'{BOX}.value') == '0.012', js(c, f'{BOX}.value'))
    rendered = js(c, """Object.fromEntries([...window.sbfsem.state.neurons.values()].map(
        (n) => [n.id, n.render.pixel_size_um]))""")
    r.check('each cell holds the size it is built at', rendered.get(str(ctx.big)) == estimate['finest_um']
            and rendered.get(str(ctx.cell)) == 0.012, rendered)

    # The big cell alone: one size for every selected cell, so the box shows it.
    boot(c, ctx.url)
    js(c, VOXEL_SPY)
    big = load(c, ctx.big, ctx.big_source)
    js(c, 'window.__holdIds.add(%d)' % ctx.big)
    select_only(c, [big])
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    set_control(c, 'render-method', 'closedcurve')
    type_into(c, BOX, '0.012')
    press_key(c, BOX, 'Enter')
    chip_settled(c, 180)
    x, y, *_ = c.center('#apply-selected')
    c.click(x, y)
    c.wait_for('window.sbfsem.api.__meshes.some((m) => m.id === %d && m.method === "closedcurve")' % ctx.big, 240)
    c.settle(2)
    r.check('one cell coarsened alone: the box shows the size it is built at',
            js(c, f'{BOX}.value') == ('%g' % estimate['finest_um']), js(c, f'{BOX}.value'))

    # The compare pane's form is the same control, with the same estimate.
    boot(c, ctx.url)
    js(c, VOXEL_SPY)
    small = load(c, ctx.cell)
    js(c, "document.querySelector('.tab[data-view=\"compare\"]').click()")
    c.settle(3)
    js(c, """(() => { const p = document.getElementById('compare-cell'); p.value = %s;
        p.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(small))
    js(c, "window.sbfsem.compare.panes[0].optsButton.click()")
    c.settle(2)
    pane_box = "document.querySelector('#panel-compare-options [data-key=\"pixel_size_um\"] input')"
    js(c, """(() => { const m = document.querySelector('#panel-compare-options [data-key="method"] select');
        m.value = 'closedcurve'; m.dispatchEvent(new Event('input', { bubbles: true }));
        m.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    c.settle(2)
    type_into(c, pane_box, '0.03')
    press_key(c, pane_box, 'Enter')
    pane_chip = "document.querySelector('#panel-compare-options .cost-chip')"
    c.wait_for(f"{pane_chip}.textContent !== '≈ …'", 90)
    c.settle(2)
    got = js(c, f"""({{ box: {pane_box}.value, chip: {pane_chip}.textContent,
        help: {pane_chip}.dataset.help,
        layer: window.sbfsem.compare.panes[0].layers[0].config.options.pixel_size_um }})""")
    r.check('Render compare: a size typed into a pane\'s form is the layer\'s, with the estimate',
            got['box'] == '0.03' and got['layer'] == 0.03 and ' tri · ' in got['chip']
            and '0.03 µm' in got['help'], {k: (v[:120] if isinstance(v, str) else v) for k, v in got.items()})

    # And a layer too fine for its cell is built at the finest size that fits it.
    js(c, "document.querySelector('.tab[data-view=\"render\"]').click()")
    big = load(c, ctx.big, ctx.big_source)
    js(c, 'window.__holdIds.add(%d)' % ctx.big)
    js(c, "document.querySelector('.tab[data-view=\"compare\"]').click()")
    c.settle(2)
    js(c, """(() => { const p = document.getElementById('compare-cell'); p.value = %s;
        p.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(big))
    c.settle(2)
    js(c, "window.sbfsem.compare.panes[0].optsButton.getAttribute('aria-expanded') === 'true'"
          " || window.sbfsem.compare.panes[0].optsButton.click()")
    js(c, """(() => { const m = document.querySelector('#panel-compare-options [data-key="method"] select');
        m.value = 'closedcurve'; m.dispatchEvent(new Event('input', { bubbles: true }));
        m.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    type_into(c, pane_box, '0.012')
    press_key(c, pane_box, 'Enter')
    first = js(c, 'window.sbfsem.api.__meshes.length')
    js(c, "document.getElementById('compare-build').click()")
    c.wait_for('window.sbfsem.api.__meshes.slice(%d).some((m) => m.id === %d && m.method === "closedcurve")'
               % (first, ctx.big), 240)
    asked = [m['pixel'] for m in js(c, 'window.sbfsem.api.__meshes.slice(%d)' % first)
             if m['id'] == ctx.big and m['method'] == 'closedcurve']
    layer = js(c, 'window.sbfsem.compare.panes[0].layers[0].config.options.pixel_size_um')
    status = js(c, "document.getElementById('compare-status').textContent")
    r.check('Render compare: a layer too fine for its cell is built at the finest size that fits',
            asked == [estimate['finest_um']] and layer == estimate['finest_um']
            and 'the finest Voxel size that fits' in status, {'asked': asked, 'layer': layer, 'status': status})


def race(c: Chrome, r: Report, ctx):
    r.start('race: a size applied while an estimate was out is not overwritten by its answer')
    boot(c, ctx.url)
    js(c, VOXEL_SPY)
    # The estimate for 0.005 um is held back, and then says "too fine, 0.022 fits".
    js(c, r"""(() => {
      const api = window.sbfsem.api;
      const spied = api.renderEstimate;
      let release;
      window.__estimateGate = new Promise((resolve) => { release = resolve; });
      window.__releaseEstimate = () => release();
      window.__heldEstimates = [];
      api.renderEstimate = async (source, id, opts, extra) => {
        if (opts.pixel_size_um !== 0.005) return spied(source, id, opts, extra);
        window.__heldEstimates.push(id);
        await window.__estimateGate;
        return { applies: true, method: opts.method, pixel_size_um: 0.005, voxels: 1e6, faces: 2e7,
          triangles: 3e7, peak_mb: 1e4, serve_mb: 1e4, server_mb: 2e4, json_mb: 1e3, tab_mb: 2e3,
          seconds: 600, over: ['faces'], finest_um: 0.022, binding: 'faces',
          limits: { voxels: 3e8, faces: 1e7, memory_mb: 9000, json_mb: 500 },
          basis: { kind: 'reference', pixel_um: 0.1 }, approximate: false };
      };
    })()""")
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    set_control(c, 'render-method', 'closedcurve')
    type_into(c, BOX, '0.005')
    press_key(c, BOX, 'Enter')
    # A disc-traced cell loads under the panel's settings, and its build waits on that estimate.
    key = load(c, ctx.cell, mesh=False)
    held = c.wait_for("window.__heldEstimates.includes(%d)" % ctx.cell, 60)
    r.check('the load asks for the estimate of 0.005 um, which is held', held)
    # Meanwhile the person applies 0.1 um to the cell; it is built and drawn.
    select_only(c, [key])
    set_control(c, 'render-method', 'closedcurve')
    type_into(c, BOX, '0.1')
    press_key(c, BOX, 'Enter')
    x, y, *_ = c.center('#apply-selected')
    c.click(x, y)
    drawn = c.wait_for("window.sbfsem.api.__meshes.some((m) => m.id === %d && m.pixel === 0.1)"
                       " && window.sbfsem.renderer.meshes.has(%s)" % (ctx.cell, json.dumps(key)), 240)
    r.check('the size applied meanwhile is built', drawn)
    js(c, 'window.__releaseEstimate()')
    time.sleep(2.0)
    c.settle(3)
    now = js(c, f'{STATE}.neurons.get({json.dumps(key)}).render.pixel_size_um')
    title = js(c, "document.getElementById('render-notice').hidden ? '' : "
                  "document.getElementById('render-notice-title').textContent")
    r.check('the late answer does not overwrite it, nor announce a size', now == 0.1
            and '0.022' not in title, {'pixel': now, 'notice': title})


def fine(c: Chrome, r: Report, ctx):
    r.start('fine: a Fine voxel the server built coarser is named, with why')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    select_only(c, [key])
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    set_control(c, 'render-method', 'cylinder')
    set_control(c, 'pathway', 'voxel2')
    set_control(c, 'voxel2-um', '0.01')
    drawn = js(c, 'window.sbfsem.renderer.meshes.size')
    del drawn
    x, y, *_ = c.center('#apply-selected')
    c.click(x, y)
    shown = c.wait_for("!document.getElementById('render-notice').hidden"
                       " && document.getElementById('render-notice-title').textContent.startsWith('Fine voxel')", 240)
    note = js(c, """[document.getElementById('render-notice-title').textContent,
        document.getElementById('render-notice-text').textContent]""")
    r.check('the notice says which Fine voxel was used and why', shown and '0.01 µm typed' in note[1]
            and ('finest typed voxel' in note[1] or 'raw triangles' in note[1] or 'field evaluations' in note[1]), note)


CONFLICT = "document.getElementById('render-conflict')"
MARKED = "[...document.querySelectorAll('#panel-render .field.conflict')].map((e) => e.dataset.key).sort()"


def conflicts(c: Chrome, r: Report, ctx):
    r.start('conflicts: settings that will not build as set turn black and red, with fixes to pick')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    select_only(c, [key])
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    set_control(c, 'render-method', 'cylinder')
    set_control(c, 'pathway', 'voxel2')
    r.check('settings that build as set mark nothing', c.wait_for(f'{CONFLICT}.hidden', 30)
            and js(c, MARKED) == [], js(c, MARKED))
    # c943 (121 um2 of surface) under a Face cap of 20,000 is held to 0.027 um; 0.02 is typed.
    set_control(c, 'voxel2-faces', '20000')
    set_control(c, 'voxel2-um', '0.02')
    shown = c.wait_for(f'!{CONFLICT}.hidden', 60)
    got = js(c, f"""(() => {{
        const style = (el) => {{ const s = getComputedStyle(el); return [s.backgroundColor, s.color]; }};
        const field = document.getElementById('voxel2-um-field');
        return {{ marked: {MARKED}, text: {CONFLICT}.innerText,
          field: style(field), box: style(document.getElementById('voxel2-um')),
          fixes: [...{CONFLICT}.querySelectorAll('.render-conflict-use')].map((b) => [b.textContent, b.classList.contains('best')]),
          invalid: document.getElementById('voxel2-um').getAttribute('aria-invalid') }};
    }})()""")
    r.check('a typed Fine voxel the Face cap holds coarser marks both', shown
            and got['marked'] == ['voxel2_faces', 'voxel2_um'] and got['invalid'] == 'true', got)
    r.check('black, with red text: the field and its box', got['field'] == ['rgb(0, 0, 0)', 'rgb(255, 0, 0)']
            and got['box'] == ['rgb(0, 0, 0)', 'rgb(255, 0, 0)'], got)
    r.check('the row says why and lists the fixes, the least Face cap first', f'coarser for c{ctx.cell}' in got['text']
            and got['fixes'] and got['fixes'][0][0].startswith('Face cap ') and got['fixes'][0][1]
            and any(f[0] == 'Face cap 0 (keep every triangle)' for f in got['fixes'])
            and got['fixes'][-1][0].startswith('Fine voxel '), got['fixes'])
    x, y, *_ = c.center('#render-conflict .render-conflict-use.best')
    c.click(x, y)
    cleared = c.wait_for(f'{CONFLICT}.hidden', 60)
    now = js(c, f"[document.getElementById('voxel2-faces').value, document.getElementById('voxel2-um').value, {MARKED}]")
    cap = int(got['fixes'][0][0].removeprefix('Face cap ').replace(',', ''))
    r.check('picking the best fix puts it in the controls and clears the marks; nothing is built yet',
            cleared and now[0] == str(cap) and now[1] == '0.02' and now[2] == [], now)
    meshes = js(c, 'window.sbfsem.renderer.meshes.size')
    x, y, *_ = c.center('#apply-selected')
    c.click(x, y)
    c.wait_for("document.getElementById('status').textContent.startsWith('Rebuilt')", 240)
    c.settle(2)
    note = js(c, "document.getElementById('render-notice').hidden ? '' : "
                 "document.getElementById('render-notice-title').textContent")
    r.check('Apply builds it as typed: no Fine voxel notice', not note.startswith('Fine voxel')
            and js(c, 'window.sbfsem.renderer.meshes.size') == meshes, note)

    # Closed curve past the server's memory, with Join bodies on: an estimate held to it.
    js(c, r"""(() => {
      const api = window.sbfsem.api;
      const real = api.renderEstimate;
      api.renderEstimate = async (source, id, opts, extra) => {
        if (opts.method !== 'closedcurve' || opts.pixel_size_um !== 0.006) return real(source, id, opts, extra);
        return { applies: true, method: 'closedcurve', pixel_size_um: 0.006, voxels: 1e7, faces: 8e6,
          triangles: 1.5e7, peak_mb: 9000, serve_mb: 4500, server_mb: 13500, json_mb: 540, tab_mb: 900,
          seconds: 240, over: ['memory', 'page'], finest_um: 0.011, binding: 'memory',
          alternatives: [{ off: 'joins', set: { curve_tie_um: 0 }, finest_um: 0.009, fits: false }],
          limits: { voxels: 3e8, faces: 1e7, memory_mb: 9000, json_mb: 500 },
          basis: { kind: 'built', pixel_um: 0.1 }, approximate: false };
      };
    })()""")
    set_control(c, 'render-method', 'closedcurve')
    type_into(c, BOX, '0.006')
    press_key(c, BOX, 'Enter')
    shown = c.wait_for(f'!{CONFLICT}.hidden', 60)
    got = js(c, f"({{ marked: {MARKED}, fixes: [...{CONFLICT}.querySelectorAll('.render-conflict-use')].map((b) => b.textContent) }})")
    r.check('Closed curve too fine: Voxel and Join bodies are marked, the finest size first',
            shown and got['marked'] == ['curve_tie_um', 'pixel_size_um']
            and got['fixes'] == ['Voxel 0.011 µm', 'Join bodies off, Voxel 0.009 µm'], got)
    buttons = js(c, f"[...{CONFLICT}.querySelectorAll('.render-conflict-use')].length")
    del buttons
    x, y, *_ = c.center('#render-conflict li:nth-child(2) .render-conflict-use')
    c.click(x, y)
    c.wait_for(f'{CONFLICT}.hidden', 60)
    now = js(c, f"[{BOX}.value, document.getElementById('curve-tie').value, {MARKED}]")
    r.check('the other way is two settings at once', now == ['0.009', '0', []], now)
    set_control(c, 'render-method', 'cylinder')
    set_control(c, 'pathway', 'original')
    r.check('a method that reads neither marks nothing', c.wait_for(f'{CONFLICT}.hidden', 10)
            and js(c, MARKED) == [], js(c, MARKED))
# -- compare -----------------------------------------------------------------------------

COMPARE = 'window.sbfsem.compare'
TECHS = ['voxel2', 'original', 'tube', 'wrap']


def _compare_tab(c: Chrome, panes: int) -> None:
    """Open Render compare with `panes` panes (the page's own tab button and Panes box)."""
    js(c, "document.querySelector('[data-view=compare]').click()")
    c.settle(3)
    js(c, """(() => { const s = document.getElementById('compare-panes'); s.value = '%d';
        s.dispatchEvent(new Event('change', { bubbles: true })); })()""" % panes)
    c.settle(3)


def _xy(c: Chrome, expression: str, scroll: bool = True):
    """Where to put the pointer for the element a JavaScript expression names (scrolled into view)."""
    return js(c, """(() => { const e = %s; if (!e) return null; %s
        const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()"""
              % (expression, "e.scrollIntoView({ block: 'nearest' });" if scroll else ''))


def _pane_el(pane: int, inside: str = '') -> str:
    return f"{COMPARE}.panes[{pane}].{inside}" if inside else f"{COMPARE}.panes[{pane}]"


def _click_js(c: Chrome, expression: str) -> None:
    where = _xy(c, expression)
    if where is None:
        raise RuntimeError(f'nothing to click: {expression}')
    c.click(*where)


def _layers_to(c: Chrome, pane: int, techs: list[str]) -> None:
    """Give a pane one layer per technique, through the page's own + menu and the editor's select."""
    el = _pane_el(pane)
    for _ in range(len(techs) - 1):
        _click_js(c, f'{el}.addButton')
        _click_js(c, f'{el}.addLayerItem')
    for k, tech in enumerate(techs):
        js(c, """(() => { const v = %s; const p = v.panes[%d]; p.editing = %d; v._fillEditor(p, { keepOpen: true });
            p.techSelect.value = '%s'; p.techSelect.dispatchEvent(new Event('change', { bubbles: true })); })()"""
           % (COMPARE, pane, k, tech))
    js(c, f'{COMPARE}._closePop()')


def _focus_at(c: Chrome, expression: str | None = None) -> str:
    """Where the keyboard is: a short description of document.activeElement (or of `expression`)."""
    return js(c, """(() => { const e = %s; if (!e) return 'nothing'; const v = %s;
        const where = v.panes.findIndex((p) => p.el.contains(e));
        const inEditor = where >= 0 && v.panes[where].editor.contains(e);
        return (e.tagName === 'BODY' ? 'page' : e.tagName.toLowerCase() + (e.className ? '.' + String(e.className).split(' ')[0] : ''))
          + (where >= 0 ? ' of pane ' + (where + 1) + (inEditor ? " (in the layer editor)" : '') : ''); })()"""
              % (expression or 'document.activeElement', COMPARE))


# What each state does to a layer: only the facts the pane's state and the chips' words are derived from.
# Measured in each: the header's height, whether a name is cut short to under 25 px, and whether a layer's dot
# (its switch) is clipped out of the line it is in.  A pane that is `narrow` or `tight` has dots and arrows
# for names, and is not asked for a name.
HEADER_STATES = r"""(() => {
  const v = %s;
  const STATES = {
    idle: () => {}, queued: (l) => { l.task = { phase: 'queued' }; }, building: (l) => { l.task = { phase: 'building' }; },
    measuring: (l) => { l.task = { phase: 'measuring' }; }, stale: (l) => { l.mesh = { n_faces: 1 }; },
    stopped: (l) => { l.stopped = true; }, failed: (l) => { l.error = { kind: 'build', error: new Error('x') }; },
  };
  const reset = (l) => { l.task = null; l.stopped = false; l.error = null; l.mesh = null; };
  const heights = {}, fit = [];
  let tight = 0;
  for (const [name, set] of Object.entries(STATES)) {
    for (const which of ['all', 'first']) {
      for (const p of v.panes) { p.layers.forEach((l, i) => { reset(l); if (which === 'all' || i === 0) set(l); }); v._renderState(p); }
      v.panes.forEach((p, i) => {
        (heights[i] = heights[i] || []).push(Math.round(p.head.getBoundingClientRect().height * 10) / 10);
        if (v._isDiff(p)) return;
        const compact = p.el.classList.contains('tight') || p.el.classList.contains('narrow');
        if (p.el.classList.contains('tight')) tight += 1;
        const line = p.chipsEl.getBoundingClientRect();
        for (const d of p.chipsEl.querySelectorAll('.layer-dot')) {
          const r = d.getBoundingClientRect();
          if (r.right > line.right + 0.5 || r.left < line.left - 0.5) fit.push([i + 1, name, 'a dot is clipped']);
        }
        if (!compact) for (const l of p.layers) {
          const n = l.chip.name;
          if (n.scrollWidth > n.clientWidth + 1 && n.getBoundingClientRect().width < 25) fit.push([i + 1, name, 'a name is cut to ' + Math.round(n.getBoundingClientRect().width) + ' px']);
        }
      });
    }
  }
  for (const p of v.panes) { p.layers.forEach(reset); v._renderState(p); }
  return { jitter: v.panes.map((p, i) => [i + 1, [...new Set(heights[i])]]).filter(([, h]) => h.length > 1), fit, tight };
})()""" % COMPARE


# -- compareconflicts ----------------------------------------------------------------------

PANE_FORM = f'{COMPARE}.optionsPanel.form'
PANE_ROW = "document.querySelector('#panel-compare-options .render-conflict')"
PANE_MARKED = ("[...document.querySelectorAll('#panel-compare-options .field.conflict')]"
               ".map((e) => e.dataset.key).sort()")


def _pane_set(c: Chrome, key: str, value: str) -> None:
    """Set a control of the pane options panel as a person does: its value, then input and change."""
    js(c, """(() => { const b = %s.control(%s); b.value = %s;
        b.dispatchEvent(new Event('input', { bubbles: true }));
        b.dispatchEvent(new Event('change', { bubbles: true })); })()""" % (PANE_FORM, json.dumps(key), json.dumps(value)))
    c.settle(2)


def compareconflicts(c: Chrome, r: Report, ctx):
    r.start('compareconflicts: a Render compare pane\'s settings that will not build as set, with fixes to pick')
    boot(c, ctx.url)
    js(c, VOXEL_SPY)
    key = load(c, ctx.cell)
    main = "({ faces: document.getElementById('voxel2-faces').value, um: document.getElementById('voxel2-um').value," \
           " pixel: document.getElementById('pixel-size').value, tie: document.getElementById('curve-tie').value })"
    main_shown = js(c, main)
    _compare_tab(c, 2)
    js(c, """(() => { const p = document.getElementById('compare-cell'); p.value = %s;
        p.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(key))
    c.settle(2)
    for pane in (0, 1):
        js(c, """(() => { const t = %s.panes[%d].techSelect; t.value = 'voxel2';
            t.dispatchEvent(new Event('change', { bubbles: true })); })()""" % (COMPARE, pane))
    _click_js(c, _pane_el(0, 'optsButton'))
    c.settle(2)
    r.check('settings that build as set mark nothing', c.wait_for(f'{PANE_ROW}.hidden', 30)
            and js(c, PANE_MARKED) == [], js(c, PANE_MARKED))
    # c943 under a Face cap of 20,000 is held to 0.027 um; 0.02 is typed (as in the conflicts section).
    _pane_set(c, 'voxel2_faces', '20000')
    _pane_set(c, 'voxel2_um', '0.02')
    shown = c.wait_for(f'!{PANE_ROW}.hidden', 60)
    got = js(c, f"""(() => {{
        const style = (el) => {{ const s = getComputedStyle(el); return [s.backgroundColor, s.color]; }};
        const form = {PANE_FORM};
        return {{ marked: {PANE_MARKED}, text: {PANE_ROW}.innerText,
          field: style(form.el('voxel2_um')), box: style(form.control('voxel2_um')),
          fixes: [...{PANE_ROW}.querySelectorAll('.render-conflict-use')].map((b) => [b.textContent, b.classList.contains('best')]),
          invalid: form.control('voxel2_um').getAttribute('aria-invalid'),
          rows: document.querySelectorAll('.render-conflict').length,
          ids: document.querySelectorAll('#render-conflict').length,
          main: document.getElementById('render-conflict').hidden }};
    }})()""")
    r.check('a pane\'s typed Fine voxel the Face cap holds coarser marks both of its controls', shown
            and got['marked'] == ['voxel2_faces', 'voxel2_um'] and got['invalid'] == 'true', got)
    r.check('black, with red text: the field and its box', got['field'] == ['rgb(0, 0, 0)', 'rgb(255, 0, 0)']
            and got['box'] == ['rgb(0, 0, 0)', 'rgb(255, 0, 0)'], got)
    r.check('the pane\'s row says why and lists the fixes, the least Face cap first',
            f'coarser for c{ctx.cell} · {ctx.source}' in got['text']
            and got['fixes'] and got['fixes'][0][0].startswith('Face cap ') and got['fixes'][0][1]
            and any(f[0] == 'Face cap 0 (keep every triangle)' for f in got['fixes'])
            and got['fixes'][-1][0].startswith('Fine voxel '), got['fixes'])
    r.check('the pane\'s row is its own: the 3D render panel\'s stays hidden, one element has its id',
            got['rows'] == 2 and got['ids'] == 1 and got['main'], got)
    other = js(c, f'JSON.stringify({COMPARE}.panes[1].layers[0].config.options)')
    builds = js(c, 'window.sbfsem.api.__meshes.length')
    _click_js(c, f'{PANE_ROW}.querySelector(".render-conflict-use.best")')
    cleared = c.wait_for(f'{PANE_ROW}.hidden', 60)
    cap = int(got['fixes'][0][0].removeprefix('Face cap ').replace(',', ''))
    now = js(c, f"""({{ layer: [{COMPARE}.panes[0].layers[0].config.options.voxel2_faces,
        {COMPARE}.panes[0].layers[0].config.options.voxel2_um],
        box: [{PANE_FORM}.control('voxel2_faces').value, {PANE_FORM}.control('voxel2_um').value],
        marked: {PANE_MARKED}, other: JSON.stringify({COMPARE}.panes[1].layers[0].config.options),
        main: {main}, status: document.getElementById('compare-status').textContent,
        builds: window.sbfsem.api.__meshes.length }})""")
    r.check('picking the best fix puts it in that layer and its boxes and clears the marks',
            cleared and now['layer'] == [cap, 0.02] and now['box'] == [str(cap), '0.02'] and now['marked'] == [], now)
    r.check('only that layer: the other pane and the 3D render panel keep their settings, nothing is built',
            now['other'] == other and now['main'] == main_shown and now['builds'] == builds
            and 'press Compare' in now['status'], now)
    _click_js(c, "document.getElementById('compare-build')")
    c.wait_for(f"{COMPARE}.panes[0].layers[0].mesh && !{COMPARE}.running", 240)
    c.settle(2)
    built = js(c, f"""(() => {{ const m = {COMPARE}.panes[0].layers[0].mesh || {{}};
        return {{ used: m.voxel2_um ?? null, coarsened: m.voxel2_coarsened ?? null, faces: m.n_faces ?? null,
          asked: window.sbfsem.api.__meshes.slice({builds}).filter((x) => x.id === {ctx.cell}).length }}; }})()""")
    r.check('Compare builds it as typed', built['asked'] >= 1 and not built['coarsened']
            and built['used'] is not None and abs(built['used'] - 0.02) < 1e-6, built)

    # Closed curve past the server's memory, with Join bodies on: an estimate held to it.
    js(c, r"""(() => {
      const api = window.sbfsem.api;
      const real = api.renderEstimate;
      api.renderEstimate = async (source, id, opts, extra) => {
        if (opts.method !== 'closedcurve' || opts.pixel_size_um !== 0.006) return real(source, id, opts, extra);
        return { applies: true, method: 'closedcurve', pixel_size_um: 0.006, voxels: 1e7, faces: 8e6,
          triangles: 1.5e7, peak_mb: 9000, serve_mb: 4500, server_mb: 13500, json_mb: 540, tab_mb: 900,
          seconds: 240, over: ['memory', 'page'], finest_um: 0.011, binding: 'memory',
          alternatives: [{ off: 'joins', set: { curve_tie_um: 0 }, finest_um: 0.009, fits: false }],
          limits: { voxels: 3e8, faces: 1e7, memory_mb: 9000, json_mb: 500 },
          basis: { kind: 'built', pixel_um: 0.1 }, approximate: false };
      };
    })()""")
    _pane_set(c, 'method', 'closedcurve')
    pane_box = f"{PANE_FORM}.control('pixel_size_um')"
    type_into(c, pane_box, '0.006')
    press_key(c, pane_box, 'Enter')
    shown = c.wait_for(f'!{PANE_ROW}.hidden', 60)
    got = js(c, f"({{ marked: {PANE_MARKED}, fixes: [...{PANE_ROW}.querySelectorAll('.render-conflict-use')].map((b) => b.textContent) }})")
    r.check('a pane\'s Closed curve too fine: Voxel and Join bodies are marked, the finest size first',
            shown and got['marked'] == ['curve_tie_um', 'pixel_size_um']
            and got['fixes'] == ['Voxel 0.011 µm', 'Join bodies off, Voxel 0.009 µm'], got)
    _click_js(c, f'{PANE_ROW}.querySelector("li:nth-child(2) .render-conflict-use")')
    c.wait_for(f'{PANE_ROW}.hidden', 60)
    now = js(c, f"""({{ box: [{pane_box}.value, {PANE_FORM}.control('curve_tie_um').value],
        layer: [{COMPARE}.panes[0].layers[0].config.options.pixel_size_um,
          {COMPARE}.panes[0].layers[0].config.options.curve_tie_um, {COMPARE}.panes[0].layers[0].config.pixelChosen],
        marked: {PANE_MARKED}, other: JSON.stringify({COMPARE}.panes[1].layers[0].config.options), main: {main} }})""")
    r.check('the other way is two settings at once, in that layer only',
            now['box'] == ['0.009', '0'] and now['layer'] == [0.009, 0, True] and now['marked'] == []
            and now['other'] == other and now['main'] == main_shown, now)
    _pane_set(c, 'method', 'cylinder')
    _pane_set(c, 'pathway', 'original')
    r.check('a method that reads neither marks nothing', c.wait_for(f'{PANE_ROW}.hidden', 10)
            and js(c, PANE_MARKED) == [], js(c, PANE_MARKED))


def compare(c: Chrome, r: Report, ctx):
    r.start('compare: the layers\' header, its tab order and its popovers')
    boot(c, ctx.url)
    load(c, ctx.cell, mesh=False)

    # -- the header keeps its height, whatever the panes' states, at every width, pane and layer count --------
    # (Forced states: a pane's chip and a layer's words come from these facts alone.)  A header that gains a
    # line takes it from the render under it.  Every layer's dot (its switch) stays inside its line, and a name
    # is not cut to nothing: where the chips do not fit the pane is `tight` (dots and arrows) instead.
    bad, cut, tried, tight = {}, {}, 0, 0
    for width in (900, 1140, 1440, 1920):
        c.emulate('mac', width, 900)
        for panes in (3, 6):
            _compare_tab(c, panes)
            for layers in (1, 3, 4):
                js(c, """(() => { const v = %s; for (const p of v.panes) { if (v._isDiff(p)) continue;
                    while (p.layers.length > %d) v.removeLayer(p.layers[p.layers.length - 1]);
                    while (p.layers.length < %d && v.addLayer(p)) {}
                    v.setAnnotations(p, true); } })()""" % (COMPARE, layers, layers))
                c.settle(3)
                found = js(c, HEADER_STATES)
                tried += 1
                tight += found['tight']
                key = f'{width} px, {panes} panes, {layers} layers'
                if found['jitter']:
                    bad[key] = found['jitter']
                if found['fit']:
                    cut[key] = found['fit'][:3]
    r.check(f'a pane\'s header is one height in every state (idle, waiting, building, measuring, out of date, stopped, failed) '
            f'at 900, 1140, 1440 and 1920 px, 3 and 6 panes, 1, 3 and 4 layers, annotations on ({tried} layouts)', not bad, bad)
    r.check('and in each of them every layer\'s dot is inside its line and no name is cut to under 25 px (a pane whose chips '
            'do not fit shows dots and arrows instead)', not cut, cut)
    r.check('and the layouts include crowded ones: some panes went `tight`', tight > 0, tight)

    # -- four layers at 900 px, through a real build -----------------------------------------------------
    boot(c, ctx.url)
    load(c, ctx.cell, mesh=False)
    c.emulate('mac', 900, 800)
    _compare_tab(c, 4)
    pane = _pane_el(0)
    _layers_to(c, 0, TECHS)
    r.check('four layers sit in the first pane, one chip each',
            js(c, f'{pane}.layers.length') == 4
            and js(c, f'{pane}.chipsEl.querySelectorAll(".layer-chip:not(.overlay)").length') == 4)

    def head() -> list:
        return js(c, f"""[{pane}.head.getBoundingClientRect().height, {pane}.viewport.getBoundingClientRect().height,
            {pane}.layers.map((l) => l.chip.state.textContent).join('|')]""")

    states = {'not built': head()}
    js(c, '__hold()')
    _click_js(c, "document.getElementById('compare-build')")
    c.wait_for(f'{pane}.layers.some((l) => l.state === "building")', 60)
    c.settle(2)
    states['building'] = head()
    js(c, '__release()')
    c.wait_for(f'!{COMPARE}.running', 600)
    c.settle(3)
    states['ready'] = head()
    js(c, """(() => { const v = %s; const p = v.panes[0]; for (const k of [1, 2]) { p.editing = k; v._fillEditor(p);
        const s = p.techSelect; s.value = k === 1 ? 'adaptive' : 'voxel2_iso'; s.dispatchEvent(new Event('change', { bubbles: true })); } })()"""
       % COMPARE)
    c.settle(3)
    states['two out of date'] = head()
    heights = {k: round(v[0], 1) for k, v in states.items()}
    r.check('with four layers at 900 px a real build, and an edit after it, leave the header one height '
            '(not built, building, ready, out of date)',
            len(set(heights.values())) == 1,
            {'head': heights, 'render': {k: round(v[1], 1) for k, v in states.items()}})
    r.check('and the words are there (a chip says what its layer is doing)',
            'not built' in states['not built'][2] and 'building' in states['building'][2] and 'out of date' in states['two out of date'][2],
            {k: v[2] for k, v in states.items()})

    # -- Tab goes the way the eye does ---------------------------------------------------------------------
    def stops_after(badge: str, count: int) -> list:
        _click_js(c, badge)
        out = []
        for _ in range(count):
            c.key('Tab')
            out.append(js(c, """(() => { const a = document.activeElement; if (!a || !a.getBoundingClientRect) return null;
                const r = a.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.left), String(a.className.baseVal ?? a.className),
                a.closest('.compare-pane') ? [...document.querySelectorAll('.compare-pane')].indexOf(a.closest('.compare-pane')) : -1]; })()"""))
        return out

    stops = stops_after(f'{pane}.head.querySelector(".compare-num")', 9)
    inside = [s for s in stops if s and s[3] == 0]
    tops = [s[0] for s in inside]
    # The buttons sit a few pixels apart on their line (the shift box, the arrow, the dot and the name); a line is 26.
    r.check('four layers: the first stops are the pane\'s buttons, then its chips on the line under them: down the page and not up it',
            len(inside) >= 7 and all(b >= a - 8 for a, b in zip(tops, tops[1:], strict=False)) and tops[-1] > tops[0] + 15, stops)
    one = _pane_el(1)
    stops = stops_after(f'{one}.head.querySelector(".compare-num")', 6)
    inside = [s for s in stops if s and s[3] == 1]
    lefts, tops = [s[1] for s in inside], [s[0] for s in inside]
    r.check('one layer: its chip, then the buttons, left to right on one line',
            len(inside) == 6 and all(b > a for a, b in zip(lefts, lefts[1:], strict=False)) and max(tops) - min(tops) <= 8, stops)

    # -- popovers on a short window: the bottom row's end inside it --------------------------------------
    c.emulate('mac', 1440, 600)
    c.settle(3)
    js(c, f'{COMPARE}.setPaneCount(6)')
    c.settle(3)
    rooms = {}
    for index in (3, 4, 5):
        for which, expression in (('editor', f'{_pane_el(index)}.layers[0].chip.name'), ('add', f'{_pane_el(index)}.addButton'),
                                  ('more', f'{_pane_el(index)}.moreButton')):
            _click_js(c, expression)
            c.settle(2)
            node = {'editor': 'editor', 'add': 'addMenu', 'more': 'paneMenu'}[which]
            rooms[f'{which} {index + 1}'] = js(c, f"""(() => {{ const e = {_pane_el(index)}.{node}; const b = e.getBoundingClientRect();
                return [Math.round(b.top), Math.round(b.bottom), window.innerHeight, e.hidden]; }})()""")
            js(c, f'{COMPARE}._closePop()')
    bad = {k: v for k, v in rooms.items() if v[3] or v[1] > v[2] + 1 or v[0] < -1}
    r.check('at 600 px tall the bottom row\'s editors and menus end inside the window (above the header, or cut short)',
            not bad, {'outside': bad, 'all': rooms})
    # Too short for either side: cut short at the window's foot, and scrolls.
    c.emulate('mac', 1440, 360)
    c.settle(3)
    js(c, f'{COMPARE}.setPaneCount(2)')
    c.settle(3)
    _click_js(c, f'{_pane_el(0)}.layers[0].chip.name')
    c.settle(2)
    cut = js(c, f"""(() => {{ const e = {_pane_el(0)}.editor; const b = e.getBoundingClientRect(); const cs = getComputedStyle(e);
        return {{ bottom: Math.round(b.bottom), inner: window.innerHeight, overflowY: cs.overflowY, scrolls: e.scrollHeight > e.clientHeight,
                  top: Math.round(b.top), hidden: e.hidden }}; }})()""")
    r.check('at 360 px tall, with no room above the header or below it, the editor is cut short at the window\'s foot and scrolls',
            not cut['hidden'] and cut['bottom'] <= cut['inner'] and cut['overflowY'] == 'auto' and cut['scrolls'], cut)
    js(c, f'{COMPARE}._closePop()')
    c.emulate('mac', 1440, 900)


def comparekeys(c: Chrome, r: Report, ctx):
    r.start('compare: the keyboard in the layers\' header, and the color picker')
    boot(c, ctx.url)
    load(c, ctx.cell, mesh=False)
    c.emulate('mac', 1100, 800)
    _compare_tab(c, 2)
    pane = _pane_el(0)
    _layers_to(c, 0, TECHS[:3])

    # -- the annotations dot keeps the keyboard -------------------------------------------------------------
    js(c, f'{COMPARE}.setAnnotations({pane}, true)')
    c.settle(3)
    js(c, f'{pane}.annotationsChip.dot.focus()')
    c.key(' ')
    r.check('Space on the annotations dot toggles it and the keyboard stays on it',
            js(c, f'{pane}.annotationsVisible') is False and js(c, f'document.activeElement === {pane}.annotationsChip.dot'),
            _focus_at(c))
    c.key(' ')
    r.check('and the second Space toggles it back', js(c, f'{pane}.annotationsVisible') is True
            and js(c, f'document.activeElement === {pane}.annotationsChip.dot'), _focus_at(c))

    # -- the color picker the editor opens does not close it ------------------------------------------------
    _click_js(c, f'{pane}.layers[0].chip.name')
    r.check('a click on a chip opens the layer editor', js(c, f'!{pane}.editor.hidden'))
    c.click(*_xy(c, f'{pane}.colorInput'))
    opened = c.wait_for('!!document.querySelector(".color-pop")', 5)
    r.check('the Color swatch opens the page\'s color picker', opened)
    if opened:
        before = js(c, f'{pane}.layers[0].config.color')
        c.click(*_xy(c, 'document.querySelectorAll(".color-pop .cp-swatch")[3]', scroll=False))
        r.check('a click in the picker picks a color and the editor is still there', js(c, f'!{pane}.editor.hidden')
                and js(c, f'{pane}.layers[0].config.color') != before, js(c, f'{pane}.layers[0].config.color'))
        # Escape from where the keyboard is (a CDP Escape stops this headless Chrome's frames, so it is dispatched).
        js(c, """document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))""")
        c.settle(2)
        r.check('Escape closes the picker and not the editor under it',
                js(c, '!document.querySelector(".color-pop")') and js(c, f'!{pane}.editor.hidden'),
                [js(c, '!!document.querySelector(".color-pop")'), js(c, f'{pane}.editor.hidden')])
        js(c, 'document.querySelector(".color-pop")?.remove()')
    js(c, f'{COMPARE}._closePop()')

    # -- a control that disables itself hands the keyboard on, not to the page --------------------------------
    # Real Enter on a focused Duplicate: the fourth layer disables it, and the keyboard stays in the editor.
    one = _pane_el(1)
    _click_js(c, f'{one}.layers[0].chip.name')
    for _ in range(3):
        js(c, f'{one}.duplicateButton.focus()')
        c.key('Enter')
    r.check('Duplicate three times with the keyboard: four layers, the button off, and the keyboard still in the layer editor',
            js(c, f'{one}.layers.length') == 4 and js(c, f'{one}.duplicateButton.disabled') is True
            and js(c, f'{one}.editor.contains(document.activeElement)'), _focus_at(c))
    for _ in range(3):
        js(c, f'{one}.removeButton.focus()')
        c.key('Enter')
    r.check('Remove three times with the keyboard: one layer, the button off, and the keyboard still in the layer editor',
            js(c, f'{one}.layers.length') == 1 and js(c, f'{one}.removeButton.disabled') is True
            and js(c, f'{one}.editor.contains(document.activeElement)'), _focus_at(c))
    js(c, f'{COMPARE}._closePop()')
    # The + menu, opened and used from the keyboard: Render layer puts the keyboard on the chip it added,
    # Annotations on the + button; the tools menu's items on the button that opened it.
    js(c, f'{one}.addButton.focus()')
    c.key('Enter')
    in_menu = js(c, f'{one}.addMenu.hidden === false && {one}.addMenu.contains(document.activeElement)')
    c.key('Enter')
    r.check('+ then Enter on Render layer: the menu took the keyboard, a second layer was added, and the keyboard is on its chip',
            in_menu and js(c, f'{one}.layers.length') == 2 and js(c, f'document.activeElement === {one}.layers[1].chip.name'),
            _focus_at(c))
    js(c, f'{one}.addButton.focus()')
    c.key('Enter')
    js(c, f'{one}.annotationsItem.focus()')
    c.key('Enter')
    r.check('+ then Enter on Annotations: the overlay is on, and the keyboard is back on the + button',
            js(c, f'{one}.annotations') is True and js(c, f'document.activeElement === {one}.addButton'), _focus_at(c))
    js(c, f'{one}.moreButton.focus()')
    c.key('Enter')
    js(c, f'{one}.paneMenu.querySelector("button").focus()')
    js(c, f'{one}.paneMenu.querySelector("button").click()')       # Reset view: a click, since the menu closes on it
    r.check('the tools menu\'s items give the keyboard back to the button that opened the menu',
            js(c, f'document.activeElement === {one}.moreButton'), _focus_at(c))
    c.emulate('mac', 1440, 900)


def comparestate(c: Chrome, r: Report, ctx):
    r.start('compare: banners, the status line and the cameras')
    boot(c, ctx.url)
    load(c, ctx.cell, mesh=False)
    c.emulate('mac', 1440, 900)
    _compare_tab(c, 1)
    pane = _pane_el(0)
    status = "document.getElementById('compare-status').textContent"
    # Builds of the Original fail, as a server out of memory would say (and later the Tube's: pathway and method).
    js(c, """(() => { const api = window.sbfsem.api; const was = api.mesh; window.__fail = new Set(['original/cylinder']);
        api.mesh = async (source, id, opts, extra) => { if (window.__fail.has(opts.pathway + '/' + opts.method)) {
          throw Object.assign(new Error('out of memory'), { serverDetail: 'out of memory', failedStage: 'isotropic remesh' }); }
          return was(source, id, opts, extra); }; })()""")
    _layers_to(c, 0, ['voxel2', 'original', 'tube'])
    banner = f'{pane}.card.banner'
    shown = f"(() => {{ const b = {banner}; const r = b.getBoundingClientRect(); return !b.hidden && r.width > 0 && r.height > 0; }})()"
    _click_js(c, "document.getElementById('compare-build')")
    # The end, not the start: a Compare of layers that fail at once may be over before the first look.
    c.wait_for(f'{pane}.layers[1].state === "error-build" && {pane}.layers[2].state === "ready" && !{COMPARE}.running', 600)
    c.settle(3)
    r.check('layer 2 failed and layer 3 worked after it: when the work is over the red banner is on show, about layer 2',
            js(c, shown) and 'layer 2' in js(c, f'{banner}.textContent') and 'out of memory' in js(c, f'{banner}.textContent'),
            js(c, f'{banner}.textContent'))
    line = js(c, status)
    r.check('and the status line that sends people to the banner is true', 'red banner' in line and js(c, shown), line)
    # Two failed layers (the second and a fourth, new one; the third is current and is not built again): the
    # banner is the last one's, and Dismiss gives it to the other.
    js(c, "window.__fail.add('wrap/cylinder')")
    _click_js(c, f'{pane}.addButton')
    _click_js(c, f'{pane}.addLayerItem')
    js(c, """(() => { const v = %s; const p = v.panes[0]; p.editing = 3; v._fillEditor(p, { keepOpen: true });
        p.techSelect.value = 'wrap'; p.techSelect.dispatchEvent(new Event('change', { bubbles: true })); })()""" % COMPARE)
    js(c, f'{COMPARE}._closePop()')
    _click_js(c, "document.getElementById('compare-build')")
    c.wait_for(f'{pane}.layers[3].state === "error-build" && {pane}.layers[1].state === "error-build" && !{COMPARE}.running', 600)
    c.settle(3)
    first = js(c, f'{banner}.textContent')
    _click_js(c, f'{pane}.card.dismissButton')
    c.settle(2)
    second = js(c, f'{banner}.textContent')
    r.check('two layers failed: the banner is the last one\'s; Dismiss shows the other\'s, and the status line still names a banner that is there',
            js(c, shown) and 'layer 4' in first and 'layer 2' in second and 'red banner' in js(c, status),
            [first[:60], second[:60], js(c, status)])
    _click_js(c, f'{pane}.card.dismissButton')
    c.settle(2)
    r.check('and when both are dismissed there is no banner and the line stops pointing at one',
            not js(c, shown) and 'red banner' not in js(c, status), js(c, status))
    js(c, 'window.__fail.clear()')

    # -- the status line and the Panes box -----------------------------------------------------------------
    def set_panes(n: int) -> str:
        js(c, """(() => { const s = document.getElementById('compare-panes'); s.value = '%d';
            s.dispatchEvent(new Event('change', { bubbles: true })); })()""" % n)
        c.settle(3)
        return js(c, status)

    more = set_panes(5)
    fewer = set_panes(2)
    r.check('more panes: the status line says the new ones wait for Compare; fewer panes: it says nothing about filling new ones',
            'fill the new' in more and 'fill the new' not in fewer and '2 panes' in fewer, [more, fewer])
    # -- putting the grid back says how the panes stand ----------------------------------------------------
    js(c, f'{COMPARE}.toggleMaximize({_pane_el(1)})')
    c.settle(3)
    maximized = js(c, status)
    js(c, f'{COMPARE}.toggleMaximize(null)')
    c.settle(3)
    back = js(c, status)
    r.check('one pane fills the grid and says so; putting the grid back does not leave that line up',
            'fills the grid' in maximized and 'fills the grid' not in back, [maximized, back])

    # -- the camera the user arranged survives putting the grid back -------------------------------------
    # On a page of its own: the panes must not have been framed already, so that the ones that are out of sight
    # while Compare runs are framed against no size at all (the case that broke it).
    boot(c, ctx.url)
    load(c, ctx.cell, mesh=False)
    c.emulate('mac', 1440, 900)
    _compare_tab(c, 3)
    js(c, f'{COMPARE}.toggleMaximize({_pane_el(0)})')
    c.settle(3)
    _click_js(c, "document.getElementById('compare-build')")
    c.wait_for(f'{COMPARE}.panes.every((p) => p.layers[0].state === "ready") && !{COMPARE}.running', 600)
    c.settle(3)
    out_of_sight = js(c, f'{COMPARE}.panes.map((p) => p.renderer.framed)')
    r.check('(premise) the pane in front was framed and the others, built while out of sight, were framed against no size',
            out_of_sight == [True, False, False], out_of_sight)
    # An orbit of the pane in front, with the real mouse; then wait for it to come to rest (it carries on a little).
    x, y = js(c, f"""(() => {{ const r = {_pane_el(0)}.viewport.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
    c.press(x, y)
    c.move(x + 60, y + 30, buttons=1)
    c.move(x + 120, y + 55, buttons=1)
    c.release(x + 120, y + 55)
    camera = f'{_pane_el(0)}.renderer.camera.position.toArray()'
    framed = js(c, camera)
    arranged, steady = framed, 0
    for _ in range(80):
        c.settle(3)
        now = js(c, camera)
        steady = steady + 1 if all(abs(a - b) < 1e-3 for a, b in zip(now, arranged, strict=True)) else 0
        arranged = now
        if steady >= 3:
            break
    r.check('(premise) the drag moved the camera of the pane in front',
            any(abs(a - b) > 1 for a, b in zip(framed, arranged, strict=True)), {'framed': framed, 'arranged': arranged})
    js(c, f'{COMPARE}.toggleMaximize(null)')
    c.settle(8)
    after = [js(c, f'{_pane_el(k)}.renderer.camera.position.toArray()') for k in range(3)]
    r.check('the pane that was in front keeps the orbit the user gave it, and the others follow it',
            all(abs(a - b) < 1e-2 for camera_k in after for a, b in zip(camera_k, arranged, strict=True)),
            {'arranged': arranged, 'after': after})


# -- comparetoolbar -------------------------------------------------------------------------

# The window widths the grid is measured at.  900, 1140, 1440 and 1920 px are the four every layout check uses
# (CLAUDE.md), 1000 px is where the 2.5.2 reviewers measured the summary taking a line off the grid, 700 px is
# where the controls themselves wrap, and 480 px is the phone layout (the cell list above the tabs).
TOOLBAR_WIDTHS = (480, 700, 900, 1000, 1140, 1440, 1920)

# The grid, the render in it and the toolbar under it at this moment: heights, where the controls end, where the
# text is, whether it is cut short and whether the whole of it is on hover.  Written for either layout of the text
# (a flex item of the controls' row, or a row of its own), so that it runs on both.
TOOLBAR_PROBE = r"""(() => {
  const $ = (id) => document.getElementById(id);
  const tenth = (x) => Math.round(x * 10) / 10;
  const bar = $('compare-build').closest('.viewport-tools');
  const summary = $('compare-summary');
  const status = $('compare-status');
  const texts = bar.querySelector('.compare-texts');
  const last = $('compare-error').closest('label').getBoundingClientRect();
  const button = $('compare-build').getBoundingClientRect();
  const b = bar.getBoundingClientRect();
  const cut = (e) => e.scrollWidth > e.clientWidth + 1;
  const t = texts && texts.getBoundingClientRect();
  // Where the words end: what the row clips is not seen (a status with no room left is a gap past the row's edge).
  const words = [summary, status].filter((e) => e.textContent);
  const right = Math.max(0, ...words.map((e) => Math.min(e.getBoundingClientRect().right, t ? t.right : Infinity)));
  return {
    grid: tenth($('compare-grid').getBoundingClientRect().height),
    render: tenth(window.sbfsem.compare.panes[0].viewport.getBoundingClientRect().height),
    bar: tenth(b.height),
    view: tenth($('view-compare').getBoundingClientRect().height),
    footer: tenth(bar.parentElement.getBoundingClientRect().height),
    controlsRight: tenth(last.right),
    controlsBottom: tenth(last.bottom),
    build: tenth(button.width),
    row: !!t,
    below: t ? t.top >= last.bottom - 1 : null,
    textTop: t ? tenth(t.top) : null,
    textWidth: t ? tenth(t.width) : null,
    spill: tenth(Math.max(0, right - (b.right - 12))),
    summary: summary.textContent, status: status.textContent,
    summaryCut: cut(summary), statusCut: cut(status),
    summaryTitle: summary.title, statusTitle: status.title,
    errorLook: status.classList.contains('error'),
  };
})()"""


def _dismiss_offer(c: Chrome, seconds: float = 3.0) -> None:
    """Answer the offer at the top of the page, if the server has cells open ("Start empty").

    A page asks the server what it holds as it starts, and a server that holds a cell puts a banner of 47 to 52 px over
    the view until it is answered: the first page of a fresh server has none and the second has one, which would move
    the grid by itself between two pages of one check."""
    if c.wait_for("!document.getElementById('restore-banner').hidden", seconds):
        js(c, "document.getElementById('restore-no').click()")
        c.settle(2)


def _beside_from(c: Chrome, narrow: int = 1440, wide: int = 1920) -> int:
    """The narrowest window, between `narrow` (text under the controls) and `wide` (beside them), at which the toolbar
    puts the text beside the controls: found by halving, on the page as it stands."""
    while wide - narrow > 1:
        middle = (narrow + wide) // 2
        c.emulate('mac', middle, 900)
        c.settle(2)
        if js(c, TOOLBAR_PROBE)['below']:
            narrow = middle
        else:
            wide = middle
    return wide


def comparetoolbar(c: Chrome, r: Report, ctx):
    r.start('compare: the text under the grid does not resize it')
    samples: dict[tuple[int, str], dict] = {}
    widths = list(TOOLBAR_WIDTHS)

    def sweep(state: str) -> None:
        """Measure the toolbar and the grid at every width, in both themes, with the page as it stands."""
        for width in widths:
            for scheme in ('dark', 'light'):
                c.emulate('mac', width, 900, scheme)
                c.settle(3)
                samples[(width, f'{state}, {scheme}')] = js(c, TOOLBAR_PROBE)

    # -- a Compare that works: idle, a note, running with the clock at four readings, done ----------------
    boot(c, ctx.url)
    _dismiss_offer(c)
    load(c, ctx.cell, mesh=False)
    c.emulate('mac', 1440, 900)
    _compare_tab(c, 4)
    js(c, f"{COMPARE}.onStatus('')")
    # The two windows either side of the width where the text moves under the controls: the one place where a control
    # that changes its width with the state ("Compare" becomes "Stop all") could swap the two layouts as a Compare starts.
    edge = _beside_from(c)
    widths += [edge - 1, edge]
    sweep('idle')
    js(c, f"{COMPARE}.onStatus('Panes 5 and 6 are new: press Compare to fill them.')")
    sweep('note')
    js(c, f"{COMPARE}.onStatus('')")
    js(c, '__hold()')
    _click_js(c, "document.getElementById('compare-build')")
    c.wait_for(f'{COMPARE}.panes.some((p) => p.layers.some((l) => l.state === "building"))', 60)
    c.settle(3)
    for label, seconds in (('running 3 s', 3), ('running 41 s', 41), ('running 1 min 05 s', 65), ('running 12 min 05 s', 725)):
        # The run's clock is put back so that the summary reads this long; the page's own ticker goes on from there.
        js(c, f'{COMPARE}._runStartedAt = Date.now() - {seconds * 1000 - 100}; {COMPARE}._renderSummary()')
        sweep(label)
    js(c, '__release()')
    c.wait_for(f'!{COMPARE}.running', 600)
    c.settle(3)
    sweep('done')

    # -- a Compare that fails in every pane, and a message far longer than the toolbar -------------------
    boot(c, ctx.url)
    _dismiss_offer(c)
    load(c, ctx.cell, mesh=False)
    c.emulate('mac', 1440, 900)
    _compare_tab(c, 4)
    js(c, """(() => { window.sbfsem.api.mesh = async () => {
        throw Object.assign(new Error('out of memory'), { serverDetail: 'out of memory', failedStage: 'isotropic remesh' }); }; })()""")
    _click_js(c, "document.getElementById('compare-build')")
    c.wait_for(f'!{COMPARE}.running', 600)
    c.settle(3)
    sweep('failed')
    js(c, f"""{COMPARE}.onStatus('Pane 2: ' + 'the server could not build this surface, and said why at some length. '.repeat(6), 'error')""")
    sweep('failed, long message')

    # -- the grid is one height ------------------------------------------------------------------------
    states = list(dict.fromkeys(state for _, state in samples))
    moved = {}
    for width in widths:
        groups: dict[tuple, list[str]] = {}
        for state in states:
            s = samples[(width, state)]
            groups.setdefault((s['grid'], s['render'], s['view']), []).append(state)
        if any(max(g[k] for g in groups) - min(g[k] for g in groups) > 0.05 for k in range(3)):
            moved[width] = {f'grid {g}, render {rd}, view {v}': names for (g, rd, v), names in groups.items()}
    r.check('the grid, and so every pane\'s render, is one height at 480, 700, 900, 1000, 1140, 1440 and 1920 px whether the toolbar '
            'is idle, has a note, is running (the clock at 3 s, 41 s, 1 min 05 s and 12 min 05 s), is done, or has failed '
            'with a long message, in both themes', not moved, moved)

    # -- where the text is --------------------------------------------------------------------------------
    plain = {w: samples[(w, 'idle, dark')] for w in widths}
    r.check('the text is a row of its own, under the controls, on a window narrower than about 1650 px, and beside the '
            'controls on a wider one, which pays nothing for it (the layouts change between 1500 and 1800 px)',
            all(plain[w]['below'] is True for w in widths if w < edge) and plain[1920]['below'] is False
            and 1500 <= edge <= 1800, {'beside from': edge, 'under the controls': {w: plain[w]['below'] for w in widths}})
    r.check('the row is there when it is empty, as high as when it has words in it (the bar is one height in every state)',
            all(len({samples[(w, s)]['bar'] for s in states}) == 1 for w in widths),
            {w: sorted({samples[(w, s)]['bar'] for s in states}) for w in widths})
    r.check('the controls stay where they are when the text changes ("Compare" is as wide as "Stop all"), so a window on the edge '
            'of the two layouts does not swap between them as a Compare starts',
            all(len({(samples[(w, s)]['controlsRight'], samples[(w, s)]['controlsBottom'], samples[(w, s)]['build'])
                     for s in states}) == 1 for w in widths),
            {w: sorted({(samples[(w, s)]['controlsRight'], samples[(w, s)]['build']) for s in states}) for w in widths})
    spill = {k: v['spill'] for k, v in samples.items() if v['spill'] > 0.5}
    r.check('no text runs past the toolbar\'s right edge', not spill, spill)

    # -- what can be read ---------------------------------------------------------------------------------
    # At 900 px the row is 570 px: a run of three panes at once is a summary of about 520, and it keeps its words,
    # the clock at their end among them; the status line gives way to it.  Where both are short they both read.
    cut = {s: [samples[(900, s)]['summary'], samples[(900, s)]['summaryCut'], samples[(900, s)]['status'], samples[(900, s)]['statusCut']]
           for s in ('note, dark', 'running 41 s, dark', 'running 12 min 05 s, dark', 'done, dark')}
    r.check('at 900 px a running summary reads in full, its clock too, and so do the status after a Compare and a note',
            not samples[(900, 'running 41 s, dark')]['summaryCut'] and not samples[(900, 'running 12 min 05 s, dark')]['summaryCut']
            and not samples[(900, 'done, dark')]['summaryCut'] and not samples[(900, 'done, dark')]['statusCut']
            and not samples[(900, 'note, dark')]['statusCut'], cut)
    r.check('and at 1140 px, with the summary of a run of three panes, the status line beside it reads in full as well',
            not samples[(1140, 'running 41 s, dark')]['summaryCut'] and not samples[(1140, 'running 41 s, dark')]['statusCut'],
            {k: samples[(1140, 'running 41 s, dark')][k] for k in ('summary', 'summaryCut', 'status', 'statusCut', 'textWidth')})
    unsaid = {k: [v['summary'], v['summaryTitle'], v['status'], v['statusTitle']] for k, v in samples.items()
              if v['summaryTitle'] != v['summary'] or v['statusTitle'] != v['status']}
    r.check('the whole of a text that can be cut short is on hover (its title is its text)', not unsaid, dict(list(unsaid.items())[:3]))
    long = samples[(1920, 'failed, long message, dark')]
    r.check('the failure keeps its look, and a message longer than the row is cut short at 1920 px too',
            long['errorLook'] and long['statusCut'] and long['spill'] <= 0.5, long)
    c.emulate('mac', 1440, 900)


# -- help movies ------------------------------------------------------------------------------

def _hover_field(c: Chrome, key: str):
    """Rest the pointer on a render option's label text, after any scroll has finished."""
    sel = f'#panel-render label[data-key="{key}"]'
    c.ev("document.querySelector(%s)?.scrollIntoView({ block: 'center' })" % json.dumps(sel))
    time.sleep(0.6)
    spot = c.ev("""(() => { const e = document.querySelector(%s); if (!e || e.hidden) return null;
      const r = e.getClientRects()[0]; return r ? [r.left + 6, r.top + r.height / 2] : null; })()"""
                % json.dumps(sel))
    if spot:
        c.move(spot[0] - 3, spot[1])
        c.move(spot[0], spot[1])
    return spot


def _movie_state(c: Chrome):
    return js(c, """(() => { const m = document.querySelector('.help-pop .help-movie'); if (!m) return null;
      const frames = [...m.querySelectorAll('.hm-stage .hm-frame')];
      return { frames: frames.length, image: frames[0]?.style.backgroundImage || '',
               ticks: [...m.querySelectorAll('.hm-tick')].map((t) => t.textContent),
               now: [...m.querySelectorAll('.hm-tick')].findIndex((t) => t.classList.contains('now')),
               value: m.querySelector('.hm-value')?.textContent || '',
               numbers: m.querySelector('.hm-numbers')?.textContent || '',
               view: m.querySelector('.hm-view')?.textContent || '',
               strip: m.querySelectorAll('.hm-thumb').length,
               above: m.compareDocumentPosition(m.parentElement.lastChild) & Node.DOCUMENT_POSITION_FOLLOWING }; })()""")


def helpmovie(c: Chrome, r: Report, ctx):
    r.start('helpmovie: a render option\'s help plays real renders at a sweep of its values')
    boot(c, ctx.url)
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    ok = c.wait_for("(() => { try { return !!window.sbfsem && document.querySelector('label[data-key=\"voxel_smooth_um\"]') "
                    "&& !document.querySelector('label[data-key=\"voxel_smooth_um\"]').hidden; } catch (e) { return false; } })()", 10)
    manifest = js(c, "fetch('/static/help/movies.json').then((r) => r.ok ? r.json() : null)", wait=True)
    r.check('the movies\' manifest is served', bool(manifest), type(manifest).__name__)
    if not ok or not manifest or 'voxel_smooth_um' not in manifest:
        r.skip('no Envelope σ movie to hover', 'the panel or the manifest is missing')
        return
    time.sleep(2.8)                   # the manifest is fetched 2.5 s after boot
    _hover_field(c, 'voxel_smooth_um')
    c.wait_for("!!document.querySelector('.help-pop .help-movie')", 4)
    first = _movie_state(c)
    spec = manifest['voxel_smooth_um']
    r.check('hovering Envelope σ shows a movie above the help words, two layers to cross-fade',
            bool(first and first['frames'] == 2 and first['above']), first)
    r.check('its frames are the shipped WebP',
            bool(first and 'voxel_smooth_um.webp' in first['image']), (first or {}).get('image'))
    served = js(c, """fetch('/static/help/' + %s).then((r) => [r.status, r.headers.get('content-type')])"""
                % json.dumps(spec['image']), wait=True)
    r.check('the image is served as WebP', served == [200, 'image/webp'], served)
    r.check('the scale names every value, and marks the one set now (0.5)',
            bool(first and first['ticks'] == spec['labels'] and spec['labels'][first['now']] == '0.5'), first)
    r.check('the frame on screen says its value and what its build measured',
            bool(first and first['value'].startswith('Envelope σ ') and 'triangles' in first['numbers']
                 and 'from the annotations' in first['numbers']), first)
    time.sleep(1.9)
    later = _movie_state(c)
    r.check('it plays: the next frame shows by itself',
            bool(later and later['value'] != first['value']), [first and first['value'], later and later['value']])
    c.move(5, 5)
    time.sleep(0.4)
    r.check('the pointer leaving puts it away', not js(c, "!!document.querySelector('.help-pop')"))

    # The method's own movie: Disc avg under Closed curve, the general one under Cylinder.
    if 'curve_avg_discs@closedcurve' in manifest and 'curve_avg_discs' in manifest:
        for method, want in (('closedcurve', manifest['curve_avg_discs@closedcurve']['view']),
                             ('cylinder', manifest['curve_avg_discs']['view'])):
            js(c, """(() => { const m = document.getElementById('render-method'); m.value = %s;
                m.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(method))
            c.settle(2)
            _hover_field(c, 'curve_avg_discs')
            c.wait_for("!!document.querySelector('.help-pop .help-movie')", 4)
            got = _movie_state(c)
            r.check(f'under {method}, Disc avg shows its {method} movie',
                    bool(got and got['view'].startswith(want)), (got or {}).get('view'))
            c.move(5, 5)
            time.sleep(0.4)

    # Reduced motion: the frames laid out still.
    c.send('Emulation.setEmulatedMedia', features=[{'name': 'prefers-reduced-motion', 'value': 'reduce'},
                                                   {'name': 'prefers-color-scheme', 'value': 'dark'}])
    js(c, """(() => { const m = document.getElementById('render-method'); m.value = 'cylinder';
        m.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    c.settle(2)
    _hover_field(c, 'voxel_smooth_um')
    c.wait_for("!!document.querySelector('.help-pop .help-movie')", 4)
    still = _movie_state(c)
    r.check('with reduced motion the frames are a still strip',
            bool(still and still['frames'] == 0 and still['strip'] == spec['frames']), still)
    c.move(5, 5)
    c.send('Emulation.setEmulatedMedia', features=[{'name': 'prefers-color-scheme', 'value': 'dark'}])


def settings(c: Chrome, r: Report, ctx):
    r.start('settings: the debug tab')
    boot(c, ctx.url)
    _dismiss_offer(c)
    where = js(c, """(() => {
        const tabs = [...document.querySelectorAll('.tabs .tab')];
        const mine = tabs.find((t) => t.dataset.view === 'settings');
        const before = tabs[tabs.length - 2].getBoundingClientRect();
        const box = mine.getBoundingClientRect(), row = mine.parentElement.getBoundingClientRect();
        return { last: tabs[tabs.length - 1] === mine, apart: Math.round(box.left - before.right),
                 fromEnd: Math.round(row.right - box.right) }; })()""")
    r.check('Settings is the last tab, set apart at the far end of the row',
            bool(where['last'] and where['apart'] > 40 and where['fromEnd'] < 24), where)
    js(c, "document.querySelector('[data-view=settings]').click()")
    shown = c.wait_for("document.querySelectorAll('#settings-panel .card').length >= 4"
                       " && !!document.querySelector('.settings-server table')", 15)
    r.check('it shows its four cards, the server\'s with its tools (an open server: everyone is an admin)', shown)
    state = js(c, "document.querySelector('#settings-panel p[data-state]')?.dataset.state")
    r.check('the page is the build the server serves (on a freshly started server)', state == 'same', state)
    button = "[...document.querySelectorAll('#settings-panel button')].find((b) => b.textContent === %s)"
    restart = js(c, (button % "'Restart the server'") + "?.disabled")
    r.check('Restart is not offered by a server systemd did not start', restart is True, restart)

    js(c, "window.confirm = () => true; " + (button % "'Clear surfaces in memory'") + ".click()")
    cleared = c.wait_for("/^Cleared \\d+ surface/.test("
                         "document.querySelector('.settings-server p[role=status]')?.textContent || '')", 10)
    r.check('clearing surfaces in memory says what it cleared', cleared,
            js(c, "document.querySelector('.settings-server p[role=status]')?.textContent"))

    # The reloads below must keep this page's storage, as a person's would.
    js(c, "sessionStorage.setItem('__keep', '1'); window.__mark = 1; " + (button % "'Clear cached files and reload'") + ".click()")
    back = c.wait_for("window.__mark === undefined && !!document.querySelector('.settings-note')", 20)
    js(c, HELPERS)
    r.check('Clear cached files reloads and comes back to Settings with its note', back and js(
        c, "document.querySelector('.tab.active').dataset.view === 'settings'"),
        js(c, "document.querySelector('.settings-note')?.textContent"))

    js(c, """(() => { localStorage.setItem('sbfsem:swatches', '["#123456"]');
        localStorage.setItem('sbfsem.session.v1.tab.check-app', JSON.stringify(
          { schema: 1, saved_at: new Date().toISOString(), neurons: [{ id: 943, source: 'i' }] }));
        localStorage.setItem('sbfsem:sidebar-width', '333'); })()""")
    js(c, "document.querySelector('[data-view=render]').click(); document.querySelector('[data-view=settings]').click()")
    c.wait_for("!!document.querySelector(\"#settings-panel input[aria-label='Reset Saved colors']\")", 10)
    # What the page saves after the list on screen was made (it saves just after the tab opens, and on
    # every change) goes too: a slot written now stands for it.
    js(c, """localStorage.setItem('sbfsem.session.v1.tab.late', JSON.stringify(
        { schema: 1, saved_at: new Date().toISOString(), neurons: [{ id: 1178, source: 'i' }] }))""")
    js(c, """(() => { window.confirm = () => true; window.__mark = 2;
        for (const label of ['Reset Saved sessions', 'Reset Saved colors']) {
          document.querySelector(`#settings-panel input[aria-label='${label}']`).click(); }
        [...document.querySelectorAll('#settings-panel button')]
          .find((b) => b.textContent.startsWith('Reset the ticked')).click(); })()""")
    c.wait_for("window.__mark === undefined && !!document.querySelector('.settings-note')", 20)
    left = js(c, """(() => ({ keys: Object.keys(localStorage).filter((k) => k.startsWith('sbfsem')),
        cells: Object.keys(localStorage).filter((k) => k.startsWith('sbfsem.session'))
          .reduce((n, k) => n + ((JSON.parse(localStorage.getItem(k)) || {}).neurons || []).length, 0),
        note: document.querySelector('.settings-note')?.textContent || '' }))()""")
    r.check('a reset removes what was ticked, the saved cells included, and keeps the rest',
            'sbfsem:swatches' not in left['keys'] and 'sbfsem.session.v1.tab.check-app' not in left['keys']
            and 'sbfsem.session.v1.tab.late' not in left['keys']
            and left['cells'] == 0 and 'sbfsem:sidebar-width' in left['keys'] and 'removed' in left['note'], left)
    js(c, "sessionStorage.removeItem('__keep')")


def visualizations(c: Chrome, r: Report, ctx):
    """The Visualizations menu (2026-10-19): the open row's Look dropdown, the right-click submenu
    with its "apply to all cells" follow-up, Save, and the manager's file and delete."""
    r.start('visualizations: a cell\'s look at one pick, saved, shared and applied to all')
    boot(c, ctx.url)
    _dismiss_offer(c)
    first = load(c, ctx.cell, source=ctx.source)
    second = load(c, 1178, source='NeitzInferiorMonkey')
    js(c, """(() => { const li = [...document.querySelectorAll('.neuron-item')].find((x) => x.dataset.key === %s);
        if (!li.classList.contains('expanded')) li.querySelector('.neuron-head').click(); })()""" % json.dumps(first))
    c.settle(2)
    row = js(c, """(() => { const s = document.querySelector('.neuron-item.expanded .vis-select'); if (!s) return null;
        return { shown: s.selectedOptions[0].textContent, n: s.options.length,
                 groups: [...s.querySelectorAll('optgroup')].map((g) => g.label),
                 sub: document.querySelector('.neuron-item.expanded .neuron-sub').title }; })()""")
    r.check('an open row\'s second line is the Look dropdown: App default for a new cell, the lab\'s looks, More',
            bool(row) and row['shown'] == 'App default' and row['n'] >= 12 and 'More' in row['groups']
            and 'Inferior' in row['sub'], row)
    js(c, """(() => { const s = document.querySelector('.neuron-item.expanded .vis-select');
        s.value = 'crystal-skeleton-rings'; s.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    drawn = c.wait_for("/is drawn as/.test(document.getElementById('status').textContent)", 120)
    look = js(c, """(() => { const rec = window.sbfsem.state.neurons.get(%s); const R = window.sbfsem.renderer;
        return { surface: rec.display.showSurface, skeleton: R.skeletons.has(rec.key), rings: R.discs.has(rec.key),
                 color: rec.display.discColor, method: rec.render.method,
                 shown: document.querySelector('.neuron-item.expanded .vis-select').selectedOptions[0].textContent }; })()"""
              % json.dumps(first))
    r.check('choosing Crystal\'s look draws the skeleton and yellow rings with the surface off, and the row names it',
            drawn and bool(look) and not look['surface'] and look['skeleton'] and look['rings']
            and look['color'] == '#edec4f' and look['method'] == 'hybrid' and look['shown'].startswith('Crystal'), look)
    # The right-click menu, on the other cell's row.
    js(c, """(() => { const head = [...document.querySelectorAll('.neuron-item')].find((x) => x.dataset.key === %s)
        .querySelector('.neuron-head'); const b = head.getBoundingClientRect();
        head.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true,
          clientX: b.left + 60, clientY: b.top + 8 })); })()""" % json.dumps(second))
    c.settle(1)
    items = js(c, "[...document.querySelectorAll('#ctx-menu button')].map((b) => b.textContent.trim())") or []
    r.check('the right-click menu lists the same looks', 'Emphasis · Skeleton' in items
            and "Save this cell's look as…" in items, items[:16])
    js(c, """[...document.querySelectorAll('#ctx-menu button')].find((b) => b.textContent.trim() === 'Emphasis · Skeleton').click()""")
    c.settle(1)
    offer = js(c, "document.querySelector('.vis-offer')?.textContent")
    r.check('choosing one from it offers to apply it to all cells', offer == 'Apply "Emphasis · Skeleton" to all 2 cells', offer)
    c.wait_for("/is drawn as/.test(document.getElementById('status').textContent)", 120)
    js(c, "document.getElementById('viewport').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))")
    c.settle(1)
    widths = js(c, "[...window.sbfsem.state.neurons.values()].map((r) => [r.id, r.display.skeletonWidth, r.display.showSurface])")
    width = {w[0]: w[1] for w in widths or []}
    r.check('a click anywhere else takes the offer away, and only that cell has the look',
            js(c, "!document.querySelector('.vis-offer')") and width.get(1178) == 0.15 and width.get(ctx.cell) == 0, widths)
    js(c, """(() => { const head = [...document.querySelectorAll('.neuron-item')].find((x) => x.dataset.key === %s)
        .querySelector('.neuron-head'); const b = head.getBoundingClientRect();
        head.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: b.left + 60, clientY: b.top + 8 }));
        [...document.querySelectorAll('#ctx-menu button')].find((x) => x.textContent.trim() === 'Emphasis · Annotations').click();
        setTimeout(() => document.querySelector('.vis-offer button').click(), 50); })()""" % json.dumps(second))
    allof = c.wait_for("/2 cells are drawn as/.test(document.getElementById('status').textContent)", 180)
    rings = js(c, "[...window.sbfsem.state.neurons.values()].map((r) => [r.display.showDiscs, r.display.discOpacity, r.opacity])")
    r.check('its button applies the look to every cell', allof and rings == [[True, 1, 0.12], [True, 1, 0.12]], rings)
    # Save, the manager's file and delete.
    js(c, """(() => { window.prompt = () => 'Check look'; window.confirm = () => true;
        const s = document.querySelector('.neuron-item.expanded .vis-select');
        s.value = '@save'; s.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    c.settle(1)
    kept = js(c, "JSON.parse(localStorage.getItem('sbfsem.visualizations.v1') || '[]').map((v) => v.name)")
    groups = js(c, "[...document.querySelectorAll('.neuron-item.expanded .vis-select optgroup')].map((g) => [g.label, g.children.length])")
    r.check('Save this cell\'s look keeps it in this browser, in every cell\'s list',
            kept == ['Check look'] and ['Saved in this browser', 1] in groups, [kept, groups])
    file = js(c, """(async () => { const s = document.querySelector('.neuron-item.expanded .vis-select');
        s.value = '@manage'; s.dispatchEvent(new Event('change', { bubbles: true }));
        await new Promise((r) => setTimeout(r, 200)); const m = document.querySelector('.vis-manager');
        [...m.querySelectorAll('.vis-manager-row')].find((x) => x.textContent.includes('Check look')).querySelector('input').click();
        let blob = null; const make = URL.createObjectURL; URL.createObjectURL = (b) => { blob = b; return make(b); };
        [...m.querySelectorAll('.vis-manager-actions button')].find((b) => b.textContent.startsWith('Save')).click();
        URL.createObjectURL = make; const text = blob ? await blob.text() : null;
        [...m.querySelectorAll('.vis-manager-actions button')].find((b) => b.textContent.startsWith('Delete')).click();
        return text; })()""", wait=True)
    doc = json.loads(file) if file else {}
    r.check('the manager saves it to a file that says what the look is (only what differs from the defaults)',
            doc.get('kind') == 'sbfsem-visualizations' and doc['visualizations'][0]['name'] == 'Check look'
            and doc['visualizations'][0]['display'].get('discOpacity') == 1, doc)
    r.check('and deletes it from this browser', js(c, "localStorage.getItem('sbfsem.visualizations.v1')") == '[]')


# -- locate ------------------------------------------------------------------------

#: The selection, and whether its yellow ring is drawn: `{ id, kind, drawn }`, or null.
SELECTED = """(() => { const r = window.sbfsem.renderer; const s = r.selection; if (!s) return null;
    const own = r.highlightGroup.children.find((o) => o.userData.slot === 'primary' && !o.userData.extra);
    return { id: s.locationId, kind: s.kind, drawn: !!own && own.children.length > 0,
             color: own?.children[0]?.material.color.getStyle() ?? null, yellow: r.markerColor('primary') }; })()"""


def locate(c: Chrome, r: Report, ctx):
    r.start('locate: /open?location= and a typed Location ID select the annotation')
    boot(c, ctx.url)
    key = load(c, ctx.cell)
    ids = js(c, """(async () => { const nodes = await fetch('/api/neurons/' + %s + '/nodes?cell_only=true')
        .then((x) => x.json());
      const ringed = nodes.filter((n) => n.radius_um > 0.1).sort((a, b) => a.z_section - b.z_section);
      return [ringed[Math.floor(ringed.length / 3)].id, ringed[Math.floor(2 * ringed.length / 3)].id]; })()"""
                % json.dumps(key), wait=True)
    # As Viking's context menu opens it: a fresh page on the link.
    boot(c, ctx.url, f'/open?volume={ctx.source}&cells={ctx.cell}&location={ids[0]}')
    got = c.wait_for('window.sbfsem.renderer && window.sbfsem.renderer.selection '
                     '&& window.sbfsem.renderer.selection.locationId === %d' % ids[0], 120)
    sel = js(c, SELECTED)
    r.check('the link\'s annotation is selected and drawn yellow',
            got and sel and sel['kind'] == 'annotation' and sel['drawn'] and sel['color'] == sel['yellow'], sel)
    c.wait_for(f"window.sbfsem.renderer.meshes.has({json.dumps(key)})", 120)
    time.sleep(3)
    sel = js(c, SELECTED)
    r.check('and still is once the cell has finished drawing', bool(sel) and sel['id'] == ids[0] and sel['drawn'], sel)
    field = js(c, "document.getElementById('pick-location').textContent")
    r.check('the readout names it, with its volume', f'Location ID {ids[0]} (' in field, field)

    # Typed into the readout's Location ID (right-click it), Enter.
    def typed(location_id: int):
        js(c, """document.getElementById('pick-location').dispatchEvent(
            new MouseEvent('contextmenu', { bubbles: true, cancelable: true })); true""")
        js(c, """(() => { const i = document.getElementById('pick-input'); i.value = '%d';
            i.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); return true; })()"""
           % location_id)
        return c.wait_for('window.sbfsem.renderer.selection && window.sbfsem.renderer.selection.locationId === %d'
                          % location_id, 30)

    got = typed(ids[1])
    sel = js(c, SELECTED)
    r.check('a typed Location ID selects that annotation, drawn yellow',
            got and sel['drawn'] and sel['color'] == sel['yellow'], sel)
    status = js(c, "document.getElementById('status').textContent")
    r.check('the status line says what is selected', status.startswith(f'Selected: Annotation {ids[1]} '), status)

    # The cell's annotations hidden: a ring at the annotation's radius, still.
    js(c, """(() => { const box = [...document.querySelectorAll('.cell-panel label.check')]
        .find((l) => l.textContent.trim() === 'Show annotations')?.querySelector('input');
      if (box && box.checked) box.click(); return true; })()""")
    c.wait_for(f'!window.sbfsem.renderer.discs.has({json.dumps(key)})', 10)
    got = typed(ids[0])
    sel = js(c, SELECTED)
    r.check('with the cell\'s annotations hidden it is drawn all the same',
            got and js(c, f'!window.sbfsem.renderer.discs.has({json.dumps(key)})') and sel['drawn'], sel)



# -- viewcube ---------------------------------------------------------------------------

def viewcube(c: Chrome, r: Report, ctx):
    r.start('viewcube: the cube in the corner turns the view')
    renderer = 'window.sbfsem.renderer'
    boot(c, ctx.url)
    load(c, ctx.cell)
    shown = js(c, "(() => { const v = document.querySelector('.viewcube'); return !!v && !v.hidden; })()")
    r.check('the cube is shown by default', shown)
    inset = js(c, "getComputedStyle(document.querySelector('.viewport-overlay')).right")
    r.check('the overlay row leaves it the corner', inset == '152px', inset)

    def at(region: list[int]) -> tuple[float, float]:
        """Where a part of the cube is drawn now, in page pixels (the module's own projection).

        Aimed at 0.8 of the way out to the part, inside its zone: a part's own point can lie on
        the cube's outline (a corner seen from an edge view), where the pointer grazes the cube."""
        return tuple(js(c, """(async () => {
            const m = await import('/static/js/viewcube.js');
            const b = document.querySelector('.vc-stage').getBoundingClientRect();
            const [x, y] = m.project(%s.viewFrame(), %s);
            return [b.left + b.width / 2 + x, b.top + b.height / 2 + y]; })()"""
                        % (renderer, json.dumps([0.8 * v for v in region])), wait=True))

    def double(x: float, y: float) -> None:
        c.move(x, y)
        for count in (1, 2):
            c.send('Input.dispatchMouseEvent', type='mousePressed', x=x, y=y, button='left', buttons=1,
                   clickCount=count)
            c.send('Input.dispatchMouseEvent', type='mouseReleased', x=x, y=y, button='left', buttons=0,
                   clickCount=count)

    def frame() -> dict:
        return js(c, f'{renderer}.viewFrame()')

    def near(a: list[float], b: list[float], eps: float = 1e-6) -> bool:
        return all(abs(x - y) <= eps for x, y in zip(a, b, strict=True))

    def arrived(back: list[float], up: list[float], seconds: float = 3.0) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            f = frame()
            if near(f['back'], back) and near(f['up'], up):
                return True
            time.sleep(0.05)
        return False

    def still() -> dict:
        """The camera once it has stopped moving (a load's framing and glide come after the mesh)."""
        last = js(c, f'{renderer}.cameraState()')
        for _ in range(40):
            time.sleep(0.25)
            now = js(c, f'{renderer}.cameraState()')
            if same(now, last):
                return now
            last = now
        return last

    def same(a: dict, b: dict, eps: float = 1e-9) -> bool:
        return all(near(a[k], b[k], eps) for k in ('position', 'target', 'up'))

    steps = lambda: js(c, 'window.sbfsem.history().undo.length')  # noqa: E731
    h = 2 ** -0.5

    before = still()
    c.click(*at([0, 0, 1]))
    time.sleep(1.0)
    r.check('a single click on a face turns nothing', same(js(c, f'{renderer}.cameraState()'), before))

    count = steps()
    double(*at([0, 0, 1]))
    r.check('a double-click on +Z looks down Z, Y up', arrived([0, 0, 1], [0, 1, 0]), frame())
    c.wait_for('window.sbfsem.history().undo.length > %d' % count, 5)
    r.check('and is an undo step', steps() == count + 1, f'{count} -> {steps()}')
    cube = js(c, f'{renderer}.cameraState()')
    js(c, "document.querySelector('[data-viewpoint=\"xy1\"]').click()")
    c.settle(2)
    button = js(c, f'{renderer}.cameraState()')
    r.check('exactly where the XY1 button puts the camera', same(cube, button, 1e-6), (cube, button))
    r.check('the face controls are offered, face on',
            js(c, "document.querySelector('.viewcube').classList.contains('face-on')"))

    def press(which: str) -> None:
        box = js(c, """(() => { const b = document.querySelector('.vc-%s').getBoundingClientRect();
            return [b.left + b.width / 2, b.top + b.height / 2]; })()""" % which)
        c.move(*at([0, 0, 1]))       # the controls show while the pointer is on the cube
        c.settle(2)
        c.click(*box)

    pivot = js(c, f'{renderer}.controls.target.toArray()')
    press('nudge-down')
    r.check('the bottom triangle brings −Y round: XZ1', arrived([0, -1, 0], [0, 0, 1]), frame())
    press('roll-cw')
    r.check('the clockwise arrow turns the picture a quarter (+X, on the right, goes down)',
            arrived([0, -1, 0], [-1, 0, 0]), frame())
    press('roll-ccw')
    r.check('and the other arrow turns it back', arrived([0, -1, 0], [0, 0, 1]), frame())
    for which in ('nudge-left', 'nudge-left', 'nudge-left', 'nudge-left', 'nudge-up'):
        press(which)
        c.wait_for(f'!{renderer}._endFlight', 3)
    r.check('four turns round the sides and one up: back on XY1', arrived([0, 0, 1], [0, 1, 0]), frame())
    after = js(c, f'{renderer}.controls.target.toArray()')
    r.check('every quarter turn kept the pivot (no creep)', near(after, pivot, 1e-6), (pivot, after))

    # From XY1 the edges round the top face are in sight (the upright ones are end on).
    double(*at([1, 0, 1]))
    r.check('an edge is the 45 degree view between its faces, Z as upright as it goes',
            arrived([h, 0, h], [-h, 0, h]), frame())
    js(c, "document.querySelector('[data-viewpoint=\"xz1\"]').click()")
    c.settle(2)
    double(*at([1, -1, 0]))
    r.check('an upright edge, from XZ1: Z up', arrived([h, -h, 0], [0, 0, 1]), frame())
    s3 = 3 ** -0.5
    double(*at([1, -1, 1]))
    corner_up = [-1 / 6 ** 0.5, 1 / 6 ** 0.5, 2 / 6 ** 0.5]
    r.check('a corner is the view between three faces', arrived([s3, -s3, s3], corner_up), frame())
    r.check('not face on, no face controls',
            not js(c, "document.querySelector('.viewcube').classList.contains('face-on')"))

    # Straight after the turn: a drag begun within 350 ms of one used to be caught halfway by a
    # second history step of the turn's.
    count = steps()
    start = frame()['back']
    x, y = at([1, -1, 1])
    c.press(x, y)
    for i in range(1, 21):
        c.move(x - i * 2, y, buttons=1)
        time.sleep(0.02)
    c.release(x - 40, y)
    c.wait_for(f'!{renderer}._spin.yaw && !{renderer}._spin.endPending', 10)     # the glide
    turned = math.degrees(math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(
        frame()['back'], start, strict=True))))))
    rate = js(c, "import('/static/js/viewcube.js').then((m) => m.DRAG_RADIANS_PER_PX)", wait=True)
    asked = math.degrees(40 * rate)
    r.check('a drag on the cube orbits the view, about a quarter turn for the cube\'s width',
            0.85 * asked < turned <= asked + 0.1, f'{turned:.1f} degrees for 40 px ({asked:.1f} at most)')
    c.wait_for('window.sbfsem.history().undo.length > %d' % count, 5)
    r.check('and is one undo step', steps() == count + 1, f'{count} -> {steps()}')
    js(c, "document.getElementById('undo-btn').click()")
    r.check('Undo puts the corner view back', arrived([s3, -s3, s3], corner_up), frame())

    c.move(*at([1, -1, 1]))
    c.settle(2)
    home = js(c, """(() => { const b = document.querySelector('.vc-home').getBoundingClientRect();
        return [b.left + b.width / 2, b.top + b.height / 2]; })()""")
    c.click(*home)
    c.settle(2)
    homed = js(c, f'{renderer}.cameraState()')
    js(c, "document.getElementById('reset-view').click()")
    c.settle(2)
    reset = js(c, f'{renderer}.cameraState()')
    r.check('the house is Reset view', same(homed, reset, 1e-6), (homed, reset))

    js(c, "document.getElementById('opt-flipz').click()")
    c.settle(3)
    top = js(c, "document.querySelector('.vc-face-pz .vc-label').textContent")
    r.check('under Flip Z the face on top is the volume\'s −Z', top == '−Z', top)
    js(c, "document.getElementById('opt-flipz').click()")
    c.settle(3)

    js(c, "document.getElementById('show-viewcube').click()")
    c.settle(2)
    hidden = js(c, "document.querySelector('.viewcube').hidden")
    inset = js(c, "getComputedStyle(document.querySelector('.viewport-overlay')).right")
    r.check('unticked, the cube goes and the overlay row has the corner back', hidden and inset == '12px', inset)
    time.sleep(1.0)
    boot(c, ctx.url, keep=True)
    c.wait_for("!document.getElementById('restore-banner').hidden", 30)
    js(c, "document.getElementById('restore-yes').click()")
    c.wait_for('window.sbfsem.renderer.meshes.size === 1', 120)
    c.settle(2)
    r.check('and stays hidden after a reload and Restore',
            js(c, "!document.getElementById('show-viewcube').checked && document.querySelector('.viewcube').hidden"))


SECTIONS = {
    'notice': notice, 'waiting': waiting, 'draws': draws, 'undo': undo, 'idle': idle,
    'picker': picker, 'save': save, 'analysis': analysis, 'strata': strata, 'twod': twod, 'sheets': sheets, 'volume': volume,
    'hold': hold,
    'orbit': orbit,
    'keys': keys,
    'viking': viking,
    'voxel': voxel,
    'race': race,
    'fine': fine,
    'conflicts': conflicts,
    'compareconflicts': compareconflicts,
    'compare': compare, 'comparekeys': comparekeys, 'comparestate': comparestate,
    'comparetoolbar': comparetoolbar,
    'helpmovie': helpmovie,
    'settings': settings,
    'visualizations': visualizations,
    'locate': locate,
    'viewcube': viewcube,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8765', help='the dev server')
    parser.add_argument('--chrome', help="Chrome for Testing, if not in Playwright's cache")
    parser.add_argument('--cell', type=int, default=943, help='a small cell of --source')
    parser.add_argument('--second', type=int, default=21768, help='another cell of --source')
    parser.add_argument('--third', type=int, default=10912, help='a third cell of --source')
    parser.add_argument('--source', default='NeitzInferiorMonkey')
    parser.add_argument('--other', type=int, default=410, help='a cell of --other-source')
    parser.add_argument('--other-source', default='RC1')
    parser.add_argument('--big', type=int, default=2628,
                        help='a large traced cell of --big-source, too big for a fine Voxel size')
    parser.add_argument('--big-source', default='RPC1')
    parser.add_argument('--only', help='comma list of: ' + ', '.join(SECTIONS))
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    wanted = [s for s in SECTIONS if s in set((args.only or ','.join(SECTIONS)).split(','))]
    report = Report(args.verbose)
    chrome = find_chrome(args.chrome)
    # "Always allow" for the server's origin, as a person who has ticked it has: with the question
    # open, headless Chrome on macOS takes no more input (the Viking section needs a real click).
    origin = '{0.scheme}://{0.netloc}'.format(urllib.parse.urlsplit(args.url))
    page = Chrome(chrome, 1440, 900, preferences={'protocol_handler': {
        'allowed_origin_protocol_pairs': {origin: {'viking': True, 'viking-test': True}}}})
    try:
        for name in wanted:
            try:
                SECTIONS[name](page, report, args)
            except Exception as error:  # noqa: BLE001 - a script error is a failed check, the rest still run
                report.check(f'the {name} section ran to its end', False,
                             f'{type(error).__name__}: {str(error)[:400]}')
            finally:        # the next section starts at the size every one has always had
                with suppress(Exception):
                    page.emulate('mac', 1440, 900)
        report.start('the page')
        thrown = [e['params']['exceptionDetails'].get('text', '') for e in page.events
                  if e.get('method') == 'Runtime.exceptionThrown']
        report.check('nothing was thrown', not thrown, thrown[:3])
    finally:
        page.close()
    return report.summary()


if __name__ == '__main__':
    sys.exit(main())
