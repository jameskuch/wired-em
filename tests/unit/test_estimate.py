"""The preflight estimate of a Closed curve or Hybrid render (sbfsem.service.estimate).

Held to real builds of the closed-curve fixture (a traced structure of three
outlines) and the disc-traced c943: the raster's voxels are exact, its exposed
faces follow the 1 / pixel and 1 / pixel^2 laws from a raster at another
pixel, the finest Voxel size that fits is the one the server's own refusal
names, and builds feed later estimates.  The route answers for the page's copy
of the cell, behind the volume guard, outside the build queue.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sbfsem.geometry.closed_curve import surface_fitting_pixel
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service import estimate
from sbfsem.service.estimate import (
    CURVE_RASTER_META,
    RasterCount,
    finest_by,
)
from sbfsem.service.facade import SbfsemService, _curve_outlines
from sbfsem_web.app import create_app

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "i90000_closedcurve.json"
CELL, SOURCE = 90000, "i"


@pytest.fixture(autouse=True)
def _no_reference_in_flight():
    """A head start a test left running (one raster at a time, process-wide) must not make
    the next test's first estimate pending: wait for it before and after."""
    assert estimate._REFERENCE_SLOT.acquire(timeout=120)
    estimate._REFERENCE_SLOT.release()
    yield
    assert estimate._REFERENCE_SLOT.acquire(timeout=120)
    estimate._REFERENCE_SLOT.release()


@pytest.fixture
def service():
    svc = SbfsemService(offline=True)
    svc.add_neuron(read_legacy_json(FIXTURE))
    yield svc
    svc.close()


def _estimate(svc, pixel, *, memory=9000.0, **options):
    return svc.render_estimate(CELL, SOURCE, memory_limit_mb=memory,
                               **{"method": "closedcurve", "pixel_size_um": pixel, **options})


def _built(svc, pixel):
    mesh = svc.mesh_for(svc.neuron(CELL, SOURCE), {"method": "closedcurve", "pixel_size_um": pixel})
    return mesh, RasterCount.from_meta(mesh.meta[CURVE_RASTER_META])


def test_a_raster_count_scales_in_plane_as_one_over_the_pixel_and_across_as_its_square():
    count = RasterCount(0.1, plane=1000, across=500)
    assert count.faces_at(0.1) == 1500
    assert count.faces_at(0.05) == pytest.approx(2000 + 2000)
    assert count.faces_at(0.025) == pytest.approx(4000 + 8000)
    assert RasterCount.from_meta(count.to_meta()) == RasterCount(0.1, 1000, 500, built=True)
    assert RasterCount.from_meta({"pixel_um": 0.1}) is None
    assert RasterCount.from_meta("nonsense") is None


def test_finest_by_finds_the_first_thousandth_that_fits():
    assert finest_by(lambda px: px >= 0.0213) == 0.022
    assert finest_by(lambda px: True) == 0.005
    assert finest_by(lambda px: False) is None


def test_a_build_leaves_its_raster_cost_in_the_mesh(service):
    mesh, count = _built(service, 0.05)
    assert count is not None and count.built
    assert count.pixel_um == 0.05
    assert count.triangles == len(mesh.faces)
    assert count.plane > 0 and count.across > 0
    # The count is the build's own raster's: what the limit was checked against.
    annotations, how = _curve_outlines(service.neuron(CELL, SOURCE), discs=True)
    from sbfsem.geometry.closed_curve import build_curve_volume, curve_grid_voxels

    volume = build_curve_volume(annotations, volume_scale=service.neuron(CELL, SOURCE).volume_scale,
                                pixel_size_um=0.05, **how)
    assert volume.exposed == (count.plane, count.across)
    assert count.voxels == curve_grid_voxels(
        annotations, volume_scale=service.neuron(CELL, SOURCE).volume_scale, pixel_size_um=0.05, **how)


def test_the_estimate_matches_the_builds_it_was_not_given(service):
    """Cold: no build of the cell yet, so a raster at 0.1 um is made and scaled."""
    answers = {px: _estimate(service, px) for px in (0.05, 0.02)}
    assert all(a["basis"] == {"kind": "reference", "pixel_um": 0.1} for a in answers.values())
    for px, answer in answers.items():
        mesh, count = _built(service, px)
        assert answer["voxels"] == count.voxels, "the voxels are counted exactly"
        assert answer["faces"] == pytest.approx(count.faces, rel=0.02)
        assert answer["triangles"] == pytest.approx(len(mesh.faces), rel=0.10)
        assert answer["over"] == []


def test_a_build_of_the_cell_is_what_later_estimates_scale(service):
    first = _estimate(service, 0.02)
    assert first["basis"]["kind"] == "reference"
    mesh, count = _built(service, 0.02)
    # The answer kept for 0.02 was scaled from less, and is not served again.
    again = _estimate(service, 0.02)
    assert again["basis"] == {"kind": "built", "pixel_um": 0.02}
    assert again["faces"] == count.faces
    assert again["triangles"] == len(mesh.faces)
    finer = _estimate(service, 0.015)
    assert finer["basis"] == {"kind": "built", "pixel_um": 0.02}
    assert finer["triangles_per_face"] == pytest.approx(len(mesh.faces) / count.faces, rel=0.05)


