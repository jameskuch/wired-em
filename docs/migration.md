# Migrating from the MATLAB toolbox

A call-by-call map from SBFSEM-tools to SBFSEM-pytools. Anything marked **fixed**
also changes the answer; see [`../DEVIATIONS.md`](../DEVIATIONS.md).

## Loading

| MATLAB | Python |
|---|---|
| `c = Neuron(943, 'i')` | `c = load_neuron(943, "i")` |
| `c = Neuron(943, 'i', true)` | `c = load_neuron(943, "i", include_synapses=True)` |
| `c = Neuron(943, 'i', true, 'custom')` | `c = load_neuron(943, "i", include_synapses=True, transform="custom")` |
| `c.update()` | `load_neuron(..., refresh=True)`, or `SbfsemService.load_neuron(..., refresh=True)` |
| `c.getSynapses()` | `include_synapses=True` at load, or `NeuronLoader.fetch_synapses` |
| `c = NeuronJSON('i943.json')` | `c = read_neuron_json("i943.json")` (dispatches v2 / legacy) |
| `x = sbfsem.io.JSON('i', dir); x.export(c)` | `write_neuron_json(c, "c943.json")` |
| `validateSource('inf')` | `validate_source("inf")` |
| `getODataScale('i')` | `ODataClient("i").get_scale()` |

## The neuron object

| MATLAB | Python |
|---|---|
| `c.ID`, `c.source` | `c.id`, `c.source` |
| `c.nodes`, `c.edges`, `c.synapses` | same names, pandas DataFrames |
| `c.nodes.XYZum` | `c.nodes[["Xum", "Yum", "Zum"]]`, or `c.xyz_um` |
| `c.nodes.Geometry` | `c.nodes["TypeCode"]` — the OData field's real name |
| `c.getCellNodes()` | `c.get_cell_nodes()` |
| `c.getCellXYZ()` | `c.get_cell_xyz()` |
| `c.getSomaXYZ()`, `c.getSomaSize()`, `c.getSomaID()` | `c.get_soma_xyz()`, `c.get_soma_size()`, `c.get_soma_id()` — **fixed**, cell nodes only |
| `c.getSynapseXYZ('RibbonPost')` | `c.get_synapse_xyz("RibbonPost")` — **fixed**, empty not NaN |
| `c.getSynapseN('ConvPre')` | `c.get_synapse_count("ConvPre")` |
| `c.synapseNames()` | `c.synapse_names()` |
| `c.printSyn()` | `c.synapse_summary()` (a DataFrame) |
| `c.offEdges`, `c.terminals`, `c.unfinished` | `c.off_edges`, `c.terminals`, `c.unfinished` |
| `c.getDAspect()` | `c.get_daspect()` |
| `[G, nodeIDs] = c.graph()` | `g = c.graph()` — a networkx graph keyed by location ID; there is no `nodeIDs` |
| `c.getBranchNodes(a, b)` | `c.get_branch_nodes(a, b)` |

## Geometry and rendering

| MATLAB | Python |
|---|---|
| `dendriteSegmentation(c)` | `dendrite_segmentation(c)` — **fixed**, segments no longer span branch points |
| `c.build(); c.render()` | `cylinder_render(c)` -> a `Mesh` |
| `sbfsem.render.Cylinder(c, 'method', 2)` | `cylinder_render(c, method=2)` |
| `renderClosedCurveOutlines(c)` | `build_curve_volume(annotations_from_geometries(c.geometries))` then `curve_volume_to_mesh` — **fixed**, output is in microns |
| `volumeRender(vol)` | `volume_to_mesh(vol, spacing=..., origin=...)` |
| `clipMesh(FV, z)` | `clip_mesh(mesh, z)` |
| `clipMeshByStratification(FV, 'i', 0.5)` | `clip_mesh_by_stratification(mesh, load_ipl_boundaries("i"), 0.5)` |
| `getStrataCData(FV, INL, GCL)` | `load_ipl_boundaries("i").strata_cdata(mesh.vertices)` |
| `synapseSphere(c, 'ConvPre')` | `synapse_spheres(c.get_synapse_xyz("ConvPre"))` |
| `sbfsem.image.ConeOutline(c)` | `cone_outlines_from_neuron(c)` — the MATLAB class did not load |
| `catmullRomSpline(x, y)` | `catmull_rom_spline(x, y)`; `close_spline(points)` to close a loop |

## Analysis

