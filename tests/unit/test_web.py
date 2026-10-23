from __future__ import annotations

import contextlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from sbfsem.io.legacy_json import read_legacy_json
from sbfsem_web.app import create_app


@pytest.fixture(scope="module")
def client(request):
    app = create_app()
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as test_client:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield test_client


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_build_identifies_the_running_process(client):
    """Browsers poll this to notice a redeploy; it must always be complete."""
    body = client.get("/api/build").json()
    assert set(body) == {"version", "assets", "instance", "started_at", "meshlab"}
    assert isinstance(body["meshlab"], bool)     # can this server run the MeshLab option
    assert body["instance"] and len(body["instance"]) == 12
    assert body["assets"].startswith(body["version"])
    assert body["started_at"] > 0
    # Stable within one process, or every poll would announce a fake redeploy.
    assert client.get("/api/build").json()["instance"] == body["instance"]


@pytest.mark.parametrize("setting, expected", [
    (None, 12), ("40", 40), (" 7 ", 7), ("0", 12), ("-3", 12), ("many", 12), ("", 12),
])
def test_the_in_memory_mesh_cache_size_is_a_setting(monkeypatch, setting, expected):
    """The deployment guide tells operators to raise it; there was no way to."""
    if setting is None:
        monkeypatch.delenv("SBFSEM_MESH_CACHE_SIZE", raising=False)
    else:
        monkeypatch.setenv("SBFSEM_MESH_CACHE_SIZE", setting)
    with TestClient(create_app()) as c:
        assert c.app.state.service._mesh_cache_size == expected


def test_build_instance_changes_between_processes():
    """A restart must be visible to clients even when no file changed."""
    ids = set()
    for _ in range(2):
        with TestClient(create_app()) as c:
            ids.add(c.get("/api/build").json()["instance"])
    assert len(ids) == 2


