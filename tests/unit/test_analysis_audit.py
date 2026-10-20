"""Analysis, export and network after the 2026-10-15 audit (W11).

Each test pins a defect the audit reproduced: the path length that carried another
synapse's ID, a soma cutoff taken over child annotations, a synapse counted once by
each of its cells.  Small neurons are built here (1 pixel = 1 um) so the numbers read
directly.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from sbfsem.analysis import dendrite as D
from sbfsem.analysis import synapse as S
from sbfsem.analysis.annotators import _when
from sbfsem.core.structure import Neuron, SynapseNotFoundError
from sbfsem.export import bulk
from sbfsem.export.swc import SWC_TYPES, swc_table, write_swc
from sbfsem.network.graph import cytoscape_elements, network_from_links
from sbfsem.network.links import all_linked_neurons, linked_neurons

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


def line_neuron(*, scale=SCALE, child_radius=0.5) -> Neuron:
    """A soma at the origin and a dendrite along x; three conventional presynapses.

    The synapse table lists 200, 201, 202; 200 sits by the fourth annotation and
    201 by the second, and the child annotations are given 201's first, so the
    node table and the synapse table are in different orders.  202 has no
    annotation at all.
    """
    cell = _nodes([(1, 0, 0, 0, 8), (2, 10, 0, 0, 1), (3, 20, 0, 0, 1),
                   (4, 30, 0, 0, 1), (5, 40, 0, 0, 1)], 100)
    edges = pd.DataFrame({"ID": [100] * 4, "A": [1, 2, 3, 4], "B": [2, 3, 4, 5]})
    neuron = Neuron(100, "i", nodes=cell, edges=edges, volume_scale=scale, omitted_ids=[])
    synapses = pd.DataFrame({
        "ID": [200, 201, 202], "ParentID": [100] * 3, "TypeID": [34] * 3,
        "Tags": [""] * 3, "Label": [""] * 3,
    })
    child = _nodes([(10, 10, 1, 0, child_radius), (11, 30, 1, 0, child_radius)], 0)
    child["ParentID"] = [201, 200]
    neuron.merge_child_data(synapses, child, None)
    return neuron


# -- W11 F04: the synapse path length ---------------------------------------------
def test_path_length_pairs_each_synapse_with_its_own_marker():
    """The IDs came from the synapse table and the points from the node table, two
    orders; a synapse without annotations has no point at all (65 of 184 rows of
    c1178's ConvPost carried another synapse's ID)."""
    neuron = line_neuron()
    table = S.synapse_soma_path_length(neuron, "ConvPre")
    assert table["SynapseID"].tolist() == [201, 200]
    got = dict(zip(table["SynapseID"], table["PathLength"], strict=True))
    assert got[201] == pytest.approx(10.0)
    assert got[200] == pytest.approx(30.0)
    assert dict(zip(table["SynapseID"], table["LocationID"], strict=True)) == {201: 2, 200: 4}


def test_path_length_of_a_type_the_cell_lacks_raises():
    with pytest.raises(SynapseNotFoundError):
        S.synapse_soma_path_length(line_neuron(), "RibbonPre")


# -- W11 F08/F11: the SWC ---------------------------------------------------------
def test_the_soma_cutoff_is_taken_over_the_cell_s_own_annotations():
    """A child annotation larger than the soma set the cutoff for the whole cell,
    so no node of the cell was typed soma."""
    neuron = line_neuron(child_radius=20.0)
    table = swc_table(neuron)
    assert table["T"].iloc[0] == SWC_TYPES["soma"]
    assert (table["T"] == SWC_TYPES["soma"]).sum() == 1


def test_the_swc_header_states_the_cutoff_the_rows_were_typed_with(tmp_path):
    neuron = line_neuron(child_radius=20.0)
    path = write_swc(neuron, tmp_path / "c.swc")
    text = path.read_text(encoding="utf-8")
    assert "SOMA cutoff 6.400 um" in text          # 0.8 of the soma's 8, not of the 20
    assert "1 rows typed 1 (soma)" in text


def test_swc_z_keeps_the_75_nm_sections(tmp_path):
    """Two decimals wrote the Temporal volume's 0.075 um sections as 0.07 or 0.08."""
    cell = _nodes([(1, 0, 0, 1, 2), (2, 5, 0, 2, 1), (3, 10, 0, 3, 1)], 100)
    edges = pd.DataFrame({"ID": [100] * 2, "A": [1, 2], "B": [2, 3]})
    neuron = Neuron(100, "i", nodes=cell, edges=edges, volume_scale=(1000.0, 1000.0, 75.0),
                    omitted_ids=[])
    path = write_swc(neuron, tmp_path / "c.swc")
    rows = [line.split() for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    assert [row[4] for row in rows] == ["0.0750", "0.1500", "0.2250"]


def test_the_swc_stamp_is_the_utc_instant_whatever_the_label():
    """The same digits under Viking's old (-07:00) and new (Z) labels stamp alike."""
    from sbfsem.export.swc import provenance_lines

    old = datetime(2026, 10, 14, 5, 31, 15, tzinfo=timezone(timedelta(hours=-7)))
    new = datetime(2026, 10, 14, 5, 31, 15, tzinfo=UTC)
    stamps = []
    for stamp in (old, new):
        neuron = line_neuron()
        neuron.data_stamp = stamp
        lines = provenance_lines(neuron, swc_table(neuron), {}, None, None)
        stamps.append(next(line for line in lines if line.startswith("ANNOTATIONS_STAMP"))
                      .split()[1])
    assert stamps[0] == stamps[1] == "2026-10-14T05:31:15+00:00"


def test_swc_refuses_what_it_cannot_write_and_says_so(tmp_path):
    """The branches of ``_build`` the suite never ran (audit W11 F27)."""
    neuron = line_neuron()
    with pytest.raises(ValueError, match="sampling"):
        swc_table(neuron, sampling="dense")
    with pytest.raises(ValueError, match="radius"):
        swc_table(neuron, radius="mean")
    with pytest.raises(ValueError, match="do not combine"):
        swc_table(neuron, lamellae=True, positions={1: (0.0, 0.0)})
    with pytest.raises(ValueError, match="not in c100's graph"):
        swc_table(neuron, start_node=999)
    empty = Neuron(100, "i", nodes=_nodes([(1, 0, 0, 0, 1)], 100).iloc[:0], omitted_ids=[])
    assert len(swc_table(empty)) == 0


# -- W11 F05/F14/F15: the bulk archive -------------------------------------------------
@pytest.mark.parametrize("pattern", ["c{id}\n", "c{id}\n.swc", "c{id} ", "c{id}/x", "../c{id}"])
def test_the_file_name_guard_refuses_a_newline_and_the_other_escapes(pattern):
    with pytest.raises(ValueError):
        bulk.file_name(pattern, structure_id=1, volume="i")


def _zip(data):
    return zipfile.ZipFile(io.BytesIO(data))


def test_a_late_failure_leaves_neither_the_file_nor_a_row_that_says_it_was_written(monkeypatch):
    def failing(neuron, **kwargs):
        raise RuntimeError("morphometrics failed")

    monkeypatch.setattr("sbfsem.analysis.dendrite.morphometrics", failing)
    data = bulk.swc_archive([1], lambda i: line_neuron(), volume="i")
    archive = _zip(data)
    assert archive.namelist() == ["manifest.csv"]
    manifest = pd.read_csv(io.StringIO(archive.read("manifest.csv").decode("utf-8")),
                           dtype=str, keep_default_na=False)
    assert manifest.loc[0, "error"].startswith("RuntimeError")
    assert manifest.loc[0, "file"] == "" and manifest.loc[0, "swc_rows"] == ""


def test_an_archive_stops_starting_structures_when_its_time_is_up(monkeypatch):
    """200 cold cells cannot finish inside the gateway's 300 s: past the budget
    the rest are manifest rows that say so, and what was built is sent."""
    ticks = iter(range(0, 10_000, 100))
    monkeypatch.setattr(bulk.time, "monotonic", lambda: float(next(ticks)))
    data = bulk.swc_archive([1, 2, 3], lambda i: line_neuron(), volume="i",
                            pattern="c{id}", time_budget_s=150)
    archive = _zip(data)
    assert sorted(archive.namelist()) == ["c1.swc", "manifest.csv"]
    manifest = pd.read_csv(io.StringIO(archive.read("manifest.csv").decode("utf-8")),
                           dtype=str, keep_default_na=False).set_index("structure_id")
    assert manifest.loc["1", "error"] == ""
    assert manifest.loc["2", "error"].startswith("NotAttempted")
    assert manifest.loc["3", "error"].startswith("NotAttempted")
    assert manifest.loc["1", "annotations_stamp"] == ""      # this neuron has no stamp


def test_no_budget_means_every_structure_is_attempted():
    data = bulk.swc_archive([1, 2], lambda i: line_neuron(), volume="i")
    assert sorted(_zip(data).namelist()) == ["c1.swc", "c2.swc", "manifest.csv"]


def test_the_manifest_stamp_is_the_utc_instant_whatever_the_label():
    """The CHANGELOG says the manifest's stamp is the UTC instant; the neurons of the other
    bulk tests have none, so the label form (``data_stamp`` as it came) passed every test."""
    old = datetime(2026, 10, 14, 5, 31, 15, tzinfo=timezone(timedelta(hours=-7)))
    new = datetime(2026, 10, 14, 5, 31, 15, tzinfo=UTC)

    def load(i):
        neuron = line_neuron()
        neuron.data_stamp = {1: old, 2: new, 3: "2026-10-14T05:31:15-07:00", 4: "2026-10-14T05:31:15Z"}[i]
        return neuron

    manifest = pd.read_csv(io.StringIO(_zip(bulk.swc_archive([1, 2, 3, 4], load, volume="i"))
                                       .read("manifest.csv").decode("utf-8")),
                           dtype=str, keep_default_na=False)
    assert manifest["annotations_stamp"].tolist() == ["2026-10-14T05:31:15+00:00"] * 4


def test_an_archive_says_how_many_structures_it_holds_and_how_many_it_lacks(monkeypatch):
    """The route sends a partial archive with a 200, and the viewer reads only the
    response: the counts travel with the bytes, so it can say 'k of n' instead of
    showing a truncated export as a complete one (CR-1)."""
    def load(i):
        if i == 2:
            raise RuntimeError("Viking said no")
        return line_neuron()

    whole = bulk.swc_archive([1, 2, 3], load, volume="i")
    assert isinstance(whole, bytes) and _zip(whole).testzip() is None
    assert (whole.asked, whole.written, whole.failed, whole.not_attempted) == (3, 2, 1, 0)
    ticks = iter(range(0, 10_000, 100))
    monkeypatch.setattr(bulk.time, "monotonic", lambda: float(next(ticks)))
    cut = bulk.swc_archive([1, 2, 3, 4], load, volume="i", time_budget_s=150)
    assert (cut.asked, cut.written, cut.failed, cut.not_attempted) == (4, 1, 0, 3)
    # The counts are the manifest's rows, counted: the same story twice.
    manifest = pd.read_csv(io.StringIO(_zip(cut).read("manifest.csv").decode("utf-8")),
                           dtype=str, keep_default_na=False)
    assert manifest["error"].str.startswith("NotAttempted").sum() == cut.not_attempted
    assert (manifest["file"] != "").sum() == cut.written
    # Repeated structure IDs are asked for once.
    assert bulk.swc_archive([1, 1, 1], lambda i: line_neuron(), volume="i").asked == 1


# -- W11 F02: a synapse listed by both of its cells --------------------------------------
class FakeClient:
    """One ribbon synapse: cell 1's pre structure 101 is linked to cell 2's post 202."""

    def __init__(self, labels=None, label_error=None):
        self.labels, self.label_error = labels or {1: "A", 2: "B"}, label_error

    def fetch_structure_links(self, ids):
        link = {"SourceID": 101, "TargetID": 202, "Source": {"ParentID": 1},
                "Target": {"ParentID": 2}}
        return [link] if (101 in ids or 202 in ids) else []

    def get_labels(self, ids):
        if self.label_error:
            raise self.label_error
        return {i: self.labels[i] for i in ids}


class FakeCell:
    def __init__(self, cell_id, kind, synapse_id):
        self.id, self._kind, self._synapse = cell_id, kind, synapse_id

    def synapse_ids(self, kind):
        return np.array([self._synapse] if kind.value == self._kind else [], dtype=int)

    def synapse_names(self):
        return [self._kind]


def _link_rows(cell, direction_pre):
    """The rows the service turns a cell's link table into, as the facade does."""
    table = all_linked_neurons(cell, FakeClient())
    rows = []
    for row in table.itertuples():
        pre = row.Direction == "pre"
        rows.append({"SourceID": cell.id if pre else int(row.NeuronID),
                     "TargetID": int(row.NeuronID) if pre else cell.id,
                     "SynapseID": int(row.SynapseID),
                     "PartnerSynapseID": int(row.PartnerSynapseID),
                     "SynapseType": row.SynapseType})
    assert direction_pre == (rows[0]["SourceID"] == cell.id)
    return rows


def test_the_link_table_names_the_other_end_of_the_link():
    a = linked_neurons(FakeCell(1, "RibbonPre", 101), "RibbonPre", FakeClient())
    b = linked_neurons(FakeCell(2, "RibbonPost", 202), "RibbonPost", FakeClient())
    assert a[["NeuronID", "SynapseID", "PartnerSynapseID"]].iloc[0].tolist() == [2, 101, 202]
    assert b[["NeuronID", "SynapseID", "PartnerSynapseID"]].iloc[0].tolist() == [1, 202, 101]


def test_a_synapse_between_two_loaded_cells_is_one_link():
    rows = _link_rows(FakeCell(1, "RibbonPre", 101), True) \
        + _link_rows(FakeCell(2, "RibbonPost", 202), False)
    both = network_from_links(pd.DataFrame(rows))
    assert both.weight(1, 2) == 1 and both.graph.number_of_edges() == 1
    # The presynaptic side names the link, whichever cell was listed first.
    reversed_order = network_from_links(pd.DataFrame(rows[::-1]))
    assert reversed_order.weight(1, 2) == 1
    assert both.graph[1][2]["types"] == reversed_order.graph[1][2]["types"] == ["RibbonPre"]
    edge = next(e for e in cytoscape_elements(both) if "source" in e["data"])
    assert edge["data"]["weight"] == 1
    # With one cell loaded, the count is what it always was.
    assert network_from_links(pd.DataFrame(rows[:1])).weight(1, 2) == 1


def test_the_service_and_the_route_count_a_synapse_between_two_loaded_cells_once(monkeypatch):
    """The double-count fix, pinned where it ships (SPEC-1).  ``_link_rows`` above rebuilds
    the facade's rows by hand and feeds ``network_from_links``; a ``SbfsemService.network()``
    that stopped passing ``PartnerSynapseID`` (the column dropped, or set to None) reinstated
    the double count and left every test but this one green."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from sbfsem_web.app import create_app

    cells = {1: FakeCell(1, "RibbonPre", 101), 2: FakeCell(2, "RibbonPost", 202)}
    tables = {i: all_linked_neurons(cell, FakeClient()) for i, cell in cells.items()}
    app = create_app()
    with TestClient(app) as client:
        svc = app.state.service
        svc.offline = True
        monkeypatch.setattr(svc, "neuron", lambda i, source: SimpleNamespace(id=i, label={1: "A", 2: "B"}[i]))
        monkeypatch.setattr(svc, "links", lambda i, source: tables[i])
        both = svc.network([1, 2], "i")
        assert (both.n_nodes, both.n_edges) == (2, 1)
        edge = next(e for e in both.elements if "source" in e["data"])
        assert edge["data"]["weight"] == 1 and edge["data"]["source"] == "1"
        # The same story through GET /api/network, which the viewer calls.
        body = client.get("/api/network", params={"source": "i", "ids": "1,2"}).json()
        assert (body["n_nodes"], body["n_edges"]) == (2, 1)
        assert next(e for e in body["elements"] if "source" in e["data"])["data"]["weight"] == 1
        # With one cell loaded, the count is what it always was, from either end.
        for loaded in (1, 2):
            one = svc.network([loaded], "i")
            assert next(e for e in one.elements if "source" in e["data"])["data"]["weight"] == 1


def test_synapses_with_no_partner_end_and_hand_made_tables_are_all_kept():
    table = pd.DataFrame({"SourceID": [1, 1], "TargetID": [2, 2],
                          "SynapseID": [7, 8], "SynapseType": ["ConvPre", "ConvPre"]})
    assert network_from_links(table).weight(1, 2) == 2
    table["PartnerSynapseID"] = [None, None]
    assert network_from_links(table).weight(1, 2) == 2


# -- W11 F10: a label lookup that fails ------------------------------------------------------
def test_a_failed_label_lookup_is_logged_and_marked_on_the_table(caplog):
    cell = FakeCell(1, "RibbonPre", 101)
    with caplog.at_level("WARNING", logger="sbfsem.network.links"):
        table = all_linked_neurons(cell, FakeClient(label_error=RuntimeError("down")))
    assert "labels" in caplog.text
    assert table.attrs.get("labels_missing") is True
    assert table["NeuronLabel"].tolist() == [""]
    ok = all_linked_neurons(cell, FakeClient())
    assert "labels_missing" not in ok.attrs and ok["NeuronLabel"].tolist() == ["B"]


def test_the_service_does_not_keep_a_link_table_that_lacks_its_labels(i943_path, monkeypatch):
    """A failed label lookup was swallowed and the blank-labeled table kept on the
    cell until someone refreshed."""
    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem.service import facade
    from sbfsem.service.facade import SbfsemService

    calls = []

    def fake(neuron, client):
        calls.append(1)
        table = pd.DataFrame({"NeuronID": [2], "NeuronLabel": ["" if len(calls) == 1 else "B"]})
        if len(calls) == 1:
            table.attrs["labels_missing"] = True
        return table

    monkeypatch.setattr(facade, "all_linked_neurons", fake)
    neuron = read_legacy_json(i943_path)
    svc = SbfsemService(offline=True)
    try:
        svc.add_neuron(neuron)
        assert svc.links(neuron.id, neuron.source)["NeuronLabel"].tolist() == [""]
        assert svc.links(neuron.id, neuron.source)["NeuronLabel"].tolist() == ["B"]
        assert svc.links(neuron.id, neuron.source)["NeuronLabel"].tolist() == ["B"]
        assert len(calls) == 2                       # kept once it was whole
    finally:
        svc.close()


# -- W11 F09: annotator times --------------------------------------------------------------------
def test_annotator_times_read_the_digits_as_utc_whatever_the_label():
    expected = datetime(2023, 5, 31, 4, 47, 0, tzinfo=UTC)
    assert _when("2023-05-31T04:47:00.000-07:00") == expected
    assert _when("2023-05-31T04:47:00.000Z") == expected
    assert _when("2023-05-31T04:47:00") == expected
    assert _when(datetime(2023, 5, 31, 4, 47, tzinfo=timezone(timedelta(hours=-8)))) == expected
    assert _when(None) is None and _when("nonsense") is None


# -- W11 F07: the dendrite diameter bins --------------------------------------------------------------
def test_the_diameter_bins_are_bounded():
    neuron = line_neuron()
    table = D.dendrite_diameter_by_soma_distance(neuron, bin_width=10.0)
    assert len(table) == 4
    for bad in (0.0, -1.0, 1e-9):
        with pytest.raises(ValueError):
            D.dendrite_diameter_by_soma_distance(neuron, bin_width=bad)
