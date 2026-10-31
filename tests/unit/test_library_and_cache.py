"""Shared libraries, cached renders, freshness, and the build gate."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime

import numpy as np
import pytest

from sbfsem.geometry.mesh import Mesh
from sbfsem.io.loader import newest_stamp
from sbfsem.service.meshcache import MeshDiskCache
from sbfsem_web.jobs import MeshJobStore
from sbfsem_web.library import validate_library_doc
from sbfsem_web.saves import LibraryStore, NameError_, QuotaError


@pytest.fixture(scope="module")
def client(request):
    from fastapi.testclient import TestClient

    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem_web.app import create_app

    app = create_app()
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as test_client:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield test_client


@pytest.fixture
def service(i943_path):
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    svc = SbfsemService(offline=True)
    svc.add_neuron(read_legacy_json(i943_path))
    yield svc
    svc.close()


# -- library store -----------------------------------------------------------


def test_library_store_roundtrip(tmp_path):
    store = LibraryStore(tmp_path / "materials")
    assert store.list() == []
    doc = store.set("Felt", {"kind": "material", "roughness": 1.0})
    assert doc["name"] == "Felt"
    assert store.get("Felt")["roughness"] == 1.0
    assert [d["name"] for d in store.list()] == ["Felt"]
    assert store.delete("Felt") is True
    assert store.delete("Felt") is False
    assert store.get("Felt") is None


def test_library_store_refuses_escapes_and_oversize(tmp_path):
    store = LibraryStore(tmp_path / "gradients")
    with pytest.raises(NameError_):
        store.set("../etc", {"kind": "gradient"})
    with pytest.raises(NameError_):
        store.get(".hidden")
    with pytest.raises(QuotaError):
        store.set("big", {"blob": "x" * 20000})


def test_validate_material_clamps_and_checks_colors():
    doc = validate_library_doc("materials", {
        "roughness": 7, "metalness": -1, "sheen": 0.5, "sheenColor": "#FFAA00",
        "flatShading": 1, "unlit": 0, "description": "fuzzy", "ignored": "x",
    })
    assert doc == {
        "kind": "material", "roughness": 1.0, "metalness": 0.0, "sheen": 0.5,
        "sheenColor": "#ffaa00", "flatShading": True, "unlit": False, "description": "fuzzy",
    }
    with pytest.raises(ValueError):
        validate_library_doc("materials", {"sheenColor": "red"})
    with pytest.raises(ValueError):
        validate_library_doc("materials", {"roughness": "soft"})


def test_material_fields_are_the_browsers_fields():
    """``MATERIAL_FIELDS`` and the colors are written twice (library.py and
    static/js/materials.js); a field in one and not the other is a setting that
    is saved and never shown, or shown and refused (the server once took an
    ``emissive`` color the browser never sends)."""
    import re
    from pathlib import Path

    from sbfsem_web import library

    source = (Path(library.__file__).parent / "static" / "js" / "materials.js").read_text(
        encoding="utf-8")

    def table(name):
        body = re.search(rf"export const {name} = \{{\n(.*?)\n\}};", source, re.S)
        assert body, f"{name} not found in materials.js"
        return body.group(1)

    number = r"(-?\d+(?:\.\d+)?)"
    fields = {
        key: (float(lo), float(hi), float(default))
        for key, lo, hi, default in re.findall(
            rf"^\s+(\w+): \[{number}, {number}, {number},", table("MATERIAL_FIELDS"), re.M)
    }
    assert fields, "no material fields parsed from materials.js"
    assert fields == library.MATERIAL_FIELDS
    colors = set(re.findall(r"^\s+(\w+): \['", table("MATERIAL_COLORS"), re.M))
    assert colors == library._MATERIAL_COLORS
    # The on/off switches completeSpec keeps (flat shading, unlit): one the server drops
    # is a box that saves to the library and comes back unticked.
    flags = set(re.findall(r"^\s+spec\.(\w+) = !!partial\?\.\1;", source, re.M))
    assert flags == library._MATERIAL_FLAGS


def test_validate_gradient_orders_and_pins_the_ends():
    doc = validate_library_doc("gradients", {
        "stops": [[0.9, "#ffffff"], [0.2, "#000000"], [0.5, "#FF0000"]],
    })
    assert doc["stops"] == [[0.0, "#000000"], [0.5, "#ff0000"], [1.0, "#ffffff"]]
    with pytest.raises(ValueError):
        validate_library_doc("gradients", {"stops": [[0, "#000000"]]})
    with pytest.raises(ValueError):
        validate_library_doc("gradients", {"stops": [[0, "#000000"], [1, "white"]]})
    with pytest.raises(ValueError):
        validate_library_doc("nothing", {})


def test_library_routes(client, tmp_path, monkeypatch):
    app = client.app
    monkeypatch.setattr(app.state, "libraries", {
        "materials": LibraryStore(tmp_path / "materials"),
        "gradients": LibraryStore(tmp_path / "gradients"),
    }, raising=False)
    assert client.get("/api/library/materials").json() == {"kind": "materials", "entries": []}
    put = client.put("/api/library/materials/Velvet 2", json={"sheen": 1, "roughness": 1})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["name"] == "Velvet 2" and body["sheen"] == 1.0 and "saved_at" in body
    assert [e["name"] for e in client.get("/api/library/materials").json()["entries"]] == ["Velvet 2"]
    assert client.put("/api/library/materials/x", json={"sheenColor": "nope"}).status_code == 422
    assert client.put("/api/library/materials/-dash first", json={}).status_code == 400
    assert client.get("/api/library/nothing").status_code == 404
    assert client.delete("/api/library/materials/Velvet 2").json() == {"removed": True}
    assert client.delete("/api/library/materials/Velvet 2").json() == {"removed": False}
    grad = client.put("/api/library/gradients/Strata",
                      json={"stops": [[0, "#0000ff"], [1, "#ff0000"]]})
    assert grad.status_code == 200 and grad.json()["stops"][0] == [0.0, "#0000ff"]


# -- freshness ------------------------------------------------------------------


def test_newest_stamp_picks_the_latest_location_edit():
    rows = [
        {"ID": 1, "LastModified": "2024-01-01T00:00:00Z"},
        {"ID": 2, "LastModified": "2025-06-01T12:00:00+00:00"},
        {"ID": 3},
    ]
    assert newest_stamp(rows) == datetime(2025, 6, 1, 12, tzinfo=UTC)
    assert newest_stamp([]) is None


def test_freshness_route_offline_is_502_not_a_crash(client):
    """An offline service cannot ask Viking; the route says so plainly."""
    r = client.get("/api/neurons/i/943/freshness")
    assert r.status_code == 502
    batch = client.post("/api/neurons/freshness",
                        json={"cells": [{"source": "i", "id": 943}, {"source": "i", "id": 1}]})
    assert batch.status_code == 200
    cells = batch.json()["cells"]
    assert [c["id"] for c in cells] == [943, 1]
    assert all(c["freshness"] is None for c in cells)


def test_load_response_carries_no_freshness_when_offline(client):
    """The fixture cell is imported, not fetched, so a load has nothing to check."""
    detail = client.get("/api/neurons/i/943").json()
    assert detail.get("freshness") is None
    assert "data_stamp" in detail["summary"]


def test_odata_fresh_bypasses_and_refills_the_cache(tmp_path):
    from sbfsem.io.cache import DiskCache
    from sbfsem.io.odata import ODataClient

    class FakeHttp:
        def __init__(self):
            self.calls = 0

        def get(self, url):
            self.calls += 1
            calls = self.calls

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"value": [{"n": calls}]}
            return R()

        def close(self):
            pass

    http = FakeHttp()
    client = ODataClient("i", cache=DiskCache(tmp_path), client=http)
    url = "http://example/Structures(1)/Locations?$top=1"
    assert client.get_collection(url)[0]["n"] == 1
    assert client.get_collection(url)[0]["n"] == 1  # cached
    with client.fresh():
        assert client.get_collection(url)[0]["n"] == 2  # bypassed
    assert client.get_collection(url)[0]["n"] == 2  # and the cache was refilled
    assert http.calls == 2


# -- disk mesh cache --------------------------------------------------------------


def _mesh(n=10):
    verts = np.random.default_rng(0).random((n, 3))
    faces = np.array([[0, 1, 2], [2, 3, 4]])
    return Mesh(vertices=verts, faces=faces, vertex_data=np.linspace(0, 1, n), meta={"a": 1})


def test_disk_mesh_cache_roundtrip_and_miss(tmp_path):
    cache = MeshDiskCache(tmp_path / "meshes")
    key = ("i", 1, "cylinder", 1, 1.0, 0.05, (("pathway", "voxel"),), "2025-01-01", 10)
    assert cache.get(key) is None
    cache.put(key, _mesh())
    back = cache.get(key)
    assert back is not None
    np.testing.assert_allclose(back.vertices, _mesh().vertices, atol=1e-6)
    assert back.faces.tolist() == [[0, 1, 2], [2, 3, 4]]
    assert back.vertex_data is not None and back.meta == {"a": 1}
    assert cache.get((*key[:-1], 11)) is None  # a different stamp is a different entry
    assert cache.stats()["files"] == 1


@pytest.mark.parametrize("damage", ["empty", "cut in half", "garbage", "cut near the end"])
def test_a_cut_off_cached_mesh_is_a_miss_and_is_replaced(tmp_path, damage):
    """An empty or truncated file raised EOFError or BadZipFile out of get(), which
    failed that render on every request instead of rebuilding it."""
    cache = MeshDiskCache(tmp_path / "meshes")
    key = ("i", 1, "cylinder", 1, 1.0, 0.05, (), "2025-01-01", 10)
    cache.put(key, _mesh(2000))
    path = cache.path(key)
    whole = path.read_bytes()
    path.write_bytes({"empty": b"", "cut in half": whole[: len(whole) // 2],
                      "garbage": b"not a zip at all" * 20,
                      "cut near the end": whole[:-40]}[damage])
    assert cache.get(key) is None
    assert not path.exists(), "the unreadable file is gone, so the next get is a clean miss"
    cache.put(key, _mesh(2000))
    assert cache.get(key) is not None


def test_a_damaged_header_or_a_flipped_byte_is_a_miss_that_is_removed_never_an_exception(tmp_path):
    """Truncation was covered, damage inside the file was not: a byte changed in the npy
    header made ``np.load`` raise ``tokenize.TokenError`` (and, by other values,
    ``SyntaxError``, ``IndexError`` or ``struct.error``) out of ``get()``, which failed that
    render on every request because the file was not removed either.  Every byte of the
    first 256, set to three values, is read: none may raise; one that cannot be read is
    gone, so the rebuilt mesh replaces it."""
    cache = MeshDiskCache(tmp_path / "meshes")
    key = ("i", 1, "cylinder", 1, 1.0, 0.05, (), "2025-01-01", 10)
    # Halves, so the bytes after the npy header hold NULs (a damaged header length reads them,
    # and the tokenizer raises), in a member bigger than the zip read-ahead (a small one is
    # CRC-checked before its header is parsed, and shows none of this).
    verts = np.arange(6000, dtype=float).reshape(2000, 3) / 2
    cache.put(key, Mesh(vertices=verts, faces=np.array([[0, 1, 2], [2, 3, 4]]),
                        vertex_data=np.linspace(0, 1, 2000), meta={"a": 1}))
    path = cache.path(key)
    whole = path.read_bytes()
    removed = loaded = 0
    for position in range(256):
        for value in (0xFF, 0x00, 0x7F):
            if whole[position] == value:
                continue
            path.write_bytes(whole[:position] + bytes([value]) + whole[position + 1:])
            try:
                mesh = cache.get(key)
            except Exception as exc:  # noqa: BLE001 - the point: none escapes
                pytest.fail(f"byte {position} set to {value:#x} raised {type(exc).__name__}: {exc}")
            if mesh is None:
                removed += 1
                assert not path.exists(), f"byte {position} = {value:#x}: unreadable and still there"
                path.write_bytes(whole)
            else:
                loaded += 1
    assert removed > 50, "the fuzz did not reach the header"
    assert loaded > 0, "a byte numpy does not read must not cost the entry"


def test_a_read_that_fails_for_a_reason_other_than_the_bytes_leaves_the_entry(tmp_path, monkeypatch):
    """EMFILE, an I/O error, a permission or network-filesystem fault say nothing about the
    file: a cached surface that can cost minutes to rebuild (MeshLab, closed-curve rasters)
    was deleted by any ``OSError``."""
    import errno

    cache = MeshDiskCache(tmp_path / "meshes")
    key = ("i", 1, "cylinder", 1, 1.0, 0.05, (), "2025-01-01", 10)
    cache.put(key, _mesh(300))
    path = cache.path(key)
    whole = path.read_bytes()
    real_load = np.load

    def flaky(*args, **kwargs):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(np, "load", flaky)
    assert cache.get(key) is None, "this try is a miss"
    assert path.read_bytes() == whole, "and the entry is still there"
    monkeypatch.setattr(np, "load", real_load)
    assert cache.get(key) is not None, "the next try finds it"

    def vanished(*args, **kwargs):
        raise FileNotFoundError(errno.ENOENT, "gone between the check and the read")

    monkeypatch.setattr(np, "load", vanished)
    assert cache.get(key) is None     # a miss, and nothing to remove


def test_a_failed_read_does_not_delete_the_rebuild_that_replaced_the_file(tmp_path, monkeypatch):
    """Two requests: one reads a damaged file and fails, another has meanwhile put the
    rebuild in its place.  Removing the path then removed the good one."""
    import zipfile

    cache = MeshDiskCache(tmp_path / "meshes")
    key = ("i", 1, "cylinder", 1, 1.0, 0.05, (), "2025-01-01", 10)
    cache.put(key, _mesh(300))
    path = cache.path(key)
    path.write_bytes(path.read_bytes()[:100])               # damaged
    real_load = np.load

    def damaged_while_another_request_rebuilds(*args, **kwargs):
        cache.put(key, _mesh(300))                          # the other request's replacement
        raise zipfile.BadZipFile("File is not a zip file")

    monkeypatch.setattr(np, "load", damaged_while_another_request_rebuilds)
    assert cache.get(key) is None
    monkeypatch.setattr(np, "load", real_load)
    assert path.exists(), "the rebuilt entry was removed with the damaged one"
    assert cache.get(key) is not None


def test_a_dead_writers_temporary_file_is_swept_and_a_failed_write_leaves_none(tmp_path, monkeypatch):
    cache = MeshDiskCache(tmp_path / "meshes")
    cache.put(("k", 1), _mesh())
    stale = cache.path(("k", 1)).with_suffix(".npz.4242.1.tmp")
    fresh = cache.path(("k", 1)).with_suffix(".npz.4243.2.tmp")
    stale.write_bytes(b"x" * 100)
    fresh.write_bytes(b"x" * 100)
    old = time.time() - MeshDiskCache.TEMPORARY_MAX_AGE_S - 60
    os.utime(stale, (old, old))
    cache.prune()
    assert not stale.exists() and fresh.exists(), "only a temporary file no live write can own"

    def fail(fh, **arrays):
        fh.write(b"half")
        raise OSError("No space left on device")

    monkeypatch.setattr(np, "savez", fail)
    cache.put(("k", 2), _mesh())  # a full disk must not fail the render
    assert cache.get(("k", 2)) is None
    assert sorted(p.name for p in (tmp_path / "meshes").rglob("*.tmp")) == [fresh.name]


def test_disk_mesh_cache_prunes_least_recently_used(tmp_path):
    cache = MeshDiskCache(tmp_path / "meshes", budget_mb=0.002)  # ~2 kB
    for i in range(6):
        cache.put(("k", i), _mesh(30))
        time.sleep(0.01)
    cache.prune()
    assert cache.stats()["bytes"] <= cache.budget_bytes
    assert cache.get(("k", 5)) is not None  # the newest survives


def test_service_serves_a_rebuild_from_disk(tmp_path, i943_path):
    """A second service process finds the first one's build on disk."""
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    first = SbfsemService(offline=True, cache_dir=tmp_path)
    first.add_neuron(read_legacy_json(i943_path))
    dto = first.render_mesh(943, "i", method="cylinder")
    assert dto.n_faces > 0
    assert first._disk_meshes.stats()["files"] == 1
    first.close()

    second = SbfsemService(offline=True, cache_dir=tmp_path)
    second.add_neuron(read_legacy_json(i943_path))
    second._build_mesh = lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt"))
    again = second.render_mesh(943, "i", method="cylinder")
    assert again.n_faces == dto.n_faces
    second.close()


