"""The finished mesh poll's binary form: the page's numbers are the JSON's, bit for bit.

The page asks ``GET /api/mesh-jobs/{id}`` for ``application/x-sbfsem-mesh`` and gets
int32 vertices on the JSON's 1e-4 µm step, uint32 faces and float64 ``vertex_data``
(:mod:`sbfsem_web.mesh_binary`).  Every other client, and every status without a
finished mesh, gets the JSON it always did, byte for byte.

A render is encoded from its arrays (``SbfsemService.render_buffers``), never through
the DTO's lists; ``_old_render_mesh`` below is the list path it replaced, kept to hold
the arrays' replies to its bytes.

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

from sbfsem.geometry.mesh import Mesh
from sbfsem.geometry.meshops import clip_mesh, clip_mesh_by_stratification
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.dto import MeshDTO
from sbfsem.service.facade import MeshBuffers, SbfsemService, round_as_python
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


# -- a render, from its arrays -----------------------------------------------------------


def _old_mesh_dto(name: str, mesh: Mesh) -> MeshDTO:
    """``SbfsemService._mesh_dto`` as it was before 2.19.6: a Python number per coordinate."""
    vertices = np.round(mesh.vertices, 4)
    meta = mesh.meta or {}
    voxel = meta.get("voxel2_um")
    coarsened = meta.get("voxel2_coarsened")
    return MeshDTO(
        name=name, n_vertices=mesh.n_vertices, n_faces=len(mesh.faces),
        vertices=[float(v) for v in vertices.ravel()], faces=[int(v) for v in mesh.faces.ravel()],
        vertex_data=([round(float(v), 6) for v in mesh.vertex_data] if mesh.vertex_data is not None else None),
        bounding_box_um=[float(v) for v in mesh.bounding_box], surface_area_um2=mesh.surface_area(),
        volume_um3=mesh.volume(), mesh_id=mesh.meta.get("mesh_id"),
        voxel2_um=float(voxel) if voxel is not None else None,
        voxel2_asked_um=(float(meta["voxel2_asked"]) if meta.get("voxel2_asked") is not None else None),
        voxel2_coarsened=bool(coarsened) if coarsened is not None else None,
        voxel2_reason=(str(meta["voxel2_reason"]) or None) if "voxel2_reason" in meta else None)


def _old_render_mesh(svc, neuron_id, source, *, clip_z=None, clip_ipl=None, keep_above=True,
                     color_by_strata=False, **options) -> MeshDTO:
    """``SbfsemService.render_mesh`` as it was before 2.19.6: the cached mesh copied, cut
    and colored in place, then made into the DTO's lists."""
    neuron = svc.neuron(neuron_id, source)
    mesh = svc.mesh_for(neuron, options).copy()
    if clip_z is not None:
        mesh = clip_mesh(mesh, clip_z, keep_above=keep_above)
    if clip_ipl is not None:
        mesh = clip_mesh_by_stratification(mesh, svc.boundaries(neuron.source), clip_ipl, keep_above=keep_above)
    if clip_z is not None or clip_ipl is not None:
        mesh.meta.pop("mesh_id", None)
    if color_by_strata and not mesh.is_empty:
        mesh.vertex_data = svc.boundaries(neuron.source).strata_cdata(mesh.vertices)
    return _old_mesh_dto(f"c{neuron.id}", mesh)


@pytest.fixture(scope="module")
def c943():
    svc = SbfsemService(offline=True)
    svc._disk_meshes = None
    svc.add_neuron(read_legacy_json(ROOT / "fixtures" / "i943.json"))
    return svc


#: Real renders of c943: discs and a Voxel remesh, whole, cut along Z or by IPL depth
#: (a cut that removes nothing included), colored by depth.
RENDERS = {
    "cylinder": {**QUICK},
    "smoothed": {"method": "cylinder", "pathway": "original", "smooth_iterations": 1},
    "clip_z": {**QUICK, "clip_z": 140.0},
    "clip_z_below_colored": {**QUICK, "clip_z": 140.0, "keep_above": False, "color_by_strata": True},
    "clip_nothing": {**QUICK, "clip_z": -1e6},
    "clip_ipl": {**QUICK, "clip_ipl": 50.0},
    "colored": {**QUICK, "color_by_strata": True},
    "voxel2": {"method": "cylinder", "pathway": "voxel2", "voxel2_um": 0.1, "voxel2_faces": 0},
    "voxel2_colored": {"method": "cylinder", "pathway": "voxel2", "voxel2_um": 0.1, "voxel2_faces": 0,
                       "color_by_strata": True},
}


@pytest.mark.parametrize("name", list(RENDERS))
def test_a_render_from_its_arrays_is_the_lists_reply_byte_for_byte(c943, name):
    """The page's binary and a JSON client's text of a render made from its arrays are
    the bytes the DTO's lists made: header, vertices, faces and ``vertex_data``."""
    options = RENDERS[name]
    old = _old_render_mesh(c943, 943, "i", **options)
    buffers = c943.render_buffers(943, "i", **options)
    assert isinstance(buffers, MeshBuffers)
    assert _json_bytes(buffers.dto()) == _json_bytes(old)
    assert _json_bytes(c943.render_mesh(943, "i", **options)) == _json_bytes(old), "the mesh route's JSON"
    held, before = _encoded_mesh(buffers), _encoded_mesh(old)
    assert isinstance(held, mesh_binary.EncodedMesh) and isinstance(before, mesh_binary.EncodedMesh)
    assert held.fields == before.fields and list(held.fields) == list(before.fields)
    assert held.arrays == before.arrays
    assert held.data == before.data
    for binary in (True, False):
        assert (_job_status_response(_status(held), binary=binary).body
                == _job_status_response(_status(before), binary=binary).body)
    if options.get("color_by_strata"):
        assert held.arrays["vertex_data"] is not None and old.vertex_data


