"""Every render option, declared once.

A render knob has a name, a default, a range, a label, a help text, a place in
the panel and rules for when it applies.  Until this module those facts were
copied by hand into eleven lists (four on the server, seven in the browser)
that agreed on every value and disagreed on which options each one named.  Now
they are declared here and everything else is checked against, or generated
from, this one list:

* ``src/sbfsem_web/static/js/render-schema.js`` is generated from it
  (``python -m sbfsem.service.render_schema > src/sbfsem_web/static/js/render-schema.js``)
  and the browser's option defaults, bounds, form and presets are built from
  that file.  A test fails when the shipped file is not the generator's output.
* The four server declarations stay hand-written -- ``mesh_params`` and
  ``_geometry_options`` in ``sbfsem_web/api/routes.py``, ``MeshOptionsBody``
  there, and ``SbfsemService.MESH_OPTION_DEFAULTS`` -- and
  ``tests/unit/test_render_schema.py`` introspects all four against this list:
  keys, defaults, bounds and patterns.  A knob added to one and not the others
  fails there, naming it.  (A cell's attached render, ``AttachedRenderBody``,
  is ``MeshOptionsBody`` plus the four ``view`` options the mesh route also
  takes, and is checked against the ``mesh`` wire the same way.)
* ``tests/fixtures/render_visibility.json`` is :func:`visibility_cases`: which
  fields hide and grey out for every method, pathway and MeshLab mode
  (``python -m sbfsem.service.render_schema --fixture >
  tests/fixtures/render_visibility.json``).  The node tests check the
  browser's evaluator against it, so the two cannot drift.

To add a knob: add its :class:`Field` here (in the order of the browser's
``DEFAULT_RENDER``), add it to the server declarations that carry it, add it to
``PANEL_SLOTS`` if it has a control, regenerate the JS and the fixture, and run
the tests.

The named techniques (:data:`PRESETS`) are declared here too: the render bar's
picker, every Render compare pane's picker and ``sbfsem-measure``'s
``--technique`` names are one list.

Two defaults differ between the library and the app on purpose (CLAUDE.md
ground rule 1): the bare API keeps the MATLAB-faithful ``original`` pathway and
the app starts on ``voxel2``; and the app joins a Closed curve surface's
separate bodies up to 5 um apart (``curve_tie_um``, the user's choice on
2026-10-16), where the library and the API join none.  A test fails on a third.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FIELDS",
    "PANEL_SLOTS",
    "PRESETS",
    "REQUIRES",
    "Field",
    "FieldState",
    "OptionRow",
    "Preset",
    "api_defaults",
    "app_defaults",
    "describe_options",
    "field_by_key",
    "field_state",
    "match_preset",
    "option_text",
    "fixture_source",
    "js_source",
    "preset_by_id",
    "technique_table",
    "visibility_cases",
]


class _Same:
    """Marks "the app uses the API default" (``None`` is a real default)."""

    def __repr__(self) -> str:
        return "SAME"


SAME = _Same()

#: What a control that needs an optional server feature says when the server
#: lacks it.  Keyed by ``Field.requires``; the context flag is ``<name>Off``.
REQUIRES: dict[str, str] = {
    "meshlab": 'This server does not have pymeshlab installed (pip install "sbfsem[meshlab]")',
}


@dataclass(frozen=True, eq=False)
class Field:
    """One render option.

    ``kind`` is how it is edited and read: ``enum`` (a select over ``choices``),
    ``int`` / ``float`` (a number box), ``percent`` (a slider with a percent
    readout), ``numchoice`` (a box a number is typed into, with a list of
    named sizes beside it: any value in range, listed or not), ``optfloat`` (a
    number box where blank means none) or ``bool``.

    ``min``/``max`` are the browser's clamp and its controls' bounds;
    ``server`` says what the routes accept when that differs (a ``gt`` bound,
    or a wider range); ``step`` is the number box's step (``None`` leaves the
    browser's default).

    ``show`` hides the field unless every listed option holds one of the
    allowed values.  ``inert`` is a list of rules, each ``{"when": {option:
    (values...)}, "why": reason}``: the field is greyed (and its help prefixed
    with the reason) for the first rule whose options all hold one of their
    values, because the build ignores it then.
    """

    key: str
    kind: str
    label: str
    default: Any
    help: str = ""
    app_default: Any = SAME
    min: float | None = None
    max: float | None = None
    step: float | None = None
    server: tuple[tuple[str, float], ...] | None = None
    #: The server validates this enum with a ``^(a|b)$`` pattern over ``choices``.
    server_enum: bool = False
    choices: tuple[tuple[Any, str], ...] = ()
    #: ``strip`` (the render panel's row), ``more`` (its More menu) or ``None``.
    slot: str | None = None
    show: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    inert: tuple[Mapping[str, Any], ...] = ()
    #: Part of a technique's tuning: saved with "Save technique defaults".
    tune: bool = False
    #: ``geometry`` changes the surface; ``view`` is clipping/coloring, applied
    #: to a mesh after it is built and never part of what two panes compare.
    scope: str = "geometry"
    #: Which server declarations carry it: ``mesh`` (``mesh_params`` and
    #: ``render_mesh``), ``export`` (``_geometry_options``), ``body``
    #: (``MeshOptionsBody``) and ``facade`` (``MESH_OPTION_DEFAULTS``).
    wire: tuple[str, ...] = ()
    #: The legacy element ids the main panel keeps: ``wrapper``, ``control``,
    #: and ``out`` / ``side`` / ``menu`` (a ``numchoice``'s list of sizes) where
    #: the field has a second element.
    ids: Mapping[str, str] = field(default_factory=dict)
    ui: bool = True
    ui_reason: str = ""
    #: Another field this one's control also edits (Clip IPL carries keep_above).
    companion: str | None = None
    placeholder: str | None = None
    #: An optional server feature (``REQUIRES``) the control is disabled without.
    requires: str | None = None
    #: In the browser's ``DEFAULT_RENDER`` (everything but the API-only ``clip_z``).
    in_render: bool = True

    def app_value(self) -> Any:
        return self.default if self.app_default is SAME else self.app_default

    def server_bounds(self) -> tuple[tuple[str, float], ...]:
        """The routes' bounds: ``server`` if given, else ``[min, max]`` inclusive."""
        if self.server is not None:
            return self.server
        if self.min is not None and self.max is not None:
            return (("ge", self.min), ("le", self.max))
        return ()

    def server_pattern(self) -> str | None:
        if not self.server_enum:
            return None
        return "^(" + "|".join(str(value) for value, _ in self.choices) + ")$"

    def to_js(self) -> dict[str, Any]:
        """The browser's copy: camelCase keys, whole floats as integers."""
        return _plain({
            "key": self.key, "kind": self.kind, "label": self.label, "help": self.help,
            "default": self.default,
            "appDefault": None if self.app_default is SAME else self.app_default,
            "hasAppDefault": self.app_default is not SAME,
            "min": self.min, "max": self.max, "step": self.step,
            "server": [list(b) for b in self.server_bounds()],
            "choices": [list(c) for c in self.choices],
            "slot": self.slot,
            "show": {k: list(v) for k, v in self.show.items()},
            "inert": [
                {"when": {k: list(v) for k, v in rule["when"].items()}, "why": rule["why"]}
                for rule in self.inert
            ],
            "tune": self.tune, "scope": self.scope, "wire": list(self.wire),
            "ids": dict(self.ids), "ui": self.ui, "uiReason": self.ui_reason,
            "companion": self.companion, "placeholder": self.placeholder,
            "requires": self.requires, "inRender": self.in_render,
        })


