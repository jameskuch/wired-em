"""Scripted browser check of Closed curve's reaches: curved links, joined bodies, the two bridges.

    .venv/bin/python tools/check_curve_links.py                       # the dev server on :8765
    .venv/bin/python tools/check_curve_links.py --url http://127.0.0.1:8773 -v
    .venv/bin/python tools/check_curve_links.py --only controls,compare
    .venv/bin/python tools/check_curve_links.py --only shots --shots /tmp/links    # before/after pictures

It drives a headless Chrome for Testing over the DevTools protocol with real mouse and key
events, in a throwaway profile, with ``tools/check_curve_averaging.py``'s helpers.  It is
outside the pytest gate: it needs a running server that can load the cells
(``SBFSEM_OFFLINE=1`` and the cells cached under ``~/.cache/sbfsem`` do: Inferior c1178,
soma traced and dendrites discs, and c16137, discs alone).  The pure half is covered by
``tests/js/test_renderoptions.mjs``, which the gate does run.

* ``controls`` -- the four controls exist in the 3D render panel with the ids the schema gives
  them (Curve links and Join bodies in the row, the two bridges in the More menu), start at
  their defaults (0, 0, 1 and 5 um), carry their bounds and a help text, and show where they
  act: all four under Closed curve, the bridges alone under Hybrid, none under Cylinder or
  Tube.
* ``compare`` -- Render compare's per-pane options panel has them too, with no element id, and
  follows the pane's technique.
* ``build`` -- Closed curve on c1178: the request carries the four (at their defaults until
  they are typed), a value typed into a box and applied makes the next request carry it and
  brings back another surface, and the defaults typed back bring back the first one.
* ``shots`` (with ``--shots DIR``) -- the regions the user's screenshots showed: c1178's
  three links across 22 untraced sections, a chain of long links on c16137, and a turning
  chain on c1178, curved links off and on, shaded and as a wireframe.
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
from check_app import boot, js, load  # noqa: E402
from check_curve_averaging import (  # noqa: E402
    RECORD,
    aim,
    apply_closed_curve,
    press_apply,
    requests,
    set_method,
    shoot,
    type_into,
    vertex_count,
)
from check_dropdowns import Chrome, Report, find_chrome  # noqa: E402

#: The ids, bounds and defaults the schema gives the four controls, and the slot they sit in.
CONTROLS = {
    'curve_links_um': {'wrapper': 'curve-links-field', 'control': 'curve-links', 'min': '0',
                       'max': '20', 'step': '0.25', 'value': 0.0, 'slot': 'strip', 'word': 'curve'},
    # The viewer joins bodies up to 5 um apart by default (2.6.2); the library and the API at 0.
    'curve_tie_um': {'wrapper': 'curve-tie-field', 'control': 'curve-tie', 'min': '0', 'max': '20',
                     'step': '0.25', 'value': 5.0, 'slot': 'strip', 'word': 'bod'},
    'curve_bridge_um': {'wrapper': 'curve-bridge-field', 'control': 'curve-bridge', 'min': '0',
                        'max': '10', 'step': '0.1', 'value': 1.0, 'slot': 'more', 'word': 'skip'},
    'curve_link_bridge_um': {'wrapper': 'curve-link-bridge-field', 'control': 'curve-link-bridge',
                             'min': '0', 'max': '50', 'step': '0.5', 'value': 5.0, 'slot': 'more',
                             'word': 'link'},
}

#: Under each method: which of the four show.
SHOWN = {
    'closedcurve': {'curve_links_um': True, 'curve_tie_um': True, 'curve_bridge_um': True,
                    'curve_link_bridge_um': True},
    'hybrid': {'curve_links_um': False, 'curve_tie_um': False, 'curve_bridge_um': True,
               'curve_link_bridge_um': True},
    'cylinder': dict.fromkeys(CONTROLS, False),
    'curvemesh': dict.fromkeys(CONTROLS, False),
}


def shown(c: Chrome, key: str) -> bool:
    return not js(c, "document.getElementById(%s).hidden" % json.dumps(CONTROLS[key]['wrapper']))


def open_more(c: Chrome) -> None:
    """Open the render panel's More menu, where the two bridges are."""
    js(c, "document.getElementById('panel-render').hidden && document.getElementById('toggle-render').click()")
    if js(c, "document.getElementById('defaults-menu').hidden"):
        x, y, *_ = c.center('#defaults-menu-btn')
        c.click(x, y)
        c.settle(1)


def close_more(c: Chrome) -> None:
    if not js(c, "document.getElementById('defaults-menu').hidden"):
        c.key('Escape')
        c.settle(1)
    if not js(c, "document.getElementById('defaults-menu').hidden"):
        x, y, *_ = c.center('#defaults-menu-btn')
        c.click(x, y)
        c.settle(1)


