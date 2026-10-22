"""A flattened IPL depth axis at 1:1: Z replaced by fraction x one stated thickness.

James Anderson's request of 2026-10-22, stage 2: draw cells with the INL and GCL
flat, Z scaled back so X/Y:Z is 1:1.  Each point's Z becomes its depth fraction
(:meth:`IPLBoundaries.depth`) times a thickness, by default the volume's median
IPL thickness along the normals; the INL sits at 0 and the GCL at that thickness
in every volume.  The analytic cases know the flattened Z exactly.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from sbfsem.analysis.boundary import (
    IPL_METHODS,
    BoundarySurface,
    IPLBoundaries,
    load_ipl_boundaries,
    thickness_summary,
)
from sbfsem.geometry import meshops
from sbfsem.geometry.mesh import Mesh

SOURCE = "NeitzInferiorMonkey"


def _surface(layer: str, height, *, lo: float = 0.0, hi: float = 200.0, n: int = 201) -> BoundarySurface:
    """A boundary whose fit is ``height(x, y)`` sampled on an n x n grid (rows are Y)."""
    pts = np.linspace(lo, hi, n)
    x, y = np.meshgrid(pts, pts)
    return BoundarySurface(layer, SOURCE, np.zeros((3, 3)), pts, pts, height(x, y).astype(float))


def _with_markers(b: IPLBoundaries) -> IPLBoundaries:
    """Markers all over both fits, so every column of the thickness summary counts."""
    grid = np.array([[x, y, 0.0] for x in range(0, 201, 20) for y in range(0, 201, 20)])
    for s in (b.inl, b.gcl):
        s.marker_locations = grid / 0.0075  # Viking pixels at Inferior's 7.5 nm
    return b


def _points(b: IPLBoundaries, fractions, xy) -> np.ndarray:
    """Points between the two fits by the column's fraction, at each XY."""
    xy = np.asarray(xy, dtype=float)
    z_inl = b.inl.xy_eval(xy[:, 0], xy[:, 1])
    z_gcl = b.gcl.xy_eval(xy[:, 0], xy[:, 1])
    return np.concatenate([np.column_stack([xy, z_inl + f * (z_gcl - z_inl)]) for f in fractions])


XY = np.array([[60.0, 70.0], [100.0, 100.0], [140.0, 90.0], [80.0, 130.0]])
FRACTIONS = (-0.1, 0.0, 0.25, 0.5, 0.9, 1.0, 1.15)


# -- tilted planes: the flattened Z known exactly ------------------------------------
def test_a_tilted_ipl_of_one_thickness_flattens_to_each_points_distance_from_the_inl():
    """Two parallel planes tilted 20°, 30 µm apart along Z: the IPL is 30 cos 20°
    = 28.19 µm thick along its normal everywhere, which is the volume's median.
    Flattened by it, every point's Z is its distance from the INL plane along
    the normal, exactly, at every depth inside and outside the IPL; X and Y do
    not move."""
    theta = np.radians(20.0)
    gap = 30.0
    b = _with_markers(IPLBoundaries(_surface("INL", lambda x, y: 10 + np.tan(theta) * x),
                                    _surface("GCL", lambda x, y: 10 + gap + np.tan(theta) * x)))
    assert b.median_thickness_um == pytest.approx(gap * np.cos(theta), rel=1e-9)
    pts = _points(b, FRACTIONS, XY)
    # The signed distance from the INL plane z = 10 + x tan θ, along its unit normal.
    from_inl = (pts[:, 2] - 10 - np.tan(theta) * pts[:, 0]) * np.cos(theta)
    for method in IPL_METHODS:
        flat = b.flatten(pts, method)
        np.testing.assert_array_equal(flat[:, :2], pts[:, :2])
        np.testing.assert_allclose(flat[:, 2], from_inl, atol=1e-9)
        np.testing.assert_allclose(flat[:, 2], np.repeat(FRACTIONS, len(XY)) * gap * np.cos(theta),
                                   atol=1e-9)
    # Along the normal the depth in µm is the flattened Z itself.
    np.testing.assert_allclose(b.flatten(pts, "normal")[:, 2], b.depth(pts, "normal").depth_um, atol=1e-9)


