from __future__ import annotations

import pytest

from sbfsem.core.volumes import Volumes, validate_source


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("i", "NeitzInferiorMonkey"),
        ("inferior", "NeitzInferiorMonkey"),
        ("INF", "NeitzInferiorMonkey"),
        ("t", "NeitzTemporalMonkey"),
        ("nasal", "NeitzNasalMonkey"),
        ("cped", "NeitzCped"),
        # Display names round-trip, so a UI value can be sent straight back.
        ("Inferior Monkey", "NeitzInferiorMonkey"),
        ("inferior monkey", "NeitzInferiorMonkey"),
        ("InferiorMonkey", "NeitzInferiorMonkey"),
        ("Temporal Monkey", "NeitzTemporalMonkey"),
        ("Nasal Monkey", "NeitzNasalMonkey"),
        ("Cped", "NeitzCped"),
        # The Marc lab rabbit volumes, with their MATLAB validateSource aliases.
        ("RC1", "RC1"),
        ("rc1", "RC1"),
        ("marcrc1", "RC1"),
        ("r", "RC1"),
        ("RC2", "RC2"),
        ("marcrc2", "RC2"),
        ("RPC1", "RPC1"),
        ("marcrpc1", "RPC1"),
        ("RPC2", "RPC2"),
        ("marcrpc2", "RPC2"),
    ],
)
def test_validate_source(alias, expected):
    assert validate_source(alias) == expected


def test_marc_rabbit_volumes():
    """The four Marc lab volumes are served from the same host as the Neitz
    volumes; their cached scales are the live OData Scale values, not MATLAB's
    (2.18, 2.18, 90) fallback whose section thickness was wrong."""
    for name in ("RC1", "RC2", "RPC1", "RPC2"):
        volume = Volumes.from_str(name)
        assert volume.animal == "rabbit"
        assert volume.cached_scale == (2.176, 2.176, 70.0)
        assert volume.is_odata
        assert volume.service_root == f"https://websvc.codepharm.net/{name}/OData/"
        assert not volume.has_custom_transform
        assert volume.n_sections is None
    assert Volumes.RC1.abbrev == "r"
    assert Volumes.RC1.has_boundary  # MATLAB hasBoundary lists MarcRC1
    # RC2's markers arrived after MATLAB's list; cached 2026-09-11.
    assert Volumes.RC2.has_boundary
    assert not Volumes.NeitzCped.has_boundary


def test_display_names():
    assert Volumes.NeitzInferiorMonkey.display_name == "Inferior Monkey"
    assert Volumes.NeitzTemporalMonkey.display_name == "Temporal Monkey"
    assert Volumes.NeitzNasalMonkey.display_name == "Nasal Monkey"
    assert Volumes.NeitzCped.display_name == "Cped"
    # The wire identifier is deliberately unchanged: it is in API paths, cache
    # filenames and previously exported JSON.
    assert Volumes.NeitzInferiorMonkey.source == "NeitzInferiorMonkey"


def test_validate_source_rejects_unknown():
    with pytest.raises(ValueError):
        validate_source("not-a-volume")
    with pytest.raises(TypeError):
        validate_source(42)  # F064: MATLAB silently passed this through


def test_nasal_url_alias_is_preserved():
    assert Volumes.NeitzNasalMonkey.service_root.endswith("/NeitzNM/OData/")


def test_demo_volume_is_not_odata():
    assert not Volumes.DemoVolume.is_odata
    with pytest.raises(ValueError):
        _ = Volumes.DemoVolume.service_root


def test_scale_and_flags():
    assert Volumes.NeitzInferiorMonkey.cached_scale == (7.5, 7.5, 90.0)
    assert Volumes.NeitzNasalMonkey.info.force_cached_scale
    assert Volumes.NeitzInferiorMonkey.has_cones
    assert not Volumes.NeitzTemporalMonkey.has_cones
    assert Volumes.NeitzInferiorMonkey.n_sections == 1893
    # MATLAB raised for volumes it did not know; None is the honest answer.
    assert Volumes.NeitzCped.n_sections is None
