# Backend guide

`sbfsem` is usable on its own. Nothing in it imports a GUI toolkit or needs a
display.

## Loading

```python
from sbfsem import load_neuron, read_legacy_json

c = load_neuron(943, "i", include_synapses=True)
c = read_legacy_json("i943.json")           # a MATLAB-era export
```

Volumes are addressed by name or abbreviation: `"i"`, `"inferior"` and
`"NeitzInferiorMonkey"` are the same volume. `validate_source` is the resolver.

For repeated work, keep the client so the disk cache and the HTTP connection are
reused:

```python
from sbfsem.io.loader import NeuronLoader

loader = NeuronLoader("i")
neurons = [loader.load(i, include_synapses=True) for i in (943, 1441, 2542)]
loader.client.close()
```

### Viking's current section transforms

The positions in Viking's database are the section-to-volume mapping as it was
when each annotation was saved. To draw what Viking draws now, map the
section coordinates through the volume's current stos files:

```python
from sbfsem.io.registration import VolumeRegistration, apply_registration, live_offsets

c = load_neuron(21768, "i", include_synapses=True)
reg = VolumeRegistration(c.volume.vikingxml_url)   # the volume's VikingXML
result = live_offsets(c, reg)                       # where each annotation goes
apply_registration(c, result)                       # Xum/Yum and outlines move
print(c.live_registration["sections_moved"])        # "937-1258, 1260-1288, ..."
```

`VolumeX`/`VolumeY` keep the stored values, and `LiveDXnm`/`LiveDYnm` hold each
annotation's move. Only flat registrations are read, where every section maps
straight to one control section. `parse_stos` reads mesh and grid transforms
as Viking's `TransformFactory` does. Apply it before the section correction
below, which is sampled at the positions it leaves.

### Viking's section corrections

Viking publishes a residual registration field per volume: for each section,
a sparse lattice of XY offsets measured from the traced processes. It is off
by default in the library, as MATLAB has nothing like it. To apply it:

```python
from sbfsem.io.corrections import CorrectionsClient, correct_neuron

c = load_neuron(21768, "i", include_synapses=True)
with CorrectionsClient() as client:          # corrections.codepharm.net, gRPC
    correction = correct_neuron(c, client)   # None: no set for this volume
print(c.section_correction)                  # set, build time, how far things moved
```

Every annotation moves by the offset at its own volume position on its own
section, and only where the sample is trusted. `Xum`/`Yum` and the
closed-curve outlines carry the move; `VolumeX`/`VolumeY` keep Viking's
values, and `CorrDXnm`/`CorrDYnm` hold each annotation's move.
`CorrectionsClient` also answers `list_volumes`, `rebuild_status`,
`correction_sets`, `corrected_sections`, `manifest`, `section` (one section's
lattice), `correct_points` and `correct_structures`.

That is the `neighbor` mode, the default of `correct_neuron`. The service can
also correct whole structures (`CorrectStructures`) with a leave-one-out
Catmull-Rom fit along each traced process:

```python
correct_neuron(c, client, mode="curvefit")   # or "all": the field, then the fit
```

The service maps each Location's section X/Y through the StosGroup itself and
returns the correction on top of that mapping; only the correction (`dx`/`dy`)
is applied, to the cell's own positions. Those are the same mapping when the
live registration is on (c21768: within 1 nm for 1,563 of 1,567 discs, the
other 4 within 0.31 µm).
Locations the service does not answer for, and any it places more than
`PLACEMENT_LIMIT_NM` (20 µm) from the cell's own position, are not moved. Until
2026-09-24 23:19 UTC the service mapped lines and curves with empty X/Y columns
from section (0, 0), about 200 µm away. Answers kept on disk are keyed on the
set and on `service_stamp()`: the service's own `service_version` (1.1.0 on),
which moves with an algorithm deploy even when no set was rebuilt (on
2026-09-25 its answers changed that way), and which a restart does not move.
Only a service that reports no version falls back to when it last finished a
rebuild, which it does on every start.
`correction_mode()` reads a setting (`True` is `neighbor`, `False` is `off`),
and `SbfsemService(section_corrections=...)` and the API take the same
values.

### What the two moves did to a cell

