"""What the render panel is told before Apply about settings that will not build as set.

A whole-cell field (Voxel remesh, Convolution, Ultraliser) is sized before it is
contoured: the Fine voxel typed (or the annotations' own), held to the raw
triangles its Face cap allows and the field evaluations those allow
(``unify.plan_voxel``).  The render estimate runs that sizing on the build's own
pieces without contouring (``estimate.FieldPlanner``), so what it says is what a
build then does; these tests hold it to real builds of the disc-traced c943.
A Closed curve estimate past the server's memory names the finest Voxel size
with Join bodies or the smoothing off, which only add memory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sbfsem.geometry.unify import field_bodies, plan_voxel, raw_budget
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service import estimate
from sbfsem.service.facade import SbfsemService
from sbfsem_web.app import create_app

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "i90000_closedcurve.json"


@pytest.fixture
def svc(c943):
    service = SbfsemService(offline=True)
    service.add_neuron(c943)
    yield service
    service.close()


def _field(svc, **options):
    answer = svc.render_estimate(943, "i", memory_limit_mb=9000, method="cylinder",
                                 pathway="voxel2", **options)
    assert answer["applies"] is False, "the outline estimate is not for this method"
    return answer["field"]


@pytest.mark.parametrize(("typed", "cap"), [(0.0, 400_000), (0.03, 20_000), (0.02, 400_000),
                                            (0.05, 0), (0.01, 0)])
def test_the_voxel_estimated_is_the_voxel_the_build_uses(svc, typed, cap):
    field = _field(svc, voxel2_um=typed, voxel2_faces=cap)
    built = svc.render_mesh(943, "i", pathway="voxel2", voxel2_um=typed, voxel2_faces=cap)
    assert built.voxel2_um == pytest.approx(field["voxel_um"], abs=1e-4)
    assert bool(built.voxel2_coarsened) == field["coarsened"]
    assert (built.voxel2_reason or "") == field["reason"]


def test_the_cap_it_names_builds_the_voxel_typed(svc):
    held = _field(svc, voxel2_um=0.03, voxel2_faces=20_000)
    assert held["coarsened"] and held["reason"] == "faces" and held["voxel_um"] > 0.04
    way = held["cap_for_asked"]
    assert way is not None and way["voxel_um"] == pytest.approx(0.03)
    assert way["voxel2_faces"] % estimate.CAP_STEP == 0
    built = svc.render_mesh(943, "i", pathway="voxel2", voxel2_um=0.03,
                            voxel2_faces=way["voxel2_faces"])
    assert built.voxel2_um == pytest.approx(0.03) and not built.voxel2_coarsened
    # The least such cap, to the step: one step less does not build it.
    less = _field(svc, voxel2_um=0.03, voxel2_faces=way["voxel2_faces"] - estimate.CAP_STEP)
    assert less["voxel_um"] > 0.03
    # Face cap 0 builds it too, keeping every raw triangle.
    assert held["uncapped"]["voxel_um"] == pytest.approx(0.03)
    assert held["uncapped"]["triangles"] == held["uncapped"]["raw_triangles"] > way["voxel2_faces"]


def test_memory_is_the_sheet_the_cell_draws_not_the_whole_budget(svc):
    """The page is told what a build drawing the cell's raw triangles peaks at (c16137 at
    0.04 um, every triangle kept: 1,422 MB from the law, 1,429 measured); the build gate
    keeps pricing the whole budget a cap allows."""
    from sbfsem.service import facade

    held = _field(svc, voxel2_um=0.03, voxel2_faces=20_000)
    for way in (held["cap_for_asked"], held["uncapped"]):
        assert way["peak_mb"] == round(facade.field_sheet_mb(way["raw_triangles"], way["voxel2_faces"]))
        assert way["gate_mb"] >= way["peak_mb"] - 1
    # A cap of a million or less decimates the sheet whole, beside it; over that a chunk at a time.
    whole = facade.field_sheet_mb(2_000_000, 1_000_000)
    assert whole == facade.FIELD_SHEET_BASE_MB + 2_000_000 * (
        facade.FIELD_SHEET_BYTES_PER_FACE + facade.FIELD_SHEET_BYTES_PER_DECIMATED_FACE) / 1e6
    assert facade.field_sheet_mb(2_000_000, 1_100_000) == facade.field_sheet_mb(
        2_000_000, 0) + facade.FIELD_CHUNKED_DECIMATION_MB


def test_a_voxel_no_budget_reaches_is_said_with_the_finest_there_is(svc):
    floor = _field(svc, voxel2_um=0.01, voxel2_faces=0)
    assert floor["reason"] == "floor" and floor["voxel_um"] == pytest.approx(0.02)
    assert floor["cap_for_asked"] is None and floor["uncapped"] is None
    assert floor["finest_um"] == pytest.approx(0.02)
    auto = _field(svc)
    assert not auto["typed"] and not auto["coarsened"]
    assert auto["finest_at_cap_um"] > auto["finest_um"]


def test_plan_voxel_is_the_builds_sizing(c943):
    """The planner and a build share one sizing: the planner's answer for the build's
    own pieces at several caps is the voxel the estimate reports."""
    from sbfsem.geometry.cylinder import STRAND_COIN_HALF_UM, field_pieces

    bodies, thin = field_bodies(field_pieces(c943, pathway="voxel2"), lens_h=STRAND_COIN_HALF_UM)
    plans = [plan_voxel(bodies, thin, voxel_um=0.03, max_raw_faces=raw_budget(cap))
             for cap in (20_000, 460_000, 0)]
    assert [p.reason for p in plans] == ["faces", "", ""]
    assert plans[0].voxel_um > plans[1].voxel_um == pytest.approx(0.03)
    assert plans[0].raw_faces == pytest.approx(plans[0].raw_budget, rel=0.01)
    assert plan_voxel([], [], voxel_um=0.03) is None


def test_estimates_are_kept_and_cleared_with_the_renders(svc):
    first = _field(svc, voxel2_um=0.03, voxel2_faces=20_000)
    again = _field(svc, voxel2_um=0.03, voxel2_faces=20_000)
    assert again == first and again["estimate_s"] == first["estimate_s"]
    svc.clear_renders()
    assert svc._field_planner._answers == {} and svc._field_planner._bodies == {}
    assert svc._field_planner._plans == {}


def test_other_pathways_and_methods_get_no_field(svc):
    for options in ({"pathway": "voxel"}, {"pathway": "original"}, {"method": "closedcurve"}):
        answer = svc.render_estimate(943, "i", memory_limit_mb=9000,
                                     **{"method": "cylinder", **options})
        assert "field" not in answer, options


# -- Closed curve: the size with Join bodies or the smoothing off --------------------------


@pytest.fixture
def traced():
    service = SbfsemService(offline=True)
    service.add_neuron(read_legacy_json(FIXTURE))
    yield service
    service.close()


def _curve(svc, pixel, memory, **options):
    return svc.render_estimate(90000, "i", memory_limit_mb=memory,
                               **{"method": "closedcurve", "pixel_size_um": pixel, **options})


def test_past_the_memory_it_names_the_size_with_what_only_adds_memory_off(traced):
    joined = _curve(traced, 0.005, 900, curve_tie_um=5.0, curve_avg_paths=0.25)
    assert "memory" in joined["over"]
    ways = {a["off"]: a for a in joined["alternatives"]}
    assert set(ways) == {"joins", "averaging", "both"}
    assert ways["joins"]["set"] == {"curve_tie_um": 0.0}
    assert ways["averaging"]["set"] == {"curve_avg_paths": 0.0}
    assert ways["both"]["set"] == {"curve_tie_um": 0.0, "curve_avg_paths": 0.0}
    for way in ways.values():
        assert way["finest_um"] < joined["finest_um"]
        # The size it names fits with those settings, as the estimate then says.
        then = _curve(traced, way["finest_um"], 900, **{"curve_tie_um": 5.0,
                                                        "curve_avg_paths": 0.25, **way["set"]})
        assert then["over"] == [], way
    assert ways["both"]["finest_um"] <= min(ways["joins"]["finest_um"], ways["averaging"]["finest_um"])


def test_nothing_is_named_when_neither_is_on_or_the_limit_is_not_memory(traced, monkeypatch):
    plain = _curve(traced, 0.005, 900)
    assert "memory" in plain["over"] and plain["alternatives"] == []
    fits = _curve(traced, 0.05, 9000, curve_tie_um=5.0)
    assert fits["over"] == [] and fits["alternatives"] == []
    # Past the faces alone, turning either off changes nothing.
    monkeypatch.setattr(estimate, "MAX_SURFACE", 1000)
    faces = _curve(traced, 0.01, 90_000, curve_tie_um=5.0)
    assert faces["over"] == ["faces"] and faces["alternatives"] == []


# -- the route -------------------------------------------------------------------------------


def test_the_route_answers_the_field_for_a_field_pathway(i943_path):
    app = create_app()
    with TestClient(app) as local:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(i943_path))
        got = local.get("/api/neurons/i/943/render-estimate"
                        "?method=cylinder&pathway=voxel2&voxel2_um=0.03&voxel2_faces=20000")
        assert got.status_code == 200, got.text
        field = got.json()["field"]
        assert field["voxel_um"] > 0.03 and field["cap_for_asked"]["voxel_um"] == pytest.approx(0.03)
        assert {"typed", "asked_um", "reason", "raw_budget", "work", "work_budget", "peak_mb",
                "gate_mb", "uncapped", "finest_um", "finest_at_cap_um"} <= set(field)
        assert app.state.mesh_jobs.load()["by_kind"] == {}, "nothing was built"