def test_the_finest_size_is_the_one_the_servers_refusal_names(service, monkeypatch):
    """With the face limit lowered under the cell, the estimate is over it and names the
    pixel the build would name: the same law, the same 5 % to spare."""
    _mesh, count = _built(service, 0.02)
    limit = count.faces // 2
    monkeypatch.setattr(estimate, "MAX_SURFACE", limit)
    answer = _estimate(service, 0.02)
    assert answer["over"] == ["faces"]
    assert answer["limits"]["faces"] == limit
    assert answer["binding"] == "faces"
    assert answer["finest_um"] == surface_fitting_pixel(0.02, count.plane, count.across, limit)
    assert answer["finest_um"] > 0.02
    # And at that size the estimate is under the limit.
    assert _estimate(service, answer["finest_um"])["over"] == []


def test_the_server_memory_limit_is_the_builds_budget_and_coarsens_too(service):
    tight = _estimate(service, 0.005, memory=900)
    assert "memory" in tight["over"]
    assert tight["limits"]["memory_mb"] == 900
    assert tight["finest_um"] > 0.005
    fits = _estimate(service, tight["finest_um"], memory=900)
    assert fits["over"] == []
    assert fits["server_mb"] <= 0.95 * 900 + 1
    # The same size on a server that gives builds more is not over.
    assert _estimate(service, 0.005)["over"] == []


def test_the_page_limit_is_the_longest_string_a_tab_parses(service, monkeypatch):
    monkeypatch.setattr(estimate, "PAGE_JSON_LIMIT_MB", 1.0)
    answer = _estimate(service, 0.01)
    assert "page" in answer["over"]
    assert answer["json_mb"] > 1.0
    assert _estimate(service, answer["finest_um"])["json_mb"] <= 0.95


def test_averaging_costs_memory_and_time(service):
    plain = _estimate(service, 0.02)
    averaged = _estimate(service, 0.02, curve_avg_paths=0.25)
    assert averaged["averaging"] and not plain["averaging"]
    assert averaged["peak_mb"] > plain["peak_mb"]
    assert averaged["seconds"] > plain["seconds"]
    assert averaged["faces"] == plain["faces"]


def test_joining_bodies_costs_memory_and_time_under_closed_curve_alone(service):
    """The viewer joins a Closed curve surface's bodies by default (2.6.2): the chip and the
    per-cell fit must count what that adds, about 260 bytes a triangle and a third more time
    (``estimate.JOIN_BYTES_PER_TRIANGLE``, ``JOIN_TIME``); Hybrid does not join, so it pays
    nothing."""
    plain = _estimate(service, 0.02, curve_tie_um=0.0)
    joined = _estimate(service, 0.02, curve_tie_um=5.0)
    assert joined["joins"] and not plain["joins"]
    assert joined["faces"] == plain["faces"] and joined["triangles"] == plain["triangles"]
    extra = estimate.JOIN_BYTES_PER_TRIANGLE * plain["triangles"] / 1e6
    assert abs((joined["peak_mb"] - plain["peak_mb"]) - extra) <= 1.0
    assert joined["seconds"] > plain["seconds"]
    hybrid = _estimate(service, 0.02, method="hybrid", curve_tie_um=5.0)
    assert not hybrid["joins"]
    assert hybrid["peak_mb"] == _estimate(service, 0.02, method="hybrid", curve_tie_um=0.0)["peak_mb"]


def test_other_methods_get_only_the_build_gates_memory(service):
    answer = _estimate(service, 0.05, method="cylinder")
    assert answer["applies"] is False
    assert answer["peak_mb"] == service.estimate_peak_mb(CELL, SOURCE, method="cylinder")
    assert "faces" not in answer


def test_a_disc_traced_cell_is_estimated_from_its_discs(c943):
    svc = SbfsemService(offline=True)
    try:
        svc.add_neuron(c943)
        answer = svc.render_estimate(943, "i", memory_limit_mb=9000, method="closedcurve",
                                     pixel_size_um=0.1)
        assert answer["applies"] and not answer.get("empty")
        assert answer["outlines"] > 100
        mesh = svc.mesh_for(svc.neuron(943, "i"), {"method": "closedcurve", "pixel_size_um": 0.1})
        assert answer["triangles"] == pytest.approx(len(mesh.faces), rel=0.02)
        # Hybrid on a cell with no traced outlines has no outline half to estimate.
        hybrid = svc.render_estimate(943, "i", memory_limit_mb=9000, method="hybrid",
                                     pixel_size_um=0.1)
        assert hybrid["empty"] is True
    finally:
        svc.close()


