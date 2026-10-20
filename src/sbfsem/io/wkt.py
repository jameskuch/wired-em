"""Well-known-text parsing for Viking ClosedCurve geometries.

Replaces the hand-rolled string surgery in
``+sbfsem/+io/GeometryOData.parseClosedCurve`` and ``util/import/parseClosedCurve.m``,
which disagreed with each other: the class version sliced ``str(3:end)`` and left
the trailing parentheses in place, then indexed a char array as a cell (F024, F025).

Viking sends ``CURVEPOLYGON(...)`` / ``POLYGON(...)`` strings.  The first ring is
the outer boundary; any further rings are cut-outs (holes).

WKT rings are explicitly closed — the last coordinate repeats the first — but the
*control points* of a closed curve are the distinct vertices.  The duplicate is
dropped, matching the MATLAB parser, which stopped one short of the end.  Leaving
it in would give ``close_spline`` a doubled knot and a flat spot in the curve.
"""

from __future__ import annotations

import re

import numpy as np
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Polygon

__all__ = [
    "parse_closed_curve",
    "rings_in_volume_frame",
    "volume_frame_shift_um",
    "ClosedCurveRings",
    "linestring_endpoints",
]

#: Result type: a list of ``(N, 2)`` rings, outer boundary first.
ClosedCurveRings = list[np.ndarray]

_RING_RE = re.compile(r"\(([^()]*)\)")


def parse_closed_curve(text: str, volume_scale=None) -> ClosedCurveRings:
    """Parse a Viking WKT geometry into rings of control points.

    Parameters
    ----------
    text:
        A shape's ``Geometry.WellKnownText`` string from OData: ``VolumeShape``
        (registered) or ``MosaicShape`` (the section's own frame);
        :func:`rings_in_volume_frame` chooses and shifts.
    volume_scale:
        Optional ``(x, y, z)`` scale.  Values greater than 1 are treated as nm
        and converted to microns, matching the MATLAB behavior.  When omitted
        the points stay in Viking pixels.

    Returns
    -------
    list of ``(N, 2)`` float arrays, outer ring first, then any cut-outs.
    """
    if text is None:
        return []
    rings = _parse_rings(str(text))
    if volume_scale is None:
        return rings
    vs = np.asarray(volume_scale, dtype=float)
    if vs.max() > 1:  # nm -> um
        vs = vs / 1e3
    return [r * vs[:2] for r in rings]


def volume_frame_shift_um(row, volume_scale) -> np.ndarray:
    """Microns to add to a Location's ``MosaicShape`` rings so they sit beside
    its disc neighbours.

    Viking serves two frames.  ``MosaicShape`` and the Location's own ``X``,
    ``Y`` are in the section's mosaic; ``VolumeX``/``VolumeY`` are the
    registered volume frame that every disc centre (and so every mesh) uses.
    The two differ by that section's registration, which across one
    annotation is a translation: on InferiorMonkey c1178 the frames sit 15 to
    20 um apart, and the translated outline lies within 0.15 um (median,
    0.37 um worst) of Viking's own registered ``VolumeShape``.  Rendering
    the mosaic rings unshifted put that cell's soma 18 um from its dendrites.

    A row missing either pair, or holding non-finite values, shifts by zero.
    """
    try:
        vol = np.array([row["VolumeX"], row["VolumeY"]], dtype=float)
        mos = np.array([row["X"], row["Y"]], dtype=float)
    except (KeyError, TypeError, ValueError):
        return np.zeros(2)
    if not (np.isfinite(vol).all() and np.isfinite(mos).all()):
        return np.zeros(2)
    vs = np.asarray(volume_scale, dtype=float)
    if vs.max() > 1:  # nm -> um
        vs = vs / 1e3
    return (vol - mos) * vs[:2]


