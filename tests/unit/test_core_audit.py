"""Core and io after the 2026-10-15 audit (W10): the JSON round trip that retyped
synapses, organelle positions in the wrong frame, packaged data read by any spelling,
a volume Viking does not name.  Small neurons are built here (1 pixel = 1 um)."""

from __future__ import annotations

import httpx
import pandas as pd
import pytest

from sbfsem.core import registry
from sbfsem.core.structure import Neuron
from sbfsem.export.json_v2 import read_neuron_json, write_neuron_json
from sbfsem.io.cache import DiskCache
from sbfsem.io.odata import ODataClient
from sbfsem.io.structure_group import Mitochondria

SCALE = (1000.0, 1000.0, 1000.0)


def _nodes(rows, parent):
    """``rows``: (ID, x, y, z, radius)."""
    n = len(rows)
    return pd.DataFrame({
        "ID": [r[0] for r in rows], "ParentID": [parent] * n,
        "VolumeX": [float(r[1]) for r in rows], "VolumeY": [float(r[2]) for r in rows],
        "Z": [r[3] for r in rows], "Radius": [float(r[4]) for r in rows],
        "X": [0.0] * n, "Y": [0.0] * n,
        "OffEdge": [False] * n, "Terminal": [False] * n, "TypeCode": [1] * n,
    })


# -- W10 F01: the JSON round trip ----------------------------------------------------------------------
def _typing_neuron(policy, partners):
    cell = _nodes([(1, 0, 0, 0, 2)], 100)
    neuron = Neuron(100, "i", nodes=cell, volume_scale=SCALE, untagged_psd=policy,
                    omitted_ids=[])
    neuron.psd_partners = partners
    synapses = pd.DataFrame({
        "ID": [200, 201, 202, 203], "ParentID": [100] * 4, "TypeID": [35, 35, 35, 35],
        "Tags": ["Bipolar;Glutamate;Ribbon", "", "", "AC"], "Label": [""] * 4,
    })
    child = _nodes([(10 + i, i, 1, 1 + i, 0.5) for i in range(4)], 0)
    child["ParentID"] = [200, 201, 202, 203]
    neuron.merge_child_data(synapses, child, None)
    return neuron


def _kinds(neuron):
    return {int(r.ID): (r.LocalName.value, r.TagStatus, r.SuggestedTag)
            for r in neuron.synapses.itertuples()}


@pytest.mark.parametrize("policy", ["assume", "separate", "infer"])
def test_json_round_trip_keeps_what_each_postsynapse_is(tmp_path, policy):
    """Inferred and untagged postsynapses came back as ConvPost (RC1 c606: 649
    untagged and 256 of 262 ribbon postsynapses)."""
    neuron = _typing_neuron(policy, {201: (73,), 202: (34, 73)})
    before = _kinds(neuron)
    path = write_neuron_json(neuron, tmp_path / "c.json")
    again = read_neuron_json(path)
    assert _kinds(again) == before
    assert again.untagged_psd == policy
    assert again.psd_partners == neuron.psd_partners
    if policy == "infer":
        assert before[201] == ("RibbonPost", "inferred", "Ribbon")
        assert before[202][0] == "UntaggedPost"
    if policy == "separate":
        assert before[201][0] == "UntaggedPost"


