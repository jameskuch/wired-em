"""Viking's section corrections: the residual registration field, per section.

James Anderson's service ``AnnotateSectionCorrections`` (package
``Viking.SectionCorrectionServiceTypes.gRPC.V1``; TLS gRPC at
``corrections.codepharm.net:443``, no auth; the proto is
``SectionCorrection/section_correction.proto`` in
github.com/jamesra/viking_gRPC_protos) publishes, per volume and StosGroup, a
sparse lattice of XY offsets for each section it could measure.  The field is
built from every traced process in the volume (Viking's
``SectionCorrectionBuilder``): where a process's annotation on one section sits
off the curve its neighbors above and below predict, the section is
misregistered there.  The offsets (predicted - actual, volume nanometers) are
pooled on a 2 um grid over an 8 um kernel and published where at least three
annotations vote.

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
residual field (``neighbor``), a leave-one-out Catmull-Rom fit along each traced
process (``curvefit``), or both, the field first (``all``).  It maps each
Location's section X/Y through the StosGroup itself and returns the correction
on top of that mapping; :func:`fetch_structure_offsets` applies that correction
to this cell's own positions, which with the live registration on are the same
mapping; a Location the service places more than :data:`PLACEMENT_LIMIT_NM`
away is left alone.  ``neighbor`` keeps sampling the field with
``CorrectPoints`` at this cell's positions.

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
    "correction_mode",
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
    "service_stamp",
    "stamp_version",
    "apply_offsets",
    "uncorrect_outlines",
    "correct_neuron",
    "CorrectionsProvider",
]

log = logging.getLogger(__name__)

#: Where the service is published (the pinned ``[Viking]`` post in Slack #ai,
#: 2026-09-22).  ``SBFSEM_CORRECTIONS_URL`` overrides it; an ``http://`` URL
#: means cleartext HTTP/2.
DEFAULT_TARGET = "https://corrections.codepharm.net"
SERVICE = "Viking.SectionCorrectionServiceTypes.gRPC.V1.AnnotateSectionCorrections"

#: Points per ``CorrectPoints`` request.  Whole sections are never split, so a
#: request may run over by one section's points.
POINTS_PER_REQUEST = 20_000

#: Viking's ``CorrectionMode``: what a correction applies.  ``neighbor`` is the
#: published residual field; ``curvefit`` a leave-one-out Catmull-Rom fit along
#: each traced process; ``all`` both, the field first -- what the service does
#: when a request leaves the mode unset.  ``none`` maps and corrects nothing.
CORRECTION_MODES: dict[str, int] = {"none": 0, "neighbor": 1, "curvefit": 2, "all": 3}


def correction_mode(value) -> str:
    """A correction setting as one name: ``off``, ``neighbor``, ``curvefit`` or ``all``.

    ``True`` is ``neighbor``: what the on/off switch always meant, the residual
    field.  ``False``, ``None``, ``"none"`` and the usual spellings of off are
    ``off``.  Case, ``-`` and ``_`` are ignored (``"CurveFit"``, ``"curve_fit"``).
    """
    if value is None or value is False:
        return "off"
    if value is True:
        return "neighbor"
    text = str(value).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    if text in ("", "off", "none", "0", "false", "no"):
        return "off"
    if text in ("1", "true", "yes", "on", "field", "residual"):
        return "neighbor"
    if text in CORRECTION_MODES:
        return text
    raise ValueError(f"not a correction mode: {value!r} (off, neighbor, curvefit or all)")


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
#: volume is unavailable: ``CorrectStructures`` maps each Location's section
#: X/Y through the volume's transforms for ``curvefit`` and ``all`` alike;
#: ``neighbor`` (``CorrectPoints``) samples the published field at positions
#: mapped here.
SAME_CALL: dict[str, tuple[str, ...]] = {
    "neighbor": ("neighbor",), "curvefit": ("curvefit", "all"), "all": ("curvefit", "all")}


class CorrectionNotFound(CorrectionsError):
    """``NOT_FOUND``: an unknown volume or StosGroup, or a section with no map."""


# -- protobuf wire format ----------------------------------------------------
_VARINT, _I64, _LEN, _I32 = 0, 1, 2, 5

#: A message schema: field number -> (name, kind, repeated).  ``kind`` is one
#: of ``string``, ``bool``, ``int32``, ``int64``, ``double``, or another schema
#: for a nested message.  A trailing ``?`` (``"int32?"``) marks a proto3
#: ``optional`` field: sent whenever it is set, even at its default, and read
#: as ``None`` when absent (``CorrectionMode.NONE`` is 0, and an unset mode
#: means something else).
Schema = dict[int, tuple[str, Any, bool]]


def _optional(kind) -> tuple[Any, bool]:
    """``(kind without its ?, whether it was optional)``."""
    if isinstance(kind, str) and kind.endswith("?"):
        return kind[:-1], True
    return kind, False

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
        kind, present = _optional(kind)
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
            if not present and _is_default(kind, value):
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
    kind, optional = _optional(kind)
    if isinstance(kind, dict) or optional:
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
        kind, _ = _optional(kind)
        if repeated and wire == _LEN and kind in _PACKABLE:
            message[name].extend(_unpack(kind, bytes(raw)))
        elif repeated:
            message[name].append(_convert(kind, wire, raw))
        else:
            message[name] = _convert(kind, wire, raw)
    return message


# -- the service's messages (section_correction.proto) -----------------------
_TIMESTAMP: Schema = {1: ("seconds", "int64", False), 2: ("nanos", "int32", False)}
_VOLUME_INFO: Schema = {
    1: ("volume_name", "string", False),
    2: ("vikingxml_endpoint", "string", False),
    3: ("annotation_endpoint", "string", False),
}
_RESIDUAL_WINDOW: Schema = {
    1: ("min_both", "int32", False), 2: ("max_both", "int32", False),
    3: ("min_one", "int32", False), 4: ("max_one", "int32", False),
}
_PROVENANCE: Schema = {
    1: ("built_utc", _TIMESTAMP, False),
    2: ("annotation_watermark", _TIMESTAMP, False),
    3: ("pitch_nm", "double", False),
    4: ("kernel_radius_nm", "double", False),
    5: ("min_annotation_votes", "int32", False),
    6: ("residual_window", _RESIDUAL_WINDOW, False),
}
_SET_INFO: Schema = {
    1: ("volume_name", "string", False),
    2: ("stos_group", "string", False),
    3: ("provenance", _PROVENANCE, False),
}
_LATTICE_VECTOR: Schema = {
    1: ("gx", "int32", False), 2: ("gy", "int32", False),
    3: ("dx", "double", False), 4: ("dy", "double", False),
    5: ("vote_count", "int32", False),
}
_VOLUME_XY: Schema = {1: ("x", "double", False), 2: ("y", "double", False)}
_SECTION_POINTS: Schema = {1: ("z", "int64", False), 2: ("points", _VOLUME_XY, True)}
_CORRECTED_XY: Schema = {
    1: ("x", "double", False), 2: ("y", "double", False),
    3: ("dx", "double", False), 4: ("dy", "double", False),
    5: ("trusted", "bool", False),
}
_CORRECTED_SECTION: Schema = {
    1: ("z", "int64", False),
    2: ("found_section", "bool", False),
    3: ("points", _CORRECTED_XY, True),
}
_CORRECTED_LOCATION: Schema = {
    1: ("location_id", "int64", False), 2: ("structure_id", "int64", False),
    3: ("z", "int64", False),
    4: ("x", "double", False), 5: ("y", "double", False),
    6: ("dx", "double", False), 7: ("dy", "double", False),
}

EMPTY_SCHEMA: Schema = {}
LIST_VOLUMES_RESPONSE_SCHEMA: Schema = {1: ("volumes", _VOLUME_INFO, True)}
REBUILD_STATUS_SCHEMA: Schema = {
    1: ("in_progress", "bool", False),
    2: ("last_started_utc", _TIMESTAMP, False),
    3: ("last_completed_utc", _TIMESTAMP, False),
    4: ("last_error", "string", False),
    5: ("last_volumes", "string", True),
    # Service 1.1.0 on; changes with an algorithm deploy that republishes no
    # field (tags from [Viking-Server], #ai 1791401830.735369, 2026-10-07).
    6: ("service_version", "string", False),
}
LIST_SETS_REQUEST_SCHEMA: Schema = {1: ("volume_name", "string", False)}
LIST_SETS_RESPONSE_SCHEMA: Schema = {1: ("sets", _SET_INFO, True)}
VOLUME_GROUP_SCHEMA: Schema = {
    1: ("volume_name", "string", False),
    2: ("stos_group", "string", False),
}
LIST_SECTIONS_RESPONSE_SCHEMA: Schema = {**VOLUME_GROUP_SCHEMA, 3: ("z", "int64", True)}
MANIFEST_SCHEMA: Schema = {
    **VOLUME_GROUP_SCHEMA,
    3: ("provenance", _PROVENANCE, False),
    4: ("volume_url", "string", False),
    5: ("z", "int64", True),
}
SECTION_REQUEST_SCHEMA: Schema = {**VOLUME_GROUP_SCHEMA, 3: ("z", "int64", False)}
SECTION_CORRECTION_SCHEMA: Schema = {
    **VOLUME_GROUP_SCHEMA,
    3: ("z", "int64", False),
    4: ("origin_x_nm", "double", False),
    5: ("origin_y_nm", "double", False),
    6: ("pitch_nm", "double", False),
    7: ("nodes", _LATTICE_VECTOR, True),
}
CORRECT_POINTS_REQUEST_SCHEMA: Schema = {
    **VOLUME_GROUP_SCHEMA, 3: ("sections", _SECTION_POINTS, True),
    # Unset: the residual field, all a loose point can have (no process to fit).
    4: ("correction", "int32?", False),
}
CORRECT_POINTS_RESPONSE_SCHEMA: Schema = {
    **VOLUME_GROUP_SCHEMA, 3: ("sections", _CORRECTED_SECTION, True),
}
# Given by [Viking-Server] in Slack #ai on 2026-09-24 (thread 1790282139.403739),
# before it reached the public proto repository.
CORRECT_STRUCTURES_REQUEST_SCHEMA: Schema = {
    **VOLUME_GROUP_SCHEMA,
    3: ("structure_ids", "int64", True),
    4: ("include_children", "bool", False),
    5: ("correction", "int32?", False),
    # 6: the curve fit's extent ratio, once [Viking-Server] deploys it (a
    # neighbor more than this many times the node's XY extent is left out of
    # the fit; Slack #ai 1792522897.715849, not assigned on 2026-10-20).  Then
    # the ratio the Settings tab's Correction log is set to goes here too, and
    # joins _structures_key; until then it sets only the log's own test
    # (sbfsem.analysis.correction_checks).
}
CORRECT_STRUCTURES_RESPONSE_SCHEMA: Schema = {
    **VOLUME_GROUP_SCHEMA, 3: ("locations", _CORRECTED_LOCATION, True),
    4: ("service_version", "string", False),
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
    """One published StosGroup field of one volume, and how it was built."""

    volume_name: str
    stos_group: str
    built_utc: datetime | None = None
    annotation_watermark: datetime | None = None
    pitch_nm: float = 0.0
    kernel_radius_nm: float = 0.0
    min_annotation_votes: int = 0

    @classmethod
    def from_message(cls, message: dict, volume_name: str = "") -> CorrectionSet:
        prov = message.get("provenance") or {}
        return cls(
            volume_name=message.get("volume_name") or volume_name,
            stos_group=message.get("stos_group", ""),
            built_utc=_timestamp(prov.get("built_utc")),
            annotation_watermark=_timestamp(prov.get("annotation_watermark")),
            pitch_nm=float(prov.get("pitch_nm") or 0.0),
            kernel_radius_nm=float(prov.get("kernel_radius_nm") or 0.0),
            min_annotation_votes=int(prov.get("min_annotation_votes") or 0),
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
            annotation_watermark=when(d.get("annotation_watermark")),
            pitch_nm=float(d.get("pitch_nm") or 0.0),
            kernel_radius_nm=float(d.get("kernel_radius_nm") or 0.0),
            min_annotation_votes=int(d.get("min_annotation_votes") or 0),
        )

    def describe(self) -> dict:
        return {
            "volume_name": self.volume_name,
            "stos_group": self.stos_group,
            "built_utc": self.built_utc.isoformat() if self.built_utc else None,
            "annotation_watermark": (
                self.annotation_watermark.isoformat() if self.annotation_watermark else None),
            "pitch_nm": self.pitch_nm,
            "kernel_radius_nm": self.kernel_radius_nm,
            "min_annotation_votes": self.min_annotation_votes,
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
        """Published sets of one volume; every volume's when ``volume_name`` is empty."""
        answer = self.call("ListCorrectionSets", {"volume_name": volume_name},
                           LIST_SETS_REQUEST_SCHEMA, LIST_SETS_RESPONSE_SCHEMA)
        return [CorrectionSet.from_message(m, volume_name) for m in answer["sets"]]

    def corrected_sections(self, volume_name: str, stos_group: str) -> list[int]:
        answer = self.call("ListCorrectedSections",
                           {"volume_name": volume_name, "stos_group": stos_group},
                           VOLUME_GROUP_SCHEMA, LIST_SECTIONS_RESPONSE_SCHEMA)
        return [int(z) for z in answer["z"]]

    def manifest(self, volume_name: str, stos_group: str) -> dict:
        answer = self.call("GetCorrectionManifest",
                           {"volume_name": volume_name, "stos_group": stos_group},
                           VOLUME_GROUP_SCHEMA, MANIFEST_SCHEMA)
        answer["set"] = CorrectionSet.from_message(answer, volume_name)
        return answer

    def section(self, volume_name: str, stos_group: str, z: int) -> dict:
        """The sparse lattice of one section: node ``(gx, gy)`` sits at ``(gx, gy) * pitch_nm``."""
        return self.call("GetSectionCorrection",
                         {"volume_name": volume_name, "stos_group": stos_group, "z": int(z)},
                         SECTION_REQUEST_SCHEMA, SECTION_CORRECTION_SCHEMA)

    def correct_points(self, volume_name: str, stos_group: str,
                       sections: dict[int, np.ndarray]) -> dict[int, dict]:
        """Sample the field.  ``sections`` maps Z to an ``(N, 2)`` array of volume nm.

        Returns ``{z: {"found": bool, "dx", "dy": (N,) nm, "trusted": (N,) bool}}``
        in the order the points were given.  The service refuses an empty
        request, an empty section and a repeated Z, so none is ever sent.
        """
        request = {
            "volume_name": volume_name,
            "stos_group": stos_group,
            "sections": [
                {"z": int(z), "points": [{"x": float(x), "y": float(y)} for x, y in pts]}
                for z, pts in sections.items() if len(pts)
            ],
        }
        if not request["sections"]:
            return {}
        answer = self.call("CorrectPoints", request,
                           CORRECT_POINTS_REQUEST_SCHEMA, CORRECT_POINTS_RESPONSE_SCHEMA)
        out: dict[int, dict] = {}
        for sec in answer["sections"]:
            pts = sec["points"]
            out[int(sec["z"])] = {
                "found": bool(sec["found_section"]),
                "dx": np.array([p["dx"] for p in pts], dtype=float),
                "dy": np.array([p["dy"] for p in pts], dtype=float),
                "trusted": np.array([p["trusted"] for p in pts], dtype=bool),
            }
        return out

    def correct_structures(self, volume_name: str, stos_group: str, structure_ids: Iterable[int],
                           *, include_children: bool = True, mode: str = "all",
                           timeout: float | None = None) -> pd.DataFrame:
        """Viking's correction of whole structures: one row per Location.

        ``location_id``, ``structure_id``, ``z``; ``x``, ``y``: the corrected
        position, volume nm -- the Location's section X/Y mapped through
        ``stos_group``, then corrected; ``dx``, ``dy``: the correction alone.
        ``mode`` is a :data:`CORRECTION_MODES` name, always sent (unset would
        mean ``all``).
        """
        request = {
            "volume_name": volume_name,
            "stos_group": stos_group,
            "structure_ids": [int(s) for s in structure_ids],
            "include_children": bool(include_children),
            "correction": CORRECTION_MODES[mode],
        }
        answer = self.call("CorrectStructures", request, CORRECT_STRUCTURES_REQUEST_SCHEMA,
                           CORRECT_STRUCTURES_RESPONSE_SCHEMA, timeout=timeout)
        return pd.DataFrame(answer["locations"],
                            columns=[name for name, _, _ in _CORRECTED_LOCATION.values()])


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
    #: only trusted ones are applied.  After :func:`uncorrect_outlines`, a ``kept``
    #: column marks the traced outlines left where the registration drew them:
    #: their offset is kept for the record and not applied.
    offsets: pd.DataFrame
    source_url: str = ""
    cached: bool = False
    extras: dict = field(default_factory=dict)

    @property
    def applied(self) -> pd.DataFrame:
        rows = self.offsets["trusted"]
        if "kept" in self.offsets.columns:
            rows = rows & ~self.offsets["kept"].astype(bool)
        return self.offsets[rows]

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
        """Counts and shift sizes (um) for a status line.

        The sizes are of the offsets applied; after :func:`uncorrect_outlines` the
        outlines kept are counted (``n_kept``) and named (``kept_outlines``).
        """
        applied = self.applied
        mags = np.hypot(applied["dx_nm"], applied["dy_nm"]).to_numpy() / 1000.0
        moved = mags > 0.0005
        out = {
            **self.cset.describe(),
            "service": self.source_url,
            "cached": self.cached,
            "n_annotations": int(len(self.offsets)),
            "n_sections": int(self.offsets["Z"].nunique()) if len(self.offsets) else 0,
            "n_found": int(self.offsets["found"].sum()),
            "n_trusted": int(self.offsets["trusted"].sum()),
            "n_moved": int(moved.sum()),
            "median_um": float(np.median(mags[moved])) if moved.any() else 0.0,
            "p95_um": float(np.percentile(mags[moved], 95)) if moved.any() else 0.0,
            "max_um": float(mags.max()) if len(mags) else 0.0,
            "token": self.token,
            **self.extras,
        }
        if "kept" in self.offsets.columns:
            kept = self.offsets.loc[self.offsets["kept"].astype(bool), "ID"]
            ids = sorted({int(i) for i in kept})
            out.update(keep_outlines=True, n_kept=len(ids), kept_outlines=ids)
        return out


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


