"""How far the section correction and the current transforms moved a cell, and whether it helped.

There is no MATLAB counterpart.  A loaded cell's annotations pass through up to
three stages (``Structure.apply_transform`` adds the moves in volume nm to the
stored ``VolumeX``/``VolumeY``, in this order):

============== ==============================================================
stage          where the annotation is
============== ==============================================================
stored         Viking's own stored position (``VolumeX`` x scale)
registered     where the volume's current section transforms put it, which is
               where Viking draws it (stored + ``LiveDXnm``/``LiveDYnm``)
corrected      where Viking's section-correction service puts it, which is the
               cell's ``Xum``/``Yum`` (registered + ``CorrDXnm``/``CorrDYnm``)
============== ==============================================================

A *pair* is the move between two stages: ``correction`` (registered to
corrected, "before and after the corrections service"), ``transforms`` (stored
to registered, the stos edits) and ``both`` (stored to corrected).  Every move
lies in the section plane: there is no Z column, and a section's Z is its
number.

Two questions are answered here, for the viewer's *Position shift* layer
(``/shift-summary``) and for ``tools/section_corrections.py``:

* **How big, and where.**  The sizes of the moves (:func:`pair_stats`) and the
  sections where they agree in one direction (:func:`coherent_sections`): a
  section whose annotations all moved the same way is a section that was
  shifted as a whole, which is what a stos edit or a re-registration looks
  like, against a per-annotation correction, which scatters.
* **Did it help.**  Viking links two annotations of one cell on different
  sections; the sideways step along such a link is the jump a misregistered
  section makes.  :func:`link_stats` gives the step's distribution at each
  stage and :func:`link_verdict` how many links a pair shortened or lengthened.

The stage positions are read back from the cell's own columns (``Xum`` minus
the moves), the same arithmetic the viewer does with a node's ``xyz_um``,
``shift_um`` and ``live_shift_um``, so the layer, this module and the tool
agree.  That holds for the Standard transform, the only one the viewer asks for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "COHERENT_MIN_MEAN_UM",
    "COHERENT_MIN_N",
    "COHERENT_MIN_RESULTANT",
    "LINK_OVER_UM",
    "LINK_TOLERANCE_UM",
    "MOVED_UM",
    "PAIRS",
    "PAIR_STAGES",
    "STAGES",
    "ShiftSummary",
    "coherent_sections",
    "link_stats",
    "link_steps",
    "link_verdict",
    "pair_stats",
    "pair_vectors",
    "shift_summary",
    "stage_frame",
    "steps",
]

#: The three places an annotation can be, in the order they are reached.
STAGES = ("stored", "registered", "corrected")
#: Pair name -> (the stage it starts from, the stage it ends at).
PAIR_STAGES = {
    "correction": ("registered", "corrected"),
    "transforms": ("stored", "registered"),
    "both": ("stored", "corrected"),
}
PAIRS = tuple(PAIR_STAGES)

#: A move smaller than this is not counted as one (0.5 nm; the section
#: correction's own summary uses it).  The current transforms already leave
#: out anything under 10 nm.
MOVED_UM = 0.0005
#: A link step over this is a jump worth counting (the tool's "over 1.5 um").
LINK_OVER_UM = 1.5
#: A link whose step changed by less than this neither shrank nor grew (5 nm).
LINK_TOLERANCE_UM = 0.005
#: A section is coherent when it has at least this many annotations ...
COHERENT_MIN_N = 4
#: ... their moves point one way (resultant length over summed lengths, 1 =
#: all parallel, 0 = they cancel) ...
COHERENT_MIN_RESULTANT = 0.6
#: ... and the mean move, zeros included, is at least this long (um).
COHERENT_MIN_MEAN_UM = 0.2


# -- stage positions ----------------------------------------------------------
def _finite_or_zero(frame: pd.DataFrame, name: str) -> np.ndarray:
    """A move column in um (nm / 1000); a missing column or a NaN is no move."""
    if name not in frame.columns:
        return np.zeros(len(frame))
    values = frame[name].to_numpy(dtype=float)
    return np.where(np.isfinite(values), values, 0.0) / 1000.0


def stage_frame(nodes: pd.DataFrame) -> pd.DataFrame:
    """Each annotation's section, its two moves and its XY at every stage (um).

    Columns ``ID``, ``Z``, ``live_dx_um``, ``live_dy_um``, ``corr_dx_um``,
    ``corr_dy_um`` and ``{stage}_x_um``/``{stage}_y_um`` for each of
    :data:`STAGES`, in ``nodes``' row order.  ``corrected`` is ``Xum``/``Yum``
    less a person's own move (``ManDXnm``/``ManDYnm``, :mod:`sbfsem.core.moves`),
    which comes after the correction; the earlier stages take the moves back
    off it.  A cell that was not drawn through the transforms or the correction
    has no such column: its stages coincide.
    """
    live_x, live_y = _finite_or_zero(nodes, "LiveDXnm"), _finite_or_zero(nodes, "LiveDYnm")
    corr_x, corr_y = _finite_or_zero(nodes, "CorrDXnm"), _finite_or_zero(nodes, "CorrDYnm")
    hand_x, hand_y = _finite_or_zero(nodes, "ManDXnm"), _finite_or_zero(nodes, "ManDYnm")
    cx = nodes["Xum"].to_numpy(dtype=float) - hand_x
    cy = nodes["Yum"].to_numpy(dtype=float) - hand_y
    rx, ry = cx - corr_x, cy - corr_y
    return pd.DataFrame({
        "ID": nodes["ID"].to_numpy(),
        "Z": nodes["Z"].to_numpy(),
        "live_dx_um": live_x, "live_dy_um": live_y,
        "corr_dx_um": corr_x, "corr_dy_um": corr_y,
        "stored_x_um": rx - live_x, "stored_y_um": ry - live_y,
        "registered_x_um": rx, "registered_y_um": ry,
        "corrected_x_um": cx, "corrected_y_um": cy,
    }, index=nodes.index)


def pair_vectors(frame: pd.DataFrame, pair: str) -> np.ndarray:
    """The ``(n, 2)`` move of every annotation across ``pair``, um.

    Read from the move columns, not by subtracting two stages, so the vector
    of a correction is exactly ``CorrDXnm / 1000``.
    """
    if pair not in PAIR_STAGES:
        raise ValueError(f"unknown pair {pair!r}; expected one of {', '.join(PAIRS)}")
    live = frame[["live_dx_um", "live_dy_um"]].to_numpy(dtype=float)
    corr = frame[["corr_dx_um", "corr_dy_um"]].to_numpy(dtype=float)
    return {"correction": corr, "transforms": live, "both": live + corr}[pair]


def pair_stats(frame: pd.DataFrame, pair: str) -> dict:
    """How many annotations moved across ``pair`` and by how much (um).

    The median and percentiles are over the annotations that moved
    (:data:`MOVED_UM`); all 0.0 when none did.
    """
    vectors = pair_vectors(frame, pair)
    magnitude = np.hypot(vectors[:, 0], vectors[:, 1])
    moved = magnitude[magnitude > MOVED_UM]
    out = {"n_annotations": int(len(magnitude)), "n_moved": int(len(moved)),
           "median_um": 0.0, "p95_um": 0.0, "p99_um": 0.0, "max_um": 0.0}
    if len(moved):
        p50, p95, p99 = np.percentile(moved, [50, 95, 99])
        out.update(median_um=float(p50), p95_um=float(p95), p99_um=float(p99),
                   max_um=float(moved.max()))
    return out


# -- sections that moved together ------------------------------------------------
def coherent_sections(frame: pd.DataFrame, pair: str, *, min_n: int = COHERENT_MIN_N,
                      min_resultant: float = COHERENT_MIN_RESULTANT,
                      min_mean_um: float = COHERENT_MIN_MEAN_UM) -> list[dict]:
    """The sections whose annotations moved across ``pair`` as one.

    Per section of ``frame``: ``n`` annotations (moved or not), the mean move
    (``mean_dx_um``, ``mean_dy_um``, its length ``mean_um``, every annotation
    counted) and the resultant ratio ``|sum v| / sum |v|``.  A section is
    listed when ``n >= min_n``, the ratio is at least ``min_resultant`` and the
    mean at least ``min_mean_um``; strongest first (the mean's length, then
    the section number).
    """
    vectors = pair_vectors(frame, pair)
    dx, dy = vectors[:, 0], vectors[:, 1]
    sections, group = np.unique(frame["Z"].to_numpy(), return_inverse=True)
    n = np.bincount(group)
    sum_dx = np.bincount(group, weights=dx)
    sum_dy = np.bincount(group, weights=dy)
    sum_length = np.bincount(group, weights=np.hypot(dx, dy))
    resultant_length = np.hypot(sum_dx, sum_dy)
    # A section where nothing moved has no direction to agree on.
    moved = sum_length > 0
    ratio = np.divide(resultant_length, sum_length, out=np.zeros(len(n)), where=moved)
    mean_um = resultant_length / n
    keep = np.flatnonzero(moved & (n >= min_n) & (ratio >= min_resultant) & (mean_um >= min_mean_um))
    # Strongest first; the section number breaks a tie (``keep`` is already by section).
    keep = keep[np.argsort(-mean_um[keep], kind="stable")]
    return [{"section": int(sections[i]), "n": int(n[i]), "mean_dx_um": float(sum_dx[i] / n[i]),
             "mean_dy_um": float(sum_dy[i] / n[i]), "mean_um": float(mean_um[i]),
             "resultant": float(ratio[i])} for i in keep]


# -- links across sections ---------------------------------------------------------
#: The node columns this module reads (the node table also holds the shapes,
#: which are slow to copy).
_COLUMNS = ("ID", "ParentID", "Z", "Xum", "Yum", "LiveDXnm", "LiveDYnm", "CorrDXnm", "CorrDYnm",
            "ManDXnm", "ManDYnm")


_MOVE_COLUMNS = ("LiveDXnm", "LiveDYnm", "CorrDXnm", "CorrDYnm", "ManDXnm", "ManDYnm")


def moves_apply(neuron) -> bool:
    """Whether the move columns mean what this module says they do.

    They are nanometres in the volume's pixel frame, added to the Standard
    transform's micron positions.  A cell loaded with ``transform="None"``
    keeps raw pixels in ``Xum``/``Yum``, so subtracting a move from them mixes
    units: its stages are one position.
    """
    from sbfsem.core.transforms import Transforms

    return getattr(neuron, "transform", Transforms.Standard) is not Transforms.NoTransform


def _cell(neuron) -> pd.DataFrame:
    """The cell's own annotation rows (no child structures), only the columns read here."""
    nodes = neuron.nodes
    columns = [c for c in _COLUMNS if c in nodes.columns
               and (c not in _MOVE_COLUMNS or moves_apply(neuron))]
    return nodes.loc[nodes["ParentID"] == neuron.id, columns]


