"""Who annotated a cell, and what the record can and cannot say about it.

There is no MATLAB counterpart beyond ``util/import/getContributions.m``, which
counted annotations per username across a list of structures.  Added 2026-09-21
for the render window's *Return list of annotators*.

What Viking stores
------------------

A ``Location`` row carries one ``Username``, a ``Created`` and a
``LastModified``.  **The username is the last person who wrote the row, not the
person who placed it**, and no history is kept: an annotation that one
annotator placed and another later moved reads as the second annotator's, and
the first is gone from the record.  Measured on Inferior Monkey c1178
(2026-09-21): among the 763 annotations never edited since they were created,
the usernames fall into 10 runs in ID order -- one run per person per sitting,
exactly what tracing looks like; among the 1671 that *were* edited later, the
same usernames fall into 415 runs, interleaved annotation by annotation, which
no four people could have produced by tracing consecutive IDs seconds apart.

So the counts below are *current attribution*.  ``revisions`` -- an annotation
whose last write came well after it was created -- is as close as the data gets
to "corrected somebody's work", and it names only the corrector.  A mentor who
fixes 30 of a student's 50 annotations leaves the student with 20 and takes 30
of their own, with no trace of whose they were.  The table says so.

Bulk edits
----------

``LastModified`` is also written by work that is nobody's annotating.  On
2023-05-31, in nine minutes (04:47 to 04:56 by OData's timestamps, which read
7 h late), 780 of c1178's 2434 annotations were
re-stamped in bursts of 40 to 130 a minute spread over three usernames -- a
volume-wide operation (a re-registration writes every row) that bumped the
timestamp and left ``Username`` alone.  Counting those as corrections would
have said "57 % of this cell was corrected".  :func:`_bulk_clusters` finds
them by the one signature a person cannot fake: twenty writes in one minute
under more than one username at once.  Those rows are reported separately as
``restamped`` and are not counted as revisions.

That test sees only the cell, and a sweep can pass under it.  The Viking
client's Update Volume Positions (run on Inferior Monkey from 2026-09-23)
walks the volume a section at a time, so it writes a few of one cell's rows a
minute, often all under one name: c62676's 175 read as 179 revisions.
:func:`_volume_bulk` asks the rest of the volume instead.  For each session of
the cell's later writes, it counts the annotations of other structures, more
than a day old, that were rewritten in the same window.  Measured on Inferior
Monkey: 38 to 2,336 a minute in bulk sessions, 0 to 4 while people were
editing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

__all__ = [
    "Annotator",
    "AnnotatorReport",
    "BULK_EDGE_ROWS",
    "BULK_SEED_ROWS",
    "BULK_VOLUME_RATE",
    "DEFAULT_TOLERANCE_S",
    "VolumeRewrites",
    "annotator_report",
]

#: How long after it was created an annotation may still be written without
#: the write counting as a revision.  Placing an annotation and nudging it in
#: the same sitting is one act; the default hour is well past that and well
#: short of a later pass over somebody else's work.
DEFAULT_TOLERANCE_S = 3600.0

#: A minute in which this many of one cell's annotations were written under at
#: least two usernames is machinery, not a person: nobody writes under two
#: names at once, and two people editing the same cell in the same minute do
#: not reach twenty annotations between them.
BULK_SEED_ROWS = 20
BULK_SEED_USERS = 2
#: A burst found that way is taken whole: neighbouring minutes holding at least
#: this many writes belong to the same event.
BULK_EDGE_ROWS = 5
#: Minutes of quiet that end a burst.
BULK_GAP_MIN = 1

#: A session of the cell's later writes is part of a volume-wide operation when
#: the rest of the volume rewrote this many old annotations a minute during it.
#: Measured on Inferior Monkey (2026-09-23): 38 to 2,336 a minute in bulk
#: sessions, 0 to 4 while people were editing.
BULK_VOLUME_RATE = 20.0
#: The cell's later writes further apart than this are separate sessions.
BULK_SESSION_GAP_MIN = 5
#: Padding on each side of a session's window, seconds.
BULK_SESSION_PAD_S = 30
#: An annotation created less than this before a session is new work, not a
#: rewrite.
BULK_OLD_ROW_DAYS = 1
#: Bulk writes of one cell closer together than this count as one bulk edit,
#: however they were found: a sweep reaches a cell's sections at several times.
BULK_EVENT_GAP_MIN = 30

#: For each ``(start, end, created_before)`` window: how many annotations
#: anywhere in the volume were last written in ``[start, end]`` and created
#: before ``created_before``, or ``None`` where the volume could not say.
VolumeRewrites = Callable[
    [Sequence[tuple[datetime, datetime, datetime]]], Sequence[int | None]
]


@dataclass(frozen=True)
class Annotator:
    """One username's share of a cell, as Viking currently attributes it."""

    username: str
    #: Annotations whose last writer is this username.
    annotations: int
    #: ``annotations`` as a fraction of the cell's total, 0 to 1.
    fraction: float
    #: Of those, on the cell itself and on its child structures.
    on_cell: int
    on_children: int
    #: Never written since it was created: certainly this person's own work.
    as_placed: int
    #: Written more than the tolerance after creation, bulk edits excluded:
    #: this person edited an annotation that already existed.
    revisions: int
    #: Rows whose last write falls inside a bulk edit -- no information about
    #: who did what, beyond the username the bulk edit did not change.
    restamped: int
    #: Child structures (synapses and the like) created under this username.
    structures: int
    #: Earliest ``Created`` and latest ``LastModified``, ISO-8601 or ``None``.
    first: str | None
    last: str | None

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "annotations": self.annotations,
            "fraction": self.fraction,
            "on_cell": self.on_cell,
            "on_children": self.on_children,
            "as_placed": self.as_placed,
            "revisions": self.revisions,
            "restamped": self.restamped,
            "structures": self.structures,
            "first": self.first,
            "last": self.last,
        }


