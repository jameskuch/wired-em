"""Manual moves: a person's own corrections to where annotations are drawn.

Viking's section correction and the volume's current section transforms move
annotations in their section plane (``sbfsem.io.corrections``,
``sbfsem.io.registration``).  Where a correction leaves an annotation off its
neighbors, the viewer lets a person move it further by hand (asked for
2026-10-19: "allow a secondary subsequent movement of the annotation").  A
manual move is the last stage of the same pipeline:

    stored -> + current transforms -> + section correction -> + manual move

It is X and Y in microns, as the other two stages, plus Z in whole sections:
an annotation is a disc (or an outline) in its section's plane, never between
two (CLAUDE.md ground rule 2), so a Z move puts it on another section.

Nothing here writes to Viking.  A move set is held by the page that made it and
sent with each load of the cell; the service applies it to a copy of the cell
made from the copy loaded with the same registration setting
(``SbfsemService.load_neuron``), so two people, or one person under two
settings, never see each other's moves.  The copy carries the columns
``ManDXnm``/``ManDYnm`` (nanometers, like ``CorrDXnm``) and ``ManDZ``
(sections); :meth:`~sbfsem.core.structure.Structure.apply_transform` adds the
first two after the correction, and ``Z`` itself holds the moved section, so
everything built from the copy -- surfaces, skeletons, exports, measurements --
sees the annotations where they were moved.  Viking's own section is
``Z - ManDZ``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

import numpy as np

__all__ = [
    "MAX_MOVE_UM", "MAX_MOVE_SECTIONS", "MAX_MOVES", "ManualMove", "apply_manual_moves",
    "moved_copy", "moves_token", "normalize_moves",
]

#: The longest X or Y move taken, microns: farther than any registration error
#: seen (Viking's own placement limit is 20 um), short of a typo's thousands.
MAX_MOVE_UM = 100.0
#: The most sections an annotation is moved in Z.
MAX_MOVE_SECTIONS = 50
#: The most moves in one set (one cell, one registration setting).
MAX_MOVES = 20000
#: Moves are kept to 0.1 nm: two sets that differ by less are the same set, so
#: float noise from a page never makes another copy of a cell.
_DECIMALS = 4


class ManualMove(NamedTuple):
    """One annotation's move: its Location ID, X and Y in microns, Z in sections."""

    id: int
    dx_um: float
    dy_um: float
    dz: int