def test_an_estimate_at_a_coarse_size_makes_the_finer_reference_ahead(service):
    """A cell drawn at 0.25 um has only that count; the 0.1 um raster a finer size needs
    is made in the background as soon as anyone looks at an estimate of it."""
    import time

    coarse = _estimate(service, 0.25)
    assert coarse["basis"]["pixel_um"] == 0.25
    cells = service._estimator._cells
    deadline = time.time() + 60
    while time.time() < deadline and not any(
            0.1 in cell.counts for cell in cells.values()):
        time.sleep(0.05)
    assert any(0.1 in cell.counts for cell in cells.values()), "the 0.1 um reference was not made"
    fine = _estimate(service, 0.02)
    assert fine["basis"] == {"kind": "reference", "pixel_um": 0.1}


def _counting_rasters(monkeypatch) -> list[float]:
    """Every reference raster the estimator makes, by pixel."""
    made: list[float] = []
    real = estimate.build_curve_volume

    def counted(*args, **kwargs):
        made.append(kwargs["pixel_size_um"])
        return real(*args, **kwargs)

    monkeypatch.setattr(estimate, "build_curve_volume", counted)
    return made


def test_one_reference_raster_at_a_time_and_nobody_waits_for_it(service, monkeypatch):
    """Review 2026-10-16: every estimate of a cell without a fine count started a raster in
    the web process, as many at once as cells were asked about, and a request could wait on
    another's.  Now one at a time in the whole process: while it is taken, a cell with no
    count is pending, a cell with a coarse count is answered from it, approximate, and the
    head start is skipped, never queued; none of these is kept, and none waits."""
    import time

    made = _counting_rasters(monkeypatch)
    assert estimate._REFERENCE_SLOT.acquire(blocking=False)
    try:
        t0 = time.perf_counter()
        pending = _estimate(service, 0.02)
        assert time.perf_counter() - t0 < 2.0, "the request did not wait for the raster"
        assert pending["pending"] is True and pending["approximate"] is True
        assert pending["finest_um"] is None and pending["over"] == []
        assert pending["voxels"] > 0, "the voxels are counted all the same"
        assert made == []
        assert _estimate(service, 0.02)["pending"] is True, "not kept: asked again"
        _built(service, 0.25)                       # a coarse count, as a traced cell's load makes
        approximate = _estimate(service, 0.02)
        assert approximate["approximate"] is True and not approximate.get("pending")
        assert approximate["basis"] == {"kind": "built", "pixel_um": 0.25}
        time.sleep(0.3)
        assert made == [], "the head start was skipped, not queued"
    finally:
        estimate._REFERENCE_SLOT.release()
    exact = _estimate(service, 0.02)
    assert exact["approximate"] is False and exact["basis"] == {"kind": "reference", "pixel_um": 0.1}
    assert made == [0.1]
    assert _estimate(service, 0.02) == exact, "kept once exact"


def test_a_capped_reference_is_final_for_the_cell(service, monkeypatch):
    """Review 2026-10-16: a cell whose 0.1 um raster passed the voxel cap was rasterized at a
    coarser pixel, never counted as having its reference, and rasterized again on every new
    estimate (twice: the request and the head start)."""
    import time

    made = _counting_rasters(monkeypatch)
    monkeypatch.setattr(estimate, "REFERENCE_MAX_VOXELS", 10_000)
    first = _estimate(service, 0.02)
    assert len(made) == 1 and made[0] > 0.1, made
    assert first["basis"]["pixel_um"] == made[0]
    for pixel in (0.015, 0.03, 0.05, 0.25):
        assert _estimate(service, pixel)["approximate"] is False
    time.sleep(0.3)
    assert len(made) == 1, f"rasterized again: {made}"


def test_an_estimate_below_the_floor_is_refused_not_overflowed(service, client):
    with pytest.raises(ValueError, match="between 0.005 and 1"):
        _estimate(service, 1e-200)
    local, _app = client
    got = local.get(f"/api/neurons/{SOURCE}/{CELL}/render-estimate"
                    "?method=closedcurve&pixel_size_um=1e-200")
    assert got.status_code == 422 and "0.005" in got.json()["detail"]
    assert local.get(f"/api/neurons/{SOURCE}/{CELL}/render-estimate"
                     "?method=closedcurve&pixel_size_um=0.005").status_code == 200


def test_answers_are_kept_and_a_second_estimate_is_quick(service):
    first = _estimate(service, 0.03)
    second = _estimate(service, 0.03)
    assert second == first
    assert second["estimate_s"] == first["estimate_s"], "the kept answer, not a new one"


# -- the Fine voxel the server used --------------------------------------------------