def test_estimate_peak_mb_scales_with_the_cell(service):
    small = service.estimate_peak_mb(943, "i", method="cylinder", pathway="original")
    voxel = service.estimate_peak_mb(943, "i", method="cylinder", pathway="voxel")
    curve = service.estimate_peak_mb(943, "i", method="closedcurve", pixel_size_um=0.25)
    assert 100 < small < voxel
    assert curve > 100


def test_the_worst_closed_curve_build_is_estimated_within_the_memory_gate(service, monkeypatch):
    """A raster may have 10 million exposed voxel faces (``MAX_SURFACE``), which is up
    to 19 million triangles and 6.8 GB of build (measured: 120 MB plus 350 bytes a
    triangle; RPC1 c2628 at 0.025 um, 10.1 million triangles, 3.4 GB).  The estimate
    the build gate reads is capped at that, follows the limit when it moves, and the
    production unit's budget can hold it, so such a build runs alone and does not wait
    for ever behind the budget it can never fit."""
    import re
    from pathlib import Path

    import sbfsem.service.facade as facade
    from sbfsem.geometry.closed_curve import MAX_SURFACE

    assert pytest.approx(120.0 + 350e-6 * 1.9 * MAX_SURFACE) == facade.CURVE_MAX_BUILD_MB
    unit = (Path(__file__).resolve().parents[2] / "deploy" / "sbfsem-web.service").read_text()
    budget = float(re.search(r"SBFSEM_MESH_MEMORY_MB=(\d+)", unit).group(1))
    assert budget > 150.0 + facade.CURVE_MAX_BUILD_MB

    # A raster whose tiles say 250 million voxels would be 12.5 GB at the per-voxel
    # figure: the surface limit is what bounds the mesh, and the estimate stops there.
    monkeypatch.setattr(facade, "curve_grid_voxels", lambda *a, **k: 250_000_000)
    capped = service.estimate_peak_mb(943, "i", method="closedcurve", pixel_size_um=0.05)
    assert capped == round(150.0 + facade.CURVE_MAX_BUILD_MB)
    # Below it the estimate is still the tiles' own.
    monkeypatch.setattr(facade, "curve_grid_voxels", lambda *a, **k: 40_000_000)
    assert service.estimate_peak_mb(943, "i", method="closedcurve", pixel_size_um=0.05) == round(
        150.0 + 40_000_000 * facade.CURVE_BYTES_PER_VOXEL / 1e6)