def test_planes_that_are_not_parallel_flatten_to_the_fraction_times_the_thickness():
    """A flat INL at z = 0 and a GCL tilted to z = 20 + 0.2 x, flattened by 25 µm:
    along the normal Z' = 25 d_inl / (d_inl + d_gcl) with d_inl = z and d_gcl the
    distance to the tilted plane; along Z, Z' = 25 z / (20 + 0.2 x)."""
    b = IPLBoundaries(_surface("INL", lambda x, y: 0 * x),
                      _surface("GCL", lambda x, y: 20 + 0.2 * x))
    pts = _points(b, (0.0, 0.1, 0.5, 0.9, 1.0), XY)
    x, z = pts[:, 0], pts[:, 2]
    d_gcl = (20 + 0.2 * x - z) / np.sqrt(1 + 0.2 ** 2)
    np.testing.assert_allclose(b.flatten(pts, "normal", 25.0)[:, 2], 25 * z / (z + d_gcl), atol=1e-9)
    np.testing.assert_allclose(b.flatten(pts, "column", 25.0)[:, 2], 25 * z / (20 + 0.2 * x), atol=1e-12)


def test_a_gcl_below_the_inl_still_puts_the_inl_at_0_and_the_gcl_at_the_thickness():
    """The Neitz volumes have the GCL at smaller Z than the INL: flattened, the
    INL is at 0 and the GCL at +T there too, as in RC1 and RC2."""
    b = IPLBoundaries(_surface("INL", lambda x, y: 80 + 0.1 * y),
                      _surface("GCL", lambda x, y: 50 + 0.1 * y))
    assert b.gcl_side == -1.0
    pts = _points(b, (0.0, 0.3, 1.0), XY)
    for method in IPL_METHODS:
        np.testing.assert_allclose(b.flatten(pts, method, 27.5)[:, 2],
                                   np.repeat([0.0, 0.3 * 27.5, 27.5], len(XY)), atol=1e-9)


def test_a_point_with_no_depth_has_no_flattened_z_and_a_bad_thickness_is_refused():
    b = IPLBoundaries(_surface("INL", lambda x, y: 0 * x), _surface("GCL", lambda x, y: 0 * x + 20))
    flat = b.flatten(np.array([[100.0, 100.0, 10.0], [500.0, 100.0, 10.0]]), "column", 20.0)
    assert flat[0, 2] == pytest.approx(10.0)
    assert np.isnan(flat[1, 2])  # beyond the fits
    for bad in (0.0, -3.0, float("nan")):
        with pytest.raises(ValueError, match="thickness above 0"):
            b.flatten(np.zeros((1, 3)), "column", bad)
    # No marker near any column: no median to scale by.
    for s in (b.inl, b.gcl):
        s.marker_locations = np.full((3, 3), 1e8)
    assert np.isnan(b.median_thickness_um)
    with pytest.raises(ValueError, match="thickness above 0"):
        b.flatten(np.zeros((1, 3)))


# -- the mesh -----------------------------------------------------------------------
def test_a_mesh_flattens_drops_what_has_no_depth_and_says_what_it_did():
    theta = np.radians(15.0)
    b = IPLBoundaries(_surface("INL", lambda x, y: 10 + np.tan(theta) * y),
                      _surface("GCL", lambda x, y: 50 + np.tan(theta) * y))
    inside = _points(b, (0.0, 0.5, 1.0), XY[:2])  # six vertices inside the fits
    outside = np.array([[400.0, 100.0, 30.0]])    # beyond them
    vertices = np.vstack([inside, outside])
    faces = np.array([[0, 1, 2], [3, 4, 5], [0, 2, 6]])
    data = np.arange(7, dtype=float)
    mesh = Mesh(vertices.copy(), faces, data, {"mesh_id": "abc"})
    flat = meshops.flatten_ipl(mesh, b, "normal", 30.0)
    assert flat.n_vertices == 6 and len(flat.faces) == 2  # the face on the far vertex is gone
    np.testing.assert_allclose(flat.vertices[:, 2], [0, 0, 15, 15, 30, 30], atol=1e-9)
    np.testing.assert_array_equal(flat.vertex_data, data[:6])
    assert flat.meta["ipl_flatten"] == {"thickness_um": 30.0, "method": "normal", "dropped_vertices": 1}
    np.testing.assert_array_equal(mesh.vertices, vertices)  # the original is untouched
    assert "ipl_flatten" not in mesh.meta
    # MATLAB's flattenRender is unchanged: the fraction itself on Z.
    np.testing.assert_allclose(meshops.flatten_render(mesh, b).vertices[:6, 2], [0, 0, 0.5, 0.5, 1, 1],
                               atol=1e-9)