| MATLAB | Python |
|---|---|
| `micron2ipl(xyz, 'i')` | `load_ipl_boundaries("i").micron2ipl(xyz)` — **fixed**, the INL grid axis bug |
| `iplDepth(c)` | `ipl_depth(c)` — **fixed**, true bin centers |
| `getSynapseStratification(c, 'RibbonPost')` | `synapse_stratification(c, "RibbonPost")` |
| `singleDendriteStratification(c, a, b)` | `branch_stratification(c, a, b)` |
| `tortuosity(c, a, b)` | `tortuosity(c, a, b, dim=2)` — **fixed**, 2-D/3-D no longer swapped |
| `singleDendriteDiameter(c, a, b)` | `branch_diameter(c, a, b)` — returns diameters, not radii |
| `sbfsem.analysis.DendriteDiameter(c)` | `dendrite_diameter_by_soma_distance(c)` |
| `sbfsem.analysis.DendriticFieldHull(c)` | `dendritic_field_hull(c)` |
| `surfaceArea(c)` | `surface_area(c)` — **fixed**, cell nodes only, microns squared |
| `ieRatio(c)` | `ie_ratio(c)` — returns both `ratio` and `inhibitory_fraction` |
| `getSynapseSomaDistance(c, t)` | `synapse_soma_distance(c, t)` |
| `getSynapseSomaPathLength(c, t)` | `synapse_soma_path_length(c, t)` |
| `getOpenEnded(c)` | `open_ended_branches(c)` |
| `getNearestAnnotation(c, xyz)` | `nearest_annotation(c, xyz)` |
| `getPathDistance(c, a, b)` | `path_distance(c, a, b)` |
| `densityRecoveryProfile(pts, w, n)` | `density_recovery_profile(pts, w, n)` |
| `sbfsem.builtin.INLBoundary('i')` | `load_boundary("i", "INL")` |
| `RegularizeData3D(x, y, z, xn, yn, ...)` | `regularize_data_3d(x, y, z, xn, yn, ...)` |
| `sem(v)`, `printStat(v)` | `sem(v)`, `describe(v)` — **fixed**, NaN-safe |

## Network

| MATLAB | Python |
|---|---|
| `getLinkedNeurons(c, 'RibbonPost')` | `linked_neurons(c, "RibbonPost", client)` — batched |
| `getAllLinkedNeurons(c)` | `all_linked_neurons(c, client)` |
| `countLinkedNeurons(T)` | `count_linked_neurons(links)` |
| `Network(...)`, `neuronGraphPlot` | `network_from_links(links)`, then `cytoscape_elements` for the browser view |
| `adjacencyMatrix(...)` | `adjacency_matrix(graph)` |

## Export

| MATLAB | Python |
|---|---|
| `sbfsem.io.SWC(c).save()` | `write_swc(c, "c943.swc")` |
| `c.dae()`, `exportSceneDAE(...)` | `write_collada({"c943": mesh}, "c943.dae")` — see DEVIATIONS §6.1 on `unit/@meter` |
| `sbfsem.io.STL(c)` | `write_stl(mesh, "c943.stl")` |
| — | `write_gltf(mesh, "c943.glb")` (new; what the browser view loads) |
| `pajekNET(...)` | `write_pajek(network, "net.net")` |
| `linklistTXT(...)` | `write_link_list(links, "links.txt")` |
| `stack2gif(...)` | `write_gif(volume, "stack.gif")` |

## Registration

| MATLAB | Python |
|---|---|
| `xyRegistration('i', [1284 1305])` | `xy_registration(client, (1284, 1305))` |
| `updateTransform(S, 'i')` | `update_offsets("i", result)` then `write_offset_file` — **fixed**, no double-applied section |
| `xyShift(c, sections)` | `xy_shift(c, sections)` |
| `omitLocations(id, 'i')` | `read_omitted_ids(id, "i")` |

## Idioms

- `findgroups`/`splitapply` becomes `DataFrame.groupby(...).agg(...)`.
- MATLAB `table` becomes `pandas.DataFrame`; `height(T)` is `len(frame)`.
- MATLAB `graph`/`digraph` becomes `networkx.Graph`/`DiGraph`, keyed by location
  ID rather than a 1-based node index. Anywhere you wrote
  `nodeIDs(find(nodeIDs == locID))`, use the location ID directly.
- 1-based indexing becomes 0-based. The `dim` argument of `clip_mesh` is now
  `axis`, 0/1/2 rather than 1/2/3.
- Functions that took or returned a MATLAB graphics handle take and return a
  `Mesh` instead; the frontend applies the result.