@dataclass(frozen=True)
class AnnotatorReport:
    """Every annotator of one cell, with the cell's totals."""

    structure_id: int
    source: str
    annotations: int
    on_cell: int
    on_children: int
    n_children: int
    #: Cell-wide revisions and re-stamps (the sums of the rows').
    revisions: int
    restamped: int
    #: How many bulk edits the re-stamped rows fall into.
    bulk_edits: int
    tolerance_s: float
    #: ``Structure.Username`` of the cell itself -- who last wrote the row that
    #: names the cell, which is usually whoever started it.
    owner: str | None
    annotators: list[Annotator]

    def as_dict(self) -> dict:
        return {
            "structure_id": self.structure_id,
            "source": self.source,
            "annotations": self.annotations,
            "on_cell": self.on_cell,
            "on_children": self.on_children,
            "n_children": self.n_children,
            "revisions": self.revisions,
            "restamped": self.restamped,
            "bulk_edits": self.bulk_edits,
            "tolerance_s": self.tolerance_s,
            "owner": self.owner,
            "annotators": [a.as_dict() for a in self.annotators],
        }


def _name(row: dict) -> str:
    """The username, trimmed.  Viking's column is fixed-width ``char``, so
    every value arrives padded (F030), and an empty one is a real state."""
    return str(row.get("Username") or "").strip()


def _when(value) -> datetime | None:
    """A Viking timestamp as an aware UTC datetime, or ``None``: its digits are UTC."""
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    # Viking writes UTC digits.  Its OData labeled them Pacific (-07:00, -08:00
    # in winter) and now labels them Z; the label is dropped and the digits
    # read as UTC, as sbfsem.io.odata.odata_utc does for the rest of the code
    # (analysis does not import io).  Read with its label, a row cached under
    # the old one came out 7 h later than the same row fetched today.
    return stamp.replace(tzinfo=UTC)


