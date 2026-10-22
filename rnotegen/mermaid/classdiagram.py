"""Mermaid `classDiagram` -> native Rnote shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from ..diagram import TextSpec, label_at, measure_label, node_elements, node_outline
from ..model import Line, ShapeEl, Style, TextEl
from .common import Diagram, MermaidError, Options, Source, apply_css, clean_label, parse_css_style
from .graphdraw import GCluster, GEdge, GNode, render_graph

NAME = r"[\w\-.À-￿]+(?:~[^~]+~)?"
REL_RE = re.compile(
    rf"^(?P<a>{NAME})\s*(?:\"(?P<ca>[^\"]*)\")?\s*"
    r"(?P<l><\||\*|o|<|\(\))?(?P<line>--|\.\.)(?P<r>\|>|\*|o|>|\(\))?"
    rf"\s*(?:\"(?P<cb>[^\"]*)\")?\s*(?P<b>{NAME})\s*(?::\s*(?P<label>.*))?$")
HEAD = {"<|": "triangle_open", "|>": "triangle_open", "*": "diamond", "o": "diamond_open", "<": "arrow", ">": "arrow",
        "()": "circle_open", None: "none"}


@dataclass
class Cls:
    id: str
    label: str
    annotations: list[str] = field(default_factory=list)
    attrs: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    css: dict = field(default_factory=dict)


def _gen(s: str) -> str:
    return re.sub(r"~([^~]+)~", r"<\1>", s)


def parse(src: Source):
    classes: dict[str, Cls] = {}
    order: list[str] = []
    rels = []
    notes: list[tuple[str | None, str]] = []
    namespaces: list[tuple[str, list[str]]] = []
    class_defs: dict[str, dict] = {}
    direction = "TB"
    header = False
    in_class: Cls | None = None
    in_ns: tuple[str, list[str]] | None = None

    def ensure(name: str, label: str | None = None) -> Cls:
        cid = name.split("~")[0]
        if cid not in classes:
            classes[cid] = Cls(cid, _gen(name))
            order.append(cid)
            if in_ns is not None:
                in_ns[1].append(cid)
        c = classes[cid]
        if "~" in name:
            c.label = _gen(name)
        if label:
            c.label = label
        return c

    def member(c: Cls, text: str):
        t = text.strip()
        if not t:
            return
        if re.match(r"^<<.+>>$", t):
            c.annotations.append(t[2:-2].strip())
        elif "(" in t:
            c.methods.append(_gen(t))
        else:
            c.attrs.append(_gen(t))

    for lineno, raw in src.lines:
        line = raw.strip()
        if not line:
            continue
        if not header:
            if not re.match(r"^classdiagram(-v2)?\b", line, re.I):
                raise MermaidError("expected 'classDiagram'", lineno)
            header = True
            continue
        if in_class is not None:
            if line == "}":
                in_class = None
            else:
                member(in_class, line)
            continue
        m = re.match(r"^direction\s+(\w+)$", line, re.I)
        if m:
            direction = m.group(1).upper().replace("TD", "TB")
            continue
        if line == "}" and in_ns is not None:
            namespaces.append(in_ns)
            in_ns = None
            continue
        m = re.match(r"^namespace\s+([\w.\-]+)\s*\{$", line)
        if m:
            in_ns = (m.group(1), [])
            continue
        m = re.match(rf"^class\s+(?P<n>{NAME})(?:\[\"?(?P<lab>[^\]\"]*)\"?\])?(?::::(?P<cls>\w+))?\s*(?P<open>\{{)?\s*(?P<rest>.*)$", line)
        if m:
            c = ensure(m.group("n"), clean_label(m.group("lab")) if m.group("lab") else None)
            if m.group("cls"):
                c.classes.append(m.group("cls"))
            if m.group("open"):
                rest = m.group("rest").strip()
                if rest.endswith("}"):
                    for part in rest[:-1].split(";"):
                        member(c, part)
                else:
                    in_class = c
            continue
        m = re.match(rf"^<<(.+)>>\s*({NAME})$", line)
        if m:
            ensure(m.group(2)).annotations.append(m.group(1).strip())
            continue
        m = REL_RE.match(line)
        if m:
            a, b = ensure(m.group("a")), ensure(m.group("b"))
            rels.append((a.id, b.id, m.group("l"), m.group("r"), m.group("line"), m.group("ca"), m.group("cb"),
                         clean_label(m.group("label")) if m.group("label") else None))
            continue
        m = re.match(rf"^note\s+for\s+({NAME})\s+\"(.*)\"$", line)
        if m:
            notes.append((ensure(m.group(1)).id, clean_label(m.group(2))))
            continue
        m = re.match(r"^note\s+\"(.*)\"$", line)
        if m:
            notes.append((None, clean_label(m.group(1))))
            continue
        m = re.match(r"^classDef\s+(\w+)\s+(.+)$", line)
        if m:
            class_defs[m.group(1)] = parse_css_style(m.group(2))
            continue
        m = re.match(r"^(?:cssClass|class)\s+\"?([\w,\s]+?)\"?\s+(\w+)$", line)
        if m:
            for cid in re.split(r"\s*,\s*", m.group(1)):
                if cid in classes:
                    classes[cid].classes.append(m.group(2))
            continue
        m = re.match(r"^style\s+(\w+)\s+(.+)$", line)
        if m:
            if m.group(1) in classes:
                classes[m.group(1)].css.update(parse_css_style(m.group(2)))
            continue
        if re.match(r"^(click|link|callback|accTitle|accDescr|title)\b", line):
            continue
        m = re.match(rf"^({NAME})\s*:\s*(.+)$", line)
        if m:
            member(ensure(m.group(1)), m.group(2))
            continue
        m = re.match(rf"^({NAME})$", line)
        if m:
            ensure(m.group(1))
            continue
        raise MermaidError(f"could not understand {line!r} in class diagram", lineno)
    if in_class is not None:
        raise MermaidError(f"class '{in_class.id}' body is missing its closing '}}'")
    return classes, order, rels, notes, namespaces, class_defs, direction


def _member_text(t: str) -> tuple[str, bool, bool]:
    """Returns (display text, static, abstract)."""
    static = abstract = False
    t = t.strip()
    m = re.search(r"\)([*$])", t)
    if m:
        static, abstract = m.group(1) == "$", m.group(1) == "*"
        t = t[: m.start() + 1] + t[m.end():]
    elif t.endswith("$"):
        static, t = True, t[:-1]
    elif t.endswith("*"):
        abstract, t = True, t[:-1]
    return t, static, abstract


def render(src: Source, opts: Options) -> Diagram:
    classes, order, rels, notes, namespaces, class_defs, direction = parse(src)
    nodes: dict[str, GNode] = {}
    for cid in order:
        c = classes[cid]
        style = opts.node_style()
        tcolor = None
        for k in c.classes:
            if k in class_defs:
                style, tcolor, _ = apply_css(style, tcolor, class_defs[k])
        if c.css:
            style, tcolor, _ = apply_css(style, tcolor, c.css)
        nodes[cid] = _class_node(c, style, opts.text(tcolor), opts)

    edges = []
    for a, b, l, r, line, ca, cb, label in rels:
        st = opts.edge_style(dash="dashed_narrow" if line == ".." else "solid")
        edges.append(GEdge(a, b, st, label, opts.text(opts.theme.edge_text, opts.font_size * 0.85),
                           start=HEAD.get(l, "none"), end=HEAD.get(r, "none"), start_label=ca, end_label=cb))
    in_ns = {cid for _, members in namespaces for cid in members}
    clusters = [GCluster(f"__ns_{name}", name, members) for name, members in namespaces]
    els, bb, boxes = render_graph(nodes, edges, [c for c in order if c not in in_ns], clusters, opts.direction or direction,
                                  opts, nodesep=50, ranksep=60)
    for target, text in notes:
        nspec = opts.text(opts.theme.text, opts.font_size * 0.85)
        tw, _, lines = measure_label(text, nspec, opts.wrap)
        nw, nh = tw + 20, lines * nspec.size * 1.25 + 14
        if target and target in boxes:
            x, y, w, h = boxes[target]
            nx, ny = x + w + 40, y
        else:
            nx, ny = bb.x1 + 30, bb.y0
        st = Style(stroke=opts.theme.note_stroke, fill=opts.theme.note_fill, width=1.2, rough=opts.rough)
        els += node_elements("note", nx, ny, nw, nh, st) + [label_at(text, nx + nw / 2, ny + nh / 2, nspec, tw)]
        if target and target in boxes:
            x, y, w, h = boxes[target]
            els.insert(len(els) - 2, ShapeEl(Line((x + w, y + min(h / 2, 20)), (nx, ny + nh / 2)),
                                             Style(stroke=opts.theme.note_stroke, width=1.0, dash="dashed_narrow")))
    from ..model import union_all
    return Diagram(els, union_all([e.bbox() for e in els]), "class", src.title)


def _class_node(c: Cls, style: Style, spec: TextSpec, opts: Options) -> GNode:
    name_spec = replace(spec, bold=True, markup=False)
    ann_spec = replace(spec, size=spec.size * 0.8, italic=True, markup=False)
    mem_spec = replace(spec, size=spec.size * 0.88, markup=False)
    pad = 10.0
    lh = mem_spec.size * 1.3
    head_lines = [(f"«{a}»", ann_spec) for a in c.annotations] + [(c.label, name_spec)]
    widths = [measure_label(t, s, None)[0] for t, s in head_lines]
    attrs = [_member_text(a) for a in c.attrs]
    meths = [_member_text(m) for m in c.methods]
    widths += [measure_label(t, mem_spec, None)[0] for t, _, _ in attrs + meths]
    w = max(widths + [80.0]) + 2 * pad
    head_h = sum(s.size * 1.3 for _, s in head_lines) + 12
    attr_h = max(len(attrs) * lh + 10, 14.0)
    meth_h = max(len(meths) * lh + 10, 14.0) if (meths or attrs) else 0.0
    if not attrs and not meths:
        attr_h = 14.0
    h = head_h + attr_h + meth_h

    def draw(x, y, w_, h_):
        out = node_elements("rect", x, y, w_, h_, style)
        yy = y + 6
        for t, s in head_lines:
            out.append(label_at(t, x + w_ / 2, yy + s.size * 0.65, s, None))
            yy += s.size * 1.3
        sep = replace(style, fill=None, width=max(1.0, style.width * 0.8))
        y1 = y + head_h
        out.append(ShapeEl(Line((x, y1), (x + w_, y1)), sep))
        ty = y1 + 5
        for t, static, abstract in attrs:
            out.append(TextEl(t, x + pad, ty, font=mem_spec.font, size=mem_spec.size, color=mem_spec.color,
                              italic=abstract, underline=static))
            ty += lh
        if meth_h:
            y2 = y1 + attr_h
            out.append(ShapeEl(Line((x, y2), (x + w_, y2)), sep))
            ty = y2 + 5
            for t, static, abstract in meths:
                out.append(TextEl(t, x + pad, ty, font=mem_spec.font, size=mem_spec.size, color=mem_spec.color,
                                  italic=abstract, underline=static))
                ty += lh
        return out

    return GNode(c.id, w, h, draw, lambda x, y, w_, h_: node_outline("rect", x, y, w_, h_))
