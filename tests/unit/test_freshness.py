"""Freshness from Viking's StructureFreshnessBatch, and the mesh key it names."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import httpx
import pytest

from sbfsem.geometry.mesh import Mesh
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.io.odata import ODataClient, ODataError, odata_utc, parse_datetime
from sbfsem.service.facade import SbfsemService, cell_freshness

# c943's newest annotation as a row cached under Viking's old label, and the
# same edit as Viking labels it now.
CACHED = "2026-09-23T05:31:15.223-07:00"
LIVE = "2026-09-23T05:31:15.223Z"


class FakeViking:
    """Viking answering both the batch action and the per-cell query alike."""

    def __init__(self, newest: str, own_count: int, family_count: int | None = None,
                 *, batch: bool = True):
        self.newest = newest
        self.own_count = own_count
        self.family_count = family_count
        self.batch = batch
        self.calls: list[tuple] = []

    def structure_freshness(self, ids, *, depth=0):
        self.calls.append(("batch", tuple(sorted(set(ids))), depth))
        if not self.batch:
            raise ODataError("no such action")
        count = self.own_count if depth == 0 else self.family_count
        return {int(i): (odata_utc(self.newest), count) for i in ids}

    @contextlib.contextmanager
    def fresh(self):
        yield

    def get_last_annotations(self, structure_id, n=1):
        self.calls.append(("locations", structure_id))
        return [{"LastModified": self.newest}]


@pytest.fixture
def service(i943_path):
    svc = SbfsemService(offline=True)
    neuron = read_legacy_json(i943_path)
    neuron.data_stamp = parse_datetime(CACHED)
    neuron.viking["LastModified"] = "2017-03-19T23:27:30.913-06:00"
    svc.add_neuron(neuron)
    yield svc, neuron
    svc.close()


def _ask(svc, viking):
    svc.client = lambda source: viking
    return svc.freshness(943, "i")


def test_the_summary_names_its_stamps_as_the_utc_instants_the_freshness_check_uses(service):
    """The tab shows a load's stamp beside Viking's: Update used to report the
    load with Pacific's label on UTC digits, 7 h before the instant it names."""
    svc, neuron = service
    neuron.last_modified = parse_datetime("2017-03-19T23:27:30.913-06:00")
    summary = svc.neuron_detail(943, "i").summary
    assert summary.data_stamp == odata_utc(LIVE) == odata_utc(CACHED)
    assert summary.data_stamp.utcoffset().total_seconds() == 0
    assert summary.data_stamp.isoformat() == "2026-09-23T05:31:15.223000+00:00"
    assert summary.last_modified.isoformat() == "2017-03-19T23:27:30.913000+00:00"
    assert cell_freshness(neuron)[0] >= summary.data_stamp
    neuron.data_stamp = None
    assert svc.neuron_detail(943, "i").summary.data_stamp is None


def test_odata_utc_reads_the_digits_whatever_the_label():
    assert odata_utc(CACHED) == odata_utc(LIVE) == datetime(2026, 9, 23, 5, 31, 15, 223000, tzinfo=UTC)
    assert odata_utc(parse_datetime(CACHED)) == odata_utc(LIVE)
    assert odata_utc(None) is None and odata_utc("") is None


def test_a_current_copy_is_not_stale(service):
    svc, neuron = service
    answer = _ask(svc, FakeViking(LIVE, own_count=190))
    assert not answer.stale and not answer.surface_stale
    assert answer.loaded == answer.live == odata_utc(LIVE)
    assert answer.loaded_count == answer.live_count == 190


def test_an_edit_within_seven_hours_of_the_fetch_is_seen(service):
    """A row cached as -07:00 and a live Z row: 3 h later was read as 4 h earlier."""
    svc, _ = service
    answer = _ask(svc, FakeViking("2026-09-23T08:31:15.223Z", own_count=190))
    assert answer.stale and answer.surface_stale


def test_a_deleted_annotation_is_seen(service):
    """Deleting an annotation leaves the newest date alone; the count drops."""
    svc, _ = service
    answer = _ask(svc, FakeViking(LIVE, own_count=189))
    assert answer.stale and answer.surface_stale
    assert (answer.loaded_count, answer.live_count) == (190, 189)


def test_children_are_compared_by_count_and_never_move_the_surface(service):
    svc, neuron = service
    neuron.children_loaded = True
    same = _ask(svc, FakeViking(LIVE, own_count=190, family_count=199))
    assert not same.stale
    added = _ask(svc, FakeViking(LIVE, own_count=190, family_count=200))
    assert added.stale and added.children_stale and not added.surface_stale
    # A cell loaded without its children is never compared with them.
    neuron.children_loaded = False
    assert not _ask(svc, FakeViking(LIVE, own_count=190, family_count=500)).stale


def test_one_request_per_volume_for_many_cells(service):
    svc, neuron = service
    neuron.children_loaded = True
    viking = FakeViking(LIVE, own_count=190, family_count=199)
    svc.client = lambda source: viking
    answers = svc.freshness_many([(943, "i", None), (943, "NeitzInferiorMonkey", None),
                                  (12345, "i", None)])
    assert [type(a).__name__ for a in answers] == ["FreshnessDTO", "FreshnessDTO",
                                                   "NeuronNotLoadedError"]
    assert viking.calls == [("batch", (943,), 0), ("batch", (943,), 1)]


