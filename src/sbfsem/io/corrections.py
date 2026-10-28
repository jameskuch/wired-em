"""Viking's section corrections: the residual registration field, per section.

James Anderson's service ``AnnotateSectionCorrections`` (package
``Viking.SectionCorrectionServiceTypes.gRPC.V2``; TLS gRPC at
``corrections.codepharm.net:443``, no auth; the proto is
``SectionCorrection/section_correction_v2.proto`` in
github.com/jamesra/viking_gRPC_protos, with its positions in
``Common/geometry.proto``) publishes, per volume and StosGroup, a sparse
lattice of XY offsets for each section it could measure.  The field is built
from every traced process in the volume (Viking's
``SectionCorrectionBuilder``): where a process's annotation on one section sits
off the curve its neighbors above and below predict, the section is
misregistered there.  The offsets (predicted - actual, volume nanometers) are
pooled on a lattice (4 um apart in the sets built since 2026-10-25, 2 um
before) over an 8 um kernel and published where at least three annotations
vote.  Protocol V1 was retired on 2026-10-26 (service 1.4.0; every V1 call has
answered ``UNIMPLEMENTED`` since about 15:13 UTC).

Viking's own morphology export adds the sampled offset to each annotation's
stored volume position, and only where the sample is trusted
(``DataExport/MorphologyController.ApplyPublishedResidualField`` ->
``MorphologyGraph.ApplyResidualField``).  :func:`correct_neuron` does the same
to a loaded neuron: every annotation -- the cell's and its child structures' --
moves by the offset sampled at its own volume position on its own section, and
a closed-curve outline moves rigidly with its annotation, whose stored position
is the outline's centroid.  The offsets land in the node table as
``CorrDXnm``/``CorrDYnm``; :meth:`~sbfsem.core.structure.Structure.apply_transform`
adds them to ``Xum``/``Yum``, while ``VolumeX``/``VolumeY`` keep Viking's values.

The service also corrects whole structures (``CorrectStructures``, 2026-09-24),
in one of Viking's ``CorrectionMode``\\ s (:data:`CORRECTION_MODES`): the
residual field (``neighbor``, 0, the proto's default) or nothing (``none``).
It maps each Location's section X/Y through the StosGroup itself and returns
the correction on top of that mapping; :func:`fetch_structure_offsets` applies
that correction to this cell's own positions, which with the live registration
on are the same mapping; a Location the service places more than
:data:`PLACEMENT_LIMIT_NM` away is left alone.  :func:`correct_neuron` and
:class:`CorrectionsProvider` correct this way; :func:`fetch_offsets` still
samples the field with ``CorrectPoints`` at this cell's positions.

Until 2026-10-27 the service also ran a leave-one-out Catmull-Rom fit along each
traced process (``curvefit``), alone or after the field (``all``).  It removed
the fit as a default in service 1.5.0, after an 11-cell measurement found the
field alone as good (mean linked-outline overlap 0.792, field then fit 0.791,
with a quarter of the losses), and 1.5.1 answers the old numbers with the field.
Those two names are read here as ``neighbor`` (:data:`RETIRED_MODES`), so a
saved setting still loads, and nothing here asks for the fit.

The messages are few and flat, so they are encoded here from the proto's field
numbers (the ``*_SCHEMA`` tables) instead of generated protobuf classes:
``grpcio`` carries the bytes, and nothing depends on a protobuf runtime version.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "DEFAULT_TARGET",
    "SERVICE",
    "CORRECTION_MODES",
    "RETIRED_MODES",
    "correction_mode",
    "request_mode",
    "CorrectionsError",
    "CorrectionsUnavailable",
    "CorrectionNotFound",
    "REQUEST_CODES",
    "names_volume",
    "SAME_CALL",
    "CorrectionSet",
    "CellCorrection",
    "CorrectionsClient",
    "encode",
    "decode",
    "choose_set",
    "sample_offsets",
    "fetch_offsets",
    "fetch_structure_offsets",
    "apply_offsets",
    "correct_neuron",
    "CorrectionsProvider",
]

log = logging.getLogger(__name__)

#: Where the service is published (the pinned ``[Viking]`` post in Slack #ai,
#: 2026-09-22).  ``SBFSEM_CORRECTIONS_URL`` overrides it; an ``http://`` URL
#: means cleartext HTTP/2.
DEFAULT_TARGET = "https://corrections.codepharm.net"
SERVICE = "Viking.SectionCorrectionServiceTypes.gRPC.V2.AnnotateSectionCorrections"

#: Points per ``CorrectPoints`` request.  Whole sections are never split, so a
#: request may run over by one section's points.
POINTS_PER_REQUEST = 20_000

#: Viking's ``CorrectionMode``, numbered as protocol V2 numbers it: what a
#: correction applies.  ``neighbor`` is the published residual field, and 0,
#: proto3's default, so a request carries no mode at all (the encoder leaves a
#: default out, as protobuf does); ``none`` maps and corrects nothing.  The
#: proto reserves 2 and 3 (1.5.2 gives raw 2, 3 and 4 to a curve fit
#: experiment), and nothing here sends them.  (V1 numbered them none 0,
#: neighbor 1, curvefit 2, all 3; this client sent V2 neighbor 2, curvefit 3,
#: all 0 from 2026-10-26 until 2026-10-27.)
CORRECTION_MODES: dict[str, int] = {"neighbor": 0, "none": 1}

#: The modes the service ran until 2026-10-27, read as ``neighbor``: its curve
#: fit (``curvefit``) and the field then the fit (``all``), and the numbers
#: this client sent for ``curvefit`` and ``neighbor`` (3 and 2; ``"0"`` has
#: always meant off here, ``"1"`` on).  A saved session, Look, workspace or
#: API call that names one is corrected by the field, and its status says
#: ``neighbor``.
RETIRED_MODES: frozenset[str] = frozenset({"curvefit", "all", "fieldfit", "2", "3"})


def correction_mode(value) -> str:
    """A correction setting as one name: ``off`` or ``neighbor``.

    ``True`` is ``neighbor``: what the on/off switch always meant, the residual
    field, and so is every retired mode (:data:`RETIRED_MODES`: ``curvefit``,
    ``all``).  ``False``, ``None``, ``"none"`` and the usual spellings of off
    are ``off``.  Case, spaces, ``-``, ``_`` and ``+`` are ignored
    (``"CurveFit"``, ``"curve_fit"``, ``"Field + fit"``).
    """
    if value is None or value is False:
        return "off"
    if value is True:
        return "neighbor"
    text = str(value).strip().lower()
    for mark in ("-", "_", " ", "+"):
        text = text.replace(mark, "")
    if text in ("", "off", "none", "0", "false", "no"):
        return "off"
    if text in ("1", "true", "yes", "on", "field", "residual", "residualfield", "neighbor"):
        return "neighbor"
    if text in RETIRED_MODES:
        return "neighbor"
    raise ValueError(f"not a correction mode: {value!r} (off or neighbor; the retired "
                     "curvefit and all are read as neighbor)")


def request_mode(mode: str) -> str:
    """The :data:`CORRECTION_MODES` name a request for ``mode`` carries: ``none``
    for off, else ``neighbor`` (a retired mode included).  Raises ``ValueError``
    for a name :func:`correction_mode` does not know."""
    return "none" if correction_mode(mode) == "off" else "neighbor"


# -- errors -----------------------------------------------------------------
class CorrectionsError(RuntimeError):
    """The corrections service answered with an error.

    ``code`` is the gRPC status's name (``"UNKNOWN"``), ``""`` for an error
    raised here rather than by the service; ``details`` the status's own text.
    """

    code = ""
    details = ""


#: gRPC statuses about the one request sent, not about its volume or the
#: service: an exception in the service's handler (``UNKNOWN`` "Exception was
#: thrown by handler.", ``INTERNAL``) and a request it refused.  A provider
#: holds the structure asked about after one of these, not its volume.
REQUEST_CODES = frozenset({"UNKNOWN", "INTERNAL", "INVALID_ARGUMENT"})


class CorrectionsUnavailable(CorrectionsError):
    """The service could not be reached, or did not answer in time."""


def names_volume(exc: Exception, *volume_names: str) -> bool:
    """Is ``exc`` the service saying that this volume, not the service, is unavailable?

    gRPC says ``UNAVAILABLE`` for a service it cannot reach ("failed to
    connect to all addresses", "errors resolving ..."), which knows nothing of
    the request.  The service says it too when it cannot reach one volume's own
    server, and then names the volume asked about in quotes: "volume mapping
    temporarily unavailable for 'cped': Error connecting to volume server after
    4 attempts: http://v2223.host.s.uw.edu/Neitz/CPED/cped.xml Connection
    refused" (2026-10-20 17:31 UTC, while RC1 answered every call).
    """
    if getattr(exc, "code", "") != "UNAVAILABLE":
        return False
    details = str(getattr(exc, "details", "")).lower()
    return any(name and f"'{name.lower()}'" in details for name in volume_names)


#: The correction modes one call answers, held together after it says their
#: volume is unavailable.  ``neighbor`` is the only one left: the curve fit's
#: ``curvefit`` and ``all`` shared a ``CorrectStructures`` call until
#: 2026-10-27.
SAME_CALL: dict[str, tuple[str, ...]] = {"neighbor": ("neighbor",)}


class CorrectionNotFound(CorrectionsError):
    """``NOT_FOUND``: an unknown volume or StosGroup, or a section with no map."""


# -- protobuf wire format ----------------------------------------------------
_VARINT, _I64, _LEN, _I32 = 0, 1, 2, 5

#: A message schema: field number -> (name, kind, repeated).  ``kind`` is one
#: of ``string``, ``bool``, ``int32``, ``int64``, ``double``, or another schema
#: for a nested message.  An enum is an ``int32``.
Schema = dict[int, tuple[str, Any, bool]]

_PACKABLE = ("bool", "int32", "int64", "double")


def _varint(value: int) -> bytes:
    if value < 0:  # int32 and int64 negatives are ten-byte two's complement
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint longer than ten bytes")


def _signed(value: int, bits: int) -> int:
    value &= (1 << 64) - 1
    if value >= 1 << 63:
        value -= 1 << 64
    if bits == 32:  # an int32 is sign-extended to 64 bits on the wire
        value = ((value + (1 << 31)) & ((1 << 32) - 1)) - (1 << 31)
    return value


def _scalar(kind: str, value) -> bytes:
    if kind == "double":
        return struct.pack("<d", float(value))
    if kind == "bool":
        return b"\x01" if value else b"\x00"
    return _varint(int(value))


def _is_default(kind, value) -> bool:
    if isinstance(kind, dict):
        return False
    return value in ("", 0, 0.0, False) and not (kind == "double" and _negative_zero(value))


def _negative_zero(value) -> bool:
    return isinstance(value, float) and value == 0.0 and struct.pack("<d", value)[7] == 0x80


def encode(message: dict, schema: Schema) -> bytes:
    """Serialize ``message`` the way protobuf's proto3 serializer does.

    Fields in number order, scalars at their default value left out, repeated
    numeric scalars packed.  Keys not in the schema are an error, so a typo
    cannot silently drop a request field.
    """
    names = {name for name, _, _ in schema.values()}
    unknown = set(message) - names
    if unknown:
        raise KeyError(f"not in the schema: {sorted(unknown)}")
    out = bytearray()
    for number in sorted(schema):
        name, kind, repeated = schema[number]
        value = message.get(name)
        if value is None:
            continue
        if repeated:
            values = list(value)
            if not values:
                continue
            if kind in _PACKABLE:
                body = b"".join(_scalar(kind, v) for v in values)
                out += _varint(number << 3 | _LEN) + _varint(len(body)) + body
                continue
        else:
            if _is_default(kind, value):
                continue
            values = [value]
        for item in values:
            if isinstance(kind, dict):
                body = encode(item, kind)
                out += _varint(number << 3 | _LEN) + _varint(len(body)) + body
            elif kind == "string":
                body = str(item).encode("utf-8")
                out += _varint(number << 3 | _LEN) + _varint(len(body)) + body
            elif kind == "double":
                out += _varint(number << 3 | _I64) + _scalar(kind, item)
            else:
                out += _varint(number << 3 | _VARINT) + _scalar(kind, item)
    return bytes(out)


def _default(kind, repeated: bool):
    if repeated:
        return []
    if isinstance(kind, dict):
        return None
    return {"string": "", "bool": False, "double": 0.0}.get(kind, 0)


def _convert(kind, wire: int, raw) -> Any:
    if isinstance(kind, dict):
        if wire != _LEN:
            raise ValueError("a message field arrived with a scalar wire type")
        return decode(bytes(raw), kind)
    if kind == "string":
        return bytes(raw).decode("utf-8")
    if kind == "double":
        if wire != _I64:
            raise ValueError("a double arrived with the wrong wire type")
        return struct.unpack("<d", bytes(raw))[0]
    if wire != _VARINT:
        raise ValueError(f"a {kind} arrived with the wrong wire type")
    if kind == "bool":
        return raw != 0
    return _signed(raw, 32 if kind == "int32" else 64)


def _unpack(kind: str, body: bytes) -> list:
    if kind == "double":
        return list(struct.unpack(f"<{len(body) // 8}d", body))
    out, pos = [], 0
    while pos < len(body):
        raw, pos = _read_varint(body, pos)
        out.append(_convert(kind, _VARINT, raw))
    return out


def decode(data: bytes, schema: Schema) -> dict:
    """Parse a protobuf message into a dict; absent fields take proto3 defaults.

    Unknown fields are skipped, as protobuf does, so a service that grows a
    field does not break this reader.
    """
    message = {name: _default(kind, repeated) for name, kind, repeated in schema.values()}
    pos, end = 0, len(data)
    while pos < end:
        key, pos = _read_varint(data, pos)
        number, wire = key >> 3, key & 7
        raw: Any
        if wire == _VARINT:
            raw, pos = _read_varint(data, pos)
        elif wire == _I64:
            raw, pos = data[pos:pos + 8], pos + 8
        elif wire == _LEN:
            size, pos = _read_varint(data, pos)
            raw, pos = data[pos:pos + size], pos + size
        elif wire == _I32:
            raw, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        if pos > end:
            raise ValueError("truncated message")
        entry = schema.get(number)
        if entry is None:
            continue
        name, kind, repeated = entry
        if repeated and wire == _LEN and kind in _PACKABLE:
            message[name].extend(_unpack(kind, bytes(raw)))
        elif repeated:
            message[name].append(_convert(kind, wire, raw))
        else:
            message[name] = _convert(kind, wire, raw)
    return message


# -- the service's messages (section_correction_v2.proto) --------------------
_TIMESTAMP: Schema = {1: ("seconds", "int64", False), 2: ("nanos", "int32", False)}
# viking.geometry.v1 (Common/geometry.proto)
_VECTOR2: Schema = {1: ("x", "double", False), 2: ("y", "double", False)}
_GRID_CELL: Schema = {1: ("row", "int32", False), 2: ("col", "int32", False)}

_SET_ID: Schema = {1: ("volume_name", "string", False), 2: ("stos_group", "string", False)}
_VOLUME_INFO: Schema = {
    1: ("volume_name", "string", False),
    2: ("vikingxml_endpoint", "string", False),
    3: ("annotation_endpoint", "string", False),
}
_CORRECTION_SET: Schema = {
    1: ("id", _SET_ID, False),
    2: ("built_utc", _TIMESTAMP, False),
    3: ("pitch_nm", "double", False),
    4: ("kernel_radius_nm", "double", False),
    5: ("z", "int64", True),  # the sections with a map, ascending
    # The damaged sections the field was built with, ascending: the volume's
    # NonStosSectionNumbers (service 1.6.0; RC1's from 1.7.0).  Empty both
    # when the volume lists none and when its list could not be read.
    6: ("damaged_z", "int64", True),
}
# Node (row, col) sits at (col * pitch_nm, row * pitch_nm); fields 1 and 2
# (gx, gy) are reserved.
_LATTICE_NODE: Schema = {
    3: ("offset", _VECTOR2, False),
    4: ("votes", "int32", False),
    5: ("node", _GRID_CELL, False),
}
_SECTION_POINTS: Schema = {1: ("z", "int64", False), 2: ("points", _VECTOR2, True)}
_POINT_OFFSET: Schema = {1: ("offset", _VECTOR2, False), 2: ("trusted", "bool", False)}
_SECTION_OFFSETS: Schema = {1: ("found", "bool", False), 2: ("points", _POINT_OFFSET, True)}
_CORRECTED_LOCATION: Schema = {
    1: ("location_id", "int64", False), 2: ("structure_id", "int64", False),
    3: ("z", "int64", False),
    4: ("position", _VECTOR2, False),  # corrected, volume nm
    5: ("offset", _VECTOR2, False),  # the correction alone
}

EMPTY_SCHEMA: Schema = {}
LIST_VOLUMES_RESPONSE_SCHEMA: Schema = {1: ("volumes", _VOLUME_INFO, True)}
REBUILD_STATUS_SCHEMA: Schema = {
    1: ("in_progress", "bool", False),
    2: ("last_started_utc", _TIMESTAMP, False),
    3: ("last_completed_utc", _TIMESTAMP, False),
    4: ("last_error", "string", False),
    # Part of every AnswerStamp; moves with an algorithm deploy that
    # republishes no field (#ai 1791401830.735369, 2026-10-07).
    5: ("service_version", "string", False),
    6: ("current_volume", "string", False),
    7: ("current_step", "string", False),
    8: ("groups_done", "int32", False),
    9: ("groups_total", "int32", False),
}
LIST_SETS_REQUEST_SCHEMA: Schema = {1: ("volume_name", "string", False)}
LIST_SETS_RESPONSE_SCHEMA: Schema = {1: ("sets", _CORRECTION_SET, True)}
SECTION_REQUEST_SCHEMA: Schema = {1: ("set", _SET_ID, False), 2: ("z", "int64", False)}
SECTION_CORRECTION_SCHEMA: Schema = {
    1: ("pitch_nm", "double", False),
    2: ("nodes", _LATTICE_NODE, True),
}
CORRECT_POINTS_REQUEST_SCHEMA: Schema = {
    1: ("set", _SET_ID, False), 2: ("sections", _SECTION_POINTS, True),
}
# One entry per request section, in request order; points in request order.
CORRECT_POINTS_RESPONSE_SCHEMA: Schema = {1: ("sections", _SECTION_OFFSETS, True)}
CORRECT_STRUCTURES_REQUEST_SCHEMA: Schema = {
    1: ("set", _SET_ID, False),
    2: ("structure_ids", "int64", True),
    3: ("include_children", "bool", False),
    4: ("correction", "int32", False),  # CORRECTION_MODES; 0 (unset) is the field
    # The curve fit's extent ratio, once [Viking-Server] adds it to the request
    # (a neighbor more than this many times the node's XY extent is left out of
    # the fit; Slack #ai 1792522897.715849, not in V2 on 2026-10-26).  Then the
    # ratio the Settings tab's Correction log is set to goes here too, and
    # joins _structures_key; until then it sets only the log's own test
    # (sbfsem.analysis.correction_checks).
}
#: Everything a ``CorrectStructures`` answer depends on: two answers to the
#: same request with equal stamps are identical.  Also what ``GetAnswerStamp``
#: answers, without loading the volume or fitting.
ANSWER_STAMP_SCHEMA: Schema = {
    1: ("service_version", "string", False),
    2: ("field_built_utc", _TIMESTAMP, False),
    3: ("annotation_fingerprint", "string", False),
    4: ("annotation_watermark", _TIMESTAMP, False),
}
CORRECT_STRUCTURES_RESPONSE_SCHEMA: Schema = {
    1: ("locations", _CORRECTED_LOCATION, True),
    2: ("stamp", ANSWER_STAMP_SCHEMA, False),
}


def _timestamp(message: dict | None) -> datetime | None:
    """A ``google.protobuf.Timestamp`` as an aware UTC datetime; unset is ``None``."""
    if not message or (not message.get("seconds") and not message.get("nanos")):
        return None
    return datetime.fromtimestamp(
        message["seconds"] + message.get("nanos", 0) / 1e9, tz=UTC)


# -- what the service describes ----------------------------------------------
@dataclass(frozen=True)
class CorrectionSet:
    """One published StosGroup field of one volume, and how it was built.

    ``sections`` (the sections with a map, ascending) is what
    ``ListCorrectionSets`` says; it is not part of :meth:`describe`, so a cell's
    status and the disk cache do not carry a thousand section numbers, and it
    is left out of comparisons.  ``damaged`` (service 1.6.0's ``damaged_z``)
    is the volume's damaged sections, the ones its slice-to-volume
    registration was built without (``NonStosSectionNumbers``): they correct
    no other section, and each gets its own shift.  A few dozen at most, so
    :meth:`describe` carries them; empty when the volume lists none, the
    service could not read the list, or the service predates 1.6.0, and
    ``None`` from a description kept on disk before 2.43.0.  Protocol V1 also
    named the annotation watermark the field was built from and the votes a
    lattice node needed; V2 does not.
    """

    volume_name: str
    stos_group: str
    built_utc: datetime | None = None
    pitch_nm: float = 0.0
    kernel_radius_nm: float = 0.0
    sections: tuple[int, ...] = field(default=(), compare=False, repr=False)
    damaged: tuple[int, ...] | None = ()

    @classmethod
    def from_message(cls, message: dict, volume_name: str = "") -> CorrectionSet:
        """From a V2 ``CorrectionSet`` message."""
        set_id = message.get("id") or {}
        return cls(
            volume_name=set_id.get("volume_name") or volume_name,
            stos_group=set_id.get("stos_group", ""),
            built_utc=_timestamp(message.get("built_utc")),
            pitch_nm=float(message.get("pitch_nm") or 0.0),
            kernel_radius_nm=float(message.get("kernel_radius_nm") or 0.0),
            sections=tuple(int(z) for z in message.get("z") or ()),
            damaged=tuple(sorted({int(z) for z in message.get("damaged_z") or ()})),
        )

    @classmethod
    def from_description(cls, d: dict) -> CorrectionSet:
        """Inverse of :meth:`describe` (what the disk cache keeps)."""
        def when(v):
            return datetime.fromisoformat(v) if v else None
        return cls(
            volume_name=d.get("volume_name", ""),
            stos_group=d.get("stos_group", ""),
            built_utc=when(d.get("built_utc")),
            pitch_nm=float(d.get("pitch_nm") or 0.0),
            kernel_radius_nm=float(d.get("kernel_radius_nm") or 0.0),
            damaged=(tuple(int(z) for z in d["damaged_z"])
                     if d.get("damaged_z") is not None else None),
        )

    def describe(self) -> dict:
        return {
            "volume_name": self.volume_name,
            "stos_group": self.stos_group,
            "built_utc": self.built_utc.isoformat() if self.built_utc else None,
            "pitch_nm": self.pitch_nm,
            "kernel_radius_nm": self.kernel_radius_nm,
            "damaged_z": list(self.damaged) if self.damaged is not None else None,
        }


def choose_set(sets: Iterable[CorrectionSet], preferred: str | None = None) -> CorrectionSet | None:
    """The set to apply: ``preferred`` when it is published, else the first by name.

    The same rule as Viking's DataExport (``Corrections:StosGroup``, else the
    alphabetically first set).  A preferred group that is not published is
    not silently replaced by another.
    """
    sets = sorted(sets, key=lambda s: s.stos_group.lower())
    if preferred:
        for s in sets:
            if s.stos_group.lower() == preferred.lower():
                return s
        return None
    return sets[0] if sets else None


# -- the client ----------------------------------------------------------------
def _parse_target(target: str) -> tuple[str, bool]:
    """``https://host[:port]``, ``http://host:port`` or ``host:port`` -> (host:port, TLS?)."""
    target = target.strip().rstrip("/")
    secure = True
    if target.startswith("https://"):
        target = target[len("https://"):]
    elif target.startswith("http://"):
        target, secure = target[len("http://"):], False
    if ":" not in target.rsplit("]", 1)[-1]:
        target += ":443" if secure else ":80"
    return target, secure


class CorrectionsClient:
    """Unary calls to ``AnnotateSectionCorrections``, one HTTP/2 channel reused.

    ``grpc`` is imported on first use: a library user who never asks for
    corrections needs no gRPC at all.
    """

    #: A ``CorrectPoints`` answer is about 45 bytes a point; the default 4 MB
    #: receive limit would refuse a large cell's.
    MAX_MESSAGE_BYTES = 64 * 1024 * 1024

    def __init__(self, target: str | None = None, *, timeout: float = 20.0):
        raw = target or os.environ.get("SBFSEM_CORRECTIONS_URL") or DEFAULT_TARGET
        self.address, self.secure = _parse_target(raw)
        self.timeout = timeout
        self._channel = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"CorrectionsClient({'https' if self.secure else 'http'}://{self.address})"

    @property
    def url(self) -> str:
        return f"{'https' if self.secure else 'http'}://{self.address}"

    def _get_channel(self):
        import grpc

        with self._lock:
            if self._channel is None:
                options = [
                    ("grpc.max_receive_message_length", self.MAX_MESSAGE_BYTES),
                    ("grpc.max_send_message_length", self.MAX_MESSAGE_BYTES),
                ]
                self._channel = (
                    grpc.secure_channel(self.address, grpc.ssl_channel_credentials(), options)
                    if self.secure else grpc.insecure_channel(self.address, options)
                )
            return self._channel

    def close(self) -> None:
        with self._lock:
            channel, self._channel = self._channel, None
        if channel is not None:
            channel.close()

    def __enter__(self) -> CorrectionsClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def call(self, method: str, request: dict, request_schema: Schema,
             response_schema: Schema, *, timeout: float | None = None) -> dict:
        """One unary RPC; gRPC failures become :class:`CorrectionsError` subclasses."""
        import grpc

        stub: Callable[..., dict] = self._get_channel().unary_unary(
            f"/{SERVICE}/{method}",
            request_serializer=lambda m: encode(m, request_schema),
            response_deserializer=lambda b: decode(b, response_schema),
        )
        try:
            return stub(request, timeout=timeout or self.timeout)
        except grpc.RpcError as exc:
            code = exc.code() if hasattr(exc, "code") else None
            details = exc.details() if hasattr(exc, "details") else str(exc)
            name = str(getattr(code, "name", code or ""))
            text = f"{method} at {self.url}: {name} {details}"
            if code == grpc.StatusCode.NOT_FOUND:
                error: CorrectionsError = CorrectionNotFound(text)
            elif code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                error = CorrectionsUnavailable(text)
            else:
                error = CorrectionsError(text)
            error.code = name
            error.details = str(details or "")
            raise error from exc

    # -- RPCs, in the proto's order ------------------------------------------
    def list_volumes(self) -> list[dict]:
        return self.call("ListVolumes", {}, EMPTY_SCHEMA, LIST_VOLUMES_RESPONSE_SCHEMA)["volumes"]

    def rebuild_status(self) -> dict:
        status = self.call("GetRebuildStatus", {}, EMPTY_SCHEMA, REBUILD_STATUS_SCHEMA)
        for key in ("last_started_utc", "last_completed_utc"):
            status[key] = _timestamp(status.get(key))
        return status

    def correction_sets(self, volume_name: str = "") -> list[CorrectionSet]:
        """Published sets of one volume, with their sections; every volume's when
        ``volume_name`` is empty."""
        answer = self.call("ListCorrectionSets", {"volume_name": volume_name},
                           LIST_SETS_REQUEST_SCHEMA, LIST_SETS_RESPONSE_SCHEMA)
        return [CorrectionSet.from_message(m, volume_name) for m in answer["sets"]]

    def corrected_sections(self, volume_name: str, stos_group: str) -> list[int]:
        """The sections of one set that have a map, ascending.

        From ``ListCorrectionSets``: V2 has no call of its own for it.  A set
        that is not published is :class:`CorrectionNotFound`, as V1 answered.
        """
        cset = choose_set(self.correction_sets(volume_name), stos_group)
        if cset is None:
            error = CorrectionNotFound(
                f"ListCorrectionSets at {self.url}: no set {volume_name} / {stos_group}")
            error.code = "NOT_FOUND"
            raise error
        return list(cset.sections)

    def section(self, volume_name: str, stos_group: str, z: int) -> dict:
        """The sparse lattice of one section: ``pitch_nm`` and its ``nodes``.

        A node's ``node`` ``{row, col}`` sits at ``(col, row) * pitch_nm``
        volume nm; its ``offset`` ``{x, y}`` (nm) is added to a volume point,
        and ``votes`` counts the annotations that support it.
        """
        return self.call("GetSectionCorrection",
                         {"set": {"volume_name": volume_name, "stos_group": stos_group},
                          "z": int(z)},
                         SECTION_REQUEST_SCHEMA, SECTION_CORRECTION_SCHEMA)

    def correct_points(self, volume_name: str, stos_group: str,
                       sections: dict[int, np.ndarray]) -> dict[int, dict]:
        """Sample the field.  ``sections`` maps Z to an ``(N, 2)`` array of volume nm.

        Returns ``{z: {"found": bool, "dx", "dy": (N,) nm, "trusted": (N,) bool}}``
        in the order the points were given.  The service refuses an empty
        request, an empty section and a repeated Z, so none is ever sent.  It
        answers the sections in the order they were asked for, without their
        Z, so an answer with another count of sections is an error.
        """
        sent = [(int(z), pts) for z, pts in sections.items() if len(pts)]
        if not sent:
            return {}
        request = {
            "set": {"volume_name": volume_name, "stos_group": stos_group},
            "sections": [{"z": z, "points": [{"x": float(x), "y": float(y)} for x, y in pts]}
                         for z, pts in sent],
        }
        answer = self.call("CorrectPoints", request,
                           CORRECT_POINTS_REQUEST_SCHEMA, CORRECT_POINTS_RESPONSE_SCHEMA)
        got = answer["sections"]
        if len(got) != len(sent):
            raise CorrectionsError(
                f"CorrectPoints answered {len(got)} sections; {len(sent)} were sent")
        out: dict[int, dict] = {}
        for (z, _), sec in zip(sent, got, strict=True):
            pts = sec["points"]
            offsets = [p["offset"] or _ZERO for p in pts]
            out[z] = {
                "found": bool(sec["found"]),
                "dx": np.array([o["x"] for o in offsets], dtype=float),
                "dy": np.array([o["y"] for o in offsets], dtype=float),
                "trusted": np.array([p["trusted"] for p in pts], dtype=bool),
            }
        return out

    def _structures_request(self, volume_name: str, stos_group: str,
                            structure_ids: Iterable[int], include_children: bool,
                            mode: str) -> dict:
        return {
            "set": {"volume_name": volume_name, "stos_group": stos_group},
            "structure_ids": [int(s) for s in structure_ids],
            "include_children": bool(include_children),
            # 0 or 1, never a reserved number (request_mode).
            "correction": CORRECTION_MODES[request_mode(mode)],
        }

    def correct_structures(self, volume_name: str, stos_group: str, structure_ids: Iterable[int],
                           *, include_children: bool = True, mode: str = "neighbor",
                           timeout: float | None = None) -> pd.DataFrame:
        """Viking's correction of whole structures: one row per Location.

        ``location_id``, ``structure_id``, ``z``; ``x``, ``y``: the corrected
        position, volume nm -- the Location's section X/Y mapped through
        ``stos_group``, then corrected; ``dx``, ``dy``: the correction alone.
        ``mode`` is a :data:`CORRECTION_MODES` name (a retired one asks for
        ``neighbor``, :func:`request_mode`).  The answer's stamp
        (:meth:`answer_stamp`'s, as the answer was made) is in ``attrs["stamp"]``.
        """
        request = self._structures_request(volume_name, stos_group, structure_ids,
                                           include_children, mode)
        answer = self.call("CorrectStructures", request, CORRECT_STRUCTURES_REQUEST_SCHEMA,
                           CORRECT_STRUCTURES_RESPONSE_SCHEMA, timeout=timeout)
        rows = answer["locations"]
        position = [r["position"] or _ZERO for r in rows]
        offset = [r["offset"] or _ZERO for r in rows]
        frame = pd.DataFrame({
            "location_id": np.array([r["location_id"] for r in rows], dtype=np.int64),
            "structure_id": np.array([r["structure_id"] for r in rows], dtype=np.int64),
            "z": np.array([r["z"] for r in rows], dtype=np.int64),
            "x": np.array([v["x"] for v in position], dtype=float),
            "y": np.array([v["y"] for v in position], dtype=float),
            "dx": np.array([v["x"] for v in offset], dtype=float),
            "dy": np.array([v["y"] for v in offset], dtype=float),
        })
        frame.attrs["stamp"] = _answer_stamp(answer["stamp"])
        return frame

    def answer_stamp(self, volume_name: str, stos_group: str, structure_ids: Iterable[int],
                     *, include_children: bool = True, mode: str = "neighbor") -> dict:
        """What ``CorrectStructures`` would stamp its answer to this request with
        now, from the service's SQL aggregates and set catalog alone (well under
        a second): ``service_version``, ``field_built_utc`` (``None`` when the
        mode samples no field), ``annotation_fingerprint`` (compare by
        equality) and ``annotation_watermark``."""
        request = self._structures_request(volume_name, stos_group, structure_ids,
                                           include_children, mode)
        return _answer_stamp(self.call("GetAnswerStamp", request,
                                       CORRECT_STRUCTURES_REQUEST_SCHEMA, ANSWER_STAMP_SCHEMA))


_ZERO = {"x": 0.0, "y": 0.0}


def _answer_stamp(message: dict | None) -> dict:
    message = message or {}
    return {
        "service_version": message.get("service_version", ""),
        "field_built_utc": _timestamp(message.get("field_built_utc")),
        "annotation_fingerprint": message.get("annotation_fingerprint", ""),
        "annotation_watermark": _timestamp(message.get("annotation_watermark")),
    }


# -- sampling and applying ---------------------------------------------------
def sample_offsets(client: CorrectionsClient, cset: CorrectionSet, z: np.ndarray,
                   x_nm: np.ndarray, y_nm: np.ndarray,
                   *, points_per_request: int = POINTS_PER_REQUEST) -> pd.DataFrame:
    """The field at each point: ``dx_nm``, ``dy_nm``, ``trusted``, ``found``, in input order.

    Points whose coordinates are not finite are not sent; they come back
    untrusted with a zero offset, as a point on an unpublished section does.
    """
    z = np.asarray(z, dtype=np.int64)
    x_nm = np.asarray(x_nm, dtype=float)
    y_nm = np.asarray(y_nm, dtype=float)
    n = len(z)
    dx, dy = np.zeros(n), np.zeros(n)
    trusted, found = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    ok = np.isfinite(x_nm) & np.isfinite(y_nm)
    groups: dict[int, np.ndarray] = {}
    for section in np.unique(z[ok]):
        groups[int(section)] = np.flatnonzero(ok & (z == section))

    batch: dict[int, np.ndarray] = {}
    size = 0

    def flush() -> None:
        nonlocal batch, size
        if not batch:
            return
        answer = client.correct_points(
            cset.volume_name, cset.stos_group,
            {s: np.column_stack([x_nm[rows], y_nm[rows]]) for s, rows in batch.items()})
        for s, rows in batch.items():
            got = answer.get(s)
            if got is None or len(got["dx"]) != len(rows):
                raise CorrectionsError(
                    f"CorrectPoints answered {0 if got is None else len(got['dx'])} points "
                    f"for section {s}; {len(rows)} were sent")
            dx[rows], dy[rows] = got["dx"], got["dy"]
            trusted[rows], found[rows] = got["trusted"], got["found"]
        batch, size = {}, 0

    for section, rows in groups.items():
        batch[section] = rows
        size += len(rows)
        if size >= points_per_request:
            flush()
    flush()
    return pd.DataFrame({"dx_nm": dx, "dy_nm": dy, "trusted": trusted, "found": found})


@dataclass
class CellCorrection:
    """The offsets applied to one loaded structure, and where they came from."""

    cset: CorrectionSet
    #: One row per annotation: ``ID``, ``Z``, ``dx_nm``, ``dy_nm``, ``trusted``,
    #: ``found``.  Untrusted rows keep their sampled offset here for the record;
    #: only trusted ones are applied.
    offsets: pd.DataFrame
    source_url: str = ""
    cached: bool = False
    extras: dict = field(default_factory=dict)
    #: Why an answer kept on disk was used without checking it against the
    #: service's ``GetAnswerStamp``: the call's error; ``None`` when it was
    #: checked, fetched, or not to be asked (:func:`fetch_structure_offsets`).
    stamp_error: CorrectionsError | None = field(default=None, repr=False)

    @property
    def applied(self) -> pd.DataFrame:
        return self.offsets[self.offsets["trusted"]]

    @property
    def token(self) -> str:
        """Short hash of the offsets actually applied, to the nanometer.

        What a cached render is keyed on: a rebuilt field that leaves this
        cell's offsets unchanged keeps its renders.
        """
        rows = self.applied.sort_values("ID")
        text = ";".join(
            f"{int(i)}:{round(float(a))}:{round(float(b))}"
            for i, a, b in zip(rows["ID"], rows["dx_nm"], rows["dy_nm"], strict=True))
        return hashlib.sha1(text.encode("ascii")).hexdigest()[:16]

    def summary(self) -> dict:
        """Counts and shift sizes (um) for a status line."""
        applied = self.applied
        mags = np.hypot(applied["dx_nm"], applied["dy_nm"]).to_numpy() / 1000.0
        moved = mags > 0.0005
        return {
            **self.cset.describe(),
            "service": self.source_url,
            "cached": self.cached,
            "n_annotations": int(len(self.offsets)),
            "n_sections": int(self.offsets["Z"].nunique()) if len(self.offsets) else 0,
            "n_found": int(self.offsets["found"].sum()),
            "n_trusted": int(len(applied)),
            "n_moved": int(moved.sum()),
            "median_um": float(np.median(mags[moved])) if moved.any() else 0.0,
            "p95_um": float(np.percentile(mags[moved], 95)) if moved.any() else 0.0,
            "max_um": float(mags.max()) if len(mags) else 0.0,
            "token": self.token,
            **self.extras,
        }


def apply_offsets(neuron, correction: CellCorrection) -> None:
    """Move ``neuron``'s annotations and outlines by the trusted offsets.

    Sets ``CorrDXnm``/``CorrDYnm`` on every node row (zero where the field has
    no trusted sample), recomputes the micron columns, shifts each closed-curve
    outline by its annotation's offset, and records the correction on
    ``neuron.section_correction``.  Run it on a freshly loaded neuron: a second
    call would shift the outlines twice.
    """
    if getattr(neuron, "section_correction", None):
        raise ValueError(f"c{neuron.id} already carries a section correction")
    applied = correction.applied.drop_duplicates("ID").set_index("ID")
    nodes = neuron.nodes
    ids = nodes["ID"].astype(np.int64)
    nodes["CorrDXnm"] = ids.map(applied["dx_nm"]).fillna(0.0).astype(float)
    nodes["CorrDYnm"] = ids.map(applied["dy_nm"]).fillna(0.0).astype(float)
    neuron.apply_transform()

    geometries = getattr(neuron, "geometries", None)
    if geometries is not None and len(geometries):
        frame = geometries.copy()
        shifted = []
        for row in frame.itertuples():
            curves = row.Curve or []
            key = int(row.ID)
            if key in applied.index:
                shift = np.array([applied.at[key, "dx_nm"], applied.at[key, "dy_nm"]]) / 1000.0
                curves = [np.asarray(c, dtype=float) + shift for c in curves]
            shifted.append(curves)
        frame["Curve"] = shifted
        neuron.geometries = frame
    neuron.section_correction = correction.summary()


def _offsets_key(client: CorrectionsClient, cset: CorrectionSet, neuron) -> str:
    stamp = neuron.data_stamp.isoformat() if getattr(neuron, "data_stamp", None) else "unstamped"
    built = cset.built_utc.isoformat() if cset.built_utc else "unbuilt"
    sx, sy = float(neuron.volume_scale[0]), float(neuron.volume_scale[1])
    live = (getattr(neuron, "live_registration", None) or {}).get("token") or "stored"
    return (f"corrections:{client.url}/{cset.volume_name}/{cset.stos_group}@{built}"
            f"/c{int(neuron.id)}/{stamp}/{len(neuron.nodes)}/{sx:g},{sy:g}/{live}")


def fetch_offsets(neuron, client: CorrectionsClient, cset: CorrectionSet, *,
                  cache=None, allow_fetch: bool = True) -> CellCorrection:
    """The field at every annotation of ``neuron`` (its own and its children's).

    Sampled at the stored volume position (``VolumeX`` x scale, nm) on the
    annotation's own section.  With a ``cache``
    (:class:`~sbfsem.io.cache.DiskCache`), a cell whose annotations and field
    are unchanged is answered from disk; ``allow_fetch=False`` answers only
    from there and raises :class:`CorrectionsUnavailable` otherwise.
    """
    nodes = neuron.nodes
    key = _offsets_key(client, cset, neuron)
    cached = cache.get(key) if cache is not None else None
    if cached is not None and len(cached.get("ID", [])) == len(nodes):
        return CellCorrection(cset, pd.DataFrame(cached), client.url, cached=True)
    if not allow_fetch:
        raise CorrectionsUnavailable(
            f"c{neuron.id}: no offsets on disk for {cset.volume_name} {cset.stos_group}")
    sx, sy = float(neuron.volume_scale[0]), float(neuron.volume_scale[1])
    x_nm = nodes["VolumeX"].to_numpy(dtype=float) * sx
    y_nm = nodes["VolumeY"].to_numpy(dtype=float) * sy
    # Where the current section transforms put them, if they were applied:
    # the field is built in that frame.
    if "LiveDXnm" in nodes.columns:
        x_nm = x_nm + nodes["LiveDXnm"].fillna(0.0).to_numpy(dtype=float)
        y_nm = y_nm + nodes["LiveDYnm"].fillna(0.0).to_numpy(dtype=float)
    sampled = sample_offsets(client, cset, nodes["Z"].to_numpy(), x_nm, y_nm)
    offsets = pd.concat(
        [nodes[["ID", "Z"]].reset_index(drop=True).astype("int64"), sampled], axis=1)
    if cache is not None:
        cache.set(key, {c: offsets[c].tolist() for c in offsets.columns})
    return CellCorrection(cset, offsets, client.url)


#: A Location the service places farther than this from where this cell has it
#: is not moved: its correction was worked out somewhere else.  Until
#: 2026-09-24 23:19 UTC the service mapped a line or curve with empty X/Y
#: columns from section (0, 0), about 200 um away; RC1 c476's stored positions
#: sit up to 6.2 um from the service's mapping, well inside.
PLACEMENT_LIMIT_NM = 20_000.0


def _structures_key(client: CorrectionsClient, cset: CorrectionSet, neuron, mode: str) -> str:
    # ``mode`` is a request_mode name: a retired one is kept under ``neighbor``.
    stamp = neuron.data_stamp.isoformat() if getattr(neuron, "data_stamp", None) else "unstamped"
    # v2: trusted by PLACEMENT_LIMIT_NM, not by empty X/Y columns.  v3: and by
    # the service's own stamp, not the set's build time alone.  v4: that stamp
    # is the service version, which a restart does not move.  v5: the answer is
    # kept with the AnswerStamp it came with and checked against GetAnswerStamp
    # (_stamp_record), so the key names neither the set's build time nor the
    # service version.  The curve fit's extent ratio joins it once the request
    # carries it.
    return (f"corrections:{client.url}/{cset.volume_name}/{cset.stos_group}"
            f"/structures-v5:{mode}/c{int(neuron.id)}/{stamp}/{len(neuron.nodes)}")


def _stamp_record(stamp: dict | None) -> dict | None:
    """An ``AnswerStamp`` as it is kept on disk and compared, times as ISO text.

    ``None`` for a stamp without an annotation fingerprint: it vouches for no
    answer, so an answer kept with it is asked for again.
    """
    if not stamp or not stamp.get("annotation_fingerprint"):
        return None
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in stamp.items()}