def _bulk_clusters(
    stamps: Sequence[tuple[datetime, str]],
    *,
    seed_rows: int = BULK_SEED_ROWS,
    seed_users: int = BULK_SEED_USERS,
    edge_rows: int = BULK_EDGE_ROWS,
    gap_min: int = BULK_GAP_MIN,
) -> list[tuple[datetime, datetime]]:
    """The ``(first, last)`` minute of every burst of writes no person made.

    ``stamps`` is ``(LastModified, username)`` for the rows written later than
    the tolerance after they were created.  A minute holding ``seed_rows``
    writes under ``seed_users`` usernames seeds a burst -- the test a person
    cannot pass, because one person writes under one name -- and the burst
    then grows through neighbouring minutes of at least ``edge_rows`` writes,
    across gaps of up to ``gap_min`` empty minutes, so its quieter head and
    tail are not left behind to read as somebody's corrections.

    Rates are never averaged over a span: a burst with a slow tail would
    dilute below any threshold, which is how the 2023-05-31 07:11 event on
    c1178 (three usernames, 69 writes in one minute) first went unnoticed.
    """
    if not stamps:
        return []
    minutes: dict[datetime, list[str]] = {}
    for stamp, user in stamps:
        key = stamp.replace(second=0, microsecond=0)
        minutes.setdefault(key, []).append(user)
    keys = sorted(minutes)
    seeds = [
        k for k in keys
        if len(minutes[k]) >= seed_rows and len(set(minutes[k])) >= seed_users
    ]
    if not seeds:
        return []

    step = timedelta(minutes=gap_min + 1)
    taken: set[datetime] = set()
    out: list[tuple[datetime, datetime]] = []
    for seed in seeds:
        if seed in taken:
            continue
        run = {seed}
        for direction in (-1, 1):
            edge = seed
            while True:
                near = [
                    k for k in keys
                    if k not in run
                    and 0 < (k - edge).total_seconds() * direction <= step.total_seconds()
                    and len(minutes[k]) >= edge_rows
                ]
                if not near:
                    break
                edge = max(near) if direction > 0 else min(near)
                run.update(near)
        taken |= run
        out.append((min(run), max(run)))
    out.sort()
    return out


def _minute(stamp: datetime) -> datetime:
    return stamp.replace(second=0, microsecond=0)


def _volume_bulk(
    later: Sequence[datetime],
    written: Sequence[tuple[datetime, datetime]],
    covered: Sequence[tuple[datetime, datetime]],
    volume_rewrites: VolumeRewrites,
    *,
    rate: float = BULK_VOLUME_RATE,
    gap_min: float = BULK_SESSION_GAP_MIN,
    pad_s: float = BULK_SESSION_PAD_S,
    old_days: float = BULK_OLD_ROW_DAYS,
) -> list[tuple[datetime, datetime]]:
    """The ``(first, last)`` minute of every session of the cell's later writes
    that fell inside a volume-wide operation.

    ``later`` is when each row written after the tolerance was last written;
    those inside ``covered`` (the cell's own bursts) are already decided.  A
    session is a run of the rest no more than ``gap_min`` apart.  Its window is
    padded by ``pad_s`` on each side, and the volume is asked how many
    annotations created more than ``old_days`` before the session were
    rewritten in it.  ``written`` is ``(LastModified, Created)`` of every row of
    the report: those are taken out of the count, so a cell cannot vouch for
    itself, and a cell's child structures count as the cell.
    """
    stamps = sorted(t for t in later if not any(a <= _minute(t) <= b for a, b in covered))
    if not stamps:
        return []
    sessions: list[list[datetime]] = [[stamps[0]]]
    for stamp in stamps[1:]:
        if (stamp - sessions[-1][-1]).total_seconds() > gap_min * 60:
            sessions.append([stamp])
        else:
            sessions[-1].append(stamp)
    pad = timedelta(seconds=pad_s)
    # Whole seconds, so the bounds the volume is asked with are the bounds the
    # cell's own rows are taken out with.
    windows = [
        (
            (s[0] - pad).replace(microsecond=0),
            (s[-1] + pad).replace(microsecond=0) + timedelta(seconds=1),
            (s[0] - timedelta(days=old_days)).replace(microsecond=0),
        )
        for s in sessions
    ]
    counts = volume_rewrites(windows)
    out: list[tuple[datetime, datetime]] = []
    for session, (start, end, before), count in zip(sessions, windows, counts, strict=True):
        if count is None:
            continue
        own = sum(1 for modified, created in written
                  if start <= modified <= end and created < before)
        minutes = (end - start).total_seconds() / 60.0
        if (count - own) / minutes >= rate:
            out.append((_minute(session[0]), _minute(session[-1])))
    return out


def _events(intervals: Sequence[tuple[datetime, datetime]],
            gap_min: float = BULK_EVENT_GAP_MIN) -> int:
    """How many operations ``intervals`` are, joining those less than
    ``gap_min`` apart."""
    events = 0
    reach: datetime | None = None
    for start, end in sorted(intervals):
        if reach is None or (start - reach).total_seconds() > gap_min * 60:
            events += 1
        reach = end if reach is None or end > reach else reach
    return events