def test_the_page_hears_which_fine_voxel_was_used_and_why(c943):
    """The whole-cell grid's voxel and why it came out coarser reach the page with the
    mesh (they were in the mesh's meta only, and nothing in the viewer read them)."""
    svc = SbfsemService(offline=True)
    try:
        svc.add_neuron(c943)
        typed = svc.render_mesh(943, "i", pathway="voxel2", voxel2_um=0.01)
        assert typed.voxel2_asked_um == pytest.approx(0.01)
        assert typed.voxel2_coarsened is True
        assert typed.voxel2_um > 0.01
        assert typed.voxel2_reason in ("floor", "faces", "work")
        auto = svc.render_mesh(943, "i", pathway="voxel2")
        assert auto.voxel2_um is not None and auto.voxel2_asked_um > 0
        other = svc.render_mesh(943, "i", pathway="adaptive_taubin")
        assert other.voxel2_um is None and other.voxel2_coarsened is None
        # The JSON the page reads carries them.
        assert '"voxel2_reason"' in typed.model_dump_json()
    finally:
        svc.close()


def test_a_fine_voxel_typed_under_the_old_floor_has_a_key_of_its_own():
    """A typed Fine voxel under 0.04 um was built at 0.04 until 2026-10-16 and stored under
    its own options; it is honored down to 0.02 now, so its key names the new floor and the
    old surface is not served.  Every other key, the automatic voxel's above all, is as it was
    (the geometry fingerprint is untouched)."""
    key = SbfsemService._options_key
    grid = {**SbfsemService._mesh_options(), "pathway": "voxel2"}
    for method in ("cylinder", "hybrid", None):
        assert key({**grid, "voxel2_um": 0.03}, method)[-1] == ("voxel2_floor", 0.02), method
        assert key({**grid, "voxel2_um": 0.01}, method)[-1] == ("voxel2_floor", 0.02), method
    for pathway in ("convolution", "ultraliser"):
        assert key({**grid, "pathway": pathway, "voxel2_um": 0.03}, "cylinder")[-1] == (
            "voxel2_floor", 0.02)
    off = SbfsemService.UNKEYED_AT_OFF

    def unkeyed(k, v) -> bool:
        # An option a stored surface was built without is left out at the value it was built with.
        return k in off and ((not v) if not off[k] else v == off[k])

    def before(options) -> tuple:
        return tuple(sorted((k, v) for k, v in options.items() if not unkeyed(k, v)))

    for unchanged in (0.0, 0.04, 0.05, 0.3):
        options = {**grid, "voxel2_um": unchanged}
        assert key(options, "cylinder") == before(options), unchanged
    # Only a surface the whole-cell grid makes reads the Fine voxel: every other keeps its key
    # whatever is typed there (review 2026-10-16: the entry was added for every method).
    for method, pathway in (("cylinder", "original"), ("cylinder", "voxel"), ("curvemesh", "original"),
                            ("closedcurve", "voxel2"), ("hybrid", "adaptive_taubin")):
        options = {**grid, "pathway": pathway, "voxel2_um": 0.03}
        assert key(options, method) == before(options), (method, pathway)


def test_the_build_gate_prices_a_fine_voxel_typed_under_the_old_floor(c943):
    """Temporal c106 typed at 0.02 um peaked at 931 MB and was priced 511: a typed Fine voxel
    under 0.04 um is priced at the sheet its raw budget allows (review 2026-10-16)."""
    from sbfsem.geometry.unify import raw_budget
    from sbfsem.service import facade

    svc = SbfsemService(offline=True)
    try:
        svc.add_neuron(c943)

        def peak(**kw):
            return svc.estimate_peak_mb(943, "i", method="cylinder", pathway="voxel2", **kw)

        auto = peak()
        assert peak(voxel2_um=0.05) == auto and peak(voxel2_um=0.04) == auto, "unchanged from 0.04 up"
        capped = peak(voxel2_um=0.02)
        sheet = facade.FIELD_SHEET_BASE_MB + raw_budget(400_000) * (
            facade.FIELD_SHEET_BYTES_PER_FACE + facade.FIELD_SHEET_BYTES_PER_DECIMATED_FACE) / 1e6
        assert capped == round(sheet) and capped > 931, capped
        assert peak(voxel2_um=0.02, voxel2_faces=0) > capped, "no cap: the whole 10 million"
        # Other pathways do not read it.
        assert svc.estimate_peak_mb(943, "i", pathway="voxel", voxel2_um=0.02) == svc.estimate_peak_mb(
            943, "i", pathway="voxel")
    finally:
        svc.close()


def _field_options(svc, **kw):
    return {**svc._mesh_options(pathway="voxel2", **kw), "method": "cylinder"}


def _gate(svc, options, *, planned=True):
    return svc.estimate_peak_mb(943, "i", method=options["method"], pathway=options["pathway"],
                                voxel2_faces=options["voxel2_faces"], voxel2_um=options["voxel2_um"],
                                field_options=options if planned else None)


