"""Volume registry.

Ports ``+sbfsem/+builtin/Volumes.m`` together with the six free functions that
duplicated parts of it: ``validateSource``, ``getServiceRoot``, ``getServerName``,
``getVolumeAbbrev``, ``getAnimal`` and ``loadCachedVolumeScale``.

The MATLAB code scattered volume facts across those files and they disagreed
(``validateSource`` accepted the four Marc lab volumes, ``getServiceRoot`` raised
for them; ``Volumes.nSections`` knew three volumes, ``getVolumeAbbrev`` four).
Everything now lives in one table, so a volume is either listed or absent; a
fact that is not known for a listed one (a section count, an abbreviation, a
VikingXML) is ``None``, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Volumes",
    "VolumeInfo",
    "validate_source",
    "SERVER_NAME",
    "from_viking_name",
    "viking_names",
]

#: Base Viking web service.  ``util/import/getServerName.m``.
SERVER_NAME = "https://websvc.codepharm.net/"


@dataclass(frozen=True)
class VolumeInfo:
    """Static facts about one Viking volume."""

    name: str
    #: Path segment used in the OData service root.  ``NeitzNasalMonkey`` is
    #: served as ``NeitzNM``; this alias is load-bearing and preserved verbatim.
    url_name: str | None
    #: What the Viking Identity Server calls this volume -- a *third* spelling,
    #: independent of both ``name`` and ``url_name``.  The Nasal monkey volume
    #: is ``NeitzNasalMonkey`` here, ``NeitzNM`` in the OData path, and ``NM``
    #: to the identity server.  It is the key of the ``AccessibleVolumes``
    #: response, so it is how a permission grant is matched to a volume we can
    #: render.  ``None`` for volumes the identity server does not know, which
    #: are therefore never gated.  Matched case-insensitively: Viking spells
    #: one of them ``cped``.
    viking_name: str | None
    #: What the interface shows.  Separate from ``name`` because ``name`` is the
    #: wire identifier -- it appears in API paths, cache filenames, exported
    #: JSON and saved sessions, so renaming it would orphan existing data.
    display_name: str
    abbrev: str | None
    animal: str
    #: The lab whose volume it is (``"Neitz"``, ``"Marc"``), ``None`` for the
    #: bundled demo.  The web app picks a default section correction by it.
    lab: str | None
    #: (nm/pixel X, nm/pixel Y, nm/section Z) used when the server is unreachable.
    cached_scale: tuple[float, float, float]
    #: Volumes whose OData ``Scale`` endpoint was once known to be wrong: the
    #: Nasal monkey volume reported the Inferior volume's scale, so the cached
    #: one is used without asking.  Viking's answer for it has read (5, 5, 50),
    #: the cached scale, since at least 2026-09-17 (the oldest cached answer, read
    #: again on 2026-10-16), so today the override only saves a request and guards
    #: against the old answer coming back.
    force_cached_scale: bool = False
    n_sections: int | None = None
    has_cones: bool = False
    has_boundary: bool = False
    has_custom_transform: bool = False
    is_odata: bool = True
    #: Which Viking columns the cached XY offsets were derived from, and so
    #: which ones the Custom transform must be applied to (F215).  MATLAB
    #: generated Inferior offsets from VolumeX/VolumeY but applied them to X/Y.
    custom_transform_columns: tuple[str, str] = ("VolumeX", "VolumeY")
    aliases: tuple[str, ...] = field(default_factory=tuple)
    #: The VikingXML that lists the volume's section-to-volume stos files
    #: (sbfsem.io.registration), when it is a flat registration read here and
    #: checked against the stored positions (2026-09-23).  ``None``: RC1's is
    #: chained, Nasal's lists no stos, Temporal's answered 404, RC2's stos
    #: paths answered 404.
    vikingxml_url: str | None = None


_INFO: dict[str, VolumeInfo] = {
    "NeitzInferiorMonkey": VolumeInfo(
        name="NeitzInferiorMonkey",
        display_name="Inferior Monkey",
        url_name="NeitzInferiorMonkey",
        viking_name="InferiorMonkey",
        abbrev="i",
        animal="monkey",
        lab="Neitz",
        cached_scale=(7.5, 7.5, 90.0),
        n_sections=1893,
        has_cones=True,
        has_boundary=True,
        has_custom_transform=True,
        aliases=("inferior", "i", "neitzinferior", "neitzinferiormonkey", "inf"),
        vikingxml_url="http://v2486.host.s.uw.edu/Neitz/InferiorMonkey/InferiorMonkey.xml",
    ),
    "NeitzNasalMonkey": VolumeInfo(
        name="NeitzNasalMonkey",
        display_name="Nasal Monkey",
        url_name="NeitzNM",
        viking_name="NM",
        abbrev="n",
        animal="monkey",
        lab="Neitz",
        cached_scale=(5.0, 5.0, 50.0),
        force_cached_scale=True,
        n_sections=2354,
        has_boundary=True,
        has_custom_transform=True,
        custom_transform_columns=("X", "Y"),
        aliases=("nasal", "n", "neitznasal", "neitznasalmonkey"),
    ),
    "NeitzTemporalMonkey": VolumeInfo(
        name="NeitzTemporalMonkey",
        display_name="Temporal Monkey",
        url_name="NeitzTemporalMonkey",
        viking_name="TemporalMonkey",
        abbrev="t",
        animal="monkey",
        lab="Neitz",
        # Viking's live Scale (2026-09-17): 75 nm sections.  The MATLAB
        # fallback said 70.
        cached_scale=(7.5, 7.5, 75.0),
        n_sections=837,
        has_boundary=True,
        aliases=("temporal", "t", "neitztemporal", "neitztemporalmonkey", "temp"),
    ),
    "NeitzCped": VolumeInfo(
        name="NeitzCped",
        display_name="Cped",
        url_name="NeitzCPED",
        viking_name="cped",
        abbrev="c",
        animal="monkey",
        lab="Neitz",
        # Viking's live Scale (2026-09-17): 5.315 nm pixels, 50 nm sections.
        # The MATLAB fallback of (7.5, 7.5, 90) would have stretched an
        # offline render 1.8x in Z.
        cached_scale=(5.315, 5.315, 50.0),
        aliases=("cped", "c", "neitzcped"),
        vikingxml_url="http://v2223.host.s.uw.edu/Neitz/CPED/cped.xml",
    ),
    # The four Marc lab rabbit volumes, served from the same host as the
    # Neitz volumes (the Utah connectomes servers no longer resolve).  The
    # cached scales are the live OData ``Scale`` values (2026-08-31); MATLAB's
    # offline fallback of (2.18, 2.18, 90) had the wrong section thickness.
    "RC1": VolumeInfo(
        name="RC1",
        display_name="RC1",
        url_name="RC1",
        viking_name="RC1",
        abbrev="r",
        animal="rabbit",
        lab="Marc",
        cached_scale=(2.176, 2.176, 70.0),
        has_boundary=True,
        aliases=("rc1", "marcrc1", "r"),
    ),
    "RC2": VolumeInfo(
        name="RC2",
        display_name="RC2",
        url_name="RC2",
        viking_name="RC2",
        # Viking gained INL/IPL and IPL/GCL markers after the MATLAB list was
        # written (128 and 117 structures on 2026-09-11); cached in data/.
        has_boundary=True,
        abbrev=None,
        animal="rabbit",
        lab="Marc",
        cached_scale=(2.176, 2.176, 70.0),
        aliases=("rc2", "marcrc2"),
    ),
    "RPC1": VolumeInfo(
        name="RPC1",
        display_name="RPC1",
        url_name="RPC1",
        viking_name="RPC1",
        abbrev=None,
        animal="rabbit",
        lab="Marc",
        cached_scale=(2.176, 2.176, 70.0),
        aliases=("rpc1", "marcrpc1"),
        vikingxml_url="http://rogue1.codepharm.net/RPC1/SliceToVolume.VikingXML",
    ),
    "RPC2": VolumeInfo(
        name="RPC2",
        display_name="RPC2",
        url_name="RPC2",
        viking_name="RPC2",
        abbrev=None,
        animal="rabbit",
        lab="Marc",
        cached_scale=(2.176, 2.176, 70.0),
        aliases=("rpc2", "marcrpc2"),
        vikingxml_url="http://rogue1.codepharm.net/RPC2/SliceToVolume.VikingXML",
    ),
    "DemoVolume": VolumeInfo(
        name="DemoVolume",
        display_name="Demo Volume",
        url_name=None,
        viking_name=None,
        abbrev="d",
        animal="monkey",
        lab=None,
        cached_scale=(7.5, 7.5, 90.0),
        is_odata=False,
        aliases=("demo", "demovolume", "d"),
    ),
}

_ALIAS_LOOKUP: dict[str, str] = {}
for _canonical, _vi in _INFO.items():
    _ALIAS_LOOKUP[_canonical.lower()] = _canonical
    _ALIAS_LOOKUP[_vi.display_name.lower()] = _canonical
    # So a display name survives a round trip through a URL or a form field.
    _ALIAS_LOOKUP[_vi.display_name.lower().replace(" ", "")] = _canonical
    for _a in _vi.aliases:
        _ALIAS_LOOKUP[_a.lower()] = _canonical


class Volumes(Enum):
    """The volumes SBFSEM-tools knows about."""

    NeitzInferiorMonkey = "NeitzInferiorMonkey"
    NeitzNasalMonkey = "NeitzNasalMonkey"
    NeitzTemporalMonkey = "NeitzTemporalMonkey"
    NeitzCped = "NeitzCped"
    RC1 = "RC1"
    RC2 = "RC2"
    RPC1 = "RPC1"
    RPC2 = "RPC2"
    DemoVolume = "DemoVolume"

    # -- construction ------------------------------------------------------
    @classmethod
    def from_str(cls, source: str | Volumes) -> Volumes:
        """Accept a volume name, abbreviation or ``Volumes`` member."""
        if isinstance(source, cls):
            return source
        return cls(validate_source(source))

    # -- facts -------------------------------------------------------------
    @property
    def info(self) -> VolumeInfo:
        return _INFO[self.value]

    @property
    def source(self) -> str:
        """Canonical volume name, as ``validateSource`` returned."""
        return self.value

    @property
    def display_name(self) -> str:
        """Name for the interface, e.g. ``Inferior Monkey``."""
        return self.info.display_name

    @property
    def is_odata(self) -> bool:
        return self.info.is_odata

    @property
    def viking_name(self) -> str | None:
        """What the Viking Identity Server calls this volume, if it knows it."""
        return self.info.viking_name

    @property
    def abbrev(self) -> str | None:
        return self.info.abbrev

    @property
    def animal(self) -> str:
        return self.info.animal

    @property
    def lab(self) -> str | None:
        """The lab whose volume it is (``"Neitz"``, ``"Marc"``), ``None`` for the demo."""
        return self.info.lab

    @property
    def has_cones(self) -> bool:
        return self.info.has_cones

    @property
    def has_boundary(self) -> bool:
        return self.info.has_boundary

    @property
    def has_custom_transform(self) -> bool:
        return self.info.has_custom_transform

    @property
    def vikingxml_url(self) -> str | None:
        """Where the volume's current section transforms are listed, if they are read here."""
        return self.info.vikingxml_url

    @property
    def n_sections(self) -> int | None:
        """Section count, or ``None`` when unknown (MATLAB raised instead)."""
        return self.info.n_sections

    @property
    def custom_transform_columns(self) -> tuple[str, str]:
        return self.info.custom_transform_columns

    @property
    def cached_scale(self) -> tuple[float, float, float]:
        return self.info.cached_scale

    @property
    def service_root(self) -> str:
        """OData service root, ending in ``/OData/``."""
        if not self.info.is_odata or self.info.url_name is None:
            raise ValueError(f"{self.value} has no OData service")
        return f"{SERVER_NAME}{self.info.url_name}/OData/"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


