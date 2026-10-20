"""The finished mesh poll's binary form: the page's numbers are the JSON's, bit for bit.

The page asks ``GET /api/mesh-jobs/{id}`` for ``application/x-sbfsem-mesh`` and gets
int32 vertices on the JSON's 1e-4 µm step, uint32 faces and float64 ``vertex_data``
(:mod:`sbfsem_web.mesh_binary`).  Every other client, and every status without a
finished mesh, gets the JSON it always did, byte for byte.

``tests/fixtures/mesh_binary.json`` is the replies the node test
(``tests/js/test_meshbinary.mjs``) decodes with the page's own code; after a change to
the encoding, regenerate it with ``python tests/unit/test_mesh_binary.py --write``.
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.dto import MeshDTO
from sbfsem_web import mesh_binary
from sbfsem_web.api.routes import _encoded_mesh, _job_status_body, _job_status_response, _json_bytes
from sbfsem_web.app import create_app
from sbfsem_web.json_response import EncodedJSON

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "mesh_binary.json"
QUICK = {"method": "cylinder", "pathway": "original", "smooth_iterations": 0}
BINARY = {"Accept": f"{mesh_binary.MEDIA_TYPE}, application/json"}


@pytest.fixture
def web():
    app = create_app()
    with TestClient(app) as client:
        app.state.service.offline = True
        app.state.service._disk_meshes = None
        app.state.service.add_neuron(read_legacy_json(ROOT / "fixtures" / "i943.json"))
        yield client, app.state.service


def _wait(client, job_id, seconds=120.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = client.app.state.mesh_jobs.get(job_id)
        if job.status in ("done", "error", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def decode(content: bytes) -> dict:
    """The reply as docs/api.md lays it out, read without the module that wrote it."""
    length = int.from_bytes(content[:4], "little")
    head = json.loads(content[4:4 + length].decode("utf-8"))
    start = 4 + length
    assert start % 8 == 0, "the data starts on 8 bytes, so every array can be viewed in place"
    arrays = head.pop("arrays")

    def array(name, dtype):
        where = arrays[name]
        if where is None:
            return None
        assert (start + where[0]) % np.dtype(dtype).itemsize == 0, name
        return np.frombuffer(content, dtype=dtype, count=where[1], offset=start + where[0])

    steps = array("vertices", "<i4")
    vertices = steps / 1e4
    vertices[steps == -(2**31)] = -0.0
    mesh = head["mesh"]
    assert mesh["vertices"] is None and mesh["faces"] is None and mesh["vertex_data"] is None
    mesh["vertices"] = vertices
    mesh["faces"] = array("faces", "<u4")
    mesh["vertex_data"] = array("vertex_data", "<f8")
    return head


def assert_same_as_json(decoded: dict, parsed: dict) -> None:
    """``decoded`` (binary) holds exactly the numbers ``parsed`` (JSON) does: the
    same bits, the sign of a zero included."""
    mesh, want = decoded["mesh"], parsed["mesh"]
    assert {k: v for k, v in decoded.items() if k != "mesh"} == {k: v for k, v in parsed.items() if k != "mesh"}
    assert list(mesh) == list(want), "the mesh's keys, in the JSON's order"
    for key in want:
        if key in ("vertices", "faces", "vertex_data"):
            continue
        assert mesh[key] == want[key], key
    got = np.asarray(mesh["vertices"], dtype=np.float64)
    assert np.array_equal(got.view(np.uint64), np.asarray(want["vertices"], dtype=np.float64).view(np.uint64))
    assert mesh["faces"].tolist() == want["faces"]
    if want["vertex_data"] is None:
        assert mesh["vertex_data"] is None
    else:
        values = np.asarray(want["vertex_data"], dtype=np.float64)
        assert np.array_equal(np.asarray(mesh["vertex_data"]).view(np.uint64), values.view(np.uint64))


def _dto(vertices, faces, vertex_data=None, name="m") -> MeshDTO:
    vertices = [float(v) for v in vertices]
    return MeshDTO(name=name, n_vertices=len(vertices) // 3, n_faces=len(faces) // 3, vertices=vertices,
                   faces=[int(f) for f in faces], vertex_data=vertex_data,
                   bounding_box_um=[0.0, 1.0, 0.0, 1.0, 0.0, 1.0], surface_area_um2=1.25, volume_um3=0.5,
                   mesh_id="abc123", voxel2_um=0.05, voxel2_asked_um=0.0, voxel2_coarsened=False,
                   voxel2_reason=None)


def _status(mesh) -> dict:
    return {"job_id": "j", "status": "done", "waiting": False, "progress": None, "seconds": 1.5,
            "detail": "", "kind": "mesh", "failed_stage": "", "mesh": mesh, "result": None}


#: Coordinates on the JSON's 1e-4 step that are easy to get wrong: both zeros, the
#: smallest step either way, the ends of int32's range, and steps where
#: ``k * 1e-4`` is not the number ``k / 1e4`` is (3 * 1e-4 = 0.00030000000000000003).
TRICKY = [0.0, -0.0, 1e-4, -1e-4, 0.0003, 0.0007, 0.1, 0.3, 1234.5678, -987.6543,
          214748.3647, -214748.3647, 12.3457, 99999.9999, -0.0001, 7.0]


def _cases() -> dict[str, MeshDTO]:
    """The meshes the node test decodes with the page's code."""
    rng = np.random.default_rng(20261020)
    random = np.round(rng.uniform(-300, 1800, size=300 * 3), 4)
    vertices = TRICKY + [0.0, 0.0] + random.tolist()  # 18 + 900 values: 306 vertices
    faces = rng.integers(0, len(vertices) // 3, size=200 * 3).tolist()
    data = [round(float(x), 6) for x in rng.normal(scale=3.0, size=len(vertices) // 3)]
    data[:4] = [0.0, -0.0, 1e-300, -123456.789012]
    return {
        "tricky": _dto(vertices, faces, name="tricky"),
        "vertex_data": _dto(vertices, faces, vertex_data=data, name="vertex_data"),
        "empty": _dto([], [], name="empty"),
        "empty_vertex_data": _dto([], [], vertex_data=[], name="empty_vertex_data"),
    }


def _replies(dto: MeshDTO) -> tuple[bytes, bytes]:
    """(binary reply, JSON reply) of a finished job holding ``dto``."""
    held = _encoded_mesh(dto)
    assert isinstance(held, mesh_binary.EncodedMesh)
    return _job_status_response(_status(held), binary=True).body, _job_status_response(_status(held)).body


def _fixture() -> dict:
    out = {}
    for name, dto in _cases().items():
        binary, text = _replies(dto)
        out[name] = {"binary": base64.b64encode(binary).decode("ascii"), "json": text.decode("utf-8")}
    return out


# -- the encoding --------------------------------------------------------------------


@pytest.mark.parametrize("name", list(_cases()))
def test_the_binary_decodes_to_exactly_the_jsons_numbers(name):
    dto = _cases()[name]
    binary, text = _replies(dto)
    old = _job_status_response(_status(EncodedJSON(_json_bytes(dto)))).body
    assert text == old, "a JSON client gets the bytes it got before the binary existed"
    assert_same_as_json(decode(binary), json.loads(text))


def test_a_negative_zero_keeps_its_sign():
    binary, text = _replies(_dto([-0.0, 0.0, -0.0, 1.0, -0.0, 0.0], [0, 1, 1]))
    assert b"-0.0" in text
    signs = np.signbit(decode(binary)["mesh"]["vertices"]).tolist()
    assert signs == [True, False, True, False, True, False]


def test_the_binary_is_smaller_than_the_json_and_held_in_its_place():
    """The job holds the binary, not the JSON text: 7.2 MB against 12.7 MB for c1178's
    400,000 faces; here, with short face numbers, two thirds of it."""
    rng = np.random.default_rng(3)
    dto = _dto(np.round(rng.uniform(0, 900, size=3000 * 3), 4), rng.integers(0, 3000, size=6000 * 3))
    held = _encoded_mesh(dto)
    assert isinstance(held, mesh_binary.EncodedMesh)
    assert held.nbytes == 4 * 3000 * 3 + 4 * 6000 * 3
    assert held.nbytes < 0.7 * len(_json_bytes(dto))


@pytest.mark.parametrize("why, vertices, vertex_data", [
    ("past int32's range", [214748.3648, 0.0, 0.0], None),
    ("past int32's range, below", [0.0, -214748.3648, 0.0], None),
    ("a non-finite vertex", [0.0, float("nan"), 0.0], None),
    ("an infinite vertex", [float("inf"), 0.0, 0.0], None),
    ("off the 1e-4 step", [0.00001, 0.0, 0.0], None),
    ("a non-finite vertex_data", [0.0, 0.0, 0.0], [float("nan")]),
])
def test_a_mesh_the_binary_cannot_carry_exactly_stays_json(why, vertices, vertex_data):
    """The JSON says ``null`` for a non-finite number and carries any digits; the
    binary would say something else, so such a mesh is held as JSON and every client,
    the page included, gets JSON."""
    dto = MeshDTO.model_construct(
        name="odd", n_vertices=1, n_faces=1, vertices=vertices, faces=[0, 0, 0], vertex_data=vertex_data,
        bounding_box_um=[], surface_area_um2=0.0, volume_um3=0.0, mesh_id=None, voxel2_um=None,
        voxel2_asked_um=None, voxel2_coarsened=None, voxel2_reason=None)
    held = _encoded_mesh(dto)
    assert isinstance(held, EncodedJSON), why
    binary = _job_status_response(_status(held), binary=True)
    assert binary.media_type == "application/json"
    assert binary.body == _job_status_response(_status(held)).body


def test_the_accept_header_is_read_as_a_list_of_types():
    assert mesh_binary.accepts("application/x-sbfsem-mesh")
    assert mesh_binary.accepts("application/x-sbfsem-mesh, application/json")
    assert mesh_binary.accepts("application/json;q=0.9, Application/X-SBFSEM-Mesh;q=1")
    assert not mesh_binary.accepts(None)
    assert not mesh_binary.accepts("")
    assert not mesh_binary.accepts("*/*")
    assert not mesh_binary.accepts("application/json")
    assert not mesh_binary.accepts("application/x-sbfsem-meshes")


def test_the_node_tests_fixture_is_what_the_encoder_makes():
    """``tests/js/test_meshbinary.mjs`` decodes these replies with the page's code."""
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == _fixture(), (
        "the encoding changed: python tests/unit/test_mesh_binary.py --write")


# -- through the route -----------------------------------------------------------------


def test_a_finished_render_goes_to_the_page_as_binary_and_to_anyone_else_as_json(web):
    client, svc = web
    started = client.get("/api/neurons/i/943/mesh-job", params=QUICK).json()
    job = _wait(client, started["job_id"])
    assert job.status == "done" and isinstance(job.result, mesh_binary.EncodedMesh)
    plain = client.get(f"/api/mesh-jobs/{job.id}")
    assert plain.headers["content-type"] == "application/json"
    assert "accept" in plain.headers.get("vary", "").lower()
    # The bytes a poll sent before the binary: the model's JSON spliced into the status.
    before = _job_status_body(job)
    before["mesh"] = EncodedJSON(_json_bytes(svc.render_mesh(943, "i", **QUICK)))
    assert plain.content == _job_status_response(before).body
    head = plain.json()
    binary = client.get(f"/api/mesh-jobs/{job.id}", headers=BINARY)
    assert binary.status_code == 200
    assert binary.headers["content-type"] == mesh_binary.MEDIA_TYPE
    assert "accept" in binary.headers.get("vary", "").lower()
    assert len(binary.content) < 0.7 * len(plain.content)
    assert_same_as_json(decode(binary.content), head)


def test_a_vertex_data_mesh_and_an_empty_mesh_through_the_route(web):
    client, _ = web
    store = client.app.state.mesh_jobs
    for dto in (_cases()["vertex_data"], _cases()["empty"]):
        job = store.submit(("binary", dto.name), lambda dto=dto: _encoded_mesh(dto))
        _wait(client, job.id)
        plain = client.get(f"/api/mesh-jobs/{job.id}")
        binary = client.get(f"/api/mesh-jobs/{job.id}", headers=BINARY)
        assert binary.headers["content-type"] == mesh_binary.MEDIA_TYPE, dto.name
        assert_same_as_json(decode(binary.content), plain.json())


def test_a_running_failed_or_measurement_job_is_json_under_the_binary_accept(web):
    client, _ = web
    store = client.app.state.mesh_jobs
    gate = threading.Event()
    running = store.submit(("running",), lambda: gate.wait(10))
    for _ in range(200):
        if store.get(running.id).status == "running":
            break
        time.sleep(0.02)

    def fail():
        raise ValueError("boom")

    failed = store.submit(("failing",), fail)
    measured = store.submit(("measured",), lambda: {"mean_um": 0.5}, kind="deviation")
    _wait(client, failed.id)
    _wait(client, measured.id)
    try:
        for job in (running, failed, measured):
            plain = client.get(f"/api/mesh-jobs/{job.id}")
            binary = client.get(f"/api/mesh-jobs/{job.id}", headers=BINARY)
            assert binary.headers["content-type"] == "application/json"
            assert binary.content == plain.content
        assert client.get(f"/api/mesh-jobs/{failed.id}", headers=BINARY).json()["detail"] == "boom"
        missing = client.get("/api/mesh-jobs/nope", headers=BINARY)
        assert missing.status_code == 404 and "No such mesh job" in missing.json()["detail"]
    finally:
        gate.set()


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        FIXTURE.write_text(json.dumps(_fixture(), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {FIXTURE}")
    else:
        sys.exit("usage: python tests/unit/test_mesh_binary.py --write")
