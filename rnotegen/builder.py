"""Executes rnotegen scripts (and Markdown / Mermaid input) into a Document."""

from __future__ import annotations

import base64
import difflib
import math
import os
import re
import struct
from dataclasses import dataclass, field, replace
from typing import Callable

from .colors import Color, ColorError, parse_color
from .diagram import (
    TextSpec, catmull_rom_to_bezier, clip_to_outline, connector_elements, get_theme, label_at, label_box_elements,
    measure_label, node_elements, node_label_center, node_outline, node_size_for_text, normalize_head,
    normalize_kind, path_midpoint, rounded_rect_pts,
)
from .dsl import ScriptError, Stmt, Token, parse_script
from .markdown import Op, parse_markdown
from .markup import parse_inline
from .model import (
    ALIGNMENTS, Arrow, BBox, BitmapImageEl, CubBez, Document, Element, Ellipse, Line, PAGE_FORMATS_MM, LAYOUTS,
    PATTERNS, PenEl, Point, Polygon, Polyline, QuadBez, Rect, ShapeEl, Style, TextEl, VectorImageEl, mm_to_px,
    union_all,
)
from .textmetrics import LINE_HEIGHT, center_factor, resolve_family

UNITS = {"px": 1.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0, "pt": 96 / 72}
_LEN_RE = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+))\s*(px|mm|cm|in|pt|%)?$", re.I)
_SIZE_RE = re.compile(r"^([-+]?[\d.]+(?:px|mm|cm|in|pt|%)?)\s*[x×*X]\s*([-+]?[\d.]+(?:px|mm|cm|in|pt|%)?)$")
_REF_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\.([A-Za-z_]+))?(?:([+-])(.+))?$")
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DASH_NAMES = {
    "solid": "solid", "none": "solid", "dashed": "dashed_equidistant", "dash": "dashed_equidistant",
    "dotted": "dotted", "dots": "dotted", "dashed_narrow": "dashed_narrow", "narrow": "dashed_narrow",
    "dash_narrow": "dashed_narrow", "short": "dashed_narrow", "dashed_wide": "dashed_wide", "wide": "dashed_wide",
    "dash_wide": "dashed_wide", "long": "dashed_wide", "dashed_equidistant": "dashed_equidistant",
}
FILL_STYLE_NAMES = {
    "solid": "solid", "hachure": "hachure", "hatch": "hachure", "hatched": "hachure", "zigzag": "zig_zag",
    "zig_zag": "zig_zag", "zigzag_line": "zig_zag_line", "zig_zag_line": "zig_zag_line", "crosshatch": "crosshatch",
    "cross": "crosshatch", "dots": "dots", "dotted": "dots", "dashed": "dashed",
}
BULLETS = ["•", "◦", "▪", "‣"]
_MISSING = object()


class BuildError(Exception):
    def __init__(self, errors: list[ScriptError], warnings: list[str]):
        super().__init__("\n".join(str(e) for e in errors))
        self.errors = errors
        self.warnings = warnings


@dataclass
class Defaults:
    font: str = "sans-serif"
    size: float = 16.0
    text_color: Color = field(default_factory=lambda: parse_color("#1e1e1e"))
    stroke: Color | None = field(default_factory=lambda: parse_color("#1e1e1e"))
    fill: Color | None = None
    stroke_width: float = 2.0
    dash: str = "solid"
    rough: bool = False
    fill_style: str = "solid"
    gap: float = 12.0
    theme: str = "default"
    heading_color: Color | None = None
    box_fill: Color | None = field(default_factory=lambda: parse_color("#f3f5ff"))
    box_stroke: Color | None = field(default_factory=lambda: parse_color("#3b4a7a"))
    link_color: Color = field(default_factory=lambda: parse_color("#1f5fbf"))


@dataclass
class Entry:
    """A named thing that can be referenced as `id`, `id.anchor`, or connected."""

    elements: list[Element]
    line: int
    shape: tuple[str, float, float, float, float] | None = None  # (kind, x, y, w, h) for nodes

    def bbox(self) -> BBox:
        if self.shape:
            _, x, y, w, h = self.shape
            return BBox(x, y, x + w, y + h)
        return union_all([e.bbox() for e in self.elements]) or BBox(0, 0, 0, 0)

    def outline(self) -> list[Point]:
        if self.shape:
            kind, x, y, w, h = self.shape
            return node_outline(kind, x, y, w, h)
        b = self.bbox()
        return [(b.x0, b.y0), (b.x1, b.y0), (b.x1, b.y1), (b.x0, b.y1)]


@dataclass
class Ctx:
    out: list[Element]
    ox: float
    oy: float
    col_x: float
    col_w: float
    cursor: float
    paged: bool
    placed_on_page: bool = False
    after_list: bool = False  # the previous flow element was a list item (tighter spacing)
    pending_heading: tuple | None = None  # (elements, top y, height) of a heading not yet followed by content


class Opts:
    """Typed access to a statement's options; remembers which ones were used."""

    def __init__(self, st: Stmt, b: "Builder"):
        self.st = st
        self.b = b
        self.used: set[str] = set()
        self.used_pos: set[int] = set()

    def has(self, *names: str) -> bool:
        return any(n in self.st.kw for n in names)

    def raw(self, *names: str, default=None):
        for n in names:
            if n in self.st.kw:
                self.used.add(n)
                return self.st.kw[n].value
        return default

    def flag(self, *names: str) -> bool:
        for n in names:
            if n in self.st.kw:
                self.used.add(n)
                return parse_bool(self.st.kw[n].value, self.st.line, n)
        for i, t in enumerate(self.st.pos):
            if not t.quoted and t.value.lower().replace("-", "_") in names:
                self.used_pos.add(i)
                return True
        return False

    def flag_off(self, *names: str) -> bool:
        """True when the option was explicitly turned off (e.g. rough=off)."""
        for n in names:
            if n in self.st.kw:
                self.used.add(n)
                return not parse_bool(self.st.kw[n].value, self.st.line, n)
        return False

    def num(self, *names: str, default=None, axis: str | None = None):
        v = self.raw(*names)
        if v is None:
            return default
        return self.b.length(v, self.st.line, axis, names[0])

    def color(self, *names: str, default=_MISSING):
        v = self.raw(*names)
        if v is None:
            return None if default is _MISSING else default
        try:
            return parse_color(v)
        except (ColorError, ValueError) as e:
            raise ScriptError(self.st.line, f"option {names[0]}: {e}")

    def strings(self) -> list[str]:
        out = []
        for i, t in enumerate(self.st.pos):
            if t.quoted:
                self.used_pos.add(i)
                out.append(t.value)
        return out

    def words(self) -> list[tuple[int, str]]:
        return [(i, t.value) for i, t in enumerate(self.st.pos) if not t.quoted and i not in self.used_pos]

    def finish(self, known_flags: tuple[str, ...] = ()) -> None:
        for k in self.st.kw:
            if k not in self.used:
                valid = sorted(self.used | set(self.b.last_known))
                near = difflib.get_close_matches(k, valid + list(_COMMON_KEYS), n=1, cutoff=0.6)
                hint = f" (did you mean '{near[0]}'?)" if near else ""
                self.b.warn(self.st.line, f"option '{self.st.kw_lines.get(k, k)}' is not used by '{self.st.cmd}'{hint}")
        for i, t in enumerate(self.st.pos):
            if i not in self.used_pos:
                self.b.warn(self.st.line, f"value {t.value!r} was not understood by '{self.st.cmd}' and was ignored")


_COMMON_KEYS = ("at", "center", "anchor", "below", "above", "right_of", "left_of", "gap", "align", "id", "width",
                "height", "size", "stroke", "fill", "color", "stroke_width", "dash", "font", "rotate", "opacity",
                "label", "shape", "theme", "scale", "x", "y")


def parse_bool(v: str, line: int, name: str) -> bool:
    low = v.strip().lower()
    if low in ("1", "true", "yes", "on", "y"):
        return True
    if low in ("0", "false", "no", "off", "n", "none"):
        return False
    raise ScriptError(line, f"option {name}: expected on/off, got {v!r}")


