"""Neuron JSON schema v2.

Replaces the write half of ``+sbfsem/+io/JSON.m``.  The MATLAB format was shaped
by jsonlab's nesting quirks — struct arrays came back wrapped in two extra list
levels, numeric arrays as ``{"_ArrayType_": ...}`` objects, and ``LocalName`` as
a cell array of char — which is why reading one back needed
``vertcat(S.nodes{:}{:})`` and why ``NeuronJSON`` could not load any neuron with
synapses at all (F013).

Schema v2 is flat, explicitly versioned and self-describing.  Legacy files still
load through :func:`sbfsem.io.legacy_json.read_legacy_json`, and
:func:`read_neuron_json` dispatches on the presence of ``schema``.

Other MATLAB defects not carried over: ``JSON.export`` destroyed the neuron's
built render model as a side effect (F045), the constructor changed MATLAB's
working directory (F047), and the filename used ``getVolumeAbbrev``, which knew
only four of the eight supported volumes (F048).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..core.structure import Neuron
from ..core.transforms import Transforms
from ..core.types import UNTAGGED_POLICIES, StructureTypes
from ..core.volumes import Volumes

__all__ = ["SCHEMA_VERSION", "neuron_to_dict", "write_neuron_json", "read_neuron_json"]

SCHEMA_VERSION = 2


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame is None or len(frame) == 0:
        return []
    clean = frame.copy()
    for column in clean.columns:
        if clean[column].dtype == object:
            clean[column] = clean[column].map(_jsonable)
    return json.loads(clean.to_json(orient="records", double_precision=10))


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # enum member
        return value.value
    if isinstance(value, np.generic):
        return value.item()
    return value


def neuron_to_dict(neuron) -> dict:
    """Serialize a neuron to the v2 schema."""
    geometries = None
    if getattr(neuron, "geometries", None) is not None and len(neuron.geometries):
        frame = neuron.geometries.copy()
        frame["Curve"] = frame["Curve"].map(
            lambda curves: [np.asarray(c, dtype=float).tolist() for c in (curves or [])]
        )
        geometries = _records(frame)

    return {
        "schema": SCHEMA_VERSION,
        "generator": "SBFSEM-pytools",
        "id": int(neuron.id),
        "source": neuron.source,
        "volumeScale": list(neuron.volume_scale),
        "transform": neuron.transform.value,
        "lastModified": neuron.last_modified.isoformat() if neuron.last_modified else None,
        "viking": _jsonable(neuron.viking),
        "omittedIDs": [int(i) for i in np.asarray(neuron.omitted_ids).ravel()],
        "nodes": _records(neuron.nodes),
        "edges": _records(neuron.edges),
        "synapses": _records(getattr(neuron, "synapses", None)),
        # What a postsynapse whose tags name no kind became, and the partners
        # the "infer" policy decided from: without them a reader can only
        # type such a synapse the default way.
        "untaggedPsd": getattr(neuron, "untagged_psd", None),
        "psdPartners": {str(k): [int(t) for t in v]
                        for k, v in (getattr(neuron, "psd_partners", None) or {}).items()},
        "geometries": geometries,
        # Outlines are in the registered volume frame, beside the discs.
        # Files without this key predate 2026-09-11 and hold the section
        # mosaic frame instead; the reader moves them.
        "curveFrame": "volume",
        # Viking's section correction, when one moved the annotations: the
        # set and how far.  Xum/Yum and the outlines carry it; the nodes'
        # CorrDXnm/CorrDYnm say by how much each moved.
        "sectionCorrection": getattr(neuron, "section_correction", None),
        # The volume's current section transforms, when they moved the
        # annotations from their stored positions (LiveDXnm/LiveDYnm).
        "liveRegistration": getattr(neuron, "live_registration", None),
    }


def write_neuron_json(neuron, path: str | Path, *, indent: int | None = None) -> Path:
    path = Path(path)
    path.write_text(json.dumps(neuron_to_dict(neuron), indent=indent), encoding="utf-8")
    return path


def read_neuron_json(path: str | Path) -> Neuron:
    """Load a neuron from JSON, dispatching between schema v2 and the legacy format."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if int(payload.get("schema", 0)) < SCHEMA_VERSION:
        from ..io.legacy_json import read_legacy_json

        return read_legacy_json(path)
    return _from_dict(payload)


