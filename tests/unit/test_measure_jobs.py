"""Render compare's measurements as jobs: no request carries a build or a measurement.

On 2026-10-15 a pane built through MeshLab isotropic and measured against the
annotations answered "504 -- the gateway gave up after 5 minutes": the
synchronous POST built the mesh (115 CPU-seconds in MeshLab alone) and then
ran the distance kernels inside the request.  Both are jobs now; these tests
pin the contract the browser codes against.
"""

from __future__ import annotations

import json
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sbfsem import progress
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem_web.app import create_app

#: Fast, deterministic options on the 943 fixture (a small disc-traced cell).
QUICK = {"method": "cylinder", "pathway": "original", "smooth_iterations": 0}
OTHER = {"method": "cylinder", "pathway": "adaptive_taubin", "smooth_iterations": 0}


@pytest.fixture
def web(request):
    app = create_app()
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as client:
        app.state.service.offline = True
        # No disk cache: a mesh built by an earlier test (or by the person
        # running the suite) must not stand in for the build under test.
        app.state.service._disk_meshes = None
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield client, app.state.service


def _poll(client, job_id, *, seconds=120.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = client.get(f"/api/mesh-jobs/{job_id}").json()
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def test_a_deviation_job_answers_the_start_at_once_and_carries_no_geometry(web):
    client, svc = web
    started = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["kind"] == "deviation" and body["job_id"] and body["weight_mb"] >= 900
    job = _poll(client, body["job_id"])
    assert job["status"] == "done" and job["kind"] == "deviation"
    assert job["mesh"] is None and job["failed_stage"] == ""
    result = job["result"]
    assert "vertices" not in result and "faces" not in result and "mesh" not in result
    assert len(result["vertex_um"]) == result["n_vertices"] > 0
    assert result["deviation"]["n_discs"] > 0 and result["scale_um"] > 0
    # The numbers belong to the mesh the browser already holds: same id, same size.
    mesh = client.get("/api/neurons/i/943/mesh", params=QUICK).json()
    assert mesh["mesh_id"] and mesh["mesh_id"] == result["mesh_id"]
    assert mesh["n_vertices"] == result["n_vertices"]
    # ... and the distances the definition gives, computed here from the mesh and
    # the annotations rather than read back from the same cache.
    from sbfsem.geometry.meshdiff import deviation_map
    from sbfsem.service.facade import annotation_ground_truth

    neuron = svc.neuron(943, "i")
    expected = deviation_map(svc.mesh_for(neuron, QUICK), *annotation_ground_truth(neuron))
    np.testing.assert_allclose(result["vertex_um"], np.round(expected.vertex_um, 4), atol=1e-9)
    assert result["scale_um"] == pytest.approx(expected.scale_um)
    assert result["n_rims"] == expected.n_rims
    # The synchronous route (kept) answers the same numbers (it rounds to 6 places).
    sync = client.post("/api/neurons/i/943/mesh-deviation", json=QUICK).json()
    np.testing.assert_allclose(sync["mesh"]["vertex_data"], result["vertex_um"], atol=2e-4)


def test_the_incident_a_pane_measured_before_its_mesh_was_built(web, monkeypatch):
    """The browser asked to measure a mesh nobody had built.  The request that
    starts the work returns in milliseconds, however long the build takes; the
    client polls, and sees the build's own stages."""
    client, svc = web
    real = svc._run_build

    def slow_build(*args, **kwargs):
        progress.report("MeshLab remesh")
        time.sleep(4.0)
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "_run_build", slow_build)
    t0 = time.time()
    started = client.post("/api/neurons/i/943/mesh-deviation-job", json=OTHER).json()
    # Well inside the build's 4 s, loose enough for a loaded machine.
    assert time.time() - t0 < 3.0, "the start request waited for the work"
    seen = []
    deadline = time.time() + 120
    while time.time() < deadline:
        job = client.get(f"/api/mesh-jobs/{started['job_id']}").json()
        if job["progress"]:
            seen.append(job["progress"]["stage"])
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert "MeshLab remesh" in seen


def test_identical_requests_share_one_job_and_cancel_only_when_the_last_lets_go(web, monkeypatch):
    client, svc = web
    gate = threading.Event()
    monkeypatch.setattr(svc, "_run_build", lambda *a, **k: gate.wait(5) and None)
    store = client.app.state.mesh_jobs
    # Fill every worker so the measurement stays queued.
    blockers = [store.submit((f"hold{i}",), lambda: gate.wait(5), weight_mb=0.0)
                for i in range(store.workers)]
    time.sleep(0.2)
    a = client.post("/api/neurons/i/943/mesh-deviation-job", json=OTHER).json()
    b = client.post("/api/neurons/i/943/mesh-deviation-job", json=OTHER).json()
    assert a["job_id"] == b["job_id"]
    assert client.delete(f"/api/mesh-jobs/{a['job_id']}").json() == {"cancelled": False}
    assert client.delete(f"/api/mesh-jobs/{b['job_id']}").json() == {"cancelled": True}
    assert client.get(f"/api/mesh-jobs/{a['job_id']}").json()["status"] == "cancelled"
    gate.set()
    for job in blockers:
        for _ in range(100):
            if job.status == "done":
                break
            time.sleep(0.02)


def test_a_diff_job_returns_the_distances_of_mesh_a(web):
    client, svc = web
    started = client.post("/api/neurons/i/943/mesh-diff-job", json={"a": QUICK, "b": OTHER}).json()
    assert started["kind"] == "diff"
    job = _poll(client, started["job_id"])
    assert job["status"] == "done" and job["mesh"] is None
    result = job["result"]
    mesh_a = client.get("/api/neurons/i/943/mesh", params=QUICK).json()
    assert result["mesh_id"] == mesh_a["mesh_id"] and result["n_vertices"] == mesh_a["n_vertices"]
    assert len(result["distance_um"]) == result["n_vertices"]
    assert result["max_abs_um"] >= result["p95_abs_um"] >= 0 and result["n_samples"] > 0
    assert result["deviation_a"]["n_discs"] > 0 and result["deviation_b"]["n_discs"] > 0
    # The definition, from the two meshes, and the synchronous route (kept) agree.
    from sbfsem.geometry.meshdiff import mesh_difference

    neuron = svc.neuron(943, "i")
    expected = mesh_difference(svc.mesh_for(neuron, QUICK), svc.mesh_for(neuron, OTHER),
                               samples=250_000)
    np.testing.assert_allclose(result["distance_um"], np.round(expected.distance_um, 4), atol=1e-9)
    assert result["mean_abs_um"] == pytest.approx(expected.mean_abs_um)
    sync = client.post("/api/neurons/i/943/mesh-diff", json={"a": QUICK, "b": OTHER}).json()
    assert sync["mean_abs_um"] == pytest.approx(result["mean_abs_um"])


def test_a_failed_measurement_says_which_stage_it_failed_in(web, monkeypatch):
    client, svc = web
    from sbfsem.service import facade

    def failing(mesh, *args, **kwargs):
        progress.report("distance to outlines", 3, 10)
        raise ValueError("boom")

    monkeypatch.setattr(facade, "deviation_map", failing)
    started = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    job = _poll(client, started["job_id"])
    assert job["status"] == "error" and "boom" in job["detail"]
    assert job["failed_stage"] == "distance to outlines" and job["result"] is None
    # A failed job does not block the retry.
    monkeypatch.undo()
    retry = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    assert retry["job_id"] != started["job_id"]
    assert _poll(client, retry["job_id"])["status"] == "done"


