"""A JSON response that never emits ``NaN`` or ``Infinity``.

``NaN`` and ``Infinity`` are not valid JSON. Python's ``json`` module emits them
by default and ``JSON.parse`` rejects them, so a single non-finite float in an
analysis result breaks the whole response in the browser.

Most DTOs declare their float fields as ``float | None`` with a validator that
maps non-finite values to ``None``, which is the right thing at the type level.
This class is the backstop for everything else: raw dicts, lists of floats, and
any field added later without a validator.

A route with a ``response_model`` is written by pydantic's own encoder instead
(:class:`SafeJSONRouter`), which sends a non-finite float as ``null`` too.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.datastructures import Default, DefaultPlaceholder
from fastapi.responses import JSONResponse

__all__ = ["EncodedJSON", "SafeJSONResponse", "SafeJSONRouter", "sanitize"]


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


class SafeJSONRouter(APIRouter):
    """An ``APIRouter`` whose routes answer in :class:`SafeJSONResponse` by default,
    while a route with a ``response_model`` keeps FastAPI's Rust encoder.

    FastAPI writes a model's reply in one pass with pydantic's ``dump_json`` only
    while the route's response class is still a default (a ``DefaultPlaceholder``).
    A class named anywhere, on the route, its router or the app (2.17.7 and before:
    the app's ``default_response_class``), turns every such reply into
    ``dump_python`` and then this module's ``json.dumps``, in the one server
    process: 0.17 s of 0.63 s for c1178's 11.7 MB closed-curve mesh, 0.05 s of
    0.11 s for its annotation wireframe, where ``dump_json`` takes 0.04 s and
    0.008 s (2026-10-20, Mac).  So the default here stays a placeholder, of
    :class:`SafeJSONResponse`: a model's reply is ``dump_json``'s, which writes a
    non-finite float as ``null`` as the backstop does, and every other reply (a
    dict, a list) still goes through the backstop.  The bytes are the same, but
    for a number under 1e-4 with a one-digit exponent, which pydantic spells
    ``0.00001`` or ``1e-6`` where Python writes ``1e-05`` or ``1e-06``: the same
    value.  The app itself names no default class.
    """

    def add_api_route(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        response_class = kwargs.get("response_class", Default(JSONResponse))
        if isinstance(response_class, DefaultPlaceholder) and response_class.value is JSONResponse:
            kwargs["response_class"] = Default(SafeJSONResponse)
        super().add_api_route(path, endpoint, **kwargs)


def _dumps(content: Any) -> bytes:
    return json.dumps(
        content,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
