"""The OData client, the disk cache and the loader after the 2026-10-15 audit (W10).

Percent-encoded label terms, the HTTP status an ``ODataError`` carries, lists
that expire, a cache that survives two writers, and a loader that does not ask
Viking for the closed curves it already holds.
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import parse_qs, unquote

import httpx
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.loader import NeuronLoader
from sbfsem.io.odata import ODataClient, ODataError, _quote


def _client(handler, tmp_path, *, cache=True, offline=False):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ODataClient("i", client=http, cache=DiskCache(tmp_path, enabled=cache),
                       offline=offline)


def _age(cache, url, seconds):
    """Make a cached answer ``seconds`` old (every answer when ``url`` is None)."""
    old = time.time() - seconds
    paths = list(cache.directory.rglob("*.json")) if url is None else [cache._path(url)]
    for path in paths:
        os.utime(path, (old, old))


def _counting_client(tmp_path, answers):
    """Answers the nth request with ``answers[n]`` (a status code or a JSON body)."""
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        answer = answers[min(len(calls), len(answers)) - 1]
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json=answer)

    client = _client(handler, tmp_path)
    return client, client.cache, calls


# -- encoding -----------------------------------------------------------------
def test_label_terms_are_percent_encoded_where_the_query_string_needs_it(tmp_path):
    """``&``, ``#``, ``%``, ``+`` and ``;`` ended or rewrote the value: a search
    for ``R&D`` asked for ``R``, and a label with a ``%`` could not be found."""
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"value": []})

    client = _client(handler, tmp_path, cache=False)
    label = "R&D #1 50% a+b; O'Brien"
    client.ids_by_label(label)
    client.ids_by_label(label, exact_match=False)
    client.resolve_label(label)
    client.get_user_last_annotations(label)
    client.get_user_last_structures(label)
    assert len(seen) == 5
    for request in seen:
        # What the server does: split the raw query on '&', decode each value.
        query = parse_qs(request.url.query.decode("ascii"), keep_blank_values=True)
        value = query["$filter"][0].lower()
        assert "r&d #1 50% a+b; o''brien" in value
        assert len(query["$filter"]) == 1


def test_an_ordinary_label_keeps_its_url():
    """Spaces and apostrophes stay as they were, so the disk cache's keys for
    ordinary searches do not change."""
    assert _quote("ON mBC cell") == "ON mBC cell"
    assert _quote("O'Brien") == "O''Brien"
    assert _quote("a&b#c%d+e;f") == "a%26b%23c%25d%2Be%3Bf"


def test_the_label_search_keeps_the_url_the_disk_cache_already_holds(tmp_path):
    """Selecting TypeID too would fill SearchResultDTO.type_id, which the viewer never
    reads, and re-key every cached search (an offline server's Find would miss)."""
    seen = []

    def handler(request):
        seen.append(unquote(str(request.url)))
        return httpx.Response(200, json={"value": []})

    client = _client(handler, tmp_path, cache=False)
    client.ids_by_label("a")
    client.ids_by_label("a", exact_match=False)
    assert all(url.endswith("&$select=ID,Label") for url in seen)


# -- the status ---------------------------------------------------------------
def test_odata_error_carries_the_http_status(tmp_path):
    """A structure that does not exist (404) and a gateway that gave up (504)
    read the same once the status was dropped."""
    for status in (404, 504):
        def handler(request, status=status):
            return httpx.Response(status, text="no")

        with pytest.raises(ODataError) as raised:
            _client(handler, tmp_path, cache=False).get("https://x/y")
        assert raised.value.status == status
        assert f"HTTP {status}" in str(raised.value)

    def refuses(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ODataError) as raised:
        _client(refuses, tmp_path, cache=False).get("https://x/y")
    assert raised.value.status is None
    assert ODataError("plain").status is None


def test_the_live_only_queries_carry_the_status_too(tmp_path):
    """``count_rewrites`` and ``structure_freshness`` make their own requests, outside
    ``get``: a mutant that dropped the status from either left every test green."""
    from datetime import UTC, datetime

    for status in (404, 504):
        def handler(request, status=status):
            return httpx.Response(status, text="no")

        client = _client(handler, tmp_path, cache=False)
        when = datetime(2026, 10, 15, tzinfo=UTC)
        with pytest.raises(ODataError) as raised:
            client.count_rewrites(when, when, when)
        assert raised.value.status == status and f"HTTP {status}" in str(raised.value)
        with pytest.raises(ODataError) as raised:
            client.structure_freshness([1178])
        assert raised.value.status == status and f"HTTP {status}" in str(raised.value)


def test_annotation_info_is_the_utc_instant_whatever_label_the_row_wears(tmp_path):
    """Viking writes UTC digits and labels them Pacific (-07:00), now Z: a row cached under
    the old label read as 7 h later than the live one, read with its label."""
    labels = {1: "2026-10-14T05:31:15.223-07:00", 2: "2026-10-14T05:31:15.223Z"}

    def handler(request):
        sid = int(str(request.url).split("Structures(")[1].split(")")[0])
        return httpx.Response(200, json={"Username": " sara ", "LastModified": labels[sid]})

    info = _client(handler, tmp_path, cache=False).get_annotation_info([1, 2])
    assert [row["LastModified"].isoformat() for row in info] == ["2026-10-14T05:31:15.223000+00:00"] * 2
    assert [row["Username"] for row in info] == ["sara", "sara"]


# -- lists that expire --------------------------------------------------------
def test_the_label_lists_are_asked_again_after_an_hour(tmp_path):
    """A cache entry never expired: a vessel added or a cell labeled after the
    first search was not found until someone cleared the disk."""
    client, cache, calls = _counting_client(
        tmp_path, [{"value": [{"ID": 1, "Label": "a"}]},
                   {"value": [{"ID": 1, "Label": "a"}, {"ID": 2, "Label": "ab"}]}])
    assert [r["ID"] for r in client.ids_by_label("a", exact_match=False)] == [1]
    assert [r["ID"] for r in client.ids_by_label("a", exact_match=False)] == [1]
    assert len(calls) == 1                        # young: from the disk
    _age(cache, None, client.LIST_MAX_AGE + 60)
    assert [r["ID"] for r in client.ids_by_label("a", exact_match=False)] == [1, 2]
    assert len(calls) == 2                        # old: asked again


@pytest.mark.parametrize("call", [
    lambda c: c.ids_by_label("a"),
    lambda c: c.resolve_label("a"),
    lambda c: c.query_labels(),
    lambda c: c.structures_by_type(3),
])
def test_every_list_that_grows_expires(tmp_path, call):
    client, cache, calls = _counting_client(tmp_path, [{"value": [{"ID": 1}]}])
    call(client)
    call(client)
    assert len(calls) == 1
    _age(cache, None, client.LIST_MAX_AGE + 60)
    call(client)
    assert len(calls) == 2


def test_a_cell_s_own_rows_are_still_cached_for_good(tmp_path):
    client, cache, calls = _counting_client(tmp_path, [{"value": [{"ID": 1}]}])
    client.fetch_locations(5)
    _age(cache, None, 10 * 365 * 86400)
    client.fetch_locations(5)
    assert len(calls) == 1


def test_an_expired_list_is_kept_when_viking_cannot_be_reached(tmp_path):
    """Better the old answer than none: a service that is down must not take
    Find with it."""
    client, cache, calls = _counting_client(
        tmp_path, [{"value": [{"ID": 1, "Label": "a"}]}, 503])
    client.ids_by_label("a")
    _age(cache, None, client.LIST_MAX_AGE + 60)
    assert [r["ID"] for r in client.ids_by_label("a")] == [1]
    assert len(calls) == 2                        # it did try
    # An offline client answers from what it holds, however old.
    offline = ODataClient("i", cache=cache, offline=True)
    assert [r["ID"] for r in offline.ids_by_label("a")] == [1]


def test_a_list_is_not_assembled_from_two_moments(tmp_path):
    """Once page 1 had to be asked again, page 2 is too, though its own entry
    is young."""
    first = {"value": [{"ID": 1}], "@odata.nextLink": "https://x/Structures?page=2"}
    second = {"value": [{"ID": 2}]}
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        page = request.url.params.get("page")
        return httpx.Response(200, json=second if page == "2" else first)

    client = _client(handler, tmp_path)
    url = f"{client.base_url}Structures?$filter=Label ne null"
    assert [r["ID"] for r in client.get_collection(url, max_age=3600)] == [1, 2]
    assert len(calls) == 2
    _age(client.cache, url, 7200)                 # page 1 only; page 2 is young
    client.get_collection(url, max_age=3600)
    assert len(calls) == 4


def _two_page_list(tmp_path):
    """A two-page list whose rows change with ``state['moment']``, and whose pages can be made to fail."""
    state = {"moment": 1, 1: 200, 2: 200}
    second_url = "https://x/Structures?page=2"

    def handler(request):
        page = 2 if request.url.params.get("page") == "2" else 1
        if state[page] != 200:
            return httpx.Response(state[page])
        moment = state["moment"]
        if page == 2:
            return httpx.Response(200, json={"value": [{"ID": 3 if moment == 1 else 9}, {"ID": 4}]})
        return httpx.Response(200, json={"value": [{"ID": 1}, {"ID": 2 if moment == 1 else 8}],
                                         "@odata.nextLink": second_url})

    client = _client(handler, tmp_path)
    url = f"{client.base_url}Structures?$filter=Label ne null"
    return client, url, state


@pytest.mark.parametrize("failing_page", [2, 1])
def test_a_list_is_not_assembled_from_two_moments_when_a_page_fails(tmp_path, failing_page):
    """The docstring says a list is never assembled from two moments.  Page 1 was asked
    again, page 2 failed, and the older page 2 was joined to the new page 1 ([1, 8, 3, 4]);
    the other way round (page 1 unreachable, page 2 answering) was as bad.  An answer is
    one moment, whole: here the older one, and the cache keeps no mixture for the next ask."""
    client, url, state = _two_page_list(tmp_path)
    ids = lambda rows: [r["ID"] for r in rows]                 # noqa: E731 - a one-line helper
    assert ids(client.get_collection(url, max_age=3600)) == [1, 2, 3, 4]       # moment 1
    _age(client.cache, None, 7200)
    state.update(moment=2)
    state[failing_page] = 503                                   # Viking changed, and one page fails
    older = [1, 2, 3, 4]
    assert ids(client.get_collection(url, max_age=3600)) == older
    assert ids(client.get_collection(url, max_age=3600)) == older, "and again: no mixture was kept"
    state[failing_page] = 200                                   # Viking recovers: one whole new moment
    assert ids(client.get_collection(url, max_age=3600)) == [1, 8, 9, 4]
    assert ids(client.get_collection(url, max_age=3600)) == [1, 8, 9, 4]       # now young, from the disk


def test_a_list_with_no_older_answer_to_fall_back_on_fails_as_it_did(tmp_path):
    client, url, state = _two_page_list(tmp_path)
    state[2] = 503
    with pytest.raises(ODataError) as raised:
        client.get_collection(url, max_age=3600)
    assert raised.value.status == 503
    # Nothing half-way was kept for the next ask.
    assert not list(client.cache.directory.rglob("*.json"))


# -- the disk cache -----------------------------------------------------------
def test_disk_cache_max_age(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("u", {"a": 1})
    assert cache.get("u", max_age=60) == {"a": 1}
    _age(cache, "u", 120)
    assert cache.get("u", max_age=60) is None
    assert cache.get("u") == {"a": 1}             # no limit asked, none applied
    assert DiskCache(tmp_path, ttl=60).get("u") is None
    assert DiskCache(tmp_path, ttl=60).get("u", max_age=600) == {"a": 1}


def test_two_writers_of_one_url_never_share_a_temporary_file(tmp_path, monkeypatch):
    """Deterministic: the name each writer replaced the target from.  One shared name
    is the defect, whether or not a race happens to tear a file on this run."""
    from pathlib import Path

    cache = DiskCache(tmp_path)
    used: list[str] = []
    real_replace = Path.replace

    def spy(self, target):
        used.append(self.name)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy)
    for i in range(5):
        cache.set("same", {"i": i})
    assert len(used) == 5 and all(name.endswith(".tmp") for name in used)
    assert len(set(used)) == 5, "writers of one URL shared a temporary file"
    assert cache.get("same") == {"i": 4}


def test_concurrent_writers_of_one_url_never_expose_a_torn_file(tmp_path):
    """Writers of one URL shared one temporary file: the first to finish moved
    the second's half-written bytes into place.  The payloads differ in length, so
    bytes of two of them mixed are not the JSON of either (six payloads of one length
    could be: this test passed against the shared name in 20 of 20 runs), and the
    reader reads the file itself, since ``DiskCache.get`` maps invalid JSON to a miss."""
    import json

    cache = DiskCache(tmp_path)
    payloads = [{"who": i, "pad": "x" * (40_000 * (i + 1))} for i in range(6)]
    path = cache._path("same")
    stop = threading.Event()
    torn: list[object] = []

    def write(payload):
        for _ in range(30):
            cache.set("same", payload)

    def read():
        while not stop.is_set():
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue                     # before the first write
            try:
                got = json.loads(text)["data"]
            except json.JSONDecodeError:
                torn.append(len(text))
                continue
            if got not in payloads:
                torn.append(got)

    reader = threading.Thread(target=read)
    reader.start()
    writers = [threading.Thread(target=write, args=(p,)) for p in payloads]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    reader.join()
    assert torn == []
    assert cache.get("same") in payloads
    assert list(cache.directory.rglob("*.tmp")) == []


# -- the loader ---------------------------------------------------------------
def test_closed_curves_are_not_asked_for_twice(tmp_path):
    """``load`` holds every Location row, closed curves included: the closed-curve
    query returned the same rows in the same order (c1178, c192, c2542), so the
    loader filters them instead of asking Viking again."""
    urls: list[str] = []
    rows = [
        {"ID": 1, "ParentID": 9, "Z": 5, "TypeCode": 1, "Radius": 3.0, "X": 1.0, "Y": 1.0,
         "VolumeX": 1.0, "VolumeY": 1.0},
        {"ID": 2, "ParentID": 9, "Z": 6, "TypeCode": 6, "Radius": 3.0, "X": 1.0, "Y": 1.0,
         "VolumeX": 1.0, "VolumeY": 1.0,
         "VolumeShape": {"Geometry": {"WellKnownText": "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))"}}},
    ]

    def handler(request):
        url = str(request.url)
        urls.append(url)
        if "TypeCode eq 6" in unquote(url):
            return httpx.Response(200, json={"value": [rows[1]]})
        if "Structures(9)?" in url or url.endswith("Structures(9)/"):
            return httpx.Response(200, json={"ID": 9, "TypeID": 1})
        if "/Scale" in url:
            return httpx.Response(200, json={"X": {"Value": 7.5}, "Y": {"Value": 7.5},
                                             "Z": {"Value": 90}})
        if "Locations/" in url:
            return httpx.Response(200, json={"value": rows})
        return httpx.Response(200, json={"value": []})

    neuron = NeuronLoader("i", client=_client(handler, tmp_path, cache=False)).load(9)
    assert urls and not any("TypeCode eq 6" in unquote(u) for u in urls)
    assert neuron.geometries is not None and len(neuron.geometries) == 1
    assert int(neuron.geometries["ID"].iloc[0]) == 2
    assert bool(neuron.geometries["Registered"].iloc[0])


@pytest.mark.parametrize("rows", [
    [{"ID": 1}],                                                 # no type code at all
    [{"ID": 1, "TypeCode": 1}],                                  # no closed curve among them
    [{"ID": 2, "TypeCode": 6, "Z": 6, "ParentID": 9}],           # a closed curve without its shape
])
def test_the_loader_asks_when_the_rows_it_holds_cannot_stand_in(tmp_path, rows):
    """A list whose closed curves lack their shapes (a thinned test cassette, a
    server that stops sending them) is not trusted: the closed-curve query is made,
    or a cell would silently lose its outlines."""
    urls: list[str] = []

    def handler(request):
        url = str(request.url)
        urls.append(url)
        return httpx.Response(200, json={"value": []})

    loader = NeuronLoader("i", client=_client(handler, tmp_path, cache=False))
    assert loader.fetch_geometries(9, (7.5, 7.5, 90.0), rows) is None
    assert any("TypeCode eq 6" in unquote(u) for u in urls)
