"""Resolving a neuron's synaptic partners from Viking StructureLinks.

Ports ``util/network/getLinkedNeurons.m``, ``getAllLinkedNeurons.m``,
``getLinkedNeuronsByType.m``, ``countLinkedNeurons.m``,
``util/analysis/getAllLinkedNeurons_sorted.m`` and the deprecated
``util/import/getStructureLinks.m`` and ``getPresynapticNeurons.m``.

Deviations (see DEVIATIONS.md):

* All of these issued one OData request per synapse — hundreds per neuron.
  :meth:`sbfsem.io.odata.ODataClient.fetch_structure_links` batches them.
* ``getAllLinkedNeurons`` initialized its accumulator to ``[]`` and then assigned
  a struct field to it when a neuron had no synapses, which errored before
  ``Neuron.getLinks``'s ``isempty`` guard could run (F021).  Empty in, empty out.
* ``getLinkedNeurons`` converted a char synapse name with the enumeration
  constructor rather than ``fromStr`` (F231); :meth:`StructureTypes.from_str`
  handles every member.
* ``getPresynapticNeurons.m`` defined a function named ``getLinkedNeurons``,
  shadowing the newer three-output version depending on path order (F056).  It
  is not ported; :func:`linked_neurons` covers both directions.
"""

from __future__ import annotations

import logging

import pandas as pd

from ..core.types import StructureTypes

log = logging.getLogger(__name__)

__all__ = ["linked_neurons", "all_linked_neurons", "count_linked_neurons",
           "LINK_COLUMNS", "REPORT_COLUMNS", "LINK_ORDERS", "attach_synapse_xyz", "attach_tags",
           "sort_links", "link_report", "links_to_csv", "links_to_xlsx"]

#: ``PartnerSynapseID`` is the structure at the other end of the StructureLink
#: (the partner cell's side of the same synapse), so a synapse seen from both
#: of its cells is one link, not two (:func:`sbfsem.network.graph.network_from_links`).
LINK_COLUMNS = ["NeuronID", "SynapseID", "SynapseType", "NeuronLabel", "Direction",
                "PartnerSynapseID"]


def _partner_id(link: dict, is_pre: bool) -> int | None:
    """The parent structure on the other side of a StructureLink."""
    end = link.get("Target") if is_pre else link.get("Source")
    if not isinstance(end, dict):
        return None
    parent = end.get("ParentID")
    return int(parent) if parent is not None else None


def linked_neurons(
    neuron,
    synapse_type,
    client,
    *,
    include_unlinked: bool = True,
) -> pd.DataFrame:
    """Neurons connected to ``neuron`` through one synapse type.

    Undirected types (``Unknown``, ``Touch``, ``GapJunction``, ``Desmosome``)
    are matched from both ends, because Viking's Source/Target assignment for
    them is arbitrary.
    """
    kind = StructureTypes.from_str(synapse_type)
    synapse_ids = neuron.synapse_ids(kind)
    if synapse_ids.size == 0:
        return pd.DataFrame(columns=LINK_COLUMNS)

    wanted = {int(i) for i in synapse_ids}
    rows: list[dict] = []
    seen_synapses: set[int] = set()

    for link in client.fetch_structure_links(sorted(wanted)):
        source_id = link.get("SourceID")
        target_id = link.get("TargetID")
        for own_id, is_pre in ((source_id, True), (target_id, False)):
            if own_id is None or int(own_id) not in wanted:
                continue
            if kind.is_pre and not is_pre:
                continue
            if kind.is_post and is_pre:
                continue
            partner = _partner_id(link, is_pre)
            other_id = target_id if is_pre else source_id
            seen_synapses.add(int(own_id))
            rows.append(
                {
                    "NeuronID": partner,
                    "SynapseID": int(own_id),
                    "SynapseType": kind.value,
                    "NeuronLabel": "",
                    "Direction": "pre" if is_pre else "post",
                    "PartnerSynapseID": int(other_id) if other_id is not None else None,
                }
            )

    if include_unlinked:
        for sid in sorted(wanted - seen_synapses):
            rows.append(
                {
                    "NeuronID": None,
                    "SynapseID": int(sid),
                    "SynapseType": kind.value,
                    "NeuronLabel": "",
                    "Direction": "pre" if kind.is_pre else "post",
                    "PartnerSynapseID": None,
                }
            )

    frame = pd.DataFrame(rows, columns=LINK_COLUMNS)
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset=["NeuronID", "SynapseID", "Direction"])
    return frame.sort_values(["NeuronID", "SynapseID"], na_position="last").reset_index(drop=True)


