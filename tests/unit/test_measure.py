"""sbfsem-measure: the numbers a geometry claim quotes."""

from __future__ import annotations

import json

import numpy as np
import pytest

from sbfsem.geometry.mesh import Mesh, concatenate
from sbfsem.geometry.meshdiff import visual_bodies
from sbfsem.geometry.primitives import sphere
from sbfsem.service import measure
from sbfsem.service.measure import (
    RenderMeasurement,
    main,
    measure_render,
    technique_options,
)


def test_visual_bodies_counts_touching_pieces_as_one():
    a = sphere(1.0)
    far = sphere(1.0).translate([5.0, 0.0, 0.0])
    near = sphere(1.0).translate([2.1, 0.0, 0.0])  # 0.1 um gap: reads as one
    assert visual_bodies(a) == 1
    assert visual_bodies(concatenate([a, far])) == 2
    assert visual_bodies(concatenate([a, near])) == 1
    assert visual_bodies(concatenate([a, near, far])) == 2
    assert visual_bodies(Mesh(np.zeros((0, 3)), np.zeros((0, 3)))) == 0


def test_visual_bodies_ignores_unreferenced_vertices():
    a = sphere(1.0)
    stray = Mesh(np.vstack([a.vertices, [[40.0, 40.0, 40.0]]]), a.faces)
    assert visual_bodies(stray) == 1


def test_technique_options_resolve_aliases_and_overrides():
    assert technique_options("voxel") == {"method": "cylinder", "pathway": "voxel"}
    assert technique_options("adaptive_taubin")["pathway"] == "adaptive_taubin"
    assert technique_options("tube")["smooth_iterations"] == 0
    assert technique_options("voxel", {"tie_um": 0})["tie_um"] == 0
    with pytest.raises(ValueError, match="unknown technique"):
        technique_options("balloon")


#: The names ``--technique`` took before they were derived from the app's
#: preset list, with what each meant, pinned: a measurement quoted last week
#: must still mean the same thing.
PINNED_TECHNIQUES = {
    "voxel": {"method": "cylinder", "pathway": "voxel"},
    "voxel2": {"method": "cylinder", "pathway": "voxel2"},
    "convolution": {"method": "cylinder", "pathway": "convolution"},
    "ultraliser": {"method": "cylinder", "pathway": "ultraliser"},
    "original": {"method": "cylinder", "pathway": "original"},
    "adaptive": {"method": "cylinder", "pathway": "adaptive_taubin"},
    "wrap": {"method": "cylinder", "pathway": "wrap"},
    "tube": {"method": "curvemesh", "pathway": "original", "smooth_iterations": 0},
    "closedcurve": {"method": "closedcurve", "pathway": "original", "pixel_size_um": 0.25},
    "hybrid": {"method": "hybrid", "pathway": "voxel", "pixel_size_um": 0.25},
}


def test_techniques_are_the_apps_presets_and_keep_their_old_meanings():
    for name, options in PINNED_TECHNIQUES.items():
        assert measure.TECHNIQUES[name] == options, name
        assert technique_options(name) == options, name
    assert technique_options("adaptive_taubin") == PINNED_TECHNIQUES["adaptive"]
    # The three MeshLab entries of the app's pickers are names here too.
    for name, mode in (("voxel2_iso", "isotropic"), ("voxel2_qem", "qem"),
                       ("voxel2_poisson", "poisson")):
        assert technique_options(name) == {
            "method": "cylinder", "pathway": "voxel2", "meshlab": mode}
    assert set(measure.TECHNIQUES) == set(PINNED_TECHNIQUES) | {
        "voxel2_iso", "voxel2_qem", "voxel2_poisson"}


def test_measure_render_reports_every_number(c943):
    m = measure_render(c943, "original", samples=5_000)
    assert m.cell == 943
    assert m.technique == "original"
    assert m.bodies >= 1
    assert m.faces > 0 and m.vertices > 0
    assert m.build_s > 0 and m.peak_rss_mb > 0
    assert m.n_discs > 0
    assert m.dev_mean_um is not None and m.dev_mean_um >= 0
    assert m.dev_p95_um is not None and m.dev_max_um is not None
    # Round-trips through the JSON the CLI writes and --compare reads.
    assert RenderMeasurement.from_dict(json.loads(json.dumps(m.to_dict()))) == m


def test_measure_render_rejects_a_mistyped_knob(c943):
    with pytest.raises(ValueError, match="unknown render option"):
        measure_render(c943, "voxel", {"voxel_smoothing_um": 0.3})


def test_cli_lists_and_tabulates(capsys, tmp_path, monkeypatch, c943):
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "voxel" in out and "tie_um" in out

    # --in-process avoids spawning a child; the neuron comes from a stub
    # service so no cache or network is involved.
    real = measure.SbfsemService(offline=True)

    class _Svc:
        def __init__(self, **_):
            pass

        def load_neuron(self, *_a, **_k):
            return None

        def neuron(self, *_a):
            return c943

        def build_mesh(self, neuron, **options):
            return real.build_mesh(neuron, **options)

        def close(self):
            pass

    monkeypatch.setattr(measure, "SbfsemService", _Svc)
    before = tmp_path / "before.json"
    assert main(["943", "--technique", "original", "--in-process",
                 "--samples", "3000", "--json", str(before)]) == 0
    out = capsys.readouterr().out
    assert "c943 original" in out and before.exists()
    assert main(["943", "--technique", "original", "--in-process",
                 "--samples", "3000", "--compare", str(before)]) == 0
    out = capsys.readouterr().out
    assert "Δ vs baseline" in out


def test_voxel2_is_a_named_technique():
    from sbfsem.service.measure import TECHNIQUES, technique_options

    assert "voxel2" in TECHNIQUES
    assert technique_options("voxel2", {"voxel2_um": 0.1}) == {
        "method": "cylinder", "pathway": "voxel2", "voxel2_um": 0.1,
    }
