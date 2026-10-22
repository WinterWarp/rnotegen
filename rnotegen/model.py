"""In-memory model of an Rnote document: page setup plus an ordered list of
elements (strokes). Elements know their bounding box (for layout and
anchors) and how to serialize themselves into Rnote's stroke JSON.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace

from .colors import BLACK, WHITE, Color
from .markup import Span, byte_offsets, char_flags
from .textmetrics import LINE_HEIGHT, measure_text

Point = tuple[float, float]


# --------------------------------------------------------------------------- geometry


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def union(self, o: "BBox") -> "BBox":
        return BBox(min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1))

    def expand(self, d: float) -> "BBox":
        return BBox(self.x0 - d, self.y0 - d, self.x1 + d, self.y1 + d)

    def intersects(self, o: "BBox", tol: float = 0.0) -> bool:
        return self.x0 < o.x1 - tol and o.x0 < self.x1 - tol and self.y0 < o.y1 - tol and o.y0 < self.y1 - tol

    def anchor(self, name: str) -> Point:
        name = name.lower().replace("_", "").replace("-", "")
        table = {
            "topleft": (self.x0, self.y0), "tl": (self.x0, self.y0), "nw": (self.x0, self.y0),
            "top": (self.cx, self.y0), "t": (self.cx, self.y0), "n": (self.cx, self.y0), "topcenter": (self.cx, self.y0),
            "topright": (self.x1, self.y0), "tr": (self.x1, self.y0), "ne": (self.x1, self.y0),
            "left": (self.x0, self.cy), "l": (self.x0, self.cy), "w": (self.x0, self.cy), "centerleft": (self.x0, self.cy),
            "center": (self.cx, self.cy), "c": (self.cx, self.cy), "middle": (self.cx, self.cy), "centre": (self.cx, self.cy),
            "right": (self.x1, self.cy), "r": (self.x1, self.cy), "e": (self.x1, self.cy), "centerright": (self.x1, self.cy),
            "bottomleft": (self.x0, self.y1), "bl": (self.x0, self.y1), "sw": (self.x0, self.y1),
            "bottom": (self.cx, self.y1), "b": (self.cx, self.y1), "s": (self.cx, self.y1), "bottomcenter": (self.cx, self.y1),
            "bottomright": (self.x1, self.y1), "br": (self.x1, self.y1), "se": (self.x1, self.y1),
        }
        if name not in table:
            raise KeyError(name)
        return table[name]


ANCHOR_NAMES = ["topleft", "top", "topright", "left", "center", "right", "bottomleft", "bottom", "bottomright"]


def bbox_of_points(pts: list[Point]) -> BBox:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return BBox(min(xs), min(ys), max(xs), max(ys))


def union_all(boxes: list[BBox]) -> BBox | None:
    out = None
    for b in boxes:
        out = b if out is None else out.union(b)
    return out


def _pt(p: Point) -> list[float]:
    return [round(p[0], 3), round(p[1], 3)]


def affine_json(tx: float, ty: float, angle_deg: float = 0.0, sx: float = 1.0, sy: float = 1.0) -> dict:
    """nalgebra Affine2, serialized column-major as 9 values (Rnote <= 0.14)."""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    m = [c * sx, s * sx, 0.0, -s * sy, c * sy, 0.0, tx, ty, 1.0]
    return {"affine": [round(v, 3) + 0.0 for v in m]}


def _rot(p: Point, angle_deg: float, center: Point) -> Point:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    dx, dy = p[0] - center[0], p[1] - center[1]
    return (center[0] + dx * c - dy * s, center[1] + dx * s + dy * c)


# --------------------------------------------------------------------------- style

LINE_STYLES = {"solid", "dotted", "dashed_narrow", "dashed_equidistant", "dashed_wide"}
FILL_STYLES = {"solid", "hachure", "zig_zag", "zig_zag_line", "crosshatch", "dots", "dashed"}


@dataclass
class Style:
    stroke: Color | None = BLACK
    width: float = 2.0
    fill: Color | None = None
    dash: str = "solid"
    cap: str = "rounded"
    rough: bool = False
    fill_style: str = "solid"  # only used by rough style
    hachure_angle: float = -41.0  # degrees, rough only
    seed: int | None = None

    def to_json(self, rng: random.Random) -> dict:
        if self.rough:
            seed = self.seed if self.seed is not None else rng.getrandbits(63)
            return {
                "rough": {
                    "stroke_color": self.stroke.to_json() if self.stroke else None,
                    "stroke_width": round(self.width, 3),
                    "fill_color": self.fill.to_json() if self.fill else None,
                    "fill_style": self.fill_style,
                    "hachure_angle": round(math.radians(self.hachure_angle), 3),
                    "seed": seed,
                }
            }
        cap = "rounded" if self.dash == "dotted" else ("rounded" if self.cap == "rounded" else "straight")
        return {
            "smooth": {
                "stroke_width": round(self.width, 3),
                "stroke_color": self.stroke.to_json() if self.stroke else None,
                "fill_color": self.fill.to_json() if self.fill else None,
                "pressure_curve": "const",
                "line_style": self.dash,
                "line_cap": cap,
            }
        }

    def scaled(self, s: float) -> "Style":
        return replace(self, width=self.width * s)


# --------------------------------------------------------------------------- shapes


class Shape:
    kind = "shape"

    def bbox(self) -> BBox:
        raise NotImplementedError

    def transform(self, s: float, dx: float, dy: float) -> None:
        raise NotImplementedError

    def to_json(self) -> dict:
        raise NotImplementedError


def _tp(p: Point, s: float, dx: float, dy: float) -> Point:
    return (p[0] * s + dx, p[1] * s + dy)


@dataclass
class Line(Shape):
    start: Point
    end: Point
    kind = "line"

    def bbox(self):
        return bbox_of_points([self.start, self.end])

    def transform(self, s, dx, dy):
        self.start, self.end = _tp(self.start, s, dx, dy), _tp(self.end, s, dx, dy)

    def to_json(self):
        return {"line": {"start": _pt(self.start), "end": _pt(self.end)}}


ARROW_TIP_ANGLE = 13.0 / 16.0 * math.pi


def arrow_tip_lines(start: Point, tip: Point, stroke_width: float) -> tuple[Point, Point]:
    """The two short lines of Rnote's arrow head (mirrors rnote-compose)."""
    dx, dy = tip[0] - start[0], tip[1] - start[1]
    n = math.hypot(dx, dy)
    ux, uy = (1.0, 0.0) if n == 0 else (dx / n, dy / n)
    length = 10.0 * (1.0 + 0.18 * stroke_width)
    out = []
    for ang in (ARROW_TIP_ANGLE, -ARROW_TIP_ANGLE):
        c, s = math.cos(ang), math.sin(ang)
        out.append((tip[0] + (ux * c - uy * s) * length, tip[1] + (ux * s + uy * c) * length))
    return out[0], out[1]


