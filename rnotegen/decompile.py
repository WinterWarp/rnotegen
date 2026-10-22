"""Convert an existing .rnote file back into an rnotegen script.

This lets an LLM read a note, edit the script, and build it again. Shapes,
text and settings round-trip; freehand pen strokes are simplified (or
summarized) because they can contain thousands of points.
"""

from __future__ import annotations

import base64
import math
import os

from .colors import Color
from .rnoteformat import iter_strokes, page_setup_from_snapshot, parse_semver, read_file_json
from .model import PAGE_FORMATS_MM, mm_to_px

_DASH_OUT = {"dotted": "dotted", "dashed_narrow": "dashed_narrow", "dashed_equidistant": "dashed",
             "dashed_wide": "dashed_wide"}
_FILL_OUT = {"hachure": "hachure", "zig_zag": "zigzag", "zig_zag_line": "zigzag_line", "crosshatch": "crosshatch",
             "dots": "dots", "dashed": "dashed"}


def _n(v: float) -> str:
    v = round(float(v), 1)
    return str(int(v)) if v == int(v) else f"{v:g}"


def _p(p) -> str:
    return f"{_n(p[0])},{_n(p[1])}"


def _col(d) -> str | None:
    c = Color.from_json(d)
    return None if c is None else c.to_spec()


def _affine(tr) -> tuple[float, float, float, float, float, float]:
    """(m00, m10, m01, m11, tx, ty) from either the 9-value (<=0.14) or 6-value (0.15) form."""
    a = tr.get("affine") if isinstance(tr, dict) else tr
    if a is None:
        return 1, 0, 0, 1, 0, 0
    if len(a) == 9:
        return a[0], a[1], a[3], a[4], a[6], a[7]
    return a[0], a[1], a[2], a[3], a[4], a[5]


def _transform_of(obj) -> tuple[float, float, float, float, float, float]:
    if "transform" in obj:
        return _affine(obj["transform"])
    if "affine" in obj:
        return _affine(obj["affine"])
    return 1, 0, 0, 1, 0, 0


