"""Measure a rendering in the numbers a claim about it must carry.

``sbfsem-measure`` (also ``python -m sbfsem.service.measure``) builds one or
more cells under one or more techniques and reports, per build:

* **bodies** -- how many separate visual bodies the mesh falls into
  (:func:`~sbfsem.geometry.meshdiff.visual_bodies`); a correct cell is 1.
* **faces / vertices** -- the mesh size.
* **build s** -- wall-clock seconds for the build alone.
* **peak MB** -- the process's peak resident memory after the build.  Each
  (cell, technique) runs in its own child process by default, so the number
  belongs to that build and not to whatever ran before it.
* **deviation** -- how far the surface strays from the annotations, in both
  directions (:func:`~sbfsem.geometry.meshdiff.annotation_deviation`):
  ``mean``, ``surface`` (unsupported surface), ``annot`` (unreached
  annotations), ``p95`` and ``max``, all in microns; then the exterior-only
  variants ``ext`` (surface the mesh does not enclose) and ``annext`` (rims
  the mesh does not enclose), and ``hidden``, the share of the surface area
  buried inside the render itself -- a piece-assembled render hides a
  quarter of its area inside its own soma and scores it ~0 in ``surface``.
* **creases** -- microns of edge sharper than 45 degrees per square micron
  of surface (:func:`~sbfsem.geometry.meshdiff.crease_density`): the
  staircase of a section-by-section raster is creases, and a smooth surface
  has few.  The number the Closed curve method's averaging
  (``--set curve_avg_discs=0.5``) is held to, beside the deviation.

Save a run with ``--json before.json``, change the code, and run again with
``--compare before.json`` to get a delta line under every row.  That pair is
what a before/after geometry claim should quote.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from ..core.structure import Neuron
from ..geometry.meshdiff import annotation_deviation, crease_density, visual_bodies
from .facade import SbfsemService, annotation_ground_truth
from .render_schema import technique_table

__all__ = [
    "TECHNIQUES",
    "RenderMeasurement",
    "main",
    "measure_render",
    "technique_options",
]

#: The app's named techniques, as render options: the render bar's picker and
#: every Render compare pane's picker are built from the same list
#: (``sbfsem.service.render_schema.PRESETS``), so a name here is a name there.
#: A measurement wants fixed values the app seeds per cell instead -- a tube
#: unsmoothed, closed-curve rasters at the app's 0.25 um (it uses 1 um for
#: blood vessels: pass ``--set pixel_size_um=1``) -- and the presets carry
#: those as ``measure_set``.  ``voxel2_iso``, ``voxel2_qem`` and
#: ``voxel2_poisson`` put the default technique through MeshLab.
TECHNIQUES: dict[str, dict[str, Any]] = technique_table()

_ALIASES = {
    "voxel_orig": "voxel",
    "adaptive_taubin": "adaptive",
    "curvemesh": "tube",
    "curve": "closedcurve",
    "volume": "closedcurve",
    "mixed": "hybrid",
}


def _parse_value(text: str) -> Any:
    """``--set`` values: numbers become numbers, true/false booleans."""
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    return text


def technique_options(technique: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render options for a named technique, with ``overrides`` applied.

    Unknown technique names raise; unknown option keys are left for
    :meth:`SbfsemService.build_mesh` to reject, so a typo never measures a
    default in silence.
    """
    key = _ALIASES.get(technique.lower(), technique.lower())
    try:
        options = dict(TECHNIQUES[key])
    except KeyError:
        raise ValueError(
            f"unknown technique {technique!r}; one of {', '.join(TECHNIQUES)}"
        ) from None
    options.update(overrides or {})
    return options


@dataclass(frozen=True)
class RenderMeasurement:
    """Everything ``sbfsem-measure`` reports for one build."""

    cell: int
    source: str
    technique: str
    options: dict[str, Any]
    bodies: int
    faces: int
    vertices: int
    surface_area_um2: float
    build_s: float
    peak_rss_mb: float
    deviation_s: float
    n_discs: int
    n_curves: int
    #: ``None`` when the cell has no annotations to measure against.
    dev_mean_um: float | None = None
    dev_surface_um: float | None = None
    dev_annotation_um: float | None = None
    dev_p95_um: float | None = None
    dev_max_um: float | None = None
    excess_volume_um3: float | None = None
    sample_spacing_um: float | None = None
    #: Exterior-only variants (2026-09-09): surface and rims the mesh does not
    #: enclose.  A render made of interpenetrating pieces hides part of its
    #: surface inside itself, where the raw metric scores it ~0.
    dev_exterior_um: float | None = None
    dev_annotation_exterior_um: float | None = None
    hidden_surface_fraction: float | None = None
    #: Microns of edge over 45 degrees per square micron of surface (2026-10-16;
    #: ``None`` in a file written before it).
    crease_um_per_um2: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenderMeasurement:
        return cls(**data)

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.source, self.cell, self.technique)


