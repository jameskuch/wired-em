"""Viking's section corrections: the wire codec, the client, and applying them.

The golden bytes below were produced by protoc (libprotoc 29.3, ``--encode``)
from ``section_correction_v2.proto`` and ``Common/geometry.proto``
(github.com/jamesra/viking_gRPC_protos master, 2026-10-26), so the
hand-written codec is held to what protobuf's own serializer writes and reads.
The end-to-end tests run a local gRPC server that answers the way
``CorrectionsService`` does over protocol V2 (``CorrectPoints`` adds the
sampled offset and answers the sections in request order; a section with no
map comes back not found and untrusted; ``CorrectStructures`` answers mode 0,
the field, and 1, none, and refuses any other number with INVALID_ARGUMENT, as
service 1.5.0 did on 2026-10-27).  The NONE and NEIGHBOR request bytes were
encoded by protoc from a proto with V2's ``CorrectStructuresRequest`` fields
and ``CorrectionMode`` numbers (2026-10-27).
"""

from __future__ import annotations

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
    "correct_points_request": "0a180a0e496e666572696f724d6f6e6b65791206477269643136122208a8071212090000000060e336411100000000882a41411209110000000000000cc0121708a50a12120977be9f1a2fdd5e40110000000000a88840",
    "correct_points_response": "0a1c080112160a120900000000000029401100000000000024c0100112000a0412020a000a00",
    "list_sets_response": "0a500a180a0e496e666572696f724d6f6e6b65791206477269643136120c08b882c9d5061080cab5ee01190000000000409f4021000000000040bf402a140102a50a808080808020fbffffffffffffffff010a220a200a0e496e666572696f724d6f6e6b6579120e536c696365546f566f6c756d6531",
    "section_correction": "090000000000409f4012251a12090000000000c862c011000000000080404020042a0d080210fdffffffffffffffff01121c1a0911000000000000f03f200c2a0d08ffffffffffffffffff011007",
    "rebuild_status": "0801120608e09ed7d5062204626f6f6d2a05312e342e30320e496e666572696f724d6f6e6b65793a0766697474696e6740034808",
    "section_request_negative_z": "0a180a0e496e666572696f724d6f6e6b6579120647726964313610ffffffffffffffffff01",
    "list_volumes_response": "0a500a0e496e666572696f724d6f6e6b6579122268747470733a2f2f6578616d706c652f696e666572696f722f56696b696e67584d4c1a1a68747470733a2f2f6578616d706c652f616e6e6f746174696f6e",
    "correct_structures_request_none": "0a200a0e496e666572696f724d6f6e6b6579120e536c696365546f566f6c756d6531120488aa010518012001",
    "correct_structures_request_neighbor": "0a200a0e496e666572696f724d6f6e6b6579120e536c696365546f566f6c756d6531120488aa01051801",
    "correct_structures_response": "0a33089bd50f1088aa0118940622120900000000e40a00411100000000488a04412a120900000000000029401100000000000024c00a02080512320a05312e342e30120608b882c9d5061a19313a353532353a36333932383233343237323432333030303022060880f7c4d506",
    "answer_stamp": "0a05312e342e301a19313a353532353a363339323832333432373234323330303030220c0880f7c4d50610c0efd9c901",
}


def golden(name: str) -> bytes:
    return bytes.fromhex(GOLDEN[name])


# -- the codec, against protobuf's own bytes ---------------------------------
def test_the_client_speaks_protocol_v2():
    # V1 answers every call UNIMPLEMENTED since 2026-10-26 15:13 UTC (service 1.4.0).
    assert corr.SERVICE == "Viking.SectionCorrectionServiceTypes.gRPC.V2.AnnotateSectionCorrections"
    # V2's CorrectionMode since service 1.5.0 (2026-10-27): NEIGHBOR 0, the default, and
    # NONE 1; 2 and 3 are reserved, and 1.5.2 gives raw 2, 3 and 4 to a curve fit experiment.
    assert corr.CORRECTION_MODES == {"neighbor": 0, "none": 1}


def test_encode_matches_protobuf_for_a_correct_points_request():
    request = {
        "set": {"volume_name": "InferiorMonkey", "stos_group": "Grid16"},
        "sections": [
            {"z": 936, "points": [{"x": 1.5e6, "y": 2.25e6}, {"x": 0.0, "y": -3.5}]},
            {"z": 1317, "points": [{"x": 123.456, "y": 789.0}]},
        ],
    }
    assert corr.encode(request, corr.CORRECT_POINTS_REQUEST_SCHEMA) == golden(
        "correct_points_request")


def test_encode_negative_int64_is_ten_bytes_like_protobuf():
    request = {"set": {"volume_name": "InferiorMonkey", "stos_group": "Grid16"}, "z": -1}
    assert corr.encode(request, corr.SECTION_REQUEST_SCHEMA) == golden(
        "section_request_negative_z")


def test_encode_refuses_a_field_the_schema_does_not_have():
    with pytest.raises(KeyError):
        corr.encode({"volume": "InferiorMonkey"}, corr.LIST_SETS_REQUEST_SCHEMA)
    with pytest.raises(KeyError):  # V1's flat names, inside the set
        corr.encode({"set": {"volume_name": "RC1", "z": 3}}, corr.SECTION_REQUEST_SCHEMA)


def test_decode_correct_points_response():
    answer = corr.decode(golden("correct_points_response"), corr.CORRECT_POINTS_RESPONSE_SCHEMA)
    first, second, third = answer["sections"]
    assert first["found"] is True
    assert first["points"][0] == {"offset": {"x": 12.5, "y": -10.0}, "trusted": True}
    # A point at the defaults is sent with no fields at all, its offset unset.
    assert first["points"][1] == {"offset": None, "trusted": False}
    assert second == {"found": False, "points": [{"offset": {"x": 0.0, "y": 0.0}, "trusted": False}]}
    assert third == {"found": False, "points": []}


def test_decode_correction_sets_with_their_sections():
    answer = corr.decode(golden("list_sets_response"), corr.LIST_SETS_RESPONSE_SCHEMA)
    cset, bare = [corr.CorrectionSet.from_message(m) for m in answer["sets"]]
    assert cset.volume_name == "InferiorMonkey"
    assert cset.stos_group == "Grid16"
    assert cset.built_utc == datetime.fromtimestamp(1790067000.5, tz=UTC)
    assert (cset.pitch_nm, cset.kernel_radius_nm) == (2000.0, 8000.0)
    # Packed int64, a negative one ten bytes long.
    assert cset.sections == (1, 2, 1317, 2**40, -5)
    assert (bare.stos_group, bare.built_utc, bare.sections) == ("SliceToVolume1", None, ())
    # The sections stay out of the description (the disk cache, a cell's status).
    assert "sections" not in cset.describe()
    assert corr.CorrectionSet.from_description(cset.describe()) == cset