def test_a_render_leaves_the_cached_mesh_as_it_was(c943):
    """No copy of the surface is made, so nothing may be written into the cached one: a
    cut drops the id from its own mesh, a coloring sets its own data."""
    neuron = c943.neuron(943, "i")
    cached = c943.mesh_for(neuron, QUICK)
    vertices, faces, meta = cached.vertices.copy(), cached.faces.copy(), dict(cached.meta)
    for options in RENDERS.values():
        if options.get("pathway") == "voxel2":
            continue
        buffers = c943.render_buffers(943, "i", **options)
        cut = "clip_z" in options or "clip_ipl" in options
        assert (buffers.head.mesh_id is None) == cut
        with pytest.raises(ValueError):
            buffers.faces[0] = 1  # the cached mesh's own faces, read-only
    assert c943.mesh_for(neuron, QUICK) is cached
    assert np.array_equal(cached.vertices, vertices) and np.array_equal(cached.faces, faces)
    assert cached.meta == meta and cached.meta.get("mesh_id")
    assert cached.vertex_data is None and cached.faces.flags.writeable


@pytest.mark.filterwarnings("ignore:invalid value:RuntimeWarning")  # the area of an infinite triangle
def test_a_render_the_binary_cannot_carry_stays_its_lists_json():
    """Off the 1e-4 step, past int32's range or not finite: held as the JSON text the
    DTO's lists make, as before."""
    for vertices, data in (([[0.0, 0.0, 0.0], [214748.3648, 0.0, 0.0], [0.0, 1.0, 0.0]], None),
                           ([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], [0.0, float("nan"), 1.0]),
                           ([[0.0, 0.0, 0.0], [float("inf"), 0.0, 0.0], [0.0, 1.0, 0.0]], None)):
        mesh = Mesh(np.array(vertices), np.array([[0, 1, 2]]), data, {"mesh_id": "x"})
        held = _encoded_mesh(SbfsemService.mesh_buffers("odd", mesh))
        assert isinstance(held, EncodedJSON)
        assert bytes(held) == _json_bytes(_old_mesh_dto("odd", mesh))


#: Values where scaling by 1e6 and rounding half to even differ from Python's round,
#: or nearly do: halves and their neighbors, ties made exact in binary (1/128 is
#: 7812.5e-6), the ends of the exact scaling, the tiny, the huge and the non-finite.
def _hard_values() -> np.ndarray:
    rng = np.random.default_rng(6)
    halves = (rng.integers(-5_000_000, 5_000_000, 20_000) + 0.5) / 1e6
    near = np.concatenate([np.nextafter(halves, np.inf), np.nextafter(halves, -np.inf)])
    exact_ties = np.array([1 / 128, -1 / 128, 3 / 128, 0.5, 2.5e-6, 0.0000005, 1.0000005, 123456.7890125])
    edges = np.array([2.0 ** 50 / 1e6, -(2.0 ** 50) / 1e6, 2.0 ** 53 / 1e6, 4.6e9, 1e300, -1e300, 1.7e308,
                      5e-324, -5e-324, 1e-7, -1e-7, -3e-7, 4.9999999e-7, -4.9999999e-7, 0.0, -0.0,
                      np.inf, -np.inf, np.nan])
    uniform = rng.uniform(-3, 3, 200_000)
    wide = rng.standard_normal(50_000) * 10.0 ** rng.integers(-12, 12, 50_000)
    return np.concatenate([halves, near, exact_ties, edges, uniform, wide])


@pytest.mark.parametrize("digits", [6, 4, 0])
def test_rounding_as_python_is_pythons_round_bit_for_bit(digits):
    values = _hard_values()
    got = round_as_python(values, digits)
    want = np.array([round(float(v), digits) for v in values])
    assert got.dtype == np.float64 and got.shape == values.shape
    same = (got.view(np.uint64) == want.view(np.uint64)) | (np.isnan(got) & np.isnan(want))
    assert same.all(), values[~same][:5]
    if digits == 6:
        assert (np.round(values, 6).view(np.uint64) != want.view(np.uint64)).sum() > 1000, (
            "np.round alone parts from Python here: the values reach the cases it gets wrong")


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


def test_a_render_job_never_makes_the_lists(web, monkeypatch):
    """The job holds the binary made from the render's arrays, and a JSON poll reads it
    back from the binary: the DTO's lists, 380 bytes a triangle in the web process, are
    not made for either."""
    client, _ = web

    def no_lists(self):
        raise AssertionError("the DTO's lists were made")

    monkeypatch.setattr(MeshBuffers, "dto", no_lists)
    started = client.get("/api/neurons/i/943/mesh-job", params={**QUICK, "color_by_strata": True}).json()
    job = _wait(client, started["job_id"])
    assert job.status == "done" and isinstance(job.result, mesh_binary.EncodedMesh), job.detail
    binary = client.get(f"/api/mesh-jobs/{job.id}", headers=BINARY)
    plain = client.get(f"/api/mesh-jobs/{job.id}")
    assert binary.headers["content-type"] == mesh_binary.MEDIA_TYPE
    assert plain.headers["content-type"] == "application/json"
    assert_same_as_json(decode(binary.content), plain.json())
    assert decode(binary.content)["mesh"]["vertex_data"] is not None


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