def _peak_rss_mb() -> float:
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux kilobytes.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def measure_render(
    neuron: Neuron,
    technique: str = "voxel",
    overrides: dict[str, Any] | None = None,
    *,
    samples: int = 60_000,
    service: SbfsemService | None = None,
) -> RenderMeasurement:
    """Build ``neuron`` under ``technique`` and measure the result.

    ``overrides`` are render option keys (``voxel_smooth_um=0.3``,
    ``tie_um=0`` ...) laid over the technique's own.  The build is uncached
    and timed on its own; the deviation is timed separately.  ``peak_rss_mb``
    is the *process* peak, so call this from a fresh process (the CLI does)
    when the number matters.
    """
    options = technique_options(technique, overrides)
    svc = service or SbfsemService(offline=True)
    t0 = time.perf_counter()
    mesh = svc.build_mesh(neuron, **options)
    build_s = time.perf_counter() - t0
    peak = _peak_rss_mb()

    t1 = time.perf_counter()
    centers, radii, polygons = annotation_ground_truth(neuron)
    dev = annotation_deviation(mesh, centers, radii, polygons, samples=samples)
    deviation_s = time.perf_counter() - t1

    return RenderMeasurement(
        cell=int(neuron.id),
        source=str(neuron.source),
        technique=_ALIASES.get(technique.lower(), technique.lower()),
        options=options,
        bodies=visual_bodies(mesh),
        faces=int(len(mesh.faces)),
        vertices=int(mesh.n_vertices),
        surface_area_um2=float(mesh.surface_area()) if not mesh.is_empty else 0.0,
        build_s=build_s,
        peak_rss_mb=peak,
        deviation_s=deviation_s,
        n_discs=int(len(centers)),
        n_curves=int(len(polygons)),
        dev_mean_um=dev.mean_um if dev else None,
        dev_surface_um=dev.surface_um if dev else None,
        dev_annotation_um=dev.annotation_um if dev else None,
        dev_p95_um=dev.p95_um if dev else None,
        dev_max_um=dev.max_um if dev else None,
        excess_volume_um3=dev.excess_volume_um3 if dev else None,
        sample_spacing_um=dev.sample_spacing_um if dev else None,
        dev_exterior_um=dev.exterior_um if dev else None,
        dev_annotation_exterior_um=dev.annotation_exterior_um if dev else None,
        hidden_surface_fraction=dev.hidden_fraction if dev else None,
        crease_um_per_um2=crease_density(mesh),
    )


# -- command line -------------------------------------------------------------

_COLUMNS: list[tuple[str, str, int]] = [
    # (attribute, header, width)
    ("bodies", "bodies", 6),
    ("faces", "faces", 10),
    ("build_s", "build s", 8),
    ("peak_rss_mb", "peak MB", 8),
    ("dev_mean_um", "dev µm", 7),
    ("dev_surface_um", "surface", 7),
    ("dev_annotation_um", "annot", 7),
    ("dev_p95_um", "p95", 7),
    ("dev_max_um", "max", 7),
    ("dev_exterior_um", "ext", 7),
    ("dev_annotation_exterior_um", "annext", 7),
    ("hidden_surface_fraction", "hidden", 7),
    ("crease_um_per_um2", "creases", 7),
    ("n_discs", "discs", 6),
    ("n_curves", "curves", 6),
]


def _fmt(value: Any, width: int, *, delta: bool = False) -> str:
    if value is None:
        return "–".rjust(width)
    if isinstance(value, bool):
        return str(value).rjust(width)
    if isinstance(value, int):
        text = f"{value:+,}" if delta else f"{value:,}"
    else:
        text = f"{value:+.3f}" if delta else f"{value:.3f}"
        if abs(value) >= 100:
            text = f"{value:+.1f}" if delta else f"{value:.1f}"
    return text.rjust(width)


def _format_table(results: list[RenderMeasurement],
                  baseline: dict[tuple, RenderMeasurement] | None) -> str:
    label_w = max(24, *(len(f"c{r.cell} {r.technique}") + 2 for r in results))
    head = "cell / technique".ljust(label_w) + " ".join(h.rjust(w) for _, h, w in _COLUMNS)
    lines = [head, "-" * len(head)]
    for r in results:
        lines.append(
            f"c{r.cell} {r.technique}".ljust(label_w)
            + " ".join(_fmt(getattr(r, a), w) for a, _, w in _COLUMNS)
        )
        before = baseline.get(r.key) if baseline else None
        if before is not None:
            deltas = []
            for a, _, w in _COLUMNS:
                now, then = getattr(r, a), getattr(before, a)
                deltas.append(_fmt(None if now is None or then is None else now - then,
                                   w, delta=True))
            lines.append("  Δ vs baseline".ljust(label_w) + " ".join(deltas))
    return "\n".join(lines)


