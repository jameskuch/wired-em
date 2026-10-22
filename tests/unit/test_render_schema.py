"""The render schema and everything that must agree with it.

``sbfsem.service.render_schema`` declares each render option once.  The four
server declarations are hand-written, and these tests introspect every one of
them (and the generated browser files) against the schema, so a knob added,
renamed, re-bounded or re-defaulted in one place and not the others fails
here, naming the knob.
"""

from __future__ import annotations

import inspect
import json
import re
import typing
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from sbfsem.geometry import meshlab
from sbfsem.geometry.cylinder import PATHWAYS
from sbfsem.service import render_schema as schema
from sbfsem.service.facade import SbfsemService
from sbfsem_web.api import routes

ROOT = Path(__file__).resolve().parents[2]

BY_KEY = {f.key: f for f in schema.FIELDS}

#: The order of the browser's DEFAULT_RENDER (and so of the JSON the undo
#: history compares), pinned: it is not free to change.
DEFAULT_RENDER_ORDER = [
    "method", "pathway", "smooth_iterations", "post_order", "voxel_detail",
    "voxel_smooth_um", "tie_um", "ring_arc_um", "wrap_spacing_um", "wrap_blend",
    "wrap_reject_sigma", "wrap_passes", "voxel2_um", "voxel2_faces", "voxel2_blend_um",
    "exact_tube_deg", "conv_support", "ultra_soma", "ultra_smooth", "ultra_lambda", "ultra_inflate",
    "meshlab", "meshlab_edge_um", "meshlab_fraction", "meshlab_depth",
    "reduce_fraction", "pixel_size_um", "curve_avg_discs", "curve_avg_paths",
    "surface_smooth_um", "curve_links_um", "curve_tie_um", "curve_bridge_um",
    "curve_link_bridge_um", "color_by_strata", "clip_ipl", "keep_above",
]

#: Today's TECH_KNOBS: what "Save technique defaults" stores.
TECH_KNOBS = [
    "smooth_iterations", "post_order", "voxel_smooth_um", "tie_um", "ring_arc_um",
    "wrap_spacing_um", "wrap_blend", "wrap_reject_sigma", "wrap_passes",
    "voxel2_um", "voxel2_faces", "voxel2_blend_um", "exact_tube_deg",
    "conv_support", "ultra_soma", "ultra_smooth", "ultra_lambda", "ultra_inflate",
    "meshlab", "meshlab_edge_um", "meshlab_fraction", "meshlab_depth",
    "reduce_fraction", "pixel_size_um", "curve_avg_discs", "curve_avg_paths",
    "surface_smooth_um", "curve_links_um", "curve_tie_um", "curve_bridge_um",
    "curve_link_bridge_um",
]


# -- introspecting the four server declarations ------------------------------
def _constraints(metadata: list[Any]) -> dict[str, Any]:
    """``gt/ge/lt/le/pattern`` from pydantic/annotated-types metadata."""
    found: dict[str, Any] = {}
    for item in metadata:
        for name in ("gt", "ge", "lt", "le", "pattern"):
            value = getattr(item, name, None)
            if value is not None:
                found[name] = value
    return found


def _from_function(fn: Any) -> dict[str, dict[str, Any]]:
    """``{name: {default, type, bounds}}`` for a route-dependency function."""
    hints = typing.get_type_hints(fn)
    out: dict[str, dict[str, Any]] = {}
    for name, param in inspect.signature(fn).parameters.items():
        default = param.default
        if hasattr(default, "metadata"):                      # a Query(...)
            out[name] = {"default": default.default, "type": hints.get(name),
                         "bounds": _constraints(default.metadata)}
        else:
            out[name] = {"default": default, "type": hints.get(name), "bounds": {}}
    return out


def _from_body() -> dict[str, dict[str, Any]]:
    return {
        name: {"default": info.default, "type": info.annotation,
               "bounds": _constraints(info.metadata)}
        for name, info in routes.MeshOptionsBody.model_fields.items()
    }


def _from_attached() -> dict[str, dict[str, Any]]:
    return {
        name: {"default": info.default, "type": info.annotation,
               "bounds": _constraints(info.metadata)}
        for name, info in routes.AttachedRenderBody.model_fields.items()
    }