class Builder:
    def __init__(self, base_dir: str = ".", page=None):
        self.doc = Document()
        if page is not None:
            self.doc.page = page
        self.base_dir = base_dir
        self.d = Defaults()
        self.ids: dict[str, Entry] = {}
        self.warnings: list[str] = []
        self.errors: list[ScriptError] = []
        self.src_line: dict[int, int] = {}
        self.min_pages = 1
        self.last_known: tuple[str, ...] = ()
        self.start_y: float | None = None  # where flow content starts (used when appending to a file)
        self.cur_line = 0
        self.shape_entries: list[Entry] = []
        self.ctx: Ctx = self._top_ctx()
        self._adapt_to_background()
        self.ctx_stack: list[Ctx] = []

    # ------------------------------------------------------------------ public API

    def run_script(self, text: str) -> Document:
        try:
            stmts = parse_script(text)
        except ScriptError as e:
            raise BuildError([e], self.warnings)
        for st in stmts:
            if st.cmd == "document":
                self._exec(st)
        self.ctx = self._top_ctx()
        for st in stmts:
            if st.cmd != "document":
                self._exec(st)
        return self.finish()

    def run_markdown(self, text: str) -> Document:
        st = Stmt("markdown", 1, body=text.split("\n"), body_line=1)
        self._exec(st)
        return self.finish()

    def run_mermaid(self, text: str, auto_orient: bool = True, **opts) -> Document:
        """Put a single Mermaid diagram on a page (landscape when the diagram is wide)."""
        if auto_orient:
            from .mermaid import MermaidError, render_mermaid

            try:
                dg = render_mermaid(text)
                p = self.doc.page
                if dg.bbox.w > dg.bbox.h * 1.15 and p.width < p.height:
                    p.width, p.height = p.height, p.width
                    self.ctx = self._top_ctx()
            except (MermaidError, ValueError):
                pass  # reported by the real run below
        st = Stmt("mermaid", 1, body=text.split("\n"), body_line=1)
        for k, v in opts.items():
            if v is not None and v is not False:
                st.kw[k] = Token("on" if v is True else str(v))
        self._exec(st)
        return self.finish()

    def finish(self) -> Document:
        if self.errors:
            raise BuildError(self.errors, self.warnings)
        self.doc.min_pages = self.min_pages
        self._checks()
        return self.doc

    def warn(self, line: int, msg: str) -> None:
        self.warnings.append(f"line {line}: {msg}")

    # ------------------------------------------------------------------ infrastructure

    def _top_ctx(self) -> Ctx:
        p = self.doc.page
        paged = p.layout in ("fixed_size", "continuous_vertical")
        start = p.margin if self.start_y is None else max(p.margin, self.start_y)
        return Ctx(self.doc.elements, 0.0, 0.0, p.margin, p.width - 2 * p.margin, start, paged)

    def _adapt_to_background(self) -> None:
        """Light ink and the dark diagram theme on dark pages."""
        bg = self.doc.page.background
        lum = 0.2126 * bg.r + 0.7152 * bg.g + 0.0722 * bg.b
        if lum < 0.4:
            d = self.d
            d.text_color = parse_color("#ececec")
            d.stroke = parse_color("#e0e0e0")
            d.box_fill = parse_color("#2b3045")
            d.box_stroke = parse_color("#aab4ff")
            d.link_color = parse_color("#8ab4ff")
            d.theme = "dark"

    def _exec(self, st: Stmt) -> None:
        handler = getattr(self, f"cmd_{st.cmd}", None)
        if handler is None:
            self.errors.append(ScriptError(st.line, f"command '{st.cmd}' is not implemented"))
            return
        o = Opts(st, self)
        self.cur_line = st.line
        try:
            handler(st, o)
            o.finish()
        except ScriptError as e:
            self.errors.append(e)
        except (ColorError, ValueError) as e:
            self.errors.append(ScriptError(st.line, str(e)))

    def length(self, v: str, line: int, axis: str | None = None, name: str = "value") -> float:
        m = _LEN_RE.match(v.strip())
        if not m:
            raise ScriptError(line, f"{name}: expected a number like 120, 20mm or 50%, got {v!r}")
        num = float(m.group(1))
        unit = (m.group(2) or "px").lower()
        if unit == "%":
            base = self.doc.page.height if axis == "y" else self.doc.page.width
            return num / 100.0 * base
        return num * UNITS[unit]

    def point(self, v: str, line: int, name: str = "point") -> Point:
        """Parse 'x,y' (relative to the current origin) or 'id.anchor[+dx,dy]' (absolute)."""
        s = v.strip()
        parts = s.split(",")
        if len(parts) == 2 and _LEN_RE.match(parts[0].strip() or "x") and _LEN_RE.match(parts[1].strip() or "x"):
            return (self.ctx.ox + self.length(parts[0], line, "x", name), self.ctx.oy + self.length(parts[1], line, "y", name))
        m = _REF_RE.match(s)
        if m and m.group(1) in self.ids:
            ent = self.ids[m.group(1)]
            bb = ent.bbox()
            anchor = m.group(2) or "center"
            try:
                p = bb.anchor(anchor)
            except KeyError:
                raise ScriptError(line, f"unknown anchor '.{anchor}'",
                                  "use one of: topleft top topright left center right bottomleft bottom bottomright")
            if m.group(3):
                # the sign belongs to dx only: a.bottom-70,20 means dx=-70, dy=+20
                off = (m.group(3) + m.group(4)).split(",")
                if len(off) != 2:
                    raise ScriptError(line, f"bad offset in {s!r}; write it like {m.group(1)}.{anchor}+10,20 or {m.group(1)}.{anchor}-10,-20")
                p = (p[0] + self.length(off[0], line, "x"), p[1] + self.length(off[1], line, "y"))
            return p
        if m:
            near = difflib.get_close_matches(m.group(1), list(self.ids), n=1)
            hint = f"did you mean '{near[0]}'?" if near else (
                "ids must be defined (id=...) before they are used" if self.ids else "no element has an id yet; add id=name to an element first")
            raise ScriptError(line, f"unknown id {m.group(1)!r} in {name} {v!r}", hint)
        raise ScriptError(line, f"{name}: expected x,y (like 120,80) or id.anchor (like title.bottom), got {v!r}")

    def points(self, toks: list[str], line: int) -> list[Point]:
        out = []
        for t in toks:
            parts = t.split(",")
            if len(parts) > 2 and len(parts) % 2 == 0 and all(_LEN_RE.match(p.strip() or "x") for p in parts):
                for k in range(0, len(parts), 2):
                    out.append(self.point(parts[k] + "," + parts[k + 1], line))
            else:
                out.append(self.point(t, line))
        return out

    def size(self, v: str, line: int, name: str = "size") -> tuple[float, float]:
        m = _SIZE_RE.match(v.strip())
        if not m:
            n = _LEN_RE.match(v.strip())
            if n:
                s = self.length(v, line, None, name)
                return s, s
            raise ScriptError(line, f"{name}: expected WxH like 200x80, got {v!r}")
        return self.length(m.group(1), line, "x", name), self.length(m.group(2), line, "y", name)

    def emit(self, st: Stmt, els: list[Element], o: Opts | None = None, flow: bool = False,
             shape: tuple | None = None, register: bool = True) -> Entry:
        for e in els:
            self.src_line[id(e)] = st.line
        self.ctx.out.extend(els)
        ent = Entry(els, st.line, shape)
        if shape is not None and not self.ctx_stack:
            self.shape_entries.append(ent)
        self.ctx.pending_heading = None
        if o is not None and register:
            ident = o.raw("id", "name")
            if ident:
                if not _ID_RE.match(ident):
                    raise ScriptError(st.line, f"invalid id {ident!r}", "ids are letters, digits and _ (no spaces or dashes)")
                if ident in self.ids:
                    raise ScriptError(st.line, f"duplicate id {ident!r} (first defined on line {self.ids[ident].line})")
                self.ids[ident] = ent
        floating = o.flag("float", "floating", "overlay") if o is not None else False
        if els and not floating:
            bb = ent.bbox() if shape is None else union_all([e.bbox() for e in els])
            self.ctx.cursor = max(self.ctx.cursor, bb.y1 + self.d.gap)
            self.ctx.placed_on_page = True
        return ent

    # ------------------------------------------------------------------ placement

    def place(self, o: Opts, w: float, h: float, need_flow_ok: bool = True, align: str = "left",
              keep: float = 0.0) -> tuple[float, float, bool]:
        """Top-left corner for a w x h box from at=/center=/below=/x=/y= options, or the flow position."""
        line = o.st.line
        anchor = (o.raw("anchor", "origin") or "topleft").lower()
        try:
            ax, ay = BBox(0, 0, w, h).anchor(anchor)
        except KeyError:
            raise ScriptError(line, f"unknown anchor {anchor!r}",
                              "use one of: topleft top topright left center right bottomleft bottom bottomright")
        v = o.raw("at", "pos", "position", "xy")
        if v is not None:
            p = self.point(v, line, "at")
            return p[0] - ax, p[1] - ay, False
        v = o.raw("center", "centre", "middle", "c")
        if v is not None:
            p = self.point(v, line, "center")
            return p[0] - w / 2, p[1] - h / 2, False
        gap = o.num("gap", "margin", "spacing", default=20.0)
        for keys, side in ((("below", "under"), "below"), (("above", "over"), "above"),
                           (("right_of", "rightof", "right"), "right"), (("left_of", "leftof", "left"), "left")):
            v = o.raw(*keys)
            if v is None:
                continue
            ref = self._ref_bbox(v, line)
            al = (o.raw("align") or ("center" if side in ("below", "above") else "middle")).lower()
            if side in ("below", "above"):
                x = {"left": ref.x0, "start": ref.x0, "right": ref.x1 - w, "end": ref.x1 - w}.get(al, ref.cx - w / 2)
                y = ref.y1 + gap if side == "below" else ref.y0 - gap - h
            else:
                y = {"top": ref.y0, "bottom": ref.y1 - h}.get(al, ref.cy - h / 2)
                x = ref.x1 + gap if side == "right" else ref.x0 - gap - w
            return x, y, False
        xs, ys = o.raw("x", "left_x"), o.raw("y", "top_y")
        if xs is not None or ys is not None:
            x = self.ctx.ox + self.length(xs, line, "x", "x") - ax if xs is not None else self.ctx.col_x
            y = self.ctx.oy + self.length(ys, line, "y", "y") - ay if ys is not None else self.ctx.cursor
            return x, y, False
        if not need_flow_ok:
            raise ScriptError(line, f"'{o.st.cmd}' needs a position", "add at=X,Y (or center=X,Y, below=ID, ...)")
        al = (o.raw("align") or align).lower()
        y = self.flow_y(h + keep)
        c = self.ctx
        if al in ("center", "centre", "middle"):
            x = c.col_x + (c.col_w - w) / 2
        elif al in ("right", "end"):
            x = c.col_x + c.col_w - w
        else:
            x = c.col_x
        return x, y, True

    def _ref_bbox(self, v: str, line: int) -> BBox:
        v = v.strip()
        if v in self.ids:
            return self.ids[v].bbox()
        p = self.point(v, line)
        return BBox(p[0], p[1], p[0], p[1])

    def flow_y(self, h: float, list_item: bool = False) -> float:
        c = self.ctx
        if c.after_list and not list_item:
            c.cursor += self.d.gap * 0.55  # restore normal spacing after a list
        c.after_list = list_item
        y = c.cursor
        if not c.paged:
            return y
        page = self.doc.page
        ph, m = page.height, page.margin
        idx = max(0, math.floor((y - 0.001) / ph))
        top = idx * ph
        if y < top + m:
            y = top + m
        limit = top + ph - m
        if y + h > limit and h <= ph - 2 * m + 0.5 and y > top + m + 1:
            left = limit - y
            y = self._new_page_y()
            if left > 0.35 * (ph - 2 * m):
                self.warn(self.cur_line, f"this did not fit on the page and moved to the next one, leaving {left:.0f}px "
                                         f"empty (make it smaller with height=/scale=/width=, or accept the gap)")
        return y

    def _new_page_y(self) -> float:
        """Move the flow to the top of the next page, taking a just-placed heading along."""
        c = self.ctx
        page = self.doc.page
        idx = max(0, math.floor((c.cursor - 0.001) / page.height))
        y = (idx + 1) * page.height + page.margin
        if c.pending_heading is not None:
            els, top, height = c.pending_heading
            if math.floor(top / page.height) == idx:
                for e in els:
                    e.translate(0, y - top)
                y += height
            c.pending_heading = None
        c.cursor = y
        c.placed_on_page = y > (idx + 1) * page.height + page.margin + 1
        return y

    def break_page(self) -> None:
        """Continue the flow at the top of the next page."""
        if self.ctx.paged:
            self._new_page_y()

    def _keep_together(self, h: float) -> None:
        """Move to the next page if a block of height h does not fit here but is short enough to keep whole."""
        c = self.ctx
        if not c.paged or self.at_page_top():
            return
        page = self.doc.page
        if self.page_room() < h <= (page.height - 2 * page.margin) * 0.5:
            self.break_page()

    def page_room(self) -> float:
        """Remaining height on the current page for flow content (inf when not paged)."""
        c = self.ctx
        if not c.paged:
            return float("inf")
        page = self.doc.page
        idx = max(0, math.floor((c.cursor - 0.001) / page.height))
        return (idx + 1) * page.height - page.margin - max(c.cursor, idx * page.height + page.margin)

    def at_page_top(self) -> bool:
        c = self.ctx
        if not c.paged:
            return not c.placed_on_page
        page = self.doc.page
        idx = max(0, math.floor((c.cursor - 0.001) / page.height))
        return c.cursor <= idx * page.height + page.margin + 1

    # ------------------------------------------------------------------ styles

    def style(self, o: Opts, closed: bool, width_key_is_stroke: bool = False) -> Style:
        d = self.d
        stroke = o.color("stroke", "color", "line_color", "border", "outline", "stroke_color", default=d.stroke)
        fill = o.color("fill", "background", "bg", "fill_color", default=d.fill if closed else None)
        keys = ("stroke_width", "thickness", "lw", "line_width", "linewidth", "border_width")
        if width_key_is_stroke:
            keys = keys + ("width", "w")
        sw = o.num(*keys, default=d.stroke_width)
        dash = d.dash
        dv = o.raw("dash", "line_style", "linestyle", "pattern")
        if dv is not None:
            if dv.lower() not in DASH_NAMES:
                raise ScriptError(o.st.line, f"unknown dash style {dv!r}", "use solid, dashed, dotted, dashed_narrow or dashed_wide")
            dash = DASH_NAMES[dv.lower()]
        for f, val in (("dashed", "dashed_equidistant"), ("dotted", "dotted"), ("solid", "solid")):
            if o.flag(f):
                dash = val
        rough = d.rough
        if o.flag("rough", "sketch", "sketchy", "handdrawn", "hand_drawn"):
            rough = True
        if o.flag_off("rough"):
            rough = False
        sv = o.raw("style")
        if sv is not None:
            rough = sv.lower() in ("rough", "sketch", "sketchy", "hand", "handdrawn")
        fs = d.fill_style
        fv = o.raw("fill_style", "hatch", "fillstyle")
        if fv is not None:
            if fv.lower() not in FILL_STYLE_NAMES:
                raise ScriptError(o.st.line, f"unknown fill_style {fv!r}",
                                  "use solid, hachure, zigzag, zigzag_line, crosshatch, dots or dashed")
            fs = FILL_STYLE_NAMES[fv.lower()]
            if fs != "solid":
                rough = True  # fill patterns are only available in Rnote's rough style
        op = o.num("opacity", "alpha")
        if op is not None:
            stroke = stroke.with_alpha(stroke.a * op) if stroke else None
            fill = fill.with_alpha(fill.a * op) if fill else None
        return Style(stroke=stroke, width=sw, fill=fill, dash=dash, rough=rough, fill_style=fs)

    def text_spec(self, o: Opts, size: float | None = None, color: Color | None = None, bold: bool = False,
                  italic: bool = False) -> dict:
        d = self.d
        font = o.raw("font", "font_family", "family")
        size_tok = o.st.kw.get("size")
        if size_tok is not None and _SIZE_RE.match(size_tok.value.strip()):
            sz = o.num("font_size", "fontsize", "text_size")  # size=WxH is a box size, not a font size
        else:
            sz = o.num("size", "font_size", "fontsize", "text_size")
        col = o.color("color", "text_color", "font_color", default=None)
        weight = o.raw("weight", "font_weight")
        w = 700 if (o.flag("bold", "strong") or bold) else 400
        if weight is not None:
            wl = weight.lower()
            w = {"bold": 700, "normal": 400, "regular": 400, "light": 300, "thin": 200, "semibold": 600,
                 "medium": 500, "black": 900, "heavy": 800}.get(wl, None) or int(float(weight))
        op = o.num("opacity", "alpha")
        c = col or color or d.text_color
        if op is not None:
            c = c.with_alpha(c.a * op)
        return {
            "font": resolve_family(font) if font else d.font,
            "size": sz or size or d.size,
            "color": c,
            "weight": w,
            "italic": o.flag("italic", "em", "oblique") or italic,
            "underline": o.flag("underline", "underlined"),
            "strike": o.flag("strike", "strikethrough", "crossed"),
            "markup": not (o.flag("raw", "plain", "nomarkup") or o.flag_off("markup", "markdown")),
        }

    def make_text(self, text: str, x: float, y: float, spec: dict, max_width: float | None, align: str = "start",
                  angle: float = 0.0) -> TextEl:
        if spec.get("markup", True):
            plain, spans = parse_inline(text, link_color=self.d.link_color)
        else:
            plain, spans = text, []
        return TextEl(plain, x, y, font=spec["font"], size=spec["size"], color=spec["color"], weight=spec["weight"],
                      italic=spec["italic"], max_width=max_width, align=ALIGNMENTS.get(align, "start"), spans=spans,
                      angle=angle, underline=spec.get("underline", False), strike=spec.get("strike", False))

    # ------------------------------------------------------------------ setup commands

    def cmd_document(self, st: Stmt, o: Opts) -> None:
        p = self.doc.page
        size = o.raw("size", "format", "paper")
        orient = (o.raw("orientation", "orient") or "").lower()
        if o.flag("landscape"):
            orient = "landscape"
        if o.flag("portrait"):
            orient = "portrait"
        if size:
            key = size.lower().replace("-", "_").replace(" ", "_")
            if key in PAGE_FORMATS_MM:
                w, h = PAGE_FORMATS_MM[key]
                p.width, p.height = mm_to_px(w), mm_to_px(h)
            else:
                p.width, p.height = self.size(size, st.line, "size")
        if orient == "landscape" and p.width < p.height or orient == "portrait" and p.width > p.height:
            p.width, p.height = p.height, p.width
        v = o.raw("layout", "mode")
        if v is not None:
            key = v.lower().replace("-", "_")
            if key not in LAYOUTS:
                raise ScriptError(st.line, f"unknown layout {v!r}", "use pages, continuous or infinite")
            p.layout = LAYOUTS[key]
        p.background = o.color("background", "bg", "paper_color", default=p.background) or p.background
        self._adapt_to_background()
        v = o.raw("pattern", "grid", "lines")
        if v is not None:
            key = v.lower()
            if key not in PATTERNS:
                raise ScriptError(st.line, f"unknown pattern {v!r}", "use none, lines, grid, dots, isometric_grid or isometric_dots")
            p.pattern = PATTERNS[key]
        p.pattern_color = o.color("pattern_color", "grid_color", "line_color", default=p.pattern_color) or p.pattern_color
        p.pattern_size = o.num("pattern_size", "spacing", "grid_size", "line_spacing", default=p.pattern_size)
        p.margin = o.num("margin", "margins", default=p.margin)
        v = o.raw("borders", "page_borders", "show_borders")
        if v is not None:
            p.show_borders = parse_bool(v, st.line, "borders")
        pages = o.num("pages", "min_pages")
        if pages:
            self.min_pages = max(self.min_pages, int(pages))

    def cmd_defaults(self, st: Stmt, o: Opts) -> None:
        d = self.d
        v = o.raw("font", "font_family")
        if v:
            d.font = resolve_family(v)
        d.size = o.num("size", "font_size", "text_size", default=d.size)
        c = o.color("color", "ink", default=None)
        if c is not None:
            d.text_color = c
            d.stroke = c
        d.text_color = o.color("text_color", "font_color", default=d.text_color) or d.text_color
        if o.has("stroke", "line_color", "stroke_color"):
            d.stroke = o.color("stroke", "line_color", "stroke_color")
        if o.has("fill", "fill_color"):
            d.fill = o.color("fill", "fill_color")
        d.stroke_width = o.num("stroke_width", "thickness", "lw", "line_width", "width", default=d.stroke_width)
        v = o.raw("dash", "line_style")
        if v is not None:
            if v.lower() not in DASH_NAMES:
                raise ScriptError(st.line, f"unknown dash style {v!r}")
            d.dash = DASH_NAMES[v.lower()]
        v = o.raw("rough", "sketch")
        if v is not None:
            d.rough = parse_bool(v, st.line, "rough")
        elif o.flag("rough", "sketch"):
            d.rough = True
        v = o.raw("fill_style", "hatch")
        if v is not None:
            d.fill_style = FILL_STYLE_NAMES.get(v.lower(), "solid")
        d.gap = o.num("gap", "spacing", "paragraph_gap", default=d.gap)
        v = o.raw("theme", "diagram_theme")
        if v is not None:
            get_theme(v)
            d.theme = v
        d.heading_color = o.color("heading_color", "headings", default=d.heading_color)
        if o.has("box_fill"):
            d.box_fill = o.color("box_fill")
        if o.has("box_stroke"):
            d.box_stroke = o.color("box_stroke")
        d.link_color = o.color("link_color", default=d.link_color) or d.link_color

    def cmd_page(self, st: Stmt, o: Opts) -> None:
        if self.ctx_stack:
            raise ScriptError(st.line, "'page' cannot be used inside a group")
        n = None
        for i, t in o.words():
            if re.match(r"^\d+$", t):
                n = int(t)
                o.used_pos.add(i)
                break
        n = int(o.num("number", "n", default=n or 0))
        if n < 1:
            raise ScriptError(st.line, "page needs a page number", "write: page 2")
        ph = self.doc.page.height
        self.ctx.ox, self.ctx.oy = 0.0, (n - 1) * ph
        self.ctx.cursor = (n - 1) * ph + self.doc.page.margin
        self.ctx.placed_on_page = False
        self.min_pages = max(self.min_pages, n)

    def cmd_newpage(self, st: Stmt, o: Opts) -> None:
        c = self.ctx
        if not c.paged:
            self.warn(st.line, "newpage has no effect with an infinite layout")
            return
        ph = self.doc.page.height
        idx = max(0, math.floor((c.cursor - 0.001) / ph))
        if self.at_page_top() and not c.placed_on_page:
            return
        c.cursor = (idx + 1) * ph + self.doc.page.margin
        c.placed_on_page = False
        c.pending_heading = None
        if not self.ctx_stack:
            c.ox, c.oy = 0.0, (idx + 1) * ph
        self.min_pages = max(self.min_pages, idx + 2)

    def cmd_space(self, st: Stmt, o: Opts) -> None:
        amount = 20.0
        for i, t in o.words():
            amount = self.length(t, st.line, "y", "space")
            o.used_pos.add(i)
            break
        amount = o.num("height", "amount", "size", default=amount, axis="y")
        self.ctx.cursor += amount

    # ------------------------------------------------------------------ text commands

    def _text_content(self, st: Stmt, o: Opts) -> str:
        strs = o.strings()
        if st.body is not None:
            body = "\n".join(st.body).strip("\n")
            return body
        if not strs:
            v = o.raw("text", "content", "value")
            if v is not None:
                return v
            raise ScriptError(st.line, f"'{st.cmd}' needs text", f'write: {st.cmd} "your text"')
        return "\n".join(strs)

    def cmd_text(self, st: Stmt, o: Opts, flow_width: bool = False) -> None:
        text = self._text_content(st, o)
        spec = self.text_spec(o)
        align = (o.raw("text_align", "justify_text") or o.st.kw.get("align", Token("left")).value).lower()
        angle = o.num("rotate", "rotation", "angle", default=0.0)
        wrap = o.num("width", "max_width", "wrap", "w")
        raw_origin = o.raw("text_origin")
        if raw_origin is not None:
            # the stored Rnote text position, no alignment adjustment (used by decompiled scripts)
            p = self.point(raw_origin, st.line, "text_origin")
            el = self.make_text(text, p[0], p[1], spec, wrap, align, angle)
            o.raw("align")
            self.emit(st, [el], o)
            return
        explicit = o.has("at", "center", "centre", "middle", "below", "above", "right_of", "left_of", "x", "y",
                         "pos", "position", "under", "over", "rightof", "leftof", "c", "xy")
        if not explicit:
            # flow text: wraps to the column
            c = self.ctx
            wrap = wrap or c.col_w
            el = self.make_text(text, 0, 0, spec, wrap, align)
            w, h, _ = el.measure()
            o.raw("align")
            y = self.flow_y(h)
            el.y = y
            el.x = self._aligned_x(c.col_x, wrap, w, align, spec["font"])
            self.emit(st, [el], o, flow=True)
            return
        el = self.make_text(text, 0, 0, spec, wrap, align, angle)
        est_w, h, _ = el.measure()
        a = ALIGNMENTS.get(align, "start")
        vis_w = est_w * (center_factor(spec["font"]) if a in ("center", "end") else 1.0)
        # with width=, the position refers to the whole text box and the text is aligned inside it
        box_w = wrap if wrap else vis_w
        x, y, _ = self.place(o, box_w, h, need_flow_ok=True, align=align)
        el.x = self._aligned_x(x, box_w, est_w, align, spec["font"]) if wrap else x
        el.y = y
        if wrap is None:
            page_w = self.doc.page.width
            room = page_w - self.doc.page.margin / 2 - x
            if est_w > room and room > 80 and x < page_w:
                el.max_width = room
                self.warn(st.line, f"text is wider than the space to the right page edge; wrapped at {room:.0f}px (set width= to control)")
        o.raw("align")
        self.emit(st, [el], o)

    def _aligned_x(self, col_x: float, col_w: float, text_w: float, align: str, font: str = "sans-serif") -> float:
        """Left edge for text of estimated width text_w aligned inside [col_x, col_x + col_w].

        Rnote positions centered/right-aligned text by its left edge, so the
        alignment has to be done here.
        """
        a = ALIGNMENTS.get(align, "start")
        f = center_factor(font)
        if a == "center":
            return col_x + (col_w - text_w * f) / 2
        if a == "end":
            return col_x + col_w - text_w * f
        return col_x

    def cmd_para(self, st: Stmt, o: Opts) -> None:
        self.cmd_text(st, o)

    def _heading(self, st: Stmt, o: Opts, level: int, text: str | None = None, keep: float | None = None) -> None:
        text = text if text is not None else self._text_content(st, o)
        scale = {1: 2.0, 2: 1.6, 3: 1.3, 4: 1.12, 5: 1.0, 6: 0.95}.get(level, 1.0)
        spec = self.text_spec(o, size=self.d.size * scale, color=self.d.heading_color, bold=True)
        c = self.ctx
        if not self.at_page_top():
            c.cursor += spec["size"] * 0.55
        align = (o.raw("align") or "left").lower()
        explicit = o.has("at", "center", "below", "above", "right_of", "left_of", "x", "y")
        el = self.make_text(text, 0, 0, spec, c.col_w, align)
        w, h, _ = el.measure()
        if explicit:
            x, y, _ = self.place(o, w, h)
            el.x, el.y = x, y
            el.max_width = None
        else:
            if keep is None:
                keep = self.d.size * LINE_HEIGHT * 3  # keep with the next lines
            y = self.flow_y(h + self.d.gap + keep)
            el.x, el.y = self._aligned_x(c.col_x, c.col_w, w, align, spec["font"]), y
        els: list[Element] = [el]
        if o.flag("rule", "line", "underline_bar") or (level == 1 and o.flag("hr")):
            ly = el.y + h + 4
            els.append(ShapeEl(Line((c.col_x, ly), (c.col_x + c.col_w, ly)),
                               Style(stroke=spec["color"].with_alpha(0.5), width=1.5)))
        self.emit(st, els, o)
        c.cursor -= self.d.gap * 0.4
        if not explicit:
            c.pending_heading = (els, el.y, c.cursor - el.y)

    def cmd_h1(self, st, o):
        self._heading(st, o, 1)

    def cmd_h2(self, st, o):
        self._heading(st, o, 2)

    def cmd_h3(self, st, o):
        self._heading(st, o, 3)

    def cmd_h4(self, st, o):
        self._heading(st, o, 4)

    def cmd_heading(self, st, o):
        level = int(o.num("level", "lvl", default=1))
        self._heading(st, o, max(1, min(6, level)))

    def _list_item(self, st: Stmt, o: Opts | None, text: str, level: int, marker: str, spec: dict,
                   marker_color: Color | None = None) -> None:
        c = self.ctx
        indent = 24.0 * (level - 1)
        msize = spec["size"]
        mw = max(18.0, measure_label(marker, TextSpec(spec["font"], msize, spec["color"]), None)[0] + 8)
        x0 = c.col_x + indent
        body_x = x0 + mw
        el = self.make_text(text, body_x, 0, spec, c.col_w - indent - mw)
        w, h, _ = el.measure()
        y = self.flow_y(h, list_item=True)
        el.y = y
        mk = TextEl(marker, x0 + (2 if marker in BULLETS else 0), y, font=spec["font"], size=msize,
                    color=marker_color or spec["color"], weight=spec["weight"])
        self.emit(st, [mk, el], o)
        c.cursor -= self.d.gap * 0.55

    def cmd_bullet(self, st: Stmt, o: Opts) -> None:
        text = self._text_content(st, o)
        level = int(o.num("level", "indent", "depth", default=1))
        marker = o.raw("marker", "symbol", "number", "bullet")
        if marker is None:
            marker = BULLETS[(level - 1) % len(BULLETS)]
        self._list_item(st, o, text, level, marker, self.text_spec(o))

    def cmd_todo(self, st: Stmt, o: Opts) -> None:
        text = self._text_content(st, o)
        level = int(o.num("level", "indent", default=1))
        done = o.flag("done", "checked", "complete", "x")
        spec = self.text_spec(o)
        if done:
            spec["color"] = spec["color"].mix(Color(1, 1, 1), 0.45)
        self._list_item(st, o, text, level, "☑" if done else "☐", spec)

    def cmd_quote(self, st: Stmt, o: Opts) -> None:
        text = self._text_content(st, o)
        spec = self.text_spec(o, italic=True, color=self.d.text_color.mix(Color(1, 1, 1), 0.3))
        self._quote(st, o, text, spec)

    def _quote(self, st: Stmt, o: Opts | None, text: str, spec: dict) -> None:
        c = self.ctx
        el = self.make_text(text, c.col_x + 18, 0, spec, c.col_w - 18)
        w, h, _ = el.measure()
        y = self.flow_y(h)
        el.y = y
        bar = ShapeEl(Line((c.col_x + 4, y + 2), (c.col_x + 4, y + h - 2)),
                      Style(stroke=parse_color("#9aa5b1"), width=4.0, cap="straight"))
        self.emit(st, [bar, el], o)

    def cmd_callout(self, st: Stmt, o: Opts) -> None:
        text = self._text_content(st, o)
        title = o.raw("title", "heading")
        spec = self.text_spec(o)
        fill = o.color("fill", "background", "bg", default=parse_color("#fff4c2"))
        stroke = o.color("stroke", "border", default=parse_color("#d6b656"))
        c = self.ctx
        explicit = o.has("at", "center", "below", "above", "right_of", "left_of", "x", "y")
        width = o.num("width", "w", default=c.col_w if not explicit else min(360.0, c.col_w))
        pad = 12.0
        body = f"**{title}**\n{text}" if title else text
        el = self.make_text(body, 0, 0, spec, width - 2 * pad)
        _, th_, _ = el.measure()
        h = th_ + 2 * pad
        x, y, _ = self.place(o, width, h)
        el.x, el.y = x + pad, y + pad
        box = ShapeEl(Polygon(rounded_rect_pts(x, y, width, h, 8)), Style(stroke=stroke, fill=fill, width=1.5,
                                                                         rough=self.d.rough))
        self.emit(st, [box, el], o, shape=("round", x, y, width, h))

    def cmd_hr(self, st: Stmt, o: Opts | None) -> None:
        c = self.ctx
        y = self.flow_y(10) + 4
        col = (o.color("color", "stroke", default=None) if o else None) or parse_color("#c4c9d4")
        sw = (o.num("stroke_width", "thickness", "width", default=1.5) if o else 1.5)
        el = ShapeEl(Line((c.col_x, y), (c.col_x + c.col_w, y)), Style(stroke=col, width=sw))
        self.emit(st, [el], o)
        c.cursor = y + 4 + self.d.gap

    # ------------------------------------------------------------------ shapes

    def _label_in(self, o: Opts, st: Stmt, x: float, y: float, w: float, h: float, kind: str = "rect") -> list[Element]:
        lab = o.raw("label", "text", "title")
        if not lab:
            return []
        spec = self.text_spec(o)
        ts = TextSpec(spec["font"], spec["size"], spec["color"], bold=spec["weight"] >= 600, italic=spec["italic"],
                      markup=spec["markup"])
        cx, cy = node_label_center(kind, x, y, w, h)
        return [label_at(lab, cx, cy, ts, max(20.0, w - 16))]

    def cmd_rect(self, st: Stmt, o: Opts) -> None:
        words = o.words()
        pts, sizes = [], []
        for i, t in words:
            if _SIZE_RE.match(t):
                sizes.append(self.size(t, st.line))
                o.used_pos.add(i)
            elif "," in t or _REF_RE.match(t) and t.split(".")[0].split("+")[0] in self.ids:
                pts.append(self.point(t, st.line))
                o.used_pos.add(i)
        frm, to = o.raw("from", "start", "p1"), o.raw("to", "end", "p2")
        if frm and to:
            pts = [self.point(frm, st.line), self.point(to, st.line)]
        if o.has("size", "dims", "dimensions"):
            sizes = [self.size(o.raw("size", "dims", "dimensions"), st.line)]
        w = o.num("width", "w", axis="x")
        h = o.num("height", "h", axis="y")
        if len(pts) >= 2 and not sizes:
            (x1, y1), (x2, y2) = pts[0], pts[1]
            x, y, w, h = min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
            explicit = True
        else:
            if sizes:
                w, h = sizes[0]
            if w is None or h is None:
                if st.cmd == "rect" and (w or h):
                    w = w or h
                    h = h or w
                else:
                    raise ScriptError(st.line, "rect needs a size", "write: rect at=100,100 size=200x80  (or rect 100,100 300,180)")
            if pts:
                anchor = (o.raw("anchor") or "topleft").lower()
                ax, ay = BBox(0, 0, w, h).anchor(anchor)
                x, y = pts[0][0] - ax, pts[0][1] - ay
            else:
                x, y, _ = self.place(o, w, h)
        style = self.style(o, closed=True)
        angle = o.num("rotate", "rotation", "angle", default=0.0)
        r = o.num("radius", "corner_radius", "rounded", "r", default=0.0)
        if o.flag("rounded", "round") and not r:
            r = min(12.0, w / 4, h / 4)
        if r and not angle:
            els: list[Element] = [ShapeEl(Polygon(rounded_rect_pts(x, y, w, h, r)), style)]
            kind = "round"
        else:
            els = [ShapeEl(Rect(x, y, w, h, angle), style)]
            kind = "rect"
        els += self._label_in(o, st, x, y, w, h, kind)
        self.emit(st, els, o, shape=(kind, x, y, w, h) if not angle else None)

    def cmd_ellipse(self, st: Stmt, o: Opts, circle: bool = False) -> None:
        words = o.words()
        center = None
        rx = ry = None
        nums = []
        for i, t in words:
            if _SIZE_RE.match(t):
                w, h = self.size(t, st.line)
                rx, ry = w / 2, h / 2
                o.used_pos.add(i)
            elif "," in t and center is None:
                center = self.point(t, st.line)
                o.used_pos.add(i)
            elif "," in t:
                a, b = t.split(",", 1)
                rx, ry = self.length(a, st.line), self.length(b, st.line)
                o.used_pos.add(i)
            elif _LEN_RE.match(t):
                nums.append(self.length(t, st.line))
                o.used_pos.add(i)
            elif _REF_RE.match(t) and t.split(".")[0] in self.ids and center is None:
                center = self.point(t, st.line)
                o.used_pos.add(i)
        if nums and rx is None:
            rx = ry = nums[0]
        rv = o.raw("radius", "r", "radii")
        if rv is not None:
            if "," in rv:
                a, b = rv.split(",", 1)
                rx, ry = self.length(a, st.line), self.length(b, st.line)
            else:
                rx = ry = self.length(rv, st.line)
        if o.has("diameter", "d"):
            dd = o.num("diameter", "d")
            rx = ry = dd / 2
        if o.has("size"):
            w, h = self.size(o.raw("size"), st.line)
            rx, ry = w / 2, h / 2
        if o.has("width", "w") or o.has("height", "h"):
            w = o.num("width", "w")
            h = o.num("height", "h")
            rx = w / 2 if w else rx
            ry = h / 2 if h else ry
            if circle:
                rx = ry = (rx or ry)
        if rx is None:
            raise ScriptError(st.line, f"{st.cmd} needs a radius or size",
                              "write: circle center=200,200 radius=50   or   ellipse center=200,200 radius=80,40")
        if ry is None:
            ry = rx
        if circle:
            ry = rx
        if center is None:
            v = o.raw("center", "centre", "c")
            if v is not None:
                center = self.point(v, st.line)
        if center is None:
            x, y, _ = self.place(o, 2 * rx, 2 * ry)
            center = (x + rx, y + ry)
        style = self.style(o, closed=True)
        angle = o.num("rotate", "rotation", "angle", default=0.0)
        x, y = center[0] - rx, center[1] - ry
        els: list[Element] = [ShapeEl(Ellipse(center[0], center[1], rx, ry, angle), style)]
        els += self._label_in(o, st, x, y, 2 * rx, 2 * ry, "ellipse")
        self.emit(st, els, o, shape=("ellipse", x, y, 2 * rx, 2 * ry))

    def cmd_circle(self, st: Stmt, o: Opts) -> None:
        self.cmd_ellipse(st, o, circle=True)

    def _collect_points(self, st: Stmt, o: Opts) -> list[Point]:
        toks = []
        for i, t in o.words():
            if "," in t or (_REF_RE.match(t) and t.split(".")[0].split("+")[0].split("-")[0] in self.ids):
                toks.append(t)
                o.used_pos.add(i)
        pts = self.points(toks, st.line)
        v = o.raw("points", "pts", "path")
        if v is not None:
            pts += self.points(re.split(r"\s+|;", v.strip()), st.line)
        frm, to = o.raw("from", "start", "p1"), o.raw("to", "end", "p2")
        via = o.raw("via", "through")
        if frm:
            pts.insert(0, self.point(frm, st.line))
        if via:
            pts += self.points(re.split(r"\s+|;", via.strip()), st.line) if not to else []
        if to:
            if via:
                pts += self.points(re.split(r"\s+|;", via.strip()), st.line)
            pts.append(self.point(to, st.line))
        return pts

    def cmd_line(self, st: Stmt, o: Opts, default_end: str = "none") -> None:
        pts = self._collect_points(st, o)
        if len(pts) < 2:
            raise ScriptError(st.line, f"{st.cmd} needs at least two points", f"write: {st.cmd} 100,100 300,100")
        style = self.style(o, closed=False, width_key_is_stroke=True)
        end, start = default_end, "none"
        av = o.raw("arrow", "arrows", "heads")
        if av is not None:
            a = av.lower()
            end = "arrow" if a in ("end", "both", "yes", "on", "true", "forward") else "none"
            start = "arrow" if a in ("start", "both", "back", "backward", "reverse") else "none"
        if o.flag("both", "double", "bidirectional"):
            end = start = "arrow"
        hv = o.raw("head", "end_head", "tip")
        if hv is not None:
            end = normalize_head(hv)
        tv = o.raw("tail", "start_head")
        if tv is not None:
            start = normalize_head(tv)
        curved = o.flag("smooth", "curved", "curve")
        els = connector_elements(pts, style, end=end, start=start, curved=curved, bg=self.doc.page.background)
        if not els:
            raise ScriptError(st.line, f"{st.cmd}: all points are identical")
        if len(pts) > 2 and end == "none" and start == "none" and not curved and o.flag("closed"):
            els = [ShapeEl(Polygon(pts), style)]
        self.emit(st, els, o)

    def cmd_arrow(self, st: Stmt, o: Opts) -> None:
        self.cmd_line(st, o, default_end="arrow")

    def cmd_polygon(self, st: Stmt, o: Opts) -> None:
        pts = self._collect_points(st, o)
        if len(pts) < 3:
            raise ScriptError(st.line, "polygon needs at least three points", "write: polygon 100,100 200,100 150,180")
        style = self.style(o, closed=True)
        bb = BBox(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
        els: list[Element] = [ShapeEl(Polygon(pts), style)]
        els += self._label_in(o, st, bb.x0, bb.y0, bb.w, bb.h)
        self.emit(st, els, o)

    def cmd_curve(self, st: Stmt, o: Opts) -> None:
        pts = self._collect_points(st, o)
        ctrl = o.raw("control", "ctrl", "via", "cp")
        if ctrl is not None and len(pts) == 2:
            pts.insert(1, self.point(ctrl, st.line))
        if len(pts) != 3:
            raise ScriptError(st.line, "curve needs 3 points: start, control, end", "write: curve 100,200 200,100 300,200")
        style = self.style(o, closed=False, width_key_is_stroke=True)
        els: list[Element] = [ShapeEl(QuadBez(pts[0], pts[1], pts[2]), style)]
        els += self._curve_heads(o, style, pts[1], pts[2], pts[1], pts[0])
        self.emit(st, els, o)

    def cmd_bezier(self, st: Stmt, o: Opts) -> None:
        pts = self._collect_points(st, o)
        c1, c2 = o.raw("c1", "control1", "cp1"), o.raw("c2", "control2", "cp2")
        if c1 and c2 and len(pts) == 2:
            pts = [pts[0], self.point(c1, st.line), self.point(c2, st.line), pts[1]]
        if len(pts) != 4:
            raise ScriptError(st.line, "bezier needs 4 points: start, control1, control2, end",
                              "write: bezier 100,200 150,100 250,100 300,200")
        style = self.style(o, closed=False, width_key_is_stroke=True)
        els: list[Element] = [ShapeEl(CubBez(pts[0], pts[1], pts[2], pts[3]), style)]
        els += self._curve_heads(o, style, pts[2], pts[3], pts[1], pts[0])
        self.emit(st, els, o)

    def _curve_heads(self, o: Opts, style: Style, before_end: Point, end: Point, after_start: Point, start: Point) -> list[Element]:
        av = (o.raw("arrow", "arrows") or ("end" if o.flag("arrow") else "none")).lower()
        out: list[Element] = []
        st2 = replace(style, dash="solid")
        for want, a, b in (("end", before_end, end), ("start", after_start, start)):
            if av in (want, "both"):
                dx, dy = b[0] - a[0], b[1] - a[1]
                n = math.hypot(dx, dy) or 1
                out.append(ShapeEl(Arrow((b[0] - dx / n * 6, b[1] - dy / n * 6), b), st2))
        return out

    def cmd_pen(self, st: Stmt, o: Opts, highlighter: bool = False) -> None:
        pts = self._collect_points(st, o)
        if len(pts) < 2:
            raise ScriptError(st.line, f"{st.cmd} needs at least two points")
        default_col = parse_color("#ffe066").with_alpha(0.5) if highlighter else self.d.stroke or self.d.text_color
        col = o.color("color", "stroke", "ink", default=default_col) or default_col
        if highlighter and col.a > 0.6:
            col = col.with_alpha(0.5)
        width = o.num("width", "stroke_width", "thickness", "size", default=16.0 if highlighter else self.d.stroke_width)
        if o.flag("smooth", "curved") and len(pts) > 2:
            dense: list[Point] = []
            for (p1, c1, c2, p2) in catmull_rom_to_bezier(pts):
                for k in range(8):
                    t = k / 8
                    u = 1 - t
                    dense.append((u**3 * p1[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p2[0],
                                  u**3 * p1[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p2[1]))
            dense.append(pts[-1])
            pts = dense
        pressure = o.num("pressure", default=1.0)
        pen = PenEl([(p[0], p[1], pressure) for p in pts], color=col, width=width, highlighter=highlighter,
                    pressure_curve="linear" if o.has("pressure") else "const")
        self.emit(st, [pen], o)

    def cmd_highlight(self, st: Stmt, o: Opts) -> None:
        self.cmd_pen(st, o, highlighter=True)

    # ------------------------------------------------------------------ composites

    def cmd_box(self, st: Stmt, o: Opts) -> None:
        strs = o.strings()
        text = "\n".join(strs) if strs else (o.raw("label", "text", "title") or "")
        kind = normalize_kind(o.raw("shape", "kind", "type") or "rect")
        for i, t in o.words():
            try:
                kind = normalize_kind(t)
                o.used_pos.add(i)
            except ValueError:
                pass
        d = self.d
        spec = self.text_spec(o, color=d.text_color)
        ts = TextSpec(spec["font"], spec["size"], spec["color"], bold=spec["weight"] >= 600, italic=spec["italic"],
                      markup=spec["markup"])
        wrap = o.num("wrap", "max_text_width", default=220.0)
        sz = o.raw("size", "dims")
        w = h = None
        if sz is not None and _SIZE_RE.match(sz):
            w, h = self.size(sz, st.line)
        for i, t in o.words():
            if _SIZE_RE.match(t):
                w, h = self.size(t, st.line)
                o.used_pos.add(i)
        w = o.num("width", "w", default=w, axis="x")
        h = o.num("height", "h", default=h, axis="y")
        pad = o.num("padding", "pad", default=None)
        padx = pad if pad is not None else 14.0
        pady = pad if pad is not None else 9.0
        if text:
            tw, th, _ = measure_label(text, ts, (w - 2 * padx) if w else wrap)
        else:
            tw, th = 0.0, 0.0
        min_w, min_h = (80.0, 40.0) if kind not in ("circle", "doublecircle", "start", "end", "bar", "text") else (0.0, 0.0)
        aw, ah = node_size_for_text(kind, tw, th, padx, pady, min_w=min_w, min_h=min_h)
        if w is None:
            w = aw
        if h is None:
            h = ah
        x, y, _ = self.place(o, w, h)
        style = self.style(o, closed=True)
        if not o.has("stroke", "color", "line_color", "border", "outline", "stroke_color"):
            style = replace(style, stroke=d.box_stroke)
        if not o.has("fill", "background", "bg", "fill_color"):
            style = replace(style, fill=d.box_fill)
        if kind == "text":
            style = replace(style, stroke=None, fill=None)
        els = node_elements(kind, x, y, w, h, style)
        if text:
            cx, cy = node_label_center(kind, x, y, w, h)
            label_w = max(10.0, min(tw, w - 2 * padx) if kind in ("rect", "round", "note", "text") else tw)
            ta = (o.raw("text_align") or "center").lower()
            if ta in ("left", "start"):
                el = label_at(text, x + padx, cy, ts, label_w, align="start")
            else:
                el = label_at(text, cx, cy, ts, label_w)
            els.append(el)
        self.emit(st, els, o, shape=(kind, x, y, w, h))

    def cmd_connect(self, st: Stmt, o: Opts) -> None:
        ops = {"->": ("none", "arrow", "solid"), "-->": ("none", "arrow", "dashed_narrow"),
               "<-": ("arrow", "none", "solid"), "<--": ("arrow", "none", "dashed_narrow"),
               "<->": ("arrow", "arrow", "solid"), "<-->": ("arrow", "arrow", "dashed_narrow"),
               "--": ("none", "none", "solid"), "---": ("none", "none", "solid"),
               "..": ("none", "none", "dashed_narrow"), "..>": ("none", "arrow", "dashed_narrow"),
               "-.->": ("none", "arrow", "dashed_narrow"), "=>": ("none", "arrow", "thick"),
               "==>": ("none", "arrow", "thick"), "<=>": ("arrow", "arrow", "thick")}
        # options and flags first, so that flag words are not mistaken for endpoints
        labels = o.strings()
        label = labels[0] if labels else o.raw("label", "text")
        explicit_dash = o.has("dash", "line_style") or any(
            not t.quoted and t.value.lower() in ("dashed", "dotted", "solid") for t in st.pos)
        style = self.style(o, closed=False, width_key_is_stroke=True)
        route = (o.raw("route", "routing", "path_style") or "straight").lower()
        if o.flag("elbow", "orthogonal", "ortho", "angled"):
            route = "elbow"
        if o.flag("curved", "curve", "smooth"):
            route = "curve"
        both = o.flag("both", "bidirectional")
        ends: list[str] = []
        start_head, end_head, dash = "none", "arrow", None
        for i, t in o.words():
            if t in ops:
                start_head, end_head, dash = ops[t]
            else:
                ends.append(t)
            o.used_pos.add(i)
        if o.has("from"):
            ends.insert(0, o.raw("from"))
        if o.has("to"):
            ends.append(o.raw("to"))
        if len(ends) != 2:
            raise ScriptError(st.line, f"connect needs two endpoints, got {len(ends)}: {' '.join(ends) or '(none)'}",
                              "write: connect a -> b \"label\"   (a and b are ids or x,y points)")
        if dash == "thick":
            style = replace(style, width=style.width * 2)
        elif dash and not explicit_dash:
            style = replace(style, dash=dash)
        style = replace(style, fill=None)
        if both:
            start_head = end_head = "arrow"
        av = o.raw("arrow", "arrows")
        if av is not None:
            a = av.lower()
            end_head = "arrow" if a in ("end", "both", "yes", "on", "true") else "none"
            start_head = "arrow" if a in ("start", "both") else "none"
        if o.has("head", "end_head", "tip"):
            end_head = normalize_head(o.raw("head", "end_head", "tip"))
        if o.has("tail", "start_head"):
            start_head = normalize_head(o.raw("tail", "start_head"))
        a_pt, a_ent = self._endpoint(ends[0], st.line)
        b_pt, b_ent = self._endpoint(ends[1], st.line)
        fs = (o.raw("from_side", "exit", "start_side") or "").lower() or None
        ts_ = (o.raw("to_side", "entry", "end_side") or "").lower() or None
        if route in ("straight", "direct", "line"):
            p0, p1 = a_pt, b_pt
            if fs and a_ent:
                p0 = a_ent.bbox().anchor(fs)
            if ts_ and b_ent:
                p1 = b_ent.bbox().anchor(ts_)
            if a_ent and not fs:
                p0 = clip_to_outline(a_ent.outline(), a_pt, p1)
            if b_ent and not ts_:
                p1 = clip_to_outline(b_ent.outline(), b_pt, p0)
            pts = [p0, p1]
            curved = False
        elif route in ("elbow", "orthogonal", "ortho", "manhattan", "step"):
            pts = self._elbow(a_pt, a_ent, b_pt, b_ent, fs, ts_)
            curved = False
        elif route in ("curve", "curved", "bezier", "smooth"):
            pts = self._elbow(a_pt, a_ent, b_pt, b_ent, fs, ts_, curve=True)
            curved = True
        else:
            raise ScriptError(st.line, f"unknown route {route!r}", "use straight, elbow or curve")
        els: list[Element]
        if curved:
            p0, c1, c2, p1 = pts
            els = [ShapeEl(CubBez(p0, c1, c2, p1), style)]
            for want, a, b in ((end_head, c2, p1), (start_head, c1, p0)):
                if want == "arrow":
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    n = math.hypot(dx, dy) or 1
                    els.append(ShapeEl(Arrow((b[0] - dx / n * 6, b[1] - dy / n * 6), b), replace(style, dash="solid")))
                elif want != "none":
                    from .diagram import head_elements
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    n = math.hypot(dx, dy) or 1
                    els += head_elements(want, b, (dx / n, dy / n), style, self.doc.page.background)[0]
            mid = _cubic_at(p0, c1, c2, p1, float(o.raw("label_pos", default="0.5")))
        else:
            els = connector_elements(pts, style, end=end_head, start=start_head, bg=self.doc.page.background)
            mid = path_midpoint(pts)[0]
        if label:
            spec = self.text_spec(o, size=self.d.size * 0.9)
            ts = TextSpec(spec["font"], spec["size"], spec["color"], bold=spec["weight"] >= 600, markup=spec["markup"])
            bg = None if (o.raw("label_bg") or "").lower() in ("none", "off", "no") else self.doc.page.background
            els += label_box_elements(label, mid[0], mid[1], ts, bg, 240.0)
        self.emit(st, els, o)

    def _endpoint(self, tok: str, line: int) -> tuple[Point, Entry | None]:
        t = tok.strip()
        if _ID_RE.match(t):
            if t in self.ids:
                ent = self.ids[t]
                b = ent.bbox()
                return (b.cx, b.cy), ent
            near = difflib.get_close_matches(t, list(self.ids), n=1)
            raise ScriptError(line, f"unknown id {t!r}",
                              f"did you mean '{near[0]}'?" if near else "give the element an id first, e.g. box \"A\" id=a")
        return self.point(t, line), None

    def _elbow(self, a: Point, ae: Entry | None, b: Point, be: Entry | None, fs: str | None, ts: str | None,
               curve: bool = False) -> list[Point]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        vertical = abs(dy) >= abs(dx)
        if fs is None:
            fs = ("bottom" if dy > 0 else "top") if vertical else ("right" if dx > 0 else "left")
        if ts is None:
            ts = ("top" if dy > 0 else "bottom") if vertical else ("left" if dx > 0 else "right")
        p0 = ae.bbox().anchor(fs) if ae else a
        p1 = be.bbox().anchor(ts) if be else b
        hv0 = fs in ("left", "right")
        hv1 = ts in ("left", "right")
        normals = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}
        if curve:
            dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            k = max(30.0, dist / 2.5)
            n0, n1 = normals.get(fs, (0, 0)), normals.get(ts, (0, 0))
            return [p0, (p0[0] + n0[0] * k, p0[1] + n0[1] * k), (p1[0] + n1[0] * k, p1[1] + n1[1] * k), p1]
        if not hv0 and not hv1:
            if abs(p0[0] - p1[0]) < 1:
                return [p0, p1]
            my = (p0[1] + p1[1]) / 2
            return [p0, (p0[0], my), (p1[0], my), p1]
        if hv0 and hv1:
            if abs(p0[1] - p1[1]) < 1:
                return [p0, p1]
            mx = (p0[0] + p1[0]) / 2
            return [p0, (mx, p0[1]), (mx, p1[1]), p1]
        if hv0:
            return [p0, (p1[0], p0[1]), p1]
        return [p0, (p0[0], p1[1]), p1]

    def cmd_group(self, st: Stmt, o: Opts) -> None:
        explicit_at = o.raw("at", "origin", "pos")
        width = o.num("width", "w", axis="x")
        parent = self.ctx
        out: list[Element] = []
        if explicit_at is not None:
            ox, oy = self.point(explicit_at, st.line)
            flow = False
        else:
            ox, oy = 0.0, 0.0
            flow = True
        page_w = self.doc.page.width
        col_w = width or (parent.col_w if flow else max(100.0, page_w - self.doc.page.margin - ox))
        ctx = Ctx(out, ox, oy, ox, col_w, oy, False)
        self.ctx_stack.append(parent)
        self.ctx = ctx
        try:
            for child in st.children or []:
                self._exec(child)
        finally:
            self.ctx = self.ctx_stack.pop()
        if not out:
            self.warn(st.line, "group is empty")
            return
        if flow:
            bb = union_all([e.bbox() for e in out])
            x, y, _ = self.place(o, bb.x1 - min(0.0, bb.x0), bb.y1 - min(0.0, bb.y0))
            for e in out:
                e.translate(x - min(0.0, bb.x0), y - min(0.0, bb.y0))
        else:
            o.raw("align")
        for e in out:
            self.src_line.setdefault(id(e), st.line)
        self.emit(st, out, o)

    # ------------------------------------------------------------------ blocks

    def cmd_mermaid(self, st: Stmt, o: Opts) -> None:
        from .mermaid import MermaidError, render_mermaid
        from .mermaid.common import make_options

        src = "\n".join(st.body or [])
        if not src.strip():
            raise ScriptError(st.line, "mermaid block is empty")
        try:
            opts = make_options(theme=o.raw("theme") or self.d.theme, font=o.raw("font") or self.d.font,
                                font_size=o.num("font_size", "size", "text_size"),
                                rough=o.flag("rough", "sketch") or (self.d.rough and not o.flag_off("rough")),
                                wrap=o.num("wrap", "max_label_width"), curved=o.flag("curved", "smooth", "curve"),
                                background=self.doc.page.background,
                                native_arrows=not o.flag("triangle_arrows", "filled_arrows"))
        except ValueError as e:
            raise ScriptError(st.line, str(e))
        dv = o.raw("direction", "dir")
        if dv:
            opts.direction = dv.upper().replace("TD", "TB")
        try:
            dg = render_mermaid(src, opts)
        except MermaidError as e:
            ln = st.body_line + (e.line - 1 if e.line else 0)
            raise ScriptError(ln, f"mermaid: {e}")
        w, h = max(dg.bbox.w, 1.0), max(dg.bbox.h, 1.0)
        scale = o.num("scale", "zoom")
        tw, th = o.num("width", "w", axis="x"), o.num("height", "h", axis="y")
        if scale is None:
            if tw and th:
                scale = min(tw / w, th / h)
            elif tw:
                scale = tw / w
            elif th:
                scale = th / h
            else:
                scale = 1.0
        explicit = o.has("at", "center", "below", "above", "right_of", "left_of", "x", "y", "pos", "centre")
        max_w = o.num("max_width", "fit_width", axis="x")
        if max_w is None and not explicit and not tw and not th:
            max_w = self.ctx.col_w
        if max_w and w * scale > max_w:
            scale = max_w / w
        if self.ctx.paged and not th:
            room = self.doc.page.height - 2 * self.doc.page.margin
            if h * scale > room:
                scale = room / h
                self.warn(st.line, f"diagram scaled to {scale:.0%} to fit on one page")
            left = self.page_room() - self.d.gap
            if not explicit and not o.has("scale", "zoom") and left < h * scale <= left / 0.75 and not self.at_page_top():
                scale = left / h  # shrink a little rather than leave a big gap
                self.warn(st.line, f"diagram shrunk to {scale:.0%} to fit the rest of the page (set height= or scale= to control)")
        if scale < 0.6:
            self.warn(st.line, f"diagram drawn at {scale:.0%} of its natural size; text may be small "
                               "(try direction=TB, fewer/shorter labels, or a landscape page)")
        x, y, _ = self.place(o, w * scale, h * scale, align="center")
        for e in dg.elements:
            e.transform(scale, x, y)
        self.emit(st, dg.elements, o)

    def cmd_markdown(self, st: Stmt, o: Opts) -> None:
        text = "\n".join(st.body or [])
        ops = parse_markdown(text)
        explicit = o.raw("at", "pos", "position")
        width = o.num("width", "w", axis="x")
        if explicit is not None or width is not None:
            parent = self.ctx
            if explicit is not None:
                ox, oy = self.point(explicit, st.line)
                paged = False
            else:
                ox, oy = parent.col_x, parent.cursor
                paged = parent.paged
            col_w = width or max(100.0, self.doc.page.width - self.doc.page.margin - ox)
            ctx = Ctx(parent.out if not explicit else [], ox, oy, ox, col_w, oy, paged)
            self.ctx_stack.append(parent)
            self.ctx = ctx
            try:
                self._run_ops(st, ops, o)
            finally:
                self.ctx = self.ctx_stack.pop()
            if explicit is not None:
                for e in ctx.out:
                    self.src_line.setdefault(id(e), st.line)
                self.emit(st, ctx.out, o)
            else:
                parent.cursor = max(parent.cursor, ctx.cursor)
                o.raw("id")
            return
        self._run_ops(st, ops, o)
        o.raw("id")

    def _run_ops(self, st: Stmt, ops: list[Op], o: Opts | None) -> None:
        base = st.body_line or st.line
        for k, op in enumerate(ops):
            sub = Stmt(op.kind, base + max(0, op.line - 1))
            so = Opts(sub, self)
            try:
                if op.kind == "heading":
                    nxt = ops[k + 1] if k + 1 < len(ops) else None
                    self._heading(sub, so, op.level, op.text, keep=self._first_part_height(nxt))
                else:
                    self._op(sub, so, op)
            except ScriptError as e:
                self.errors.append(e)

    def _first_part_height(self, op: Op | None) -> float:
        """How much of the next block should stay on the same page as a heading."""
        lh = self.d.size * LINE_HEIGHT
        page = self.doc.page
        cap = (page.height - 2 * page.margin) * 0.45
        if op is None:
            return 0.0
        if op.kind == "code":
            return min(cap, (op.text.count("\n") + 1) * lh * 0.85 + 20)
        if op.kind == "table":
            return min(cap, len(op.rows) * (lh + 12))
        if op.kind == "mermaid":
            return cap * 0.6
        if op.kind == "image":
            return cap * 0.5
        return lh * 3

    def _op(self, st: Stmt, o: Opts, op: Op) -> None:
        d = self.d
        if op.kind == "heading":
            self._heading(st, o, op.level, op.text)
        elif op.kind == "para":
            spec = self.text_spec(o)
            c = self.ctx
            el = self.make_text(op.text, c.col_x, 0, spec, c.col_w)
            _, h, _ = el.measure()
            el.y = self.flow_y(h)
            self.emit(st, [el], None)
        elif op.kind in ("bullet", "todo"):
            spec = self.text_spec(o)
            if op.kind == "todo":
                marker = "☑" if op.done else "☐"
                if op.done:
                    spec["color"] = spec["color"].mix(Color(1, 1, 1), 0.45)
            else:
                marker = op.marker or BULLETS[(op.level - 1) % len(BULLETS)]
            self._list_item(st, None, op.text, op.level, marker, spec)
        elif op.kind == "quote":
            spec = self.text_spec(o, italic=True, color=d.text_color.mix(Color(1, 1, 1), 0.3))
            self._quote(st, None, op.text, spec)
        elif op.kind == "hr":
            self.cmd_hr(st, None)
        elif op.kind == "pagebreak":
            self.cmd_newpage(st, o)
        elif op.kind == "code":
            self._code(st, None, op.text, op.lang)
        elif op.kind == "table":
            self._table(st, None, op.rows, op.aligns)
        elif op.kind == "mermaid":
            sub = Stmt("mermaid", st.line, body=op.text.split("\n"), body_line=st.line + 1)
            self.cmd_mermaid(sub, Opts(sub, self))
        elif op.kind == "image":
            sub = Stmt("image", st.line)
            sub.kw["file"] = Token(op.text, True)
            self.cmd_image(sub, Opts(sub, self))

    def cmd_code(self, st: Stmt, o: Opts) -> None:
        text = "\n".join(st.body or [])
        self._code(st, o, text, (o.raw("lang", "language") or ""))

    def _code(self, st: Stmt, o: Opts | None, text: str, lang: str) -> None:
        c = self.ctx
        size = (o.num("size", "font_size", default=None) if o else None) or self.d.size * 0.85
        pad = 10.0
        lines = text.rstrip("\n").split("\n") or [""]
        lh = size * LINE_HEIGHT
        fill = parse_color("#f4f5f7")
        stroke = parse_color("#d9dce3")
        col = parse_color("#24292f")
        i = 0
        self._keep_together(len(lines) * lh + 2 * pad)
        while i < len(lines):
            room = self.page_room() - 2 * pad
            if room < lh * 3 and not self.at_page_top():
                self.break_page()
                room = self.page_room() - 2 * pad
            n = max(1, min(len(lines) - i, int(room // lh))) if room != float("inf") else len(lines) - i
            chunk = "\n".join(lines[i:i + n])
            el = TextEl(chunk, 0, 0, font="monospace", size=size, color=col, max_width=c.col_w - 2 * pad)
            _, th, _ = el.measure()
            h = th + 2 * pad
            y = self.flow_y(h)
            box = ShapeEl(Polygon(rounded_rect_pts(c.col_x, y, c.col_w, h, 6)), Style(stroke=stroke, fill=fill, width=1.0))
            el.x, el.y = c.col_x + pad, y + pad
            self.emit(st, [box, el], o)
            i += n

    def cmd_table(self, st: Stmt, o: Opts) -> None:
        rows_txt = [l for l in (st.body or []) if l.strip()]
        from .markdown import _table

        op = _table(rows_txt, st.line)
        header = not o.flag_off("header", "headers")
        self._table(st, o, op.rows, op.aligns, header)

    def _table(self, st: Stmt, o: Opts | None, rows: list[list[str]], aligns: list[str], header: bool = True) -> None:
        if not rows:
            return
        c = self.ctx
        size = (o.num("size", "font_size", default=None) if o else None) or self.d.size * 0.95
        spec = {"font": self.d.font, "size": size, "color": self.d.text_color, "weight": 400, "italic": False,
                "markup": True}
        hspec = dict(spec, weight=700)
        pad = 8.0
        ncol = len(rows[0])
        nat = [0.0] * ncol
        for r, row in enumerate(rows):
            for k, cell in enumerate(row):
                el = self.make_text(cell, 0, 0, hspec if (header and r == 0) else spec, None)
                nat[k] = max(nat[k], el.measure()[0] + 2 * pad)
        avail = (o.num("width", "w", default=None) if o else None) or c.col_w
        widths = list(nat)
        if sum(widths) > avail:
            # shrink the widest columns first, keep a sensible minimum
            minw = 60.0
            while sum(widths) > avail + 0.5:
                k = max(range(ncol), key=lambda j: widths[j])
                excess = sum(widths) - avail
                others = sorted(widths, reverse=True)
                target = others[1] if len(others) > 1 and others[1] < widths[k] else widths[k] - excess
                widths[k] = max(minw, max(target, widths[k] - excess))
                if all(w <= minw + 0.01 for w in widths):
                    break
        total_w = sum(widths)
        border = parse_color("#b8bfcc")
        head_fill = parse_color("#eef1f6")
        cells_by_row = []
        heights = []
        for r, row in enumerate(rows):
            els = []
            hmax = size * LINE_HEIGHT
            for k, cell in enumerate(row):
                el = self.make_text(cell, 0, 0, hspec if (header and r == 0) else spec, widths[k] - 2 * pad)
                w_, h_, _ = el.measure()
                els.append((el, w_))
                hmax = max(hmax, h_)
            cells_by_row.append(els)
            heights.append(hmax + 2 * pad - 4)
        x0 = c.col_x
        self._keep_together(sum(heights))
        r = 0
        while r < len(rows):
            # rows that fit on this page (header repeats on continuation pages)
            room = self.page_room()
            if room != float("inf") and room < heights[r] + (heights[0] if header and r > 0 else 0) and not self.at_page_top():
                self.break_page()
                room = self.page_room()
            chunk = []
            used = 0.0
            if header and r > 0:
                chunk.append(0)
                used += heights[0]
            while r < len(rows) and (not chunk or used + heights[r] <= room or room == float("inf")):
                chunk.append(r)
                used += heights[r]
                r += 1
                if room == float("inf"):
                    continue
            y0 = self.flow_y(used)
            els: list[Element] = []
            yy = y0
            for ri in chunk:
                if header and ri == 0:
                    els.append(ShapeEl(Rect(x0, yy, total_w, heights[0]), Style(stroke=None, fill=head_fill, width=0)))
                xx = x0
                for k, (el, w_) in enumerate(cells_by_row[ri]):
                    al = aligns[k] if k < len(aligns) else "left"
                    el.y = yy + pad - 2
                    inner = widths[k] - 2 * pad
                    if al == "center":
                        el.x = xx + pad + (inner - w_ * center_factor(spec["font"])) / 2
                        el.align = "center"
                    elif al == "right":
                        el.x = xx + pad + inner - w_ * center_factor(spec["font"])
                        el.align = "end"
                    else:
                        el.x = xx + pad
                    xx += widths[k]
                    els.append(el)
                yy += heights[ri]
            grid = Style(stroke=border, width=1.0)
            els.insert(0 if not header else 1, ShapeEl(Rect(x0, y0, total_w, yy - y0), Style(stroke=border, width=1.2)))
            ly = y0
            for ri in chunk[:-1]:
                ly += heights[ri]
                els.append(ShapeEl(Line((x0, ly), (x0 + total_w, ly)), grid))
            lx = x0
            for k in range(ncol - 1):
                lx += widths[k]
                els.append(ShapeEl(Line((lx, y0), (lx, yy)), grid))
            self.emit(st, els, o if r >= len(rows) else None)

    def cmd_svg(self, st: Stmt, o: Opts) -> None:
        path = o.raw("file", "path", "src")
        strs = o.strings()
        if path is None and strs:
            path = strs[0]
        if path is not None:
            full = os.path.join(self.base_dir, path)
            try:
                with open(full, encoding="utf-8") as f:
                    svg = f.read()
            except OSError as e:
                raise ScriptError(st.line, f"cannot read svg file {path!r}: {e.strerror}")
        else:
            svg = "\n".join(st.body or [])
        iw, ih = svg_intrinsic_size(svg)
        if iw is None:
            raise ScriptError(st.line, "svg has no width/height or viewBox", "add width=\"...\" height=\"...\" to the <svg> tag")
        self._place_image(st, o, iw, ih, lambda x, y, w, h: VectorImageEl(svg, x, y, w, h, (iw, ih)))

    def cmd_image(self, st: Stmt, o: Opts) -> None:
        path = o.raw("file", "path", "src")
        strs = o.strings()
        if path is None and strs:
            path = strs[0]
        if path is None:
            raise ScriptError(st.line, "image needs a file", 'write: image "photo.png" width=300')
        full = os.path.join(self.base_dir, path)
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError as e:
            raise ScriptError(st.line, f"cannot read image {path!r}: {e.strerror}")
        if path.lower().endswith(".svg"):
            svg = data.decode("utf-8", "replace")
            iw, ih = svg_intrinsic_size(svg)
            if iw is None:
                raise ScriptError(st.line, "svg has no width/height or viewBox")
            self._place_image(st, o, iw, ih, lambda x, y, w, h: VectorImageEl(svg, x, y, w, h, (iw, ih)))
            return
        dims, mime = raster_info(data)
        if dims is None:
            raise ScriptError(st.line, f"{path!r} is not a PNG, JPEG, GIF or WebP image")
        pw, ph = dims
        href = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
        premul = premultiplied_rgba(data)

        def make(x, y, w, h):
            if premul is not None:
                return BitmapImageEl(premul[0], premul[1], premul[2], x, y, w, h, preview_href=href)
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                   f'width="{pw}" height="{ph}" viewBox="0 0 {pw} {ph}"><image width="{pw}" height="{ph}" '
                   f'xlink:href="{href}"/></svg>')
            return VectorImageEl(svg, x, y, w, h, (pw, ph))

        self._place_image(st, o, pw, ph, make)

    def _place_image(self, st: Stmt, o: Opts, iw: float, ih: float, make: Callable) -> None:
        w = o.num("width", "w", axis="x")
        h = o.num("height", "h", axis="y")
        if o.has("size"):
            w, h = self.size(o.raw("size"), st.line)
        if w and not h:
            h = ih * w / iw
        elif h and not w:
            w = iw * h / ih
        elif not w and not h:
            w, h = iw, ih
            explicit = o.has("at", "center", "below", "above", "right_of", "left_of", "x", "y")
            if not explicit and w > self.ctx.col_w:
                w, h = self.ctx.col_w, ih * self.ctx.col_w / iw
        x, y, _ = self.place(o, w, h, align="center")
        self.emit(st, [make(x, y, w, h)], o)

    # ------------------------------------------------------------------ checks

    def _checks(self) -> None:
        page = self.doc.page
        els = self.doc.elements
        if not els:
            self.warnings.append("the document is empty")
            return
        paged = page.layout in ("fixed_size", "continuous_vertical")
        reported = 0
        for e in els:
            bb = e.bbox()
            ln = self.src_line.get(id(e), 0)
            if paged and (bb.x1 > page.width + 1 or bb.x0 < -1):
                self.warnings.append(f"line {ln}: content extends past the {'right' if bb.x1 > page.width + 1 else 'left'} "
                                     f"page edge (x {bb.x0:.0f}..{bb.x1:.0f}, page width {page.width:.0f})")
                reported += 1
            if bb.y0 < -1:
                self.warnings.append(f"line {ln}: content above the top of the first page (y={bb.y0:.0f})")
                reported += 1
            if page.layout == "fixed_size" and math.floor((bb.y0 + 0.5) / page.height) != math.floor((bb.y1 - 0.5) / page.height) \
                    and bb.h < page.height:
                self.warnings.append(f"line {ln}: element crosses a page boundary (y {bb.y0:.0f}..{bb.y1:.0f})")
                reported += 1
            if reported > 12:
                break
        shapes = self.shape_entries
        n_ov = 0
        for i in range(len(shapes)):
            a = shapes[i].bbox()
            for j in range(i + 1, len(shapes)):
                b = shapes[j].bbox()
                if not a.intersects(b, tol=1.0):
                    continue
                contains = (a.x0 <= b.x0 and a.y0 <= b.y0 and a.x1 >= b.x1 and a.y1 >= b.y1) or \
                           (b.x0 <= a.x0 and b.y0 <= a.y0 and b.x1 >= a.x1 and b.y1 >= a.y1)
                if contains:
                    continue
                ix = min(a.x1, b.x1) - max(a.x0, b.x0)
                iy = min(a.y1, b.y1) - max(a.y0, b.y0)
                if ix * iy > 0.05 * min(a.w * a.h, b.w * b.h):
                    self.warnings.append(f"lines {shapes[i].line} and {shapes[j].line}: shapes overlap "
                                         f"({ix:.0f}x{iy:.0f}px)")
                    n_ov += 1
                    if n_ov >= 8:
                        break
            if n_ov >= 8:
                break
        texts = [e for e in els if isinstance(e, TextEl)]
        n = 0
        for i in range(len(texts)):
            bi = texts[i].bbox()
            for j in range(i + 1, len(texts)):
                if self.src_line.get(id(texts[i])) == self.src_line.get(id(texts[j])):
                    continue
                bj = texts[j].bbox()
                if bi.intersects(bj):
                    ix = min(bi.x1, bj.x1) - max(bi.x0, bj.x0)
                    iy = min(bi.y1, bj.y1) - max(bi.y0, bj.y0)
                    small = min(bi.w * bi.h, bj.w * bj.h) or 1
                    if ix * iy > 0.25 * small:
                        self.warnings.append(f"lines {self.src_line.get(id(texts[i]))} and {self.src_line.get(id(texts[j]))}: "
                                             f"text overlaps other text ({texts[i].text[:24]!r} / {texts[j].text[:24]!r})")
                        n += 1
                        if n >= 8:
                            return

    def id_report(self) -> list[str]:
        out = []
        for k, ent in self.ids.items():
            b = ent.bbox()
            out.append(f"{k}: x={b.x0:.0f} y={b.y0:.0f} w={b.w:.0f} h={b.h:.0f}")
        return out


def _cubic_at(p0, c1, c2, p1, t):
    u = 1 - t
    return (u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p1[0],
            u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p1[1])


# ---------------------------------------------------------------------- image helpers


def svg_intrinsic_size(svg: str) -> tuple[float | None, float | None]:
    m = re.search(r"<svg\b[^>]*>", svg, re.S)
    if not m:
        return None, None
    tag = m.group(0)

    def attr(name):
        a = re.search(rf'\b{name}\s*=\s*["\']([^"\']+)["\']', tag)
        return a.group(1) if a else None

    def plen(v):
        if v is None:
            return None
        mm = re.match(r"^\s*([\d.]+)\s*(px|pt|mm|cm|in)?\s*$", v)
        if not mm:
            return None
        return float(mm.group(1)) * UNITS[(mm.group(2) or "px")]

    w, h = plen(attr("width")), plen(attr("height"))
    vb = attr("viewBox")
    if vb:
        parts = [float(x) for x in re.split(r"[\s,]+", vb.strip()) if x]
        if len(parts) == 4:
            if w is None and h is None:
                w, h = parts[2], parts[3]
            elif w is None:
                w = h * parts[2] / parts[3]
            elif h is None:
                h = w * parts[3] / parts[2]
    return w, h


def raster_info(data: bytes) -> tuple[tuple[int, int] | None, str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return (w, h), "image/png"
    if data[:3] == b"GIF":
        w, h = struct.unpack("<HH", data[6:10])
        return (w, h), "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        try:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(data))
            return im.size, "image/webp"
        except Exception:
            return None, ""
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return (w, h), "image/jpeg"
            seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + seg
        return None, ""
    return None, ""


def premultiplied_rgba(data: bytes):
    """(base64 RGBA premultiplied pixels, width, height) via Pillow, or None if Pillow is missing."""
    try:
        from PIL import Image
        import io
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    w, h = im.size
    if w * h > 4096 * 4096:
        return None
    try:
        premul = im.convert("RGBa").tobytes()
    except ValueError:
        raw = bytearray(im.tobytes())
        for k in range(0, len(raw), 4):
            a = raw[k + 3]
            if a < 255:
                raw[k] = raw[k] * a // 255
                raw[k + 1] = raw[k + 1] * a // 255
                raw[k + 2] = raw[k + 2] * a // 255
        premul = bytes(raw)
    return base64.b64encode(premul).decode("ascii"), w, h