def validate_source(source: str | Volumes) -> str:
    """Match a name or abbreviation to a canonical volume name.

    Ports ``util/import/validateSource.m``.  The MATLAB version fell through
    silently for non-char, non-enum input (F064); this raises.
    """
    if isinstance(source, Volumes):
        return source.value
    if not isinstance(source, str):
        raise TypeError(f"source must be a string or Volumes member, got {type(source).__name__}")
    try:
        return _ALIAS_LOOKUP[source.strip().lower()]
    except KeyError:
        raise ValueError(f"Unknown volume source: {source!r}") from None


#: Viking Identity Server volume name (lowercased) -> canonical volume name.
_VIKING_LOOKUP: dict[str, str] = {
    _vi.viking_name.lower(): _canonical
    for _canonical, _vi in _INFO.items()
    if _vi.viking_name is not None
}


def from_viking_name(viking_name: str) -> str | None:
    """The canonical volume name behind an identity-server volume name.

    ``None`` when the identity server knows a volume we cannot render -- there
    are two dozen of those (``Zia``, ``Wohl``, ``RPC3``, ...), and a permission
    grant on one of them is simply not actionable here.  The caller decides
    whether to report it or ignore it; what it must not do is guess.
    """
    if not isinstance(viking_name, str):
        return None
    return _VIKING_LOOKUP.get(viking_name.strip().lower())


def viking_names() -> dict[str, str]:
    """Canonical volume name -> identity-server name, for the volumes it knows."""
    return {
        canonical: info.viking_name
        for canonical, info in _INFO.items()
        if info.viking_name is not None
    }