def _from_render_mesh() -> dict[str, dict[str, Any]]:
    hints = typing.get_type_hints(SbfsemService.render_mesh)
    return {
        name: {"default": p.default, "type": hints.get(name), "bounds": {}}
        for name, p in inspect.signature(SbfsemService.render_mesh).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY and name != "neuron"
    }


def _surfaces() -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "mesh": _from_function(routes.mesh_params),
        "render_mesh": _from_render_mesh(),
        "export": _from_function(routes._geometry_options),
        "body": _from_body(),
        "attach": _from_attached(),
        "facade": {k: {"default": v, "type": None, "bounds": {}}
                   for k, v in SbfsemService.MESH_OPTION_DEFAULTS.items()},
    }


def _expected_keys(surface: str) -> set[str]:
    wire = "mesh" if surface in ("render_mesh", "attach") else surface
    return {f.key for f in schema.FIELDS if wire in f.wire}


def _alternatives(pattern: str) -> set[str]:
    match = re.fullmatch(r"\^\(([^()]*)\)\$", pattern)
    assert match, f"not an anchored alternation: {pattern!r}"
    return set(match.group(1).split("|"))


def _schema_bounds(f: schema.Field) -> dict[str, Any]:
    return dict(f.server_bounds())


# -- 1. the same keys on every surface ---------------------------------------
@pytest.mark.parametrize("surface", ["mesh", "render_mesh", "export", "body", "attach", "facade"])
def test_every_surface_declares_exactly_the_schemas_keys(surface):
    actual = set(_surfaces()[surface])
    expected = _expected_keys(surface)
    assert actual == expected, (
        f"{surface}: missing {sorted(expected - actual)}, "
        f"not in the schema {sorted(actual - expected)}"
    )


def test_the_route_dependencies_return_what_they_declare():
    """A dict that forgets a declared parameter (the export route's missing
    ``pixel_size_um``) is as wrong as a missing parameter."""
    for fn in (routes.mesh_params, routes._geometry_options):
        declared = _from_function(fn)
        kwargs = {name: info["default"] for name, info in declared.items()}
        assert set(fn(**kwargs)) == set(declared), fn.__name__


def test_field_keys_are_unique_and_in_default_render_order():
    keys = [f.key for f in schema.FIELDS]
    assert len(keys) == len(set(keys)) == 40
    assert [f.key for f in schema.FIELDS if f.in_render] == DEFAULT_RENDER_ORDER
    # The page's one IPL depth method and its one flattened view are not a
    # cell's render options either.
    assert [f.key for f in schema.FIELDS if not f.in_render] == ["clip_z", "ipl_method", "ipl_flatten"]
    assert keys[:-3] == DEFAULT_RENDER_ORDER, "clip_z, ipl_method and ipl_flatten come last"


def test_wire_and_scope_agree():
    for f in schema.FIELDS:
        assert f.scope in ("geometry", "view"), f.key
        assert set(f.wire) <= {"mesh", "export", "body", "facade"}, f.key
        if f.scope == "view":
            assert f.wire == ("mesh",), f.key
        else:
            assert {"mesh", "export", "body"} <= set(f.wire), f.key
    # The facade's MESH_OPTION_DEFAULTS is the geometry options _cached_mesh
    # does not take as explicit arguments.
    explicit = {"method", "smooth_iterations", "reduce_fraction", "pixel_size_um"}
    assert {f.key for f in schema.FIELDS if "facade" in f.wire} == {
        f.key for f in schema.FIELDS if f.scope == "geometry"} - explicit
    assert sum(1 for f in schema.FIELDS if f.scope == "geometry") == 34


# -- 2. the same defaults everywhere ------------------------------------------
def test_defaults_agree_on_every_surface_and_the_schema():
    for surface, declared in _surfaces().items():
        for key, info in declared.items():
            want = BY_KEY[key].default
            assert info["default"] == want, (
                f"{surface}: {key} defaults to {info['default']!r}, the schema says {want!r}")
            assert type(info["default"]) is type(want) or (
                isinstance(info["default"], (int, float)) and isinstance(want, (int, float))
                and not isinstance(info["default"], bool)
            ), f"{surface}: {key} is a {type(info['default']).__name__}, the schema's a {type(want).__name__}"