def _load_baseline(path: str) -> dict[tuple, RenderMeasurement]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    out = {}
    for item in payload.get("results", []):
        m = RenderMeasurement.from_dict(item)
        out[m.key] = m
    return out


def _parse_sets(pairs: list[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        overrides[key.strip()] = _parse_value(value)
    return overrides


def _measure_one(args: argparse.Namespace) -> RenderMeasurement:
    """Load one cell and measure it under one technique, in this process."""
    svc = SbfsemService(offline=args.offline, cache_dir=args.cache_dir)
    try:
        svc.load_neuron(args.cell, args.source, include_synapses=False)
        neuron = svc.neuron(args.cell, args.source)
        return measure_render(
            neuron, args.technique, _parse_sets(args.set),
            samples=args.samples, service=svc,
        )
    finally:
        svc.close()


def _child_command(cell: int, technique: str, args: argparse.Namespace) -> list[str]:
    cmd = [sys.executable, "-m", "sbfsem.service.measure", "--_one", str(cell),
           "--source", args.source, "--technique", technique,
           "--samples", str(args.samples)]
    for pair in args.set:
        cmd += ["--set", pair]
    if args.offline:
        cmd.append("--offline")
    if args.cache_dir:
        cmd += ["--cache-dir", args.cache_dir]
    return cmd


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbfsem-measure",
        description=(
            "Measure renders: visual bodies, size, build time, peak memory and "
            "deviation from the annotations. Numbers before claims."
        ),
        epilog=(
            "examples:\n"
            "  sbfsem-measure 1178 --technique voxel\n"
            "  sbfsem-measure 1178 118 --technique voxel wrap --set tie_um=0\n"
            "  sbfsem-measure 1178 --json before.json   # then change code, then\n"
            "  sbfsem-measure 1178 --compare before.json\n"
            "  sbfsem-measure 1178 --offline --cache-dir tests/cassettes   # the pinned cell\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("cells", nargs="*", type=int, metavar="cell",
                        help="structure IDs to measure")
    parser.add_argument("--source", default="i",
                        help="volume name or abbreviation (default: i, Inferior Monkey)")
    parser.add_argument("--technique", nargs="+", default=["voxel"],
                        help=f"one or more of {', '.join(TECHNIQUES)} (default: voxel)")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a render option, e.g. --set voxel_smooth_um=0.3")
    parser.add_argument("--samples", type=int, default=60_000,
                        help="surface samples for the deviation metric (default: 60000)")
    parser.add_argument("--offline", action="store_true",
                        help="never contact Viking; fail on a cache miss")
    parser.add_argument("--cache-dir", default=None,
                        help="OData disk cache to read (default: $SBFSEM_CACHE_DIR or the user cache)")
    parser.add_argument("--json", metavar="PATH", help="also write the results as JSON")
    parser.add_argument("--compare", metavar="PATH",
                        help="a previous --json file; prints a delta line under each row")
    parser.add_argument("--in-process", action="store_true",
                        help="run every build in this process (peak MB then accumulates)")
    parser.add_argument("--list", action="store_true",
                        help="list techniques and option keys, then exit")
    parser.add_argument("--_one", type=int, dest="cell", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print("techniques:")
        for name, opts in TECHNIQUES.items():
            print(f"  {name:12s} {opts}")
        print("option keys:")
        keys = sorted(set(SbfsemService.MESH_OPTION_DEFAULTS)
                      | {"method", "smooth_iterations", "reduce_fraction", "pixel_size_um"})
        print("  " + ", ".join(keys))
        return 0

    if args.cell is not None:
        # Child mode: one build, one JSON object on stdout, logs on stderr.
        args.technique = args.technique[0]
        print(json.dumps(_measure_one(args).to_dict()))
        return 0

    if not args.cells:
        parser.error("give at least one cell ID (or --list)")

    results: list[RenderMeasurement] = []
    failures = 0
    for cell in args.cells:
        for technique in args.technique:
            technique_options(technique)  # fail early on a typo
            if args.in_process:
                one = argparse.Namespace(**{**vars(args), "cell": cell, "technique": technique})
                results.append(_measure_one(one))
                continue
            proc = subprocess.run(
                _child_command(cell, technique, args), capture_output=True, text=True,
            )
            sys.stderr.write(proc.stderr)
            if proc.returncode != 0:
                failures += 1
                print(f"c{cell} {technique}: FAILED (exit {proc.returncode})",
                      file=sys.stderr)
                continue
            results.append(RenderMeasurement.from_dict(json.loads(proc.stdout)))

    baseline = _load_baseline(args.compare) if args.compare else None
    if results:
        print(_format_table(results, baseline))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "results": [r.to_dict() for r in results],
            }, fh, indent=1)
        print(f"wrote {args.json}")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
