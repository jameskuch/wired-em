"""Parity against output captured from MATLAB R2023b running SBFSEM-tools.

The plan's Phase 0 is done properly here: ``tests/fixtures/capture/*.m`` were run
against the live Viking service under MATLAB R2023b (Image Processing and
Statistics toolboxes, matGeom, GUI Layout Toolbox), and their output is committed
under ``tests/fixtures/capture/ground_truth/``.

Two kinds of assertion live here:

* **Agreement.** Where the port is meant to reproduce MATLAB, it must, to
  floating-point tolerance.
* **Deviation.** Where the port deliberately differs — the corrections listed in
  DEVIATIONS.md — the difference is asserted to be exactly the documented one and
  nothing else.  Several capture scripts therefore record *both* the value MATLAB
  ships and the value its own documentation says it intended.

Neuron data comes from ``tests/cassettes`` so these run offline.  Re-capture with::

    cd tests/fixtures/capture
    /Applications/MATLAB_R2023b.app/bin/matlab -batch capture_regularize
    /Applications/MATLAB_R2023b.app/bin/matlab -batch capture_boundaries
    /Applications/MATLAB_R2023b.app/bin/matlab -batch capture_neurons
    /Applications/MATLAB_R2023b.app/bin/matlab -batch capture_meshes
    /Applications/MATLAB_R2023b.app/bin/matlab -batch capture_ipl_neuron
    /Applications/MATLAB_R2023b.app/bin/matlab -batch capture_colormaps
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.loader import NeuronLoader
from sbfsem.io.odata import ODataClient

GT = Path(__file__).resolve().parents[1] / "fixtures" / "capture" / "ground_truth"
CASSETTES = Path(__file__).resolve().parents[1] / "cassettes"

#: Column order written by ``capture_neurons.m``.
NODE_COLUMNS = [
    "ID", "ParentID", "VolumeX", "VolumeY", "Z", "Radius", "X", "Y",
    "OffEdge", "Terminal", "TypeCode", "Xum", "Yum", "Zum", "Rum",
]

pytestmark = pytest.mark.skipif(
    not GT.is_dir(), reason="MATLAB ground truth has not been captured"
)


def _load(neuron_id: int, *, synapses: bool):
    client = ODataClient("i", cache=DiskCache(CASSETTES), offline=True)
    return NeuronLoader("i", client=client).load(neuron_id, include_synapses=synapses)


def _scalars(name: str) -> dict[str, float]:
    return {
        k: float(v)
        for k, v in (line.split(",", 1) for line in (GT / name).read_text().splitlines())
    }


@pytest.fixture(scope="module")
def neurons():
    return {
        943: _load(943, synapses=True),
        2542: _load(2542, synapses=True),
        7161: _load(7161, synapses=False),
        7162: _load(7162, synapses=True),
        1441: _load(1441, synapses=True),
    }


# ---------------------------------------------------------------- RegularizeData3D
class TestRegularizeData3D:
    """The riskiest port in the toolbox: it fits the IPL boundary surfaces."""

    @pytest.fixture(scope="class")
    def scattered(self):
        data = np.loadtxt(GT / "regularize_input.csv", delimiter=",")
        return (
            data[:, 0], data[:, 1], data[:, 2],
            np.loadtxt(GT / "regularize_xnodes.csv"),
            np.loadtxt(GT / "regularize_ynodes.csv"),
        )

    @pytest.mark.parametrize("interp", ["triangle", "bilinear", "bicubic", "nearest"])
    @pytest.mark.parametrize("smoothness", [0.005, 0.01, 1.0])
    def test_matches_matlab(self, scattered, interp, smoothness):
        from sbfsem.analysis.regularize import regularize_data_3d

        x, y, z, xnodes, ynodes = scattered
        expected = np.loadtxt(GT / f"regularize_{interp}_{smoothness:g}.csv", delimiter=",")
        got, _, _ = regularize_data_3d(
            x, y, z, xnodes, ynodes, interp=interp, smoothness=smoothness
        )
        assert got.shape == expected.shape
        span = expected.max() - expected.min()
        assert np.abs(got - expected).max() / span < 1e-9

    def test_nearest_uses_matlab_rounding(self, scattered):
        """MATLAB rounds half away from zero; numpy rounds half to even.

        On a regular grid a point sits exactly at a cell midpoint often enough
        that the two rules pick different nodes for a large share of the data.
        """
        from sbfsem.analysis.regularize import _round_half_up

        np.testing.assert_array_equal(_round_half_up([0.5, 1.5, 0.4999]), [1, 2, 0])
        np.testing.assert_array_equal(np.round([0.5, 1.5]), [0.0, 2.0])  # numpy differs


# --------------------------------------------------------------- boundary surfaces
class TestBoundarySurfaces:
    @pytest.fixture(scope="class")
    def boundaries(self):
        from sbfsem.analysis.boundary import load_ipl_boundaries

        return load_ipl_boundaries("i")

    @pytest.mark.parametrize("layer", ["inl", "gcl"])
    def test_surface_matches_matlab(self, boundaries, layer):
        surface = getattr(boundaries, layer)
        np.testing.assert_allclose(
            np.loadtxt(GT / f"{layer}_xpts.csv"), surface.x_points, atol=1e-9
        )
        np.testing.assert_allclose(
            np.loadtxt(GT / f"{layer}_ypts.csv"), surface.y_points, atol=1e-9
        )
        expected = np.loadtxt(GT / f"{layer}_surface.csv", delimiter=",")
        # Nine nanometres over a sixty micron range.
        assert np.abs(expected - surface.surface).max() < 1e-7

    def test_micron2ipl_matches_matlab_corrected(self, boundaries):
        """The port reproduces the calculation MATLAB's own near-duplicates do."""
        probe = np.loadtxt(GT / "ipl_probe_points.csv", delimiter=",")
        expected = np.loadtxt(GT / "ipl_probe_matlab_fixed.csv")
        assert np.nanmax(np.abs(boundaries.micron2ipl(probe) - expected)) < 1e-8

    def test_micron2ipl_differs_from_shipped_matlab_by_the_bug(self, boundaries):
        """F214: the shipped ``micron2ipl`` builds the INL grid from Y twice."""
        probe = np.loadtxt(GT / "ipl_probe_points.csv", delimiter=",")
        shipped = np.loadtxt(GT / "ipl_probe_matlab_asis.csv")
        gap = np.nanmax(np.abs(boundaries.micron2ipl(probe) - shipped))
        # Up to ~6% of total IPL depth on this probe grid: a real, systematic error.
        assert 0.05 < gap < 0.07

    def test_depth_is_right_at_the_markers(self, boundaries):
        """The check MATLAB never made: 0 at an INL marker, 1 at a GCL marker."""
        inl = np.loadtxt(GT / "inl_markers_um.csv", delimiter=",")
        gcl = np.loadtxt(GT / "gcl_markers_um.csv", delimiter=",")
        assert abs(np.nanmedian(boundaries.micron2ipl(inl))) < 0.01
        assert abs(np.nanmedian(boundaries.micron2ipl(gcl)) - 1.0) < 0.01
        # MATLAB as shipped lands at -0.0244 for the INL markers, six times worse.


