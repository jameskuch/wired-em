# Deviations from SBFSEM-tools (MATLAB)

This file records everything in SBFSEM-pytools that does **not** reproduce the
MATLAB toolbox faithfully, and why. It is the companion to the port itself: if a
number differs from what MATLAB produced, the reason should be here.

`F###` references are findings in `matlab-bug-review.md`. Line references are to
the `neitz` branch of SBFSEM-tools as reviewed on 2026-08-28.

---

## 1. Ground truth

Phase 0 was done properly: **MATLAB R2023b** (Image Processing Toolbox, Statistics
and Machine Learning Toolbox, matGeom/geom3d, GUI Layout Toolbox) was used to run
the real SBFSEM-tools against the live Viking service, and its output is committed
under `tests/fixtures/capture/ground_truth/`. The capture scripts are in
`tests/fixtures/capture/*.m`; `tests/parity/test_matlab_ground_truth.py` asserts
against them, offline, using the HTTP cassettes in `tests/cassettes/`.

Where the port is meant to agree with MATLAB it is asserted to agree. Where it
deliberately differs, the difference is asserted to be **exactly** the documented
correction and nothing else — several capture scripts record both the value
MATLAB ships and the value its own documentation says it intended.

### Proven identical to MATLAB

| What | Agreement |
|---|---|
| Node tables for c943, c2542, c7161, c7162, c1441 — every column including `Xum`/`Yum`/`Zum`/`Rum` | **bit-identical** (0.0 difference on 1,606 rows) |
| Edge and synapse tables, `LocalName` and per-synapse annotation counts | identical |
| Closed-curve control points for all 40 sections of c2542 | identical |
| Soma ID, radius, XYZ in microns and in Viking pixels | identical |
| Omitted-location handling | identical (5 for c1441) |
| `RegularizeData3D`, all four interpolants × three smoothness values | ≤ 5e-11 relative |
| INL and GCL boundary surfaces (200×200 grids) | ≤ 9 nm over a 60 µm range |
| `micron2ipl` versus MATLAB's *corrected* calculation | ≤ 4e-10 |
| `gencyl` grids for 3- and 61-annotation segments | ≤ 3e-14 µm |
| `Line3` cylinder mesh for c7161, vertex for vertex | ≤ 7e-15 µm |
| Full c943 mesh given MATLAB's own segments — vertices, faces and surface area | ≤ 3e-14 µm; area to 12 significant figures |
| Dendritic field hull area | 127.147122 µm², identical |
| I:E counts and fraction | identical |
| Tortuosity versus MATLAB's *intended* calculation | ≤ 5e-16 |
| `surfaceArea` computed MATLAB's way (over all annotations) | identical |

### Deviations, measured

| Correction | MATLAB | Port | Size of the error |
|---|---|---|---|
| IPL depth (§3.1) at the INL markers, where the answer must be 0 | −0.0244 | **+0.0042** | 6× closer to correct |
| IPL depth on a 12×12 probe grid | — | — | up to **0.058**, ~6% of IPL depth |
| c1441 median IPL depth | 0.194882 | 0.200179 | 0.0053 |
| Stratification mode (§3.3) | 0.129000 | — | **exactly half a bin** (0.0315 of 0.063) |
| 2-D tortuosity (§3.2), c1441 | 13.302765 | **11.690016** | 14% inflated |
| 3-D tortuosity, c1441 | 2.171177 | **2.470711** | 12% understated, and *below* the 2-D value, which a correct pair never is |
| Segmentation (§3.4), c943 | 4 of 17 segments jump across degree-3/4 branch points | 0 of 17 | same nodes covered, correct grouping |
| `surfaceArea` (§3.7), c2542 | 102.948 µm² | **81.251 µm²** | 27% inflated by synapse annotations |
| Mesh volume, c943 | −90.123 µm³ | **91.727 µm³** | MATLAB winds 15 of 17 components inward and 2 outward, so its signed sum partly cancels |

`iplDepth` on a neuron outside the traced boundary region returns `n = 0` in both,
but MATLAB then reports `Mode = 1` from an empty histogram; the port returns NaN.

### Every documented crash reproduces

The findings in §4 were static reasoning in the review. They were run under
MATLAB R2023b; the transcript is
`tests/fixtures/capture/ground_truth/verify_findings_output.txt`. All of them
reproduce:

| Finding | MATLAB error |
|---|---|
| F001 `StructureTypes.isBoundaryMarker` | `Unable to resolve the name 'StructureTypes.CHBoundary'` |
| F145 `sbfsem.builtin.BloodVessel` | superclass is not an immediate superclass of the constructor |
| F146 `sbfsem.builtin.Nucleolus` | `Invalid expression` at line 70 — the file does not parse |
| F164 `sbfsem.image.ConeOutline` | `Undefined function 'addParameter' for input arguments of type 'function_handle'` |
| F089 `clipMeshByVertices` | `Unrecognized function or variable 'cutVerts'` |
| F142 `gauss2` | `Unrecognized function or variable 'x'` |
| F004 `NeuronGraph` | `Dot indexing is not supported for variables of this type` |
| F007 `graph('Synapses', true)` | `A table row subscript must be a numeric array...` |
| F014 second `getSynapses` | `All tables being vertically concatenated must have the same number of variables` |
| F038 `sbfsem.io.COLLADA(neuron, ...)` | `Must provide Neuron object or axes handle` — for an actual `Neuron` |
| F127 `ieRatio` on a synapse-free neuron | `Dot indexing is not supported for variables of this type` |

The `micron2ipl` typo is visible directly in the source, next to the correct
near-duplicates that establish intent:

```
micron2ipl.m        meshgrid(GCL.newXPts, GCL.newYPts)   <- correct
micron2ipl.m        meshgrid(INL.newYPts, INL.newYPts)   <- Y on both axes
stratificationMap.m meshgrid(INL.newXPts, INL.newYPts)   <- correct
getStrataCData.m    meshgrid(INL.newXPts, INL.newYPts)   <- correct
```

And the mitochondrial volume formula, for three annotations of radius 0.1, 0.2
and 0.3 µm over 0.09 µm sections: MATLAB's `2*pi*(sum r)^2*dz` gives 0.203575 µm³
where `sum(pi*r^2)*dz` gives 0.039584 µm³ — **5.1x inflated**, and the factor
grows with the number of annotations per organelle.

### Three defects the capture found in this port

Running against MATLAB caught three things review alone had not:

1. **`nearest` interpolation rounded the wrong way.** MATLAB's `round` goes half
   away from zero, numpy's goes half to even, and on a regular grid a point sits
   exactly at a cell midpoint often enough that the two rules picked different
   nodes for a large share of the data — a 1% relative error in the fitted
   surface. Fixed; now ≤ 5e-11 like the others.
2. **WKT rings kept their closing vertex.** Shapely returns the explicit closing
   coordinate that repeats the first; MATLAB's parser stopped one short. Keeping
   it gave `close_spline` a doubled knot and a flat spot in every outline. Fixed;
   control points now match MATLAB exactly for all 40 sections of c2542.
3. **`surf2patch` vertex ordering.** MATLAB flattens the surface grid
   column-major; the port was row-major. Same geometry, but the vertex buffers
   did not line up index-for-index, which is what makes a mesh comparable at all.
   Fixed.

### What is still not proven

