"""Mermaid -> Rnote converters.

`render_mermaid(source, **options)` returns a Diagram whose elements are
positioned with the top-left corner of the diagram at (0, 0).
"""

from __future__ import annotations

import re

from ..model import BBox, TextEl
from .common import Diagram, MermaidError, Options, make_options, preprocess

SUPPORTED = ["flowchart", "graph", "sequenceDiagram", "stateDiagram-v2", "classDiagram", "erDiagram", "mindmap",
             "pie", "timeline", "gantt"]


def detect_kind(src) -> str:
    for _, line in src.lines:
        s = line.strip()
        if not s:
            continue
        word = re.split(r"[\s;]", s, maxsplit=1)[0]
        return word
    raise MermaidError("empty mermaid diagram")


def render_mermaid(text: str, opts: Options | None = None, **kw) -> Diagram:
    opts = opts or make_options(**kw)
    src = preprocess(text)
    if src.theme and "theme" not in kw:
        try:
            opts = make_options(theme=src.theme, font=opts.font, font_size=opts.font_size, rough=opts.rough,
                                wrap=opts.wrap, curved=opts.curved, background=opts.background,
                                native_arrows=opts.native_arrows)
        except ValueError:
            pass
    kind = detect_kind(src)
    low = kind.lower()
    if low in ("flowchart", "graph", "flowchart-elk"):
        from . import flowchart as mod
    elif low == "sequencediagram":
        from . import sequence as mod
    elif low in ("statediagram", "statediagram-v2"):
        from . import state as mod
    elif low in ("classdiagram", "classdiagram-v2"):
        from . import classdiagram as mod
    elif low == "erdiagram":
        from . import erdiagram as mod
    elif low == "mindmap":
        from . import mindmap as mod
    elif low == "pie":
        from . import pie as mod
    elif low in ("timeline", "gantt"):
        from . import timeline as mod
    else:
        raise MermaidError(f"unsupported mermaid diagram type {kind!r}; supported: {', '.join(SUPPORTED)}")
    d = mod.render(src, opts)
    _normalize(d, opts)
    return d


def _normalize(d: Diagram, opts: Options) -> None:
    """Move the diagram so its bbox starts at (0,0), adding the title on top."""
    bb = d.bbox
    for e in d.elements:
        e.translate(-bb.x0, -bb.y0)
    w, h = bb.w, bb.h
    if d.title and d.kind not in ("pie", "timeline", "gantt"):
        size = opts.font_size * 1.3
        from ..diagram import label_at

        t = label_at(f"**{d.title}**", w / 2, size * 0.65, opts.text(size=size))
        shift = size * 1.3 + 12
        for e in d.elements:
            e.translate(0, shift)
        d.elements.insert(0, t)
        tb = t.bbox()
        h += shift
        w = max(w, tb.x1)
        if tb.x0 < 0:
            for e in d.elements:
                e.translate(-tb.x0, 0)
            w += -tb.x0
    d.bbox = BBox(0, 0, w, h)


__all__ = ["render_mermaid", "Diagram", "MermaidError", "Options", "make_options", "SUPPORTED", "TextEl"]