def test_a_voxel_remesh_build_over_the_old_ceiling_is_priced_by_its_raw_sheet(service):
    """Up to 3 million raw triangles (a Face cap of 1,000,000) the estimate is the per-annotation
    one it was measured as; past that every triangle of the budget adds what it was measured to
    cost (Inferior c1178: 310 MB plus 445 bytes a triangle with every triangle kept, 2.1 to 7.1
    million of them).  Under such a cap the sheet is decimated a chunk at a time (2026-10-20), which
    adds a chunk's weight, not a second copy of the sheet (356 to 384 bytes a triangle before).  A
    build with no cap asks for 10 million and adds 3.5 GB; one capped at 3.4 million or more
    marches the same 10 million and adds 4 GB, the heaviest request the page can make (7 GB
    before), and both fit the production unit's budget beside the weight of the largest cell
    measured."""
    import re
    from pathlib import Path

    import sbfsem.service.facade as facade
    from sbfsem.geometry import unify

    default = service.estimate_peak_mb(943, "i", method="cylinder", pathway="voxel2")
    assert service.estimate_peak_mb(
        943, "i", method="cylinder", pathway="voxel2", voxel2_faces=1_000_000) == default
    over = unify.MAX_RAW_FACES - unify.RAW_FACES_KEYED_ABOVE
    kept_all = over * facade.FIELD_BYTES_PER_RAW_FACE / 1e6
    worst = kept_all + facade.FIELD_CHUNKED_DECIMATION_MB
    assert kept_all == 3500 and worst == 4000
    for pathway in ("voxel2", "convolution", "ultraliser"):
        def weigh(faces, pathway=pathway):
            return service.estimate_peak_mb(
                943, "i", method="cylinder", pathway=pathway, voxel2_faces=faces)
        base = service.estimate_peak_mb(943, "i", method="cylinder", pathway=pathway)
        assert abs(weigh(0) - base - kept_all) <= 1
        assert abs(weigh(3_400_000) - base - worst) <= 1
        assert abs(weigh(5_000_000) - weigh(3_400_000)) <= 1
        assert base < weigh(2_000_000) < weigh(0) < weigh(3_400_000)
    # The other pathways have no such sheet.
    original = service.estimate_peak_mb(943, "i", method="cylinder", pathway="original")
    assert service.estimate_peak_mb(
        943, "i", method="cylinder", pathway="original", voxel2_faces=0) == original

    unit = (Path(__file__).resolve().parents[2] / "deploy" / "sbfsem-web.service").read_text()
    budget = float(re.search(r"SBFSEM_MESH_MEMORY_MB=(\d+)", unit).group(1))
    c1178 = 120.0 + 2304 * 0.3 + 300.0       # its per-annotation part
    assert c1178 + worst < budget


