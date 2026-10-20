from __future__ import annotations

import numpy as np
import pytest

from sbfsem.core.registry import read_boundary_markers, read_omitted_ids, read_xy_offset
from sbfsem.core.transforms import Transforms, viking2micron


def test_scale_handles_every_column_count():
    # F152: MATLAB had no Nx2 branch and returned an unassigned value.
    scale = (7.5, 7.5, 90.0)
    xyz = np.array([[11228.8, 3348.52, 1578.0]])
    np.testing.assert_allclose(
        Transforms.scale(xyz, scale), [[84.216, 25.1139, 142.02]], rtol=1e-9
    )
    np.testing.assert_allclose(Transforms.scale(xyz[:, :2], scale), [[84.216, 25.1139]])
    np.testing.assert_allclose(Transforms.scale(np.array([95.64]), scale), [0.7173])
    with pytest.raises(ValueError):
        Transforms.scale(np.zeros((2, 4)), scale)


def test_viking2micron_matches_matlab_fixture_values():
    np.testing.assert_allclose(
        viking2micron(np.array([[11228.8, 3348.52, 1578.0]]), "i"),
        [[84.216, 25.1139, 142.02]],
        rtol=1e-9,
    )


def test_from_str():
    assert Transforms.from_str("standard") is Transforms.Standard
    assert Transforms.from_str("local") is Transforms.Custom
    assert Transforms.from_str("none") is Transforms.NoTransform
    assert Transforms.from_str(Transforms.Custom) is Transforms.Custom
    with pytest.raises(ValueError):
        Transforms.from_str("bogus")


def test_translate_uses_section_keyed_offsets():
    offsets = read_xy_offset("i")
    row = offsets[offsets[:, 0] == 5][0]
    x, y = Transforms.translate(np.array([[100.0, 200.0, 5.0]]), "i")
    assert x[0] == pytest.approx(100.0 + row[1])
    assert y[0] == pytest.approx(200.0 + row[2])


def test_translate_rejects_out_of_range_sections():
    with pytest.raises(IndexError):
        Transforms.translate(np.array([[0.0, 0.0, 99999.0]]), "i")


def test_nasal_monkey_correction_is_identity_at_the_pivot():
    xyz = np.array([[1000.0, 2000.0, 1177.0]])
    x, y = Transforms.nasal_monkey(xyz, (5.0, 5.0, 50.0))
    assert x[0] == pytest.approx(1000.0)
    assert y[0] == pytest.approx(2000.0)


def test_packaged_data_files_load():
    assert read_xy_offset("i").shape[1] == 3
    assert read_xy_offset("t") is None
    assert read_boundary_markers("i", "GCL").shape[1] == 3
    assert read_boundary_markers("i", "INL").shape[1] == 3
    assert read_omitted_ids(1441, "i").size > 0
    assert read_omitted_ids(-1, "i").size == 0
    with pytest.raises(ValueError):
        read_boundary_markers("i", "IPL")