def _plain(value: Any) -> Any:
    """Whole floats as ints (JSON would print ``20.0``), recursively."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


# -- the help texts ---------------------------------------------------------
# Moved verbatim from the render panel's markup (index.html) by script, and
# the panel is now generated from them.
_HELP: dict[str, str] = {
    "method": (
        "How the surface is built from the annotations. Cylinder (Line3) fits a tangent frame per annotation and aligns each ring — the most faithful, and about 25x the work of Tube. Tube sweeps a variable-radius tube along the centerline. Closed curve rasterizes traced outlines into a voxel grid and runs marching cubes; a cell's discs join in as circles of their own radius, with the links between them (all of it, for a cell traced only with discs). Hybrid unions the closed-curve volume with a Cylinder build of the disc annotations — the only method that shows every annotation of a structure traced with both kinds (blood vessels, for example). Under Voxel remesh and Ultraliser the outlines join the discs' field and the two are one surface (Inferior c1178: 5 separate sheets became 1, its disc-drawn soma top no longer a shell of its own sitting on the traced soma); under the other pathways the two halves are laid side by side."
    ),
    "pathway": (
        "How the surface is built and finished, measured on c1178 (soma faceting = mean angle between neighboring faces; volume against the analytic lofted-cone value of 13,563 µm³).\n"
        "\n"
        "Original — what MATLAB did, and the default of the Python library and the bare API. Fixed 10 vertices per ring and a full-weight Laplacian smoother. 37° faceting, 63% of the volume, 4 s.\n"
        "\n"
        "Adaptive ring + Taubin — ring vertices scale with radius, and Taubin smooths without collapsing. 20° faceting, 106% volume, 4 s, twice the triangles.\n"
        "\n"
        "Voxel remesh orig — thick segments are rebuilt from a distance field that reads each annotation the way it was drawn: a disc in its section plane, not a sphere. Runs of stacked discs (somas, thick trunks) are lofted so the surface passes through every disc; genuine tube steps stay capsules. Soma faceting drops from ~34–37° to ~3°, the body ends exactly where the annotations end, and the field reconstructs analytic test shapes to under 1%. About the same build time as Original (0.90–0.96 of it, CPU time on c1178, c21768 and c10912) and ~1.5–3× the triangles. Only segments at least 1.5 µm across are remeshed.\n"
        "\n"
        "Voxel remesh (this viewer's default) — the same disc-loft fields for the thick bodies as Voxel remesh orig, plus every thin stretch as an exact round-cone field, all contoured together on ONE whole-cell grid at dendrite resolution (about two voxels across the thinnest 2% of annotations). One surface: a dendrite leaving the soma is part of the soma's sheet instead of a separate tube pushed into it, so a wireframe or a translucent material shows one object (c106 went from 27 separate pieces to 1). Same deviation from the annotations as Voxel remesh orig; several times the build time and up to the face cap in triangles.\n"
        "\n"
        "Convolution surface — a published implicit-surface method (Frontiers in Neuroinformatics 2022): a finitely supported quartic kernel is integrated along every skeleton segment with a weight that makes a straight neurite reproduce its own radius, the contributions are summed, and the surface is the level set of the sum. Superposition blends branch points and the soma into crease-free junctions where the voxel pathways meet at an exact intersection curve; it also rounds the soma over a two-radius kernel and tapers neurite ends. Whole-cell grid like Voxel remesh; the most expensive of the three because a soma's kernels reach two radii out.\n"
        "\n"
        "Ultraliser (voxelized proxies) — the Blue Brain pipeline for watertight neuron meshes (Abdellah et al. 2023): proxy geometry (round cones between annotations, plus either our disc-loft soma or an icosphere of the soma's mean radius) is voxelized to an inside/outside grid, contoured, then smoothed with a Laplacian pass that re-inflates to protect thin parts, and coarsened. The half-voxel staircase of the binary grid is what the smoothing removes; its own default resolution is 0.2 µm (set Fine voxel to 0.2 to see it), ours sizes the grid from the annotations."
    ),
    "smooth_iterations": (
        "How many smoothing passes. With Taubin the effect saturates quickly — most of the gain is in the first two — and more costs almost nothing, since the vertex adjacency is built once and each pass is a single sparse multiply."
    ),
    "reduce_fraction": (
        "Keeps this share of the mesh's triangles and collapses the rest. The shape barely changes; what shrinks is the download and how much your graphics card has to draw. A 10,000-annotation cell is 829,000 triangles at 100% and 249,000 at 30%. Try 30% when a cell is slow to appear or you are showing several at once."
    ),
    "pixel_size_um": (
        "Rasterization resolution for the closed-curve method and the outline half of Hybrid, in microns: pick a size from the list beside the box or type one from 0.005 to 1 µm (Enter or leaving the box sets it; anything else goes back to what was there). Halving it doubles the voxel faces in the sections' planes and quadruples those between sections: for a cone pedicle, 0.10 µm takes under a second where 0.025 µm takes ten.\n"
        "\n"
        "The chip at the end of the row estimates the build for the selected cells as you choose: triangles, the server's memory and seconds (hover it for the reasoning). Amber is a long wait or a large share of the server's memory; red is a size past a limit, with the finest size that fits. A size too fine for a cell (more than 300 million raster voxels or 10 million voxel faces, more memory than the server gives a build, or a mesh too large for the page to read) is built at the finest size that fits that cell, and a notice says which cell got which size."
    ),
    "voxel_smooth_um": (
        "Voxel remesh, Voxel remesh orig, Ultraliser and Wrap. Strength of the smoothing that turns the literal stack of annotated discs into the smooth outer envelope, in microns at a 5 µm body — larger bodies scale it up proportionally (a 10.9 µm soma gets ~1.1 µm at the default 0.5), because annotators' radius steps grow with the body. 0 shows the literal annotations, terraces and all; past ~1 genuine small features start to soften; 2 is deliberately too far. Under Wrap it smooths only the lofted pieces Wrap adds around its cube-fit bodies (on c1178 most of the surface does not move between 0.5 and 1.5)."
    ),
    "tie_um": (
        "Voxel remesh, Voxel remesh orig, Convolution surface, Ultraliser and Wrap. One cell should be one visual body, but the pieces these pathways assemble can miss each other by a hair — and Viking itself holds branches nobody ever linked (c1178 has seven). Any remaining gap up to this many microns is closed with a 0.25 µm tie wire along the shortest crossing; wider gaps stay honestly open. 0 turns ties off and shows the raw pieces."
    ),
    "voxel2_um": (
        "Voxel remesh, Convolution surface and Ultraliser. Edge of the whole-cell grid's voxel, in microns. 0 (auto) sizes it from the annotations — two voxels across the thinnest 2% of radii, never under 0.04 µm; a voxel typed here is honored down to 0.02 µm. Either way it coarsens if the raw sheet would pass three times the Face cap (10 million triangles at most, and with no cap), or the build would pass its evaluation budget; when a typed voxel comes out coarser, a notice says which voxel was used and why. Halving the voxel is about 4× the triangles and 3–4× the build time; the deviation from the annotations barely moves (c106: 0.092 µm at 0.05 and at 0.10)."
    ),
    "voxel2_faces": (
        "Voxel remesh, Convolution surface and Ultraliser. Triangle cap applied after smoothing. A whole-cell sheet spends triangles uniformly; quadric decimation takes them back off the flat soma and keeps them on the thin arbor. 0 keeps every triangle. The sheet marched before smoothing may be three times this, up to 10 million triangles (0 allows all 10 million), which is what limits how fine the voxel can go."
    ),
    "voxel2_blend_um": (
        "Voxel remesh only (not orig). Fillet radius, in microns, where bodies meet. 0 is the exact union — a crease along the true intersection curve; 0.2–0.4 rounds every dendrite root and branch point the way tissue does. Above 0 it also webs together surfaces of the same cell that pass within that distance of each other, and the fillet counts as unsupported surface in the deviation metric."
    ),
    "exact_tube_deg": (
        "Voxel remesh and Ultraliser, for the tubes of Cylinder, Tube and Hybrid's discs. A thin process is drawn as round cones from each disc to the next, and each cone ends in a ball a radius tall, so where a process crosses the sections steeply, its discs a section or two apart, the balls overlap across many sections and the tube takes the outer edge of every wobble of the annotated centers and radii. Above 0, a pair of discs on a stretch running within this many degrees of straight through the sections is drawn instead as the hull of the two discs, whose slice at each section is the disc exactly as annotated; an oblique process keeps its round cones, whose slice is the ellipse a slanted round process makes. No annotation moves. 0, the default, draws every pair as a round cone.\n"
        "\n"
        "Measured at 35° under Voxel remesh on eight cells (Inferior c943, c1178, c16137, c21768, c10912, Temporal c118, c106, c160). On the six with steep thin stretches, the tube's median cross-section through a steep disc falls from 1.16 to 1.73 times the disc's own area to 0.98 to 1.26 (Temporal c160 1.20 → 0.98, Inferior c1178 1.73 → 1.23). The mean distance from the annotations is the same or up to 7 % smaller (c160 0.090 → 0.084 µm, c21768 0.180 → 0.176; c106 0.5 % larger), and the volume up to 1.3 % smaller. The cost is a ring of creases at each section where one hull meets the next: creases per µm² of surface up to 3.6 times as many (c160 0.10 → 0.37, c21768 0.15 → 0.32, c1178 0.081 → 0.092). Smooth surface µm 0.5 takes most of them back out and keeps the closer fit (c118 0.27 → 0.16, against 0.13 with round cones; c21768 0.32 → 0.17, against 0.15). At 50° more pairs are hulls: c160's steep tubes 0.95 times, its mean distance 0.076 µm, its creases 0.63.\n"
        "\n"
        "Why it is not the default: annotators draw a disc at a process's width (0.85 of the traced half-width at every tilt, measured at 212 places where a traced outline links to a disc), so a tube exact to its discs is about 0.72 of a round process's traced cross-section, and the round cones' extra lands near the traced size on average. Exact tubes are closer to the annotations, not necessarily to the cell."
    ),
    "conv_support": (
        "Convolution surface only. Kernel radius per neurite radius (R = k·d). The paper's empirical value is 2: kernels reach two radii out, so junctions blend over about a radius and a soma is rounded over twice its radius. Smaller is crisper and closer to the annotations: 1.6 was closer than 2 on all four cells measured (rims unreached by the surface, Temporal c118 0.18 against 0.21 µm, Inferior c16137 0.27 against 0.36). Larger blends more and costs more, and it shrinks every short, fat body: the kernel's weight is set so an infinitely long process keeps its radius, and a soma about as tall as it is wide has too little length under a wide kernel to reach the surface. On c118 the rims unreached rise to 0.39 µm at 2.5, 0.74 at 3 and 1.23 at 4, where the soma is gone (none of its 67 soma discs inside the surface). So the box stops at 2.5; a value up to 4 is still accepted from the API and from a page that was open before 2.7.1."
    ),
    "ultra_soma": (
        "Ultraliser only. What stands in for the soma before voxelization. Discs: our disc-loft field, the reading every other pathway uses. Sphere: an icosphere of the soma's mean radius at its centroid — Ultraliser's own starting soma (it then pulls it toward the neurites, which is not reproduced here). Sphere shows what a skeleton tool makes of soma sections."
    ),
    "ultra_smooth": (
        "Ultraliser only. Iterations of the optimizer's Laplacian smoothing, each followed by an inflate step. Removes the half-voxel staircase of the binary grid; more shrinks thin parts unless Inflate keeps up."
    ),
    "ultra_lambda": (
        "Ultraliser only. The smoothing value: how far each vertex moves toward its neighbors' mean per iteration (0–1)."
    ),
    "ultra_inflate": (
        "Ultraliser only. The inflate step after each smoothing step, moving vertices back out from the new mean; Ultraliser adds it to dampen the shrinking of thin parts. Slightly above Smooth λ holds volume; 0 is plain Laplacian shrinkage."
    ),
    "ring_arc_um": (
        "Adaptive ring + Taubin and Wrap (cube fit). Target spacing between adjacent ring vertices, in microns (clamped 8–128 points per ring). This controls facet fineness, mostly visible on thin dendrites — it cannot fix the sharp creases where overlapping soma tubes fold through each other; that is structural to tube sweeping, and the Voxel remesh pathway is the fix for somas. The default 0.35 µm gives a 10.9 µm soma 128 points instead of the fixed pipeline's 10."
    ),
    "wrap_spacing_um": (
        "Wrap (cube fit) only. Size of the evaluation cubes in microns — the new surface's resolution. Smaller cubes follow finer shape at more cost."
    ),
    "wrap_blend": (
        "Wrap (cube fit) only. Neighborhood reach as a multiple of the cube size: how far each cube looks for mesh vertices when fitting its local plane. Larger values smooth more."
    ),
    "meshlab": (
        "Remesh the finished surface with MeshLab (pymeshlab, run on the server in a process of its own). It changes the triangles, not the accuracy: fidelity to the annotations is set by the technique.\n"
        "\n"
        "Isotropic: uniform, well-shaped triangles with edges about this long, kept within a tenth of an edge of the surface. Inferior c943: 147,700 → 10,916 faces at 0.25 µm, 0.014 µm from the original, slivers 2.3 % → 0.9 %. Best for exports to Blender and for meshing software that dislikes marching-cubes slivers.\n"
        "\n"
        "Quadric (QEM): collapses edges down to this share of the faces, keeping topology and normals. c943 at 25 %: 0.012 µm from the original. Better than Decimate at the same face count.\n"
        "\n"
        "Poisson: a new watertight surface fitted to the vertices and normals, the step applied to VikingMesh output before Blender. On these meshes it gains nothing and leaves more slivers (11 %); depth 9 loses detail on a large cell (RC1 c606: 0.10 µm from the original).\n"
        "\n"
        "Exports follow what is on screen, so the exported file is the remeshed one."
    ),
    "meshlab_edge_um": (
        "Isotropic remesh: target edge length in microns. 0.25 suits a whole cell; 0.15 keeps more of a fine process and costs about twice the faces."
    ),
    "meshlab_fraction": (
        "Quadric decimation: the share of the faces to keep."
    ),
    "meshlab_depth": (
        "Screened Poisson: octree depth. Each step doubles the resolution; 9 is coarse for a large cell, 10 follows it."
    ),
    "curve_avg_discs": (
        "Moves the discs before the surface is built (never in Viking). Each disc's center (X and Y; never Z, so it stays in its own section) and radius are replaced by a smooth fit through its neighbors along its chain of links, over this many of its own radii, so a soma disc moves more than a dendrite disc. The two ends of every chain stay as annotated. 0, the default, draws every disc exactly as annotated; to smooth the finished surface without moving any annotation, use Smooth surface µm.\n"
        "\n"
        "How far, at 0.5: 72 to 91 % of a cell's discs move, a median 0.015 to 0.044 µm and 95 % of them under 0.10 to 0.20 µm (Inferior c1178, c16137, c943, Temporal c118); soma discs a median 0.16 to 0.24 µm and at most 0.77 µm; radii within 6 %. At 1, c118's soma discs move up to 1.56 µm. For scale, the section correction moves Inferior c1178 a median 0.109 µm, at most 3.99 µm.\n"
        "\n"
        "Why move them. Under Cylinder, Tube and Hybrid a tube is the union of round cones from each disc to the next, so it takes the outer edge of every wobble in the annotators' centers and radii and comes out fatter than the process: Inferior c1178's thin discs a median 1.27 times their own area under Voxel remesh, 1.11 at 0.5. Under Closed curve it also averages out the staircase of a cell traced with discs (a soma drawn as a stack of plates), and then smooths the finished surface over the same length, except where a process is only a pixel or two across: Inferior c16137 at Voxel 0.1 µm, strength 1, creases per µm² of surface 1.08 → 0.45, the mean distance from the annotations 0.075 → 0.081 µm."
    ),
    "curve_avg_paths": (
        "Closed curve and Hybrid. Moves the traced outlines before the surface is built (never in Viking), to average out their staircase from one section to the next: each outline is replaced by the average of its neighbors' along its chain of links, point for point, over this many microns of the chain, and the finished surface is then smoothed over the same distance. Microns, not sections, because sections are 70 nm apart in one volume and 90 nm in another; 0.25 µm is three or four sections. 0, the default, draws every outline exactly as traced; to smooth the finished surface without moving any outline, use Smooth surface µm.\n"
        "\n"
        "How far, at 0.25 µm: a median outline point moves 0.019 µm (RC1 c410, 1,959 outlines) to 0.070 µm (Inferior c1178's soma), 95 % of them under 0.13 to 0.33 µm, but a point where an outline changes shape sharply from one section to the next (a process leaving it) can move 2 to 3 µm; at 0.1 µm, 0.010 to 0.043 µm, at most 0.8 to 1.1 µm.\n"
        "\n"
        "RPC1 c2628 at Voxel 0.1 µm: 0.25 cuts the creases per µm² of surface from 1.76 to 0.69 and raises the mean distance from the annotations from 0.111 to 0.138 µm; 0.1 is gentler (1.30, 0.121 µm), 0.5 stronger (0.42, 0.162 µm)."
    ),
    "surface_smooth_um": (
        "Every method. Smooths the finished surface over this many microns and moves no annotation: the surface is drawn through the annotations as the method draws it, then each vertex takes volume-preserving Taubin passes (no shrinking) for a length of this many microns, none where its process is only a pixel or two across, so a thin dendrite is not eroded. What it takes out is the fine grain a build leaves: the pixel-wide teeth where a Closed curve contour crosses from one section's footprint to the next, the kinks where discs a section apart are joined across a small offset, the rings Exact tubes leaves. Steps several sections tall that are in the tracing itself stay (Inferior c1178's traced soma keeps its plates at 1 µm): averaging those is what Smooth discs and Smooth outlines do, by moving the annotations. 0, the default, leaves the surface as built. Smooth discs and Smooth outlines do something else: they move the annotations before the surface is built.\n"
        "\n"
        "Measured, the mean distance from the annotations the same to within 0.003 µm and the volume to within 0.4 % in every case: Inferior c16137, Closed curve at Voxel 0.1 µm, creases per µm² of surface 1.08 → 0.78 at 0.1, 0.51 at 0.25, 0.32 at 0.5, the surface moving at most 0.02, 0.05 and 0.06 µm; Inferior c1178, Hybrid at Voxel 0.1 µm, 0.196 → 0.161 at 0.1, 0.108 at 0.5 (at most 0.09 and 0.31 µm); RC1 c410, Hybrid at 0.25 µm, 0.626 → 0.527 at 0.1, 0.380 at 0.5. A coarse Voxel size has long edges, so small lengths are one pass (c1178, Closed curve at 0.25 µm: 1.19 → 0.98 for anything up to 0.25, 0.70 at 0.5). Under Voxel remesh alone it changes little: Passes already smooths that surface (Temporal c118 0.132 → 0.112 to 0.139). It adds under a second to 3.5 s to a build.\n"
        "\n"
        "Where it stops. A vertex takes at most 20 passes, which is a length of about 6.7 of the mesh's edges; past it every setting builds the same surface. On Inferior c1178 that is about 0.65 µm under Closed curve at Voxel 0.1 µm (edges 0.096 µm), 0.8 µm under Hybrid at 0.1 µm and 1.3 µm under Closed curve at 0.25 µm. More passes would not reach further: Taubin takes out only grain a few edges long, and with no stop at all 2 µm moved c1178's surface at most 0.11 µm. So 0.25 to 0.5 at Voxel 0.1 µm, 0.5 to 1.25 at 0.25 µm. To smooth steps several sections tall, use Smooth discs or Smooth outlines."
    ),
    "curve_links_um": (
        "Closed curve only. A link between two discs is drawn as a straight band from one to the other, and a chain of long links as a zigzag with a corner at every disc. Above 0, a link whose discs are at least this many µm apart follows a smooth curve through the chain instead: it passes through both disc centers and takes its direction at each from the discs on the far side (a Catmull-Rom spline, in 3D, so a run that leaves a chain of discs climbing leaves it climbing). It stays straight at a chain's end, at a branch and where the chain turns back by more than 90°, never bends tighter than the process's own radius, and keeps within a quarter of its length of the straight band. 0 draws every link straight.\n"
        "\n"
        "Inferior c1178 at 1 µm: 163 of its 211 links that long curve, the turn at their discs falls from 29° to 13° (median), and the farthest a curve strays from its straight band is 0.58 µm, on a link that crosses 22 untraced sections; c16137: 33 of 37, 18° to 9°, 0.07 µm. 1 is the suggestion: a link under 1 µm is under four radii of a typical dendrite, reads as a joint rather than a band, and 95 % of them would stray under 0.05 µm. It adds about a second to the build."
    ),
    "curve_tie_um": (
        "Closed curve only. One cell should be one body, but a Closed curve surface can come apart: the voxel grid's smoothing removes any process under about a pixel and a half in radius (at Voxel 0.25 µm a 0.2 µm dendrite breaks into beads, and a thin traced process into specks), and Viking holds parts nobody linked. Bodies up to this many µm apart are joined by a tie drawn into the grid along the shortest crossing between their surfaces, a pixel and a half thick at least, so the surface stays one sheet; wider gaps stay open. 0 shows the bodies as they come. The viewer starts at 5 µm; the library and the API at 0. A tie is surface no annotation drew (on Inferior c16137 at 0.25 µm, 5 µm moves the mean distance of the unsupported surface from the annotations from 0.064 to 0.108 µm).\n"
        "\n"
        "At Voxel 0.25 µm Inferior c1178 is 44 bodies: 23 at 1 µm, 11 at 2 µm, 1 at 5 µm (58 ties, about 1.5 s); RPC1 c2628 70: 8, 3 and 1. At 0.1 µm c1178 is 2 bodies and 1 from 1 µm, c2628 94 and 1 from 2 µm (397 ties, about 2.5 s). A finer Voxel keeps thin processes whole by itself."
    ),
    "curve_bridge_um": (
        "Closed curve and Hybrid. Annotators skip sections. Where two traced sections are at most this many µm apart, the sections skipped between them are filled by interpolating the two sections' outlines; a longer skip stays open, because a body invented across it would be a guess. 1 µm, the default (a constant until 2026-10-16), bridges 93 % of the untraced runs in the 160 most-traced structures of the eight volumes, 82 % of which are one or two sections. It acts on traced outlines: a cell traced with discs alone has its links drawn instead. 0 bridges nothing."
    ),
    "curve_link_bridge_um": (
        "Closed curve and Hybrid. A Viking link between two annotations more than a section apart says the process carries on through the sections between them, and it is followed through them, branch by branch: a disc's run, or the two traced outlines interpolated. This is the most Z, in µm, a link is followed across; a longer one is taken for a stray and not drawn. 5 µm, the default (a constant until 2026-10-16): the 1,597 section-skipping links of 18 surveyed cells span 0.56 µm or less, and Inferior c1178's three longest cross 22 untraced sections, 1.98 µm. Lower it to drop such a link; 0 follows none across a skip. A link to the next section skips nothing and is always drawn."
    ),
    "post_order": (
        "Smoothing and decimation both run after the mesh is built, and the order changes the result. Smooth first works on the full-resolution mesh and gives a far better surface — measured on c1178 at 30% decimation, 26° faceting smoothing first against 53° decimating first. Decimate first is cheaper, because the smoother then has fewer vertices to move. Adaptive rings is not part of this choice: it happens during generation, before either."
    ),
    "wrap_reject_sigma": (
        "Wrap (cube fit) only. Outlier cut in standard deviations: vertices whose distance from the cube's fitted plane exceeds this many σ lose their vote before the refit."
    ),
    "wrap_passes": (
        "Wrap (cube fit) only. How many reject-and-refit rounds each cube runs. 0 disables outlier rejection."
    ),
    "clip_ipl": (
        "Cuts every mesh at a stratification depth (0 = INL border, 1 = GCL border), keeping the side above or below — for isolating ON vs OFF arbors. It lives with the render settings because the cut is applied on the server when the mesh is built; applied by Apply to selected / all. Needs the volume to have IPL boundary markers."
    ),
}

_GEOMETRY = ("mesh", "export", "body", "facade")
#: ``_cached_mesh`` takes these four as explicit arguments, not through the
#: facade's ``MESH_OPTION_DEFAULTS``.
_GEOMETRY_ARGS = ("mesh", "export", "body")
_VIEW = ("mesh",)

_METHODS = (
    ("cylinder", "Cylinder (Line3)"),
    ("curvemesh", "Tube (curveToMesh)"),
    ("closedcurve", "Closed curve volume"),
    ("hybrid", "Hybrid (discs + curves)"),
)

# Pathway order is the dropdown's, not PATHWAYS'.
_PATHWAYS = (
    ("original", "Original (MATLAB)"),
    ("adaptive_taubin", "Adaptive ring + Taubin"),
    ("voxel2", "Voxel remesh"),
    ("voxel", "Voxel remesh orig"),
    ("convolution", "Convolution surface"),
    ("ultraliser", "Ultraliser (voxelized proxies)"),
    ("wrap", "Wrap (cube fit)"),
)

_MESHLAB_MODES = (
    ("none", "off"),
    ("isotropic", "isotropic remesh"),
    ("qem", "quadric decimation"),
    ("poisson", "screened Poisson"),
)

def _ids(wrapper: str, control: str, **more: str) -> dict[str, str]:
    return {"wrapper": wrapper, "control": control, **more}


# What each method actually uses (the ``inert`` rules below, and the pixel size's
# ``show``): measured, not assumed.  The closed-curve path (``_build_geometry``)
# receives the voxel size and nothing else: not the pathway, not its knobs, not
# Passes, Order or Decimate (c1178: the mesh is identical bit for bit with all of
# them changed).  ``smooth_iterations`` is ignored by the tube path only under the
# Original pathway (``cylinder_render`` skips the Laplacian smoother for method 2;
# Taubin, which every other pathway but Ultraliser uses, is applied to a tube
# too: c1178 moves up to 5.2 µm between 0 and 2 passes under Adaptive + Taubin)
# and by Ultraliser, which smooths the contour itself (identical for 0 and 2
# passes, cylinder and tube).  Showing live controls that the chosen method
# silently discards is worse than showing them greyed.  Hybrid takes both
# halves' knobs: the disc half all the cylinder controls, the curve half the voxel
# size.  Each pathway's own parameters appear only when that pathway is chosen:
# ties join the pieces the remesh pathways assemble; ring detail also drives the
# wrap's thin tubes and its fit input; the whole-cell field pathways (voxel2,
# convolution, ultraliser) share the grid and face-cap knobs, the fillet is
# voxel2's, the kernel support the convolution surface's, and the soma proxy and
# smoothing knobs Ultraliser's; MeshLab has one knob per mode.

#: The reason the closed-curve method gives for every pathway option.
_NO_PATHWAY: tuple[Mapping[str, Any], ...] = ({
    "when": {"method": ("closedcurve",)},
    "why": "The closed-curve method builds its surface from a voxel grid, so it has no "
           "pathway and ignores this.",
},)

#: In the order of the browser's ``DEFAULT_RENDER`` (the undo history compares
#: the JSON of the controls, so that order is not free to change), with the
#: API-only ``clip_z`` last.  Where a field sits in the panel is ``PANEL_SLOTS``.
FIELDS: tuple[Field, ...] = (
    Field(
        "method", "enum", "Method", "cylinder", help=_HELP["method"],
        choices=_METHODS, slot="strip", wire=_GEOMETRY_ARGS,
        ids=_ids("method-field", "render-method"),
    ),
    Field(
        "pathway", "enum", "Pathway", "original", help=_HELP["pathway"], app_default="voxel2",
        choices=_PATHWAYS, server_enum=True, slot="strip", wire=_GEOMETRY,
        ids=_ids("pathway-field", "pathway"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "smooth_iterations", "int", "Passes", 1, help=_HELP["smooth_iterations"],
        min=0, max=10, server=(("ge", 0), ("le", 50)), slot="strip", tune=True,
        wire=_GEOMETRY_ARGS,
        inert=(
            {"when": {"method": ("closedcurve",)},
             "why": "The closed-curve method builds its surface from a voxel grid and ignores this."},
            {"when": {"method": ("curvemesh",), "pathway": ("original",)},
             "why": "The tube method under the Original pathway is not smoothed (MATLAB skipped "
                    "it for curveToMesh) and ignores this."},
            {"when": {"pathway": ("ultraliser",)},
             "why": "Ultraliser smooths the contour itself (its Smoothing step) and ignores this."},
        ),
        ids=_ids("smooth-field", "smooth-iter"),
    ),
    Field(
        "post_order", "enum", "Order", "smooth_first", help=_HELP["post_order"],
        choices=(("smooth_first", "smooth → decimate"), ("decimate_first", "decimate → smooth")),
        server_enum=True, slot="more", tune=True, wire=_GEOMETRY,
        ids=_ids("order-field", "post-order"),
        inert=(*_NO_PATHWAY, {
            "when": {"pathway": ("ultraliser",)},
            "why": "Ultraliser has no generic smoothing pass, so there is nothing to put before "
                   "or after the decimation.",
        }),
    ),
    Field(
        "voxel_detail", "float", "Voxel detail", 1.0, min=0.25, max=3.0,
        server=(("gt", 0.2), ("le", 3)), wire=_GEOMETRY, ui=False,
        ui_reason="API-only: it changes the voxel pathways but has never had a control",
        inert=_NO_PATHWAY,
    ),
    Field(
        "voxel_smooth_um", "float", "Envelope σ", 0.5, help=_HELP["voxel_smooth_um"],
        min=0, max=2, step=0.1, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("voxel", "voxel2", "ultraliser", "wrap")},
        ids=_ids("voxel-smooth-field", "voxel-smooth"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "tie_um", "float", "Tie gaps ≤", 20.0, help=_HELP["tie_um"],
        min=0, max=100, step=1, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("voxel", "wrap", "voxel2", "convolution", "ultraliser")},
        ids=_ids("tie-field", "tie-um"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "ring_arc_um", "float", "Ring detail", 0.35, help=_HELP["ring_arc_um"],
        min=0.1, max=1.5, step=0.05, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("adaptive_taubin", "wrap")},
        ids=_ids("ring-arc-field", "ring-arc"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "wrap_spacing_um", "float", "Cube size", 0.4, help=_HELP["wrap_spacing_um"],
        min=0.15, max=2, step=0.05, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("wrap",)},
        ids=_ids("wrap-spacing-field", "wrap-spacing"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "wrap_blend", "float", "Blend", 2.0, help=_HELP["wrap_blend"],
        min=1.2, max=6, step=0.1, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("wrap",)},
        ids=_ids("wrap-blend-field", "wrap-blend"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "wrap_reject_sigma", "float", "Reject σ", 2.0, help=_HELP["wrap_reject_sigma"],
        min=0.5, max=6, step=0.25, slot="more", tune=True, wire=_GEOMETRY,
        show={"pathway": ("wrap",)},
        ids=_ids("wrap-reject-field", "wrap-reject"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "wrap_passes", "int", "Refits", 2, help=_HELP["wrap_passes"],
        min=0, max=6, step=1, slot="more", tune=True, wire=_GEOMETRY,
        show={"pathway": ("wrap",)},
        ids=_ids("wrap-passes-field", "wrap-passes"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "voxel2_um", "float", "Fine voxel", 0.0, help=_HELP["voxel2_um"],
        min=0, max=0.3, step=0.01, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("voxel2", "convolution", "ultraliser")},
        ids=_ids("voxel2-um-field", "voxel2-um"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "voxel2_faces", "int", "Face cap", 400_000, help=_HELP["voxel2_faces"],
        min=0, max=5_000_000, step=10_000, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("voxel2", "convolution", "ultraliser")},
        ids=_ids("voxel2-faces-field", "voxel2-faces"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "voxel2_blend_um", "float", "Junction fillet", 0.0, help=_HELP["voxel2_blend_um"],
        min=0, max=1, step=0.05, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("voxel2",)},
        ids=_ids("voxel2-blend-field", "voxel2-blend"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "exact_tube_deg", "float", "Exact tubes °", 0.0, help=_HELP["exact_tube_deg"],
        min=0, max=60, step=5, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("voxel2", "ultraliser")},
        ids=_ids("exact-tube-field", "exact-tube"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "conv_support", "float", "Kernel R/d", 2.0, help=_HELP["conv_support"],
        # The panel stops where a soma still holds (2.7.1; convolution.py's notes);
        # the server keeps the range it always accepted.
        min=1.2, max=2.5, step=0.1, server=(("ge", 1.2), ("le", 4.0)), slot="strip",
        tune=True, wire=_GEOMETRY,
        show={"pathway": ("convolution",)},
        ids=_ids("conv-support-field", "conv-support"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "ultra_soma", "enum", "Soma proxy", "discs", help=_HELP["ultra_soma"],
        choices=(("discs", "discs (ours)"), ("sphere", "sphere (Ultraliser)")),
        server_enum=True, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("ultraliser",)},
        ids=_ids("ultra-soma-field", "ultra-soma"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "ultra_smooth", "int", "Smoothing", 15, help=_HELP["ultra_smooth"],
        min=0, max=100, step=1, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("ultraliser",)},
        ids=_ids("ultra-smooth-field", "ultra-smooth"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "ultra_lambda", "float", "Smooth λ", 0.5, help=_HELP["ultra_lambda"],
        min=0, max=1, step=0.05, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("ultraliser",)},
        ids=_ids("ultra-lambda-field", "ultra-lambda"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "ultra_inflate", "float", "Inflate", 0.52, help=_HELP["ultra_inflate"],
        min=0, max=1, step=0.01, slot="strip", tune=True, wire=_GEOMETRY,
        show={"pathway": ("ultraliser",)},
        ids=_ids("ultra-inflate-field", "ultra-inflate"),
        inert=_NO_PATHWAY,
    ),
    Field(
        "meshlab", "enum", "MeshLab", "none", help=_HELP["meshlab"],
        choices=_MESHLAB_MODES, server_enum=True, slot="more", tune=True, wire=_GEOMETRY,
        requires="meshlab", ids=_ids("meshlab-field", "meshlab-mode"),
    ),
    Field(
        "meshlab_edge_um", "float", "Edge µm", 0.25, help=_HELP["meshlab_edge_um"],
        min=0.05, max=2, step=0.05, slot="more", tune=True, wire=_GEOMETRY,
        show={"meshlab": ("isotropic",)},
        ids=_ids("meshlab-edge-field", "meshlab-edge"),
    ),
    Field(
        "meshlab_fraction", "float", "Keep", 0.25, help=_HELP["meshlab_fraction"],
        min=0.02, max=1, step=0.05, slot="more", tune=True, wire=_GEOMETRY,
        show={"meshlab": ("qem",)},
        ids=_ids("meshlab-fraction-field", "meshlab-fraction"),
    ),
    Field(
        "meshlab_depth", "int", "Depth", 9, help=_HELP["meshlab_depth"],
        min=5, max=11, step=1, slot="more", tune=True, wire=_GEOMETRY,
        show={"meshlab": ("poisson",)},
        ids=_ids("meshlab-depth-field", "meshlab-depth"),
    ),
    Field(
        "reduce_fraction", "percent", "Decimate", 1.0, help=_HELP["reduce_fraction"],
        min=0.1, max=1, step=0.05, server=(("gt", 0), ("le", 1)), slot="strip", tune=True,
        wire=_GEOMETRY_ARGS,
        inert=({"when": {"method": ("closedcurve",)},
                "why": "The closed-curve method ignores this; use Voxel size to control detail."},),
        ids=_ids("reduce-field", "reduce-frac", out="reduce-out"),
    ),
    # Typed or picked (2026-10-16).  The floor is 0.005 um: 5 nm is the native XY
    # pixel of the Temporal and Nasal volumes (5.0 and 5.3 nm), finer than the
    # 7.5 nm of Inferior and 2.3 times the 2.2 nm of RC1, RC2, RPC1 and RPC2, and
    # 10 to 18 times finer than a section (50 to 90 nm): nothing an annotator drew
    # is finer, and MATLAB rasterized at 0.01.  The server's limits stop a whole
    # cell long before it (RPC1 c2628's finest is 0.021 um, Inferior c16137's
    # 0.011); a small structure can go that far.  The server still accepts any
    # pixel above 0 (its refusals name the finest that fits).
    Field(
        "pixel_size_um", "numchoice", "Voxel", 0.05, help=_HELP["pixel_size_um"],
        min=0.005, max=1, server=(("gt", 0), ("le", 1)), slot="strip", tune=True,
        wire=_GEOMETRY_ARGS,
        choices=((1, "1 µm (vessels)"), (0.25, "0.25 µm (whole cells)"), (0.1, "0.10 µm (coarse)"),
                 (0.05, "0.05 µm"), (0.025, "0.025 µm (fine)")),
        show={"method": ("closedcurve", "hybrid")},
        ids=_ids("pixel-field", "pixel-size", menu="pixel-size-menu"),
    ),
    Field(
        "curve_avg_discs", "float", "Smooth discs ×r", 0.0, help=_HELP["curve_avg_discs"],
        min=0, max=3, step=0.1, slot="strip", tune=True, wire=_GEOMETRY,
        show={"method": ("closedcurve", "cylinder", "curvemesh", "hybrid")},
        ids=_ids("curve-avg-discs-field", "curve-avg-discs"),
    ),
    Field(
        "curve_avg_paths", "float", "Smooth outlines µm", 0.0, help=_HELP["curve_avg_paths"],
        min=0, max=2, step=0.05, slot="strip", tune=True, wire=_GEOMETRY,
        show={"method": ("closedcurve", "hybrid")},
        ids=_ids("curve-avg-paths-field", "curve-avg-paths"),
    ),
    Field(
        "surface_smooth_um", "float", "Smooth surface µm", 0.0, help=_HELP["surface_smooth_um"],
        min=0, max=2, step=0.05, slot="strip", tune=True, wire=_GEOMETRY,
        ids=_ids("surface-smooth-field", "surface-smooth"),
    ),
    Field(
        "curve_links_um", "float", "Curve links µm", 0.0, help=_HELP["curve_links_um"],
        min=0, max=20, step=0.25, slot="strip", tune=True, wire=_GEOMETRY,
        show={"method": ("closedcurve",)},
        ids=_ids("curve-links-field", "curve-links"),
    ),
    Field(
        "curve_tie_um", "float", "Join bodies µm", 0.0, help=_HELP["curve_tie_um"], app_default=5.0,
        min=0, max=20, step=0.25, slot="strip", tune=True, wire=_GEOMETRY,
        show={"method": ("closedcurve",)},
        ids=_ids("curve-tie-field", "curve-tie"),
    ),
    Field(
        "curve_bridge_um", "float", "Bridge skips µm", 1.0, help=_HELP["curve_bridge_um"],
        min=0, max=10, step=0.1, slot="more", tune=True, wire=_GEOMETRY,
        show={"method": ("closedcurve", "hybrid")},
        ids=_ids("curve-bridge-field", "curve-bridge"),
    ),
    Field(
        "curve_link_bridge_um", "float", "Follow links µm", 5.0,
        help=_HELP["curve_link_bridge_um"],
        min=0, max=50, step=0.5, slot="more", tune=True, wire=_GEOMETRY,
        show={"method": ("closedcurve", "hybrid")},
        ids=_ids("curve-link-bridge-field", "curve-link-bridge"),
    ),
    Field(
        "color_by_strata", "bool", "Color by strata", False, scope="view", wire=_VIEW, ui=False,
        ui_reason="a per-cell display choice: the cell panel's depth coloring owns it, and "
                  "Apply keeps each cell's own",
    ),
    Field(
        "clip_ipl", "optfloat", "Clip IPL", None, help=_HELP["clip_ipl"], step=0.05,
        slot="more", scope="view", wire=_VIEW, companion="keep_above", placeholder="off",
        ids=_ids("clip-field", "clip-ipl", side="clip-side"),
    ),
    Field(
        "keep_above", "bool", "Keep above", True, scope="view", wire=_VIEW,
        choices=(("above", "above"), ("below", "below")), ui=False,
        ui_reason="edited by the Clip IPL control (its side select), not on its own",
    ),
    Field(
        "clip_z", "optfloat", "Clip Z", None, scope="view", wire=_VIEW, ui=False,
        in_render=False,
        ui_reason="API-only: a cut by section depth, which the app does by Hide above/below "
                  "(a view change, not a rebuild)",
    ),
)

#: Where the controls sit, in DOM order: the render panel's row, then its More menu.
PANEL_SLOTS: dict[str, tuple[str, ...]] = {
    "strip": (
        "method", "pathway", "smooth_iterations", "reduce_fraction", "pixel_size_um",
        "curve_avg_discs", "curve_avg_paths", "surface_smooth_um", "curve_links_um", "curve_tie_um",
        "voxel_smooth_um", "tie_um", "voxel2_um", "voxel2_faces", "voxel2_blend_um", "exact_tube_deg",
        "conv_support", "ultra_soma", "ultra_smooth", "ultra_lambda", "ultra_inflate",
        "ring_arc_um", "wrap_spacing_um", "wrap_blend",
    ),
    "more": (
        "meshlab", "meshlab_edge_um", "meshlab_fraction", "meshlab_depth", "post_order",
        "wrap_reject_sigma", "wrap_passes", "curve_bridge_um", "curve_link_bridge_um", "clip_ipl",
    ),
}

_BY_KEY = {f.key: f for f in FIELDS}


def field_by_key(key: str) -> Field:
    return _BY_KEY[key]


def api_defaults() -> dict[str, Any]:
    """Every option's default as the library and bare API have it."""
    return {f.key: f.default for f in FIELDS}


