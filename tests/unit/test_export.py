from __future__ import annotations

import json

import numpy as np
import pytest
from lxml import etree

from sbfsem.export.collada import COLLADA_NS, MICROMETER, write_collada
from sbfsem.export.images import volume_to_frames, write_gif, write_image_stack
from sbfsem.export.json_v2 import SCHEMA_VERSION, read_neuron_json, write_neuron_json
from sbfsem.export.mesh_formats import (
    mesh_to_glb_bytes,
    read_mesh,
    write_gltf,
    write_obj,
    write_ply,
    write_stl,
)
from sbfsem.export.pajek import write_edge_csv, write_link_list, write_pajek
from sbfsem.export.swc import SWC_TYPES, swc_table, write_swc
from sbfsem.geometry.primitives import cuboid
from sbfsem.network.graph import network_from_links


@pytest.fixture(scope="module")
def box():
    return cuboid((2, 2, 2))


def test_swc_types_every_node(c943):
    """F049: MATLAB never typed or parented the last node of each segment."""
    table = swc_table(c943)
    assert len(table) == c943.graph().number_of_nodes()
    assert table["T"].isin(SWC_TYPES.values()).all()
    assert (table["T"] != 0).all()
    assert (table["P"] == -1).sum() == 1
    # Every parent index precedes its child, as SWC requires.
    assert (table.loc[table["P"] != -1, "P"] < table.loc[table["P"] != -1, "n"]).all()


def test_swc_emits_a_soma_type(c943):
    # F050: MATLAB never emitted type 1 and ignored hasSoma.
    assert (swc_table(c943)["T"] == SWC_TYPES["soma"]).any()
    assert not (swc_table(c943, has_soma=False)["T"] == SWC_TYPES["soma"]).any()


def test_swc_marks_axon_nodes(c943):
    ids = c943.get_cell_nodes()["ID"].iloc[:3].tolist()
    table = swc_table(c943, axon_ids=ids)
    assert (table.loc[table["ID"].isin(ids), "T"] == SWC_TYPES["axon"]).all()


def test_swc_file_header(c943, tmp_path):
    path = write_swc(c943, tmp_path / "c943.swc")
    text = path.read_text()
    assert "# ORIGINAL_SOURCE SBFSEM-pytools" in text
    assert "# CREATURE monkey" in text
    assert "# REGION Inferior" in text
    # F052: the MATLAB version date used invalid datestr symbols.
    version_line = next(line for line in text.splitlines() if "VERSION_DATE" in line)
    assert len(version_line.split()[-1].split("-")) == 3
    data = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert len(data) == len(swc_table(c943))


def test_swc_plain_dendrite_typing(c943):
    """branch_types=False: no 5s or 6s, which modern readers take for custom types."""
    table = swc_table(c943, branch_types=False)
    assert not table["T"].isin([SWC_TYPES["fork"], SWC_TYPES["end"]]).any()
    assert set(table["T"].unique()) <= {SWC_TYPES["soma"], SWC_TYPES["dendrite"]}
    # Same rows, same order, same parents: only the type column differs.
    default = swc_table(c943)
    assert table["ID"].tolist() == default["ID"].tolist()
    assert table["P"].tolist() == default["P"].tolist()
    assert (default["T"] == SWC_TYPES["fork"]).any()


def test_swc_header_spells_out_the_type_codes(c943, tmp_path):
    text = write_swc(c943, tmp_path / "a.swc").read_text()
    assert "# STRUCTURE c943 in NeitzInferiorMonkey" in text
    assert "# TYPE_CODES 1 soma, 3 dendrite, 5 branch point, 6 end point" in text
    assert "not the Viking TypeCode" in text
    plain = write_swc(c943, tmp_path / "b.swc", branch_types=False).read_text()
    assert "# TYPE_CODES 1 soma, 3 dendrite\n" in plain