def test_decode_section_lattice_with_negative_indices():
    answer = corr.decode(golden("section_correction"), corr.SECTION_CORRECTION_SCHEMA)
    assert answer["pitch_nm"] == 2000.0
    assert answer["nodes"] == [
        {"node": {"row": 2, "col": -3}, "offset": {"x": -150.25, "y": 33.0}, "votes": 4},
        {"node": {"row": -1, "col": 7}, "offset": {"x": 0.0, "y": 1.0}, "votes": 12},
    ]


def test_decode_rebuild_status_and_volumes():
    status = corr.decode(golden("rebuild_status"), corr.REBUILD_STATUS_SCHEMA)
    assert status["in_progress"] is True
    assert status["last_started_utc"] == {"seconds": 1790300000, "nanos": 0}
    assert status["last_completed_utc"] is None
    assert status["last_error"] == "boom"
    assert status["service_version"] == "1.4.0"
    assert (status["current_volume"], status["current_step"]) == ("InferiorMonkey", "fitting")
    assert (status["groups_done"], status["groups_total"]) == (3, 8)
    volumes = corr.decode(golden("list_volumes_response"), corr.LIST_VOLUMES_RESPONSE_SCHEMA)
    assert volumes["volumes"][0]["vikingxml_endpoint"] == "https://example/inferior/VikingXML"


#: A schema with every kind the codec writes, for the round trip.
ROUND_TRIP: corr.Schema = {
    1: ("name", "string", False), 2: ("tags", "string", True), 3: ("z", "int64", True),
    4: ("at", corr._VECTOR2, False), 5: ("on", "bool", False), 6: ("n", "int32", False),
}


