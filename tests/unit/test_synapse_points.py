"""GraphApp's synapse dots: one point a synapse, where the 3D render's markers sit."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sbfsem.io.legacy_json import read_legacy_json
from sbfsem_web.app import create_app


@pytest.fixture
def client(i943_path):
    app = create_app()
    with TestClient(app) as test_client:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(i943_path))
        yield test_client


def test_each_synapse_is_one_point_where_its_marker_is_drawn(client):
    points = client.get("/api/neurons/i/943/synapse-points").json()
    neuron = client.app.state.service.neuron(943, "i")
    assert len(points) == len(neuron.synapses) == 4
    assert [p["synapse_id"] for p in points] == sorted(p["synapse_id"] for p in points)
    counts = {c["local_name"]: c["count"] for c in client.get("/api/neurons/i/943").json()["synapse_counts"]}
    for kind, n in counts.items():
        mine = [p for p in points if p["type"] == kind]
        assert len(mine) == n, kind
        # The same places the render's synapse markers are built at (get_synapse_xyz).
        np.testing.assert_allclose(sorted(p["xyz_um"] for p in mine), sorted(neuron.get_synapse_xyz(kind).tolist()))
    for p in points:
        node = neuron.nodes[neuron.nodes["ID"] == p["location_id"]].iloc[0]
        assert int(node["ParentID"]) == p["synapse_id"] and bool(node["Unique"])
        assert p["z_section"] == int(node["Z"])


def test_a_cell_without_synapses_has_no_points_and_one_not_loaded_is_a_404(client, i943_path):
    svc = client.app.state.service
    bare = read_legacy_json(i943_path)
    bare.id = 9430
    bare.synapses = bare.synapses.iloc[0:0]
    svc.add_neuron(bare)
    assert client.get("/api/neurons/i/9430/synapse-points").json() == []
    assert client.get("/api/neurons/i/12345/synapse-points").status_code == 404