def fetch_structure_offsets(neuron, client: CorrectionsClient, cset: CorrectionSet,
                            mode: str = "neighbor", *, cache=None, allow_fetch: bool = True,
                            timeout: float | None = None) -> CellCorrection:
    """``CorrectStructures`` for ``neuron`` and its child structures, as offsets.

    The correction alone (``dx``/``dy``) is what moves an annotation, from
    wherever this cell has it: the service's own mapping of the section X/Y
    through ``cset``'s StosGroup is the live registration's.  Not trusted, so
    not moved: a Location the service did not return, and one it placed more
    than :data:`PLACEMENT_LIMIT_NM` from where this cell has it (the live
    registration's position, else the stored one).

    With a ``cache``, the answer is kept on disk with the ``AnswerStamp`` it
    came with, and a later load of the same annotations asks
    ``GetAnswerStamp`` (:meth:`CorrectionsClient.answer_stamp`: about 0.1 s,
    against 6 s for RC1 c410's fit) instead of the fit.  Equal stamps mean an
    identical answer ([Viking-Server]), so the kept one is used; a stamp that
    moved -- a new service version, a rebuilt field, an annotation of the
    structures added, changed or deleted -- asks ``CorrectStructures`` again.
    ``mode`` is read by :func:`request_mode`: a retired one asks for the field.
    ``allow_fetch=False`` uses the kept answer without asking, and so does a
    stamp the service cannot give, with the error in ``stamp_error`` and
    ``extras["service_note"]``.
    """
    mode = request_mode(mode)
    nodes = neuron.nodes
    key = _structures_key(client, cset, neuron, mode)
    kept = (cache.get(key) if cache is not None else None) or {}
    if len(kept.get("offsets", {}).get("ID", [])) == len(nodes):
        made = kept.get("stamp")
        # The service version that made the answer, for the cell's status and the correction log.
        stored = CellCorrection(
            cset, pd.DataFrame(kept["offsets"]), client.url, cached=True,
            extras={"mode": mode, "service_version": (made or {}).get("service_version", "")})
        if not allow_fetch:
            return stored
        if made is not None:
            try:
                now = _stamp_record(client.answer_stamp(
                    cset.volume_name, cset.stos_group, [int(neuron.id)],
                    include_children=True, mode=mode))
            except CorrectionsError as exc:
                stored.stamp_error = exc
                stored.extras["service_note"] = str(exc)
                return stored
            if now == made:
                return stored
            moved = sorted(k for k in made if (now or {}).get(k) != made[k])
            log.info("c%s %s: the %s answer's stamp moved (%s); asked again",
                     neuron.id, cset.volume_name, mode, ", ".join(moved))
    elif not allow_fetch:
        raise CorrectionsUnavailable(
            f"c{neuron.id}: no {mode} offsets on disk for {cset.volume_name} {cset.stos_group}")
    answer = client.correct_structures(cset.volume_name, cset.stos_group, [int(neuron.id)],
                                       include_children=True, mode=mode, timeout=timeout)
    got = answer.drop_duplicates("location_id").set_index("location_id")
    ids = nodes["ID"].astype("int64")
    found = ids.isin(got.index).to_numpy()
    # Where this cell has each annotation, and where the service had it
    # before correcting it; a row not returned compares as NaN, not near.
    sx, sy = float(neuron.volume_scale[0]), float(neuron.volume_scale[1])
    x_nm = nodes["VolumeX"].to_numpy(dtype=float) * sx
    y_nm = nodes["VolumeY"].to_numpy(dtype=float) * sy
    if "LiveDXnm" in nodes.columns:
        x_nm = x_nm + nodes["LiveDXnm"].fillna(0.0).to_numpy(dtype=float)
        y_nm = y_nm + nodes["LiveDYnm"].fillna(0.0).to_numpy(dtype=float)
    theirs_x = ids.map(got["x"] - got["dx"]).to_numpy(dtype=float)
    theirs_y = ids.map(got["y"] - got["dy"]).to_numpy(dtype=float)
    near = np.hypot(theirs_x - x_nm, theirs_y - y_nm) <= PLACEMENT_LIMIT_NM
    offsets = pd.DataFrame({
        "ID": ids.to_numpy(),
        "Z": nodes["Z"].astype("int64").to_numpy(),
        "dx_nm": ids.map(got["dx"]).fillna(0.0).to_numpy(dtype=float),
        "dy_nm": ids.map(got["dy"]).fillna(0.0).to_numpy(dtype=float),
        "trusted": found & near,
        "found": found,
    })
    made = answer.attrs.get("stamp") or {}
    if cache is not None:
        cache.set(key, {"stamp": _stamp_record(made),
                        "offsets": {c: offsets[c].tolist() for c in offsets.columns}})
    return CellCorrection(cset, offsets, client.url,
                          extras={"mode": mode, "service_version": made.get("service_version", "")})


