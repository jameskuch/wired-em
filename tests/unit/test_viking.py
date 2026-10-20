"""Sign-in and volume permissions through the Viking Identity Server.

Nothing here talks to identity.codepharm.net.  The shapes asserted against are
the ones the live server actually returns -- an ``AccessibleVolumes`` object
keyed by volume id, ``CurrentUser`` as a bare JSON string, a 401 for a spent
token -- captured while reading its source and probing it directly.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from sbfsem.core.volumes import Volumes, from_viking_name, viking_names
from sbfsem_web.app import create_app
from sbfsem_web.viking import (
    PERMISSION_MAX_STALE,
    PERMISSION_RETRY,
    PERMISSION_TTL,
    SESSION_TTL,
    VIKING_COOKIE,
    PermissionsClient,
    VikingAuth,
    VikingAuthExpired,
    VikingConfig,
    VikingError,
    VikingSession,
    VikingSessionStore,
    _volume_url_name,
    sort_volumes,
)

# --- configuration ------------------------------------------------------------


def test_off_unless_asked_for(monkeypatch):
    """The default has to be "behaves exactly as before"."""
    for name in ("SBFSEM_VIKING_AUTH", "SBFSEM_VIKING_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert VikingConfig.from_env().enabled is False


def test_asking_for_it_without_a_secret_stays_off(monkeypatch, caplog):
    """A confidential client with no secret cannot finish the exchange.

    Falling back to "run without one" would put a button on the login page
    that leads to a dead end, so the misconfiguration is logged and ignored.
    """
    monkeypatch.setenv("SBFSEM_VIKING_AUTH", "1")
    monkeypatch.delenv("SBFSEM_VIKING_CLIENT_SECRET", raising=False)
    with caplog.at_level("ERROR"):
        assert VikingConfig.from_env().enabled is False
    assert "CLIENT_SECRET" in caplog.text


def test_enabled_with_a_secret(monkeypatch):
    monkeypatch.setenv("SBFSEM_VIKING_AUTH", "yes")
    monkeypatch.setenv("SBFSEM_VIKING_CLIENT_SECRET", "shh")
    config = VikingConfig.from_env()
    assert config.enabled is True
    assert config.client_id == "sbfsem-tools"


def test_authority_always_ends_in_a_slash(monkeypatch):
    """The discovery URL is built by concatenation, so this matters."""
    monkeypatch.setenv("SBFSEM_VIKING_AUTHORITY", "https://example.org:5001")
    config = VikingConfig.from_env()
    assert config.authority == "https://example.org:5001/"
    assert config.metadata_url == "https://example.org:5001/.well-known/openid-configuration"


# --- the volume name mapping --------------------------------------------------


def test_viking_names_round_trip():
    for canonical, viking in viking_names().items():
        assert from_viking_name(viking) == canonical


def test_viking_names_are_case_and_space_insensitive():
    """Viking spells one of them ``cped``; a name may arrive however it likes."""
    assert from_viking_name("CPED") == "NeitzCped"
    assert from_viking_name("  inferiormonkey ") == "NeitzInferiorMonkey"


def test_volumes_the_identity_server_does_not_know():
    """Two dozen volumes there have no renderer here; that is not an error."""
    assert from_viking_name("Zia") is None
    assert from_viking_name("gRPC-RC1-Test") is None
    assert Volumes.DemoVolume.viking_name is None


def test_every_volume_declares_its_viking_name():
    """A volume added later must say either its Viking name or ``None``."""
    for volume in Volumes:
        assert volume.viking_name is None or isinstance(volume.viking_name, str)


def test_volume_names_with_spaces_are_hyphenated_in_urls():
    assert _volume_url_name("gRPC RC1 Test") == "gRPC-RC1-Test"
    assert _volume_url_name("RC1") == "RC1"


# --- reading an AccessibleVolumes answer --------------------------------------

#: The response shape, keyed by the identity server's own volume ids.
ACCESSIBLE = {
    "42": {
        "id": 42,
        "name": "RC1",
        "description": "Rabbit retinal connectome 1",
        "endpoint": "https://websvc.codepharm.net/RC1",
        "permissions": ["Read", "Annotate"],
    },
    "7": {"id": 7, "name": "InferiorMonkey", "permissions": ["Read"]},
    "9": {"id": 9, "name": "Zia", "permissions": ["Read", "Annotate"]},
    "3": {"id": 3, "name": "RC2", "permissions": []},
}


def test_sort_volumes_splits_what_we_can_open_from_what_we_cannot():
    mine, unsupported = sort_volumes(list(ACCESSIBLE.values()))
    assert mine == {"RC1": ["Read", "Annotate"], "NeitzInferiorMonkey": ["Read"]}
    assert unsupported == ["Zia"]


def test_a_volume_with_no_permissions_is_no_access():
    """Listed but empty means listed, not granted."""
    mine, _ = sort_volumes(list(ACCESSIBLE.values()))
    assert "RC2" not in mine


def test_sort_volumes_ignores_junk():
    mine, unsupported = sort_volumes([{"name": ""}, {"permissions": ["Read"]}, {}])
    assert mine == {} and unsupported == []


# --- the Permissions Web API --------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    """A PermissionsClient whose httpx.get is answered from a routing table."""
    routes: dict[str, httpx.Response] = {}

    def fake_get(url, headers=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer ")
        for path, response in routes.items():
            if url.endswith(path):
                return response
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr("sbfsem_web.viking.httpx.get", fake_get)
    client = PermissionsClient("https://identity.example:6001/")
    return client, routes


def test_current_user_unwraps_the_json_string(api):
    """These endpoints return a JSON string, so the quotes are not part of it."""
    client, routes = api
    routes["Permissions/CurrentUser"] = httpx.Response(200, json="jkuchen")
    assert client.current_user("tok") == "jkuchen"


def test_accessible_volumes_returns_the_records(api):
    client, routes = api
    routes["Permissions/AccessibleVolumes"] = httpx.Response(200, json=ACCESSIBLE)
    names = {r["name"] for r in client.accessible_volumes("tok")}
    assert names == {"RC1", "InferiorMonkey", "Zia", "RC2"}


#: ``UserAccessibleVolumeTree`` as Identity 1.1.0 answers it (2026-10-08).
TREE = [
    {
        "id": 1, "name": "Jones Lab", "parentId": None, "resourceType": "OrganizationalUnit",
        "volumes": [
            {"id": 2, "name": "RC1", "resourceType": "AnnotationContext",
             "permissions": ["Read"], "metadata": {"AnnotationServerName": None}},
            {"id": 30, "name": "RC2-Internal", "resourceType": "AnnotationContext",
             "permissions": ["Read", "Annotate"], "metadata": {"AnnotationServerName": "RC2"}},
        ],
        "children": [
            {"id": 5, "name": "Inner", "resourceType": "OrganizationalUnit",
             "volumes": [{"id": 9, "name": "Zia", "permissions": ["Read"]}]},
        ],
    },
]


def test_identity_1_1_without_accessible_volumes_falls_back_to_the_tree(api):
    """Identity 1.1.0 answers AccessibleVolumes with 404; every sign-in failed."""
    client, routes = api
    routes["Permissions/UserAccessibleVolumeTree"] = httpx.Response(200, json=TREE)
    names = {r["name"] for r in client.accessible_volumes("tok")}
    assert names == {"RC1", "RC2-Internal", "Zia"}


def test_a_volume_is_known_by_its_annotation_server_name():
    """RC2-Internal is an image set of the RC2 annotation server."""
    records = [v for unit in TREE for v in unit["volumes"]]
    mine, unsupported = sort_volumes(records)
    assert mine == {"RC1": ["Read"], "RC2": ["Read", "Annotate"]}
    assert unsupported == []


def test_two_volumes_on_one_server_merge_their_permissions():
    mine, _ = sort_volumes([
        {"name": "RC1", "permissions": ["Read"]},
        {"name": "RC1-Internal", "permissions": ["Read", "Annotate"],
         "metadata": {"AnnotationServerName": "RC1"}},
    ])
    assert mine == {"RC1": ["Read", "Annotate"]}


def test_a_401_is_an_expired_token_not_a_failure(api):
    """The caller's response to this one is to refresh and retry."""
    client, routes = api
    routes["Permissions/AccessibleVolumes"] = httpx.Response(401)
    with pytest.raises(VikingAuthExpired):
        client.accessible_volumes("stale")


def test_an_unreachable_server_is_an_error_not_an_empty_list(api):
    """Reading "no answer" as "no volumes" would silently lock everyone out."""
    client, _ = api

    def boom(url, headers=None, timeout=None):
        raise httpx.ConnectError("no route to host")

    import sbfsem_web.viking as viking_module

    original = viking_module.httpx.get
    viking_module.httpx.get = boom
    try:
        with pytest.raises(VikingError):
            client.accessible_volumes("tok")
    finally:
        viking_module.httpx.get = original


def test_unknown_volume_is_reported(api):
    client, routes = api
    routes["Permissions/resource/Nope"] = httpx.Response(404)
    with pytest.raises(VikingError, match="no such volume"):
        client.volume_permissions("tok", "Nope")


# --- server-side sessions -----------------------------------------------------


def _session(store: VikingSessionStore, **over) -> VikingSession:
    fields = {
        "user": "jkuchen",
        "user_id": "abc-123",
        "access_token": "at",
        "refresh_token": "rt",
        "expires_at": time.time() + 3600,
        "volumes": {"RC1": ["Read"]},
        "volumes_at": time.time(),
    }
    fields.update(over)
    return store.create(**fields)


def test_session_round_trip(tmp_path):
    store = VikingSessionStore(tmp_path / "sessions")
    made = _session(store)
    read = store.load(made.sid)
    assert read is not None
    assert (read.user, read.user_id, read.access_token) == ("jkuchen", "abc-123", "at")
    assert read.volumes == {"RC1": ["Read"]}


def test_tokens_are_not_world_readable(tmp_path):
    store = VikingSessionStore(tmp_path / "sessions")
    made = _session(store)
    assert (store.base / f"{made.sid}.json").stat().st_mode & 0o077 == 0


def test_a_cookie_cannot_name_a_file_outside_the_store(tmp_path):
    """The session id comes from the browser, so it is attacker-controlled."""
    store = VikingSessionStore(tmp_path / "sessions")
    for hostile in ("../../etc/passwd", "a/b", "..", "NOTHEX", "x" * 200):
        assert store.load(hostile) is None


