"""Render compare's report: the pictures, the numbers, the PDF and its job.

The report (``sbfsem.export.compare_pdf``) holds every chosen rendering of a cell
to its annotations and every chosen pair to each other both ways, as a color
PDF.  These tests pin what can be checked without looking at it: the rasterizer
draws what geometry says it should, the numbers of a pair are the definitions'
(two concentric spheres have every distance known), the PDF has the pages its
plan says with their bookmarks and links, a failure is a page that says so, and
the web job starts, reports, stops and serves the file.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from pypdf import PdfReader

from sbfsem import progress
from sbfsem.analysis.compare_report import boundary_edges, field_stats, pair_result, surface_facts
from sbfsem.export.compare_pdf import (
    Plan,
    RenderingDeviation,
    ReportPane,
    ReportRendering,
    ReportSpec,
    report_colors,
    write_compare_report,
)
from sbfsem.export.pdfpage import Page, diverging_rgb, error_rgb, fmt, ink
from sbfsem.geometry.mesh import Mesh
from sbfsem.geometry.meshdiff import deviation_map, mesh_difference
from sbfsem.geometry.raster import (
    NAMED_VIEWS,
    View,
    frame_for,
    rasterize,
    section_segments,
    view_basis,
)
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.render_schema import describe_options, field_by_key, match_preset, option_text
from sbfsem_web.app import create_app


def _sphere(radius: float, subdivisions: int = 4, center=(0.0, 0.0, 0.0)) -> Mesh:
    s = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    return Mesh(np.asarray(s.vertices) + np.asarray(center), np.asarray(s.faces))


# -- the rasterizer ---------------------------------------------------------------------
def test_a_sphere_covers_its_disc_from_every_named_view():
    sphere = _sphere(10.0, 5)
    for view in NAMED_VIEWS.values():
        frame = frame_for([sphere.vertices], view, 300, 220)
        picture = rasterize(sphere.vertices, sphere.faces, frame)
        area = picture.coverage.sum() * frame.um_per_px ** 2
        assert area == pytest.approx(np.pi * 100.0, rel=0.003), view.key


def test_the_nearer_surface_hides_the_farther_one():
    """Two spheres one behind the other along +Z: from XY1 (looking down from +Z)
    the upper one's color is what shows where they overlap, from below the lower one's."""
    near, far = _sphere(3.0, 3, (0, 0, 5)), _sphere(3.0, 3, (1.0, 0, -5))
    both = Mesh(np.vstack([near.vertices, far.vertices]),
                np.vstack([near.faces, far.faces + len(near.vertices)]))
    values = np.r_[np.ones(len(near.vertices)), -np.ones(len(far.vertices))]
    for view, expected in ((NAMED_VIEWS["xy1"], 1.0), (View("xy2", "from -Z", (0, 0, -1), (0, 1, 0)), -1.0)):
        frame = frame_for([both.vertices], view, 120, 120)
        pic = rasterize(both.vertices, both.faces, frame, values=values,
                        colormap=lambda v: np.where(v[:, None] > 0, [[1.0, 0, 0]], [[0, 0, 1.0]]),
                        ambient=1.0, supersample=1)
        centre = frame.project(np.array([[0.2, 0.0, 0.0]]))[0].round().astype(int)
        r, _, b = pic.rgb[centre[1], centre[0]]
        assert (r > b) == (expected > 0), view.key


def test_views_are_the_viewers():
    right, up, toward = view_basis(NAMED_VIEWS["xy1"])
    assert np.allclose(right, [1, 0, 0]) and np.allclose(up, [0, 1, 0]) and np.allclose(toward, [0, 0, 1])
    right, up, _ = view_basis(NAMED_VIEWS["xz1"])
    assert np.allclose(right, [1, 0, 0]) and np.allclose(up, [0, 0, 1])
    right, up, _ = view_basis(NAMED_VIEWS["yz1"])
    assert np.allclose(right, [0, 1, 0]) and np.allclose(up, [0, 0, 1])
    # Flip Z mirrors the volume's Z before it is looked at, as the viewer's group does.
    flipped = View("v", "flipped", (0, -1, 0), (0, 0, 1), mirror_z=True)
    frame = frame_for([np.array([[0, 0, 0], [0, 0, 10.0]])], flipped, 50, 50)
    top, bottom = frame.project(np.array([[0, 0, 10.0], [0, 0, 0.0]]))
    assert top[1] > bottom[1], "under Flip Z a higher Z is drawn lower"


def test_a_cut_through_a_sphere_is_its_circle():
    sphere = _sphere(10.0, 5)
    seg = section_segments(sphere.vertices, sphere.faces, 6.0)
    radius = np.linalg.norm(seg.reshape(-1, 2), axis=1)
    assert len(seg) > 50
    assert radius == pytest.approx(np.full_like(radius, 8.0), abs=0.02)
    assert len(section_segments(sphere.vertices, sphere.faces, 20.0)) == 0


# -- the numbers ---------------------------------------------------------------------------
def test_concentric_spheres_give_the_known_distances_and_volume():
    big, small = _sphere(10.0, 5), _sphere(9.5, 5)
    diff = mesh_difference(big, small, sign="enclosure", both=True)
    res = pair_result(0, 1, big, small, diff, section_of=lambda z: z * 1000 / 70)
    # A is outside B by 0.5 everywhere, B inside A by 0.5; the sampling adds at most its spacing.
    assert res.ab.mean_signed_um == pytest.approx(0.5, abs=res.sample_spacing_um)
    assert res.ba.mean_signed_um == pytest.approx(-0.5, abs=res.sample_spacing_um)
    assert res.ab.outside_share == pytest.approx(1.0) and res.ba.outside_share == pytest.approx(0.0)
    assert res.chamfer_um == pytest.approx(0.5, abs=res.sample_spacing_um)
    assert 0.5 <= res.hausdorff_um <= 0.5 + 2 * res.sample_spacing_um
    # The volume between them, from the distances: the average of the two one-sided
    # integrals misses by 2 pi (R - r)^3 / 3; each one alone by about 5 %.
    exact = 4.0 / 3.0 * np.pi * (10.0 ** 3 - 9.5 ** 3)
    estimate = 0.5 * (res.ab.integral_signed_um3 - res.ba.integral_signed_um3)
    assert estimate == pytest.approx(exact, rel=0.01)
    assert abs(res.ab.integral_signed_um3 - exact) / exact > 0.03
    # Area-weighted: the surface areas the fields sum over are the meshes'.
    assert res.ab.area_um2 == pytest.approx(big.surface_area())
    assert res.ab.places and res.ab.places[0].section is not None


def test_field_stats_weigh_by_area_not_by_vertex():
    """Two squares of the same area, one meshed finely at |d| 0.1, one coarsely at
    0.6: the area mean is 0.35 whatever the tessellation, where a vertex mean leans
    to the fine one."""
    def square(x0: float, step: float) -> trimesh.Trimesh:
        box = trimesh.creation.box(extents=(1, 1, 0.001))
        v, f = trimesh.remesh.subdivide_to_size(box.vertices, box.faces, step)
        return trimesh.Trimesh(np.asarray(v) + [x0, 0, 0], f)

    fine, coarse = square(-1.0, 0.05), square(1.0, 1.0)
    mesh = Mesh(np.vstack([fine.vertices, coarse.vertices]),
                np.vstack([fine.faces, coarse.faces + len(fine.vertices)]))
    d = np.r_[np.full(len(fine.vertices), 0.1), np.full(len(coarse.vertices), 0.6)]
    stats = field_stats(mesh, d, scale_um=1.0)
    assert d.mean() < 0.2, "the fine square has most of the vertices"
    assert stats.mean_abs_um == pytest.approx(0.35, abs=1e-9)
    assert stats.within[2][1] == pytest.approx(0.5, abs=1e-9)   # within 0.25 um: the fine half


def test_surface_facts_of_a_closed_and_an_open_surface():
    sphere = _sphere(5.0, 4)
    facts = surface_facts(sphere)
    assert facts.closed and facts.bodies == 1
    assert facts.volume_um3 == pytest.approx(4 / 3 * np.pi * 125, rel=0.01)
    cut = Mesh(sphere.vertices, sphere.faces[sphere.vertices[sphere.faces].mean(axis=1)[:, 2] < 4.0])
    assert boundary_edges(cut) > 0 and not surface_facts(cut).closed


def test_the_settings_are_described_in_the_panels_words():
    rows = {r.key: r for r in describe_options({"method": "cylinder", "pathway": "voxel2",
                                                "voxel2_faces": 100_000})}
    assert rows["voxel2_faces"].changed and rows["voxel2_faces"].text == "100,000"
    assert rows["voxel2_faces"].default_text == "400,000"
    assert not rows["pathway"].changed and rows["pathway"].text == "Voxel remesh"
    assert "pixel_size_um" not in rows, "a field the technique hides is not printed"
    assert option_text(field_by_key("voxel2_um"), 0.0) == "0 (automatic)"
    assert match_preset({"method": "cylinder", "pathway": "voxel2", "meshlab": "qem"}).id == "voxel2_qem"
    curve = {r.key: r for r in describe_options({"method": "closedcurve", "pathway": "original"})}
    assert curve["pathway"].inert and not curve["method"].changed


def test_report_helpers():
    assert fmt(1234567.0) == "1,234,567" and fmt(0.000123) == "1.23e-04" and fmt(0.5066, 3) == "0.507"
    assert fmt(0.25, signed=True) == "+0.25" and fmt(None) == "—"
    assert ink("#ffd400") != "#ffd400" and ink("#1f78b4") == "#1f78b4"
    assert np.allclose(diverging_rgb(np.array([0.0]), 1.0), 1.0)
    assert np.allclose(error_rgb(np.array([0.0]), 1.0), [[0.85, 0.87, 0.90]])
    page = Page((8.5, 11.0), 1)
    lines = page.wrap("word " * 80, 3.0, 9)
    assert len(lines) > 3 and all(len(line) < 80 for line in lines)
    # Two first layers on screen share yellow: the report gives each its own color.
    same = [ReportRendering("a", "#ffd400", 1, 1, {}), ReportRendering("b", "#ffd400", 2, 1, {})]
    assert len(set(report_colors(same))) == 2
    apart = [ReportRendering("a", "#ffd400", 1, 1, {}), ReportRendering("b", "#56b4e9", 1, 2, {})]
    assert report_colors(apart) == ["#ffd400", "#56b4e9"]


# -- the PDF ---------------------------------------------------------------------------------
class _Spheres:
    """A report source of made-up surfaces held to three discs, as the service's is."""

    def __init__(self, meshes, *, broken=()):
        self.meshes = meshes
        self.broken = set(broken)
        z = np.array([-2.0, 0.0, 2.0])
        self.centers = np.column_stack([np.zeros(3), np.zeros(3), z])
        self.radii = np.array([4.5, 5.0, 4.5])

    def mesh(self, i):
        if i in self.broken:
            raise ValueError("this rendering does not build")
        return self.meshes[i]

    def deviation(self, i):
        m = self.mesh(i)
        found = deviation_map(m, self.centers, self.radii)
        return RenderingDeviation(found, {"surface_um": float(found.vertex_um.mean()), "annotation_um": 0.2,
                                          "mean_um": 0.3, "p95_um": 1.0, "max_um": 2.0,
                                          "excess_volume_um3": 10.0, "sample_spacing_um": 0.05,
                                          "n_discs": 3, "n_curves": 0})

    def difference(self, a, b):
        return mesh_difference(self.mesh(a), self.mesh(b), sign="enclosure", both=True, samples=20_000)

    def annotations(self):
        return self.centers, self.radii, []

    def annotation_names(self):
        return np.array([101, 102, 103]), np.array([10, 11, 12])


