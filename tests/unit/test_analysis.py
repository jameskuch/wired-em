from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sbfsem.analysis import dendrite as D
from sbfsem.analysis import synapse as S
from sbfsem.analysis.drp import density_recovery_profile, nearest_neighbor_distances
from sbfsem.analysis.stats import (
    bin_centers,
    describe,
    euclid3d,
    hist_counts,
    sem,
    step_lengths_2d,
    step_lengths_3d,
)
from sbfsem.core.structure import Neuron


def test_sem_is_nan_safe():
    # F136: std(data)/sqrt(length(data)) counted NaNs and used the largest dim.
    assert sem([1.0, 2.0, 3.0]) == pytest.approx(np.std([1, 2, 3], ddof=1) / np.sqrt(3))
    assert sem([1.0, 2.0, 3.0, np.nan]) == pytest.approx(sem([1.0, 2.0, 3.0]))
    assert np.isnan(sem([]))
    assert np.isnan(sem([5.0]))


def test_describe_handles_empty():
    stats = describe([])
    assert stats.n == 0
    assert np.isnan(stats.mean)


def test_bin_centers_are_centers():
    # F123: the MATLAB sites reported left edges, right edges and true centers.
    edges = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    np.testing.assert_allclose(bin_centers(edges), [0.125, 0.375, 0.625, 0.875])
    counts, edges, centers = hist_counts([0.1, 0.2, 0.9], bins=2, range_=(0, 1))
    np.testing.assert_allclose(centers, [0.25, 0.75])
    np.testing.assert_array_equal(counts, [2, 1])


def test_distance_helpers():
    np.testing.assert_allclose(euclid3d([0, 0, 0], [[3, 4, 0], [0, 0, 5]]), [5, 5])
    path = np.array([[0.0, 0, 0], [3, 4, 0], [3, 4, 12]])
    assert step_lengths_3d(path).sum() == pytest.approx(17.0)
    assert step_lengths_2d(path).sum() == pytest.approx(5.0)


def test_tortuosity_dimensions_are_not_swapped(c943):
    """F122: the 2-D branch used the 3-D distance and vice versa."""
    g = c943.graph()
    ends = [n for n, d in g.degree() if d == 1]
    a, b = ends[0], ends[1]
    xyz = c943.get_branch_nodes(a, b)[["Xum", "Yum", "Zum"]].to_numpy()

    expected_2d = step_lengths_2d(xyz).sum() / np.linalg.norm(xyz[0, :2] - xyz[-1, :2])
    expected_3d = step_lengths_3d(xyz).sum() / np.linalg.norm(xyz[0] - xyz[-1])
    assert D.tortuosity(c943, a, b, dim=2) == pytest.approx(expected_2d)
    assert D.tortuosity(c943, a, b, dim=3) == pytest.approx(expected_3d)
    # A 3-D path over a 3-D chord can never be less than 1.
    assert D.tortuosity(c943, a, b, dim=3) >= 1.0
    with pytest.raises(ValueError):
        D.tortuosity(c943, a, b, dim=4)


def test_branch_diameter_reports_diameters(c943):
    g = c943.graph()
    ends = [n for n, d in g.degree() if d == 1]
    diameters, stats = D.branch_diameter(c943, ends[0], ends[1])
    radii = c943.get_branch_nodes(ends[0], ends[1])["Rum"].to_numpy()
    np.testing.assert_allclose(diameters, 2 * radii)
    assert stats.mean == pytest.approx(2 * radii.mean())


def test_surface_area_uses_cell_nodes_only(c943):
    """F128: MATLAB summed every annotation, including synapses."""
    expected = 2 * np.pi * c943.get_cell_nodes()["Rum"].sum() * 0.09
    assert D.surface_area(c943) == pytest.approx(expected)
    with_synapses = 2 * np.pi * c943.nodes["Rum"].sum() * 0.09
    assert D.surface_area(c943) < with_synapses


