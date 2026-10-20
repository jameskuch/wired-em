"""Viking annotation tag parsing.

Ports ``SynapseOData.parseTags`` and its near-duplicate ``util/import/parseTags.m``.
Viking stores tags as an HTML-ish blob; the payload is whatever sits inside the
double quotes, joined with semicolons.

Deviations:

* An odd number of quote characters made the MATLAB ``reshape`` throw (F034).
* The ``util/import`` copy dropped a row entirely for any tag with no quotes
  (F061); this always returns one string per input.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = ["parse_tag", "parse_tags"]

_QUOTED = re.compile(r'"([^"]*)"')


def parse_tag(value) -> str:
    """Extract the semicolon-joined tag names from one Viking tag blob."""
    if value is None:
        return ""
    text = str(value)
    if not text or text == "-":
        return ""
    found = _QUOTED.findall(text)
    if found:
        return ";".join(found)
    # No quotes at all: Viking sometimes stores a bare name.
    stripped = re.sub(r"<[^>]*>", "", text).strip()
    return stripped.strip('"').strip()


def parse_tags(values: Iterable) -> list[str]:
    """Vectorised :func:`parse_tag`; always returns one entry per input."""
    return [parse_tag(v) for v in values]