def app_defaults() -> dict[str, Any]:
    """Every option's default as the web app starts (``DEFAULT_RENDER``'s values)."""
    return {f.key: f.app_value() for f in FIELDS}


# -- the named techniques -----------------------------------------------------
@dataclass(frozen=True, eq=False)
class Preset:
    """A named technique: a method and pathway (and, for the MeshLab entries,
    a MeshLab mode) that fill the same form.

    One list feeds the render bar's picker, every Render compare pane's picker
    and ``sbfsem-measure``'s ``--technique`` names.  ``set`` is what the preset
    adds to the technique's defaults in the app; ``measure_set`` is what the
    command line adds on top, for a measurement that wants a fixed value the
    app seeds per cell instead (a tube is measured unsmoothed, a closed-curve
    volume at the 0.25 um the app uses for whole cells).  ``short`` is a
    narrower label for a pane header, where there is one.  ``requires`` names
    an optional server feature (``REQUIRES``) the preset needs.
    """

    id: str
    label: str
    method: str
    pathway: str
    short: str | None = None
    set: Mapping[str, Any] = field(default_factory=dict)
    measure_set: Mapping[str, Any] = field(default_factory=dict)
    requires: str | None = None

    def meshlab(self) -> str:
        return str(self.set.get("meshlab", "none"))

    def to_js(self) -> dict[str, Any]:
        return _plain({
            "id": self.id, "label": self.label, "short": self.short, "method": self.method,
            "pathway": self.pathway, "set": dict(self.set), "requires": self.requires,
        })


