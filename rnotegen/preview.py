"""SVG / PNG previews of a Document, approximating how Rnote renders it.

The preview uses the same geometry that goes into the .rnote file. Text is
wrapped with the same estimate the layout uses, so the preview shows where
Rnote will put things; exact glyph widths depend on the fonts installed.
"""

from __future__ import annotations

import base64
import html
import math
import os
import shutil
import subprocess
import tempfile

from .colors import Color
from .markup import char_flags
from .model import (
    Arrow, BitmapImageEl, CubBez, Document, Ellipse, Line, PenEl, Polygon, Polyline, QuadBez, Rect, ShapeEl, Style,
    TextEl, VectorImageEl, arrow_tip_lines, union_all,
)
from .textmetrics import LINE_HEIGHT, wrap_ranges

_DASH = {"solid": [], "dotted": [0.0, 0.0], "dashed_narrow": [1.0, 0.618], "dashed_equidistant": [1.0, 1.0],
         "dashed_wide": [1.0, 1.618]}
_FONT_STACK = {"sans-serif": "Inter, 'Adwaita Sans', Cantarell, 'Noto Sans', 'DejaVu Sans', sans-serif",
               "serif": "'Noto Serif', 'DejaVu Serif', serif", "monospace": "'DejaVu Sans Mono', 'Noto Sans Mono', monospace"}


def _c(c: Color | None) -> str:
    return "none" if c is None else c.to_hex()


def _paint(attr: str, c: Color | None) -> str:
    if c is None or c.a <= 0:
        return f'{attr}="none"'
    s = f'{attr}="{c.to_hex()}"'
    if c.a < 0.999:
        s += f' {attr}-opacity="{c.a:.3f}"'
    return s


def _dasharray(style: Style) -> str:
    pat = list(_DASH.get(style.dash, []))
    if not pat:
        return ""
    w = style.width
    e = math.e
    rounded = style.cap == "rounded" or style.dash == "dotted"
    out = []
    for i, v in enumerate(pat):
        if not rounded:
            v *= w * e
        else:
            if style.dash != "dotted":
                v *= w * e
            if i % 2 == 1:
                v += 2 * w
        out.append(max(v, 0.001))
    return ' stroke-dasharray="' + " ".join(f"{v:.2f}" for v in out) + '"'


class _Svg:
    def __init__(self):
        self.defs: dict[str, str] = {}
        self.body: list[str] = []

    def hatch(self, c: Color, kind: str, angle_deg: float) -> str:
        key = f"h{kind}{c.to_hex()[1:]}{int(angle_deg)}"
        if key not in self.defs:
            lines = '<line x1="0" y1="0" x2="0" y2="8" stroke="{0}" stroke-width="1.2" stroke-opacity="{1:.2f}"/>'.format(c.to_hex(), c.a)
            if kind == "crosshatch":
                lines += '<line x1="0" y1="0" x2="8" y2="0" stroke="{0}" stroke-width="1.2" stroke-opacity="{1:.2f}"/>'.format(c.to_hex(), c.a)
            if kind == "dots":
                lines = f'<circle cx="4" cy="4" r="1.3" fill="{c.to_hex()}" fill-opacity="{c.a:.2f}"/>'
            self.defs[key] = (f'<pattern id="{key}" width="8" height="8" patternUnits="userSpaceOnUse" '
                              f'patternTransform="rotate({angle_deg + 90:.1f})">{lines}</pattern>')
        return f"url(#{key})"


def _style_attrs(svg: _Svg, st: Style, closed: bool) -> str:
    parts = []
    if closed and st.fill is not None and st.fill.a > 0:
        if st.rough and st.fill_style not in ("solid",):
            parts.append(f'fill="{svg.hatch(st.fill, st.fill_style, st.hachure_angle)}"')
        else:
            parts.append(_paint("fill", st.fill))
    else:
        parts.append('fill="none"')
    if st.stroke is not None and st.width > 0:
        parts.append(_paint("stroke", st.stroke))
        parts.append(f'stroke-width="{st.width:.2f}"')
        cap = "round" if (st.cap == "rounded" or st.dash == "dotted") else "butt"
        parts.append(f'stroke-linecap="{cap}" stroke-linejoin="round"')
        parts.append(_dasharray(st))
    return " ".join(p for p in parts if p)