def test_the_build_gate_prices_the_sheet_a_field_build_is_planned_to_draw(c943):
    """Under 3 million raw triangles the gate priced a field build by its annotations alone:
    RPC1 c430 at a Face cap of 1,460,000 drew 2.4 million and peaked at 2,582 MB, Inferior
    c1178 at 1,100,000 drew 2.3 million and peaked at 2,535, priced 2,546 and 1,451
    (2026-10-20).  Given the build's options it prices the sheet the build is sized to draw
    as well, whichever is more, and no build draws more than that sheet."""
    from sbfsem.service import facade

    svc = SbfsemService(offline=True)
    try:
        svc.add_neuron(c943)
        neuron = svc.neuron(943, "i")
        # A sheet under its cap is kept as drawn: no decimator's copy.
        under = _field_options(svc, voxel2_um=0.04, voxel2_faces=900_000)
        raw = svc._field_planner.raw_triangles(neuron, under)
        assert 0 < raw < 900_000
        sheet = facade.FIELD_SHEET_BASE_MB + raw * facade.FIELD_SHEET_BYTES_PER_FACE / 1e6
        assert _gate(svc, under, planned=False) < sheet
        assert _gate(svc, under) == round(sheet)
        # A sheet over its cap is decimated, and the copy is priced too.
        over = _field_options(svc, voxel2_um=0.04, voxel2_faces=150_000)
        raw_over = svc._field_planner.raw_triangles(neuron, over)
        assert raw_over > 150_000
        assert _gate(svc, over) == round(facade.FIELD_SHEET_BASE_MB + raw_over * (
            facade.FIELD_SHEET_BYTES_PER_FACE + facade.FIELD_SHEET_BYTES_PER_DECIMATED_FACE) / 1e6)
        # What a build draws is never more than the sheet priced.
        for options, planned in ((under, raw), (over, raw_over)):
            built = svc.build_mesh(neuron, **options)
            assert 0 < built.meta["voxel2_raw_faces"] <= planned, options["voxel2_faces"]
        # Where the annotations price more, that price stands: the default settings, and a
        # raw budget over 3 million, priced whole.
        for options in (_field_options(svc), _field_options(svc, voxel2_um=0.05, voxel2_faces=0)):
            assert _gate(svc, options) == _gate(svc, options, planned=False)
    finally:
        svc.close()


def test_the_sheet_law_counts_the_decimators_copy_only_when_it_runs():
    """Temporal c106 at the default cap drew 323,256 raw triangles, kept them all and peaked
    at 380 MB: a sheet is decimated only over its Face cap or under a Decimate of 1."""
    from sbfsem.service.facade import (
        FIELD_SHEET_BASE_MB,
        FIELD_SHEET_BYTES_PER_DECIMATED_FACE,
        FIELD_SHEET_BYTES_PER_FACE,
        field_sheet_mb,
    )

    kept = FIELD_SHEET_BASE_MB + 323_256 * FIELD_SHEET_BYTES_PER_FACE / 1e6
    copied = FIELD_SHEET_BASE_MB + 323_256 * (
        FIELD_SHEET_BYTES_PER_FACE + FIELD_SHEET_BYTES_PER_DECIMATED_FACE) / 1e6
    assert field_sheet_mb(323_256, 400_000) == pytest.approx(kept)
    assert field_sheet_mb(323_256, 0) == pytest.approx(kept)
    assert field_sheet_mb(323_256, 323_256) == pytest.approx(kept)
    assert field_sheet_mb(323_256, 300_000) == pytest.approx(copied)
    assert field_sheet_mb(323_256, 0, 0.5) == pytest.approx(copied)


def test_the_gate_plans_only_field_builds_on_the_pages_plans(c943, monkeypatch):
    """The plan runs in the web process on the mesh job's submit: after the page's estimate of
    the same settings it is the one already made.  Nothing but a whole-cell field is planned,
    and a plan that fails leaves the other price."""
    svc = SbfsemService(offline=True)
    try:
        svc.add_neuron(c943)
        options = _field_options(svc, voxel2_um=0.04, voxel2_faces=900_000)
        svc.render_estimate(943, "i", memory_limit_mb=9000, **options)
        planner = svc._field_planner
        held = (len(planner._plans), len(planner._bodies))
        priced = _gate(svc, options)
        assert (len(planner._plans), len(planner._bodies)) == held, "no plan of its own"
        assert priced > _gate(svc, options, planned=False)
        for method, pathway in (("cylinder", "voxel"), ("cylinder", "original"),
                                ("hybrid", "voxel2"), ("closedcurve", "voxel2")):
            kw = {"method": method, "pathway": pathway, "voxel2_faces": 900_000, "voxel2_um": 0.04}
            assert svc.estimate_peak_mb(943, "i", field_options={**options, "pathway": pathway},
                                        **kw) == svc.estimate_peak_mb(943, "i", **kw), (method, pathway)

        def fails(*_args, **_kw):
            raise RuntimeError("no plan")

        monkeypatch.setattr(planner, "raw_triangles", fails)
        assert _gate(svc, options) == _gate(svc, options, planned=False)
    finally:
        svc.close()


