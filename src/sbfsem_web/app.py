"""FastAPI application for SBFSEM-pytools.

Run it with::

    sbfsem-web            # or: uvicorn sbfsem_web.app:app --reload

The backend package never imports this module; the dependency runs one way only,
which is what lets :mod:`sbfsem` be used headlessly in a script or notebook.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager
from hashlib import blake2b
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from sbfsem import __version__
from sbfsem.io.cache import default_cache_dir
from sbfsem.service.facade import SbfsemService

from .api.admin import finish_restart
from .api.admin import router as admin_router
from .api.routes import router
from .auth import COOKIE_NAME, MIN_PASSWORD_LENGTH, SESSION_TTL, AuthManager
from .jobs import MeshJobStore
from .saves import MAX_FILE_BYTES, CellRenderStore, LibraryStore, SaveStore
from .viking import SESSION_TTL as VIKING_SESSION_TTL
from .viking import VIKING_COOKIE, VIKING_INSTALL_URL, VikingAuth, VikingError

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE_DIR = Path(__file__).parent / "templates"

#: Reachable without a session even when login is on.  /api/health stays open
#: for monitoring; /login, /logout and /welcome (invite redemption) must be,
#: or nobody could sign in in the first place.  So must the three /auth paths:
#: they *are* the Viking sign-in, and a browser arriving at /auth/callback has
#: by definition no session yet.
#: /api/build too: it names only the running version, and a tab whose login
#: has expired must still learn that a new build is up, or its redeploy
#: banner never appears (every poll was a silent redirect to the login page).
AUTH_EXEMPT_PATHS = frozenset({
    "/login", "/logout", "/welcome", "/api/health", "/api/build",
    "/auth/login", "/auth/callback", "/auth/logout",
})

#: Why a Viking sign-in failed, as a code in the redirect back to /login.
#: A code rather than the message itself: whatever is in the query string is
#: rendered into the page, and an attacker-chosen sentence on a real login
#: page is a phishing tool ("Your session expired, call this number").
LOGIN_ERRORS = {
    "viking": "Viking sign-in failed. Please try again.",
    "viking_denied": "That sign-in was cancelled or refused.",
    "viking_unreachable": (
        "The Viking Identity Server could not be reached. Try again in a moment."
    ),
    "viking_no_volumes": (
        "You are signed in to Viking, but no volumes have been shared with your "
        "account yet. Ask the Viking administrators for access."
    ),
    "viking_unsupported_volumes": (
        "You are signed in to Viking, but none of the volumes shared with your "
        "account is one this tool can open."
    ),
}


def _login_error(code: str) -> str:
    return LOGIN_ERRORS.get(code, "")


def _env_count(name: str) -> int | None:
    """A positive whole number from the environment, or ``None`` (unset, blank or not one)."""
    try:
        value = int((os.environ.get(name) or "").strip() or 0)
    except ValueError:
        log.warning("%s is not a whole number; using the default", name)
        return None
    return value if value > 0 else None


def _env_mb(name: str) -> float | None:
    """A number of MB from the environment (0 included), or ``None`` (unset, blank or not one)."""
    text = (os.environ.get(name) or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        log.warning("%s is not a number; using the default", name)
        return None
    return value if value >= 0 else None


#: Paths a signed-out browser reaches from Viking itself, so the visitor is a
#: Viking user by construction.  They get one silent sign-in attempt
#: (``prompt=none``) before any login page: Viking's context menu goes through
#: the identity server first, so its session cookie is there to reuse, and a
#: page with a "Sign in with Viking" button on it would only be a click in the
#: way.  Everything else still starts at /login, where a built-in account can
#: choose the local form.
SILENT_SIGN_IN_PATHS = frozenset({"/open"})

#: What the identity server answers a ``prompt=none`` request it cannot serve
#: without showing something (OpenID Connect Core 3.1.2.6).  Measured against
#: identity.codepharm.net on 2026-09-18: no session there -> ``login_required``
#: straight back to /auth/callback, no login page in between.
SILENT_REFUSALS = frozenset({
    "login_required", "interaction_required",
    "consent_required", "account_selection_required",
})

#: A second silent attempt this soon after the last one means the first one
#: signed the browser in and the session did not stick (a cookie refused, a
#: session file that could not be written).  Trying again would loop between
#: here and the identity server until the browser gives up; the login page is
#: where it stops.
SILENT_RETRY_GUARD = 60.0


def configure_logging() -> None:
    """Make this application's own INFO lines visible in the journal.

    uvicorn installs handlers on its own loggers and leaves the root logger
    bare, so ``log.info(...)`` from these modules reached nothing: only
    WARNING and above surfaced, by way of :data:`logging.lastResort`.  Every
    line added on 2026-09-10 to make a Viking sign-in traceable was invisible
    for exactly this reason, which is a poor way to find out.

    Adding a root handler does not duplicate uvicorn's output: its loggers
    carry their own handlers and do not propagate.
    """
    level = (os.environ.get("SBFSEM_LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s: %(message)s"))
        root.addHandler(handler)
    for name in ("sbfsem", "sbfsem_web"):
        logging.getLogger(name).setLevel(level)


#: How many cell ids one /open link may carry.  Far above any real link, and
#: it stops a hand-edited URL from queueing a thousand builds.
MAX_OPEN_CELLS = 50


def resolve_open_link(
    request: Request, volume: str, cells: str, technique: str, location: str = ""
) -> dict:
    """Turn an ``/open`` query into instructions, or into a reason it cannot be.

    Returns a dict the shell stamps into the page.  ``error`` is set to a
    sentence to show the visitor; when it is, nothing is loaded.
    """
    from sbfsem.core.volumes import Volumes, from_viking_name, validate_source
    from sbfsem.geometry.cylinder import PATHWAYS

    name = (volume or "").strip()
    if not name:
        return {"error": "That link did not say which volume to open."}

    # The identity server's name first -- that is what the contract specifies
    # and what their site emits -- then our own, so a link copied out of this
    # app's address bar keeps working.
    source = from_viking_name(name)
    if source is None:
        try:
            source = validate_source(name)
        except (ValueError, TypeError):
            source = None
    if source is None:
        return {
            "error": (
                f"SBFSEM-tools has no renderer for the volume {name!r}. "
                "Viking governs more volumes than this tool can open."
            )
        }

    session = getattr(request.state, "viking_session", None)
    if (
        session is not None
        and request.app.state.viking.config.gate_volumes
        and not session.may_read(source)
    ):
        return {
            "error": (
                f"Your Viking account has no access to {name}. "
                "Ask the Viking administrators to share this volume with you."
            )
        }

    ids: list[int] = []
    dropped: list[str] = []
    for token in (cells or "").replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError:
            dropped.append(token)
    truncated = len(ids) > MAX_OPEN_CELLS
    ids = ids[:MAX_OPEN_CELLS]

    payload: dict = {
        "source": source,
        "display_name": Volumes.from_str(source).display_name,
        "viking_name": name,
        "cells": ids,
    }
    # What the visitor should be told about the link, all of it: each problem
    # used to replace the one before, so a link with an unknown technique AND
    # an unreadable cell id reported only the second.
    notes: list[str] = []
    # A technique we do not have is not worth refusing the whole link over;
    # the app's own default renders the cell either way.
    if technique.strip() in PATHWAYS:
        payload["technique"] = technique.strip()
    elif technique.strip():
        notes.append(f"Unknown technique {technique.strip()!r}; used the default.")
    # The annotation a Viking context menu was opened on, so the view lands
    # on it.  Location IDs are numbered per volume, like structure IDs; the
    # volume resolved above is the one it will be looked up in.
    loc = (location or "").strip()
    if loc:
        if loc.isdigit() and int(loc) > 0:
            payload["location"] = int(loc)
        else:
            notes.append(f"Ignored location {loc!r}: not a Location ID.")
    if dropped:
        notes.append(f"Ignored {len(dropped)} unreadable cell id(s): {', '.join(dropped[:5])}")
    if truncated:
        notes.append(f"A link may open at most {MAX_OPEN_CELLS} cells; the rest were dropped.")
    if notes:
        payload["note"] = " ".join(notes)
    return payload


class LaunchRefused(Exception):
    """Why an "Open in Viking" link could not be built, with the status to say it in."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