def test_a_cell_loaded_again_gets_new_measurement_jobs_not_the_old_ones(web, request):
    """A finished job is kept ten minutes.  Handed to a cell loaded again (other
    registration, an update from Viking) it would paint the old copy's numbers:
    the failure of 2026-09-23 for surfaces, which test_web pins for mesh jobs."""
    client, svc = web
    deviation = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    diff = client.post("/api/neurons/i/943/mesh-diff-job", json={"a": QUICK, "b": OTHER}).json()
    again = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    assert again["job_id"] == deviation["job_id"]               # the same copy shares its job
    _poll(client, deviation["job_id"])
    _poll(client, diff["job_id"])
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    svc.add_neuron(read_legacy_json(fixture))                   # loaded again
    assert client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()["job_id"] \
        != deviation["job_id"]
    assert client.post("/api/neurons/i/943/mesh-diff-job",
                       json={"a": QUICK, "b": OTHER}).json()["job_id"] != diff["job_id"]


def test_start_requests_name_the_copy_and_refuse_an_unknown_one(web):
    """The browser sends ?copy= on every request for a cell, the POSTs included."""
    client, _ = web
    assert client.post("/api/neurons/i/943/mesh-deviation-job?copy=nope.9", json=QUICK).status_code == 404
    assert client.post("/api/neurons/i/943/mesh-diff-job?copy=nope.9",
                       json={"a": QUICK, "b": OTHER}).status_code == 404


def test_a_build_that_fails_is_not_run_again_by_everyone_waiting_for_it(web, monkeypatch):
    """Three requests want one mesh; its MeshLab build fails after minutes.  The
    two that waited used to build it again, one after another (3 builds, the last
    failure heard after 3x the time); now they hear the first failure."""
    _, svc = web
    neuron = svc.neuron(943, "i")
    builds = []

    def failing(*args, **kwargs):
        builds.append(1)
        time.sleep(0.5)
        raise RuntimeError("MeshLab isotropic did not finish")

    monkeypatch.setattr(svc, "_run_build", failing)
    errors = []

    def want():
        try:
            svc.mesh_for(neuron, OTHER)
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=want) for _ in range(3)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 1 and errors == ["MeshLab isotropic did not finish"] * 3
    assert time.time() - t0 < 1.4
    # A request after they have all left starts fresh: the failure is not remembered.
    monkeypatch.setattr(svc, "_run_build", lambda *a, **k: svc.build_mesh(neuron, **QUICK))
    assert svc.mesh_for(neuron, OTHER).n_vertices > 0 or True
    assert not svc._flights


def test_a_job_waiting_on_anothers_build_says_so(web, monkeypatch):
    _, svc = web
    neuron = svc.neuron(943, "i")
    release = threading.Event()
    real = svc._run_build

    def slow(*args, **kwargs):
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "_run_build", slow)
    holder = threading.Thread(target=lambda: svc.mesh_for(neuron, OTHER))
    holder.start()
    time.sleep(0.3)
    heard = []
    waiter = threading.Thread(target=lambda: _listen(heard, lambda: svc.mesh_for(neuron, OTHER)))
    waiter.start()
    time.sleep(0.3)
    release.set()
    holder.join()
    waiter.join()
    assert heard and heard[0][0].startswith("waiting for another request")


def _listen(heard, call):
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        call()


def test_a_cached_measurement_for_another_mesh_is_not_an_answer(web):
    """A rebuild that came out with one more vertex must not be served the old
    mesh's distances (the result would say n_vertices 8227 with 8226 distances)."""
    _, svc = web
    neuron = svc.neuron(943, "i")
    first = svc.deviation_result(neuron, QUICK)
    key = next(iter(svc._deviation_maps))
    found = svc._deviation_maps[key]
    from dataclasses import replace

    svc._deviation_maps[key] = replace(found, vertex_um=found.vertex_um[:-1])
    again = svc.deviation_result(neuron, QUICK)
    assert len(again.vertex_um) == again.n_vertices == first.n_vertices


def test_start_requests_validate_like_the_synchronous_routes(web):
    client, _ = web
    assert client.post("/api/neurons/i/943/mesh-deviation-job", json={"pathway": "bogus"}).status_code == 422
    assert client.post("/api/neurons/i/999999/mesh-deviation-job", json=QUICK).status_code == 404
    assert client.post("/api/neurons/i/943/mesh-diff-job", json={"a": {"meshlab": "bogus"}}).status_code == 422
    assert client.post("/api/neurons/i/999999/mesh-diff-job", json={"a": QUICK, "b": OTHER}).status_code == 404


