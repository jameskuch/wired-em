"""The correction log: annotations the curve fit pulled toward a much wider linked one.

The synthetic cell below can be checked by hand.  Pixels are one micron
(``volume_scale`` 1000 nm), so a stored position and a radius are their um.
A soma (1, r 10) sits at the origin on section 100, with a second soma disc (2)
above it; a thin process leaves it along +x: 3 (r 0.5) at x = 12, then 4, 5 and
the tip 6, 2 um apart.  The correction moves 3 halfway to the soma's center
and its neighbor 4 the same way by about as much; 5 a little, along y; 6 not
at all.  7 (r 1) on another process moves 3 um *away* from the soma, 8 (r 2)
moves 3 um toward it (less than its diameter, 4 um), and 9 (r 2.4, so the soma
is 4.17x as wide) moves 5 um toward it; 10 is a synapse (a child structure)
that moved toward the soma and is not the cell's.

==== ==== ======= ============== ==================== ===================
ID   Z    r (um)  stored (um)    correction (nm)      linked to
==== ==== ======= ============== ==================== ===================
1    100  10      (0, 0)         -                    2, 3, 7, 8, 9, 10
2    101  10      (0, 0)         -                    1
3    100  0.5     (12, 0)        (-6000, 0)           1, 4
4    100  0.5     (14, 0)        (-6200, 300)         3, 5
5    100  0.5     (16, 0)        (0, 400)             4, 6
6    100  0.5     (18, 0)        -                    5
7    100  1       (0, 12)        (0, 3000)            1
8    100  2       (0, -12)       (0, 3000)            1
9    100  2.4     (-12, 0)       (5000, 0)            1
10   100  0.5     (12, 1)        (-6000, 0)           1 (a child)
==== ==== ======= ============== ==================== ===================
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sbfsem.analysis import correction_checks as cc
from sbfsem.analysis.shifts import stage_frame
from sbfsem.core.structure import Neuron

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def make_cell(*, transform: str = "Standard", corrected: bool = True) -> Neuron:
    rows = {  # ID: (Z, r, x, y, dx nm, dy nm, parent)
        1: (100, 10.0, 0.0, 0.0, 0.0, 0.0, 9),
        2: (101, 10.0, 0.0, 0.0, 0.0, 0.0, 9),
        3: (100, 0.5, 12.0, 0.0, -6000.0, 0.0, 9),
        4: (100, 0.5, 14.0, 0.0, -6200.0, 300.0, 9),
        5: (100, 0.5, 16.0, 0.0, 0.0, 400.0, 9),
        6: (100, 0.5, 18.0, 0.0, 0.0, 0.0, 9),
        7: (100, 1.0, 0.0, 12.0, 0.0, 3000.0, 9),
        8: (100, 2.0, 0.0, -12.0, 0.0, 3000.0, 9),
        9: (100, 2.4, -12.0, 0.0, 5000.0, 0.0, 9),
        10: (100, 0.5, 12.0, 1.0, -6000.0, 0.0, 44),
    }
    ids = list(rows)
    nodes = pd.DataFrame({
        "ID": ids,
        "ParentID": [rows[i][6] for i in ids],
        "VolumeX": [rows[i][2] for i in ids],
        "VolumeY": [rows[i][3] for i in ids],
        "Z": [rows[i][0] for i in ids],
        "Radius": [rows[i][1] for i in ids],
        "X": [0.0] * len(ids), "Y": [0.0] * len(ids),
        "OffEdge": [False] * len(ids), "Terminal": [False] * len(ids),
        "TypeCode": [1] * len(ids),
    })
    links = [(1, 2), (1, 3), (3, 4), (4, 5), (5, 6), (1, 7), (1, 8), (9, 1), (1, 10)]
    edges = pd.DataFrame({"ID": [9] * len(links), "A": [a for a, _ in links], "B": [b for _, b in links]})
    neuron = Neuron(9, "i", nodes=nodes, edges=edges, volume_scale=(1000.0, 1000.0, 1000.0),
                    omitted_ids=[], data_stamp=datetime(2026, 10, 20, tzinfo=UTC), transform=transform)
    if corrected:
        neuron.nodes["CorrDXnm"] = [rows[i][4] for i in ids]
        neuron.nodes["CorrDYnm"] = [rows[i][5] for i in ids]
        neuron.section_correction = {"n_moved": 7}
    neuron.apply_transform()
    return neuron


def test_a_thin_process_pulled_halfway_to_the_soma_is_flagged_with_what_moved_with_it():
    found = cc.scan_neuron(make_cell())
    assert found.n_annotations == 9          # the synapse is not the cell's
    assert found.n_moved == 6
    pulls = found.at()                       # 4x by default
    assert [p.location_id for p in pulls] == [3, 9]
    p = pulls[0]
    assert (p.z, p.r_um, p.neighbor_id, p.neighbor_r_um) == (100, 0.5, 1, 10.0)
    assert p.move_um == pytest.approx(6.0)
    assert (p.dx_um, p.dy_um) == pytest.approx((-6.0, 0.0))
    assert p.ratio == pytest.approx(20.0)
    assert p.distance_um == pytest.approx(12.0)
    assert p.frac == pytest.approx(0.5)      # halfway: the bug's signature
    assert p.cos == pytest.approx(1.0)
    # 4 moved within a quarter of 3's move (0.36 um of 6); 5 did not, so the walk stops there.
    assert p.moved_with == (4,)
    assert pulls[1].ratio == pytest.approx(10 / 2.4)
    assert pulls[1].frac == pytest.approx(5 / 12)


def test_a_move_away_from_the_wider_one_or_shorter_than_the_diameter_is_not_a_pull():
    found = cc.scan_neuron(make_cell())
    listed = {p.location_id for p in found.candidates}
    assert 7 not in listed                   # moved 3 um, but away from the soma
    assert 8 not in listed                   # toward it, but 3 um < 2 r = 4 um
    assert 5 not in listed and 6 not in listed


def test_the_ratio_filters_without_a_second_scan():
    found = cc.scan_neuron(make_cell())
    assert [p.location_id for p in found.at(4.0)] == [3, 9]
    assert [p.location_id for p in found.at(4.2)] == [3]
    assert [p.location_id for p in found.at(1.0)] == [3, 9]
    assert found.at(25.0) == []
    # Moved and next to a wider one: 3 (20x), 7 (10x), 8 (5x), 9 (4.17x); 4 and 5
    # are next to as wide (1x) only.
    assert found.n_next_to_wider(4.0) == 4
    assert found.n_next_to_wider(5.0) == 3   # 8 is exactly 5x: "at least", so counted
    assert found.n_next_to_wider(1.0) == 6
    assert found.n_next_to_wider(30.0) == 0


def test_a_cell_without_its_moves_has_nothing_to_check():
    assert cc.scan_neuron(make_cell(corrected=False)).candidates == []
    # Raw pixels in Xum: the moves do not apply (shifts.moves_apply).
    none = cc.scan_neuron(make_cell(transform="None"))
    assert none.candidates == [] and none.n_annotations == 0


def test_a_zero_width_annotation_is_infinitely_narrower():
    neuron = make_cell()
    neuron.nodes.loc[neuron.nodes["ID"] == 3, "Rum"] = 0.0
    p = next(p for p in cc.scan_neuron(neuron).candidates if p.location_id == 3)
    assert np.isinf(p.ratio)
    assert p.as_dict()["ratio"] is None      # JSON has no infinity


def test_links_to_unknown_annotations_and_to_itself_are_ignored():
    frame = stage_frame(make_cell().nodes.query("ParentID == 9"))
    frame["Rum"] = make_cell().nodes.query("ParentID == 9")["Rum"].to_numpy()
    found = cc.scan(frame, np.array([3, 3, 777]), np.array([3, 4, 1]))
    # Only 3-4 is left, and the two are as wide (1x): both moved, so both are next
    # to one as wide.  4 moved toward 3 but 3.1 times past it, and 3 moved away
    # from 4, so neither is a pull; without its link to the soma, 3 is not pulled.
    assert found.n_next_to_wider(1.0) == 2 and found.n_next_to_wider(1.5) == 0
    assert found.candidates == []
    assert cc.scan(frame, np.zeros(0, np.int64), np.zeros(0, np.int64)).candidates == []


def scan_rows(rows: dict, links: list) -> cc.PullScan:
    """``rows``: ID -> (Z, r, registered x, y, correction dx, dy), all um."""
    ids = list(rows)
    frame = pd.DataFrame({
        "ID": ids, "Z": [rows[i][0] for i in ids], "Rum": [rows[i][1] for i in ids],
        "registered_x_um": [rows[i][2] for i in ids], "registered_y_um": [rows[i][3] for i in ids],
        "corr_dx_um": [rows[i][4] for i in ids], "corr_dy_um": [rows[i][5] for i in ids],
    })
    return cc.scan(frame, np.array([a for a, _ in links]), np.array([b for _, b in links]))


def test_a_tip_moved_past_its_thin_neighbor_is_not_a_pull():
    """RC2 c74420's 773079 on production, 2026-10-20: a tip of r 0.035 um, its one
    neighbor (r 0.21 um, 6x) 0.33 um away, moved 1.06 um the same way, 3.1 times as
    far as the neighbor.  Listed by 2.22.0; the fit did not pull it toward a soma."""
    found = scan_rows({
        1: (622, 0.21, 0.0, 0.0, 0.0, 0.0),
        2: (624, 0.035, 0.33, 0.0, -1.06, 0.0),
    }, [(1, 2)])
    assert found.n_next_to_wider(4.0) == 1   # moved, next to one 6x as wide
    assert found.candidates == []


def test_a_pull_toward_a_soma_two_sections_away_goes_a_third_of_the_way():
    """RC1 c410's 1567770 on production, 2026-10-20: the fit put it on the line
    between its neighbors as far as its section is between theirs, a third of the way
    from the thin one (one section up) to the soma (two down), 0.31 of the way to the
    soma.  A band around 0.5 would have left this pull out."""
    thin_x, own_x = 4.88, 4.71
    t = (67 - 68) / (65 - 68)
    moved_to = thin_x + t * (0.0 - thin_x)
    found = scan_rows({
        1: (65, 3.16, 0.0, 0.0, 0.0, 0.0),                 # the soma
        2: (67, 0.1, own_x, 0.0, moved_to - own_x, 0.0),
        3: (68, 0.06, thin_x, 0.0, 0.0, 0.0),
    }, [(1, 2), (2, 3)])
    (p,) = found.at(4.0)
    assert (p.location_id, p.neighbor_id) == (2, 1)
    assert p.frac == pytest.approx((own_x - moved_to) / own_x)
    assert p.frac == pytest.approx(0.309, abs=5e-4)


def test_a_move_onto_the_wider_one_is_listed_and_one_past_it_is_not():
    for dx, listed in ((-2.0, [2]), (-2.0 * cc.MAX_FRAC - 0.2, [])):
        found = scan_rows({
            1: (100, 1.0, 0.0, 0.0, 0.0, 0.0),
            2: (100, 0.1, 2.0, 0.0, dx, 0.0),
        }, [(1, 2)])
        assert [p.location_id for p in found.candidates] == listed, dx
        if listed:
            assert found.candidates[0].frac == pytest.approx(1.0)


# -- InferiorMonkey c1178 as Viking's service corrected it on 2026-10-20 -------------
@pytest.fixture(scope="module")
def c1178():
    with gzip.open(FIXTURES / "correction_pulls_c1178.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


def scan_c1178(doc: dict, mode: str) -> cc.PullScan:
    frame = pd.DataFrame({
        "ID": doc["ID"], "Z": doc["Z"], "Rum": doc["Rum"],
        "registered_x_um": doc["registered_x_um"], "registered_y_um": doc["registered_y_um"],
        "corr_dx_um": doc["corr_um"][mode]["dx"], "corr_dy_um": doc["corr_um"][mode]["dy"],
    })
    edges = np.array(doc["edges"], dtype=np.int64)
    return cc.scan(frame, edges[:, 0], edges[:, 1])


def test_c1178_under_curve_fit_flags_the_two_pulls_viking_server_confirmed(c1178):
    """Measured on production data (live registration, service 1.2.0)."""
    found = scan_c1178(c1178, "curvefit")
    assert (found.n_annotations, found.n_moved) == (2436, 2179)
    pulls = found.at(4.0)
    assert [p.location_id for p in pulls] == [1328721, 2028495]
    first, second = pulls
    assert (first.z, first.neighbor_id) == (1160, 1557441)
    assert first.r_um == pytest.approx(0.737, abs=5e-4)
    assert first.move_um == pytest.approx(10.645, abs=5e-4)
    assert first.neighbor_r_um == pytest.approx(9.887, abs=5e-4)
    assert first.ratio == pytest.approx(13.4, abs=0.05)
    assert first.frac == pytest.approx(0.495, abs=5e-4)
    # The thin tip beyond it moved 10.86 um to the same place, though its own
    # neighbor (1328721) is thin: the one-hop rule misses it, the walk does not.
    assert first.moved_with == (1328723,)
    assert (second.z, second.neighbor_id) == (1180, 2028493)
    assert second.r_um == pytest.approx(2.477, abs=5e-4)
    assert second.move_um == pytest.approx(10.356, abs=5e-4)
    assert second.neighbor_r_um == pytest.approx(10.786, abs=5e-4)
    assert second.ratio == pytest.approx(4.35, abs=0.005)
    assert second.frac == pytest.approx(0.497, abs=5e-4)


def test_c1178_counts_at_each_ratio(c1178):
    found = scan_c1178(c1178, "curvefit")
    table = {n: (found.n_next_to_wider(n), len(found.at(n))) for n in (2, 3, 4, 6)}
    assert table == {2: (55, 3), 3: (17, 2), 4: (12, 2), 6: (6, 1)}
    # 39961 (r 2.618) was pulled 4.0 um toward 39973 (r 5.344, 2.04x), 0.49 of the
    # way, but that is less than its diameter (5.2 um), so the log leaves it out
    # at every ratio; it counts only as next to a wider one at 2x.
    assert 39961 not in {p.location_id for p in found.candidates}


def test_c1178_under_field_and_fit_adds_a_short_pull(c1178):
    found = scan_c1178(c1178, "all")
    pulls = {p.location_id: p for p in found.at(4.0)}
    assert set(pulls) == {1328721, 2028495, 690259}
    p = pulls[690259]
    assert (p.z, p.neighbor_id) == (1114, 40440)
    assert p.r_um == pytest.approx(0.253, abs=5e-4)
    assert p.move_um == pytest.approx(1.127, abs=5e-4)
    assert p.ratio == pytest.approx(6.7, abs=0.05)
    assert p.frac == pytest.approx(0.43, abs=0.005)
    # 1720976 (r 0.279 um, its neighbor 1.35x) moved 1.058 um onto its neighbor, 1.03 um
    # away, and the field's part 1.4 % past it: not a pull.  Under Curve fit alone it
    # lands on it (0.99998 of the way), a candidate at 1x.
    assert 1720976 not in {q.location_id for q in found.candidates}
    assert [(q.location_id, round(q.frac, 4)) for q in scan_c1178(c1178, "curvefit").at(1.0)
            if q.location_id == 1720976] == [(1720976, 1.0)]


# -- the service and the endpoint ----------------------------------------------------
@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from sbfsem_web.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        app.state.service.offline = True
        yield test_client


def test_the_log_lists_each_loaded_copy_and_its_pulls_at_the_ratio_asked(client):
    fitted, field = make_cell(), make_cell()
    fitted.correction_status = {"requested": True, "applied": True, "mode": "curvefit"}
    fitted.section_correction = {"n_moved": 6, "stos_group": "Grid16", "service_version": "1.2.0"}
    field.correction_status = {"requested": True, "applied": True, "mode": "neighbor"}
    service = client.app.state.service
    service.add_neuron(fitted)                # scanned as a load would be (a file keeps the moves)
    service.add_neuron(field)
    assert fitted.correction_pulls is not None and field.correction_pulls is None

    log = client.get("/api/correction-log").json()
    assert (log["ratio"], log["default_ratio"], log["min_ratio"], log["max_ratio"]) == (4.0, 4.0, 1.0, 50.0)
    assert log["ratio_sent_to_viking"] is False
    cells = {c["copy_tag"]: c for c in log["cells"]}
    assert set(cells) == {"curvefit.0", "neighbor.0"}
    checked = cells["curvefit.0"]
    assert (checked["source"], checked["neuron_id"], checked["mode"]) == ("NeitzInferiorMonkey", 9, "curvefit")
    assert checked["checked"] and checked["service_version"] == "1.2.0" and checked["stos_group"] == "Grid16"
    assert (checked["n_annotations"], checked["n_moved"], checked["n_next_to_wider"]) == (9, 6, 4)
    assert [p["location_id"] for p in checked["pulls"]] == [3, 9]
    first = checked["pulls"][0]
    assert first["neighbor_id"] == 1 and first["moved_with"] == [4]
    assert first["frac"] == pytest.approx(0.5) and first["ratio"] == pytest.approx(20.0)
    unchecked = cells["neighbor.0"]
    assert not unchecked["checked"] and unchecked["pulls"] == []
    assert unchecked["note"] == "Not checked: this mode has no curve fit"

    tighter = client.get("/api/correction-log?ratio=4.2").json()
    assert [p["location_id"] for c in tighter["cells"] for p in c["pulls"]] == [3]
    assert tighter["cells"][0]["n_next_to_wider"] == 3
    for bad in ("0.5", "51", "four"):
        assert client.get(f"/api/correction-log?ratio={bad}").status_code == 422, bad


def test_a_moved_copy_is_left_to_its_base(client):
    """A person's moves come after the correction: their copy is not listed twice."""
    from sbfsem.core.moves import moved_copy, normalize_moves

    cell = make_cell()
    cell.correction_status = {"requested": True, "applied": True, "mode": "all"}
    service = client.app.state.service
    service.add_neuron(cell)
    moves = normalize_moves([{"id": 6, "dx_um": 1.0, "dy_um": 0.0}])
    derived = moved_copy(cell, moves)
    with service._lock:
        service._store(next(iter(service._copies)), derived, "all.0.mabc")
    log = client.get("/api/correction-log").json()
    assert [c["copy_tag"] for c in log["cells"]] == ["all.0"]


