"""Structure type enumerations.

Ports ``+sbfsem/+core/StructureTypes.m``, ``+sbfsem/+builtin/VikingStructureTypes.m``,
and ``+sbfsem/+core/EntityType.m``.

``EntityType`` was an empty MATLAB stub class and is not ported.
``StructureGroup`` is an OData query base class, not a type enum; it lives in
:mod:`sbfsem.io.structure_group`.

Deviations from the MATLAB original (see DEVIATIONS.md):

* ``StructureTypes.is_boundary_marker`` referenced a non-existent ``GCLIPLBoundary``
  member (F001).  The real member is ``IPLGCLBoundary``.
* ``VikingStructureTypes.localName`` switched on ``StructureTypes`` members while
  dispatching on a ``VikingStructureTypes`` object, so every case fell through to
  ``otherwise`` (F146).  Fixed to switch on ``VikingStructureTypes``.
* Char-to-enum conversion is supported everywhere via :meth:`StructureTypes.from_str`,
  which accepts every member name case-insensitively rather than the six the MATLAB
  ``fromStr`` handled (F020, F231).
* A postsynapse's tags are read as a set, in any order and any case (DEVIATIONS
  2.x): MATLAB compared the whole string, so ``Ribbon;Bipolar;Glutamate`` fell
  through to ``ConvPost``.  A postsynapse no tag names is still ``ConvPost`` by
  default, as in MATLAB; ``untagged=`` chooses otherwise (``UntaggedPost``).
"""

from __future__ import annotations

import re
from enum import Enum, IntEnum

__all__ = [
    "StructureTypes",
    "VikingStructureTypes",
    "postsynapse_kind",
    "tag_set",
    "UNTAGGED_POLICIES",
    "PARTNER_POST_KIND",
    "SUGGESTED_TAGS",
]


class VikingStructureTypes(IntEnum):
    """Viking's server-side structure type IDs."""

    Cell = 1
    Vessel = 3
    GapJunction = 28
    Bipolar = 31
    Conventional = 34
    Postsynapse = 35
    RibbonSynapse = 73
    Test = 80
    OrganizedSER = 81
    Adherens = 85
    CisternPre = 181
    CisternPost = 182
    Cilium = 183
    BCConventionalSynapse = 189
    Multicistern = 219
    Endocytosis = 220
    INLIPLBoundary = 224
    MultivesicularBody = 225
    RibosomePatch = 226
    RibbonCluster = 227
    Touch = 229
    Loop = 230
    Polysomes = 232
    Depth = 233
    Marker = 234
    IPLGCLBoundary = 235
    Plaque = 236
    Axon = 237
    PlaqueLikePre = 240
    PlaqueLikePost = 241
    NeuroglialAdherens = 243
    Unknown = 244
    Nucleolus = 245
    Mitochondria = 246
    Caveola = 247
    NuclearFilament = 248
    GolgiPlaque = 249
    GolgiNormal = 250
    Lysosome = 252
    AnnularGapJunction = 253
    VesselAdjacency = 254
    Rootlet = 255
    CHBoundary = 256
    # 257-270 appear in the Marc lab volumes' live StructureTypes tables
    # (RC1/RC2/RPC1/RPC2, read 2026-08-31); the Neitz volumes stop earlier.
    DistalJunction = 257
    FlatContact = 258
    BubblesSwirls = 259
    PeriGJAdherens = 260
    CaveolaString = 261
    CoatedPits = 262
    GJEndo = 263
    DenseCoreVesicle = 264
    StartVaricosity = 265
    EndVaricosity = 266
    GlialAdjacency = 267
    HorizontalCell = 268
    ExtracellularVesicle = 269
    BasalBody = 270

    def local_name(self, tag: str | None = None) -> str:
        """Return the SBFSEM-tools name for this Viking type and tag string.

        The MATLAB name table as a string, kept for the library and reached
        only from tests: the loader types a synapse with
        :meth:`StructureTypes.from_viking`, which this does not always agree
        with.  Measured 2026-10-16 over every Viking type and seven tag
        strings, they differ for ``Conventional`` tagged ``GABA`` among other
        tags (``ConvPre`` here, ``GABAPre`` there: this compares the whole
        string), for the two boundaries (``INLBoundary`` and ``GCLBoundary``
        here, ``INLIPLBoundary`` and ``IPLGCLBoundary`` there), for
        ``PlaqueLikePre`` and ``PlaqueLikePost`` (``GABAPre`` and ``GABAPost``
        here, themselves there), and for the types with no local member, which
        this answers by their Viking name and ``from_viking`` refuses.
        """
        tag = (tag or "").lower()
        V = VikingStructureTypes
        if self is V.Conventional:
            return "GABAPre" if tag == "gaba" else "ConvPre"
        if self is V.Postsynapse:
            kind = postsynapse_kind(tag)
            return kind.value if kind is not None else "ConvPost"
        return {
            V.RibbonSynapse: "RibbonPre",
            V.Adherens: "Desmosome",
            V.BCConventionalSynapse: "BipConvPre",
            V.INLIPLBoundary: "INLBoundary",
            V.IPLGCLBoundary: "GCLBoundary",
            V.PlaqueLikePre: "GABAPre",
            V.PlaqueLikePost: "GABAPost",
        }.get(self, self.name)