# ------------------------------------------------------------------ neuron tables
@pytest.mark.parametrize(("neuron_id", "n_nodes"),
                         [(943, 198), (2542, 169), (7161, 3), (7162, 17), (1441, 1219)])
def test_node_tables_are_bit_identical(neurons, neuron_id, n_nodes):
    """Import, parsing and the micron transform, against MATLAB's own tables."""
    expected = pd.DataFrame(
        np.loadtxt(GT / f"c{neuron_id}_nodes.csv", delimiter=","), columns=NODE_COLUMNS
    ).sort_values("ID").reset_index(drop=True)
    got = neurons[neuron_id].nodes.sort_values("ID").reset_index(drop=True)

    assert len(expected) == n_nodes == len(got)
    np.testing.assert_array_equal(expected["ID"].to_numpy(np.int64), got["ID"].to_numpy())
    for column in ("VolumeX", "VolumeY", "Z", "Radius", "X", "Y", "TypeCode",
                   "Xum", "Yum", "Zum", "Rum"):
        np.testing.assert_array_equal(
            expected[column].to_numpy(float), got[column].to_numpy(float),
            err_msg=f"c{neuron_id}.{column}",
        )
    for flag in ("OffEdge", "Terminal"):
        np.testing.assert_array_equal(
            expected[flag].to_numpy(bool), got[flag].to_numpy(bool)
        )


