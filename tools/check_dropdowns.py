"""Scripted browser check of the in-page dropdown list (static/js/selectpicker.js).

    .venv/bin/python tools/check_dropdowns.py                         # the dev server on :8765
    .venv/bin/python tools/check_dropdowns.py --url http://127.0.0.1:8772 --cell 943
    .venv/bin/python tools/check_dropdowns.py --only keys,dialog -v   # some sections, with detail
    .venv/bin/python tools/check_dropdowns.py --shots /tmp/dropdowns  # and the screenshot matrix

It drives a headless Chrome for Testing over the DevTools protocol with real
mouse and key events (``Input.dispatchMouseEvent`` / ``dispatchKeyEvent``), in
a throwaway profile, so the user's own browser is untouched.  Chrome for
Testing comes from Playwright's cache (``--chrome`` points elsewhere); the
only Python dependencies are ``websockets`` and, for the screenshot checks,
Pillow, both in the repo's venv.  It is outside the pytest gate: it needs a
running server.  The pure half of the module is covered by
``tests/js/test_selectpicker.mjs``, which the gate does run.

What it cannot see: the browser's own popup.  On a Mac, Chrome draws a select's
list as a system menu that no headless run shows, and the user-agent override
used here to look like Windows changes what the page believes, not Chromium's
own platform code.  So "the native list did not open" is proved the way a page
can prove it: the opening key or press was cancelled (``defaultPrevented``), and
no ``keypress`` follows a cancelled Space or Enter, which is the event Chrome
opens its list on.  Whether a real Windows Chrome then shows only ours, and
whether the fault in a virtual machine goes away, is for that machine to say.

``--cell ID`` loads a cell through the page's own form first (a volume's cell,
for the selects that live in a cell's panel); without it those checks are
skipped.  A server started offline with a fixture cell (the way
tests/unit/test_web.py does it) is enough: ``--cell 943``.
"""

# ruff: noqa: UP031
# The JavaScript snippets below are filled with `%`: their braces would all need
# doubling in an f-string or `.format`, which buries the script they carry.

from __future__ import annotations

import argparse
import atexit
import base64
import glob
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import suppress
from pathlib import Path

from websockets.sync.client import connect

# -- what the pages are told about the browser -----------------------------------

UA_WINDOWS = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36')
UA_MAC = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36')
CHROMIUM_BRANDS = [{'brand': 'Chromium', 'version': '148'},
                   {'brand': 'Google Chrome', 'version': '148'},
                   {'brand': 'Not.A/Brand', 'version': '99'}]
AGENTS = {
    'windows': {'userAgent': UA_WINDOWS, 'platform': 'Win32', 'meta': ('Windows', CHROMIUM_BRANDS)},
    'mac': {'userAgent': UA_MAC, 'platform': 'MacIntel', 'meta': ('macOS', CHROMIUM_BRANDS)},
    # Brands without Chromium: what a browser that is not one looks like to the page.
    'other': {'userAgent': UA_WINDOWS, 'platform': 'Win32',
              'meta': ('Windows', [{'brand': 'Other', 'version': '1'}])},
}

STORAGE_KEY = 'sbfsem:select-lists'

# Added to every document before the page's own scripts: one log of what a
# listener running AFTER the picker's capture handler saw.  `cap:` entries come
# from window-capture listeners, which run before it.
PROBE = r"""
(() => {
  const log = (window.__log = []);
  const rec = (type, e, extra) => log.push(Object.assign({
    type, key: e.key, id: (e.target && (e.target.id || e.target.tagName)) || '',
    dp: e.defaultPrevented }, extra || {}));
  for (const t of ['keydown', 'keypress', 'keyup']) {
    window.addEventListener(t, (e) => rec('cap:' + t, e), true);
    window.addEventListener(t, (e) => rec(t, e));
  }
  window.addEventListener('mousedown', (e) => rec('mousedown', e));
  for (const t of ['input', 'change']) {
    window.addEventListener(t, (e) => {
      if (e.target instanceof HTMLSelectElement) {
        rec(t, e, { value: e.target.value, idx: e.target.selectedIndex,
                    bubbles: e.bubbles, composed: e.composed });
      }
    }, true);
  }
})();
"""

# The open list, as the page shows it.
POP = r"""
(() => {
  const p = document.querySelector('.sel-pop');
  if (!p) return null;
  const rows = [...p.querySelectorAll('.sel-opt')];
  const r = p.getBoundingClientRect();
  const live = document.querySelector('.sel-live');
  return {
    count: document.querySelectorAll('.sel-pop').length,
    n: rows.length, heads: p.querySelectorAll('.sel-head').length,
    labels: rows.map((n) => n.textContent),
    disabled: rows.map((n) => n.getAttribute('aria-disabled') === 'true'),
    helps: rows.map((n) => n.dataset.help || ''),
    active: rows.findIndex((n) => n.classList.contains('active')),
    selected: rows.findIndex((n) => n.getAttribute('aria-selected') === 'true'),
    top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height,
    scrollH: p.scrollHeight, clientH: p.clientHeight, scrollTop: p.scrollTop,
    host: p.parentElement.tagName + (p.parentElement.id ? '#' + p.parentElement.id : ''),
    role: p.getAttribute('role'), name: p.getAttribute('aria-label'),
    liveRole: live ? live.getAttribute('role') : null, liveText: live ? live.textContent : null,
    liveSibling: live ? live.parentElement === p.parentElement : null,
    liveInside: !!p.querySelector('.sel-live'),
    optionRoles: rows.every((n) => n.getAttribute('role') === 'option'),
    owner: (() => { const s = document.querySelector('select[aria-expanded="true"]');
      return s ? { id: s.id, controls: s.getAttribute('aria-controls'),
                   activedescendant: s.getAttribute('aria-activedescendant'),
                   popId: p.id, rowIds: rows.map((n) => n.id) } : null; })(),
  };
})()
"""

VK = {'Enter': 13, 'Escape': 27, ' ': 32, 'ArrowLeft': 37, 'ArrowUp': 38, 'ArrowRight': 39,
      'ArrowDown': 40, 'Home': 36, 'End': 35, 'PageUp': 33, 'PageDown': 34, 'Tab': 9,
      'F4': 115, 'Backspace': 8}
CODES = {'Enter': 'Enter', 'Escape': 'Escape', ' ': 'Space', 'Tab': 'Tab', 'F4': 'F4'}
ALT, CTRL, META, SHIFT = 1, 2, 4, 8

DEFAULT_CHROME_GLOBS = (
    '~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app'
    '/Contents/MacOS/Google Chrome for Testing',
    '~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome',
)


def find_chrome(explicit: str | None) -> str:
    if explicit:
        return explicit
    for pattern in DEFAULT_CHROME_GLOBS:
        found = sorted(glob.glob(os.path.expanduser(pattern)))
        if found:
            return found[-1]
    sys.exit('Chrome for Testing not found: install it with Playwright or pass --chrome PATH')


# -- every browser this process started goes when it does ---------------------------
# Every check starts its browsers through Chrome below.  One that stops early (an
# exception, Ctrl-C, a kill, a closed terminal) used to leave headless Chrome for
# Testing running and its profile in $TMPDIR; on 2026-10-16 strays piled up beside
# the test suites and dev servers until the Mac ran out of memory.  So each Chrome is
# registered here until closed, atexit closes what is left, and SIGTERM and SIGHUP
# unwind the way Ctrl-C does (finally blocks, then atexit).  Only SIGKILL gets past
# this: tools/cleanup_dev.sh lists, and with --kill stops, what that leaves.
PROFILE_PREFIX = 'check-dropdowns-'
_LIVE: list[Chrome] = []


def close_all() -> None:
    """Close every browser this process started and has not closed."""
    for page in list(_LIVE):
        page.close()


def _unwind(signum, _frame):
    raise SystemExit(128 + signum)


atexit.register(close_all)
for _name in ('SIGTERM', 'SIGHUP'):
    if hasattr(signal, _name):
        with suppress(ValueError):      # only the main thread may set a handler
            signal.signal(getattr(signal, _name), _unwind)


# -- the DevTools client -----------------------------------------------------------

