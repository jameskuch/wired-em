"""Viking's section corrections: the wire codec, the client, and applying them.

The golden bytes below were produced by the protobuf runtime (7.36) from
``section_correction.proto`` (github.com/jamesra/viking_gRPC_protos,
2026-09-22), so the hand-written codec is held to what the service's own
serializer writes and reads.  The end-to-end tests run a local gRPC server
that answers the way ``CorrectionsService`` does (``CorrectPoints`` adds the
sampled offset; a section with no map comes back not found and untrusted).
"""

from __future__ import annotations

import struct
import time
from concurrent import futures
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from sbfsem.core.structure import Neuron
from sbfsem.io import corrections as corr
from sbfsem.io.cache import DiskCache

GOLDEN = {
    "correct_points_request": "0a0e496e666572696f724d6f6e6b657912064772696431361a2208a8071212090000000060e336411100000000882a41411209110000000000000cc01a1708a50a12120977be9f1a2fdd5e40110000000000a88840",
    "correct_points_response": "0a0e496e666572696f724d6f6e6b657912064772696431361a3808a80710011a2609000000806ce336411100000000832a41411900000000000029402100000000000024c028011a09110000000000000cc01a16080c1a12090000000000001c40110000000000002040",
    "list_sets_response": "0a4e0a0e496e666572696f724d6f6e6b657912064772696431361a340a0c08b882c9d5061080cab5ee0112060880f7c4d506190000000000409f4021000000000040bf40280332080802100318052007",
    "section_correction": "0a0e496e666572696f724d6f6e6b6579120647726964313618a50a21000000000070b7c029000000000040af40310000000000409f403a2108fdffffffffffffffff011002190000000000c862c021000000000080404028043a18080710ffffffffffffffffff0121000000000000f03f280c",
    "list_sections_response": "0a0e496e666572696f724d6f6e6b657912064772696431361a140102a50a808080808020fbffffffffffffffff01",
    "rebuild_status": "0801120608c0a0ccd5062204626f6f6d2a0e496e666572696f724d6f6e6b65792a0452504331",
    "section_request_negative_z": "0a0e496e666572696f724d6f6e6b6579120647726964313618ffffffffffffffffff01",
    "list_volumes_response": "0a500a0e496e666572696f724d6f6e6b6579122268747470733a2f2f6578616d706c652f696e666572696f722f56696b696e67584d4c1a1a68747470733a2f2f6578616d706c652f616e6e6f746174696f6e",
}


def golden(name: str) -> bytes:
    return bytes.fromhex(GOLDEN[name])


# -- the codec, against protobuf's own bytes ---------------------------------
def test_encode_matches_protobuf_for_a_correct_points_request():
    request = {
        "volume_name": "InferiorMonkey",
        "stos_group": "Grid16",
        "sections": [
            {"z": 936, "points": [{"x": 1.5e6, "y": 2.25e6}, {"x": 0.0, "y": -3.5}]},
            {"z": 1317, "points": [{"x": 123.456, "y": 789.0}]},
        ],
    }
    assert corr.encode(request, corr.CORRECT_POINTS_REQUEST_SCHEMA) == golden(
        "correct_points_request")


def test_encode_negative_int64_is_ten_bytes_like_protobuf():
    request = {"volume_name": "InferiorMonkey", "stos_group": "Grid16", "z": -1}
    assert corr.encode(request, corr.SECTION_REQUEST_SCHEMA) == golden(
        "section_request_negative_z")


def test_encode_refuses_a_field_the_schema_does_not_have():
    with pytest.raises(KeyError):
        corr.encode({"volume": "InferiorMonkey"}, corr.LIST_SETS_REQUEST_SCHEMA)


def test_decode_correct_points_response():
    answer = corr.decode(golden("correct_points_response"), corr.CORRECT_POINTS_RESPONSE_SCHEMA)
    assert answer["volume_name"] == "InferiorMonkey"
    first, second = answer["sections"]
    assert (first["z"], first["found_section"]) == (936, True)
    assert first["points"][0] == {"x": 1500012.5, "y": 2249990.0, "dx": 12.5, "dy": -10.0,
                                  "trusted": True}
    # A point at the defaults is sent with no fields at all.
    assert first["points"][1] == {"x": 0.0, "y": -3.5, "dx": 0.0, "dy": 0.0, "trusted": False}
    assert (second["z"], second["found_section"]) == (12, False)
    assert second["points"] == [{"x": 7.0, "y": 8.0, "dx": 0.0, "dy": 0.0, "trusted": False}]


def test_decode_correction_sets_with_provenance():
    answer = corr.decode(golden("list_sets_response"), corr.LIST_SETS_RESPONSE_SCHEMA)
    (cset,) = [corr.CorrectionSet.from_message(m) for m in answer["sets"]]
    assert cset.volume_name == "InferiorMonkey"
    assert cset.stos_group == "Grid16"
    assert cset.built_utc == datetime.fromtimestamp(1790067000.5, tz=UTC)
    assert cset.annotation_watermark == datetime.fromtimestamp(1790000000, tz=UTC)
    assert (cset.pitch_nm, cset.kernel_radius_nm, cset.min_annotation_votes) == (2000.0, 8000.0, 3)
    window = answer["sets"][0]["provenance"]["residual_window"]
    assert window == {"min_both": 2, "max_both": 3, "min_one": 5, "max_one": 7}
    assert corr.CorrectionSet.from_description(cset.describe()) == cset


def test_decode_section_lattice_with_negative_indices():
    answer = corr.decode(golden("section_correction"), corr.SECTION_CORRECTION_SCHEMA)
    assert (answer["z"], answer["origin_x_nm"], answer["origin_y_nm"], answer["pitch_nm"]) == (
        1317, -6000.0, 4000.0, 2000.0)
    assert answer["nodes"] == [
        {"gx": -3, "gy": 2, "dx": -150.25, "dy": 33.0, "vote_count": 4},
        {"gx": 7, "gy": -1, "dx": 0.0, "dy": 1.0, "vote_count": 12},
    ]


def test_decode_packed_int64_and_repeated_strings():
    answer = corr.decode(golden("list_sections_response"), corr.LIST_SECTIONS_RESPONSE_SCHEMA)
    assert answer["z"] == [1, 2, 1317, 2**40, -5]
    status = corr.decode(golden("rebuild_status"), corr.REBUILD_STATUS_SCHEMA)
    assert status["in_progress"] is True
    assert status["last_volumes"] == ["InferiorMonkey", "RPC1"]
    assert status["last_error"] == "boom"
    assert status["last_completed_utc"] is None
    volumes = corr.decode(golden("list_volumes_response"), corr.LIST_VOLUMES_RESPONSE_SCHEMA)
    assert volumes["volumes"][0]["vikingxml_endpoint"] == "https://example/inferior/VikingXML"


def test_round_trip_and_unknown_fields_are_skipped():
    message = {"volume_name": "RPC1", "stos_group": "Brute", "z": [3, -4, 5]}
    data = corr.encode(message, corr.LIST_SECTIONS_RESPONSE_SCHEMA)
    assert corr.decode(data, corr.LIST_SECTIONS_RESPONSE_SCHEMA) == message
    # A field this reader does not know (number 9, a string) is passed over.
    extra = data + bytes([9 << 3 | 2, 3]) + b"new"
    assert corr.decode(extra, corr.LIST_SECTIONS_RESPONSE_SCHEMA) == message
    with pytest.raises(ValueError):
        corr.decode(data[:-1], corr.LIST_SECTIONS_RESPONSE_SCHEMA)