def test_round_trip_and_unknown_fields_are_skipped():
    message = {"name": "RPC1", "tags": ["a", "b"], "z": [3, -4, 5], "at": {"x": -0.5, "y": 2.0},
               "on": True, "n": -7}
    data = corr.encode(message, ROUND_TRIP)
    assert corr.decode(data, ROUND_TRIP) == message
    # A field this reader does not know (number 9, a string) is passed over.
    extra = data + bytes([9 << 3 | 2, 3]) + b"new"
    assert corr.decode(extra, ROUND_TRIP) == message
    with pytest.raises(ValueError):
        corr.decode(data[:-1], ROUND_TRIP)


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
    """Answers like ``CorrectionsService`` over V2: one set, a field on some sections.

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
        # z, x, y), the (dx, dy) each mode (by name) adds to every one of them,
        # the requests it was sent, and whether it throws as the live one did:
        # for every request (True), or for the structures in a set, with
        # ``fail_code``.
        self.locations: list[dict] = []
        self.fit: dict[str, tuple[float, float]] = {}
        # A Location's own (dx, dy) nm, in place of its mode's fit (the curve
        # fit's pull toward a soma, for the correction log).
        self.moves: dict[int, tuple[float, float]] = {}
        self.structure_requests: list[dict] = []
        self.stamp_requests: list[dict] = []
        # Every CorrectionMode number the client sent, refused or not.
        self.modes_sent: list[int] = []
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
        # The published set's build time, and what the AnswerStamp of
        # CorrectStructures and GetAnswerStamp fingerprints the structures'
        # annotations as (a delete moves it); the (status, details) a
        # GetAnswerStamp answers with while it cannot give one.
        self.built = 1790067000
        self.fingerprint = "f1"
        self.stamp_error: tuple[str, str] | None = None

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
                "id": {"volume_name": name, "stos_group": "Grid16"},
                "built_utc": {"seconds": self.built}, "pitch_nm": 2000.0,
                "kernel_radius_nm": 8000.0,
                "z": sorted(z for z in self.field if z != "*"),
            } for name in self.volumes if asked in ("", name)]}

        def volume_down(request, context):
            failing = self.volume_errors.get(request["set"]["volume_name"])
            if failing:
                context.abort(getattr(grpc.StatusCode, failing[0]), failing[1])

        def field_at(z, x, y):
            """CorrectPoints' rule for one point: (dx, dy, trusted, found)."""
            entry = self.field.get(z, self.field.get("*"))
            if entry is None:
                return 0.0, 0.0, False, False
            dx, dy, radius = entry
            near = bool(np.hypot(x, y) <= radius)
            return (dx, dy, near, True) if near else (0.0, 0.0, False, True)

        def mode_of(request, context):
            # As service 1.5.0 answered it: 0 (left out of the request) the field,
            # 1 none, any other number refused.
            number = request["correction"]
            self.modes_sent.append(number)
            names = {n: name for name, n in corr.CORRECTION_MODES.items()}
            if number not in names:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                              f"unknown correction mode {number}")
            return names[number]

        def correct(request, context):
            volume_down(request, context)
            if request["set"]["stos_group"] != "Grid16":
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
                        points.append({})  # zero and untrusted, every field left out
                        continue
                    dx, dy, radius = entry
                    near = np.hypot(p["x"], p["y"]) <= radius
                    ox, oy = (dx, dy) if near else (0.0, 0.0)
                    points.append({"offset": {"x": ox, "y": oy}, "trusted": bool(near)})
                # In request order, without the section's Z.
                out.append({"found": entry is not None, "points": points})
            return {"sections": out}

        def stamp(mode):
            # What an answer to this request depends on: the field only in the
            # mode that samples it (V2's AnswerStamp).
            return {"service_version": self.version,
                    "field_built_utc": ({"seconds": self.built} if mode == "neighbor" else None),
                    "annotation_fingerprint": self.fingerprint,
                    "annotation_watermark": {"seconds": 1790000000}}

        def structures(request, context):
            self.structure_requests.append(request)
            volume_down(request, context)
            failing = self.fail_structures
            if failing is True or (failing and set(request["structure_ids"]) & failing):
                context.abort(getattr(grpc.StatusCode, self.fail_code),
                              "Exception was thrown by handler.")
            if request["set"]["stos_group"] != "Grid16":
                context.abort(grpc.StatusCode.NOT_FOUND, "no correction set")
            # 0, left out of the request, is NEIGHBOR: the field where the service
            # places each Location, unless the test gave the mode one offset for all.
            mode = mode_of(request, context)
            wanted = set(request["structure_ids"])
            out = []
            for loc in self.locations:
                if loc["structure_id"] in wanted or request["include_children"]:
                    if loc["location_id"] in self.moves:
                        dx, dy = self.moves[loc["location_id"]]
                    elif mode in self.fit:
                        dx, dy = self.fit[mode]
                    elif mode == "neighbor":
                        dx, dy = field_at(loc["z"], loc["x"], loc["y"])[:2]
                    else:
                        dx, dy = 0.0, 0.0
                    out.append({**{k: loc[k] for k in ("location_id", "structure_id", "z")},
                                "position": {"x": loc["x"] + dx, "y": loc["y"] + dy},
                                "offset": {"x": dx, "y": dy}})
            return {"locations": out, "stamp": stamp(mode)}

        def rebuild_status(request, context):
            return {"in_progress": False, "last_completed_utc": {"seconds": self.rebuilt},
                    "service_version": self.version}

        def answer_stamp(request, context):
            self.stamp_requests.append(request)
            if self.stamp_error:
                context.abort(getattr(grpc.StatusCode, self.stamp_error[0]), self.stamp_error[1])
            return stamp(mode_of(request, context))

        return grpc.method_handlers_generic_handler(corr.SERVICE, {
            "GetRebuildStatus": unary("GetRebuildStatus", corr.EMPTY_SCHEMA,
                                      corr.REBUILD_STATUS_SCHEMA, rebuild_status),
            "ListCorrectionSets": unary("ListCorrectionSets", corr.LIST_SETS_REQUEST_SCHEMA,
                                        corr.LIST_SETS_RESPONSE_SCHEMA, sets),
            "CorrectPoints": unary("CorrectPoints", corr.CORRECT_POINTS_REQUEST_SCHEMA,
                                   corr.CORRECT_POINTS_RESPONSE_SCHEMA, correct),
            "CorrectStructures": unary("CorrectStructures", corr.CORRECT_STRUCTURES_REQUEST_SCHEMA,
                                       corr.CORRECT_STRUCTURES_RESPONSE_SCHEMA, structures),
            "GetAnswerStamp": unary("GetAnswerStamp", corr.CORRECT_STRUCTURES_REQUEST_SCHEMA,
                                    corr.ANSWER_STAMP_SCHEMA, answer_stamp),
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
    # c9's Locations where make_neuron has them, as the service maps them.
    fake.locations = locations_of(make_neuron())
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    server.add_generic_rpc_handlers((fake.handler(),))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    fake.server = server  # a test may take the service away
    try:
        yield fake, f"http://127.0.0.1:{port}"
    finally:
        server.stop(None)


def locations_of(neuron, structure_id: int | None = None) -> list[dict]:
    """``neuron``'s annotations as the fake's ``CorrectStructures`` knows them: at
    their stored positions in volume nm (no live registration in these tests)."""
    sx, sy = map(float, neuron.volume_scale[:2])
    sid = int(neuron.id if structure_id is None else structure_id)
    return [{"location_id": int(r.ID), "structure_id": sid, "z": int(r.Z),
             "x": float(r.VolumeX) * sx, "y": float(r.VolumeY) * sy}
            for r in neuron.nodes.drop_duplicates("ID").itertuples()]


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
    # The service corrects every Location of the structure, by nothing where the
    # field has no support; each is placed where the cell has it.
    assert summary["n_found"] == summary["n_trusted"] == 5
    assert summary["n_moved"] == 1
    assert summary["max_um"] == pytest.approx(np.hypot(0.12, 0.08))
    assert summary["mode"] == "neighbor"
    # The field is asked of CorrectStructures, with no mode on the wire: 0, NEIGHBOR.
    assert fake.calls == ["ListCorrectionSets", "CorrectStructures"]
    assert fake.modes_sent == [0]


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
    assert fake.calls.count("CorrectStructures") == 1
    assert fake.calls.count("GetAnswerStamp") == 1  # the answer on disk, checked
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
    assert fake.calls.count("CorrectStructures") == 1


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

    from sbfsem.core.volumes import Volumes
    from sbfsem.io.loader import NeuronLoader
    from sbfsem.io.odata import ODataClient
    from sbfsem.service.facade import SbfsemService

    fake, url = service
    fake.field.clear()
    fake.field["*"] = (120.0, -80.0, 1e12)
    cassettes = Path(__file__).resolve().parents[1] / "cassettes"
    odata = ODataClient("i", cache=DiskCache(cassettes), offline=True)
    # c1178 and its child structures, as the service's CorrectStructures finds them.
    fake.locations = locations_of(
        NeuronLoader(Volumes.from_str("i"), client=odata).load(1178, include_synapses=False))
    svc = SbfsemService(cache_dir=tmp_path, section_corrections=True, corrections_url=url)
    svc._clients["NeitzInferiorMonkey"] = odata
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


# -- CorrectStructures and the modes (2026-09-24; protocol V2 2026-10-26; field only 2026-10-27)
def test_encode_correct_structures_like_protobuf():
    request = {"set": {"volume_name": "InferiorMonkey", "stos_group": "SliceToVolume1"},
               "structure_ids": [21768, 5], "include_children": True}
    schema = corr.CORRECT_STRUCTURES_REQUEST_SCHEMA
    none = {**request, "correction": corr.CORRECTION_MODES["none"]}
    assert corr.encode(none, schema) == golden("correct_structures_request_none")
    assert corr.decode(golden("correct_structures_request_none"), schema) == none
    # NEIGHBOR is 0, proto3's default, so protobuf leaves it out, as the codec does;
    # the service reads an absent mode as NEIGHBOR.
    field = {**request, "correction": corr.CORRECTION_MODES["neighbor"]}
    assert corr.encode(field, schema) == golden("correct_structures_request_neighbor")
    assert corr.decode(golden("correct_structures_request_neighbor"), schema)["correction"] == 0


def test_no_request_carries_a_mode_the_service_reserves():
    """1.5.2 gives raw 2, 3 and 4 to a curve fit experiment: 3 is the fit alone,
    without the field.  Whatever name a request is made with, a saved or retired
    one included, it carries 0 (left out) or 1, and never anything else."""
    with corr.CorrectionsClient("http://127.0.0.1:9") as client:
        for name in ("neighbor", "Neighbor", "curvefit", "CurveFit", "all", "ALL", "Field + fit", True,
                     "on", "1", "2", "3", "field"):
            request = client._structures_request("RC1", "grid16", [410], True, name)
            assert request["correction"] == 0, name
            assert b"\x20" not in corr.encode(request, corr.CORRECT_STRUCTURES_REQUEST_SCHEMA)[-2:], name
        for name in ("none", "off", False, None, "0"):
            assert client._structures_request("RC1", "grid16", [410], True, name)["correction"] == 1, name
        for name in ("4", "sideways"):
            with pytest.raises(ValueError):
                client._structures_request("RC1", "grid16", [410], True, name)


def test_decode_correct_structures_response_and_its_stamp():
    answer = corr.decode(golden("correct_structures_response"),
                         corr.CORRECT_STRUCTURES_RESPONSE_SCHEMA)
    assert answer["locations"] == [
        {"location_id": 256667, "structure_id": 21768, "z": 788,
         "position": {"x": 131420.5, "y": 168265.0}, "offset": {"x": 12.5, "y": -10.0}},
        # A Location with only its ID: no position, no offset.
        {"location_id": 5, "structure_id": 0, "z": 0, "position": None, "offset": None},
    ]
    stamp = corr._answer_stamp(answer["stamp"])
    assert stamp == {"service_version": "1.4.0",
                     "field_built_utc": datetime.fromtimestamp(1790067000, tz=UTC),
                     "annotation_fingerprint": "1:5525:639282342724230000",
                     "annotation_watermark": datetime.fromtimestamp(1790000000, tz=UTC)}
    # GetAnswerStamp answers the stamp alone; a mode that samples no field leaves
    # field_built_utc unset.
    alone = corr._answer_stamp(corr.decode(golden("answer_stamp"), corr.ANSWER_STAMP_SCHEMA))
    assert alone["field_built_utc"] is None
    assert alone["annotation_watermark"] == datetime.fromtimestamp(1790000000.423, tz=UTC)


def test_correction_mode_names():
    assert corr.correction_mode(True) == "neighbor"  # what the old switch meant
    for off in (False, None, "off", "none", "0", "", "No"):
        assert corr.correction_mode(off) == "off"
    assert corr.correction_mode("1") == "neighbor"
    assert corr.correction_mode("Residual field") == "neighbor"
    # The modes Viking's service ran until 2026-10-27, read as the field: from a
    # saved session, a Look, an environment variable or an API caller.
    for retired in ("CurveFit", "curve_fit", "curvefit", "ALL", "all", "Field + fit", "2", "3"):
        assert corr.correction_mode(retired) == "neighbor", retired
    assert {"curvefit", "all"} <= corr.RETIRED_MODES
    for unknown in ("sideways", "4"):
        with pytest.raises(ValueError):
            corr.correction_mode(unknown)
    assert (corr.request_mode("curvefit"), corr.request_mode("off")) == ("neighbor", "none")


def make_structure_neuron(source: str = "i") -> Neuron:
    """Discs 1-4 with section X/Y; 5 a line, whose X/Y columns are empty (0, 0)."""
    neuron = make_neuron(with_curve=False, source=source)
    nodes = neuron.nodes
    nodes.loc[nodes["ID"] <= 4, ["X", "Y"]] = [133.0, 267.0]
    nodes.loc[nodes["ID"] == 5, "TypeCode"] = 5
    return neuron


@pytest.fixture
def structures(service):
    """The fake knows Locations 1, 2, 3 and 5 of c9 (not 4); the field adds
    (+120, -80) nm to each."""
    fake, url = service
    fake.locations = [{"location_id": i, "structure_id": 9, "z": z, "x": 7500.0, "y": 15000.0}
                      for i, z in ((1, 935), (2, 936), (3, 937), (5, 936))]
    fake.fit = {"neighbor": (120.0, -80.0)}
    return fake, url


@pytest.mark.parametrize("mode", ["neighbor", "curvefit", "all", True])
def test_the_field_moves_what_the_service_corrected(structures, mode):
    """A retired mode (a saved session, an API call) asks for the field, as the
    field itself does: no number but 0 goes on the wire."""
    fake, url = structures
    neuron = make_structure_neuron()
    before = neuron.nodes.set_index("ID")[["Xum", "Yum"]].copy()
    with corr.CorrectionsClient(url) as client:
        result = corr.correct_neuron(neuron, client, mode=mode)
    assert result is not None
    (request,) = fake.structure_requests
    assert request["structure_ids"] == [9] and request["include_children"] is True
    assert request["correction"] == corr.CORRECTION_MODES["neighbor"] == 0
    assert fake.modes_sent == [0]
    shift = (neuron.nodes.set_index("ID")[["Xum", "Yum"]] - before).round(9)
    for i in (1, 2, 3):
        assert shift.loc[i].tolist() == [0.12, -0.08]
    # 4 was not answered for; the service placed 5 at (7.5, 15) um, 60 um from
    # where the cell has it -- past PLACEMENT_LIMIT_NM.
    for i in (4, 5):
        assert shift.loc[i].tolist() == [0.0, 0.0]
    s = neuron.section_correction
    assert s["mode"] == "neighbor"
    assert (s["n_annotations"], s["n_found"], s["n_trusted"], s["n_moved"]) == (5, 4, 3, 3)
    assert fake.calls == ["ListCorrectionSets", "CorrectStructures"]


def test_a_line_placed_where_the_cell_has_it_is_corrected(structures):
    """Empty X/Y columns are no reason by themselves: since 2026-09-24 the
    service maps such a line from its outline's centroid."""
    fake, url = structures
    fake.locations[-1]["x"] = 67500.0  # Location 5 where the cell has it: 9,000 px x 7.5 nm
    neuron = make_structure_neuron()
    with corr.CorrectionsClient(url) as client:
        corr.correct_neuron(neuron, client, mode="neighbor")
    assert neuron.section_correction["n_trusted"] == 4
    row = neuron.nodes.set_index("ID").loc[5]
    assert (row["CorrDXnm"], row["CorrDYnm"]) == (120.0, -80.0)


def test_structure_offsets_are_kept_per_mode(structures, tmp_path):
    """The field's answer is kept once, whatever name asked for it; none, the
    other mode, is kept apart."""
    fake, url = structures
    cache = DiskCache(tmp_path)
    with corr.CorrectionsClient(url) as client:
        cset = corr.choose_set(client.correction_sets("InferiorMonkey"))
        first = corr.correct_neuron(make_structure_neuron(), client, cache=cache, mode="neighbor")
        again = corr.correct_neuron(make_structure_neuron(), client, cache=cache, mode="curvefit")
        also = corr.correct_neuron(make_structure_neuron(), client, cache=cache, mode="all")
        none = corr.fetch_structure_offsets(make_structure_neuron(), client, cset, "none", cache=cache)
    assert [r["correction"] for r in fake.structure_requests] == [0, 1]  # the field, none
    assert again.cached and again.token == first.token and again.summary()["mode"] == "neighbor"
    assert also.cached and also.token == first.token
    assert not none.cached and none.token != first.token and none.extras["mode"] == "none"


def test_a_service_version_keys_the_cache_instead_of_restarts(structures, tmp_path):
    """The answer stamp names the service version: a restart that changes
    nothing keeps the cached answers, an algorithm deploy (a new version) drops them."""
    fake, url = structures
    fake.version = "1.1.0"
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path), sets_ttl_s=0)
    try:
        provider.correct(make_structure_neuron(), "neighbor")
        fake.rebuilt += 3600  # restarted, same version
        kept = provider.correct(make_structure_neuron(), "neighbor")
        assert kept.cached and len(fake.structure_requests) == 1
        fake.version = "1.1.1"
        fresh = provider.correct(make_structure_neuron(), "neighbor")
        assert not fresh.cached and len(fake.structure_requests) == 2
    finally:
        provider.close()


