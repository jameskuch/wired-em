"""The annotation wireframe: rings from the annotations, averaged along Z by scale."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sbfsem.geometry.annotation_wire import (
    _best_shift,
    _clamp_into,
    annotation_wire,
)

DZ = 0.09  # um per section, Inferior Monkey


def _nodes(rows):
    """rows: (id, x, y, section, radius[, type])."""
    out = []
    for row in rows:
        loc, x, y, sec, r = row[:5]
        out.append({"ID": loc, "Xum": x, "Yum": y, "Zum": sec * DZ, "Z": sec, "Rum": r,
                    "TypeCode": row[5] if len(row) > 5 else 1})
    return pd.DataFrame(out)


def _stack(radii, centers, first_id=1, first_section=0):
    rows = [(first_id + k, c[0], c[1], first_section + k, r)
            for k, (r, c) in enumerate(zip(radii, centers, strict=True))]
    links = [(first_id + k, first_id + k + 1) for k in range(len(radii) - 1)]
    return rows, links


def _size(xy):
    return float(np.hypot(*(xy - xy.mean(axis=0)).T).mean())


def _sphere(radius_um=5.0, noise=0.0, seed=0):
    """A ball of discs, one per section, with tracing noise in size and center."""
    rng = np.random.default_rng(seed)
    n = int(2 * radius_um / DZ)
    z = (np.arange(n) - (n - 1) / 2) * DZ
    true = np.sqrt(np.maximum(radius_um ** 2 - z ** 2, 0.04))
    radii = true * (1 + noise * rng.standard_normal(n))
    centers = noise * radius_um * rng.standard_normal((n, 2))
    return true, radii, centers


def test_gain_zero_draws_the_annotations_exactly():
    _true, radii, centers = _sphere(noise=0.05)
    rows, links = _stack(radii, centers)
    wire = annotation_wire(_nodes(rows), links, gain=0)
    assert len(wire.chains) == 1 and not wire.ties
    for ring in wire.rings:
        np.testing.assert_allclose(ring.xy, ring.raw)
        assert ring.sigma_um == 0


def test_a_soma_is_averaged_without_shrinking_and_ends_stay_as_annotated():
    true, radii, centers = _sphere(noise=0.05)
    rows, links = _stack(radii, centers)
    wire = annotation_wire(_nodes(rows), links, gain=0.5)
    chain = wire.chains[0]
    raw = np.array([_size(wire.rings[i].raw) for i in chain])
    fitted = np.array([_size(wire.rings[i].xy) for i in chain])
    # Noise goes down severalfold ...
    inner = slice(10, -10)
    raw_err = np.abs(raw - true)[inner].mean()
    fit_err = np.abs(fitted - true)[inner].mean()
    assert fit_err < raw_err / 3
    # ... with no systematic shrink at the equator (a mean loses ~R/8 there).
    mid = len(chain) // 2
    assert abs(fitted[mid - 5:mid + 5].mean() - true[mid - 5:mid + 5].mean()) < 0.1
    # The top and bottom rings are what the annotator drew.
    for i in (chain[0], chain[-1]):
        np.testing.assert_allclose(wire.rings[i].xy, wire.rings[i].raw)
    # Rings never leave their section planes.
    assert [wire.rings[i].z_um for i in chain] == pytest.approx(
        [r[3] * DZ for r in rows])


def test_a_mean_flattens_the_dome_that_the_quadratic_keeps():
    true, radii, centers = _sphere(noise=0.0)
    rows, links = _stack(radii, centers)
    mid = len(radii) // 2
    out = {}
    for fit in ("mean", "quadratic"):
        wire = annotation_wire(_nodes(rows), links, gain=0.5, fit=fit)
        out[fit] = _size(wire.rings[wire.chains[0][mid]].xy) - true[mid]
    assert out["mean"] < -0.3                 # pulled well inside the equator
    assert abs(out["quadratic"]) < 0.05       # follows the dome (1 % of R)


def test_the_window_scales_with_the_ring_so_thin_dendrites_barely_move():
    rng = np.random.default_rng(1)
    n = 60
    thin = 0.2 * (1 + 0.1 * rng.standard_normal(n))
    fat = 6.0 * (1 + 0.1 * rng.standard_normal(n))
    rows_a, links_a = _stack(thin, np.zeros((n, 2)), first_id=1)
    rows_b, links_b = _stack(fat, np.zeros((n, 2)), first_id=1001)
    wire = annotation_wire(_nodes(rows_a + rows_b), links_a + links_b, gain=0.5)
    # The middle of each 60-section stack, where the chain's ends do not cap
    # the window.
    sigma = {round(r.radius_um) == 0: r.sigma_um for r in wire.rings
             if 25 < r.location_id % 1000 < 35}
    assert sigma[True] < 0.15 and sigma[False] > 1.0
    thin_moved = np.median([r.moved_um / r.radius_um for r in wire.rings if r.location_id < 1000])
    fat_moved = np.median([r.moved_um / r.radius_um for r in wire.rings if r.location_id > 1000])
    assert thin_moved < fat_moved


def test_sideways_wander_within_a_ring_is_not_distance_but_travel_is():
    # A soma whose center wanders 0.2 um a section keeps its Z window; a thin
    # dendrite moving 1 um a section in-plane is a long way along.
    big = [(k + 1, 0.2 * (k % 2), 0.0, k, 6.0) for k in range(10)]
    thin = [(k + 101, 1.0 * k, 0.0, k, 0.2) for k in range(10)]
    links = [(k + 1, k + 2) for k in range(9)] + [(k + 101, k + 102) for k in range(9)]
    wire = annotation_wire(_nodes(big + thin), links)
    by_first = {wire.rings[c[0]].location_id: s for c, s in zip(wire.chains, wire.positions, strict=True)}
    assert by_first[1][-1] == pytest.approx(9 * DZ, rel=0.01)
    # 1 um sideways on a 0.2 um ring counts 1 / 1.2 = 0.83 um a step.
    assert by_first[101][-1] == pytest.approx(9 * np.hypot(DZ, 1 / 1.2))


def test_branches_and_size_jumps_become_ties_not_chain_steps():
    # A soma stack with a dendrite leaving its middle ring, and a Y fork.
    soma, soma_links = _stack([2, 4, 6, 6, 4, 2], np.zeros((6, 2)), first_id=1)
    dend = [(20, 7.0, 0.0, 3, 0.4), (21, 9.0, 0.0, 4, 0.4), (22, 11.0, 0.0, 5, 0.4)]
    fork = [(23, 12.0, 1.0, 6, 0.3), (24, 12.0, -1.0, 6, 0.3)]
    links = soma_links + [(3, 20), (20, 21), (21, 22), (22, 23), (22, 24)]
    wire = annotation_wire(_nodes(soma + dend + fork), links)
    ids = [[wire.rings[i].location_id for i in c] for c in wire.chains]
    assert [1, 2, 3, 4, 5, 6] in ids                       # the soma is one chain
    assert not any(20 in c and 3 in c for c in ids)        # 6 um -> 0.4 um: a tie
    assert sum(1 for c in ids if 23 in c or 24 in c) == 2  # two forks, two chains
    ties = {(wire.rings[a].location_id, wire.rings[b].location_id) for a, b in wire.ties}
    assert (20, 3) in ties                                 # smaller ring onto the larger
    assert len(wire.ties) == 2                             # soma-dendrite and one fork arm


def test_a_chain_never_turns_back_through_an_annotation():
    # Two dendrites both leaving an annotation upward must not be one chain.
    rows = [(1, 0, 0, 0, 0.3), (2, -1, 0, 1, 0.3), (3, 1, 0, 1, 0.3)]
    wire = annotation_wire(_nodes(rows), [(1, 2), (1, 3)])
    assert len(wire.chains) == 2 and len(wire.ties) == 1


def test_closed_curves_are_rings_of_their_outlines():
    t = np.linspace(0, 2 * np.pi, 200, endpoint=False)
    ellipse = np.column_stack([3 * np.cos(t), np.sin(t)])
    rows = [(1, 0, 0, 0, 9.9, 6), (2, 0, 0, 1, 9.9, 6), (3, 0, 0, 2, 0.5)]
    # One outline clockwise and closed, one rotated to start elsewhere.
    outlines = {1: np.vstack([ellipse[::-1], ellipse[-1:]]),
                2: np.roll(ellipse, 37, axis=0)}
    wire = annotation_wire(_nodes(rows), [(1, 2)], outlines, gain=0)
    curve = [r for r in wire.rings if r.kind == "curve"]
    assert len(curve) == 2
    for ring in curve:
        assert ring.radius_um == pytest.approx(np.sqrt(3.0), rel=0.01)   # area 3 pi
        x, y = ring.raw[:, 0], ring.raw[:, 1]
        assert (x * np.roll(y, -1) - np.roll(x, -1) * y).sum() > 0      # counterclockwise
    # Lined up point for point, so lines along do not twist.
    np.testing.assert_allclose(curve[0].raw, curve[1].raw, atol=0.1)
    # A closed curve with no outline is not drawn as its stored radius's circle
    # silently: it falls back to that circle, as a disc.
    lone = annotation_wire(_nodes([(9, 0, 0, 0, 2.0, 6)]), [], {})
    assert lone.rings[0].kind == "disc" and lone.rings[0].radius_um == 2.0


def test_best_shift_undoes_a_rotation():
    t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    shape = np.column_stack([2 * np.cos(t) + 0.3 * np.cos(3 * t), np.sin(t)])
    rolled = np.roll(shape, -7, axis=0) + 5.0
    shift = _best_shift(shape, rolled)
    np.testing.assert_allclose(np.roll(rolled, -shift, axis=0) - 5.0, shape, atol=1e-9)


def test_ties_drop_onto_the_annotation_or_its_rim():
    square = np.array([[0, 0], [2, 0], [2, 2], [0, 2]], dtype=float)
    pts = np.array([[1.0, 1.0], [3.0, 1.0], [-1.0, 3.0]])
    np.testing.assert_allclose(_clamp_into(pts, square), [[1, 1], [2, 1], [0, 2]])


def test_drawing_choices():
    radii = np.full(30, 1.0)
    rows, links = _stack(radii, np.zeros((30, 2)))
    wire = annotation_wire(_nodes(rows), links)
    n = wire.rings[0].n_points
    assert len(wire.ring_polylines(0.0)) == 30
    spaced = wire.ring_polylines(0.5)
    zs = [z for z, _ in spaced]
    assert zs[0] == 0 and zs[-1] == pytest.approx(29 * DZ)
    assert all(b - a >= 0.5 - 1e-9 for a, b in zip(zs[:-2], zs[1:-1], strict=True))
    assert len(wire.line_polylines(12)) == min(12, n)
    assert all(line.shape == (30, 3) for line in wire.line_polylines(12))
    assert wire.line_polylines(0) == []
    segs = wire.segments(12, 0.0)
    assert segs.shape == (30 * n + min(12, n) * 29, 2, 3)


def test_unknown_fit_is_refused():
    with pytest.raises(ValueError):
        annotation_wire(_nodes([(1, 0, 0, 0, 1.0)]), [], fit="cubic")


def test_empty_and_degenerate_input():
    assert annotation_wire(pd.DataFrame(columns=["ID", "Xum", "Yum", "Zum", "Z", "Rum"]),
                           []).rings == []
    rows = [(1, 0, 0, 0, 0.0), (2, np.nan, 0, 1, 1.0), (3, 0, 0, 2, 1.0)]
    wire = annotation_wire(_nodes(rows), [(1, 2), (2, 3), (3, 3)])
    assert [r.location_id for r in wire.rings] == [3] and not wire.ties


def test_endpoint(tmp_path, request):
    from fastapi.testclient import TestClient

    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem_web.app import create_app

    app = create_app()
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as client:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(fixture))
        body = client.get("/api/neurons/i/943/annotation-wire").json()
        assert body["neuron_id"] == 943
        assert body["n_annotations"] > 0 and len(body["rings"]) == body["n_annotations"]
        assert all(len(r) % 2 == 1 for r in body["rings"])
        assert all(len(line) % 3 == 0 for line in body["lines"]) and len(body["ties"]) % 6 == 0
        assert body["moved_um"]["n"] == body["n_annotations"]
        thinned = client.get("/api/neurons/i/943/annotation-wire?ring_spacing_um=2&lines=0").json()
        assert len(thinned["rings"]) < len(body["rings"]) and thinned["lines"] == []
        flat = client.get("/api/neurons/i/943/annotation-wire?gain=0").json()
        assert flat["moved_um"]["max"] == 0
        assert client.get("/api/neurons/i/943/annotation-wire?fit=cubic").status_code == 422
        assert client.get("/api/neurons/i/999999/annotation-wire").status_code == 404


@pytest.fixture
def wire_client(request):
    from fastapi.testclient import TestClient

    from sbfsem.io.legacy_json import read_legacy_json
    from sbfsem_web.app import create_app

    app = create_app()
    fixture = request.path.parents[1] / "fixtures" / "i943.json"
    with TestClient(app) as client:
        app.state.service.offline = True
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield client


@pytest.mark.parametrize("fmt", ["obj", "ply", "stl", "csv", "tsv", "npy", "npz", "h5"])
def test_export_formats(wire_client, fmt):
    response = wire_client.get(f"/api/neurons/i/943/annotation-wire/export/{fmt}")
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        f'attachment; filename="c943-NeitzInferiorMonkey-annotation-wire.{fmt}"')
    assert len(response.content) > 100


def test_export_refuses_what_is_not_a_line_format(wire_client):
    base = "/api/neurons/i/943/annotation-wire/export"
    assert wire_client.get(f"{base}/HDF5").status_code == 200
    assert wire_client.get(f"{base}/swc").status_code == 400
    assert wire_client.get(f"{base}/glb").status_code == 400
    assert wire_client.get(f"{base}/obj?fit=cubic").status_code == 422
    assert wire_client.get(f"{base}/obj?gain=9").status_code == 422
    assert wire_client.get("/api/neurons/i/999999/annotation-wire/export/obj").status_code == 404


def test_export_is_the_wireframe_built_with_the_settings_given(wire_client, tmp_path):
    from sbfsem.export.mesh_formats import segment_edges
    from sbfsem.geometry.annotation_wire import annotation_wire_from_neuron

    svc = wire_client.app.state.service
    settings = {"gain": 0.25, "fit": "mean", "size_tolerance": 2.0, "break_ratio": 4.0,
                "lines": 4, "ring_spacing_um": 1.0}
    seen: list[dict] = []
    original = svc.annotation_wire_segments

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return original(*args, **kwargs)

    svc.annotation_wire_segments = spy
    query = "&".join(f"{k}={v}" for k, v in settings.items())
    response = wire_client.get(f"/api/neurons/i/943/annotation-wire/export/npy?{query}")
    assert response.status_code == 200
    assert seen == [settings]

    path = tmp_path / "w.npy"
    path.write_bytes(response.content)
    got = np.load(path)
    built = annotation_wire_from_neuron(
        svc.neuron(943, "i"), gain=0.25, fit="mean", size_tolerance=2.0, break_ratio=4.0)
    v, edges = segment_edges(built.segments(lines=4, spacing_um=1.0))
    np.testing.assert_allclose(got, v[edges])

    # Fewer lines and wider ring spacing draw fewer segments; the file says how it was built.
    default = wire_client.get("/api/neurons/i/943/annotation-wire/export/obj").text
    thinned = wire_client.get(f"/api/neurons/i/943/annotation-wire/export/obj?{query}").text
    assert thinned.count("\nl ") < default.count("\nl ")
    header = [ln for ln in thinned.splitlines() if ln.startswith("#")]
    assert "# source NeitzInferiorMonkey" in header and "# neuron_id 943" in header
    assert "# gain 0.25" in header and "# fit mean" in header and "# lines 4" in header


def test_concurrent_requests_for_the_kept_wireframes_do_not_trample_each_other(wire_client):
    """The kept builds are one ordered dict that every request moves entries in and
    evicts from: two threads at once could pop the entry the other was about to move.
    A build for a cell and its settings is made by one request at a time, and the
    others wait for it."""
    import threading
    import time
    from collections import OrderedDict

    import sbfsem.service.facade as facade_mod

    svc = wire_client.app.state.service
    neuron = svc.neuron(943, "i")

    class Watched(OrderedDict):
        """The kept builds, failing the moment any request touches them without the
        service's lock: single flight alone passes with both ``with self._lock`` blocks
        removed, because the requests it lets through are for different settings and
        each moves and evicts entries of the one dict."""

        def _held(self, what: str) -> None:
            assert svc._lock._is_owned(), f"kept wireframes: {what} without the service lock"

        def get(self, *args, **kwargs):
            self._held("get")
            return super().get(*args, **kwargs)

        def move_to_end(self, *args, **kwargs):
            self._held("move_to_end")
            return super().move_to_end(*args, **kwargs)

        def __setitem__(self, key, value):
            self._held("set")
            super().__setitem__(key, value)

        def popitem(self, *args, **kwargs):
            self._held("popitem")
            return super().popitem(*args, **kwargs)

        def __len__(self):
            self._held("len")
            return super().__len__()

    neuron.analysis["annotation_wire"] = Watched()
    lock = threading.Lock()
    active: dict[float, int] = {}
    most_at_once = 0
    built: set[float] = set()
    real = facade_mod.wire.annotation_wire_from_neuron

    def counted(neuron_, **settings):
        nonlocal most_at_once
        gain = settings["gain"]
        with lock:
            active[gain] = active.get(gain, 0) + 1
            most_at_once = max(most_at_once, active[gain])
            built.add(gain)
        time.sleep(0.05)  # long enough for the others to arrive
        try:
            return real(neuron_, **settings)
        finally:
            with lock:
                active[gain] -= 1

    errors: list[Exception] = []

    def ask(gain: float) -> None:
        try:
            svc._annotation_wire_built(neuron, gain=gain, size_tolerance=1.5, break_ratio=2.5, fit="quadratic")
        except Exception as exc:  # noqa: BLE001 - the point is that none escapes
            errors.append(exc)

    facade_mod.wire.annotation_wire_from_neuron = counted
    try:
        # 4 threads for each of 9 settings (the service keeps ANNOTATION_WIRE_KEEP of them).
        threads = [threading.Thread(target=ask, args=(g / 10,)) for g in range(9) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
    finally:
        facade_mod.wire.annotation_wire_from_neuron = real
    assert errors == []
    assert 0 < OrderedDict.__len__(neuron.analysis["annotation_wire"]) <= svc.ANNOTATION_WIRE_KEEP
    assert built == {g / 10 for g in range(9)}
    assert most_at_once == 1, "two requests built the same wireframe at once"