def test_a_mesh_job_is_weighed_by_the_sheet_it_will_draw(i943_path):
    app = create_app()
    with TestClient(app) as local:
        svc = app.state.service
        svc.offline = True
        svc._disk_meshes = None
        svc.add_neuron(read_legacy_json(i943_path))
        query = "method=cylinder&pathway=voxel2&voxel2_um=0.04&voxel2_faces=900000"
        field = local.get(f"/api/neurons/i/943/render-estimate?{query}").json()["field"]
        started = local.get(f"/api/neurons/i/943/mesh-job?{query}").json()
        assert started["weight_mb"] == field["gate_mb"] > svc.estimate_peak_mb(
            943, "i", method="cylinder", pathway="voxel2", voxel2_um=0.04, voxel2_faces=900_000)
        deadline = time.time() + 120
        while local.get(f"/api/mesh-jobs/{started['job_id']}").json()["status"] not in (
                "done", "error", "cancelled"):
            assert time.time() < deadline, "the build did not finish"
            time.sleep(0.05)


# -- a Hybrid whose outlines and discs are one field ----------------------------------


def _hybrid_options(svc, **kw):
    return {**svc._mesh_options(pathway="voxel2", **kw), "method": "hybrid", "pixel_size_um": 0.25}


def _hybrid_gate(svc, options, *, planned=True):
    return svc.estimate_peak_mb(1178, "i", method="hybrid", pathway=options["pathway"],
                                pixel_size_um=options["pixel_size_um"],
                                voxel2_faces=options["voxel2_faces"], voxel2_um=options["voxel2_um"],
                                curve_options=options, field_options=options if planned else None)


def _disc_half_mb(neuron):
    """The old price of a Hybrid's disc half under 3 million raw triangles: 0.3 MB a disc and 420."""
    nodes = neuron.get_cell_nodes()
    return 120.0 + 0.3 * int((nodes["TypeCode"] == 1).sum()) + 300.0


@pytest.fixture
def mixed(c1178):
    """Inferior c1178: 130 traced outlines and 2,281 discs, the cell 2.7.0 made one field."""
    svc = SbfsemService(offline=True)
    svc.add_neuron(c1178)
    yield svc, svc.neuron(1178, "i")
    svc.close()


def test_the_build_gate_prices_a_one_field_hybrid_by_the_sheet_it_is_planned_to_draw(mixed):
    """A Hybrid in one field (2.7.0) draws its outlines into its discs' sheet, at the discs'
    voxel, and was priced by its outline raster and its disc annotations: at the default
    settings RPC1 c2628 drew 2.66 million raw triangles and peaked at 2,921 MB, priced 783, and
    Inferior c16137 peaked at 1,491, priced 1,140 (2026-10-20).  Given the build's options its
    disc half is never priced under the sheet it is sized to draw, planned as the build plans it
    (the voxel the build contours at, never fewer raw triangles than it draws), with the outline
    raster on top."""
    from sbfsem.service.facade import field_sheet_mb

    svc, neuron = mixed
    planner = svc._field_planner
    options = _hybrid_options(svc)
    cap = options["voxel2_faces"]
    raw = planner.raw_triangles(neuron, options)
    outline = planner.outline_triangles(neuron, options)
    assert raw > 3 * cap and outline > 0
    old = _hybrid_gate(svc, options, planned=False)
    priced = _hybrid_gate(svc, options)
    assert priced > old
    # The outline raster as before, and the disc half at the sheet: the outline half's own
    # triangles join the Face cap the sheet is decimated to.
    assert priced == pytest.approx(old - _disc_half_mb(neuron)
                                   + field_sheet_mb(raw, cap + outline), abs=1)
    built = svc._build_geometry(neuron, "hybrid", 1, 1.0, 0.25,
                                {"pathway": "voxel2", "voxel2_faces": cap})
    assert built.meta.get("hybrid_one_field") is True
    plan = planner._plan(neuron, options, 0.0, cap)
    assert built.meta["voxel2_um"] == pytest.approx(plan.voxel_um, rel=1e-9)
    assert 0 < built.meta["voxel2_raw_faces"] <= raw
    assert len(built.faces) <= cap + outline


def test_a_hybrids_plan_is_kept_apart_from_the_whole_cells(mixed):
    """The disc half is planned from a copy of the cell with its id and load serial
    (``_disc_subset``): its bodies and plans are kept under keys of their own, per raster pixel
    and bridges, and the whole cell's plan is the one a planner that never saw the Hybrid makes."""
    from sbfsem.service.estimate import FieldPlanner
    from sbfsem.service.facade import field_sheet_mb

    svc, neuron = mixed
    planner = svc._field_planner
    whole = svc._mesh_options(pathway="voxel2", voxel2_faces=150_000)
    hybrid = _hybrid_options(svc, voxel2_faces=150_000)
    alone = FieldPlanner(svc._field_pieces, lambda _n, _o: 0.0, field_sheet_mb).raw_triangles(
        neuron, whole)
    half = planner.raw_triangles(neuron, hybrid)
    assert planner.raw_triangles(neuron, whole) == alone != half
    assert planner.outline_triangles(neuron, whole) == 0 < planner.outline_triangles(neuron, hybrid)
    keys = {planner._cell_key(neuron, o) for o in (
        whole, hybrid, {**hybrid, "pixel_size_um": 0.5}, {**hybrid, "curve_bridge_um": 3.0})}
    assert len(keys) == 4