#: In the order the picker lists them.  The three MeshLab entries put the app's
#: default technique through MeshLab, so the error map and a Difference pane can
#: say what each remesh costs.  (The tube is geometry-identical at 0, 1 and 3
#: smoothing passes, so it carries no ``smooth_iterations`` of its own and
#: shares the main view's cached mesh.)
PRESETS: tuple[Preset, ...] = (
    Preset("voxel2", "Voxel remesh", "cylinder", "voxel2"),
    Preset("voxel", "Voxel remesh orig", "cylinder", "voxel"),
    Preset("convolution", "Convolution surface", "cylinder", "convolution"),
    Preset("ultraliser", "Ultraliser (voxelized proxies)", "cylinder", "ultraliser",
           short="Ultraliser"),
    Preset("original", "Original (MATLAB)", "cylinder", "original"),
    Preset("adaptive", "Adaptive + Taubin", "cylinder", "adaptive_taubin"),
    Preset("wrap", "Wrap (cube fit)", "cylinder", "wrap"),
    Preset("tube", "Tube (curveToMesh)", "curvemesh", "original",
           measure_set={"smooth_iterations": 0}),
    Preset("closedcurve", "Closed curve volume", "closedcurve", "original",
           measure_set={"pixel_size_um": 0.25}),
    Preset("hybrid", "Hybrid (discs + curves)", "hybrid", "voxel",
           measure_set={"pixel_size_um": 0.25}),
    Preset("voxel2_iso", "Voxel remesh + MeshLab isotropic", "cylinder", "voxel2",
           set={"meshlab": "isotropic"}, requires="meshlab"),
    Preset("voxel2_qem", "Voxel remesh + MeshLab quadric", "cylinder", "voxel2",
           set={"meshlab": "qem"}, requires="meshlab"),
    Preset("voxel2_poisson", "Voxel remesh + MeshLab Poisson", "cylinder", "voxel2",
           set={"meshlab": "poisson"}, requires="meshlab"),
)