#: The two Viking clients a link can go to, by the id the interface uses.  The
#: unsigned test build installs beside the signed one as "Viking Test" and
#: registers ``viking-test://`` only, leaving ``viking://`` to the signed one
#: (``VikingChannelIdentity.cs``, Legacy branch); both read the same query.
VIKING_CLIENT_SCHEMES = {"viking": "viking", "test": "viking-test"}
VIKING_CLIENT_NAMES = {"viking": "Viking", "test": "Viking Test"}


def _launch_client(value: str, what: str = "client") -> str:
    """``viking`` or ``test``; blank is the signed Viking, anything else is refused."""
    client = (value or "").strip().lower() or "viking"
    if client not in VIKING_CLIENT_SCHEMES:
        raise LaunchRefused(
            400, f"Unknown Viking {what} {value!r}: it is viking (the signed client) or test."
        )
    return client


def viking_launch_target(
    request: Request, volume: str, location: str = "", client: str = "viking"
) -> str:
    """The link that opens ``volume`` in Viking, at ``location``.

    The reverse of :func:`resolve_open_link`, under the same contract: the
    volume goes out under the **identity server's** name, and the place is a
    Location ID, or ``x,y,z[,downsample]`` in Viking's pixel/section space for
    a point that is no annotation.  With the launch mode ``direct`` (the
    default, :data:`~sbfsem_web.viking.DEFAULT_LAUNCH_MODE`) that is
    ``viking://open?volumeName=&location=`` itself, and a cold start signs in
    in Viking's own window.  With ``code`` it is their
    ``VikingLaunch/CreateCode``, which mints a one-use code under the
    browser's own session there and redirects to ``viking://open``.  Either
    way no token of ours, and no request of ours, is involved.

    ``client`` picks the program: ``viking`` (the default) or ``test``, the
    unsigned "Viking Test" build, whose link is ``viking-test://open?...``.
    Their ``CreateCode`` only ever redirects to ``viking://``, so a link for
    the test build is always the direct one, whatever the launch mode.
    """
    from sbfsem.core.volumes import Volumes, from_viking_name, validate_source

    name = (volume or "").strip()
    if not name:
        raise LaunchRefused(400, "That link did not say which volume to open in Viking.")
    source: str | None
    try:
        source = validate_source(name)
    except (ValueError, TypeError):
        source = from_viking_name(name)
    if source is None:
        raise LaunchRefused(404, f"SBFSEM-tools knows no volume called {name!r}.")
    viking_name = Volumes.from_str(source).viking_name
    if viking_name is None:
        raise LaunchRefused(
            404, f"{Volumes.from_str(source).display_name} is not a Viking volume."
        )
    session = getattr(request.state, "viking_session", None)
    if (
        session is not None
        and request.app.state.viking.config.gate_volumes
        and not session.may_read(source)
    ):
        raise LaunchRefused(403, f"Your Viking account has no access to {viking_name}.")

    config = request.app.state.viking.config
    query = f"volumeName={quote(viking_name, safe='')}"
    loc = (location or "").strip()
    if loc:
        place = _launch_location(loc, config.launch_max_downsample)
        query += f"&location={quote(place, safe=',')}"
    client = _launch_client(client)
    if config.launch_mode == "code" and client == "viking":
        return f"{config.launch_url}?{query}"
    # The same two parameters, straight to the client (``launch_mode``).
    return f"{VIKING_CLIENT_SCHEMES[client]}://open?{query}"