# -- controls ----------------------------------------------------------------------------

def controls(c: Chrome, r: Report, ctx):
    r.start('controls: the four reaches in the 3D render panel')
    boot(c, ctx.url)
    for key, spec in CONTROLS.items():
        got = js(c, """(() => { const box = document.getElementById(%s);
            const wrap = document.getElementById(%s);
            return box ? { value: box.value, min: box.min, max: box.max, step: box.step, type: box.type,
                           key: wrap && wrap.dataset.key, help: wrap ? wrap.dataset.help : '',
                           more: !!box.closest('#defaults-menu') } : null; })()"""
                 % (json.dumps(spec['control']), json.dumps(spec['wrapper'])))
        r.check(f'{key}: there is a control with the schema\'s id', got is not None, got)
        if got is None:
            continue
        r.check(f'{key}: a number box starting at {spec["value"]:g} with the schema\'s bounds',
                got['type'] == 'number' and float(got['value']) == spec['value']
                and (got['min'], got['max'], got['step']) == (spec['min'], spec['max'], spec['step']), got)
        r.check(f'{key}: in the {"More menu" if spec["slot"] == "more" else "panel row"}',
                got['more'] is (spec['slot'] == 'more'), got['more'])
        r.check(f'{key}: its wrapper names the option and carries a help text with measured numbers',
                got['key'] == key and spec['word'] in got['help'].lower() and len(got['help']) > 300
                and 'µm' in got['help'], got['help'][:120])
    for method, expect in SHOWN.items():
        set_method(c, method)
        c.settle(2)
        got = {key: shown(c, key) for key in CONTROLS}
        r.check(f'{method}: {", ".join(k for k, v in expect.items() if v) or "none"} shown', got == expect, got)
    set_method(c, 'cylinder')
    inert = js(c, """['curve-links-field', 'curve-tie-field', 'curve-bridge-field', 'curve-link-bridge-field']
        .some((id) => document.getElementById(id).classList.contains('inert'))""")
    r.check('a hidden control is never grayed as well', not inert)


# -- compare -----------------------------------------------------------------------------

def compare(c: Chrome, r: Report, ctx):
    r.start('compare: the same four in a pane\'s options')
    boot(c, ctx.url)
    load(c, ctx.cell, ctx.source)
    js(c, "document.querySelector('button.tab[data-view=compare]').click()")
    c.settle(3)
    opened = js(c, """(() => { const arrow = document.querySelector('#compare-grid .compare-opts');
        if (!arrow) return false; arrow.click(); return true; })()""")
    r.check('a technique pane has an options arrow', bool(opened))
    c.wait_for("!document.getElementById('panel-compare-options').hidden", 10)
    keys = json.dumps(list(CONTROLS))
    panel = js(c, """(() => { const p = document.getElementById('panel-compare-options');
        const out = {};
        for (const key of %s) {
          const label = p.querySelector('[data-key="' + key + '"]');
          out[key] = label ? { ids: [label, ...label.querySelectorAll('*')].map((e) => e.id).filter(Boolean),
                               help: label.dataset.help.length > 300 } : null;
        }
        return out; })()""" % keys)
    for key in CONTROLS:
        r.check(f'{key}: the pane\'s options have it, with no element id, and a help text',
                panel[key] is not None and panel[key]['ids'] == [] and panel[key]['help'], panel[key])
    for method in ('closedcurve', 'hybrid', 'cylinder'):
        js(c, """(() => { const m = document.querySelector('#panel-compare-options [data-key="method"] select');
            m.value = %s; m.dispatchEvent(new Event('change', { bubbles: true })); })()""" % json.dumps(method))
        c.settle(2)
        got = js(c, """(() => { const p = document.getElementById('panel-compare-options');
            return Object.fromEntries(%s.map((k) => [k, !p.querySelector('[data-key="' + k + '"]').hidden])); })()"""
                 % keys)
        r.check(f'under {method} the pane shows what the 3D panel does', got == SHOWN[method], got)


# -- build -------------------------------------------------------------------------------

