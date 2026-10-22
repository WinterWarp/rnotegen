"""Mermaid `mindmap` -> native Rnote shapes (balanced left/right tree)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..colors import Color
from ..diagram import label_at, measure_label, node_elements, node_size_for_text
from ..model import CubBez, ShapeEl, Style, union_all
from .common import Diagram, MermaidError, Options, Source, clean_label

SHAPES = [("((", "))", "circle"), ("))", "((", "bang"), (")", "(", "cloud"), ("{{", "}}", "hexagon"),
          ("[", "]", "rect"), ("(", ")", "round")]


@dataclass
class MNode:
    label: str
    kind: str = "default"
    depth: int = 0
    children: list["MNode"] = field(default_factory=list)
    w: float = 0.0
    h: float = 0.0
    x: float = 0.0  # center
    y: float = 0.0
    sub_h: float = 0.0
    color: Color | None = None
    side: int = 1


def _parse_node(text: str) -> tuple[str, str]:
    t = text.strip()
    t = re.sub(r":::\s*[\w\s-]+$", "", t).strip()
    m = re.match(r"^[\w\-]*?(\(\(|\)\)|\)|\{\{|\[|\()(.*)$", t, re.S)
    if m:
        opener = m.group(1)
        rest = m.group(2)
        for op, cl, kind in SHAPES:
            if op == opener and rest.endswith(cl):
                return clean_label(rest[: -len(cl)]), kind
    return clean_label(t), "default"


def parse(src: Source) -> MNode:
    root: MNode | None = None
    stack: list[tuple[int, MNode]] = []
    header = False
    for lineno, raw in src.lines:
        if not raw.strip():
            continue
        if not header:
            if raw.strip().lower() != "mindmap":
                raise MermaidError("expected 'mindmap'", lineno)
            header = True
            continue
        s = raw.strip()
        if s.startswith("::icon"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        label, kind = _parse_node(s)
        node = MNode(label, kind)
        if root is None:
            root = node
            stack = [(indent, node)]
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            raise MermaidError(f"{s!r} is not indented under the root node (a mindmap has exactly one root)", lineno)
        parent = stack[-1][1]
        node.depth = parent.depth + 1
        parent.children.append(node)
        stack.append((indent, node))
    if root is None:
        raise MermaidError("mindmap is empty")
    return root


def render(src: Source, opts: Options) -> Diagram:
    root = parse(src)
    pal = opts.theme.palette or [Color(0.85, 0.9, 1.0)]
    hgap, vgap = 46.0, 14.0

    def size(n: MNode):
        fs = opts.font_size * (1.25 if n.depth == 0 else (1.05 if n.depth == 1 else 0.92))
        spec = opts.text(size=fs, bold=n.depth <= 1)
        tw, th, _ = measure_label(n.label, spec, opts.wrap * (1.2 if n.depth == 0 else 1.0))
        kind = n.kind if n.kind != "default" else ("circle" if n.depth == 0 else "round")
        n.kind = kind
        n.w, n.h = node_size_for_text(kind, tw, th, padx=14 if n.depth else 20, pady=7 if n.depth > 1 else 10)
        n._spec = spec  # type: ignore[attr-defined]
        n._tw = tw  # type: ignore[attr-defined]
        for c in n.children:
            size(c)

    size(root)

    def subtree(n: MNode) -> float:
        if not n.children:
            n.sub_h = n.h
        else:
            n.sub_h = max(n.h, sum(subtree(c) for c in n.children) + vgap * (len(n.children) - 1))
        return n.sub_h

    for c in root.children:
        subtree(c)
    # balance children between right (1) and left (-1), keeping order: first part right, rest left
    total = sum(c.sub_h for c in root.children)
    acc = 0.0
    right, left = [], []
    for c in root.children:
        if acc + c.sub_h / 2 <= total / 2 or not right:
            right.append(c)
            acc += c.sub_h + vgap
        else:
            left.append(c)
    for i, c in enumerate(root.children):
        c.color = pal[i % len(pal)]

    def place(nodes: list[MNode], parent: MNode, side: int):
        tot = sum(n.sub_h for n in nodes) + vgap * (len(nodes) - 1)
        y = parent.y - tot / 2
        for n in nodes:
            n.side = side
            n.y = y + n.sub_h / 2
            n.x = parent.x + side * (parent.w / 2 + hgap + n.w / 2)
            if n.color is None:
                n.color = parent.color
            place(n.children, n, side)
            y += n.sub_h + vgap

    root.x = root.y = 0.0
    place(right, root, 1)
    place(left, root, -1)

    edges, shapes = [], []

    def draw(n: MNode, parent: MNode | None):
        col = n.color or pal[0]
        dark = col.mix(Color(0.15, 0.15, 0.2), 0.55)
        if parent is not None:
            sx = parent.x + n.side * parent.w / 2
            if parent.kind in ("circle", "bang", "cloud"):
                sx = parent.x + n.side * parent.w * 0.45
            ex = n.x - n.side * n.w / 2
            dx = (ex - sx) * 0.5
            width = max(1.6, 4.5 - n.depth)
            edges.append(ShapeEl(CubBez((sx, parent.y), (sx + dx, parent.y), (ex - dx, n.y), (ex, n.y)),
                                 Style(stroke=dark, width=width, rough=opts.rough or opts.theme.rough)))
        fill = col if n.depth <= 1 else col.mix(Color(1, 1, 1), 0.45)
        if n.depth == 0:
            fill = opts.theme.node_fill or pal[0]
        st = Style(stroke=dark if n.depth else opts.theme.node_stroke, fill=fill, width=1.4,
                   rough=opts.rough or opts.theme.rough)
        x, y = n.x - n.w / 2, n.y - n.h / 2
        shapes.extend(node_elements(n.kind, x, y, n.w, n.h, st))
        shapes.append(label_at(n.label, n.x, n.y, n._spec, n._tw))  # type: ignore[attr-defined]
        for c in n.children:
            draw(c, n)

    draw(root, None)
    els = edges + shapes
    return Diagram(els, union_all([e.bbox() for e in els]), "mindmap", src.title)
