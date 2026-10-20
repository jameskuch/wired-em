"""The fingerprint of what a full ``bootstrap.sh`` run sets up besides the package's own code.

    python3 deploy/install_stamp.py [APP_DIR]

Prints one sha256 over the files that decide what ``bootstrap.sh`` does apart from
reinstalling this package: the dependencies (``pyproject.toml`` without its
``version`` line, which every release changes and the package reinstall covers),
the systemd unit (``ReadWritePaths=`` and ``Environment=``, CLAUDE.md ground rule 10),
the nginx configs, and ``bootstrap.sh`` and this script themselves.

A full ``bootstrap.sh`` run writes it into the venv when the service is up again;
``bootstrap.sh --quick`` reinstalls only the package when the synced tree's stamp
is the one written there, and does the full run when it differs.  Standard library
only: it runs under the system ``python3`` on the server and on the Mac.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

FILES = (
    "pyproject.toml",
    "deploy/bootstrap.sh",
    "deploy/install_stamp.py",
    "deploy/sbfsem-web.service",
    "deploy/nginx-sbfsem.conf",
    "deploy/nginx-sbfsem-tls.conf",
)

_VERSION_LINE = re.compile(rb'^version\s*=\s*"[^"]*"[ \t]*\r?\n', re.M)


def stamp(root: Path) -> str:
    digest = hashlib.sha256()
    for name in FILES:
        data = (root / name).read_bytes()
        if name == "pyproject.toml":
            data = _VERSION_LINE.sub(b"", data, count=1)
        digest.update(name.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    print(stamp(root))
