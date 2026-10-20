"""The Settings tab's debug tools: what this server holds, and the means to clear it or restart it.

Two kinds of route live here:

``POST /api/browser/clear-cache``
    Anyone's.  It answers with ``Clear-Site-Data: "cache"``, so the browser
    that asked drops what it has cached of this site (a stale ``index.html``
    or module, CLAUDE.md ground rule 7) and nothing else: saved settings and
    the sign-in stay.

``/api/admin/...``
    An admin's.  They act on the server everyone shares: clear its cached
    renders, replace its build processes, restart it.  An admin is a name in
    ``SBFSEM_ADMIN_USERS`` (comma-separated; a built-in or a Viking user name,
    compared without case), any request from the machine itself (the deploy
    scripts' loopback calls), or anyone at all when no sign-in is configured
    (a local checkout, the test suite).  ``GET /api/admin/whoami`` is open, so
    the page can say why the server's tools are not offered.

The actions are POSTs whose JSON body names them (``{"confirm": "restart"}``):
the session cookies are ``SameSite=Lax``, and a JSON body cannot be sent from
another site without a preflight this app never answers, so a page elsewhere
cannot press them for a signed-in admin.

Restarting ends the process with ``RESTART_EXIT_CODE`` after a normal shutdown,
and the unit (``deploy/sbfsem-web.service``) starts it again for that code
(``RestartForceExitStatus=``) without counting it a failure
(``SuccessExitStatus=``).  A server that systemd did not start would not come
back, so it refuses (409).
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

#: Comma-separated user names allowed the server's tools.
ADMIN_ENV = "SBFSEM_ADMIN_USERS"

#: The exit code a requested restart ends with; the unit restarts for it.
RESTART_EXIT_CODE = 75

#: How long after answering the process is told to stop, so the answer gets out.
RESTART_DELAY_S = 0.5

#: A shutdown that has not finished by then is cut short, so a stuck request
#: cannot leave the service stopped instead of restarted.
RESTART_DEADLINE_S = 30.0

router = APIRouter(prefix="/api")

_BODY = Body(default=None)


def admin_names() -> frozenset[str]:
    raw = os.environ.get(ADMIN_ENV, "")
    return frozenset(name.strip().casefold() for name in raw.split(",") if name.strip())


def _login_configured(request: Request) -> bool:
    state = request.app.state
    return bool(state.auth.enabled or state.viking.enabled)


def _loopback(request: Request) -> bool:
    from ..app import LOOPBACK_HOSTS  # the module that imports this one

    return request.client is not None and request.client.host in LOOPBACK_HOSTS


def is_admin(request: Request) -> bool:
    if not _login_configured(request) or _loopback(request):
        return True
    user = getattr(request.state, "user", None)
    return isinstance(user, str) and user.casefold() in admin_names()


def _who(request: Request) -> str:
    return getattr(request.state, "user", None) or "a caller with no sign-in"


def _require_admin(request: Request) -> None:
    if not is_admin(request):
        user = getattr(request.state, "user", None)
        raise HTTPException(
            status_code=403,
            detail=f"The server's tools need an admin account; {user or 'this account'} is not one "
                   f"(the server's {ADMIN_ENV} names them)",
        )


def _require_confirm(request: Request, body: Any, word: str) -> dict:
    # The declared type too: FastAPI also reads a body that declares none as
    # JSON, and another site may send one of those without a preflight.
    declared = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared != "application/json" or not isinstance(body, dict) or body.get("confirm") != word:
        raise HTTPException(status_code=400,
                            detail=f'send {{"confirm": "{word}"}} as application/json')
    return body


def supervised() -> bool:
    """Whether systemd started this process: it sets ``INVOCATION_ID`` for every unit it runs."""
    return bool(os.environ.get("INVOCATION_ID"))


def _cgroup_memory_bytes() -> int | None:
    """The service's memory as systemd counts it (cgroup v2 ``memory.current``), where there is one."""
    try:
        lines = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
        path = next(line.split("::", 1)[1] for line in lines if line.startswith("0::"))
        current = Path("/sys/fs/cgroup") / path.lstrip("/") / "memory.current"
        return int(current.read_text(encoding="ascii").strip())
    except (OSError, StopIteration, ValueError):
        return None