def test_a_hybrid_too_large_to_plan_here_is_priced_at_the_most_it_may_draw(mixed, monkeypatch):
    """The plan rasters the outlines in the web process, within the render estimate's bounds and
    in its one slot: a raster over them is not made (and not tried again), nor while the slot
    stays busy, and the build is priced at the most any budget lets it draw."""
    from sbfsem.geometry.unify import MAX_RAW_FACES
    from sbfsem.service.facade import field_sheet_mb

    svc, neuron = mixed
    planner = svc._field_planner
    options = _hybrid_options(svc)
    most = _hybrid_gate(svc, options, planned=False) - _disc_half_mb(neuron) \
        + field_sheet_mb(MAX_RAW_FACES, options["voxel2_faces"])
    # The slot held elsewhere: priced at the most, and planned once it is free.
    monkeypatch.setattr(estimate, "OUTLINE_PLAN_WAIT_S", 0.01)
    assert estimate._REFERENCE_SLOT.acquire(timeout=5)
    try:
        assert _hybrid_gate(svc, options) == pytest.approx(most, abs=1)
    finally:
        estimate._REFERENCE_SLOT.release()
    assert planner.raw_triangles(neuron, options) < MAX_RAW_FACES
    # Over the bounds: priced at the most, and the raster is not tried again.
    over = {**options, "pixel_size_um": 0.2}
    monkeypatch.setattr(estimate, "OUTLINE_PLAN_MAX_VOXELS", 1_000)
    asked = []
    halves = planner._halves_of
    monkeypatch.setattr(planner, "_halves_of", lambda *a: asked.append(1) or halves(*a))
    assert planner.raw_triangles(neuron, over) == MAX_RAW_FACES
    assert planner.raw_triangles(neuron, over) == MAX_RAW_FACES
    assert len(asked) == 1


def test_only_a_one_field_hybrid_is_planned_and_a_failed_plan_leaves_the_old_price(mixed,
                                                                                    monkeypatch):
    svc, neuron = mixed
    for pathway in ("convolution", "original", "adaptive_taubin", "voxel"):
        options = {**_hybrid_options(svc, voxel2_faces=150_000), "pathway": pathway}
        assert _hybrid_gate(svc, options) == _hybrid_gate(svc, options, planned=False), pathway
    ultraliser = {**_hybrid_options(svc), "pathway": "ultraliser"}
    assert _hybrid_gate(svc, ultraliser) > _hybrid_gate(svc, ultraliser, planned=False)

    def fails(*_args, **_kw):
        raise RuntimeError("no plan")

    options = _hybrid_options(svc)
    monkeypatch.setattr(svc._field_planner, "_halves_of", fails)
    assert _hybrid_gate(svc, options) == _hybrid_gate(svc, options, planned=False)


def _page(svc, options, *, memory=9000.0):
    return svc.render_estimate(1178, "i", memory_limit_mb=memory, **options)


def test_the_page_prices_a_one_field_hybrid_as_the_build_gate_does(mixed):
    """The cost chip added the old price of a Hybrid's discs alone to its outline half's:
    RPC1 c2628 at the default settings 989 MB, where the build peaks at 2,849 and the gate
    weighs it 3,293 (2026-10-20).  It is now the gate's price of the gate's own plan, made
    for the estimate and found by the gate, and the triangles are the ones the build keeps:
    its Face cap and its outline half's own."""
    svc, neuron = mixed
    planner = svc._field_planner
    options = _hybrid_options(svc)
    page = _page(svc, options)
    assert page["one_field"]["planned"] is True and page["one_field"]["pixel_um"] == 0.25
    raw = planner.raw_triangles(neuron, options)
    outline = planner.outline_triangles(neuron, options)
    assert page["one_field"]["raw_triangles"] == raw
    assert page["one_field"]["outline_triangles"] == outline
    assert page["peak_mb"] == pytest.approx(_hybrid_gate(svc, options), abs=1)
    assert page["triangles"] == options["voxel2_faces"] + outline
    assert page["server_mb"] == pytest.approx(
        page["peak_mb"] + estimate.SERVE_BASE_MB + estimate.SERVE_BYTES_PER_TRIANGLE
        * page["triangles"] / 1e6, abs=1)
    # Not a field of one surface: the two halves' prices, as before.
    for pathway in ("convolution", "original"):
        apart = _page(svc, {**options, "pathway": pathway})
        assert apart["one_field"] is None, pathway


