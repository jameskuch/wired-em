"""OData client, WKT parsing, tag parsing and the disk cache."""

from __future__ import annotations

import httpx
import numpy as np
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.loader import InvalidStructureError, NeuronLoader
from sbfsem.io.odata import ODataClient, ODataError, odata_url, parse_datetime
from sbfsem.io.tags import parse_tag, parse_tags
from sbfsem.io.wkt import parse_closed_curve


# -- URLs ------------------------------------------------------------------
def test_odata_urls_include_the_volume():
    """F029: OData.baseURL omitted the volume segment entirely."""
    assert odata_url(127, "i", "location").endswith(
        "NeitzInferiorMonkey/OData/Structures(127)/Locations/"
    )
    assert odata_url(127, "i", "link").endswith("LocationLinks/?$select=A,B")
    assert "NeitzNM" in odata_url(127, "n", "child")
    assert odata_url(None, "i", "scale").endswith("/Scale")
    assert odata_url(None, "i", "").endswith("/OData/")


def test_parse_datetime():
    assert parse_datetime("2018-02-26T02:09:26.757-07:00").year == 2018
    assert parse_datetime("2017-03-19T22:56:37.6531234-06:00").microsecond == 653123
    assert parse_datetime(None) is None
    assert parse_datetime("nonsense") is None


# -- pagination ------------------------------------------------------------
def _client(handler, tmp_path):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return ODataClient("i", client=http, cache=DiskCache(tmp_path, enabled=False))


def test_resolve_label_uses_the_portal_filter_and_pages(tmp_path):
    """Label terms resolve exactly as the Viking ExportPortal does.

    Case-insensitive substring via ``contains(tolower(Label),...)``, parents
    only via ``ParentID eq null``, ordered by ID — and the continuation link
    is followed, so a term matching more than one server page loses nothing.
    """
    seen_urls = []
    pages = {
        None: {"value": [{"ID": 138, "Label": "CBa"}],
               "@odata.nextLink": "https://x/Structures?page=2"},
        "2": {"value": [{"ID": 142, "Label": "CBb3n"}]},
    }

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=pages[request.url.params.get("page")])

    rows = _client(handler, tmp_path).resolve_label("cB")
    assert [r["ID"] for r in rows] == [138, 142]
    first = seen_urls[0]
    assert "contains(tolower(Label),'cb')" in first.replace("%20", " ").replace(
        "%28", "(").replace("%29", ")").replace("%27", "'").replace("%2C", ",")
    assert "ParentID eq null" in first.replace("%20", " ")
    assert "$orderby=ID" in first


def test_get_collection_follows_next_link(tmp_path):
    """Nothing in the MATLAB client followed @odata.nextLink."""
    pages = {
        "1": {"value": [{"ID": 1}, {"ID": 2}], "@odata.nextLink": "https://x/p?page=2"},
        "2": {"value": [{"ID": 3}]},
    }

    def handler(request):
        page = request.url.params.get("page", "1")
        return httpx.Response(200, json=pages[page])

    client = _client(handler, tmp_path)
    rows = client.get_collection("https://x/p?page=1")
    assert [r["ID"] for r in rows] == [1, 2, 3]


def test_pagination_loop_is_broken(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"value": [{"ID": 1}], "@odata.nextLink": str(request.url)})

    rows = _client(handler, tmp_path).get_collection("https://x/p")
    assert len(rows) == 1  # not an infinite loop


def test_single_entity_response(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"ID": 7, "TypeID": 1})

    rows = _client(handler, tmp_path).get_collection("https://x/Structures(7)")
    assert rows == [{"ID": 7, "TypeID": 1}]


def test_batched_parent_query_chunks(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"value": [{"ID": 1, "ParentID": 2}]})

    client = _client(handler, tmp_path)
    client.fetch_locations_by_parents(range(150), batch_size=60)
    assert len(seen) == 3  # 150 IDs in three requests, not 150


def test_label_filter_is_escaped(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"value": []})

    _client(handler, tmp_path).ids_by_label("O'Brien cell")
    assert "O''Brien" in seen[0].replace("%27", "'")