def test_a_file_written_before_the_policy_was_recorded_still_restores_its_kinds(tmp_path):
    import json

    neuron = _typing_neuron("infer", {201: (73,)})
    before = _kinds(neuron)
    path = write_neuron_json(neuron, tmp_path / "c.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("untaggedPsd", "psdPartners"):
        del payload[key]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _kinds(read_neuron_json(path)) == before


def test_a_kind_this_version_does_not_know_keeps_the_tags_reading(tmp_path):
    import json

    neuron = _typing_neuron("assume", {})
    path = write_neuron_json(neuron, tmp_path / "c.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["synapses"][0]["LocalName"] = "FutureKind"
    path.write_text(json.dumps(payload), encoding="utf-8")
    again = _kinds(read_neuron_json(path))
    assert again[200][0] == "RibbonPost"


@pytest.mark.parametrize("damaged", [{"201": 5}, [1, 2], {"201": [1, "x"]}, {"x": [1]}, 7, {"201": None}],
                         ids=["a number for a list", "a list for a map", "a word in a list", "a word for an ID",
                              "a number", "null partners"])
def test_a_damaged_psd_partners_key_is_an_unreadable_file_not_a_crash(tmp_path, damaged):
    """The key is new, and a file whose ``psdPartners`` is not a map of lists raised TypeError
    or AttributeError, which the import route (it catches ValueError) answered with a 500
    where the reader before it ignored the key.  Every damage reads as the ValueError that a
    non-numeric key already was, so the route says 400."""
    import json

    path = write_neuron_json(_typing_neuron("infer", {201: (73,)}), tmp_path / "c.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["psdPartners"] = damaged
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="psdPartners"):
        read_neuron_json(path)


def test_the_import_route_answers_400_for_a_damaged_psd_partners_key(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from sbfsem_web.app import create_app

    path = write_neuron_json(_typing_neuron("infer", {201: (73,)}), tmp_path / "c.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["psdPartners"] = {"201": 5}
    with TestClient(create_app()) as client:
        client.app.state.service.offline = True
        response = client.post("/api/neurons/import", files={"file": ("c.json", json.dumps(payload).encode())})
        assert response.status_code == 400 and "psdPartners" in response.json()["detail"]
        payload["psdPartners"] = {"201": [73]}
        assert client.post("/api/neurons/import",
                           files={"file": ("c.json", json.dumps(payload).encode())}).status_code == 200


# -- W10 F06: positions of an organelle group -------------------------------------------------------------
def test_ultrastructure_positions_are_in_the_volume_frame(tmp_path):
    """Xum/Yum came from the section mosaic's X/Y: up to 345 um off on RC1."""

    def handler(request):
        url = str(request.url)
        if "Structures?$filter=TypeID" in url or "Structures?$filter=TypeID".replace(" ", "%20") in url:
            return httpx.Response(200, json={"value": [{"ID": 10, "ParentID": 100}]})
        if "Scale" in url:
            return httpx.Response(200, json={"X": {"Value": 10.0}, "Y": {"Value": 10.0},
                                             "Z": {"Value": 90}})
        return httpx.Response(200, json={"value": [
            {"ID": 1, "ParentID": 10, "X": 100, "Y": 200, "VolumeX": 1000, "VolumeY": 2000,
             "Z": 3, "Radius": 40}]})

    client = ODataClient("i", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cache=DiskCache(tmp_path, enabled=False))
    group = Mitochondria("i", client=client)
    group.pull()
    row = group.locations.iloc[0]
    assert (row["Xum"], row["Yum"]) == pytest.approx((10.0, 20.0))   # VolumeX, not X
    assert row["Zum"] == pytest.approx(0.27)


def test_ultrastructure_falls_back_to_the_mosaic_columns_only_without_volume_ones(tmp_path):
    def handler(request):
        url = str(request.url)
        if "TypeID" in url:
            return httpx.Response(200, json={"value": [{"ID": 10, "ParentID": 100}]})
        if "Scale" in url:
            return httpx.Response(200, json={"X": {"Value": 10.0}, "Y": {"Value": 10.0},
                                             "Z": {"Value": 90}})
        return httpx.Response(200, json={"value": [
            {"ID": 1, "ParentID": 10, "X": 100, "Y": 200, "Z": 3, "Radius": 40}]})

    client = ODataClient("i", client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cache=DiskCache(tmp_path, enabled=False))
    group = Mitochondria("i", client=client)
    group.pull()
    assert group.locations.iloc[0]["Xum"] == pytest.approx(1.0)


# -- W10 F30: the packaged data readers -------------------------------------------------------------------------
def test_data_readers_take_any_spelling_and_hand_out_read_only_arrays():
    a = registry.read_xy_offset("i")
    assert registry.read_xy_offset("NeitzInferiorMonkey") is a
    assert registry.read_xy_offset("inferior") is a
    assert not a.flags.writeable
    with pytest.raises(ValueError):
        a[0, 0] = 1.0
    markers = registry.read_boundary_markers("i", "GCL")
    assert registry.read_boundary_markers("NeitzInferiorMonkey", "GCL") is markers
    assert not markers.flags.writeable
    assert registry.read_xy_offset("t") is None
    assert registry.read_omitted_sections("i").size == 0
    assert hasattr(registry.read_xy_offset, "cache_clear")
    with pytest.raises(ValueError):
        registry.read_boundary_markers("i", "IPL")


def test_data_readers_take_their_arguments_by_keyword_as_they_always_did():
    """The readers were ``functools.cache`` functions, which take any argument by name;
    the wrapper that spells a volume once took only positional ones, so
    ``read_boundary_markers('i', layer='GCL')`` raised TypeError (a script or a notebook;
    nothing in the tree passes one by name).  A name and a position are one cached read."""
    markers = registry.read_boundary_markers("i", "GCL")
    assert registry.read_boundary_markers("i", layer="GCL") is markers
    assert registry.read_boundary_markers(source="NeitzInferiorMonkey", layer="GCL") is markers
    assert registry.read_boundary_markers(layer="GCL", source="inferior") is markers
    offsets = registry.read_xy_offset("i")
    assert registry.read_xy_offset(source="i") is offsets
    assert registry.read_omitted_sections(source="i").size == 0
    # A call the function would refuse is still refused, as a TypeError.
    for call in (lambda: registry.read_boundary_markers("i"),
                 lambda: registry.read_boundary_markers("i", "GCL", "extra"),
                 lambda: registry.read_boundary_markers("i", layers="GCL"),
                 lambda: registry.read_xy_offset()):
        with pytest.raises(TypeError):
            call()
    with pytest.raises(ValueError):
        registry.read_boundary_markers("i", layer="IPL")


def test_the_data_files_nothing_loads_are_gone():
    import importlib.resources as resources

    names = {p.name for p in resources.files("sbfsem").joinpath("data").iterdir()}
    assert not any(n.startswith("MARCRC1") for n in names)
    assert "VikingMetadata.xml" not in names
    # The alias still resolves to the file that is there.
    assert registry.read_boundary_markers("MarcRC1", "GCL") is not None


# -- W10 F26: a volume Viking does not name ----------------------------------------------------------------------------
def test_a_volume_without_a_viking_name_is_not_corrected():
    """Asked with no volume name the service lists every volume's sets, and one of
    another volume's would be chosen."""
    from sbfsem.io.corrections import CorrectionsProvider, correct_neuron

    class NeverAsked:
        def correction_sets(self, *args, **kwargs):
            raise AssertionError("no volume name: nothing to ask")

    cell = _nodes([(1, 0, 0, 0, 2)], 100)
    neuron = Neuron(100, "DemoVolume", nodes=cell, omitted_ids=[])
    assert neuron.volume.viking_name is None
    assert correct_neuron(neuron, NeverAsked(), mode="neighbor") is None
    provider = CorrectionsProvider.__new__(CorrectionsProvider)
    assert provider.correct(neuron, "neighbor") is None
