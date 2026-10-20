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
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar

__all__ = ["Listener", "Throttle", "current", "prefixed", "report", "reporting"]

#: ``listener(stage, done, total)``; ``done``/``total`` are ``None`` for a
#: stage without a count.
Listener = Callable[[str, "int | None", "int | None"], None]

_listener: ContextVar[Listener | None] = ContextVar("sbfsem_progress", default=None)


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
