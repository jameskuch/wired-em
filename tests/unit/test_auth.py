"""The login layer: passwords, tokens, the user store, and the app behind it."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sbfsem_web.auth import (
    COOKIE_NAME,
    SESSION_TTL,
    AuthManager,
    SessionSigner,
    UserStore,
    hash_password,
    main,
    verify_password,
)

# --- passwords ---------------------------------------------------------------


def test_password_roundtrip():
    stored = hash_password("hunter2")
    assert stored.startswith("pbkdf2_sha256$")
    assert "hunter2" not in stored
    assert verify_password("hunter2", stored)
    assert not verify_password("hunter3", stored)


def test_same_password_salts_differently():
    assert hash_password("pw") != hash_password("pw")


def test_verify_survives_garbage():
    for stored in ("", "plaintext", "a$b$c$d", "pbkdf2_sha256$x$zz$zz"):
        assert not verify_password("pw", stored)


# --- session tokens ----------------------------------------------------------


def test_token_roundtrip():
    signer = SessionSigner(b"secret")
    token = signer.make_token("alice", now=1000.0)
    assert signer.read_token(token, now=1000.0) == "alice"
    assert signer.read_token(token, now=1000.0 + SESSION_TTL + 1) is None


def test_token_username_may_contain_delimiters():
    signer = SessionSigner(b"secret")
    assert signer.read_token(signer.make_token("a.b:c")) == "a.b:c"


def test_tampered_or_foreign_tokens_rejected():
    signer = SessionSigner(b"secret")
    token = signer.make_token("alice")
    assert signer.read_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert signer.read_token("") is None
    assert signer.read_token("not.a.token") is None
    assert SessionSigner(b"other-secret").read_token(token) is None


# --- the user store ----------------------------------------------------------


def test_store_add_check_remove(tmp_path):
    store = UserStore(tmp_path / "users.json")
    assert not store.configured()
    store.add("alice", "pw")
    assert store.configured()
    assert store.check("alice", "pw")
    assert not store.check("alice", "wrong")
    assert not store.check("bob", "pw")
    store.remove("alice")
    # Removing the last user locks the site rather than falling open; only
    # deleting users.json itself turns login off.
    assert store.configured()
    assert store.names() == []


def test_store_notices_external_edits(tmp_path):
    path = tmp_path / "users.json"
    store = UserStore(path)
    UserStore(path).add("alice", "pw")  # another process writes the file
    assert store.check("alice", "pw")
    path.unlink()
    assert not store.configured()


def test_malformed_users_file_fails_closed(tmp_path):
    path = tmp_path / "users.json"
    path.write_text("{ not json")
    store = UserStore(path)
    assert store.configured()  # auth stays ON ...
    assert not store.check("alice", "pw")  # ... but nobody gets in
    assert store.names() == []


def test_cli(tmp_path, capsys):
    args = ["--dir", str(tmp_path)]
    assert main([*args, "add", "alice", "--password", "pw"]) == 0
    assert main([*args, "list"]) == 0
    assert "alice" in capsys.readouterr().out
    data = json.loads((tmp_path / "users.json").read_text())
    assert set(data) == {"alice"} and "pw" not in data["alice"]
    assert main([*args, "remove", "alice"]) == 0
    assert main([*args, "remove", "alice"]) == 1
    assert main([*args, "add", "bob", "--password", ""]) == 1


# --- the app behind the login ------------------------------------------------


@pytest.fixture()
def locked_client(monkeypatch, tmp_path):
    from sbfsem_web.app import create_app

    auth_dir = tmp_path / "auth"
    UserStore(auth_dir / "users.json").add("alice", "sesame")
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(auth_dir))
    with TestClient(create_app()) as client:
        yield client


def test_open_when_no_users_configured(monkeypatch, tmp_path):
    from sbfsem_web.app import create_app

    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "empty"))
    with TestClient(create_app()) as client:
        assert client.get("/", follow_redirects=False).status_code == 200
        # /login on an open app bounces straight back in.
        response = client.get("/login", follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (302, "/")


def test_locked_app_redirects_pages_and_401s_api(locked_client):
    response = locked_client.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (302, "/login?next=/")
    assert locked_client.get("/static/js/app.js", follow_redirects=False).status_code == 302
    assert locked_client.get("/api/neurons").status_code == 401
    assert locked_client.get("/api/health").status_code == 200  # monitoring stays open
    assert locked_client.get("/login").status_code == 200


def test_login_logout_flow(locked_client):
    bad = locked_client.post(
        "/login", data={"username": "alice", "password": "wrong", "next": "/"}
    )
    assert bad.status_code == 401 and "Wrong username or password" in bad.text
    assert COOKIE_NAME not in locked_client.cookies

    good = locked_client.post(
        "/login",
        data={"username": "alice", "password": "sesame", "next": "/"},
        follow_redirects=False,
    )
    assert (good.status_code, good.headers["location"]) == (303, "/")
    assert COOKIE_NAME in locked_client.cookies

    index = locked_client.get("/")
    assert index.status_code == 200 and 'data-user="alice"' in index.text
    assert locked_client.get("/api/neurons").status_code == 200

    locked_client.get("/logout", follow_redirects=False)
    assert locked_client.get("/api/neurons").status_code == 401


def test_login_rejects_offsite_next(locked_client):
    response = locked_client.post(
        "/login",
        data={"username": "alice", "password": "sesame", "next": "//evil.example"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


def test_removing_a_user_kills_their_session(locked_client, tmp_path):
    locked_client.post(
        "/login", data={"username": "alice", "password": "sesame", "next": "/"}
    )
    assert locked_client.get("/api/neurons").status_code == 200
    UserStore(tmp_path / "auth" / "users.json").remove("alice")
    assert locked_client.get("/api/neurons").status_code == 401


def test_loopback_requests_bypass_login_for_the_deploy_script(monkeypatch, tmp_path):
    from sbfsem_web.app import create_app

    auth_dir = tmp_path / "auth"
    UserStore(auth_dir / "users.json").add("alice", "sesame")
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(auth_dir))
    with TestClient(create_app(), client=("127.0.0.1", 4242)) as client:
        assert client.get("/api/neurons").status_code == 200


def test_store_invite_lifecycle(tmp_path):
    store = UserStore(tmp_path / "users.json")
    code = store.invite("alice", ttl=100, now=1000.0)
    assert store.configured()
    assert store.status("alice", now=1000.0) == "invited"
    assert not store.check("alice", code)  # a pending invite cannot log in
    assert store.find_invite(code, now=1000.0) == "alice"
    assert store.find_invite("wrong-code", now=1000.0) is None
    assert store.find_invite(code, now=1101.0) is None  # expired
    assert "pw" not in (tmp_path / "users.json").read_text()  # code stored hashed

    assert store.redeem(code, "chosen-pw", now=1000.0) == "alice"
    assert store.check("alice", "chosen-pw")
    assert store.status("alice") == "active"
    assert store.redeem(code, "again", now=1000.0) is None  # one use only


def test_invite_resets_an_active_account(tmp_path):
    store = UserStore(tmp_path / "users.json")
    store.add("alice", "old-pw")
    store.invite("alice")
    assert not store.check("alice", "old-pw")
    assert store.status("alice") == "invited"


def test_cli_invite(tmp_path, capsys):
    args = ["--dir", str(tmp_path)]
    assert main([*args, "invite", "alice", "--url", "https://example.org/"]) == 0
    out = capsys.readouterr().out
    assert "https://example.org/welcome?code=" in out
    assert main([*args, "list"]) == 0
    assert "alice  (invited)" in capsys.readouterr().out
    code = out.split("code=")[1].split()[0]
    assert UserStore(tmp_path / "users.json").redeem(code, "chosen-pw") == "alice"


def test_welcome_flow(locked_client, tmp_path):
    store = UserStore(tmp_path / "auth" / "users.json")
    code = store.invite("bob")

    assert locked_client.get("/welcome", follow_redirects=False).status_code == 404
    assert locked_client.get("/welcome?code=nope").status_code == 404
    page = locked_client.get(f"/welcome?code={code}")
    assert page.status_code == 200 and "Welcome, bob" in page.text

    short = locked_client.post("/welcome", data={"code": code, "password": "short", "confirm": "short"})
    assert short.status_code == 400 and "at least 8" in short.text
    mismatch = locked_client.post(
        "/welcome", data={"code": code, "password": "long-enough", "confirm": "long-enuff"}
    )
    assert mismatch.status_code == 400 and "do not match" in mismatch.text

    done = locked_client.post(
        "/welcome",
        data={"code": code, "password": "long-enough", "confirm": "long-enough"},
        follow_redirects=False,
    )
    assert (done.status_code, done.headers["location"]) == (303, "/")
    assert COOKIE_NAME in locked_client.cookies  # signed in on the spot
    assert locked_client.get("/api/neurons").status_code == 200
    # The code is spent; a second visitor gets nothing.
    assert locked_client.get(f"/welcome?code={code}").status_code == 404
    # And the chosen password works for a normal sign-in.
    assert store.check("bob", "long-enough")


def test_auth_manager_ignores_stale_cookie_secret(tmp_path):
    manager = AuthManager(tmp_path)
    UserStore(tmp_path / "users.json").add("alice", "pw")
    token = manager.login("alice", "pw")
    assert token and manager.current_user(token) == "alice"
    assert manager.current_user("junk") is None
    assert manager.login("alice", "wrong") is None


# --- the size cap on saved documents runs behind the login -------------------------


@pytest.fixture()
def locked_app(monkeypatch, tmp_path):
    """The app behind the built-in login, with its saves in a directory of its own."""
    from sbfsem_web.app import create_app

    auth_dir = tmp_path / "auth"
    UserStore(auth_dir / "users.json").add("alice", "sesame")
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(auth_dir))
    monkeypatch.setenv("SBFSEM_SAVE_DIR", str(tmp_path / "saves"))
    monkeypatch.setenv("SBFSEM_CELL_RENDER_DIR", str(tmp_path / "renders"))
    monkeypatch.setenv("SBFSEM_LIBRARY_DIR", str(tmp_path / "library"))
    return create_app()


def _call_asgi(app, method, path, *, body_bytes, headers=()):
    """One request straight into the ASGI app, counting how often it reads the body."""
    import asyncio

    reads = {"n": 0}
    chunks = [b"x" * 65536 for _ in range(body_bytes // 65536)] + [b"x" * (body_bytes % 65536)]
    pending = iter(chunks)

    async def receive():
        reads["n"] += 1
        chunk = next(pending, None)
        if chunk is None:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunk, "more_body": True}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "path": path, "raw_path": path.encode(), "root_path": "", "scheme": "http",
        "query_string": b"", "client": ("203.0.113.5", 5000), "server": ("testserver", 80),
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(body_bytes).encode()), *headers],
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, reads["n"]


def test_an_oversize_save_from_a_visitor_who_is_not_signed_in_is_a_401_and_is_not_read(locked_app):
    """The cap was registered after the login check, so it ran first: a caller with no
    session got a 507, and the server read the whole body (nginx lets 64 MB through)
    before any login check, where every other /api route answers 401 unread."""
    six_mb = 6 * 1024 * 1024
    for method, path in (("PUT", "/api/saves/Nope/doc"), ("POST", "/api/saves")):
        status, reads = _call_asgi(locked_app, method, path, body_bytes=six_mb)
        assert status == 401, f"{method} {path} -> {status}"
        assert reads == 0, f"{method} {path}: the body was read {reads} times for a caller who is not signed in"


def test_a_signed_in_user_still_gets_the_507_for_an_oversize_save(locked_app):
    with TestClient(locked_app) as client:
        client.post("/login", data={"username": "alice", "password": "sesame", "next": "/"})
        assert client.post("/api/saves", json={"name": "Big"}).status_code == 201
        body = b'{"blob": "' + b"x" * (6 * 1024 * 1024) + b'"}'
        response = client.put("/api/saves/Big/huge", content=body,
                              headers={"content-type": "application/json"})
        assert response.status_code == 507 and "6144 kB exceeds the 5120 kB limit" in response.json()["detail"]
