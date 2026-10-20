"""Build progress: the report hook, its throttle, and the two ways it travels."""

from __future__ import annotations

import threading
import time

import pytest

from sbfsem import progress
from sbfsem_web.jobs import MeshJobStore


def test_report_without_a_listener_is_a_no_op():
    progress.report("contouring", 1, 2)  # nothing installed: nothing happens


def test_reporting_routes_to_the_listener_and_then_stops():
    heard = []
    with progress.reporting(lambda *a: heard.append(a)):
        progress.report("contouring", 3, 10)
        progress.report("smoothing")
    progress.report("after")
    assert heard == [("contouring", 3, 10), ("smoothing", None, None)]


def test_a_failing_listener_never_breaks_the_build():
    def boom(*_a):
        raise RuntimeError("listener broke")

    with progress.reporting(boom):
        progress.report("contouring", 1, 2)


def test_throttle_keeps_stage_changes_and_the_last_count():
    heard = []
    throttle = progress.Throttle(lambda *a: heard.append(a), interval=60.0)
    for i in range(100):
        throttle("contouring", i, 100)
    throttle("contouring", 100, 100)
    throttle("smoothing")
    assert heard == [("contouring", 0, 100), ("contouring", 100, 100), ("smoothing", None, None)]


def test_threads_do_not_hear_each_other():
    heard: dict[str, list] = {"a": [], "b": []}
    barrier = threading.Barrier(2)

    def run(name):
        with progress.reporting(lambda *a: heard[name].append(a[0])):
            barrier.wait()
            progress.report(name)

    threads = [threading.Thread(target=run, args=(n,)) for n in heard]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert heard == {"a": ["a"], "b": ["b"]}


def test_a_running_job_carries_its_build_stage():
    store = MeshJobStore(workers=1, memory_mb=1000)
    reported = threading.Event()
    release = threading.Event()

    def build():
        progress.report("contouring", 412, 1830)
        reported.set()
        release.wait(2)
        return "mesh"

    job = store.submit(("p",), build)
    assert reported.wait(2)
    assert job.status == "running"
    assert job.progress == {"stage": "contouring", "done": 412, "total": 1830}
    release.set()
    for _ in range(50):
        if job.status == "done":
            break
        time.sleep(0.02)
    assert job.status == "done"
    store.close()


@pytest.mark.parametrize("processes", [0, 1])
def test_a_build_reports_its_stages_in_thread_and_in_a_worker_process(i943_path, processes):
    """The default pathway's stages reach the listener from a child process too."""
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    service = SbfsemService(offline=True, build_processes=processes)
    service.add_neuron(read_legacy_json(i943_path))
    heard = []
    try:
        with progress.reporting(lambda *a: heard.append(a)):
            service.render_mesh(943, "i", method="cylinder", pathway="voxel2")
        # Every report is in by the time the build returns: a worker's last
        # ones are waited for, not raced.
    finally:
        service.close()
    stages = [s for s, _d, _t in heard]
    assert "building the field" in stages and "contouring" in stages
    final = [(d, t) for s, d, t in heard if s == "contouring"][-1]
    assert final[0] == final[1] and final[1] > 0  # the count closes at total
    assert stages.index("building the field") < stages.index("contouring")
    assert stages[-1] in ("smoothing", "decimating")
