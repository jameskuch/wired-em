"""Who annotated a cell: counts, what counts as a revision, and bulk edits.

The numbers in the docstrings of :mod:`sbfsem.analysis.annotators` come from
Inferior Monkey c1178, measured live on 2026-09-21; these are the synthetic
cases that pin the rules those numbers justified.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sbfsem.analysis.annotators import DEFAULT_TOLERANCE_S, _bulk_clusters, annotator_report

T0 = datetime(2019, 4, 1, 12, 0, tzinfo=UTC)


def row(rid, user, created, modified=None, parent=1):
    return {
        "ID": rid,
        "ParentID": parent,
        "Username": f"{user}          ",          # Viking's column is padded (F030)
        "Created": created.isoformat(),
        "LastModified": (modified or created).isoformat(),
    }


def at(minutes=0, seconds=0, days=0):
    return T0 + timedelta(days=days, minutes=minutes, seconds=seconds)


def test_shares_are_of_the_annotations_viking_attributes_now():
    rows = [row(i, "sara", at(i)) for i in range(3)] + [row(9, "jk", at(9))]
    report = annotator_report(rows, structure_id=1, source="NeitzInferiorMonkey")
    assert report.annotations == 4
    assert [(a.username, a.annotations) for a in report.annotators] == [("sara", 3), ("jk", 1)]
    assert report.annotators[0].fraction == 0.75
    # Every annotation is somebody's: the shares add up to one.
    assert sum(a.fraction for a in report.annotators) == 1.0


def test_a_padded_username_is_trimmed_and_an_empty_one_survives():
    rows = [row(1, "sara", at()), {"ID": 2, "ParentID": 1, "Username": "   "}]
    report = annotator_report(rows, structure_id=1, source="RC1")
    assert {a.username for a in report.annotators} == {"sara", ""}


def test_an_annotation_never_written_again_is_certainly_its_annotators():
    rows = [row(1, "sara", at()), row(2, "sara", at(1), at(1, 30))]
    report = annotator_report(rows, structure_id=1, source="RC1")
    (sara,) = report.annotators
    # Placed and nudged in the same minute is one act, not a correction.
    assert sara.as_placed == 2 and sara.revisions == 0 and report.revisions == 0


def test_a_write_long_after_creation_is_a_revision_and_names_only_the_reviser():
    """A mentor who fixes a student's annotation takes it over entirely: the
    record keeps one username, so the student's 30 simply vanish."""
    student = [row(i, "student", at(i)) for i in range(20)]
    mentor = [row(100 + i, "mentor", at(i), at(i, days=3)) for i in range(30)]
    report = annotator_report(student + mentor, structure_id=1, source="RC1")
    by = {a.username: a for a in report.annotators}
    assert by["student"].annotations == 20 and by["student"].revisions == 0
    assert by["mentor"].annotations == 30 and by["mentor"].revisions == 30
    assert by["mentor"].as_placed == 0        # the mentor placed none of them
    assert report.revisions == 30
    # What the record has lost: the student placed all 50 and holds 40 % of them.
    assert by["student"].fraction == 0.4


def test_the_tolerance_is_a_knob():
    rows = [row(1, "sara", at(), at(0, 90))]         # rewritten 90 seconds later
    assert annotator_report(rows, structure_id=1, source="RC1").annotators[0].as_placed == 1
    tight = annotator_report(rows, structure_id=1, source="RC1", tolerance_s=60)
    assert tight.annotators[0].revisions == 1 and tight.tolerance_s == 60.0


def test_a_bulk_edit_is_not_anybodys_correction():
    """Tens of writes a minute under two usernames at once is machinery: the
    2023-05-31 event on c1178 re-stamped 780 of its 2434 annotations and would
    otherwise have read as "57 % of this cell was corrected"."""
    rows = [
        row(i, "sara" if i % 2 else "abordt", at(days=-900), at(0, i % 60))
        for i in range(100)
    ]
    report = annotator_report(rows, structure_id=1, source="RC1")
    assert report.bulk_edits == 1
    assert report.restamped == 100 and report.revisions == 0
    assert sum(a.restamped for a in report.annotators) == 100