@pytest.mark.parametrize("neuron_id", [943, 2542, 7161, 7162, 1441])
def test_edges_match(neurons, neuron_id):
    expected = np.loadtxt(GT / f"c{neuron_id}_edges.csv", delimiter=",", ndmin=2)
    got = neurons[neuron_id].edges[["ID", "A", "B"]].to_numpy(float)
    assert len(expected) == len(got)
    np.testing.assert_array_equal(
        np.unique(expected[:, 1:], axis=0), np.unique(got[:, 1:], axis=0)
    )


@pytest.mark.parametrize("neuron_id", [943, 2542, 7162, 1441])
def test_synapse_names_and_counts_match(neurons, neuron_id):
    expected = pd.read_csv(
        GT / f"c{neuron_id}_synapses.csv", header=None,
        names=["ID", "ParentID", "TypeID", "N", "LocalName"],
    )
    got = neurons[neuron_id].synapses.copy()
    got["LocalName"] = got["LocalName"].map(lambda t: t.value)
    merged = expected.merge(got, on="ID", suffixes=("_m", "_p"))
    assert len(merged) == len(expected)
    np.testing.assert_array_equal(merged["N_m"].to_numpy(), merged["N_p"].to_numpy())
    assert (merged["LocalName_m"] == merged["LocalName_p"]).all()


@pytest.mark.parametrize("neuron_id", [943, 2542, 7161, 7162])
def test_scalars_match(neurons, neuron_id):
    s = _scalars(f"c{neuron_id}_scalars.csv")
    n = neurons[neuron_id]
    assert n.get_soma_id() == int(s["somaID"])
    assert n.get_soma_size() == pytest.approx(s["somaRadiusUm"], abs=1e-12)
    np.testing.assert_allclose(
        n.get_soma_xyz(), [s["somaXum"], s["somaYum"], s["somaZum"]], atol=1e-12
    )
    np.testing.assert_allclose(
        n.get_soma_xyz(micron=False), [s["somaXpx"], s["somaYpx"], s["somaZpx"]], atol=1e-9
    )
    assert n.nodes["Radius"].max() == pytest.approx(s["maxRadius"], abs=1e-9)
    assert len(n.get_cell_nodes()) == int(s["nCellNodes"])
    assert len(n.unfinished) == int(s["nUnfinished"])
    expected = np.loadtxt(GT / f"c{neuron_id}_unfinished.csv", ndmin=1).astype(np.int64)
    np.testing.assert_array_equal(np.sort(expected), np.sort(n.unfinished))


def test_geometry_control_points_match(neurons):
    """Closed-curve outlines, including the WKT closing-vertex convention.

    MATLAB parsed ``MosaicShape``, the section's own mosaic frame; the parser
    still reproduces its control points exactly.  The loader, deliberately,
    does not stop there (DEVIATIONS 6.7): it returns Viking's registered
    ``VolumeShape``, the frame every disc centre uses, which on Inferior
    Monkey sits 15 to 20 um from the mosaic frame.
    """
    from sbfsem.io.wkt import parse_closed_curve, volume_frame_shift_um

    n = neurons[2542]
    client = ODataClient("i", cache=DiskCache(CASSETTES), offline=True)
    raw = {int(r["ID"]): r for r in client.fetch_closed_curves(2542)}
    ordered = n.geometries.sort_values("Z", ascending=False)
    mosaic = [parse_closed_curve(raw[int(g.ID)]["MosaicShape"]["Geometry"]["WellKnownText"],
                                 n.volume_scale) for g in ordered.itertuples()]

    # Parity: MATLAB's control points, point for point.
    expected = np.loadtxt(GT / "c2542_geometry_first_outline.csv", delimiter=",")
    np.testing.assert_array_equal(expected, mosaic[0][0])
    rows = [line.split(",") for line in
            (GT / "c2542_geometry_summary.csv").read_text().strip().splitlines()]
    assert len(rows) == len(ordered)
    for row, rings in zip(rows, mosaic, strict=True):
        assert [int(v) for v in row[5:]] == [len(np.asarray(c)) for c in rings]

    # Deviation: what the loader hands out is the registered outline, and it
    # lies where the mosaic outline lands once moved by its own node's offset.
    assert bool(ordered["Registered"].all())
    for g, rings in zip(ordered.itertuples(), mosaic, strict=True):
        shift = volume_frame_shift_um(raw[int(g.ID)], n.volume_scale)
        assert np.linalg.norm(shift) > 10.0  # the frames really are apart here
        registered = np.asarray(g.Curve[0])
        moved = rings[0] + shift
        assert np.linalg.norm(registered.mean(axis=0) - moved.mean(axis=0)) < 1.0