def test_service_version_is_read_from_the_status_and_the_answers(structures):
    fake, url = structures
    fake.version = "1.4.0"
    with corr.CorrectionsClient(url) as client:
        assert client.rebuild_status()["service_version"] == "1.4.0"
        frame = client.correct_structures("InferiorMonkey", "Grid16", [9])
        stamp = client.answer_stamp("InferiorMonkey", "Grid16", [9])
        none = client.answer_stamp("InferiorMonkey", "Grid16", [9], mode="none")
    assert list(frame.columns) == ["location_id", "structure_id", "z", "x", "y", "dx", "dy"]
    assert frame["location_id"].tolist() == [1, 2, 3, 5]
    assert frame[["dx", "dy"]].drop_duplicates().to_numpy().tolist() == [[120.0, -80.0]]
    assert frame.attrs["stamp"]["service_version"] == stamp["service_version"] == "1.4.0"
    # The field's answer names the field it sampled; none's samples nothing.
    assert stamp["annotation_fingerprint"] == "f1"
    assert stamp["field_built_utc"] == datetime.fromtimestamp(fake.built, tz=UTC)
    assert none["field_built_utc"] is None
    # GetAnswerStamp takes the very request CorrectStructures does.
    assert fake.stamp_requests[:1] == fake.structure_requests