def test_targets():
    assert corr._parse_target("https://corrections.codepharm.net") == (
        "corrections.codepharm.net:443", True)
    assert corr._parse_target("http://localhost:5100/") == ("localhost:5100", False)
    assert corr._parse_target("corrections.codepharm.net:8443") == (
        "corrections.codepharm.net:8443", True)


def test_choose_set_follows_dataexport():
    a = corr.CorrectionSet("InferiorMonkey", "Grid16")
    b = corr.CorrectionSet("InferiorMonkey", "brute32")
    assert corr.choose_set([a, b]) is b  # alphabetical, ignoring case
    assert corr.choose_set([a, b], "GRID16") is a
    assert corr.choose_set([a, b], "Mosaic") is None
    assert corr.choose_set([]) is None


# -- a local service ----------------------------------------------------------
class FakeService:
    """Answers like ``CorrectionsService``: one set, a field on some sections.

    ``field`` maps Z to ``(dx, dy, radius_nm)``: points within ``radius_nm``
    of the origin get the offset and are trusted; farther ones get zero,
    untrusted.  ``"*"`` stands for every other section; without it, other
    sections are not found.
    """

    def __init__(self, field: dict[int, tuple[float, float, float]]):
        self.field = field
        self.calls: list[str] = []
        self.points_seen = 0
        # CorrectStructures: the Locations it knows (location_id, structure_id,
        # z, x, y), the (dx, dy) each mode number adds to every one of them, the
        # requests it was sent, and whether it throws as the live one did:
        # for every request (True), or for the structures in a set, with
        # ``fail_code``.
        self.locations: list[dict] = []
        self.fit: dict[int, tuple[float, float]] = {}
        self.structure_requests: list[dict] = []
        self.fail_structures: bool | set[int] = False
        self.fail_code = "UNKNOWN"
        # The volumes it publishes a set for, and the (status, details) it
        # answers a correction of one volume with while that volume's server is
        # not reachable from the service (Cped's and Inferior's, 2026-10-20).
        self.volumes = ["InferiorMonkey"]
        self.volume_errors: dict[str, tuple[str, str]] = {}
        # GetRebuildStatus: when the last rebuild finished (the service
        # rebuilds on every start, so a restart moves it).
        self.rebuilt = 1790300000
        # GetRebuildStatus.service_version, empty before service 1.1.0.
        self.version = ""

    def handler(self):
        import grpc

        def unary(name, request_schema, response_schema, fn):
            def run(request, context):
                self.calls.append(name)
                return fn(request, context)
            return grpc.unary_unary_rpc_method_handler(
                run,
                request_deserializer=lambda b: corr.decode(b, request_schema),
                response_serializer=lambda m: corr.encode(m, response_schema),
            )

        def sets(request, context):
            asked = request["volume_name"]
            return {"sets": [{
                "volume_name": name, "stos_group": "Grid16",
                "provenance": {"built_utc": {"seconds": 1790067000}, "pitch_nm": 2000.0,
                               "kernel_radius_nm": 8000.0, "min_annotation_votes": 3},
            } for name in self.volumes if asked in ("", name)]}

        def volume_down(request, context):
            failing = self.volume_errors.get(request["volume_name"])
            if failing:
                context.abort(getattr(grpc.StatusCode, failing[0]), failing[1])

        def correct(request, context):
            volume_down(request, context)
            if request["stos_group"] != "Grid16":
                context.abort(grpc.StatusCode.NOT_FOUND, "no correction set")
            seen = set()
            out = []
            for section in request["sections"]:
                if not section["points"] or section["z"] in seen:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, "bad sections")
                seen.add(section["z"])
                self.points_seen += len(section["points"])
                entry = self.field.get(section["z"], self.field.get("*"))
                points = []
                for p in section["points"]:
                    if entry is None:
                        points.append({"x": p["x"], "y": p["y"]})
                        continue
                    dx, dy, radius = entry
                    near = np.hypot(p["x"], p["y"]) <= radius
                    ox, oy = (dx, dy) if near else (0.0, 0.0)
                    points.append({"x": p["x"] + ox, "y": p["y"] + oy, "dx": ox, "dy": oy,
                                   "trusted": bool(near)})
                out.append({"z": section["z"], "found_section": entry is not None,
                            "points": points})
            return {**{k: request[k] for k in ("volume_name", "stos_group")}, "sections": out}

        def structures(request, context):
            self.structure_requests.append(request)
            volume_down(request, context)
            failing = self.fail_structures
            if failing is True or (failing and set(request["structure_ids"]) & failing):
                context.abort(getattr(grpc.StatusCode, self.fail_code),
                              "Exception was thrown by handler.")
            if request["stos_group"] != "Grid16":
                context.abort(grpc.StatusCode.NOT_FOUND, "no correction set")
            mode = 3 if request["correction"] is None else request["correction"]
            dx, dy = self.fit.get(mode, (0.0, 0.0))
            wanted = set(request["structure_ids"])
            return {**{k: request[k] for k in ("volume_name", "stos_group")}, "locations": [
                {**loc, "x": loc["x"] + dx, "y": loc["y"] + dy, "dx": dx, "dy": dy}
                for loc in self.locations
                if loc["structure_id"] in wanted or request["include_children"]]}

        def rebuild_status(request, context):
            return {"in_progress": False, "last_completed_utc": {"seconds": self.rebuilt},
                    "service_version": self.version}

        return grpc.method_handlers_generic_handler(corr.SERVICE, {
            "GetRebuildStatus": unary("GetRebuildStatus", corr.EMPTY_SCHEMA,
                                      corr.REBUILD_STATUS_SCHEMA, rebuild_status),
            "ListCorrectionSets": unary("ListCorrectionSets", corr.LIST_SETS_REQUEST_SCHEMA,
                                        corr.LIST_SETS_RESPONSE_SCHEMA, sets),
            "CorrectPoints": unary("CorrectPoints", corr.CORRECT_POINTS_REQUEST_SCHEMA,
                                   corr.CORRECT_POINTS_RESPONSE_SCHEMA, correct),
            "CorrectStructures": unary("CorrectStructures", corr.CORRECT_STRUCTURES_REQUEST_SCHEMA,
                                       corr.CORRECT_STRUCTURES_RESPONSE_SCHEMA, structures),
        })


@pytest.fixture
def service():
    grpc = pytest.importorskip("grpc")
    fake = FakeService({
        # Discs near the origin on 936 move (+120, -80) nm; far ones do not.
        936: (120.0, -80.0, 50_000.0),
        # Section 937 is published but has no support where the cell is.
        937: (500.0, 500.0, 0.0),
    })
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    server.add_generic_rpc_handlers((fake.handler(),))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    fake.server = server  # a test may take the service away
    try:
        yield fake, f"http://127.0.0.1:{port}"
    finally:
        server.stop(None)


def make_neuron(with_curve: bool = True, source: str = "i") -> Neuron:
    """Four annotations on 935-938 (7.5 nm pixels) and a closed curve on 936."""
    nodes = pd.DataFrame({
        "ID": [1, 2, 3, 4, 5],
        "ParentID": [9, 9, 9, 9, 9],
        "VolumeX": [1000.0, 1000.0, 1000.0, 1000.0, 9_000.0],
        "VolumeY": [2000.0, 2000.0, 2000.0, 2000.0, 2_000.0],
        "Z": [935, 936, 937, 938, 936],
        "Radius": [40.0, 40.0, 40.0, 40.0, 0.0],
        "X": [0.0] * 5, "Y": [0.0] * 5,
        "OffEdge": [False] * 5, "Terminal": [False] * 5,
        "TypeCode": [1, 1, 1, 1, 6 if with_curve else 1],
    })
    edges = pd.DataFrame({"ID": [9, 9, 9], "A": [1, 2, 3], "B": [2, 3, 4]})
    neuron = Neuron(9, source, nodes=nodes, edges=edges, volume_scale=(7.5, 7.5, 90.0),
                    omitted_ids=[], data_stamp=datetime(2026, 9, 1, tzinfo=UTC))
    if with_curve:
        ring = np.array([[67.0, 15.0], [68.0, 15.0], [68.0, 16.0], [67.0, 16.0]])
        neuron.geometries = pd.DataFrame([{
            "ID": 5, "ParentID": 9, "Z": 936, "Zum": 936 * 0.09, "Curve": [ring],
            "Registered": True}])
    return neuron