class Chrome:
    """One headless Chrome with one page, in a throwaway profile."""

    def __init__(self, chrome: str, width: int = 1440, height: int = 900, preferences: dict | None = None):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        self.profile = tempfile.mkdtemp(prefix=PROFILE_PREFIX)
        self.proc: subprocess.Popen | None = None
        _LIVE.append(self)     # before Chrome starts: a Ctrl-C from here on still removes the profile
        if preferences:     # what the profile holds before Chrome first reads it
            (Path(self.profile) / 'Default').mkdir()
            (Path(self.profile) / 'Default' / 'Preferences').write_text(json.dumps(preferences), encoding='utf-8')
        self.proc = subprocess.Popen(
            [chrome, '--headless=new', f'--remote-debugging-port={port}',
             f'--user-data-dir={self.profile}', f'--window-size={width},{height}',
             '--no-first-run', '--no-default-browser-check', '--disable-extensions',
             'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page = None
        for _ in range(300):          # up to a minute: the machine may be busy
            try:
                targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json'))
                page = next((t for t in targets if t['type'] == 'page'), None)
                if page:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if not page:
            self.close()
            sys.exit('Chrome did not start')
        self._connection = connect(page['webSocketDebuggerUrl'], max_size=2 ** 27)
        self.ws = self._connection.__enter__()
        self._id = 0
        self.events: list[dict] = []
        self.width, self.height = width, height
        self.send('Page.enable')
        self.send('Runtime.enable')
        self.send('Log.enable')

    # protocol ------------------------------------------------------------------
    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=60))
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError(f'{method}: {msg["error"]}')
                return msg.get('result', {})
            self.events.append(msg)

    def ev(self, expression: str, wait: bool = False):
        done = self.send('Runtime.evaluate', expression=expression, returnByValue=True,
                         awaitPromise=wait, userGesture=True)
        if 'exceptionDetails' in done:
            raise RuntimeError(json.dumps(done['exceptionDetails'])[:600])
        return done['result'].get('value')

    def settle(self, frames: int = 2):
        """Wait for the page to have drawn `frames` frames (or 0.4 s, if it draws none)."""
        if self.ev('document.visibilityState') == 'hidden':
            self.send('Page.bringToFront')     # headless Chrome hides its page after a dialog closes
        self.ev('new Promise((resolve) => { let n = %d; const tick = () => '
                '(--n <= 0 ? resolve(true) : requestAnimationFrame(tick)); '
                'requestAnimationFrame(tick); setTimeout(resolve, 400); })' % frames, wait=True)

    def wait_for(self, expression: str, seconds: float = 10.0) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if self.ev(expression):
                return True
            time.sleep(0.1)
        return False

    # the browser ------------------------------------------------------------------
    def emulate(self, agent: str = 'windows', width: int | None = None, height: int | None = None,
                scheme: str = 'dark'):
        spec = AGENTS[agent]
        platform, brands = spec['meta']
        self.width = width or self.width
        self.height = height or self.height
        # A screen larger than the viewport, or the app takes the window for
        # full screen (fullscreen.js) and hides its cell list.
        self.send('Emulation.setDeviceMetricsOverride', width=self.width, height=self.height,
                  deviceScaleFactor=1, mobile=False, screenWidth=2560, screenHeight=1600)
        self.send('Emulation.setUserAgentOverride', userAgent=spec['userAgent'],
                  platform=spec['platform'],
                  userAgentMetadata={'brands': brands, 'fullVersionList': brands,
                                     'platform': platform, 'platformVersion': '15.0.0',
                                     'architecture': 'arm', 'model': '', 'mobile': False})
        self.send('Emulation.setEmulatedMedia',
                  features=[{'name': 'prefers-color-scheme', 'value': scheme}])

    def scheme(self, scheme: str):
        self.send('Emulation.setEmulatedMedia',
                  features=[{'name': 'prefers-color-scheme', 'value': scheme}])

    def goto(self, url: str, stored: str | None = None, extra_script: str = ''):
        """Load the app; `stored` is what the Display checkbox would have saved."""
        seed = (f"try {{ localStorage.setItem('{STORAGE_KEY}', '{stored}'); }} catch (e) {{}}"
                if stored else f"try {{ localStorage.removeItem('{STORAGE_KEY}'); }} catch (e) {{}}")
        self.send('Page.addScriptToEvaluateOnNewDocument',
                  source=f'{PROBE}\n{seed}\n{extra_script}')
        self.send('Page.navigate', url=url)
        if not self.wait_for("document.readyState === 'complete'", 30):
            raise RuntimeError('the page did not finish loading')
        # The volume list arrives after boot() has bound the controls.
        if not self.wait_for("document.querySelector('#volume') && "
                             "document.querySelector('#volume').options.length > 0", 30):
            raise RuntimeError('the app did not start: #volume stayed empty')
        time.sleep(0.4)
        self.settle()

    def close(self):
        """Stop the browser and delete its profile; safe to call twice, and on a half-started one."""
        with suppress(Exception):
            self.send('Browser.close')
        with suppress(Exception):
            self._connection.__exit__(None, None, None)
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
                with suppress(Exception):
                    self.proc.wait(timeout=5)
        shutil.rmtree(self.profile, ignore_errors=True)
        with suppress(ValueError):
            _LIVE.remove(self)

    # input -------------------------------------------------------------------------
    def move(self, x: float, y: float, buttons: int = 0):
        # A drag names the button it holds as well as counting it, as a real mouse's does.  Counted alone,
        # Chrome lets go of a pointer capture at the first move ("lostpointercapture", measured 2026-10-16
        # on the error drawer's histogram), which a page that ends a drag on that event then obeys.
        button = 'left' if buttons & 1 else 'right' if buttons & 2 else 'none'
        self.send('Input.dispatchMouseEvent', type='mouseMoved', x=x, y=y, buttons=buttons, button=button)

    def press(self, x: float, y: float, button: str = 'left'):
        self.move(x, y)
        self.send('Input.dispatchMouseEvent', type='mousePressed', x=x, y=y, button=button,
                  buttons=1 if button == 'left' else 2, clickCount=1)

    def release(self, x: float, y: float, button: str = 'left'):
        self.send('Input.dispatchMouseEvent', type='mouseReleased', x=x, y=y, button=button,
                  buttons=0, clickCount=1)

    def click(self, x: float, y: float, button: str = 'left'):
        self.press(x, y, button)
        self.release(x, y, button)
        self.settle()

    def key(self, name: str, mods: int = 0, settle: bool = True, repeat: bool = False):
        vk = VK.get(name) or ord(name.upper())
        code = CODES.get(name) or (name if name.startswith(('Arrow', 'Page')) or name in ('Home', 'End')
                                   else f'Key{name.upper()}' if name.isalpha() else name)
        text = {'Enter': '\r', ' ': ' '}.get(name, name if len(name) == 1 else '')
        if mods & (ALT | CTRL | META):
            text = ''
        base = {'key': name, 'code': code, 'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk,
                'modifiers': mods}
        self.send('Input.dispatchKeyEvent', type='keyDown' if text else 'rawKeyDown',
                  text=text, unmodifiedText=text, autoRepeat=repeat, **base)
        self.send('Input.dispatchKeyEvent', type='keyUp', **base)
        if settle:
            self.settle()

    def tap(self, x: float, y: float):
        self.send('Input.dispatchTouchEvent', type='touchStart', touchPoints=[{'x': x, 'y': y}])
        self.send('Input.dispatchTouchEvent', type='touchEnd', touchPoints=[])
        self.settle()

    def shot(self, clip: dict | None = None) -> bytes:
        params: dict = {'format': 'png'}
        if clip:
            params['clip'] = {**clip, 'scale': 1}
        return base64.b64decode(self.send('Page.captureScreenshot', **params)['data'])

    # the page ------------------------------------------------------------------------
    def pop(self):
        return self.ev(POP)

    def log(self, clear: bool = True):
        return self.ev('window.__log.splice(0)' if clear else 'window.__log')

    def center(self, selector: str):
        """Scroll an element into view and return where to click it."""
        return self.ev("""(() => {
          const e = document.querySelector(%s);
          if (!e) return null;
          e.scrollIntoView({ block: 'center', inline: 'nearest' });
          const r = e.getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2, r.width, r.height];
        })()""" % json.dumps(selector))

    def row_xy(self, index: int):
        return self.ev("""(() => {
          const e = document.querySelectorAll('.sel-pop .sel-opt')[%d];
          if (!e) return null;
          const r = e.getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        })()""" % index)

    def reachable(self, selector: str) -> bool:
        """Is the element the topmost thing at its own centre (a real click would land on it)?"""
        return bool(self.ev("""(() => {
          const e = document.querySelector(%s);
          if (!e) return false;
          const r = e.getBoundingClientRect();
          if (!r.width || !r.height) return false;
          const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return !!at && (at === e || e.contains(at));
        })()""" % json.dumps(selector)))


# -- reporting ----------------------------------------------------------------------

class Report:
    def __init__(self, verbose: bool):
        self.verbose = verbose
        self.passed = 0
        self.failed: list[str] = []
        self.skipped: list[str] = []
        self.section = ''

    def start(self, name: str):
        self.section = name
        print(f'\n== {name}')

    def check(self, name: str, ok: bool, detail=''):
        text = f'{name}' + (f'  [{detail}]' if detail != '' and (not ok or self.verbose) else '')
        if ok:
            self.passed += 1
            print(f'ok   {text}')
        else:
            self.failed.append(f'{self.section}: {name}')
            print(f'FAIL {text}')

    def skip(self, name: str, why: str):
        self.skipped.append(f'{self.section}: {name} ({why})')
        print(f'skip {name} ({why})')

    def summary(self) -> int:
        print(f'\n{self.passed} passed, {len(self.failed)} failed, {len(self.skipped)} skipped')
        for line in self.failed:
            print(f'  FAILED  {line}')
        return 1 if self.failed else 0


# -- page-level helpers -------------------------------------------------------------

def show_view(c: Chrome, view: str):
    c.ev(f"document.querySelector('.tab[data-view=\"{view}\"]').click()")
    c.settle()


def prepare(c: Chrome, sid: str) -> bool:
    """Bring a static select into view the way a person would: tabs, panels, menus, dialogs."""
    c.ev("document.querySelector('#defaults-menu') && !document.querySelector('#defaults-menu').hidden "
         "&& document.querySelector('#defaults-menu-btn').click()")
    for dialog in c.ev("[...document.querySelectorAll('dialog[open]')].map((d) => d.id)"):
        c.ev(f"document.getElementById('{dialog}').close()")
    where = c.ev("""(() => {
      const s = document.getElementById(%s);
      const view = s.closest('[id^="view-"]');
      return { view: view ? view.id.slice(5) : null,
               panel: (s.closest('#panel-render, #panel-display') || {}).id || null,
               menu: !!s.closest('#defaults-menu'),
               dialog: (s.closest('dialog') || {}).id || null };
    })()""" % json.dumps(sid))
    if where['view'] and where['view'] != 'render':
        show_view(c, where['view'])
    else:
        show_view(c, 'render')
    if where['panel']:
        toggle = 'toggle-render' if where['panel'] == 'panel-render' else 'toggle-display'
        c.ev(f"document.getElementById('{where['panel']}').hidden "
             f"&& document.getElementById('{toggle}').click()")
        c.settle()
    if where['menu']:
        c.ev("document.getElementById('defaults-menu').hidden "
             "&& document.getElementById('defaults-menu-btn').click()")
        c.settle()
    if where['dialog']:
        c.ev(f"document.getElementById('{where['dialog']}').showModal()")
        c.settle()
    # A collapsed <details> (the gradient editor) hides what is inside it.
    c.ev(f"for (let n = document.getElementById('{sid}').closest('details'); n; n = n.parentElement && n.parentElement.closest('details')) n.open = true")
    if sid == 'pixel-size-menu':
        set_value(c, 'render-method', 'closedcurve')
    elif sid == 'ultra-soma':
        set_value(c, 'render-method', 'cylinder')
        set_value(c, 'pathway', 'ultraliser')
    elif sid == 'post-order':
        # Grayed out (and so not clickable) under Ultraliser, which the soma proxy above left
        # chosen, and under Closed curve: a pathway that smooths.
        set_value(c, 'render-method', 'cylinder')
        set_value(c, 'pathway', 'voxel2')
    return c.reachable(f'#{sid}')


def set_value(c: Chrome, sid: str, value: str):
    """What a pick does to the app, without going through the list."""
    c.ev(f"""(() => {{ const s = document.getElementById('{sid}'); s.value = '{value}';
      s.dispatchEvent(new Event('input', {{ bubbles: true }}));
      s.dispatchEvent(new Event('change', {{ bubbles: true }})); }})()""")
    c.settle()


def close_list(c: Chrome):
    if c.pop():
        c.key('Escape')


def open_by_click(c: Chrome, selector: str):
    x, y, *_ = c.center(selector)
    c.click(x, y)
    return c.pop()


def open_pane_editor(c: Chrome, pane: int = 0):
    """Render compare keeps a pane's technique select in its layer editor, a popover under the
    layer's chip: open it (it stays open while the pane is rebuilt), as a click on the chip does."""
    c.ev("""(() => { const p = window.sbfsem.compare.panes[%d];
      if (p.editor.hidden) window.sbfsem.compare._openEditor(p.layers[0]); })()""" % pane)
    time.sleep(0.25)