def test_the_sets_name_their_sections(service):
    fake, url = service
    with corr.CorrectionsClient(url) as client:
        (cset,) = client.correction_sets("InferiorMonkey")
        assert cset.sections == (936, 937)
        assert client.corrected_sections("InferiorMonkey", "grid16") == [936, 937]
        with pytest.raises(corr.CorrectionNotFound):
            client.corrected_sections("InferiorMonkey", "Mosaic")
    assert fake.calls == ["ListCorrectionSets"] * 3


def test_an_answer_with_another_count_of_sections_is_refused(service, monkeypatch):
    """CorrectPoints answers the sections in request order and does not name
    their Z: one missing would shift every section after it."""
    _, url = service
    with corr.CorrectionsClient(url) as client:
        call = client.call
        monkeypatch.setattr(client, "call", lambda *a, **k: {"sections": call(*a, **k)["sections"][:1]})
        with pytest.raises(corr.CorrectionsError, match="answered 1 sections; 2 were sent"):
            client.correct_points("InferiorMonkey", "Grid16",
                                  {936: np.array([[1.0, 2.0]]), 937: np.array([[3.0, 4.0]])})


def test_the_answer_stamp_keeps_a_cached_answer_until_it_moves(structures, tmp_path):
    """[Viking-Server]: equal AnswerStamps mean an identical answer.  A cell on
    disk asks GetAnswerStamp, not the fit: an equal stamp keeps the answer, a
    changed fingerprint (an annotation deleted, which the cell's own OData stamp
    does not see) asks CorrectStructures again, and the new answer is kept."""
    fake, url = structures
    cache = DiskCache(tmp_path)
    provider = corr.CorrectionsProvider(url, cache=cache, sets_ttl_s=0)
    try:
        first = provider.correct(make_structure_neuron(), "neighbor")
        assert not first.cached and fake.stamp_requests == []  # the answer came with its stamp
        kept = provider.correct(make_structure_neuron(), "neighbor")
        assert kept.cached and kept.token == first.token
        assert len(fake.structure_requests) == 1 and len(fake.stamp_requests) == 1
        fake.rebuilt += 3600  # a restart (it rebuilds on start) moves no stamp
        assert provider.correct(make_structure_neuron(), "neighbor").cached
        assert len(fake.structure_requests) == 1

        fake.fingerprint = "f2"  # a Location of c9 deleted in Viking
        fake.fit["neighbor"] = (100.0, -60.0)
        neuron = make_structure_neuron()
        assert neuron.data_stamp == make_structure_neuron().data_stamp
        fresh = provider.correct(neuron, "neighbor")
        assert not fresh.cached and fresh.token != first.token
        assert len(fake.structure_requests) == 2
        moved = neuron.nodes.set_index("ID").loc[1, ["CorrDXnm", "CorrDYnm"]].tolist()
        assert moved == [100.0, -60.0]
        again = provider.correct(make_structure_neuron(), "neighbor")
        assert again.cached and again.token == fresh.token
        assert len(fake.structure_requests) == 2 and len(fake.stamp_requests) == 4
    finally:
        provider.close()
    # Offline, the answer on disk is used without asking.
    offline = corr.CorrectionsProvider(url, cache=cache, offline=True)
    try:
        asked = len(fake.calls)
        neuron = make_structure_neuron()
        kept = offline.correct(neuron, "neighbor")
        assert kept.cached and kept.token == fresh.token and len(fake.calls) == asked
        assert neuron.section_correction["service_note"] == "offline"
    finally:
        offline.close()


def test_a_rebuilt_field_asks_again(structures, tmp_path):
    """The stamp names the field's build time: a rebuilt field moves it, and the
    field's answer is asked for again (none's, which samples no field, is kept)."""
    fake, url = structures
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path), sets_ttl_s=0)
    cache = DiskCache(tmp_path / "none")
    try:
        cset = corr.choose_set(provider.client.correction_sets("InferiorMonkey"))
        provider.correct(make_structure_neuron(), "neighbor")
        corr.fetch_structure_offsets(make_structure_neuron(), provider.client, cset, "none", cache=cache)
        fake.built += 86400
        assert corr.fetch_structure_offsets(make_structure_neuron(), provider.client, cset, "none",
                                            cache=cache).cached
        assert not provider.correct(make_structure_neuron(), "neighbor").cached
        assert provider.correct(make_structure_neuron(), "all").cached  # a retired name: the same answer
        assert [r["correction"] for r in fake.structure_requests] == [0, 1, 0]
    finally:
        provider.close()


