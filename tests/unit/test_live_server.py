"""Behavior that only shows on a real socket: the test client hands a request over whole.

A response sent while the client is still sending its body resets the connection
under it, and the client sees a broken pipe instead of the answer.  uvicorn runs
here in a thread, on a port of the kernel's choosing.
"""

from __future__ import annotations

import http.client
import threading
import time

import pytest
import uvicorn

from sbfsem_web.app import create_app


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("SBFSEM_SAVE_DIR", str(tmp_path / "saves"))
    monkeypatch.setenv("SBFSEM_CELL_RENDER_DIR", str(tmp_path / "renders"))
    monkeypatch.setenv("SBFSEM_LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("SBFSEM_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("SBFSEM_OFFLINE", "1")
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the server did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield port
    server.should_exit = True
    thread.join(10)


def _request(port: int, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        # Connection: close, as urllib and curl send it: uvicorn then closes the connection the
        # moment it has answered, and with part of the body still unread that is a reset.  (A
        # keep-alive client, nginx's upstream connection included, is read to the end.)
        connection.request(method, path, body=body,
                           headers={"Content-Type": "application/json", "Connection": "close"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_a_refused_oversize_save_is_answered_after_the_body_is_read_not_over_it(live):
    """The 507 reached the client as a broken pipe while the 6 MB was still on its way."""
    assert _request(live, "POST", "/api/saves", b'{"name": "Big"}')[0] == 201
    # Large enough that the client is still sending when the answer is ready (the limit is
    # 5 MB; nginx lets 64 MB through): a body the kernel's buffers swallow whole hides it.
    megabytes = 40
    body = b'{"blob": "' + b"x" * (megabytes * 1024 * 1024) + b'"}'
    for _ in range(3):  # a race rather than a rule: ask a few times
        status, answer = _request(live, "PUT", "/api/saves/Big/huge", body)
        assert status == 507 and b"kB exceeds the 5120 kB limit" in answer
    assert _request(live, "PUT", "/api/saves/Big/small", b'{"x": 1}')[0] == 200
