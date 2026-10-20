"""Structure and Neuron data model.

Ports ``+sbfsem/+core/StructureAPI.m``, ``+sbfsem/+core/NeuronAPI.m`` and
``Neuron.m``.  Data lives in :class:`pandas.DataFrame` tables and the morphology
graph is a :class:`networkx.Graph` keyed directly by Viking location ID, which
removes the ``nodeIDs`` index-to-location-ID lookup array that pervaded the
MATLAB code.

Deviations (see DEVIATIONS.md):

* ``somaRow`` searched the whole node table, so an imported synapse annotation
  larger than the soma silently became the soma (F019).  The search is now
  restricted to cell-body annotations.
* The "unique" synapse marker used ``Z == floor(median(Z))``, which selects no
  node at all for an even number of distinct sections and several nodes when
  they tie (F016).  It now picks exactly one node, the one nearest the median.
* ``graph('Synapses', true)`` indexed a numeric column with a whole table and
  could not run (F007); ``Weighted`` was ignored for directed graphs (F009) and
  computed weights from the unfiltered edge list (F008).  All three are fixed.
* Re-importing synapses concatenated tables with mismatched columns (F014).
  Derived columns are rebuilt after every merge.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

import networkx as nx
import numpy as np
import pandas as pd

from .frames import (
    TYPECODE_CLOSED_CURVE,
    empty_edges,
    empty_nodes,
    normalize_nodes,
    xyz_um,
)
from .registry import read_omitted_ids, read_omitted_sections
from .transforms import Transforms
from .types import (
    PARTNER_POST_KIND,
    SUGGESTED_TAGS,
    UNTAGGED_POLICIES,
    StructureTypes,
    VikingStructureTypes,
    postsynapse_kind,
)
from .volumes import Volumes

__all__ = ["Structure", "Neuron", "SynapseNotFoundError"]

log = logging.getLogger(__name__)


class SynapseNotFoundError(LookupError):
    """Raised when a requested synapse type has no annotations on this neuron.

    The MATLAB code returned ``[NaN NaN NaN]`` here, which defeated every
    ``isempty`` guard downstream and produced plausible-looking distances to an
    unrelated annotation (F131 and the whole ``util/analysis`` empty-guard
    family).  :meth:`Neuron.get_synapse_xyz` returns an empty ``(0, 3)`` array
    unless it is called with ``strict=True``, which raises this.
    """


class Structure:
    """A Viking structure: metadata plus node and edge tables."""

    def __init__(
        self,
        structure_id: int,
        source,
        *,
        viking: dict | None = None,
        nodes: pd.DataFrame | None = None,
        edges: pd.DataFrame | None = None,
        volume_scale: Sequence[float] | None = None,
        transform: Transforms | str = Transforms.Standard,
        geometries: pd.DataFrame | None = None,
        omitted_ids: Iterable[int] | None = None,
        last_modified: datetime | None = None,
        data_stamp: datetime | None = None,
    ):
        self.id = int(structure_id)
        self.volume = Volumes.from_str(source)
        self.source = self.volume.source
        self.viking = dict(viking or {})
        self.volume_scale = tuple(
            float(v) for v in (volume_scale if volume_scale is not None else self.volume.cached_scale)
        )
        self.transform = Transforms.from_str(transform)
        self.nodes = empty_nodes() if nodes is None else normalize_nodes(nodes)
        self.edges = empty_edges() if edges is None else edges
        self.geometries = geometries
        self.analysis: dict[str, object] = {}
        self.model = None
        self.last_modified = last_modified or datetime.now(UTC)
        #: The newest ``LastModified`` among the annotations these nodes were
        #: built from -- what a cached render is compared against to decide
        #: whether Viking has moved on.  ``None`` for data with no such stamp
        #: (a JSON import, a test fixture).
        self.data_stamp = data_stamp
        #: Whether child structures (synapses...) were merged into ``nodes``:
        #: their annotations can only be compared with Viking's if they were
        #: fetched at all.
        self.children_loaded = False
        #: Viking's section correction, when one was applied
        #: (:func:`sbfsem.io.corrections.apply_offsets`): the set, its build
        #: time and how far the annotations moved.  ``None`` = Viking's own
        #: stored positions.
        self.section_correction: dict | None = None
        #: What the web service asked for and got (requested, applied, why
        #: not); set by ``SbfsemService`` on load.
        self.correction_status: dict | None = None
        #: The volume's current section transforms, when they were applied
        #: (:func:`sbfsem.io.registration.apply_registration`): where they
        #: were read and how far the annotations moved from their stored
        #: positions.  ``None`` = the stored positions.
        self.live_registration: dict | None = None
        #: As ``correction_status``, for the live registration.
        self.registration_status: dict | None = None
        #: A person's own moves, after the correction (:mod:`sbfsem.core.moves`):
        #: how many and how far, and the set's token.  ``None`` = no manual move.
        self.manual_moves: dict | None = None
        #: For a copy made by applying manual moves: the ``load_serial`` of the
        #: copy it was made from, so a copy made from one that has since been
        #: loaded again is made again.  ``None`` otherwise.
        self.base_serial: int | None = None
        #: Which copy of the cell this is in ``SbfsemService``'s table: set
        #: each time a load puts one there, so what was built from an earlier
        #: copy is never handed out for this one.  ``None`` outside the service.
        self.load_serial: int | None = None
        #: The setting this copy was loaded with (``SbfsemService``'s
        #: ``copy_tag``, e.g. ``"curvefit.1"``): the table holds one copy of a
        #: cell per setting in use.  ``None`` outside the service.
        self.copy_tag: str | None = None
        #: Whether the service fetched this cell with its synapses (its own
        #: load); ``None`` for a cell it was handed (a file), which a later
        #: load takes as it is.
        self.loaded_synapses: bool | None = None
        if omitted_ids is None:
            omitted_ids = self._default_omitted_ids()
        self.omitted_ids = np.asarray(list(omitted_ids), dtype=np.int64)
        if not self.nodes.empty and "Xum" not in self.nodes.columns:
            self.apply_transform()

    # -- construction helpers ---------------------------------------------
    def _default_omitted_ids(self) -> np.ndarray:
        ids = list(read_omitted_ids(self.id, self.source))
        sections = read_omitted_sections(self.source)
        if sections.size and not self.nodes.empty:
            mask = self.nodes["Z"].isin(sections.tolist())
            ids.extend(self.nodes.loc[mask, "ID"].tolist())
        return np.asarray(sorted({int(i) for i in ids}), dtype=np.int64)

    # -- properties --------------------------------------------------------
    @property
    def notes(self) -> str:
        return str(self.viking.get("Notes") or "")

    @property
    def label(self) -> str:
        return str(self.viking.get("Label") or "").strip()

    @property
    def type_id(self) -> int | None:
        value = self.viking.get("TypeID")
        return int(value) if value is not None else None

    @property
    def off_edges(self) -> np.ndarray:
        """Location IDs flagged as running off the volume edge."""
        if self.nodes.empty:
            return np.empty(0, dtype=np.int64)
        return self.nodes.loc[self.nodes["OffEdge"], "ID"].to_numpy(dtype=np.int64)

    @property
    def terminals(self) -> np.ndarray:
        """Location IDs flagged as branch endings."""
        if self.nodes.empty:
            return np.empty(0, dtype=np.int64)
        return self.nodes.loc[self.nodes["Terminal"], "ID"].to_numpy(dtype=np.int64)

    @property
    def unfinished(self) -> np.ndarray:
        """Degree-1 nodes that are neither terminals nor off-edge."""
        graph = self.graph()
        degree_one = np.array(
            sorted(n for n, d in graph.degree() if d == 1), dtype=np.int64
        )
        remaining = np.setdiff1d(degree_one, self.terminals)
        return np.setdiff1d(remaining, self.off_edges)

    @property
    def edge_nodes(self) -> np.ndarray:
        """Nodes that are both terminal and off-edge."""
        return np.intersect1d(self.terminals, self.off_edges)

    @property
    def has_closed_curves(self) -> bool:
        if self.nodes.empty:
            return False
        return bool((self.nodes["TypeCode"] == TYPECODE_CLOSED_CURVE).any())

    # -- coordinate transform ---------------------------------------------
    def apply_transform(self) -> None:
        """(Re)compute the micron columns ``Xum``, ``Yum``, ``Zum`` and ``Rum``."""
        if self.nodes.empty:
            for col in ("Xum", "Yum", "Zum", "Rum"):
                self.nodes[col] = pd.Series(dtype="float64")
            if "Unique" not in self.nodes.columns:
                self.nodes["Unique"] = pd.Series(dtype="bool")
            return

        if self.transform is Transforms.NoTransform:
            x = self.nodes["VolumeX"].to_numpy(dtype=float)
            y = self.nodes["VolumeY"].to_numpy(dtype=float)
            z = self.nodes["Z"].to_numpy(dtype=float)
            self.nodes["Xum"], self.nodes["Yum"], self.nodes["Zum"] = x, y, z
            self.nodes["Rum"] = self.nodes["Radius"].to_numpy(dtype=float)
            if "Unique" not in self.nodes.columns:
                self.nodes["Unique"] = False
            return

        if self.transform is Transforms.Custom and self.volume.has_custom_transform:
            xcol, ycol = self.volume.custom_transform_columns
            raw = np.column_stack(
                [
                    self.nodes[xcol].to_numpy(dtype=float),
                    self.nodes[ycol].to_numpy(dtype=float),
                    self.nodes["Z"].to_numpy(dtype=float),
                ]
            )
            x, y = Transforms.translate(raw, self.source)
        else:
            x = self.nodes["VolumeX"].to_numpy(dtype=float)
            y = self.nodes["VolumeY"].to_numpy(dtype=float)
            # Moves in volume nm, each where one was applied, VolumeX/VolumeY
            # keeping Viking's stored values: the current section transforms
            # (sbfsem.io.registration), then the section correction
            # (sbfsem.io.corrections).
            for dx_col, dy_col in (("LiveDXnm", "LiveDYnm"), ("CorrDXnm", "CorrDYnm")):
                if dx_col in self.nodes.columns:
                    x = x + self.nodes[dx_col].fillna(0.0).to_numpy(dtype=float) / self.volume_scale[0]
                    y = y + self.nodes[dy_col].fillna(0.0).to_numpy(dtype=float) / self.volume_scale[1]

        xyz = np.column_stack([x, y, self.nodes["Z"].to_numpy(dtype=float)])
        scaled = Transforms.scale(xyz, self.volume_scale)
        # Last, a person's own moves (sbfsem.core.moves), in nm whatever the
        # transform: after the correction, and under the Custom transform too.
        if "ManDXnm" in self.nodes.columns:
            scaled[:, 0] += self.nodes["ManDXnm"].fillna(0.0).to_numpy(dtype=float) / 1000.0
            scaled[:, 1] += self.nodes["ManDYnm"].fillna(0.0).to_numpy(dtype=float) / 1000.0
        self.nodes["Xum"] = scaled[:, 0]
        self.nodes["Yum"] = scaled[:, 1]
        self.nodes["Zum"] = scaled[:, 2]
        self.nodes["Rum"] = Transforms.scale(
            self.nodes["Radius"].to_numpy(dtype=float), self.volume_scale
        )
        if "Unique" not in self.nodes.columns:
            self.nodes["Unique"] = False

    def set_transform(self, transform: Transforms | str) -> None:
        self.transform = Transforms.from_str(transform)
        self.apply_transform()

    # -- lookups -----------------------------------------------------------
    def id2xyz(self, ids, micron: bool = True) -> np.ndarray:
        """XYZ for one or more location IDs, in row order of ``ids``."""
        wanted = np.atleast_1d(np.asarray(ids, dtype=np.int64))
        cols = ["Xum", "Yum", "Zum"] if micron else ["X", "Y", "Z"]
        indexed = self.nodes.set_index("ID")
        missing = [int(i) for i in wanted if i not in indexed.index]
        if missing:
            raise KeyError(f"Location IDs not in structure {self.id}: {missing}")
        return indexed.loc[wanted, cols].to_numpy(dtype=float)

    def get_nodes_by_parent(self, parent_id: int) -> pd.DataFrame:
        return self.nodes[self.nodes["ParentID"] == int(parent_id)]

    def get_xyz_by_parent(self, parent_id: int, micron: bool = True) -> np.ndarray:
        frame = self.get_nodes_by_parent(parent_id)
        cols = ["Xum", "Yum", "Zum"] if micron else ["X", "Y", "Z"]
        return frame[cols].to_numpy(dtype=float)

    def get_cell_nodes(self) -> pd.DataFrame:
        """Only the structure's own annotations, excluding child structures."""
        return self.get_nodes_by_parent(self.id)

    def get_cell_xyz(self, micron: bool = True) -> np.ndarray:
        return self.get_xyz_by_parent(self.id, micron)

    # -- graph -------------------------------------------------------------
    def graph(
        self,
        *,
        directed: bool = False,
        weighted: bool = False,
        synapses: bool = False,
    ) -> nx.Graph | nx.DiGraph:
        """Morphology as a networkx graph keyed by location ID.

        ``weighted`` attaches a ``weight`` edge attribute holding the Euclidean
        distance in microns.  Unlike the MATLAB version this works for directed
        graphs too (F009) and always uses the same filtered edge list as the
        nodes (F008).
        """
        if synapses:
            edges = self.edges
        else:
            edges = self.edges[self.edges["ID"] == self.id]

        graph: nx.Graph | nx.DiGraph = nx.DiGraph() if directed else nx.Graph()
        node_ids = self.nodes["ID"].to_numpy(dtype=np.int64)
        if not synapses and not self.nodes.empty:
            node_ids = self.get_cell_nodes()["ID"].to_numpy(dtype=np.int64)
        graph.add_nodes_from(int(i) for i in node_ids)

        if not edges.empty:
            a = edges["A"].to_numpy(dtype=np.int64)
            b = edges["B"].to_numpy(dtype=np.int64)
            if weighted and not self.nodes.empty:
                coords = self.nodes.set_index("ID")[["Xum", "Yum", "Zum"]]
                known = coords.index
                keep = np.isin(a, known) & np.isin(b, known)
                a, b = a[keep], b[keep]
                if a.size:
                    pa = coords.loc[a].to_numpy(dtype=float)
                    pb = coords.loc[b].to_numpy(dtype=float)
                    w = np.linalg.norm(pa - pb, axis=1)
                    graph.add_weighted_edges_from(
                        (int(x), int(y), float(d)) for x, y, d in zip(a, b, w, strict=True)
                    )
            else:
                graph.add_edges_from(
                    (int(x), int(y)) for x, y in zip(a, b, strict=True)
                )

        if self.omitted_ids.size:
            removed = [int(i) for i in self.omitted_ids if graph.has_node(int(i))]
            graph.remove_nodes_from(removed)
            if removed:
                log.debug("Omitted %u locations from c%u graph", len(removed), self.id)
        return graph

    def get_branch_nodes(self, location_a: int, location_b: int) -> pd.DataFrame:
        """Nodes along the shortest path between two location IDs."""
        graph = self.graph()
        try:
            path = nx.shortest_path(graph, int(location_a), int(location_b))
        except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
            raise ValueError(
                f"Location IDs {location_a} and {location_b} are not connected"
            ) from exc
        indexed = self.nodes.set_index("ID", drop=False)
        return indexed.loc[path].reset_index(drop=True)

    # -- rendering helpers -------------------------------------------------
    def get_daspect(self) -> np.ndarray:
        """Axis aspect ratio for plotting, as ``StructureAPI.getDAspect``."""
        vs = np.asarray(self.volume_scale, dtype=float)
        return vs.max() / vs

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<{type(self).__name__} c{self.id} {self.source} "
            f"nodes={len(self.nodes)} edges={len(self.edges)}>"
        )


