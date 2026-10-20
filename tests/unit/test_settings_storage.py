"""Every name the scripts save in the browser has a place in the Settings tab's list.

The Settings tab (``static/js/settings.js``) lists what this site keeps in the
browser by what it is for, and resets what is ticked.  A key saved under a name
its ``STORAGE_GROUPS`` does not know would show only as "Other", with nothing to
say what it holds: this reads every ``..._KEY = 'sbfsem...'`` constant in
``static/js`` and checks each is named there.
"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parents[2] / "src" / "sbfsem_web" / "static" / "js"

CONSTANT = re.compile(r"""\b[A-Z_]*KEY\s*=\s*['"](sbfsem[.:][^'"]+)['"]""")


def test_every_saved_key_is_named_in_the_settings_list():
    settings = (JS / "settings.js").read_text(encoding="utf-8")
    groups = settings.split("export const STORAGE_GROUPS", 1)[1].split("];", 1)[0]
    named = set(re.findall(r"""['"](sbfsem[.:][^'"]+)['"]""", groups))
    found: dict[str, set[str]] = {}
    for path in sorted(JS.glob("*.js")):
        for key in CONSTANT.findall(path.read_text(encoding="utf-8")):
            found.setdefault(key, set()).add(path.name)
    assert len(found) >= 12, f"the key pattern stopped matching: {sorted(found)}"
    missing = {key: sorted(files) for key, files in found.items() if key not in named}
    assert not missing, f"saved in the browser but not named in settings.js STORAGE_GROUPS: {missing}"
    # The per-tab session slots are a family, named by their prefix.
    session = (JS / "session.js").read_text(encoding="utf-8")
    assert "const TAB_PREFIX = `${KEY}.tab.`;" in session and "'sbfsem.session.v1.tab.'" in groups