def _spec(n=3, pairs=((0, 1), (0, 2), (1, 2)), **more):
    renderings = tuple(ReportRendering(f"Sphere {k}", "#ffd400", k + 1, 1, {"method": "cylinder",
                                                                           "pathway": "voxel2"})
                       for k in range(n))
    return ReportSpec(cell=943, source="i", volume="Inferior Monkey", renderings=renderings,
                      pairs=pairs, panes=(ReportPane(n + 1, kind="difference", difference=(0, 1)),),
                      views=(NAMED_VIEWS["xy1"],), z_scale_nm=70.0, created="2026-10-20 12:00 PDT",
                      **more)


def test_the_pdf_has_the_pages_its_plan_says_with_bookmarks_and_links():
    meshes = [_sphere(5.0, 3), _sphere(5.4, 3), _sphere(4.6, 3, (0.3, 0, 0))]
    spec = _spec()
    seen = []
    with progress.reporting(lambda stage, done, total: seen.append((stage, done, total))):
        report = write_compare_report(spec, _Spheres(meshes))
    plan = Plan(spec)
    reader = PdfReader(io.BytesIO(report.pdf))
    assert report.pdf.startswith(b"%PDF") and len(reader.pages) == report.pages == plan.total
    assert report.notes == () and report.filename == "sbfsem-compare-c943-i-2026-10-20.pdf"
    # Front matter, a section per pane (three of spheres, one Difference), the addendum.
    assert plan.page("pair", (0, 1)) and plan.page("addendum", 8) == plan.total
    titles = [item.title if not isinstance(item, list) else None for item in reader.outline]
    assert "Summary" in titles and "Pane 1" in titles and "Addendum: the mathematics" in titles
    contents = reader.pages[plan.page("contents") - 1]
    links = [a.get_object() for a in contents.get("/Annots") or []]
    assert len(links) >= len(plan.body) and all(a["/Subtype"] == "/Link" for a in links)
    # The progress follows the report's own steps, from the first to the last.
    counted = [(d, t) for _, d, t in seen if t]
    assert counted and counted[-1][0] == counted[-1][1]
    assert all(d <= t for d, t in counted)