def test_annotation_sizes_keeps_the_largest(c943):
    # F130: MATLAB's strict `data < max` always dropped the soma row.
    stats = D.annotation_sizes(c943)
    assert stats.n == len(c943.get_cell_nodes())
    assert stats.max == pytest.approx(c943.get_cell_nodes()["Rum"].max())


def test_hull_is_degenerate_safe():
    nodes = pd.DataFrame(
        {
            "ID": [1, 2], "ParentID": [1, 1],
            "VolumeX": [0.0, 1.0], "VolumeY": [0.0, 1.0], "Z": [1, 2],
            "Radius": [1.0, 1.0], "X": [0.0, 0.0], "Y": [0.0, 0.0],
            "OffEdge": [False, False], "Terminal": [False, False], "TypeCode": [1, 1],
        }
    )
    n = Neuron(1, "i", nodes=nodes)
    hull = D.dendritic_field_hull(n)
    assert hull.area == 0.0
    assert hull.points.shape == (0, 2)


def test_hull_of_a_square():
    side = 10.0
    corners = [(0, 0), (side, 0), (side, side), (0, side)]
    nodes = pd.DataFrame(
        {
            "ID": range(1, 5), "ParentID": [1] * 4,
            "VolumeX": [x / 0.0075 for x, _ in corners],
            "VolumeY": [y / 0.0075 for _, y in corners],
            "Z": [1, 2, 3, 4], "Radius": [1.0] * 4,
            "X": [0.0] * 4, "Y": [0.0] * 4,
            "OffEdge": [False] * 4, "Terminal": [False] * 4, "TypeCode": [1] * 4,
        }
    )
    hull = D.dendritic_field_hull(Neuron(1, "i", nodes=nodes))
    assert hull.area == pytest.approx(side * side, rel=1e-6)
    assert hull.perimeter == pytest.approx(4 * side, rel=1e-6)


def test_nearest_annotation_and_path(c943):
    soma = c943.get_soma_xyz()
    location_id, distance = D.nearest_annotation(c943, soma)
    assert location_id == c943.get_soma_id()
    assert distance == pytest.approx(0.0, abs=1e-9)
    total, steps = D.path_distance(c943, c943.get_soma_id(), location_id)
    assert total == 0.0
    assert steps.size == 0


def test_ie_ratio_reports_both_definitions(c943):
    result = S.ie_ratio(c943)
    assert result.n_excitatory == 2
    assert result.n_inhibitory == 0
    assert result.ratio == 0.0
    assert result.inhibitory_fraction == 0.0


def test_ie_ratio_of_a_synapse_free_cell_is_nan():
    nodes = pd.DataFrame(
        {
            "ID": [1], "ParentID": [1], "VolumeX": [0.0], "VolumeY": [0.0], "Z": [1],
            "Radius": [1.0], "X": [0.0], "Y": [0.0],
            "OffEdge": [False], "Terminal": [False], "TypeCode": [1],
        }
    )
    result = S.ie_ratio(Neuron(1, "i", nodes=nodes))
    assert np.isnan(result.ratio)
    assert np.isnan(result.inhibitory_fraction)


def test_synapse_soma_distance(c943):
    distances = S.synapse_soma_distance(c943, "RibbonPost")
    xyz = c943.get_synapse_xyz("RibbonPost")
    expected = np.linalg.norm(xyz - c943.get_soma_xyz(), axis=1)
    np.testing.assert_allclose(distances, expected)
    assert S.synapse_soma_distance(c943, "GapJunction").size == 0


def test_synapse_soma_path_length_raises_for_a_missing_type(c943):
    # F125: MATLAB measured a real path to an unrelated annotation instead.
    from sbfsem.core.structure import SynapseNotFoundError

    with pytest.raises(SynapseNotFoundError):
        S.synapse_soma_path_length(c943, "GapJunction")


def test_synapse_direction(c943):
    frame = S.synapse_direction(c943, "RibbonPost")
    assert len(frame) == 2
    assert (frame["distance"] > 0).all()
    assert frame["azimuth"].between(-180, 180).all()