def test_mesh_job_flow(client):
    """A build started as a job delivers the same mesh the direct GET does."""
    import time

    params = "?method=cylinder&pathway=original&smooth_iterations=0"
    start = client.get(f"/api/neurons/i/943/mesh-job{params}").json()
    assert start["status"] in ("queued", "running", "done")

    job = None
    for _ in range(150):  # a tiny cell builds in well under this
        job = client.get(f"/api/mesh-jobs/{start['job_id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert job["status"] == "done", job.get("detail")
    assert job["mesh"]["n_faces"] > 0
    assert job["seconds"] >= 0

    direct = client.get(f"/api/neurons/i/943/mesh{params}").json()
    assert direct["n_faces"] == job["mesh"]["n_faces"]

    # The same request joins the same (finished) job rather than rebuilding.
    again = client.get(f"/api/neurons/i/943/mesh-job{params}").json()
    assert again["job_id"] == start["job_id"]

    assert client.get("/api/mesh-jobs/nope").status_code == 404
    assert client.delete(f"/api/mesh-jobs/{start['job_id']}").json() == {"cancelled": False}


def test_a_job_poll_can_wait_for_the_job_to_end(client):
    """`?wait=` holds the poll until the job ends (or about a second), so the page hears of a
    finished build at once; the wait holds no thread and blocks no other request."""
    import threading

    jobs = client.app.state.mesh_jobs
    release = threading.Event()
    job = jobs.submit(("test-wait", time.time()), lambda: release.wait(10) and {"n_faces": 0},
                      kind="grid")
    answers = {}

    def poll():
        t0 = time.perf_counter()
        answers["body"] = client.get(f"/api/mesh-jobs/{job.id}?wait=1").json()
        answers["seconds"] = time.perf_counter() - t0

    waiting = threading.Thread(target=poll)
    waiting.start()
    time.sleep(0.2)
    # The event loop is free while the poll waits: another request answers at once.
    t0 = time.perf_counter()
    assert client.get("/api/health").status_code == 200
    assert time.perf_counter() - t0 < 0.5
    assert waiting.is_alive()
    release.set()
    waiting.join(5)
    assert answers["body"]["status"] == "done" and answers["body"]["result"] == {"n_faces": 0}
    assert answers["seconds"] < 1.0

    # Still running at the end of the wait: answered after about a second (more is held to 1).
    hold = threading.Event()
    slow = jobs.submit(("test-wait-slow", time.time()), lambda: hold.wait(10), kind="grid")
    t0 = time.perf_counter()
    body = client.get(f"/api/mesh-jobs/{slow.id}?wait=30").json()
    assert body["status"] == "running" and 0.9 < time.perf_counter() - t0 < 2.0
    # Without `wait`, as before: at once.
    t0 = time.perf_counter()
    assert client.get(f"/api/mesh-jobs/{slow.id}").json()["status"] == "running"
    assert time.perf_counter() - t0 < 0.5
    hold.set()
    assert client.get(f"/api/mesh-jobs/{slow.id}?wait=1").json()["status"] == "done"
    assert client.get(f"/api/mesh-jobs/{slow.id}?wait=-1").status_code == 422
    assert client.get("/api/mesh-jobs/nope?wait=1").status_code == 404

    # A render followed the page's way, waiting and asking for the binary, ends with the binary.
    from sbfsem_web.mesh_binary import MEDIA_TYPE

    start = client.get("/api/neurons/i/943/mesh-job?method=cylinder&pathway=original"
                       "&smooth_iterations=0&reduce_fraction=0.9").json()
    accept = {"Accept": f"{MEDIA_TYPE}, application/json"}
    for _ in range(60):
        reply = client.get(f"/api/mesh-jobs/{start['job_id']}?wait=1", headers=accept)
        if reply.headers["content-type"].startswith(MEDIA_TYPE):
            break
        assert reply.json()["status"] in ("queued", "running"), reply.json().get("detail")
    assert reply.headers["content-type"].startswith(MEDIA_TYPE)


def test_grid_job_keeps_the_cells_grid_once(client):
    """Move mode asks for the whole-cell grid: built once in a job of its own, then kept;
    a technique with no whole-cell grid says so."""
    import time

    def finished(path):
        start = client.get(path).json()
        for _ in range(300):
            job = client.get(f"/api/mesh-jobs/{start['job_id']}").json()
            if job["status"] in ("done", "error"):
                return job
            time.sleep(0.2)
        return job

    params = "?method=cylinder&pathway=voxel2&voxel2_um=0.2&smooth_iterations=0"
    job = finished(f"/api/neurons/i/943/grid-job{params}")
    assert job["status"] == "done" and job["kind"] == "grid" and job["mesh"] is None, job.get("detail")
    assert job["result"]["ready"] is True and job["result"]["built"] is True
    # The cell's render on that grid needs no second build of the grid.
    assert client.app.state.service.prepare_grid(
        943, "i", method="cylinder", pathway="voxel2", voxel2_um=0.2, smooth_iterations=0,
    )["built"] is False
    none = finished("/api/neurons/i/943/grid-job?method=cylinder&pathway=original")
    assert none["result"]["ready"] is False and "no whole-cell grid" in none["result"]["reason"]


def test_resolve_structures_endpoint(client):
    """The ExportPortal grammar over HTTP: numeric input needs no OData."""
    body = client.post(
        "/api/volumes/i/resolve-structures",
        json={"text": "5-7; 12, 12\nbad-token 9"},
    ).json()
    # "bad-token 9" holds a letter, so the whole entry is one label term --
    # its 9 belongs to the label, not to the numeric IDs.
    assert body["ids"] == [5, 6, 7, 12]
    assert body["numeric_ids"] == [5, 6, 7, 12]
    assert [entry["term"] for entry in body["labels"]] == ["bad-token 9"]
    assert body["invalid"] == []

    refused = client.post("/api/volumes/i/resolve-structures", json={"text": 7})
    assert refused.status_code == 422


def test_index_and_static(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/static/js/session.js").status_code == 200
    assert client.get("/static/js/ids.js").status_code == 200
    assert client.get("/static/js/colormaps.js").status_code == 200
    assert client.get("/static/js/colormap-tables.js").status_code == 200
    assert client.get("/static/js/render-schema.js").status_code == 200
    assert client.get("/static/js/renderoptions.js").status_code == 200
    assert client.get("/static/js/renderform.js").status_code == 200
    assert client.get("/static/js/renderestimate.js").status_code == 200
    assert client.get("/static/js/compare.js").status_code == 200
    assert client.get("/static/js/comparelayers.js").status_code == 200
    assert client.get("/static/js/compareoptions.js").status_code == 200
    assert client.get("/static/js/connectivity.js").status_code == 200
    assert client.get("/static/js/linkedneurons.js").status_code == 200
    assert client.get("/static/js/selectpicker.js").status_code == 200
    assert client.get("/static/vendor/three/three.module.min.js").status_code == 200
    assert client.get("/static/vendor/three/three.core.min.js").status_code == 200
    assert client.get("/static/vendor/cytoscape.esm.min.mjs").status_code == 200


def test_in_page_dropdowns_are_wired_in(client):
    """The in-page dropdown list (selectpicker.js) is installed, switchable and styled.

    Its behavior is checked in a browser by tools/check_dropdowns.py and, for the
    pure half, by tests/js/test_selectpicker.mjs; this pins the wiring that the
    browser check cannot see from a server: the import and the call in app.js,
    the Display checkbox with its help text (a title would open a native tooltip
    window, the thing the list exists to avoid), and the stylesheet block.
    """
    app = client.get("/static/js/app.js").text
    assert "import { installSelectPicker } from './selectpicker.js';" in app
    assert app.count("installSelectPicker()") == 1
    # After the color picker, so both capture listeners are in before any control is used.
    assert app.index("installColorPicker({") < app.index("installSelectPicker()")

    page = client.get("/").text
    assert page.count('id="opt-page-selects"') == 1
    label = page[page.rindex("<label", 0, page.index('id="opt-page-selects"')):
                 page.index("</label>", page.index('id="opt-page-selects"'))]
    assert "data-help=" in label and "title=" not in label
    assert "In-page dropdowns" in label
    assert "Display" in page[page.rindex('<div class="panel-group">', 0, page.index("opt-page-selects")):]

    css = client.get("/static/css/app.css").text
    for selector in (".sel-pop", ".sel-opt.active", ".sel-live", 'select[aria-expanded="true"]'):
        assert selector in css, selector
    # Under the help panel (60), so a row's description shows, over the menus it opens from.
    assert "z-index: 59" in css[css.index(".sel-pop {"):css.index(".sel-opt, .sel-head")]

    module = client.get("/static/js/selectpicker.js").text
    assert "sbfsem:select-lists" in module
    assert "export function installSelectPicker" in module
    # The popover API's top layer would paint over the help panel.
    assert "showPopover" not in module


def test_index_must_be_revalidated(client):
    """A heuristically cached index.html carries yesterday's version stamp,
    which faithfully loads yesterday's app -- the one stale link that defeats
    all the stamping downstream of it."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_static_modules_must_be_revalidated(client):
    """A deploy must not leave a browser running new app.js on old modules.

    Only the URLs written into index.html carry the asset version; the ES
    modules app.js imports do not, so without revalidation a returning
    browser silently runs a half-updated app.
    """
    for path in ("/static/js/app.js", "/static/js/render3d.js", "/static/css/app.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers.get("etag")

    # Revalidation is cheap: an unchanged asset is a 304 with no body.
    etag = client.get("/static/js/render3d.js").headers["etag"]
    again = client.get("/static/js/render3d.js", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert not again.content


def test_volumes(client):
    volumes = client.get("/api/volumes").json()
    names = {v["name"] for v in volumes}
    assert "NeitzInferiorMonkey" in names
    # Which volumes the viewer offers "Viking's current section transforms" for.
    has = {v["name"]: v["has_transforms"] for v in volumes}
    assert has["NeitzInferiorMonkey"] is True and has["RPC1"] is True
    assert has["RC1"] is False and has["RC2"] is False
    assert client.get("/api/volumes/i").json()["volume_scale"] == [7.5, 7.5, 90.0]
    assert client.get("/api/volumes/not-a-volume").status_code == 404


def test_neuron_endpoints(client):
    detail = client.get("/api/neurons/i/943").json()
    assert detail["summary"]["label"] == "ON mBC"
    assert client.get("/api/neurons/i/999").status_code == 404

    nodes = client.get("/api/neurons/i/943/nodes").json()
    assert len(nodes) == 190
    assert len(client.get("/api/neurons/i/943/nodes?cell_only=false").json()) == 199
    assert len(client.get("/api/neurons/i/943/edges").json()) == 194


def test_mesh_endpoints(client):
    mesh = client.get("/api/neurons/i/943/mesh").json()
    assert mesh["n_faces"] > 0
    assert len(mesh["faces"]) == mesh["n_faces"] * 3
    glb = client.get("/api/neurons/i/943/mesh.glb")
    assert glb.headers["content-type"] == "model/gltf-binary"
    assert glb.content[:4] == b"glTF"
    syn = client.get("/api/neurons/i/943/synapses/RibbonPost/mesh").json()
    assert syn["n_faces"] > 0


def test_boundary_mesh_endpoint(client):
    """The context sheets RenderApp drew under a neuron."""
    body = client.get("/api/volumes/i/boundary/GCL/mesh").json()
    assert body["n_faces"] > 0
    assert len(body["vertices"]) == body["n_vertices"] * 3
    # Decimation is a real reduction, not a no-op.
    coarse = client.get("/api/volumes/i/boundary/GCL/mesh?decimate=8").json()
    assert 0 < coarse["n_faces"] < body["n_faces"]
    assert client.get("/api/volumes/i/boundary/NOPE/mesh").status_code == 400
    # A volume with no boundary markers says so rather than 500ing.
    assert client.get("/api/volumes/NeitzCped/boundary/GCL/mesh").status_code == 400


def test_vessel_mesh_endpoint_offline(client):
    """Offline the vessel query yields nothing; the route must still answer."""
    response = client.get("/api/volumes/i/vessels/mesh")
    assert response.status_code in (200, 502)
    if response.status_code == 200:
        assert response.json()["n_faces"] >= 0


def test_mesh_diff_endpoint(client):
    body = {
        "a": {"smooth_iterations": 0},
        "b": {"smooth_iterations": 1},
        "samples": 10_000,
    }
    data = client.post("/api/neurons/i/943/mesh-diff", json=body).json()
    assert data["mesh"]["n_vertices"] > 0
    assert len(data["mesh"]["vertex_data"]) == data["mesh"]["n_vertices"]
    assert data["mean_abs_um"] > 0
    assert data["sample_spacing_um"] > 0
    assert data["scale_um"] > 0

    assert client.post("/api/neurons/i/999/mesh-diff", json=body).status_code == 404
    bad = {"a": {"pathway": "bogus"}}
    assert client.post("/api/neurons/i/943/mesh-diff", json=bad).status_code == 422


def test_meshlab_is_a_render_option_and_a_capability(client):
    """`/api/build` says whether this server can honor it; the mesh routes
    validate it; without pymeshlab the refusal is a 400 that says why."""
    from sbfsem.geometry import meshlab

    assert client.get("/api/build").json()["meshlab"] is meshlab.available()
    assert client.get("/api/neurons/i/943/mesh?meshlab=laplacian").status_code == 422
    assert client.get("/api/neurons/i/943/mesh?meshlab=isotropic&meshlab_edge_um=9").status_code == 422
    response = client.get("/api/neurons/i/943/mesh?smooth_iterations=0&meshlab=qem&meshlab_fraction=0.5")
    if meshlab.available():
        plain = client.get("/api/neurons/i/943/mesh?smooth_iterations=0").json()
        assert response.status_code == 200
        assert response.json()["n_faces"] == pytest.approx(plain["n_faces"] * 0.5, rel=0.05)
    else:
        assert response.status_code == 400
        assert "sbfsem[meshlab]" in response.json()["detail"]


def test_mesh_deviation_endpoint(client):
    """One rendering against the cell's annotations, per vertex and per rim point."""
    data = client.post("/api/neurons/i/943/mesh-deviation", json={"smooth_iterations": 0}).json()
    mesh = data["mesh"]
    assert mesh["n_vertices"] > 0 and len(mesh["vertex_data"]) == mesh["n_vertices"]
    assert min(mesh["vertex_data"]) >= 0                      # absolute distances
    assert len(data["rim_xyz"]) == 3 * len(data["rim_um"]) > 0
    assert data["n_rims"] >= len(data["rim_um"]) and data["rim_stride"] >= 1
    assert data["scale_um"] > 0
    assert data["deviation"]["n_discs"] > 0
    # The rim layer's mean is the metric's annotation_um when nothing was thinned.
    if data["rim_stride"] == 1:
        assert sum(data["rim_um"]) / len(data["rim_um"]) == pytest.approx(
            data["deviation"]["annotation_um"], abs=1e-3)

    assert client.post("/api/neurons/i/999/mesh-deviation", json={}).status_code == 404
    assert client.post("/api/neurons/i/943/mesh-deviation",
                       json={"pathway": "bogus"}).status_code == 422


def test_export_formats(client):
    for fmt, marker in [
        ("swc", b"# ORIGINAL_SOURCE"),
        ("json", b'"schema"'),
        ("dae", b"COLLADA"),
        ("obj", b"v "),
    ]:
        response = client.get(f"/api/neurons/i/943/export/{fmt}")
        assert response.status_code == 200, fmt
        assert marker in response.content, fmt
    assert client.get("/api/neurons/i/943/export/stl").content[:5] not in (b"", None)
    assert client.get("/api/neurons/i/943/export/xyz").status_code == 400


def test_an_exported_file_is_named_for_its_volume_as_well_as_its_cell(client):
    """c943 is another cell in every volume (ground rule 11); the linked-neurons files and
    the bulk archive already said which, and a single cell's file did not."""
    for fmt in ("swc", "json", "obj"):
        response = client.get(f"/api/neurons/i/943/export/{fmt}")
        assert response.headers["content-disposition"] == (
            f'attachment; filename="c943-NeitzInferiorMonkey.{fmt}"'), fmt
    wire = client.get("/api/neurons/i/943/export/obj", params={"wireframe": "true"})
    assert wire.headers["content-disposition"] == (
        'attachment; filename="c943-NeitzInferiorMonkey-wireframe.obj"')


def test_exports_take_the_closed_curve_pixel_size_the_screen_shows(client, monkeypatch):
    """A Closed curve or Hybrid cell shown at 0.25 um exported at the 0.05 um
    default: the route never took ``pixel_size_um``, so the file was another
    (twenty-fold larger) mesh than the one on screen.  Every mesh export and
    ``mesh.glb`` hand it to the mesh build now, bounded like the mesh route."""
    service = client.app.state.service
    seen = []
    real = service.mesh_object

    def spy(neuron_id, source, **kwargs):
        seen.append(kwargs)
        return real(neuron_id, source, **kwargs)

    monkeypatch.setattr(service, "mesh_object", spy)
    for path in ("export/obj", "export/stl", "export/dae", "export/ply", "mesh.glb"):
        base = f"/api/neurons/i/943/{path}?method=curvemesh"
        seen.clear()
        assert client.get(f"{base}&pixel_size_um=0.25").status_code == 200, path
        assert [kw["pixel_size_um"] for kw in seen] == [0.25], path
        seen.clear()
        assert client.get(base).status_code == 200, path
        assert [kw["pixel_size_um"] for kw in seen] == [0.05], path   # the default, as before
        for bad in ("0", "-1", "1.5"):
            assert client.get(f"{base}&pixel_size_um={bad}").status_code == 422, (path, bad)


def test_the_routes_take_the_closed_curve_averaging_and_bound_it(client, monkeypatch):
    """``curve_avg_discs`` (a multiple of each disc's radius) and ``curve_avg_paths`` (microns)
    reach the build from the mesh route, the mesh job, the exports and the glTF, are off
    when left out, and are refused outside what the controls offer (0 to 3 and 0 to 2)."""
    service = client.app.state.service
    seen = {"render": [], "object": []}
    real_render, real_object = service.render_mesh, service.mesh_object

    def spy_render(neuron_id, source, **kwargs):
        seen["render"].append(kwargs)
        return real_render(neuron_id, source, **kwargs)

    def spy_object(neuron_id, source, **kwargs):
        seen["object"].append(kwargs)
        return real_object(neuron_id, source, **kwargs)

    monkeypatch.setattr(service, "render_mesh", spy_render)
    monkeypatch.setattr(service, "mesh_object", spy_object)
    base = "/api/neurons/i/943/mesh?method=closedcurve&pixel_size_um=0.25"
    plain = client.get(base)
    averaged = client.get(f"{base}&curve_avg_discs=1&curve_avg_paths=0.2")
    assert plain.status_code == averaged.status_code == 200
    assert (seen["render"][0]["curve_avg_discs"], seen["render"][0]["curve_avg_paths"]) == (0.0, 0.0)
    assert (seen["render"][1]["curve_avg_discs"], seen["render"][1]["curve_avg_paths"]) == (1.0, 0.2)
    assert plain.json()["vertices"] != averaged.json()["vertices"], "the surface is averaged"
    for path in ("export/obj", "export/stl", "mesh.glb"):
        seen["object"].clear()
        url = f"/api/neurons/i/943/{path}?method=closedcurve&pixel_size_um=0.25"
        assert client.get(f"{url}&curve_avg_discs=0.5&curve_avg_paths=0.1").status_code == 200, path
        assert (seen["object"][0]["curve_avg_discs"], seen["object"][0]["curve_avg_paths"]) == (0.5, 0.1), path
        seen["object"].clear()
        assert client.get(url).status_code == 200
        assert (seen["object"][0]["curve_avg_discs"], seen["object"][0]["curve_avg_paths"]) == (0.0, 0.0), path
    started = client.get(f"{base}&curve_avg_discs=1".replace("/mesh?", "/mesh-job?"))
    assert started.status_code == 200 and started.json()["job_id"]
    for _ in range(600):                                    # a job is not left running behind the test
        status = client.get(f"/api/mesh-jobs/{started.json()['job_id']}").json()["status"]
        if status in ("done", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert status == "done"
    for query in ("curve_avg_discs=-0.1", "curve_avg_discs=3.1", "curve_avg_paths=-1", "curve_avg_paths=2.1"):
        assert client.get(f"{base}&{query}").status_code == 422, query
        assert client.get(f"/api/neurons/i/943/export/obj?{query}").status_code == 422, query


def test_exports_cut_a_clipped_render_as_the_mesh_route_does(client):
    """A cell clipped on screen (Clip IPL, to isolate its ON or OFF arbors) was
    exported whole: the export routes never took the clip.  They take the mesh
    route's ``clip_z``, ``clip_ipl`` and ``keep_above`` now, and the file holds
    the surface the viewer shows."""
    base = "/api/neurons/i/943"
    z_lo, z_hi = client.get(f"{base}/mesh?smooth_iterations=0").json()["bounding_box_um"][4:6]
    cut = (z_lo + z_hi) / 2

    def vertices(path, query):
        body = client.get(f"{base}/{path}?smooth_iterations=0{query}")
        assert body.status_code == 200, (path, query, body.text[:200])
        return sum(1 for line in body.content.splitlines() if line.startswith(b"v "))

    whole = vertices("export/obj", "")
    whole_ply = len(client.get(f"{base}/export/ply?smooth_iterations=0").content)
    for keep in ("true", "false"):
        query = f"&clip_z={cut}&keep_above={keep}"
        shown = client.get(f"{base}/mesh?smooth_iterations=0{query}").json()["n_vertices"]
        assert 0 < shown < whole, keep
        assert vertices("export/obj", query) == shown, keep           # the file is the surface on screen
        # whatever the format: the file of the cut surface is smaller than the whole one's
        assert len(client.get(f"{base}/export/ply?smooth_iterations=0{query}").content) < whole_ply, keep
    # the cached mesh behind it is not cut by an export: the next one is whole again
    assert vertices("export/obj", "") == whole
    # the other mesh routes too
    cut_glb = client.get(f"{base}/mesh.glb?smooth_iterations=0&clip_z={cut}").content
    assert 0 < len(cut_glb) < len(client.get(f"{base}/mesh.glb?smooth_iterations=0").content)
    # the wireframe export is cut too (it is a second branch of the route, with its own call
    # of the mesh builder): the cut wireframe has fewer vertices and edges than the whole
    def wire_counts(query):
        body = client.get(f"{base}/export/obj?smooth_iterations=0&wireframe=true{query}")
        assert body.status_code == 200, (query, body.text[:200])
        lines = body.content.splitlines()
        return (sum(1 for ln in lines if ln.startswith(b"v ")),
                sum(1 for ln in lines if ln.startswith(b"l ")))

    whole_wire = wire_counts("")
    for keep in ("true", "false"):
        cut_wire = wire_counts(f"&clip_z={cut}&keep_above={keep}")
        assert 0 < cut_wire[0] < whole_wire[0], (keep, cut_wire, whole_wire)
        assert 0 < cut_wire[1] < whole_wire[1], (keep, cut_wire, whole_wire)
    # a cut that leaves nothing is the empty-mesh 400, as for any export
    assert client.get(f"{base}/export/obj?clip_ipl=0.5").status_code == 400
    # ... and which side of the stratification cut is kept decides which: i943 lies on the
    # far side of 0.5, so the other side keeps all of it.  Without this a clip_ipl that
    # always kept the upper side (or an export that dropped keep_above) passed.
    assert vertices("export/obj", "&clip_ipl=0.5&keep_above=false") == whole
    assert client.get(f"{base}/export/obj?smooth_iterations=0&clip_ipl=0.5&keep_above=true"
                      ).status_code == 400
    assert client.get(f"{base}/export/obj?smooth_iterations=0&wireframe=true&clip_ipl=0.5"
                      ).status_code == 400, "the wireframe export is cut by the IPL clip too"
    assert wire_counts("&clip_ipl=0.5&keep_above=false") == whole_wire
    # and the SWC and JSON exports, which are not meshes, still ignore it
    assert client.get(f"{base}/export/swc?clip_z={cut}").status_code == 200


def test_the_export_routes_declare_the_views_clip_as_the_mesh_route_does(client):
    """The clip is a fifth declaration, beside the schema's four (``_export_clip``):
    the export routes take it, how its IPL depth is measured and the flattened
    IPL, with the type, default and bounds the mesh route gives them, and nothing
    else of the schema's ``view`` fields (depth coloring is a look, not
    geometry).  A clip bound or name changed on one side and not the other fails
    here, naming the parameter."""
    from sbfsem.service import render_schema as schema

    paths = client.get("/openapi.json").json()["paths"]

    def declared(path):
        return {p["name"]: p["schema"] for p in paths[path]["get"]["parameters"]}

    mesh = declared("/api/neurons/{source}/{neuron_id}/mesh")
    clip_keys = {"clip_ipl", "keep_above", "clip_z", "ipl_method", "ipl_flatten"}
    view_keys = {f.key for f in schema.FIELDS if f.scope == "view"}
    assert clip_keys <= view_keys, "the clip is still a view field of the schema"
    assert clip_keys <= set(mesh), "and the mesh route still takes all three"
    for path in ("/api/neurons/{source}/{neuron_id}/export/{fmt}",
                 "/api/neurons/{source}/{neuron_id}/mesh.glb"):
        taken = declared(path)
        assert set(taken) & view_keys == clip_keys, (
            path, "takes", sorted(set(taken) & view_keys), "of the view fields")
        for key in sorted(clip_keys):
            # same type and default as the mesh route; description and title are prose
            plain = {k: v for k, v in taken[key].items() if k not in ("title", "description")}
            mine = {k: v for k, v in mesh[key].items() if k not in ("title", "description")}
            assert plain == mine, (path, key, plain, mine)
    # the schema's own defaults are the routes' defaults
    for key in clip_keys:
        field = next(f for f in schema.FIELDS if f.key == key)
        assert mesh[key].get("default") == field.default, key


def test_closed_curve_export_is_the_mesh_of_the_pixel_size_asked_for(request):
    """The size of the failure, end to end: c90000's closed-curve volume is
    about 3.9k faces at 0.25 um and about 78k at the 0.05 um default."""
    fixture = request.path.parents[1] / "fixtures" / "i90000_closedcurve.json"
    app = create_app()
    with TestClient(app) as local:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(fixture))

        def faces(query):
            body = local.get(f"/api/neurons/i/90000/export/obj?method=closedcurve{query}").content
            return sum(1 for line in body.splitlines() if line.startswith(b"f "))

        coarse, fine = faces("&pixel_size_um=0.25"), faces("")
    assert fine > 20_000
    assert coarse < fine / 10


def test_a_closed_curve_build_over_the_voxel_limit_is_a_400_on_every_export_route(request):
    """The mesh route said why (the finest pixel that fits); the exports answered a bare 500."""
    fixture = request.path.parents[1] / "fixtures" / "i90000_closedcurve.json"
    app = create_app()
    with TestClient(app) as local:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(fixture))
        base = "/api/neurons/i/90000"
        for path in ("mesh", "export/obj", "export/stl", "export/glb", "export/dae", "mesh.glb"):
            got = local.get(f"{base}/{path}?method=closedcurve&pixel_size_um=0.0005")
            assert got.status_code == 400, (path, got.status_code)
            assert "voxel" in got.json()["detail"].lower() or "pixel" in got.json()["detail"].lower(), path


def test_smooth_iterations_has_one_range_on_every_route(client):
    """Zero to fifty on the mesh, mesh-job, export, diff and render-prefs routes
    alike; the mesh route had no bound at all, and the diff body stopped at 10."""
    from pydantic import ValidationError

    from sbfsem_web.api.routes import MeshOptionsBody

    for bad in ("-1", "51"):
        assert client.get(f"/api/neurons/i/943/mesh?smooth_iterations={bad}").status_code == 422, bad
        assert client.get(
            f"/api/neurons/i/943/mesh-job?smooth_iterations={bad}").status_code == 422, bad
        assert client.get(
            f"/api/neurons/i/943/export/obj?smooth_iterations={bad}").status_code == 422, bad
    for good in ("0", "50"):
        assert client.get(f"/api/neurons/i/943/mesh?smooth_iterations={good}").status_code == 200, good
    assert client.get("/api/neurons/i/943/mesh-job?smooth_iterations=50").status_code == 200

    assert MeshOptionsBody(smooth_iterations=50).smooth_iterations == 50
    assert MeshOptionsBody(smooth_iterations=0).smooth_iterations == 0
    for bad_value in (51, -1):
        with pytest.raises(ValidationError):
            MeshOptionsBody(smooth_iterations=bad_value)
    assert client.post("/api/neurons/i/943/mesh-deviation-job",
                       json={"smooth_iterations": 51}).status_code == 422
    assert client.post("/api/neurons/i/943/mesh-diff-job",
                       json={"a": {"smooth_iterations": 51}}).status_code == 422


def test_analysis_endpoints(client):
    assert client.get("/api/neurons/i/943/hull").json()["area_um2"] > 0
    assert client.get("/api/neurons/i/943/ie-ratio").json()["n_excitatory"] == 2
    assert client.get("/api/neurons/i/943/surface-area").json()["values"][0] > 0
    assert client.get("/api/neurons/i/943/dendrite-diameter").json()["rows"]
    assert client.get("/api/neurons/i/943/open-ended").json()["extra"]["location_ids"]
    strat = client.get("/api/neurons/i/943/stratification?omit_outliers=false")
    assert strat.status_code == 200
    assert strat.json()["stats"]["n"] == 190


def test_import_roundtrip(client, request):
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    with fixture.open("rb") as fh:
        response = client.post("/api/neurons/import", files={"file": ("i943.json", fh)})
    assert response.status_code == 200
    assert response.json()["summary"]["id"] == 943


def test_import_rejects_junk(client):
    response = client.post(
        "/api/neurons/import", files={"file": ("bad.json", b"{not json")}
    )
    assert response.status_code == 400


def test_offline_load_reports_cleanly(client):
    response = client.post("/api/neurons", json={"id": 1234, "source": "i"})
    assert response.status_code == 502
    assert "Viking" in response.json()["detail"]


def test_json_responses_never_contain_nan(client):
    body = client.get("/api/neurons/i/943/stratification").text
    assert "NaN" not in body
    json.loads(body)


def test_the_json_backstop_walks_a_result_only_when_it_holds_a_non_finite_number(monkeypatch):
    """The walk that replaces NaN was half the cost of every response and found
    nothing in nearly all of them: the encoder refuses a NaN by itself."""
    import sbfsem_web.json_response as jr

    walks = []
    real = jr.sanitize
    monkeypatch.setattr(jr, "sanitize", lambda value: walks.append(value) or real(value))
    clean = {"a": [1.5, 2, (3.25, None)], "b": {"c": "é", "d": True}}
    assert jr.SafeJSONResponse(clean).body == json.dumps(
        clean, ensure_ascii=False, separators=(",", ":")).encode()
    assert walks == []
    odd = {"a": [1.5, float("nan")], "b": {"c": float("inf"), "d": -float("inf")}}
    assert json.loads(jr.SafeJSONResponse(odd).body) == {"a": [1.5, None], "b": {"c": None, "d": None}}
    assert walks[0] is odd and not any(w is clean for w in walks)


def test_every_analysis_endpoint_returns_strict_json(client):
    """NaN and Infinity are not valid JSON; JSON.parse rejects both."""
    import json as _json

    paths = [
        "/api/health",
        "/api/volumes",
        "/api/volumes/i",
        "/api/neurons",
        "/api/neurons/i/943",
        "/api/neurons/i/943/nodes",
        "/api/neurons/i/943/edges",
        "/api/neurons/i/943/skeleton",
        "/api/neurons/i/943/mesh",
        "/api/neurons/i/943/synapses/RibbonPost/mesh",
        "/api/neurons/i/943/hull",
        "/api/neurons/i/943/ie-ratio",
        "/api/neurons/i/943/morphometrics",
        "/api/neurons/i/943/surface-area",
        "/api/neurons/i/943/dendrite-diameter",
        "/api/neurons/i/943/open-ended",
        "/api/neurons/i/943/closed-curves",
        "/api/neurons/i/943/stratification",
        "/api/neurons/i/943/stratification?omit_outliers=false",
        "/api/neurons/i/943/synapse-soma-distance/RibbonPost",
        "/api/neurons/i/943/synapse-soma-distance/GapJunction",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        assert "NaN" not in response.text, path
        assert "Infinity" not in response.text, path
        _json.loads(response.text)


def test_skeleton_check_endpoint(client):
    body = client.get("/api/neurons/i/943/skeleton/check").json()
    assert body["neuron_id"] == 943 and body["source"] == "NeitzInferiorMonkey"
    assert set(body["counts"]) == {"loop", "fragment", "jump", "hairpin", "spike", "spoke", "stub", "lamella"}
    assert sum(body["counts"].values()) == len(body["problems"])
    summary = body["summary"]
    assert summary["n_annotations"] > 0 and summary["section_thickness_um"] == pytest.approx(0.09)
    assert summary["rim_corrected_length_um"] <= summary["total_length_um"]
    for problem in body["problems"]:
        assert problem["anchor_id"] in problem["location_ids"] and problem["fix"] in ("viking", "export")
    assert client.get("/api/neurons/i/999999/skeleton/check").status_code == 404


class _FakeProvenance:
    """A volume that answers the provenance queries and the rewrite counts, and
    nothing else.  It notes whether each read came past the disk cache."""

    CREATED = "2019-04-01T12:00:00+00:00"
    LATER = "2019-04-08T12:00:00+00:00"
    PROVENANCE_SELECT = "ID,ParentID,Username,Created,LastModified,TypeCode,Z"

    def __init__(self):
        self._fresh = False
        self.reads = []          # True for a read made inside fresh()
        self.windows = []

    @contextlib.contextmanager
    def fresh(self):
        self._fresh = True
        try:
            yield
        finally:
            self._fresh = False

    def count_rewrites(self, start, end, created_before):
        self.windows.append((start, end, created_before))
        return 0

    def fetch_child_provenance(self, structure_id):
        self.reads.append(self._fresh)
        return [{"ID": 77, "TypeID": 35, "Username": "jk  ", "Label": "",
                 "Created": self.CREATED, "LastModified": self.CREATED}]

    def fetch_location_provenance(self, structure_id):
        self.reads.append(self._fresh)
        return [
            {"ID": 1, "ParentID": structure_id, "Username": "sarastokes ",
             "Created": self.CREATED, "LastModified": self.CREATED},
            {"ID": 2, "ParentID": structure_id, "Username": "jk ",
             "Created": self.CREATED, "LastModified": self.LATER},
        ]

    def fetch_location_provenance_by_parents(self, ids, batch_size=None):
        self.reads.append(self._fresh)
        return [{"ID": 3, "ParentID": 77, "Username": "jk ",
                 "Created": self.CREATED, "LastModified": self.CREATED}]

    def get_structure(self, structure_id):
        self.reads.append(self._fresh)
        return {"ID": structure_id, "Username": "sarastokes  "}


def test_a_cell_loaded_again_gets_a_new_mesh_job(client):
    """A finished job is kept ten minutes and shared by requests for the same
    render.  A cell loaded again (another registration setting, an update from
    Viking) must not be handed the surface built before: on 2026-09-23
    c21768's surface stayed put while its annotations moved up to 11 um."""
    import time
    from pathlib import Path

    params = "method=cylinder&pathway=original&smooth_iterations=0"
    first = client.get(f"/api/neurons/i/943/mesh-job?{params}").json()
    for _ in range(200):
        if client.get(f"/api/mesh-jobs/{first['job_id']}").json()["status"] == "done":
            break
        time.sleep(0.05)
    again = client.get(f"/api/neurons/i/943/mesh-job?{params}").json()
    assert again["job_id"] == first["job_id"]      # the same copy shares its finished job
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "i943.json"
    client.app.state.service.add_neuron(read_legacy_json(fixture))   # loaded again
    fresh = client.get(f"/api/neurons/i/943/mesh-job?{params}").json()
    assert fresh["job_id"] != first["job_id"]


def test_annotators_endpoint(client):
    """Shares of the cell's annotations, cell and children together, live."""
    svc = client.app.state.service
    saved = svc._clients.get("NeitzInferiorMonkey")
    fake = svc._clients["NeitzInferiorMonkey"] = _FakeProvenance()
    try:
        body = client.get("/api/neurons/i/943/annotators").json()
        # Live: every read went past the disk cache, which never expires.
        assert fake.reads and all(fake.reads)
        # The one later write is a session, and the rest of the volume was asked
        # about it; a quiet volume leaves it a revision.
        assert len(fake.windows) == 1
        assert body["neuron_id"] == 943 and body["source"] == "NeitzInferiorMonkey"
        assert body["annotations"] == 3 and body["on_cell"] == 2 and body["on_children"] == 1
        assert body["n_children"] == 1 and body["owner"] == "sarastokes"
        assert body["tolerance_s"] == 3600.0
        by = {a["username"]: a for a in body["annotators"]}
        assert by["jk"]["annotations"] == 2 and by["jk"]["structures"] == 1
        assert by["jk"]["revisions"] == 1 and body["revisions"] == 1
        assert by["sarastokes"]["as_placed"] == 1
        assert sum(a["fraction"] for a in body["annotators"]) == pytest.approx(1.0)
        # The tolerance is the caller's: a week is long enough to forgive that edit.
        loose = client.get("/api/neurons/i/943/annotators?tolerance_s=1209600").json()
        assert loose["revisions"] == 0 and loose["tolerance_s"] == 1209600.0
    finally:
        if saved is not None:
            svc._clients["NeitzInferiorMonkey"] = saved
        else:
            svc._clients.pop("NeitzInferiorMonkey", None)
    assert client.get("/api/neurons/i/999999/annotators").status_code == 404


def test_annotators_reports_an_unreachable_viking_rather_than_guessing(client):
    """The suite runs offline; a volume that cannot be asked is a 502, not an
    empty table that would read as "nobody annotated this"."""
    assert client.get("/api/neurons/i/943/annotators").status_code == 502


def test_bulk_swc_export(client):
    import io
    import zipfile

    response = client.get("/api/neurons/i/export-bulk/swc?ids=943&pattern=cell_{id}")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["cell_943.swc", "manifest.csv"]
        swc = archive.read("cell_943.swc").decode()
        manifest = archive.read("manifest.csv").decode().splitlines()
    assert "# SECTION_THICKNESS_UM 0.0900" in swc and "# DROPPED_LINKS 0 " in swc
    assert "# OPTIONS branch_types=false" in swc
    assert manifest[0].startswith("structure_id,volume,label,file") and "cell_943.swc" in manifest[1]
    relaxed = client.get("/api/neurons/i/export-bulk/swc?ids=943&swc_relax=0.5&bracket=true")
    with zipfile.ZipFile(io.BytesIO(relaxed.content)) as archive:
        assert "# POSITIONS relaxed f=0.50 bound=outline:" in archive.read("c943.swc").decode()
        header, row = archive.read("manifest.csv").decode().splitlines()[:2]
    cells = dict(zip(header.split(","), row.split(","), strict=True))
    assert float(cells["length_floor_um"]) < float(cells["total_length_um"])
    assert cells["options"] == ("branch_types=false lamellae=false radius=stored "
                                "sampling=annotations relax=0.50 relax_bound=outline")
    assert int(cells["swc_rows"]) > 0 and float(cells["swc_length_um"]) > 0
    # The export options, and one position setting for every cell of an archive.
    options = client.get("/api/neurons/i/export-bulk/swc?ids=943&swc_sampling=sphere"
                         "&swc_radius=modal&swc_lamellae=true&live_registration=false"
                         "&section_corrections=off")
    assert options.status_code == 200
    with zipfile.ZipFile(io.BytesIO(options.content)) as archive:
        text = archive.read("c943.swc").decode()
        header, row = archive.read("manifest.csv").decode().splitlines()[:2]
    cells = dict(zip(header.split(","), row.split(","), strict=True))
    assert cells["options"] == "branch_types=false lamellae=true radius=modal sampling=sphere"
    assert "# SAMPLING sphere: " in text and "# R_COLUMN modal: " in text
    assert "lamellae=true" in text and "sampling=sphere" in text
    assert client.get("/api/neurons/i/export-bulk/swc?ids=943&section_corrections=sideways"
                      ).status_code == 400
    single = client.get("/api/neurons/i/943/export/swc?swc_sampling=sphere&swc_radius=modal")
    assert single.status_code == 200 and "# SAMPLING sphere: " in single.text
    assert "# R_COLUMN modal: " in single.text
    assert client.get("/api/neurons/i/943/export/swc?swc_relax=0.5&swc_lamellae=true"
                      ).status_code == 400
    assert client.get("/api/neurons/i/943/export/swc?swc_sampling=thin").status_code == 422
    assert client.get("/api/neurons/i/943/export/swc?swc_radius=mean").status_code == 422
    single = client.get("/api/neurons/i/943/export/swc?swc_relax=1&swc_relax_bound=radius")
    assert "# POSITIONS relaxed f=1.00 bound=radius:" in single.text
    assert client.get("/api/neurons/i/943/export/swc?swc_relax=0.5&swc_relax_bound=hull").status_code == 422
    assert client.get("/api/neurons/i/943/export/swc?swc_relax=2").status_code == 422
    for bad in ("ids=abc", "ids=", "ids=943&pattern=nothing", "ids=943&pattern=../c{id}",
                "ids=" + ",".join(str(i) for i in range(201))):
        assert client.get(f"/api/neurons/i/export-bulk/swc?{bad}").status_code in (400, 422), bad


def test_morphometrics_endpoint(client):
    body = client.get("/api/neurons/i/943/morphometrics").json()
    assert body["neuron_id"] == 943 and body["source"] == "NeitzInferiorMonkey"
    assert body["total_length_um"] > body["dendritic_length_um"] > 0
    assert body["n_tips"] > 0 and body["n_branch_points"] > 0
    # The floor under the cable length costs seconds, so it is on request.
    assert body["length_floor_um"] is None
    bracketed = client.get("/api/neurons/i/943/morphometrics?bracket=true").json()
    assert 0 < bracketed["length_floor_um"] < bracketed["total_length_um"]
    assert bracketed["length_floor_by_radius_um"] > 0
    # The rim-corrected length is reported beside the uncorrected one.
    assert body["rim_corrected_length_um"] == pytest.approx(
        body["total_length_um"] - body["spoke_length_um"])
    assert len(body["extent_um"]) == 3
    assert client.get("/api/neurons/i/943/morphometrics?soma_fraction=0").status_code == 422


def test_swc_export_branch_typing_is_a_switch(client):
    default = client.get("/api/neurons/i/943/export/swc").text
    plain = client.get("/api/neurons/i/943/export/swc?swc_branch_types=false").text
    types = lambda text: {int(line.split()[1]) for line in text.splitlines()  # noqa: E731
                          if line and not line.startswith("#")}
    assert {5, 6} <= types(default)
    assert types(plain) == {1, 3}
    assert "# TYPE_CODES" in plain


def test_bad_parameters_are_rejected_not_crashed(client):
    assert client.get("/api/neurons/i/943/mesh?reduce_fraction=5").status_code == 422
    assert client.get("/api/neurons/i/943/stratification?num_bins=0").status_code == 422
    assert client.get("/api/network?source=i&ids=abc").status_code == 422
    assert client.get("/api/neurons/i/943/tortuosity?location_a=1&location_b=2").status_code == 400
    assert client.post("/api/neurons", json={"source": "i"}).status_code == 422
    assert client.post("/api/neurons", json={"id": 943, "source": "i",
                                             "section_corrections": "sideways"}).status_code == 422


def test_a_load_body_reads_a_string_flag_as_the_boolean_it_spells(client, monkeypatch):
    """``bool("false")`` is true: a flag sent as text used to switch the option on."""
    seen = {}

    def load(neuron_id, source, **options):
        seen.update(options, neuron_id=neuron_id, source=source)
        raise ValueError("stop here")

    monkeypatch.setattr(client.app.state.service, "load_neuron", load)
    body = {"id": 943, "source": "i", "refresh": "false", "include_synapses": "false",
            "live_registration": "false", "check_fresh": "false", "transform": "None",
            "keep_outlines": "true"}
    assert client.post("/api/neurons", json=body).status_code == 400
    assert seen["refresh"] is False and seen["include_synapses"] is False
    assert seen["live_registration"] is False and seen["transform"] == "None"
    assert seen["keep_outlines"] is True
    assert seen["neuron_id"] == 943 and seen["source"] == "NeitzInferiorMonkey"
    # What was already true stays true: defaults, and null read as false.
    seen.clear()
    client.post("/api/neurons", json={"id": 943})
    assert seen["include_synapses"] is True and seen["refresh"] is False
    assert seen["live_registration"] is None and seen["transform"] == "Standard"
    assert seen["keep_outlines"] is None
    assert seen["source"] == "NeitzInferiorMonkey"
    seen.clear()
    client.post("/api/neurons", json={"id": 943, "include_synapses": None, "refresh": None})
    assert seen["include_synapses"] is False and seen["refresh"] is False
    # A flag that is neither is refused, not guessed at.
    assert client.post("/api/neurons", json={"id": 943, "refresh": "maybe"}).status_code == 422
    assert client.post("/api/neurons", json={"id": 943, "source": None}).status_code == 404


def test_an_unknown_method_is_a_400_on_the_mesh_and_export_routes(client):
    for path in ("/api/neurons/i/943/mesh", "/api/neurons/i/943/export/stl",
                 "/api/neurons/i/943/mesh.glb", "/api/neurons/i/943/mesh-job"):
        r = client.get(path, params={"method": "bogus"})
        if path.endswith("mesh-job"):
            # A job reports it on its poll, as it reports every build that cannot run.
            assert r.status_code == 200
            job = r.json()["job_id"]
            for _ in range(100):
                poll = client.get(f"/api/mesh-jobs/{job}").json()
                if poll["status"] != "queued" and poll["status"] != "running":
                    break
                time.sleep(0.05)
            assert poll["status"] == "error" and "method must be one of" in poll["detail"]
        else:
            assert r.status_code == 400 and "method must be one of" in r.json()["detail"], path


def test_the_web_app_corrects_by_curve_fit_unless_asked_otherwise(client):
    # The user's decision of 2026-09-24; the library's own default stays off.
    assert client.app.state.service.correction_mode == "curvefit"


def test_the_web_app_corrects_the_neitz_volumes_by_field_and_fit(client):
    # The user's decision of 2026-10-20: a Neitz lab cell is drawn through the
    # current section transforms and corrected by the field, then the fit; the
    # page learns each volume's default from /api/volumes.
    svc = client.app.state.service
    volumes = {v["name"]: v for v in client.get("/api/volumes").json()}
    for name in ("NeitzInferiorMonkey", "NeitzNasalMonkey", "NeitzTemporalMonkey", "NeitzCped"):
        assert svc.default_correction(name) == "all"
        assert volumes[name]["lab"] == "Neitz"
        assert volumes[name]["default_correction"] == "all"
        assert volumes[name]["default_registration"] is True
    for name in ("RC1", "RC2", "RPC1", "RPC2"):
        assert volumes[name]["lab"] == "Marc"
        assert volumes[name]["default_correction"] == "curvefit"


def test_index_stamps_asset_urls(client):
    """A changed asset must not be served from a browser's cache."""
    import re

    from sbfsem import __version__
    from sbfsem_web.app import asset_version

    html = client.get("/").text
    assert "__SBFSEM_VERSION__" not in html
    version = asset_version()
    assert version.startswith(f"{__version__}-")
    assert f"/static/js/app.js?v={version}" in html
    assert f"/static/css/app.css?v={version}" in html
    assert client.get(f"/static/js/app.js?v={version}").status_code == 200
    # Every asset reference carries a stamp.
    for match in re.findall(r'"/static/(?:js|css)/[^"]+"', html):
        assert "?v=" in match, match


def test_asset_version_tracks_file_changes():
    """Editing a served file changes the cache key, even at the same version.

    Skipped against a hardened install, where the package tree is root-owned and
    read-only on purpose — the staleness this guards against is a development
    concern, and assets that cannot change cannot go stale.
    """
    import os

    from sbfsem_web.app import STATIC_DIR, asset_version

    target = STATIC_DIR / "js" / "app.js"
    if not os.access(target, os.W_OK):
        pytest.skip("package tree is read-only (production install)")

    before = asset_version()
    original = target.stat().st_mtime_ns
    try:
        os.utime(target, ns=(original + 1_000_000_000, original + 1_000_000_000))
        assert asset_version() != before
    finally:
        os.utime(target, ns=(original, original))
    assert asset_version() == before


# -- saved documents -------------------------------------------------------
@pytest.fixture
def save_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SBFSEM_SAVE_DIR", str(tmp_path / "saves"))
    monkeypatch.setenv("SBFSEM_OFFLINE", "1")
    with TestClient(create_app()) as c:
        yield c


def test_saves_round_trip(save_client):
    assert save_client.get("/api/saves").json() == []
    assert save_client.post("/api/saves", json={"name": "Project A"}).status_code == 201

    doc = {"schema": 1, "neurons": [{"id": 1178}]}
    put = save_client.put("/api/saves/Project A/run one", json=doc)
    assert put.status_code == 200
    assert put.json()["name"] == "run one"

    assert [f["name"] for f in save_client.get("/api/saves/Project A").json()] == ["run one"]
    assert save_client.get("/api/saves/Project A/run one").json() == doc

    folders = save_client.get("/api/saves").json()
    assert folders[0]["name"] == "Project A" and folders[0]["files"] == 1

    assert save_client.delete("/api/saves/Project A/run one").status_code == 204
    assert save_client.get("/api/saves/Project A").json() == []


@pytest.mark.parametrize("name", [
    "../etc", "..", ".", "/etc/passwd", "a/b", "a\\b", ".hidden", "",
    "con", "x" * 64,
])
def test_saves_reject_unsafe_folder_names(save_client, name, tmp_path):
    """Traversal and reserved names are refused, and nothing lands outside."""
    r = save_client.post("/api/saves", json={"name": name})
    assert r.status_code == 400, f"{name!r} was accepted"
    base = tmp_path / "saves"
    strays = [p for p in tmp_path.rglob("*") if p != base and base not in p.parents]
    assert not strays, strays


@pytest.mark.parametrize("name", ["../escape", "..", "a/b", ".env", "x" * 64])
def test_saves_reject_unsafe_document_names(save_client, name):
    """Any status but 2xx is fine: some of these never reach the handler.

    A literal ``..`` in the path is normalised away by the HTTP layer before
    routing, which is a rejection in its own right; the encoded case below is
    the one that reaches the name validation.
    """
    save_client.post("/api/saves", json={"name": "Safe"})
    r = save_client.put(f"/api/saves/Safe/{name}", json={"x": 1})
    assert r.status_code >= 400, f"{name!r} was accepted"


@pytest.mark.parametrize("encoded", ["%2e%2e%2fescape", "%2e%2e", "a%2fb", "%2e env"])
def test_saves_reject_percent_encoded_traversal(save_client, encoded, tmp_path):
    """Encoded separators survive routing, so validation has to catch them."""
    save_client.post("/api/saves", json={"name": "Safe"})
    r = save_client.put(f"/api/saves/Safe/{encoded}", json={"x": 1})
    assert r.status_code >= 400, f"{encoded!r} was accepted"
    base = tmp_path / "saves"
    strays = [p for p in tmp_path.rglob("*") if p != base and base not in p.parents]
    assert not strays, strays


def test_saves_reject_encoded_traversal_in_folder(save_client, tmp_path):
    r = save_client.put("/api/saves/%2e%2e/doc", json={"x": 1})
    assert r.status_code >= 400
    base = tmp_path / "saves"
    assert not [p for p in tmp_path.rglob("*") if p != base and base not in p.parents]


def test_saves_enforce_the_size_limit(save_client):
    """The quota is the only bound on an unauthenticated writer, so it must hold."""
    save_client.post("/api/saves", json={"name": "Big"})
    huge = {"blob": "x" * (6 * 1024 * 1024)}
    r = save_client.put("/api/saves/Big/huge", json=huge)
    assert r.status_code == 507
    assert save_client.get("/api/saves/Big").json() == []


def test_an_oversize_document_is_refused_before_it_is_read(save_client, monkeypatch):
    """nginx lets 64 MB through for the neuron upload; the saves are capped at 5 MB, and
    the declared length decides without the route parsing the body."""
    from sbfsem_web.saves import SaveStore

    save_client.post("/api/saves", json={"name": "Big"})
    reached = []
    monkeypatch.setattr(SaveStore, "write", lambda *args, **kw: reached.append(args))
    body = b'{"blob": "' + b"x" * (6 * 1024 * 1024) + b'"}'
    r = save_client.put("/api/saves/Big/huge", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 507 and "6144 kB exceeds the 5120 kB limit" in r.json()["detail"]
    assert reached == []
    # The folder route is capped the same way, and a small one still goes through.
    r = save_client.post("/api/saves", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 507
    monkeypatch.undo()
    assert save_client.put("/api/saves/Big/small", json={"x": 1}).status_code == 200


def test_the_size_cap_is_on_the_body_as_sent_and_says_so(save_client):
    """The declared-length check cannot know what the store will write, so its limit is on
    the request body as sent, and its message says what the limit is on.  A document that
    the store keeps at 2.8 MB, pretty-printed past 5 MB, is refused (a script's problem:
    the viewer sends compact JSON); the same document sent compact is stored."""
    import json

    save_client.post("/api/saves", json={"name": "Pretty"})
    document = {"rows": [[i, i + 1, i + 2] for i in range(60_000)]}
    from sbfsem_web.saves import MAX_FILE_BYTES, _dumps

    stored = len(_dumps(document, indent=1))
    padded = json.dumps(document, indent=10).encode("utf-8")
    compact = json.dumps(document, separators=(",", ":")).encode("utf-8")
    assert stored < MAX_FILE_BYTES < len(padded) and len(compact) < stored, (stored, len(padded))
    headers = {"content-type": "application/json"}
    refused = save_client.put("/api/saves/Pretty/doc", content=padded, headers=headers)
    assert refused.status_code == 507
    detail = refused.json()["detail"]
    assert f"{len(padded) // 1024} kB exceeds the {MAX_FILE_BYTES // 1024} kB limit" in detail
    assert "request body as sent" in detail and "compact" in detail
    assert save_client.put("/api/saves/Pretty/doc", content=compact, headers=headers).status_code == 200
    assert save_client.get("/api/saves/Pretty/doc").json() == document
    # And the other way, which only the store can judge, by what it writes: nested lists are
    # small compact and large indented (every level of every chain starts a line of its own).
    chain: list = []
    for _ in range(400):
        chain = [chain]
    nested = {"chains": [chain] * 50}
    body = json.dumps(nested, separators=(",", ":")).encode("utf-8")
    assert len(body) < MAX_FILE_BYTES < len(_dumps(nested, indent=1)), (len(body), len(_dumps(nested, indent=1)))
    refused = save_client.put("/api/saves/Pretty/nested", content=body, headers=headers)
    assert refused.status_code == 507 and "request body as sent" not in refused.json()["detail"]


def test_a_document_json_cannot_hold_is_a_400_not_a_500(save_client):
    """A client other than the browser's (whose JSON.stringify writes null) can send NaN."""
    save_client.post("/api/saves", json={"name": "Odd"})
    r = save_client.put("/api/saves/Odd/nan", content=b'{"x": NaN}',
                        headers={"content-type": "application/json"})
    assert r.status_code == 400 and "JSON" in r.json()["detail"]
    assert save_client.get("/api/saves/Odd").json() == []


def test_a_corrupt_saved_document_is_a_400_that_names_it(save_client, tmp_path):
    save_client.post("/api/saves", json={"name": "Old"})
    save_client.put("/api/saves/Old/good", json={"x": 1})
    (tmp_path / "saves" / "Old" / "torn.json").write_text('{"x": [1, 2', encoding="utf-8")
    (tmp_path / "saves" / "Old" / "binary.json").write_bytes(b"\xff\xfe\x00")
    for name in ("torn", "binary"):
        r = save_client.get(f"/api/saves/Old/{name}")
        assert r.status_code == 400 and f"'{name}' is not a readable document" in r.json()["detail"], name
    assert save_client.get("/api/saves/Old/good").json() == {"x": 1}
    assert [f["name"] for f in save_client.get("/api/saves/Old").json()] == ["binary", "good", "torn"]


def test_saves_writing_to_a_missing_folder_is_a_clean_error(save_client):
    r = save_client.put("/api/saves/Nope/doc", json={"x": 1})
    assert r.status_code == 400
    assert "folder" in r.json()["detail"].lower()


def test_render_prefs_roundtrip(client, tmp_path, monkeypatch):
    """A render attached to a cell persists on the server and comes back with
    the cell's detail, so every future load starts from it."""
    from sbfsem_web.saves import CellRenderStore

    app = client.app
    monkeypatch.setattr(app.state, "cell_renders",
                        CellRenderStore(tmp_path / "cell-renders"), raising=False)

    assert client.get("/api/neurons/i/943/render-prefs").json() == {"render": None}
    put = client.put("/api/neurons/i/943/render-prefs",
                     json={"pathway": "voxel", "tie_um": 15})
    assert put.status_code == 200
    stored = put.json()["render"]
    assert stored["pathway"] == "voxel" and stored["tie_um"] == 15.0

    got = client.get("/api/neurons/i/943/render-prefs").json()["render"]
    assert got["pathway"] == "voxel"
    detail = client.get("/api/neurons/i/943").json()
    assert detail["attached_render"]["pathway"] == "voxel"

    assert client.delete("/api/neurons/i/943/render-prefs").json() == {"removed": True}
    assert client.get("/api/neurons/i/943/render-prefs").json() == {"render": None}
    assert client.get("/api/neurons/i/943").json()["attached_render"] is None

    # Junk options are rejected by the same validation the mesh endpoint uses.
    assert client.put("/api/neurons/i/943/render-prefs",
                      json={"pathway": "bogus"}).status_code == 422


def test_an_attached_render_keeps_the_cut_and_the_strata_coloring(client, tmp_path, monkeypatch):
    """The viewer sends its whole render; the body used to be the diff's, which
    leaves the view's own options out, so a Clip IPL came back as no clip."""
    from sbfsem_web.saves import CellRenderStore

    monkeypatch.setattr(client.app.state, "cell_renders",
                        CellRenderStore(tmp_path / "cell-renders"), raising=False)
    sent = {"pathway": "voxel", "clip_ipl": 0.4, "keep_above": False, "color_by_strata": True,
            "clip_z": 12.5}
    stored = client.put("/api/neurons/i/943/render-prefs", json=sent).json()["render"]
    for key, value in sent.items():
        assert stored[key] == value, key
    assert client.get("/api/neurons/i/943").json()["attached_render"]["clip_ipl"] == 0.4
    # A cut that is off stays out of the document (a null would read as a value),
    # and the mesh options' own bounds still apply.
    off = client.put("/api/neurons/i/943/render-prefs", json={"clip_ipl": None, "clip_z": None}).json()
    assert "clip_ipl" not in off["render"] and "clip_z" not in off["render"]
    assert off["render"]["keep_above"] is True and off["render"]["color_by_strata"] is False
    assert client.put("/api/neurons/i/943/render-prefs",
                      json={"smooth_iterations": 51}).status_code == 422


def test_an_attached_render_needs_a_structure_id_and_a_bounded_number_of_them(client, tmp_path, monkeypatch):
    from sbfsem_web import saves
    from sbfsem_web.saves import CellRenderStore

    monkeypatch.setattr(client.app.state, "cell_renders",
                        CellRenderStore(tmp_path / "cell-renders"), raising=False)
    for bad in ("0", "-5", str(2**31)):
        for method in ("get", "put", "delete"):
            kwargs = {"json": {}} if method == "put" else {}
            r = getattr(client, method)(f"/api/neurons/i/{bad}/render-prefs", **kwargs)
            assert r.status_code == 422, (method, bad)
    assert not list((tmp_path / "cell-renders").rglob("*.json"))
    # Past the cap a new cell is refused as the saves are (507); a cell that has
    # one may still replace it.
    monkeypatch.setattr(saves, "MAX_ATTACHED_RENDERS", 2)
    for cell in (1, 2):
        assert client.put(f"/api/neurons/i/{cell}/render-prefs", json={}).status_code == 200
    assert client.put("/api/neurons/i/3/render-prefs", json={}).status_code == 507
    assert client.put("/api/neurons/i/2/render-prefs", json={"tie_um": 5}).status_code == 200


def test_colormap_tables_js_is_generated_from_the_registry():
    """The app colors from the same MATLAB tables the library ships.

    Regenerate with ``python -m sbfsem.util.colormaps > src/sbfsem_web/static/js/colormap-tables.js``.
    """
    from importlib import resources

    from sbfsem.util.colormaps import js_tables_source

    shipped = resources.files("sbfsem_web").joinpath("static", "js", "colormap-tables.js")
    assert shipped.read_text(encoding="utf-8") == js_tables_source()


def test_locate_finds_a_loaded_annotation_and_404s_an_unknown_one(client):
    """The pick readout's Goto Location ID: a loaded cell answers for its own
    annotations in its micron frame; an id nobody knows is a 404 (offline
    here, so Viking cannot be asked)."""
    nodes = client.get("/api/neurons/i/943/nodes").json()
    node = nodes[0]
    found = client.get(f"/api/volumes/i/locations/{node['id']}").json()
    assert found["loaded"] is True
    assert found["structure_id"] == 943
    assert found["xyz_um"] == pytest.approx(node["xyz_um"])
    assert len(found["viking"]) == 3 and all(isinstance(v, int) for v in found["viking"])
    assert client.get("/api/volumes/i/locations/987654321").status_code == 404


def test_voxel2_pathway_is_accepted_everywhere(client, tmp_path, monkeypatch):
    """The fifth pathway passes the same validation as the others: the mesh
    endpoint, attached renders and the diff body."""
    from sbfsem_web.saves import CellRenderStore

    app = client.app
    monkeypatch.setattr(app.state, "cell_renders",
                        CellRenderStore(tmp_path / "cell-renders"), raising=False)
    put = client.put("/api/neurons/i/943/render-prefs",
                     json={"pathway": "voxel2", "voxel2_um": 0.1, "voxel2_faces": 0})
    assert put.status_code == 200
    stored = put.json()["render"]
    assert stored["pathway"] == "voxel2" and stored["voxel2_um"] == 0.1
    assert stored["voxel2_faces"] == 0
    assert client.put("/api/neurons/i/943/render-prefs",
                      json={"pathway": "voxel2", "voxel2_um": 5}).status_code == 422
    started = client.get("/api/neurons/i/943/mesh-job?pathway=voxel2&voxel2_um=0.2")
    assert started.status_code == 200


# -- linked neurons and connectivity, with Viking faked --------------------
class _FakeViking:
    """Answers the link queries the report and a connectivity query make."""

    def __init__(self):
        self.calls = []

    # getAllLinkedNeurons: one row per synapse in either orientation, so the
    # right one is picked whatever the synapse's type.
    def fetch_structure_links(self, ids, batch_size=None):
        rows = []
        for sid in ids:
            rows.append({"SourceID": sid, "TargetID": 9000 + sid,
                         "Source": {"ID": sid, "ParentID": 943},
                         "Target": {"ID": 9000 + sid, "ParentID": 555}})
            rows.append({"SourceID": 8000 + sid, "TargetID": sid,
                         "Source": {"ID": 8000 + sid, "ParentID": 777},
                         "Target": {"ID": sid, "ParentID": 943}})
        return rows

    def get_labels(self, ids):
        return {555: "ON parasol", 777: "AC"}

    # Connectivity: c943 -ConvPre-> c555 -RibbonPre-> c777
    def fetch_links_by_parent_structures(self, ids, batch_size=None):
        self.calls.append(sorted(ids))
        links = [
            {"SourceID": 1, "TargetID": 2, "Bidirectional": False,
             "Source": {"ID": 1, "ParentID": 943, "TypeID": 34, "Tags": ""},
             "Target": {"ID": 2, "ParentID": 555, "TypeID": 35, "Tags": ""}},
            {"SourceID": 3, "TargetID": 4, "Bidirectional": False,
             "Source": {"ID": 3, "ParentID": 555, "TypeID": 73, "Tags": ""},
             "Target": {"ID": 4, "ParentID": 777, "TypeID": 35, "Tags": ""}},
        ]
        ids = set(ids)
        return [r for r in links
                if r["Source"]["ParentID"] in ids or r["Target"]["ParentID"] in ids]

    def get_structures_brief(self, ids):
        labels = {943: "GC", 555: "ON parasol", 777: "AC"}
        return {i: {"label": labels.get(i, ""), "type_id": 1, "parent_id": None} for i in ids}

    def query_labels(self, search=None):
        return [{"ID": 943, "Label": "GC", "TypeID": 1},
                {"ID": 555, "Label": "ON parasol", "TypeID": 1}]


@pytest.fixture
def faked_viking(client):
    svc = client.app.state.service
    fake = _FakeViking()
    saved = svc._clients.get("NeitzInferiorMonkey")
    svc._clients["NeitzInferiorMonkey"] = fake
    # Links are cached on the neuron; start each test from Viking.
    neuron = svc.neuron(943, "i")
    neuron.links = None
    svc._link_graphs.clear()
    yield fake
    neuron.links = None
    if saved is not None:
        svc._clients["NeitzInferiorMonkey"] = saved
    else:
        svc._clients.pop("NeitzInferiorMonkey", None)


def test_linked_neurons_report(client, faked_viking):
    body = client.get("/api/neurons/i/943/linked-neurons").json()
    assert body["neuron_id"] == 943 and body["source"] == "NeitzInferiorMonkey"
    assert body["order"] == "neuron"
    assert body["n_synapses"] >= 1
    assert body["n_linked"] == body["n_synapses"] - body["n_unlinked"]
    assert body["n_partners"] >= 1
    row = body["rows"][0]
    assert set(row) == {"neuron_id", "synapse_id", "synapse_type", "neuron_label",
                        "direction", "synapse_xyz_um", "tags", "tag_status",
                        "suggested_tag", "location_id", "synapse_shift_um"}
    assert row["synapse_shift_um"] is None  # no section correction on this cell
    assert body["partners"][0]["count"] >= 1
    # The sorted-script ordering is a query away.
    typed = client.get("/api/neurons/i/943/linked-neurons?order=type").json()
    assert typed["order"] == "type"
    assert [r["synapse_type"] for r in typed["rows"]] == sorted(
        r["synapse_type"] for r in typed["rows"])
    assert client.get("/api/neurons/i/943/linked-neurons?order=random").status_code == 422
    assert client.get("/api/neurons/i/99999999/linked-neurons").status_code == 404


def test_linked_neurons_downloads(client, faked_viking):
    csv = client.get("/api/neurons/i/943/linked-neurons.csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    # The volume travels with the ID in the filename (ground rule 11).
    assert 'filename="c943-NeitzInferiorMonkey-linked-neurons.csv"' in csv.headers["content-disposition"]
    assert csv.text.splitlines()[0].startswith("NeuronID,SynapseID,SynapseType,NeuronLabel,")

    xlsx = client.get("/api/neurons/i/943/linked-neurons.xlsx?order=type")
    assert xlsx.status_code == 200
    assert xlsx.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert xlsx.content[:2] == b"PK"
    assert client.get("/api/neurons/i/943/linked-neurons.txt").status_code == 400


def test_connectivity_query_endpoint(client, faked_viking):
    body = client.post("/api/connectivity/query", json={
        "source": "i", "hops": 2, "start_ids": [943],
    }).json()
    assert body["source"] == "NeitzInferiorMonkey"
    assert body["direction"] == "downstream" and body["anchors"] == [943]
    assert body["n_paths"] == 1
    assert body["paths"][0][0::2] == [943, 555, 777]
    assert body["matrix"] == {"rows": [943], "cols": [777], "cells": [[[0]]]}
    assert body["node_table"]["columns"] == ["2:1"]
    assert body["node_table"]["rows"][0]["id"] == 555
    assert body["nodes"]["555"]["label"] == "ON parasol"
    assert [e["type"] for e in body["edges"]] == ["ConvPre", "RibbonPre"]
    assert body["expanded"] == 2 and faked_viking.calls == [[943], [555]]
    assert body["graph_nodes"] == 3 and body["graph_synapses"] == 2

    # The graph is kept: the same query fetches nothing more.
    again = client.post("/api/connectivity/query", json={
        "source": "i", "hops": 2, "start_ids": [943]}).json()
    assert again["expanded"] == 0 and again["n_paths"] == 1
    size = client.get("/api/connectivity/i/graph").json()
    assert size == {"nodes": 3, "edges": 2, "expanded": 2, "synapses": 2}

    # Upstream from the end, with a label-pattern anchor set.
    up = client.post("/api/connectivity/query", json={
        "source": "i", "hops": 1, "direction": "upstream", "start_pattern": "ON.*"}).json()
    assert up["anchors"] == [555] and up["paths"][0][0::2] == [943, 555]

    # Refresh forgets the cached graph first.
    fresh = client.post("/api/connectivity/query", json={
        "source": "i", "hops": 1, "start_ids": [943], "refresh": True}).json()
    assert fresh["expanded"] == 1
    assert fresh["allow_repeats"] is False
    loop = client.post("/api/connectivity/query", json={
        "source": "i", "hops": 2, "start_ids": [943], "allow_repeats": True}).json()
    assert loop["allow_repeats"] is True and loop["n_paths"] == 1  # nothing to repeat here


def test_connectivity_query_rejects_bad_input(client, faked_viking):
    post = lambda body: client.post("/api/connectivity/query", json=body)  # noqa: E731
    assert post({"source": "i", "hops": 2}).status_code == 422  # no anchors
    assert post({"source": "i", "hops": 9, "start_ids": [1]}).status_code == 422
    assert post({"source": "i", "hops": 2, "min_hops": 3, "start_ids": [1]}).status_code == 422
    assert post({"source": "i", "hops": 2, "start_ids": [1], "direction": "sideways"}).status_code == 422
    bad = post({"source": "i", "hops": 1, "start_ids": [943], "node_patterns": ["(", ""]})
    assert bad.status_code == 400 and "pattern" in bad.json()["detail"]


def test_each_tab_is_answered_from_its_own_copy_of_a_cell(client):
    """The page names the copy it loaded (``?copy=``, from the summary's
    ``copy_tag``); two settings of one cell get two answers, and a tab
    unloading its copy leaves the other's."""
    from pathlib import Path

    import pytest

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "i943.json"
    moved = read_legacy_json(fixture)
    moved.nodes["Xum"] += 5.0
    moved.registration_status = {"requested": True, "applied": True}
    client.app.state.service.add_neuron(moved)

    plain = client.get("/api/neurons/i/943/nodes?copy=off.0").json()
    shifted = client.get("/api/neurons/i/943/nodes?copy=off.1").json()
    assert shifted[0]["xyz_um"][0] - plain[0]["xyz_um"][0] == pytest.approx(5.0)
    assert client.get("/api/neurons/i/943").json()["summary"]["copy_tag"] == "off.1"
    assert client.get("/api/neurons/i/943?copy=off.0").json()["summary"]["copy_tag"] == "off.0"

    assert client.delete("/api/neurons/i/943?copy=off.0").status_code == 200
    gone = client.get("/api/neurons/i/943/nodes?copy=off.0")
    assert gone.status_code == 404
    assert "is not loaded with the setting off.0" in gone.json()["detail"]
    assert client.get("/api/neurons/i/943/nodes?copy=off.1").status_code == 200


def test_a_load_with_manual_moves_names_its_copy_and_a_bad_move_is_a_400(client):
    """Manual moves (2026-10-19) travel with the load, by the setting they were made
    under; the copy they make is named in ``copy_tag`` and answers ``?copy=``."""
    plain_body = {"id": 943, "source": "i", "section_corrections": "off", "live_registration": False,
                  "check_fresh": False}
    assert client.post("/api/neurons", json=plain_body).json()["summary"]["copy_tag"] == "off.0"
    first = client.get("/api/neurons/i/943/nodes?copy=off.0").json()[0]
    body = {**plain_body, "manual_moves": {"off.0": [{"id": first["id"], "dx_um": 0.5, "dz": 1}]}}
    answer = client.post("/api/neurons", json=body)
    assert answer.status_code == 200, answer.text
    summary = answer.json()["summary"]
    tag = summary["copy_tag"]
    assert tag.startswith("off.0.m") and summary["manual_moves"]["n_moved"] == 1
    moved = {n["id"]: n for n in client.get(f"/api/neurons/i/943/nodes?copy={tag}").json()}
    row = moved[first["id"]]
    assert row["xyz_um"][0] == pytest.approx(first["xyz_um"][0] + 0.5)
    assert row["z_section"] == first["z_section"] + 1 and row["manual_dz"] == 1
    plain = {n["id"]: n for n in client.get("/api/neurons/i/943/nodes?copy=off.0").json()}
    assert plain[first["id"]]["manual_shift_um"] is None
    body["manual_moves"] = {"off.0": [{"id": first["id"], "dx_um": 5000}]}
    assert client.post("/api/neurons", json=body).status_code == 400
    body["manual_moves"] = {"off.0": "everything"}
    assert client.post("/api/neurons", json=body).status_code == 422
    # Back to the plain copy for the tests that follow.
    body.pop("manual_moves")
    assert client.post("/api/neurons", json=body).json()["summary"]["copy_tag"] == "off.0"
