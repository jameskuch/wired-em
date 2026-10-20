"""Cone outlines: splined closed-curve traces of cone pedicles.

Ports ``+sbfsem/+image/ConeOutline.m`` and ``+sbfsem/+image/ConeTrace.m``.

The MATLAB class had four independent constructor defects and did not load at
all (F164):

* ``addParameter('FaceAlpha', 1, @isnumeric)`` was missing the parser handle;
* the 3-D branch referenced an undefined ``z``;
* ``T(T.Z == Z)`` indexed a table with one subscript where two were needed;
* ``isinteger(neuron)`` is false for a MATLAB double literal, so the documented
  ``ConeOutline(2542)`` form never took the "load this neuron" path.

It also closed its Catmull-Rom loop by appending the **last** three control
points (``outline(end-2:end,:)``); ``ClosedCurve.getSpline`` and
``util/renders/getSpline.m`` both append the **first** three, which is what
actually closes the loop (F165).  This module uses
:func:`sbfsem.geometry.spline.close_spline`, so all three agree.

The context menu and DAE export callbacks were tied to MATLAB figure handles;
the equivalent lives in the web frontend, and :func:`cone_outline_mesh` gives the
exporters a real mesh to work with.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .closed_curve import repair_leading_nan
from .mesh import EMPTY_MESH, Mesh
from .rasterize import rasterize_rings
from .spline import DEFAULT_POINT_BUDGET, close_spline

__all__ = ["ConeOutline", "cone_outlines_from_neuron", "cone_outline_mesh"]


@dataclass
class ConeOutline:
    """A single splined cone outline at one section."""

    outline: np.ndarray
    cutouts: list[np.ndarray]
    z_section: int
    z_um: float
    parent_id: int | None = None
    label: str = ""
    #: The Viking Location ID of the annotation the outline was traced as.
    location_id: int | None = None
    #: Viking's registered, already-evaluated curve: drawn as it is, not
    #: splined again (see ``ClosedCurveAnnotation.registered``).
    registered: bool = False

    def __post_init__(self) -> None:
        # Viking occasionally emits a NaN first control point; repair it here so
        # every downstream consumer sees finite coordinates.
        self.outline = repair_leading_nan(self.outline)
        self.cutouts = [repair_leading_nan(c) for c in self.cutouts]

    def spline(self, n: int | None = None, *, budget: int = DEFAULT_POINT_BUDGET) -> np.ndarray:
        """The outline as a closed Catmull-Rom curve, ``(M, 2)``.

        ``n`` is samples per control-point interval; leave it ``None`` to spend a
        total ``budget`` across the outline.
        """
        if self.registered or len(self.outline) < 3:
            # F158: MATLAB indexed x0(1:3) unconditionally.
            return self.outline
        return close_spline(self.outline, n, budget=budget)

    def rings(self, n: int | None = None, *, budget: int = DEFAULT_POINT_BUDGET) -> list[np.ndarray]:
        out = [self.spline(n, budget=budget)]
        for cut in self.cutouts:
            spline_it = not self.registered and len(cut) >= 3
            out.append(close_spline(cut, n, budget=budget) if spline_it else cut)
        return out

    @property
    def area_um2(self) -> float:
        """Enclosed area by the shoelace formula, minus any cut-outs."""
        rings = self.rings()
        area = _polygon_area(rings[0])
        for hole in rings[1:]:
            area -= _polygon_area(hole)
        return float(max(area, 0.0))

    @property
    def equivalent_diameter_um(self) -> float:
        return float(2.0 * np.sqrt(self.area_um2 / np.pi)) if self.area_um2 > 0 else 0.0

    @property
    def centroid_um(self) -> np.ndarray:
        curve = self.spline()
        return curve.mean(axis=0) if len(curve) else np.zeros(2)

    def mask(self, shape, *, pixel_size_um: float = 0.05, origin=None) -> np.ndarray:  # noqa: D401
        """Rasterize the outline (minus cut-outs) onto a pixel grid."""
        origin = np.zeros(2) if origin is None else np.asarray(origin, dtype=float)
        rings = [
            np.column_stack([
                (r[:, 0] - origin[0]) / pixel_size_um,
                (r[:, 1] - origin[1]) / pixel_size_um,
            ])
            for r in self.rings()
        ]
        return rasterize_rings(rings, tuple(shape))


def cone_outlines_from_neuron(neuron, *, z_section: int | None = None) -> list[ConeOutline]:
    """Build cone outlines from a neuron's ``geometries`` table.

    ``z_section`` selects a single section; without it every closed-curve
    annotation is returned, rather than the MATLAB warning-and-give-up path.
    """
    geometries = getattr(neuron, "geometries", None)
    if geometries is None or len(geometries) == 0:
        return []
    frame = geometries
    if z_section is not None:
        frame = frame[frame["Z"] == int(z_section)]
    out: list[ConeOutline] = []
    for row in frame.itertuples():
        curves = list(row.Curve) if row.Curve is not None else []
        if not curves:
            continue
        out.append(
            ConeOutline(
                outline=curves[0],
                cutouts=[np.asarray(c) for c in curves[1:]],
                z_section=int(row.Z),
                z_um=float(getattr(row, "Zum", 0.0)),
                parent_id=int(getattr(row, "ParentID", 0)),
                label=getattr(neuron, "label", ""),
                location_id=int(getattr(row, "ID", 0)),
                registered=bool(getattr(row, "Registered", False)),
            )
        )
    return out


#: A slab's thickness when nothing says what a section is: MATLAB's assumed
#: 90 nm, which is Inferior Monkey's and not RC1's or RC2's 70 nm.
DEFAULT_SLAB_UM = 0.09


def section_thickness_um(outlines) -> float | None:
    """One section's thickness in microns, from the outlines' own ``z_um`` over
    their section number (the loader sets ``Zum = Z * thickness``); ``None``
    when no outline sits above section 0."""
    ratios = [o.z_um / o.z_section for o in outlines if o.z_section > 0 and o.z_um > 0]
    return float(np.median(ratios)) if ratios else None


def cone_outline_mesh(
    outlines, *, thickness_um: float | None = None, budget: int = 200
) -> Mesh:
    """A flat, capped slab mesh through each outline, for 3D scenes and export.

    A slab is one section thick -- the volume's, which the outlines carry in
    their Z (70 nm for RC1 and RC2, where it used to be MATLAB's 90) -- unless
    ``thickness_um`` says otherwise.  ``budget`` is the spline sample count per
    ring.  Each sample becomes cap triangles and wall quads, so this is the
    knob that decides whether a forty-section pedicle is a 90k-face mesh or a
    700k-face one; 200 is already smoother than the section spacing justifies.
    """
    outlines = list(outlines)
    if not outlines:
        return EMPTY_MESH
    if thickness_um is None:
        thickness_um = section_thickness_um(outlines) or DEFAULT_SLAB_UM
    meshes = []
    for outline in outlines:
        rings = outline.rings(budget=budget)
        if len(rings[0]) < 3:
            continue
        half = thickness_um / 2.0
        holes = [r for r in rings[1:] if len(r) >= 3]
        meshes.append(
            _extruded_polygon(rings[0], holes, outline.z_um - half, outline.z_um + half)
        )
    if not meshes:
        return EMPTY_MESH
    from .mesh import concatenate

    return concatenate(meshes).fix_winding()


def _extruded_polygon(curve: np.ndarray, holes, z_low: float, z_high: float) -> Mesh:
    """Triangulate a closed polygon with holes and extrude it between two Z planes."""
    import trimesh
    from shapely.geometry import Polygon

    polygon = Polygon(curve, [np.asarray(h, dtype=float) for h in holes])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty:
        return EMPTY_MESH
    solid = trimesh.creation.extrude_polygon(polygon, height=z_high - z_low)
    vertices = np.asarray(solid.vertices, dtype=float)
    vertices[:, 2] += z_low
    return Mesh(vertices, np.asarray(solid.faces))


def _polygon_area(points: np.ndarray) -> float:
    if len(points) < 3:
        return 0.0
    x, y = points[:, 0], points[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)
