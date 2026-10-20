"""Ports the exhaustive ``testSynapseLocalName`` assertions from test/NeuronTest.m."""

from __future__ import annotations

import pytest

from sbfsem.core.types import StructureTypes, VikingStructureTypes

# (TypeID, Tags) -> expected local name.  Taken from the 13 cases NeuronTest
# checks against structures 7233-7246 in NeitzInferiorMonkey.
FROM_VIKING_CASES = [
    (35, "", "ConvPost"),
    (34, "", "ConvPre"),
    (73, "", "RibbonPre"),
    (35, "Bipolar;Ribbon;Glutamate", "RibbonPost"),
    (28, "", "GapJunction"),
    (35, "Conventional;NTA", "BasalNTA"),
    (35, "Conventional;GABA", "GABAPost"),
    (35, "Conventional;mNTA", "BasalMNTA"),
    (35, "Conventional;TA", "BasalTA"),
    (85, "", "Desmosome"),
    (244, "", "Unknown"),
    (189, "", "BipConvPre"),
    (35, "Bipolar;Conventional;Glutamate", "BipConvPost"),
]


@pytest.mark.parametrize(("type_id", "tags", "expected"), FROM_VIKING_CASES)
def test_from_viking(type_id, tags, expected):
    assert StructureTypes.from_viking(type_id, tags).value == expected


@pytest.mark.parametrize("type_id", range(257, 271))
def test_marc_lab_types_have_local_names(type_id):
    """DEVIATIONS 5.8: MATLAB stopped at 256 and called these Unknown."""
    local = StructureTypes.from_viking(type_id)
    assert local.value == VikingStructureTypes(type_id).name
    assert local.color != (0.0, 0.0, 0.0)


def test_from_viking_gaba_pre():
    assert StructureTypes.from_viking(34, "GABA") is StructureTypes.GABAPre


def test_from_viking_unmapped_raises():
    with pytest.raises(ValueError):
        StructureTypes.from_viking(VikingStructureTypes.Bipolar)


def test_is_boundary_marker_does_not_raise():
    # F001: the MATLAB version named a member that does not exist.
    assert StructureTypes.IPLGCLBoundary.is_boundary_marker
    assert StructureTypes.INLIPLBoundary.is_boundary_marker
    assert StructureTypes.CHBoundary.is_boundary_marker
    assert not StructureTypes.RibbonPre.is_boundary_marker


def test_predicates():
    assert StructureTypes.RibbonPre.is_pre
    assert StructureTypes.RibbonPost.is_post
    assert StructureTypes.GapJunction.is_undirected
    assert StructureTypes.RibbonPre.is_synapse
    assert not StructureTypes.Cell.is_synapse
    assert not StructureTypes.Plaque.is_synapse


def test_from_str_covers_every_member():
    # The MATLAB fromStr knew only six of 43 members.
    for member in StructureTypes:
        assert StructureTypes.from_str(member.name.lower()) is member
        assert StructureTypes.from_str(member) is member
    with pytest.raises(ValueError):
        StructureTypes.from_str("NotAType")


def test_local_name_dispatches_on_viking_type():
    # F146: the MATLAB localName switched on StructureTypes members.
    assert VikingStructureTypes.Conventional.local_name("GABA") == "GABAPre"
    assert VikingStructureTypes.Conventional.local_name() == "ConvPre"
    assert VikingStructureTypes.INLIPLBoundary.local_name() == "INLBoundary"
    assert VikingStructureTypes.IPLGCLBoundary.local_name() == "GCLBoundary"
    assert VikingStructureTypes.RibbonSynapse.local_name() == "RibbonPre"
    assert VikingStructureTypes.Rootlet.local_name() == "Rootlet"


def test_colors():
    assert StructureTypes.Cell.color == (0.2, 0.2, 0.2)
    # MATLAB drew the types it had no color for black; none is now (DEVIATIONS 5.10).
    assert StructureTypes.Rootlet.color != (0.0, 0.0, 0.0)


def test_every_member_has_a_color_that_is_not_black():
    black = [m.name for m in StructureTypes if m.color == (0.0, 0.0, 0.0)]
    assert black == []


def test_members_share_a_color_only_where_matlab_did():
    by_color: dict[tuple, list[str]] = {}
    for member in StructureTypes:
        by_color.setdefault(member.color, []).append(member.name)
    shared = {tuple(sorted(names)) for names in by_color.values() if len(names) > 1}
    assert shared == {
        ("GABAPost", "GABAPre"), ("BasalMNTA", "BasalNTA"), ("BasalTA", "ConvPre"),
    }
