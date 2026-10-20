"""The front end's node tests, run by the same gate as every other test.

``tests/js/*.mjs`` are plain, dependency-free node scripts (the repo has no JS
test runner) that exit non-zero on failure.  They pin the client side of the
Render compare incident of 2026-10-15 -- a pane measured with settings that did
not build its mesh, a failed measurement that deleted the surface -- and the
polling every render in the viewer goes through.  Until this file existed no
gate ran them: ``redeploy.sh`` runs pytest, ruff and mypy, and nothing else.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SUITES = sorted((Path(__file__).resolve().parents[1] / "js").glob("test_*.mjs"))


def test_the_node_suites_are_found():
    assert SUITES, "tests/js has no test_*.mjs: the front end's tests moved or were deleted"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("suite", SUITES, ids=lambda path: path.name)
def test_node_suite(suite):
    done = subprocess.run(["node", str(suite)], capture_output=True, text=True,
                          timeout=300, check=False)
    tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-25:])
    assert done.returncode == 0, f"{suite.name} failed:\n{tail}"
