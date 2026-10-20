"""Column schemas for the node / edge / synapse tables.

The MATLAB code built an 11-column positional numeric matrix in
``OData.processLocationData`` and only afterwards attached names.  Everything
here goes straight to a named-column :class:`pandas.DataFrame`.

The column the MATLAB code called ``Geometry`` is Viking's ``TypeCode`` field.
It is named ``TypeCode`` throughout the port; :func:`normalize_nodes` accepts the
old name so legacy JSON exports still load.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "NODE_COLUMNS",
    "EDGE_COLUMNS",
    "SYNAPSE_COLUMNS",
    "TYPECODE_DISC",
    "TYPECODE_CLOSED_CURVE",
    "empty_nodes",
    "empty_edges",
    "nodes_from_odata",
    "edges_from_odata",
    "normalize_nodes",
    "xyz_um",
]

#: Viking ``TypeCode`` values the toolbox understands.
TYPECODE_DISC = 1
TYPECODE_CLOSED_CURVE = 6

NODE_COLUMNS: dict[str, str] = {
    "ID": "int64",
    "ParentID": "int64",
    "VolumeX": "float64",
    "VolumeY": "float64",
    "Z": "int64",
    "Radius": "float64",
    "X": "float64",
    "Y": "float64",
    "OffEdge": "bool",
    "Terminal": "bool",
    "TypeCode": "int64",
}

EDGE_COLUMNS: dict[str, str] = {"ID": "int64", "A": "int64", "B": "int64"}

SYNAPSE_COLUMNS: dict[str, str] = {
    "ID": "int64",
    "ParentID": "int64",
    "TypeID": "int64",
    "Tags": "object",
    "Label": "object",
}


def empty_nodes() -> pd.DataFrame:
    frame = pd.DataFrame({name: pd.Series(dtype=dt) for name, dt in NODE_COLUMNS.items()})
    for extra, dt in (("Xum", "float64"), ("Yum", "float64"), ("Zum", "float64"),
                      ("Rum", "float64"), ("Unique", "bool")):
        frame[extra] = pd.Series(dtype=dt)
    return frame


def empty_edges() -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype=dt) for name, dt in EDGE_COLUMNS.items()})


def nodes_from_odata(rows) -> pd.DataFrame:
    """Build the node table from raw OData ``Locations`` documents."""
    rows = list(rows)
    if not rows:
        return empty_nodes()
    frame = pd.DataFrame(rows)
    return normalize_nodes(frame)


def normalize_nodes(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce an arbitrary node-ish frame onto the canonical schema."""
    frame = frame.copy()
    if "TypeCode" not in frame.columns and "Geometry" in frame.columns:
        frame = frame.rename(columns={"Geometry": "TypeCode"})
    for name, dtype in NODE_COLUMNS.items():
        if name not in frame.columns:
            frame[name] = 0
        if dtype == "bool":
            frame[name] = frame[name].fillna(0).astype(float).astype(bool)
        elif dtype.startswith("int"):
            frame[name] = pd.to_numeric(frame[name], errors="coerce").fillna(0).astype("int64")
        else:
            frame[name] = pd.to_numeric(frame[name], errors="coerce").astype("float64")
    keep = list(NODE_COLUMNS)
    extras = [c for c in frame.columns if c not in keep]
    frame = frame[keep + extras]
    if "Unique" in frame.columns:
        frame["Unique"] = frame["Unique"].fillna(0).astype(float).astype(bool)
    return frame.reset_index(drop=True)


def edges_from_odata(rows, structure_id: int | None = None) -> pd.DataFrame:
    """Build the edge table from raw ``LocationLinks`` documents.

    Rows without both endpoints are dropped rather than raising: a Viking
    deployment that answers an unexpected shape should degrade to "no edges",
    not to a ``KeyError`` halfway through a neuron import.
    """
    rows = [r for r in rows if isinstance(r, dict) and "A" in r and "B" in r]
    if not rows:
        return empty_edges()
    frame = pd.DataFrame(rows)
    if "ID" not in frame.columns:
        frame["ID"] = structure_id if structure_id is not None else 0
    for name, dtype in EDGE_COLUMNS.items():
        frame[name] = pd.to_numeric(frame[name], errors="coerce").fillna(0).astype(dtype)
    return frame[list(EDGE_COLUMNS)].reset_index(drop=True)


def xyz_um(frame: pd.DataFrame) -> np.ndarray:
    """Return the ``(N, 3)`` micron coordinate matrix of a node table."""
    if frame.empty:
        return np.empty((0, 3), dtype=float)
    return frame[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