def test_an_expired_session_is_dropped(tmp_path):
    store = VikingSessionStore(tmp_path / "sessions")
    made = _session(store)
    made.created_at = time.time() - SESSION_TTL - 1
    store.save(made)
    assert store.load(made.sid) is None
    assert not (store.base / f"{made.sid}.json").exists()


def test_an_unreadable_session_is_discarded(tmp_path):
    store = VikingSessionStore(tmp_path / "sessions")
    made = _session(store)
    (store.base / f"{made.sid}.json").write_text("{not json", encoding="utf-8")
    assert store.load(made.sid) is None


def test_purge_removes_only_the_old(tmp_path):
    store = VikingSessionStore(tmp_path / "sessions")
    fresh = _session(store)
    old = _session(store)
    import os

    stale = time.time() - SESSION_TTL - 10
    os.utime(store.base / f"{old.sid}.json", (stale, stale))
    assert store.purge() == 1
    assert store.load(fresh.sid) is not None


def test_a_new_sign_in_sweeps_out_expired_sessions(tmp_path):
    """purge() existed from the start but nothing in the app called it."""
    import os

    store = VikingSessionStore(tmp_path / "sessions")
    old = _session(store)
    stale = time.time() - SESSION_TTL - 10
    os.utime(store.base / f"{old.sid}.json", (stale, stale))
    _session(store)  # the next person signing in
    assert not (store.base / f"{old.sid}.json").exists()


# --- what a session may read --------------------------------------------------


def test_may_read_follows_the_grant(tmp_path):
    store = VikingSessionStore(tmp_path / "s")
    session = _session(store, volumes={"RC1": ["Read"]})
    assert session.may_read("RC1") is True
    assert session.may_read("RC2") is False


def test_may_read_accepts_an_alias(tmp_path):
    """The API takes ``i`` and ``Inferior Monkey`` as well as the full name."""
    store = VikingSessionStore(tmp_path / "s")
    session = _session(store, volumes={"NeitzInferiorMonkey": ["Read"]})
    assert session.may_read("i") is True


def test_the_demo_volume_is_never_gated(tmp_path):
    """Viking has no such volume, so it has no opinion about it."""
    store = VikingSessionStore(tmp_path / "s")
    session = _session(store, volumes={})
    assert session.may_read("DemoVolume") is True


def test_an_unknown_source_is_not_readable(tmp_path):
    store = VikingSessionStore(tmp_path / "s")
    session = _session(store, volumes={"RC1": ["Read"]})
    assert session.may_read("NoSuchVolume") is False


# --- keeping a session alive --------------------------------------------------


class _StubPermissions:
    """Stands in for the Permissions Web API."""

    def __init__(self, volumes=None):
        self.volumes = volumes if volumes is not None else list(ACCESSIBLE.values())
        self.calls = 0
        self.raises: Exception | None = None

    def current_user(self, token):
        return "jkuchen"

    def current_user_id(self, token):
        return "abc-123"

    def accessible_volumes(self, token):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.volumes


@pytest.fixture
def auth(tmp_path):
    manager = VikingAuth(
        tmp_path / "auth",
        VikingConfig(requested=True, client_secret="shh"),
    )
    manager.permissions = _StubPermissions()  # type: ignore[assignment]
    manager._metadata = {"token_endpoint": "https://identity.example/connect/token"}
    return manager


def test_a_fresh_session_is_returned_untouched(auth):
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]}, volumes_at=time.time(),
    )
    assert auth.permissions.calls == 0
    assert auth.current(session.sid) is not None
    assert auth.permissions.calls == 0  # nothing was stale, so nothing was asked


def test_a_stale_access_token_is_refreshed(auth, monkeypatch):
    """Short-lived tokens are the point of holding a refresh token."""
    posted = {}

    def fake_post(url, data=None, timeout=None):
        posted.update(data)
        return httpx.Response(
            200,
            json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("sbfsem_web.viking.httpx.post", fake_post)
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="old", refresh_token="old-rt",
        expires_at=time.time() - 1, volumes={"RC1": ["Read"]}, volumes_at=time.time(),
    )
    live = auth.current(session.sid)
    assert live is not None
    assert live.access_token == "new-at"
    # A refresh returns a new refresh token; the old one must be discarded.
    assert live.refresh_token == "new-rt"
    assert posted["grant_type"] == "refresh_token"
    assert auth.store.load(session.sid).access_token == "new-at"


def test_a_refused_refresh_signs_the_browser_out(auth, monkeypatch):
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: httpx.Response(
            400, json={"error": "invalid_grant"}, request=httpx.Request("POST", url)
        ),
    )
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="old", refresh_token="spent",
        expires_at=time.time() - 1, volumes={"RC1": ["Read"]}, volumes_at=time.time(),
    )
    assert auth.current(session.sid) is None
    assert auth.store.load(session.sid) is None


def test_permissions_are_refetched_when_stale(auth):
    """A grant made in the management site should appear without signing out."""
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={}, volumes_at=time.time() - PERMISSION_TTL - 1,
    )
    live = auth.current(session.sid)
    assert live is not None
    assert auth.permissions.calls == 1
    assert set(live.volumes) == {"RC1", "NeitzInferiorMonkey"}
    assert live.unsupported == ["Zia"]


def test_an_unreachable_identity_server_does_not_sign_everyone_out(auth, caplog):
    """This site's availability should not be the product of two servers'."""
    auth.permissions.raises = VikingError("connection refused")
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_TTL - 1,
    )
    with caplog.at_level("WARNING"):
        live = auth.current(session.sid)
    assert live is not None
    assert live.volumes == {"RC1": ["Read"]}  # the last known answer, still used
    assert "stale" not in caplog.text.lower() or "old viking permissions" in caplog.text


def test_an_empty_recheck_keeps_the_last_good_list(auth):
    """Mid-session, "no volumes" is likelier a changed answer than revoked rights."""
    auth.permissions.volumes = [{"name": "Zia", "permissions": ["Read"]}]
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_TTL - 1,
    )
    live = auth.current(session.sid)
    assert live is not None
    assert live.volumes == {"RC1": ["Read"]}


def test_but_it_cannot_go_on_forever(auth, caplog):
    """A revoked grant has to take effect even if the server stays down."""
    auth.permissions.raises = VikingError("connection refused")
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_MAX_STALE - 1,
    )
    with caplog.at_level("WARNING"):
        assert auth.current(session.sid) is None
    assert auth.store.load(session.sid) is None


def test_a_rejected_token_is_refreshed_once_then_retried(auth, monkeypatch):
    """A 401 from the permissions API means refresh and try again, not give up."""
    calls = {"n": 0}

    def flaky(token):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VikingAuthExpired("stale")
        return list(ACCESSIBLE.values())

    auth.permissions.accessible_volumes = flaky  # type: ignore[assignment]
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: httpx.Response(
            200, json={"access_token": "fresh", "expires_in": 3600},
            request=httpx.Request("POST", url),
        ),
    )
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={}, volumes_at=0,
    )
    live = auth.current(session.sid)
    assert live is not None and live.access_token == "fresh"
    assert calls["n"] == 2


def test_a_failed_permissions_refresh_is_not_retried_by_the_next_request(auth, caplog):
    """Every request used to pay the failed call again, each up to its timeout."""
    auth.permissions.raises = VikingError("connection refused")
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_TTL - 1,
    )
    with caplog.at_level("WARNING"):
        assert auth.current(session.sid).volumes == {"RC1": ["Read"]}  # type: ignore[union-attr]
        assert auth.permissions.calls == 1
        for _ in range(5):
            assert auth.current(session.sid) is not None
    assert auth.permissions.calls == 1, "the failure is remembered for a while"
    assert caplog.text.count("old viking permissions") == 1
    # Once the wait is over the identity server is asked again, and an answer ends it.
    auth._refresh_failed[session.sid] -= PERMISSION_RETRY + 1
    auth.permissions.raises = None
    live = auth.current(session.sid)
    assert auth.permissions.calls == 2
    assert live is not None and set(live.volumes) == {"RC1", "NeitzInferiorMonkey"}
    assert session.sid not in auth._refresh_failed
    auth.current(session.sid)
    assert auth.permissions.calls == 2, "a fresh answer is used as it always was"


def test_the_backoff_does_not_outlive_the_staleness_limit(auth):
    auth.permissions.raises = VikingError("connection refused")
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_TTL - 1,
    )
    assert auth.current(session.sid) is not None
    stored = auth.store.load(session.sid)
    stored.volumes_at = time.time() - PERMISSION_MAX_STALE - 1  # type: ignore[union-attr]
    auth.store.save(stored)  # type: ignore[arg-type]
    assert auth.current(session.sid) is None, "in the backoff window, too"
    assert auth.store.load(session.sid) is None


def test_a_request_that_finds_the_session_being_renewed_uses_what_it_has(auth):
    """One renewal at a time; the others do not queue behind it."""
    import threading

    started, release = threading.Event(), threading.Event()
    seen = []

    def slow(token):
        seen.append(token)
        started.set()
        release.wait(5)
        return list(ACCESSIBLE.values())

    auth.permissions.accessible_volumes = slow  # type: ignore[assignment]
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_TTL - 1,
    )
    first = []
    worker = threading.Thread(target=lambda: first.append(auth.current(session.sid)))
    worker.start()
    assert started.wait(5)
    t0 = time.perf_counter()
    other = auth.current(session.sid)
    assert time.perf_counter() - t0 < 1.0
    assert other is not None and other.volumes == {"RC1": ["Read"]}  # the old answer
    release.set()
    worker.join(5)
    assert len(seen) == 1 and first[0] is not None
    assert set(first[0].volumes) == {"RC1", "NeitzInferiorMonkey"}


def _due_session(auth, **kwargs):
    """A session whose access token has fallen due, on permissions fetched a minute ago."""
    fields = {
        "user": "jkuchen", "user_id": "abc-123", "access_token": "old", "refresh_token": "rt",
        "expires_at": time.time() - 1, "volumes": {"RC1": ["Read"]}, "volumes_at": time.time() - 60,
    }
    return auth.store.create(**{**fields, **kwargs})