class Neuron(Structure):
    """A Viking ``Cell`` structure, with its child synapse structures."""

    #: What a postsynapse whose tags name no kind becomes; one of
    #: :data:`~sbfsem.core.types.UNTAGGED_POLICIES`.  ``assume`` (conventional)
    #: is what MATLAB did.
    untagged_psd: str = "assume"

    def __init__(self, *args, synapses: pd.DataFrame | None = None, links=None,
                 untagged_psd: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.synapses = synapses if synapses is not None else pd.DataFrame()
        self.links = links
        if untagged_psd is not None:
            self.untagged_psd = untagged_psd
        #: Postsynapse ID -> the Viking TypeIDs of the structures it is linked
        #: to; what ``infer`` reads.  Filled by the loader.
        self.psd_partners: dict[int, tuple[int, ...]] = {}
        if not self.synapses.empty:
            self.setup_synapses()

    # -- soma --------------------------------------------------------------
    @property
    def soma_row(self) -> pd.DataFrame:
        """The largest *cell-body* annotation (F019: not the largest of all)."""
        cell_nodes = self.get_cell_nodes()
        if cell_nodes.empty:
            return cell_nodes
        return cell_nodes[cell_nodes["Radius"] == cell_nodes["Radius"].max()]

    def get_soma_id(self) -> int:
        row = self.soma_row
        if row.empty:
            raise ValueError(f"c{self.id} has no cell-body annotations")
        return int(row.iloc[0]["ID"])

    def get_soma_size(self, use_diameter: bool = False) -> float:
        row = self.soma_row
        if row.empty:
            raise ValueError(f"c{self.id} has no cell-body annotations")
        value = float(row["Rum"].max())
        return value * 2 if use_diameter else value

    def get_soma_xyz(self, micron: bool = True) -> np.ndarray:
        row = self.soma_row
        if row.empty:
            return np.empty((0, 3), dtype=float)
        cols = ["Xum", "Yum", "Zum"] if micron else ["X", "Y", "Z"]
        return row.iloc[[0]][cols].to_numpy(dtype=float).ravel()

    # -- synapses ----------------------------------------------------------
    def setup_synapses(self) -> None:
        """Attach ``LocalName``, ``N`` and the ``Unique`` node marker.

        Ports ``Neuron.setupSynapses``.  ``Unique`` marks exactly one annotation
        per synapse structure — the one closest to the median section — where the
        MATLAB ``Z == floor(median(Z))`` test could match zero or several (F016).
        """
        self.nodes["Unique"] = False    # every time: a marker left by an earlier call is not this one's
        if self.synapses.empty:
            return

        counts: list[int] = []
        unique_index: list[int] = []
        by_parent = dict(self.nodes.groupby("ParentID").groups.items())
        z_values = self.nodes["Z"]
        for syn_id in self.synapses["ID"].to_numpy(dtype=np.int64):
            idx = by_parent.get(int(syn_id))
            if idx is None or len(idx) == 0:
                counts.append(0)
                continue
            counts.append(len(idx))
            z = z_values.loc[idx].to_numpy(dtype=float)
            target = float(np.median(z))
            unique_index.append(int(idx[int(np.argmin(np.abs(z - target)))]))
        if unique_index:
            self.nodes.loc[unique_index, "Unique"] = True
        self.synapses = self.synapses.copy()
        self.synapses["N"] = counts

        local, status, suggested = [], [], []
        for syn_id, type_id, tags in zip(
            self.synapses["ID"].to_numpy(dtype=np.int64),
            self.synapses["TypeID"].to_numpy(dtype=np.int64),
            self.synapses.get("Tags", pd.Series([""] * len(self.synapses))).to_numpy(),
            strict=True,
        ):
            kind, state, tag = self._resolve_synapse(int(syn_id), int(type_id), _as_tag(tags))
            local.append(kind)
            status.append(state)
            suggested.append(tag)
        self.synapses["LocalName"] = local
        #: ``tagged``, ``inferred``, ``untagged`` or ``assumed`` for a
        #: postsynapse, blank for types whose Viking type says what they are.
        self.synapses["TagStatus"] = status
        #: For an inferred postsynapse, the tag that would say so in Viking.
        self.synapses["SuggestedTag"] = suggested
        # Lenient on purpose: the server-side type ontology grows (the Marc
        # volumes reached TypeID 270 while the enum stopped at 256), and one
        # unmodelled child type must not make a whole cell unloadable.
        self.synapses["VikingName"] = [
            _viking_type_name(int(t)) for t in self.synapses["TypeID"]
        ]
        self._warn_old_conventions()

    def _resolve_synapse(self, syn_id: int, type_id: int, tags: str) -> tuple:
        """``(LocalName, TagStatus, SuggestedTag)`` for one child structure."""
        if type_id != VikingStructureTypes.Postsynapse:
            try:
                return StructureTypes.from_viking(type_id, tags), "", ""
            except ValueError:
                return StructureTypes.Unknown, "", ""
        kind = postsynapse_kind(tags)
        if kind is not None:
            return kind, "tagged", ""
        policy = self.untagged_psd
        if policy not in UNTAGGED_POLICIES:
            raise ValueError(f"untagged_psd must be one of {UNTAGGED_POLICIES}, not {policy!r}")
        if policy == "assume":
            return StructureTypes.ConvPost, "assumed", ""
        if policy == "infer":
            partners = self.psd_partners.get(syn_id, ())
            kinds = {PARTNER_POST_KIND.get(int(t)) for t in partners}
            known = {k for k in kinds if k is not None}
            # One partner kind, and no partner that implies none: a PSD
            # linked to a ribbon and to something unmodelled is not guessed.
            if len(kinds) == 1 and len(known) == 1:
                inferred = next(iter(known))
                return inferred, "inferred", SUGGESTED_TAGS.get(inferred, "")
        return StructureTypes.UntaggedPost, "untagged", ""

    def untagged_postsynapses(self) -> pd.DataFrame:
        """The postsynapses whose Viking tags do not say what they are.

        One row per structure -- ``ID``, ``Tags``, ``TagStatus``,
        ``LocalName``, ``SuggestedTag`` -- the list to put right in Viking.
        """
        cols = ["ID", "Tags", "TagStatus", "LocalName", "SuggestedTag"]
        if self.synapses.empty or "TagStatus" not in self.synapses.columns:
            return pd.DataFrame(columns=cols)
        mask = self.synapses["TagStatus"].isin(("inferred", "untagged", "assumed"))
        return self.synapses.loc[mask, cols].reset_index(drop=True)

    def _warn_old_conventions(self) -> None:
        """Ports ``util/import/makeConsistent.m``."""
        if self.source in ("RC1", "RPC1", "RC2", "RPC2"):
            return
        old = {181, 182, 240, 241}
        if set(self.synapses["TypeID"].tolist()) & old:
            log.warning("c%u uses old GABA synapse markers (TypeID 181/182/240/241)", self.id)

    def synapse_ids(self, which=None) -> np.ndarray:
        if self.synapses.empty:
            return np.empty(0, dtype=np.int64)
        if which is None:
            return self.synapses["ID"].to_numpy(dtype=np.int64)
        wanted = StructureTypes.from_str(which)
        mask = self.synapses["LocalName"] == wanted
        return self.synapses.loc[mask, "ID"].to_numpy(dtype=np.int64)

    def get_synapse_count(self, which) -> int:
        return int(self.synapse_ids(which).size)

    def synapse_names(self, as_str: bool = False):
        if self.synapses.empty:
            return []
        names = list(dict.fromkeys(self.synapses["LocalName"].tolist()))
        names.sort(key=lambda t: t.value)
        return [n.value for n in names] if as_str else names

    def get_synapse_nodes(self, only_unique: bool = True) -> pd.DataFrame:
        mask = self.nodes["ParentID"] != self.id
        if only_unique:
            mask &= self.nodes["Unique"]
        return self.nodes[mask].sort_values("ParentID").reset_index(drop=True)

    def get_synapse_nodes_by_id(self, synapse_id: int) -> pd.DataFrame:
        return self.nodes[self.nodes["ParentID"] == int(synapse_id)]

    def get_synapse_nodes_by_type(self, which, only_unique: bool = True) -> pd.DataFrame:
        ids = self.synapse_ids(which)
        mask = self.nodes["ParentID"].isin(ids.tolist())
        if only_unique:
            mask &= self.nodes["Unique"]
        return self.nodes[mask]

    def get_synapse_xyz(self, which, micron: bool = True, strict: bool = False) -> np.ndarray:
        """XYZ of one marker per synapse of the given type or structure ID(s).

        Returns an empty ``(0, 3)`` array when nothing matches.  The MATLAB
        version returned ``[NaN NaN NaN]``, which every downstream ``isempty``
        guard failed to catch.
        """
        if isinstance(which, (str, StructureTypes)):
            ids = self.synapse_ids(which)
        else:
            ids = np.atleast_1d(np.asarray(which, dtype=np.int64))
        mask = self.nodes["ParentID"].isin(ids.tolist()) & self.nodes["Unique"]
        cols = ["Xum", "Yum", "Zum"] if micron else ["X", "Y", "Z"]
        xyz = self.nodes.loc[mask, cols].to_numpy(dtype=float)
        if xyz.size == 0:
            if strict:
                raise SynapseNotFoundError(f"No {which} annotations on c{self.id}")
            log.info("No locations found for %s on c%u", which, self.id)
            return np.empty((0, 3), dtype=float)
        return xyz

    def get_synapse_axes(self, which, micron: bool = True):
        """Center, unit axis and half-length of each synapse annotation.

        Viking stores a synapse as a ``LINESTRING`` in volume pixel space -- the
        two ends of the segment the annotator drew -- alongside the disc radius.
        The segment midpoint reproduces the node's own ``VolumeX``/``VolumeY``
        exactly, so the two representations agree and the segment adds only the
        orientation, which nothing else in the toolbox used.

        Returns ``(centers, axes, half_lengths)``.  A row whose geometry is
        missing or degenerate gets a zero axis, which the ellipsoid builder
        renders as a sphere.
        """
        if isinstance(which, (str, StructureTypes)):
            ids = self.synapse_ids(which)
        else:
            ids = np.atleast_1d(np.asarray(which, dtype=np.int64))
        mask = self.nodes["ParentID"].isin(ids.tolist()) & self.nodes["Unique"]
        rows = self.nodes.loc[mask]
        cols = ["Xum", "Yum", "Zum"] if micron else ["X", "Y", "Z"]
        centers = rows[cols].to_numpy(dtype=float)
        if centers.size == 0:
            empty = np.empty((0, 3), dtype=float)
            return empty, empty, np.empty(0, dtype=float)

        axes = np.zeros_like(centers)
        halves = np.zeros(len(centers), dtype=float)
        shapes = (
            rows["VolumeShape"].tolist() if "VolumeShape" in rows.columns
            else [None] * len(rows)
        )
        # Directions scale like positions because the micron transform is a
        # plain per-axis scale; any offset cancels in the difference.
        vs = np.asarray(self.volume_scale, dtype=float)[:2] / 1e3 if micron else np.ones(2)
        # Imported here, not at module scope: ``sbfsem.io`` imports
        # ``sbfsem.core``, so a top-level import would close the cycle.
        from ..io.wkt import linestring_endpoints

        for i, shape in enumerate(shapes):
            segment = linestring_endpoints(shape)
            if segment is None:
                continue
            delta = (segment[1] - segment[0]) * vs
            length = float(np.hypot(*delta))
            if length < 1e-12:
                continue
            axes[i, 0], axes[i, 1] = delta / length
            halves[i] = length / 2.0
        return centers, axes, halves

    def synapse_summary(self) -> pd.DataFrame:
        """Counts by Viking name and by local name.  Ports ``printSyn``."""
        if self.synapses.empty:
            return pd.DataFrame(columns=["LocalName", "VikingName", "Count"])
        grouped = (
            self.synapses.assign(LocalNameStr=self.synapses["LocalName"].map(lambda t: t.value))
            .groupby(["LocalNameStr", "VikingName"], as_index=False)
            .size()
            .rename(columns={"LocalNameStr": "LocalName", "size": "Count"})
        )
        return grouped.sort_values("Count", ascending=False).reset_index(drop=True)

    # -- merging -----------------------------------------------------------
    def merge_child_data(
        self,
        synapses: pd.DataFrame,
        child_nodes: pd.DataFrame,
        child_edges: pd.DataFrame,
    ) -> None:
        """Replace this neuron's child-structure data.

        Existing synapse rows are dropped first, and derived columns are rebuilt
        so a second import cannot hit the mismatched-column concatenation that
        broke ``Neuron.update`` (F014).
        """
        self.children_loaded = True
        base_nodes = self.nodes[self.nodes["ParentID"] == self.id]
        base_edges = self.edges[self.edges["ID"] == self.id]
        child_nodes = normalize_nodes(child_nodes) if child_nodes is not None else empty_nodes()

        merged = pd.concat(
            [base_nodes.drop(columns=["Unique"], errors="ignore"),
             child_nodes.drop(columns=["Unique"], errors="ignore")],
            ignore_index=True,
        )
        self.nodes = normalize_nodes(merged)
        self.apply_transform()
        frames = [base_edges]
        if child_edges is not None and not child_edges.empty:
            frames.append(child_edges)
        self.edges = pd.concat(frames, ignore_index=True)
        self.synapses = synapses if synapses is not None else pd.DataFrame()
        self.omitted_ids = self._default_omitted_ids()
        self.setup_synapses()

    # -- convenience -------------------------------------------------------
    @property
    def xyz_um(self) -> np.ndarray:
        return xyz_um(self.nodes)


def _as_tag(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and np.isnan(value):
        return ""
    return str(value)


def _viking_type_name(type_id: int) -> str:
    try:
        return VikingStructureTypes(type_id).name
    except ValueError:
        return f"Type {type_id}"
