"""Per-section XY registration offsets.

Ports ``util/registration/xyRegistration.m``, ``branchRegistration.m``,
``updateTransform.m``, ``xyShift.m``, ``data/NeitzInferiorMonkeyRegistration.m``
and ``data/NeitzNasalMonkeyRegistration.m``.

These functions *generate* the ``XY_OFFSET_*.txt`` files that
:meth:`sbfsem.core.transforms.Transforms.translate` applies.

Deviations (see DEVIATIONS.md):

* **The generator and the applier disagreed about which coordinate space they
  worked in.**  For every volume but the Nasal monkey, ``xyRegistration`` derived
  offsets from ``VolumeX``/``VolumeY`` while ``StructureAPI.setXYZum`` applied
  them to ``X``/``Y`` (F215).  One space per volume is now recorded on
  :class:`~sbfsem.core.volumes.VolumeInfo` and used by both sides.
* ``updateTransform`` applied the offset twice to the lowest new section because
  its two ranges overlapped at ``min(S.sections)``, leaving a one-section
  discontinuity; ``data/updateRegistration.m`` got the same arithmetic right with
  ``1:(minZ-1)`` (F216).  Fixed.
* ``updateTransform`` read and wrote the offset file by bare filename, so it
  depended on the working directory (F217).  Paths are explicit here.
* ``xyRegistration`` called an unqualified ``sem`` that shadowed the packaged
  copy, and plotted the Y error bars on the X panel (F219, F220).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..analysis.stats import describe
from ..core.registry import data_path, read_xy_offset
from ..core.volumes import Volumes, validate_source

__all__ = ["RegistrationResult", "xy_registration", "update_offsets",
           "write_offset_file", "xy_shift"]

log = logging.getLogger(__name__)


@dataclass
class RegistrationResult:
    """Per-section XY offsets derived from annotations spanning a section range."""

    #: ``[section, xMedian, yMedian]`` — the matrix ``updateTransform`` consumes.
    offsets: np.ndarray
    #: Full per-section statistics.
    stats: pd.DataFrame
    #: Structure IDs contributing to the fit.
    neuron_ids: np.ndarray
    #: Which Viking columns the offsets are expressed in.
    columns: tuple[str, str]


def xy_registration(
    client,
    sections,
    *,
    exclude_zero: bool = True,
) -> RegistrationResult:
    """Derive per-section XY offsets from cell annotations across a section range.

    Every neuron's position at the most sclerad section in the range is the
    reference; each section's offset is the median displacement from it.
    """
    volume = Volumes.from_str(client.source)
    xcol, ycol = volume.custom_transform_columns
    lo, hi = int(min(sections)), int(max(sections))
    url = (
        f"{client.base_url}Locations?$filter=Z le {hi} and Z ge {lo} and TypeCode eq 1"
        f"&$select=ID,ParentID,{xcol},{ycol},Z,Radius"
    )
    frame = pd.DataFrame(client.get_collection(url))
    if frame.empty:
        raise ValueError(f"No annotations between sections {lo} and {hi}")
    frame = frame.rename(columns={xcol: "X", ycol: "Y"})
    if exclude_zero:
        # Viking emits X = Y = 0 for annotations it could not place.
        frame = frame[(frame["X"] != 0) & (frame["Y"] != 0)]

    reference_z = int(frame["Z"].max())
    reference = frame[frame["Z"] == reference_z].set_index("ParentID")[["X", "Y"]]
    missing = set(frame["ParentID"]) - set(reference.index)
    if missing:
        log.info("Dropping %u neurons absent from the reference section", len(missing))
        frame = frame[~frame["ParentID"].isin(missing)]
    # A neuron with several annotations on the reference section has no single
    # reference position; drop it rather than silently taking the first.
    duplicated = reference.index[reference.index.duplicated()].unique()
    if len(duplicated):
        log.info("Dropping %u neurons with multiple reference annotations", len(duplicated))
        frame = frame[~frame["ParentID"].isin(duplicated)]
        reference = reference[~reference.index.duplicated(keep=False)]
    if frame.empty:
        raise ValueError("No usable annotations left after filtering")

    frame = frame.join(reference, on="ParentID", rsuffix="_ref")
    frame["XShift"] = frame["X_ref"] - frame["X"]
    frame["YShift"] = frame["Y_ref"] - frame["Y"]

    rows = []
    for section, group in frame.groupby("Z"):
        xs, ys = describe(group["XShift"]), describe(group["YShift"])
        rows.append(
            {
                "Z": int(section),
                "n": xs.n,
                "xMean": xs.mean, "xMedian": xs.median, "xSD": xs.std, "xSEM": xs.sem,
                "yMean": ys.mean, "yMedian": ys.median, "ySD": ys.std, "ySEM": ys.sem,
            }
        )
    stats = pd.DataFrame(rows).sort_values("Z").reset_index(drop=True)
    offsets = stats[["Z", "xMedian", "yMedian"]].to_numpy(dtype=float)
    return RegistrationResult(
        offsets=offsets,
        stats=stats,
        neuron_ids=np.unique(frame["ParentID"].to_numpy(dtype=np.int64)),
        columns=(xcol, ycol),
    )


def update_offsets(source, result: RegistrationResult, *, base=None) -> np.ndarray:
    """Fold new per-section offsets into a volume's existing offset table.

    Sections below the lowest newly measured one inherit that section's offset,
    so the registration stays continuous.  The MATLAB version's two ranges
    overlapped at ``min(sections)``, applying the offset there twice (F216).
    """
    if base is None:
        table = read_xy_offset(source)
        if table is None:
            raise FileNotFoundError(
                f"{validate_source(source)} has no XY offset file to update; "
                "pass base= to start one from scratch"
            )
    else:
        table = np.asarray(base, dtype=float)
    table = table.copy()
    index = {int(section): i for i, section in enumerate(table[:, 0])}

    new = np.asarray(result.offsets, dtype=float)
    for section, dx, dy in new:
        row = index.get(int(section))
        if row is None:
            log.warning("Section %d is not in the offset table; skipped", int(section))
            continue
        table[row, 1] += dx
        table[row, 2] += dy

    lowest = int(new[:, 0].min())
    dx, dy = new[np.argmin(new[:, 0]), 1:3]
    below = table[:, 0] < lowest
    table[below, 1] += dx
    table[below, 2] += dy
    return table


def write_offset_file(source, table, path: str | Path | None = None) -> Path:
    """Write an offset table, defaulting to the packaged data directory."""
    name = validate_source(source).upper()
    if path is None:
        existing = data_path(f"XY_OFFSET_{name}.txt")
        if existing is None:
            raise FileNotFoundError(f"No packaged offset file for {name}")
        path = existing
    path = Path(path)
    np.savetxt(path, np.asarray(table, dtype=float), delimiter=",", fmt="%g")
    read_xy_offset.cache_clear()
    return path


def xy_shift(neuron, sections, *, columns=("VolumeX", "VolumeY")):
    """Bridge a registration gap between two consecutive sections.

    Returns ``(x, y, offset)`` with every annotation at or below the vitread
    section shifted so the two sections line up.  As the MATLAB header warns,
    this is a per-neuron hack: applied broadly it leaves each neuron's most
    vitread sections in slightly different coordinate systems.
    """
    sclerad, vitread = int(max(sections)), int(min(sections))
    x = neuron.nodes[columns[0]].to_numpy(dtype=float).copy()
    y = neuron.nodes[columns[1]].to_numpy(dtype=float).copy()
    z = neuron.nodes["Z"].to_numpy(dtype=int)

    upper, lower = z == sclerad, z == vitread
    if not upper.any() or not lower.any():
        log.info("No annotations bridging the %u-%u gap", sclerad, vitread)
        return x, y, np.zeros(2)
    if upper.sum() > 1 or lower.sum() > 1:
        log.info(
            "Multiple annotations: %u on %u, %u on %u; using their means",
            upper.sum(), sclerad, lower.sum(), vitread,
        )
    offset = np.array([x[upper].mean() - x[lower].mean(), y[upper].mean() - y[lower].mean()])
    below = z <= vitread
    x[below] += offset[0]
    y[below] += offset[1]
    return x, y, offset
