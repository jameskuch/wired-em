"""Viking's current section registration: stos parsing, the VikingXML, and moving a cell.

The stos files and positions below are Inferior Monkey's, as served on
2026-09-23: sections 936 (a mesh transform), 900 and 850 (grids), all written
2017-08-30 and older than c21768's annotations on them, so mapping an
annotation's section coordinates through them must give back the volume
position Viking stored.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx
import numpy as np
import pandas as pd
import pytest

from sbfsem.core.structure import Neuron
from sbfsem.io import registration as reg
from sbfsem.io.cache import DiskCache

STOS_936 = (
    "K:\\Neitz\\InferiorMonkey\\SEM\\0914\\SEM\\Leveled\\Images\\001\\0914_SEM_Leveled.png\n"
    "K:\\Neitz\\InferiorMonkey\\SEM\\0936\\SEM\\Leveled\\Images\\001\\0936_SEM_Leveled.png\n"
    "0\n0\n1 1 35776 35808\n1 1 33696 34048\n"
    "MeshTransform_double_2_2 vp 44 0.397814 0 14783.1 2442.91 1 0.122695 33143 5856.48 "
    "0.73873 0.176543 25179.2 7528.61 0.496433 0.187589 17795.2 7859.71 0 0.32995 2440.03 "
    "12076.4 0.820353 0.408575 27679.2 14349.7 0.900786 0.695341 30179.5 22726.8 0.048718 "
    "0.73074 4082.85 23780.8 0.495275 0.800888 17480.5 25721.6 0.202886 0.908493 8904.26 "
    "28409.6 0.811673 1 27417.2 31595 fp 8 0 16 16 1934.66 755.392 30593.1 28888.3 11 \n"
    "two_user_supplied_masks:\n"
)
STOS_900 = (
    "a.png\nb.png\n0\n0\n1 1 35776 35808\n1 1 35808 35808\n"
    "GridTransform_double_2_2 vp 8 -14.7577 -59.9341 35761.3 -59.9341 -14.7577 35715.8 "
    "35761.3 35715.8 fp 7 0 1 1 1 1 35807 35807 \n"
)
STOS_850 = (
    "a.png\nb.png\n0\n0\n1 1 35776 35808\n1 1 35808 35840\n"
    "GridTransform_double_2_2 vp 8 -150.451 -225.087 35625.9 -225.087 -150.451 35582.4 "
    "35625.9 35582.4 fp 7 0 1 1 1 1 35807 35839 \n"
)
#: (section, X, Y, stored VolumeX, stored VolumeY) of c21768 annotations.
STORED = [
    (936, 11986.422473477412, 18450.748078897297, 12552.789295101014, 20274.446155071993),
    (900, 12538.603364096165, 20308.46322478967, 12513.01119842031, 20230.797204569248),
    (900, 12094.447100567322, 20544.47398998623, 12069.238270565333, 20466.600444542488),
    (850, 13515.026, 22129.383, 13353.007019648536, 21884.837997816827),
    (850, 14304.26010763373, 20508.774686728084, 14141.565269321847, 20265.655622689817),
]
STOS = {936: STOS_936, 900: STOS_900, 850: STOS_850}


@pytest.mark.parametrize("z, x, y, vx, vy", STORED)
def test_the_stos_gives_back_the_stored_position(z, x, y, vx, vy):
    mapped = reg.parse_stos(STOS[z])([[x, y]])[0]
    np.testing.assert_allclose(mapped, [vx, vy], atol=0.05)  # 0.4 nm at 7.5 nm/pixel


def test_parse_stos_kinds_and_refusals():
    mesh = reg.parse_stos(STOS_936)
    assert mesh.kind == "MeshTransform_double_2_2" and len(mesh.mapped) == 11
    grid = reg.parse_stos(STOS_900)
    assert grid.kind == "GridTransform_double_2_2" and len(grid.simplices) == 2
    # A grid's corners land on its control points exactly.
    np.testing.assert_allclose(grid(grid.mapped), grid.control)
    # Outside every triangle: not mapped.
    assert np.isnan(grid([[-10.0, -10.0]])).all()
    with pytest.raises(reg.RegistrationError):
        reg.parse_stos(STOS_900.replace("GridTransform_double_2_2", "AffineTransform_double_2_2"))
    with pytest.raises(reg.RegistrationError):
        reg.parse_stos("too\nshort\n")
    with pytest.raises(reg.RegistrationError):
        reg.parse_stos(STOS_900.replace("-14.7577", "nan"))


def test_derived_points_follow_viking():
    kind, verts = reg._shape({"Geometry": {"WellKnownText": "LINESTRING (0 0, 10 4)"}})
    assert kind == "LINESTRING"
    np.testing.assert_allclose(reg._derived_point(kind, verts), [5.0, 2.0])  # envelope center
    kind, verts = reg._shape("POLYGON ((0 0, 4 0, 4 2, 0 2, 0 0))")
    np.testing.assert_allclose(reg._derived_point(kind, verts), [2.0, 1.0])  # centroid
    kind, verts = reg._shape("POINT (3 7)")
    np.testing.assert_allclose(reg._derived_point(kind, verts), [3.0, 7.0])
    assert reg._shape(None) is None and reg._shape("EMPTY") is None


def test_ranges():
    assert reg._ranges([]) == ""
    assert reg._ranges([937, 938, 939, 1315, 1317, 1318]) == "937-939, 1315, 1317-1318"


# -- a volume served over HTTP ------------------------------------------------------
def vikingxml(entries: list[tuple[int, int]]) -> str:
    stos = "\n".join(
        f'<stos GroupName="SliceToVolume1" controlSection="{c}" mappedSection="{m}" '
        f'path="SEM\\SliceToVolume1\\{m}-{c}.stos" pixelspacing="1" type="Brute"/>'
        for m, c in entries)
    return (f'<?xml version="1.0" ?>\n<Volume Name="Test" path="http://vol.test/Test" '
            f'num_sections="4">\n{stos}\n</Volume>\n')


class Server:
    """Answers the VikingXML and stos files, honoring If-Modified-Since."""

    def __init__(self, files: dict[str, tuple[str, str]]):
        self.files = files  # path -> (text, Last-Modified)
        self.requests: list[tuple[str, str | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        since = request.headers.get("If-Modified-Since")
        self.requests.append((path, since))
        if path not in self.files:
            return httpx.Response(404)
        text, modified = self.files[path]
        if since == modified:
            return httpx.Response(304)
        return httpx.Response(200, text=text, headers={"Last-Modified": modified})


OLD = "Wed, 30 Aug 2017 17:38:31 GMT"
NEW = "Tue, 22 Sep 2026 20:37:12 GMT"
SHIFTED_900 = STOS_900.replace("vp 8 -14.7577 -59.9341 35761.3 -59.9341 -14.7577 35715.8 35761.3 35715.8",
                               "vp 8 85.2423 -159.9341 35861.3 -159.9341 85.2423 35615.8 35861.3 35615.8")


@pytest.fixture
def served():
    server = Server({
        "/Test/volume.xml": (vikingxml([(936, 914), (900, 914), (850, 914)]), OLD),
        "/Test/SEM/SliceToVolume1/936-914.stos": (STOS_936, OLD),
        "/Test/SEM/SliceToVolume1/900-914.stos": (STOS_900, OLD),
        "/Test/SEM/SliceToVolume1/850-914.stos": (STOS_850, OLD),
    })
    client = httpx.Client(transport=httpx.MockTransport(server), base_url="http://vol.test")
    yield server, client
    client.close()


LINE_900 = np.array([[12490.0, 20340.0], [12500.0, 20360.0]])


def make_neuron(source: str = "i") -> Neuron:
    """Three annotations on 936, 900 and 850, a synapse line on 900, and an outline on 850.

    The line's stored position is what Viking derives from it: the envelope
    center of the line mapped through 900's (2017) stos.
    """
    z = [936, 900, 850, 900, 850]
    rows = STORED[:2] + STORED[3:4]
    mapped_line = reg.parse_stos(STOS_900)(LINE_900)
    line_center = (mapped_line.min(axis=0) + mapped_line.max(axis=0)) / 2
    nodes = pd.DataFrame({
        "ID": [1, 2, 3, 4, 5],
        "ParentID": [9, 9, 9, 44, 9],
        "X": [r[1] for r in rows] + [0.0, STORED[4][1]],
        "Y": [r[2] for r in rows] + [0.0, STORED[4][2]],
        "VolumeX": [r[3] for r in rows] + [line_center[0], STORED[4][3]],
        "VolumeY": [r[4] for r in rows] + [line_center[1], STORED[4][4]],
        "Z": z,
        "Radius": [40.0] * 5,
        "OffEdge": [False] * 5, "Terminal": [False] * 5,
        "TypeCode": [1, 1, 1, 5, 6],
        "MosaicShape": [None, None, None,
                        {"Geometry": {"WellKnownText": "LINESTRING (12490 20340, 12500 20360)"}},
                        None],
    })
    edges = pd.DataFrame({"ID": [9, 9], "A": [1, 2], "B": [2, 3]})
    neuron = Neuron(9, source, nodes=nodes, edges=edges, volume_scale=(7.5, 7.5, 90.0),
                    omitted_ids=[], data_stamp=datetime(2026, 9, 1, tzinfo=UTC))
    ring = np.array([[106.0, 152.0], [107.0, 152.0], [107.0, 153.0]])
    neuron.geometries = pd.DataFrame([{"ID": 5, "ParentID": 9, "Z": 850, "Zum": 76.5,
                                       "Curve": [ring], "Registered": True}])
    return neuron


def test_vikingxml_and_flatness(served):
    server, client = served
    r = reg.VolumeRegistration("http://vol.test/Test/volume.xml", client=client)
    r.load()
    assert (r.base, r.control, r.group) == ("http://vol.test/Test", 914, "SliceToVolume1")
    assert sorted(r.entries) == [850, 900, 936]
    assert r.xml_last_modified == OLD
    chained = Server({"/Test/volume.xml": (vikingxml([(900, 899), (899, 898)]), OLD)})
    with (httpx.Client(transport=httpx.MockTransport(chained)) as c2,
          pytest.raises(reg.RegistrationError, match="chained")):
        reg.VolumeRegistration("http://vol.test/Test/volume.xml", client=c2).load()


def test_a_fresh_cell_does_not_move(served):
    _, client = served
    neuron = make_neuron()
    before = neuron.nodes[["Xum", "Yum"]].to_numpy().copy()
    r = reg.VolumeRegistration("http://vol.test/Test/volume.xml", client=client)
    result = reg.live_offsets(neuron, r)
    reg.apply_registration(neuron, result)
    assert result.offsets["mapped"].all()
    assert result.moved.empty and result.token is None
    np.testing.assert_allclose(neuron.nodes[["Xum", "Yum"]].to_numpy(), before, atol=1e-4)
    assert neuron.live_registration["n_moved"] == 0


def test_an_edited_stos_moves_its_section_only(served, tmp_path):
    server, client = served
    cache = DiskCache(tmp_path)
    r = reg.VolumeRegistration("http://vol.test/Test/volume.xml", client=client, cache=cache,
                               xml_ttl_s=0.0)
    reg.live_offsets(make_neuron(), r)
    # The user re-registers section 900 (+100, -100 px) and the VikingXML is rewritten.
    server.files["/Test/SEM/SliceToVolume1/900-914.stos"] = (SHIFTED_900, NEW)
    server.files["/Test/volume.xml"] = (server.files["/Test/volume.xml"][0], NEW)
    server.requests.clear()
    neuron = make_neuron()
    result = reg.live_offsets(neuron, r)
    reg.apply_registration(neuron, result)
    # Every stos was asked again, conditionally; only 900 came back changed.
    asked = dict(server.requests)
    assert asked["/Test/SEM/SliceToVolume1/936-914.stos"] == OLD
    assert asked["/Test/SEM/SliceToVolume1/900-914.stos"] == OLD
    moved = result.moved.set_index("ID")
    assert sorted(moved.index) == [2, 4]  # the disc and the synapse line on 900
    np.testing.assert_allclose(moved.loc[2, ["dx_nm", "dy_nm"]].astype(float), [750.0, -750.0], atol=1)
    assert neuron.live_registration["sections_moved"] == "900"
    np.testing.assert_allclose(neuron.nodes.set_index("ID").loc[2, ["LiveDXnm", "LiveDYnm"]].astype(float),
                               [750.0, -750.0], atol=1)
    # VolumeX is Viking's; Xum carries the move.
    assert neuron.nodes.set_index("ID").loc[2, "VolumeX"] == STORED[1][3]
    assert result.token is not None
    with pytest.raises(ValueError):
        reg.apply_registration(neuron, result)


def test_a_closed_curve_outline_moves_with_its_annotation(served):
    server, client = served
    server.files["/Test/SEM/SliceToVolume1/850-914.stos"] = (
        STOS_850.replace("-150.451 -225.087", "-50.451 -225.087")
        .replace("35625.9 -225.087", "35725.9 -225.087")
        .replace("-150.451 35582.4", "-50.451 35582.4")
        .replace("35625.9 35582.4", "35725.9 35582.4"), NEW)
    neuron = make_neuron()
    ring = neuron.geometries.iloc[0]["Curve"][0].copy()
    r = reg.VolumeRegistration("http://vol.test/Test/volume.xml", client=client)
    reg.apply_registration(neuron, reg.live_offsets(neuron, r))
    np.testing.assert_allclose(neuron.geometries.iloc[0]["Curve"][0], ring + [0.75, 0.0], atol=1e-3)


def test_provider_budget_offline_and_pause(served, tmp_path, monkeypatch):
    server, client = served
    cache = DiskCache(tmp_path)
    url = "http://vol.test/Test/volume.xml"
    live = reg.RegistrationProvider(cache=cache)
    monkeypatch.setattr(live, "registration",
                        lambda u: reg.VolumeRegistration(u, client=client, cache=cache))
    result = live.apply(make_neuron(), url)
    assert result.offsets["mapped"].all()
    live.close()

    # Offline, the copies on disk answer.
    offline = reg.RegistrationProvider(cache=cache, offline=True)
    neuron = make_neuron()
    assert offline.apply(neuron, url).offsets["mapped"].all()
    offline.close()

    # A server that is too slow: the cell keeps its stored positions, and it
    # is not a failure (no pause).
    class Slow(Server):
        def __call__(self, request):
            time.sleep(0.5)
            return super().__call__(request)

    slow_client = httpx.Client(transport=httpx.MockTransport(Slow(server.files)))
    slow = reg.RegistrationProvider(budget_s=0.2)
    monkeypatch.setattr(slow, "registration",
                        lambda u: reg.VolumeRegistration(u, client=slow_client))
    with pytest.raises(reg.RegistrationError, match="still being read"):
        slow.apply(make_neuron(), url)
    assert not slow.paused(url)
    slow.close()

    # A server that is gone: the volume pauses.
    gone = reg.RegistrationProvider()
    dead = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    monkeypatch.setattr(gone, "registration", lambda u: reg.VolumeRegistration(u, client=dead))
    with pytest.raises(reg.RegistrationError):
        gone.apply(make_neuron(), url)
    assert gone.paused(url)
    gone.close()


def test_the_web_service_applies_it_and_keys_the_render(served, tmp_path, monkeypatch, i943_path):
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service.facade import SbfsemService

    server, client = served
    server.files["/Test/SEM/SliceToVolume1/900-914.stos"] = (SHIFTED_900, NEW)
    svc = SbfsemService(cache_dir=tmp_path, live_registration=True)
    provider = svc.registrations()
    monkeypatch.setattr(provider, "registration", lambda u: reg.VolumeRegistration(
        "http://vol.test/Test/volume.xml", client=client))
    neuron = make_neuron()
    status = svc._register(neuron)
    assert status["applied"] and status["n_moved"] == 2
    svc.add_neuron(neuron)
    dto = svc.neuron_detail(9, "i").summary.live_registration
    assert dto is not None and dto.applied and dto.sections_moved == "900"
    # A volume whose transforms are not read says so and keeps its positions.
    note = svc._register(make_neuron("RC1"))
    assert not note.get("applied") and "not read here" in note["note"]
    # The render of a moved cell has its own key; the unmoved one keeps the old.
    keys = []
    real_put = svc._disk_meshes.put
    svc._disk_meshes.put = lambda key, mesh: (keys.append(key), real_put(key, mesh))
    plain = read_legacy_json(i943_path)
    svc.add_neuron(plain)
    svc.mesh_object(943, "i", method="cylinder", pathway="original", smooth_iterations=0)
    assert not any("live-registration" in str(k[-1]) for k in keys)
    svc.close()


def _correction(trusted_ids: dict[int, tuple[float, float]]):
    """A section correction that moves the given annotations (dx, dy in nm), by hand."""
    from sbfsem.io import corrections as corr

    ids = [1, 2, 3, 4, 5]
    moves = [trusted_ids.get(i, (0.0, 0.0)) for i in ids]
    offsets = pd.DataFrame({
        "ID": ids, "Z": [936, 900, 850, 900, 850],
        "dx_nm": [m[0] for m in moves], "dy_nm": [m[1] for m in moves],
        "trusted": [i in trusted_ids for i in ids], "found": True,
    })
    return corr.CellCorrection(corr.CorrectionSet("Test", "Grid16"), offsets)


def test_the_nodes_carry_every_stage_of_the_positions(served, tmp_path):
    """stored = xyz - live_shift - shift, registered = xyz - shift, corrected = xyz."""
    from sbfsem.io import corrections as corr
    from sbfsem.service.facade import SbfsemService

    server, client = served
    server.files["/Test/SEM/SliceToVolume1/900-914.stos"] = (SHIFTED_900, NEW)
    registration = reg.VolumeRegistration("http://vol.test/Test/volume.xml", client=client)
    # ID 3 is trusted but not moved; ID 4 is a child structure (a synapse line).
    moves = {1: (120.0, -80.0), 2: (-300.0, 250.0), 3: (0.0, 0.0), 4: (10.0, 10.0), 5: (50.0, 50.0)}

    both = make_neuron()
    reg.apply_registration(both, reg.live_offsets(both, registration))
    corr.apply_offsets(both, _correction(moves))
    only_correction = make_neuron()
    corr.apply_offsets(only_correction, _correction(moves))
    untouched = make_neuron()

    svc = SbfsemService(cache_dir=tmp_path)
    try:
        svc.add_neuron(untouched)
        base = {n.id: n for n in svc.nodes(9, "i", cell_only=False)}
        assert all(n.shift_um is None and n.live_shift_um is None for n in base.values())

        svc.add_neuron(both)
        nodes = {n.id: n for n in svc.nodes(9, "i", cell_only=False)}
        assert sorted(nodes) == [1, 2, 3, 4, 5]
        # live_shift_um is set exactly where the current transforms moved the annotation:
        # section 900 (the disc and the synapse line), nowhere else.
        assert {i for i, n in nodes.items() if n.live_shift_um is not None} == {2, 4}
        np.testing.assert_allclose(nodes[2].live_shift_um, [0.75, -0.75], atol=1e-3)
        # shift_um is the correction: trusted and non-zero.
        assert {i for i, n in nodes.items() if n.shift_um is not None} == {1, 2, 4, 5}
        assert nodes[2].shift_um == pytest.approx([-0.3, 0.25])
        for n in nodes.values():
            stored = np.array(n.xyz_um[:2])
            registered = stored.copy()
            if n.shift_um is not None:
                registered = stored - n.shift_um
            if n.live_shift_um is not None:
                stored = registered - n.live_shift_um
            else:
                stored = registered
            expected = base[n.id].xyz_um
            np.testing.assert_allclose(stored, expected[:2], atol=1e-6)   # live_shift_um is rounded to 1e-6 um
            assert n.xyz_um[2] == expected[2]     # Z does not move
        # The cell-only list (what the viewer draws) leaves the synapse line out.
        assert sorted(n.id for n in svc.nodes(9, "i")) == [1, 2, 3, 5]

        # A copy drawn at the stored positions then corrected has no live move at all.
        svc.add_neuron(only_correction)
        corrected = svc.nodes(9, "i", cell_only=False)
        assert all(n.live_shift_um is None for n in corrected)
        assert {n.id for n in corrected if n.shift_um is not None} == {1, 2, 4, 5}
    finally:
        svc.close()


# -- audit 2026-10-15, W10: an unreadable stos and a NaN point ------------------------
def test_a_nan_point_unmaps_itself_and_not_its_triangle():
    """One NaN in a call made every point of the triangle it landed in NaN too."""
    grid = reg.parse_stos(STOS_900)
    points = np.array([[1000.0, 1000.0], [np.nan, 5.0], [1010.0, 1005.0], [5.0, np.inf]])
    mapped = grid(points)
    assert np.isnan(mapped[1]).all() and np.isnan(mapped[3]).all()
    assert np.isfinite(mapped[0]).all() and np.isfinite(mapped[2]).all()
    np.testing.assert_allclose(mapped[[0, 2]], grid(points[[0, 2]]))
    mesh = reg.parse_stos(STOS_936)
    inside = mesh(np.array([[12000.0, 18000.0], [np.nan, np.nan], [12010.0, 18010.0]]))
    assert np.isfinite(inside[[0, 2]]).all() and np.isnan(inside[1]).all()


@pytest.mark.parametrize("damage", [
    lambda t: t.replace("vp 8", "vp 80"),                 # a count that runs past the numbers
    lambda t: t.replace("-14.7577", "abc"),               # text where a number belongs
    lambda t: t.replace("fp 7 0 1 1 1 1 35807 35807", "fp 7 0 1"),   # a truncated fp block
    lambda t: "\n".join(t.splitlines()[:6] + [""]),       # a blank transform line
    lambda t: t.replace("GridTransform_double_2_2 vp", "GridTransform_double_2_2 xx"),
])
def test_a_damaged_stos_is_a_registration_error_not_an_index_or_value_error(damage):
    """These escaped as IndexError, ValueError or QhullError and failed the load of
    every cell that crossed the section."""
    with pytest.raises(reg.RegistrationError):
        reg.parse_stos(damage(STOS_900))


def test_a_mesh_stos_the_triangulation_cannot_use_is_a_registration_error():
    one_point = STOS_936.replace("vp 44", "vp 4").replace(
        "fp 8 0 16 16 1934.66 755.392 30593.1 28888.3 11", "fp 8 0 16 16 1934.66 755.392 30593.1 28888.3 11")
    with pytest.raises(reg.RegistrationError):
        reg.parse_stos(one_point)


def test_one_damaged_stos_costs_its_section_and_not_the_cell():
    server = Server({
        "/Test/volume.xml": (vikingxml([(936, 914), (900, 914), (850, 914)]), OLD),
        "/Test/SEM/SliceToVolume1/936-914.stos": (STOS_936, OLD),
        "/Test/SEM/SliceToVolume1/900-914.stos": (STOS_900.replace("vp 8", "vp 80"), NEW),
        "/Test/SEM/SliceToVolume1/850-914.stos": (STOS_850, OLD),
    })
    with httpx.Client(transport=httpx.MockTransport(server), base_url="http://vol.test") as client:
        registration = reg.VolumeRegistration("http://vol.test/Test/volume.xml", client=client)
        maps = registration.transforms([936, 900, 850])
        assert sorted(maps) == [850, 936]            # 900 is absent, as an unreadable one is
        result = reg.live_offsets(make_neuron(), registration, maps=maps)
        mapped = result.offsets.set_index("ID")["mapped"]
        assert bool(mapped[1]) and not bool(mapped[2])    # 936 mapped, the disc on 900 not