def test_json_v2_roundtrip(c943, tmp_path):
    path = write_neuron_json(c943, tmp_path / "c943.json")
    payload = json.loads(path.read_text())
    assert payload["schema"] == SCHEMA_VERSION
    again = read_neuron_json(path)
    assert again.id == c943.id
    assert len(again.nodes) == len(c943.nodes)
    assert len(again.synapses) == len(c943.synapses)
    np.testing.assert_allclose(again.get_cell_xyz(), c943.get_cell_xyz())
    assert again.synapse_names(True) == c943.synapse_names(True)


def test_json_v2_dispatches_to_the_legacy_reader(i943_path):
    neuron = read_neuron_json(i943_path)
    assert neuron.id == 943


def test_json_v2_carries_geometries(tmp_path, request):
    from sbfsem.io.legacy_json import read_legacy_json

    source = read_legacy_json(
        request.path.parents[1] / "fixtures" / "i90000_closedcurve.json"
    )
    path = write_neuron_json(source, tmp_path / "c90000.json")
    again = read_neuron_json(path)
    assert len(again.geometries) == len(source.geometries)
    np.testing.assert_allclose(
        np.asarray(again.geometries["Curve"].iloc[0][0]),
        np.asarray(source.geometries["Curve"].iloc[0][0]),
    )


def test_json_v2_moves_old_files_outlines_beside_their_discs(tmp_path):
    """Files written before 2026-09-11 hold outlines in the section mosaic
    frame; each outline's own node carries both frames, so the reader moves
    it.  A file that names the frame is left alone."""
    node = {"ID": 7, "ParentID": 5, "VolumeX": 1100.0, "VolumeY": 2200.0, "Z": 10,
            "Radius": 3.0, "X": 100.0, "Y": 200.0, "OffEdge": 0, "Terminal": 0,
            "TypeCode": 6}
    square = [[0, 0], [0.03, 0], [0.03, 0.03], [0, 0.03]]
    payload = {
        "schema": SCHEMA_VERSION, "id": 5, "source": "i",
        "volumeScale": [7.5, 7.5, 90.0], "transform": "Standard", "viking": {},
        "omittedIDs": [], "nodes": [node], "edges": [], "synapses": [],
        "geometries": [{"ID": 7, "ParentID": 5, "Z": 10, "Zum": 0.9, "Curve": [square]}],
    }
    old = tmp_path / "old.json"
    old.write_text(json.dumps(payload))
    moved = read_neuron_json(old)
    np.testing.assert_allclose(
        moved.geometries["Curve"].iloc[0][0], np.asarray(square) + [7.5, 15.0]
    )
    assert not moved.geometries["Registered"].iloc[0]

    payload["curveFrame"] = "volume"
    new = tmp_path / "new.json"
    new.write_text(json.dumps(payload))
    kept = read_neuron_json(new)
    np.testing.assert_allclose(kept.geometries["Curve"].iloc[0][0], square)
    # And what this version writes names the frame.
    assert json.loads(write_neuron_json(kept, tmp_path / "again.json").read_text())["curveFrame"] == "volume"


def test_collada_structure(box, tmp_path):
    path = write_collada({"c943": box}, tmp_path / "c943.dae")
    root = etree.parse(str(path)).getroot()
    ns = {"c": COLLADA_NS}
    assert root.get("version") == "1.4.1"
    assert root.find(".//c:up_axis", ns).text == "Z_UP"
    # DEVIATIONS 6.1: micron data, so 1 unit == 1e-6 m.
    assert float(root.find(".//c:unit", ns).get("meter")) == MICROMETER
    # F039: the free function emitted "instance_visual_schene".
    assert root.find(".//c:instance_visual_scene", ns) is not None
    triangles = root.find(".//c:triangles", ns)
    assert int(triangles.get("count")) == len(box.faces)
    indices = [int(v) for v in triangles.find("c:p", ns).text.split()]
    assert len(indices) == box.faces.size
    assert min(indices) == 0  # zero-based, as MATLAB emitted