def test_a_volume_without_the_action_falls_back_per_cell(service):
    svc, _ = service
    viking = FakeViking("2026-09-23T08:31:15.223Z", own_count=190, batch=False)
    answer = _ask(svc, viking)
    assert answer.stale and answer.live_count is None
    assert ("locations", 943) in viking.calls


def test_unstamped_data_is_never_stale(service):
    svc, neuron = service
    neuron.data_stamp = None
    assert not _ask(svc, FakeViking("2030-01-01T00:00:00Z", own_count=1)).stale


def test_structure_freshness_posts_chunks_and_reads_utc(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        seen.append((request.method, request.url.path, len(body["IDs"]), body["Depth"]))
        return httpx.Response(200, json={"value": [
            {"StructureID": i, "LastModified": "2026-10-15T02:52:24.307Z", "AnnotationCount": i}
            for i in body["IDs"] if i != 7]})

    from sbfsem.io.cache import DiskCache
    client = ODataClient("rc2", cache=DiskCache(tmp_path),
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    client.FRESHNESS_CHUNK = 4
    out = client.structure_freshness([3, 1, 2, 7, 5, 6, 1], depth=1)
    assert seen == [("POST", "/RC2/OData/StructureFreshnessBatch", 4, 1),
                    ("POST", "/RC2/OData/StructureFreshnessBatch", 2, 1)]
    assert sorted(out) == [1, 2, 3, 5, 6]        # 7 unknown to Viking
    assert out[5] == (datetime(2026, 10, 15, 2, 52, 24, 307000, tzinfo=UTC), 5)
    assert client.structure_freshness([]) == {}
    with pytest.raises(ODataError):
        ODataClient("rc2", offline=True, cache=DiskCache(tmp_path)).structure_freshness([1])
    broken = ODataClient("rc2", cache=DiskCache(tmp_path), client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(ODataError):
        broken.structure_freshness([1])


def test_mesh_key_is_the_cells_own_freshness(service):
    """Children, and the old time label, no longer change the key."""
    _, neuron = service
    stamp, count = cell_freshness(neuron)
    assert (stamp, count) == (odata_utc(LIVE), 190)
    neuron.data_stamp = parse_datetime(LIVE)
    assert cell_freshness(neuron) == (stamp, count)
    neuron.viking["LastModified"] = "2026-10-01T00:00:00Z"     # a newer structure row
    assert cell_freshness(neuron)[0] == datetime(2026, 10, 1, tzinfo=UTC)


class FakeDisk:
    """A disk cache holding one surface, under the key used before 2026-10-15:
    the newest Location as labeled (``-07:00``) and every node, children too."""

    def __init__(self, mesh: Mesh):
        self.mesh = mesh
        self.puts: list[tuple] = []

    def get(self, key):
        return self.mesh if "-07:00" in repr(key) and ", 199, " in repr(key) else None

    def put(self, key, mesh):
        self.puts.append(key)


def test_a_surface_under_the_old_key_is_served_and_stored_again(service, c943):
    svc, _ = service
    disk = FakeDisk(svc.build_mesh(c943, method="cylinder"))
    svc._disk_meshes = disk
    svc._build_mesh = lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt"))
    dto = svc.render_mesh(943, "i", method="cylinder")
    assert dto.n_faces == len(disk.mesh.faces)
    assert len(disk.puts) == 1
    new_key = repr(disk.puts[0])
    assert "+00:00" in new_key and "-07:00" not in new_key and ", 190, " in new_key


class FakeLastModified:
    """Viking answering the newest annotation, and remembering how it was asked."""

    def __init__(self, row):
        self.row = row
        self.fresh_depth = 0
        self.asked_fresh: list[bool] = []

    @contextlib.contextmanager
    def fresh(self):
        self.fresh_depth += 1
        try:
            yield
        finally:
            self.fresh_depth -= 1

    def get_last_modified_annotation(self, structure_id):
        self.asked_fresh.append(self.fresh_depth > 0)
        return self.row


def test_show_last_modified_asks_viking_and_reads_the_time_as_utc(service):
    """The disk cache has no expiry, so the answer was the first one ever given;
    and the time came back with Viking's Pacific label on UTC wall-clock time, 7 h late."""
    svc, neuron = service
    node = neuron.nodes.iloc[0]
    viking = FakeLastModified({"ID": int(node["ID"]), "LastModified": CACHED, "Username": " becca "})
    svc.client = lambda source: viking
    answer = svc.last_modified_annotation(943, "i")
    assert viking.asked_fresh == [True]                     # never from the cache
    assert answer["last_modified"].endswith("Z")
    assert odata_utc(answer["last_modified"]) == odata_utc(LIVE)
    assert "-07:00" not in answer["last_modified"]
    assert answer["username"] == "becca"
    assert answer["xyz_um"] == [float(node.Xum), float(node.Yum), float(node.Zum)]
    # the same edit labeled as Viking labels it now is the same instant
    viking.row = {**viking.row, "LastModified": LIVE}
    assert svc.last_modified_annotation(943, "i")["last_modified"] == answer["last_modified"]
    viking.row = None
    assert svc.last_modified_annotation(943, "i") is None
