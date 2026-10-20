"""Catmull-Rom splines through closed-curve control points.

Ports ``util/renders/catmullRomSpline.m``, ``util/renders/getSpline.m`` and the
identical static ``getSpline`` methods on ``sbfsem.builtin.ClosedCurve`` and
``sbfsem.image.ConeOutline``.

Closing a Catmull-Rom loop means wrapping the *first* three control points onto
the end.  ``ConeOutline`` appended the last three instead, so its outline never
closed (see the meta-pattern section of the review).
"""

from __future__ import annotations

import numpy as np

__all__ = ["catmull_rom_spline", "close_spline", "DEFAULT_POINT_BUDGET"]


def catmull_rom_spline(
    px, py, n: int = 100, tension: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate a Catmull-Rom spline through ``px``/``py``.

    Needs at least four control points; each consecutive quadruple contributes
    ``n + 1`` samples, exactly as the MATLAB loop did.
    """
    px = np.asarray(px, dtype=float).ravel()
    py = np.asarray(py, dtype=float).ravel()
    if px.size != py.size:
        raise ValueError("px and py must be the same length")
    if px.size < 4:
        raise ValueError("A Catmull-Rom spline needs at least 4 control points")
    n = max(1, int(np.ceil(n)))

    s = (1.0 - tension) / 2.0
    basis = np.array(
        [
            [-s, 2 - s, s - 2, s],
            [2 * s, s - 3, 3 - 2 * s, -s],
            [-s, 0.0, s, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ]
    )
    u = np.linspace(0.0, 1.0, n + 1)
    powers = np.column_stack([u**3, u**2, u, np.ones_like(u)])
    weights = powers @ basis  # (n+1, 4)

    xs, ys = [], []
    for i in range(px.size - 3):
        xs.append(weights @ px[i : i + 4])
        ys.append(weights @ py[i : i + 4])
    return np.concatenate(xs), np.concatenate(ys)


#: Default total sample budget for a closed outline.  ``n`` in
#: :func:`catmull_rom_spline` is samples *per control-point interval*, so a
#: literal ``n = 100`` on a 108-point Viking outline yields 10,900 points —
#: fine for a MATLAB plot, ruinous for a JSON payload or a triangulation.
DEFAULT_POINT_BUDGET = 600


def close_spline(
    points, n: int | None = None, tension: float = 0.0, *,
    budget: int = DEFAULT_POINT_BUDGET,
) -> np.ndarray:
    """Spline a closed polygon, wrapping the first three control points.

    ``points`` is ``(N, 2)``; the result is ``(M, 2)``.

    ``n`` is samples per control-point interval, as in the MATLAB original.
    Leave it ``None`` to spend a total ``budget`` of samples across the whole
    outline instead, which keeps a sparse outline smooth without letting a dense
    one explode.
    """
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    if len(pts) < 3:
        return pts
    if n is None:
        n = max(1, int(round(budget / len(pts))))
    padded = np.vstack([pts, pts[:3]])
    x, y = catmull_rom_spline(padded[:, 0], padded[:, 1], n, tension)
    return np.column_stack([x, y])
