# Wired

<sub>Visual tools for connectomics</sub>

Wired renders and analyzes connectomes reconstructed from serial-section
electron microscopy, SEM or TEM, of any species and tissue: cells in 3D in the
browser, their synapses and partners, paths through the network, and the
measurements behind them. The live instance is <https://sbfsem-tools.com>.

Wired began as a Python port of [SBFSEM-tools][sbfsem], the MATLAB connectomics
toolbox for serial block-face electron microscopy annotations, developed by
[Sara Patterson][mysite] in the [Neitz Lab][neitz] at the University of
Washington, and the library keeps its name (`sbfsem`) and its MATLAB-faithful
defaults. Annotations come from the [Viking][viking] OData service at the
University of Utah.

The port has two halves:

- **`sbfsem`** — a headless backend. No GUI toolkit, no display required. Usable
  from a script, a notebook, a batch job or a server.
- **`sbfsem_web`** — a FastAPI service and a browser interface: 3D neuron
  renders (three.js), an interactive connectivity graph (Cytoscape.js),
  Graffinity-style connectivity matrices and intermediate-node tables over
  multi-hop path queries, stratification plots, annotation tables, the
  linked-neurons report and exports.

## For maintainers

Development happens in a private repository that also holds the development
notes (`CLAUDE.md`, `docs/context/`). The public repository,
<https://github.com/jameskuch/wired-em>, is published from it at each release
and leaves those notes out, so a few references to them have no file behind
them there. User-visible history: [CHANGELOG.md](CHANGELOG.md); departures from
MATLAB: [DEVIATIONS.md](DEVIATIONS.md); how to report a bug or offer a change:
[CONTRIBUTING.md](CONTRIBUTING.md).

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[web,dev]"
```

Python 3.11 or newer. No Node.js is needed: the JavaScript libraries are
vendored and there is no build step.

Optional: `pip install -e ".[web,dev,meshlab]"` adds MeshLab remeshing
(isotropic remesh, quadric decimation, screened Poisson) as a step after any
render. It pulls in pymeshlab, which is GPL-3 and about 300 MB installed;
`sbfsem` never imports it and runs it in a child process. Without it the
MeshLab control is grayed out and everything else is unchanged.

## Run the web interface

```bash
sbfsem-web --open
```

That serves <http://127.0.0.1:8765/>. Enter a Viking structure ID, pick a volume
and press Load; or search by label; or drag in a JSON export.

Environment variables: `SBFSEM_CACHE_DIR` sets where OData responses are cached,
and `SBFSEM_OFFLINE=1` forces cache-only operation.

## Use the backend directly

```python
from sbfsem import load_neuron
from sbfsem.analysis.stratification import ipl_depth
from sbfsem.geometry.cylinder import cylinder_render
from sbfsem.export.swc import write_swc

c = load_neuron(943, "i", include_synapses=True)
print(c.synapse_summary())

mesh = cylinder_render(c)
print(f"{mesh.surface_area():.1f} um2, {len(mesh)} faces")

profile = ipl_depth(c)
print(f"median IPL depth {profile.stats.median:.3f}")

