"""The addendum of Render compare's report: what every number is.

Nine pages, each a definition with the formula the code computes
(:mod:`sbfsem.geometry.meshdiff`, :mod:`sbfsem.analysis.compare_report`) and a
figure of what it measures.  The figures are drawn from small made-up shapes,
except A6's worked example, which is computed when the report is made, on two
spheres, by the same functions that measured the cell.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from matplotlib.patches import FancyArrowPatch, Polygon

from .pdfpage import DIVERGING, ERROR, INK, MUTED, Column, Page, fmt

__all__ = ["draw_addendum"]

_A = "#2f6db5"      # mesh A in the figures
_B = "#c2552d"      # mesh B
_GRAY = "#8d95a1"
_INSIDE = (0.23, 0.45, 0.92)
_OUTSIDE = (0.86, 0.22, 0.22)


def _arrow(ax, p, q, color=INK, lw=0.7, style="-|>", ms=6):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=ms, color=color, lw=lw,
                                 shrinkA=0, shrinkB=0))


def _figure(page: Page, x: float, y: float, w: float, h: float, *, title: str = ""):
    ax = page.axes(x, y, w, h)
    ax.set_aspect("equal")
    ax.set_axis_off()
    if title:
        page.text(x, y - 0.04, title, size=7, weight="bold", va="bottom")
    return ax


def _blob(t: np.ndarray, r0: float, bumps: tuple[tuple[float, float, float], ...],
          center=(0.0, 0.0)) -> np.ndarray:
    r = r0 + sum(a * np.cos(k * t + phase) for a, k, phase in bumps)
    return np.column_stack([center[0] + r * np.cos(t), center[1] + r * np.sin(t)])


def _inside(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Even-odd test of points against a closed polygon (the figures' "solid")."""
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x0, y0 = poly[:, 0][None, :], poly[:, 1][None, :]
    x1, y1 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    crosses = ((y0 > y) != (y1 > y)) & (x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-300) + x0)
    return (crosses.sum(axis=1) % 2) == 1


