"""Mermaid `sequenceDiagram` -> native Rnote shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from ..colors import Color, parse_color
from ..diagram import (
    connector_elements, label_at, measure_label, node_elements,
)
from ..model import Element, Ellipse, Line, Polygon, Rect, ShapeEl, Style, union_all, BBox
from .common import Diagram, MermaidError, Options, Source, clean_label

ARROWS = ["<<-->>", "<<->>", "-->>", "->>", "--x", "-x", "--)", "-)", "-->", "->"]
_ARROW_RE = "|".join(re.escape(a) for a in ARROWS)
MSG_RE = re.compile(rf"^(?P<a>[^:]+?)\s*(?P<arrow>{_ARROW_RE})\s*(?P<act>[+-])?\s*(?P<b>[^:+\-][^:]*?)\s*(?::\s*(?P<text>.*))?$")
NOTE_RE = re.compile(r"^note\s+(?P<pos>left of|right of|over)\s+(?P<who>[^:]+?)\s*:\s*(?P<text>.*)$", re.I)
PART_RE = re.compile(r"^(?:create\s+)?(?P<kind>participant|actor)\s+(?P<id>[^\s@]+?)(?:@\{[^}]*\})?(?:\s+as\s+(?P<label>.+))?$", re.I)
BLOCK_KW = ("loop", "alt", "opt", "par", "critical", "break", "rect")
DIVIDER_KW = ("else", "and", "option")


@dataclass
class Participant:
    id: str
    label: str
    actor: bool = False
    idx: int = 0
    w: float = 0.0
    x: float = 0.0  # center


@dataclass
class Event:
    kind: str  # msg | note | block | divider | end | activate | deactivate
    line: int
    a: str = ""
    b: str = ""
    arrow: str = ""
    text: str = ""
    act: str | None = None
    pos: str = ""
    who: list[str] = field(default_factory=list)
    block: str = ""


@dataclass
class Box:
    label: str
    color: Color | None
    members: list[str] = field(default_factory=list)


def parse(src: Source):
    parts: dict[str, Participant] = {}
    order: list[str] = []
    events: list[Event] = []
    boxes: list[Box] = []
    cur_box: Box | None = None
    title = src.title
    autonumber = False
    depth = 0
    header = False

    def part(pid: str, label: str | None = None, actor: bool = False):
        pid = pid.strip()
        if pid not in parts:
            parts[pid] = Participant(pid, label or pid, actor, len(order))
            order.append(pid)
            if cur_box is not None:
                cur_box.members.append(pid)
        elif label:
            parts[pid].label = label
        return parts[pid]

    for lineno, raw in src.lines:
        line = raw.strip()
        if not line:
            continue
        if not header:
            if not line.lower().startswith("sequencediagram"):
                raise MermaidError("expected 'sequenceDiagram'", lineno)
            header = True
            continue
        low = line.lower()
        m = PART_RE.match(line)
        if m:
            label = clean_label(m.group("label")) if m.group("label") else None
            part(m.group("id"), label, m.group("kind").lower() == "actor")
            continue
        if low.startswith("title"):
            title = clean_label(line[5:].lstrip(" :"))
            continue
        if low.startswith("autonumber"):
            autonumber = True
            continue
        if re.match(r"^(destroy|links?|properties|details)\b", low):
            continue
        if low.startswith("box"):
            rest = line[3:].strip()
            color = None
            mm = re.match(r"^(rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}|[A-Za-z]+)\b\s*(.*)$", rest)
            if mm:
                try:
                    color = parse_color(mm.group(1))
                    rest = mm.group(2)
                except ValueError:
                    color = None
            cur_box = Box(clean_label(rest), color)
            boxes.append(cur_box)
            continue
        m = re.match(r"^(activate|deactivate)\s+(.+)$", line, re.I)
        if m:
            part(m.group(2))
            events.append(Event(m.group(1).lower(), lineno, a=m.group(2).strip()))
            continue
        m = NOTE_RE.match(line)
        if m:
            who = [w.strip() for w in m.group("who").split(",") if w.strip()]
            for w in who:
                part(w)
            events.append(Event("note", lineno, pos=m.group("pos").lower(), who=who, text=clean_label(m.group("text"))))
            continue
        kw = low.split(None, 1)[0] if low else ""
        if kw in BLOCK_KW:
            depth += 1
            events.append(Event("block", lineno, block=kw, text=clean_label(line[len(kw):].strip())))
            continue
        if kw in DIVIDER_KW:
            events.append(Event("divider", lineno, block=kw, text=clean_label(line[len(kw):].strip())))
            continue
        if low == "end":
            if cur_box is not None and depth == 0:
                cur_box = None
                continue
            if depth == 0:
                raise MermaidError("'end' without an open loop/alt/opt/par/critical/break/rect block", lineno)
            depth -= 1
            events.append(Event("end", lineno))
            continue
        m = MSG_RE.match(line)
        if m:
            a, b = m.group("a").strip(), m.group("b").strip()
            part(a)
            part(b)
            events.append(Event("msg", lineno, a=a, b=b, arrow=m.group("arrow"), act=m.group("act"),
                                text=clean_label(m.group("text") or "")))
            continue
        raise MermaidError(f"could not understand {line!r} in sequence diagram", lineno)
    if depth:
        raise MermaidError("a loop/alt/opt/par/critical/break/rect block is missing its 'end'")
    return parts, order, events, boxes, title, autonumber


def render(src: Source, opts: Options) -> Diagram:
    parts, order, events, boxes, title, autonumber = parse(src)
    if not order:
        raise MermaidError("sequence diagram has no participants or messages")
    th = opts.theme
    fs = opts.font_size
    spec_part = opts.text()
    spec_msg = opts.text(th.edge_text, fs * 0.9)
    spec_note = opts.text(th.text, fs * 0.85)
    box_h = 40.0
    for p in parts.values():
        w, _, _ = measure_label(p.label, spec_part, opts.wrap)
        p.w = max(w + 30, 90.0)

    # ---- horizontal placement: satisfy spacing constraints between lifelines
    n = len(order)
    gaps = [0.0] * max(0, n - 1)  # center-to-center distance between i and i+1
    for i in range(n - 1):
        gaps[i] = parts[order[i]].w / 2 + parts[order[i + 1]].w / 2 + 40
    cons: list[tuple[int, int, float]] = []  # (i, j, min center distance)
    idx = {pid: i for i, pid in enumerate(order)}
    for ev in events:
        if ev.kind == "msg":
            i, j = sorted((idx[ev.a], idx[ev.b]))
            tw, _, _ = measure_label(ev.text, spec_msg, opts.wrap) if ev.text else (0, 0, 0)
            if i == j:
                if i + 1 < n:
                    cons.append((i, i + 1, parts[order[i]].w / 2 + tw + 50))
            else:
                cons.append((i, j, tw + 40))
        elif ev.kind == "note":
            tw, _, _ = measure_label(ev.text, spec_note, opts.wrap)
            ids = sorted(idx[w] for w in ev.who)
            if ev.pos == "over" and len(ids) > 1:
                cons.append((ids[0], ids[-1], tw + 20 - 40))
            elif ev.pos == "right of" and ids[0] + 1 < n:
                cons.append((ids[0], ids[0] + 1, tw + 50))
            elif ev.pos == "left of" and ids[0] > 0:
                cons.append((ids[0] - 1, ids[0], tw + 50))
    for i, j, need in sorted(cons, key=lambda c: c[1] - c[0]):
        cur = sum(gaps[i:j])
        if cur < need:
            extra = (need - cur) / (j - i)
            for k in range(i, j):
                gaps[k] += extra
    x = parts[order[0]].w / 2
    for i, pid in enumerate(order):
        if i > 0:
            x += gaps[i - 1]
        parts[pid].x = x

    # the leftmost note may stick out to the left; computed later via bbox normalization
    els_back: list[Element] = []
    els_life: list[Element] = []
    els_front: list[Element] = []
    top = 0.0
    if boxes:
        top = fs * 1.4 + 10
    has_actor = any(p.actor for p in parts.values())
    band = 84.0 if has_actor else box_h  # header band height
    y = top + band + 20
    num = 0
    active: dict[str, list[float]] = {pid: [] for pid in order}
    blocks: list[dict] = []
    edge_style = opts.edge_style()
    lifeline_style = replace(edge_style, width=1.0, dash="dashed_narrow", stroke=th.node_stroke)

    def span_of(pids: list[str]) -> tuple[float, float]:
        xs = [parts[p].x for p in pids]
        return min(xs), max(xs)

    def note_box(ev: Event):
        nonlocal y
        tw, tht, lines = measure_label(ev.text, spec_note, opts.wrap)
        h = lines * spec_note.size * 1.25 + 14
        xs = [parts[w].x for w in ev.who]
        if ev.pos == "over":
            x0, x1 = min(xs) - 30, max(xs) + 30
            w = max(x1 - x0, tw + 20)
            cx = (min(xs) + max(xs)) / 2
            x0 = cx - w / 2
        elif ev.pos == "right of":
            x0 = xs[0] + 14
            w = tw + 20
        else:
            w = tw + 20
            x0 = xs[0] - 14 - w
        st = Style(stroke=th.note_stroke, fill=th.note_fill, width=1.2, rough=opts.rough or th.rough)
        els_front.append(ShapeEl(Rect(x0, y, w, h), st))
        els_front.append(label_at(ev.text, x0 + w / 2, y + h / 2, spec_note, tw))
        for b in blocks:
            b["x0"] = min(b["x0"], x0)
            b["x1"] = max(b["x1"], x0 + w)
        y += h + 12

    for ev in events:
        if ev.kind == "msg":
            pa, pb = parts[ev.a], parts[ev.b]
            dotted = ev.arrow.startswith("--") or ev.arrow.startswith("<<--")
            st = replace(edge_style, dash="dashed_narrow") if dotted else edge_style
            end = {">>": "arrow", "x": "cross", ")": "arrow"}.get(ev.arrow.lstrip("<-"), "none")
            start = "arrow" if ev.arrow.startswith("<<") else "none"
            tw, _, lines = measure_label(ev.text, spec_msg, opts.wrap) if ev.text else (0, 0, 0)
            text_h = lines * spec_msg.size * 1.25
            y += text_h + 6
            for b in blocks:
                lo, hi = span_of([ev.a, ev.b])
                b["x0"] = min(b["x0"], lo - 20)
                b["x1"] = max(b["x1"], hi + 20)
            off_a = 5.0 if active[ev.a] else 0.0
            off_b = 5.0 if active[ev.b] or ev.act == "+" else 0.0
            badge_at = len(els_front)
            if autonumber:
                num += 1
                r = 9.0
                els_front.append(ShapeEl(Ellipse(pa.x, y, r, r), Style(stroke=th.edge, fill=th.edge, width=1.0)))
                els_front.append(label_at(str(num), pa.x, y, opts.text(opts.background, fs * 0.65, bold=True)))
            if pa is pb:
                x0 = pa.x + off_a
                h = 26.0
                pts = [(x0, y), (x0 + 40, y), (x0 + 40, y + h), (x0 + (5 if ev.act == "+" else off_a), y + h)]
                els_front += connector_elements(pts, st, end=end, start=start, bg=opts.background)
                if ev.text:
                    els_front.append(label_at(ev.text, x0 + 48, y - text_h / 2 - 3, spec_msg, opts.wrap, align="start"))
                    els_front[-1].x = x0 + 8
                y += h
            else:
                sgn = 1 if pb.x > pa.x else -1
                p0 = (pa.x + sgn * off_a, y)
                p1 = (pb.x - sgn * off_b, y)
                els_front += connector_elements([p0, p1], st, end=end, start=start, bg=opts.background)
                if ev.text:
                    els_front.append(label_at(ev.text, (pa.x + pb.x) / 2, y - text_h / 2 - 3, spec_msg, opts.wrap))
            if autonumber:
                # draw the number badge on top of the arrow start
                badge = [e for e in els_front[badge_at:badge_at + 2]]
                del els_front[badge_at:badge_at + 2]
                els_front += badge
            if ev.act == "+":
                active[ev.b].append(y)
            elif ev.act == "-":
                if active[ev.a]:
                    _activation(els_life, parts[ev.a].x, active[ev.a].pop(), y, len(active[ev.a]), th, opts)
            y += 18
        elif ev.kind == "activate":
            active[ev.a].append(y - 8)
        elif ev.kind == "deactivate":
            if active[ev.a]:
                _activation(els_life, parts[ev.a].x, active[ev.a].pop(), y - 8, len(active[ev.a]), th, opts)
        elif ev.kind == "note":
            note_box(ev)
        elif ev.kind == "block":
            color = None
            if ev.block == "rect":
                try:
                    color = parse_color(ev.text) if ev.text else None
                except ValueError:
                    color = None
            blocks.append({"kind": ev.block, "label": ev.text, "y0": y, "x0": 1e9, "x1": -1e9,
                           "dividers": [], "color": color})
            y += (0 if ev.block == "rect" else fs * 1.3 + 8) + 4
        elif ev.kind == "divider":
            if blocks:
                blocks[-1]["dividers"].append((y, ev.text))
                y += fs * 1.2 + 8
        elif ev.kind == "end":
            b = blocks.pop()
            y += 6
            if b["x0"] > b["x1"]:
                b["x0"], b["x1"] = parts[order[0]].x - 40, parts[order[-1]].x + 40
            x0, x1 = b["x0"] - 12, b["x1"] + 12
            kw_w, _, _ = measure_label(b["kind"], replace(spec_note, bold=True), None)
            lab_w = measure_label(f"[{b['label']}]", spec_note, None)[0] if b["label"] else 0.0
            for _, dt in b["dividers"]:
                lab_w = max(lab_w, measure_label(f"[{dt}]", spec_note, None)[0] - kw_w - 18)
            need = kw_w + 18 + lab_w + 30
            if x1 - x0 < need:
                grow = (need - (x1 - x0)) / 2
                x0, x1 = x0 - grow, x1 + grow
            for outer in blocks:
                outer["x0"] = min(outer["x0"], x0 - 8)
                outer["x1"] = max(outer["x1"], x1 + 8)
            _block(els_back, b, x0, x1, y, th, opts, spec_note, els_front)
            y += 10

    for pid in order:
        while active[pid]:
            _activation(els_life, parts[pid].x, active[pid].pop(), y - 8, len(active[pid]), th, opts)

    bottom = y + 10
    # participant boxes (top and bottom) and lifelines
    node_style = opts.node_style()
    for pid in order:
        p = parts[pid]
        els_life.insert(0, ShapeEl(Line((p.x, top + band), (p.x, bottom)), lifeline_style))
        for yy in (top, bottom):
            if p.actor:
                aw = 40.0
                els_front += node_elements("actor", p.x - aw / 2, yy, aw, 58.0, node_style)
                els_front.append(label_at(p.label, p.x, yy + 58 + fs * 0.75, spec_part, opts.wrap))
            else:
                by = yy + (band - box_h if yy == top else 0.0)
                els_front += node_elements("rect", p.x - p.w / 2, by, p.w, box_h, node_style)
                els_front.append(label_at(p.label, p.x, by + box_h / 2, spec_part, opts.wrap))

    for bx in boxes:
        if not bx.members:
            continue
        xs = [parts[m].x for m in bx.members]
        ws = [parts[m].w for m in bx.members]
        x0 = min(x - w / 2 for x, w in zip(xs, ws)) - 10
        x1 = max(x + w / 2 for x, w in zip(xs, ws)) + 10
        fill = bx.color.with_alpha(min(bx.color.a, 0.35)) if bx.color else th.cluster_fill
        els_back.insert(0, ShapeEl(Rect(x0, 0, x1 - x0, bottom + band + 10), Style(stroke=th.cluster_stroke, fill=fill, width=1.0)))
        if bx.label:
            els_back.insert(1, label_at(f"**{bx.label}**", (x0 + x1) / 2, fs * 0.8, opts.text(th.cluster_text)))

    els = els_back + els_life + els_front
    bb = union_all([e.bbox() for e in els]) or BBox(0, 0, 1, 1)
    return Diagram(els, bb, "sequence", title)


def _activation(out: list[Element], x: float, y0: float, y1: float, depth: int, th, opts: Options) -> None:
    w = 10.0
    xx = x - w / 2 + depth * 5
    out.append(ShapeEl(Rect(xx, y0, w, max(8.0, y1 - y0)), Style(stroke=th.node_stroke, fill=th.cluster_fill or th.background,
                                                               width=1.0, rough=opts.rough or th.rough)))


def _block(out: list[Element], b: dict, x0: float, x1: float, y1: float, th, opts: Options, spec,
           front: list[Element]) -> None:
    y0 = b["y0"]
    if b["kind"] == "rect":
        col = b["color"] or th.cluster_fill
        if col is not None and col.a > 0.6:
            col = col.with_alpha(0.35) if b["color"] is not None and b["color"].a >= 0.999 else col
        out.insert(0, ShapeEl(Rect(x0, y0, x1 - x0, y1 - y0), Style(stroke=None, fill=col, width=0.0)))
        return
    st = Style(stroke=th.cluster_stroke, fill=None, width=1.2, rough=opts.rough or th.rough)
    out.append(ShapeEl(Rect(x0, y0, x1 - x0, y1 - y0), st))
    kw = b["kind"]
    kw_spec = replace(spec, bold=True)
    kw_w, _, _ = measure_label(kw, kw_spec, None)
    tab_h = spec.size * 1.35
    tab_w = kw_w + 18
    out.append(ShapeEl(Polygon([(x0, y0), (x0 + tab_w, y0), (x0 + tab_w, y0 + tab_h - 6), (x0 + tab_w - 6, y0 + tab_h), (x0, y0 + tab_h)]),
                       Style(stroke=th.cluster_stroke, fill=th.cluster_fill or th.background, width=1.2)))
    front.append(label_at(kw, x0 + tab_w / 2, y0 + tab_h / 2, kw_spec, None))
    if b["label"]:
        front.append(label_at(f"[{b['label']}]", (x0 + tab_w + x1) / 2, y0 + tab_h / 2, spec, None))
    for (dy, text) in b["dividers"]:
        out.append(ShapeEl(Line((x0, dy), (x1, dy)), replace(st, dash="dashed_narrow")))
        if text:
            front.append(label_at(f"[{text}]", (x0 + x1) / 2, dy + spec.size * 0.75, spec, None))