def rings_in_volume_frame(row, volume_scale) -> tuple[ClosedCurveRings, bool]:
    """A Location's closed-curve rings in microns, in the registered volume frame.

    Returns ``(rings, registered)``.  Viking's ``VolumeShape`` is the outline
    registered vertex by vertex, and it is Viking's own evaluated curve rather
    than the control polygon (about nine points per control interval on
    Inferior Monkey), so ``registered`` is ``True`` and consumers draw it as
    it is instead of splining it again.  Without a ``VolumeShape`` the
    ``MosaicShape`` control points are translated by
    :func:`volume_frame_shift_um` (``registered`` ``False``): on Inferior
    Monkey that lies within 0.5 um of the registered polygon, but RC1 and RC2
    registrations also rotate and scale, and there a large outline can miss
    by several microns.  ``([], False)`` when the row carries no shape.
    """
    text = _shape_text(row, "VolumeShape")
    if text:
        rings = parse_closed_curve(text, volume_scale)
        if rings:
            return rings, True
    text = _shape_text(row, "MosaicShape")
    if not text:
        return [], False
    shift = volume_frame_shift_um(row, volume_scale)
    return [ring + shift for ring in parse_closed_curve(text, volume_scale)], False


def _shape_text(row, key: str) -> str | None:
    """``row[key].Geometry.WellKnownText`` when every level is there."""
    shape = row.get(key) if isinstance(row, dict) else None
    geometry = shape.get("Geometry") if isinstance(shape, dict) else None
    text = geometry.get("WellKnownText") if isinstance(geometry, dict) else None
    return str(text) if text else None


def _parse_rings(text: str) -> ClosedCurveRings:
    text = text.strip()
    # shapely understands POLYGON/MULTIPOLYGON; CURVEPOLYGON it does not, and
    # Viking's curve rings are just point lists, so treat them the same way.
    upper = text.upper()
    if upper.startswith(("POLYGON", "MULTIPOLYGON")):
        try:
            geom = shapely_wkt.loads(text)
        except Exception:  # noqa: BLE001 - fall through to the manual parser
            geom = None
        if geom is not None:
            return _rings_from_shapely(geom)
    return _parse_rings_manual(text)


def _rings_from_shapely(geom) -> ClosedCurveRings:
    rings: ClosedCurveRings = []
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    for poly in polys:
        if not isinstance(poly, Polygon) or poly.is_empty:
            continue
        rings.append(_open_ring(np.asarray(poly.exterior.coords, dtype=float)[:, :2]))
        for interior in poly.interiors:
            rings.append(_open_ring(np.asarray(interior.coords, dtype=float)[:, :2]))
    return rings


def _open_ring(points: np.ndarray) -> np.ndarray:
    """Drop a trailing coordinate that just repeats the first."""
    if len(points) > 1 and np.allclose(points[0], points[-1]):
        return points[:-1]
    return points


def _parse_rings_manual(text: str) -> ClosedCurveRings:
    """Fallback for ``CURVEPOLYGON`` and other non-OGC spellings."""
    body = text[text.find("(") :] if "(" in text else text
    groups = _RING_RE.findall(body)
    if not groups:
        groups = [body.strip("()")]
    rings: ClosedCurveRings = []
    for group in groups:
        points = _parse_points(group)
        if points.size:
            rings.append(_open_ring(points))
    return rings


def _parse_points(text: str) -> np.ndarray:
    coords: list[tuple[float, float]] = []
    for part in text.split(","):
        fields = part.split()
        if len(fields) < 2:
            continue
        try:
            coords.append((float(fields[0]), float(fields[1])))
        except ValueError:
            continue
    if not coords:
        return np.empty((0, 2), dtype=float)
    return np.asarray(coords, dtype=float)


def linestring_endpoints(shape) -> np.ndarray | None:
    """First and last XY of a Viking ``LINESTRING`` shape column.

    Accepts the nested ``{"Geometry": {"WellKnownText": ...}}`` dict the OData
    service returns, a bare WKT string, or ``None``.  Returns ``(2, 2)`` or
    ``None`` when there is no usable geometry -- callers treat that as "no
    orientation available" rather than an error, because plenty of annotations
    legitimately have none.
    """
    if shape is None:
        return None
    text = shape
    if isinstance(shape, dict):
        text = (shape.get("Geometry") or {}).get("WellKnownText")
    if not isinstance(text, str) or "LINESTRING" not in text.upper():
        return None
    # ``_parse_points`` takes the bare coordinate list; its other callers strip
    # the type keyword and parentheses first, so do the same here.
    body = re.search(r"\(([^()]*)\)", text)
    if body is None:
        return None
    points = _parse_points(body.group(1))
    if len(points) < 2:
        return None
    return np.asarray([points[0, :2], points[-1, :2]], dtype=float)