def test_the_library_flattens_along_z_unless_told_otherwise():
    """Ground rule 1: the column is the library's default here too."""
    for fn in (IPLBoundaries.flatten, meshops.flatten_ipl):
        assert inspect.signature(fn).parameters["method"].default == "column", fn.__qualname__
        assert inspect.signature(fn).parameters["thickness_um"].default is None, fn.__qualname__


def test_each_marked_volume_flattens_by_its_median_thickness_along_the_normals():
    """2026-10-22: RC1 15.6 µm, Temporal 23.8, Inferior 27.7, Nasal 28.7, RC2 42.1."""
    expected = {"RC1": 15.6, "NeitzTemporalMonkey": 23.8, "NeitzInferiorMonkey": 27.7,
                "NeitzNasalMonkey": 28.7, "RC2": 42.1}
    for source, median in expected.items():
        b = load_ipl_boundaries(source)
        assert b.median_thickness_um == thickness_summary(b)["normal_um"]["median"], source
        assert b.median_thickness_um == pytest.approx(median, abs=0.05), source


# -- the service and the API --------------------------------------------------------
def test_the_option_is_off_everywhere_by_default_and_a_view_option():
    from sbfsem.service import render_schema
    from sbfsem.service.facade import SbfsemService

    field = render_schema.field_by_key("ipl_flatten")
    assert field.default is False and field.app_value() is False
    assert field.scope == "view" and field.wire == ("mesh",) and not field.in_render
    for fn in (SbfsemService.render_mesh, SbfsemService.render_buffers, SbfsemService.clipped_mesh,
               SbfsemService.boundary_mesh, SbfsemService.vessel_mesh, SbfsemService.skeleton,
               SbfsemService.synapse_mesh):
        assert inspect.signature(fn).parameters["ipl_flatten"].default is False, fn.__qualname__


def test_a_volume_without_boundary_markers_is_not_flattened():
    from sbfsem.service.facade import SbfsemService

    svc = SbfsemService()
    mesh = Mesh(np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]]), np.array([[0, 1, 2]]))
    for source in ("cped", "rpc1", "rpc2"):
        with pytest.raises(ValueError, match="no IPL boundary markers"):
            svc.flatten(mesh, source)


@pytest.fixture(scope="module")
def client(request):
    from fastapi.testclient import TestClient

    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem_web.app import create_app

    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    app = create_app()
    with TestClient(app) as c:
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield c


def _median():
    return load_ipl_boundaries(SOURCE).median_thickness_um


def test_the_mesh_route_flattens_by_the_volumes_median_and_says_so(client):
    plain = client.get("/api/neurons/i/943/mesh").json()
    assert plain["ipl_flatten_um"] is None and plain["mesh_id"]
    for method in IPL_METHODS:
        flat = client.get(f"/api/neurons/i/943/mesh?ipl_flatten=true&ipl_method={method}").json()
        assert flat["ipl_flatten_um"] == pytest.approx(_median(), abs=1e-9)
        assert flat["ipl_flatten_method"] == method
        assert flat["mesh_id"] is None  # not the cached render
        assert flat["n_vertices"] + flat["ipl_flatten_dropped"] == plain["n_vertices"]
        v = np.asarray(flat["vertices"], dtype=float).reshape(-1, 3)
        p = np.asarray(plain["vertices"], dtype=float).reshape(-1, 3)
        # X and Y do not move; Z is the depth fraction times the median.
        b = load_ipl_boundaries(SOURCE)
        expected = b.flatten(p, method, _median())
        keep = np.isfinite(expected[:, 2])
        np.testing.assert_allclose(v, expected[keep], atol=2e-4)  # the wire's 1e-4 µm step
    assert client.get("/api/neurons/i/943/mesh?ipl_flatten=maybe").status_code == 422