def build(c: Chrome, r: Report, ctx):
    r.start('build: a reach typed into its box reaches the build and changes the surface')
    boot(c, ctx.url)
    js(c, RECORD)
    key = load(c, ctx.cell, ctx.source, mesh=True, seconds=600)
    js(c, RECORD)
    apply_closed_curve(c, key, ctx.pixel)
    press_apply(c, key)
    first = requests(c)[-1]['opts']
    defaults = {k: spec['value'] for k, spec in CONTROLS.items()}
    r.check('the request carries the four at their defaults until they are typed',
            first.get('method') == 'closedcurve'
            and all(first.get(k) == v for k, v in defaults.items()), {k: first.get(k) for k in defaults})
    plain = vertex_count(c, key)
    r.check('and a surface comes back', plain > 1000, plain)
    # Each typed away from its default and back (Join bodies starts at 5 in the viewer).
    for name, control, value, back in (('curve_links_um', 'curve-links', '1', '0'),
                                       ('curve_tie_um', 'curve-tie', '0', '5')):
        type_into(c, control, value)
        press_apply(c, key)
        sent = requests(c)[-1]['opts']
        r.check(f'Apply sends {name}={value} and the other three as they were',
                sent.get(name) == float(value)
                and all(sent.get(k) == v for k, v in defaults.items() if k != name), sent)
        other = vertex_count(c, key)
        r.check(f'{name} {value}: the surface that came back is another one', other not in (0, plain),
                (plain, other))
        record = js(c, "window.sbfsem.state.neurons.get(%s).render.%s" % (json.dumps(key), name))
        r.check(f'{name}: the cell\'s own settings hold it, to be attached, saved and restored', record == float(value),
                record)
        type_into(c, control, back)
        press_apply(c, key)
        r.check(f'{name} {back} again is the surface as it was', vertex_count(c, key) == plain,
                (vertex_count(c, key), plain))
    open_more(c)
    type_into(c, 'curve-bridge', '2')
    open_more(c)
    type_into(c, 'curve-link-bridge', '1.5')
    close_more(c)
    press_apply(c, key)
    sent = requests(c)[-1]['opts']
    r.check('the two bridges typed into the More menu are sent with the next Apply',
            sent.get('curve_bridge_um') == 2 and sent.get('curve_link_bridge_um') == 1.5, sent)
    shorter = vertex_count(c, key)
    r.check('following links 1.5 um and no farther drops c1178\'s three 22-section links: another surface',
            shorter not in (0, plain), (plain, shorter))


# -- shots -------------------------------------------------------------------------------

#: (name, cell, raster um, target, distance, elevation, azimuth): where the user's screenshots
#: were, as best the annotations say.
SHOTS = [
    ('c1178-band', 1178, 0.1, [137.6, 103.6, 83.25], 13.0, 12.0, -100.0),
    ('c1178-band-top', 1178, 0.1, [137.6, 103.6, 83.25], 13.0, 80.0, -90.0),
    ('c16137-chain', 16137, 0.05, [135.4, 74.6, 63.6], 9.0, 70.0, -90.0),
    ('c1178-turn', 1178, 0.1, [84.4, 148.8, 96.9], 9.0, 70.0, -90.0),
]


def shots(c: Chrome, r: Report, ctx):
    if not ctx.shots:
        return
    folder = Path(ctx.shots)
    loaded: dict[int, str] = {}
    for name, cell, pixel, target, distance, elevation, azimuth in SHOTS:
        if ctx.tag and not name.startswith(ctx.tag):
            continue
        r.start(f'shots: {name}, curved links off and on')
        if cell not in loaded:
            boot(c, ctx.url)
            js(c, RECORD)
            loaded.clear()
            loaded[cell] = load(c, cell, ctx.source, mesh=True, seconds=600)
            js(c, RECORD)
        key = loaded[cell]
        apply_closed_curve(c, key, pixel)
        for label, links in (('off', '0'), ('on', str(ctx.links))):
            type_into(c, 'curve-links', links)
            press_apply(c, key)
            for style in ('surface', 'wire'):
                js(c, "window.sbfsem.renderer.setWireframe(%s, %s)" % (json.dumps(key),
                                                                        json.dumps(style == 'wire')))
                aim(c, target, distance, elevation, azimuth)
                shoot(c, folder, f'{name}-{style}-{label}.png')
            js(c, "window.sbfsem.renderer.setWireframe(%s, false)" % json.dumps(key))
            r.check(f'{name} {label}: pictures written', True)


SECTIONS = {'controls': controls, 'compare': compare, 'build': build, 'shots': shots}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default='http://127.0.0.1:8765', help='the dev server')
    parser.add_argument('--chrome', help="Chrome for Testing, if not in Playwright's cache")
    parser.add_argument('--cell', type=int, default=1178, help='a cell with long disc links (Inferior Monkey)')
    parser.add_argument('--source', default='NeitzInferiorMonkey')
    parser.add_argument('--pixel', type=float, default=0.25, help='the closed-curve raster of the build check, um')
    parser.add_argument('--only', help='comma list of: ' + ', '.join(SECTIONS))
    parser.add_argument('--shots', help='write the before/after pictures into this folder')
    parser.add_argument('--tag', default='', help='only the pictures whose name starts with this')
    parser.add_argument('--links', type=float, default=1.0, help='Curve links of the "on" pictures, um')
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
