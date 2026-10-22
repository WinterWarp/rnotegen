"""Generic node/edge/cluster renderer on top of the layered layout.

Used by flowcharts, state diagrams, class diagrams and ER diagrams.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable

from ..diagram import (
    TextSpec, clip_to_outline, connector_elements, label_at, label_box_elements, measure_label, path_midpoint,
)
from ..layout import LCluster, LEdge, compound_layout
from ..model import BBox, CubBez, Arrow, Element, Point, Rect, ShapeEl, Style, union_all
from .common import Options


@dataclass
class GNode:
    id: str
    w: float
    h: float
    draw: Callable[[float, float, float, float], list[Element]]
    outline: Callable[[float, float, float, float], list[Point]]


@dataclass
class GEdge:
    u: str
    v: str
    style: Style
    label: str | None = None
    label_spec: TextSpec | None = None
    start: str = "none"
    end: str = "arrow"
    minlen: int = 1
    invisible: bool = False
    start_label: str | None = None  # e.g. multiplicity near u
    end_label: str | None = None


@dataclass
class GCluster:
    id: str
    title: str
    nodes: list[str] = field(default_factory=list)
    clusters: list["GCluster"] = field(default_factory=list)
    direction: str | None = None
    style: Style | None = None
    title_spec: TextSpec | None = None


def render_graph(nodes: dict[str, GNode], edges: list[GEdge], top_nodes: list[str], top_clusters: list[GCluster],
                 direction: str, opts: Options, nodesep: float = 45.0, ranksep: float = 55.0) -> tuple[list[Element], BBox, dict[str, tuple[float, float, float, float]]]:
    """Lay out and draw a graph. Returns (elements, bbox, node boxes as x, y, w, h)."""
    label_max = opts.wrap
    ledges: list[LEdge] = []
    for i, e in enumerate(edges):
        lw = lh = 0.0
        if e.label:
            spec = e.label_spec or opts.text(opts.theme.edge_text, opts.font_size * 0.9)
            w, h, n = measure_label(e.label, spec, label_max)
            lw, lh = w + 10, n * spec.size * 1.25 + 6
        ledges.append(LEdge(e.u, e.v, e.minlen, lw, lh, i))

    def to_lcluster(c: GCluster) -> LCluster:
        tw = th = 0.0
        if c.title:
            spec = c.title_spec or opts.text(opts.theme.cluster_text, opts.font_size * 0.95, bold=True)
            tw, _, n = measure_label(c.title, spec, None)
            th = n * spec.size * 1.3 + 4
        return LCluster(c.id, list(c.nodes), [to_lcluster(x) for x in c.clusters], c.direction, tw, th)

    sizes = {k: (n.w, n.h) for k, n in nodes.items()}
    res = compound_layout(sizes, ledges, top_nodes, [to_lcluster(c) for c in top_clusters], direction, nodesep, ranksep)

    els: list[Element] = []
    outlines: dict[str, list[Point]] = {}
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for nid, n in nodes.items():
        if nid not in res.pos:
            continue
        cx, cy = res.pos[nid]
        x, y = cx - n.w / 2, cy - n.h / 2
        boxes[nid] = (x, y, n.w, n.h)
        outlines[nid] = n.outline(x, y, n.w, n.h)

    # clusters (outermost first so inner ones paint on top)
    def draw_cluster(c: GCluster):
        if c.id not in res.clusters:
            return
        x, y, w, h = res.clusters[c.id]
        st = c.style or Style(stroke=opts.theme.cluster_stroke, fill=opts.theme.cluster_fill, width=1.2,
                              rough=opts.rough or opts.theme.rough)
        els.append(ShapeEl(Rect(x, y, w, h), st))
        outlines[c.id] = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        if c.title:
            spec = c.title_spec or opts.text(opts.theme.cluster_text, opts.font_size * 0.95, bold=True)
            els.append(label_at(c.title, x + w / 2, y + 8 + spec.size * 0.65, spec, None))
        for sub in c.clusters:
            draw_cluster(sub)

    for c in top_clusters:
        draw_cluster(c)

    edge_els: list[Element] = []
    label_els: list[Element] = []
    for i, e in enumerate(edges):
        if e.invisible:
            continue
        if e.u == e.v:
            if e.u in boxes:
                edge_els += _self_loop(boxes[e.u], e, opts)
            continue
        pts = res.routes.get(i)
        if not pts or e.u not in outlines or e.v not in outlines:
            continue
        pts = list(pts)
        pts[0] = clip_to_outline(outlines[e.u], pts[0], pts[1])
        pts[-1] = clip_to_outline(outlines[e.v], pts[-1], pts[-2])
        edge_els += connector_elements(pts, e.style, end=e.end, start=e.start, curved=opts.curved,
                                       bg=opts.background)
        if e.label:
            spec = e.label_spec or opts.text(opts.theme.edge_text, opts.font_size * 0.9)
            lp = res.labels.get(i) or path_midpoint(pts)[0]
            label_els += label_box_elements(e.label, lp[0], lp[1], spec, opts.background, label_max)
        for txt, end_pt, nxt in ((e.start_label, pts[0], pts[1]), (e.end_label, pts[-1], pts[-2])):
            if not txt:
                continue
            spec = opts.text(opts.theme.edge_text, opts.font_size * 0.8)
            dx, dy = nxt[0] - end_pt[0], nxt[1] - end_pt[1]
            n = math.hypot(dx, dy) or 1.0
            ux, uy = dx / n, dy / n
            px, py = -uy, ux
            w, _, _ = measure_label(txt, spec, None)
            off = 12 + w / 2
            lp = (end_pt[0] + ux * 18 + px * off, end_pt[1] + uy * 18 + py * (spec.size * 0.8 + 2))
            label_els.append(label_at(txt, lp[0], lp[1], spec, None))
    els += edge_els + label_els
    for nid, n in nodes.items():
        if nid in boxes:
            els += n.draw(*boxes[nid])
    bb = union_all([e.bbox() for e in els]) or BBox(0, 0, 1, 1)
    return els, bb, boxes


def _self_loop(box: tuple[float, float, float, float], e: GEdge, opts: Options) -> list[Element]:
    x, y, w, h = box
    a = (x + w, y + h * 0.3)
    b = (x + w, y + h * 0.7)
    r = max(28.0, h * 0.6)
    els: list[Element] = [ShapeEl(CubBez(a, (a[0] + r, a[1] - r * 0.4), (b[0] + r, b[1] + r * 0.4), b), e.style)]
    if e.end != "none":
        els.append(ShapeEl(Arrow((b[0] + 6, b[1] + 2.4), b), replace(e.style, dash="solid")))
    if e.label:
        spec = e.label_spec or opts.text(opts.theme.edge_text, opts.font_size * 0.9)
        tw, _, _ = measure_label(e.label, spec, opts.wrap)
        els += label_box_elements(e.label, x + w + r * 0.75 + tw / 2 + 6, y + h / 2, spec, opts.background, opts.wrap)
    return els