def test_a_rendering_that_will_not_build_is_a_page_that_says_so():
    meshes = [_sphere(5.0, 3), None, _sphere(4.6, 3)]
    spec = _spec()
    report = write_compare_report(spec, _Spheres(meshes, broken={1}))
    assert len(PdfReader(io.BytesIO(report.pdf)).pages) == Plan(spec).total
    assert any("R2 could not be built" in note for note in report.notes)


def test_a_report_asked_to_stop_stops_between_its_steps():
    meshes = [_sphere(5.0, 3), _sphere(5.4, 3), _sphere(4.6, 3)]
    asked = {"stop": False}
    source = _Spheres(meshes)
    real = source.difference

    def difference(a, b):
        asked["stop"] = True          # the person pressed Stop during the first pair
        return real(a, b)

    source.difference = difference
    with progress.stoppable(lambda: asked["stop"]), pytest.raises(progress.Stopped):
        write_compare_report(_spec(), source)


def test_a_spec_refuses_what_it_cannot_draw():
    with pytest.raises(ValueError, match="pair"):
        _spec(pairs=((0, 0),))
    with pytest.raises(ValueError, match="renderings"):
        ReportSpec(cell=1, source="i", volume="v", renderings=())
    spec = _spec(pairs=((2, 0), (0, 2), (1, 0)))
    assert spec.pairs == ((0, 1), (0, 2)), "pairs are unordered, each once"


