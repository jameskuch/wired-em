"""Postsynapse tags: read as a set, and what an untagged one becomes.

Crystal Sigulinsky (2026-09-22): RC1's early postsynapses, annotated before
tags, were all counted conventional, which put her ganglion cell's I:E ratio
"off the charts".  The production cache that day held 7,837 untagged RC1
postsynapses of 10,008, 2,479 of them linked to a ribbon.
"""

from __future__ import annotations

import pandas as pd
import pytest

from sbfsem.core.structure import Neuron
from sbfsem.core.types import StructureTypes, postsynapse_kind, tag_set
from sbfsem.io.loader import NeuronLoader
from sbfsem.network.links import link_report

S = StructureTypes


@pytest.mark.parametrize(("tags", "expected"), [
    # The MATLAB table's own strings keep their meaning.
    ("Bipolar;Ribbon;Glutamate", S.RibbonPost),
    ("Ribbon", S.RibbonPost),
    ("Ribbon;Glutamate", S.RibbonPost),
    ("Bipolar;Conventional;Glutamate", S.BipConvPost),
    ("Conventional;GABA", S.GABAPost),
    ("GABA", S.GABAPost),
    ("Conventional;TA", S.BasalTA),
    ("Conventional;NTA", S.BasalNTA),
    ("Conventional;mNTA", S.BasalMNTA),
    ("Conventional", S.ConvPost),
    # Orders and cases seen in RC1, RC2, RPC1 and RPC2, which MATLAB's
    # whole-string lookup counted as conventional.
    ("Ribbon;Bipolar;Glutamate", S.RibbonPost),
    ("Bipolar;Glutamate;Ribbon", S.RibbonPost),
    ("Glutamate;Ribbon;Bipolar", S.RibbonPost),
    ("PR(cone);Glutamate;Ribbon", S.RibbonPost),
    ("Conventional;Bipolar;Glutamate", S.BipConvPost),
    ("conventional", S.ConvPost),
    ("Conventional;DCV;1.0", S.ConvPost),
    # Tags that do not say what it is.
    ("", None),
    ("AC", None),
    ("Doubtful", None),
    ("BC;CBb4w", None),
    ("Conventional;Ribbon", None),   # contradicts itself
])
def test_postsynapse_kind_reads_tags_as_a_set(tags, expected):
    assert postsynapse_kind(tags) is expected


def test_tag_set_reads_the_raw_viking_blob():
    raw = '<Structure><Attrib Name="Ribbon"/><Attrib Name="Glutamate"/></Structure>'
    assert tag_set(raw) == {"ribbon", "glutamate"}
    assert postsynapse_kind(raw) is S.RibbonPost
    assert tag_set(None) == frozenset()


def test_from_viking_keeps_matlab_default_and_can_keep_untagged_apart():
    assert S.from_viking(35, "") is S.ConvPost             # MATLAB
    assert S.from_viking(35, "", untagged="UntaggedPost") is S.UntaggedPost
    assert S.from_viking(35, "Glutamate;Ribbon") is S.RibbonPost
    assert S.from_viking(34, "gaba") is S.GABAPre
    assert S.UntaggedPost.is_post and S.UntaggedPost.is_synapse


def _neuron(policy, partners=None):
    nodes = pd.DataFrame({
        "ID": [1, 2, 3, 4, 5, 6], "ParentID": [100, 200, 201, 202, 203, 204],
        "VolumeX": [0.0] * 6, "VolumeY": [0.0] * 6, "Z": [1, 2, 3, 4, 5, 6],
        "Radius": [1.0] * 6, "X": [0.0] * 6, "Y": [0.0] * 6,
        "OffEdge": [False] * 6, "Terminal": [False] * 6, "TypeCode": [1] * 6,
    })
    synapses = pd.DataFrame({
        "ID": [200, 201, 202, 203, 204], "ParentID": [100] * 5,
        "TypeID": [35, 35, 35, 35, 73],
        "Tags": ["Bipolar;Glutamate;Ribbon", "", "", "AC", ""],
        "Label": [""] * 5,
    })
    neuron = Neuron(100, "i", nodes=nodes.iloc[:1], untagged_psd=policy)
    neuron.psd_partners = partners or {}
    neuron.merge_child_data(synapses, nodes.iloc[1:], None)
    return neuron


