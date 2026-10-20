"""Stratification analyses on top of the corrected IPL depth conversion."""

from __future__ import annotations

import numpy as np
import pytest

from sbfsem.analysis.boundary import BoundarySurface, IPLBoundaries
from sbfsem.analysis.stratification import (
    branch_stratification,
    ipl_depth,
    synapse_stratification,
)


@pytest.fixture(scope="module")
def flat():
    """A flat IPL between z = 130 and z = 150, covering c943's whole extent."""
    pts = np.linspace(0, 400, 20)
    return IPLBoundaries(
        BoundarySurface("INL", "NeitzInferiorMonkey", np.zeros((3, 3)), pts, pts,
                        np.full((20, 20), 130.0)),
        BoundarySurface("GCL", "NeitzInferiorMonkey", np.zeros((3, 3)), pts, pts,
                        np.full((20, 20), 150.0)),
    )


def test_ipl_depth_over_a_synthetic_ipl(c943, flat):
    result = ipl_depth(c943, boundaries=flat, num_bins=10)
    nodes = c943.get_cell_nodes()
    expected = (nodes["Zum"].to_numpy() - 130.0) / 20.0
    assert result.stats.n == len(nodes)
    assert result.stats.median == pytest.approx(np.median(expected))
    assert result.counts.sum() == len(nodes)
    # Bin centers, not edges (F123).
    width = result.edges[1] - result.edges[0]
    np.testing.assert_allclose(result.centers, result.edges[:-1] + width / 2)
    assert result.centers.min() > result.edges.min()
    assert result.mode in result.centers


def test_ipl_depth_bin_locations(c943, flat):
    bins = np.linspace(0, 1, 11)
    result = ipl_depth(c943, boundaries=flat, bin_locations=bins)
    np.testing.assert_allclose(result.edges, bins)
    np.testing.assert_allclose(result.centers, (bins[:-1] + bins[1:]) / 2)


def test_omit_outliers_counts_what_it_drops(c943, flat):
    kept = ipl_depth(c943, boundaries=flat, omit_outliers=True, ipl_margin=0.0)
    everything = ipl_depth(c943, boundaries=flat, omit_outliers=False)
    assert kept.stats.n + kept.n_omitted_outlier == everything.stats.n


def test_include_soma_flag_changes_the_count(c943, flat):
    with_soma = ipl_depth(c943, boundaries=flat, omit_outliers=False)
    without = ipl_depth(c943, boundaries=flat, include_soma=False, omit_outliers=False)
    assert without.stats.n < with_soma.stats.n


def test_out_of_range_neuron_reports_empty_not_a_spurious_mode(c943):
    """c943 sits outside the shipped boundary marker range."""
    from sbfsem.analysis.boundary import load_ipl_boundaries

    result = ipl_depth(c943, boundaries=load_ipl_boundaries("i"))
    assert result.stats.n == 0
    assert np.isnan(result.mode)
    assert result.counts.size == 0
    assert result.n_omitted_outlier == len(c943.get_cell_nodes())


def test_synapse_stratification(c943, flat):
    result = synapse_stratification(c943, "RibbonPost", boundaries=flat, num_bins=5)
    assert result.stats.n == 2
    pdf = synapse_stratification(c943, "RibbonPost", boundaries=flat, num_bins=5, pdf=True)
    assert pdf.counts.sum() == pytest.approx(1.0)
    with pytest.raises(ValueError):
        synapse_stratification(c943, "GapJunction", boundaries=flat)


def test_branch_stratification(c943, flat):
    graph = c943.graph()
    ends = [n for n, d in graph.degree() if d == 1]
    result = branch_stratification(c943, ends[0], ends[1], boundaries=flat)
    assert result.stats.n == len(c943.get_branch_nodes(ends[0], ends[1]))
    # F135: singleDendriteStratification fed NaNs straight into its statistics.
    assert np.isfinite(result.stats.median)


def test_result_serialises(c943, flat):
    payload = ipl_depth(c943, boundaries=flat).as_dict()
    assert set(payload) >= {"depth", "counts", "centers", "edges", "stats", "mode"}
    assert isinstance(payload["counts"], list)
