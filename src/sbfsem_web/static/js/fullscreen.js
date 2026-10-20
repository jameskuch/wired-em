// Full screen: telling when the page has the whole screen, and asking for it.
//
// Two different things are called full screen. The browser's own -- F11 on
// Windows and Linux; on macOS ⌃⌘F in Chrome, Edge and Safari, ⌘⇧F (or ⌃⌘F)
// in Firefox -- hides the browser's toolbars around the page. The page's own,
// through the Fullscreen API (the toolbar button, the `f` key), does the same
// from inside. The layout wants to know about both, and only the second one
// announces itself (`fullscreenchange`). The first is inferred: Chromium and
// Firefox flip the `display-mode: fullscreen` media query for it, and failing
// that a viewport the size of the screen is full screen -- a maximized window
// still gives up 80px or more to the browser's own bars.

const query = typeof window.matchMedia === 'function'
  ? window.matchMedia('(display-mode: fullscreen)') : null;

// How much shorter than the screen a full-screen viewport may be: Safari keeps
// its toolbar (~52px) in full screen unless told otherwise, and a notched
// MacBook keeps the camera band (~37px). Every maximized window falls short by
// more than this.
const HEIGHT_SLACK_PX = 56;

export function isFullscreen() {
  if (document.fullscreenElement) return true;
  if (query?.matches) return true;
  const { width, height } = window.screen || {};
  if (!width || !height) return false;
  return window.innerWidth >= width - 2 && window.innerHeight >= height - HEIGHT_SLACK_PX;
}

/** Call `onChange(on)` now and whenever the answer changes; returns a recheck. */
export function watchFullscreen(onChange) {
  let last = null;
  const check = () => {
    const now = isFullscreen();
    if (now === last) return;
    last = now;
    onChange(now);
  };
  document.addEventListener('fullscreenchange', check);
  window.addEventListener('resize', check);
  query?.addEventListener?.('change', check);
  check();
  return check;
}

/** Enter or leave the page's own full screen; null when the browser refused. */
export async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      return false;
    }
    await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
    return true;
  } catch (error) {
    console.warn('full screen refused:', error);
    return null;
  }
}

/** The browser's own full-screen shortcut on this platform, for messages. */
export function browserShortcut() {
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || '');
  if (!mac) return 'F11';
  return /Firefox/.test(navigator.userAgent || '') ? '⌘⇧F' : '⌃⌘F';
}
