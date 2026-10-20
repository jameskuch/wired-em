"""Every assertion in the MATLAB test suite, run against recorded Viking responses.

MATLAB was not available when this port was written, so the plan's Phase 0 fell
back to the assertions hard-coded in ``test/NeuronTest.m``, ``test/GraphTest.m``
and ``test/BoundaryTest.m``.  Those assertions *are* ground truth: they were
written against the live database and encode exact expected values.

The HTTP responses for structures 943, 2542, 7161, 7162 and 7233 in
NeitzInferiorMonkey are recorded under ``tests/cassettes``, so these run offline.
Re-record with::

    SBFSEM_CACHE_DIR=tests/cassettes python -c "from sbfsem import load_neuron; load_neuron(7161, 'i')"
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.loader import InvalidStructureError, NeuronLoader
from sbfsem.io.odata import ODataClient

CASSETTES = Path(__file__).resolve().parents[1] / "cassettes"


def _load(neuron_id: int, *, synapses: bool):
    client = ODataClient("i", cache=DiskCache(CASSETTES), offline=True)
    return NeuronLoader("i", client=client).load(neuron_id, include_synapses=synapses)


@pytest.fixture(scope="module")
def t1():
    """NeuronTest's T1: Neuron(7161, 'i')."""
    return _load(7161, synapses=False)


@pytest.fixture(scope="module")
def t2():
    """NeuronTest's T2: Neuron(7162, 'i', true) — two equally sized annotations."""
    return _load(7162, synapses=True)


@pytest.fixture(scope="module")
def pr():
    """NeuronTest's PR: Neuron(2542, 'i') — closed curves only."""
    return _load(2542, synapses=True)


@pytest.fixture(scope="module")
def c943_live():
    """GraphTest's neuron: Neuron(943, 'i')."""
    return _load(943, synapses=True)


class TestNeuronTest:
    """Ports ``test/NeuronTest.m``."""

    def test_import_neuron_rejects_a_synapse_id(self):
        # x = @()Neuron(7233, 'i'); verifyError 'SBFSEM:NeuronOData:invalidTypeID'
        with pytest.raises(InvalidStructureError):
            _load(7233, synapses=False)

    def test_import_volume_scale(self, t1):
        assert t1.volume_scale == (7.5, 7.5, 90.0)

    def test_import_geometry(self, t1, pr):
        # A Disc structure has no geometries; a ClosedCurve structure does.
        assert t1.geometries is None
        assert pr.geometries is not None and len(pr.geometries) > 0

    def test_soma_size(self, t1, t2, pr):
        assert t1.get_soma_size() == pytest.approx(t1.get_cell_nodes()["Rum"].max())
        assert t1.get_soma_size(True) == pytest.approx(2 * t1.get_cell_nodes()["Rum"].max())
        assert pr.get_soma_size() == pytest.approx(pr.get_cell_nodes()["Rum"].max())
        assert np.ndim(t2.get_soma_size()) == 0  # HasElementCount(1)
        # verifyEqual(max(T1.nodes.Radius), 851.79, 'AbsTol', 0.01)
        assert t1.nodes["Radius"].max() == pytest.approx(851.79, abs=0.01)

    def test_soma_id(self, t1, t2):
        assert t1.get_soma_id() == 176264
        assert np.ndim(t2.get_soma_id()) == 0

    def test_soma_xyz(self, t1, t2):
        xyz = t1.get_soma_xyz(micron=False)
        assert xyz[0] == pytest.approx(7227.79, abs=0.01)
        assert xyz[1] == pytest.approx(1172.65, abs=0.01)
        assert xyz[2] == 490
        assert t2.get_soma_xyz().size == 3

    def test_cell_xyz(self, t2):
        assert t2.get_cell_xyz().shape == (2, 3)

    def test_daspect(self, t1):
        np.testing.assert_array_equal(t1.get_daspect(), [12, 12, 1])

    def test_unfinished(self, t1):
        np.testing.assert_array_equal(sorted(t1.unfinished), [176264, 176266])

    def test_synapses(self, t2):
        assert set(t2.synapses["ParentID"]) == {t2.id}
        from sbfsem.core.types import StructureTypes

        assert all(isinstance(v, StructureTypes) for v in t2.synapses["LocalName"])
        # x = T2.synapses(T2.synapses.ID == 7243, :); verifyEqual(x.N, 2)
        assert int(t2.synapses.loc[t2.synapses["ID"] == 7243, "N"].iloc[0]) == 2

    @pytest.mark.parametrize(
        ("synapse_id", "expected"),
        [
            (7233, "ConvPost"), (7234, "ConvPre"), (7235, "RibbonPre"),
            (7236, "RibbonPost"), (7237, "GapJunction"), (7239, "BasalNTA"),
            (7240, "GABAPost"), (7241, "BasalMNTA"), (7242, "BasalTA"),
            (7243, "Desmosome"), (7244, "Unknown"), (7245, "BipConvPre"),
            (7246, "BipConvPost"),
        ],
    )
    def test_synapse_local_name(self, t2, synapse_id, expected):
        """All 13 cases of NeuronTest's testSynapseLocalName."""
        row = t2.synapses[t2.synapses["ID"] == synapse_id]
        assert len(row) == 1, f"synapse {synapse_id} missing"
        assert row["LocalName"].iloc[0].value == expected

    def test_geometry_update_does_not_duplicate(self, pr):
        before = len(pr.geometries)
        client = ODataClient("i", cache=DiskCache(CASSETTES), offline=True)
        again = NeuronLoader("i", client=client).fetch_geometries(2542, pr.volume_scale)
        assert len(again) == before


class TestGraphTest:
    """Ports ``test/GraphTest.m``."""

    def test_graph_directedness(self, c943_live):
        assert c943_live.graph(directed=True).is_directed()
        assert not c943_live.graph(directed=False).is_directed()

    def test_node_ids_match_the_segmentation(self, c943_live):
        """GraphTest compared dendriteSegmentation's nodeIDs against graph()'s.

        Both are keyed by location ID here, so the check is that segmentation
        covers exactly the graph's nodes.
        """
        from sbfsem.geometry.segmentation import dendrite_segmentation

        result = dendrite_segmentation(c943_live)
        covered = np.unique(np.concatenate(result.segments))
        expected = np.array(sorted(c943_live.graph().nodes), dtype=np.int64)
        np.testing.assert_array_equal(covered, expected)

    def test_swc_terminal_nodes_match_degree_one(self, c943_live):
        """GraphTest: find(G.degree == 1) == find(SWC.T.ID == 6)."""
        from sbfsem.export.swc import SWC_TYPES, swc_table

        graph = c943_live.graph()
        degree_one = {int(n) for n, d in graph.degree() if d == 1}
        table = swc_table(c943_live, has_soma=False)
        typed_ends = set(table.loc[table["T"] == SWC_TYPES["end"], "ID"].astype(int))
        assert typed_ends == degree_one


class TestBoundaryTest:
    """Ports ``test/BoundaryTest.m``."""

    def test_analysis_grid_size(self):
        from sbfsem.analysis.boundary import load_boundary

        inl = load_boundary("i", "INL", 200)
        assert inl.x_points.shape == (200,)
        assert inl.y_points.shape == (200,)
        assert inl.surface.shape == (200, 200)

    def test_xy_eval_returns_one_value(self):
        from sbfsem.analysis.boundary import load_boundary

        inl = load_boundary("i", "INL", 200)
        value = inl.xy_eval(np.median(inl.x_points), np.median(inl.y_points))
        assert value.size == 1
        assert np.isfinite(value[0])