- **Analyses with no in-repo caller** — `estimateSynapseXY`, `getStructureLinks`
  (deprecated in MATLAB), `validateNeuronAttributes`, `nearestNodes`, the
  adjacency helpers — have no MATLAB usage example to capture against.
- **`sbfsem.analysis.NearestNeighbor`** could never run in MATLAB, so there is
  nothing to compare to.
- **The closed-curve volume render** is validated against analytic values (a ring
  of known radius encloses a known area) rather than against MATLAB, because the
  MATLAB path rasterizes through an offscreen figure and `getframe`, which does
  not run headless and whose output depends on the display.
- **`Volumes` other than NeitzInferiorMonkey.** Everything captured is from
  `NeitzInferiorMonkey`.

## 2. Architecture

### 2.1 Web frontend instead of a Qt desktop app

The port plan specified a PyQt6 + PyVista desktop application with a
PyInstaller/Inno Setup Windows installer, and left FastAPI + a browser frontend
as future work. **The user asked for a web interface, so those roles are
swapped.** `sbfsem_web` is a FastAPI service plus a no-build browser UI; there is
no Qt frontend and no Windows installer.

The backend seam the plan defined is unchanged and is what made the swap cheap:
`sbfsem.service.facade.SbfsemService` accepts and returns pydantic DTOs, never
pandas frames or mesh objects, and `tests/unit/test_service.py` asserts that
nothing under `sbfsem/` imports a GUI toolkit and that a full
load-render-analyze-export cycle runs in a subprocess with no display.

3D rendering is three.js rather than VTK/PyVista; the connectivity view is
Cytoscape.js, as the plan recommended. Both are vendored under
`sbfsem_web/static/vendor/`, so the app runs offline with no build step and no
Node.js at runtime.

### 2.2 Package layout

`sbfsem_qt/` does not exist. `sbfsem/util/` holds the registration, colormap and
image helpers that had no better home. Otherwise the layout follows the plan.

### 2.3 What the frontend does instead of MATLAB figure code

Anything that drove a MATLAB figure handle is not ported as such: `FigureView`,
`LayoutManager`, `View`, `Interface`, `ProgressBar`, `TogglePartsView`,
`ScaleBar2`/`ScaleBar3`, `HistogramView`, `PolarHistogramView`, the `plot`
methods on the analysis classes, `util/plots/*`, `util/export/exportFigure.m` and
`saveHighResImage.m`. The equivalent capability lives in the browser: the render
view, the stratification chart, the annotation table and the snapshot button.

`+sbfsem/+render/BinaryView.m` and `ClosedCurve.binarize` rasterized by drawing
white polygons into an offscreen figure and thresholding a `getframe` screen
capture. That is the single biggest obstacle to headless operation and has no
Python equivalent; `sbfsem.geometry.rasterize` uses direct polygon rasterization
with explicit hole handling instead, following `mpoly2mask`, the cleaner of the
two MATLAB paths.

### 2.4 Not ported

- `+sbfsem/+deprecated/` — explicitly retired in the MATLAB repo.
- `+sbfsem/+core/EntityType.m` — an empty stub class.
- `util/export/exportSWC.m` — dead code: it opens the file read-only and uses the
  wrong field capitalisation. `+sbfsem/+io/SWC.m` is the working implementation
  and is what was ported.
- `util/import/getPresynapticNeurons.m` — declares a function named
  `getLinkedNeurons`, colliding by name with the newer three-output
  `util/network/getLinkedNeurons.m` (F056). `sbfsem.network.links.linked_neurons`
  covers both directions.
- `lib/MarchingCubes.m` and `lib/SurfaceSmooth.m` — vendored but unreferenced.
- `+sbfsem/+render/Primitives.m` — emitted VTK-style polyline cell lists for
  `vtkwrite`; nothing consumed them but its own unit test.
  `sbfsem.geometry.primitives` provides triangle meshes instead.
- `+sbfsem/+analysis/Segment.m` is a near-duplicate of
  `util/analysis/dendriteSegmentation.m` and `+sbfsem/+render/Segment.m`. One
  implementation is ported: `sbfsem.geometry.segmentation`.
- The vendored colormap files (`crameri.m`, `pmkmp.m`, `othercolor.m`, `lbmap.m`,
  `haxby.m`, `viridis.m`, `antijet.m`, `isolum.m`, `ametrine.m`) are pure data.
  The 256-entry table each produced under MATLAB R2023b is captured
  (`tests/fixtures/capture/capture_colormaps.m`) and shipped as
  `sbfsem/data/colormaps.json`, so the colors are MATLAB's own; see §6.4 for the
  one map that is corrected.
- `+sbfsem/+analysis/NearestNeighbor.m` referenced an undefined `xyz` and called a
  `somaDiameter` method that does not exist, so it could never run (F114). The
  `knnsearch` scaffolding is ported onto `scipy.spatial.cKDTree` in
  `sbfsem.analysis.drp.nearest_neighbor_distances` and the missing parts are
  simply implemented.

---

## 3. Corrections that change numerical results

These change what the toolbox reports. Anything computed with the MATLAB version
through these paths should be recomputed.

### 3.1 IPL depth — the largest correction in the port (F214)

`util/micron2ipl.m:29` built the INL interpolation grid as
`meshgrid(INL.newYPts, INL.newYPts)` — the Y point vector on *both* axes. Two
lines earlier the GCL block does the same thing correctly. `micron2ipl` is the
single entry point converting a micron Z coordinate into an IPL depth fraction,
and its output flows into `iplDepth`, `getSynapseStratification`,
`singleDendriteStratification`, `clipMeshByStratification`, `getStrataCData`,
`RenderApp`'s stratification histogram and `IPLDepthApp`.

Because `ipl = (z - vINL) / ((vGCL - vINL) + eps)` uses `vINL` in both numerator
and denominator, the error neither cancels nor stays small: wherever the INL X and
Y ranges differ the surface is sampled at the wrong location, and wherever a valid
X falls outside the Y range `interp2` returns NaN — which is why every consumer of
`micron2ipl` had grown NaN-stripping code.

`sbfsem.analysis.boundary.IPLBoundaries.micron2ipl` evaluates each surface on its
own grid. `tests/parity/test_regularize.py` checks the result the way the MATLAB
code never was: the depth at an INL marker must be 0 and at a GCL marker must be
1.

**Measured against MATLAB R2023b.** At the INL markers, where the answer must be
0, MATLAB returns a median of **-0.0244** and the port **+0.0042**. At the GCL
markers both return 0.9990, because only the INL term is wrong. On a 12x12 probe
grid inside both surfaces the two calculations differ by up to **0.058 -- about
6% of total IPL depth**. For c1441, an OFF midget ganglion cell with 969 usable
annotations, the median depth moves from 0.194882 to 0.200179.

**Any published IPL depth, stratification profile or ON/OFF classification
produced with the MATLAB toolbox through this path should be recomputed.**

The same line's `+ eps` in the denominator turned a zero-thickness IPL into a very
large finite number; a zero denominator now yields NaN.

### 3.2 Tortuosity, 2-D and 3-D swapped (F122)

`util/analysis/tortuosity.m:49-55`: the `ndim == 2` branch called `euclideanDist3`
and the `ndim == 3` branch called `euclideanDist2`, while the chord length in each
branch had the correct dimensionality. The default 2-D tortuosity was therefore a
3-D path over a 2-D chord — inflated by exactly the Z jitter the 2-D mode exists
to suppress — and the 3-D result was a 2-D path over a 3-D chord, which can fall
below 1.0, a physically impossible tortuosity.

