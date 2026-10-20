"""Parity checks for the RegularizeData3D port and the IPL depth chain.

These are the gate the plan puts on Phase 5: the boundary fit is what every IPL
depth number in the toolbox depends on, so it is validated numerically rather
than by inspection.
"""

from __future__ import annotations

import numpy as np
import pytest

from sbfsem.analysis import regularize as R
from sbfsem.analysis.boundary import load_ipl_boundaries
from sbfsem.analysis.regularize import regularize_data_3d
from sbfsem.core.transforms import viking2micron


def _fidelity(interp, x, y, xnodes, ynodes):
    nx, ny = xnodes.size, ynodes.size
    indx = np.clip(np.searchsorted(xnodes, x, side="right") - 1, 0, nx - 2)
    indy = np.clip(np.searchsorted(ynodes, y, side="right") - 1, 0, ny - 2)
    ind = indy + ny * indx
    dx, dy = np.diff(xnodes), np.diff(ynodes)
    tx = np.clip((x - xnodes[indx]) / dx[indx], 0, 1)
    ty = np.clip((y - ynodes[indy]) / dy[indy], 0, 1)
    return R._fidelity_matrix(interp, x.size, nx * ny, nx, ny, ind, indx, indy,
                              tx, ty, x, y, xnodes, ynodes)


@pytest.fixture(scope="module")
def scatter():
    rng = np.random.default_rng(3)
    xnodes = np.linspace(0, 10, 13)
    ynodes = np.linspace(0, 10, 11)
    x = rng.uniform(0.1, 9.9, 300)
    y = rng.uniform(0.1, 9.9, 300)
    return x, y, xnodes, ynodes


@pytest.mark.parametrize(
    ("interp", "surface", "exact"),
    [
        ("triangle", lambda X, Y: 3 * X + 2 * Y + 1, True),
        ("bilinear", lambda X, Y: 3 * X + 2 * Y + 1, True),
        ("bicubic", lambda X, Y: 3 * X + 2 * Y + 1, True),
        ("bilinear", lambda X, Y: X * Y, True),
        ("triangle", lambda X, Y: X * Y, False),
        ("bicubic", lambda X, Y: 0.01 * X**3 - 0.02 * Y**3 + 0.1 * X * Y**2, True),
        ("bilinear", lambda X, Y: 0.01 * X**3 - 0.02 * Y**3 + 0.1 * X * Y**2, False),
    ],
)
def test_interpolation_matrices_reproduce_polynomials(scatter, interp, surface, exact):
    """Each interpolant must be exact for the polynomial degree it claims."""
    x, y, xnodes, ynodes = scatter
    A = _fidelity(interp, x, y, xnodes, ynodes)
    XG, YG = np.meshgrid(xnodes, ynodes)
    grid_values = surface(XG, YG).ravel(order="F")
    error = np.abs(A @ grid_values - surface(x, y)).max()
    if exact:
        assert error < 1e-10
    else:
        assert error > 1e-3


def test_interpolation_matrices_are_a_partition_of_unity(scatter):
    x, y, xnodes, ynodes = scatter
    for interp in ("triangle", "bilinear", "bicubic", "nearest"):
        A = _fidelity(interp, x, y, xnodes, ynodes)
        row_sums = np.asarray(A.sum(axis=1)).ravel()
        assert np.abs(row_sums - 1.0).max() < 1e-9


def test_fit_recovers_a_smooth_surface():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 10, 800)
    y = rng.uniform(0, 10, 800)

    def f(X, Y):
        return 0.5 * X + 0.2 * Y + 2 * np.sin(X / 3) * np.cos(Y / 4)

    z = f(x, y) + rng.normal(0, 0.01, 800)
    zg, xg, yg = regularize_data_3d(
        x, y, z, np.linspace(0, 10, 30), np.linspace(0, 10, 30),
        interp="bicubic", smoothness=0.005,
    )
    rms = np.sqrt(((zg - f(xg, yg)) ** 2).mean())
    assert rms < 0.05


