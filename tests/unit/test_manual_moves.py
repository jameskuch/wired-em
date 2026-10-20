"""Manual moves (``sbfsem.core.moves``): a person's own corrections after Viking's.

Asked for 2026-10-19: once an annotation's correction can be seen, let it be moved
further by hand, remembered with the registration setting it was made under.  The
service applies a page's move set to a copy of the cell made from the copy loaded
with that setting; nothing is written to Viking.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sbfsem.core.moves import (
    MAX_MOVE_SECTIONS,
    MAX_MOVE_UM,
    ManualMove,
    apply_manual_moves,
    moved_copy,
    moves_token,
    normalize_moves,
)
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.facade import SbfsemService, positions_token, requested_copy


@pytest.fixture
def service(i943_path):
    svc = SbfsemService(offline=True)
    svc.add_neuron(read_legacy_json(i943_path))
    yield svc
    svc.close()


def _own_ids(neuron, n=3):
    own = neuron.nodes[neuron.nodes["ParentID"] == neuron.id]
    return [int(i) for i in own["ID"].head(n)]


# -- the move set ------------------------------------------------------------------------------

def test_a_move_set_is_checked_rounded_and_sorted():
    moves = normalize_moves([
        {"id": 30, "dx_um": 0.123456789, "dy_um": -0.5},
        {"id": "10", "dz": 2},
        {"id": 20, "dx_um": 0, "dy_um": 0, "dz": 0},        # nothing: dropped
        {"id": 30, "dx_um": 0.25},                          # named again: the last wins
    ])
    assert moves == (ManualMove(10, 0.0, 0.0, 2), ManualMove(30, 0.25, 0.0, 0))
    assert normalize_moves(None) == ()
    assert normalize_moves([]) == ()
    # Float noise below 0.1 nm is the same set.
    a = normalize_moves([{"id": 1, "dx_um": 0.1 + 0.2}])
    b = normalize_moves([{"id": 1, "dx_um": 0.3}])
    assert a == b and moves_token(a) == moves_token(b)
    assert normalize_moves([{"id": 1, "dx_um": -0.0, "dy_um": 0.5}])[0].dx_um == 0.0


@pytest.mark.parametrize("bad, words", [
    ({"id": 1, "dx_um": "far"}, "must be a number"),
    ({"id": 1, "dx_um": float("nan")}, "finite"),
    ({"id": 1, "dx_um": MAX_MOVE_UM + 1}, "at most"),
    ({"id": 1, "dz": MAX_MOVE_SECTIONS + 1}, "sections in Z"),
    ({"id": 1, "dz": 0.5}, "whole sections"),
    ({"dx_um": 1}, "Location ID"),
    ("move", "is an object"),
])
def test_a_move_that_cannot_be_applied_is_refused(bad, words):
    with pytest.raises(ValueError, match=words):
        normalize_moves([bad])


def test_too_many_moves_are_refused(monkeypatch):
    import sbfsem.core.moves as m

    monkeypatch.setattr(m, "MAX_MOVES", 3)
    with pytest.raises(ValueError, match="at most 3"):
        m.normalize_moves([{"id": i, "dx_um": 0.1} for i in range(4)])


def test_the_token_names_the_set():
    one = normalize_moves([{"id": 1, "dx_um": 0.5}])
    assert moves_token(()) is None
    assert len(moves_token(one)) == 12
    assert moves_token(one) == moves_token(normalize_moves([{"id": 1, "dx_um": 0.5, "dy_um": 0}]))
    assert moves_token(one) != moves_token(normalize_moves([{"id": 1, "dx_um": 0.6}]))
    assert moves_token(one) != moves_token(normalize_moves([{"id": 1, "dx_um": 0.5, "dz": 1}]))


# -- applying it ------------------------------------------------------------------------------

def test_applied_moves_move_x_y_and_the_section(c943):
    a, b, c = _own_ids(c943)
    before = c943.nodes.set_index("ID")[["Xum", "Yum", "Zum", "Z"]].copy()
    thickness = c943.volume_scale[2] / 1000.0
    moved = moved_copy(c943, normalize_moves([
        {"id": a, "dx_um": 1.5, "dy_um": -0.25},
        {"id": b, "dz": 2},
        {"id": 999999999, "dx_um": 1},                    # not the cell's
    ]))
    after = moved.nodes.set_index("ID")
    assert after.at[a, "Xum"] == pytest.approx(before.at[a, "Xum"] + 1.5, abs=1e-9)
    assert after.at[a, "Yum"] == pytest.approx(before.at[a, "Yum"] - 0.25, abs=1e-9)
    assert after.at[a, "Z"] == before.at[a, "Z"]
    assert after.at[b, "Z"] == before.at[b, "Z"] + 2
    assert after.at[b, "Zum"] == pytest.approx(before.at[b, "Zum"] + 2 * thickness, abs=1e-9)
    assert after.at[b, "ManDZ"] == 2
    assert after.at[c, "Xum"] == before.at[c, "Xum"]
    assert after.at[a, "ManDXnm"] == pytest.approx(1500.0)
    # The original is untouched: the copy has its own table.
    assert "ManDXnm" not in c943.nodes.columns
    pd.testing.assert_frame_equal(c943.nodes.set_index("ID")[["Xum", "Yum", "Zum", "Z"]], before)
    assert c943.manual_moves is None
    summary = moved.manual_moves
    assert summary["n_moves"] == 3 and summary["n_moved"] == 2 and summary["n_unknown"] == 1
    assert summary["unknown"] == [999999999]
    assert summary["n_z"] == 1
    assert summary["max_um"] == pytest.approx(np.hypot(1.5, 0.25))
    assert positions_token(moved).endswith(f"manual:{summary['token']}")
    assert positions_token(c943) is None
    with pytest.raises(ValueError, match="already carries"):
        apply_manual_moves(moved, normalize_moves([{"id": a, "dx_um": 1}]))


def test_a_synapse_annotation_is_not_moved(c943):
    child = c943.nodes[c943.nodes["ParentID"] != c943.id]
    if child.empty:
        pytest.skip("c943's fixture has no child structure annotations")
    sid = int(child["ID"].iloc[0])
    moved = moved_copy(c943, normalize_moves([{"id": sid, "dx_um": 1}]))
    assert moved.manual_moves["n_moved"] == 0 and moved.manual_moves["unknown"] == [sid]


def test_an_outline_moves_with_its_annotation(c943):
    a, b, _ = _own_ids(c943)
    ring = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    c943.geometries = pd.DataFrame({
        "ID": [a, b], "Z": [100, 101], "Zum": [7.0, 7.07], "ParentID": [c943.id, c943.id],
        "Curve": [[ring.copy()], [ring.copy()]],
    })
    moved = moved_copy(c943, normalize_moves([{"id": a, "dx_um": 2, "dy_um": 1, "dz": -1}]))
    rows = moved.geometries.set_index("ID")
    np.testing.assert_allclose(rows.at[a, "Curve"][0], ring + [2, 1])
    assert rows.at[a, "Z"] == 99
    assert rows.at[a, "Zum"] == pytest.approx(7.0 - c943.volume_scale[2] / 1000.0)
    np.testing.assert_allclose(rows.at[b, "Curve"][0], ring)
    # The original outlines are where they were.
    np.testing.assert_allclose(c943.geometries.set_index("ID").at[a, "Curve"][0], ring)


# -- the service ------------------------------------------------------------------------------

def test_a_load_with_moves_answers_from_a_copy_named_by_them(service):
    base = service.neuron(943, "i")
    a, b, _ = _own_ids(base)
    sets = {"off.0": [{"id": a, "dx_um": 0.75}], "curvefit.1": [{"id": b, "dx_um": 9}]}
    detail = service.load_neuron(943, "i", section_corrections="off", live_registration=False,
                                 manual_moves=sets)
    token = moves_token(normalize_moves(sets["off.0"]))
    tag = f"off.0.m{token}"
    assert detail.summary.copy_tag == tag
    assert detail.summary.manual_moves.applied and detail.summary.manual_moves.n_moved == 1
    assert detail.summary.manual_moves.max_um == pytest.approx(0.75)
    assert detail.summary.positions == f"manual:{token}"
    moved = service.neuron(943, "i")
    assert moved is not base and moved.copy_tag == tag and moved.base_serial == base.load_serial
    # Only the set of the setting loaded: b (under curvefit.1) did not move.
    rows = {n.id: n for n in service.nodes(943, "i")}
    assert rows[a].manual_shift_um == pytest.approx([0.75, 0.0, 0.0])
    assert rows[a].manual_dz is None
    assert rows[b].manual_shift_um is None
    with requested_copy("off.0"):
        assert service.neuron(943, "i") is base
        assert {n.id: n for n in service.nodes(943, "i")}[a].manual_shift_um is None

    # The same set again: the same copy, not a new one.
    again = service.load_neuron(943, "i", section_corrections="off", live_registration=False,
                                manual_moves={"off.0": [{"id": a, "dx_um": 0.75, "dy_um": 0}]})
    assert again.summary.copy_tag == tag
    assert service.neuron(943, "i") is moved

    # No set for the setting loaded: the plain copy, which becomes the latest.
    plain = service.load_neuron(943, "i", section_corrections="off", live_registration=False,
                                manual_moves={"curvefit.1": sets["curvefit.1"]})
    assert plain.summary.copy_tag == "off.0"
    assert plain.summary.manual_moves is None
    assert service.neuron(943, "i") is base


def test_a_z_move_is_reported_on_the_node(service):
    a = _own_ids(service.neuron(943, "i"), 1)[0]
    viking = {n.id: n for n in service.nodes(943, "i")}[a].z_section
    service.load_neuron(943, "i", section_corrections="off", live_registration=False,
                        manual_moves={"off.0": [{"id": a, "dz": 3}]})
    row = {n.id: n for n in service.nodes(943, "i")}[a]
    assert row.z_section == viking + 3 and row.manual_dz == 3
    assert row.manual_shift_um[2] == pytest.approx(3 * service.neuron(943, "i").volume_scale[2] / 1000.0)


def test_copies_with_moves_are_bounded_and_go_with_their_base(service):
    base = service.neuron(943, "i")
    a = _own_ids(base, 1)[0]
    tags = []
    for step in range(SbfsemService.MOVED_COPIES + 3):
        d = service.load_neuron(943, "i", section_corrections="off", live_registration=False,
                                manual_moves={"off.0": [{"id": a, "dx_um": 0.1 * (step + 1)}]})
        tags.append(d.summary.copy_tag)
    held = [t for t in service._copies[base_key(base)] if ".m" in t]
    assert len(held) == SbfsemService.MOVED_COPIES
    assert set(held) == set(tags[-SbfsemService.MOVED_COPIES:])
    assert "off.0" in service._copies[base_key(base)]


def base_key(neuron):
    from sbfsem.service.facade import _Key

    return _Key(neuron.source, int(neuron.id))


def test_a_bad_move_set_is_a_value_error(service):
    a = _own_ids(service.neuron(943, "i"), 1)[0]
    with pytest.raises(ValueError, match="at most"):
        service.load_neuron(943, "i", section_corrections="off", live_registration=False,
                            manual_moves={"off.0": [{"id": a, "dx_um": 1e6}]})


def test_the_shift_summary_takes_a_manual_move_off_the_corrected_stage(c943):
    from sbfsem.analysis.shifts import stage_frame

    a = _own_ids(c943, 1)[0]
    moved = moved_copy(c943, normalize_moves([{"id": a, "dx_um": 2.0}]))
    plain = stage_frame(c943.nodes).set_index("ID")
    hand = stage_frame(moved.nodes).set_index("ID")
    assert hand.at[a, "corrected_x_um"] == pytest.approx(plain.at[a, "corrected_x_um"])
    assert hand.at[a, "stored_x_um"] == pytest.approx(plain.at[a, "stored_x_um"])