def uncorrect_outlines(neuron, correction: CellCorrection, location_ids: Iterable[int]) -> list[int]:
    """Leave these traced outlines where the registration drew them: take the
    section correction off each, node row and outline, every other annotation
    keeping its own.

    For the outlines the correction log lists
    (:func:`sbfsem.analysis.correction_checks.scan_outlines`): Viking's curve fit
    takes an outline's centroid as a point of the process, and where an outline
    takes in a lobe it moves the next one off the membrane it lines up with.  Run
    it after :func:`apply_offsets` with the same ``correction``.  An ID that is
    not a traced outline of ``neuron`` or was not moved is not kept.

    The rows kept stay in ``correction.offsets`` (a ``kept`` column), not
    applied, so the summary's ``token`` and sizes are those of the offsets
    applied, and ``neuron.section_correction`` names the outlines kept.  Returns
    their IDs, ascending.
    """
    if not getattr(neuron, "section_correction", None):
        raise ValueError(f"c{neuron.id} carries no section correction to take off")
    applied = correction.applied.drop_duplicates("ID").set_index("ID")
    geometries = getattr(neuron, "geometries", None)
    traced: set[int] = set()
    if geometries is not None and len(geometries):
        traced = {int(row.ID) for row in geometries.itertuples() if row.Curve}
    wanted = {int(i) for i in location_ids}
    kept = sorted(i for i in wanted & traced & {int(i) for i in applied.index}
                  if applied.at[i, "dx_nm"] or applied.at[i, "dy_nm"])
    offsets = correction.offsets
    already = offsets["kept"].astype(bool) if "kept" in offsets.columns else False
    correction.offsets = offsets.assign(kept=already | offsets["ID"].astype(np.int64).isin(kept))
    if kept and geometries is not None:  # a kept ID has an outline, so the frame exists
        nodes = neuron.nodes
        rows = nodes["ID"].astype(np.int64).isin(kept)
        nodes.loc[rows, ["CorrDXnm", "CorrDYnm"]] = 0.0
        neuron.apply_transform()
        frame = geometries.copy()
        moved_back = []
        back = set(kept)
        for row in frame.itertuples():
            curves = row.Curve or []
            key = int(row.ID)
            if key in back:
                shift = np.array([applied.at[key, "dx_nm"], applied.at[key, "dy_nm"]]) / 1000.0
                curves = [np.asarray(c, dtype=float) - shift for c in curves]
            moved_back.append(curves)
        frame["Curve"] = moved_back
        neuron.geometries = frame
    neuron.section_correction = correction.summary()
    return kept


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