def test_a_raw_budget_over_the_old_ceiling_is_named_in_the_surface_key(service):
    """The app's Face cap and a cap of a million (a raw budget of 3 million, the old ceiling) keep
    the key they always had.  A cap of 2 million, or none, asks for more than any release before
    2.5.1 allowed, so its surface is stored under a key that says so; otherwise the surface built
    under the old ceiling for the same options would answer it."""
    import sbfsem.service.facade as facade

    class Disk:
        def __init__(self):
            self.keys = []

        def get(self, key):
            return None

        def put(self, key, mesh):
            self.keys.append(key)

    service._disk_meshes = Disk()
    stored = {}
    for faces in (400_000, 1_000_000, 2_000_000, 0):
        service.mesh_object(943, "i", method="cylinder", pathway="voxel2", voxel2_um=0.3,
                            voxel2_faces=faces)
        stored[faces] = service._disk_meshes.keys[-1]
    assert stored[400_000][-1] == facade.GEOMETRY_VERSION
    assert stored[1_000_000][-1] == facade.GEOMETRY_VERSION
    assert stored[2_000_000][-1] == "raw-budget:6000000:chunks1"
    assert stored[0][-1] == "raw-budget:10000000"
    assert len(set(stored.values())) == 4

    token = facade._budget_token
    assert token("closedcurve", {"pathway": "voxel2", "voxel2_faces": 0}) is None
    assert token("cylinder", {"pathway": "original", "voxel2_faces": 0}) is None
    assert token("hybrid", {"pathway": "voxel2", "voxel2_faces": 0}) == "raw-budget:10000000"
    assert token("curvemesh", {"pathway": "ultraliser", "voxel2_faces": 0}) == "raw-budget:10000000"
    assert token("cylinder", {"pathway": "convolution", "voxel2_faces": 5_000_000}) == "raw-budget:10000000:chunks1"
    # A cap of a million or less keeps the one-pass decimation and its key.
    assert token("cylinder", {"pathway": "voxel2", "voxel2_faces": 1_000_000}) is None