def _own(neuron) -> pd.DataFrame:
    """The cell's own annotations, one row per ID, by ID."""
    return _cell(neuron).drop_duplicates("ID").set_index("ID")


def _links_across_sections(neuron, own: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The links between two of ``own`` on different sections.

    ``(A, B, row_a, row_b)``: the two Location IDs of every such link, and
    where each end sits in ``own``, in the order of ``neuron.edges``.
    """
    edges = neuron.edges
    id_a, id_b = edges["A"].to_numpy(), edges["B"].to_numpy()
    row_a, row_b = own.index.get_indexer(id_a), own.index.get_indexer(id_b)
    keep = (row_a >= 0) & (row_b >= 0)
    z = own["Z"].to_numpy()
    keep[keep] = z[row_a[keep]] != z[row_b[keep]]
    return id_a[keep], id_b[keep], row_a[keep], row_b[keep]


def steps(neuron) -> pd.DataFrame:
    """Every link between the cell's own annotations on different sections.

    One row per link: ``A`` and ``B`` (Location IDs), their sections ``zA`` and
    ``zB``, and ``step_um``, the sideways distance between the two ends at the
    cell's current positions (``Xum``/``Yum``).
    """
    own = _own(neuron)
    id_a, id_b, row_a, row_b = _links_across_sections(neuron, own)
    x, y, z = own["Xum"].to_numpy(), own["Yum"].to_numpy(), own["Z"].to_numpy()
    return pd.DataFrame({
        "A": id_a, "B": id_b, "zA": z[row_a], "zB": z[row_b],
        "step_um": np.hypot(x[row_b] - x[row_a], y[row_b] - y[row_a]),
    })


def link_steps(neuron) -> pd.DataFrame:
    """:func:`steps` at every stage: ``stored_um``, ``registered_um`` and ``corrected_um``.

    Links with a non-finite end at any stage are left out, so every column
    describes the same links.
    """
    own = _own(neuron)
    stages = stage_frame(own.reset_index())
    id_a, id_b, row_a, row_b = _links_across_sections(neuron, own)
    z = stages["Z"].to_numpy()
    frame = pd.DataFrame({"A": id_a, "B": id_b, "zA": z[row_a], "zB": z[row_b]})
    for stage in STAGES:
        x, y = stages[f"{stage}_x_um"].to_numpy(), stages[f"{stage}_y_um"].to_numpy()
        frame[f"{stage}_um"] = np.hypot(x[row_b] - x[row_a], y[row_b] - y[row_a])
    finite = np.isfinite(frame[[f"{stage}_um" for stage in STAGES]].to_numpy()).all(axis=1)
    return frame[finite].reset_index(drop=True)


def link_stats(step_um: pd.Series | np.ndarray) -> dict | None:
    """The distribution of link steps (um): median, p90, p99, max and how many pass 1.5 um.

    ``None`` when there are no links.
    """
    s = np.asarray(step_um, dtype=float)
    if not len(s):
        return None
    p50, p90, p99 = np.percentile(s, [50, 90, 99])
    return {"median_um": float(p50), "p90_um": float(p90), "p99_um": float(p99),
            "max_um": float(s.max()), "n_over_1p5_um": int((s > LINK_OVER_UM).sum())}


def link_verdict(links: pd.DataFrame, pair: str) -> dict:
    """How ``pair`` changed the steps along the links: shorter, longer or the same.

    A step within :data:`LINK_TOLERANCE_UM` (5 nm) of what it was is *within*.
    ``share_*`` are fractions of ``n_links`` (0.0 with no links).
    """
    if pair not in PAIR_STAGES:
        raise ValueError(f"unknown pair {pair!r}; expected one of {', '.join(PAIRS)}")
    before_stage, after_stage = PAIR_STAGES[pair]
    before = links[f"{before_stage}_um"].to_numpy(dtype=float)
    after = links[f"{after_stage}_um"].to_numpy(dtype=float)
    n = len(links)
    shrank = int((after < before - LINK_TOLERANCE_UM).sum())
    grew = int((after > before + LINK_TOLERANCE_UM).sum())
    within = n - shrank - grew
    return {"pair": pair, "before": before_stage, "after": after_stage, "n_links": n,
            "n_shrank": shrank, "n_grew": grew, "n_within_5nm": within,
            "share_shrank": shrank / n if n else 0.0, "share_grew": grew / n if n else 0.0,
            "share_within_5nm": within / n if n else 0.0}


# -- the whole report ------------------------------------------------------------------
@dataclass
class ShiftSummary:
    """:func:`shift_summary`'s answer; :meth:`as_dict` is what ``/shift-summary`` sends."""

    neuron_id: int
    source: str
    pair: str
    applied: dict
    pairs: dict
    n_links: int
    stages: dict
    verdict: dict
    coherent_sections: list

    def as_dict(self) -> dict:
        return {
            "neuron_id": self.neuron_id, "source": self.source, "pair": self.pair,
            "applied": self.applied, "pairs": self.pairs,
            "links": {"n_links": self.n_links, "stages": self.stages, "verdict": self.verdict},
            "coherent_sections": self.coherent_sections,
        }


def shift_summary(neuron, pair: str = "correction") -> ShiftSummary:
    """Sizes, link steps and coherent sections of one loaded cell.

    ``pair`` picks the verdict and the coherent sections; the sizes are given
    for all three pairs and the link steps for all three stages.  Only the
    cell's own annotations count, as in the viewer's layer.  A stage that was
    not applied moves nothing, so its pairs read as zero moved.
    """
    if pair not in PAIR_STAGES:
        raise ValueError(f"unknown pair {pair!r}; expected one of {', '.join(PAIRS)}")
    nodes = neuron.nodes
    frame = stage_frame(_cell(neuron))
    links = link_steps(neuron)
    return ShiftSummary(
        neuron_id=int(neuron.id), source=str(neuron.source), pair=pair,
        applied={
            "correction": moves_apply(neuron) and (
                bool(getattr(neuron, "section_correction", None)) or "CorrDXnm" in nodes.columns),
            "transforms": moves_apply(neuron) and (
                bool(getattr(neuron, "live_registration", None)) or "LiveDXnm" in nodes.columns),
        },
        pairs={name: pair_stats(frame, name) for name in PAIRS},
        n_links=len(links),
        stages={stage: link_stats(links[f"{stage}_um"]) for stage in STAGES},
        verdict=link_verdict(links, pair),
        coherent_sections=coherent_sections(frame, pair),
    )