def test_a_hybrids_estimate_is_kept_for_its_face_cap(mixed):
    """An answer was kept by the pixel and the method's own options, not the Face cap that
    sizes a one-field Hybrid's sheet: asked again at another cap, the page heard the first."""
    svc, _neuron = mixed
    low = _page(svc, _hybrid_options(svc, voxel2_faces=150_000))
    high = _page(svc, _hybrid_options(svc, voxel2_faces=400_000))
    assert high["triangles"] - low["triangles"] == 250_000
    assert high["peak_mb"] != low["peak_mb"]


def test_a_one_field_hybrid_too_fine_to_raster_here_is_priced_from_a_coarser_plan(mixed,
                                                                                    monkeypatch):
    """Past the raster the web process makes, the gate prices the most any budget lets the
    build draw (and runs it alone); the page, which coarsens a cell over the memory, prices
    it from the plan at the finest pixel coarser that it may make, carried to the pixel
    asked: never under that plan's own price, and under the gate's most."""
    svc, neuron = mixed
    fine = {**_hybrid_options(svc), "pixel_size_um": 0.1}
    cell = svc._estimator._cell(neuron, False)
    monkeypatch.setattr(estimate, "OUTLINE_PLAN_MAX_VOXELS", svc._estimator._voxels(cell, 0.1) - 1)
    page = _page(svc, fine)
    held = page["one_field"]
    assert held["planned"] is False and 0.1 < held["pixel_um"] < 0.25
    coarse = {**fine, "pixel_size_um": held["pixel_um"]}
    assert _hybrid_gate(svc, coarse) <= page["peak_mb"] < _hybrid_gate(svc, fine)
    assert held["raw_triangles"] >= svc._field_planner.raw_triangles(neuron, coarse)


def test_a_one_field_hybrid_over_the_memory_is_fitted_coarser_or_not_at_all(mixed):
    """Held to a memory between the old price and the gate's, the old estimate said it fits;
    now it is over, and the size that fits is searched from the one asked up: a one-field
    sheet stops being decimated where its outline half's triangles pass it, so its price
    need not grow as the pixel shrinks, and a finer size must never be offered."""
    svc, _neuron = mixed
    options = _hybrid_options(svc)
    limit = _hybrid_gate(svc, options) + 50.0
    over = _page(svc, options, memory=limit)
    assert "memory" in over["over"]
    assert over["finest_um"] is None or over["finest_um"] > options["pixel_size_um"]


def test_a_one_field_hybrids_estimate_waits_for_no_other_raster(mixed):
    """The plan is made in the one slot the web process rasters in; held by another raster,
    the page is answered at the old price, marked approximate (not kept, asked again), and
    priced from the plan once the slot is free."""
    svc, _neuron = mixed
    _page(svc, _hybrid_options(svc))
    assert estimate._REFERENCE_SLOT.acquire(timeout=120)
    try:
        held = _page(svc, {**_hybrid_options(svc), "pixel_size_um": 0.2})
    finally:
        estimate._REFERENCE_SLOT.release()
    assert held["approximate"] is True and held["one_field"] is None
    again = _page(svc, {**_hybrid_options(svc), "pixel_size_um": 0.2})
    assert again["approximate"] is False and again["one_field"]["planned"] is True
    assert again["peak_mb"] > held["peak_mb"]


# -- the route ----------------------------------------------------------------------


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as local:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(FIXTURE))
        yield local, app


def test_the_route_answers_for_the_cell_and_its_options(client):
    local, app = client
    got = local.get(f"/api/neurons/{SOURCE}/{CELL}/render-estimate"
                    "?method=closedcurve&pixel_size_um=0.02&clip_ipl=0.5")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["applies"] is True
    assert body["pixel_size_um"] == 0.02
    assert body["cell"] == CELL and body["copy"]
    assert body["limits"]["memory_mb"] == app.state.mesh_jobs.memory_mb
    assert {"voxels", "faces", "triangles", "peak_mb", "serve_mb", "server_mb", "seconds",
            "json_mb", "tab_mb", "over", "finest_um", "binding", "basis"} <= set(body)
    # It is not a build: nothing was queued.
    assert app.state.mesh_jobs.load()["running"] == 0
    assert app.state.mesh_jobs.load()["by_kind"] == {}


def test_the_route_validates_like_the_mesh_route_and_names_missing_cells(client):
    local, _app = client
    base = f"/api/neurons/{SOURCE}/{CELL}/render-estimate"
    assert local.get(f"{base}?pixel_size_um=0").status_code == 422
    assert local.get(f"{base}?pixel_size_um=2").status_code == 422
    assert local.get(f"{base}?method=nonsense").status_code == 400
    assert local.get(f"/api/neurons/{SOURCE}/1/render-estimate").status_code == 404
    # The copy is the page's: one that is not held is not answered from another.
    assert local.get(f"{base}?method=closedcurve&copy=curvefit.1").status_code == 404