### 3.3 Stratification histogram half-bin shift (F123)

`iplDepth` reported bin centers as `b(2:end) - (b(2)-b(1))` (left edges), plotted
`b(1:end-1) + (b(2)-b(1))` (right edges), and reported the mode as
`b(ind) + binWidth` (right edge again). At the default 20 bins that is a
systematic 2.5% shift of total IPL depth — enough to move a cell between the ON
and OFF strata — and the table and the plot disagreed by a full bin.
`sbfsem.analysis.stats.bin_centers` returns true centers, used everywhere.

**Measured against MATLAB R2023b.** For c1441 at the default 20 bins, MATLAB
reports a mode of 0.129000 where the true bin center is 0.097500 -- a difference
of 0.031500, exactly half the 0.063000 bin width. The whole `stats.bins` vector
sits half a bin low by the same amount.

### 3.4 Segments spanning branch points (F119)

`dendriteSegmentation` closed each segment onto its parent by appending *every*
remaining neighbor of the last node. At a degree-3 node that is more than one
node, so the segment's polyline jumped across the branch point into sibling
branches and every length, diameter and tortuosity measured from it was wrong.
`sbfsem.geometry.segmentation` appends only the node's DFS parent, and
`tests/unit/test_geometry.py::test_segment_never_spans_a_branch_point` asserts
that consecutive IDs in every segment are graph edges.

**Measured against MATLAB R2023b.**  For c943, 4 of MATLAB's 17 segments contain
consecutive nodes that are not connected in the graph — one jumps 31428 → 31471
at a degree-3 node, another 31661 → 31664 at a degree-4 node.  The port has none.
Both cover exactly the same set of annotations.

### 3.5 Mitochondrial volume (F147)

`Mitochondria.getVolume` computed `2 * pi * (sum r)^2`, squaring the *total* of
every annotation's radius rather than summing squared radii, with a spurious
factor of two. The reported volume was inflated roughly in proportion to how
finely each organelle had been traced.
`sbfsem.io.structure_group.Mitochondria.volumes` computes `sum(pi * r^2) * dz`.

### 3.6 Closed-curve render scale (F096)

`renderClosedCurveOutlines.m` multiplied every outline by 100 to get integer pixel
indices and never divided the factor out, so renders came back undersized in XY by
roughly `100 * volumeScale(1)` with the wrong XY-to-Z aspect ratio. The file's own
`05Aug2019` history note ("correct scaling but translation is slightly off")
suggests the residual was noticed and misattributed.

`sbfsem.geometry.closed_curve.build_curve_volume` takes an explicit
`pixel_size_um` and returns a `CurveVolume` carrying its own origin and spacing,
so the mesh comes back in microns by construction. **Default resolution changed**:
MATLAB used 0.01 µm implicitly (round to 2 decimals, times 100); the default here
is 0.05 µm, which is finer than the 0.07-0.09 µm section spacing and keeps volumes
an order of magnitude smaller. Pass `pixel_size_um=0.01` to match MATLAB.

Sections were also padded on the leading edge only, translating each one by a
different amount (F086); every section is now rasterized against one shared
bounding box.

### 3.6a Closed-curve renders closed up every skipped section

`renderClosedCurveOutlines.m` builds its volume with one slice per *unique
traced* Z (`zList = sort(unique(T.Z))`, `zInd = find(zList == ...)`) and then
places slice `k` at `(k + min(Z)) * volumeScale(3)`. That is right only when
every section between the first and the last was traced. Annotators skip
sections, so the render comes out short in Z by the number of skipped sections
and slides off its own annotations: exact at the bottom, the whole deficit at
the top. Found on RPC1's Müller cell c2628 (2026-09-18): 676 sections traced
over a span of 754, surface 5.46 µm short. Of the eight volumes only Inferior
Monkey is free of it; RPC2 c2968, traced at its soma and its terminal with
discs between, had the terminal drawn 54 µm from its annotations.

`build_curve_volume` gives every slice its own section number. Skipped sections
between two traced ones at most `bridge_um` apart (default 1 µm: 14 sections at
70 nm, 11 at 90 nm) are filled by shape-based interpolation (Raya & Udupa 1990,
`interpolate_masks`); a longer skip stays open, `CurveVolume.runs()` reports the
consecutive stretches and `curve_volume_to_mesh` contours each at its own
height. `CurveVolume.traced` says which slices were traced. A structure with no
skipped sections renders exactly as before.

MATLAB also never read the location links when rendering closed curves. Where
the EM is bad the annotators skip sections and link the outline below to the
one above (1,597 such links over 18 surveyed cells, none longer than 8
sections); when other branches of the structure were traced on those sections
the whole-section rule sees nothing missing and the branch is cut by a slit.
`build_curve_volume(..., links=...)` takes Viking's `(A, B)` location links and
interpolates each linked pair more than a section apart on its own, up to
`link_bridge_um` (5 µm); the service passes `neuron.edges`. There is no MATLAB-faithful switch:
the MATLAB output is a wrong height, not a convention (`bridge_um=0` turns the
interpolation off, not the placement).

### 3.7 Surface area and annotation sizes counted synapses (F128, F129)

`surfaceArea.m` and `AnnotationSizes` summed over `neuron.nodes`, which includes
child-structure annotations once synapses are imported, so their answers changed
depending on whether synapses had been loaded. Both use cell-body nodes now.
`surfaceArea` also printed microns squared under an "mm2" label, a factor of 10^6
-- confirmed live: MATLAB prints "c2542 surface area = 103 mm2" for 103 um2.

**Measured against MATLAB R2023b.** For c2542 (37 synapses) MATLAB reports
102.948 um2 and the port 81.251 um2, a 27% inflation. For c7161, which has no
synapses, the two are identical. Computing the port's sum MATLAB's way -- over
every annotation -- reproduces MATLAB bit-exactly for all five test neurons,
which is how the parity test proves this is the only difference.

### 3.8 Soma detection could pick a synapse (F019)

`somaRow` was `find(nodes.Radius == max(nodes.Radius))` over the whole node table.
Any child-structure annotation larger than the soma became the soma, changing
`getSomaID`, `getSomaXYZ` and `getSomaSize` depending on whether synapses were
imported. The search is restricted to `ParentID == ID`.

### 3.9 The "unique" synapse marker could select zero or several nodes (F016)

For a multi-node synapse, MATLAB marked nodes whose `Z` equalled
`floor(median(Z))`. With an even number of distinct sections — `Z = [10, 13]`
gives median 11.5, floor 11 — no node matched, and everything filtering on
`nodes.Unique` silently dropped that synapse. Several nodes sharing the median
section double-counted it. Exactly one node per synapse is now marked: the one
nearest the median.

### 3.10 Empty synapse queries returned `[NaN NaN NaN]` (F125)

`getSynapseXYZ` returned a NaN row rather than empty, so every `isempty` guard in
`util/analysis` was dead: `min` over an all-NaN distance vector returns index 1,
and the caller received a real, plausible micron path length measured to an
arbitrary unrelated annotation for a synapse type the neuron does not have.
`Neuron.get_synapse_xyz` returns an empty `(0, 3)` array, or raises
`SynapseNotFoundError` with `strict=True`.

### 3.11 Registration coordinate spaces reconciled (F215)