def _from_dict(payload: dict) -> Neuron:
    nodes = pd.DataFrame(payload.get("nodes") or [])
    edges = pd.DataFrame(payload.get("edges") or [])
    synapses = pd.DataFrame(payload.get("synapses") or [])

    policy = payload.get("untaggedPsd")
    neuron = Neuron(
        int(payload["id"]),
        Volumes.from_str(payload["source"]),
        viking=payload.get("viking") or {},
        nodes=nodes if len(nodes) else None,
        edges=edges if len(edges) else None,
        volume_scale=tuple(payload.get("volumeScale") or ()) or None,
        transform=Transforms.from_str(payload.get("transform")),
        omitted_ids=payload.get("omittedIDs") or [],
        untagged_psd=policy if policy in UNTAGGED_POLICIES else None,
    )
    neuron.psd_partners = _psd_partners(payload.get("psdPartners"))
    if len(synapses):
        neuron.synapses = synapses
        neuron.setup_synapses()
        _restore_synapse_kinds(neuron, synapses)
    neuron.section_correction = payload.get("sectionCorrection") or None
    neuron.live_registration = payload.get("liveRegistration") or None
    geometries = payload.get("geometries")
    if geometries:
        frame = pd.DataFrame(geometries)
        frame["Curve"] = frame["Curve"].map(
            lambda curves: [np.asarray(c, dtype=float) for c in (curves or [])]
        )
        if "Registered" not in frame.columns:
            frame["Registered"] = False
        if payload.get("curveFrame") != "volume":
            frame = _curves_into_volume_frame(frame, neuron.nodes, neuron.volume_scale)
        neuron.geometries = frame
    return neuron


def _psd_partners(raw) -> dict[int, tuple[int, ...]]:
    """The file's ``psdPartners`` (a postsynapse ID to the structure IDs it is linked
    to) as the neuron holds it.  A key that is not a map of lists of IDs is an
    unreadable file: a ``ValueError``, which is what the import route answers 400 for
    (a bare TypeError or AttributeError was a 500)."""
    if not raw:
        return {}
    try:
        return {int(k): tuple(int(t) for t in v) for k, v in raw.items()}
    except (TypeError, AttributeError, ValueError) as exc:
        raise ValueError(
            "psdPartners must map each postsynapse ID to the list of structure IDs it is "
            f"linked to ({type(exc).__name__}: {exc})") from exc


def _restore_synapse_kinds(neuron: Neuron, written: pd.DataFrame) -> None:
    """Put back what the file says each synapse is.

    ``setup_synapses`` types a postsynapse from its tags and the neuron's
    policy, which a file written before the policy was recorded cannot give
    it back (an inferred or untagged postsynapse came out ConvPost: RC1 c606
    lost 649 untagged and 256 of its 262 ribbon postsynapses).  The file's own
    ``LocalName``, ``TagStatus`` and ``SuggestedTag`` are the record, and win.
    """
    if "LocalName" not in written.columns or "ID" not in written.columns:
        return
    kinds = {}
    for syn_id, name in zip(written["ID"], written["LocalName"], strict=True):
        try:
            kinds[int(syn_id)] = StructureTypes.from_str(name)
        except (ValueError, TypeError):
            continue  # a name this version does not know: keep the tags' reading
    synapses = neuron.synapses.copy()
    ids = synapses["ID"].astype(int)
    synapses["LocalName"] = [kinds.get(int(i), old)
                             for i, old in zip(ids, synapses["LocalName"], strict=True)]
    for column in ("TagStatus", "SuggestedTag"):
        if column in written.columns:
            by_id = dict(zip(written["ID"].astype(int), written[column].fillna("").astype(str),
                             strict=True))
            synapses[column] = [by_id.get(int(i), old)
                                for i, old in zip(ids, synapses[column], strict=True)]
    neuron.synapses = synapses


def _curves_into_volume_frame(frame: pd.DataFrame, nodes: pd.DataFrame,
                              volume_scale) -> pd.DataFrame:
    """Move outlines a pre-2026-09-11 file holds in the section mosaic frame
    beside the discs, by each outline's own node (both frames travel with it).
    """
    from ..io.wkt import volume_frame_shift_um

    needed = {"ID", "X", "Y", "VolumeX", "VolumeY"}
    if nodes is None or not len(nodes) or not needed <= set(nodes.columns):
        return frame
    by_id = nodes.drop_duplicates("ID").set_index("ID")
    shifted = []
    for _, row in frame.iterrows():
        curves = row["Curve"] or []
        try:
            node = by_id.loc[int(row["ID"])]
        except (KeyError, ValueError, TypeError):
            shifted.append(curves)
            continue
        shift = volume_frame_shift_um(node.to_dict(), volume_scale)
        shifted.append([np.asarray(c, dtype=float) + shift for c in curves])
    frame = frame.copy()
    frame["Curve"] = shifted
    return frame