def test_a_label_with_url_characters_reaches_the_filter_whole(tmp_path):
    """A label search put its text in the query string as it was: an & ended the
    $filter, a # ended the URL and a + read as a space."""
    label = "A&B #1 + 50% O'Brien"
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"value": []})

    client = _client(handler, tmp_path)
    client.ids_by_label(label)
    client.ids_by_label(label, exact_match=False)
    client.resolve_label(label)
    client.get_user_last_annotations(label)
    exact, contains, resolved, by_user = seen[:4]
    assert by_user.url.params["$filter"] == "Username eq 'A&B #1 + 50% O''Brien'"
    assert exact.url.params["$filter"] == "Label eq 'A&B #1 + 50% O''Brien'"
    assert contains.url.params["$filter"] == "contains(Label,'A&B #1 + 50% O''Brien')"
    assert resolved.url.params["$filter"] == (
        "contains(tolower(Label),'a&b #1 + 50% o''brien') and ParentID eq null")
    assert not any(request.url.fragment for request in seen)
    # An ordinary label is the URL it always was, so its cache entry still answers.
    seen.clear()
    client.ids_by_label("Starburst amacrine")
    assert "$filter=Label eq 'Starburst amacrine'" in str(seen[0].url).replace("%20", " ")


def test_http_errors_become_odata_errors(tmp_path):
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(ODataError):
        _client(handler, tmp_path).get("https://x/y")


def test_scale_falls_back_to_the_cache(tmp_path):
    def handler(request):
        return httpx.Response(503)

    assert _client(handler, tmp_path).get_scale() == (7.5, 7.5, 90.0)


def test_nasal_scale_override_is_preserved():
    """getODataScale hard-coded [5 5 50] for the Nasal volume."""
    client = ODataClient("n", offline=True)
    assert client.get_scale() == (5.0, 5.0, 50.0)


def test_offline_client_raises_without_a_cache_entry():
    with pytest.raises(ODataError, match="Offline"):
        ODataClient("i", offline=True).get("https://x/y")


# -- cache -----------------------------------------------------------------
def test_disk_cache_roundtrip(tmp_path):
    cache = DiskCache(tmp_path)
    assert cache.get("u") is None
    cache.set("u", {"value": [1, 2]})
    assert cache.get("u") == {"value": [1, 2]}
    assert cache.clear() == 1
    assert cache.get("u") is None


def test_disabled_cache_is_a_noop(tmp_path):
    cache = DiskCache(tmp_path, enabled=False)
    cache.set("u", {"a": 1})
    assert cache.get("u") is None


def test_client_uses_the_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"value": []})

    transport = httpx.MockTransport(handler)
    client = ODataClient("i", client=httpx.Client(transport=transport),
                         cache=DiskCache(tmp_path))
    client.get("https://x/y")
    client.get("https://x/y")
    assert len(calls) == 1


# -- WKT -------------------------------------------------------------------
def test_wkt_curve_polygon_with_holes():
    text = (
        "CURVEPOLYGON((10 20, 30 20, 30 40, 10 40, 10 20),"
        "(15 25, 20 25, 20 30, 15 25))"
    )
    rings = parse_closed_curve(text)
    # WKT rings repeat the first coordinate to close; the control points are the
    # distinct vertices, so 5 and 4 written become 4 and 3.
    assert [len(r) for r in rings] == [4, 3]
    assert rings[0][0].tolist() == [10.0, 20.0]
    assert not np.allclose(rings[0][0], rings[0][-1])


def test_wkt_polygon_is_parsed_by_shapely():
    rings = parse_closed_curve("POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))")
    assert len(rings) == 1
    assert len(rings[0]) == 4


def test_wkt_scales_to_microns():
    rings = parse_closed_curve("POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))", (7.5, 7.5, 90.0))
    assert rings[0][1].tolist() == pytest.approx([0.03, 0.0])


def test_wkt_empty_and_junk():
    assert parse_closed_curve(None) == []
    assert parse_closed_curve("") == []
    assert parse_closed_curve("CURVEPOLYGON(())") == []


# -- tags ------------------------------------------------------------------
def test_parse_tags():
    assert parse_tag('<Structure TypeName="GABA" />') == "GABA"
    assert parse_tag('<a>"Ribbon"</a><b>"Glutamate"</b>') == "Ribbon;Glutamate"
    assert parse_tag("-") == ""
    assert parse_tag(None) == ""
    # F034: an odd number of quotes made the MATLAB reshape throw.
    assert parse_tag('"odd') == "odd"
    # F061: the util/import copy dropped a row for any tag without quotes.
    assert parse_tags(["a", None, "-"]) == ["a", "", ""]


# -- loader ----------------------------------------------------------------
def test_loader_rejects_a_synapse_id(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"ID": 7233, "TypeID": 35})

    loader = NeuronLoader("i", client=_client(handler, tmp_path))
    with pytest.raises(InvalidStructureError):
        loader.fetch_viking(7233)


def test_loader_accepts_a_cell(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"ID": 943, "TypeID": 1, "Label": "ON mBC"})

    loader = NeuronLoader("i", client=_client(handler, tmp_path))
    assert loader.fetch_viking(943)["Label"] == "ON mBC"


