"""Mermaid `timeline` and `gantt` -> native Rnote shapes."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from ..colors import Color, parse_color
from ..diagram import label_at, measure_label, node_elements
from ..model import Arrow, Line, Polygon, Rect, ShapeEl, Style, TextEl, union_all
from .common import Diagram, MermaidError, Options, Source, clean_label


def render(src: Source, opts: Options) -> Diagram:
    first = next((l.strip() for _, l in src.lines if l.strip()), "")
    kind = first.split()[0].lower()
    if kind == "timeline":
        return _timeline(src, opts)
    if kind == "gantt":
        return _gantt(src, opts)
    raise MermaidError(f"unsupported diagram type {kind!r}")


# --------------------------------------------------------------------------- timeline


@dataclass
class Period:
    label: str
    events: list[str] = field(default_factory=list)
    section: int = -1


def _timeline(src: Source, opts: Options) -> Diagram:
    title = src.title
    periods: list[Period] = []
    sections: list[str] = []
    for lineno, raw in src.lines[1:] if src.lines else []:
        line = raw.strip()
        if not line or line.lower() == "timeline":
            continue
        m = re.match(r"^title\s+(.*)$", line, re.I)
        if m:
            title = clean_label(m.group(1))
            continue
        m = re.match(r"^section\s+(.*)$", line, re.I)
        if m:
            sections.append(clean_label(m.group(1)))
            continue
        if line.startswith(":"):
            if not periods:
                raise MermaidError("event line ': ...' must follow a time period", lineno)
            periods[-1].events += [clean_label(e) for e in line[1:].split(":") if e.strip()]
            continue
        parts = line.split(":")
        p = Period(clean_label(parts[0]), [clean_label(e) for e in parts[1:] if e.strip()], len(sections) - 1)
        periods.append(p)
    if not periods:
        raise MermaidError("timeline has no time periods")
    pal = opts.theme.palette or [Color(0.85, 0.9, 1)]
    pspec = opts.text(bold=True)
    espec = opts.text(size=opts.font_size * 0.88)
    colw = []
    for p in periods:
        w = max([measure_label(p.label, pspec, 180)[0]] + [measure_label(e, espec, 170)[0] for e in p.events])
        colw.append(max(w + 24, 110.0))
    gap = 18.0
    els = []
    y = 0.0
    if title:
        tspec = opts.text(size=opts.font_size * 1.3, bold=True)
        total_w = sum(colw) + gap * (len(colw) - 1)
        els.append(label_at(title, total_w / 2, tspec.size * 0.65, tspec, None))
        y = tspec.size * 1.3 + 16
    sec_h = 0.0
    if sections:
        sec_h = opts.font_size * 1.6 + 8
    ph = max(measure_label(p.label, pspec, 180)[2] for p in periods) * pspec.size * 1.25 + 18
    x = 0.0
    xs = []
    for w in colw:
        xs.append(x)
        x += w + gap
    total_w = x - gap
    # sections
    if sections:
        for si, name in enumerate(sections):
            cols = [i for i, p in enumerate(periods) if p.section == si]
            if not cols:
                continue
            x0, x1 = xs[cols[0]], xs[cols[-1]] + colw[cols[-1]]
            col = pal[si % len(pal)]
            els += node_elements("round", x0, y, x1 - x0, sec_h - 8, Style(stroke=col.mix(Color(0, 0, 0), 0.4), fill=col, width=1.2))
            els.append(label_at(f"**{name}**", (x0 + x1) / 2, y + (sec_h - 8) / 2, opts.text()))
    py = y + sec_h
    axis_y = py + ph + 22
    els.append(ShapeEl(Arrow((-10, axis_y), (total_w + 20, axis_y)), Style(stroke=opts.theme.edge, width=2.0)))
    max_bottom = axis_y
    for i, p in enumerate(periods):
        ci = p.section if p.section >= 0 else i
        col = pal[ci % len(pal)]
        dark = col.mix(Color(0, 0, 0), 0.4)
        st = Style(stroke=dark, fill=col, width=1.4, rough=opts.rough or opts.theme.rough)
        els += node_elements("round", xs[i], py, colw[i], ph, st)
        els.append(label_at(p.label, xs[i] + colw[i] / 2, py + ph / 2, pspec, 180))
        cx = xs[i] + colw[i] / 2
        els.append(ShapeEl(Line((cx, py + ph), (cx, axis_y)), Style(stroke=dark, width=1.2)))
        ey = axis_y + 18
        light = col.mix(Color(1, 1, 1), 0.5)
        for e in p.events:
            _, _, n = measure_label(e, espec, 170)
            eh = n * espec.size * 1.25 + 14
            els.append(ShapeEl(Line((cx, ey - 18 if ey == axis_y + 18 else ey - 10), (cx, ey)),
                               Style(stroke=dark, width=1.0, dash="dashed_narrow")))
            els += node_elements("round", xs[i] + 4, ey, colw[i] - 8, eh, Style(stroke=dark, fill=light, width=1.0,
                                                                              rough=opts.rough or opts.theme.rough))
            els.append(label_at(e, cx, ey + eh / 2, espec, 170))
            ey += eh + 10
        max_bottom = max(max_bottom, ey)
    return Diagram(els, union_all([e.bbox() for e in els]), "timeline", None)


# --------------------------------------------------------------------------- gantt


@dataclass
class Task:
    name: str
    section: str
    tags: set[str]
    tid: str | None
    start_spec: str | None
    end_spec: str | None
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    line: int = 0


_FMT_TOKENS = [("YYYY", "%Y"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S")]
_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(ms|s|m|h|d|w|M|y)$")


def _strptime_fmt(fmt: str) -> str:
    out = fmt
    for tok, rep in _FMT_TOKENS:
        out = out.replace(tok, rep)
    return out


def _gantt(src: Source, opts: Options) -> Diagram:
    title = src.title
    fmt = "%Y-%m-%d"
    section = ""
    tasks: list[Task] = []
    for lineno, raw in src.lines:
        line = raw.strip()
        if not line or line.lower() == "gantt":
            continue
        low = line.lower()
        if low.startswith("title "):
            title = clean_label(line[6:])
            continue
        if low.startswith("dateformat"):
            fmt = _strptime_fmt(line.split(None, 1)[1].strip())
            continue
        if re.match(r"^(axisformat|tickinterval|excludes|includes|todaymarker|weekday|displaymode|inclusiveenddates|topaxis|accTitle|accDescr)\b", low):
            continue
        if low.startswith("section"):
            section = clean_label(line[7:].strip())
            continue
        if ":" not in line:
            raise MermaidError(f"could not understand {line!r} (expected 'Task name : [tags,] [id,] start, end')", lineno)
        name, meta = line.split(":", 1)
        items = [x.strip() for x in meta.split(",") if x.strip()]
        tags = set()
        while items and items[0].lower() in ("crit", "active", "done", "milestone"):
            tags.add(items.pop(0).lower())
        tid = start = end = None
        if len(items) >= 3:
            tid, start, end = items[0], items[1], items[2]
        elif len(items) == 2:
            if _looks_like_start(items[0], fmt):
                start, end = items
            else:
                tid, end = items
        elif len(items) == 1:
            end = items[0]
        tasks.append(Task(clean_label(name), section, tags, tid, start, end, line=lineno))
    if not tasks:
        raise MermaidError("gantt chart has no tasks")
    by_id: dict[str, Task] = {}
    prev_end: dt.datetime | None = None
    for t in tasks:
        t.start = _resolve_start(t, fmt, by_id, prev_end)
        t.end = _resolve_end(t, fmt, by_id)
        if t.end < t.start:
            t.end = t.start
        if t.tid:
            by_id[t.tid] = t
        prev_end = t.end
    t0 = min(t.start for t in tasks)
    t1 = max(t.end for t in tasks)
    span_days = max((t1 - t0).total_seconds() / 86400, 1.0)
    chart_w = 640.0
    px_day = chart_w / span_days
    spec = opts.text(size=opts.font_size * 0.88)
    name_w = max(measure_label(t.name, spec, None)[0] for t in tasks)
    sec_w = max([measure_label(t.section, opts.text(bold=True), None)[0] for t in tasks if t.section] + [0.0])
    left = sec_w + 24 + name_w + 20 if sec_w else name_w + 20
    row = spec.size * 1.9
    els = []
    y = 0.0
    if title:
        tspec = opts.text(size=opts.font_size * 1.3, bold=True)
        els.append(label_at(title, left + chart_w / 2, tspec.size * 0.65, tspec, None))
        y = tspec.size * 1.3 + 14
    axis_h = spec.size * 1.6
    top = y + axis_h
    bottom = top + row * len(tasks)
    pal = opts.theme.palette or [Color(0.85, 0.9, 1)]
    # section bands
    sections: list[tuple[str, int, int]] = []
    for i, t in enumerate(tasks):
        if sections and sections[-1][0] == t.section:
            sections[-1] = (t.section, sections[-1][1], i)
        else:
            sections.append((t.section, i, i))
    for si, (name, a, b) in enumerate(sections):
        col = pal[si % len(pal)].mix(Color(1, 1, 1), 0.45)
        els.append(ShapeEl(Rect(0, top + a * row, left + chart_w, (b - a + 1) * row), Style(stroke=None, fill=col, width=0.0)))
        if name:
            els.append(TextEl(name, 8, top + a * row + row / 2 - spec.size * 0.62, font=spec.font, size=spec.size,
                              color=opts.theme.text, weight=700))
    # ticks
    step = _tick_step(span_days)
    d = dt.datetime(t0.year, t0.month, t0.day)
    grid = Style(stroke=Color(0.6, 0.6, 0.6, 0.6), width=0.8, dash="dashed_narrow")
    while d <= t1:
        x = left + (d - t0).total_seconds() / 86400 * px_day
        if x >= left - 0.5:
            els.append(ShapeEl(Line((x, top), (x, bottom)), grid))
            lab = d.strftime("%Y-%m-%d") if step >= 28 else d.strftime("%m-%d")
            els.append(label_at(lab, x, y + axis_h / 2, opts.text(size=opts.font_size * 0.75), None))
        d += dt.timedelta(days=step)
    # bars
    base = opts.theme.node_fill or Color(0.85, 0.88, 1)
    for i, t in enumerate(tasks):
        ry = top + i * row
        x0 = left + (t.start - t0).total_seconds() / 86400 * px_day
        x1 = left + (t.end - t0).total_seconds() / 86400 * px_day
        stroke = opts.theme.node_stroke
        fill = base
        if "done" in t.tags:
            fill, stroke = Color(0.82, 0.82, 0.82), Color(0.5, 0.5, 0.5)
        if "active" in t.tags:
            fill = parse_color("#bfdbfe")
        if "crit" in t.tags:
            fill, stroke = parse_color("#fecaca"), parse_color("#dc2626")
        st = Style(stroke=stroke, fill=fill, width=1.3, rough=opts.rough or opts.theme.rough)
        els.append(TextEl(t.name, (sec_w + 24) if sec_w else 8, ry + row / 2 - spec.size * 0.62, font=spec.font,
                          size=spec.size, color=opts.theme.text))
        if "milestone" in t.tags:
            cx, cy, r = x0, ry + row / 2, row * 0.32
            els.append(ShapeEl(Polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]), st))
        else:
            els += node_elements("round", x0, ry + row * 0.18, max(x1 - x0, 3.0), row * 0.64, st)
    els.append(ShapeEl(Line((left, top), (left, bottom)), Style(stroke=opts.theme.edge, width=1.2)))
    return Diagram(els, union_all([e.bbox() for e in els]), "gantt", None)


def _looks_like_start(s: str, fmt: str) -> bool:
    if s.lower().startswith("after "):
        return True
    try:
        dt.datetime.strptime(s, fmt)
        return True
    except ValueError:
        return False


def _duration(s: str) -> dt.timedelta | None:
    m = _DUR_RE.match(s.strip())
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    return {"ms": dt.timedelta(milliseconds=v), "s": dt.timedelta(seconds=v), "m": dt.timedelta(minutes=v),
            "h": dt.timedelta(hours=v), "d": dt.timedelta(days=v), "w": dt.timedelta(weeks=v),
            "M": dt.timedelta(days=30 * v), "y": dt.timedelta(days=365 * v)}[u]


def _resolve_start(t: Task, fmt: str, by_id: dict[str, Task], prev_end):
    s = t.start_spec
    if s is None:
        if prev_end is None:
            raise MermaidError(f"task '{t.name}' needs a start date (it is the first task)", t.line)
        return prev_end
    if s.lower().startswith("after "):
        ends = []
        for ref in s[6:].split():
            if ref not in by_id:
                raise MermaidError(f"task '{t.name}': unknown task id {ref!r} in 'after'", t.line)
            ends.append(by_id[ref].end)
        return max(ends)
    try:
        return dt.datetime.strptime(s, fmt)
    except ValueError:
        raise MermaidError(f"task '{t.name}': date {s!r} does not match dateFormat", t.line) from None


def _resolve_end(t: Task, fmt: str, by_id: dict[str, Task]):
    e = t.end_spec
    if e is None:
        return t.start + dt.timedelta(days=1)
    d = _duration(e)
    if d is not None:
        return t.start + d
    if e.lower().startswith("until "):
        ref = e[6:].strip()
        if ref in by_id:
            return by_id[ref].start
    try:
        return dt.datetime.strptime(e, fmt)
    except ValueError:
        raise MermaidError(f"task '{t.name}': end {e!r} is not a date or a duration like 5d", t.line) from None


def _tick_step(days: float) -> int:
    for step in (1, 2, 7, 14, 28, 61, 91, 182, 365):
        if days / step <= 10:
            return step
    return 730