# --------------------------------------------------------------------- deviations
@pytest.mark.parametrize("neuron_id", [943, 2542, 7161, 7162, 1441])
def test_surface_area_deviation_is_exactly_the_synapse_annotations(neurons, neuron_id):
    """F128: MATLAB summed every annotation, child structures included."""
    from sbfsem.analysis.dendrite import surface_area

    n = neurons[neuron_id]
    name = f"c{neuron_id}_analyses.csv" if neuron_id == 1441 else f"c{neuron_id}_scalars.csv"
    expected = _scalars(name)["surfaceArea"]
    dz = n.volume_scale[2] / 1e3

    over_all = 2 * np.pi * n.nodes["Rum"].sum() * dz
    assert over_all == pytest.approx(expected, rel=1e-12), "MATLAB's own formula must match"

    shipped = surface_area(n)
    over_cell = 2 * np.pi * n.get_cell_nodes()["Rum"].sum() * dz
    assert shipped == pytest.approx(over_cell, rel=1e-12)
    if len(n.nodes) > len(n.get_cell_nodes()):
        assert shipped < expected


def test_segmentation_deviation_is_only_the_branch_point_fix(neurons):
    """F119: MATLAB appended every remaining neighbor, not just the parent."""
    from sbfsem.geometry.segmentation import dendrite_segmentation

    n = neurons[943]
    meta = {k: int(v) for k, v in (line.split(",") for line in
            (GT / "c943_segmentation_meta.csv").read_text().strip().splitlines())}
    matlab = [[int(v) for v in line.split(",")] for line in
              (GT / "c943_segments.csv").read_text().strip().splitlines()]
    port = [[int(v) for v in s] for s in
            dendrite_segmentation(n, start_node=meta["startLocationID"]).segments]

    assert len(port) == len(matlab) == meta["nSegments"]
    # Same nodes covered, different grouping at branch points.
    assert set(np.concatenate(matlab).tolist()) == set(np.concatenate(port).tolist())

    graph = n.graph()

    def spans_a_branch(segments):
        return [s for s in segments
                if any(not graph.has_edge(a, b)
                       for a, b in zip(s[:-1], s[1:], strict=True))]

    assert len(spans_a_branch(matlab)) == 4, "MATLAB segments jump across branch points"
    assert spans_a_branch(port) == [], "port segments follow real edges only"


def test_tortuosity_deviation_is_the_swapped_distance_functions(neurons):
    """F122: the 2-D branch called euclideanDist3 and the 3-D branch euclideanDist2."""
    from sbfsem.analysis.dendrite import tortuosity

    a = _scalars("c1441_analyses.csv")
    n = neurons[1441]
    A, B = int(a["tortA"]), int(a["tortB"])

    branch = n.get_branch_nodes(A, B)
    assert len(branch) == int(a["branchNodeCount"])
    np.testing.assert_array_equal(
        np.loadtxt(GT / "c1441_branch_xyz.csv", delimiter=","),
        branch[["Xum", "Yum", "Zum"]].to_numpy(),
    )

    assert tortuosity(n, A, B, dim=2) == pytest.approx(a["tort2D_fixed"], rel=1e-12)
    assert tortuosity(n, A, B, dim=3) == pytest.approx(a["tort3D_fixed"], rel=1e-12)
    # MATLAB as shipped reports 13.30 where the correct 2-D value is 11.69, and
    # a 3-D value *below* the 2-D one, which the correct pair never is.
    assert a["tort2D_asis"] > a["tort2D_fixed"] * 1.1
    assert a["tort3D_asis"] < a["tort2D_asis"]
    assert a["tort3D_fixed"] > a["tort2D_fixed"] * 0.2