def test_an_unreachable_token_endpoint_does_not_sign_a_session_out(auth, monkeypatch, caplog):
    """The guide says a session keeps working on its last known permissions, for up to
    a day, when the identity server cannot be reached.  The access token falls due
    first (about an hour), and a refresh that could not even be asked deleted the
    session at once -- and the person could not sign back in, since signing in needs
    the same server."""
    asked = []

    def down(url, data=None, timeout=None):
        asked.append(url)
        raise httpx.ConnectTimeout("simulated outage")

    monkeypatch.setattr("sbfsem_web.viking.httpx.post", down)
    session = _due_session(auth)
    with caplog.at_level("WARNING"):
        live = auth.current(session.sid)
    assert live is not None and live.volumes == {"RC1": ["Read"]}
    assert auth.store.load(session.sid) is not None
    assert "simulated outage" in caplog.text
    # The failed call is not paid for again by every request, as for the permissions.
    for _ in range(5):
        assert auth.current(session.sid) is not None
    assert len(asked) == 1
    # When the wait is over and the server answers, the token is renewed and the failure forgotten.
    auth._refresh_failed[session.sid] -= PERMISSION_RETRY + 1
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: httpx.Response(
            200, json={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
            request=httpx.Request("POST", url)),
    )
    renewed = auth.current(session.sid)
    assert renewed is not None and renewed.access_token == "new-at"
    assert session.sid not in auth._refresh_failed
    assert auth.store.load(session.sid).access_token == "new-at"          # type: ignore[union-attr]


@pytest.mark.parametrize("answer", [
    httpx.Response(500, text="boom"),
    httpx.Response(502, text="bad gateway"),
    httpx.Response(503, text="try later"),
    httpx.Response(200, text="<html>a proxy's page</html>"),
    httpx.Response(200, json={"error": "no token for you"}),
    httpx.Response(200, json=["not", "an", "object"]),
])
def test_a_token_endpoint_that_does_not_answer_is_an_outage_not_a_refusal(auth, monkeypatch, answer):
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: httpx.Response(
            answer.status_code, content=answer.content, request=httpx.Request("POST", url)),
    )
    session = _due_session(auth)
    assert auth.current(session.sid) is not None
    assert auth.store.load(session.sid) is not None


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_token_endpoint_that_refuses_signs_the_session_out_at_once(auth, monkeypatch, status):
    """A refusal is the identity server's answer, not an outage: a revoked grant takes effect."""
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: httpx.Response(
            status, json={"error": "invalid_grant"}, request=httpx.Request("POST", url)),
    )
    session = _due_session(auth)
    assert auth.current(session.sid) is None
    assert auth.store.load(session.sid) is None


def test_an_unreachable_token_endpoint_cannot_keep_a_session_past_the_limit(auth, monkeypatch):
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: (_ for _ in ()).throw(httpx.ConnectError("down")))
    session = _due_session(auth, volumes_at=time.time() - PERMISSION_MAX_STALE - 1)
    assert auth.current(session.sid) is None
    assert auth.store.load(session.sid) is None


def test_the_usual_outage_stales_the_token_and_the_permissions_together(auth, monkeypatch):
    """After an hour of outage both have fallen due: neither the token endpoint nor the
    permissions API answers, and the session goes on until the limit."""
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))
    auth.permissions.raises = VikingError("connection refused")
    session = _due_session(auth, volumes_at=time.time() - 3 * 3600)
    live = auth.current(session.sid)
    assert live is not None and live.volumes == {"RC1": ["Read"]}
    assert auth.permissions.calls == 0, "the permissions are not asked for with a token that could not be renewed"


def test_a_permissions_api_that_rejects_the_token_then_a_token_endpoint_that_is_down(auth, monkeypatch):
    """The token looked current and the permissions API refused it; the one refresh
    allowed could not be asked either."""
    auth.permissions.raises = VikingAuthExpired("stale")
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.post",
        lambda url, data=None, timeout=None: (_ for _ in ()).throw(httpx.ConnectTimeout("down")))
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]}, volumes_at=time.time() - PERMISSION_TTL - 1)
    assert auth.current(session.sid) is not None
    assert auth.store.load(session.sid) is not None


def test_an_unreadable_discovery_document_is_an_outage_too(auth, monkeypatch):
    """The token endpoint's address comes from the discovery document, fetched once
    per process: a restart during an outage cannot read it, and used to sign everyone out."""
    auth._metadata = None
    monkeypatch.setattr(
        "sbfsem_web.viking.httpx.get",
        lambda url, timeout=None: (_ for _ in ()).throw(httpx.ConnectError("down")))
    session = _due_session(auth)
    assert auth.current(session.sid) is not None
    assert auth.store.load(session.sid) is not None


def test_a_request_during_the_renewal_of_a_session_over_the_limit_does_not_sign_it_out(auth):
    """A session whose permissions are more than a day old, renewed successfully: the
    requests that arrive while the identity server is answering are the renewer's
    company, not its judges.  They used to apply the limit to the answer they held,
    forget the session (file and lock) and be signed out, with the renewal about to
    succeed -- a browser restored with several tabs open in the morning."""
    import threading

    started, release = threading.Event(), threading.Event()
    seen = []

    def slow(token):
        seen.append(token)
        started.set()
        release.wait(5)
        return list(ACCESSIBLE.values())

    auth.permissions.accessible_volumes = slow  # type: ignore[assignment]
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_MAX_STALE - 60,
    )
    first = []
    worker = threading.Thread(target=lambda: first.append(auth.current(session.sid)))
    worker.start()
    assert started.wait(5)
    others = [auth.current(session.sid) for _ in range(3)]
    assert all(other is not None for other in others), "the requests that arrive meanwhile are signed out"
    assert all(other.volumes == {"RC1": ["Read"]} for other in others)    # type: ignore[union-attr]
    assert auth.store.load(session.sid) is not None, "and the session is still there to be renewed"
    release.set()
    worker.join(5)
    assert len(seen) == 1, "one identity-server call for four requests"
    assert first[0] is not None and set(first[0].volumes) == {"RC1", "NeitzInferiorMonkey"}
    assert auth.store.load(session.sid) is not None
    assert auth.current(session.sid) is not None, "and the session goes on"


def test_the_renewal_survives_a_request_that_comes_between_its_lock_and_its_reload(auth, monkeypatch):
    """The interleaving that lost a session for good: another request, over the
    limit, ran between the renewer taking the lock and reading the session again.
    It forgot the file, so the renewer read nothing and signed the person out
    although the identity server was answering."""
    import threading

    in_reload, go = threading.Event(), threading.Event()
    session = auth.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_MAX_STALE - 60,
    )
    real_load = auth.store.load
    loads = {"renewer": 0}

    def load(sid):
        if threading.current_thread().name == "renewer":
            loads["renewer"] += 1
            if loads["renewer"] == 2:                  # the re-read after the lock is taken
                in_reload.set()
                assert go.wait(5)
        return real_load(sid)

    monkeypatch.setattr(auth.store, "load", load)
    first = []
    worker = threading.Thread(name="renewer", target=lambda: first.append(auth.current(session.sid)))
    worker.start()
    assert in_reload.wait(5)
    between = auth.current(session.sid)                # the other request, mid-renewal
    go.set()
    worker.join(5)
    assert first[0] is not None, "the renewal came out empty-handed"
    assert real_load(session.sid) is not None, "and the session file is gone for good"
    assert set(first[0].volumes) == {"RC1", "NeitzInferiorMonkey"}
    assert between is not None, "the request that came between was signed out"


