# User guide

The interface opens on the 3D render tab with nothing loaded.

## Loading neurons

**Structure ID** takes a Viking ID and loads it into the selected volume.
Synapses are always imported with the cell, but their markers start hidden —
the counts are ready in the cell's Synapses section, one checkbox away.
Opening a saved workspace restores whatever was shown when it was saved.

**Search by label** (Find) does a substring match against Viking structure
labels and lists every match with a checkbox — type `ON parasol` or `lmTRACE`,
then click a result to load just that cell, tick some and **Add selected**, or
**Add all**. Find only lists; nothing loads until you ask. The results belong to
the volume they were searched in: they are cleared when you switch volume (IDs
are numbered per volume), and a search still out when you switch is dropped.
**Load** takes the
structure box literally — IDs, ranges and labels — and loads everything it
names at once (see *Loading several cells at once*). A cell that is already
loaded keeps its render and display settings when its ID is loaded again.

**Import…** in the Files group accepts four kinds of file: schema v2 neuron
exports written by this tool, the MATLAB-era exports written by
`sbfsem.io.JSON`, workspace documents — a workspace opens exactly as
**Open…** would open it from the server — and plain-text structure lists in
the Viking export portal's format (IDs, ranges, and labels; see *Loading
several cells at once*), which load against the selected volume.

Each loaded neuron gets a row in the sidebar: a visibility checkbox, a color
swatch, a ↻ that re-fetches it from Viking, and an ✕ to unload it.

### Cells edited in Viking

Annotations are cached on the server so a cell loads in a fraction of a
second, but the cache never expires on its own. So once a load finishes,
the viewer asks Viking, in one request per volume however many cells, for
each cell's newest edit and its number of annotations, and compares both
with the data it has. A cell edited since its data was fetched, or with
annotations added or deleted, shows an amber dot after its name, its ↻ lights
up (hover it for both timestamps, and both counts when they differ), and the
line under the list counts them (`3 edited in Viking`). A cell whose synapses
or other child structures gained or lost annotations is marked too; the ↻
then says its own annotations, and so its surface, did not change. Click ↻
to re-fetch that cell, or **Refresh all** to re-fetch every loaded cell —
edited ones first. A refresh always goes to Viking, never to the cache.

Since 2.26.0 a load asks Viking first, too. When the server already holds the
cell (another tab or person loaded it earlier) or its disk cache has it, the
server asks Viking whether the cell's own annotations changed since (one short
request, about 30 ms; at most every 30 s for one copy) and fetches it again if
they did. A cell being traced is drawn as it is now: on 2026-10-21 Temporal
Monkey c162392 was drawn from its first annotation, held since the morning,
when it had 153, and the render came back empty.

### Registration: where the annotations are drawn

Viking draws each annotation by mapping its section coordinates through the
volume's current section transforms, the stos files its VikingXML lists. The
positions stored in Viking's database are that mapping as it was when each
annotation was saved. They lag any transform edited since, until someone runs
VikingAU. This viewer draws what Viking draws: it reads the same files and
maps every annotation, outline and synapse through them. Where nothing was
edited, the result equals the stored positions to a few nanometers.

The cell's panel has a **Registration** section. Its header gives the number
of annotations the current transforms put away from their stored positions,
and the largest move (`548 moved, max 11.26 µm`). It reads `current` when
nothing moved, `stored positions` when the switch is off, and
`stored (unavailable)` when the volume's files could not be fetched. Untick
**Viking's current section transforms** to see the stored positions. ↻ re-reads
the cell's transforms along with its annotations.

Nothing stands under the switches as text. Beside each is a small chip: how
many annotations it moved (`56 moved`, `none moved`), or `unavailable`. Rest the
pointer on the chip for two seconds for the facts (the VikingXML file, its stos
group and date, how many moved and how far, on which sections) or, for
`unavailable`, the reason in plain words with the server's own message at the
end. Rest it on a switch for what the switch does, with a drawing above the
words.

The switch is there only for the volumes whose transforms are read here
(`has_transforms` in `/api/volumes`): Inferior Monkey, Cped, RPC1 and RPC2.
RC1's registration is chained and is not composed here, and Nasal, Temporal and
RC2 list no stos this tool can fetch, so their cells are drawn at their stored
positions and their panels have no switch (a session saved with it on still
loads, at the stored positions).

#### Viking's section correction

Viking also measures, from every traced process, how far each annotation sits
off the curve through its neighbors on the sections above and below, and
corrects for it: small sideways moves, section by section, that take out the
jitter the alignment leaves between sections (on Inferior c1178, with Curve fit,
a median of 0.11 µm). That is what Viking's corrections service gives; the
switch's hover help says it with a drawing, and ends with what the service did
for this cell, read from the cell's own status. With **Viking's section correction** ticked (the default),
each annotation moves by that correction, on top of the current transforms;
untick it to see the cell where the transforms alone put it. **Correction**
picks how it is computed:

- **Curve fit** (the default for the Marc lab's rabbit volumes, RC1, RC2,
  RPC1 and RPC2, since 2026-09-24; for every volume until 2026-10-20). Viking fits
  each traced process on its own, leaving each annotation out of its own fit.
  On c21768 it took the median step between linked annotations on adjacent
  sections from 0.194 to 0.125 µm, and the steps over 1.5 µm from 39 to 19.
  Synapses are not part of a process and keep their place: on c21768 a
  synapse's distance to its cell moved by a median 0.04 µm (at most 0.64).
- **Residual field.** The same measurement pooled across the volume on a
  2 µm grid, as Viking's own exports apply it. Each annotation, outline and
  synapse moves by the field at its own place on its own section, and nothing
  moves where the field has no support. On 2026-09-23 it made the steps near
  c21768 larger, not smaller.
- **Field + fit** (the default for the Neitz lab's volumes, Inferior, Nasal,
  Temporal and Cped, since 2026-10-20). Both, the field first. This is what
  Viking's service does when it is not told which. On Inferior c1178 it moved
  2,431 of 2,819 annotations (median 0.12 µm) where Curve fit moved 2,176
  (median 0.11 µm).

An annotation Viking's service places more than 20 µm from where the viewer
draws it keeps its place: its correction was worked out somewhere else. (Until
the evening of 2026-09-24 the service placed lines and curves with empty
section X and Y at the section's origin.) When the service does not answer, or
has no correction of the kind chosen for the volume, the cell is drawn
uncorrected and the chip reads `unavailable`, with why in its hover. A service
that cannot be reached is not asked again for two minutes, for any volume; a
cell corrected before is drawn meanwhile with the correction it had then, and
its hover says so. When it answers with an error for a cell, it is not asked again
for that cell and correction for ten minutes, and once two cells of a volume
fail in a row, for the whole volume, so the cells load at once; the chip's
hover says until when. The other cells of the volume are still corrected while
only one is held. When the service says it cannot reach one volume's own
server, that volume is not asked again for two minutes, in Curve fit and
Field + fit, while the other volumes' cells are still corrected.

Both settings are per cell and survive reloads. The Viking coordinates in the
pick readout name where Viking draws the annotation, so they paste into
**Commands → Goto Location** at it.

Switching back to a setting the tab has already drawn the cell under draws its
annotations, traced outlines and skeleton from what the tab kept of them, without
asking the server again; so do the Undo of an unload and an **Update from Viking**
that found nothing changed. They are kept by the cell's annotations as Viking last
stamped them and by where the settings and any manual moves put them, so an edit
in Viking, another setting or a manual move is always asked for. A tab keeps about
3 million numbers of them (RPC1 c430, 897 outlines, is about 430,000), the least
recently used going first.

Neither corrects the cell itself: a jog the neighboring cells do not make is
the cell's own, and it stays.

## 3D render

Renders are tube meshes built from the disc annotations, or marching-cubes
surfaces built from closed-curve annotations.

| Method | What it does |
|---|---|
| Cylinder (Line3) | Per-annotation tangent frame, rings aligned by Nelder-Mead, hemispherical caps. The MATLAB default. |
| Tube (curveToMesh) | Variable-radius tube. Faster, no smoothing — smoothing degrades this path. |
| Closed curve volume | Rasterize each section's outline, stack every section at its own height, smooth, marching cubes. Sections skipped while tracing are interpolated from their neighbors when those are at most 1 µm apart; a longer untraced stretch stays open. Two outlines joined by a Viking link are always interpolated through the sections the link skips, so a branch the annotator carried across bad sections is whole even where the rest of the cell was traced there; two that do not overlap (an outline linked to a disc several microns away across untraced sections) are drawn as a run from one to the other, the way a link between two discs is. A slit across a branch that remains means the link is missing in Viking: add it there and refresh the cell. For cone pedicles and other contour-traced structures. A cell's discs join in as outlines of their own (all of it, for a cell traced only with discs; a cell with traced outlines and discs, the soma traced and the dendrites discs, gets both): each disc is its circle in its own section, and each link between discs on different sections also draws, on every section it crosses, the stretch of the process within one section of that plane, so a process running obliquely stays whole. Processes thinner than about two pixels are smoothed away, so a disc-traced cell wants a fine Pixel size: the raster is kept only where the outlines are, not over the cell's whole bounding box, so a wide cell fits one (Inferior c16137, 128 × 176 µm: one body at 0.1 µm or 0.05 µm; 38 at 0.184 µm, the finest its bounding box fitted until 2026-10-15). The raster's staircase (a soma that comes out as a stack of plates, a dendrite along the section plane with a row of teeth down its rim) can be smoothed: **Smooth surface µm** smooths the finished surface and moves no annotation; **Smooth discs ×r** and **Smooth outlines µm** move the annotations themselves first, by measured amounts; all three are off until turned up (*Averaging out the staircase*, below). |
| Hybrid (discs + curves) | The closed-curve volume unioned with a Cylinder build of the disc annotations — the only method that shows **every** annotation of a structure traced with both kinds. The disc half honors the pathway and its knobs; the curve half honors Voxel size. Under **Voxel remesh** and **Ultraliser** the two are **one surface** (since 2.7.0): the outline raster joins the discs' whole-cell field and is contoured with it, so where the annotator switched from outlines to discs (Inferior c1178's soma top) the wireframe is one sheet, not a disc-built shell sitting on the traced soma. Under the other pathways the halves are built apart and laid side by side, as before (*Hybrid is one surface*, below). |

**Mixed and contour-traced structures pick their technique automatically.**
A cell holding both discs and traced outlines — blood vessels, most
prominently — loads on the Hybrid technique; a cell traced only as closed
curves loads on Closed curve volume. Vessels rasterize their curve volume at
1 µm because they thread the whole volume (RC2's `c1` spans
661 × 731 × 1206 µm over 21,757 annotations — 15,834 outlines plus 5,922
discs); other structures rasterize at 0.25 µm. An attached render, or
anything you apply afterward, still wins.

**Long builds no longer wait inside one request.** Renders run as background
jobs on the server (and so do the error maps and Differences of Render
compare): the app starts the job, polls its progress (the busy line counts the
elapsed seconds), and collects the result whenever it finishes — so a
five-minute build is not cut off by the gateway's five-minute request limit as a
bare "504". It is not a promise that every build succeeds: a MeshLab step is
still stopped after `SBFSEM_MESHLAB_TIMEOUT_S` (30 minutes by default) and the
job fails with the stage named; the app follows a job through up to a minute
of failed status checks (a network drop, a restart) and then reports that it
lost contact, though the job may still finish on the server; and the mesh
downloads (GLB, OBJ and the other exports) still build inside one request.
Removing a cell mid-build abandons its job.