def _structures_key(client: CorrectionsClient, cset: CorrectionSet, neuron, mode: str,
                    service: str) -> str:
    stamp = neuron.data_stamp.isoformat() if getattr(neuron, "data_stamp", None) else "unstamped"
    built = cset.built_utc.isoformat() if cset.built_utc else "unbuilt"
    # v2: trusted by PLACEMENT_LIMIT_NM, not by empty X/Y columns.  v3: and by
    # the service's own stamp (service_stamp), not the set's build time alone.
    # v4: that stamp is the service version, which a restart does not move.
    # The curve fit's extent ratio joins it once it is sent (request field 6).
    return (f"corrections:{client.url}/{cset.volume_name}/{cset.stos_group}@{built}"
            f"/structures-v4:{mode}@{service}/c{int(neuron.id)}/{stamp}/{len(neuron.nodes)}")


def service_stamp(client: CorrectionsClient) -> str:
    """What changes when ``CorrectStructures`` may answer differently.

    The service's ``service_version`` (1.1.0 on), which moves with an
    algorithm deploy that republishes no field: on 2026-09-25 05:56 UTC its
    base moved to the stored positions while every set kept its
    ``built_utc``.  An older service without one falls back to when it last
    finished a rebuild, which it does on every start, so every restart
    refetched every cell.  ``""`` if it cannot say.  A cached answer is keyed
    on it.
    """
    try:
        status = client.rebuild_status()
    except CorrectionsUnavailable:
        raise
    except CorrectionsError:
        return ""
    version = status.get("service_version")
    if version:
        return f"v{version}"
    done = status.get("last_completed_utc")
    return done.isoformat() if done else ""