# -- the build gate ---------------------------------------------------------------


def test_jobs_run_in_parallel_under_the_budget():
    store = MeshJobStore(workers=3, memory_mb=1000)
    started = []
    lock = threading.Lock()
    release = threading.Event()

    def work(name):
        def fn():
            with lock:
                started.append(name)
            release.wait(2)
            return name
        return fn

    jobs = [store.submit(("a", i), work(i), weight_mb=300) for i in range(3)]
    time.sleep(0.3)
    assert sorted(started) == [0, 1, 2]  # 3 x 300 fits under 1000
    release.set()
    for job in jobs:
        for _ in range(50):
            if job.status == "done":
                break
            time.sleep(0.02)
        assert job.status == "done"
    assert store.load()["running"] == 0
    store.close()


def test_a_heavy_job_waits_for_the_room_and_then_runs_alone():
    store = MeshJobStore(workers=2, memory_mb=1000)
    release = threading.Event()
    order = []

    def work(name, wait=True):
        def fn():
            order.append(("start", name))
            if wait:
                release.wait(2)
            order.append(("end", name))
            return name
        return fn

    small = store.submit(("s",), work("small"), weight_mb=400)
    heavy = store.submit(("h",), work("heavy", wait=False), weight_mb=5000)
    time.sleep(0.3)
    assert heavy.status == "queued"  # 400 + 5000 does not fit while small runs
    assert heavy.waiting == "memory"  # it holds a thread, blocked at the gate
    assert store.load()["gate_blocked"] == 1
    release.set()
    for _ in range(100):
        if heavy.status == "done":
            break
        time.sleep(0.02)
    assert heavy.status == "done" and small.status == "done"
    assert order.index(("end", "small")) < order.index(("start", "heavy"))
    assert heavy.waiting == "" and heavy.gate_seconds > 0.1
    load = store.load()
    assert load["gate_blocked"] == 0 and load["gate_waits"] == 1
    assert load["gate_wait_max_s"] >= 0.1
    store.close()


def test_a_job_waiting_for_a_thread_is_not_counted_at_the_gate():
    store = MeshJobStore(workers=1, memory_mb=1000)
    release = threading.Event()
    first = store.submit(("1",), lambda: release.wait(2), weight_mb=100)
    second = store.submit(("2",), lambda: "ran", weight_mb=100)
    time.sleep(0.1)
    assert second.status == "queued" and second.waiting == "worker"
    assert store.load()["gate_blocked"] == 0
    release.set()
    for _ in range(50):
        if second.status == "done":
            break
        time.sleep(0.02)
    assert first.status == "done" and second.status == "done"
    assert store.load()["gate_waits"] == 0
    store.close()


def test_cancelled_job_never_starts_and_frees_the_gate():
    store = MeshJobStore(workers=1, memory_mb=100)
    release = threading.Event()
    first = store.submit(("1",), lambda: release.wait(2), weight_mb=100)
    second = store.submit(("2",), lambda: "ran", weight_mb=100)
    time.sleep(0.1)
    assert store.cancel(second.id) is True
    release.set()
    for _ in range(50):
        if first.status == "done":
            break
        time.sleep(0.02)
    assert second.status == "cancelled" and second.result is None
    store.close()


