"""Scripted browser check of the Closed curve averaging controls and their effect on a surface.

    .venv/bin/python tools/check_curve_averaging.py                       # the dev server on :8765
    .venv/bin/python tools/check_curve_averaging.py --url http://127.0.0.1:8811 --cell 16137 -v
    .venv/bin/python tools/check_curve_averaging.py --only controls,compare
    .venv/bin/python tools/check_curve_averaging.py --shots /tmp/staircase      # the before/after pictures

It drives a headless Chrome for Testing over the DevTools protocol with real mouse and key
events (the client and the report are ``tools/check_dropdowns.py``'s), in a throwaway
profile.  It is outside the pytest gate: it needs a running server that can load the cell
(``SBFSEM_OFFLINE=1`` and the cells cached under ``~/.cache/sbfsem`` do: Inferior c16137,
a cell traced with discs alone, and c1178, whose soma is traced and whose dendrites are
discs).  The pure half is covered by ``tests/js/test_renderoptions.mjs``, which the gate does
run; what needs the page itself is checked here.

* ``controls`` -- the two averaging controls exist in the 3D render panel with the ids the
  schema gives them, start at 0, carry their bounds and a help text, and show where they act:
  both under Closed curve and Hybrid, the disc strength alone under Cylinder and Tube (it
  averages the discs their tubes run through, 2.7.0).
* ``compare`` -- Render compare's per-pane options panel has them too, with no element id (it
  lives beside the main panel's controls), and follows the pane's technique.
* ``build`` -- the Closed curve technique on a cell traced with discs: the cell's request
  carries the strengths (0 until they are typed), a strength typed into the box and applied
  makes the next request carry it, and the surface that comes back is another surface.
* ``shots`` (with ``--shots DIR``) -- a thick dendrite at the zoom the staircase was reported
  at, the same cell and camera, averaging off and on, in the dark theme and the light one.
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
from check_app import STATE, boot, js, load, row  # noqa: E402
from check_dropdowns import Chrome, Report, find_chrome  # noqa: E402

# Records every mesh request the page makes, with the render options it was made with.
RECORD = r"""
(() => {
  const api = window.sbfsem.api;
  if (api.__recording) return;
  api.__recording = true;
  window.__requests = [];
  const original = api.mesh;
  api.mesh = (source, id, opts, extra) => {
    window.__requests.push({ id, opts: JSON.parse(JSON.stringify(opts || {})) });
    return original(source, id, opts, extra);
  };
  // Every surface the viewport draws, whether it was built or came from the page's own cache.
  window.__draws = 0;
  const renderer = window.sbfsem.renderer;
  const setMesh = renderer.setMesh.bind(renderer);
  renderer.setMesh = (key, mesh, o) => { window.__draws += 1; return setMesh(key, mesh, o); };
})();
"""

#: The ids the schema gives the two controls and their wrappers.
CONTROLS = {
    'curve_avg_discs': {'wrapper': 'curve-avg-discs-field', 'control': 'curve-avg-discs',
                        'min': '0', 'max': '3', 'step': '0.1'},
    'curve_avg_paths': {'wrapper': 'curve-avg-paths-field', 'control': 'curve-avg-paths',
                        'min': '0', 'max': '2', 'step': '0.05'},
}


def set_method(c: Chrome, method: str) -> None:
    js(c, """(() => { const s = document.getElementById('render-method'); s.value = %s;
        s.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(method))


def shown(c: Chrome, key: str) -> bool:
    return not js(c, "document.getElementById(%s).hidden" % json.dumps(CONTROLS[key]['wrapper']))


def requests(c: Chrome) -> list[dict]:
    return js(c, 'window.__requests')


def type_into(c: Chrome, element_id: str, text: str) -> None:
    """Click a number box, select what is in it and type: a real click, then real key events."""
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    c.settle(2)
    x, y, *_ = c.center('#' + element_id)
    c.click(x, y)
    js(c, "document.getElementById(%s).select()" % json.dumps(element_id))
    c.send('Input.insertText', text=text)       # what typing leaves: the box's input and change events
    c.key('Tab')


# -- controls ----------------------------------------------------------------------------

def controls(c: Chrome, r: Report, ctx):
    r.start('controls: the two strengths in the 3D render panel')
    boot(c, ctx.url)
    for key, spec in CONTROLS.items():
        got = js(c, """(() => { const box = document.getElementById(%s);
            const wrap = document.getElementById(%s);
            return box ? { value: box.value, min: box.min, max: box.max, step: box.step, type: box.type,
                           key: wrap && wrap.dataset.key, help: wrap ? wrap.dataset.help : '',
                           label: wrap ? wrap.firstChild.textContent : '' } : null; })()"""
                 % (json.dumps(spec['control']), json.dumps(spec['wrapper'])))
        r.check(f'{key}: there is a control with the schema\'s id', got is not None, got)
        if got is None:
            continue
        r.check(f'{key}: it is a number box starting at 0 with the schema\'s bounds',
                got['type'] == 'number' and float(got['value']) == 0
                and (got['min'], got['max'], got['step']) == (spec['min'], spec['max'], spec['step']), got)
        r.check(f'{key}: its wrapper names the option and carries a help text that says what it does',
                got['key'] == key and 'average' in got['help'].lower() and 'staircase' in got['help'].lower()
                and len(got['help']) > 200, got['help'][:120])
    for method, discs, paths in (('closedcurve', True, True), ('hybrid', True, True),
                                 ('cylinder', True, False), ('curvemesh', True, False)):
        set_method(c, method)
        c.settle(2)
        got = (shown(c, 'curve_avg_discs'), shown(c, 'curve_avg_paths'))
        r.check(f'{method}: the disc strength is {"shown" if discs else "hidden"} and the outline '
                f'strength {"shown" if paths else "hidden"}', got == (discs, paths), got)
    set_method(c, 'closedcurve')
    set_method(c, 'cylinder')
    inert = js(c, "document.getElementById(%s).classList.contains('inert')"
               % json.dumps(CONTROLS['curve_avg_paths']['wrapper']))
    r.check('a hidden control is never grayed as well', not inert)


# -- compare -----------------------------------------------------------------------------

def compare(c: Chrome, r: Report, ctx):
    r.start('compare: the same two strengths in a pane\'s options')
    boot(c, ctx.url)
    load(c, ctx.cell, ctx.source)
    js(c, "document.querySelector('button.tab[data-view=compare]').click()")
    c.settle(3)
    opened = js(c, """(() => { const arrow = document.querySelector('#compare-grid .compare-opts');
        if (!arrow) return false; arrow.click(); return true; })()""")
    r.check('a technique pane has an options arrow', bool(opened))
    c.wait_for("!document.getElementById('panel-compare-options').hidden", 10)
    panel = js(c, """(() => { const p = document.getElementById('panel-compare-options');
        const out = {};
        for (const key of ['curve_avg_discs', 'curve_avg_paths']) {
          const label = p.querySelector('[data-key="' + key + '"]');
          out[key] = label ? { hidden: label.hidden, ids: [label, ...label.querySelectorAll('*')].map((e) => e.id).filter(Boolean),
                               help: label.dataset.help.length > 200 } : null;
        }
        return out; })()""")
    for key in CONTROLS:
        r.check(f'{key}: the pane\'s options have it, with no element id, and a help text',
                panel[key] is not None and panel[key]['ids'] == [] and panel[key]['help'], panel[key])
    # The pane's technique decides which show: pick Closed curve in its dropdown.
    js(c, """(() => { const m = document.querySelector('#panel-compare-options [data-key="method"] select');
        m.value = 'closedcurve'; m.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    c.settle(2)
    got = js(c, """(() => { const p = document.getElementById('panel-compare-options');
        return ['curve_avg_discs', 'curve_avg_paths'].map(
          (k) => !p.querySelector('[data-key="' + k + '"]').hidden); })()""")
    r.check('under Closed curve the pane shows both', got == [True, True], got)
    js(c, """(() => { const m = document.querySelector('#panel-compare-options [data-key="method"] select');
        m.value = 'cylinder'; m.dispatchEvent(new Event('change', { bubbles: true })); })()""")
    c.settle(2)
    got = js(c, """(() => { const p = document.getElementById('panel-compare-options');
        return ['curve_avg_discs', 'curve_avg_paths'].map(
          (k) => !p.querySelector('[data-key="' + k + '"]').hidden); })()""")
    r.check('and under Cylinder the disc strength alone', got == [True, False], got)


# -- build -------------------------------------------------------------------------------

def vertex_count(c: Chrome, key: str) -> int:
    return js(c, """(() => { const group = window.sbfsem.renderer.meshes.get(%s);
        const part = group && group.children.find((m) => m.userData.part === 'surface');
        return part ? part.geometry.getAttribute('position').count : 0; })()""" % json.dumps(key))


def apply_closed_curve(c: Chrome, key: str, pixel: float) -> None:
    """Pick the Closed curve technique and the raster for the selected cell, as a person does."""
    js(c, """(() => { const s = window.sbfsem.state;
        if (!s.selection.has(%s)) %s.click(); })()""" % (json.dumps(key), row(key, '.sel')))
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    set_method(c, 'closedcurve')
    # The Voxel box takes a typed size (2026-10-16): a change on it sets what it holds.
    js(c, """(() => { const b = document.getElementById('pixel-size');
        b.value = %s; b.dispatchEvent(new Event('change', { bubbles: true })); })()"""
       % json.dumps(str(pixel)))
    c.settle(2)


def press_apply(c: Chrome, key: str) -> None:
    """Press Apply to selected and wait for the surface it asks for to be drawn."""
    drawn = js(c, 'window.__draws')
    x, y, *_ = c.center('#apply-selected')
    c.click(x, y)
    if not c.wait_for('window.__draws > %d' % drawn, 900):
        raise RuntimeError(f'no surface was drawn for {key} after Apply')
    c.settle(3)


def build(c: Chrome, r: Report, ctx):
    r.start('build: a strength typed into the box reaches the build and changes the surface')
    boot(c, ctx.url)
    js(c, RECORD)
    key = load(c, ctx.cell, ctx.source, mesh=True, seconds=600)
    js(c, RECORD)
    apply_closed_curve(c, key, ctx.pixel)
    press_apply(c, key)
    first = requests(c)[-1]['opts']
    r.check('the request carries the strengths, both 0 until they are typed',
            first.get('method') == 'closedcurve' and first.get('curve_avg_discs') == 0
            and first.get('curve_avg_paths') == 0, first)
    plain = vertex_count(c, key)
    r.check('and a surface comes back', plain > 1000, plain)
    type_into(c, 'curve-avg-discs', '1')
    shown_value = js(c, "document.getElementById('curve-avg-discs').value")
    r.check('typing 1 into Disc avg puts 1 in the box', float(shown_value) == 1.0, shown_value)
    press_apply(c, key)
    second = requests(c)[-1]['opts']
    r.check('Apply sends curve_avg_discs=1 and leaves the outline strength at 0',
            second.get('curve_avg_discs') == 1 and second.get('curve_avg_paths') == 0, second)
    averaged = vertex_count(c, key)
    r.check('the surface that came back is another one (fewer vertices: the creases are gone)',
            0 < averaged < plain, (plain, averaged))
    record = js(c, f'{STATE}.neurons.get({json.dumps(key)}).render.curve_avg_discs')
    r.check('the cell\'s own settings hold it, to be attached, saved and restored with the rest',
            record == 1, record)
    type_into(c, 'curve-avg-discs', '0')
    press_apply(c, key)
    record = js(c, f'{STATE}.neurons.get({json.dumps(key)}).render.curve_avg_discs')
    r.check('0 again is the surface as it was (the page may hold it still: no new request)',
            record == 0 and vertex_count(c, key) == plain, (record, vertex_count(c, key), plain))


# -- shots -------------------------------------------------------------------------------

def aim(c: Chrome, target: list[float], distance: float, elevation: float, azimuth: float) -> None:
    """Put the camera ``distance`` um from ``target``, ``elevation`` degrees over the section
    plane and ``azimuth`` round it (0 looks from +X), Z up, in perspective."""
    js(c, """(() => { const r = window.sbfsem.renderer; const t = %s;
        const el = %f * Math.PI / 180; const az = %f * Math.PI / 180;
        const dir = [Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el)];
        r.restoreCamera({ position: [t[0] + dir[0] * %f, t[1] + dir[1] * %f, t[2] + dir[2] * %f],
                          target: t, up: [0, 0, 1], zoom: 1, orthographic: false }); })()"""
       % (json.dumps(target), elevation, azimuth, distance, distance, distance))
    c.settle(4)


def shoot(c: Chrome, folder: Path, name: str) -> None:
    """The viewport's picture alone (its canvas), without the panels round it."""
    folder.mkdir(parents=True, exist_ok=True)
    box = js(c, """(() => { const canvas = window.sbfsem.renderer.webgl.domElement;
        window.__hidden = [...canvas.parentElement.children].filter((e) => e !== canvas);
        window.__hidden.push(...document.querySelectorAll('.viewport-overlay, .view-footer, .toast, #toast'));
        window.__hidden.forEach((e) => { e.dataset.was = e.style.visibility; e.style.visibility = 'hidden'; });
        const r = canvas.getBoundingClientRect();
        return { x: r.left, y: r.top, width: r.width, height: r.height }; })()""")
    c.settle(1)
    (folder / name).write_bytes(c.shot(clip=box))
    js(c, """(() => { (window.__hidden || []).forEach((e) => { e.style.visibility = e.dataset.was || ''; });
        window.__hidden = []; })()""")


def shots(c: Chrome, r: Report, ctx):
    if not ctx.shots:
        return
    r.start('shots: a dendrite, averaging off and on, in both themes')
    folder = Path(ctx.shots)
    boot(c, ctx.url)
    js(c, RECORD)
    key = load(c, ctx.cell, ctx.source, mesh=True, seconds=600)
    js(c, RECORD)
    apply_closed_curve(c, key, ctx.pixel)
    # Only the surface is of interest: the cell's own colour and nothing else drawn.
    for scheme in ('dark', 'light'):
        c.scheme(scheme)
        if scheme == 'light':
            js(c, "document.getElementById('toggle-display').click()")
            c.settle(1)
            js(c, """(() => { const b = document.getElementById('opt-invert'); if (!b.checked) b.click(); })()""")
            js(c, "document.getElementById('toggle-display').click()")
        for label, discs, paths in (('off', 0, 0), ('on', ctx.discs, ctx.paths)):
            type_into(c, 'curve-avg-discs', str(discs))
            if ctx.paths or paths:
                type_into(c, 'curve-avg-paths', str(paths))
            press_apply(c, key)
            for zoom, (distance, elevation) in {'user': (ctx.user_distance, 15.0),
                                                'close': (ctx.close_distance, 15.0)}.items():
                aim(c, ctx.target, distance, elevation, ctx.azimuth)
                shoot(c, folder, f'{ctx.tag}-{scheme}-{zoom}-{label}.png')
            r.check(f'{scheme} {label}: pictures written', True)


SECTIONS = {'controls': controls, 'compare': compare, 'build': build, 'shots': shots}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8765', help='the dev server')
    parser.add_argument('--chrome', help="Chrome for Testing, if not in Playwright's cache")
    parser.add_argument('--cell', type=int, default=16137, help='a cell traced with discs (Inferior Monkey)')
    parser.add_argument('--source', default='NeitzInferiorMonkey')
    parser.add_argument('--pixel', type=float, default=0.1, help='the closed-curve raster, um')
    parser.add_argument('--only', help='comma list of: ' + ', '.join(SECTIONS))
    parser.add_argument('--shots', help='write the before/after pictures into this folder')
    parser.add_argument('--tag', default='c16137', help='what the pictures are called')
    parser.add_argument('--target', type=float, nargs=3, default=[159.3, 85.5, 55.3],
                        metavar=('X', 'Y', 'Z'), help='where the camera looks, um')
    parser.add_argument('--azimuth', type=float, default=0.0, help='degrees round Z from +X')
    parser.add_argument('--user-distance', type=float, default=27.0,
                        help='camera distance of the first picture, um: the zoom of the report')
    parser.add_argument('--close-distance', type=float, default=5.0, help='and of the close-up')
    parser.add_argument('--discs', type=float, default=1.0, help='the disc strength of the "on" pictures')
    parser.add_argument('--paths', type=float, default=0.0, help='the outline strength of the "on" pictures')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    wanted = [s for s in SECTIONS if s in set((args.only or ','.join(SECTIONS)).split(','))]
    report = Report(args.verbose)
    chrome = find_chrome(args.chrome)
    page = Chrome(chrome, 1440, 900)
    try:
        page.emulate('mac', 1440, 900)
        for name in wanted:
            try:
                SECTIONS[name](page, report, args)
            except Exception as error:  # noqa: BLE001 - a script error is a failed check, the rest still run
                report.check(f'the {name} section ran to its end', False,
                             f'{type(error).__name__}: {str(error)[:400]}')
        report.start('the page')
        thrown = [e['params']['exceptionDetails'].get('text', '') for e in page.events
                  if e.get('method') == 'Runtime.exceptionThrown']
        report.check('nothing was thrown', not thrown, thrown[:3])
    finally:
        page.close()
    time.sleep(0)
    return report.summary()


if __name__ == '__main__':
    sys.exit(main())