def test_correct_neuron_moves_trusted_annotations_only(service):
    fake, url = service
    neuron = make_neuron()
    before = neuron.nodes.set_index("ID")[["Xum", "Yum"]].copy()
    ring_before = neuron.geometries.iloc[0]["Curve"][0].copy()
    client = corr.CorrectionsClient(url)
    try:
        result = corr.correct_neuron(neuron, client)
    finally:
        client.close()
    assert result is not None and result.cset.stos_group == "Grid16"
    after = neuron.nodes.set_index("ID")[["Xum", "Yum"]]
    shift = (after - before).round(9)
    # 2 (936, 7.5 um from the origin) and the outline 5 (936, 68 um away:
    # outside the fake's support) -> only 2 moves; 935, 937, 938 do not.
    assert shift.loc[2].tolist() == [0.12, -0.08]
    for i in (1, 3, 4, 5):
        assert shift.loc[i].tolist() == [0.0, 0.0]
    # Viking's own numbers stay as they were.
    assert neuron.nodes["VolumeX"].tolist() == [1000.0, 1000.0, 1000.0, 1000.0, 9000.0]
    np.testing.assert_array_equal(neuron.geometries.iloc[0]["Curve"][0], ring_before)
    summary = neuron.section_correction
    assert summary["n_annotations"] == 5
    assert summary["n_found"] == 3  # 936 x2 and 937
    assert summary["n_trusted"] == 1
    assert summary["n_moved"] == 1
    assert summary["max_um"] == pytest.approx(np.hypot(0.12, 0.08))
    assert fake.calls == ["ListCorrectionSets", "CorrectPoints"]
    assert fake.points_seen == 5


def test_a_closed_curve_moves_with_its_annotation(service):
    fake, url = service
    fake.field[936] = (120.0, -80.0, 1e9)  # support everywhere on 936
    neuron = make_neuron()
    ring_before = neuron.geometries.iloc[0]["Curve"][0].copy()
    with corr.CorrectionsClient(url) as client:
        corr.correct_neuron(neuron, client)
    np.testing.assert_allclose(neuron.geometries.iloc[0]["Curve"][0], ring_before + [0.12, -0.08])
    row = neuron.nodes.set_index("ID").loc[5]
    assert (row["CorrDXnm"], row["CorrDYnm"]) == (120.0, -80.0)


def test_apply_twice_is_refused(service):
    _, url = service
    neuron = make_neuron()
    with corr.CorrectionsClient(url) as client:
        correction = corr.correct_neuron(neuron, client)
    with pytest.raises(ValueError):
        corr.apply_offsets(neuron, correction)


def test_no_published_set_leaves_the_cell_alone(service):
    _, url = service
    neuron = make_neuron()
    before = neuron.nodes.copy()
    with corr.CorrectionsClient(url) as client:
        assert corr.correct_neuron(neuron, client, stos_group="Mosaic") is None
    pd.testing.assert_frame_equal(neuron.nodes, before)
    assert neuron.section_correction is None


def test_cached_offsets_are_not_fetched_again(service, tmp_path):
    fake, url = service
    cache = DiskCache(tmp_path)
    with corr.CorrectionsClient(url) as client:
        first = corr.correct_neuron(make_neuron(), client, cache=cache)
        again = make_neuron()
        second = corr.correct_neuron(again, client, cache=cache)
    assert fake.calls.count("CorrectPoints") == 1
    assert second.cached and not first.cached
    assert second.token == first.token
    assert again.section_correction["cached"] is True


def test_sampling_splits_requests_by_whole_sections(service):
    fake, url = service
    z = np.repeat([936, 937, 938], 4)
    with corr.CorrectionsClient(url) as client:
        cset = corr.choose_set(client.correction_sets("InferiorMonkey"))
        out = corr.sample_offsets(client, cset, z, np.full(12, 100.0), np.full(12, 100.0),
                                  points_per_request=4)
        assert fake.calls.count("CorrectPoints") == 3
        # A limit that falls inside a section never splits it: 936 and 937 go
        # together, 938 alone.
        corr.sample_offsets(client, cset, z, np.full(12, 100.0), np.full(12, 100.0),
                            points_per_request=5)
    assert fake.calls.count("CorrectPoints") == 5
    assert out["trusted"].tolist() == [True] * 4 + [False] * 8
    assert out["found"].tolist() == [True] * 8 + [False] * 4


def test_unreachable_service_is_unavailable():
    grpc = pytest.importorskip("grpc")
    del grpc
    client = corr.CorrectionsClient("http://127.0.0.1:9", timeout=3.0)
    try:
        with pytest.raises(corr.CorrectionsUnavailable):
            client.correction_sets("InferiorMonkey")
    finally:
        client.close()


def test_provider_falls_back_to_what_it_remembers(service, tmp_path):
    fake, url = service
    cache = DiskCache(tmp_path)
    live = corr.CorrectionsProvider(url, cache=cache)
    try:
        assert live.correct(make_neuron()) is not None
    finally:
        live.close()
    # The service goes away: a new process remembers the set and the offsets.
    gone = corr.CorrectionsProvider("http://127.0.0.1:9", cache=cache, timeout=3.0)
    try:
        # The set list is on disk under the live URL, not this one: nothing
        # to go on, so it says why.
        with pytest.raises(corr.CorrectionsUnavailable):
            gone.correct(make_neuron())
        assert gone.last_error
    finally:
        gone.close()
    offline = corr.CorrectionsProvider(url, cache=cache, offline=True)
    try:
        neuron = make_neuron()
        result = offline.correct(neuron)
        assert result is not None and result.cached
        assert neuron.section_correction["service_note"] == "offline"
        # A cell never corrected before cannot be, offline.
        other = make_neuron()
        other.id = 10
        with pytest.raises(corr.CorrectionsUnavailable):
            offline.correct(other)
    finally:
        offline.close()
    assert fake.calls.count("CorrectPoints") == 1


def test_provider_pauses_after_a_failure(monkeypatch):
    pytest.importorskip("grpc")
    provider = corr.CorrectionsProvider("http://127.0.0.1:9", timeout=3.0, retry_after_s=60)
    try:
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(make_neuron())
        asked = []
        monkeypatch.setattr(provider.client, "correction_sets",
                            lambda v: asked.append(v) or [])
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(make_neuron())
        assert asked == []  # not asked again inside the pause
    finally:
        provider.close()


# -- the web service ----------------------------------------------------------
@pytest.fixture
def web(service, tmp_path):
    """A service with corrections on, c1178's annotations from the cassette."""
    from pathlib import Path

    from sbfsem.io.odata import ODataClient
    from sbfsem.service.facade import SbfsemService

    fake, url = service
    fake.field.clear()
    fake.field["*"] = (120.0, -80.0, 1e12)
    cassettes = Path(__file__).resolve().parents[1] / "cassettes"
    svc = SbfsemService(cache_dir=tmp_path, section_corrections=True, corrections_url=url)
    svc._clients["NeitzInferiorMonkey"] = ODataClient(
        "i", cache=DiskCache(cassettes), offline=True)
    yield fake, svc
    svc.close()


