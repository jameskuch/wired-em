"""The schema's show and grey-out rules agree with what was measured.

``tests/fixtures/render_knob_matrix.json`` holds, for rows of (method, pathway,
option), whether changing only that option changed the surface of Inferior c1178
(``tools/measure_knob_matrix.py`` makes it).  A knob that acts must be shown and
usable for that method and pathway; one that does nothing must be hidden or
greyed out.  Measuring needs the cell and minutes of CPU, so it is a tool; this
test is the fast half that keeps the rules honest between measurements.
"""

from __future__ import annotations

import json
from pathlib import Path

from sbfsem.service import render_schema as schema

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "render_knob_matrix.json"

#: Rows where the rule and the measurement disagree today, and why.  Pinned, so a
#: fix to the rule fails this test until the entry is removed.  Empty since the audit
#: package greyed Passes for the Tube under the Original pathway only (C1-F13 of the
#: 2026-10-15 audit): under the others the pathway's own smoothing runs on the tube.
KNOWN_GAPS: set[tuple[str, str, str]] = set()


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]


def _key(row: dict) -> tuple[str, str, str]:
    return row["method"], row["pathway"], row["option"]


def _agrees(row: dict) -> bool:
    state = schema.field_state({"method": row["method"], "pathway": row["pathway"]})[row["option"]]
    if row["acts"]:
        return not state.hidden and not state.inert
    return state.hidden or state.inert


def test_the_matrix_holds_what_2026_10_15_measured():
    rows = _rows()
    assert len({_key(r) for r in rows}) == len(rows), "a row is measured twice"
    for row in rows:
        schema.field_by_key(row["option"])        # a real option
    dead = {_key(r) for r in rows if not r["acts"]}
    assert dead == {
        ("cylinder", "ultraliser", "smooth_iterations"),
        ("cylinder", "ultraliser", "post_order"),
        ("cylinder", "convolution", "voxel_smooth_um"),
        ("hybrid", "ultraliser", "smooth_iterations"),
        ("curvemesh", "original", "smooth_iterations"),
        # Curved links and joined bodies (2026-10-16): Hybrid's discs are the Cylinder build's,
        # which ties its own pieces (Tie gaps).
        ("hybrid", "voxel", "curve_links_um"),
        ("hybrid", "voxel", "curve_tie_um"),
    }
    # Disc avg reaches Hybrid's discs since 2.7.0 (it was dead there on 2026-10-16).
    assert ("hybrid", "voxel", "curve_avg_discs") not in dead
    # Decimation really happened where Order was measured (it was 400,000 faces at the cap).
    for row in rows:
        if row["option"] == "post_order":
            assert row["faces"][0] < 400_000, row


def test_the_rules_show_and_grey_exactly_what_the_measurements_say():
    disagree = {_key(r) for r in _rows() if not _agrees(r)}
    assert disagree == KNOWN_GAPS, (
        f"rules now agree where they did not: {sorted(KNOWN_GAPS - disagree)}; "
        f"rules disagree with a measurement: {sorted(disagree - KNOWN_GAPS)}")
