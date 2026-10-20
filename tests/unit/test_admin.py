"""The Settings tab's debug tools: who may use the server's, and what each one does."""

from __future__ import annotations

import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sbfsem.geometry.mesh import Mesh
from sbfsem.service.meshcache import MeshDiskCache
from sbfsem_web.api import admin
from sbfsem_web.auth import UserStore

JSON = {"Content-Type": "application/json"}


def _mesh(n=10):
    verts = np.random.default_rng(0).random((n, 3))
    return Mesh(vertices=verts, faces=np.array([[0, 1, 2], [2, 3, 4]]))


@pytest.fixture()
def open_client(monkeypatch, tmp_path):
    """No sign-in configured: a local checkout, where everyone is an admin."""
    from sbfsem_web.app import create_app

    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "no-auth"))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture()
def signed_in(monkeypatch, tmp_path):
    """Sign-in on, and alice signed in; who is an admin is up to each test."""
    from sbfsem_web.app import create_app

    auth_dir = tmp_path / "auth"
    UserStore(auth_dir / "users.json").add("alice", "sesame")
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(auth_dir))
    monkeypatch.delenv(admin.ADMIN_ENV, raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    with TestClient(create_app()) as client:
        client.post("/login", data={"username": "alice", "password": "sesame", "next": "/"})
        yield client


# --- who ---------------------------------------------------------------------


def test_everyone_is_an_admin_where_no_sign_in_is_configured(open_client):
    me = open_client.get("/api/admin/whoami").json()
    assert me["admin"] is True and me["login"] is False
    status = open_client.get("/api/admin/status")
    assert status.status_code == 200
    body = status.json()
    for key in ("version", "assets", "instance", "uptime_s", "memory", "disk_cache", "pool", "builds"):
        assert key in body, key
    assert body["supervised"] is False


def test_a_signed_in_user_not_named_gets_none_of_the_servers_tools(signed_in):
    me = signed_in.get("/api/admin/whoami").json()
    assert me == {"user": "alice", "admin": False, "login": True,
                  "admins_named": False, "admin_setting": admin.ADMIN_ENV}
    refused = signed_in.get("/api/admin/status")
    assert refused.status_code == 403 and "alice" in refused.json()["detail"]
    for path, word in (("clear-renders", "clear"), ("recycle-workers", "recycle"), ("restart", "restart")):
        answer = signed_in.post(f"/api/admin/{path}", json={"confirm": word})
        assert answer.status_code == 403, path
    # Her own browser's cache is hers to clear.
    assert signed_in.post("/api/browser/clear-cache").status_code == 200


def test_a_name_in_the_admin_list_matches_without_case(signed_in, monkeypatch):
    monkeypatch.setenv(admin.ADMIN_ENV, " Alice , bob")
    me = signed_in.get("/api/admin/whoami").json()
    assert me["admin"] is True and me["admins_named"] is True
    assert signed_in.get("/api/admin/status").status_code == 200


def test_nobody_signed_out_reaches_the_tools(monkeypatch, tmp_path):
    from sbfsem_web.app import create_app

    auth_dir = tmp_path / "auth"
    UserStore(auth_dir / "users.json").add("alice", "sesame")
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(auth_dir))
    monkeypatch.setenv(admin.ADMIN_ENV, "alice")
    with TestClient(create_app()) as client:
        assert client.get("/api/admin/status").status_code == 401
        assert client.post("/api/admin/restart", json={"confirm": "restart"}).status_code == 401
        assert client.post("/api/browser/clear-cache").status_code == 401


# --- the actions ---------------------------------------------------------------


@pytest.mark.parametrize("send", [
    {"content": b'{"confirm": "clear"}', "headers": {"Content-Type": "text/plain"}},
    {"content": b'{"confirm": "clear"}'},                     # no declared type at all
    {"json": {"confirm": "restart"}},                         # the wrong action's word
    {"json": ["clear"]},
    {},
])
def test_an_action_runs_only_for_a_json_body_that_names_it(open_client, send):
    """Another site can send a form or a typeless body without asking first; never JSON."""
    answer = open_client.post("/api/admin/clear-renders", **send)
    assert answer.status_code == 400
    assert "application/json" in answer.json()["detail"]


def test_clearing_renders_forgets_the_surfaces_held_and_with_disk_the_files(open_client):
    service = open_client.app.state.service
    service._cache_mesh(("k", 1), _mesh())
    service._cache_mesh(("k", 2), _mesh())
    answer = open_client.post("/api/admin/clear-renders", json={"confirm": "clear"})
    assert answer.status_code == 200
    assert answer.json()["memory_meshes"] == 2 and answer.json()["disk_files"] == 0
    assert service.memory_report()["meshes_cached"] == 0

    disk = service._disk_meshes
    if disk is None:
        pytest.skip("this service has no disk cache")
    before = disk.stats()["files"]
    disk.put(("admin-test", 1), _mesh())
    answer = open_client.post("/api/admin/clear-renders", json={"confirm": "clear", "disk": True})
    assert answer.json()["disk_files"] == before + 1 and disk.stats()["files"] == 0