def synth_key(c: Chrome, selector: str, key: str, mods: int = 0) -> dict:
    """An untrusted keydown on a focused select: our listeners see it, the browser's own handling does not.

    The headless Chrome here is a Mac's, and on a Mac an unhandled arrow on a
    select opens the system menu, which a headless run cannot dismiss.  So a key
    that must be *left to the browser* is checked this way: was it cancelled?
    """
    return c.ev("""(() => {
      const s = document.querySelector(%s); s.focus();
      const e = new KeyboardEvent('keydown', { key: %s, bubbles: true, cancelable: true,
        altKey: %s, ctrlKey: %s, metaKey: %s, shiftKey: %s });
      const passed = s.dispatchEvent(e);
      return { cancelled: !passed, open: !!document.querySelector('.sel-pop') };
    })()""" % (json.dumps(selector), json.dumps(key), *('true' if mods & m else 'false' for m in (ALT, CTRL, META, SHIFT))))


def synth_mousedown(c: Chrome, selector: str, touch: bool = False) -> dict:
    """An untrusted press on a select (after an untrusted touch pointerdown, if `touch`)."""
    return c.ev("""(() => {
      const s = document.querySelector(%s);
      if (%s) s.dispatchEvent(new PointerEvent('pointerdown', { pointerType: 'touch', bubbles: true, cancelable: true }));
      const e = new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 });
      const passed = s.dispatchEvent(e);
      const out = { cancelled: !passed, open: !!document.querySelector('.sel-pop') };
      if (%s) s.dispatchEvent(new PointerEvent('pointerdown', { pointerType: 'mouse', bubbles: true }));
      return out;
    })()""" % (json.dumps(selector), 'true' if touch else 'false', 'true' if touch else 'false'))


def dispatch_count(entries, kind: str) -> int:
    return sum(1 for e in entries if e['type'] == kind)


def make_playground(c: Chrome, options: int = 6, sid: str = 'dd-test', groups: bool = False):
    """A fixed box in the top-left corner holding one extra select, for cases the app has no select for."""
    c.ev("""(() => {
      document.getElementById('dd-box')?.remove();
      const box = document.createElement('div');
      box.id = 'dd-box';
      box.style.cssText = 'position:fixed;top:60px;left:380px;z-index:5;padding:6px;background:var(--panel)';
      const s = document.createElement('select');
      s.id = %s;
      document.body.append(box); box.append(s);
    })()""" % json.dumps(sid))
    fill_playground(c, options, sid, groups)


def fill_playground(c: Chrome, options: int, sid: str = 'dd-test', groups: bool = False):
    c.ev("""(() => {
      const s = document.getElementById(%s);
      if (!%s) { s.replaceChildren(...Array.from({ length: %d }, (_, i) => new Option('Item ' + (i + 1), 'v' + (i + 1)))); return; }
      const g1 = document.createElement('optgroup'); g1.label = 'First group';
      g1.append(new Option('Alpha', 'a'), new Option('Beta (off)', 'b'), new Option('Gamma', 'c'));
      g1.children[1].disabled = true;
      const g2 = document.createElement('optgroup'); g2.label = 'Second group';
      g2.append(new Option('Delta', 'd'), new Option('Hidden', 'h'), new Option('Epsilon', 'e'));
      g2.children[1].hidden = true;
      s.replaceChildren(new Option('Loose', 'l'), g1, g2);
    })()""" % (json.dumps(sid), 'true' if groups else 'false', options))
    c.settle()


# -- sections -------------------------------------------------------------------------

def s_load(c: Chrome, r: Report, ctx):
    r.start('load, default mode and the Display checkbox (Windows user agent)')
    state = c.ev("import('/static/js/selectpicker.js').then((m) => m.selectPickerState())", wait=True)
    r.check('the module is served and imported by the app', bool(state), state)
    r.check('Windows Chromium with a fine pointer: the list is on by default', state.get('mode') == 'page', state)
    box = c.ev("""(() => { const b = document.getElementById('opt-page-selects');
      return b && { checked: b.checked, hidden: b.closest('label').hidden,
                    help: b.closest('label').dataset.help || '', title: b.closest('label').title,
                    text: b.closest('label').textContent.trim() }; })()""")
    r.check('the Display checkbox exists, labelled "In-page dropdowns"', bool(box) and box['text'] == 'In-page dropdowns', box)
    r.check('it is ticked and visible', bool(box) and box['checked'] and not box['hidden'], box)
    r.check('it explains itself with data-help, not title', bool(box) and len(box['help']) > 80 and not box['title'])
    r.check('nothing is open and no stray list exists', c.pop() is None)
    # 31 when this was written; a later control adds to it and is covered by the loop below.
    r.check(f'{ctx["static_n"]} static selects with an id are on the page (31 when this was written)',
            ctx['static_n'] >= 31, ctx['static_n'])


def option_of_row(c: Chrome, sid: str, row: int) -> int:
    """The index among a select's options of the list's row `row` (hidden options have no row)."""
    return c.ev(f"""(() => {{ const s = document.getElementById('{sid}');
        return [...s.options].indexOf([...s.options].filter((o) => !o.hidden)[{row}]); }})()""")


def s_static(c: Chrome, r: Report, ctx):
    r.start('every static select: opens a list of its visible options, a pick fires input then change once')
    ids = ctx['static']
    problems: list[str] = []
    empty = []
    picked = 0
    for sid in ids:
        if not prepare(c, sid):
            problems.append(f'{sid}: not reachable by a real click')
            continue
        want = c.ev(f"[...document.getElementById('{sid}').options].filter((o) => !o.hidden).length")
        # The list's rows are the visible options (a hidden one, such as the Voxel size list's
        # row for a typed size, has none), so the selection is counted among those.
        sel = c.ev(f"""(() => {{ const s = document.getElementById('{sid}');
            return [...s.options].slice(0, s.selectedIndex).filter((o) => !o.hidden).length; }})()""")
        if want == 0:
            empty.append(sid)
            c.center(f'#{sid}')
            if synth_mousedown(c, f'#{sid}') != {'cancelled': False, 'open': False}:
                problems.append(f'{sid}: a list opened for a select with no options')
            continue
        c.log()
        pop = open_by_click(c, f'#{sid}')
        if not pop:
            problems.append(f'{sid}: no list opened')
            continue
        press = next((e for e in c.log() if e['type'] == 'mousedown' and e['id'] == sid), None)
        if not press or not press['dp']:
            problems.append(f"{sid}: the press that opened the list was not cancelled, so the browser's own list opens too")
        if pop['n'] != want:
            problems.append(f"{sid}: {pop['n']} rows for {want} options")
        if pop['count'] != 1:
            problems.append(f"{sid}: {pop['count']} lists at once")
        if pop['role'] != 'listbox' or not pop['optionRoles']:
            problems.append(f'{sid}: roles {pop["role"]}')
        if pop['selected'] != sel:
            problems.append(f"{sid}: marks row {pop['selected']} selected, the select is on {sel}")
        if pop['active'] != sel:
            problems.append(f"{sid}: highlights row {pop['active']}, not the selection {sel}")
        expanded = c.ev(f"document.getElementById('{sid}').getAttribute('aria-expanded')")
        if expanded != 'true':
            problems.append(f'{sid}: aria-expanded is {expanded}')
        c.log()
        # The same row again: nothing fires.  Another row: input then change, once each.
        x, y = c.row_xy(sel)
        c.click(x, y)
        again = c.log()
        if pop and any(e['type'] in ('input', 'change') for e in again):
            problems.append(f'{sid}: re-picking the selected row fired {[e["type"] for e in again]}')
        if c.pop():
            problems.append(f'{sid}: the list stayed open after a pick')
        if want > 1:
            other = 1 if sel != 1 else 0
            open_by_click(c, f'#{sid}')
            c.log()
            x, y = c.row_xy(other)
            c.click(x, y)
            seen = [e for e in c.log() if e['type'] in ('input', 'change')]
            kinds = [e['type'] for e in seen if e['id'] == sid]
            if kinds != ['input', 'change']:
                problems.append(f'{sid}: a pick fired {kinds}')
            elif not (seen[0]['bubbles'] and seen[1]['bubbles'] and seen[0]['composed']):
                problems.append(f'{sid}: events do not bubble / input not composed')
            now = c.ev(f"document.getElementById('{sid}').selectedIndex")
            wanted = option_of_row(c, sid, other)
            if now != wanted:
                problems.append(f'{sid}: selectedIndex {now}, wanted {wanted}')
            if c.ev('document.activeElement.id') != sid:
                problems.append(f'{sid}: focus left the select')
            picked += 1
            # Put it back, through the list as well.
            open_by_click(c, f'#{sid}')
            x, y = c.row_xy(sel)
            c.click(x, y)
            c.log()
    r.check(f'{len(ids) - len(empty)} selects with options each open a list with the right rows, '
            f'marked, focused and announced', not problems, '; '.join(problems[:6]))
    r.check(f'{picked} selects took a pick: one input then one change, bubbling, focus on the select',
            picked >= 20 and not problems, picked)
    if empty:
        r.check(f'{len(empty)} selects have no options yet ({", ".join(empty)}): no list, the browser keeps them',
                True)
    close_list(c)


def s_keys_closed(c: Chrome, r: Report, ctx):
    r.start('keys on a closed select (Windows): only the keys Chrome opens its own list on open ours')
    prepare(c, 'pathway')
    sel = '#pathway'

    def fresh():
        close_list(c)
        c.ev(f"document.querySelector('{sel}').focus()")
        c.log()
        c.settle()

    def press(name, mods=0):
        fresh()
        c.key(name, mods)
        entries = c.log()
        down = next((e for e in entries if e['type'] == 'keydown' and e['key'] == name), None)
        return c.pop(), entries, down

    for name, mods, label in [(' ', 0, 'Space'), ('Enter', 0, 'Enter'), ('F4', 0, 'F4'),
                              ('ArrowDown', ALT, 'Alt+ArrowDown'), ('ArrowUp', ALT, 'Alt+ArrowUp')]:
        pop, entries, down = press(name, mods)
        r.check(f'{label} opens exactly one list', bool(pop) and pop['count'] == 1)
        r.check(f'{label}: the keydown was cancelled', bool(down) and down['dp'], down)
        if name in (' ', 'Enter'):
            r.check(f'{label}: no keypress follows (the event Chrome opens its own list on)',
                    dispatch_count(entries, 'keypress') + dispatch_count(entries, 'cap:keypress') == 0, entries)
    for name, mods, label in [('ArrowDown', 0, 'ArrowDown'), ('ArrowUp', 0, 'ArrowUp'), ('Home', 0, 'Home'),
                              ('End', 0, 'End'), ('PageUp', 0, 'PageUp'), ('PageDown', 0, 'PageDown'),
                              ('c', 0, 'a letter'), ('F4', ALT, 'Alt+F4'), (' ', CTRL, 'Ctrl+Space'),
                              ('F4', CTRL, 'Ctrl+F4'), ('Enter', CTRL, 'Ctrl+Enter')]:
        fresh()
        got = synth_key(c, sel, name, mods)
        r.check(f'{label} is left to the browser: no list, keydown not cancelled',
                got == {'cancelled': False, 'open': False}, got)
    # Space inside a type-ahead session is a letter of the word, not an opener.
    fresh()
    c.key('a')
    c.key(' ')
    entries = c.log()
    downs = [e for e in entries if e['type'] == 'keydown' and e['key'] == ' ']
    r.check('Space within a second of a typed letter types ahead: not cancelled, no list',
            c.pop() is None and bool(downs) and not downs[0]['dp'], downs)
    # The control: with the setting off, the same opening keys are left alone.
    c.ev("document.getElementById('opt-page-selects').checked = false; "
         "document.getElementById('opt-page-selects').dispatchEvent(new Event('change', { bubbles: true }))")
    fresh()
    outcomes = [synth_key(c, sel, k) for k in (' ', 'Enter', 'F4')]
    r.check('control, setting off: Space, Enter and F4 are not cancelled and open no list',
            all(o == {'cancelled': False, 'open': False} for o in outcomes), outcomes)
    c.ev("document.getElementById('opt-page-selects').checked = true; "
         "document.getElementById('opt-page-selects').dispatchEvent(new Event('change', { bubbles: true }))")
    c.ev("document.activeElement.blur()")
    close_list(c)


