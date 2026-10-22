"""Mermaid `pie` -> native Rnote shapes."""

from __future__ import annotations

import math
import re

from ..colors import Color
from ..diagram import PIE_PALETTE, label_at, measure_label
from ..model import Ellipse, Polygon, Rect, ShapeEl, Style, TextEl, union_all
from .common import Diagram, MermaidError, Options, Source, clean_label


def parse(src: Source):
    title = src.title
    show = False
    slices: list[tuple[str, float]] = []
    header = False
    for lineno, raw in src.lines:
        line = raw.strip()
        if not line:
            continue
        if not header:
            m = re.match(r"^pie\b\s*(showData)?\s*(?:title\s+(.*))?$", line, re.I)
            if not m:
                raise MermaidError("expected 'pie'", lineno)
            header = True
            show = bool(m.group(1))
            if m.group(2):
                title = clean_label(m.group(2))
            continue
        if line.lower() == "showdata":
            show = True
            continue
        m = re.match(r"^title\s+(.*)$", line, re.I)
        if m:
            title = clean_label(m.group(1))
            continue
        m = re.match(r'^"([^"]*)"\s*:\s*([-+]?[\d.]+)\s*$', line) or re.match(r"^'([^']*)'\s*:\s*([-+]?[\d.]+)\s*$", line)
        if m:
            v = float(m.group(2))
            if v < 0:
                raise MermaidError(f"pie slice values must be positive, got {v}", lineno)
            slices.append((clean_label(m.group(1)), v))
            continue
        raise MermaidError(f'could not understand {line!r} (expected: "Label" : 42)', lineno)
    if not slices:
        raise MermaidError("pie chart has no slices")
    return title, show, slices


def render(src: Source, opts: Options) -> Diagram:
    title, show, slices = parse(src)
    total = sum(v for _, v in slices) or 1.0
    r = 150.0
    cx, cy = r, r
    top = 0.0
    els = []
    if title:
        spec = opts.text(size=opts.font_size * 1.25, bold=True)
        els.append(label_at(title, cx, spec.size * 0.65, spec, None))
        top = spec.size * 1.3 + 16
        cy += top
    pal = PIE_PALETTE
    a = -math.pi / 2
    edge = Color(1, 1, 1)
    labels = []
    for i, (name, v) in enumerate(slices):
        frac = v / total
        sweep = frac * 2 * math.pi
        col = pal[i % len(pal)]
        st = Style(stroke=edge, fill=col, width=2.0, rough=opts.rough or opts.theme.rough)
        if frac >= 0.9999:
            els.append(ShapeEl(Ellipse(cx, cy, r, r), st))
        elif frac > 0:
            n = max(2, int(math.degrees(sweep) / 3))
            pts = [(cx, cy)] + [(cx + r * math.cos(a + sweep * k / n), cy + r * math.sin(a + sweep * k / n)) for k in range(n + 1)]
            els.append(ShapeEl(Polygon(pts), st))
        if frac >= 0.04:
            mid = a + sweep / 2
            lx, ly = cx + r * 0.66 * math.cos(mid), cy + r * 0.66 * math.sin(mid)
            labels.append(label_at(f"**{frac * 100:.0f}%**", lx, ly, opts.text(Color(1, 1, 1), opts.font_size * 0.9), None))
        a += sweep
    els += labels
    # legend
    lx = cx + r + 40
    spec = opts.text(size=opts.font_size * 0.95)
    row = spec.size * 1.6
    ly = cy - len(slices) * row / 2
    for i, (name, v) in enumerate(slices):
        col = pal[i % len(pal)]
        els.append(ShapeEl(Rect(lx, ly + i * row + 3, 16, 16), Style(stroke=None, fill=col, width=0.0)))
        text = f"{name} [{_fmt(v)}]" if show else name
        els.append(TextEl(text, lx + 26, ly + i * row + 11 - spec.size * 0.62, font=spec.font, size=spec.size,
                          color=spec.color))
    return Diagram(els, union_all([e.bbox() for e in els]), "pie", None)


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:g}"