def test_loader_drops_synapses_with_no_locations(tmp_path):
    """Viking keeps deleted synapses with a structure ID and links but no locations."""

    def handler(request):
        url = str(request.url)
        if "Children" in url:
            return httpx.Response(200, json={"value": [
                {"ID": 10, "TypeID": 35, "Tags": "", "ParentID": 1, "Label": None},
                {"ID": 11, "TypeID": 35, "Tags": "", "ParentID": 1, "Label": None},
            ]})
        if "Locations" in url and "LocationLinksA" in url:
            return httpx.Response(200, json={"value": []})
        if "Locations" in url:
            return httpx.Response(200, json={"value": [
                {"ID": 100, "ParentID": 10, "VolumeX": 1, "VolumeY": 2, "Z": 3,
                 "Radius": 4, "X": 1, "Y": 2, "OffEdge": 0, "Terminal": 0, "TypeCode": 1},
            ]})
        return httpx.Response(200, json={"value": []})

    loader = NeuronLoader("i", client=_client(handler, tmp_path))
    synapses, nodes, _edges = loader.fetch_synapses(1)
    assert synapses["ID"].tolist() == [10]
    assert len(nodes) == 1


# -- every query method ----------------------------------------------------
class _Recorder:
    """Answers any OData query and records the URLs it was asked for."""

    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, request):
        url = str(request.url)
        self.urls.append(url)
        if url.endswith("/Scale"):
            return httpx.Response(200, json={
                "X": {"Value": 7.5}, "Y": {"Value": 7.5}, "Z": {"Value": 90}
            })
        if "$count=true" in url:
            return httpx.Response(200, json={"@odata.count": 42, "value": []})
        if "?$select=Label" in url:
            return httpx.Response(200, json={"Label": " ON parasol  "})
        if "Username,LastModified" in url:
            return httpx.Response(200, json={
                "Username": "sarastokes   ",
                "LastModified": "2018-02-26T02:09:26.757-07:00",
            })
        if "Username,ID" in url:
            return httpx.Response(200, json={"value": [
                {"Username": "sarastokes   ", "ID": 1},
                {"Username": "jk ", "ID": 2},
            ]})
        if "TypeCode" in url and "$select=TypeCode" in url:
            return httpx.Response(200, json={"value": [
                {"TypeCode": 1}, {"TypeCode": 6}, {"TypeCode": 1},
            ]})
        return httpx.Response(200, json={"value": [{"ID": 1, "TypeID": 1, "Label": "x"}]})


def _recording_client(tmp_path):
    recorder = _Recorder()
    client = ODataClient(
        "i", client=httpx.Client(transport=httpx.MockTransport(recorder)),
        cache=DiskCache(tmp_path, enabled=False),
    )
    return client, recorder


def test_volume_level_queries(tmp_path):
    client, rec = _recording_client(tmp_path)
    assert client.get_scale() == (7.5, 7.5, 90.0)
    assert client.count_cells_in_volume() == 42
    assert client.count_locations_in_volume() == 42
    assert client.get_label(943) == "ON parasol"
    assert client.query_labels("x")
    assert client.annotations_by_section([1, 2, 3])
    assert client.get_geometry_types(943) == [1, 6]
    assert client.structures_by_type(246)
    assert all(u.startswith(client.base_url) for u in rec.urls)


def test_user_and_date_queries(tmp_path):
    client, rec = _recording_client(tmp_path)
    info = client.get_annotation_info([943])
    assert info[0]["Username"] == "sarastokes"
    assert info[0]["LastModified"].year == 2018
    names, ids = client.get_usernames(943)
    assert names == ["sarastokes", "jk"] and ids == [1, 2]
    assert client.get_contributions([943]) == {"sarastokes": 1, "jk": 1}
    assert client.get_last_annotations(943, 3)
    assert client.get_last_modified_annotation(943) is not None
    assert client.get_user_last_annotations("sarastokes", 5)
    assert client.get_user_last_structures("sarastokes")
    assert client.get_user_last_structures("sarastokes", 2)
    assert any("$top=2" in u for u in rec.urls)