def _nearest(poly: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Distance from each point to a closed polyline, and the foot point."""
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    t = np.clip(np.einsum("pk,sk->ps", pts, ab) - np.einsum("sk,sk->s", a, ab)[None, :], 0, None)
    t = np.clip(t / np.maximum(np.einsum("sk,sk->s", ab, ab), 1e-300)[None, :], 0, 1)
    foot = a[None, :, :] + t[..., None] * ab[None, :, :]
    d = np.linalg.norm(pts[:, None, :] - foot, axis=2)
    k = d.argmin(axis=1)
    return d[np.arange(len(pts)), k], foot[np.arange(len(pts)), k]


# -- the pages --------------------------------------------------------------------------
def _a1(page: Page, y: float, spec: Any) -> None:
    y = page.para(page.left, y, (
        "A rendering is one layer of a Render compare pane: a technique with every one of its "
        "settings, as it was built. The report measures the surface each layer holds, never the "
        "settings its panel shows now (a layer changed after it was built is marked). Its pages "
        "use the same measurements as the viewer, so a number here is the number the viewer "
        "shows for the same surface: the error map against the annotations (the Error toggle) "
        "and the signed difference (a Difference pane), here always measured both ways."))
    y = page.para(page.left, y + 0.08, (
        "Units. Distances are in microns (µm), areas in µm² and volumes in µm³. X and Y are "
        "Viking's pixel coordinates times the volume's scale; Z is the section number times the "
        "section thickness, and every Z the report names is also given as a section, so a place "
        "can be found in Viking. A section or a Location ID means something only in its volume, "
        "which every page names in its footer."))
    y = page.para(page.left, y + 0.08, (
        "What is measured. For every rendering: its own size and shape, and (when asked) its "
        "distance from the annotations in both directions (A7). For every chosen pair A, B: the "
        "signed distance from every vertex of A to B's surface, and from every vertex of B to "
        "A's (A2, A3), summarized over each surface's area (A4, A5), with the volume the two "
        "enclose between them (A6)."))
    y = page.para(page.left, y + 0.08, (
        "Where to find it. The summary comes first: the renderings, four matrices of pairwise "
        "error (each cell links to its pair), every pair ranked, and charts. Then one section per "
        "pane: the pane as it was on screen, each of its renderings (settings and shape, then "
        "against the annotations), and each pair it leads (pictures, numbers, where they differ). "
        "The bookmarks of the PDF follow the same order."))
    ax = _figure(page, page.left + 0.5, y + 0.3, page.usable - 1.0, 2.6,
                 title="What is compared with what")
    ax.set_xlim(0, 10)
    ax.set_ylim(-0.2, 4.0)
    boxes = {"R1": (4.0, 3.0, _A), "R2": (4.0, 1.6, _B), "R3": (4.0, 0.2, "#3d8f5a")}
    ax.add_patch(Polygon([[0.3, 1.6], [2.5, 1.6], [2.5, 2.4], [0.3, 2.4]], closed=True,
                         facecolor="white", edgecolor=_GRAY, lw=1.2))
    ax.text(1.4, 2.0, "Annotations\n(discs, outlines)", ha="center", va="center", fontsize=7,
            color=_GRAY)
    for name, (x, yy, color) in boxes.items():
        ax.add_patch(Polygon([[x, yy], [x + 2.0, yy], [x + 2.0, yy + 0.8], [x, yy + 0.8]],
                             closed=True, facecolor="white", edgecolor=color, lw=1.2))
        ax.text(x + 1.0, yy + 0.4, name, ha="center", va="center", fontsize=8, color=color)
        _arrow(ax, (2.55, 2.0), (x - 0.05, yy + 0.4), color=_GRAY, lw=0.6, style="<|-|>")
    # Every pair, both ways: an arc between the two boxes' right edges.
    for one, two, rad in (("R1", "R2", -0.7), ("R2", "R3", -0.7), ("R1", "R3", -0.55)):
        y1, y2 = boxes[one][1] + 0.4, boxes[two][1] + 0.4
        ax.add_patch(FancyArrowPatch((6.05, y1), (6.05, y2), arrowstyle="<|-|>", mutation_scale=6,
                                     color=INK, lw=0.6, connectionstyle=f"arc3,rad={rad}"))
    ax.text(7.7, 1.9, "every chosen pair,\nboth ways (A2–A6)", fontsize=7, va="center")
    ax.text(0.3, 1.25, "each rendering against\nthe annotations (A7)", fontsize=7, va="top",
            color=MUTED)


def _a2(page: Page, y: float, spec: Any) -> None:
    y = page.para(page.left, y, (
        "The distance from a point p to a surface S is the length of the shortest segment from p "
        "to any point of S:"))
    y = page.math(page.left + 0.4, y + 0.05, r"$d(p,S)\;=\;\min_{q\,\in\,S}\;\|p-q\|$", size=12)
    y = page.para(page.left, y + 0.02, (
        "Exact point-to-triangle distances over hundreds of thousands of triangles need a spatial "
        "index of triangles; instead S is sampled densely and the distance to the nearest sample "
        "is used. N points s_k are drawn on S uniformly by area (a triangle is chosen with "
        "probability proportional to its area, then a point uniformly inside it), and S's own "
        "vertices are added so a thin feature always counts:"))
    y = page.math(page.left + 0.4, y + 0.05,
                  r"$\hat d(p)\;=\;\min_{k}\;\|p-s_k\|\;\;\geq\;\;d(p,S),\qquad N=250{,}000$", size=11)
    y = page.para(page.left, y + 0.02, (
        "The estimate is never below the true distance, and exceeds it by at most about the "
        "spacing of the samples, the mean distance between neighbors of an area-uniform sample:"))
    y = page.math(page.left + 0.4, y + 0.05, r"$h\;\approx\;\frac{1}{2}\sqrt{A_S/N}$", size=12)
    y = page.para(page.left, y + 0.02, (
        "h is printed with every pair as its resolution: a difference well above h is real; one "
        "below it is the sampling. The nearest sample is found with a k-d tree. The samples are "
        "drawn from a fixed seed, so the same two surfaces always give the same numbers."))
    ax = _figure(page, page.left + 0.6, y + 0.3, page.usable - 1.2, 3.0,
                 title="The nearest sample stands in for the nearest point")
    t = np.linspace(-3.2, 3.2, 400)
    curve = np.column_stack([t, 0.35 * np.sin(1.3 * t)])
    ax.plot(curve[:, 0], curve[:, 1], color=_B, lw=1.4)
    rng = np.random.default_rng(3)
    s = np.sort(rng.uniform(-3.2, 3.2, 22))
    samples = np.column_stack([s, 0.35 * np.sin(1.3 * s)])
    ax.plot(samples[:, 0], samples[:, 1], "o", ms=2.6, color=_B)
    p = np.array([0.55, 1.6])
    d, foot = _nearest(curve, p[None, :])
    k = int(np.argmin(np.linalg.norm(samples - p, axis=1)))
    ax.plot(*p, "o", ms=4, color=_A)
    ax.text(p[0] + 0.1, p[1] + 0.05, "p", fontsize=8, color=_A)
    _arrow(ax, tuple(p), tuple(foot[0]), color=INK)
    ax.text((p[0] + foot[0, 0]) / 2 - 0.55, (p[1] + foot[0, 1]) / 2, "d(p, S)", fontsize=7)
    _arrow(ax, tuple(p), tuple(samples[k]), color=_GRAY, lw=0.7)
    ax.text((p[0] + samples[k, 0]) / 2 + 0.12, (p[1] + samples[k, 1]) / 2 + 0.05,
            r"$\hat d(p)$", fontsize=8, color=_GRAY)
    ax.text(2.2, -0.75, "S, with its samples $s_k$", fontsize=7, color=_B)
    ax.set_xlim(-3.3, 3.3)
    ax.set_ylim(-1.0, 2.0)


def _a3(page: Page, y: float, spec: Any) -> None:
    y = page.para(page.left, y, (
        "A difference is signed: positive where a point of A lies outside B, negative where it "
        "lies inside, so its sign says whether A is fatter or thinner than B there. Two rules "
        "decide which side a point is on; the report names the one it used on every pair page."))
    y = page.para(page.left, y + 0.06, (
        "Enclosure (exact, the default). The point is inside when B's solid encloses it: a ray "
        "from the point crosses B an odd number of times. A surface assembled from pieces (the "
        "Original pathway and the other piece-built ones) has open ends; each round one (4πA/L² "
        "of at least 0.25 for its fan, A the fan's area, L the length of the rim) is closed with "
        "a fan first. Where a fan would be a sheet that is not there (a long thin gap), the point "
        "is placed by the generalized winding number of B as it is, inside above one half:"),
        size=8.6)
    y = page.math(page.left + 0.4, y + 0.06,
                  r"$d_{A\to B}(p)=s(p)\,\hat d(p),\quad s(p)=-1$ inside $B$, $+1$ outside;"
                  r"$\quad w(p)=\frac{1}{4\pi}\sum_{f\in B}\Omega_f(p)$", size=10.5)
    y = page.para(page.left, y + 0.02, (
        "Ω_f(p) is the solid angle triangle f fills seen from p; w is 1 inside a closed surface "
        "and 0 outside. An open surface does not say what it encloses: where w is between 0.2 and "
        "0.8 the side is a convention (the count of the counted rays decides it, the same from "
        "every direction). How many points the winding number put on the other side is printed "
        "when it is not zero."), size=8.6)
    y = page.para(page.left, y + 0.06, (
        "Normal (faster). The sign of B's outward normal at the nearest sample, n · (p − q*). "
        "Right wherever that sample is on B's outer surface, and wrong where it is on a piece "
        "buried inside B: against the Original pathway, 21.8 % of a Voxel remesh's vertices of "
        "Inferior c21768 were signed the wrong way."), size=8.6)
    ax = _figure(page, page.left + 0.3, y + 0.3, 3.3, 2.7, title="A colored by its side of B")
    t = np.linspace(0, 2 * np.pi, 600, endpoint=False)
    A = _blob(t, 1.0, ((0.16, 3, 0.4), (0.08, 5, 1.0)))
    B = _blob(t, 1.0, ((0.12, 2, 1.7), (0.07, 4, 0.2)), center=(0.12, 0.0))
    ax.fill(B[:, 0], B[:, 1], color=_B, alpha=0.12, lw=0)
    ax.plot(*np.vstack([B, B[:1]]).T, color=_B, lw=1.0)
    inside = _inside(B, A)
    for k in range(len(A)):
        j = (k + 1) % len(A)
        ax.plot(A[[k, j], 0], A[[k, j], 1], color=_INSIDE if inside[k] else _OUTSIDE, lw=2.0,
                solid_capstyle="butt")
    for k in range(0, len(A), 75):
        _, foot = _nearest(B, A[k:k + 1])
        _arrow(ax, tuple(A[k]), tuple(foot[0]), color=INK, lw=0.5, ms=4)
    ax.text(0.15, 0.0, "B", fontsize=9, color=_B, ha="center")
    ax.set_xlim(-1.4, 1.5)
    ax.set_ylim(-1.35, 1.35)
    page.para(page.left + 0.3, y + 3.1, "Red: A outside B (d > 0). Blue: A inside B (d < 0). "
              "Arrows: to the nearest point of B.", size=6.5, color=MUTED, width=3.3)
    page.para(page.left + 4.0, y + 3.1, "p is inside B, but its nearest sample is on a piece "
              "buried in B (dashed) whose normal n points at p: the normal rule says outside; "
              "enclosure says inside.", size=6.5, color=MUTED, width=3.3)
    ax2 = _figure(page, page.left + 4.0, y + 0.3, 3.3, 2.7, title="A buried piece fools the normal")
    outer = _blob(t, 1.0, ((0.0, 1, 0.0),))
    piece = _blob(t, 0.35, ((0.0, 1, 0.0),), center=(0.25, 0.0))
    ax2.plot(*np.vstack([outer, outer[:1]]).T, color=_B, lw=1.0)
    ax2.plot(*np.vstack([piece, piece[:1]]).T, color=_B, lw=1.0, ls="--")
    p = np.array([0.72, 0.0])
    ax2.plot(*p, "o", ms=4, color=_A)
    ax2.text(p[0] - 0.02, p[1] + 0.1, "p", fontsize=8, color=_A, ha="right")
    q = np.array([0.6, 0.0])
    _arrow(ax2, tuple(p), tuple(q), color=INK, lw=0.6, ms=4)
    _arrow(ax2, tuple(q), (0.6 + 0.35, 0.0), color=_GRAY, lw=0.6, ms=4)
    ax2.text(0.62, -0.18, "n", fontsize=7, color=_GRAY)
    ax2.set_xlim(-1.3, 1.3)
    ax2.set_ylim(-1.25, 1.25)


def _a4(page: Page, y: float, spec: Any) -> None:
    y = page.para(page.left, y, (
        "A surface is a mesh of triangles, and a region can be meshed ten times as finely as "
        "another without being any larger. A mean over vertices counts such a region ten times; "
        "the report's means and percentiles count each vertex for the area it stands for, a "
        "third of the area of every triangle around it, so they are statistics of the surface, "
        "not of its tessellation:"))
    y = page.math(page.left + 0.4, y + 0.05,
                  r"$w_i=\frac{1}{3}\sum_{f\,\ni\,i}\mathrm{area}(f),\qquad \sum_i w_i=A,\qquad "
                  r"\bar{x}=\frac{\sum_i w_i\,x_i}{\sum_i w_i}$", size=11)
    y = page.para(page.left, y + 0.02, (
        "A weighted percentile puts each value at the middle of its own share of the cumulative "
        "weight and reads the percentile between neighbors: with the values sorted,"))
    y = page.math(page.left + 0.4, y + 0.05,
                  r"$c_k=\frac{\sum_{j\leq k}w_j-\frac{1}{2}w_k}{\sum_j w_j},\qquad Q(q)="
                  r"\mathrm{interp}(q;\;c_k,\;x_k)$", size=11)
    y = page.para(page.left, y + 0.02, (
        "With equal weights that is the plotting-position percentile. The distances to the "
        "annotations are summarized the same way (the error map's histogram, its profile and "
        "its worst places), except the headline 'surface → annotation' mean, which is a plain "
        "mean over 60,000 area-uniform random samples of the surface: the same quantity, "
        "estimated another way."))
    ax = page.chart(page.left + 0.4, y + 0.35, page.usable - 0.8, 2.4,
                    title="Why area: a finely meshed half outvotes the rest by vertex count",
                    xlabel="position along the surface (µm)", ylabel="|d| (µm)")
    fine = np.linspace(0, 5, 51)
    coarse = np.linspace(5, 10, 6)[1:]
    x = np.concatenate([fine, coarse])
    d = np.where(x < 5, 0.1, 0.6)
    ax.plot(x, d, "o", ms=2.5, color=_A)
    ax.step([0, 5, 10], [0.1, 0.6, 0.6], where="post", color=_GRAY, lw=0.6)
    vm = float(d.mean())
    am = 0.5 * 0.1 + 0.5 * 0.6
    ax.axhline(vm, color=_B, lw=0.8, ls="--")
    ax.axhline(am, color=INK, lw=0.9)
    ax.text(10.1, vm, f" vertex mean {fmt(vm)}", fontsize=6, va="center", color=_B)
    ax.text(10.1, am, f" area mean {fmt(am)}", fontsize=6, va="center")
    ax.set_xlim(0, 12.5)
    ax.set_ylim(0, 0.75)


def _a5(page: Page, y: float, spec: Any) -> None:
    y = page.para(page.left, y, (
        "For one direction of a pair, A → B, every vertex i of A has a signed distance d_i to B "
        "and a weight w_i. Over A's surface:"))
    rows = [
        r"$\mathrm{mean}\ |d|=\frac{\sum w_i|d_i|}{\sum w_i}\qquad \mathrm{RMS}=\sqrt{\frac{\sum w_i d_i^2}"
        r"{\sum w_i}}\qquad \mathrm{mean}\ d=\frac{\sum w_i d_i}{\sum w_i}$",
        r"$p_{95}=Q(0.95)\ \mathrm{of}\ |d|\qquad \mathrm{largest}=\max_i |d_i|\qquad"
        r"\mathrm{within}\ t=\frac{\sum_{|d_i|\leq t} w_i}{\sum w_i}$",
    ]
    for row in rows:
        y = page.math(page.left + 0.3, y + 0.04, row, size=10.5)
    y = page.para(page.left, y + 0.02, (
        "The mean |d| says how far apart the surfaces are on average; the RMS weighs the large "
        "distances more; the signed mean says which way: + means A is fatter than B on the "
        "whole. The two directions differ: a branch A has and B lacks scores in A → B (its "
        "points are far from B) and hardly in B → A. Both together:"))
    y = page.math(page.left + 0.3, y + 0.06,
                  r"$H(A,B)=\max\left(\max_{a\in A}d(a,B),\ \max_{b\in B}d(b,A)\right)\qquad "
                  r"C(A,B)=\frac{1}{2}\left(\overline{|d|}_{A\to B}+\overline{|d|}_{B\to A}\right)$",
                  size=10.5)
    y = page.para(page.left, y + 0.02, (
        "H, the Hausdorff distance, is the farthest any point of either surface is from the "
        "other: one stray triangle sets it. C, the symmetric mean distance (often called the "
        "Chamfer distance), is the typical gap and is what the summary ranks pairs by."))
    cw = (page.usable - 0.5) / 2
    rng = np.random.default_rng(7)
    sample = np.concatenate([rng.gamma(2.0, 0.08, 9000), rng.gamma(6.0, 0.25, 600)])
    ax = page.chart(page.left + 0.35, y + 0.35, cw - 0.35, 2.2, title="A distribution of |d|",
                    xlabel="|d| (µm)", ylabel="share of the area (%)")
    hist, edges = np.histogram(sample, bins=60, range=(0, 2.5))
    ax.stairs(100 * hist / len(sample), edges, color=_A, fill=True, alpha=0.3)
    ax.stairs(100 * hist / len(sample), edges, color=_A, lw=0.8)
    marks = (("mean", sample.mean(), "-"), ("median", np.median(sample), ":"),
             ("p95", np.percentile(sample, 95), "--"), ("RMS", np.sqrt((sample ** 2).mean()), "-."))
    top = 100 * hist.max() / len(sample)
    for k, (name, v, ls) in enumerate(marks):
        ax.axvline(v, color=INK, lw=0.7, ls=ls)
        ax.text(v, top * (0.95 - 0.12 * k), f" {name} {fmt(v, 2)}", fontsize=5.8)
    ax = page.chart(page.left + cw + 0.5 + 0.35, y + 0.35, cw - 0.35, 2.2,
                    title="The same as 'share within a distance'", xlabel="|d| (µm)",
                    ylabel="share of the area (%)")
    q = np.percentile(sample, np.arange(101))
    ax.plot(q, np.arange(101), color=_A, lw=0.9)
    p95 = np.percentile(sample, 95)
    ax.plot([p95, p95, 0], [0, 95, 95], color=INK, lw=0.6, ls="--")
    ax.text(p95, 50, f" p95 = {fmt(p95, 2)} µm:\n 95 % of the surface\n is closer", fontsize=5.8)
    ax.set_xlim(0, 2.5)
    ax.set_ylim(0, 100)


def _a6(page: Page, y: float, spec: Any) -> None:
    from ..analysis.compare_report import pair_result
    from ..geometry.mesh import Mesh
    from ..geometry.meshdiff import mesh_difference

    y = page.para(page.left, y, (
        "For two closed surfaces close together, the signed distances add up to the volume "
        "between them: each patch dA of A at distance d outside B encloses a slab of about d dA. "
        "Either surface gives an estimate, one a little high and one a little low (A's patches "
        "are larger than the slab's middle where A is outside), and their average is far closer:"))
    y = page.math(page.left + 0.3, y + 0.05,
                  r"$V_A-V_B\;\approx\;\int_A d_{A\to B}\,dA\;\approx\;-\int_B d_{B\to A}\,dA,\qquad"
                  r"V_A-V_B\;\approx\;\frac{1}{2}\left(\int_A d_{A\to B}\,dA-\int_B d_{B\to A}\,dA\right)$",
                  size=10.2)
    y = page.para(page.left, y + 0.02, (
        "The integrals are the sums of w_i d_i over each surface. The volumes themselves are by "
        "the divergence theorem, the sum over the triangles of v0 · (v1 × v2) / 6: exact for a "
        "closed surface wound outward. Each pair page prints both, so they check each other: a "
        "large gap between them points at a surface that is open, or assembled from pieces "
        "that overlap (the divergence volume counts an overlap once for every piece in it)."))
    y = page.math(page.left + 0.3, y + 0.05, r"$V=\left|\frac{1}{6}\sum_{f}v_0\cdot(v_1\times v_2)\right|$",
                  size=11)
    # A worked example, measured now by the functions that measured the cell.
    import trimesh

    big, small = (trimesh.creation.icosphere(subdivisions=5, radius=r) for r in (10.0, 9.5))
    mesh_a, mesh_b = Mesh(big.vertices, big.faces), Mesh(small.vertices, small.faces)
    diff = mesh_difference(mesh_a, mesh_b, sign="enclosure", both=True)
    res = pair_result(0, 1, mesh_a, mesh_b, diff)
    exact = 4.0 / 3.0 * np.pi * (10.0 ** 3 - 9.5 ** 3)
    est = 0.5 * (res.ab.integral_signed_um3 - res.ba.integral_signed_um3)
    y = page.section(y + 0.05, "Worked example, computed while this report was made", size=9)
    y = page.table(page.left, y, [
        Column("Two concentric spheres, radius 10 and 9.5 µm (20,480 triangles each)", 5.4),
        Column("Value", 2.0, "right")], [
        ["Exact volume between the spheres, 4π(10³ − 9.5³)/3", f"{fmt(exact, 5)} µm³"],
        ["Divergence-theorem volumes, V_A − V_B (the meshes are slightly inside the spheres)",
         f"{fmt(res.volume_a_um3 - res.volume_b_um3, 5)} µm³"],
        ["∫_A d dA (one side)", f"{fmt(res.ab.integral_signed_um3, 5)} µm³"],
        ["−∫_B d dA (the other)", f"{fmt(-res.ba.integral_signed_um3, 5)} µm³"],
        ["½(∫_A d dA − ∫_B d dA)", f"{fmt(est, 5)} µm³"],
        ["Mean d both ways (exact: +0.5 and −0.5)",
         f"{fmt(res.ab.mean_signed_um, 5, signed=True)} / {fmt(res.ba.mean_signed_um, 5, signed=True)} µm"],
        ["Hausdorff / symmetric mean distance", f"{fmt(res.hausdorff_um, 5)} / {fmt(res.chamfer_um, 5)} µm"],
        ["Resolution (sample spacing)", f"{fmt(res.sample_spacing_um, 3)} µm"],
    ], size=7) + 0.1
    page.para(page.left, y, (
        "For concentric spheres the average misses by 2π(R − r)³/3 only (0.26 µm³), while each "
        "one-sided integral misses by about 5 %. Any distance above 0.5 µm here is the sampling's "
        "overestimate (A2), within the resolution; the meshes' flat triangles lie a little inside "
        "the true spheres, which is why their volumes miss the exact one."), size=8)


def _a7(page: Page, y: float, spec: Any) -> None:
    y = page.para(page.left, y, (
        "The annotations are the closest thing to ground truth a rendering can be held to. A "
        "Viking annotation is a disc in its section plane, not a sphere: the set of points at the "
        "section's Z within its radius of its center. Its distance from a point p is exact:"),
        size=8.8)
    y = page.math(page.left + 0.3, y + 0.05,
                  r"$d(p,D)=\sqrt{\max(\rho-r,\,0)^2+\Delta z^2},\qquad \rho=\|(p_x,p_y)-c\|,"
                  r"\quad \Delta z=|p_z-z_D|$", size=10.5)
    y = page.para(page.left, y + 0.02, (
        "A traced outline (a closed curve) is the filled polygon in its plane: the in-plane "
        "distance to the polygon (0 inside it) takes the place of max(ρ − r, 0). The distance to "
        "the annotations is the least over all of them. It is measured both ways, because each "
        "sees a different failure:"), size=8.8)
    items = [
        "Surface → annotation ('surface'): from points of the rendered surface to the nearest "
        "annotation. A bulge past the last annotated section or a sweep through tissue nobody "
        "annotated scores here. The headline is the mean over 60,000 area-uniform samples; the "
        "pictures color every vertex by it.",
        "Annotation rim → surface ('rims'): from points along every annotation's rim (8 to 64 per "
        "disc, about every 0.25 µm or the sampling's spacing; every point of a traced outline) to "
        "the nearest surface sample. A dropped disc or a body too thin scores here.",
        "Hidden surface: a sample is hidden when the point 0.05 µm outside it along its normal "
        "is still inside the render: a tube buried in a soma. It scores near 0 and dilutes the "
        "mean, so the mean over the surface that can be seen is given beside it.",
        "Excess volume = surface mean × area: to first order, the volume the render claims "
        "beyond any annotation's support.  Bias: a rim point inside the render means the render "
        "is fatter there; one outside, that it stops short.",
        "Sampling floor = half the median gap between annotated sections: a perfect surface "
        "between two sections is up to that far from both, so no rendering scores below it.",
    ]
    for text in items:
        page.text(page.left + 0.1, y + 0.02, "•", size=9)
        y = page.para(page.left + 0.28, y + 0.02, text, size=8.2) + 0.02
    ax = _figure(page, page.left + 0.6, y + 0.3, page.usable - 1.2, 2.7,
                 title="Side view (X across, Z up): three annotated sections and a rendered surface")
    zs = np.array([0.0, 1.0, 2.0])
    rs = np.array([1.2, 1.7, 1.0])
    for z, r in zip(zs, rs, strict=True):
        ax.plot([-r, r], [z, z], color=_GRAY, lw=3.0, solid_capstyle="butt")
    zz = np.linspace(-0.45, 2.45, 300)
    # A surface that bulges between sections and falls short of the middle disc's rim.
    rr = np.interp(zz, zs, [1.25, 1.25, 1.05]) + 0.45 * np.sin(np.pi * np.clip(zz, 0, 2)) ** 2
    ax.plot(rr, zz, color=_A, lw=1.1)
    ax.plot(-rr, zz, color=_A, lw=1.1)

    def to_disc(px: float, pz: float) -> tuple[float, float]:
        """The nearest point of the nearest disc, by d = hypot(max(rho - r, 0), dz)."""
        d = [np.hypot(max(abs(px) - r, 0.0), pz - z) for z, r in zip(zs, rs, strict=True)]
        k = int(np.argmin(d))
        return float(np.sign(px) * min(abs(px), rs[k])), float(zs[k])

    k = int(np.argmin(np.abs(zz - 1.5)))
    q = to_disc(float(rr[k]), float(zz[k]))
    _arrow(ax, (float(rr[k]), float(zz[k])), q, color=INK, lw=0.7, ms=5)
    ax.text(float(rr[k]) + 0.1, 0.5 * (float(zz[k]) + q[1]) + 0.05, "surface → annotation",
            fontsize=6, va="center")
    m = int(np.argmin(np.abs(zz - 1.0)))
    _arrow(ax, (-1.7, 1.0), (-float(rr[m]), 1.0), color=_B, lw=0.8, ms=5)
    ax.text(-1.78, 1.0, "rim → surface", fontsize=6, color=_B, ha="right", va="center")
    ax.annotate("", xy=(-2.3, 0.0), xytext=(-2.3, 0.5), arrowprops={"arrowstyle": "<->", "lw": 0.6})
    ax.text(-2.4, 0.25, "floor: half the gap", fontsize=6, va="center", ha="right")
    ax.text(1.12, 2.06, "a disc in its section plane", fontsize=6, color=_GRAY, va="bottom")
    ax.set_xlim(-3.1, 3.7)
    ax.set_ylim(-0.6, 2.6)


def _a8(page: Page, y: float, spec: Any) -> None:
    views = ", ".join(v.label for v in getattr(spec, "views", ()))
    y = page.para(page.left, y, (
        "Pictures are orthographic: each pixel shows the nearest surface along the viewpoint's "
        "direction, lit from just above the camera. The named viewpoints are the viewer's (XY1 "
        "from +Z with X right and Y up; XZ1 from −Y with Z up; YZ1 from +X with Z up), and 'this "
        "view' is the panes' camera direction when the report was asked for (under Flip Z, "
        f"mirrored as on screen). This report's: {views or 'as on the cover'}. Every picture "
        "from one viewpoint is in the same frame, so it is at the same place and scale on every "
        "page; the bar in its corner is a round number of microns."), size=8.6)
    y = page.para(page.left, y + 0.06, (
        "Colors. A signed difference runs from blue (inside the other surface) through white "
        "(they agree) to red (outside), clipped at ± the pair's scale: the larger 98th "
        "percentile of |d| of the two directions, so a few stray points do not wash the rest "
        "out. A distance from the annotations runs from light gray (agrees) through amber to red "
        "at the scale, one scale for every error picture of the report (the largest any "
        "rendering suggests), so the same color is the same microns on every page. Both are the "
        "viewer's own maps."), size=8.6)
    y = page.para(page.left, y + 0.06, (
        "Outlines. The third picture of a pair is where the two silhouettes differ from that "
        "viewpoint: A's color where only A covers the picture, B's where only B does, gray "
        "where both do; the shares are of the area the two cover together. It is a projection: "
        "two surfaces can differ where their silhouettes agree."), size=8.6)
    y = page.para(page.left, y + 0.06, (
        "Cuts and close-ups. A cut is the section plane at an annotated section's Z (the one "
        "nearest the Z where the two disagree most, or through the worst place): each surface's "
        "crossing as lines, the annotations of that section in gray. A close-up draws only the "
        "triangles within a few microns of a place, so nothing in front hides it. Profiles along "
        "Z are in bins of 1 µm (wider for a long cell, at most 240 bins), each bin weighted by "
        "area; section numbers run along the top."), size=8.6)
    x = page.left + 0.3
    y += 0.3
    ax = page.axes(x, y, 3.0, 0.16)
    ax.imshow(np.linspace(-1, 1, 256)[None, :], cmap=DIVERGING, aspect="auto", extent=(-1, 1, 0, 1))
    ax.set_yticks([])
    ax.set_xticks([-1, 0, 1])
    ax.set_xticklabels(["−scale\ninside", "0\nagree", "+scale\noutside"], fontsize=6)
    ax2 = page.axes(x + 3.8, y, 3.0, 0.16)
    ax2.imshow(np.linspace(0, 1, 256)[None, :], cmap=ERROR, aspect="auto", extent=(0, 1, 0, 1))
    ax2.set_yticks([])
    ax2.set_xticks([0, 0.5, 1])
    ax2.set_xticklabels(["0\nagrees", "½ scale", "scale\nand beyond"], fontsize=6)
    page.text(x, y - 0.05, "Signed difference", size=7, weight="bold", va="bottom")
    page.text(x + 3.8, y - 0.05, "Distance from the annotations", size=7, weight="bold", va="bottom")


def _a9(page: Page, y: float, spec: Any) -> None:
    items = [
        ("Resolution.", "Every distance is to the nearest of a finite sample of the other surface "
         "(A2). Below the printed spacing, a difference is the sampling."),
        ("Open and assembled surfaces.", "The Original pathway and the other piece-built ones have "
         "open ends and overlapping pieces. Their divergence volume counts overlaps more than once "
         "(A6); their buried surface is 'hidden' (A7); which side of an open surface a point is on "
         "is decided by caps and the winding number, and is a convention inside a long crack (A3)."),
        ("What was built.", "A rendering is the surface its layer holds. A layer whose panel was "
         "changed after it was built is marked, and measured as built."),
        ("What the annotations say.", "The annotations are the ground truth only as far as they "
         "go: between sections a surface is an interpolation, and no rendering can score below "
         "the sampling floor. A cell's annotations are read in the registration of the copy each "
         "rendering was built from (the cover says which); another registration moves them, so two "
         "renderings of different registrations are each held to their own."),
        ("Places.", "A place is the center of the surface within 2 µm that carries the most of "
         "area × distance there, no two within 2 µm of each other; its Z is also given as a "
         "section of the volume. The nearest annotation's Location ID is that volume's."),
        ("Cuts.", "A cut is made at the annotated section nearest the Z it is for, so the "
         "annotations of that section lie in its plane."),
        ("Time.", "'Made' is the browser's clock and time zone when the report was asked for."),
    ]
    for head, text in items:
        page.text(page.left, y, head, size=8.6, weight="bold")
        y = page.para(page.left + 1.9, y, text, size=8.4, width=page.usable - 1.9) + 0.1


_DRAW = {0: _a1, 1: _a2, 2: _a3, 3: _a4, 4: _a5, 5: _a6, 6: _a7, 7: _a8, 8: _a9}


def draw_addendum(index: int, page: Page, title: str, spec: Any) -> None:
    """Draw addendum page ``index`` (0-based, as ``ADDENDUM_TITLES``) on ``page``."""
    y = page.header(title, "Addendum: what every number of this report is")
    _DRAW[index](page, y, spec)