def test_a_stamp_the_service_cannot_give_leaves_the_answer_on_disk_applied(structures, tmp_path):
    """While the service answers, a cell on disk is checked with GetAnswerStamp;
    when that fails, the answer on disk is applied as during a pause, its status
    says why, and the service or volume is paused or held as for the fit."""
    fake, url = structures
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path), timeout=3.0,
                                        retry_after_s=0.5, volume_retry_s=60)
    try:
        first = provider.correct(structure_neuron(9), "neighbor")
        # An exception in the service's handler: applied from disk, nothing held.
        fake.stamp_error = ("UNKNOWN", "Exception was thrown by handler.")
        neuron = structure_neuron(9)
        kept = provider.correct(neuron, "neighbor")
        assert kept.cached and kept.token == first.token
        assert neuron.section_correction["n_moved"] == 3
        assert "GetAnswerStamp" in neuron.section_correction["service_note"]
        assert not provider.paused_for("InferiorMonkey", "neighbor", 9)
        # It could not reach the volume's server: that volume is held.
        fake.stamp_error = MAPPING_DOWN
        neuron = structure_neuron(9)
        assert provider.correct(neuron, "neighbor").cached
        note = neuron.section_correction["service_note"]
        assert "volume mapping temporarily unavailable for 'InferiorMonkey'" in note
        assert "not asked again for InferiorMonkey until" in note
        assert provider.paused_for("InferiorMonkey", "neighbor") and not provider.paused
        stamps = len(fake.stamp_requests)
        assert provider.correct(structure_neuron(9), "neighbor").cached  # held: not asked
        assert len(fake.stamp_requests) == stamps
        # The hold over and the stamp equal again: checked, and kept.
        fake.stamp_error = None
        time.sleep(0.55)
        neuron = structure_neuron(9)
        assert provider.correct(neuron, "neighbor").cached
        assert "service_note" not in neuron.section_correction
        assert len(fake.stamp_requests) == stamps + 1
        # The service gone: paused for every volume, the answer on disk applied.
        fake.server.stop(None)
        neuron = structure_neuron(9)
        assert provider.correct(neuron, "neighbor").cached
        assert provider.paused and neuron.section_correction["service_note"] == provider.last_error
        assert len(fake.structure_requests) == 1
    finally:
        provider.close()


def test_an_swc_names_the_mode(structures, tmp_path):
    from sbfsem.export.swc import write_swc

    _, url = structures
    neuron = make_structure_neuron()
    with corr.CorrectionsClient(url) as client:
        corr.correct_neuron(neuron, client, mode="curvefit")  # a retired name: the field
    swc = write_swc(neuron, tmp_path / "field.swc").read_text()
    assert "3 of 5 annotations moved in x and y" in swc
    assert "(Viking's published residual registration field, as Viking's own exports apply it)" in swc
    assert "curve fit" not in swc


def test_a_web_load_takes_the_mode_and_reloads_for_another(web):
    """A load that names a retired mode (a tab or session from before 2026-10-27)
    is the field's load, into the field's copy; off is another copy."""
    fake, svc = web
    plain = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    assert plain.summary.section_correction is None and plain.summary.copy_tag == "off.0"
    raw = svc.neuron(1178, "i")
    nodes = raw.nodes

    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="curvefit")
    sc = detail.summary.section_correction
    assert sc.applied and sc.mode == "neighbor" and detail.summary.copy_tag == "neighbor.0"
    assert sc.n_found == sc.n_trusted == sc.n_moved == len(nodes)
    field = svc.neuron(1178, "i")
    asked = len(fake.structure_requests)
    # The same copy for every name of the field, and for the server's default.
    for name in ("all", "neighbor", True, None):
        svc.load_neuron(1178, "i", include_synapses=False, section_corrections=name)
        assert svc.neuron(1178, "i") is field, name
    assert len(fake.structure_requests) == asked
    assert fake.modes_sent == [0]
    # Another setting is another load, into a copy of its own; switching back is
    # answered from the copy already held (one copy per setting in use, so a
    # tab that switches never changes another tab's cell).
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    assert svc.neuron(1178, "i") is raw
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    assert svc.neuron(1178, "i") is field
    # A page from before names its copy by the old tag: the same copy answers.
    from sbfsem.service.facade import requested_copy

    with requested_copy("all.0"):
        assert svc.neuron(1178, "i") is field
    with requested_copy("curvefit.0"):
        assert svc.neuron(1178, "i") is field


def test_a_curve_fit_load_logs_a_thin_process_pulled_toward_a_wider_one(web, caplog, tmp_path):
    """The correction log.  c1178's 690259 (r 0.25 um) is linked to 40440 (r 1.70 um,
    6.7x) in the cassette; Field + fit moved it 0.43 of the way there on 2026-10-20.
    A load now asks for the field, which is not checked, and says why; a cell read
    from a file saved under the fit before 2026-10-27 is checked as at a load then:
    here 690259 moved halfway, and its other neighbor 690258 with it."""
    import logging

    from sbfsem.analysis.correction_checks import NO_CURVE_FIT
    from sbfsem.export.json_v2 import read_neuron_json, write_neuron_json

    fake, svc = web
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    nodes = svc.neuron(1178, "i").nodes.drop_duplicates("ID").set_index("ID")
    sx, sy = map(float, svc.neuron(1178, "i").volume_scale[:2])
    fake.fit = {"neighbor": (0.0, 0.0)}
    half = 0.5 * np.array([(nodes.at[40440, a] - nodes.at[690259, a]) * s
                           for a, s in (("VolumeX", sx), ("VolumeY", sy))])
    assert np.hypot(*half) > 1000.0           # over 1 um, and over the annotation's diameter
    fake.moves = {690259: tuple(half), 690258: tuple(half * 1.02)}

    with caplog.at_level(logging.INFO, logger="sbfsem.service.facade"):
        svc.load_neuron(1178, "i", include_synapses=False, section_corrections="all")
    assert "pulled" not in caplog.text, "the field is not checked"
    cells = {c.copy_tag: c for c in svc.correction_log()}
    field = cells["neighbor.0"]
    assert field.mode == "neighbor" and not field.checked and field.note == NO_CURVE_FIT
    assert "no longer runs a curve fit" in field.note
    assert "off.0" not in cells                # not corrected: nothing to log

    # The same moves, saved under Field + fit: checked when the file is read.
    saved = read_neuron_json(write_neuron_json(svc.neuron(1178, "i"), tmp_path / "c1178.json"))
    saved.correction_status = {**svc.neuron(1178, "i").correction_status, "mode": "all"}
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="sbfsem.service.facade"):
        svc.add_neuron(saved)
    assert "pulled 1 annotation(s) toward a linked one 4x wider or more" in caplog.text
    assert "690259" in caplog.text
    (fitted,) = [c for c in svc.correction_log() if c.mode == "all"]
    assert fitted.checked
    assert [p.location_id for p in fitted.pulls] == [690259]
    pull = fitted.pulls[0]
    assert pull.neighbor_id == 40440 and pull.moved_with == [690258]
    assert pull.ratio == pytest.approx(6.7, abs=0.05)
    assert pull.frac == pytest.approx(0.5, abs=1e-3) and pull.cos == pytest.approx(1.0, abs=1e-6)
    assert fitted.service_version is None     # the fake service has no version
    assert [p.location_id for p in svc.correction_log(7.0)[0].pulls] == []


