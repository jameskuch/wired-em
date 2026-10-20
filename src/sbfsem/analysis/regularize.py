"""Regularized least-squares surface fitting.

A direct port of ``lib/RegularizeData3D.m`` (John D'Errico's ``gridfit``, as
modified by Charles Jekel / "Math for Mere Mortals").  This is a *smoother*, not
an interpolant: it solves for grid node heights that jointly minimize the
residual to the scattered input and the sum of squared second derivatives across
the grid.

It matters because it fits the INL and GCL boundary surfaces, so any numerical
difference here propagates into every IPL depth the toolbox produces.  The
formulation is preserved exactly:

* the fidelity block ``A`` maps each scattered point onto its grid cell with
  triangle, bilinear, bicubic or nearest interpolation;
* the regularizer block stacks the three-point second-derivative stencils in y
  and then in x, scaled by ``sqrt(nFidelity / nRegularizer) * surfaceDomainScale``;
* the stacked system is solved in the least-squares sense.

MATLAB solved with backslash on the rectangular system, a sparse QR least
squares solve.  The default here is ``solver="normal"``: a sparse direct solve of
the normal equations, which is what MATLAB's own ``'normal'`` option did and
which the regularizer makes well conditioned.  On the shipped 1136-marker INL
cache it agrees with a fully converged ``lsqr`` to within 1e-9 and takes 0.7 s
where ``lsqr`` needs tens of thousands of iterations to get there — so ``lsqr``
is available but is not the default, because a half-converged iterative solve
silently returns a surface that is wrong by tens of microns.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import lsqr, spsolve

__all__ = ["regularize_data_3d", "RegularizeResult"]

_INTERP_METHODS = ("triangle", "bilinear", "bicubic", "nearest")


class RegularizeResult(tuple):
    """``(zgrid, xgrid, ygrid)`` with named access."""

    __slots__ = ()

    @property
    def zgrid(self) -> np.ndarray:
        return self[0]

    @property
    def xgrid(self) -> np.ndarray:
        return self[1]

    @property
    def ygrid(self) -> np.ndarray:
        return self[2]


def regularize_data_3d(
    x,
    y,
    z,
    xnodes,
    ynodes,
    *,
    smoothness: float | tuple[float, float] = 0.01,
    interp: str = "triangle",
    solver: str = "normal",
    max_iter: int | None = None,
    extend: str = "always",
) -> RegularizeResult:
    """Fit a smooth surface to scattered ``(x, y, z)`` on a rectangular grid.

    Parameters mirror the MATLAB signature.  ``xnodes``/``ynodes`` may be an
    integer node count instead of an explicit vector.

    Returns ``(zgrid, xgrid, ygrid)`` where ``zgrid`` has shape
    ``(len(ynodes), len(xnodes))``, matching MATLAB's row-major-in-y convention.
    """
    if interp not in _INTERP_METHODS:
        raise ValueError(f"interp must be one of {_INTERP_METHODS}")

    x = np.asarray(x, dtype=float).ravel()
    y = np.asarray(y, dtype=float).ravel()
    z = np.asarray(z, dtype=float).ravel()
    if not (x.size == y.size == z.size):
        raise ValueError("x, y and z must be the same length")
    good = ~(np.isnan(x) | np.isnan(y) | np.isnan(z))
    x, y, z = x[good], y[good], z[good]
    if x.size < 3:
        raise ValueError("Insufficient data for surface estimation")

    xnodes = _make_nodes(xnodes, x)
    ynodes = _make_nodes(ynodes, y)
    min_axis = 4 if interp == "bicubic" else 3
    if xnodes.size < min_axis or ynodes.size < min_axis:
        raise ValueError(
            f"The output grid must have at least {min_axis} nodes on each axis "
            f"for {interp} interpolation"
        )
    if np.any(np.diff(xnodes) <= 0) or np.any(np.diff(ynodes) <= 0):
        raise ValueError("xnodes and ynodes must be monotone increasing")

    xnodes, ynodes = _extend_nodes(xnodes, ynodes, x, y, extend)

    nx, ny = xnodes.size, ynodes.size
    ngrid = nx * ny
    dx, dy = np.diff(xnodes), np.diff(ynodes)
    n = x.size

    # Which cell does each point fall in?  MATLAB's histc, 1-based, with points
    # on the last node folded into the last cell.
    indx = np.clip(np.searchsorted(xnodes, x, side="right") - 1, 0, nx - 2)
    indy = np.clip(np.searchsorted(ynodes, y, side="right") - 1, 0, ny - 2)
    # Column-major linear index into an (ny, nx) grid, zero-based.
    ind = indy + ny * indx

    tx = np.clip((x - xnodes[indx]) / dx[indx], 0.0, 1.0)
    ty = np.clip((y - ynodes[indy]) / dy[indy], 0.0, 1.0)

    A = _fidelity_matrix(interp, n, ngrid, nx, ny, ind, indx, indy, tx, ty,
                         x, y, xnodes, ynodes)

    Areg = _regularizer(nx, ny, dx, dy, smoothness)

    n_fidelity = A.shape[0]
    n_regular = nx * (ny - 2) + ny * (nx - 2)
    scale = np.sqrt(n_fidelity / n_regular)
    domain = (xnodes.max() - xnodes.min()) * (ynodes.max() - ynodes.min())
    scale = scale * domain

    system = sp.vstack([A, Areg * scale], format="csr")
    rhs = np.concatenate([z, np.zeros(Areg.shape[0])])

    if solver == "normal":
        normal = (system.T @ system).tocsc()
        values = spsolve(normal, system.T @ rhs)
    else:
        iter_lim = max_iter if max_iter is not None else min(10000, ngrid) * 10
        values = lsqr(system, rhs, atol=1e-12, btol=1e-12, iter_lim=iter_lim)[0]

    zgrid = np.asarray(values).reshape((ny, nx), order="F")
    xgrid, ygrid = np.meshgrid(xnodes, ynodes)
    return RegularizeResult((zgrid, xgrid, ygrid))


def _make_nodes(nodes, values: np.ndarray) -> np.ndarray:
    if np.isscalar(nodes):
        count = int(np.asarray(nodes).item())
        out = np.linspace(values.min(), values.max(), count)
        out[-1] = values.max()
        return out
    return np.asarray(nodes, dtype=float).ravel()


def _extend_nodes(xnodes, ynodes, x, y, extend: str):
    xnodes, ynodes = xnodes.copy(), ynodes.copy()
    if extend == "never":
        if x.min() < xnodes[0] or x.max() > xnodes[-1] or y.min() < ynodes[0] or y.max() > ynodes[-1]:
            raise ValueError("Data falls outside the node range and extend='never'")
        return xnodes, ynodes
    xnodes[0] = min(xnodes[0], x.min())
    xnodes[-1] = max(xnodes[-1], x.max())
    ynodes[0] = min(ynodes[0], y.min())
    ynodes[-1] = max(ynodes[-1], y.max())
    return xnodes, ynodes


def _fidelity_matrix(interp, n, ngrid, nx, ny, ind, indx, indy, tx, ty,
                     x, y, xnodes, ynodes) -> sp.csr_matrix:
    rows = np.repeat(np.arange(n), 3)
    if interp == "triangle":
        L = np.where(tx > ty, ny, 1)
        t1 = np.minimum(tx, ty)
        t2 = np.maximum(tx, ty)
        cols = np.column_stack([ind, ind + ny + 1, ind + L]).ravel()
        vals = np.column_stack([1 - t2, t1, t2 - t1]).ravel()
        return sp.csr_matrix((vals, (rows, cols)), shape=(n, ngrid))

    if interp == "nearest":
        # MATLAB's round() goes half away from zero; numpy's goes half to even.
        # `1 - t` is in [0, 1] and lands on exactly 0.5 whenever a point sits at
        # a cell's midpoint, which is common on a regular grid, so the two rules
        # pick different nodes for a large fraction of the data.
        offset = _round_half_up(1 - ty) + _round_half_up(1 - tx) * ny
        return sp.csr_matrix(
            (np.ones(n), (np.arange(n), ind + offset)), shape=(n, ngrid)
        )

    if interp == "bilinear":
        rows4 = np.repeat(np.arange(n), 4)
        cols = np.column_stack([ind, ind + 1, ind + ny, ind + ny + 1]).ravel()
        vals = np.column_stack(
            [(1 - tx) * (1 - ty), (1 - tx) * ty, tx * (1 - ty), tx * ty]
        ).ravel()
        return sp.csr_matrix((vals, (rows4, cols)), shape=(n, ngrid))

    # bicubic: a 4x4 Lagrange stencil centered as close to the point as possible
    xi = np.clip(indx - 1, 0, nx - 4)
    yi = np.clip(indy - 1, 0, ny - 4)
    xc = _lagrange_coefficients(x, xnodes, xi)
    yc = _lagrange_coefficients(y, ynodes, yi)
    base = yi + xi * ny
    # 16 columns per point: 4 x-offsets (times ny) crossed with 4 y-offsets.
    col_block = np.stack([base + k * ny for k in range(4)], axis=1)  # (n, 4)
    cols = np.concatenate([col_block + k for k in range(4)], axis=1)  # (n, 16)
    # MATLAB builds TheseCoefficients = xc' * yc and flattens column-major,
    # i.e. y varies fastest within each x block, matching the column order above.
    vals = (xc[:, :, None] * yc[:, None, :]).transpose(0, 2, 1).reshape(n, 16)
    rows16 = np.repeat(np.arange(n), 16)
    return sp.csr_matrix((vals.ravel(), (rows16, cols.ravel())), shape=(n, ngrid))


def _round_half_up(values: np.ndarray) -> np.ndarray:
    """Round half away from zero, as MATLAB's ``round`` does."""
    return np.floor(np.asarray(values, dtype=float) + 0.5).astype(int)