def test_one_person_working_fast_is_not_a_bulk_edit():
    """The two-username test is what a person cannot pass; a rate alone would
    also catch somebody dragging a long stretch of annotations straight."""
    rows = [row(i, "sara", at(days=-900), at(0, i % 60)) for i in range(100)]
    report = annotator_report(rows, structure_id=1, source="RC1")
    assert report.bulk_edits == 0 and report.restamped == 0
    assert report.revisions == 100


def test_a_bursts_quiet_head_and_tail_belong_to_it():
    """Averaging a rate over a burst with a slow tail dilutes it below any
    threshold, which is how c1178's 07:11 event (three usernames, 69 writes in
    one minute) first went unnoticed."""
    stamps = (
        [(at(0, s), "a" if s % 2 else "b") for s in range(30)]     # the seed minute
        + [(at(1, s), "a") for s in range(8)]                      # its tail
        + [(at(30), "a")]                                          # a real later edit
    )
    clusters = _bulk_clusters(stamps)
    assert len(clusters) == 1
    start, end = clusters[0]
    assert start == at().replace(second=0, microsecond=0)
    assert end == at(1).replace(second=0, microsecond=0)


def test_child_structures_are_part_of_the_cells_work():
    rows = [row(1, "sara", at()), row(2, "jk", at(1), parent=77)]
    children = [{"ID": 77, "TypeID": 35, "Username": "jk", "Label": ""},
                {"ID": 78, "TypeID": 35, "Username": "mentor", "Label": ""}]
    report = annotator_report(
        rows, structure_id=1, source="RC1", children=children, owner="sara"
    )
    assert report.on_cell == 1 and report.on_children == 1 and report.n_children == 2
    by = {a.username: a for a in report.annotators}
    assert by["jk"].on_children == 1 and by["jk"].structures == 1
    # Somebody who marked a synapse but annotated nothing still appears.
    assert by["mentor"].annotations == 0 and by["mentor"].structures == 1
    assert report.owner == "sara"


def test_rows_without_timestamps_lose_the_dates_not_the_counts():
    rows = [{"ID": 1, "ParentID": 1, "Username": "sara"}] * 3
    report = annotator_report(rows, structure_id=1, source="RC1")
    (sara,) = report.annotators
    assert sara.annotations == 3 and sara.first is None and sara.last is None
    assert sara.as_placed == 0 and sara.revisions == 0


def test_dates_are_the_earliest_creation_and_the_latest_write():
    rows = [row(1, "sara", at(days=-10)), row(2, "sara", at(), at(days=5))]
    (sara,) = annotator_report(rows, structure_id=1, source="RC1").annotators
    assert sara.first.startswith("2019-03-22") and sara.last.startswith("2019-04-06")


def test_an_empty_cell_reports_nothing_rather_than_dividing_by_zero():
    report = annotator_report([], structure_id=1, source="RC1")
    assert report.annotations == 0 and report.annotators == [] and report.bulk_edits == 0
    assert report.tolerance_s == DEFAULT_TOLERANCE_S


def sweeping(count):
    """A volume that answers every window with ``count(start, end, before)``,
    and keeps the windows it was asked about."""
    asked = []

    def volume_rewrites(windows):
        asked.extend(windows)
        return [count(*window) for window in windows]

    return volume_rewrites, asked