# Viking type IDs that may legitimately head a "Structure" the toolbox models.
VALID_STRUCTURE_TYPE_IDS = (1, 3, 245, 254)


class StructureTypes(Enum):
    """SBFSEM-tools' local annotation vocabulary."""

    Cell = "Cell"
    GapJunction = "GapJunction"
    ConvPre = "ConvPre"
    ConvPost = "ConvPost"
    RibbonPre = "RibbonPre"
    RibbonPost = "RibbonPost"
    BipConvPre = "BipConvPre"
    BipConvPost = "BipConvPost"
    GABAPre = "GABAPre"
    GABAPost = "GABAPost"
    Desmosome = "Desmosome"
    BasalTA = "BasalTA"
    BasalNTA = "BasalNTA"
    BasalMNTA = "BasalMNTA"
    Touch = "Touch"
    CisternPre = "CisternPre"
    CisternPost = "CisternPost"
    PlaqueLikePre = "PlaqueLikePre"
    PlaqueLikePost = "PlaqueLikePost"
    INLIPLBoundary = "INLIPLBoundary"
    IPLGCLBoundary = "IPLGCLBoundary"
    Unknown = "Unknown"
    AnnularGapJunction = "AnnularGapJunction"
    Axon = "Axon"
    Caveola = "Caveola"
    CHBoundary = "CHBoundary"
    Cilium = "Cilium"
    Endocytosis = "Endocytosis"
    GolgiPlaque = "GolgiPlaque"
    GolgiNormal = "GolgiNormal"
    Loop = "Loop"
    Lysosome = "Lysosome"
    Mitochondria = "Mitochondria"
    MultivesicularBody = "MultivesicularBody"
    NuclearFilament = "NuclearFilament"
    Nucleolus = "Nucleolus"
    NeuroglialAdherens = "NeuroglialAdherens"
    OrganizedSER = "OrganizedSER"
    Plaque = "Plaque"
    Polysomes = "Polysomes"
    RibbonCluster = "RibbonCluster"
    RibosomePatch = "RibosomePatch"
    Rootlet = "Rootlet"
    VesselAdjacency = "VesselAdjacency"
    # Viking types 257-270, only in the Marc lab volumes; the MATLAB vocabulary
    # stops at 256 and left them Unknown (DEVIATIONS 5.8).
    DistalJunction = "DistalJunction"
    FlatContact = "FlatContact"
    BubblesSwirls = "BubblesSwirls"
    PeriGJAdherens = "PeriGJAdherens"
    CaveolaString = "CaveolaString"
    CoatedPits = "CoatedPits"
    GJEndo = "GJEndo"
    DenseCoreVesicle = "DenseCoreVesicle"
    StartVaricosity = "StartVaricosity"
    EndVaricosity = "EndVaricosity"
    GlialAdjacency = "GlialAdjacency"
    HorizontalCell = "HorizontalCell"
    ExtracellularVesicle = "ExtracellularVesicle"
    BasalBody = "BasalBody"
    #: A postsynapse (Viking's PSD) whose tags do not say what kind it is and
    #: whose kind was not inferred -- not assumed conventional.
    UntaggedPost = "UntaggedPost"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    # -- colors -----------------------------------------------------------
    @property
    def color(self) -> tuple[float, float, float]:
        """RGB triple in 0-1: ``StructureColor`` in the MATLAB source for the
        types it knew, and a color of its own for each of the others (MATLAB
        drew them black)."""
        return _STRUCTURE_COLORS.get(self, (0.0, 0.0, 0.0))

    # -- predicates --------------------------------------------------------
    @property
    def is_pre(self) -> bool:
        return self in _PRE

    @property
    def is_post(self) -> bool:
        return self in _POST

    @property
    def is_undirected(self) -> bool:
        return self in _UNDIRECTED

    # ``is_synapse`` and ``is_boundary_marker`` below are reached only from
    # tests (audit 2026-10-15); the viewer asks the Viking type instead.
    @property
    def is_synapse(self) -> bool:
        return self not in _NOT_SYNAPSE

    @property
    def is_boundary_marker(self) -> bool:
        """F001: the MATLAB version named a member that does not exist."""
        return self in (
            StructureTypes.CHBoundary,
            StructureTypes.INLIPLBoundary,
            StructureTypes.IPLGCLBoundary,
        )

    # -- constructors ------------------------------------------------------
    @classmethod
    def from_str(cls, name: str | StructureTypes) -> StructureTypes:
        """Case-insensitive lookup by member name.

        Extends the MATLAB ``fromStr``, which only knew six members and returned
        an empty array (with a warning) for everything else.
        """
        if isinstance(name, cls):
            return name
        key = str(name).strip().lower()
        try:
            return _BY_LOWER_NAME[key]
        except KeyError:
            raise ValueError(f"Unknown StructureTypes name: {name!r}") from None

    @classmethod
    def from_viking(
        cls,
        viking_structure: VikingStructureTypes | int,
        tags: str | None = None,
        *,
        untagged: StructureTypes | str = "ConvPost",
    ) -> StructureTypes:
        """Resolve a Viking type ID plus its tag string onto a local name.

        Mirrors ``StructureTypes.fromViking``.  Unmapped Viking types (``Vessel``,
        ``Bipolar``, ``Test``, ``Multicistern``, ``Depth``, ``Marker``) left the
        MATLAB output unassigned; here they raise :class:`ValueError`.

        A postsynapse is typed by its tags, read as a set (any order, any
        case; see :func:`postsynapse_kind`).  One whose tags name no kind --
        no tag at all, ``AC``, ``Doubtful`` -- is ``untagged``: ``ConvPost``
        by default, which is what MATLAB assumed, or ``UntaggedPost`` to keep
        such synapses apart.
        """
        vs = VikingStructureTypes(viking_structure)
        V = VikingStructureTypes

        if vs is V.Conventional:
            return cls.GABAPre if "gaba" in tag_set(tags) else cls.ConvPre
        if vs is V.Postsynapse:
            kind = postsynapse_kind(tags)
            return kind if kind is not None else cls.from_str(untagged)

        direct = {
            V.Cell: cls.Cell,
            V.Adherens: cls.Desmosome,
            V.BCConventionalSynapse: cls.BipConvPre,
            V.RibbonSynapse: cls.RibbonPre,
            V.Touch: cls.Touch,
            V.GapJunction: cls.GapJunction,
            V.PlaqueLikePre: cls.PlaqueLikePre,
            V.PlaqueLikePost: cls.PlaqueLikePost,
            V.CisternPre: cls.CisternPre,
            V.CisternPost: cls.CisternPost,
            V.AnnularGapJunction: cls.AnnularGapJunction,
            V.Axon: cls.Axon,
            V.Caveola: cls.Caveola,
            V.CHBoundary: cls.CHBoundary,
            V.Cilium: cls.Cilium,
            V.Endocytosis: cls.Endocytosis,
            V.GolgiPlaque: cls.GolgiPlaque,
            V.GolgiNormal: cls.GolgiNormal,
            V.INLIPLBoundary: cls.INLIPLBoundary,
            V.IPLGCLBoundary: cls.IPLGCLBoundary,
            V.Loop: cls.Loop,
            V.Lysosome: cls.Lysosome,
            V.Mitochondria: cls.Mitochondria,
            V.MultivesicularBody: cls.MultivesicularBody,
            V.NuclearFilament: cls.NuclearFilament,
            V.Nucleolus: cls.Nucleolus,
            V.NeuroglialAdherens: cls.NeuroglialAdherens,
            V.OrganizedSER: cls.OrganizedSER,
            V.Plaque: cls.Plaque,
            V.Polysomes: cls.Polysomes,
            V.RibbonCluster: cls.RibbonCluster,
            V.RibosomePatch: cls.RibosomePatch,
            V.Rootlet: cls.Rootlet,
            V.Unknown: cls.Unknown,
            V.VesselAdjacency: cls.VesselAdjacency,
            V.DistalJunction: cls.DistalJunction,
            V.FlatContact: cls.FlatContact,
            V.BubblesSwirls: cls.BubblesSwirls,
            V.PeriGJAdherens: cls.PeriGJAdherens,
            V.CaveolaString: cls.CaveolaString,
            V.CoatedPits: cls.CoatedPits,
            V.GJEndo: cls.GJEndo,
            V.DenseCoreVesicle: cls.DenseCoreVesicle,
            V.StartVaricosity: cls.StartVaricosity,
            V.EndVaricosity: cls.EndVaricosity,
            V.GlialAdjacency: cls.GlialAdjacency,
            V.HorizontalCell: cls.HorizontalCell,
            V.ExtracellularVesicle: cls.ExtracellularVesicle,
            V.BasalBody: cls.BasalBody,
        }
        try:
            return direct[vs]
        except KeyError:
            raise ValueError(
                f"No local StructureTypes member for Viking type {vs.name} ({vs.value})"
            ) from None


