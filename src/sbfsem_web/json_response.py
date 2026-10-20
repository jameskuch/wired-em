"""A JSON response that never emits ``NaN`` or ``Infinity``.

``NaN`` and ``Infinity`` are not valid JSON. Python's ``json`` module emits them
by default and ``JSON.parse`` rejects them, so a single non-finite float in an
analysis result breaks the whole response in the browser.

Most DTOs declare their float fields as ``float | None`` with a validator that
maps non-finite values to ``None``, which is the right thing at the type level.
This class is the backstop for everything else: raw dicts, lists of floats, and
any field added later without a validator.
"""

from __future__ import annotations

import json
import math
from typing import Any

from fastapi.responses import JSONResponse

__all__ = ["EncodedJSON", "SafeJSONResponse", "sanitize"]


class EncodedJSON(bytes):
    """JSON text that is already made, to be sent as it is.

    A finished mesh job holds its result in this form: the same text a poll
    would have made from the model, at a sixth of the memory (a 400,000-vertex
    mesh is 41 MB of text against 228 MB of Python lists) and with nothing
    left to do for the poll but send it.
    """


def sanitize(value: Any) -> Any:
    """Recursively replace non-finite floats with ``None``."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    return value


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        # The JSON encoder refuses a non-finite number (allow_nan=False), so
        # the walk that replaces them is needed only when one is there: it was
        # half the cost of every response (0.43 s of 1.06 s for a 14 MB mesh)
        # and nearly always found nothing.
        try:
            return _dumps(content)
        except ValueError:
            return _dumps(sanitize(content))


def _dumps(content: Any) -> bytes:
    return json.dumps(
        content,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
