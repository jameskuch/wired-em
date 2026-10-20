"""What Viking's section-corrections service publishes, and what it does to a cell.

    .venv/bin/python tools/section_corrections.py                 # volumes, sets, rebuild status
    .venv/bin/python tools/section_corrections.py i 21768         # one cell, before and after
    .venv/bin/python tools/section_corrections.py i 21768 --worst 15
    .venv/bin/python tools/section_corrections.py i 21768 --live --mode curvefit   # as the web app

For a cell it prints the step along each Viking link from one section to the
next (the sideways jump a misregistered section makes), before and after the
correction: median, 90th and 99th percentile, the largest, and the worst
links one by one with how far each end moved.  ``--mode`` picks the
correction (neighbor, the residual field, by default; curvefit or all ask the
service to correct the whole structure); ``--live`` first draws the cell
through the volume's current section transforms, as the web app does.
``--url`` points at another service (``http://host:port`` for cleartext);
``--group`` picks a StosGroup.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

# The step along each link is the library's: the viewer's Position shift layer
# and /shift-summary measure it the same way.
from sbfsem.analysis.shifts import steps
from sbfsem.io.corrections import (
    CORRECTION_MODES,
    CorrectionsClient,
    CorrectionsError,
    apply_offsets,
    choose_set,
    fetch_offsets,
    fetch_structure_offsets,
)
from sbfsem.io.loader import NeuronLoader
from sbfsem.io.registration import RegistrationError, RegistrationProvider


def describe(label: str, s: pd.Series) -> str:
    p50, p90, p99 = np.percentile(s, [50, 90, 99])
    return (f"{label:>7}: median {p50:.3f}  p90 {p90:.3f}  p99 {p99:.3f}  max {s.max():.3f} um"
            f"  ({(s > 1.5).sum()} over 1.5 um)")


def overview(client: CorrectionsClient) -> None:
    status = client.rebuild_status()
    print(f"service {client.url}")
    print(f"rebuild: in progress {status['in_progress']}, last started {status['last_started_utc']}, "
          f"completed {status['last_completed_utc']}, volumes {status['last_volumes']}"
          + (f", last error {status['last_error']!r}" if status["last_error"] else ""))
    for v in client.list_volumes():
        print(f"volume {v['volume_name']}: VikingXML {v['vikingxml_endpoint']}")
    for s in client.correction_sets(""):
        sections = client.corrected_sections(s.volume_name, s.stos_group)
        span = f"Z {min(sections)}-{max(sections)}" if sections else "no sections"
        print(f"  set {s.volume_name} / {s.stos_group}: built {s.built_utc}, watermark "
              f"{s.annotation_watermark}, pitch {s.pitch_nm:g} nm, kernel {s.kernel_radius_nm:g} nm, "
              f"votes >= {s.min_annotation_votes}; {len(sections)} sections, {span}")


def cell(client: CorrectionsClient, source: str, cell_id: int, group: str | None, worst: int,
         mode: str = "neighbor", live: bool = False) -> None:
    loader = NeuronLoader(source)
    try:
        neuron = loader.load(cell_id, include_synapses=True)
    finally:
        loader.client.close()
    if live:
        url = neuron.volume.vikingxml_url
        if url is None:
            print(f"{neuron.volume.display_name}: its section transforms are not read; stored positions")
        else:
            registrations = RegistrationProvider()
            try:
                registrations.apply(neuron, url)
            except RegistrationError as exc:
                print(f"live registration not applied: {exc}")
            finally:
                registrations.close()
    volume = neuron.volume.viking_name
    cset = choose_set(client.correction_sets(volume), group)
    if cset is None:
        print(f"no published set for {volume}" + (f" named {group}" if group else ""))
        return
    before = steps(neuron)
    if mode == "neighbor":
        correction = fetch_offsets(neuron, client, cset)
    else:
        correction = fetch_structure_offsets(neuron, client, cset, mode, timeout=600)
    apply_offsets(neuron, correction)
    after = steps(neuron)
    s = neuron.section_correction
    print(f"c{cell_id} {volume}: set {cset.stos_group} built {cset.built_utc}; {mode}"
          + (", on the live registration" if live else ", on the stored positions"))
    print(f"  {s['n_annotations']} annotations on {s['n_sections']} sections; {s['n_found']} on a "
          f"published section, {s['n_trusted']} supported, {s['n_moved']} moved "
          f"(median {s['median_um']:.3f}, p95 {s['p95_um']:.3f}, max {s['max_um']:.3f} um)")
    print(f"  {len(before)} links across sections, the sideways step along each:")
    print("  " + describe("before", before["step_um"]))
    print("  " + describe("after", after["step_um"]))
    off = correction.offsets.set_index("ID")
    table = before.assign(after_um=after["step_um"]).nlargest(worst, "step_um")
    print(f"  the {worst} largest steps before:")
    for r in table.itertuples():
        moves = [f"{int(i)} ({off.at[i, 'dx_nm'] / 1e3:+.2f},{off.at[i, 'dy_nm'] / 1e3:+.2f})"
                 f"{'' if off.at[i, 'trusted'] else ' untrusted'}" for i in (r.A, r.B)]
        print(f"    Z {r.zA}->{r.zB}: {r.step_um:.2f} -> {r.after_um:.2f} um   moved {moves[0]}, {moves[1]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", nargs="?", help="volume, e.g. i")
    parser.add_argument("cell", nargs="?", type=int, help="structure ID")
    parser.add_argument("--url", default=None, help="service URL (default: corrections.codepharm.net)")
    parser.add_argument("--group", default=None, help="StosGroup (default: the first by name)")
    parser.add_argument("--worst", type=int, default=10, help="how many of the largest steps to list")
    parser.add_argument("--mode", default="neighbor",
                        choices=[m for m in CORRECTION_MODES if m != "none"],
                        help="the correction (default: neighbor, the residual field)")
    parser.add_argument("--live", action="store_true",
                        help="draw the cell through the current section transforms first")
    args = parser.parse_args(argv)
    try:
        with CorrectionsClient(args.url) as client:
            if args.source and args.cell:
                cell(client, args.source, args.cell, args.group, args.worst, args.mode, args.live)
            else:
                overview(client)
    except CorrectionsError as exc:
        print(f"corrections service: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