def test_the_apps_different_defaults_are_the_pathway_join_bodies_and_ipl_depth():
    """Ground rule 1: parity first in the library, product defaults in the app.
    The app starts on the Voxel remesh pathway, joins a Closed curve surface's
    bodies up to 5 um apart (the user's choice, 2026-10-16) and measures IPL
    depth along the layers' normals (James Anderson's request, 2026-10-22); the
    library and the API keep MATLAB's. A fourth silent product default fails here."""
    assert [f.key for f in schema.FIELDS if f.app_default is not schema.SAME] == [
        "pathway", "curve_tie_um", "ipl_method"]
    assert schema.api_defaults()["pathway"] == "original"
    assert schema.app_defaults()["pathway"] == "voxel2"
    assert schema.api_defaults()["curve_tie_um"] == 0.0
    assert schema.app_defaults()["curve_tie_um"] == 5.0
    assert schema.api_defaults()["ipl_method"] == "column"
    assert schema.app_defaults()["ipl_method"] == "normal"
    differing = {k for k in schema.api_defaults() if schema.api_defaults()[k] != schema.app_defaults()[k]}
    assert differing == {"pathway", "curve_tie_um", "ipl_method"}


# -- 3. the same bounds -------------------------------------------------------
def test_server_bounds_agree_across_routes_and_the_schema():
    for surface in ("mesh", "export", "body"):
        for key, info in _surfaces()[surface].items():
            f = BY_KEY[key]
            expected = _schema_bounds(f)
            pattern = f.server_pattern()
            got = dict(info["bounds"])
            got_pattern = got.pop("pattern", None)
            assert got == expected, f"{surface}: {key} is bounded {got}, the schema says {expected}"
            if pattern is None:
                assert got_pattern is None, f"{surface}: {key} has a pattern the schema lacks"
            else:
                assert got_pattern is not None, f"{surface}: {key} has no pattern"
                assert _alternatives(got_pattern) == _alternatives(pattern), (
                    f"{surface}: {key} accepts {sorted(_alternatives(got_pattern))}, "
                    f"the schema lists {sorted(_alternatives(pattern))}")


def test_the_browsers_ranges_lie_inside_the_servers():
    """A value the controls or the clamp allow must never be a 422."""
    for f in schema.FIELDS:
        if f.min is None or f.max is None:
            continue
        bounds = _schema_bounds(f)
        if "gt" in bounds:
            assert f.min > bounds["gt"], f.key
        if "ge" in bounds:
            assert f.min >= bounds["ge"], f.key
        if "lt" in bounds:
            assert f.max < bounds["lt"], f.key
        if "le" in bounds:
            assert f.max <= bounds["le"], f.key
        assert f.min < f.max, f.key


def test_types_agree():
    expected = {
        "enum": {str}, "int": {int}, "float": {float}, "percent": {float}, "numchoice": {float},
        "bool": {bool}, "optfloat": {float | None},
    }
    for surface in ("mesh", "render_mesh", "export", "body"):
        for key, info in _surfaces()[surface].items():
            f = BY_KEY[key]
            assert info["type"] in expected[f.kind], (surface, key, info["type"], f.kind)
            if f.kind == "int":
                assert isinstance(f.default, int) and not isinstance(f.default, bool), key


# -- 4. the enums ---------------------------------------------------------------
def test_enum_choices_are_what_the_server_accepts():
    pathway = BY_KEY["pathway"]
    assert {v for v, _ in pathway.choices} == set(PATHWAYS)
    assert len(pathway.choices) == len(PATHWAYS)
    assert tuple(v for v, _ in BY_KEY["meshlab"].choices) == meshlab.MODES
    assert {v for v, _ in BY_KEY["post_order"].choices} == {"smooth_first", "decimate_first"}
    assert {v for v, _ in BY_KEY["ultra_soma"].choices} == {"discs", "sphere"}
    # Method is a free string on the server (it also takes aliases); the
    # schema lists the four the app offers.
    assert BY_KEY["method"].server_pattern() is None
    assert [v for v, _ in BY_KEY["method"].choices] == [
        "cylinder", "curvemesh", "closedcurve", "hybrid"]
    for f in schema.FIELDS:
        if f.kind == "enum":
            assert f.default in {v for v, _ in f.choices}, f.key
            if f.app_default is not schema.SAME:
                assert f.app_default in {v for v, _ in f.choices}, f.key