def stamp_version(stamp: str | None) -> str:
    """The service version a :func:`service_stamp` names (``"v1.2.0"`` -> ``"1.2.0"``).

    ``""`` for a stamp that is a rebuild time (a service older than 1.1.0) or
    nothing (``""``, ``"unknown"``).
    """
    text = str(stamp or "")
    return text[1:] if text[:1] == "v" and text[1:2].isdigit() else ""


def fetch_structure_offsets(neuron, client: CorrectionsClient, cset: CorrectionSet,
                            mode: str = "curvefit", *, cache=None, allow_fetch: bool = True,
                            timeout: float | None = None,
                            service: str = "unknown") -> CellCorrection:
    """``CorrectStructures`` for ``neuron`` and its child structures, as offsets.

    The correction alone (``dx``/``dy``) is what moves an annotation, from
    wherever this cell has it: the service's own mapping of the section X/Y
    through ``cset``'s StosGroup is the live registration's.  Not trusted, so
    not moved: a Location the service did not return, and one it placed more
    than :data:`PLACEMENT_LIMIT_NM` from where this cell has it (the live
    registration's position, else the stored one).  With a ``cache``, a cell
    whose annotations, set and ``service`` stamp (:func:`service_stamp`) are
    unchanged is answered from disk; ``allow_fetch=False`` answers only from there.
    """
    nodes = neuron.nodes
    key = _structures_key(client, cset, neuron, mode, service or "unknown")
    # The version the answer is keyed on, for the cell's status and the correction log.
    extras = {"mode": mode, "service_version": stamp_version(service)}
    cached = cache.get(key) if cache is not None else None
    if cached is not None and len(cached.get("ID", [])) == len(nodes):
        return CellCorrection(cset, pd.DataFrame(cached), client.url, cached=True,
                              extras=extras)
    if not allow_fetch:
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
    if cache is not None:
        cache.set(key, {c: offsets[c].tolist() for c in offsets.columns})
    return CellCorrection(cset, offsets, client.url, extras=extras)


