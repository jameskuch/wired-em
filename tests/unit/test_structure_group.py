"""Structure groups: mitochondria, vasculature and the cone mosaic.

``BloodVessel`` and ``Nucleolus`` did not parse in MATLAB (F145, F146) and
``Vasculature.pull`` called methods on cell entries (F150), so none of this ever
ran there.
"""

from __future__ import annotations

import httpx
import numpy as np
import pandas as pd
import pytest

from sbfsem.io.cache import DiskCache
from sbfsem.io.odata import ODataClient
from sbfsem.io.structure_group import (
    ConeMosaic,
    Mitochondria,
    Nucleolus,
    StructureGroup,
    Vasculature,
)


def _client(handler, tmp_path):
    return ODataClient(
        "i", client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=DiskCache(tmp_path, enabled=False),
    )


def test_type_ids():
    assert Mitochondria.type_id == 246
    assert Nucleolus.type_id == 245
    assert Vasculature.type_id == 3


def test_base_class_requires_a_type_id(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"value": []})

    group = StructureGroup("i", client=_client(handler, tmp_path))
    with pytest.raises(NotImplementedError):
        group.pull()


def test_mitochondria_volume_sums_squared_radii():
    """F147: MATLAB computed 2*pi*(sum r)^2, inflating with annotation count."""
    mito = Mitochondria.__new__(Mitochondria)
    mito.volume_scale = (7.5, 7.5, 90.0)
    mito.locations = pd.DataFrame(
        {"ParentID": [1, 1, 1, 2], "Rum": [0.1, 0.2, 0.3, 0.5]}
    )
    frame = mito.volumes().set_index("ParentID")
    expected_one = float(np.sum(np.pi * np.array([0.1, 0.2, 0.3]) ** 2) * 0.09)
    assert frame.loc[1, "volume_um3"] == pytest.approx(expected_one)
    assert frame.loc[1, "n"] == 3
    assert frame.loc[2, "volume_um3"] == pytest.approx(np.pi * 0.25 * 0.09)
    # The MATLAB formula would have given (0.1+0.2+0.3)^2 * 2pi * 0.09.
    wrong = 2 * np.pi * (0.6**2) * 0.09
    assert frame.loc[1, "volume_um3"] < wrong / 5


def test_mitochondria_pull_batches_locations(tmp_path):
    calls = []

    def handler(request):
        url = str(request.url)
        calls.append(url)
        if "Structures?$filter=TypeID" in url:
            return httpx.Response(200, json={"value": [
                {"ID": i, "ParentID": 100} for i in range(10, 20)
            ]})
        if "Scale" in url:
            return httpx.Response(200, json={
                "X": {"Value": 7.5}, "Y": {"Value": 7.5}, "Z": {"Value": 90}
            })
        return httpx.Response(200, json={"value": [
            {"ID": 1, "ParentID": 10, "X": 100, "Y": 200, "Z": 3, "Radius": 40},
            {"ID": 2, "ParentID": 11, "X": 110, "Y": 210, "Z": 4, "Radius": 50},
        ]})

    group = Mitochondria("i", client=_client(handler, tmp_path))
    group.pull()
    assert group.ids.tolist() == list(range(10, 20))
    assert len(group.locations) == 2
    assert group.locations["Rum"].iloc[0] == pytest.approx(40 * 0.0075)
    # F148: one batched Locations request, not one per structure.
    location_calls = [c for c in calls if "Locations" in c]
    assert len(location_calls) == 1


def test_group_with_no_structures(tmp_path):
    def handler(request):
        if "Scale" in str(request.url):
            return httpx.Response(200, json={
                "X": {"Value": 7.5}, "Y": {"Value": 7.5}, "Z": {"Value": 90}
            })
        return httpx.Response(200, json={"value": []})

    group = Nucleolus("i", client=_client(handler, tmp_path))
    group.pull()
    assert group.ids.size == 0
    assert group.locations.empty


def test_vasculature_records_failures_instead_of_shifting(tmp_path):
    """F150: a swallowed import failure shifted every later vessel."""
    def handler(request):
        url = str(request.url)
        if "Structures?$filter=TypeID" in url:
            return httpx.Response(200, json={"value": [
                {"ID": 1, "ParentID": None}, {"ID": 2, "ParentID": None},
            ]})
        if url.endswith("Structures(1)/"):
            return httpx.Response(200, json={"ID": 1, "TypeID": 3})
        if url.endswith("Structures(2)/"):
            return httpx.Response(500)
        if "Scale" in url:
            return httpx.Response(200, json={
                "X": {"Value": 7.5}, "Y": {"Value": 7.5}, "Z": {"Value": 90}
            })
        return httpx.Response(200, json={"value": []})

    group = Vasculature("i", client=_client(handler, tmp_path))
    group.pull()
    vessels = group.load_vessels()
    assert set(vessels) == {1}
    assert group.failed == [2]


def test_cone_mosaic_requires_a_volume_with_cones():
    with pytest.raises(ValueError):
        ConeMosaic("t")


def test_cone_mosaic_reads_outlines(tmp_path):
    wkt = "POLYGON((1000 2000, 1400 2000, 1400 2400, 1000 2400, 1000 2000))"

    def handler(request):
        url = str(request.url)
        if "Scale" in url:
            return httpx.Response(200, json={
                "X": {"Value": 7.5}, "Y": {"Value": 7.5}, "Z": {"Value": 90}
            })
        if "contains(Label" in url:
            return httpx.Response(200, json={"value": [{"ID": 42, "Label": "lmTRACE 1"}]})
        if "Structures(42)?$select=Label" in url:
            return httpx.Response(200, json={"Label": "lmTRACE 1"})
        if "TypeCode" in url:
            return httpx.Response(200, json={"value": [{
                "ID": 500, "ParentID": 42, "Z": 1200,
                "MosaicShape": {"Geometry": {"WellKnownText": wkt}},
            }]})
        return httpx.Response(200, json={"value": []})

    mosaic = ConeMosaic("i", client=_client(handler, tmp_path))
    traces = mosaic.get_cones("LM")
    assert len(traces) == 1
    trace = traces[0]
    assert trace.cone_type == "LM"
    assert trace.z_um == pytest.approx(1200 * 0.09)
    # 400 pixels at 7.5 nm/pixel is 3 um.
    assert np.ptp(trace.outline[:, 0]) == pytest.approx(3.0)
    np.testing.assert_allclose(trace.centroid, trace.outline.mean(axis=0))
    with pytest.raises(ValueError):
        mosaic.cone_ids("XX")


def test_cone_mosaic_summary(tmp_path):
    def handler(request):
        if "Scale" in str(request.url):
            return httpx.Response(200, json={
                "X": {"Value": 7.5}, "Y": {"Value": 7.5}, "Z": {"Value": 90}
            })
        return httpx.Response(200, json={"value": []})

    mosaic = ConeMosaic("i", client=_client(handler, tmp_path))
    assert mosaic.get_all() == {"LM": [], "S": [], "U": []}
    assert mosaic.summary() == {"LM": 0, "S": 0, "U": 0}