For every volume but the Nasal monkey, `xyRegistration.m` derived the per-section
offsets from `VolumeX`/`VolumeY` while `StructureAPI.setXYZum` applied them to
`X`/`Y`. **This is the one correction whose direction depends on a fact only the
live database can settle.** The port records one coordinate space per volume on
`VolumeInfo.custom_transform_columns` — `VolumeX`/`VolumeY` for
NeitzInferiorMonkey, matching how its shipped offset file was generated, and
`X`/`Y` for NeitzNasalMonkey, which was already self-consistent — and uses it in
both the generator and the applier. If the intended Inferior space is in fact
`X`/`Y`, change that one field.

Related: `updateTransform.m` applied the offset twice to the lowest new section
because its two ranges overlapped at `min(sections)`, leaving a one-section
discontinuity (F216); `data/updateRegistration.m` got the same arithmetic right.
`sbfsem.util.registration.update_offsets` follows the correct version.

### 3.11a Mesh face winding is normalized

MATLAB's condensed cylinder mesh winds most components inward and a few outward —
for c943, 15 of 17 inward and 2 outward — because `Line3`'s ring triangulation and
`gencyl`'s `surf2patch` disagree about orientation. Summing signed tetrahedron
volumes across such a mesh partly cancels: MATLAB's own arithmetic gives −90.123
µm³ where the sum of the components' absolute volumes is 91.727 µm³.

The port orients every segment outward, which is what correct lighting and glTF
back-face culling need, and reports 91.727 µm³. The *geometry* is identical:
given MATLAB's own segments, the port reproduces its 8,376 vertices to 3e-14 µm,
the same 16,340 faces, and the same surface area to twelve significant figures.

### 3.12 `sem` (F136)

`std(data)/sqrt(length(data))`: `length` returns the largest dimension, NaNs were
counted but not excluded, and empty input silently returned NaN. Every error bar
produced through `printStat` or `createStatsStructure` went through it.
`sbfsem.analysis.stats.sem` excludes NaNs and divides by the count actually used.

### 3.13 The I:E ratio was a proportion (F127)

`ieRatio.m` documented "ratio of inhibitory:excitatory synapses" but computed
`numInh / (numInh + numExc)` — the inhibitory *proportion*, bounded by 1. Rather
than pick one and silently change what published numbers mean,
`sbfsem.analysis.synapse.ie_ratio` reports **both**: `ratio` is the documented
`numInh / numExc` and `inhibitory_fraction` reproduces the MATLAB value. A cell
with neither synapse type yields NaN rather than a silent 0/0.

### 3.13a Postsynapse tags are a set, and an untagged one is not assumed conventional

`StructureTypes.fromViking` typed a Viking *Postsynapse* (TypeID 35) by
comparing its whole tag string with nine spellings, and anything else fell
through to `ConvPost`. Two consequences, both measured on the production
cache on 2026-09-22:

- **Tag order.** Viking stores tags as a set and people type them in any
  order: 66 RC1, 33 RC2 and 21 RPC1 ribbon postsynapses read
  `Ribbon;Bipolar;Glutamate`, `Bipolar;Glutamate;Ribbon`, `Glutamate;Ribbon`
  and so on, and every one was counted conventional (so was `conventional`,
  lower case, and the `PR(cone);…;Ribbon` photoreceptor inputs of RPC2).
  `postsynapse_kind` reads the tags as a case-insensitive set; the nine
  MATLAB spellings keep their meaning, so no parity result changes. This is a
  fix, in the library too.