def correct_neuron(neuron, client: CorrectionsClient | None = None, *,
                   stos_group: str | None = None, cache=None,
                   mode: str = "neighbor", keep_outlines: bool = False) -> CellCorrection | None:
    """Apply Viking's section correction to a freshly loaded neuron.

    ``mode`` ``neighbor`` samples the published residual field at each
    annotation (``CorrectPoints``); ``curvefit`` and ``all`` ask the service to
    correct the whole structure (``CorrectStructures``).  ``None`` (the neuron
    untouched) when the volume has no published set, or not the
    ``stos_group`` asked for.  Service failures propagate as
    :class:`CorrectionsError`.

    ``keep_outlines`` (off by default; ``curvefit`` and ``all`` only) leaves the
    traced outlines the correction log lists where the registration drew them
    (:func:`uncorrect_outlines`, :func:`~sbfsem.analysis.correction_checks.scan_outlines`).
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
        if mode == "neighbor":
            correction = fetch_offsets(neuron, client, cset, cache=cache)
            correction.extras["mode"] = mode
        else:
            service = service_stamp(client) if cache is not None else "unknown"
            correction = fetch_structure_offsets(neuron, client, cset, mode, cache=cache,
                                                 service=service)
    finally:
        if own:
            client.close()
    apply_offsets(neuron, correction)
    if keep_outlines and mode in ("curvefit", "all"):
        from ..analysis.correction_checks import scan_neuron

        listed = scan_neuron(neuron).outline_moves
        uncorrect_outlines(neuron, correction, [m.location_id for m in listed])
    return correction


class CorrectionsProvider:
    """What a long-running server needs around the client.

    The published sets of each volume are asked for at most every
    ``sets_ttl_s`` and remembered on disk; after the service fails, it is not
    asked again for ``retry_after_s``, and meanwhile a cell whose offsets are
    on disk is still corrected (its status says so).  When the service answers
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
        # (monotonic time asked, service_stamp); asked as often as the sets
        self._service: tuple[float, str] | None = None
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
        (UTC); empty when it may be asked.  A held volume holds every structure."""
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

    def _service_key(self) -> str:
        return f"corrections:{self.client.url}/service-stamp"

    def service_stamp(self) -> str:
        """:func:`service_stamp`, asked at most every ``sets_ttl_s`` and
        remembered on disk, so offsets cached under it are found while the
        service is down; ``"unknown"`` with nothing to go on."""
        now = time.monotonic()
        with self._lock:
            held = self._service
        if held is not None and now - held[0] < self.sets_ttl_s:
            return held[1]
        stamp = ""
        if not self.paused:
            try:
                stamp = service_stamp(self.client)
            except CorrectionsUnavailable as exc:
                self._failed(exc)
        if stamp:
            with self._lock:
                self._service = (now, stamp)
            if self.cache is not None:
                self.cache.set(self._service_key(), stamp)
            return stamp
        remembered = self.cache.get(self._service_key()) if self.cache is not None else None
        return remembered or (held[1] if held is not None else "unknown")

    def correct(self, neuron, mode: str = "neighbor") -> CellCorrection | None:
        """Apply Viking's correction to ``neuron`` in ``mode`` (see :func:`correct_neuron`).

        ``None`` when its volume has no published set.  While the service is
        paused, or the volume or this structure is held after a failure in the
        service, only offsets on disk are used.
        """
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
            if mode == "neighbor":
                correction = fetch_offsets(neuron, self.client, cset, cache=self.cache,
                                           allow_fetch=fresh and not held)
                correction.extras["mode"] = mode
            else:
                correction = fetch_structure_offsets(
                    neuron, self.client, cset, mode, cache=self.cache,
                    allow_fetch=fresh and not held, timeout=self.structures_timeout,
                    service=self.service_stamp())
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
