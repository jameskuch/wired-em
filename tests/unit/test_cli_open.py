"""``sbfsem-web --open`` opens the browser when the server answers, not before it binds."""

from __future__ import annotations

import socket
import time

from sbfsem_web.__main__ import open_when_listening


def test_the_browser_is_opened_only_once_the_port_answers(monkeypatch):
    opened: list[tuple[str, float]] = []
    monkeypatch.setattr("sbfsem_web.__main__.webbrowser.open", lambda url: opened.append((url, time.monotonic())))
    server = socket.socket()
    try:
        server.bind(("127.0.0.1", 0))  # bound, not yet listening: a connection is refused
        port = server.getsockname()[1]
        thread = open_when_listening("127.0.0.1", port, every_s=0.02)
        time.sleep(0.3)
        assert opened == [], "the server had not started yet"
        listening_at = time.monotonic()
        server.listen()
        thread.join(5)
        assert not thread.is_alive()
        assert [url for url, _ in opened] == [f"http://127.0.0.1:{port}/"]
        assert opened[0][1] >= listening_at
    finally:
        server.close()


def test_it_gives_up_quietly_when_the_server_never_starts(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("sbfsem_web.__main__.webbrowser.open", opened.append)
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))  # a port nothing listens on
    port = holder.getsockname()[1]
    try:
        thread = open_when_listening("127.0.0.1", port, timeout_s=0.3, every_s=0.02)
        thread.join(5)
        assert not thread.is_alive() and opened == []
    finally:
        holder.close()


def test_a_wildcard_host_is_probed_on_loopback(monkeypatch):
    probed: list[tuple] = []

    def refuse(address, timeout=None):
        probed.append(address)
        raise OSError("refused")

    monkeypatch.setattr("sbfsem_web.__main__.socket.create_connection", refuse)
    monkeypatch.setattr("sbfsem_web.__main__.webbrowser.open", lambda url: None)
    open_when_listening("0.0.0.0", 8765, timeout_s=0.1, every_s=0.02).join(5)
    assert probed and set(probed) == {("127.0.0.1", 8765)}