def test_the_service_version_is_kept_with_a_correction(structures, tmp_path):
    fake, url = structures
    fake.version = "1.2.0"
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path))
    try:
        neuron = make_structure_neuron()
        provider.correct(neuron, "neighbor")
        assert neuron.section_correction["service_version"] == "1.2.0"
        again = make_structure_neuron()       # answered from disk, with the version that made it
        provider.correct(again, "neighbor")
        assert again.section_correction["cached"] and again.section_correction["service_version"] == "1.2.0"
    finally:
        provider.close()


def test_a_lab_can_have_its_own_default_mode(web):
    # The web app: the residual field on every volume since 2026-10-27; before, Field +
    # fit for the Neitz lab's volumes (the user's decision of 2026-10-20), Curve fit for
    # the others.  Those names, from an old environment, are the field now.
    fake, svc = web
    old = type(svc)(section_corrections="curvefit", lab_corrections={"Neitz": "all"})
    try:
        assert old.default_correction("i") == old.default_correction("RC2") == "neighbor"
        assert old.volume_info("NeitzCped").default_correction == "neighbor"
    finally:
        old.close()
    svc.correction_mode = "neighbor"
    svc.lab_corrections = {"Neitz": "off"}
    assert svc.default_correction("i") == "off"
    assert svc.default_correction("RC2") == "neighbor"
    assert svc.default_correction("demo") == "neighbor"  # no lab
    assert svc.volume_info("NeitzCped").default_correction == "off"
    assert svc.volume_info("RPC1").lab == "Marc"
    assert svc.volume_info("RPC1").default_correction == "neighbor"

    # A load that names no mode gets the lab's; one that names a mode, that one.
    detail = svc.load_neuron(1178, "i", include_synapses=False)
    assert detail.summary.section_correction is None and detail.summary.copy_tag == "off.0"
    named = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="all")
    assert named.summary.section_correction.mode == "neighbor"
    assert named.summary.copy_tag == "neighbor.0"


def test_a_failure_in_the_service_holds_that_cell_and_mode(web):
    fake, svc = web
    fake.fail_structures = True
    svc.corrections().volume_retry_s = 0.3
    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    sc = detail.summary.section_correction
    assert sc.requested and not sc.applied and sc.mode == "neighbor"
    assert "Exception was thrown by handler" in sc.note
    first = svc.neuron(1178, "i")
    assert "CorrDXnm" not in first.nodes.columns
    # Held: the same load again is answered at once, without asking the service,
    # whatever name the field is asked by.
    fake.fail_structures = False
    for name in ("neighbor", "curvefit", None):
        svc.load_neuron(1178, "i", include_synapses=False, section_corrections=name)
        assert svc.neuron(1178, "i") is first and len(fake.structure_requests) == 1
    provider = svc.corrections()
    assert provider.paused_for("InferiorMonkey", "neighbor", 1178)
    assert provider.paused_for("InferiorMonkey", "all", 1178), "a retired name is the same hold"
    assert not provider.paused_for("InferiorMonkey", "neighbor"), "only that cell is held"
    # Once the hold is over, the next load asks again.
    time.sleep(0.35)
    again = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    assert again.summary.section_correction.applied
    assert len(fake.structure_requests) == 2


def test_a_correction_the_service_refuses_says_so_in_the_cells_status(web):
    """Service 1.5.0 answered the modes it no longer ran with INVALID_ARGUMENT
    (2026-10-27): the cell is drawn uncorrected, and its status says the service
    refused, never nothing."""
    fake, svc = web
    fake.fail_structures = True
    fake.fail_code = "INVALID_ARGUMENT"
    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    sc = detail.summary.section_correction
    assert sc.requested and not sc.applied
    assert "INVALID_ARGUMENT" in sc.note and sc.note.startswith("Not applied")
    assert "CorrDXnm" not in svc.neuron(1178, "i").nodes.columns

    # A kept answer the service then refuses to check is applied, and says it was refused.
    import copy

    fake.fail_structures = False
    svc.corrections()._held_structures.clear()
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    plain = svc.neuron(1178, "i")
    assert svc._correct(copy.deepcopy(plain), "neighbor")["applied"]  # now on disk
    fake.stamp_error = ("INVALID_ARGUMENT", "unknown correction mode 0")
    status = svc._correct(copy.deepcopy(plain), "neighbor")
    assert status["applied"] and status["cached"]
    assert status["note"].startswith("The service refused the request; offsets kept from before")
    assert "INVALID_ARGUMENT" in status["note"]


def test_a_cell_the_service_throws_on_leaves_the_rest_of_its_volume_corrected(web):
    """Cped c1 on 2026-10-20: one structure whose ``all`` request threw held
    Field + fit for all of Cped for ten minutes, and the cells loaded after it
    were drawn uncorrected though the service answered for them."""
    fake, svc = web
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="off")
    loaded = svc.neuron(1178, "i")
    fake.fit = {"neighbor": (60.0, 40.0)}
    fake.fail_structures = {9}
    provider = svc.corrections()
    with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler") as failed:
        provider.correct(make_structure_neuron(), "neighbor")  # c9 of the same volume
    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    sc = detail.summary.section_correction
    assert sc.applied and sc.mode == "neighbor" and sc.n_moved == len(loaded.nodes), sc.note
    assert [r["structure_ids"] for r in fake.structure_requests] == [[9], [1178]]
    assert failed.value.code == "UNKNOWN"
    assert provider.paused_for("InferiorMonkey", "neighbor", 9)
    assert not provider.paused_for("InferiorMonkey", "neighbor")
    # c9 itself stays held: asked again, it is not sent.
    with pytest.raises(corr.CorrectionsError,
                       match="not asked again for c9 of InferiorMonkey until .* UTC"):
        provider.correct(make_structure_neuron(), "neighbor")
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
    asked = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    assert asked.summary.copy_tag == "neighbor.0" and not asked.summary.section_correction.applied
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
    fake.fit = {"neighbor": (120.0, -80.0)}
    fake.fail_structures = False
    time.sleep(0.35)
    done = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    assert done.summary.copy_tag == "neighbor.0" and done.summary.section_correction.applied
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
            provider.correct(structure_neuron(9), "neighbor")
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for c9 of InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(9), "neighbor")
        assert len(fake.structure_requests) == 1
        assert provider.paused_for("InferiorMonkey", "neighbor", 9)
        assert not provider.paused_for("InferiorMonkey", "neighbor")
        # A second structure throws, with no answer between: the volume is held
        # (RC1 on 2026-09-24, Cped while its volume server refused on 2026-10-20).
        with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
            provider.correct(structure_neuron(10), "neighbor")
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(11), "neighbor")
        assert len(fake.structure_requests) == 2
        assert provider.paused_for("InferiorMonkey", "neighbor")
        assert provider.paused_for("InferiorMonkey", "neighbor", 11)
        assert not provider.paused  # the host answered: nothing else waits
        assert not provider.paused_for("RC1", "neighbor")
    finally:
        provider.close()


