"""Background mesh builds, so no render ever outlives an HTTP request.

A mesh can take longer than any sane HTTP timeout -- RC2's c1 on a disc
pathway runs past nginx's five-minute proxy window, and the browser then
shows a bare gateway 504 with the worker still grinding.  The fix is
structural: the request that STARTS a build is never the request that
carries the result.  A client asks for a job, polls its status with cheap
instant requests, and collects the mesh when it is done; build duration
stops being an HTTP concern entirely.

Jobs run on a small pool of worker threads (``SBFSEM_MESH_WORKERS``, one
per vCPU is the sensible setting) behind a memory gate: every job carries an
estimate of its peak resident memory, and a job starts only when that fits
under ``SBFSEM_MESH_MEMORY_MB`` next to what is already building -- so a
batch of small bipolar cells builds in parallel while a 2 GB closed-curve
volume runs alone.  Identical requests share a single job, so two tabs
asking for the same render cost one build.

A queued job is waiting for one of two things, and ``MeshJob.waiting`` says
which: ``"worker"`` (no pool thread yet) or ``"memory"`` (it holds a thread
but does not fit under the gate).  Every gate wait is logged when it starts
and when it ends, and ``load()`` carries the running totals, so whether the
gate ever limits production is a measured fact rather than arithmetic.

A running build says where it has got to: the job listens to
:mod:`sbfsem.progress` while ``fn`` runs (a build in a worker process
reports back through the facade), and keeps the latest stage and count in
``MeshJob.stage``/``done``/``total`` for the poll.

A poll may wait for the job to end (:meth:`MeshJobStore.wait`, the poll's
``?wait=``): the job wakes it the moment it is done, failed or cancelled, so
the page hears of a finished build at once rather than at its next poll, and
nothing holds a thread while it waits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from sbfsem import progress

log = logging.getLogger(__name__)

__all__ = ["MeshJob", "MeshJobStore"]

#: How long a finished job lingers for late polls before it is swept.  The
#: mesh itself is also in the facade's cache, so a swept job costs a re-poll
#: cycle, not a rebuild.
DONE_TTL_SECONDS = 600.0


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


#: How many builds may run at once.  Threads share the neuron table and the
#: mesh cache; numpy, scipy and the marching-cubes kernels release the GIL in
#: their heavy loops, so two workers on two vCPUs overlap real work.
DEFAULT_WORKERS = _env_int("SBFSEM_MESH_WORKERS", 1)

#: Peak resident memory the running builds may add up to, in MB.  The default is
#: for a small machine (a 3.8 GB box with MemoryHigh 2600 MB and ~600 MB idle
#: leaves ~1700 MB for builds); the production unit sets SBFSEM_MESH_MEMORY_MB
#: for its 16 GB box (deploy/sbfsem-web.service), and a different box scales it.
DEFAULT_MEMORY_MB = _env_int("SBFSEM_MESH_MEMORY_MB", 1700)


@dataclass
class MeshJob:
    id: str
    key: Hashable
    status: str = "queued"  # queued | running | done | error | cancelled
    #: While queued: "worker" (no pool thread yet) or "memory" (holding a
    #: thread, blocked at the memory gate).  Empty once the build starts.
    waiting: str = "worker"
    detail: str = ""
    result: Any = None
    #: Estimated peak memory of the build, MB; what the gate reasons with.
    weight_mb: float = 0.0
    started: float | None = None
    finished: float | None = None
    #: Seconds this job spent blocked at the memory gate.
    gate_seconds: float = 0.0
    #: Where the running build has got to: a stage ("contouring"), and its
    #: count when it has one (block 412 of 1,830).
    stage: str = ""
    done: int | None = None
    total: int | None = None
    #: Requests waiting on this build: the one that started it and every
    #: identical request that joined it.  A cancel lets go of one, and a
    #: queued build stops only when nobody is left waiting.
    waiters: int = 1
    #: What the job makes: "mesh" (a render), "deviation" (an error map),
    #: "diff" (a mesh-to-mesh difference) or "grid" (a cell's whole-cell grid
    #: kept for the manual moves' Apply).  They share the pool, the memory
    #: gate, the poll and the cancel.
    kind: str = "mesh"
    #: Which cell and settings, for the journal ("c1178 NeitzInferiorMonkey
    #: voxel2+isotropic"): job ids alone say nothing when a build takes minutes.
    label: str = ""
    #: The stage that was running when the job failed, so the message can say
    #: where ("failed during MeshLab remesh"), not only that it did.
    failed_stage: str = ""
    #: Polls waiting for the job to end (:meth:`MeshJobStore.wait`): each is called
    #: once, from the thread that ends it, under the store's lock.
    watchers: list[Callable[[], None]] = field(default_factory=list, repr=False, compare=False)

    @property
    def ended(self) -> bool:
        """Done, failed or cancelled: nothing about the job will change again."""
        return self.status not in ("queued", "running")

    @property
    def progress(self) -> dict | None:
        """The stage for the poll, or None before the build reports one."""
        if not self.stage:
            return None
        return {"stage": self.stage, "done": self.done, "total": self.total}

    @property
    def seconds(self) -> float:
        """Wall-clock the build has been running (or ran)."""
        if self.started is None:
            return 0.0
        return (self.finished or time.time()) - self.started


def _wake(job: MeshJob) -> None:
    """The job has ended: wake every poll waiting on it.  Called under the store's lock."""
    watchers, job.watchers = job.watchers, []
    for wake in watchers:
        wake()


