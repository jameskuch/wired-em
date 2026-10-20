"""Why the viewer's Kernel R/d stops at 2.5 (2.7.1).

The convolution kernel's weight makes an infinitely long neurite keep its radius, so a
body shorter than its kernel's reach sums to less than the iso-value.  A soma drawn as
stacked discs is about as tall as it is wide: under a kernel wider than 2.5 radii
nothing of it reaches the surface, and Temporal c118's soma was gone at 4 (none of its
67 soma discs inside the surface).  The panel stops where such a stack still holds; the
server keeps accepting what it always did.
"""

from __future__ import annotations

import numpy as np

from sbfsem.geometry.convolution import (
    MAX_PANEL_SUPPORT,
    THRESHOLD,
    SegmentKernel,
    merge_short_segments,
)
from sbfsem.service.render_schema import field_by_key


def _stack_field(radius: float, height: float, support: float) -> tuple[float, float]:
    """The field at mid-height of a stack of discs (one per 75 nm section), on its
    axis and at its rim, in units of the iso-value."""
    z = np.arange(0.0, height + 1e-9, 0.075)
    points = np.column_stack([np.zeros_like(z), np.zeros_like(z), z])
    pts, rad = merge_short_segments(points, np.full(len(z), radius), support=support)
    kernels = [SegmentKernel.from_pair(pts[i], pts[i + 1], rad[i], rad[i + 1], support=support)
               for i in range(len(pts) - 1)]
    probe = np.array([[0.0, 0.0, height / 2], [radius, 0.0, height / 2]])
    axis, rim = sum(k.potential(probe) for k in kernels) / THRESHOLD
    return float(axis), float(rim)


def test_a_body_as_tall_as_it_is_wide_holds_up_to_the_panels_widest_kernel():
    axis, _ = _stack_field(5.0, 10.0, MAX_PANEL_SUPPORT)
    assert axis > 1.0, "at the panel's widest kernel the soma's axis is still inside"
    axis, rim = _stack_field(5.0, 10.0, 3.0)
    assert axis < 1.0 and rim < 1.0, "a wider kernel leaves nothing of it"


def test_a_long_process_keeps_its_radius_at_any_width():
    for support in (1.6, 2.0, MAX_PANEL_SUPPORT, 3.0, 4.0):
        _, rim = _stack_field(0.5, 20.0, support)
        assert abs(rim - 1.0) < 0.02, support


def test_the_panel_stops_at_it_and_the_server_still_takes_up_to_4():
    field = field_by_key("conv_support")
    assert field.max == MAX_PANEL_SUPPORT == 2.5
    assert dict(field.server_bounds()) == {"ge": 1.2, "le": 4.0}
    assert field.min <= field.default <= field.max
