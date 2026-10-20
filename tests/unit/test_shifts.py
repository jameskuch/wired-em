"""The stages of an annotation's position, and what the section correction did to a cell.

The cell below is built so every number can be checked by hand.  Pixels are
one micron (``volume_scale`` 1000 nm), so a stored position is its um, and the
two moves are written in nm as the node table holds them.  Annotations 1-6 are
a chain on sections 10-15 whose last three sections the current section
transforms pulled 3 um back (a stos edit), after which the correction nudged
annotation 2 and pulled the last three another 1.5 um; 8 is a side branch on
section 14 and 7 a synapse of the cell (a child structure).

======= ===== ================= ================= ================
ID      Z     stored (um)       live move (nm)    correction (nm)
======= ===== ================= ================= ================
1       10    (0.0, 0.0)        -                 -
2       11    (0.1, 0.0)        -                 (0, 200)
3       12    (0.2, 0.0)        -                 -
4       13    (5.0, 0.0)        (-3000, 0)        (-1500, 0)
5       14    (5.1, 0.0)        (-3000, 0)        (-1500, 0)
6       15    (5.2, 0.0)        (-3000, 0)        (-1500, 0)
8       14    (5.1, 1.0)        (-3000, 0)        -
7 child 13    (9.0, 9.0)        (1000, 1000)      (2000, 2000)
======= ===== ================= ================= ================

Links 1-2, 2-3, 3-4, 4-5 and 5-6 run between sections.  5-8 stays on one
section and 4-7 leaves the cell, so neither counts.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sbfsem.analysis import shifts
from sbfsem.core.structure import Neuron

ROOT = Path(__file__).resolve().parents[2]


def make_cell(*, live: bool = True, corr: bool = True, transform: str = "Standard") -> Neuron:
    ids = [1, 2, 3, 4, 5, 6, 8, 7]
    stored = {1: (0.0, 0.0), 2: (0.1, 0.0), 3: (0.2, 0.0), 4: (5.0, 0.0), 5: (5.1, 0.0),
              6: (5.2, 0.0), 8: (5.1, 1.0), 7: (9.0, 9.0)}
    section = {1: 10, 2: 11, 3: 12, 4: 13, 5: 14, 6: 15, 8: 14, 7: 13}
    nodes = pd.DataFrame({
        "ID": ids,
        "ParentID": [44 if i == 7 else 9 for i in ids],
        "VolumeX": [stored[i][0] for i in ids],
        "VolumeY": [stored[i][1] for i in ids],
        "Z": [section[i] for i in ids],
        "Radius": [40.0] * len(ids),
        "X": [0.0] * len(ids), "Y": [0.0] * len(ids),
        "OffEdge": [False] * len(ids), "Terminal": [False] * len(ids),
        "TypeCode": [1] * len(ids),
    })
    edges = pd.DataFrame({"ID": [9] * 7, "A": [1, 2, 3, 4, 5, 5, 4], "B": [2, 3, 4, 5, 6, 8, 7]})
    neuron = Neuron(9, "i", nodes=nodes, edges=edges, volume_scale=(1000.0, 1000.0, 1000.0),
                    omitted_ids=[], data_stamp=datetime(2026, 9, 1, tzinfo=UTC), transform=transform)
    live_nm = {4: (-3000.0, 0.0), 5: (-3000.0, 0.0), 6: (-3000.0, 0.0), 8: (-3000.0, 0.0),
               7: (1000.0, 1000.0)}
    corr_nm = {2: (0.0, 200.0), 4: (-1500.0, 0.0), 5: (-1500.0, 0.0), 6: (-1500.0, 0.0),
               7: (2000.0, 2000.0)}
    if live:
        neuron.nodes["LiveDXnm"] = [live_nm.get(i, (0.0, 0.0))[0] for i in ids]
        neuron.nodes["LiveDYnm"] = [live_nm.get(i, (0.0, 0.0))[1] for i in ids]
        neuron.live_registration = {"n_moved": 4}
    if corr:
        neuron.nodes["CorrDXnm"] = [corr_nm.get(i, (0.0, 0.0))[0] for i in ids]
        neuron.nodes["CorrDYnm"] = [corr_nm.get(i, (0.0, 0.0))[1] for i in ids]
        neuron.section_correction = {"n_moved": 4}
    neuron.apply_transform()
    return neuron


def by_id(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.set_index("ID")


# -- the stages ----------------------------------------------------------------------
def test_the_stages_are_read_back_off_the_corrected_positions():
    neuron = make_cell()
    cell = neuron.nodes[neuron.nodes["ParentID"] == 9]
    frame = by_id(shifts.stage_frame(cell))
    assert sorted(frame.index) == [1, 2, 3, 4, 5, 6, 8]          # the synapse is not the cell's
    # corrected is the cell's own Xum/Yum; registered takes the correction off, stored the live move too.
    np.testing.assert_allclose(frame.loc[4, ["stored_x_um", "stored_y_um"]].astype(float), [5.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(frame.loc[4, ["registered_x_um", "registered_y_um"]].astype(float), [2.0, 0.0],
                               atol=1e-12)
    np.testing.assert_allclose(frame.loc[4, ["corrected_x_um", "corrected_y_um"]].astype(float), [0.5, 0.0],
                               atol=1e-12)
    np.testing.assert_allclose(frame.loc[2, ["stored_x_um", "stored_y_um"]].astype(float), [0.1, 0.0], atol=1e-12)
    np.testing.assert_allclose(frame.loc[2, ["corrected_x_um", "corrected_y_um"]].astype(float), [0.1, 0.2],
                               atol=1e-12)
    # The moves are in um and keep their sign.
    assert frame.loc[4, "live_dx_um"] == pytest.approx(-3.0)
    assert frame.loc[4, "corr_dx_um"] == pytest.approx(-1.5)
    assert frame.loc[2, "corr_dy_um"] == pytest.approx(0.2)
    assert frame.loc[1, ["live_dx_um", "live_dy_um", "corr_dx_um", "corr_dy_um"]].abs().sum() == 0.0


def test_a_cell_without_the_move_columns_has_one_position():
    neuron = make_cell(live=False, corr=False)
    frame = shifts.stage_frame(neuron.nodes)
    for stage in ("stored", "registered"):
        np.testing.assert_array_equal(frame[f"{stage}_x_um"], frame["corrected_x_um"])
        np.testing.assert_array_equal(frame[f"{stage}_y_um"], frame["corrected_y_um"])
    summary = shifts.shift_summary(neuron).as_dict()
    assert summary["applied"] == {"correction": False, "transforms": False}
    assert all(p["n_moved"] == 0 for p in summary["pairs"].values())
    assert summary["coherent_sections"] == []


def test_a_nan_in_a_move_column_is_no_move():
    neuron = make_cell()
    neuron.nodes.loc[neuron.nodes["ID"] == 4, "CorrDXnm"] = np.nan
    frame = by_id(shifts.stage_frame(neuron.nodes))
    assert frame.loc[4, "corr_dx_um"] == 0.0
    assert np.isfinite(frame[["stored_x_um", "registered_x_um", "corrected_x_um"]].to_numpy()).all()


def test_an_unknown_pair_is_refused():
    frame = shifts.stage_frame(make_cell().nodes)
    for call in (lambda: shifts.pair_vectors(frame, "sideways"),
                 lambda: shifts.pair_stats(frame, "sideways"),
                 lambda: shifts.coherent_sections(frame, "sideways"),
                 lambda: shifts.shift_summary(make_cell(), "sideways")):
        with pytest.raises(ValueError, match="sideways"):
            call()


# -- how big ------------------------------------------------------------------------------
def test_pair_stats_count_the_cells_own_annotations_that_moved():
    summary = shifts.shift_summary(make_cell()).as_dict()
    pairs = summary["pairs"]
    # Seven annotations of the cell; the synapse (7) is not among them.
    assert {p["n_annotations"] for p in pairs.values()} == {7}
    # correction: annotation 2 by 0.2 um and 4, 5, 6 by 1.5 um.
    assert pairs["correction"]["n_moved"] == 4
    assert pairs["correction"]["median_um"] == pytest.approx(1.5)
    assert pairs["correction"]["max_um"] == pytest.approx(1.5)
    assert pairs["correction"]["p95_um"] == pytest.approx(np.percentile([0.2, 1.5, 1.5, 1.5], 95))
    # transforms: 4, 5, 6 and 8, each 3 um.
    assert pairs["transforms"]["n_moved"] == 4
    assert pairs["transforms"]["median_um"] == pytest.approx(3.0)
    assert pairs["transforms"]["max_um"] == pytest.approx(3.0)
    # both: the two add where they share an annotation (4.5 um), 8 keeps its 3 um, 2 its 0.2.
    assert pairs["both"]["n_moved"] == 5
    assert pairs["both"]["max_um"] == pytest.approx(4.5)
    assert pairs["both"]["median_um"] == pytest.approx(4.5)
    assert summary["applied"] == {"correction": True, "transforms": True}


def test_pair_vectors_are_the_columns_not_a_difference_of_positions():
    nodes = make_cell().nodes
    frame = shifts.stage_frame(nodes[nodes["ParentID"] == 9])
    row = list(frame["ID"]).index(4)
    # Exactly the columns (nm / 1000), with no rounding from subtracting two positions.
    np.testing.assert_array_equal(shifts.pair_vectors(frame, "correction")[row], [-1.5, 0.0])
    np.testing.assert_array_equal(shifts.pair_vectors(frame, "transforms")[row], [-3.0, 0.0])
    np.testing.assert_array_equal(shifts.pair_vectors(frame, "both")[row], [-4.5, 0.0])


def test_a_sub_half_nanometer_move_is_not_counted():
    frame = shifts.stage_frame(make_cell(live=False).nodes)
    frame.loc[frame["ID"] == 1, "corr_dx_um"] = 0.0004      # 0.4 nm
    frame.loc[frame["ID"] == 3, "corr_dx_um"] = 0.0006      # 0.6 nm
    stats = shifts.pair_stats(frame[frame["ID"].isin([1, 2, 3])], "correction")
    assert stats["n_moved"] == 2        # annotation 2 (0.2 um) and 3; 1 is below the threshold


# -- did it help --------------------------------------------------------------------------------
def test_link_steps_at_each_stage_leave_out_children_and_same_section_links():
    links = shifts.link_steps(make_cell())
    assert list(zip(links["A"], links["B"], strict=True)) == [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]
    assert list(zip(links["zA"], links["zB"], strict=True)) == [(10, 11), (11, 12), (12, 13), (13, 14), (14, 15)]
    np.testing.assert_allclose(links["stored_um"], [0.1, 0.1, 4.8, 0.1, 0.1], atol=1e-12)
    np.testing.assert_allclose(links["registered_um"], [0.1, 0.1, 1.8, 0.1, 0.1], atol=1e-12)
    np.testing.assert_allclose(links["corrected_um"], [np.hypot(0.1, 0.2)] * 2 + [0.3, 0.1, 0.1], atol=1e-12)


def test_the_link_stage_statistics():
    summary = shifts.shift_summary(make_cell()).as_dict()
    assert summary["links"]["n_links"] == 5
    stages = summary["links"]["stages"]
    assert stages["stored"]["median_um"] == pytest.approx(0.1)
    assert stages["stored"]["p90_um"] == pytest.approx(np.percentile([0.1, 0.1, 0.1, 0.1, 4.8], 90))
    assert stages["stored"]["max_um"] == pytest.approx(4.8)
    assert stages["stored"]["n_over_1p5_um"] == 1
    assert stages["registered"]["max_um"] == pytest.approx(1.8)
    assert stages["registered"]["n_over_1p5_um"] == 1       # 1.8 um is still over
    assert stages["corrected"]["max_um"] == pytest.approx(0.3)
    assert stages["corrected"]["median_um"] == pytest.approx(np.hypot(0.1, 0.2))
    assert stages["corrected"]["n_over_1p5_um"] == 0


def test_the_verdict_counts_shorter_longer_and_within_five_nanometers():
    cell = make_cell()
    correction = shifts.shift_summary(cell, "correction").as_dict()["links"]["verdict"]
    # 3-4: 1.8 -> 0.3 shrank; 1-2 and 2-3: 0.1 -> 0.224 grew; 4-5 and 5-6 stayed 0.1.
    assert (correction["pair"], correction["before"], correction["after"]) == (
        "correction", "registered", "corrected")
    assert (correction["n_links"], correction["n_shrank"], correction["n_grew"],
            correction["n_within_5nm"]) == (5, 1, 2, 2)
    assert correction["share_shrank"] == pytest.approx(0.2)
    assert correction["share_grew"] == pytest.approx(0.4)
    assert correction["share_within_5nm"] == pytest.approx(0.4)
    transforms = shifts.shift_summary(cell, "transforms").as_dict()["links"]["verdict"]
    assert (transforms["before"], transforms["after"]) == ("stored", "registered")
    assert (transforms["n_shrank"], transforms["n_grew"], transforms["n_within_5nm"]) == (1, 0, 4)
    both = shifts.shift_summary(cell, "both").as_dict()["links"]["verdict"]
    assert (both["before"], both["after"]) == ("stored", "corrected")
    assert (both["n_shrank"], both["n_grew"], both["n_within_5nm"]) == (1, 2, 2)


def test_a_step_that_moves_less_than_five_nanometers_is_within():
    links = pd.DataFrame({"registered_um": [1.0, 1.0, 1.0, 1.0], "corrected_um": [1.004, 0.996, 1.006, 0.994],
                          "stored_um": [1.0] * 4})
    verdict = shifts.link_verdict(links, "correction")
    assert (verdict["n_grew"], verdict["n_shrank"], verdict["n_within_5nm"]) == (1, 1, 2)
    empty = shifts.link_verdict(links.iloc[:0], "correction")
    assert empty["n_links"] == 0 and empty["share_shrank"] == 0.0 and empty["share_within_5nm"] == 0.0
    assert shifts.link_stats([]) is None


def test_a_cell_with_only_the_transforms_applied():
    summary = shifts.shift_summary(make_cell(corr=False), "transforms").as_dict()
    assert summary["applied"] == {"correction": False, "transforms": True}
    assert summary["pairs"]["correction"]["n_moved"] == 0
    assert summary["pairs"]["transforms"]["n_moved"] == 4
    assert summary["pairs"]["both"] == summary["pairs"]["transforms"]
    stages = summary["links"]["stages"]
    assert stages["registered"] == stages["corrected"]
    assert stages["stored"]["max_um"] == pytest.approx(4.8)
    assert stages["registered"]["max_um"] == pytest.approx(1.8)
    assert summary["links"]["verdict"]["n_shrank"] == 1


def test_a_cell_loaded_without_the_transform_has_no_stages_to_tell_apart():
    """transform="None" leaves pixels in Xum/Yum; subtracting a nanometre move from
    them would mix units, so the moves do not apply to such a cell."""
    cell = make_cell(transform="None")
    assert not shifts.moves_apply(cell)
    frame = shifts.stage_frame(shifts._cell(cell))
    assert (frame["registered_x_um"] == frame["corrected_x_um"]).all()
    assert (frame["stored_x_um"] == frame["corrected_x_um"]).all()
    summary = shifts.shift_summary(cell).as_dict()
    assert summary["applied"] == {"correction": False, "transforms": False}
    assert summary["pairs"]["both"]["n_moved"] == 0
    from sbfsem.service.facade import SbfsemService

    svc = SbfsemService(offline=True)
    svc.add_neuron(cell)
    assert all(n.shift_um is None and n.live_shift_um is None for n in svc.nodes(9, "i"))


def test_a_cell_with_no_links_has_no_link_statistics():
    neuron = make_cell()
    neuron.edges = neuron.edges.iloc[:0]
    summary = shifts.shift_summary(neuron).as_dict()
    assert summary["links"]["n_links"] == 0
    assert summary["links"]["stages"] == {"stored": None, "registered": None, "corrected": None}
    assert summary["links"]["verdict"]["n_links"] == 0
    assert len(shifts.steps(neuron)) == 0


def test_steps_is_the_step_along_each_link_at_the_current_positions():
    steps = shifts.steps(make_cell())
    assert list(steps.columns) == ["A", "B", "zA", "zB", "step_um"]
    np.testing.assert_allclose(steps["step_um"], shifts.link_steps(make_cell())["corrected_um"], atol=1e-12)
    # Positions at any other stage are the cell's own again once the moves are taken off.
    plain = shifts.steps(make_cell(live=False, corr=False))
    np.testing.assert_allclose(plain["step_um"], [0.1, 0.1, 4.8, 0.1, 0.1], atol=1e-12)


def test_the_tool_prints_the_modules_numbers():
    spec = importlib.util.spec_from_file_location("section_corrections_tool", ROOT / "tools" / "section_corrections.py")
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.steps is shifts.steps
    cell = make_cell()
    line = tool.describe("after", tool.steps(cell)["step_um"])
    stats = shifts.link_stats(shifts.link_steps(cell)["corrected_um"])
    assert stats is not None
    assert f"median {stats['median_um']:.3f}" in line and f"p90 {stats['p90_um']:.3f}" in line
    assert f"p99 {stats['p99_um']:.3f}" in line and f"max {stats['max_um']:.3f}" in line
    assert f"({stats['n_over_1p5_um']} over 1.5 um)" in line


# -- sections that moved together -------------------------------------------------------------------
def coherent_cell(rows: dict[int, list[tuple[float, float]]]) -> pd.DataFrame:
    """Annotations per section with the given corrections (nm) at Xum = Yum = 0."""
    records = []
    for section, moves in rows.items():
        for dx, dy in moves:
            records.append({"ID": len(records) + 1, "Z": section, "Xum": 0.0, "Yum": 0.0,
                            "CorrDXnm": dx, "CorrDYnm": dy})
    return shifts.stage_frame(pd.DataFrame(records))


def test_coherent_sections_need_enough_annotations_one_direction_and_a_long_mean():
    frame = coherent_cell({
        20: [(500, 100)] * 5,                       # all one way, mean 0.51 um: coherent
        21: [(500, 0), (-500, 0)] * 2 + [(500, 0)],  # cancel: resultant 0.2
        22: [(1000, 0)] * 3,                        # three annotations: too few
        23: [(150, 0)] * 4,                         # one way but a 0.15 um mean: too short
        24: [(600, 0)] * 3 + [(0, 0)],              # an unmoved annotation counts in n and the mean: 0.45 um
        25: [(500, 0)] * 4 + [(-500, 0)] * 2,       # resultant 0.33
        26: [(500, 0)] * 4 + [(-100, 0)],           # resultant 0.905, mean 0.38 um
        27: [(0, 0)] * 6,                           # nothing moved
    })
    found = shifts.coherent_sections(frame, "correction")
    assert [c["section"] for c in found] == [20, 24, 26]            # strongest mean first
    top = found[0]
    assert top["n"] == 5
    assert top["mean_dx_um"] == pytest.approx(0.5) and top["mean_dy_um"] == pytest.approx(0.1)
    assert top["mean_um"] == pytest.approx(np.hypot(0.5, 0.1))
    assert top["resultant"] == pytest.approx(1.0)
    assert found[1]["n"] == 4 and found[1]["mean_um"] == pytest.approx(0.45)
    assert found[1]["resultant"] == pytest.approx(1.0)
    assert found[2]["resultant"] == pytest.approx(1.9 / 2.1)
    assert found[2]["mean_um"] == pytest.approx(0.38)
    # The thresholds are arguments.
    assert [c["section"] for c in shifts.coherent_sections(frame, "correction", min_n=3)] == [22, 20, 24, 26]
    assert [c["section"] for c in shifts.coherent_sections(frame, "correction", min_resultant=0.95)] == [20, 24]
    assert [c["section"] for c in shifts.coherent_sections(frame, "correction", min_mean_um=0.4)] == [20, 24]
    assert shifts.coherent_sections(frame, "transforms") == []        # no live columns


def test_a_stos_edit_shows_as_a_coherent_section_in_the_transforms_pair():
    records = [{"ID": i, "Z": 939, "Xum": 0.0, "Yum": 0.0, "LiveDXnm": -620.0, "LiveDYnm": 650.0}
               for i in range(1, 7)]
    records += [{"ID": 10 + i, "Z": 940, "Xum": 0.0, "Yum": 0.0, "LiveDXnm": 0.0, "LiveDYnm": 0.0}
                for i in range(5)]
    frame = shifts.stage_frame(pd.DataFrame(records))
    (found,) = shifts.coherent_sections(frame, "transforms")
    assert found["section"] == 939 and found["n"] == 6
    assert (found["mean_dx_um"], found["mean_dy_um"]) == pytest.approx((-0.62, 0.65))
    assert found["resultant"] == pytest.approx(1.0)
    assert shifts.coherent_sections(frame, "correction") == []
    assert [c["section"] for c in shifts.coherent_sections(frame, "both")] == [939]


# -- the endpoint ------------------------------------------------------------------------------------------
@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from sbfsem_web.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        app.state.service.offline = True
        yield test_client


def test_the_endpoint_answers_from_the_copy_the_page_names(client):
    both, live_only = make_cell(), make_cell(corr=False)
    both.correction_status = {"requested": True, "applied": True, "mode": "curvefit"}
    both.registration_status = {"requested": True, "applied": True}
    live_only.registration_status = {"requested": True, "applied": True}
    service = client.app.state.service
    service.add_neuron(both)
    service.add_neuron(live_only)             # the latest copy; "off.1"

    body = client.get("/api/neurons/i/9/shift-summary").json()
    assert body["neuron_id"] == 9 and body["pair"] == "correction"
    assert body["applied"] == {"correction": False, "transforms": True}      # the latest copy
    named = client.get("/api/neurons/i/9/shift-summary?copy=curvefit.1").json()
    assert named["applied"] == {"correction": True, "transforms": True}
    assert named["pairs"]["correction"]["n_moved"] == 4
    assert named["links"]["n_links"] == 5
    assert named["links"]["verdict"]["n_shrank"] == 1 and named["links"]["verdict"]["n_grew"] == 2
    assert named["links"]["stages"]["stored"]["n_over_1p5_um"] == 1
    assert set(named) == {"neuron_id", "source", "pair", "applied", "pairs", "links", "coherent_sections"}
    assert set(named["pairs"]) == {"correction", "transforms", "both"}
    assert set(named["pairs"]["both"]) == {"n_annotations", "n_moved", "median_um", "p95_um", "p99_um", "max_um"}

    # The pair picks the verdict and the coherent sections.
    transforms = client.get("/api/neurons/i/9/shift-summary?copy=curvefit.1&pair=transforms").json()
    assert transforms["pair"] == "transforms"
    assert (transforms["links"]["verdict"]["before"], transforms["links"]["verdict"]["after"]) == (
        "stored", "registered")
    assert client.get("/api/neurons/i/9/shift-summary?pair=sideways").status_code == 422


def test_the_endpoint_says_when_a_cell_is_not_loaded(client):
    assert client.get("/api/neurons/i/987654/shift-summary").status_code == 404
    service = client.app.state.service
    cell = make_cell()
    service.add_neuron(cell)
    assert client.get("/api/neurons/i/9/shift-summary?copy=curvefit.1").status_code == 404
    assert client.get("/api/neurons/i/9/shift-summary").status_code == 200