def test_the_wrap_constants_and_the_geometry_functions_defaults_are_the_apps_defaults():
    """The wrap defaults are literals in the facade's options, in the render signature and in
    the schema, and the named constants of ``wrap.py`` in ``cylinder_render``'s: changing
    ``WRAP_BLEND`` to 3.0 kept 150 tests green, because nothing held them to each other.  And
    every option ``cylinder_render`` shares with the app has the app's default."""
    from sbfsem.geometry import wrap
    from sbfsem.geometry.cylinder import cylinder_render

    constants = {"wrap_spacing_um": wrap.WRAP_SPACING_UM, "wrap_blend": wrap.WRAP_BLEND,
                 "wrap_reject_sigma": wrap.WRAP_REJECT_SIGMA, "wrap_passes": wrap.WRAP_PASSES}
    render = inspect.signature(SbfsemService.render_mesh).parameters
    for key, value in constants.items():
        assert SbfsemService.MESH_OPTION_DEFAULTS[key] == value, f"MESH_OPTION_DEFAULTS[{key}]"
        assert BY_KEY[key].default == value, f"the schema's {key}"
        assert render[key].default == value, f"render_mesh({key}=...)"
    cylinder = inspect.signature(cylinder_render).parameters
    shared = [key for key in SbfsemService.MESH_OPTION_DEFAULTS if key in cylinder]
    assert len(shared) >= 17, "the comparison reached the options"
    for key in shared:
        assert cylinder[key].default == SbfsemService.MESH_OPTION_DEFAULTS[key], f"cylinder_render({key}=...)"


def test_the_methods_the_service_knows_are_the_schemas_and_their_aliases():
    """``METHOD_NAMES`` is a fifth hand-written declaration of the method enum, beside the
    four this file compares: the schema's four methods must be exactly the names the
    service builds under, and the aliases (docs/backend.md promises them) must map to
    them.  Dropping an alias, or adding a method to one list and not the other, failed no
    test."""
    from sbfsem.service.facade import METHOD_NAMES, canonical_method

    offered = [v for v, _ in BY_KEY["method"].choices]
    assert sorted(set(METHOD_NAMES.values())) == sorted(offered)
    for method in offered:
        assert canonical_method(method) == method, "a canonical name is its own"
    aliases = {"tube": "curvemesh", "curve": "closedcurve", "outline": "closedcurve",
               "volume": "closedcurve", "mixed": "hybrid"}
    assert {k: v for k, v in METHOD_NAMES.items() if k not in offered} == aliases
    for alias, canonical in aliases.items():
        assert canonical_method(alias) == canonical
        assert canonical_method(f"  {alias.upper()} ") == canonical, "case and spaces do not matter"
    with pytest.raises(ValueError, match="method must be one of .*closedcurve.*cylinder.*hybrid"):
        canonical_method("cylindr")


# -- 5. the fields are complete ----------------------------------------------
def test_every_control_has_what_it_needs():
    for f in schema.FIELDS:
        if f.ui:
            assert f.label.strip(), f.key
            assert f.help.strip(), f"{f.key}: no help text"
            assert f.slot in ("strip", "more"), f.key
            assert {"wrapper", "control"} <= set(f.ids), f.key
            if f.kind in ("float", "percent", "optfloat"):
                assert f.step is not None and f.step > 0, f.key
            if f.kind in ("enum", "numchoice"):
                assert f.choices, f.key
        else:
            assert f.ui_reason.strip(), f"{f.key}: ui=False without a reason"
            assert f.slot is None, f.key
            assert not f.ids, f.key


def test_panel_slots_list_every_control_once_in_slot_order():
    listed = [k for keys in schema.PANEL_SLOTS.values() for k in keys]
    assert len(listed) == len(set(listed)) == 34
    assert set(listed) == {f.key for f in schema.FIELDS if f.ui}
    for slot, keys in schema.PANEL_SLOTS.items():
        for key in keys:
            assert BY_KEY[key].slot == slot, key
    ids = [i for f in schema.FIELDS for i in f.ids.values()]
    assert len(ids) == len(set(ids)), "an element id is claimed twice"
    # A wrapper and a control each, Decimate's readout, Clip IPL's side, the Voxel size's list.
    assert len(ids) == 2 * 34 + 3
    assert [f.key for f in schema.FIELDS if f.companion] == ["clip_ipl"]
    assert BY_KEY["clip_ipl"].companion == "keep_above"


