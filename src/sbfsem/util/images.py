"""Image-space helpers.

Ports ``util/images/im2vikingXY.m``, ``vikingBasis.m``, ``vikingBasis2.m``,
``rotateZ.m``, ``rotationMatrix.m`` and ``util/rect2pts.m``.

Deviations (see DEVIATIONS.md):

* ``vikingBasis`` measured the pixel distance between the two calibration points
  *after* shifting the red point, so every derived scale factor was wrong and
  every coordinate traced through the basis was mis-scaled (F229).
* ``util/plots/vikingBasis2.m`` and ``util/images/vikingBasis2.m`` were two
  different functions with the same name, so which one ran depended on MATLAB's
  path order and they produced different offsets.  There is one here.
* ``vikingBasis`` also read ``getImageRGB``'s ``[row, column]`` output as
  ``[x, y]``, swapping the axes (F226).  :func:`viking_basis` takes explicit
  ``(x, y)`` pairs in both spaces, so there is no orientation to get wrong.
* ``im2vikingXY`` left its outputs unassigned for a single input point (F227);
  :meth:`VikingBasis.to_viking` reshapes, so one point works like many.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["VikingBasis", "viking_basis", "image_to_viking", "rotation_matrix",
           "rotate_z", "rect_to_points"]


@dataclass
class VikingBasis:
    """Maps image pixel coordinates onto Viking volume coordinates.

    Built from two calibration points whose Viking coordinates are known.
    """

    origin_px: np.ndarray
    origin_viking: np.ndarray
    scale: float

    def to_viking(self, points) -> np.ndarray:
        """Image pixels -> Viking coordinates."""
        pts = np.asarray(points, dtype=float).reshape(-1, 2)
        return self.origin_viking + (pts - self.origin_px) * self.scale

    def to_image(self, points) -> np.ndarray:
        """Viking coordinates -> image pixels."""
        pts = np.asarray(points, dtype=float).reshape(-1, 2)
        return self.origin_px + (pts - self.origin_viking) / self.scale


def viking_basis(point_a_px, point_a_viking, point_b_px, point_b_viking) -> VikingBasis:
    """Derive an image-to-Viking basis from two calibration points.

    The scale is the Viking distance between the two points divided by their
    pixel distance.  ``vikingBasis.m`` measured the pixel distance after already
    translating one of the points, which made every scale factor wrong (F229).
    """
    a_px = np.asarray(point_a_px, dtype=float).ravel()[:2]
    b_px = np.asarray(point_b_px, dtype=float).ravel()[:2]
    a_vk = np.asarray(point_a_viking, dtype=float).ravel()[:2]
    b_vk = np.asarray(point_b_viking, dtype=float).ravel()[:2]

    pixel_distance = float(np.linalg.norm(b_px - a_px))
    if pixel_distance == 0:
        raise ValueError("Calibration points must be distinct in the image")
    viking_distance = float(np.linalg.norm(b_vk - a_vk))
    return VikingBasis(a_px, a_vk, viking_distance / pixel_distance)


def image_to_viking(points, basis: VikingBasis) -> np.ndarray:
    """Ports ``util/images/im2vikingXY.m``."""
    return basis.to_viking(points)


def rotation_matrix(angle_deg: float, axis: str = "z") -> np.ndarray:
    """A 3x3 rotation matrix about one axis.  Ports ``rotationMatrix.m``."""
    t = np.deg2rad(float(angle_deg))
    c, s = np.cos(t), np.sin(t)
    axis = axis.lower()
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)
    raise ValueError("axis must be 'x', 'y' or 'z'")


def rotate_z(points, angle_deg: float, center=None) -> np.ndarray:
    """Rotate points about the Z axis.  Ports ``util/images/rotateZ.m``."""
    pts = np.asarray(points, dtype=float)
    squeeze = pts.ndim == 1
    pts = np.atleast_2d(pts)
    if pts.shape[1] == 2:
        pts = np.column_stack([pts, np.zeros(len(pts))])
        was_2d = True
    else:
        was_2d = False
    center = np.zeros(3) if center is None else np.asarray(center, dtype=float).ravel()
    if center.size == 2:
        center = np.append(center, 0.0)
    out = (pts - center) @ rotation_matrix(angle_deg, "z").T + center
    if was_2d:
        out = out[:, :2]
    return out.ravel() if squeeze else out


def rect_to_points(rect) -> np.ndarray:
    """``[x, y, w, h]`` -> the four corner points.  Ports ``util/rect2pts.m``."""
    x, y, w, h = (float(v) for v in np.asarray(rect, dtype=float).ravel()[:4])
    return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
