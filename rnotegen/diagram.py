"""Shared diagram building blocks: themes, node shapes, labels and connectors.

Everything here produces plain model elements (native Rnote shapes and text),
so diagrams stay fully editable in Rnote.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .colors import Color, parse_color
from .markup import parse_inline
from .model import (
    Arrow, BBox, CubBez, Element, Ellipse, Line, Point, Polygon, Polyline, Rect, ShapeEl, Style, TextEl,
    bbox_of_points,
)
from .textmetrics import CENTER_WIDTH_FACTOR, LINE_HEIGHT, measure_text

CENTER_LINE_HEIGHT = 1.2  # used to vertically center text; real fonts are ~1.17-1.36


# --------------------------------------------------------------------------- themes


def _c(s: str) -> Color:
    c = parse_color(s)
    assert c is not None
    return c


@dataclass
class Theme:
    name: str
    node_fill: Color | None
    node_stroke: Color
    text: Color
    edge: Color
    edge_text: Color
    cluster_fill: Color | None
    cluster_stroke: Color
    cluster_text: Color
    note_fill: Color | None
    note_stroke: Color
    background: Color = field(default_factory=lambda: _c("white"))
    palette: list[Color] = field(default_factory=list)
    font: str = "sans-serif"
    font_size: float = 16.0
    stroke_width: float = 1.6
    rough: bool = False

    def node_style(self, **over) -> Style:
        s = Style(stroke=self.node_stroke, fill=self.node_fill, width=self.stroke_width, rough=self.rough)
        return replace(s, **over) if over else s

    def edge_style(self, **over) -> Style:
        s = Style(stroke=self.edge, fill=None, width=self.stroke_width, rough=self.rough)
        return replace(s, **over) if over else s


_PASTEL = ["#cfe3ff", "#ffe0b3", "#d7f5d0", "#ffd6e7", "#e6dcff", "#fff3b0", "#c9f2ef", "#ffd9cc", "#e2e8f0", "#f0e0ff"]
_PIE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"]

THEMES: dict[str, Theme] = {
    "default": Theme("default", _c("#ececff"), _c("#9370db"), _c("#222222"), _c("#333333"), _c("#333333"),
                     _c("#ffffde"), _c("#aaaa33"), _c("#333333"), _c("#fff5ad"), _c("#aaaa33"),
                     palette=[_c(x) for x in _PASTEL]),
    "neutral": Theme("neutral", _c("#f4f4f4"), _c("#666666"), _c("#222222"), _c("#555555"), _c("#333333"),
                     _c("#fafafa"), _c("#999999"), _c("#444444"), _c("#ffffe0"), _c("#999999"),
                     palette=[_c(x) for x in ["#eeeeee", "#dddddd", "#e8e8e8", "#d4d4d4", "#f2f2f2", "#cccccc"]]),
    "forest": Theme("forest", _c("#cde498"), _c("#13540c"), _c("#1b2e12"), _c("#2e5e24"), _c("#1b2e12"),
                    _c("#f2f8e6"), _c("#6eaa49"), _c("#1b2e12"), _c("#fff5ad"), _c("#6eaa49"),
                    palette=[_c(x) for x in ["#cde498", "#b6e2a1", "#e8f5c8", "#a8d5ba", "#d9f0a3", "#c7e9c0"]]),
    "blue": Theme("blue", _c("#e3efff"), _c("#2b6cb0"), _c("#1a2b44"), _c("#2b4c7e"), _c("#1a2b44"),
                  _c("#f5f9ff"), _c("#90b4e0"), _c("#2b4c7e"), _c("#fff5ad"), _c("#c9b458"),
                  palette=[_c(x) for x in ["#e3efff", "#cfe0ff", "#dbeafe", "#bfdbfe", "#e0f2fe", "#c7d2fe"]]),
    "ink": Theme("ink", None, _c("#1e1e1e"), _c("#1e1e1e"), _c("#1e1e1e"), _c("#1e1e1e"),
                 None, _c("#777777"), _c("#333333"), None, _c("#1e1e1e"),
                 palette=[_c(x) for x in _PASTEL], stroke_width=2.0),
    "dark": Theme("dark", _c("#2b3045"), _c("#aab4ff"), _c("#ececec"), _c("#d0d0d0"), _c("#e6e6e6"),
                  _c("#23262f"), _c("#6b7280"), _c("#d0d0d0"), _c("#4a4320"), _c("#c9b458"),
                  background=_c("#1e1e1e"),
                  palette=[_c(x) for x in ["#3b4a7a", "#5a4a2a", "#2f5a3a", "#5a2f4a", "#4a3b7a", "#5a5a2a"]]),
    "sketch": Theme("sketch", _c("#fff8e1"), _c("#1e1e1e"), _c("#1e1e1e"), _c("#1e1e1e"), _c("#1e1e1e"),
                    None, _c("#555555"), _c("#333333"), _c("#fff3b0"), _c("#1e1e1e"),
                    palette=[_c(x) for x in _PASTEL], stroke_width=2.0, rough=True),
}
PIE_PALETTE = [_c(x) for x in _PIE]


def get_theme(name: str | None) -> Theme:
    if not name:
        return THEMES["default"]
    key = name.strip().lower()
    if key in ("base", "light", "mermaid"):
        key = "default"
    if key in ("mono", "monochrome", "bw", "plain"):
        key = "ink"
    if key in ("rough", "hand", "handdrawn", "hand-drawn"):
        key = "sketch"
    if key not in THEMES:
        raise ValueError(f"unknown theme {name!r}; choose one of: {', '.join(THEMES)}")
    return THEMES[key]


# --------------------------------------------------------------------------- labels


@dataclass
class TextSpec:
    font: str = "sans-serif"
    size: float = 16.0
    color: Color = field(default_factory=lambda: _c("#222222"))
    bold: bool = False
    italic: bool = False
    markup: bool = True


def prepare_text(text: str, markup: bool = True):
    text = text.replace("\\n", "\n")
    if markup:
        return parse_inline(text, link_color=_c("#1f5fbf"))
    return text, []


def measure_label(text: str, spec: TextSpec, max_width: float | None) -> tuple[float, float, int]:
    plain, spans = prepare_text(text, spec.markup)
    from .markup import char_flags

    b, m = char_flags(plain, spans)
    return measure_text(plain, spec.font, spec.size, max_width, spec.bold, b, m)


def label_at(text: str, cx: float, cy: float, spec: TextSpec, max_width: float | None = None,
             align: str = "center") -> TextEl:
    """A text element whose rendered block is (approximately) centered on (cx, cy).

    `max_width` is the wrap width; the block is positioned from the estimated
    widest line because Rnote anchors centered text at its left edge.
    """
    plain, spans = prepare_text(text, spec.markup)
    w, _, lines = measure_label(text, spec, max_width)
    h = lines * spec.size * CENTER_LINE_HEIGHT
    wrap = (max_width if max_width else w) + 4  # slack so Pango never wraps earlier than estimated
    x = cx - w * CENTER_WIDTH_FACTOR / 2 if align == "center" else cx
    return TextEl(plain, x, cy - h / 2, font=spec.font, size=spec.size, color=spec.color,
                  weight=700 if spec.bold else 400, italic=spec.italic, max_width=wrap,
                  align=align, spans=spans)


def label_box_elements(text: str, cx: float, cy: float, spec: TextSpec, bg: Color | None,
                       max_width: float | None = None, pad: float = 3.0) -> list[Element]:
    """Text with an optional background rectangle (used for edge labels)."""
    out: list[Element] = []
    w, h, lines = measure_label(text, spec, max_width)
    if bg is not None:
        hh = lines * spec.size * CENTER_LINE_HEIGHT
        out.append(ShapeEl(Rect(cx - w / 2 - pad, cy - hh / 2 - pad, w + 2 * pad, hh + 2 * pad),
                           Style(stroke=None, fill=bg, width=0.0)))
    out.append(label_at(text, cx, cy, spec, max_width))
    return out


# --------------------------------------------------------------------------- node shapes

NODE_KINDS = {
    "rect", "round", "stadium", "subroutine", "cylinder", "circle", "doublecircle", "ellipse", "diamond",
    "hexagon", "parallelogram", "parallelogram_alt", "trapezoid", "trapezoid_alt", "asymmetric", "note",
    "start", "end", "bar", "choice", "text", "cloud", "bang", "document", "actor",
}

SHAPE_ALIASES = {
    "rectangle": "rect", "box": "rect", "square": "rect", "process": "rect", "rounded": "round", "roundrect": "round",
    "rounded_rect": "round", "event": "round", "pill": "stadium", "terminal": "stadium", "subprocess": "subroutine",
    "database": "cylinder", "db": "cylinder", "cyl": "cylinder", "storage": "cylinder", "oval": "ellipse",
    "rhombus": "diamond", "decision": "diamond", "question": "diamond", "hex": "hexagon", "prepare": "hexagon",
    "lean_right": "parallelogram", "lean-r": "parallelogram", "in_out": "parallelogram", "lean_left": "parallelogram_alt",
    "lean-l": "parallelogram_alt", "trap": "trapezoid", "trap-b": "trapezoid", "trap-t": "trapezoid_alt",
    "priority": "trapezoid", "manual": "trapezoid_alt", "flag": "asymmetric", "odd": "asymmetric",
    "dbl-circ": "doublecircle", "double_circle": "doublecircle", "circ": "circle", "sm-circ": "start",
    "f-circ": "start", "fr-circ": "end", "stop": "end", "fork": "bar", "join": "bar", "none": "text",
    "plain": "text", "label": "text", "doc": "document", "notch-rect": "note", "card": "note", "lin-rect": "subroutine",
    "fr-rect": "subroutine", "framed-rectangle": "subroutine", "stadium-shape": "stadium", "diam": "diamond",
    "cylinder-shape": "cylinder", "h-cyl": "cylinder", "das": "cylinder", "person": "actor", "stickman": "actor",
}


def normalize_kind(kind: str) -> str:
    k = kind.strip().lower().replace(" ", "_")
    k = SHAPE_ALIASES.get(k, k)
    if k not in NODE_KINDS:
        raise ValueError(f"unknown shape {kind!r}; use one of: {', '.join(sorted(NODE_KINDS))}")
    return k


def node_size_for_text(kind: str, tw: float, th: float, padx: float = 14.0, pady: float = 9.0,
                       min_w: float = 0.0, min_h: float = 0.0) -> tuple[float, float]:
    w, h = tw + 2 * padx, th + 2 * pady
    if kind in ("diamond", "choice"):
        w, h = tw * 1.45 + 2 * padx + th * 0.4, th * 1.45 + 2 * pady + tw * 0.12
        h = max(h, 50.0)
    elif kind in ("circle", "doublecircle", "cloud", "bang"):
        d = max(math.hypot(tw, th) + 2 * pady, 40.0)
        w = h = d + (8 if kind == "doublecircle" else 0)
    elif kind == "ellipse":
        w, h = tw * 1.35 + 2 * padx, th * 1.35 + 2 * pady
    elif kind == "hexagon":
        w = tw + 2 * padx + h * 0.6
    elif kind in ("parallelogram", "parallelogram_alt", "trapezoid", "trapezoid_alt"):
        w = tw + 2 * padx + h * 0.7
    elif kind == "asymmetric":
        w = tw + 2 * padx + h * 0.4
    elif kind == "stadium":
        w = tw + 2 * padx + h * 0.5
    elif kind == "subroutine":
        w = tw + 2 * padx + 16
    elif kind == "cylinder":
        h = th + 2 * pady + 18
    elif kind == "document":
        h = th + 2 * pady + 10
    elif kind == "start":
        w = h = 22.0
    elif kind == "end":
        w = h = 26.0
    elif kind == "bar":
        w, h = max(70.0, tw), 8.0
    elif kind == "actor":
        w, h = max(tw + 8, 40.0), 64.0 + th
    elif kind == "text":
        w, h = tw + 8, th + 6
    return max(w, min_w), max(h, min_h)


def _ellipse_pts(cx: float, cy: float, rx: float, ry: float, n: int = 48, a0: float = 0.0, a1: float = 2 * math.pi) -> list[Point]:
    return [(cx + rx * math.cos(a0 + (a1 - a0) * i / n), cy + ry * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def rounded_rect_pts(x: float, y: float, w: float, h: float, r: float, seg: int = 6) -> list[Point]:
    r = max(0.0, min(r, w / 2, h / 2))
    if r < 0.5:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pts: list[Point] = []
    for (cx, cy, a0) in ((x + w - r, y + r, -math.pi / 2), (x + w - r, y + h - r, 0.0),
                         (x + r, y + h - r, math.pi / 2), (x + r, y + r, math.pi)):
        for i in range(seg + 1):
            a = a0 + (math.pi / 2) * i / seg
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def node_outline(kind: str, x: float, y: float, w: float, h: float) -> list[Point]:
    """Closed outline polygon used for clipping connectors to the node border."""
    cx, cy = x + w / 2, y + h / 2
    if kind in ("circle", "doublecircle", "ellipse", "start", "end", "cloud", "bang"):
        return _ellipse_pts(cx, cy, w / 2, h / 2)[:-1]
    if kind in ("diamond", "choice"):
        return [(cx, y), (x + w, cy), (cx, y + h), (x, cy)]
    if kind == "hexagon":
        d = min(h * 0.3, w / 4)
        return [(x + d, y), (x + w - d, y), (x + w, cy), (x + w - d, y + h), (x + d, y + h), (x, cy)]
    if kind == "parallelogram":
        d = min(h * 0.35, w / 4)
        return [(x + d, y), (x + w, y), (x + w - d, y + h), (x, y + h)]
    if kind == "parallelogram_alt":
        d = min(h * 0.35, w / 4)
        return [(x, y), (x + w - d, y), (x + w, y + h), (x + d, y + h)]
    if kind == "trapezoid":
        d = min(h * 0.35, w / 4)
        return [(x + d, y), (x + w - d, y), (x + w, y + h), (x, y + h)]
    if kind == "trapezoid_alt":
        d = min(h * 0.35, w / 4)
        return [(x, y), (x + w, y), (x + w - d, y + h), (x + d, y + h)]
    if kind == "asymmetric":
        d = min(h * 0.4, w / 4)
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x + d, cy)]
    if kind == "round":
        return rounded_rect_pts(x, y, w, h, min(10.0, h / 4))
    if kind == "stadium":
        return rounded_rect_pts(x, y, w, h, h / 2, seg=10)
    if kind == "note":
        f = min(12.0, w / 4, h / 4)
        return [(x, y), (x + w - f, y), (x + w, y + f), (x + w, y + h), (x, y + h)]
    if kind == "document":
        wave = []
        for i in range(17):
            t = i / 16
            wave.append((x + w * (1 - t), y + h - 6 + 6 * math.sin(t * 2 * math.pi)))
        return [(x, y), (x + w, y)] + wave
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def node_elements(kind: str, x: float, y: float, w: float, h: float, style: Style) -> list[Element]:
    """The shape strokes of a node (without its label)."""
    cx, cy = x + w / 2, y + h / 2
    out: list[Element] = []
    if kind == "text":
        return out
    if kind == "rect":
        out.append(ShapeEl(Rect(x, y, w, h), style))
    elif kind in ("circle", "ellipse", "cloud", "bang"):
        out.append(ShapeEl(Ellipse(cx, cy, w / 2, h / 2), style))
    elif kind == "doublecircle":
        out.append(ShapeEl(Ellipse(cx, cy, w / 2, h / 2), style))
        out.append(ShapeEl(Ellipse(cx, cy, w / 2 - 5, h / 2 - 5), replace(style, fill=None)))
    elif kind == "start":
        c = style.stroke or _c("#222222")
        out.append(ShapeEl(Ellipse(cx, cy, w / 2, h / 2), replace(style, fill=c, stroke=c)))
    elif kind == "end":
        c = style.stroke or _c("#222222")
        out.append(ShapeEl(Ellipse(cx, cy, w / 2, h / 2), replace(style, fill=None, stroke=c)))
        out.append(ShapeEl(Ellipse(cx, cy, w / 2 - 5, h / 2 - 5), replace(style, fill=c, stroke=c)))
    elif kind == "bar":
        c = style.stroke or _c("#222222")
        out.append(ShapeEl(Rect(x, y, w, h), replace(style, fill=c, stroke=c)))
    elif kind == "subroutine":
        out.append(ShapeEl(Rect(x, y, w, h), style))
        inner = replace(style, fill=None)
        out.append(ShapeEl(Line((x + 8, y), (x + 8, y + h)), inner))
        out.append(ShapeEl(Line((x + w - 8, y), (x + w - 8, y + h)), inner))
    elif kind == "cylinder":
        ry = min(9.0, h / 6)
        top = y + ry
        bottom = y + h - ry
        # silhouette: left side down, bottom arc left->right, right side up, top arc right->left (back)
        bottom_arc = [(cx - (w / 2) * math.cos(math.pi * i / 24), bottom + ry * math.sin(math.pi * i / 24)) for i in range(25)]
        top_back = [(cx + (w / 2) * math.cos(math.pi * i / 24), top - ry * math.sin(math.pi * i / 24)) for i in range(25)]
        body = [(x, top)] + bottom_arc + top_back
        out.append(ShapeEl(Polygon(body), style))
        rim = [(cx - (w / 2) * math.cos(math.pi * i / 24), top + ry * math.sin(math.pi * i / 24)) for i in range(25)]
        out.append(ShapeEl(Polyline(rim), replace(style, fill=None)))
    elif kind == "note":
        pts = node_outline(kind, x, y, w, h)
        out.append(ShapeEl(Polygon(pts), style))
        f = min(12.0, w / 4, h / 4)
        out.append(ShapeEl(Polyline([(x + w - f, y), (x + w - f, y + f), (x + w, y + f)]), replace(style, fill=None)))
    elif kind == "actor":
        c = style.stroke or _c("#222222")
        st = replace(style, fill=None, stroke=c)
        head_r = 8.0
        hx, hy = cx, y + head_r + 1
        out.append(ShapeEl(Ellipse(hx, hy, head_r, head_r), replace(st, fill=style.fill)))
        neck = hy + head_r
        hip = neck + 22
        out.append(ShapeEl(Line((hx, neck), (hx, hip)), st))
        out.append(ShapeEl(Line((hx - 14, neck + 8), (hx + 14, neck + 8)), st))
        out.append(ShapeEl(Polyline([(hx - 12, hip + 18), (hx, hip), (hx + 12, hip + 18)]), st))
    else:
        out.append(ShapeEl(Polygon(node_outline(kind, x, y, w, h)), style))
    return out


def node_label_center(kind: str, x: float, y: float, w: float, h: float) -> Point:
    if kind == "cylinder":
        return (x + w / 2, y + h / 2 + min(9.0, h / 6) / 2)
    if kind == "asymmetric":
        return (x + w / 2 + min(h * 0.4, w / 4) / 2, y + h / 2)
    if kind == "document":
        return (x + w / 2, y + h / 2 - 3)
    if kind == "actor":
        return (x + w / 2, y + h - (h - 64) / 2)
    return (x + w / 2, y + h / 2)


def node_label_width(kind: str, w: float, h: float, padx: float = 14.0) -> float:
    if kind in ("diamond", "choice"):
        return max(20.0, w * 0.62 - padx)
    if kind in ("circle", "doublecircle", "cloud", "bang", "ellipse"):
        return max(20.0, w * 0.74 - padx)
    if kind in ("hexagon", "parallelogram", "parallelogram_alt", "trapezoid", "trapezoid_alt"):
        return max(20.0, w - 2 * padx - h * 0.6)
    if kind == "stadium":
        return max(20.0, w - 2 * padx - h * 0.4)
    if kind == "subroutine":
        return max(20.0, w - 2 * padx - 16)
    return max(20.0, w - 2 * padx)


# --------------------------------------------------------------------------- clipping


def clip_to_outline(outline: list[Point], inside: Point, outside: Point) -> Point:
    """Point where the segment inside->outside leaves the outline polygon."""
    best_t = None
    ax, ay = inside
    bx, by = outside
    n = len(outline)
    for i in range(n):
        p, q = outline[i], outline[(i + 1) % n]
        t = _seg_intersect(ax, ay, bx, by, p[0], p[1], q[0], q[1])
        if t is not None and (best_t is None or t > best_t):
            best_t = t
    if best_t is None:
        return outside
    return (ax + (bx - ax) * best_t, ay + (by - ay) * best_t)


def _seg_intersect(ax, ay, bx, by, cx, cy, dx, dy):
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    t = ((cx - ax) * sy - (cy - ay) * sx) / den
    u = ((cx - ax) * ry - (cy - ay) * rx) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return t
    return None


def point_in_polygon(pt: Point, poly: list[Point]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


# --------------------------------------------------------------------------- connectors

HEADS = {"none", "arrow", "triangle", "triangle_open", "diamond", "diamond_open", "circle", "circle_open",
         "cross", "bar", "crow_one", "crow_zero_one", "crow_many", "crow_one_many", "crow_zero_many"}
HEAD_ALIASES = {"": "none", "no": "none", "false": "none", "open": "arrow", "normal": "arrow", "v": "arrow",
                "filled": "triangle", "solid": "triangle", "block": "triangle", "hollow": "triangle_open",
                "inherit": "triangle_open", "inheritance": "triangle_open", "empty": "triangle_open",
                "composition": "diamond", "aggregation": "diamond_open", "dot": "circle", "o": "circle_open",
                "x": "cross", "tee": "bar", "|": "bar"}


def normalize_head(h: str | None) -> str:
    if h is None:
        return "none"
    k = h.strip().lower().replace("-", "_")
    k = HEAD_ALIASES.get(k, k)
    if k not in HEADS:
        raise ValueError(f"unknown arrow head {h!r}; use one of: {', '.join(sorted(HEADS))}")
    return k


def _unit(a: Point, b: Point) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (1.0, 0.0) if n < 1e-9 else (dx / n, dy / n)


def head_size(width: float) -> float:
    return 9.0 + 2.2 * width


def head_elements(head: str, tip: Point, direction: tuple[float, float], style: Style, bg: Color | None) -> tuple[list[Element], float]:
    """Head shapes at `tip`, pointing along `direction`. Returns (elements, setback):
    the line should end `setback` before the tip."""
    ux, uy = direction
    px, py = -uy, ux
    L = head_size(style.width)
    col = style.stroke or _c("#222222")
    solid = replace(style, fill=col, stroke=col, dash="solid")
    hollow = replace(style, fill=bg, stroke=col, dash="solid")
    plain = replace(style, fill=None, stroke=col, dash="solid")
    tx, ty = tip
    if head == "triangle" or head == "triangle_open":
        base = (tx - ux * L, ty - uy * L)
        pts = [tip, (base[0] + px * L * 0.45, base[1] + py * L * 0.45), (base[0] - px * L * 0.45, base[1] - py * L * 0.45)]
        return [ShapeEl(Polygon(pts), solid if head == "triangle" else hollow)], L
    if head in ("diamond", "diamond_open"):
        L2 = L * 1.5
        mid = (tx - ux * L2 / 2, ty - uy * L2 / 2)
        pts = [tip, (mid[0] + px * L * 0.4, mid[1] + py * L * 0.4), (tx - ux * L2, ty - uy * L2),
               (mid[0] - px * L * 0.4, mid[1] - py * L * 0.4)]
        return [ShapeEl(Polygon(pts), solid if head == "diamond" else hollow)], L2
    if head in ("circle", "circle_open"):
        r = L * 0.35
        c = (tx - ux * r, ty - uy * r)
        return [ShapeEl(Ellipse(c[0], c[1], r, r), solid if head == "circle" else hollow)], 2 * r
    if head == "cross":
        r = L * 0.4
        c = (tx - ux * r, ty - uy * r)
        a = [(c[0] + (ux + px) * r * 0.7, c[1] + (uy + py) * r * 0.7), (c[0] - (ux + px) * r * 0.7, c[1] - (uy + py) * r * 0.7)]
        b = [(c[0] + (ux - px) * r * 0.7, c[1] + (uy - py) * r * 0.7), (c[0] - (ux - px) * r * 0.7, c[1] - (uy - py) * r * 0.7)]
        return [ShapeEl(Line(a[0], a[1]), plain), ShapeEl(Line(b[0], b[1]), plain)], 0.0
    if head == "bar":
        r = L * 0.5
        return [ShapeEl(Line((tx + px * r, ty + py * r), (tx - px * r, ty - py * r)), plain)], 0.0
    if head.startswith("crow_"):
        return _crow(head, tip, direction, plain, bg), 0.0
    return [], 0.0


def _crow(head: str, tip: Point, d: tuple[float, float], st: Style, bg: Color | None) -> list[Element]:
    """ER-diagram cardinality markers (crow's foot notation)."""
    ux, uy = d
    px, py = -uy, ux
    tx, ty = tip
    w = 8.0
    out: list[Element] = []

    def at(dist: float) -> Point:
        return (tx - ux * dist, ty - uy * dist)

    def bar(dist: float) -> None:
        c = at(dist)
        out.append(ShapeEl(Line((c[0] + px * w, c[1] + py * w), (c[0] - px * w, c[1] - py * w)), st))

    many = head in ("crow_many", "crow_one_many", "crow_zero_many")
    if many:
        root = at(14)
        out.append(ShapeEl(Polyline([(tx + px * w, ty + py * w), root, (tx - px * w, ty - py * w)]), st))
    if head == "crow_one":
        bar(8)
        bar(14)
    elif head == "crow_zero_one":
        bar(8)
    elif head == "crow_one_many":
        bar(20)
    if head in ("crow_zero_one", "crow_zero_many"):
        c = at(26 if many else 20)
        out.append(ShapeEl(Ellipse(c[0], c[1], 5, 5), replace(st, fill=bg)))
    return out


def catmull_rom_to_bezier(pts: list[Point]) -> list[tuple[Point, Point, Point, Point]]:
    out = []
    n = len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        out.append((p1, c1, c2, p2))
    return out


def connector_elements(points: list[Point], style: Style, end: str = "arrow", start: str = "none",
                       curved: bool = False, bg: Color | None = None) -> list[Element]:
    """Draw a path with optional heads at either end, using native strokes.

    A plain 'arrow' head uses Rnote's own arrow shape, so straight arrows stay a
    single editable stroke.
    """
    pts = [p for i, p in enumerate(points) if i == 0 or math.hypot(p[0] - points[i - 1][0], p[1] - points[i - 1][1]) > 0.01]
    if len(pts) < 2:
        return []
    end, start = normalize_head(end), normalize_head(start)
    out: list[Element] = []
    heads: list[Element] = []

    # non-native heads: shorten the path and add head shapes
    if end not in ("none", "arrow"):
        d = _unit(pts[-2], pts[-1])
        els, setback = head_elements(end, pts[-1], d, style, bg)
        heads += els
        if setback:
            pts[-1] = (pts[-1][0] - d[0] * setback, pts[-1][1] - d[1] * setback)
    if start not in ("none", "arrow"):
        d = _unit(pts[1], pts[0])
        els, setback = head_elements(start, pts[0], d, style, bg)
        heads += els
        if setback:
            pts[0] = (pts[0][0] - d[0] * setback, pts[0][1] - d[1] * setback)

    if len(pts) == 2 or not curved:
        if len(pts) == 2:
            a, b = pts
            if end == "arrow" and start == "arrow":
                m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                out += [ShapeEl(Arrow(m, b), style), ShapeEl(Arrow(m, a), style)]
            elif end == "arrow":
                out.append(ShapeEl(Arrow(a, b), style))
            elif start == "arrow":
                out.append(ShapeEl(Arrow(b, a), style))
            else:
                out.append(ShapeEl(Line(a, b), style))
        else:
            body = list(pts)
            if start == "arrow":
                out.append(ShapeEl(Arrow(body[1], body[0]), style))
                body = body[1:]
            tail_arrow = None
            if end == "arrow":
                tail_arrow = ShapeEl(Arrow(body[-2], body[-1]), style)
                body = body[:-1]
            if len(body) >= 2:
                out.append(ShapeEl(Line(body[0], body[1]) if len(body) == 2 else Polyline(body), style))
            if tail_arrow:
                out.append(tail_arrow)
    else:
        for (p1, c1, c2, p2) in catmull_rom_to_bezier(pts):
            out.append(ShapeEl(CubBez(p1, c1, c2, p2), style))
        stub = 6.0
        if end == "arrow":
            d = _unit(pts[-2], pts[-1])
            out.append(ShapeEl(Arrow((pts[-1][0] - d[0] * stub, pts[-1][1] - d[1] * stub), pts[-1]), replace(style, dash="solid")))
        if start == "arrow":
            d = _unit(pts[1], pts[0])
            out.append(ShapeEl(Arrow((pts[0][0] - d[0] * stub, pts[0][1] - d[1] * stub), pts[0]), replace(style, dash="solid")))
    return out + heads


def path_midpoint(pts: list[Point]) -> tuple[Point, tuple[float, float]]:
    """Point halfway along a polyline, and the direction there."""
    total = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))
    half = total / 2
    acc = 0.0
    for i in range(len(pts) - 1):
        seg = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if acc + seg >= half and seg > 0:
            t = (half - acc) / seg
            return ((pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t),
                    _unit(pts[i], pts[i + 1]))
        acc += seg
    return pts[len(pts) // 2], (1.0, 0.0)


def elements_bbox(els: list[Element]) -> BBox | None:
    out = None
    for e in els:
        b = e.bbox()
        out = b if out is None else out.union(b)
    return out


def text_block_size(text: str, spec: TextSpec, max_width: float | None) -> tuple[float, float]:
    w, h, lines = measure_label(text, spec, max_width)
    return w, lines * spec.size * LINE_HEIGHT


__all__ = [
    "Theme", "THEMES", "get_theme", "TextSpec", "label_at", "label_box_elements", "measure_label", "node_elements",
    "node_outline", "node_size_for_text", "normalize_kind", "connector_elements", "clip_to_outline", "path_midpoint",
    "elements_bbox", "normalize_head", "bbox_of_points", "PIE_PALETTE",
]