def test_the_technique_tuning_is_todays_twenty_nine_knobs():
    assert [f.key for f in schema.FIELDS if f.tune] == TECH_KNOBS
    assert all(f.scope == "geometry" for f in schema.FIELDS if f.tune)


def test_visibility_rules_name_only_known_options_and_choices():
    for f in schema.FIELDS:
        for key, allowed in f.show.items():
            assert key in BY_KEY, (f.key, key)
            choices = {str(v) for v, _ in BY_KEY[key].choices}
            assert set(allowed) <= choices, (f.key, key, allowed)
        for rule in f.inert:
            assert rule["when"], f"{f.key}: an inert rule with no condition always applies"
            for key, values in rule["when"].items():
                assert key in BY_KEY, (f.key, key)
                choices = {v for v, _ in BY_KEY[key].choices}
                assert set(values) <= choices, (f.key, key, values)
            assert rule["why"].strip(), f"{f.key}: a reason"
        if f.requires is not None:
            assert f.requires in schema.REQUIRES, f.key


def _other_value(f: schema.Field) -> Any:
    """A value of the field's other than its default, for the build to ignore."""
    if f.kind in ("enum", "numchoice"):
        return next(v for v, _ in f.choices if v != f.default)
    top, bottom = f.max, f.min
    assert top is not None and bottom is not None, f.key
    return top if top != f.default else bottom


def test_what_the_schema_greys_out_is_what_the_build_ignores(c943):
    """Each ``inert`` rule is a claim about the build, so it is tested as one: under
    the rule's conditions the mesh is vertex-for-vertex and face-for-face the same
    with the option at another value (the browser treats such an option as not a
    difference, and the panel greys it out)."""
    import numpy as np

    svc = SbfsemService(offline=True)
    try:
        def build(options):
            mesh = svc.build_mesh(c943, **{"pixel_size_um": 0.25, **options})
            return np.asarray(mesh.vertices), np.asarray(mesh.faces)

        bases: dict[str, tuple[Any, Any]] = {}
        checked = 0
        for f in schema.FIELDS:
            for rule in f.inert:
                # A state the rule applies to: its first values over a plain Cylinder / Original.
                state = {"method": "cylinder", "pathway": "original",
                         **{k: values[0] for k, values in rule["when"].items()}}
                change = {"pathway": "voxel2"} if f.key == "pathway" else {f.key: _other_value(f)}
                where = json.dumps(state, sort_keys=True)
                if where not in bases:
                    bases[where] = build(state)
                vertices, faces = bases[where]
                got_vertices, got_faces = build({**state, **change})
                assert got_vertices.shape == vertices.shape and np.array_equal(got_vertices, vertices), \
                    f"{change} changes the mesh under {state}: {f.key} is not inert there"
                assert np.array_equal(got_faces, faces), (f.key, state)
                checked += 1
        assert checked >= 20
    finally:
        svc.close()


def test_a_tube_is_smoothed_under_the_taubin_pathways(c943):
    """The other half of the claim: Passes is NOT inert for a Tube under a pathway
    that smooths with Taubin (the panel used to grey it out for every Tube)."""
    import numpy as np

    svc = SbfsemService(offline=True)
    try:
        for pathway in ("adaptive_taubin", "voxel"):
            still = svc.build_mesh(c943, method="curvemesh", pathway=pathway, smooth_iterations=0)
            smoothed = svc.build_mesh(c943, method="curvemesh", pathway=pathway, smooth_iterations=2)
            assert np.asarray(still.vertices).shape == np.asarray(smoothed.vertices).shape
            assert not np.array_equal(np.asarray(still.vertices), np.asarray(smoothed.vertices)), pathway
            assert not schema.field_state({"method": "curvemesh", "pathway": pathway})[
                "smooth_iterations"].inert
    finally:
        svc.close()


