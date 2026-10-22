"""IPL stratification analyses.

Ports ``util/analysis/iplDepth.m``, ``util/analysis/getSynapseStratification.m``,
``util/analysis/singleDendriteStratification.m`` and
``util/plots/stratificationMap.m``.

Deviations (see DEVIATIONS.md):

* Everything here sits on top of :mod:`sbfsem.analysis.boundary`, so it inherits
  the ``micron2ipl`` axis fix (F214) — the single largest correction in the port.
* Every analysis takes ``method``: ``"column"`` (the default, MATLAB's: depth
  measured straight along Z) or ``"normal"`` (along each IPL surface's normal),
  an addition (2026-10-22; :mod:`sbfsem.analysis.boundary`).  The result's
  ``meta`` names the method that made it.
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
import pandas as pd

from .boundary import IPLBoundaries, ipl_method, load_ipl_boundaries
from .stats import Stats, bin_centers, describe

__all__ = ["StratificationResult", "ipl_depth", "annotation_depths", "synapse_stratification",
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
    method: str = "column",
) -> StratificationResult:
    """IPL depth of every cell-body annotation on a neuron.

    ``include_soma=False`` drops annotations larger than 80% of the soma radius,
    matching the MATLAB definition.  ``method``: ``"column"`` (MATLAB's) or
    ``"normal"`` (:meth:`IPLBoundaries.depth`).
    """
    method = ipl_method(method)
    boundaries = boundaries or load_ipl_boundaries(neuron.source)
    nodes = neuron.get_cell_nodes()
    if not include_soma:
        soma_radius = float(nodes["Rum"].max())
        nodes = nodes[nodes["Rum"] <= 0.8 * soma_radius]

    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    depth = boundaries.micron2ipl(xyz, method)
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
        {"neuron_id": getattr(neuron, "id", None), "include_soma": include_soma, "method": method},
    )


def annotation_depths(
    neuron,
    *,
    boundaries: IPLBoundaries | None = None,
    include_soma: bool = True,
    omit_outliers: bool = True,
    ipl_margin: float = 0.2,
    method: str = "column",
) -> pd.DataFrame:
    """Every cell-body annotation's IPL depth, and whether :func:`ipl_depth`
    with the same settings counts it: one row per annotation, in the order of
    the cell's nodes.

    Columns: ``location_id``, ``section`` (Viking's Z), ``x_um``, ``y_um``,
    ``z_um``, ``radius_um``, ``ipl_depth`` (0 at the INL border, 1 at the GCL
    border; NaN where the point is not between the fitted surfaces),
    ``depth_um`` (the distance from the INL border toward the GCL),
    ``ipl_thickness_um`` (the IPL's thickness through the annotation; both
    along Z for ``"column"``, along the surfaces' normals for ``"normal"``),
    ``counted`` and ``left_out``: ``""`` for a counted annotation, else why it
    is not, ``"soma"`` (larger than 80 % of the largest radius, with the soma
    left out), ``"outside surfaces"`` or ``"outlier"`` (beyond ``ipl_margin``
    of the IPL).  The counted depths are :func:`ipl_depth`'s ``depth``, in the
    same order, for the same ``method``.
    """
    method = ipl_method(method)
    boundaries = boundaries or load_ipl_boundaries(neuron.source)
    nodes = neuron.get_cell_nodes()
    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    if len(nodes):
        measured = boundaries.depth(xyz, method)
        depth, depth_um, thickness = measured.fraction, measured.depth_um, measured.thickness_um
    else:
        depth = depth_um = thickness = np.empty(0, dtype=float)
    left_out = np.full(len(nodes), "", dtype=object)
    # In ipl_depth's order: the soma first, then no depth, then the margin.
    if not include_soma and len(nodes):
        left_out[nodes["Rum"].to_numpy(dtype=float) > 0.8 * float(nodes["Rum"].max())] = "soma"
    left_out[(left_out == "") & np.isnan(depth)] = "outside surfaces"
    if omit_outliers:
        with np.errstate(invalid="ignore"):
            beyond = (depth > 1 + ipl_margin) | (depth < -ipl_margin)
        left_out[(left_out == "") & beyond] = "outlier"
    return pd.DataFrame({
        "location_id": nodes["ID"].to_numpy(dtype=np.int64),
        "section": nodes["Z"].to_numpy(),
        "x_um": xyz[:, 0],
        "y_um": xyz[:, 1],
        "z_um": xyz[:, 2],
        "radius_um": nodes["Rum"].to_numpy(dtype=float),
        "ipl_depth": depth,
        "depth_um": depth_um,
        "ipl_thickness_um": thickness,
        "counted": left_out == "",
        "left_out": left_out,
    })


def synapse_stratification(
    neuron,
    synapse_type,
    *,
    boundaries: IPLBoundaries | None = None,
    num_bins: int = 25,
    pdf: bool = False,
    method: str = "column",
) -> StratificationResult:
    """IPL depth of one synapse type.  Ports ``getSynapseStratification``."""
    method = ipl_method(method)
    boundaries = boundaries or load_ipl_boundaries(neuron.source)
    xyz = neuron.get_synapse_xyz(synapse_type)
    if xyz.size == 0:
        raise ValueError(f"No {synapse_type} annotations on c{neuron.id}")
    depth = boundaries.micron2ipl(xyz, method)
    n_nan = int(np.isnan(depth).sum())
    depth = depth[~np.isnan(depth)]
    result = _summarize(
        depth, num_bins, n_nan, 0,
        {"neuron_id": getattr(neuron, "id", None), "synapse_type": str(synapse_type),
         "method": method},
    )
    if pdf and result.counts.sum():
        result.counts = result.counts / result.counts.sum()
    return result


def branch_stratification(
    neuron, location_a: int, location_b: int, *, boundaries: IPLBoundaries | None = None,
    num_bins: int = 20, method: str = "column",
) -> StratificationResult:
    """IPL depth along one branch.  Ports ``singleDendriteStratification``."""
    method = ipl_method(method)
    boundaries = boundaries or load_ipl_boundaries(neuron.source)
    nodes = neuron.get_branch_nodes(location_a, location_b)
    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    depth = boundaries.micron2ipl(xyz, method)
    n_nan = int(np.isnan(depth).sum())
    depth = depth[~np.isnan(depth)]
    return _summarize(
        depth, num_bins, n_nan, 0,
        {"neuron_id": getattr(neuron, "id", None),
         "location_a": int(location_a), "location_b": int(location_b), "method": method},
    )