def swept_rows(n=30, user="arieke"):
    """Old annotations of one person, rewritten three a minute: how the Viking
    client's Update Volume Positions reaches one cell as it walks the volume."""
    return [row(i, user, at(days=-900), at(i // 3, (i % 3) * 20)) for i in range(n)]


def test_a_sweep_through_the_volume_is_not_anybodys_correction():
    """The cell alone cannot see it: three writes a minute under one name reads
    as that person revising all 30 (c62676's 175 read as 179 revisions).  The
    rest of the volume, rewritten by the thousand in the same minutes, can."""
    rows = swept_rows()
    alone = annotator_report(rows, structure_id=1, source="NeitzInferiorMonkey")
    assert alone.revisions == 30 and alone.restamped == 0
    volume, asked = sweeping(lambda start, end, before: 1200)
    report = annotator_report(rows, structure_id=1, source="NeitzInferiorMonkey",
                              volume_rewrites=volume)
    assert report.restamped == 30 and report.revisions == 0 and report.bulk_edits == 1
    assert len(asked) == 1
    assert report.annotators[0].restamped == 30


def test_people_editing_while_the_volume_is_quiet_are_still_revising():
    """40 other annotations in the same 11 minutes is under 4 a minute: people."""
    volume, _ = sweeping(lambda start, end, before: 30 + 40)     # its own 30, and 40 more
    report = annotator_report(swept_rows(), structure_id=1, source="RC1", volume_rewrites=volume)
    assert report.revisions == 30 and report.restamped == 0 and report.bulk_edits == 0


def test_a_cell_cannot_vouch_for_itself():
    """Its own rows and its children's come out of the volume's count, so one
    person revising 300 of a cell's annotations in five minutes stays one person."""
    rows = [row(i, "sara", at(days=-900), at(i // 60, i % 60), parent=1 if i < 200 else 77)
            for i in range(300)]
    volume, _ = sweeping(lambda start, end, before: 300)
    report = annotator_report(rows, structure_id=1, source="RC1", volume_rewrites=volume)
    assert report.revisions == 300 and report.restamped == 0


def test_the_windows_the_volume_is_asked_about():
    """A session is the cell's later writes no more than five minutes apart,
    padded 30 s each side, in whole seconds; only annotations more than a day
    older than the session count."""
    rows = [
        row(1, "sara", at(days=-900), at(0, 10)),
        row(2, "sara", at(days=-900), at(4, 10)),       # four minutes on: the same session
        row(3, "sara", at(days=-900), at(10, 10)),      # six more: a new one
        row(4, "sara", at(0, 5)),                       # placed then, never rewritten
    ]
    volume, asked = sweeping(lambda *window: 0)
    annotator_report(rows, structure_id=1, source="RC1", volume_rewrites=volume)
    assert asked == [
        (at(0, -20), at(4, 41), at(0, 10, days=-1)),
        (at(9, 40), at(10, 41), at(10, 10, days=-1)),
    ]


def test_a_window_the_volume_cannot_answer_keeps_the_cells_own_verdict():
    volume, _ = sweeping(lambda *window: None)
    report = annotator_report(swept_rows(), structure_id=1, source="RC1", volume_rewrites=volume)
    assert report.revisions == 30 and report.restamped == 0


def test_the_cells_own_bursts_are_not_asked_about_again():
    burst = [row(i, "sara" if i % 2 else "abordt", at(days=-900), at(0, i % 60))
             for i in range(100)]
    volume, asked = sweeping(lambda *window: 0)
    report = annotator_report(burst + [row(200, "sara", at(days=-900), at(30))],
                              structure_id=1, source="RC1", volume_rewrites=volume)
    assert report.restamped == 100 and report.revisions == 1 and report.bulk_edits == 1
    assert [window[0] for window in asked] == [at(29, 30)]


def test_one_sweep_reaching_a_cell_twice_is_one_bulk_edit():
    """A sweep reaches a cell's sections at several times; sessions less than
    half an hour apart are one operation, and one two hours later is another."""
    rows = [row(10 * k + i, "arieke", at(days=-900), at(start + i))
            for k, start in enumerate((0, 20, 120)) for i in range(3)]
    volume, asked = sweeping(lambda *window: 5000)
    report = annotator_report(rows, structure_id=1, source="RC1", volume_rewrites=volume)
    assert len(asked) == 3 and report.restamped == 9 and report.bulk_edits == 2