def all_linked_neurons(neuron, client, *, resolve_labels: bool = True) -> pd.DataFrame:
    """Linked neurons across every synapse type on ``neuron``."""
    frames = [
        linked_neurons(neuron, name, client) for name in neuron.synapse_names()
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=LINK_COLUMNS)
    table = pd.concat(frames, ignore_index=True)

    labels_missing = False
    if resolve_labels:
        ids = sorted({int(v) for v in table["NeuronID"].dropna()})
        try:
            labels = client.get_labels(ids)
        except Exception as exc:  # noqa: BLE001 - a missing label must not fail the query
            log.warning("Partner labels of %d neurons not fetched: %s", len(ids), exc)
            labels, labels_missing = {}, True
        table["NeuronLabel"] = [
            "" if pd.isna(v) else labels.get(int(v), "") for v in table["NeuronID"]
        ]
    out = table.sort_values(["NeuronID", "SynapseType"], na_position="last").reset_index(drop=True)
    if labels_missing:
        # A caller that keeps the table (the service keeps one per cell) can
        # tell it lacks the labels and ask again, instead of serving the blanks.
        out.attrs["labels_missing"] = True
    return out


def count_linked_neurons(links: pd.DataFrame) -> pd.DataFrame:
    """Synapse counts per partner neuron and type.  Ports ``countLinkedNeurons``."""
    if links is None or links.empty:
        return pd.DataFrame(columns=["NeuronID", "NeuronLabel", "SynapseType", "Count"])
    known = links.dropna(subset=["NeuronID"]).copy()
    known["NeuronID"] = known["NeuronID"].astype(int)
    grouped = (
        known.groupby(["NeuronID", "NeuronLabel", "SynapseType"], as_index=False)
        .size()
        .rename(columns={"size": "Count"})
    )
    return grouped.sort_values("Count", ascending=False).reset_index(drop=True)


# -- the linked-neurons report ---------------------------------------------
# ``getAllLinkedNeurons`` returned a table with a ``SynapseXYZ`` column that
# the port's ``all_linked_neurons`` leaves out (a network needs partners, not
# positions).  The report puts it back, one column per axis in microns, and
# offers the two orderings people actually use: the MATLAB function's own
# (by partner) and the one ``util/analysis/getAllLinkedNeurons_sorted.m``
# introduced in 2025 (by synapse type, then label, partner and synapse).

REPORT_COLUMNS = [
    "NeuronID", "SynapseID", "SynapseType", "NeuronLabel",
    "SynapseX_um", "SynapseY_um", "SynapseZ_um", "Direction",
    # Appended (2026-09-22), so a script reading the MATLAB columns by
    # position still finds them: whether Viking's tags say what the synapse
    # is, and where to go in Viking to make them say it.
    "Tags", "TagStatus", "SuggestedTag", "LocationID",
]

_TEXT_COLUMNS = ("SynapseType", "NeuronLabel", "Direction", "Tags", "TagStatus", "SuggestedTag")

LINK_ORDERS = {
    "neuron": ["NeuronID", "SynapseType", "SynapseID"],
    "type": ["SynapseType", "NeuronLabel", "NeuronID", "SynapseID"],
}


