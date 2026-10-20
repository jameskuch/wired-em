"""Building again on the last whole-cell grid (manual moves, 2026-10-20).

"Redo the portion of the render where the move is": a build of a cell's annotations
moved by hand contours only the blocks the moves touched, on the grid the last build
of the cell left, and is then exactly what a full contour on that grid gives.  On
Inferior c1178 (Voxel remesh) one annotation moved 0.3 um: 2 of 2,456 blocks contoured
again, the build 5.9 s -> 2.8 s, 99.9 % of the vertices where they were (a fresh grid
moved all of them: its voxel follows the cell's whole area).
"""

from __future__ import annotations

import numpy as np
import pytest

from sbfsem.core.moves import moved_copy, normalize_moves
from sbfsem.geometry.unify import (
    GridReuse,
    GridState,
    grid_reuse,
    leaf_fingerprint,
    taubin_on_grid,
    unified_mesh,
)
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem.service.facade import SbfsemService


def _items(shift=0.0):
    """Two thin processes and a coin: stretch items as the piece assembler hands them."""
    t = np.linspace(0, 1, 30)[:, None]
    a = np.hstack([t * 20.0, np.zeros_like(t), t * 4.0])
    b = np.hstack([np.zeros_like(t) + 6.0, t * 18.0 + 1.0, t * 3.0])
    b = b.copy()
    b[12:15, 0] += shift            # three discs of the second process, moved in X
    return [("stretch", a, np.full(len(a), 0.6)),
            ("stretch", b, np.full(len(b), 0.5)),
            ("coin", np.array([15.0, 10.0, 1.0]), 0.8)]


def _arrays(mesh):
    return np.asarray(mesh.vertices), np.asarray(mesh.faces)


def test_a_build_on_the_kept_grid_is_a_full_contour_on_it_with_only_touched_blocks_contoured():
    keep = GridReuse(keep=True)
    with grid_reuse(keep):
        base = unified_mesh(_items(), voxel_um=0.2, tie_um=0)
    state = keep.out
    assert state is not None and state.signatures and state.tris
    assert keep.stats["reused"] is False and keep.stats["contoured"] == keep.stats["blocks"]

    reused_ctx = GridReuse(state=state, keep=True)
    with grid_reuse(reused_ctx):
        reused = unified_mesh(_items(shift=0.4), voxel_um=0.2, tie_um=0)
    full_ctx = GridReuse(state=state, recontour_all=True)
    with grid_reuse(full_ctx):
        full = unified_mesh(_items(shift=0.4), voxel_um=0.2, tie_um=0)

    rv, rf = _arrays(reused)
    fv, ff = _arrays(full)
    assert np.array_equal(rv, fv) and np.array_equal(rf, ff), "exactly a full contour on that grid"
    stats = reused_ctx.stats
    assert stats["reused"] and 0 < stats["contoured"] < stats["blocks"] // 2, stats
    assert reused.meta["voxel2_reused_blocks"] == stats["blocks"] - stats["contoured"]
    assert reused.meta["voxel2_um"] == pytest.approx(state.voxel)
    # The surface away from the move is the base's, vertex for vertex.
    bv, _ = _arrays(base)
    kept = {tuple(v) for v in np.round(bv, 9)}
    same = np.mean([tuple(v) in kept for v in np.round(rv, 9)])
    assert same > 0.5, same
    # The new build leaves its own grid for the next.
    assert reused_ctx.out is not None and reused_ctx.out.voxel == state.voxel


def test_nothing_moved_contours_nothing_and_a_move_below_the_grid_starts_over():
    keep = GridReuse(keep=True)
    with grid_reuse(keep):
        unified_mesh(_items(), voxel_um=0.2, tie_um=0)
    same = GridReuse(state=keep.out)
    with grid_reuse(same):
        unified_mesh(_items(), voxel_um=0.2, tie_um=0)
    assert same.stats["reused"] and same.stats["contoured"] == 0
    # A disc moved past the grid's low corner: the old origin cannot hold it.
    items = _items()
    items[0][1][0, 0] -= 5.0
    below = GridReuse(state=keep.out)
    with grid_reuse(below):
        unified_mesh(items, voxel_um=0.2, tie_um=0)
    assert below.stats["reused"] is False


def test_another_kind_of_grid_is_not_reused_and_a_second_contour_asks_for_none():
    keep = GridReuse(keep=True)
    with grid_reuse(keep):
        unified_mesh(_items(), voxel_um=0.2, tie_um=0)
    other = GridReuse(state=keep.out)
    with grid_reuse(other):
        unified_mesh(_items(), voxel_um=0.2, tie_um=0, blend_um=0.3)
    assert other.stats["reused"] is False, "a fillet is another grid"
    twice = GridReuse(state=keep.out, keep=True)
    with grid_reuse(twice):
        unified_mesh(_items(), voxel_um=0.2, tie_um=0)
        first_out = twice.out
        unified_mesh(_items(), voxel_um=0.2, tie_um=0)
    assert twice.out is first_out, "the second contour of one build leaves nothing"