def test_a_cell_answers_its_own_pulls_for_the_copy_and_ratio_asked(client):
    """``GET /neurons/{source}/{id}/correction-pulls``: the cell panel's line, as the log's rows."""
    fitted, field = make_cell(), make_cell()
    fitted.correction_status = {"requested": True, "applied": True, "mode": "curvefit"}
    fitted.section_correction = {"n_moved": 6, "stos_group": "Grid16", "service_version": "1.2.0"}
    field.correction_status = {"requested": True, "applied": True, "mode": "neighbor"}
    service = client.app.state.service
    service.add_neuron(fitted)
    service.add_neuron(field)

    own = client.get("/api/neurons/i/9/correction-pulls?copy=curvefit.0").json()
    assert (own["source"], own["neuron_id"], own["copy_tag"], own["mode"]) == (
        "NeitzInferiorMonkey", 9, "curvefit.0", "curvefit")
    assert own["checked"] and own["note"] == "" and own["service_version"] == "1.2.0"
    assert (own["ratio"], own["ratio_sent_to_viking"]) == (4.0, False)
    assert (own["n_annotations"], own["n_moved"], own["n_next_to_wider"]) == (9, 6, 4)
    assert [p["location_id"] for p in own["pulls"]] == [3, 9]
    assert own["pulls"][0]["moved_with"] == [4]
    # The same rows as the log's, field for field.
    logged = client.get("/api/correction-log").json()["cells"]
    assert own["pulls"] == next(c for c in logged if c["copy_tag"] == "curvefit.0")["pulls"]

    tighter = client.get("/api/neurons/i/9/correction-pulls?copy=curvefit.0&ratio=4.2").json()
    assert (tighter["ratio"], [p["location_id"] for p in tighter["pulls"]]) == (4.2, [3])
    other = client.get("/api/neurons/i/9/correction-pulls?copy=neighbor.0").json()
    assert (other["mode"], other["checked"], other["pulls"]) == ("neighbor", False, [])
    assert other["note"] == "Not checked: this mode has no curve fit"
    for bad in ("0.5", "51", "four"):
        assert client.get(f"/api/neurons/i/9/correction-pulls?ratio={bad}").status_code == 422, bad
    assert client.get("/api/neurons/i/987654/correction-pulls").status_code == 404


def test_a_cell_without_a_correction_is_off_and_a_moved_copy_answers_its_base(client):
    from sbfsem.core.moves import moved_copy, normalize_moves

    service = client.app.state.service
    service.add_neuron(make_cell(corrected=False))
    plain = client.get("/api/neurons/i/9/correction-pulls").json()
    assert (plain["mode"], plain["checked"], plain["pulls"]) == ("off", False, [])
    assert plain["note"] == "Not checked: loaded without a section correction"

    cell = make_cell()
    cell.correction_status = {"requested": True, "applied": True, "mode": "all"}
    service.add_neuron(cell)
    derived = moved_copy(cell, normalize_moves([{"id": 6, "dx_um": 1.0, "dy_um": 0.0}]))
    with service._lock:
        service._store(next(iter(service._copies)), derived, "all.0.mabc")
    moved = client.get("/api/neurons/i/9/correction-pulls?copy=all.0.mabc").json()
    assert (moved["copy_tag"], moved["mode"], moved["checked"]) == ("all.0.mabc", "all", True)
    assert [p["location_id"] for p in moved["pulls"]] == [3, 9]