def s_keys_open(c: Chrome, r: Report, ctx):
    r.start('keys while a list is open (select-only combobox pattern)')
    prepare(c, 'pathway')
    sel = '#pathway'
    labels = c.ev("[...document.querySelector('#pathway').options].map((o) => o.text)")
    n = len(labels)

    def reopen(start=None):
        close_list(c)
        if start is not None:
            c.ev(f"document.querySelector('{sel}').selectedIndex = {start}")
        open_by_click(c, sel)
        c.log()
        return c.pop()

    pop = reopen(2)
    r.check('opens on the selected row', pop['active'] == 2, pop['active'])
    owner = pop['owner']
    r.check('ARIA: select has aria-expanded, aria-controls to the list, aria-activedescendant to the row',
            bool(owner) and owner['controls'] == owner['popId']
            and owner['activedescendant'] == owner['rowIds'][2], owner)
    r.check('ARIA: the list is a labelled listbox with a live region beside it, not inside it',
            pop['role'] == 'listbox' and bool(pop['name']) and pop['liveRole'] == 'status'
            and pop['liveSibling'] and not pop['liveInside'], [pop['name'], pop['liveRole']])
    r.check('the live region says which row and how many', pop['liveText'] == f'{labels[2]}, 3 of {n}', pop['liveText'])
    c.key('ArrowDown')
    r.check('ArrowDown moves the highlight down', c.pop()['active'] == 3)
    c.key('ArrowUp')
    c.key('ArrowUp')
    r.check('ArrowUp moves it up', c.pop()['active'] == 1)
    entries = c.log()
    r.check('the arrows never reach the page, nor change the select',
            not any(e['type'] in ('keydown', 'input', 'change') for e in entries)
            and c.ev(f"document.querySelector('{sel}').selectedIndex") == 2, entries)
    c.key('End')
    r.check('End goes to the last row', c.pop()['active'] == n - 1)
    c.key('Home')
    r.check('Home goes to the first', c.pop()['active'] == 0)
    c.key('PageDown')
    page = c.pop()['active']
    r.check('PageDown moves a page (clamped to the last row)', page == min(n - 1, page) and page >= 1, page)
    c.key('ArrowRight')
    c.key('ArrowLeft')
    r.check('ArrowLeft and ArrowRight do not step the select under the list',
            c.ev(f"document.querySelector('{sel}').selectedIndex") == 2 and c.pop()['active'] == page)
    # Type-ahead onto a row whose first letter differs from the current one.
    target = next(i for i, text in enumerate(labels) if text[0].lower() != labels[page][0].lower())
    c.key(labels[target][0].lower())
    r.check('a letter types ahead to a row starting with it',
            c.pop()['labels'][c.pop()['active']][0].lower() == labels[target][0].lower(), c.pop()['active'])
    # Escape closes only the list.
    c.log()
    c.key('Escape')
    entries = c.log()
    r.check('Escape closes the list and nothing else sees it',
            c.pop() is None and not any(e['type'] == 'keydown' for e in entries)
            and c.ev("document.activeElement.id") == 'pathway'
            and c.ev(f"document.querySelector('{sel}').selectedIndex") == 2, entries)
    # Enter commits.
    reopen(2)
    c.key('ArrowDown')
    c.log()
    c.key('Enter')
    entries = c.log()
    kinds = [e['type'] for e in entries if e['type'] in ('input', 'change')]
    r.check('Enter commits: one input then one change, the list closes, focus stays',
            kinds == ['input', 'change'] and c.pop() is None and c.ev("document.activeElement.id") == 'pathway'
            and c.ev(f"document.querySelector('{sel}').selectedIndex") == 3, [kinds, entries])
    # Space commits when no type-ahead is running.
    reopen(3)
    c.key('ArrowUp')
    c.log()
    c.key(' ')
    kinds = [e['type'] for e in c.log() if e['type'] in ('input', 'change')]
    r.check('Space commits when nothing has been typed', kinds == ['input', 'change'] and c.pop() is None, kinds)
    # Space after a letter is part of the word.
    reopen(0)
    c.key('c')
    c.log()
    c.key(' ')
    entries = c.log()
    r.check('Space right after a typed letter is type-ahead, not a pick: the list stays open, nothing fires',
            c.pop() is not None and not [e for e in entries if e['type'] in ('input', 'change')], entries)
    # F4 and Alt+Arrow commit and close.
    reopen(0)
    c.key('ArrowDown')
    c.log()
    c.key('F4')
    kinds = [e['type'] for e in c.log() if e['type'] in ('input', 'change')]
    r.check('F4 commits and closes', kinds == ['input', 'change'] and c.pop() is None, kinds)
    reopen(0)
    c.key('ArrowDown')
    c.log()
    c.key('ArrowDown', ALT)
    kinds = [e['type'] for e in c.log() if e['type'] in ('input', 'change')]
    r.check('Alt+ArrowDown commits and closes', kinds == ['input', 'change'] and c.pop() is None, kinds)
    # Tab commits and lets focus move on.
    reopen(0)
    c.key('ArrowDown')
    c.log()
    c.key('Tab')
    kinds = [e['type'] for e in c.log() if e['type'] in ('input', 'change')]
    r.check('Tab commits, closes and moves the focus on',
            kinds == ['input', 'change'] and c.pop() is None and c.ev("document.activeElement.id") != 'pathway', kinds)
    # A held key repeats its keydown: it must not commit, close and reopen in a loop.
    reopen(2)
    c.key(' ', repeat=True)
    c.key('Enter', repeat=True)
    r.check('a held Space or Enter (auto-repeat) neither commits nor closes the list',
            c.pop() is not None and not [e for e in c.log() if e['type'] in ('input', 'change')])
    c.key('Enter')
    c.log()
    c.key('Enter', repeat=True)
    c.key(' ', repeat=True)
    r.check('and on the closed select it opens nothing, and cancels the keypress Chrome would open its list on',
            c.pop() is None and dispatch_count(c.log(), 'keypress') == 0)
    # Ctrl combinations pass through.
    c.ev(f"document.querySelector('{sel}').focus()")
    reopen(0)
    c.key('a', CTRL)
    entries = c.log()
    r.check('Ctrl+key passes through to the page while a list is open',
            any(e['type'] == 'keydown' and e['key'] == 'a' for e in entries) and c.pop() is not None, entries)
    close_list(c)
    # The restore: the select is back where the other sections expect it.
    c.ev(f"document.querySelector('{sel}').selectedIndex = 0")


def s_headings(c: Chrome, r: Report, ctx):
    r.start('optgroup headings, disabled and hidden options (a select the app has none of)')
    prepare(c, 'pathway')
    make_playground(c, groups=True)
    pop = open_by_click(c, '#dd-test')
    r.check('headings become rows that cannot be picked, hidden options are skipped',
            bool(pop) and pop['heads'] == 2 and pop['n'] == 6 and 'Hidden' not in pop['labels'], pop and pop['labels'])
    r.check('a disabled option is marked aria-disabled',
            bool(pop) and pop['disabled'] == [False, False, True, False, False, False], pop and pop['disabled'])
    c.log()
    x, y = c.row_xy(2)
    c.click(x, y)
    r.check('clicking a disabled row does nothing and leaves the list open',
            c.pop() is not None and not [e for e in c.log() if e['type'] in ('input', 'change')])
    head = c.ev("(() => { const r = document.querySelector('.sel-head').getBoundingClientRect();"
                " return [r.left + r.width / 2, r.top + r.height / 2]; })()")
    c.click(*head)
    r.check('clicking a heading does nothing and leaves the list open', c.pop() is not None)
    c.key('ArrowDown')
    c.key('ArrowDown')
    c.key('ArrowDown')
    after = c.pop()
    r.check('arrows skip the disabled row and the headings',
            after['labels'][after['active']] in ('Delta', 'Epsilon', 'Gamma'), after['labels'][after['active']])
    c.key('Home')
    r.check('Home lands on the first row that can be picked', c.pop()['labels'][c.pop()['active']] == 'Loose')
    close_list(c)
    r.check('the select is still labelled for assistive technology',
            (c.ev("document.getElementById('dd-test').getAttribute('aria-expanded')") is None))


def s_dialog(c: Chrome, r: Report, ctx):
    r.start('inside a modal <dialog> (#video-dialog)')
    prepare(c, 'video-mode')
    sel = '#video-mode'
    x, y, *_ = c.center(sel)
    c.click(x, y)
    pop = c.pop()
    r.check('a list opens inside the dialog, not behind it on the body',
            bool(pop) and pop['host'] == 'DIALOG#video-dialog', pop and pop['host'])
    reach = c.ev("(() => { const e = document.querySelector('.sel-opt'); const r = e.getBoundingClientRect();"
                 " return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === e; })()")
    r.check('its rows are reachable by a real pointer (the dialog does not make them inert)', bool(reach))
    c.key('Escape')
    r.check('first Escape closes only the list', c.pop() is None and c.ev("document.getElementById('video-dialog').open"))
    c.key('Escape')
    r.check('second Escape closes the dialog',
            not c.ev("document.getElementById('video-dialog').open"))
    prepare(c, 'video-mode')
    c.log()
    pop = open_by_click(c, sel)
    x, y = c.row_xy(1 if pop['selected'] != 1 else 0)
    c.click(x, y)
    kinds = [e['type'] for e in c.log() if e['type'] in ('input', 'change')]
    r.check('a click on a row inside the dialog picks it', kinds == ['input', 'change'], kinds)
    c.ev("document.getElementById('video-dialog').close()")