def test_a_joined_job_is_cancelled_only_when_nobody_waits():
    """Two requests for one surface share a job: one letting go must not stop
    the other's build."""
    store = MeshJobStore(workers=1, memory_mb=100)
    release = threading.Event()
    store.submit(("busy",), lambda: release.wait(2), weight_mb=100)
    mine = store.submit(("c5922",), lambda: "built", weight_mb=100)
    theirs = store.submit(("c5922",), lambda: "built twice", weight_mb=100)
    assert theirs is mine and mine.waiters == 2
    time.sleep(0.1)
    assert store.cancel(mine.id) is False and mine.status == "queued"
    release.set()
    for _ in range(100):
        if mine.status == "done":
            break
        time.sleep(0.02)
    assert mine.status == "done" and mine.result == "built"
    # The last one to let go does stop a build still queued.
    release.clear()
    store.submit(("busy2",), lambda: release.wait(2), weight_mb=100)
    lone = store.submit(("c1",), lambda: "ran", weight_mb=100)
    time.sleep(0.1)
    assert store.cancel(lone.id) is True and lone.status == "cancelled"
    release.set()
    store.close()


def test_a_job_is_given_its_result_before_it_is_called_done(monkeypatch):
    """A poll takes no lock: one that saw "done" while the result was still unset
    answered a finished render with null; "error" without its reason, likewise."""
    from sbfsem_web.jobs import MeshJob

    order = []
    real = MeshJob.__setattr__

    def spy(self, name, value):
        if name in ("status", "result", "detail", "failed_stage"):
            order.append((name, value))
        real(self, name, value)

    monkeypatch.setattr(MeshJob, "__setattr__", spy)
    store = MeshJobStore(workers=1, memory_mb=100)

    def fail():
        raise ValueError("boom")

    ok = store.submit(("ok",), lambda: "built", weight_mb=1)
    bad = store.submit(("bad",), fail, weight_mb=1)
    for _ in range(100):
        if ok.status == "done" and bad.status == "error":
            break
        time.sleep(0.02)
    store.close()
    assert order.index(("result", "built")) < order.index(("status", "done"))
    assert order.index(("detail", "boom")) < order.index(("status", "error"))
    assert order.index(("failed_stage", "")) < order.index(("status", "error"))


def test_a_finished_job_is_let_go_after_its_time_without_another_job_being_asked_for(monkeypatch):
    """It was swept only when the next job was submitted, so a finished render
    (hundreds of MB of mesh) stayed for as long as nobody asked for another."""
    ttl = 1.0  # long against a busy machine's hiccups
    monkeypatch.setattr("sbfsem_web.jobs.DONE_TTL_SECONDS", ttl)

    def finished(store, key):
        job = store.submit((key,), lambda: "built", weight_mb=1)
        for _ in range(100):
            if job.status == "done":
                break
            time.sleep(0.02)
        assert job.status == "done"
        return job

    asked = MeshJobStore(workers=1, memory_mb=100)
    job = finished(asked, "one")
    assert asked.get(job.id) is job  # within its time
    time.sleep(ttl + 0.2)
    assert asked.get(job.id) is None  # asking for a job sweeps
    assert asked.submit(("one",), lambda: "built again", weight_mb=1) is not job
    asked.close()

    reported = MeshJobStore(workers=1, memory_mb=100)
    job = finished(reported, "two")
    time.sleep(ttl + 0.2)
    assert job.id in reported._jobs
    reported.load()  # and so does the health report
    assert reported._jobs == {}
    reported.close()


# -- child-process builds -----------------------------------------------------------


def test_process_pool_builds_the_same_mesh(i943_path):
    """The geometry step in a spawned worker returns what the thread would."""
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    inline = SbfsemService(offline=True)
    inline.add_neuron(read_legacy_json(i943_path))
    expected = inline.render_mesh(943, "i", method="cylinder", smooth_iterations=0)
    inline.close()

    pooled = SbfsemService(offline=True, build_processes=1)
    pooled.add_neuron(read_legacy_json(i943_path))
    try:
        got = pooled.render_mesh(943, "i", method="cylinder", smooth_iterations=0)
    finally:
        pooled.close()
    assert got.n_faces == expected.n_faces and got.n_vertices == expected.n_vertices
    np.testing.assert_allclose(got.vertices[:30], expected.vertices[:30], atol=1e-6)


def test_a_killed_build_process_does_not_break_every_build_after_it(i943_path):
    """The kernel's out-of-memory killer takes a build worker: the pool is broken for
    good, and every build after it raised BrokenProcessPool until a restart."""
    import os
    import signal
    from concurrent.futures import Future
    from concurrent.futures.process import BrokenProcessPool

    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    svc = SbfsemService(offline=True, build_processes=1)
    svc.add_neuron(read_legacy_json(i943_path))
    quick = {"method": "cylinder", "smooth_iterations": 0}
    try:
        first = svc.render_mesh(943, "i", **quick)
        pool = svc._process_pool
        assert pool is not None and first.n_faces > 0

        # 1. A worker dies while idle (or under an earlier build): the next build
        #    never started in it, so it runs in a new pool without a word.
        for pid in list(pool._processes):
            os.kill(pid, signal.SIGKILL)
        for _ in range(100):
            if getattr(pool, "_broken", False):
                break
            time.sleep(0.1)
        again = svc.render_mesh(943, "i", pathway="adaptive_taubin", **quick)
        assert again.n_faces > 0
        assert svc._process_pool is not pool and svc._process_pool is not None

        # 2. A worker dies under the build itself: that build says so, the pool is
        #    dropped, and the next build works.
        class DyingPool:
            def submit(self, *args):
                future: Future = Future()
                future.set_exception(BrokenProcessPool("a process in the process pool was terminated"))
                return future

            def shutdown(self, **kwargs):
                pass

        doomed = DyingPool()
        live = svc._process_pool
        svc._process_pool = doomed  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="build process stopped.*ran out of memory"):
            svc.render_mesh(943, "i", pathway="voxel", **quick)
        assert svc._process_pool is None
        live.shutdown(wait=False, cancel_futures=True)
        recovered = svc.render_mesh(943, "i", pathway="wrap", **quick)
        assert recovered.n_faces > 0 and svc._process_pool is not None
    finally:
        svc.close()


