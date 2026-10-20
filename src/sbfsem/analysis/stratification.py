"""IPL stratification analyses.

Ports ``util/analysis/iplDepth.m``, ``util/analysis/getSynapseStratification.m``,
``util/analysis/singleDendriteStratification.m`` and
``util/plots/stratificationMap.m``.

Deviations (see DEVIATIONS.md):

* Everything here sits on top of :mod:`sbfsem.analysis.boundary`, so it inherits
  the ``micron2ipl`` axis fix (F214) — the single largest correction in the port.
* ``iplDepth`` reported bin centers as ``b(2:end) - (b(2)-b(1))`` (the left edge),
  plotted ``b(1:end-1) + (b(2)-b(1))`` (the right edge) and reported the mode as
  ``b(ind) + binWidth`` (the right edge again): a systematic half-bin shift of the
  statistics and a full-bin disagreement between table and plot (F123).  At the
  default 20 bins that is 2.5% of total IPL depth, enough to move a cell between
  the ON and OFF strata.  All three now use true bin centers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .boundary import IPLBoundaries, load_ipl_boundaries
from .stats import Stats, bin_centers, describe

__all__ = ["StratificationResult", "ipl_depth", "synapse_stratification",
           "branch_stratification"]


@dataclass
class StratificationResult:
    """IPL depths for a set of annotations, plus their histogram."""

    depth: np.ndarray
    counts: np.ndarray
    edges: np.ndarray
    centers: np.ndarray
    stats: Stats
    mode: float
    n_omitted_nan: int = 0
    n_omitted_outlier: int = 0
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "depth": self.depth.tolist(),
            "counts": self.counts.tolist(),
            "edges": self.edges.tolist(),
            "centers": self.centers.tolist(),
            "stats": self.stats.as_dict(),
            "mode": self.mode,
            "n_omitted_nan": self.n_omitted_nan,
            "n_omitted_outlier": self.n_omitted_outlier,
            **self.meta,
        }


def _summarize(depth: np.ndarray, bins, n_nan: int, n_outlier: int, meta) -> StratificationResult:
    if depth.size == 0:
        # An empty histogram over np.histogram's default (0, 1) range would
        # report a spurious mode; say nothing instead.
        empty = np.empty(0, dtype=float)
        return StratificationResult(
            depth=depth, counts=empty, edges=empty, centers=empty,
            stats=describe(depth), mode=float("nan"),
            n_omitted_nan=n_nan, n_omitted_outlier=n_outlier, meta=meta,
        )
    counts, edges = np.histogram(depth, bins=bins)
    centers = bin_centers(edges)
    mode = float(centers[int(np.argmax(counts))]) if counts.size else float("nan")
    return StratificationResult(
        depth=depth,
        counts=counts,
        edges=edges,
        centers=centers,
        stats=describe(depth),
        mode=mode,
        n_omitted_nan=n_nan,
        n_omitted_outlier=n_outlier,
        meta=meta,
    )


def ipl_depth(
    neuron,
    *,
    boundaries: IPLBoundaries | None = None,
    num_bins: int = 20,
    bin_locations=None,
    include_soma: bool = True,
    omit_outliers: bool = True,
    ipl_margin: float = 0.2,
) -> StratificationResult:
    """IPL depth of every cell-body annotation on a neuron.

    ``include_soma=False`` drops annotations larger than 80% of the soma radius,
    matching the MATLAB definition.
    """
    boundaries = boundaries or load_ipl_boundaries(neuron.source)
    nodes = neuron.get_cell_nodes()
    if not include_soma:
        soma_radius = float(nodes["Rum"].max())
        nodes = nodes[nodes["Rum"] <= 0.8 * soma_radius]

    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    depth = boundaries.micron2ipl(xyz)
    n_nan = int(np.isnan(depth).sum())
    depth = depth[~np.isnan(depth)]

    n_outlier = 0
    if omit_outliers:
        keep = (depth <= 1 + ipl_margin) & (depth >= -ipl_margin)
        n_outlier = int((~keep).sum())
        depth = depth[keep]

    bins = bin_locations if bin_locations is not None else num_bins
    return _summarize(
        depth, bins, n_nan, n_outlier,
        {"neuron_id": getattr(neuron, "id", None), "include_soma": include_soma},
    )


def synapse_stratification(
    neuron,
    synapse_type,
    *,
    boundaries: IPLBoundaries | None = None,
    num_bins: int = 25,
    pdf: bool = False,
) -> StratificationResult:
    """IPL depth of one synapse type.  Ports ``getSynapseStratification``."""
    boundaries = boundaries or load_ipl_boundaries(neuron.source)
    xyz = neuron.get_synapse_xyz(synapse_type)
    if xyz.size == 0:
        raise ValueError(f"No {synapse_type} annotations on c{neuron.id}")
    depth = boundaries.micron2ipl(xyz)
    n_nan = int(np.isnan(depth).sum())
    depth = depth[~np.isnan(depth)]
    result = _summarize(
        depth, num_bins, n_nan, 0,
        {"neuron_id": getattr(neuron, "id", None), "synapse_type": str(synapse_type)},
    )
    if pdf and result.counts.sum():
        result.counts = result.counts / result.counts.sum()
    return result


def branch_stratification(
    neuron, location_a: int, location_b: int, *, boundaries: IPLBoundaries | None = None,
    num_bins: int = 20,
) -> StratificationResult:
    """IPL depth along one branch.  Ports ``singleDendriteStratification``."""
    boundaries = boundaries or load_ipl_boundaries(neuron.source)
    nodes = neuron.get_branch_nodes(location_a, location_b)
    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    depth = boundaries.micron2ipl(xyz)
    n_nan = int(np.isnan(depth).sum())
    depth = depth[~np.isnan(depth)]
    return _summarize(
        depth, num_bins, n_nan, 0,
        {"neuron_id": getattr(neuron, "id", None),
         "location_a": int(location_a), "location_b": int(location_b)},
    )