def s_placement(c: Chrome, r: Report, ctx):
    r.start('placement: flips up from the bottom toolbar, long lists scroll, 2,000 rows are quick')
    prepare(c, 'quick-technique')
    pop = open_by_click(c, '#quick-technique')
    sel = c.ev("(() => { const r = document.querySelector('#quick-technique').getBoundingClientRect();"
               " return { top: r.top, bottom: r.bottom, left: r.left }; })()")
    r.check('the bottom toolbar select opens its list upward', bool(pop) and pop['bottom'] <= sel['top'], [pop and pop['bottom'], sel])
    r.check('inside the viewport with a margin', bool(pop) and pop['top'] >= 8 and pop['left'] >= 8
            and pop['right'] <= ctx['width'] - 8, pop and [pop['top'], pop['left'], pop['right']])
    r.check('wide enough to show its widest label, as wide as the select at least',
            bool(pop) and pop['width'] >= c.ev("document.querySelector('#quick-technique').getBoundingClientRect().width") - 1)
    close_list(c)
    prepare(c, 'render-method')
    pop = open_by_click(c, '#render-method')
    sel = c.ev("document.querySelector('#render-method').getBoundingClientRect().bottom")
    r.check('a select in the panel above the toolbar opens downward or upward without covering itself',
            bool(pop) and (pop['top'] >= sel or pop['bottom'] <= c.ev("document.querySelector('#render-method').getBoundingClientRect().top")))
    close_list(c)

    make_playground(c, 300)
    c.ev("document.getElementById('dd-test').selectedIndex = 150")
    pop = open_by_click(c, '#dd-test')
    r.check('a 300-row list is capped at 360 px and scrolls', bool(pop) and pop['height'] <= 360.5 and pop['scrollH'] > pop['clientH'], pop and [pop['height'], pop['scrollH']])
    visible = c.ev("""(() => { const p = document.querySelector('.sel-pop'); const a = p.querySelector('.active');
      const pr = p.getBoundingClientRect(), ar = a.getBoundingClientRect();
      return ar.top >= pr.top - 0.5 && ar.bottom <= pr.bottom + 0.5; })()""")
    r.check('the selected row (150) is scrolled into view when it opens', bool(visible) and pop['scrollTop'] > 0)
    c.key('End')
    end = c.ev("""(() => { const p = document.querySelector('.sel-pop'); const a = p.querySelector('.active');
      const pr = p.getBoundingClientRect(), ar = a.getBoundingClientRect();
      return { in: ar.top >= pr.top - 0.5 && ar.bottom <= pr.bottom + 0.5, label: a.textContent,
               atEnd: p.scrollTop + p.clientHeight >= p.scrollHeight - 1 }; })()""")
    r.check('End scrolls to the last row and keeps it visible', end['in'] and end['label'] == 'Item 300' and end['atEnd'], end)
    c.key('PageUp')
    up = c.pop()
    r.check('PageUp moves up by a page, not by one', up['active'] < 299 - 5, up['active'])
    c.key('Home')
    home = c.ev("document.querySelector('.sel-pop').scrollTop")
    r.check('Home scrolls back to the top', home == 0 and c.pop()['active'] == 0, home)
    for _ in range(3):
        c.key('ArrowDown')
    c.key('Enter')
    r.check('keyboard pick from a scrolled list lands on the highlighted row',
            c.ev("document.getElementById('dd-test').value") == 'v4')
    # Wheel scrolling inside the list does not scroll the page behind it.
    pop = open_by_click(c, '#dd-test')
    px = (pop['left'] + pop['right']) / 2
    py = (pop['top'] + pop['bottom']) / 2
    c.send('Input.dispatchMouseEvent', type='mouseWheel', x=px, y=py, deltaX=0, deltaY=120)
    c.settle()
    r.check('the mouse wheel scrolls the list', c.pop() is not None and c.pop()['scrollTop'] > 0, c.pop() and c.pop()['scrollTop'])
    close_list(c)

    make_playground(c, 2000)
    t = c.ev("""(() => { const s = document.getElementById('dd-test'); s.selectedIndex = 1000;
      const t0 = performance.now();
      s.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0 }));
      const t1 = performance.now();
      const n = document.querySelectorAll('.sel-opt').length;
      return { ms: t1 - t0, n }; })()""")
    ctx['open_2000_ms'] = t['ms']
    r.check(f"a 2,000-row list builds and lays out in {t['ms']:.0f} ms", t['n'] == 2000 and t['ms'] < 400, t)
    close_list(c)
    c.ev("document.getElementById('dd-box').remove()")


def s_more_menu(c: Chrome, r: Report, ctx):
    r.start('the More menu stays open while a select inside it is used')
    prepare(c, 'clip-side')
    shown = c.ev("!document.getElementById('defaults-menu').hidden")
    r.check('the menu is open', bool(shown))
    start = c.ev("document.getElementById('clip-side').selectedIndex")
    pop = open_by_click(c, '#clip-side')
    r.check('the list opens and the menu is still open',
            bool(pop) and c.ev("!document.getElementById('defaults-menu').hidden"))
    x, y = c.row_xy(1 - start)
    c.click(x, y)
    r.check('clicking a row picks it and leaves the menu open',
            c.ev("document.getElementById('clip-side').selectedIndex") == 1 - start
            and c.ev("!document.getElementById('defaults-menu').hidden"))
    # Press on the select, drag to a row, release: the click lands on a common ancestor.
    cx, cy, *_ = c.center('#clip-side')
    c.press(cx, cy)
    popped = c.pop()
    rx, ry = c.row_xy(start)
    c.move(rx, ry, buttons=1)
    c.release(rx, ry)
    c.settle()
    c.ev("1")
    r.check('press, drag onto a row and release picks it and leaves the menu open',
            bool(popped) and c.pop() is None and c.ev("document.getElementById('clip-side').selectedIndex") == start
            and c.ev("!document.getElementById('defaults-menu').hidden"))
    # A click on a heading or padding inside the list does not close the menu either.
    open_by_click(c, '#clip-side')
    pop = c.pop()
    px = pop['left'] + 1.5
    py = (pop['top'] + pop['bottom']) / 2
    c.click(px, py)
    r.check('a click on the list\'s padding keeps the list and the menu',
            c.pop() is not None and c.ev("!document.getElementById('defaults-menu').hidden"))
    close_list(c)
    # The control: the menu really does close on a click outside it.
    c.click(ctx['width'] / 2, 130)
    r.check('control: a click outside the menu closes it',
            c.ev("document.getElementById('defaults-menu').hidden"))


def s_dismiss(c: Chrome, r: Report, ctx):
    r.start('dismissing a list: outside presses, other controls, the select itself')
    prepare(c, 'pathway')
    # What the 3D view's canvas would have seen.
    c.ev("""(() => { window.__canvas = { pointerdown: 0, click: 0 };
      const cv = document.querySelector('#viewport canvas') || document.querySelector('#viewport');
      cv.addEventListener('pointerdown', () => window.__canvas.pointerdown++);
      cv.addEventListener('click', () => window.__canvas.click++); })()""")
    open_by_click(c, '#pathway')
    cx, cy = ctx['width'] * 0.6, 200
    c.click(cx, cy)
    seen = c.ev('window.__canvas')
    r.check('a press on the 3D view only dismisses the list: no orbit, no pick',
            c.pop() is None and seen == {'pointerdown': 0, 'click': 0}, seen)
    c.click(cx, cy)
    seen = c.ev('window.__canvas')
    r.check('the next click on the 3D view is a normal one (the swallow did not outlive the gesture)',
            seen['pointerdown'] == 1 and seen['click'] == 1, seen)
    # A button: one click is enough and it works.
    prepare(c, 'pathway')
    open_by_click(c, '#pathway')
    c.ev("window.__btn = 0; document.getElementById('toggle-display').addEventListener('click', () => window.__btn++)")
    x, y, *_ = c.center('#toggle-display')
    c.click(x, y)
    r.check('a button works in one click and the list closes', c.pop() is None and c.ev('window.__btn') == 1)
    c.ev("document.getElementById('toggle-display').click()")
    # Another select: its own list opens in the same click.
    prepare(c, 'pathway')
    open_by_click(c, '#pathway')
    x, y, *_ = c.center('#render-method')
    c.click(x, y)
    pop = c.pop()
    r.check('a press on another select closes the first list and opens that one\'s in one click',
            bool(pop) and pop['count'] == 1 and pop['n'] == 4 and c.ev("document.querySelector('[aria-expanded=true]').id") == 'render-method', pop and pop['n'])
    # The select itself toggles.
    x, y, *_ = c.center('#render-method')
    c.click(x, y)
    r.check('a press on the open select closes its list', c.pop() is None)
    # Escape, resize, scroll.
    open_by_click(c, '#render-method')
    c.send('Emulation.setDeviceMetricsOverride', width=ctx['width'] - 40, height=ctx['height'], deviceScaleFactor=1,
           mobile=False, screenWidth=2560, screenHeight=1600)
    c.settle(3)
    r.check('a window resize closes the list', c.pop() is None)
    open_by_click(c, '#render-method')
    c.send('Input.dispatchMouseEvent', type='mouseWheel', x=ctx['width'] * 0.7, y=300, deltaX=0, deltaY=100)
    c.settle()
    r.check('the mouse wheel over the page behind it (zooming the render) closes the list', c.pop() is None)
    c.send('Emulation.setDeviceMetricsOverride', width=ctx['width'], height=ctx['height'], deviceScaleFactor=1,
           mobile=False, screenWidth=2560, screenHeight=1600)
    c.settle(2)
    # Right button outside: closes, and is not swallowed.
    open_by_click(c, '#render-method')
    c.press(cx, cy, 'right')
    c.release(cx, cy, 'right')
    c.settle()
    r.check('a right-click outside closes the list', c.pop() is None)
    c.ev("document.getElementById('ctx-menu')?.remove()")
    # A press on the list's own border or scrollbar, drifting onto a row: a scroll, not a pick.
    make_playground(c, options=40)
    set_value(c, 'dd-test', 'v1')
    x, y, *_ = c.center('#dd-test')
    c.click(x, y)
    rect = c.ev("(() => { const r = document.querySelector('.sel-pop').getBoundingClientRect();"
                " return [r.left, r.top, r.width, r.height]; })()")
    rx, ry = c.row_xy(2)
    c.press(rect[0] + 1, ry)
    c.release(rx, ry)
    c.settle()
    r.check('a press on the list\'s border released over a row picks nothing and leaves the list open',
            c.pop() is not None and c.ev("document.getElementById('dd-test').value") == 'v1')
    c.press(rx, ry)
    c.release(rx, ry)
    c.settle()
    r.check('a press that began on a row picks it',
            c.pop() is None and c.ev("document.getElementById('dd-test').value") == 'v3')
    c.ev("document.getElementById('dd-box')?.remove()")
    prepare(c, 'pathway')


