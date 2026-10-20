from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CASSETTES = Path(__file__).parent / "cassettes"


@pytest.fixture(scope="session", autouse=True)
def _auth_off(tmp_path_factory):
    """Point the app at an empty auth dir so a developer's own
    ~/.sbfsem/auth/users.json can never lock the test suite out.

    Session-scoped deliberately: module-scoped fixtures (test_web.py's
    client) are instantiated before any function-scoped autouse fixture, so
    a function-scoped guard would come too late for them.  Tests that want
    auth ON override the variable with their own function-scoped monkeypatch.
    """
    patch = pytest.MonkeyPatch()
    patch.setenv("SBFSEM_AUTH_DIR", str(tmp_path_factory.mktemp("auth") / "none"))
    yield
    patch.undo()


@pytest.fixture(scope="session", autouse=True)
def _private_dirs(tmp_path_factory):
    """Give the run its own cache, saves, cell-render and library directories.

    Left unset, every app and service a test makes used the developer's own
    ``~/.cache/sbfsem`` (its ``meshes/`` is the dev server's render cache) and
    ``~/.sbfsem``: a run left built meshes there for the dev server to find, and
    under ``pytest -n`` the workers shared one cache and its pruning.  Under
    xdist ``tmp_path_factory`` is per worker, so each worker gets its own.  Tests
    that need a directory of their own still set the variable themselves.

    The cache starts as a copy of the recorded cassettes (7 MB): the bulk SWC
    export fetches c943 from OData even offline, and found it only because the
    developer's cache held it, so it failed on any other machine.
    """
    patch = pytest.MonkeyPatch()
    cache = tmp_path_factory.mktemp("cache")
    shutil.copytree(CASSETTES, cache, dirs_exist_ok=True)
    patch.setenv("SBFSEM_CACHE_DIR", str(cache))
    for name, sub in (("SBFSEM_SAVE_DIR", "saves"), ("SBFSEM_CELL_RENDER_DIR", "cell-renders"),
                      ("SBFSEM_LIBRARY_DIR", "library")):
        patch.setenv(name, str(tmp_path_factory.mktemp(sub)))
    yield
    patch.undo()


@pytest.fixture(scope="session")
def i943_path() -> Path:
    return FIXTURES / "i943.json"


@pytest.fixture(scope="session")
def c943(i943_path):
    from sbfsem.io.legacy_json import read_legacy_json

    return read_legacy_json(i943_path)


@pytest.fixture(scope="session")
def c1178():
    """The full c1178 (Inferior Monkey), from the offline cassette.

    The cell whose render connectivity keeps regressing: a 10.9 um soma
    spanning four segmentation rows, rescue-lofted entry stretches, and seven
    annotation components Viking never linked.
    """
    from sbfsem.io.cache import DiskCache
    from sbfsem.io.loader import NeuronLoader
    from sbfsem.io.odata import ODataClient

    cassettes = Path(__file__).parent / "cassettes"
    client = ODataClient("i", cache=DiskCache(cassettes), offline=True)
    try:
        return NeuronLoader("i", client=client).load(1178)
    finally:
        client.close()