def _launch_where(volume: str, location: str) -> str:
    """The place a launch is headed, for a person to read.

    The volume always travels with the ID (ground rule 11): a bare Location
    number means nothing without the volume it is numbered in.
    """
    from sbfsem.core.volumes import Volumes, from_viking_name, validate_source

    name = (volume or "").strip()
    source: str | None
    try:
        source = validate_source(name)
    except (ValueError, TypeError):
        source = from_viking_name(name)
    display = Volumes.from_str(source).display_name if source else name or "that volume"
    loc = (location or "").strip()
    if not loc:
        return f"{display}, at the view Viking opens with."
    if loc.isdigit():
        return f"{display}, at Location {loc}."
    return f"{display}, at {loc} in Viking's own x, y, section."


def viking_launch_page(
    target: str,
    where: str,
    client: str = "viking",
    then: str = "",
    then_target: str = "",
) -> HTMLResponse:
    """The page that hands ``target`` to the operating system, and watches.

    Sent instead of a bare 302 because the two things worth saying about a
    launch can only be said from our own origin: Viking runs on Windows only,
    and a launch that never takes the focus away never happened -- which on
    Windows means the client is not installed.  Once the browser has left for
    the identity server there is no script of ours to notice either.  The page
    itself is ``static/viking-launch.html``; it is sent only for ``?page=1``,
    so the endpoint's own contract -- a 302 to the identity server -- stands.

    ``client`` is the program ``target`` is for.  ``then`` names a second one,
    and ``then_target`` its link: tried when nothing answers the first within
    the page's wait, which is how a machine that has only the test build is
    found by a link for the signed one.
    """
    html = (STATIC_DIR / "viking-launch.html").read_text(encoding="utf-8")
    for token, value in (
        ("__SBFSEM_TARGET__", target),
        ("__SBFSEM_WHERE__", where),
        ("__SBFSEM_INSTALL__", VIKING_INSTALL_URL),
        ("__SBFSEM_INSTALL_TEXT__", VIKING_INSTALL_URL),
        ("__SBFSEM_CLIENT__", client),
        ("__SBFSEM_CLIENT_NAME__", VIKING_CLIENT_NAMES[client]),
        ("__SBFSEM_THEN__", then),
        ("__SBFSEM_THEN_NAME__", VIKING_CLIENT_NAMES.get(then, "")),
        ("__SBFSEM_THEN_TARGET__", then_target),
    ):
        html = html.replace(token, escape(value, quote=True))
    # It carries a one-use launch target: never stored, never shared.
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _launch_location(text: str, max_downsample: float) -> str:
    """A Location ID, or ``x,y,z[,downsample]``, exactly as Viking reads them.

    The downsample is capped at ``max_downsample``: Viking applies whatever it
    is sent, and a whole-cell view here corresponds to several hundred, which
    opens there as a volume the size of a speck.  A closer view passes as is.
    """
    if text.isdigit():
        if int(text) <= 0:
            raise LaunchRefused(400, "A Location ID is a positive integer.")
        return str(int(text))
    parts = [p for p in text.replace(";", ",").replace(" ", ",").split(",") if p]
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        numbers = []
    if len(numbers) not in (3, 4) or not all(math.isfinite(n) for n in numbers):
        raise LaunchRefused(
            400, f"Cannot read {text!r} as a Location ID or as x,y,z[,downsample]."
        )
    if len(numbers) == 4 and numbers[3] <= 0:
        raise LaunchRefused(400, "The downsample must be positive.")
    # X, Y and the section number are whole pixels and sections in Viking.
    out = [str(round(n)) for n in numbers[:3]]
    if len(numbers) == 4:
        out.append(f"{min(numbers[3], max_downsample):g}")
    return ",".join(out)


#: uvicorn listens on loopback only and trusts forwarded headers from nginx
#: alone, so a request whose client is loopback really originated on the
#: server itself -- the deploy script's snapshot/restore calls, mostly.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


def asset_version() -> str:
    """A cache key that changes whenever a served asset changes.

    The package version alone is not enough: during development the files change
    while the version does not, and the browser's heuristic cache then serves
    yesterday's ``app.js`` indefinitely.  Mixing in the modification times of the
    JavaScript and CSS makes every edit a cache miss and every unchanged reload a
    hit.
    """
    stamps = sorted(
        f"{p.name}:{p.stat().st_mtime_ns}"
        for p in (*STATIC_DIR.glob("js/*.js"), *STATIC_DIR.glob("css/*.css"))
    )
    digest = blake2b("|".join(stamps).encode("utf-8"), digest_size=6).hexdigest()
    return f"{__version__}-{digest}"