def _names(neuron):
    return {int(r.ID): (r.LocalName.value, r.TagStatus, r.SuggestedTag)
            for r in neuron.synapses.itertuples()}


def test_assume_is_matlab_and_says_so():
    names = _names(_neuron("assume"))
    assert names[200] == ("RibbonPost", "tagged", "")
    assert names[201] == ("ConvPost", "assumed", "")
    assert names[203] == ("ConvPost", "assumed", "")
    assert names[204] == ("RibbonPre", "", "")


def test_separate_never_guesses():
    names = _names(_neuron("separate", {201: (73,)}))
    assert names[201] == ("UntaggedPost", "untagged", "")
    assert names[203] == ("UntaggedPost", "untagged", "")


def test_infer_takes_the_linked_partner_and_only_an_unambiguous_one():
    neuron = _neuron("infer", {201: (73,), 202: (34, 73), 203: (189,)})
    names = _names(neuron)
    assert names[201] == ("RibbonPost", "inferred", "Ribbon")
    assert names[202] == ("UntaggedPost", "untagged", "")          # two kinds
    assert names[203] == ("BipConvPost", "inferred", "Bipolar;Conventional;Glutamate")
    worklist = neuron.untagged_postsynapses()
    assert worklist["ID"].tolist() == [201, 202, 203]
    # A partner type that implies nothing is not outvoted.
    assert _names(_neuron("infer", {201: (73, 244)}))[201][1] == "untagged"


def test_unknown_policy_is_refused():
    with pytest.raises(ValueError):
        _neuron("guess")


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.asked = None

    def fetch_structure_links(self, ids):
        self.asked = list(ids)
        return self.rows


def test_loader_asks_only_about_untagged_postsynapses_and_reads_both_ends():
    synapses = pd.DataFrame({
        "ID": [200, 201, 202, 204], "TypeID": [35, 35, 35, 73],
        "Tags": ["Ribbon", "", "AC", ""],
    })
    client = _Client([
        {"SourceID": 900, "TargetID": 201, "Source": {"TypeID": 73}, "Target": {"TypeID": 35}},
        # Linked backwards: the postsynapse is the Source.
        {"SourceID": 202, "TargetID": 901, "Source": {"TypeID": 35}, "Target": {"TypeID": 34}},
    ])
    loader = NeuronLoader.__new__(NeuronLoader)
    loader.client = client
    assert loader.fetch_psd_partners(synapses) == {201: (73,), 202: (34,)}
    assert client.asked == [201, 202]


def test_a_viking_failure_costs_the_inference_not_the_cell():
    class Down:
        def fetch_structure_links(self, ids):
            raise OSError("timed out")

    loader = NeuronLoader.__new__(NeuronLoader)
    loader.client = Down()
    synapses = pd.DataFrame({"ID": [201], "TypeID": [35], "Tags": [""]})
    assert loader.fetch_psd_partners(synapses) == {}


def test_link_report_carries_tags_status_and_a_location():
    neuron = _neuron("infer", {201: (73,)})
    links = pd.DataFrame({
        "NeuronID": [555.0, float("nan")], "SynapseID": [201, 203],
        "SynapseType": ["RibbonPost", "UntaggedPost"], "NeuronLabel": ["CBb", ""],
        "Direction": ["post", "post"],
    })
    table = link_report(neuron, links).set_index("SynapseID")
    assert table.loc[201, "TagStatus"] == "inferred"
    assert table.loc[201, "SuggestedTag"] == "Ribbon"
    assert table.loc[203, "Tags"] == "AC"
    assert table.loc[203, "TagStatus"] == "untagged"
    assert int(table.loc[201, "LocationID"]) == 3


def test_ie_ratio_counts_inferred_and_leaves_untagged_out():
    from sbfsem.analysis.synapse import ie_ratio

    result = ie_ratio(_neuron("infer", {201: (73,), 203: (34,)}))
    # 200 tagged ribbon, 201 inferred ribbon; 203 inferred conventional;
    # 202 untagged and in neither count.
    assert (result.n_excitatory, result.n_inhibitory) == (2, 1)
    assert (result.n_inferred, result.n_untagged, result.n_assumed) == (2, 1, 0)
    matlab = ie_ratio(_neuron("assume"))
    assert (matlab.n_excitatory, matlab.n_inhibitory, matlab.n_assumed) == (1, 3, 3)