An annotation's position passes through up to three stages: **stored**
(`VolumeX` x scale), **registered** (plus `LiveDXnm`/`LiveDYnm`: where the
volume's current transforms put it, which is where Viking draws it) and
**corrected** (plus `CorrDXnm`/`CorrDYnm`: `Xum`/`Yum`). Every move is in the
section plane. `sbfsem.analysis.shifts` reads the stages back off a loaded
cell and answers how big the moves are and whether the links between sections
agree better afterwards:

```python
from sbfsem.analysis.shifts import shift_summary

s = shift_summary(c, pair="correction").as_dict()   # or "transforms", "both"
s["pairs"]["correction"]    # n_moved, median_um, p95_um, p99_um, max_um
s["links"]["stages"]        # the step along each link between sections, per stage
s["links"]["verdict"]       # how many links shrank, grew or stayed within 5 nm
s["coherent_sections"]      # sections whose annotations moved together
```

A section whose annotations all moved the same way was shifted as a whole (a
stos edit); a per-annotation correction scatters. `steps(neuron)` is the step
along each link at the current positions, which is what
`tools/section_corrections.py` prints before and after. The web app serves the
summary as `/api/neurons/{source}/{id}/shift-summary`; the `nodes` table
carries each annotation's two moves (`shift_um`, `live_shift_um`).

`sbfsem.analysis.correction_checks` (2.22.0) finds the annotations Viking's
curve fit pulled toward a much wider linked annotation. Service 1.2.0 takes a
soma's or a large outline's centroid as a control point of the process, so a
thin process leaving it moves about halfway to its center (InferiorMonkey
c1178's 1328721, r 0.74 um, 10.6 um toward 1557441, r 9.9 um, 0.495 of the way):

```python
from sbfsem.analysis.correction_checks import scan_neuron

scan = scan_neuron(c)              # a cell loaded with curvefit or all
scan.at(4.0)                       # Pulls toward a neighbor at least 4x as wide
scan.n_next_to_wider(4.0)          # moved annotations next to one that wide
```

A pull is an annotation whose widest linked one is at least the ratio as wide,
whose correction move points at it (cosine over 0.8, at the registered
positions), is longer than `max(2 r, 1 um)` and does not carry it past it
(`frac`, how far along the line it went, at most `MAX_FRAC` = 1 since 2.22.1);
`moved_with` lists the chain neighbors that moved with it. The fit puts an
annotation with two links on the line between its neighbors' centroids, as far
along it as its section is between theirs, so `frac` is about
`(z_i - z_k) / (z_j - z_k)`, k the thin neighbor and j the wide one: 0.5 with
one a section either side, 1/3, 2/3 or 3/4 otherwise. On production (2026-10-20,
19 cells, 64 rows at 4x) 45 such rows in six cells sat within 0.08 um of that
point (0.1 to 0.4 um on RC2); the five over 1 were tips beside a thin neighbor
under 0.7 um away, a tip that followed its pulled neighbor and a point of
r 0.001 um inside an outline, none the soma's pull.
`SbfsemService` scans every cell it corrects with a fit (milliseconds: 3 ms on
c1178), logs the pulls at 4x, and serves them as `/api/correction-log` (every
loaded cell, `correction_log`) and `/api/neurons/{source}/{id}/correction-pulls`
(one copy of one cell, `correction_pulls`).

The same scan (2.36.0) lists the traced outlines the fit moved off the outlines
linked to them (`scan.outline_moves`, `scan_outlines`). The fit takes an
outline's centroid as a point of the process, so where an outline takes in a
lobe or the base of a branch its centroid steps sideways though its membrane does
not, and the fit moves the next outline toward that step: InferiorMonkey
c16137's 1337465 (section 547) moved 0.58 um toward 546's lobe, though it lies
inside 546 (radius ratio 1.1, so no ratio catches it). Per outline, at the
registered positions (`registered_rings` moves each back by its annotation's
correction): the *overlap* with its linked outlines on other sections (shared
area over the smaller one's, summed over them), the same with the outline moved
by the correction, and `off_share`, the overlap lost over `circle_loss`, what the
same move takes from a circle of the outline's area lying on a copy of itself.
Listed when the move is at least `OUTLINE_MIN_MOVE_UM` (0.25 um), the overlap
where Viking draws it at least `OUTLINE_MIN_OVERLAP` (0.8) and `off_share` at
least `OUTLINE_MIN_OFF` (0.2); `cause` says why (`shape`, `slide`, `other`).
`outline_overlaps(..., min_move_um=0)` gives every moved outline's numbers. The
thresholds, what they list on 11 cells and what leaving outlines in place would
do are in `docs/context/correction-outlines-2026-10-22.md`.

## The data model

`Neuron.nodes`, `.edges` and `.synapses` are pandas DataFrames.

```python
c.nodes.columns
# ID ParentID VolumeX VolumeY Z Radius X Y OffEdge Terminal TypeCode
# Xum Yum Zum Rum Unique

c.get_cell_nodes()            # excludes child structures
c.get_soma_xyz()              # microns
c.get_soma_xyz(micron=False)  # Viking pixels
c.get_synapse_xyz("RibbonPost")
c.synapse_summary()
```

The column MATLAB called `Geometry` is `TypeCode`, which is what the OData field
actually is. `Xum`/`Yum`/`Zum` replace MATLAB's `XYZum` matrix column.

`Neuron.graph()` returns a `networkx.Graph` **keyed by Viking location ID**.
There is no `nodeIDs` lookup array; use the location ID directly.

```python
g = c.graph(directed=False, weighted=True)   # weight = micron distance
leaves = [n for n, d in g.degree() if d == 1]
c.get_branch_nodes(leaves[0], leaves[1])
```

## Geometry

```python
from sbfsem.geometry.cylinder import cylinder_render
from sbfsem.geometry.segmentation import dendrite_segmentation

seg = dendrite_segmentation(c)
print(len(seg), "segments", seg.components, "disconnected fragments")

mesh = cylinder_render(c, smooth_iterations=1)
mesh.surface_area(), mesh.volume(), mesh.bounding_box
mesh.decimate(0.25)     # a quarter of the faces
```

Closed-curve structures go through the volume path:

```python
from sbfsem.geometry.closed_curve import annotations_from_geometries, build_curve_volume
from sbfsem.geometry.volume import curve_volume_to_mesh

volume = build_curve_volume(
    annotations_from_geometries(c.geometries),
    volume_scale=c.volume_scale,
    pixel_size_um=0.05,
)
mesh = curve_volume_to_mesh(volume)
```

Pass `links=zip(c.edges["A"], c.edges["B"])` to follow Viking's location links:
two linked outlines more than a section apart are interpolated through the
sections between them, whatever else was traced there (the service does this).

Every slice of the volume sits at its own section number (`volume.sections`).
Sections skipped while tracing are interpolated when their traced neighbors
are at most `bridge_um` (1 µm) apart, and `volume.traced` marks which slices
were traced; a longer skip stays open, `volume.runs()` lists the consecutive
stretches, and `curve_volume_to_mesh` contours each one at its own height. Use
it rather than `volume_to_mesh(volume.volume, ...)`, which assumes one
unbroken stack.

The volume is kept in 32 x 32-pixel tiles of each section, allocated only
under some outline's window, so a wide cell costs the area its outlines cover
rather than its bounding box (`volume.allocated_voxels`; `curve_grid_voxels`
counts it beforehand, and `max_voxels` caps it). `volume.volume` (or
`volume.dense()`) builds the whole box as one array, for small structures and
tests. `curve_volume_to_mesh` smooths and contours each run in blocks of 64
cubes, only where an outline reaches, and returns the mesh a whole-run
marching cubes would: the same vertex and face arrays, bit for bit.

### Averaging out the staircase (Closed curve)

A closed-curve surface is built a section at a time, so a wobble of an
annotator's rim from one section to the next and a process that crosses the
sections at a slant both show as steps: a soma as a stack of plates, a dendrite
along the section plane as a ribbon with a tooth on its rim for every section.
`sbfsem.geometry.curve_average` averages them out in two steps, each off (0)
by default and each in the unit of what it averages:

```python
from sbfsem.geometry.closed_curve import (
    annotations_from_discs, annotations_from_geometries, build_curve_volume)
from sbfsem.geometry.curve_average import average_outlines, polish_curve_mesh
from sbfsem.geometry.volume import curve_volume_to_mesh

links = list(zip(c.edges["A"], c.edges["B"]))

# A cell traced with discs: each disc over 1 x its own radius of its chain.
discs = annotations_from_discs(c.get_cell_nodes(), links, volume_scale=c.volume_scale, average=1.0)
volume = build_curve_volume(discs, volume_scale=c.volume_scale, pixel_size_um=0.1,
                            bridge_um=0.0, keep_labels=True)
mesh = polish_curve_mesh(curve_volume_to_mesh(volume), volume, discs=1.0)

# A cell traced with outlines: each over 0.25 um along its chain.
outlines = average_outlines(annotations_from_geometries(c.geometries), links, 0.25,
                            spacing_um=0.05)
volume = build_curve_volume(outlines, volume_scale=c.volume_scale, pixel_size_um=0.1,
                            links=links, keep_labels=True)
mesh = polish_curve_mesh(curve_volume_to_mesh(volume), volume, paths_um=0.25)
```

The service does both in one raster for a cell that has both kinds:
`svc.build_mesh(c, method="closedcurve", pixel_size_um=0.1, curve_avg_discs=1.0,
curve_avg_paths=0.25)`. Under Hybrid `curve_avg_paths` averages the outline half and
`curve_avg_discs` the discs before their tubes are built (since 2.7.0, as it does
under Cylinder and Tube: `facade._averaged_discs`, the same `average_disc_nodes`).
Both move the annotations the surface is drawn through (in the build only), which is
why they are named Smooth discs and Smooth outlines in the viewer since 2.9.0 and stay
off. Two options since 2.9.0 move none: `surface_smooth_um` smooths the finished
surface of any method (`curve_average.smooth_surface`: the per-vertex Taubin passes
alone, after the technique's build and before MeshLab), and `exact_tube_deg` draws a
thin stretch's steep pairs of discs as the hull of the two discs
(`unify.DiscHull`, chosen by `unify.hull_pairs`; `unified_mesh(hull_tilt_deg=...)`)
under `voxel2` and `ultraliser`. Both 0 by default and left out of the cache key at 0.

**Hybrid as one field (2.7.0).** Under `voxel2` and `ultraliser` a Hybrid build hands
its outline raster to the whole-cell grid as one more piece,
`("curve", CurveVolume, area_um2)` (`unify._CurveLeaf`: the raster's 3×3×3 box-smoothed
occupancy, read trilinearly at each fine sample, written as `(0.5 - s) × 3 pixels`), so
`unified_mesh` contours the outlines and the discs as one sheet. `cylinder_render`
takes it as `extra_items`, with `extra_faces` (the outline half's own triangles, added
to the Face cap) and `max_auto_um` (the raster's pixel, which an automatic Fine voxel
does not exceed); `unified_mesh`'s `curve_raw_faces` widens the raw budget so the discs
keep their own grid. Convolution refuses the piece (a raster has no potential) and the
piece pathways have no field, so Hybrid stays two halves side by side under them.

**Along the chain** (`average_disc_nodes`, `average_outlines`). Every annotation
is replaced by a Gaussian-weighted local quadratic fit over the annotations
near it along its chain of Viking links: the averaging of the annotation
wireframe (`annotation_wire`), whose chains, window rule and break ratio it
reuses. A disc's window is its strength times *its own radius*, so a 5 µm soma
disc averages over microns of Z and a 0.2 µm dendrite disc over a section or
two; a traced outline has no one radius, so its window is a distance, in µm
along the chain (a length and not a number of sections, because sections are 70
nm apart in one volume and 90 nm in another). The window narrows to fit inside
its chain, so a chain's ends are as traced and a one- or two-section outline is
never touched; chains break where the radius jumps by more than 2.5 times
(`DEFAULT_BREAK_RATIO`); a disc's averaged radius stays within 0.25 to 4 times
what was traced. Outlines are resampled to a point every half pixel (16 to
1,024 points), aligned to their neighbors' with the best rotation of their
start, so the raster loses nothing of them; cutouts are kept.

**Over the surface** (`polish_curve_mesh`). The teeth are creases the width of
a pixel, which no averaging of annotations reaches. A raster built with
`keep_labels=True` also keeps a byte for every voxel (`CurveVolume.labels`: the
radius of the annotation that drew it on a logarithmic scale, ten steps to an
octave, and whether a traced outline drew it, `label_code` and `decode_labels`),
and `CurveVolume.label_at(points_um)` reads the largest label among the eight
voxels round a point. Each vertex of the contoured mesh then takes
`passes = round(0.45 · (L / edge)²)` Taubin passes (`smoothing_passes`) where `L`
is its annotation's length: the disc strength times the disc's radius, or the
outline distance. That is capped at 20 (`POLISH_MAX_PASSES`), scaled down
together if the sum over the mesh passes `POLISH_BUDGET` (60 million
vertex-passes, about half a minute on a quiet machine), and ramped to none for a process whose radius is
under 1.6 mesh edges (full from 3: `THIN_EDGES`), which Taubin would collapse.
`Mesh.taubin_weighted(passes)` is the smoother that takes a count per vertex:
for a uniform count it is `Mesh.taubin(count)` (bitwise on c1178 in the tests),
and it is about twice as fast, because stages of vertices that have stopped drop
out of the sparse product. Past 1.5 million vertices it runs in slabs along the
longest axis, each with a margin of the reach of the largest count, so the
result is the same and the memory is bounded.

Two measures describe what it does, and both are in `sbfsem-measure` and
`sbfsem.geometry.meshdiff`:

```python
from sbfsem.geometry.meshdiff import crease_density, dihedral_angles

crease_density(mesh)                 # um of edge sharper than 45 degrees per um^2 of surface
angle_deg, length_um = dihedral_angles(mesh)   # every interior edge: its angle and length
```

`sbfsem-measure 16137 --technique closedcurve --set pixel_size_um=0.1 --set
curve_avg_discs=1` prints them as the *creases* column beside the deviation from
the annotations; the numbers for c16137, c1178 and c2628 are in the user guide.

The options are left out of the mesh's cache key while both are 0
(`facade.UNKEYED_AT_OFF`), so a stored surface and its `mesh_id` stay what they
were and `GEOMETRY_VERSION` did not change; once either is above 0 they are in
the key. A later change to how the averaging reads annotations *is* a change to
the geometry of the surfaces that carry the option, and needs a version bump
like any other (CLAUDE.md ground rule 12). `estimate_peak_mb(...,
curve_averaging=True)` adds to the build's estimate up to 70 % of it
(`CURVE_AVERAGING_FACTOR`, 1.7) and never more than 900 MB
(`CURVE_AVERAGING_MAX_MB`): the labels add a byte a voxel and the smoothing the
mesh's adjacency a second time, and the slabs stop that from growing with a mesh
of millions of vertices. Measured peaks, off and on: c16137 at 0.1 µm 338 and 456
MB, c1178 569 and 802, c2628 794 and 1,122; c16137 at 0.025 µm (3.5 million
faces) 1,177 and 1,837; c2628 at 0.025 µm (10.1 million) 3,222 and 3,880.

### Curved links, joined bodies and the bridges (Closed curve)

Four keywords reach further than the straight runs and the constants the method
had; each is off, or the constant it was, by default.

```python
from sbfsem.geometry.closed_curve import annotations_from_discs, build_curve_volume
from sbfsem.geometry.curve_links import link_curves, turning_angles
from sbfsem.geometry.curve_ties import tie_bodies
from sbfsem.geometry.volume import curve_volume_to_mesh

links = list(zip(c.edges["A"], c.edges["B"]))

# Links whose discs are 1 um or more apart follow a curve through the chain.
discs = annotations_from_discs(c.get_cell_nodes(), links, volume_scale=c.volume_scale,
                               curve_um=1.0)
volume = build_curve_volume(discs, volume_scale=c.volume_scale, pixel_size_um=0.25,
                            links=None, bridge_um=0.0)

# Bodies up to 5 um apart joined by ties in the raster; only the blocks the
# ties reach are contoured again (the cache is what the first contour kept).
blocks = {}
mesh = curve_volume_to_mesh(volume, cache=blocks)
mesh, ties = tie_bodies(volume, mesh, 5.0, cache=blocks)
```

The service takes them as `curve_links_um`, `curve_tie_um`, `curve_bridge_um`
and `curve_link_bridge_um`: `svc.build_mesh(c, method="closedcurve",
pixel_size_um=0.25, curve_links_um=1.0, curve_tie_um=5.0)`. Hybrid reads the
two bridges alone (its discs are the Cylinder build's, which ties its own pieces).

**Curved links** (`curve_links.link_curves`). For every drawn link at least
`min_step_um` long (3D distance between the disc centers): a cubic Hermite
segment from the lower disc to the upper, its tangents Catmull-Rom's with chordal
knots (`CURVE_ALPHA` = 1) through the disc on the far side of each end, read from
the first disc there at least the end disc's radius away (at most `LOOK_BACK`
discs, so a stack's sideways jitter does not tilt it). An end keeps the chord's
tangent at a chain's end (`"end"`), a branch (`"branch"`) and a turn over
`MAX_TURN_DEG` (90°, `"turn"`). The tangents' Z is held between 0 and three times
the link's rise (Fritsch and Carlson's condition for a monotone cubic), so the run
crosses exactly the sections the straight one did, and `annotations_from_discs`
gives each section the part of the curve whose Z is within one section of its
plane (`LinkCurve.window`), drawn as the union of the convex hulls of the circles
at short steps along it (`curve_outline`: shapely's `union_all`, steps cutting at
most `STEP_TOLERANCE_UM`, 0.01 µm, inside a bend). A curve whose tightest bend is
under `BEND_RADII` (1) process radii, or that strays from its chord by more than
`CORRIDOR` (a quarter) of its length, has both tangents blended toward the chord
by bisection (`LinkCurve.scale`); under `MIN_SCALE` it is drawn straight. A link
left straight is drawn by the same code as before, so a cell with nothing to curve
is the same annotations, outline for outline. `turning_angles(points, links,
curves)` gives the turn at every disc with two links, as straight runs and as
drawn.

**Joined bodies** (`curve_ties.tie_bodies`). The contour's connected pieces
(`_pieces`: `unify.split_components`'s pieces, with one sort of the faces) are
joined along the minimum spanning tree of their gaps up to `max_um`, shortest
first, by `cylinder._tie_segments` with no touch tolerance and every vertex
searched (`SAMPLE_ALL`: one big sheet and many small ones, so a subsample only
adds the refinement round each tie). Each tie is painted into the raster
(`CurveVolume._allocate` adds the tiles it needs) at `tie_radius_um(pixel)`,
max(0.25 µm, 1.5 pixels), extended two radii into both bodies, each section
getting the part within one section of its plane; a tie whose ends lie in two
runs of sections is left out. `curve_volume_to_mesh(volume, cache=..., dirty=...)`
then contours again only the blocks the ties reach (`geometry.volume.blocks_touched`) and
reuses the rest: the mesh is the one a fresh contour of the tied raster makes,
vertex for vertex (`tests/unit/test_curve_links.py`). The mesh's `meta` says how
many ties it took (`curve_ties`).

**Bridges.** `bridge_um` and `link_bridge_um` of `build_curve_volume` and
`annotations_from_discs` were always keywords; the service now passes them only
when they differ from `BRIDGE_UM` and `LINK_BRIDGE_UM`, so a build at the defaults
is the call it always was.

The four are left out of the mesh's cache key at their defaults
(`facade.UNKEYED_AT_OFF`, now a mapping of each option to the value a surface
stored before it existed was built with), so `GEOMETRY_VERSION` did not change;
2.5.1's raw-triangle budget token sits beside them in the key and never applies
to Closed curve. `estimate_peak_mb(..., curve_options=...)` counts the raster with
the bridges asked for, and adds 35 bytes a raster voxel (`CURVE_TIE_BYTES_PER_VOXEL`,
at most `CURVE_TIE_MAX_MB`, which follows `MAX_SURFACE`) for joined bodies, which keep every block's contour for
the second pass and hold the first surface meanwhile. Measured peaks, off and with
Join bodies 2 µm: c1178 at 0.1 µm 520 and 912 MB, at 0.05 µm 1,074 and 1,704; c2628 at
0.05 µm 1,352 and 2,260, at 0.025 µm (11 million triangles) 3,231 and 5,835. The ties'
tiles are allocated once for all of them (`CurveVolume._allocate` takes every window):
one allocation a tie left c2628 at 0.025 µm peaking at 10,067 MB.

### Where an annotation is read as a ball

An annotation is a disc in its section plane, not a sphere (CLAUDE.md ground
rule 2), and no default pathway reads one as anything else. Two settings do,
on purpose:

- **Convolution surface** gives an annotation nothing else joins a
  `PointKernel`, a sphere of its radius, because the published method's
  skeletons have no such strays. The other field pathways read the same
  annotation as a coin (a lens of half-height `STRAND_COIN_HALF_UM`).
- **Ultraliser with Soma = Sphere** (`ultra_soma="sphere"`) replaces each fat
  cluster with a ball of its mean radius, as Ultraliser's own starting soma is.
  It is there to show what a skeleton tool makes of soma sections.

### Library knobs without a control

Some arguments of `cylinder_render` and its helpers are reached by no caller in
the app or the API; the library keeps them for scripts (audit 2026-10-15):
`voxel_min_radius_um` (the radius from which an annotation takes part in the
voxel pathways, default `REMESH_MIN_RADIUS_UM`), `curve_points`, the
`smooth_mode`, `adaptive_rings` and `remesh` overrides of a pathway's
combination (`PATHWAYS`), and `gencyl_mesh`'s `n_between`. `voxel_detail` is
API-only (the render schema says so): it sizes the cluster fields of the
Voxel, Wrap and Voxel remesh pathways, and **Convolution ignores it** because it
convolves the skeleton and builds no cluster fields. In
`sbfsem.geometry.segmentation`, `segment_endpoints`, `SegmentationResult.starting_nodes`
and `.start_id`, `dfs_events(restart=False)` and `dendrite_segmentation(graph=)`
are ports no caller uses.

What a Voxel remesh build did about the Fine voxel is in `mesh.meta`
(`voxel2_um`, the voxel used, and `voxel2_coarsened`, true when it came out
coarser than asked: a face or work budget, or the 0.04 µm floor raising a
smaller value that was typed). The server log has the same; no API field
carries it, so the viewer cannot show it.

### The annotation wireframe

A wireframe straight from the annotations, no surface: each disc and closed
curve a ring in its section plane, strung into chains along Viking's links,
each ring fitted (a local quadratic by default) over the rings near it along
its chain, with a window of `gain` times its own radius. A soma is averaged
over microns of Z, a thin dendrite over a section or two; chain ends stay as
annotated, and a link across a radius jump of more than `break_ratio` is a
tie, not a chain step.

```python
from sbfsem.geometry.annotation_wire import annotation_wire_from_neuron

wire = annotation_wire_from_neuron(c, gain=0.5, fit="quadratic")
len(wire.rings), len(wire.chains), len(wire.ties)
wire.moved_um()                      # how far averaging moved each ring
segments = wire.segments(lines=12, spacing_um=0.5)   # (S, 2, 3), microns
```

The wireframe writers take these segments as well as a mesh (whose triangle
edges they write); endpoints shared between segments become one vertex:

```python
from sbfsem.export.mesh_formats import write_wireframe_obj, write_wireframe_hdf5

write_wireframe_obj(segments, "c943-annotation-wire.obj", notes={"gain": 0.5})
write_wireframe_hdf5(segments, "c943-annotation-wire.h5", name="c943")
```

### MeshLab remeshing (optional)

```python
from sbfsem.geometry import meshlab

if meshlab.available():                       # is pymeshlab installed? (never imports it)
    uniform = meshlab.remesh(mesh, "isotropic", edge_um=0.25)
    light = meshlab.remesh(mesh, "qem", fraction=0.25)
    fitted = meshlab.remesh(mesh, "poisson", depth=10)
```

pymeshlab is the `[meshlab]` extra: GPL-3, so `sbfsem` looks for it with
`find_spec` and runs it in a child process (`sbfsem.geometry._meshlab_worker`,
the mesh in on stdin and out on stdout, nothing written to disk) rather than
importing it. `MeshlabUnavailable` says how to install it; any failure of the
child is a `MeshlabError` with the tail of its stderr. Through the service it
is the render option `meshlab` (`none`, `isotropic`, `qem`, `poisson`) with
`meshlab_edge_um`, `meshlab_fraction` and `meshlab_depth`, applied after the
technique and part of the mesh cache key.

### Measuring a render

A claim about a render should carry numbers. `sbfsem-measure` builds a cell
under one or more techniques and reports how many visual bodies the mesh
falls into, its size, the build time, the peak memory, and how far the
surface strays from the annotations in both directions:

```bash
sbfsem-measure 1178 --technique voxel wrap
sbfsem-measure 1178 --json before.json      # then change code, then
sbfsem-measure 1178 --compare before.json   # a delta line under every row
sbfsem-measure 1178 --offline --cache-dir tests/cassettes   # the pinned cell
```

The technique names (`voxel2`, `voxel`, `original`, `adaptive`, `wrap`,
`convolution`, `ultraliser`, `tube`, `closedcurve`, `hybrid`, and the MeshLab
ones `voxel2_iso`, `voxel2_qem`, `voxel2_poisson`) are the web app's technique
picker, one list declared in `sbfsem.service.render_schema` along with every
other render option. `sbfsem-measure --list` prints each as render options; a
tube is measured unsmoothed and the closed-curve methods at 0.25 µm.

Which options act on which pathway is measured, not assumed. Each row of
`tools/measure_knob_matrix.py` builds Inferior c1178 twice with one option
changed and records whether the surface changed
(`tests/fixtures/render_knob_matrix.json`); `tests/unit/test_knob_matrix.py`
holds the schema's `show` and `inert` rules (the controls the 3D render and
Render compare show or grey out) to those verdicts, and pins the one place
they still disagree. Perturb hard: Decimate 0.5 looks inert on a whole-cell
pathway only because both builds hit its 400,000-face cap, and 0.1 acts.

The same numbers are available in Python:

```python
from sbfsem.geometry.meshdiff import annotation_deviation, visual_bodies
from sbfsem.service.measure import measure_render

m = measure_render(c, "voxel", {"tie_um": 0})
m.bodies, m.faces, m.build_s, m.peak_rss_mb, m.dev_mean_um
```

`visual_bodies` merges mesh components whose surfaces come within 0.3 µm, so
a cell that *looks* like one body counts as one; `annotation_deviation`
measures surface-to-annotation and annotation-to-surface distances separately
(unsupported surface and unreached annotations are different failures).

Both `annotation_deviation` and `deviation_map` (the same distances, kept
point by point for the error picture) measure through one kernel,
`nearest_annotation(points, centers, radii, polygons)`. For each point it
returns `dist` (to the nearest disc or closed-curve outline), `owner` (which
annotation: the discs by index, then the outlines in the order given, -1 when
there are none), and the two parts of the distance, `radial` (in the section
plane) and `axial` (along Z). It is exact, not an approximation: the tests hold
it to the loop over every disc and every outline edge: the discs, the owner and both
components bit for bit, the outline distances to within a ulp (1e-12 in the test; the
largest difference seen is 9e-16), and every number bit for bit against the previous
implementation on five cells. The discs
are split by radius into classes, each with its own k-d tree and a certificate
sized to its own largest radius (a few wide discs would otherwise spoil it for
every point); the outlines start from the disc distance already found, so an
outline is measured only for the points it could still be nearest to. Measured
on the 200,000-vertex Voxel remesh of each cell (2026-10-15, a machine at load
7 to 14), the whole error map took 160 s before and 4.3 s now on RC1 c606
(8,260 discs), 412 s and 10.2 s on Inferior c1178 (2,304 discs, 130 outlines),
101 s and 3.5 s on RC2 c74420, 21 s and 4.1 s on Inferior c19698, and 93 s and
53 s on RPC1 c2628 (3,978 outlines of 361 edges: 710 million point-edge pairs,
all exact), every distance identical to the last bit. The memory a block of point-edge pairs may
take is still bounded by `KERNEL_BUDGET_BYTES`. `hypot(radial, axial)` is
`dist` to the last bit except
where the k-d tree's own distance to an outline vertex was the nearer of the
two, which it can be by one ulp.

`deviation_map` keeps those distances per point and now says how the error
arises as well, all of it additive. Per vertex: `axial_um` (the gap along Z to
the nearest annotation; the sideways part is `sqrt(vertex_um**2 -
axial_um**2)`) and `owner` (which annotation). Per displayed rim point:
`rim_enclosed`, whether the mesh encloses it (`enclosed_points`), which is the
render's side: enclosed means it is fatter than that annotation there, outside
means it stops short; `bias` sums it up with positive meaning the render claims
more than the annotations support. A surface has no side, since a disc is flat.
Weighted by the area each vertex stands for (`vertex_areas`, a third of its
faces, so a finely meshed region counts for its area and not its vertices):
`surface` (mean, 0 to 100 percent quantiles by `weighted_quantiles`, a
40-bin histogram), `annotations` (a row per annotation: its share of the
surface, the error over it, the distances from its rim), `z_profile` (the same
in 1 µm bins along the cell), `spots` (the places that carry the most excess
volume, no two within 2 µm) and `floor_um` (the error even a perfect render
shows between sections). On the Voxel remesh of the five cells above the
area-weighted mean is within 1% of the sampled `surface_um` of
`annotation_deviation` (c606 0.7594 against 0.7582, c1178 0.3928 against 0.3926,
c19698 0.1096 against 0.1087, c74420 0.2569 against 0.2549, c2628 0.2007
against 0.2021); it is the
mean of the *painted* field, so on a render whose vertices lie on the
annotations (the tube pathways) it is lower than the sampled one, which also
sees the sides between them (Inferior c943, Original: 0.18 against 0.34).
`annotation_targets(neuron)` in the service facade gives the Location IDs and
sections in the order a measurement numbers its annotations.

### Signing a difference: `mesh_difference(sign=, both=)`

`mesh_difference(a, b)` is the distance from every vertex of mesh A to mesh B's
surface (the k-d tree over a dense sample of B), signed: positive where A lies
outside B. `sign` says how the side is decided, and only the sign differs:
the magnitudes are the same bits either way.

* `sign="normal"` (the default; every number is what it was before the argument
  existed, pinned bit for bit by a test that carries the old algorithm) takes
  the sign of B's outward normal at the nearest surface sample. That is right
  wherever the nearest sample is on B's outer surface and wrong where it is on a
  piece buried inside B, which a body assembled from interpenetrating closed
  pieces (the `original` pathway) has.
* `sign="enclosure"` takes it from whether B's solid encloses the vertex
  (`enclosed_points_open`, built on the ray count that already measures the
  hidden surface and the rim sides): negative inside, positive outside. It
  reports the counted stage "difference: which side of mesh B" (and "... (open
  ends)" for the vertices that need the winding number, below) and obeys
  `KERNEL_BUDGET_BYTES` like the other kernels. B must be wound outward, which
  every builder here guarantees. B need **not** be closed, and the builders do
  not guarantee that: the Original pathway (and the tube, adaptive, wrap and
  Voxel remesh orig builds) has open ends, 3,282 boundary edges on c1178, 3,028
  on c21768 and 17,098 on c606 after welding vertices by position (the Voxel
  remesh has none). On an open surface an upward ray can slip through a gap, so
  the bare count of a vertex near an open end depends on the direction of the
  ray, and what an open surface *encloses* has no single answer.

**What the enclosure sign is, and what it is measured against.** The measure is
independent of every rule it judges: the *generalized winding number* of B as it
is (Van Oosterom and Strackee: the sum over B's faces of the solid angle each
subtends at the vertex, as a share of the sphere; 1 inside a closed surface, 0
outside it, a fraction near an opening), with "inside" meaning above one half.
It uses no ray. It was computed by brute force on every vertex of the Voxel
remesh where any rule below, cast along any of four directions, disagreed with
another (1,712 vertices on c1178, 2,865 on c606, 10,526 on c21768). "Wrong"
counts the vertices where the winding number is confident (under 0.2 or over 0.8)
and the rule says the other thing, as a share of the 200,000 vertices. Vertices
where every rule agrees were not examined, so a vertex that all of them get wrong
is not counted: each figure is a lower bound.

| Rule (a Voxel remesh against the Original, 2026-10-16) | wrong, c1178 | wrong, c606 | wrong, c21768 | most vertices whose side changes with the ray direction (c1178 / c606 / c21768) |
|---|---|---|---|---|
| bare upward ray count | 0.074 % | 0.091 % | 0.789 % (1,578) | 1,041 / 1,866 / 4,624 |
| one fan over every open piece, then the count (the previous revision) | 0 | 0.0005 % (1) | **0.67 %** (1,347) | 0 / 0 / 1 |
| majority of four bare rays (+z, +x, +y, -z), four times the work | 0 | 0.0020 % (4) | 0 (75 more where the winding number is not confident) | 0 / 10 / 144 |
| the hybrid proposed in review: the fan's count, and where that differs from the bare count the majority of the four bare rays | 0 | 0 | 0.030 % (60) | 12 / 16 / 482 (a variant that keeps the upward answer on a tie) |
| **this revision**: fans over round pieces, the winding number where a fan would be a sheet that is not there | 0 | 0.0005 % (1) | 0.0005 % (1) | 0 / 0 / 1 |

(The last column is the largest number of vertices whose side changes when the
ray is cast along +x, +y or -z instead of +z, the rule applied in that frame. For
the majority and the hybrid it is the rule's own asymmetry: its four rays are not
the same four after a turn.)

Reading the table:

* **The first fix was direction independent and wrong on c21768.** One fan to
  the centroid of every open piece makes the surface closed, so every direction
  counts the same. On c21768 one piece of B's boundary is a single simple cycle
  of 1,642 edges, 382 µm round and 18 x 13 x 42 µm across, enclosing 43 µm² (a
  thin open slot), and its fan is 1,563 µm² of surface B does not have: 37.8 %
  of B's own area (4,151 µm²). No other piece comes near (c1178's fans add up
  to 0.13 % of B's surface, c606's largest is 438 µm², 1.4 %). The fan changed
  the upward count of 2,914 vertices; the winding number sides with the fan on
  1,597 of them and with the bare count on the other 1,317 (|d| median 0.11 µm,
  up to 0.44; winding number about 0.015 there). The claim that the bare upward
  count "had been wrong on 1.46 % (c21768)" took the fan's answer for the truth
  and is withdrawn: against the winding number the bare count is wrong on 0.79 %
  and the fan's on 0.67 %. On c1178 and c606 the fans are as good as anything
  (0 and 0.0005 %) and the bare count is 0.07 to 0.09 % wrong.
* **A vote of bare rays is neither.** It depends on the direction (its four
  rays are not the same four after a turn: up to 144 vertices of c21768), it ties
  on the lumen of an open tube (all 401 points tried had two rays leave through
  the open ends and two cross the wall), and used only where the fan and the bare
  count differ it is wrong on 60 vertices of c21768, every one a vertex where
  the two agree on a wrong answer.
* **This revision's rule** closes a boundary piece with its fan only where the
  piece is *round*: the quotient 4 pi A / L² (A the fan's area, L the length of
  the boundary; a circle is 1, a 24-gon ring 0.99) is at least 0.25
  (`CAP_ROUNDNESS_MIN`). Of the 1,912 pieces on the three cells 1,911 are 0.35
  or more (the small degenerate rings of the Original pathway are 0.35 to 0.52)
  and the slot is 0.13; Inferior c19698's Original and Tube builds, which were not
  used to choose it, have none under 0.35. Any other piece is closed too, to make the surface
  closed and its count an integer from every direction, but its fan is not
  believed: a vertex it could reach is placed by the winding number of B as it
  is, which is the count of the closed surface minus the solid angle of the fans
  that were not believed. That subtraction is exact and is only worked out for the
  vertices such a fan could change (a ball about the fan, a ray crossing it up to
  `FAN_LAYERS` = 3 times the same way: 52 % of c21768's vertices, and none on
  c1178 or c606, whose pieces are all round, so they cost what they did). It
  put 1,380 vertices of c21768 on the other side from the fan's count
  (`MeshDifference.sign_overruled`, `DiffResultDTO.sign_overruled`, and
  `sign_overruled_b` for the reverse field).
* **What is left.** Two vertices, each 0.0005 %. On c21768 it is a vertex 0.03 nm
  from a face of B, where the side is a rounding either way (it is also the one
  vertex whose side changes with the ray direction). On c606 it is a vertex 0.29
  µm from B under the fan of a small piece that passes the roundness test (0.51)
  but encloses nothing (42 edges, 19 µm round, vector area 0.04 µm², a fan of 15
  µm²): a ribbon folded flat, of which every cell has some. A second test, the
  fan's area against the area its boundary encloses, would catch it and was not
  added: it would send all the other flat ribbons through the slower path, and
  the fans of c1178 (they among them) are right on all 148 vertices they
  changed.
* **What it does not mean.** Where the winding number is itself between 0.2 and
  0.8 (3 of the vertices examined on c1178, 12 on c606, 890 on c21768) the open
  surface does not say which side the vertex is on, and above one half is a
  convention: no rule is right or wrong there. The share of c21768's vertices
  inside B is 5.80 % by the bare count, 5.63 % by the previous fan and 4.97 %
  now, so a legend that reports it moves by 0.7 points (12 %) between the two
  revisions.
* The caps decide only the sign: every |d| is the same bit as before, and
  `sign="normal"` and the plain `enclosed_points` are untouched (the latter
  bit-identical on all 200,000 vertices of the three cells, bare and capped). One
  real weakness of `enclosed_points` was found and left alone, because its numbers
  are pinned: it keeps a triangle if the squared double area of its projection is
  over 1e-18, which cancellation noise passes, so a triangle exactly edge-on to the
  ray (a cap's horizontal disc seen from the side) "hits" rays 0.02 µm off its
  plane (10 of 5,114 points of a test fixture counted 0 or -1 instead of 1 along
  one axis). The open-surface count skips triangles whose projected corner has a
  sine under 1e-6 (`SLIVER_SINE`); the plain count's sideways dependence on that
  noise is unmeasured. (The hidden-surface and rim-side tests of
  `annotation_deviation` and `deviation_map` call `enclosed_points` on the bare
  mesh, as they always did; their numbers are pinned and are not capped here, so
  the same dependence on the ray direction applies to them on an open pathway,
  unmeasured.)

Voxel remesh against Original, 200,000 vertices of mesh A, a machine at load 13
to 15 (2026-10-16; the extra memory is the process's resident peak above the
loaded meshes): the vertices signed differently by the two rules are 22.4 % on
Inferior c21768 (the normal puts 76 % of the Voxel remesh's vertices outside
the original, the enclosure 95 %; it was 21.8 % with one fan over every piece),
11.6 % on Inferior c1178 and 6.1 % on RC1 c606. Enclosure, before this revision and after: c1178 3.8 s (+369 MB) and 3.7 s
(+403 MB), c606 9.6 s (+554 MB) and 8.5 s (+591 MB), c21768 10.5 s (+537 MB) and
24.2 s (+759 MB), the difference being the slot's 104,000 vertices worked out
against its 1,642 fan triangles; with `both`, c1178 11.1 s and 12.7 s, c21768
19.7 s (+677 MB) and 32.5 s (+692 MB). The solid-angle blocks are bounded by
`KERNEL_BUDGET_BYTES` at 1,300 bytes a pair (the live temporaries are 152; the
resident size grows by about 1,200 a pair with 64-point blocks and 1,500 with 613,
and small blocks are the faster). The times move with the load by a factor of
two (at load 6 the previous revision took 3.6 s, 1.9 s and 3.3 s one way on c21768, c1178 and c606), and the
cells that are long in Z cost more: RPC1 c2628 (z 33.6 to 89.4 µm, Original
364,400 faces) took 21.0 s (+548 MB) one way and 44.9 s (+721 MB) with `both`
under the previous revision, and was not measured again.

`both=True` also measures B against A by the same rule
(`MeshDifference.distance_b_um`, positive where B lies outside A), at twice the
cost; `add_reverse` is that second pass alone, which the service uses to answer
"both ways" from a held one-way result (and the reverse, a one-way request from
a both-ways result, costs nothing). `MeshDifference.stats_area` (and `stats_area_b`) repeat the statistics
weighted by the surface area each vertex stands for (`vertex_areas`) instead of
counting every vertex once: on a mesh meshed ten times as finely in one place,
the vertex figures say a tenth of the surface is off where half of it is. On
c606 the mean |d| is 0.223 µm by vertex and 0.343 by area, the 95th percentile
0.558 and 1.546.

### The comparison report

`sbfsem.export.compare_pdf.write_compare_report(spec, source)` writes Render
compare's report: a color PDF of renderings of one cell held to its annotations
and to each other, both ways. `spec` (`ReportSpec`) names the renderings
(`ReportRendering`: label, color, pane, layer and the options it was built
with), the pairs (indices), the panes (`ReportPane`, with a picture of each),
the viewpoints (`sbfsem.geometry.raster.View`; `NAMED_VIEWS` holds XY1, XZ1 and
YZ1), the sign rule, the paper and the volume's Z scale (`z_scale_nm`, which
turns a Z into a section). `source` (`ReportSource`) gives the surfaces and the
measurements by index: `mesh(i)`, `deviation(i)` (a `RenderingDeviation`: the
`deviation_map` and the `annotation_deviation` summary), `difference(a, b)`
(`mesh_difference(..., both=True)`), `annotations()` and `annotation_names()`
(Location IDs and sections). The web service's source reads its own caches
(`SbfsemService.compare_report`); a test builds meshes and passes them in.

Renderings may be of different copies of the cell (registrations: the
transforms on or off, a correction mode): `ReportRendering.copy` and
`registration` name each one's, and `SbfsemService.compare_report(neuron,
spec, copies)` takes the copy each was built from. Its source then builds and
measures each on its own copy, holds a pair across two to each its own
(`_cached_difference(..., neuron_b=)`), and also answers `annotations_of(i)`,
`annotation_names_of(i)` (the section cuts and worst places of that copy) and
`registration_facts()`, one entry per copy: what its transforms and correction
moved of the cell's own annotations and the link steps at each stage
(`sbfsem.analysis.shifts.shift_summary(neuron, "both")`), its coherent sections,
and the curve fit's pulls at the default ratio (`correction_checks`). With two
or more copies the plan holds a "Summary: the registrations" page after the
renderings' (`Plan`; the page's `estimatePages` counts it too).

What a registration does to what each section image shows is
`sbfsem.analysis.registration_quality`: `section_distortion(neuron)` (pairs of the
cell's own annotations on one section within `RADIUS_UM` = 5 µm of each other at
their stored positions: the change `|m_i - m_j|` of their offset, for their moves
`m` by the transforms and the correction) and `synapse_drift(neuron)` (each synapse
against the cell's nearest annotation on its section within 5 µm: the change of
their offset), each summarized by `offset_stats` (how many, median, p95, largest,
how many over `OVER_UM` = 0.25 µm). Annotations drawn on one image are offset from
each other as the image shows, so a registration that moves sections as a whole
(the transforms, the residual field) keeps both near zero, and one that moves each
annotation of the cell on its own (the curve fit, which leaves the synapses where
they were drawn) does not: the cost side of the link steps, which the fit lowers by
construction. `sbfsem.service.facade.registration_quality(neuron)` gathers them with
the moves, the link steps at the copy's positions, the coherent sections and the
curve fit's pulls (the report's registrations page and `GET .../registration-quality`);
`tools/registration_quality.py` runs it over every registration of many cells.

```python
from sbfsem.export.compare_pdf import ReportRendering, ReportSpec, write_compare_report
from sbfsem.geometry.raster import NAMED_VIEWS

spec = ReportSpec(cell=943, source="i", volume="Inferior Monkey", z_scale_nm=70.0,
                  renderings=(ReportRendering("Voxel remesh", "#ffd400", 1, 1,
                                              {"method": "cylinder", "pathway": "voxel2"}),
                              ReportRendering("Original", "#56b4e9", 2, 1,
                                              {"method": "cylinder", "pathway": "original"})),
                  pairs=((0, 1),), views=(NAMED_VIEWS["xy1"], NAMED_VIEWS["xz1"]))
report = svc.compare_report(svc.neuron(943, "i"), spec)      # a ReportFile
open(report.filename, "wb").write(report.pdf)
```

The numbers are `sbfsem.analysis.compare_report`'s: `surface_facts` (triangles,
area, divergence volume, open edges, visual bodies, creases), `field_stats`
(one direction of a difference over the surface, every vertex weighted by the
area it stands for: mean |d|, RMS, signed mean, percentiles, the share within
0.05 to 2 µm, the share outside, ∫ d dA, a signed histogram, a profile along Z
and the places that carry the most of ∫ |d| dA) and `pair_result` (both
directions, the Hausdorff distance, the symmetric mean distance and the Z
where the two disagree most). For two closed surfaces close together,
½(∫_A d dA − ∫_B d dA) is the volume between them to third order: two concentric
spheres of 10 and 9.5 µm give 597.5 against the exact 597.4 µm³, where each
one-sided integral is 5 % off.

The pictures are `sbfsem.geometry.raster`'s: an orthographic z-buffer in NumPy
(`rasterize`, exact coverage at twice the size, averaged), the viewer's views
and Flip Z, one frame per viewpoint for the whole report (`frame_around`), and
`section_segments` for a cut at a Z. A 327,000-triangle sphere draws in 0.37 s;
its drawn area is π r² to 0.01 %. The pages are matplotlib figures
(`sbfsem.export.pdfpage.Page`: inches from the top-left, measured word wrap,
tables and charts), written in two streams (the body as each rendering and pair
is measured; the front matter last, when every number is known) and put
together by pypdf (`assemble`), which adds the bookmarks and links and stores
the pictures as JPEG. Every page number is known before anything is drawn
(`Plan`). The addendum is `sbfsem.export.compare_addendum`. A rendering or pair
that fails gets pages that say why; `progress.check_stop()` between the steps
lets the job's cancel stop it (`sbfsem_web.jobs.STOPPABLE_KINDS`).

### The browser's halves of the error views

Four modules hold what the browser does with those fields, so the panes, the
legends, the error inspector and the Section view can share it. Two are pure
(`errorpaint.js` and `levelset.js`: no DOM, no three.js); the other two
(`errorinspector.js` and `slice.js`) have a pure half and a DOM half. Each loads
under node and has a test in `tests/js/`; the DOM halves are tested against a fake
DOM there, and drawn for real by `tools/check_inspector.py` (headless Chrome, a
running server with Inferior c1178, screenshots in both color schemes with
`--shots`).

`static/js/errorpaint.js` turns a field into colors and numbers. Its error
ramp (`errorColors`) and signed ramp (`divergingColors`) are copies, unchanged,
of the functions Render compare has always painted with. `compare.js` still
defines its own and does not import these yet: until it does there are two live
copies, and the only thing that keeps them the same is the test, which holds a
verbatim copy of the old ones, compares the buffers byte for byte over every
scale and value it can think of, and also runs `compare.js`'s own. Around them:
the lateral part of an error (`lateralValues`,
`sqrt(d² - axial²)`, with `errorValues` choosing total, lateral or axial); the
threshold paint (`thresholdColors`: gray below the threshold, the ramp
starting over at it and reaching red at the scale; with the threshold at or
above the scale the ramp has no width and every vertex at or above the
threshold is red, the one exactly at it too); the area-weighted numbers
(`vertexAreas`, `weightedQuantiles`, `quantileTable`, `areaStats`, `areaHistogram`),
pinned against the Python's `vertex_areas`, `weighted_quantiles`, `AreaStats` and
surface histogram on the same inputs; `percentileValue` and `percentileOf` for a
slider that speaks in percent of the area; `histogramModel` and `legendModel`,
the numbers a histogram and a legend are drawn from; `classifyOverlap`
(inside, shared with, outside the other surface, by a tolerance that defaults to
twice the sample spacing) with its colors and area shares; and `filterRims`
(the rim points the render encloses, or does not). On a 200,000-vertex mesh a
threshold repaint takes 3 ms and the one-time quantile table 49 ms.

`static/js/levelset.js` is marching triangles on a per-vertex scalar. With a
coordinate as the field it gives the contour of a surface at a section
(`buildSliceIndex` bins the faces along Z once; `sliceContour(index, z)` reads
the one bin); with a signed field it gives the zero line (`zeroLine`: where
mesh A crosses mesh B). A vertex at or above the level is on the positive side, with
no tolerance, so a closed surface gives only closed contours without nudging
the level, and a cap lying exactly on a section's plane is cut along its rim
**if it is the top of a stack**. A bottom cap, with the wall above it, is
wholly on the positive side and gives an empty contour: on c1178's closed-curve
surface (648,012 vertices, 4,856 vertex heights) the level of the lowest vertex,
99.855 µm, gave no segment by that rule and one closed loop of 370 by the other,
the only empty one of the 4,856 exact vertex heights. That surface has one stack.
A cell with a second piece, or a branch that begins at a section above the
lowest one, has a bottom cap at every such section, and each is empty by the
default rule too (two closed prisms, z 0..2 and 5..7, with caps on their
section planes: 0 segments at z = 5, where the first revision's lowest-vertex
rule also gave 0). `side: "below"` (a vertex exactly on the level is not
positive) cuts the bottom caps and leaves the top ones empty;
`sectionContour(index, z)`, at a level that is the height of a vertex of the
faces it looks at, takes the union of the two rules and keeps a segment that both
find once (compared by position, both ends within `JOIN_UM` and in either order,
so a rim repeated along a seam is not drawn twice: a ring in the middle of a
stack, or a top cap and a bottom cap on one ring, is one outline, and a narrower
prism that begins on the same plane adds its own). So every
section has its outline at both ends of every stack, wherever in the surface the
cap is, and between vertex heights it is `sliceContour`, segment for segment,
without the second rule being run (the level is compared exactly: a section
height that misses a cap's plane by a rounding has no cap to cut). The default is
unchanged.
`contourLoops` joins the segments into curves by position, not by vertex index,
so a surface repeated along a seam still comes out in one piece, and a vertex
that is a hair off the level (a real mesh's float32 rounding, 1e-6 µm) leaves
pieces that join. The result carries the float32 segment buffer
`Renderer3D.setLines` takes. From the same triangles, by the same rule, it can also
carry along another per-vertex field interpolated at both ends of every segment
(`values`: the error) and the side of every segment the solid is on (`axis`: the
face's outward normal, projected into the plane across that axis; `contourLoops`
then returns a closed loop in the direction its segments say, counterclockwise about
a body and clockwise about a hole): what the Section view needs, so it does not
repeat the marching. On c1178 (400 cuts at the section heights and
between them) the closed-curve surface (119,644 faces) gave 416 loops and the
Voxel remesh (400,000 faces) 1,840, every one closed; a cut takes 0.03 to
0.04 ms through the index plus 0.1 to 0.2 ms to join, against 1 to 4 ms to
scan every face, and the index builds in 6 to 12 ms.

`static/js/errorinspector.js` is the content of the error inspector drawer: the
numbers the error-map job sent, laid out. Its first half is pure and loads
under node: `biasModel` (the share of the tested rim points the render
encloses, "fat", and the share it does not, "short"), `commonEdges` and
`rebinHistogram` (layers measured to different scales share one axis: the
farthest reach, 40 equal bins, each layer's bins spread over the new ones by
overlap, which keeps the area), `thresholdReadout` and `thresholdAtPercent`
(a threshold in um and its percentile in the area-weighted quantile table, one
number two ways), `zProfileSeries` (a point for each bin that has something to
say: the surface's mean or 95th percentile where it has area, the rim distance
where an annotation sits), `annotationRows`, `sortRows` and `filterMinArea` (the
annotation table as rows, sorted with the missing values last and IDs in numeric
order, and the minimum-area filter, 0.02 % of the surface by default: 1,434 of
the 2,434 annotations of Inferior c1178's Voxel remesh), `parseEdges` and
`radiusClasses` (the classes by radius, edges 0.25, 0.5 and 1.5 um by default),
`spotSummary`, `improvement` (two tables joined by Location ID: which render is
closer to each annotation, by the error over the surface it owns or the distance
of its rim, with a tolerance of 10 nm below which an annotation is unchanged; the
headline gives the change per annotation and by area, which can read the other way)
and `ringColorMap` (the colors a ring of each annotation can take, by rim error,
surface error or the change between two layers, as a `Map` of `[r, g, b]` for
`Renderer3D.setDiscs`'s `ringColors` and `outlineColors`: the ramp's own numbers, in
linear light as the renderer takes an array, so a ring has the color of the surface and
of the rim points for the same error; a CSS string would be read as sRGB and differ).
The classes are summed here from the
annotation table, so the edges you type take effect at once; they add up to the
server's totals (the area to 1, the mean error to `surface.mean_um`, the excess
to `surface.excess_um3`): on c1178's Voxel remesh within 2.3 parts in 100,000,
the six decimals the shares are sent with. The second half is `ErrorInspector`,
the DOM around them: a bias bar per layer, the histogram with a threshold handle
that drags (the chart is not redrawn while it moves, so the pointer stays on it,
and a redraw in the middle of a drag ends it) and takes the keyboard (a percent of
the area a step, Shift five, Escape takes it off; a "Set at 90 %" button is the way
in from the keyboard while there is no threshold, and a click on the chart leaves
the keyboard on the grip), its bars, floor and percentile marks are `errorpaint.js`'s `histogramModel` laid on the axis the
layers share, and what lies past the end of that axis is said (a "7.4 % beyond 3.58 µm"
label, and the marks beyond it as one mark at the end naming each) and not dropped, so
p98, the legend's scale, is always marked; the profile along Z with the layers overlaid in their colors and the section
of the Section view marked, the worst places (the header says what the top ten
hold together), the annotation table, the classes and the improvement table,
charts as inline SVG in the style of `charts.js`. Every Location ID is shown, and
handed to a callback, with its volume (IDs are numbered per volume), and a result
without the newer fields still draws what it has. It calls back through
`onThreshold({ value, percent, layerId } | null)`, `onFlyTo({ layerId, volume,
annotationId, section, xyz, source })`, `onSection(section, { layerId, volume,
annotationId, source })`, `onFocus` and `onEdges`; `setLayers`, `setFocus`,
`setThreshold` and `setSection` change it from outside without calling back. It
carries its own stylesheet (`INSPECTOR_CSS`, added once by
`installInspectorStyles`), in the app's tokens, so both color schemes follow the
page; the cards stack below a drawer width of about 1100 px and sit three across
from there. On the real result of c1178 (Voxel remesh and Original, 2,434
annotations each) building the whole drawer takes 10 ms.

`static/js/slice.js` is the Section view: one cut through the cell, drawn flat on
a canvas with a pan and zoom of its own (so no 3D camera moves with it). Per
visible surface layer it draws the contour where the section's plane cuts it
(`contourSegments` is `levelset.js`'s `sectionContour` itself, carrying the error to
both ends of every segment and the side of the segment the solid is on; it used to
repeat the marching rule, and the copy fell behind when the rule changed),
colored by the error ramp when the layer is painted by error; the
annotation discs and traced outlines of that section, the nearest annotated
sections on either side ghosted; and, for each annotation, how far its rim lies
from the contour in the section's plane. That gap is positive where the contour
encloses a rim point (the render is fatter than the annotation there) and
negative where it does not (the render stops short): the sign is a signed count
over the contour's segments and the distance the nearest segment, both read from a
uniform grid (`buildSegmentGrid`, `nearestOnGrid`, `insideContour`, the last two
tested against a scan of every segment). Every segment runs with the solid on its
left (its face's outward normal, projected into the plane, says which way), a ray to
+X adds one where a segment crosses it going up and takes one where it goes down,
and a point is inside where the sum is positive: the number of bodies round it,
as the server's `enclosed_points` counts, and not a parity, because the assembled
pathways (Original and its kin) are unions of interpenetrating closed pieces, and
a point inside two of them has an even number of crossings to its right. The section number maps to Z
by a line fitted to the annotations' own Z (`fitZMap`): 90 nm for Inferior c1178,
a residual of 7e-14 um, and 70 nm for RC1 and RC2, never assumed. A circle's
stroke is the gap on the signed ramp, its set's color a halo under it, its mean
gap a number beside it, and a hover names it with its volume and the gap to each
layer. X runs right and Y down, as in Viking's section image. Keys act only while
the canvas has the keyboard (it listens on itself, never on the document): Up and
Down the next and previous annotated section (Shift: ten of them), Right and
Left one section (Shift: ten), Page Up and Page Down ten, Home and End the first
and last annotated section, `+`, `-` and `0` the wheel's and the double-click's
zoom and fit for a keyboard alone; the wheel zooms about the pointer, a drag pans,
a double-click fits the view to the section. A view nobody has panned or zoomed
follows the content from section to section; one that was keeps its framing.
`setArrows((section) => [{ x0, y0, x1, y1 }])` is the hook for the Position shift
vectors (`buildArrowIndex(prepareField(nodes, pair))` gives them), and `nodesAtStage`
gives the nodes at their stored, registered or corrected position for two
annotation sets side by side. On Inferior c1178 (627 annotated sections, the
Voxel remesh of 400,000 faces) every section's cut closes (2,464 closed loops, none
open, no degenerate segment, no empty section) and the inside test puts 45,730 of the
45,732 rim points the server displays on the side the server's 3-D test puts them
(99.996 %); the share of an annotation's rim the contour encloses differs from the
share of its displayed rim points the server's test encloses by a median of 0.031
(90th percentile 0.12, 95 % within 0.15; 2,434 annotations; the two sample
different points of a rim, so the shares differ a little though nearly every point
agrees; all 627 sections with their 45,732 rim points in 360 ms); the face index
builds in 23 ms and a step to another section takes 0.6 ms of drawing commands. Those
figures are the Voxel remesh's.
The Original of the same cell is open (709 open loops among 10,662 closed ones, 1,883
of those clockwise: the cuts of holes and of notches between pieces), and there the signed
count puts 45,343 of the same 45,732 points (99.15 %) on the server's side (the even-odd
rule it replaced, 37,402: 81.8 %), and the annotation shares differ by a median of 0.054 (90th percentile
0.156, 88 % within 0.15; the even-odd rule, 0.103, 0.307 and 63 %). Of the 389 points on
which they differ, 344 are points where the count itself depends on the direction of the
ray (a ray to +X, -X, +Y and -Y do not agree), which is what an open end does: an open
surface does not say which side of it a point is on, and the server's own upward ray is
no more right there than the section's.

## IPL depth

The boundary surfaces are fitted once and cached per volume:

```python
from sbfsem.analysis.boundary import load_ipl_boundaries
from sbfsem.analysis.stratification import ipl_depth

boundaries = load_ipl_boundaries("i")     # ~1 s, then cached
depth = boundaries.micron2ipl(c.get_cell_xyz())   # 0 at INL, 1 at GCL

profile = ipl_depth(c, boundaries=boundaries, num_bins=20)
profile.stats.median, profile.mode, profile.n_omitted_outlier

# Every annotation, with its depth and whether ipl_depth counts it (and why not:
# "soma", "outside surfaces", "outlier"); the counted depths are profile.depth.
from sbfsem.analysis.stratification import annotation_depths
table = annotation_depths(c, boundaries=boundaries)

# Along the layers' normals instead of along Z (2.31.0): every function above takes
# method="column" (the default, MATLAB's) or method="normal".
d = boundaries.depth(c.get_cell_xyz(), "normal")   # IPLDepth
d.fraction, d.depth_um, d.thickness_um             # fraction = depth_um / thickness_um
ipl_depth(c, boundaries=boundaries, method="normal").meta["method"]
table = annotation_depths(c, boundaries=boundaries, method="normal")  # + depth_um, ipl_thickness_um

# The volume's IPL thickness along the normals and along Z, where it thins, each
# layer's tilt and the angle between their normals, and a 60 x 60 map.
from sbfsem.analysis.boundary import thickness_summary
thickness_summary(boundaries)["normal_um"]["median"]
```

`"column"` evaluates both fits at the point's own X/Y; `"normal"` takes the
nearest point of each fit (`BoundarySurface.nearest`, a Newton solve on the same
heights, with slopes and curvatures from the bicubic spline through the nodes),
so its depth is the distance from the INL, its thickness the sum of the two
distances, and its fraction their ratio. A point whose nearest point on a layer
lies beyond that layer's fitted rectangle gets NaN that way. `clip_mesh_by_stratification`,
`flatten_render` and `strata_cdata` take `method` too; DEVIATIONS.md §5.17 has the
measurements.

`sbfsem.export.stratification` writes the Stratification tab's files from these:
`stratification_pdf` (the chart as vectors, then optional data pages) and
`stratification_xlsx` (a Summary sheet and a sheet per cell); `ipl_ticks` and
`labeled_ticks` are the axis rule `charts.js` uses (`iplTicks`, `labeledTicks`).

NaN means the point falls outside the fitted surfaces, or the two surfaces
coincide there. It is not silently filled in.

## Analyses

```python
from sbfsem.analysis.dendrite import (
    dendritic_field_hull, surface_area, tortuosity,
    dendrite_diameter_by_soma_distance, open_ended_branches,
)
from sbfsem.analysis.synapse import ie_ratio, synapse_soma_distance

tortuosity(c, a, b, dim=2)          # XY only, suppressing Z jitter
dendritic_field_hull(c).area
dendrite_diameter_by_soma_distance(c, bin_width=5.0)
open_ended_branches(c)              # unfinished branches
ie_ratio(c).ratio                   # numInh / numExc
```

Some analyses and exports are library-only: no route or control of the web app
reaches them (audit 2026-10-15). `sbfsem.analysis.drp` (the density recovery
profile), `synapse_soma_path_length`, `synapse_direction` and
`reciprocal_synapses`, `stratification.branch_stratification`,
`dendrite.branch_diameter`, `sbfsem.network.metrics`, `sbfsem.export.pajek`,
`sbfsem.export.images` and `mesh_formats.read_mesh` are for scripts and
notebooks. The routes that are reached set fewer parameters than the functions
behind them take: the Skeleton check always runs with `check_skeleton`'s
thresholds (`spoke_ratio`, `jump_um`, ...), and the SWC routes never pass
`soma_fraction`, `axon_ids`, `start_node` or `has_soma` to `swc_table`.

## Connectivity

```python
from sbfsem.io.odata import ODataClient
from sbfsem.network.links import all_linked_neurons, count_linked_neurons
from sbfsem.network.graph import network_from_links, degree_table

client = ODataClient("i")
links = all_linked_neurons(c, client)
count_linked_neurons(links)

# A link table has a row per synapse of this cell: a partner that Viking does
# not link (NeuronID blank) is not an edge, and a "post" row is a synapse this
# cell receives, so the partner is its source.
partners = links.dropna(subset=["NeuronID"])
sent = partners["Direction"] == "pre"
network = network_from_links(partners.assign(
    SourceID=partners["NeuronID"].where(~sent, c.id).astype(int),
    TargetID=partners["NeuronID"].where(sent, c.id).astype(int),
))
degree_table(network)

# The getAllLinkedNeurons report: positions in microns, either ordering,
# and the files the lab's script wrote.
from sbfsem.network.links import link_report, links_to_csv, links_to_xlsx
table = link_report(c, links, order="type")
open("c943-linked-neurons.csv", "w").write(links_to_csv(table))
```

### Path queries (Graffinity)

`sbfsem.network.paths` implements the connectivity matrix and intermediate
node table of Kerzner et al. (EuroVis 2017) over a neuron-level link graph
that is grown one hop at a time from Viking:

```python
from sbfsem.network.paths import (
    LinkGraph, PathQuery, find_paths, connectivity_matrix,
    intermediate_node_table, neuron_edges_from_structure_links,
)

graph = LinkGraph()

def expand(ids):                       # called once per hop with the frontier
    rows = client.fetch_links_by_parent_structures(ids)
    graph.add_edges(neuron_edges_from_structure_links(rows))
    graph.mark_expanded(ids)
    for nid, info in client.get_structures_brief(graph.unlabeled()).items():
        graph.set_node(nid, label=info["label"], type_id=info["type_id"])

query = PathQuery(hops=2, direction="upstream", start_ids=[1178],
                  node_patterns=["", "", ""], edge_patterns=["", "ConvPre"])
found = find_paths(graph, query, expand)
matrix = connectivity_matrix(found.paths)          # rows × cols of path indices
table = intermediate_node_table(found.paths)       # node -> "length:position" -> paths
```

Paths are `[n0, e0, n1, e1, …]` lists (neuron IDs and edge indices), as
Graffinity stored them. `SbfsemService.connectivity_query` wraps this with a
per-volume graph cache and the DTO the web app draws.

## Export

```python
from sbfsem.export.swc import write_swc
from sbfsem.export.json_v2 import write_neuron_json, read_neuron_json
from sbfsem.export.collada import write_collada
from sbfsem.export.mesh_formats import write_stl, write_gltf

write_swc(c, "c943.swc")
# Options, none the default; the header says what each did.  Lamella chains
# (not with relax=), Bradley & Withers's sphere network, the modal radius in R.
write_swc(c, "c943-options.swc", lamellae=True, sampling="sphere", radius="modal")
write_neuron_json(c, "c943.json")
write_collada({"c943": mesh}, "c943.dae")   # meter=1e-6 by default
write_gltf(mesh, "c943.glb")
```

`write_neuron_json` records what each postsynapse is (`LocalName`, `TagStatus`,
`SuggestedTag` in its synapse rows) and the policy and partners that decided it
(`untaggedPsd`, `psdPartners`), and `read_neuron_json` puts them back: a file
read again types every synapse as it did when it was written. Files from before
2026-10-16 carry the rows' kinds but not the policy, and the kinds win. The
`lastModified` key is the structure row's `LastModified` as Viking labeled it;
nothing reads it back.

## The service facade

If you are building a frontend, go through `SbfsemService`: it caches neurons and
meshes, and returns pydantic DTOs rather than pandas frames.

```python
from sbfsem.service.facade import SbfsemService

svc = SbfsemService()
svc.load_neuron(943, "i")
svc.render_mesh(943, "i", color_by_strata=True)
svc.stratification(943, "i", num_bins=20)
svc.network([943], "i")
svc.synapse_points(943, "i")   # one point a synapse: ID, type, its Unique annotation (GraphApp's dots)
```

What the facade is strict about, because a lenient answer would be a wrong one
shown without a word:

- `method` is `cylinder`, `curvemesh` (`tube`), `closedcurve` (`curve`, `outline`,
  `volume`) or `hybrid` (`mixed`); the aliases are cached as the name they stand
  for, and anything else is a `ValueError` (it used to build a cylinder under a
  cache key of its own). A render option the service does not have is a
  `ValueError` too, from `build_mesh`, `mesh_object` and `mesh_for` alike.
- `load_neuron` answers from the copy it already holds only if that copy was
  fetched with the synapses the call asks for and with the transform it names;
  a cell handed to `add_neuron` (a file) is taken as it is.  A copy it holds, or
  one the disk cache gives it, is checked against Viking first
  (`_viking_newer`: `StructureFreshnessBatch` at depth 0, compared as the
  freshness route compares, at most every `FRESH_CHECK_S` = 30 s per copy) and
  fetched again past the cache when Viking is newer; an offline service, unstamped
  data and an unanswered question keep the copy.
- `locate` answers from the copy named by the request (`requested_copy`), else
  the one loaded last.
- A build process killed from outside (the kernel's out-of-memory killer) fails
  the build it was running with a message that says so; the pool is made anew
  for the next one. `build_processes=0` builds in the calling thread and has no
  pool to lose.
- `mesh_cache_size` (the web app: `SBFSEM_MESH_CACHE_SIZE`) is how many built
  meshes stay in memory; `memory_report()` says what is held.

## Offline work

Every OData response is cached on disk by URL. To work without network access,
point the cache at a directory that already has what you need and construct the
client with `offline=True`:

```python
from sbfsem.io.cache import DiskCache
from sbfsem.io.odata import ODataClient

client = ODataClient("i", cache=DiskCache("cassettes"), offline=True)
```

An uncached URL then raises `ODataError` instead of reaching the network.

### What the disk cache keeps, and for how long

A cell's own rows (its locations, links, children, closed curves) are kept for
good: they are asked again only for a refresh (`client.fresh()`, "update this
cell from Viking"). The lists that grow are not: `ids_by_label` (which also serves
the cone lists, `ConeMosaic.cone_ids`), `resolve_label`, `query_labels` (which the
Graffinity start set reads) and `structures_by_type` (a structure group's list:
the vessels and their adjacencies, and the boundary layers' structures) are asked
again after `ODataClient.LIST_MAX_AGE` (one hour). When Viking cannot be reached
the older answer is returned, and an `offline` client answers from whatever it
holds, however old. A list of several pages is one moment or the other, never
both: its pages are stored together once the whole list is in, and if a page
cannot be had after the one before it was asked again (or the reverse), the older
list is returned whole, from entries nothing has overwritten (a page with no
older answer fails the call, and nothing of it is kept).
`DiskCache.get(url, max_age=)` is the primitive; the cache's
own `ttl` is the default limit and the product sets none. Nothing bounds the
cache's size (3.0 GB in 5,952 files on the development machine, 2026-10-16), and
two writers of one URL no longer share a temporary file.

An `ODataError` carries the HTTP `status` Viking answered with (`None` when
there was none), and its message names it: a structure that does not exist
(404) and a gateway that gave up (502, 504) are told apart.