def test_two_saves_of_one_session_do_not_share_a_temporary_file(tmp_path):
    import threading

    store = VikingSessionStore(tmp_path / "s")
    session = store.create(user="u", user_id="1", access_token="at", refresh_token=None,
                           expires_at=time.time() + 3600)
    errors = []

    def save_many():
        try:
            for _ in range(40):
                store.save(session)
        except Exception as exc:  # noqa: BLE001 - the point is that none escapes
            errors.append(exc)

    threads = [threading.Thread(target=save_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert [p.name for p in (tmp_path / "s").iterdir()] == [f"{session.sid}.json"]


def test_begin_session_records_who_and_what(auth):
    session = auth.begin_session(
        {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    )
    assert session.user == "jkuchen"
    assert session.user_id == "abc-123"
    assert set(session.volumes) == {"RC1", "NeitzInferiorMonkey"}
    assert auth.store.load(session.sid) is not None


def test_a_token_response_with_no_access_token_is_refused(auth):
    with pytest.raises(VikingError):
        auth.begin_session({"refresh_token": "rt"})


def test_a_slow_identity_server_does_not_stall_the_event_loop(tmp_path, monkeypatch):
    """The middleware renewed sessions on the event loop: a slow identity server
    held every request, health checks included, and the next request paid again."""
    import asyncio
    import types

    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("SBFSEM_VIKING_AUTH", "1")
    monkeypatch.setenv("SBFSEM_VIKING_CLIENT_SECRET", "shh")
    app = create_app()
    app.state.service = types.SimpleNamespace(list_volumes=lambda: [])
    session = app.state.viking.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600, volumes={"RC1": ["Read"]},
        volumes_at=time.time() - PERMISSION_TTL - 1,
    )
    asked = []
    outage_s = 3.0  # long against a busy machine's hiccups, short against a test's patience

    def outage(url, **kwargs):
        asked.append(url)
        time.sleep(outage_s)
        raise httpx.ConnectTimeout("simulated outage")

    monkeypatch.setattr("sbfsem_web.viking.httpx.get", outage)

    async def main():
        # A client that is not loopback, which the login skips.
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            t0 = time.perf_counter()

            async def timed(path, cookies=None):
                response = await client.get(path, cookies=cookies)
                return path, response.status_code, time.perf_counter() - t0

            both = await asyncio.gather(
                timed("/api/volumes", {VIKING_COOKIE: session.sid}), timed("/api/health"))
            t1 = time.perf_counter()
            again = await client.get("/api/volumes", cookies={VIKING_COOKIE: session.sid})
            return both, again.status_code, time.perf_counter() - t1

    (volumes, health), again_status, again_seconds = asyncio.run(main())
    assert volumes[1] == 200 and health[1] == 200
    assert volumes[2] >= outage_s - 0.1, "the request that renews the session waits for the identity server"
    assert health[2] < outage_s / 2, "but the others do not wait behind it"
    assert again_status == 200 and again_seconds < outage_s / 2, "and a failure is not paid for twice"
    assert len(asked) == 1


def test_the_sign_in_callback_does_not_hold_the_event_loop_while_the_identity_server_answers(
        tmp_path, monkeypatch):
    """The callback's two identity-server calls (who the user is, and what they may open)
    ran on the event loop: a slow answer held every request, health checks included, for
    as long as it took.  Nothing in the suite noticed when ``begin_session`` went back on
    the loop, because no test had a callback with a slow identity server."""
    import asyncio
    import types

    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("SBFSEM_VIKING_AUTH", "1")
    monkeypatch.setenv("SBFSEM_VIKING_CLIENT_SECRET", "shh")
    app = create_app()
    viking = app.state.viking
    slow_s = 3.0    # long against a busy machine's hiccups, short against a test's patience

    async def authorize_access_token(request):
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    viking._oauth = types.SimpleNamespace(
        viking=types.SimpleNamespace(authorize_access_token=authorize_access_token))
    began = []

    def begin_session(token):
        began.append(token)
        time.sleep(slow_s)                     # the identity server, slow but answering
        return viking.store.create(
            user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
            expires_at=time.time() + 3600, volumes={"RC1": ["Read"]}, volumes_at=time.time())

    monkeypatch.setattr(viking, "begin_session", begin_session)

    async def main():
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            t0 = time.perf_counter()

            async def timed(path):
                response = await client.get(path, follow_redirects=False)
                return response, time.perf_counter() - t0

            return await asyncio.gather(timed("/auth/callback?code=abc&state=s"), timed("/api/health"))

    (callback, callback_s), (health, health_s) = asyncio.run(main())
    assert len(began) == 1
    assert callback.status_code == 303 and VIKING_COOKIE in callback.headers["set-cookie"]
    assert callback_s >= slow_s - 0.1, "the callback waits for the identity server"
    assert health.status_code == 200 and health_s < slow_s / 2, "but the others do not wait behind it"


# --- the app with Viking sign-in on -------------------------------------------


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    """The app with a browser signed in as a user who may open RC1 only.

    The identity server is never called: a canned session is handed to the
    middleware, which is the only thing downstream code sees anyway.
    """
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("SBFSEM_VIKING_AUTH", "1")
    monkeypatch.setenv("SBFSEM_VIKING_CLIENT_SECRET", "shh")
    app = create_app()
    assert app.state.viking.enabled

    session = app.state.viking.store.create(
        user="jkuchen", user_id="abc-123", access_token="at", refresh_token="rt",
        expires_at=time.time() + 3600,
        volumes={"RC1": ["Read", "Annotate"]},
        unsupported=["Zia"],
        volumes_at=time.time(),
    )
    # Skip the refresh machinery; it has its own tests above.
    app.state.viking.current = lambda sid: session if sid == session.sid else None
    with TestClient(app) as client:
        app.state.service.offline = True
        client.cookies.set(VIKING_COOKIE, session.sid)
        yield client, app, session


def test_signed_out_browsers_are_sent_to_the_login_page(signed_in):
    client, _, _ = signed_in
    client.cookies.clear()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login")


def test_the_login_page_offers_viking_first(signed_in):
    client, _, _ = signed_in
    client.cookies.clear()
    page = client.get("/login").text
    assert "Sign in with Viking" in page
    assert page.index("Sign in with Viking") < page.index('name="password"')


def test_the_api_says_401_rather_than_redirecting(signed_in):
    """A fetch() must get a status it can act on, not a login page."""
    client, _, _ = signed_in
    client.cookies.clear()
    response = client.get("/api/volumes")
    assert response.status_code == 401


def test_the_volume_list_is_what_viking_shared(signed_in):
    client, _, _ = signed_in
    names = {v["name"] for v in client.get("/api/volumes").json()}
    # RC1 was granted; the demo volume is not Viking's to withhold.
    assert names == {"RC1", "DemoVolume"}


def test_a_volume_that_was_not_shared_is_refused(signed_in):
    client, _, _ = signed_in
    response = client.get("/api/volumes/RC2")
    assert response.status_code == 403
    assert "no access to RC2" in response.json()["detail"]


def test_the_granted_volume_is_not_refused(signed_in):
    """The gate must let the right people through, not just stop the wrong ones."""
    client, _, _ = signed_in
    assert client.get("/api/volumes/RC1").status_code == 200


def test_an_alias_cannot_walk_around_the_gate(signed_in):
    """``i``, ``inferior`` and ``Inferior Monkey`` are all the same volume."""
    client, _, _ = signed_in
    for alias in ("i", "inferior", "Inferior%20Monkey", "NeitzInferiorMonkey"):
        assert client.get(f"/api/volumes/{alias}").status_code == 403, alias


def test_a_source_in_the_query_string_is_gated(signed_in):
    client, _, _ = signed_in
    assert client.get("/api/network?source=RC2&ids=1").status_code == 403


def test_the_render_estimate_is_gated_like_every_cell_route(signed_in):
    """The preflight estimate (2026-10-16) reads a cell's outlines: a volume the
    account was not granted is refused before the route runs, in the path and in
    the query string alike; a granted one reaches the route (not loaded: 404)."""
    client, _, _ = signed_in
    assert client.get("/api/neurons/RC2/1/render-estimate?method=closedcurve").status_code == 403
    assert client.get("/api/neurons/RC1/1/render-estimate?source=RC2").status_code == 403
    assert client.get("/api/neurons/RC1/1/render-estimate?method=closedcurve").status_code == 404


def test_a_source_in_the_body_is_gated(signed_in):
    client, _, _ = signed_in
    response = client.post("/api/neurons", json={"id": 1, "source": "RC2"})
    assert response.status_code == 403


def test_the_shared_list_of_loaded_cells_is_gated_too(signed_in, monkeypatch):
    """``GET /api/neurons`` names no volume, so the guard cannot see one: the
    route filters it, or a new tab lists other volumes' cells."""
    from sbfsem.service.dto import NeuronSummaryDTO

    client, app, _ = signed_in
    loaded = [NeuronSummaryDTO(id=476, source="RC1", n_nodes=1, n_edges=0),
              NeuronSummaryDTO(id=21768, source="NeitzInferiorMonkey", n_nodes=1, n_edges=0)]
    monkeypatch.setattr(app.state.service, "loaded_neurons", lambda: loaded)
    listed = client.get("/api/neurons").json()
    assert [(n["id"], n["source"]) for n in listed] == [(476, "RC1")]


def test_a_gated_body_request_can_still_be_read_by_the_route(signed_in):
    """Reading the body in the guard must not consume it for the endpoint."""
    client, _, _ = signed_in
    response = client.post("/api/neurons", json={"id": 999999, "source": "RC1"})
    # Allowed through the gate; it then fails on its own terms, not on a
    # missing body.
    assert response.status_code != 403
    assert response.status_code != 422


def test_a_load_that_names_no_source_is_gated_as_the_volume_it_defaults_to(signed_in, monkeypatch):
    """``POST /neurons`` without a source loads Inferior Monkey; the guard reads
    only what the body says, so the route has to check the default itself."""
    client, app, _ = signed_in
    loads = []
    monkeypatch.setattr(app.state.service, "load_neuron",
                        lambda *args, **kw: loads.append(args) or (_ for _ in ()).throw(ValueError("stop")))
    for body in ({"id": 1}, {"id": 1, "source": "i"}):
        response = client.post("/api/neurons", json=body)
        assert response.status_code == 403, body
        assert "NeitzInferiorMonkey" in response.json()["detail"]
    assert loads == []
    # The volume this account was granted still loads, with or without naming it.
    response = client.post("/api/neurons", json={"id": 1, "source": "RC1"})
    assert response.status_code == 400 and len(loads) == 1


def test_the_freshness_batch_asks_only_for_the_volumes_the_account_may_open(signed_in, monkeypatch):
    client, app, _ = signed_in
    asked = []

    def freshness_many(wanted):
        asked.append(list(wanted))
        return [RuntimeError("stub") for _ in wanted]

    monkeypatch.setattr(app.state.service, "freshness_many", freshness_many)
    response = client.post("/api/neurons/freshness", json={"cells": [
        {"source": "i", "id": 1178},        # not shared: never asked
        {"id": 1179},                      # no source: Inferior Monkey too
        {"source": "RC1", "id": 476, "copy": "curvefit.1"},
        {"source": "RC2", "id": 74420},
    ]})
    assert response.status_code == 200
    assert asked == [[(476, "RC1", "curvefit.1")]]
    entries = {(c["source"], c["id"]): c for c in response.json()["cells"]}
    assert set(entries) == {("RC1", 476), ("NeitzInferiorMonkey", 1178),
                            ("NeitzInferiorMonkey", 1179), ("RC2", 74420)}
    for key in (("NeitzInferiorMonkey", 1178), ("NeitzInferiorMonkey", 1179), ("RC2", 74420)):
        assert entries[key]["freshness"] is None
        assert "no access" in entries[key]["error"]
    assert entries[("RC1", 476)]["error"] == "stub"


#: The routes whose volume rides in a JSON body, each with the service method it
#: calls and a body that names ``source``.  Every one checks its volume itself,
#: once FastAPI has parsed the body, so a spelling of the request that the router's
#: guard misreads cannot get past it.
VOLUME_IN_BODY = {
    ("POST", "/api/connectivity/query"): (
        "connectivity_query", lambda src: {"source": src, "hops": 1, "start_ids": [1178]}),
    ("POST", "/api/neurons"): (
        "load_neuron", lambda src: {"id": 1178, "source": src}),
}

#: Every way a client can say "this body is JSON" that FastAPI believes: the
#: media type is case-insensitive, may have a space before it, and any ``+json``
#: subtype is JSON; an older FastAPI reads a body with no Content-Type at all as
#: JSON too.  The guard once read exactly ``application/json`` and nothing else.
JSON_SPELLINGS = (
    "application/json",
    "APPLICATION/JSON",
    " application/json",
    "application/vnd.api+json",
    "application/json; charset=utf-8",
    "Application/Json;charset=UTF-8",
    None,
)


def _post_body(client, url, body, content_type):
    headers = {} if content_type is None else {"content-type": content_type}
    return client.post(url, content=json.dumps(body).encode(), headers=headers)


def test_every_route_with_a_body_is_gated_by_the_guard_or_by_itself(signed_in):
    """The sweep above fills ``{source}`` in the path, so it cannot see a route
    whose volume rides in its body.  Each one is listed here with who checks it,
    and a new one fails this test until somebody decides."""
    _, app, _ = signed_in
    found = {}
    for route in _all_routes(app):
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or "{source}" in path:
            continue
        for method in sorted(getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}):
            found[(method, path)] = None
    assert found.keys() == {
        # The route checks, because its body names the volume (or leaves it out, or
        # carries several); the guard reads the body too, as a second line, but
        # only what a route decides for itself is the same on every spelling.
        *VOLUME_IN_BODY,
        ("POST", "/api/neurons/freshness"),
        # The route checks, once the uploaded file says which volume it holds.
        ("POST", "/api/neurons/import"),
        # No volume: shared documents, shared libraries, and a job already started.
        ("POST", "/api/saves"),
        ("DELETE", "/api/saves/{folder}"),
        ("PUT", "/api/saves/{folder}/{name}"),
        ("DELETE", "/api/saves/{folder}/{name}"),
        ("PUT", "/api/library/{kind}/{name}"),
        ("DELETE", "/api/library/{kind}/{name}"),
        ("DELETE", "/api/mesh-jobs/{job_id}"),
        # No volume: the Settings tab's server tools (an admin's: api/admin.py) and
        # the browser's own cached files.
        ("POST", "/api/admin/clear-renders"),
        ("POST", "/api/admin/recycle-workers"),
        ("POST", "/api/admin/restart"),
        ("POST", "/api/browser/clear-cache"),
    }, "a route with a body is new: does the guard see its volume?"


def test_a_body_that_names_a_volume_is_refused_however_it_is_spelled(signed_in, monkeypatch):
    """The plain case is not the test: the account may open RC1 only, and the
    request names Inferior Monkey (or RC2) in its body, in every spelling of JSON
    a client can send, with and without a granted volume in the query string.
    The route behind the guard must never run."""
    client, app, _ = signed_in
    reached = []
    for method_name, _body in VOLUME_IN_BODY.values():
        monkeypatch.setattr(
            app.state.service, method_name,
            lambda *args, _name=method_name, **kw: reached.append(_name) or (_ for _ in ()).throw(ValueError("stop")),
        )
    for (_method, path), (_name, body_for) in VOLUME_IN_BODY.items():
        for content_type in JSON_SPELLINGS:
            for query in ("", "?source=RC1", "?source=RC1&source=RC1"):
                for ungranted in ("i", "RC2", "Inferior Monkey"):
                    response = _post_body(client, path + query, body_for(ungranted), content_type)
                    where = f"POST {path}{query} as {content_type!r} naming {ungranted!r}"
                    assert response.status_code == 403, f"{where} -> {response.status_code}"
                    assert "no access to" in response.json()["detail"], where
    assert reached == [], "a route ran for a volume the account was not granted"


def test_a_body_that_names_a_granted_volume_still_gets_through(signed_in, monkeypatch):
    """The gate must stop the wrong ones without stopping the right ones, in any
    spelling of JSON that FastAPI itself reads."""
    client, app, _ = signed_in
    reached = []
    for method_name, _body in VOLUME_IN_BODY.values():
        monkeypatch.setattr(
            app.state.service, method_name,
            lambda *args, _name=method_name, **kw: reached.append(_name) or (_ for _ in ()).throw(ValueError("stop")),
        )
    for (_method, path), (name, body_for) in VOLUME_IN_BODY.items():
        for content_type in JSON_SPELLINGS[:-1]:    # no Content-Type: current FastAPI does not read a body
            reached.clear()
            response = _post_body(client, path + "?source=RC1", body_for("RC1"), content_type)
            assert response.status_code == 400, f"POST {path} as {content_type!r} -> {response.status_code}"
            assert reached == [name], f"POST {path} as {content_type!r} never reached its route"


def test_the_freshness_batch_is_not_misled_by_a_spelling_or_a_query_string(signed_in, monkeypatch):
    """A list of cells, some granted and some not, in every spelling and with a
    granted volume in the query string: Viking is asked about the granted ones
    only, and the rest come back with their reason."""
    client, app, _ = signed_in
    asked = []

    def freshness_many(wanted):
        asked.append(list(wanted))
        return [RuntimeError("stub") for _ in wanted]

    monkeypatch.setattr(app.state.service, "freshness_many", freshness_many)
    cells = [
        {"source": "RC1", "id": 476},
        {"source": "i", "id": 1178},
        {"id": 1179},                       # no source: Inferior Monkey
        {"source": "RC2", "id": 74420},
    ]
    for content_type in JSON_SPELLINGS[:-1]:
        for query in ("", "?source=RC1"):
            asked.clear()
            response = _post_body(client, "/api/neurons/freshness" + query, {"cells": cells}, content_type)
            where = f"{content_type!r} {query!r}"
            assert response.status_code == 200, where
            assert asked == [[(476, "RC1", None)]], where
            errors = {(c["source"], c["id"]): c.get("error") for c in response.json()["cells"]}
            assert errors[("RC1", 476)] == "stub", where
            for key in (("NeitzInferiorMonkey", 1178), ("NeitzInferiorMonkey", 1179), ("RC2", 74420)):
                assert "no access" in errors[key], f"{where} {key}"


def test_an_upload_is_refused_whatever_the_query_string_says(signed_in):
    """The volume is inside the file; a granted volume in the query string, or
    a second one, must not stand in for it."""
    client, app, _ = signed_in
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "i943.json"    # Inferior Monkey
    for query in ("", "?source=RC1", "?source=RC1&source=RC1"):
        with fixture.open("rb") as fh:
            response = client.post("/api/neurons/import" + query, files={"file": ("i943.json", fh)})
        assert response.status_code == 403, query
    assert not app.state.service.loaded_neurons()


def _guard_request(app, session, *, method="POST", path_params=None, query="", body=None,
                   content_type="application/json"):
    """A request as the router's guard sees it, with no route behind it."""
    from starlette.requests import Request

    payload = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    headers = [] if content_type is None else [(b"content-type", content_type.encode())]
    sent = {"read": 0}

    async def receive():
        sent["read"] += 1
        return {"type": "http.request", "body": payload, "more_body": False}

    scope = {
        "type": "http", "method": method, "path": "/api/anything", "raw_path": b"/api/anything",
        "headers": headers, "query_string": query.encode(), "path_params": path_params or {},
        "app": app, "state": {"viking_session": session},
    }
    return Request(scope, receive), sent


def test_the_guard_reads_every_place_a_volume_can_be_named(signed_in):
    """The guard on its own, as it must behave for a route added tomorrow that
    forgets to check: it refuses the request when any place names a volume the
    account was not granted, not only the first place it finds one."""
    import asyncio

    from fastapi import HTTPException

    from sbfsem_web.api import routes

    _, app, session = signed_in

    def guard(**kw):
        request, _sent = _guard_request(app, session, **kw)
        return asyncio.run(routes._volume_access_guard(request))

    refused = [
        {"path_params": {"source": "RC2"}},
        {"query": "source=RC2"},
        {"query": "source=RC1&source=RC2"},            # the last value, the first, and the middle
        {"query": "source=RC2&source=RC1"},
        {"query": "source=RC1&source=RC1&source=RC2"},
        {"path_params": {"source": "RC1"}, "query": "source=RC2"},
        {"path_params": {"source": "RC2"}, "query": "source=RC1"},
        {"query": "source=RC1", "body": {"source": "RC2"}},
        {"path_params": {"source": "RC1"}, "body": {"source": "i"}},
        {"body": {"source": "i"}},
    ]
    for kwargs in refused:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for content_type in JSON_SPELLINGS:
                case = {**kwargs, "method": method, "content_type": content_type}
                if "body" not in kwargs and content_type != "application/json":
                    continue         # the body is not part of this case: one spelling is enough
                with pytest.raises(HTTPException) as caught:
                    guard(**case)
                assert caught.value.status_code == 403, case
    # The right ones pass, wherever the granted volume is named.
    for kwargs in (
        {},
        {"path_params": {"source": "RC1"}},
        {"query": "source=RC1&source=RC1"},
        {"path_params": {"source": "RC1"}, "query": "source=RC1", "body": {"source": "RC1"}},
        {"body": {"id": 1}},                           # names no volume: the route's business
        {"body": {"source": None}},
        {"query": "source=nonesuch"},                  # not a volume at all: the route's own 404
    ):
        guard(**kwargs)


def test_the_guard_reads_a_body_exactly_when_fastapi_does(signed_in):
    """What FastAPI parses as JSON, the guard parses; what it does not, the guard
    leaves unread, so an upload is not held in memory twice and a body the route
    cannot see names nothing."""
    import asyncio

    from fastapi import HTTPException

    from sbfsem_web.api import routes

    _, app, session = signed_in

    def guard(**kw):
        request, sent = _guard_request(app, session, **kw)
        asyncio.run(routes._volume_access_guard(request))
        return sent["read"]

    body = {"source": "i"}
    # A multipart upload is parsed by the framework as a form, never as JSON: left alone.
    assert guard(body=json.dumps(body).encode(), content_type="multipart/form-data; boundary=x") == 0
    assert guard(body=json.dumps(body).encode(), content_type="text/plain") == 0
    assert guard(body=json.dumps(body).encode(), content_type="application/x-www-form-urlencoded") == 0
    # A GET has no body to read.
    assert guard(method="GET", body=body) == 0
    # JSON that is not a JSON object, or not JSON at all, names nothing.
    for text in (b"[1, 2]", b"\"i\"", b"7", b"not json", b"\xff\xfe", b""):
        assert guard(body=text) <= 1
    # And the readable spellings are refused.
    with pytest.raises(HTTPException):
        guard(body=body, content_type="application/hal+json")


def test_a_route_that_names_the_volume_checks_it_without_the_guard(signed_in):
    """The routes with a volume in the body each check it themselves, so the
    answer does not depend on how the guard read the request."""
    from fastapi import HTTPException

    from sbfsem_web.api import routes

    _, app, session = signed_in
    request, _sent = _guard_request(app, session, path_params={}, body=None)

    with pytest.raises(HTTPException) as caught:
        routes.connectivity_query(request, routes.ConnectivityQueryBody(source="i", start_ids=[1178]))
    assert caught.value.status_code == 403 and "NeitzInferiorMonkey" in caught.value.detail

    with pytest.raises(HTTPException) as caught:
        routes.load_neuron(request, routes.LoadNeuronBody(id=1178))
    assert caught.value.status_code == 403 and "NeitzInferiorMonkey" in caught.value.detail


def test_an_uploaded_file_for_a_volume_that_was_not_shared_is_refused(signed_in, request):
    """The volume is inside the upload, so the route checks it once the file is read;
    and the route is a plain function, so reading it does not hold the event loop."""
    import inspect

    from sbfsem_web.api import routes

    client, app, _ = signed_in
    assert not inspect.iscoroutinefunction(routes.import_neuron)
    fixture = request.path.parents[1] / "fixtures" / "i943.json"       # Inferior Monkey; RC1 only is shared
    with fixture.open("rb") as fh:
        response = client.post("/api/neurons/import", files={"file": ("i943.json", fh)})
    assert response.status_code == 403 and "no access to NeitzInferiorMonkey" in response.json()["detail"]
    assert not app.state.service.loaded_neurons()


def test_the_session_endpoint_reports_the_grant(signed_in):
    client, _, _ = signed_in
    body = client.get("/api/viking/session").json()
    assert body["signed_in"] is True
    assert body["user"] == "jkuchen"
    assert body["volumes"] == {"RC1": ["Read", "Annotate"]}
    assert body["unsupported"] == ["Zia"]


def test_signing_out_drops_the_server_side_session(signed_in):
    client, app, session = signed_in
    client.get("/auth/logout", follow_redirects=False)
    assert app.state.viking.store.load(session.sid) is None


def _all_routes(app):
    """Every route, including those inside an included router.

    Recent FastAPI wraps an included router in a ``_IncludedRouter`` whose own
    ``routes`` is empty -- the real ones hang off ``original_router`` -- so a
    naive walk over ``app.routes`` finds none of the API and the sweep below
    would pass by checking nothing.
    """
    found = []

    def walk(holder):
        for route in getattr(holder, "routes", []) or []:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner)
            elif getattr(route, "routes", None):
                walk(route)
            else:
                found.append(route)

    walk(app)
    return found