def correct_neuron(neuron, client: CorrectionsClient | None = None, *,
                   stos_group: str | None = None, cache=None,
                   mode: str = "neighbor") -> CellCorrection | None:
    """Apply Viking's section correction to a freshly loaded neuron.

    ``mode`` ``neighbor`` (or ``True``, or a retired mode, :func:`correction_mode`)
    asks the service to correct the whole structure by the published residual
    field (``CorrectStructures``, :func:`fetch_structure_offsets`); off does
    nothing.  ``None`` (the neuron untouched) when the volume has no published
    set, or not the ``stos_group`` asked for.  Service failures propagate as
    :class:`CorrectionsError`.  Until 2026-10-27 ``neighbor`` sampled the field
    at the cell's positions (``CorrectPoints``, still :func:`fetch_offsets`).
    """
    mode = correction_mode(mode)
    if mode == "off":
        return None
    volume_name = neuron.volume.viking_name
    if not volume_name:
        # Asked with no name the service lists every volume's sets, and one of
        # another volume's would be chosen: a volume Viking does not know has none.
        return None
    own = client is None
    client = client or CorrectionsClient()
    try:
        cset = choose_set(client.correction_sets(volume_name), stos_group)
        if cset is None:
            return None
        correction = fetch_structure_offsets(neuron, client, cset, mode, cache=cache)
    finally:
        if own:
            client.close()
    apply_offsets(neuron, correction)
    return correction


