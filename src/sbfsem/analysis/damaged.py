"""Where a cell crosses its volume's damaged sections.

There is no MATLAB counterpart.  A damaged section is one a volume's
slice-to-volume registration was built without (``NonStosSectionNumbers`` in
its VikingXML).  Viking's section-correction service lists a set's damaged
sections in ``CorrectionSet.damaged_z`` (service 1.6.0 on; RC1's from 1.7.0):
none of them corrects another section, and each gets a shift of its own,
fitted against the corrected sections either side.  What an annotation on one
shows is less sure than on its neighbors, so the viewer marks them on request.

A cell *crosses* a damaged section where it has an annotation on it, or where
a link between two of its annotations skips over it: InferiorMonkey's 916 to
935 hold no annotation at all, and its cells cross them by links of twenty
sections.  Only the cell's own annotations count, not its child structures'.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

import numpy as np

__all__ = ["DamagedCrossing", "DamagedMark", "crossings", "find"]

#: Decimals of a mark's position and radius (um): a tenth of a nanometre, the
#: mesh JSON's step.
_DECIMALS = 4


@dataclass(frozen=True)
class DamagedCrossing:
    """One damaged section the cell crosses."""

    #: The section number (Viking Z).
    z: int
    #: The cell's annotations on it.
    n_annotations: int
    #: The links between two of the cell's annotations that skip over it.
    n_links_over: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DamagedMark:
    """Where the cell crosses a damaged section: a disc in that section's plane.

    An annotation on it is its own disc; a link over it is the disc between
    the link's two ends where the link passes the section, its center and
    radius taken linearly by section number.  In the cell's own frame (its
    ``Xum``/``Yum``/``Zum``, which ``/nodes`` serves as ``xyz_um``), so the
    viewer draws it where it draws the cell's annotations.
    """

    z: int
    xyz_um: tuple[float, float, float]
    radius_um: float
    #: A link over the section, not an annotation on it.
    over: bool

    def as_dict(self) -> dict:
        return {"z": self.z, "xyz_um": list(self.xyz_um), "radius_um": self.radius_um,
                "over": self.over}


def crossings(neuron, damaged: Iterable[int]) -> list[DamagedCrossing]:
    """The damaged sections (``damaged``, section numbers) ``neuron`` crosses, ascending.

    A section the cell spans without an annotation on it or a link over it
    (another branch of the cell is there, this one is not) is not crossed.
    A link stored both ways counts once.
    """
    return find(neuron, damaged)[0]


def find(neuron, damaged: Iterable[int]) -> tuple[list[DamagedCrossing], list[DamagedMark]]:
    """:func:`crossings`, and a :class:`DamagedMark` for each annotation and link they count.

    The marks are by section, the annotations on it first.
    """
    sections = np.unique(np.asarray(list(damaged), dtype=np.int64))
    nodes = neuron.nodes
    if not len(sections) or nodes is None or nodes.empty:
        return [], []
    columns = [c for c in ("ID", "Z", "Xum", "Yum", "Zum", "Rum") if c in nodes.columns]
    own = nodes.loc[nodes["ParentID"] == neuron.id, columns].drop_duplicates("ID")
    z = np.rint(own["Z"].to_numpy(dtype=float)).astype(np.int64)
    placed = all(c in own.columns for c in ("Xum", "Yum", "Zum"))
    xyz = own[["Xum", "Yum", "Zum"]].to_numpy(dtype=float) if placed else None
    radius = own["Rum"].to_numpy(dtype=float) if "Rum" in own.columns else np.zeros(len(own))

    on_rows = np.flatnonzero(np.isin(z, sections))
    on = np.zeros(len(sections), dtype=np.int64)
    np.add.at(on, np.searchsorted(sections, z[on_rows]), 1)

    over = np.zeros(len(sections), dtype=np.int64)
    spans: list[tuple[int, int, int, int]] = []  # (row a, row b, first, stop) in `sections`
    edges = getattr(neuron, "edges", None)
    if edges is not None and len(edges):
        index = own.set_index("ID").index
        row_a = index.get_indexer(edges["A"].to_numpy())
        row_b = index.get_indexer(edges["B"].to_numpy())
        keep = (row_a >= 0) & (row_b >= 0)
        ends = np.unique(np.sort(np.stack([row_a[keep], row_b[keep]], axis=1), axis=1), axis=0)
        lo = np.minimum(z[ends[:, 0]], z[ends[:, 1]])
        hi = np.maximum(z[ends[:, 0]], z[ends[:, 1]])
        # The damaged sections strictly between a link's ends: [start, stop) in `sections`.
        start = np.searchsorted(sections, lo, side="right")
        stop = np.searchsorted(sections, hi, side="left")
        span = np.flatnonzero(stop > start)
        steps = np.zeros(len(sections) + 1, dtype=np.int64)
        np.add.at(steps, start[span], 1)
        np.add.at(steps, stop[span], -1)
        over = np.cumsum(steps)[:-1]
        spans = [(int(ends[i, 0]), int(ends[i, 1]), int(start[i]), int(stop[i])) for i in span]

    crossed = np.flatnonzero((on > 0) | (over > 0))
    found = [DamagedCrossing(int(sections[i]), int(on[i]), int(over[i])) for i in crossed]
    if xyz is None:
        return found, []

    def mark(section: int, at: np.ndarray, r: float, is_over: bool) -> DamagedMark:
        x, y, zz = (round(float(v), _DECIMALS) for v in at)
        return DamagedMark(section, (x, y, zz), round(max(float(r), 0.0), _DECIMALS), is_over)

    marks: list[tuple[int, int, DamagedMark]] = []
    for row in on_rows:
        if np.isfinite(xyz[row]).all():
            marks.append((int(z[row]), 0, mark(int(z[row]), xyz[row], radius[row], False)))
    for a, b, first, stop_at in spans:
        if not (np.isfinite(xyz[a]).all() and np.isfinite(xyz[b]).all()):
            continue
        for i in range(first, stop_at):
            section = int(sections[i])
            t = (section - z[a]) / (z[b] - z[a])
            at = xyz[a] + t * (xyz[b] - xyz[a])
            r = radius[a] + t * (radius[b] - radius[a])
            marks.append((section, 1, mark(section, at, r if np.isfinite(r) else 0.0, True)))
    marks.sort(key=lambda m: (m[0], m[1]))
    return found, [m[2] for m in marks]