def s_live(c: Chrome, r: Report, ctx):
    r.start('the page changing under an open list')
    prepare(c, 'pathway')
    make_playground(c, 5)
    open_by_click(c, '#dd-test')
    c.key('ArrowDown')
    c.key('ArrowDown')
    before = c.pop()
    # What fillCompareCells(), replaceChildren() and select.add() do to a select.
    c.ev("document.getElementById('dd-test').add(new Option('Late addition', 'late'))")
    c.settle(3)
    after = c.pop()
    r.check('an option added while the list is open appears in it, keeping the highlight',
            bool(after) and after['n'] == 6 and after['labels'][-1] == 'Late addition' and after['active'] == before['active'],
            after and [after['n'], after['active'], before['active']])
    c.ev("document.getElementById('dd-test').replaceChildren(new Option('New A', 'x1'), new Option('Item 3', 'v3'), new Option('New C', 'x3'))")
    c.settle(3)
    after = c.pop()
    r.check('replaceChildren rebuilds the list and keeps the highlight by value',
            bool(after) and after['labels'] == ['New A', 'Item 3', 'New C'] and after['active'] == 1, after and [after['labels'], after['active']])
    c.log()
    c.key('Enter')
    seen = [e for e in c.log() if e['type'] in ('input', 'change')]
    r.check('a pick after a rebuild lands on the highlighted row of the new options, once',
            [e['type'] for e in seen] == ['input', 'change'] and seen[-1]['value'] == 'v3', seen)
    # Disabled while open: the list closes.
    fill_playground(c, 5)
    open_by_click(c, '#dd-test')
    c.ev("document.getElementById('dd-test').disabled = true")
    c.settle(3)
    r.check('disabling the select closes its list', c.pop() is None)
    c.ev("document.getElementById('dd-test').disabled = false")
    # Removed while open: the list closes.
    open_by_click(c, '#dd-test')
    c.ev("document.getElementById('dd-test').remove()")
    c.settle(3)
    r.check('removing the select closes its list', c.pop() is None and c.ev("!document.querySelector('.sel-live')"))
    # Scrolling or reflowing the page under it closes it.
    make_playground(c, 5)
    open_by_click(c, '#dd-test')
    c.ev("document.getElementById('dd-box').style.top = '90px'")
    c.settle(3)
    r.check('moving the select closes the list', c.pop() is None)
    # A select that is disabled never opens ours.
    c.ev("document.getElementById('dd-test').disabled = true")
    x, y, *_ = c.center('#dd-test')
    c.click(x, y)
    r.check('a disabled select opens no list', c.pop() is None)
    # An options-less select that gets options later.
    c.ev("(() => { const s = document.getElementById('dd-test'); s.disabled = false; s.replaceChildren(); })()")
    x, y, *_ = c.center('#dd-test')
    c.click(x, y)
    r.check('an empty select opens no list', c.pop() is None)
    c.ev("document.getElementById('dd-test').add(new Option('First', 'f'))")
    x, y, *_ = c.center('#dd-test')
    c.click(x, y)
    r.check('options added later make the same select open a list (nothing is bound to the element)',
            bool(c.pop()) and c.pop()['labels'] == ['First'])
    close_list(c)
    # A select with size or multiple stays native.
    c.ev("(() => { const s = document.getElementById('dd-test'); s.multiple = true; })()")
    x, y, *_ = c.center('#dd-test')
    c.click(x, y)
    r.check('a multiple select is left to the browser', c.pop() is None)
    c.ev("document.getElementById('dd-box').remove()")


def open_cell_panel(c: Chrome) -> bool:
    """Expand the first cell, every section of its panel, and switch on the features that add selects."""
    show_view(c, 'render')
    if not c.ev("!!document.querySelector('.neuron-item.expanded .cell-panel')"):
        spot = c.center('.neuron-item .chev')
        if not spot:
            return False
        c.click(spot[0], spot[1])
        if not c.wait_for("!!document.querySelector('.cell-panel')", 10):
            return False
    expand = "document.querySelectorAll('.cell-panel .section-head[aria-expanded=\"false\"]').forEach((e) => e.click())"
    c.ev(expand)
    for name in ('Annotation wireframe', 'Synapses'):
        c.ev("""(() => { const s = [...document.querySelectorAll('.cell-section')]
          .find((x) => x.querySelector('.sec-name').textContent === %s);
          const b = s && s.querySelector('input[type=checkbox]'); if (b && !b.checked) b.click(); })()""" % json.dumps(name))
        time.sleep(0.6)
        c.ev(expand)
    c.settle(3)
    return True


def cell_selects(c: Chrome):
    return c.ev("""[...document.querySelectorAll('.cell-panel select')].map((s, i) => ({
      i, n: [...s.options].filter((o) => !o.hidden).length, first: s.options[0] ? s.options[0].text : '',
      titles: [...s.options].filter((o) => o.title).length }))""")


def tag_cell_select(c: Chrome, i: int) -> str:
    c.ev("document.querySelectorAll('[data-ddi]').forEach((s) => delete s.dataset.ddi);"
         "document.querySelectorAll('.cell-panel select')[%d].dataset.ddi = 'x'" % i)
    return '.cell-panel select[data-ddi="x"]'


def s_cell(c: Chrome, r: Report, ctx):
    r.start('selects the app creates: Render compare, and a loaded cell\'s panel')
    show_view(c, 'compare')
    heads = c.ev("document.querySelectorAll('#compare-grid .compare-head select').length")
    r.check('Render compare built a technique select in each pane header', heads >= 4, heads)
    sel = '#compare-grid .compare-head select'
    open_pane_editor(c)
    pop = open_by_click(c, sel)
    want = c.ev(f"[...document.querySelector('{sel}').options].filter((o) => !o.hidden).length")
    r.check('a pane header select opens a list with all its techniques', bool(pop) and pop['n'] == want, pop and pop['n'])
    r.check('the pane select has data-help on itself; hovering the list does not arm the help panel',
            c.ev("document.querySelector('.help-pop') === null"))
    c.log()
    other = 0 if pop['selected'] != 0 else 1
    x, y = c.row_xy(other)
    c.click(x, y)
    kinds = [e['type'] for e in c.log() if e['type'] in ('input', 'change')]
    r.check('picking a technique in a pane runs the app\'s own change handler once, which rebuilds the header',
            kinds == ['input', 'change'], kinds)
    # More panes, so a Difference pane has several to choose between (it lists only
    # the panes that are not Difference themselves, and disables the one its twin uses).
    pop = open_by_click(c, '#compare-panes')
    x, y = c.row_xy(pop['n'] - 1)
    c.click(x, y)
    panes = c.ev("document.querySelectorAll('#compare-grid .compare-head').length")
    r.check('the pane count select (the last row, by click) rebuilds the grid', panes == int(c.ev("document.getElementById('compare-panes').value")), panes)
    # The Difference technique builds two more selects of its own, in the same header.
    diff_index = c.ev(f"[...document.querySelector('{sel}').options].findIndex((o) => o.value === 'diff')")
    open_pane_editor(c)
    open_by_click(c, sel)
    x, y = c.row_xy(diff_index)
    c.click(x, y)
    inside = c.ev("document.querySelector('#compare-grid .compare-head select').closest('.compare-head')"
                  ".querySelectorAll('select').length")
    r.check('choosing Difference turns the header into three selects (the technique and the two panes)', inside == 3, inside)
    if inside == 3:
        c.ev("document.querySelector('#compare-grid .compare-head select').closest('.compare-head')"
             ".querySelectorAll('select')[1].id = 'dd-diff'")
        pop = open_by_click(c, '#dd-diff')
        r.check('a Difference pane\'s own select opens a list of the other panes',
                bool(pop) and pop['n'] >= 3 and pop['labels'][0].startswith('pane'), pop and pop['labels'])
        enabled = [i for i, off in enumerate(pop['disabled']) if not off and i != pop['selected']]
        r.check('the pane its twin already uses is disabled in the list, as the app\'s own options are',
                any(pop['disabled']), pop['disabled'])
        c.log()
        disabled = pop['disabled'].index(True)
        x, y = c.row_xy(disabled)
        c.click(x, y)
        r.check('clicking that disabled row fires nothing and leaves the list open',
                not [e for e in c.log() if e['type'] in ('input', 'change')] and c.pop() is not None)
        x, y = c.row_xy(enabled[0])
        c.click(x, y)
        seen = [e for e in c.log() if e['type'] in ('input', 'change')]
        r.check('a pick of an enabled row fires input then change once', [e['type'] for e in seen] == ['input', 'change'], seen)
    close_list(c)
    show_view(c, 'render')
    if not ctx.get('cell'):
        r.skip('the cell panel selects (finish, fit, correction, synapse shape), Linked neurons order',
               'no --cell given')
        return
    ok = c.wait_for("document.querySelectorAll('.neuron-item').length > 0", 15)
    r.check('a cell is loaded through the page\'s own form', ok)
    if not ok or not open_cell_panel(c):
        r.check('the cell panel opens', False)
        return
    info = cell_selects(c)
    if ctx['verbose']:
        print('     cell panel selects:', info)
    r.check('the panel holds the four kinds of select the app creates for a cell (finish, fit, shape, correction)',
            len(info) >= 4, info)
    done = 0
    for i in range(len(info)):
        if not open_cell_panel(c):
            break
        now = cell_selects(c)
        if i >= len(now):
            break
        n = now[i]['n']
        label = now[i]['first']
        sel = tag_cell_select(c, i)
        if not c.reachable(sel):
            c.center(sel)
        good = c.reachable(sel)
        pop = open_by_click(c, sel) if good else None
        good = bool(pop) and pop['n'] == n
        detail = pop and pop['n']
        if good and n > 1:
            c.log()
            other = 0 if pop['selected'] != 0 else 1
            x, y = c.row_xy(other)
            c.click(x, y)
            seen = [e for e in c.log() if e['type'] in ('input', 'change')]
            good = [e['type'] for e in seen] == ['input', 'change'] and seen[-1]['idx'] == other
            detail = [e['type'] for e in seen]
        r.check(f'cell-panel select "{label}..." ({n} options): opens, one pick fires input then change', good, detail)
        done += int(good)
        close_list(c)
    r.check('every select in the cell\'s panel was exercised', done == len(info), [done, len(info)])
    r.skip('Linked neurons order select (Analyses)', 'its card needs partner links from Viking')