class CorrectionsProvider:
    """What a long-running server needs around the client.

    The published sets of each volume are asked for at most every
    ``sets_ttl_s`` and remembered on disk; after the service fails, it is not
    asked again for ``retry_after_s``, and meanwhile a cell whose offsets are
    on disk is still corrected (its status says so).  A ``CorrectStructures``
    answer on disk is checked with ``GetAnswerStamp`` while the service is
    asked (:func:`fetch_structure_offsets`); a stamp it cannot give leaves the
    kept answer applied, with the error in its status, and is paused or held
    by the rules below as any other call is.  When the service answers
    that one volume is unavailable (:func:`names_volume`: it could not reach
    that volume's server), only that volume is held so, in the modes the same
    call answers (:data:`SAME_CALL`); on 2026-10-20 Cped's and Inferior's
    servers refused the service while RC1 answered, and every volume waited
    out the pause.  A correction that fails
    inside the service -- an exception there, not an unreachable host -- is
    not asked for again for ``volume_retry_s``: for that structure and mode
    when the service's handler threw on its request (:data:`REQUEST_CODES`),
    and for the whole volume and mode once ``volume_after`` different
    structures of it fail so in a row, or after any other error.  On
    2026-09-24 every RC1 ``CorrectStructures`` call threw after 5-15 s, which
    every RC1 load then waited out; on 2026-10-20 one failure of Cped c1 under
    ``all`` held all of Cped, and cells the service answered for again a
    minute later were drawn uncorrected for ten minutes.  ``offline`` never
    asks at all.
    """

    def __init__(self, target: str | None = None, *, cache=None,
                 stos_group: str | None = None, timeout: float = 15.0,
                 structures_timeout: float = 120.0,
                 sets_ttl_s: float = 600.0, retry_after_s: float = 120.0,
                 volume_retry_s: float = 600.0, volume_after: int = 2,
                 offline: bool = False):
        self.client = CorrectionsClient(target, timeout=timeout)
        #: ``CorrectStructures`` fits the whole cell: 5-10 s for c21768's 1,680
        #: Locations (2026-09-24), so it gets longer than the other calls.
        self.structures_timeout = structures_timeout
        self.cache = cache
        self.stos_group = stos_group or os.environ.get("SBFSEM_CORRECTIONS_STOS_GROUP") or None
        self.sets_ttl_s = sets_ttl_s
        self.retry_after_s = retry_after_s
        self.volume_retry_s = volume_retry_s
        self.volume_after = volume_after
        self.offline = offline
        self._sets: dict[str, tuple[float, list[CorrectionSet]]] = {}
        self._down_until = 0.0
        # (volume, mode) -> (monotonic time it may be asked again, the error)
        self._held: dict[tuple[str, str], tuple[float, str]] = {}
        # (volume, mode, structure) -> the same, after its request threw
        self._held_structures: dict[tuple[str, str, int], tuple[float, str]] = {}
        # (volume, mode) -> {structure: monotonic time its request threw}, the
        # failures since the service last answered for that volume and mode
        self._failing: dict[tuple[str, str], dict[int, float]] = {}
        self.last_error = ""
        self._lock = threading.Lock()

    def close(self) -> None:
        self.client.close()

    @property
    def paused(self) -> bool:
        """Offline, or inside the pause after a failure: the service is not asked."""
        return self.offline or time.monotonic() < self._down_until

    def held(self, volume_name: str, mode: str, structure: int | None = None) -> str:
        """Why this volume (or, with ``structure``, that structure of it) is
        not asked for ``mode`` just now: the service's error and until when
        (UTC); empty when it may be asked.  A held volume holds every structure.
        A retired ``mode`` is the field's (:func:`request_mode`)."""
        mode = request_mode(mode)
        now = time.monotonic()
        with self._lock:
            until, error = self._held.get((volume_name, mode), (0.0, ""))
            what = volume_name
            if until <= now and structure is not None:
                until, error = self._held_structures.get(
                    (volume_name, mode, int(structure)), (0.0, ""))
                what = f"c{int(structure)} of {volume_name}"
        left = until - now
        if left <= 0:
            return ""
        again = datetime.now(UTC) + timedelta(seconds=left)
        return f"{error} (not asked again for {what} until {again:%H:%M} UTC)"

    def paused_for(self, volume_name: str, mode: str, structure: int | None = None) -> bool:
        """Is a load of this volume (and ``structure``) in ``mode`` not to ask the service now?"""
        return self.paused or bool(self.held(volume_name, mode, structure))

    def _hold(self, volume_name: str, mode: str, exc: Exception,
              structure: int | None = None) -> None:
        """Hold ``structure`` after its request threw in the service, and the
        volume once ``volume_after`` structures have in a row; the volume at
        once after any other error, or with no ``structure``."""
        now = time.monotonic()
        until = now + self.volume_retry_s
        sid = (int(structure) if structure is not None
               and getattr(exc, "code", "") in REQUEST_CODES else None)
        alone = False
        with self._lock:
            if sid is not None:
                self._held_structures = {k: v for k, v in self._held_structures.items()
                                         if v[0] > now}
                self._held_structures[(volume_name, mode, sid)] = (until, str(exc))
                failing = {s: t for s, t in self._failing.get((volume_name, mode), {}).items()
                           if t > now - self.volume_retry_s}
                failing[sid] = now
                self._failing[(volume_name, mode)] = failing
                alone = len(failing) < self.volume_after
            if not alone:
                self._held[(volume_name, mode)] = (until, str(exc))
                self._failing.pop((volume_name, mode), None)
        if alone:
            log.warning("section correction (%s) failed for c%s of %s; not asked again for "
                        "%.0f s: %s", mode, structure, volume_name, self.volume_retry_s, exc)
        else:
            log.warning("section correction (%s) failed for %s; not asked again for %.0f s: %s",
                        mode, volume_name, self.volume_retry_s, exc)

    def _unavailable(self, volume_name: str, mode: str, exc: Exception) -> None:
        """The service could not reach this volume: hold it for ``retry_after_s``
        in every mode the call answers, a longer hold kept; other volumes are asked."""
        until = time.monotonic() + self.retry_after_s
        modes = SAME_CALL.get(mode, (mode,))
        with self._lock:
            for m in modes:
                if self._held.get((volume_name, m), (0.0, ""))[0] < until:
                    self._held[(volume_name, m)] = (until, str(exc))
        log.warning("section correction (%s) unavailable for %s; not asked again for %.0f s: %s",
                    "/".join(modes), volume_name, self.retry_after_s, exc)

    def _answered(self, volume_name: str, mode: str) -> None:
        """The service answered for this volume and mode: no failures in a row."""
        with self._lock:
            self._failing.pop((volume_name, mode), None)

    def _sets_key(self, volume_name: str) -> str:
        return f"corrections:{self.client.url}/sets/{volume_name}"

    def _remembered(self, volume_name: str) -> list[CorrectionSet] | None:
        with self._lock:
            held = self._sets.get(volume_name)
        if held is not None:
            return held[1]
        stored = self.cache.get(self._sets_key(volume_name)) if self.cache is not None else None
        if stored is None:
            return None
        return [CorrectionSet.from_description(d) for d in stored]

    def _failed(self, exc: Exception) -> None:
        with self._lock:
            self._down_until = time.monotonic() + self.retry_after_s
            self.last_error = str(exc)
        log.warning("section corrections unavailable: %s", exc)

    def sets(self, volume_name: str) -> tuple[list[CorrectionSet], bool]:
        """``(published sets, fresh?)``; raises :class:`CorrectionsUnavailable` with nothing to go on."""
        now = time.monotonic()
        with self._lock:
            held = self._sets.get(volume_name)
            down = self.offline or now < self._down_until
        if held is not None and now - held[0] < self.sets_ttl_s:
            return held[1], True
        if not down:
            try:
                sets = self.client.correction_sets(volume_name)
            except CorrectionsUnavailable as exc:
                self._failed(exc)
            else:
                with self._lock:
                    self._sets[volume_name] = (now, sets)
                if self.cache is not None:
                    self.cache.set(self._sets_key(volume_name), [s.describe() for s in sets])
                return sets, True
        remembered = self._remembered(volume_name)
        if remembered is None:
            raise CorrectionsUnavailable(
                "offline, and no correction set remembered" if self.offline
                else self.last_error or f"{self.client.url} is not answering")
        return remembered, False

    def _unchecked(self, volume_name: str, cset: CorrectionSet, mode: str,
                   correction: CellCorrection, structure: int) -> None:
        """A kept answer is applied because ``GetAnswerStamp`` failed: pause the
        service or hold the volume as for any unavailable call, and say why in
        the cell's status.  Another error is not held: the next load asks again."""
        exc = correction.stamp_error
        if isinstance(exc, CorrectionsUnavailable):
            if names_volume(exc, volume_name, cset.volume_name):
                self._unavailable(volume_name, mode, exc)
            else:
                self._failed(exc)
        else:
            log.warning("section correction (%s) of c%s of %s: the answer on disk was not "
                        "checked: %s", mode, structure, volume_name, exc)
        correction.extras["service_note"] = self.held(volume_name, mode, structure) or str(exc)

    def correct(self, neuron, mode: str = "neighbor") -> CellCorrection | None:
        """Apply Viking's correction to ``neuron`` in ``mode`` (see :func:`correct_neuron`).

        ``None`` when ``mode`` is off or its volume has no published set.  While the service is
        paused, or the volume or this structure is held after a failure in the
        service, only offsets on disk are used, and so they are when the
        service cannot say whether a ``CorrectStructures`` answer on disk is
        still its answer.
        """
        if correction_mode(mode) == "off":
            return None
        mode = request_mode(mode)
        volume_name = neuron.volume.viking_name
        if not volume_name:
            return None  # see correct_neuron: no name would list every volume's sets
        sets, fresh = self.sets(volume_name)
        cset = choose_set(sets, self.stos_group)
        if cset is None:
            return None
        structure = int(neuron.id)
        held = self.held(volume_name, mode, structure)
        # Sets asked for within sets_ttl_s are fresh during a pause as well.
        fresh = fresh and not self.paused
        try:
            correction = fetch_structure_offsets(
                neuron, self.client, cset, mode, cache=self.cache,
                allow_fetch=fresh and not held, timeout=self.structures_timeout)
            if correction.stamp_error is not None:
                self._unchecked(volume_name, cset, mode, correction, structure)
        except CorrectionsUnavailable as exc:
            if held:
                raise CorrectionsError(held) from exc
            if names_volume(exc, volume_name, cset.volume_name):
                self._unavailable(volume_name, mode, exc)
            elif fresh:
                self._failed(exc)
            raise
        except CorrectionNotFound:
            raise
        except CorrectionsError as exc:
            self._hold(volume_name, mode, exc, structure)
            raise
        if not correction.cached:
            self._answered(volume_name, mode)
        if held or not fresh:
            correction.extras["service_note"] = held or self.last_error or "offline"
        apply_offsets(neuron, correction)
        return correction