_PRESET_BY_ID = {p.id: p for p in PRESETS}


def preset_by_id(preset_id: str) -> Preset:
    return _PRESET_BY_ID[preset_id]


def technique_table() -> dict[str, dict[str, Any]]:
    """The presets as render options, as ``sbfsem-measure`` names them."""
    return {
        p.id: {"method": p.method, "pathway": p.pathway, **p.set, **p.measure_set}
        for p in PRESETS
    }


# -- when a field applies ---------------------------------------------------
@dataclass(frozen=True)
class FieldState:
    hidden: bool
    inert: bool
    why: str
    disabled: bool
    title: str


def field_state(
    options: Mapping[str, Any] | None = None, ctx: Mapping[str, Any] | None = None,
) -> dict[str, FieldState]:
    """Which fields hide, grey out or disable for ``options``.

    The reference evaluator: ``renderoptions.js``'s ``fieldState`` does the same
    in the browser and is checked against :func:`visibility_cases`.  A field is
    hidden unless every ``show`` rule holds; a visible field is inert when every
    option of one of its ``inert`` rules holds one of the rule's values (the first
    rule says why); a field that ``requires``
    a server feature is disabled when ``ctx`` has ``<feature>Off``.  A hidden
    field is never inert: nobody can see it.
    """
    full = {**app_defaults(), **(options or {})}
    ctx = ctx or {}
    out: dict[str, FieldState] = {}
    for f in FIELDS:
        hidden = any(full.get(k) not in allowed for k, allowed in f.show.items())
        inert, why = False, ""
        if not hidden:
            for rule in f.inert:
                if all(full.get(k) in values for k, values in rule["when"].items()):
                    inert, why = True, rule["why"]
                    break
        disabled = bool(f.requires and ctx.get(f"{f.requires}Off"))
        out[f.key] = FieldState(
            hidden=hidden, inert=inert, why=why, disabled=disabled,
            title=REQUIRES[f.requires] if disabled and f.requires else "",
        )
    return out