@router.post("/browser/clear-cache")
def clear_browser_cache() -> JSONResponse:
    """Tell the browser that asked to drop what it has cached of this site."""
    return JSONResponse({"cleared": "cache"},
                        headers={"Clear-Site-Data": '"cache"', "Cache-Control": "no-store"})


@router.get("/admin/whoami")
def whoami(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    return {"user": user if isinstance(user, str) else None,
            "admin": is_admin(request),
            "login": _login_configured(request),
            "admins_named": bool(admin_names()),
            "admin_setting": ADMIN_ENV}


@router.get("/admin/status")
def status(request: Request) -> dict:
    _require_admin(request)
    from sbfsem import __version__

    state = request.app.state
    service = state.service
    jobs = getattr(state, "mesh_jobs", None)
    started = float(getattr(state, "started_at", 0.0) or 0.0)
    return {
        "version": __version__,
        "assets": getattr(state, "assets", ""),
        "instance": getattr(state, "instance_id", ""),
        "started_at": started,
        "uptime_s": max(0.0, time.time() - started) if started else None,
        "pid": os.getpid(),
        "supervised": supervised(),
        "service_memory_bytes": _cgroup_memory_bytes(),
        "memory": service.memory_report(),
        "disk_cache": service.disk_cache_report(),
        "pool": service.build_pool_report(),
        "builds": jobs.load() if jobs is not None else None,
    }


@router.post("/admin/clear-renders")
def clear_renders(request: Request, body: Any = _BODY) -> dict:
    _require_admin(request)
    disk = bool(_require_confirm(request, body, "clear").get("disk"))
    log.warning("cached renders cleared by %s (disk=%s)", _who(request), disk)
    return request.app.state.service.clear_renders(disk=disk)


@router.post("/admin/recycle-workers")
def recycle_workers(request: Request, body: Any = _BODY) -> dict:
    _require_admin(request)
    _require_confirm(request, body, "recycle")
    log.warning("build workers replaced by %s", _who(request))
    return {"recycled": request.app.state.service.recycle_build_workers()}


@router.post("/admin/restart", status_code=202)
def restart(request: Request, body: Any = _BODY) -> dict:
    _require_admin(request)
    _require_confirm(request, body, "restart")
    if not supervised():
        raise HTTPException(
            status_code=409,
            detail="This server was not started by systemd, so it would not come back: "
                   "restart it where it was started",
        )
    state = request.app.state
    if getattr(state, "restart_exit", None) is None:
        state.restart_exit = RESTART_EXIT_CODE
        log.warning("restart requested by %s", _who(request))
        # SIGTERM is uvicorn's own way to stop: it finishes the requests in
        # flight and runs the lifespan's shutdown, which ends with finish_restart.
        threading.Timer(RESTART_DELAY_S, os.kill, (os.getpid(), signal.SIGTERM)).start()
        deadline = threading.Timer(RESTART_DELAY_S + RESTART_DEADLINE_S, _exit_now)
        deadline.daemon = True
        deadline.start()
    return {"restarting": True, "instance": getattr(state, "instance_id", "")}


def _exit_now() -> None:
    log.warning("the shutdown took over %.0f s: ending now", RESTART_DEADLINE_S)
    logging.shutdown()
    os._exit(RESTART_EXIT_CODE)


def finish_restart(app: Any) -> None:
    """Called last by the lifespan: after a requested restart, exit with the code the unit restarts for."""
    code = getattr(app.state, "restart_exit", None)
    if code is not None:
        log.warning("restarting (exit %d)", code)
        logging.shutdown()
        os._exit(code)