_BY_LOWER_NAME = {m.name.lower(): m for m in StructureTypes}

_STRUCTURE_COLORS: dict[StructureTypes, tuple[float, float, float]] = {
    StructureTypes.Cell: (0.2, 0.2, 0.2),
    StructureTypes.ConvPre: (1.0, 0.278, 0.298),
    StructureTypes.ConvPost: (0.992, 0.667, 0.282),
    StructureTypes.RibbonPre: (0.082, 0.69, 0.102),
    StructureTypes.RibbonPost: (0.2501, 0.639, 0.408),
    StructureTypes.BipConvPre: (0.024, 0.322, 1.0),
    StructureTypes.BipConvPost: (0.016, 0.847, 0.698),
    StructureTypes.GABAPre: (0.780, 0.376, 1.0),
    StructureTypes.GABAPost: (0.780, 0.376, 1.0),
    StructureTypes.BasalTA: (1.0, 0.278, 0.298),
    StructureTypes.BasalNTA: (0.518, 0.0, 0.0),
    StructureTypes.BasalMNTA: (0.518, 0.0, 0.0),
    StructureTypes.GapJunction: (0.58, 0.824, 0.988),
    StructureTypes.Desmosome: (0.024, 0.604, 0.953),
    StructureTypes.Touch: (0.075, 0.918, 0.788),
    StructureTypes.Endocytosis: (0.565, 0.894, 0.757),
    StructureTypes.Unknown: (0.5, 0.5, 0.5),
    # Viking 257-270: no MATLAB color exists; picked to be told apart.
    StructureTypes.DistalJunction: (0.333, 0.42, 0.184),
    StructureTypes.FlatContact: (0.737, 0.561, 0.561),
    StructureTypes.BubblesSwirls: (0.69, 0.769, 0.871),
    StructureTypes.PeriGJAdherens: (0.275, 0.51, 0.706),
    StructureTypes.CaveolaString: (0.604, 0.804, 0.196),
    StructureTypes.CoatedPits: (0.824, 0.706, 0.549),
    StructureTypes.GJEndo: (0.4, 0.804, 0.667),
    StructureTypes.DenseCoreVesicle: (0.545, 0.271, 0.075),
    StructureTypes.StartVaricosity: (0.855, 0.439, 0.839),
    StructureTypes.EndVaricosity: (0.6, 0.196, 0.8),
    StructureTypes.GlialAdjacency: (0.0, 0.6, 0.6),
    StructureTypes.HorizontalCell: (0.8, 0.6, 0.0),
    StructureTypes.ExtracellularVesicle: (0.941, 0.502, 0.502),
    StructureTypes.BasalBody: (0.412, 0.412, 0.412),
    # Pale, and nothing else is: these are the ones to fix in Viking.
    StructureTypes.UntaggedPost: (0.86, 0.86, 0.86),
    # The rest of the vocabulary had no color and drew black (audit 2026-10-15,
    # W10 F09).  The two cisterns are the browser network's own colors
    # (network.js); the others are picked to be told apart from each other and
    # from the colors above, none of them near black or near the pale gray of
    # the untagged ones.
    StructureTypes.CisternPre: (0.882, 0.439, 0.333),
    StructureTypes.CisternPost: (0.980, 0.694, 0.627),
    StructureTypes.PlaqueLikePre: (0.450, 0.350, 0.850),
    StructureTypes.PlaqueLikePost: (0.700, 0.500, 0.850),
    StructureTypes.AnnularGapJunction: (0.314, 0.675, 0.867),
    StructureTypes.INLIPLBoundary: (0.180, 0.545, 0.341),
    StructureTypes.IPLGCLBoundary: (0.545, 0.180, 0.341),
    StructureTypes.CHBoundary: (0.650, 0.600, 0.200),
    StructureTypes.Axon: (0.255, 0.412, 0.882),
    StructureTypes.Caveola: (0.420, 0.557, 0.137),
    StructureTypes.Cilium: (0.502, 0.000, 0.502),
    StructureTypes.GolgiPlaque: (0.855, 0.647, 0.125),
    StructureTypes.GolgiNormal: (0.933, 0.510, 0.933),
    StructureTypes.Loop: (0.000, 0.000, 0.620),
    StructureTypes.Lysosome: (0.690, 0.188, 0.376),
    StructureTypes.Mitochondria: (0.700, 0.200, 0.200),
    StructureTypes.MultivesicularBody: (0.373, 0.620, 0.627),
    StructureTypes.NuclearFilament: (0.600, 0.600, 0.750),
    StructureTypes.Nucleolus: (0.282, 0.239, 0.545),
    StructureTypes.NeuroglialAdherens: (0.290, 0.760, 0.000),
    StructureTypes.OrganizedSER: (0.824, 0.412, 0.118),
    StructureTypes.Plaque: (0.450, 0.400, 0.050),
    StructureTypes.Polysomes: (0.596, 0.984, 0.596),
    StructureTypes.RibbonCluster: (0.133, 0.545, 0.133),
    StructureTypes.RibosomePatch: (0.855, 0.439, 0.576),
    StructureTypes.Rootlet: (0.650, 0.450, 0.250),
    StructureTypes.VesselAdjacency: (0.863, 0.078, 0.235),
}

