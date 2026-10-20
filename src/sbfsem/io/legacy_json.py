"""Reader for JSON files written by the MATLAB toolbox.

Ports the read half of ``NeuronJSON.m`` and ``+sbfsem/+io/JSON.m``.

The MATLAB format is shaped by jsonlab's nesting quirks: ``nodes``, ``edges``,
``synapses`` and ``geometries`` each arrive wrapped in one or two extra list
levels (hence ``vertcat(S.nodes{:}{:})`` on read), numeric arrays may be encoded
as ``{"_ArrayType_": ..., "_ArrayData_": ...}`` objects, and ``LocalName`` is a
cell array of char.  ``NeuronJSON`` used ``arrayfun`` over that cell column and so
could not load any neuron that had synapses (F013).

Stored ``XYZum``/``Rum`` values are **recomputed** by default.  Older exports
were produced when the micron transform read Viking's ``X``/``Y`` columns, which
Viking leaves at zero for child structures — every synapse in ``i943.json`` has
``XYZum = [0, 0, z]``.  Pass ``trust_stored_um=True`` to keep the file's values
verbatim for a byte-faithful round trip.  See :mod:`sbfsem.export.json_v2` for the
current schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.frames import edges_from_odata, normalize_nodes
from ..core.structure import Neuron
from ..core.transforms import Transforms
from ..core.volumes import validate_source

__all__ = ["read_legacy_json", "unwrap"]

#: ``JSON.export`` named files ``<volumeAbbrev><ID>.json``.
_FILENAME = re.compile(r"^(?P<abbrev>[a-zA-Z]+)(?P<id>\d+)$")


def unwrap(value):
    """Undo jsonlab's extra nesting and array-object encoding."""
    if isinstance(value, dict):
        if "_ArrayData_" in value:
            data = value.get("_ArrayData_")
            if data is None:
                return []
            return data
        return value
    if isinstance(value, list):
        # jsonlab wraps a struct array as [[{...}, {...}]] or [[[{...}]]]
        while len(value) == 1 and isinstance(value[0], list):
            value = value[0]
        return value
    return value


def _records(value) -> list[dict]:
    rows = unwrap(value)
    if rows is None:
        return []
    if isinstance(rows, dict):
        return [rows]
    out: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, list):
            out.extend(r for r in row if isinstance(r, dict))
    return out


def read_legacy_json(path: str | Path, *, trust_stored_um: bool = False) -> Neuron:
    """Load a MATLAB-era neuron export.

    Parameters
    ----------
    trust_stored_um:
        Keep the file's ``XYZum``/``Rum`` columns instead of re-deriving them
        from ``VolumeX``/``VolumeY``.  Only useful for round-trip checks; the
        stored values are zero in X and Y for child structures.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    # dict.get's default is evaluated eagerly, so the filename fallbacks are
    # taken only when the payload really is missing the field.
    structure_id = payload.get("ID")
    structure_id = int(structure_id) if structure_id is not None else _id_from_filename(path)
    source = payload.get("source")
    source = validate_source(source) if source else _source_from_filename(path)

    node_rows = _records(payload.get("nodes"))
    nodes = pd.DataFrame(node_rows) if node_rows else pd.DataFrame()
    if not nodes.empty and "XYZum" in nodes.columns:
        if trust_stored_um:
            xyz = np.asarray([list(v) for v in nodes["XYZum"]], dtype=float)
            nodes["Xum"], nodes["Yum"], nodes["Zum"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        nodes = nodes.drop(columns=["XYZum"])
    if not trust_stored_um and not nodes.empty:
        nodes = nodes.drop(columns=["Rum"], errors="ignore")
    nodes = normalize_nodes(nodes) if not nodes.empty else nodes

    edge_rows = _records(payload.get("edges"))
    edges = edges_from_odata(edge_rows, structure_id)

    synapse_rows = _records(payload.get("synapses"))
    synapses = pd.DataFrame(synapse_rows) if synapse_rows else pd.DataFrame()

    scale = payload.get("volumeScale")
    volume_scale = tuple(float(v) for v in unwrap(scale)) if scale else None

    omitted = unwrap(payload.get("omittedIDs")) or []
    omitted = [int(v) for v in np.ravel(np.asarray(omitted, dtype=float))] if len(omitted) else []

    neuron = Neuron(
        structure_id,
        source,
        viking=payload.get("viking") or {},
        nodes=nodes if not nodes.empty else None,
        edges=edges,
        volume_scale=volume_scale,
        transform=_legacy_transform(payload.get("transform")),
        omitted_ids=omitted,
    )
    if not synapses.empty:
        neuron.synapses = synapses
        neuron.setup_synapses()

    geometry_rows = _records(payload.get("geometries"))
    if geometry_rows:
        neuron.geometries = pd.DataFrame(geometry_rows)
    return neuron


def _legacy_transform(value) -> Transforms:
    """Map retired transform names onto the current enumeration."""
    key = str(value or "").strip().lower()
    if key in ("sbfsemtools", "local", "custom"):
        return Transforms.Custom
    if key in ("viking", "standard"):
        return Transforms.Standard
    return Transforms.NoTransform


def _id_from_filename(path: Path) -> int:
    match = _FILENAME.match(path.stem)
    if not match:
        raise ValueError(f"Cannot infer structure ID from {path.name}")
    return int(match.group("id"))


def _source_from_filename(path: Path) -> str:
    match = _FILENAME.match(path.stem)
    if not match:
        raise ValueError(f"Cannot infer volume from {path.name}")
    return validate_source(match.group("abbrev"))
