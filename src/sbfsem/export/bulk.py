"""Many skeletons in one archive.

Asked for by Rebecca Pfeiffer (2026-09-21): a list of structure IDs in, a
folder of SWC files out, named by a pattern with an ``{id}`` placeholder, each
file saying in its own header what was exported and with which options -- so
that months later every cell in a figure can be shown to have come through the
same settings.  The archive adds ``manifest.csv``, one row per structure asked
for, including the ones that could not be written and why.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..io.odata import odata_utc
from .swc import _write_swc, tree_length

__all__ = ["MANIFEST_COLUMNS", "SwcArchive", "file_name", "swc_archive"]

#: What a name may hold besides its placeholders.
_PLAIN = re.compile(r"^[A-Za-z0-9._\-{}]+$")
_PLACEHOLDERS = ("id", "volume", "label")

MANIFEST_COLUMNS = (
    "structure_id", "volume", "label", "file", "annotations", "links", "pieces",
    "loops", "section_thickness_um", "total_length_um", "rim_corrected_length_um",
    "length_floor_um", "length_floor_by_radius_um", "swc_rows", "swc_length_um",
    "options", "annotations_stamp", "exported_at", "error",
)


class SwcArchive(bytes):
    """The bytes of an archive, and how many structures went into it.

    A ``bytes`` in every way a caller already uses it (a zip to write or send);
    the counts ride along so a route can say them outside the zip.  A partial
    archive is sent with a 200 (the alternative was a 504 holding nothing), and
    a client that reads only the response cannot tell it from a whole one:
    ``manifest.csv`` is the only place that said so.

    ``asked`` is the structures asked for (each once), ``written`` those with an
    ``.swc`` in the archive, ``failed`` those that could not be loaded or
    written, and ``not_attempted`` those the time budget ran out before.
    """

    asked: int
    written: int
    failed: int
    not_attempted: int

    def __new__(cls, data: bytes, *, asked: int = 0, written: int = 0, failed: int = 0,
                not_attempted: int = 0) -> SwcArchive:
        self = super().__new__(cls, data)
        self.asked, self.written = asked, written
        self.failed, self.not_attempted = failed, not_attempted
        return self


def file_name(pattern: str, *, structure_id: int, volume: str, label: str = "") -> str:
    """``pattern`` with ``{id}``, ``{volume}`` and ``{label}`` filled in.

    The pattern must name the structure (``{id}``), and may hold only letters,
    digits, ``. _ -`` and the placeholders: it becomes a path inside an
    archive, so nothing that could leave the archive's folder gets in.
    """
    if "{id}" not in pattern:
        raise ValueError("The file name pattern must contain {id}")
    if not _PLAIN.fullmatch(pattern) or ".." in pattern:
        raise ValueError(
            "The file name pattern may hold letters, digits, '.', '_', '-' and the "
            "placeholders {id}, {volume} and {label}")
    unknown = set(re.findall(r"{([^}]*)}", pattern)) - set(_PLACEHOLDERS)
    if unknown or pattern.count("{") != pattern.count("}"):
        raise ValueError(f"Unknown placeholder(s) in the file name pattern: {sorted(unknown)}")
    safe_label = re.sub(r"[^A-Za-z0-9_\-]+", "-", label or "").strip("-") or "unlabeled"
    return pattern.format(id=int(structure_id), volume=volume, label=safe_label)


def swc_archive(
    structure_ids: Iterable[int],
    load: Callable[[int], Any],
    *,
    volume: str,
    pattern: str = "c{id}",
    branch_types: bool = False,
    relax: float | None = None,
    relax_bound: str = "outline",
    bracket: bool = False,
    lamellae: bool = False,
    sampling: str = "annotations",
    radius: str = "stored",
    time_budget_s: float | None = None,
) -> SwcArchive:
    """A zip of one ``.swc`` per structure, plus ``manifest.csv``.

    ``load`` turns a structure ID into a neuron and may raise; a structure that
    cannot be loaded or written is a row of the manifest with its ``error``
    filled in, not a failed archive -- one bad ID in a population of fifty
    should cost one file.  The manifest's lengths are the annotation graph's;
    ``swc_rows`` and ``swc_length_um`` are the file's own, which the export
    options change.

    ``time_budget_s`` is the seconds the archive may take to start another
    structure: past it every structure not yet started is a manifest row whose
    ``error`` says it was not attempted, and the archive is returned with the
    rest.  The request behind it has a gateway that gives up at 300 s, and a
    cold structure costs a Viking round trip (RPC1 c192 5 s, 7 s with
    ``bracket``), so a long list ends as a partial archive that names what to
    ask for again, not as a 504 that holds nothing.  The result is a
    :class:`SwcArchive`: the zip's bytes and the counts a caller reports
    beside it.
    """
    from ..analysis.dendrite import morphometrics
    from .swc import RADII, SAMPLINGS

    file_name(pattern, structure_id=0, volume=volume)   # refuse a bad pattern before any work
    if sampling not in SAMPLINGS or radius not in RADII:
        raise ValueError(f"sampling must be one of {SAMPLINGS} and radius one of {RADII}")
    if relax is not None and lamellae:
        raise ValueError(
            "Relaxed positions and lamella chains do not combine: a chain replaces the "
            "annotations a relaxed node is held inside; ask for one or the other")
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    options = (f"branch_types={str(bool(branch_types)).lower()} "
               f"lamellae={str(bool(lamellae)).lower()} radius={radius} sampling={sampling}")
    if relax is not None:
        options += f" relax={float(relax):.2f} relax_bound={relax_bound}"
    rows: list[dict] = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive, \
            tempfile.TemporaryDirectory() as tmp:
        used: set[str] = set()
        started = time.monotonic()
        for structure_id in dict.fromkeys(int(i) for i in structure_ids):
            row: dict[str, Any] = dict.fromkeys(MANIFEST_COLUMNS, "")
            row.update(structure_id=structure_id, volume=volume, options=options,
                       exported_at=stamp)
            if time_budget_s is not None and time.monotonic() - started > time_budget_s:
                row["error"] = (f"NotAttempted: the archive's {time_budget_s:.0f} s ran out "
                                "before this structure was reached; ask for it again")
                rows.append(row)
                continue
            try:
                neuron = load(structure_id)
                label = getattr(neuron, "label", "") or ""
                name = file_name(pattern, structure_id=structure_id, volume=volume,
                                 label=label) + ".swc"
                if name in used:
                    raise ValueError(f"Two structures would both be written as {name}")
                path, table = _write_swc(neuron, Path(tmp) / name, branch_types=branch_types,
                                         relax=relax, relax_bound=relax_bound,
                                         lamellae=lamellae, sampling=sampling, radius=radius)
                # Everything the manifest row needs is worked out before the
                # file joins the archive, so a failure here leaves neither a
                # file nor a row that says it was written.
                graph = neuron.graph()
                m = morphometrics(neuron, bracket=bracket)
                scale = tuple(getattr(neuron, "volume_scale", None) or ())
                data_stamp = odata_utc(getattr(neuron, "data_stamp", None))
                row.update(
                    label=label, file=name,
                    annotations=graph.number_of_nodes(), links=graph.number_of_edges(),
                    pieces=m.n_components, loops=m.n_loops,
                    section_thickness_um=f"{float(scale[2]) / 1e3:.4f}" if len(scale) > 2 else "",
                    total_length_um=f"{m.total_length_um:.3f}",
                    rim_corrected_length_um=f"{m.rim_corrected_length_um:.3f}",
                    length_floor_um="" if m.length_floor_um is None
                    else f"{m.length_floor_um:.3f}",
                    length_floor_by_radius_um="" if m.length_floor_by_radius_um is None
                    else f"{m.length_floor_by_radius_um:.3f}",
                    swc_rows=len(table), swc_length_um=f"{tree_length(table):.3f}",
                    annotations_stamp=data_stamp.isoformat() if data_stamp else "",
                )
                archive.write(path, name)
                used.add(name)
            except Exception as exc:  # noqa: BLE001 - reported per structure, see above
                # A manifest row says nothing but the error when the structure failed.
                row = {**dict.fromkeys(MANIFEST_COLUMNS, ""), "structure_id": structure_id,
                       "volume": volume, "options": options, "exported_at": stamp,
                       "error": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        archive.writestr("manifest.csv", text.getvalue())
    skipped = sum(str(r["error"]).startswith("NotAttempted") for r in rows)
    return SwcArchive(
        buffer.getvalue(), asked=len(rows), written=sum(1 for r in rows if r["file"]),
        failed=sum(1 for r in rows if r["error"]) - skipped, not_attempted=skipped)
