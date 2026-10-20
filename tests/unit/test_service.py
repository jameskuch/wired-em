"""The service facade must work with no display and no GUI toolkit installed.

That constraint is what keeps a headless deployment possible, so it is asserted
rather than left as a convention.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from sbfsem.core.volumes import Volumes
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.facade import NeuronNotLoadedError, SbfsemService

BACKEND_PACKAGES = ["sbfsem"]
FORBIDDEN_IMPORTS = ("PyQt", "PySide", "pyvista", "vtk", "tkinter", "sbfsem_web")


@pytest.fixture
def service(i943_path):
    svc = SbfsemService(offline=True)
    svc.add_neuron(read_legacy_json(i943_path))
    yield svc
    svc.close()


def test_a_vessel_added_since_the_overlay_was_built_is_found(monkeypatch):
    """The vessel list is asked again after an hour (``ODataClient.LIST_MAX_AGE``), but the
    overlay's mesh was memoized in memory under a key with no sign of which vessels it held,
    so ``pull()`` and the expiry were never reached again and a vessel added after the first
    request was not drawn until the process restarted.  The list is asked for first (through
    the disk cache: cheap), and the vessels it names are part of the memo's key."""
    import sbfsem.io.structure_group as groups
    import sbfsem.service.facade as facade
    from sbfsem.geometry.mesh import Mesh

    log = {"pulls": 0, "loads": [], "renders": 0}
    listed = {"ids": [10, 11]}

    class StubVasculature:
        def __init__(self, source, *, client=None):
            self.ids = np.empty(0, dtype=np.int64)

        def pull(self):
            log["pulls"] += 1
            self.ids = np.array(listed["ids"], dtype=np.int64)

        def load_vessels(self):
            log["loads"].append(tuple(int(i) for i in self.ids))
            return {int(i): type("Vessel", (), {"id": int(i)})() for i in self.ids}

    def render(vessel, **kwargs):
        log["renders"] += 1
        base = float(vessel.id)
        return Mesh(vertices=np.array([[base, 0, 0], [base + 1, 0, 0], [base, 1, 0]], dtype=float),
                    faces=np.array([[0, 1, 2]]))

    monkeypatch.setattr(groups, "Vasculature", StubVasculature)
    monkeypatch.setattr(facade, "cylinder_render", render)
    svc = SbfsemService(offline=True)
    try:
        first = svc.vessel_mesh("i", reduce_fraction=1.0)
        assert first.n_faces == 2 and log["loads"] == [(10, 11)]
        again = svc.vessel_mesh("i", reduce_fraction=1.0)
        assert again.n_faces == 2 and log["loads"] == [(10, 11)], "the same vessels: the mesh built once"
        assert log["pulls"] == 2, "but the list is asked every time, and it decides when Viking is"
        listed["ids"] = [10, 11, 12]                       # a vessel added in Viking
        grown = svc.vessel_mesh("i", reduce_fraction=1.0)
        assert grown.n_faces == 3, "the new vessel is drawn"
        assert log["loads"] == [(10, 11), (10, 11, 12)]
        listed["ids"] = []                                 # and all removed
        assert svc.vessel_mesh("i", reduce_fraction=1.0).n_faces == 0
    finally:
        svc.close()


def test_backend_never_imports_a_gui_toolkit():
    """An import-linter style contract, enforced as a test."""
    root = Path(__file__).resolve().parents[2] / "src" / "sbfsem"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            if any(name.lower() in stripped.lower() for name in FORBIDDEN_IMPORTS):
                offenders.append(f"{path.relative_to(root)}: {stripped}")
    assert offenders == [], "backend must not import a frontend: " + "; ".join(offenders)