def match_preset(options: Mapping[str, Any]) -> Preset | None:
    """The named technique a set of options is, as the browser's ``matchPreset``
    names it: the presets of its method and pathway, the one with its MeshLab
    mode first; ``None`` for a method and pathway no preset has."""
    same = [p for p in PRESETS
            if p.method == options.get("method") and p.pathway == options.get("pathway")]
    meshlab = options.get("meshlab", "none") or "none"
    for preset in same:
        if preset.meshlab() == meshlab:
            return preset
    return same[0] if same else None


#: Options whose 0 is a setting of its own, as their help says.
_ZERO_MEANS = {"voxel2_um": "automatic", "voxel2_faces": "no cap"}


def option_text(f: Field, value: Any) -> str:
    """An option's value as the panel shows it: an enum by its label, a percent
    as one, a blank optional number as "none", numbers without trailing zeros
    and whole counts with thousands separators."""
    if f.choices:
        for choice, label in f.choices:
            if choice == value:
                return str(label)
    if f.key in _ZERO_MEANS and value is not None and float(value) == 0:
        return f"0 ({_ZERO_MEANS[f.key]})"
    if value is None:
        return "none"
    if f.kind == "bool":
        return "on" if value else "off"
    if f.kind == "percent":
        return f"{float(value) * 100:g} %"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, int) or (f.kind == "int" and float(value).is_integer()):
        return f"{int(value):,}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


