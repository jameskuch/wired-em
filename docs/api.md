# HTTP API

The service is self-documenting: `/docs` serves interactive OpenAPI docs and
`/openapi.json` the schema. This page is the summary.

Every route is a thin adapter over one `SbfsemService` method. No analysis or
geometry logic lives in the web layer.

`{source}` accepts a volume name or abbreviation.

## Volumes

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | version, status, and the build queue (`builds`: workers, memory budget, running, queued; `gate_blocked` now, `by_kind` the queued and running jobs of each kind (`mesh`, `deviation`, `diff`), and `gate_waits`, `gate_wait_s`, `gate_wait_max_s` since the service started) |
| GET | `/api/build` | the running build, for a tab to notice a redeploy: `version`, `assets` (a stamp of the served JavaScript and CSS), `instance` (changes with every process start), `started_at`, and `meshlab` (whether this server can honor the MeshLab option). Answers without a sign-in, like `health` |
| GET | `/api/library/{kind}` | every shared entry of a library — `materials` or `gradients` — as saved by any user |
| PUT | `/api/library/{kind}/{name}` | create or replace a shared entry (a material's shading parameters, or a gradient's `stops`: `[[position, "#rrggbb"], …]`); validated, stamped with who saved it |
| DELETE | `/api/library/{kind}/{name}` | remove a shared entry |
| GET | `/api/volumes` | every volume the caller may open (see below): `name`, `display_name`, its capabilities, `viking_name`, `has_transforms` (whether the volume's current section transforms are read here, so its cells can be drawn where Viking draws them now; the viewer offers that switch, and the Position shift pairs that need it, only where it is true), `lab` (`Neitz`, `Marc`; null for the demo), and what a load that names neither gets: `default_registration` (the current section transforms) and `default_correction` (`off`, `neighbor`, `curvefit` or `all`; the web app's is `all` for the Neitz lab's volumes, `curvefit` for the others) |
| GET | `/api/volumes/{source}` | one volume's scale and capabilities |
| GET | `/api/volumes/{source}/search?label=&exact=` | structures matching a label (`type_id` is always null: the query does not select it); a label's `&`, `#`, `%`, `+` and `;` reach Viking's filter percent-encoded, so they match as written; the answer is kept for an hour |
| GET | `/api/volumes/{source}/locations/{location_id}` | where one Viking annotation is: `{id, parent_id, structure_id, xyz_um, viking, radius_um, loaded}`. A loaded cell of the volume that holds it answers (from the copy named by `?copy=`, else the one loaded last; `loaded: true`, in that cell's micron frame), otherwise Viking is asked (`loaded: false`). 404 when neither knows it (offline, only the loaded cells can), 502 when Viking cannot be asked. Location IDs are numbered per volume |
| GET | `/api/viking/session` | who Viking says the caller is, the volumes it shared with them, and the ones it shared that this tool cannot render; reports `enabled: false` when Viking sign-in is off |
| POST | `/api/volumes/{source}/resolve-structures` | parse a structure specification (`{"text": "..."}` — IDs, ranges, and label terms in the Viking export portal's grammar) and resolve the labels to IDs |
| GET | `/api/neurons/{source}/{id}/mesh-job?…` | start (or join) a background build of exactly what the mesh endpoint would return, with the same query parameters; answers `{job_id, status}`. A build is shared, and a finished one kept for ten minutes, only for the same copy of the cell: once the cell is loaded again (other registration settings, an update from Viking) the next request starts a new build |
| GET | `/api/neurons/{source}/{id}/grid-job?…` | have the server keep the whole-cell grid of the render these mesh parameters describe, building it in the background when it has none (move mode asks when it starts); a job of kind `grid` whose result is `{ready, built, seconds}`, `ready: false` with a `reason` for a technique that contours no whole-cell grid. A build of the cell's manually moved copy then contours again only the blocks the moves changed, on that grid |
| GET | `/api/mesh-jobs/{job_id}` | poll a job: `{status, waiting, progress, seconds, detail, kind, failed_stage, mesh, result}`. `?wait=1` holds the poll until a queued or running job ends (done, failed or cancelled) and answers then, or after `wait` seconds (at most 1; more is held to 1) with the job as it is: a client that sends the next such poll as soon as one answers hears of a finished job at once, at about one poll a second. The wait holds no thread on the server; the viewer sends it over HTTP/2 only (a waiting poll holds its connection, and over HTTP/1.1 a browser has six per server). Without `wait`, the poll answers at once, as before — `waiting` says what a queued job waits for (`worker` or `memory`); `progress` is `{stage, done, total}` while it runs (`done`/`total` count blocks, pieces, tubes or outlines, null for a stage without a count; a comparison prefixes its stages with `mesh A:` / `mesh B:`); `kind` is `mesh`, `deviation`, `diff` or `grid`; `mesh` is filled for a finished `mesh` job and `result` for the other kinds; `failed_stage` is the stage that was running when a job ended in `error`. A finished job is kept ten minutes; after that, or after a restart, the poll answers 404 and the request can simply be started again (the work is cached). A client whose `Accept` names `application/x-sbfsem-mesh` (the viewer) gets a finished mesh as binary, the same numbers in 55 to 60 % of the bytes (*A finished mesh as binary*, under Meshes); without it the reply is JSON, as it always was |
| DELETE | `/api/mesh-jobs/{job_id}` | let go of a job: a queued one stops when the last request waiting on it lets go (a running one finishes and lands in the cache) |
| POST | `/api/neurons/{source}/{id}/mesh-deviation-job` | body: one option set; start (or join) the measurement of that rendering against the cell's own annotations; answers `{job_id, status, kind: "deviation", weight_mb}` in milliseconds. The result (poll `result`) has no geometry: `mesh_id` and `n_vertices` name the mesh it is for (the one `mesh` returns, same ids), `vertex_um` is every vertex's distance to the nearest annotation in that mesh's vertex order, then `rim_xyz`, `rim_um`, `rim_stride`, `n_rims`, `scale_um` and `deviation` as for `mesh-deviation`, then how the error arises (all optional: absent, empty or null from a server or cache that predates them). `axial_um` is each vertex's gap along Z to its nearest annotation (the sideways part is `sqrt(vertex_um² - axial_um²)`); `rim_enclosed` is 1 for each displayed rim point the render encloses (the render is fatter than the annotation there) and 0 for each it does not (it stops short), in the order of `rim_um`; `bias` sums that up (`n`, `enclosed_share`, `enclosed_mean_um`, `outside_mean_um`, and `signed_mean_um`, positive when the render claims more than the annotations support). A surface has no side — a disc is flat — so only the rims carry one. `surface` is the error of the surface weighted by the area each vertex stands for (a third of its faces): `mean_um`, `quantiles` (the 0th to 100th percentile, 101 values), `hist` (`edges` 41 values reaching 1.5 `scale_um`, `area_share` of each of the 40 bins, `tail_share` beyond the last edge), `floor_um` (the error a perfect render still shows between sections: half the median gap between annotated sections), `area_um2` and `excess_um3` (area times error summed over the vertices). It describes the *painted* field: on a render whose vertices lie on the annotations (the tube pathways) it is lower than `deviation.surface_um`, which also samples the sides between them. `annotations` has one entry per annotation in every list, the discs first and then the closed-curve outlines in the order they are measured: `ids` (Location IDs, numbered per volume), `sections`, `kind` (0 disc, 1 outline), `xyz` (flat, µm: the disc's center or the outline's centroid), `radius` (µm: the disc's, or the outline's equivalent), `area_share` (the share of the surface whose nearest annotation this is), `surface_mean` / `surface_max` (the error over that surface), `rim_mean` / `rim_max` (the distances from its rim to the render) and `rim_enclosed_share` (null where none of its rim points was tested). `z_profile` is the error along the cell in bins of `step` (1 µm) from `z0`: `area`, `mean`, `p95`, `rim_mean` and `n_annotations` per bin. `spots` are the places that carry the most excess volume (up to 12, no two within 2 µm, largest first): `xyz` (a surface vertex), `section` and `annotation_id` of the annotation that accounts for most of it, `excess_um3`, `share` of all the excess, `mean` and `max` error. The mesh is built first when no request has built it, as the job's own stages |
| POST | `/api/neurons/{source}/{id}/mesh-diff-job` | body `{a, b, samples, sign, both}`; the same for the signed difference of two renderings: `result` has `distance_um` for the vertices of mesh A (`mesh_id`, `n_vertices` name it), the statistics, and `deviation_a`/`deviation_b`. Neither job runs inside a request, so neither can time out at nginx's five minutes. `sign` says what decides which side of B a vertex is on: `normal` (the default; B's outward normal at the nearest surface sample, the numbers this route has always returned) or `enclosure` (negative where B's solid encloses the vertex, positive where it does not, by a ray count over B with its round open ends closed and B's winding number where closing a long thin gap would invent a sheet; the magnitudes are identical). The normal's sign is wrong where the nearest sample lies on a piece buried inside B, which a body assembled from interpenetrating closed pieces (the `original` pathway) has: the two rules put 22.4 % of the Voxel remesh's vertices of Inferior c21768 on different sides against the original (2026-10-16; 21.8 % on 2026-10-15). `enclosure` costs seconds to tens of seconds on a 200,000-vertex mesh (Voxel remesh against the original at load 13 to 15, 2026-10-16: Inferior c1178 3.7 s and +403 MB, RC1 c606 8.5 s and +591 MB, Inferior c21768 24.2 s and +759 MB, 32.5 s with `both`; RPC1 c2628, long in Z, 21.0 s and +548 MB, 44.9 s and +721 MB with `both` under the previous revision and not measured again) and reports its own counted stages ("difference: closing the open ends of mesh B", then "difference: which side of mesh B", then "difference: which side of mesh B (open ends)" for the vertices that need the winding number). B need not be closed, and the `original` pathway is not (c1178 has 3,282 boundary edges, c21768 3,028, c606 17,098): on an open surface the plain upward ray count depends on the ray direction (it changes the side of 0.2 to 2.3 % of a Voxel remesh's vertices against the original, 6.5 to 12 % against the tube build). So each open boundary piece is closed with a fan to its centroid, which makes the count the same from every direction; a fan over a *round* piece (the end of a branch) is believed, and a fan over a long thin one (a crack in the surface) is not, since it is a sheet B does not have: the vertices it could reach are placed by the generalized winding number of B as it is (inside above one half; computed exactly, from the count of the closed surface minus the solid angle of the fans not believed). The caps decide only the sign, never a magnitude. Measured against that winding number, computed apart from the rule, the sign is wrong on 0, 0.0005 % and 0.0005 % of the 200,000 vertices of c1178, c606 and c21768 where the winding number is confident (under 0.2 or over 0.8), against 0.074, 0.091 and 0.789 % for the bare upward count and 0, 0.0005 and 0.67 % for one fan over every piece (c21768 has a 382 µm slot around 43 µm² that gets a 1,563 µm² fan); it is the same along +z, +x, +y and -z except for one vertex that lies 0.03 nm from B's surface. Where the winding number itself is between 0.2 and 0.8 (12 vertices examined on c606, 890 on c21768) the open surface does not say which side a vertex is on, and the sign there is a convention (`docs/backend.md`). The result counts the vertices the winding number put on the other side from the count over the capped surface: `sign_overruled` for mesh A (1,380 on c21768, 0 on c1178 and c606) and `sign_overruled_b` for the reverse field (0 for a closed A). `both: true` also measures B against A, by the same rule and at twice the cost: `distance_b_um` for the vertices of mesh B, with `stats_area_b`. The result says `sign_by`, and always names mesh B (`mesh_id_b`, `n_vertices_b`) so a client holding it can check it is the one compared. `stats_area` and `stats_area_b` hold `mean_abs_um`, `rms_um`, `p95_abs_um`, `p98_abs_um`, `max_abs_um` and `mean_signed_um` weighted by the surface area each vertex stands for (a third of its faces; the statistics beside them count every vertex once, and on an unevenly tessellated mesh say something else); `p98_abs_um` is the color scale they suggest. Every new field is additive: a request without `sign` and `both`, and a client that does not read them, gets what it did before. The sign rule and `both` are each part of the job's identity (four requests that differ in one of them are four jobs). `copy_b` (a `copy_tag`, optional) names the copy of the cell mesh B is built from and held to when it is not mesh A's, whose copy is the request's `?copy=`: a surface before a setting that moves the annotations (the section correction, the volume's current transforms) held to the surface after it. Each mesh is measured against the annotations of the copy it was built from (`deviation_a`, `deviation_b`), the difference is the distance between the two surfaces wherever they are, and the result says which copies it compared (`copy_a`, `copy_b`) beside `mesh_id` and `mesh_id_b`. A `copy_b` that is not held is a 404; naming mesh A's own copy is the same job as naming none; both copies' serials are in the job's key and in the cached difference's, so loading either copy again retires what was measured against it and nothing else. The blocking `mesh-diff` route refuses a `copy_b` with a 400 The cached difference is keyed by the pair of renderings and the sign rule: `both` is not part of the key, so a one-way result is extended by the reverse pass alone when `both: true` is asked later, and a both-ways result answers a one-way request without a measurement (and without the reverse half) |

## Neurons

**One copy of a cell per setting.** The server holds a separate copy of a cell
for each setting it is loaded with: Viking's correction mode and whether the
volume's current section transforms are applied, named `<mode>.<0|1>` (for
example `curvefit.1`). The settings are per tab, so two tabs (or two people)
looking at one cell with different settings never change each other's cell.
A load answers with its copy's `summary.copy_tag`, and with `summary.positions`, the moves that
copy's annotations were given (the tokens of the current transforms and of the section
correction, joined by `|`; `null` at the stored positions): the tag is what was asked for, and a
correction asked for while the service is down leaves the cell uncorrected under the same tag, so
a client caching anything drawn from the positions keys it on both, as the server's disk mesh key
does. Every route under
`/api/neurons/{source}/{id}` takes `?copy=` to be answered from that copy (the
page adds it to every request and download link), and without it the copy
loaded most recently answers. A copy asked for and not held is a 404
("… is not loaded with the setting …"). The measurement jobs
(`mesh-deviation-job`, `mesh-diff-job`) are no exception: they build and measure
the copy `?copy=` names, and a job is shared only between requests for the same
copy (the copy's serial is in its key, so a cell loaded again is not handed the
old copy's numbers). A difference may also be asked **across two copies**, a
surface before and after a setting that moves the annotations: see `copy_b` on
`mesh-diff-job`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/neurons` | what is loaded on this server, by anyone; signed in with Viking, only volumes you may open |
| POST | `/api/neurons` | body `{id, source, include_synapses, transform, refresh, live_registration, section_corrections, check_fresh}`: only `id` is required; `source` is Inferior Monkey when left out (and the volume it names, or that default, must be one the account may open: 403 otherwise); `include_synapses` and `check_fresh` default to true, `refresh` to false, `transform` to `Standard`. The flags are booleans, and text that spells one (`"false"`) is read as it spells; `null` reads as false. `section_corrections`: `off`, `curvefit`, `neighbor`, `all`, or `true`/`false`. A copy already held is answered as it is only if it was loaded with the synapses a load asks for and with the transform it names; otherwise the cell is loaded again. `manual_moves` (optional): a person's own moves of the cell's annotations by the setting they were made under, `{"curvefit.1": [{"id", "dx_um", "dy_um", "dz"}]}` (X and Y in µm, Z in whole sections; at most 100 µm, 50 sections and 20,000 moves; a bad move is a 400): the set of the setting loaded is applied after the correction to a copy made from the plain one, whose `copy_tag` is `<setting>.m<token>`; the summary's `manual_moves` says what was applied, `positions` ends `manual:<token>`, and `/nodes` gives each moved annotation `manual_shift_um` (X, Y, Z in µm) and `manual_dz` (sections; `z_section` is then the moved section). Never written to Viking |
| POST | `/api/neurons/import` | multipart upload of a neuron JSON file. The cell is held under the settings the file was saved with and replaces the copy of that cell held with the same ones; a file for a volume the account may not open is a 403 |
| GET | `/api/neurons/{source}/{id}` | summary plus synapse counts; the summary's `copy_tag` names the copy it describes |
| DELETE | `/api/neurons/{source}/{id}` | unload: the copy named by `copy`, or every copy when none is named |
| GET | `/api/neurons/{source}/{id}/nodes?cell_only=` | annotation table |
| GET | `/api/neurons/{source}/{id}/edges` | location links |
| GET | `/api/neurons/{source}/{id}/skeleton` | per-segment polylines |
| GET | `/api/neurons/{source}/{id}/closed-curves?z_section=` | splined outlines (Viking's registered curves as they are). The reply is kept as made, by the cell's annotations (as Viking's StructureFreshness names them) and the moves applied to them (the summary's `positions`), so a copy loaded again at positions already seen is answered at once; a cell with no stamp (an imported file) is made each time |
| GET | `/api/neurons/{source}/{id}/annotation-wire?gain=0.5&fit=quadratic&size_tolerance=1.5&break_ratio=2.5&lines=12&ring_spacing_um=0` | the annotations as a wireframe, each ring averaged along Z over a window `gain` times its radius (`sbfsem.geometry.annotation_wire`): `rings` (each `[z, x0, y0, x1, y1, ...]`, closed), `lines` along the chains (each `[x0, y0, z0, ...]`), `ties` (flat segment pairs), counts, and `moved_um` (how far averaging moved each ring) |
| GET | `/api/neurons/{source}/{id}/annotation-wire/export/{obj,ply,stl,csv,tsv,npy,npz,h5}?gain=0.5&fit=quadratic&size_tolerance=1.5&break_ratio=2.5&lines=12&ring_spacing_um=0&wireframe_thickness_um=0.05` | the annotation wireframe as a file, built with the same six settings as `annotation-wire` (the viewer passes the ones it draws with), so the file holds what is on screen: every ring, line and tie as a line segment in microns, shared endpoints one vertex. The formats are the mesh wireframe's (`export/{fmt}?wireframe=true`) less SWC: OBJ `l` and PLY `edge` elements, STL rods `wireframe_thickness_um` in radius, CSV/TSV one row per edge with both endpoints, NPY `(M, 2, 3)` segments, NPZ and HDF5 `vertices` and `edges`. OBJ and PLY comments and HDF5 attributes record the volume, the ID, the units and the settings. Named `c{id}-{source}-annotation-wire.{fmt}` (the volume with the ID); 400 for another format or a cell with nothing to draw |
| GET | `/api/neurons/{source}/{id}/annotators?tolerance_s=3600` | who Viking attributes each of the cell's annotations to now, cell and child structures together, live: per username `annotations`, `fraction`, `as_placed`, `revisions`, `restamped`, `structures`, `first`, `last`; plus the cell's `revisions`, `restamped`, `bulk_edits` and `owner`. Viking keeps only the **last writer** of an annotation, so these are shares of the record, not of the work |
| GET | `/api/neurons/{source}/{id}/skeleton/check` | loops, unlinked pieces, jumps, hairpins, spikes, spokes, stubs and lamellae of the annotation graph, each with its Location IDs, links, anchor, length and where it is fixed (`viking` or `export`); `summary` has the piece, loop and spoke totals, both cable lengths, `dropped_links`, and the lamella totals: `lamella_net_um` (the chains less what they replace, no link inside the outlines counted) and `lamella_written_net_um` (what an SWC written with the chains gains, the links joining each chain to the cell included; `lamella_joins_inside_um` of those lie inside the outlines) |
| GET | `/api/neurons/{source}/{id}/shift-summary?pair=correction` | what the section correction and the volume's current section transforms did to the cell's own annotations, and whether the links across sections agree better after (`sbfsem.analysis.shifts`; milliseconds, so a plain GET), for the copy named by `copy`. `pair` is the move the `links.verdict` and `coherent_sections` are about: `correction` (registered to corrected, the default), `transforms` (stored to registered, the stos edits) or `both` (stored to corrected). `applied` says which stages this copy has. `pairs` has, for all three pairs, `n_annotations`, `n_moved` (more than 0.5 nm) and the `median_um`, `p95_um`, `p99_um` and `max_um` of the moves of those that moved. `links` has `n_links` (Viking links between two of the cell's annotations on different sections), `stages` (`stored`, `registered`, `corrected`: the sideways step along those links, `median_um`, `p90_um`, `p99_um`, `max_um`, `n_over_1p5_um`; `null` without links) and `verdict` (how the pair changed the steps: `n_shrank`, `n_grew`, `n_within_5nm` and their `share_*`). `coherent_sections` lists the sections where the annotations moved as one, strongest first: at least 4 annotations, a resultant ratio `abs(sum v) / sum abs(v)` of 0.6 or more and a mean move (every annotation counted) of 0.2 um or more; each has `section`, `n`, `mean_dx_um`, `mean_dy_um`, `mean_um`, `resultant`. A 422 for another `pair` |
| GET | `/api/neurons/{source}/{id}/morphometrics?bracket=true` | adds `length_floor_um` and `length_floor_by_radius_um`, the shortest path that still passes through every annotation (seconds on a large cell; `null` without `bracket`) |
| GET | `/api/neurons/{source}/export-bulk/swc?ids=&pattern=c{id}&swc_branch_types=false&refresh=false` | a zip of one SWC per structure (at most 200; they need not be loaded) plus `manifest.csv`; the archive stops starting structures after 240 s (`BULK_EXPORT_BUDGET_S`, under the gateway's 300 s) and sends what it built, each structure not reached a manifest row whose `error` begins `NotAttempted` (ask for those again); `pattern` takes `{id}`, `{volume}`, `{label}`; `swc_relax`/`swc_relax_bound`, `swc_lamellae`, `swc_sampling` and `swc_radius` as the single export; `bracket=true` adds the two length floors to the manifest; every cell is drawn the same way: `live_registration=true|false` and `section_corrections=off|neighbor|curvefit|all`, the server's defaults when absent (a loaded cell drawn otherwise is fetched again for the archive; until 2026-09-25 a loaded cell went in as loaded and the rest at their stored positions); the manifest's lengths are the annotation graph's, and `swc_rows`/`swc_length_um` the file's own; a structure that fails is a manifest row with its `error`, not a failed archive; the response headers `X-Archive-Asked`, `-Written`, `-Failed` and `-Not-Attempted` count the structures, so a client that does not open the zip can tell a short archive from a whole one |

## Meshes

| Method | Path | Notes |
|---|---|---|
| GET | `/api/neurons/{source}/{id}/mesh` | `method`, `smooth_iterations` (0 to 50; the mesh, export, diff and render-prefs routes all take the same range, and the viewer offers 0 to 10), `reduce_fraction`, `pixel_size_um`, `curve_avg_discs` (0 to 3) and `curve_avg_paths` (0 to 2): Closed curve's averaging of its staircase (`sbfsem.geometry.curve_average`), both 0, which is off, unless set — each disc is averaged with its neighbors along its chain of links over that many of its own radii (smaller discs less, larger discs more), each traced outline over that many microns along its chain, and the finished surface over the same length, except where a process is a pixel or two across; Cylinder, Tube and Hybrid read `curve_avg_discs` too since 2.7.0 (the discs their tubes run through are averaged first), Hybrid reads both, and Closed curve both, and a surface with both at 0 has the cache key and the mesh id it had before the options existed; `curve_links_um` (0 to 20, 0 off: Closed curve draws a link whose discs are at least that far apart as a curve through the chain's discs, `sbfsem.geometry.curve_links`), `curve_tie_um` (0 to 20, 0 off: Closed curve joins separate bodies of its raster up to that far apart with ties, `sbfsem.geometry.curve_ties`), `curve_bridge_um` (0 to 10, default 1: the widest skip between traced sections that is interpolated) and `curve_link_bridge_um` (0 to 50, default 5: the most Z a link is followed across skipped sections) — the last two Closed curve's and Hybrid's, the first two Closed curve's alone, each left out of the cache key at its default, so a surface built before they existed keeps its key and mesh id; `surface_smooth_um` (0 to 2, 0 off, every method: the finished surface smoothed over that length, no annotation moved, `curve_average.smooth_surface`) and `exact_tube_deg` (0 to 60, 0 off: Cylinder, Tube and Hybrid under Voxel remesh and Ultraliser draw a thin stretch's pairs of discs within that many degrees of vertical as the hull of the two discs, exact at both sections, `unify.DiscHull`), both since 2.9.0 and left out of the key at 0; `clip_ipl` with `keep_above` (the viewer's Clip IPL), `clip_z` (a cut at a Z in microns: for scripts, the viewer has no control for it), `color_by_strata`; every technique knob (and `voxel_detail`, 0.25 to 3, which scales the voxel pathways' grid: the API and the client carry it, and the viewer has no control for it); `meshlab` (`none`, `isotropic`, `qem`, `poisson`) with `meshlab_edge_um`, `meshlab_fraction`, `meshlab_depth` — a MeshLab remesh after the technique, 400 with the reason when the server lacks the optional pymeshlab (`/api/build` reports `meshlab: true/false`). Every mesh also carries `mesh_id`, a short id of that exact cached render (null once it is clipped), which a measurement job answers with so the numbers can be attached to the mesh a client already holds; and, for the whole-cell grid of Voxel remesh, Convolution and Ultraliser (and a Hybrid's disc half on them), `voxel2_um` (the voxel it was contoured at), `voxel2_asked_um` (the one typed, or the annotations' own for `voxel2_um=0`), `voxel2_coarsened` and `voxel2_reason` (`floor`: under 0.02 µm typed, 0.04 µm sized from the annotations; `faces`: past the raw-triangle budget, `unify.raw_budget`: three per triangle of the Face cap, at least 600,000 and at most 10,000,000; `work`: past the evaluation budget, `unify.work_budget`), all `null` for other techniques |
| GET | `/api/neurons/{source}/{id}/render-estimate?…` | what a render with the mesh endpoint's query parameters would cost on the copy named by `?copy=`, answered before anything is built and outside the build queue (`sbfsem.service.estimate`). For `closedcurve` and `hybrid` (`applies: true`): `voxels` (the outline raster's tiles, counted exactly; `null` under 0.005 µm, where they are not counted), `faces` (exposed voxel faces, split as `faces_plane` and `faces_across`, scaled from a raster of the cell at another pixel: in the sections' planes as 1 / pixel, between sections as 1 / pixel², within 2 % on RPC1 c2628, Inferior c16137 and RC1 c410), `triangles` with `triangles_per_face` (the cell's own, from a contoured raster at 0.1 µm or a build at 0.1 µm or finer; `triangles_range` is 1.4 to 2 per face when none says), `peak_mb` (the build) and `serve_mb` (what serving the finished mesh adds in the web process: 65 MB and 28 bytes a triangle since 2.19.6, 300 bytes a triangle before), `server_mb` (their sum), `seconds` (production's, build and serialization), `json_mb` (the text the page receives) and `tab_mb` (the heap the tab grows by), `limits` (`voxels` 300,000,000, `faces` 10,000,000, `memory_mb` this server's `SBFSEM_MESH_MEMORY_MB`, `json_mb` 500: Chrome cannot parse a response longer than V8's 536,870,888-character string), `over` (which of `voxels`, `faces`, `memory`, `page` the options are past), `finest_um` (the finest pixel, to the thousandth of a micron, that fits every limit with 5 % to spare, as the server's own refusal names it; `null` when not even 1 µm fits) and `binding` (the limit that sets it), `basis` (`{kind: built | reference, pixel_um}`: what the faces were scaled from), `averaging`, `outlines`, `hybrid_discs`, `estimate_s`, and `cell`, `source`, `copy`. Past the server's memory with Join bodies or the smoothing on (both only add memory), `alternatives` lists `{off: joins | averaging | both, set, finest_um, fits}`: the options that turn it off, the finest pixel that then fits, and whether the pixel asked for does (an empty list otherwise). A cell with nothing to rasterize answers `empty: true`. Every other method answers `applies: false` and the build gate's `peak_mb`; a Cylinder or Tube build under a field pathway (`voxel2`, `convolution`, `ultraliser`) adds `field` (since 2.18.0), the Fine voxel the build would contour at, sized as the build sizes it (`unify.plan_voxel`) on the build's own pieces without contouring: `voxel_um`, `typed` (a Fine voxel was typed) with `typed_um` and `asked_um` (it held to 0.02 to 0.3 µm, or the annotations' own when automatic), `coarsened` and `reason` (`floor`, `faces` for the raw-triangle budget, `work` for the field evaluations), `area_um2`, `voxel2_faces`, `raw_budget`, `raw_triangles`, `triangles` (kept, after the Face cap), `work` and `work_budget`, `peak_mb` (what a build drawing those raw triangles peaks at: 310 MB and 0.445 MB for every 1,000, 0.37 MB more when the cap decimates them: a sheet over its cap, or a Decimate under 1; under a cap over a million, which decimates in chunks since 2.19.4, 500 MB for a chunk instead) and `gate_mb` (the build gate's price: the whole budget a cap allows over 3 million, and since 2.19.2 never less than `peak_mb`, the sheet the build is sized to draw), `cap_for_asked` (the least Face cap, to 10,000, that builds `asked_um`, with its `voxel_um`, `raw_triangles`, `triangles`, `peak_mb`, `gate_mb`; `null` when the cap is not what holds it or none does), `uncapped` (the same for Face cap 0; `null` when the cap is 0), `finest_um` (the finest voxel any cap allows the cell) and `finest_at_cap_um` (this cap's), and `estimate_s`. The page marks what will not build as set from these (`renderconflicts.js`). Answers are kept per copy of the cell and options; a build of the cell feeds the next answer (a later one is scaled from it), and a size finer than 2.5 times a count of the cell (a traced cell is built at 0.25 µm on load) needs one raster of it at 0.1 µm, contoured for its triangles per face, made once per copy of the cell: in the background as soon as an estimate of the cell is asked at a coarser size, else by the first request that needs it (2026-10-16, load average 12: 4.3 to 7.0 s for RC1 c410, Inferior c16137 and RPC1 c2628, then 0.06 to 0.3 s an answer, and nothing for one already given) |
| GET | `/api/neurons/{source}/{id}/mesh.glb` | binary glTF, for a WebGL loader |
| GET | `/api/volumes/{source}/boundary/{layer}/mesh?decimate=` | the fitted INL or GCL surface as a renderable sheet |
| GET | `/api/volumes/{source}/vessels/mesh` | every traced blood vessel in the volume, decimated |
| POST | `/api/neurons/{source}/{id}/mesh-diff` | (blocking; the viewer uses `mesh-diff-job`) body `{a, b, samples, sign}` of two option sets (`sign` is `normal`, the default; `sign: "enclosure"` and `both: true` are refused with a 400 that points at `mesh-diff-job`: the enclosure test adds 430 to 790 MB and seconds to minutes on a 200,000-vertex mesh, and this route has neither the memory gate nor a job's progress, and the response is mesh A only; the response says `sign_by`); mesh A with per-vertex signed distance (µm) to B in `vertex_data`, plus mean/RMS/p95/max, the sampling resolution, and `deviation_a`/`deviation_b`: each rendering's absolute deviation from the cell's own annotations (mean/p95/max, direction split, unsupported volume in µm³, and the three figures of the exterior surface that `mesh-deviation` describes) |
| POST | `/api/neurons/{source}/{id}/mesh-deviation` | (blocking, so it can end as a 504 on a large cell; the viewer uses `mesh-deviation-job`) body: one option set (as one side of `mesh-diff`); that rendering held to the cell's own annotations point by point: the mesh with each vertex's absolute distance (µm) to the nearest annotation in `vertex_data`, `rim_xyz` / `rim_um` — points along the annotations' rims with each one's distance to the surface, every `rim_stride`-th of `n_rims` (at most 50,000) — `scale_um` (98th percentile of both pooled) and `deviation`, the averages those distances produce, and with them how much of the render is buried inside itself: `exterior_um` (the surface mean over the surface that can be seen; null when none can), `hidden_fraction` (the share of the surface area that is hidden, because a hair outside it along its normal is still inside the mesh) and `annotation_exterior_um` (the rim mean over the rims the render does not enclose; null when it encloses all) |
| GET | `/api/neurons/{source}/{id}/render-prefs` | the render settings attached to this cell on the server (`{"render": null}` when none) |
| PUT | `/api/neurons/{source}/{id}/render-prefs` | attach a render option set to this cell; validated like the mesh endpoint's options, returned with defaults filled. It keeps the view's own options with the geometry (`clip_ipl`, `keep_above`, `color_by_strata`, `clip_z`; a cut that is off is left out of the stored document). `{id}` is a 32-bit structure ID (422 otherwise); the server holds at most 5,000 attached renders (507 past that, for a cell that has none yet) |
| DELETE | `/api/neurons/{source}/{id}/render-prefs` | remove the attached render |
| GET | `/api/neurons/{source}/{id}/last-modified` | the cell's newest annotation as Viking has it now (asked live, never answered from the disk cache, which has no expiry): `{id, last_modified, username, xyz_um}`; `last_modified` is the UTC instant, written with a `Z` (Viking labels UTC wall-clock time Pacific, which read 7 h late), `xyz_um` is the annotation's place in the loaded cell, `null` for a synapse annotation of a child structure. 404 when the cell has no annotations; 502 when Viking cannot be asked |
| GET | `/api/neurons/{source}/{id}/freshness` | `{loaded, live, stale, surface_stale, children_stale, loaded_count, live_count}`: the cell's own annotations as the loaded data has them and as Viking's `StructureFreshness` reports them now (never from the cache), dates in UTC; `surface_stale` when the newest edit is later or the count differs (so a deletion counts), `children_stale` when only its child structures' annotation count does |
| POST | `/api/neurons/freshness` | the same for many cells at once, one Viking request per volume: `{"cells": [{source, id, copy?}]}`; a cell that cannot be answered has `freshness: null` and its `error` |
| POST | `/api/neurons` with `check_fresh: false` | load without the freshness check (the viewer asks for a whole batch afterward) |
| GET | `/api/neurons/{source}/{id}/synapses/{type}/mesh?radius_um=&shape=circle` | a marker per synapse of the type: `shape=circle` (the default) a sphere of `radius_um`, as MATLAB's `synapseSphere` drew them; `shape=oval` an ellipsoid aligned to the annotation's own `LINESTRING` axis, which shows which way each synapse was drawn |
| GET | `/api/neurons/{source}/{id}/closed-curves/mesh?thickness_um=` | extruded outline slabs |

`MeshDTO` carries flat `vertices` and `faces` buffers plus `bounding_box_um`,
`surface_area_um2` and `volume_um3`. `vertex_data` holds per-vertex IPL depth
when `color_by_strata=true`.

### A finished mesh as binary

`GET /api/mesh-jobs/{id}` with `Accept: application/x-sbfsem-mesh` answers a
finished `mesh` job with `Content-Type: application/x-sbfsem-mesh` (since
2.15.0). It is what the viewer asks for: Inferior c1178's Voxel remesh is
7.2 MB instead of 12.7 MB of JSON, 2.99 MB instead of 3.43 MB on the wire, and
the browser has no JSON to parse. Everything else the route answers (a job
queued, running or failed, a measurement, a 404) is JSON whatever was asked,
and so is a mesh the binary cannot carry exactly (below). A client that does
not name the type gets the JSON, byte for byte the reply it got before. The
reply carries `Vary: Accept`. All numbers are little-endian:

| Bytes | What |
|---|---|
| 4 | `uint32`: the header's length `H` |
| `H` | the header: UTF-8 JSON, padded with spaces so the data starts at a multiple of 8 bytes |
| the rest | the data: the arrays the header's `arrays` places |

The header is the poll's JSON with the mesh's arrays taken out: the job's
status fields, `result: null`, and `mesh` with every field of `MeshDTO` in its
order and `null` where `vertices`, `faces` and `vertex_data` go. It adds
`arrays`: `{"vertices": [offset, count], "faces": [offset, count],
"vertex_data": [offset, count] or null}`, each offset in bytes from the start
of the data, each array starting at a multiple of its own element size.

- `vertices`: `int32`, each coordinate (µm) as `rint(v × 10⁴)`, the JSON's own
  fourth decimal. Read it back as `k / 10⁴` in float64 (a division: `k × 10⁻⁴`
  is not always the same number) to get exactly the number the JSON text
  parses to. `-2³¹` stands for `-0.0`.
- `faces`: `uint32`, three per triangle.
- `vertex_data`: `float64`, absent (`null`) when the JSON's is `null`.

A mesh is sent as JSON instead when one of its numbers cannot travel this way
exactly: a coordinate beyond ±214,748.3647 µm, one with more than four
decimals, a non-finite vertex or `vertex_data` value (JSON sends those as
`null`). The viewer's reader is `decodeMeshReply` in `static/js/api.js`, the
writer `sbfsem_web/mesh_binary.py`.

Since 2.19.6 the server makes the reply straight from the surface's arrays
(`SbfsemService.render_buffers`, `mesh_binary.encode_arrays`), the same bytes
as before: no Python number is made per coordinate, and a JSON client's lists
are made only when one asks. Measured on production from cached surfaces, the
web process grows by 97 MB for 0.5 s for 1.70 M triangles and 263 MB for
2.0 s for 7.07 M, where it grew by 702 MB for 2.7 s and 2,891 MB for 11.3 s.

## Analyses

| Method | Path |
|---|---|
| GET | `/api/neurons/{source}/{id}/stratification?num_bins=&include_soma=&omit_outliers=&ipl_margin=` |
| GET | `/api/neurons/{source}/{id}/synapse-stratification/{type}?num_bins=` |
| GET | `/api/neurons/{source}/{id}/hull` |
| GET | `/api/neurons/{source}/{id}/morphometrics` — cable length (3-D and in-plane, total and dendritic), longest path from the soma, branch points, tips, fragments, extent; `?soma_fraction=0.8` |
| GET | `/api/neurons/{source}/{id}/ie-ratio` — `ratio`, `inhibitory_fraction`, `n_excitatory`, `n_inhibitory`, and of those `n_inferred` (typed from the linked partner) and `n_assumed`; `n_untagged` postsynapses are in neither count |
| GET | `/api/neurons/{source}/{id}/surface-area` |
| GET | `/api/neurons/{source}/{id}/dendrite-diameter?bin_width=` — `bin_width` 0.05 to 1000 µm (422 outside; the library refuses more than 50,000 bins) |
| GET | `/api/neurons/{source}/{id}/tortuosity?location_a=&location_b=&dim=` |
| GET | `/api/neurons/{source}/{id}/open-ended?include_off_edge=&include_terminal=` |
| GET | `/api/neurons/{source}/{id}/synapse-soma-distance/{type}` |

## Linked neurons

| Method | Path |
|---|---|
| GET | `/api/neurons/{source}/{id}/linked-neurons?order=neuron|type&refresh=` |
| GET | `/api/neurons/{source}/{id}/linked-neurons.csv?order=` |
| GET | `/api/neurons/{source}/{id}/linked-neurons.xlsx?order=` |

The `getAllLinkedNeurons` table for a loaded neuron: `rows` (partner, synapse
ID, type, label, direction, position in µm), `partners` (synapse counts per
partner and type) and the totals. `order=neuron` is the MATLAB function's
ordering, `order=type` the lab's `getAllLinkedNeurons_sorted` ordering. The
`.csv` and `.xlsx` forms are downloads named `c{id}-{source}-linked-neurons`.
Links are cached with the neuron; `refresh=true` asks Viking again. Each row
also carries the synapse's Viking `tags`, its `tag_status` (`tagged`,
`inferred`, `untagged` or `assumed` for a postsynapse; blank where the Viking
type alone decides), the `suggested_tag` an inferred one is missing, and a
`location_id` of the synapse; the files add them as `Tags`, `TagStatus`,
`SuggestedTag`, `LocationID` after the MATLAB columns.

A postsynapse whose tags do not say what it is becomes, on this server,
the kind of the structure it is linked to (`inferred`) or `UntaggedPost`
(`SBFSEM_UNTAGGED_PSD`: `infer`, the default; `separate`; `assume`, MATLAB's
conventional). The neuron detail's `synapse_counts` give `inferred` and
`assumed` per type.

`live_registration` (`true`/`false`; left out = the server's default, on)
draws the cell where the volume's current section transforms put it
(`sbfsem.io.registration`), as Viking does, instead of at the positions stored
when each annotation was saved. The summary's `live_registration` says whether
it was `applied` (or why not, in `note`), which files it read (`vikingxml`,
`xml_last_modified`) and what moved (`n_moved`, `sections_moved`,
`median_um`/`p95_um`/`max_um`). A `refresh` re-reads the cell's stos files too.

`section_corrections` applies Viking's section correction when the cell is
loaded (`sbfsem.io.corrections`), in a mode: `curvefit` (a fit along each
traced process), `neighbor` (the residual field), `all` (both) or `off`.
`true` and `false`, as before the modes, mean `neighbor` and `off`; an unknown
mode is a 422. Left out, it is the server's default for the cell's volume
(`default_correction` in `/api/volumes`): in the web app `all` for the Neitz
lab's volumes and `curvefit` for the others; off in a bare `SbfsemService`. A loaded cell asked for with another
setting is loaded again with it. The summary's `section_correction` says
whether it was asked for (`requested`), in which `mode`, whether it was
`applied` and, if not, why (`note`), the set (`volume_name`, `stos_group`,
`built_utc`), and what moved: `n_annotations`; `n_found`, on a published
section (`neighbor`) or answered for by the service (`curvefit`, `all`);
`n_trusted`, where the field has support (`neighbor`) or applied (not a line
whose section X/Y are empty); `n_moved`; and `median_um`/`p95_um`/`max_um`. In the
`nodes` table, `shift_um` is how far the correction moved an annotation in
X and Y (`null` if it did not move) and `live_shift_um` how far the volume's
current section transforms moved it (`null` if the cell was not drawn through
them or this annotation did not move); both in um, in the section plane (there
is no Z move). They give the annotation's three positions in
this copy of the cell: **stored** (Viking's own) is `xyz_um - live_shift_um -
shift_um`; **registered** (where Viking draws it now, and what Goto Location
takes) is `xyz_um - shift_um`; **corrected** is `xyz_um`; a `null` counts as
zero. (This holds for the Standard transform; a cell loaded with transform
`None` keeps raw pixels in `xyz_um`, and both shifts are `null`.) `live_shift_um`
is rounded to a picometre (1e-6 um). A linked-neurons row's `synapse_shift_um` is the same correction shift
for its synapse annotation.

## Saved workspaces

Named JSON documents on the server, in one level of user-named folders: what
Save and Open in the viewer write and read. Shared by everyone signed in, and
not tied to a volume.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/saves` | every folder: `name`, `bytes`, `modified`, `files` |
| POST | `/api/saves` | body `{"name": ...}`: create a folder (201); 400 if it exists or the name is not allowed |
| GET | `/api/saves/{folder}` | the documents in a folder: `name`, `bytes`, `modified` |
| DELETE | `/api/saves/{folder}` | remove an empty folder (204) |
| GET | `/api/saves/{folder}/{name}` | the document; 400 if it is no longer readable JSON |
| PUT | `/api/saves/{folder}/{name}` | write the body as the document (any JSON object), replacing the one of that name, through a temporary file of its own so a reader sees the old or the new |
| DELETE | `/api/saves/{folder}/{name}` | remove the document (204) |

A name is letters, digits, spaces, dots, dashes and underscores, starts with a
letter or digit and is at most 63 characters (400 otherwise; nothing can name a
path outside the save directory). The limits are 5 MB a document, 100 folders,
200 documents a folder and 200 MB in all (507). The 5 MB is checked twice: on
the request body as sent, from its declared length and before any of it is
parsed, and on the size the store writes (it re-encodes with an indent). So a
body that is pretty-printed or padded with whitespace past 5 MB is refused
though the store would keep it under, and a compact body under 5 MB can still be
refused for what it stores as; send compact JSON, as the viewer does.

## Connectivity (Graffinity)

| Method | Path |
|---|---|
| POST | `/api/connectivity/query` |
| GET | `/api/connectivity/{source}/graph` |

The query body:

```json
{"source": "i", "hops": 2, "min_hops": 2, "direction": "upstream",
 "start_ids": [1178], "start_pattern": null,
 "node_patterns": ["", "", ""], "edge_patterns": ["", ""],
 "max_paths": 20000, "max_frontier": 300, "allow_repeats": false,
 "refresh": false}
```

`start_ids` or `start_pattern` names the anchors — the starts of a
`downstream` query, the ends of an `upstream` one. `node_patterns` has
`hops + 1` whole-string regular expressions over labels (position 0 is the
first node of every path, position `hops` the last) and `edge_patterns`
`hops` over synapse-type names; empty means anything. `min_hops < hops`
keeps shorter paths that reach a matching end. Paths are simple unless
`allow_repeats` is true.

The answer holds `paths` (`[n0, e0, n1, e1, …]`: neuron IDs at even
positions, indices into `edges` at odd ones), `nodes` (label and type of
every neuron touched), `edges` (source, target, type, synapse count and the
child structure IDs of each synapse), `matrix` (rows, cols and the path
indices per cell), `node_table` (one row per intermediate neuron with path
indices per `"length:position"` column), and the search's bookkeeping:
`n_paths`, `truncated`, `frontier_capped`, `expanded`, `unexpanded`,
`seconds`, and the size of the volume's cached link graph. Each hop costs
two batched Viking requests; a bad pattern is a 400, a Viking failure a 502.

## Network and export

| Method | Path |
|---|---|
| GET | `/api/network?source=&ids=1,2,3` |
| GET | `/api/neurons/{source}/{id}/export/{swc,json,dae,stl,obj,ply,glb}` — the mesh formats (and `mesh.glb`) take the same geometry options as `mesh`, `pixel_size_um` (the closed-curve raster, default 0.05) included since 2026-10-15 `curve_avg_discs`, `curve_avg_paths`, `curve_links_um`, `curve_tie_um`, `curve_bridge_um` and `curve_link_bridge_um` since 2026-10-16, `surface_smooth_um` and `exact_tube_deg` since 2.9.0, so a Closed curve or Hybrid cell exports the mesh that is on screen, and `clip_ipl` (with `keep_above`) and `clip_z`, which cut the file as the mesh route cuts a render: the viewer sends the clip of a cell clipped on screen (a cut that leaves nothing is the empty-mesh 400); SWC takes `?swc_branch_types=true|false` (default true: branch points 5 and tips 6 as MATLAB wrote them; the app passes false, every neurite node 3). The SWC header lists the type codes present, and since 2026-09-21 `SECTION_THICKNESS_UM`, `ANNOTATION_GRAPH` (annotations, links, connected pieces, loops), `ROOTS`, `DROPPED_LINKS` with one `DROPPED_LINK a b rows n m` per loop (the links in Viking a tree cannot hold: both Location IDs, then their rows in the file) `POSITIONS` (`viking`, or `relaxed f=… bound=…` with `RELAXED_STATS`) and `OPTIONS`; since 2026-09-25 also `ANNOTATIONS_STAMP` (the newest LastModified of the cell's annotations; since 2026-10-16 in UTC whatever zone Viking's OData labeled it with, so one cell's stamp no longer depends on the age of the cache), z written to four decimals (two broke the Temporal volume's 75 nm sections), `SAMPLING` and `R_COLUMN` (what the rows and the radius column are), and `OPTIONS` names every option, defaults included. Since 2026-10-07 `SOMA` gives the soma cutoff (`soma_fraction` of the largest stored radius of the cell's own annotations, not its synapses', and where that radius is), how many rows are typed 1, and that the soma is typed only: no row is moved or dropped for it and every link counts in the file's length (`SOMA none` with `has_soma=false`). `?swc_relax=0…1` writes each node moved toward its neighbors to shorten the path but never outside that fraction of its own annotation (`swc_relax_bound=outline`: its traced outline scaled about its centroid, or its circle; `radius`: a circle of its stored radius — which does not keep a closed curve's node inside its outline); tips fixed, z and R untouched. `?swc_lamellae=true` writes each lamella stack as the chain along it in place of its annotations (header `LAMELLAE`, `LAMELLA_RULE`, one `LAMELLA k locations … sections … rows … cable= joins= joins_inside= internal= leaving= replaced= beyond=` per stack; a chain node's R is half the local width; not with `swc_relax`, 400). `?swc_sampling=sphere` keeps only the rows of Bradley & Withers's minimal sphere network (eq. 1) along each unbranched path, tips, branch points, roots and dropped-link ends always kept (`SAMPLING sphere: N of M rows kept …`). `?swc_radius=modal` writes a closed curve's modal radius in R, a circle's stored radius, and Viking's stored radius of each closed curve as `R_STORED row r`. A build the export cannot run (a Closed curve raster over its voxel limit, an unknown `method`, a render option the service does not have) is a 400 with the reason. The download is named `c{id}-{source}.{fmt}` (`c{id}-{source}-wireframe.{fmt}` for a wireframe): the volume travels with the ID |

`/api/network` returns Cytoscape.js `elements` plus a degree table.

## Settings: the debug tools

The app's Settings tab (`static/js/settings.js`). The `admin` routes act on the server
everyone shares, so they need an admin: a name in `SBFSEM_ADMIN_USERS` (built-in or Viking,
any case), a request from the machine itself, or anyone when no sign-in is configured;
anyone else gets 403 naming their account. An action runs only for a body sent as
`application/json` that names it (`{"confirm": "clear"}`), else 400: another site cannot
send that without a preflight, which this app never answers. Each is written to the
journal with who asked (`src/sbfsem_web/api/admin.py`).

| Method | Path | What |
|---|---|---|
| POST | `/api/browser/clear-cache` | answers with `Clear-Site-Data: "cache"`: the browser that asked drops what it cached of the site, and keeps its storage and cookies |
| GET | `/api/admin/whoami` | `user`, `admin` (whether this caller may use the routes below), `login`, `admins_named`, `admin_setting`; open to anyone signed in |
| GET | `/api/admin/status` | `version`, `assets`, `instance`, `started_at`, `uptime_s`, `pid`, `supervised` (started by systemd), `service_memory_bytes` (the unit's cgroup, on Linux), `memory` (the service's memory report: cells, copies, annotations, surfaces held), `disk_cache` (`files`, `bytes`, `budget_bytes`), `pool` (`build_processes`, `pool_running`, `pool_recycles`, `recycle_mb`), `builds` (as in `/api/health`) |
| POST | `/api/admin/clear-renders` | `{"confirm": "clear", "disk": false}`: forget every surface held in memory (with the deviations, error maps and differences measured on them, and the outline replies kept), and with `disk: true` delete the disk cache's files too; answers `memory_meshes`, `disk_files`, `disk_bytes`. Cells stay loaded |
| POST | `/api/admin/recycle-workers` | `{"confirm": "recycle"}`: replace the build processes, as a heavy build does by itself; builds running finish in the old ones. `recycled: false` when builds run in the server process or none has started |
| POST | `/api/admin/restart` | `{"confirm": "restart"}`: 202, then a normal shutdown and exit code 75, which the unit restarts for; a second press while it stops does nothing more. 409 on a server systemd did not start |

## Errors

| Status | Meaning |
|---|---|
| 400 | a valid request the backend cannot satisfy — an unknown export format, an unknown `method`, an empty mesh, a missing boundary cache, `sign: "enclosure"`, `both: true` or a `copy_b` on the blocking `mesh-diff` (all three are a job's: `mesh-diff-job`); a document that JSON cannot hold (a `NaN`) or a saved document that is no longer readable |
| 401 | `{"detail": "Not authenticated"}`: a login is configured and the request has no session (a page request is redirected to `/login` instead). Not asked for `/api/health` and `/api/build`, or of the server's own loopback |
| 404 | unknown volume, or a neuron that is not loaded |
| 422 | malformed parameters or body |
| 403 | the signed-in Viking account has no access to the volume named in the request; or a Settings `admin` route asked by an account that is not an admin |
| 502 | the Viking query failed or timed out |
| 507 | a quota: a saved document over 5 MB (as sent: refused from its declared length, before it is read; and as stored), a full save directory (200 MB), too many folders (100) or documents in a folder (200), or too many attached renders (5,000) |

## Deep links

`GET /open?volume=&cells=&technique=&location=` opens a volume, and optionally
some cells, from a link elsewhere — the Viking Identity site and Viking's context
menus — and with `location` flies the view to that one annotation. `volume` is
the identity server's name for it, and `location` is looked up in that volume. Not an `/api` route: it
serves the app shell with the request stamped in. See
[Viking Identity sign-in](viking-identity.md).

`GET /open-in-viking?volume=&location=` goes the other way: it opens a place in
the Viking desktop client. `volume` is our name or the identity server's;
`location` is a Location ID, or `x,y,z[,downsample]` in Viking's own pixel and
section space (a downsample above `SBFSEM_VIKING_LAUNCH_MAX_DOWNSAMPLE`,
default 32, is capped). The answer is a 302 to
`viking://open?volumeName=&location=`, which the operating system hands to
Viking; with `SBFSEM_VIKING_LAUNCH_MODE=code`, to the identity server's
`VikingLaunch/CreateCode` instead, which redirects there with a one-use code.
With `page=1`, which is what the interface asks for, it answers with a launch
page instead that makes the same hand-off from our origin, refuses off Windows,
and says so when Viking is not installed. `client=test` is the same link for the
unsigned "Viking Test" build, `viking-test://open?volumeName=&location=`, which is
always the direct link, whatever the launch mode: `CreateCode` only redirects to
`viking://`. With `page=1`, `then=viking` or `then=test` names the other client for
the page to offer, on a button, when nothing answers the first (`client` defaults to
`viking`; the bare redirect ignores `then`). Refusals are `text/plain`: 400 for no
volume, a malformed location, or a client that is neither `viking` nor `test` (or a
`then` equal to `client`), 404 for a volume that is not a Viking volume, 403 for one
the signed-in Viking account cannot read. Not an `/api` route, and not in the
OpenAPI schema.

## Volume access

With [Viking Identity sign-in](viking-identity.md) switched on, a request that
names a volume the signed-in account has not been granted is a 403, and
`/api/volumes` and `GET /api/neurons` list only the volumes it has. One
guard on every `/api` route reads the volume from the path (`{source}`), from
every `source=` of the query string and from the `source` of a JSON body (a
body FastAPI reads as JSON: `application/json` in any capitalization, with
parameters or a leading space, or any `+json` type; a body with no
Content-Type at all is read too, because an older FastAPI takes it for JSON),
and refuses the request when any one of them names a volume the account may
not open, so a granted volume in one place cannot stand in for another. Four routes check
themselves as well, on the volume FastAPI parsed, because the guard can only
read what a request says: `POST /api/connectivity/query` and `POST
/api/neurons` (a body without a `source` loads Inferior Monkey, so that is the
volume checked), `POST /api/neurons/freshness` (one volume per cell: a cell of
a volume the account may not open comes back with `freshness: null` and an
`error`, and Viking is not asked about it) and `POST /api/neurons/import` (the
volume is inside the uploaded file). Nothing else is filtered: a job is reached
by its id (`/api/mesh-jobs/{id}`), and the saved workspaces and the shared
libraries belong to no volume. With Viking sign-in off, or under the built-in
login, no route is filtered.

## Options the viewer has no control for

The HTTP API is also the scripting surface, and some of its options have no
control in the viewer (it never sends them, so they keep their defaults there):
the SWC export's `swc_relax`, `swc_relax_bound`, `swc_lamellae`, `swc_sampling`
and `swc_radius`; `soma_fraction` of `morphometrics`; `wireframe_thickness_um` of
a wireframe STL; `ipl_margin` of `stratification`; `include_off_edge` and
`include_terminal` of `open-ended`; `voxel_detail` and `clip_z` of the mesh;
`spline_points` of `closed-curves` and `thickness_um` of `closed-curves/mesh`.
They are tested and meant for scripts; adding a control for one is a product
choice, not a fix.

The bulk SWC archive takes at most 200 structures, and a structure that is not
loaded, or loaded but drawn with other Registration or section-correction
choices than the archive's, and not in the disk cache is a Viking round trip
(the route's own note puts RPC1 c192 at 5 s). The archive stops starting
structures after 240 s (`BULK_EXPORT_BUDGET_S`, under nginx's five minutes) and
is sent with what it built, each structure not reached a manifest row whose
`error` begins `NotAttempted`. That is a 200 and a zip like a whole one, so the
response says it as well: `X-Archive-Asked`, `X-Archive-Written`,
`X-Archive-Failed` and `X-Archive-Not-Attempted` count the structures, and the
viewer reads them (a short archive is saved, and the status line says in red
that it holds only 130 of 150 skeletons and that `manifest.csv` names the rest).
A script that asks for structures it has not loaded should look at
`X-Archive-Not-Attempted` and ask again for those (what was fetched is cached),
or ask for 40 or fewer at a time.

Responses never contain `NaN` or `Infinity`: non-finite floats serialize as
`null`, because `JSON.parse` rejects the alternatives.

Since 2.17.8 a reply with a response model, and a finished job's poll, is
written by pydantic's encoder. A number under 1e-4 whose exponent has one
digit is then spelled another way (`0.00001` where 2.17.7 wrote `1e-05`,
`1e-6` for `1e-06`); any JSON parser reads the same value. Every other byte is
as before.