def _raster(dent=False):
    """A traced slab of 60 x 60 um over 30 sections at 0.25 um, with a dent at one corner."""
    from sbfsem.geometry.closed_curve import CurveVolume

    occ = np.zeros((240, 240, 30), dtype=bool)
    occ[20:220, 20:220, 3:27] = True
    if dent:
        occ[25:45, 25:45, 12:15] = False      # three sections near one corner: a few tiles
    return CurveVolume.from_dense(occ, origin_um=[0.0, 0.0, 0.0], spacing_um=[0.25, 0.25, 0.07],
                                  sections=np.arange(100, 130), tile=16)


def test_an_outline_raster_is_fingerprinted_by_the_tiles_each_block_reads():
    """Hybrid's outline half is one leaf; a move redraws a few of its tiles, and only the
    blocks those reach are contoured again (c1178's soma disc: 537 -> 150 of 1,833)."""
    keep = GridReuse(keep=True)
    with grid_reuse(keep):
        unified_mesh([("curve", _raster(), 4 * 200 * 200 * 0.0625)], voxel_um=0.2, tie_um=0)
    assert keep.out is not None and keep.stats["blocks"] >= 100
    ctx = GridReuse(state=keep.out, keep=True)
    with grid_reuse(ctx):
        reused = unified_mesh([("curve", _raster(dent=True), 4 * 200 * 200 * 0.0625)],
                              voxel_um=0.2, tie_um=0)
    full = GridReuse(state=keep.out, recontour_all=True)
    with grid_reuse(full):
        forced = unified_mesh([("curve", _raster(dent=True), 4 * 200 * 200 * 0.0625)],
                              voxel_um=0.2, tie_um=0)
    rv, rf = _arrays(reused)
    fv, ff = _arrays(forced)
    assert np.array_equal(rv, fv) and np.array_equal(rf, ff), "exactly a full contour on that grid"
    stats = ctx.stats
    assert stats["reused"] and 0 < stats["contoured"] < stats["blocks"] // 10, stats


def test_a_cluster_field_is_fingerprinted_by_the_window_each_block_reads():
    """A cluster field's blocks far from a change keep their fingerprint; near it, and
    everywhere when its calibrated level moves (a soma's), they do not."""
    from types import SimpleNamespace

    from sbfsem.geometry.remesh import SampledField
    from sbfsem.geometry.unify import _FieldLeaf

    rng = np.random.default_rng(3)
    values = rng.normal(size=(60, 30, 30)).astype(np.float32)
    grid = SimpleNamespace(origin=np.zeros(3), block=8, voxel=0.25)       # a block: 2 um, 4 field samples
    leaf = lambda f, level=0.1: _FieldLeaf(SampledField(f, np.zeros(3), 0.5, level))  # noqa: E731
    changed = values.copy()
    changed[2, 2, 2] += 0.5                                             # one sample near the low corner
    a, b = leaf(values), leaf(changed)
    near, far = (0, 0, 0), (12, 2, 2)                                  # 0 um and 24 um away in X
    assert a.block_fingerprint(grid, near) != b.block_fingerprint(grid, near)
    assert a.block_fingerprint(grid, far) == b.block_fingerprint(grid, far)
    assert leaf(values, 0.1).block_fingerprint(grid, far) != leaf(values, 0.2).block_fingerprint(grid, far)


def _bare(state):
    """``state`` without what its tie step and smoothing left: a build on it ties and smooths
    the whole sheet, the reference a local build must equal."""
    return GridState(state.voxel, state.origin, state.block, state.blend, state.mode, state.threshold,
                     state.work, state.signatures, state.tris)