def s_help(c: Chrome, r: Report, ctx):
    r.start('the help panel and the list')
    prepare(c, 'pathway')
    x, y, *_ = c.center('#pathway')
    c.move(x, y)
    time.sleep(1.2)
    r.check('control: hovering the Method row\'s select shows its help after a deliberate hover',
            c.ev("document.querySelector('.help-pop') !== null"))
    c.click(x, y)
    r.check('pressing the select hides the help and opens the list',
            c.ev("document.querySelector('.help-pop') === null") and c.pop() is not None)
    rx, ry = c.row_xy(2)
    c.move(rx, ry)
    c.move(rx + 1, ry)
    time.sleep(1.3)
    r.check('hovering the rows of a list whose select sits under a long help arms no help',
            c.ev("document.querySelector('.help-pop') === null"))
    close_list(c)
    if not ctx.get('cell'):
        r.skip('rows with a description (the finish list)', 'no --cell given: the finish select lives in a cell\'s panel')
        return
    # A list whose options carry descriptions: the surface finishes in a cell's panel.
    if not open_cell_panel(c):
        r.check('the cell panel opens', False)
        return
    index = next((s['i'] for s in cell_selects(c) if s['titles'] >= 3), None)
    r.check('the finish select has options with descriptions', index is not None)
    if index is None:
        return
    sel = tag_cell_select(c, index)
    c.center(sel)
    pop = open_by_click(c, sel)
    withhelp = [i for i, h in enumerate(pop['helps']) if h]
    r.check('options with a title give their row a data-help (and no title, so no native tooltip)',
            len(withhelp) >= 3 and c.ev("document.querySelectorAll('.sel-pop [title]').length") == 0, len(withhelp))
    i = withhelp[0]
    rx, ry = c.row_xy(i)
    c.move(rx, ry)
    c.move(rx + 1, ry)
    time.sleep(1.2)
    shown = c.ev("(() => { const h = document.querySelector('.help-pop'); return h && { text: h.textContent,"
                 " z: getComputedStyle(h).zIndex, listZ: getComputedStyle(document.querySelector('.sel-pop')).zIndex }; })()")
    r.check('hovering such a row shows that option\'s description, in a layer above the list',
            bool(shown) and shown['text'] == pop['helps'][i] and int(shown['z']) > int(shown['listZ']), shown)
    c.key('Escape')
    r.check('Escape closes the list and the help with it (no stale text)',
            c.pop() is None and c.ev("document.querySelector('.help-pop') === null"))
    # And by picking the row.
    open_by_click(c, sel)
    rx, ry = c.row_xy(i)
    c.move(rx, ry)
    c.move(rx + 1, ry)
    time.sleep(1.2)
    c.click(rx, ry)
    r.check('picking the row whose help is showing leaves no help behind',
            c.pop() is None and c.ev("document.querySelector('.help-pop') === null"))
    # And by pressing outside.
    sel = tag_cell_select(c, index)
    open_by_click(c, sel)
    rx, ry = c.row_xy(i)
    c.move(rx, ry)
    c.move(rx + 1, ry)
    time.sleep(1.2)
    c.click(ctx['width'] * 0.7, 300)
    r.check('dismissing the list with the help showing leaves no help behind either',
            c.pop() is None and c.ev("document.querySelector('.help-pop') === null"))
    close_list(c)


def s_settings(c: Chrome, r: Report, ctx):
    r.start('the Display checkbox, the stored choice, and what an open list does when it is turned off')
    c.ev("localStorage.removeItem('%s')" % STORAGE_KEY)
    prepare(c, 'mat-select')
    box = c.center('#opt-page-selects')
    r.check('the checkbox is reachable in the Display panel', c.reachable('#opt-page-selects'))
    c.click(box[0], box[1])
    stored = c.ev("localStorage.getItem('%s')" % STORAGE_KEY)
    r.check('unticking it stores "native"',
            stored == 'native' and not c.ev("document.getElementById('opt-page-selects').checked"), stored)
    got = synth_mousedown(c, '#mat-select')
    r.check('with it off a press on a select is left to the browser: no list, mousedown not cancelled',
            got == {'cancelled': False, 'open': False}, got)
    box = c.center('#opt-page-selects')
    c.click(box[0], box[1])
    stored = c.ev("localStorage.getItem('%s')" % STORAGE_KEY)
    r.check('ticking it again stores "page" and the list is back',
            stored == 'page' and bool(open_by_click(c, '#mat-select')), stored)
    # Turning it off while a list is open closes it.
    c.ev("document.getElementById('opt-page-selects').checked = false;"
         "document.getElementById('opt-page-selects').dispatchEvent(new Event('change', { bubbles: true }))")
    c.settle()
    r.check('turning it off while a list is open closes the list', c.pop() is None)
    c.ev("document.getElementById('opt-page-selects').checked = true;"
         "document.getElementById('opt-page-selects').dispatchEvent(new Event('change', { bubbles: true }))")
    c.settle()
    r.check('the setting is the only select-related key the page stores',
            c.ev("Object.keys(localStorage).filter((k) => k.includes('select')).join()") == STORAGE_KEY)
    # Another tab changing it is picked up through the storage event.
    c.ev("localStorage.setItem('%s', 'native')" % STORAGE_KEY)
    c.ev("window.dispatchEvent(new StorageEvent('storage', { key: '%s', newValue: 'native' }))" % STORAGE_KEY)
    r.check('a change stored by another tab turns the list off here too (storage event) and unticks the box',
            c.ev("import('/static/js/selectpicker.js').then((m) => m.selectPickerState().mode)", wait=True) == 'native'
            and not c.ev("document.getElementById('opt-page-selects').checked"))
    c.ev("localStorage.setItem('%s', 'page')" % STORAGE_KEY)
    c.ev("window.dispatchEvent(new StorageEvent('storage', { key: '%s', newValue: 'page' }))" % STORAGE_KEY)
    c.ev("localStorage.removeItem('%s')" % STORAGE_KEY)


def fresh_agent(ctx, agent: str, stored: str | None):
    m = Chrome(ctx['chrome'], ctx['width'], ctx['height'])
    m.emulate(agent, ctx['width'], ctx['height'])
    m.goto(ctx['url'], stored=stored)
    return m


def mode_of(m: Chrome) -> dict:
    return m.ev("import('/static/js/selectpicker.js').then((x) => x.selectPickerState())", wait=True)


def s_agents(c: Chrome, r: Report, ctx):
    """Other browsers: separate Chrome processes, each told it is something else."""
    r.start('macOS Chromium: native by default')
    m = fresh_agent(ctx, 'mac', None)
    try:
        state = mode_of(m)
        r.check('the default is native', state['mode'] == 'native' and state['platform'] == 'macOS', state)
        r.check('the Display checkbox is shown, unticked',
                m.ev("(() => { const b = document.getElementById('opt-page-selects');"
                     " return !b.checked && !b.closest('label').hidden; })()"))
        got = synth_mousedown(m, '#volume')
        r.check('a press on a select is left alone: no list, not cancelled',
                got == {'cancelled': False, 'open': False}, got)
        got = synth_key(m, '#volume', 'ArrowDown')
        r.check('and so are the opening keys', got == {'cancelled': False, 'open': False}, got)
    finally:
        m.close()
    r.start('macOS Chromium with the setting on: the arrows open ours, Enter does not')
    m = fresh_agent(ctx, 'mac', 'page')
    try:
        state = mode_of(m)
        r.check('page mode', state['mode'] == 'page', state)
        m.ev("document.getElementById('volume').focus()")
        m.log()
        m.key('ArrowDown')
        entries = m.log()
        down = next((e for e in entries if e['type'] == 'keydown' and e['key'] == 'ArrowDown'), None)
        r.check('ArrowDown on a closed select opens ours (on macOS it opens the menu) and is cancelled',
                m.pop() is not None and bool(down) and down['dp'], entries)
        m.key('Escape')
        got = synth_key(m, '#volume', 'Enter')
        r.check('Enter does not open it (on macOS Enter does not open the menu) and is not cancelled',
                got == {'cancelled': False, 'open': False}, got)
        m.ev("document.getElementById('volume').focus()")
        m.key(' ')
        r.check('Space opens it', m.pop() is not None)
        m.key('Escape')
        m.ev("document.getElementById('volume').focus()")
        m.key('ArrowDown', ALT)
        r.check('Alt+ArrowDown opens it', m.pop() is not None)
    finally:
        m.close()
    r.start('a browser that is not Chromium keeps its own list, whatever is stored')
    m = fresh_agent(ctx, 'other', 'page')
    try:
        state = mode_of(m)
        r.check('mode is native and the browser is not Chromium',
                state['mode'] == 'native' and not state['chromium'], state)
        r.check('the Display checkbox is hidden',
                m.ev("document.getElementById('opt-page-selects').closest('label').hidden"))
        got = synth_mousedown(m, '#volume')
        r.check('a press opens no list and is not cancelled', got == {'cancelled': False, 'open': False}, got)
    finally:
        m.close()
    r.start('Windows Chromium with "native" stored keeps the browser\'s list')
    m = fresh_agent(ctx, 'windows', 'native')
    try:
        state = mode_of(m)
        r.check('a stored "native" beats the Windows default',
                state['mode'] == 'native' and not m.ev("document.getElementById('opt-page-selects').checked"), state)
        got = synth_mousedown(m, '#volume')
        r.check('and a press is left to the browser', got == {'cancelled': False, 'open': False}, got)
    finally:
        m.close()


def s_touch(c: Chrome, r: Report, ctx):
    r.start('touch: a tap keeps the system picker, even when the in-page list is forced on')
    m = fresh_agent(ctx, 'windows', 'page')
    try:
        x, y, *_ = m.center('#volume')
        m.click(x, y)
        r.check('control: a mouse click opens the list', m.pop() is not None)
        m.key('Escape')
        got = synth_mousedown(m, '#volume', touch=True)
        r.check('after a touch pointerdown the compatibility mousedown opens nothing and is not cancelled',
                got == {'cancelled': False, 'open': False}, got)
        m.click(x, y)
        r.check('and the next mouse click opens the list again (the touch did not stick)', m.pop() is not None)
        m.key('Escape')
        # The real thing, once, with touch emulation: Chrome's own picker is not ours to open.
        m.send('Emulation.setTouchEmulationEnabled', enabled=True, maxTouchPoints=5)
        m.ev("window.__touch = []; document.addEventListener('pointerdown', "
             "(e) => window.__touch.push(e.pointerType), true)")
        m.tap(x, y)
        kinds = m.ev('window.__touch')
        r.check('a real emulated tap reports pointerType "touch"', 'touch' in kinds, kinds)
        r.check('and opens none of our lists', m.pop() is None)
        m.send('Emulation.setTouchEmulationEnabled', enabled=False)
    finally:
        m.close()


