"""Which Viking answers the client may reuse (2026-11-20).

Inferior c51578's Linked neurons report showed four partners unlabeled and five
synapses "unlinked" for a day, through reloads and its own refresh button: the
StructureLinks and label queries were answered from the disk cache, where an
answer was kept for good.  Every query method now has a declared policy
(``QUERY_POLICIES``): ``live`` ones ask Viking every time and use the disk only
when Viking cannot answer, saying so; ``checked`` ones are a cell's own rows,
kept and checked against Viking by the caller.  These tests hold each method to
its policy and fail for a method that has none.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import httpx
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.odata import LIVE, QUERY_POLICIES, ODataClient, ODataError

#: What the client is besides its queries: its lifecycle and its primitives.
NOT_QUERIES = {"fresh", "bypassing_cache", "answers_from_before", "base_url", "get_url",
               "client", "close", "get", "get_collection"}

#: One call of each query method, with arguments that reach Viking.
WHEN = datetime(2026, 11, 20, tzinfo=UTC)
CALLS = {
    "ids_by_label": lambda c: c.ids_by_label("a"),
    "resolve_label": lambda c: c.resolve_label("a"),
    "query_labels": lambda c: c.query_labels(),
    "structures_by_type": lambda c: c.structures_by_type(3),
    "annotations_by_section": lambda c: c.annotations_by_section([1, 2]),
    "get_label": lambda c: c.get_label(1),
    "get_labels": lambda c: c.get_labels([1, 2]),
    "get_structures_brief": lambda c: c.get_structures_brief([1]),
    "get_linked_ids": lambda c: c.get_linked_ids(5),
    "get_location": lambda c: c.get_location(5),
    "get_usernames": lambda c: c.get_usernames(1),
    "get_annotation_info": lambda c: c.get_annotation_info([1]),
    "get_last_annotations": lambda c: c.get_last_annotations(1),
    "get_last_modified_annotation": lambda c: c.get_last_modified_annotation(1),
    "get_user_last_annotations": lambda c: c.get_user_last_annotations("u"),
    "get_user_last_structures": lambda c: c.get_user_last_structures("u"),
    "get_geometry_types": lambda c: c.get_geometry_types(1),
    "fetch_location_provenance": lambda c: c.fetch_location_provenance(1),
    "fetch_location_provenance_by_parents": lambda c: c.fetch_location_provenance_by_parents([1]),
    "fetch_child_provenance": lambda c: c.fetch_child_provenance(1),
    "get_contributions": lambda c: c.get_contributions([1]),
    "fetch_structure_links": lambda c: c.fetch_structure_links([1]),
    "fetch_links_by_parent_structures": lambda c: c.fetch_links_by_parent_structures([1]),
    "location_link_signature": lambda c: c.location_link_signature(1),
    "count_rewrites": lambda c: c.count_rewrites(WHEN, WHEN, WHEN),
    "structure_freshness": lambda c: c.structure_freshness([1]),
    "count": lambda c: c.count(f"{c.base_url}Structures"),
    "count_cells_in_volume": lambda c: c.count_cells_in_volume(),
    "count_locations_in_volume": lambda c: c.count_locations_in_volume(),
    "get_structure": lambda c: c.get_structure(1),
    "fetch_locations": lambda c: c.fetch_locations(1),
    "fetch_location_links": lambda c: c.fetch_location_links(1),
    "fetch_children": lambda c: c.fetch_children(1),
    "fetch_closed_curves": lambda c: c.fetch_closed_curves(1),
    "fetch_locations_by_parents": lambda c: c.fetch_locations_by_parents([1]),
    "fetch_links_by_parents": lambda c: c.fetch_links_by_parents([1]),
    "get_scale": lambda c: c.get_scale(),
    "scale_answer": lambda c: c.scale_answer(),
}

ROW = {"ID": 1, "Label": "x", "TypeID": 1, "ParentID": 2, "TypeCode": 1, "Username": "u",
       "LastModified": "2026-11-19T22:02:38.493Z", "Created": "2026-11-19T22:02:38.493Z",
       "SourceID": 1, "TargetID": 2, "A": 1, "B": 2,
       "Source": {"ID": 1, "ParentID": 7, "TypeID": 34}, "Target": {"ID": 2, "ParentID": 9, "TypeID": 35}}


def _viking(calls: list[str], *, down: list[bool] | None = None):
    """A Viking that answers anything, counting what it is asked; ``down[0]`` set
    answers 503 instead."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url}")
        if down and down[0]:
            return httpx.Response(503)
        if request.method == "POST":
            return httpx.Response(200, json={"value": [{"StructureID": 1, "LastModified": ROW["LastModified"],
                                                        "AnnotationCount": 3}]})
        if "/$count" in request.url.path:
            return httpx.Response(200, text="﻿7")
        return httpx.Response(200, json={
            **ROW, "value": [ROW], "@odata.count": 1,
            "X": {"Value": 2.18}, "Y": {"Value": 2.18}, "Z": {"Value": 70.0},
        })
    return handler


