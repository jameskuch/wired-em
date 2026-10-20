"""The structure-specification grammar, mirrored from the Viking ExportPortal.

Every case here matches the behavior of ``parseEntries`` in
``Servers/ExportPortal/js/portal.js`` (jamesra/Viking, ``Jotunn`` branch), so
files and strings that work on that site work here, and vice versa.  The
JavaScript twin lives in ``src/sbfsem_web/static/js/ids.js``.
"""

from __future__ import annotations

from sbfsem.io.spec import MAX_RANGE_SPAN, parse_structure_spec


def test_single_id():
    parsed = parse_structure_spec("1178")
    assert parsed.ids == (1178,)
    assert parsed.labels == ()
    assert parsed.invalid == ()


def test_comma_list_and_ranges():
    parsed = parse_structure_spec("5-10, 12, 1178")
    assert parsed.ids == (5, 6, 7, 8, 9, 10, 12, 1178)


def test_semicolons_and_newlines_separate_entries():
    parsed = parse_structure_spec("180;476\n514")
    assert parsed.ids == (180, 476, 514)


def test_whitespace_separates_within_a_numeric_entry():
    # The portal's own example: a pasted "180 476 514" keeps working.
    parsed = parse_structure_spec("180 476 514")
    assert parsed.ids == (180, 476, 514)


def test_spaced_range_is_tightened():
    # "180 - 476" must not tear into three meaningless tokens.
    parsed = parse_structure_spec("180 - 220")
    assert parsed.ids == tuple(range(180, 221))
    assert parsed.invalid == ()


def test_en_dash_range():
    # A word processor turns the hyphen into an en dash; superset of the portal.
    parsed = parse_structure_spec("5–8")
    assert parsed.ids == (5, 6, 7, 8)


def test_reversed_range_is_swapped():
    assert parse_structure_spec("10-5").ids == (5, 6, 7, 8, 9, 10)


def test_duplicates_collapse_and_sort():
    assert parse_structure_spec("12, 5-7, 6, 12").ids == (5, 6, 7, 12)


def test_labels_are_whole_entries_never_split():
    # Labels contain spaces: splitting on whitespace would tear them apart.
    parsed = parse_structure_spec("GC ON, yAC ON+OFF; 1178")
    assert parsed.labels == ("GC ON", "yAC ON+OFF")
    assert parsed.ids == (1178,)


def test_label_terms_deduplicate_in_order():
    parsed = parse_structure_spec("CBb4w\nCBa\nCBb4w")
    assert parsed.labels == ("CBb4w", "CBa")


def test_invalid_tokens_are_reported_not_dropped():
    parsed = parse_structure_spec("12, 13a4 5, 7-")
    # "13a4 5" contains a letter, so the whole entry is a label term.
    assert parsed.labels == ("13a4 5",)
    assert parsed.ids == (12,)
    assert [e.text for e in parsed.invalid] == ["7-"]
    assert parsed.invalid[0].reason == "not an ID or a range"


def test_oversized_range_is_refused_with_the_span_named():
    parsed = parse_structure_spec(f"1-{MAX_RANGE_SPAN + 1}")
    assert parsed.ids == ()
    assert "over the" in parsed.invalid[0].reason


def test_range_at_the_span_limit_expands():
    parsed = parse_structure_spec(f"1-{MAX_RANGE_SPAN}")
    assert len(parsed.ids) == MAX_RANGE_SPAN


def test_zero_is_refused():
    parsed = parse_structure_spec("0, 5")
    assert parsed.ids == (5,)
    assert parsed.invalid[0].reason == "ids start at 1"


def test_unsafe_integer_is_refused():
    parsed = parse_structure_spec(str(2**53))
    assert parsed.ids == ()
    assert parsed.invalid[0].reason == "number is too large"


def test_empty_and_none():
    for text in ("", None, " \n ,;\n"):
        parsed = parse_structure_spec(text)
        assert parsed.ids == ()
        assert parsed.labels == ()
        assert parsed.invalid == ()
