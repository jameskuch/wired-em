"""Collections of structures queried by Viking type.

Ports ``+sbfsem/+core/StructureGroup.m``, ``+sbfsem/+core/Ultrastructure.m``,
``+sbfsem/+builtin/Mitochondria.m``, ``Nucleolus.m``, ``OrganizedSER.m``,
``Vasculature.m``, ``BloodVessel.m``, ``VesselAdjacency.m``,
``+sbfsem/+builtin/ConeMosaic.m`` and ``+sbfsem/+io/ConeOData.m``.

Deviations (see DEVIATIONS.md):

* ``Mitochondria.getVolume`` computed ``2 * pi * (sum r)^2``, squaring the *total*
  of every annotation's radius rather than summing squared radii, and carried a
  spurious factor of two.  The reported volume was therefore inflated roughly in
  proportion to how finely each organelle had been traced (F147).  The correct
  ``sum(pi * r^2) * dz`` is used here.
* ``Mitochondria.pull``'s "all mitochondria" branch reassigned its response
  variable inside a loop and then read structure IDs out of a *Locations*
  response, so it queried ``Structures(locationID)/Locations`` (F148).
* ``Vasculature.pull`` built a cell array and then called methods on cells, and a
  swallowed import failure silently shifted every later vessel out of alignment
  with its ID (F150).  ``BloodVessel`` and ``Nucleolus`` did not even parse
  (F145, F146).
* ``ConeMosaic.getCones`` returned a numeric ID vector rather than the traces for
  the ``'U'`` branch (F155).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..core.types import VikingStructureTypes
from ..core.volumes import Volumes
from .odata import ODataClient
from .wkt import rings_in_volume_frame

__all__ = [
    "StructureGroup",
    "Ultrastructure",
    "Mitochondria",
    "ConeMosaic",
    "ConeTrace",
    "Vasculature",
]

log = logging.getLogger(__name__)


class StructureGroup:
    """Every structure in a volume with one Viking type ID."""

    type_id: int = -1

    def __init__(self, source, *, client: ODataClient | None = None):
        self.volume = Volumes.from_str(source)
        self.source = self.volume.source
        self.client = client or ODataClient(self.volume)
        self.structures = pd.DataFrame(columns=["ID", "ParentID"])

    def pull(self, parent_id: int | None = None) -> pd.DataFrame:
        """Query the structure list, optionally restricted to one parent."""
        if self.type_id < 0:
            raise NotImplementedError("Subclasses must set type_id")
        rows = self.client.structures_by_type(self.type_id)
        frame = pd.DataFrame(rows)
        if frame.empty:
            self.structures = pd.DataFrame(columns=["ID", "ParentID"])
            return self.structures
        for column in ("ID", "ParentID"):
            if column not in frame.columns:
                frame[column] = pd.NA
        if parent_id is not None:
            frame = frame[frame["ParentID"] == int(parent_id)]
        self.structures = frame[["ID", "ParentID"]].reset_index(drop=True)
        return self.structures

    @property
    def ids(self) -> np.ndarray:
        if self.structures.empty:
            return np.empty(0, dtype=np.int64)
        return self.structures["ID"].to_numpy(dtype=np.int64)


class Ultrastructure(StructureGroup):
    """A group of organelle annotations, with their locations."""

    def __init__(self, source, *, client: ODataClient | None = None):
        super().__init__(source, client=client)
        self.locations = pd.DataFrame()
        self.volume_scale = self.client.get_scale()

    def pull(self, parent_id: int | None = None) -> pd.DataFrame:
        super().pull(parent_id)
        if self.ids.size == 0:
            self.locations = pd.DataFrame()
            log.info("No %s found in %s", type(self).__name__, self.source)
            return self.structures
        # Batched, rather than the MATLAB request-per-structure loop.
        rows = self.client.fetch_locations_by_parents(self.ids)
        frame = pd.DataFrame(rows)
        if not frame.empty:
            scale = np.asarray(self.volume_scale, dtype=float) / 1e3
            # Positions in the registered volume frame, as every cell's are
            # (``Structure.apply_transform``); X/Y are the section mosaic's,
            # which sit up to 345 um away on RC1.  Only a row set that lacks
            # the volume columns falls back to them.
            x_col = "VolumeX" if "VolumeX" in frame.columns else "X"
            y_col = "VolumeY" if "VolumeY" in frame.columns else "Y"
            frame["Rum"] = frame["Radius"].astype(float) * scale[0]
            frame["Xum"] = frame[x_col].astype(float) * scale[0]
            frame["Yum"] = frame[y_col].astype(float) * scale[1]
            frame["Zum"] = frame["Z"].astype(float) * scale[2]
        self.locations = frame
        return self.structures


class Mitochondria(Ultrastructure):
    """Mitochondrial annotations (Viking type 246)."""

    type_id = int(VikingStructureTypes.Mitochondria)

    def volumes(self) -> pd.DataFrame:
        """Per-mitochondrion volume in cubic microns.

        Each annotation is a disc of radius ``r`` over one section, so the volume
        is ``sum(pi * r^2) * sectionThickness``.
        """
        if self.locations.empty:
            return pd.DataFrame(columns=["ParentID", "n", "volume_um3"])
        dz = float(self.volume_scale[2]) / 1e3
        grouped = self.locations.groupby("ParentID")["Rum"]
        frame = grouped.agg(
            n="size",
            volume_um3=lambda r: float(np.sum(np.pi * np.asarray(r, dtype=float) ** 2) * dz),
        ).reset_index()
        return frame.sort_values("volume_um3", ascending=False).reset_index(drop=True)


class Nucleolus(Ultrastructure):
    """Nucleolus annotations (Viking type 245)."""

    type_id = int(VikingStructureTypes.Nucleolus)


class OrganizedSER(Ultrastructure):
    """Organized smooth ER annotations (Viking type 81)."""

    type_id = int(VikingStructureTypes.OrganizedSER)


class Vasculature(StructureGroup):
    """Blood vessel structures (Viking type 3) and their adjacencies."""

    type_id = int(VikingStructureTypes.Vessel)

    def __init__(self, source, *, client: ODataClient | None = None):
        super().__init__(source, client=client)
        self.vessels: dict[int, object] = {}
        self.failed: list[int] = []

    def load_vessels(self, *, include_synapses: bool = False):
        """Load every vessel as a :class:`~sbfsem.core.structure.Structure`.

        Failures are recorded in :attr:`failed` rather than silently shifting the
        remaining vessels out of alignment with their IDs (F150).
        """
        from .loader import NeuronLoader

        loader = NeuronLoader(self.volume, client=self.client)
        self.vessels, self.failed = {}, []
        for vessel_id in self.ids:
            try:
                self.vessels[int(vessel_id)] = loader.load(
                    int(vessel_id), include_synapses=include_synapses
                )
            except Exception as exc:  # noqa: BLE001 - one bad vessel must not stop the rest
                log.warning("Vessel %u failed to import: %s", vessel_id, exc)
                self.failed.append(int(vessel_id))
        return self.vessels

    def adjacencies(self) -> pd.DataFrame:
        """Vessel-adjacency annotations (Viking type 254)."""
        rows = self.client.structures_by_type(int(VikingStructureTypes.VesselAdjacency))
        return pd.DataFrame(rows)


@dataclass
class ConeTrace:
    """One cone outline traced as a closed curve."""

    structure_id: int
    cone_type: str
    label: str
    z_section: int
    z_um: float
    outline: np.ndarray
    cutouts: list[np.ndarray] = field(default_factory=list)

    @property
    def centroid(self) -> np.ndarray:
        return self.outline.mean(axis=0) if len(self.outline) else np.zeros(2)


class ConeMosaic:
    """Cone outlines traced in NeitzInferiorMonkey.

    Ports ``+sbfsem/+builtin/ConeMosaic.m`` and ``+sbfsem/+io/ConeOData.m``.
    Cones are found by label: ``lmTRACE``, ``sTRACE`` and ``uTRACE``.
    """

    CONE_TYPES = ("LM", "S", "U")
    LABELS = {"LM": "lmTRACE", "S": "sTRACE", "U": "uTRACE"}

    def __init__(self, source="NeitzInferiorMonkey", *, client: ODataClient | None = None):
        self.volume = Volumes.from_str(source)
        if not self.volume.has_cones:
            raise ValueError(f"{self.volume.source} has no traced cone outlines")
        self.source = self.volume.source
        self.client = client or ODataClient(self.volume)
        self.volume_scale = self.client.get_scale()
        self.cones: dict[str, list[ConeTrace]] = {t: [] for t in self.CONE_TYPES}

    def cone_ids(self, cone_type: str) -> list[int]:
        """Structure IDs whose label contains the trace marker for this type."""
        cone_type = cone_type.upper()
        if cone_type not in self.CONE_TYPES:
            raise ValueError(f"cone_type must be one of {self.CONE_TYPES}")
        rows = self.client.ids_by_label(self.LABELS[cone_type], exact_match=False)
        return [int(r["ID"]) for r in rows]

    def get_cones(self, cone_type: str) -> list[ConeTrace]:
        """Load the closed-curve outline of every cone of one type."""
        cone_type = cone_type.upper()
        traces: list[ConeTrace] = []
        scale_um = np.asarray(self.volume_scale, dtype=float) / 1e3
        for structure_id in self.cone_ids(cone_type):
            label = self.client.get_label(structure_id)
            for row in self.client.fetch_closed_curves(structure_id):
                rings, _registered = rings_in_volume_frame(row, self.volume_scale)
                if not rings:
                    continue
                traces.append(
                    ConeTrace(
                        structure_id=structure_id,
                        cone_type=cone_type,
                        label=label,
                        z_section=int(row["Z"]),
                        z_um=float(row["Z"]) * float(scale_um[2]),
                        outline=rings[0],
                        cutouts=list(rings[1:]),
                    )
                )
        self.cones[cone_type] = traces
        return traces

    def get_all(self) -> dict[str, list[ConeTrace]]:
        for cone_type in self.CONE_TYPES:
            self.get_cones(cone_type)
        return self.cones

    def summary(self) -> dict[str, int]:
        return {t: len(v) for t, v in self.cones.items()}
