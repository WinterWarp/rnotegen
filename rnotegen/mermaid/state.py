"""Mermaid `stateDiagram` / `stateDiagram-v2` -> native Rnote shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from ..diagram import label_at, measure_label, node_elements, node_outline, node_size_for_text
from ..model import Line, ShapeEl, Style
from .common import Diagram, MermaidError, Options, Source, apply_css, clean_label, parse_css_style
from .graphdraw import GCluster, GEdge, GNode, render_graph

TRANS_RE = re.compile(r"^(?P<a>\[\*\]|[\w.\-À-￿]+)\s*-->\s*(?P<b>\[\*\]|[\w.\-À-￿]+)\s*(?::\s*(?P<label>.*))?$")


@dataclass
class SState:
    id: str
    label: str
    kind: str = "state"  # state | start | end | fork | join | choice
    desc: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)


@dataclass
class Scope:
    id: str
    label: str
    states: list[str] = field(default_factory=list)
    subs: list["Scope"] = field(default_factory=list)
    direction: str | None = None


@dataclass
class SNote:
    target: str
    side: str
    text: str


def parse(src: Source):
    states: dict[str, SState] = {}
    owner: dict[str, Scope] = {}
    root = Scope("__root__", "")
    stack = [root]
    trans: list[tuple[str, str, str | None]] = []
    notes: list[SNote] = []
    class_defs: dict[str, dict] = {}
    direction = "TB"
    header = False
    pending_note: SNote | None = None
    scopes: dict[str, Scope] = {}

    def ensure(sid: str, label: str | None = None) -> str:
        if sid in scopes:
            return sid
        if sid not in states:
            states[sid] = SState(sid, sid)
            stack[-1].states.append(sid)
            owner[sid] = stack[-1]
        if label:
            states[sid].label = label
        return sid

    def pseudo(kind: str) -> str:
        sc = stack[-1]
        sid = f"__{kind}_{sc.id}"
        if sid not in states:
            states[sid] = SState(sid, "", kind)
            sc.states.append(sid)
            owner[sid] = sc
        return sid

    for lineno, raw in src.lines:
        line = raw.strip()
        if pending_note is not None:
            if line.lower() == "end note":
                notes.append(pending_note)
                pending_note = None
            else:
                pending_note.text += ("\n" if pending_note.text else "") + line
            continue
        if not line:
            continue
        if not header:
            if not re.match(r"^statediagram(-v2)?\b", line, re.I):
                raise MermaidError("expected 'stateDiagram-v2'", lineno)
            header = True
            continue
        low = line.lower()
        m = re.match(r"^direction\s+(\w+)$", line, re.I)
        if m:
            d = m.group(1).upper().replace("TD", "TB")
            if len(stack) > 1:
                stack[-1].direction = d
            else:
                direction = d
            continue
        if low.startswith("hide empty") or low.startswith("scale ") or low.startswith("accTitle") or low.startswith("accdescr"):
            continue
        if line == "}":
            if len(stack) == 1:
                raise MermaidError("'}' without an open composite state", lineno)
            stack.pop()
            continue
        if line == "--":
            continue  # concurrency separator: regions are drawn together
        m = re.match(r"^classDef\s+(\w+)\s+(.+)$", line)
        if m:
            class_defs[m.group(1)] = parse_css_style(m.group(2))
            continue
        m = re.match(r"^class\s+(.+?)\s+(\w+)$", line)
        if m:
            for sid in re.split(r"\s*,\s*", m.group(1)):
                ensure(sid)
                if sid in states:
                    states[sid].classes.append(m.group(2))
            continue
        m = re.match(r"^note\s+(left of|right of)\s+([\w.\-]+)\s*(?::\s*(.*))?$", line, re.I)
        if m:
            target = ensure(m.group(2))
            n = SNote(target, m.group(1).lower(), clean_label(m.group(3) or ""))
            if m.group(3) is None:
                pending_note = n
            else:
                notes.append(n)
            continue
        m = re.match(r"^state\s+\"([^\"]*)\"\s+as\s+([\w.\-]+)\s*(\{)?$", line)
        if m:
            if m.group(3):
                sc = Scope(m.group(2), clean_label(m.group(1)))
                _open_scope(sc, stack, scopes, states, owner)
            else:
                ensure(m.group(2), clean_label(m.group(1)))
            continue
        m = re.match(r"^state\s+([\w.\-]+)\s*(<<(fork|join|choice)>>)?\s*(\{)?$", line)
        if m:
            sid = m.group(1)
            if m.group(4):
                sc = Scope(sid, sid)
                _open_scope(sc, stack, scopes, states, owner)
            else:
                ensure(sid)
                if m.group(3):
                    states[sid].kind = m.group(3)
                    states[sid].label = ""
            continue
        m = TRANS_RE.match(line)
        if m:
            a = pseudo("start") if m.group("a") == "[*]" else ensure(m.group("a"))
            b = pseudo("end") if m.group("b") == "[*]" else ensure(m.group("b"))
            trans.append((a, b, clean_label(m.group("label")) if m.group("label") else None))
            continue
        m = re.match(r"^([\w.\-]+)\s*:\s*(.+)$", line)
        if m:
            sid = ensure(m.group(1))
            if sid in states:
                states[sid].desc.append(clean_label(m.group(2)))
            continue
        m = re.match(r"^([\w.\-]+)(:::(\w+))?$", line)
        if m:
            sid = ensure(m.group(1))
            if m.group(3) and sid in states:
                states[sid].classes.append(m.group(3))
            continue
        raise MermaidError(f"could not understand {line!r} in state diagram", lineno)
    if len(stack) > 1:
        raise MermaidError(f"composite state '{stack[-1].id}' is missing its closing '}}'")
    return states, root, trans, notes, class_defs, direction, scopes


def _open_scope(sc: Scope, stack, scopes, states, owner):
    if sc.id in states:  # was referenced as a plain state before
        st = states.pop(sc.id)
        owner[sc.id].states.remove(sc.id)
        if st.label and st.label != sc.id and sc.label == sc.id:
            sc.label = st.label
    if sc.id in scopes:
        stack.append(scopes[sc.id])
        return
    scopes[sc.id] = sc
    stack[-1].subs.append(sc)
    stack.append(sc)


def render(src: Source, opts: Options) -> Diagram:
    states, root, trans, notes, class_defs, direction, scopes = parse(src)
    horizontal = (opts.direction or direction) in ("LR", "RL")
    nodes: dict[str, GNode] = {}
    spec = opts.text()
    for sid, st in states.items():
        style = opts.node_style()
        tcolor = None
        for c in st.classes:
            if c in class_defs:
                style, tcolor, _ = apply_css(style, tcolor, class_defs[c])
        if st.kind == "start":
            nodes[sid] = _simple(sid, "start", 22, 22, style)
        elif st.kind == "end":
            nodes[sid] = _simple(sid, "end", 26, 26, style)
        elif st.kind in ("fork", "join"):
            w, h = (8, 70) if horizontal else (70, 8)
            nodes[sid] = _simple(sid, "bar", w, h, style)
        elif st.kind == "choice":
            nodes[sid] = _simple(sid, "diamond", 30, 30, style)
        else:
            nodes[sid] = _state_node(sid, st, style, opts.text(tcolor), opts)

    edges = [GEdge(a, b, opts.edge_style(), label, opts.text(opts.theme.edge_text, opts.font_size * 0.9))
             for a, b, label in trans]

    def conv(sc: Scope) -> GCluster:
        st = Style(stroke=opts.theme.node_stroke, fill=opts.theme.cluster_fill or opts.background, width=1.4,
                   rough=opts.rough or opts.theme.rough)
        return GCluster(sc.id, sc.label, list(sc.states), [conv(x) for x in sc.subs], sc.direction, st,
                        opts.text(opts.theme.text, bold=True))

    els, bb, boxes = render_graph(nodes, edges, list(root.states), [conv(s) for s in root.subs],
                           opts.direction or direction, opts, nodesep=40, ranksep=50)

    # notes: placed beside their state after layout
    if notes:
        nspec = opts.text(opts.theme.text, opts.font_size * 0.85)
        occupied = [e.bbox() for e in els]
        for n in notes:
            if n.target not in boxes:
                continue
            x, y, w, h = boxes[n.target]
            tw, _, lines = measure_label(n.text, nspec, opts.wrap)
            nw, nh = tw + 20, lines * nspec.size * 1.25 + 14
            nx = x + w + 30 if n.side == "right of" else x - 30 - nw
            ny = y + h / 2 - nh / 2
            for _ in range(20):
                clash = any(b.intersects(_bb(nx, ny, nw, nh).expand(4)) for b in occupied)
                if not clash:
                    break
                nx += (40 if n.side == "right of" else -40)
            st = Style(stroke=opts.theme.note_stroke, fill=opts.theme.note_fill, width=1.2, rough=opts.rough)
            note_els = node_elements("note", nx, ny, nw, nh, st) + [label_at(n.text, nx + nw / 2, ny + nh / 2, nspec, tw)]
            ax = nx if n.side == "right of" else nx + nw
            note_els.insert(0, ShapeEl(Line((x + w if n.side == "right of" else x, y + h / 2), (ax, ny + nh / 2)),
                                       Style(stroke=opts.theme.note_stroke, width=1.0, dash="dashed_narrow")))
            els += note_els
            occupied += [e.bbox() for e in note_els]
        from ..model import union_all
        bb = union_all([e.bbox() for e in els])
    return Diagram(els, bb, "state", src.title)


def _bb(x, y, w, h):
    from ..model import BBox
    return BBox(x, y, x + w, y + h)


def _simple(sid: str, kind: str, w: float, h: float, style: Style) -> GNode:
    def draw(x, y, w_, h_):
        return node_elements(kind, x, y, w_, h_, style)

    return GNode(sid, w, h, draw, lambda x, y, w_, h_: node_outline(kind, x, y, w_, h_))


def _state_node(sid: str, st: SState, style: Style, spec, opts: Options) -> GNode:
    name = st.label or st.id
    if st.desc:
        name_spec = replace(spec, bold=True)
        dspec = replace(spec, size=spec.size * 0.85)
        nw, _, nl = measure_label(name, name_spec, opts.wrap)
        desc = "\n".join(st.desc)
        dw, _, dl = measure_label(desc, dspec, opts.wrap)
        name_h = nl * name_spec.size * 1.25 + 10
        desc_h = dl * dspec.size * 1.25 + 12
        w = max(nw, dw) + 30
        h = name_h + desc_h

        def draw(x, y, w_, h_):
            out = node_elements("round", x, y, w_, h_, style)
            out.append(label_at(name, x + w_ / 2, y + name_h / 2, name_spec, nw))
            out.append(ShapeEl(Line((x, y + name_h), (x + w_, y + name_h)), replace(style, fill=None, width=1.0)))
            out.append(label_at(desc, x + w_ / 2, y + name_h + desc_h / 2, dspec, dw))
            return out
    else:
        tw, th, _ = measure_label(name, spec, opts.wrap)
        w, h = node_size_for_text("round", tw, th, min_w=60, min_h=38)

        def draw(x, y, w_, h_):
            return node_elements("round", x, y, w_, h_, style) + [label_at(name, x + w_ / 2, y + h_ / 2, spec, tw)]

    return GNode(sid, w, h, draw, lambda x, y, w_, h_: node_outline("round", x, y, w_, h_))
