"""Sign-in and volume permissions through the Viking Identity Server.

Viking is the annotation environment these volumes are traced in: it loads the
EM images, takes the annotations, and writes the database this application
reads.  Its identity server is therefore the authoritative answer to both
questions this application used to answer for itself -- *who is this* and
*which volumes may they see* -- so when it is configured, it wins.

This application is a **confidential OpenID Connect client**.  The user signs
in on the identity server's own page; the browser comes back to
``/auth/callback`` with an authorization code; the server exchanges that code
for tokens over its own TLS connection.  The client secret never reaches the
browser and the browser never calls the token endpoint.

Where things live
-----------------

``$SBFSEM_AUTH_DIR/viking-sessions/``
    One JSON file per signed-in browser, holding the tokens and the last
    permissions answer.  On disk rather than in memory because every change to
    this application redeploys it (ground rule 5) and restarting a process
    should not sign the lab out.  It is a subdirectory of the existing auth
    directory on purpose: that path is already created, chowned and listed in
    the unit's ``ReadWritePaths``, so this needs no new deployment edits
    (ground rule 10).

``$SBFSEM_AUTH_DIR/viking-state.key``
    Signing key for the short-lived cookie Authlib keeps the OAuth ``state``
    and PKCE verifier in while the user is away at the login page.

Configuration
-------------

Off unless ``SBFSEM_VIKING_AUTH`` is set *and* a client secret is present, so
a local checkout and the test suite behave exactly as they did before.  See
``docs/viking-identity.md``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from sbfsem.core.volumes import from_viking_name

log = logging.getLogger(__name__)

#: Cookie naming the server-side session.  Distinct from the local-login
#: cookie so the two schemes can coexist and be reasoned about separately.
VIKING_COOKIE = "sbfsem_viking"

#: How long a signed-in browser stays signed in without re-authorizing.  The
#: refresh token renews the access token silently within this window.
SESSION_TTL = 30 * 24 * 3600

#: How long an ``AccessibleVolumes`` answer is reused before asking again.
#: Short enough that a grant made in the management site shows up in a few
#: minutes without anyone signing out.
PERMISSION_TTL = 300

#: How long a cached permissions answer may go on being used when the identity
#: server cannot be reached.  Beyond this the session is dropped: a permission
#: revoked while the server was down must not be honored indefinitely.
PERMISSION_MAX_STALE = 24 * 3600

#: After a permissions refresh fails (the identity server is down or slow), how
#: long the last good answer is used without asking again.  Without it every
#: request from the browser paid the failed call again, each up to its ten
#: second timeout.
PERMISSION_RETRY = 60

#: Renew the access token this many seconds before it actually expires, so a
#: request never races the expiry.
REFRESH_MARGIN = 60

DEFAULT_AUTHORITY = "https://identity.codepharm.net:5001/"
DEFAULT_PERMISSIONS_API = "https://identity.codepharm.net:6001/"
DEFAULT_CLIENT_ID = "sbfsem-tools"
DEFAULT_REDIRECT_URI = "https://sbfsem-tools.com/auth/callback"
DEFAULT_POST_LOGOUT_URI = "https://sbfsem-tools.com/"
#: Where a Windows machine without Viking gets it.  https rather than the http
#: form the feed advertises: Chrome refuses an insecure download of an
#: executable from a secure page, and the same file is served over both.
VIKING_INSTALL_URL = "https://websvc.codepharm.net/Software/Viking/Viking-win-Setup.exe"

#: The management site's launch endpoint.  It mints a one-use code under the
#: browser's own identity-server session and redirects to ``viking://open``;
#: we only ever send the browser there, never a request of our own.
DEFAULT_LAUNCH_URL = "https://identity.codepharm.net:4001/VikingLaunch/CreateCode"
#: The most zoomed-out view "Open in Viking" will ask for.  Viking applies the
#: downsample it is sent with no upper limit (``VikingMain.cs``; only a floor
#: of 0.01), and starts at 32 when it is sent none -- which is what a launch by
#: Location ID gets.  Our whole-cell views correspond to 700 to 1000: on RC2
#: (2.18 nm per pixel) that is a 3 mm field for a 0.25 mm volume, a speck.  32
#: is a field of about 100 µm, and makes both kinds of launch land alike.
DEFAULT_LAUNCH_MAX_DOWNSAMPLE = 32.0
#: How "Open in Viking" hands a place to the client (``SBFSEM_VIKING_LAUNCH_MODE``).
#: ``direct`` sends ``viking://open?volumeName=&location=`` with no launch code:
#: a Viking already open takes the link over its pipe as before, and a cold
#: start shows Viking's own sign-in (which remembers the account), selects the
#: volume by name and goes to the place (``Program.cs``, ``LoginWindow``, Legacy
#: branch).  ``code`` goes through ``DEFAULT_LAUNCH_URL`` for a one-use launch
#: code, which should spare the sign-in, but since Viking 1.2.67 the annotation
#: service refuses the token a code is exchanged for, so a cold start by code
#: opens a Viking that can load nothing.  ``direct`` is the default until
#: [Viking-Server] deploys that fix; then set ``code``.
LAUNCH_MODES = ("direct", "code")
DEFAULT_LAUNCH_MODE = "direct"

#: The scopes registered for this client.  Volume-specific scopes such as
#: ``RC1.Read`` are deliberately absent: the Permissions API authorizes on the
#: signed-in user, not on the scopes in the token, and asking for one that has
#: not been granted makes the identity server refuse the whole request.
DEFAULT_SCOPES = "openid profile Viking.Annotation offline_access"


class VikingError(RuntimeError):
    """The identity server could not be reached, or answered unusably."""


class VikingAuthExpired(VikingError):
    """The access token was rejected; refresh it and retry, or sign in again."""


class VikingUnreachable(VikingError):
    """The identity server could not be asked, or what it answered is not an answer.

    Told apart from a refusal (a 4xx to a refresh, which says the person must sign
    in again) because an outage is no verdict on the session: it goes on, on its
    last known permissions, until ``PERMISSION_MAX_STALE``.
    """


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class VikingConfig:
    """Everything about the identity server, read once from the environment."""

    authority: str = DEFAULT_AUTHORITY
    permissions_api: str = DEFAULT_PERMISSIONS_API
    client_id: str = DEFAULT_CLIENT_ID
    client_secret: str = ""
    redirect_uri: str = DEFAULT_REDIRECT_URI
    post_logout_uri: str = DEFAULT_POST_LOGOUT_URI
    scopes: str = DEFAULT_SCOPES
    launch_url: str = DEFAULT_LAUNCH_URL
    launch_max_downsample: float = DEFAULT_LAUNCH_MAX_DOWNSAMPLE
    launch_mode: str = DEFAULT_LAUNCH_MODE
    #: Gate the volume list on ``AccessibleVolumes``.  Separable from sign-in
    #: so authentication can be turned on before authorization is.
    gate_volumes: bool = True
    requested: bool = False

    @classmethod
    def from_env(cls) -> VikingConfig:
        def url(name: str, default: str) -> str:
            value = (os.environ.get(name) or default).strip()
            return value if value.endswith("/") else value + "/"

        def positive(name: str, default: float) -> float:
            try:
                value = float(os.environ.get(name) or default)
            except ValueError:
                return default
            return value if math.isfinite(value) and value > 0 else default

        return cls(
            authority=url("SBFSEM_VIKING_AUTHORITY", DEFAULT_AUTHORITY),
            permissions_api=url("SBFSEM_VIKING_PERMISSIONS_API", DEFAULT_PERMISSIONS_API),
            client_id=(os.environ.get("SBFSEM_VIKING_CLIENT_ID") or DEFAULT_CLIENT_ID).strip(),
            client_secret=(os.environ.get("SBFSEM_VIKING_CLIENT_SECRET") or "").strip(),
            redirect_uri=(
                os.environ.get("SBFSEM_VIKING_REDIRECT_URI") or DEFAULT_REDIRECT_URI
            ).strip(),
            post_logout_uri=(
                os.environ.get("SBFSEM_VIKING_POST_LOGOUT_URI") or DEFAULT_POST_LOGOUT_URI
            ).strip(),
            scopes=(os.environ.get("SBFSEM_VIKING_SCOPES") or DEFAULT_SCOPES).strip(),
            launch_url=(
                os.environ.get("SBFSEM_VIKING_LAUNCH_URL") or DEFAULT_LAUNCH_URL
            ).strip(),
            launch_max_downsample=positive(
                "SBFSEM_VIKING_LAUNCH_MAX_DOWNSAMPLE", DEFAULT_LAUNCH_MAX_DOWNSAMPLE
            ),
            launch_mode=next(
                (m for m in LAUNCH_MODES
                 if m == (os.environ.get("SBFSEM_VIKING_LAUNCH_MODE") or "").strip().lower()),
                DEFAULT_LAUNCH_MODE,
            ),
            gate_volumes=_flag("SBFSEM_VIKING_GATE_VOLUMES", True),
            requested=_flag("SBFSEM_VIKING_AUTH"),
        )

    @property
    def metadata_url(self) -> str:
        """Discovery document.  Endpoints are read from it, never hard-coded."""
        return f"{self.authority}.well-known/openid-configuration"

    @property
    def enabled(self) -> bool:
        """Whether to offer Viking sign-in at all.

        Asking for it without a secret is a misconfiguration, not a request to
        run without one: a confidential client cannot complete the code
        exchange, so the button would lead to a dead end.  It is logged and
        the feature stays off.
        """
        if not self.requested:
            return False
        if not self.client_secret:
            log.error(
                "SBFSEM_VIKING_AUTH is set but SBFSEM_VIKING_CLIENT_SECRET is empty; "
                "Viking sign-in stays off"
            )
            return False
        return True


# --- what a signed-in browser is ---------------------------------------------


@dataclass
class VikingSession:
    """One signed-in browser: who, which tokens, and what they may see."""

    sid: str
    user: str
    user_id: str
    access_token: str
    refresh_token: str | None
    #: Unix time the access token stops working.
    expires_at: float
    created_at: float
    #: ``AccessibleVolumes`` as last seen, canonical name -> permission list.
    volumes: dict[str, list[str]] = field(default_factory=dict)
    #: Identity-server volume names we have no renderer for.  Not shown in
    #: the interface, but kept and reported by ``/api/viking/session`` so a
    #: grant that has no renderer here is recorded rather than lost.
    unsupported: list[str] = field(default_factory=list)
    #: Unix time ``volumes`` was last refreshed; 0 means never.
    volumes_at: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "user": self.user,
            "user_id": self.user_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "volumes": self.volumes,
            "unsupported": self.unsupported,
            "volumes_at": self.volumes_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> VikingSession:
        return cls(
            sid=str(data["sid"]),
            user=str(data.get("user") or ""),
            user_id=str(data.get("user_id") or ""),
            access_token=str(data.get("access_token") or ""),
            refresh_token=data.get("refresh_token") or None,
            expires_at=float(data.get("expires_at") or 0),
            created_at=float(data.get("created_at") or 0),
            volumes={str(k): [str(p) for p in v] for k, v in (data.get("volumes") or {}).items()},
            unsupported=[str(v) for v in (data.get("unsupported") or [])],
            volumes_at=float(data.get("volumes_at") or 0),
        )

    def expired(self, now: float | None = None) -> bool:
        clock = now if now is not None else time.time()
        return clock > self.created_at + SESSION_TTL

    def token_stale(self, now: float | None = None) -> bool:
        clock = now if now is not None else time.time()
        return clock >= self.expires_at - REFRESH_MARGIN

    def permissions_stale(self, now: float | None = None) -> bool:
        clock = now if now is not None else time.time()
        return clock >= self.volumes_at + PERMISSION_TTL

    def may_read(self, source: str) -> bool:
        """Whether this user may open a volume, named any way the API accepts.

        Canonicalizes first, because ``volumes`` is keyed canonically and every
        route takes aliases: ``i``, ``Inferior Monkey`` and
        ``NeitzInferiorMonkey`` are one volume and must give one answer.

        Volumes the identity server does not know (the bundled demo) are not
        its business and are never gated here.
        """
        from sbfsem.core.volumes import Volumes

        try:
            volume = Volumes.from_str(source)
        except (ValueError, TypeError):
            return False  # Not a volume at all; the route's own 404 follows.
        if volume.viking_name is None:
            return True
        return bool(self.volumes.get(volume.source))


class VikingSessionStore:
    """Server-side sessions, one small JSON file each.

    A random 32-byte id in an httpOnly cookie is the only thing the browser
    holds; the tokens never leave the server.  Files rather than memory so a
    redeploy -- which happens after every change -- does not sign everyone out.
    """

    def __init__(self, base: Path) -> None:
        self.base = base

    def _path(self, sid: str) -> Path:
        # The id is generated here and is hex, but a cookie is attacker-controlled
        # input: anything that is not a plain hex id cannot name a file.
        if not sid or len(sid) > 128 or any(c not in "0123456789abcdef" for c in sid):
            raise ValueError("malformed session id")
        return self.base / f"{sid}.json"

    def create(self, **fields: Any) -> VikingSession:
        # Nothing else ever removes a session that no browser will load again,
        # so until 2026-09-10 they simply accumulated.  One was observed that
        # day: Chrome's "Dangerous site" interstitial cancelled a callback
        # after the code exchange had already succeeded here, so the session
        # was written, holding a live refresh token, and its cookie never
        # reached a browser.  Sweeping on every sign-in bounds that.
        self.purge()
        session = VikingSession(sid=secrets.token_hex(32), created_at=time.time(), **fields)
        self.save(session)
        return session

    def save(self, session: VikingSession) -> None:
        path = self._path(session.sid)
        self.base.mkdir(parents=True, exist_ok=True)
        self.base.chmod(0o700)
        # A temporary file of this writer's own: requests of one browser now
        # run in threads, and two of them saving one session must not share it.
        tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            tmp.write_text(json.dumps(session.to_json()), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def load(self, sid: str | None) -> VikingSession | None:
        if not sid:
            return None
        try:
            raw = self._path(sid).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        try:
            session = VikingSession.from_json(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("discarding unreadable viking session: %s", exc)
            self.delete(sid)
            return None
        if session.expired():
            self.delete(sid)
            return None
        return session

    def delete(self, sid: str | None) -> None:
        if not sid:
            return
        with contextlib.suppress(OSError, ValueError):
            self._path(sid).unlink(missing_ok=True)

    def purge(self, now: float | None = None) -> int:
        """Delete sessions past their TTL.  Returns how many went."""
        clock = now if now is not None else time.time()
        gone = 0
        cutoff = clock - SESSION_TTL
        for path in self.base.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    gone += 1
            except OSError:
                continue
        return gone


# --- the Permissions Web API --------------------------------------------------


def _volume_url_name(volume: str) -> str:
    """Volume names with spaces are hyphenated in Permissions API URLs.

    ``gRPC RC1 Test`` is requested as ``gRPC-RC1-Test``.
    """
    return "-".join(volume.split())


class PermissionsClient:
    """The Permissions Web API on its own port, read with the access token.

    Access tokens here are *reference tokens* -- opaque strings, not JWTs, so
    nothing about them can be decoded locally.  Every question about a user's
    access is therefore a call.
    """

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.timeout = timeout

    @staticmethod
    def describe_token(token: str) -> str:
        """The token's *shape*, never the token.

        Their integration note says access tokens are reference tokens --
        opaque strings.  A client registered without
        ``AccessTokenType.Reference`` issues a JWT instead, which an API set
        up to introspect reference tokens will refuse.  Telling the two apart
        costs nothing and names that mistake immediately.
        """
        parts = token.split(".")
        if len(parts) == 3 and all(parts):
            return f"JWT-shaped, {len(token)} chars"
        return f"opaque, {len(token)} chars"

    def _get(self, path: str, token: str) -> httpx.Response:
        try:
            response = httpx.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise VikingError(f"permissions API unreachable: {exc}") from exc
        if response.status_code == 401:
            # ASP.NET puts the actual reason in WWW-Authenticate --
            # invalid_token with a description naming the failed check
            # (signature, audience, expiry).  Without it a 401 is just a
            # shrug, and this one cost an evening.
            detail = response.headers.get("www-authenticate", "")
            body = (response.text or "").strip()[:200]
            raise VikingAuthExpired(
                "access token rejected by the permissions API"
                + (f" [{detail}]" if detail else "")
                + (f" body={body!r}" if body else "")
            )
        return response

    def current_user(self, token: str) -> str:
        response = self._get("Permissions/CurrentUser", token)
        if response.status_code != 200:
            raise VikingError(f"CurrentUser returned {response.status_code}")
        return _as_text(response)

    def current_user_id(self, token: str) -> str:
        response = self._get("Permissions/CurrentUserId", token)
        if response.status_code != 200:
            raise VikingError(f"CurrentUserId returned {response.status_code}")
        return _as_text(response)

    def accessible_volumes(self, token: str) -> list[dict[str, Any]]:
        """Every volume this user may touch, as the API's own records.

        The response is an object keyed by volume id; the ids are the identity
        server's, not ours, so only the values are of use here.
        """
        response = self._get("Permissions/AccessibleVolumes", token)
        if response.status_code == 404:
            # Identity 1.1.0 (deployed 2026-10-08) no longer serves it; every
            # Viking sign-in failed until this fallback.
            return self.accessible_volume_tree(token)
        if response.status_code != 200:
            raise VikingError(f"AccessibleVolumes returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VikingError(f"AccessibleVolumes was not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise VikingError("AccessibleVolumes was not an object keyed by volume id")
        return [v for v in payload.values() if isinstance(v, dict)]

    def accessible_volume_tree(self, token: str) -> list[dict[str, Any]]:
        """The same records from ``UserAccessibleVolumeTree``, flattened.

        Organizational units hold ``volumes`` and may nest ``children``.  The
        endpoint is ``[AllowAnonymous]``: a token it cannot place gets the
        public subset, which is what an anonymous visitor may read anyway.
        """
        response = self._get("Permissions/UserAccessibleVolumeTree", token)
        if response.status_code != 200:
            raise VikingError(f"UserAccessibleVolumeTree returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VikingError(f"UserAccessibleVolumeTree was not JSON: {exc}") from exc
        if not isinstance(payload, list):
            raise VikingError("UserAccessibleVolumeTree was not a list")
        records: list[dict[str, Any]] = []

        def walk(nodes: Any) -> None:
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                records.extend(v for v in node.get("volumes") or [] if isinstance(v, dict))
                walk(node.get("children"))

        walk(payload)
        return records

    def volume_permissions(self, token: str, volume: str) -> list[str]:
        """``Read``/``Annotate``/``Review`` on one volume; empty means no access."""
        response = self._get(f"Permissions/resource/{_volume_url_name(volume)}", token)
        if response.status_code == 404:
            raise VikingError(f"no such volume: {volume}")
        if response.status_code != 200:
            raise VikingError(f"resource/{volume} returned {response.status_code}")
        payload = response.json()
        return [str(p) for p in payload] if isinstance(payload, list) else []


def _as_text(response: httpx.Response) -> str:
    """These endpoints return a JSON string, which is not the same as text."""
    try:
        value = response.json()
    except ValueError:
        return response.text.strip().strip('"')
    return str(value)


def sort_volumes(records: list[dict[str, Any]]) -> tuple[dict[str, list[str]], list[str]]:
    """Split an ``AccessibleVolumes`` answer into what we can and cannot open.

    Returns ``(canonical name -> permissions, identity-server names we have no
    renderer for)``.  The second half is not an error: the identity server
    governs two dozen volumes and this tool renders eight of them.
    """
    mine: dict[str, list[str]] = {}
    unsupported: list[str] = []
    for record in records:
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        permissions = [str(p) for p in (record.get("permissions") or [])]
        if not permissions:
            # Present but with nothing granted: the same as no access.
            continue
        canonical = from_viking_name(name)
        if canonical is None:
            # Identity 1.1.0 lets several volumes (RC1, RC1-Internal, ...)
            # share one annotation server; that name is the one we know.
            metadata = record.get("metadata")
            server = metadata.get("AnnotationServerName") if isinstance(metadata, dict) else None
            canonical = from_viking_name(server) if server else None
        if canonical is None:
            unsupported.append(name)
        else:
            held = mine.setdefault(canonical, [])
            held.extend(p for p in permissions if p not in held)
    return mine, sorted(unsupported)


# --- the piece the app talks to ----------------------------------------------


class VikingAuth:
    """Sign-in through the identity server, and what the signed-in user may see.

    Authlib drives the browser half of the flow -- ``state``, ``nonce``, the
    PKCE verifier and the id_token check -- because getting any of those subtly
    wrong is how OpenID Connect clients go wrong.  Everything afterwards (token
    refresh, permissions, the session) is plain :mod:`httpx` against endpoints
    read from the discovery document.
    """

    def __init__(self, auth_dir: Path, config: VikingConfig | None = None) -> None:
        self.config = config or VikingConfig.from_env()
        self.auth_dir = auth_dir
        self.store = VikingSessionStore(auth_dir / "viking-sessions")
        self.permissions = PermissionsClient(self.config.permissions_api)
        self._oauth: Any = None
        self._metadata: dict[str, Any] | None = None
        # Renewing one session at a time (a request that finds it being
        # renewed uses the answer it has), and when each session's last
        # permissions refresh failed, so the next try waits PERMISSION_RETRY.
        self._renewing: dict[str, threading.Lock] = {}
        self._refresh_failed: dict[str, float] = {}
        self._guard = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    # -- the OAuth client ---------------------------------------------------

    @property
    def client(self) -> Any:
        """The Authlib client, registered on first use.

        Lazy so that importing this module -- which the test suite does with
        the feature off -- never reaches for the network or the key file.
        """
        if self._oauth is None:
            from authlib.integrations.starlette_client import OAuth

            oauth = OAuth()
            oauth.register(
                name="viking",
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                server_metadata_url=self.config.metadata_url,
                client_kwargs={
                    "scope": self.config.scopes,
                    "code_challenge_method": "S256",
                },
            )
            self._oauth = oauth
        return self._oauth.viking

    def state_secret(self) -> str:
        """Key for the cookie holding ``state`` and the PKCE verifier.

        Kept on disk so a redeploy in the seconds a user spends on the login
        page does not strand them with an unverifiable callback.
        """
        path = self.auth_dir / "viking-state.key"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            secret = secrets.token_urlsafe(32)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(secret, encoding="utf-8")
            path.chmod(0o600)
            log.info("created viking state key %s", path)
            return secret

    def metadata(self) -> dict[str, Any]:
        """The discovery document, fetched once per process."""
        if self._metadata is None:
            try:
                response = httpx.get(self.config.metadata_url, timeout=10.0)
                response.raise_for_status()
                self._metadata = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise VikingError(f"discovery document unreadable: {exc}") from exc
        return self._metadata

    def endpoint(self, name: str) -> str:
        value = self.metadata().get(name)
        if not isinstance(value, str) or not value:
            raise VikingError(f"discovery document has no {name}")
        return value

    # -- signing in ---------------------------------------------------------

    def begin_session(self, token: dict[str, Any]) -> VikingSession:
        """Turn a fresh token response into a stored session.

        The signed-in identity comes from the Permissions API rather than from
        the id_token: it is the same service that answers every later question
        about this user, so agreeing with it is what matters.
        """
        access_token = str(token.get("access_token") or "")
        if not access_token:
            raise VikingError("token response carried no access_token")
        try:
            user = self.permissions.current_user(access_token)
        except VikingAuthExpired as exc:
            # The identity server minted this token seconds ago, so "expired"
            # is not credible.  Ask the issuer's own userinfo endpoint about
            # the same token: if that accepts it, the token is fine and the
            # permissions API is what needs looking at.
            log.error(
                "permissions API refused a token the issuer just minted "
                "(%s, scope=%r): %s -- issuer userinfo says: %s",
                self.permissions.describe_token(access_token),
                token.get("scope", "?"),
                exc,
                self.probe_userinfo(access_token),
            )
            log.error(
                "anonymous-capable endpoint on the same API, same token: %s "
                "(anonymous callers see 3: Mosaics, RC1, RPC1 -- more than that "
                "means the token DID identify the user)",
                self.probe_volume_tree(access_token),
            )
            raise
        user_id = self.permissions.current_user_id(access_token)
        session = self.store.create(
            user=user,
            user_id=user_id,
            access_token=access_token,
            refresh_token=token.get("refresh_token"),
            expires_at=_expiry(token),
        )
        self.refresh_permissions(session)
        log.info("viking sign-in: %r (%s)", session.user, session.user_id)
        return session

    def probe_volume_tree(self, access_token: str) -> str:
        """Does the permissions API resolve this token to a *user*?

        ``UserAccessibleVolumeTree`` is the one endpoint there marked
        ``[AllowAnonymous]``, and it answers either way: the signed-in user's
        volumes when it can identify them, a public subset when it cannot.
        Counting what comes back with the token attached separates "the token
        never authenticated" from "it authenticated but the API could not map
        it to an account" -- which are different bugs on their side, and a
        bare 401 looks identical for both.
        """
        try:
            response = httpx.get(
                f"{self.permissions.base_url}Permissions/UserAccessibleVolumeTree",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            return f"unreachable ({exc})"
        if response.status_code != 200:
            return f"{response.status_code}"
        try:
            payload = response.json()
        except ValueError:
            return "200 but unreadable"
        names: list[str] = []

        def walk(nodes: Any) -> None:
            for node in nodes or []:
                for volume in node.get("volumes") or []:
                    if volume.get("name"):
                        names.append(str(volume["name"]))
                walk(node.get("children"))

        walk(payload if isinstance(payload, list) else [])
        return f"200 with {len(names)} volume(s): {', '.join(sorted(names)) or 'none'}"

    def probe_userinfo(self, access_token: str) -> str:
        """Does the *issuer* accept this token?  Diagnosis only, never a gate.

        Run when the permissions API rejects a freshly minted token.  Their
        two services are on different ports and validate independently, so
        "userinfo accepts it, permissions does not" localizes the fault to
        one of them without anyone reading a log on the other side.
        """
        try:
            response = httpx.get(
                self.endpoint("userinfo_endpoint"),
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
        except (httpx.HTTPError, VikingError) as exc:
            return f"unreachable ({exc})"
        if response.status_code == 200:
            return "200 OK -- the token is good; the permissions API is the problem"
        return (
            f"{response.status_code}"
            + (f" [{response.headers.get('www-authenticate')}]"
               if response.headers.get("www-authenticate") else "")
        )

    def refresh_token(self, session: VikingSession) -> bool:
        """Renew the access token.  ``False`` means the user must sign in again.

        A refresh returns a *new* refresh token; the old one is discarded, as
        the identity server expects.

        ``False`` is the identity server's refusal (a 4xx: the grant is spent or
        revoked) or a session with nothing to refresh with.  An identity server
        that could not be asked, or answered with an error, a page that is not
        JSON or a token response with no token, raises :class:`VikingUnreachable`
        instead: that is an outage, which is no reason to end a session.
        """
        if not session.refresh_token:
            return False
        try:
            response = httpx.post(
                self.endpoint("token_endpoint"),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": session.refresh_token,
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                },
                timeout=15.0,
            )
        except (httpx.HTTPError, VikingError) as exc:
            raise VikingUnreachable(f"token refresh could not be asked: {exc}") from exc
        if 400 <= response.status_code < 500:
            log.info("token refresh for %r refused (%s)", session.user, response.status_code)
            return False
        if response.status_code != 200:
            raise VikingUnreachable(f"token endpoint answered {response.status_code}")
        try:
            token = response.json()
        except ValueError as exc:
            raise VikingUnreachable("token endpoint answered with something that is not JSON") from exc
        access_token = str(token.get("access_token") or "") if isinstance(token, dict) else ""
        if not access_token:
            raise VikingUnreachable("token endpoint answered without an access token")
        session.access_token = access_token
        session.refresh_token = token.get("refresh_token") or session.refresh_token
        session.expires_at = _expiry(token)
        self.store.save(session)
        return True

    def refresh_permissions(self, session: VikingSession) -> None:
        """Ask the Permissions API which volumes this user may open."""
        records = self.permissions.accessible_volumes(session.access_token)
        volumes, unsupported = sort_volumes(records)
        if not volumes and session.volumes:
            # A user who could open volumes five minutes ago and now has none
            # is far likelier a changed answer than revoked rights; ride it out
            # like an unreachable server, on the last good list.
            raise VikingError(
                "permissions answer has no volumes we can open"
                + (f" (unsupported: {', '.join(unsupported)})" if unsupported else "")
            )
        session.volumes, session.unsupported = volumes, unsupported
        session.volumes_at = time.time()
        self.store.save(session)

    def current(self, sid: str | None) -> VikingSession | None:
        """The live session behind a cookie, tokens and permissions current.

        ``None`` whenever the browser must sign in again -- an unknown cookie,
        an expired session, a refresh the identity server refused, or a
        permissions answer too old to keep trusting.

        Blocking: renewing talks to the identity server.  The web app calls it
        from a worker thread, never from the event loop (a slow or absent
        identity server stalled every request, health checks included).  One
        request renews a session; the others, arriving meanwhile, use the
        answer they already hold -- whatever its age: only the request that
        holds the renewal enforces ``PERMISSION_MAX_STALE`` and forgets a
        session, because it alone knows whether the renewal succeeds.
        """
        session = self.store.load(sid)
        if session is None:
            return None
        if not session.token_stale() and not session.permissions_stale():
            return session
        lock = self._renewal_lock(session.sid)
        if not lock.acquire(blocking=False):
            # Being renewed by another request.  Applying the staleness limit
            # here signed out every request that arrived during a successful
            # renewal of a session over a day old, and could delete the file
            # between the renewer's lock and its re-read, losing the session.
            return session
        try:
            # Another request may have renewed it between the load and the lock.
            session = self.store.load(sid)
            if session is None:
                return None
            return self._renew(session)
        finally:
            lock.release()

    def _renewal_lock(self, sid: str) -> threading.Lock:
        with self._guard:
            return self._renewing.setdefault(sid, threading.Lock())

    def _renew(self, session: VikingSession) -> VikingSession | None:
        """Bring a session's token and permissions up to date, or end it.

        An identity server that cannot be asked ends nothing: the session goes
        on, on its last known permissions, until ``PERMISSION_MAX_STALE``
        (:meth:`_keep_stale`), whether it was the permissions or the access
        token (which falls due first, about an hour in) that could not be
        renewed.  Only a refusal -- the identity server answering that the
        grant is spent -- signs a person out sooner.
        """
        failed_at = self._refresh_failed.get(session.sid)
        if failed_at is not None and time.time() - failed_at < PERMISSION_RETRY:
            return self._within_stale_limit(session)
        if session.token_stale():
            try:
                renewed = self.refresh_token(session)
            except VikingUnreachable as exc:
                return self._keep_stale(session, exc)
            if not renewed:
                log.info("viking session for %r could not be renewed", session.user)
                self._forget(session.sid)
                return None
        if not session.permissions_stale():
            self._refresh_failed.pop(session.sid, None)
            return session
        try:
            self.refresh_permissions(session)
        except VikingAuthExpired:
            # The token was rejected even though it looked current; one
            # refresh, then give up.
            try:
                renewed = self.refresh_token(session)
            except VikingUnreachable as exc:
                return self._keep_stale(session, exc)
            if not renewed:
                self._forget(session.sid)
                return None
            try:
                self.refresh_permissions(session)
            except VikingError as exc:
                return self._keep_stale(session, exc)
        except VikingError as exc:
            return self._keep_stale(session, exc)
        self._refresh_failed.pop(session.sid, None)
        return session

    def _forget(self, sid: str) -> None:
        """Delete a session and what is remembered about it."""
        self.store.delete(sid)
        with self._guard:
            self._renewing.pop(sid, None)
            self._refresh_failed.pop(sid, None)

    def _within_stale_limit(self, session: VikingSession) -> VikingSession | None:
        """The session on its last permissions, unless they are too old to trust."""
        if time.time() - session.volumes_at > PERMISSION_MAX_STALE:
            self._forget(session.sid)
            return None
        return session

    def _keep_stale(self, session: VikingSession, exc: Exception) -> VikingSession | None:
        """Ride out an unreachable identity server on the last known answer.

        The alternative -- refusing every request the moment the identity
        server hiccups -- would make this site's availability the product of
        two servers' rather than one's.  It cannot go on forever, though: a
        revoked grant has to take effect eventually, so past
        ``PERMISSION_MAX_STALE`` the session is dropped and the user signs in
        again (which will fail loudly if the server really is down).  Until
        then the failure is remembered for ``PERMISSION_RETRY`` seconds, so
        the next request does not pay for the same failed call.  The same
        for the permissions that could not be fetched and for the access
        token that could not be renewed.
        """
        age = time.time() - session.volumes_at
        if age > PERMISSION_MAX_STALE:
            log.warning(
                "viking permissions for %r are %.0f h stale and unrefreshable: signing out (%s)",
                session.user, age / 3600, exc,
            )
            self._forget(session.sid)
            return None
        with self._guard:
            self._refresh_failed[session.sid] = time.time()
        log.warning("using %.0f min old viking permissions for %r: %s", age / 60, session.user, exc)
        return session

    def sign_out(self, sid: str | None) -> None:
        self.store.delete(sid)
        if sid:
            with self._guard:
                self._renewing.pop(sid, None)
                self._refresh_failed.pop(sid, None)

    def end_session_url(self) -> str | None:
        """Where to send the browser so the identity server signs them out too.

        Best effort: without it the user stays signed in *there*, and clicking
        sign-in again would silently sign them straight back in here.
        """
        try:
            endpoint = self.endpoint("end_session_endpoint")
        except VikingError as exc:
            log.info("no end_session_endpoint: %s", exc)
            return None
        from urllib.parse import urlencode

        return f"{endpoint}?{urlencode({'post_logout_redirect_uri': self.config.post_logout_uri})}"


def _expiry(token: dict[str, Any]) -> float:
    """When an access token stops working, from whichever field the server sent."""
    if "expires_at" in token:
        try:
            return float(token["expires_at"])
        except (TypeError, ValueError):
            pass
    try:
        return time.time() + float(token.get("expires_in", 3600))
    except (TypeError, ValueError):
        return time.time() + 3600
