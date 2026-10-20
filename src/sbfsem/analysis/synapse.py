"""Synapse-level analyses.

Ports ``util/analysis/ieRatio.m``, ``util/analysis/getSynapseSomaDistance.m``,
``util/analysis/getSynapseSomaPathLength.m``,
``util/analysis/getSynapseDirection.m``, ``util/analysis/labelSearch.m`` and
``+sbfsem/+analysis/ReciprocalSynapses.m``.

Deviations (see DEVIATIONS.md):

* ``ReciprocalSynapses`` overwrote ``postXYZ`` on every pass of its inner loop
  and then measured against whichever candidate the loop happened to end on, so
  the reported "minimum reciprocal distance" was not a minimum over anything.
  It also never applied ``searchRadius``.  Both fixed here.
* ``getSynapseSomaPathLength`` relied on ``getSynapseXYZ`` returning empty when a
  synapse type is absent; it returned ``[NaN NaN NaN]``, so the guard was dead
  and the caller got a plausible micron path length measured to an arbitrary
  unrelated annotation (F125/F131).  This raises instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core.structure import SynapseNotFoundError
from ..core.types import StructureTypes
from .dendrite import nearest_annotation, path_distance

__all__ = [
    "IERatio",
    "ie_ratio",
    "synapse_soma_distance",
    "synapse_soma_path_length",
    "synapse_direction",
    "reciprocal_synapses",
]


@dataclass
class IERatio:
    """Inhibition/excitation balance for one neuron.

    ``ieRatio.m``'s header documented "ratio of inhibitory:excitatory synapses"
    but computed ``numInh / (numInh + numExc)`` — the inhibitory *proportion*,
    bounded by 1 (F127).  Rather than pick one and silently change what published
    numbers mean, both are reported: :attr:`inhibitory_fraction` reproduces the
    MATLAB value and :attr:`ratio` is the documented I:E ratio.
    """

    #: numInh / numExc, the documented inhibitory-to-excitatory ratio.
    ratio: float
    #: numInh / (numInh + numExc), what the MATLAB code actually returned.
    inhibitory_fraction: float
    n_excitatory: int
    n_inhibitory: int
    #: Of the counts, postsynapses typed from their linked partner because
    #: their Viking tags do not say (``untagged_psd="infer"``).
    n_inferred: int = 0
    #: Of ``n_inhibitory``, postsynapses assumed conventional for the same
    #: reason (``untagged_psd="assume"``, MATLAB's behavior).
    n_assumed: int = 0
    #: Postsynapses left out of both counts: tags that do not say, and no
    #: partner that does.
    n_untagged: int = 0

    def as_dict(self) -> dict:
        return {
            "ratio": self.ratio,
            "inhibitory_fraction": self.inhibitory_fraction,
            "n_excitatory": self.n_excitatory,
            "n_inhibitory": self.n_inhibitory,
            "n_inferred": self.n_inferred,
            "n_assumed": self.n_assumed,
            "n_untagged": self.n_untagged,
        }


def ie_ratio(neuron) -> IERatio:
    """Inhibitory versus excitatory post-synapse counts.

    Excitation is ribbon post-synapses and inhibition conventional
    post-synapses, as the MATLAB docstring defines them.  A neuron with neither
    yields NaN for both measures rather than a silent 0/0.
    """
    n_exc = neuron.get_synapse_count(StructureTypes.RibbonPost)
    n_inh = neuron.get_synapse_count(StructureTypes.ConvPost)
    total = n_exc + n_inh
    synapses = getattr(neuron, "synapses", None)
    n_inferred = n_assumed = n_untagged = 0
    if synapses is not None and not synapses.empty and "TagStatus" in synapses.columns:
        kind = synapses["LocalName"]
        counted = kind.isin((StructureTypes.RibbonPost, StructureTypes.ConvPost))
        n_inferred = int((counted & (synapses["TagStatus"] == "inferred")).sum())
        n_assumed = int((counted & (synapses["TagStatus"] == "assumed")).sum())
        n_untagged = int((kind == StructureTypes.UntaggedPost).sum())
    return IERatio(
        ratio=float(n_inh / n_exc) if n_exc else float("nan"),
        inhibitory_fraction=float(n_inh / total) if total else float("nan"),
        n_excitatory=n_exc,
        n_inhibitory=n_inh,
        n_inferred=n_inferred,
        n_assumed=n_assumed,
        n_untagged=n_untagged,
    )


def synapse_soma_distance(neuron, synapse_type) -> np.ndarray:
    """Straight-line distance from the soma to each synapse, in microns."""
    xyz = neuron.get_synapse_xyz(synapse_type)
    if xyz.size == 0:
        return np.empty(0, dtype=float)
    soma = np.asarray(neuron.get_soma_xyz(), dtype=float).ravel()
    return np.linalg.norm(xyz - soma, axis=1)


def synapse_soma_path_length(neuron, synapse_type) -> pd.DataFrame:
    """Distance from the soma to each synapse *along the dendrite*, in microns.

    One row per synapse that has a marker annotation, ``SynapseID`` being the
    structure that marker belongs to.  (The IDs used to be paired with the
    markers by position, though the synapse table and the node table are in
    different orders and a synapse with no annotations has no marker: 65 of
    184 rows of c1178's ConvPost carried another synapse's ID.)
    """
    markers = neuron.get_synapse_nodes_by_type(synapse_type)
    if markers.empty:
        raise SynapseNotFoundError(f"No {synapse_type} annotations on c{neuron.id}")
    xyz = markers[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    ids = markers["ParentID"].to_numpy(dtype=np.int64)
    soma_id = neuron.get_soma_id()
    rows = []
    for i, point in enumerate(xyz):
        annotation_id, offset = nearest_annotation(neuron, point)
        try:
            distance, _ = path_distance(neuron, soma_id, annotation_id)
        except ValueError:
            distance = float("nan")  # not connected to the soma
        rows.append(
            {
                "SynapseID": int(ids[i]),
                "LocationID": annotation_id,
                "OffsetToAnnotation": offset,
                "PathLength": distance,
            }
        )
    return pd.DataFrame(rows)


def synapse_direction(neuron, synapse_type) -> pd.DataFrame:
    """Vector from the soma to each synapse, with azimuth and elevation."""
    xyz = neuron.get_synapse_xyz(synapse_type)
    soma = np.asarray(neuron.get_soma_xyz(), dtype=float).ravel()
    if xyz.size == 0:
        return pd.DataFrame(columns=["dx", "dy", "dz", "azimuth", "elevation", "distance"])
    delta = xyz - soma
    distance = np.linalg.norm(delta, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        elevation = np.degrees(np.arcsin(np.clip(delta[:, 2] / distance, -1, 1)))
    return pd.DataFrame(
        {
            "dx": delta[:, 0],
            "dy": delta[:, 1],
            "dz": delta[:, 2],
            "azimuth": np.degrees(np.arctan2(delta[:, 1], delta[:, 0])),
            "elevation": elevation,
            "distance": distance,
        }
    )


def reciprocal_synapses(neuron, links: pd.DataFrame, search_radius: float = 0.5) -> pd.DataFrame:
    """Pre/post synapse pairs onto the same partner within ``search_radius``.

    ``links`` is the table from :func:`sbfsem.network.links.linked_neurons`,
    with ``NeuronID``, ``SynapseID`` and ``SynapseType`` columns.  The 500 nm
    default is from Tsukamoto & Omi (2016).
    """
    if links is None or links.empty:
        return pd.DataFrame(columns=["NeuronID", "PreID", "PostID", "Distance"])

    is_pre = links["SynapseType"].map(lambda t: StructureTypes.from_str(t).is_pre)
    is_post = links["SynapseType"].map(lambda t: StructureTypes.from_str(t).is_post)
    pre = links[is_pre]
    post = links[is_post]
    shared = sorted(set(pre["NeuronID"]) & set(post["NeuronID"]))

    coords = {
        int(pid): frame[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
        for pid, frame in neuron.nodes.groupby("ParentID")
    }

    rows = []
    for partner in shared:
        pre_ids = pre.loc[pre["NeuronID"] == partner, "SynapseID"].astype(int).tolist()
        post_ids = post.loc[post["NeuronID"] == partner, "SynapseID"].astype(int).tolist()
        for a in pre_ids:
            xa = coords.get(a)
            if xa is None or xa.size == 0:
                continue
            for b in post_ids:
                xb = coords.get(b)
                if xb is None or xb.size == 0:
                    continue
                # Minimum over every annotation pair, not the last one seen.
                distance = float(
                    np.min(np.linalg.norm(xa[:, None, :] - xb[None, :, :], axis=2))
                )
                if distance <= search_radius:
                    rows.append(
                        {
                            "NeuronID": int(partner),
                            "PreID": int(a),
                            "PostID": int(b),
                            "Distance": distance,
                        }
                    )
    frame = pd.DataFrame(rows, columns=["NeuronID", "PreID", "PostID", "Distance"])
    return frame.sort_values("Distance").reset_index(drop=True)