@dataclass
class Arrow(Shape):
    start: Point
    tip: Point
    kind = "arrow"

    def bbox(self):
        l, r = arrow_tip_lines(self.start, self.tip, 2.0)
        return bbox_of_points([self.start, self.tip, l, r])

    def transform(self, s, dx, dy):
        self.start, self.tip = _tp(self.start, s, dx, dy), _tp(self.tip, s, dx, dy)

    def to_json(self):
        return {"arrow": {"start": _pt(self.start), "tip": _pt(self.tip)}}


@dataclass
class Rect(Shape):
    x: float
    y: float
    w: float
    h: float
    angle: float = 0.0  # degrees, about the center
    kind = "rect"

    def corners(self) -> list[Point]:
        c = (self.x + self.w / 2, self.y + self.h / 2)
        pts = [(self.x, self.y), (self.x + self.w, self.y), (self.x + self.w, self.y + self.h), (self.x, self.y + self.h)]
        return [_rot(p, self.angle, c) for p in pts] if self.angle else pts

    def bbox(self):
        return bbox_of_points(self.corners())

    def transform(self, s, dx, dy):
        self.x, self.y = _tp((self.x, self.y), s, dx, dy)
        self.w *= s
        self.h *= s

    def to_json(self):
        return {
            "rect": {
                "cuboid": {"half_extents": [round(self.w / 2, 3), round(self.h / 2, 3)]},
                "transform": affine_json(self.x + self.w / 2, self.y + self.h / 2, self.angle),
            }
        }