def test_smoothing_again_around_the_change_is_smoothing_the_whole_sheet():
    keep = GridReuse(keep=True)
    with grid_reuse(keep):
        base = unified_mesh(_items(), voxel_um=0.1, tie_um=0)
        first = taubin_on_grid(base, 1)
    assert first is not None and np.array_equal(first.vertices, base.taubin(1).vertices)
    assert keep.out.smoothed and keep.out.smooth_key == (1, 0.5, -0.53)
    ctx = GridReuse(state=keep.out, keep=True)
    with grid_reuse(ctx):
        moved = unified_mesh(_items(shift=0.4), voxel_um=0.1, tie_um=0)
        local = taubin_on_grid(moved, 1)
    assert local is not None
    assert np.array_equal(local.vertices, moved.taubin(1).vertices), "to the bit"
    assert 0 < ctx.stats["smoothed_again"] < len(moved.vertices) // 4, ctx.stats
    assert local.meta["voxel2_smoothed_again"] == ctx.stats["smoothed_again"]
    # The next build smooths around its own change from this one's.
    nxt = GridReuse(state=ctx.out, keep=True)
    with grid_reuse(nxt):
        again = unified_mesh(_items(shift=0.8), voxel_um=0.1, tie_um=0)
        local2 = taubin_on_grid(again, 1)
    assert np.array_equal(local2.vertices, again.taubin(1).vertices)
    # Passes that reach past a block smooth the whole sheet; outside a grid build, nothing.
    far = GridReuse(state=ctx.out, keep=True)
    with grid_reuse(far):
        m = unified_mesh(_items(shift=0.8), voxel_um=0.1, tie_um=0)
        whole = taubin_on_grid(m, 20)
    assert np.array_equal(whole.vertices, m.taubin(20).vertices)
    assert far.stats["smoothed_again"] == len(m.vertices) and "voxel2_smoothed_again" not in whole.meta
    assert taubin_on_grid(m, 1) is None, "outside a whole-cell build, the caller smooths"


def _two_pieces(shift_far=0.0, shift_near=0.0):
    """A process and a short one 1.8 um past its end: one gap, one tie."""
    t = np.linspace(0, 1, 40)[:, None]
    a = np.hstack([t * 20.0, np.zeros_like(t), np.zeros_like(t)]).copy()
    a[3:6, 1] += shift_far            # 17 um from the gap
    a[37:40, 1] += shift_near         # at the gap
    c = np.hstack([23.0 + t[:10] * 3.0, np.zeros((10, 1)), np.zeros((10, 1))])
    return [("stretch", a, np.full(len(a), 0.6)), ("stretch", c, np.full(len(c), 0.6))]


def test_the_last_builds_ties_hold_where_the_moves_are_far_from_any_gap():
    keep = GridReuse(keep=True)
    with grid_reuse(keep):
        base = unified_mesh(_two_pieces(), voxel_um=0.1, tie_um=5.0)
    assert base.meta["voxel2_ties"] == 1 and base.meta["voxel2_components"] == 1
    assert keep.out.ties is not None and len(keep.out.ties["pieces"]) == 2
    ctx = GridReuse(state=keep.out, keep=True)
    with grid_reuse(ctx):
        kept = unified_mesh(_two_pieces(shift_far=0.3), voxel_um=0.1, tie_um=5.0)
    ref_ctx = GridReuse(state=_bare(keep.out), keep=True)
    with grid_reuse(ref_ctx):
        ref = unified_mesh(_two_pieces(shift_far=0.3), voxel_um=0.1, tie_um=5.0)
    assert ctx.stats["ties_kept"] and not ref_ctx.stats["ties_kept"]
    assert kept.meta["voxel2_ties_kept"] is True
    assert np.array_equal(kept.vertices, ref.vertices) and np.array_equal(kept.faces, ref.faces)
    assert kept.meta["voxel2_components"] == ref.meta["voxel2_components"] == 1
    # Within a tie's reach of the other piece, the gaps are searched again.
    near = GridReuse(state=keep.out, keep=True)
    with grid_reuse(near):
        unified_mesh(_two_pieces(shift_near=0.3), voxel_um=0.1, tie_um=5.0)
    assert near.stats["ties_kept"] is False
    # A move that joins the two pieces changes them: searched again.
    joined = _two_pieces()
    joined[0] = ("stretch", np.vstack([joined[0][1], [[23.0, 0.0, 0.0]]]), np.full(41, 0.6))
    merge = GridReuse(state=keep.out, keep=True)
    with grid_reuse(merge):
        m = unified_mesh(joined, voxel_um=0.1, tie_um=5.0)
    assert merge.stats["ties_kept"] is False and m.meta["voxel2_ties"] == 0


def test_fingerprints_follow_content():
    from sbfsem.geometry.unify import Capsule

    a = Capsule(np.array([0.0, 0, 0]), np.array([1.0, 0, 0]), 0.5, 0.4)
    b = Capsule(np.array([0.0, 0, 0]), np.array([1.0, 0, 0]), 0.5, 0.4)
    c = Capsule(np.array([0.0, 0, 0]), np.array([1.0, 0, 1e-9]), 0.5, 0.4)
    assert leaf_fingerprint(a) == leaf_fingerprint(b) != leaf_fingerprint(c)
    assert leaf_fingerprint(object()) is None


# -- the service ----------------------------------------------------------------------------------

