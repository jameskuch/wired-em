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

import numpy as np
from fastapi import APIRouter
from fastapi.datastructures import Default, DefaultPlaceholder
from fastapi.responses import JSONResponse

__all__ = ["EncodedJSON", "SafeJSONResponse", "SafeJSONRouter", "json_float_list", "sanitize"]


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


#: Numbers written at a time by :func:`json_float_parts`: its working arrays stay
#: a few MB however long the list is.
FLOAT_LIST_CHUNK = 1 << 18


def json_float_list(values: Any, decimals: int) -> bytes | None:
    """``values`` as the JSON list pydantic writes of them as Python floats, made
    without a Python float per number, or ``None`` where it cannot vouch for every byte.

    Each value must already be rounded to ``decimals`` (1 to 4), as ``np.round``
    and Python's ``round`` leave it: the double nearest some ``k / 10**decimals``.
    Its shortest round-trip text, which pydantic and ``repr`` both write, is then
    that decimal itself, trailing zeros dropped and one fractional digit kept: a
    shorter decimal of 15 digits or fewer is another double.  Both write such a
    number positionally (pydantic from 1e-5, ``repr`` from 1e-4, up to 1e16).

    Checked, not assumed: a value off that step, a non-finite one (pydantic
    writes ``null``) or one of 1e11 or more answers ``None``, and the caller writes
    the list the old way.  A 3.54 M-vertex error map's two lists of vertex
    distances held 277 MB as Python lists in the web process and are 50 MB as
    this text (Inferior c1178, production, 2026-10-20).
    """
    parts = json_float_parts(values, decimals)
    return None if parts is None else b"".join(parts)


def json_float_parts(values: Any, decimals: int) -> list[bytes] | None:
    """:func:`json_float_list`'s text in pieces, brackets included, for a caller
    that joins it into a longer text: one copy of the numbers, not two."""
    if not 1 <= decimals <= 4:
        raise ValueError(f"decimals must be 1 to 4, not {decimals}")
    x = np.asarray(values, dtype=np.float64).ravel()
    parts = [b"["]
    for at in range(0, len(x), FLOAT_LIST_CHUNK):
        piece = _float_text(x[at:at + FLOAT_LIST_CHUNK], decimals)
        if piece is None:
            return None
        if at:
            parts.append(b",")
        parts.append(piece)
    parts.append(b"]")
    return parts


def _word_tables(decimals: int) -> tuple[np.ndarray, ...]:
    """The text of a number's two halves as 8-byte words, each with a mask of the
    bytes it shows: the sign's place and the whole part (under 10,000, right-aligned,
    leading zeros hidden, the units digit shown) for each whole part; the point, the
    fraction (trailing zeros hidden, the first digit shown) and the comma for each
    fraction.  A row of a gather is one word, which NumPy does at memory speed (a
    row of bytes is many times slower)."""
    found = _WORD_TABLES.get(decimals)
    if found is None:
        def digits(count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            n = np.arange(10 ** count)
            place = 10 ** np.arange(count - 1, -1, -1)  # the first column is the highest digit
            return n, place, (n[:, None] // place % 10 + ord("0")).astype(np.uint8)

        n, place, text = digits(4)
        head = np.zeros((len(n), 8), dtype=np.uint8)
        head_shown = np.zeros(head.shape, dtype=np.uint8)
        head[:, 3] = ord("-")  # shown by the number's own sign
        head[:, 4:] = text
        head_shown[:, 4:] = (n[:, None] >= place) | (place == 1)
        n, place, text = digits(decimals)
        tail = np.zeros((len(n), 8), dtype=np.uint8)
        tail_shown = np.zeros(tail.shape, dtype=np.uint8)
        tail[:, 0], tail_shown[:, 0] = ord("."), 1
        tail[:, 1:1 + decimals] = text
        tail_shown[:, 1:1 + decimals] = (n[:, None] % (10 * place) != 0) | (place == place[0])
        tail[:, 1 + decimals], tail_shown[:, 1 + decimals] = ord(","), 1
        found = _WORD_TABLES[decimals] = tuple(
            table.view(np.uint64).ravel() for table in (head, head_shown, tail, tail_shown))
    return found


_WORD_TABLES: dict[int, tuple[np.ndarray, ...]] = {}


def _float_text(x: np.ndarray, decimals: int) -> bytes | None:
    """The numbers of ``x`` as JSON, comma-separated (:func:`json_float_list`).

    Each number is written into a row of the same width, sign, whole part
    right-aligned, point, ``decimals`` digits and a comma, and the characters a
    number does not use are left out as the rows are read in order.  A whole
    part under 10,000 (every distance) comes from :func:`_word_tables`, a larger
    one a digit at a time.
    """
    scale = 10 ** decimals
    with np.errstate(invalid="ignore", over="ignore"):
        k = np.rint(x * scale)
        on_step = np.isfinite(k) & (np.abs(k) < 1e15)
    # The value must be the double nearest k / scale, which the division gives exactly.
    if not on_step.all() or not np.array_equal(k / scale, x):
        return None
    whole, frac = np.divmod(np.abs(k).astype(np.int64), scale)
    negative = np.signbit(x)  # -0.0 is written "-0.0", as pydantic writes it
    top = int(whole.max())
    if top < 10_000:
        head, head_shown, tail, tail_shown = _word_tables(decimals)
        words = np.empty((len(x), 2), dtype=np.uint64)
        shown = np.empty((len(x), 2), dtype=np.uint64)
        words[:, 0], shown[:, 0] = head[whole], head_shown[whole]
        words[:, 1], shown[:, 1] = tail[frac], tail_shown[frac]
        rows, keep = words.view(np.uint8), shown.view(np.uint8).view(bool)
        keep[:, 3] = negative
        keep[-1, 9 + decimals] = False  # no comma after the last
        return rows[keep].tobytes()
    width = len(str(top))  # the widest whole part
    point = 1 + width
    rows = np.empty((len(x), point + decimals + 2), dtype=np.uint8)
    keep = np.ones(rows.shape, dtype=bool)
    rows[:, 0] = ord("-")
    keep[:, 0] = negative
    rest = whole.copy()
    for column in range(point - 1, 0, -1):  # the whole part, its last digit first
        rows[:, column] = rest % 10 + ord("0")
        if column < point - 1:
            keep[:, column] = rest > 0  # no leading zeros, but the units digit stays
        rest //= 10
    rows[:, point] = ord(".")
    rest = frac.copy()
    for column in range(point + decimals, point, -1):  # the fraction, its last digit first
        rows[:, column] = rest % 10 + ord("0")
        rest //= 10
    # The fraction's trailing zeros are dropped, but its first digit always stays.
    for j in range(1, decimals):
        keep[:, point + 1 + j] = frac % 10 ** (decimals - j) != 0
    rows[:, -1] = ord(",")
    keep[-1, -1] = False
    return rows[keep].tobytes()