@dataclass(frozen=True)
class OptionRow:
    """One render option of a set, in words: its label, the value (``text``),
    the technique's default (``default_text``), whether the value is not that
    default (``changed``), and whether the build ignores it here (``inert``,
    with ``why``)."""

    key: str
    label: str
    value: Any
    text: str
    default_text: str
    changed: bool
    inert: bool
    why: str


def describe_options(options: Mapping[str, Any]) -> list[OptionRow]:
    """The geometry options a build with ``options`` shows, in the panel's
    order and words: every option the technique does not hide, against the
    defaults the app starts the technique with (the app's defaults with the
    preset's method, pathway and own settings on top; a person's saved
    technique defaults live in their browser and are not known here).  What
    the report prints for a rendering."""
    full = {**app_defaults(), **dict(options)}
    preset = match_preset(full)
    defaults = {**app_defaults(), **({"method": preset.method, "pathway": preset.pathway,
                                      **preset.set} if preset else {})}
    states = field_state(full)
    rows: list[OptionRow] = []
    for f in FIELDS:
        if f.scope != "geometry" or "body" not in f.wire or states[f.key].hidden:
            continue
        value, default = full.get(f.key), defaults.get(f.key)
        if isinstance(value, float) and isinstance(default, (int, float)):
            changed = abs(float(value) - float(default)) > 1e-12 * max(1.0, abs(float(default)))
        else:
            changed = value != default
        rows.append(OptionRow(
            key=f.key, label=f.label, value=value, text=option_text(f, value),
            default_text=option_text(f, default), changed=bool(changed),
            inert=states[f.key].inert, why=states[f.key].why,
        ))
    return rows