def _client(tmp_path, calls, **kw) -> ODataClient:
    http = httpx.Client(transport=httpx.MockTransport(_viking(calls, **kw)))
    return ODataClient("i", client=http, cache=DiskCache(tmp_path))


def test_every_query_method_has_a_policy_and_every_policy_a_method():
    methods = {name for name, member in inspect.getmembers(ODataClient)
               if not name.startswith("_") and (callable(member) or isinstance(member, property))
               and name.islower()}
    queries = methods - NOT_QUERIES
    assert queries - set(QUERY_POLICIES) == set(), "a query method with no declared policy"
    assert set(QUERY_POLICIES) - queries == set(), "a policy for a method that is gone"
    assert set(QUERY_POLICIES.values()) <= {"live", "checked", "fixed"}
    assert set(CALLS) == set(QUERY_POLICIES), "each method is exercised below"


@pytest.mark.parametrize("name", sorted(n for n, p in QUERY_POLICIES.items() if p == "live"))
def test_a_live_question_is_asked_of_viking_every_time(tmp_path, name):
    calls: list[str] = []
    client = _client(tmp_path, calls)
    CALLS[name](client)
    first = len(calls)
    assert first > 0
    CALLS[name](client)
    assert len(calls) == 2 * first, f"{name} answered its second call from the disk"


@pytest.mark.parametrize("name", sorted(n for n, p in QUERY_POLICIES.items() if p == "checked"))
def test_a_cell_s_own_rows_are_kept_for_the_caller_to_check(tmp_path, name):
    calls: list[str] = []
    client = _client(tmp_path, calls)
    CALLS[name](client)
    first = len(calls)
    CALLS[name](client)
    assert len(calls) == first, f"{name} is kept; the caller checks it against Viking"
    with client.fresh():
        CALLS[name](client)
    assert len(calls) == 2 * first, "and asks again past the disk when the check says so"


@pytest.mark.parametrize("name", ["fetch_structure_links", "get_labels", "get_structures_brief",
                                  "fetch_links_by_parent_structures", "query_labels"])
def test_a_live_question_viking_cannot_answer_is_answered_from_the_disk_and_named(tmp_path, name):
    calls: list[str] = []
    down = [False]
    client = _client(tmp_path, calls, down=down)
    before = CALLS[name](client)
    down[0] = True
    with client.answers_from_before() as old:
        assert CALLS[name](client) == before
    assert old, "the caller hears the answer is not current"
    assert all(a.stored_at is not None for a in old)
    # Nothing stored for it at all: the failure is the caller's to see.
    fresh_disk = _client(tmp_path / "empty", calls, down=down)
    with pytest.raises(ODataError):
        CALLS[name](fresh_disk)