def annotator_report(
    rows: Iterable[dict],
    *,
    structure_id: int,
    source: str,
    children: Iterable[dict] = (),
    owner: str | None = None,
    tolerance_s: float = DEFAULT_TOLERANCE_S,
    volume_rewrites: VolumeRewrites | None = None,
) -> AnnotatorReport:
    """Count one cell's annotations by the username Viking now attributes them to.

    ``rows`` are ``Locations`` documents of the cell and of its child
    structures (``ID``, ``ParentID``, ``Username``, ``Created``,
    ``LastModified``); ``children`` are the child structures themselves, so a
    synapse marked but never annotated still names whoever marked it.
    Anything missing is treated as absent rather than as zero: a deployment
    that answers without ``Created`` gets counts and no dates.
    ``volume_rewrites`` lets the rest of the volume show which later writes
    came from a volume-wide operation (see :func:`_volume_bulk`); without it
    only the cell's own bursts are found.
    """
    rows = [r for r in rows if isinstance(r, dict)]
    children = [c for c in children if isinstance(c, dict)]
    cell_ids = {int(structure_id)}

    per: dict[str, dict] = {}

    def slot(user: str) -> dict:
        return per.setdefault(user, {
            "annotations": 0, "on_cell": 0, "on_children": 0, "as_placed": 0,
            "revisions": 0, "restamped": 0, "structures": 0,
            "first": None, "last": None,
        })

    # Pass one: the timestamps of every write that came after its creation,
    # so the bulk edits are known before anything is attributed to a person.
    later: list[tuple[datetime, str]] = []
    prepared = []
    for row in rows:
        user = _name(row)
        created, modified = _when(row.get("Created")), _when(row.get("LastModified"))
        delta = (
            (modified - created).total_seconds()
            if created is not None and modified is not None
            else None
        )
        prepared.append((row, user, created, modified, delta))
        if delta is not None and delta > tolerance_s and modified is not None:
            later.append((modified, user))
    bulk = _bulk_clusters(later)
    if volume_rewrites is not None and later:
        written = [(m, c) for _, _, c, m, _ in prepared if m is not None and c is not None]
        bulk = sorted(bulk + _volume_bulk([m for m, _ in later], written, bulk, volume_rewrites))
    bulk_edits = _events(bulk)

    def in_bulk(stamp: datetime | None) -> bool:
        if stamp is None:
            return False
        minute = stamp.replace(second=0, microsecond=0)
        return any(start <= minute <= end for start, end in bulk)

    revisions = restamped = on_cell = on_children = 0
    for row, user, created, modified, delta in prepared:
        entry = slot(user)
        entry["annotations"] += 1
        try:
            parent = int(row["ParentID"])
        except (KeyError, TypeError, ValueError):
            parent = int(structure_id)
        if parent in cell_ids:
            entry["on_cell"] += 1
            on_cell += 1
        else:
            entry["on_children"] += 1
            on_children += 1
        if delta is None:
            pass
        elif delta <= tolerance_s:
            entry["as_placed"] += 1
        elif in_bulk(modified):
            entry["restamped"] += 1
            restamped += 1
        else:
            entry["revisions"] += 1
            revisions += 1
        if created is not None and (entry["first"] is None or created < entry["first"]):
            entry["first"] = created
        if modified is not None and (entry["last"] is None or modified > entry["last"]):
            entry["last"] = modified

    for child in children:
        slot(_name(child))["structures"] += 1

    total = len(prepared)
    annotators = [
        Annotator(
            username=user,
            annotations=e["annotations"],
            fraction=(e["annotations"] / total) if total else 0.0,
            on_cell=e["on_cell"],
            on_children=e["on_children"],
            as_placed=e["as_placed"],
            revisions=e["revisions"],
            restamped=e["restamped"],
            structures=e["structures"],
            first=e["first"].isoformat() if e["first"] else None,
            last=e["last"].isoformat() if e["last"] else None,
        )
        for user, e in per.items()
    ]
    # Largest share first; ties by name, so the order never depends on the
    # order Viking happened to answer in.
    annotators.sort(key=lambda a: (-a.annotations, a.username.lower()))
    return AnnotatorReport(
        structure_id=int(structure_id),
        source=str(source),
        annotations=total,
        on_cell=on_cell,
        on_children=on_children,
        n_children=len(children),
        revisions=revisions,
        restamped=restamped,
        bulk_edits=bulk_edits,
        tolerance_s=float(tolerance_s),
        owner=(owner or None),
        annotators=annotators,
    )
