"""Freshness from Viking's StructureFreshnessBatch, and the mesh key it names."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import httpx
import pytest

from sbfsem.core.transforms import Transforms
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
                 *, batch: bool = True, links=None, family_newest: str | None = None):
        self.newest = newest
        self.own_count = own_count
        self.family_count = family_count
        self.family_newest = family_newest
        self.batch = batch
        #: ``(count, newest Created)`` of the LocationLinks; ``None``: not answered.
        self.links = links
        self.calls: list[tuple] = []
        self.link_calls: list[int] = []

    def structure_freshness(self, ids, *, depth=0):
        self.calls.append(("batch", tuple(sorted(set(ids))), depth))
        if not self.batch:
            raise ODataError("no such action")
        count = self.own_count if depth == 0 else self.family_count
        newest = self.newest if depth == 0 else (self.family_newest or self.newest)
        return {int(i): (odata_utc(newest), count) for i in ids}

    def location_link_signature(self, structure_id):
        self.link_calls.append(int(structure_id))
        if self.links is None:
            raise ODataError("not answered")
        return self.links

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


# -- what the stamp cannot see (2026-11-20) -------------------------------------
LINKED = datetime(2026, 10, 28, 21, 25, 41, 333333, tzinfo=UTC)


def test_a_link_made_or_removed_between_existing_annotations_is_seen(service):
    """Viking's stamp is the newest Location and the count: a LocationLink made or
    removed between two annotations that exist touches neither (Inferior c51578:
    369 of 571 links are newer than both their annotations)."""
    svc, neuron = service
    neuron.link_signature = (188, LINKED)
    same = _ask(svc, FakeViking(LIVE, own_count=190, links=(188, LINKED)))
    assert not same.stale and not same.links_stale
    removed = _ask(svc, FakeViking(LIVE, own_count=190, links=(187, LINKED)))
    assert removed.stale and removed.surface_stale and removed.links_stale
    made = _ask(svc, FakeViking(LIVE, own_count=190,
                                links=(188, datetime(2026, 11, 19, 22, 2, 38, tzinfo=UTC))))
    assert made.stale and made.surface_stale and made.links_stale, "one removed, one made: newer"
    # Viking not answering about links, or a copy with no signature: not a change.
    assert not _ask(svc, FakeViking(LIVE, own_count=190, links=None)).stale
    neuron.link_signature = None
    assert not _ask(svc, FakeViking(LIVE, own_count=190, links=(1, LINKED))).stale


def test_a_synapse_retagged_or_moved_is_seen_by_its_date(service):
    """Children were compared by count alone, so a retagged PSD (the report's own
    "Untagged PSDs" worklist asks for those) never showed as a change."""
    svc, neuron = service
    neuron.children_loaded = True
    neuron.children_stamp = parse_datetime("2026-11-19T22:50:05.367Z")
    kids = "2026-11-19T22:50:05.367Z"
    same = _ask(svc, FakeViking(LIVE, own_count=190, family_count=199, family_newest=kids))
    assert not same.stale
    retagged = _ask(svc, FakeViking(LIVE, own_count=190, family_count=199,
                                    family_newest="2026-11-20T09:00:00Z"))
    assert retagged.stale and retagged.children_stale and not retagged.surface_stale
    # Within one SQL tick (Viking answers milliseconds, the rows a third of one).
    tick = _ask(svc, FakeViking(LIVE, own_count=190, family_count=199,
                                family_newest="2026-11-19T22:50:05.370Z"))
    assert not tick.stale


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


class _Loads:
    """``NeuronLoader`` as the service uses it: each load is c943 stamped ``stamp``,
    noted with whether it went past the disk cache (``client.fresh()``)."""

    def __init__(self, i943_path, viking):
        self.path, self.viking = i943_path, viking
        self.stamp = CACHED
        self.links = (188, LINKED)
        self.calls: list[str] = []

    def __call__(self, volume, client=None):
        loads = self

        class Loader:
            def load(self, structure_id, *, include_synapses=False, transform="Standard",
                     untagged_psd="assume"):
                loads.calls.append("fresh" if loads.viking.fresh_now else "cached")
                neuron = read_legacy_json(loads.path)
                neuron.transform = Transforms.from_str(transform)
                neuron.data_stamp = parse_datetime(loads.stamp)
                neuron.link_signature = loads.links
                return neuron

        return Loader()


class _Viking(FakeViking):
    """FakeViking that says when a fetch goes past the disk cache, or answers nothing."""

    def __init__(self, *args, down: bool = False, **kw):
        super().__init__(*args, **kw)
        self.fresh_now = False
        self.down = down

    def structure_freshness(self, ids, *, depth=0):
        if self.down:
            raise ODataError("Viking is down")
        return super().structure_freshness(ids, depth=depth)

    def get_last_annotations(self, structure_id, n=1):
        if self.down:
            raise ODataError("Viking is down")
        return super().get_last_annotations(structure_id, n)

    @contextlib.contextmanager
    def fresh(self):
        self.fresh_now = True
        try:
            yield
        finally:
            self.fresh_now = False


def test_a_load_checks_the_copy_against_viking_and_fetches_a_newer_one(i943_path, monkeypatch):
    """A load answered from the copy the server held, or from the disk cache,
    drew a cell as it was when first fetched: Temporal c162392, being traced,
    was drawn from its 1 annotation an hour after it had 153, and the build came
    back empty (2026-10-21).  A load now asks Viking first, once per 5 s per copy
    (30 s until 2026-11-20)."""
    import sbfsem.service.facade as facade_mod

    viking = _Viking(LIVE, own_count=190, links=(188, LINKED))
    loads = _Loads(i943_path, viking)
    monkeypatch.setattr(facade_mod, "NeuronLoader", loads)
    svc = SbfsemService(offline=True)
    svc.offline = False                      # Viking is the fake: asked, never reached
    svc.client = lambda source: viking
    load = lambda **kw: svc.load_neuron(943, "i", section_corrections="off",  # noqa: E731
                                        live_registration=False, **kw)
    try:
        load()
        assert loads.calls == ["cached"], "the disk cache's copy, current in Viking: kept"
        assert viking.calls == [("batch", (943,), 0)]

        load()
        assert loads.calls == ["cached"] and len(viking.calls) == 1, "held, and asked under 30 s ago"

        svc.FRESH_CHECK_S = 0.0
        load()
        assert loads.calls == ["cached"] and len(viking.calls) == 2, "asked again; still current"

        # Annotations added in Viking: the held copy is fetched again past the cache.
        viking.own_count = 191
        loads.stamp = "2026-09-23T09:00:00Z"
        detail = load()
        assert loads.calls == ["cached", "fresh"]
        assert detail.summary.data_stamp == odata_utc("2026-09-23T09:00:00Z")

        # The disk cache's copy is older than Viking's: fetched again at once.
        assert svc.unload_neuron(943, "i")
        loads.stamp = CACHED
        load()
        assert loads.calls == ["cached", "fresh", "cached", "fresh"]

        # A link made in Viking between annotations that exist: the stamp and
        # count are as they were, and the copy is fetched again all the same.
        viking.links = (189, datetime(2026, 11, 19, 22, 2, 38, tzinfo=UTC))
        before = list(loads.calls)
        load()
        assert loads.calls == before + ["fresh"], "the link signature differs"
        loads.links = viking.links

        # An explicit refresh asks nothing first.
        asked = len(viking.calls)
        load(refresh=True)
        assert loads.calls[-1] == "fresh" and len(viking.calls) == asked

        # Viking not answering: the copy is used as it is.
        viking.down = True
        before = list(loads.calls)
        load()
        assert loads.calls == before
    finally:
        svc.close()


def test_an_offline_service_never_asks(i943_path, monkeypatch):
    import sbfsem.service.facade as facade_mod

    viking = _Viking("2030-01-01T00:00:00Z", own_count=1)
    loads = _Loads(i943_path, viking)
    monkeypatch.setattr(facade_mod, "NeuronLoader", loads)
    svc = SbfsemService(offline=True)
    svc.client = lambda source: viking
    try:
        svc.load_neuron(943, "i", section_corrections="off", live_registration=False)
        svc.load_neuron(943, "i", section_corrections="off", live_registration=False)
        assert loads.calls == ["cached"] and viking.calls == []
    finally:
        svc.close()


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
    """A disk cache of surfaces by key, remembering what was asked and stored."""

    def __init__(self, held: dict | None = None):
        self.held = dict(held or {})
        self.gets: list[tuple] = []
        self.puts: list[tuple] = []

    def get(self, key):
        self.gets.append(key)
        return self.held.get(key)

    def put(self, key, mesh):
        self.puts.append(key)
        self.held[key] = mesh


def test_the_disk_key_names_the_links_and_no_older_key_is_read(service, c943):
    """A link made or removed between annotations that exist moves neither the
    stamp nor the count, so a cell refreshed after its missing link was added in
    Viking -- what the user guide tells an annotator to do about a slit -- was
    served its surface from before.  The keys used before (without the links, and
    the one before 2026-10-15) say nothing of the links: never read again."""
    svc, neuron = service
    neuron.link_signature = (188, LINKED)
    disk = FakeDisk()
    svc._disk_meshes = disk
    first = svc.render_mesh(943, "i", method="cylinder")
    (stored,) = disk.puts
    assert ("links", 188, LINKED.isoformat()) in stored
    assert all("-07:00" not in repr(k) for k in disk.gets), "the legacy key is not asked for"
    assert len(disk.gets) == 1

    # The same links on another load: the surface on disk.
    svc._meshes.clear()
    svc._build_mesh = lambda *a, **k: (_ for _ in ()).throw(AssertionError("rebuilt"))
    assert svc.render_mesh(943, "i", method="cylinder").n_faces == first.n_faces

    # A link made in Viking: another key, so the surface is built again.
    del svc._build_mesh
    svc._meshes.clear()
    neuron.load_serial = "after-the-link"
    neuron.link_signature = (189, datetime(2026, 11, 19, 22, 2, 38, tzinfo=UTC))
    svc.render_mesh(943, "i", method="cylinder")
    assert len(disk.puts) == 2 and ("links", 189, "2026-11-19T22:02:38+00:00") in disk.puts[1]


def test_the_summary_tokens_follow_what_the_copy_was_built_from(service):
    """The page keys its surfaces on ``surface_token`` and all it computes from the
    cell on ``viking_token``: the stamp did not move for a synapse added or retagged
    or a link made, and an Update left the Morphology card as it was."""
    svc, neuron = service
    neuron.link_signature = (188, LINKED)
    neuron.children_loaded = True
    neuron.children_stamp = parse_datetime("2026-11-19T22:50:05.367Z")
    first = svc.neuron_detail(943, "i").summary
    assert len(first.surface_token) == len(first.viking_token) == 12

    neuron.children_stamp = parse_datetime("2026-11-20T09:00:00Z")      # a synapse retagged
    retagged = svc.neuron_detail(943, "i").summary
    assert retagged.surface_token == first.surface_token, "a synapse is not the surface"
    assert retagged.viking_token != first.viking_token

    neuron.link_signature = (189, LINKED)                                   # a link made
    linked = svc.neuron_detail(943, "i").summary
    assert linked.surface_token != first.surface_token
    assert linked.viking_token != retagged.viking_token

    neuron.data_stamp = None                                                # a file import
    bare = svc.neuron_detail(943, "i").summary
    assert bare.surface_token == bare.viking_token == ""


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


def test_the_bulk_swc_archive_checks_each_cell_against_viking(i943_path, monkeypatch):
    """A cell not open in the page went into the archive as first fetched, for
    good: the loader read the disk with no check (2026-11-20)."""
    import sbfsem.service.facade as facade_mod

    viking = _Viking(LIVE, own_count=190, links=(188, LINKED))
    loads = _Loads(i943_path, viking)
    monkeypatch.setattr(facade_mod, "NeuronLoader", loads)
    svc = SbfsemService(offline=True)
    svc.offline = False                      # Viking is the fake
    svc.client = lambda source: viking
    try:
        archive = lambda: svc.swc_archive([943], "i", section_corrections="off",  # noqa: E731
                                          live_registration=False)
        archive()
        assert loads.calls == ["cached"], "the disk's copy, current in Viking"
        viking.links = (189, datetime(2026, 11, 19, 22, 2, 38, tzinfo=UTC))   # a link made
        archive()
        assert loads.calls[-2:] == ["cached", "fresh"], "checked, and fetched again"
    finally:
        svc.close()
