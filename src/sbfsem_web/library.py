"""Validation for the shared material and gradient libraries.

A library entry is written by one user and rendered by everyone, so the
document is checked field by field here: a material is a bounded set of
physically based shading parameters, a gradient is an ordered list of color
stops.  Anything else is refused with a sentence.
"""

from __future__ import annotations

import re

__all__ = ["MATERIAL_FIELDS", "validate_library_doc"]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

#: name -> (lo, hi, default).  Mirrors ``static/js/materials.js``.
MATERIAL_FIELDS: dict[str, tuple[float, float, float]] = {
    "roughness": (0.0, 1.0, 0.5),
    "metalness": (0.0, 1.0, 0.0),
    "clearcoat": (0.0, 1.0, 0.0),
    "clearcoatRoughness": (0.0, 1.0, 0.3),
    "sheen": (0.0, 1.0, 0.0),
    "sheenRoughness": (0.0, 1.0, 0.8),
    "specularIntensity": (0.0, 2.0, 1.0),
    "emissiveIntensity": (0.0, 1.0, 0.0),
    "envMapIntensity": (0.0, 3.0, 1.0),
    "transmission": (0.0, 1.0, 0.0),
    "ior": (1.0, 2.333, 1.5),
    "iridescence": (0.0, 1.0, 0.0),
}
#: ``emissive`` is not here on purpose: the glow is the cell's own color, set
#: from ``emissiveIntensity`` by the browser, which never sends or reads a color
#: for it (``MATERIAL_COLORS`` in ``static/js/materials.js``).
_MATERIAL_COLORS = {"sheenColor", "specularColor"}
#: Booleans; mirror the checkboxes ``static/js/materialpanel.js`` builds.
_MATERIAL_FLAGS = {"flatShading", "unlit"}

MAX_STOPS = 32


def _clamp(value, lo: float, hi: float, what: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a number") from exc
    if v != v:  # NaN
        raise ValueError(f"{what} must be a number")
    return min(hi, max(lo, v))


def validate_material(doc: dict) -> dict:
    out: dict = {"kind": "material"}
    for key, (lo, hi, _default) in MATERIAL_FIELDS.items():
        if key in doc and doc[key] is not None:
            out[key] = _clamp(doc[key], lo, hi, key)
    for key in _MATERIAL_COLORS:
        if key in doc and doc[key] is not None:
            value = str(doc[key]).strip()
            if not _HEX.match(value):
                raise ValueError(f"{key} must be a #rrggbb color")
            out[key] = value.lower()
    for key in _MATERIAL_FLAGS:
        if key in doc:
            out[key] = bool(doc[key])
    desc = str(doc.get("description", "") or "").strip()
    if len(desc) > 200:
        raise ValueError("description is at most 200 characters")
    if desc:
        out["description"] = desc
    based = str(doc.get("based_on", "") or "").strip()
    if based:
        out["based_on"] = based[:64]
    return out


def validate_gradient(doc: dict) -> dict:
    stops = doc.get("stops")
    if not isinstance(stops, list) or len(stops) < 2:
        raise ValueError("a gradient needs at least two stops")
    if len(stops) > MAX_STOPS:
        raise ValueError(f"a gradient has at most {MAX_STOPS} stops")
    clean: list[tuple[float, str]] = []
    for stop in stops:
        if not isinstance(stop, (list, tuple)) or len(stop) != 2:
            raise ValueError("each stop is [position, '#rrggbb']")
        position = _clamp(stop[0], 0.0, 1.0, "stop position")
        color = str(stop[1]).strip()
        if not _HEX.match(color):
            raise ValueError(f"stop color {color!r} must be #rrggbb")
        clean.append((round(position, 4), color.lower()))
    clean.sort(key=lambda s: s[0])
    # The ends are pinned: a gradient always covers the whole 0-1 range.
    clean[0] = (0.0, clean[0][1])
    clean[-1] = (1.0, clean[-1][1])
    out: dict = {"kind": "gradient", "stops": [[p, c] for p, c in clean]}
    desc = str(doc.get("description", "") or "").strip()
    if len(desc) > 200:
        raise ValueError("description is at most 200 characters")
    if desc:
        out["description"] = desc
    based = str(doc.get("based_on", "") or "").strip()
    if based:
        out["based_on"] = based[:64]
    return out


def validate_library_doc(kind: str, doc) -> dict:
    if not isinstance(doc, dict):
        raise ValueError("the body must be a JSON object")
    if kind == "materials":
        return validate_material(doc)
    if kind == "gradients":
        return validate_gradient(doc)
    raise ValueError(f"no such library: {kind!r}")