def test_a_web_load_applies_the_correction_and_says_so(web):
    fake, svc = web
    detail = svc.load_neuron(1178, "i", include_synapses=False)
    sc = detail.summary.section_correction
    assert sc is not None and sc.requested and sc.applied
    assert (sc.volume_name, sc.stos_group) == ("InferiorMonkey", "Grid16")
    assert sc.n_annotations == sc.n_trusted == sc.n_moved == 2434
    assert sc.max_um == pytest.approx(np.hypot(0.12, 0.08))
    corrected = svc.neuron(1178, "i")
    nodes = svc.nodes(1178, "i")
    assert {tuple(round(v, 9) for v in n.shift_um) for n in nodes} == {(0.12, -0.08)}

    # Off: loaded again with Viking's positions, and nothing asked of the service.
    calls = len(fake.calls)
    plain = svc.load_neuron(1178, "i", include_synapses=False, section_corrections=False)
    assert plain.summary.section_correction is None
    raw = svc.neuron(1178, "i")
    assert raw is not corrected and len(fake.calls) == calls
    assert all(n.shift_um is None for n in svc.nodes(1178, "i"))
    moved = corrected.nodes.set_index("ID")[["Xum", "Yum"]] - raw.nodes.set_index("ID")[["Xum", "Yum"]]
    np.testing.assert_allclose(moved.to_numpy(), np.tile([0.12, -0.08], (2434, 1)), atol=1e-9)
    ring_raw = raw.geometries.set_index("ID")["Curve"]
    ring_cor = corrected.geometries.set_index("ID")["Curve"]
    first = ring_raw.index[0]
    np.testing.assert_allclose(ring_cor[first][0] - ring_raw[first][0], [[0.12, -0.08]] * len(ring_raw[first][0]))

    # On again; a repeat of the same request is answered from memory.
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections=True)
    again = svc.neuron(1178, "i")
    svc.load_neuron(1178, "i", include_synapses=False)
    assert svc.neuron(1178, "i") is again


def test_an_unreachable_service_leaves_the_cell_as_viking_stores_it(tmp_path):
    pytest.importorskip("grpc")
    from pathlib import Path

    from sbfsem.io.odata import ODataClient
    from sbfsem.service.facade import SbfsemService

    cassettes = Path(__file__).resolve().parents[1] / "cassettes"
    svc = SbfsemService(cache_dir=tmp_path, section_corrections=True,
                        corrections_url="http://127.0.0.1:9")
    svc._clients["NeitzInferiorMonkey"] = ODataClient(
        "i", cache=DiskCache(cassettes), offline=True)
    try:
        detail = svc.load_neuron(1178, "i", include_synapses=False)
        sc = detail.summary.section_correction
        assert sc.requested and not sc.applied
        assert sc.note.startswith("Not applied")
        first = svc.neuron(1178, "i")
        assert "CorrDXnm" not in first.nodes.columns
        # Inside the pause after the failure, a repeat load is not retried.
        svc.load_neuron(1178, "i", include_synapses=False)
        assert svc.neuron(1178, "i") is first
    finally:
        svc.close()


def test_a_corrected_render_has_its_own_disk_key(service, tmp_path, i943_path):
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    fake, url = service
    fake.field.clear()
    fake.field["*"] = (120.0, -80.0, 1e12)
    svc = SbfsemService(cache_dir=tmp_path)
    keys = []
    real_put = svc._disk_meshes.put
    svc._disk_meshes.put = lambda key, mesh: (keys.append(key), real_put(key, mesh))
    try:
        svc.add_neuron(read_legacy_json(i943_path))
        svc.mesh_object(943, "i", method="cylinder", pathway="original", smooth_iterations=0)
        neuron = read_legacy_json(i943_path)
        with corr.CorrectionsClient(url) as client:
            correction = corr.correct_neuron(neuron, client)
        svc.add_neuron(neuron)
        dto = svc.neuron_detail(943, "i").summary.section_correction
        assert dto.applied and dto.note == "As saved in the file"
        svc.mesh_object(943, "i", method="cylinder", pathway="original", smooth_iterations=0)
    finally:
        svc.close()
    plain, moved = keys
    # An uncorrected cell's key is exactly what it always was.
    assert len(plain) == len(moved) - 1
    assert moved[-1] == f"section-correction:{correction.token}"
    assert moved[:-1] == plain


def test_exports_say_the_positions_were_corrected(service, tmp_path):
    from sbfsem.export.json_v2 import read_neuron_json, write_neuron_json
    from sbfsem.export.swc import write_swc

    fake, url = service
    fake.field.clear()
    fake.field["*"] = (120.0, -80.0, 1e12)
    neuron = make_neuron(with_curve=False)
    plain = write_swc(neuron, tmp_path / "plain.swc").read_text()
    with corr.CorrectionsClient(url) as client:
        corr.correct_neuron(neuron, client)
    swc = write_swc(neuron, tmp_path / "corrected.swc").read_text()
    assert "SECTION_CORRECTION" not in plain and "# POSITIONS viking (" in plain
    assert "# SECTION_CORRECTION InferiorMonkey Grid16 built 2026-09-22T" in swc
    assert "5 of 5 annotations moved in x and y" in swc
    assert "# POSITIONS viking corrected (" in swc

    back = read_neuron_json(write_neuron_json(neuron, tmp_path / "c9.json"))
    assert back.section_correction["token"] == neuron.section_correction["token"]
    np.testing.assert_allclose(back.nodes["Xum"], neuron.nodes["Xum"])
    np.testing.assert_allclose(back.nodes["CorrDXnm"], [120.0] * 5)


# -- CorrectStructures and the modes (2026-09-24) ------------------------------
# No protobuf runtime was at hand for these, so the bytes are assembled by hand
# from the proto3 wire format; the live service took exactly these requests on
# 2026-09-24 and answered c21768 with 1,680 Locations.
def test_encode_correct_structures_sends_the_mode_even_when_it_is_none():
    head = (bytes.fromhex("0a0e") + b"InferiorMonkey" + bytes.fromhex("120e") + b"SliceToVolume1"
            + bytes.fromhex("1a0488aa0105")  # structure_ids 21768 and 5, packed varints
            + bytes.fromhex("2001"))  # include_children
    request = {"volume_name": "InferiorMonkey", "stos_group": "SliceToVolume1",
               "structure_ids": [21768, 5], "include_children": True}
    schema = corr.CORRECT_STRUCTURES_REQUEST_SCHEMA
    # NONE is 0, proto3's default, and is still sent: an unset mode means All.
    assert corr.encode({**request, "correction": 0}, schema) == head + bytes.fromhex("2800")
    assert corr.encode({**request, "correction": 2}, schema) == head + bytes.fromhex("2802")
    assert corr.encode(request, schema) == head
    assert corr.decode(head + bytes.fromhex("2800"), schema)["correction"] == 0
    assert corr.decode(head, schema)["correction"] is None


def test_decode_correct_structures_response():
    def double(number: int, value: float) -> bytes:
        return bytes([number << 3 | 1]) + struct.pack("<d", value)

    body = (bytes.fromhex("089bd50f")  # location_id 256667
            + bytes.fromhex("1088aa01")  # structure_id 21768
            + bytes.fromhex("189406")  # z 788
            + double(4, 131420.5) + double(5, 168265.0) + double(6, 12.5) + double(7, -10.0))
    data = (bytes.fromhex("0a0e") + b"InferiorMonkey" + bytes.fromhex("120e") + b"SliceToVolume1"
            + bytes([3 << 3 | 2, len(body)]) + body
            + bytes([3 << 3 | 2, 2]) + bytes.fromhex("0805"))  # a Location with only its ID
    answer = corr.decode(data, corr.CORRECT_STRUCTURES_RESPONSE_SCHEMA)
    assert answer["locations"] == [
        {"location_id": 256667, "structure_id": 21768, "z": 788,
         "x": 131420.5, "y": 168265.0, "dx": 12.5, "dy": -10.0},
        {"location_id": 5, "structure_id": 0, "z": 0, "x": 0.0, "y": 0.0, "dx": 0.0, "dy": 0.0},
    ]