@dataclass
class Ellipse(Shape):
    cx: float
    cy: float
    rx: float
    ry: float
    angle: float = 0.0
    kind = "ellipse"

    def bbox(self):
        if not self.angle:
            return BBox(self.cx - self.rx, self.cy - self.ry, self.cx + self.rx, self.cy + self.ry)
        a = math.radians(self.angle)
        hw = math.hypot(self.rx * math.cos(a), self.ry * math.sin(a))
        hh = math.hypot(self.rx * math.sin(a), self.ry * math.cos(a))
        return BBox(self.cx - hw, self.cy - hh, self.cx + hw, self.cy + hh)

    def transform(self, s, dx, dy):
        self.cx, self.cy = _tp((self.cx, self.cy), s, dx, dy)
        self.rx *= s
        self.ry *= s

    def to_json(self):
        return {"ellipse": {"radii": [round(self.rx, 3), round(self.ry, 3)], "transform": affine_json(self.cx, self.cy, self.angle)}}


@dataclass
class QuadBez(Shape):
    start: Point
    cp: Point
    end: Point
    kind = "quadbez"

    def bbox(self):
        return bbox_of_points(_sample_quad(self.start, self.cp, self.end))

    def transform(self, s, dx, dy):
        self.start, self.cp, self.end = (_tp(p, s, dx, dy) for p in (self.start, self.cp, self.end))

    def to_json(self):
        return {"quadbez": {"start": _pt(self.start), "cp": _pt(self.cp), "end": _pt(self.end)}}


@dataclass
class CubBez(Shape):
    start: Point
    cp1: Point
    cp2: Point
    end: Point
    kind = "cubbez"

    def bbox(self):
        return bbox_of_points(_sample_cubic(self.start, self.cp1, self.cp2, self.end))

    def transform(self, s, dx, dy):
        self.start, self.cp1, self.cp2, self.end = (_tp(p, s, dx, dy) for p in (self.start, self.cp1, self.cp2, self.end))

    def to_json(self):
        return {"cubbez": {"start": _pt(self.start), "cp1": _pt(self.cp1), "cp2": _pt(self.cp2), "end": _pt(self.end)}}


@dataclass
class Polyline(Shape):
    points: list[Point]
    kind = "polyline"

    def bbox(self):
        return bbox_of_points(self.points)

    def transform(self, s, dx, dy):
        self.points = [_tp(p, s, dx, dy) for p in self.points]

    def to_json(self):
        return {self.kind: {"start": _pt(self.points[0]), "path": [_pt(p) for p in self.points[1:]]}}


@dataclass
class Polygon(Polyline):
    kind = "polygon"


def _sample_quad(a: Point, b: Point, c: Point, n: int = 16) -> list[Point]:
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * a[0] + 2 * u * t * b[0] + t * t * c[0], u * u * a[1] + 2 * u * t * b[1] + t * t * c[1]))
    return out


def _sample_cubic(a: Point, b: Point, c: Point, d: Point, n: int = 24) -> list[Point]:
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((
            u**3 * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t**3 * d[0],
            u**3 * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t**3 * d[1],
        ))
    return out


# --------------------------------------------------------------------------- elements


class Element:
    id: str | None = None
    layer = "user"  # user | highlighter | image

    def bbox(self) -> BBox:
        raise NotImplementedError

    def geom_bbox(self) -> BBox:
        """Bounding box of the geometry, without the stroke width (bbox() includes it)."""
        return self.bbox()

    def transform(self, s: float, dx: float, dy: float) -> None:
        raise NotImplementedError

    def translate(self, dx: float, dy: float) -> None:
        self.transform(1.0, dx, dy)

    def to_stroke(self, rng: random.Random) -> dict:
        raise NotImplementedError


@dataclass
class ShapeEl(Element):
    shape: Shape
    style: Style = field(default_factory=Style)
    id: str | None = None

    def bbox(self):
        return self.shape.bbox().expand(self.style.width / 2 if self.style.stroke else 0)

    def geom_bbox(self):
        return self.shape.bbox()

    def transform(self, s, dx, dy):
        self.shape.transform(s, dx, dy)
        if s != 1.0:
            self.style = self.style.scaled(s)

    def to_stroke(self, rng):
        return {"shapestroke": {"shape": self.shape.to_json(), "style": self.style.to_json(rng)}}


ALIGNMENTS = {"start": "start", "left": "start", "center": "center", "centre": "center", "middle": "center",
              "end": "end", "right": "end", "fill": "fill", "justify": "fill"}


