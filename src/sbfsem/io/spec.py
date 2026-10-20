"""Parsing structure specifications: IDs, ranges, and label terms.

This is a faithful port of the input grammar of the Viking Data Export portal
(``Servers/ExportPortal/js/portal.js`` on the ``Jotunn`` branch of
jamesra/Viking), so a file or a typed string that works there works here:

* Entries are separated by newline, comma, or semicolon — never by whitespace
  alone, because labels contain spaces ("GC ON", "yAC ON+OFF").
* An entry containing a letter is a **label term**, matched later as a
  case-insensitive substring against parent-structure labels, and is never
  split further.
* An entry without letters is whitespace-split into tokens, each a plain ID
  or an inclusive range (``180-476``; spaces around the dash are tolerated,
  and so are the en dashes word processors substitute — a superset of the
  portal, which knows only the hyphen).
* Unreadable tokens are **reported, not dropped**: to the export service an
  empty ID set means "the whole volume", so silently discarding input is the
  one failure this grammar must never have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["InvalidEntry", "ParsedSpec", "parse_structure_spec", "MAX_RANGE_SPAN"]

#: A range wider than this is refused rather than expanded, exactly as in the
#: portal: far above any plausible real request, and it stops a slip such as
#: ``1-99999999`` from building a multi-megabyte list.
MAX_RANGE_SPAN = 10_000

#: IDs above this would lose precision in the portal's JavaScript
#: (``Number.isSafeInteger``); refusing them keeps the two parsers identical.
_MAX_SAFE_INTEGER = 2**53 - 1

_ENTRY_SEPARATORS = re.compile(r"[\n\r,;]+")
#: ASCII only, as the portal's ``/[a-z]/i`` and ``\d`` are: Python's ``\d`` and
#: ignore-case ``[a-z]`` also take Arabic-Indic and full-width digits, the
#: long s and the Kelvin sign, which ``ids.js`` (the box's own parser) refuses.
_HAS_LETTER = re.compile(r"[A-Za-z]")
#: Dash variants: hyphen, plus the en/em dashes a word processor produces.
_DASH_RUN = re.compile(r"\s*[-‐‑‒–—―]\s*")
_RANGE = re.compile(r"^([0-9]+)-([0-9]+)$")
_NUMBER = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class InvalidEntry:
    """One token that could not be read, with the reason it was refused."""

    text: str
    reason: str


@dataclass(frozen=True)
class ParsedSpec:
    """The three classes of input: numeric IDs, label terms, and refusals."""

    ids: tuple[int, ...]  # sorted and de-duplicated
    labels: tuple[str, ...]  # in order of first appearance
    invalid: tuple[InvalidEntry, ...]


def parse_structure_spec(text: str | None) -> ParsedSpec:
    """Split raw text into entries and classify each one.

    Mirrors the portal's ``parseEntries`` so both accept the same strings and
    the same files.
    """
    ids: set[int] = set()
    labels: list[str] = []
    invalid: list[InvalidEntry] = []

    for raw_entry in _ENTRY_SEPARATORS.split(text or ""):
        entry = raw_entry.strip()
        if not entry:
            continue

        # Anything with a letter is a label, and is never split further.
        if _HAS_LETTER.search(entry):
            if entry not in labels:
                labels.append(entry)
            continue

        # Tighten "180 - 476" to "180-476" first, so the whitespace split
        # below cannot break a spaced range into three meaningless tokens.
        for token in _DASH_RUN.sub("-", entry).split():
            if _NUMBER.match(token):
                n = int(token)
                if n > _MAX_SAFE_INTEGER:
                    invalid.append(InvalidEntry(token, "number is too large"))
                elif n < 1:
                    # Viking IDs start at 1; the portal would pass a zero
                    # through to a service that resolves it to nothing.
                    invalid.append(InvalidEntry(token, "ids start at 1"))
                else:
                    ids.add(n)
                continue

            match = _RANGE.match(token)
            if match:
                lo, hi = int(match.group(1)), int(match.group(2))
                if lo > _MAX_SAFE_INTEGER or hi > _MAX_SAFE_INTEGER:
                    invalid.append(InvalidEntry(token, "number is too large"))
                    continue
                if lo > hi:
                    lo, hi = hi, lo
                span = hi - lo + 1
                if span > MAX_RANGE_SPAN:
                    invalid.append(InvalidEntry(
                        token,
                        f"range covers {span:,} IDs, over the {MAX_RANGE_SPAN:,} limit",
                    ))
                    continue
                if lo < 1:
                    invalid.append(InvalidEntry(token, "ids start at 1"))
                    continue
                ids.update(range(lo, hi + 1))
                continue

            invalid.append(InvalidEntry(token, "not an ID or a range"))

    return ParsedSpec(
        ids=tuple(sorted(ids)),
        labels=tuple(labels),
        invalid=tuple(invalid),
    )
