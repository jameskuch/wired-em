"""The stylesheet's tokens and its narrow-window grid, checked as text.

Nothing here draws a page; each check is the cheapest thing that would have
caught a fault the 2026-10-15 audit found by looking at a screen:

* a ``var(--token)`` nobody defines.  ``--hover`` and ``--border`` were used with
  a dark-theme fallback (``var(--hover, #232b34)``), so in the light theme a
  hovered menu row or the current Skeleton-check row was ink (#16191d) on a dark
  fill: a contrast of 1.23.  The fallback hid it.
* a grid with fewer tracks than children.  ``.layout`` has three children (the
  cell list, the drag handle, the content) and at 720 px or less it had two
  columns, so the handle took the second and the content fell to a second row
  220 px wide and no tall (measured at 700 px: render 220 x 0).
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "sbfsem_web" / "static"
CSS = (STATIC / "css" / "app.css").read_text(encoding="utf-8")


def _block(text: str, opener: str) -> str:
    """The body of the brace block that starts at ``opener`` (braces matched)."""
    start = text.index(opener)
    depth, i = 0, text.index("{", start)
    begin = i + 1
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[begin:i]
        i += 1


def _tokens(body: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))


LIGHT = _tokens(_block(CSS, ":root {"))
DARK = {**LIGHT, **_tokens(_block(_block(CSS, "@media (prefers-color-scheme: dark)"), ":root {"))}


def _luminance(color: str) -> float:
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_token_the_stylesheet_uses_is_defined_somewhere():
    defined = set(re.findall(r"(--[\w-]+)\s*:", CSS))
    for path in sorted((STATIC / "js").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        defined |= set(re.findall(r"setProperty\(\s*['\"`](--[\w-]+)", text))
        defined |= set(re.findall(r"['\"`](--[\w-]+)\s*:", text))  # inline style strings
    used = set(re.findall(r"var\(\s*(--[\w-]+)", CSS))
    assert used, "the pattern stopped matching: this test checks nothing"
    assert not used - defined, f"var() of a token nothing defines: {sorted(used - defined)}"


def test_the_colors_every_page_leans_on_are_defined_in_both_themes():
    for scheme, tokens in (("light", LIGHT), ("dark", DARK)):
        for name in ("--bg", "--panel", "--ink", "--muted", "--line", "--accent", "--hover"):
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", tokens.get(name, "").strip()), f"{name} in {scheme}"


def test_ink_reads_on_a_hovered_row_in_both_themes():
    for scheme, tokens in (("light", LIGHT), ("dark", DARK)):
        ink, hover, panel = (tokens[name].strip() for name in ("--ink", "--hover", "--panel"))
        assert contrast(ink, hover) >= 7, f"{scheme}: ink on a hovered row {contrast(ink, hover):.2f}"
        assert contrast(ink, panel) >= 7, f"{scheme}: ink on the panel {contrast(ink, panel):.2f}"
        assert contrast(hover, panel) >= 1.05, f"{scheme}: a hovered row cannot be told from the panel"


def test_no_var_carries_a_fallback_for_a_token_the_themes_define():
    """A fallback on a defined token is dead; on an undefined one it is the bug above."""
    themed = set(LIGHT)
    for name, fallback in re.findall(r"var\(\s*(--[\w-]+)\s*,\s*(#[0-9a-fA-F]{3,8})", CSS):
        assert name not in themed, f"var({name}, {fallback}): the themes define it, so the fallback is dead"


def _tracks(declaration: str) -> int:
    # Track lists here are plain (``220px 6px 1fr``): no repeat() or minmax(); a var() is one track.
    return len(re.sub(r"var\([^)]*\)", "X", declaration).split())


def _media_bodies(text: str) -> list[tuple[int, str]]:
    """``(width, body)`` of every ``@media (max-width: <width>px)`` block."""
    bodies = []
    for match in re.finditer(r"@media \(max-width:\s*(\d+)px\)\s*\{", text):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        bodies.append((int(match.group(1)), text[match.end():i - 1]))
    return bodies


def test_the_layout_grid_has_a_track_for_every_visible_child():
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    layout = markup[markup.index('<main class="layout">'):]
    children = ("sidebar", "sidebar-resizer", "content")
    for child in children:
        assert re.search(rf'class="{child}[" ]', layout), f"{child} is no longer a child of .layout"

    def count(body: str, visible: int) -> None:
        rule = re.search(r"(?m)^\s*\.layout\s*\{([^}]*)\}", body)
        assert rule, "no .layout rule"
        columns = re.search(r"grid-template-columns:\s*([^;]+);", rule.group(1))
        rows = re.search(r"grid-template-rows:\s*([^;]+);", rule.group(1))
        assert columns
        cells = _tracks(columns.group(1)) * (_tracks(rows.group(1)) if rows else 1)
        assert cells == visible, f"{cells} tracks for {visible} visible children: {body.strip()[:200]}"

    count(CSS, len(children))  # the base rule: list, handle, content
    blocks = _media_bodies(CSS)
    narrow = [(width, body) for width, body in blocks if re.search(r"(?m)^\s*\.layout\s*\{", body)]
    assert narrow, "no narrow-window layout rule: this test checks nothing"
    for width, body in narrow:
        # A window of `width` px or less also matches every wider max-width block.
        hidden = any(re.search(r"\.sidebar-resizer\s*\{[^}]*display:\s*none", other)
                     for other_width, other in blocks if other_width >= width)
        count(body, len(children) - (1 if hidden else 0))


def test_full_screen_without_the_list_gives_the_content_the_one_row_at_480_px():
    """The content is then the layout's only child: left in the ``auto`` row of the
    two-row narrow grid it is 0 px tall (measured at 400 px wide: 400 x 0)."""
    narrow = _block(CSS, "@media (max-width: 480px)")
    assert re.search(r"body\.fullscreen:not\(\.fs-sidebar\)\s+\.layout\s*\{\s*grid-template-rows:\s*1fr;", narrow)


def test_the_skeleton_checks_failure_line_reads_on_the_dark_panel():
    opener = "@media (prefers-color-scheme: dark)"
    blocks = [_block(CSS[start:], opener) for start in (m.start() for m in re.finditer(re.escape(opener), CSS))]
    match = next((m for b in blocks
                  if (m := re.search(r"\.skel-check \.skel-facts\.error\s*\{\s*color:\s*(#[0-9a-fA-F]{6});", b))), None)
    assert match, "the dark theme gives the failure line its own red"
    assert contrast(match.group(1), DARK["--panel"].strip()) >= 4.5
    assert contrast(LIGHT["--danger"].strip(), LIGHT["--panel"].strip()) >= 4.5   # the light theme keeps --danger
