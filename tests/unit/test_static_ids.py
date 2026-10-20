"""Every element id the front end looks up exists.

The render panel's 26 controls are generated from the schema at boot, so their
ids no longer appear in ``index.html``; a lookup of one that the schema does not
make (a renamed id, a deleted field) would be a ``null`` at run time, in a
browser, and nowhere else.  This reads every literal ``$('id')`` and
``getElementById('id')`` in ``static/js`` and checks it against the markup, the
schema's legacy ids and the one element made on demand.
"""

from __future__ import annotations

import re
from pathlib import Path

from sbfsem.service.render_schema import FIELDS

STATIC = Path(__file__).resolve().parents[2] / "src" / "sbfsem_web" / "static"

#: Made by app.js when a context menu opens, looked up to close it again.
MADE_AT_RUN_TIME = {"ctx-menu"}

LOOKUP = re.compile(r"""(?:\$|getElementById)\(\s*['"`]([A-Za-z][\w-]*)['"`]\s*\)""")


def test_every_literal_element_lookup_resolves():
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    known = set(re.findall(r'\bid="([^"]+)"', markup))
    known |= {i for f in FIELDS for i in f.ids.values()}
    known |= MADE_AT_RUN_TIME
    lookups: dict[str, set[str]] = {}
    for path in sorted((STATIC / "js").glob("*.js")):
        for match in LOOKUP.finditer(path.read_text(encoding="utf-8")):
            lookups.setdefault(match.group(1), set()).add(path.name)
    assert len(lookups) > 150, "the lookup pattern stopped matching: this test checks nothing"
    missing = {i: sorted(files) for i, files in lookups.items() if i not in known}
    assert not missing, f"looked up but never made: {missing}"


def test_the_schema_ids_are_not_also_written_in_the_markup():
    """A generated control with a twin in index.html would be two elements for
    one id, and ``getElementById`` would answer with whichever came first."""
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    written = set(re.findall(r'\bid="([^"]+)"', markup))
    generated = {i for f in FIELDS for i in f.ids.values()}
    assert not (written & generated), sorted(written & generated)


def test_the_compare_button_describes_the_work_it_starts():
    """The tooltip said Compare "builds every pane ... one after another" long after
    it built layers, up to three panes at a time: its numbers are the code's."""
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    help_text = re.search(r'<button id="compare-build"[^>]*?data-help="([^"]*)"', markup, re.S).group(1)
    layers = (STATIC / "js" / "comparelayers.js").read_text(encoding="utf-8")
    limit = int(re.search(r"export const MAX_WORKING_PANES = (\d+);", layers).group(1))
    words = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    assert f"up to {words[limit]} panes at a time" in help_text, help_text
    assert "every layer that is not up to date" in help_text
    assert "one after another" in help_text and "layers of a pane" in help_text
    assert "every pane that is not up to date" not in help_text