def test_correction_mode_names():
    assert corr.correction_mode(True) == "neighbor"  # what the old switch meant
    for off in (False, None, "off", "none", "0", "", "No"):
        assert corr.correction_mode(off) == "off"
    assert corr.correction_mode("CurveFit") == "curvefit"
    assert corr.correction_mode("curve_fit") == "curvefit"
    assert corr.correction_mode("ALL") == "all"
    assert corr.correction_mode("1") == "neighbor"
    with pytest.raises(ValueError):
        corr.correction_mode("sideways")


def make_structure_neuron(source: str = "i") -> Neuron:
    """Discs 1-4 with section X/Y; 5 a line, whose X/Y columns are empty (0, 0)."""
    neuron = make_neuron(with_curve=False, source=source)
    nodes = neuron.nodes
    nodes.loc[nodes["ID"] <= 4, ["X", "Y"]] = [133.0, 267.0]
    nodes.loc[nodes["ID"] == 5, "TypeCode"] = 5
    return neuron


@pytest.fixture
def structures(service):
    """The fake knows Locations 1, 2, 3 and 5 of c9 (not 4); curvefit adds
    (+120, -80) nm to each, all (+60, +40)."""
    fake, url = service
    fake.locations = [{"location_id": i, "structure_id": 9, "z": z, "x": 7500.0, "y": 15000.0}
                      for i, z in ((1, 935), (2, 936), (3, 937), (5, 936))]
    fake.fit = {2: (120.0, -80.0), 3: (60.0, 40.0)}
    return fake, url


def test_curvefit_moves_what_the_service_corrected(structures):
    fake, url = structures
    neuron = make_structure_neuron()
    before = neuron.nodes.set_index("ID")[["Xum", "Yum"]].copy()
    with corr.CorrectionsClient(url) as client:
        result = corr.correct_neuron(neuron, client, mode="curvefit")
    assert result is not None
    (request,) = fake.structure_requests
    assert request["structure_ids"] == [9] and request["include_children"] is True
    assert request["correction"] == corr.CORRECTION_MODES["curvefit"] == 2
    shift = (neuron.nodes.set_index("ID")[["Xum", "Yum"]] - before).round(9)
    for i in (1, 2, 3):
        assert shift.loc[i].tolist() == [0.12, -0.08]
    # 4 was not answered for; the service placed 5 at (7.5, 15) um, 60 um from
    # where the cell has it -- past PLACEMENT_LIMIT_NM.
    for i in (4, 5):
        assert shift.loc[i].tolist() == [0.0, 0.0]
    s = neuron.section_correction
    assert s["mode"] == "curvefit"
    assert (s["n_annotations"], s["n_found"], s["n_trusted"], s["n_moved"]) == (5, 4, 3, 3)
    assert fake.calls == ["ListCorrectionSets", "CorrectStructures"]


def test_a_line_placed_where_the_cell_has_it_is_corrected(structures):
    """Empty X/Y columns are no reason by themselves: since 2026-09-24 the
    service maps such a line from its outline's centroid."""
    fake, url = structures
    fake.locations[-1]["x"] = 67500.0  # Location 5 where the cell has it: 9,000 px x 7.5 nm
    neuron = make_structure_neuron()
    with corr.CorrectionsClient(url) as client:
        corr.correct_neuron(neuron, client, mode="curvefit")
    assert neuron.section_correction["n_trusted"] == 4
    row = neuron.nodes.set_index("ID").loc[5]
    assert (row["CorrDXnm"], row["CorrDYnm"]) == (120.0, -80.0)


def test_structure_offsets_are_kept_per_mode(structures, tmp_path):
    fake, url = structures
    cache = DiskCache(tmp_path)
    with corr.CorrectionsClient(url) as client:
        first = corr.correct_neuron(make_structure_neuron(), client, cache=cache, mode="curvefit")
        again = corr.correct_neuron(make_structure_neuron(), client, cache=cache, mode="curvefit")
        both = corr.correct_neuron(make_structure_neuron(), client, cache=cache, mode="all")
    assert [r["correction"] for r in fake.structure_requests] == [2, 3]
    assert again.cached and again.token == first.token
    assert both.token != first.token and both.summary()["mode"] == "all"


def test_a_service_version_keys_the_cache_instead_of_restarts(structures, tmp_path):
    """Service 1.1.0 names its version: a restart that changes nothing keeps
    the cached answers, an algorithm deploy (a new version) drops them."""
    fake, url = structures
    fake.version = "1.1.0"
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path), sets_ttl_s=0)
    try:
        provider.correct(make_structure_neuron(), "curvefit")
        fake.rebuilt += 3600  # restarted, same version
        kept = provider.correct(make_structure_neuron(), "curvefit")
        assert kept.cached and len(fake.structure_requests) == 1
        fake.version = "1.1.1"
        fresh = provider.correct(make_structure_neuron(), "curvefit")
        assert not fresh.cached and len(fake.structure_requests) == 2
    finally:
        provider.close()


def test_service_version_is_read_from_both_answers():
    status = corr.decode(corr.encode({"service_version": "1.1.0"}, corr.REBUILD_STATUS_SCHEMA),
                         corr.REBUILD_STATUS_SCHEMA)
    answer = corr.decode(
        corr.encode({"volume_name": "RC1", "service_version": "1.1.0"},
                    corr.CORRECT_STRUCTURES_RESPONSE_SCHEMA),
        corr.CORRECT_STRUCTURES_RESPONSE_SCHEMA)
    assert status["service_version"] == answer["service_version"] == "1.1.0"


def test_a_restarted_service_is_asked_again_for_cached_structures(structures, tmp_path):
    """On 2026-09-25 the service changed its answers with no set rebuilt: the
    cache must follow the service's own stamp, not only the set's build time."""
    fake, url = structures
    cache = DiskCache(tmp_path)
    provider = corr.CorrectionsProvider(url, cache=cache, sets_ttl_s=0)
    try:
        provider.correct(make_structure_neuron(), "curvefit")
        again = provider.correct(make_structure_neuron(), "curvefit")
        assert again.cached and len(fake.structure_requests) == 1
        fake.rebuilt += 3600  # redeployed: it rebuilt on start
        fresh = provider.correct(make_structure_neuron(), "curvefit")
        assert not fresh.cached and len(fake.structure_requests) == 2
    finally:
        provider.close()
    # Offline, the stamp remembered on disk still finds the offsets.
    offline = corr.CorrectionsProvider(url, cache=cache, offline=True)
    try:
        kept = offline.correct(make_structure_neuron(), "curvefit")
        assert kept.cached and len(fake.structure_requests) == 2
    finally:
        offline.close()


def test_an_swc_names_the_mode(structures, tmp_path):
    from sbfsem.export.swc import write_swc

    _, url = structures
    neuron = make_structure_neuron()
    with corr.CorrectionsClient(url) as client:
        corr.correct_neuron(neuron, client, mode="curvefit")
    swc = write_swc(neuron, tmp_path / "fit.swc").read_text()
    assert "3 of 5 annotations moved in x and y" in swc
    assert "(Viking's curve fit along each traced process)" in swc