@dataclass
class TextEl(Element):
    text: str
    x: float
    y: float
    font: str = "sans-serif"
    size: float = 18.0
    color: Color = BLACK
    weight: int = 400
    italic: bool = False
    max_width: float | None = None
    align: str = "start"
    spans: list[Span] = field(default_factory=list)
    angle: float = 0.0
    underline: bool = False
    strike: bool = False
    id: str | None = None

    def measure(self) -> tuple[float, float, int]:
        bold, mono = char_flags(self.text, self.spans)
        return measure_text(self.text, self.font, self.size, self.max_width, self.weight >= 600, bold, mono)

    def bbox(self):
        w, h, _ = self.measure()
        h = max(h, self.size * LINE_HEIGHT)
        if self.angle:
            return bbox_of_points([_rot(p, self.angle, (self.x, self.y)) for p in
                                   [(self.x, self.y), (self.x + w, self.y), (self.x + w, self.y + h), (self.x, self.y + h)]])
        return BBox(self.x, self.y, self.x + w, self.y + h)

    def transform(self, s, dx, dy):
        self.x, self.y = _tp((self.x, self.y), s, dx, dy)
        self.size *= s
        if self.max_width:
            self.max_width *= s
        for sp in self.spans:
            if "size" in sp.attrs:
                sp.attrs["size"] *= s

    def ranged_attributes(self) -> list[dict]:
        offs = byte_offsets(self.text)
        out = []
        for sp in sorted(self.spans, key=lambda s: s.start):
            if sp.end <= sp.start:
                continue
            rng = {"start": offs[sp.start], "end": offs[min(sp.end, len(self.text))]}
            attrs = []
            if sp.bold:
                attrs.append({"font_weight": 700})
            if sp.italic:
                attrs.append({"font_style": "italic"})
            if sp.underline:
                attrs.append({"underline": True})
            if sp.strike:
                attrs.append({"strikethrough": True})
            if sp.mono:
                attrs.append({"font_family": "monospace"})
            if sp.color is not None:
                attrs.append({"text_color": sp.color.to_json()})
            if "size" in sp.attrs:
                attrs.append({"font_size": round(sp.attrs["size"], 3)})
            for a in attrs:
                out.append({"range": rng, "attribute": a})
        if self.text and (self.underline or self.strike):
            whole = {"start": 0, "end": offs[-1]}
            if self.underline:
                out.insert(0, {"range": whole, "attribute": {"underline": True}})
            if self.strike:
                out.insert(0, {"range": whole, "attribute": {"strikethrough": True}})
        return out

    def to_stroke(self, rng):
        return {
            "textstroke": {
                "text": self.text,
                "transform": affine_json(self.x, self.y, self.angle),
                "text_style": {
                    "font_family": self.font,
                    "font_size": round(self.size, 3),
                    "font_weight": int(self.weight),
                    "font_style": "italic" if self.italic else "regular",
                    "color": self.color.to_json(),
                    "max_width": round(self.max_width, 3) if self.max_width else None,
                    "alignment": ALIGNMENTS.get(self.align, "start"),
                    "ranged_text_attributes": self.ranged_attributes(),
                },
            }
        }


@dataclass
class PenEl(Element):
    """A freehand brush stroke (what the pen tool produces)."""

    points: list[tuple[float, float, float]]  # x, y, pressure
    color: Color = BLACK
    width: float = 2.0
    pressure_curve: str = "const"
    highlighter: bool = False
    id: str | None = None

    @property
    def layer(self):  # type: ignore[override]
        return "highlighter" if self.highlighter else "user"

    def bbox(self):
        return bbox_of_points([(p[0], p[1]) for p in self.points]).expand(self.width / 2)

    def geom_bbox(self):
        return bbox_of_points([(p[0], p[1]) for p in self.points])

    def transform(self, s, dx, dy):
        self.points = [(p[0] * s + dx, p[1] * s + dy, p[2]) for p in self.points]
        self.width *= s

    def to_stroke(self, rng):
        def el(p):
            return {"pos": [round(p[0], 3), round(p[1], 3)], "pressure": round(p[2], 3)}

        return {
            "brushstroke": {
                "path": {"start": el(self.points[0]), "segments": [{"lineto": {"end": el(p)}} for p in self.points[1:]]},
                "style": {
                    "smooth": {
                        "stroke_width": round(self.width, 3),
                        "stroke_color": self.color.to_json(),
                        "fill_color": None,
                        "pressure_curve": self.pressure_curve,
                        "line_style": "solid",
                        "line_cap": "rounded",
                    }
                },
            }
        }