def _pts(pts) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)


def element_svg(svg: _Svg, e) -> None:
    b = svg.body
    if isinstance(e, ShapeEl):
        s, st = e.shape, e.style
        if isinstance(s, Rect):
            cx, cy = s.x + s.w / 2, s.y + s.h / 2
            tr = f' transform="rotate({s.angle:.2f} {cx:.2f} {cy:.2f})"' if s.angle else ""
            b.append(f'<rect x="{s.x:.2f}" y="{s.y:.2f}" width="{s.w:.2f}" height="{s.h:.2f}"{tr} {_style_attrs(svg, st, True)}/>')
        elif isinstance(s, Ellipse):
            tr = f' transform="rotate({s.angle:.2f} {s.cx:.2f} {s.cy:.2f})"' if s.angle else ""
            b.append(f'<ellipse cx="{s.cx:.2f}" cy="{s.cy:.2f}" rx="{s.rx:.2f}" ry="{s.ry:.2f}"{tr} {_style_attrs(svg, st, True)}/>')
        elif isinstance(s, Line):
            b.append(f'<line x1="{s.start[0]:.2f}" y1="{s.start[1]:.2f}" x2="{s.end[0]:.2f}" y2="{s.end[1]:.2f}" {_style_attrs(svg, st, False)}/>')
        elif isinstance(s, Arrow):
            l, r = arrow_tip_lines(s.start, s.tip, st.width)
            d = f"M{s.start[0]:.2f},{s.start[1]:.2f} L{s.tip[0]:.2f},{s.tip[1]:.2f} M{l[0]:.2f},{l[1]:.2f} L{s.tip[0]:.2f},{s.tip[1]:.2f} L{r[0]:.2f},{r[1]:.2f}"
            b.append(f'<path d="{d}" {_style_attrs(svg, st, False)}/>')
        elif isinstance(s, Polygon):
            b.append(f'<polygon points="{_pts(s.points)}" {_style_attrs(svg, st, True)}/>')
        elif isinstance(s, Polyline):
            b.append(f'<polyline points="{_pts(s.points)}" {_style_attrs(svg, st, False)}/>')
        elif isinstance(s, QuadBez):
            d = f"M{s.start[0]:.2f},{s.start[1]:.2f} Q{s.cp[0]:.2f},{s.cp[1]:.2f} {s.end[0]:.2f},{s.end[1]:.2f}"
            b.append(f'<path d="{d}" {_style_attrs(svg, st, True)}/>')
        elif isinstance(s, CubBez):
            d = (f"M{s.start[0]:.2f},{s.start[1]:.2f} C{s.cp1[0]:.2f},{s.cp1[1]:.2f} {s.cp2[0]:.2f},{s.cp2[1]:.2f} "
                 f"{s.end[0]:.2f},{s.end[1]:.2f}")
            b.append(f'<path d="{d}" {_style_attrs(svg, st, True)}/>')
    elif isinstance(e, TextEl):
        _text_svg(b, e)
    elif isinstance(e, PenEl):
        pts = [(p[0], p[1]) for p in e.points]
        b.append(f'<polyline points="{_pts(pts)}" fill="none" {_paint("stroke", e.color)} stroke-width="{e.width:.2f}" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')
    elif isinstance(e, VectorImageEl):
        href = "data:image/svg+xml;base64," + base64.b64encode(e.svg.encode("utf-8")).decode("ascii")
        b.append(f'<image x="{e.x:.2f}" y="{e.y:.2f}" width="{e.w:.2f}" height="{e.h:.2f}" preserveAspectRatio="none" href="{href}"/>')
    elif isinstance(e, BitmapImageEl):
        if e.preview_href:
            b.append(f'<image x="{e.x:.2f}" y="{e.y:.2f}" width="{e.w:.2f}" height="{e.h:.2f}" preserveAspectRatio="none" href="{e.preview_href}"/>')