def test_the_inert_and_hidden_rules_are_the_measured_ones():
    """What the build ignores, as measured on c1178 (bit-identical meshes with the
    option changed): the closed-curve method reads the voxel size (and, when they
    are on, the two averaging strengths, the curved links, the joined bodies and the
    bridges) and nothing else; the tube skips the smoother only under Original;
    Ultraliser smooths itself.  The pixel size and those six closed-curve options
    are the only fields the method hides."""
    pathway_knobs = [
        "pathway", "post_order", "voxel_detail", "voxel_smooth_um", "tie_um", "ring_arc_um",
        "wrap_spacing_um", "wrap_blend", "wrap_reject_sigma", "wrap_passes", "voxel2_um",
        "voxel2_faces", "voxel2_blend_um", "exact_tube_deg", "conv_support", "ultra_soma",
        "ultra_smooth", "ultra_lambda", "ultra_inflate",
    ]
    assert [f.key for f in schema.FIELDS if f.inert] == [
        "pathway", "smooth_iterations", "post_order", "voxel_detail",
        *pathway_knobs[3:], "reduce_fraction",
    ]
    assert [f.key for f in schema.FIELDS if "method" in f.show] == [
        "pixel_size_um", "curve_avg_discs", "curve_avg_paths", "curve_links_um", "curve_tie_um",
        "curve_bridge_um", "curve_link_bridge_um"]

    def passes(**options):
        return schema.field_state(options)["smooth_iterations"]

    assert passes(method="curvemesh", pathway="original").inert
    assert "Original pathway" in passes(method="curvemesh", pathway="original").why
    for pathway in ("adaptive_taubin", "voxel", "wrap", "voxel2", "convolution"):
        assert not passes(method="curvemesh", pathway=pathway).inert, pathway
    assert not passes(method="cylinder", pathway="original").inert   # MATLAB smoothed a cylinder
    for method in ("cylinder", "curvemesh", "hybrid"):
        assert passes(method=method, pathway="ultraliser").inert, method
    assert passes(method="closedcurve").inert and "voxel grid" in passes(method="closedcurve").why
    assert not schema.field_state({"method": "curvemesh"})["reduce_fraction"].inert
    state = schema.field_state({"method": "closedcurve", "pathway": "voxel2"})
    assert state["reduce_fraction"].inert
    for key in pathway_knobs:
        assert state[key].inert or state[key].hidden, key
        assert not schema.field_state({"method": "cylinder", "pathway": "voxel2"})[key].inert, key
    assert state["voxel2_um"].inert and state["tie_um"].inert
    assert not state["pixel_size_um"].hidden and not state["pixel_size_um"].inert
    assert not state["meshlab"].inert
    assert schema.field_state({"method": "cylinder"})["pixel_size_um"].hidden
    assert not schema.field_state({"method": "hybrid"})["pixel_size_um"].hidden
    # Hidden fields are never also greyed, and MeshLab is disabled only where the server lacks it.
    off = schema.field_state({}, {"meshlabOff": True})
    assert off["meshlab"].disabled and "pymeshlab" in off["meshlab"].title
    assert not schema.field_state({}, {})["meshlab"].disabled


def test_ultraliser_smooths_itself_and_wrap_uses_the_envelope():
    """Measured on c1178 (the builds are in the commit that made this rule): under Ultraliser
    Passes and Order change nothing, and under Wrap Envelope sigma does."""
    ultraliser = schema.field_state({"pathway": "ultraliser"})
    for key in ("smooth_iterations", "post_order"):
        assert ultraliser[key].inert and "Ultraliser" in ultraliser[key].why, key
    assert not ultraliser["reduce_fraction"].inert        # decimation is still applied
    # The method's own reason wins where both hold, and a hybrid's disc half is Ultraliser's.
    assert "closed-curve" in schema.field_state({"method": "closedcurve", "pathway": "ultraliser"})[
        "smooth_iterations"].why
    assert schema.field_state({"method": "hybrid", "pathway": "ultraliser"})["smooth_iterations"].inert
    for pathway in set(PATHWAY_NAMES) - {"ultraliser"}:
        state = schema.field_state({"pathway": pathway})
        assert not state["post_order"].inert and not state["smooth_iterations"].inert, pathway
    assert not schema.field_state({"pathway": "wrap"})["voxel_smooth_um"].hidden
    assert schema.field_state({"pathway": "convolution"})["voxel_smooth_um"].hidden
    assert schema.field_state({"pathway": "original"})["voxel_smooth_um"].hidden