def _style_opts(style: dict, closed: bool) -> list[str]:
    out = []
    kind = next(iter(style))
    s = style[kind]
    sc = _col(s.get("stroke_color"))
    fc = _col(s.get("fill_color"))
    out.append(f"stroke={sc}" if sc else "stroke=none")
    out.append(f"stroke_width={_n(s.get('stroke_width', 2))}")
    if closed or fc:
        out.append(f"fill={fc}" if fc else "fill=none")
    if kind == "smooth" and s.get("line_style", "solid") != "solid":
        out.append(f"dash={_DASH_OUT.get(s['line_style'], 'dashed')}")
    if kind == "rough":
        out.append("rough")
        fs = s.get("fill_style", "hachure")
        if fc and fs in _FILL_OUT:
            out.append(f"fill_style={_FILL_OUT[fs]}")
    return out


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _rdp(pts, eps):
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0][:2], pts[-1][:2]
    dx, dy = x2 - x1, y2 - y1
    n = math.hypot(dx, dy) or 1e-9
    best, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / n
        if d > best:
            best, idx = d, i
    if best > eps:
        return _rdp(pts[: idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def _text_markup(text: str, ranged: list[dict]) -> str:
    """Re-encode Rnote's ranged attributes as inline markup."""
    b = text.encode("utf-8")
    # map byte offsets -> char offsets
    char_at = {}
    acc = 0
    for i, ch in enumerate(text):
        char_at[acc] = i
        acc += len(ch.encode("utf-8"))
    char_at[acc] = len(text)
    opens: dict[int, list[str]] = {}
    closes: dict[int, list[str]] = {}
    for ra in ranged or []:
        r, attr = ra.get("range", {}), ra.get("attribute", {})
        s, e = char_at.get(r.get("start", 0)), char_at.get(r.get("end", 0))
        if s is None or e is None or e <= s:
            continue
        o = c = None
        if attr.get("font_weight", 400) >= 600:
            o = c = "**"
        elif attr.get("font_style") == "italic":
            o = c = "*"
        elif attr.get("strikethrough"):
            o = c = "~~"
        elif attr.get("underline"):
            o, c = "<u>", "</u>"
        elif "font_family" in attr and "mono" in str(attr["font_family"]).lower():
            o = c = "`"
        elif "text_color" in attr:
            o, c = f"<color={_col(attr['text_color'])}>", "</color>"
        if o:
            opens.setdefault(s, []).append(o)
            closes.setdefault(e, []).insert(0, c)
    del b
    out = []
    for i, ch in enumerate(text):
        out += closes.get(i, [])
        out += opens.get(i, [])
        out.append("\\" + ch if ch in "*_~`<[\\" else ch)
    out += closes.get(len(text), [])
    return "".join(out)


def decompile(path: str, pen: str = "simplify", images: str = "inline", image_dir: str | None = None) -> tuple[str, dict]:
    """Returns (script, summary). pen: full | simplify | skip. images: inline | skip."""
    fj = read_file_json(path)
    ver = str(fj.get("version"))
    snap = fj["data"]["engine_snapshot"]
    if parse_semver(ver) < (0, 13, 0) and "config" not in snap.get("document", {}):
        d = snap["document"]
        d["config"] = {k: d.get(k) for k in ("format", "background", "layout") if k in d}
    page = page_setup_from_snapshot(snap)
    lines = [f"# decompiled from {os.path.basename(path)} (Rnote file format {ver})"]
    size = f"{_n(page.width)}x{_n(page.height)}"
    for name, (w, h) in PAGE_FORMATS_MM.items():
        if name.startswith("us_"):
            continue
        if abs(mm_to_px(w) - page.width) < 1 and abs(mm_to_px(h) - page.height) < 1:
            size = name
        elif abs(mm_to_px(h) - page.width) < 1 and abs(mm_to_px(w) - page.height) < 1:
            size = f"{name} orientation=landscape"
    layout = {"fixed_size": "pages", "continuous_vertical": "continuous"}.get(page.layout, page.layout)
    doc_line = f"document size={size} layout={layout}"
    if page.pattern != "none":
        doc_line += f" pattern={page.pattern} pattern_color={page.pattern_color.to_spec()} pattern_size={_n(page.pattern_size)}"
    if page.background.to_spec() != "#ffffff":
        doc_line += f" background={page.background.to_spec()}"
    lines.append(doc_line)
    counts: dict[str, int] = {}
    texts: list[str] = []
    img_n = 0
    for t, val, layer in iter_strokes(snap):
        kind = next(iter(val))
        s = val[kind]
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "textstroke":
            ts = s.get("text_style", {})
            m00, m10, m01, m11, tx, ty = _transform_of(s)
            pos_key = "at" if ts.get("alignment", "start") == "start" or not ts.get("max_width") else "text_origin"
            parts = [f"text {_quote(_text_markup(s.get('text', ''), ts.get('ranged_text_attributes')))}", f"{pos_key}={_n(tx)},{_n(ty)}"]
            scale = math.hypot(m00, m10)
            parts.append(f"size={_n(ts.get('font_size', 32) * scale)}")
            fam = ts.get("font_family", "serif")
            parts.append(f"font={_quote(fam) if ' ' in fam else fam}")
            c = _col(ts.get("color"))
            if c and c != "#000000":
                parts.append(f"color={c}")
            if ts.get("font_weight", 400) >= 600:
                parts.append("bold")
            elif ts.get("font_weight", 400) not in (400, 500):
                parts.append(f"weight={ts['font_weight']}")
            if ts.get("font_style") == "italic":
                parts.append("italic")
            if ts.get("max_width"):
                parts.append(f"width={_n(ts['max_width'] * scale)}")
            al = ts.get("alignment", "start")
            if al != "start":
                parts.append("align=" + {"center": "center", "end": "right", "fill": "justify"}.get(al, "left"))
            ang = math.degrees(math.atan2(m10, m00))
            if abs(ang) > 0.05:
                parts.append(f"rotate={_n(ang)}")
            lines.append(" ".join(parts))
            texts.append(s.get("text", ""))
        elif kind == "shapestroke":
            shape = s["shape"]
            sk = next(iter(shape))
            g = shape[sk]
            style = s.get("style", {"smooth": {}})
            if sk == "line":
                lines.append(f"line {_p(g['start'])} {_p(g['end'])} " + " ".join(_style_opts(style, False)))
            elif sk == "arrow":
                lines.append(f"arrow {_p(g['start'])} {_p(g['tip'])} " + " ".join(_style_opts(style, False)))
            elif sk == "rect":
                hx, hy = g["cuboid"]["half_extents"]
                m00, m10, m01, m11, tx, ty = _transform_of(g)
                sx, sy = math.hypot(m00, m10), math.hypot(m01, m11)
                w, h = 2 * hx * sx, 2 * hy * sy
                ang = math.degrees(math.atan2(m10, m00))
                rot = f" rotate={_n(ang)}" if abs(ang) > 0.05 else ""
                lines.append(f"rect center={_n(tx)},{_n(ty)} size={_n(w)}x{_n(h)}{rot} " + " ".join(_style_opts(style, True)))
            elif sk == "ellipse":
                rx, ry = g["radii"]
                m00, m10, m01, m11, tx, ty = _transform_of(g)
                sx, sy = math.hypot(m00, m10), math.hypot(m01, m11)
                ang = math.degrees(math.atan2(m10, m00))
                rot = f" rotate={_n(ang)}" if abs(ang) > 0.05 else ""
                lines.append(f"ellipse center={_n(tx)},{_n(ty)} radius={_n(rx * sx)},{_n(ry * sy)}{rot} " + " ".join(_style_opts(style, True)))
            elif sk in ("polyline", "polygon"):
                pts = [g["start"]] + list(g.get("path", []))
                cmd = "line" if sk == "polyline" else "polygon"
                lines.append(f"{cmd} " + " ".join(_p(p) for p in pts) + " " + " ".join(_style_opts(style, sk == "polygon")))
            elif sk == "quadbez":
                lines.append(f"curve {_p(g['start'])} {_p(g['cp'])} {_p(g['end'])} " + " ".join(_style_opts(style, False)))
            elif sk == "cubbez":
                lines.append(f"bezier {_p(g['start'])} {_p(g['cp1'])} {_p(g['cp2'])} {_p(g['end'])} " + " ".join(_style_opts(style, False)))
        elif kind == "brushstroke":
            if pen == "skip":
                continue
            path_ = s.get("path", {})
            pts = [(path_["start"]["pos"][0], path_["start"]["pos"][1])]
            for seg in path_.get("segments", []):
                sk = next(iter(seg))
                pts.append(tuple(seg[sk]["end"]["pos"]))
            if pen == "simplify":
                pts = _rdp(pts, 0.8)
            st = s.get("style", {})
            sk = next(iter(st)) if st else "smooth"
            o = st.get(sk, {}) if st else {}
            col = _col(o.get("stroke_color")) or "#000000"
            hl = layer == "highlighter" or (isinstance(layer, str) and layer.lower() == "highlighter")
            cmd = "highlight" if hl else "pen"
            lines.append(f"{cmd} " + " ".join(_p(p) for p in pts) + f" color={col} width={_n(o.get('stroke_width', 2))}")
        elif kind == "vectorimage":
            if images == "skip":
                lines.append("# (vector image skipped)")
                continue
            rect = s["rectangle"]
            hx, hy = rect["cuboid"]["half_extents"]
            m00, m10, m01, m11, tx, ty = _transform_of(rect)
            w, h = 2 * hx * math.hypot(m00, m10), 2 * hy * math.hypot(m01, m11)
            lines.append(f"svg at={_n(tx - w / 2)},{_n(ty - h / 2)} width={_n(w)} height={_n(h)}")
            lines.append("```")
            lines.append(s.get("svg_data", ""))
            lines.append("```")
        elif kind == "bitmapimage":
            rect = s["rectangle"]
            hx, hy = rect["cuboid"]["half_extents"]
            m00, m10, m01, m11, tx, ty = _transform_of(rect)
            w, h = 2 * hx * math.hypot(m00, m10), 2 * hy * math.hypot(m01, m11)
            saved = None
            if images != "skip" and image_dir:
                saved = _save_bitmap(s.get("image", {}), image_dir, img_n)
                img_n += 1
            if saved:
                lines.append(f"image file={_quote(saved)} at={_n(tx - w / 2)},{_n(ty - h / 2)} width={_n(w)} height={_n(h)}")
            else:
                lines.append(f"# bitmap image at {_n(tx - w / 2)},{_n(ty - h / 2)} size {_n(w)}x{_n(h)} (not exported)")
    summary = {"version": ver, "strokes": counts, "page": {"width": page.width, "height": page.height, "layout": page.layout},
               "text": texts}
    return "\n".join(lines) + "\n", summary


def _save_bitmap(img: dict, out_dir: str, n: int) -> str | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        raw = base64.b64decode(img["data"])
        w, h = int(img["pixel_width"]), int(img["pixel_height"])
        im = Image.frombytes("RGBa", (w, h), raw).convert("RGBA")
        name = f"image_{n + 1}.png"
        os.makedirs(out_dir, exist_ok=True)
        im.save(os.path.join(out_dir, name))
        return name
    except Exception:
        return None
