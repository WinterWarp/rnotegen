"""Markdown -> flow operations.

The builder executes these operations with its flow layout (automatic
positioning and page breaks), so a Markdown note becomes native Rnote text,
lines, tables and diagrams.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Op:
    kind: str  # heading | para | bullet | code | table | hr | quote | mermaid | image | space | todo | pagebreak
    text: str = ""
    level: int = 1
    marker: str = ""
    lang: str = ""
    rows: list[list[str]] = field(default_factory=list)
    aligns: list[str] = field(default_factory=list)
    done: bool = False
    line: int = 0  # 1-based line inside the markdown source


_BULLET_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TODO_RE = re.compile(r"^\[( |x|X)\]\s+(.*)$")
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_IMG_RE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")


def parse_markdown(text: str) -> list[Op]:
    lines = text.replace("\r\n", "\n").split("\n")
    ops: list[Op] = []
    i = 0
    para: list[str] = []
    para_line = 0

    def flush():
        nonlocal para
        if para:
            ops.append(Op("para", " ".join(s.strip() for s in para).replace("  ", " "), line=para_line))
            para = []

    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        ln = i + 1
        if not s:
            flush()
            i += 1
            continue
        if s in ("<!-- pagebreak -->", "\\pagebreak", "\\newpage", "<div style=\"page-break-after: always\"></div>"):
            flush()
            ops.append(Op("pagebreak", line=ln))
            i += 1
            continue
        m = re.match(r"^(```+|~~~+)\s*([\w+-]*)", s)
        if m:
            flush()
            fence, lang = m.group(1), m.group(2).lower()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            i += 1
            ops.append(Op("mermaid" if lang == "mermaid" else "code", "\n".join(_dedent(body)), lang=lang, line=ln))
            continue
        m = _HEAD_RE.match(s)
        if m:
            flush()
            ops.append(Op("heading", m.group(2), level=len(m.group(1)), line=ln))
            i += 1
            continue
        if i + 1 < len(lines) and para == [] and re.match(r"^(=+|-+)\s*$", lines[i + 1].strip()) and not _BULLET_RE.match(raw):
            ops.append(Op("heading", s, level=1 if lines[i + 1].strip()[0] == "=" else 2, line=ln))
            i += 2
            continue
        if re.match(r"^(\*\s*\*\s*\*[\s*]*|-\s*-\s*-[\s-]*|_\s*_\s*_[\s_]*)$", s):
            flush()
            ops.append(Op("hr", line=ln))
            i += 1
            continue
        if s.startswith("|") or (s.count("|") >= 2 and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip())):
            flush()
            rows = []
            start = i
            while i < len(lines) and "|" in lines[i]:
                rows.append(lines[i].strip())
                i += 1
            ops.append(_table(rows, start + 1))
            continue
        if s.startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip()[1:].strip())
                i += 1
            ops.append(Op("quote", "\n".join(q), line=ln))
            continue
        m = _IMG_RE.match(s)
        if m:
            flush()
            ops.append(Op("image", m.group(2), marker=m.group(1), line=ln))
            i += 1
            continue
        m = _BULLET_RE.match(raw)
        if m:
            flush()
            indent = len(m.group(1).replace("\t", "    "))
            level = 1 + indent // 2 if indent < 8 else 1 + indent // 4
            marker = m.group(2)
            item = m.group(3)
            i += 1
            # continuation lines of the same item
            while i < len(lines) and lines[i].strip() and not _BULLET_RE.match(lines[i]) and \
                    len(lines[i]) - len(lines[i].lstrip()) > indent and not lines[i].strip().startswith(("```", "|", ">")):
                item += " " + lines[i].strip()
                i += 1
            t = _TODO_RE.match(item)
            if t:
                ops.append(Op("todo", t.group(2), level=level, done=t.group(1) != " ", line=ln))
            else:
                ops.append(Op("bullet", item, level=level, marker=marker if marker[0].isdigit() else "", line=ln))
            continue
        if not para:
            para_line = ln
        # hard line break: two trailing spaces or backslash
        if raw.endswith("  ") or raw.endswith("\\"):
            para.append(s.rstrip("\\") + "\n")
        else:
            para.append(s)
        i += 1
    flush()
    # merge "\n " artifacts from hard breaks
    for op in ops:
        if op.kind == "para":
            op.text = op.text.replace("\n ", "\n")
    return ops


def _table(rows: list[str], line: int) -> Op:
    def cells(r: str) -> list[str]:
        r = r.strip()
        if r.startswith("|"):
            r = r[1:]
        if r.endswith("|"):
            r = r[:-1]
        return [c.strip() for c in re.split(r"(?<!\\)\|", r)]

    parsed = [cells(r) for r in rows]
    aligns: list[str] = []
    out_rows = []
    for k, r in enumerate(parsed):
        if k == 1 and all(re.match(r"^:?-{1,}:?$", c) for c in r if c):
            aligns = ["center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left" for c in r]
            continue
        out_rows.append([c.replace("\\|", "|") for c in r])
    n = max((len(r) for r in out_rows), default=0)
    out_rows = [r + [""] * (n - len(r)) for r in out_rows]
    aligns = (aligns + ["left"] * n)[:n]
    return Op("table", rows=out_rows, aligns=aligns, line=line)


def _dedent(body: list[str]) -> list[str]:
    ind = [len(l) - len(l.lstrip()) for l in body if l.strip()]
    if not ind:
        return body
    m = min(ind)
    return [l[m:] for l in body]