def test_a_web_load_takes_the_mode_and_reloads_for_another(web):
    fake, svc = web
    plain = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    assert plain.summary.section_correction is None
    loaded = svc.neuron(1178, "i")
    nodes = loaded.nodes
    sx, sy = map(float, loaded.volume_scale[:2])
    # The service places each Location where the cell has it (its stored
    # position: no live registration here) and moves it by the fit.
    fake.locations = [{"location_id": int(r.ID), "structure_id": 1178, "z": int(r.Z),
                       "x": r.VolumeX * sx, "y": r.VolumeY * sy} for r in nodes.itertuples()]
    fake.fit = {2: (120.0, -80.0)}

    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    sc = detail.summary.section_correction
    assert sc.applied and sc.mode == "curvefit"
    assert sc.n_found == sc.n_trusted == sc.n_moved == len(nodes)
    fitted = svc.neuron(1178, "i")
    # Another mode is another load, into a copy of its own; switching back is
    # answered from the copy already held (one copy per setting in use, so a
    # tab that switches never changes another tab's cell), as is the same mode again.
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    assert svc.neuron(1178, "i").section_correction["mode"] == "neighbor"
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    again = svc.neuron(1178, "i")
    assert again is fitted and again.section_correction["mode"] == "curvefit"
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    assert svc.neuron(1178, "i") is again


def test_a_lab_can_have_its_own_default_mode(web):
    # The web app: Field + fit for the Neitz lab's volumes (the user's decision
    # of 2026-10-20), Curve fit for the others.
    fake, svc = web
    svc.correction_mode = "curvefit"
    svc.lab_corrections = {"Neitz": "all"}
    assert svc.default_correction("i") == "all"
    assert svc.default_correction("RC2") == "curvefit"
    assert svc.default_correction("demo") == "curvefit"  # no lab
    assert svc.volume_info("NeitzCped").default_correction == "all"
    assert svc.volume_info("RPC1").lab == "Marc"
    assert svc.volume_info("RPC1").default_correction == "curvefit"

    plain = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    nodes = svc.neuron(1178, "i").nodes
    sx, sy = map(float, svc.neuron(1178, "i").volume_scale[:2])
    fake.locations = [{"location_id": int(r.ID), "structure_id": 1178, "z": int(r.Z),
                       "x": r.VolumeX * sx, "y": r.VolumeY * sy} for r in nodes.itertuples()]
    fake.fit = {2: (120.0, -80.0), 3: (60.0, 40.0)}
    assert plain.summary.section_correction is None

    # A load that names no mode gets the lab's; one that names a mode, that one.
    detail = svc.load_neuron(1178, "i", include_synapses=False)
    assert detail.summary.section_correction.mode == "all"
    assert detail.summary.copy_tag == "all.0"
    named = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    assert named.summary.section_correction.mode == "curvefit"


def test_a_failure_in_the_service_holds_that_cell_and_mode(web):
    fake, svc = web
    fake.fail_structures = True
    svc.corrections().volume_retry_s = 0.3
    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    sc = detail.summary.section_correction
    assert sc.requested and not sc.applied and sc.mode == "curvefit"
    assert "Exception was thrown by handler" in sc.note
    first = svc.neuron(1178, "i")
    assert "CorrDXnm" not in first.nodes.columns
    # Held: the same load again is answered at once, without asking the service.
    fake.fail_structures = False
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    assert svc.neuron(1178, "i") is first and len(fake.structure_requests) == 1
    provider = svc.corrections()
    assert provider.paused_for("InferiorMonkey", "curvefit", 1178)
    assert not provider.paused_for("InferiorMonkey", "curvefit"), "only that cell is held"
    # Another mode is not held.
    other = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    assert other.summary.section_correction.applied
    # Once the hold is over, the next load asks again.
    time.sleep(0.35)
    again = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    assert again.summary.section_correction.applied
    assert len(fake.structure_requests) == 2


def test_a_cell_the_service_throws_on_leaves_the_rest_of_its_volume_corrected(web):
    """Cped c1 on 2026-10-20: one structure whose ``all`` request threw held
    Field + fit for all of Cped for ten minutes, and the cells loaded after it
    were drawn uncorrected though the service answered for them."""
    fake, svc = web
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    loaded = svc.neuron(1178, "i")
    sx, sy = map(float, loaded.volume_scale[:2])
    fake.locations = [{"location_id": int(r.ID), "structure_id": 1178, "z": int(r.Z),
                       "x": r.VolumeX * sx, "y": r.VolumeY * sy} for r in loaded.nodes.itertuples()]
    fake.fit = {3: (60.0, 40.0)}
    fake.fail_structures = {9}
    provider = svc.corrections()
    with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler") as failed:
        provider.correct(make_structure_neuron(), "all")  # c9 of the same volume
    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="all")
    sc = detail.summary.section_correction
    assert sc.applied and sc.mode == "all" and sc.n_moved == len(loaded.nodes), sc.note
    assert [r["structure_ids"] for r in fake.structure_requests] == [[9], [1178]]
    assert failed.value.code == "UNKNOWN"
    assert provider.paused_for("InferiorMonkey", "all", 9)
    assert not provider.paused_for("InferiorMonkey", "all")
    # c9 itself stays held: asked again, it is not sent.
    with pytest.raises(corr.CorrectionsError,
                       match="not asked again for c9 of InferiorMonkey until .* UTC"):
        provider.correct(make_structure_neuron(), "all")
    assert len(fake.structure_requests) == 2


def test_a_correction_the_service_could_not_make_is_keyed_as_the_stored_positions(web):
    """The Curve fit outage of 2026-10-16 (07:41 to 18:40 UTC): c1178 asked for as
    ``curvefit``, nothing applied, held as that copy all the same.  A surface built
    from it is filed under the stored positions' disk key (the key an ``off`` copy
    reads, which holds the same positions), never under a corrected one; the corrected
    load of the same copy that follows is keyed apart, and the summary says which
    moves each copy was given (``positions``), which the browser keys its own cache on."""
    from sbfsem.geometry.mesh import Mesh

    fake, svc = web
    built = []

    def build(neuron, *args):
        built.append(int(neuron.nodes["Xum"].sum() * 1000))
        return Mesh(np.eye(3), [[0, 1, 2]])

    svc._run_build = build  # type: ignore[method-assign]
    puts = []
    real_put = svc._disk_meshes.put
    svc._disk_meshes.put = lambda key, mesh: (puts.append(key), real_put(key, mesh))
    quick = {"method": "cylinder", "pathway": "original", "smooth_iterations": 0}

    fake.fail_structures = True
    svc.corrections().volume_retry_s = 0.3
    asked = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    assert asked.summary.copy_tag == "curvefit.0" and not asked.summary.section_correction.applied
    assert asked.summary.positions is None
    svc.mesh_object(1178, "i", **quick)
    (uncorrected,) = puts
    assert not any("section-correction" in str(part) for part in uncorrected)

    # The off copy holds the same positions: it reads that surface instead of building.
    plain = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    assert plain.summary.positions is None
    svc.mesh_object(1178, "i", **quick)
    assert len(built) == 1 and len(puts) == 1

    # The service answers again: the same copy, corrected, is another surface.
    loaded = svc.neuron(1178, "i")
    sx, sy = map(float, loaded.volume_scale[:2])
    fake.locations = [{"location_id": int(r.ID), "structure_id": 1178, "z": int(r.Z),
                       "x": r.VolumeX * sx, "y": r.VolumeY * sy} for r in loaded.nodes.itertuples()]
    fake.fit = {2: (120.0, -80.0)}
    fake.fail_structures = False
    time.sleep(0.35)
    done = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    assert done.summary.copy_tag == "curvefit.0" and done.summary.section_correction.applied
    token = svc.neuron(1178, "i").section_correction["token"]
    assert done.summary.positions == f"section-correction:{token}"
    svc.mesh_object(1178, "i", **quick)
    assert len(built) == 2 and built[0] != built[1], "built again, from the moved annotations"
    corrected = puts[-1]
    assert corrected[:-1] == uncorrected and corrected[-1] == f"section-correction:{token}"