def test_ipl_mode_deviation_is_exactly_half_a_bin(neurons):
    """F123: iplDepth reported the mode at a bin's right edge."""
    a = _scalars("c1441_analyses.csv")
    edges = np.loadtxt(GT / "c1441_ipl_bin_edges.csv")
    width = edges[1] - edges[0]
    assert a["iplMode_asis"] - a["iplMode_centres"] == pytest.approx(width / 2, rel=1e-9)

    bins_asis = np.loadtxt(GT / "c1441_ipl_bins_asis.csv")
    centers = np.loadtxt(GT / "c1441_ipl_bins_centres.csv")
    # stats.bins was the vector of *left* edges, half a bin below the centers.
    np.testing.assert_allclose(centers - bins_asis, width / 2, atol=1e-9)


def test_matching_analyses_agree_exactly(neurons):
    """Analyses with no deviation must reproduce MATLAB to the last digit."""
    from sbfsem.analysis.dendrite import dendritic_field_hull
    from sbfsem.analysis.synapse import ie_ratio

    a = _scalars("c1441_analyses.csv")
    n = neurons[1441]
    assert dendritic_field_hull(n).area == pytest.approx(a["hullArea"], rel=1e-9)
    ratio = ie_ratio(n)
    assert ratio.n_excitatory == int(a["nExc"])
    assert ratio.n_inhibitory == int(a["nInh"])
    assert ratio.inhibitory_fraction == pytest.approx(a["ieRatio"], rel=1e-12)


# -------------------------------------------------------------------------- meshes
class TestCylinderMesh:
    def test_gencyl_grid_matches_matlab(self):
        """``lib/gencyl.m``, compared before the surf2patch step."""
        from sbfsem.geometry.cylinder import _cylinder, _rotation_matrix

        for neuron_id in (7161, 943):
            xyz = np.loadtxt(GT / f"c{neuron_id}_seg1_xyz.csv", delimiter=",")
            radii = np.loadtxt(GT / f"c{neuron_id}_seg1_rum.csv", ndmin=1)
            grids = [np.loadtxt(GT / f"c{neuron_id}_seg1_gencyl_{a}.csv", delimiter=",")
                     for a in "xyz"]

            directions = np.diff(xyz, axis=0)
            lengths = np.linalg.norm(directions, axis=1)
            rows: list[list[np.ndarray]] = [[], [], []]
            for i in range(len(xyz) - 1):
                # gencyl advances the radius window by one per sub-cylinder.
                tx, ty, tz = _cylinder(radii[i:i + 2], 20)
                axis = np.cross([0.0, 0.0, 1.0], directions[i])
                angle = float(np.arccos(np.clip(directions[i][2] / lengths[i], -1, 1)))
                out = _rotation_matrix(axis, angle) @ np.vstack(
                    [tx.ravel(), ty.ravel(), lengths[i] * tz.ravel()]
                ) + xyz[i][:, None]
                for k in range(3):
                    rows[k].append(out[k].reshape(tx.shape))
            for k in range(3):
                np.testing.assert_allclose(np.vstack(rows[k]), grids[k], atol=1e-12)

    def test_line3_mesh_is_vertex_identical(self, neurons):
        """c7161 is a single three-annotation segment: pure Line3, no fallback."""
        from sbfsem.geometry.cylinder import cylinder_render

        meta = _scalars("c7161_mesh_meta.csv")
        mesh = cylinder_render(neurons[7161], smooth_iterations=0)
        assert mesh.n_vertices == int(meta["nVertices"])
        assert len(mesh) == int(meta["nFaces"])
        expected = np.loadtxt(GT / "c7161_mesh_vertices.csv", delimiter=",")
        assert np.abs(expected - mesh.vertices).max() < 1e-9
        assert mesh.surface_area() == pytest.approx(meta["area"], rel=1e-12)

    def test_whole_mesh_matches_when_given_matlab_segments(self, neurons):
        """Feeding MATLAB's segments in isolates the meshing from the segmentation."""
        from sbfsem.geometry.cylinder import segment_mesh
        from sbfsem.geometry.mesh import concatenate

        n = neurons[943]
        indexed = n.nodes.set_index("ID")
        segments = [[int(v) for v in line.split(",")] for line in
                    (GT / "c943_segments.csv").read_text().strip().splitlines()]
        meshes = []
        for ids in segments:
            sub = indexed.loc[ids]
            mesh = segment_mesh(
                sub[["Xum", "Yum", "Zum"]].to_numpy(float), sub["Rum"].to_numpy(float)
            )
            if not mesh.is_empty:
                meshes.append(mesh)
        combined = concatenate(meshes)

        meta = _scalars("c943_mesh_meta.csv")
        expected_v = np.loadtxt(GT / "c943_mesh_vertices.csv", delimiter=",")
        expected_f = np.loadtxt(GT / "c943_mesh_faces.csv", delimiter=",").astype(np.int64) - 1

        assert combined.n_vertices == int(meta["nVertices"])
        assert len(combined) == int(meta["nFaces"])
        # Vertex order matches too, which needs MATLAB's column-major surf2patch.
        assert np.abs(expected_v - combined.vertices).max() < 1e-9
        assert np.array_equal(
            np.unique(np.sort(expected_f, axis=1), axis=0),
            np.unique(np.sort(combined.faces, axis=1), axis=0),
        )
        assert combined.surface_area() == pytest.approx(meta["area"], rel=1e-12)

    def test_volume_deviation_is_matlabs_mixed_face_winding(self, neurons):
        """MATLAB's condensed mesh winds most components inward and some outward.

        Summing signed volumes across it partly cancels; per-component absolute
        volume is the meaningful number, and that is what the port reports.
        """
        import trimesh

        from sbfsem.geometry.mesh import Mesh

        meta = _scalars("c943_mesh_meta.csv")
        vertices = np.loadtxt(GT / "c943_mesh_vertices.csv", delimiter=",")
        faces = np.loadtxt(GT / "c943_mesh_faces.csv", delimiter=",").astype(np.int64) - 1

        def signed(mesh):
            v = mesh.vertices[mesh.faces]
            return float(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum() / 6)

        assert signed(Mesh(vertices, faces)) == pytest.approx(-meta["volume"], rel=1e-9)
        components = trimesh.Trimesh(vertices, faces, process=False).split(
            only_watertight=False
        )
        inward = sum(1 for c in components
                     if signed(Mesh(np.asarray(c.vertices), np.asarray(c.faces))) < 0)
        assert 0 < inward < len(components), "MATLAB's winding really is mixed"
        oriented = sum(abs(signed(Mesh(np.asarray(c.vertices), np.asarray(c.faces))))
                       for c in components)
        assert oriented > meta["volume"]