def attach_synapse_xyz(neuron, links: pd.DataFrame) -> pd.DataFrame:
    """Add each synapse's location in microns to a link table.

    One annotation per synapse structure, the ``Unique`` one where the
    structure has several, as ``getLinkedNeurons`` did with
    ``neuron.getSynapseXYZ``.  A synapse whose annotations are not loaded
    gets NaN.
    """
    table = links.copy()
    for column in ("SynapseX_um", "SynapseY_um", "SynapseZ_um"):
        table[column] = float("nan")
    table["LocationID"] = pd.array([pd.NA] * len(table), dtype="Int64")
    if table.empty:
        return table
    nodes = neuron.nodes
    wanted = {int(v) for v in table["SynapseID"]}
    subset = nodes[nodes["ParentID"].isin(wanted)]
    if subset.empty:
        return table
    if "Unique" in subset.columns:
        subset = subset.sort_values("Unique", ascending=False)
    first = subset.drop_duplicates("ParentID").set_index("ParentID")
    ids = table["SynapseID"].astype(int)
    for column, axis in (("SynapseX_um", "Xum"), ("SynapseY_um", "Yum"), ("SynapseZ_um", "Zum")):
        if axis in first.columns:
            table[column] = ids.map(first[axis]).astype(float).to_numpy()
    if "ID" in first.columns:
        table["LocationID"] = pd.array(ids.map(first["ID"]).tolist(), dtype="Int64")
    return table


def attach_tags(neuron, links: pd.DataFrame) -> pd.DataFrame:
    """Add each synapse's Viking tags and whether they say what it is.

    ``TagStatus`` is ``tagged``, ``inferred``, ``untagged`` or ``assumed``
    for a postsynapse and blank for a type Viking's type alone decides;
    ``SuggestedTag`` is the tag an inferred one is missing.
    """
    table = links.copy()
    synapses = getattr(neuron, "synapses", None)
    columns = ("Tags", "TagStatus", "SuggestedTag")
    if synapses is None or synapses.empty or table.empty:
        for column in columns:
            table[column] = ""
        return table
    by_id = synapses.drop_duplicates("ID").set_index("ID")
    ids = table["SynapseID"].astype(int)
    for column in columns:
        if column in by_id.columns:
            table[column] = ids.map(by_id[column]).fillna("").astype(str).to_numpy()
        else:
            table[column] = ""
    return table


def sort_links(links: pd.DataFrame, order: str = "neuron") -> pd.DataFrame:
    """Order a link table by partner (``neuron``) or by synapse type (``type``)."""
    try:
        keys = LINK_ORDERS[order]
    except KeyError:
        raise ValueError(f"order must be one of {sorted(LINK_ORDERS)}, not {order!r}") from None
    present = [k for k in keys if k in links.columns]
    if links.empty or not present:
        return links.reset_index(drop=True)
    return links.sort_values(present, na_position="last", kind="stable").reset_index(drop=True)


def link_report(neuron, links: pd.DataFrame, order: str = "neuron") -> pd.DataFrame:
    """The ``getAllLinkedNeurons`` table: partners, synapses, positions, in order."""
    table = attach_tags(neuron, attach_synapse_xyz(neuron, links))
    for column in REPORT_COLUMNS:
        if column not in table.columns:
            table[column] = "" if column in _TEXT_COLUMNS else float("nan")
    return sort_links(table[REPORT_COLUMNS], order)


def links_to_csv(table: pd.DataFrame) -> str:
    """CSV text of a report table, IDs as integers and unlinked partners blank."""
    out = table.copy()
    for column in ("NeuronID", "LocationID"):
        if column in out.columns:
            out[column] = out[column].map(lambda v: "" if pd.isna(v) else str(int(v)))
    return out.to_csv(index=False, lineterminator="\n")


def links_to_xlsx(table: pd.DataFrame, sheet_name: str = "Linked neurons") -> bytes:
    """An Excel workbook of a report table (the format the lab's script saved)."""
    import io

    out = table.copy()
    for column in ("NeuronID", "LocationID"):
        if column in out.columns:
            out[column] = out[column].astype("Int64")
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name=sheet_name[:31])
        sheet = writer.sheets[sheet_name[:31]]
        for column_cells in sheet.columns:
            width = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(10, width + 2), 40)
        sheet.freeze_panes = "A2"
    return buffer.getvalue()
