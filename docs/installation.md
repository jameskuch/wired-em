# Installation

## From source

```bash
git clone <your fork> SBFSEM-pytools
cd SBFSEM-pytools
python -m venv .venv && source .venv/bin/activate
pip install -e ".[web,dev]"
```

Python 3.11 or newer. On Windows use `.venv\Scripts\activate`.

## Backend only

If you only need the analysis library — in a script, a notebook or a batch job —
skip the `web` extra:

```bash
pip install -e .
```

Nothing in `sbfsem` requires a display or a GUI toolkit.

## Optional extras

- `cmcrameri` gives the exact Crameri colormaps (`Roma`, `Batlow`, `Hawaii`,
  `Devon`); without it those fall back to matplotlib approximations.
- `mapbox-earcut` is required to extrude closed-curve outlines into slab meshes
  and is installed as a core dependency.
- `meshlab` (`pip install -e ".[meshlab]"`) adds the MeshLab render option:
  isotropic remeshing, quadric decimation or screened Poisson after any
  technique. pymeshlab is GPL-3 and about 300 MB installed, so it is optional,
  never imported by this package, and run in a child process. Without it the
  MeshLab control in the render panel is disabled.

## Running the web interface

```bash
sbfsem-web --open
```

Or with uvicorn directly, which is what you want for `--reload`:

```bash
uvicorn sbfsem_web.app:app --reload --port 8765
```

| Variable | Effect |
|---|---|
| `SBFSEM_CACHE_DIR` | Where OData responses are cached. Defaults to `~/.cache/sbfsem`. |
| `SBFSEM_OFFLINE` | Set to `1` to refuse any network call and serve only from the cache. |

## No build step

The JavaScript libraries (three.js, Cytoscape.js) are vendored under
`src/sbfsem_web/static/vendor/`, and the frontend is native ES modules with an
import map. There is no bundler, no `npm install` and no Node.js at runtime.

To update a vendored library:

```bash
npm install three@<version> cytoscape@<version>
cp node_modules/three/build/three.module.min.js src/sbfsem_web/static/vendor/three/
cp node_modules/three/build/three.core.min.js src/sbfsem_web/static/vendor/three/
cp node_modules/three/examples/jsm/controls/OrbitControls.js \
   src/sbfsem_web/static/vendor/three/addons/controls/
cp node_modules/cytoscape/dist/cytoscape.esm.min.mjs src/sbfsem_web/static/vendor/
```

`three.module.min.js` imports `three.core.min.js`, so both are needed.

## Tests

```bash
pytest              # everything, offline
pytest tests/parity # the numerics other results depend on
```

`tests/cassettes/` holds recorded Viking responses for structures 943, 1441,
2542, 7161, 7162 and 7233 in NeitzInferiorMonkey, so the parity tests run without
network access. Re-record them by pointing the cache at that directory and
loading the same IDs.

`tests/fixtures/capture/ground_truth/` holds output captured from **MATLAB
R2023b** running the original toolbox. Re-capturing it needs MATLAB with the
Image Processing and Statistics toolboxes, a copy of SBFSEM-tools beside this
repository, and a reachable Viking server:

```bash
cd tests/fixtures/capture
matlab -batch capture_regularize
matlab -batch capture_boundaries
matlab -batch capture_neurons
matlab -batch capture_meshes
matlab -batch capture_ipl_neuron
matlab -batch verify_findings
```

Without MATLAB the parity tests still run against the committed capture; they
skip only if the directory is missing.