def _text_svg(b: list[str], t: TextEl) -> None:
    bold, mono = char_flags(t.text, t.spans)
    lines = wrap_ranges(t.text, t.font, t.size, t.max_width, t.weight >= 600, bold, mono)
    widest = max((w for _, _, w in lines), default=0.0)
    fam = _FONT_STACK.get(t.font, f"'{t.font}', sans-serif")
    # per-character attribute map from spans
    n = len(t.text)
    attrs: list[dict] = [dict() for _ in range(n)]
    for sp in t.spans:
        for k in range(sp.start, min(sp.end, n)):
            a = attrs[k]
            if sp.bold:
                a["font-weight"] = "700"
            if sp.italic:
                a["font-style"] = "italic"
            if sp.mono:
                a["font-family"] = _FONT_STACK["monospace"]
            if sp.color is not None:
                a["fill"] = sp.color.to_hex()
            if sp.underline or sp.strike:
                a["text-decoration"] = " ".join(x for x in (("underline" if sp.underline else ""), ("line-through" if sp.strike else "")) if x)
            if "size" in sp.attrs:
                a["font-size"] = f"{sp.attrs['size']:.2f}"
    lh = t.size * 1.17
    tr = f' transform="rotate({t.angle:.2f} {t.x:.2f} {t.y:.2f})"' if t.angle else ""
    deco = []
    if t.underline:
        deco.append("underline")
    if t.strike:
        deco.append("line-through")
    base = (f'font-family="{html.escape(fam)}" font-size="{t.size:.2f}" font-weight="{t.weight}" '
            f'{_paint("fill", t.color)}' + (' font-style="italic"' if t.italic else "")
            + (f' text-decoration="{" ".join(deco)}"' if deco else ""))
    out = [f"<text {base}{tr} xml:space=\"preserve\">"]
    for li, (a, bnd, w) in enumerate(lines):
        if t.align == "center":
            dx = (widest - w) / 2
        elif t.align == "end":
            dx = widest - w
        else:
            dx = 0.0
        y = t.y + li * lh + t.size * 0.93
        out.append(f'<tspan x="{t.x + dx:.2f}" y="{y:.2f}">')
        k = a
        while k < bnd:
            j = k
            while j < bnd and attrs[j] == attrs[k]:
                j += 1
            seg = html.escape(t.text[k:j])
            if attrs[k]:
                at = " ".join(f'{key}="{html.escape(v)}"' for key, v in attrs[k].items())
                out.append(f"<tspan {at}>{seg}</tspan>")
            else:
                out.append(seg)
            k = j
        out.append("</tspan>")
    out.append("</text>")
    b.append("".join(out))


def document_svg(doc: Document, show_pages: bool = True, pad: float = 0.0) -> str:
    page = doc.page
    bb = doc.content_bbox()
    paged = page.layout in ("fixed_size", "continuous_vertical")
    if paged:
        n = doc.page_count()
        x0, y0, w, h = 0.0, 0.0, page.width, page.height * n
        if bb is not None:
            # still show content that sticks out of the pages
            x0 = min(0.0, bb.x0 - 10)
            w = max(page.width, bb.x1 + 10) - x0
    else:
        if bb is None:
            x0, y0, w, h = 0.0, 0.0, page.width, page.height
        else:
            x0, y0 = min(0.0, bb.x0 - 40), min(0.0, bb.y0 - 40)
            w, h = max(page.width, bb.x1 + 40) - x0, max(page.height, bb.y1 + 40) - y0
    svg = _Svg()
    bg = []
    bg.append(f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{w:.2f}" height="{h:.2f}" fill="#d8d8d8"/>' if paged else "")
    if paged:
        n = doc.page_count()
        for k in range(n):
            bg.append(f'<rect x="0" y="{k * page.height:.2f}" width="{page.width:.2f}" height="{page.height:.2f}" {_paint("fill", page.background)}/>')
        bg.append(_pattern(page, 0.0, 0.0, page.width, page.height * n))
        if show_pages:
            for k in range(1, n):
                yy = k * page.height
                bg.append(f'<line x1="0" y1="{yy:.2f}" x2="{page.width:.2f}" y2="{yy:.2f}" stroke="#9a9a9a" stroke-width="1" stroke-dasharray="6 4"/>')
    else:
        bg.append(f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{w:.2f}" height="{h:.2f}" {_paint("fill", page.background)}/>')
        bg.append(_pattern(page, x0, y0, w, h))
    # Rnote draws images first, then highlighter strokes, then everything else
    order = {"image": 0, "highlighter": 1, "user": 2}
    for e in sorted(doc.elements, key=lambda e: order.get(e.layer, 2)):
        if isinstance(e, PenEl) and e.highlighter:
            pass
        element_svg(svg, e)
    defs = "<defs>" + "".join(svg.defs.values()) + "</defs>" if svg.defs else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w:.0f}" height="{h:.0f}" viewBox="{x0:.2f} {y0:.2f} {w:.2f} {h:.2f}">'
            + defs + "".join(bg) + "".join(svg.body) + "</svg>")