write_swc(c, "c943.swc")
```

Volumes are addressed by name or abbreviation — `"i"`, `"inferior"` and
`"NeitzInferiorMonkey"` are the same volume.

## What is here

| Module | Contents |
|---|---|
| `sbfsem.core` | `Neuron`, `Structure`, `StructureTypes`, `Volumes`, `Transforms` |
| `sbfsem.io` | Viking OData client, WKT geometry, disk cache, legacy JSON reader |
| `sbfsem.geometry` | Segmentation, cylinder meshing, closed-curve rasterization, marching cubes, clipping |
| `sbfsem.analysis` | `RegularizeData3D`, IPL boundaries and depth, stratification, dendrite morphometrics, synapse analyses, DRP |
| `sbfsem.network` | Connectivity graphs, link resolution, graph metrics |
| `sbfsem.export` | SWC, COLLADA, STL, OBJ, PLY, glTF/GLB, JSON v2, Pajek, image stacks and GIFs |
| `sbfsem.service` | `SbfsemService` facade and the pydantic DTOs a frontend sees |
| `sbfsem_web` | FastAPI routes and the browser interface |

## Read this before trusting a number

[`DEVIATIONS.md`](DEVIATIONS.md) records every place this port does not
reproduce the MATLAB toolbox, and why. The most consequential entry:
`util/micron2ipl.m` built the INL interpolation grid from the Y point vector on
both axes, which corrupted every IPL depth the MATLAB toolbox produced through
that path. **Any published IPL depth, stratification profile or ON/OFF
classification computed with the MATLAB version should be recomputed.**

Measured against MATLAB R2023b: IPL depth at the INL boundary comes out at
−0.0244 where it must be 0, and is wrong by up to 6% of IPL depth; 2-D tortuosity
on one c1441 branch reads 13.30 where the correct value is 11.69; the
stratification mode sits exactly half a bin too deep; 4 of c943's 17 dendrite
segments jump across branch points; surface area is inflated 27% for a neuron
with synapses imported.

Also changed: mitochondrial volumes squared the sum of radii; closed-curve
renders never divided out a 100x rasterization factor; and the COLLADA
`unit/@meter` value changed, which rescales previously exported assets.

## Tests

```bash
pytest
```

`tests/unit` covers the backend and the HTTP layer; `tests/parity` checks the
numerics against MATLAB itself, plus the internal invariants that hold regardless
— the `RegularizeData3D` interpolation operators reproduce polynomials of their
own degree, and IPL depth is 0 at an INL marker and 1 at a GCL marker.

`tests/parity/test_matlab_ground_truth.py` compares against output captured from
**MATLAB R2023b** running the real toolbox against the live Viking service. Node
tables are bit-identical across five neurons and 1,606 annotations; the
`RegularizeData3D` port agrees to 5e-11; the IPL boundary surfaces to 9 nm; the
cylinder mesh vertex-for-vertex to 7e-15 µm. Where the port deliberately differs,
the test asserts the difference is exactly the documented correction and nothing
else. `DEVIATIONS.md` §1 has the full table.

Re-capture the ground truth with the scripts in `tests/fixtures/capture/`.

## Deployment

The live instance runs on AWS Lightsail (Ubuntu 24.04, us-west-2,
4 vCPU / 16 GB since 2026-09-11). Deploying a change is one command, which
tests the tree, syncs it, installs and restarts the service:

```bash
SBFSEM_HOST=ubuntu@<your-server> ./deploy/redeploy.sh
```

See [`docs/deployment.md`](docs/deployment.md) for the full walkthrough, the
instance sizing rationale, and what multiple concurrent users will require.

## Contributing

Bug reports, questions and ideas are welcome as
[issues](https://github.com/jameskuch/wired-em/issues). Pull requests are
welcome too: each one is read, though not every one is merged. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Migrating from the MATLAB toolbox

See [`docs/migration.md`](docs/migration.md) for a call-by-call map from the
MATLAB API.

## Citation

To cite Wired itself, use [CITATION.cff](CITATION.cff) (GitHub's *Cite this
repository*). If you use it in your research, please also cite the original
toolbox:

> Patterson SS, Neitz M, Neitz J (2019). *An S-cone circuit for edge detection in
> the primate retina.* Scientific Reports, 9, 11913.

RRID: [SCR_017350](https://scicrunch.org/resolver/SCR_017350)

Publications using SBFSEM-tools:

- Bordt et al (2019). Synaptic inputs from identified bipolar and amacrine cells
  to a sparsely branched ganglion cell in rabbit retina. *Visual Neuroscience*, 36, E006
- Patterson et al (2019). An S-cone circuit for edge detection in the primate
  retina. *Scientific Reports*, 9, 11913
- Patterson, Bordt et al (2019). Wide-field amacrine cell inputs to ON parasol
  ganglion cells in macaque retina. *Journal of Comparative Neurology*
- Patterson et al (2020). A color vision circuit for non-image-forming vision in
  the primate retina. *Current Biology*, 30(7), 1269-1273
- Patterson et al (2020). Another blue-ON ganglion cell in the primate retina.
  *Current Biology*, 30(23), R1409-R1410
- Bordt et al (2021). Synaptic inputs to broad thorny ganglion cells in macaque
  retina. *Journal of Comparative Neurology*

## License

MIT, carried over from SBFSEM-tools; the original notice is kept beside the
port's. See [`LICENSE.txt`](LICENSE.txt).

[sbfsem]: https://github.com/sarastokes/SBFSEM-tools
[neitz]: http://www.neitzvision.com/
[viking]: https://connectomes.utah.edu/
[mysite]: https://sarastokes.github.io