def test_a_build_process_left_holding_too_much_is_replaced(i943_path):
    """After heavy builds one worker held 2.3 GB for as long as the service ran, and
    the service idled at 4.7 GB instead of 1.5 GB (2026-10-16).  A worker that still
    holds more than ``build_recycle_mb`` after a build goes with its pool; the next
    build runs in a new one, started at once; the old one winds down on its own."""
    from sbfsem import progress
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    quick = {"method": "cylinder", "smooth_iterations": 0}
    # Any worker holds more than 1 MB: every build replaces its pool.
    svc = SbfsemService(offline=True, build_processes=1, build_recycle_mb=1)
    svc.add_neuron(read_legacy_json(i943_path))
    try:
        svc.render_mesh(943, "i", **quick)
        second = svc._process_pool
        queue = svc._progress_queue
        assert svc.pool_recycles == 1 and second is not None and queue is not None
        # The new pool's first worker was started by the replacement, not by a build.
        (pid,) = list(second._processes)
        events = []
        with progress.reporting(lambda stage, done, total: events.append(stage)):
            again = svc.render_mesh(943, "i", pathway="adaptive_taubin", **quick)
        assert again.n_faces > 0 and events, "the new pool builds and reports progress"
        assert svc.pool_recycles == 2 and svc._process_pool not in (None, second)
        assert svc._progress_queue not in (None, queue), "each pool reports on a queue of its own"
        for _ in range(300):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"the replaced pool's worker {pid} is still running")
    finally:
        svc.close()

    # 0: a worker is never replaced for what it holds.
    kept = SbfsemService(offline=True, build_processes=1, build_recycle_mb=0)
    kept.add_neuron(read_legacy_json(i943_path))
    try:
        kept.render_mesh(943, "i", **quick)
        pool = kept._process_pool
        assert pool is not None
        pids = sorted(pool._processes)
        kept.render_mesh(943, "i", pathway="adaptive_taubin", **quick)
        assert kept._process_pool is pool and sorted(pool._processes) == pids
        assert kept.pool_recycles == 0 and all(os.getpid() != p for p in pids)
    finally:
        kept.close()


def test_a_build_handed_to_a_pool_just_retired_goes_to_the_next(i943_path):
    """Two builds at once: one takes the pool, and before it hands its work over the
    other retires that pool for its size.  The submit raised "cannot schedule new
    futures after shutdown" and the first build failed with it; it goes to the new
    pool instead.  And a build that fails in its worker, leaving the worker big,
    retires the pool as a finished one does."""
    from concurrent.futures import Future

    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    class ShutPool:
        def __init__(self):
            self.shutdowns = 0

        def submit(self, *args):
            raise RuntimeError("cannot schedule new futures after shutdown")

        def shutdown(self, **kwargs):
            self.shutdowns += 1

    svc = SbfsemService(offline=True, build_processes=1, build_recycle_mb=1024)
    svc.add_neuron(read_legacy_json(i943_path))
    try:
        shut, real_pool, taken = ShutPool(), svc._pool, []

        def pool_once():
            taken.append(1)
            return shut if len(taken) == 1 else real_pool()

        svc._pool = pool_once  # type: ignore[method-assign]
        mesh = svc.render_mesh(943, "i", method="cylinder", smooth_iterations=0)
        assert mesh.n_faces > 0 and len(taken) == 2
        svc._pool = real_pool  # type: ignore[method-assign]

        class FailingPool(ShutPool):
            def submit(self, *args):
                future: Future = Future()
                error = ValueError("the build failed")
                error.held_mb = 3000.0  # type: ignore[attr-defined]
                future.set_exception(error)
                return future

        live, failing = svc._process_pool, FailingPool()
        svc._process_pool = failing  # type: ignore[assignment]
        with pytest.raises(ValueError, match="the build failed"):
            svc.render_mesh(943, "i", method="cylinder", pathway="voxel", smooth_iterations=0)
        assert svc.pool_recycles == 1 and svc._process_pool not in (None, failing)
        for _ in range(50):
            if failing.shutdowns:
                break
            time.sleep(0.1)
        assert failing.shutdowns == 1, "the retired pool is wound down"
        assert live is not None
        live.shutdown(wait=False, cancel_futures=True)
    finally:
        svc.close()


