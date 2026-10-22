"""Mermaid `erDiagram` -> native Rnote shapes (crow's foot notation)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from ..diagram import label_at, measure_label, node_elements, node_outline
from ..model import Line, Rect, ShapeEl, Style, TextEl
from .common import Diagram, MermaidError, Options, Source, clean_label
from .graphdraw import GEdge, GNode, render_graph

ENT = r"(?:\"[^\"]+\"|[\w\-.À-￿]+)"
REL_RE = re.compile(
    rf"^(?P<a>{ENT})\s*(?P<l>\|o|\|\||\}}o|\}}\||o\||o\{{|\|\{{|\{{o)(?P<line>--|\.\.)"
    rf"(?P<r>o\||\|\||o\{{|\|\{{|\|o|\}}o|\}}\||\{{o)\s*(?P<b>{ENT})\s*:\s*(?P<label>.+)$")
CARD = {"|o": "crow_zero_one", "o|": "crow_zero_one", "||": "crow_one", "}o": "crow_zero_many", "o{": "crow_zero_many",
        "{o": "crow_zero_many", "}|": "crow_one_many", "|{": "crow_one_many"}


@dataclass
class Entity:
    id: str
    label: str
    attrs: list[tuple[str, str, str, str]] = field(default_factory=list)  # type, name, keys, comment


def parse(src: Source):
    ents: dict[str, Entity] = {}
    order: list[str] = []
    rels = []
    direction = "TB"
    header = False
    cur: Entity | None = None

    def ensure(name: str) -> Entity:
        name = name.strip().strip('"')
        m = re.match(r"^([\w\-.]+)\[(.+)\]$", name)
        label = None
        if m:
            name, label = m.group(1), clean_label(m.group(2))
        if name not in ents:
            ents[name] = Entity(name, label or name)
            order.append(name)
        elif label:
            ents[name].label = label
        return ents[name]

    for lineno, raw in src.lines:
        line = raw.strip()
        if not line:
            continue
        if not header:
            if not line.lower().startswith("erdiagram"):
                raise MermaidError("expected 'erDiagram'", lineno)
            header = True
            continue
        if cur is not None:
            if line == "}":
                cur = None
                continue
            m = re.match(r'^(\S+)\s+(\S+)(?:\s+((?:PK|FK|UK)(?:\s*,\s*(?:PK|FK|UK))*))?(?:\s+"([^"]*)")?\s*$', line)
            if not m:
                raise MermaidError(f"bad attribute line {line!r} (expected: type name [PK|FK|UK] [\"comment\"])", lineno)
            cur.attrs.append((m.group(1), m.group(2), (m.group(3) or "").replace(" ", ""), m.group(4) or ""))
            continue
        m = re.match(r"^direction\s+(\w+)$", line, re.I)
        if m:
            direction = m.group(1).upper().replace("TD", "TB")
            continue
        m = re.match(rf"^({ENT}(?:\[[^\]]+\])?)\s*\{{\s*$", line)
        if m:
            cur = ensure(m.group(1))
            continue
        m = REL_RE.match(line)
        if m:
            a, b = ensure(m.group("a")), ensure(m.group("b"))
            rels.append((a.id, b.id, m.group("l"), m.group("r"), m.group("line"), clean_label(m.group("label").strip('"'))))
            continue
        m = re.match(rf"^({ENT}(?:\[[^\]]+\])?)$", line)
        if m:
            ensure(m.group(1))
            continue
        if re.match(r"^(style|classDef|class|accTitle|accDescr|title)\b", line):
            continue
        raise MermaidError(f"could not understand {line!r} in ER diagram", lineno)
    if cur is not None:
        raise MermaidError(f"entity '{cur.id}' is missing its closing '}}'")
    return ents, order, rels, direction


def render(src: Source, opts: Options) -> Diagram:
    ents, order, rels, direction = parse(src)
    nodes = {eid: _entity_node(ents[eid], opts) for eid in order}
    edges = []
    for a, b, l, r, line, label in rels:
        st = opts.edge_style(dash="dashed_narrow" if line == ".." else "solid")
        edges.append(GEdge(a, b, st, label, opts.text(opts.theme.edge_text, opts.font_size * 0.85),
                           start=CARD[l], end=CARD[r]))
    els, bb, _ = render_graph(nodes, edges, list(order), [], opts.direction or direction, opts, nodesep=60, ranksep=70)
    return Diagram(els, bb, "er", src.title)


def _entity_node(e: Entity, opts: Options) -> GNode:
    spec = opts.text()
    name_spec = replace(spec, bold=True, markup=False)
    row_spec = replace(spec, size=spec.size * 0.85, markup=False)
    pad = 8.0
    rh = row_spec.size * 1.45
    cols = [[a[0] for a in e.attrs], [a[1] for a in e.attrs], [a[2] for a in e.attrs], [a[3] for a in e.attrs]]
    cw = [max((measure_label(t, row_spec, None)[0] for t in col if t), default=0.0) for col in cols]
    used = [i for i in range(4) if cw[i] > 0]
    body_w = sum(cw[i] + 2 * pad for i in used)
    nw, _, _ = measure_label(e.label, name_spec, None)
    w = max(body_w, nw + 30, 100.0)
    head_h = name_spec.size * 1.3 + 14
    h = head_h + len(e.attrs) * rh
    style = opts.node_style()

    def draw(x, y, w_, h_):
        out = [ShapeEl(Rect(x, y, w_, head_h), style)]
        out.append(label_at(e.label, x + w_ / 2, y + head_h / 2, name_spec, None))
        if e.attrs:
            body = replace(style, fill=opts.background)
            out.append(ShapeEl(Rect(x, y + head_h, w_, h_ - head_h), body))
            extra = (w_ - body_w) / max(1, len(used))
            for r, a in enumerate(e.attrs):
                ry = y + head_h + r * rh
                if r % 2 == 1 and style.fill is not None:
                    out.append(ShapeEl(Rect(x + 1, ry, w_ - 2, rh), Style(stroke=None, fill=style.fill.with_alpha(0.35), width=0.0)))
                cx = x
                for i in used:
                    out.append(TextEl(a[i], cx + pad, ry + (rh - row_spec.size * 1.2) / 2, font=row_spec.font,
                                      size=row_spec.size, color=row_spec.color, italic=(i == 3), weight=600 if i == 2 else 400))
                    cx += cw[i] + 2 * pad + extra
        return out

    return GNode(e.id, w, h, draw, lambda x, y, w_, h_: node_outline("rect", x, y, w_, h_))