def structure_neuron(sid: int, source: str = "i") -> Neuron:
    neuron = make_structure_neuron(source)
    neuron.id = sid
    return neuron


def test_provider_holds_a_structure_then_its_volume_after_failures_in_the_service(structures):
    fake, url = structures
    fake.fail_structures = True
    provider = corr.CorrectionsProvider(url, volume_retry_s=60)
    try:
        with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
            provider.correct(structure_neuron(9), "curvefit")
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for c9 of InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(9), "curvefit")
        assert len(fake.structure_requests) == 1
        assert provider.paused_for("InferiorMonkey", "curvefit", 9)
        assert not provider.paused_for("InferiorMonkey", "curvefit")
        # A second structure throws, with no answer between: the volume is held
        # (RC1 on 2026-09-24, Cped while its volume server refused on 2026-10-20).
        with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
            provider.correct(structure_neuron(10), "curvefit")
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(11), "curvefit")
        assert len(fake.structure_requests) == 2
        assert provider.paused_for("InferiorMonkey", "curvefit")
        assert provider.paused_for("InferiorMonkey", "curvefit", 11)
        assert not provider.paused_for("InferiorMonkey", "neighbor")
        assert not provider.paused  # the host answered: nothing else waits
        # The residual field for the same volume is still asked for.
        assert provider.correct(make_structure_neuron(), "neighbor") is not None
    finally:
        provider.close()


def test_an_answer_between_two_failures_keeps_the_volume_asked(structures):
    fake, url = structures
    fake.fail_structures = {9, 10, 12}
    provider = corr.CorrectionsProvider(url, volume_retry_s=0.3)
    try:
        with pytest.raises(corr.CorrectionsError):
            provider.correct(structure_neuron(9), "all")
        assert provider.correct(structure_neuron(11), "all") is not None
        with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
            provider.correct(structure_neuron(10), "all")
        assert not provider.paused_for("InferiorMonkey", "all")
        # Nor does a failure count once its hold is over.
        time.sleep(0.35)
        with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
            provider.correct(structure_neuron(12), "all")
        assert not provider.paused_for("InferiorMonkey", "all")
        assert provider.correct(structure_neuron(13), "all") is not None
        assert [r["structure_ids"] for r in fake.structure_requests] == [
            [9], [11], [10], [12], [13]]
    finally:
        provider.close()


def test_an_error_about_more_than_the_request_holds_the_volume_at_once(structures):
    fake, url = structures
    fake.fail_structures = {9}
    fake.fail_code = "PERMISSION_DENIED"
    provider = corr.CorrectionsProvider(url, volume_retry_s=60)
    try:
        with pytest.raises(corr.CorrectionsError) as failed:
            provider.correct(structure_neuron(9), "curvefit")
        assert failed.value.code == "PERMISSION_DENIED"
        assert provider.paused_for("InferiorMonkey", "curvefit")
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(10), "curvefit")
        assert len(fake.structure_requests) == 1
    finally:
        provider.close()


#: What Viking's service answered for Cped at 17:31 UTC on 2026-10-20, with
#: Inferior's name and volume server (whose own outage it reported at 17:29).
MAPPING_DOWN = (
    "UNAVAILABLE",
    "volume mapping temporarily unavailable for 'InferiorMonkey': Error connecting to volume "
    "server after 4 attempts: http://v2486.host.s.uw.edu/Neitz/InferiorMonkey/InferiorMonkey.xml "
    "Connection refused")


def test_an_unavailable_volume_holds_that_volume_and_leaves_the_others_asked(structures):
    """2026-10-20 17:31 UTC: the service could not reach Cped's volume server and
    said so, naming Cped, while RC1 answered 8 of 8 calls; the whole service was
    paused for every volume all the same."""
    fake, url = structures
    fake.volumes = ["InferiorMonkey", "RC1"]
    fake.volume_errors["InferiorMonkey"] = MAPPING_DOWN
    provider = corr.CorrectionsProvider(url, retry_after_s=60)
    try:
        with pytest.raises(corr.CorrectionsUnavailable,
                           match="volume mapping temporarily unavailable") as failed:
            provider.correct(structure_neuron(9), "all")
        assert failed.value.code == "UNAVAILABLE"
        # RC1, not asked for before, is asked and corrected.
        rc1 = structure_neuron(476, "RC1")
        assert provider.correct(rc1, "all") is not None
        assert rc1.section_correction["n_moved"] == 3
        assert not provider.paused and not provider.paused_for("RC1", "all")
        # Inferior is held in both modes CorrectStructures answers, for retry_after_s.
        assert provider.paused_for("InferiorMonkey", "all")
        assert provider.paused_for("InferiorMonkey", "curvefit", 10)
        assert not provider.paused_for("InferiorMonkey", "neighbor")
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(10), "curvefit")
        assert [(r["volume_name"], r["structure_ids"]) for r in fake.structure_requests] == [
            ("InferiorMonkey", [9]), ("RC1", [476])]
    finally:
        provider.close()


@pytest.mark.parametrize("details", [
    "no healthy upstream",  # what a proxy in front of the service says
    "volume mapping temporarily unavailable for 'RC1': Connection refused",  # not the one asked
])
def test_an_unavailable_naming_no_volume_asked_pauses_the_whole_service(structures, details):
    fake, url = structures
    fake.volumes = ["InferiorMonkey", "RC1"]
    fake.volume_errors["InferiorMonkey"] = ("UNAVAILABLE", details)
    provider = corr.CorrectionsProvider(url, retry_after_s=60)
    try:
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(structure_neuron(9), "all")
        assert provider.paused and provider.paused_for("RC1", "all")
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(structure_neuron(476, "RC1"), "all")
        assert len(fake.structure_requests) == 1
    finally:
        provider.close()


@pytest.mark.parametrize("outage", ["no healthy upstream", "unreachable"])
def test_a_service_wide_pause_asks_no_volume_whose_sets_are_fresh(
        structures, tmp_path, monkeypatch, outage):
    """Until 2.17.5 a volume whose sets were asked for within ``sets_ttl_s`` was
    asked all the same during the pause: RC1 c476 was (measured on 2.17.2), and
    against a host that drops traffic each such load waited out its deadline
    (120 s for ``CorrectStructures``) and moved the end of the pause later."""
    fake, url = structures
    fake.volumes = ["InferiorMonkey", "RC1"]
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path), timeout=3.0,
                                        retry_after_s=60)
    try:
        # RC1's sets asked for just now, and c476's offsets on disk.
        assert provider.correct(structure_neuron(476, "RC1"), "all") is not None
        if outage == "unreachable":
            fake.server.stop(None)  # gRPC's own UNAVAILABLE: "failed to connect ..."
        else:
            fake.volume_errors["InferiorMonkey"] = ("UNAVAILABLE", outage)
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(structure_neuron(9), "all")
        assert provider.paused
        down_until = provider._down_until
        sent = []
        call = provider.client.call
        monkeypatch.setattr(provider.client, "call",
                            lambda method, *a, **k: sent.append(method) or call(method, *a, **k))
        # c476's offsets on disk are still applied, and its status says why they are old.
        neuron = structure_neuron(476, "RC1")
        kept = provider.correct(neuron, "all")
        assert kept.cached and neuron.section_correction["n_moved"] == 3
        assert neuron.section_correction["service_note"] == provider.last_error != ""
        # Nothing on disk: refused without asking, in either call.
        with pytest.raises(corr.CorrectionsUnavailable, match="no all offsets on disk"):
            provider.correct(structure_neuron(477, "RC1"), "all")
        with pytest.raises(corr.CorrectionsUnavailable, match="no offsets on disk"):
            provider.correct(structure_neuron(476, "RC1"), "neighbor")
        assert sent == []
        assert provider._down_until == down_until  # a refusal does not lengthen the pause
        assert [r["volume_name"] for r in fake.structure_requests] == (
            ["RC1"] if outage == "unreachable" else ["RC1", "InferiorMonkey"])
    finally:
        provider.close()