def test_documented_crashes_reproduced_under_matlab():
    """The §4 findings were run under MATLAB R2023b; all of them reproduce.

    This guards the DEVIATIONS table against drifting away from what was actually
    observed: if the transcript is regenerated and a finding stops reproducing,
    the claim in the docs needs revisiting.
    """
    transcript = (GT / "verify_findings_output.txt").read_text()
    # Only the verdict lines; the finding IDs also appear in section headers.
    verdicts = [line for line in transcript.splitlines()
                if "CONFIRMED" in line or "NOT REPRODUCED" in line]
    for finding in ("F001", "F145", "F146", "F164", "F089", "F142",
                    "F004", "F007", "F014", "F038", "F127"):
        matches = [line for line in verdicts if finding in line]
        assert matches, f"{finding} is missing from the transcript"
        assert all("CONFIRMED" in line for line in matches), \
            f"{finding} no longer reproduces: {matches}"
    assert "NOT REPRODUCED" not in transcript
    # The typo, next to the correct near-duplicates that establish intent.
    assert "micron2ipl.m        meshgrid(INL.newYPts, INL.newYPts)" in transcript
    assert "stratificationMap.m meshgrid(INL.newXPts, INL.newYPts)" in transcript
    # Mitochondrial volume: 5.1x inflated for three annotations.
    assert "5.1x inflated" in transcript


