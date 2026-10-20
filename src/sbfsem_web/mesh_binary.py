"""A finished render as the page collects it: binary, the same numbers as the JSON.

The JSON of a 400,000-face surface is 12.7 MB of float text, and production's nginx
spent about 0.6 s compressing it at gzip level 5 on every finished poll, longer than
the link took to carry it (measured 2026-10-20, c1178).  The page asks for this form
instead (``Accept: application/x-sbfsem-mesh``); every other client gets the JSON.

Layout, all little-endian::

    uint32   header length H, in bytes
    H bytes  header: UTF-8 JSON, padded with spaces so the data starts on 8 bytes
    data     vertices as int32, faces as uint32, vertex_data as float64

The header is the job's status with ``mesh`` holding the mesh's other fields and
``null`` where each array goes, plus ``arrays``: ``{name: [offset, count]}``, the
offset in bytes from the start of the data, or ``null`` for an absent
``vertex_data``.  A vertex coordinate ``v`` (µm) travels as ``rint(v * 1e4)``, the
JSON's own fourth decimal (``SbfsemService.COORDINATE_DECIMALS``), and is read back
as ``k / 1e4`` in float64: exactly the number the JSON text parses to.  ``-0.0``
travels as ``-2**31`` (``NEGATIVE_ZERO``), a value no coordinate takes.

A mesh this cannot carry exactly (a coordinate past ±214,748 µm, one not on the
1e-4 step, a non-finite number, which the JSON sends as ``null``) is not encoded:
:func:`encode` answers ``None`` and the job keeps its JSON text.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

__all__ = ["MEDIA_TYPE", "NEGATIVE_ZERO", "EncodedMesh", "accepts", "encode", "encode_arrays"]

MEDIA_TYPE = "application/x-sbfsem-mesh"

#: The JSON's coordinate step is 1e-4 µm: a vertex is sent as this many steps.
STEPS_PER_UM = 10_000.0

#: int32's lowest value, which no coordinate in range reaches: it carries -0.0.
NEGATIVE_ZERO = -(2**31)

#: The arrays of a mesh, in the order they are laid out, with their wire types.
_ARRAYS = (("vertices", "<i4"), ("faces", "<u4"), ("vertex_data", "<f8"))


def accepts(header: str | None) -> bool:
    """Whether an ``Accept`` header names the binary mesh (parameters ignored)."""
    if not header:
        return False
    return any(part.split(";", 1)[0].strip().lower() == MEDIA_TYPE for part in header.split(","))


class EncodedMesh:
    """A finished render held as its binary arrays and its other fields.

    Held in place of the JSON text for the ten minutes a finished job lingers,
    at a little over half its memory (7.2 MB against 12.7 MB for c1178's
    400,000 faces).  :meth:`plain` gives back the JSON's values exactly, so a
    client that asks for JSON gets today's bytes.
    """

    __slots__ = ("fields", "arrays", "data")

    def __init__(self, fields: dict, arrays: dict, data: bytes | memoryview):
        #: The mesh's model dump, in field order, with ``None`` for each array.
        self.fields = fields
        #: ``{name: [offset, count]}`` into ``data``, or ``None`` for an absent array.
        self.arrays = arrays
        #: The arrays as they go out, read-only.
        self.data = data

    @property
    def nbytes(self) -> int:
        return len(self.data)

    def _array(self, name: str) -> np.ndarray | None:
        where = self.arrays[name]
        if where is None:
            return None
        dtype = dict(_ARRAYS)[name]
        return np.frombuffer(self.data, dtype=dtype, count=where[1], offset=where[0])

    def vertices_um(self) -> np.ndarray:
        """The vertices as the JSON's float64 numbers (``-0.0`` included)."""
        steps = self._array("vertices")
        assert steps is not None
        return _decode_vertices(steps)

    def plain(self) -> dict:
        """The mesh as the plain JSON values its model dump had, in the same order."""
        faces = self._array("faces")
        vertex_data = self._array("vertex_data")
        assert faces is not None
        out = dict(self.fields)
        out["vertices"] = self.vertices_um().tolist()
        out["faces"] = faces.tolist()
        out["vertex_data"] = vertex_data.tolist() if vertex_data is not None else None
        return out

    def reply(self, status: dict) -> bytes:
        """The whole binary reply: ``status`` is the job's status without its mesh."""
        head = dict(status)
        head["mesh"] = self.fields
        head["arrays"] = self.arrays
        text = json.dumps(head, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        text += b" " * (-(4 + len(text)) % 8)
        return len(text).to_bytes(4, "little") + text + self.data


def _decode_vertices(steps: np.ndarray) -> np.ndarray:
    # A division, as the page does it: k * 1e-4 is not always the number k / 1e4 is.
    vertices = steps.astype(np.float64)
    vertices /= STEPS_PER_UM
    vertices[steps == NEGATIVE_ZERO] = -0.0
    return vertices


def _same_bits(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and bool(np.array_equal(a.view(np.uint64), b.view(np.uint64)))


def encode(fields: dict[str, Any]) -> EncodedMesh | None:
    """The binary form of a mesh's model dump, or ``None`` when it cannot carry
    the JSON's numbers exactly (see the module's notes)."""
    vertex_data = fields.get("vertex_data")
    return encode_arrays(
        fields,
        np.asarray(fields.get("vertices") or [], dtype=np.float64),
        np.asarray(fields.get("faces") or [], dtype=np.int64),
        None if vertex_data is None else np.asarray(vertex_data, dtype=np.float64),
    )


def encode_arrays(fields: dict[str, Any], vertices: np.ndarray, faces: np.ndarray,
                  vertex_data: np.ndarray | None) -> EncodedMesh | None:
    """:func:`encode` of a mesh whose three buffers are arrays: ``fields`` is the
    model dump of the rest (whatever it holds for the buffers is not read), and
    the arrays hold the numbers the dump's lists would, flat
    (``SbfsemService.mesh_buffers``).  No Python number per coordinate is made."""
    vertices = np.asarray(vertices, dtype=np.float64).ravel()
    if not np.isfinite(vertices).all():
        return None
    scaled = vertices * STEPS_PER_UM
    np.rint(scaled, out=scaled)
    if scaled.size and max(-scaled.min(), scaled.max()) > 2**31 - 1:
        return None
    steps = scaled.astype(np.int32)
    del scaled
    steps[(vertices == 0) & np.signbit(vertices)] = NEGATIVE_ZERO
    if not _same_bits(_decode_vertices(steps), vertices):
        return None  # a coordinate off the 1e-4 step: the JSON carries more digits
    faces = np.asarray(faces).ravel()
    if faces.size and (faces.min() < 0 or faces.max() > 2**32 - 1):
        return None
    extra = None
    if vertex_data is not None:
        extra = np.asarray(vertex_data, dtype=np.float64).ravel()
        if not np.isfinite(extra).all():
            return None  # the JSON sends null there, and a float64 cannot say null

    values = {"vertices": steps, "faces": faces, "vertex_data": extra}
    arrays: dict[str, list[int] | None] = {}
    layout: list[tuple[np.ndarray, str, int]] = []
    offset = 0
    for name, dtype in _ARRAYS:
        array = values[name]
        if array is None:
            arrays[name] = None
            continue
        offset += -offset % np.dtype(dtype).itemsize  # each array on its own type's size
        arrays[name] = [offset, int(array.size)]
        layout.append((array, dtype, offset))
        offset += array.size * np.dtype(dtype).itemsize
    # Each array is written once, straight into the data held, and the data is not
    # copied again into bytes (127 MB for a 7.07 M-triangle surface).
    data = bytearray(offset)
    for array, dtype, start in layout:
        np.frombuffer(data, dtype=dtype, count=array.size, offset=start)[:] = array
    held = {key: (None if key in arrays else value) for key, value in fields.items()}
    return EncodedMesh(held, arrays, memoryview(data).toreadonly())