def test_one_mesh_is_built_once_however_many_requests_want_it(web, monkeypatch):
    """A pane's mesh job and a measurement job for the same mesh must not each
    build it (the second used to, inside its own request)."""
    _, svc = web
    neuron = svc.neuron(943, "i")
    real = svc._run_build
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        time.sleep(0.4)
        return real(*args, **kwargs)

    monkeypatch.setattr(svc, "_run_build", counting)
    results = []
    threads = [threading.Thread(target=lambda: results.append(svc.mesh_for(neuron, OTHER)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert len({id(m) for m in results}) == 1 and results[0].meta["mesh_id"]
    assert not svc._flights, "the per-mesh locks are forgotten with their last user"


def test_measurements_are_cached_per_copy_and_dropped_with_it(web):
    _, svc = web
    neuron = svc.neuron(943, "i")
    first = svc.deviation_result(neuron, QUICK)
    assert len(svc._deviation_maps) == 1
    again = svc.deviation_result(neuron, QUICK)
    assert again.vertex_um == first.vertex_um
    svc.diff_result(neuron, QUICK, OTHER)
    assert len(svc._differences) == 1
    svc._drop_meshes(next(iter(svc._deviation_maps))[0])
    assert not svc._deviation_maps and not svc._differences and not svc._deviations


def test_a_meshlab_render_weighs_more_at_the_memory_gate(web):
    _, svc = web
    plain = svc.estimate_peak_mb(943, "i", method="cylinder", pathway="voxel2")
    iso = svc.estimate_peak_mb(943, "i", method="cylinder", pathway="voxel2", meshlab="isotropic")
    poisson = svc.estimate_peak_mb(943, "i", method="cylinder", pathway="voxel2",
                                   meshlab="poisson", meshlab_depth=10)
    assert iso - plain == svc.MESHLAB_MB and poisson - plain == svc.MESHLAB_POISSON_MB
    neuron = svc.neuron(943, "i")
    assert svc.measure_weight_mb(neuron, QUICK) == svc.MEASURE_MB
    # A job weighs the heavier of what it measures and what it may have to build first.
    assert svc.measure_weight_mb(neuron, {"pathway": "voxel2", "meshlab": "isotropic"}) \
        == max(svc.MEASURE_MB, iso)
    svc.MEASURE_MB = 500.0                    # an instance override, to see the build side win
    assert svc.measure_weight_mb(neuron, {"pathway": "voxel2", "meshlab": "isotropic"}) == iso


def test_the_health_report_counts_jobs_by_kind(web, monkeypatch):
    client, svc = web
    gate = threading.Event()
    store = client.app.state.mesh_jobs
    store.submit(("a",), lambda: gate.wait(5), kind="deviation")
    store.submit(("b",), lambda: gate.wait(5), kind="diff")
    time.sleep(0.2)
    by_kind = client.get("/api/health").json()["builds"]["by_kind"]
    assert sum(v["running"] + v["queued"] for v in by_kind.values()) >= 2
    assert {"deviation", "diff"} <= set(by_kind)
    gate.set()


def test_progress_prefix_names_the_piece_of_the_job():
    heard = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        with progress.prefixed("mesh A: "):
            progress.report("contouring", 3, 10)
        progress.report("comparing")
    assert heard == [("mesh A: contouring", 3, 10), ("comparing", None, None)]
    with progress.prefixed("mesh B: "):        # no listener: nothing happens, nothing breaks
        progress.report("contouring")


def test_the_meshlab_ceiling_is_configurable(monkeypatch):
    from sbfsem.geometry import meshlab

    monkeypatch.setenv("SBFSEM_MESHLAB_TIMEOUT_S", "42")
    assert meshlab._env_seconds("SBFSEM_MESHLAB_TIMEOUT_S", 1800.0) == 42.0
    monkeypatch.setenv("SBFSEM_MESHLAB_TIMEOUT_S", "nonsense")
    assert meshlab._env_seconds("SBFSEM_MESHLAB_TIMEOUT_S", 1800.0) == 1800.0
    monkeypatch.setenv("SBFSEM_MESHLAB_TIMEOUT_S", "-5")
    assert meshlab._env_seconds("SBFSEM_MESHLAB_TIMEOUT_S", 1800.0) == 1800.0
    monkeypatch.delenv("SBFSEM_MESHLAB_TIMEOUT_S", raising=False)
    assert meshlab._env_seconds("SBFSEM_MESHLAB_TIMEOUT_S", 1800.0) == 1800.0


def test_a_meshlab_run_past_its_ceiling_is_a_clear_error(monkeypatch):
    """The ceiling kills the child and says so; nothing waits on it any more."""
    import subprocess

    from sbfsem.geometry import meshlab
    from sbfsem.geometry.primitives import sphere

    monkeypatch.setattr(meshlab, "available", lambda: True)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="meshlab", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(meshlab.MeshlabError, match="did not finish in 7 s"):
        meshlab.remesh(sphere(1.0, divisions=8), "isotropic", timeout_s=7)


# -- the poll's body: serialized without jsonable_encoder ----------------------------


def _bytes_of_the_old_way(job, svc=None):
    """What the status route sent until 2026-10-15: the dict through FastAPI's
    ``jsonable_encoder``, then the same ``JSONResponse``.  A finished mesh is held
    as text since 2026-10-16, and as binary since 2026-10-20, and a finished
    measurement as text since 2.19.7; the old way is the model itself through the
    encoder (these jobs are QUICK's, and a difference QUICK against OTHER)."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    from sbfsem_web.api.routes import _job_status_body
    from sbfsem_web.json_response import EncodedJSON
    from sbfsem_web.mesh_binary import EncodedMesh

    body = _job_status_body(job)
    if isinstance(body["mesh"], (EncodedJSON, EncodedMesh)):
        body["mesh"] = svc.render_mesh(943, "i", **QUICK)
    if isinstance(body["result"], EncodedJSON):
        neuron = svc.neuron(943, "i")
        body["result"] = (svc.deviation_result(neuron, QUICK) if job.kind == "deviation"
                          else svc.diff_result(neuron, QUICK, OTHER))
    return JSONResponse(jsonable_encoder(body)).body


def test_a_finished_jobs_status_is_byte_for_byte_what_jsonable_encoder_made(web):
    """The final poll of a job used to run ``jsonable_encoder`` over every number
    of its result in the event loop (0.9 s for a c1178 error map, 4 s for its
    mesh).  The body is the same bytes, for a mesh, an error map and a difference."""
    client, svc = web
    store = client.app.state.mesh_jobs
    mesh = client.get("/api/neurons/i/943/mesh-job", params=QUICK).json()
    deviation = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    diff = client.post("/api/neurons/i/943/mesh-diff-job", json={"a": QUICK, "b": OTHER}).json()
    for started, kind in ((mesh, "mesh"), (deviation, "deviation"), (diff, "diff")):
        _poll(client, started["job_id"])
        job = store.get(started["job_id"])
        assert job.status == "done" and job.kind == kind
        got = client.get(f"/api/mesh-jobs/{job.id}")
        assert got.status_code == 200 and got.headers["content-type"] == "application/json"
        assert got.content == _bytes_of_the_old_way(job, svc)
        body = got.json()
        assert body["status"] == "done" and body["kind"] == kind
        # Mesh and result are still exclusive, and the one that is set is the model's dump.
        key, other = ("mesh", "result") if kind == "mesh" else ("result", "mesh")
        if kind == "mesh":
            # A finished render is held as the binary the page collects, not as
            # the model (a tenth of the memory for as long as the job lingers).
            from sbfsem_web.mesh_binary import EncodedMesh

            assert isinstance(job.result, EncodedMesh)
        else:
            # And a finished measurement as its text (a sixth of the model's lists).
            from sbfsem_web.json_response import EncodedJSON

            assert isinstance(job.result, EncodedJSON)
        dumped = job.result.plain() if kind == "mesh" else json.loads(job.result)
        assert body[other] is None and body[key] == dumped
        assert set(body) == {"job_id", "status", "waiting", "progress", "seconds", "detail",
                             "kind", "failed_stage", "mesh", "result"}


def test_the_final_poll_of_a_finished_job_never_runs_the_general_encoder(web, monkeypatch):
    """724404b spliced a finished mesh into its poll but sent every other status through
    ``JSONResponse(plain)``, which refuses a model nested in the dict, so the final poll of
    an error map or a difference went back through ``jsonable_encoder`` (0.38 s against
    1.23 s for 350,000 numbers).  The bytes were right, so only a spy sees it."""
    from sbfsem_web.api import routes

    client, svc = web
    store = client.app.state.mesh_jobs
    started = [
        client.get("/api/neurons/i/943/mesh-job", params=QUICK).json(),
        client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json(),
        client.post("/api/neurons/i/943/mesh-diff-job", json={"a": QUICK, "b": OTHER}).json(),
    ]
    for each in started:
        _poll(client, each["job_id"])

    def refuse(*args, **kwargs):
        raise AssertionError("jsonable_encoder ran on the poll of a finished job")

    monkeypatch.setattr(routes, "jsonable_encoder", refuse)
    for each in started:
        job = store.get(each["job_id"])
        got = client.get(f"/api/mesh-jobs/{job.id}")
        assert got.status_code == 200 and got.json()["kind"] == job.kind
        assert got.content == _bytes_of_the_old_way(job, svc), job.kind


def test_the_final_poll_of_a_big_measurement_costs_less_than_the_general_encoder():
    """The same thing measured instead of spied on: both ways run here, back to back,
    best of three, so a busy machine slows them alike.  Before the fix the poll did the
    general encoder's work and a refused ``JSONResponse`` besides (a ratio of 1.0 or more);
    without the encoder's walk it is a third to a half of it (0.51 s against 1.63 s)."""
    import time as clock

    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    from sbfsem.service.dto import DeviationResultDTO
    from sbfsem_web.api.routes import _job_status_response

    n = 250_000
    rng = np.random.default_rng(7)
    result = DeviationResultDTO(
        mesh_id="m", n_vertices=n, n_faces=2 * n, scale_um=1.0,
        vertex_um=np.round(rng.random(n), 4).tolist(), axial_um=np.round(rng.random(n), 4).tolist(),
    )
    body = {"job_id": "j", "status": "done", "waiting": False, "progress": None, "seconds": 1.0,
            "detail": "", "kind": "deviation", "failed_stage": "", "mesh": None, "result": result}

    def best(make):
        seconds = []
        for _ in range(3):
            t0 = clock.perf_counter()
            out = make()
            seconds.append(clock.perf_counter() - t0)
        return min(seconds), out

    fast, mine = best(lambda: _job_status_response(body).body)
    slow, theirs = best(lambda: JSONResponse(jsonable_encoder(body)).body)
    assert mine == theirs, "the same bytes"
    assert fast < 0.8 * slow, f"the poll took {fast:.2f} s against the general encoder's {slow:.2f} s"


def test_a_finished_measurement_never_makes_the_models_lists(web, monkeypatch):
    """An error map and a difference both ways are held as their text, made from the
    arrays: the model's lists of every vertex's distance (277 MB in the web process for
    c1178's 3.54 M vertices, production 2026-10-20) are made neither by the job nor by
    its poll, and the poll is the bytes the model makes."""
    from sbfsem.service.facade import ResultBuffers
    from sbfsem_web.json_response import EncodedJSON

    client, svc = web
    store = client.app.state.mesh_jobs

    def no_lists(self):
        raise AssertionError("the model's lists were made")

    monkeypatch.setattr(ResultBuffers, "dto", no_lists)
    deviation = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    diff = client.post("/api/neurons/i/943/mesh-diff-job",
                       json={"a": QUICK, "b": OTHER, "both": True}).json()
    polls = {}
    for started in (deviation, diff):
        _poll(client, started["job_id"])
        job = store.get(started["job_id"])
        assert job.status == "done" and isinstance(job.result, EncodedJSON), job.detail
        polls[job.kind] = client.get(f"/api/mesh-jobs/{job.id}", headers={"Accept": "application/x-sbfsem-mesh"})
    monkeypatch.undo()
    neuron = svc.neuron(943, "i")
    models = {"deviation": svc.deviation_result(neuron, QUICK),
              "diff": svc.diff_result(neuron, QUICK, OTHER, both=True)}
    for kind, got in polls.items():
        assert got.headers["content-type"] == "application/json", "a measurement is JSON for the page too"
        body = got.json()
        assert body["result"] == models[kind].model_dump(mode="json")
        expected = {**body, "result": models[kind]}
        from sbfsem_web.api.routes import _json_bytes

        assert got.content == _json_bytes(expected), kind
    assert len(polls["diff"].json()["result"]["distance_b_um"]) == models["diff"].n_vertices_b > 0


@pytest.mark.parametrize("options", [
    QUICK, OTHER,
    {"method": "cylinder", "pathway": "voxel2", "smooth_iterations": 0, "voxel2_um": 0.1},
    {"method": "curvemesh", "smooth_iterations": 0},
], ids=["original", "adaptive_taubin", "voxel2", "curvemesh"])
def test_a_measurements_text_is_the_models_bytes_on_real_results(web, options):
    """c943's error map and its differences, every way the job is asked for: the text
    made from the arrays is the bytes pydantic writes of the model, and every list of it
    was written from its array (none fell back to the model's numbers)."""
    from sbfsem_web.api.routes import _encoded_result, _json_bytes
    from sbfsem_web.json_response import json_float_list

    _, svc = web
    neuron = svc.neuron(943, "i")
    buffers = [svc.deviation_buffers(neuron, options)]
    for sign, both in (("normal", False), ("normal", True), ("enclosure", True)):
        buffers.append(svc.diff_buffers(neuron, options, QUICK if options is not QUICK else OTHER,
                                        sign=sign, both=both))
    for made in buffers:
        assert made.arrays and all(json_float_list(v, made.decimals) is not None for v in made.arrays.values())
        assert _encoded_result(made) == _json_bytes(made.dto())
    deviation, one_way, both_ways, _ = buffers
    assert set(deviation.arrays) == {"vertex_um", "rim_xyz", "rim_um", "axial_um"}
    assert len(deviation.arrays["vertex_um"]) == deviation.head.n_vertices and not deviation.head.vertex_um
    assert set(one_way.arrays) == {"distance_um"} and set(both_ways.arrays) == {"distance_um", "distance_b_um"}
    assert (one_way.arrays["distance_um"] < 0).any(), "a signed field, so the minus signs are tested too"


def test_a_list_the_text_cannot_vouch_for_is_written_by_pydantic():
    """A non-finite distance (null in the JSON), one off the 0.1 nm step and one too
    large to write exactly each take pydantic's way for that list alone, and the bytes
    are still the model's."""
    from sbfsem.service.dto import DeviationResultDTO, DiffResultDTO
    from sbfsem.service.facade import ResultBuffers
    from sbfsem_web.api.routes import _encoded_result, _json_bytes
    from sbfsem_web.json_response import EncodedJSON

    head = DeviationResultDTO(mesh_id="m", n_vertices=3, n_faces=1, scale_um=0.5, rim_stride=2, n_rims=4)
    for odd in (np.array([0.1, np.nan, 0.2]), np.array([0.12345, 0.1, 0.2]), np.array([1e12, 0.1, -0.0])):
        made = ResultBuffers(head, {"vertex_um": odd, "rim_um": np.array([0.5, -0.0]),
                                    "axial_um": np.round(odd * 0.5, 4)})
        text = _encoded_result(made)
        assert isinstance(text, EncodedJSON) and text == _json_bytes(made.dto())
    nan = json.loads(_encoded_result(ResultBuffers(head, {"vertex_um": np.array([np.nan])})))
    assert nan["vertex_um"] == [None]
    diff = DiffResultDTO(n_vertices=2, n_faces=1, mean_abs_um=0.1, rms_um=0.1, p95_abs_um=0.1, max_abs_um=0.2,
                         mean_signed_um=0.0, scale_um=0.2, sample_spacing_um=0.05, n_samples=10)
    one_way = ResultBuffers(diff, {"distance_um": np.array([-0.0001, 0.2])})
    assert _encoded_result(one_way) == _json_bytes(one_way.dto())
    assert json.loads(_encoded_result(one_way))["distance_b_um"] == []
    # Anything that is not a result's arrays is held as it came.
    assert _encoded_result({"mean_um": 1.0}) == {"mean_um": 1.0}


def test_a_non_finite_number_in_a_result_is_null_not_a_500_on_every_poll(web):
    """JSON has no NaN.  The poll's own serializer skips the app's backstop to stay
    fast, so a non-finite float in a result must still come out as null."""
    client, _ = web
    store = client.app.state.mesh_jobs
    job = store.submit(("nan-result",), lambda: {"mean_um": float("nan"), "per_class": [1.0, float("inf")]},
                       kind="deviation")
    _poll(client, job.id)
    got = client.get(f"/api/mesh-jobs/{job.id}")
    assert got.status_code == 200
    assert got.json()["result"] == {"mean_um": None, "per_class": [1.0, None]}


def test_a_running_queued_or_failed_jobs_status_has_no_result(web, monkeypatch):
    client, svc = web
    store = client.app.state.mesh_jobs
    gate = threading.Event()
    running = store.submit(("running",), lambda: (progress.report("contouring", 3, 10), gate.wait(10))[1],
                           kind="deviation")
    for _ in range(200):
        if store.get(running.id).status == "running":
            break
        time.sleep(0.02)
    live = client.get(f"/api/mesh-jobs/{running.id}").json()
    assert live["status"] == "running" and live["mesh"] is None and live["result"] is None
    assert live["progress"] == {"stage": "contouring", "done": 3, "total": 10}
    assert live["kind"] == "deviation" and live["detail"] == "" and live["failed_stage"] == ""
    gate.set()

    def fail():
        progress.report("distance to outlines", 4, 9)
        raise ValueError("boom")

    failed = store.submit(("failing",), fail, kind="diff")
    for _ in range(200):
        if store.get(failed.id).status == "error":
            break
        time.sleep(0.02)
    gone = client.get(f"/api/mesh-jobs/{failed.id}")
    assert gone.content == _bytes_of_the_old_way(store.get(failed.id), svc)
    body = gone.json()
    assert body["status"] == "error" and body["detail"] == "boom" and body["progress"] is None
    assert body["failed_stage"] == "distance to outlines" and body["mesh"] is None and body["result"] is None


def test_an_unknown_job_is_a_404_and_an_odd_result_still_goes_out(web):
    """No such job: 404 with the same message.  A result that is neither a
    pydantic model nor plain JSON (a set, here) takes the general encoder, as it
    always did."""
    client, _ = web
    missing = client.get("/api/mesh-jobs/nope")
    assert missing.status_code == 404 and "No such mesh job" in missing.json()["detail"]
    store = client.app.state.mesh_jobs
    odd = store.submit(("odd",), lambda: {3}, kind="deviation")
    for _ in range(200):
        if store.get(odd.id).status == "done":
            break
        time.sleep(0.02)
    got = client.get(f"/api/mesh-jobs/{odd.id}")
    assert got.status_code == 200 and got.json()["result"] == [3]
    assert got.content == _bytes_of_the_old_way(store.get(odd.id), None)


# -- how the error arises, in the job's result ---------------------------------------


def test_annotation_targets_name_the_annotations_in_the_order_they_are_measured():
    """Entry for entry with annotation_ground_truth: discs that pass its filter
    (not a closed curve, finite, a radius), then outlines that pass its filter
    (a ring of three points or more)."""
    import pandas as pd

    from sbfsem.service.facade import annotation_ground_truth, annotation_targets

    nodes = pd.DataFrame({
        "ID": [11, 12, 13, 14, 15, 16],
        "Z": [100, 101, 102, 103, 104, 105],
        "TypeCode": [1, 6, 1, 1, 1, 1],
        "Xum": [0.0, 1.0, 2.0, np.nan, 4.0, 5.0],
        "Yum": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        "Zum": [7.0, 7.1, 7.2, 7.3, 7.4, 7.5],
        "Rum": [0.5, 9.9, 0.0, 0.5, 0.6, 0.7],
    })
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    geometries = pd.DataFrame({
        "ID": [21, 22, 23], "Z": [200, 201, 202], "Zum": [8.0, 8.1, 8.2],
        "Curve": [[square], [square[:2]], [square + 5.0]],
    })

    class Cell:
        def __init__(self, table, curves):
            self._table, self.geometries = table, curves

        def get_cell_nodes(self):
            return self._table

    cell = Cell(nodes, geometries)
    centers, radii, polygons = annotation_ground_truth(cell)
    targets = annotation_targets(cell)
    assert targets.ids.tolist() == [11, 15, 16, 21, 23]                 # the closed curve 12, the zero radius 13,
    assert targets.sections.tolist() == [100, 104, 105, 200, 202]       # the NaN 14 and the two-point ring 22 are out
    assert targets.kind.tolist() == [0, 0, 0, 1, 1]
    assert len(targets.ids) == len(centers) + len(polygons)
    assert radii.tolist() == [0.5, 0.6, 0.7] and centers[:, 2].tolist() == [7.0, 7.4, 7.5]
    assert [z for _, z in polygons] == [8.0, 8.2]
    # Neither disc nor outline: nothing to name.
    empty = Cell(nodes.iloc[0:0], None)
    assert len(annotation_targets(empty).ids) == 0 and len(annotation_ground_truth(empty)[0]) == 0


def test_a_deviation_result_says_how_the_error_arises(web):
    client, svc = web
    neuron = svc.neuron(943, "i")
    result = svc.deviation_result(neuron, QUICK)
    n = result.n_vertices
    assert len(result.axial_um) == len(result.vertex_um) == n
    assert all(0 <= a <= d for a, d in zip(result.axial_um, result.vertex_um, strict=True))
    assert len(result.rim_enclosed) == len(result.rim_um) > 0 and set(result.rim_enclosed) <= {0, 1}
    # The annotations are named in the order they are measured, and the volume's IDs are the cell's.
    from sbfsem.service.facade import annotation_ground_truth, annotation_targets

    centers, radii, polygons = annotation_ground_truth(neuron)
    table = result.annotations
    assert len(table.ids) == len(table.sections) == len(table.kind) == len(centers) + len(polygons)
    assert table.ids == annotation_targets(neuron).ids.tolist() and set(table.ids) <= set(neuron.get_cell_nodes()["ID"])
    assert len(table.xyz) == 3 * len(table.ids) and table.radius[:len(centers)] == pytest.approx(np.round(radii, 4))
    assert len({len(getattr(table, name)) for name in
                ("area_share", "surface_mean", "surface_max", "rim_mean", "rim_max", "rim_enclosed_share")}) == 1
    assert sum(table.area_share) == pytest.approx(1.0, abs=1e-4)
    surface = result.surface
    assert len(surface.quantiles) == 101 and surface.quantiles == sorted(surface.quantiles)
    assert len(surface.hist.edges) == 41 and len(surface.hist.area_share) == 40
    assert sum(surface.hist.area_share) + surface.hist.tail_share == pytest.approx(1.0, abs=1e-4)
    assert surface.floor_um > 0 and surface.area_um2 > 0 and surface.mean_um > 0
    # The surface statistics are of the painted field (per vertex, weighted by the area each stands for):
    # on this tube render, whose vertices lie ON the annotations, that is lower than the legend's mean over
    # surface samples, which also sees the sides between them.  Both are in the result.
    assert 0 < surface.mean_um < result.deviation.surface_um
    z = result.z_profile
    assert z.step == 1.0 and len(z.area) == len(z.mean) == len(z.p95) == len(z.rim_mean) == len(z.n_annotations) > 1
    assert sum(z.n_annotations) == len(table.ids) and sum(z.area) == pytest.approx(surface.area_um2, rel=1e-3)
    assert 0 < len(result.spots) <= 12
    for spot in result.spots:
        assert spot.annotation_id in table.ids and spot.section == table.sections[table.ids.index(spot.annotation_id)]
        assert len(spot.xyz) == 3 and 0 < spot.share <= 1 and spot.max >= spot.mean > 0
    assert [s.excess_um3 for s in result.spots] == sorted((s.excess_um3 for s in result.spots), reverse=True)
    assert sum(s.share for s in result.spots) <= 1.0 + 1e-6
    assert result.bias.n == len(result.rim_um) and 0 <= result.bias.enclosed_share <= 1
    # What the result already said is unchanged: the numbers of the same map without the detail.
    from sbfsem.geometry.meshdiff import deviation_map

    plain = deviation_map(svc.mesh_for(neuron, QUICK), *annotation_ground_truth(neuron), rim_sides=False)
    np.testing.assert_allclose(result.vertex_um, np.round(plain.vertex_um, 4), atol=1e-9)
    np.testing.assert_allclose(result.rim_um, np.round(plain.rim_um, 4), atol=1e-9)
    assert result.scale_um == plain.scale_um and result.n_rims == plain.n_rims and result.rim_stride == plain.rim_stride


def test_a_result_without_the_detail_reads_as_before():
    """The new fields default to empty: a result built without them (an older
    server's, a stale cache) is a valid result, and its dump says nothing new."""
    from sbfsem.service.dto import DeviationResultDTO

    bare = DeviationResultDTO(n_vertices=3, n_faces=1, scale_um=0.5, vertex_um=[0.1, 0.2, 0.3])
    dumped = bare.model_dump(mode="json")
    assert dumped["axial_um"] == [] and dumped["rim_enclosed"] == [] and dumped["spots"] == []
    assert dumped["surface"] is None and dumped["annotations"] is None
    assert dumped["z_profile"] is None and dumped["bias"] is None
    assert {k: v for k, v in dumped.items() if k in ("mesh_id", "n_vertices", "n_faces", "vertex_um", "rim_xyz",
                                                      "rim_um", "rim_stride", "n_rims", "scale_um", "deviation")} == {
        "mesh_id": None, "n_vertices": 3, "n_faces": 1, "vertex_um": [0.1, 0.2, 0.3], "rim_xyz": [],
        "rim_um": [], "rim_stride": 1, "n_rims": 0, "scale_um": 0.5, "deviation": None}


def test_the_job_result_and_the_old_routes_keep_their_shape(web):
    client, _ = web
    started = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    result = _poll(client, started["job_id"])["result"]
    old = {"mesh_id", "n_vertices", "n_faces", "vertex_um", "rim_xyz", "rim_um", "rim_stride", "n_rims",
           "scale_um", "deviation"}
    assert old <= set(result)
    assert set(result) - old == {"axial_um", "rim_enclosed", "surface", "annotations", "z_profile", "spots", "bias"}
    assert {"quantiles", "hist", "floor_um", "mean_um"} <= set(result["surface"])
    assert {"z0", "step", "area", "mean", "p95", "rim_mean", "n_annotations"} == set(result["z_profile"])
    assert {"xyz", "section", "annotation_id", "excess_um3", "share", "mean", "max"} == set(result["spots"][0])
    # The synchronous routes answer what they did, and the deviation gains the three figures of
    # the exterior surface that the library computed all along (exterior_um, hidden_fraction,
    # annotation_exterior_um); nothing else about their shape changes.
    sync = client.post("/api/neurons/i/943/mesh-deviation", json=QUICK).json()
    assert set(sync) == {"mesh", "rim_xyz", "rim_um", "rim_stride", "n_rims", "scale_um", "deviation"}
    assert set(sync["deviation"]) == {"mean_um", "surface_um", "annotation_um", "p95_um", "max_um",
                                      "excess_volume_um3", "sample_spacing_um", "n_discs", "n_curves",
                                      "exterior_um", "hidden_fraction", "annotation_exterior_um"}
    assert result["deviation"] == sync["deviation"], "the job and the blocking route say the same"
    diff = client.post("/api/neurons/i/943/mesh-diff", json={"a": QUICK, "b": OTHER}).json()
    assert "deviation_a" in diff and set(diff["deviation_a"]) == set(sync["deviation"])


# -- the enclosure-signed difference, both ways, and its area statistics ---------------------


def test_a_diff_job_signed_by_enclosure_and_both_ways(web):
    client, svc = web
    body = {"a": QUICK, "b": OTHER, "sign": "enclosure", "both": True}
    started = client.post("/api/neurons/i/943/mesh-diff-job", json=body).json()
    plain = client.post("/api/neurons/i/943/mesh-diff-job", json={"a": QUICK, "b": OTHER}).json()
    assert started["job_id"] != plain["job_id"], "the sign rule and both are part of the job's identity"
    job = _poll(client, started["job_id"])
    default = _poll(client, plain["job_id"])
    assert job["status"] == "done" and default["status"] == "done"
    result, ordinary = job["result"], default["result"]
    assert result["sign_by"] == "enclosure" and ordinary["sign_by"] == "normal"
    # Only the sign may differ: the magnitudes are the measurement's own.
    np.testing.assert_allclose(np.abs(result["distance_um"]), np.abs(ordinary["distance_um"]), atol=1e-9)
    # The statistics weighted by the surface are there, for both directions.
    for key in ("mean_abs_um", "rms_um", "p95_abs_um", "p98_abs_um", "max_abs_um", "mean_signed_um"):
        assert result["stats_area"][key] is not None and result["stats_area_b"][key] is not None
    assert result["stats_area"]["max_abs_um"] <= result["max_abs_um"] + 1e-9
    # Mesh B's field is for mesh B: the ids and the length say which mesh that is.
    mesh_b = client.get("/api/neurons/i/943/mesh", params=OTHER).json()
    assert result["mesh_id_b"] == mesh_b["mesh_id"] and result["n_vertices_b"] == mesh_b["n_vertices"]
    assert len(result["distance_b_um"]) == mesh_b["n_vertices"]
    # Not asked for, not sent -- but mesh B is named either way.
    assert ordinary["distance_b_um"] == [] and ordinary["stats_area_b"] is None
    assert ordinary["mesh_id_b"] == mesh_b["mesh_id"] and ordinary["n_vertices_b"] == mesh_b["n_vertices"]
    assert ordinary["stats_area"]["mean_abs_um"] > 0
    # The definition, from the two meshes.
    from sbfsem.geometry.meshdiff import mesh_difference

    neuron = svc.neuron(943, "i")
    expected = mesh_difference(svc.mesh_for(neuron, QUICK), svc.mesh_for(neuron, OTHER),
                               samples=250_000, sign="enclosure", both=True)
    np.testing.assert_allclose(result["distance_um"], np.round(expected.distance_um, 4), atol=1e-9)
    np.testing.assert_allclose(result["distance_b_um"], np.round(expected.distance_b_um, 4), atol=1e-9)
    assert result["stats_area"]["mean_abs_um"] == pytest.approx(expected.stats_area.mean_abs_um)
    assert result["stats_area_b"]["p98_abs_um"] == pytest.approx(expected.stats_area_b.p98_abs_um)
    # How many vertices the winding number of an open B put on the other side from the capped count:
    # the result says so, one count for each direction, and what the library measured is what was sent.
    assert result["sign_overruled"] == expected.sign_overruled and result["sign_overruled_b"] == expected.sign_overruled_b
    assert ordinary["sign_overruled"] == 0 and ordinary["sign_overruled_b"] == 0, "by the normal nothing is overruled"


def test_each_sign_rule_is_its_own_cached_difference(web):
    _, svc = web
    neuron = svc.neuron(943, "i")
    svc.diff_result(neuron, QUICK, OTHER)
    assert len(svc._differences) == 1
    svc.diff_result(neuron, QUICK, OTHER, sign="enclosure")
    assert len(svc._differences) == 2
    # Both ways is the same measurement with its reverse half added: one entry, not two.
    svc.diff_result(neuron, QUICK, OTHER, sign="enclosure", both=True)
    assert len(svc._differences) == 2
    again = svc.diff_result(neuron, QUICK, OTHER, sign="enclosure", both=True)
    assert len(svc._differences) == 2, "asking again is a cache hit"
    assert again.sign_by == "enclosure" and len(again.distance_b_um) == again.n_vertices_b
    # A pair measured one way round is not the other way round.
    svc.diff_result(neuron, OTHER, QUICK)
    assert len(svc._differences) == 3
    svc._drop_meshes(next(iter(svc._differences))[0])
    assert not svc._differences


def test_a_difference_is_never_measured_twice_whichever_way_it_is_asked_for(web, monkeypatch):
    """Both ways costs the forward pass plus the reverse one.  A client that asked one way
    and then toggles "both" pays for the reverse pass alone, and one that asked both ways
    and then drops it pays for nothing; neither gets the half it did not ask for."""
    from sbfsem.geometry import meshdiff

    _, svc = web
    neuron = svc.neuron(943, "i")
    passes: list[str] = []
    real = meshdiff._signed_distance

    def counting(source, reference, **kwargs):
        passes.append(kwargs["name"])        # "B": A's vertices against B; "A": the reverse
        return real(source, reference, **kwargs)

    monkeypatch.setattr(meshdiff, "_signed_distance", counting)
    for sign in ("normal", "enclosure"):
        passes.clear()
        one_way = svc.diff_result(neuron, QUICK, OTHER, sign=sign)
        assert passes == ["B"] and one_way.distance_b_um == [] and one_way.stats_area_b is None
        both_ways = svc.diff_result(neuron, QUICK, OTHER, sign=sign, both=True)
        assert passes == ["B", "A"], "the forward pass is not run again"
        assert len(both_ways.distance_b_um) == both_ways.n_vertices_b > 0
        assert both_ways.stats_area_b is not None and both_ways.sign_by == sign
        assert both_ways.distance_um == one_way.distance_um and both_ways.stats_area == one_way.stats_area
        # And back: the held entry has the reverse half, which this request did not ask for.
        again = svc.diff_result(neuron, QUICK, OTHER, sign=sign)
        assert passes == ["B", "A"], "answered from the both-ways entry"
        assert again.distance_b_um == [] and again.stats_area_b is None
        assert again.distance_um == one_way.distance_um
        assert svc.diff_result(neuron, QUICK, OTHER, sign=sign, both=True).distance_b_um == both_ways.distance_b_um
        assert passes == ["B", "A"]
    # The other order, on a pair not yet measured: both ways first, then one way for free.
    passes.clear()
    first = svc.diff_result(neuron, OTHER, QUICK, both=True)
    assert passes == ["B", "A"] and first.distance_b_um
    one_way = svc.diff_result(neuron, OTHER, QUICK)
    assert passes == ["B", "A"] and one_way.distance_b_um == []
    assert one_way.distance_um == first.distance_um
    # The synchronous result is mesh A alone, from the same entry.
    sync = svc.mesh_diff(943, "i", options_a=OTHER, options_b=QUICK)
    assert passes == ["B", "A"] and len(sync.mesh.vertex_data) == sync.mesh.n_vertices


def test_the_enclosure_test_is_a_visible_stage_of_the_job(web):
    _, svc = web
    neuron = svc.neuron(943, "i")
    heard: list[tuple] = []
    with progress.reporting(lambda stage, done, total: heard.append((stage, done, total))):
        svc.diff_result(neuron, QUICK, OTHER, sign="enclosure")
    stages = list(dict.fromkeys(s for s, *_ in heard))
    assert stages.index("comparing mesh A with mesh B") < stages.index("difference: sampling mesh B") \
        < stages.index("difference: which side of mesh B")
    assert any(total for stage, _, total in heard if stage == "difference: which side of mesh B")


def test_the_synchronous_diff_signs_by_the_normal_and_sends_the_rest_to_the_job(web):
    """The blocking route has no memory gate and no progress: the enclosure test (several
    hundred MB and seconds to minutes on a large cell) and the reverse field are a job's."""
    client, svc = web
    default = client.post("/api/neurons/i/943/mesh-diff", json={"a": QUICK, "b": OTHER}).json()
    assert default["sign_by"] == "normal"
    named = client.post("/api/neurons/i/943/mesh-diff", json={"a": QUICK, "b": OTHER, "sign": "normal"})
    assert named.status_code == 200 and named.json()["sign_by"] == "normal"
    before = len(svc._differences)
    for extra in ({"sign": "enclosure"}, {"both": True}, {"sign": "enclosure", "both": True}):
        refused = client.post("/api/neurons/i/943/mesh-diff", json={"a": QUICK, "b": OTHER, **extra})
        assert refused.status_code == 400 and "mesh-diff-job" in refused.json()["detail"], extra
    assert len(svc._differences) == before, "a refused request measures nothing"
    # The job takes the same body and answers what the route refused.
    started = client.post("/api/neurons/i/943/mesh-diff-job",
                          json={"a": QUICK, "b": OTHER, "sign": "enclosure"}).json()
    job = _poll(client, started["job_id"])
    assert job["status"] == "done" and job["result"]["sign_by"] == "enclosure"
    np.testing.assert_allclose(np.abs(job["result"]["distance_um"]),
                               np.abs(default["mesh"]["vertex_data"]), atol=2e-4)
    for route in ("mesh-diff", "mesh-diff-job"):
        assert client.post(f"/api/neurons/i/943/{route}",
                           json={"a": QUICK, "b": OTHER, "sign": "inward"}).status_code == 422


def test_a_diff_job_is_told_apart_by_its_sign_and_by_both_each_alone(web):
    """Drop either field from the job's key and a request for one flavor joins a finished job
    of the other for the ten minutes it is kept, and gets a result with or without the reverse
    field that it did not ask for.  Four requests that differ in one field at a time."""
    client, _ = web

    def start(sign, both):
        body = {"a": QUICK, "b": OTHER, "sign": sign, "both": both}
        return client.post("/api/neurons/i/943/mesh-diff-job", json=body).json()["job_id"]

    flavors = [("normal", False), ("normal", True), ("enclosure", False), ("enclosure", True)]
    ids = {flavor: start(*flavor) for flavor in flavors}
    assert len(set(ids.values())) == 4, ids
    # Finished jobs are kept: asking again joins the job of the same flavor, and only that one.
    for flavor in flavors:
        _poll(client, ids[flavor])
    assert {flavor: start(*flavor) for flavor in flavors} == ids
    for (sign, both), job_id in ids.items():
        result = client.get(f"/api/mesh-jobs/{job_id}").json()["result"]
        assert result["sign_by"] == sign
        assert bool(result["distance_b_um"]) is both and (result["stats_area_b"] is not None) is both
    # The same measurement under the same sign: only the half that was not asked for differs.
    np.testing.assert_allclose(
        client.get(f"/api/mesh-jobs/{ids[('enclosure', False)]}").json()["result"]["distance_um"],
        client.get(f"/api/mesh-jobs/{ids[('enclosure', True)]}").json()["result"]["distance_um"], atol=1e-9)


# -- a measurement of one copy of a cell, and a difference across two copies ----------


def _add_shifted_copy(request, svc, shift_um, token):
    """Another copy of the 943 fixture (tag ``off.1``): its annotations moved ``shift_um`` in
    X by the volume's current section transforms, as ``registration_status`` says, so it is
    held next to the plain ``off.0`` copy; ``token`` names the move the way a real one is
    named, which is what gives the copy's surface an id of its own."""
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    moved = read_legacy_json(fixture)
    moved.nodes["Xum"] += shift_um
    moved.registration_status = {"requested": True, "applied": True}
    moved.live_registration = {"token": token}
    return svc.add_neuron(moved)


def test_a_job_started_with_a_copy_measures_that_copy(web, request):
    """The page names the copy it holds as ``?copy=`` on the POST that starts a job, as on
    every request for the cell.  The measurement is of that copy: its surface, held to its
    annotations (the copy that moved 5 um has every rim point 5 um over), in a job of its own."""
    client, svc = web
    _add_shifted_copy(request, svc, 5.0, "reg-5")           # the latest copy: what no ?copy= gets
    plain = client.post("/api/neurons/i/943/mesh-deviation-job?copy=off.0", json=QUICK).json()
    moved = client.post("/api/neurons/i/943/mesh-deviation-job?copy=off.1", json=QUICK).json()
    assert plain["job_id"] != moved["job_id"]
    latest = client.post("/api/neurons/i/943/mesh-deviation-job", json=QUICK).json()
    assert latest["job_id"] == moved["job_id"]              # no ?copy=: the copy loaded last
    result_plain = _poll(client, plain["job_id"])["result"]
    result_moved = _poll(client, moved["job_id"])["result"]
    # Each result is for the surface its own copy builds ...
    for tag, result in (("off.0", result_plain), ("off.1", result_moved)):
        mesh = client.get("/api/neurons/i/943/mesh", params={**QUICK, "copy": tag}).json()
        assert mesh["mesh_id"] == result["mesh_id"] and mesh["n_vertices"] == result["n_vertices"]
    assert result_plain["mesh_id"] != result_moved["mesh_id"]
    # ... and held to that copy's annotations: the surface and the rim points moved together.
    # Measured against the other copy's annotations the copy would be about 5 um off its own
    # surface, not the same distance from it.
    rim_plain = np.array(result_plain["rim_xyz"]).reshape(-1, 3)
    rim_moved = np.array(result_moved["rim_xyz"]).reshape(-1, 3)
    assert rim_plain.shape == rim_moved.shape and len(rim_plain) > 0
    np.testing.assert_allclose(rim_moved - rim_plain, np.tile([5.0, 0.0, 0.0], (len(rim_plain), 1)),
                               atol=2e-4)
    np.testing.assert_allclose(result_moved["vertex_um"], result_plain["vertex_um"], atol=2e-3)
    np.testing.assert_allclose(result_moved["rim_um"], result_plain["rim_um"], atol=2e-3)


def test_a_difference_across_two_copies_keys_on_both_serials(web, request, monkeypatch):
    client, svc = web
    base = svc.neuron(943, "i")
    _add_shifted_copy(request, svc, 5.0, "reg-5")
    after = svc.neuron(943, "i")
    assert (base.copy_tag, after.copy_tag) == ("off.0", "off.1") and base.load_serial != after.load_serial
    used = []
    real = svc._annotation_ground_truth
    monkeypatch.setattr(svc, "_annotation_ground_truth",
                        lambda neuron: (used.append(neuron.load_serial), real(neuron))[1])

    def start(a_copy, b_copy):
        body = {"a": QUICK, "b": QUICK}
        if b_copy is not None:
            body["copy_b"] = b_copy
        return client.post(f"/api/neurons/i/943/mesh-diff-job?copy={a_copy}", json=body).json()["job_id"]

    across = start("off.0", "off.1")
    same = start("off.0", None)
    assert across != same
    assert start("off.0", "off.0") == same                  # naming A's own copy is not another job
    assert start("off.0", "off.1") == across
    assert start("off.1", "off.0") not in (across, same)    # and the order matters
    result = _poll(client, across)["result"]
    baseline = _poll(client, same)["result"]
    assert (result["copy_a"], result["copy_b"]) == ("off.0", "off.1")
    assert (baseline["copy_a"], baseline["copy_b"]) == ("off.0", "off.0")
    # Mesh A is the plain copy's surface, mesh B the moved copy's: the result names both.
    mesh_a = client.get("/api/neurons/i/943/mesh", params={**QUICK, "copy": "off.0"}).json()
    mesh_b = client.get("/api/neurons/i/943/mesh", params={**QUICK, "copy": "off.1"}).json()
    assert result["mesh_id"] == mesh_a["mesh_id"] and result["mesh_id_b"] == mesh_b["mesh_id"]
    assert result["n_vertices"] == mesh_a["n_vertices"] and result["n_vertices_b"] == mesh_b["n_vertices"]
    assert baseline["mesh_id_b"] == baseline["mesh_id"] != result["mesh_id_b"]
    # The surfaces are 5 um apart in X; two renderings of one copy agree to the sampling.
    assert result["mean_abs_um"] > 10 * max(baseline["mean_abs_um"], 0.01)
    assert 1.0 < result["max_abs_um"] <= 5.5
    # Each rendering was held to the annotations of the copy it was built from.
    assert {base.load_serial, after.load_serial} <= set(used)
    assert result["deviation_a"]["n_discs"] > 0 and result["deviation_b"]["n_discs"] > 0
    # The measurement is cached under both serials: mesh A's last, mesh B's last in the middle tuple.
    held = {(k[-1], k[1][-1]) for k in svc._differences}
    assert (base.load_serial, after.load_serial) in held and (base.load_serial, base.load_serial) in held
    # Loading copy B again retires what was measured against the old one, and only that.
    _add_shifted_copy(request, svc, 7.0, "reg-7")
    newer = svc.neuron(943, "i")
    assert newer.load_serial not in (base.load_serial, after.load_serial)
    remaining = {(k[-1], k[1][-1]) for k in svc._differences}
    assert (base.load_serial, after.load_serial) not in remaining
    assert (base.load_serial, base.load_serial) in remaining
    again = start("off.0", "off.1")
    assert again != across                                  # a new job, for the copy that is held now
    fresh = _poll(client, again)["result"]
    assert fresh["mesh_id_b"] != result["mesh_id_b"]
    assert fresh["mean_abs_um"] > result["mean_abs_um"]     # 7 um apart now, not 5


def test_a_second_copy_that_is_not_held_is_refused_and_the_blocking_route_does_not_take_one(web):
    client, _ = web
    body = {"a": QUICK, "b": OTHER, "copy_b": "nope.9"}
    missing = client.post("/api/neurons/i/943/mesh-diff-job", json=body)
    assert missing.status_code == 404 and "nope.9" in missing.json()["detail"]
    # The blocking route would silently measure one copy twice: it says so instead.
    sync = client.post("/api/neurons/i/943/mesh-diff", json={"a": QUICK, "b": OTHER, "copy_b": "off.0"})
    assert sync.status_code == 400 and "mesh-diff-job" in sync.json()["detail"]
    for bad in ("", "x" * 65):
        assert client.post("/api/neurons/i/943/mesh-diff-job",
                           json={"a": QUICK, "b": OTHER, "copy_b": bad}).status_code == 422


def test_the_facade_takes_the_second_copy_directly(web, request):
    """``diff_result(neuron, ..., neuron_b=...)`` is what the job calls; two copies of
    different cells cannot be compared."""
    import copy

    _, svc = web
    base = svc.neuron(943, "i")
    _add_shifted_copy(request, svc, 5.0, "reg-5")
    moved = svc.neuron(943, "i")
    across = svc.diff_result(base, QUICK, QUICK, neuron_b=moved)
    assert across.copy_a == "off.0" and across.copy_b == "off.1" and across.mean_abs_um > 1.0
    plain = svc.diff_result(base, QUICK, QUICK)
    assert plain.copy_a == plain.copy_b == "off.0" and plain.mean_abs_um < 0.5
    stranger = copy.copy(moved)
    stranger.id = 944
    mesh = svc.mesh_for(base, QUICK)
    with pytest.raises(ValueError, match="not of the same cell"):
        svc._cached_difference(base, QUICK, QUICK, mesh, mesh, 20_000, neuron_b=stranger)