def luminance(rgb) -> float:
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def hex_rgb(text: str):
    text = text.strip().lstrip('#')
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, share: float):
    """`share` of a over b, as color-mix(in srgb, a share%, transparent) over b paints it."""
    return tuple(round(x * share + y * (1 - share)) for x, y in zip(a, b, strict=True))


def s_visual(c: Chrome, r: Report, ctx):
    r.start('screenshots and colors: the list is part of the page, closed controls are unchanged')
    from PIL import Image, ImageChops, ImageDraw
    prepare(c, 'pathway')
    clip = c.ev("""(() => { const r = document.querySelector('#panel-render').getBoundingClientRect();
      return { x: Math.max(0, r.left), y: Math.max(0, r.top - 330), width: Math.min(r.width, 700), height: r.height + 330 }; })()""")
    closed = Image.open(io.BytesIO(c.shot(clip))).convert('RGB')
    open_by_click(c, '#pathway')
    pop = c.pop()
    select = c.ev("(() => { const r = document.querySelector('#pathway').getBoundingClientRect();"
                  " return [r.left, r.top, r.right, r.bottom]; })()")
    opened = Image.open(io.BytesIO(c.shot(clip))).convert('RGB')
    to_clip = lambda x, y: (int(x - clip['x']), int(y - clip['y']))  # noqa: E731
    box = (*to_clip(pop['left'], pop['top']), *to_clip(pop['right'], pop['bottom']))
    diff = ImageChops.difference(closed, opened)
    inside = diff.crop(box).getbbox()
    r.check('Page.captureScreenshot shows the list: the screenshot differs inside its rectangle', inside is not None, box)
    # Outside the list and its shadow (20 px blur, 6 px down) only the select may differ:
    # its border turns to the accent while a list is open.
    shadow = (box[0] - 24, box[1] - 24, box[2] + 24, box[3] + 32)
    ImageDraw.Draw(diff).rectangle(shadow, fill=(0, 0, 0))
    rest = diff.getbbox()
    sx0, sy0 = to_clip(select[0] - 3, select[1] - 3)
    sx1, sy1 = to_clip(select[2] + 3, select[3] + 3)
    r.check('opening it changes nothing else on the page except the select\'s accent border',
            rest is None or (rest[0] >= sx0 and rest[1] >= sy0 and rest[2] <= sx1 and rest[3] <= sy1), rest)
    close_list(c)

    # The numbers the design rests on, read from the page's own tokens in both themes.
    for scheme in ('light', 'dark'):
        c.scheme(scheme)
        c.settle(2)
        tokens = c.ev("""(() => { const s = getComputedStyle(document.documentElement);
          return { panel: s.getPropertyValue('--panel'), ink: s.getPropertyValue('--ink'),
                   accent: s.getPropertyValue('--accent'), muted: s.getPropertyValue('--muted') }; })()""")
        panel, ink, accent, muted = (hex_rgb(tokens[k]) for k in ('panel', 'ink', 'accent', 'muted'))
        tint = mix(accent, panel, 0.16)
        bar = contrast(accent, panel)
        text = contrast(ink, tint)
        dim = contrast(muted, panel)
        r.check(f'{scheme}: the active row\'s 2 px accent bar is {bar:.1f}:1 against the panel (non-text minimum 3:1)', bar >= 3, tokens)
        r.check(f'{scheme}: ink on the tinted active row is {text:.1f}:1 (minimum 4.5:1)', text >= 4.5)
        r.check(f'{scheme}: a disabled row\'s muted ink is {dim:.1f}:1 (it is meant to look off, not to vanish)', dim >= 2.5)
    c.scheme('dark')

    # Closed controls: the same pixels with the list on and with the browser's own.
    shots = {}
    for mode in ('page', 'native'):
        c.ev("localStorage.setItem('%s', '%s')" % (STORAGE_KEY, mode))
        c.ev('location.reload()')
        c.wait_for("document.readyState === 'complete'", 20)
        c.wait_for("document.querySelector('#volume').options.length > 0", 20)
        time.sleep(0.5)
        prepare(c, 'pathway')
        region = c.ev("""(() => { const r = document.querySelector('#method-field').getBoundingClientRect();
          const q = document.querySelector('#quick-technique').getBoundingClientRect();
          const v = document.querySelector('#volume').getBoundingClientRect();
          return [r, q, v].map((b) => ({ x: b.left, y: b.top, width: b.width, height: b.height })); })()""")
        shots[mode] = [c.shot(rg) for rg in region]
    r.check('the closed Method, toolbar and Volume selects are pixel-identical with the list on and off',
            shots['page'] == shots['native'])
    c.ev("localStorage.removeItem('%s')" % STORAGE_KEY)
    c.ev('location.reload()')
    c.wait_for("document.querySelector('#volume') && document.querySelector('#volume').options.length > 0", 20)
    time.sleep(0.5)


def s_console(c: Chrome, r: Report, ctx):
    r.start('console')
    c.settle()
    bad = []
    for msg in c.events:
        method = msg.get('method')
        params = msg.get('params', {})
        if method == 'Runtime.exceptionThrown':
            bad.append(('exception', params['exceptionDetails'].get('text', ''), params['exceptionDetails'].get('exception', {}).get('description', '')[:200]))
        elif method == 'Runtime.consoleAPICalled' and params.get('type') in ('error', 'warning'):
            text = ' '.join(str(a.get('value', a.get('description', ''))) for a in params.get('args', []))
            bad.append((params['type'], text[:200]))
        elif method == 'Log.entryAdded' and params['entry'].get('level') == 'error':
            text = params['entry'].get('text', '')
            if 'Failed to load resource' not in text:
                bad.append(('log', text[:200]))
    r.check('no exceptions, console errors or warnings during the whole run', not bad, bad[:5])
    c.events.clear()


# -- screenshot matrix ----------------------------------------------------------------

def load_cell(c: Chrome, cell: str) -> bool:
    """Load a cell through the page's own form and wait for its row."""
    c.ev("document.getElementById('neuron-id').value = %s" % json.dumps(cell))
    c.ev("document.getElementById('load-form').requestSubmit()")
    ok = c.wait_for("document.querySelectorAll('.neuron-item').length > 0", 30)
    c.settle(3)
    return ok


def matrix(c: Chrome, ctx, folder: str):
    """An open list from five places, light and dark, at four widths: the files to look at."""
    os.makedirs(folder, exist_ok=True)
    print(f'\n== screenshot matrix -> {folder}')
    done = []
    for scheme in ('light', 'dark'):
        for width in (900, 1140, 1440, 1920):
            c.emulate('windows', width, 900, scheme)
            c.ev('location.reload()')
            c.wait_for("document.readyState === 'complete'", 30)
            c.wait_for("document.querySelector('#volume') && document.querySelector('#volume').options.length > 0", 30)
            time.sleep(0.6)
            tag = f'{scheme}-{width}'

            def snap(name, tag=tag):
                c.settle(3)
                path = os.path.join(folder, f'{name}-{tag}.png')
                with open(path, 'wb') as out:
                    out.write(c.shot())
                done.append(path)

            prepare(c, 'pathway')
            open_by_click(c, '#pathway')
            snap('method')
            close_list(c)
            prepare(c, 'quick-technique')
            open_by_click(c, '#quick-technique')
            snap('toolbar')
            close_list(c)
            show_view(c, 'compare')
            open_pane_editor(c)
            open_by_click(c, '#compare-grid .compare-head select')
            snap('compare')
            close_list(c)
            show_view(c, 'render')
            if ctx.get('cell') and load_cell(c, ctx['cell']) and open_cell_panel(c):
                # The Correction dropdown went with 2.42.0 (one correction is left): any of the panel's.
                index = next((s['i'] for s in cell_selects(c)), None)
                if index is not None:
                    c.center(tag_cell_select(c, index))
                    open_by_click(c, '.cell-panel select[data-ddi="x"]')
                    snap('cellpanel')
                    close_list(c)
            prepare(c, 'video-quality')
            open_by_click(c, '#video-quality')
            snap('video')
            close_list(c)
            c.ev("document.getElementById('video-dialog').close()")
    print(f'   {len(done)} screenshots')


# -- main -------------------------------------------------------------------------------

SECTIONS = {
    'load': s_load, 'static': s_static, 'keys': s_keys_closed, 'open': s_keys_open,
    'headings': s_headings, 'dialog': s_dialog, 'placement': s_placement, 'more': s_more_menu,
    'dismiss': s_dismiss, 'live': s_live, 'cell': s_cell, 'help': s_help, 'settings': s_settings,
    'agents': s_agents, 'touch': s_touch, 'visual': s_visual,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--url', default='http://127.0.0.1:8765/', help='the running app (default %(default)s)')
    ap.add_argument('--chrome', help='Chrome for Testing binary (default: the Playwright cache)')
    ap.add_argument('--cell', help='load this cell ID through the page first (for the cell-panel selects)')
    ap.add_argument('--only', help='comma-separated sections: ' + ','.join(SECTIONS))
    ap.add_argument('--shots', help='also write the light/dark x 900/1140/1440/1920 screenshot matrix here')
    ap.add_argument('--width', type=int, default=1440)
    ap.add_argument('--height', type=int, default=900)
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    url = args.url if args.url.endswith('/') else args.url + '/'
    chrome = find_chrome(args.chrome)
    wanted = args.only.split(',') if args.only else list(SECTIONS)
    unknown = [w for w in wanted if w not in SECTIONS]
    if unknown:
        sys.exit(f'unknown section(s): {unknown}')
    try:
        build = json.load(urllib.request.urlopen(url + 'api/build', timeout=10))
    except Exception as error:
        sys.exit(f'no app at {url}: {error}')
    print(f'app {build["version"]} assets {build["assets"]}; chrome {os.path.basename(os.path.dirname(chrome))}')

    report = Report(args.verbose)
    ctx: dict = {'url': url, 'chrome': chrome, 'width': args.width, 'height': args.height,
                 'cell': args.cell, 'verbose': args.verbose}
    c = Chrome(chrome, args.width, args.height)
    try:
        c.emulate('windows', args.width, args.height, 'dark')
        c.goto(url)
        if args.cell and not load_cell(c, args.cell):
            sys.exit(f'cell {args.cell} did not load: is it on the server (or the server offline with it)?')
        ctx['static'] = c.ev("[...document.querySelectorAll('select[id]')].map((s) => s.id)")
        ctx['static_n'] = len(ctx['static'])
        for name in wanted:
            SECTIONS[name](c, report, ctx)
        report.start('whole run')
        s_console(c, report, ctx)
        if args.shots:
            matrix(c, ctx, args.shots)
    finally:
        c.close()
    if 'open_2000_ms' in ctx:
        print(f'2,000-row list: {ctx["open_2000_ms"]:.0f} ms to build and lay out')
    return report.summary()


if __name__ == '__main__':
    sys.exit(main())
