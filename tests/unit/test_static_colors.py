"""The Network tab's link-type colors against the 3D markers' (audit 2026-10-15, W6-F23).

A synapse type has one color in the 3D render, the server's table
(``sbfsem.core.types._STRUCTURE_COLORS``, sent with every synapse), and another
copy of that table in ``static/js/network.js`` for the edges of the Network
tab.  Measured at the audit, 6 of the 31 types in both differed.  One was a
slip (ConvPre, a blue channel of 0x40 for 0x4c) and is fixed; the five below are
different in a way that could be a choice -- the Network tab recolors the three
Basal types goldenrod, where the 3D table draws BasalTA in ConvPre's own red --
so they are named here rather than made to agree, for the owner of the colors
to settle.  Every other type must match, so a new type cannot be added to one
table and drift in the other.
"""

from __future__ import annotations

import re
from pathlib import Path

from sbfsem.core.types import _STRUCTURE_COLORS

NETWORK_JS = (Path(__file__).resolve().parents[2] / "src" / "sbfsem_web" / "static" / "js"
              / "network.js").read_text(encoding="utf-8")

#: Types whose Network color is not the 3D marker's, and has not been decided.
KNOWN_DIFFERENT = {"GABAPre", "GABAPost", "BasalTA", "BasalNTA", "BasalMNTA"}


def _network_colors() -> dict[str, str]:
    start = NETWORK_JS.index("const SYNAPSE_COLORS")
    block = NETWORK_JS[start:NETWORK_JS.index("};", start)]
    return {name: color.lower() for name, color in re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{6})'", block)}


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(255 * c):02x}" for c in rgb)


def test_a_type_in_both_tables_has_the_same_color_in_both():
    network = _network_colors()
    assert len(network) > 25, "the table stopped parsing: this test checks nothing"
    markers = {kind.name: _hex(rgb) for kind, rgb in _STRUCTURE_COLORS.items()}
    shared = sorted(set(network) & set(markers))
    assert len(shared) > 20
    differ = {name: (network[name], markers[name]) for name in shared
              if network[name] != markers[name] and name not in KNOWN_DIFFERENT}
    assert not differ, f"Network color vs 3D marker color: {differ}"


def test_the_types_still_listed_as_different_exist_in_both_tables():
    """An entry that no longer exists in one table would hide a type from the check above."""
    network = _network_colors()
    markers = {kind.name for kind in _STRUCTURE_COLORS}
    assert set(network) >= KNOWN_DIFFERENT
    assert markers >= KNOWN_DIFFERENT
