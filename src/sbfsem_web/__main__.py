"""``sbfsem-web`` console entry point."""

from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser


def open_when_listening(host: str, port: int, *, timeout_s: float = 60.0, every_s: float = 0.1) -> threading.Thread:
    """Open the browser at the server once something answers on its port.

    ``--open`` used to call ``webbrowser.open`` before uvicorn had bound the port, so the
    browser's first request was refused (a "can't connect" page for as long as the
    imports and the start-up took).  This waits in a thread of its own for the connection
    to be accepted, and gives up quietly after ``timeout_s`` (the server failed to start,
    which uvicorn has already said).
    """
    # A wildcard address is what to listen on, not where to connect.
    probe = {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}.get(host, host)

    def wait_and_open() -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((probe, port), timeout=1.0):
                    break
            except OSError:
                time.sleep(every_s)
        else:
            return
        webbrowser.open(f"http://{host}:{port}/")

    thread = threading.Thread(target=wait_and_open, name="open-browser", daemon=True)
    thread.start()
    return thread


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the SBFSEM-pytools web interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--open", action="store_true", help="open a browser once the server starts")
    args = parser.parse_args()

    import uvicorn

    if args.open:
        open_when_listening(args.host, args.port)
    uvicorn.run(
        "sbfsem_web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