def test_provenance_queries_select_only_the_columns_they_need(tmp_path):
    """A bare ``Locations`` row carries both geometry blobs; the annotator
    report needs five columns and asks for exactly those."""
    client, rec = _recording_client(tmp_path)
    client.fetch_location_provenance(943)
    client.fetch_child_provenance(943)
    client.fetch_location_provenance_by_parents([11, 12])
    urls = [u.replace("%20", " ").replace("%28", "(").replace("%29", ")").replace("%2C", ",")
            for u in rec.urls]
    assert any(u.endswith("Structures(943)/Locations?$select=" + client.PROVENANCE_SELECT)
               for u in urls)
    assert any("Structures?$filter=ParentID eq 943" in u and "Username" in u for u in urls)
    assert any("Locations?$filter=ParentID in (11,12)" in u
               and u.endswith(client.PROVENANCE_SELECT) for u in urls)
    # Never the shapes: they are three orders of magnitude larger.
    assert not any("VolumeShape" in u for u in urls)


def test_structure_level_queries(tmp_path):
    client, rec = _recording_client(tmp_path)
    client.fetch_locations(943)
    client.fetch_location_links(943)
    client.fetch_children(943)
    client.fetch_closed_curves(943)
    client.get_linked_ids(1)
    joined = " ".join(rec.urls)
    assert "Structures(943)/Locations/" in joined
    assert "LocationLinks/?$select=A,B" in joined
    assert "Children?$select=ID,TypeID,Tags,ParentID,Label" in joined
    # F028: the MATLAB version joined $expand with & where ? was required.
    assert "Locations(1)?$expand=" in joined


def test_get_labels_is_one_query(tmp_path):
    client, rec = _recording_client(tmp_path)
    labels = client.get_labels([1, 2, 3])
    assert labels == {1: "x"}
    assert len([u for u in rec.urls if "Structures?$filter" in u]) == 1


def test_batching_falls_back_when_in_is_refused(tmp_path):
    """Viking rejects an `or` chain past ~22 clauses and a long URL with a 404."""
    attempts: list[str] = []

    def handler(request):
        url = str(request.url)
        attempts.append(url)
        if " in (" in url.replace("%20", " ").replace("%28", "("):
            return httpx.Response(400, text="filter too complex")
        return httpx.Response(200, json={"value": [{"ID": 1, "ParentID": 2}]})

    client = ODataClient(
        "i", client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=DiskCache(tmp_path, enabled=False),
    )
    rows = client.fetch_locations_by_parents(range(30))
    assert rows  # the `or` fallback carried it
    or_calls = [u for u in attempts if "eq" in u.replace("%20", " ")]
    assert or_calls


def test_batching_falls_back_all_the_way_to_one_query_per_id(tmp_path):
    attempts: list[str] = []

    def handler(request):
        url = str(request.url).replace("%20", " ").replace("%28", "(")
        attempts.append(url)
        if " in (" in url or url.count(" eq ") > 1:
            return httpx.Response(400)
        return httpx.Response(200, json={"value": [{"ID": 1, "ParentID": 2}]})

    client = ODataClient(
        "i", client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=DiskCache(tmp_path, enabled=False),
    )
    rows = client.fetch_locations_by_parents([10, 11, 12])
    assert len(rows) == 3
    singles = [u for u in attempts if u.count(" eq ") == 1]
    assert len(singles) == 3


def test_fetch_structure_links_deduplicates(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"value": [
            {"SourceID": 1, "TargetID": 2, "Source": {"ParentID": 10},
             "Target": {"ParentID": 20}},
        ]})

    client = ODataClient(
        "i", client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=DiskCache(tmp_path, enabled=False),
    )
    # Queried once by SourceID and once by TargetID; the same link comes back twice.
    assert len(client.fetch_structure_links([1, 2])) == 1
    assert client.fetch_structure_links([]) == []


def test_client_context_manager_closes(tmp_path):
    with ODataClient("i", cache=DiskCache(tmp_path, enabled=False)) as client:
        assert client.get_url() == client.base_url
    assert client._client is None


# -- closed curves land in the volume frame ----------------------------------
def test_closed_curve_rings_land_in_the_volume_frame(tmp_path):
    """MosaicShape is the section mosaic; discs live in the registered volume.

    On InferiorMonkey c1178 the two frames sit 15 to 20 um apart, and the
    rings drawn unshifted put the soma beside the cell instead of on it.
    Each curve's own Location carries both frames, so the loader shifts its
    rings by that node's mosaic-to-volume offset.
    """

    def handler(request):
        if "Locations" in str(request.url):
            return httpx.Response(200, json={"value": [{
                "ID": 7, "ParentID": 1, "Z": 10, "TypeCode": 6, "Radius": 3.0,
                "X": 100.0, "Y": 200.0, "VolumeX": 1100.0, "VolumeY": 2200.0,
                "MosaicShape": {"Geometry": {
                    "WellKnownText": "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))"}},
            }]})
        return httpx.Response(200, json={"value": []})

    loader = NeuronLoader("i", client=_client(handler, tmp_path))
    frame = loader.fetch_geometries(1, (7.5, 7.5, 90.0))
    assert frame is not None and len(frame) == 1
    ring = np.asarray(frame["Curve"].iloc[0][0])
    # 1000 px east and 2000 px north at 7.5 nm per pixel.
    expected = np.array([[0, 0], [4, 0], [4, 4], [0, 4]]) * 0.0075 + [7.5, 15.0]
    np.testing.assert_allclose(ring, expected)
    assert not frame["Registered"].iloc[0]
    assert frame["Zum"].iloc[0] == pytest.approx(10 * 0.09)