def test_every_route_that_names_a_volume_is_gated(signed_in):
    """No route may reach its body with a volume Viking did not share.

    Enumerated from the app rather than listed by hand, so a route added
    later is covered on the day it is written -- the failure mode this guards
    against is a new endpoint that quietly forgets to ask.
    """
    client, app, _ = signed_in
    fillers = {
        "source": "RC2",  # granted: RC1 only
        "neuron_id": "1", "location_id": "1", "job_id": "x", "layer": "INL",
        "synapse_type": "ribbon pre", "fmt": "json", "name": "n", "folder": "f",
        "kind": "materials",
    }
    checked = 0
    for route in _all_routes(app):
        path = getattr(route, "path", "")
        if "{source}" not in path or not path.startswith("/api/"):
            continue
        url = path
        for key, value in fillers.items():
            url = url.replace("{" + key + "}", value)
        assert "{" not in url, f"unfilled path parameter in {path}"
        method = next(iter(sorted(getattr(route, "methods", {"GET"}) - {"HEAD", "OPTIONS"})))
        response = client.request(method, url, json={})
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
        checked += 1
    assert checked > 20, f"only {checked} source routes found; the sweep is not working"


def test_gating_can_be_turned_off_without_turning_off_sign_in(signed_in):
    """Authentication and authorization are separable knobs."""
    import dataclasses

    client, app, _ = signed_in
    app.state.viking.config = dataclasses.replace(
        app.state.viking.config, gate_volumes=False
    )
    assert client.get("/api/volumes/RC2").status_code == 200
    names = {v["name"] for v in client.get("/api/volumes").json()}
    assert "RC2" in names