def test_a_volume_held_unavailable_keeps_its_offsets_on_disk_and_is_asked_again(
        structures, tmp_path):
    fake, url = structures
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path), retry_after_s=0.3,
                                        volume_retry_s=60)
    try:
        assert provider.correct(structure_neuron(9), "all") is not None  # now on disk
        fake.fail_structures = {7, 8}
        for sid in (7, 8):  # two structures throw in a row: curvefit held for volume_retry_s
            with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
                provider.correct(structure_neuron(sid), "curvefit")
        fake.volume_errors["InferiorMonkey"] = MAPPING_DOWN
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(structure_neuron(10), "all")
        # c9's offsets are on disk: still applied, and the status says why they are old.
        neuron = structure_neuron(9)
        kept = provider.correct(neuron, "all")
        assert kept.cached and neuron.section_correction["n_moved"] == 3
        note = neuron.section_correction["service_note"]
        assert "volume mapping temporarily unavailable for 'InferiorMonkey'" in note
        assert "not asked again for InferiorMonkey until" in note
        # The longer hold of curvefit is not cut short by the shorter one.
        assert "Exception was thrown by handler" in provider.held("InferiorMonkey", "curvefit")
        n = len(fake.structure_requests)
        fake.volume_errors.clear()
        time.sleep(0.35)
        assert not provider.paused_for("InferiorMonkey", "all")
        assert provider.correct(structure_neuron(10), "all") is not None
        assert provider.paused_for("InferiorMonkey", "curvefit")
        assert len(fake.structure_requests) == n + 1
    finally:
        provider.close()


def test_a_load_while_its_volume_is_unavailable_is_held_and_other_modes_are_asked(web):
    fake, svc = web
    fake.volume_errors["InferiorMonkey"] = MAPPING_DOWN
    svc.corrections().retry_after_s = 0.3
    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="all")
    sc = detail.summary.section_correction
    assert sc.requested and not sc.applied
    assert "volume mapping temporarily unavailable for 'InferiorMonkey'" in sc.note
    first = svc.neuron(1178, "i")
    # Held: the same load again is answered at once, from the copy it has.
    fake.volume_errors.clear()
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="all")
    assert svc.neuron(1178, "i") is first and len(fake.structure_requests) == 1
    provider = svc.corrections()
    assert not provider.paused and provider.paused_for("InferiorMonkey", "curvefit", 1178)
    # CorrectPoints maps nothing there: the residual field is still asked for.
    other = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    assert other.summary.section_correction.applied
    time.sleep(0.35)
    again = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="all")
    assert again.summary.section_correction.applied
    assert len(fake.structure_requests) == 2


def test_names_volume_reads_only_the_services_own_unavailable():
    def error(code, details):
        exc = corr.CorrectionsUnavailable(f"CorrectStructures at x: {code} {details}")
        exc.code, exc.details = code, details
        return exc

    said = error("UNAVAILABLE", "volume mapping temporarily unavailable for 'cped': refused")
    assert corr.names_volume(said, "cped")
    assert corr.names_volume(said, "CPED", "")  # Viking's names are not case-sensitive
    assert not corr.names_volume(said, "RC1")
    assert not corr.names_volume(said, "")
    # What gRPC itself says when the service cannot be reached (measured 2026-10-20).
    assert not corr.names_volume(error("UNAVAILABLE", "failed to connect to all addresses; last "
                                       "error: UNKNOWN: ipv4:127.0.0.1:9: Connection refused"),
                                 "cped")
    assert not corr.names_volume(error("DEADLINE_EXCEEDED", "for 'cped'"), "cped")


def test_shift_um_reads_rows_and_series():
    from sbfsem.service.facade import _shift_um

    frame = pd.DataFrame({"ID": [1, 2, 3], "CorrDXnm": [120.0, 0.0, np.nan],
                          "CorrDYnm": [-80.0, 0.0, np.nan]})
    rows = list(frame.itertuples())
    assert _shift_um(rows[0]) == [0.12, -0.08]
    assert _shift_um(rows[1]) is None and _shift_um(rows[2]) is None
    by_id = frame.set_index("ID")
    assert _shift_um(by_id.loc[1]) == [0.12, -0.08]
    assert _shift_um(pd.Series({"Xum": 1.0})) is None


def test_shift_um_reads_the_live_columns_by_prefix():
    from sbfsem.service.facade import _shift_um

    frame = pd.DataFrame({"ID": [1, 2, 3, 4],
                          "LiveDXnm": [750.0, 0.0, np.nan, np.inf],
                          "LiveDYnm": [-750.0, 0.0, np.nan, 5.0],
                          "CorrDXnm": [120.0, -30.0, 0.0, 0.0],
                          "CorrDYnm": [-80.0, 10.0, 0.0, 0.0]})
    rows = list(frame.itertuples())
    # The prefix picks the columns; each stage answers for itself.
    assert _shift_um(rows[0], "Live") == [0.75, -0.75]
    assert _shift_um(rows[0]) == [0.12, -0.08]
    assert _shift_um(rows[0], "Corr") == _shift_um(rows[0])
    # A zero, a NaN and a non-finite value are no move; so is a missing column.
    assert [_shift_um(r, "Live") for r in rows[1:]] == [None, None, None]
    assert _shift_um(rows[1]) == [-0.03, 0.01]
    by_id = frame.set_index("ID")
    assert _shift_um(by_id.loc[1], "Live") == [0.75, -0.75]
    assert _shift_um(pd.Series({"CorrDXnm": 5.0, "CorrDYnm": 5.0}), "Live") is None
    assert _shift_um(pd.Series({"Xum": 1.0}), "Live") is None


def test_shifts_um_answers_for_every_row_what_shift_um_answers_for_one():
    from sbfsem.service.facade import _shift_um, _shifts_um

    values = [(120.0, -80.0), (0.0, 0.0), (np.nan, 5.0), (5.0, np.nan), (np.inf, 1.0),
              (0.0, 0.4), (-3.0, 0.0), (None, None), (1e-9, 0.0)]
    frame = pd.DataFrame({"ID": range(len(values)),
                          "CorrDXnm": [v[0] for v in values], "CorrDYnm": [v[1] for v in values],
                          "LiveDXnm": [v[1] for v in values], "LiveDYnm": [v[0] for v in values]})
    for prefix in ("Corr", "Live"):
        assert _shifts_um(frame, prefix) == [_shift_um(r, prefix) for r in frame.itertuples()]
    assert _shifts_um(frame)[0] == [0.12, -0.08]
    # A column that is not there, an empty frame, and a frame where nothing moved.
    # Half a pair is zeros for the other half, as the row-by-row call reads it; no pair is no move.
    half = frame.drop(columns=["LiveDYnm"])
    assert _shifts_um(half, "Live") == [_shift_um(r, "Live") for r in half.itertuples()]
    assert any(s is not None for s in _shifts_um(half, "Live"))
    assert _shifts_um(frame.drop(columns=["LiveDXnm", "LiveDYnm"]), "Live") == [None] * len(values)
    assert _shifts_um(frame.iloc[:0]) == []
    assert _shifts_um(frame.iloc[[1, 2]]) == [None, None]