def test_a_pool_that_has_already_been_replaced_is_not_dropped_again():
    """Several builds that fail together all drop the pool they saw.  The first drop
    makes room for a new pool; a second, late drop of the old one must not take the new
    pool (and its progress queue) down with it."""
    from sbfsem.service.facade import SbfsemService

    class FakePool:
        def __init__(self):
            self.shutdowns = 0

        def shutdown(self, **kwargs):
            self.shutdowns += 1

    class FakeQueue:
        def __init__(self):
            self.put_calls = []

        def put(self, item):
            self.put_calls.append(item)

    svc = SbfsemService(offline=True, build_processes=1)
    try:
        old, old_queue = FakePool(), FakeQueue()
        svc._process_pool, svc._progress_queue = old, old_queue      # type: ignore[assignment]
        svc._drop_pool(old)                                          # type: ignore[arg-type]
        assert svc._process_pool is None and old.shutdowns == 1 and old_queue.put_calls == [None]
        # A second build, which failed with the first, makes its own pool...
        new, new_queue = FakePool(), FakeQueue()
        svc._process_pool, svc._progress_queue = new, new_queue      # type: ignore[assignment]
        # ... and the first one's late report that the OLD pool is broken is not about this one.
        svc._drop_pool(old)                                          # type: ignore[arg-type]
        assert svc._process_pool is new and svc._progress_queue is new_queue
        assert new.shutdowns == 0 and new_queue.put_calls == []
        assert old.shutdowns == 1 and old_queue.put_calls == [None], "and the old one is not shut down twice"
        # The pool that is current is dropped, once.
        svc._drop_pool(new)                                          # type: ignore[arg-type]
        svc._drop_pool(new)                                          # type: ignore[arg-type]
        assert svc._process_pool is None and svc._progress_queue is None
        assert new.shutdowns == 1 and new_queue.put_calls == [None]
    finally:
        svc._process_pool = None
        svc.close()


def test_outline_estimate_counts_the_raster_the_build_allocates(request, monkeypatch):
    """The outline half is sized from the outlines' own grid, not the whole cell.

    Sizing it from the cell's x/y/z extent put an RC2 Hybrid with 8 outlines
    (measured peak 933 MB) at 218,190 MB on 2026-10-07, which made the gate
    run it alone while another cell waited.
    """
    from sbfsem.geometry.closed_curve import (
        annotations_from_geometries,
        build_curve_volume,
        curve_grid_voxels,
    )
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import CURVE_BYTES_PER_VOXEL, SbfsemService, _location_links

    neuron = read_legacy_json(request.path.parents[1] / "fixtures" / "i90000_closedcurve.json")
    service = SbfsemService(offline=True)
    service.add_neuron(neuron)
    neuron = service.neuron(90000, "i")
    annotations = annotations_from_geometries(neuron.geometries)
    links = _location_links(neuron)
    for pixel in (0.25, 0.1):
        voxels = curve_grid_voxels(annotations, volume_scale=neuron.volume_scale,
                                   pixel_size_um=pixel, links=links)
        built = build_curve_volume(annotations, volume_scale=neuron.volume_scale,
                                   pixel_size_um=pixel, links=links)
        assert voxels == built.allocated_voxels  # the estimate's raster is the build's
        estimate = service.estimate_peak_mb(90000, "i", method="closedcurve",
                                            pixel_size_um=pixel)
        assert estimate == round(150.0 + voxels * CURVE_BYTES_PER_VOXEL / 1e6)
    # A raster over the limit is refused before anything is allocated.
    import sbfsem.service.facade as facade

    monkeypatch.setattr(facade, "CURVE_MAX_VOXELS", 1_000)
    refused = service.estimate_peak_mb(90000, "i", method="closedcurve", pixel_size_um=0.25)
    assert refused == 150.0
    service.close()


def _wait_for(store, job, timeout):
    """`store.wait(job, timeout)` from a thread with no loop of its own: (ended, seconds)."""
    import asyncio

    t0 = time.perf_counter()
    ended = asyncio.run(store.wait(job, timeout))
    return ended, time.perf_counter() - t0


def test_a_waiting_poll_is_woken_as_the_job_ends():
    """A poll that waits answers the moment the build is done, failed or cancelled, not at
    its timeout, and lets go of the job when it answers."""
    store = MeshJobStore(workers=1, memory_mb=100)
    release = threading.Event()
    job = store.submit(("built",), lambda: release.wait(5) and "built", weight_mb=1)
    threading.Timer(0.2, release.set).start()
    ended, seconds = _wait_for(store, job, 5.0)
    assert ended and job.status == "done" and job.result == "built"
    assert 0.15 < seconds < 1.0
    assert job.watchers == []

    def fail():
        time.sleep(0.2)
        raise ValueError("boom")

    bad = store.submit(("bad",), fail, weight_mb=1)
    ended, seconds = _wait_for(store, bad, 5.0)
    assert ended and bad.status == "error" and seconds < 1.0

    # Cancelled while queued behind a running build: the wait ends with it.
    hold = threading.Event()
    store.submit(("busy",), lambda: hold.wait(5), weight_mb=100)
    queued = store.submit(("queued",), lambda: "ran", weight_mb=100)
    threading.Timer(0.2, lambda: store.cancel(queued.id)).start()
    ended, seconds = _wait_for(store, queued, 5.0)
    assert ended and queued.status == "cancelled" and seconds < 1.0
    hold.set()
    store.close()


def test_a_waiting_poll_answers_at_its_timeout_and_at_once_for_an_ended_job():
    store = MeshJobStore(workers=1, memory_mb=100)
    release = threading.Event()
    job = store.submit(("slow",), lambda: release.wait(5), weight_mb=1)
    ended, seconds = _wait_for(store, job, 0.3)
    assert not ended and job.status == "running"
    assert 0.25 < seconds < 1.0
    assert job.watchers == []  # a poll that timed out leaves nothing behind
    release.set()
    for _ in range(100):
        if job.status == "done":
            break
        time.sleep(0.02)
    ended, seconds = _wait_for(store, job, 5.0)
    assert ended and seconds < 0.1
    store.close()