class MeshJobStore:
    """One-at-a-time mesh builds with pollable status."""

    def __init__(self, workers: int | None = None, memory_mb: float | None = None) -> None:
        self._jobs: dict[str, MeshJob] = {}
        self._by_key: dict[Hashable, str] = {}
        self._lock = threading.Lock()
        self.workers = max(1, int(workers or DEFAULT_WORKERS))
        self.memory_mb = float(memory_mb or DEFAULT_MEMORY_MB)
        self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="mesh-job")
        # The memory gate: builds admitted so far, by estimated peak.
        self._gate = threading.Condition(self._lock)
        self._running_mb = 0.0
        self._running = 0
        # Gate waits since start: how many, their total and longest seconds.
        self._gate_waits = 0
        self._gate_wait_s = 0.0
        self._gate_wait_max_s = 0.0

    def submit(self, key: Hashable, fn: Callable[[], Any], *, weight_mb: float = 0.0,
               kind: str = "mesh", label: str = "") -> MeshJob:
        """Start a build, or return the live/finished job for the same request.

        A job that errored or was cancelled does not block a retry: only
        queued, running, and successfully finished jobs are shared.
        ``weight_mb`` is the build's estimated peak memory; the gate holds it
        until it fits beside what is already running (a build heavier than
        the whole budget still runs -- alone).
        """
        with self._lock:
            self._sweep()
            existing_id = self._by_key.get(key)
            if existing_id is not None:
                existing = self._jobs.get(existing_id)
                if existing is not None and existing.status in ("queued", "running", "done"):
                    if existing.status != "done":
                        existing.waiters += 1
                    return existing
            job = MeshJob(id=uuid.uuid4().hex[:12], key=key, kind=kind, label=label,
                          weight_mb=max(0.0, float(weight_mb)))
            self._jobs[job.id] = job
            self._by_key[key] = job.id
        self._pool.submit(self._run, job, fn)
        return job

    def _fits(self, job: MeshJob) -> bool:
        # Alone, anything runs: a build the budget cannot hold must not wait
        # forever, and running it alone is exactly the old single-worker rule.
        return self._running == 0 or self._running_mb + job.weight_mb <= self.memory_mb

    def _run(self, job: MeshJob, fn: Callable[[], Any]) -> None:
        with self._gate:
            blocked_at = None
            if job.status == "queued" and not self._fits(job):
                blocked_at = time.time()
                job.waiting = "memory"
                self._gate_waits += 1
                log.info("mesh job %s waits at the memory gate: needs %.0f MB, "
                         "%d running use %.0f of %.0f MB",
                         job.id, job.weight_mb, self._running, self._running_mb, self.memory_mb)
            while job.status == "queued" and not self._fits(job):
                self._gate.wait(timeout=0.5)
            if blocked_at is not None:
                job.gate_seconds = time.time() - blocked_at
                self._gate_wait_s += job.gate_seconds
                self._gate_wait_max_s = max(self._gate_wait_max_s, job.gate_seconds)
                log.info("mesh job %s %s after %.1f s at the memory gate", job.id,
                         "cancelled" if job.status == "cancelled" else "admitted",
                         job.gate_seconds)
            job.waiting = ""
            if job.status == "cancelled":
                return
            job.status = "running"
            job.started = time.time()
            self._running += 1
            self._running_mb += job.weight_mb
        def listen(stage: str, done: int | None, total: int | None) -> None:
            job.stage, job.done, job.total = stage, done, total

        try:
            try:
                with progress.reporting(listen):
                    result = fn()
            except Exception as exc:  # surfaced to the poller, never lost
                log.warning("%s job %s (%s) failed during %r after %.0f s: %s", job.kind, job.id,
                            job.label or "-", job.stage,
                            time.time() - (job.started or time.time()), exc)
                with self._lock:
                    # The status last: a poll takes no lock, and one that sees
                    # "error" must see the reason with it.
                    job.detail, job.failed_stage, job.finished = str(exc), job.stage, time.time()
                    job.status = "error"
                    _wake(job)
                return
            with self._lock:
                # The status last, for the same reason: a poll that saw "done"
                # with the result still unset answered a finished job with null.
                job.result, job.finished = result, time.time()
                job.status = "done"
                _wake(job)
            # The only record of how long builds and measurements really take
            # on the server: until 2026-10-15 the journal had no durations.
            log.info("%s job %s (%s) done in %.1f s (weight %.0f MB, last stage %r)",
                     job.kind, job.id, job.label or "-", job.seconds, job.weight_mb, job.stage)
        finally:
            with self._gate:
                self._running -= 1
                self._running_mb -= job.weight_mb
                self._gate.notify_all()

    def load(self) -> dict:
        """What is building and waiting right now, for the health report."""
        with self._lock:
            self._sweep()
            queued = [j for j in self._jobs.values() if j.status == "queued"]
            by_kind: dict[str, dict[str, int]] = {}
            for j in self._jobs.values():
                if j.status in ("queued", "running"):
                    by_kind.setdefault(j.kind, {"queued": 0, "running": 0})[j.status] += 1
            return {
                "workers": self.workers,
                "memory_mb": self.memory_mb,
                "running": self._running,
                "running_mb": round(self._running_mb),
                "queued": len(queued),
                "gate_blocked": sum(1 for j in queued if j.waiting == "memory"),
                "gate_waits": self._gate_waits,
                "gate_wait_s": round(self._gate_wait_s, 1),
                "gate_wait_max_s": round(self._gate_wait_max_s, 1),
                "by_kind": by_kind,
            }

    def get(self, job_id: str) -> MeshJob | None:
        with self._lock:
            # Swept here as well as on a new job: a finished render was kept
            # until the next one was asked for, however long that took.
            self._sweep()
            return self._jobs.get(job_id)

    async def wait(self, job: MeshJob, timeout: float) -> bool:
        """Wait, in the event loop, until ``job`` ends or ``timeout`` seconds pass.

        Answers whether it has ended.  Nothing is polled and no thread is held:
        the thread that ends the job wakes this one through the loop
        (``call_soon_threadsafe``), so the answer goes out as the job ends.  The
        page follows a build with one such poll after another; before, a poll a
        second showed a finished build up to a second late (0.5 s on average,
        measured 0 to 0.7 s on production, 2026-10-20).
        """
        loop = asyncio.get_running_loop()
        ended = asyncio.Event()

        def wake() -> None:
            # A closed loop (shutdown) raises: nobody is left to answer.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(ended.set)

        with self._lock:
            if job.ended:
                return True
            job.watchers.append(wake)
        try:
            await asyncio.wait_for(ended.wait(), timeout)
        except TimeoutError:
            pass
        finally:
            with self._lock:
                if wake in job.watchers:
                    job.watchers.remove(wake)
        return job.ended

    def cancel(self, job_id: str) -> bool:
        """Let go of a job; stop it if it has not started and nobody else waits.

        A running numpy build cannot be interrupted, and its result is cached
        harmlessly anyway.  A build two requests joined (two tabs, two people,
        the same surface) used to be stopped by the first one's cancel -- a
        cell unloaded in one tab left the other with no surface and no error;
        now it is stopped only by the last.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.status == "queued":
                job.waiters -= 1
                if job.waiters > 0:
                    return False
                job.status, job.finished = "cancelled", time.time()
                if job.waiting == "worker":
                    job.waiting = ""
                self._gate.notify_all()
                _wake(job)
                return True
            return False

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _sweep(self) -> None:
        now = time.time()
        for job_id, job in list(self._jobs.items()):
            if job.finished is not None and now - job.finished > DONE_TTL_SECONDS:
                self._jobs.pop(job_id, None)
                if self._by_key.get(job.key) == job_id:
                    self._by_key.pop(job.key, None)