def test_the_batches_of_one_query_are_asked_side_by_side_inside_fresh_and_named(tmp_path):
    """The batches run on other threads: a ``fresh()`` and a collector of old answers
    must reach them, or a refresh would read the disk and a fallback pass unnoticed."""
    calls: list[str] = []
    down = [False]
    client = _client(tmp_path, calls, down=down)
    ids = list(range(1, 4 * client.IN_BATCH_SIZE + 1))
    client.fetch_locations_by_parents(ids)
    assert len(calls) == 4
    client.fetch_locations_by_parents(ids)
    assert len(calls) == 4, "checked: from the disk"
    with client.fresh():
        client.fetch_locations_by_parents(ids)
    assert len(calls) == 8, "every batch past the disk, on whichever thread asked it"
    down[0] = True
    with client.answers_from_before() as old, pytest.raises(ODataError):
        client.get_labels(ids)            # never stored: fails
    assert old == []
    down[0] = False
    client.get_labels(ids)
    down[0] = True
    with client.answers_from_before() as old:
        client.get_labels(ids)
    assert len(old) == 4, "each batch's fallback named to the thread that asked"


def test_the_link_signature_is_the_count_and_the_newest_link(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"@odata.count": 571, "value": [
            {"A": 2029207, "B": 2029208, "Created": "2026-10-28T21:25:41.3333333Z"}]})

    client = ODataClient("i", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cache=DiskCache(tmp_path))
    count, newest = client.location_link_signature(51578)
    assert count == 571
    assert newest == datetime(2026, 10, 28, 21, 25, 41, 333333, tzinfo=UTC)
    query = str(seen[0].url)
    assert "LocationA/ParentID eq 51578" in httpx.URL(query).params["$filter"]
    assert "$count=true" in query and "$top=1" in query

    def no_count(request):
        return httpx.Response(200, json={"value": []})

    bare = ODataClient("i", client=httpx.Client(transport=httpx.MockTransport(no_count)),
                       cache=DiskCache(tmp_path))
    with pytest.raises(ODataError):
        bare.location_link_signature(1)
    with pytest.raises(ODataError):
        ODataClient("i", cache=DiskCache(tmp_path), offline=True).location_link_signature(1)


def test_live_is_zero():
    assert LIVE == 0


def test_a_connection_viking_dropped_is_asked_again_once_on_a_new_one(tmp_path):
    """Production, right after a deploy: both StructureLinks requests of c51578's
    report failed with ``[SSL: UNEXPECTED_EOF_WHILE_READING]`` on keep-alive
    connections Viking had closed, and the report was answered from the disk."""
    seen: list[str] = []
    drops = {"left": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if drops["left"]:
            drops["left"] -= 1
            raise httpx.ReadError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred", request=request)
        if request.method == "POST":
            return httpx.Response(200, json={"value": [{"StructureID": 1, "AnnotationCount": 3,
                                                        "LastModified": ROW["LastModified"]}]})
        return httpx.Response(200, json={"value": [ROW]})

    client = ODataClient("i", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cache=DiskCache(tmp_path))
    client.cache.set(f"{client.base_url}Structures(1)?$select=Label", {"Label": "old"})
    with client.answers_from_before() as old:
        assert client.get_labels([1]) == {1: "x"}
    assert old == [] and seen == ["GET", "GET"], "asked again, and the live answer used"

    drops["left"] = 2                     # dropped twice: the stored answer, named
    with client.answers_from_before() as old:
        assert client.get_labels([1]) == {1: "x"}
    assert len(old) == 1
    drops["left"] = 1
    assert client.structure_freshness([1]), "the freshness POST is asked again too"

    # A timeout is not asked again: it already waited the whole timeout.
    asked: list[str] = []

    def slow(request):
        asked.append(str(request.url))
        raise httpx.ReadTimeout("timed out", request=request)

    patient = ODataClient("i", client=httpx.Client(transport=httpx.MockTransport(slow)),
                          cache=DiskCache(tmp_path / "slow"))
    with pytest.raises(ODataError):
        patient.get_label(1)
    assert len(asked) == 1