# -- the web job ---------------------------------------------------------------------------
QUICK = {"method": "cylinder", "pathway": "original", "smooth_iterations": 0}
TUBE = {"method": "curvemesh", "pathway": "original", "smooth_iterations": 0}


@pytest.fixture
def web(request):
    app = create_app()
    fixture = Path(request.path).parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as client:
        app.state.service.offline = True
        app.state.service._disk_meshes = None
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield client, app


def _body(**more):
    body = {
        "renderings": [
            {"options": QUICK, "label": "Original (MATLAB)", "color": "#ffd400", "pane": 1, "layer": 1},
            {"options": TUBE, "label": "Tube", "color": "#ffd400", "pane": 2, "layer": 1, "deviation": False},
        ],
        "pairs": [[1, 0]],
        "views": ["view", "xy1"],
        "camera": {"direction": [0.5, -0.5, 0.7], "up": [0, 0, 1], "flip_z": True},
        "panes": [{"number": 1, "layers": 1}, {"number": 2, "layers": 1},
                  {"number": 3, "kind": "difference", "difference": [0, 1]}],
        "created": "2026-10-20 15:00 PDT",
        "addendum": False,
    }
    body.update(more)
    return body


def _poll(client, job_id, seconds=180.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = client.get(f"/api/mesh-jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError("the report did not finish")


def test_the_report_job_makes_the_pdf_and_serves_it(web):
    client, _ = web
    started = client.post("/api/neurons/i/943/compare-report-job", json=_body())
    assert started.status_code == 200, started.text
    head = started.json()
    assert head["kind"] == "report" and head["weight_mb"] > 1500 and head["pages"] > 10
    job = _poll(client, head["job_id"])
    assert job["status"] == "done", job
    meta = job["result"]
    assert meta["pages"] == head["pages"] and meta["filename"].endswith(".pdf") and meta["bytes"] > 10_000
    assert job["mesh"] is None
    pdf = client.get(f"/api/compare-reports/{head['job_id']}")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert f'filename="{meta["filename"]}"' in pdf.headers["content-disposition"]
    assert len(PdfReader(io.BytesIO(pdf.content)).pages) == meta["pages"]
    assert client.get("/api/compare-reports/nope").status_code == 404


def test_a_report_without_a_viewpoint_it_can_draw_is_refused(web):
    client, _ = web
    answer = client.post("/api/neurons/i/943/compare-report-job",
                         json=_body(views=["view"], camera=None))
    assert answer.status_code == 400 and "viewpoint" in answer.json()["detail"]
    answer = client.post("/api/neurons/i/943/compare-report-job",
                         json=_body(camera={"direction": [0, 0, 0], "up": [0, 0, 1]}))
    assert answer.status_code == 400
    assert client.post("/api/neurons/i/943/compare-report-job",
                       json=_body(pairs=[[0, 0]])).status_code == 400
    assert client.post("/api/neurons/i/943/compare-report-job",
                       json=_body(renderings=[])).status_code == 422


def test_a_pane_picture_is_taken_only_when_it_is_a_picture(web):
    import base64

    from sbfsem_web.api.routes import _picture_from_data_url

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    assert _picture_from_data_url("data:image/png;base64," + base64.b64encode(png).decode()) == png
    jpeg = b"\xff\xd8\xff" + b"0" * 20
    assert _picture_from_data_url("data:image/jpeg;base64," + base64.b64encode(jpeg).decode()) == jpeg
    assert _picture_from_data_url("data:image/png;base64," + base64.b64encode(b"GIF89a").decode()) is None
    assert _picture_from_data_url("data:image/png;base64,not base64!") is None
    assert _picture_from_data_url("https://example.com/x.png") is None


def test_stop_ends_a_running_report_and_nothing_else(web, monkeypatch):
    client, app = web
    svc = app.state.service
    release = threading.Event()
    real = svc.compare_report

    def slow(neuron, spec):
        # Running, at a step that can stop, until the test lets go.
        release.wait(10)
        return real(neuron, spec)

    monkeypatch.setattr(svc, "compare_report", slow)
    job_id = client.post("/api/neurons/i/943/compare-report-job", json=_body()).json()["job_id"]
    deadline = time.time() + 10
    while client.get(f"/api/mesh-jobs/{job_id}").json()["status"] != "running" and time.time() < deadline:
        time.sleep(0.02)
    assert client.delete(f"/api/mesh-jobs/{job_id}").json() == {"cancelled": True}
    release.set()
    assert _poll(client, job_id)["status"] == "cancelled"
    # A running build is not a report: a cancel lets it run to its end, as before.
    store = app.state.mesh_jobs
    gate = threading.Event()
    build = store.submit(("hold",), lambda: gate.wait(5) and "built", kind="mesh")
    while store.get(build.id).status != "running":
        time.sleep(0.02)
    assert store.cancel(build.id) is False
    gate.set()
    while store.get(build.id).status == "running":
        time.sleep(0.02)
    assert store.get(build.id).status == "done"
