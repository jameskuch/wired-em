"""Routes touched by the 2026-10-15 audit of the core library (W10 and W11)."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from sbfsem.io.legacy_json import read_legacy_json
from sbfsem_web.api import routes
from sbfsem_web.app import create_app


@pytest.fixture(scope="module")
def client(request):
    app = create_app()
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as test_client:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield test_client


def test_a_bulk_archive_past_its_time_budget_is_sent_with_the_rest_named(client, monkeypatch):
    """200 cold cells could not finish inside the gateway's 300 s and ended as a
    504 holding nothing; past the budget the archive is sent with what was built
    and a manifest row that says which structures to ask for again."""
    monkeypatch.setattr(routes, "BULK_EXPORT_BUDGET_S", -1.0)
    response = client.get("/api/neurons/i/export-bulk/swc?ids=943,944")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["manifest.csv"]
        text = archive.read("manifest.csv").decode("utf-8")
    assert text.count("NotAttempted") == 2
    assert routes.BULK_EXPORT_BUDGET_S == -1.0


def test_a_bulk_archive_says_in_its_headers_what_it_holds_and_what_it_lacks(client, monkeypatch):
    """A 200 with only manifest.csv looked like a finished export to the viewer, which
    reads the response and not the zip.  The headers carry the counts."""
    whole = client.get("/api/neurons/i/export-bulk/swc?ids=943")
    assert whole.status_code == 200
    assert {k: whole.headers[k] for k in whole.headers if k.lower().startswith("x-archive-")} == {
        "x-archive-asked": "1", "x-archive-written": "1",
        "x-archive-failed": "0", "x-archive-not-attempted": "0"}
    monkeypatch.setattr(routes, "BULK_EXPORT_BUDGET_S", -1.0)
    cut = client.get("/api/neurons/i/export-bulk/swc?ids=943,944,945")
    assert cut.status_code == 200
    assert (cut.headers["x-archive-asked"], cut.headers["x-archive-written"],
            cut.headers["x-archive-failed"], cut.headers["x-archive-not-attempted"]) == ("3", "0", "0", "3")
    # A structure that fails is counted as failed, not as not attempted.
    monkeypatch.setattr(routes, "BULK_EXPORT_BUDGET_S", 240.0)
    mixed = client.get("/api/neurons/i/export-bulk/swc?ids=943,999999")
    assert (mixed.headers["x-archive-asked"], mixed.headers["x-archive-written"],
            mixed.headers["x-archive-failed"], mixed.headers["x-archive-not-attempted"]) == ("2", "1", "1", "0")


def test_the_budget_is_under_the_gateway_s_read_timeout():
    # deploy/nginx-sbfsem*.conf: proxy_read_timeout 300s.
    assert 0 < routes.BULK_EXPORT_BUDGET_S < 300


def test_a_cell_too_long_for_its_bin_width_is_a_400_not_a_500(client, monkeypatch):
    """A width inside the route's range (0.05 to 1000) over a cell long enough to need more
    than the library's bins is the library's ValueError, which the route answers 400; the
    suite had the library's raise but not the mapping (a cell of 2,500 um is needed at 0.05,
    so the limit is lowered instead)."""
    import sbfsem.analysis.dendrite as dendrite

    assert client.get("/api/neurons/i/943/dendrite-diameter?bin_width=0.05").status_code == 200
    monkeypatch.setattr(dendrite, "MAX_DIAMETER_BINS", 3)
    response = client.get("/api/neurons/i/943/dendrite-diameter?bin_width=0.05")
    assert response.status_code == 400
    assert "at most 3 are drawn" in response.json()["detail"] and "bins" in response.json()["detail"]


def test_dendrite_diameter_bin_width_is_bounded(client):
    assert client.get("/api/neurons/i/943/dendrite-diameter").json()["rows"]
    assert client.get("/api/neurons/i/943/dendrite-diameter?bin_width=2").status_code == 200
    for bad in ("0", "-1", "0.001", "5000"):
        response = client.get(f"/api/neurons/i/943/dendrite-diameter?bin_width={bad}")
        assert response.status_code == 422, bad