def test_reciprocal_synapses_finds_close_pairs(c943):
    links = pd.DataFrame(
        {
            "NeuronID": [7000, 7000],
            "SynapseID": [944, 946],
            "SynapseType": ["RibbonPre", "RibbonPost"],
            "NeuronLabel": ["", ""],
            "Direction": ["pre", "post"],
        }
    )
    close = S.reciprocal_synapses(c943, links, search_radius=100.0)
    assert len(close) == 1
    far = S.reciprocal_synapses(c943, links, search_radius=1e-6)
    assert far.empty
    assert S.reciprocal_synapses(c943, pd.DataFrame()).empty


def test_open_ended_branches(c943):
    ids = D.open_ended_branches(c943)
    graph = c943.graph()
    assert all(graph.degree(int(i)) < 2 for i in ids)
    near = D.open_ended_branches(c943, soma_threshold=1.0)
    assert len(near) <= len(ids)


def test_density_recovery_profile_of_a_poisson_field():
    rng = np.random.default_rng(0)
    points = rng.uniform(0, 200, (2000, 2))
    result = density_recovery_profile(points, bin_width=2.0, num_bins=15)
    assert result.counts.sum() > 0
    assert result.bin_centers[0] == pytest.approx(1.0)
    # Uncorrected for edges, so this underestimates the true 0.05 per um^2.
    assert 0.02 < result.mean_density < 0.06
    with pytest.raises(ValueError):
        density_recovery_profile(np.zeros((1, 2)))


def test_nearest_neighbors():
    points = np.array([[0.0, 0], [1, 0], [5, 0]])
    distances, indices = nearest_neighbor_distances(points, k=2)
    assert distances.shape == (3, 2)
    np.testing.assert_allclose(distances[:, 0], 0.0)
    np.testing.assert_allclose(distances[:, 1], [1.0, 1.0, 4.0])
    assert indices[0, 0] == 0


def test_morphometrics_match_the_graph(c943):
    m = D.morphometrics(c943)
    g = c943.graph()
    xyz = c943.get_cell_nodes().set_index("ID")[["Xum", "Yum", "Zum"]]
    expected = sum(
        float(np.linalg.norm(xyz.loc[a].to_numpy() - xyz.loc[b].to_numpy())) for a, b in g.edges()
    )
    assert m.total_length_um == pytest.approx(expected)
    assert m.total_length_xy_um <= m.total_length_um
    assert m.dendritic_length_um <= m.total_length_um
    assert m.dendritic_length_xy_um <= m.dendritic_length_um
    assert m.n_branch_points == sum(1 for _, d in g.degree() if d > 2)
    assert m.n_tips == sum(1 for n, d in g.degree() if d == 1 and n != c943.get_soma_id())
    assert m.n_annotations == len(c943.get_cell_nodes())
    assert m.n_components == 1
    assert 0 < m.longest_path_um <= m.total_length_um
    assert m.soma_radius_um == pytest.approx(c943.get_soma_size())
    assert len(m.extent_um) == 3 and all(v > 0 for v in m.extent_um)
    as_dict = m.as_dict()
    assert isinstance(as_dict["extent_um"], list)


def test_morphometrics_soma_fraction_moves_the_soma_boundary(c943):
    strict = D.morphometrics(c943, soma_fraction=0.99)
    loose = D.morphometrics(c943, soma_fraction=0.5)
    assert strict.n_soma_annotations <= loose.n_soma_annotations
    assert strict.dendritic_length_um >= loose.dendritic_length_um
    assert strict.total_length_um == pytest.approx(loose.total_length_um)


def test_morphometrics_empty_neuron():
    empty = Neuron.__new__(Neuron)
    empty.id = 1
    empty.nodes = pd.DataFrame(
        columns=["ID", "ParentID", "Xum", "Yum", "Zum", "Rum", "OffEdge", "Terminal", "Unique"]
    )
    empty.edges = pd.DataFrame(columns=["ID", "A", "B"])
    m = D.morphometrics(empty)
    assert m.total_length_um == 0.0 and m.n_annotations == 0