def test_the_averaging_strengths_show_only_where_the_method_reads_them():
    """Closed curve draws a cell's discs as circles and its traced outlines; Cylinder, Tube
    and Hybrid's disc half build tubes through the discs, which Disc avg averages first
    (2.7.0), and only Closed curve and Hybrid draw traced outlines.  Off is the default
    everywhere, in the library and in the app."""
    for method, discs, paths in (("closedcurve", True, True), ("hybrid", True, True),
                                 ("cylinder", True, False), ("curvemesh", True, False)):
        state = schema.field_state({"method": method})
        assert (not state["curve_avg_discs"].hidden) is discs, method
        assert (not state["curve_avg_paths"].hidden) is paths, method
        assert not state["curve_avg_discs"].inert and not state["curve_avg_paths"].inert, method
    for key in ("curve_avg_discs", "curve_avg_paths"):
        f = BY_KEY[key]
        assert f.default == 0.0 and f.app_value() == 0.0 and f.tune and f.slot == "strip", key
        assert f.help.strip() and f.min == 0, key
    assert BY_KEY["curve_avg_discs"].show == {
        "method": ("closedcurve", "cylinder", "curvemesh", "hybrid")}
    assert BY_KEY["curve_avg_paths"].show == {"method": ("closedcurve", "hybrid")}


def test_the_closed_curve_reaches_show_only_where_the_method_reads_them():
    """Curved links and joined bodies act on what Closed curve draws of a cell's discs and on
    its whole raster; Hybrid's discs are the Cylinder build's, so neither shows there.  The two
    bridges act on traced outlines and on links, which Hybrid's outline half has too.  Off,
    or the constants they were, is the library's and the API's default; the app joins bodies up
    to 5 um apart (the user's choice, 2026-10-16) and leaves the rest as the library has them."""
    for method, own, bridges in (("closedcurve", True, True), ("hybrid", False, True),
                                 ("cylinder", False, False), ("curvemesh", False, False)):
        state = schema.field_state({"method": method})
        for key in ("curve_links_um", "curve_tie_um"):
            assert (not state[key].hidden) is own, (method, key)
        for key in ("curve_bridge_um", "curve_link_bridge_um"):
            assert (not state[key].hidden) is bridges, (method, key)
        assert not any(state[k].inert for k in ("curve_links_um", "curve_tie_um", "curve_bridge_um",
                                                "curve_link_bridge_um")), method
    defaults = {"curve_links_um": 0.0, "curve_tie_um": 0.0, "curve_bridge_um": 1.0,
                "curve_link_bridge_um": 5.0}
    in_app = {**defaults, "curve_tie_um": 5.0}
    for key, value in defaults.items():
        f = BY_KEY[key]
        assert f.default == value and f.app_value() == in_app[key] and f.tune, key
        assert f.help.strip() and f.min == 0 and f.max > value, key
        assert SbfsemService.MESH_OPTION_DEFAULTS[key] == value, key
    assert BY_KEY["curve_links_um"].slot == BY_KEY["curve_tie_um"].slot == "strip"
    assert BY_KEY["curve_bridge_um"].slot == BY_KEY["curve_link_bridge_um"].slot == "more"


#: What a help text's lead calls each pathway.
PATHWAY_NAMES = {
    "original": "Original", "adaptive_taubin": "Adaptive ring + Taubin", "voxel2": "Voxel remesh",
    "voxel": "Voxel remesh orig", "convolution": "Convolution surface", "ultraliser": "Ultraliser",
    "wrap": "Wrap",
}


def test_a_pathway_knobs_help_names_exactly_the_pathways_that_show_it():
    """Envelope sigma, Tie gaps and Ring detail named fewer pathways than showed them (found
    2026-10-15): the first sentence of a pathway-specific knob's help lists the pathways whose
    choice shows the knob, so the two cannot drift again."""
    names = sorted(PATHWAY_NAMES.values(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(n) for n in names))
    checked = 0
    for f in schema.FIELDS:
        shown_for = f.show.get("pathway")
        if not f.ui or shown_for is None:
            continue
        lead = f.help.split(". ")[0]
        named = set(pattern.findall(lead))
        assert named == {PATHWAY_NAMES[p] for p in shown_for}, (f.key, lead)
        checked += 1
    assert checked == 16
    assert BY_KEY["voxel_smooth_um"].help.startswith(
        "Voxel remesh, Voxel remesh orig, Ultraliser and Wrap. ")
    assert BY_KEY["tie_um"].help.startswith(
        "Voxel remesh, Voxel remesh orig, Convolution surface, Ultraliser and Wrap. ")
    assert BY_KEY["ring_arc_um"].help.startswith("Adaptive ring + Taubin and Wrap (cube fit). ")


