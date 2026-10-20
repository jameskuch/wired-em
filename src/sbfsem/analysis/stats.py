"""Small statistics helpers shared by the analyses.

Ports ``util/analysis/helpers/`` — ``sem``, ``createStatsStructure``,
``printStat``, ``myhistcounts``, ``fastEuclid2d``, ``fastEuclid3d``,
``rmNaN``, ``fwhm2sigma`` — and the top-level ``util/euclideanDist2/3``.

Deviations (see DEVIATIONS.md):

* ``sem`` was ``std(data) / sqrt(length(data))``.  ``length`` returns the largest
  dimension, so a row vector and a matrix gave different answers; NaNs were
  counted but not excluded; and empty input silently returned NaN.  Every error
  bar produced through ``printStat`` or ``createStatsStructure`` went through it.
  This version excludes NaNs and counts the values actually used.
* The bin-center helper existed six times across the repository with three
  different formulas.  There is one here, and it returns true centers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

__all__ = [
    "sem",
    "Stats",
    "describe",
    "hist_counts",
    "bin_centers",
    "euclid2d",
    "euclid3d",
    "step_lengths_2d",
    "step_lengths_3d",
    "fwhm2sigma",
]


def sem(data) -> float:
    """Standard error of the mean, excluding NaNs."""
    values = np.asarray(data, dtype=float).ravel()
    values = values[~np.isnan(values)]
    if values.size < 2:
        return float("nan")
    return float(values.std(ddof=1) / np.sqrt(values.size))


@dataclass
class Stats:
    """The statistics bundle ``createStatsStructure`` returned."""

    n: int
    mean: float
    median: float
    sem: float
    std: float
    var: float
    min: float
    max: float

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return f"{self.mean:.3f} +- {self.sem:.3f} (n={self.n})"


def describe(data) -> Stats:
    """Ports ``createStatsStructure`` / ``printStat``, NaN-safe throughout."""
    values = np.asarray(data, dtype=float).ravel()
    values = values[~np.isnan(values)]
    if values.size == 0:
        nan = float("nan")
        return Stats(0, nan, nan, nan, nan, nan, nan, nan)
    return Stats(
        n=int(values.size),
        mean=float(values.mean()),
        median=float(np.median(values)),
        sem=sem(values),
        std=float(values.std(ddof=1)) if values.size > 1 else float("nan"),
        var=float(values.var(ddof=1)) if values.size > 1 else float("nan"),
        min=float(values.min()),
        max=float(values.max()),
    )


def hist_counts(data, bins=20, range_=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Histogram returning ``(counts, edges, centers)``.

    ``iplDepth`` reported ``b(2:end) - (b(2)-b(1))``, which is the *left* edge,
    and plotted ``b(1:end-1) + (b(2)-b(1))``, which is the *right* edge — a full
    bin apart, and both off the true center by half a bin (F123).
    """
    values = np.asarray(data, dtype=float).ravel()
    values = values[~np.isnan(values)]
    counts, edges = np.histogram(values, bins=bins, range=range_)
    return counts, edges, bin_centers(edges)


def bin_centers(edges) -> np.ndarray:
    edges = np.asarray(edges, dtype=float).ravel()
    return (edges[:-1] + edges[1:]) / 2.0


def euclid3d(point, points) -> np.ndarray:
    """Distance from one point to many, in 3D.  Ports ``fastEuclid3d``."""
    a = np.asarray(point, dtype=float).reshape(-1, 3)
    b = np.asarray(points, dtype=float).reshape(-1, 3)
    return np.linalg.norm(a - b, axis=1)


def euclid2d(point, points) -> np.ndarray:
    """Distance from one point to many, in the XY plane.  Ports ``fastEuclid2d``."""
    a = np.asarray(point, dtype=float).reshape(-1, np.shape(point)[-1])[:, :2]
    b = np.asarray(points, dtype=float).reshape(-1, np.shape(points)[-1])[:, :2]
    return np.linalg.norm(a - b, axis=1)


def step_lengths_3d(xyz) -> np.ndarray:
    """Distance between consecutive points.  Ports ``euclideanDist3``."""
    pts = np.asarray(xyz, dtype=float).reshape(-1, 3)
    return np.linalg.norm(np.diff(pts, axis=0), axis=1)


def step_lengths_2d(xyz) -> np.ndarray:
    """Distance between consecutive points in XY.  Ports ``euclideanDist2``."""
    pts = np.asarray(xyz, dtype=float)
    return np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1)


def fwhm2sigma(fwhm: float) -> float:
    return float(fwhm) / (2.0 * np.sqrt(2.0 * np.log(2.0)))
