# MATLAB ground-truth capture

These scripts run the original SBFSEM-tools under MATLAB and write its output to
`ground_truth/`, which `tests/parity/test_matlab_ground_truth.py` asserts
against. They were last run under **MATLAB R2023b** on macOS with the Image
Processing Toolbox, Statistics and Machine Learning Toolbox, matGeom/geom3d and
the GUI Layout Toolbox, against the live Viking service.

They expect `SBFSEM-tools/` to sit beside the `SBFSEM-pytools/` directory:

```
SBFSEM-port-to-python/
  SBFSEM-tools/        <- the MATLAB toolbox, neitz branch
  SBFSEM-pytools/      <- this repository
```

| Script | Captures |
|---|---|
| `capture_regularize.m` | `RegularizeData3D` on fixed scattered data, four interpolants × three smoothness values |
| `capture_boundaries.m` | The fitted INL/GCL surfaces, and IPL depth computed both as `micron2ipl` ships and as its own near-duplicates compute it |
| `capture_neurons.m` | Node/edge/synapse/geometry tables, soma scalars and the dendrite segmentation for c943, c2542, c7161, c7162 |
| `capture_meshes.m` | Cylinder mesh vertices and faces, raw `gencyl` grids, and the analyses for c943 and c7161 |
| `capture_ipl_neuron.m` | The same for c1441, a ganglion cell that actually sits inside the traced IPL, so the stratification analyses have real data |
| `capture_colormaps.m` | All 18 `sbfsem.ui.ColorMaps` tables at 256 and 16 entries, plus the raw `lbmap` RedBlue table the port corrects |
| `verify_findings.m` | Runs the documented crash findings and records whether each reproduces |

Run them from this directory:

```bash
cd tests/fixtures/capture
matlab -batch capture_regularize
matlab -batch capture_boundaries
matlab -batch capture_neurons
matlab -batch capture_meshes
matlab -batch capture_ipl_neuron
matlab -batch capture_colormaps
matlab -batch verify_findings > ground_truth/verify_findings_output.txt
```

A few notes for anyone re-running them:

- `writematrix` has no precision option in R2023b; these use
  `dlmwrite(..., 'precision', '%.17g')`, which round-trips a double exactly.
- Local functions must appear at the end of a MATLAB script file.
- The neuron captures hit the live Viking server, so the node counts can drift as
  annotation continues. If they do, re-record `tests/cassettes/` in the same
  session so the two stay consistent.