def test_the_disk_cache_clears_its_meshes_and_leaves_a_write_in_progress(tmp_path):
    cache = MeshDiskCache(tmp_path / "meshes")
    keys = [("i", 1, "cylinder"), ("i", 2, "voxel2")]
    for key in keys:
        cache.put(key, _mesh())
    writing = cache.path(keys[0]).with_suffix(".npz.123.456.tmp")
    writing.write_bytes(b"half a mesh")
    gone = cache.clear()
    assert gone["files"] == 2 and gone["bytes"] > 0
    assert all(cache.get(key) is None for key in keys)
    assert writing.exists()
    assert MeshDiskCache(tmp_path / "absent").clear() == {"files": 0, "bytes": 0}


def test_recycling_with_no_pool_says_so(open_client):
    answer = open_client.post("/api/admin/recycle-workers", json={"confirm": "recycle"})
    assert answer.status_code == 200 and answer.json() == {"recycled": False}


def test_recycling_replaces_the_build_pool_without_counting_it_a_heavy_one(i943_path):
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    svc = SbfsemService(offline=True, build_processes=1, build_recycle_mb=0)
    svc.add_neuron(read_legacy_json(i943_path))
    try:
        assert svc.recycle_build_workers() is False      # no pool made yet
        svc.render_mesh(943, "i", method="cylinder", smooth_iterations=0)
        first = svc._process_pool
        assert first is not None
        assert svc.recycle_build_workers() is True
        assert svc._process_pool not in (None, first)
        assert svc.pool_recycles == 0                     # not for what a worker held
        assert svc.build_pool_report()["pool_running"] is True
        again = svc.render_mesh(943, "i", method="cylinder", smooth_iterations=0, pathway="adaptive_taubin")
        assert again.n_faces > 0
    finally:
        svc.close()


def test_a_server_systemd_did_not_start_refuses_to_restart(open_client):
    answer = open_client.post("/api/admin/restart", json={"confirm": "restart"})
    assert answer.status_code == 409 and "systemd" in answer.json()["detail"]
    assert getattr(open_client.app.state, "restart_exit", None) is None


def test_a_restart_stops_the_server_once_and_exits_with_the_code_the_unit_restarts_for(
        open_client, monkeypatch):
    timers = []

    class Timer:
        def __init__(self, delay, fn, args=()):
            self.delay, self.fn, self.args, self.daemon = delay, fn, args, False
            timers.append(self)

        def start(self):
            pass

    monkeypatch.setenv("INVOCATION_ID", "abc")
    monkeypatch.setattr(admin.threading, "Timer", Timer)
    first = open_client.post("/api/admin/restart", json={"confirm": "restart"})
    assert first.status_code == 202 and first.json()["restarting"] is True
    second = open_client.post("/api/admin/restart", json={"confirm": "restart"})
    assert second.status_code == 202
    # One stop and one deadline, however often it is pressed.
    assert len(timers) == 2
    stop, deadline = timers
    assert (stop.fn, stop.args) == (os.kill, (os.getpid(), admin.signal.SIGTERM))
    assert deadline.daemon and deadline.delay > stop.delay
    assert open_client.app.state.restart_exit == admin.RESTART_EXIT_CODE == 75

    exits = []
    monkeypatch.setattr(admin.os, "_exit", exits.append)
    admin.finish_restart(open_client.app)
    assert exits == [75]
    del open_client.app.state.restart_exit
    admin.finish_restart(open_client.app)
    assert exits == [75], "a shutdown nobody asked to restart exits as before"


def test_the_unit_restarts_for_the_code_and_does_not_count_it_a_failure():
    from pathlib import Path

    unit = (Path(__file__).resolve().parents[2] / "deploy" / "sbfsem-web.service").read_text(encoding="utf-8")
    code = admin.RESTART_EXIT_CODE
    assert "Restart=on-failure" in unit
    assert f"RestartForceExitStatus={code}" in unit and f"SuccessExitStatus={code}" in unit
    assert f"Environment={admin.ADMIN_ENV}=" in unit


def test_clearing_the_browser_cache_tells_the_browser_and_keeps_its_settings(open_client):
    answer = open_client.post("/api/browser/clear-cache")
    assert answer.status_code == 200
    assert answer.headers["Clear-Site-Data"] == '"cache"'    # not "storage" or "cookies"
    assert answer.headers["Cache-Control"] == "no-store"