def test_volume_frame_shift_is_zero_without_both_frames():
    from sbfsem.io.wkt import rings_in_volume_frame, volume_frame_shift_um

    scale = (7.5, 7.5, 90.0)
    assert volume_frame_shift_um({"X": 1, "Y": 2}, scale).tolist() == [0, 0]
    assert volume_frame_shift_um({"X": 1, "Y": 2, "VolumeX": None, "VolumeY": 3}, scale).tolist() == [0, 0]
    np.testing.assert_allclose(
        volume_frame_shift_um({"X": 0, "Y": 0, "VolumeX": 1000, "VolumeY": -1000}, scale),
        [7.5, -7.5],
    )
    # Scales already in microns are used as they are.
    np.testing.assert_allclose(
        volume_frame_shift_um({"X": 0, "Y": 0, "VolumeX": 2, "VolumeY": 2}, (0.5, 0.5, 0.09)),
        [1.0, 1.0],
    )
    assert rings_in_volume_frame({"ID": 1}, scale) == ([], False)
    assert rings_in_volume_frame({"MosaicShape": {"Geometry": {}}}, scale) == ([], False)


def test_registered_volume_shape_is_preferred_and_kept_as_is():
    """Viking's ``VolumeShape`` is the outline registered vertex by vertex.

    RC1 and RC2 registrations rotate and scale as well as translate, and on a
    large outline the translated mosaic polygon misses the registered one by
    several microns; the registered polygon is used whenever it is served.
    It is Viking's evaluated curve, not control points, so it is flagged for
    consumers to draw as it is.
    """
    from sbfsem.io.wkt import rings_in_volume_frame

    row = {
        "X": 100.0, "Y": 200.0, "VolumeX": 1100.0, "VolumeY": 2200.0,
        "MosaicShape": {"Geometry": {"WellKnownText": "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0))"}},
        "VolumeShape": {"Geometry": {"WellKnownText":
                                     "POLYGON((1000 2000, 1004 2001, 1003 2004, 999 2003, 1000 2000))"}},
    }
    rings, registered = rings_in_volume_frame(row, (7.5, 7.5, 90.0))
    assert registered is True
    np.testing.assert_allclose(
        rings[0], np.array([[1000, 2000], [1004, 2001], [1003, 2004], [999, 2003]]) * 0.0075
    )
    # An empty VolumeShape falls back to the translated mosaic control points.
    row["VolumeShape"] = {"Geometry": {"WellKnownText": "POLYGON EMPTY"}}
    rings, registered = rings_in_volume_frame(row, (7.5, 7.5, 90.0))
    assert registered is False
    np.testing.assert_allclose(rings[0][0], [7.5, 15.0])


def test_count_rewrites_asks_live_and_reads_the_bare_count(tmp_path):
    """``/$count`` answers plain text behind a byte-order mark.  A count over a
    past window falls as its rows are written again, so it is never cached."""
    from datetime import UTC, datetime, timedelta, timezone

    answers = iter(["﻿1230", "﻿7"])
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=next(answers))

    client = ODataClient("i", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cache=DiskCache(tmp_path))
    pacific = timezone(timedelta(hours=-7))
    start = datetime(2026, 9, 22, 22, 25, 0, 500000, tzinfo=pacific)
    window = (start, start + timedelta(minutes=2), datetime(2026, 9, 22, 5, 25, tzinfo=UTC))
    assert client.count_rewrites(*window) == 1230
    assert client.count_rewrites(*window) == 7
    request = seen[0]
    assert request.url.path.endswith("/Locations/$count")
    assert request.url.params["$filter"] == (
        "LastModified ge 2026-09-23T05:25:00Z and LastModified le 2026-09-23T05:27:00Z"
        " and Created lt 2026-09-22T05:25:00Z"
    )
    offline = ODataClient("i", cache=DiskCache(tmp_path), offline=True)
    with pytest.raises(ODataError):
        offline.count_rewrites(*window)