# --- /open, the deep link Viking and the Identity site link to -----------------


def _open_payload(html: str) -> dict:
    """Pull the stamped instructions back out of the served shell."""
    import html as html_mod
    import re

    m = re.search(r'data-open="([^"]*)"', html)
    assert m, "the shell carries no open-link slot"
    raw = html_mod.unescape(m.group(1))
    return json.loads(raw.replace(r"\/", "/")) if raw else {}


def test_open_translates_vikings_volume_name(signed_in):
    """Their contract says the query carries *their* name, not our renderer id."""
    client, _, _ = signed_in
    body = _open_payload(client.get("/open?volume=RC1").text)
    assert body["source"] == "RC1"
    assert body["viking_name"] == "RC1"


def test_open_maps_a_name_that_differs_from_ours(signed_in):
    """``InferiorMonkey`` there is ``NeitzInferiorMonkey`` here."""
    client, _, session = signed_in
    session.volumes["NeitzInferiorMonkey"] = ["Read"]
    body = _open_payload(client.get("/open?volume=InferiorMonkey").text)
    assert body["source"] == "NeitzInferiorMonkey"
    assert body["display_name"] == "Inferior Monkey"
    assert body["viking_name"] == "InferiorMonkey"


def test_open_parses_the_cell_list(signed_in):
    client, _, _ = signed_in
    body = _open_payload(client.get("/open?volume=RC1&cells=1178,5678").text)
    assert body["cells"] == [1178, 5678]


def test_open_accepts_a_known_technique_and_shrugs_off_an_unknown_one(signed_in):
    client, _, _ = signed_in
    good = _open_payload(client.get("/open?volume=RC1&cells=1&technique=voxel2").text)
    assert good["technique"] == "voxel2"
    bad = _open_payload(client.get("/open?volume=RC1&cells=1&technique=nonsense").text)
    # A technique we do not have is not worth refusing the link over.
    assert "technique" not in bad
    assert "note" in bad


def test_open_explains_a_volume_we_cannot_render(signed_in):
    """Viking governs two dozen volumes; this tool renders eight."""
    client, _, _ = signed_in
    body = _open_payload(client.get("/open?volume=Zia").text)
    assert "no renderer" in body["error"]
    assert "cells" not in body


def test_open_refuses_a_volume_this_account_was_not_granted(signed_in):
    client, _, _ = signed_in
    body = _open_payload(client.get("/open?volume=RC2&cells=1").text)
    assert "no access to RC2" in body["error"]
    assert "cells" not in body


def test_open_says_so_when_the_link_names_no_volume(signed_in):
    client, _, _ = signed_in
    assert "did not say which volume" in _open_payload(client.get("/open").text)["error"]


def test_open_caps_the_cell_count(signed_in):
    """A hand-edited URL must not be able to queue a thousand builds."""
    from sbfsem_web.app import MAX_OPEN_CELLS

    client, _, _ = signed_in
    many = ",".join(str(i) for i in range(MAX_OPEN_CELLS + 20))
    body = _open_payload(client.get(f"/open?volume=RC1&cells={many}").text)
    assert len(body["cells"]) == MAX_OPEN_CELLS
    assert "at most" in body["note"]


def test_open_reports_every_problem_with_the_link_not_only_the_last(signed_in):
    """Each problem used to replace the note before it: an unknown technique, an
    unreadable cell id and a bad location arrived as the cell id's note alone."""
    client, _, _ = signed_in
    body = _open_payload(
        client.get("/open?volume=RC1&cells=1,abc&technique=nonsense&location=xyz").text
    )
    assert body["cells"] == [1]
    for part in ("Unknown technique 'nonsense'", "Ignored location 'xyz'", "unreadable cell id"):
        assert part in body["note"], (part, body["note"])


def test_open_says_nothing_when_the_link_has_nothing_to_report(signed_in):
    client, _, _ = signed_in
    body = _open_payload(client.get("/open?volume=RC1&cells=1&technique=voxel2").text)
    assert "note" not in body


def test_open_carries_the_location_to_land_on(signed_in):
    """A Viking context menu on one annotation opens the view on it."""
    client, _, _ = signed_in
    body = _open_payload(client.get("/open?volume=RC1&cells=1178&location=769111").text)
    assert body["location"] == 769111
    assert body["cells"] == [1178]


def test_open_ignores_a_location_that_is_not_an_id(signed_in):
    client, _, _ = signed_in
    for bad in ("abc", "0", "-5", "12.5"):
        body = _open_payload(client.get(f"/open?volume=RC1&location={bad}").text)
        assert "location" not in body, bad
        assert "not a Location ID" in body["note"], bad


def test_the_ordinary_shell_carries_no_open_instructions(signed_in):
    """`/` must not accidentally inherit a deep link's behavior."""
    client, _, _ = signed_in
    assert _open_payload(client.get("/").text) == {}


def test_the_signed_in_name_is_escaped_into_the_page(signed_in):
    """It goes into an attribute: a quote in a name must not end it."""
    client, _, session = signed_in
    session.user = 'x" onload="alert(1)" data-y="<&>'
    html = client.get("/").text
    assert 'data-user="x&quot; onload=&quot;alert(1)&quot; data-y=&quot;&lt;&amp;&gt;"' in html
    assert 'onload="alert(1)"' not in html


