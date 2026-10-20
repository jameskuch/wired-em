"""Build :class:`~sbfsem.core.structure.Neuron` objects from Viking OData.

Ports ``+sbfsem/+io/NeuronOData.m``, ``+sbfsem/+io/SynapseOData.m`` and
``+sbfsem/+io/GeometryOData.m``, plus the ``pull``/``getSynapses`` orchestration
that lived on ``StructureAPI`` and ``Neuron``.

Deviations (see DEVIATIONS.md):

* Synapse annotations are fetched in batches rather than one request per child
  structure, which was hundreds of round trips per neuron.
* The offline path raises a clear :class:`~sbfsem.io.odata.ODataError` instead of
  dereferencing an empty client (F022).
* Viking retains structure IDs and links for deleted synapses that have no
  locations; those rows are dropped, as the MATLAB ``nullIDs`` guard did.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from ..core.frames import (
    SYNAPSE_COLUMNS,
    edges_from_odata,
    empty_edges,
    nodes_from_odata,
)
from ..core.structure import Neuron, Structure
from ..core.transforms import Transforms
from ..core.types import (
    UNTAGGED_POLICIES,
    VALID_STRUCTURE_TYPE_IDS,
    VikingStructureTypes,
    postsynapse_kind,
)
from ..core.volumes import Volumes
from .odata import ODataClient, parse_datetime
from .tags import parse_tags
from .wkt import rings_in_volume_frame

__all__ = ["NeuronLoader", "load_neuron", "load_structure", "InvalidStructureError"]

log = logging.getLogger(__name__)


class InvalidStructureError(ValueError):
    """The requested ID is not a structure this toolbox can model.

    Ports the ``SBFSEM:NeuronOData:invalidTypeID`` error.
    """


def newest_stamp(rows) -> datetime | None:
    """The latest ``LastModified`` among OData location rows, or ``None``."""
    newest = None
    for row in rows or ():
        stamp = parse_datetime(row.get("LastModified")) if isinstance(row, dict) else None
        if stamp is not None and (newest is None or stamp > newest):
            newest = stamp
    return newest


class NeuronLoader:
    """Fetches and assembles one structure's data."""

    def __init__(self, source, *, client: ODataClient | None = None, **client_kwargs):
        self.volume = Volumes.from_str(source)
        self.source = self.volume.source
        self.client = client or ODataClient(self.volume, **client_kwargs)

    # -- pieces ------------------------------------------------------------
    def fetch_viking(self, structure_id: int) -> dict:
        data = self.client.get_structure(structure_id)
        type_id = data.get("TypeID")
        if type_id is None or int(type_id) not in VALID_STRUCTURE_TYPE_IDS:
            raise InvalidStructureError(
                f"Structure {structure_id} has TypeID {type_id}, which is not a "
                f"modellable structure (expected one of {VALID_STRUCTURE_TYPE_IDS})"
            )
        return data

    def fetch_edges(self, structure_id: int) -> pd.DataFrame:
        rows = self.client.fetch_location_links(structure_id)
        return edges_from_odata(rows, structure_id)

    def fetch_geometries(self, structure_id: int, volume_scale,
                         locations: list[dict] | None = None) -> pd.DataFrame | None:
        """Closed-curve control points, one row per annotation.

        The rings are in microns in the registered volume frame -- the frame
        the disc centres use -- not the section mosaic Viking traces them in;
        see :func:`~sbfsem.io.wkt.rings_in_volume_frame`.  ``Registered`` says
        the ring is Viking's own registered curve, to be drawn as it is.

        ``locations`` are the structure's rows as :meth:`load` already holds
        them.  Viking's full list carries each closed curve's shape columns,
        and the closed curves among its rows are then the rows the closed-curve
        query would return, in the same order (identical on c1178, c192 and
        c2542, 2026-10-16), so Viking is not asked a second time.  A list whose
        closed curves lack their shapes (the thinned rows of a test cassette, or
        a server that stops sending them) is not trusted: the query is made.
        """
        rows = None
        if locations is not None:
            closed = [row for row in locations if row.get("TypeCode") == 6]
            if closed and all(row.get("VolumeShape") or row.get("MosaicShape")
                              for row in closed):
                rows = closed
        if rows is None:
            rows = self.client.fetch_closed_curves(structure_id)
        if not rows:
            return None
        scale_um = np.asarray(volume_scale, dtype=float) / 1e3
        records = []
        for row in rows:
            curves, registered = rings_in_volume_frame(row, volume_scale)
            records.append(
                {
                    "ID": int(row["ID"]),
                    "ParentID": int(row["ParentID"]),
                    "Z": int(row["Z"]),
                    "Zum": float(row["Z"]) * float(scale_um[2]),
                    "Curve": curves,
                    "Registered": bool(registered),
                }
            )
        frame = pd.DataFrame(records)
        return frame.sort_values("Z", ascending=False).reset_index(drop=True)

    def fetch_synapses(self, structure_id: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Child structures with their annotations and links.

        Returns ``(synapses, child_nodes, child_edges)``.
        """
        children = self.client.fetch_children(structure_id)
        if not children:
            return _empty_synapses(), nodes_from_odata([]), empty_edges()

        synapses = pd.DataFrame(children)
        for name, dtype in SYNAPSE_COLUMNS.items():
            if name not in synapses.columns:
                synapses[name] = None
            if dtype.startswith("int"):
                synapses[name] = (
                    pd.to_numeric(synapses[name], errors="coerce").fillna(0).astype("int64")
                )
        synapses["Tags"] = parse_tags(synapses["Tags"].tolist())
        synapses["Label"] = [
            (str(v).strip() if v is not None else "") for v in synapses["Label"]
        ]

        child_ids = synapses["ID"].to_numpy(dtype=np.int64)
        log.info("c%u has %u child structures", structure_id, child_ids.size)

        node_rows = self.client.fetch_locations_by_parents(child_ids)
        child_nodes = nodes_from_odata(node_rows)

        # Viking keeps deleted synapses in the database with a structure ID and
        # links but no locations; drop them.
        present = set(child_nodes["ParentID"].tolist()) if not child_nodes.empty else set()
        null_ids = [int(i) for i in child_ids if int(i) not in present]
        if null_ids:
            log.info("Dropping %u child structures with no locations", len(null_ids))
            synapses = synapses[~synapses["ID"].isin(null_ids)].reset_index(drop=True)

        live_ids = synapses["ID"].to_numpy(dtype=np.int64)
        link_rows = self.client.fetch_links_by_parents(live_ids) if live_ids.size else []
        child_edges = edges_from_odata(link_rows)
        return synapses.reset_index(drop=True), child_nodes, child_edges

    def fetch_psd_partners(self, synapses: pd.DataFrame) -> dict[int, tuple[int, ...]]:
        """The Viking types linked to each postsynapse whose tags name no kind.

        What ``untagged_psd="infer"`` decides from.  The partner is the other
        end of each StructureLink, whichever end the postsynapse is (a few are
        linked backwards).  Batched, and only for the postsynapses that need
        it.  A Viking failure costs the inference, never the cell: those
        postsynapses stay untagged.
        """
        if synapses.empty:
            return {}
        wanted = [
            int(i) for i, t, tags in zip(synapses["ID"], synapses["TypeID"],
                                         synapses["Tags"], strict=True)
            if int(t) == VikingStructureTypes.Postsynapse and postsynapse_kind(tags) is None
        ]
        if not wanted:
            return {}
        keep = set(wanted)
        partners: dict[int, set[int]] = {}
        try:
            rows = self.client.fetch_structure_links(wanted)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("Partners of %u untagged postsynapses not fetched: %s", len(wanted), exc)
            return {}
        for row in rows:
            for own, other in (("Source", "Target"), ("Target", "Source")):
                own_id = row.get(f"{own}ID")
                end = row.get(other)
                if own_id is None or int(own_id) not in keep or not isinstance(end, dict):
                    continue
                if end.get("TypeID") is not None:
                    partners.setdefault(int(own_id), set()).add(int(end["TypeID"]))
        return {k: tuple(sorted(v)) for k, v in partners.items()}

    # -- assembly ----------------------------------------------------------
    def load(
        self,
        structure_id: int,
        *,
        include_synapses: bool = False,
        include_geometries: bool = True,
        transform: Transforms | str = Transforms.Standard,
        untagged_psd: str = "assume",
    ) -> Neuron:
        """Fetch a structure and return a fully-populated :class:`Neuron`.

        ``untagged_psd`` is what a postsynapse whose tags name no kind
        becomes (:data:`~sbfsem.core.types.UNTAGGED_POLICIES`): ``assume``
        conventional as MATLAB did, keep it ``separate``, or ``infer`` it from
        the structure it is linked to (one more batched Viking query).
        """
        if untagged_psd not in UNTAGGED_POLICIES:
            raise ValueError(f"untagged_psd must be one of {UNTAGGED_POLICIES}, not {untagged_psd!r}")
        structure_id = int(structure_id)
        viking = self.fetch_viking(structure_id)
        volume_scale = self.client.get_scale()
        rows = self.client.fetch_locations(structure_id)
        if not rows:
            log.info("No locations for structure %u", structure_id)
        nodes = nodes_from_odata(rows)
        edges = self.fetch_edges(structure_id)

        neuron = Neuron(
            structure_id,
            self.volume,
            viking=viking,
            nodes=nodes,
            edges=edges,
            volume_scale=volume_scale,
            transform=transform,
            last_modified=parse_datetime(viking.get("LastModified")),
            data_stamp=newest_stamp(rows),
            untagged_psd=untagged_psd,
        )

        if include_geometries and neuron.has_closed_curves:
            neuron.geometries = self.fetch_geometries(structure_id, volume_scale, rows)

        if include_synapses:
            synapses, child_nodes, child_edges = self.fetch_synapses(structure_id)
            if untagged_psd == "infer":
                neuron.psd_partners = self.fetch_psd_partners(synapses)
            neuron.merge_child_data(synapses, child_nodes, child_edges)
        return neuron

    def load_structure(self, structure_id: int, **kwargs) -> Structure:
        """Load without the neuron-specific synapse handling."""
        return self.load(structure_id, include_synapses=False, **kwargs)


def _empty_synapses() -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype=dt) for name, dt in SYNAPSE_COLUMNS.items()})

def load_neuron(
    structure_id: int,
    source,
    *,
    include_synapses: bool = False,
    transform: Transforms | str = Transforms.Standard,
    untagged_psd: str = "assume",
    **client_kwargs,
) -> Neuron:
    """Convenience wrapper mirroring MATLAB's ``Neuron(ID, source, synapses)``."""
    loader = NeuronLoader(source, **client_kwargs)
    try:
        return loader.load(
            structure_id, include_synapses=include_synapses, transform=transform,
            untagged_psd=untagged_psd,
        )
    finally:
        loader.client.close()


def load_structure(structure_id: int, source, **kwargs) -> Structure:
    loader = NeuronLoader(source, **{k: v for k, v in kwargs.items() if k != "transform"})
    try:
        return loader.load_structure(structure_id, **{k: v for k, v in kwargs.items() if k == "transform"})
    finally:
        loader.client.close()