def test_collada_offsets_multiple_meshes(box, tmp_path):
    """F040: the scene exporter concatenated faces without offsetting."""
    path = write_collada(
        {"a": box, "b": box.translate((5, 0, 0))}, tmp_path / "scene.dae"
    )
    root = etree.parse(str(path)).getroot()
    ns = {"c": COLLADA_NS}
    geometries = root.findall(".//c:geometry", ns)
    assert len(geometries) == 2
    for geometry in geometries:
        indices = [int(v) for v in geometry.find(".//c:p", ns).text.split()]
        assert max(indices) < box.n_vertices


def test_collada_meter_override(box, tmp_path):
    path = write_collada({"a": box}, tmp_path / "old.dae", meter=0.1, unit_name="meter")
    root = etree.parse(str(path)).getroot()
    assert root.find(f".//{{{COLLADA_NS}}}unit").get("meter") == "0.1"


def test_mesh_formats_roundtrip(box, tmp_path):
    for writer, name in [
        (write_stl, "a.stl"), (write_obj, "a.obj"), (write_ply, "a.ply"),
        (write_gltf, "a.glb"),
    ]:
        path = writer(box, tmp_path / name)
        assert path.stat().st_size > 0
        loaded = read_mesh(path)
        assert loaded.n_vertices >= 8
        np.testing.assert_allclose(loaded.bounds, box.bounds, atol=1e-4)


def test_glb_bytes(box):
    data = mesh_to_glb_bytes(box, "c943")
    assert data[:4] == b"glTF"


def test_pajek_and_link_lists(tmp_path):
    links = __import__("pandas").DataFrame(
        {
            "SourceID": [1, 1, 2],
            "TargetID": [2, 2, 3],
            "SynapseID": [10, 11, 12],
            "SynapseType": ["RibbonPre", "RibbonPre", "ConvPre"],
        }
    )
    network = network_from_links(links)
    text = write_pajek(network, tmp_path / "n.net").read_text()
    assert text.startswith("*Vertices 3")
    assert "*Arcs" in text
    assert "1 2 2" in text  # two parallel synapses become weight 2

    assert write_link_list(links, tmp_path / "links.txt").stat().st_size > 0
    csv = write_edge_csv(network, tmp_path / "edges.csv").read_text()
    assert "SourceID,TargetID,Weight,Types" in csv


def test_image_exports(tmp_path):
    volume = np.zeros((20, 20, 4), dtype=bool)
    for k in range(4):
        volume[5:15, 5:15, k] = True
    frames = volume_to_frames(volume)
    assert len(frames) == 4
    assert frames[0].dtype == np.uint8
    assert frames[0].max() == 255
    assert volume_to_frames(volume, invert=True)[0].min() == 0
    assert write_gif(volume, tmp_path / "a.gif").stat().st_size > 0
    assert len(write_image_stack(volume, tmp_path / "stack")) == 4
    with pytest.raises(ValueError):
        write_gif(np.zeros((4, 4, 0)), tmp_path / "b.gif")


# -- wireframe exports ------------------------------------------------------

def test_wireframe_obj_and_ply_carry_edges(box, tmp_path):
    from sbfsem.export.mesh_formats import (
        mesh_edges,
        write_wireframe_obj,
        write_wireframe_ply,
    )

    edges = mesh_edges(box)
    # Euler: a closed triangle mesh has E = 3F/2.
    assert len(edges) == 3 * len(box.faces) // 2

    obj = write_wireframe_obj(box, tmp_path / "wf.obj").read_text()
    assert obj.count("\nl ") + obj.startswith("l ") == len(edges)
    assert obj.count("v ") == len(box.vertices)

    ply = write_wireframe_ply(box, tmp_path / "wf.ply").read_text()
    assert f"element edge {len(edges)}" in ply
    assert f"element vertex {len(box.vertices)}" in ply


def test_wireframe_stl_builds_closed_rods(box, tmp_path):
    from sbfsem.export.mesh_formats import mesh_edges, write_wireframe_stl

    path = write_wireframe_stl(box, tmp_path / "wf.stl", thickness_um=0.05)
    import trimesh

    rods = trimesh.load(path)
    # Eight triangles per edge: three sides of two triangles plus two caps.
    assert len(rods.faces) == 8 * len(mesh_edges(box))