@pytest.fixture
def service(i943_path):
    svc = SbfsemService(offline=True)
    svc.add_neuron(read_legacy_json(i943_path))
    yield svc
    svc.close()


OPTS = {"pathway": "voxel2", "voxel2_um": 0.15}


def test_a_cell_nobody_moves_keeps_no_grid(service):
    service.render_mesh(943, "i", method="cylinder", **OPTS)
    assert not service._grid_states, "a plain render pays nothing for the moves' grids"


def test_the_service_keeps_the_grid_move_mode_asks_for_and_builds_a_moved_copy_on_it(service):
    base = service.neuron(943, "i")
    service.prepare_grid(943, "i", method="cylinder", **OPTS)
    service.render_mesh(943, "i", method="cylinder", **OPTS)
    assert len(service._grid_states) == 1, "move mode's cell keeps its grid"
    own = base.nodes[base.nodes["ParentID"] == base.id]
    service.load_neuron(943, "i", section_corrections="off", live_registration=False,
                        manual_moves={"off.0": [{"id": int(own.iloc[len(own) // 2].ID), "dx_um": 0.3}]})
    moved = service.render_mesh(943, "i", method="cylinder", **OPTS)
    assert moved.n_faces > 0
    mesh = service.mesh_object(943, "i", method="cylinder", **OPTS)
    assert mesh.meta.get("voxel2_reused_blocks", 0) > 0, "built on the kept grid"
    # A plain copy never builds on a kept grid: its surface does not depend on history.
    plain = base
    service._meshes.clear()
    rebuilt = service.build_mesh(plain, method="cylinder", **OPTS)
    assert "voxel2_reused_blocks" not in rebuilt.meta
    ctx = service._grid_context(plain, service._grid_key(plain, "cylinder", 1, 1.0, 0.05,
                                                          service._mesh_options(**OPTS)))
    assert ctx is not None and ctx.state is None and ctx.keep


def test_a_moved_copy_built_locally_is_the_build_that_ties_and_smooths_it_all(service):
    """Through the service's own build: c943 moved by hand, on the kept grid, smoothed again
    around the move, against the same build smoothing the whole sheet.  (All of c943 lies
    within a tie's reach of its one gap, so its tie is searched again: the synthetic
    two-piece test above keeps one.)"""
    opts = {"pathway": "voxel2", "voxel2_um": 0.08}
    base = service.neuron(943, "i")
    keep = GridReuse(keep=True)
    with grid_reuse(keep):
        service.build_mesh(base, method="cylinder", **opts)
    own = base.nodes[base.nodes["ParentID"] == base.id]
    moved = moved_copy(base, normalize_moves([{"id": int(own.iloc[len(own) // 2].ID), "dx_um": 0.3}]))
    ctx = GridReuse(state=keep.out, keep=True)
    with grid_reuse(ctx):
        local = service.build_mesh(moved, method="cylinder", **opts)
    with grid_reuse(GridReuse(state=_bare(keep.out), keep=True)):
        whole = service.build_mesh(moved, method="cylinder", **opts)
    assert 0 < ctx.stats["smoothed_again"] < len(whole.vertices), ctx.stats
    assert np.array_equal(local.vertices, whole.vertices) and np.array_equal(local.faces, whole.faces)


def test_prepare_grid_builds_once_and_not_for_a_technique_without_one(service):
    first = service.prepare_grid(943, "i", method="cylinder", **OPTS)
    assert first["ready"] and first["built"]
    again = service.prepare_grid(943, "i", method="cylinder", **OPTS)
    assert again == {"ready": True, "built": False, "seconds": 0.0}
    none = service.prepare_grid(943, "i", method="cylinder", pathway="original")
    assert none["ready"] is False and "no whole-cell grid" in none["reason"]


def test_the_kept_grids_are_held_to_their_cap(service, monkeypatch):
    service._grid_cache_mb = 1e-6
    service.prepare_grid(943, "i", method="cylinder", **OPTS)
    service.prepare_grid(943, "i", method="cylinder", **{**OPTS, "voxel2_um": 0.2})
    # Over the cap: what does not fit goes, never past it.
    assert len(service._grid_states) <= 1
    service._grid_cache_mb = 0
    assert service._grid_key(service.neuron(943, "i"), "cylinder", 1, 1.0, 0.05,
                             service._mesh_options(**OPTS)) is None


def test_moved_copy_from_the_moves_module_is_what_the_service_builds(c943):
    own = c943.nodes[c943.nodes["ParentID"] == c943.id]
    moved = moved_copy(c943, normalize_moves([{"id": int(own.iloc[3].ID), "dx_um": 0.2}]))
    assert moved.manual_moves["n_moved"] == 1