def visibility_cases() -> list[dict[str, Any]]:
    """Every method x pathway x MeshLab mode, with and without MeshLab on the
    server: what :func:`field_state` says, in the form the node tests read."""
    cases: list[dict[str, Any]] = []
    for meshlab_off in (False, True):
        for method, _ in field_by_key("method").choices:
            for pathway, _ in field_by_key("pathway").choices:
                for mode, _ in field_by_key("meshlab").choices:
                    options = {"method": method, "pathway": pathway, "meshlab": mode}
                    states = field_state(options, {"meshlabOff": meshlab_off})
                    cases.append({
                        "options": options,
                        "ctx": {"meshlabOff": meshlab_off},
                        "hidden": [k for k, s in states.items() if s.hidden],
                        "inert": {k: s.why for k, s in states.items() if s.inert},
                        "disabled": {k: s.title for k, s in states.items() if s.disabled},
                    })
    return cases


def fixture_source() -> str:
    """``tests/fixtures/render_visibility.json``."""
    return json.dumps(visibility_cases(), ensure_ascii=False, indent=1) + "\n"


# -- the browser's copy -----------------------------------------------------
def js_source() -> str:
    """``src/sbfsem_web/static/js/render-schema.js``: the schema as data."""
    lines = [
        "// Generated by `python -m sbfsem.service.render_schema > src/sbfsem_web/static/js/render-schema.js`.",
        "// Do not edit by hand: tests/unit/test_render_schema.py asserts it matches the Python",
        "// schema (src/sbfsem/service/render_schema.py), which is where a render option is declared.",
        "//",
        "// Data only: renderoptions.js builds the defaults, bounds and rules from it,",
        "// and renderform.js builds the controls.",
        "",
        "export const RENDER_FIELDS = [",
    ]
    for f in FIELDS:
        lines.append("  " + json.dumps(f.to_js(), ensure_ascii=False) + ",")
    lines += [
        "];",
        "",
        "// Where the controls sit, in DOM order.",
        "export const RENDER_SLOTS = "
        + json.dumps({k: list(v) for k, v in PANEL_SLOTS.items()}, ensure_ascii=False) + ";",
        "",
        "// What a control says when the server lacks the optional feature it needs.",
        "export const RENDER_REQUIRES = " + json.dumps(REQUIRES, ensure_ascii=False) + ";",
        "",
        "// The named techniques, in the order the pickers list them.",
        "export const RENDER_PRESETS = [",
    ]
    for p in PRESETS:
        lines.append("  " + json.dumps(p.to_js(), ensure_ascii=False) + ",")
    lines += ["];"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    # ``python -m sbfsem.service.render_schema`` writes the browser's copy;
    # ``--fixture`` writes the visibility fixture the node tests read.
    sys.stdout.write(fixture_source() if "--fixture" in sys.argv[1:] else js_source())
