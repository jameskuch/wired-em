"""A build stopped by a deploy or a restart is not a build that ran out of memory.

On 2026-10-20 a deploy stopped the service at 17:42:41 UTC, 20 s into a Closed
curve build of Inferior c1178 at 0.05 um.  systemd stops the whole unit, so the
build process got the same SIGTERM; its pool broke, and the page said the
server had most likely run out of memory and to ask again with a coarser
setting.  Nothing in the settings was wrong (the build peaks at 1.19 GB).
"""

from __future__ import annotations

import signal
import time
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

import pytest

from sbfsem.service import facade
from sbfsem.service.facade import BuildInterrupted, SbfsemService
from sbfsem_web.jobs import MeshJobStore


class DyingPool:
    """A pool whose worker dies under every build."""

    def submit(self, *args):
        future: Future = Future()
        future.set_exception(BrokenProcessPool("a process in the process pool was terminated"))
        return future

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def svc(i943_path, monkeypatch):
    from sbfsem.io.legacy_json import read_legacy_json

    monkeypatch.setattr(facade, "STOP_GRACE_S", 0.05)
    service = SbfsemService(offline=True, build_processes=1)
    service.add_neuron(read_legacy_json(i943_path))
    yield service
    service.close()


def test_a_build_stopped_with_the_service_says_so(svc):
    svc._process_pool = DyingPool()  # type: ignore[assignment]
    svc.note_stopping()
    with pytest.raises(BuildInterrupted, match="restarted during this build"):
        svc.render_mesh(943, "i", method="cylinder", smooth_iterations=0)
    assert svc._process_pool is None


def test_a_build_process_killed_while_the_service_runs_is_still_memory(svc):
    svc._process_pool = DyingPool()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="ran out of memory") as caught:
        svc.render_mesh(943, "i", method="cylinder", smooth_iterations=0)
    assert not isinstance(caught.value, BuildInterrupted)


def test_the_stop_can_be_heard_after_the_pool_breaks(svc):
    """The pool may break before this process has run its SIGTERM handler."""
    import threading

    svc._process_pool = DyingPool()  # type: ignore[assignment]
    timer = threading.Timer(0.01, svc.note_stopping)
    timer.start()
    try:
        with pytest.raises(BuildInterrupted):
            svc.render_mesh(943, "i", method="cylinder", smooth_iterations=0)
    finally:
        timer.cancel()


def test_an_interrupted_job_is_left_for_the_page_to_ask_again():
    """Not failed: the page polls on through the restart, the new process says the
    job is gone (404), and the page asks for it again once (api.js pollJob)."""
    store = MeshJobStore(workers=1, memory_mb=100)

    def stopped():
        raise BuildInterrupted("the server restarted during this build")

    try:
        job = store.submit(("stopped",), stopped, weight_mb=1)
        for _ in range(100):
            if job.stage:
                break
            time.sleep(0.01)
        assert job.status == "running" and not job.ended
        assert job.stage == "the server is restarting" and job.detail in ("", None)
        # The gate is left as if it had ended: the next build is not held for it.
        assert store.load()["running"] == 0 and store.load()["running_mb"] == 0
        failed = store.submit(("failed",), lambda: 1 / 0, weight_mb=1)
        for _ in range(100):
            if failed.ended:
                break
            time.sleep(0.01)
        assert failed.status == "error"
    finally:
        store.close()


def test_the_service_hears_sigterm_before_uvicorn_does(svc):
    from sbfsem_web.app import _watch_stop

    heard: list[int] = []
    before = signal.signal(signal.SIGTERM, lambda signum, frame: heard.append(signum))
    try:
        _watch_stop(svc)
        assert not svc.stopping.is_set()
        signal.raise_signal(signal.SIGTERM)
        assert svc.stopping.is_set() and heard == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, before)


def test_without_a_handler_to_chain_to_nothing_is_installed(svc):
    from sbfsem_web.app import _watch_stop

    before = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        _watch_stop(svc)
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, before)