def _number(value: Any, what: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a number, not {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"{what} must be finite, not {value!r}")
    return out


def normalize_moves(raw: Iterable[Mapping[str, Any]] | None) -> tuple[ManualMove, ...]:
    """A move set as the service applies it: checked, rounded, sorted by ID.

    ``raw`` is a list of ``{"id", "dx_um", "dy_um", "dz"}`` (any of the three
    moves may be left out: zero).  A move of nothing is dropped; an ID named
    twice keeps its last move.  Raises ``ValueError`` for a move that is not a
    number, is longer than :data:`MAX_MOVE_UM` or :data:`MAX_MOVE_SECTIONS`,
    a Z move that is not a whole number of sections, or more than
    :data:`MAX_MOVES` moves.
    """
    if raw is None:
        return ()
    by_id: dict[int, ManualMove] = {}
    for count, item in enumerate(raw, start=1):
        if count > MAX_MOVES:
            raise ValueError(f"at most {MAX_MOVES:,} manual moves a cell")
        if not isinstance(item, Mapping):
            raise ValueError(f"a manual move is an object, not {item!r}")
        try:
            location = int(item["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"a manual move needs a Location ID: {item!r}") from exc
        dx = round(_number(item.get("dx_um", 0.0), "dx_um"), _DECIMALS)
        dy = round(_number(item.get("dy_um", 0.0), "dy_um"), _DECIMALS)
        dz_raw = _number(item.get("dz", 0), "dz")
        if dz_raw != int(dz_raw):
            raise ValueError(f"dz is whole sections, not {dz_raw}")
        dz = int(dz_raw)
        if abs(dx) > MAX_MOVE_UM or abs(dy) > MAX_MOVE_UM:
            raise ValueError(f"a manual move is at most {MAX_MOVE_UM:g} um in X and Y "
                             f"(Location {location}: {dx:g}, {dy:g})")
        if abs(dz) > MAX_MOVE_SECTIONS:
            raise ValueError(f"a manual move is at most {MAX_MOVE_SECTIONS} sections in Z "
                             f"(Location {location}: {dz})")
        # -0.0 and 0.0 are one move.
        move = ManualMove(location, dx + 0.0, dy + 0.0, dz)
        if dx == 0.0 and dy == 0.0 and dz == 0:
            by_id.pop(location, None)
        else:
            by_id[location] = move
    return tuple(by_id[k] for k in sorted(by_id))


def moves_token(moves: Iterable[ManualMove]) -> str | None:
    """A short name for a normalized move set (12 hex digits), ``None`` for none.

    It names the copy of the cell the set was applied to (``copy_tag``) and
    joins the mesh key (``positions_token``), so a surface built with one set
    is never served for another.
    """
    items = [[m.id, m.dx_um, m.dy_um, m.dz] for m in moves]
    if not items:
        return None
    text = json.dumps(items, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def apply_manual_moves(neuron, moves: tuple[ManualMove, ...]) -> dict:
    """Move ``neuron``'s own annotations by ``moves``, in place; the summary.

    Only the cell's own annotations (``ParentID`` is the cell) move: a move
    naming a synapse's annotation, or an ID the cell does not have, is counted
    as unknown and left out.  Sets ``ManDXnm``/``ManDYnm``/``ManDZ`` on every
    node row (zero elsewhere), moves ``Z`` by the sections, recomputes the
    micron columns, and moves each closed-curve outline with its annotation.
    Run it on a copy (:func:`moved_copy`): a second call would move the
    outlines twice.
    """
    if getattr(neuron, "manual_moves", None):
        raise ValueError(f"c{neuron.id} already carries manual moves")
    nodes = neuron.nodes
    own = nodes["ParentID"].astype(np.int64) == int(neuron.id) if "ParentID" in nodes.columns \
        else np.ones(len(nodes), dtype=bool)
    ids = nodes["ID"].astype(np.int64).to_numpy()
    own_ids = set(ids[np.asarray(own)].tolist())
    applied = [m for m in moves if m.id in own_ids]
    unknown = [m.id for m in moves if m.id not in own_ids]
    by_id = {m.id: m for m in applied}
    dx_nm = np.array([by_id[i].dx_um * 1000.0 if i in by_id else 0.0 for i in ids], dtype=float)
    dy_nm = np.array([by_id[i].dy_um * 1000.0 if i in by_id else 0.0 for i in ids], dtype=float)
    dz = np.array([by_id[i].dz if i in by_id else 0 for i in ids], dtype=np.int64)
    nodes["ManDXnm"] = dx_nm
    nodes["ManDYnm"] = dy_nm
    nodes["ManDZ"] = dz
    if dz.any():
        nodes["Z"] = (nodes["Z"].to_numpy() + dz).astype(nodes["Z"].dtype)
    neuron.apply_transform()

    thickness_um = float(neuron.volume_scale[2]) / 1000.0 if neuron.volume_scale[2] > 1 \
        else float(neuron.volume_scale[2])
    geometries = getattr(neuron, "geometries", None)
    if geometries is not None and len(geometries) and by_id:
        frame = geometries.copy()
        curves, zs, zums = [], [], []
        has_z = "Z" in frame.columns
        has_zum = "Zum" in frame.columns
        for row in frame.itertuples():
            rings = row.Curve or []
            move = by_id.get(int(row.ID))
            z: Any = getattr(row, "Z", None)
            zum: Any = getattr(row, "Zum", None)
            if move is not None:
                shift = np.array([move.dx_um, move.dy_um])
                rings = [np.asarray(c, dtype=float) + shift for c in rings]
                if move.dz and z is not None:
                    z = int(z) + move.dz
                if move.dz and zum is not None:
                    zum = float(zum) + move.dz * thickness_um
            curves.append(rings)
            zs.append(z)
            zums.append(zum)
        frame["Curve"] = curves
        if has_z:
            frame["Z"] = zs
        if has_zum:
            frame["Zum"] = zums
        neuron.geometries = frame

    lengths = [math.hypot(m.dx_um, m.dy_um) for m in applied]
    summary = {
        "applied": True,
        "token": moves_token(moves),
        "n_moves": len(moves),
        "n_moved": len(applied),
        "n_unknown": len(unknown),
        "unknown": unknown[:20],
        "n_z": sum(1 for m in applied if m.dz),
        "max_um": max(lengths, default=0.0),
    }
    neuron.manual_moves = summary
    return summary


def moved_copy(neuron, moves: tuple[ManualMove, ...]):
    """A copy of ``neuron`` with ``moves`` applied; ``neuron`` is untouched.

    Shallow, like the service's other views of a cell (``_disc_subset``): its
    own node and outline tables, a fresh ``analysis`` and no model, the rest
    (links, synapse table, Viking's row) shared and read only.
    """
    out = copy.copy(neuron)
    out.nodes = neuron.nodes.copy()
    geometries = getattr(neuron, "geometries", None)
    out.geometries = geometries.copy() if geometries is not None else None
    out.analysis = {}
    out.model = None
    out.manual_moves = None
    apply_manual_moves(out, moves)
    return out