def test_a_deep_link_survives_the_login_redirect(signed_in):
    """The query string is the whole point of the link; it must not be dropped."""
    client, _, _ = signed_in
    client.cookies.clear()
    response = client.get("/open?volume=RC1&cells=1178,5678", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert "volume%3DRC1" in location or "volume=RC1" in location
    assert "1178" in location


def test_the_next_parameter_cannot_leave_the_site(signed_in):
    """A login page that forwards anywhere is a phishing tool."""
    client, _, _ = signed_in
    client.cookies.clear()
    for hostile in ("//evil.example/", "https://evil.example/", "/\\evil.example"):
        page = client.get("/login", params={"next": hostile}).text
        assert "evil.example" not in page


def test_the_oauth_state_cookie_is_not_https_only_for_a_localhost_callback(tmp_path, monkeypatch):
    """The registered dev callback is plain http; a Secure cookie never arrives.

    Getting this wrong fails as a state mismatch at the callback, which looks
    nothing like its cause.
    """
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("SBFSEM_VIKING_AUTH", "1")
    monkeypatch.setenv("SBFSEM_VIKING_CLIENT_SECRET", "shh")
    monkeypatch.setenv("SBFSEM_VIKING_REDIRECT_URI", "http://localhost:8765/auth/callback")
    app = create_app()
    session_mw = [m for m in app.user_middleware if "Session" in m.cls.__name__]
    assert session_mw, "the OAuth handshake needs SessionMiddleware"
    assert session_mw[0].kwargs["https_only"] is False


def test_the_oauth_state_cookie_is_https_only_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("SBFSEM_VIKING_AUTH", "1")
    monkeypatch.setenv("SBFSEM_VIKING_CLIENT_SECRET", "shh")
    monkeypatch.delenv("SBFSEM_VIKING_REDIRECT_URI", raising=False)
    app = create_app()
    session_mw = [m for m in app.user_middleware if "Session" in m.cls.__name__]
    assert session_mw[0].kwargs["https_only"] is True


# --- silent sign-in for links that come from Viking ----------------------------


def _offline_client(app, browser=None):
    """The real Authlib client, told its endpoints so it never fetches them.

    ``browser`` is moved to https: the handshake cookie is ``Secure`` with the
    production callback, and over plain http it would never come back.
    """
    client = app.state.viking.client
    client.server_metadata.update({
        "authorization_endpoint": "https://id.example/connect/authorize",
        "_loaded_at": time.time(),
    })
    if browser is not None:
        browser.base_url = "https://testserver"
    return client


def _query(url: str) -> dict[str, str]:
    from urllib.parse import parse_qsl, urlsplit

    return dict(parse_qsl(urlsplit(url).query))


def test_a_signed_out_deep_link_tries_the_identity_server_session_first(signed_in):
    """Viking's menu goes through the identity server; its session is there to reuse."""
    client, _, _ = signed_in
    client.cookies.clear()
    response = client.get("/open?volume=RC1&cells=1178", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/auth/login?silent=1&next=")
    assert _query(location)["next"] == "/open?volume=RC1&cells=1178"


def test_only_deep_links_are_silent(signed_in):
    """`/` still starts at the login page, where a built-in account has a form."""
    client, _, _ = signed_in
    client.cookies.clear()
    location = client.get("/", follow_redirects=False).headers["location"]
    assert location.startswith("/login")


def test_a_silent_sign_in_asks_for_no_prompt(signed_in):
    client, app, _ = signed_in
    client.cookies.clear()
    _offline_client(app, client)
    response = client.get(
        "/auth/login", params={"silent": "1", "next": "/open?volume=RC1"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    sent = _query(response.headers["location"])
    assert sent["prompt"] == "none"
    assert sent["code_challenge_method"] == "S256"


def test_an_ordinary_sign_in_still_shows_their_login_page(signed_in):
    client, app, _ = signed_in
    client.cookies.clear()
    _offline_client(app, client)
    response = client.get("/auth/login", params={"next": "/"}, follow_redirects=False)
    assert "prompt" not in _query(response.headers["location"])


def test_no_session_to_reuse_lands_on_our_login_page_with_the_link_intact(signed_in, caplog):
    """``login_required`` is the expected answer, not an error to show anyone."""
    client, app, _ = signed_in
    client.cookies.clear()
    _offline_client(app, client)
    started = client.get(
        "/auth/login", params={"silent": "1", "next": "/open?volume=RC1&cells=1178"},
        follow_redirects=False,
    )
    state = _query(started.headers["location"])["state"]
    with caplog.at_level("INFO"):
        back = client.get(
            "/auth/callback", params={"error": "login_required", "state": state},
            follow_redirects=False,
        )
    assert back.status_code == 302
    location = back.headers["location"]
    assert location.startswith("/login?next=")
    assert "error" not in _query(location)
    assert _query(location)["next"] == "/open?volume=RC1&cells=1178"
    page = client.get(location).text
    assert "Sign in with Viking" in page
    assert "cancelled or refused" not in page
    # The link survives the button too.
    assert "volume%3DRC1" in page or "volume=RC1" in page


def test_a_refused_interactive_sign_in_still_says_so(signed_in):
    """Cancelling on their login page is not a silent miss."""
    client, app, _ = signed_in
    client.cookies.clear()
    _offline_client(app, client)
    started = client.get("/auth/login", params={"next": "/"}, follow_redirects=False)
    state = _query(started.headers["location"])["state"]
    back = client.get(
        "/auth/callback", params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert back.headers["location"] == "/login?error=viking_denied"


def test_login_required_without_a_silent_attempt_is_still_a_refusal(signed_in):
    """The silent flag lives in our cookie; a crafted callback cannot claim it."""
    client, _, _ = signed_in
    client.cookies.clear()
    back = client.get(
        "/auth/callback", params={"error": "login_required", "state": "x"},
        follow_redirects=False,
    )
    assert back.headers["location"] == "/login?error=viking_denied"


def test_an_abandoned_handshake_is_not_carried_in_the_cookie(signed_in):
    """No code is exchanged after an error, so nothing else would clear it."""
    import base64

    client, app, _ = signed_in
    client.cookies.clear()
    _offline_client(app, client)
    started = client.get(
        "/auth/login", params={"silent": "1", "next": "/open?volume=RC1"},
        follow_redirects=False,
    )
    state = _query(started.headers["location"])["state"]

    def handshake_keys() -> list[str]:
        raw = client.cookies.get("sbfsem_oauth")
        if not raw:                      # an emptied session deletes its cookie
            return []
        payload = raw.split(".")[0]
        data = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))
        return [k for k in data if k.startswith("_state_")]

    assert any(k.endswith(state) for k in handshake_keys())
    client.get(
        "/auth/callback", params={"error": "login_required", "state": state},
        follow_redirects=False,
    )
    assert handshake_keys() == []


def test_a_silent_sign_in_that_did_not_stick_does_not_loop(signed_in, caplog):
    """Signed in over there, no session here: the second try is the login page."""
    client, app, _ = signed_in
    client.cookies.clear()
    _offline_client(app, client)
    first = client.get(
        "/auth/login", params={"silent": "1", "next": "/open?volume=RC1"},
        follow_redirects=False,
    )
    assert first.headers["location"].startswith("https://id.example/")
    with caplog.at_level("WARNING"):
        second = client.get(
            "/auth/login", params={"silent": "1", "next": "/open?volume=RC1"},
            follow_redirects=False,
        )
    assert second.headers["location"].startswith("/login?next=")
    assert "did not leave a session behind" in caplog.text


def test_a_clean_miss_lifts_the_loop_guard(signed_in):
    """Two links in a minute, signed out both times, both get their silent try."""
    client, app, _ = signed_in
    client.cookies.clear()
    _offline_client(app, client)
    params = {"silent": "1", "next": "/open?volume=RC1"}
    started = client.get("/auth/login", params=params, follow_redirects=False)
    state = _query(started.headers["location"])["state"]
    client.get("/auth/callback", params={"error": "login_required", "state": state},
               follow_redirects=False)
    again = client.get("/auth/login", params=params, follow_redirects=False)
    assert again.headers["location"].startswith("https://id.example/")


# --- "Open in Viking" -----------------------------------------------------------


def test_open_in_viking_sends_the_link_straight_to_viking(signed_in):
    """By default the link goes to the client itself, with no launch code.  A
    Viking already open takes it over its pipe; a cold start signs in in
    Viking's own window, selects the volume by name and goes to the place
    (``Program.cs``/``LoginWindow``, Legacy branch).  Since Viking 1.2.67 the
    annotation service refuses the token a launch code is exchanged for, so a
    cold start by code opened a Viking that could load nothing."""
    client, _, _ = signed_in
    response = client.get("/open-in-viking?volume=RC1&location=769111", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "viking://open?volumeName=RC1&location=769111"


def test_open_in_viking_can_go_through_their_launch_page(signed_in):
    """Launch mode ``code``: their ``CreateCode`` mints a one-use code, for the
    day the annotation service accepts the token it is exchanged for."""
    import dataclasses

    client, app, _ = signed_in
    app.state.viking.config = dataclasses.replace(app.state.viking.config, launch_mode="code")
    response = client.get("/open-in-viking?volume=RC1&location=769111", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://identity.codepharm.net:4001/VikingLaunch/CreateCode"
        "?volumeName=RC1&location=769111"
    )


def test_the_launch_mode_is_a_setting(monkeypatch):
    monkeypatch.delenv("SBFSEM_VIKING_LAUNCH_MODE", raising=False)
    assert VikingConfig.from_env().launch_mode == "direct"
    monkeypatch.setenv("SBFSEM_VIKING_LAUNCH_MODE", " Code ")
    assert VikingConfig.from_env().launch_mode == "code"
    for junk in ("", "codes", "0"):
        monkeypatch.setenv("SBFSEM_VIKING_LAUNCH_MODE", junk)
        assert VikingConfig.from_env().launch_mode == "direct"


def test_open_in_viking_speaks_their_volume_names(signed_in):
    """Ours is ``NeitzInferiorMonkey``; Viking (and their ``CreateCode``) know ``InferiorMonkey``."""
    client, _, session = signed_in
    session.volumes["NeitzInferiorMonkey"] = ["Read"]
    for ours in ("NeitzInferiorMonkey", "InferiorMonkey"):
        response = client.get(f"/open-in-viking?volume={ours}", follow_redirects=False)
        assert _query(response.headers["location"]) == {"volumeName": "InferiorMonkey"}


def test_open_in_viking_takes_a_point_that_is_no_annotation(signed_in):
    """``x,y,z,downsample`` in their pixel and section space; negatives are real."""
    client, _, _ = signed_in
    response = client.get(
        "/open-in-viking", params={"volume": "RC1", "location": "200549.4, -160700 1111,8.5"},
        follow_redirects=False,
    )
    assert _query(response.headers["location"])["location"] == "200549,-160700,1111,8.5"
    # Commas stay commas: their parser splits on them.
    assert "location=200549,-160700,1111,8.5" in response.headers["location"]


@pytest.mark.parametrize(
    ("sent", "arrives"),
    [
        ("970", "32"),      # a whole-cell view here: a speck of a volume there
        ("32.5", "32"),
        ("32", "32"),
        ("8.5", "8.5"),     # a closer view is the user's own choice of scale
        ("0.5", "0.5"),
    ],
)
def test_open_in_viking_never_asks_for_a_view_farther_out_than_vikings_default(
    signed_in, sent, arrives
):
    """Viking applies the downsample it is sent with no upper limit of its own."""
    client, _, _ = signed_in
    response = client.get(
        "/open-in-viking", params={"volume": "RC1", "location": f"106891,5440,1134,{sent}"},
        follow_redirects=False,
    )
    assert _query(response.headers["location"])["location"] == f"106891,5440,1134,{arrives}"


def test_a_point_without_a_downsample_is_sent_without_one(signed_in):
    """Viking then starts at its own default; inventing one here would override it."""
    client, _, _ = signed_in
    response = client.get(
        "/open-in-viking", params={"volume": "RC1", "location": "106891,5440,1134"},
        follow_redirects=False,
    )
    assert _query(response.headers["location"])["location"] == "106891,5440,1134"


def test_the_farthest_view_is_a_setting(signed_in, monkeypatch):
    client, app, _ = signed_in
    monkeypatch.setenv("SBFSEM_VIKING_LAUNCH_MAX_DOWNSAMPLE", "64")
    assert VikingConfig.from_env().launch_max_downsample == 64.0
    for junk in ("", "zero", "0", "-4", "nan", "inf"):
        monkeypatch.setenv("SBFSEM_VIKING_LAUNCH_MAX_DOWNSAMPLE", junk)
        assert VikingConfig.from_env().launch_max_downsample == 32.0
    assert app.state.viking.config.launch_max_downsample == 32.0


@pytest.mark.parametrize("location", ["abc", "1,2", "1,2,3,4,5", "1,2,3,0", "0", "nan,1,2"])
def test_open_in_viking_refuses_a_place_it_cannot_read(signed_in, location):
    client, _, _ = signed_in
    response = client.get(
        "/open-in-viking", params={"volume": "RC1", "location": location},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "location" not in response.headers


def test_open_in_viking_refuses_a_volume_this_account_was_not_granted(signed_in):
    client, _, _ = signed_in
    response = client.get("/open-in-viking?volume=RC2&location=1", follow_redirects=False)
    assert response.status_code == 403
    assert "no access to RC2" in response.text


def test_open_in_viking_knows_the_demo_is_not_a_viking_volume(signed_in):
    client, _, _ = signed_in
    response = client.get("/open-in-viking?volume=DemoVolume", follow_redirects=False)
    assert response.status_code == 404


def test_open_in_viking_needs_a_volume(signed_in):
    client, _, _ = signed_in
    assert client.get("/open-in-viking", follow_redirects=False).status_code == 400
    assert client.get("/open-in-viking?volume=Nowhere", follow_redirects=False).status_code == 404


def test_the_launch_page_hands_over_from_our_own_origin(signed_in):
    """``page=1`` answers with our page instead of the redirect, because only a
    page of ours can say Viking runs on Windows only and notice a hand-off that
    nothing answered.  The target it navigates to is the same one."""
    client, _, _ = signed_in
    response = client.get("/open-in-viking?volume=RC1&location=769111&page=1",
                          follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # A one-use launch target must not be cached or shared.
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "viking://open?volumeName=RC1&amp;location=769111" in body
    assert "Viking client can only be run on Windows." in body
    assert "Viking client is not installed." in body
    assert "Viking-win-Setup.exe" in body
    # RC1, not a bare 769111: an ID always travels with its volume.
    assert "RC1, at Location 769111." in body
    assert "__SBFSEM_" not in body


def test_open_in_viking_can_go_to_the_unsigned_test_build(signed_in):
    """Viking Test installs beside the signed Viking and registers ``viking-test://``
    only (``VikingChannelIdentity.cs``, Legacy branch); it reads the same query.
    ``client=test`` is the same link on its scheme."""
    client, _, _ = signed_in
    for client_arg in ("test", " Test "):
        response = client.get(
            "/open-in-viking", params={"volume": "RC1", "location": "769111", "client": client_arg},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "viking-test://open?volumeName=RC1&location=769111"
    # The signed client is what it was: no client, a blank one, or the name.
    for params in ({}, {"client": ""}, {"client": "viking"}):
        response = client.get(
            "/open-in-viking", params={"volume": "RC1", "location": "769111", **params},
            follow_redirects=False,
        )
        assert response.headers["location"] == "viking://open?volumeName=RC1&location=769111"


def test_the_test_build_is_always_the_direct_link_whatever_the_launch_mode(signed_in):
    """Their ``CreateCode`` redirects to ``viking://`` only, which the signed client
    handles; the test build's link therefore never goes through it."""
    import dataclasses

    client, app, _ = signed_in
    app.state.viking.config = dataclasses.replace(app.state.viking.config, launch_mode="code")
    test = client.get("/open-in-viking?volume=RC1&location=769111&client=test",
                      follow_redirects=False)
    assert test.headers["location"] == "viking-test://open?volumeName=RC1&location=769111"
    signed = client.get("/open-in-viking?volume=RC1&location=769111&client=viking",
                        follow_redirects=False)
    assert signed.headers["location"].startswith(
        "https://identity.codepharm.net:4001/VikingLaunch/CreateCode?")


def test_open_in_viking_refuses_a_client_it_does_not_know(signed_in):
    client, _, _ = signed_in
    for query in ("client=beta", "client=viking-test", "client=test&then=beta",
                  "client=test&then=test", "client=viking&then=viking"):
        for page in ("0", "1"):
            response = client.get(f"/open-in-viking?volume=RC1&location=1&page={page}&{query}",
                                  follow_redirects=False)
            assert response.status_code == 400, (query, page, response.text)


def test_the_launch_page_knows_which_program_to_try_first_and_which_after(signed_in):
    """The page tries ``client`` and, when nothing answers it within its wait,
    ``then``: a machine with only the test build has no handler for ``viking://``,
    and that silence is how it is found.  The message that nothing is installed is
    the same either way."""
    client, _, _ = signed_in
    body = client.get("/open-in-viking?volume=RC1&location=769111&page=1"
                      "&client=viking&then=test").text
    assert 'data-target="viking://open?volumeName=RC1&amp;location=769111"' in body
    assert 'data-client="viking"' in body and 'data-client-name="Viking"' in body
    assert 'data-then="test"' in body and 'data-then-name="Viking Test"' in body
    assert 'data-then-target="viking-test://open?volumeName=RC1&amp;location=769111"' in body
    assert "Starting Viking…" in body
    assert "Viking client is not installed." in body
    assert "Viking-win-Setup.exe" in body
    assert "__SBFSEM_" not in body

    body = client.get("/open-in-viking?volume=RC1&location=769111&page=1"
                      "&client=test&then=viking").text
    assert 'data-target="viking-test://open?volumeName=RC1&amp;location=769111"' in body
    assert 'data-client="test"' in body and 'data-client-name="Viking Test"' in body
    assert 'data-then="viking"' in body
    assert 'data-then-target="viking://open?volumeName=RC1&amp;location=769111"' in body
    assert "Starting Viking Test…" in body
    assert "Viking client is not installed." in body
    assert "__SBFSEM_" not in body

    # No second program: nothing to fall back to, and the page is as it was.
    body = client.get("/open-in-viking?volume=RC1&location=769111&page=1").text
    assert 'data-then=""' in body and 'data-then-target=""' in body
    assert 'data-client="viking"' in body
    assert "__SBFSEM_" not in body


def test_the_launch_page_keeps_what_each_launch_saw(signed_in):
    """What took the focus, and what did not, is kept for the next click
    (``static/js/vikingclient.js``), and a launch never waits for it."""
    client, _, _ = signed_in
    body = client.get("/open-in-viking?volume=RC1&location=769111&page=1&client=viking&then=test").text
    assert "import('/static/js/vikingclient.js')" in body
    assert "recordFromLaunch(window.localStorage, id, handled)" in body
    # The other program is a button: Chrome refuses a launch a script starts seconds after
    # the page loaded ("a user gesture is required"), so it is never started by a timer.
    assert 'id="tryOther"' in body and "Open in Viking Test" in body
    assert "tryTheOther" not in body
    # The wait and the pinned strings of the hand-off are unchanged.
    assert "var WAIT_MS = 6000;" in body
    assert "window.addEventListener('focus', function () { if (away) done(); });" in body


def test_the_launch_page_says_where_a_point_launch_is_going(signed_in):
    client, _, _ = signed_in
    body = client.get("/open-in-viking?volume=RC1&location=106891,5440,1134&page=1").text
    assert "RC1, at 106891,5440,1134 in Viking&#x27;s own x, y, section." in body
    body = client.get("/open-in-viking?volume=RC1&page=1").text
    assert "RC1, at the view Viking opens with." in body


def test_the_launch_page_closes_its_tab_after_the_hand_off_and_not_under_sign_in(signed_in):
    """The tab closes itself once the link has gone to Viking: the focus back
    after it left, or hidden while it is still the page.  Where the browser
    refuses, the page says the tab can be closed.  Being replaced by the
    identity server's sign-in page hides a page too (Firefox 156: pagehide,
    then hidden), and a close made on that event shut the sign-in tab, so the
    close on hidden is queued and skipped after pagehide.  The behavior was
    measured in headless Chrome and Firefox (docs/viking-identity.md); this
    pins the page that was measured."""
    client, _, _ = signed_in
    body = client.get("/open-in-viking?volume=RC1&location=769111&page=1").text
    assert "window.addEventListener('focus', function () { if (away) done(); });" in body
    assert "window.addEventListener('pagehide', function () { leaving = true; gone(); });" in body
    assert "setTimeout(function () { if (!leaving && document.hidden) done(); }, 0);" in body
    assert "This tab closes itself when you come back to it." in body
    assert "This tab can be closed." in body


def test_the_launch_page_is_refused_exactly_as_the_redirect_is(signed_in):
    """Volume gating and bad input come first; no page is built for either."""
    client, _, _ = signed_in
    assert client.get("/open-in-viking?volume=RC2&location=1&page=1").status_code == 403
    assert client.get("/open-in-viking?page=1").status_code == 400
    assert client.get("/open-in-viking?volume=Nowhere&page=1").status_code == 404
    assert client.get("/open-in-viking?volume=DemoVolume&page=1").status_code == 404


def test_the_launch_page_is_a_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("SBFSEM_VIKING_LAUNCH_URL", " https://id.example/VikingLaunch/CreateCode ")
    assert VikingConfig.from_env().launch_url == "https://id.example/VikingLaunch/CreateCode"


def test_the_volume_list_names_each_volume_for_viking(signed_in):
    """The interface offers "Open in Viking" only where this is set."""
    client, _, _ = signed_in
    listed = {v["name"]: v["viking_name"] for v in client.get("/api/volumes").json()}
    assert listed["RC1"] == "RC1"
    assert listed.get("DemoVolume") is None
