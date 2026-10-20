# Changelog

## Unreleased

### The Position shift browser check passes again under Field + fit (2026-10-20)

- **Ten of `tools/check_shifts.py`'s checks had failed since 2.17.0 made Field + fit the default correction for the Neitz volumes**, the same ten on 2.17.1's own front end, so none was a fault of the app. `moves`, `moveset` and `moveplane` expected c1178's plain copy to be `curvefit.1`; they now read the cell's own copy tag (`all.1` today) and look up the moves and the moved copy (`all.1.m…`) under it. The `moves` check that the moved copy's annotations and outlines are asked for while its surface builds now accepts them in the same tick as the last poll, as `regswitch` does: a surface the server already holds is answered by the first poll, and the two can share a 0.1 ms timestamp.
- **Two checks were measuring something other than what they meant to (measured before changing them).** `slicecolors` bounded the plane by its section's annotations alone, but the plane also frames the arrows drawn on that section, tails included (`planeRect`). Under Field + fit a closed curve on c1178's section 1159 moves 10.9 µm, and its arrow's tail widens what the section holds from 21.3 to 38.8 µm; the plane is exactly that plus its 5 % margin (42.6 × 23.2 µm). The bound is now taken against the annotations and their arrows, with the same tolerance, and the plane must still be a small part of the cell's 206 × 210 µm. `focus` counted yellow pixels in the frame drawn just after the camera closed in, before the page's frame loop redraws the selection's tube for the new zoom. In that frame the tube was the cell view's 2 px seen from 25 µm, several times as wide, and the arrow lay inside it (29,791 lit with the focused arrow, 29,955 without). The check now lets the page draw first: 1,856 with, 1,068 without. Full run on a dev server of this tree: 84 passed, 10 failed → 96 passed, 0 failed, 1 skipped (`layout`, which takes screenshots only with `--shots`; `moveplane` used to stop two checks early).

### A public copy on GitHub, as Wired (2026-10-20)

- **The project has a public home, <https://github.com/jameskuch/wired-em>, under the name Wired** ("visual tools for connectomics"). Each release is published there from the private repository by `tools/publish_public.py`. It leaves out the development notes, the AI-session files, colleagues' account names on the site and a paper we may not redistribute (`.public-exclude`), and replaces the server's address (`.public-scrub`). It refuses to publish when anything private or shaped like a credential is left, when a file is over 50 MB, or when a link points at a file it left out. Only committed work is published. The site, the library (`sbfsem`), its commands and the domain keep their names for now.
- README (Wired, how to deploy without naming the server, contributing), LICENSE.txt (the port's notice beside the original), CONTRIBUTING.md and CITATION.cff (GitHub's *Cite this repository*). CI (`.github/workflows/ci.yml`) now runs on the public copy: Ubuntu with Python 3.11 to 3.13 and macOS with 3.13; Windows joins once the suite has been run there. Tests: `tests/unit/test_publish_public.py` (new, 6: what is left out and replaced, every refusal, and a publish from a real repository and clone that carries only the committed tree).
- **The first public CI run found that skimage on Apple Silicon draws knife-edge pixels differently.** Its arm64 build fuses `x0*y1 - x1*y0` into one multiply-add (rounding `x1*y0` alone), so on GitHub's macOS runner two of the random-polygon cases came out 11 and 1 pixels away from the fill; `math.fma` reproduces exactly those pixels, and the x86_64 builds (this Mac's Python, the Ubuntu runners, production) match the fill bit for bit. The fill is numpy, which never fuses, so it gives one answer everywhere; where skimage fuses, `tests/unit/test_rasterize.py` now holds it to skimage's rule with each product rounded on its own (`unfused_mask`), and checks that rule against skimage itself wherever skimage does not fuse (+12 tests, all 3,600 random polygons). Rings the library hands to skimage itself (small, not finite, or too large) are still compared with skimage on every platform.

## 2.19.2 — 2026-10-20

### The build gate weighs a Voxel remesh by the sheet it will draw (2026-10-20)

- **A Voxel remesh, Convolution or Ultraliser build whose raw sheet stayed under 3 million triangles was weighed by the cell's annotations alone** (0.3 MB each and 420 MB), not by the sheet a large Face cap lets it draw, so it could be started beside other builds it did not fit with. Measured with `sbfsem-measure`'s build on this Mac: RPC1 c430 at a Face cap of 1,460,000 drew 2.38 million raw triangles and peaked at 2,582 MB, weighed 2,546; Inferior c1178 at 1,100,000 drew 2.30 million and peaked at 2,535 MB, weighed 1,451. At the default cap Inferior c16137 peaked at 1,091 MB (weighed 1,000) and RC2 c5624 at 1,006 (weighed 915).
- **The gate now also prices the sheet the build is sized to draw**, the raw triangles the render estimate has named since 2.18.0 (`FieldPlanner.raw_triangles`, the build's own sizing without contouring), by the measured law (310 MB and 0.445 MB for every 1,000, 0.37 MB more when the cap decimates them), and keeps whichever price is more: c430 3,869 MB, c1178 at 1,100,000 2,978, c16137, c5624, c1178 and c430 at the default cap 1,288. Every build measured peaked 15 to 33 % under the price the sheet sets, because the sizing counts 1.2 to 2 times the triangles a build then draws. c1178 at 2,000,000 keeps its 4,151 (its 6 million budget, priced whole). The cells too small for the sheet to matter keep their price: Inferior c943 477 MB and Temporal c106 511 at the default settings (peaks 273 and 380). At a typed 0.04 µm under a Face cap of 900,000 they are weighed 579 and 577 MB (peaks 445 and 458; 477 and 511 before).
- **The decimator's copy is counted only when it runs**, when the sheet is over its Face cap or Decimate is under 1. A sheet under its cap is kept as drawn, which is what the render estimate's `peak_mb` now says too: Temporal c106 at the default cap drew 323,256 triangles, kept them all and peaked at 380 MB, which the copy would have priced at 573.
- **What it costs the mesh job's request**: nothing when the page's render estimate of the same settings has planned them, which it does as the settings change (the plans are kept beside the estimates); otherwise one plan, 0.04 to 0.07 s, and on a cell planned for the first time 0.18 to 0.54 s to make its pieces (c430, c1178). A plan that fails leaves the old price. Hybrid is weighed as before.
- Tests: `tests/unit/test_estimate.py` +4 (the price is the planned sheet's on c943 and no build draws more than it, the decimator's copy, only field builds are planned and on the page's plans, a mesh job's weight), all 4 failing on 2.19.1.

## 2.19.1 — 2026-10-20

### Render compare marks a pane's settings that will not build as set, with the fixes (2026-10-20)

- **A Render compare pane's options panel now marks the settings that will not build as set, as the 3D render panel has since 2.18.0** (the user's request of 2026-10-20, which 2.18.0 met for the 3D render alone). The layer's controls to change turn black with red text, and a row at the top of the panel says why, for the cell the picker names, and lists the fixes, best first: a Closed curve or Hybrid Voxel size past a limit (Voxel marked, and Join bodies or the smoothing when the server's memory is the limit), or a typed Fine voxel the Face cap holds coarser under Voxel remesh, Convolution or Ultraliser (Fine voxel, and Face cap when a larger cap would help). The answers are the server's estimates the 3D render reads, from the one cache the two tabs share, so a size already asked about in either is not asked again. **A fix changes only the layer the panel edits**, as if typed (a Voxel size it sets becomes the layer's own, kept across cells): the other layers and panes and the 3D render panel keep their settings, and nothing is built until Compare. Measured on c943 (Inferior): Fine voxel 0.02 µm under Face cap 20,000 is held to 0.0266 µm; the row offers Face cap 360,000 (1.1 M raw triangles, about 1.2 GB), Face cap 0 (about 781 MB) and Fine voxel 0.0266 µm; with the first picked, Compare built 0.02 µm as typed (360,000 triangles, not coarsened). The cost chip's hover also names the Face cap that would build an automatic Fine voxel at the annotations' voxel, as the 3D render's does. Code: `compareoptions.js` (`OptionsPanel._estimate`, `_conflicts`; the chip and the marks read one estimate), `compare.js` (`_fixPicked`), `conflictpanel.js` (the row's id is the 3D render panel's alone).
- Tests: `tests/js/test_compare.mjs` (+3: a field layer's marks, its fixes in order and one picked into that layer only, the other pane untouched and nothing built; a Closed curve layer's Voxel and Join bodies, the second fix setting both and the size becoming the layer's own; no row without estimates). `tools/check_app.py --only compareconflicts` (new, 11, headless Chrome on a dev server of this tree): the marks are rgb(0, 0, 0) with rgb(255, 0, 0) text on the field and its box, the row is the pane's own (the 3D render's stays hidden, one element has its id), a real click on the best fix fills that layer and its boxes and clears the marks, Compare then builds the typed voxel, and the Closed curve case marks and fixes both settings. Unchanged and passing in the same run: `conflicts` (9), `voxel` (19; it covers the pane's cost chip, which now reads the same estimate), `compare`, `comparekeys`, `comparestate` and `comparetoolbar` (39); 79 in all, nothing thrown. Docs: `docs/user-guide.md` (*Render compare*, *Settings that will not build as set*).

## 2.19.0 — 2026-10-20

### A view cube in the corner of the 3D view, as in Fusion 360 (2026-10-20)

- **A cube in the top-right corner of the 3D view stays where it is and turns with the view** (asked for 2026-10-20: "a stationary X Y Z coordinate cube, identical to the one in Autodesk Fusion 360 … double clicking rotates the viewport to that plane"). Its faces are labeled with the volume's axes (+X, −Y, +Z …), with an X/Y/Z tripod in the Axes helper's colors from the corner nearest the volume's origin. **Double-click** a face to look straight down an axis (the six faces are the XY1 to YZ2 buttons exactly, up included), an edge for the 45° view between two faces (Z as upright as the view allows), a corner for the isometric view between three. The part under the pointer lights blue and is named under the cube; a single click turns nothing. Dragging the cube orbits (about a quarter turn for its width), the house is Reset view, and looking straight at a face, four triangles turn to the face beside it and two curved arrows turn the picture a quarter. A turn glides there (0.22 s, up to 0.6 s for a half turn; none with reduced motion) about the same pivot a viewpoint button uses, so what is in the middle of the picture stays there at its size; each turn and drag is one undo step. Under Flip Z the labels and tripod follow the volume's Z. **View cube** in the toolbar, beside Axes, shows or hides it (on by default, kept with the session); the readout row and the notices give it the corner. It is drawn with CSS 3D transforms over the render, not with WebGL: no extra context (the page has 16, shared with the Render compare panes), and never in a snapshot or a video. Code: `static/js/viewcube.js` (new: the hit test casts the pointer's ray against the cube itself, the views, the quarter turns, the labels), `Renderer3D.turnTo` / `viewFrame` / `spinBy` / `onFrame`; `setViewpoint` and the cube share `_viewPivot` and `_placeView`.
- **Viewpoints in a row keep their pivot again.** After a view is placed, OrbitControls moves the camera and its target by a float bit or two over the next frames (2e-14 µm on c943 after XY1; a bit a frame after XZ1), and the check that nothing had moved since the last viewpoint compared exactly, so it measured the depth again and the pivot crept (the creep it was written against: 33.7 to 43.4 µm over seven buttons on RPC1 c2628). It now allows a billionth of the scene's radius. Measured in the browser check: nine quarter turns of the cube leave the pivot where it was, and XY1 pressed after the cube's +Z puts the camera exactly where the cube did.
- **A drag begun within 350 ms of the end of another camera move was caught halfway in the undo history.** The end of every camera gesture took its undo step at once and then a second one 350 ms later (through `persist`), which landed mid-drag when the next drag had begun by then: Undo then stepped to a view half-way through it. The end of a gesture now only saves the session. Reset view and a viewpoint button settle a view cube turn still under way rather than being overwritten by it.
- Tests: `tests/js/test_viewcube.mjs` (new, 23: all 26 parts hit at the middle of their own view, the zone bounds, faces against the renderer's own viewpoint table, no face's text mirrored from any view, the quarter turns, Flip Z, the axis letters behind the cube hidden and on the stage from every direction). `tools/check_app.py --only viewcube` (new, 24 checks with a real mouse; the drag's step failed before the history fix, and XY1 after the cube's +Z before the pivot fix). Unchanged and passing: `check_app.py --only orbit,keys,undo,twod,hold,locate,sheets` (37), `check_renderer.py` (70). Docs: `docs/user-guide.md` (View cube).

## 2.18.0 — 2026-10-20

### Settings that will not build as set are marked before Apply, with the ways to fix them (2026-10-20)

- **The render panel now says before Apply when a combination of settings will not build as set, marks the controls to change black with red text, and lists what to change, best first** (the user's request of 2026-10-20). A row at the top of the panel names each selected cell and why; each fix is a button that puts its settings into the controls, and Apply then builds them. Nothing changes for settings that build as set. Two cases, both read from the server's estimate of each selected cell (`GET .../render-estimate`):
  - **Voxel remesh, Convolution and Ultraliser: a typed Fine voxel the build would contour coarser.** Until now the page learned it from the finished mesh's notice, after the build. The whole-cell grid's voxel is held to the raw triangles the Face cap allows (three for each one kept, at most 10 million), to the field evaluations those allow, and to 0.02 µm. The server now runs the build's own sizing on the build's own pieces without contouring (`unify.plan_voxel`, `estimate.FieldPlanner`) and answers the voxel the build would use and why, the least Face cap that builds the typed voxel (to 10,000 triangles), Face cap 0, the finest any budget allows, and each one's raw triangles and memory. Fine voxel is marked, and Face cap when a larger cap would help. Measured: RPC1 c430 under the default Voxel remesh is built at 0.076 µm, where its annotations ask for 0.040 µm; Face cap 1,460,000 builds 0.040 µm (4.4 million raw triangles, about 3.9 GB) and so does Face cap 0 (about 2.3 GB, nothing decimated); the finest any budget allows it is 0.030 µm. Inferior c1178 is built at 0.120 µm under the default cap (its annotations ask for 0.073 µm) and can go no finer than 0.042 µm. An automatic Fine voxel the cap holds coarser is not marked, since that is what the cap is for; the cost chip's hover names the cap that would build it at the annotations' voxel. On this Mac 0.04 to 0.45 s to make a cell's pieces (c16137, c430, c1178), then 0.04 to 0.11 s an answer; on production 2.7 to 3.1 s for a cell's first answer (c430, c1178), 0.7 to 0.9 s for each new Fine voxel or Face cap, and 0.006 s for one already asked: answers are kept per copy and options and cleared with the cached renders.
  - **Closed curve and Hybrid: a Voxel size past a limit.** The chip already turned red and Apply already built each cell at the finest size that fits it; now Voxel is marked and the first fix is the size every selected cell fits at. When the limit is the server's memory and Join bodies or the smoothing is on (each only adds memory), it is marked too, and the estimate names the finer size that fits with it off (`alternatives`): Inferior c16137 at 0.008 µm on a server giving builds 3 GB, 0.027 µm as set and 0.022 µm with Join bodies off.
  - The memory the page is told for a Voxel remesh build is the sheet the cell draws (310 MB and 0.445 MB for every 1,000 raw triangles, 0.37 MB more under a cap): c16137 at 0.04 µm with every triangle kept, 1,422 MB from it, 1,429 MB measured. The build gate still prices the whole budget a cap allows.
- **A build cut short by a restart of the server was reported as one that ran out of memory, with advice to ask for a coarser setting.** On 2026-10-20 at 17:42:41 UTC a deploy stopped the service 20 s into a Closed curve build of c1178 at 0.05 µm with Curve links 1 µm (a build that peaks at 1.19 GB here; the kernel logged no out-of-memory kill), and the page said the server had most likely run out of memory. systemd stops the whole unit at once, so the build processes get the service's SIGTERM. The service now hears that signal first (`app._watch_stop`, chained ahead of uvicorn's), a build whose process dies within 2 s of it is `BuildInterrupted`, and its job is left open saying "the server is restarting": the page polls through the restart, is told by the new process that the job is gone, and asks for it again, as it already did for a job lost in a restart. A process killed while the service runs is still reported as memory.
- Tests: `tests/unit/test_render_conflicts.py` (new, 14: the voxel estimated is the voxel five builds of c943 use, coarsened or not, and why; the cap it names builds the typed voxel and one step less does not; the memory is the sheet law; the floor and the automatic voxel; answers kept and cleared; other pathways get none; Closed curve's alternatives each fit at the size they name, and none when the limit is the faces or neither is on; the route), `tests/unit/test_build_interrupted.py` (new, 6; none can run on 2.17.9, which has no `BuildInterrupted`), `tests/js/test_renderconflicts.mjs` (new, 11). `tools/check_app.py --only conflicts` (new, 10, headless Chrome): the marks are rgb(0, 0, 0) with rgb(255, 0, 0) text on the field and its box, the fixes in order, a pick fills the controls and clears the marks, Apply then builds the typed voxel with no Fine voxel notice, the Closed curve case marks Voxel and Join bodies and its other fix sets both, and a method that reads neither marks nothing. The geometry fingerprints are unchanged: the sizing was moved out of `unified_mesh` into `_size_voxel`, `_grid_maker` and `_fit_work`, which it now calls. Docs: `docs/user-guide.md` (*Settings that will not build as set*), `docs/api.md` (`render-estimate`'s `field` and `alternatives`).

## 2.17.9 — 2026-10-20

### The skeleton is made in a fifth of the time (2026-10-20)

- **A cell's skeleton was the slowest reply left after 2.17.8, and nearly all of its time was one lookup.** Splitting the cell into unbranched segments is quick. Giving each segment its annotations' sections, positions and radii was not: a pandas label lookup per segment, half a millisecond each, 87 % of the reply (RPC1 c430's 218 segments, 0.19 of 0.23 s under the profiler). They are now looked up all at once by sorted Location ID. The arrays are the old ones to the bit and in the same memory layout, so the skeleton reply is byte for byte what it was (RC2 c74420, RPC1 c430 and Inferior c1178 compared), and every surface built from discs, which segments the cell first in its build process, is the same surface made sooner (no geometry version bump; the fingerprints in `tests/unit/test_geometry_version.py` pass unchanged). The segmentation itself: c74420's 584 segments 0.27 → 0.03 s, c430's 218 0.10 → 0.01 s, c1178's 156 0.08 → 0.01 s.
- **Before → after**, on this Mac, in the process, the whole request, three in a row: RC2 c74420 (10,518 annotations, 0.84 MB) 0.28 to 0.30 → 0.05 to 0.06 s, RPC1 c430 0.11 to 0.12 → 0.02 s, Inferior c1178 0.08 to 0.10 → 0.02 s. On production, on the server itself, with no build running: c74420 0.78 to 0.81 → 0.10 to 0.27 s, c430 0.28 to 0.29 → 0.035 s, and the replies hash the same before and after. The reply is not kept as 2.17.6 keeps the outlines: made this cheaply, a kept copy would save at most 0.1 to 0.3 s a request on the largest cell, and hold memory, and the page already keeps the skeleton it was sent across a switch back (2.17.7).
- **The skeleton check is left as it is.** It costs 0.4 to 0.5 s on c74420 here and 0.9 to 1.2 s on production, every time, but it runs only when its button is pressed, its time is spread over a dozen steps (the cell's graph built three times, a median for every annotation, a distance for every link), and the length floor fetched after every check costs 9.7 s on c74420 the first time it is asked (kept after that). Tests: `tests/unit/test_geometry.py` (+3: the new lookup against pandas' on c943 and c1178, every array's values, type and layout; a missing Location ID named; repeated IDs still looked up by label).

## 2.17.8 — 2026-10-20

### Large replies are written by the fast JSON encoder (2026-10-20)

- **Every reply with a fixed shape was written twice, in the one server process every other request waits on.** pydantic turned it into Python lists and dicts, then Python's `json.dumps` turned those into text one number at a time. FastAPI writes such a reply in one pass with pydantic's own encoder, written in Rust, but only while no response class is named for the route. The app named one for every route (the class that writes `NaN` as `null`), so none took that path. 2.17.6 sent the outlines reply through pydantic's encoder by hand. Now every route with a response model does, and so does a finished job's poll (an error map, a difference, a surface sent as JSON). Replies without a model (a dict, a list) still go through the `NaN` backstop, and a non-finite number is still `null` everywhere.
- **The bytes are the same as before.** Of 45 replies (two cells; every route that sends geometry, and the analyses), 43 are identical. `/nodes` differs only in how 3 numbers (c1178) and 15 (RPC1 c430) under 1e-4 are spelled (`0.00008…` for `8…e-05`): the same values. Of the finished jobs, the surface and the difference are identical, and the error map spells 1,306 small numbers the other way.
- **Before → after**, on this Mac, in the process, the whole request. Inferior c1178: closed-curve mesh (11.7 MB) 0.63 → 0.47 s, vessels (4.0 MB) 0.137 → 0.076 s, annotation wireframe (1.9 MB) 0.107 → 0.059 s, its largest synapse mesh (1.4 MB) 0.052 → 0.031 s, `/nodes` (0.75 MB) 0.033 → 0.023 s. RPC1 c430: annotation wireframe (2.1 MB) 0.128 → 0.077 s. A finished job's JSON poll, c1178: error map (3.0 MB) 0.088 → 0.020 s, difference (1.5 MB) 0.042 → 0.011 s, surface (5.9 MB; clients other than the page, which takes the binary) 0.115 → 0.051 s. On production, on the server itself (the two warm requests of three): RC2 c74420's annotation wireframe (5.3 MB) 0.65 to 0.86 → 0.45 s, its largest synapse mesh (1.8 MB) 0.13 → 0.08 s, `/nodes` (3.3 MB) 0.23 to 0.42 → 0.17 to 0.35 s; RPC1 c430's annotation wireframe (2.0 MB) 0.25 → 0.17 s, its largest synapse mesh (1.7 MB) 0.12 → 0.08 s. The skeleton, whose time is the building of it, is unchanged. Tests: `tests/unit/test_json_encoder.py` (new, 8; 7 fail on 2.17.6: the fast path is taken for every route with a model, a dict route keeps the backstop, the bytes are the old ones, `NaN` is `null`). Docs: `docs/api.md`.

## 2.17.7 — 2026-10-20

### A switch back draws the outlines the tab already holds (2026-10-20)

- **A Registration switch back to a setting the tab had already drawn asked the server for the cell's annotations, traced outlines and skeleton again.** The page kept one set per cell and let it go on every switch. Since 2.17.6 the server answers the outlines at once, but RPC1 c430's are still 7.6 MB (3.3 MB gzipped), and nginx compressing them and the link carrying them held up the surface's own download beside them. Now the tab keeps what it was answered by the state of the copy it came from, as the server keeps its outlines reply: the cell, its annotations as Viking last stamped them (the newest annotation, the structure row and how many annotations it has) and the moves applied to them (the summary's `positions`: the current transforms, the section correction, a person's own moves). A switch back, the Undo of an unload, and an Update from Viking that found nothing changed draw from it without a request. An edit in Viking (a later stamp or a different count, so a deleted annotation too), another setting, or a manual move is another state, and is asked for. An answer is kept only when no load of the cell was under way as it was asked or began before it came back, since a load can change what the copy holds under the same name. A tab keeps 3 million numbers of them (about 100 MB at the most), the least recently used going first; c430's three weigh 425,916, its outlines 14 MB of the page's memory. They are let go of when a new version is deployed or the server restarts, and when a workspace is opened.
- **Before → after**, production from this Mac through nginx, c430 with Show annotations and Show skeleton on, switching back between Curve fit with and without the current transforms (the server's cached surface; the page's order replayed, 4 of each in alternating pairs): 0.996 → 0.415 s from the click to everything in hand. The surface itself lands sooner too, no longer sharing the link with the outlines: its last poll 0.48 to 0.82 s → 0.17 to 0.19 s. With the display's defaults (no rings, no skeleton) only the annotations were asked again: 0.452 → 0.429 s. Tests: `tests/js/test_appcore.mjs` (+9: the key against each thing that moves an annotation and each kind of Viking edit, the weight, an answer kept, a failure or a doubtful answer not kept, the bound, a switch back, an Update with and without an edit, a load under way, and a load begun or answered while a request was out). `tools/check_shifts.py --only regswitch` 11 → 19 checks: a switch back to the setting the cell started on and to the first switch's setting, and an Update that found nothing changed, ask for nothing and draw what was kept; an Update that found the cell edited asks again. The six new checks fail on 2.17.6's front end. Docs: `docs/user-guide.md` (Registration).

## 2.17.6 — 2026-10-20

### A cell's outlines are kept, and made in a third of the time (2026-10-20)

- **A cell's traced outlines were made again for every request, and since 2.17.2 they were what a Registration switch back waited for.** The server rebuilt the whole outlines reply each time it was asked, even for a copy it had just answered, and even when the outlines had not moved. The cost was not the curves: RPC1 c430's 897 outlines and Inferior c1178's 155 are Viking's own registered curves, drawn as they are and read in 0.01 s. It was turning 185,771 points into 7.6 MB of JSON, one number at a time, in the one process every other request waits on. Now the reply is kept as it was sent, keyed by what decides it, as a surface on disk is: the cell's annotations as Viking's StructureFreshness names them, and the moves applied to them (the summary's `positions`). A copy loaded again at positions the server has seen, such as a switch back to a Registration setting, is answered at once. An edit in Viking, another setting, or a manual move makes it anew. A cell with no stamp (an imported file) is made each time. Up to 128 MB of replies are kept, the least recently used going first; clearing cached renders in Settings clears them too.
- **The reply is also made in a third of the time when it is made.** Each outline is splined once, not five times (its area, equivalent diameter and centroid each splined it again), and the JSON is written by pydantic's encoder instead of Python's. The bytes are the same as before on c430 and c1178 (whole cell, `spline_points=8`, and one section).
- **Before → after**, c430 (7.63 MB, 3.34 MB gzipped). On this Mac, in the process: 0.45 s a request → 0.16 s the first time, 0.003 to 0.007 s after that and on a switch back. On production from this Mac through nginx, 2.17.5: 0.94 to 1.16 s to the first byte (1.47 to 1.69 s in all) for every request, on the held copy, after a switch and after a switch back alike → 2.17.6: 0.020 to 0.026 s to the first byte (0.55 s in all, now nginx compressing the reply and the link carrying it) on the switch back and on every later request; a copy's first request, which makes the reply, 0.39 s. Inferior c1178 on this Mac: 0.04 s, then 0.003 s. Tests: `tests/unit/test_closed_curve.py` (+5: the reply is the outlines and their measures, as Python's encoder wrote them; kept until the annotations or their positions change, and the first reply again on the way back; an unstamped cell made each time; the budget and Settings' clear; `measures` is the three properties). Docs: `docs/api.md`.

## 2.17.5 — 2026-10-20

### While corrections are paused, no volume is asked (2026-10-20)

- **While the correction service was paused because it could not be reached, cells of a volume loaded in the last ten minutes were still sent to it.** After the service cannot be reached (gRPC's own `UNAVAILABLE`, a missed deadline, or an `UNAVAILABLE` that names no volume), it is not asked again for 120 s. A volume whose correction sets had been asked for in the last ten minutes was asked anyway, because its sets still counted as fresh. Only volumes not loaded recently were refused: RC1 c476 was asked during the pause and RC2 was refused (measured on 2.17.2 while 2.17.3 was being built). Against a host that drops traffic, each such load waited out its deadline (120 s for Curve fit and Field + fit, 15 s for Residual field), and each failure pushed the end of the pause later. Now no volume is asked during the pause. A cell whose offsets are on disk is corrected from them, and its hover now says the service is not answering and the offsets are kept from before; before, it said nothing. A cell with nothing on disk is drawn uncorrected at once, as a cell of a volume not loaded recently already was. Holds on one cell (2.17.1) or one volume (2.17.3) are unchanged.
- **Before → after**: the test service behind a proxy that drops traffic, timeouts 2 s, RC1 loaded just before the pause. An RC1 cell with nothing on disk took 2.004 s and sent one `CorrectStructures` under Field + fit; now 0.000 s and nothing sent. Under Residual field it took 2.007 s and sent one `CorrectPoints`; now 0.000 s and nothing sent. Together they pushed the end of the pause 4.0 s later; now 0.0 s. RC1 c476's offsets from disk had no note; now the service's error is its note. Tests: `tests/unit/test_corrections.py` (+1, 2 cases: a proxy's `UNAVAILABLE`, and the service stopped; both fail on 2.17.4). Docs: `docs/user-guide.md`, `docs/deployment.md`.

## 2.17.4 — 2026-10-20

### A drag turns the view while cells come in (2026-10-20)

- **While a Restore or an opened workspace brought cells in, a plain left-drag turned nothing; zoom and pan worked.** A restore put the saved view back only after its last cell, and until then a fresh page's camera sat on its own orbit target at the origin, where a turn has nothing to turn about. In Orthographic view, which draws on both sides of the camera's plane, the cells showed from there and zoomed and panned (both work without a distance to the target), so only rotation looked broken. That is how the user's workspace of 2026-10-20 (c1178 and c16137) is saved, and its restores took 10 to 27 s on production. The saved view now goes up when the restore starts. At the end it is put back again only if the view was not moved meanwhile; it is checked again against the cells that came in, and the scene is framed instead when it does not see them. A restore with no saved view frames on the first thing that lands, as a first load does.
- **A first load's framing at its end threw away a view turned during the load**, as a restore's end did. Both now leave a view the person moved while the cells came in (a position, target, up or zoom that differs by more than a millionth of the camera's distance: `appcore.sameCamera`); an untouched view does not drift (0 µm over 301 frames, in both views). A first load whose first cell comes from a cache, inside the waiting points' 0.6 s, is now framed on that cell; before, it stayed at the origin until the load ended.
- **Before → after** (headless Chrome, real mouse, c943 with its build held at the gate; `tools/check_app.py --only orbit`, new, 9 checks, 5 fail on 2.17.3): a 120 px drag during the restore of an Orthographic session turned the view 0.0° of 60 → 59.5°; the saved view was up during the restore: no (the camera at the origin) → yes; the end of the restore left the turned view: no → 0.006 µm from it; a turn during a first load survived its end: no (30.4 µm away) → yes; an untouched restore still ends on the saved view. Tests: `tests/js/test_appcore.mjs` (+1: the user's saved view against the controls' every-frame recompute, a pixel of pan, 0.1° of turn, a wheel notch, a roll, Orthographic switched off, and a camera aimed at nothing). Docs: `docs/user-guide.md` (loading and Restore).

## 2.17.3 — 2026-10-20

### One volume's server dropping out no longer pauses corrections for every volume (2026-10-20)

- **When Viking's correction service could not reach one volume's own server, every volume's cells went uncorrected for two minutes.** The service says so with `UNAVAILABLE` and the volume's name: "volume mapping temporarily unavailable for 'cped': Error connecting to volume server after 4 attempts: http://v2223.host.s.uw.edu/Neitz/CPED/cped.xml Connection refused" (17:31 UTC; Inferior's `v2486` at 17:29), while RC1 answered 8 of 8 calls in the same minutes. Any `UNAVAILABLE` paused the whole service for 120 s, as if the service itself could not be reached. Now an `UNAVAILABLE` that names the volume asked about holds only that volume for those 120 s, in Curve fit and Field + fit together (`CorrectStructures` maps the volume's sections for both, so both fail while its server is down). The other volumes are still asked, and so is the same volume's Residual field (`CorrectPoints` samples positions mapped here). A cell of the held volume whose offsets are on disk is still corrected from them, and its status says why they are old. An `UNAVAILABLE` that names no volume, or another one, still pauses the whole service: what gRPC says when it cannot reach the service ("failed to connect to all addresses; … Connection refused", "errors resolving …", measured) never names one. A longer hold already on the volume is kept.
- **Before → after**, with the test service answering Inferior's `CorrectStructures` with that error, one provider: 2.17.2 paused every volume for 120 s (`paused_for` true for RC1, RC2 and Cped); an RC2 cell whose sets had not been asked for in the last ten minutes was refused with Inferior's error and drawn uncorrected, and a copy of any volume drawn uncorrected was not asked again until the pause ended. 2.17.3: only Inferior is held (Curve fit and Field + fit); an RC1 cell asked next is corrected (3 of 5 annotations moved), Inferior's Residual field still answers, and Inferior is asked again once its 120 s are over. The live service answered every volume at 17:59 UTC (Cped c3 in 8.4 s, c108 in 4.9 s, RC1 c476 in 8.1 s), so the outage was replayed on the test service, not the live one.
- The correction chip's hover now says that the service could not reach this volume's own server and that cells of other volumes are still corrected, not that the service could not be reached. Docs: `docs/user-guide.md`, `docs/deployment.md`. Tests: `tests/unit/test_corrections.py` (+5, 6 cases; 4 fail on 2.17.2): the volume named is held in both structure modes while RC1 is corrected; an `UNAVAILABLE` from a proxy or naming another volume still pauses the service; offsets on disk kept with the note, a longer hold not shortened, the volume asked again after its hold; a web load held and not asked again while the Residual field loads; `names_volume` reads only the service's own answer. The test service can publish more than one volume and fail one of them. `tests/js/test_regstatus.mjs` (+1 case).

## 2.17.2 — 2026-10-20

### A Registration switch and Update from Viking bring the rings with the surface (2026-10-20)

- **A Registration switch, a change of Correction and Update from Viking drew the cell's new copy, then asked for its outlines and skeleton one after another.** The rings and the skeleton came 0.4 to 2.0 s after the surface, on cells whose outlines take that long to read (production, Voxel remesh, the cell's transforms switched on and off with Show annotations and Show skeleton on). They are now asked for once the server has taken the surface's build, as a manual move's Apply has asked since 2.16.0 (`extrasEarly` from `setRegistration` and `refetchCell`), and are in hand before the build ends. Nothing is drawn from a copy the cell has left: a second switch made while the first one's outlines are still out draws the second copy's rings and keeps nothing of the first, and a draw already replaced asks for nothing.
- **The annotations were asked for twice during a switch.** With the surface off the screen while the new copy builds, the points that stand in for it asked for the annotations 0.6 s in, beside the switch's own request, whose answer waits for the surface before it reaches the cache. Every request for a cell's annotations now shares the one of its current load (`askNodes`, `appcore.sharedRequest`): one `/nodes` per load, answered or not, and a failure is asked again.
- **Before → after on production, a switch replayed in the page's order from the user's link, the transforms on and off in turn, both orders alternated in each run (medians of 4):** Inferior c4983 (61 outlines), the surface built afresh: switch to everything in hand 2.52 → 2.05 s, the surface itself at 2.06 and 2.05 s; served from the server's cache: 0.87 → 0.66 s (the surface 0.06 s later: the requests share the link with it). RPC2 c787 (185 outlines, 1.2 MB): 12.47 → 12.21 s. RPC1 c430 (over 620 outlines, 7.6 MB): 13.43 → 11.25 s, 2.0 s of rings after the surface → none (in these two runs the old order counts the annotations' 0.06 to 0.1 s after the surface, where the old page's stand-in points had mostly fetched them during the build); from the server's cache 2.65 → 2.32 s, where the outlines' own 1.7 to 2.2 s of reading is the floor. A cell drawn without its rings and skeleton (the default) gains nothing and makes no more requests than before.
- Tests: `tests/js/test_appcore.mjs` (+3: askers of one load share its request, answered or not, and a later load sends its own; an earlier load's request never handed to a later one or another cell; a failure fails every asker and is asked again); `tools/check_shifts.py --only regswitch` (new, 11; 2 of them fail on 2.17.1's front end: the new copy's annotations and outlines asked once each during the build, for the transforms switch and for Update from Viking; the rings drawn with the surface; a second switch while the first one's outlines are held draws only the second copy's rings, and none of the first is left in the caches).

## 2.17.1 — 2026-10-20

### A cell the correction service throws on no longer holds its whole volume (2026-10-20)

- **One cell's failure in Viking's section correction service held its whole volume for ten minutes.** When `CorrectStructures` threw on a request ("Exception was thrown by handler."), every cell of that volume was drawn uncorrected in that mode for 600 s after it, even when the service answered for them. Since 2.17.0 made Field + fit the default for the Neitz volumes, one failed Cped cell left every Cped cell loaded after it uncorrected. Now an exception in the service on one cell's request (gRPC `UNKNOWN`, `INTERNAL` or `INVALID_ARGUMENT`) holds that cell and mode for ten minutes. The volume and mode are held once two different cells of it fail in a row with no answer between, as every call did on RC1 on 2026-09-24 and on Cped while its volume server was down today. Any other error from the service still holds the volume at once, and an unreachable service still pauses every volume for two minutes. A held cell's own load is still answered from its copy without asking (`paused_for(volume, mode, structure)`).
- **Before → after**, Cped in one service at Field + fit with no correction on disk, 17:36 to 17:40 UTC: c1 failed; once the service answered a direct request for c3 again (87 and 99 s later), c3 and c108 were loaded through the service that had failed c1. 2.17.0: both drawn uncorrected, "not asked again for cped until 17:46 UTC", with 512 s of the volume's hold left. 2.17.1: c3 corrected (128 of 128 annotations moved, 6.2 s) and c108 (1 of 1, 4.9 s); only c1 held, 489 s left.
- **What failed on Cped c1 was Cped's volume server, not c1.** 2.17.0 reported that c1 alone made the service throw under Field + fit. Measured again from 17:26 to 17:40 UTC, c1 answered under Field + fit 23 of 31 times, and each of its 8 failures fell in a minute in which other Cped cells failed too (c3 every time; c108 and Curve fit whenever they were asked). Viking's service said why on some of those calls: `UNAVAILABLE` "volume mapping temporarily unavailable for 'cped'", its volume server `v2223.host.s.uw.edu:80` refusing connections (Inferior's, `v2486`, at 17:29). On most it threw the same `UNKNOWN` handler exception instead. Between 17:30 and 17:34 UTC Cped answered 12 of 32 requests, RC1 8 of 8.
- Docs: `docs/user-guide.md` and `docs/deployment.md` (what is held, and for how long). Tests: `tests/unit/test_corrections.py` (+3, and two rewritten; 5 of 5 fail on 2.17.0): a cell the service throws on leaves c1178 of its volume corrected, the held cell itself not asked again; a second cell failing in a row holds the volume; an answer between two failures, or a failure whose hold is over, keeps it asked; a `PERMISSION_DENIED` holds the volume at once. `CorrectionsError.code` carries the gRPC status's name, and the test service fails only the structures it is told to, with the code it is told to.

## 2.17.0 — 2026-10-20

### The Neitz lab's cells are corrected by Field + fit (2026-10-20)

- **A cell of a Neitz lab volume (Inferior, Nasal, Temporal, Cped) now loads with Viking's current section transforms on, Viking's section correction on and Correction = Field + fit** (the residual field, then the curve fit), the user's decision. The Marc lab's rabbit volumes (RC1, RC2, RPC1, RPC2) keep Curve fit, the default since 2026-09-24. A cell whose panel already chose a correction, and a saved session that names one, keep it. Nasal and Temporal have no transforms read here, so their cells stay at their stored positions, corrected.
- **Measured on 2026-10-20, Curve fit → Field + fit, annotations moved (median)**: Inferior c1178 2,176 → 2,431 of 2,819 (0.110 → 0.120 µm), c21768 1,461 → 1,566 of 1,680 (0.089 → 0.106 µm); Temporal c1 3,574 → 4,251 of 4,256 (0.041 µm both); Cped c3 122 → 128 of 255 (0.135 → 0.199 µm); Nasal c3 462 of 654 (0.129 µm; its Curve fit failed at that moment on Viking's side). A first fetch took 5 to 8 s in either mode, a cached one 0.2 to 0.6 s. One cell refused: Cped c1 (one annotation) makes the service throw under Field + fit ("Exception was thrown by handler") while Curve fit and Residual field answer, and the failure holds Field + fit for all of Cped for 600 s, as any mode's failure holds that mode. Other one- and two-annotation cells of all four volumes answered.
- **How**: each volume names its lab (`VolumeInfo.lab`); `SbfsemService(lab_corrections=...)` gives a lab its own default over `section_corrections`, asked through `default_correction(volume)` by a load, a held copy's check and the bulk SWC archive; the web app sets `{"Neitz": "all"}` (`SBFSEM_SECTION_CORRECTIONS_NEITZ`, default `all`). `/api/volumes` now says each volume's `lab`, `default_correction` and `default_registration`, and the page uses them: the Correction select falls back to the volume's default while the correction is off, and the Position shift's button reads **Apply field + fit** on a Neitz volume (**Apply curve fit** elsewhere) and applies that mode. The library and a bare `SbfsemService` are unchanged (off).
- **Manual moves are kept per setting**: moves made on a Neitz cell under the old default (`curvefit.1`) are kept, and come back when its Correction is set to Curve fit; under Field + fit the cell is drawn without them.
- Docs: `docs/user-guide.md` (the modes' defaults, Apply), `docs/api.md` (`/api/volumes`, the default per volume), `docs/deployment.md` (`SBFSEM_SECTION_CORRECTIONS_NEITZ`). Tests: `tests/unit/test_corrections.py` (+1: a lab's default mode, a named mode still wins, the copy tag), `tests/unit/test_web.py` (+1: the web app's defaults per volume in `/api/volumes`); `tests/js/test_compare.mjs` copies `regstatus.js`, which the shift panel now imports.

## 2.16.0 — 2026-10-20

### An Apply's rings and skeleton come during the build, and a finished build is heard of at once (2026-10-20)

- **The moved copy's annotations, outlines and skeleton were asked for one after another once its surface was drawn: 1.17 s more per Apply of Inferior c1178 on production** (0.11 s, 0.85 s of which 0.53 s was the server splining the outlines, then 0.25 s). None of them needs the surface. They are now asked for side by side as soon as the server has taken the build (`addToScene`'s `extrasEarly`, `fetchExtrasEarly`; `appcore.extrasWanted`, `startExtras`, `adoptExtras`), and are in hand about 2 s into a 5 to 7 s build. They are still drawn with the surface, in the same frame (the user's choice): until then the moves' own preview stands in, and the screen never shows two copies of the cell at once. Only what the cell's display draws is fetched (Show annotations, the Position shift layer's rings, the skeleton, the annotation wireframe), and what comes back for a copy since replaced is never kept or drawn. Asked any earlier, beside the render estimate or the build's start, the outlines held a Hybrid's estimate up from 0.17 to 1.05 s and its start from 0.15 to 1.2 s (both are Python work in the same service process), so they wait for the start's answer (`pollJob`'s new `onSubmitted`).
- **A build's end reached the page up to a second late.** The page polled 150 ms after the start, then every second, and the finished poll came 0.64 to 0.97 s after the build ended (production, 2.15.0). A poll may now wait for its job: `GET /api/mesh-jobs/{id}?wait=1` answers the moment the job is done, failed or cancelled, or after 1 s with the job as it is, and the page sends the next one at once. The wait is in the event loop and holds no thread (`MeshJobStore.wait`: the thread that ends the job wakes it through `call_soon_threadsafe`); the reply is put together in the thread pool as before. It costs the server what a poll a second did: 0.65 to 0.8 ms of CPU per followed job each second, against 0.55 to 1.25 ms (measured on the Mac's uvicorn, ten pollers for 20 s). Polling every 100 ms instead would have cost 7.6 ms and still answered up to 100 ms late. The page asks it to wait only when it came over HTTP/2 or HTTP/3 (production does): a waiting poll holds its connection, and over HTTP/1.1 (a local dev server) a browser has six per server, which a restore of six cells would fill. A server that answers at once is followed after the rest of the second, as before.
- **Before → after on production, c1178 at the app's defaults, an Apply replayed in the page's order from the user's own link, the time from the POST to the surface, rings, outlines and skeleton in hand beyond the server's own build** (2.15.0 → 2.16.0, medians): Voxel remesh 2.31 → 0.23 s (4 Applies each), Hybrid 2.32 → 0.60 s (3 and 4). Builds were 4.5 to 7.0 s before and 4.7 to 6.7 s after, not slower for the requests beside them.
- Docs: `docs/api.md` (`?wait=` on the job poll). Tests: `tests/unit/test_library_and_cache.py` (+2: a waiting poll woken as its job is done, fails or is cancelled, and let go at its timeout), `tests/unit/test_web.py` (+1: the route answers when the job ends, another request answers meanwhile, a still running job after about a second, more than 1 held to 1, the binary after a wait), `tests/js/test_polljob.mjs` (+6: `pollWaitFor`, the first poll at once, a held poll followed at once, one answered at once followed after the interval, a failed one retried after it, `onSubmitted`), `tests/js/test_appcore.mjs` (+4: what is fetched for which display, requests sent at once and a failure settling to null, nothing kept before the draw takes it, nothing kept for a replaced copy); `tools/check_shifts.py --only moves` (+3, 13: the moved copy's annotations and outlines asked once each during the build, the rings drawn with the surface, the polls waiting and no more than one a second).

## 2.15.0 — 2026-10-20

### A finished surface reaches the page as binary (2026-10-20)

- **The poll that carries a finished surface was mostly the server compressing JSON.** Measured on production for Inferior c1178 at the app's defaults (2.14.0): the reply is 12.7 MB of JSON for Voxel remesh and 16.9 MB for Hybrid, and nginx's gzip at level 5 spent about 0.6 s of CPU on it, longer than the link took to carry the 3.4 or 4.5 MB it made. The page now asks for the surface as binary (`Accept: application/x-sbfsem-mesh`): vertices as int32 steps of 1e-4 µm (the JSON's own fourth decimal), faces as uint32, `vertex_data` as float64, behind a JSON header with the job's status and the mesh's other fields (`sbfsem_web/mesh_binary.py`, read by `decodeMeshReply` in `api.js`). nginx compresses `/api/mesh-jobs/` at level 1, reading the app's reply 64 KB at a time (at nginx's default 4 KB it flushed the gzip stream on every read: 3.06 MB in 0.22 s on the server, against 2.99 MB in 0.15 s). The numbers are the JSON's to the bit: a vertex is read back as `k / 1e4` in float64, `-0.0` included, and stays float64 on the page; only the GPU buffer narrows it. The page no longer parses the JSON either (14 to 80 ms).
- **Every other client gets the JSON as before, byte for byte**, and so does a mesh the binary cannot carry exactly (a coordinate beyond ±214,748 µm or with more than four decimals, a non-finite number). A job that is still running, a failure and a measurement are JSON whatever was asked. The server holds a finished surface for its ten minutes as the binary (7.2 MB for c1178's Voxel remesh, against 12.7 MB of JSON text) and makes the JSON from it for a JSON client, the same bytes.
- **Before → after on production, the finished poll from the user's own link** (this Mac, through production's nginx, TLS and HTTP/2; median of 5): Voxel remesh 0.678 s for 3.43 MB → **0.222 s for 2.99 MB**; Hybrid 0.872 s for 4.50 MB → **0.290 s for 3.92 MB**. In Chrome, with the page's own code on production's reply: the 7.2 MB binary decodes in 4.6 ms (`JSON.parse` of the 12.7 MB JSON: 20.5 ms), and none of its 1.2 million numbers differs from the JSON's. (Measured on c1178's copies `curvefit.0` before and `all.1` after: the same surface sizes, 12.73 and 16.9 MB of JSON.) A JSON client takes about as long as before on this link (0.65 and 0.86 s) but gets its first byte later (0.46 and 0.61 s: its JSON is made from the held binary at each poll) and 22 % more bytes at level 1 (4.20 and 5.50 MB).
- Docs: `docs/api.md` (*A finished mesh as binary*). Tests: `tests/unit/test_mesh_binary.py` (new: the binary decodes to the JSON's numbers bit for bit, both zeros and int32's ends; a JSON client's bytes unchanged; `vertex_data`; an empty mesh; a running, failed or measurement job under the binary `Accept`; meshes that stay JSON), `tests/js/test_meshbinary.mjs` (new: the page's decoder against the server's replies, the `Accept` it sends, a JSON answer and one with no headers), `tests/unit/test_deploy_files.py` (+2: the poll's location at gzip level 1 with the type and the 64k read buffer, proxied as `location /`); `tools/check_renderer.py` (+4: a typed-array mesh draws, colors and picks as an Array one; without the `setIndex` fix the section throws).

## 2.14.0 — 2026-10-20

### Apply ties and smooths again only around the move (2026-10-20)

- **Smoothing again only around the move.** A build of a moved copy on the kept grid smooths (Taubin, Smooth passes) only the vertices within a few edges of a block whose triangles changed, on the faces around them, numbered in the sheet's own order; every other vertex keeps the place the last build smoothed it to (`unify.taubin_on_grid`). The neighbor sums run in vertex-index order, so the result is the whole sheet smoothed, to the bit (checked on c1178 for one annotation, a section and a soma disc, Voxel remesh and Hybrid: the final decimated surface identical). On c1178, 3,164 vertices smoothed again in 0.08 s instead of 459,301 in 0.56 s.
- **The last build's ties, where the move cannot change them.** The tie step's split into pieces (0.32 s on c1178) and its gap search (0.32 s) are skipped when the untied sheet still falls into the same pieces (the triangles around the changed blocks join the sheet around them in the same groups), the change is inside one piece, and every other piece is farther from it than **Tie gaps ≤** (`unify._reused_ties`); the ties' blocks the move left alone keep their triangles. Otherwise the gaps are searched afresh, as before. c1178's soma lies within 20 µm of its detached pieces, so a soma move searches again.
- **Decimation stays whole-cell.** It removes the cheapest edges across the whole cell until the Face cap is reached, so where it stops depends on every part of the cell; a local version would leave a seam of undecimated triangles around each move.
- **Before → after on Inferior c1178.** The build alone, in one process, on the kept grid (2.12.0's step → this one): Voxel remesh, one dendrite annotation 2.5 → 1.4 s, section 843 3.2 → 1.6 s, the soma disc 3.6 → 3.6 s (a third of the sheet changes: done whole); Hybrid, one dendrite annotation 3.5 → 1.6 s, the soma disc 3.2 → 2.6 s, section 843 3.4 → 1.7 s. Apply on the server (load and build in a build process) with no kept grid → now: Voxel remesh 6.6 → 3.0 s, 5.6 → 1.9 s, 6.0 → 5.1 s; Hybrid 7.3 → 3.3 s, 7.3 → 4.3 s, 7.5 → 3.3 s. A build that keeps its grid (move mode) pays about 0.24 s for what the next one reuses. The server log says how much was kept: "ties kept, 3,164 vertices smoothed again".
- Docs: user guide (*Only the part that moved is built again*). Tests: `tests/unit/test_grid_reuse.py` (+3, 14: local smoothing equals the whole sheet's to the bit, a second build from the first's, passes that reach past a block; the ties kept far from a gap and searched again near it or when the pieces join; c943 through the service, against the build that ties and smooths it all).

## 2.13.1 — 2026-10-20

### Viking's "Show in SBFSEM-tools" and a typed Location ID select the annotation (2026-10-20)

- **Opening a location from Viking's right-click menu, or typing a Location ID into the readout, moved the view there and left the annotation unmarked.** Both went through `gotoLocationId`, which flew the camera and filled the readout but never selected anything. Now an annotation of a loaded cell is selected: drawn yellow as a triple click draws it, named by the readout and the marker table, the arrow keys' reference, and it stays selected after the cell finishes drawing. With the cell's annotations hidden it is drawn as a ring at its radius. One on a cell that is not loaded is selected once the status line's *Load cN* button has loaded the cell.
- Tests: `tools/check_app.py --only locate` (new, 7 checks: `/open?volume=NeitzInferiorMonkey&cells=943&location=31431` on a fresh page, a typed Location ID, the same with the annotations hidden); against 2.13.0's front end 5 of them fail, against 2.13.1's none.

## 2.13.0 — 2026-10-20

### Select all annotations on a z-slice, and step it a section at a time (2026-10-20)

- **With an annotation selected (yellow, red or blue), the right-click menu offers *Select all annotations on z-slice S (same cell only)* and *Select all annotations on z-slice S*.** The first takes every annotation of the annotation's own cell on its section, the second every annotation of every loaded, shown cell of the volume on it; the rest are drawn in the annotation's color, and the annotation itself still carries the readout, the marker table's row, *Hide above / below* and the manual move. The items act on the ring the menu was opened on when it is one of the three (or of one's z-slice), else on the one the arrow keys act on; with a marker set they name its color. The one in force is ticked, and choosing it again, or `Esc`, goes back to the one annotation. The table's Z column says how many (*625 ×3*). A cell whose annotations are not shown gets a ring at each annotation's radius.
- **The arrow keys then step by section, not along the links:** to the next section above or below with annotations in the same scope, all of them. From the yellow z-slice `↑`/`↓` start a red or blue z-slice, a red or blue one steps on a section at a time and goes when it reaches the yellow's section, and `Shift` moves the yellow one, which stops at a marker's section and takes its place: z-slices meet and never pass. The ring that carries the readout after a step is the nearest in XY of the same cell. A marker that is one annotation still walks the links. On c1178 (Inferior Monkey): section 625 holds 3 of its annotations, `↑` makes a red z-slice of the 2 on 626, `↑` again the 2 on 627 (ΔZ +0.18 µm, +2 sec), `↓↓` brings it back to 625 where it goes; with c21768 loaded too, every loaded cell on section 1026 is 7 annotations of the two cells, all yellow.
- Tests: `tests/js/test_markers.mjs` (+6, 23: the next section each way, the ring that carries the readout, membership by cell and Location ID, a marker's step and merge, a new marker from the yellow z-slice and Flip Z, the yellow stopping at a marker); `tools/check_contextmenu.py --only zslice` (new, 15 checks with real right clicks and keys). Unchanged and green against the new front end: `check_contextmenu.py --only markers,markerthird,candidates` (54), `check_renderer.py` (66), `check_app.py` (181), `check_shifts.py --only focus,moves` (20).

## 2.12.1 — 2026-10-20

### A flared status message stays until it has been read (2026-10-20)

- **Open in Viking on a Mac sometimes said "ready" instead of "Viking client can only be run on Windows."** The busy tracker says "ready" when the last request in flight ends, whoever started it, and the freshness check a load sends as its surface lands (`POST /api/neurons/freshness`) was still in flight: in `check_app.py --only viking` it started 70 ms before the click and ended 8 ms after it, and its "ready" replaced the message before it showed. Every flared message had the same race (a registration or section correction not applied, a manual move refused, an annotation no longer in the cell). A flared message now keeps the status line against the tracker's "working…" and "ready" for 4 s, the flare's 2.4 s and a moment to read it; work still going on when the hold ends takes the line back, and an error or answer written over the flare meanwhile is left as it is (`appcore.StatusHold`).
- Tests: `tests/js/test_appcore.mjs` (+3: a request that began before the flare cannot wipe it, what the end of the hold gives back, a second flare); `tools/check_app.py --only viking` (+3, 29) holds a request open across the click so the race happens every run: on 2.12.0's front end 2 of its checks fail, on 2.12.1's none.

## 2.12.0 — 2026-10-20

### Apply builds again only where the move is; moves stay on their section; the skeleton follows (2026-10-20)

- **"Redo the portion of the render where the move is."** When move mode starts, the server keeps the whole-cell grid the cell's surface was contoured on (`GET .../grid-job`, built in the background when the surface came from a cache), and Apply builds the moved copy on that grid, contouring again only the blocks whose field the moves changed (`unify.GridState`, `grid_reuse`; a block's signature is the fingerprints of the leaves it evaluates, an outline raster's by the tiles the block reads and a cluster field's by the window of values and spline coefficients it reads). The result is exactly a full contour on that grid (tested to the bit), and the rest of the surface keeps its triangles: on Inferior c1178 99.9 % of the vertices stay where they were, where a fresh build moved all of them (its voxel follows the whole cell's area). Apply on the server, load and build in a build process, before → after: Voxel remesh, one dendrite annotation 7.8 → 4.8 s (2 of 2,454 blocks contoured), section 843's four 7.2 → 4.8 s; Hybrid, one dendrite annotation 8.9 → 5.3 s, a soma disc 8.5 → 5.6 s, section 843 10.9 → 5.3 s. A soma disc under Voxel remesh gains little (7.2 → 6.0 s): the soma is one field whose level is fitted to all of it, so all of it is contoured again, as it must be. The rest of a build (ties, smoothing, decimation, about 2 s on c1178) is still done for the whole cell. Grids are kept only for cells move mode was started on or that have moves, 512 MB at most (`SBFSEM_GRID_CACHE_MB`; c1178 holds 21 MB under Voxel remesh, 45 MB under Hybrid).
- **The cell stays on screen while Apply builds.** It used to leave the screen for the build; now the surface, rings and skeleton stay as they were and each is replaced as its new one arrives (`forgetDrawn(..., { keepDrawn })`).
- **A move stays on the annotation's section.** "The annotations move along the existing skeleton; they need to move on the plane that they are on": Xp, Yp and Zp stepped a whole section where the screen axis pointed within 60° of Z (Yp seen from the side), which carried the annotation along its own process. They now always move in the section plane, and where the axis points along Z (within about 14°) they move nothing and say to choose another axis; only Z, chosen as such, changes the section (`manualmoves.stepMove`, `IN_PLANE_MIN`).
- **The skeleton follows a move as it is made.** Its line through a moved annotation bends to where the move puts it at each step, before Apply (`manualmoves.pendingDeltas`, `nudgedSkeleton`, `Renderer3D.nudgeSkeleton`); after Apply it is the moved copy's own.
- Docs: user guide (*Moving an annotation by hand*: the axes, Applying, only the part that moved), API guide (`grid-job`, job kind `grid`), deployment guide (`SBFSEM_GRID_CACHE_MB`). Tests: `tests/unit/test_grid_reuse.py` (11: reuse equals a full contour on the grid, untouched blocks kept, a raster's and a cluster field's per-block fingerprints, the service keeps grids only when asked and within its cap), `test_web.py` (the grid job), `tests/js/test_manualmoves.mjs` (+2, and the axes: no view axis ever leaves the plane); `tools/check_shifts.py --only moveplane` (7: in the plane from the side, the skeleton bends, nothing leaves the screen during Apply).

## 2.11.0 — 2026-10-20

### One slice at a time in the markers' colors, Show in Viking, and moving a section by hand (2026-10-20)

- **One slice at a time: the plane fits its section.** It spanned the cell's whole XY box (+5 %), larger than the view when zoomed in; it is now the box of that section's annotations and the arrows drawn on it, with a 5 % margin (`shiftlayer.planeRect`), resized at each step. On Inferior c1178 the plane at section 1180 is 45 × 31 µm, at section 1114 (the dendrites' layer) 90 × 150 µm, against 215 × 220 µm before.
- **The slice in the markers' colors.** The arrows and rings of the plane's section are drawn in the selection's yellow at full strength, those above it in the red marker's color and those below in the blue one's (Flip Z swaps them), at **Off the plane** (new, 0.06 by default) of their opacity (`sliceTint`, a per-vertex alpha in the wide lines, `Renderer3D.setShiftSliceTint`); the plane's outline is yellow. With an annotation of the cell selected the plane's section turns pale yellow, so the selected one's own arrow (full yellow, wider, edged) stays apart, and the selection's fade is not laid over the slice. **Others when selected** now defaults to 0.06 too ("deemphasis ... to almost nothing"); cells that saved 0.2 keep it.
- **Show in Viking from the marker table:** a ↗ beside each red, yellow and blue row's Viking numbers opens that annotation in Viking (Open in Viking; Windows only). The table's cells are 4 px apart instead of 6, so it is 547 px wide and fits a 900 px window with the new button.
- **Moving a set by hand.** The move panel's **Move** row: *This one* (and annotations of the cell added or taken out with ⌘-click, Ctrl-click on Windows), *Its section* (every annotation on that section) or *Sections* from and to (*Red to blue* takes the markers' run). Each member gets the same step from its own earlier move (`manualmoves.membersOf`, `stepSet`, `setMoves`), the set is drawn as pale yellow rings (`Renderer3D.setMoveSet`) and counted, and *Undo these moves* takes it back. In the Manual moves list, a click on a section ticks every move on it and Shift-click ticks a run of rows.
- Docs: user guide (*Position shift*: the slice's plane and colors, the ↗; *Moving an annotation by hand*: sets, the list). Tests: `tests/js/test_shiftlayer.mjs` (+3: the plane's rect, the tint, what syncSlice hands over), `test_shifts.mjs`, `test_manualmoves.mjs` (+2: members, a set's step); `tools/check_shifts.py --only slicecolors` (5), `--only moveset` (8), `tools/check_contextmenu.py --only markers` (+2, the ↗).

## 2.11.0 — 2026-10-20

- **Smooth surface µm's hover movie showed the same picture at every value.** It framed the whole c1178 soma, 56 µm across, where the most the knob moves the surface (0.07 µm) is under a pixel. It now frames 3 µm of the soma's traced wall (Closed curve, Voxel 0.1 µm), where the pixel-wide grain is plain at 0 and gone at 0.5 µm; in that frame the creases per µm² fall 1.74 → 0.37 at 0.5.
- **Its help now says where it stops.** A vertex takes at most 20 passes, a length of about 6.7 of the mesh's edges, and past it every value builds the same surface: on Inferior c1178 about 0.65 µm under Closed curve at Voxel 0.1 µm, 0.8 µm under Hybrid at 0.1 µm, 1.3 µm under Closed curve at 0.25 µm (measured: at 0.1 µm, 0.75 to 2 all give creases 0.18 per µm² and move the surface at most 0.072 µm). Lifting the stop would not help: with none, 2 µm (195 passes) moved c1178's surface at most 0.11 µm, since Taubin takes out only grain a few edges long. Suggested: 0.25 to 0.5 at Voxel 0.1 µm, 0.5 to 1.25 at 0.25 µm; steps several sections tall are Smooth discs' and Smooth outlines' to average. No geometry changed.

## 2.10.0 — 2026-10-19

### Visualizations: a cell's whole look at one pick (2026-10-19)

- **The open cell's second line is now its Look dropdown** (it said the volume, the technique and the label; those are its hover and the volume tag's). A visualization is the whole look: every render option, the material and opacity, what is shown and every color, width and opacity of it, synapse types and Position shift included; not the cell's color, Z range, registration or manual moves. What it leaves out is the default (`visualizations.applied`). The dropdown names the visualization the cell shows exactly (the render compared as the build reads it), or *Custom look*.
- **Pre-filled from the workspaces saved on the server** (read 2026-10-19: 13 of James's, 2 of Crystal's, 2 in ALLofUS): James's Closed curve in clay with skeleton and rings (2026-10-16, without the Smooth discs and outlines those had, which move annotations), his Hybrid in glass colored by IPL depth (2026-09-18) and his faint surface with synapse ovals (2026-08-29); Crystal's skeleton and yellow rings with no surface (RC2, also ALLofUS); Becca's skeleton over the annotations, put together from her skeleton work (she has saved no workspace); and three emphases: Skeleton, Annotations, and Annotation error service (Position shift with original rings).
- **Save, delete, share, import.** *Save this cell's look as…* keeps a look in this browser (`sbfsem.visualizations.v1`, listed in Settings); *Manage, share and import…* deletes the ticked ones, saves them to a `.sbfsem-vis.json` file (only what differs from the defaults), imports such a file and applies one. A file from a newer page, or one that is not a visualization file, is refused with the reason; a render knob out of range in a file is clamped.
- **The right-click menu has the same list** (Visualizations): the chosen look is applied to that cell and, with more than one loaded, a button offers to apply it to all of them; any other click or Esc takes the offer away.
- Docs: user guide (*Visualizations: a cell's whole look at one pick*). Tests: `tests/js/test_visualizations.mjs` (6: the built-ins name only real keys and move no annotation, a save leaves out the cell's own, applying sets the whole look, matching, the store, the file); `tests/unit/test_settings_storage.py` covers the new key; `tools/check_app.py --only visualizations` (10).

### The selected annotation's shift arrow, and moving an annotation by hand (2026-10-19)

- **"I don't know which arrow is the movement of this annotation."** With Position shift on and an annotation selected, its own arrow is now drawn in the selection's yellow (the red marker's in red, the blue marker's in blue), 1.6 times as wide as the layer's, over a dark edge and over everything, with the ring where it was in the same color; whatever Only shifts above, the 2,000 cap or the slice leave out, at the layer's own exaggeration (`shiftlayer.focusMarks`, `Renderer3D.setShiftFocus`). Every other arrow and ring is drawn at **Others when selected** of its opacity (new, 0.2 by default; 0 hides them), until the selection goes (`setShiftDim`). An annotation the pair did not move has no arrow and dims nothing. The marker table has a **Shift µm** column. Measured on Inferior c1178 (428 arrows): the selected one is the only arrow at full strength; a closed curve's outline where it was is drawn in yellow beside the outline where it is.
- **Moving an annotation by hand, after Viking's correction (new).** Select an annotation and press `g` (or the right-click menu's *Move annotation … by hand*, or ✥ in the marker table): a panel under the marker table shows its X, Y, Z and section, how far it was moved, a drawing of the volume's X, Y and Z as the viewport sees them, the axis and the step. `←`/`→` move it along the axis (Shift ten steps, Alt a tenth), `1`–`7` choose X, Y, Z, Xp, Yp, Zp (the viewport's right, up and out of the screen) or Shift (along its own correction), `Enter` applies, `Esc` is done. X and Y move by the step (0.01 to 1 µm, 0.05 by default); Z by one section, since an annotation lies in its section's plane; a viewport axis moves in the section plane, or by a section where it points within 60° of Z. The chosen ring is drawn where the move puts it at once; Apply loads the cell again from the server's copy (no Viking request, 5.6 s on c1178) and everything is drawn from it, the selection and markers following their annotations.
- **Kept with the registration setting, never in Viking.** A cell keeps its moves under the Registration setting they were made under (`record.manualMoves`, by copy tag: `curvefit.1` is Curve fit with the current transforms on), in the page's saved state and its saved sessions, and with undo and redo; every load of the cell sends them (`api.setMovesProvider`), and the server applies the set of the setting it loads, after the correction, to a copy made from the plain one, named `curvefit.1.m<token>` (`sbfsem.core.moves`, `SbfsemService.load_neuron`, at most 4 such copies a cell). The mesh key's positions end `manual:<token>`, so a surface built with one set is never served for another, and the surfaces of earlier sets stay cached. `/nodes` gives a moved annotation `manual_shift_um` and `manual_dz`; Position shift's arrows still end where the correction drew it, and the readout's Viking numbers take a move back as they take the correction back.
- **The Manual moves section** of a cell lists its moves under its setting (ID, section, ΔX, ΔY, ΔZ; click an ID to select it and look at it), deletes one (✕), several (the boxes) or all (**Reset all**; the other settings' moves stay), applies the ones not yet in the render, and **Show moves** draws an arrow for each, from where the correction drew the annotation to where it was moved, at true scale.
- A fetch of a cell's annotations that was under way when the cell was loaded again is no longer kept for the new load (`nodesFor`): it put the old copy's annotations back in the cache.
- Docs: user guide (*Position shift* — the selected annotation's own arrow; *Moving an annotation by hand*; the keys), API guide (`manual_moves` in the load body, the node fields), CLAUDE.md's map. Tests: `tests/unit/test_manual_moves.py` (18), `test_web.py` (the load route), `tests/js/test_manualmoves.mjs` (10), `test_shiftlayer.mjs` (+4: the focused marks and the controller), `test_shifts.mjs`, `test_appcore.mjs`; `tools/check_shifts.py --only focus` (11) and `--only moves` (10).

## 2.9.0 — 2026-10-17

### Smoothing that moves no annotation; the two that do say so; tubes exact to their discs (2026-10-17)

- **The user, on reading Disc avg's help:** "If 'Disc avg' changes annotation locations before the render, then you have changed the shape of the cell", and only the volume's registration and the correction service should move an annotation. What they had asked for was the wireframe smoothed. After the numbers below they chose all three changes and dropped the parked "Disc avg 0.5 as the viewer's default", which stays dropped.
- **Disc avg and Path avg are now Smooth discs ×r and Smooth outlines µm**, and their help opens with "Moves the discs/the traced outlines before the surface is built (never in Viking)" and says how far. At 0.5, 72 to 91 % of a cell's discs move, a median 0.015 to 0.044 µm and 95 % under 0.10 to 0.20 µm (Inferior c1178, c16137, c943, Temporal c118); soma discs a median 0.16 to 0.24 µm, at most 0.77 µm; radii within 6 %. Smooth outlines at 0.25 µm moves a median outline point 0.019 µm (RC1 c410) to 0.070 µm (c1178's soma), 95 % under 0.13 to 0.33 µm, but 2 to 3 µm at a point where an outline changes shape sharply. For scale, the section correction moves c1178 a median 0.109 µm, at most 3.99 µm. Both stay 0 (off) in the library, the API and the app; the option keys (`curve_avg_discs`, `curve_avg_paths`) and every stored surface are unchanged.
- **Smooth surface µm (new, every method, off by default)** smooths the finished surface and moves no annotation: Closed curve's own surface step alone (`curve_average.smooth_surface`: volume-preserving Taubin passes per vertex over that length, none where a process is under 1.6 mesh edges in radius), after the technique's build and before MeshLab. Measured, the mean distance from the annotations the same within 0.003 µm and the volume within 0.4 %: Inferior c16137 under Closed curve at Voxel 0.1 µm, creases per µm² of surface 1.08 → 0.78 at 0.1, 0.51 at 0.25 and 0.32 at 0.5 (Smooth discs 1 reaches 0.45 by moving the discs), no vertex moving more than 0.02, 0.05 and 0.06 µm; c1178 under Hybrid at 0.1 µm 0.196 → 0.161 and 0.108; RC1 c410 under Hybrid at 0.25 µm 0.626 → 0.527 and 0.380; a build under a second to 3.5 s longer. It takes out fine grain, not steps in the tracing: c1178's traced soma keeps its plates, each several sections tall, even at 1 µm (its movie shows it). Under Voxel remesh it changes little (Passes already smooths that surface). The owner of each vertex is found among the 8 nearest disc centers and the outline points by k-d tree (an exact nearest-annotation search made RC1 c410's build 23.5 → 33 s; this one 23.6 → 24.3 to 25.8 s).
- **Exact tubes ° (new, off by default; Cylinder, Tube and Hybrid's discs under Voxel remesh and Ultraliser)** answers the third part of 2.7.0's *Why disc tubes come out fatter than traced outlines* in the builder. A pair of discs on a stretch running within that many degrees of straight through the sections is drawn as the hull of the two discs (`unify.DiscHull`, chosen by `unify.hull_pairs`), whose slice at each section is the disc exactly as annotated, instead of a round cone, whose end balls reach a radius past each section and stack up where discs are a section apart. Measured at 35° on eight cells under Voxel remesh: the tube through a steep disc falls from a median 1.16 to 1.73 times the disc's area to 0.98 to 1.26 (six cells with steep stretches); the mean distance from the annotations the same or up to 7 % smaller (Temporal c160 0.090 → 0.084 µm; c106 0.5 % larger); the volume up to 1.3 % smaller; but creases per µm² up to 3.6 times as many, a ring at each section where one hull meets the next (c160 0.10 → 0.37, c21768 0.15 → 0.32), most of which Smooth surface 0.5 takes back out (c21768 0.17, c118 0.16, against 0.15 and 0.13 with round cones). Not the default, because annotators draw a disc at a process's width (0.85 of the traced half-width at 212 outline-disc links), so the round cones' extra lands near the traced size on average: exact tubes are closer to the annotations, not necessarily to the cell. c1178's left process, the one in the user's screenshot, runs obliquely (none of its links within 35° of straight through), so it keeps its round cones.
- **Keys:** both new options are left out of the cache key at 0 (`UNKEYED_AT_OFF`), and Exact tubes out of a Closed curve key at any value (`READ_ONLY_BY`), so every stored surface is kept and `GEOMETRY_VERSION` stays 6.
- **Help movies:** Smooth surface (c1178's soma under Closed curve) and Exact tubes (c118's branch point under Voxel remesh) have movies of their own; the words under Smooth discs' and Smooth outlines' movies say the annotations move. An error banner names Exact tubes only when it is on.
- Docs: user guide (the table of what each method reads, *Why disc tubes come out fatter than traced outlines* with the Exact tubes table, *Averaging out the staircase* with Smooth surface), DEVIATIONS 5.15, API and backend guides, CLAUDE.md's map. Tests: `tests/unit/test_surface_smooth.py` (6), `tests/unit/test_exact_tubes.py` (4: each disc exact at its section, which pairs are hulls, a steep wobbly stretch 1.3 times its discs' area with round cones and 1.0 with hulls, the service draws them and moves no annotation, Convolution untouched); the schema, cache-key and node suites' counts and hashes re-pinned; `tools/check_curve_averaging.py` 24 of 24, `tools/check_app.py --only helpmovie,compare` 22 of 22. 1,553 tests.

## 2.8.1 — 2026-10-17

### The third marker from the yellow, and a triple click offers the ring under the pointer (2026-10-17)

- **After a red marker was stepped up, clicking the yellow ring and pressing ↓ moved the red one back instead of adding the blue one** (and the same with a blue marker and ↑). The arrows always moved the marker moved last; choosing the yellow again only drew it again. Clicking the yellow ring now (once, a triple click, or its row's dot in the marker table) makes the next ↑ or ↓ start a marker from the yellow: ↓ adds the blue one below it while the red stays, ↑ the red one beside it, in place of one already there (as a triple click on a ring above does). A click on a red or blue ring makes that one the marker the arrows move. Esc still takes the markers before the selection. `tools/check_contextmenu.py --only markerthird` repeats it with a triple and a single click: 4 of its checks fail on 2.8.0 (the three cases and the chooser below), none on 2.8.1, and the whole check, 112.
- **A triple click on a ring listed six annotations at a whole cell's zoom.** It offers every ring whose rim is within 6 px of the pointer, at most six, and at that zoom the rings of the neighboring sections are 2 to 5 px away: on Inferior c1178, 1 candidate 4 µm away, 4 at 15 µm, 6 at 30 and 60 µm, the clicked ring at 0 px and the others at 1.8 to 4.7. It now offers the rings about as near as the nearest (within 1.5 px of it, `markers.choosable`): 1 or 2 at every one of those zooms, a ring the clicked one overlaps included. A skeleton branch, a wireframe or a synapse type keeps its own 6 px reach.
- Docs: user guide (*Markers above and below*). Tests: `tests/js/test_markers.mjs` (17).

## 2.8.0 — 2026-10-16

### A Settings tab with debug tools (2026-10-16)

- **A new last tab, Settings, set apart at the far end of the tab row**, for when something looks wrong rather than for the work. The user asked for a "clear browser cache" option kept out of the normal places, with tools to clear cached renders and to restart the back end.
- **This page:** says whether the page is the build the server serves, from the `?v=` its own script was fetched with against `/api/build` (CLAUDE.md ground rule 7, the first question when a fix seems not to have arrived), and *Copy details* puts both builds, the browser and the window on the clipboard.
- **Cached files:** *Clear cached files and reload* asks the server for a `Clear-Site-Data: "cache"` answer (`POST /api/browser/clear-cache`), so the browser drops what it cached of the site and keeps its sessions, saved settings and sign-in; the session is saved first, and the page comes back to the tab with a note saying it was done.
- **Saved in this browser:** what the site keeps in the browser, by what it is for (saved sessions, render defaults, layout, colors, the Connectivity query, the dropdown style, the Viking programs seen), with sizes; *Reset the ticked items and reload* removes them from every tab of the site. A reset of the saved sessions stops this page from writing its session back on the way out (`session.stopSaving`), and the list is read again at the moment of the reset: the page saves its session just after the tab opens, so a first draft that used the list on screen removed 2 of 4 items (the browser check fails on it).
- **This server, for an admin** (`SBFSEM_ADMIN_USERS`: built-in or Viking names, any case; on production `jkuchen`; everyone where no sign-in is configured, and the machine's own loopback calls): its version, uptime, memory (the unit's cgroup on Linux), cells and surfaces held, disk cache, build processes and queue, and four buttons, each asking first and written to the journal with who pressed it: *Clear surfaces in memory*, *Clear surfaces in memory and on disk* (`SbfsemService.clear_renders`, `MeshDiskCache.clear`: cells stay loaded, a write in progress is left alone), *Replace build processes* (`recycle_build_workers`, the 2.6.5 pool replacement on request, not counted as one for memory) and *Restart the server*. Anyone else is told which account they are signed in as and where admins are named.
- **Restart:** `POST /api/admin/restart` answers 202, then stops the service the way systemd does (SIGTERM, so requests in flight finish and the shutdown runs), which exits with code 75; the unit now has `RestartForceExitStatus=75` and `SuccessExitStatus=75`, so it is started again and not logged as a failure, and a 30 s deadline ends a shutdown that hangs. A server systemd did not start answers 409 (nothing would bring it back), so a local checkout never offers it. The page saves its session, waits for a new `instance` from `/api/build` (up to 120 s) and reloads; other open pages get the "server restarted" banner they already had.
- **Guarded:** an action runs only for a body sent as `application/json` that names it (`{"confirm": "restart"}`), else 400 (FastAPI also reads a body with no type as JSON, and another site can send one of those without a preflight); the session cookies were already `SameSite=Lax`.
- Docs: user guide (*Settings: debug tools*), API guide (the routes), deployment guide (`SBFSEM_ADMIN_USERS`, the restart and the unit's exit codes), CLAUDE.md's map. Tests: `tests/unit/test_admin.py` (17), `tests/unit/test_settings_storage.py` (every key the scripts save is named in the list), `tests/js/test_settings.mjs` (9); `test_viking.py` places the four new routes with a body (none names a volume); `tools/check_app.py --only settings` (7 checks: 16 of 16 with `notice` and `twod` on a fresh server). 1,543 tests.
- The headings of 2.6.5, 2.7.0 and 2.7.1 said 2026-10-17, the UTC date; they were released on the evening of 2026-10-16, Pacific time, and now say so.

## 2.7.1 — 2026-10-16

### Kernel R/d stops at 2.5: a wider kernel loses the soma (2026-10-16)

- **Found while making the 2.7.0 help movies:** Convolution surface at Kernel R/d 3 and 4 lost Temporal c118's soma. The cause is the method's own calibration, not a fault in the code: the kernel's weight makes an infinitely long neurite keep its radius, so anything shorter than the kernel's reach sums to less than the iso-value, and a soma drawn as stacked discs is about as tall as it is wide. At mid-height of such a stack the field on its axis is 1.63 T at 2, 1.04 T at 2.5 and 0.78 T at 3, where nothing of it is left. Measured on four cells, the mean distance from the annotations' rims to the surface grows with R/d on every one (Temporal c118 0.177 µm at 1.6, 0.209 at 2, 0.389 at 2.5, 0.735 at 3, 1.225 at 4 with none of its 67 soma discs inside the surface; c106 0.261 to 1.029; Inferior c16137 0.274 to 0.859; c943 0.225 to 0.673).
- **The panel's Kernel R/d box stops at 2.5** (`convolution.MAX_PANEL_SUPPORT`), and its help says why, with the numbers: 1.6 is closer to the annotations than the paper's 2 on all four cells. A saved or typed value above 2.5 is clamped by the page as every out-of-range value is; the server still accepts up to 4, so a page open from before this release and a script keep working. No surface changes: the default stays 2 and nothing about how a value builds changed.
- Docs: user guide (the Convolution surface section, a table by R/d). Tests: `tests/unit/test_convolution_support.py` (3); `tests/js/test_renderoptions.mjs`'s clamp hash re-pinned.

## 2.7.0 — 2026-10-16

### Hybrid is one surface; Disc avg reaches the tubes; the render options' help shows what they do (2026-10-16)

- **Hybrid under Voxel remesh is one surface.** The user, on Inferior c1178 (130 traced outlines over its soma, 2,304 discs): "the top of the soma (when it switches back to disc) is a separate wireframe body". Hybrid built its two halves apart, the outlines as a Closed curve surface and the discs as a Cylinder build, and laid them side by side: five sheets, the disc-drawn soma top a closed shell sitting on the traced soma's flat top, the process leaving the soma's left end a tube laid over the traced stub, 6.1 % of the surface hidden inside the rest. Under Voxel remesh and Ultraliser the outline raster now joins the discs' whole-cell field (`unify._CurveLeaf`: the same box-smoothed occupancy whose 0.5 level the outline half was, read at every sample of the grid) and the union is contoured once. c1178 at Voxel 0.1 µm: 1 sheet, the same 829,988 triangles (the outline half's own count joins the Face cap), hidden surface 0.2 %, creases per µm² 0.370 → 0.196, the exterior distance from the annotations the same (0.244 → 0.245 µm), 6.7 → 7.0 s and a peak of 1,048 → 1,399 MB (at Voxel 0.25 µm: 519,644 triangles both, hidden 6.0 % → 0, 5.5 → 6.1 s, 1,024 → 1,258 MB). The grid is sized so the discs keep the voxel they had alone, and an automatic Fine voxel is never coarser than the Voxel size. The server's memory estimate for these builds already covers them (measured against it at 0.25, 0.1 and 0.05 µm). Under Convolution and the piece pathways Hybrid is as it was. Only a Hybrid build of a cell with traced outlines under those two pathways is built again (`facade._hybrid_token` joins its key); every other stored surface is kept and `GEOMETRY_VERSION` stays 6.
- **Why disc tubes come out fatter than traced outlines, measured.** The same user: "why the discs create such extra volume ... nearly 4x the area when it switches back to disc". c1178's left process is 4.5 to 5.2 µm² in cross-section where it is traced as outlines and 8 to 10.6 µm² in the Voxel remesh tube through its discs. Three parts: (1) the discs are not drawn too big: at 212 places in Temporal, Inferior, Cped and RC1 cells where an outline links to a disc within three sections, the disc's radius is 0.85 times the outline's half-width, at every tilt from 15° to 83°; (2) a disc records width, not height: traced in-plane processes are 0.48 to 0.70 times as tall as wide (1,209 strips, four volumes; c1178's stub 2.8 × 2.0 µm), part of it real and part the grazing sections annotators cannot trace, so nothing is squashed; (3) a tube is the union of round cones through every disc, so it takes the outer edge of every wobble of the centers and radii: a straight synthetic process with 0.1 µm and 15 % of wobble comes out 1.4 to 1.9 times its cross-section, c1178's thin discs a median 1.27 times their area, c16137's and Temporal c118's 1.13 times.
- **Disc avg ×r now reaches the tubes** of Cylinder, Tube and Hybrid's discs (`facade._averaged_discs`, the averaging Closed curve gives its circles): each disc is averaged with its neighbors along its chain of links over that many of its own radii before the tubes are built. At 0.5: c1178's median 1.27 → 1.11 times the disc's area (the upper quartile 1.85 → 1.61), c16137 1.13 → 1.09, c118 1.13 → 1.06, and the distance from the annotations stays the same or improves (c1178 0.177 → 0.168 µm, c16137 0.141 → 0.143, c118 0.108 → 0.111), in the same build time. Off (0) in the library, the API and the app, and left out of the key while 0: every surface is as it was. Envelope σ does not reach this; it smooths the soma-scale bodies only.
- **A render option's hover help shows what it does to the surface.** The user: "include a graphical movie representation of what the parameter is related to in the final wireframe/surface ... if this value gets bigger this happens ... and if this value gets smaller this happens (shown graphically)". Above the words (kept as they were, with their values for each volume and Voxel size), each of 28 options plays a short movie: one piece of Temporal c118 or Inferior c1178 built by the server's own code at a sweep of the option's values (every other option at the viewer's default for the technique it belongs to), drawn as the viewer draws a cell, surface and wireframe, from the smallest value to the largest and back. The frame's value is in its corner, the scale under it marks that frame and, with a dot, the value set now, the two ends are said in words, and each frame carries what its build measured: triangles, seconds and mean distance from the annotations. With reduce motion set, the frames are a still strip. Disc avg has a movie of its own under Closed curve. `tools/help_movies.py` makes them (29 movies, one WebP each, and `static/help/movies.json`: 2.4 MB, a movie fetched only when its help shows); `static/js/helpmovies.js` shows them; `tests/js/test_helpmovies.mjs` (10) and `tests/unit/test_help_movies.py`.
- Docs: user guide (*Hybrid is one surface*, *Why disc tubes come out fatter than traced outlines*, *Hover help that shows what a setting does*), DEVIATIONS 5.14, CLAUDE.md ground rules 12 and 13. Tests: `tests/unit/test_hybrid_field.py` (8); `tools/check_app.py --only helpmovie` (10) and `tools/check_curve_averaging.py` (17) in the browser. Found while making the movies, not changed: Convolution surface at Kernel R/d 3 and 4 loses Temporal c118's soma (mean distance from the annotations 0.20 µm at 2, 0.53 at 3, 0.86 at 4); its movie stops at 2.5.

## 2.6.5 — 2026-10-16

### Left clicks pick thin cells; a corrected copy is never shown an uncorrected surface; build workers give memory back (2026-10-16)

- **A left click picks what the right-click menu would list first.** The pick readout took the first thing one ray met, with three.js's line threshold of a micron. On Inferior c1178, 16 of 60 pixels the cell is drawn at picked nothing (the ray went between the thin processes' triangles). Beside c1178's skeleton line, 11 of 60 left clicks on c21768's surface, 14 to 25 px from the line in six views, picked c1178, because a micron is that many pixels at close zoom. Both buttons now read one list (`contextmenu.pickOf` over `Renderer3D.candidatesAt`): a surface within 4 px (`pickgeom.js`), a line within 6 px, only what the nearest opaque surface leaves in view. Now 60 of 60 and 60 of 60, empty space still picks nothing, and a ring is picked at its center, so the readout's Location ID is that ring's. A pointerdown costs 19.1 ms against the ray's 18.6 (1.84 million triangles, 400 points). `tools/check_contextmenu.py --only leftpick` repeats `thin` and `lines` with the left button: 2 of its 8 checks fail on 2.6.4.
- **The browser keys a surface on the moves its cell was given, not only on the copy asked for.** A copy's tag (`curvefit.1`) names the setting asked for. With Viking's Curve fit down (2026-10-16, 07:41 to 18:40 UTC), the cell stayed uncorrected under that same tag. Unloaded during the outage (✕ keeps a cell's surfaces for an Undo) and loaded again once the correction answered, it got its uncorrected surface back, drawn under corrected annotations. The same held for the Morphology numbers and the Skeleton check. A cell's summary now carries `positions`, the tokens of the moves applied (`null` at the stored positions), and the tab's mesh cache and `dataKey` include it. The server's disk mesh key was already right, so `GEOMETRY_VERSION` stays 6 and no surface on disk changes: the key's correction token is the offsets applied, and an uncorrected build files under the stored positions' key, which an `off` copy shares. A test pins that. Production's c1178 was corrected (2,180 of 2,435 annotations moved, median 0.109 µm, max 3.99).
- **A build process left holding too much memory is replaced.** A worker now trims its heap after every build (until now only the parent did) and reports what it still holds. One over `SBFSEM_MESH_RECYCLE_MB` (default 1024; 0 never) is let go with its pool: the builds running in that pool finish, its idle workers exit, and the next build goes to a new pool whose first worker starts at once. On production 2.6.4, one c1178 build (Voxel remesh, Face cap 3,400,000, Fine voxel 0.041 µm, 106 s) took the service from 1,122 MB to a 4,888 MB peak, and it stayed at 4,219 MB, its worker holding 2,812 MB. On 2.6.5 the same build (Fine voxel 0.042, 103 s) peaked at 3,888 MB. Its worker still held 2,125 MB with its heap trimmed, so the pool was replaced, and 5 s later the service was at 1,553 MB, its new worker at 139 MB: 2,666 MB less at rest than before. Trimming alone gave back about 690 MB of the 2,812. A new worker costs 1.2 s of spawn and imports on the server (1.0 s on an M4 Max), paid before the next build arrives, so that build runs as fast as in a worker that never left (0.32 s against 0.33 for c943 locally). A build that lost the race to a retired pool now goes to the new one, and a retired pool reports on its own queue until its last build ends.

## 2.6.4 — 2026-10-16

### A faster, lighter build-test-deploy loop (2026-10-16)

Developer tooling only: nothing a viewer sees changes.  The Mac this is built on (M4 Max, 16 cores, 48 GB) ran out of memory under parallel sessions, suites, dev servers and browser checks on 2026-10-16, and the 2.6.3 deploy ran past 10 minutes.  Measured on a quiet machine before and after.

- **The suite runs in parallel.** pytest-xdist is in the `dev` extra: `pytest -q -n 6 tests` runs the 1,506 tests in 67.9 s against 221.6 s serial (226.6 s before this change), at a peak of 7.1 GB across its 13 processes against 2.1 GB serial (`-n 3`: 83.7 s and 4.6 GB; `-n 8`: 59.3 s and 8.7 GB).  Serial still passes, and so do `--dist loadfile` and `worksteal`.
- **Each run gets its own cache, saves, cell-render and library directories** (`tests/conftest.py`; per worker under xdist), the cache seeded with a copy of `tests/cassettes` (7 MB).  Every app and service the tests made used the developer's own `~/.cache/sbfsem` and `~/.sbfsem`: one parallel run left 13 built meshes in the dev server's render cache, the workers shared that cache and its pruning, and the bulk SWC export tests passed only because the developer's cache held c943 from Viking (with an empty one, `test_bulk_swc_export` and the bulk archive header test fail: they fail on any other machine, and on the server).
- **`deploy/redeploy.sh` tests with `-n 6`** when xdist is installed (`SBFSEM_TEST_WORKERS` to change it), and prints how long each step took.
- **A code-only deploy reinstalls only the package.** `bootstrap.sh --quick`, which `redeploy.sh` now asks for, compares an install stamp of the synced tree (`deploy/install_stamp.py`: `pyproject.toml` without its version line, the unit, both nginx configs, `bootstrap.sh` and itself) with the one its last full run left in the venv.  The same: `pip install --no-deps --no-build-isolation .` and a restart, without the system-package check, the dependency resolution, the MeshLab proof, the unit and nginx installs or an nginx restart.  Different or missing: the full run, which writes the stamp only once the service answers.  A new directory (ground rule 10 edits the unit and `bootstrap.sh`) or dependency always gets the full run; `--full` forces it.  Install step: 13 s for the full run (the first 2.6.4 deploy, which had no stamp), 5 s quick (the next three).
- **The server's cells are re-loaded 4 at a time** (`--restore-jobs N`; `--no-restore` skips it, and open browsers restore their own).  It pays when cells wait on Viking and costs a little when they do not.  At 2.6.3 the 343 cells took 604 s one by one, because Viking's correction grids had changed that evening and every cell asked for its section correction again.  On the first 2.6.4 deploy 57 of 342 cells asked Viking (50 on RC2, whose grid changed at 22:49 UTC), at 7 to 19 s each: 167 s.  With all 342 from the disk cache, one by one took 51.1 s and 4 at a time 67.1 s, measured on two deploys of the same tree minutes apart: a load from the cache is Python work in the one service process, so four at once share it (median 0.29 s a cell against 0.07 s).
- **Deploys of 2.6.4**, all on the server's 342 cells: 257 s with the tests (checks 69 s, snapshot 3 s, sync 2 s, full install 13 s, restore 170 s with 57 cells asking Viking); then without tests and with the quick install, 89 s (restore 79 s, 45 cells asking Viking again), 63 s (restore one by one, 53 s, all from disk) and 79 s (restore 69 s, all from disk).  The 2.6.3 deploy ran 627 s on the server alone (bootstrap from 22:42:01 UTC, the last cell back at 22:52:28), its tests apart.
- **Browser checks leave nothing running.** The shared launcher (`Chrome` in `tools/check_dropdowns.py`, used by every `tools/check_*.py`) registers each headless Chrome for Testing until it is closed; `atexit` closes what is left, and SIGTERM and SIGHUP unwind as Ctrl-C does.  Six endings with two browsers each (an exception, `sys.exit`, a `finally` that closed one, SIGTERM, SIGHUP, Ctrl-C to the process group): the old launcher left 9 browsers running (about 222 MB and 7 processes each) and 11 profiles; now none.
- **`tools/cleanup_dev.sh`** lists headless Chrome for Testing, the check profiles no browser uses, and uvicorn servers on ports 8765 to 8799 with their working directory, memory and age; `--kill` stops them (SIGTERM, then SIGKILL after 5 s) and deletes the profiles.  It found and stopped the 9 browsers above in 2 s.  Anything else listening on those ports is shown and left alone.
- **The local dev servers build in 2 child processes behind a 3,000 MB gate** (`.claude/launch.json`: `SBFSEM_MESH_WORKERS=2`, `SBFSEM_MESH_PROCESSES=2`, `SBFSEM_MESH_MEMORY_MB=3000`, through `/usr/bin/env`).  The unset defaults were already one build at a time in the server process behind 1,700 MB; the gate limits what builds together, not how large one build gets, so CLAUDE.md now says 10-million-triangle builds and measurements belong on a server.

## 2.6.3 — 2026-10-16

### Position shift: outliers are not exaggerated past Full color at (2026-10-16)

- **Only a shift up to the legend's *Full color at* is exaggerated; past it the arrow grows at true scale.** An outlier is drawn as the arrow of Full color at plus its true excess, so it is no longer k times its own length: on Inferior c1178 at Auto ×13 (Full color at 0.56 µm, the 95th percentile), the few 1.4 to 4 µm section corrections ran to the 30 µm cap and across the view; a 3.99 µm one is now 10.7 µm and a 1.4 µm one 8.1 µm, against 7.3 µm for a shift at Full color at. A longer shift is still a longer arrow (and never shorter than it is), the 10 % cap and the true-length rule for a shift longer than the cap are unchanged, and every number shown is still the true shift. *Full color at* typed higher brings the exaggeration back to longer shifts. `shifts.drawnLength` / `markScales` take it; a test pins the lengths and that they stay monotone.

## 2.6.2 — 2026-10-16

### The viewer joins a Closed curve cell's bodies by default (2026-10-16)

- **Join bodies µm starts at 5 µm in the viewer** (the user's choice; it was 0), so a Closed curve cell is one body unless its pieces are farther apart than that. At the 0.25 µm Voxel the app picks for traced cells, Inferior c1178 goes from 44 bodies to 1 and RPC1 c2628 from 70 to 1; Inferior c16137 at 0.25 µm from 98 to 2 (the app draws it at 0.05 µm, where it is one body already). The ties are surface no annotation drew: c16137's unsupported surface moves from 0.064 to 0.108 µm from the annotations. Set it to 0 to see the bodies as the grid makes them. The library and the API keep 0 (ground rule 1); a cell whose render was saved with 0 keeps it until *Defaults to selected* or *to all*.
- **Every stored surface of the other methods is still the one served.** The viewer sends every option with every build, so the outline methods' own options (Join bodies, Curve links, Disc avg; and Path avg and the two bridges outside Closed curve and Hybrid) are now left out of the cache key of a method that does not read them, at any value: a Cylinder or Voxel remesh build asked for with Join bodies at 5 µm has the key it had (a test pins it). Closed curve surfaces are built again once, joined.
- **The cost chip counts what joining adds**: about 260 bytes a triangle of build memory and a third more time (the render estimate's `JOIN_BYTES_PER_TRIANGLE`, from the measured 185 to 356 on c1178 and c2628), so the per-cell Voxel size that fits accounts for it; the browser's rough time for Join bodies is a third more, not four fifths (the ties got faster in 2.6.0's review). Hybrid, which does not join, pays nothing.

## 2.6.1 — 2026-10-16

### Render compare: the text under the grid no longer resizes the panes (2026-10-16)

The one thing the reviewers of 2.5.2 left open (2.6.0 did not touch it), as the user chose it from three options (a row of its own, one line with an ellipsis, an overlay on the panes). **The summary of a run and the status line under Render compare's grid are one line high whatever they say**, so a Compare starting, ending or failing never moves the panes. They had been two more items of the toolbar's wrapping row (Cell, Panes, Compare, Sync cameras, Error), so a long text wrapped the whole row, and each wrapped line took 29 px from the grid and rescaled every render in it. Measured on c943 with 4 panes in a window 900 px tall (the grid's height, `tools/check_app.py comparetoolbar`, a fresh server): at 900 px wide 730.9 px idle, 701.5 with a note or after a Compare, 672.1 while three panes built, 668.1 after four failures and 598.6 under a long server message; at 1440 px 766.2, 766.2, 736.8, 732.8 and 698.0; at 1920 px 766.2 in every state but the long message (715.4). Now each width has one height in every state: 700.7 px at 900 and 1000 px wide, 736.0 at 1140 to 1440, 766.2 from 1639 px up.

- **One item, one line.** `.compare-texts` (22 px) holds both texts, never wraps (`nowrap`, an ellipsis) and gives the whole of what it cuts as each text's tooltip, the way the top bar's status line already does (`app.js`, a `MutationObserver` on each). The summary keeps its words, the clock at their end; the status line gives way first.
- **Where it sits.** Beside the controls where 760 px are left for it (a window of 1,639 px or more with c943 loaded and the cell list at its usual width); under the controls, in a row of its own, below that, empty when nothing is said. The row's `flex-basis`, not its words, decides where the toolbar breaks, so a window breaks at the same width idle and running. The price is one line (30 px, 4 % of the grid) below that width, even when idle; a wider window pays nothing. At 900 px the row is 570 px: a run of three panes at once reads in full with its clock, and so do the status after a finished Compare and a note; the status line of that run and a failure of every pane are cut (the whole text is on hover).
- **"Compare" is as wide as "Stop all".** The button was 66.3 px and became 76.8 as a Compare started, which moved Sync cameras and Error by 10 px and, in a window within 10 px of the width where the text moves, swapped the two layouts as a run began (the new check, with the `min-width` taken out, finds it at 1,631 px). The button has a `min-width` of 6em (84 px) now.
- Tests: the new `comparetoolbar` section of `tools/check_app.py`, 9 checks in 12 s. The grid and the first pane's render are one height at 480, 700, 900, 1000, 1140, 1440 and 1920 px and at the two widths either side of the layout's edge, in the dark and the light theme, idle, with a note, running (the clock at 3 s, 41 s, 1 min 05 s and 12 min 05 s), done, failed in every pane, and with a 400-character message; where the text sits; the bar and the controls are one height and one place in every state; no text past the toolbar's edge; what reads in full at 900 and 1140 px; the tooltip is the text. Against 2.5.2 it passes 3 and fails 6; each of three mutants (no `min-width`, texts allowed to wrap, no tooltips) fails 3, 4 and 1. The page's "cells open on this server" offer (47 to 52 px over the view while it is up) is dismissed by the check: a fresh server has none on a script's first page and one on its second, which moved the grid by itself.
- Not changed: the *Position shift* row above the toolbar appears and goes when a pane's shift box is ticked, which resizes the grid by its own height; that is the user's own click, not a run's progress. No surface, mesh id or export changes.

## 2.6.0 — 2026-10-16

### Markers above and below the selection: red, yellow and blue, with a table of the three (2026-10-16)

- **The selected annotation is drawn yellow and is the reference; a second annotation of the same volume marked above it is red, one below it blue.** A triple click on an annotation above the yellow one makes it the red marker, below it the blue one (in place of a marker already there); one on the yellow one's own section becomes the yellow one, the markers staying. *Above* is the top of the upright view, as for *Hide above*: higher sections unless Flip Z is on, and turning Flip Z swaps the two colors. There was one light blue "second" marker since 2026-09-22.
- **`↑` / `↓` step the marker moved last** along the cell's links (with none, the first press starts one: red going up, blue going down); `Shift` moves the yellow one. A marker stepped onto the yellow annotation goes; one that passes it another way changes color when it is the only marker, and stops when the other side is taken, saying so. Moving the yellow one keeps the markers on their sides, merges one it lands on, and stops where a marker would end level with it.
- **A table under the readout, whenever something is selected**: a row for each marker that is set, red, yellow and blue top to bottom, each in its marker's color (cyan, pink and orange on cells of those colors), with the cell, the Location ID and the Viking X Y Z (a click copies either, naming the volume), the section, ΔZ in sections and in µm, and the straight-line distance from the yellow annotation in the volume. Each row's ▲ ▼ step its marker and its dot draws the ring again as when it was chosen (and makes it the one the arrows move); its ✕ removes it (on the yellow row, deselects). A selected branch, wireframe or synapse type gets a yellow row naming the annotation nearest the point.
- **The readout over the render names the marker chosen last.** It went back to the yellow annotation after a triple click chose another ring, so the click seemed to answer with the wrong Location ID and section (the reported "not positive that it's returning the same location IDs"). A selected annotation's Viking X Y Z now takes back its own section correction (on c1178 no annotation's nearest neighbor differed, so this changed no number there).
- **Hide above goes by the red marker and Hide below by the blue one**, the yellow annotation standing in for a marker that is not set, with *Hide above red and below blue (keep D µm)* for the slab between; with no marker the items read as before. `Esc` takes the marker moved last, then the other, then the selection.
- Checks: `tests/js/test_markers.mjs` (16: which a chosen or stepped annotation becomes, crossings, Flip Z, the hide levels, the rows), `tools/check_contextmenu.py --only markers` (13, real triple clicks on c1178: the colors and rows, a copy, a row's ▲, `↓`, the menu, `Esc`, and the table inside the render at 900 to 1920 px), `tools/check_renderer.py` (the red ring settles and replays like the others).

### The Voxel size is typed, estimated before building, and fitted to each cell (2026-10-16)

- **The Voxel size of Closed curve and Hybrid is typed as well as picked**: a box takes any size from 0.005 µm (the Temporal and Nasal volumes' native pixel; MATLAB rasterized at 0.01) to 1 µm, with the named sizes one click away in the list beside it (in the page on Windows and Linux, never a native popup). Enter or leaving the box sets it, Escape or anything it cannot read puts the size back, ArrowUp/ArrowDown step through the named sizes, and a saved size the list does not name shows as it is. Render compare's pane form is the same control.
- **The cost chip says what the build will cost before it is built**, from the server (`GET .../render-estimate`): triangles, the server's memory (the build plus sending it) and production seconds — "≈ 9.6 M tri · 6.1 GB · 1 min 44 s" for RPC1 c2628 at 0.025 µm (production measured 109 s). It turns amber past 60 s, past half the memory the server gives builds or past 512 MB in the tab, and red past a limit, naming the finest size that fits; the reasoning, cell by cell, is on a 2 s hover. It is asked once typing stops, once per cell and setting. The rough chip of the other methods now turns amber at 60 s too.
- **A size too fine for a cell is built at the finest size that fits that cell**, on Apply, Apply to all, a load, Copy to and in Render compare, so cells of different sizes end up at different sizes; a notice names each cell, its size and why (RPC1 c2628 asked for 0.012 µm: 445 M raster voxels and 25 M voxel faces, built at 0.021 µm, the server's own finest; Inferior c943 beside it kept 0.012). The server's refusal after rasterizing stays as the backstop.
- **The page has a limit of its own**: Chrome cannot parse a response longer than V8's longest string (536,870,888 characters; Chrome 148 parsed 536 MB and failed at 537 MB). Inferior c16137 at 0.011 µm would be about 8.3 M voxel faces, under the server's 10 M, and about 577 MB of text no tab can read; the estimate holds the page to 500 MB.
- **Measured** against builds of RPC1 c2628, Inferior c16137 and RC1 c410 from 0.1 to 0.012 µm: voxel faces within −2.1 to 0 %, raster voxels exact, triangles −7 to +9 %, build peak −2 to +36 % (it leans high), page text −2 to +16 %. The faces are scaled from one raster of the cell at 0.1 µm (in-plane as 1/size, between sections as 1/size²: exact to 0.1 % between sections) or from its last build. The first estimate of a cell takes 3–5 s and is started ahead in the background; the next ones 0.04–0.12 s.
- **Fine voxel**: the mesh says which voxel the whole-cell grid used and why (`voxel2_um`, `voxel2_coarsened`, `voxel2_reason`: floor, faces, work), and a typed Fine voxel that came out coarser is announced. A typed Fine voxel is honored down to 0.02 µm (it was 0.04, which stays the automatic one's floor, so no surface built without a typed voxel changes; a typed one under 0.04 has a cache key of its own, and `GEOMETRY_VERSION` stays 6). Temporal c106 at 0.02 µm with no cap: 1.96 M raw triangles, 947 MB; Inferior c1178 stays at 0.0414 µm, the 10 M raw budget's, whatever is typed.
- **What a review of it found, fixed before release:** the estimate makes at most one reference raster at a time in the whole server (bounded at 20 million voxels, under 0.7 GB; three cells asked at once peaked 442 MB where three at once would be about 800), and a cell that cannot have one yet gets an approximate or pending answer at once and the chip refines itself; a capped reference is kept rather than made again on every estimate; a size applied while an estimate is still out is no longer replaced when that answer arrives; a Voxel box being typed into is left alone; the build gate prices a Fine voxel typed under 0.04 µm (Temporal c106 at 0.02: 931 MB, it was counted as 511); the new cache-key marker for a typed Fine voxel is given only to builds on the whole-cell grid; an estimate asked for under 0.005 µm is refused with a 422.
- API: `GET /api/neurons/{source}/{id}/render-estimate`. MeshDTO gains the four `voxel2_*` fields. `closed_curve.curve_voxel_counter`, `finest_fitting_pixel`, `surface_fitting_pixel` and `CurveVolume.exposed`. Docs: API guide, user guide (Voxel size). Checks: `tools/check_app.py --only voxel,fine` (21), `tests/js/test_renderestimate.mjs` (11).

### Position shift: thicker arrows with a legend, help in tooltips with drawings, no transforms switch where a volume has none (2026-10-16)

- **The arrows are drawn 2.5 px wide, with a filled head and a tail that fades**, where they were one-pixel lines (WebGL ignores a line's width). Each segment is two triangles spread across the screen by a vertex shader (`fatlines.js`), and the head is a triangle a set number of pixels long that shrinks on an arrow short on the screen. **Arrow width** (1 to 8 px) is in the cell panel and in Render compare's strip. Measured across one shaft looking down Z on c1178: 3 px at 2.5, 7 px at 6, 2 px at 1. Slice windows, the inverted viewport's colors, opacity, Through the surface, Flip Z, the orthographic camera, volume offsets, the Z range, the right-click menu and Render compare's panes all work as before; the menu measures an arrow by its drawn shaft and head (it measured to barbs the wide arrows no longer draw, so a click on empty background beside a long arrow listed it).
- **A legend** sits in the render's bottom-right corner while a cell draws arrows, and top-right in each Render compare pane that does. It shows the pair and its two positions (`registered → corrected`), the exaggeration, and then the Magnitude ramp with ticks in µm (`≥0.56 µm` when some moves are longer than Full color at), the Direction wheel (+X right, +Y up, in the volume's X and Y) or the cell color. It follows the inverted viewport, moves up when the chip below it wraps, and is not shown when no arrow is drawn (Arrows off, or every move under Only shifts above).
- **No standing text in Registration and Position shift.** Each switch has a small chip (`56 moved`, `unavailable`, `427 shown`) whose 2 s hover holds the facts the panel used to print. For `unavailable` it gives the reason in plain words with the server's own message last: `NOT_FOUND no VikingXML endpoint` reads "Viking's corrections service answered that it has no Curve fit for this volume…". Render compare's strip has the same chip.
- **Help with drawings:** a help pop can draw a figure above its words (`data-help-figure`, built from constants and colored by the theme). There are twelve: the transforms switch (a section re-aligned, its annotation carried with it), the section correction (a process zig-zagging across sections pulled onto its path) and its three modes, Between (stored → registered → corrected), an arrow and Exaggerate, Original rings, Only shifts above, One slice at a time, the color modes and Full color at, and Through the surface. The section correction's help now says what the corrections service gives, and ends with what it did for this cell, read from the cell's own status.
- **"Viking's current section transforms" is shown only for volumes whose transforms are read here.** `/api/volumes` now says `has_transforms`: Inferior Monkey, Cped, RPC1 and RPC2 have them; RC1, RC2, Nasal and Temporal do not. On those four the Position shift offers the section correction alone. A session saved with the switch on still loads, at the stored positions. Library and API defaults are unchanged.
- Checks: `tools/check_shifts.py` (46) and the node suites `test_fatlines`, `test_helpfigures`, `test_shiftlegend`, `test_regstatus`.

### Closed curve volume: long links curve, separate bodies join, and the bridges are options (2026-10-16)

- **Curve links µm** (0 to 20, off by default; Closed curve) draws a link whose discs are at least that far apart as a curve through the chain instead of a straight band. A link drawn straight was a band from one disc to the next, and a chain of long links a zigzag with a corner at every disc (Inferior c1178 has 211 links of 1 µm or more, and three that cross 22 untraced sections 2.4 to 5.4 µm sideways). The curve is a cubic through both disc centers with Catmull-Rom tangents (chordal knots, in 3D) read from the chain on each side. It stays straight at a chain's end, a branch or a turn over 90°, never bends tighter than the process's radius, stays within a quarter of its length of the chord, and never turns back in Z, so each section still gets the part of the run within one section of its plane. Measured at 1 µm: c1178, 163 links curve and the median turn at their discs goes 29.1° → 12.7°, the largest departure from a chord 0.58 µm; c16137, 33 links, 17.6° → 8.7° (its zigzags are links of 0.5 to 1 µm: at 0.5 µm, 395 links, 23.1° → 7.3°). The surface moves only along those links (c1178 at 0.1 µm keeps 0.085 µm mean deviation; creases 0.844 → 0.846), for about a second.
- **Join bodies µm** (0 to 20, off by default; Closed curve) joins separate bodies of the voxel grid up to that far apart with ties along the shortest crossing, drawn into the grid and contoured again only where they reach. At the 0.25 µm the app picks for traced cells nearly every gap is a process the grid's smoothing removed, not a skipped section (c1178: 42 of 43 gaps; c2628: 61 of 69 plus 8 specks), so the bridges cannot close them. Measured at 0.25 µm, 5 µm: c1178 44 → 1 body (58 ties, about 1.5 s); RPC1 c2628 70 → 1, mean deviation 0.354 → 0.272 µm; c16137 98 → 2, its unsupported surface 0.064 → 0.108 µm (the ties are surface no annotation drew). At 0.1 µm, 2 µm: c2628 94 → 1 (397 ties, about 2.5 s, peak 741 → 1,259 MB; at 0.025 µm, 11 million triangles, 3,231 → 5,835 MB); the build gate prices it at 35 bytes a raster voxel, up to 4,940 MB (following the surface limit). The inner sheet of a cavity is never tied (it was tied out through the wall), ties are chosen within one run of sections (a tie refused across two runs no longer hid a valid one), the gap search is bounded by the reach (two 205,000-vertex bodies: 5.1 → 0.34 s), and the tiles a tie needs are allocated once (each tie had copied every tile: c2628 at 0.025 µm peaked at 10 GB).
- **Bridge skips µm and Follow links µm** (More menu; Closed curve and Hybrid) are the constants 1 µm and 5 µm made options: how wide a skip between traced sections is interpolated, and how much Z a link is followed across skipped sections (a link to the next section is always drawn, discs and traced outlines alike: at 0 a disc-traced dendrite fell into beads).
- **Every surface is as it was:** 15 of 15 Closed curve and Hybrid surfaces of c943, the cone fixture, c16137, c1178 and c2628 hash the same with the options off or at their defaults, and the options are left out of the cache key there, so `GEOMETRY_VERSION` stays 6.
- API: the four keys on `mesh`, `mesh-job`, the exports and the render-prefs bodies, and in each Render compare pane. Docs: user guide (*Long links, separate bodies and skipped sections*), DEVIATIONS 5.13, backend and API guides, two rejected ideas. Checks: `tools/check_curve_links.py` (42), `tests/unit/test_curve_links.py` (22).

## 2.5.2 — 2026-10-16

### Render compare's header, popovers and keyboard: the front-end lows of the layers review (2026-10-16)

The nine findings of the layers review that 2.4.2 left open. Each has a check that fails on 2.5.1 and passes now: a node test (`tests/js/test_compare.mjs`, 154 cases; 33 mutants, each taking one fix out by hand, are all caught) or one of three new sections of `tools/check_app.py` (`compare`, `comparekeys`, `comparestate`: real mouse and key events, 30 checks in 30 s; against 2.5.0, whose front end 2.5.1 shares, the same script passes 8 and fails 23). Two reviewers who had not written the change read it before it shipped and found defects in its first draft (names cut to nothing in a crowded header, a failure's banner still missing on four paths, the rings of a load wiped by a failure on the same load, a re-shown banner naming the wrong layer, a stale line after the grid is put back mid-run, panes out of step after a restore, an unreadable red dot in the dark theme); they are fixed below. No surface, mesh id or export changes.

- **A pane's header keeps its height.** It gained a line, and gave it back, whenever a layer started, finished or was edited. Measured with every state forced (idle, waiting, building, measuring, out of date, stopped, failed; every layer, then only the first) at 900, 1140, 1440 and 1920 px, 1 to 6 panes, 1 to 4 layers: 37 of 96 layouts changed height on 2.5.0 (78 → 102 px with four layers at 900 px; 28 → 53 px with one layer in a 3-pane window at 1140 px); none of 96 does now, with annotations off, on and in the light theme. The chips are one line that takes the width the buttons leave, and the pane's state chip, an item of the header's first line, is the first thing in that line. Where the chips do not all fit the state words shrink first, then the names, never below about three letters (no name is cut to under 25 px in 64 layouts × 6 states in both themes); where even that does not fit (168 pane-states of those) every chip is its dot and a small arrow, as in a narrow pane. The pane's own state is a dot (gray, amber or red; its tooltip says the word) where the pane has several layers, is narrow or has under 215 px for it. A narrow pane's chips take a line of their own under its buttons.
- **Tab goes the way the eye does.** With several layers the chips are on a line under the buttons, but they came first in the page's order, so Tab visited them and then went back up to the buttons. They now follow the buttons when they are on their own line and sit among them when there is one layer.
- **The annotations dot keeps the keyboard.** Toggling it rebuilt every chip of the pane, so the keyboard fell to the page and the second Space did nothing; the dot is updated in place (an open editor keeps its anchor too).
- **The layer editor stays open under its color picker.** A click on the picker's palette counted as a click away from the editor, because the picker is a page-wide popup, and closed the editor under it. Escape closes the picker only.
- **A control that disables itself, or whose menu closes, no longer drops the keyboard to the page**: Duplicate at four layers and Remove at one hand it to the editor's first control; *Render layer* in the + menu to the chip it added; *Annotations*, *Reset view*, *Save picture* and *Maximize* to the button that opened the menu; Retry to the chip.
- **The status line never names a banner that is not there.** When a layer that worked after a failed one had taken the failure's banner down, the line said "the red banner on it says why" over no banner. Whatever takes a banner down (a layer's card, work stopped, a layer removed, the annotations' banner dismissed), a pane with nothing left to do and a failed layer shows that failure, the first failed layer's, and Dismiss shows the next one's; a re-shown banner names the layer by its place now. After the Panes box changes the line says what happened (more panes: the new ones wait for Compare; fewer: "Showing 2 panes; the ones that stay keep what they show" instead of "Press Compare to fill the new panes"), and putting the grid back after one pane filled it says how the panes stand, or that a Compare is under way, instead of leaving "Pane 2 fills the grid".
- **Putting the grid back no longer moves the camera.** A Compare run with a pane maximized built the others against no size; when they came back each framed itself, and Sync copied the first of those framings over the orbit the user had arranged (c943: the pane in front at (60.9, 40.2, 170.2) went to (127.0, 35.1, 168.9), and so did the other two). The panes take their size first, with Sync held, and then the camera of the pane that was in front (or, if it never drew, of the first of the others that did: they still look the same way); with Sync off each keeps its own.
- **Popovers on a short window.** The bottom row's editor and menus ran off the foot of the window (the editor of panes 5 and 6 ended at 628 px in a window 600 px tall, at 467 px in one 360 px tall): they open above the header when there is room there, else are cut short (not under 120 px) and scroll.
- **The Compare button's tooltip says what it does** (every *layer* that is not up to date, up to three panes at a time, a pane's layers one after another, a Difference waits for the layers it measures); a test ties its number to `MAX_WORKING_PANES`.
- **A failed build leaves no rings of another load**: a pane whose last surface was just taken away showed the annotations of the load before it (or of another cell); those go, and the rings of this load, still true, stay.
- Not changed: the toolbar under the grid wraps its summary and status text by itself at 900 to 1440 px, which resizes the grid by one text line (29 px) when a Compare starts, ends or fails (678.5 → 649.2 → 619.8 px at 900 px). It is the toolbar's, not the header's; the options are in `docs/context/state-2026-10-16.md`.

## 2.5.1 — 2026-10-16

### Voxel remesh may march 10 million raw triangles, up from 3 million (2026-10-16)

- **The raw-triangle limit of Voxel remesh, Convolution surface and Ultraliser is 10,000,000** (`unify.MAX_RAW_FACES`; it was 3,000,000). A build asks for three times its Face cap (the app's 400,000 asks for 1.2 million), at least 600,000 and at most the limit; Face cap 0, which keeps every triangle, asks for the limit itself. The default render is therefore exactly what it was, and only a Face cap over 1,000,000, or none, reaches the new range. The Fine voxel and Face cap tooltips said "3 million" and did not say that the cap is what limits the voxel; both now do.
- **The evaluation budget follows it** (`unify.work_budget`): 60 million field evaluations up to a raw budget of 3 million, 20 for each triangle above it (200 million at 10 million). Measured first, the triangle limit alone did nothing: Inferior c1178 came out at 0.0745 µm instead of 0.076 µm, 2.29 million faces instead of 2.13, the same deviation (0.592 µm), because the 60 million evaluations ran out at once; c1178 needs 194 million at 0.0414 µm, the finest voxel the new limit allows it.
- **What it costs** (c1178, 4,090 µm² of surface, `sbfsem-measure`, on a Mac that was also busy, so the times are indicative): every triangle kept, 2.1 million faces 13 s and 1,256 MB at the old limit, 4.9 million 31 s and 2,467 MB at 0.05 µm, 7.1 million 60 s and 3,467 MB at 0.0414 µm: a peak of 310 MB plus 0.445 MB per 1,000 triangles. A Face cap adds the decimator's copy, 0.37 MB per 1,000 raw triangles: 7.1 million down to 3.4 million, 78 s and 5,615 MB. Ultraliser at the same voxel: 119 s, 3,305 MB. The mean deviation from the annotations is 0.59 µm at every size: a finer voxel is more triangles on the same surface. The table is in the user guide.
- **Convolution surface** gets finer with no cap, too: c1178 at a typed 0.06 µm, 0.152 µm, 561,000 faces, 24 s, 540 MB (0.245 µm, 215,000 faces, 19 s, 320 MB at the old limits).
- **The build gate prices the extra sheet.** `estimate_peak_mb` takes the Face cap (the route and a measurement job pass it): up to 3 million raw triangles the estimate is what it was; above, 500 bytes for each triangle of the budget with no cap and 1,000 under one, so a build with no cap weighs 3.5 GB more, and one capped at 3.4 million or more (the heaviest the page can ask for) 7 GB more, and runs alone under the production unit's 9,000 MB budget.
- **Stored surfaces are not served for a build that asks for more.** A build over the old limit's budget (a Face cap over 1,000,000, or none) is stored under a key that names the budget (`facade._budget_token`), so the surface a release before this one built for the same options is not answered for it; a build at or under 3 million keeps its key and its surface, and `GEOMETRY_VERSION` stays 6.
- Tests: the limit and `raw_budget`, `work_budget`, the evaluation budget following the raw one in `unified_mesh`, the key (the default cap and a million keep theirs; 2 million and none name the budget), the estimate against the production unit's budget; `test_voxel2_asks_for_a_raw_sheet_three_times_its_face_cap` now pins a cap of 2,000,000 at 6 million and 4,000,000 at the limit.

## 2.5.0 — 2026-10-16

### Closed curve volume averages out its staircase (2026-10-16)

- **Two controls beside Voxel size, Disc avg ×r and Path avg µm, average out the staircase of a closed-curve surface**: a soma that comes out as a stack of plates, a dendrite along the section plane with a tooth on its rim for every section. Both start at 0, which is off: every surface, mesh id and export is as it was (the Closed curve and Hybrid surfaces of c943, c16137, c1178, c606 and c2628, ten of them, hash the same before and after; the options are left out of the cache key while 0, so `GEOMETRY_VERSION` stays 6).
- **Disc avg ×r** (0 to 3, Closed curve) averages each disc with its neighbors along its chain of links over that many of its own radii: a 5 µm soma disc over microns of the chain, a 0.2 µm dendrite disc over a section or two; the ends of a chain stay as traced, and a chain breaks at a radius jump over 2.5×. **Path avg µm** (0 to 2, Closed curve and Hybrid) does the same for traced outlines on its own control, over that many microns along the chain: a length, not a count of sections, because sections are 70 nm apart in some volumes and 90 nm in others. The finished surface is then smoothed over the same length (a Taubin pass count per vertex: no shrinking), and not at all where a process is under 1.6 mesh edges across, so a thin dendrite is not eroded. Hybrid's discs are the Cylinder build's, so it shows Path avg alone.
- **Measured** at Voxel 0.1 µm (creases = µm of edge sharper than 45° per µm² of surface; ripple = the soma's facet ripple; distance = mean distance from the annotations): Inferior c16137, Disc avg 1: creases 1.08 → 0.45, ripple 7.6° → 3.0°, distance 0.075 → 0.081 µm, 2 s and 338 MB → 4 s and 456 MB. RPC1 c2628, Path avg 0.25 µm: creases 1.76 → 0.69, distance 0.111 → 0.138 µm (0.1 to 0.25 µm is the range to use: Path avg 0.5 µm costs 0.051 µm). **A finer Voxel size does not do this:** c16137 at 0.025 µm has more creases (2.26) and a rougher soma (14.5°) in ten times the triangles, and c2628's 0.1 to 0.025 µm cuts the distance (0.111 → 0.034) and raises the ripple (14.8° → 23.3°). The averaging moves the surface off the annotations and the deviation metric counts that as error: that is its price. Volume changes by at most 4 %, and c16137 stays one body at every strength. The ideas that were tried and turned down are in `docs/context/rejected-ideas.md`.
- API: `curve_avg_discs` (0–3) and `curve_avg_paths` (0–2) on `mesh`, `mesh-job`, the exports and the render-prefs bodies; both are in each Render compare pane's options. The build gate's memory estimate adds up to 70 % (at most 900 MB) for a build that averages (measured ×1.2 to ×1.66). `sbfsem-measure` gains a *creases* column (`meshdiff.crease_density`, `dihedral_angles`); `Mesh.taubin_weighted` takes a pass count per vertex (exact slabs past 1.5 million vertices, 60 million vertex-passes at most). Docs: user guide (*Averaging out the staircase*), backend guide, API guide, DEVIATIONS 5.12; `tools/check_curve_averaging.py` drives the controls in a browser (24 checks).

### The right-click menu finds thin cells, lists what is under the pointer, and opens when the button comes up (2026-10-16)

- **A right click on a thin cell found nothing a quarter to two thirds of the time.** The menu asked a ray where the pointer was, and a cell's processes are one to four pixels wide: of 60 pixels each was drawn at, c1178 opened its menu on 44 (Voxel remesh, and Tube), c16137 on 32 (Original) and 23 (Closed curve volume), c21768 on 48 (Voxel remesh orig). The distance from the pointer to the cell's triangles is now measured on the screen (one pass over them, the price of a ray): a surface within 4 pixels counts when none is under the pointer; rings, a skeleton branch, the wireframe, the annotation wireframe and position-shift arrows within 6. 60 of 60 in every technique.
- **With the surface hidden, nothing drawn was clickable.** The rings (0 of 12), the wireframe (0 of 12) and the annotation wireframe (0 of 24) opened no menu: no ray meets a ring or a line. **A skeleton line took the clicks of the cell behind it** (its pick was a micron in the world, 14 to 25 pixels at close zoom: 27 of 32 clicks on another cell's surface beside the line opened the line's cell). **A click where the crop had cut the cell away opened its menu** (8 of 8); **in orthographic view** the part of a cell an orbit or pan carried behind the camera's plane was drawn and unclickable (16 of 54 points).
- **A right-drag that began on a cell opened the menu and stalled the pan (macOS).** The browser asks for the menu as the button goes down; the menu now opens when it comes up, within 8 pixels of the press (it was 5, and Windows only).
- **More than one thing under the pointer is listed at the top of the same menu**, headed *Under the pointer*: annotations, a skeleton branch, a synapse type, each cell's surface, named with cell, section and volume, nearest first. Hovering lights a row's item up; a click selects it (a second annotation becomes the second marker) and the menu stays, about it: *Open in Viking* goes to that annotation exactly, *Hide above* / *Hide below* and *Deselect* are its. Every item stays; Esc closes. A submenu at the window's right edge opens to the left; a tall menu starts at the top.
- **Render compare's panes had no menu.** A right click now opens the compared cell's menu, for any layer or the annotations overlay. Checks: `tools/check_contextmenu.py` (82, real right clicks: against 2.4.2 the same script fails exactly the fix checks), `tests/js/test_contextmenu.mjs`, `tests/js/test_pickgeom.mjs`.

## 2.4.2 — 2026-10-16

### The reviewers' defects: volume access, sessions, finished renders, the Section view and Render compare's layers (2026-10-16)

- **A signed-in account could still read a volume it was not granted through `POST /api/connectivity/query`** (and, on `main` before 2.4.0, through `POST /api/neurons` and the freshness batch: 2.4.0's route checks closed those two). The router's guard read the volume from the first place a request named one (the path, else the query string, else a body) and read a body only when its Content-Type began with exactly `application/json`; the query route had no check of its own. Tried on `main`, on 2.4.0 and on the fix with an account that may open RC1 only: `?source=RC1` beside a body naming Inferior Monkey, and a body sent as `APPLICATION/JSON`, ` application/json`, `application/vnd.api+json` or with no Content-Type, answered 200 with data. The guard now reads every place (the path, each `source=` of the query string, the `source` of a JSON body of any body-carrying method, a body being JSON exactly when FastAPI takes it for JSON) and refuses the request when any of them is ungranted, and the connectivity query checks its own volume. All 31 evasions tried are 403; a granted request still reaches its route. (`GET /api/volumes/RC1?source=i` is a 403 too; no client sends that.)
- **A session being renewed no longer signs out the requests that arrive meanwhile**, and the interleaving that lost it for good is gone: a request that finds the session being renewed uses what it holds, and only the request that renews it applies the 24-hour limit.
- **An unreachable identity server no longer ends every session within the hour.** The access token falls due first (about an hour), and a failed refresh deleted the session at once, so in a real outage everyone was signed out and nobody could sign back in. A refresh that fails for a timeout, a refused connection, an unreadable discovery document, a 5xx, or a 200 that is not a token now leaves the session on its last permissions, backing off between tries, up to the 24-hour bound it already had; an explicit refusal (a 4xx) still ends it at once.
- **The final poll of a finished error map or difference is as fast as 2.3 made it** (0.29 s against 0.81 s for 250,000 numbers; an earlier change had made it 0.88 s): the job's result is dumped once, not walked by the general encoder.
- **A bulk SWC archive that ran out of time says so.** The response carries `X-Archive-Asked`, `-Written`, `-Failed` and `-Not-Attempted`, and the viewer's status line says in red that the archive holds only k of n skeletons, how many were not attempted or failed, and that `manifest.csv` names them (it used to read "Exported … .zip").
- **Saves**: the login runs before the size cap (an unsigned-in caller is answered 401 and the server no longer reads the body), and the 507 says the limit is on the body as sent. **A damaged cached mesh** (a cut-off or edited file: 542 `TokenError`s in 3,500 byte edits of a real one) is a miss and is removed, and a good one is left alone when its read fails for any other reason. **A list of several pages** (label searches, the Graffinity start set, the vessel, cone and boundary lists) is one moment or the other, never both, and the vessel overlay finds a vessel added since. Deviations carry `exterior_um`, `hidden_fraction` and `annotation_exterior_um` on the API; the packaged data readers take their arguments by name again; DEVIATIONS 5.10 and 5.11 record the 27 synapse-type colors and the one-section cone-outline slab. 1,397 tests, with a test for each of these that fails on the old code (and five mutants of the audit's own code that no test noticed now each have one).
- **The Section view signs a point by the number of bodies round it, not the parity of the crossings.** The Original and the other assembled pathways are unions of interpenetrating closed pieces, so a point inside two of them was called outside: against the server's 3-D rim test only 81.8 % of the Original's 45,732 rim points were on the same side; 99.15 % are now (the Voxel remesh's 45,730 of 45,732 are unchanged; most of the rest are points where an open end makes the in-plane rays disagree). A section asked for before the annotations arrive is kept, not clamped to 0; a view nobody has panned says when it follows the content, so two panes sharing a view stay together; the canvas fits the host's content box.
- **The error drawer**: a threshold drag ends when the chart is redrawn, or on a move with no button, and the redraw after a resize is made in the next frame (it raised "ResizeObserver loop completed" seven times in eight window sizes); a click on the chart leaves the keyboard on the grip (so Escape works), "Set at 90 %" makes a threshold from the keyboard, and a control a keypress rebuilds gives the keyboard to its replacement. The Distribution card is the shared histogram model and says what lies past its axis (about 4 % of the Original's area), a ring has the color of its surface (it was `rgb(255,176,0)` where the surface was `rgb(255,216,0)` for 0.5 µm), and the improvement headline gives both the mean change per annotation and the area-weighted one (−0.069 µm and +0.005 µm for Voxel remesh to Original on c1178: it read as "much better" where, by area, it is no better).
- **Render compare**: removing a layer while it builds (or unticking error while a stale layer rebuilds) takes the pane's progress card, and its Stop that did nothing, down; a hidden layer no longer shows its error map's rim points on nothing; a caller that has already left starts no mesh download. The 2026-10-15 incident's rule (a measurement is made from the options that built the mesh) and the per-layer switches now have tests that fail when they are broken. `tools/check_dropdowns.py`'s drag moves name the held button, as a real mouse's do (counted alone, Chrome let go of the pointer capture at the first move).

## 2.4.1 — 2026-10-16

### A cell's Closed curve surface may have 10 million voxel faces (2026-10-16)

- **The limit on a Closed curve raster's exposed voxel faces is 10,000,000, up from 3,000,000** (`closed_curve.MAX_SURFACE`; the refusal that read "would need N voxel faces … the limit is 3,000,000" reads 10,000,000). A voxel face is 1.4 to 1.9 triangles, so a surface may have up to 19 million. RPC1 c2628, which had to be coarsened to 0.044 µm, builds at the menu's 0.025 µm and its finest pixel is 0.021 µm (10,072,984 faces at 0.02). Measured on that cell, one process per build: 0.05 µm 3.4 M triangles, 10.5 s, 1.38 GB at the build's peak, 114 MB of JSON; 0.035 µm 5.9 M, 17.7 s, 2.05 GB, 203 MB; 0.03 µm 7.6 M, 27.9 s, 2.55 GB, 261 MB; 0.026 µm 9.5 M, 26.4 s, 3.16 GB, 329 MB; 0.025 µm 10.1 M, 29.6 s, 3.38 GB, 351 MB (the page makes the text from lists of numbers, 2.3 GB more at that size). In the page, at 0.025 µm: 48 s from request to parsed mesh, the tab's heap 37 → 620 MB, 1.1 s to draw 10.15 M triangles.
- **The build gate counts such a build honestly.** The estimate it reads was capped at 2,100 MB, which was exactly 3 million faces; c2628 at 0.03 µm and finer was estimated at 2,250 MB and built in 2,554 to 3,377 MB. The cap now follows the limit (`CURVE_MAX_BUILD_MB` = 120 MB plus 350 bytes for each of 1.9 triangles a face: 6,770 MB), and the production unit's 9,000 MB budget holds one build that large at a time. A test pins that the worst build fits the unit's budget and that the cap follows `MAX_SURFACE`. Nothing that built before changes (no `GEOMETRY_VERSION` bump); the page's refit to "the finest pixel that fits" reads the server's own number, so it follows.
- At the limit a surface is 520 to 650 MB of JSON and a 1.2 GB heap in the tab: **Decimate** and exports (which take the file, not the page's copy) are the levers when that is too much. The Voxel remesh's own 3 million *raw triangles* (`MAX_RAW_FACES`, the Fine voxel's auto size) is a different limit and is unchanged.

## 2.4.0 — 2026-10-16

### The selection glow settles (2026-10-16)

- **A selected ring was drawn too large and too bright, and always.** Its glow was three times the tube's radius at 30 % opacity, which a stack of rings only added up. A ring you choose (triple click, a menu row, the second marker stepping with `↑` / `↓`) is now drawn as it was, which says "this one", and settles over about a second and a half (held 0.35 s, then an ease-out over 0.9 s) to a calmer one: the tube 0.8 times as thick, the glow 2 times the base radius instead of 3 and 20 % opaque instead of 30 %. A ring that is only redrawn (the zoom changed) or previewed from the chooser's rows is drawn settled at once, so are all of them when the system asks for reduced motion, and the skeleton check's rings (dozens at a time) keep their faint glow and do not pulse. The tubes are not built again as they settle: each vertex moves in toward its path (`static/js/highlight.js`, `tests/js/test_highlight.mjs`; measured on a real ring by `tools/check_renderer.py --only glow`: tube 0.0935 → 0.0748 µm, glow 0.2804 → 0.1869 µm, opacity 0.30 → 0.20).

### The readout names the section of each marker; Open in Viking can open Viking Test (2026-10-16)

- **The second marker's readout says which section each ring is in.** With a second (blue) marker the readout read `ΔZ +0.21 µm · +3 sec`, and `+1`, `+2`, `+3` as the marker stepped; it now reads `Z 1231 → 1234 · ΔZ +0.21 µm · +3 sec`, the yellow ring's section in the yellow ring's color and the blue ring's in the blue one's (cyan and pink on a yellow cell). The same line is the status line's after an arrow-key step. A marker with no section number leaves only the Z between. `static/js/markers.js`, `tests/js/test_markers.mjs`.
- **Open in Viking knows the unsigned "Viking Test" build.** It installs beside the signed Viking as *Viking Test* and registers `viking-test://` (Viking's `VikingChannelIdentity.cs`); it reads the same query as `viking://`. A page cannot ask Windows what is installed, so the launch tab (`viking-launch.html`) notes which link took the focus and which did not, and the browser keeps that (this browser only; `static/js/vikingclient.js`). Only the signed Viking: nothing changes. **Only Viking Test:** the first open says the usual *Viking client is not installed.* and offers **Open in Viking Test** under it; one click is remembered, and from then on the item opens Viking Test directly. **Both, and both have answered here:** the first open of a session asks *Open in which Viking?* and keeps the answer for the session. **Choose Viking client…**, under *Open in Viking* in the cell menu (Windows only), asks on request, and is how a machine with both first tells the browser about the second. **Neither:** the same message and installer address as before. Elsewhere than Windows nothing here appears and the item says what it said.
- **Why the second program is a button.** Chrome refuses a launch a script starts seconds after its page loaded (*Not allowed to launch 'viking-test://…' because a user gesture is required*, measured in Chrome for Testing 148, also with "Always allow" set) and lets a click's through, so the page never starts the other program by itself. `GET /open-in-viking` takes `client=viking|test` and (with `page=1`) `then=` the other; `client=test` is always the direct link, because their `CreateCode` only redirects to `viking://`; an unknown client is a 400. `tools/check_app.py --only viking` walks the launch tab with real input and the menu's four cases (it needs "Always allow" for the server's origin in its throwaway profile, which `check_app.py` now sets: headless Chrome on macOS takes no more input once the question is open).

### Orthographic view no longer cuts cells away as they orbit (2026-10-16)

- **In Orthographic view, parts of cells vanished as if past an invisible boundary.** The orthographic camera is made with clipping planes 20,000 µm either way, so it cuts nothing, but every Reset view, view button, restored view and fly-to wrote a perspective camera's planes over them: a near plane a fraction of a micrometer in front of the camera. After a pan and an orbit about the moved pivot, the camera sweeps through the scene and everything on its side of that plane was cut: in a test, 241 of the 482 surface vertices on screen (32 to 99 of 400 on other angles) at the click of a drag. The planes of an orthographic camera now hold the whole scene on both sides of its own plane (symmetric, resized as the scene changes), at every one of those places and every frame. A perspective camera is as it was (its near plane follows the working distance, its far plane the scene). `tools/check_renderer.py --only planes` orbits, pans and resets a two-sheet scene 500 µm long in both views and counts the vertices a plane cuts; on the old code it found them in the orthographic view, now none in either; a real-mouse fuzz of 60 orbits, pans, rolls and zooms of three cells found none.

### Closed curve volume draws a cell's discs together with its traced outlines (2026-10-16)

- **A cell with traced outlines and discs is drawn whole under Closed curve.** Inferior c16137 had four closed curves traced on it at 04:21 and Closed curve then drew only those: a patch 14 × 13 µm and 0.4 µm thick in place of the cell (`volume` 40 µm³ against 450). The discs stood in as circles only for a cell with no traced outline at all. They now join in for every cell: each disc is its circle of its own radius at its XY center in its own section, with the links between discs drawn through the sections they cross, and the traced outlines keep Viking's links between them and the interpolation across skipped sections; a link from a disc to an outline is followed. A cell traced only with discs, and Hybrid (its curve half is the outlines, its disc half the disc build), are as they were. On c16137 with four outlines added in a test: the same 339,428 faces and 440 µm³ as its discs alone, 4.7 s at 0.1 µm.
- **`GEOMETRY_VERSION` is 6**: the Closed curve surface of a cell with both kinds changes, so every stored surface is built again the first time it is asked for (a cache key carries the version; nothing else about a surface changes).

### Front-end fixes from the 2026-10-15 audit, the rest of the viewer (2026-10-16)

- **Light theme: a hovered row can be read.** The right-click menu's hovered row and the Skeleton check's hovered or current row were ink on a dark fill (contrast 1.23) because `--hover` and `--border` were never defined and their dark-theme fallbacks showed through. Both themes now define `--hover` (ink on it 14.9:1 light, 10.9:1 dark) and the menu draws with `--line`. The Network graph drew its label text, halo, loaded-cell ring, selected node and selected edge in fixed light-on-dark colors (the ring and the edge were 1.2:1 on the light canvas); they are the page's ink and panel now (17.6:1) and the graph restyles in place when the system's theme changes. The synapse types' own edge colors are unchanged, and a few pale ones (UntaggedPost, Touch, GapJunction) stay faint on the light canvas.
- **A narrow window has a render again.** At 720 px or less the layout had two columns for its three children, so the drag handle took the second and the render was 220 x 0 px at 700 px (and 0 px tall at 480). The handle (which has nothing to drag when the list is a fixed 220 px) is left out there: 480 x 511 px of render at 700 px, 480 x 151 at 480 px, 400 x 118 at 400 x 800.
- **The Connectivity tab takes any number of paths.** Math.min, Math.max and push were given every index of a cell or every path as arguments, which throws from about 125,000 (the tab accepts 200,000); they are loops. The legend's boundaries take the fewest decimals that tell them apart (a scale up to 3 in six steps read 1 1 2 2 3 3, now 1, 1.3, 1.7, 2, 2.3, 2.7, 3; one topping out at 1 read seven 1s, now one step, or two (1 and 1+) when Max is set to 1 and cells go past it), and the step width is in the stylesheet only. **Matrix .csv** names each row and column as on screen (`c1178 GC ON`, `DB4 BC (12)`); the row names used to run the ID and label together with the toggle glyph.
- **Skeleton check**: when the request for the shortest path through every annotation fails, the line says so, with the reason, in red, instead of promising it for ever; the highlight's label reads Lamella, not Lamellae.
- **The Structure ID box and the server read the same strings.** Python's `\d` and ignore-case `[a-z]` took Arabic-Indic and full-width digits, the long s and the Kelvin sign, which the box refused; a number the box called unreadable was an ID on the server. The server's digit and letter patterns are ASCII, as the box's; 33 strings are run through both parsers in a test (they still differ on exotic whitespace such as a byte-order mark).
- Smaller: the dendrite-diameter chart's y axis says "Mean diameter (µm)"; the two editors in the Display panel (Edit materials, Edit gradients) no longer inherit a 10 px, line-height-1 chevron-button style, so the open summary is ink and their text is the panel's 12 px (519 and 456 px tall, from 512 and 426); the network's ConvPre is the 3D marker's red to the last digit; removed what nothing reached (`state.setOpacity`, `.cell-panel .sub`, a second `.muted.error`, two grid declarations on non-grids, an unused element id, a dead rule in viking-launch.html). Tests: tests/js/test_connectivity.mjs, tests/js/test_skeletoncheck.mjs, tests/unit/test_static_css.py (undefined tokens, contrast, a track for every visible child), test_static_colors.py (the two link-type color tables agree except five named types), test_spec_parity.py.

### Backend audit fixes: volume access, the identity server, finished renders, attached renders and the deploy files (2026-10-16)

- **An account without Inferior Monkey could load its cells by leaving "source" out of `POST /api/neurons`, and ask whether any volume's cell had been edited.** The router's guard reads only a source a request states: a load that names none defaults to Inferior Monkey, and the freshness batch names a volume per cell. Both routes now check the volume themselves (the batch answers per cell: a cell of a volume the account may not open comes back with `freshness: null` and an `error`, and Viking is not asked about it), the upload route uses the same check, and a test lists every route that takes a body and who checks its volume. So does `POST /api/connectivity/query`, which the guard alone did not cover: a body that named another volume than a granted `?source=` beside it, or that was sent as `APPLICATION/JSON`, ` application/json`, `application/vnd.api+json` or with no type, got past a guard that stopped at the first volume it found and read only a body typed exactly `application/json` (on `main` as well, and `POST /api/neurons` and the freshness batch with it). The guard now reads every place a volume is named (the path, each `source=`, the `source` of a JSON body in any spelling FastAPI reads) and refuses the request if any is not granted, and the test tries each route with the spellings, a second source and a list of cells. The load body is a model now, so a flag sent as text (`"refresh": "false"`) is false; it was true.
- **A slow or absent identity server no longer stalls the site.** Signing in and renewing a session called the identity server on the event loop, so one slow permissions answer held every request, health checks included, and every later request from that browser paid the failed call again (the audit's probe, with a 3 s outage: both requests took 3.1 s, and the next one 3.1 s again; now the health check answers in 0.1 s and the next request in 0.0 s). Renewal runs in a worker thread, one request at a time per session (a request that finds it being renewed uses the answer it holds, whatever its age: only the request renewing decides that a session is too old), a failed refresh is not tried again for a minute (the 24-hour limit on an unrefreshable session is unchanged), the sign-in callback's two identity-server calls leave the loop too, and a session file is saved through a temporary file of the writer's own. The 24 hours hold for the access token as well as the permissions: a token refresh that could not be asked, or that the identity server answered with an error that is not its refusal, used to end the session at once (the token falls due first, about an hour in, and signing back in needs the same server); now the session goes on, on its last known permissions, and only a 4xx from the token endpoint (a spent or revoked grant) signs a person out sooner.
- **A finished render waits to be collected as 41.5 MB of text, not 228 MB of Python lists** (a 400,000-vertex, 800,000-face mesh with a value per vertex; 206 MB without one), and collecting it costs 0.02 s, not 5.7 s: the job's thread makes the JSON once and the poll splices it in, byte for byte the body the poll made before. A job is set to "done" after its result (and to "error" after its reason), so a poll can no longer see a finished render with a null mesh; a finished job is swept when it is asked for or the health report is read, not only when another job is submitted. `SafeJSONResponse` walked every response for NaN before encoding (about half its time); the encoder refuses a NaN by itself, so the walk runs only when one is there (the 41 MB mesh: 2.5 s to 1.2 s).
- **Attaching a render keeps the cut and the strata coloring.** `clip_ipl`, `keep_above`, `color_by_strata` and `clip_z` were dropped by the body (the diff's, which leaves the view's own options out on purpose): a Clip IPL came back as no clip after a reload. A cut that is off is left out of the stored document. The cell ID in the three `render-prefs` paths must be a 32-bit structure ID, and the server holds at most 5,000 attached renders (507 past that).
- **Saves say what is wrong with them.** A `NaN` in a document and a saved document that is no longer readable were 500s and are 400s that name the document; an oversize document (the limit is 5 MB; nginx lets 64 MB through) is refused from its declared length with the 507 it always got, before any of it is parsed, and after the login: a caller who is not signed in is answered 401 without the body being read. The declared length is the body as sent, so a body pretty-printed or padded past 5 MB is refused though the store (which writes it with an indent) would keep it under, and the message says what the limit is on; the viewer sends compact JSON.
- **A build worker killed from outside no longer breaks every build after it.** The kernel's out-of-memory killer took a worker and left the process pool broken for good: every later build raised until the service restarted. A build that finds the pool broken when it is handed over runs in a new one; one that was running when the worker died fails with a message that says so, and the pool is made anew. A truncated, empty or otherwise damaged cached mesh (a changed byte in the file's header raised `TokenError` out of `np.load`) is a miss and is removed instead of failing that render on every request, unless another request has replaced it meanwhile; a read that fails for another reason (descriptors exhausted, an I/O fault) leaves the file, where it used to delete a good one. Temporary files a dead writer left are swept.
- **A mistyped `method` or render option is refused.** A `method` the build does not know built a plain cylinder under a cache key of its own (`tube`, `Tube` and `curvemesh` were three builds of one surface), and an unknown option reaching an export, a difference or a deviation was taken for its default: now a 400 on the mesh and export routes and a failed job on the others. A load that asks for the synapses, or for another transform, than the copy held has is loaded again. A summary's `data_stamp` and `last_modified` are the UTC instants the freshness check reads (an Update used to report its load 7 hours before a plain load did). A location is found in the copy the request names.
- **Smaller:** `SBFSEM_MESH_CACHE_SIZE` sets how many built meshes stay in memory (the deployment guide told operators to raise a constant they could not reach); label searches percent-encode `&`, `#`, `+` and `%`; the signed-in name is escaped into the page; an uploaded neuron is parsed in a worker thread; the API names a single-cell export for its volume too (`Content-Disposition: c943-NeitzInferiorMonkey.swc`: the volume travels with the ID), while the viewer saves every export under a name it builds itself (`c943.swc`) and does so still; `sbfsem-web --open` waits for the port; the kept wireframe builds are locked; `sbfsem.service.dto.__all__` lists every DTO; `bootstrap.sh` closes the library directory like the others; the vendored-libraries location in nginx sets `X-Forwarded-For` itself (a request for a vendored library claiming to come from 127.0.0.1 skipped the login).
- **Deploys:** `redeploy.sh` runs what ground rule 4 names (pytest, ruff on src and tests, and mypy) and refuses to ship from a machine with no `.venv` unless `--skip-tests`; `--warm` is removed (it pre-built a mesh nothing asks for). Tests hold both nginx configs, the generated TLS config, the scripts and every environment variable to the guide, and `tests/unit/test_geometry_version.py` fingerprints the disc-built surfaces of c943 under six settings and the closed-curve surface of a cone cell with three traced outlines under three (counts, area, volume and where each sits: a surface moved whole keeps all but the last), so a change to one of those surfaces fails until `GEOMETRY_VERSION` is bumped (ground rule 12). A cell drawn through a registration or a section correction is not covered.
- Docs: `docs/api.md` gains the saves, locations and build routes, the 401 and 507 statuses and the volume guard's real reach, and says which options only a script reaches; `docs/deployment.md` lists every environment variable the code reads.
- **Removed (nothing reached it):** `_LINK_ORDERS`, `GRADIENT_KINDS`, `MeshJob.created`, a redundant `import re` and `import time`, a dead variable and a dead `getattr` branch.

### The core library after review: encodings, expiry, a round trip that kept nothing, and counts that doubled (2026-10-16)

- **Find and the ID box match labels with `&`, `#`, `%`, `+` or `;` in them.** They went into the query string unescaped, so a search for `R&D` asked for `R`. An apostrophe is doubled as before. Spaces and letters are left alone, so the disk cache's entries for ordinary searches still hit.
- **The lists that grow are asked again after an hour.**
  - These are: label searches, the ID box's label terms, the labeled-structure list the Graffinity start set reads, and the vessel, cone and boundary lists.
  - They were kept on disk for good, so a vessel added or a cell labeled after the first search was not found until the cache was cleared.
  - If Viking cannot be reached the older answer is used, and an offline server answers from whatever it holds.
  - A list of several pages is one moment or the other, never both: if a page cannot be had after the one before it was asked again, the older list is returned whole (it used to join the new first page to the old second).
  - The vessel overlay asks the list too, and is drawn again when a vessel has been added; its in-memory copy named no vessels, so it was served unchanged until the process restarted.
  - A cell's own rows are still kept for good.
- **A Viking error says what Viking answered.** `ODataError` carries the HTTP status and its message names it. A structure that does not exist (404) no longer reads like a gateway timeout (504).
- **Two requests storing one cached answer at once can no longer hand a reader a half-written file.**
- **A damaged stos file costs its section, not the cell.**
  - A count that ran past its numbers, text where a number belongs, or points the triangulation cannot use raised an `IndexError`, `ValueError` or `QhullError`. That failed the load of every cell crossing the section.
  - Such a file is now skipped like any other unreadable section.
  - One NaN point in a transform call no longer unmaps every point of the triangle it lands in.
- **A JSON export read back keeps what each postsynapse is.**
  - Inferred and untagged postsynapses came back as conventional. RC1 c606 lost 649 untagged and 256 of its 262 ribbon postsynapses.
  - The file now records the policy and the partners (`untaggedPsd`, `psdPartners`).
  - Files written before still restore their kinds from the rows.
- **The Network tab counts a synapse once when both of its cells are loaded.** Each cell listed it, so the edge weighed two. The link table names the other end of each StructureLink (`PartnerSynapseID`), and the presynaptic side names the edge.
- **A failed partner-label lookup is logged, and the service asks again** instead of keeping a table with blank labels on the cell.
- **Synapse-to-soma path lengths carry the right synapse.** `synapse_soma_path_length` paired IDs from the synapse table with markers from the node table, which are in different orders. 65 of 184 rows of c1178's ConvPost had another synapse's ID.
- **SWC export.**
  - The soma cutoff is taken over the cell's own annotations. A synapse annotation larger than the soma used to set the cutoff for the whole cell.
  - z is written to four decimals. Two decimals broke the Temporal volume's 75 nm sections.
  - `ANNOTATIONS_STAMP` and the manifest stamp are the UTC instant, whatever label the cached answer was fetched under.
- **The bulk SWC archive stops starting structures after 240 s** (the gateway gives up at 300) and sends what it built.
  - Each structure not reached is a manifest row whose `error` begins `NotAttempted`.
  - The response says so too: `X-Archive-Asked`, `-Written`, `-Failed` and `-Not-Attempted` count the structures, and the viewer's status line says in red that an archive holds only k of n skeletons, how many were not attempted or failed, and that `manifest.csv` names them (SWC of every loaded cell used to read "Exported …zip" for an archive of `NotAttempted` rows, the 200 having replaced the gateway's 504).
  - A failure after the file was written no longer leaves the SWC in the archive beside a manifest row that says it failed.
  - A trailing newline in the file-name pattern is refused.
- **Dendrite-diameter `bin_width` is bounded.** 0.05 to 1000 µm at the route (422 outside), and at most 50,000 bins in the library. A width of 1e-9 once asked for 10^12 rows.
- **Organelle groups (`Mitochondria`, `Nucleolus`, ...) are positioned in the volume frame** like every cell, not from the section mosaic's X/Y, which sits up to 345 µm away on RC1.
- **Every synapse type has a color.** 27 of them drew black, the legend's swatches included (MATLAB's `StructureColor` returns black for any type it has no case for). None shares a color except where MATLAB did (DEVIATIONS.md 5.10).
- **Closed curves.**
  - An outline with a NaN control point builds. It used to raise.
  - The closed-curves overlay splines a curve's cut-outs as it splines the outline.
  - The cone-outline slab is one section thick: 70 nm on RC1 and RC2, not the 90 MATLAB assumes (DEVIATIONS.md 5.11).
  - No surface changes. The meshes of c943 (Original, Voxel remesh orig, Voxel remesh, Wrap, Convolution), of c1178 (Original, Voxel remesh orig, Voxel remesh, Closed curve) and of the disc stand-in hash the same before and after, and this package leaves `GEOMETRY_VERSION` alone (the Closed curve change further up bumps it to 6).
- **Smaller.**
  - A Fine voxel typed under 0.04 µm, which the build raises to 0.04, is recorded as coarsened (`voxel2_coarsened` in `mesh.meta`, and a line in the server log) instead of silently becoming 0.04. No API field carries it, so the viewer does not show it.
  - `volume_to_mesh(resize=)` keeps the mesh's size.
  - A loaded cell's closed curves are read from the Location rows already held, when those rows carry their shapes. That saves one request on a cold load, and c1178, c192 and c2542 come out bit-identical.
  - The correction service is not asked for another volume's sets when a volume has no Viking name.
  - The packaged data readers read once per volume and hand out read-only arrays.
  - shapely 2.1 is required.
  - Removed: the orphan data files `MARCRC1_*.txt` and `VikingMetadata.xml`, `ConeOutline.spline_3d`, `cone_outline_from_wkt`, `NeuronLoader.fetch_nodes`, `BoundarySurface.type_id`, `markers_um` and `meshgrid`, and `IPLBoundaries.thickness`.
  - The live registration's `RegistrationResult` is now `LiveRegistrationResult`.
  - Stale docstrings, the backend guide's Connectivity recipe and its correction-cache key are corrected.

### Render compare draws layers: up to four renderings in one pane, one to six panes, three building at once (2026-10-16)

- **A pane draws up to four layers of the cell together.** A layer is one rendering with its own technique and render options, color, opacity, visibility, surface, wireframe and error map; what a pane carried moves down to it. Each layer has a chip in the pane's header: a dot that hides and shows it (shift-click: only this one; a hollow dot is hidden, and a hidden layer still builds), its short name and, in a pane with several layers, a word for its state. The name opens the layer's editor (technique, Color, α with a Ghost button, show, surface, wire, error, ◀ ▶ for its place in the draw order, Duplicate, Remove, Retry, All options…). The **+** menu adds a render layer (a copy of the layer you were editing, in the next color at α 0.45: an A/B in one step) or the **Annotations** overlay, which replaces the *discs* box (one overlay per pane, drawn once per cell load). The options arrow edits the layer being edited, and the panel's title says which ("Pane 2 · layer 2 — Wrap (cube fit)"); *Copy tuning to other panes* became *Copy tuning to other layers* and reaches the other layers of a pane too.
- **A pane with one layer draws what it drew.** c1178 and c606, plain, with rings, as a wireframe at α 0.5 and as an error map with its rim points, two panes each: 16 canvases of 560 × 420 px against the previous build, 0 differing pixels.
- **Layers are drawn in the order of their chips and stay in it as you orbit** (the earlier layer wins a tie in depth). Three translucent layers dragged round by a real mouse through 378° keep one draw order on c1178 and c606, where without the order it flips; two identical layers show none of the later one's color. Colors come from a palette by slot, not by technique (yellow, sky blue, pink, green: at least 43.9, 14.0 and 12.1 CIEDE2000 apart for 2, 3 and 4 layers under normal, protan, deutan and tritan vision); the first layer keeps its near-black wireframe and another's is its own hue at 45 %. A pane's **⋯** menu has Reset view, Save picture, Maximize (also a double-click on its number: the other panes keep everything and stop drawing) and a Z range that cuts every layer and the annotations.
- **One to six panes, added and taken away at the end.** The panes you keep keep their layers, settings, surfaces and WebGL contexts (a change used to rebuild every pane: the settings carried over, the surfaces and WebGL contexts did not). A toolbar **Error** toggle ticks or clears the error box of every solid layer in every pane (half-ticked when only some have it).
- **Compare works three panes at a time**: a pane builds its layers one after another, a Difference after its sources, and the browser keeps the twelve last meshes it downloaded, so a technique used by several layers or by two panes at once is built and downloaded once (a copy of a layer costs no job, three different layers cost three) and going back to a technique is instant. Stop on a pane's card stops all its layers. A failed layer keeps a red chip, with the server's message in its tooltip, when another layer's progress card takes the banner's place (Retry is in its editor), and the summary line names the layer in a pane with several ("pane 2 layer 3 building").

### The error inspector and the Section view as modules, and a difference across two copies of a cell (2026-10-16)

- **A difference can hold mesh B to another copy of the cell (API; the viewer follows).** `POST .../mesh-diff-job` takes `copy_b`, the `copy_tag` mesh B is built from and measured against (mesh A's copy is the request's `?copy=`, as everywhere): a surface before a setting that moves the annotations, held to the surface after it. Each mesh is measured against the annotations of its own copy; the job's key and the cached difference's name both copies, so loading either one again retires what was measured against it and nothing else; the result says `copy_a` and `copy_b`. A copy that is not held is a 404, and the blocking `mesh-diff` refuses a `copy_b`. `api.deviationJob` and `api.diffJob` take a per-call `copy` (and `copyB`), for a layer that is not on the copy the tab loaded last.
- Two more browser modules, not wired to a screen yet. `errorinspector.js` is the content of the error drawer: a bias bar per layer, the area-weighted histogram with a threshold handle (drag, click or arrow keys; it reports the threshold in µm and its percentile), the error along Z, the worst places, the annotations (sortable, with a minimum-area filter), the annotations by radius (edges 0.25, 0.5 and 1.5 µm, yours to change; the classes add up to the server's totals) and, with two layers, which one fits each annotation better. Every Location ID carries its volume. `slice.js` is the Section view: one cut through the cell on a canvas with its own pan and zoom, with the contour of each render (by the error ramp when it is painted), the annotation discs and outlines of the section, and how far each annotation's rim lies from the contour in that plane (positive where the render is fatter than the annotation, negative where it stops short). Its keys act only while the canvas has the keyboard: Up and Down the next and previous annotated section, Left and Right one section, Page Up and Page Down ten, Home and End the first and last. Which side of the contour a rim point is on is a signed count over the cut's segments (each runs with the solid on its left), as the server's `enclosed_points` counts, and not a parity: the Original and the other assembled pathways are unions of interpenetrating closed pieces, and a point inside two of them has an even number of crossings to its right. On Inferior c1178's Voxel remesh every one of the 627 annotated sections cuts into closed loops (2,464, none open) and the section puts 45,730 of the 45,732 rim points the server displays on the side the server's 3-D test puts them; the shares of a rim the render encloses differ by a median of 0.03. On its Original, which is open, 45,343 of the same points (99.15 %) agree, where the even-odd rule agreed on 37,402 (81.8 %); the 389 that differ are almost all (344) points where the answer depends on the direction of the ray, an open end's signature. `tools/check_inspector.py` draws both in headless Chrome on the real result.

### Render compare's options panel and the viewer's cell list, after review (2026-10-16)

- **The options panel can be reached and used from the keyboard.** Tabbing through the panes' headers no longer moves the panel to the pane Tab is in (or closes it on a Difference): only a click on a header, picking a technique, or an arrow moves it. Enter or Space on a pane's arrow opens the panel and moves focus to its first control; Esc or the close button closes it and puts focus back on that arrow. Clicking another pane's arrow while the panel is open moves it instead of closing it. A panel hidden because its pane became a Difference, or because lowering the pane count removed it, stays closed until an arrow opens it.
- **A Voxel size you pick is kept, 0.05 µm included.** Compare no longer replaces an explicit 0.05 on a Closed curve or Hybrid pane with the 0.25 µm a traced cell is seeded to, and Use 3D render settings / Use this cell's settings keep the raster they bring when the technique they bring shows one. Changing a pane's Method to Closed curve or Hybrid seeds the raster at once (the box and the cost chip show what Compare will build). Reset to technique defaults and picking the technique again hand the choice back to the cell, and a raster seeded for one cell goes back to the technique's own when the next cell has no outlines.
- **Copy tuning copies only the knobs both panes use.** It used to overwrite a Wrap pane's Cube size, Ring detail and Blend, or a Closed curve pane's Voxel size, with the source's hidden defaults.
- **The cost chip prices Closed curve by its raster**: a raster that was not timed (the 0.25 µm and 1 µm a traced cell is seeded to) is the 0.05 µm rate scaled by (0.05 / pixel)². c1178 at 0.25 µm read ⚠ 41 s and now reads ≈ 2 s (measured 0.7 s locally); the 3D render's chip is corrected the same way. An empty Closed curve or Hybrid build no longer advises picking Closed curve or Hybrid.
- **The cell's loading points no longer stay on screen** after a build that was given up for a surface the browser already held, nor after the cell is unloaded.
- **Undo of an unload brings the cell back from the surfaces the browser kept** instead of downloading them again (c1178: 0.34 s instead of 1.87 s; Unload all then Undo of c1178 and c21768: 0.60 s instead of 3.65 s, no mesh request). They sit in a bounded cache (about 200 MB, least recently used first) and go when the cell is loaded again as other data, when Open replaces the workspace, on Rebuild all renders, and when the server is redeployed or restarted.
- **A load, Update from Viking, Refresh all or a Registration choice no longer rebuilds the cell list under a dropdown, slider or box you are using** in a cell panel (the dropdown that asked for a Registration load is rebuilt, so its section shows the new numbers). Enter in the Save dialog saves once (a held or doubled Enter wrote the document twice and one request failed); each save writes through a temporary file of its own; Open and Create folder run once at a time, and a second Open during a workspace's load is a real request, not a repeat.
- Right-click Analysis > Connectivity opens on the volume of the cell you clicked; Stratification redraws when you open it with other cells or data, and its tiles show whole numbers. A 2D view saved in a session or an undo step comes back with its 2D box ticked; undoing a boundary or vessel sheet reaches the stored session at once. The /open link notice reads "About the link you followed: …". A failed closed-curve export hints at the finest pixel that fits, not at the voxel limit. `tools/check_app.py` is the browser check of the glue in `app.js`.

### Render compare has every render option and the 3D render's technique list (2026-10-16)

- **Every technique pane carries the full set of render options.** The one number box per technique is gone. A pane's arrow (▲) opens a panel that rises from the toolbar over the panes (nothing behind it is resized) for the focused pane, named in its title and marked by an accent on its number. It holds all 25 geometry controls the 3D render panel has, built by the same code from the same schema: the same labels, help, ranges, steps, defaults and rules for which ones show or grey out. Beside them a build-time estimate and a **More** menu: MeshLab, Order, Wrap's Reject σ and Refits, and six bridges (use the 3D render's settings, use the cell's, send the pane's to the 3D render, copy its tuning to the other panes where they use it, reset to the technique's defaults, save as my technique defaults). Clip IPL is not offered (only the 3D render applies it, and a clipped mesh cannot be measured against). Typing rebuilds nothing: the pane is marked out of date and Compare rebuilds the panes that are.
- **The technique list is the 3D render's**: Voxel remesh, Voxel remesh orig, Convolution surface, Ultraliser, Original (MATLAB), Adaptive + Taubin, Wrap, Tube, **Closed curve volume**, **Hybrid** and Voxel remesh + MeshLab isotropic, quadric and Poisson (greyed out with the reason on a server without pymeshlab), then Custom… and Difference. A pane's name always follows its options. Closed curve and Hybrid compare now: a pane whose Voxel size is still the 0.05 µm default is given the raster the cell wants (0.25 µm, 1 µm for vessels) when you press Compare, and the box shows it.
- **Pane 1 is Voxel remesh**, the app's default. Tube no longer sends `smooth_iterations=0`: it shares the 3D render's cached mesh instead of building the same surface twice. A pane starts from your saved technique defaults, and panes keep their settings when you change the number of panes (the surfaces are not kept: press Compare).
- **Which knob acts is held to measurements** (c1178, one pair of builds per change; `tools/measure_knob_matrix.py` and `tests/unit/test_knob_matrix.py` check the schema's show and grey-out rules against 17 rows). Ultraliser greys Passes and Order (identical surfaces); Wrap shows Envelope σ (306,938 against 322,690 faces at 0.5 and 1.5); a Tube is smoothed under every pathway except Original (up to 5.2 µm between 0 and 2 passes under Adaptive + Taubin), so Passes is greyed for it only under Original; the closed-curve method reads the voxel size alone, so the pathway, its knobs, Order, Passes and Decimate grey out under it whichever pathway is selected. The schema's inert rule is now a list of rules with several conditions. An option the technique ignores no longer counts as a difference: re-picking a technique, Defaults and Apply say "Those cells already use these settings" instead of rebuilding an identical surface. Envelope σ's, Tie gaps' and Ring detail's help name every pathway that shows them.

### Renderer fixes: Render compare panes no longer evict the main viewport, Undo restores the Display panel, and the colorbar shows the bands (2026-10-15)

- **Changing Render compare's pane count no longer costs the main 3D viewport its WebGL context.** `Renderer3D.dispose()` freed nothing that mattered: 20 pane rebuilds created 105 contexts, tripped Chrome's limit of 16 live ones 89 times and lost the main viewport's for good. It now takes the listeners off, frees every layer's geometries, materials and textures and ends the context itself: the same 20 rebuilds leave 5 contexts alive (the page's own). Renderers out of sight draw nothing: with the Render tab open the four hidden Compare panes drew 240 of 300 frames a second, now none.
- **Undo and Redo put back everything the Display & context panel turned on.** `DisplayPanel.restore` only switched things on, so undoing a crop, Hold, a volume shift or a boundary or vessel sheet left it as it was; undoing or redoing a Hide above/below left the old Z range.
- **The colorbar shows the bands the surface is painted in** (with Levels at 64 or fewer, that many flat blocks); opening the gradient editor no longer swaps the cells' exact map for its 9-stop sample; depth colors are 13 to 16 times faster (800,000 vertices: 35 ms to 2.2 ms), the same colors to the last bit.
- Smaller: blank Az, El and axis-limit boxes keep the current value instead of reading as 0; a hidden cell hides its selection highlight; the points layer follows a cell's visibility, offset and Z range; the Display panel's standing paragraphs are deferred help. New for layered compare views (additive, defaults unchanged; a main-viewer canvas of c1178 is byte-identical before and after in both themes): `setVertexColors` recolors a surface in place (about 1 ms against 43 ms for a rebuild at 405,000 faces), `setLayerOrder`, `setLines`, per-ring colors for `setDiscs`, `setPaused`; `tools/check_renderer.py` is the scripted browser check.

### The compare view's engine room: an exact sign for a difference, area statistics, and the error paint and contour code (2026-10-15)

- **A difference can be signed by enclosure (API; the viewer follows).** `mesh_difference` and `POST .../mesh-diff-job` take `sign`: `normal` (the default, every number as before: B's outward normal at the nearest surface sample) or `enclosure` (negative where B's solid encloses the vertex, positive where it does not; the magnitudes are identical). The normal's sign is not reliable where the nearest sample lies on a piece buried inside B, which the Original pathway has: against the Original the two signs differ on 22.4 % of the Voxel remesh's vertices of c21768, 11.6 % on c1178 and 6.1 % on c606. On 200,000 vertices the enclosure test takes 3.7 to 24 s (c21768 is the slow one, see the next bullet) and obeys the kernel memory budget. `both: true` also measures B against A (about twice the cost). The result gains `stats_area` (mean |d|, RMS, 95th and 98th percentile, max, mean signed, each vertex weighted by the area it stands for: c606 mean |d| 0.223 µm by vertex, 0.343 by area), `sign_by`, `mesh_id_b` and `n_vertices_b`; the old fields keep their numbers.
- **The enclosure sign of an open reference is measured against its winding number, and a fan that would be a sheet that is not there is not believed.** The Original pathway is open (3,282 boundary edges on c1178, 3,028 on c21768, 17,098 on c606), and the plain upward ray count changes the side of 0.2 to 2.3 % of a Voxel remesh's vertices with the ray direction. One fan over every open piece made the sign the same from every direction but was not shown to be right, and on c21768 it is not: one thin open slot (1,642 edges, 382 µm round around 43 µm²) got a 1,563 µm² sheet (38 % of the surface), and 1,347 vertices were put inside B that B's generalized winding number (an independent solid-angle sum, inside above one half) puts outside. A fan is now believed only over a round piece (4πA/L² of at least 0.25; 1,911 of 1,912 pieces on the three cells are 0.35 or more, the slot 0.13). The vertices a fan of any other piece could reach are placed by the winding number itself, worked out exactly. Where the winding number is confident (under 0.2 or over 0.8) the sign is wrong on 0, 0.0005 and 0.0005 % of the 200,000 vertices of c1178, c606 and c21768, against 0.074, 0.091 and 0.789 % for the bare upward count and 0, 0.0005 and 0.67 % for one fan over every piece. It is the same along +z, +x, +y and −z except for one vertex that lies 0.03 nm from B's surface. Where the winding number is itself between 0.2 and 0.8 (12 vertices examined on c606, 890 on c21768) an open surface does not say which side a vertex is on, and the sign there is a convention. The inside share of c21768's Voxel remesh is 4.97 % (5.63 % with one fan per piece, 5.80 % bare). Cells whose open pieces are all round (c1178, c606) cost what they did; c21768 takes 24 s against 10 s at load 13 to 15 (+759 MB against +537). Every |d|, `sign="normal"` and `enclosed_points` are bit-identical. The job result gains `sign_overruled` and `sign_overruled_b`: the vertices the winding number put on the other side from the capped count (1,380 on c21768, 0 on the others).
- Two pure browser modules, not wired to a screen yet: `errorpaint.js` (the error and signed ramps, byte-identical to compare's; the lateral and axial split; thresholds; area statistics and histograms) and `levelset.js` (a surface's contour at a section, and the zero line of a signed field).
- **`sectionContour` has the outline at every cap on a section's plane, not only the surface's lowest.** A cell with a second piece, or a branch that begins above the lowest section, has bottom caps at other heights, and each gave an empty outline (two closed prisms, z 0..2 and 5..7: 0 segments at z = 5). At a level that is the height of a vertex it now takes the union of the two rules, a segment both find kept once (compared by position, so a rim repeated along a seam is not drawn twice). Between vertex heights it is `sliceContour`, segment for segment.
- A field of zeros against a scale of 0 stays the palest gray with a threshold of 0 (`thresholdColors`; two identical renderings used to paint worst red).

### Viewer fixes from the 2026-10-15 audit (2026-10-16)

- **A failed export is said.** Every export is fetched first and saved when it arrives; when the server refuses (a Closed curve raster over its voxel limit, a surface the clip left empty, a gateway timeout) nothing is saved and the status line says why in red, with the finest Pixel size that fits. `/export/{obj,stl,ply,dae,glb}` and `/mesh.glb` take `clip_ipl`, `keep_above` and `clip_z` as the mesh route does, so a cell clipped to its ON or OFF arbors is no longer exported whole; the Analyses card offers PLY like the menu. SWC of every loaded cell draws each cell as this tab draws it (separate archives per Registration or correction choice, pieces over 200 cells).
- **Every way of loading cells is one loader.** Display > Hold was ignored by every batch load (the loader shadowed the Display panel with a variable of its own). **Show them** loads as a typed list does (an attached render is kept; a cell traced with closed curves starts Hybrid or Closed curve), loading an ID that is already loaded keeps its render and display settings, and the load strip counts cells by volume and ID.
- **Cells leave the browser completely, and a surface is keyed like the server's.** Unloading a cell dropped its annotations but kept its surfaces in the browser for good, and a reload could be served the old one; the surface cache is keyed on the cell's copy and annotation stamp as well as the render options, and every exit goes through one function. Pressing Apply again while the first build runs retires the older draw (on c1178 the older Cylinder build used to land over the newer Tube).
- **Switching volume is one function** (the select, an /open link, Undo, a restored session, Import): a link or undo that changed volume left the old volume's boundary sheets and vessels on screen. Find results are cleared when the volume changes and load into the volume that was searched. Undoing a switch of Viking's current section transforms or section correction loads the cell under the earlier choice; Ctrl/Cmd+Z works while a checkbox, slider or dropdown has the focus.
- **Show Last Modified asks Viking** (it answered from a cache that never expires, 7 h late). The Morphology card and the Skeleton check follow the cell's copy (c1178's cable length is 1072.9 µm with the correction off and 895.6 with the curve fit, and showed one of them after switching). Update, Refresh all and a Registration click no longer tick a cell you unticked; "Refreshed N" and "Unloaded N" count only what worked. The Analyses tab and the Annotations table rebuild when another cell loads or its Registration changes; right-click Analysis acts on the cell clicked. Enter confirms the Save, Open and Video dialogs. Dendrite-diameter bars sit at their distance from the soma. A long help (the Pathway help is 2,862 characters) opens in two to four columns and stays on screen.
- **Removed (nothing reached it):** the auto-update switch and pending-cells machinery, refreshScene, `renderer.volumeScale`, `escapeHtml`, dead exports and about a dozen unused CSS blocks.

### Position shift: where each annotation was and where it is drawn (2026-10-15)

- **A new layer, off by default, in the cell panel's Position shift section and in Render compare.** It draws an arrow from where each annotation was to where it is drawn, so the section correction's moves (tenths of a micrometer, invisible in a surface) can be read. It adds a layer and changes no geometry, mesh key, `GEOMETRY_VERSION` or default. The pair is a select, **Between**: *Section correction* (where the volume's current transforms put it, then where Viking's correction puts it; the default), *Current transforms* (stored, then registered) or *Both*; a pair that was not applied or moved nothing is grayed with the reason, and the next one that can be shown stands in.
- **Arrows at annotations only, on purpose**: on c1178 a shift predicted from same-section neighbors within 3, 10 or 30 µm explains less variance than zero does, so a drawn field would show structure that is not there. Each head is on the annotation as drawn (for Section correction and Both); only the tail is exaggerated (Auto: 2.5 % of the cell's diagonal over the 95th percentile, ×13 on c1178; 1 to 100, Auto returns to it). An arrow whose true shift is already longer than 10 % of the diagonal is drawn at true length (RC2 c5597, 5598 and 5599 each carry one 158 to 160 µm correction on section 383). **Original rings** draws the annotation where it was (a closed curve's traced outline, moved the same way).
- **Every section or one slice.** *One slice at a time* shows the section and 5 either side (settable) with a plane; `[` `]` and Up/Down step one section, Shift or PageUp/PageDown ten, Home/End the first and last section with a shift. Up/Down are the slice's only while no annotation is selected (otherwise the walk along links, and the slice follows the annotation or second marker). A key step moves no camera, adds no undo step and writes nothing. A per-section chart, chips for the sections where annotations moved together (c1178 section 939: 6, mean −0.62, +0.65 µm), *Look down Z* and *Y down* (a new view; not checked against Viking's orientation).
- **A chip over the viewport** (`Shifts · ×13 · section 939 (84.51 µm) ±5 · 53 shown`, stepper, Off; with cells of two volumes each is named with its volume), a `shift 0.14 µm (+0.12, −0.08)` field on the pick readout, magnitude, direction or cell color, and *Through the surface* and Opacity. The layer is never picked and never in the scene's bounds (only Reset view re-frames); it follows the cell's visibility, volume offset, Z range, Flip Z and the orthographic camera, is saved with the cell, copied by Apply to, and undoable.
- **Render compare**: a *shift* box on every pane (Difference panes too) and one strip for the settings they share, one slice for all panes, keys on any pane step all; Compare again after a Registration change and the arrows follow the new copy.
- **The data behind it.** `/nodes` rows gain `live_shift_um`, how far the volume's current section transforms moved the annotation (um, rounded to a picometre, `null` when it did not move), beside `shift_um`. One copy of a cell now gives an annotation's three positions: stored = `xyz_um − live_shift_um − shift_um`, registered (where Viking draws it, and what Goto Location takes) = `xyz_um − shift_um`, corrected = `xyz_um`; both are `null` for a cell loaded with transform `None`, whose positions are raw pixels. The comment on `shift_um` called `xyz_um` minus it Viking's stored position; it is the registered one whenever the transforms moved the annotation too. On c1178 the identity holds to 3e-14 µm; 56 rows have `live_shift_um`; `/nodes` grows 2 kB gzipped.
- **`GET /api/neurons/{source}/{id}/shift-summary?pair=correction|transforms|both`** (honors `?copy=`): per pair how many annotations moved and by how much, the sideways step along the links between sections at each stage and how many links the pair shortened, lengthened or left within 5 nm, and the sections whose annotations moved as one (a stos edit, not a per-annotation correction). c1178: 2,428 links, median step 0.237 to 0.182 µm, over 1.5 µm 116 to 71, 65 % shrank and 29 % grew; 18 coherent sections, led by 939, 938, 1085, 940 and 937 (939: six annotations, mean −0.62, +0.65 µm). `sbfsem.analysis.shifts` holds the arithmetic; `tools/section_corrections.py` imports its `steps()` back and prints the same numbers.
- The Current transforms pair moves things by hundredths (c1178: at most 0.21 µm), so switching to it lowers "Only shifts above" until it shows something, and switching back gives yours back (within the page session). For that pair the arrow head is where Viking draws the annotation, which differs from the ring on screen by the section correction. The user guide's key table no longer lists the w a s d and q e pan keys, which do not exist (q and e roll).

### Dropdown lists drawn inside the page, for virtual machines (2026-10-15)

- **A new setting, In-page dropdowns (Display & context), draws every dropdown's list inside the page instead of in the browser's own popup window.** Reported from Chrome in a Windows 11 ARM64 guest under Parallels on a Mac: on Windows and Linux, Chrome draws a select's list as a separate window, which did not always draw there and could not be clicked. The dropdown itself is untouched (still the browser's control, still the element that has focus, with the value and events every part of the viewer already uses); only the list that opens from it is replaced, so all 31 dropdowns in the page, the ones the viewer builds later (technique and Difference panes in Render compare, a cell's finish, wireframe fit, synapse shape and correction) and any added after load behave as before. It is on by default in Chrome, Edge and other Chromium browsers on Windows, Linux and ChromeOS with a mouse or pen, and off on macOS, on touch screens (which keep the system list), in Firefox and in Safari; the box overrides that, is remembered in this browser, and is hidden where the browser is not Chromium. A page opened over plain http (a dev server reached by IP from the VM) has no `userAgentData`, so the browser is told from its user-agent string instead. If building the list fails, the browser's own list opens.
- Keys and clicks follow the browser's own. Space, Enter, F4 and Alt+Up/Down open the list; the arrows alone still step the value on Windows. In the list: arrows, Home, End, Page Up/Down, type-ahead, Enter, Space or Tab pick, and Esc closes only the list (also inside the Record a video dialog). A pick fires `input` then `change` once, as the browser's list does. Option descriptions (the surface finishes) show in the help panel; group headings, disabled and hidden options work; the list opens upward from the bottom toolbar, scrolls past 360 px, redraws when the options change under it, and closes when the dropdown moves or disappears, on a wheel over the page behind it, or a window resize. A click on empty space or the 3D view only closes it (it does not pick a cell), and the More menu stays open while a dropdown inside it is used.
- Tested: `tests/js/test_selectpicker.mjs` (the pure logic, run by pytest) and `tools/check_dropdowns.py` (real input in headless Chrome, against a running server). A headless run cannot see the browser's own popup, so whether this fixes the Parallels fault is for that machine to say; the user guide has "Dropdown lists in a virtual machine" with the setting and an ordered list of things to try, including a 30-second test of the cause. `appearance: base-select` was measured and turned down (docs/context/rejected-ideas.md).

### One declaration of every render option; the 3D render panel and technique pickers are built from it (2026-10-15)

- **Exports follow the closed-curve raster on screen.** `/export/{obj,stl,ply,dae,glb}` and `/mesh.glb` never took `pixel_size_um`, so a Closed curve or Hybrid cell shown at 0.25 µm was exported at the 0.05 µm default: another mesh, five to twenty times larger (c90000's closed-curve volume is 3,856 faces on screen and was 78,064 in the file; c1178 Hybrid is 304,480 faces at 0.25 µm and was 1,449,720, 24 s to build against 8 s). The routes now take it (same bound as the mesh route, default unchanged) and the viewer sends it. A build that cannot run (the raster over its voxel limit) is a 400 with the reason on every export route, as on the mesh route, not a bare 500.
- **`smooth_iterations` has one range, 0 to 50, on every route.** It was unbounded on `/mesh` and `/mesh-job`, 0 to 50 on exports and 0 to 10 on the diff, deviation and render-prefs bodies. The viewer's control stays 0 to 10.
- **Every render option is declared once** in `src/sbfsem/service/render_schema.py` (default, range, label, help, panel slot, and when it shows or greys out). The browser's copy (`render-schema.js`) is generated from it, and `tests/unit/test_render_schema.py` introspects the four hand-written server declarations against it, naming the knob and the declaration on any disagreement. CLAUDE.md gets ground rule 13.
- **The 3D render panel's 26 controls are generated from the schema** (`renderform.js`), with the same ids, labels, help, ranges, steps and defaults as before: all 112 method × pathway × MeshLab states and 48 screenshots are identical to the hand-written panel. Nine controls (Tie gaps, Fine voxel, Face cap, Junction fillet, Kernel R/d and the four Ultraliser knobs) never told Apply that they had been edited; they now do. Clear startup default no longer leaves stale knobs showing.
- **One list of thirteen techniques** feeds the bottom-bar picker and `sbfsem-measure --technique`. The picker gains Voxel remesh + MeshLab isotropic, quadric and Poisson (greyed out with the reason on a server without pymeshlab), and names the MeshLab mode: Voxel remesh with the panel's MeshLab mode on reads "Voxel remesh + MeshLab quadric" instead of "Voxel remesh" (in the list; the closed picker is narrow, so hover shows the full name). `sbfsem-measure` gains `voxel2_iso`, `voxel2_qem` and `voxel2_poisson`; the ten old names mean what they did (pinned by test). Render compare's panes do not use the shared list yet.

### The error kernel is exact and 2 to 40 times faster, and an error map says how its error arises (2026-10-15)

- **The distances behind an error map are faster on every cell measured, and identical to the last bit.** One kernel, `nearest_annotation`, now measures both the error map and the numbers in the legend, so the picture and the numbers are still the same distances. The discs are split by radius into classes, each with its own k-d tree and a certificate sized to its own largest radius (a few wide discs spoiled the single certificate for every point), and the closed-curve outlines start from the disc distance already found instead of the nearest outline vertex (median starting bound on c1178: 43.2 µm, now 0.31 µm). On a 200,000-vertex Voxel remesh the error map of RC1 c606 takes 4.3 s instead of 160 s, Inferior c1178 10.2 s instead of 412 s, RC2 c74420 3.5 s instead of 101 s, Inferior c19698 4.1 s instead of 21 s, RPC1 c2628 53 s instead of 93 s (3,978 outlines: 710 million exact point-edge pairs remain); the legend's annotation deviation takes 2 to 13 times less. Checked against the exhaustive loop in the tests, and against the previous implementation on those five cells: every distance, rim distance and legend number identical, max difference 0.0.
- **The error map needs more memory than before**, because it now also tests which side of the surface each rim point is on and summarizes the error: its peak above an idle process went from 329 to 672 MB on c19698, 704 to 786 MB on c1178 and 753 to 1,015 MB on c2628 (down on c606 and c74420). The hidden-surface and rim-side tests are bounded in memory like the distance kernels, so testing 49,562 rim points of c2628 takes +503 MB, not +1,287. The synchronous `POST .../mesh-deviation` shares the cached map and pays for the same extras (a few seconds on a large cell).
- **Polling a finished job stalls the server far less.** The final poll of a job ran FastAPI's `jsonable_encoder` over every number of its result in the event loop: 3.3 s for a 200,000-vertex mesh (14.5 MB) and 0.64 s for an error map, during which a health check waited 6.5 s. The body is now built in the endpoint's thread and is byte for byte the same: 0.60 s and 0.21 s, and a health check waits 1.1 s. A non-finite number in a result becomes `null`, as on every other route.
- **An error map says how its error arises (API; the viewer follows).** The result of `POST .../mesh-deviation-job` gains, additively and optionally: each vertex's gap along Z (`axial_um`; the sideways part is `sqrt(vertex_um² − axial_um²)`), which side of the render each displayed rim point is on (`rim_enclosed` and `bias`: the render is fatter where it encloses a rim point and short where it does not; a surface has no side because a disc is flat), the surface's error by area (`surface`: mean, percentiles, a histogram, and the floor a perfect render still shows between sections), a row per annotation with its Location ID and section (`annotations`), the error along Z (`z_profile`) and the places that carry the most excess volume (`spots`). About 4 to 5 MB for a 200,000-vertex mesh. Nothing that was there changes. On the Voxel remesh of the five cells the area-weighted surface mean is within 1 % of the legend's sampled `surface_um`; on a tube render, whose vertices lie on the annotations, it is lower (it is the mean of the painted field).
- `/api/mesh-jobs/{id}` progress: the disc stage counts passes, and the new stages are "rims: which side of the surface" and "summarizing the error".

### Closed curve fills its outlines faster, with the same pixels (2026-10-16)

- **A traced cell builds several times faster.** Nearly all of a traced
  cell's Closed curve build went into filling outlines: scikit-image tests
  every pixel of an outline's window against every edge, and registered
  outlines carry ~300 points each. Outlines are now filled row by row from
  where their edges cross each row, with scikit-image's own test evaluated
  only next to those crossings. Small rings, such as a disc-traced cell's
  circles, stay with scikit-image, which is quicker for them. RPC1 c2628 at
  0.05 µm (`sbfsem-measure`, old and new alternating on this Mac): 41.3 →
  7.0 s and 37.6 → 10.5 s, with the same 27 bodies, 3,409,540 faces, peak
  memory and deviations. Filling alone: c2628 0.05 µm 25.5 → 1.9 s, 0.1 µm
  7.1 → 1.0 s; RC1 c410 0.1 µm 1.1 → 0.6 s; Inferior c16137 0.25 µm 0.67 →
  0.61 s.
- **Not one pixel changes**, so no stored surface does. The rasters of those
  four builds hash identical before and after. The new fill matches
  `skimage.draw.polygon` on 3,600 random polygons in the tests (convex,
  concave, self-touching, sub-pixel, vertices on and within 1e-12 of pixel
  centers, edges along pixel rows, partly off the grid) and on all 19,026
  rings of those four builds, the small ones too when forced through it.

### Closed curve keeps its raster only where the outlines are (2026-10-15)

- **A wide cell fits a fine pixel.** The closed-curve raster was one array
  over the cell's whole bounding box, 300 million voxels at most, so Inferior
  c16137 (discs spread over 128 × 176 µm) fitted only 0.184 µm, and its
  processes under two pixels across were smoothed away: 38 bodies, 0.086 µm
  of the annotations unreached on average. The raster is now kept in 32 × 32
  pixel tiles under the outlines and contoured in blocks where they are:
  c16137 is **one body** at 0.1 µm (0.063 µm unreached, 95th percentile
  0.136 µm; 2.6 s, 348 MB) and at the 0.05 µm default (0.055 / 0.135 µm;
  7.1 s, 542 MB), where its box would be 4.0 billion voxels and its tiles are
  7.6 million. RC1 c410 now renders at 0.05 µm instead of being coarsened to
  0.062 µm.
- **Nothing that rendered before changes.** The pixels, and the mesh's vertex
  and face arrays, are the whole box's bit for bit (tested on the cone
  fixture, synthetic stacks with small tiles and blocks, and RPC1 c2628 at 0.1
  and 0.05 µm, RC1 c410 at 0.1 µm, c16137 at 0.25 and 0.184 µm), so no stored
  surface changes. The same pixel, before → after (this Mac, old and new code
  alternating): c16137 at 0.184 µm 6.6 → 1.1 s and 2,723 → 258 MB; c410 at
  0.062 µm 13.0 → 7.9 s and 2,895 → 523 MB; c2628 at 0.05 µm 42.5 → 38.5 s
  and 2,018 → 1,158 MB.
- **A limit on the surface instead of the box.** The box used to bound the
  mesh too; tiles do not. A raster with more than 3 million exposed voxel
  faces (1.4 to 1.9 triangles each) is refused before marching cubes, naming
  the finest pixel that fits, and the page coarsens to it as it does for the
  voxel limit (c410 at 0.025 µm → 0.027 µm, 37 s). RPC1 c2628's finest pixel
  moves from 0.042 to 0.044 µm: 4.15 million triangles at 1,375 MB, where
  0.042 µm as a box was 4.46 million at 2,797 MB.
- The job gate counts 50 bytes per allocated raster voxel (measured 32 to
  49), and at most 2.1 GB for a closed-curve build.

### Closed curve renders a cell traced with discs (2026-10-15)

- **Closed curve no longer fails on a disc-traced cell.** Inferior c16137
  (1,933 discs, no closed curves) came back "the build produced an empty mesh
  (Closed curve needs traced outlines)". A cell with no traced outline now has
  its discs stand in: each disc is its circle, at its center and radius, in its
  own section; each link between discs on different sections also draws, on
  every section it crosses, the stretch of the process within one section of
  that plane, so a dendrite that moves sideways more than its own width from
  one section to the next stays whole. Nothing is drawn above or below the
  first and last disc. Cells with any traced outline, and Hybrid, render
  exactly as before; no stored surface changes (DEVIATIONS §5.9).
- **Measured on c16137** at 0.183 µm, the finest raster its 128 × 176 µm box
  fits (the app coarsens the 0.05 µm default to it by itself): annotations
  0.08 µm unreached on average, 95th percentile 0.14 µm, max 1.4 µm, 46
  bodies, 20 s. Circles alone were 0.49 µm unreached in 133 bodies. Viking
  holds the cell in 7 pieces; most other breaks are processes under about two
  pixels across, which the closed-curve smoothing removes (3 bodies at 0.1 µm,
  three times the server's voxel limit). Voxel remesh stays the faithful view
  of a disc-traced cell: 1 body, 0.14 µm mean deviation.
- **Outlines rasterize on their own window, not the whole section**: the same
  pixels (tested), and c16137's 5,812 outlines take 1 s instead of 60 s.

### Render compare: error maps and differences are jobs with progress, and cannot time out (2026-10-15)

- **The 504 on a MeshLab pane is fixed.** Cause, from the production log: a
  pane's technique had been changed after Compare and *error* ticked without
  pressing Compare, so the browser asked the server to measure a mesh nobody
  had built. The server built it (c1178 through MeshLab isotropic: about 115
  CPU-seconds in MeshLab alone) and then ran the distance kernels, about 290
  CPU-seconds in all, inside one request; nginx answered 504 after five
  minutes and the pane threw away its good surface. Now an error map and a
  Difference are jobs on the same queue as renders (`POST .../mesh-deviation-job`
  and `.../mesh-diff-job`, polled on `/api/mesh-jobs/{id}`), so no request waits
  on them. The old blocking routes remain for the API.
- **Each pane shows what it is doing.** A progress card with the stage and its
  count (`contouring 1,609 of 2,512`, `MeshLab remesh`, `vertices: distance to
  outlines 14 of 130`), elapsed seconds, and Stop; "no answer for N s,
  retrying" while the server is unreachable. A failure is a persistent red
  banner with the server's message, the stage it failed in, Retry and
  Dismiss, and the surface stays on screen (a failed measurement used to delete
  it). Compare becomes **Stop all** while anything runs, and the toolbar reads
  "3 of 6 panes ready, pane 4 measuring, 41 s".
- **A pane measures only the mesh it holds.** Changing a technique or a knob
  marks the pane *out of date*; ticking *error* on it rebuilds it first through
  a mesh job ("Settings changed, rebuilding this pane") and then measures.
  Compare rebuilds only the panes that are not current.
- **The distance kernels cannot run the server out of memory.** They tested
  blocks of 20,000 points against every edge or disc at once: 1.7 GB against
  c1178's longest outline, about 8 GB for the disc fallback on RC1 c606, and
  the deviation of a 200,000-vertex mesh passed 6.5 GB. The block now follows
  a 192 MB budget; the distances are bit-identical (tested at a one-byte and a
  whole-stack budget, and the fallback is spied on to be sure it runs).
- Two requests for one mesh build it once, and one failure is reported to
  everyone waiting (they used to build it again, one after another).
  Measurements are cached per copy of the cell. A MeshLab render weighs 600 MB
  more at the memory gate, a measurement 1,500 MB, and the MeshLab ceiling is
  1,800 s (`SBFSEM_MESHLAB_TIMEOUT_S`) now that nothing waits on it.
- `/api/health` `builds.by_kind` counts queued and running jobs per kind, and
  the journal logs every finished job with its kind, cell, settings, duration
  and last stage (until now it had no durations).
- **The main viewer's renders poll through the same code.** Faster first
  answer (150 ms, then 1 s), the load strip says "no answer from the server
  for N s, retrying" instead of freezing for up to a minute, and a cell
  unloaded during a re-submit is not built.
- Found by an adversarial review of this change and fixed with it: Dismiss on a
  failure banner did nothing, Stop was ignored during the annotation fetch after
  a build, re-ticking *error* while a cancelled task was queued did nothing, the
  banner clipped at five or six panes below 1400 px, a double click on Compare
  landed on Stop all, and unloading the cell mid-Compare showed red failures
  for the user's own action.
- **The front end's tests now run with the rest.** 63 node tests
  (`tests/js`) pin the polling and the compare state machine, and
  `tests/unit/test_js.py` runs them in `pytest -q tests`, which `redeploy.sh`
  gates on; until now no gate ran them.

### Stop a batch load from the load strip (2026-10-15)

- **Stop**, on the strip over the viewport, ends a load (typed IDs, a link
  from Viking, a restored session) at once: no further cells start, and
  every cell of the load not yet drawn is dropped from the list, its server
  copy unloaded and its queued build cancelled. Cells already on screen stay.
  A build already running on the server cannot be interrupted; it finishes
  and its surface is cached. Asked for in #ai-requests on 2026-10-14. Tested
  on a 21-cell Inferior load: Stop dropped the 6 cells in flight, cancelled 5
  queued builds (the 6th had started and finished in 17 s), and kept the 2
  drawn. The strip says "Stopped" without waiting for the annotation fetches
  still in the air (one ran on for over 15 s); their answers are dropped.
- **Unloading a cell in one tab no longer cancels another tab's build of the
  same surface.** Identical requests share one build; the first cancel used
  to stop it for everyone still waiting, who were left with no surface and no
  error. A queued build now stops only when the last request waiting on it
  lets go (1 of 1 test fails on the old code).

### "Edited in Viking" from one request per volume, and surfaces keyed on Viking's own freshness (2026-10-15)

- **One request per volume instead of one per cell.** Viking's new
  `StructureFreshnessBatch` (James Anderson, 2026-10-15) answers a cell's
  newest edit and its number of annotations for any number of cells. A batch
  load or a restored session now asks once, after the cells are in, instead
  of once per cell during the load. 200 RC2 structures: 0.51 s and 2 requests,
  against 4.9 to 5.2 s and 200 requests before; 12 cells over 7 volumes:
  0.17 s against 0.46 to 1.12 s.
- **Deleted annotations now count as an edit.** The check compared only the
  newest edit date, which a deletion never moves; it now compares the count
  too.
- **An edit made within 7 hours of a fetch was missed.** Viking used to label
  its UTC times as Pacific and now labels them UTC, so a copy cached under the
  old label read 7 hours late against a live answer. Dates are now read as the
  UTC digits Viking stores, whatever the label; the ↻ tooltip shows the right
  local time as well. Both misses checked against the old code: 2 of 2.
- **Synapses are checked too**, by annotation count: a cell whose child
  structures gained or lost annotations is marked, and its tooltip says its
  own annotations, and so its surface, did not change. A child edit that
  keeps the count is not seen: Viking's date for the children also covers
  structure rows we do not fetch (Temporal c108 read 28 minutes "newer" than
  any of its annotations right after a fresh fetch).
- **Surfaces on disk are keyed on the cell's own annotations exactly as
  Viking's `StructureFreshness` names them** (newest edit, UTC, and count).
  A synapse edit no longer looks like a new surface, a cell loaded with or
  without its synapses shares one entry, and the same edit under Viking's old
  and new time labels is one key. Checked on 13 cells across 7 volumes: the
  loaded and live answers agree exactly. Surfaces stored under the old key are
  still found and stored again under the new one, so nothing rebuilds for it.
- A volume without the new function falls back to the per-cell check.

### Export the annotation wireframe (2026-10-14)

- **Right-click a cell → *Export annotation wireframe*,** offered while the
  cell's annotation wireframe is on. It writes what is drawn, with the
  panel's settings (Averaging, Fit, Size tolerance, Break at, Lines along,
  Ring spacing), to `c{id}-annotation-wire.{ext}` in the mesh wireframe's
  formats less SWC: OBJ and PLY line elements, STL rods, CSV/TSV edge tables,
  NumPy `.npy`/`.npz` and HDF5. OBJ, PLY and HDF5 record the volume, the cell
  and the settings. On Inferior c943 (Mean fit, 0.5 µm ring spacing) the file
  holds 2,631 segments, the 802 ring, 1,673 line and 156 tie segments on
  screen. *Export wireframe* (the surface's triangle edges) is unchanged.
- A submenu low in the window now rises to stay on screen; the exports, last
  in the menu, opened past the bottom edge of a short window.
- New endpoint `/api/neurons/{source}/{id}/annotation-wire/export/{fmt}`; the
  library's `write_wireframe_*` writers take `(S, 2, 3)` line segments as well
  as a mesh.

### An annotation wireframe, averaged along Z by the cell's own scale (2026-10-14)

- **New per-cell section, *Annotation wireframe*.** A wireframe made from the
  annotations alone, with no surface: every disc and closed curve as a ring in
  its section plane, lines along the linked annotations, and ties where a
  branch leaves another annotation. Each ring is averaged with its neighbors
  along Z over a window of *Averaging* times its own radius (0.50 by default):
  a soma ring over microns, a thin dendrite ring over a section or two. Rings
  stay in their section planes, chain ends (a soma's top and bottom, a
  dendrite's tip) stay as annotated, and a link across a radius jump of more
  than 2.5 is a tie, so a soma never averages in the dendrite that leaves it.
- **The fit is a local quadratic, not a mean.** On Inferior Monkey c1178's
  soma (170 rings) a mean held the equator about a micron inside the
  annotations; the quadratic follows the dome, keeps 90 % of the rings within
  8 % of their annotated size, and takes the section-to-section wobble in ring
  size from 0.069 to 0.004 µm. *Mean* stays selectable.
- Measured at the defaults on Inferior c1178 and c19698, RC1 c606, RC2
  c74420 and RPC1 c2628: soma rings (r ≥ 3 µm) moved 0.18 to 0.93 µm at the
  median, rings under 0.3 µm 0 to 0.014 µm. c1178 moves most: its soma's
  outlines step about 4 µm sideways for some 25 sections and back. About a
  second to build on the largest (10,518 annotations).
- Surfaces, the triangle wireframe and the raw annotation rings are
  untouched; nothing about how a surface is built changed (no
  `GEOMETRY_VERSION` bump). New endpoint
  `/api/neurons/{source}/{id}/annotation-wire`.

### Working rules: a rejected-ideas log, a failing-test gate, a bug checklist (2026-10-09)

- Developer process only; nothing in the app changes. `docs/context/rejected-ideas.md`
  records ideas measured and turned down (annotations as spheres, tuning swept
  tubes against fold-through, the residual field as default), each with a
  "do not retry unless" line; `/measure` reads it first. `/ship` now requires a
  bug fix's test to fail against the old code, with the count in the commit.
  `.claude/rules/common-bugs.md` lists the bugs this codebase makes: axes,
  coordinate frames, units, precision, OData time, IDs without their volume.
  Adopted from the practices [Viking-Server] shared in `#ai` on 2026-10-08.

### Cells stay in view while a large load arrives, and the Loaded actions stay at hand (2026-10-09)

- **Cells were cut away by the far clipping plane during a big load.** The
  far plane was sized only when the view was framed, which a batch load does
  for its first cell and again at the end, so a 177-cell import showed cells
  vanishing as the view turned until the load finished. It now follows
  everything drawn (cells, rings, markers, boundary sheets) from wherever the
  camera is, every frame, as the near plane already did; the camera never
  moves for it, and a held view (Display's *Hold*) keeps its planes growing
  too. Checked: geometry added 50,000 µm out pulled the far plane from 17.7 to
  62,500 µm on the next frame, and it shrank back when that geometry left.
- **Select all / Select none / ↻ / Clear all cells sat under the whole list.**
  They now sit beside the *Loaded* heading (just under it in a narrow
  sidebar), and the heading stays at the top of the sidebar while hundreds of
  cells scroll under it.

### A cleared tab reloads empty, and Flip Z mirrors the volume context (2026-10-09)

- **Reloading a tab you had cleared brought back other people's cells.** A
  tab with no cells saved fell back to the server's shared list, and the
  reload the new-version notice offers pulled that list in without asking.
  A cleared tab now comes back empty. The shared list is offered (*Show
  them* / *Start empty*) only to a browser with no saved session at all,
  and never loaded unasked.
- **Flip Z left the INL/GCL boundaries and the vasculature unflipped.** They
  sit in their own group so framing ignores them, and the mirror reached
  only the cells. Both now flip together.

### Cached Viking structure corrections survive a restart of the correction service (2026-10-08)

- Viking's correction service 1.1.0 names its version (`service_version`).
  The `CorrectStructures` cache is now keyed on it rather than on when the
  service last finished a rebuild, which moved on every restart and made
  every cell refetch its curve-fit correction. A new version still drops the
  cache, as an algorithm change should. Older services fall back to the
  rebuild time.

### No limit on how many cells one load brings in (2026-10-08)

- **The 200-cell cap is gone.** The Structure ID box and Find's
  *Add all* / *Add selected* refused any request over 200 cells
  ("… at a time is the limit"). They now load whatever is asked; the
  cells still arrive a few at a time, as before. A single dash range is
  still capped at 10,000 IDs, as in Viking's export portal.

### Viking sign-in works again with Identity 1.1.0, and GlialAdjacency has a name (2026-10-08)

- **Viking sign-in was down.** Viking's Identity 1.1.0, deployed 2026-10-08,
  answers `Permissions/AccessibleVolumes` with 404, so every Viking sign-in
  ended on "The Viking Identity Server could not be reached" although the
  server was up. The volume list now falls back to
  `Permissions/UserAccessibleVolumeTree`.
- **Volumes named after their annotation server.** A volume Viking calls
  `RC1-Internal` or `RC2-Internal` is opened as RC1 or RC2 through its
  `AnnotationServerName`, and two volumes on one server merge their
  permissions.
- **An empty recheck keeps the volume list.** The 5-minute permission recheck
  no longer empties the volume menu when the answer suddenly has nothing we
  can open; it keeps the last good list, as it does when Viking is
  unreachable. A first sign-in whose volumes are all ones this tool cannot
  render now says so instead of "no volumes have been shared".
- **Viking types 257–270 have names.** `GlialAdjacency` and the 13 other
  Marc-lab types (`DistalJunction`, `FlatContact`, `PeriGJAdherens`, …) are
  their own rows with their own colors instead of falling under Unknown
  (DEVIATIONS 5.8). Requested for RC1 and RPC1.

### A building cell shows its annotations, and Hybrid renders stop holding up other people's (2026-10-07)

- **Dots while a cell builds.** Its annotations appear as small points in its
  color until the surface replaces them, so the viewer is no longer empty for
  the length of a build. Cells the server has already built (back within
  600 ms) never show dots. A first load frames its first cloud so it is on
  screen. The user's decision 1 of the load-progress plan.
- **The memory estimate for outlines is fixed.** The gate log from 2026-10-06
  showed an RC2 Hybrid (c5624, 8 outlines, measured peak 933 MB) estimated at
  218,190 MB. Anything over the 9,000 MB budget runs alone, so another
  person's cell waited 30.2 s. The estimate sized the outline raster from the
  whole cell; it now counts the grid the build allocates (1,063 MB for
  c5624), and a Hybrid counts only its discs toward the disc half (c74420,
  no outlines: 17,295 → 3,575 MB, measured 1,736 MB).
- Steps 4 and 5 of the plan (box ghost; batch admission) are on hold.

### A cell's build says how far it has got (2026-10-07)

- **The load strip names the build's stage.** Before, a building cell read
  only `building 14 s`. Now the build itself reports, and the strip shows
  `contouring 1,609 of 2,512 · 2 s`, then tying gaps, smoothing, decimating
  (rasterizing outlines for traced cells, sweeping tubes for the tube
  techniques). Step 3 of `docs/context/progress-plan-2026-10-06.md`.
- **How it travels.** Geometry calls a no-op hook (`sbfsem.progress`) unless
  someone listens; the mesh job listens. Production builds in four worker
  processes, which send their reports back on one queue, at most four a second.
  `/api/mesh-jobs/{id}` carries `progress: {stage, done, total}` while running.
  No geometry changed.
- **Measured on production** (a cold build of Inferior Monkey c19698 through a
  worker process): contouring 0 → 1,000 of 1,000 blocks, smoothing, decimating.

### Every SWC says how it treats the soma (2026-10-07)

- **A `SOMA` header line.** Rebecca Pfeiffer found four different soma
  treatments for one cell: the SWC takes nothing off, the Morphometrics
  dendritic length takes 5.6 µm (links between two soma rows), the Skeleton
  check's rim-corrected length takes the spokes, and her pipeline takes 18 µm.
  The file now says which one it uses, so nobody corrects a figure that has
  already been corrected: the cutoff (`soma_fraction` of the largest stored radius,
  and the Location that radius belongs to), how many rows are typed 1, and that
  the typing moves and drops nothing (c192: cutoff 3.582 µm, 37 rows).
  `SOMA none` with `has_soma=false`.
- **The width ceiling for lamella chains, measured instead of built.** Her
  nine cells give 4,953 chained profiles with a single smooth short-axis mode
  (median 0.67 µm) and no gap. A 1.5 µm ceiling removes 318.5 µm of chain but
  moves the written length only +3 µm. Chains at branch points are 39 % of
  the profiles and no wider than the rest. RC1 514, 2610 and 3679 have almost
  no closed curves. The full write-up is `docs/context/to-becca-skeleton-2026-10-07.rst`.

### Loads show their progress over the viewport, and the build gate keeps a record (2026-10-06)

- **The load strip.** James Anderson asked (`#ai-requests`, 2026-09-26) to see
  that a link or a long list was received and how far it had got; a 12px
  counter in the status corner was all there was. Every load now shows a strip
  at the top of the viewport: `Loading 37 of 207 cells`, a bar, and each cell
  in flight (six at most) by ID and label with its stage — fetching
  annotations, queued on the server, waiting for server memory, building N s,
  drawing. A single cell, a Viking link and a restored session get it too;
  overlapping loads add up. It goes red if any cell failed and hides 2.5 s
  after the end (6 s with failures).
- **The memory gate is logged.** A queued build now says whether it waits for
  a worker thread or for memory (`waiting` in `/api/mesh-jobs/{id}`). Each wait
  at the gate is written to the journal when it starts and ends, and
  `/api/health` reports `gate_blocked`, `gate_waits`, `gate_wait_s` and
  `gate_wait_max_s`. Nothing about admission changed; this is the record that
  decides whether batch admission (step 5 of the plan) is worth building.
- **The plan** for the rest (a progress channel out of the build, a box ghost
  that fills in, batch submission with fair admission) is written up in
  `docs/context/progress-plan-2026-10-06.md`, awaiting decisions.

### A cell traced with outlines no longer stays blank after picking Hybrid or Closed curve (2026-10-02)

- **The report.** Becca's RC1 c410 (discs plus outlines) loaded as Hybrid at
  0.25 µm for days. On 2026-10-01 she picked Closed curve and then Hybrid from
  the technique picker, which reset the pixel to the 0.05 µm default: 476 M
  voxels against the 300 M limit. The cell saves its render in the browser, so
  it failed on every load, and the banner's advice (a Defaults button) chose
  0.05 µm again.
- **The picker and the Defaults buttons** now give such a cell the pixel a
  fresh load would (0.25 µm, 1 µm for vessels) when the technique's pixel is
  the untouched default. A pixel the user typed or saved is kept.
- **A pixel still too fine** is coarsened once to the finest one the server
  says fits (c410 at 0.05 µm: 0.063 µm), and the cell renders.
- Both changes are announced for 15 s in a notice at the viewport's upper
  right, with the new size in bold.
- **The banner** now names the pixel size to set when the size limit is
  what failed.
- **The Voxel dropdown** offers 1 and 0.25 µm, and shows any other size in
  use. It listed only 0.1, 0.05 and 0.025, so a cell at 0.25 µm showed a
  blank box, which read back as 0 and was clamped to 0.01 µm, the finest
  there is, on the next Apply.

## 2.3.0 — 2026-09-28

### Two tabs with different settings no longer change each other's cell (2026-09-28)

- **The bug.** Registration and correction settings are per tab, but the
  server held one copy of each cell for everyone. A second tab (or a second
  person) loading c21768 with the curve-fit correction off replaced the copy the
  first tab was drawing with the correction on: that tab's next request for
  annotations, skeleton or surface got the other setting's cell.
- **The fix.** The server keeps one copy of a cell per setting in use,
  `<correction mode>.<0|1 for current transforms>` (`curvefit.1`, `off.0`).
  A load answers with its copy's `copy_tag`, and the page names it back
  (`?copy=`) on every request and download link for the cell. A request
  that names none is answered from the copy loaded most recently, as before.
  Unloading a cell in one tab removes only that tab's copy.
- **Switching back is instant.** A setting already held is answered from
  memory, and its built surfaces stay cached while another setting's copy is
  loaded.
- **Checked** in two browser tabs on c21768: one with the curve-fit correction
  (1,461 annotations moved), one without. Each kept its own positions after
  the other loaded, and every request carried its tab's copy.

### Open in Viking from a closed Viking reaches the Location (2026-09-28)

- **Why.** Since Viking 1.2.67, the annotation service refuses the token that a
  launch code is exchanged for. A cold start by code therefore opened a Viking
  that could load nothing and reported the Location "not found". Viking's
  server fix has been pending since 2026-09-21.
- **The stopgap.** The link now goes straight to the client:
  `viking://open?volumeName=…&location=…`, with no launch code.
  - A Viking that is already open takes it as before.
  - A cold start shows Viking's own sign-in (it remembers the account), then
    selects the volume and goes to the Location.
  - This was read from Viking's Legacy source: `Program.cs` and `LoginWindow`.
- **Switching back.** `SBFSEM_VIKING_LAUNCH_MODE=code` restores the identity
  server's `CreateCode`, for the day Viking's server fix is deployed.

### Skeleton exports: lamella chains, sphere sampling and the modal radius (2026-09-25)

Rebecca Pfeiffer said yes to all three export options offered on 2026-09-22,
each as an option and not the default, so that a figure can name which file it
used (`docs/context/from-becca-skeleton-2026-09-23.rst`). None changes the
default file's rows.

- **`swc_lamellae=true`** writes each lamella stack as the chain along it, in
  place of its annotations (`sbfsem.analysis.lamellae.chain_graph`). Her
  answer to the open question: chain beyond the outermost branch is cable, so
  a chain's ends are tips. Each link that left the stack joins the chain node
  nearest the annotation at its other end; a chain node's R is half the local
  width. The header gives the rule (`LAMELLA_RULE`), the totals (`LAMELLAE`),
  and each stack's Location IDs, sections and rows (`LAMELLA`). Not with
  `swc_relax` (400).
  - On RPC1 c192 as of 2026-09-21, the file is 3,456 rows and 918.94 µm,
    against 2,849 rows and 764.36 µm. That is +154.03 µm, not the +48.5 of the
    Skeleton check. The check counts no link inside an outline. The file has
    to join each chain to the processes that continue above and below its
    stack: 135.52 µm of joins, 109.10 µm of them inside the outlines.
  - The check's lamella rows and summary now also give what a file written
    with the chain gains (`lamella_written_net_um`, `lamella_joins_inside_um`).
- **`swc_sampling=sphere`** keeps only the rows of Bradley & Withers's minimal
  sphere network (2016, eq. 1). Along each unbranched path, from a kept row,
  the next is the farthest *by distance* of the unbroken run of rows whose
  spheres of radius R touch it. Tips, branch points, roots and both ends of
  each dropped link are always kept. c192: 1,167 rows, 688.39 µm.
  - The 1,536 nodes and 675.05 µm sent on 2026-09-22 came from a script that
    counted every branch point and tip once per path it ends. That script also
    took the farthest touching *index*, run unbroken or not. By that rule the
    distinct count is 1,115 (hers: 1,122).
- **`swc_radius=modal`** puts a closed curve's modal radius in R, and each
  closed curve's stored radius in the header as `R_STORED row r`.
  - The modal radius is measured from the center of the outline's largest
    inscribed circle. It is the first peak, at or above that circle's radius,
    of the outline's length at each distance over the distance, smoothed by
    0.02 µm (`sbfsem.analysis.thickness`).
  - A circle reads its radius and a 1 µm slab 0.515 µm. On c192 it is a median
    0.66 of the stored radius: 0.84 on round profiles, 0.59 on lamellae.
  - The six figures sent on 2026-09-22 were measured from the area centroid,
    and two were not thicknesses. Location 98799's centroid lies outside its
    own outline: it read 2.26 µm and now reads 0.52. Location 104848 read 1.47
    and now reads 0.29.
- **Every SWC header** now says when the cell's annotations were last written
  (`ANNOTATIONS_STAMP`), what the rows are (`SAMPLING`) and what R holds
  (`R_COLUMN`). `OPTIONS` names every option, defaults included. Rows and
  every other header line are byte-identical to before, checked on c192.
- **The lamella rule itself**, as the Skeleton check measures it:
  - A chain is walked by the paper's eq. 1 (it was the farthest touching
    index), which adds 2.0 µm on c192.
  - Each chain node sits at the mean section of the profiles containing it
    (it was the stack's mean). A tall stack's links no longer climb half the
    stack; this adds 3.2 µm.
  - c192's net: +48.51 → +53.66 µm.
- **Bulk archives draw every cell the same way.** A cell loaded in the viewer
  went in as loaded (on the web app, through the current section transforms
  and the curve-fit correction). The rest went in at their stored positions.
  So one archive could hold both. Now every cell of an archive follows
  `live_registration` and `section_corrections`, or the server's defaults
  when they are absent. A loaded cell drawn another way is fetched again. The
  manifest adds `swc_rows` and `swc_length_um` (the file's own), and its
  `options` lists the new options.
- Found while testing: chain nodes were numbered from −1, which is also an
  SWC root's parent, so a branch hung from that node was written as a root.
  They are numbered from −2.

### A page load lists the cells it would bring back, and waits (2026-09-24)

- **What you see.** Coming back to the viewer shows a banner listing the
  cells your last visit left, by volume, before any is fetched: **Restore** or
  **Discard**. The volume and panel settings come back either way. Until you
  choose, the list is kept, so a tab closed without deciding loses nothing.
  The reload the redeploy banner offers still restores without asking.
- **Why.** James Anderson opened sbfsem-tools.com, switched to RC1, and
  watched cells he had not asked for fill the list one by one ("it would be
  nice to see the list first so I can cancel before they appear"). They were
  his own, restored from his browser's saved session, but nothing said so.
- **With no saved session**, the viewer used to take on, unasked, every cell
  loaded on the server by anyone. That list now gets the same banner: **Show
  them** or **Start empty**. Otherwise a Discard would come back on the next
  visit, because the server still holds those cells.
- **Viking volume gate.** `GET /api/neurons`, the one listing that names no
  volume, now shows a signed-in Viking user only cells of volumes they may
  open. Before, it listed every volume's cells; opening one was refused.
- In full screen, the banners no longer sit under the floating ≡ Cells button.

### Cells are drawn with Viking's curve-fit section correction (2026-09-24)

- **What you see.** Each cell now moves by Viking's new section correction, on
  top of the current section transforms: a leave-one-out curve fit along each
  traced process, from Viking's corrections service (`CorrectStructures`). On
  c21768 the median sideways step between linked annotations on adjacent
  sections went from 0.194 to 0.125 µm, the 90th percentile from 0.647 to
  0.439 µm, and the steps over 1.5 µm from 39 to 19. The unfinished stos step
  at 938→939 went from 3.28 to 1.03 µm.
- **Switching back.** In the cell panel's Registration section, untick
  **Viking's section correction** to see the uncorrected positions.
  **Correction** picks Curve fit, Residual field (the old switch, off by
  default since it measured worse on 2026-09-23), or Field + fit (on c21768:
  median 0.127 µm, 18 steps over 1.5 µm).
- **Synapses** are not part of a process and keep their place under the curve
  fit. On c21768 a synapse's distance to its cell's nearest annotation on the
  same section changed by a median 0.04 µm (95th percentile 0.25, max 0.64).
  An annotation Viking's service places more than 20 µm from where the viewer
  has it is not moved. Until 23:19 UTC the service mapped lines and curves with
  empty section X and Y (113 of c21768's 1,680 Locations, all synapses) from
  section (0, 0), about 200 µm away. It now maps them from their outline's
  centroid: 109 of the 113 land within 1 nm of the viewer's placement.
- **When the service fails**, the cell is drawn uncorrected and the
  Registration section says why. A failure inside the service holds that
  volume and correction for ten minutes. On 2026-09-24 every RC1 call threw
  after 5-15 s, and before the hold every RC1 load waited that long.
- **A redeployed service is asked again (2026-09-25).** Viking changed what
  the curve fit returns at 05:56 UTC: RC1 c180's steps over 1.5 µm with it on
  went from 64 to 1. No correction set was rebuilt, and answers kept on disk
  were keyed on the set alone, so a cell cached before then would have kept
  the old spikes. They are now also keyed on when the service last finished a
  rebuild, which happens every time it starts.
- **API and library.** `section_corrections` takes a mode (`off`,
  `curvefit`, `neighbor`, `all`; `true` still means `neighbor`), and the
  summary's `section_correction.mode` says which was applied. The library's
  default is unchanged (off). `correct_neuron(mode=...)`,
  `CorrectionsClient.correct_structures`, and `--mode`/`--live` in
  `tools/section_corrections.py`.

### A cell loaded again is drawn from its new data, surface included (2026-09-23)

- **The bug.** Ticking or unticking a Registration checkbox, or Update from
  Viking, loads the cell again. Its annotations and skeleton were redrawn from
  the new data, but the surface was not. The server keeps a finished mesh
  build for ten minutes and handed it to any request with the same render
  settings, so the cell got back the surface built before the change. On
  c21768 the annotations moved up to 10.95 µm and the surface did not move at
  all.
- **The fix.** Each copy of a cell the server loads is numbered, and a mesh
  build belongs to the copy it was made from. A build is shared only between
  requests for the same copy, and a late build of an old copy is never served
  for the new one. The disk cache of built meshes is unchanged, so a setting
  already built comes back at once.
- **Quick clicks no longer race.**
  - The page sends one cell's loads one at a time, in order.
  - A click builds on the one before it, even if that one has not finished.
  - A redraw still under way for an earlier load stands down, instead of
    landing its old surface last.
- **Checked on c21768 in the browser.** Transforms off; then the correction
  field on; then the field off and transforms on, 0.4 s apart. After each
  step, the drawn surface and rings match the server's exactly. The rings sit
  on the surface: median gap −0.06 to −0.07 µm, largest 0.32.

### The annotators panel finds bulk edits the cell alone cannot show, and reads Viking live (2026-09-22)

- **Sweeps are bulk edits too.** Viking's Update Volume Positions, run on
  Inferior Monkey from 2026-09-23, reaches each cell a few annotations a
  minute, often under one username. The panel's test (twenty writes a minute
  under two names) cannot see that: c62676's 175 rewritten annotations read
  as 179 revisions.
  - The new test splits the cell's later writes into sessions: writes no more
    than 5 minutes apart.
  - For each session, the panel asks Viking how many annotations of other
    structures, more than a day old, were rewritten in the same window. The
    cell's own rows and its children's are taken out of that count.
  - At 20 a minute or more, the session is a bulk edit. Measured on Inferior
    Monkey: 38 to 2,336 a minute in bulk sessions, 0 to 4 while people were
    editing.
  - Revisions before → after: c62676 179 → 4, c127943 147 → 0, c1178 424 →
    322, c2123 182 → 10. Its 2023-05-31 re-registration had also slipped past
    the old test.
  - The cost is one count query per session: 0.1 to 0.9 s for these cells.
- **Bulk edits are counted as operations.** A cell's bulk writes less than 30
  minutes apart are one bulk edit, whichever test found them.
- **Live, as documented.** The panel read Viking through the disk cache, which
  keeps a query's first answer for good. It showed a cell as it stood the first
  time anyone opened its panel. It now reads Viking every time.
- **Viking's timestamps read 7 h late** (8 h for winter dates), in this panel
  and in the "edited in Viking" tooltip. Viking stores UTC, and its OData
  service labels the digits with its host's Pacific offset. Ordering is
  unaffected. `[Viking]` is changing the service to label them UTC.
- Library: `ODataClient.count_rewrites(start, end, created_before)` and
  `annotator_report(..., volume_rewrites=)`.

### Cells are drawn where Viking's current section transforms put them (2026-09-22)

Why the user's fixes to Inferior Monkey never showed here, and the fix.
Viking draws an annotation by mapping its section coordinates through the
volume's current stos transforms. The OData columns this tool drew from
(`VolumeX`, `VolumeY`, `VolumeShape`) are that mapping as it stood when each
annotation was saved; a stos edited later leaves them behind until VikingAU
rewrites them, and no stos edit triggers VikingAU (`[Viking]`, #ai,
2026-09-23). On 2026-09-22 every Inferior Monkey stos from section 937 to 1835
was rewritten (937–1258 at 20:37–20:38 UTC, 1259–1835 at 21:40–21:43), so none
of it reached this tool.

- **Positions now come from the volume's own files, as Viking's do**: its
  VikingXML lists one stos per section, and each annotation's section
  coordinates are mapped through its section's stos (mesh and grid transforms,
  read as Viking's `TransformFactory.ParseStos` reads them). A line annotation
  (a synapse) keeps no section coordinates of its own; its position is derived
  from its mapped shape as `VolumeX` is from `VolumeShape`. Closed-curve
  outlines move with their annotation. `VolumeX`/`VolumeY` keep Viking's
  stored values, and each annotation's move is in `LiveDXnm`/`LiveDYnm`.
- **Checked against the stored positions where they must agree**, on sections
  whose stos is older than the annotation: Inferior Monkey c21768 to 0.005 µm
  (1,132 annotations), Cped c934 to 0.002 µm, RPC1 c192 and RPC2 c2968 to a
  median of 0.
- **c21768** (Inferior Monkey): 548 of 1,681 annotations move, on sections
  937–1472, median 0.62 µm, largest 11.26. The jumps the user fixed close:
  936→937 from 3.23 to 0.11 µm, 1317→1318 from 7.92 to 1.79. Across every
  structure nearby, 936→937 goes from 2.80 to 0.25 µm (64 links within 40 µm)
  and 1317→1318 from 7.64 to 1.15 (17 links within 50 µm).
- **The user's edit is not finished**, and this shows it as Viking does: 938→939
  now steps 2.72 µm (+1.48, −2.28) for every structure nearby, where it stepped
  0.11. Sections 937 and 938 moved about (−1.8, +2.5) µm, 939 and above only
  (−0.15, −0.57). 1318→1319 gained 1.49 µm.
- **Stale positions elsewhere are corrected too.** On RPC1 section 885 (stos of
  2018-12-07) the 27 annotations saved before 2019-02 move a median
  (−0.59, −0.10) µm and the 73 saved since do not move. That includes c2628's
  outline 107463, moved (−0.57, −0.17) µm: the sideways jump reported to
  James Anderson on 2026-09-19. RPC1 c192: 592 of 4,175 annotations move, at
  most 0.52 µm.
- The cell panel's section is now **Registration**, with two switches:
  *Viking's current section transforms* (on) and *Viking's residual
  correction field* (off). Each lists what it moved. The header reads
  `548 moved, max 11.26 µm`, `current` (nothing moved), `stored positions`, or
  `stored (unavailable)`. A refresh (↻) also re-reads the cell's stos files;
  otherwise they are re-checked whenever the VikingXML changes.
- Volumes read: Inferior Monkey, Cped, RPC1 and RPC2. RC1's registration is
  chained (sections registered to their neighbors) and is not composed here,
  Nasal's VikingXML lists no stos, Temporal's answered 404, and RC2's stos
  paths answered 404. Those draw the stored positions, and the panel says so.
- Files are fetched in parallel and kept in the cache directory; a cold cell
  of 852 sections loads in about 4 s. A cell whose files take longer than
  45 s is drawn at its stored positions, with a note, while the fetch finishes
  in the background.
- SWC headers say so (`SECTION_TRANSFORMS current …`,
  `POSITIONS viking current`); JSON exports carry it (`liveRegistration`).
  The library reads stored positions unless asked (`sbfsem.io.registration`),
  as MATLAB did. `SBFSEM_LIVE_REGISTRATION=0` returns the web app to the stored
  positions.

### Section corrections are opt-in: the live field made Inferior Monkey worse (2026-09-22)

The corrections service answered from 02:03 UTC, publishing eight sets (cped,
InferiorMonkey, RC1, RC2, RPC1, RPC2, RPC3 with no sections, TemporalMonkey;
none for Nasal). Measured against Inferior Monkey's (`SliceToVolume1`, built
2026-09-22 07:54 UTC), the field does not do what it is for:

- **c21768**: 1,226 of 1,681 annotations have support and move (median
  0.10 µm, largest 1.65). The step along a link between sections goes from
  median 0.194 to 0.185 µm, p90 0.632 to 0.650, p99 1.803 to 1.856. The 7.92 µm
  jump at 1317→1318 has no support and stays. **The 3.23 µm jump at 936→937
  grows to 4.83 µm.**
- **Its neighborhood** (1,618 annotations within 40 µm, Z 928–946, 878 links
  with both ends supported): the mean step goes from 0.375 to 0.578 µm, p90
  0.82 to 1.36, p99 3.6 to 5.6. Reversing the sign is worse too (mean 0.723),
  so this is not a sign slip. The field and the stored positions disagree
  about where section 937 is: every structure steps (+1.71, −2.69) µm from 936
  to 937 and nothing from 937 to 938, while the field moves 937 by
  (+1.17, −1.21) relative to 936 and by (+1.38, −2.37) relative to 938.
- So the web app now draws Viking's stored positions unless a cell's
  **Section corrections** switch is ticked (`SBFSEM_SECTION_CORRECTIONS=1`
  restores the old default). Production had applied it to one cell load,
  c21768 at 02:04 UTC, before this change.

### Viking's section corrections (2026-09-22)

The user: in Inferior Monkey c21768 the render jumps sideways between some
sections, and no correction made to the volume in Viking ever reached this
tool. James Anderson pointed to the service his agent `[Viking]` had pinned in
Slack #ai, *Section corrections gRPC*.

- **A cell is drawn with Viking's published section correction** wherever its
  volume has one. The service (`corrections.codepharm.net`, gRPC) publishes,
  for each section, a 2 µm lattice of XY offsets measured from every traced
  process in the volume: each annotation against the curve through its
  neighbors above and below, pooled over 8 µm, published where at least three
  annotations agree. Every annotation of a loaded cell (its own, its child
  structures', each closed-curve outline) moves by the offset at its own place
  on its own section, and only where the field has support. That is what
  Viking's own morphology export does (`ApplyPublishedResidualField`).
  `VolumeX`/`VolumeY` keep Viking's values; each annotation's move is in
  `CorrDXnm`/`CorrDYnm`.
- **The cell panel has a *Section corrections* section**: the switch (on by
  default until 2026-09-23, see above), the set and when it was built, how many
  annotations moved and how far. Off draws Viking's stored positions. The pick readout's Viking
  coordinates take the move back, so they still paste into Viking's Goto
  Location at the annotation. So do those in the linked-neurons worklist of
  postsynapses to fix; each row gets `synapse_shift_um`.
- **c21768, measured** (774 sections, 1,568 annotations): the step along a
  link from one section to the next has a median of 0.19 µm, a 99th percentile
  of 1.8 µm, and a maximum of 7.9 µm at Z 1317→1318. The one other structure
  linked across that pair steps the same way, (−3.64, −6.77) µm against the
  cell's (−3.95, −6.87), so that section is misregistered. So is 936→937,
  where the cell's (+1.78, −2.69) µm matches the median of the 13 other
  structures linked there, (+1.68, −2.76). The cell's 2–3 µm steps at 767, 819
  and 869 are its own: neighbors move 0.1–0.2 µm there, so the field has
  nothing to correct.
- **Not yet seen against the real service.** From 2026-09-23 00:13 to 00:45
  UTC, `corrections.codepharm.net:443` closed every TLS handshake for that
  name; `identity.codepharm.net` on the same address answered, and port 80
  gave openresty's 502. This was reported to `[Viking]` in the pinned thread.
  Until the service answers, a cell's panel says *not applied* and why, and
  its render is unchanged. The feature was tested end to end against a local
  stand-in that speaks the same messages with the same `CorrectPoints`
  semantics.
- While the service is down, a cell corrected before keeps its offsets from
  disk, and its note says so. After a failure the service is not asked again
  for two minutes, so loads do not wait on it.
- A corrected render is cached under a key that includes its offsets; an
  uncorrected cell's key is what it always was.
- SWC headers name the correction (`SECTION_CORRECTION …`, `POSITIONS viking
  corrected`); JSON exports carry it (`sectionCorrection`) and read it back.
- `sbfsem.io.corrections`: the client and `correct_neuron` for library use,
  which is off by default (MATLAB parity). The client uses `grpcio` with no
  protobuf runtime: the proto's few messages are encoded from their field
  numbers, and the tests hold them to protobuf's own bytes. New dependency:
  `grpcio`. Server settings: `SBFSEM_SECTION_CORRECTIONS=0` turns it off;
  `SBFSEM_CORRECTIONS_URL` and `SBFSEM_CORRECTIONS_STOS_GROUP` point it
  elsewhere.

### Open in Viking closes its tab (2026-09-22)

- **The launch tab closes itself once the link has gone to Viking**, and the
  render it was opened from is in front again. It waits for proof rather than
  for a lost focus: the browser's "Open Viking?" question lives in that tab,
  and closing the tab while it is asked cancels the launch. It closes when the
  focus comes back after it left (the question answered either way, or the
  user back from Viking), or when it is hidden while still the page (Viking's
  window covers the browser, or another tab is chosen). It stays under the
  identity server's sign-in page (the next launch reuses that tab and closes
  it), while *Viking client is not installed.* is showing, and in a tab opened
  by hand, which the browser will not let a page close; that one says it can
  be closed. Measured in headless Chrome for Testing 148 and Firefox 156 with
  Windows emulated and the identity server answered locally: 8 of 8 and 5 of 5
  cases as intended; the tab closes 9 ms (Chrome) and 1 s (Firefox) after the
  render is brought to the front. Firefox hides a page as it is replaced, and
  a close made on that event shut the sign-in tab, so the close on *hidden* is
  queued and skipped after `pagehide`. Not yet seen on Windows with Viking.

### Untagged postsynapses, and two annotation markers (2026-09-22)

Crystal Sigulinsky: RC1's postsynapses from before tags were all counted as
conventional on the Analyses tab, which put her ganglion cell's I:E ratio
"off the charts". The fix is the one agreed in the thread (Becca Pfeiffer's
rule, the user's "do not assume"); nothing here writes to Viking.

- **Tags are read as a set** — any order, any case. Measured on the
  production cache: 66 RC1, 33 RC2 and 21 RPC1 ribbon postsynapses with
  reordered tags (`Ribbon;Bipolar;Glutamate`, `Bipolar;Glutamate;Ribbon`…)
  were being counted as conventional. Crystal asked whether tag order
  matters: it did, and now it does not.
- **A postsynapse whose tags do not say is not assumed conventional.** It
  takes the kind of the structure it is linked to in Viking — a ribbon makes
  it RibbonPost, a conventional synapse ConvPost, a bipolar conventional
  synapse BipConvPost — marked *inferred*; one with no link, or links that
  disagree, becomes **UntaggedPost**, its own type, counted in neither total.
  RC1 c606: 257 inferred ribbon, 130 inferred conventional, 649 untagged; its
  I:E ratio is 0.50 (131 / 261), where it was 259 (1,037 / 4). Live, RC1 has
  12,644 PSDs with no tag of 46,605. The library keeps MATLAB's default and
  reports it (`TagStatus` `assumed`); DEVIATIONS 3.13a.
- **Analyses tab.** The Linked neurons table has a sortable **Tagged?**
  column (Y / N / inferred), untagged rows in amber and inferred ones in
  italics, a count above the table, and **Untagged PSDs (.csv)**: every one
  of them with a Location ID and Viking X Y Z, the tags it has and the tag an
  inferred one is missing — the list to put right in Viking. The .csv/.xlsx
  gain `Tags`, `TagStatus`, `SuggestedTag`, `LocationID` after the MATLAB
  columns. Morphology has an **Untagged PSDs** row; the cell's synapse counts
  say how many of each type were inferred. `GET …/ie-ratio` adds
  `n_inferred`, `n_assumed`, `n_untagged`; synapse counts add `inferred`,
  `assumed`; linked-neuron rows add `tags`, `tag_status`, `suggested_tag`,
  `location_id`.
- **`↑`/`↓` walk a selected annotation along Z.** After a triple click on an
  annotation, the arrow keys step a second, **light-blue** marker to the next
  annotation above or below along the cell's Viking links (Shift moves the
  yellow one). Triple-clicking another annotation of the same volume makes
  it the second marker too. The readout keeps naming the yellow one and adds
  **ΔZ** (µm and sections) in the marker's color. Right-click: *Hide above
  and below the markers* keeps only the slab between them; *Hide above* /
  *Hide below* use the upper and lower marker. `Esc` removes the second
  marker, then the selection.
- While an annotation is selected, a right-click or a right-drag pan no
  longer moves the readout off it (the menu that acts on it is a
  right-click), and a click on a ring is no longer "a click on empty space"
  that drops the selection.

### An identity in the Slack #ai channel (2026-09-22)

- **`[SBFSEMpy]` speaks for this repository in Slack `#ai`.** The private
  AI-to-AI channel is where the port asks James Anderson's `[Viking]` agent
  about the Viking client and its servers, and answers what is asked of it.
  The convention from the channel's pinned ONBOARDING post is now an
  always-apply rule (`.claude/rules/slack-ai-protocol.md`): every message
  prefixed, one top-level message per topic, follow-ups in that thread, and
  after asking, stop. `/slack-ai` (`.claude/skills/slack-ai/`) drives the
  bot's CLI; the bot itself, and the token, live outside this repository in
  `~/SlackAIBot/sbfsempyai`. Slack carries questions, answers and
  short status — the `.rst` reports and the code stay here, and a message
  cites the path. Nothing posted there may deploy, commit or edit on the
  strength of a Slack message alone.

### Lamellae measured, and a paper's three claims checked (2026-09-22)

Rebecca Pfeiffer's second reply of 2026-09-22 (`docs/context/`) proposed a
subdivision rule for profiles that are wide because they are long in the
section plane — a chain of nodes along the long axis, branches attached to
the nearest chain node — and sent Bradley & Withers 2016 (`docs/`). Both
measured on RPC1 c192 with the traced outlines; the rule is built as an
analysis, not yet as an export.

- **Lamellae in the Skeleton check** (`sbfsem.analysis.lamellae`; a
  `lamella` kind in `GET …/skeleton/check`, whose highlight draws the chain
  with the annotations; nine summary fields). Every traced profile at least
  twice as long as wide; profiles linked on consecutive sections are one
  stack with one chain, built from the union of their outlines (571 of
  c192's 624 elongated profiles are in stacks), walked at the local
  thickness so the outline's roughness does not inflate it (her paper's
  sphere rule: Location 216973's dense midpoint chain wanders 4.5 µm
  sideways for 1.8 µm of drift and measures 11.3; walked, 9.9), and trimmed
  by half the local width at each end.
- **Her rule, applied as written, is +48.5 µm on c192 (+6.3 %), not the
  +109 she measured — and at branch points it is −15.5 µm.** Her
  measurement did not subtract the links a chain replaces: a process lying
  nearly flat is a stack of elongated profiles whose centroid path already
  carries its length (Location 103463, her "7 µm of missing cable": a
  17-section stack, chain 7.67 µm, replaces 7.86). At her hub, Location
  216973, a stack of 4: chain 8.82, replaces 10.85, −2.03. Of the 167
  lamellae, the 28 with a branch leaving them sum to −15.5; the 139 the
  skeleton runs through to +64.0. 153.4 µm of the chains lies beyond the
  outermost branch (flat ends; Location 105068, one of the three detached
  pieces, is a 9.4 µm slab with no links at all): with it the rule is +48.5,
  without it −104.9. That is her own open question 3, and it decides the
  sign, so the SWC that writes the chains waits on her answer.
- **Bradley & Withers 2016, checked.** (1) Their noise formula inverted per
  link, σ = 0.081 µm, reproduces the relaxation floor to the micron (605.06
  at 0.080 against 602.37) — though it zeroes 1,080 of 2,850 links, so the
  agreement is of aggregates, not segments. (2) Their sphere-network
  sampling of the skeleton gives 675.05 µm (she: 670.85), inside the bracket
  with no parameter. (3) Their **modal radius is a thickness, not a rim**:
  on a round profile it is the stored radius (14283: 4.33 vs 4.34), on a
  4.9 × 1.8 µm lamella 0.66 µm where its spokes run 2.61 µm inside it. It
  does not replace the outline clipping.
- Her lamella table (`from-becca-skeleton-2026-09-22b-lamellae.csv`) and
  ours (`to-becca-skeleton-2026-09-22b-lamellae.csv`, 167 rows with the
  Location IDs) are filed with the letters.

### A refusal you cannot miss, and the correspondence filed by who and which way (2026-09-21)

- **"Viking client can only be run on Windows." now flares**: red, a swell to
  1.28×, back to size, then a fade to the usual gray, all in 2.4 s. It is the
  one message with nothing else to notice it by — no window opens, no tab
  appears, there is no error. Done with `transform`, never `font-size`, and
  anchored at the right edge: measured through the animation, the top bar
  stays 44 px and the render does not move (the fault the one-line status was
  made to fix). `prefers-reduced-motion` gets the color without the swell.
- **The launch page does the same on Windows when nothing answers the
  hand-off**: *Viking client is not installed.* flares, and so does the
  installer's address, which now has its own line and a **Copy address**
  button. Without clipboard permission it selects the address instead and
  says so, which is what a browser gives you when the write is refused.
- **Fixed while testing it**: the Copy button had no listener at all. It was
  registered after the platform check's early return, so on macOS and Linux
  it was never wired up; on Windows it was. Both button listeners now go in
  before that check.
- **The correspondence is filed by correspondent and direction.** Every letter
  in `docs/context/` is now `<to|from>-<person>-<topic>-<date>[-<subject>]`:
  `to-becca-skeleton-2026-09-22.rst`, `from-janderson-identity-server-2026-09-18-plan.md`,
  and so on — 18 files renamed, every reference in the repo and the index
  updated. `docs/context/README.md` is grouped into correspondence (by
  person), handoffs and state, and plans and audits. Becca's opening letter
  of 2026-09-18, which had been read but never filed, is restored from the
  copy read that day.

### A deploy installs what was synced, every time (2026-09-21)

- **Found while shipping the two entries below:** the service restarted clean
  and answered 404 for a route that was in the synced tree. The server builds
  the package through a leftover `build/` folder, setuptools copies a file
  into it only when the source is *newer* than the copy already there, and
  rsync keeps the sender's timestamps. The deploy before had come from a fresh
  checkout (files stamped at extraction, 20:24 UTC); this one came from the
  working tree (files stamped when last edited, 20:11), so every copy looked
  up to date and the old code was installed. `deploy/bootstrap.sh` now removes
  `build/` before installing. Verified by reading the files the service
  actually imports: 12 of 12 identical to the tree (md5), where before the
  installed `routes.py` had no `annotators` route at all. **Check a deploy
  against the installed package** (`/srv/sbfsem/venv/.../site-packages`), not
  against the synced tree under `/srv/sbfsem/app`, which is only its source.

### Who annotated a cell, and a launch that says why Viking did not start (2026-09-21)

- **Return list of annotators** (right-click a cell in the render). One row per
  username: annotations Viking attributes to them *now*, their share, how many
  are **as placed** (never written again since they were created), how many are
  **edits** (written more than an hour after creation), child structures they
  created, and their first and last dates; ★ marks whoever created the cell.
  *Copy* puts it on the clipboard as tab-separated text. The panel stays open
  while you read or select from it and closes when the focus leaves it (a click
  elsewhere, `Esc`, or ✕). Live from Viking, cell and child structures
  together: `GET /api/neurons/{source}/{id}/annotators`.
- **What the record cannot say, said in the panel.** Viking stores one username
  per annotation — the *last* person to write it — and no history. Measured on
  Inferior Monkey c1178: of the annotations never edited since creation, the
  usernames fall into 10 runs in ID order (one person per sitting, which is
  what tracing looks like); of those edited later, into 415 runs, interleaved
  annotation by annotation, which no four people could produce by tracing
  consecutive IDs seconds apart. So a mentor who fixes a student's annotation
  takes it over entirely, and a student whose every annotation was corrected
  does not appear at all.
- **Bulk edits are not corrections.** A volume-wide operation writes
  `LastModified` on every row and leaves `Username` alone: on c1178, 1,210 of
  2,819 annotations were re-stamped in three bursts of tens a minute spread
  over three usernames. Counting those as corrections reads as "57 % of this
  cell was corrected"; separating them (a minute holding 20+ writes under 2+
  usernames seeds a burst, which then takes in its quieter neighbouring
  minutes) leaves the honest 15 %, and the panel reports the re-stamped rows
  on their own line.
- **"Open in Viking" on a Mac now says so** instead of opening a tab where
  nothing happens: *Viking client can only be run on Windows.* Nothing is
  opened.
- **On Windows, a launch that nothing answers reports the missing client.**
  The tab now holds a page of ours that hands `viking://` over and watches the
  focus: a launch takes it (the browser's "Open Viking?" question does, and so
  does Viking's own window), so a page that still has the focus six seconds
  later was handed the link and nothing answered — *Viking client is not
  installed.*, with the installer's address. The notice withdraws itself if
  Viking turns up late. `/open-in-viking` keeps its 302 contract; the interface
  asks for the page with `?page=1`.


### A floor under the cable length, a relaxed SWC, and two of my own numbers corrected (2026-09-21)

Becca Pfeiffer's reply of 2026-09-22 (`docs/context/`): yes to the relaxation
proposal, both offerings, "the numbers first", and the bound in the header.
Building it properly overturned two figures sent to her the day before.

- **The shortest path through every annotation** (`sbfsem.analysis.relax`).
  The path through the annotation centers over-measures a cable: every
  sideways wander of a center between sections is added in. The true
  centerline passes through every annotated cross-section, so the shortest
  path that stays inside every annotation is a floor that needs no
  calibration. Each node moves to the length-weighted mean of its neighbors
  (Weiszfeld's step: it minimizes the path *length*) and is put back inside
  `f` of its own annotation — its traced outline scaled about its centroid, or
  its circle — until 200 more steps gain under 0.01 µm; tips never move. RPC1
  c192, 769.40 µm through the centers: **673.76 / 639.93 / 617.27 / 602.37 µm
  at f = 0.25 / 0.5 / 0.75 / 1**, 1 to 2 s each. In the Skeleton check as "no
  shorter than N µm", in `GET …/morphometrics?bracket=true`
  (`length_floor_um`, `length_floor_by_radius_um`), and in the bulk manifest
  with `bracket=true`. RPC1 c2628 1,510.8 → 1,108.6; RC1 c606 4,024.9 →
  3,122.4; Inferior c943 105.8 → 71.7.
- **The floor sent on 2026-09-21 (632.75 µm) was neither converged nor a
  floor.** It came from 60 sweeps of an unweighted mean, which minimizes the
  squared link lengths and had not settled: long unbranched paths relax
  slowly (60 steps leave c192 18 µm above 4,000).
- **`?swc_relax=f` writes that skeleton**, with `swc_relax_bound=outline`
  (default) or `radius`, on the single and the bulk export. The header says
  `POSITIONS relaxed f=0.50 bound=outline: …` and `RELAXED_STATS 2589 of 2849
  nodes moved, median 0.100 um, largest 1.26 um; length over all links 769.40
  -> 639.93 um`; an ordinary file says `POSITIONS viking`. x and y change; n,
  T, z, R and P do not (tested column by column).
- **A stored radius does not keep a node inside the cell.** A closed curve's
  radius is its equivalent-area radius, and that circle reaches outside an
  elongated outline. Held by radius at f = 1, 281 of c192's 1,170 traced
  annotations end with their node outside their own outline (38 at 0.25, 87
  at 0.5, 188 at 0.75), and the "floor" drops to 590.72 µm. Held by outline,
  none leaves. The radius bound stays available because it is all an SWC
  allows, and is named in the header for that reason.
- **Spokes on c192 are 109.8 µm (14.3 %), not 133.6 (17.4 %).** Her in-plane
  branch point, Location 216973, is a 9 × 3 µm crescent whose centroid lies
  0.24 µm *outside* its own outline; the rim was found by a ray from the
  center to the farthest crossing, which counted the gap as inside. Each link
  is now clipped exactly against the outline (every crossing, inside and out
  in turn). Brute force, 400 samples a link: 109.82 µm. c2628: 625.4 → 441.6
  µm. Stored-radius figures are unchanged (84.1).
- **Her proposal for the spoke test, measured and not adopted.** "Is the
  link's start inside the parent's outline?" with no ratio in front is true
  of 85 % of c192's length (656 of 769 µm), because consecutive cross-sections
  of any process overlap; restricted to links whose wide end is not also
  inside the thin one, 44 %. And a profile that is wide because it is long in
  plane is itself cable that only its spokes represent: her hub is 9.15 µm
  long and its five links have 5.51 µm inside it. The 3× gate stays, reported
  with every result; the question of what such a profile's cable is went back
  to her.
- **Directions are not corrected, and the app now says so** (Analyses → *How
  these are computed*, the user guide, the module): a branch still starts at
  the center of the profile it leaves, so branch angles and junction bearings
  read off these skeletons are wrong at every such junction.

### Skeleton check, SWC files that say what they dropped, many skeletons at once (2026-09-21)

Rebecca Pfeiffer (Pitt) was exporting each cell's SWC and mesh to Blender to
find and hand-fix what misleads a path length. This brings the finding into
the viewer, and answers her notes of 2026-09-18 and 2026-09-21
(`docs/context/`). Nothing here edits an annotation: an annotation error is
fixed in Viking, once, for everyone.

- **Skeleton check** (a new section in each cell's panel; `GET
  …/skeleton/check`; `sbfsem.analysis.skeleton_check`). Lists **loops,
  unlinked pieces, jumps, hairpins and spikes** to fix in Viking and **spokes
  and stubs** for an export to account for. A row flies there, lights the
  annotations and their links up through the surface, puts the Location ID in
  the pick readout and offers *Open in Viking* at it. Her pipeline infers
  these from an SWC and a mesh; here the loops and pieces are exact (the
  annotation graph), and the rest is scaled to the cell rather than set in
  microns. RPC1 c192 today: 5 loops, 3 unlinked pieces, 1 jump, 1 hairpin, 2
  spikes; 60 spokes of 1 µm or more and 35 stubs; 0.3 s. One misplaced
  annotation is listed once, not once per test (Inferior c943: a 10 µm link
  tripped seven flags, now two). The jump test is relative to the cell's own
  links: RC1 c606 has 195 links over 2 µm edge to edge (an in-plane dendrite
  traced as sparse circles; 90th percentile 2.2 µm, largest 7.2) and none of
  them is an error, so none is flagged.
- **Her three "still detached" pieces are detached in Viking.** Asked of
  Viking directly, Location by Location and uncached: the nine annotations at
  sections 968–975 link only to each other, and Locations 289378 and 105068
  have no links at all (last edited 2020 and 2021). The export is faithful;
  nothing is excluded for being tagged off-edge.
- **Rim-corrected cable length, beside the uncorrected one** (Analyses →
  Morphology, the morphometrics endpoint, the check's summary). A thin process
  is linked to the *center* of the wide profile it leaves; a spoke is the part
  of that link inside the profile, to its traced outline along the link (its
  radius for a circle) — her definition. c192: 769.4 µm total, 133.6 µm of
  spokes by the outlines (17 %; **corrected on 2026-09-22 to 109.8 µm, 14 %,
  see above**), 84.1 µm with stored radii as the rims (11 %;
  she measures 82.5 from the SWC). 108 of the 116 wide ends are closed curves,
  whose stored radius understates how far the outline reaches; the six
  largest spokes were checked by point-in-polygon.
- **Every SWC header now says what the file cannot**: `SECTION_THICKNESS_UM`,
  `ANNOTATION_GRAPH` (annotations, links, pieces, loops), `ROOTS`,
  `DROPPED_LINKS` and one `DROPPED_LINK a b rows n m` per loop — the links in
  Viking that a tree cannot hold, as Location IDs and as rows of the file —
  and `OPTIONS`. With every neurite node typed 3 nothing else recorded that a
  tip was not a tip in Viking. c192: five dropped links, 5.04 µm.
- **Many skeletons in one request**: `GET
  /api/neurons/{source}/export-bulk/swc?ids=…&pattern=c{id}` returns a zip of
  one SWC per structure (up to 200, loaded or not; the unloaded are fetched
  for the archive alone and do not join the shared cell list) and
  `manifest.csv` (file, annotations, links, pieces, loops, section thickness,
  both lengths, options, the annotations' stamp, export time, or the error).
  One bad ID costs one row, not the archive: c192, c2628 and a nonexistent ID
  in 2.0 s. In the app: right-click a cell → Export → *SWC of every loaded …
  cell (.zip)*. Meshes in bulk are not built: each is a build job of tens of
  seconds and hundreds of MB, which belongs in the job queue, not in one
  request.
- **SWC export is 8× faster on a large cell.** The exporter asked
  `has_path(root, n)` of every annotation to find the unlinked pieces: 2.7 of
  the 3.8 s for c192. Connected components give the same rows and parents
  (byte-identical tables for RPC1 c192, c2628 and Inferior c943; the old
  traversal is kept in a test): 0.44 s.

### Annotation depth works in Firefox and is set in nanometers; a too-fine voxel says what would fit (2026-09-19)

- **Any depth but zero made every annotation of a large cell disappear in
  Firefox.** Firefox refuses a draw call of more than 30,000,000 indices
  (`webgl.max-vert-ids-per-draw`) and a refused call draws nothing; the
  annotation layer is one call, and RPC1 c2628's is 9,536,730 indices flat but
  38,146,920 with a depth. Chromium has no such ceiling, which is where the
  feature was checked. Past 12,000,000 indices the layer is now drawn in
  several calls over the same buffers and the same material (c2628 with a
  depth: four), so color, opacity, *Through the surface*, the Z range and
  selection behave as before; a layer under the budget is one call, as it was.
- **Depth is in nanometers**, the unit sections are counted in: 0 to 500 nm,
  the slider in steps of 5, any whole number typed (it was 0.00 to 0.50 µm in
  a box two decimals wide, and 70 nm read as 0.00007 rather than 0.07). The
  header reads "72 nm deep". It is the *total* height, centered on the section
  plane — 70 nm is 35 above and 35 below — and the control's hover help now
  says so. Saved workspaces are unchanged: the state still holds microns.
- **Voxel size 0.025 µm on RPC1 c2628 is refused for its size, not for time or
  a fault.** The closed-curve raster is capped at 300,000,000 voxels; c2628
  spans 24.0 × 28.5 µm over 754 sections, which is 207,517,388 at 0.05 µm and
  828,482,382 at 0.025 (about 3.3 GB as float32 before its smoothed copy).
  The error now gives the limit and the finest pixel that fits the structure
  (0.042 µm here) instead of only suggesting a unit mistake.

### Annotations can have a depth; and what is going on at RPC1 c2628, sections 884 and 885 (2026-09-18)

- **Annotations → Depth.** An annotation is drawn flat in its section plane, so
  edge-on (the XZ and YZ views, where Z levels are read) it has nothing to
  show. *Depth* gives each ring and outline a height along Z, centered on its
  section plane: a band with a rectangular cross-section, stroke wide and
  depth tall. **0 by default** and on the **flat** button — the ribbon exactly
  as it was, to the vertex (RPC1 c2628: 3,178,910 vertices either way);
  **one section (70 nm)** sets the volume's own section thickness, so
  consecutive sections just touch and a missing one shows as a gap; the slider
  goes to 0.5 µm. c2628, 1.59 M outline points: a depth rebuilds in 0.4 s,
  4 × the triangles (12.7 M), 9.1 ms a frame against 3.7 flat and 1.6 with the
  rings hidden. Per cell, saved with the workspace.
- **"Are 884 and 885 too close?" No — their neighbors are far.** Viking's
  OData has c2628 on sections 881, 884, 885 and 888 there; 882, 883, 886 and
  887 carry no annotation of *any* of the 249 structures within 25 µm, so they
  are missing from the volume. 884 → 885 is one section (0.07 µm), 881 → 884
  and 885 → 888 are three (0.21 µm). Orthographic XZ1 with a depth of 0.03 µm
  shows it: the 881→884 and 884→885 spacings measure 3 : 1 on screen.
- **The sideways jump at 885 is in Viking's stored volume coordinates, and is
  stale data, not a registration jump and not ours.** Outline 107463 (885)
  sits (+0.56, +0.16) µm from 107457 (884), and 107482 (888) is back where 884
  was (IoU 0.518 → 0.728 once aligned). Not the section: 75 other structures
  move (0.00, 0.00) ± 0.06 µm from 884 to 885. It goes with the modification
  date: on 885 the 20 neighboring discs last modified before February 2019 sit
  a median (+0.74, +0.27) µm from where the section's present transform puts
  them, the 53 modified since agree with it (−0.00, +0.06), and on 884 and 888
  nothing predates February 2019. 107463 was last modified 2018-02-23; its
  linked neighbors 2019-02-05, the day 1,328 of c2628's 3,978 outlines were
  last touched — a bulk recomputation that missed some Locations. Viking draws
  from section coordinates through the live transform, which is why stepping
  884 → 885 there shows no jump. Report for James Anderson:
  `docs/context/to-janderson-viking-odata-2026-09-19-stale-volume-positions.rst`. Nothing
  is corrected on our side: we draw what the OData says.
- **The yellow bar in Viking on 884 reads as a link, not a shift** (inferred
  from the data, not from Viking's source): 107457 (884) is linked to *two*
  outlines on 881, the soma's (107441) and a small process 4.5 µm away
  (365795, r 0.69 µm) that joins it, and the bar in the screenshot runs from
  the soma outline to a small c2628 circle about that far off — how Viking
  shows a link to an annotation on another section.

### MeshLab remeshing, as an option after any render (2026-09-18)

Becca Pfeiffer's suggestion of 2026-09-17, assessed then and built now.

- **More ▾ → MeshLab** in the render panel: *isotropic remesh* (Edge µm),
  *quadric decimation* (Keep) or *screened Poisson* (Depth), applied to the
  finished surface of any technique, cached like any other render option, and
  carried into every export (exports follow what is on screen). Render
  compare gains *Voxel remesh + MeshLab isotropic / quadric / Poisson*, so
  the error map and a Difference pane can say what each costs.
- **Measured through the new option** (voxel2 meshes; distance from the
  as-built surface; our disc metric; share of sliver triangles):

  | Cell, variant | Faces | From as-built | Surface→ann. | Ann.→surface | Slivers |
  |---|---|---|---|---|---|
  | c943 as built | 147,700 | – | 0.549 µm | 0.184 µm | 2.3 % |
  | c943 isotropic 0.25 µm | 10,916 | 0.014 µm | 0.558 | 0.185 | 0.9 % |
  | c943 isotropic 0.15 µm | 23,380 | 0.016 | 0.557 | 0.186 | 0.3 % |
  | c943 quadric to 25 % | 36,924 | 0.012 | 0.547 | 0.186 | 0.2 % |
  | c943 Poisson depth 9 | 179,970 | 0.017 | 0.553 | 0.185 | 11.5 % |
  | RC1 c606 as built | 400,000 | – | 0.744 | 0.294 | 3.6 % |
  | c606 isotropic 0.25 µm | 360,285 | 0.043 | 0.751 | 0.295 | 0.6 % |
  | c606 quadric to 25 % | 100,000 | 0.062 | 0.744 | 0.321 | 2.6 % |
  | c606 Poisson depth 9 | 93,504 | 0.097 | 0.811 | 0.377 | 11.3 % |
  | c606 Poisson depth 10 | 414,134 | 0.082 | 0.763 | 0.307 | 11.7 % |

  Same reading as on 2026-09-17: the gain is triangle quality and face
  count, never fidelity — that is the technique's. Isotropic is the one for
  Blender; quadric is a better Decimate; Poisson, the step applied to
  VikingMesh output, gains nothing on these meshes. In the app, c943 on
  isotropic: 147,700 → 10,916 faces, 8 s.
- **pymeshlab is GPL-3, so it is optional and never imported**: the
  `[meshlab]` extra, found with `find_spec`, run in a child process
  (`sbfsem.geometry._meshlab_worker`) that takes the mesh on stdin and returns
  it on stdout — nothing written to disk, so nothing new for the unit's
  `ProtectSystem=strict` (ground rule 10). A test asserts `pymeshlab` is not
  in `sys.modules`. Without it: the control is grayed out with the reason,
  `/api/build` reports `"meshlab": false`, and asking anyway is a 400 that
  says `pip install "sbfsem[meshlab]"`. A crash, a hang (600 s) or garbage
  from the child is a `MeshlabError` with the tail of its stderr, never a
  mesh.
- `deploy/bootstrap.sh` installs it on the server best effort (pymeshlab plus
  `libgl1` / `libopengl0`), proves it by running a mesh through the worker,
  and carries on either way; `SBFSEM_MESHLAB=0` skips it.

### Render compare shows the error it scores by (2026-09-18)

Asked for: a graphical view of the error between the rendered surface and
the annotations, the quantity render types are compared on.

- **Every technique pane has an *error* checkbox.** The surface is colored
  by each vertex's distance to the nearest annotation (the
  *surface→annotation* half of the score: unsupported surface lights up), and
  points along every annotation's rim are drawn through the surface, colored
  by their distance to it (the *annotation→surface* half: annotations the
  render failed to reach light up). Light gray agrees, amber is half the
  scale, red the scale and beyond. The legend carries the pane's own numbers.
  Same annotations, same surface samples and same distances as the numbers,
  kept per point instead of averaged (`meshdiff.deviation_map`; the rim
  layer's mean *is* `annotation_um`, pinned by a test), so the picture and
  the numbers cannot disagree.
- **One scale for all panes**, or the colors would not be comparable:
  automatic (the largest 98th percentile among the measured panes) or pinned
  from the box on any pane's color bar. Inferior c943, Voxel remesh orig
  beside Original: both 0.48 µm automatic; 0.300 / 0.067 µm and 0.313 /
  0.087 µm surface→annotation / annotation→surface. On both, the red is the
  stretch of axon between sparse annotations — which is where c943's 0.30
  comes from, and was not visible before.
- New endpoint `POST /api/neurons/{source}/{id}/mesh-deviation`.
- **The annotation deviation no longer takes three quarters of an hour on a
  closed-curve cell.** It tested every point against every edge of every
  outline: RPC1 c2628 (3,978 outlines, 1.44 M outline points) took 131 s for
  3,000 points, 44 minutes and 9 GB for the 60,000 a deviation uses — what
  made `sbfsem-measure` unusable on it on 2026-09-18. A point's distance to
  the nearest outline *vertex* bounds its answer from above, so outlines
  farther away than that in Z, or whose bounding box is, are skipped for that
  point; the bound tightens as outlines are measured. Same distances (max
  difference 9e-19 µm against the exhaustive version, which a test keeps as
  the definition): 3,000 points in 0.65 s, the mesh's 156,366 vertices in
  7.2 s. The other hog was testing all 1.44 M rim points for enclosure (70 of
  the remaining 77 s): above 100,000 rim points every k-th stands for the
  rest, in `annotation_exterior_um` alone — every other number still uses
  all of them and does not move. c2628's full deviation: about 12 s; its
  error map, 18 s and 12 MB.

### One color picker in every browser (2026-09-18)

A color swatch opened whatever the browser had: Chrome's small popup, which
closes on a click away, or in Firefox and Safari the macOS color window,
which stays until its close button is pressed. Asked for: the same behavior
everywhere, closing on a click away, and the wheel the macOS window has.

- **`colorpicker.js` replaces the browser's picker for every color input**:
  the nine there are (cell, wireframe, annotations, skeleton, synapse types,
  materials, gradient stops, the context menu's Change Color) and any added
  later, because it catches the click rather than being wired to each one.
  The inputs are untouched; it writes their value and fires `input` while
  dragging and `change` on closing, as a native picker does.
- **What it has**: hue/saturation wheel with a brightness slider, old and new
  color side by side (click the old to put it back), hex and R G B fields,
  the screen eyedropper where the browser offers one (Chrome, Edge), the cell
  palette, the colors in use, and swatches you save (**+**; kept in this
  browser, right-click to remove). Closes on a click anywhere else, Esc, or a
  window resize; arrow keys move the wheel marker.
- Checked on RPC1 c2628: a drag on the wheel recolors the surface live
  (`#e0643a` → `#3033e0` in the input, the state and the three.js material);
  put-back, palette, Esc and click-away behave; the context menu's throwaway
  input is removed on close (a native picker dismissed untouched used to
  leave it behind); the popup stays inside a 900 × 700 window. **Verified in
  Chromium only.** Cancelling the click is the standard way to stop a color
  input opening its picker and Firefox and Safari honor it for other input
  types, but neither was run here: the first Firefox session should confirm
  the macOS color window no longer appears.

### Toolbar, Display & context, and rings at opacity 1.00 (2026-09-18)

- **Full screen sits at the right end of the bottom bar**, last in the row
  and pushed right on whichever row the wrap leaves it; *Exit full screen*
  is the same button, so it is in the same place.
- **Display & context is two paragraphs shorter.** The standing notes under
  Materials ("Choosing a material gives it to the selected cells…") and
  Colormap ("Applies to cells rendered with Color by IPL depth") are gone and
  *Edit materials…* / *Edit gradients…* move up into their place. Resting
  the pointer on either for two seconds brings the text up beside it, over
  the render; the colormap one now also says what the map is read against
  (0 % at the INL border, 100 % at the GCL), what Levels, Invert and Show
  colorbar do, that nothing is rebuilt, and that saved gradients are shared.
  Same mechanism as the toolbar's help (`data-help`), with a per-control
  delay (`data-help-delay`) and side (`data-help-side`).
- **Annotation rings no longer change color at opacity 1.00.** Reported on
  RPC1 c2628: black rings over a 0.20 crimson surface were black at 0.95 and
  washed crimson at 1.00. At 1.00 the ring material became opaque, and
  three.js draws every opaque object before every translucent one whatever
  its `renderOrder`, so the surface was then blended over rings that are
  meant to be drawn *through* it. Measured over the rings' own pixels:
  rgb(5, 2, 3) at 0.95 and rgb(89, 25, 48) at 1.00 before; rgb(3, 1, 2) at
  1.00 now. Through-the-surface rings stay in the transparent pass at any
  opacity. **Nothing else has the jump**: the skeleton (90, 113, 52 at 0.95
  and 90, 118, 51 at 1.00), the synapses, the wireframe and rings with
  *Through the surface* off all test depth, so a surface in front of them
  tints them the same either side of 1.00.

### Viewpoint buttons keep what is in view; XZ2 and YZ2 (2026-09-18)

Asked: what XY1 and XY2 are, whether XZ and YZ can have the same pair, and
that a viewpoint button should turn the view without losing the content and
zoom on screen — only Reset view pulls back.

- **XY1 and XY2 are MATLAB's.** `RenderApp.m` has XY1 = `view(2)` (the section
  plane from +Z), XY2 = `view(0,-90)` (the same plane from −Z, mirrored), and
  one each of XZ (`view(0,0)`, from −Y) and YZ (`view(90,0)`, from +X). Those
  two are now **XZ1** and **YZ1**, and **XZ2** (from +Y) and **YZ2** (from
  −X) are new. Each tooltip says where it looks from and which way the axes
  run; the user guide has the table. The old names `xz` / `yz` still resolve.
- **A viewpoint button no longer re-frames the scene.** It used to fit the
  whole scene every time, so changing axis from a close-up threw the
  close-up away. Now the camera swings about the point on the view axis at
  the depth of what the viewport shows, and keeps its distance to it.
  Measured on RPC1 c2628 from a close-up 28.64 µm from the content (scene
  diagonal 61 µm): over all seven buttons in a row the content's center
  stays at the center of the picture (0.0000, 0.0000 in normalized device
  coordinates) and the distance stays 28.64 µm; after zooming in between
  presses the depth is measured again (21.82 µm) and kept. A press takes 3
  to 5 ms. Reset view is unchanged and is now the only thing that pulls
  back: Reset view, then a button, is the whole scene along that axis. The
  2D checkbox (down Z, orthographic) follows the same rule.
- **How "what is in view" is found**: the median depth of the surface
  vertices that project into the middle 70 % of the picture (20,000 per
  mesh, about a millisecond; skeletons and rings when no surface is drawn;
  hidden cells and Z-hidden stretches excluded). Tried first and dropped: a
  5 × 5 grid of rays — 15 to 35 ms each on c2628 with its rings drawn, so a
  time budget decided how many were cast and two presses disagreed by a
  micron; and a ray reports the front face, where a quarter turn wants the
  middle of the column being looked through. Also fixed on the way:
  re-measuring on every press crept outward (33.7 → 43.4 µm over seven
  presses), so presses with nothing moved in between reuse the pivot.
- Toolbar checked at 900, 1140, 1300 and 1920 px, light and dark: one row at
  1920, the seven buttons stay together when it wraps, no overflow.

### The pick readout and the navigation hint no longer overlap (2026-09-18)

Seen at a 900 × 700 window with RPC1 c2628 picked: "µm 38.19, 46.16, 87.44"
ran into "drag orbits", and the hint stood four lines tall.

- **The readout never shrinks under its own text.** The two overlays share
  the top of the render, and the readout's box was allowed to shrink to
  nothing while its fields do not break: at 900 px it was squeezed to 223 px
  around a 238 px field (171 px around 217 px before a pick), so the text
  hung 11 to 42 px over the hint. A longer volume name ("Inferior Monkey",
  +80 px a field) did the same at 1024 px. The box now stops at its widest
  field.
- **On a narrow render the hint folds into a "?"** at the top right; a click
  opens it under the readout across the full width (two lines at 900 px,
  where it was four beside the readout), another click closes it, and the
  view keys keep working after the click. It folds when the overlays have
  under 760 px to share — a window of about 1090 px with the cell list at its
  default width — and it is the render's width that decides, not the
  window's, so widening the cell list or going full screen is counted.
- Measured at 900, 1024, 1140, 1440 and 1920 px, light and dark, hint open
  and closed: no box intersects another and no field leaves its box; at 900 px
  the readout has 534 px (two lines, was three). From 1140 px up the layout is
  pixel for pixel what it was (318/480, 436/662, 626/952 px). Worst case
  checked: "Cone pedicles (cped) c12345  µm 123.45, 123.45, 123.45" holds
  399 px right at the fold and the hint takes the remaining 350 px.
- Nothing about the render, the wireframes or what the readout says changed.
- **The hint fits on one line at 1920 px.** It was 1037 px of text beside a
  681 px readout in 1590 px, so both wrapped to two lines. It now reads *drag
  orbit · right-drag pan · triple-click select · shift/ctrl/alt-drag
  roll/pitch/yaw · q/e roll · z/Z zoom · m reset · ⌘Z undo · c copy Viking*:
  814 px, every control still named. The three single-axis drags became one
  item (that is where 100 of the 223 px came from), "X Y Z downsample" left
  the `c` item (the field it copies is labeled *Viking* beside it), and the
  yaw drag is named by `alt`, which works on every platform; `⌘` still works
  and is in the user guide's table. At 1920 px both overlays are one line
  (25 px tall, was 45); the widest RPC1 readout tried (six-digit cell,
  three-digit µm, a 58.25 downsample: 761 px) still fits, with 3 px to spare.
  A Monkey-volume readout (862 px) still wraps both to two lines there: no
  wording that keeps all eleven controls reaches the 716 px that would take.
  At 1440 px the pair is 501/597 px (was 436/662), at 1140 px 364/434 px (was
  318/480); the folded layouts at 900 and 1024 px are unchanged. Light and
  dark, no overlaps.

### Deselecting, and a status line that no longer moves the render (2026-09-18)

Asked: "in the case of a selected annotation (or other), how does one
deselect it?" The answer was `Esc`, and it did not always work.

- **`Esc` deselects from anywhere on the render tab.** It was handled on the
  render canvas only, and choosing from the *Select* menu leaves the keyboard
  focus on a menu row that no longer exists, so after the usual way of
  selecting, `Esc` went nowhere until the render was clicked. It is handled
  by the page now (a menu first, then the selection; never while typing in a
  field), and the focus returns to the render after a choice, so the view
  keys work at once.
- **A click on empty space deselects**, as in every other 3D program; a drag
  from empty space still only orbits, and a click on a cell keeps the
  selection. **Right-click → *Deselect*** is in the cell's menu and on empty
  space. The status line says *Selection cleared*.
- **The pick readout names the selected annotation.** Asked: "does the
  Location ID and Viking XYZ in the upper left … show that ID?" Not reliably:
  each click of a triple click is an ordinary pick, so the readout held the
  annotation nearest the clicked surface point (within 5 µm — often a
  neighbor), and it did not change when another annotation was chosen from
  the *Select* menu or the ring had no surface behind it. Selecting an
  annotation now sets the readout to it — Location ID with its volume, and
  the µm and Viking X Y Z of the position Viking stores for it — so the ID
  click and `c` copy go to that annotation. Checked on RPC1 c2628: the pick
  said 133575 (section 1135); choosing 133351 from the menu gave *Location ID
  133351 (RPC1)*, Viking 16925 15422 1126, its stored 36.83, 33.56, 78.82 µm.
- **The status line is one line, always.** Found while testing the above: the
  150-character "Selected: …" message wrapped at 1,024 px, the top bar grew
  from 17 to 35 px of text, and the render moved down 23 px and shrank
  (559 → 535 px) the moment something was selected. Any long message did
  this. The line now ends in an ellipsis with the full text as its tooltip,
  the selection messages are half as long, and the bar is 44 px with a
  200-character message at 900 px, light or dark.

### Zoom that never stalls, triple-click selection, Hide above / Hide below (2026-09-18)

Three requests for the render window, and one fault found on the way.

- **Deep zoom no longer slows everything down.** Reported: "when I zoom in
  extremely far, the speed of the zoom decreases more and more … panning,
  rotation, and other operations become painfully slow. One has to zoom out
  again." Nothing was slow — RPC1 c2628 (636,246 triangles) held 60 frames a
  second throughout. The controls zoom by shrinking the distance to an
  invisible pivot 5 % a notch and pan in proportion to it, and the pivot need
  not be on anything: over 364 notches that distance went 25 µm → 0.0000007 µm
  and the step per notch 1.3 µm → 0.00000004 µm. "Not always" was whether the
  pivot lay in front of the surface being approached or behind it. Now the
  pivot is slid along the view axis to the depth of the surface under the
  pointer before a wheel step or a pan (the camera and the picture do not
  move: measured 7 × 10⁻¹⁴ µm and 0 rad). Same 364 notches: the working
  distance tracks the surface (22.7 vs 23.4 µm, 6.25 vs 6.36, 1.70 vs 1.73,
  0.46 vs 0.47), the step never falls below 0.0125 µm, and a pan press on a
  ruler collapsed to 0.001 µm puts it back at the surface's 89.15 µm. Over
  empty space zooming in holds its step (4.78 µm a notch for 60 notches)
  instead of shrinking against nothing; zooming out grows as before. The near
  plane follows the zoom (it was fixed at framing, 0.05 to 0.3 µm, and cut
  surfaces open on approach). Orthographic zoom, rotation, fly-to and the
  wireframes are untouched.
- **Left triple click selects what is under the pointer** — an annotation, a
  skeleton branch, a wireframe, a synapse type; never the surface — and
  lights it up (yellow with a glow; cyan on a yellow cell). Several
  candidates open a chooser whose rows preview the highlight. Matching is in
  screen space, within 6 px, so a one-pixel skeleton and a ring seen edge-on
  can be hit; opaque surfaces occlude, except annotations drawn through the
  surface. On c2628 (3,978 traced outlines, 1.6 million outline points) a
  triple click costs 15 ms on average, 42 ms at worst.
- **Hide above / Hide below a selected annotation, and Restore full render**,
  in the cell's right-click menu: two clipping planes on that cell's own
  materials, so it is instant, reversible and per cell; both together leave
  the slab between two sections. Stored in the volume's microns (the same
  annotations stay hidden through Flip Z and a volume offset), saved with the
  session and undo, ignored by exports, and clicks pass through the hidden
  part. A cell hidden entirely is restored from a right-click on empty space
  or from the cell list.
- **Fixed: every traced outline of a cell reported the cell's ID as its
  Location ID.** `GET …/closed-curves` filled `location_id` from the parent
  structure (c2628's 3,978 outlines all said 2628). It now carries each
  annotation's own ID, which the selection needs; nothing else read it.

### Traced surfaces sit on their annotations in Z (2026-09-18)

Reported on RPC1's Müller cell c2628: the rings and the skeleton stood off
the surface along Z, whatever technique was chosen.

- **A closed-curve surface is as tall as what was traced.** The volume
  stacked one slice per *traced* section, a section apart, so every skipped
  section moved everything above it down: 0 µm wrong at the bottom of the
  cell, the whole deficit at the top. c2628 is traced on 676 of the 754
  sections it spans, so its surface ended 5.46 µm below its top outline.
  MATLAB's `renderClosedCurveOutlines.m` does the same (one slice per unique
  Z, placed at `index + min(Z)`); it was ported faithfully and is now fixed
  rather than kept (DEVIATIONS §3.6a). Every technique showed it because a
  cell with outlines always loads on Hybrid or Closed curve volume, and the
  traced half of both is this volume. Rings, skeleton, picks and Viking
  coordinates were right all along; only the surface moves.
- **Skipped sections are interpolated, long holes stay open.** Two traced
  sections at most 1 µm apart are joined by shape-based interpolation
  (blended signed distance maps: an outline present on both sides morphs, one
  present on one side tapers off). A longer stretch is left open and the
  stack carries on at its true height; in a mixed cell that is where the
  discs take over. Surveyed over the 160 most-traced structures of the eight
  volumes: 82 % of the untraced runs are one or two sections, 93 % are under
  a micron. Knock-out test on Inferior c1178 (traced on all 127 sections):
  a section rebuilt from its neighbors overlaps the traced one by IoU 0.963
  for one missing section, 0.928 for five, 0.894 for ten; adjacent traced
  sections overlap each other by 0.945.
- **Measured, outline → surface distance, mean / 95th percentile, µm**
  (0.25 µm raster): RPC1 c2628 1.049 / 3.84 → 0.369 / 1.25; RPC1 c33379
  1.641 / 5.44 → 0.235 / 0.83; RC1 c9025 0.606 / 1.63 → 0.300 / 1.14;
  Temporal c162 1.443 / 3.96 → 0.075 / 0.16; RPC2 c2968, a bipolar cell
  traced at both ends with discs between, 30.2 / 53.8 → 0.467 / 2.79 (its
  terminal was drawn 54 µm from where it is). Inferior c1178, no skipped
  sections, is unchanged to the vertex. Only Inferior Monkey was free of it;
  the rabbit volumes had it on nearly every traced cell.
- **Cost**: RC2's vessel c1, the largest, 92 → 102 M voxels, 11.5 → 12.9 s,
  peak 2,478 → 2,572 MB; its surface now ends at 101.19 µm against outlines
  ending at 101.15 (was 92.37).
- **A branch untraced for a few sections is whole: Viking's links are
  followed.** Becca's point, relayed the same day: where the EM is bad the
  annotators skip the section and *link* the outline below to the one above.
  Checked in the OData and true: on c2628 location 144100 (section 1121)
  links to 144101 (1125), and 144119 (1142) to 144120 (1145). Over 18 cells
  in the eight volumes there are 1,597 closed-curve links that skip
  sections, every one 8 sections (0.56 µm) or less; only Inferior, Nasal and
  Cped had none. The whole-section rule above could not see 0 to 31 of them
  per cell (8 on c2628, 31 on RC1 c410), because *other* branches of the
  cell were traced on the skipped sections, so the section was not empty and
  the branch showed a slit a few sections tall. Each linked pair is now
  interpolated on its own, in a window just big enough for the two outlines:
  inside the 144100–144101 pair's box, sections 1122–1124 go from 0 pixels
  to 134 / 102 / 129 (the ends have 135 and 129); 1143–1144 go from 48 / 38
  to 217 / 202. c2628 gains 0.48 % of its voxels and 0.35 s; its largest
  piece is 98.0 % of the faces (the rest are hair-thin processes about a
  raster pixel wide). A linked pair is followed up to 5 µm; nothing seen is
  past 0.56.
- **Tested and dropped: sliding the two outlines onto a common centroid
  before blending.** Leaving a traced outline out of a linked chain and
  rebuilding it from its two neighbors, 1,500 chains on c2628: IoU against
  the traced outline 0.711 plain, 0.682 aligned (RC1 c410 0.698 / 0.676,
  RPC2 c774 0.788 / 0.772). These outlines change shape between sections
  more than they travel. Where two linked outlines do not overlap at all
  (2.6 % of those chains) the plain blend tapers to nothing half way, so a
  linked pair carries the nearer outline across instead: a link is never
  cut.
- **Missing links are rare, and are Viking's to fix.** An outline that ends
  upward facing, within a micron, an outline that ends downward and overlaps
  it by half: 5 on c2628 (all under 0.1 µm², smaller than a raster pixel), 0
  on RC1 c410, 2 on RC1 c9025, 2 on RPC1 c33379 — one of them real: locations
  198281 (section 862) and 198283 (864), 90 % overlap over 10.4 µm², no link.
  Nothing guesses at links here; a link added in Viking shows up on the next
  refresh.
- `GEOMETRY_VERSION` 3 → 5 over the two changes, so cached surfaces rebuild
  once. A saved `.json` workspace holds annotations, not surfaces, and needs
  nothing.
- Still true, and not part of this: an outline smaller than a raster pixel
  (c2968's topmost 1.2 µm, 0.06 to 0.10 µm² each at 0.25 µm) leaves no
  surface; a finer **Voxel size** shows it.

### Viking and SBFSEM-tools hand over to each other without a login page (2026-09-18)

James Anderson sent his plan for smoothing the two deep links. Most of it is
in his servers and in Viking; this is our half.

- **A link from Viking signs in silently.** A signed-out `/open` used to stop
  at our login page and wait for a click on *Sign in with Viking*. Viking's
  context menu now goes through the identity server first
  (`SbfsemOpen/Redirect`, live on his side), so the browser arrives holding a
  session there; `/open` — and only `/open` — now asks the identity server to
  reuse it (`prompt=none`) and lands in the render with nothing shown.
  Measured against his server: with no session it answers
  `error=login_required` in one redirect, no login page, and we fall back to
  our ordinary login page with the link intact and no error on it. A
  hand-made `login_required` callback is still a refusal, an abandoned
  handshake no longer rides in the cookie for an hour, and a silent sign-in
  that did not stick goes to the login page rather than looping. `/` is
  unchanged, so a built-in account still gets its form.
- **Open in Viking.** Right-click a cell → *Open in Viking* opens the volume
  in Viking at the annotation under the pointer (within 5 µm), else at the
  point as `x,y,z,downsample`; from the cell list, at the soma. It goes
  through our `/open-in-viking`, which speaks Viking's volume names
  (`NeitzInferiorMonkey` → `InferiorMonkey`), refuses the demo volume and
  volumes the account was not granted, and redirects to his
  `VikingLaunch/CreateCode?volumeName=&location=` in a tab of its own, so a
  sign-in page there never replaces the render.
  `SBFSEM_VIKING_LAUNCH_URL` moves it. `/api/volumes` carries `viking_name`.
  On the day this shipped his `CreateCode` answered HTTP 500 to every browser;
  we reported it with the request ID and he fixed it the same day (its
  `[Authorize]` listed the Bearer scheme first, whose forward selector
  returns an empty scheme name when there is no `Authorization` header, and
  that throws before any challenge). Re-measured signed out at 22:21 UTC:
  302 to his login page with `volumeName` and `location` kept on the
  `ReturnUrl`. He also confirmed the `:4001` sign-in cookie
  (`.AspNet.SharedVikingIdentity`) is the session `:5001` honors for
  `prompt=none`.
- **Open in Viking never opens Viking farther out than Viking's own
  default.** For a point that is no annotation we send the downsample our
  view corresponds to, and Viking applies it with no upper limit (read in
  `VikingMain.cs`: only a floor of 0.01; with none sent it starts at 32,
  which is what a launch by Location ID gets). Our whole-cell views measured
  717 to 1031: at 970 on RC2 (2.18 nm per pixel) a 1500-pixel Viking window
  would span 3.2 mm for a volume 0.25 mm across. The value is now capped at
  32, a field of about 105 µm; a closer view (8, say) is sent as it is, and
  the pick readout still shows and copies the true value.
  `SBFSEM_VIKING_LAUNCH_MAX_DOWNSAMPLE` moves the cap.

### Collaborator requests (2026-09-17)

Crystal Sigulinsky and Becca Pfeiffer sent a list after using the tool;
every item is answered here, in the user guide, or in the session notes.

- **Each browser tab keeps its own session.** Two tabs on different volumes
  used to share one saved session, so whichever tab reloaded came back as
  the other one. A tab's session is now keyed by an id in `sessionStorage`
  (a reload finds it, closing the tab forgets it), the shared slot is still
  written so a new tab starts from the last save, duplicated tabs detect
  the shared id over a `BroadcastChannel` and take a new one, and slots
  untouched for two weeks are swept.
- **Viking coordinates copy without commas.** The pick readout's Viking
  field and `c` now copy `X Y Z downsample`. Viking's Goto Location paste
  parser keeps digit-group separators inside a number, and `,` is that
  separator in a US locale, so `5120, 3810, 371` arrived as `5120,` and was
  refused. Typing or pasting into the field still accepts commas.
- **Annotations draw through the surface.** With *Show annotations* on, an
  opaque soma showed almost none of its rings: each ring sits on the
  annotated rim, which the smoothed surface encloses by a hair, and the
  top and bottom rings of a soma stack lie under its caps (RC1 c606, "the
  top of the soma is not being shown"). Rings are now drawn regardless of
  depth by default; **Through the surface** in the cell's Annotations
  section turns the old behavior back on. Meshes and wireframes are
  untouched.
- **Synapse types named as the volume names them.** The Analyses summary
  listed `Unknown 2 (PeriGJAdherens)` for a Viking type this tool has no
  name for; such rows now carry the Viking name (`PeriGJAdherens 2`). In
  the cell's Synapses section the Viking types that share one local name
  (every unmapped type is Unknown) are one row and one marker set instead
  of several rows toggling the same markers; the tooltip lists them.
- **The Analyses pane scrolls, and its cards fit any window.** The card
  grid had no `flex: 1; min-height: 0`, so cards below the fold were
  unreachable. Separately, the global no-wrap table rule let a card's
  key/value table grow past its card, where the next card painted over the
  values — on a 1140 px window the grid packed four 260 px cards and cut
  "1037 (Postsynapse)" to "(Postsynap" (Crystal's screenshot). Cards are
  now at least 340 px wide (one column below that), their tables wrap,
  rows are sized to their content, charts scale with their card, and the
  tab bar wraps on a narrow window. Checked at 900, 1140 and 1920 px in
  the light and dark themes.
- **Both linked-neurons tables sort by every column**, the *Synapses per
  partner* table included, and unsorted headings show `↕` so the feature is
  discoverable.
- **Every Morphology number has its definition** on hover and in a *How
  these are computed* block, including what the I:E ratio counts (ConvPost
  ÷ RibbonPost; gap junctions play no part) and what an unfinished branch
  is (a cell-body annotation with at most one link that is not tagged
  Terminal; Off Edge ends count).
- **Dendritic length and skeleton morphometrics.** New
  `sbfsem.analysis.dendrite.morphometrics` and
  `GET /api/neurons/{source}/{id}/morphometrics`: total cable length (3-D
  and in the section plane), dendritic length (soma links excluded),
  longest path from the soma, mean tip path, branch points, tips (with
  Terminal / Off Edge counts), fragments, soma annotations and extent. The
  Morphology card shows them; with several cells loaded a **Loaded cells**
  card lists every cell from every volume with all metrics, sortable, with
  a **.csv** download — the table for comparing dendritic length across
  volumes. Inferior Monkey c943: 105.8 µm of cable, 75.3 µm dendritic; RC1
  c606: 4,025 µm in 5 fragments.
- **Full screen reclaims the title bar and the cell list.** A *Full
  screen* button in the render toolbar and the `f` key use the Fullscreen
  API; the browser's own full screen (F11 on Windows and Linux; ⌃⌘F on
  macOS in Chrome, Edge and Safari, ⌘⇧F in Firefox) is detected too, through
  the `display-mode: fullscreen` media query or a viewport the size of the
  screen. In full screen the title bar goes, the status line floats over
  the right end of the tab strip, and the cell list folds away behind a
  *≡ Cells* button (its state is remembered), so the render gains the bar's
  height and the list's 300 px of width.
- **Volume offsets.** With cells from two or more volumes loaded, a
  *Volume offsets* group under Display & context gives every volume an X, Y,
  Z shift in microns, with *Side by side* (volumes along X, centered on the
  first, a gap of 15 % of the widest), *Align selected somas* (each other
  volume moved so its selected cell's soma sits on the first selected
  cell's) and *Reset*. Objects carry their volume, so a shift moves meshes,
  skeletons, annotations, synapse markers, boundary sheets and vessels
  together, while picks, Location IDs, Viking coordinates and exports stay
  in each volume's own frame; jumps to an annotation land on the shifted
  cell. Stored with the session and with saved workspaces. In passing, a
  pick on a Flip Z scene now reports the unmirrored Z.
- **Section thickness per volume.** Every cell's Z spacing already follows
  its own volume's live Viking `Scale` (Inferior 90 nm, Nasal 50, Temporal
  75, Cped 50, the four rabbit volumes 70); the user guide now lists them.
  The offline fallbacks for Temporal (70) and Cped (7.5 nm pixels, 90 nm
  sections) were the MATLAB values and are corrected to Viking's.
- **The SWC type column is explained and, from the app, plain.** The
  header now names the structure and volume, the units, the row order and
  the type codes present (`# TYPE_CODES 1 soma, 3 dendrite, 5 branch
  point, 6 end point`). The app's `.swc` links pass `swc_branch_types=false`
  so every neurite node is `3` dendrite, which is what today's SWC readers
  expect (they take 5 and 6 for custom types — the "what are types 5 and
  6?" question); the API and `write_swc` default to the MATLAB typing,
  now a `branch_types` switch.

### The yellow landing sphere is gone

- **No more yellow ball on the annotation you jumped to.** Every camera
  flight — a Viking "view in SBFSEM tools" link, a typed Location ID or
  X,Y,Z, the Analyses tab's *Show location on render* — used to drop a
  translucent yellow sphere on its destination and never remove it. Its
  radius was 6 % of the flight distance, and a deep link flies at
  whole-cell distance, so on a 400 µm cell it was a 24 µm ball drawn on top
  of the soma (reported by the user from James Anderson's new Viking
  context menu, 2026-09-17). The sphere is removed outright; the flight,
  the pick readout with the Location ID and the status line are unchanged,
  and the annotation sits at the center of the view when the flight lands.

### Deployment: the 4 vCPU / 16 GB instance

- **Production moves to a 4 vCPU / 16 GB / 320 GB Lightsail instance**
  (`sbfsem-web-upgrade`, static IP <server-ip>), the plan
  `docs/vm-upgrade.md` recommended. The unit file carries that document's
  sizing: `SBFSEM_MESH_WORKERS=4`, `SBFSEM_MESH_PROCESSES=4` (the process
  pool is on for the first time, so builds scale past two), 
  `SBFSEM_MESH_MEMORY_MB=9000`, `SBFSEM_MESH_CACHE_MB=65536`,
  `MemoryHigh=11000M`, `MemoryMax=13500M`. `deploy/redeploy.sh` and
  `deploy/status.sh` default to the new address. DNS moved on 2026-09-12;
  the old 2 vCPU box at <old-server-ip> is retired and must not be redeployed
  with these numbers.
- The OData/mesh cache, saved workspaces, the users file and session key,
  the Viking secrets file and the Let's Encrypt certificate were copied
  across, so nothing changes for signed-in users at cutover.

### The linked-neurons report is on the Analyses tab

- **`getAllLinkedNeurons` is a card on the Analyses tab.** One row per synapse
  of the selected cell: the partner neuron (blank when Viking has no link for
  it), its label, the synapse type and structure ID, the direction, and the
  synapse's position in microns — the MATLAB table's `SynapseXYZ`, one column
  per axis. The summary line counts synapses, linked ones, distinct partners
  and unlinked ones (Inferior Monkey c1178: 239 rows, 230 linked to 157
  partners, 9 unlinked). **Order** switches between the MATLAB function's
  ordering (by partner) and the one `getAllLinkedNeurons_sorted` (2025) wrote
  (by synapse type, label, partner, synapse); column headings sort, a filter
  box narrows, **.csv** and **.xlsx** download the table named with the cell
  and its volume, **↻ Viking** re-resolves the partners, and **Synapses per
  partner** collapses it to counts. Partner IDs are buttons that put the ID,
  with its volume, in the Structure ID box. API:
  `GET /api/neurons/{source}/{id}/linked-neurons` (`?order=neuron|type`,
  `?refresh=`) and the same path with `.csv` or `.xlsx`.
- `openpyxl` is a new dependency (the `.xlsx` writer).

### Connectivity tab: a stepped scale, pinned highlights, Graffinity's path list

- **The color scale is stepped**, as Graffinity's legend was: **Bins** sets
  the number of steps between 1 and the top, **Max** sets the top (blank
  follows the data; cells above a lower Max take the darkest step and the
  legend reads `112+`), and the legend under each table shows the steps with
  their boundaries. Both tables share the controls.
- **Clicking a cell pins its highlight.** The outline of the cells in the
  other table that share its paths used to vanish on mouse-out; it now stays
  until the same cell is clicked again, Escape, or **Clear selection** — so
  it can be screenshotted for a figure — and survives metric changes and
  regrouping.
- **The path list is grouped as Graffinity grouped it**: one row per
  sequence of cells with **Hops** and **Freq** (the distinct synapse-type
  combinations joining those cells), most frequent first, opening to the
  combinations under `Edge 1 … Edge n`, each opening to the structure IDs
  of every synapse. **List: Every path** keeps the flat one-line-per-path
  view.
- **Allow repeats** (under Limits) lets a cell appear twice in a path,
  Graffinity's own behavior, off by default — how a cell linked to itself
  shows up. API: `allow_repeats` on `POST /api/connectivity/query`.

### The Network tab builds one volume, names its cells and explains its colors

- **One network per volume.** Build network used to send every loaded cell
  to the server under the first cell's volume, a leftover from the
  single-volume days: with cells from two volumes loaded the build failed
  with "not loaded for …", and a cell whose ID also exists in the first
  volume would have been silently swapped for that volume's cell. The tab
  now has a **Volume** select listing the volumes with loaded cells and
  builds the cells of the chosen one; the request carries that volume, the
  summary names it, and clicking a node puts the ID *and* its volume in the
  Structure ID box. Loaded cells are outlined to tell them from partners.
- **Names** shows cell IDs, Viking labels, or both beside the nodes; the
  labels used to hide the IDs.
- **A legend** under the graph lists every link type the build found with
  its color and synapse count, and a checkbox per type leaves it out — the
  edges, weights and colors are recomputed from what remains, without a new
  request. Undirected links (gap junctions, adherens junctions, touches,
  unknown) are drawn dashed and without an arrowhead, since Viking records no
  direction for them; they used to get the same arrow as a ribbon synapse.
  An edge's color now follows the type with the most synapses on it rather
  than the alphabetically first. Hovering an edge lists its synapses by
  type; hovering a node gives its ID, label and degree. Min weight and Names
  redraw from the kept answer instead of asking Viking again.

### A Connectivity tab: Graffinity's overviews over Viking

- **The Connectivity tab implements the connectivity matrix and the
  intermediate node table of Kerzner et al., *Graffinity: Visualizing
  Connectivity in Large Graphs* (EuroVis 2017)**, the tool the Marc lab used
  on RC1, for every volume this tool opens. A query is a direction
  (downstream from the loaded cells, or upstream into them — the one a
  ganglion cell needs), a path length of 1 to 4, and a template of
  whole-string regular expressions: one over labels per node position and one
  over synapse types per hop. The anchors are the loaded cells of a volume or
  every cell whose label matches a pattern. The matrix shows start cells
  against end cells with a path count, shortest length or synapse count per
  cell on a linear or log color scale, grouped by label on demand with each
  group expandable; the node table ranks the neurons the paths pass through
  by (length, position); hovering either view outlines the other's cells that
  share paths, and clicking lists the paths with their synapse structure IDs.
  Matrix and paths download as `.csv`. Paths are simple — no neuron twice —
  which Graffinity's own search did not require; with undirected links, 157
  of c1178's 455 three-hop paths had bounced through c1178 itself.
- **Multi-hop queries never load a neuron.** Each hop is two batched OData
  requests — the `StructureLinks` of the frontier's cells by parent
  (`Source/ParentID in (…)`, which Viking's service accepts), then the labels
  of the cells they reach — and what was fetched stays in the server's memory
  for later queries. Measured 2026-09-11 for Inferior Monkey c1178: a two-hop
  upstream query fetched 158 neurons and answered in 1.7 s (35 paths from 31
  inputs; c23357, a diffuse amacrine cell, carried 15 of them), the repeat
  0.1 s. Caps on paths and on neurons expanded per hop (300) are exposed under
  **Limits**, and the status line names the one that bit. API:
  `POST /api/connectivity/query`, `GET /api/connectivity/{source}/graph`;
  library: `sbfsem.network.paths`.
- Nothing here is a port of the Graffinity source (AngularJS 1.5 and D3 3,
  whose README recommends re-implementing the techniques); the definitions
  follow the paper's sections 4.1 and 4.2, and the query grammar its
  client-only release (Zenodo 10.5281/zenodo.1422851).

## 2.2.0 — 2026-09-11

### Closed curves sit where they were traced

- **Closed-curve outlines were drawn up to 20 µm from the cell's discs.**
  Viking traces an outline in its section's own mosaic frame and registers
  disc centres into the volume frame; the outlines were never moved into the
  volume frame, so a soma traced as closed curves rendered beside its own
  dendrites (Inferior Monkey c1178: 18 µm away, with the disc annotations of
  the soma showing as rings around nothing). The other volumes were worse:
  Nasal Monkey's outlines sat 130 µm from their discs, RC1's 46 µm and RC2's
  86 µm at the median. The loader now takes Viking's own registered polygon
  (`VolumeShape`), which is served for every volume, and falls back to the
  mosaic outline translated by its annotation's own offset when it is not.
  Meshes cached before this change are rebuilt, and a cell file saved by an
  earlier version has its outlines moved when it is opened.
- **The measurement tool was already correcting for this**, so its deviation
  numbers for curve-traced cells described a mesh other than the one on
  screen. Both now see the same outlines.

### RC2 has its IPL boundaries

- **INL and GCL boundary surfaces, IPL-depth coloring and clipping now work in
  RC2.** The volume list still carried MATLAB's `hasBoundary` verdict, written
  before RC2 had boundary markers, so every request was refused with "RC2 has
  no IPL boundary markers" although Viking holds 128 INL-IPL and 117 IPL-GCL
  markers there. They are cached with the package like the other volumes'.
  `tools/cache_boundary_markers.py` writes such a cache for any volume; the
  older volumes' caches are the MATLAB parity fixtures and stay as they are
  (Viking has a handful more markers for each now). Cped, RPC1 and RPC2
  still have no markers in Viking.

### No wires through a traced soma

- **Hybrid no longer threads tie tubes through the closed-curve volume.** The
  disc half of a Hybrid build sees the cell's link graph cut wherever a traced
  run interrupts it, and its one-body step tied the discs on either side back
  together with wires — three on c1178: two straight through the soma, 11 and
  18 µm long, and one 7 µm along its base. Pieces that touch the traced
  volume are one body through it already, so the tie step is now told which
  pieces those are and lays no wire between two of them. Ties elsewhere in
  the arbor are unchanged, and disc-only cells have no traced volume to
  consult.

### Annotations show what was traced

- **"Disc annotations" is now "Annotations", and a closed curve is drawn as
  its outline** — a band along the traced polygon in its section plane —
  rather than as a circle of the radius Viking happens to store for it. Discs
  are rings, as before. Color, opacity and stroke apply to both, in the 3D
  view and in Compare panes.

### The view turns all the way over

- **Left-drag orbiting has no stops.** It used to halt at straight up and
  straight down — the orbit control measured elevation against a fixed pole
  and clamped it there. A plain drag now turns the whole view frame, as the
  arrow keys and single-axis drags already did, so rotation carries on for as
  long as you drag; the familiar glide at the end of a drag is kept. `m` sets
  the view upright again.

### IDs name their volume

- **The pick readout says which volume an ID belongs to** —
  `RC2 c74420`, `Location ID 769111 (RC2)` — and copying one says which volume
  to open in Viking. Structure and Location IDs are numbered per volume, so a
  bare number was being looked up in the wrong one: RC2's c74420 is a ganglion
  cell, Inferior Monkey's is a child structure of c19698.
- **Typing a Location ID only searches this volume's cells.** It used to
  search every loaded cell, so the same number in another volume could send
  the view somewhere unrelated.

### Open a volume from a link

- **`location=` lands on one annotation**: `/open?volume=RC2&cells=74420&location=769111`
  loads the cell and flies to that Location — what a Viking context menu on
  one annotation needs.

- **`/open?volume=RC1&cells=1178,5678`** — the deep link agreed with the
  Viking authors, so the Viking Identity site and Viking's own context menus
  can link straight into a render. `volume` is *Viking's* name for the volume
  and is translated here; ours works too, so a link copied out of the address
  bar keeps working. A signed-out visitor is sent through sign-in and lands
  back on the same link, query string and all. An unknown volume, one this
  tool cannot render, or one the account was not granted is explained on the
  page instead of showing an empty viewer.

### Sign in with Viking Identity

- **"Sign in with Viking"** on the login page. This site is now a
  confidential OpenID Connect client of the Viking Identity Server: the user
  signs in on Viking's own page, the code exchange happens server to server,
  and the tokens live in a server-side session — the browser holds only a
  random id in an httpOnly cookie. Endpoints come from the discovery
  document, never hard-coded.
- **Viking decides which volumes you see.** After sign-in the server asks the
  Permissions API for the volumes shared with that account and shows only
  those, refreshing every five minutes so a grant made in the Viking
  management site appears without signing out. Volumes Viking shares that
  this tool cannot render (`Zia`, `Wohl`, and about twenty more) are left
  out of the picker; the bundled demo volume has no Viking counterpart and
  is never gated.
- **The built-in login stays.** Both ways in work at once, so nobody with a
  local account is locked out. `sbfsem-users` is unchanged.
- **Off unless switched on.** With `SBFSEM_VIKING_AUTH` unset — every local
  checkout, the test suite, and any server that has not been configured — the
  application behaves exactly as it did. See
  [docs/viking-identity.md](docs/viking-identity.md).

### Operations and tooling

- **The app's own log lines reach the journal.** uvicorn configures only its
  own loggers, so every INFO line from this application had been dropped and
  only warnings surfaced; `configure_logging` fixes it (`SBFSEM_LOG_LEVEL`
  overrides). A Viking sign-in now leaves a readable trail: started, complete
  with the granted volumes, or failed with whether the handshake cookie
  came back.
- **Expired Viking sessions are swept** at startup and on every sign-in; the
  sweep existed but nothing called it.
- **`tools/rst_fix_tables.py` and `tools/rst_fix_headings.py`** size
  reStructuredText tables and heading rules from their content; reports to
  the Viking authors are validated with docutils before they go out.

### Sizing the VM

- **[docs/vm-upgrade.md](docs/vm-upgrade.md) now names the plan and answers
  the multi-user question.** The measured recommendation is unchanged —
  4 vCPU / 16 GB — but the doc says which Lightsail tab it is (General
  purpose, Dual-stack, $84/month) and shows why the other two ratios lose:
  compute-optimized gives 8 GB at 4 vCPU, and the worst case measured here
  is 6.7 GB. Ten to twenty users do not change the answer: four cores
  saturate at mean-build-seconds ÷ minutes-between-builds of 12, and the
  measured mixes land at 0.6 to 8, before job sharing and the mesh cache
  take their cut. What does cap that number is the single-tenant service,
  not the box. `SBFSEM_MESH_CACHE_MB` rises to 65,536 in the after-resize
  table, since the plan brings 320 GB of disk in place of 77 GB.

## 2.1.0 — 2026-09-09

### The pick readout talks to Viking both ways

- **Pick readout → Viking.** Clicking a render still names the point in
  microns and Viking coordinates; the Viking field now carries the
  **downsample** the view corresponds to (image pixels per screen pixel at
  that point), so a left click on it copies “X, Y, Z, downsample” ready for
  Viking's *Commands → Goto Location* (`c` does the same). A new **Location
  ID** field, after a separator, names the nearest annotation of the clicked
  cell (within 5 µm); a left click copies the ID for *Annotation → Goto
  Location ID*.
- **Viking → pick readout.** Right-click either field to type or paste one:
  a Viking location (`X, Y, Z` or `X, Y, Z, downsample`) or a Location ID,
  and Enter flies the view there — a downsample sets the zoom. A Location ID
  a loaded cell holds is answered from the cell; any other is looked up in
  Viking (`GET /api/volumes/{source}/locations/{id}`, 404 when unknown), the
  view goes there, and the status line names the cell it belongs to with a
  *Load* button when it is not loaded.

### Two published methods as pathways: the convolution surface and Ultraliser

- **Convolution surface** (Frontiers in Neuroinformatics 2022, "Robust
  quasi-uniform surface meshing of neuronal morphology using line
  skeleton-based progressive convolution approximation"): the paper's quartic
  kernel f(r) = (1 − r²/R²)², support R = 2d, is integrated in closed form
  along every skeleton segment with the weight 15TR⁴/16(R² − d²)^{5/2} that
  makes a straight neurite of radius d the T iso-surface; the fields are
  summed and contoured on the whole-cell block grid (`unify.py` gained a
  `sum` mode; `geometry/convolution.py` holds the kernels). Junctions and the
  soma blend crease-free — c118's trunk leaves its soma as a smooth trumpet —
  at the price of rounding the soma over two radii and tapering neurite
  ends: c106 mean deviation 0.276 µm against 0.092 for Voxel remesh, c118
  0.199 against 0.108. Section-by-section samples are merged into segments
  at least a quarter kernel radius long (the paper's elimination of trivial
  segments). Knob: **Kernel R/d** (2). The paper's sphere-deformation
  meshing is not reproduced.
- **Ultraliser (voxelized proxies)** (Abdellah et al., Briefings in
  Bioinformatics 2023): the Voxel remesh pieces, or an icosphere soma of the
  mean radius (**Soma proxy** = sphere, Ultraliser's own starting soma), are
  binarized to an inside/outside grid (`unify.py` `occupancy` mode — what
  conservative voxelization plus flood filling produces), contoured, then
  smoothed by Ultraliser's Laplacian-with-inflate step (`Mesh.smooth_inflate`;
  **Smoothing** 15 iterations, **Smooth λ** 0.5, **Inflate** 0.52, which
  holds volume to 1 % on a tube) and coarsened to the face cap. It reproduces
  Voxel remesh's solid to the metric's resolution (c106 0.092 vs 0.092 µm,
  c118 0.109 vs 0.108) with a faint ring texture from the binarized terraces.
  Dual marching cubes, the neurite-pulled soma and the MeshFix repair are not
  reproduced. Both pathways are in the Pathway menu, the toolbar picker, the
  compare tab, the API (`conv_support`, `ultra_soma`, `ultra_smooth`,
  `ultra_lambda`, `ultra_inflate`) and `sbfsem-measure`, and share the Fine
  voxel, Face cap and Tie gaps knobs with Voxel remesh.

### Voxel remesh is the default and one surface; a wider, denser cell list

- **Renamed and re-defaulted.** The one-surface pathway shipped earlier
  today as "Voxel remesh 2" is now simply **Voxel remesh** and the app's
  default for new browsers; the piece-assembled pathway is **Voxel remesh
  orig**. Option ids are unchanged (`voxel2`, `voxel`), so saved sessions,
  attached renders and technique defaults keep meaning what they meant;
  `sbfsem-measure` accepts `voxel_orig` as an alias. Hybrid keeps its disc
  half on Voxel remesh orig. The library and bare API still default to
  `original`.
- **Travelling disc stacks are tubes.** A run of stacked discs whose centers
  move sideways farther than the run is tall (and farther than its widest
  disc) is an oblique process annotated section by section, not a body; the
  loft made a slanted plate of it — the wedge with a shelf under it where
  c118's trunk leaves its soma (six 1.0–1.35 µm discs shifting 2.7 µm
  across 0.45 µm of height), a 1.6 µm process of c1178 running 14 µm
  horizontally. Voxel remesh now reads such runs as round cones
  (`TRAVEL_HEIGHT_RATIO` in `remesh.py`), so c118's trunk base is a round
  taper that merges into the soma. The disc metric moves the other way by
  a hair (c118 mean 0.102 → 0.108 µm, c1178 0.347 → 0.362): a tube's top
  and bottom sit a radius from the section circles that describe it, where
  the plate hugged them — the metric assumes the circles are horizontal
  sections of the body, which for an oblique process they are not. Voxel
  remesh orig is untouched, so its output is unchanged.
- **The cell list is denser and the panel resizable.** Drag the handle
  between the panel and the viewport to widen it (300 px minimum, kept per
  browser, double-click resets). A shut row is one line — chevron, eye,
  swatch, cell number, two-letter volume tag, refresh, select, unload — at
  about half the old height; an open row adds a second line with the volume
  name, the technique and the label. The accent rail follows the tree's
  indentation (row header, section heads, open section body) instead of
  running down the whole item, and the sections sit tighter without a font
  change. Under the list: Select all, Select none, a ↻ *Refresh all cached
  renders* button, *Clear all cells* (undoable), and the selection count
  centered beneath.

### Voxel remesh 2: the whole cell as one surface

- **A fifth pathway, Voxel remesh 2**, in the Pathway menu, the toolbar
  technique picker and the compare tab. Voxel remesh builds a cell from
  pieces — a marching-cubes envelope per soma-scale cluster, a ring tube per
  thin stretch, links, tie wires — and concatenates them, so a dendrite
  leaving the soma is a separate shell pushed into the envelope (c106 in
  Temporal Monkey: 27 topological components; plain to see with the
  wireframe or a translucent material). Voxel remesh 2 keeps the same
  cluster fields (lofted discs, Envelope σ, volume compensation) and adds
  every thin stretch as an exact round-cone field, links and coins as
  their own primitives, and ties stamped into the field, then contours the
  union **once** on a whole-cell grid at dendrite resolution: c106 is 1
  component, c1178 is 1 (299 before). The grid is evaluated in 32-voxel
  blocks that geometry actually touches and welded exactly along shared
  faces, so it is never materialized. Knobs: **Fine voxel** (0 = auto: two
  voxels across the thinnest 2 % of radii, ≥ 0.04 µm, coarsened to the
  triangle budget), **Face cap** (quadric decimation after smoothing,
  default 400,000, 0 = none) and **Junction fillet** (smooth-minimum radius
  where bodies meet, 0 = exact union). Measured: c106 2.3 s / 323k faces /
  384 MB against 0.3 s / 19k / 148 MB; c1178 5.8 s / 400k / 1,104 MB against
  4.2 s / 222k / 282 MB. The mesh's `meta` carries the voxel used, whether
  a budget coarsened it, the raw face count, blocks, ties and components.
- **The deviation metric learned to see buried surface.** Surface a render
  hides inside itself (a tube running on through the soma, a cap inside its
  parent tube) is invisible yet sits among annotations and scores ~0, so an
  assembled render dilutes its own mean — the c106 Voxel remesh mesh buries
  22 % of its area, c1178's 36 %, a single sheet none. `sbfsem-measure` now
  reports `ext` (surface the mesh does not enclose, by a signed ray-crossing
  count — parity would miscount a union of overlapping pieces), `annext`
  (rims the mesh does not enclose) and `hidden` (the buried share) beside
  the raw columns; `AnnotationDeviation` carries `exterior_um`,
  `hidden_fraction` and `annotation_exterior_um`, and
  `meshdiff.enclosed_points` is the point-in-mesh test behind them. On
  those, Voxel remesh and Voxel remesh 2 are within the metric's resolution
  of each other on c106 (exterior surface 0.077 vs 0.088 µm, exterior rims
  0.139 vs 0.123 µm); on c1178 Voxel remesh keeps a 0.06 µm edge on the
  area-weighted exterior surface while Voxel remesh 2 is closer to the disc
  rims in every radius class — see the user guide.
- **Leaner mesh internals**, from making voxel remesh 2 affordable: the
  smoothers apply their neighbor mean through `bincount` instead of building
  a sparse matrix with duplicates (same weights, a third of the memory on a
  two-million-face sheet); the winding test is chunked; the tie search
  works on subsamples of large pieces and refines the accepted ties locally
  (c1178's voxel remesh 2 tie pass went from 25 s to 0.7 s) and can be asked
  to tie touching pieces too; the voxel pathway's piece assembly is one
  shared function (`_assemble_pieces`) that the voxel, wrap and voxel2
  pathways all use, and `voxel_remesh_cell_fields` returns the cluster
  fields themselves (`SampledField`, with a tricubic `sample`).

### Materials per cell, a material editor, and gradients you can draw

- **Materials belong to cells now**, not to the whole view. Each cell's
  Surface section has a Material menu with ten finishes and `→ selected` /
  `→ all` to hand the finish on; the old global Material control in Display
  & context is gone (a saved session's global choice is applied to cells
  that had none of their own). The presets are physically based
  (three.js MeshPhysicalMaterial) and actually differ from one another:
  *Standard*, *Matte* (MATLAB's dull), *Plastic*, *Glossy* (MATLAB's shiny),
  *Metal* (reflects a procedural environment instead of showing its own
  color in the highlights — the old "metal" and "shiny" were the same Phong
  formula with different numbers), *Velvet* (the felt/fuzzy look: a sheen
  rim where the surface turns away, no hard highlight), *Clay*, *Glass*
  (transmission and refraction), *Glow* (self-lit in the cell color) and
  *Faceted*, which is what "flat" was: flat shading, kept because it shows
  every triangle and is the quickest way to judge a mesh's resolution. Each
  menu entry explains itself on hover. Lights were retuned for the new
  materials.
- **The Material menu in Display & context is always in reach**, above the
  editor rather than inside it, and works like the Map menu: choosing a
  material gives it to the selected cells (or every cell when none is
  selected) at once, with *Apply to selected / all cells* beside it. The
  list footer reads as two centered lines each: `12 of 12 / selected` and
  `Refresh all / renders`.
- **A material editor** (Display & context → Materials → *Edit materials…*)
  exposes the underlying properties — roughness, metalness, highlight,
  clearcoat, sheen, glow, reflections, transmission, refraction,
  iridescence, sheen and highlight colors, flat shading — with a live
  preview on the selected cells. *Save to library* stores it under a name
  on the server, where every user sees it on every Material menu; presets
  can be copied, custom entries deleted, and either applied to the selected
  cells or to all.
- **Gradients you can draw.** Under Colormap, *Edit gradients…* starts from
  any of the 18 MATLAB maps or a saved gradient, lets you move, recolor,
  add, remove, reverse and space the stops with a live preview on the
  depth-colored cells and the colorbar, and *Save to library* puts the
  gradient on the Map menu for everyone (starred), where a session
  remembers it.

### Docs

- **Sizing the VM** ([docs/vm-upgrade.md](docs/vm-upgrade.md)): what a
  bigger Lightsail instance would and would not speed up, the recommended
  plan, and the exact unit-file settings to change after resizing. The
  2026-09-09 session handoff is in `docs/context/`.

### Many cells at once: parallel loads, renders that stay cached, and cells that know when Viking moved on

- **Find results can be added in bulk.** Every match now lists with a
  checkbox; **Add all** loads the whole result and **Add selected** loads
  the ticked ones (clicking a row still loads that one cell). Results are
  no longer cut off at sixty.
- **Loads run side by side.** A label that names thirty cells used to load
  them one after another; now six are in flight at a time, the server no
  longer serializes Viking fetches behind one lock, and a session restore
  does the same. The per-request cap rises from 60 to 200 cells.
- **Mesh builds use more than one worker.** The build queue takes
  `SBFSEM_MESH_WORKERS` threads (two on the current 2-vCPU box) behind a
  memory gate: every build carries an estimated peak, and starts only when
  it fits under `SBFSEM_MESH_MEMORY_MB` beside what is already running, so a
  batch of small bipolar cells builds in parallel while a 2 GB curve volume
  runs alone. `SBFSEM_MESH_PROCESSES` moves the geometry step into child
  processes for a machine with more than two vCPUs, where threads stop
  paying (the GIL).
- **Built meshes are kept on disk** (`SBFSEM_CACHE_DIR/meshes`, pruned
  least-recently-used past `SBFSEM_MESH_CACHE_MB`) and keyed by the newest
  annotation edit the data was fetched with, so a redeploy or the thirteenth
  cell of a batch no longer rebuilds everything, and a refreshed cell can
  never be served its old surface.
- **Cells report when Viking has edited them.** Every load asks Viking for
  the cell's newest annotation and compares it with what the data carries;
  an edited cell gets an amber dot on its row and a lit ↻ button, the
  selection line counts them, and **Refresh all** (next to *None*)
  re-fetches every loaded cell. *Update from Viking* and the ↻ button now
  bypass the response cache — they were being answered from it, which
  meant an "update" could return the same stale annotations.
- **The "new version deployed" banner reaches sleeping and logged-out
  tabs.** The build check now also runs the moment a tab becomes visible or
  focused (browsers throttle a background tab's timers to once a minute
  and stop a sleeping tab's entirely), and `/api/build` answers without a
  login, so a tab whose session has expired still learns a redeploy
  happened instead of swallowing a login redirect on every poll.
- **Attached renders could not be saved in production.** The service's
  systemd hardening (`ProtectSystem=strict`) allowed writes only under the
  cache, saves and auth directories; *Attach render* wrote to a fourth,
  so every PUT answered 500. The unit now lists it, and the new shared
  library directory beside it.

### Measured renders, and a health report for the server

- **`sbfsem-measure`** builds a cell under any technique and prints the
  numbers a render claim needs: visual bodies (the detached-body detector
  from the 2026-08-30 session, now `sbfsem.geometry.meshdiff.visual_bodies`),
  faces, build time, peak memory (each build in its own process), and the
  surface's deviation from the annotations in both directions. `--json` and
  `--compare` give a before/after delta line per cell. c1178 voxel from the
  pinned cassette: 1 body, 221,564 faces, 4.1 s, 272 MB peak, 0.25 µm mean.
- **`deploy/status.sh`** is a read-only production report: build stamp,
  service state, memory against the cgroup ceilings and the kernel's
  pressure counters, loaded neurons, the service journal, nginx errors and a
  status-code histogram, optionally a py-spy stack of the worker.
- Project skills (`/ship`, `/measure`, `/prod`, `/handoff`) in
  `.claude/skills/` make those, the deploy sequence and the session handoff
  one command each; CLAUDE.md gained the three rules they enforce.

### The MATLAB colormaps

- **The Colormap menu is the MATLAB one.** All 18 maps of `RenderApp`
  (Spectral, Bone, CubicL, RdYlGn, Gray, Haxby, Hsv, Roma, Batlow, Hawaii,
  Devon, AntiJet, Parula, RedBlue, Viridis, StepSeq, Ametrine, Isolum) replace
  the previous eight generic ones, in the same order, and Spectral is the
  default as it was there. The colors are the tables MATLAB R2023b itself
  produced, captured under `tests/fixtures/capture/` and shipped in both the
  library (`sbfsem.util.colormaps`, which no longer needs matplotlib) and the
  app. Saved views that name a retired map still open: `stratify` becomes
  RedBlue, its MATLAB original; `jet` becomes AntiJet, as MATLAB read it.

- **Boundary sheets restore from the right volume.** Reloading a session
  saved on RC1 with the INL and GCL boundaries on brought back Inferior
  Monkey's INL over RC1 cells: the restore fired the context fetches before
  it had switched the volume, so the first request left for the default
  volume while the second saw the saved one, and the wrong sheet was then
  recorded as RC1's and never refetched. Restore and undo now set the volume
  first, and every context fetch pins the volume it was made for, discarding
  the result if the volume changed while it was in flight.

### Hybrid renders, and no more gateway timeouts

- **A new Hybrid (discs + curves) technique** unions the closed-curve volume
  with a Cylinder build of the disc annotations — the only method that shows
  every annotation of a structure traced with both kinds. The disc half
  honors the pathway and its knobs; the curve half honors Voxel size. Cells
  holding both kinds now load on Hybrid automatically; cells traced only as
  closed curves load on Closed curve volume (vessels rasterize at 1 µm,
  everything else at 0.25 µm). Attached renders and later edits still win.
- **Curve volumes build in float32, in place.** The isosurface pipeline
  promoted the raster to float64 and copied it at every step; on a ~145M
  voxel grid (RC2 c1 at a 1 µm raster) that peaked at several extra
  gigabytes, pushed the deployed service over its 2 GiB cgroup ceiling, and
  the kernel reclaim-stalled it at 94 % memory pressure — a 24 s build
  crawling at 0 % CPU for half an hour. Smoothing now computes straight
  into float32 (measured locally: 27 s, 2.5 GB whole-process peak), and the
  service ceiling rises to 2.6 GiB high / 3.3 GiB max on the 3.8 GB box.
- **Renders run as background jobs, so no build can hit the gateway
  timeout.** The app starts a build with one request, polls it cheaply (the
  busy line counts elapsed seconds), and collects the mesh whenever it
  finishes — the "— 504 — the server took too long" banner class is gone
  structurally, not just reworded. Identical concurrent requests share one
  build; unloading a cell abandons its job.

### Vessels and very large structures

- **Blood vessels load on the technique that can actually render them.** A
  Viking type-3 structure (e.g. RC2 `c1`, "Vessel JRB": 21,757 annotations,
  15,834 of them closed-curve outlines, spanning 661 × 731 × 1206 µm) is
  seeded with the Closed curve volume technique at a 1 µm raster — measured
  at ~25 s — instead of a disc-sweeping pathway that grinds past the
  gateway's five-minute limit and dies as a bare "504". An attached render,
  or any setting applied afterward, still wins.
- **Gateway errors read as sentences.** Over HTTP/2 the browser gets no
  status text, so a proxy timeout surfaced as "— 504 ." in the render-error
  banner. 502 and 504 now say what happened and what to try.

### Session restore

- **Restored sessions are healed onto techniques their cells can render.**
  A session saved before the annotation-aware seeding could carry Voxel
  remesh for a mixed structure like RC2 c1 and re-break on every restore —
  the recurring "c1 with Voxel remesh — 504" report. A restored cell whose
  stored technique is disc-only but whose annotations include traced
  outlines is re-seeded (Hybrid, or Closed curve volume) with a status
  note; stored Hybrid and Closed-curve choices are kept as saved.

### Removing cells

- **A cell removed mid-build can no longer leave a ghost mesh.** Unloading a
  cell while its mesh request was still in the server's queue let the reply
  re-add the mesh after the row was gone — an unremovable stray that was
  mistaken for another cell (reported 2026-08-31). Every await in the scene
  pipeline now re-checks that the cell is still loaded before drawing.
- **✕ works after a server restart.** Removing a cell the restarted server
  no longer had loaded failed with a 404 and left the row; the 404 is now
  treated as "already gone" and the row is removed.

### Loading cells

- **The Structure ID box and a new `.txt` import speak the Viking export
  portal's grammar** (jamesra/Viking, `Jotunn` branch): IDs, inclusive
  ranges (spaced dashes tolerated), and **label terms** — any entry with a
  letter loads every parent structure whose label contains it,
  case-insensitively. Entries separate on commas, semicolons, or new lines,
  never on spaces alone, so labels with spaces (`GC ON`) stay whole. Every
  label's matches and every unreadable token are named in the status line —
  input is never silently dropped. The same files the portal accepts can be
  posted through **Import…**, and everything the box accepted before still
  works. Label resolution follows every OData `@odata.nextLink`
  continuation, as all our OData reads have since the port began; all 32
  query shapes the app emits were re-verified against the updated OData
  interface (2026-08-31) on both a Neitz and a Marc volume.

### Volumes

- **The four Marc lab rabbit volumes are back**: RC1, RC2, RPC1 and RPC2, in
  the volume menu and the library (`validate_source` accepts the MATLAB
  aliases `marcrc1`, `r`, and so on). They are served from the same host as
  the Neitz volumes; MATLAB itself could never reach them (its
  `getServiceRoot` raised) and its offline scale fallback had the wrong
  section thickness — see DEVIATIONS §6.6.

### Viewport

- **Wheel zoom is aimed by the mouse pointer** (the Autodesk Fusion 360
  behavior): the camera dollies along the ray under the pointer, so an
  off-center pointer pulls that part of the scene toward screen center as
  you zoom in — the further from center, the more angled the zoom. Dead
  center it is a straight zoom, and `z`/`Z` remain centered.

### Render options

- **Out-of-range render parameters are clamped to the server's accepted
  range** everywhere options enter the app — typed into a number box (HTML
  `min`/`max` never stopped typing), restored from a saved session or
  technique default, or attached to a cell. Before this, a typed 0.1 in
  Cube size (floor 0.15) was rejected by the server, but the rejected
  options were already saved on the cell and came back on the next restore
  as "cells that no longer render" (the 2026-08-31 morning report). Apply
  also writes the clamped values back into the boxes, so what you see is
  what rendered.
- **Server validation errors read as sentences** in the render-error banner
  ("wrap_spacing_um: Input should be greater than or equal to 0.15") instead
  of raw JSON.

### Geometry

- **Far same-section jumps between thin annotations are links now, not
  tubes** (voxel, wrap, and adaptive pathways; `original` still sweeps them,
  MATLAB-faithful). c33 in NeitzCPED holds a 0.24 µm annotation linked
  50 µm across its own section: sweeping that out-and-back drew 14 % of the
  cell's surface through unannotated tissue (mean annotation deviation
  1.24 µm, max 20.6). Jumps whose disc rims part by ≥ 1.5 µm are cut; a
  connector wire is drawn only within the tie pass's 20 µm honesty bound,
  and an annotation stranded by its removed jumps renders as a coin where
  it was annotated. c33 voxel now measures 0.087 µm mean (max 1.27),
  wrap 0.157 µm.

### Render panel

- **The render parameters are now a horizontal strip** just above the bottom
  toolbar, instead of a vertical column over the viewport — and it holds to
  **one row**: the defaults actions, Rebuild all, Order, Clip IPL, and the
  wrap insurance knobs (Reject σ, Refits) folded into a compact **More ▾**
  menu at the end of the strip.
- **Defaults are per technique.** "Defaults to selected/all" no longer jumps
  everything back to Voxel remesh: it keeps the technique you are on and
  resets its knobs to that technique's defaults. **Save technique defaults**
  stores your own tuning per technique (per browser); **Forget saved**
  discards it; picking a technique in the toolbar quick-picker now applies
  that technique's full parameter set.
- **A failed render announces itself.** A cell that fails to build (empty
  mesh, bad parameter, server error) raises a red banner over the viewport
  naming the cell, the technique and settings it failed under, and why —
  instead of a 12px note in the status corner.

### Access control

- **Optional login.** When a users file exists (`sbfsem-users add NAME`
  creates it; see *Access control* in docs/deployment.md), every page and API
  route requires signing in; without one the app runs open exactly as before.
  Passwords are stored as salted PBKDF2 hashes, sessions are signed 30-day
  cookies, and adding or removing a user takes effect immediately — removing
  someone invalidates their existing session on their next request. The
  topbar shows who is signed in, with a **Sign out** link.
- **Invite links.** `sbfsem-users invite NAME` prints a one-time
  `/welcome?code=...` link (7-day default) where the person chooses their own
  password and is signed in on the spot — the administrator never has to know
  or transmit it. Codes are stored hashed and spent on first use; re-inviting
  an existing user doubles as a password reset.

### Volumes

- Removed the four Marc lab rabbit volumes (`RC1`, `RPC1`, `RC2`, `RPC2`).
- Volumes now carry a separate display name — **Inferior Monkey**, **Nasal
  Monkey**, **Temporal Monkey**, **Cped** — used everywhere the interface shows
  a volume. The canonical `name` is deliberately unchanged: it is the wire
  identifier in API paths, cache filenames, saved sessions and exported JSON,
  so renaming it would orphan existing data. Display names are accepted as
  input too, so a value from the UI round-trips.

### Synapse markers

- Synapses can be drawn as an **oval** aligned to the annotation's own axis.
  Viking stores each synapse as a `LINESTRING` — the segment the annotator drew
  — and its midpoint reproduces the node coordinate exactly, so the segment adds
  orientation the toolbox never used. `Neuron.get_synapse_axes` reads it and
  `synapse_ellipsoids` builds the marker; `circle` remains the default and is
  unchanged.

### Rendering

- **Render compare audit: a cell change can no longer leave stale
  Difference panes.** Five defects fixed. A failed measurement aborted the
  remaining Difference panes and left them silently showing the previous
  cell's map — each pane now fails alone, shows its error in place, and the
  rest continue. Switching cells now clears every Difference pane at build
  start (so a later failure can never resurrect the old cell), and the cell
  picker change itself dims the legends and says "press Compare". Pressing
  Compare mid-build was silently ignored — it now queues and runs when the
  current build finishes. Re-comparing the same cell keeps your camera
  instead of re-framing. And the cell picker updates the moment a cell
  loads, not only on tab entry. Server-side, each rendering's
  annotation-deviation is now cached per (cell, geometry options), so a
  re-Compare stops paying 15-40 s per Difference pane for unchanged
  settings.

- **Compare panes: numbered badges, live surface opacity, and honest stale
  legends.** Each pane header starts with its number — what a Difference
  pane's dropdowns and legend refer to — and gains an **α** input that sets
  the surface opacity live, so the disc annotations and wireframe show
  through the envelope. A Difference legend always names the panes it
  actually measured, and changing any setting dims it and stamps it "Out of
  date — press Compare to re-measure" instead of letting last build's
  numbers pass as current.

- **Difference panes now hold both renderings to the cell's own
  annotations.** A second legend line reports each source pane's mean
  absolute deviation from the discs (and any closed-curve outlines) — the
  closest thing to ground truth — alongside the existing mean |d| between
  the two surfaces. Measured in both directions so nothing hides: surface
  with no annotated support (a soma cap past the last annotated section, a
  cone sweep through unannotated tissue) and annotations the render failed
  to reach both score, always as absolute values that never cancel. The
  hover detail adds the direction split, p95/max, and an **unsupported
  volume** in µm³ (mean surface deviation × surface area). Closed-curve
  outlines are shifted from their mosaic frame into the registered volume
  frame before measuring (they were ~19 µm off on c1178). Pinned against
  analytic cylinders/spheres in the test suite; also in the mesh-diff API
  as `deviation_a`/`deviation_b`.

- **Your preferred render can now be the default, per browser or per cell.**
  **Set as startup default** (More ▾ menu) makes the current technique and
  parameters what the browser opens with and what newly loaded cells get;
  **Attach render to selected cell(s)** saves a cell's current render to the
  server so loading that cell anywhere starts it with those settings
  (`/render-prefs` API). Precedence, most specific first: restored session,
  attached render, startup default, app default.

- **One cell now renders as one visual body on the voxel and wrap pathways.**
  The pieces those pathways assemble could miss each other by a hair — a
  rescued stretch's boundary pair went unrendered, a tube's anchor could land
  outside a surface the Envelope blur pulled inward, and Viking holds
  same-cell branches nobody ever linked — leaving cells visibly in pieces
  (c1178 rendered as 8 detached bodies, its arbor 3.3 µm adrift of its soma;
  c118 as 4, c8232 as 3, c160 as 2). Any residual gap up to 20 µm is now
  closed with a thin 0.25 µm tie wire along the shortest surface-to-surface
  crossing; wider gaps stay honestly open and are logged. The ceiling is the
  **Tie gaps ≤** control in the render panel (`tie_um` in the API; 0
  disables). All six production-loaded cells now measure exactly one body,
  and c1178 is pinned as a regression test.

- **"Export wireframe" in the right-click menu.** The rendered wireframe as
  real data, in nine formats: OBJ line elements and PLY edge elements (import
  as edges in Blender), STL as thin printable rods (STL has no lines), SWC as
  the centerline skeleton, CSV/TSV edge tables (one row per edge with both
  endpoints in microns), NumPy `.npy` (an M×2×3 segment array), compressed
  `.npz` (vertices + edge indices), and HDF5. All carry the on-screen
  geometry options, so the exported wireframe matches the rendered one.

- **The "bubble" artifacts are gone from every modern pathway, measured by
  a disc-based metric** (surface distance outside the annotated discs —
  the ball-based metric could not see a hemisphere cap hiding inside its
  annotation's ball). Three fixes, verified on c1178, c4115 (both volumes)
  and c4010:
  - *Fat tube-fallback stretches are lofted, not swept.* A stretch the
    cluster field lost, or one just under the remesh threshold (r ≥ 1 µm),
    crumpled when swept — stacked-disc rings fold through each other and
    the cap reads as a ball half-buried in the soma. Each such stretch now
    gets its own small loft grid (43 stretches on c1178 alone).
  - *Same-section branch links lose their girth in adaptive, wrap and the
    tube fallback too*, matching the earlier voxel fix: split the sweep at
    the jump and connect at 0.75 µm. `original` still sweeps them,
    byte-faithful to MATLAB.
  - *Wrap honesty pass.* Tube samples outside the z-clamped capsule union
    (the vertical rings a laterally wandering soma stack sweeps a full
    radius below its lowest section) lose their vote, and isolated fat
    annotations are sampled as coins, not balls. On c1178 wrap dropped
    from 25,618 to 383 vertices >1.5 µm outside the discs (worst 6.8 →
    3.8 µm); on c4115 from 3,027 to 0 — the half-buried knob under its
    soma is gone. This also removes the apparent interpenetration of
    neighbouring cells: their annotations never overlapped (max 0.4 µm,
    thin dendrites), the inflated renders did.
  - Same-section overlapping fat discs now read as an in-plane slab in
    wrap, agreeing with the voxel pathway's disc model.
- **Wrap default Blend 2.5 → 2.0**, from a parameter sweep on c1178: 2.0
  is tighter everywhere measured, 3.5 smears the fit outward, and cube
  size 0.4 / reject 2 / refits 2 stay. The user guide has the sweep table.

- **Voxel remesh: same-section branch links no longer sweep sausages.** A
  segmentation row that jumps laterally between two same-section fat discs
  is a branch link, not a tube — but it was swept as a radius-thick capsule
  through unannotated tissue. On c4115 its underside was the "detached
  knob" reaching 2.70 µm below the lowest annotation (204 mesh vertices
  more than 1 µm outside every annotation); c1178 carried a 22 µm long,
  10.5 µm thick one through its soma (388 such vertices, worst 4.3 µm).
  Links are now drawn at attachment scale (0.75 µm) — the bodies stay
  visibly connected, and both cells now have **zero** vertices more than
  1 µm outside the annotations. Pinned by two new geometry tests.

- **Voxel remesh: soma size and phantom nodules fixed.** The smoothing blur
  no longer costs volume (the extraction level is Newton-adjusted to recover
  the pre-blur volume, now within ~0.1%), and a fat annotation the field
  loses (a stack thinner than a voxel, or blur-erased) renders as its honest
  flat disc instead of a full-radius capsule — the sphere-like nodules that
  protruded from somas are gone, and with them the "missing reach" that read
  as a smaller soma.
- **New experimental pathway: Wrap (cube fit).** The user-designed algorithm:
  axis-aligned cubes where the tube mesh has vertices, per-cube robust plane
  fits with σ-outlier rejection and refits, repeated coarse-to-fine, and the
  fitted implicit surface extracted as one outer envelope (interior fold
  walls are pruned via the z-clamped capsule union, which also serves as the
  fit's lower bound so junction noise cannot tear craters). Inherits the
  adaptive tube union's soma size. Cube size, blend reach, reject σ, and
  refit passes are all exposed in the toolbar.
- **Per-cell "Disc annotations"**: every cell can draw its raw Viking
  annotations as rings in their section planes, with color, opacity, and
  stroke width controls.
- **Cell panels reorganized as Surface / Wireframe / Disc annotations /
  Skeleton / Synapses** — each feature has its own section whose checkbox
  reveals its controls; the wireframe gains independent color and opacity.
- **Right-click context menu on rendered cells** — the RenderApp menu,
  ported: Update (re-fetch from Viking), Remove, Change Color, Change
  Transparency, Color by strata, Show Last Modified (flies to the
  annotation), Open GraphApp, Analysis, and Export (OBJ/STL/PLY/glB/DAE/
  SWC/JSON). A right CLICK opens it; right-DRAG still pans.
- **Camera roll**: q/e roll the view about the screen axis (with Shift: an
  exact 90°), Shift+drag rolls freely. The redundant w/a/s/d/q/e pan keys
  are gone — panning is right-drag.
- **Clip moved to the left column** under "General IPL options", and renders
  now always auto-update (the checkbox is gone).

- **The app now defaults to the recommended rendering: Voxel remesh with
  Envelope σ 0.5.** Newly loaded cells and the "Defaults to
  selected/all" buttons use it. The Python library and the bare mesh API keep
  Original (MATLAB) as their default, so the port stays parity-first.

- **"Defaults to selected" / "Defaults to all" buttons** reset cells to the
  default rendering (Cylinder, Original pathway, 1 pass, no decimation, no
  clip) and sync the toolbar to match. Per-cell display choices — color,
  opacity, depth coloring, wireframe, skeleton, synapses — are left alone.
  "Rebuild all" is distinct from "Apply to all" (it re-fetches under each
  cell's own settings instead of unifying them) and its help text now says so.

- **Voxel remesh is now cell-wide, one distance field per fat cluster.** A
  soma split across segmentation rows previously produced a stack of
  independent per-row envelopes — interpenetrating shells, flat "loaf" caps
  mid-soma, and rows mixing soma with thin dendrites silently falling back to
  folded tubes. All soma-scale annotations are now clustered across the whole
  cell and each cluster is one marching-cubes body; thin geometry always
  renders as tubes, anchored into the envelope. Faster too (c1178: 21 s →
  4 s), since thin dendrites no longer force fine grids.
- **Tube ends are capped** on the Adaptive ring + Taubin and Voxel remesh
  pathways. MATLAB displayed its patches double-sided, so gencyl's open pipe
  ends were never visible there; our viewer showed straight down the pipe.
  The Original pathway stays byte-faithful to MATLAB.
- **Ring detail's ceiling raised from 64 to 128 vertices per ring.** At the
  old cap every setting produced the identical clamped ring on a 10 µm soma,
  which made the control appear dead.

- **Voxel remesh now extracts the smooth outer envelope, not the literal disc
  stack.** A Gaussian is applied to the distance field before marching cubes
  (**Envelope σ**, default 0.5, 0 = literal), scaled by the body's radius so a
  10 µm soma's ~2 µm annotator radius steps smooth out while thin trunks keep
  a light touch. Disc stacks that span segmentation rows are bridged in
  z-order, so row boundaries no longer leave capped terraces mid-soma. An
  isolated soma-scale annotation between distant thin neighbors becomes a lens
  (its disc extruded half a section step) rather than a sphere sitting on the
  soma.
- **Ring detail** exposed for the adaptive pathway (target µm between ring
  vertices). Self-intersection of the tube methods is structural — no ring
  parameter can fix a swept tube folding through itself inside a soma; the
  voxel envelope is the answer there.

- **Voxel remesh no longer turns somas into amoebas.** The distance field read
  every annotation as a sphere; a 9 µm disc became a 9 µm ball bulging a full
  radius above and below its section, and the union of wandering balls was the
  lobed-blob artifact. Viking annotations are discs in the section plane, so
  runs of stacked discs (99% of the pairs inside a soma) are now *lofted* —
  the surface passes through each disc as drawn, with flat caps where a run
  ends — while genuine tube steps keep the capsule field, decided per
  consecutive pair. Soma faceting: ~3° (was 34–37° on the other pathways);
  a body now ends exactly at its annotated sections; analytic test shapes
  (cylinder, sphere-of-discs, lofted cone) reconstruct to under 1%. Also
  faster: c1178 5.3 s against the capsule field's 19 s. This supersedes the
  previous release's volume claims for this pathway, whose "analytic"
  benchmark shared the same sphere misreading.

- **Pathway** selects how a mesh is built and finished: **Original (MATLAB)**,
  **Adaptive ring + Taubin**, or **Voxel remesh**. Original is the default
  everywhere, in the library and the application — the previous release made
  adaptive+Taubin the default, which changed how every cell looked.
- **Voxel remesh**, new: thick segments are rebuilt from their signed distance
  field with marching cubes. Soma faceting drops from 37° to 0.8°. Validated
  against analytic volumes (cylinder and sphere, within 1%), and against the
  analytic lofted-cone volume of c1178's widest segment: 6,577 µm³ against
  6,542 exact, where the tube builders give 3,559 — under by 46%.
- Remeshing is applied only to segments at least 1.5 µm across. Doing every
  segment took 190 s and 2.9 M faces for c1178; the thick ones alone take 19 s
  and 735 k, for the same visible result.
- The disc-loft rewrite was adversarially reviewed by a 12-agent workflow that
  independently reproduced five defects before deployment: an invalid branch of
  the exact round-cone SDF when one endpoint ball contains the other (carved
  craters into somas; guarded, and such pairs are lofted anyway), same-z
  duplicate annotations re-inflating the amoeba (now lofted), dropped NaN
  annotations being bridged by fabricated tube (pairs now form only between
  originally-adjacent annotations), sub-voxel disc runs vanishing silently
  (kept as thin coins; neighbouring segments supply the rest of the body), and
  over-coarsened grids shattering long thin segments into beads (the remesh now
  declines and the tube builders take the segment).
- The distance field uses the exact round-cone form. The obvious
  distance-minus-interpolated-radius is only right where the radius changes
  slowly along a segment, which is precisely not what a soma does.

- **Taubin smoothing**, now the default, on both the Line3 and tube methods.
  The previous smoother is matGeom's `smoothMesh` — faithful to MATLAB, and a
  full-weight Laplacian that throws away 37% of a neuron's volume on its first
  pass, 96% by the tenth, while tearing the unwelded seams between tube
  segments apart by up to 4.9 µm. Taubin keeps 98% at two iterations. The old
  behaviour is kept as **Legacy (MATLAB)**.
- **Adaptive ring count.** `CIRCLE_POINTS` was a fixed 10 regardless of radius,
  making a 10.9 µm soma a 10-sided prism with 6.9 µm facets — the same vertex
  budget as a dendrite 62x thinner. Scaling it with the local radius drops soma
  faceting from 42° to 24°; with Taubin, to 20°. Defaults off in the library so
  the MATLAB-identical geometry the parity tests assert is unchanged, and on in
  the application.
- **Pipeline order** is selectable. Smoothing and decimation both run after
  generation and do not commute: at 30% decimation, smoothing first gives 26°
  faceting against 53° decimating first. Adaptive rings is deliberately not in
  this choice — it happens during generation, before either.
- Mesh cache keys now include these options, so two settings cannot share one
  cached mesh, and exports take the same options as the render on screen.

### Web interface

- **A technique picker in the render toolbar**, ganged to the disclosure
  arrow that opens the render panel (▲ collapsed, ▼ open). Collapsed,
  picking a technique applies it to the selected cells at once; open, it
  only sets the panel's dropdowns so a technique can be dialled in with its
  parameters first. It follows the panel both ways and reports a
  hand-tuned pair as *Custom…*.
- **Fixed: the view came loose from the volume's axes.** Three faults, all
  reachable from ordinary use of the single-axis rotations:
  - Arrow-key orbiting built its spherical in a hardcoded world-Z-up frame
    while the mouse orbited about `camera.up`, so once a roll or pitch had
    tilted the horizon the keys and the mouse turned the scene about two
    different poles. Both now go through the same viewport-axis rotation.
  - Flip Z mirrors the geometry through the scene group, but the XYZ
    indicator sat outside it and went on pointing +Z the old way — an axis
    marker lying about which way Z runs. It mirrors with the scene now.
  - Reset view did nothing at all when no cell was visible, leaving a
    rolled view rolled with no way out short of a reload; it now levels the
    horizon regardless.

- **The render toolbar is now a fixed row of view actions**, with everything
  else in two expandable panels above it: **Render options** (method,
  pathway, every parameter, the IPL clip, Apply/Defaults/Rebuild) and
  **Display & context**. Adding or removing a render knob no longer moves
  the buttons the hand already knows.
- **Named viewpoints** — XY1, XY2, XZ, YZ and 3D next to Reset view, each
  looking down one volume axis with a fixed up vector, so the same named
  view is reproducible between sessions.
- **Video recording.** Auto-rotate (turntable, tumble or roll) for a set
  duration and number of turns, or free recording while you orbit by hand.
  Quality (2.5–40 Mbps), frame rate and format are all settable; MP4 leads
  because a browser-written WebM carries no duration in its header. The
  turn is stepped from the wall clock, so a turntable returns exactly to
  its start and loops cleanly. Frames are pumped to the encoder explicitly
  rather than left to the compositor — an automatic capture of a WebGL
  canvas records a whole take of empty frames.
- **RenderApp "Plot" tab parity**, in the Display & context panel:
  - **Invert** (light viewport for figures), **Flip Z**, **2D**, and
    **Material** (default / shiny / dull / metal / flat), which survives the
    rebuild a settings change triggers.
  - **Colormaps** for IPL-depth coloring — stratify (the previous diverging
    default), parula, viridis, jet, hot, cool, bone, gray — with invert,
    **Levels** to quantize into discrete bands, and an optional colorbar.
    Changing any of them recolors in place with no server round trip.
  - **A typed viewpoint**: azimuth and elevation in MATLAB's
    `view(az, el)` convention plus distance, with Read view to capture the
    current angle. `-37.5 / 30` reproduces MATLAB's default 3-D view.
  - **Axis limits** as a live crop (six clipping planes), with Read, Clear
    and a **Hold** that stops a newly loaded cell from re-framing the view.
  - **A micron scale bar**, auto-rounding to 1/2/5 × a power of ten or
    pinned to a typed length, measured in the plane through the orbit
    target.
- **Volume context, as the MATLAB app drew it.** The fitted **INL** and
  **GCL** boundary surfaces (`GET /api/volumes/{source}/boundary/{layer}/mesh`)
  and every traced **blood vessel** (`GET /api/volumes/{source}/vessels/mesh`,
  Viking structure type 3). Context geometry is kept out of scene framing
  and out of picking, and shares one opacity slider. Vessels are decimated
  to 15% server-side: the full build is 880k triangles of smooth tube (a
  50 MB payload) that looks identical decimated.

- **100-step undo/redo** (⌘Z / Ctrl+Z, ⇧⌘Z / Ctrl+Y, or the Undo/Redo
  buttons). One history of everything: cells loaded and unloaded, colors,
  render and display settings, selection, the active tab, and the viewport —
  every finished camera move is a step of its own. Applied back by diffing,
  so undoing a camera move never rebuilds a mesh and an undone unload
  reloads from cache in well under a second.
- **Single-axis rotation.** Alongside shift-drag roll: **ctrl + left-drag**
  rotates only about the screen-horizontal axis, **⌘ + left-drag** (alt on
  Windows/Linux) only about the screen-vertical axis. The axes are the
  viewport's, not the volume's. Fixing this properly also fixed a
  long-standing subtlety: OrbitControls' orbit frame is now re-synced
  whenever the up vector changes (roll, single-axis, session restore), so
  rotations no longer drift (~2% per drag before).
- **Render compare uses fixed colors** — yellow surfaces, near-black
  (32,32,32) wireframes, blue (0,25,255) disc annotations in every pane, so
  the eye compares geometry rather than palette. Difference panes keep
  their distance map, on the surface and the wireframe alike.

- **Render compare tab.** One cell, side by side under 2–6 panes, each pane
  its own technique (Voxel remesh, Original, Adaptive + Taubin, Wrap, Tube)
  with its one defining knob (Envelope σ, ring detail, cube size) and its own
  surface / wireframe / disc-annotation overlays. The cameras are locked
  together: orbiting any pane orbits them all.
- **Difference panes measure, not just show.** A Difference pane renders
  pane A's mesh colored by its signed distance to pane B's surface — red
  outside, blue inside, on a diverging scale you can pin (±µm) or leave at
  the automatic 98th percentile — over the surface, the wireframe, or both.
  Computed server-side (`POST …/mesh-diff`): B's surface is sampled
  area-weighted and deterministically, A's vertices are measured against it
  through a k-d tree, and the legend reports mean |d|, RMS, p95 and max in
  microns alongside the measurement's own resolution (~sample spacing), so
  differences below it read as noise, not geometry. Verified against
  concentric analytic spheres in the test suite.

- **Open… / Save… are a real file browser.** Both dialogs show the folders
  pane beside the documents pane — sizes, dates and times, per-document
  delete, and delete for empty folders. Selecting a document in Open shows
  what is inside it (cells, volume, save time, whether a viewport was
  captured) before opening; double-click opens directly. In Save, clicking
  an existing document fills in its name and the button becomes
  **Overwrite**, so save-as and save-over are both explicit.
- **Capture current viewport** checkbox in the Save dialog (on by default).
  Checked, the document stores the camera position and zoom and opening it
  returns to exactly that viewpoint; unchecked, opening frames the cells.
  Workspace documents always carry every per-cell render and display
  setting — surface, wireframe, disc annotations, skeleton, per-type
  synapse settings — plus the toolbar controls.
- **Import… understands both JSON kinds.** A neuron export loads as a cell;
  a workspace document opens as a workspace, same as Open….
- **Synapses are always imported** — the "Import synapses" checkbox is
  gone. Markers start hidden on a freshly loaded cell (the counts are
  ready in its Synapses section); opening a saved workspace restores
  whatever was shown when it was saved.
- Removed the standing "Click a cell to open its settings…" note under the
  loaded list; the space now only speaks up when a re-render is pending.

- **Loaded-list tree polish.** The expand triangles are larger, an open
  cell's sections are indented further under their row, and the section rows
  (Surface, Wireframe, …) have more breathing room. The two stacked
  "Apply this cell to …" buttons are now a labelled pair — "Apply this
  cell's visualization to" over **N selected** / **all cells** side by side.
- **JSON group.** Import…, Open… and Save… live in one labelled JSON group
  box on a single row, instead of a dashed import strip above an unlabelled
  button row.

- **Save JSON… / Open…** store the current workspace — loaded cells, their
  colors and display settings, per-type synapse settings and the camera — in
  named folders on the server, under one confined directory. The annotations
  are not copied, so a saved workspace is a couple of kilobytes.
- Names are validated and the resolved path is re-checked against the base
  directory, since one regex is a typo away from a traversal. Quotas (100
  folders, 200 documents each, 5 MB per document, 200 MB total) are enforced,
  not advisory: with no authentication they are the only bound on what an
  anonymous caller can write. `ReadWritePaths` in the systemd unit had to be
  widened too, or `ProtectSystem=strict` would have failed every write.

- **Show surface**, per cell, independent of **Show wireframe**. They were
  mutually exclusive before — the wireframe was `material.wireframe` on the one
  mesh, so turning it on took the surface away. They are now separate children
  over a shared geometry (no extra geometry memory, one more draw call), so any
  combination works, including neither. Defaults per cell: surface on,
  wireframe off, skeleton off.

- **Render settings moved out of the sidebar** into a horizontal bar along the
  bottom of the 3D view, after Snapshot. The left column is now only about
  cells. The bar is one row on a normal display and wraps to two when narrow,
  rather than scrolling the Apply buttons off the right edge where nothing
  would say they exist.
- **Volume** moved from the top bar to the top of the sidebar.
- Control help is deferred: it appears on a deliberate ~1s hover and vanishes
  on leaving, instead of occupying a paragraph under every control. The
  build-cost estimate is a compact chip with its reasoning on hover.

- **Controls a method ignores are now grayed, with the reason.** Smoothing is
  discarded by the tube path and both smoothing and decimation are discarded by
  the closed-curve path, which never receives them — but all three were shown
  as live controls.
- **Build-time estimate.** The Render panel predicts how long rebuilding the
  selected cells will take on the server, from measured per-annotation rates,
  and warns past 30 s. Where the estimate is long it names the actual cause:
  Cylinder (Line3) is ~25x the work of Tube per annotation, and smoothing is
  not the expensive part despite looking like the obvious suspect.
- Plain-language help under Smoothing and Decimate.
- User-facing text, comments, documentation and identifiers converted from
  British to US spelling. MATLAB-captured ground-truth column names and
  filenames were deliberately left alone — they are recorded data.

- **Render settings are now per neuron.** The sidebar panel seeds newly loaded
  cells and is applied on demand via **Apply to selected** / **Apply to all**,
  so several neurons can share a scene under different methods. Each row in the
  Loaded list shows its method (`CYL` / `TUBE` / `CURVE`); clicking it loads
  that neuron's settings back into the panel. Exports follow the neuron's own
  method rather than whatever the panel currently reads.
- Documented right-drag panning in the viewport hint and the user guide. It
  already worked; nothing said so.
- **Sessions survive a restart.** Loaded neurons, colors, visibility,
  per-cell render settings, camera, active tab and viewport toggles are kept in
  `localStorage` and restored on load. A neuron that will no longer load is
  skipped and named rather than aborting the restore.
- **Redeploy notification.** Tabs poll the new `/api/build` every 20s and, when
  the version, asset hash or process instance changes, save the session and
  offer a reload. Replaces telling people to hard-refresh.
- **Per-cell display settings.** Each row in the Loaded list expands to its own
  panel: color by IPL depth (disabled with a note in volumes that have no
  boundary markers), color, opacity, skeleton, and synapses. Synapses are
  configured **per type** — every type on the cell gets its own visibility,
  shape, size, color and opacity, with a reset back to the cell defaults. **Apply to selected** / **Apply to all** copy them between cells, and
  **Auto-update renders** decides whether a change needing new server geometry
  re-renders at once or waits for Apply.
- **Load several cells at once.** The Structure ID box accepts lists and
  inclusive ranges (`5-10, 12, 1178`), loading sequentially and reporting any
  ids the volume does not have rather than stopping.
- Color by IPL depth moved entirely into each cell's panel; the duplicate in
  the Render panel is gone, since two controls for one setting could only
  disagree.
- **Jump to an annotation.** Right-click a row in the Annotations tab for *Show
  location on render*: it switches tabs, ensures the cell is visible and meshed,
  and flies the camera there over ~1.5s, choosing an approach direction by
  raycasting so the view does not end up inside the soma.

### Deployment

- **HTTPS at https://sbfsem-tools.com** via Let's Encrypt. `deploy/certbot-setup.sh`
  obtains the certificate using the **webroot** challenge rather than certbot's
  nginx plugin: the plugin rewrites the nginx site in place, which
  `bootstrap.sh` reinstalls from the repository on every deploy, so the plugin's
  work would be reverted at the next redeploy and the site would drop back to
  plain HTTP. `bootstrap.sh` now installs `nginx-sbfsem-tls.conf` automatically
  whenever a certificate exists, and the HTTP config otherwise — so a host that
  has never run certbot still comes up rather than failing `nginx -t`.
- The HTTPS redirect targets the canonical domain rather than `$host`, so a
  visitor arriving on the bare IP is sent to `https://sbfsem-tools.com`
  instead of `https://<old-server-ip>`, where no certificate can match and the
  browser shows a warning before anything loads. Paths are preserved.
- `deploy/nginx-sbfsem-tls.conf` is generated from the HTTP config by
  `deploy/render-nginx-tls.py`, so the proxy timeouts and gzip rules cannot
  drift between them.

- `deploy/redeploy.sh`: test, sync, install, restart, verify and restore in one
  command (~30s including the suite). Refuses to deploy a failing build,
  re-loads the neurons the server had open, and warns when a deploy shipped no
  asset change — the signature of a sync that silently did nothing.

### Fixes

- **The skeleton disappeared when a cell was made translucent.** `setStyle`
  applied the mesh's opacity to the skeleton, and a one-pixel line at 0.1
  opacity is invisible — precisely when the centerline is wanted. The skeleton
  now has its own color, width and opacity, untouched by the cell's styling.
- Skeleton **line width** is real, in microns: WebGL ignores
  `LineBasicMaterial.linewidth`, so any width above zero builds tube geometry
  rather than offering a control that does nothing. Width 0 keeps the cheap
  one-pixel line.
- **Show wireframe**, per cell — the surface as triangle edges rather than
  filled faces, which is how to see what Decimate did.

- Opacity is available while **Color by IPL depth** is on. Depth shading
  replaces a cell's color, not its transparency, but both controls were hidden
  together — so a depth-shaded cell could not be made translucent to see what
  was behind it.

- The visibility control is an **eye icon**, and the selection box moved to the
  right of the row, so the two are at opposite ends rather than adjacent and
  identical.
- **Unloading a cell is undoable for twelve seconds.** Spacing a destructive
  control away from its neighbors lowers the odds of a mis-click; it does not
  make one recoverable. The undo restores the cell's color, opacity and every
  per-type synapse override, none of which survive anywhere else.
- The eye button did not repaint when toggled: a checkbox updates itself, but
  `setVisible` emits a style event that repaints the scene and deliberately
  does not rebuild the list, so the icon has to be updated in place.

- **Selecting several cells fought with opening one.** Clicking a row both
  expanded it *and* reset the selection to that row, so the one workflow the
  Apply buttons exist for — copy cell B's settings onto A and C — was
  impossible: opening B cleared A and C. Selection is now an explicit **sel**
  checkbox that clicking the row never touches, beside a separate **show** box
  for visibility. Only one cell is expanded at a time.

- **Sliders stopped responding after the first movement.** Each `input` event
  ran a handler that re-rendered the whole loaded list, which replaced the very
  `<input>` element the pointer was holding and killed the drag. Changes that
  need the server are now debounced and the list is rebuilt only when its
  structure changes. (Not the render auto-update, which was the plausible
  suspect — the DOM rebuild was destroying the control.)
- **"Apply to selected" could not be aimed.** There was no multi-selection: the
  per-row checkbox controls visibility, and `selected` was a single focused
  cell, so "selected" never meant what the button implied. The list now has a
  real selection — click, ctrl/⌘-click, shift-click, Select all / None — kept
  separate from visibility, and every Apply action reports the count it will
  affect.
- A number box beside every slider, for values a 200-pixel track cannot
  address.
- `.cell-panel input[type="number"]` was more specific than `.numbox` and gave
  the new number boxes `width: 100%`, collapsing their own labels to zero width
  inside the flex row.

- Synapse **opacity had no visible effect** until something else forced a
  rebuild. three.js needs `material.needsUpdate` when `transparent` changes,
  because it selects the blending path and requires a shader recompile; setting
  `opacity` alone is a uniform update. The first opaque-to-translucent
  transition therefore changed the material and nothing on screen. It looked
  shape-dependent only because changing shape or size rebuilds the materials
  from scratch, so opacity applied at that moment landed on a fresh one. The
  same bug affected neuron mesh and skeleton opacity.
- **Unchecking "show synapses" left them on screen.** The refresh test fired
  only when a feature was switched *on*, so turning one off updated the state
  and never removed the objects. Hiding is also no longer queued behind Apply
  when auto-update is off, since removing something costs no server request.

- `setStyle` repainted a cell's synapse markers with the cell color, so the
  per-type Viking colors were destroyed the first time anyone touched the
  color swatch. Color and opacity now apply to the mesh and skeleton only;
  synapses have their own `setSynapseStyle`.
- The camera flight used `requestAnimationFrame`, which does not fire in a
  hidden document — a fly-to started or interrupted in a background tab left its
  promise pending and `controls.enabled` false, freezing the view permanently.
  It now completes immediately when the document is hidden.

- A camera saved before the scene was framed — possible while a large mesh was
  still building — was restored faithfully as position `[0,0,0]` looking at
  `[0,0,0]`, leaving a loaded neuron invisible with nothing reporting an error.
  `cameraState()` no longer serializes an unframed camera, and `restoreCamera()`
  rejects a degenerate, non-finite or far-off-scene viewpoint and re-frames
  instead.
- `deploy/sync.sh` no longer passes `--info=`, which macOS's bundled
  `openrsync` rejects; it detects the client and falls back to `--stats`.
- `deploy/bootstrap.sh` skips the apt step when prerequisites are already
  present and waits for the dpkg lock otherwise, so a redeploy is no longer
  broken by `unattended-upgrades`. Redeploys drop from ~90s to ~13s.
- The bootstrap health check went through nginx, so once TLS was on it saw the
  HTTP-to-HTTPS 301 — which `curl -f` reports as success — and would have
  declared the service healthy with the app down. It now queries the
  application port directly. `redeploy.sh` likewise talks to the app over SSH
  rather than the public URL, so it is unaffected by redirects or by port 443
  being closed.
- `mesh_object()` bypassed the mesh cache, so `/mesh.glb` and every binary
  export rebuilt the mesh from scratch (4.2s each). Now shares the render
  cache: 0.2-0.5s.

## 2.0.0 — Python port

The MATLAB toolbox is reimplemented in Python as two packages: `sbfsem`, a
headless analysis and rendering backend, and `sbfsem_web`, a FastAPI service with
a browser interface.

[`DEVIATIONS.md`](DEVIATIONS.md) is the authoritative record of what changed and
why. This is the summary.

### Validation

Ground truth was captured from **MATLAB R2023b** running the real toolbox against
the live Viking service (`tests/fixtures/capture/*.m`), and the port is asserted
against it offline in `tests/parity/test_matlab_ground_truth.py`.

Identical to MATLAB: node/edge/synapse tables across five neurons (bit-exact on
1,606 annotations, including the micron transform), closed-curve control points,
soma geometry, `RegularizeData3D` for all four interpolants (≤5e-11), the IPL
boundary surfaces (≤9 nm), `gencyl` grids (≤3e-14 µm), the `Line3` cylinder mesh
vertex-for-vertex (≤7e-15 µm), dendritic field hull area, and I:E counts.

Running against MATLAB also caught three defects that review alone had not: the
`nearest` interpolant rounded half-to-even where MATLAB rounds half-away-from-zero;
WKT rings kept their redundant closing vertex, giving every spline a doubled knot;
and `surf2patch` vertex ordering was row-major where MATLAB is column-major.

### Corrections that change results

Anything computed with the MATLAB version through these paths should be
recomputed.

- **IPL depth.** `util/micron2ipl.m` built the INL interpolation grid from the Y
  point vector on both axes. Measured: depth at the INL markers reads −0.0244
  where it must be 0, and the error reaches 6% of IPL depth on a probe grid. Every stratification number that flowed through it —
  `iplDepth`, `getSynapseStratification`, `singleDendriteStratification`,
  `clipMeshByStratification`, `getStrataCData`, the RenderApp histogram,
  `IPLDepthApp` — is affected. The `+ eps` that turned a zero-thickness IPL into
  a large finite number is gone too; it now yields NaN.
- **Tortuosity.** The 2-D and 3-D branches called each other's distance
  functions. Measured on a c1441 branch: MATLAB reports 2-D 13.30 / 3-D 2.17
  where the correct values are 11.69 / 2.47 — and MATLAB's 3-D value comes out
  below its 2-D value, which a correct pair never does.
- **Stratification histograms** reported left edges as bin centers, plotted right
  edges and reported the mode as a right edge. Measured on c1441: mode 0.129000
  against a true bin center of 0.097500, exactly half the 0.063000 bin width.
- **Dendrite segments** could span branch points, because the parent-linking step
  appended every remaining neighbor rather than the one the search came from.
  Measured: 4 of c943's 17 segments contain consecutive nodes that are not
  connected in the graph. Every length, diameter and tortuosity measured from
  such a segment was wrong.
- **Mitochondrial volume** squared the sum of radii instead of summing squared
  radii, with a spurious factor of two.
- **Closed-curve renders** never divided out the 100x rasterization factor, so
  they came back undersized in XY with the wrong aspect ratio.
- **Surface area and annotation sizes** counted synapse annotations as dendrite
  annotations, so their answers changed depending on whether synapses had been
  imported. Measured: 102.948 µm² against 81.251 µm² for c2542, a 27% inflation.
  `surfaceArea` also labeled microns squared as mm².
- **Soma detection** searched the whole node table, so an imported synapse
  annotation larger than the soma became the soma.
- **The "unique" synapse marker** used `Z == floor(median(Z))`, which selects no
  node for an even number of distinct sections and several when they tie.
- **Empty synapse queries** returned `[NaN NaN NaN]`, which defeated every
  `isempty` guard downstream and produced plausible distances to unrelated
  annotations.
- **`sem`** divided by the largest array dimension and counted NaNs.
- **Registration** derived offsets in one coordinate space and applied them in
  another for NeitzInferiorMonkey; `updateTransform` applied the offset twice to
  the lowest new section.
- **`vikingBasis`** measured its pixel distance after already translating one
  calibration point.
- **`ieRatio`** documented `numInh/numExc` but computed
  `numInh/(numInh+numExc)`. Both are reported now, separately named.

### Crashes and dead code, now working

`NeuronGraph` (never ran), `StructureTypes.isBoundaryMarker`, the COLLADA
exporter (rejected every neuron), `clipMeshByVertices`, `ConeOutline` (did not
load), `BloodVessel`, `Nucleolus`, `NeuronJSON` for any neuron with synapses,
`getSynapses` on a second call, offline mode, SWC segment typing, and
`GraphApp`'s annotation table, which called a class that does not exist anywhere
in the repository.

### New

- **OData pagination.** The MATLAB client never followed `@odata.nextLink`, so
  large collection queries were silently truncated.
- **Batched queries.** Synapse import, link resolution and label lookups were one
  request per item — hundreds per neuron. They are batched, with automatic
  fallback when a server rejects the compact filter form.
- **Web interface**: 3D renders, an interactive connectivity graph, stratification
  plots, annotation tables, analyses and exports, in the browser.
- **glTF/GLB export.**
- **JSON schema v2**, flat and versioned, with a reader for the MATLAB format.
- **Disconnected annotation fragments are reported** rather than silently
  measured as one neuron.
- **Recorded Viking responses and captured MATLAB output**, so every assertion in
  `NeuronTest.m`, `GraphTest.m` and `BoundaryTest.m` — and the numeric comparison
  against MATLAB itself — runs offline as a regression test.
- **Mesh face winding is normalized outward.** MATLAB's condensed mesh winds 15 of
  c943's 17 components inward and 2 outward, so its signed volume partly cancels
  (−90.123 µm³ against a true 91.727 µm³).

### Changed by decision

- **COLLADA `unit/@meter` is `1e-6`**, where the MATLAB class wrote `0.1` and the
  free function wrote `1`. This rescales every previously exported asset. Pass
  `meter=0.1` to reproduce the old output.
- **Closed-curve rasterization defaults to 0.05 µm/pixel** rather than MATLAB's
  implicit 0.01 µm.
- **Colormaps** are matplotlib equivalents; exact RGB values differ.
- The node column MATLAB called `Geometry` is `TypeCode`.
- Graphs are keyed by Viking location ID; the `nodeIDs` index array is gone.

### Not ported

`+sbfsem/+deprecated/`, `EntityType` (an empty stub), `exportSWC.m` (dead code),
`getPresynapticNeurons.m` (a name collision), `lib/MarchingCubes.m` and
`lib/SurfaceSmooth.m` (vendored but unreferenced), `Primitives` (VTK cell lists
nothing consumed), and everything that drove a MATLAB figure handle — the
frontend covers that.