**A finished surface downloads for as long as it keeps arriving.** A fine
Voxel size can make a surface of millions of triangles (RC1 c9025 at 0.02 µm:
8.5 million, 154 MB, 50 MB on the wire), which takes a minute on a slow link.
The busy line and the progress card count it ("downloading the surface, 23 of
154 MB"). Only a download that stops arriving for 20 s is dropped and asked for
again; after three of those, the banner says **Download failed**: the surface
was built on the server, with its triangles and size, and downloading it
failed. The server keeps it, so Apply (or ↻ on the cell's row) asks for it
again without building it again; a coarser setting makes a smaller surface.

### Where the controls live

**Volume** sits at the top of the left column, above Load a neuron. The left
column is otherwise entirely about cells: loading them, and the settings of
each one.

The bottom of the 3D view is a **fixed toolbar of view actions** — Axes,
View cube, Pivot, Orthographic, Undo/Redo, Reset view, the named viewpoints, Snapshot
and Video — that never rearranges. In **Orthographic**, showing or hiding a cell
keeps the scale and where everything else is drawn (since 2.30.1: the scale used to
follow the shown cells' extent, so hiding the larger of two cells zoomed in 8x about
the pair and threw the other off screen), and **Reset view** takes back a zoom
there as well (it used to keep it). Restore and Undo put an
Orthographic view back at the scale it was saved at (since 2.32.1: a Restore came
back zoomed in by however much the cells' extent outgrew the empty scene's, 8.9x for
c943 beside c21768). Everything else lives in two expandable
panels opened from its left edge:

- **Render options** — opened by the ▲ arrow next to the technique picker.
  Method, pathway, every render parameter, the IPL clip, and the Apply /
  Defaults / Rebuild buttons. However many render knobs come and go, the
  toolbar buttons stay where your hand expects them.

  The **technique picker** beside that arrow is the shortcut for the one
  setting reached for constantly: Voxel remesh, Voxel remesh orig, Convolution
  surface, Ultraliser, Original (MATLAB), Adaptive + Taubin, Wrap (cube fit),
  Tube, Closed curve volume, Hybrid, or Voxel remesh put through MeshLab
  (isotropic, quadric or Poisson; grayed out on a server without pymeshlab).
  It names the MeshLab mode too: Voxel remesh with the MeshLab mode on reads
  *Voxel remesh + MeshLab …*, while Original with MeshLab on still reads
  Original. The same list names the techniques of `sbfsem-measure` and of
  Render compare's panes.
  With the panel
  **collapsed**, choosing one applies it to the selected cells immediately —
  no Apply needed. With the panel **open**, it only sets the dropdowns, so a
  technique can be dialled in alongside its parameters before anything
  rebuilds. A hand-tuned method/pathway pair the list does not name shows as
  *Custom…*.
- **Display & context** — the MATLAB `RenderApp` "Plot" tab: Invert, Flip Z,
  2D (it unticks itself when you orbit away from the straight-down view or
  switch to perspective), In-page dropdowns (see [Dropdown lists in a virtual machine](#dropdown-lists-in-a-virtual-machine)),
  Material, the colormap and its colorbar, the scale bar, the volume
  context (boundaries and vasculature), a typed viewpoint, and axis limits.

### Named viewpoints

**XY1**, **XY2**, **XZ1**, **XZ2**, **YZ1**, **YZ2** and **3D** sit next to
Reset view and look down one volume axis each with a fixed up vector, so a
cell photographed from "XZ1" today matches one photographed from "XZ1" next
month. The letters are the plane you see; **1** is that plane from the
positive end of the third axis, **2** the same plane from behind, mirrored
left to right:

| Button | Looking from | Right | Up |
|---|---|---|---|
| XY1 | +Z (the section plane, face on) | +X | +Y |
| XY2 | −Z | −X | +Y |
| XZ1 | −Y | +X | +Z |
| XZ2 | +Y | −X | +Z |
| YZ1 | +X | +Y | +Z |
| YZ2 | −X | −Y | +Z |
| 3D | the oblique direction Reset view uses | | +Z |

XY1, XY2, XZ (now XZ1), YZ (now YZ1) and 3D are MATLAB RenderApp's buttons
(`view(2)`, `view(0,-90)`, `view(0,0)`, `view(90,0)`, `view(3)`); XZ2 and YZ2
are new.

**A viewpoint button turns the view; it does not pull back.** Whatever is in
the middle of the picture stays there at the size it had: the camera swings
about the point on the view axis at the median depth of the surface in the
middle 70 % of the picture, and keeps its distance to it. Several buttons in a
row, with nothing moved in between, turn about the same point at the same
distance. Only **Reset view** frames the whole scene — so for the whole cell
along an axis, press Reset view and then the button. (The **2D** checkbox,
which looks down Z, follows the same rule.)

### View cube

The cube in the top-right corner of the 3D view, after the ViewCube in
Autodesk Fusion 360, stays where it is and turns with the view, so it always
shows which way the volume's axes run. Its faces are labeled with the axis
each one faces (**+X**, **−Y**, **+Z** …), and an X/Y/Z tripod in the axes'
colors runs from the corner nearest the volume's origin. **View cube** in the
toolbar, beside Axes, shows or hides it, here and in Render compare (on by
default; kept with the session).

- **Double-click a face** to look straight down that axis. The six faces are
  the six viewpoint buttons exactly, with the same up: +Z is XY1, −Z XY2, −Y
  XZ1, +Y XZ2, +X YZ1, −X YZ2.
- **Double-click an edge** for the 45° view between its two faces, and **a
  corner** for the view between three (the isometric view: 45° round,
  35.3° up).
  Edges and corners keep Z as upright as the view allows. The band along each
  edge of a face, a fifth of its width, is that edge, and the square where two
  bands meet is that corner. The part under the pointer lights up blue, and
  its name shows under the cube.
- **Drag the cube** to orbit, as a drag on the render does: about a quarter
  turn for the cube's own width.
- **The house** (top left, on hover) is Reset view.
- **Looking straight at a face**, four triangles around the cube turn to the
  face beside it on that side of the screen, and the two curved arrows at
  the top right turn the picture a quarter clockwise or counterclockwise.
  These take a single click.

A single click on the cube itself does nothing; turning takes a double-click,
so a click that was meant for the render never turns the view. Each turn
glides there, longer the further it goes (up to 0.6 s; at once with reduced
motion on), and pivots like a viewpoint button: what is in the middle of the
picture stays there at the size it had, and turns in a row, with nothing
moved in between, keep the same pivot. With a pivot point set (see
[Where a turn pivots](#where-a-turn-pivots-and-choosing-the-point)), the cube's
turns and the viewpoint buttons swing about that point instead, and it stays
where it is on the screen. Every turn and every drag is one
undo step. Under **Flip Z** the face on top is the volume's −Z and the
tripod's Z runs down, as the Axes helper's does. The cube is drawn over the
render, not in it: it is never in a snapshot or a video. **Render compare** has
the same cube in its top-right corner, turning every pane (see *Render compare*).

### An exact viewpoint

The Viewpoint group takes **Az** and **El** in degrees, in MATLAB's
`view(az, el)` convention — azimuth measured from the −Y axis, elevation
above the XY plane — plus the camera distance in microns. Typing
`-37.5 / 30` reproduces MATLAB's default 3-D view exactly. **Read view**
copies whatever is on screen back into the boxes, so you can orbit to an
angle you like and write the numbers down.

### Axis limits

Type a box in microns and press **Crop**: the scene is clipped to it live
(six clipping planes, so it is instant and reversible), which is how you cut
a slab out of a dense field of processes. **Read** fills the boxes from the
current scene, **Clear** removes the crop, and **Hold** stops a cell loaded
or built from re-framing the camera you have set, a first load's framing and the
framing of a view that shows no cell alike (the crop itself stays until Clear either way).
Hold lasts only as long as the page: it is off whenever the page loads, and Undo and Redo
carry it, but a saved session, a Restore and a workspace leave it as the page has it (since
2.30.2). Saved with the session, a Hold ticked once came back on every later visit and left
each new cell off screen until Reset view (Temporal c162392, 2026-10-22). A box left blank in **Crop** is the scene's own
extent on that side, and a box left blank in **Set view** keeps the current
azimuth, elevation or distance.

### Materials and colormaps

**Material** is chosen per cell, in the cell's Surface section, and survives
the rebuild a settings change triggers. The ten finishes are physically
based and each explains itself on hover: *Standard* (the default), *Matte*
(no highlights; MATLAB's "dull"), *Plastic*, *Glossy* (tight wet highlights;
MATLAB's "shiny"), *Metal* (reflects the surroundings rather than showing
its own color in the highlights), *Velvet* (felt or fuzzy: a soft rim of
light where the surface turns away and no hard highlight), *Clay*, *Glass*
(transparent and refracting; slow with many cells), *Glow* (self-lit in the
cell color, for picking one cell out of a crowd) and *Faceted* (flat
shading, so every triangle shows — the way to judge a mesh's resolution).
`→ selected` and `→ all` beside the menu hand the finish to other cells, and
*Edit…* opens it in the editor.

**The Materials group** in Display & context has a Material menu that works
like the Map menu: choosing a material gives it to the selected cells, or to
every cell when none is selected, and *Apply to selected / all cells* beside
it hands the current choice on. **The material editor** sits below it, under
*Edit materials…*. Pick a material, drag its properties — roughness, metalness,
highlight strength, clearcoat, sheen, glow, reflections, transmission,
refraction, iridescence, the sheen and highlight colors, flat shading — and
the selected cells (or every cell, when none is selected) preview it as you
go. *Save to library* stores it on the server under the name you give it;
the library is shared, so it appears on every user's Material menu (starred)
at once. Built-in presets cannot be overwritten or deleted, only copied;
your own entries can. *Apply to selected / all cells* assigns the chosen
material without editing it.
**Colormap** applies to cells rendered with *Color by IPL depth*: pick a map
from the same 18 the MATLAB `RenderApp` offered, in the same order — Spectral
(the default there and here), Bone, CubicL, RdYlGn, Gray, Haxby, Hsv, Roma,
Batlow, Hawaii, Devon, AntiJet, Parula, RedBlue, Viridis, StepSeq, Ametrine
and Isolum, with the colors MATLAB itself produced — invert it, and set
**Levels** to quantize it into discrete bands — banding is what makes a depth
gradient read as strata rather than a wash. Ametrine and Isolum are the two
MATLAB marked as safe for red-green color blindness.
Changing any of them recolors in place, with no server round trip. **Show
colorbar** puts the legend in the corner of the viewport. With Levels at 64 or
fewer the bar is drawn as that many flat blocks, in the colors the surface is
painted in (at 4 levels, four blocks); above 64 the bands are narrower than a
pixel or two and it is a smooth sweep.

**Gradients of your own.** *Edit gradients…* under the Colormap controls
starts from any map on the menu — one of the MATLAB maps, sampled at nine
stops, or a saved gradient — and lets you drag each stop's position, recolor
it, add and remove stops, reverse the run and space the stops evenly, with
the depth-colored cells and the colorbar previewing as you go (opening the
editor changes nothing on screen: the first edit starts the preview, so the
exact MATLAB map stays on the cells until then). *Save to
library* stores it under a name on the server and selects it; it is then on
the Map menu (starred) for every user, sessions remember it, and *Delete*
takes a custom gradient away again. The MATLAB maps cannot be deleted.

### Scale bar

**Show scale bar** draws a micron bar in the bottom-left corner. Left on
auto it follows the zoom and rounds to a legible length (1, 2 or 5 times a
power of ten); type a **Length** to pin it. In a perspective view the scale
is the one in the plane through the orbit target — the plane you are looking
at — so what it measures is what is in focus.

### Volume context

**INL boundary** and **GCL boundary** draw the fitted boundary surfaces the
MATLAB app showed: the same smoothed fit through the layer's markers that
the IPL depth coloring uses. **Blood vessels** draws every traced vessel
(Viking structure type 3) in the volume. All three are context rather than
subject, so they never take part in framing the scene, never swallow a click,
and share one **Opacity** slider. Boundaries need a volume with boundary
markers (Inferior Monkey has them; Cped does not), and the vessels are
decimated hard server-side — vasculature is 880,000 triangles of smooth tube
that looks identical at 15%.

**Flatten IPL 1:1** (since 2.33.0, James Anderson's request) draws every cell
with its volume's INL and GCL flat. Each point's Z becomes its IPL depth,
measured as Settings → *IPL depth measured* says, times the volume's median IPL
thickness along the layers' normals (Inferior Monkey 27.66 µm, RC1 15.6, Temporal
23.8, Nasal 28.7, RC2 42.1), so the INL lies at Z = 0 and the GCL at that
thickness in every volume, and X/Y:Z stays 1:1: Z reads as depth from the INL in
µm. One number per volume, so cells of a volume share one axis; where a cell's
own IPL is thinner or thicker than the median it is squeezed or stretched by that
ratio. The boundary sheets lie flat at 0 and at the thickness, and the skeleton,
synapse markers and vessels follow. Rings, outlines, the annotation wireframe,
the Position shift arrows, manual moves, markers and a cell's Z range belong to
sections, so they are not drawn while the view is flattened, and GraphApp turns
it off. Monkey volumes have their GCL at smaller Z than their INL, so a monkey
cell is drawn the other way up when flattened (Flip Z turns it back). The note
under the box gives each volume's thickness; a Snapshot carries the same lines
under the picture; an export of a flattened cell is flattened too, its file name
ending `-ipl-flat-<µm>um` and its comments (OBJ, PLY, STL header, GLB extras,
DAE) saying what Z is. The pick readout gives the depth from the INL. It starts
off at every page load and is off in Cped, RPC1 and RPC2, which have no boundary
markers.

### Volume offsets

Cells from different volumes share one scene, each in its own volume's
micron frame, so an RC1 cell and an Inferior Monkey cell land wherever their
coordinates put them, usually overlapping or far apart. With cells from two
or more volumes loaded, **Volume offsets** appears under Display & context
with an X, Y and Z shift in microns for every volume. **Side by side** lines
the volumes up along X, centered on the first in Y and Z, with a gap of 15 %
of the widest volume (at least 5 µm); the first volume stays put. **Align
selected somas** moves every other volume so the soma of its selected cell
sits on the soma of the first selected cell, for overlaying cells of one
type from different retinas. **Reset** puts every volume back in its own
frame.

Offsets move whole volumes, boundary sheets and vessels included, and never
the data: the pick readout, the Location ID, the Viking coordinates and every
export stay in each volume's own frame, and a jump to an annotation or a
Viking location lands on the shifted cell. Offsets are part of the session
and of a saved workspace.

### Recording a video

**Video…** records the 3D view to a file. Two motions: **Auto-rotate** spins
the scene about the screen-vertical axis (turntable), the screen-horizontal
axis (tumble) or the view axis (roll) for a set **Duration** and number of
**Turns**, then stops by itself; **Free** records until you press Stop, so
you can rotate by hand. The turn is stepped from the wall clock, so a
turntable comes back exactly to its start and loops cleanly.

**Format** defaults to MP4 (H.264): a WebM written by a browser carries no
duration in its header, which most editors dislike. **Quality** sets the
bitrate (2.5 to 40 Mbps) and **Frame rate** the output fps. The file is the
canvas at its current size, so make the window big before recording a figure.

Explanations are not printed on screen. Hover a control for about a second and
its help appears; move away and it goes. A quick skim across the bar never
opens one. A long explanation (the Pathway help is nearly 3,000 characters) opens
in two to four columns, as many as it takes to fit the window; only in a window
too small even for that does the panel scroll, and then you move onto it to
turn the wheel.

### Which controls each method uses

Not every control applies to every method, and the ones that do not are grayed
with a note rather than silently ignored (measured on c1178: the mesh is the
same, vertex for vertex, with the option changed):

| | Cylinder (Line3) | Tube (curveToMesh) | Closed curve | Hybrid |
|---|---|---|---|---|
| Smoothing (Passes) | yes, but **ignored** under Ultraliser, which smooths itself | **ignored** under Original (MATLAB skipped it for curveToMesh); **used** under every other pathway but Ultraliser (up to 5.2 µm of movement under Adaptive + Taubin) | **ignored** | as Cylinder |
| Decimate | yes | yes | **ignored** — use Voxel size | yes |
| Voxel size | — | — | yes | yes |
| Smooth discs ×r | yes (since 2.7.0) | yes (since 2.7.0) | yes | yes: its discs, before their tubes are built |
| Smooth outlines µm | — | — | yes | yes: its traced outlines |
| Smooth surface µm | yes | yes | yes | yes |
| Exact tubes ° | under Voxel remesh and Ultraliser | under Voxel remesh and Ultraliser | **hidden**: no tubes | its discs, under Voxel remesh and Ultraliser |
| Curve links µm, Join bodies µm | — | — | yes | **hidden**: Hybrid's discs are the Cylinder build's, which ties its own pieces (Tie gaps) |
| Bridge skips µm, Follow links µm (More menu) | — | — | yes | yes: its traced outlines and their links |
| Pathway, its knobs and Order | yes | yes | **ignored**: no pathway is used | yes |

A control the technique ignores also counts as unchanged: picking the technique a
cell already has, or **Defaults**, says *Those cells already use these settings*
instead of rebuilding an identical surface.

The pathway can gray controls too: under **Ultraliser**, **Passes** and **Order**
are grayed (with the reason in their help) in every method, because Ultraliser
smooths the surface itself (Smoothing, Smooth λ, Inflate) and has no generic
smoothing pass to repeat or to put before the decimation; Decimate still acts.

### The three stages, and what can be reordered

Building a mesh is three stages, and only the last two can swap:

1. **Generation** — rings are laid around the centerline. **Adaptive rings**
   belongs here. It is not a post-process and cannot be reordered: everything
   after it works on the mesh it produced.
2. **Smoothing** — Taubin, Legacy, or none.
3. **Decimation**.

**Order** swaps 2 and 3. Smoothing first works on the full-resolution mesh and
gives a much better surface; decimating first is cheaper because the smoother
then has fewer vertices to move. Measured on c1178 at 30% decimation: **26°**
soma faceting smoothing first against **53°** decimating first. Smooth first is
the default for that reason.

### The cell list

The left panel can be **widened**: drag the thin handle between it and the
viewport (double-click the handle to go back to the 300 px minimum); the
width is kept per browser, and everything in the panel — rows, search,
buttons — takes the extra room. In a window 720 px wide or less the panel is a
fixed 220 px, the handle is not there, and the render takes the rest; at 480 px
or less the panel sits above the render instead. A shut row is one line: chevron, eye, color
swatch, cell number, a two-letter volume tag (`im`, `tm`, `nm`, `cp`, `rc1`,
`rc2`, `rpc1`, `rpc2`), refresh, the selection box and unload. Opening a row
adds a second line with the volume spelled out, the technique the cell is
rendered with, and its label. The accent rail follows the tree: it runs
beside the open row's header, steps in beside the section heads, and steps
in again beside an open section's controls. Beside the **Loaded** heading
(or just under it, in a narrow sidebar): **Select all**, **Select none**, the
↻ button (**Refresh all cached renders** — re-fetch every loaded cell from
Viking and rebuild it), and **Clear all cells** (unload everything; Undo
brings them back). The heading and these controls stay at the top of the
sidebar while a long list scrolls under them. The selection count sits
centered under the list.

### Pathway

**Pathway** selects how the surface is built and finished. **Voxel remesh**
(the one-surface pathway, below) is the app's default for a fresh browser;
**Voxel remesh orig** is the piece-assembled pathway it grew out of. **Original** reproduces MATLAB
exactly and stays the default of the Python library and the bare mesh API, so
the port remains parity-first.

**Defaults are per technique.** Each technique has its own best parameters,
so "Defaults to selected/all" (in the **More ▾** menu at the end of the
strip) keeps the Method + Pathway you are on and resets only its knobs — to
your own saved values for that technique if you have any, to the recommended
ones otherwise. **Save technique defaults** remembers the current parameters
as yours for the current technique (in this browser); **Forget saved
defaults** discards them. Picking a technique in the toolbar quick-picker
loads that technique's defaults the same way.

Two further levels of preference stack on top. **Set as startup default**
makes the current technique *and* parameters what this browser opens with —
and therefore what newly loaded cells start from — instead of the app
default (Voxel remesh); **Clear startup default** reverts. That preference
lives in the browser. **Attach render to selected cell(s)** goes one step
further and saves each selected cell's current render settings to that cell
*on the server*: from then on, loading the cell — in any browser, by anyone
— starts it with its attached settings (re-attach any time to update them,
**Remove attached render** to detach). When both exist, a cell's attached
render wins over the startup default. The precedence, most specific first:
a restored session's per-cell settings, then the cell's attached render,
then the browser's startup default, then the app default.

The **More ▾** menu also holds the set-and-forget controls: **Order**
(smooth/decimate order), **Clip IPL** (server-side depth cut), the wrap
pathway's insurance knobs (Reject σ, Refits), and **Rebuild all**. The strip
itself stays one row of the knobs that actually get turned.

Measured on c1178 — soma faceting is the mean angle between neighboring
faces (whole-cell volumes are not compared here: the per-segment meshes
overlap, so their summed volume double-counts shared regions on every
pathway):

| pathway | faces | soma faceting | build |
|---|---|---|---|
| Original (MATLAB) | 193,280 | 37.1° | 3.5 s |
| Adaptive ring + Taubin | 407,370 | 20.3° | 4 s |
| Voxel remesh orig | 635,408 | **3.8°** | 5 s |

**Voxel remesh orig** rebuilds each thick segment from a signed distance field and
runs marching cubes over it, so the tessellation is set by the grid rather than
by the ring count. It is the only pathway that makes a soma actually round —
soma faceting measures ~3° against 34–37° on the other pathways.

**The field reads each annotation the way the annotator drew it: a disc in its
section plane, not a sphere.** Where consecutive annotations sit closer than
their own radii — 99% of the pairs inside a soma, traced section by section —
the surface is *lofted* through the discs, interpolating center and radius
between sections and capping flat where a run ends. Where annotations are far
apart relative to their radii (a dendrite), a swept round cone is the honest
reading and a capsule field is used. The choice is per consecutive pair, so a
segment running dendrite → soma → dendrite gets both, welded seamlessly.

An earlier version of this pathway used capsules everywhere, which turned
every soma disc into a full sphere bulging a radius above and below its
section — the union of wandering spheres looked like an amoeba. The loft field
instead reconstructs a cylinder, a sphere-of-discs and a lofted cone to within
1% of their true volumes, and a soma body ends at its annotated sections. The
loft applies only at soma scale (discs ≥ 1.5 µm): retinal dendrites run mostly
in-plane, where the honest reading of a dense annotation path is the swept
capsule the tube builders already use, and at dendrite radii a capsule's bulge
is invisible anyway. Tubes that attach to a lofted body do so at their thin
end's radius rather than capping the soma end with a duplicate ball.

**Envelope σ** (Voxel remesh, Voxel remesh orig and Ultraliser, which build their
thick bodies from these disc-loft fields; under Wrap it smooths only the lofted
pieces Wrap adds around its cube-fit bodies, and most of a Wrap surface does not
move with it) is the control that turns the literal disc stack into the smooth outer surface the tissue actually has. It is the
standard deviation, in microns, of a Gaussian applied to the distance field
before the surface is extracted — **per 5 µm of body radius**: annotators hold
one circle size for a dozen sections and then jump, so a 10 µm soma carries
~2 µm "wedding-cake" radius steps where a thin trunk carries fractions of a
micron, and the smoothing scales to match (c1178's 10.9 µm soma gets ~1.1 µm
at the default 0.5). Set it to 0 to see the literal annotations — every disc
edge and terrace, exactly as drawn. Past ~1 it starts to soften genuine small
features; at 2 thin attachments visibly detach, which is why the control stops
there.

The candidate techniques for "wrap messy geometry in a smooth outer surface"
were alpha wrapping, Poisson reconstruction, shrink-wrap projection, and
volumetric smoothing. The first three exist to *recover* a field from triangle
soup or point clouds; this pathway already owns an exact distance field, so
smoothing it directly is both the simplest and the most accurate option here.

An isolated soma-scale annotation whose neighbors are too far away for a loft
becomes a **lens** — its disc extruded half a typical section step — rather
than a sphere, so a single fat annotation no longer puts a ball on a soma.

**One field per fat cluster, not per segment.** A soma is not one segment:
the segmentation walks branches, so a soma's annotations are spread over
several rows (c1178's spans four). Every annotation at least 1.5 µm across,
anywhere in the cell, is clustered — adjacency in a row or overlapping in
space joins them — and each cluster gets exactly one distance field and one
marching-cubes pass. That is what makes a soma **one body**: no
interpenetrating per-segment shells, no flat "loaf" caps where a segmentation
row happened to end mid-soma. Everything thinner renders with the tube
builders (their sub-micron facets are invisible), each thin stretch anchored
one annotation deep into the envelope so it visibly connects. Tube ends are
capped on this pathway, so no open pipes. A single grid over a whole cell is
still not an option — the soma is 62× the radius of the thinnest dendrite —
but a cluster grid is a ~30 µm box at half a micron per voxel, which is why
this pathway is now also the fastest of the three on soma-heavy cells.

**Tie gaps ≤** (every pathway that assembles pieces: Voxel remesh orig, Wrap, Voxel
remesh, Convolution surface and Ultraliser; Original and Adaptive have none to
tie) keeps one cell one visual body. The
pieces these pathways assemble — cluster envelopes, rescue lofts, tubes —
connect only where their surfaces actually meet, and three things break that:
a rescued stretch's boundary pair goes unrendered, a tube's anchor can land
outside a surface the Envelope σ pulled inward, and Viking itself holds
same-cell branches nobody ever linked (c1178 has seven unlinked annotation
components; its arbor floated 3.3 µm from its soma). After assembly, any
remaining gap up to this many microns (default 20) is closed with a thin
0.25 µm tie wire along the shortest surface-to-surface crossing — a tie
states connectivity, it has no girth of its own. Wider gaps stay honestly
open and are logged. Set 0 to see the raw detached pieces.

**Voxel remesh** answers a complaint the metric above cannot see. Every
pathway so far *assembles* a cell from pieces — one marching-cubes envelope
per soma-scale cluster, an explicit ring tube for each thin stretch, links,
tie wires — and concatenates them. Pieces connect only where their surfaces
happen to meet, so a dendrite leaving the soma is a tube whose capped end sits
inside the envelope: one visual body, but two shells, and a wireframe or a
translucent material shows the tube as a separate object pushed into the soma
(c106 in Temporal Monkey is 27 topological components on Voxel remesh orig). No
concatenation can be one surface.

Voxel remesh makes the cell **one field**. The cluster and rescue fields
are exactly what Voxel remesh orig computes — lofted discs, Envelope σ, volume
compensation — kept as fields and resampled tricubically; every thin stretch
becomes a chain of exact round cones through its discs; links, stranded coins
and ties are stamped into the same field; and the union is contoured *once*,
on one whole-cell grid at dendrite resolution. Where a tube meets the soma the
two fields merge, so marching cubes emits a single connected sheet with a
crease along the true intersection curve. c106 comes out as **1 component**
(27 before), c1178 as 1 (299 before, with its four-row soma, rescue lofts and
the ties across Viking's seven unlinked components all in the same sheet).
The grid is never materialized: the field is evaluated in 32-voxel blocks
that some primitive's bounding box touches, each block is marched and
discarded, and blocks weld exactly along shared faces because they see the
same sample values there.

Its knobs sit in the strip when the pathway is chosen. **Fine voxel** is the
grid's voxel in microns; 0 (auto) takes two voxels across the thinnest 2 % of
the annotations' radii, never under 0.04 µm. Typed or not, it coarsens when
the raw sheet would exceed three times the face cap (at most 10 million
triangles, which is also the budget with no cap; it was 3 million until
2026-10-16) or the build would need more field evaluations than its budget
(60 million; 20 per triangle of a raw budget over 3 million, so 200 million at
10 million). Halving it is ~4× the triangles
and 3–4× the time; the deviation from the annotations barely moves (c106:
0.092 µm at 0.05 and at 0.10). **Face cap** decimates the smoothed sheet to
this many triangles (default 400,000; 0 keeps all): a uniform grid spends
triangles evenly, and quadric decimation takes them back off the flat soma
while keeping the thin arbor. **Junction fillet** rounds every place two
bodies meet with a smooth minimum of that radius; 0 is the exact union. Above
0 it also webs together surfaces of the same cell that pass within that
distance, and the fillet counts as unsupported surface in the metric.
Envelope σ and Tie gaps apply as on Voxel remesh orig.

Measured with `sbfsem-measure` (which now also reports the exterior-only
deviations, see below):

| cell | pathway | components | faces | build | peak MB | mean dev | hidden area | exterior surface | exterior rims |
|---|---|---|---|---|---|---|---|---|---|
| c106 (Temporal) | Voxel remesh orig | 27 | 19,036 | 0.3 s | 148 | 0.077 µm | 22 % | 0.077 µm | 0.139 µm |
| c106 (Temporal) | Voxel remesh | **1** | 323,256 | 2.3 s | 384 | 0.092 µm | 0.1 % | 0.088 µm | 0.123 µm |
| c1178 (Inferior) | Voxel remesh orig | 299 | 221,564 | 4.2 s | 282 | 0.247 µm | 36 % | 0.314 µm | 0.372 µm |
| c1178 (Inferior) | Voxel remesh | **1** | 400,000 | 5.8 s | 1,104 | 0.347 µm | 0 % | 0.371 µm | 0.482 µm |

**The raw deviation prefers the piece-assembled render, and much of that is
an artifact of the metric.** Surface buried inside the render itself — a
tube running on through the soma, a cap inside a parent tube — is invisible,
yet it sits among the annotations it is buried in and scores ~0 in the
surface direction; the assembled c106 hides 22 % of its area that way, the
c1178 assembly 36 %, the single sheet none. `sbfsem-measure` therefore
reports `ext` (surface the mesh does not enclose, by a signed ray-crossing
count) and `annext` (rims the mesh does not enclose) beside the raw columns,
with `hidden` as the buried share. On those, the two pathways are within the
metric's own resolution of each other on c106 (surface 0.077 vs 0.088 µm,
rims 0.139 vs 0.123 µm). On c1178 the exterior surface mean still favors
Voxel remesh orig by 0.06 µm — its assembly carries 17 % more exterior area, most
of it thick stray coins and tube caps sitting right on their annotations —
while per annotation Voxel remesh's surface is closer to the exterior disc
rims in every radius class (0.5–1 µm discs: 0.048 vs 0.106 µm; 1–1.5 µm:
0.093 vs 0.138 µm). The pooled `annext` there is dominated by c1178's 130
closed-curve annotations, which no disc pathway renders (Hybrid does).
Voxel remesh is not more faithful than Voxel remesh orig by this metric; it
renders the same geometry as one object, at 1.4–7× the build time and 2.6–4×
the memory. One reading does differ: a run of stacked discs whose centers
travel sideways farther than the run is tall (and farther than a disc
radius) is an oblique process seen section by section, not a body, and
Voxel remesh reads it as round cones where Voxel remesh orig still lofts it
into a slanted plate — the wedge with a shelf under it where c118's trunk
left its soma (now a round taper merging into the soma), and a 1.6 µm
process of c1178 running 14 µm horizontally. The disc metric prefers the
plate by a hair (c118 0.102 vs 0.108 µm), because it takes every circle for
a horizontal section of the body, which for an oblique process it is not.
Its cost scales with the arbor's surface area over the voxel
squared, and a huge cell is coarsened to the budgets (and says so in the
build log and `mesh.meta`) rather than refused.

**A finer Voxel remesh than the default** needs both a small Fine voxel and a
Face cap that lets the raw sheet be that large, because the sheet may be only
three times the cap (the default 400,000 allows 1.2 million triangles): set the
Face cap to 0, or over a million, and type the voxel. The limit on the raw
sheet is 10 million triangles (3 million until 2026-10-16), and the evaluation
budget grows with it, because the triangle limit alone changed nothing — c1178
came out at 0.0745 µm instead of 0.076 µm, the same surface, as the evaluations
ran out first. Measured on Inferior c1178 (2,304 annotations, 4,090 µm² of
surface), `sbfsem-measure`, on a Mac that was also doing other work, so the times
are indicative; the mean deviation from the annotations is 0.59 µm at every row,
and the capped rows were measured again for 2.19.4 (2.19.3's in parentheses):

| Fine voxel (used) | Face cap | raw → final faces | build | peak MB | field evaluations |
|---|---|---|---|---|---|
| auto (0.076), at the old limit | 0 | 2.13 M | 13 s | 1,256 | 45 M |
| 0.06 | 0 | 3.37 M | 19 s | 1,811 | 77 M |
| 0.05 | 0 | 4.85 M | 31 s | 2,467 | 120 M |
| 0.045 | 0 | 6.00 M | 40 s | 2,971 | 157 M |
| 0.04 (0.0414, the limit's finest) | 0 | 7.10 M | 60 s | 3,467 | 194 M |
| 0.04 (0.053, a 6 M budget) | 2,000,000 | 4.3 M → 2.0 M | 26 s (71) | 2,249 (3,842) | 102 M |
| 0.045 | 5,000,000 | 6.0 M → 5.0 M | 42 s (54) | 2,658 (5,106) | 157 M |
| 0.04 (0.0414) | 3,400,000 | 7.1 M → 3.4 M | 45 s (78) | 3,135 (5,615) | 194 M |

Peak memory is about 310 MB plus 0.445 MB for every 1,000 raw triangles. A Face
cap of a million or less that decimates the sheet (a sheet over its cap) adds
about 0.37 MB more for every 1,000 raw triangles, because the decimator holds a
second copy. A cap over a million, the only kind that lets the sheet past 3
million, decimates it a chunk of up to a million raw triangles at a time since
2.19.4, the triangles along each cut locked so the chunks weld back exactly and
each chunk's share of the cap following the error its edges start with: the
decimator then holds one chunk, and the capped rows above peak under the 3,467 MB
of keeping every triangle. On the server the same builds take about half the
memory they did, 1.5 to 7 % slower, with the same deviation, creases and one
body (c1178 at 0.042 µm under 3.4 million 3,132 → 1,564 MB; at 0.07 µm under 1.2
million 1,401 → 815; c16137 at 0.04 µm under 1.2 million 1,473 → 823). Since
2.19.5 the raw sheet's own steps (the weld of the blocks, the tie step, the
smoothing, the join of the decimated chunks) hold no copies they do not need, and
the surface is the same to the bit: on the server c1178 at 0.042 µm under 3.4
million peaks at about 1,100 MB (1,450 in 2.19.4), uncapped at 954 (1,413), and at
0.07 µm under 1.2 million at 725 (819); what is left over the sheet is mostly
the decimator's own arrays. Since
2.19.2 the build gate weighs every Voxel remesh, Convolution and Ultraliser build
at least at the sheet it is sized to draw, the one the render estimate names:
before, a sheet under 3 million was weighed by the cell's annotations alone, and
RPC1 c430 at a Face cap of 1,460,000 (2,582 MB) and Inferior c1178 at 1,100,000
(2,535 MB) were weighed at 2,546 and 1,451 MB; now 3,869 and 2,978 (both caps
are over a million, so since 2.19.4 they decimate in chunks: c1178's peaks at
1,576 MB here and is weighed at 2,267). So the heaviest request the page can
make, a cap of 3.4 million or more on a cell that fills the 10 million, is about
3.1 GB on c1178 (5.6 before 2.19.4) and is weighed at 5.3 GB by the build gate
(8.5 before), which leaves the server's 9 GB build budget room for another build;
Ultraliser at the finest voxel peaked at 3,305 MB and 119 s (7.1 M faces), and
Convolution surface, whose evaluations are counted on another scale, gets finer
too (c1178 at a typed 0.06 µm: 0.152 µm, 561,000 faces, 24 s, 540 MB, against
0.245 µm, 215,000 faces, 19 s, 320 MB at the old limits). A finer voxel does not
bring the surface closer to the annotations; it brings more triangles on the
same surface.


**Convolution surface** is a published implicit-surface method for neuron
skeletons (*Robust quasi-uniform surface meshing of neuronal morphology using
line skeleton-based progressive convolution approximation*, Frontiers in
Neuroinformatics 2022, doi:10.3389/fninf.2022.953930). A finitely supported
quartic kernel, f(r) = (1 − r²/R²)² for r ≤ R, is integrated along every
skeleton segment with support R = 2d for a segment of radius d and a weight
λ = 15TR⁴ / 16(R² − d²)^{5/2}, which makes an infinitely long straight
neurite of radius d exactly the T iso-surface; the contributions of all
segments are summed and the surface is the level set of the sum. Because the
field superposes, branch points and the soma blend into crease-free
junctions — the c118 trunk leaves its soma as a smooth trumpet — where the
voxel pathways meet along an exact intersection curve. The same superposition
is its cost in fidelity: a soma's kernels reach two radii out, so the soma is
rounded over that distance and its flat annotated top becomes a dome above
the last disc, and neurite ends taper toward a point. The paper's meshing
(progressively deforming a sphere along the skeleton with remeshing) is not
reproduced; the field is contoured on the same block grid as Voxel remesh,
after merging our section-by-section samples into segments at least a
quarter of a kernel radius long (the paper's own elimination of trivial
segments, and what keeps a 150-disc soma from costing 150 kernels 8 µm
wide). Same-section jumps are still links at 0.75 µm and stranded discs
become spheres of their radius. **Kernel R/d** is the one knob (the paper's
value is 2); Fine voxel, Face cap and Tie gaps apply.

Kernel R/d stops at 2.5 in the panel (2.7.1). The weight is set for an infinitely
long neurite, so anything shorter than its kernel's reach sums to less than the
iso-value: ends taper, and a soma, about as tall as it is wide, shrinks as the
kernel widens. The mean distance from each annotation's rim to the surface, by R/d:

| cell | 1.6 | 2 | 2.5 | 3 | 4 |
|---|---|---|---|---|---|
| Temporal c118 | 0.177 µm | 0.209 | 0.389 | 0.735 | 1.225 (soma gone: 0 of 67 soma discs inside) |
| Temporal c106 | 0.261 | 0.290 | 0.348 | 0.433 | 1.029 (1 of 10 soma discs outside) |
| Inferior c16137 | 0.274 | 0.357 | 0.473 | 0.594 | 0.859 |
| Inferior c943 | 0.225 | 0.281 | 0.370 | 0.456 | 0.673 |

At mid-height of a stack of discs as tall as it is wide the field on its axis is
1.63 T at 2, 1.04 T at 2.5 and 0.78 T at 3, where nothing of it is left. 1.6 is closer
to the annotations than the paper's 2 on all four. The API and a page open from
before 2.7.1 can still ask for up to 4.

**Ultraliser (voxelized proxies)** follows the Blue Brain pipeline for
watertight neuron meshes (Abdellah et al., *Briefings in Bioinformatics*
2023, doi:10.1093/bib/bbac491): proxy geometry is voxelized — conservative
surface voxelization, then 2D flood filling per slice for the interior — into
an inside/outside grid, contoured with marching cubes or dual marching cubes,
and optimized by Laplacian smoothing with an inflate step ("to dampen the
shrinking effect on thin parts"), flat and dense coarsening, and normal
smoothing. Here the proxies are the same pieces Voxel remesh builds — round
cones through every thin annotation pair, the disc-loft fields for the soma
and thick stretches — binarized on the block grid (a voxelizer knows only
inside and outside, so the contour lands on the half-voxel staircase),
contoured with marching cubes, then smoothed by **Smoothing** iterations of
**Smooth λ** toward the neighbor mean followed by **Inflate** back out, and
coarsened to the Face cap by quadric decimation (standing in for the flat and
dense coarsening). **Soma proxy** *sphere* substitutes Ultraliser's own
starting soma, an icosphere of the mean radius (its pull toward the neurites
is not reproduced), so the skeleton-tool reading of our soma sections can be
seen; *discs* is our reading. Ultraliser's default resolution is 5 voxels per
micron (0.2 µm; set Fine voxel to 0.2 to see it); the auto grid here is finer,
sized from the annotations. Dual marching cubes and the MeshFix-based
watertightness repair are not reproduced; the block grid's contour is
watertight by construction.

Measured against the annotations (`sbfsem-measure`):

| cell | pathway | components | faces | build | peak MB | mean dev | exterior surface | exterior rims |
|---|---|---|---|---|---|---|---|---|
| c106 | Voxel remesh | 1 | 323,256 | 2.0 s | 414 | 0.092 µm | 0.088 µm | 0.123 µm |
| c106 | Convolution surface | 1 | 125,108 | 3.2 s | 277 | 0.276 µm | 0.274 µm | 0.221 µm |
| c106 | Ultraliser | 1 | 323,256 | 2.3 s | 506 | 0.092 µm | 0.088 µm | 0.124 µm |
| c118 | Voxel remesh | 1 | 400,000 | 6.8 s | 1,117 | 0.108 µm | 0.112 µm | 0.110 µm |
| c118 | Convolution surface | 1 | 168,884 | 3.8 s | 331 | 0.199 µm | 0.195 µm | 0.262 µm |
| c118 | Ultraliser | 1 | 400,000 | 8.0 s | 1,080 | 0.109 µm | 0.113 µm | 0.119 µm |

Ultraliser reproduces Voxel remesh's solid to the metric's resolution (it
is the same union, binarized and smoothed back); what it adds is the ring
texture the section terraces leave in a binary grid, and what it removes is
the crease at junctions only as far as the smoothing reaches. The
convolution surface is two to three times farther from the annotations,
almost all of it the soma's rounding and the tapered ends; it is the pathway
to pick when a smooth, publication-style whole cell matters more than
fidelity to the discs.

**Ring detail** (Adaptive ring + Taubin and Wrap, whose thin tubes and fit input
are adaptive tubes) sets the target spacing between ring
vertices in microns (default 0.35, clamped to 8–128 vertices per ring). Note
that no ring setting can remove the tube methods' self-intersections: a swept
tube through a wandering soma path folds through itself by construction, which
is why the surface shows impossible interpenetrating planes there. The voxel
pathway is the structural fix for somas; adaptive rings makes the tubes'
facets finer, not their topology sound.

**Wrap (cube fit)** is an experimental fourth pathway. It takes the
adaptive tube mesh of each fat cluster and fits a smooth outer envelope
through its vertices: axis-aligned cubes are laid where the mesh has
vertices, each cube fits a local plane with outlier rejection (samples more
than **Reject σ** standard deviations from the fit lose their vote,
**Refits** times over), and the blended fit is extracted as a single outer
surface — interior fold walls are detected against the z-clamped capsule
union of the annotation path and never fitted, and that union is also the
fit's floor, so the wrap is never thinner than the tubes it wraps. **Cube
size** sets the resolution and **Blend** the neighborhood reach.

Since the honesty pass, samples that stray past the z-clamped capsule union
(the vertical-ring bulges a wandering soma stack sweeps below its lowest
section) lose their vote, and an isolated fat annotation is sampled as its
coin, never its ball — so the wrap now ends where the annotations end, the
same discipline as Voxel remesh orig.

**Recommended settings**, from a parameter sweep on c1178 (the metric is
surface more than 1.5 µm outside every annotated disc — lower is more
honest):

| Cube size | Blend | Reject σ | Refits | verdict |
|---|---|---|---|---|
| **0.4** | **2.0** | **2** | **2** | the defaults — best honesty per second |
| 0.4 | 1.6–1.8 | 2 | 2 | marginally tighter, worth trying on a messy soma |
| 0.4 | 3.5 | — | — | avoid: smears the fit outward (~4× the stray surface, +7% volume) |
| 0.3 | 2.5 | — | — | slower and no more honest than 0.4 |
| 0.6 | 2.5 | — | — | visibly coarser (p99 excess 1.2 µm vs 0.7) |

Reject σ (1.5–3) and Refits (1–3) barely move the result on clean cells;
they are insurance for noisy junction data, so leave them at 2/2 unless a
junction looks torn.

### Adaptive rings

Every ring got a fixed 10 vertices regardless of radius, so a 10.9 µm soma was
a 10-sided prism with 6.9 µm facets while a dendrite 62× thinner got the same
10 points. Adaptive rings scales the count with the local radius (8–128, target
~0.35 µm between vertices).

Measured on c1178, soma faceting — mean angle between neighboring faces:

| | roughness | volume |
|---|---|---|
| off, no smoothing | 42.1° | 100% |
| off, Taubin ×2 | 39.3° | 98% |
| **on**, no smoothing | 24.4° | 106% |
| **on, Taubin ×2** | **19.8°** | 105% |

Turning it off reproduces the MATLAB geometry exactly, which is what the parity
tests assert. The volume rising above 100% is not an error: a polygon inscribed
in a circle under-estimates its area, so more points converge on the true
volume from below.

### Smoothing

**Taubin** is the default. It alternates a shrinking pass with a slightly
larger re-expanding one, so it attenuates facets without collapsing the mesh —
98% of the volume survives two iterations.

**Legacy (MATLAB)** is matGeom's `smoothMesh`, exactly what the original did: a
full-weight Laplacian that discards **37% of the volume on its first pass and
96% by the tenth**, and pulls the seams between tube segments apart by microns
because the segments are separate connected components. It is kept for parity,
not because it is useful. MATLAB skipped it entirely for the tube method;
Taubin runs on both.

More passes cost almost nothing — It is close to
free: the vertex adjacency is built once and each pass is a single sparse
multiply, so ten iterations cost about 2% more than one.

**Decimate** keeps that share of the mesh's triangles and collapses the rest.
The shape barely changes — what shrinks is the download and how much your
graphics card has to draw. A 10,000-annotation cell is 829,000 triangles at
100% and 249,000 at 30%. Reach for it when a cell is slow to appear, or when
you are showing several at once.

**The expensive choice is the method itself, not smoothing.** Cylinder (Line3)
fits a tangent frame per annotation and aligns each ring by Nelder-Mead, which
is roughly 25× the work of Tube: 17 s against 0.6 s for an 11,000-annotation
cell locally, and about five times that on the server. The panel estimates the
build time for the selected cells and warns when it runs long.

**Voxel size** appears for the closed-curve method and sets the rasterization
resolution. 0.05 µm is the default and is already finer than the 0.07-0.09 µm
section spacing; 0.1 µm makes a large pedicle roughly four times lighter to load,
and 0.025 µm is rarely worth the payload. Loading a whole cell traced with
outlines, or picking Hybrid or Closed curve for it from the technique picker or
a Defaults button, uses 0.25 µm (1 µm for vessels). The server holds the raster
only where the outlines are (up to 300 million voxels) and refuses a surface of
more than 10 million voxel faces (up to 19 million triangles, about 6.8 GB of
mesh being built, and some 35 bytes of JSON for each triangle to download); a
pixel too fine for either is coarsened to the finest one that fits (RPC1 c2628
has 2.3 million faces at 0.05 µm and 6.8 million at 0.025 µm, which builds in
half a minute, is 350 MB to download and 0.6 GB in the tab; 0.021 µm is its
finest. The limit was 3 million until 2026-10-16, and 0.044 µm its finest).
Either change is announced in the viewport's upper right.

Since 2026-10-16 a size is typed into the box as well as picked from the list
beside it (the arrow): anything from 0.005 µm (the native pixel of the Temporal
and Nasal volumes, MATLAB's own was 0.01) to 1 µm; Enter or leaving the box sets
it, Escape or anything it cannot read puts the size back, and ArrowUp and
ArrowDown step through the named sizes. As the size changes, the cost chip at the
end of the row shows the server's estimate for the selected cells before anything
is built — "≈ 9.6 M tri · 3.6 GB · 1 min 14 s" is RPC1 c2628 at 0.025 µm:
triangles, the server's memory (the build plus sending it) and the production
server's seconds; the reasoning, cell by cell, is in its hover. It turns amber
past a minute, past half the memory the server gives builds (such a build waits
for the others and runs alone) or past 512 MB in the tab, and red past a limit,
naming the finest size that fits. Besides the voxels and the faces, the page has
a limit of its own: a tab cannot read a mesh of more than about 537 MB of text
(V8's longest string; Chrome 148 parsed 536 MB and failed at 537), which Inferior
c16137 would pass at 0.011 µm with fewer faces than the server allows. Apply,
loading a cell and Render compare build each cell at the finest size that fits
*that* cell, so a small cell keeps a size a large one cannot, and a notice names
each cell and its size; when every selected cell ends at one size the box shows
it. The estimate is made from the outlines without building (`docs/api.md`,
`render-estimate`): the voxels exactly, the faces from one raster of the cell at
0.1 µm (or its last build) scaled as 1 / size in the planes and 1 / size² between
sections, within 2 % on c2628, c16137 and RC1 c410; the first estimate of a cell
takes a few seconds, the next a fraction of one. A Hybrid under Voxel remesh or
Ultraliser on a cell with traced outlines is one surface, its outlines drawn into
the discs' whole-cell sheet (2.7.0), and since 2.20.2 its estimate is that sheet's:
the memory the server's build queue gives it, planned as the build plans it, and the
triangles the build keeps (its Face cap and the outlines' own). Before, it was the
two halves' prices added: RPC1 c2628 at the default settings showed about 1 GB
where its build needs 2.8 GB.

**Decimate** thins
the finished mesh, which is the other lever if a render is slow to transfer;
an export (OBJ, PLY, STL, GLB) takes the file and not the page's copy.

**Color by IPL depth** shades each vertex by its stratification depth: blue at
the INL border, red at the GCL border. **Clip IPL depth** cuts the mesh at a
depth fraction, keeping the ON or OFF side. Both need the volume to have cached
IPL boundary markers, and both measure depth as Settings says (*IPL depth
measured*): along the layers' normals unless it is switched to the column.

### Settings that will not build as set

Since 2.18.0 the render panel says so before Apply. The controls to change turn
black with red text, and a row at the top of the panel says why, cell by cell, and
lists what to change, best first. Each fix is a button that puts its settings into
the controls; nothing is built until Apply, and Apply as set still builds (see the
row's last line). The server answers it from the selected cells' estimates
(`render-estimate`, `docs/api.md`), worked out the way the build works them out,
without building:

- **Closed curve and Hybrid: a Voxel size past a limit** (the raster's voxels, its
  voxel faces, the server's memory, the page's text). **Voxel** is marked; the first
  fix is the finest size that fits every selected cell (each cell's own is named).
  When the limit is the server's memory and **Join bodies** or the smoothing (Smooth
  discs, Smooth outlines) is on, each of which only adds memory, it is marked too, and
  the fix with it off names the finer size that then fits. Applied as set, each cell
  is built at the finest size that fits it, as before, and a notice says which (in
  expert render mode, as set, which the server refuses).
- **Voxel remesh, Convolution and Ultraliser: a typed Fine voxel the build would
  contour coarser.** The whole-cell grid's voxel is held to the raw triangles the
  **Face cap** allows (three for each one kept, at most 10 million), to the field
  evaluations those allow, and to 0.02 µm, the finest a build takes. **Fine voxel** is
  marked, and **Face cap** when a larger cap would help. The fixes: the least Face
  cap that builds the voxel as typed (to 10,000 triangles), Face cap 0 (every triangle
  kept, the 10 million budget), and the Fine voxel the cap holds the cell to, each
  with the raw triangles and the build's memory (about 310 MB plus 0.445 MB for every
  1,000 raw triangles, and 0.37 MB more under a cap, which keeps a second copy to
  decimate). When no cap reaches the typed voxel, the finest any budget allows is
  named. An automatic Fine voxel (0) the cap holds coarser than the cell's annotations
  ask is not marked, since that is what the cap is for; the cost chip's hover says
  which cap would build it at theirs.

On 2026-10-20, for example: RPC1 c430 under the default Voxel remesh is built at
0.076 µm, where its annotations ask for 0.040 µm (the default cap allows 1.2 million
raw triangles; c430 has about 2,000 µm² of surface); Face cap 1,460,000 builds it at
0.040 µm (4.4 million raw triangles decimated to 1.46 million, about 3.9 GB) and Face
cap 0 too (all 4.4 million kept, about 2.3 GB, as nothing is decimated),
and the finest any budget allows it is 0.030 µm. Inferior c1178 is built at 0.120 µm
under the default cap and can go no finer than 0.042 µm, where it fills the 10 million.

### When a render fails

A render can fail because of its settings: it builds nothing (a disc technique on a
cell whose annotations no link joins, or Closed curve on annotations narrower than
about two voxels), the server refuses it (an outline raster too fine), the build runs
out of the server's memory, or it breaks. Since 2.26.0 what happens next is the
**expert render mode** checkbox's, in Settings (*Rendering*).

**Off (the default): the app builds settings that work.** It builds the cell again
with the next of these that may draw it, and the cell keeps the first that does:

1. what mends that failure in the same technique: the Voxel size the server says
   fits, a Voxel size twice as coarse or the default Fine voxel and Face cap after
   running out of memory, a finer Voxel size (0.05 µm) when an outline method built
   nothing;
2. the technique's defaults (the ones you saved for it, else the recommended);
3. Voxel remesh, the app's default;
4. Hybrid, for a cell with traced outlines;
5. Closed curve at 0.25 µm (1 µm for vessels), which draws every annotation, linked
   or not, as its own disc or outline in its section, then at 0.05 µm.

Disc techniques draw nothing from annotations no link joins, so 2 and 3 are skipped
for a disc technique on such a cell (measured on 2026-10-21: one disc, or three
unlinked, built 0 faces under every disc technique; Closed curve drew a disc of radius
0.85 µm at 0.25 µm, and one of 0.26 µm only at 0.05 µm). A notice in the upper right
names the settings used and why (*c62511 is drawn with Closed curve volume, Voxel
0.05 µm: Voxel remesh orig built nothing (1 annotation, no links)*), and its **Build
as I set it** builds the cell with its own settings once, which then fail and are
marked as below. The cell's row shows `auto`, its hover saying the same, and the
render panel shows what the cell is drawn with. The cell keeps these settings until
its annotations change in Viking: loaded or refreshed with new data, it is built as
asked again (and falls back again if that still fails). Applying settings to it asks
anew. Only when nothing draws it does the banner say so: *no technique could build a
surface from c62511 (1 annotation, no links); load it again (↻) once more of it is
traced*. A failure that is not the settings' (the network, a restart) is never
answered with other settings; the banner says to try again. Render compare builds its
panes as set either way.

**On: settings are built as set**, even when a cell cannot be built with them. The
cell is left without a surface and the banner says so; while the render panel holds
those settings for the selected cell, the controls to change are marked black with red
text, under any technique, and the row at the top of the panel lists the same ways to
fix it, best first: the technique (**Method**, **Pathway**) for a build that came back
empty or broke, **Voxel** for an outline raster refused or out of memory, **Fine voxel**
and **Face cap** for a whole-cell field out of memory. A fix puts its settings into the
controls, and Apply builds them.
How long the server takes to say so, on production: 2.7 to 3.1 s for a cell's first
answer (c430, c1178), 0.7 to 0.9 s for each new Fine voxel or Face cap, and nothing for
an answer already given; the marks appear when it comes. Render compare's options
panel marks a layer's settings the same way, for the cell the picker names, and a
fix there changes that layer only (*Render compare*).

A build cut short by a restart of the server (every deploy restarts it) is no longer
reported as one that ran out of memory with advice to ask for a coarser setting
(c1178 at 0.05 µm on 2026-10-20, a 1.2 GB build): the page waits out the restart and
asks for it again.

### Hybrid is one surface

Until 2.7.0 Hybrid built its two halves apart, the traced outlines as a Closed curve
surface and the discs as a Cylinder build, and laid them side by side. They met
wherever the annotator switched from one to the other, and there each kept its own
sheet: on Inferior c1178 (130 traced outlines over its soma, 2,304 discs) the soma's
top, drawn with discs above the last traced section, was a closed shell sitting on
the traced soma's flat top, and the process leaving the soma's left end was a disc
tube laid over the end of the traced stub. Five sheets in all, one of them 6 % of
the surface hidden inside another.

Under **Voxel remesh** (and Ultraliser) the outline raster is now a field like the
discs' own (`unify._CurveLeaf`: the same smoothed occupancy whose 0.5 level the
outline half's surface was, read at every point of the whole-cell grid), and the
union is contoured once. c1178 at Voxel 0.1 µm:

| | sheets | triangles | hidden surface | creases per µm² | build | peak memory |
|---|---|---|---|---|---|---|
| before (two halves) | 5 | 829,988 | 6.1 % | 0.370 | 6.7 s | 1,048 MB |
| one surface | 1 | 829,988 | 0.2 % | 0.196 | 7.0 s | 1,399 MB |

The outline half's own triangle count is added to the Face cap, so the cell keeps the
detail the two halves had; the grid is sized so the discs keep the voxel they would
have had alone, and an automatic Fine voxel is never coarser than the Voxel size.
The mean deviation from the annotations rises by 0.009 µm because the hidden surface,
which sat among the annotations and scored about 0, is gone; the exterior figure
(`ext`) is unchanged. Convolution sums kernels, which a raster has none of, and the
piece pathways have no field, so under those Hybrid is still the two halves side by
side. Only a Hybrid build of a cell with traced outlines under Voxel remesh or
Ultraliser is built again; every other stored surface is kept.

The one field is contoured at the discs' voxel, which is often finer than the Voxel
size the outlines were rasterized at, so it can weigh far more than either half
alone: RPC1 c2628 (26 discs, 3,978 outlines) at the default settings contours at
0.052 µm, draws 2.66 million raw triangles where its outline half alone is 312,412,
and peaks at 2,921 MB. Until 2.19.3 the build gate weighed it by its outline raster
and its disc annotations, 783 MB. It now plans the field as the build does (the disc
annotations' pieces and the outline raster at the build's Voxel size) and weighs
the disc half at least at the sheet that plan draws, with the raster on top: c2628
3,293 MB, and at Voxel 0.1 µm (4,254 MB measured) 4,762. Planning makes the outline
raster once per cell and size in the server's web process, 0.1 to 1.4 s at 0.25 µm
on the cells measured; a raster too large to make there (20 million voxels, or
900,000 exposed faces) is weighed at the 10 million raw triangles any budget allows.

### Why disc tubes come out fatter than traced outlines

On a cell traced partly with outlines and partly with discs, a process often looks
fatter where the annotator switched to discs. Inferior c1178's process leaving the
soma's left end measures 4.5 to 5.2 µm² in cross-section where it is traced as
outlines, and 8 to 10.6 µm² in the Voxel remesh tube that carries on through its
discs. Measured, in three parts:

- **A disc is not drawn too big.** Over 212 places in Temporal, Inferior, Cped and RC1
  cells where a traced outline links to a disc within three sections, the disc's
  radius is 0.85 times the outline's half-width (its minor semi-axis), at every
  tilt of the process from 15° to 83°. Annotators size a circle to the process's
  width, not to its slanted, elongated profile.
- **A disc says nothing about height.** A tube through discs is round. Traced
  outlines of processes running in the section plane are 0.48 to 0.70 times as tall
  as they are wide (median, 1,209 such strips in four volumes); c1178's stub is
  2.8 µm wide and 2.0 µm tall. Part of that is real flattening and part is that the
  grazing sections at a process's top and bottom are hard to trace, so the outline
  is itself a low estimate. Neither the discs nor the render can tell which.
- **A tube takes the outer edge of every wobble.** It is the union of round cones
  from each disc to the next, so a center drawn 0.1 µm off or a radius 15 % too
  large in one section widens it there. A straight synthetic process drawn with that
  much wobble comes out 1.4 to 1.9 times its own cross-section; c1178's thin discs
  come out a median 1.27 times their own area (a quarter of them 1.85 times or more),
  c16137's and Temporal c118's 1.13 times.

**Smooth discs ×r** (called Disc avg until 2.9.0) reaches the tubes (Cylinder, Tube, and Hybrid's discs): each disc
is averaged with its neighbors along its chain over that many of its own radii before
the tubes are built, so the tube runs through the run of discs instead of around its
outliers. At 0.5: c1178's median falls from 1.27 to 1.11 times the disc's area,
c16137's from 1.13 to 1.09, c118's to 1.06, and the surface's distance from the
annotations stays the same or improves (c1178 0.177 → 0.168 µm, c16137 0.141 → 0.143,
c118 0.108 → 0.111), at the same build time. Past 1 it changes little more. On c1178's
left process under Hybrid the tube's median cross-section falls from 8.0 µm² to 6.7 at 0.5
and 6.4 at 1, against the traced stub's 4.5 to 4.8: from 1.7 to 1.4 times. The rest is
the two parts no setting can know (the discs there are 1.2 times the stub's half-width, and
round). It starts at 0, which draws the discs as annotated. Envelope σ does not reach this: it smooths
only the soma-scale bodies, not the tubes.

It moves the discs, in the render only (Viking keeps them as drawn), which is why it
stays off and its name says so. How far, at 0.5: 72 to 91 % of a cell's discs move, a
median 0.015 to 0.044 µm, 95 % of them under 0.10 to 0.20 µm (Inferior c1178, c16137,
c943, Temporal c118); soma discs a median 0.16 to 0.24 µm and at most 0.77 µm; radii
within 6 %. For scale, the section correction moves c1178's annotations a median
0.109 µm and at most 3.99 µm. Three kinds of operation reach a surface, and only the
first and the last move an annotation: the volume's registration and the correction
service (data corrections), every method's way of drawing a surface through the
annotations (no annotation moves), and Smooth discs and Smooth outlines (input
smoothing, off unless asked for). To smooth the surface alone, use **Smooth surface µm**
(*Averaging out the staircase*, below).

**Exact tubes °** (0 to 60, under Voxel remesh and Ultraliser; off by default) fixes the
third part in the builder, without moving a disc. A pair of discs on a stretch running
within that many degrees of straight through the sections is drawn as the hull of the
two discs, whose slice at each section is the disc exactly as annotated, instead of a
round cone, whose end balls reach a radius above and below; an oblique process keeps
its round cones, whose slice is already the ellipse a slanted round process makes.
Measured at 35° on eight cells (Inferior c943, c1178, c16137, c21768, c10912, Temporal
c118, c106, c160), Voxel remesh:

| | round cones (0) | Exact tubes 35° | 35° and Smooth surface 0.5 |
|---|---|---|---|
| tube through a steep disc, times its area (median; six cells with steep stretches) | 1.16 to 1.73 | 0.98 to 1.26 | |
| mean distance from the annotations, c160 / c21768 / c118, µm | 0.090 / 0.180 / 0.107 | 0.084 / 0.176 / 0.105 | — / 0.176 / 0.105 |
| creases per µm², c160 / c21768 / c118 | 0.10 / 0.15 / 0.13 | 0.37 / 0.32 / 0.27 | — / 0.17 / 0.16 |

The mean distance is the same or up to 7 % smaller on every cell but c106 (0.5 % larger),
the volume up to 1.3 % smaller, the build no slower. The cost is a ring of creases at
each section where one hull meets the next, up to 3.6 times as many; Smooth surface 0.5
takes most of them back out and keeps the closer fit. At 50° more pairs are hulls (c160's
steep tubes 0.95 times, mean distance 0.076 µm, creases 0.63). It is not the default for
the reason in the first part above: the discs are drawn at a process's width, so a tube
exact to its discs is about 0.72 of a round process's traced cross-section, and the round
cones' extra lands near the traced size on average. Exact tubes are closer to the
annotations, not necessarily to the cell.

### Hover help that shows what a setting does

Hold the pointer over a render option (the label or its box) and its help shows a
short movie above the words: the same piece of a real cell built at a sweep of the
option's values, from smallest to largest and back, each frame the real surface with
its wireframe, under the technique the option belongs to (every other option at the
viewer's default). The value of the frame on screen is in its corner; the scale under
it marks that frame and, with a dot, the value the option is set to now; under it,
in words, what the smallest and the largest value do, and what the frame's build
measured: its triangles, its build time and its mean distance from the annotations.
The words below are the help as before, with the values that suit each volume and
Voxel size. With *reduce motion* set in the system, the frames are shown side by side,
still. Smooth discs has a movie of its own for Closed curve. The movies are made by
`tools/help_movies.py` from Temporal c118 and Inferior c1178, and made again with it
when a technique changes how it builds.

### Averaging out the staircase (Closed curve)

A closed-curve surface is built a section at a time, and its steps show: a soma
drawn as a stack of discs of stepping radii comes out as a stack of plates, a
dendrite that runs along the section plane as a ribbon with a tooth on its rim
for every section, and every wobble of an annotator's rim from one section to
the next as a terrace. **Smooth discs ×r** and **Smooth outlines µm**, beside Voxel size,
average them out by moving the annotations first (in the render only; how far is in
each one's help and below); **Smooth surface µm** smooths the finished surface and
moves none. All three start at 0, which draws the annotations exactly as traced: the
surface this method always made, vertex for vertex.

- **Smooth discs ×r** (0 to 3; Closed curve, and since 2.7.0 the tubes of Cylinder,
  Tube and Hybrid: *Why disc tubes come out fatter than traced outlines*, above)
  is for a cell's discs. Each disc is
  averaged with the discs along its chain of links (its center and its radius,
  a local fit weighted by a Gaussian) over a window of that many of the disc's
  **own radii**, so the averaging follows the size: a 5 µm soma disc is
  averaged over microns of the chain and sheds its plates, a 0.2 µm dendrite
  disc over a section or two and keeps its course. The first and the last disc
  of a chain stay as traced, and a chain is cut where the radius jumps by more
  than 2.5 times, so a soma is never averaged into the dendrite that leaves it.
- **Smooth outlines µm** (0 to 2; Closed curve and Hybrid) is for traced outlines, and
  on its own control, so a cell with both (the soma traced, the dendrites
  discs: Inferior c1178) can have one averaged and not the other. Each outline
  becomes the average of its neighbors' outlines along its chain of links,
  point for point, over a window of that many **microns** along the chain.
  Microns, not sections, because a volume's sections are 70 nm apart (RC1, RC2)
  or 90 nm, and a length means the same in every volume: 0.25 µm is three or
  four sections of 70 nm. Under Hybrid it averages the outline half; Smooth discs
  averages the discs there, before their tubes are built.

The finished surface is then smoothed over the same length (Taubin smoothing,
so the cell does not shrink): each vertex takes the passes its own annotation
asks for, growing with the square of the length over the mesh's edge (at most
20), and none where the process is only a pixel or two across (under 1.6 mesh
edges; in full from 3), so a thin dendrite is never eroded. This second step is
what takes out the teeth: they are creases a pixel wide, where the contour
crosses from one section's footprint to the next, and no averaging of the
annotations reaches them.

**Smooth surface µm** (0 to 2, every method, since 2.9.0) is that second step alone:
the same per-vertex Taubin passes on the finished surface, none where a process is
a pixel or two across, and no annotation moved. On c16137 at Voxel 0.1 µm it takes
the creases per µm² from 1.08 to 0.78 at 0.1, 0.51 at 0.25 and 0.32 at 0.5 (Smooth
discs 1 reaches 0.45 by moving the discs), with the mean distance from the
annotations unchanged at 0.073 µm and no vertex moving more than 0.02, 0.05 and
0.06 µm. On c1178 under Hybrid at 0.1 µm: 0.196 → 0.161 at 0.1 and 0.108 at 0.5
(at most 0.09 and 0.31 µm); RC1 c410 under Hybrid at 0.25 µm: 0.626 → 0.527 at 0.1
and 0.380 at 0.5. The volume stays within 0.4 % and the build takes under a second to
3.5 s longer. A coarse Voxel size has long edges, so a small length is a single pass
(c1178 under Closed curve at 0.25 µm: 1.19 → 0.98 for any length up to 0.25). It takes
out fine grain, not steps in the tracing: c1178's traced soma keeps its plates, each
several sections tall, even at 1 µm (its movie shows it), and averaging those is what
Smooth discs and Smooth outlines do, by moving the annotations. Under
Voxel remesh it changes little, since Passes already smooths that surface (Temporal
c118 0.132 → 0.112 to 0.139). With Smooth discs or Smooth outlines on as well, both
smoothings run, theirs first.

Measured (Inferior c16137, a cell traced with discs; Inferior c1178, soma traced
and dendrites discs; RPC1 c2628, traced outlines). *Creases* is the length of
mesh edge where two triangles meet at more than 45°, per µm² of surface
(`sbfsem-measure`'s *creases* column); *ripple* is how far, in degrees, a vertex's
normal strays from the average normal within 0.15 µm of it, for the vertices
nearest an annotation more than 2 µm in radius, which is what the eye reads as
terraces in a shaded picture; *mean deviation* is `sbfsem-measure`'s distance
between the surface and the annotations, in µm. Timings are from a busy machine.

| Cell, Voxel size | Smooth discs, Smooth outlines | creases | ripple | mean deviation | triangles | build, peak memory |
|---|---|---|---|---|---|---|
| c16137, 0.1 µm | 0, 0 | 1.08 | 7.6° | 0.075 | 339,000 | 2 s, 338 MB |
|  | 0.5, 0 | 0.69 | 3.1° | 0.080 | 324,000 | 4 s, 434 MB |
|  | 1, 0 | 0.45 | 3.0° | 0.081 | 322,000 | 4 s, 456 MB |
|  | 2, 0 | 0.29 | 3.1° | 0.082 | 321,000 | 4 s, 454 MB |
| c16137, 0.05 µm | 0, 0 | 0.33 | 10.0° | 0.078 | 1.04 M | 6 s, 505 MB |
|  | 1, 0 | 0.03 | 4.1° | 0.084 | 975,000 | 8 s, 781 MB |
| c16137, 0.035 µm | 0, 0 | 0.70 | 11.9° | 0.078 | 1.92 M | 11 s, 757 MB |
|  | 1, 0 | 0.03 | 5.4° | 0.085 | 1.79 M | 22 s, 1,066 MB |
| c16137, 0.025 µm | 0, 0 | 2.26 | 14.5° | 0.078 | 3.49 M | 16 s, 1,177 MB |
|  | 1, 0 | 0.05 | 6.9° | 0.085 | 3.25 M | 34 s, 1,837 MB |
| c1178, 0.1 µm | 0, 0 | 0.84 | 11.4° | 0.085 | 1.11 M | 6 s, 569 MB |
|  | 1, 0 | 0.53 | 10.7° | 0.087 | 1.07 M | 9 s, 797 MB |
|  | 0.5, 0.25 | 0.20 | 5.1° | 0.095 | 1.05 M | 8 s, 810 MB |
|  | 1, 0.5 | 0.14 | 3.8° | 0.108 | 1.04 M | 11 s, 802 MB |
| c1178, 0.05 µm | 0, 0 | 0.53 | 15.3° | 0.078 | 3.37 M | 14 s, 1,096 MB |
|  | 1, 0.25 | 0.01 | 7.1° | 0.094 | 3.15 M | 27 s, 1,820 MB |
| c2628, 0.1 µm | 0, 0 | 1.76 | 14.8° | 0.111 | 1.18 M | 4 s, 794 MB |
|  | 0, 0.1 | 1.30 | 14.2° | 0.121 | 1.17 M | 7 s, 1,112 MB |
|  | 0, 0.25 | 0.69 | 11.5° | 0.138 | 1.13 M | 10 s, 1,122 MB |
|  | 0, 0.5 | 0.42 | 8.5° | 0.162 | 1.07 M | 10 s, 1,034 MB |
| c2628, 0.05 µm | 0, 0 | 1.83 | 18.5° | 0.055 | 3.41 M | 10 s, 1,360 MB |
|  | 0, 0.25 | 0.49 | 13.5° | 0.093 | 3.16 M | 23 s, 2,079 MB |
| c2628, 0.035 µm | 0, 0 | 1.82 | 20.6° | 0.042 | 5.93 M | 12 s, 1,977 MB |
|  | 0, 0.25 | 0.41 | 14.5° | 0.084 | 5.46 M | 41 s, 2,819 MB |
| c2628, 0.025 µm | 0, 0 | 1.97 | 23.3° | 0.034 | 10.10 M | 35 s, 3,222 MB |
|  | 0, 0.25 | 0.46 | 16.7° | 0.078 | 9.29 M | 83 s, 3,880 MB |

What to read in it:

- **Averaging takes out what a finer Voxel size cannot.** A finer raster follows
  the annotations more closely, and that is all it does: it does not smooth a
  terrace, it resolves it more sharply. c16137's ripple rises at every step (7.6°
  at 0.1 µm, 10.0° at 0.05, 11.9° at 0.035, 14.5° at 0.025) and its creases are
  fewest near 0.05 µm (1.08, 0.33, 0.70, 2.26 per µm²) and rise again below it, at
  3 to 10 times the triangles; c2628's deviation falls from 0.111 µm at 0.1 to
  0.055 at 0.05 and 0.034 at 0.025, while its ripple rises from 14.8° to 18.5° and
  23.3° and its creases stay at 1.8 to 2.0. With Smooth discs 1 the same c16137 is 3.0°
  at 0.1 µm, 4.1° at 0.05, 5.4° at 0.035 and 6.9° at 0.025, with 0.03 to 0.05
  creases at the three finer ones, and c2628 at 0.025 µm with Smooth outlines 0.25 has 0.46
  creases and 16.7° at a deviation of 0.078 µm: the two together keep the raster's
  fidelity and shed most of its terraces.
- **A strength of 1 for discs and 0.25 µm for outlines is a good first setting**
  (c16137 at 0.1 µm: creases 1.08 → 0.45, ripple 7.6° → 3.0°; c2628: 1.76 →
  0.69). Past 1 the discs gain little (2 gives 0.29), and 0.5 µm or more takes
  c2628's outlines 0.05 µm further from where they were traced.
- **It moves the surface off the annotations by a few hundredths of a µm, and
  that is the price**: the metric counts the wobble it removes as error. The
  worst-placed discs at strength 1 are the soma's plates, 0.3 to 0.45 µm off on a
  5 µm radius. The Render compare tab's error map shows where.
- **Thin processes are kept.** c16137 stays one body at every strength, and the
  volume changes by 4 % at most (c16137 −4 %, c1178 −1 %, c2628 +2 %); a process
  under about two pixels across is left as it was.
- **It costs time and memory**: a few seconds and about a third more memory at
  0.1 µm (c16137 2 s and 338 MB become 4 s and 456 MB). On a surface of millions
  of triangles the smoothing runs in slabs of the mesh, so its memory stays
  bounded, and it never spends more than 60 million vertex-passes (about half
  a minute on a quiet machine; RPC1 c2628 at 0.025 µm, 10 million triangles,
  builds in 35 s and 83 s with Smooth outlines 0.25 on a busy one): a larger cell takes
  proportionally fewer passes.

The controls are in the 3D render and in each Render compare pane, with the same
help. A setting is part of a surface's cache key only while it is above 0, so a
surface built before averaging existed is found again with both at 0.

### Long links, separate bodies and skipped sections (Closed curve)

Four more controls decide how far the Closed curve method reaches between
annotations. **Curve links µm** and **Join bodies µm** sit beside the averaging;
**Bridge skips µm** and **Follow links µm** are in the More menu. They start at
0, 0, 1 and 5 µm, the values the method has always used, and at those values
the surface is the one it always made, vertex for vertex (the same cache key,
too: a surface built before the controls existed is found again).

#### Curve links µm: long links follow the chain instead of a straight band

A link between two discs is drawn as the straight, tapering run from one
circle to the other, on every section it crosses. Most links are short (Inferior
c1178: half step 0.24 µm or less, 95 % under 1.5 µm) and the straight run is the
process. A long one is a straight band, and a chain of them a zigzag with a
corner at every disc: c1178 has 211 links of 1 µm or more, and three that cross
22 untraced sections (914 to 936) 2.4 to 5.4 µm sideways, slanted bands from
one chain of discs to the next.

Above 0, every link whose discs are at least that many µm apart follows a curve
instead: a cubic through the two disc centers whose direction at each disc is
read from the chain on its far side (a Catmull-Rom spline with chordal knots, in
3D, so a run that leaves a chain climbing through the sections leaves it
climbing, then bends over to the next disc). Each section still gets the part of
the run within one section of its plane, as a straight run does; Z never turns
back between the two discs. What a curve may do is bounded, because a straight
run is never wrong by more than the annotations allow and a curve can be:

- it **passes through every disc center**, so no annotation moves;
- it stays **straight at a chain's end and at a branch**, where there is no one
  chain to read a direction from, and **where the chain turns back** by more
  than 90° at the disc (a hairpin or a jump: a curve through it would balloon);
  the direction on the far side is read from the first disc there at least the
  disc's own radius away, so the jitter of a stack climbing through the sections
  does not tilt it;
- it **never bends tighter than the process's own radius** there, as tight as a
  tube can turn without its inner side folding;
- it **keeps within a quarter of its length of the straight band**.

A curve that breaks a bound is drawn less curved, its tangents blended toward
the straight band's until it keeps them (23 of c1178's 163 curved links at 1 µm).

Measured (2026-10-16; *turn* is the angle between the two runs at a disc with two
links, at least one of them curved, as straight runs and as drawn; *strays* is how
far the curve gets from its straight band):

| Cell | Curve links | links that long | curved | ends kept straight (chain end, branch, hairpin) | turn at their discs, median | strays, median / largest |
|---|---|---|---|---|---|---|
| c1178 | 1 µm | 211 | 163 | 2, 81, 12 | 29.1° → 12.7° | 0.043 / 0.58 µm |
| c1178 | 0.5 µm | 449 | 381 | 7, 110, 41 | 33.5° → 18.8° | 0.021 / 0.58 µm |
| c16137 | 1 µm | 37 | 33 | 0, 18, 0 | 17.6° → 8.7° | 0.026 / 0.07 µm |
| c16137 | 0.5 µm | 409 | 395 | 5, 121, 5 | 23.1° → 7.3° | 0.019 / 0.11 µm |

What to read in it:

- **It moves the surface only along the long links.** c1178 at Voxel 0.1 µm:
  1,105,548 → 1,106,540 triangles, the mean deviation from the annotations 0.085
  µm both ways, creases 0.844 → 0.846 per µm², 2 bodies both ways; c16137 0.075 →
  0.076 µm. At 0.25 µm, where c1178's thin dendrites already break into beads, a
  curve can move a bead: 44 bodies become 46. Seen from above, c1178's two bands across the 22 untraced sections
  arch (the farther 0.32 µm off its chord) and their corner with the chain below
  rounds off; a chain turning at a disc between long links bends through it.
- **1 µm is a good first setting.** A link under 1 µm is under four radii of a
  typical 0.27 µm dendrite and reads as a joint rather than a band, and 95 % of
  them would stray under 0.05 µm, inside the finest default pixel. c16137's
  zigzags are links of 0.5 to 1 µm: 0.5 µm is the setting that reaches them.
- **The corners it keeps** are the branches and the hairpins: the 90th percentile
  of the turn at the discs stays near 116° on c1178.
- **It costs about a second** (c1178: 163 curves, 1 s of the build's 9), and
  nothing in memory.
- It works with Smooth discs: the discs are averaged first and the curves drawn
  through the averaged discs (c1178 at 0.1 µm, Smooth discs 1: creases 0.528 →
  0.529).

#### Join bodies µm: one cell, one body

A Closed curve surface can come apart into separate bodies, and in a wireframe
each is a shell of its own. The voxel grid is smoothed before it is contoured
(MATLAB's `smooth3`, a 3 × 3 × 3 average), which removes anything under about a
pixel and a half in radius: at Voxel 0.25 µm, the raster the app picks for a cell
with traced outlines, a 0.2 µm dendrite breaks into beads. What separates the
bodies, read off the annotations either side of each gap (2026-10-16):

| Cell, Voxel | bodies | a thin process the smoothing removed | specks with no annotation of their own | linked outlines on adjacent sections that miss each other | unlinked in Viking | gaps under 1 µm / 2 µm |
|---|---|---|---|---|---|---|
| c1178, 0.25 µm | 44 | 42 (48 discs drawn with no surface at all) | — | — | 1 | 20 / 34 of 43 |
| c1178, 0.1 µm | 2 | — | — | — | 1 (0.43 µm) | 1 / 1 |
| c16137, 0.25 µm | 98 | 97 (123 discs with no surface) | — | — | — | 67 / 86 of 97 |
| c16137, 0.1 and 0.05 µm | 1 | — | — | — | — | — |
| RPC1 c2628, 0.25 µm | 70 | 61 | 8 | — | — | 63 / 67 of 69 |
| RPC1 c2628, 0.1 µm | 94 | 62 | 28 | 2 | 1 | 92 / 93 of 93 |

None is a skipped section or a link over its reach, so the two bridges below
cannot close them (c2628 at 0.25 µm is 70 bodies with Bridge skips 1 or 2 µm).
**Join bodies µm** joins every separate body up to that many µm from another with
a tie drawn into the voxel grid along the shortest crossing between their
surfaces, joined in the order of the shortest gaps first (the minimum spanning
tree, as Tie gaps does for the voxel pathways), and contours the grid again where
the ties reach. A tie is a pixel and a half in radius at least, so the smoothing
keeps it, and 0.25 µm where the pixel is finer (the voxel pathways' tie); it
enters both bodies by two radii. A gap between two runs of sections (a stretch
left open in Z) is the bridges' business and is not tied, and a pair across one
never takes the place of a tie inside a run. The inner sheet of a cavity, a body
hollow inside, is part of that body and is never tied (a tie from it would run
through the wall and out of the cell).

Measured (`sbfsem-measure`, 2026-10-16; *mean deviation* is the distance between
the surface and the annotations, then its two halves: *surface*, surface no
annotation drew, and *annot*, annotations the surface does not reach):

| Cell, Voxel | Join bodies | bodies | ties | mean deviation (surface / annot) | creases | triangles | peak memory |
|---|---|---|---|---|---|---|---|
| c1178, 0.25 µm | 0 | 44 | — | 0.100 (0.097 / 0.102) µm | 1.196 | 291,646 | 334 MB |
|  | 2 µm | 10 | 49 | 0.101 (0.100 / 0.101) | 1.189 | 301,260 | 433 MB |
|  | 5 µm | 1 | 58 | 0.098 (0.103 / 0.095) | 1.194 | 305,728 | 372 MB |
| c16137, 0.25 µm | 0 | 98 | — | 0.097 (0.064 / 0.190) | 2.130 | 73,464 | 251 MB |
|  | 2 µm | 12 | 127 | 0.118 (0.100 / 0.171) | 1.760 | 97,128 | 264 MB |
|  | 5 µm | 2 | 137 | 0.114 (0.108 / 0.130) | 1.759 | 102,936 | 290 MB |
| RPC1 c2628, 0.25 µm | 0 | 70 | — | 0.354 (0.033 / 0.368) | 1.729 | 312,464 | 567 MB |
|  | 2 µm | 3 | 113 | 0.280 (0.042 / 0.290) | 1.782 | 343,974 | 658 MB |
|  | 5 µm | 1 | 115 | 0.272 (0.043 / 0.282) | 1.784 | 345,486 | 655 MB |
| c1178, 0.1 µm | 0 | 2 | — | 0.085 (0.109 / 0.069) | 0.844 | 1,105,548 | 564 MB |
|  | 2 or 5 µm | 1 | 5 | 0.085 (0.108 / 0.069) | 0.844 | 1,106,248 | 824 MB |
| RPC1 c2628, 0.1 µm | 0 | 94 | — | 0.111 (0.029 / 0.115) | 1.761 | 1,184,600 | 741 MB |
|  | 2 µm | 1 (2: the metric counts a cavity's inner sheet) | 397 | 0.096 (0.040 / 0.099) | 1.498 | 1,326,164 | 1,259 MB |

What to read in it:

- **5 µm makes each of these cells one body at the raster the app picks for it**
  (c16137, which the app draws at 0.05 µm, is one body there already; at 0.25 µm
  it is 2, its last gap between 5 and 10 µm). 2 µm leaves c1178 at 10 bodies and c16137 at 12.
- **A tie is surface no annotation drew**, and the deviation says so: c16137's
  *surface* half rises from 0.064 to 0.108 µm. Where the ties run through
  processes the grid lost, the annotations they pass are reached again: c2628's
  *annot* half falls from 0.368 to 0.282 µm at 0.25 µm, and its mean from 0.354
  to 0.272.
- **It costs a contour of the blocks the ties reach** and the search for the
  gaps: c1178 at 0.1 µm (5 ties) about nothing, c2628 at 0.1 µm (397 ties) 2.5 s;
  and memory, because the first contour is kept for the second pass (c2628 at 0.1
  µm 741 → 1,259 MB, at 0.025 µm, 11 million triangles, 3,231 → 5,835 MB). The
  build gate prices it at 35 bytes a voxel of the raster.
- **The inner sheet of a cavity is not a body.** c2628 at 0.1 µm keeps 8 of them,
  8 to 128 triangles each, inside the cell; the measure tool counts the one more than
  0.3 µm from the outer sheet as a second body.
- **A finer Voxel keeps thin processes whole by itself**: c1178 is 2 bodies at 0.1
  µm and c16137 one, where they are 44 and 98 at 0.25 µm.

**The viewer starts at 5 µm** (since 2.6.2, the user's choice of 2026-10-16), so a
Closed curve cell is one body unless its pieces are farther apart than that; set it
to 0 to see the bodies as the grid makes them. The library and the API keep 0, as
MATLAB drew them (CLAUDE.md ground rule 1). A cell whose render was saved with Join
bodies at 0 keeps that setting; *Defaults to selected* (or *to all*) in the render
panel's menu gives it the 5 µm, unless you saved Closed curve technique defaults of
your own, which then win.
Other methods ignore the option, and it is left out of their cache keys, so their
stored surfaces are the ones served. The cost chip counts what joining adds (about
260 bytes a triangle and a third more time).

#### Bridge skips µm and Follow links µm

These were constants of the method until 2026-10-16. **Bridge skips µm** (1 µm):
where two traced sections are at most this far apart, the sections skipped between
them are filled by interpolating the two outlines; a longer skip stays open,
because a body invented across it would be a guess. 1 µm bridges 93 % of the
untraced runs in the 160 most-traced structures of the eight volumes (82 % are one
or two sections). It acts on traced outlines; a cell traced with discs alone has
its links drawn instead. **Follow links µm** (5 µm): a Viking link between two
annotations more than a section apart is followed through the sections between
them, a disc's run or the two outlines interpolated, when it spans at most this
much Z; a longer one is taken for a stray, and 0 follows none across a skip. A
link to the next section skips nothing and is always drawn (at 0 a disc-traced
dendrite used to fall into beads). The 1,597 section-skipping links of 18
surveyed cells span 0.56 µm or less, and c1178's longest cross 22 sections, 1.98
µm: Follow links 1.5 µm drops them, and c1178 at 0.1 µm is 5 bodies instead of 2.
Neither changes the body count of c1178, c16137 or c2628 upward of its default
(Bridge skips 3 µm on c1178 and 2 µm on c2628 build the same surface).

### MeshLab remeshing

**More ▾ → MeshLab** remeshes the finished surface with
[MeshLab](https://www.meshlab.net) (pymeshlab, on the server, in a process of
its own), after whatever technique built it. It changes the triangles, not
the accuracy: fidelity to the annotations is set by the technique, and the
Render compare tab's error map shows it.

| Mode | Knob | What it does | Measured (voxel2 meshes, disc metric) |
|---|---|---|---|
| isotropic remesh | **Edge µm** | Uniform, well-shaped triangles with edges about that long, kept within a tenth of an edge of the surface | Inferior c943 at 0.25 µm: 147,700 → 10,916 faces, 0.014 µm from the original, surface→annotation 0.549 → 0.558 µm, slivers 2.3 % → 0.9 %. RC1 c606: 400,000 → 360,285 faces, 0.043 µm, slivers 3.6 % → 0.6 % |
| quadric decimation | **Keep** | Collapses edges down to that share of the faces, keeping topology and normals | c943 at 25 %: 0.012 µm from the original, 0.549 → 0.547 µm. c606: 0.062 µm, annotation→surface 0.294 → 0.321 µm |
| screened Poisson | **Depth** | A new watertight surface fitted to the vertices and normals — the step applied to VikingMesh output before Blender | Gains nothing on these meshes and leaves 11 % slivers; depth 9 is too coarse for a large cell (c606: 0.097 µm from the original, surface→annotation 0.744 → 0.811 µm), depth 10 follows it (0.763) |

Isotropic is the one to use for an export to Blender or to software that
dislikes marching-cubes slivers; quadric is a better Decimate at the same
face count. Exports follow what is on screen, so an exported file is the
remeshed surface. Each job adds the child process's start-up (about 1.6 s)
to MeshLab's own time: c943 6 s in all, c606 30 s. Render compare offers
*Voxel remesh + MeshLab isotropic / quadric / Poisson* as techniques.

The control is grayed out on a server without pymeshlab: it is an optional
extra (`pip install "sbfsem[meshlab]"`), GPL-3 and about 300 MB installed,
which `sbfsem` never imports — it runs it in a child process.

### Volumes

The volume menu lists **Inferior Monkey**, **Nasal Monkey**, **Temporal
Monkey** and **Cped**, plus the four Marc lab rabbit volumes **RC1**, **RC2**,
**RPC1** and **RPC2** (served from the same host as the Neitz volumes). The
monkey volume names are display names; the identifiers used in API paths,
cache filenames and exported JSON are unchanged (`NeitzInferiorMonkey` and so
on), so links and previously exported files keep working.

Each volume was imaged with its own pixel size and section thickness, and
the viewer works in microns, so the Z spacing of every cell follows its own
volume: `Zum = section × thickness`. The values come live from each volume's
Viking `Scale` endpoint when a cell loads (an offline fallback table holds
the same numbers). As read on 2026-09-17:

| Volume | Pixel (nm) | Section thickness (nm) |
|---|---|---|
| Inferior Monkey | 7.5 | 90 |
| Nasal Monkey | 5.0 | 50 |
| Temporal Monkey | 7.5 | 75 |
| Cped | 5.315 | 50 |
| RC1, RC2, RPC1, RPC2 | 2.176 | 70 |

The MATLAB toolbox's offline table differed for three of these (Temporal
70, Cped 90, RC1 90), so a MATLAB-era render of an RC1 cell is 29 % taller
in Z than this tool's. Which value is right for a volume is a question for
the Viking database, not this viewer: it draws what Viking reports.

### Loading several cells at once

The Structure ID box speaks the same grammar as the Viking data export
portal, so a list that works there works here — typed, pasted, or imported
as a `.txt` file:

- **IDs and inclusive ranges**, in any mixture: `1178`, `4555, 8232, 1178`,
  `5-10`, `1176-1180, 4555`, `180 - 476` (spaces around the dash are fine,
  and so are the en dashes word processors substitute). Digits are the
  ASCII 0-9: an Arabic-Indic or full-width digit is reported as unreadable,
  by the box and by the server alike.
- **Labels.** Any entry containing a letter is matched as a case-insensitive
  substring of the volume's parent-structure labels — `CBb4w` loads every
  cell whose label contains it. Entries are separated by commas, semicolons,
  or new lines, never by spaces alone, so labels with spaces (`GC ON`,
  `yAC ON+OFF`) stay whole. What each label matched — and any entry that
  could not be read — is named in the status line; input is never silently
  dropped.

Cells load several at a time — six in flight, each a Viking round trip and
a mesh build the server queues on its own workers. While they load, a strip
at the top of the viewport counts cells done out of cells asked for, with a
bar, and lists the cells in flight by ID and label with what each is doing:
fetching annotations, queued on the server, waiting for server memory,
building, drawing. While a cell builds, the line names the build's own stage and how far it has got — `contouring 1,609 of 2,512 · 2 s`, then tying gaps, smoothing, decimating (a cell already built at those settings comes back from the server's cache with no build at all). Meanwhile the cell's annotations are drawn as small dots in its color, so you can see where it is and its rough shape; the surface replaces them when it arrives. A cell the server already has built appears directly, without dots. A single cell, a link from
Viking and a restored session get the same strip. It turns red if any cell
failed and goes away a few seconds after the load ends.

A load into an empty viewer frames the view on the first thing of it to appear (a cell's dots or
its surface) and frames the whole scene when it ends. You can turn, pan and zoom the view while
the cells come in; once you have, the end of the load leaves the view where you put it, and
**Reset view** (or `m`) frames everything.

Since 2.26.1 any surface that lands in a view showing no cell frames it too, as Reset view
would: a load into a list that already holds a row, a fix applied to a cell whose first build
failed, a restored view that looks at none of its cells. Before, only a load into an empty list
framed, and a cell could be built, in the scene and out of sight until Reset view (Temporal
c162392, 2026-10-21). A view that shows any part of a cell (its surface, skeleton, rings or
dots) is never moved, nor one Held in Display. Since 2.30.2 a build that makes no surface frames
what it does draw the same way: a cell whose rings, nodes or skeleton are shown, built empty into
a view that shows no cell, is framed on them.

Since 2.31.1 **Reset view** (and `m`, and a viewpoint button on a view not yet framed) frames such
a cell too. A cell that shows no surface or skeleton of its own counts by its shown rings,
annotation wireframe or GraphApp drawing, beside other cells' surfaces or with them hidden. Before,
the scene's extent left it out whenever any cell had a surface, so Reset view framed the others and
left it out of sight. A cell with a surface is still framed on that surface alone: its rings never
widen the view.

**Stop**, at the right of the strip, ends the load at once: no further cells
start, and every cell of the load that is not drawn yet — still fetching its
annotations, or queued or building on the server — is dropped from the list
as its ✕ would drop it, and its queued build is cancelled. Cells already on
screen stay, and so does any cell that was in the list before the load
began. A build the server has already started cannot be interrupted; it
finishes in the background and its surface is kept, so loading that cell
again later is instant. The strip then reads `Stopped · 37 of 207 cells
loaded`. Ids that do not
exist in the chosen volume are named in the status line and the rest still
load. Two hundred cells per request is the cap. Built
meshes are kept on the server's disk, so loading the same cells again, or
restoring a session after a redeploy, skips the build entirely.

### Selecting cells

Each row carries two controls, at opposite ends so they are never confused:

- the **eye** on the left toggles visibility in the render, and nothing else;
- the **sel** box on the right chooses which cells an Apply action affects.
  Shift-click extends from the last one you ticked; **Select all** and **None**
  sit under the list. Newly loaded cells arrive selected.

**Clicking the row itself only opens or closes it** — it never changes the
selection. That separation is the point: to copy cell B's settings onto A and
C, tick A and C, then open B and press Apply. Opening B leaves your selection
alone. One cell is open at a time; opening another closes the previous one,
since the panels are tall.

**Unloading is undoable.** The ✕ sits beside the selection box, so it stays
dim until you hover the row, and unloading offers an **Undo** in the status
line for twelve seconds. Reloading a cell is cheap, but its color and per-type
synapse settings exist nowhere else — the undo is what makes a mis-click
harmless rather than merely unlikely. The browser keeps the cell's surfaces
for the undo, so the cell comes back from them instead of being downloaded
again (on a local server, undoing the ✕ on c1178 took about 0.3 s where
downloading its surface again took 1.9 s, and **Unload all** then Undo of
c1178 and c21768 took about 0.6 s where it took 3.6 s). They are kept in a
bounded cache of up to about 200 MB of the tab's memory, the least recently
used going first (five or six large cells, or a few hundred small ones), and
they go when the cell is loaded again as other data, when **Open…** replaces the
workspace, or on **Rebuild all renders**.

### Per-cell display settings

An open cell carries an accent rail down its left edge, and its settings are
grouped into **Surface**, **Wireframe**, **Annotations**, **Annotation
wireframe**, **Skeleton**, **Synapses**, **Skeleton check**, **Registration**,
**Position shift** and **Manual moves**, each of which opens and closes on its own. They all
start shut: a closed section still says what is inside it — "colored by IPL
depth", "0.40 µm tubes", "3 of 5 types" — so the summary is readable without
opening anything. Which sections you opened is remembered per cell.


Every row in the Loaded list opens onto that cell's own display settings —
click anywhere on the row except the visibility checkbox or the ✕:

| Setting | Notes |
|---|---|
| Color by IPL depth | Disabled, with a note, in volumes that have no IPL boundary markers — Cped is one |
| Color | Hidden while depth shading is on, which replaces it |
| Opacity | Always available, including under depth shading — a depth-shaded cell still needs to be seen through |
| Show surface | The filled surface. On by default |
| Show wireframe | The triangle edges. Off by default |
| Show annotations | The raw Viking annotations, each in its section plane: a disc as a ring at exactly its radius, a closed curve as its traced outline. Color, opacity and stroke width below it. **Depth**, in nanometers, gives each one a height along Z. It is the total height, centered on the section plane (70 nm is 35 nm above and 35 nm below); type any whole number up to 500. Whatever it is, an annotation is never less than one pixel tall on the screen, so seen edge-on (XZ, YZ), exactly where Z levels are read, each one stays a hairline at any zoom: 0 (the default, and the **one pixel** button) is that pixel alone, kept a pixel as you zoom, and face-on it draws exactly what the flat ribbon does. Since 2.40.0: before, 0 was the flat ribbon, and a workspace, session or look saved with 0 now gets the pixel. **flat** is the ribbon with no thickness, which vanishes edge-on; **one section** sets it to the volume's section thickness (70 nm on the rabbit volumes), so annotations on consecutive sections just touch and a missing section shows as a gap; anything between shows each section as a separate band. Any Depth costs what the default does: it is drawn on the ribbon's own triangles, its two edges moved apart along Z as you look, so a one-section or a typed Depth draws exactly as many triangles as one pixel (since 2.41.0; before, a Depth built a solid band of four times the triangles: on RPC1 c2628, 25.4 M against 6.4 M a frame, 18.9 ms against 2.7 face-on). Seen from any side it covers what such a band would: edge-on a section's full height, ring ends included, and at a slant the top and the near side together, as one slanted strip. Below opacity 1 an annotation now shows at the opacity set, as the one-pixel default always did, where a band's overlapping top and bottom made it look nearly opaque. Orthographic plus XZ1 is the clean way to read a stack of sections. Off by default |
| Through the surface | Draw the rings over the surface whatever is in front of them (on by default). Off, a ring is hidden wherever the surface encloses it, and since each ring sits on the annotated rim, which the smoothed surface encloses by a few tens of nanometers, and a soma's top and bottom rings lie under its caps, an opaque soma then shows almost none of its annotations. Lower the surface opacity instead if you want depth to sort them |
| Show annotation wireframe | A wireframe made from the annotations alone, with no surface: every disc and closed curve as a ring in its section plane, lines along the linked annotations, and short ties where a branch leaves another annotation. Each ring is averaged with its neighbors along Z over a window whose width is **Averaging** times the ring's own radius r (0.50 by default), so a 9 µm soma ring is fitted over about 4.5 µm of Z while a 0.2 µm dendrite ring is fitted over 0.1 µm, a section or two; 0 draws the annotations exactly as traced. Rings never leave their section planes, and the first and last ring of each chain (a soma's top and bottom, a dendrite's tip) stay as annotated. **Fit**: *Quadratic* (the default) keeps a soma's dome and a taper and removes only faster wobble; *Mean* flattens anything curved in the window. **Size tolerance** keeps rings of different size from mixing; **Break at** is the radius ratio at which a link becomes a tie instead of a chain step (2.5 by default, so a soma never averages in the dendrite that leaves it). **Lines along** (12) and **Ring spacing** (0.5 µm; 0 draws every annotation) only change what is drawn. Color, opacity and Through the surface as for the annotations. Off by default |
| Show skeleton | The traced centerline, with its own color, width and opacity. Off by default |
| Show synapses | A row per synapse type the cell has, each with its own shape, size, color and opacity (below) |
| Position shift | An arrow from where each annotation was to where it is drawn, for the section correction, the current transforms or both; every section or one slice at a time. See [Position shift](#position-shift-where-each-annotation-was-and-where-it-is-drawn). Off by default |
| Manual moves | Annotations moved by hand after the correction, kept under the cell's registration setting: the list, delete one or several, reset all, and an arrow for each. See [Moving an annotation by hand](#moving-an-annotation-by-hand) |

**Exporting the annotation wireframe.** While a cell's annotation wireframe is
on, right-click the cell (in the render or its Loaded row) → *Export
annotation wireframe* writes what is drawn, with the panel's settings, to
`c{id}-annotation-wire.{ext}`: Wavefront OBJ or PLY as line elements, STL as
thin rods (STL has no lines), a CSV or TSV table of edges, NumPy segments or
arrays, or HDF5. Every ring, line along and tie is one segment, in microns.
OBJ, PLY and HDF5 record the volume, the cell and the settings it was built
with. *Export wireframe* above it is the surface's triangle edges.

**Synapses are configured per type.** Under *Show synapses* each type the cell
has — `RibbonPost`, `ConvPost`, `GapJunction` and so on — gets its own row with
its count, a visibility checkbox, and its own **Shape**, **Size**, **Color** and
**Opacity**. So you can show ribbon inputs as large opaque spheres and
conventional inputs as fine translucent ovals in the same render, or hide a type
entirely. **⟲** on a row returns that type to the cell defaults; the button is
grayed when the type has no settings of its own. The cell defaults themselves
(Circle, 0.5 µm, each type's own Viking color, fully opaque) have no control: a
row is the only way to give a type anything else, and Apply this cell to
selected or all copies the rows.

**Surface, wireframe and skeleton are fully independent** — any combination of
the three, including none. They are separate objects sharing one geometry, not
one object in different modes, so a wireframe drawn over a translucent surface
shows the triangulation without hiding the shape. That combination is the
clearest way to see what **Decimate** actually did. The wireframe keeps full
opacity when the surface is faded, since reading structure through a
translucent surface is the case it exists for.

The skeleton's color, width and opacity are its own, deliberately independent
of the cell's: making a cell translucent to see the centerline inside it is the
main reason to draw one, and inheriting the cell's opacity made the line vanish
exactly then.

**Line width is in microns, not pixels.** WebGL ignores line thickness on
essentially every platform, so at width 0 the skeleton is a one-pixel line at
any zoom. Above 0 it is built as real tube geometry instead — affordable here,
since the largest skeleton is ~130,000 triangles against its cell mesh's
~830,000.

Two marker shapes are available. **Circle** is a sphere per synapse, as the
MATLAB `synapseSphere` drew them. **Oval** is an ellipsoid aligned to the
annotation's own axis: Viking stores each synapse as a `LINESTRING` — the
segment the annotator drew — and the oval is built at that segment's true
length and direction, so you can see which way each synapse faces. Real
annotations run 0.1–0.25 µm, so switching shape also moves Size to a sensible
thickness for it (0.5 µm round, 0.1 µm oval).

At the bottom of an open cell, **Apply this cell to selected** and **Apply this
cell to all** copy that cell's color, opacity, depth shading and every
per-type synapse setting onto the others — the button names the count, so it is
always clear what is about to change. The Render panel's own Apply buttons do
the same for the mesh settings (method, smoothing, decimation), and leave each
cell's depth shading alone.

A change that needs new geometry from the server — synapse size or shape —
re-renders at once; color and opacity are a material change, and hiding
something is free.

Every slider has a number box beside it. Sub-0.1 µm synapse sizes are not
reachable by dragging a 200-pixel track, so type them instead. Slider changes
that cost a server request are held until you stop moving.

### Position shift: where each annotation was, and where it is drawn

The section correction and the current transforms move annotations by tenths
of a micrometer, which is invisible in a surface. The cell's **Position shift**
section (off by default) draws the move itself, over the cell as it is: an
arrow from where each annotation was to where it is drawn. It adds a layer and
changes nothing else: no geometry, no cached mesh, no default.

**Which two positions.** **Between** picks the pair:

- **Section correction** (the default): where the volume's current transforms
  put an annotation (where Viking draws it), then where Viking's section
  correction puts it. This is the before and after of the correction.
- **Current transforms**: the position Viking stored, then where the current
  transforms put it (the stos edits made since the annotation was saved).
- **Both**: stored, then corrected.

A pair is available only when that stage was applied to the cell and moved at
least one of its annotations; otherwise its entry is grayed, with the reason
as its hover help. A volume whose current transforms are not read here (RC1,
RC2, Nasal, Temporal) has the section correction alone, and no **Between**. If
the pair asked for cannot be shown, the next one that can is drawn and the chip
beside **Show position shift** says so (`16 shown · Transforms`, the reason first
in its hover), with thresholds that show something for as long as that is so.
When nothing moved, the chip reads `nothing moved` and the section offers to
turn the correction on in the volume's default mode: **Apply field + fit** on a
Neitz lab volume, **Apply curve fit** on the others. Every control explains itself after a two-second hover,
most with a drawing.

**Pulled toward a soma** (since 2.23.0). Under **Curve fit** or **Field + fit**,
the section also says which of the cell's annotations Viking's curve fit pulled
toward a much wider linked one, the cell's own rows of the Settings tab's
[Correction log](#settings-debug-tools), whether the layer is on or not: *Curve fit
pulled 3 annotations toward one 4× as wide or wider:* followed by their Location IDs
(Inferior Monkey c1178 under Field + fit: 1328721, 2028495 and 690259). A Location ID
selects that annotation in the 3D view, drawn yellow; its hover says how far it moved,
toward which annotation, how far along the way and what moved with it. The shut section
counts them (`off · 3 pulled`). The ratio is the Correction log's *Wider by at least*,
kept in this browser (default 4×); changing it there changes what the cell panels list.
Nothing is shown for a cell with none, or under no correction or the residual field
alone.

**Outlines moved off** (since 2.36.0). A second line names the traced outlines the
correction moved off the outlines linked to them: *Curve fit moved 22 traced outlines
off the outlines linked to them:* followed by the first 12 Location IDs and how many
more the Settings tab's Correction log lists. The fit takes an outline's centroid as a
point of the process, so where an outline takes in a lobe or the base of a branch, the
next one is moved toward the lobe though their membranes line up (Inferior Monkey
c16137's soma outline on section 547 moved 0.58 µm toward section 546's lobe). The shut
section counts them too (`off · 3 pulled, 22 off`); the ratio does not change them.

**What is drawn.** One arrow per annotation that moved, in its section plane,
**Arrow width** pixels wide on the screen at any zoom (2.5 by default, 1 to 8),
with a filled head and a shaft that fades toward its tail, so which way it
points reads at a glance. (WebGL draws a line one pixel wide whatever it is
asked, so each arrow is drawn as triangles spread across the screen by the
graphics card.)
Its **head is on the annotation as it is drawn**, the ring you already see (for
**Section correction** and **Both**; for **Current transforms** the head is where
Viking draws the annotation, which differs from the ring on screen by the
section correction when that is applied), so only its tail is exaggerated. **Exaggerate** draws every tail that many times
farther back than the true shift: a tenth of a micrometer does not show beside
a 10 µm soma. It starts at **Auto**, 2.5 % of the cell's bounding-box diagonal
over the 95th percentile of the moves (×13 on c1178), between ×1 and ×100;
**Fit** returns to Auto. **Only a shift up to *Full color at* is exaggerated**
(since 2.6.3): past it the arrow grows at true scale, so an outlier is drawn as
the arrow of Full color at plus its true excess, and a longer shift is still a
longer arrow (on c1178 at Auto ×13, Full color at 0.56 µm, a 3.99 µm correction
is drawn 10.7 µm long, where it ran to 30 µm and across the view). An arrow is
also drawn no longer than 10 % of the diagonal, but a shift that is already
longer is drawn at its true length:
the three RC2 annotations corrected by about 159 µm are three long arrows, not
2,000 µm ones. A badge (`×13`) is always on the viewport chip when the
exaggeration is more than 1, and every number the section prints is the true
shift in micrometers. **Original rings** also draws each annotation where it
was, a ring at the arrow's tail (a closed curve's traced outline, moved by the
same amount), joined to the real one by the arrow.

There are arrows at annotations only, on purpose. Between annotations there is
nothing to draw: on c1178 a shift predicted from the other annotations within 3,
10 or 30 µm on the same section explains less of its variance than zero does
(-81, -87 and -39 %), so an interpolated field would show structure that is not
in the data.

**How many.** **Only shifts above** hides moves shorter than that, in true
micrometers (0.25 µm for every section, 0.05 µm for a slice; each keeps its own
value). Every section draws the strongest 2,000 at most (the facts line says
`strongest 2,000 of 5,321` when that bites); a slice keeps 15,000. Switching
**Between** to a pair that moves things by hundredths of a micrometer (the
current transforms) lowers the thresholds so it shows something, and switching
back gives yours back (within the page session; after a reload the lowered
value stays until you move it).

**One slice at a time.** Tick it to look at one section at a time, under one rule
for everything of the cell: **the slice is drawn at full strength, and the rest of
the cell at Outside the slice of its opacity.** The slice is the plane's section
and **Slice ± sections** either side of it (0 by default: the section alone). A
translucent plane marks that section, sized to its annotations (and the arrows drawn
on it) with a small margin, not to the whole cell. The colors are the markers': the
arrows and rings of the plane's own section in the selection's yellow, those of the
sections above it in red and those below it in blue (above is the upright view's:
higher sections, unless Flip Z is on). **Outside the slice** (0.05 by default; 0
hides it, 1 draws it as strong as the slice) applies to the other sections' arrows
and rings and to the cell's surface, annotation rings, annotation wireframe,
skeleton and synapses alike: past the slice they are drawn in the same faint red
above and blue below, and the surface keeps its own color only in the band the slice
cuts through it. Looking down Z, that leaves the slice's arrows over a trace of the
cell; from the side, the cell's band at that section between a red top and a blue
bottom. (These two controls replace *Sections each side*, which hid every section
past it, and *Off the plane*, which faded the sections inside it: two fades that
overlapped, so widening the window looked like it did nothing.) While
an annotation of the cell is selected, the plane's section turns pale yellow, so the
selected annotation's own arrow (full yellow, wider, edged) stays apart from the rest
of its section, and the selection's fade (**Others when selected**, hidden while the
slice is on) is not applied on
top. The section slider, the per-section chart (one dot per
annotation, the size of its shift against its section; click or drag across it)
and the chips for the sections where the annotations moved together (`939 · 6 ·
(-0.62, +0.65) µm`: section, annotations, mean move) set it. On the render:

| Key | Slice |
|---|---|
| `]` and `[` | one section up and down |
| `}` and `{` (shift) | ten sections |
| `PageUp` / `PageDown` | ten sections |
| `↑` / `↓` | one section (shift: ten), only while no annotation is selected |
| `Home` / `End` | the first and last section with a shown shift |

Up and down follow the view: `↑` is the higher section, the lower one under
Flip Z. With an annotation selected, `↑` and `↓` walk along its links as they
always did, and the slice follows the annotation (or the red or blue marker
moved last) to its section. With nothing selected and not in slice mode, they still pitch the view.
Stepping moves no camera, adds no undo step and is not saved: the slice is one
number per volume, and a key moves every volume in slice mode by the same
amount. **Look down Z** turns the view to XY1, where the arrows are the
two-dimensional vector field of the sections shown; **Y down** looks the same
way with Y running down the screen, the way an image is indexed. Only **Reset
view** re-frames; nothing in this section does.

**Color.** **Magnitude** runs a green, teal and violet ramp up to **Full color
at** (Auto is the 95th percentile of the moves), chosen to stay legible on the
dark and the light (Invert) viewport and apart from the highlight, error and
difference colors; **Direction** gives each angle a hue, so a section shifted
as a whole is one color; **Cell color** is the cell's own. **Through the
surface** and **Opacity** are as for the annotations.

**Legend.** While a cell draws the layer, a legend sits in the bottom right
corner of the render, above the chip: the pair (`Section correction`,
`registered → corrected`), the exaggeration (`×13`), and the colors: the ramp
with its ends and middle in µm (`≥0.56 µm` when some moves are longer than
**Full color at**), the direction wheel (+X to the right, +Y up, in the volume's
X and Y), or the cell's color. It follows the inverted viewport.

**Facts.** The chip beside **Show position shift** says how many arrows are
drawn (`429 shown`, `strongest 2,000`); its hover has
`429 of 2,183 moved shown · median 0.11 · 95% 0.56 · max 3.99 µm`, counted on
the annotations on screen, and the server's verdict on the links between
annotations of adjacent sections: `median step 0.237 to 0.182 µm · 65% shrank,
29% grew · over 1.5 µm 116 to 71`. The chip at the bottom of the
render (`Shifts · ×13 · section 939 (84.51 µm) ±5 · 53 shown`, with a stepper in
slice mode and an Off button) is there while a cell draws the layer; with
several cells it lists each one's exaggeration. Selecting an annotation adds
`shift 0.14 µm (+0.12, -0.08)` to the pick readout.

**The selected annotation's own arrow.** With an annotation selected (a triple
click: the yellow ring), its arrow is drawn in the selection's yellow, wider than
the rest, over a dark edge and over everything, with the ring where it was in the
same color while **Original rings** is on; the red marker's and the blue marker's arrows are drawn in red and
blue. **Arrows** and **Original rings** are the switches for these too: with the
rings off, no ring is drawn, the selection's included. It is drawn whatever **Only shifts above**, the 2,000 cap or the slice leave
out, at the exaggeration the layer uses (the badge). Every other arrow and ring is
drawn at **Others when selected** of its opacity (0.06 by default, almost nothing;
0 hides them, 1 leaves them as they are), until the selection goes. An annotation the pair did not
move has no arrow, and then nothing is dimmed. The marker table under the readout
has a **Shift µm** column: each marker's true shift, with X and Y in its hover. The
**↗** beside each row's Viking numbers shows that annotation in Viking (as the
right-click menu's Open in Viking does; Windows only).

The layer follows the cell's visibility, volume offset and Z range, Flip Z and
the orthographic camera. It is never picked, and adds nothing to the scene's
bounds. The settings are saved with the cell, travel with **Apply this cell to**
the others, and are undoable. The data are what the viewer already holds: the
shift of each annotation comes with `/nodes`.

### Moving an annotation by hand

Where Viking's section correction leaves an annotation off its neighbors, you can
move it further yourself. Nothing is written to Viking: a move is kept with the
cell in this page (and in its saved sessions), sent with each load of the cell,
and the server draws everything from a copy of the cell with the move applied --
the surface, the rings, the skeleton, exports and measurements.

**Start.** Select an annotation (a triple click on its ring), then press `g`, or
choose **Move annotation … by hand** in the right-click menu, or the ✥ in its row
of the marker table. The arrow keys move the marker they act on: the yellow
selection, or the red or blue marker chosen or moved last. A panel under the
marker table shows:

- where the annotation is, as moved: X, Y and Z in µm and its section (with
  Viking's section beside it after a Z move);
- how far it has been moved (ΔX, ΔY in µm, ΔZ in sections), and *not yet in the
  render* until the move is applied;
- the viewport's axes: a small drawing of the volume's X (red), Y (green) and Z
  (blue) as the screen sees them, with Xp to the right and Yp up;
- the axis a step follows, and the step (0.01 to 1 µm, 0.05 by default).

| Key, in move mode | |
|---|---|
| `←` `→` | one step back or forward along the axis (Shift: ten steps; Alt: a tenth) |
| `1`–`7` | the axis: X, Y, Z, Xp, Yp, Zp, Shift |
| `Enter` | apply the moves to the render |
| `Esc` | done (a move not yet applied is applied) |

**The axes.** **X** and **Y** are the volume's. **Z** moves one section a step,
whatever the step size: an annotation is a disc or an outline in its section's
plane, never between two, so a Z move puts it on another section. **Xp**, **Yp**
and **Zp** are the viewport's: right on the screen, up it, and out of it toward
you. They move the annotation in its section plane, along the screen axis laid
flat into the plane (a full step whatever the view's tilt), and never off it:
where the axis points along Z (within about 14°: Yp seen from the side, Zp seen
from above) it moves nothing and says so; choose another, or **Z** to put the
annotation on another section. (Until 2026-10-20 such an axis stepped a section
at a time, which carried the annotation along its own process.) **Shift** follows the annotation's own shift, the arrow of the
pair Position shift draws (else the section correction): `→` carries on the way
the correction went, `←` goes back toward where it was.

**Moving several at once.** **Move** in the panel says what a step moves:

- **This one**: the chosen annotation, and any you add with a ⌘-click (Ctrl-click on
  Windows) on a ring of the same cell; a second ⌘-click takes it out again (a ⌘-drag
  still turns the view);
- **Its section**: every annotation of the cell on the chosen one's section, for a
  section that is off as a whole;
- **Sections**: every annotation of the cell on the sections from and to (both
  included); **Red to blue** sets them to the sections between the red and blue
  markers, so ↑ and ↓ can mark the run first.

Each annotation of the set gets the same step from where it already was, so earlier
moves stay apart. While a set holds more than one annotation its rings are drawn in
pale yellow where the moves put them, the panel counts them (`Moving 37 annotations
on sections 1103–1105`), and **Undo these moves** takes the whole set back.

**Applying.** The yellow ring is drawn where the move puts it at once, and so is
the skeleton (when it is shown): its line through a moved annotation bends to
where the move puts it, step by step. The surface waits for `Enter` (or **Apply
to render**, or leaving move mode). The server applies the move set to the copy
of the cell it holds for the cell's registration setting -- no request to Viking
-- and the cell is drawn again; the selection and the markers follow their
annotations. While it builds, the cell stays on screen as it was, and each part
(surface, rings, skeleton) is replaced as its new one comes. Surfaces built with
an earlier set stay cached, so undoing a move draws its surface at once.

**Only the part that moved is built again** (Voxel remesh, Hybrid, Convolution,
Ultraliser: the techniques that contour the whole cell on one grid). When move
mode starts, the server keeps the grid the cell's surface was contoured on (built
in the background if the surface came from the cache); Apply builds the moved
copy on that same grid and contours again only the blocks whose field the moves
changed. The result is exactly what contouring every block on that grid gives, and
everywhere else the surface keeps its triangles, where a fresh build would have
moved all of them slightly (its grid follows the whole cell's area). On Inferior
c1178, one dendrite annotation moved: 2 of 2,456 blocks contoured again. A disc of
a soma that was built as one field moves that whole field (its level is fitted
to the whole soma), so all of the soma is contoured again. Grids are kept only for
cells move mode was started on or that have moves, at most 512 MB of them on the
server, the oldest let go first (`SBFSEM_GRID_CACHE_MB`); a cell nobody moves costs
nothing.

The steps after the contour are local too, where they can be. **Smoothing** (Taubin,
Smooth passes) is done again only for the vertices a few edges from a block that
changed; every other vertex keeps the place the last build smoothed it to, which is
the same, to the bit, as smoothing the whole surface. **Ties** (the wires that join
the surface's separate pieces) are the last build's when the surface still falls
into the same pieces, the change is inside one of them, and every other piece is
farther from it than the longest tie (**Tie gaps ≤**, 20 µm); otherwise the gaps are searched
again, as before. **Decimation** to the Face cap still runs on the whole surface: it
removes the cheapest edges across the whole cell until the count is reached, so
where it stops depends on every part of the cell, and a local version would leave a
seam of undecimated triangles around each move. On c1178 a reused Apply is
then about 1.5 s of building instead of 3.2 s (the result identical to tying and
smoothing the whole surface).

**Kept with the registration setting.** A move is made from where the cell's
Registration settings drew the annotation, so each cell keeps its moves under the
setting they were made under (Curve fit correction with the current transforms on,
say). Switching the Registration settings draws the cell with that setting's
moves, if it has any, and keeps the others for when you switch back; the
**Manual moves** section says how many are kept under other settings.

**The list.** The cell's **Manual moves** section lists every move under its
current setting, newest first: Location ID (click: select it and look at it),
section, ΔX, ΔY and ΔZ; ✕ deletes one, the boxes and **Delete** delete several (a
click on a section number ticks every move on that section, Shift-click on a box ticks
the rows between it and the last one ticked),
**Reset all** deletes them all (the other settings' stay), **Apply** applies moves
not yet in the render, and **Move…** starts move mode. **Show moves** draws an
arrow for each moved annotation, from where the correction drew it to where it was
moved, at true scale: light gray, or the marker's color for a chosen one. Position
shift's arrows still end where the correction drew the annotation, so the two
read one after the other. The pick readout's and the marker table's Viking numbers
take a move back, as they take the correction back: they name Viking's own place.

A move is at most 100 µm in X and Y and 50 sections in Z. Moves travel with undo
and redo and with saved sessions, not with **Apply this cell to** (they are this
cell's annotations); the bulk SWC archive is built from each cell's plain copy.

### Visualizations: a cell's whole look at one pick

An open cell's second line is its **Look**: a dropdown of visualizations, each a
cell's whole look under one name -- the technique and every render option, the
surface's material and opacity, what is shown (surface, wireframe, annotations,
annotation wireframe, skeleton, synapses and each synapse type, Position shift,
manual move arrows) and every color, width and opacity of them. Choosing one draws
the cell so; what a visualization leaves out is the default, never what the cell
had. A cell keeps its own color (so a look given to many cells still tells them
apart), its Z range, its Registration settings and its manual moves. The dropdown
names the visualization the cell shows exactly, or says *Custom look*. What the
line used to say -- the volume, the technique, the label -- is the hover of the
line and of the volume tag.

The list starts with the lab's own looks, read from the workspaces saved on the
server, and three that put one thing forward:

| Visualization | What it is |
|---|---|
| App default | Voxel remesh, an opaque surface, nothing else |
| James · Closed curve in clay, skeleton and rings | Closed curve at 0.125 µm (curved links, bodies joined within 0.25 µm), a clay surface at 0.35, the skeleton as 0.1 µm tubes, fine dark rings (James's workspaces of 2026-10-16; their Smooth discs and outlines are left off, since the app moves no annotation by default) |
| James · Hybrid in glass, colored by IPL depth | Hybrid under Voxel remesh colored by depth, a glass surface at 0.8, faint blue rings (2026-09-18) |
| James · Faint surface with synapses | a 0.16 surface colored by depth, postsynapses as 0.3 µm ovals, ribbon presynapses hidden (2026-08-29) |
| Crystal · Skeleton and rings, no surface | Hybrid, the surface hidden, the skeleton as lines and bright yellow rings (Crystal's RC2 workspaces and the shared ALLofUS ones) |
| Becca · Skeleton over the annotations | the skeleton as 0.08 µm tubes over the rings through a barely-there surface; Becca has no workspace saved here, so this one is put together from her skeleton work |
| Emphasis · Skeleton | 0.15 µm skeleton tubes over a faint matte ghost of the surface |
| Emphasis · Annotations | every ring at full strength through a faint surface |
| Emphasis · Annotation error service | what Viking's section correction did: Position shift on with each annotation's ring where it was, the rings where they are, a translucent surface, and manual move arrows |

**Your own.** *Save this cell's look as…* keeps the open cell's look under a name,
in this browser; it is then in every cell's list under *Saved in this browser*, and
saving under a name already there replaces it. *Manage, share and import…* opens a
small window with every visualization and a box for each: **Delete** removes the
ticked ones saved here, **Save to a file** saves the ticked ones (or every one saved
here) as a `.sbfsem-vis.json` file to send to someone, **Import from a file…** brings
in the looks of such a file (one of the same name is replaced), and **Apply to this
cell** applies the one ticked. A file holds only what differs from the defaults, so it
reads as what the look is.

**From the render.** The right-click menu of a cell (on the render or its row) has
the same list under **Visualizations**. Choosing one draws that cell so and, with
more than one cell loaded, offers **Apply "…" to all N cells** where the menu was;
any other click (or Esc) takes the offer away, and only the one cell changed.
**Apply this cell's visualization to** at the foot of the cell panel still copies an
open cell's look to the selected cells or all of them.

The saved ones live in this browser (the Settings tab lists them as *Saved
visualizations*): to keep them through a reset or take them to another computer,
save them to a file.

### Choosing a color

Every color swatch in the app opens the same picker, whatever the browser
(each used to open the browser's own: a small popup in Chrome and Edge, the
macOS color window in Firefox and Safari, which stays until closed by hand).
A hue and saturation wheel with a brightness slider under it; the old and the
new color side by side (click the old one to put it back); hex and R G B
fields; **⌖** picks a color from anywhere on the screen (Chrome and Edge
only, the browsers that offer it); **+** saves the color as a swatch, kept in
this browser (right-click a saved swatch to remove it). Below are the cell
palette and the colors already in use. The render follows as you drag. Click
anywhere else, or press Esc, to close it; arrow keys move the wheel's marker.

### Render settings are per cell

The Render panel is an editor, not a global switch. Changing a control does not
rebuild anything on its own — a mesh build is seconds of server time, so it
happens when you ask for it:

- **loading a neuron** gives it whatever the panel currently says;
- **Apply to selected** rebuilds just the selected neuron;
- **Apply to all** rebuilds every loaded neuron;
- **Rebuild all renders** re-fetches everything under each neuron's *own*
  settings, discarding the browser's mesh cache.

So a cone pedicle drawn as a closed-curve volume and a ganglion cell drawn as
Line3 cylinders can sit in the same scene, each in its correct place. A shut
row in the Loaded list shows a two-letter tag for the cell's volume (`im`, `rc1`,
…) whose tooltip names the technique and says when the cell is colored by IPL
depth; an open row spells out the volume and the technique under the cell's
number. Selecting a neuron loads its settings back into the panel, so the panel
always describes the cell you are looking at.

Apply to selected counts the cells it would act on (**Apply to 3** when three
are ticked), and when every one of them already uses the panel's settings the
status line answers "Those cells already use these settings" and nothing is
rebuilt. (A one-line note under the buttons that said whether the panel matches
the selected cells is in the page but has never been shown.) Exports use each
neuron's own method, so a `.stl` matches what is on screen rather than what the
panel happens to say.

### Navigation

Left-drag orbits, the wheel zooms, and **right-drag pans** (two-finger drag on a
trackpad). Orbiting has no stops: drag past straight up or straight down and the
view carries on over the top, turning the scene upside down if you keep going —
press `m` to set it the right way up again. Panning is in the plane of the
screen, so a neuron viewed edge-on still slides the way you expect.

The line at the top right of the render lists these drags and keys. When the
render is too narrow for it to sit beside the pick readout (under about 785 px
of render width: a window of about 1090 px with the cell list at its default
width), the line folds into a **?** at the top right; click it to open the
list under the readout, and again to close it. Folded, the pick readout keeps
to the width left beside the **?** and the view cube: a field too long for it
goes onto two lines (the cell, then its µm; the Location ID, then its volume),
the marker table scrolls sideways inside its own box, and the move panel's
lines wrap, so the page never scrolls sideways.

**Wheel zoom is aimed by the mouse pointer** (as in Autodesk Fusion 360): the
camera dollies along the ray under the pointer, so zooming in with the pointer
off-center pulls that part of the view toward the middle of the screen — the
further the pointer is from screen center, the more angled the zoom. With the
pointer dead center it is a straight in-and-out zoom. The keyboard `z`/`Z` zoom
stays centered.

**Zoom and pan are scaled by what the pointer is over.** Each wheel notch covers
5 % of the distance to the surface under the pointer, and a right-drag keeps the
surface you grabbed under the pointer, at any magnification — so you can keep
zooming toward a surface for as long as you like, and the view stays as quick
to pan and turn close up as it is from afar. Over empty space the wheel keeps
the step it had when the pointer left the cell, and within a quarter of a micron
of a surface the step stops shrinking and the wheel carries on through it. The
near clipping plane follows the zoom, so a surface is no longer cut open as you
approach it. (Before 2026-09-18 the step shrank against an invisible pivot
point: after a long zoom every notch, pan and turn crawled until you zoomed back
out.) Orthographic zoom is a magnification and was never affected.

**Single-axis rotation** holds a modifier during a left-drag, and every axis is
the *viewport's*, not the volume's — after a roll they follow the rolled
screen:

| Modifier + left-drag | Rotates about |
|---|---|
| shift | the axis coming out of the screen (roll) |
| ctrl | the screen-horizontal axis only (pitch) |
| ⌘ (alt on Windows/Linux) | the screen-vertical axis only (yaw) |

The MATLAB `RenderApp` keyboard navigation is preserved alongside it:

| Key | Action |
|---|---|
| arrows | azimuth and elevation, 5° per press (`↑` and `↓` step the Position shift slice instead while it is on and nothing is selected; with an annotation selected they walk along its links) |
| `g` | with an annotation selected: move it by hand ([Moving an annotation by hand](#moving-an-annotation-by-hand)); in move mode `←` `→` move it, `1`–`7` choose the axis, `Enter` applies, `Esc` is done |
| `q` `e` | roll about the view axis, 15° (shift, or `Q` `E`: 90°) |
| `z` / `Z` | zoom in / out |
| `m` | reset the view (since 2.26.1 wherever the focus is, not only after a click in the render; not while typing in a box or with a dropdown focused) |
| `[` `]` `PageUp` `PageDown` `Home` `End` | step the Position shift slice, while it is on |
| `p` | turn about the point under the pointer from now on, or about the middle of the view again (see below; also wherever the focus is) |
| `f` | full screen on or off (see below) |
| `c` | copy the picked point as “X Y Z downsample” for Viking's Goto Location |

### Where a turn pivots, and choosing the point

Every turn of the view pivots about one point: a drag with or without shift,
ctrl or ⌘/alt, its glide, the arrow keys, `q` and `e`, the view cube and the
viewpoint buttons. Unless you choose one, that point lies on the line through the
middle of the picture, at the depth of what is there, so whatever is in the middle
stays in the middle as you turn.

**The depth is measured again as a turn starts** (since 2.34.0) when it has plainly
gone off what is in the middle. The wheel keeps the depth of the last surface it was
over while you zoom over empty space, and Orthographic never moved it at all, so
zooming in past a thin process left the pivot behind it, and the next turn threw the
process out of the picture. Measured on Inferior Monkey c943, the pointer held still
80 px beside a thin process: after 17 wheel steps the process was 5.1 µm away and the
pivot 23.1 µm behind it, and a 60 px drag (about 32°) moved the process 1,561 px, off
the 1,134 px view; now the same drag moves it 144 px and it stays in view. A view
framed by Reset view is far inside the line (the pivot within a quarter of the
picture's half height of what is in the middle), so its turns are as they were.

**To keep something away from the middle in place while you turn, put the pivot on
it** — zoomed in on a process near the edge of a large view, a turn about the middle
swings it out of the picture:

- point at it and press **`p`**; or
- right-click it → **Rotate about this point (P)**; or
- tick **Pivot** in the toolbar, then click the point (the pointer is a crosshair until
  you do; `p` or `Esc` stops waiting).

On a cell the pivot goes to the middle of the annotation there, so a process turns
about its own axis; pointing at empty space just beside a thin process finds the
nearest annotation within 24 px. Close up on a soma, where that middle is more than a
quarter of the picture away from the point you pointed at, it is that point instead.
Setting it moves nothing.

While it is on, an amber ring marks the point (drawn over the render, never in a
snapshot or a video, and seen through whatever is in front of it), the label
**Rotating about a point** sits under the view cube, and the Pivot box is ticked.
Every turn then keeps the point at the same pixel and the same distance (the browser
check measures 0.00 px of movement over a plain, shift, ctrl and alt drag, the arrow
keys, `q`, a cube turn, XY1 and a cube drag). Zooming in keeps it.

It turns off with `p`, `Esc` (after any selection: one `Esc` per thing), a click on
the label, unticking Pivot, **Reset view**, a jump to an annotation (a table row, a
Location ID, a Viking location; the jump puts its point in the middle, where turns
are about it anyway) and **Flatten IPL**; and by itself when the point leaves the
picture, when you zoom out to more than 4× the scale you set it at, or when its cell
is hidden. The status line says which. **Flip Z** and a volume offset carry it with
its cell. It belongs to the page: it is not saved with the session or a workspace,
and a reload starts without it. Render compare's panes have it too, one for every pane
with Sync cameras (see *The pivot point* under [Render compare](#render-compare)).

### Full screen

**Full screen**, at the right end of the render toolbar (or `f` with the render focused), fills
the screen through the browser's Fullscreen API. The browser's own full
screen does the same: **F11** in Chrome, Edge and Firefox on Windows and
Linux; on macOS **⌃⌘F** in Chrome, Edge and Safari and **⌘⇧F** (or ⌃⌘F) in
Firefox; recent macOS also has the globe/fn key with F. The page notices
either way (the `display-mode: fullscreen` media query in Chromium and
Firefox, or a viewport the size of the screen) and reclaims the space: the
title bar goes, the status line floats over the right end of the tab strip,
and the cell list folds away so the render has the whole width. **≡ Cells**
at the top left brings the list back, and that choice is remembered for the
next full screen. Leave with `f`, the Full screen button, Esc (Fullscreen
API only) or the browser's shortcut. Safari with *Always Show Toolbar in Full
Screen* on keeps its toolbar, which the page cannot see past; the button
and `f` work there regardless.

### Dropdown lists in a virtual machine

On Windows and Linux, Chrome and Edge draw the list of a dropdown as a window
of its own, apart from the page. In some virtual machines that window does not
draw, draws in the wrong place or takes no clicks (reported from Chrome in a
Windows 11 ARM64 guest under Parallels on a Mac). macOS and Android use the
system menu instead and never show the fault.

**In-page dropdowns**, a checkbox in **Display & context**, draws the list
inside the page instead, where the page's own surface carries it. The dropdown
you see and click is still the browser's; only the list that opens from it is
replaced. A list opens with a click, and from the keyboard with **Space**,
**Enter**, **F4** or **Alt+↓ / Alt+↑**, exactly the keys that open the
browser's own. The arrow keys alone still step the value in place, as they do
in Chrome on Windows. In an open list ↑ ↓ Home End and Page Up/Down move,
letters jump to a row, **Enter**, **Space** or **Tab** pick, and **Esc** closes
only the list. A click on empty space or on the 3D view closes it without doing
what that click would have done (it will not pick a cell); a click on a button
or on another dropdown works at once, in the same click.

- **The default** is on in Chrome, Edge and other Chromium browsers on
  Windows, Linux and ChromeOS with a mouse or pen, and off on macOS. The box is
  remembered in this browser (`sbfsem:select-lists`). Tick it if lists draw
  blank, in the wrong place or ignore clicks; untick it to get the browser's own
  back.
- **Touch screens always use the system list**, whatever the box says, and
  Firefox and Safari keep their own lists (the box is hidden there).
- **What it does not cover:** the suggestions that drop down from a typed field
  (the edge-type box in Connectivity) and the small tooltips that appear when
  the pointer rests on a control are also drawn by the browser; if those
  misbehave in the same machine, say so.

If dropdowns still fail with the box ticked, these narrow it down, in order of
what each one teaches per minute:

1. **A 30-second test of the cause.** Paste this into Chrome's address bar
   inside the machine (it needs Chrome 135 or newer):
   `data:text/html,<select><option>One<option>Two<option>Three</select> <select><option>One<option>Two<option>Three</select><style>select:last-of-type,select:last-of-type::picker(select){appearance:base-select}</style>`.
   The left dropdown is the browser's, the right one draws its list inside the
   page. Left fails and right works: the separate window is the cause, and the
   in-page list is the fix. Both fail: the page itself is not being drawn
   properly, and no list can help; go to 3 to 6.
2. **Use the keyboard** meanwhile: Tab to a dropdown and press ↑ or ↓ (they
   change the value without a list on Windows), or type the first letters of the
   entry you want. The viewer listens to both `input` and `change`, so it
   behaves the same as a click.
3. **Parallels, View menu:** leave Coherence, and try Window or Full Screen.
4. **Chrome:** open `chrome://settings/system` and turn off *Use graphics
   acceleration when available*, then restart; or try another backend under
   `chrome://flags/#use-angle`.
5. **`chrome://version`** should say `(arm64)` on an ARM guest; an x64 Chrome
   running under emulation is a known source of this kind of trouble.
6. **Parallels, configuration:** turn the mouse setting "optimized for games"
   off, try the graphics settings without Retina, and update Parallels Desktop
   and Parallels Tools.

If the lists work at the 30-second test but not here, check which build the
browser loaded: a tab opened before an update runs the old page until it is
reloaded (`/api/build` shows the server's `assets` stamp).

### Undo and redo

**⌘Z / Ctrl+Z undoes and ⇧⌘Z / Ctrl+Y redoes** — up to 100 steps, covering
everything: loading and unloading cells, colors and per-cell display settings,
render settings, selection, the active tab, and **viewport moves** (every
finished drag, roll, single-axis rotation, zoom, or reset is one step). The
Undo/Redo buttons next to Reset view do the same. Undoing an unload brings the
cell back with its colors and settings intact; undoing a settings change reuses
the mesh cache, so stepping back is fast, and undoing a switch of **Viking's
current section transforms** or its **section correction** loads the cell again
under the earlier choice. A step puts back every Display & context setting as it
was, off as well as on: undoing a crop clears the crop (and Hold), undoing a volume
shift puts the volume back in its frame, undoing a boundary or vessel sheet hides it,
and undoing a Hide above/below restores the cell's Z range. The shortcuts work while
a checkbox, slider or dropdown has the focus (not in a text box, where they undo
typing), take one step per key press, and wait for a step that is still being applied.

**The pick readout** at the top left of the render follows the pointer, in the
3D render and in GraphApp alike: as the pointer moves over the view it names the
annotation nearest it, and changes as soon as another one is nearer. Nothing is
drawn to show which annotation that is; selecting (below) is for that. In the 3D
render it is the annotation nearest the point of the surface under the pointer (a
surface at least half opaque; the surfaces are 0.92 by default), or a ring drawn in
front of that surface, of a cell shown as rings or a skeleton; over no cell, the
annotation nearest the pointer on the screen. In GraphApp it is the dot nearest the
pointer, the one a click there selects (of dots within half a pixel of each other,
the one nearest you). A cell's annotations are fetched the first time the pointer
crosses the view with that cell shown. The readout names the cell and the
annotation's position in microns, then, after a separator, **Viking X Y Z
downsample** — the annotation's pixel and section coordinates as Viking stores it
(the section correction and any manual move taken back) plus the downsample the
view corresponds to (image pixels per screen pixel at that point; it follows the
view as you zoom) — and after another separator its **Location ID**, with the
volume it is numbered in. A click on the render leaves the readout to the pointer
(it still makes the clicked cell the selected row of the list). Moving the pointer
off the view leaves the readout as it was.
Left-click the Viking field to copy the four numbers for Viking's *Commands →
Goto Location*, or the Location ID field to copy the ID for *Annotation →
Goto Location ID*; `c`, with the pointer on the view, copies the Viking field of
the annotation under it. The numbers are
space-separated (`5120 3810 371 2`): Viking's paste parser treats a comma as
a digit-group separator, so a comma-separated string arrived as `5120,` and
was refused. Right-click either field
to type or paste one instead: a Viking location (`X Y Z` or `X Y Z
downsample`; commas are accepted here) or a Location ID, and Enter flies the view there (a downsample
sets the zoom; in orthographic view it sets the ortho zoom). A Location ID a
loaded cell holds is answered from that cell, and the annotation is selected:
drawn yellow, as a triple click on its ring draws it (a ring at its radius when
the cell's annotations are hidden), and the readout and the marker table name
it. Any other is looked up in Viking, the view goes there, and the status line
names the cell it belongs to with a *Load* button if that cell is not loaded;
loading it from there selects the annotation too. Viking's *Show in SBFSEM-tools*
(`/open?…&location=N`) lands the same way: the cell loaded, the annotation
selected.

**Selecting an item: left triple click.** Triple-click an annotation ring or
outline, a skeleton, a wireframe or a synapse marker and it lights up — yellow
with a glow (cyan on a yellow cell) — and the status line names it, with its
volume. It is drawn at full strength as you choose it (a tube inside a glow three
times as wide) and settles within about a second and a half to a calmer one: the
tube four fifths as thick, the glow two thirds as wide and as bright. A ring that
is only redrawn (you zoomed) or previewed from a menu row is drawn settled, and
so is every ring when your system asks for reduced motion. Anything within six pixels of the pointer counts, so a one-pixel
skeleton line or a ring seen edge-on is easy to hit. The surface is never
selected: it is what everything else is drawn on. When more than one item is
under the pointer — an annotation, the skeleton and the wireframe, or a stack of
rings seen down Z — a small menu lists them, nearest first; each row lights its
item up as the mouse passes over it, and a click selects it. Items behind an
opaque surface are left out, except annotations drawn *Through the surface*.
A skeleton is selected one branch at a time.

**A selected annotation fills the pick readout** until the pointer reaches
another annotation. The readout at the top left of the render switches to the
annotation you selected: *Location ID N (volume)*, and the µm and *Viking X Y Z
downsample* of that annotation's own position as Viking stores it — so a click on
the ID, or `c`, copies exactly what Viking's *Goto Location ID* and *Goto
Location* take. When you choose a different annotation from the menu, the readout
names the one chosen. With markers (below), the readout names whichever was chosen
or moved last, in any color; the marker table keeps every marked annotation's
Location ID and Viking X Y Z, each a click to copy, while the readout follows the
pointer. Deselecting leaves the readout as it is.

**Deselecting.** Any of: press `Esc` (from anywhere on the 3D render tab; with
a menu open the first `Esc` closes the menu, and with red or blue markers each
`Esc` takes one, the one moved last first; a marker that holds a whole z-slice
goes back to its one annotation first); click once on empty space in the
render (a drag from empty space still only orbits, and a click on a cell or on a
ring keeps the selection); right-click → *Deselect*, on a cell or on empty
space; or the ✕ on the yellow row of the marker table. Selecting something else
replaces the selection, and a triple click where nothing can be selected clears
it. The status line reads *Selection cleared*.

**Markers above and below: red, yellow and blue.** The selected annotation is
drawn **yellow**: it is the reference. Two more annotations of the same volume can
be marked against it, one **red**, always on a section *above* the yellow one,
and one **blue**, always on a section *below* it. *Above* is toward the top of the
upright view, as for *Hide above*: higher sections, unless *Flip Z* is on (turning
Flip Z on or off swaps the two colors, because it swaps the two sides).

- **Triple-click another annotation** of the same volume: above the yellow one it
  becomes the red marker, below it the blue one (in place of a marker already
  there). One on the yellow one's own section becomes the yellow one, the red and
  blue staying where they are; the yellow one itself, or a marker, is drawn
  again as when it was chosen and becomes what the arrow keys act on (below). The
  chooser's title, and the right-click list, say so when several are under the
  pointer. A triple click offers the ring under the pointer and any ring about as
  near (within 1.5 px of it), not the rings of the neighboring sections a few
  pixels off: at a whole cell's zoom those are 2 to 5 px away, inside the 6 px a
  ring can be picked from, and it used to list six of them (2.8.1). Triple-click anything else — a skeleton,
  a synapse — and it replaces the selection as before, and the markers go.
- **`↑` / `↓`** step the marker moved last to the next annotation above or below
  along the cell's own Viking links; with no marker, the first press starts one
  from the yellow annotation, red going up and blue going down. **Click the yellow
  ring** (once, or a triple click, or its row's dot) and the next press starts a
  marker from the yellow again: `↓` adds the blue one while the red stays where you
  left it, and `↑` the red one beside it (in place of a red already there, as a
  triple click on a ring above does). A click on a red or blue ring makes that one
  the marker the arrows move. The walk follows
  the process: links are walked breadth first, never back past the section it
  started from, so a stretch that runs level before it climbs is followed; at a
  branch the nearest section wins, then the nearest in XY. Where nothing is linked
  further, the status line says so and the marker stays. The camera does not move.
- **A marker stepped onto the yellow annotation goes.** One that would pass the
  yellow annotation another way (another branch) changes color as it crosses, if
  it is the only marker; with both markers set it stops, and the status line says
  to step or remove the other one first. Pressing on after a marker went starts
  one on the other side.
- **`Shift`+`↑` / `↓`** move the yellow annotation itself the same way; a marker
  it lands on goes, one it passes changes color if the other side is free, and the
  move stops when a marker would end up level with it or both on one side.

**A whole z-slice.** With an annotation selected, the right-click menu has *Select
all annotations on z-slice S (same cell only)* and *Select all annotations on
z-slice S*. They act on the yellow annotation, or on the red or blue one when the
menu was opened on that ring (or on one of its z-slice), else on the one the arrow
keys act on; with a marker set the item names the color: *… z-slice S (red, same
cell only)*. The first takes every annotation of the annotation's own cell on its
section, the second every annotation of every loaded, shown cell of its volume on
that section, and draws them all in the annotation's color (a cell whose
annotations are not shown gets a ring at each annotation's radius). The annotation
itself stays the one the readout, the table, *Hide above / below* and *Move
annotation* go by; the table's Z reads *625 ×3* (three on section 625). The item
in force is ticked; choosing it again, or `Esc`, puts the marker back to its one
annotation.

The arrow keys then step **by section, not along the links**: to the next section
above or below that has annotations in the same scope (the cell, or the loaded
cells), and take all of them. From the yellow z-slice `↑` and `↓` start a red or
blue z-slice, as they start a marker from a single annotation; a red or blue
z-slice steps on a section at a time, and goes when it reaches the yellow one's
section; `Shift`+`↑` / `↓` move the yellow z-slice, which stops at a marker's
section and takes its place (z-slices meet; they never pass). The ring that carries
the readout after a step is one of the same cell, the nearest in XY to the one
before. A marker that is a single annotation still walks the links.

**The marker table.** Under the readout, whenever something is selected, a small
table has one row for each marker that is set — red, yellow, blue, top to bottom,
as they are drawn — each in its marker's color (cyan and pink on a yellow cell,
orange on a red one), in columns:

| Column | What it is |
|---|---|
| ● ▲ ▼ | Click the dot to draw the marker again as when it was chosen (and make it the one the arrow keys move); ▲ and ▼ step it, as `↑` and `↓` do (the yellow row's move the yellow annotation) |
| Cell | the cell the annotation belongs to |
| Location ID | Viking's ID of the annotation; click to copy it, for *Annotation → Goto Location ID* |
| Viking X Y Z | where Viking has it, a section correction taken back; click to copy it with the downsample the view is at there, for *Commands → Goto Location* |
| Z | its section; *×N* when the marker holds the whole z-slice (N annotations) |
| ΔZ | sections from the yellow annotation, plus above and minus below, as Viking counts them |
| ΔZ µm | the same in microns |
| Dist µm | the straight-line distance from the yellow annotation in the volume, X, Y and Z together |
| ✕ | remove the marker (on the yellow row: deselect) |

Each copy names the volume in the status line: an ID means nothing in another
volume. A selected skeleton branch, wireframe or synapse type has a yellow row
too, naming the annotation nearest the point picked. The column headings explain
themselves on a hover. With the arrow keys taken by the markers, `←`/`→` still
orbit.

**The right-click menu.** Right-click a cell in the render, or its row in the
Loaded list, for that cell's menu: Update, Remove, colors, *Hide above / below*,
the annotators, *Open in Viking*, *Open GraphApp*, the analyses and the exports.
In the render it opens **when you let go of the button**, and only if the pointer
has not moved more than 8 pixels since you pressed: a right-drag is the pan, and
it starts from a cell as well as from empty space. (macOS asks for the menu as the
button goes down; opening it there put it under the pointer of every pan that began
on a cell, and the pan stalled.)

*What counts as being on a cell.* The pointer does not have to be on the surface
exactly. A thin process is a pixel or two wide, so when nothing is under the pointer
a surface within 4 pixels counts; annotation rings, a skeleton branch and the
wireframe count within 6 pixels, as they do for a triple click, and so do the
annotation wireframe and the position-shift arrows. A cell drawn with only those,
its surface hidden, opens its menu like any other. What a crop or a *Hide above /
below* has cut away is not there to be clicked, an orthographic view clicks the
whole of the picture (also the part an orbit has carried behind the camera), and each
pane of **Render compare** opens the compared cell's menu, whichever of its layers (or
its annotations overlay) is under the pointer, the cell listed once however many layers
meet there (a pane has no selection, so a row of its list only says what the menu is
about).

*More than one thing under the pointer.* When the click is over several things at
once — several annotation rings of one cell or of two, a skeleton branch, a synapse
type, the surface of one cell or of two translucent ones — **the same menu begins with
a list of them**, headed *Under the pointer*: nearest the pointer first, then the
specific before the general (an annotation, a branch, a synapse type, then the cell),
each named as the Select menu names it, with its cell, its section and its volume. A
cell is listed once, whether it is its surface or its wireframe that is under the
pointer, and a surface behind an opaque one is not under it. The row with the tick is
what the menu is about — the nearest, until you pick another — and the title says so
(`c1178 · annotation 40233 (Inferior Monkey)`); every item below the list is still
there. Rest the mouse on a row and its item lights up. Click it and it is selected,
as a triple click would select it (with an annotation already selected, another
annotation of the same volume becomes the **red** or **blue marker**), and the menu stays,
now about that row: *Open in Viking* goes to that annotation exactly, *Hide above /
below* are that annotation's, and the analyses, exports and annotators act on its
cell. A row for a cell's surface selects nothing; it only makes the menu about that
cell. With one thing under the pointer there is no list, and the menu is the one
there always was, its title naming the annotation or branch when that is what it is
about. `Esc` closes the menu and leaves the selection; the next `Esc` takes a
marker, and once they are gone, the selection.

**Hide above / Hide below.** With an annotation selected, right-click a cell of
the same volume (in the render or in the cell list): *Hide above annotation N
(section S)* and *Hide below …* hide that cell on one side of the annotation's
section. Use both, from two annotations, to show only the slab between two
sections. **With markers** *Hide above* goes by the red marker and *Hide below*
by the blue one (the yellow annotation standing in for a marker that is not
set), and the menu adds *Hide above red and below blue (keep D µm)*, which shows
only the slab between the two in one step; with the surface hidden, right-click
the rings and the cell's menu has the same items.
*Above* is toward the top of the upright view, so it follows *Flip Z*;
the annotation itself stays drawn. It is a hide, not a re-render: it is instant,
it applies to the cell's surface, wireframe, skeleton, annotations and synapses
together, other cells are untouched, clicks pass through the hidden part, exports
still contain the whole cell, and the range is saved with the session and with
undo. *Restore full render* in the same menu brings the cell back; if a cell is
hidden entirely, right-click empty space for *Restore full render of cN*.

**Skeleton check.** Each cell's panel has a *Skeleton check* section. *Check
the skeleton* reads the cell's annotations and links — nothing is changed, here
or in Viking — and lists what would mislead a path length, largest first:

| Kind | What it is | Where it is fixed |
|---|---|---|
| Loops | annotations linked in a ring; a skeleton file is a tree, so the export drops one link per loop (the row says which) and the ring reads as two parallel branches | Viking |
| Unlinked pieces | annotations no link joins to the piece holding the soma, with the nearest annotation of the cell and the gap | Viking |
| Jumps | a link whose two annotations stand far apart, edge to edge, *for this cell* (4× the 90th percentile of its own gaps, at least 2 µm — an in-plane dendrite traced as circles microns apart is not flagged) | Viking |
| Hairpins | the path doubles back: at least twice the cable for the progress made | Viking |
| Spikes | one annotation far from the line of its neighbors | Viking |
| Spokes | a thin process is linked to the *center* of the wide profile it leaves, so its first link runs inside that profile; listed from 1 µm | the export |
| Stubs | a tip branch no longer than the radius of the annotation it branches from | the export |
| Lamellae | a traced profile at least twice as long as wide, or a stack of them linked on consecutive sections (a sheet, or a process lying flat); the skeleton passes through its center, and a chain along its long axis — built from the union of the stack's outlines, walked at the local thickness, trimmed by half the local width at each end, each node at the sections of the profiles containing it — would measure its cable differently. Listed when the change is 0.5 µm or more either way; the highlight draws the chain with the annotations. The detail gives the chain, what it replaces (every link inside the stack, and of every link leaving it the part inside the outlines), the net, how much of the chain lies beyond the outermost branch, and what an SWC written with the chain gains (`swc_lamellae`, under *Export*), the links joining the chain to the cell included | the export |

Click a row, or step with ◀ ▶: the view flies there, the annotations involved
light up with the links between them (through the surface, whatever layers are
shown), the pick readout takes the Location ID, and *Open in Viking* goes to
that annotation. An annotation error is fixed in Viking, once, for everyone:
fix it there, *Update* the cell here, and check again — the list is forgotten
when the cell's annotations change. A misplaced annotation is listed once, by
the first test that finds it, not once per test. The line under the button gives
the cell's pieces, loops and cable length, with the **length from the rims**
beside it (hover for the detail, including the same correction computed from
stored radii instead of traced outlines). `Esc` lets go of the highlight.

A second line follows a few seconds later: **no shorter than N µm through every
annotation**. The cable length lies between two numbers. The path through the
annotation centers over-measures it, because every sideways wander of a center
from one section to the next — a hand-placed circle, a hand-traced outline,
section registration — is added in. The true centerline passes through every
annotated cross-section, so the *shortest* path that still passes inside every
annotation cannot exceed it. RPC1 c192: 769.4 µm through the centers, no shorter
than 602.4 µm. It is a bracket, not an estimate; where in it the truth lies is
one parameter (`swc_relax`, below), to be set against hand-traced branches.
If that request fails (a gateway timeout on a very large cell, say) the line
says so in red, with the reason, in place of "finding the shortest path…"; the
list above stands without it, and *Check again* asks for it again.
**Branch angles, tortuosity near a junction and the bearing on which a process
leaves one are not corrected** by this or by the length from the rims: a branch
still starts at the center of the profile it leaves.

**Who annotated this cell.** Right-click a cell in the render → *Return list of
annotators*. A panel opens at the pointer with one row per username: how many
annotations Viking attributes to them now, their share of the cell, how many
are **as placed** (never written again since they were created), how many are
**edits** (written well after creation — an hour by default), how many child
structures (synapses, boundaries) they created, and the first and last dates.
The cell's own creator carries a ★. *Copy* puts the table on the clipboard as
tab-separated text, which pastes into a spreadsheet as columns, with the lines
below it. The panel stays open while you read, select or copy from it, and
closes when the focus leaves it — a click anywhere else, `Esc`, or the ✕. It is
read live from Viking, not from the loaded copy of the cell, so it shows the
record as it stands now.

What it cannot tell you is who *placed* an annotation that somebody else later
moved. Viking stores one username per annotation — the last person to write it
— and no history: a mentor who corrects a student's annotation takes it over
entirely, and a student whose every annotation was corrected does not appear at
all. The **edits** column is as close as the record comes to "corrected
somebody's work", and it names only the corrector. Writes that are nobody's
annotating are separated out, and counted as **re-stamped** in the line under
the table rather than as anyone's edits. Two signs give them away. One is tens
of annotations a minute under several usernames at once: a re-registration
writes every row. The other is edits made while the rest of the volume was
being rewritten by the hundred. A sweep such as Viking's Update Volume
Positions reaches one cell a few annotations a minute, often under one name,
so the cell alone cannot show it. For each stretch of later edits, the panel
asks Viking how many old annotations of other structures were rewritten at the
same time. People editing make 0 to 4 such rewrites a minute; bulk operations
make 38 to 2,336, and the panel draws the line at 20. On Inferior Monkey
c1178, 1,312 of 2,819 annotations are re-stamped in five bulk edits. Counting
them as corrections would have said that 58 % of the cell had been corrected;
the honest figure is 11 %.

**Open in Viking.** Right-click a cell in the render → *Open in Viking* starts
Viking on that volume and takes it to the annotation under the pointer (the
nearest one within 5 µm, the same rule as the Location ID field; when the menu's
*Under the pointer* list is about an annotation, exactly that one), or to the
point itself when no annotation is that close (at the zoom of your view, but
never farther out than downsample 32, Viking's own starting zoom: a whole-cell
view here would open Viking on a speck). From the cell list the same
item goes to the cell's largest annotation, its soma. It opens in a separate
browser tab, reused from one launch to the next, so the render stays where it
is; the first time, the browser asks whether it may open Viking. A Viking that
is already open goes to the place at once. One that is not yet running starts
with its own sign-in window (it remembers your account, so usually only the
password is asked), then opens the volume and goes to the place. The demo
volume has no such item: it is not a Viking volume.

Viking is a Windows program. On macOS or Linux the item does not open anything
at all: the status line says *Viking client can only be run on Windows.* On
Windows, the tab that opens is a page of ours that hands the link to the
operating system and then watches: a launch takes the focus away from it —
the browser's "Open Viking?" question does, and so does Viking's own window —
so a page that still has the focus six seconds later was handed `viking://`
and nothing answered, which means the client is not installed. It then says
so, with the installer's address
(`websvc.codepharm.net/Software/Viking/Viking-win-Setup.exe`), and withdraws
the notice if Viking turns up late after all. No browser can be asked directly
whether a program is installed; this is the only signal there is, and it is
read conservatively — if the focus is simply elsewhere, nothing is claimed.

**Viking or Viking Test.** The unsigned beta build installs beside the signed
client as *Viking Test* (its own Start-menu entry and its own link scheme,
`viking-test://`; the signed one keeps `viking://`), and the item can open
either. The page cannot ask Windows which of the two are installed, so it
learns from what answers: each launch notes whether its link took the focus
away from the tab, and the browser keeps that for the next click (in this
browser only, never sent to the server). Only on Windows; elsewhere nothing
here appears.

- *Only the signed Viking* (nothing new): the item opens it.
- *Only Viking Test:* the first open in a browser says the usual *Viking
  client is not installed.* — the signed link found no handler — and, under it,
  **Open in Viking Test**. One click on that (a click, because Chrome refuses a
  launch a page starts by itself seconds after it loaded) opens it there and is
  remembered: from then on the item opens Viking Test directly, with no
  question.
- *Both, and both have answered here:* the first open of each session asks
  **Open in which Viking?** and keeps the answer for the session; the next
  opens go straight there.
- **Choose Viking client…**, right under *Open in Viking* in the same menu
  (Windows only), asks on request and then opens there. It is also how a
  machine with both installed first tells the browser about the second one.
- *Neither:* the same *Viking client is not installed.* as before, with the
  signed installer's address.

A program found not to answer is left alone for a week, then tried again, so
an install made since is noticed. Viking's own notes say the two share one
single-instance channel, so a link to the signed program reaches a Viking Test
that is already open on the same volume (not tested here).

### Your session is restored when you say so

The browser remembers which neurons you had loaded, their colors, visibility
and per-cell render settings, where the camera was pointing, which tab you were
on, and the state of the viewport toggles (axes, synapses, skeleton,
orthographic). When you come back, a banner at the top lists the cells it
would restore, by volume (`3 cells from your last visit (…): Inferior Monkey
c1178, c21768; RC1 c606`), and nothing is fetched until you choose:
**Restore** puts it all back, and the status line says how many neurons were
restored and when they were saved; **Discard** forgets them. The saved view goes
up first, so the cells come in where they were, and it turns, pans and zooms
while they do; a view you moved during the restore is kept at its end, and an
untouched one ends exactly as it was saved. Opening a saved workspace works the
same way. Until you choose,
the list is kept, so a tab closed without deciding loses nothing. The volume
and the panel settings come back either way.

A tab you cleared (*Clear all cells*, or the last cell removed) comes back
empty: no banner, and nobody else's cells.

**After a new version is deployed**, an open page shows a banner within 20 s
(*A new version … has been deployed*) with **Reload now**, and the reload restores
your session without asking. A page the browser showed from its own copy (a tab it
reopened at start-up, or Back) used to run that copy's older code with no banner at
all: the restored tab of 2026-10-22 had no GraphApp the day after it shipped. Since
2.30.1 such a page asks the server which version it serves and, when it is a newer
one, reloads itself once before drawing anything; if the reload still comes back old
(a cache between you and the server), the banner says so, and **Clear cached files and reload**
on the Settings tab is the way out. The Settings tab also shows the page's build
beside the server's.

With no saved session in the browser at all, as on a first visit, the banner
lists the cells already open on the server instead, which anyone using it may
have loaded: **Show them** or **Start empty**. They are never brought in without
asking, not even on the reload the new-version notice offers. **Show them** loads
them as a typed list does: a cell with an attached render starts with it, a cell
traced with closed curves starts as Hybrid or Closed curve, and the load strip
reports them with its Stop. Signed in with Viking, you are shown only cells of
volumes you may open.

This is per browser, not per account: it is stored locally, never sent to the
server, and does not follow you to another machine. **Each tab keeps its own
session**: two tabs can sit on different volumes with different cells, and
reloading one brings back that tab, not the other. A brand-new tab starts
from whichever tab saved last, so opening a second tab gives you a copy to
diverge from; a tab that has been closed for two weeks is forgotten. Restoring is quick because
the annotations are cached server-side; a neuron that took ~14s to fetch from
Viking comes back in about 0.1s. The meshes themselves are rebuilt, which is
the part you may wait on for a large cell.

If the server is updated while you are working, a banner appears offering a
reload. Your session is saved the moment it appears, so there is nothing to
lose by taking it — and dismissing it just defers the reload. That reload
restores without asking.

A neuron that can no longer be loaded (deleted in Viking, say) is skipped and
named in the status line rather than blocking the rest of the restore.

### Scripting the view

The page exposes `window.sbfsem` in the browser console:

```js
sbfsem.sceneInfo()                        // what is visible, the bounds, the camera
sbfsem.state.neurons                      // the loaded neurons and their colors
sbfsem.renderer.resetView()               // reframe
sbfsem.renderer.snapshot('figure.png')    // save the current view
await sbfsem.api.stratification('i', 943) // any API call, as JSON
```

Useful for scripted figure passes: set a camera angle, hide everything but one
neuron, snapshot, repeat.

## GraphApp

The tab row's first tab has two buttons, **3D render** and **GraphApp** (since
2.28.0). GraphApp shows the same cells, with the same camera and zoom, as MATLAB
SBFSEM-tools' GraphApp drew a neuron: on black, with no surface, each cell's
skeleton in a near-white, a dot at every annotation and the annotations' rings
(each in its own section plane) in a pastel of the same hue. Each cell takes the
next hue of GraphApp's own palette, and the list's dot shows it; the 3D render's
colors are left alone. No cell takes a selection marker's color: the palette
(green, peach, magenta, lavender, pink, mint, violet, lime, then again) has no
blue, yellow or red, and the markers stay the yellow, red and blue they are
everywhere else (since 2.32.1; until then a blue, a yellow and a red were among its
ten, the blue as near the blue marker as two shades of one color, and a cell drawn
yellow in the 3D render turned GraphApp's yellow marker cyan). The first hue, the
one a lone cell takes, is green. The two buttons look and behave as every other tab
does: the accent outline under the pointer, only the look shown filled. The rings
are lines, as thin as anything
drawn: one pixel at any zoom, and still one from the side, where each is a short
line at its section. Switching either way takes a frame: each cell's GraphApp
drawing is made in the background once its 3D render is drawn, and the 3D render
is never rebuilt, so going back shows it exactly as it was.

The cells shown are the list's: the eyes work in both. The cell panel keeps only
**Skeleton check** and **Registration** in GraphApp; the 3D render's display
sections, the render technique, the Display panel, the context sheets, the vessels
and the Position shift arrows step aside.

The controls under the view are GraphApp's, each with its hover help and kept in
this browser:

| Control | What it does |
|---|---|
| **Rings** | each annotation's ring in its section plane, a line one pixel wide at any zoom and from any side (on by default) |
| **Show synapses** | a dot at each synapse in its type's color |
| **Show unfinished** | red: linked to exactly one other annotation and marked neither terminal nor off-edge, an end still to trace |
| **Show terminals** | blue: marked Terminal in Viking |
| **Show off edges** | violet (MATLAB's navy, lightened for black): marked Off Edge |
| **Marker size** | Minimal, Medium or Large dots (MATLAB's 2, 7 and 10 pt "." markers) |
| **Color segments** | each unbranched segment, and its dots, in a color of its own from **Colormap**: a branch reads as one color, and an unexpected change of color shows a break in the links |
| **Find ID** | a Location ID, or a synapse's structure ID, of a cell loaded in this volume; Enter flies there and rings it in yellow |

**One click selects** in GraphApp, as MATLAB's GraphApp named the node a click
landed on (since 2.29.0; the 3D render keeps its triple click). It selects what is
under the pointer and names it: an annotation (its Location ID, section and Viking
position, as in the 3D render) or a synapse (its ID, type and the cell it links
to, read from Viking once per cell). It takes the dot nearest the pointer, as
MATLAB's Data Cursor snapped to the nearest node, and asks which only when dots lie
on top of each other on screen (half a pixel apart, as down a process seen end-on);
a click on the line between dots selects the branch, and a click on nothing lets go. The selected annotation's ring **and its
dot** are drawn yellow, at full strength and then settling, and so are the red and
blue markers' (a click on another annotation of the volume, above or below the
yellow one, as a triple click does in the 3D render) and a z-slice's. The right-click menu is short:
**Isolate cN** hides every other cell, and unticking it shows them again as they
were; **Open in Viking** for an annotation; **Deselect**. The 3D render's own right
click has **Open in GraphApp**, which goes to GraphApp with that cell alone; any cell
can be shown again from the list while there, and **3D render** gives back the
visibility from before. The GraphApp button itself keeps what is shown. When the view
showed only the other cells, so that the cell alone would be off screen, Open in
GraphApp frames it as Reset view would (since 2.30.1; Hold in Display keeps the view
as it is); a view that shows any of it is left where it is.

The differences from MATLAB's GraphApp are in DEVIATIONS.md (5.16).

## Render compare

One cell, rendered several ways at once. Pick the cell (listed with its
volume, since IDs are numbered per volume) and the number of panes (1–6), set
each pane's technique, and press **Compare**. The technique list is the 3D
render's [technique picker](#where-the-controls-live): Voxel remesh, Voxel
remesh orig, Convolution surface, Ultraliser, Original (MATLAB), Adaptive +
Taubin, Wrap (cube fit), Tube, Closed curve volume, Hybrid, and Voxel remesh
put through MeshLab (isotropic, quadric or Poisson; grayed out on a server
without pymeshlab). A pane draws up to four **layers** of the cell together
([below](#layers-several-renderings-in-one-pane)); a pane with one layer is the
pane this tab has always had: one rendering, its surface, wireframe and
opacity (**α**, applied live — turn it down to see the annotations and
wireframe inside the envelope). A pane can also be a **Difference**. The
cameras are locked together — orbiting any pane orbits all of them, so the eye
compares surfaces, not viewpoints. A pane's header starts with its number badge;
that number is what a Difference pane's dropdowns and legend refer to, and
double-clicking it makes the pane fill the grid (again, to put the grid back).
The layers' chips, **+**, the options arrow, the **shift** box and **⋯** follow.
A one-layer pane is yellow with a near-black wireframe and blue annotations,
as it always was; a Difference brings its own colors. **Right-click a pane's cell
for the compared cell's menu** — *Open in Viking* at that point, the analyses, the
exports — whichever layer is under the pointer; *The right-click menu* under
3D render says what it holds.

### Layers: several renderings in one pane

A layer is one rendering of the cell: a technique with its own render options, a
color, an opacity, and switches for its surface, its wireframe and its error map.
A pane's layers are drawn together in one view, so two techniques — or one
technique tuned two ways, or a build before and after a change — can be read
against each other where they overlap. Up to four per pane.

- **Chips.** Each layer has a chip in the pane's header: a colored dot, the
  technique's short name and, in a pane with several layers, a word for its state
  (*not built*, *waiting*, *building*, *measuring*, *out of date*, *stopped*,
  *failed*). The dot is the layer's switch: click it to hide or show the layer
  (a hidden layer still builds, so it can be shown without waiting), shift-click
  to show only that layer, and again to show all of them. A hidden layer's dot is
  hollow; one that is painted with its error map shows a small ramp. Click the
  name to open the layer's **editor**. In a narrow pane the names drop away and
  a chip is its dot and a small arrow that opens the editor; the tooltips say the rest.
  The chips are one line (with one layer, beside the buttons; with several, or in a
  narrow pane, under them), and the header is the same height whatever state a layer
  is in. When the chips do not all fit, the state words shrink first, then the names
  (never below about three letters), and where even that does not fit every chip is
  its dot and a small arrow, as in a narrow pane. The pane's own state (below) is
  the first thing on that line: a word with one layer and room for it, else a dot in
  the state's color (gray, amber or red) whose tooltip says the word.
- **The + menu** adds a **Render layer** — a copy of the layer you were editing,
  in the next color and translucent, so changing one thing and pressing Compare
  is an A/B in one step — or the **Annotations** overlay: the cell's raw
  annotations (discs as rings, closed curves as their traced outlines), one
  overlay per pane however many layers it has (it replaces the old *discs*
  box). The overlay has a chip of its own: its dot hides and shows it, and the
  **+** menu takes it off again. A Difference takes neither.
- **The editor** (a popover under the chip; Esc or a click away closes it, and a click
  in its color picker is not a click away; on a short window the bottom row's editors
  and menus open above their header, or are cut short and scroll) has
  the layer's technique, **Color**, **α** with a **Ghost** button (0.15: a faint
  shape to read the other layers through), **show** (the dot's switch), **surface**, **wire**, **error**, ◀ ▶
  to move it earlier or later in the draw order, **Duplicate**, **Remove** (a
  pane keeps at least one layer), **Retry** when the layer failed, and **All
  options…**, which opens the layer's render options (below). Color, α and
  visibility are live: nothing rebuilds and nothing goes out of date.
- **Colors** come from a palette by *slot*, not by technique, so two layers of
  one technique still get two hues: yellow (the one every pane has always had),
  sky blue, pink and green. They stay apart for color-blind eyes (the least
  distance between any two, in CIEDE2000 under normal, protan, deutan and
  tritan vision, is 43.9 for two layers, 14.0 for three and 12.1 for four) and
  against the dark viewport; no fifth color cleared the others, which is why a
  pane holds four. The first layer keeps its near-black wireframe; any other
  color's wireframe is its own hue at 45 % brightness, so an edge says whose it is.
- **Overlap.** Layers are drawn in the order of their chips and keep it as you
  orbit; where two surfaces coincide the earlier one wins, and a later one shows
  only where it protrudes. The first layer is opaque and later ones start at 0.45,
  and a translucent layer does not hide what is behind it. This has an honest limit:
  blending translucent shells that cross each other is not exact from every
  angle (the hover text of **α** says so). To read such a picture, reorder the layers,
  use Ghost, hide one, or cut the pane with a Z range.
- **The view cube** (see *View cube* under the 3D render) sits in the grid's
  top-right corner, in whichever pane is there: the last of the top row, or the
  pane that fills the grid. It shows the focused pane's camera and turns every
  pane: with **Sync cameras** the focused pane turns and the rest follow it
  frame by frame; without it every pane turns to the same direction about its
  own pivot, keeping its own zoom, and a drag on the cube orbits each by the
  same amount. Its house resets the focused pane (with Sync, every pane
  follows) or, without Sync, each pane. The **View cube** box in the 3D
  render's toolbar shows or hides it here too.
- **The pivot point** (see [Where a turn pivots](#where-a-turn-pivots-and-choosing-the-point)
  under the 3D render) works in every pane: point at the cell in a pane and press
  **`p`**, or right-click it → **Rotate about this point (P)**. It goes to the middle of
  the annotation there, read from the copy of the cell each layer was built from, so a
  process turns about its own axis. With **Sync cameras** the panes share one camera, so
  they share one pivot: set in any pane, every pane turns about the same point and
  shows a ring on it; let go in one, every pane lets go. Without Sync each pane keeps
  its own, and `p` over a pane sets or lets go of that pane's alone; turning Sync on
  gives every pane the first drawn pane's pivot, or none. Drags in any pane, the cube's
  turns and drags, and XY1 keep the point at its pixel in each pane (the browser check
  measures 0.00 px of movement in each, Sync on and off). The label **Rotating about a point** sits under
  the view cube (in that pane's corner with the cube hidden); without Sync, when only
  some panes have one, it names them. A click on it, or `Esc`, lets go of every pane's.
  It also turns off with the pane's **Reset view** or the cube's house, and by itself as
  in the 3D render. Hiding a layer keeps it: the point is on the cell, which the other
  layers still draw.
- **⋯** is the pane's tool menu: **Reset view** (with Sync cameras every pane
  follows), **Save picture** (a PNG of the pane), **Maximize** / **Restore** (the
  other panes keep their layers and wait, with their rendering paused) and a
  **Z µm** range — two boxes, either may be blank — that cuts every layer and
  the annotations of the pane to the section of the cell between those two Z levels,
  in the volume's own microns. The boxes' hints show the extent of what is drawn.
- **The pane count** is 1 to 6. Panes are added and taken away at the end, so
  the panes you keep keep their layers, their settings and their surfaces
  (a taken-away pane stops its work; the others carry on).
- **The toolbar's Error toggle** ticks the error box of every solid layer in every
  pane, and clears them again; it is half-ticked when only some have it. Compare
  measures the layers that need it.

Compare builds every layer that is not current. **Up to three panes work at once;
the layers of a pane are built one after another**, in the order of their chips,
and a Difference waits for the surfaces it measures. The browser keeps the last
twelve meshes it downloaded, so a technique used by three layers (or by two panes
at the same moment) is built and downloaded once, and going back to a technique you
had a moment ago is instant. Another load of the cell, or another cell, starts
fresh.

### A layer's render options

Every technique layer carries the **whole set of render options** the 3D
render has — the same controls, with the same labels, help, ranges and
defaults, because both are built from one list. The arrow (▲) in a pane's
header (or **All options…** in a layer's editor) opens them in a panel that rises
from the toolbar over the panes (nothing is resized behind it), for the **layer
being edited** in the **focused pane**: the pane whose header you last clicked,
marked by an accent on its number and under its header, and within it the layer
whose chip you last opened (the first, until you open another). The panel's
title names it ("Pane 3 — Wrap (cube fit)", or "Pane 3 · layer 2 — Wrap (cube
fit)" in a pane with several layers). Click another pane's header, or its own
arrow, or another layer's chip, and the panel follows it; the pane's arrow
again, the ✕, Esc inside the panel or a click on a Difference pane's header
closes it, and it stays closed until an arrow opens it (a pane that becomes a
Difference, or disappears when you lower the pane count, closes it the same way).
A layer has its own options and never changes the 3D render's, the cell's, or
another layer's.

From the keyboard, Enter or Space on a pane's arrow opens the panel and moves
focus to its first control; Tab walks the controls, and Esc or the ✕ closes the
panel and puts focus back on that arrow. Tabbing through the panes' headers does
not move the panel: only a click, picking a technique in a header, or an arrow
does.

- **The controls shown are the ones the pane's technique uses**, exactly as in
  the 3D render: Pathway's own knobs appear with it, Voxel size with Closed
  curve and Hybrid (and Smooth discs and Smooth outlines with Closed curve, Smooth outlines alone
  with Hybrid), Passes and Decimate are grayed (with the reason in their
  help) where the method ignores them. Everything the 3D render's **More ▾**
  has about the surface — MeshLab and its knob, Order, Wrap's Reject σ and
  Refits — is in the panel's **More ▾**. **Clip IPL** is not: it cuts the mesh
  after it is built and is applied only by the 3D render, so a pane never has
  one (and the 3D render's own clip is never carried into a pane).
- **Typing never rebuilds anything.** The layer's chip says *out of date*, the
  status line says to press Compare, and Compare rebuilds only the layers that
  are. What you type is kept inside the range the server accepts and shown back
  as it will be built when you leave the box (a blank box is the range's
  minimum, as in the 3D render).
- **The technique name follows the options.** Picking a technique fills the
  layer with that technique at *your saved technique defaults for it* (the ones
  *Save technique defaults* keeps in the 3D render), then you tune it. Changing
  the Method, Pathway or MeshLab mode in the panel renames the layer to the
  named technique that now fits, or to *Custom…* when none does (Original with
  MeshLab on still reads Original, as in the 3D render). Picking *Custom…* from
  the list opens the panel on that layer.
- **The cost chip** beside the controls is the build-time estimate the 3D
  render shows, for this layer's technique on the cell the picker names.
- **Closed curve and Hybrid** work here, so a cell traced with closed curves
  (a blood vessel, a mixed cell) can be compared like any other. The default
  0.05 µm raster is too fine for a whole traced cell, so a pane whose Voxel size
  you have not set is given the one the 3D render would use for the cell
  (0.25 µm, 1 µm for vessels) when you press Compare, and the box shows it; the
  next cell gets its own, and a cell with no outlines the technique's usual one.
  A value you picked in the panel (0.05 µm too), one *Use 3D render settings*
  or *Use this cell's settings* brought, or one saved as a technique default, is
  left alone. *Reset to technique defaults* and picking the technique again hand
  the choice back to the cell. The cost chip prices a pane at the raster it will
  build at.
- **Settings that will not build as set are marked here too** (since 2.19.1), as
  in the 3D render (*Settings that will not build as set*): a layer's Closed curve
  or Hybrid Voxel size past a limit for the cell the picker names, or its typed
  Fine voxel the Face cap holds coarser, turns those controls black with red text,
  and a row at the top of the panel says why and lists the fixes, best first. A
  fix goes into that layer alone, as if typed: the other layers and panes and the
  3D render panel keep their settings, and nothing is built until Compare.
- **Panes keep everything** — layers, settings, surfaces — when you change the
  number of panes (panes come and go at the end), and the arrows' panel follows
  its pane. None of it is saved with the session: a reload starts from the
  techniques' defaults again.

The panel's **More ▾** ends with six bridges, each with a hover explanation:

| Bridge | What it does |
|---|---|
| **Use 3D render settings** | The layer takes what the 3D render panel shows now. The panel is not touched. |
| **Use this cell's settings** | The layer takes the render the cell was last built with in the 3D render tab. |
| **Send to 3D render** | Puts the layer's geometry options into the 3D render panel's controls (Clip IPL stays). Nothing is applied: press *Apply to selected* there. |
| **Copy tuning to other layers** | Copies this layer's tuning — every knob but the technique, the MeshLab mode and Clip IPL — to each other technique layer (in this pane and the others), only the knobs *both* layers' techniques use (a knob either one hides or grays out is left as it is, so a Wrap layer's Cube size survives a copy from a Voxel remesh layer). The layers go out of date; nothing rebuilds. |
| **Reset to technique defaults** | Puts the layer's knobs back to your saved defaults for its technique, or the recommended values; the technique stays. |
| **Save as my technique defaults** | Keeps the layer's tuning as your defaults for its technique, in this browser — the same store the 3D render's picker and Defaults buttons read. |

A Difference's two sides are technique panes (it measures the first layer of
each), so they carry exactly these options (and the Difference keeps working as
before). A Difference is a pane of its own: it takes no layers, and a pane that
has several cannot be set to Difference until you remove the others.

Every pane, a Difference pane too, has a **shift** checkbox in its header: it draws the
cell's [Position shift](#position-shift-where-each-annotation-was-and-where-it-is-drawn)
in that pane, independently of the surface. The settings of the layer are one
row below the panes, shown while some pane has its box ticked and shared by all
of them: the pair, **Only above**, the exaggeration (**Fit** is Auto), the
arrows' **Width**, **One slice ±N** with a stepper, the color and **Full color
at**, with a chip that says how many arrows are drawn (the facts in its hover).
Each pane that draws the layer has its own legend, top right (bottom right in the
pane that holds the view cube, above a Difference pane's scale). They start from the
compared cell's own settings and stay in Compare (changing them here does not
change the cell). Every ticked pane draws the same exaggeration, the same
scale and the same slice, and `]`, `[`, `PageUp`, `PageDown`, `Home`, `End` and
the arrow keys on any pane step the slice in all of them. Compare again after
changing the cell's Registration in the main tab: the arrows follow the new
copy of the cell.

### Registrations: one cell under several

Where Viking draws an annotation depends on its **registration**: at its stored
position, or through the volume's current section transforms, and then moved,
or not, by Viking's section correction (Residual field, Curve fit, or Field +
fit; see *Registration* in the cell panel). Render compare can show the same
rendering of the cell under several registrations side by side, to see what
each does to the surface.

- **A layer's Registration** is in its editor, under Color: **As loaded** (the
  cell as the 3D render has it, your own moves included; the default) or any of
  the eight registrations (four where the volume's transforms are not read
  here). Any other than As loaded is that registration's own copy of the cell,
  without moves, which the server loads the first time a layer asks for it and
  keeps beside the page's own. Changing it marks the layer out of date; Compare
  builds it. The chip says it first: `current+field+fit · Voxel remesh`, so the
  panes stay told apart when the chips are cut short.
- **Registrations…** in the toolbar sets several up at once. It copies the layer
  being edited in the focused pane (its technique and every option) once per
  registration you tick, in a grid of the corrections (none, Residual field,
  Curve fit, Field + fit) by the positions (stored, current transforms), and
  puts them **one pane each** (up to six; the pane count follows) or as the
  **layers of that pane** (up to four, drawn over each other in the palette's
  colors). It starts with six ticked: the stored positions, the current
  transforms, the transforms with each correction, and the volume's default
  correction on the stored positions. **Set up and compare** builds them at once.
  The choice is remembered in this browser.
- **Each layer is measured against its own registration**: its error map holds
  it to its own copy's annotations, a pane's annotations overlay and Position
  shift arrows are its first layer's copy, and a Difference between two panes
  on different registrations holds each surface to its own. A copy whose
  correction the service did not apply (the service down) is drawn without it,
  and the status line and the layer's chip say so.

On Inferior c1178 the six registrations of the Original built in 13 s together
on a dev server, and loading a copy takes about as long as loading the cell.

### Progress, Stop, and what a failure looks like

Builds, error maps and Differences in this tab are background jobs on the
server, not one long web request, so none of them is cut off by the gateway's
five-minute limit as a bare "504" (they can still fail for their own reasons,
and the pane says which). Each pane shows its own
job on a **progress card** over the render — in a pane with several layers the
card names the one that is working ("Layer 2 · Original (MATLAB): Building
Original (MATLAB)") — what is happening ("Building Voxel
remesh + MeshLab isotropic", "Measuring against the annotations"), the
server's stage with a count when it has one ("contouring 412 of 1,830"), a
bar that fills only when the server counts (a MeshLab remesh does not, so the
bar just moves and the clock is the signal), the seconds elapsed on your own
clock so it keeps moving between updates, and a **Stop** button for that pane
(it stops all of the pane's layers, the ones still waiting too; a mesh another
pane is also waiting for keeps downloading for it).
A job still waiting for a worker or for server memory says so. A finished
surface on its way down says how much has come ("downloading the surface, 23
of 154 MB", the bar filling with it). If the server
stops answering, the card says "no answer for N s, retrying" and keeps trying
for a minute, then reports that contact was lost (the job may still finish on
the server; Retry looks again); a surface whose download kept breaking off is
reported as built and not downloaded, with its size. A restart that loses the job is noticed and the
job is started again once, if the cell is back: a mesh comes back from the
server's disk cache, but an error map or a Difference starts over from zero
(minutes, on a big cell).

The toolbar says where everything stands in one line — "3 of 6 panes ready,
pane 4 measuring, 41 s" (a layer of a pane with several is named: "pane 2
layer 3 building") — and **Compare** turns into **Stop all** while any
layer has work: every wait is abandoned and every job still queued on the
server is canceled. A job already running on the server cannot be
interrupted there; it finishes and its result is kept for a while, so pressing
Compare again soon after picks it up instead of repeating the work. Compare builds up to three panes at a time (a pane's layers one after another)
and leaves alone every layer that is already built with its current settings, so
after changing one knob it rebuilds only that layer (and any Difference that uses
it).

That line and the status line after it are one line high however much they say, so
a Compare starting, ending or failing never resizes the panes (a wrapped line used
to take 29 px from the grid and rescale every render in it). On a window narrower
than about 1650 px they have a row of their own under the controls, which stays,
empty, when there is nothing to say; on a wider one they sit beside the controls.
What does not fit is cut with an ellipsis and is in full when you rest the pointer
on it; the summary keeps its words (the clock is at their end) and the status line
gives way first.

A layer is always in one of these states, a pane is as far from ready as its
worst layer, and the pane's header chip says the ones that need a word (in a pane
with several layers each layer's chip carries its own word too; where the chip has no
room for the word it is a dot in the state's color, gray, amber or red, and its tooltip
says the word):

- **waiting** — queued behind the layers before it in its pane, or behind the
  three panes that are working.
- **out of date** — you changed its technique or knob (or the cell, or the
  cell was loaded again) after it was built; a layer you just added counts too,
  until Compare builds it. The surface shown is the earlier
  build; the chip's tooltip says so. Press Compare to rebuild it. Ticking
  **error** on an out-of-date pane rebuilds it first with its new settings (the
  card says "Settings changed, rebuilding this pane"), then measures — you are
  never shown an error map of settings other than the ones on screen. A map is
  always measured on the surface the pane holds, even if you change a knob
  while it is being measured.
- **stopped** — you stopped it before it finished; what it showed before is
  still shown.
- **failed** — a **red banner** across the top of the pane gives the title,
  the server's message and the stage that was running ("Failed during
  MeshLab remesh"), with **Retry** and **Dismiss**. It stays until
  dismissed, in both themes. In a pane with several layers the failed layer's
  chip also turns red, with the message in its tooltip, and stays red when the
  next layer's progress card takes the banner's place; the banner comes back
  when the pane's work ends (the first failed layer's, and **Dismiss** shows the
  next failed layer's, so the status line never points at a banner that is not
  there). **Retry** in the layer's editor (or Compare) tries it again, and
  Dismiss clears it. A pane whose *build* failed shows no surface —
  never the previous build's, and none of the annotation rings of an earlier load of
  the cell or of another cell (the rings of this load, still true, stay). A pane
  whose error *measurement* failed keeps its
  surface; only the map is missing, the error box is cleared to match, and
  Retry measures again. A *Difference* that fails has no surface of its own
  and shows no map.

Unticking **error** while a map is being measured cancels the measurement.
The status line next to the toolbar never reports a success over a failure:
if any pane failed it says how many, in a red band.

### Error maps: where a render and its annotations disagree

Every solid layer has an **error** box in its editor (and the toolbar's **Error**
toggle ticks them all). It draws the two distances
the *vs annotations* numbers are averages of, so you can see where a score
comes from instead of only comparing scores:

- **The surface is colored by each vertex's distance to the nearest
  annotation** — a disc in its section plane, or a traced outline. This is
  the *surface→annotation* half: surface that no annotation supports (a cap
  past the last annotated section, a tube running between sparse
  annotations, a sweep through unannotated tissue) lights up.
- **Points along every annotation's rim are colored by their distance to the
  surface**, drawn through the surface. This is the *annotation→surface*
  half: annotations the render failed to reach (a dropped disc, a body drawn
  too thin, a branch the render cut) light up.

Light gray agrees, amber is half the scale, red is the scale and beyond. **All
error layers share one scale**, so the same color means the same microns in
every layer: by default the largest 98th percentile among the measured layers;
type a value into the box at the right end of the color bar to pin it (every
layer follows), clear it to go back to automatic. The bar is shown once for a
pane; under it each layer's own numbers (with a stripe in the layer's color
beside them when the pane has several layers): surface→annotation, annotation→surface, their pooled
mean, the 95th percentile and the maximum. Turn **α** down to see rim points
inside the body; the **Annotations** overlay adds the annotations themselves.

The map is measured on the server the first time a pane's checkbox is
ticked (or on Compare, for panes that have it ticked) and kept until the
pane is rebuilt; changing a pane's settings dims its legend and marks the pane
out of date until the next Compare. The measurement result carries distances
only, not geometry: it is matched to the pane's own surface by the surface's
id before it is painted, and if the server's copy differs the surface is
fetched again once. Hover the legend's numbers for the explanation; the scale
box is reachable with the mouse. A cell traced as thousands of closed curves ships every k-th rim
point to the browser (at most 50,000; the legend's tooltip says which k) while
the numbers use all of them. RPC1 c2628, 3,978 outlines: 18 s.

### Difference panes

Set a pane's technique to **Difference** and choose which two panes to
measure: it renders pane A's mesh colored by the signed distance to pane B's
surface — red where A is outside B, blue where it is inside, white where they
agree. The color scale defaults to the 98th percentile of |d| and can be
pinned with the **±µm** box; the map colors the surface and the wireframe
alike, so a wireframe-only difference view works too.

A Difference is measured between the surfaces panes A and B hold. Each
dropdown leaves out the other's choice, so a pane is not measured against
itself; if the two ever resolve to the same pane, another technique pane
stands in for B. It refuses (the banner says why) when the layout has fewer
than two technique panes or when a source pane has no surface because it failed
or was never built.

The measurement is server-side and honest about its resolution: B's surface
is sampled area-weighted (deterministically, so a diff is reproducible) and
every vertex of A is measured against the samples through a k-d tree. The
legend reports mean |d|, RMS, 95th percentile and max in microns, plus the
sample spacing (~0.05 µm on a typical cell) — differences below that spacing
are sampling noise, not geometry. The implementation is pinned against
concentric analytic spheres in the test suite.

A second legend line holds each source rendering to the cell's **own
annotations** — the discs (and any closed-curve outlines), the closest thing
to ground truth a render can be measured against: `vs annotations: pane 1
0.25 · pane 2 0.87 µm mean |d|` — those two numbers, plus the first line's
mean |d| between the two surfaces, are the pane's three quantitative errors.
The legend always names the panes it actually measured; changing any setting
(the source dropdowns included) dims it and stamps it "Out of date — press
Compare to re-measure" until the next build. The deviation is measured in both
directions, because each direction sees a different failure: points sampled
on the rendered surface are measured to the nearest annotation (surface with
no annotated support — a soma cap ballooning past the last annotated
section, a sweep through unannotated tissue — scores here), and points on
every annotation's rim are measured to the rendered surface (annotations the
render failed to reach score here). All distances are absolute, so an
annotation poking outside the surface counts the same as surface ballooning
past an annotation — errors never cancel. Hover the line for the direction
split, the 95th percentile and max, and the **unsupported volume**: the mean
surface deviation times the surface area, a first-order estimate in µm³ of
the volume the render claims beyond any annotation's support. Closed-curve
outlines are shifted from their mosaic frame into the registered volume
frame by their own annotation's offset before measuring.

### The comparison report (PDF)

**Report…**, in Render compare's toolbar, makes a color PDF of what the panes
hold. Every *rendering* is one layer of a pane as it was built (its technique
with all of its settings); you choose:

- **Renderings**: every built layer is listed with what sets it apart from the
  others of its technique ("Face cap 100,000") and its triangles. A layer that
  is not built yet cannot be chosen (press Compare first); a layer whose panel
  was changed after it was built is marked, and measured as it was built.
  **vs annotations** gives a rendering its page against the cell's annotations.
- **Comparisons**: a box for every pair of the chosen renderings. Each pair is
  measured *both ways* (A against B and B against A), whether or not a
  Difference pane shows it. The quick picks: **All**, **None**, **Same
  technique** (one technique under different settings: what each setting
  does), **Across techniques**, **Panes** (the first layer of every pane against
  the others), **Within panes**, **Across registrations** (one technique with the
  same settings on two registrations: what each registration does to the
  surface) and **Differences on screen** (the pairs the Difference panes show). Up to 15 pairs every pair starts ticked; beyond, the
  panes, the layers within each pane and the Differences on screen.
- **Viewpoints**: **This view** (the panes' camera now; under Flip Z, mirrored
  as on screen), **XY1**, **XZ1** and **YZ1**, the viewer's named views. Every
  picture from one viewpoint is in the same frame on every page.
- **Sign**: **Exact (enclosure)**, what Difference panes use, or **Faster
  (normal)**, about four times faster and wrong where the nearest sample is on
  a piece buried in the other surface.
- **Include**: a picture of each pane as it is on screen, and the addendum.
  **Paper**: Letter or A4.

The dialog says how many pages and about how long; **Create PDF** makes it on
the server and saves it (`sbfsem-compare-c943-NeitzInferiorMonkey-2026-10-20.pdf`).
**Stop** ends it (the server stops at its next step). Closing the dialog does
not: the status line follows the report, and it is saved when it is done. The
server keeps the file for ten minutes (**Save again**). On Inferior c1178,
three renderings of 400,000, 194,000 and 77,000 triangles, three pairs and
three viewpoints took 79 s and made 34 pages (1.05 MB); a measurement a pane
already made costs nothing.

What is in it, in order (the PDF's bookmarks follow it, and every page number
in the contents, the tables and the matrices is a link):

1. **The cover**: the cell, its volume and copy (registration setting) or, for
   renderings of several registrations, each of them; when; the renderings, and
   the panes as they were on screen. Each rendering is held to its own
   registration's annotations, and a pair across two registrations holds each
   surface to its own.
2. **The summary**: every rendering's size and shape (triangles, area, volume,
   visual bodies) and its error against the annotations; with two or more
   registrations, a page comparing them (what each moved of the cell's own
   annotations, how far each link steps sideways between its sections at its
   positions, the sections that moved as one, the curve fit's pulls toward a
   soma, and what each does to what a section image shows: how much it changed
   the offset between two of the cell's annotations drawn on one section within
   5 µm, and between each synapse and the cell; with what those numbers can and
   cannot say); four **matrices** of
   the error between every pair (the symmetric mean distance, the Hausdorff
   distance, and, row against column, the mean signed distance and the 95th
   percentile); every pair **ranked**, most different first; charts of the
   error against the annotations and of volume, area, triangles and creases.
3. **One section per pane**: the pane as it was on screen and its layers; for
   each rendering a page of its **settings** (every option its technique uses;
   the ones changed from the technique's defaults in blue, the ones it ignores
   in gray) and its **shape**, and a page **against the annotations** (pictures
   colored by the distance to the nearest annotation on one scale for the whole
   report, the distribution, the error along Z with the section numbers, the
   rims inside and outside the render, and the worst places with their Location
   IDs and sections); then three pages for each **pair** it leads: pictures of
   both surfaces colored by their signed distance to each other and of where
   their outlines differ; every number both ways (mean, RMS, signed mean,
   percentiles, the share within 0.05 to 2 µm, the volume between them from
   the distances beside the volumes themselves) with their distributions and
   profiles along Z; and **where they differ**: the section cut where they
   disagree most with both surfaces' outlines over that section's annotations,
   close-ups of the worst places, and tables of the places by X, Y, Z and
   section.
4. **The addendum**: nine pages, each a definition with the formula the code
   computes and a figure of what it measures (distance to a surface and its
   sampling, the sign, weighting by area, the summary numbers, the volume from
   distances with an example computed as the report is made, the distance to a
   disc in its section plane, the pictures, and the limits).

Two first layers of different panes are both yellow on screen; where the
layers' colors do not tell every rendering apart, the report gives each its
own color and shows the screen's beside it.

## Network

One network per volume: cells in different volumes cannot be connected, and an
ID names a different cell in each. Pick the **Volume** (the list holds every
volume with a loaded cell) and click **Build network** to resolve the synaptic
partners of the cells loaded in it and draw the graph. The loaded cells are
outlined; their partners are not. Edges are weighted by the number of synapses
between the pair and colored by the type with the most of them; arrows point
from the presynaptic to the postsynaptic cell.

**Names** writes the cell ID, its Viking label, or both beside each node. The
parts of the graph that are not data — the label text and its halo, the ring on
a loaded cell, the selected edge — are the page's own ink and panel colors, so
they read on both themes and follow the system's light or dark setting while the
graph is open.

The **legend** under the graph lists every link type the build found, with its
color and its synapse count, so the colors are never a guess. Types are named
from the loaded cell's side: `ConvPost` is a conventional synapse onto it,
`RibbonPre` a ribbon synapse it makes (the Connectivity tab, which walks
several cells deep, names the presynaptic side instead). A link whose
Viking type has no pre and post side — a gap junction, an adherens junction
(`Desmosome`), a touch, or `Unknown` — is drawn dashed without an arrowhead,
because Viking's source and target for it are simply whichever end was
annotated first. Untick a type to leave it out; a cell's synaptic network
without its adherens junctions is one click. Weights, colors and arrows are
recomputed from the types that remain, and a partner left without a connection
disappears.

**Min weight** hides edges below a synapse count, which is how you find the real
structure in a dense graph. **Layout** switches between force-directed
(`cose`), concentric by degree, layered (`breadthfirst`) and simple geometric
layouts. Names, weight and hidden types are drawn from the answer already
fetched; only Build network asks Viking.

Hovering a node shows its ID, label and number of connections; hovering an edge
lists its synapses by type. Clicking a node puts its ID, with its volume, in
the Structure ID box, so you can walk outward from a neuron one partner at a
time.

## Connectivity

The Connectivity tab answers the question the Network tab's graph cannot once
it has more than a few dozen nodes: *which* cells connect to *which*, over
*how many* paths, and *through whom*. It implements the two overviews of
Kerzner et al., *Graffinity: Visualizing Connectivity in Large Graphs*
(EuroVis 2017), which the Marc lab used on RC1, over the synapse links in
Viking — for any volume this tool can open, fetched as the query needs them.

**The query.** Pick a volume and a **direction**. *Downstream* is
Graffinity's search: the loaded cells are the starts and paths follow synapses
out of them. *Upstream* anchors the loaded cells as the *ends* and walks
synapses backward — the query a ganglion cell needs, since every one of its
synapses is an input. **Path length** is 1 to 4 hops; **Include shorter
paths** also keeps paths that reach a matching end in fewer hops. The
**anchors** are the loaded cells in the volume, or every cell whose label
matches a pattern (`CBb.*`, `dM1.*`).

The row beneath is the path template: one box per node position and one per
hop. A node box takes a label pattern and a hop box a synapse-type pattern,
both whole-string, case-insensitive regular expressions — `DB4.*`,
`ConvPre|RibbonPre`, `.*parasol.*` — and an empty box means anything. Synapse
types use this tool's names for the presynaptic side (`ConvPre`,
`RibbonPre`, `BipConvPre`, `GABAPre`) and the undirected kinds
(`GapJunction`, `Desmosome`, `Touch`, `Unknown`); the box suggests them.
Paths never visit a neuron twice unless asked (see *Allow repeats*
below); Graffinity's own search allowed it, and a gap junction walked out
and back is not a two-hop connection.

Each hop costs two batched requests to Viking (the links of the frontier's
neurons, then the labels of the neurons they reach), whatever the frontier's
size, and what was fetched stays in the server's memory for the next query.
Measured on 2026-09-11 for Inferior Monkey c1178 (233 links): a two-hop
upstream query fetched 158 neurons and answered in 1.7 s; the same query
again, 0.1 s. **Limits** caps the paths kept and the neurons expanded per
hop (300 by default); when a cap bites the status line says so and what to
loosen.

**The connectivity matrix.** One row per start cell, one column per end
cell, and in each cell a **metric** of the paths connecting the pair: their
count, the shortest one's length, or the number of synapses on the distinct
connections they use (a pair joined by three ribbon synapses scores 3 on a
one-hop query). Colors are stepped, as Graffinity's were: **Bins** sets how
many steps run from 1 to the top of the scale, **Max** sets that top (blank
follows the data; a lower value puts every larger cell in the darkest step
and the legend says `112+`), and **Scale** makes the steps equal in size or
equal in ratio. The legend under each table shows the steps and their
boundaries, each label with the fewest decimals that tells it from the next (a
scale up to 3 in six steps reads 1, 1.3, 1.7, 2, 2.3, 2.7, 3; one that tops out
at 1 is one step). **Group rows** or **columns by label** turns every `DB4 BC`
into one row (the union of its cells' paths, as in the paper's
aggregation); the ▸ beside a group opens it to show its members beneath.
**Order** keeps the query's order, sorts by label, or by row and column
totals.

**The intermediate node table.** One row per neuron the paths pass through,
one column per (path length, position) pair, and a count in each: the hubs
of the query. The paper's example finding — a YAC amacrine cell carrying a
surprising number of the CBb-to-rod-bipolar paths — is a row near the top of
this table.

Hovering a cell in either view outlines the cells of the other that share
its paths. Clicking pins that outline — it stays through hovering, metric
changes and regrouping, for a screenshot — and lists the paths below; a
second click on the same cell, **Clear selection** or Escape unpins.

**The path list** is laid out as Graffinity's: one row per sequence of
cells, with **Hops** and **Freq** (how many distinct synapse-type
combinations join those cells in that order), the most frequent first. The
▸ opens the row to the combinations, one per line under `Edge 1 … Edge n`,
and each of those opens to the structure IDs of every synapse behind every
hop, so any link can be checked in Viking. **List: Every path** shows the
flat form instead (`c86145 —RibbonPre→ c5560 dAC? —ConvPre ×13→ c1178`).
Every cell ID is a button that puts it, with its volume, in the Structure
ID box. **Matrix .csv** downloads the matrix as drawn, each row and column
named as on screen (`c1178 GC ON`; `DB4 BC (12)` for a group) and a 0 where a
pair has no path; **Paths .csv** the listed paths, one per row, with their
structure IDs. A query may return up to 200,000 paths; nothing in the tab
breaks on a list that long.

Paths never visit a cell twice unless **Allow repeats** (under Limits) is
ticked, which restores Graffinity's own behavior — the way a cell linked to
itself, usually an annotation slip, shows up as a path.

## Stratification

IPL depth histograms for every visible neuron, overlaid. 0 is the INL/IPL border
and 1 the IPL/GCL border; the shaded margins beyond them hold annotations outside
the IPL.

**How depth is measured** (since 2.31.0). Each annotation's depth is measured
along the layers' normals: from the nearest point of the fitted INL surface and
of the fitted GCL surface, so a depth is a distance from the INL and the IPL's
thickness is measured square to the layers wherever they tilt. Before 2.31.0 it
was measured straight along Z, as MATLAB does: between the two surfaces' heights
above and below the annotation. No volume's layers are flat or parallel, and a
tilted IPL reads 1/cos(tilt) too thick along Z (on the five volumes with markers,
0.1 to 2.4 % at the median and up to 15 % in places); the depth as a percent of
the IPL moves by about 1 % or less for 95 % of points, and a cell's median by
0.2 % or less on the cells measured. *IPL depth measured* in Settings switches
back to the column, for comparing with a figure made before; the line under the
chart, the PDF and the workbook say which made their numbers. Under the
statistics, the tab gives each shown volume's IPL thickness along the normals
(the median, the range of 90 % of it and where it is thinnest) and how much
thicker it reads along Z (`GET /api/volumes/{source}/ipl-thickness` has the
whole map). An annotation whose nearest point on a layer lies past the edge of
that layer's fit has no depth along the normal and counts as outside the
surfaces.

**Omit outliers** drops annotations more than 20% of IPL thickness outside the
layer. Turn it off to see where a neuron actually sits — a cell whose entire
profile is off-scale is usually outside the region where the boundary markers
were traced, not mis-analyzed. The stat tiles report how many annotations were
dropped as outliers and how many fell outside the fitted surfaces entirely (whole
numbers, not measurements). The tab draws when you open it if the cells shown, or
their data, are not the ones it was last drawn for, so right-clicking a cell and
choosing **Analysis > Stratification** shows a chart that has that cell in it, even
when it is hidden or was loaded after the chart; **Compute** draws it again on demand.

**Ticks every** sets where the depth axis' gridlines and labels go: from the INL (0 %)
to the GCL (100 %) every so many percent, 20 by default since 2.27.0 (it was 25). Any
step from 1 to 100 is taken; one that does not divide 100 ends with a shorter interval
(30 gives INL, 30, 60, 90, GCL), and when lines crowd, only every second or third is
labeled. It redraws the chart at once, without computing it again, and is kept in this
browser.

Two buttons save what the chart shows, made on the server from the same cells and
settings:

- **PDF…** asks whether to add the data on the pages after the chart: the statistics
  and each cell's binned counts (one or two pages), and every annotation's depth, cell
  by cell (it says about how many pages; past 20,000 annotations it points to the
  workbook instead). The first page is the chart itself, drawn as vectors with its text
  kept as text, on a page the size of the figure (8 × 4.6 in) so it can go straight into
  a figure, with one line under it: the bins, the soma, the outliers, the gridlines, how
  depth was measured, the version and when. The last choice is kept in this browser.
- **Data .xlsx** saves an Excel workbook. Its **Summary** sheet has a column pair per
  cell, each bin's center (percent of the IPL) and count, matched by bin number (each
  cell's bins span its own depths), then the statistics in percent (n, median, mean,
  SEM, the mode's bin center, outliers, outside the surfaces, soma left out), then the
  settings. A sheet per cell lists every annotation: Location ID, section, X, Y and Z
  (µm), radius, IPL depth (%), its depth from the INL border and the IPL's thickness
  through it (µm, measured the same way as the percent), and whether the chart counts
  it or why not (soma, outside surfaces, outlier).

## Saving a workspace on the server

**Save…** writes the current workspace — which cells are loaded, their colors,
every per-cell render and display setting (surface, wireframe, disc
annotations, skeleton, per-type synapse settings) and the toolbar controls —
into a named folder on the server. **Capture current viewport** (on by
default) also stores the camera position and zoom; opening such a document
returns the view to exactly that viewpoint, while one saved without it opens
framing the cells. Every save asks for a folder and name — clicking an
existing document fills in its name and the button becomes **Overwrite**.

**Open…** browses folders and documents — sizes, dates, per-document delete —
and selecting a document shows what is inside it (cells, volume, save time,
whether a viewport was captured) before you commit. Opening replaces whatever
is on screen.

The annotations themselves are *not* copied. They are already on the server's
cache, and a workspace records the volume and id, so a saved file is a couple
of kilobytes rather than megabytes.

Folders and document names accept letters, digits, spaces, dots, dashes and
underscores, must start with a letter or digit, and are at most 63 characters.
Everything is confined to one directory (`/srv/sbfsem/saves`), and paths are
re-checked after resolution rather than trusting the name check alone.

**Saved workspaces are shared, not per person.** Only signed-in people reach
the site, but every one of them can read, write and delete every saved
workspace. The quotas — 100 folders, 200 documents each, 5 MB per document,
200 MB in total — bound what the site can hold.

**Enter saves and Enter opens, once.** In the Save dialog's name and folder boxes
Enter presses **Save** (or **Create**, in the New folder box); a held key or a
second press while the first is still being written does nothing, so the document
is written once. The same goes for **Open…**. Each write goes to a temporary file of
its own beside the document and replaces it, so two saves of one name at the same
moment (two browser tabs) cannot fail each other: the later one wins.

## Annotations

The full annotation table for one neuron: location ID, parent, section, micron
coordinates, radius and the OffEdge / Terminal / Unique flags. "Cell body only"
excludes child-structure annotations. The filter box matches location IDs.
The x, y and z are where the cell is drawn. When Viking's current section
transforms or its section correction moved any annotation, three more columns
give the position Viking stored for each one (`x stored`, `y stored`) and how far
it was moved (`moved um`), so the table shows both ends of the move. The table
follows the cell it names and the setting it is drawn with: choosing
*Annotations table* from a cell's right-click menu shows that cell's, and
changing its Registration redraws it.

### Jumping to an annotation

Right-click any row for **Show location on render**: it switches to the 3D tab,
makes the cell visible and meshed if it is not already, and flies the camera to
that annotation, which ends up at the center of the view. The flight eases in and out
over about 1.5s rather than cutting, and picks its approach direction by casting
against the loaded meshes, so it does not end up inside the soma or behind a
dendrite. Press `m` to reset the view afterwards.

## Analyses

Per-neuron summary, morphometrics, a dendrite-diameter profile (mean diameter in
µm, each bar at its distance from the soma, binned 5 µm wide), export links, the linked-neurons report and, with more
than one cell loaded, a table of every loaded cell for comparison across
volumes. The pane scrolls when the cards run past the bottom.

The summary card lists the cell's synapse structures by type. The type is
this tool's name (ConvPost, RibbonPre, GapJunction…) with the volume's own
name in parentheses where the two differ; a Viking type this tool has no
name for (PeriGJAdherens, Cistern…) is listed under the volume's name, and
is grouped with *Unknown* on the render, where the marker set is per local
type.

### Morphology

Every number on the Morphology card has its definition on hover, and **How
these are computed** under the card spells them all out. In short:

| Metric | Definition |
|---|---|
| Dendritic field area, equivalent diameter, perimeter | Convex hull of the cell-body annotations in the XY plane; the diameter is that of the circle of equal area |
| Surface area | Each cell-body annotation contributes `2·π·r·Δz`, one section of a disc's side (the MATLAB `surfaceArea`; not the mesh's area) |
| Total cable length | Straight-line distances between every pair of linked cell-body annotations, summed in 3-D: the traced centerline, exactly the skeleton the SWC export writes |
| Dendritic length | Total cable length minus links between two soma annotations (radius ≥ 80 % of the largest) |
| Dendritic length, in plane | The same measured in XY only. Section-to-section registration jitter adds Z steps that are not cable, so this is the conservative figure for comparing cells across volumes |
| Longest path from soma | Longest route along the skeleton from the soma annotation to any annotation |
| Branch points / tips | Annotations linked to more than two others / to exactly one other (soma excluded) |
| Fragments | Connected pieces of the annotation graph; more than one means Viking holds unlinked parts of the cell (lengths count them; paths from the soma cannot reach them) |
| I:E ratio | ConvPost ÷ RibbonPost, counted as synapse structures on the cell. Gap junctions, adherens, presynaptic sites and every other type play no part. A postsynapse is ribbon or conventional by its Viking tags, in any order; one whose tags do not say takes the type of the structure it is linked to, or is left out (see *Untagged postsynapses* below) |
| Untagged PSDs | Postsynapses whose Viking tags say neither ribbon nor conventional; "inferred" ones took their type from the linked structure and are in the counts above, the rest are in neither |
| Inhibitory fraction | ConvPost ÷ (ConvPost + RibbonPost), what the MATLAB `ieRatio` actually computed |
| Unfinished branches | Cell-body annotations with at most one link that are not tagged Terminal in Viking. Off Edge ends count (the branch continues beyond what was traced); a stranded single annotation counts; synapse annotations never do |

All lengths follow the centerline as annotated: a broad process traced as
large overlapping discs across several sections zigzags between their
centers, which inflates the 3-D length more than the in-plane one. No
smoothing or resampling is applied; the API is
`GET /api/neurons/{source}/{id}/morphometrics` (`?soma_fraction=0.8`).

### Loaded cells

With two or more cells loaded the **Loaded cells** card lists them all —
every volume together — with the Morphology metrics as columns. Headings
sort, hovering a heading shows the definition, a cell's ID selects it, and
**Download .csv** writes the table with a few extra columns (tips,
annotation count, soma radius, XYZ extent) for a spreadsheet or a plot.

### Linked neurons

The `getAllLinkedNeurons` table: one row per synapse of the cell with the
partner neuron it links to (blank when Viking has no link for it), the
partner's label, the synapse type and structure ID, the direction (`pre`
when this cell is the presynaptic side) and the synapse's position in
microns. The summary line above it counts the synapses, how many are linked
and to how many distinct partners, and how many are unlinked.

**Order** offers the two orderings in use in the lab: by partner, as the
MATLAB function returned it, or by synapse type, label, partner and synapse,
as `getAllLinkedNeurons_sorted` (2025) wrote it. Click a column heading to
sort by it; the filter box keeps rows whose partner, label, type or synapse
ID contain what you type. **.csv** and **.xlsx** download the table in the
chosen order, named with the cell and its volume
(`c1178-NeitzInferiorMonkey-linked-neurons.xlsx`). **↻ Viking** resolves the
partners again — links are cached with the cell, so an edit in Viking is not
seen until then. **Synapses per partner** below collapses the table to one
row per partner and type. A partner ID is a button that puts it, with its
volume, in the Structure ID box.

**Tagged?** says whether Viking's tags say what a postsynapse is: *Y*, *N*,
or *inferred*; it is blank (—) for every type whose Viking type alone decides
(a ribbon, a conventional presynapse, a gap junction). Rows of untagged
postsynapses are drawn in amber and inferred ones in italics; hovering the
cell shows the tags and what was inferred from what. The column sorts, and the
filter keeps `N`, `Y` or `inferred` rows when that is all it holds. The
**.csv**/**.xlsx** carry four more columns after the MATLAB ones: `Tags`,
`TagStatus`, `SuggestedTag` and `LocationID`.

#### Untagged postsynapses

A Viking *Postsynapse* (PSD) is typed by its tags: `Ribbon` makes a ribbon
postsynapse (RibbonPost), `Conventional` a conventional one (ConvPost), with
`Bipolar` the bipolar conventional one, `GABA` a GABA one, `TA`/`NTA`/`mNTA`
the basal ones. Tags are read as a set, in any order and any case —
`Ribbon;Bipolar;Glutamate` and `Bipolar;Glutamate;Ribbon` are the same — and
qualifiers (`Glutamate`, `PR(cone)`, `Perisomal`, `DCV`…) do not decide.

MATLAB counted a PSD whose tags said neither — no tag at all, as on RC1's
early annotations, or `AC`, `Doubtful`, `Conventional;Ribbon` — as
conventional. This app does not assume:

1. **Inferred.** A PSD linked in Viking to a ribbon synapse takes RibbonPost,
   to a conventional synapse ConvPost, to a bipolar conventional synapse
   BipConvPost — when every structure it is linked to says the same thing.
   It is counted, and marked *inferred*.
2. **Untagged.** Anything else — no link, or links that disagree — is
   **UntaggedPost**, a type of its own: counted in neither the ribbon nor the
   conventional total, drawn as its own (pale gray) synapse type, and listed.

The line above the table counts them (*N PSDs untagged in Viking: … typed
from the linked structure, … of unknown kind*), and **Untagged PSDs (.csv)**
downloads them, one row each, to be put right in Viking: volume, synapse ID,
a Location ID (*Goto Location ID*) and Viking X Y Z (*Goto Location*), the
type used here, the tag status, the tags it has, the tag an inferred one is
missing, and the partner. Nothing is written to Viking by this tool. RC1
c606, for instance: 1,035 of its postsynapses have no tag; 257 are linked to
ribbons and 130 to conventional synapses; 649 are linked to nothing and need
the images. Its I:E ratio is 131 ÷ 261 = 0.50; counted as MATLAB did, it was 1,037 ÷ 4 = 259.

Two I/E numbers are reported: **I:E ratio** is `numInh / numExc`, and
**inhibitory fraction** is `numInh / (numInh + numExc)`. The MATLAB `ieRatio`
documented the first and computed the second; both are given so published values
can be matched either way.

## Settings: debug tools

The last tab, set apart at the far end of the tab row, holds tools for when something
looks wrong rather than for the work itself.

- **This page** says whether the page you have is the build the server serves. When it
  is not ("This page was loaded as 2.7.1-…, and the server now serves 2.7.2-…"), reload;
  this is the first thing to look at when a fix seems not to have arrived. *Copy details*
  puts both builds, the browser and the window size on the clipboard for a bug report.
- **Rendering**: *Enable expert render mode* builds render settings as set, even when a
  cell cannot be built with them, and marks the settings to change; off (the default), a
  render that fails is built again with settings that work, which the cell keeps (*When a
  render fails*). Kept in this browser; its hover says both. *IPL depth measured*: along
  the layers' normals (the default) or straight along Z (MATLAB's column), for the
  Stratification tab and its files, Color by IPL depth, Clip IPL and a clipped export
  (*Stratification*). Switching it measures again at once: the chart, and every cell
  colored or clipped by depth. Kept in this browser.
- **Cached files**: *Clear cached files and reload* has the browser drop what it cached of
  the site (the page, its scripts and styles) and reloads. Your sessions, saved settings
  and sign-in stay, and the page comes back to this tab saying it was done.
- **Saved in this browser** lists what the site keeps in your browser, by what it is for
  (saved sessions, render defaults, layout, saved colors, the Connectivity query, the
  dropdown style, the Viking programs seen), with its size. Tick what to reset and press
  *Reset the ticked items and reload*: it is removed from every tab of the site. Resetting
  saved sessions means the next load starts with no cells. This is the cure for a setting
  saved in the browser that keeps coming back wrong.
- **Correction log** lists the annotations Viking's section correction (Curve fit, or
  Field + fit) moved toward a much wider annotation linked to them, in every cell loaded on
  the server that you may see. Viking's curve fit (service 1.2.0) takes a soma's or a large
  outline's center as a point of the process, so a thin process leaving a soma is pulled
  about halfway to the soma's center: Inferior Monkey c1178's 1328721 (radius 0.74 µm)
  moved 10.6 µm toward the soma disc 1557441, 0.5 of the way. Each row gives the
  volume, cell, mode and service version, the annotation's Location ID and section, its
  radius and how far it moved, the wider annotation and its radius, how many times wider,
  how far along the line between them it moved (*Of the way*), and the annotations on its
  chain that moved with it. The fit puts the annotation on the line between its two
  neighbors, as far along it as its section is between theirs, so *Of the way* is near 0.5
  when they are a section either side and near a third, two thirds or three quarters when
  they are not (RC1 c410's 1567770 went 0.31 of the way to a soma two sections away). A
  Location ID selects that annotation in the 3D view (a cell this tab has not loaded goes
  to the Structure ID box instead, with its volume), and ↗ shows it in Viking. *Wider by at least* (default 4×, kept in this browser)
  sets which pulls are listed; it does not change Viking's correction, which takes no such
  setting yet. A pull is listed when the move points at the wider annotation, is longer
  than the annotation's diameter (and than 1 µm), and does not carry it past the wider one
  (since 2.22.1: of five such rows on production at 4×, four were tips beside a thin
  neighbor under 0.7 µm away and one a point drawn inside an outline, none this bug).
  Cells under the residual field alone are listed as not checked. The server's log names the pulls at 4× as each cell is loaded.

  A second table (since 2.36.0), **Traced outlines moved off the outlines linked to them**,
  lists for each cell the outlines the correction moved 0.25 µm or more although, where
  Viking draws them, at least 80 % of each (of the smaller of two) overlaps the outlines
  linked to it, by a move that took it at least 0.2 of the way off them. *Overlap* is the
  shared area where Viking draws it → moved by the correction; *Off* is the overlap lost
  as a share of what the same move takes from a circle of its area lying on a copy of
  itself (1: straight off); *Toward* and *Step* name the linked outline the move points at
  most and how far apart their centroids are; *Why* is **shape** (that outline's centroid
  steps away because its shape changes, a lobe or a branch taken in), **slide** (that
  outline is this one displaced) or **other** (no linked outline lies that way: the fit
  carried it along with the process, often after a pull toward a soma). A cell's first 10
  rows are shown, *Show all* the rest. A move that brings an outline onto its neighbors (a
  misregistered section the fit lined up) is never listed.
  A cell's own pulls are also listed in its panel's
  [Position shift](#position-shift-where-each-annotation-was-and-where-it-is-drawn) section,
  at this ratio.
- **This server** is shown in full to an admin (the server lists them in
  `SBFSEM_ADMIN_USERS`; on a local checkout everyone is one): the version, how long it
  has been up, its memory, the cells and surfaces it holds, its disk cache, its build
  processes and the build queue. Its buttons act for everyone using the server:
  *Clear surfaces in memory* (each is read again from the disk cache, or built, on its next
  request), *Clear surfaces in memory and on disk* (every cell is built from scratch),
  *Replace build processes* (builds running now finish first) and *Restart the server*
  (down for about 10 s; every open page is asked to reload and gets its session back;
  builds running are lost). Each asks first. Restart is offered only where systemd runs
  the server, since nothing else would start it again. Anyone else sees why the server's
  tools are not offered, and under which account they are signed in.

## Export

From the Analyses tab: `.swc` (morphology), `.json` (schema v2, round-trips),
`.dae` (COLLADA), `.stl`, `.obj`, `.ply` and `.glb`. The right-click menu on a
cell (in the render or its Loaded row) offers the same formats under *Export*,
and *SWC of every loaded … cell (.zip)*. Mesh formats use the cell's own render
settings -- its method and pathway, the Pixel size of a Closed curve or Hybrid
cell, and its Clip IPL when it has one -- so the file is the surface on screen.

An export is fetched before it is saved: the status line shows *Exporting …* and
then *Exported c1178.obj (3.2 MB)*. When the server cannot make the file (a
Closed curve at a Pixel size that needs more voxels than the server allows, a
surface left empty by the clip, a gateway timeout on a very large cell) nothing
is saved and the status line says why, in red, with the server's own words and,
for the voxel limit, the finest Pixel size that fits. The bulk archive draws
every cell the same way, so cells whose Registration or section-correction
choices differ are exported as separate archives (`RC1-swc-1.zip`,
`RC1-swc-2.zip`), as are more than 200 cells.

### SWC

The `.swc` is the annotation skeleton: one row per cell-body annotation
(`n T x y z R P`), breadth-first from the soma so every parent precedes its
children, coordinates and radius in microns in the registered volume frame.
The **type column `T` is the SWC type, not the Viking TypeCode**: the app
writes `1` soma (annotations with a radius at least 80 % of the largest) and
`3` dendrite for everything else, and the file header lists the codes it
contains (`# TYPE_CODES 1 soma, 3 dendrite`). The MATLAB exporter also typed
branch points `5` and end points `6`, the original 1990s SWC convention;
today's SWC specification reads those as "custom" and "unspecified neurite",
so tools such as NeuroM or navis would treat them as something other than
dendrite. That typing is still available: the API and the Python library
default to it (`swc_branch_types=true`, `write_swc(..., branch_types=True)`),
and the header then reads `# TYPE_CODES 1 soma, 3 dendrite, 5 branch point,
6 end point`. Branch points and tips are recoverable from the parent column
either way.

Closed-curve annotations appear in the SWC at their centroid with the radius
Viking stores for them, which is not what was traced; a cell annotated mostly
as closed curves (a Müller cell, a cone pedicle) is better measured from its
mesh than from its SWC.

**Export options.** The default file is the record of what was traced: one row
per annotation, at Viking's positions, with Viking's stored radius. Four
options change that, none of them the default, each asked for by adding it to
the export link (or to the bulk export's), and each described in the file's
header so that a figure can say which file it used:

| Option | What the file holds instead |
|---|---|
| `swc_relax=f` | every node moved toward its neighbors to shorten the path, never outside `f` of its own annotation (see *Skeleton check* above) |
| `swc_lamellae=true` | each lamella (a traced profile at least twice as long as wide, or a stack of them on consecutive sections) as the chain along its long axis, in place of its annotations. The chain's ends are tips: a flat end past the last branch counts as cable. Each link that left the stack now leaves the chain node nearest the annotation at its other end. Header: `LAMELLAE`, `LAMELLA_RULE`, and one `LAMELLA` line per stack with its Location IDs, sections and rows. Not with `swc_relax`. |
| `swc_sampling=sphere` | only the rows Bradley & Withers's minimal sphere network keeps (2016, eq. 1): along each unbranched path, from a kept row the next is the farthest of the unbroken run of rows whose spheres of radius R touch it. Tips, branch points, roots and both ends of each dropped link are kept. |
| `swc_radius=modal` | a closed curve's modal radius in R: its thickness, measured from the deepest point of its traced outline, the same thing on a circle and a crescent. A circle keeps its stored radius, and each closed curve's stored radius follows in the header as `R_STORED row r`. |

On RPC1 c192 as of 2026-09-21 (2,849 annotations): the default file is 2,849
rows and 764.36 µm of links; with lamellae 3,456 rows and 918.94 µm; sphere
sampled 1,167 rows and 688.39 µm. A lamella file is longer than the rule's
net in the Skeleton check suggests: the check counts no link inside an outline,
while the file must join each chain to the processes that continue above and
below its stack (on c192, 135.52 µm of joins, 109.10 µm of it inside the
outlines). The header gives both figures.

Note the COLLADA scale change: `unit/@meter` is `1e-6` here, where the MATLAB
class wrote `0.1` and the free function wrote `1`. Assets exported from the
MATLAB tool will not line up with assets exported from this one.
