"""Where a build has got to, for whoever is listening.

Geometry code calls :func:`report` at its stages ("contouring", block 412 of
1,830); with nobody listening that is a no-op, so the library and its parity
tests are untouched.  A listener is installed for the duration of a block
with :func:`reporting` -- the web app's mesh job does that, so the job poll
can say what its build is doing (step 3 of
``docs/context/progress-plan-2026-10-06.md``).

The listener lives in a :class:`contextvars.ContextVar`, so each job thread
has its own and concurrent builds never hear each other.  A build that runs
in a worker process installs a listener there that forwards to the parent
(``sbfsem.service.facade``).

Work that runs for many minutes in steps (Render compare's report) can also be
stopped between its steps: the job installs a test with :func:`stoppable`, and
the work calls :func:`check_stop` where stopping is safe.  With nothing
installed :func:`check_stop` does nothing, so a build that never asks is never
interrupted.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar

__all__ = ["Listener", "Stopped", "Throttle", "check_stop", "current", "prefixed", "report",
           "reporting", "stoppable"]

#: ``listener(stage, done, total)``; ``done``/``total`` are ``None`` for a
#: stage without a count.
Listener = Callable[[str, "int | None", "int | None"], None]

_listener: ContextVar[Listener | None] = ContextVar("sbfsem_progress", default=None)

#: ``stop()`` says whether the work should stop now; ``None`` when nobody can stop it.
_stop: ContextVar[Callable[[], bool] | None] = ContextVar("sbfsem_stop", default=None)


class Stopped(Exception):
    """The work was asked to stop, and :func:`check_stop` stopped it between steps."""


def report(stage: str, done: int | None = None, total: int | None = None) -> None:
    """Tell the current listener, if there is one, where the build is."""
    listener = _listener.get()
    if listener is not None:
        # Progress must never break a build.
        with suppress(Exception):
            listener(stage, done, total)


def current() -> Listener | None:
    """The listener installed here, if any."""
    return _listener.get()


@contextmanager
def reporting(listener: Listener | None) -> Iterator[None]:
    """Send :func:`report` calls in this context to ``listener``."""
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)


@contextmanager
def stoppable(stop: Callable[[], bool] | None) -> Iterator[None]:
    """Let :func:`check_stop` in this context ask ``stop()`` whether to stop."""
    token = _stop.set(stop)
    try:
        yield
    finally:
        _stop.reset(token)


def check_stop() -> None:
    """Raise :class:`Stopped` when the work here was asked to stop.

    Called between the steps of long work, where nothing is left half done; a
    test that itself fails is taken as "carry on" (stopping must never break
    the work it guards).
    """
    stop = _stop.get()
    if stop is None:
        return
    try:
        asked = bool(stop())
    except Exception:  # noqa: BLE001 - see the docstring
        return
    if asked:
        raise Stopped("stopped")


@contextmanager
def prefixed(prefix: str) -> Iterator[None]:
    """Say which piece of a bigger job the reports inside belong to.

    A comparison builds two meshes and measures both; its listener hears
    "mesh A: contouring 412 of 1,830" instead of a bare "contouring".  A
    build in a worker process inherits it: the facade relays the child's
    reports to whatever listener is current when the build starts.  Without
    a listener this does nothing.
    """
    outer = _listener.get()
    if outer is None:
        yield
        return

    def relay(stage: str, done: int | None = None, total: int | None = None) -> None:
        outer(prefix + stage, done, total)

    with reporting(relay):
        yield


class Throttle:
    """Pass a listener's calls on at most every ``interval`` seconds.

    A new stage, and the last count of a stage, always go through; counts in
    between are dropped while they come faster than ``interval``.  For a
    listener that is costly per call (a cross-process queue).
    """

    def __init__(self, listener: Listener, interval: float = 0.25) -> None:
        self.listener = listener
        self.interval = interval
        self._stage: str | None = None
        self._sent = 0.0

    def __call__(self, stage: str, done: int | None = None, total: int | None = None) -> None:
        now = time.monotonic()
        final = done is not None and total is not None and done >= total
        if stage != self._stage or final or now - self._sent >= self.interval:
            self._stage = stage
            self._sent = now
            self.listener(stage, done, total)