def test_solvers_agree():
    rng = np.random.default_rng(7)
    x = rng.uniform(0, 5, 200)
    y = rng.uniform(0, 5, 200)
    z = np.sin(x) + np.cos(y)
    nodes = np.linspace(0, 5, 15)
    direct, _, _ = regularize_data_3d(x, y, z, nodes, nodes, solver="normal")
    iterative, _, _ = regularize_data_3d(
        x, y, z, nodes, nodes, solver="lsqr", max_iter=200000
    )
    assert np.abs(direct - iterative).max() < 1e-6


def test_smoothness_flattens():
    rng = np.random.default_rng(11)
    x = rng.uniform(0, 5, 300)
    y = rng.uniform(0, 5, 300)
    z = np.sin(3 * x) * np.cos(3 * y)
    nodes = np.linspace(0, 5, 20)
    rough, _, _ = regularize_data_3d(x, y, z, nodes, nodes, smoothness=1e-4)
    smooth, _, _ = regularize_data_3d(x, y, z, nodes, nodes, smoothness=100.0)
    assert smooth.std() < rough.std()


class TestIPLDepth:
    """The check that would have caught the ``meshgrid(newYPts, newYPts)`` typo."""

    @pytest.fixture(scope="class")
    def boundaries(self):
        return load_ipl_boundaries("i")

    def test_depth_is_zero_at_inl_markers(self, boundaries):
        xyz = viking2micron(boundaries.inl.marker_locations, "i")
        depth = boundaries.micron2ipl(xyz)
        assert abs(np.nanmedian(depth)) < 0.05
        assert np.nanpercentile(np.abs(depth), 75) < 0.1

    def test_depth_is_one_at_gcl_markers(self, boundaries):
        xyz = viking2micron(boundaries.gcl.marker_locations, "i")
        depth = boundaries.micron2ipl(xyz)
        assert abs(np.nanmedian(depth) - 1.0) < 0.05
        assert np.nanpercentile(np.abs(depth - 1.0), 75) < 0.1

    def test_surfaces_track_their_markers(self, boundaries):
        for surface in (boundaries.inl, boundaries.gcl):
            xyz = viking2micron(surface.marker_locations, "i")
            fitted = surface.xy_eval(xyz[:, 0], xyz[:, 1])
            rms = np.sqrt(np.nanmean((fitted - xyz[:, 2]) ** 2))
            assert rms < 2.0, f"{surface.layer} fit residual {rms:.2f} um"

    def test_depth_uses_the_x_grid_for_x(self, boundaries):
        """Directly: swapping the INL grid axes must change the answer."""
        inl = boundaries.inl
        assert not np.allclose(inl.x_points, inl.y_points)
        probe = np.array([[inl.x_points[150], inl.y_points[40], 60.0]])
        correct = boundaries.micron2ipl(probe)

        from scipy.interpolate import RegularGridInterpolator

        wrong_grid = RegularGridInterpolator(
            (inl.y_points, inl.y_points), inl.surface, bounds_error=False, fill_value=np.nan
        )
        v_inl_wrong = wrong_grid(probe[:, [1, 0]])
        v_gcl = boundaries.gcl.xy_eval(probe[:, 0], probe[:, 1])
        wrong = (probe[:, 2] - v_inl_wrong) / (v_gcl - v_inl_wrong)
        assert not np.allclose(correct, wrong, equal_nan=True)

    def test_zero_thickness_gives_nan_not_a_huge_number(self, boundaries):
        from sbfsem.analysis.boundary import BoundarySurface, IPLBoundaries

        flat = np.zeros((5, 5))
        pts = np.linspace(0, 4, 5)
        same = BoundarySurface("INL", "NeitzInferiorMonkey", np.zeros((3, 3)), pts, pts, flat)
        other = BoundarySurface("GCL", "NeitzInferiorMonkey", np.zeros((3, 3)), pts, pts, flat)
        pair = IPLBoundaries(same, other)
        depth = pair.micron2ipl([[2.0, 2.0, 1.0]])
        assert np.isnan(depth[0])