@dataclass
class VectorImageEl(Element):
    svg: str
    x: float
    y: float
    w: float
    h: float
    intrinsic: tuple[float, float]
    id: str | None = None
    baseline: float | None = None  # math: the baseline's distance from the top, as a fraction of the height
    layer = "image"

    def bbox(self):
        return BBox(self.x, self.y, self.x + self.w, self.y + self.h)

    def transform(self, s, dx, dy):
        self.x, self.y = _tp((self.x, self.y), s, dx, dy)
        self.w *= s
        self.h *= s

    def to_stroke(self, rng):
        return {
            "vectorimage": {
                "svg_data": self.svg,
                "intrinsic_size": [round(self.intrinsic[0], 3), round(self.intrinsic[1], 3)],
                "rectangle": Rect(self.x, self.y, self.w, self.h).to_json()["rect"],
            }
        }


@dataclass
class BitmapImageEl(Element):
    rgba_premul_b64: str
    px_w: int
    px_h: int
    x: float
    y: float
    w: float
    h: float
    preview_href: str = ""  # data URI used only by the SVG preview
    id: str | None = None
    layer = "image"

    def bbox(self):
        return BBox(self.x, self.y, self.x + self.w, self.y + self.h)

    def transform(self, s, dx, dy):
        self.x, self.y = _tp((self.x, self.y), s, dx, dy)
        self.w *= s
        self.h *= s

    def to_stroke(self, rng):
        rect = Rect(self.x, self.y, self.w, self.h).to_json()["rect"]
        return {
            "bitmapimage": {
                "image": {
                    "data": self.rgba_premul_b64,
                    "rectangle": rect,
                    "pixel_width": self.px_w,
                    "pixel_height": self.px_h,
                    "memory_format": "R8g8b8a8Premultiplied",
                },
                "rectangle": rect,
            }
        }


# --------------------------------------------------------------------------- document

# Page formats in mm (Rnote's predefined formats) and conversion to px at 96 dpi.
PAGE_FORMATS_MM = {
    "a6": (105.0, 148.0), "a5": (148.0, 210.0), "a4": (210.0, 297.0), "a3": (297.0, 420.0), "a2": (420.0, 594.0),
    "letter": (215.9, 279.4), "us_letter": (215.9, 279.4), "legal": (215.9, 355.6), "us_legal": (215.9, 355.6),
}
LAYOUTS = {"fixed": "fixed_size", "fixed_size": "fixed_size", "pages": "fixed_size",
           "continuous": "continuous_vertical", "continuous_vertical": "continuous_vertical", "vertical": "continuous_vertical",
           "endless": "continuous_vertical", "infinite": "infinite", "semi_infinite": "semi_infinite",
           "semiinfinite": "semi_infinite", "semi-infinite": "semi_infinite"}
PATTERNS = {"none": "none", "blank": "none", "plain": "none", "lines": "lines", "lined": "lines", "ruled": "lines",
            "grid": "grid", "squared": "grid", "dots": "dots", "dotted": "dots", "isometric_grid": "isometric_grid",
            "isometric-grid": "isometric_grid", "isometric_dots": "isometric_dots", "isometric-dots": "isometric_dots"}


def mm_to_px(mm: float, dpi: float = 96.0) -> float:
    return mm / 25.4 * dpi


@dataclass
class PageSetup:
    width: float = mm_to_px(210.0)
    height: float = mm_to_px(297.0)
    layout: str = "continuous_vertical"
    background: Color = WHITE
    pattern: str = "none"
    pattern_color: Color = Color(0.8, 0.9, 1.0, 1.0)
    pattern_size: float = 32.0
    border_color: Color = Color(0.871, 0.867, 0.855, 1.0)
    show_borders: bool = True
    show_origin: bool = True
    margin: float = 56.0
    orientation: str = "portrait"


@dataclass
class Document:
    page: PageSetup = field(default_factory=PageSetup)
    elements: list[Element] = field(default_factory=list)
    min_pages: int = 1

    def content_bbox(self) -> BBox | None:
        return union_all([e.bbox() for e in self.elements])

    def page_count(self) -> int:
        bb = self.content_bbox()
        if bb is None:
            return max(1, self.min_pages)
        return max(1, self.min_pages, math.ceil((bb.y1 - 0.5) / self.page.height))