- **No tag.** RC1's early postsynapses predate tags — 12,644 of its 46,605
  PSDs carry none (live count, 2026-09-22) — and all were counted as
  inhibitory: RC1 c606 read 1,037 conventional against 4 ribbon inputs (I:E 259). The
  library keeps MATLAB's answer by default (`from_viking(…, untagged=
  "ConvPost")`, `Neuron.untagged_psd = "assume"`), but now says so
  (`TagStatus` = `assumed`). The service and the web app use `infer`
  (`SBFSEM_UNTAGGED_PSD`, default `infer` in `sbfsem_web.app`): a PSD whose
  tags do not say takes the kind of the presynaptic structure it is linked
  to — ribbon → `RibbonPost`, conventional → `ConvPost`, bipolar
  conventional → `BipConvPost` — only when every link agrees, and is marked
  `inferred`; the rest become `UntaggedPost` and are counted in neither
  total. c606 now reads 131 conventional (130 inferred), 261 ribbon (257
  inferred) and 649 untagged. The rule was proposed by Rebecca Pfeiffer and
  agreed with Crystal Sigulinsky and the user (2026-09-22); nothing is
  written back to Viking.

### 3.14 `vikingBasis` measured its scale after translating (F229)

The pixel distance between the two calibration points was taken *after* the red
point had already been shifted, so every derived scale factor was wrong and every
coordinate traced through the basis was mis-scaled.

---

## 4. Crashes and dead paths, now implemented

These could never have corrupted a result, because they never produced one.

| MATLAB | Finding | In the port |
|---|---|---|
| `StructureTypes.isBoundaryMarker` names `GCLIPLBoundary`, which does not exist | F001 | `IPLGCLBoundary` |
| `NeuronGraph`: four defects, class never ran | F002-F006 | Folded into `Structure.graph` and `sbfsem.network` |
| `StructureAPI.graph('Synapses', true)` indexes a numeric column with a table | F007 | `graph(synapses=True)` works |
| Weighted graphs used the unfiltered edge list; `Weighted` ignored for digraphs | F008, F009 | Same filtered list, both directions |
| `NeuronJSON` cannot load a neuron with synapses | F013 | `read_legacy_json`, with `i943.json` as a test fixture |
| Second `getSynapses` concatenates mismatched tables | F014 | `merge_child_data` rebuilds derived columns |
| Offline mode dereferences an empty client | F022 | `ODataError` with a clear message |
| `GeometryOData.parseClosedCurve` leaves trailing parens, cell-indexes a char | F024, F025 | `sbfsem.io.wkt`, shapely-backed |
| `OData.baseURL` omits the volume | F029 | Service root always includes it |
| `getLinkedIDs` joins with `&` where `?` is required | F028 | Fixed |
| COLLADA exporter rejects every real neuron; scene faces not offset; `instance_visual_schene` | F038-F040 | One `lxml` writer |
| SWC: last node of each segment never typed or parented; no soma type | F049, F050 | Full traversal, `soma`/`axon` honoured |
| `clipMeshByVertices` references an undefined `cutVerts` | F089 | `sbfsem.geometry.meshops` |
| `Cylinder.Line3` clears the fallback flag it just set | F079 | Latching flag |
| `concatenateMeshes` asserts on a single input | F080 | `concatenate` handles 0, 1 or many |
| `ConeOutline` — four constructor defects, does not load | F164 | `sbfsem.geometry.cone_outline` |
| `ConeOutline` closes its spline with the last three control points | F165 | `close_spline` wraps the first three |
| `BloodVessel`, `Nucleolus` do not parse | F145, F146 | `sbfsem.io.structure_group` |
| `VikingStructureTypes.localName` switches on the wrong enum | F160 | Dispatches on `VikingStructureTypes` |
| `ColorMaps.setMap` calls a method that does not exist | F210 | `ColorMaps.colors` |
| `GraphApp`'s Table tab calls `AnnotationTableView`, which is nowhere in the repo | — | The Annotations tab is a real table |

---

## 5. Deliberate improvements

### 5.1 OData pagination

**Nothing in the MATLAB code follows `@odata.nextLink`.** Any collection query
returning more rows than the server page size was silently truncated.
`ODataClient.get_collection` follows every page.

### 5.2 Batched synapse import

`SynapseOData.expandChildData` issued one request per child structure — hundreds
per neuron — and `getLinkedNeurons` did the same per synapse. Both are batched
into `ParentID eq A or ParentID eq B or ...` filters. This is the single largest
performance win available and changes no result.

### 5.3 Node column named `TypeCode`

The column MATLAB called `Geometry` is Viking's `TypeCode` field. It is
`TypeCode` throughout; `normalize_nodes` accepts the old name so legacy JSON
still loads. The 11-column positional numeric matrix in `processLocationData` is
gone — parsing goes straight to a named-column DataFrame.

### 5.4 Graphs keyed by location ID

MATLAB kept a `nodeIDs` array mapping graph node index to Viking location ID, and
every call site had to convert. networkx keys nodes by location ID directly, so
that indirection — and the whole class of bugs where an index was used as an ID or
the reverse (F017, F140) — is gone.

### 5.5 JSON schema v2

The MATLAB format was shaped by jsonlab's nesting quirks. Schema v2 is flat and
explicitly versioned; `read_neuron_json` dispatches on the `schema` field, so
existing exports still load.

### 5.6 Disconnected components reported

`dfsearch` was never asked for `startnode` events, so the promised "these
annotations are not actually connected" warning could never fire, and disconnected
fragments were segmented and measured as one neuron (F088, F118).
`SegmentationResult.components` lists them.

### 5.7 Direct solve for the boundary surfaces

MATLAB's backslash on the stacked system is a sparse QR least-squares solve. The
default here is a sparse direct solve of the normal equations — MATLAB's own
`'normal'` option, which the regularizer makes well conditioned. On the shipped
1136-marker INL cache it agrees with a fully converged `lsqr` to within 1e-9 and
takes 0.7 s, where `lsqr` needs tens of thousands of iterations to get there. A
half-converged iterative solve returns a surface wrong by tens of microns with no
error, so `lsqr` is available but is not the default.

### 5.8 Viking types 257–270 have local names

The MATLAB `StructureTypes` vocabulary stops at Viking type 256, so the Marc lab
volumes' types 257–270 (`DistalJunction`, `FlatContact`, `BubblesSwirls`,
`PeriGJAdherens`, `CaveolaString`, `CoatedPits`, `GJEndo`, `DenseCoreVesicle`,
`StartVaricosity`, `EndVaricosity`, `GlialAdjacency`, `HorizontalCell`,
`ExtracellularVesicle`, `BasalBody`) had no local member and were counted as
`Unknown`. Each is now a member of its own, with its own color, so a cell's
synapse table and the viewer list them by name (requested 2026-10-07 for
`GlialAdjacency` on RC1 and RPC1). The Neitz volumes stop below 257 and are
unchanged.

### 5.9 Closed curve renders a cell's discs as circles

`renderClosedCurveOutlines` raises "No geometries imported from OData!" on a
structure with no closed curves, and the port returned an empty mesh, so
Closed curve on a disc-traced cell showed nothing (Inferior c16137,
2026-10-15). Each disc now stands in as its circle in its own section, and
each link between discs on different sections draws, on every section it
crosses, the stretch of the process within one section of that plane
(`closed_curve.annotations_from_discs`). A cell with no traced outline is
rasterized from those alone, as it was built on 2026-10-15.

A cell with traced outlines **and** discs gets both (2026-10-16: four traced
outlines were added to c16137's 1,931 discs, and the first version drew only
those four, a patch 14 µm wide). The traced outlines keep Viking's links and
the interpolation across skipped sections they had, links that join a disc to
an outline are followed, and the links between discs are the runs already
drawn. This changes the Closed curve surface of every such cell, so
`GEOMETRY_VERSION` is 6. Hybrid is unchanged: its curve half is the outlines,
its disc half the disc pathway.

### 5.10 Every structure type has a color

`StructureTypes.StructureColor` in MATLAB has a case for 17 members and returns
black (`otherwise rgb = [0, 0, 0]`) for every other. 27 members it defines
(`CisternPre`, `CisternPost`,
`PlaqueLikePre`, `PlaqueLikePost`, `AnnularGapJunction`, `INLIPLBoundary`,
`IPLGCLBoundary`, `CHBoundary`, `Axon`, `Caveola`, `Cilium`, `GolgiPlaque`,
`GolgiNormal`, `Loop`, `Lysosome`, `Mitochondria`, `MultivesicularBody`,
`NuclearFilament`, `Nucleolus`, `NeuroglialAdherens`, `OrganizedSER`, `Plaque`,
`Polysomes`, `RibbonCluster`, `RibosomePatch`, `Rootlet`, `VesselAdjacency`)
therefore drew black on a cell's markers, in the legend's swatches and in the
network's edges (found in the 2026-10-15 audit, W10 F09). The port gives each
of them a color of its own, chosen to be told apart from the others, from black and from
the pale gray of the untagged postsynapses (`UntaggedPost`); the two cisterns
are the browser network's own colors. No two members share a color except where
MATLAB's did (`GABAPre` and `GABAPost`, `BasalNTA` and `BasalMNTA`, `BasalTA` and
`ConvPre`). `core/types.py` (`_STRUCTURE_COLORS`); `tests/unit/test_types.py`
used to assert that `Rootlet` is black, as in MATLAB, and now asserts that no
member is.

### 5.11 The cone-outline slab is one section thick

The slab the port draws through a closed-curve outline (`cone_outline_mesh`,
behind `GET .../closed-curves/mesh` and its export) was first built with the
section thickness MATLAB assumes where it has no scale, 90 nm (6.6), which is
Inferior Monkey's: on RC1 and RC2, whose sections are 70 nm, neighboring
slabs overlapped by 20 nm. A slab is now one
section thick, read from the outlines' own Z (the loader sets `Zum` to the
section number times the volume's section thickness): 70 nm on RC1 and RC2,
and 90 nm only when no outline says what a section is. `thickness_um` still
overrides it. `geometry/cone_outline.py` (`section_thickness_um`,
`DEFAULT_SLAB_UM`). No stored surface changes: the slab mesh is made for each
request, and the Closed curve volume build does not use it.

### 5.12 Closed curve can average out its staircase

The closed-curve method stacks each section's outline, so a wobble of an
annotator's rim from one section to the next, and a process that crosses the
sections at a slant, show as steps: a soma as a stack of plates, a dendrite along
the section plane as a ribbon with a tooth on its rim for every section
(Inferior c16137 and c1178, 2026-10-16). MATLAB's `renderClosedCurveOutlines`
has no counterpart: it draws the outlines as traced, and so does the port by
default. Two options average the staircase out, **both 0 (off) in the library,
the API and the app**, so every surface is as it was (the hashes of Closed
curve and Hybrid surfaces of c943, c16137, c1178, c606 and c2628 are the same
before and after; the options are left out of the cache key while 0, so
`GEOMETRY_VERSION` stays 6):

- `curve_avg_discs` (Disc avg ×r, 0 to 3): each disc is averaged with the discs
  along its chain of links over that many of its own radii, so a soma disc is
  averaged more than a dendrite disc.
- `curve_avg_paths` (Path avg µm, 0 to 2): each traced outline is averaged with
  its neighbors' along its chain over that many microns, separately from the
  discs. A length rather than a count of sections, because sections are 70 nm
  apart in some volumes and 90 nm in others.

The finished surface is then smoothed over the same length (Taubin smoothing:
no shrinking), none where a process is only a pixel or two across. The averaged
surface is deliberately not the annotations': the mean deviation from them rises
by 0.006 µm (c16137, Disc avg 1, Voxel 0.1 µm) to 0.027 µm (c2628, Path avg
0.25 µm), which the deviation metric counts as error, and the chain's first and
last annotation are as traced. Hybrid reads `curve_avg_paths` alone: its discs
are the Cylinder build's. Measured numbers are in the user guide.

### 5.13 Closed curve can curve long links and join its bodies

MATLAB's `renderClosedCurveOutlines` has no links between discs at all (5.9 added
them, as straight runs), no bridges across skipped sections (the port's own:
the notes at the top of `closed_curve.py`) and no notion of separate bodies. Four options reach further, **each at the value the method always used
in the library, the API and the app**, so every surface is as it was (the c943
and cone-outline fingerprints of `tests/unit/test_geometry_version.py` are
unchanged, a build with the four spelled out at their defaults is the same
vertices and faces, and the options are left out of the cache key at those
values, so `GEOMETRY_VERSION` stays 6):

- `curve_links_um` (Curve links µm, 0 to 20, 0 off; Closed curve): a link whose
  discs are at least that far apart follows a curve through the chain's discs
  instead of the straight run (`geometry/curve_links.py`): a cubic Hermite
  segment through both disc centers with chordal Catmull-Rom tangents read from
  the chain on either side, in 3D with Z held monotone, straight at a chain's
  end, at a branch and at a turn of more than 90°, never bending tighter than the
  process's radius nor straying more than a quarter of its length from the
  chord. The surface is not the straight runs': the departure is up to 0.58 µm
  on Inferior c1178 (a 5.4 µm link across 22 untraced sections), and no disc
  moves.
- `curve_tie_um` (Join bodies µm, 0 to 20, 0 off; Closed curve): separate
  bodies of the raster up to that far apart are joined by a tie drawn into the
  voxel grid along the shortest crossing between their surfaces, a pixel and a
  half in radius at least (`geometry/curve_ties.py`), as the voxel pathways'
  Tie gaps joins their pieces. A tie is surface no annotation drew. The web
  viewer starts at 5 µm (2.6.2); the library and the API keep 0, MATLAB's
  separate bodies.
- `curve_bridge_um` (Bridge skips µm, 0 to 10, default 1) and
  `curve_link_bridge_um` (Follow links µm, 0 to 50, default 5; Closed curve and
  Hybrid) are `closed_curve.BRIDGE_UM` and `LINK_BRIDGE_UM`, constants until
  2026-10-16. A disc link to the next section is drawn whatever Follow links
  says, as a traced outline's is: the option is how far a link is followed
  across skipped sections.

Measured numbers are in the user guide (*Long links, separate bodies and
skipped sections*).

### 5.14 Hybrid is one surface, and Disc avg reaches the tubes

Hybrid is the port's own method (MATLAB rendered a structure's discs or its
outlines, never both). Until 2.7.0 it built its two halves apart and laid them
side by side, so where the annotator switched from outlines to discs each kept its
own sheet (Inferior c1178: five, its disc-drawn soma top a shell on the traced
soma; the user's report of 2026-10-17). Under the Voxel remesh and Ultraliser
pathways the outline raster now joins the discs' whole-cell field
(`unify._CurveLeaf`) and the union is contoured once; the outline half's
triangles join the Face cap. Under the other pathways Hybrid is as it was. The
change is keyed (`facade._hybrid_token`: `hybrid:one-field`, for a cell with
traced outlines under those two pathways), so no other stored surface is built
again and `GEOMETRY_VERSION` stays 6.

`curve_avg_discs` (Disc avg ×r, 5.12) also averages the discs a Cylinder, Tube or
Hybrid build runs its tubes through (`facade._averaged_discs`). A tube is the
union of round cones through every annotated disc, so it carries the outer edge of
every wobble of their centers and radii: c1178's thin discs come out a median 1.27
times their own area under Voxel remesh, 1.11 at Disc avg 0.5. **0 (off) in the
library, the API and the app**, and left out of the key while 0: every surface is
as it was. Numbers: the user guide (*Why disc tubes come out fatter than traced
outlines*).

### 5.15 Smoothing that moves annotations, and smoothing that does not

The user, 2026-10-17: only the volume's registration and the correction service
should move an annotation unless asked. Since 2.9.0 the two options of 5.12 and 5.14
are named for what they do, **Smooth discs ×r** and **Smooth outlines µm** (the
`curve_avg_discs` and `curve_avg_paths` keys are unchanged), their help opens with
"Moves the discs/the traced outlines before the surface is built (never in Viking)"
and says how far (at 0.5, a median disc moves 0.015 to 0.044 µm, a soma disc at most
0.77 µm), and both stay **0 (off) in the library, the API and the app**.

Two options were added that move no annotation, **both 0 (off) everywhere** and left
out of the cache key while 0, so every surface is as it was and `GEOMETRY_VERSION`
stays 6:

- `surface_smooth_um` (Smooth surface µm, 0 to 2, every method): volume-preserving
  Taubin passes on the finished surface over that length, per vertex, none where a
  process is under 1.6 mesh edges in radius (`curve_average.smooth_surface`): the
  surface step of 5.12 alone. The surface is not the one the method drew: it moves up
  to 0.06 µm (Inferior c16137, Closed curve, Voxel 0.1 µm, at 0.5) or 0.35 µm (c1178,
  Hybrid, at 1), the mean distance from the annotations within 0.003 µm.
- `exact_tube_deg` (Exact tubes °, 0 to 60; Cylinder, Tube and Hybrid under Voxel
  remesh and Ultraliser): a thin stretch's pair of discs within that many degrees of
  vertical is drawn as the hull of the two discs (`unify.DiscHull`), whose slice at
  each section is the disc as annotated, rather than a round cone, whose end balls
  reach a radius past each section. Off by default because it adds a ring of creases
  at every section (up to 3.6 times as many at 35°) and because a disc is drawn at a
  process's width (0.85 of the traced half-width), so the round cones' extra lands
  near the traced size on average. Read only by the disc methods
  (`facade.READ_ONLY_BY`).

### 5.16 GraphApp is a look of the 3D view

MATLAB's `GraphApp` (`views/GraphApp.m`) opened a figure of its own for one
neuron. Since 2.28.0 it is the 3D view's second look, the GraphApp button
beside 3D render (`graphapp.js`): the same cells, camera and zoom, so it holds
several cells and switching keeps the view. What it draws, and how it differs:

- **The skeleton, a dot at every annotation, and the rings**, on black. MATLAB
  drew black lines with black dots on white; here each cell takes a hue from a
  fixed palette (`GRAPH_HUES`), its skeleton a near-white of that hue and its
  dots and rings a pastel of it, so cells side by side are told apart. The 3D
  render's colors are not used.
- **No "surface".** MATLAB's Show surface lofted each segment's rings into a
  translucent tube colored by Z. Here each annotation's ring is drawn flat in
  its own section plane (*Rings*), which is what an annotation is
  (CLAUDE.md ground rule 2), as a line one pixel wide at any zoom and from any
  side (since 2.30.0; a 0.06 µm band before, which broke up zoomed out and
  vanished edge-on).
- **Dots at MATLAB's real sizes.** MATLAB draws a `.` marker at a third of the
  size it is given: Marker Size 2, 7 and 10 pt are 1.5, 3 and 4.5 px here; the
  flagged annotations' 12 pt and the synapses' 15 pt are 6 and 7 px.
- **The flagged annotations**: unfinished red, terminal blue, off-edge navy
  lightened to #a99bff so it reads on black. Unfinished is
  `Structure.unfinished` (degree 1, neither terminal nor off-edge), computed in
  the page from `/nodes` and `/edges`.
- **Color segments** takes the colormap's colors at golden-ratio steps
  (`segmentPositions`), the same every time. MATLAB shuffled them with
  `randperm` on every tick, recolored the synapse and flag dots too, and turned
  every line black when unticked; its color list doubled itself in a loop.
- **A synapse's readout** (MATLAB's data tip): its ID, type, the cell it links
  to and its annotation. MATLAB showed the link only when `neuron.links` had
  been fetched already; here the cell's links are fetched on its first synapse
  click.
- **Find ID** takes a Location ID first, then a synapse's structure ID, as
  MATLAB did, and flies there with the annotation ringed in yellow, where MATLAB
  drew a gray sphere with a Delete menu.
- **Not ported**: the keys (the arrows, `z`, `a`/`d`, `q`/`e`, `w`/`s` moved axis
  limits in MATLAB; the view's own keys stand), the `h` help dialog (hover help
  instead), the View / Data Cursor modes (a click always selects and names), the Update
  Neuron button (the list's ↻), the broken Table tab, and the off-edge delete
  that read the wrong tag.

### 5.17 IPL depth along the layers' normals, and the IPL's thickness

An addition, not a parity change (2.31.0, James Anderson's request of 2026-10-22).
MATLAB measures IPL depth along a vertical column: `micron2ipl` evaluates both
fitted surfaces at the point's own X/Y and returns `(z - INL(x,y)) / (GCL(x,y) -
INL(x,y))` (§3.1 is the fix of its axis typo; the method is MATLAB's). Every
library function that measures IPL depth now takes `method`: `"column"`, the
default and MATLAB's, unchanged to the bit, or `"normal"`, which measures from the
nearest point of each fitted surface, along that surface's normal
(`IPLBoundaries.depth`, `BoundarySurface.nearest`):

- the fraction is `d_inl / (d_inl + d_gcl)`, the depth from the INL `d_inl` µm,
  and the IPL's thickness through the point `d_inl + d_gcl` µm. The column's
  depth and thickness in µm are measured along Z, so a tilted IPL reads
  1/cos(tilt) too thick that way;
- both read the same fitted surfaces at the same heights (bilinear between the
  200 × 200 nodes, as `xyEval`); only the direction differs. The slopes and
  curvatures come from the bicubic spline through the nodes, and the nearest
  point is a Newton solve started below the point (exact in one step on a plane;
  against a 0.15 µm sampling of Inferior Monkey's INL, never farther, and within
  1 nm at the median);
- a point whose nearest point on a layer lies beyond that layer's fitted
  rectangle has no normal depth (NaN, counted as outside the surfaces), where the
  column, which looks straight down, may still have one. On the five marked
  volumes, among columns with both layers' markers within 25 µm, that is 1 to 4 %
  of points near the fits' edges; on 11 cells of four volumes, at most 15 of
  10,518 annotations (RC2 c74420; the column 9).

The library and the bare API keep the column (ground rule 1); the web app measures
along the normals for every volume (the Stratification tab, its PDF and workbook,
Color by IPL depth, Clip IPL and a clipped export), with the column one switch away
in Settings. Measured (60 × 60 columns at five depths, both layers' markers within
25 µm):

| Volume | Depth fraction moved, % of the IPL (median / 95 % / most) | Thickness along Z over along the normal (median / most) | IPL along the normals, µm (median, 5 to 95 %) |
|---|---|---|---|
| RC1 | 0.02 / 0.13 / 0.38 | 1.001 / 1.017 | 15.6 (11.8 to 17.3) |
| Nasal Monkey | 0.05 / 0.99 / 3.9 | 1.004 / 1.145 | 28.7 (24.3 to 30.1) |
| Temporal Monkey | 0.08 / 0.89 / 1.5 | 1.016 / 1.071 | 23.8 (19.8 to 26.3) |
| Inferior Monkey | 0.13 / 0.49 / 1.5 | 1.024 / 1.088 | 27.7 (22.6 to 30.4) |
| RC2 | 0.21 / 1.09 / 3.6 | 1.023 / 1.153 | 42.1 (35.3 to 47.3) |

On 11 cells' annotations (Inferior c1178, c16137, c21768; Temporal c118, c106;
RC1 c606, c410, c476; RC2 c74420, c5597, c5624; stored positions), the median
depth moved 0.0 to 0.2 % of the IPL and the 95th percentile of the shift was at
most 0.8 %, except Inferior c21768 (3.3 %). A histogram's mode can move by a bin
where two bins are nearly equal (Inferior c16137 in the app: 56.1 % along Z, 52.4 % along the normals).
Cost: 0.13 s for 200,000 mesh vertices and 0.81 s for 1.5 million (the column
0.04 and 0.30 s); a cell's annotations, under 15 ms. Cped, RPC1 and RPC2 have no
boundary markers, so neither method measures there.

### 5.18 Cells drawn on a flattened IPL depth axis at 1:1

An addition (2.33.0, the second stage of James Anderson's request of 2026-10-22).
MATLAB's `flattenRender` (ported as `meshops.flatten_render`, unchanged) put the
depth fraction itself on Z, so a flattened cell was 0 to 1 tall and hundreds of µm
wide. `IPLBoundaries.flatten(xyz, method, thickness_um)` and
`meshops.flatten_ipl(mesh, boundaries, method, thickness_um)` put
`fraction × thickness_um` on Z instead and leave X and Y alone:

- the INL lies at Z = 0 and the GCL at Z = `thickness_um` in every volume, whichever
  side of the INL the GCL lies on (the Neitz volumes have it at smaller Z, so a
  flattened monkey cell is drawn the other way up from its own Z; Flip Z turns it
  back), so a point's Z is its depth from the INL on an IPL of that one thickness;
- the thickness is one number per volume (the user's choice, 2026-10-22): its median
  IPL thickness along the normals (`IPLBoundaries.median_thickness_um`, the median
  `thickness_summary` and `GET /api/volumes/{source}/ipl-thickness` report: RC1
  15.6 µm, Temporal 23.8, Inferior 27.66, Nasal 28.7, RC2 42.1), so cells of one
  volume share one axis. Where a cell's own IPL is thinner or thicker than the
  median, its Z is stretched or squeezed by that ratio; on an IPL of one thickness
  that is tilted, the flattened Z is each point's distance from the INL along the
  normal, exactly (`tests/unit/test_ipl_flatten.py`);
- the fraction is measured by `method` as everywhere else (§5.17): the library's
  default the column, the app's the normals;
- a point with no depth (beyond the fits) is left out with every face that touches
  it; `meta["ipl_flatten"]` and the mesh DTO's `ipl_flatten_um`,
  `ipl_flatten_method` and `ipl_flatten_dropped` say what was done.

The library, the API (`ipl_flatten=false` on the mesh, mesh-job, export, skeleton,
synapse, boundary-sheet and vessel routes) and the app all default to off. In the
app it is the Display panel's Flatten IPL 1:1, one choice for the whole view, off at
every page load and greyed in Cped, RPC1 and RPC2: every cell whose volume has
boundary markers is drawn flattened, with its skeleton, synapse markers (centers
flattened, shapes unbent), the INL and GCL sheets (flat at 0 and at the thickness)
and the vessels; rings, outlines, the annotation wireframe, the Position shift
layer, manual moves, markers and a cell's Z range are drawn in section coordinates
and are not drawn while flattened, and GraphApp turns it off. The pick readout shows
the depth in µm from the INL. Every figure and file says what it was scaled by: the
panel's note, a snapshot's caption, an export's file name
(`c16137-NeitzInferiorMonkey-ipl-flat-27.6566um.obj`) and, where the format has
room, its comments (OBJ, PLY, a binary STL's header, GLB `extras.sbfsem`, DAE
`contributor/comments`). An SWC or the JSON export keeps Viking's positions.
Cost on Inferior c16137 (222,112 vertices, cached surface): 332 ms for the
flattened mesh along the normals and 220 ms along Z, against 209 ms unflattened;
no vertex had no depth.

---

## 6. Changes that need a decision

### 6.1 COLLADA `unit/@meter` — **flagged for user confirmation**

`+sbfsem/+io/COLLADA.m` wrote `meter="0.1"`; `util/export/exportSceneDAE.m` wrote
`meter="1"`. Neither is right for micron data. The default here is
`meter="1e-06"` with `name="micrometer"`, which makes an exported neuron the
correct physical size in any COLLADA consumer.

**This changes the scale of every previously exported asset.** Pass `meter=0.1`
to `write_collada` to reproduce the old files.

### 6.2 Closed-curve raster resolution

Default 0.05 µm/pixel rather than MATLAB's implicit 0.01 µm. See §3.6.

### 6.3 `Volumes.nSections` returns `None`

MATLAB raised for volumes whose section count it did not know. `None` is the
honest answer and lets a caller decide.

### 6.3b Materials are physically based finishes per cell, not `material` on the axes

MATLAB's `material` command set Phong specular terms for the whole axes,
with four words (`default`, `shiny`, `dull`, `metal`); RenderApp exposed
those plus flat shading. Here a finish belongs to a cell, the presets are
`MeshPhysicalMaterial` parameter sets (roughness, metalness, clearcoat,
sheen, transmission, …) that can be edited and saved to a shared library,
and *Velvet*, *Glass*, *Glow* and *Clay* have no MATLAB counterpart.
`dull`, `shiny` and `metal` map to *Matte*, *Glossy* and *Metal*; a session
saved with the old global control restores its choice onto cells that have
none of their own.

### 6.4 Colormaps are MATLAB's tables; `RedBlue` is reversed, not channel-swapped

All 18 maps of `sbfsem.ui.ColorMaps` are shipped as the 256-entry tables MATLAB
R2023b produced (`tests/fixtures/capture/capture_colormaps.m` →
`sbfsem/data/colormaps.json`), and other lengths resample them linearly, which
is what each vendored file did itself. `Gray`, `Bone` and `Hsv` are recomputed
from MATLAB's formulas so any length matches exactly. The web app colors from
the same tables (`static/js/colormap-tables.js`, generated from the registry and
checked by a unit test), and its Colormap menu lists the same 18 names in the
same order, opening on Spectral as `RenderApp` did.

`ColorMaps.RedBlue` returned `fliplr(lbmap(N, 'RedBlue'))`. On an N×3 table
`fliplr` swaps the R and B columns rather than reversing the map, so MATLAB
showed a purple-to-orange ramp. The port reverses the map instead (blue to red),
which is what `FigureView.setColormap('redblue')` did with `flipud`; the parity
test asserts both facts against the captured raw `lbmap` table.

### 6.5 `fastUM` returns a real micron symbol

`util/fastUM.m` returned `'?m'` — a mojibaked micro sign. `MICRON_SYMBOL` is
`µm`.

### 6.6 The Marc lab volumes are fully described, and differently from MATLAB

MATLAB accepted `RC1`, `RC2`, `RPC1` and `RPC2` in `validateSource` but could
not actually serve them: `getServiceRoot` raised for all four (only the retired
`websvc1.connectomes.utah.edu` host, left commented out in `getServerName.m`,
ever knew them), `getVolumeAbbrev` raised for all but RC1, `getAnimal` called
three of the four rabbit volumes "monkey" (its `otherwise` branch), and
`loadCachedVolumeScale` fell through to `[2.18 2.18 90]` for them. Here they
are served from the same `websvc.codepharm.net` host as the Neitz volumes
(verified live, 2026-08-31), all four are `animal="rabbit"`, and the offline
fallback scale is the live OData `Scale` value, (2.176, 2.176, 70.0) — the
MATLAB fallback's 90 nm section thickness was wrong.

### 6.7 Closed-curve outlines are in the registered volume frame

MATLAB's `GeometryOData` parsed `MosaicShape`, the section's own mosaic
frame, while `Neuron.nodes` took `VolumeX`/`VolumeY`, the registered volume
frame. The two differ by each section's registration: 15 to 20 µm on
Inferior Monkey, 130 µm on Nasal Monkey, 46 and 86 µm at the median in RC1
and RC2 and up to 280 µm at their edges (audited 2026-09-11,
`docs/context/closed-curve-audit-2026-09-11.md`). Every MATLAB closed-curve
render therefore sat that far from the same cell's discs. The loader here
returns Viking's registered `VolumeShape` — already evaluated, about nine
points per control interval, flagged `Registered` and never re-splined — and
only without one falls back to the mosaic control points translated by the
annotation's own `(VolumeX − X, VolumeY − Y)`. `parse_closed_curve` itself
still reproduces MATLAB's control points exactly (the parity test checks
both). A neuron JSON written before this names no frame and is moved on
read; `read_legacy_json` files keep MATLAB's frame.

---

## 7. Known limitations

- **`densityRecoveryProfile` applies no edge correction**, exactly as in MATLAB:
  the profile is biased downward near the border of the sampled region, and the
  `effective_radius` derived from it is correspondingly inflated. That statistic
  is an addition here — MATLAB returned only the profile — and carries the same
  bias.
- **`i943.json` predates the `VolumeX`/`VolumeY` switch.** It stores `XYZum`
  derived from Viking's `X`/`Y`, which Viking leaves at zero for child structures,
  so every synapse in it has `XYZum = [0, 0, z]`. `read_legacy_json` recomputes the
  micron columns by default; pass `trust_stored_um=True` for a byte-faithful round
  trip.
- **The shipped `NeitzInferiorMonkey` boundary markers span sections 308-1222.**
  Neurons outside that range — including `i943.json`, at sections 1500-1600 —
  produce IPL depths well outside `[0, 1]` or NaN. That is data coverage, not a
  defect; `StratificationDTO.n_omitted_outlier` reports it.
- **Analyses with no in-repo caller were ported without a usage example to check
  against**: `estimateSynapseXY`, `getStructureLinks` (deprecated in MATLAB),
  `validateNeuronAttributes`, `nearestNodes`, the adjacency helpers.
- **`sbfsem.analysis.drp.nearest_neighbor_distances` is scaffolding**, as its
  MATLAB counterpart was; there is no published analysis built on it to validate
  against.
