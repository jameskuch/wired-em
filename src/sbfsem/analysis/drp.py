"""Density recovery profile and nearest-neighbor statistics.

Ports ``util/analysis/densityRecoveryProfile.m`` and
``+sbfsem/+analysis/NearestNeighbor.m``.

Rodieck RW (1991), "The density recovery profile: a method for the analysis of
points in the plane applicable to retinal studies", *Visual Neuroscience* 6:95-111.

No edge correction is applied, exactly as in the MATLAB original: the profile is
biased downward near the border of the sampled region, and the effective radius
derived from it is correspondingly inflated.  ``effective_radius`` is an addition
here — MATLAB returned only the profile — and carries that same bias.

``NearestNeighbor`` in MATLAB referenced an undefined ``xyz`` and called a
``somaDiameter`` method that does not exist, so it could never run.  The
``knnsearch`` scaffolding is ported here on ``scipy.spatial.cKDTree``; the parts
that depended on the missing method are simply implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["DRPResult", "density_recovery_profile", "nearest_neighbor_distances"]


@dataclass
class DRPResult:
    """Binned pair-density as a function of separation."""

    density: np.ndarray
    counts: np.ndarray
    bin_centers: np.ndarray
    bin_width: float
    effective_radius: float
    mean_density: float

    def as_dict(self) -> dict:
        return {
            "density": self.density.tolist(),
            "counts": self.counts.tolist(),
            "bin_centers": self.bin_centers.tolist(),
            "bin_width": self.bin_width,
            "effective_radius": self.effective_radius,
            "mean_density": self.mean_density,
        }


def density_recovery_profile(
    centers, bin_width: float = 1.0, num_bins: int = 20
) -> DRPResult:
    """Auto-correlation density recovery profile of a 2-D point set.

    ``centers`` is ``(N, 2)``.  Each pairwise distance is counted once and the
    normalization halves the point count, which is Rodieck's double-counting
    convention.
    """
    from scipy.spatial.distance import pdist

    points = np.asarray(centers, dtype=float).reshape(-1, 2)
    n = len(points)
    if n < 2:
        raise ValueError("Need at least two points for a density recovery profile")
    distances = pdist(points)

    # One extra bin catches everything beyond the requested range; it is dropped.
    edges = np.arange(0.0, (num_bins + 1) * bin_width + bin_width, bin_width)
    counts, _ = np.histogram(distances, bins=edges)
    counts = counts[:num_bins]
    centers_ = edges[:num_bins] + bin_width / 2.0

    # Annulus area for each bin.
    inner = edges[:num_bins]
    outer = edges[1 : num_bins + 1]
    area = np.pi * (outer**2 - inner**2)
    norm = 0.5 * n
    density = counts / (area * norm)

    mean_density = float(density[num_bins // 2 :].mean()) if num_bins > 2 else float(density.mean())
    # Rodieck's effective radius: the radius of the "hole" of equal missing area.
    if mean_density > 0:
        deficit = np.clip(mean_density * area - counts / norm, 0.0, None)
        effective_radius = float(np.sqrt(deficit.sum() / (np.pi * mean_density)))
    else:  # pragma: no cover - degenerate input
        effective_radius = float("nan")
    return DRPResult(density, counts, centers_, float(bin_width), effective_radius, mean_density)


def nearest_neighbor_distances(points, k: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Distances and indices of the ``k`` nearest neighbors of each point.

    The first column is the point itself (distance zero), matching MATLAB's
    ``knnsearch(xyz, xyz, 'K', k)``.
    """
    from scipy.spatial import cKDTree

    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2:
        raise ValueError("points must be (N, D)")
    k = min(int(k), len(pts))
    distances, indices = cKDTree(pts).query(pts, k=k)
    return np.atleast_2d(distances), np.atleast_2d(indices)
