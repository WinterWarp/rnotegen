"""Mermaid `flowchart` / `graph` -> native Rnote shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from ..diagram import (
    label_at, measure_label, node_elements, node_label_center, node_outline, node_size_for_text,
    normalize_kind,
)
from ..model import Style
from .common import Diagram, MermaidError, Options, Source, apply_css, clean_label, parse_css_style, split_statements
from .graphdraw import GCluster, GEdge, GNode, render_graph

ID_CHARS = r"A-Za-z0-9_À-￿"
ID_RE = re.compile(rf"[{ID_CHARS}]+(?:[.\-](?![-.>=])[{ID_CHARS}]+)*")

# (opener, closers -> kind); longest openers first
SHAPES = [
    ("(((", {")))": "doublecircle"}),
    ("((", {"))": "circle"}),
    ("([", {"])": "stadium"}),
    ("[[", {"]]": "subroutine"}),
    ("[(", {")]": "cylinder"}),
    ("{{", {"}}": "hexagon"}),
    ("[/", {"/]": "parallelogram", "\\]": "trapezoid"}),
    ("[\\", {"\\]": "parallelogram_alt", "/]": "trapezoid_alt"}),
    ("(", {")": "round"}),
    ("[", {"]": "rect"}),
    ("{", {"}": "diamond"}),
    (">", {"]": "asymmetric"}),
]

NEW_SHAPES = {  # @{ shape: ... } names from Mermaid 11
    "rect": "rect", "rounded": "round", "stadium": "stadium", "fr-rect": "subroutine", "subroutine": "subroutine",
    "cyl": "cylinder", "cylinder": "cylinder", "circle": "circle", "circ": "circle", "dbl-circ": "doublecircle",
    "diam": "diamond", "diamond": "diamond", "decision": "diamond", "hex": "hexagon", "hexagon": "hexagon",
    "lean-r": "parallelogram", "lean-l": "parallelogram_alt", "trap-b": "trapezoid", "trap-t": "trapezoid_alt",
    "odd": "asymmetric", "doc": "document", "document": "document", "notch-rect": "note", "card": "note",
    "sm-circ": "start", "start": "start", "f-circ": "start", "fr-circ": "end", "stop": "end", "text": "text",
    "fork": "bar", "join": "bar", "das": "cylinder", "db": "cylinder", "database": "cylinder", "event": "round",
    "process": "rect", "terminal": "stadium", "pill": "stadium", "in-out": "parallelogram", "manual-input": "parallelogram",
}

LINK_FULL = [
    # (regex, kind)
    (re.compile(r"(?P<s>[<xo])?(?P<b>-{2,})(?P<e>[>xo])"), "normal"),
    (re.compile(r"(?P<s>[<xo])?(?P<b>-{3,})"), "normal"),
    (re.compile(r"(?P<s>[<xo])?(?P<b>={2,})(?P<e>[>xo])"), "thick"),
    (re.compile(r"(?P<s>[<xo])?(?P<b>={3,})"), "thick"),
    (re.compile(r"(?P<s>[<xo])?(?P<b>-\.+-)(?P<e>[>xo])?"), "dotted"),
    (re.compile(r"(?P<b>~{3,})"), "invisible"),
]
LINK_START = [
    (re.compile(r"(?P<s>[<xo])?--(?![->])"), "normal", re.compile(r"(?P<b>-{2,})(?P<e>[>xo])|(?P<b2>-{3,})")),
    (re.compile(r"(?P<s>[<xo])?==(?![=>])"), "thick", re.compile(r"(?P<b>={2,})(?P<e>[>xo])|(?P<b2>={3,})")),
    (re.compile(r"(?P<s>[<xo])?-\.(?!-)"), "dotted", re.compile(r"(?P<b>\.+-)(?P<e>[>xo])?")),
]
HEAD_OF = {">": "arrow", "<": "arrow", "x": "cross", "o": "circle_open", None: "none", "": "none"}


@dataclass
class FNode:
    id: str
    label: str
    kind: str = "rect"
    classes: list[str] = field(default_factory=list)
    css: dict = field(default_factory=dict)


@dataclass
class FEdge:
    u: str
    v: str
    kind: str
    start: str
    end: str
    minlen: int
    label: str | None
    line: int


@dataclass
class FSub:
    id: str
    title: str
    nodes: list[str] = field(default_factory=list)
    subs: list["FSub"] = field(default_factory=list)
    direction: str | None = None


class FlowParser:
    def __init__(self):
        self.nodes: dict[str, FNode] = {}
        self.order: list[str] = []
        self.edges: list[FEdge] = []
        self.direction = "TB"
        self.root = FSub("__root__", "")
        self.stack: list[FSub] = [self.root]
        self.owner: dict[str, FSub] = {}
        self.class_defs: dict[str, dict] = {}
        self.link_styles: dict[int | str, dict] = {}
        self.sub_ids: dict[str, FSub] = {}
        self.warnings: list[str] = []

    # -- nodes
    def touch(self, nid: str, label: str | None = None, kind: str | None = None, classes=None):
        n = self.nodes.get(nid)
        if n is None:
            if nid in self.sub_ids:
                return
            n = FNode(nid, nid)
            self.nodes[nid] = n
            self.order.append(nid)
            cur = self.stack[-1]
            cur.nodes.append(nid)
            self.owner[nid] = cur
        elif len(self.stack) > 1 and self.owner.get(nid) is self.root:
            # mentioned inside a subgraph after being created at top level: move it in
            self.root.nodes.remove(nid)
            self.stack[-1].nodes.append(nid)
            self.owner[nid] = self.stack[-1]
        if label is not None:
            n.label = label
        if kind is not None:
            n.kind = kind
        if classes:
            n.classes += classes

    def parse(self, src: Source) -> None:
        header_seen = False
        for lineno, raw in src.lines:
            line = raw.strip()
            if not line:
                continue
            if not header_seen:
                m = re.match(r"^(flowchart|graph)(?:-elk)?\b\s*(\w+)?", line, re.I)
                if not m:
                    raise MermaidError(f"expected 'flowchart TD' or 'graph LR', got {line!r}", lineno)
                header_seen = True
                if m.group(2):
                    self.direction = _norm_dir(m.group(2), lineno)
                rest = line[m.end():].strip().lstrip(";")
                if rest:
                    for st in split_statements(rest):
                        self.statement(st, lineno)
                continue
            for st in split_statements(line):
                self.statement(st, lineno)
        if len(self.stack) > 1:
            raise MermaidError(f"subgraph '{self.stack[-1].title or self.stack[-1].id}' is missing its 'end'")

    def statement(self, st: str, lineno: int) -> None:
        low = st.lower()
        if low == "end":
            if len(self.stack) == 1:
                raise MermaidError("'end' without matching 'subgraph'", lineno)
            self.stack.pop()
            return
        if re.match(r"^subgraph\b", low):
            self.subgraph(st[8:].strip(), lineno)
            return
        m = re.match(r"^direction\s+(\w+)$", st, re.I)
        if m:
            d = _norm_dir(m.group(1), lineno)
            if len(self.stack) > 1:
                self.stack[-1].direction = d
            else:
                self.direction = d
            return
        m = re.match(r"^classDef\s+([\w,\s-]+?)\s+(.+)$", st)
        if m:
            css = parse_css_style(m.group(2))
            for name in re.split(r"[,\s]+", m.group(1).strip()):
                self.class_defs[name] = css
            return
        m = re.match(r"^class\s+(.+?)\s+([\w-]+)$", st)
        if m:
            for nid in re.split(r"\s*,\s*", m.group(1).strip()):
                if nid in self.nodes:
                    self.nodes[nid].classes.append(m.group(2))
                elif nid in self.sub_ids:
                    pass
                else:
                    self.touch(nid, classes=[m.group(2)])
            return
        m = re.match(r"^style\s+([^\s]+)\s+(.+)$", st)
        if m:
            nid = m.group(1)
            if nid not in self.nodes and nid not in self.sub_ids:
                self.touch(nid)
            if nid in self.nodes:
                self.nodes[nid].css.update(parse_css_style(m.group(2)))
            return
        m = re.match(r"^linkStyle\s+([\w,\s]+?)\s+(.+)$", st)
        if m:
            css = parse_css_style(m.group(2))
            for k in re.split(r"\s*,\s*", m.group(1).strip()):
                self.link_styles[int(k) if k.isdigit() else k] = css
            return
        if re.match(r"^(accTitle|accDescr|title)\b", st) or (
                re.match(r"^click\s", st) and not re.search(r"--|==|-\.|~~~|&", st)):
            if st.startswith("click"):
                self.warnings.append(f"line {lineno}: 'click' interactions are ignored in Rnote")
            return
        self.chain(st, lineno)

    def subgraph(self, rest: str, lineno: int) -> None:
        rest = rest.strip()
        sid = title = None
        m = re.match(rf"^([{ID_CHARS}.\-]+)\s*\[(.*)\]\s*$", rest)
        if m:
            sid, title = m.group(1), clean_label(m.group(2))
        elif rest.startswith('"'):
            title = clean_label(rest)
            sid = f"__sub{len(self.sub_ids)}"
        elif rest:
            if " " in rest:
                title = clean_label(rest)
                sid = f"__sub{len(self.sub_ids)}"
            else:
                sid = title = rest
        else:
            sid, title = f"__sub{len(self.sub_ids)}", ""
        sub = FSub(sid, title)
        if sid in self.nodes:  # referenced before being declared as a subgraph
            n = self.nodes.pop(sid)
            self.order.remove(sid)
            self.owner[sid].nodes.remove(sid)
            del n
        self.sub_ids[sid] = sub
        self.stack[-1].subs.append(sub)
        self.stack.append(sub)

    # -- node / edge chains
    def chain(self, st: str, lineno: int) -> None:
        pos = 0
        group, pos = self.node_group(st, pos, lineno)
        while True:
            pos = _skip_ws(st, pos)
            if pos >= len(st):
                break
            link, pos = self.link(st, pos, lineno)
            if link is None:
                raise MermaidError(f"could not understand {st[pos:]!r} (expected an arrow like --> )", lineno)
            nxt, pos = self.node_group(st, pos, lineno)
            kind, s, e, minlen, label = link
            for a in group:
                for b in nxt:
                    self.edges.append(FEdge(a, b, kind, s, e, minlen, label, lineno))
            group = nxt

    def node_group(self, st: str, pos: int, lineno: int) -> tuple[list[str], int]:
        ids = []
        while True:
            pos = _skip_ws(st, pos)
            nid, pos = self.node(st, pos, lineno)
            ids.append(nid)
            p2 = _skip_ws(st, pos)
            if p2 < len(st) and st[p2] == "&":
                pos = p2 + 1
                continue
            return ids, pos

    def node(self, st: str, pos: int, lineno: int) -> tuple[str, int]:
        m = ID_RE.match(st, pos)
        if not m:
            raise MermaidError(f"expected a node id at {st[pos:]!r}", lineno)
        nid = m.group(0)
        pos = m.end()
        label = kind = None
        if st.startswith("@{", pos):
            close = st.find("}", pos)
            if close == -1:
                raise MermaidError("unclosed '@{' in node definition", lineno)
            props = st[pos + 2 : close]
            pos = close + 1
            sm = re.search(r"shape\s*:\s*([\w-]+)", props)
            lm = re.search(r"label\s*:\s*(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^,}]+)", props)
            if sm:
                kind = NEW_SHAPES.get(sm.group(1).lower(), "rect")
            if lm:
                label = clean_label(lm.group(1).strip().strip("'"))
        else:
            for opener, closers in SHAPES:
                if st.startswith(opener, pos):
                    res = _read_label(st, pos + len(opener), closers)
                    if res is None:
                        continue
                    text, closer, end = res
                    label, kind, pos = clean_label(text), closers[closer], end
                    break
        classes = []
        while st.startswith(":::", pos):
            cm = re.match(r":::([\w-]+)", st[pos:])
            if not cm:
                break
            classes.append(cm.group(1))
            pos += cm.end()
        self.touch(nid, label, kind, classes)
        return nid, pos

    def link(self, st: str, pos: int, lineno: int):
        for rx, kind in LINK_FULL:
            m = rx.match(st, pos)
            if not m:
                continue
            end_ch = m.groupdict().get("e")
            # 'x'/'o' only count as heads when not glued to a following id
            if end_ch in ("x", "o") and m.end() < len(st) and re.match(rf"[{ID_CHARS}]", st[m.end()]):
                continue
            b = m.group("b")
            s = m.groupdict().get("s")
            p = m.end()
            label = None
            p2 = _skip_ws(st, p)
            if p2 < len(st) and st[p2] == "|":
                close = st.find("|", p2 + 1)
                if close == -1:
                    raise MermaidError("unclosed |label| on link", lineno)
                label = clean_label(st[p2 + 1 : close])
                p = close + 1
            return (kind, HEAD_OF[s], HEAD_OF[end_ch], _minlen(kind, b, end_ch), label), p
        for rx, kind, close_rx in LINK_START:
            m = rx.match(st, pos)
            if not m:
                continue
            # find the closing part of the link after the label text
            search_from = m.end()
            cm = None
            for cand in close_rx.finditer(st, search_from):
                if cand.start() > search_from and st[cand.start() - 1] in " \t\"'`":
                    cm = cand
                    break
                if cand.start() > search_from:
                    cm = cand
                    break
            if cm is None:
                continue
            label = clean_label(st[search_from : cm.start()])
            e = cm.groupdict().get("e")
            b = cm.groupdict().get("b") or cm.groupdict().get("b2") or ""
            s = m.groupdict().get("s")
            return (kind, HEAD_OF[s], HEAD_OF[e], _minlen(kind, ("--" if kind != "dotted" else "-") + b, e), label or None), cm.end()
        return None, pos


def _minlen(kind: str, body: str, end: str | None) -> int:
    if kind == "dotted":
        return max(1, body.count("."))
    if kind == "invisible":
        return max(1, len(body) - 2)
    return max(1, len(body) - (1 if end else 2))


def _read_label(st: str, pos: int, closers: dict[str, str]):
    """Read label text starting at pos until one of the closers. Returns (text, closer, end)."""
    p = pos
    while p < len(st) and st[p] == " ":
        p += 1
    if p < len(st) and st[p] == '"':
        q = p + 1
        while q < len(st) and st[q] != '"':
            q += 2 if st[q] == "\\" else 1
        if q >= len(st):
            return None
        after = q + 1
        while after < len(st) and st[after] == " ":
            after += 1
        for c in sorted(closers, key=len, reverse=True):
            if st.startswith(c, after):
                return st[p : q + 1], c, after + len(c)
        return None
    best = None
    for c in closers:
        # single-char bracket closers: respect nesting of the same bracket type
        if len(c) == 1 and c in ")]}":
            opener = {")": "(", "]": "[", "}": "{"}[c]
            depth = 0
            for k in range(pos, len(st)):
                ch = st[k]
                if ch == opener:
                    depth += 1
                elif ch == c:
                    if depth == 0:
                        if best is None or k < best[0]:
                            best = (k, c)
                        break
                    depth -= 1
        else:
            k = st.find(c, pos)
            if k != -1 and (best is None or k < best[0]):
                best = (k, c)
    if best is None:
        return None
    k, c = best
    return st[pos:k], c, k + len(c)


def _skip_ws(st: str, pos: int) -> int:
    while pos < len(st) and st[pos] in " \t":
        pos += 1
    return pos


def _norm_dir(d: str, lineno: int) -> str:
    d = d.upper()
    if d == "TD":
        d = "TB"
    if d not in ("TB", "BT", "LR", "RL"):
        raise MermaidError(f"unknown direction {d!r}; use TD, TB, BT, LR or RL", lineno)
    return d


def render(src: Source, opts: Options) -> Diagram:
    p = FlowParser()
    p.parse(src)
    direction = opts.direction or p.direction
    nodes: dict[str, GNode] = {}

    for nid in p.order:
        n = p.nodes[nid]
        kind = normalize_kind(n.kind)
        style = opts.node_style()
        tcolor = None
        bold = False
        for cls in n.classes:
            if cls in p.class_defs:
                style, tcolor, b = apply_css(style, tcolor, p.class_defs[cls])
                bold = bold or b
        if "default" in p.class_defs and not n.classes:
            style, tcolor, b = apply_css(style, tcolor, p.class_defs["default"])
            bold = bold or b
        if n.css:
            style, tcolor, b = apply_css(style, tcolor, n.css)
            bold = bold or b
        spec = opts.text(tcolor, bold=bold)
        label = n.label
        if kind in ("start", "end", "bar"):
            label = ""
        tw, th, _ = measure_label(label, spec, opts.wrap) if label else (0.0, 0.0, 0)
        w, h = node_size_for_text(kind, tw, th, min_w=40.0, min_h=38.0 if kind not in ("start", "end", "bar") else 0)
        nodes[nid] = _gnode(nid, kind, label, w, h, style, spec, tw)

    edges: list[GEdge] = []
    default_css = p.link_styles.get("default", {})
    for i, e in enumerate(p.edges):
        st = opts.edge_style()
        if e.kind == "thick":
            st = replace(st, width=st.width * 2.0)
        elif e.kind == "dotted":
            st = replace(st, dash="dashed_narrow")
        tcolor = None
        for css in (default_css, p.link_styles.get(i, {})):
            if css:
                st, tcolor, _ = apply_css(st, tcolor, css)
        st = replace(st, fill=None)
        end = e.end
        if end == "arrow" and not opts.native_arrows:
            end = "triangle"
        start = e.start
        if start == "arrow" and not opts.native_arrows:
            start = "triangle"
        edges.append(GEdge(e.u, e.v, st, e.label, opts.text(tcolor or opts.theme.edge_text, opts.font_size * 0.9),
                           start=start, end=end, minlen=e.minlen, invisible=e.kind == "invisible"))

    def conv(s: FSub) -> GCluster:
        return GCluster(s.id, s.title, list(s.nodes), [conv(x) for x in s.subs], s.direction)

    els, bb, _ = render_graph(nodes, edges, list(p.root.nodes), [conv(s) for s in p.root.subs], direction, opts)
    return Diagram(els, bb, "flowchart", src.title, warnings=p.warnings)


def _gnode(nid: str, kind: str, label: str, w: float, h: float, style: Style, spec, tw: float) -> GNode:
    def draw(x, y, w_, h_):
        out = node_elements(kind, x, y, w_, h_, style)
        if label:
            cx, cy = node_label_center(kind, x, y, w_, h_)
            out.append(label_at(label, cx, cy, spec, max(tw, 10.0)))
        return out

    def outline(x, y, w_, h_):
        return node_outline(kind, x, y, w_, h_)

    return GNode(nid, w, h, draw, outline)