def _lagrange_coefficients(v: np.ndarray, nodes: np.ndarray, i0: np.ndarray) -> np.ndarray:
    """Four cubic Lagrange basis weights at ``v`` for nodes ``i0 .. i0 + 3``."""
    p = np.stack([nodes[i0 + k] for k in range(4)], axis=1)  # (n, 4)
    out = np.empty_like(p)
    for k in range(4):
        others = [j for j in range(4) if j != k]
        num = np.ones(len(v))
        den = np.ones(len(v))
        for j in others:
            num = num * (v - p[:, j])
            den = den * (p[:, k] - p[:, j])
        out[:, k] = num / den
    return out


def _regularizer(nx: int, ny: int, dx: np.ndarray, dy: np.ndarray, smoothness) -> sp.csr_matrix:
    stiffness = np.asarray(
        [smoothness, smoothness] if np.isscalar(smoothness) else smoothness, dtype=float
    ).ravel()
    if stiffness.size == 1:
        stiffness = np.repeat(stiffness, 2)
    ngrid = nx * ny

    # Second derivative in y, for every interior y node.
    i, j = np.meshgrid(np.arange(nx), np.arange(1, ny - 1), indexing="xy")
    ind = j.ravel(order="F") + ny * i.ravel(order="F")
    dy1 = dy[j.ravel(order="F") - 1]
    dy2 = dy[j.ravel(order="F")]
    rows = np.repeat(np.arange(ind.size), 3)
    cols = np.column_stack([ind - 1, ind, ind + 1]).ravel()
    vals = (
        stiffness[1]
        * np.column_stack(
            [-2.0 / (dy1 * (dy1 + dy2)), 2.0 / (dy1 * dy2), -2.0 / (dy2 * (dy1 + dy2))]
        ).ravel()
    )
    reg_y = sp.csr_matrix((vals, (rows, cols)), shape=(ind.size, ngrid))

    # Second derivative in x, for every interior x node.
    i, j = np.meshgrid(np.arange(1, nx - 1), np.arange(ny), indexing="xy")
    ind = j.ravel(order="F") + ny * i.ravel(order="F")
    dx1 = dx[i.ravel(order="F") - 1]
    dx2 = dx[i.ravel(order="F")]
    rows = np.repeat(np.arange(ind.size), 3)
    cols = np.column_stack([ind - ny, ind, ind + ny]).ravel()
    vals = (
        stiffness[0]
        * np.column_stack(
            [-2.0 / (dx1 * (dx1 + dx2)), 2.0 / (dx1 * dx2), -2.0 / (dx2 * (dx1 + dx2))]
        ).ravel()
    )
    reg_x = sp.csr_matrix((vals, (rows, cols)), shape=(ind.size, ngrid))
    return sp.vstack([reg_y, reg_x], format="csr")