# ------------------------------------------------------------- synapse geometry
class TestSynapseAxes:
    """Viking stores each synapse as a LINESTRING, not a disc.

    Nothing in the MATLAB toolbox read that segment -- ``synapseSphere`` drew an
    isotropic marker at the centroid -- so there is no ground truth to compare
    against.  These assert the segment is being read correctly against the raw
    annotation data instead.
    """

    def test_centers_match_the_sphere_path(self, neurons):
        neuron = neurons[1441]
        centers, _, _ = neuron.get_synapse_axes("RibbonPost")
        np.testing.assert_allclose(centers, neuron.get_synapse_xyz("RibbonPost"))

    def test_axis_is_a_unit_vector_in_the_section_plane(self, neurons):
        _, axes, halves = neurons[1441].get_synapse_axes("RibbonPost")
        oriented = np.linalg.norm(axes, axis=1) > 0
        assert oriented.all(), "every RibbonPost annotation carries a LINESTRING"
        np.testing.assert_allclose(np.linalg.norm(axes[oriented], axis=1), 1.0)
        # Both endpoints sit on one section, so the axis has no Z component.
        np.testing.assert_allclose(axes[:, 2], 0.0)

    def test_half_lengths_are_sub_micron(self, neurons):
        _, _, halves = neurons[1441].get_synapse_axes("RibbonPost")
        assert 0.01 < float(np.median(halves)) < 1.0

    def test_midpoint_equals_the_node_coordinate(self, neurons):
        """The segment midpoint reproduces VolumeX/VolumeY, so the two agree."""
        from sbfsem.io.wkt import linestring_endpoints

        neuron = neurons[1441]
        ids = neuron.synapse_ids("RibbonPost")
        mask = neuron.nodes["ParentID"].isin(ids.tolist()) & neuron.nodes["Unique"]
        rows = neuron.nodes.loc[mask]
        checked = 0
        for _, row in rows.iterrows():
            segment = linestring_endpoints(row["VolumeShape"])
            if segment is None:
                continue
            midpoint = segment.mean(axis=0)
            assert midpoint[0] == pytest.approx(row["VolumeX"], abs=0.5)
            assert midpoint[1] == pytest.approx(row["VolumeY"], abs=0.5)
            checked += 1
        assert checked > 10

    def test_absent_type_returns_empty_arrays(self, neurons):
        centers, axes, halves = neurons[1441].get_synapse_axes("GapJunction")
        assert centers.shape == (0, 3)
        assert axes.shape == (0, 3)
        assert halves.shape == (0,)


# -- colormaps ---------------------------------------------------------------
def _captured_colormaps(n: int) -> dict[str, np.ndarray]:
    """``capture_colormaps.m`` stacks the maps in enumeration order, N rows each."""
    from sbfsem.util.colormaps import ColorMaps

    rows = np.loadtxt(GT / f"colormaps_{n}.csv", delimiter=",")
    assert rows.shape == (len(ColorMaps) * n, 3)
    return {m.value: rows[i * n:(i + 1) * n] for i, m in enumerate(ColorMaps)}


@pytest.mark.parametrize("n", [256, 16])
def test_colormaps_are_matlabs_own_tables(n):
    """Every map RenderApp offered, at the menu default and at a Levels edit.

    The shipped tables are rounded to six decimals, hence the tolerance; the
    three MATLAB builtins are recomputed exactly.
    """
    from sbfsem.util.colormaps import ColorMaps, colormap

    captured = _captured_colormaps(n)
    for member in ColorMaps:
        if member is ColorMaps.RedBlue:
            continue  # asserted separately: the port corrects it
        ours = colormap(member, n)
        tol = 1e-12 if member.value in ("Gray", "Bone", "Hsv") else 1e-6
        np.testing.assert_allclose(ours, captured[member.value], atol=tol, err_msg=member.value)


def test_redblue_is_reversed_rather_than_channel_swapped():
    """``ColorMaps.RedBlue`` applied ``fliplr`` to an Nx3 table (§6.4).

    MATLAB therefore shipped the lbmap table with R and B swapped: a purple to
    orange ramp. The port reverses it instead, blue to red, as
    ``FigureView.setColormap('redblue')`` did with ``flipud``.
    """
    from sbfsem.util.colormaps import colormap

    raw = np.loadtxt(GT / "colormap_redblue_lbmap_raw.csv", delimiter=",")
    shipped = _captured_colormaps(256)["RedBlue"]
    np.testing.assert_allclose(shipped, raw[:, ::-1], atol=1e-12)  # what MATLAB showed
    np.testing.assert_allclose(colormap("RedBlue", 256), raw[::-1], atol=1e-6)
    assert colormap("RedBlue", 2)[0][2] > colormap("RedBlue", 2)[0][0]  # starts blue