class RevalidatingStatic(StaticFiles):
    """Static files a browser must revalidate before reusing.

    ``index.html`` stamps :func:`asset_version` onto the URLs it writes, but
    ``app.js`` is an ES module and the modules it imports carry no query of
    their own.  Left to the heuristic cache, a browser coming back after a
    deploy would fetch the new ``app.js`` and run it against yesterday's
    ``render3d.js`` -- a half-updated app, which is worse than either
    version alone and invisible from the server.

    ``no-cache`` does not mean "do not store": the file stays in the cache
    and the browser revalidates it with its ETag, so an unchanged asset
    costs a 304 with no body.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = SbfsemService(
        offline=os.environ.get("SBFSEM_OFFLINE", "").lower() in ("1", "true", "yes"),
        # Explicit, so the built-mesh cache lives beside the OData one even
        # when the variable is unset (a dev server).
        cache_dir=os.environ.get("SBFSEM_CACHE_DIR") or str(default_cache_dir()),
        # 0 builds in the job threads; N runs the geometry step in N child
        # processes, which is what uses more than two vCPUs.
        build_processes=int(os.environ.get("SBFSEM_MESH_PROCESSES", "0") or 0),
        # A child process that still holds more than this after a build is let
        # go with its pool and a new one started (default 1024 MB; 0 never).
        build_recycle_mb=_env_mb("SBFSEM_MESH_RECYCLE_MB"),
        # How many built meshes stay in memory (default 12): each render
        # option set of each cell is one, so a bigger box with more people on
        # it wants more.  The disk cache (SBFSEM_MESH_CACHE_MB) is separate.
        mesh_cache_size=_env_count("SBFSEM_MESH_CACHE_SIZE"),
        # A postsynapse Viking's tags do not type is not assumed conventional
        # here (RC1: 7,837 of 10,008 cached PSDs have no tag): it takes the
        # kind of the structure it is linked to, flagged "inferred", or stays
        # "UntaggedPost".  assume = MATLAB, separate = never infer.
        untagged_psd=os.environ.get("SBFSEM_UNTAGGED_PSD", "infer"),
        # Viking's section correction, on top of the live registration: the
        # user's decision of 2026-09-24 is its curve fit along each traced
        # process by default.  On c21768 it took the median step between
        # linked annotations on adjacent sections from 0.194 to 0.125 um and
        # the steps over 1.5 um from 39 to 19; the residual field alone
        # (neighbor, measured worse on 2026-09-23) and both (all) stay a
        # switch away in each cell's panel.  off | neighbor | curvefit | all
        # (1 = neighbor).  SBFSEM_CORRECTIONS_URL names the service.
        section_corrections=os.environ.get("SBFSEM_SECTION_CORRECTIONS", "curvefit"),
        # The Neitz lab's volumes are corrected by the field, then the fit
        # (the user's decision of 2026-10-20); on 2026-10-20 the service
        # answered it for Inferior, Nasal, Temporal and Cped cells.
        lab_corrections={"Neitz": os.environ.get("SBFSEM_SECTION_CORRECTIONS_NEITZ", "all")},
        # Draw each cell where the volume's current section transforms put it,
        # as Viking does, rather than at the positions stored when each
        # annotation was saved (sbfsem.io.registration); 0 = stored positions.
        live_registration=os.environ.get("SBFSEM_LIVE_REGISTRATION", "1").lower()
        not in ("0", "false", "no", "off"),
        corrections_url=os.environ.get("SBFSEM_CORRECTIONS_URL") or None,
    )
    # Identifies this process to already-open browsers.  A restart loses the
    # loaded-neuron table, so a client that sees a new instance id knows its
    # view of the server is stale even when no file on disk changed.
    # Confined directory for user-saved documents. Kept out of the OData cache
    # so a cache wipe never takes someone's saved work with it.
    app.state.saves = SaveStore(
        os.environ.get("SBFSEM_SAVE_DIR") or Path.home() / ".sbfsem" / "saves"
    )
    app.state.saves.ensure_base()
    log.info("saves directory: %s", app.state.saves.base)
    app.state.cell_renders = CellRenderStore(
        os.environ.get("SBFSEM_CELL_RENDER_DIR")
        or Path.home() / ".sbfsem" / "cell-renders"
    )
    app.state.cell_renders.ensure_base()
    log.info("cell render directory: %s", app.state.cell_renders.base)
    # Shared libraries: what one person saves, everyone can pick.
    library_base = Path(
        os.environ.get("SBFSEM_LIBRARY_DIR") or Path.home() / ".sbfsem" / "library"
    )
    app.state.libraries = {
        kind: LibraryStore(library_base / kind) for kind in ("materials", "gradients")
    }
    for store in app.state.libraries.values():
        store.ensure_base()
    log.info("library directory: %s", library_base)
    if app.state.viking.enabled:
        gone = app.state.viking.store.purge()
        if gone:
            log.info("purged %d expired viking session(s)", gone)
    app.state.mesh_jobs = MeshJobStore()
    log.info("mesh builds: %s", app.state.mesh_jobs.load())
    app.state.instance_id = uuid.uuid4().hex[:12]
    app.state.started_at = time.time()
    app.state.assets = asset_version()
    log.info("SBFSEM-pytools %s ready (offline=%s)", __version__, app.state.service.offline)
    try:
        yield
    finally:
        app.state.mesh_jobs.close()
        app.state.service.close()
        # Last: after a restart asked for in the Settings tab, the exit code the unit restarts for.
        finish_restart(app)


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="SBFSEM-pytools",
        version=__version__,
        description=(
            "Connectomics analysis and rendering for serial block-face EM "
            "annotations from the Viking OData service."
        ),
        lifespan=lifespan,
        # No default_response_class: one named here reaches every included route
        # and keeps FastAPI off pydantic's encoder (SafeJSONRouter).
    )
    app.include_router(router)
    app.include_router(admin_router)
    app.state.auth = AuthManager()
    app.state.viking = VikingAuth(app.state.auth.auth_dir)

    # Authlib keeps the OAuth ``state`` and the PKCE verifier in a signed
    # cookie while the user is away at the identity server's login page.  Only
    # added when Viking sign-in is on, so an ordinary checkout gains neither
    # the middleware nor the key file.
    if app.state.viking.enabled:
        from starlette.middleware.sessions import SessionMiddleware

        app.add_middleware(
            SessionMiddleware,
            secret_key=app.state.viking.state_secret(),
            session_cookie="sbfsem_oauth",
            # Secure cookies over TLS, but a `http://localhost` callback --
            # the one the identity server registered for development -- would
            # never receive one, and the handshake would fail on a state
            # mismatch that looks nothing like its cause.  Follow the scheme
            # of the callback we are actually configured to use.
            https_only=app.state.viking.config.redirect_uri.startswith("https://"),
            same_site="lax",
            # The handshake is one round trip, but the human in the middle of
            # it may be hunting for a password.  Ten minutes was tight enough
            # that expiry would surface as a bare "state mismatch", which
            # explains nothing; half an hour is still short-lived.
            max_age=1800,
        )

    @app.middleware("http")
    async def cap_saved_documents(request: Request, call_next):
        """Refuse a saved document over the size limit before it is read.

        nginx lets 64 MB through for the neuron-JSON upload, and the route
        parsed all of it into Python objects only for the store to refuse it
        at 5 MB.  The declared length decides, with the store's wording (507).

        That makes the limit one on the request body *as sent*, which is all
        that is known before parsing, and not on the size the store writes
        (it re-encodes with ``indent=1``).  The two differ both ways: a
        compact body under 5 MB can store over it (the store refuses that
        itself, with its own figure), and a body padded with whitespace or
        pretty-printed past 5 MB is refused here though it would store under
        it (a 5.9 MB ``indent=4`` body of a document that stores at 2.9 MB).
        The viewer sends compact JSON, so only a script that pretty-prints
        sees the second; the message says what the limit is on.
        """
        path = request.url.path
        if request.method in ("PUT", "POST") and (path == "/api/saves" or path.startswith("/api/saves/")):
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_FILE_BYTES:
                # Read the rest of the body and throw it away, chunk by chunk
                # (a few kilobytes at a time, no JSON, no objects): answering
                # while the client is still sending resets the connection under
                # it, and it sees a broken pipe instead of the 507.
                with contextlib.suppress(ClientDisconnect):
                    async for _chunk in request.stream():
                        pass
                return JSONResponse(
                    {"detail": f"{int(declared) // 1024} kB exceeds the {MAX_FILE_BYTES // 1024} kB limit "
                               "(the limit is on the request body as sent: send compact JSON)"},
                    status_code=507,
                )
        return await call_next(request)

    # Starlette runs the middleware added LAST outermost.  The login is added after the
    # cap on saved documents, so a caller with no session is answered 401 before the cap
    # (and the server) reads any of a body that nginx lets through up to 64 MB.
    @app.middleware("http")
    async def require_login(request: Request, call_next):
        """Every route behind the login -- when either login is configured.

        Two ways in, checked in that order: a Viking Identity session, or the
        built-in user list.  With neither configured the app runs open, exactly
        as before -- local checkouts and the test suite need no ceremony.
        """
        auth: AuthManager = request.app.state.auth
        viking: VikingAuth = request.app.state.viking
        request.state.user = None
        request.state.viking_session = None
        if (
            not (auth.enabled or viking.enabled)
            or request.url.path in AUTH_EXEMPT_PATHS
            or (request.client is not None and request.client.host in LOOPBACK_HOSTS)
        ):
            return await call_next(request)
        if viking.enabled:
            # In a worker thread: renewing a session calls the identity
            # server, and a slow or absent one must not hold up the event
            # loop (every request, health checks included, waited behind it).
            session = await run_in_threadpool(viking.current, request.cookies.get(VIKING_COOKIE))
            if session is not None:
                request.state.user = session.user
                request.state.viking_session = session
                return await call_next(request)
        user = auth.current_user(request.cookies.get(COOKIE_NAME)) if auth.enabled else None
        if user is not None:
            request.state.user = user
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        # The query string is part of where they were going.  Dropping it
        # would land a signed-out visitor following
        # /open?volume=RC1&cells=1178 on a bare /open with nothing to open.
        wanted = request.url.path
        if request.url.query:
            wanted = f"{wanted}?{request.url.query}"
        if viking.enabled and request.url.path in SILENT_SIGN_IN_PATHS:
            return RedirectResponse(
                f"/auth/login?silent=1&next={quote(wanted, safe='/')}", status_code=302
            )
        return RedirectResponse(
            f"/login?next={quote(wanted, safe='/')}", status_code=302
        )

    def login_page(
        request: Request, next_path: str, error: str = "", status_code: int = 200
    ) -> HTMLResponse:
        viking: VikingAuth = request.app.state.viking
        auth: AuthManager = request.app.state.auth
        if viking.enabled:
            target = f"/auth/login?next={quote(next_path, safe='/')}"
            block = (
                f'<a class="viking" href="{escape(target, quote=True)}">Sign in with Viking</a>'
                '<p class="viking-note">Uses your Viking Identity account. '
                "Which volumes you can open is set there.</p>"
            )
            # The local form is the fallback, and offering it to someone who
            # has no local account is just a way to fail.  Hidden entirely
            # when no user list exists.
            local_state = "" if auth.enabled else "hidden"
            if auth.enabled:
                block += '<p class="or">or</p>'
        else:
            block, local_state = "", ""
        html = (TEMPLATE_DIR / "login.html").read_text(encoding="utf-8")
        html = (
            html.replace("__NEXT__", escape(next_path, quote=True))
            .replace("__ERROR__", escape(error))
            .replace("__VIKING__", block)
            .replace("__LOCAL_STATE__", local_state)
            # Focus the username box only when it is the thing to use.
            .replace("__AUTOFOCUS__", "" if viking.enabled else "autofocus")
        )
        return HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-store"})

    def safe_next(next_path: str) -> str:
        # A relative path only; "//host" or "https://..." would make the
        # login page an open redirect.  Backslashes too: several browsers
        # normalize "/\evil.example" to "//evil.example" and follow it
        # off-site, so a lone leading "/" is not enough of a check.
        if not next_path.startswith("/") or next_path.startswith("//"):
            return "/"
        if next_path.startswith("/\\") or "\\" in next_path.split("?", 1)[0]:
            return "/"
        return next_path

    @app.get("/login", include_in_schema=False, response_model=None)
    def login_form(
        request: Request, next: str = "/", error: str = ""
    ) -> HTMLResponse | RedirectResponse:
        auth: AuthManager = request.app.state.auth
        viking: VikingAuth = request.app.state.viking
        if not (auth.enabled or viking.enabled):
            return RedirectResponse(safe_next(next), status_code=302)
        already = (auth.enabled and auth.current_user(request.cookies.get(COOKIE_NAME))) or (
            viking.enabled and viking.store.load(request.cookies.get(VIKING_COOKIE))
        )
        if already:
            return RedirectResponse(safe_next(next), status_code=302)
        return login_page(request, safe_next(next), _login_error(error))

    @app.post("/login", include_in_schema=False, response_model=None)
    def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: str = Form("/"),
    ) -> HTMLResponse | RedirectResponse:
        auth: AuthManager = request.app.state.auth
        token = auth.login(username.strip(), password)
        if token is None:
            log.warning("failed login for %r from %s", username.strip(),
                        request.client.host if request.client else "?")
            return login_page(
                request, safe_next(next), "Wrong username or password.", status_code=401
            )
        response = RedirectResponse(safe_next(next), status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response

    @app.get("/logout", include_in_schema=False)
    def logout(request: Request) -> RedirectResponse:
        viking: VikingAuth = request.app.state.viking
        # A Viking session has to end at the identity server too, or the next
        # click on "Sign in with Viking" signs the same person straight back
        # in and the button looks broken.
        if viking.enabled and request.cookies.get(VIKING_COOKIE):
            return viking_logout(request)
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(COOKIE_NAME)
        return response

    # -- Viking Identity Server ------------------------------------------------

    @app.get("/auth/login", include_in_schema=False, response_model=None)
    async def viking_login(request: Request, next: str = "/", silent: bool = False):
        """Hand the browser to the identity server's own login page.

        Authlib puts ``state``, ``nonce`` and the PKCE verifier in the
        handshake cookie; where to land afterwards goes there too, so it
        cannot be steered by whoever sends the user to the callback.

        ``silent`` asks the identity server to reuse the session the browser
        already holds there and to show nothing (``prompt=none``).  It answers
        with a code, or with ``login_required`` -- which the callback turns
        into our ordinary login page, ``next`` intact.
        """
        viking: VikingAuth = request.app.state.viking
        if not viking.enabled:
            return RedirectResponse("/login", status_code=302)
        target = safe_next(next)
        login_page_url = f"/login?next={quote(target, safe='/')}"
        extra: dict[str, str] = {}
        request.session.pop("viking_silent", None)
        if silent:
            last = request.session.get("viking_silent_at")
            now = time.time()
            if isinstance(last, (int, float)) and now - last < SILENT_RETRY_GUARD:
                log.warning(
                    "silent viking sign-in for %r repeated after %.0f s: the last one "
                    "did not leave a session behind; showing the login page",
                    target, now - last,
                )
                return RedirectResponse(login_page_url, status_code=302)
            request.session["viking_silent_at"] = now
            request.session["viking_silent"] = True
            extra["prompt"] = "none"
        request.session["viking_next"] = target
        log.info(
            "viking sign-in started from %s%s, will return to %r",
            request.client.host if request.client else "?",
            " (silent)" if silent else "", target,
        )
        try:
            return await viking.client.authorize_redirect(
                request, viking.config.redirect_uri, **extra
            )
        except Exception as exc:  # Authlib raises its own hierarchy
            log.error("could not start Viking sign-in: %s", exc)
            return RedirectResponse(
                f"{login_page_url}&error=viking_unreachable", status_code=302
            )

    @app.get("/auth/callback", include_in_schema=False, response_model=None)
    async def viking_callback(request: Request):
        """Where the identity server sends the browser back, with a code.

        The code exchange happens here, server to server; the browser never
        sees the client secret and never touches the token endpoint.
        """
        viking: VikingAuth = request.app.state.viking
        if not viking.enabled:
            return RedirectResponse("/login", status_code=302)
        silent = bool(request.session.pop("viking_silent", False))
        error = request.query_params.get("error")
        if error:
            # No code will be exchanged, so Authlib never gets to clear this
            # handshake's entry; left alone it rides in the cookie for an hour.
            state = request.query_params.get("state") or ""
            for key in [k for k in request.session if k.startswith("_state_")]:
                if state and key.endswith(f"_{state}"):
                    request.session.pop(key, None)
            if silent and error in SILENT_REFUSALS:
                # Not a failure: the browser simply holds no session at the
                # identity server.  The login page takes over, with no error
                # on it and the way back kept.  The retry guard is lifted
                # because nothing can loop from here.
                request.session.pop("viking_silent_at", None)
                next_path = safe_next(request.session.pop("viking_next", "/") or "/")
                log.info(
                    "silent viking sign-in: no session to reuse (%s); login page for %r",
                    error, next_path,
                )
                return RedirectResponse(
                    f"/login?next={quote(next_path, safe='/')}", status_code=302
                )
            log.info("viking sign-in refused: %s", error)
            return RedirectResponse("/login?error=viking_denied", status_code=302)
        # A crawler that finds this path fetches it bare.  It is not a failed
        # sign-in and must not be logged as one: a Slack link-preview bot did
        # exactly this on 2026-09-10 and left a "code exchange failed" line
        # that read like a real user hitting a real bug.
        if not request.query_params.get("code"):
            log.info(
                "bare /auth/callback from %s (%s) -- not a sign-in attempt",
                request.client.host if request.client else "?",
                request.headers.get("user-agent", "")[:60],
            )
            return RedirectResponse("/login", status_code=302)
        # Whether the handshake cookie came back at all is the difference
        # between "this browser never started a sign-in here" and "the state
        # really did not match", and the two have completely different fixes.
        had_state = any(k.startswith("_state_") for k in request.session)
        try:
            token = await viking.client.authorize_access_token(request)
        except Exception as exc:
            log.warning(
                "viking code exchange failed (handshake cookie %s, from %s): %s",
                "present" if had_state else "MISSING",
                request.client.host if request.client else "?",
                exc,
            )
            return RedirectResponse("/login?error=viking", status_code=302)
        try:
            # Two calls to the identity server, off the event loop like the
            # renewal in the middleware.
            session = await run_in_threadpool(viking.begin_session, token)
        except VikingError as exc:
            log.error("viking session could not be started: %s", exc)
            return RedirectResponse("/login?error=viking_unreachable", status_code=302)
        next_path = safe_next(request.session.pop("viking_next", "/") or "/")
        if viking.config.gate_volumes and not session.volumes:
            # Signed in, but nothing shared with them: say so on the login
            # page rather than dropping them into an app with an empty volume
            # list and no explanation.
            log.info(
                "viking user %r has no accessible volumes (unsupported: %s)",
                session.user, ", ".join(session.unsupported) or "none",
            )
            viking.sign_out(session.sid)
            code = "viking_unsupported_volumes" if session.unsupported else "viking_no_volumes"
            return RedirectResponse(f"/login?error={code}", status_code=302)
        log.info(
            "viking sign-in complete%s: %r (%s) -> %d volume(s): %s",
            " (silent, identity-server session reused)" if silent else "",
            session.user, session.user_id, len(session.volumes),
            ", ".join(sorted(session.volumes)) or "none",
        )
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            VIKING_COOKIE,
            session.sid,
            max_age=VIKING_SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response

    @app.get("/auth/logout", include_in_schema=False)
    def viking_logout(request: Request) -> RedirectResponse:
        viking: VikingAuth = request.app.state.viking
        sid = request.cookies.get(VIKING_COOKIE)
        if sid:
            viking.sign_out(sid)
        # End the session at the identity server as well when it says how.
        target = viking.end_session_url() if viking.enabled else None
        response = RedirectResponse(target or "/login", status_code=302)
        response.delete_cookie(VIKING_COOKIE)
        response.delete_cookie(COOKIE_NAME)
        return response

    def welcome_page(
        code: str, greeting: str, error: str = "", *,
        form: bool = True, status_code: int = 200,
    ) -> HTMLResponse:
        html = (TEMPLATE_DIR / "welcome.html").read_text(encoding="utf-8")
        html = (
            html.replace("__CODE__", escape(code, quote=True))
            .replace("__GREETING__", escape(greeting))
            .replace("__ERROR__", escape(error))
            .replace("__FORM_STATE__", "" if form else "hidden")
        )
        return HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-store"})

    INVALID_INVITE = (
        "This invite link is invalid, expired, or already used. "
        "Ask for a new one."
    )

    @app.get("/welcome", include_in_schema=False)
    def welcome_form(request: Request, code: str = "") -> HTMLResponse:
        auth: AuthManager = request.app.state.auth
        username = auth.users.find_invite(code) if code else None
        if username is None:
            return welcome_page("", INVALID_INVITE, form=False, status_code=404)
        return welcome_page(code, f"Welcome, {username} — set up your account")

    @app.post("/welcome", include_in_schema=False, response_model=None)
    def welcome_submit(
        request: Request,
        code: str = Form(...),
        password: str = Form(...),
        confirm: str = Form(...),
    ) -> HTMLResponse | RedirectResponse:
        auth: AuthManager = request.app.state.auth
        username = auth.users.find_invite(code)
        if username is None:
            return welcome_page("", INVALID_INVITE, form=False, status_code=404)
        greeting = f"Welcome, {username} — set up your account"
        if password != confirm:
            return welcome_page(code, greeting, "Those passwords do not match.", status_code=400)
        if len(password) < MIN_PASSWORD_LENGTH:
            return welcome_page(
                code, greeting,
                f"Use at least {MIN_PASSWORD_LENGTH} characters.", status_code=400,
            )
        redeemed = auth.redeem_invite(code, password)
        if redeemed is None:  # spent between the check above and now
            return welcome_page("", INVALID_INVITE, form=False, status_code=404)
        username, token = redeemed
        log.info("invite redeemed for %r", username)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        """Serve the shell with its asset URLs stamped by package version.

        Stamping :func:`asset_version` into the entry URLs makes a change a
        cache miss.  It only covers the URLs written in the HTML, though --
        the ES modules ``app.js`` imports carry no query of their own -- so
        :class:`RevalidatingStatic` is what keeps the rest honest.
        """
        return shell(request)

    @app.get("/open", include_in_schema=False, response_model=None)
    def open_deep_link(
        request: Request,
        volume: str = "",
        cells: str = "",
        technique: str = "",
        location: str = "",
    ) -> HTMLResponse:
        """Open a volume, and optionally some cells, from a link elsewhere.

        The contract agreed with the Viking authors on 2026-09-10, so that the
        Viking Identity site and Viking's own context menus can link straight
        into a render::

            /open?volume=RC1&cells=1178,5678&technique=voxel2&location=769111

        ``volume`` is the **identity server's** name for it (``InferiorMonkey``,
        ``NM``, ``cped``) -- they deliberately do not encode our renderer ids --
        so it is translated here.  Our own names are accepted too, because a
        link a user copies out of this app's address bar should keep working.

        Nothing here is exempt from the login: an unauthenticated visitor is
        sent through sign-in and lands back on this same URL, query string and
        all.  Everything that can go wrong -- an unknown volume, one we cannot
        render, one this account was not granted -- is reported on the page
        rather than dropping someone into an empty viewer with no explanation.
        """
        payload = resolve_open_link(request, volume, cells, technique, location)
        return shell(request, open_link=payload)

    @app.get("/open-in-viking", include_in_schema=False, response_model=None)
    def open_in_viking(
        request: Request,
        volume: str = "",
        location: str = "",
        page: int = 0,
        client: str = "viking",
        then: str = "",
    ) -> RedirectResponse | PlainTextResponse | HTMLResponse:
        """Send the browser to Viking, to open this place there.

        The other direction of ``/open``::

            /open-in-viking?volume=RC2&location=769111
            /open-in-viking?volume=RC2&location=200549,-160700,1111,8

        The answer is a 302 to :func:`viking_launch_target`: by default
        ``viking://open?volumeName=...&location=...``, which the operating
        system hands to Viking, or (launch mode ``code``) the management site's
        ``CreateCode``, which redirects there with a one-use code once the
        person has a session on their page.  The interface opens this in its
        own tab, so the render the user is looking at is never navigated away.

        ``client=test`` is the same link for the unsigned "Viking Test" build
        (``viking-test://open?...``).  With ``page=1``, ``then=viking`` or
        ``then=test`` names the other client for the page to try when nothing
        answers the first; the bare redirect has no use for it.

        ``?page=1`` -- what the interface asks for -- answers with
        :func:`viking_launch_page` instead of the redirect: the same
        navigation, made from our own origin, so that it can refuse on a
        machine Viking cannot run on and afterwards notice a hand-off that
        nothing answered.  Without it the endpoint is the bare 302 it has
        always been, which is also what to compare against when a launch is
        being diagnosed.
        """
        try:
            client = _launch_client(client)
            fallback = _launch_client(then, "fallback") if (then or "").strip() else ""
            if fallback == client:
                raise LaunchRefused(400, f"Falling back from {client} to {client} is no fallback.")
            target = viking_launch_target(request, volume, location, client)
            then_target = (
                viking_launch_target(request, volume, location, fallback) if fallback else ""
            )
        except LaunchRefused as refusal:
            return PlainTextResponse(refusal.message, status_code=refusal.status_code)
        log.info(
            "open in viking: %s [%s%s] -> %s",
            getattr(request.state, "user", None) or "?", client,
            f" then {fallback}" if fallback else "", target.split("?", 1)[1],
        )
        if not page:
            return RedirectResponse(target, status_code=302)
        return viking_launch_page(
            target, _launch_where(volume, location), client, fallback, then_target
        )

    def shell(request: Request, open_link: dict | None = None) -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        # Into an attribute, so escaped like every other stamp: a user name
        # comes from the identity server or an invite, and a quote in one
        # would end the attribute.
        user = getattr(request.state, "user", None) or ""
        html = html.replace("__SBFSEM_USER__", escape(str(user), quote=True))
        # Stamped into an attribute the frontend reads once on boot.  JSON
        # inside an HTML attribute, so it is escaped for quotes, and "/" is
        # escaped as well so no value can close the <span> it rides in.
        blob = json.dumps(open_link, separators=(",", ":")).replace("/", r"\/") if open_link else ""
        html = html.replace("__SBFSEM_OPEN__", escape(blob, quote=True))
        # The shell itself must revalidate too: a heuristically cached
        # index.html carries YESTERDAY'S version stamp, which faithfully
        # loads yesterday's app -- the one stale link that defeats all the
        # stamping downstream of it.
        return HTMLResponse(
            html.replace("__SBFSEM_VERSION__", asset_version()),
            headers={"Cache-Control": "no-cache"},
        )

    app.mount("/static", RevalidatingStatic(directory=STATIC_DIR), name="static")
    return app


app = create_app()