# -- 6. the presets ------------------------------------------------------------
PRESET_IDS = [
    "voxel2", "voxel", "convolution", "ultraliser", "original", "adaptive", "wrap", "tube",
    "closedcurve", "hybrid", "voxel2_iso", "voxel2_qem", "voxel2_poisson",
]


def _valid_value(key: str, value: Any) -> bool:
    f = BY_KEY[key]
    if f.kind in ("enum",):
        return value in {v for v, _ in f.choices}
    if f.min is not None and f.max is not None:
        return f.min <= value <= f.max
    return False


def test_there_is_one_list_of_thirteen_presets():
    assert [p.id for p in schema.PRESETS] == PRESET_IDS
    assert len({p.label for p in schema.PRESETS}) == 13
    seen: set[tuple[str, str, str]] = set()
    for p in schema.PRESETS:
        identity = (p.method, p.pathway, p.meshlab())
        assert identity not in seen, f"{p.id} repeats {identity}"
        seen.add(identity)
        assert schema.preset_by_id(p.id) is p


def test_presets_are_valid_options_and_plain_ones_force_nothing():
    for p in schema.PRESETS:
        assert _valid_value("method", p.method), p.id
        assert _valid_value("pathway", p.pathway), p.id
        for source in (p.set, p.measure_set):
            for key, value in source.items():
                assert key in BY_KEY and BY_KEY[key].scope == "geometry", (p.id, key)
                assert _valid_value(key, value), (p.id, key, value)
        if p.requires is None:
            # A plain preset keeps whatever MeshLab default the user saved for the technique.
            assert not p.set, p.id
        else:
            assert p.requires in schema.REQUIRES and set(p.set) == {"meshlab"}, p.id
            assert p.set["meshlab"] != "none", p.id
    assert [p.id for p in schema.PRESETS if p.requires] == [
        "voxel2_iso", "voxel2_qem", "voxel2_poisson"]
    assert [p.id for p in schema.PRESETS if p.short] == ["ultraliser"]


def test_the_tube_is_one_cached_mesh_in_the_app_and_unsmoothed_in_the_measurements():
    tube = schema.preset_by_id("tube")
    assert "smooth_iterations" not in tube.set          # geometry-identical at any pass count
    assert schema.technique_table()["tube"]["smooth_iterations"] == 0
    for name in ("closedcurve", "hybrid"):
        assert schema.technique_table()[name]["pixel_size_um"] == 0.25
        assert "pixel_size_um" not in schema.preset_by_id(name).set   # the app seeds it per cell


# -- 7. the generated browser file ---------------------------------------------
def test_render_schema_js_is_generated_from_the_schema():
    """Regenerate with ``python -m sbfsem.service.render_schema >
    src/sbfsem_web/static/js/render-schema.js``."""
    shipped = resources.files("sbfsem_web").joinpath("static", "js", "render-schema.js")
    shipped_lines = shipped.read_text(encoding="utf-8").splitlines()
    generated_lines = schema.js_source().splitlines()
    for number, (have, want) in enumerate(zip(shipped_lines, generated_lines, strict=False), 1):
        if have != want:
            name = re.search(r'"key": "([a-z_0-9]+)"', want)
            pytest.fail(f"render-schema.js line {number} ({name.group(1) if name else 'not a field'}) "
                        "differs from the generator's output: regenerate it")
    assert len(shipped_lines) == len(generated_lines), "render-schema.js has a different length"


# -- 8. the visibility fixture -----------------------------------------------
def test_visibility_fixture_is_the_evaluators_output():
    """The node tests check the browser's evaluator against this same file."""
    shipped = (ROOT / "tests" / "fixtures" / "render_visibility.json").read_text(encoding="utf-8")
    assert shipped == schema.fixture_source()
    cases = json.loads(shipped)
    assert len(cases) == 2 * 4 * 7 * 4
    assert {c["ctx"]["meshlabOff"] for c in cases} == {True, False}
