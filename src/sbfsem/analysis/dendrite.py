"""Dendrite morphometrics.

Ports ``util/analysis/tortuosity.m``, ``util/analysis/singleDendriteDiameter.m``,
``+sbfsem/+analysis/DendriteDiameter.m``, ``+sbfsem/+analysis/DendriticFieldHull.m``,
``util/analysis/getNearestAnnotation.m``, ``util/network/getPathDistance.m``,
``util/analysis/surfaceArea.m``, ``+sbfsem/+analysis/AnnotationSizes.m`` and
``util/analysis/getOpenEnded.m``.

Deviations (see DEVIATIONS.md):

* ``tortuosity``'s 2-D branch called ``euclideanDist3`` and its 3-D branch called
  ``euclideanDist2``, so it reported a 3-D path over a 2-D chord (inflated by
  exactly the Z jitter the 2-D mode exists to suppress) or a 2-D path over a 3-D
  chord, which can fall below 1.0 — a physically impossible tortuosity (F122).
* ``getPathDistance`` applied ``bsxfun`` to ``fastEuclid3d``, which is not an
  element-wise function (F226).
* ``surfaceArea`` and ``AnnotationSizes`` summed over ``neuron.nodes``, so they
  counted synapse annotations as dendrite annotations and changed their answer
  depending on whether synapses had been imported.  Both use cell nodes now.
  ``surfaceArea`` also printed microns squared under an "mm2" label, a factor of
  10^6.
* ``DendriticFieldHull`` crashed on collinear or fewer-than-three point sets;
  those now return an empty hull with zero area.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .stats import Stats, describe, step_lengths_2d, step_lengths_3d

__all__ = [
    "Morphometrics",
    "morphometrics",
    "tortuosity",
    "branch_diameter",
    "dendrite_diameter_by_soma_distance",
    "dendritic_field_hull",
    "HullResult",
    "nearest_annotation",
    "path_distance",
    "surface_area",
    "annotation_sizes",
    "open_ended_branches",
]


def tortuosity(neuron, location_a: int, location_b: int, dim: int = 2) -> float:
    """Path length divided by straight-line distance between two annotations.

    ``dim=2`` measures both in the XY plane, which is the point of the 2-D mode:
    it suppresses Z jitter from section-to-section registration.
    """
    if dim not in (2, 3):
        raise ValueError("dim must be 2 or 3")
    nodes = neuron.get_branch_nodes(location_a, location_b)
    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    if len(xyz) < 2:
        return float("nan")
    if dim == 2:
        path = float(step_lengths_2d(xyz).sum())
        chord = float(np.linalg.norm(xyz[0, :2] - xyz[-1, :2]))
    else:
        path = float(step_lengths_3d(xyz).sum())
        chord = float(np.linalg.norm(xyz[0] - xyz[-1]))
    if chord == 0:
        return float("nan")
    return path / chord


def path_distance(neuron, location_a: int, location_b: int, dim: int = 3) -> tuple[float, np.ndarray]:
    """Summed step length along the path between two annotations."""
    nodes = neuron.get_branch_nodes(location_a, location_b)
    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    steps = step_lengths_2d(xyz) if dim == 2 else step_lengths_3d(xyz)
    return float(steps.sum()), steps


def branch_diameter(
    neuron, location_a: int, location_b: int, *, exclude_soma: bool = False
) -> tuple[np.ndarray, Stats]:
    """Annotation diameters along one branch, in microns.

    ``singleDendriteDiameter`` printed and plotted *radius* under a "dendrite
    diameter" axis label except for one ``2*binCenters`` call (F124); this
    returns diameters throughout.
    """
    nodes = neuron.get_branch_nodes(location_a, location_b)
    radii = nodes["Rum"].to_numpy(dtype=float)
    if exclude_soma and radii.size:
        radii = radii[radii <= 0.8 * radii.max()]
    diameters = 2.0 * radii
    return diameters, describe(diameters)


#: The most bins :func:`dendrite_diameter_by_soma_distance` builds: a bin width of
#: 1e-9 um once asked for a table of 10**12 rows.
MAX_DIAMETER_BINS = 50_000


def dendrite_diameter_by_soma_distance(
    neuron, *, bin_width: float = 5.0, dim: int = 3, exclude_soma: bool = True
) -> pd.DataFrame:
    """Mean dendrite diameter binned by distance from the soma.

    Ports ``+sbfsem/+analysis/DendriteDiameter.m``.
    """
    nodes = neuron.get_cell_nodes()
    if nodes.empty:
        return pd.DataFrame(columns=["bin", "distance", "n", "mean", "sem", "std"])
    soma = np.asarray(neuron.get_soma_xyz(), dtype=float).ravel()
    xyz = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    if dim == 2:
        distance = np.linalg.norm(xyz[:, :2] - soma[:2], axis=1)
    else:
        distance = np.linalg.norm(xyz - soma, axis=1)
    diameter = 2.0 * nodes["Rum"].to_numpy(dtype=float)
    if exclude_soma:
        keep = diameter <= 0.8 * diameter.max()
        distance, diameter = distance[keep], diameter[keep]
    if distance.size == 0:
        return pd.DataFrame(columns=["bin", "distance", "n", "mean", "sem", "std"])

    if not bin_width > 0:
        raise ValueError(f"bin_width must be positive, not {bin_width!r}")
    n_bins = float(distance.max()) / float(bin_width)
    if n_bins > MAX_DIAMETER_BINS:
        raise ValueError(
            f"bin_width {bin_width:g} um over {float(distance.max()):.0f} um is "
            f"{n_bins:.0f} bins; at most {MAX_DIAMETER_BINS} are drawn")
    edges = np.arange(0.0, distance.max() + bin_width, bin_width)
    index = np.clip(np.digitize(distance, edges) - 1, 0, len(edges) - 2)
    rows = []
    for i in range(len(edges) - 1):
        values = diameter[index == i]
        stats = describe(values)
        rows.append(
            {
                "bin": i,
                "distance": (edges[i] + edges[i + 1]) / 2.0,
                "n": stats.n,
                "mean": stats.mean,
                "sem": stats.sem,
                "std": stats.std,
            }
        )
    return pd.DataFrame(rows)


@dataclass
class HullResult:
    """A dendritic field convex hull."""

    points: np.ndarray  # (N, 2) hull vertices, in order
    area: float
    perimeter: float
    equivalent_diameter: float


def dendritic_field_hull(neuron, *, exclude_soma: bool = False) -> HullResult:
    """Convex hull of a neuron's XY dendritic field, in microns.

    Ports ``+sbfsem/+analysis/DendriticFieldHull.m``, which crashed on degenerate
    point sets (F115).
    """
    from scipy.spatial import ConvexHull, QhullError

    nodes = neuron.get_cell_nodes()
    xy = nodes[["Xum", "Yum"]].to_numpy(dtype=float)
    if exclude_soma and len(nodes):
        radii = nodes["Rum"].to_numpy(dtype=float)
        xy = xy[radii <= 0.8 * radii.max()]
    empty = HullResult(np.empty((0, 2)), 0.0, 0.0, 0.0)
    if len(xy) < 3:
        return empty
    try:
        hull = ConvexHull(xy)
    except QhullError:
        return empty  # collinear or duplicate points
    points = xy[hull.vertices]
    area = float(hull.volume)  # 2-D "volume" is area
    return HullResult(points, area, float(hull.area), float(2 * np.sqrt(area / np.pi)))


def nearest_annotation(neuron, xyz) -> tuple[int, float]:
    """The cell-body annotation closest to a point.  Ports ``getNearestAnnotation``."""
    nodes = neuron.get_cell_nodes()
    if nodes.empty:
        raise ValueError(f"c{neuron.id} has no cell-body annotations")
    points = nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
    distances = np.linalg.norm(points - np.asarray(xyz, dtype=float).ravel(), axis=1)
    index = int(np.argmin(distances))
    return int(nodes.iloc[index]["ID"]), float(distances[index])


def surface_area(neuron) -> float:
    """Rudimentary surface area in microns squared.

    Each annotation contributes the lateral area of a disc of its radius over one
    section: ``2 * pi * r * dz``.  Ports ``util/analysis/surfaceArea.m``.
    """
    nodes = neuron.get_cell_nodes()
    dz = float(neuron.volume_scale[2]) * 1e-3
    return float(2.0 * np.pi * nodes["Rum"].sum() * dz)


def annotation_sizes(neuron) -> Stats:
    """Distribution of annotation radii, over cell-body annotations only."""
    return describe(neuron.get_cell_nodes()["Rum"].to_numpy(dtype=float))


def open_ended_branches(
    neuron,
    *,
    include_off_edge: bool = True,
    include_terminal: bool = False,
    soma_threshold: float | None = None,
) -> np.ndarray:
    """Degree-1 annotations that mark unfinished branches.

    Ports ``util/analysis/getOpenEnded.m``.  ``OffEdge`` and ``Terminal`` are
    Viking tags: Terminal means the branch really ends, OffEdge means it
    continues but has not been traced yet.
    """
    graph = neuron.graph()
    degree_one = {int(n) for n, d in graph.degree() if d < 2}
    frame = neuron.nodes[neuron.nodes["ID"].isin(degree_one)]
    if not include_off_edge:
        frame = frame[~frame["OffEdge"]]
    if not include_terminal:
        frame = frame[~frame["Terminal"]]
    if soma_threshold is not None and len(frame):
        soma = np.asarray(neuron.get_soma_xyz(), dtype=float).ravel()
        xyz = frame[["Xum", "Yum", "Zum"]].to_numpy(dtype=float)
        frame = frame[np.linalg.norm(xyz - soma, axis=1) <= soma_threshold]
    return frame["ID"].to_numpy(dtype=np.int64)


@dataclass
class Morphometrics:
    """Skeleton-length metrics of one cell, from its annotation graph.

    Every edge is the straight line between two linked annotation centres, so
    "length" here is the length of the traced centreline, the same skeleton the
    SWC export writes.  Nothing is smoothed or resampled.
    """

    #: Sum of every edge length in 3-D, microns.
    total_length_um: float
    #: The same sum in the section (XY) plane only.  Section-to-section
    #: registration jitter adds z steps that are not real cable, so the 2-D
    #: figure is the more conservative one for comparing cells across volumes.
    total_length_xy_um: float
    #: 3-D length excluding edges that run between two soma annotations
    #: (radius at or above ``soma_fraction`` of the largest).
    dendritic_length_um: float
    #: The same in XY.
    dendritic_length_xy_um: float
    #: Longest 3-D path from the soma annotation to any annotation, microns.
    longest_path_um: float
    #: Mean 3-D path from the soma to each tip that is reachable from it.
    mean_tip_path_um: float
    #: Cell-body annotations (child structures such as synapses excluded).
    n_annotations: int
    #: Annotations linked to more than two others.
    n_branch_points: int
    #: Annotations linked to exactly one other, the soma annotation excluded.
    n_tips: int
    #: Tips tagged Terminal in Viking (the branch really ends there).
    n_tips_terminal: int
    #: Tips tagged OffEdge in Viking (the branch leaves the volume or the
    #: traced region).
    n_tips_off_edge: int
    #: Connected pieces of the annotation graph; more than one means Viking
    #: holds unlinked fragments of this cell.
    n_components: int
    #: Annotations that counted as soma.
    n_soma_annotations: int
    soma_radius_um: float
    #: Extent of the cell-body annotations, microns.
    extent_um: tuple[float, float, float]
    #: Length lying inside a much wider annotation: a thin process is linked to
    #: a wide profile's *centre*, so its first link starts before it does.  The
    #: rim is the wide annotation's traced outline along the link (its radius
    #: for a circle).  See ``sbfsem.analysis.skeleton_check``.
    spoke_length_um: float = 0.0
    #: ``total_length_um`` minus the spokes.  Reported beside the uncorrected
    #: length, never instead of it: on RPC1 c192 the correction is 14 %.
    rim_corrected_length_um: float = 0.0
    #: The same correction with the stored radius as the rim -- what an SWC
    #: alone allows, kept so the two can be reconciled.
    spoke_length_by_radius_um: float = 0.0
    #: Links of the annotation graph a tree cannot hold (loops in Viking).
    n_loops: int = 0
    #: The shortest path that still passes through every annotation, each
    #: node held inside its traced outline (or its circle): a floor under the
    #: cable length, ``total_length_um`` being in practice its ceiling.  Only
    #: computed when asked for (seconds on a large cell).  See
    #: ``sbfsem.analysis.relax``.
    length_floor_um: float | None = None
    #: The same with every node held inside its stored radius -- what an SWC
    #: alone allows.  Lower, and not a guarantee: the circle of a closed
    #: curve's equivalent-area radius pokes out of an elongated outline.
    length_floor_by_radius_um: float | None = None

    def as_dict(self) -> dict:
        out = dict(self.__dict__)
        out["extent_um"] = [float(v) for v in self.extent_um]
        return out


def morphometrics(neuron, *, soma_fraction: float = 0.8, bracket: bool = False) -> Morphometrics:
    """Cable length, branching and extent of a cell's traced skeleton.

    Added 2026-09-17 for per-cell dendritic length comparisons across
    volumes; there is no MATLAB counterpart.  The soma rule is the one the
    SWC export and the dendrite-diameter profile use: an annotation is soma
    when its radius is at least ``soma_fraction`` of the cell's largest.
    """
    import networkx as nx

    nodes = neuron.get_cell_nodes()
    n = int(len(nodes))
    empty = Morphometrics(0.0, 0.0, 0.0, 0.0, 0.0, float("nan"), n, 0, 0, 0, 0, 0, 0,
                          float("nan"), (0.0, 0.0, 0.0))
    if n == 0:
        return empty

    xyz_of = {
        int(i): (float(x), float(y), float(z))
        for i, x, y, z in nodes[["ID", "Xum", "Yum", "Zum"]].itertuples(index=False)
    }
    radius_of = dict(zip(nodes["ID"].astype(int), nodes["Rum"].astype(float), strict=True))
    r_max = float(nodes["Rum"].max())
    soma_cut = r_max * soma_fraction
    is_soma = {i: r >= soma_cut for i, r in radius_of.items()}

    graph = neuron.graph()
    total = total_xy = dend = dend_xy = 0.0
    for a, b in graph.edges():
        if a not in xyz_of or b not in xyz_of:
            continue
        pa, pb = xyz_of[a], xyz_of[b]
        dxy = float(np.hypot(pa[0] - pb[0], pa[1] - pb[1]))
        d3 = float(np.hypot(dxy, pa[2] - pb[2]))
        total += d3
        total_xy += dxy
        if not (is_soma[a] and is_soma[b]):
            dend += d3
            dend_xy += dxy
        graph.edges[a, b]["weight"] = d3

    degree = dict(graph.degree())
    try:
        soma_id = int(neuron.get_soma_id())
    except ValueError:
        soma_id = int(nodes["ID"].iloc[0])
    tips = [i for i, d in degree.items() if d == 1 and i != soma_id]
    tagged = nodes.set_index("ID")
    terminal = tagged["Terminal"].astype(bool) if "Terminal" in tagged else None
    off_edge = tagged["OffEdge"].astype(bool) if "OffEdge" in tagged else None
    n_terminal = int(sum(bool(terminal.get(i, False)) for i in tips)) if terminal is not None else 0
    n_off_edge = int(sum(bool(off_edge.get(i, False)) for i in tips)) if off_edge is not None else 0

    longest = 0.0
    tip_paths: list[float] = []
    if graph.has_node(soma_id):
        lengths = nx.single_source_dijkstra_path_length(graph, soma_id, weight="weight")
        if lengths:
            longest = float(max(lengths.values()))
        tip_paths = [float(lengths[t]) for t in tips if t in lengths]

    from .skeleton_check import spoke_lengths

    spokes = spoke_lengths(neuron)
    spoke_total = float(sum(spokes.values()))
    by_radius = float(sum(
        min(graph[wide][thin].get("weight", 0.0), radius_of[wide]) for wide, thin in spokes))
    n_pieces = int(nx.number_connected_components(graph)) if graph.number_of_nodes() else 0

    span = np.ptp(nodes[["Xum", "Yum", "Zum"]].to_numpy(dtype=float), axis=0)
    return Morphometrics(
        total_length_um=total,
        total_length_xy_um=total_xy,
        dendritic_length_um=dend,
        dendritic_length_xy_um=dend_xy,
        longest_path_um=longest,
        mean_tip_path_um=float(np.mean(tip_paths)) if tip_paths else float("nan"),
        n_annotations=n,
        n_branch_points=int(sum(1 for d in degree.values() if d > 2)),
        n_tips=len(tips),
        n_tips_terminal=n_terminal,
        n_tips_off_edge=n_off_edge,
        n_components=int(nx.number_connected_components(graph)) if graph.number_of_nodes() else 0,
        n_soma_annotations=int(sum(is_soma.values())),
        soma_radius_um=r_max,
        extent_um=(float(span[0]), float(span[1]), float(span[2])),
        spoke_length_um=spoke_total,
        rim_corrected_length_um=total - spoke_total,
        spoke_length_by_radius_um=by_radius,
        n_loops=int(graph.number_of_edges() - graph.number_of_nodes() + n_pieces),
        **_bracket(neuron, bracket),
    )


def _bracket(neuron, wanted: bool) -> dict:
    if not wanted:
        return {}
    from .relax import length_bracket

    found = length_bracket(neuron)
    return {"length_floor_um": found["floor"],
            "length_floor_by_radius_um": found["floor_by_radius"]}