def test_an_answer_between_two_failures_keeps_the_volume_asked(structures):
    fake, url = structures
    fake.fail_structures = {9, 10, 12}
    provider = corr.CorrectionsProvider(url, volume_retry_s=0.3)
    try:
        with pytest.raises(corr.CorrectionsError):
            provider.correct(structure_neuron(9), "neighbor")
        assert provider.correct(structure_neuron(11), "neighbor") is not None
        with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
            provider.correct(structure_neuron(10), "neighbor")
        assert not provider.paused_for("InferiorMonkey", "neighbor")
        # Nor does a failure count once its hold is over.
        time.sleep(0.35)
        with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
            provider.correct(structure_neuron(12), "neighbor")
        assert not provider.paused_for("InferiorMonkey", "neighbor")
        assert provider.correct(structure_neuron(13), "neighbor") is not None
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
            provider.correct(structure_neuron(9), "neighbor")
        assert failed.value.code == "PERMISSION_DENIED"
        assert provider.paused_for("InferiorMonkey", "neighbor")
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(10), "neighbor")
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
            provider.correct(structure_neuron(9), "neighbor")
        assert failed.value.code == "UNAVAILABLE"
        # RC1, not asked for before, is asked and corrected.
        rc1 = structure_neuron(476, "RC1")
        assert provider.correct(rc1, "neighbor") is not None
        assert rc1.section_correction["n_moved"] == 3
        assert not provider.paused and not provider.paused_for("RC1", "neighbor")
        # Inferior is held, for retry_after_s, by whatever name the field is asked.
        assert provider.paused_for("InferiorMonkey", "neighbor")
        assert provider.paused_for("InferiorMonkey", "all", 10)
        with pytest.raises(corr.CorrectionsError,
                           match="not asked again for InferiorMonkey until .* UTC"):
            provider.correct(structure_neuron(10), "neighbor")
        assert [(r["set"]["volume_name"], r["structure_ids"]) for r in fake.structure_requests] == [
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
            provider.correct(structure_neuron(9), "neighbor")
        assert provider.paused and provider.paused_for("RC1", "neighbor")
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(structure_neuron(476, "RC1"), "neighbor")
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
        assert provider.correct(structure_neuron(476, "RC1"), "neighbor") is not None
        if outage == "unreachable":
            fake.server.stop(None)  # gRPC's own UNAVAILABLE: "failed to connect ..."
        else:
            fake.volume_errors["InferiorMonkey"] = ("UNAVAILABLE", outage)
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(structure_neuron(9), "neighbor")
        assert provider.paused
        down_until = provider._down_until
        sent = []
        call = provider.client.call
        monkeypatch.setattr(provider.client, "call",
                            lambda method, *a, **k: sent.append(method) or call(method, *a, **k))
        # c476's offsets on disk are still applied, and its status says why they are old.
        neuron = structure_neuron(476, "RC1")
        kept = provider.correct(neuron, "neighbor")
        assert kept.cached and neuron.section_correction["n_moved"] == 3
        assert neuron.section_correction["service_note"] == provider.last_error != ""
        # Nothing on disk: refused without asking.
        with pytest.raises(corr.CorrectionsUnavailable, match="no neighbor offsets on disk"):
            provider.correct(structure_neuron(477, "RC1"), "neighbor")
        assert sent == []
        assert provider._down_until == down_until  # a refusal does not lengthen the pause
        assert [r["set"]["volume_name"] for r in fake.structure_requests] == (
            ["RC1"] if outage == "unreachable" else ["RC1", "InferiorMonkey"])
    finally:
        provider.close()


def test_a_volume_held_unavailable_keeps_its_offsets_on_disk_and_is_asked_again(
        structures, tmp_path):
    fake, url = structures
    provider = corr.CorrectionsProvider(url, cache=DiskCache(tmp_path), retry_after_s=0.3,
                                        volume_retry_s=60)
    try:
        assert provider.correct(structure_neuron(9), "neighbor") is not None  # now on disk
        fake.volume_errors["InferiorMonkey"] = MAPPING_DOWN
        with pytest.raises(corr.CorrectionsUnavailable):
            provider.correct(structure_neuron(10), "neighbor")
        # c9's offsets are on disk: still applied, and the status says why they are old.
        neuron = structure_neuron(9)
        kept = provider.correct(neuron, "neighbor")
        assert kept.cached and neuron.section_correction["n_moved"] == 3
        note = neuron.section_correction["service_note"]
        assert "volume mapping temporarily unavailable for 'InferiorMonkey'" in note
        assert "not asked again for InferiorMonkey until" in note
        n = len(fake.structure_requests)
        fake.volume_errors.clear()
        time.sleep(0.35)
        assert not provider.paused_for("InferiorMonkey", "neighbor")
        assert provider.correct(structure_neuron(10), "neighbor") is not None
        assert len(fake.structure_requests) == n + 1
    finally:
        provider.close()


def test_a_longer_hold_is_not_cut_short_by_an_unavailable_volume(structures):
    fake, url = structures
    provider = corr.CorrectionsProvider(url, retry_after_s=0.3, volume_retry_s=60)
    try:
        fake.fail_structures = {7, 8}
        for sid in (7, 8):  # two structures throw in a row: the volume held for volume_retry_s
            with pytest.raises(corr.CorrectionsError, match="Exception was thrown by handler"):
                provider.correct(structure_neuron(sid), "neighbor")
        provider._unavailable("InferiorMonkey", "neighbor", corr.CorrectionsUnavailable("down"))
        time.sleep(0.35)
        assert "Exception was thrown by handler" in provider.held("InferiorMonkey", "neighbor")
    finally:
        provider.close()


def test_a_load_while_its_volume_is_unavailable_is_held(web):
    fake, svc = web
    fake.volume_errors["InferiorMonkey"] = MAPPING_DOWN
    svc.corrections().retry_after_s = 0.3
    detail = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
    sc = detail.summary.section_correction
    assert sc.requested and not sc.applied
    assert "volume mapping temporarily unavailable for 'InferiorMonkey'" in sc.note
    first = svc.neuron(1178, "i")
    # Held: the same load again is answered at once, from the copy it has.
    fake.volume_errors.clear()
    svc.load_neuron(1178, "i", include_synapses=False, section_corrections="all")
    assert svc.neuron(1178, "i") is first and len(fake.structure_requests) == 1
    provider = svc.corrections()
    assert not provider.paused and provider.paused_for("InferiorMonkey", "neighbor", 1178)
    time.sleep(0.35)
    again = svc.load_neuron(1178, "i", include_synapses=False, section_corrections="neighbor")
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