def test_wireframe_data_formats_roundtrip(box, tmp_path):
    from sbfsem.export.mesh_formats import (
        mesh_edges,
        write_wireframe_hdf5,
        write_wireframe_npy,
        write_wireframe_npz,
        write_wireframe_table,
    )

    edges = mesh_edges(box)

    csv = write_wireframe_table(box, tmp_path / "wf.csv").read_text()
    lines = csv.strip().splitlines()
    assert lines[0].startswith("v1,v2,x1_um")
    assert len(lines) == len(edges) + 1

    tsv = write_wireframe_table(box, tmp_path / "wf.tsv", delimiter="\t").read_text()
    assert "\t" in tsv.splitlines()[0]

    segs = np.load(write_wireframe_npy(box, tmp_path / "wf.npy"))
    assert segs.shape == (len(edges), 2, 3)
    np.testing.assert_allclose(segs, box.vertices[edges])

    with np.load(write_wireframe_npz(box, tmp_path / "wf.npz")) as z:
        np.testing.assert_allclose(z["vertices"], box.vertices)
        np.testing.assert_array_equal(z["edges"], edges)

    import h5py

    with h5py.File(write_wireframe_hdf5(box, tmp_path / "wf.h5", name="c1"), "r") as fh:
        np.testing.assert_allclose(fh["vertices"][...], box.vertices)
        np.testing.assert_array_equal(fh["edges"][...], edges)
        assert fh.attrs["units"] == "micron"
        assert fh.attrs["name"] == "c1"


def test_wireframe_writers_take_line_segments(tmp_path):
    """Segments share their common endpoints; empty, repeated and broken ones go."""
    import h5py
    import trimesh

    from sbfsem.export.mesh_formats import (
        segment_edges,
        write_wireframe_hdf5,
        write_wireframe_npy,
        write_wireframe_npz,
        write_wireframe_obj,
        write_wireframe_ply,
        write_wireframe_stl,
        write_wireframe_table,
    )

    a, b, c = [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 2.0, 0.5]
    segments = np.array([
        [a, b], [b, c],
        [c, b],                       # the same edge reversed
        [a, a],                       # zero length
        [a, [np.nan, 0.0, 0.0]],      # broken
    ])
    v, edges = segment_edges(segments)
    assert len(v) == 3 and len(edges) == 2
    np.testing.assert_allclose(sorted(map(tuple, v[edges].reshape(-1, 3).tolist())),
                               sorted([tuple(a), tuple(b), tuple(b), tuple(c)]))
    assert segment_edges(np.zeros((0, 2, 3)))[1].shape == (0, 2)

    notes = {"gain": 0.5, "fit": "quadratic"}
    obj = write_wireframe_obj(segments, tmp_path / "s.obj", notes=notes).read_text()
    assert obj.splitlines()[:2] == ["# gain 0.5", "# fit quadratic"]
    assert sum(ln.startswith("v ") for ln in obj.splitlines()) == 3
    assert sum(ln.startswith("l ") for ln in obj.splitlines()) == 2

    ply = write_wireframe_ply(segments, tmp_path / "s.ply", notes=notes).read_text()
    assert "comment gain 0.5" in ply and "element edge 2" in ply

    rows = write_wireframe_table(segments, tmp_path / "s.csv").read_text().strip().splitlines()
    assert len(rows) == 3
    assert np.load(write_wireframe_npy(segments, tmp_path / "s.npy")).shape == (2, 2, 3)
    with np.load(write_wireframe_npz(segments, tmp_path / "s.npz")) as z:
        np.testing.assert_allclose(z["vertices"], v)
    with h5py.File(write_wireframe_hdf5(segments, tmp_path / "s.h5", name="c1",
                                        notes=notes), "r") as fh:
        assert fh["edges"].shape == (2, 2)
        assert fh.attrs["gain"] == 0.5 and fh.attrs["fit"] == "quadratic"
    rods = trimesh.load(write_wireframe_stl(segments, tmp_path / "s.stl"))
    assert len(rods.faces) == 8 * 2
