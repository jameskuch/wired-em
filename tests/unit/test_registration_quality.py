"""What a registration does to what one section image shows (sbfsem.analysis.registration_quality).

The cells here are built so every number can be checked by hand.  Pixels are one
micron (``volume_scale`` 1000 nm), so a stored position is its µm, and the moves
are written in nm as the node table holds them.  Cell 9 has three annotations on
section 20 -- 1 at (0, 0), 2 at (3, 0), 3 at (20, 0), far from both -- one (4) on
section 21, and a synapse (child structure 44, annotation 7) at (0.5, 0) on
section 20, next to annotation 1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sbfsem.analysis.registration_quality import (
    OVER_UM,
    RADIUS_UM,
    offset_stats,
    section_distortion,
    synapse_drift,
)
from sbfsem.core.structure import Neuron
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem_web.app import create_app

STORED = {1: (0.0, 0.0, 20), 2: (3.0, 0.0, 20), 3: (20.0, 0.0, 20), 4: (0.0, 0.0, 21), 7: (0.5, 0.0, 20)}


def make_cell(live=None, corr=None, manual=None, transform="Standard") -> Neuron:
    """The cell above, moved by ``live``, ``corr`` and ``manual`` ({id: (dx_nm, dy_nm)})."""
    ids = list(STORED)
    nodes = pd.DataFrame({
        "ID": ids, "ParentID": [44 if i == 7 else 9 for i in ids],
        "VolumeX": [STORED[i][0] for i in ids], "VolumeY": [STORED[i][1] for i in ids],
        "Z": [STORED[i][2] for i in ids], "Radius": [200.0] * len(ids),
        "X": [0.0] * len(ids), "Y": [0.0] * len(ids),
        "OffEdge": [False] * len(ids), "Terminal": [False] * len(ids), "TypeCode": [1] * len(ids),
    })
    edges = pd.DataFrame({"ID": [9, 9], "A": [1, 1], "B": [4, 7]})
    neuron = Neuron(9, "i", nodes=nodes, edges=edges, volume_scale=(1000.0, 1000.0, 1000.0),
                    omitted_ids=[], data_stamp=datetime(2026, 10, 20, tzinfo=UTC), transform=transform)
    for prefix, moves in (("Live", live), ("Corr", corr), ("Man", manual)):
        if moves is not None:
            neuron.nodes[f"{prefix}DXnm"] = [moves.get(i, (0.0, 0.0))[0] for i in ids]
            neuron.nodes[f"{prefix}DYnm"] = [moves.get(i, (0.0, 0.0))[1] for i in ids]
    neuron.apply_transform()
    return neuron


def test_a_section_moved_as_a_whole_keeps_what_its_image_shows():
    # The transforms and a smooth field move every annotation of a section, and its synapses, alike.
    same = dict.fromkeys(STORED, (1500.0, -700.0))
    cell = make_cell(live=same, corr=same)
    distortion = section_distortion(cell)
    assert distortion["n"] == 1, "one pair within 5 µm on section 20 (1-2); 3 is far, 4 alone on 21"
    assert distortion["max_um"] == pytest.approx(0.0, abs=1e-9)
    drift = synapse_drift(cell)
    assert drift["n"] == 1 and drift["max_um"] == pytest.approx(0.0, abs=1e-9)


def test_a_move_per_annotation_changes_the_offsets_and_leaves_the_synapse_behind():
    # As the curve fit does: the cell's annotations move each their own way, the synapse stays.
    cell = make_cell(corr={1: (300.0, 400.0), 2: (0.0, 0.0), 3: (5000.0, 0.0), 4: (-1000.0, 0.0)})
    distortion = section_distortion(cell)
    assert distortion["n"] == 1 and distortion["median_um"] == pytest.approx(0.5)
    assert distortion["n_over"] == 1 and distortion["share_over"] == 1.0
    drift = synapse_drift(cell)
    assert drift["n"] == 1 and drift["median_um"] == pytest.approx(0.5), "the synapse is 0.5 µm off its cell now"


def test_pairs_are_found_at_the_stored_positions_and_a_persons_own_moves_do_not_count():
    # A move that brings 3 next to 1 does not make them a pair: the image showed them 20 µm apart.
    cell = make_cell(corr={3: (-19500.0, 0.0)}, manual={2: (0.0, 5000.0)})
    distortion = section_distortion(cell)
    assert distortion["n"] == 1 and distortion["max_um"] == pytest.approx(0.0, abs=1e-9)


def test_a_cell_whose_moves_do_not_apply_has_none():
    cell = make_cell(corr={1: (300.0, 400.0)}, transform="None")
    assert section_distortion(cell)["max_um"] == 0.0 and synapse_drift(cell)["max_um"] == 0.0


def test_the_statistics_of_no_offsets_are_zero_and_a_nan_is_left_out():
    assert offset_stats(np.empty(0)) == {"n": 0, "median_um": 0.0, "p95_um": 0.0, "max_um": 0.0,
                                         "n_over": 0, "share_over": 0.0}
    out = offset_stats(np.array([0.1, np.nan, 0.5, 0.3]))
    assert out["n"] == 3 and out["max_um"] == 0.5 and out["n_over"] == 2
    assert RADIUS_UM == 5.0 and OVER_UM == 0.25


def test_the_route_answers_for_the_copy_it_names(request):
    app = create_app()
    fixture = Path(request.path).parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as client:
        svc = app.state.service
        svc.offline = True
        svc.add_neuron(read_legacy_json(fixture))
        answer = client.get("/api/neurons/i/943/registration-quality?copy=off.0")
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["copy_tag"] == "off.0" and body["n_links"] > 0 and body["links"]["median_um"] >= 0
        assert body["distortion"]["max_um"] == 0.0 and body["synapse_drift"]["max_um"] == 0.0
        assert body["moves"]["correction"]["n_moved"] == 0 and body["pulls"] is None
        assert client.get("/api/neurons/i/943/registration-quality?copy=all.1").status_code == 404