_PRE = frozenset(
    {
        StructureTypes.GABAPre,
        StructureTypes.RibbonPre,
        StructureTypes.BipConvPre,
        StructureTypes.ConvPre,
    }
)

_POST = frozenset(
    {
        StructureTypes.GABAPost,
        StructureTypes.RibbonPost,
        StructureTypes.BipConvPost,
        StructureTypes.ConvPost,
        StructureTypes.BasalTA,
        StructureTypes.BasalNTA,
        StructureTypes.BasalMNTA,
        StructureTypes.UntaggedPost,
    }
)

_UNDIRECTED = frozenset(
    {
        StructureTypes.Unknown,
        StructureTypes.GapJunction,
        StructureTypes.Desmosome,
        StructureTypes.Touch,
    }
)

_NOT_SYNAPSE = frozenset(
    {
        StructureTypes.Cell,
        StructureTypes.Endocytosis,
        StructureTypes.INLIPLBoundary,
        StructureTypes.IPLGCLBoundary,
        StructureTypes.CHBoundary,
        StructureTypes.Cilium,
        StructureTypes.Plaque,
        StructureTypes.MultivesicularBody,
        StructureTypes.RibosomePatch,
    }
)


# -- postsynapse tags --------------------------------------------------------
# Viking stores a structure's tags as an unordered set; people type them in
# any order ("Ribbon;Bipolar;Glutamate", "Bipolar;Glutamate;Ribbon") and case
# ("conventional").  Read on the production cache 2026-09-22: 66 RC1, 33 RC2
# and 21 RPC1 ribbon postsynapses carry a reordered ribbon tag, and MATLAB's
# whole-string lookup counted every one of them as conventional.