def test_backend_imports_without_a_display():
    """Import and exercise the facade in a subprocess with DISPLAY unset."""
    code = (
        "import os; os.environ.pop('DISPLAY', None);\n"
        "from sbfsem.service.facade import SbfsemService;\n"
        "from sbfsem.io.legacy_json import read_legacy_json;\n"
        "import sys;\n"
        "s = SbfsemService(offline=True);\n"
        "s.add_neuron(read_legacy_json(sys.argv[1]));\n"
        "m = s.render_mesh(943, 'i');\n"
        "assert m.n_faces > 0;\n"
        "print('ok')\n"
    )
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "i943.json"
    result = subprocess.run(
        [sys.executable, "-c", code, str(fixture)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_load_render_analyze_export_cycle(service, tmp_path):
    detail = service.neuron_detail(943, "i")
    assert detail.summary.n_nodes == 190
    assert {c.local_name for c in detail.synapse_counts} == {"RibbonPost", "Unknown"}

    mesh = service.render_mesh(943, "i")
    assert mesh.n_faces > 1000
    assert len(mesh.vertices) == mesh.n_vertices * 3
    assert len(mesh.faces) == mesh.n_faces * 3

    skeleton = service.skeleton(943, "i")
    assert len(skeleton.segments) == 17

    hull = service.dendritic_field_hull(943, "i")
    assert hull.area_um2 > 0

    from sbfsem.export.json_v2 import write_neuron_json
    from sbfsem.export.swc import write_swc

    neuron = service.neuron(943, "i")
    assert write_swc(neuron, tmp_path / "c943.swc").stat().st_size > 0
    assert write_neuron_json(neuron, tmp_path / "c943.json").stat().st_size > 0


def test_mesh_caching_is_keyed_on_options(service):
    first = service.render_mesh(943, "i")
    again = service.render_mesh(943, "i")
    assert first.n_faces == again.n_faces
    coarse = service.render_mesh(943, "i", smooth_iterations=0)
    assert coarse.n_faces == first.n_faces  # same topology, different smoothing
    assert len(service._meshes) == 2


def test_unknown_neuron_raises(service):
    with pytest.raises(NeuronNotLoadedError):
        service.neuron(1, "i")


def test_unload(service):
    assert service.unload_neuron(943, "i")
    assert not service.unload_neuron(943, "i")
    assert service.loaded_neurons() == []


def test_dto_floats_are_json_safe(service):
    import json

    strat = service.stratification(943, "i")
    # c943 sits outside the boundary-marker range, so every value is NaN-ish.
    payload = json.dumps(strat.model_dump())
    assert "NaN" not in payload
    assert strat.stats.n == 0
    assert strat.mode is None


class _FakeODataClient:
    """resolve_label stub: 'cb' matches two cells, 'nothing' matches none."""

    def close(self):
        pass

    def resolve_label(self, term):
        if term.lower() == "cb":
            return [{"ID": 142, "Label": "CBb3n"}, {"ID": 138, "Label": "CBa"}]
        if term.lower() == "down":
            from sbfsem.io.odata import ODataError

            raise ODataError("service unreachable")
        return []


def test_resolve_structure_spec(service):
    """IDs, ranges and labels merge; every term and refusal is reported."""
    service._clients["NeitzInferiorMonkey"] = _FakeODataClient()
    out = service.resolve_structure_spec("i", "5-7, CB; down\nnope, 9, 7-")

    assert out.numeric_ids == [5, 6, 7, 9]
    # Label matches merge into the total, deduplicated and sorted.
    assert out.ids == [5, 6, 7, 9, 138, 142]

    by_term = {entry.term: entry for entry in out.labels}
    assert by_term["CB"].ids == [138, 142]
    assert by_term["CB"].samples == ["CBb3n", "CBa"]
    assert by_term["CB"].error is None
    # A term the service cannot answer is reported, not raised.
    assert "unreachable" in by_term["down"].error
    assert by_term["nope"].ids == []

    assert [e.text for e in out.invalid] == ["7-"]


def test_hybrid_on_a_disc_only_cell_is_the_cylinder_build(service):
    """With no traced outlines, Hybrid degrades to exactly the disc build."""
    hybrid = service.render_mesh(943, "i", method="hybrid")
    cylinder = service.render_mesh(943, "i", method="cylinder")
    assert hybrid.n_vertices == cylinder.n_vertices
    assert hybrid.n_faces == cylinder.n_faces
    assert hybrid.n_faces > 0


def test_closed_curve_on_a_disc_only_cell_rasterizes_its_discs(service):
    """Closed curve built nothing on a cell with no traced outline (Inferior
    c16137, 2026-10-15); its discs stand in, and the job gate counts their
    raster as the build sizes it."""
    mesh = service.render_mesh(943, "i", method="closedcurve", pixel_size_um=0.1)
    assert mesh.n_faces > 0

    from sbfsem.geometry.closed_curve import annotations_from_discs, curve_grid_voxels
    from sbfsem.service.facade import CURVE_BYTES_PER_VOXEL, _location_links

    neuron = service.neuron(943, "i")
    nodes = neuron.get_cell_nodes()
    xyz = np.asarray(mesh.vertices, dtype=float).reshape(-1, 3)
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    assert lo[2] >= nodes["Zum"].min() - 0.1 and hi[2] <= nodes["Zum"].max() + 0.1
    voxels = curve_grid_voxels(
        annotations_from_discs(nodes, _location_links(neuron), volume_scale=neuron.volume_scale),
        volume_scale=neuron.volume_scale, pixel_size_um=0.1, links=None, bridge_um=0.0)
    estimate = service.estimate_peak_mb(943, "i", method="closedcurve", pixel_size_um=0.1)
    assert estimate == round(150.0 + voxels * CURVE_BYTES_PER_VOXEL / 1e6)


def test_summary_counts_discs_and_curves(service):
    summary = service.neuron_detail(943, "i").summary
    assert summary.n_disc_annotations > 0
    assert summary.n_curve_annotations == 0
    assert summary.n_disc_annotations + summary.n_curve_annotations <= summary.n_nodes


def test_volume_listing(service):
    volumes = service.list_volumes()
    assert {v.name for v in volumes} >= {"NeitzInferiorMonkey", "NeitzCped", "DemoVolume"}
    assert {v.name for v in volumes} >= {"RC1", "RC2", "RPC1", "RPC2"}
    assert {v.display_name for v in volumes} >= {"Inferior Monkey", "Cped"}
    info = service.volume_info("i")
    assert info.volume_scale == [7.5, 7.5, 90.0]
    assert info.service_root.endswith("NeitzInferiorMonkey/OData/")
    assert service.volume_info("DemoVolume").service_root is None


def test_volume_listing_says_which_volumes_have_current_transforms(service):
    """`has_transforms` is the volume having a VikingXML read here, nothing else:
    the viewer hides the transforms switch for the others."""
    listed = {v.name: v.has_transforms for v in service.list_volumes()}
    assert {name for name, has in listed.items() if has} == {
        "NeitzInferiorMonkey", "NeitzCped", "RPC1", "RPC2"}
    for name in ("NeitzNasalMonkey", "NeitzTemporalMonkey", "RC1", "RC2", "DemoVolume"):
        assert listed[name] is False, name
    for volume in Volumes:
        assert service.volume_info(volume.source).has_transforms is (volume.vikingxml_url is not None)


def test_node_and_edge_dtos(service):
    nodes = service.nodes(943, "i")
    assert len(nodes) == 190
    assert all(n.parent_id == 943 for n in nodes)
    assert len(service.nodes(943, "i", cell_only=False)) == 199
    assert len(service.edges(943, "i")) == 194


def test_skeleton_segments_are_connected(service):
    skeleton = service.skeleton(943, "i")
    assert skeleton.neuron_id == 943
    for segment in skeleton.segments:
        assert len(segment.points_um) == len(segment.location_ids)
        assert len(segment.radii_um) == len(segment.location_ids)
    assert skeleton.soma_xyz_um is not None


def test_mesh_variants(service):
    plain = service.render_mesh(943, "i")
    clipped = service.render_mesh(943, "i", clip_z=140.0)
    assert clipped.n_faces < plain.n_faces
    below = service.render_mesh(943, "i", clip_z=140.0, keep_above=False)
    assert below.n_faces < plain.n_faces
    assert below.n_faces + clipped.n_faces <= plain.n_faces

    colored = service.render_mesh(943, "i", color_by_strata=True)
    assert colored.vertex_data is not None
    assert len(colored.vertex_data) == colored.n_vertices

    reduced = service.render_mesh(943, "i", reduce_fraction=0.3)
    assert reduced.n_faces <= plain.n_faces


def test_mesh_diff_between_two_settings(service):
    # Identical options resolve to the same cached mesh, and every vertex of A
    # is part of B's sample set, so the difference is exactly zero.
    same = service.mesh_diff(
        943, "i",
        options_a={"pathway": "original"},
        options_b={"pathway": "original"},
        samples=20_000,
    )
    assert same.mesh.n_vertices > 0
    assert len(same.mesh.vertex_data) == same.mesh.n_vertices
    assert same.max_abs_um == pytest.approx(0.0, abs=1e-9)

    # Smoothing moves vertices, so unsmoothed-vs-smoothed must disagree.
    changed = service.mesh_diff(
        943, "i",
        options_a={"pathway": "original", "smooth_iterations": 0},
        options_b={"pathway": "original"},
        samples=20_000,
    )
    assert changed.mean_abs_um > 0
    assert changed.sample_spacing_um > 0

    # Both renderings are also held to the cell's own annotations, in
    # absolute terms — the discs are the closest thing to ground truth.
    for dev in (changed.deviation_a, changed.deviation_b):
        assert dev is not None
        assert dev.mean_um > 0
        assert dev.surface_um >= 0 and dev.annotation_um >= 0
        assert dev.max_um >= dev.p95_um >= 0
        assert dev.excess_volume_um3 >= 0
        assert dev.n_discs > 0


def test_the_deviation_the_api_sends_carries_the_exterior_figures_the_library_computes(service):
    """``exterior_um``, ``annotation_exterior_um`` and ``hidden_fraction`` were computed for
    every deviation (``sbfsem-measure`` prints them) and never reached ``DeviationDTO``, so
    the API could not say how much of a render is buried inside itself."""
    from sbfsem.geometry.meshdiff import annotation_deviation
    from sbfsem.service.facade import annotation_ground_truth

    neuron = service.neuron(943, "i")
    options = {"method": "cylinder", "pathway": "original", "smooth_iterations": 0}
    truth = annotation_deviation(service.mesh_for(neuron, options), *annotation_ground_truth(neuron))
    assert truth.exterior_um is not None and truth.annotation_exterior_um is not None
    sent = [service.mesh_deviation(943, "i", options=options).deviation,
            service.mesh_diff(943, "i", options_a=options, options_b=options, samples=20_000).deviation_a]
    for dto in sent:
        assert dto.exterior_um == pytest.approx(truth.exterior_um)
        assert dto.annotation_exterior_um == pytest.approx(truth.annotation_exterior_um)
        assert dto.hidden_fraction == pytest.approx(truth.hidden_fraction)
    # An older result, built without them, is still a valid DTO.
    from sbfsem.service.dto import DeviationDTO

    bare = DeviationDTO(mean_um=1.0, surface_um=1.0, annotation_um=1.0, p95_um=1.0, max_um=1.0,
                        excess_volume_um3=1.0, sample_spacing_um=0.1, n_discs=1, n_curves=0)
    assert (bare.exterior_um, bare.annotation_exterior_um, bare.hidden_fraction) == (None, None, 0.0)


def test_boundary_mesh_is_the_fitted_surface(service):
    mesh = service.boundary_mesh("NeitzInferiorMonkey", "GCL")
    assert mesh.n_faces > 0
    # A heightfield over the marker extent, in microns.
    xmin, xmax, ymin, ymax, zmin, zmax = mesh.bounding_box_um
    assert xmax - xmin > 100 and ymax - ymin > 100
    assert zmax > zmin
    # Cached: the second call is the same object's DTO, not a refit.
    assert service.boundary_mesh("NeitzInferiorMonkey", "GCL").n_faces == mesh.n_faces
    with pytest.raises(ValueError):
        service.boundary_mesh("NeitzInferiorMonkey", "NOPE")


def test_synapse_mesh_of_a_missing_type(service):
    assert service.synapse_mesh(943, "i", "GapJunction").n_faces == 0


def test_closed_curve_methods_on_a_disc_neuron(service):
    assert service.closed_curves(943, "i") == []
    assert service.cone_outline_mesh(943, "i").n_faces == 0


def test_analysis_dtos(service):
    assert service.surface_area(943, "i").values[0] > 0
    assert service.dendritic_field_hull(943, "i").area_um2 > 0
    assert service.dendrite_diameter(943, "i").rows
    assert service.open_ended_branches(943, "i").extra["location_ids"]
    morph = service.morphometrics(943, "i")
    assert morph.total_length_um > 0 and morph.n_tips > 0 and morph.label == "" or morph.label
    distances = service.synapse_soma_distance(943, "i", "RibbonPost")
    assert distances.stats.n == 2
    assert service.synapse_soma_distance(943, "i", "GapJunction").stats is None


def test_tortuosity_through_the_facade(service):
    neuron = service.neuron(943, "i")
    graph = neuron.graph()
    ends = [n for n, d in graph.degree() if d == 1]
    result = service.tortuosity(943, "i", ends[0], ends[1])
    assert result.values[0] >= 1.0
    assert result.extra["dim"] == 2


def test_reloading_clears_the_mesh_cache(service, i943_path):
    from sbfsem.io.legacy_json import read_legacy_json

    service.render_mesh(943, "i")
    assert service._meshes
    service.add_neuron(read_legacy_json(i943_path))
    assert not service._meshes


def test_client_is_reused_per_volume(service):
    assert service.client("i") is service.client("NeitzInferiorMonkey")
    assert service.client("i") is not service.client("t")


def test_mesh_cache_is_bounded(service):
    """An unbounded cache grows forever: every slider position is an entry."""
    svc = SbfsemService(offline=True, mesh_cache_size=3)
    svc.add_neuron(service.neuron(943, "i"))
    for fraction in (1.0, 0.9, 0.8, 0.7, 0.6):
        svc.render_mesh(943, "i", reduce_fraction=fraction)
    assert len(svc._meshes) == 3
    svc.close()


def test_mesh_cache_evicts_least_recently_used(service):
    svc = SbfsemService(offline=True, mesh_cache_size=2)
    svc.add_neuron(service.neuron(943, "i"))
    svc.render_mesh(943, "i", smooth_iterations=0)
    svc.render_mesh(943, "i", smooth_iterations=1)
    svc.render_mesh(943, "i", smooth_iterations=0)   # refresh the first
    svc.render_mesh(943, "i", smooth_iterations=2)   # evicts smooth_iterations=1
    smoothing = sorted(key[2] for key in svc._meshes)
    assert smoothing == [0, 2]
    svc.close()


def test_memory_report(service):
    service.render_mesh(943, "i")
    report = service.memory_report()
    assert report["neurons_loaded"] >= 1
    assert report["annotations_held"] >= 190
    assert report["meshes_cached"] >= 1
    assert report["mesh_bytes"] > 0
    assert report["mesh_cache_limit"] == SbfsemService.MESH_CACHE_SIZE


def test_exports_reuse_the_render_cache(service):
    """A .glb or .stl of a mesh already on screen must not rebuild it."""
    svc = SbfsemService(offline=True)
    svc.add_neuron(service.neuron(943, "i"))
    svc.render_mesh(943, "i")
    assert len(svc._meshes) == 1
    cached = next(iter(svc._meshes.values()))

    exported = svc.mesh_object(943, "i")
    assert exported is cached, "mesh_object rebuilt instead of using the cache"
    assert len(svc._meshes) == 1
    svc.close()


def test_a_held_copy_without_the_synapses_or_in_another_transform_does_not_meet_a_load(
        i943_path, monkeypatch):
    """A load that asked for the synapses, or for another transform, was answered
    by whatever copy was held, however it had been loaded."""
    import sbfsem.service.facade as facade_mod
    from sbfsem.core.transforms import Transforms

    calls = []

    class FakeLoader:
        def __init__(self, volume, client=None):
            pass

        def load(self, structure_id, *, include_synapses=False, transform="Standard", untagged_psd="assume"):
            calls.append((include_synapses, Transforms.from_str(transform).value))
            neuron = read_legacy_json(i943_path)
            neuron.transform = Transforms.from_str(transform)
            return neuron

    monkeypatch.setattr(facade_mod, "NeuronLoader", FakeLoader)
    svc = SbfsemService(offline=True)
    try:
        def load(**kw):
            svc.load_neuron(943, "i", **kw)
            return len(calls)

        assert load(include_synapses=False) == 1
        assert load(include_synapses=False) == 1, "held"
        assert load(include_synapses=True) == 2, "the copy held has no synapses"
        assert load(include_synapses=True) == 2
        assert load(include_synapses=False) == 2, "synapses held are more than was asked, not less"
        assert load(include_synapses=True, transform="None") == 3
        assert load(include_synapses=True, transform="None") == 3
        assert load(include_synapses=True, transform="Standard") == 4
        assert load(include_synapses=True) == 4
        assert calls == [(False, "Standard"), (True, "Standard"), (True, "None"), (True, "Standard")]
        # A cell the service was handed (a file) is taken as it is.
        svc.add_neuron(read_legacy_json(i943_path))
        before = len(calls)
        assert load(include_synapses=True, section_corrections="off") == before
        with pytest.raises(ValueError, match="Unknown transform"):
            svc.load_neuron(943, "i", transform="sideways")
    finally:
        svc.close()


def test_a_method_the_build_does_not_have_is_refused_and_a_spelling_shares_the_cache(service):
    """An unknown method built a plain cylinder under a cache key of its own, and
    "Tube", "tube" and "curvemesh" were three builds of one surface."""
    for bad in ("bogus", "", "line3"):
        with pytest.raises(ValueError, match="method must be one of"):
            service.render_mesh(943, "i", method=bad)
        with pytest.raises(ValueError, match="method must be one of"):
            service.mesh_object(943, "i", method=bad)
        with pytest.raises(ValueError, match="method must be one of"):
            service.build_mesh(service.neuron(943, "i"), method=bad)
    assert service._meshes == {}
    quick = {"smooth_iterations": 0}
    tube = service.mesh_object(943, "i", method="curvemesh", **quick)
    assert service.mesh_object(943, "i", method="Tube", **quick) is tube
    assert service.mesh_object(943, "i", method="tube", **quick) is tube
    assert len(service._meshes) == 1
    assert service.mesh_object(943, "i", method="cylinder", **quick) is not tube


def test_an_export_or_a_difference_refuses_a_render_option_it_does_not_have(service):
    """Taken for its default, a mistyped knob exported a different surface than
    the one asked for.  The measurement path always refused."""
    neuron = service.neuron(943, "i")
    with pytest.raises(ValueError, match=r"unknown render option\(s\) voxel_deatil"):
        service.mesh_object(943, "i", voxel_deatil=2.0)
    with pytest.raises(ValueError, match="unknown render option"):
        service.mesh_for(neuron, {"pathway": "original", "tie": 3})
    with pytest.raises(ValueError, match="unknown render option"):
        service.build_mesh(neuron, voxel_deatil=2.0)
    # Every option the service has is still taken.
    assert service.mesh_object(943, "i", smooth_iterations=0, **SbfsemService.MESH_OPTION_DEFAULTS).n_vertices > 0


def test_export_defaults_match_the_render_defaults(service):
    """Otherwise every export misses the cache on a different key."""
    svc = SbfsemService(offline=True)
    svc.add_neuron(service.neuron(943, "i"))
    svc.mesh_object(943, "i")
    key_from_export = next(iter(svc._meshes))
    svc._meshes.clear()
    svc.render_mesh(943, "i")
    assert next(iter(svc._meshes)) == key_from_export
    svc.close()


def test_mesh_diff_deviations_are_cached(service, monkeypatch):
    """Audit regression: deviations are 15-40s per rendering on large cells;
    a re-Compare with unchanged options must not recompute them."""
    import sbfsem.service.facade as facade_mod

    calls = {"n": 0}
    real = facade_mod.annotation_deviation

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(facade_mod, "annotation_deviation", counting)
    opts = ({"pathway": "original", "smooth_iterations": 0},
            {"pathway": "original"})
    first = service.mesh_diff(943, "i", options_a=opts[0], options_b=opts[1],
                              samples=20_000)
    assert calls["n"] == 2
    second = service.mesh_diff(943, "i", options_a=opts[0], options_b=opts[1],
                               samples=20_000)
    assert calls["n"] == 2  # both deviations served from the cache
    assert second.deviation_a == first.deviation_a
    # Different geometry (another method) must NOT share a cache entry.
    service.mesh_diff(943, "i", options_a={"pathway": "original", "method": "curvemesh"},
                      options_b=opts[1], samples=20_000)
    assert calls["n"] == 3


def test_a_mesh_of_an_earlier_copy_of_a_cell_is_never_served_for_a_later_one(service, i943_path):
    """In memory, a mesh belongs to the copy of the cell it was built from.  A
    build of the old copy that finishes after the cell is loaded again
    (another registration setting, an update from Viking) lands under the old
    copy and must not be served for the new one."""
    import numpy as np

    old = service.neuron(943, "i")
    before = service.render_mesh(943, "i", smooth_iterations=0)
    moved = read_legacy_json(i943_path)
    moved.nodes["Xum"] += 5.0
    service.add_neuron(moved)
    assert moved.load_serial > old.load_serial
    late = service.render_mesh(943, "i", smooth_iterations=0, neuron=old)   # the old copy's build
    after = service.render_mesh(943, "i", smooth_iterations=0)

    def centre(mesh):
        return np.asarray(mesh.vertices, float).reshape(-1, 3).mean(axis=0)

    assert centre(late) == pytest.approx(centre(before))
    assert (centre(after) - centre(before))[0] == pytest.approx(5.0, abs=0.01)


def test_each_setting_keeps_its_own_copy_of_a_cell(service, i943_path):
    """Registration and correction settings are per tab, so two tabs (or two
    people) looking at one cell with different settings each keep a copy of
    their own, and loading one never changes the other's.  On 2026-09-23 a
    Registration switch in one tab moved the cell under every other tab."""
    from sbfsem.service.facade import requested_copy

    plain = service.neuron(943, "i")
    assert plain.copy_tag == "off.0"                    # stored positions, no correction
    moved = read_legacy_json(i943_path)
    moved.nodes["Xum"] += 5.0
    moved.registration_status = {"requested": True, "applied": True}
    service.add_neuron(moved)
    assert moved.copy_tag == "off.1"                    # the current section transforms
    with requested_copy("off.0"):
        assert service.neuron(943, "i") is plain
    with requested_copy("off.1"):
        assert service.neuron(943, "i") is moved
    assert service.neuron(943, "i") is moved            # naming none: the latest load
    assert len(service.loaded_neurons()) == 1           # one cell, listed once
    assert service.memory_report()["copies_held"] == 2

    # A load a copy already satisfies answers from that copy, which becomes the latest.
    detail = service.load_neuron(943, "i", section_corrections="off", live_registration=False)
    assert detail.summary.copy_tag == "off.0"
    assert service.neuron(943, "i") is plain

    # A tab unloading its copy leaves the other tab's.
    with requested_copy("off.0"):
        assert service.unload_neuron(943, "i")
    assert service.neuron(943, "i") is moved
    with requested_copy("off.0"), pytest.raises(NeuronNotLoadedError, match="with the setting off.0"):
        service.neuron(943, "i")
    assert service.unload_neuron(943, "i")              # naming none: every copy
    with pytest.raises(NeuronNotLoadedError):
        service.neuron(943, "i")


def test_every_dto_is_in_the_modules_all():
    """The hand-written list named 24 of the module's 59 classes."""
    import sbfsem.service.dto as dto

    defined = {name for name, value in vars(dto).items()
               if isinstance(value, type) and value.__module__ == dto.__name__}
    assert len(defined) > 50 and set(dto.__all__) == defined
    assert dto.__all__ == sorted(dto.__all__)
    namespace: dict = {}
    exec("from sbfsem.service.dto import *", namespace)  # noqa: S102 - the point is the star import
    assert defined <= set(namespace)


def test_a_location_is_found_in_the_copy_the_request_names(service, i943_path):
    """Locate answered from whichever copy had loaded last, whatever ?copy= said, so
    a tab with other settings was taken to where another tab draws the annotation."""
    from sbfsem.service.facade import requested_copy

    plain = service.neuron(943, "i")
    location = int(plain.nodes["ID"].iloc[0])
    at_rest = [float(plain.nodes[plain.nodes["ID"] == location].iloc[0][c]) for c in ("Xum", "Yum", "Zum")]
    moved = read_legacy_json(i943_path)
    moved.nodes["Xum"] += 5.0
    moved.registration_status = {"requested": True, "applied": True}
    service.add_neuron(moved)

    def x_of(tag):
        with requested_copy(tag):
            found = service.locate(location, "i")
        assert found.loaded and found.structure_id == 943
        return found.xyz_um[0]

    assert x_of("off.0") == pytest.approx(at_rest[0])
    assert x_of("off.1") == pytest.approx(at_rest[0] + 5.0)
    assert x_of(None) == pytest.approx(at_rest[0] + 5.0), "naming none: the latest load"
    # A copy no cell has in that setting does not answer: the location is asked of Viking
    # (offline here, so it is not known).
    with requested_copy("curvefit.1"), pytest.raises(ValueError, match="not known"):
        service.locate(location, "i")


def test_meshes_belong_to_their_copy(service, i943_path):
    """Loading a second setting's copy keeps the first copy's surface in memory,
    and each copy is drawn from its own annotations."""
    import numpy as np

    from sbfsem.service.facade import requested_copy

    plain = service.neuron(943, "i")
    first = service.render_mesh(943, "i", smooth_iterations=0)

    def held(serial):
        return [k for k in service._meshes if k[-1] == serial]

    assert held(plain.load_serial)
    moved = read_legacy_json(i943_path)
    moved.nodes["Xum"] += 5.0
    moved.registration_status = {"requested": True, "applied": True}
    service.add_neuron(moved)
    assert held(plain.load_serial)                      # the first copy's surface stays
    with requested_copy("off.0"):
        again = service.render_mesh(943, "i", smooth_iterations=0)
    with requested_copy("off.1"):
        other = service.render_mesh(943, "i", smooth_iterations=0)

    def centre(mesh):
        return np.asarray(mesh.vertices, float).reshape(-1, 3).mean(axis=0)

    assert centre(again) == pytest.approx(centre(first))
    assert (centre(other) - centre(first))[0] == pytest.approx(5.0, abs=0.01)
