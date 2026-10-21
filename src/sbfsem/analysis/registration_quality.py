"""How a registration treats what each section image shows.

A registration of a cell -- its annotations at their stored positions, or through
the volume's current section transforms, then moved by Viking's section
correction or not -- moves annotations sideways, section by section.  What it
should take out is the misregistration of the sections.  What it should leave
alone is the picture each annotation was drawn on: annotations on one section
were drawn on one image, so their offsets from each other, and from the synapses
drawn on the same image, are what that image shows.

The link steps of :mod:`sbfsem.analysis.shifts` say how smoothly a cell runs from
section to section, and the curve fit makes them small by construction (it moves
each annotation onto the path through its neighbors), so they cannot rank it
against a registration that moves whole sections.  These two measures can, as
the cost side of the trade:

* :func:`section_distortion` -- pairs of the cell's own annotations on one section
  within :data:`RADIUS_UM` of each other (at their stored positions): how much the
  registration changed the offset between the two, ``|m_i - m_j|`` for the two
  moves ``m``.  A section moved as a whole (the transforms, the residual field,
  which is smooth on a 2 µm grid) changes it little; a move per annotation (the
  curve fit) changes it by the difference of the two moves.
* :func:`synapse_drift` -- each synapse of the cell (a child structure's
  annotation) against the nearest of the cell's own annotations on its section
  within :data:`RADIUS_UM`: how much the registration changed their offset.  A
  synapse is drawn on the cell's membrane; the curve fit moves the cell and
  leaves its synapses where they were (on Inferior c1178, 2,178 of its 2,436
  annotations and 2 of its 385 synapses).

Moves are the transforms' plus the correction's (``LiveD*``, ``CorrD*``); a
person's own moves (``ManD*``) are not counted.  Pure: a loaded cell in, numbers
out (``registration_quality`` in the facade serves them, and the Render compare
report's registrations page prints them).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .shifts import moves_apply

__all__ = [
    "OVER_UM",
    "RADIUS_UM",
    "offset_stats",
    "section_distortion",
    "synapse_drift",
]

#: Two annotations (or a synapse and an annotation) this close on one section (µm,
#: at their stored positions) are held to each other: near enough that one image
#: shows both, and a registration that moves sections as a whole moves them alike.
RADIUS_UM = 5.0
#: A change of an offset larger than this is counted (µm): a quarter of a micron,
#: about the radius of a thin dendrite.
OVER_UM = 0.25


def _moves(frame: pd.DataFrame, apply: bool) -> np.ndarray:
    """The ``(n, 2)`` registration move of each row (µm): transforms plus correction."""
    out = np.zeros((len(frame), 2))
    if not apply:
        return out
    for k, (live, corr) in enumerate((("LiveDXnm", "CorrDXnm"), ("LiveDYnm", "CorrDYnm"))):
        for name in (live, corr):
            if name in frame.columns:
                values = frame[name].to_numpy(dtype=float)
                out[:, k] += np.where(np.isfinite(values), values, 0.0) / 1000.0
    return out


def _stored(frame: pd.DataFrame, moves: np.ndarray, apply: bool) -> np.ndarray:
    """The ``(n, 2)`` stored position of each row (µm): where it is, less its moves."""
    xy = frame[["Xum", "Yum"]].to_numpy(dtype=float)
    if apply:
        for k, name in enumerate(("ManDXnm", "ManDYnm")):
            if name in frame.columns:
                values = frame[name].to_numpy(dtype=float)
                xy[:, k] -= np.where(np.isfinite(values), values, 0.0) / 1000.0
    return xy - moves


def offset_stats(change_um: np.ndarray, over_um: float = OVER_UM) -> dict:
    """How much offsets changed (µm): how many, median, 95th percentile, largest, and
    how many by more than ``over_um``; zeros for none."""
    c = np.asarray(change_um, dtype=float)
    c = c[np.isfinite(c)]
    if not len(c):
        return {"n": 0, "median_um": 0.0, "p95_um": 0.0, "max_um": 0.0, "n_over": 0, "share_over": 0.0}
    p50, p95 = np.percentile(c, [50, 95])
    over = int((c > over_um).sum())
    return {"n": int(len(c)), "median_um": float(p50), "p95_um": float(p95), "max_um": float(c.max()),
            "n_over": over, "share_over": over / len(c)}


def _rows(neuron) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The cell's own annotations and its child structures' (synapses), one row per Location."""
    nodes = neuron.nodes.drop_duplicates("ID")
    finite = np.isfinite(nodes[["Xum", "Yum"]].to_numpy(dtype=float)).all(axis=1)
    nodes = nodes[finite]
    own = nodes["ParentID"].to_numpy() == neuron.id
    return nodes[own], nodes[~own]


def section_distortion(neuron, radius_um: float = RADIUS_UM, over_um: float = OVER_UM) -> dict:
    """How much the registration changed the offset between two of the cell's own
    annotations on one section within ``radius_um`` of each other (µm): see the
    module's note.  :func:`offset_stats` over every such pair."""
    own, _ = _rows(neuron)
    apply = moves_apply(neuron)
    moves = _moves(own, apply)
    stored = _stored(own, moves, apply)
    z = own["Z"].to_numpy()
    changes: list[np.ndarray] = []
    for section in np.unique(z):
        at = np.flatnonzero(z == section)
        if len(at) < 2:
            continue
        pairs = cKDTree(stored[at]).query_pairs(radius_um, output_type="ndarray")
        if len(pairs):
            i, j = at[pairs[:, 0]], at[pairs[:, 1]]
            changes.append(np.hypot(*(moves[i] - moves[j]).T))
    return offset_stats(np.concatenate(changes) if changes else np.empty(0), over_um)


def synapse_drift(neuron, radius_um: float = RADIUS_UM, over_um: float = OVER_UM) -> dict:
    """How much the registration changed the offset between each synapse of the cell
    and the nearest of its own annotations on the synapse's section within
    ``radius_um`` (µm): see the module's note.  :func:`offset_stats` over the synapses
    that have one."""
    own, kids = _rows(neuron)
    apply = moves_apply(neuron)
    own_moves, kid_moves = _moves(own, apply), _moves(kids, apply)
    own_xy, kid_xy = _stored(own, own_moves, apply), _stored(kids, kid_moves, apply)
    own_z, kid_z = own["Z"].to_numpy(), kids["Z"].to_numpy()
    changes: list[np.ndarray] = []
    for section in np.unique(kid_z):
        mine = np.flatnonzero(own_z == section)
        theirs = np.flatnonzero(kid_z == section)
        if not len(mine):
            continue
        distance, nearest = cKDTree(own_xy[mine]).query(kid_xy[theirs], distance_upper_bound=radius_um)
        found = np.isfinite(distance)
        if found.any():
            k, o = theirs[found], mine[nearest[found]]
            changes.append(np.hypot(*(kid_moves[k] - own_moves[o]).T))
    return offset_stats(np.concatenate(changes) if changes else np.empty(0), over_um)