def test_a_flattened_export_is_the_flattened_surface_and_names_its_thickness(client):
    t = round(_median(), 4)
    reply = client.get("/api/neurons/i/943/export/obj?ipl_flatten=true&ipl_method=normal")
    assert reply.status_code == 200
    assert f"-ipl-flat-{t:g}um.obj" in reply.headers["content-disposition"]
    text = reply.text
    assert text.startswith(f"# ipl_flatten_um {t}\n")
    assert "INL at Z = 0" in text and "# ipl_method normal" in text
    z = np.array([float(line.split()[3]) for line in text.splitlines() if line.startswith("v ")])
    shown = client.get("/api/neurons/i/943/mesh?ipl_flatten=true&ipl_method=normal").json()
    np.testing.assert_allclose(np.sort(z), np.sort(np.asarray(shown["vertices"], dtype=float)[2::3]),
                               atol=2e-4)
    plain = client.get("/api/neurons/i/943/export/obj")
    assert "ipl-flat" not in plain.headers["content-disposition"]
    assert not plain.text.startswith("# ipl_flatten")
    for fmt in ("ply", "stl", "glb", "dae"):
        reply = client.get(f"/api/neurons/i/943/export/{fmt}?ipl_flatten=true&ipl_method=normal")
        assert reply.status_code == 200, fmt
        assert f"-ipl-flat-{t:g}um.{fmt}" in reply.headers["content-disposition"], fmt
        assert f"ipl_flatten_um {t}".encode() in reply.content or b'"ipl_flatten_um":' in reply.content, fmt
    wire = client.get("/api/neurons/i/943/export/ply?wireframe=true&ipl_flatten=true")
    assert wire.status_code == 200 and f"comment ipl_flatten_um {t}".encode() in wire.content


def test_the_boundary_sheets_lie_flat_at_0_and_at_the_thickness(client):
    t = _median()
    for layer, z in (("INL", 0.0), ("GCL", t)):
        sheet = client.get(f"/api/volumes/i/boundary/{layer}/mesh?ipl_flatten=true&ipl_method=normal").json()
        assert sheet["ipl_flatten_um"] == pytest.approx(t)
        v = np.asarray(sheet["vertices"], dtype=float).reshape(-1, 3)
        assert len(v) > 1000
        np.testing.assert_allclose(v[:, 2], z, atol=2e-3)
        column = client.get(f"/api/volumes/i/boundary/{layer}/mesh?ipl_flatten=true&ipl_method=column").json()
        np.testing.assert_allclose(np.asarray(column["vertices"], dtype=float)[2::3], z, atol=2e-4)
    raw = client.get("/api/volumes/i/boundary/INL/mesh").json()
    assert raw["ipl_flatten_um"] is None
    assert client.get("/api/volumes/cped/boundary/INL/mesh?ipl_flatten=true").status_code == 400


def test_the_skeleton_and_the_synapse_markers_follow(client):
    t = _median()
    raw = client.get("/api/neurons/i/943/skeleton").json()
    flat = client.get("/api/neurons/i/943/skeleton?ipl_flatten=true&ipl_method=normal").json()
    b = load_ipl_boundaries(SOURCE)
    pts = np.concatenate([np.asarray(s["points_um"], dtype=float).reshape(-1, 3) for s in raw["segments"]])
    expected = b.flatten(pts, "normal", t)
    got = np.concatenate([np.asarray(s["points_um"], dtype=float).reshape(-1, 3) for s in flat["segments"]])
    np.testing.assert_allclose(got, expected[np.isfinite(expected[:, 2])], atol=1e-9)
    assert flat["soma_xyz_um"][:2] == raw["soma_xyz_um"][:2]
    assert flat["soma_xyz_um"][2] == pytest.approx(b.flatten(np.array([raw["soma_xyz_um"]]), "normal", t)[0, 2])
    types = client.get("/api/neurons/i/943").json()
    for entry in types.get("synapse_counts", [])[:1]:
        name = entry["local_name"]
        mesh = client.get(f"/api/neurons/i/943/synapses/{name}/mesh?ipl_flatten=true&ipl_method=normal").json()
        assert mesh["ipl_flatten_um"] == pytest.approx(t)


def test_an_attached_render_never_keeps_the_flattening(client):
    reply = client.put("/api/neurons/i/943/render-prefs", json={"method": "cylinder", "ipl_flatten": True})
    assert reply.status_code == 200
    assert "ipl_flatten" not in reply.json()["render"]
    client.delete("/api/neurons/i/943/render-prefs")