_QUOTED_TAG = re.compile(r'"([^"]*)"')


def tag_set(tags: str | None) -> frozenset[str]:
    """The tag names of a tag string, lower-cased, as a set.

    Takes the ``A;B`` form :func:`sbfsem.io.tags.parse_tag` returns, or
    Viking's raw attribute blob (the names sit in double quotes).
    """
    if not tags:
        return frozenset()
    text = str(tags)
    if '"' in text:
        text = ";".join(_QUOTED_TAG.findall(text))
    return frozenset(t.strip().lower() for t in text.split(";") if t.strip())


def postsynapse_kind(tags: str | None) -> StructureTypes | None:
    """What a postsynapse's tags say it is, or ``None`` when they do not say.

    The MATLAB table's nine tag strings, matched as sets: ``GABA`` makes a
    GABA postsynapse; ``Conventional`` with ``TA``/``NTA``/``mNTA`` the basal
    types; ``Ribbon`` a ribbon postsynapse, ``Conventional`` a conventional
    one, with ``Bipolar`` the bipolar-conventional one.  Other tags
    (``Glutamate``, ``PR(cone)``, ``Perisomal``, ``DCV``…) qualify and do not
    decide.  ``Ribbon`` together with ``Conventional`` contradicts itself and,
    like no tag at all, returns ``None``.
    """
    names = tag_set(tags)
    S = StructureTypes
    if "gaba" in names:
        return S.GABAPost
    if "conventional" in names:
        for tag, kind in (("mnta", S.BasalMNTA), ("nta", S.BasalNTA), ("ta", S.BasalTA)):
            if tag in names:
                return kind
    ribbon, conventional = "ribbon" in names, "conventional" in names
    if ribbon and conventional:
        return None
    if ribbon:
        return S.RibbonPost
    if conventional:
        return S.BipConvPost if "bipolar" in names else S.ConvPost
    return None


#: What an untagged postsynapse becomes: ``assume`` conventional (MATLAB),
#: keep it ``separate`` as ``UntaggedPost``, or ``infer`` its kind from the
#: presynaptic structure it is linked to (Becca Pfeiffer, 2026-09-22) and keep
#: the rest separate.
UNTAGGED_POLICIES = ("assume", "separate", "infer")

#: The postsynaptic kind a linked presynaptic Viking type implies.
PARTNER_POST_KIND: dict[int, StructureTypes] = {
    VikingStructureTypes.RibbonSynapse: StructureTypes.RibbonPost,
    VikingStructureTypes.Conventional: StructureTypes.ConvPost,
    VikingStructureTypes.BCConventionalSynapse: StructureTypes.BipConvPost,
}

#: The tag that would make Viking say what an inferred postsynapse is.
SUGGESTED_TAGS: dict[StructureTypes, str] = {
    StructureTypes.RibbonPost: "Ribbon",
    StructureTypes.ConvPost: "Conventional",
    StructureTypes.BipConvPost: "Bipolar;Conventional;Glutamate",
}