def _pattern(page, x0, y0, w, h) -> str:
    if page.pattern == "none":
        return ""
    s = page.pattern_size
    c = page.pattern_color
    out = []
    if page.pattern in ("lines", "grid"):
        y = math.ceil(y0 / s) * s
        while y < y0 + h:
            out.append(f"M{x0:.1f},{y:.1f}H{x0 + w:.1f}")
            y += s
        if page.pattern == "grid":
            x = math.ceil(x0 / s) * s
            while x < x0 + w:
                out.append(f"M{x:.1f},{y0:.1f}V{y0 + h:.1f}")
                x += s
        return f'<path d="{"".join(out)}" stroke="{c.to_hex()}" stroke-width="0.6" fill="none"/>'
    if page.pattern in ("dots", "isometric_dots"):
        pid = "rg_dots"
        return (f'<defs><pattern id="{pid}" width="{s:.2f}" height="{s:.2f}" patternUnits="userSpaceOnUse">'
                f'<circle cx="{s / 2:.2f}" cy="{s / 2:.2f}" r="0.9" fill="{c.to_hex()}"/></pattern></defs>'
                f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{w:.2f}" height="{h:.2f}" fill="url(#{pid})"/>')
    return ""


def write_svg(doc: Document, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(document_svg(doc))


def svg_to_png(svg_text: str, path: str, scale: float = 1.0) -> str:
    """Rasterize with rsvg-convert or cairosvg. Returns the tool used."""
    exe = shutil.which("rsvg-convert")
    if exe:
        with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as tf:
            tf.write(svg_text)
            tmp = tf.name
        try:
            subprocess.run([exe, "-z", str(scale), "-o", path, tmp], check=True, capture_output=True)
        finally:
            os.unlink(tmp)
        return "rsvg-convert"
    try:
        import cairosvg  # type: ignore

        cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), write_to=path, scale=scale)
        return "cairosvg"
    except ImportError:
        pass
    raise RuntimeError("PNG previews need `rsvg-convert` (librsvg) or the `cairosvg` Python package; "
                       "use an .svg preview path instead")


def write_png(doc: Document, path: str, scale: float = 1.0) -> str:
    return svg_to_png(document_svg(doc), path, scale)


def rnote_cli_png(rnote_path: str, out_png: str) -> bool:
    """Render with the real Rnote engine (rnote-cli), first page or stacked pages. Returns False if unavailable."""
    exe = shutil.which("rnote-cli")
    if not exe:
        return False
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([exe, "export", "--on-conflict", "overwrite", "doc-pages", "-o", td, "-f", "png", "-s", "page",
                            rnote_path], capture_output=True, text=True)
        if r.returncode != 0:
            return False
        pages = sorted((p for p in os.listdir(td) if p.endswith(".png")), key=lambda s: int(''.join(ch for ch in s if ch.isdigit()) or 0))
        if not pages:
            return False
        if len(pages) == 1:
            shutil.copy(os.path.join(td, pages[0]), out_png)
            return True
        try:
            from PIL import Image
        except ImportError:
            shutil.copy(os.path.join(td, pages[0]), out_png)
            return True
        ims = [Image.open(os.path.join(td, p)) for p in pages]
        W = max(i.width for i in ims)
        H = sum(i.height for i in ims) + 6 * (len(ims) - 1)
        sheet = Image.new("RGB", (W, H), (200, 200, 200))
        y = 0
        for im in ims:
            sheet.paste(im, (0, y))
            y += im.height + 6
        sheet.save(out_png)
        return True


__all__ = ["document_svg", "write_svg", "write_png", "svg_to_png", "rnote_cli_png", "union_all", "LINE_HEIGHT"]
